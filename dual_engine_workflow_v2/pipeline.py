# -*- coding: utf-8 -*-
"""Workflow v2 creation pipeline — wired reforms §三–十."""
from __future__ import print_function

import copy
import json
import threading
import time
import traceback
import uuid
from datetime import datetime

from .config import (
    WORKFLOW_VERSION,
    MIGRATION_VERSION,
    CODE_VERSION,
    DATA_VERSION,
    BACKTEST_VERSION,
    TARGET_TRADES_PER_DAY,
    MAX_REPAIR_ROUNDS,
    ARTIFACTS_DIR,
    ensure_dirs,
    load_flags,
    _now,
    _atomic,
)
from .protocol import (
    envelope,
    validate_envelope,
    validate_mechanism_statement,
    validate_condition_supplement_request,
    new_call_id,
)
from .mechanism import (
    build_fingerprint_from_statement,
    is_duplicate,
    require_statement_or_reject,
)
from .fidelity import (
    rule_based_condition_audit,
    glm_refine_condition_audit,
    apply_audit_removals,
)
from .tests_abc import run_abc_battery
from .repair_drift import (
    init_repair_log,
    record_repair_round,
    apply_engineering_repair_only,
    detect_frequency_exit,
)
from .failure_archive import (
    build_failure_archive,
    map_failed_test_to_level,
)
from .formal_4d import run_multidimensional_review
from .exploration import (
    lock_exploration_mode,
    build_mode_context,
    assert_mode_locked,
)
from . import store


_JOB = {"running": False, "kind": None, "started_at": None, "error": None}
_JOB_LOCK = threading.Lock()


def _ai_json(provider, system_prompt, user_payload, max_tokens=2200, temperature=0.35):
    import auto_trade_dual_engine_factory as dual
    return dual._ai_json(provider, system_prompt, user_payload,
                         max_tokens=max_tokens, temperature=temperature)


def _new_task_id():
    return "wv2_%s_%s" % (datetime.now().strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:4])


def _save_artifact(task_id, name, obj):
    ensure_dirs()
    path = ARTIFACTS_DIR / ("%s_%s.json" % (task_id, name))
    _atomic(path, obj)
    return str(path)


def _backtest_metrics_fn(definition, symbol, timeframe):
    import auto_trade_dual_engine_factory as dual
    import auto_trade_strategy_dsl as dsl_mod

    def _run(defn):
        d = dsl_mod.validate_strategy(defn) if not defn.get("_skip_validate") else defn
        r = dual._backtest(d, symbol, timeframe, "observed_base")
        m = dual._metrics_from_trades(r.get("trades") or [])
        return {"metrics": m, "trades": r.get("trades") or []}

    return _run


def _archive_fail(task, *, failed_test, observed, expected, statement=None,
                  confidence="medium", alternatives=None, ruled_out=None,
                  unresolved=None, retry=None, final_statement=None):
    level = map_failed_test_to_level(failed_test)
    mech_id = (task.get("mechanism_id")
               or ((task.get("fingerprint") or {}).get("family_hash"))
               or "unknown")
    focus = task.get("focus") or {}
    try:
        archive = build_failure_archive(
            strategy_id=task.get("strategy_id") or (task.get("candidate") or {}).get("key"),
            mechanism_id=mech_id,
            symbol=focus.get("symbol"),
            timeframe=focus.get("timeframe"),
            exploration_mode=task.get("exploration_mode"),
            failure_level=level,
            evidence_scope={"failed_test": failed_test, "packs_keys": list((task.get("packs") or {}).keys())},
            failed_test=failed_test,
            observed_result=observed,
            expected_result=expected,
            root_cause_confidence=confidence,
            alternative_explanations=alternatives or ["insufficient_sample", "representation_mismatch"],
            ruled_out_explanations=ruled_out or ["vague_market_unsuitable_claim"],
            unresolved_questions=unresolved or ["whether_mechanism_exists_under_other_data_grain"],
            retry_conditions=retry or ["new_data_family", "different_symbol_tf"],
            final_statement=final_statement or (
                "当前数据与测试未能支持该假设（failure_level=%s）。" % level
            ),
            task_id=task.get("id"),
            code_version=CODE_VERSION,
            data_version=DATA_VERSION,
            backtest_version=BACKTEST_VERSION,
        )
    except ValueError as exc:
        archive = {
            "failure_level": level,
            "error": str(exc),
            "failed_test": failed_test,
            "task_id": task.get("id"),
        }
    task["failure_archive"] = archive
    try:
        store.save_failure_archive(archive)
    except Exception as exc:
        task.setdefault("persist_errors", []).append("failure_archive:%s" % exc)
    return archive


def glm_require_mechanism_statement(mode_ctx, focus):
    """GLM must emit full mechanism_statement before DSL."""
    prompt = (
        "你是策略总设计师GLM-5.2。输出《策略逻辑假设书》JSON，必须包含 mechanism_statement 全部字段："
        "forced_actor,observable_behavior,predictable_distortion,persistence_reason,counterparty,"
        "activation_regime,invalidation_regime,causal_entry,causal_exit,forbidden_substitutions。"
        "另含 title,thesis,direction,symbol,timeframe,suggested_core_features[],holding_horizon。"
        "禁止空泛表述。forbidden_substitutions 必须列出禁止Codex替换的旧逻辑/指标。"
        "仅输出JSON。"
    )
    payload = {
        "exploration": mode_ctx,
        "focus": focus,
        "constraints": {
            "leverage": 20, "stop_loss_pct": 0.009, "initial_position_pct": 0.30,
            "target_trades_per_day": list(TARGET_TRADES_PER_DAY),
            "no_martingale": True, "no_grid": True, "no_add": True,
        },
    }
    call_id = new_call_id("glm_stmt")
    env = envelope("glm", payload, call_id=call_id, purpose="mechanism_statement")
    validate_envelope(env, expect_role="glm")
    res = _ai_json("glm", prompt, payload, max_tokens=2000, temperature=0.35)
    parsed = (res or {}).get("parsed") or {}
    stmt = parsed.get("mechanism_statement") or parsed
    # If model nested wrong, try extract
    if "forced_actor" not in (stmt or {}) and isinstance(parsed.get("hypothesis"), dict):
        stmt = parsed["hypothesis"].get("mechanism_statement") or parsed["hypothesis"]
    ok, errors, cleaned = require_statement_or_reject(stmt)
    return {
        "ok": ok,
        "errors": errors,
        "mechanism_statement": cleaned if ok else stmt,
        "meta": {
            "title": parsed.get("title"),
            "thesis": parsed.get("thesis"),
            "direction": parsed.get("direction") or "long",
            "symbol": parsed.get("symbol") or (focus or {}).get("symbol"),
            "timeframe": parsed.get("timeframe") or (focus or {}).get("timeframe"),
            "suggested_core_features": parsed.get("suggested_core_features") or [],
            "holding_horizon": parsed.get("holding_horizon") or "",
        },
        "call_id": call_id,
        "raw_ok": bool((res or {}).get("ok")),
        "ai_error": (res or {}).get("error"),
    }


def codex_implement_dsl(statement_pack, approved_supplements=None):
    """Codex authority-limited DSL build — no unapproved traditional indicators."""
    import auto_trade_dual_engine_factory as dual

    meta = statement_pack.get("meta") or {}
    stmt = statement_pack.get("mechanism_statement") or {}
    symbol = meta.get("symbol") or "SOL-USDT-SWAP"
    timeframe = meta.get("timeframe") or "5m"
    direction = meta.get("direction") or "long"
    title = meta.get("title") or "wf_v2_strategy"
    key = ("wf_v2_%s_%s_%s" % (
        str(symbol).split("-")[0].lower(),
        timeframe,
        uuid.uuid4().hex[:6],
    )).replace("-", "_")

    # Prefer micro features from causal_entry tokens; never inject RSI/CCI unless approved
    approved_feats = set()
    for s in (approved_supplements or []):
        if str(s.get("glm_decision") or "").lower() == "approved":
            feat = ((s.get("proposed_condition") or {}).get("feature") or s.get("feature") or "")
            if feat:
                approved_feats.add(str(feat).lower())

    core_feats = [str(x) for x in (meta.get("suggested_core_features") or [])[:3]]
    # Strip forbidden unless approved
    from .config import FORBIDDEN_CORE_FEATURES
    safe_feats = []
    for f in core_feats:
        fl = f.lower()
        if any(t in fl for t in FORBIDDEN_CORE_FEATURES) and fl not in approved_feats:
            continue
        safe_feats.append(f)
    if not safe_feats:
        # microstructure-ish defaults that DSL supports — volume/atr style if available
        safe_feats = ["atr_pct", "volume_z"] if direction else ["atr_pct"]

    # Build minimal DSL via existing hypothesis book builder path when possible
    insight = {
        "summary_zh": stmt.get("predictable_distortion") or title,
        "priority_niches": [{"symbol": symbol, "timeframe": timeframe, "reason": "wf_v2"}],
    }
    inputs = {"focus_candidates": [{"symbol": symbol, "timeframe": timeframe}]}
    try:
        books = dual._codex_hypothesis_books(insight, inputs)
    except Exception:
        books = []
    dsl = None
    if books:
        dsl = copy.deepcopy(books[0].get("dsl") or {})
        dsl["key"] = key
        dsl["name"] = title
        dsl["direction"] = direction
        dsl["timeframe"] = timeframe
        dsl["supported_instruments"] = [symbol]
        dsl["description"] = "wf_v2|%s" % (stmt.get("forced_actor") or "")[:180]
    if not dsl:
        # Explicit minimal DSL — atr filter as execution_safety style gate (not RSI)
        entry_leaves = []
        if direction == "long":
            entry_leaves.append({"left": {"feature": "atr_pct"}, "op": "gt", "right": {"value": 0.001}})
            entry_leaves.append({"left": {"feature": "volume_z"}, "op": "gt", "right": {"value": 0.5}})
        else:
            entry_leaves.append({"left": {"feature": "atr_pct"}, "op": "gt", "right": {"value": 0.001}})
            entry_leaves.append({"left": {"feature": "volume_z"}, "op": "gt", "right": {"value": 0.5}})
        # If legacy book injected RSI, we strip in fidelity audit
        dsl = {
            "schema": "qiyu_strategy_dsl_v1",
            "key": key,
            "name": title,
            "direction": direction,
            "timeframe": timeframe,
            "supported_instruments": [symbol],
            "entry": {"all": entry_leaves},
            "exit": {"any": [{"left": {"feature": "atr_pct"}, "op": "lt", "right": {"value": 0.0005}}]},
            "max_hold_bars": 24,
            "description": "wf_v2_minimal",
            "origin": "workflow_v2_codex",
            "version": 1,
            "live_enabled": False,
            "auto_trade_eligible": False,
        }

    # Condition supplement gate: if DSL contains forbidden feats not approved → strip request
    from .mechanism import extract_dsl_conditions, feature_is_forbidden_core
    supplements_needed = []
    for row in extract_dsl_conditions(dsl):
        if feature_is_forbidden_core(row.get("feature")):
            feat = str(row.get("feature") or "").lower()
            if feat not in approved_feats:
                req = {
                    "problem_solved": "legacy_template_injected_indicator",
                    "causal_link_to_mechanism": "none_auto_reject",
                    "reproducible_experiment_without": "remove_leaf_and_rerun",
                    "risk_becomes_dominant_mechanism": "high",
                    "proposed_condition": {"feature": row.get("feature"), "op": row.get("op"), "right": row.get("right")},
                    "glm_decision": "rejected_auto",
                }
                ok_req, err, cleaned = validate_condition_supplement_request(req)
                supplements_needed.append(cleaned if ok_req else req)

    book = {
        "title": title,
        "thesis": meta.get("thesis") or stmt.get("predictable_distortion"),
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "dsl": dsl,
        "mechanism_statement": stmt,
        "status": "codex_implemented",
        "supplements_needed": supplements_needed,
        "core_features": safe_feats,
    }
    return book


def run_internal_packs_v2(book):
    """Anti-overfit + friction + ABC tests (no logic_destruction ±20%)."""
    import auto_trade_dual_engine_factory as dual
    import auto_trade_strategy_dsl as dsl_mod

    dsl = book.get("dsl") or {}
    try:
        definition = dsl_mod.validate_strategy(dsl)
    except Exception as exc:
        return {"ok": False, "stage": "validate", "error": str(exc)}

    symbol = book.get("symbol") or (definition.get("supported_instruments") or ["BTC-USDT-SWAP"])[0]
    timeframe = book.get("timeframe") or definition.get("timeframe") or "5m"
    try:
        base = dual._backtest(definition, symbol, timeframe, "observed_base")
        base_m = dual._metrics_from_trades(base.get("trades") or [])
    except Exception as exc:
        return {"ok": False, "stage": "base_backtest", "error": str(exc)}

    anti_pass = (
        base_m["trades"] >= 8
        and base_m["folds"] >= 5
        and base_m["fold_positive"] >= max(3, int(base_m["folds"] * 0.5))
        and base_m["oos_profit"] > 0
        and base_m["sharpe"] >= dual.SHARPE_ANTIOF
    )
    anti = {"pass": bool(anti_pass), "metrics": base_m}

    try:
        fr = dual._backtest(definition, symbol, timeframe, "observed_base",
                            slip_mult=2.0, latency_extra=0.00005, fill_fail_pct=0.05)
        fr_m = dual._metrics_from_trades(fr.get("trades") or [])
    except Exception as exc:
        fr_m = {"error": str(exc), "sharpe": -1, "trades": 0}
    fr_pass = fr_m.get("trades", 0) >= 5 and float(fr_m.get("sharpe") or -1) > dual.SHARPE_FRICTION
    friction = {"pass": bool(fr_pass), "metrics": fr_m}

    bt_fn = _backtest_metrics_fn(definition, symbol, timeframe)
    abc = run_abc_battery(
        definition, base_m, lambda d: bt_fn(d),
        core_features=book.get("core_features"),
        allow_direction_flip=False,
    )
    # Explicitly record replacement of logic_destruction
    packs = {
        "ok": True,
        "pass": bool(anti_pass and fr_pass and abc.get("pass")),
        "definition": definition,
        "symbol": symbol,
        "timeframe": timeframe,
        "anti_overfit": anti,
        "extreme_friction": friction,
        "logic_destruction": {
            "pass": None,
            "deprecated": True,
            "replaced_by": "abc_battery",
            "note": "±20% param worsening rule removed in workflow_v2",
        },
        "abc": abc,
        "base_metrics": base_m,
        "anti_overfit_score": round(
            min(100.0, 40.0 + base_m["sharpe"] * 20.0 + max(0.0, base_m["oos_profit"]) * 200.0), 3
        ) if anti_pass else 0.0,
    }
    return packs


def run_creation_pipeline_v2(symbol=None, timeframe=None, exploration_mode="A",
                             async_hook=None):
    ensure_dirs()
    store.migrate_forward()
    tid = _new_task_id()
    mode = lock_exploration_mode(exploration_mode)
    task = {
        "id": tid,
        "schema": "qiyu_dual_engine_task_v2",
        "workflow_version": WORKFLOW_VERSION,
        "migration_version": MIGRATION_VERSION,
        "code_version": CODE_VERSION,
        "created_at": _now(),
        "stage": "mode_lock",
        "exploration_mode": mode,
        "focus": {"symbol": symbol, "timeframe": timeframe},
        "history": [],
    }
    assert_mode_locked(task, mode)

    import auto_trade_dual_engine_factory as dual
    dual._ensure_dirs()
    dual._load_env()

    # Collect + isolate
    task["stage"] = "collect_isolate"
    inputs = dual.collect_inputs()
    if symbol and timeframe:
        inputs["focus_candidates"] = (
            [{"symbol": symbol, "timeframe": timeframe, "reason": "manual"}]
            + list(inputs.get("focus_candidates") or [])
        )
    fps = store.list_fingerprints()
    exclusions = store.list_exclusion_rules()
    mode_ctx = build_mode_context(mode, inputs, fingerprint_index=fps, exclusion_rules=exclusions)
    task["mode_context_meta"] = {
        "mode": mode,
        "isolation": mode_ctx.get("isolation") or mode_ctx.get("schema"),
        "stripped_keys": mode_ctx.get("stripped_keys") or [],
        "isolation_verified": mode_ctx.get("isolation_verified"),
    }
    focus = (mode_ctx.get("focus_candidates") or inputs.get("focus_candidates") or [{}])[0]
    if symbol:
        focus = {"symbol": symbol, "timeframe": timeframe or focus.get("timeframe") or "5m"}
    task["focus"] = focus
    dual.save_task(task)
    store.save_task_meta(task)

    # Mechanism statement (hard gate)
    task["stage"] = "mechanism_statement"
    stmt_pack = glm_require_mechanism_statement(mode_ctx, focus)
    task["mechanism_statement_gate"] = {
        "ok": stmt_pack.get("ok"),
        "errors": stmt_pack.get("errors"),
        "call_id": stmt_pack.get("call_id"),
    }
    if not stmt_pack.get("ok"):
        task["stage"] = "archived"
        task["error"] = "mechanism_statement_incomplete"
        _archive_fail(
            task, failed_test="representation",
            observed=stmt_pack.get("errors"),
            expected="all mechanism_statement fields present",
            final_statement="假设书必填字段缺失，不得进入DSL实现阶段。",
        )
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "stage": "archived", "reason": "statement_incomplete"}

    stmt = stmt_pack["mechanism_statement"]
    task["mechanism_statement"] = stmt
    fp = build_fingerprint_from_statement(
        stmt,
        extras={"holding_horizon": (stmt_pack.get("meta") or {}).get("holding_horizon"),
                "information_source": mode_ctx.get("allowed_data_family") or "mode_%s" % mode},
    )
    task["fingerprint"] = fp
    task["mechanism_id"] = fp.get("family_hash")
    store.save_mechanism_statement(tid, None, task["mechanism_id"], stmt, stmt_pack.get("call_id"))
    store.save_fingerprint(tid, None, task["mechanism_id"], fp)

    dup, hits = is_duplicate(fp, fps)
    if dup:
        task["stage"] = "archived"
        task["error"] = "duplicate_mechanism"
        task["duplicate_hits"] = hits[:3]
        _archive_fail(
            task, failed_test="duplicate_mechanism",
            observed={"hits": hits[:3]},
            expected="novel mechanism family",
            final_statement="与已有策略机制指纹高度相似，判定为重复搜索。",
        )
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "stage": "archived", "reason": "duplicate"}

    # Codex implement under authority limits
    task["stage"] = "codex_dsl"
    book = codex_implement_dsl(stmt_pack)
    book["mechanism_fingerprint"] = fp
    task["strategy_id"] = (book.get("dsl") or {}).get("key")
    task["candidate"] = {
        "title": book.get("title"), "symbol": book.get("symbol"),
        "timeframe": book.get("timeframe"), "direction": book.get("direction"),
        "key": task["strategy_id"],
    }
    # Auto-reject unapproved supplements (do not write into DSL)
    if book.get("supplements_needed"):
        # strip forbidden leaves before fidelity
        from .fidelity import apply_audit_removals
        fake_audit = {
            "condition_audit": [
                {
                    "feature": (s.get("proposed_condition") or {}).get("feature"),
                    "action": "remove",
                    "classification": "unapproved_filter",
                }
                for s in book["supplements_needed"]
            ]
        }
        book["dsl"], removed = apply_audit_removals(book.get("dsl"), fake_audit)
        task["codex_stripped_unapproved"] = removed

    # Fidelity audit
    task["stage"] = "fidelity_audit"
    rule_audit = rule_based_condition_audit(
        book.get("dsl"), stmt, approved_supplements=[]
    )
    fidelity = glm_refine_condition_audit(book, rule_audit, ai_json_fn=_ai_json)
    task["condition_audit"] = fidelity
    store.save_condition_audit(tid, task["strategy_id"], fidelity, stmt_pack.get("call_id"))
    if fidelity.get("reject"):
        # remove unapproved and/or reject
        book["dsl"], removed = apply_audit_removals(book.get("dsl"), fidelity)
        task["fidelity_removed"] = removed
        # Re-audit; if still reject_strategy → archive (not a repair round)
        rule_audit2 = rule_based_condition_audit(book.get("dsl"), stmt, [])
        if rule_audit2.get("reject") or any(
            a.get("action") == "reject_strategy" for a in (fidelity.get("condition_audit") or [])
        ):
            task["stage"] = "archived"
            task["error"] = "fidelity_reject"
            _archive_fail(
                task, failed_test="fidelity",
                observed=fidelity.get("reject_reasons"),
                expected="all conditions traceable; no unapproved filters",
                final_statement="机制忠诚度审计驳回：存在未批准过滤器或机制替换。",
            )
            dual.save_task(task)
            store.save_task_meta(task)
            return {"ok": False, "task_id": tid, "reason": "fidelity_reject"}

    # Internal packs + ABC
    task["stage"] = "abc_tests"
    packs = run_internal_packs_v2(book)
    task["packs"] = {
        "pass": packs.get("pass"),
        "anti_overfit": packs.get("anti_overfit"),
        "extreme_friction": {"pass": (packs.get("extreme_friction") or {}).get("pass"),
                             "metrics": (packs.get("extreme_friction") or {}).get("metrics")},
        "abc": {
            "pass": (packs.get("abc") or {}).get("pass"),
            "necessity_pass": ((packs.get("abc") or {}).get("necessity") or {}).get("pass"),
            "stability_pass": ((packs.get("abc") or {}).get("parameter_stability") or {}).get("pass"),
            "stability_overfit": ((packs.get("abc") or {}).get("parameter_stability") or {}).get("high_overfit_risk"),
            "contribution": (packs.get("abc") or {}).get("contribution"),
        },
        "logic_destruction": packs.get("logic_destruction"),
        "base_metrics": packs.get("base_metrics"),
        "error": packs.get("error"),
    }
    if packs.get("ok"):
        try:
            store.save_abc_tests(tid, task["strategy_id"], packs.get("abc") or {})
        except Exception as exc:
            task.setdefault("persist_errors", []).append(str(exc))

    if not packs.get("ok"):
        task["stage"] = "archived"
        _archive_fail(task, failed_test="engineering", observed=packs.get("error"),
                      expected="valid dsl + backtest",
                      final_statement="工程/数据管道失败，尚未进入机制有效性判定。")
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "reason": "pack_error"}

    # Mode D: no repair on core failure
    abc = packs.get("abc") or {}
    core_fail = (
        not (packs.get("anti_overfit") or {}).get("pass")
        or not (abc.get("necessity") or {}).get("pass")
        or (abc.get("parameter_stability") or {}).get("high_overfit_risk")
    )

    repair_log = init_repair_log(tid, task["strategy_id"], stmt)
    task["repair_log"] = repair_log

    if mode == "D" and core_fail:
        failed = "necessity" if not (abc.get("necessity") or {}).get("pass") else "parameter_stability"
        if (abc.get("parameter_stability") or {}).get("high_overfit_risk"):
            failed = "isolated_peak"
        task["stage"] = "archived"
        _archive_fail(task, failed_test=failed,
                      observed=task.get("packs"),
                      expected="mode_d first-pass core tests",
                      final_statement="模式D：首次核心机制未通过关键测试，禁止过滤器/参数/止盈修复，已淘汰。")
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "reason": "mode_d_reject"}

    # Repair loop ≤3 with drift detection (engineering / approved only)
    if core_fail and mode != "D":
        for repair_i in range(MAX_REPAIR_ROUNDS):
            task["stage"] = "repair"
            before_fp = build_fingerprint_from_statement(stmt)
            before_freq = (packs.get("base_metrics") or {}).get("trades")
            before_oos = (packs.get("base_metrics") or {}).get("oos_profit")
            # Only engineering repair — no RSI/CCI inject
            root = "abc_or_antiof_fail"
            book = apply_engineering_repair_only(book, root, repair_i)
            packs = run_internal_packs_v2(book)
            after_stmt = book.get("mechanism_statement") or stmt
            # If repair introduced independent edge marker (should not in eng-only)
            introduced = False
            changed_edge = False
            after_fp = build_fingerprint_from_statement(after_stmt)
            repair_log, drift = record_repair_round(
                repair_log,
                failed_test=root,
                root_cause=root,
                modifications=["engineering_max_hold_adjust"],
                statement_before=stmt,
                statement_after=after_stmt,
                fingerprint_before=before_fp,
                fingerprint_after=after_fp,
                frequency_before=before_freq,
                frequency_after=(packs.get("base_metrics") or {}).get("trades"),
                oos_before=before_oos,
                oos_after=(packs.get("base_metrics") or {}).get("oos_profit"),
                introduced_new_condition=introduced,
                changed_edge_source=changed_edge,
            )
            store.save_drift(tid, task["strategy_id"], repair_i + 1, drift)
            task["drift"] = drift
            if drift.get("drift") == "material":
                task["stage"] = "archived"
                _archive_fail(task, failed_test="mechanism_drift",
                              observed=drift,
                              expected="no material drift during repair",
                              final_statement="修复过程中发生实质机制漂移，原任务终止，须按新策略重新立项。")
                dual.save_task(task)
                store.save_task_meta(task)
                return {"ok": False, "task_id": tid, "reason": "drift", "reopen_required": True}
            if packs.get("pass"):
                core_fail = False
                break
        task["repair_log"] = repair_log
        if core_fail:
            failed = "necessity"
            if (packs.get("abc") or {}).get("parameter_stability", {}).get("high_overfit_risk"):
                failed = "isolated_peak"
            elif not (packs.get("extreme_friction") or {}).get("pass"):
                failed = "execution_cost"
            task["stage"] = "archived"
            # frequency conflict check
            exited, tpd = detect_frequency_exit(
                TARGET_TRADES_PER_DAY,
                (packs.get("base_metrics") or {}).get("trades"),
                bars=5000,
                timeframe=book.get("timeframe"),
            )
            if exited and (packs.get("base_metrics") or {}).get("mean_net", 0) > 0:
                failed = "frequency"
            _archive_fail(
                task, failed_test=failed,
                observed={"packs_pass": packs.get("pass"), "tpd": tpd if 'tpd' in dir() else None,
                          "abc": (packs.get("abc") or {}).get("pass")},
                expected="antiOF+friction+ABC pass within 3 repairs",
                final_statement="在证据范围内未通过关键测试（failure_level 按 failed_test 映射）。",
            )
            dual.save_task(task)
            store.save_task_meta(task)
            return {"ok": False, "task_id": tid, "reason": "repair_exhausted"}

    # Apply contribution removals
    contrib = (packs.get("abc") or {}).get("contribution") or {}
    must_remove = [r.get("feature") for r in (contrib.get("must_remove") or []) if r.get("feature")]
    if must_remove:
        fake = {"condition_audit": [
            {"feature": f, "action": "remove", "classification": "redundant_condition"}
            for f in must_remove
        ]}
        book["dsl"], _ = apply_audit_removals(book.get("dsl"), fake)
        packs = run_internal_packs_v2(book)

    # 4D formal review
    task["stage"] = "formal_4d"
    definition = packs.get("definition") or book.get("dsl")
    review = run_multidimensional_review(
        book, packs, definition, _ai_json,
        aux_wr={"ai_theoretical_wr_avg": 0},
    )
    task["multidimensional_review"] = {
        "approved": review.get("approved"),
        "fatal_blocks": review.get("fatal_blocks"),
        "major_pending": review.get("major_pending"),
        "auxiliary_wr": review.get("auxiliary_wr"),
        "causal_pass": (review.get("causal") or {}).get("causal_pass"),
        "game_pass": (review.get("game_theory") or {}).get("game_theory_pass"),
        "backtest_pass": (review.get("backtest_integrity") or {}).get("backtest_pass"),
        "execution_pass": (review.get("execution_friction") or {}).get("execution_pass"),
        "backtest_executor": (review.get("backtest_integrity") or {}).get("executor"),
    }
    _save_artifact(tid, "review4d", review)
    store.save_multidimensional(tid, task["strategy_id"], review)

    if not review.get("approved"):
        task["stage"] = "archived"
        _archive_fail(
            task, failed_test="formal_fatal" if review.get("fatal_blocks") else "test_standard",
            observed=task["multidimensional_review"],
            expected="all four dimensions pass without fatal",
            final_statement="四维正式复核未通过；致命否决不可被辅助胜率覆盖。",
        )
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "reason": "formal_4d_reject", "review": task["multidimensional_review"]}

    # Push to human-confirm (unchanged production path)
    import auto_trade_human_confirm_pipeline as pipeline
    ai_review = {
        "approved": True,
        "policy": "workflow_v2_4d_formal",
        "ai_theoretical_wr_avg": (review.get("auxiliary_wr") or {}).get("mean"),
        "natural_language": "4D formal pass | fatal_blocks=%s" % (review.get("fatal_blocks"),),
        "multidimensional_review": task["multidimensional_review"],
    }
    push = pipeline.ingest_and_screen(
        {"dsl": definition, "symbol": book.get("symbol"),
         "timeframe": book.get("timeframe"), "thesis": book.get("thesis"),
         "mechanism_statement": stmt, "mechanism_fingerprint": fp},
        source="dual_engine_workflow_v2",
        ai_review=ai_review,
        require_ai_review=True,
    )
    task["pending_push"] = {
        "ok": push.get("ok"), "key": push.get("key"),
        "reason": push.get("reason"), "duplicate": push.get("duplicate"),
    }
    task["stage"] = "done" if push.get("ok") else "failed"
    dual.save_task(task)
    store.save_task_meta(task)
    dual._record_formal({
        "time": _now(), "task_id": tid, "key": task["strategy_id"],
        "title": book.get("title"), "symbol": book.get("symbol"),
        "timeframe": book.get("timeframe"),
        "status": "等待" if push.get("ok") else "退回",
        "annotation": ai_review["natural_language"],
        "workflow_version": WORKFLOW_VERSION,
    })
    return {"ok": bool(push.get("ok")), "task_id": tid, "stage": task["stage"],
            "pending": push, "review": task["multidimensional_review"]}


def start_creation_task_v2(async_mode=True, symbol=None, timeframe=None,
                           exploration_mode="A"):
    ensure_dirs()
    with _JOB_LOCK:
        if _JOB.get("running"):
            return {"ok": False, "status": "running", "error": "job_already_running",
                    "kind": _JOB.get("kind")}
        _JOB.update({"running": True, "kind": "creation_v2", "started_at": _now(),
                     "error": None})

    def _worker():
        try:
            run_creation_pipeline_v2(
                symbol=symbol, timeframe=timeframe,
                exploration_mode=exploration_mode,
            )
        except Exception as exc:
            _JOB["error"] = str(exc)
            try:
                import auto_trade_dual_engine_factory as dual
                dual._append_audit({
                    "event": "creation_v2_crash",
                    "error": str(exc),
                    "trace": traceback.format_exc()[-800:],
                })
            except Exception:
                pass
        finally:
            with _JOB_LOCK:
                _JOB.update({"running": False, "kind": None})

    if async_mode:
        threading.Thread(target=_worker, name="dual-engine-creation-v2",
                         daemon=True).start()
        time.sleep(0.05)
        return {"ok": True, "status": "started", "async": True,
                "workflow_version": WORKFLOW_VERSION,
                "exploration_mode": exploration_mode}
    return run_creation_pipeline_v2(
        symbol=symbol, timeframe=timeframe, exploration_mode=exploration_mode,
    )


def job_snapshot():
    with _JOB_LOCK:
        return dict(_JOB)
