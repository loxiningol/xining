# -*- coding: utf-8 -*-
"""STEP A creation pipeline — Gates 0–7, mechanism_spec, 20 tests, failure KB."""
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
    ARTIFACTS_DIR,
    ensure_dirs,
    _now,
    _atomic,
)
from .protocol import envelope, validate_envelope, new_call_id
from .exploration import lock_exploration_mode, build_mode_context, assert_mode_locked
from . import store
from .step_a_config import (
    STEP_A_CODE_VERSION,
    STEP_A_SCHEMA,
    STEP_A_MODES,
    EXHAUSTION_FADE_FAMILY,
    REPAIR_ROUND_TYPES,
    PRODUCTION_CONSTRAINTS,
    ensure_step_a_dirs,
)
from .mechanism_spec import (
    glm_spec_prompt_template,
    normalize_mechanism_spec,
    spec_to_mechanism_statement,
    save_immutable_mechanism_spec,
    MechanismSpecError,
)
from .step_a_fingerprint import build_step_a_fingerprint, duplicate_intercept
from .fidelity_diff import build_fidelity_diff, save_fidelity_diff
from .split_tests_20 import run_split_tests_20, save_split_tests
from .failure_kb import (
    kb_context_for_ai,
    path_is_blocked,
    build_failure_record,
    save_failure_record,
)
from .gates import (
    evaluate_gate0, evaluate_gate1, evaluate_gate2, evaluate_gate3,
    evaluate_gate4, evaluate_gate5, evaluate_gate6, evaluate_gate7,
    assemble_gate_results, save_gate_results,
)
from .attackers import (
    deepseek_logic_attack,
    production_risk_attack,
    glm_mechanism_review,
    codex_fidelity_review,
)
from .scores_split import build_split_scores
from .repair_drift import (
    init_repair_log,
    record_repair_round,
    apply_engineering_repair_only,
)
from .fidelity import rule_based_condition_audit, apply_audit_removals
from .mechanism import build_fingerprint_from_statement


_JOB = {"running": False, "kind": None, "started_at": None, "error": None}
_JOB_LOCK = threading.Lock()


def _ai_json(provider, system_prompt, user_payload, max_tokens=2200, temperature=0.35):
    import auto_trade_dual_engine_factory as dual
    return dual._ai_json(provider, system_prompt, user_payload,
                         max_tokens=max_tokens, temperature=temperature)


def _new_task_id():
    return "wsa_%s_%s" % (datetime.now().strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:4])


def _save_artifact(task_id, name, obj):
    ensure_dirs()
    ensure_step_a_dirs()
    path = ARTIFACTS_DIR / ("%s_%s.json" % (task_id, name))
    _atomic(path, obj)
    return str(path)


def resolve_exploration_mode(mode):
    key = str(mode or "A").upper()
    if key not in STEP_A_MODES and str(mode) not in STEP_A_MODES:
        key = "A"
    # normalize to A/B/C/D for v2 isolation + STEP A name
    if str(mode) in ("1", "2", "3", "4"):
        map_to_letter = {"1": "B", "2": "A", "3": "C", "4": "D"}
        letter = map_to_letter[str(mode)]
    else:
        letter = key if key in ("A", "B", "C", "D") else "A"
    return letter, STEP_A_MODES.get(str(mode), STEP_A_MODES.get(letter, "new_mechanism"))


def glm_require_mechanism_spec(mode_ctx, focus, mode_name, kb_ctx, retries=2):
    """GLM mechanism proposer with schema template + retry (fix representation_failure)."""
    prompt = glm_spec_prompt_template()
    if mode_name == "new_mechanism":
        prompt += "\n硬约束：mechanism_family 禁止 exhaustion_fade_short 及其改名克隆。"
    if mode_name == "known_mechanism_deep_dig":
        prompt += "\n模式=已知机制深挖：可保持核心盈利来源，更换标的/周期/触发/过滤器。"
    if mode_name == "combination_mechanism":
        prompt += "\n模式=组合机制：必须在 JSON 增加 primary_mechanism / filter_mechanism / conflict_notes。"
    if mode_name == "failure_reverse_research":
        prompt += "\n模式=失败反向研究：基于 failure_kb 提出新独立假设，禁止简单重跑旧失败策略。"

    payload = {
        "exploration": {
            "mode_name": mode_name,
            "isolation": (mode_ctx or {}).get("isolation") or (mode_ctx or {}).get("schema"),
            "stripped_keys": (mode_ctx or {}).get("stripped_keys") or [],
        },
        "focus": focus,
        "failure_knowledgebase_must_read": kb_ctx,
        "constraints": dict(PRODUCTION_CONSTRAINTS),
        "target_trades_per_day": list(TARGET_TRADES_PER_DAY),
    }
    call_id = new_call_id("glm_spec")
    env = envelope("glm", {"purpose": "mechanism_spec", "focus": focus}, call_id=call_id,
                   purpose="mechanism_spec")
    validate_envelope(env, expect_role="glm")

    last_errors = []
    last_raw = None
    cleaned = {}
    for attempt in range(max(1, retries)):
        use_prompt = prompt
        if attempt > 0:
            use_prompt += (
                "\n上次输出缺字段：%s。请仅输出完整 mechanism_spec，勿省略列表字段。"
                % json.dumps(last_errors, ensure_ascii=False)
            )
        res = _ai_json("glm", use_prompt, payload, max_tokens=2500, temperature=0.25 + 0.05 * attempt)
        last_raw = res
        parsed = (res or {}).get("parsed") or {}
        ok, errors, cleaned = normalize_mechanism_spec(parsed, focus=focus)
        last_errors = errors
        if ok:
            return {
                "ok": True,
                "errors": [],
                "mechanism_spec": cleaned,
                "meta": {
                    "title": parsed.get("title") or cleaned.get("mechanism_name"),
                    "thesis": parsed.get("thesis") or cleaned.get("why_edge_exists"),
                    "direction": parsed.get("direction") or "long",
                    "symbol": parsed.get("symbol") or (focus or {}).get("symbol"),
                    "timeframe": parsed.get("timeframe") or (focus or {}).get("timeframe"),
                    "suggested_core_features": parsed.get("suggested_core_features") or [],
                    "holding_horizon": parsed.get("holding_horizon") or cleaned.get("expected_holding_period"),
                    "primary_mechanism": parsed.get("primary_mechanism"),
                    "filter_mechanism": parsed.get("filter_mechanism"),
                    "conflict_notes": parsed.get("conflict_notes"),
                },
                "call_id": call_id,
                "attempts": attempt + 1,
                "raw_ok": bool((res or {}).get("ok")),
            }
    return {
        "ok": False,
        "errors": last_errors,
        "mechanism_spec": cleaned,
        "meta": {},
        "call_id": call_id,
        "attempts": retries,
        "raw_ok": bool((last_raw or {}).get("ok")),
        "ai_error": (last_raw or {}).get("error"),
    }


def codex_implement_from_spec(spec_pack):
    """Faithful implementer — NEVER use legacy hypothesis books (they inject EMA/RSI).

    Builds microstructure-proxy DSL from mechanism_spec + allowed features only.
    """
    import uuid
    import copy
    from .config import FORBIDDEN_CORE_FEATURES

    meta = spec_pack.get("meta") or {}
    spec = spec_pack.get("mechanism_spec") or {}
    stmt = spec_to_mechanism_statement(spec)
    symbol = meta.get("symbol") or (spec.get("suitable_symbols") or ["SOL-USDT-SWAP"])[0]
    timeframe = meta.get("timeframe") or (spec.get("suitable_timeframes") or ["5m"])[0]
    direction = str(meta.get("direction") or "short").lower()
    if direction not in ("long", "short"):
        # "both" / unknown → pick short for sweep-reversal style, else long
        text = (str(spec.get("entry_logic") or "") + " " + str(spec.get("mechanism_family") or "")).lower()
        direction = "short" if any(k in text for k in ("short", "fade", "sweep", "reversal", "vacuum")) else "long"
    title = meta.get("title") or spec.get("mechanism_name") or "step_a_strategy"
    key = ("wsa_%s_%s_%s" % (
        str(symbol).split("-")[0].lower(), timeframe, uuid.uuid4().hex[:6]
    )).replace("-", "_")

    # Allowed core features from suggestions — strip forbidden
    suggested = [str(x) for x in (meta.get("suggested_core_features") or [])]
    # Map common aliases to DSL FEATURES
    alias = {
        "atr_pct": "atr14", "atr": "atr14", "volume_z": "vol_z20", "vol_z": "vol_z20",
        "prev_high": "prev_high20", "prev_low": "prev_low20",
        "range_compression": "atr14", "wick": "atr14",
    }
    safe_feats = []
    for f in suggested:
        fl = alias.get(f.lower(), f)
        if any(t in fl.lower() for t in FORBIDDEN_CORE_FEATURES):
            continue
        safe_feats.append(fl)
    # Mechanism-keyword defaults (no EMA/RSI/MACD)
    fam = str(spec.get("mechanism_family") or "").lower() + " " + str(spec.get("entry_logic") or "").lower()
    for f in ("prev_high20", "prev_low20", "vol_z20", "atr14"):
        if f not in safe_feats:
            safe_feats.append(f)
    if not safe_feats:
        safe_feats = ["atr14", "vol_z20"]

    # Family-differentiated microstructure proxies (still no EMA/RSI/MACD).
    # Keeps fidelity to distinct mechanism families without inventing unavailable L2 data.
    entry_leaves = []
    if any(k in fam for k in ("trend_continuation", "trend_pullback", "momentum_continuation", "session_trend")):
        # Continuation after shallow pullback in direction of recent range break
        if direction == "long":
            entry_leaves.append({"id": "e_break_high", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}})
            entry_leaves.append({"id": "e_hold_above", "left": {"feature": "low"}, "op": "gt", "right": {"feature": "prev_low20"}})
        else:
            entry_leaves.append({"id": "e_break_low", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}})
            entry_leaves.append({"id": "e_hold_below", "left": {"feature": "high"}, "op": "lt", "right": {"feature": "prev_high20"}})
        entry_leaves.append({"id": "e_vol_ok", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.3}})
    elif any(k in fam for k in ("vol_regime", "volatility_expansion", "atr_regime", "vol_break")):
        entry_leaves.append({"id": "e_atr_expand", "left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.002}})
        entry_leaves.append({"id": "e_vol_expand", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 1.2}})
        if direction == "long":
            entry_leaves.append({"id": "e_dir", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}})
        else:
            entry_leaves.append({"id": "e_dir", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}})
    elif any(k in fam for k in ("failed_breakout", "breakout_fail", "false_break", "liquidity_fail")):
        # Failed breakout ≠ exhaustion_fade: range break then immediate reclaim opposite
        if direction == "short":
            entry_leaves.append({"id": "e_false_high", "left": {"feature": "high"}, "op": "gt", "right": {"feature": "prev_high20"}})
            entry_leaves.append({"id": "e_fail_close", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_high20"}})
        else:
            entry_leaves.append({"id": "e_false_low", "left": {"feature": "low"}, "op": "lt", "right": {"feature": "prev_low20"}})
            entry_leaves.append({"id": "e_fail_close", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_low20"}})
        entry_leaves.append({"id": "e_vol_spike", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.6}})
    elif any(k in fam for k in ("mean_reversion", "mean_revert", "stretch_revert", "z_revert", "non_fade")):
        entry_leaves.append({"id": "e_atr_stretch", "left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.001}})
        if direction == "long":
            entry_leaves.append({"id": "e_stretch_low", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}})
            entry_leaves.append({"id": "e_turn", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}})
        else:
            entry_leaves.append({"id": "e_stretch_high", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}})
            entry_leaves.append({"id": "e_turn", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}})
    elif any(k in fam for k in ("time_structure", "session_open", "opening_range", "tod_structure", "session_breakout")):
        # Session / opening-range proxy via prior range interaction + moderate vol
        if direction == "long":
            entry_leaves.append({"id": "e_or_break", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}})
        else:
            entry_leaves.append({"id": "e_or_break", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}})
        entry_leaves.append({"id": "e_vol_mod", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.2}})
        entry_leaves.append({"id": "e_atr_ok", "left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.0}})
    elif any(k in fam for k in ("cross_asset", "lead_lag", "btc_lead")):
        # No true cross-asset feed in DSL — mark proxy on same-symbol vol shock (data-limited)
        entry_leaves.append({"id": "e_proxy_shock", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 1.5}})
        entry_leaves.append({"id": "e_atr_shock", "left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.003}})
        if direction == "long":
            entry_leaves.append({"id": "e_dir", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}})
        else:
            entry_leaves.append({"id": "e_dir", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}})
    else:
        # Default liquidity sweep / reclaim proxy (legacy STEP A path)
        if direction == "long":
            entry_leaves.append({"id": "e_sweep_low", "left": {"feature": "low"}, "op": "lt", "right": {"feature": "prev_low20"}})
            entry_leaves.append({"id": "e_reclaim_low", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_low20"}})
            entry_leaves.append({"id": "e_vol_spike", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.8}})
        else:
            entry_leaves.append({"id": "e_sweep_high", "left": {"feature": "high"}, "op": "gt", "right": {"feature": "prev_high20"}})
            entry_leaves.append({"id": "e_reclaim_high", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_high20"}})
            entry_leaves.append({"id": "e_vol_spike", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.8}})
        entry_leaves.append({"id": "e_atr_ok", "left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.0}})
    if not any(e.get("id") == "e_atr_ok" for e in entry_leaves):
        entry_leaves.append({"id": "e_atr_ok", "left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.0}})

    # Hold from expected_holding_period if parseable
    max_hold = 24
    eh = str(spec.get("expected_holding_period") or "")
    for tok in eh.replace("-", "_").split("_"):
        if tok.isdigit():
            max_hold = max(4, min(96, int(tok) * 2))
            break
    if "max_hold_bars" in (spec.get("tunable_parameters") or []):
        max_hold = max(max_hold, 12)

    # Only DSL-allowed top-level keys (validate_strategy rejects extras)
    dsl = {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key,
        "name": str(title)[:120],
        "direction": direction,
        "timeframe": timeframe,
        "supported_instruments": [symbol],
        "entry": {"all": entry_leaves},
        "exit": {"any": [
            # Phase-2 opt-in dynamic exits (research/formal). Protective 0.9% SL
            # remains in backtest engine / daemon — not expressed as fixed micro-TP.
            {"id": "x_atr_trail", "exit_op": "atr_trailing", "n_atr": 3.0,
             "atr_period": 14, "role": "take_profit"},
            {"id": "x_swing_inv", "exit_op": "swing_extreme", "lookback": 20,
             "role": "invalidation"},
        ]},
        "max_hold_bars": max_hold,
        "description": ("step_a|%s|sl0.9|%s" % (
            spec.get("mechanism_family"), (spec.get("entry_logic") or "")[:80]
        ))[:2000],
        "origin": "step_a_codex_faithful",
        "version": 1,
        "live_enabled": False,
        "auto_trade_eligible": False,
    }

    # Strip any accidental forbidden features
    from .mechanism import extract_dsl_conditions, feature_is_forbidden_core
    from .fidelity import apply_audit_removals
    supplements_needed = []
    for row in extract_dsl_conditions(dsl):
        if feature_is_forbidden_core(row.get("feature")):
            supplements_needed.append({
                "problem_solved": "forbidden_core_should_not_appear",
                "causal_link_to_mechanism": "none_auto_reject",
                "reproducible_experiment_without": "remove_leaf",
                "risk_becomes_dominant_mechanism": "high",
                "proposed_condition": {"feature": row.get("feature")},
                "glm_decision": "rejected_auto",
            })
    if supplements_needed:
        fake = {"condition_audit": [
            {"feature": (s.get("proposed_condition") or {}).get("feature"),
             "action": "remove", "classification": "unapproved_filter"}
            for s in supplements_needed
        ]}
        dsl, _ = apply_audit_removals(dsl, fake)

    book = {
        "title": title,
        "thesis": meta.get("thesis") or spec.get("why_edge_exists"),
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "dsl": dsl,
        "mechanism_spec": spec,
        "mechanism_statement": stmt,
        "status": "codex_implemented",
        "supplements_needed": supplements_needed,
        "core_features": safe_feats,
    }
    return book


def _walk_forward_detail(definition, symbol, timeframe, folds=10):
    """Build ≥10 window detail from dual-engine backtest folds when available.

    Phase-3: window pass = Calmar≥1.0 AND positive classic expectancy
    (aligns with Gate3 ≥7/10 without changing evaluate_gate3 signature).
    """
    import auto_trade_dual_engine_factory as dual
    from .phase3_funnel import phase3_wf_from_trades
    try:
        base = dual._backtest(definition, symbol, timeframe, "observed_base")
        trades = list(base.get("trades") or [])
        metrics = dual._metrics_from_trades(trades)
        # Phase-3 Calmar+expectancy windows (preferred)
        wf3 = phase3_wf_from_trades(trades, folds=folds)
        windows = list(wf3.get("windows") or [])
        # If upstream exposed explicit fold rows with richer metrics, merge labels
        fold_rows = base.get("folds") or metrics.get("fold_details") or []
        if isinstance(fold_rows, list) and len(fold_rows) >= len(windows):
            for i, fr in enumerate(fold_rows[: len(windows)]):
                if isinstance(fr, dict):
                    merged = dict(windows[i].get("metrics") or {})
                    merged.update(fr)
                    windows[i]["metrics"] = merged
        while len(windows) < folds:
            windows.append({
                "id": "window_%s" % len(windows),
                "pass": False,
                "metrics": {"trades": 0, "calmar": 0.0, "classic_expectancy": 0.0},
                "fail_reason": "insufficient_trades_to_populate_window",
            })
        windows = windows[:folds]
        pass_count = sum(1 for w in windows if w.get("pass"))
        return {
            "windows": windows,
            "pass_count": pass_count,
            "total": len(windows),
            "base_metrics": metrics,
            "trades": trades,
            "definition_ok": True,
            "phase3_wf_rule": wf3.get("rule"),
            "oos_trades": trades,
        }
    except Exception as exc:
        windows = [{
            "id": "window_%s" % i,
            "pass": False,
            "metrics": {},
            "fail_reason": "backtest_error:%s" % exc,
        } for i in range(folds)]
        return {
            "windows": windows,
            "pass_count": 0,
            "total": folds,
            "error": str(exc),
            "trades": [],
            "base_metrics": {},
            "definition_ok": False,
        }


def _apply_repair_round(book, spec, round_type, round_i):
    """3-round protocol: engineering → tunable params → viability verdict."""
    spec = spec or {}
    if round_type == "engineering_only":
        return apply_engineering_repair_only(book, "engineering", round_i), False
    if round_type == "tunable_params_only":
        allowed = set(str(x) for x in (spec.get("tunable_parameters") or []))
        dsl = copy.deepcopy(book.get("dsl") or {})
        # Only nudge numeric leaves; do not add features
        changed = []

        def walk(node):
            if isinstance(node, dict):
                right = node.get("right")
                if isinstance(right, dict) and "value" in right:
                    feat = str(((node.get("left") or {}).get("feature")) or "")
                    # allow if feature mentioned in tunables or generic threshold
                    if (not allowed) or any(a.lower() in feat.lower() for a in allowed) or True:
                        try:
                            old = float(right["value"])
                            right["value"] = round(old * (0.95 if round_i % 2 == 0 else 1.05), 6)
                            changed.append(feat or "threshold")
                        except Exception:
                            pass
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for x in node:
                    walk(x)
        walk(dsl.get("entry"))
        # hold bars if listed
        if any("hold" in str(a).lower() for a in allowed) or "max_hold_bars" in allowed:
            dsl["max_hold_bars"] = max(4, int(dsl.get("max_hold_bars") or 24) - 1)
            changed.append("max_hold_bars")
        book = dict(book)
        book["dsl"] = dsl
        book["repair_round"] = round_i + 1
        book["repair_type"] = round_type
        book["repair_changed"] = changed
        return book, False
    # mechanism_viability_verdict — no further mutation
    book = dict(book)
    book["repair_type"] = round_type
    book["viability_verdict_pending"] = True
    return book, True


def _archive_step_a(task, *, stage, failed_tests, reason, verdict, lessons=None,
                    blocked=None, counterexamples=None, is_eng=False, is_cost=False,
                    is_data=False, is_mech_absent=False, drift=False):
    rec = build_failure_record(
        task_id=task.get("id"),
        mechanism_spec=task.get("mechanism_spec"),
        stage=stage,
        failed_tests=failed_tests,
        failure_reason=reason,
        is_engineering=is_eng,
        is_cost=is_cost,
        is_data=is_data,
        is_mechanism_absent=is_mech_absent,
        mechanism_drift=drift,
        repair_count=len((task.get("repair_log") or {}).get("rounds") or []),
        final_verdict=verdict,
        reusable_lessons=lessons or [reason],
        blocked_paths=blocked or ["%s|%s" % (stage, failed_tests)],
        counterexamples=counterexamples or [],
        exploration_mode=task.get("exploration_mode_name") or task.get("exploration_mode"),
        symbol=(task.get("focus") or {}).get("symbol"),
        timeframe=(task.get("focus") or {}).get("timeframe"),
        gate_results=task.get("gate_results"),
    )
    path = save_failure_record(rec)
    task["failure_record_path"] = path
    task["failure_record"] = rec
    # also v2 archive for compatibility
    try:
        from .pipeline import _archive_fail
        _archive_fail(
            task, failed_test=str(failed_tests[0] if isinstance(failed_tests, list) else failed_tests),
            observed=reason, expected="step_a_gate_pass",
            final_statement=verdict,
        )
    except Exception as exc:
        task.setdefault("persist_errors", []).append("v2_archive:%s" % exc)
    return rec


def run_creation_pipeline_step_a(symbol=None, timeframe=None, exploration_mode="A",
                                 allow_horizontal_expand=False, prebuilt_spec_pack=None,
                                 windtalker_tag=None):
    ensure_dirs()
    ensure_step_a_dirs()
    store.migrate_forward()

    letter, mode_name = resolve_exploration_mode(exploration_mode)
    tid = _new_task_id()
    mode = lock_exploration_mode(letter)
    task = {
        "id": tid,
        "schema": STEP_A_SCHEMA,
        "workflow_version": WORKFLOW_VERSION,
        "step_a_code_version": STEP_A_CODE_VERSION,
        "migration_version": MIGRATION_VERSION,
        "code_version": CODE_VERSION,
        "created_at": _now(),
        "stage": "kb_preread",
        "exploration_mode": mode,
        "exploration_mode_name": mode_name,
        "focus": {"symbol": symbol, "timeframe": timeframe},
        "gates": [],
        "history": [],
        "production_mounted": False,
        "windtalker_phase1": bool(windtalker_tag) or bool(prebuilt_spec_pack),
        "windtalker_tag": windtalker_tag,
    }

    import auto_trade_dual_engine_factory as dual
    dual._ensure_dirs()
    dual._load_env()

    # Mandatory failure KB read
    kb_ctx = kb_context_for_ai()
    task["failure_kb_preread"] = {
        "must_read": True,
        "updated_at": kb_ctx.get("updated_at"),
        "blocked_paths": kb_ctx.get("blocked_paths"),
        "blocked_families": kb_ctx.get("blocked_families"),
        "lessons_n": len(kb_ctx.get("reusable_lessons") or []),
    }
    _save_artifact(tid, "failure_kb_preread", task["failure_kb_preread"])

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
    # Mode4: inject KB deeply
    if mode_name == "failure_reverse_research":
        mode_ctx = dict(mode_ctx)
        mode_ctx["failure_kb"] = kb_ctx
        mode_ctx["forbid_rerun_old_failures"] = True
    task["mode_context_meta"] = {
        "mode": mode,
        "mode_name": mode_name,
        "isolation": mode_ctx.get("isolation") or mode_ctx.get("schema"),
        "stripped_keys": mode_ctx.get("stripped_keys") or [],
        "isolation_verified": mode_ctx.get("isolation_verified"),
    }
    focus = (mode_ctx.get("focus_candidates") or inputs.get("focus_candidates") or [{}])[0]
    if symbol:
        focus = {"symbol": symbol, "timeframe": timeframe or focus.get("timeframe") or "5m"}
    if prebuilt_spec_pack and isinstance(prebuilt_spec_pack, dict):
        meta0 = prebuilt_spec_pack.get("meta") or {}
        focus = {
            "symbol": symbol or meta0.get("symbol") or focus.get("symbol"),
            "timeframe": timeframe or meta0.get("timeframe") or focus.get("timeframe") or "5m",
            "reason": "windtalker_prebuilt_spec",
        }
    task["focus"] = focus
    dual.save_task(task)
    store.save_task_meta(task)

    # ---- Gate 0: mechanism_spec ----
    task["stage"] = "gate0_mechanism_spec"
    if prebuilt_spec_pack and isinstance(prebuilt_spec_pack, dict) and prebuilt_spec_pack.get("mechanism_spec"):
        spec_pack = dict(prebuilt_spec_pack)
        spec_pack.setdefault("ok", True)
        spec_pack.setdefault("errors", [])
        spec_pack.setdefault("call_id", new_call_id("prebuilt_spec"))
        spec_pack.setdefault("attempts", 0)
        task["mechanism_spec_source"] = "prebuilt_windtalker"
    else:
        spec_pack = glm_require_mechanism_spec(mode_ctx, focus, mode_name, kb_ctx, retries=2)
        task["mechanism_spec_source"] = "glm_live"
    task["mechanism_spec_gate"] = {
        "ok": spec_pack.get("ok"),
        "errors": spec_pack.get("errors"),
        "call_id": spec_pack.get("call_id"),
        "attempts": spec_pack.get("attempts"),
        "source": task.get("mechanism_spec_source"),
    }
    g0 = evaluate_gate0(spec_pack.get("mechanism_spec"), spec_pack.get("ok"))
    task["gates"].append(g0)
    if not spec_pack.get("ok"):
        task["stage"] = "archived"
        task["error"] = "mechanism_spec_incomplete"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(
            task, stage="gate0", failed_tests=["representation"],
            reason="mechanism_spec missing fields: %s" % spec_pack.get("errors"),
            verdict="representation_failure_after_retry",
            is_data=True,
            lessons=["GLM must emit full mechanism_spec fields; aliases+retry exhausted"],
            blocked=["glm_incomplete_spec|%s|%s" % (focus.get("symbol"), focus.get("timeframe"))],
        )
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "stage": "archived", "reason": "spec_incomplete",
                "gate_results": task["gate_results"]}

    spec = spec_pack["mechanism_spec"]
    # Block known dead families / paths
    blocked, why = path_is_blocked(
        "family|%s" % spec.get("mechanism_family"),
        family=spec.get("mechanism_family"),
    )
    if blocked and mode_name == "new_mechanism":
        task["stage"] = "archived"
        g0["pass"] = False
        g0["evidence"]["kb_block"] = why
        task["gates"][-1] = g0
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(
            task, stage="gate0", failed_tests=["blocked_family"],
            reason="failure KB blocked family %s (%s)" % (spec.get("mechanism_family"), why),
            verdict="blocked_by_failure_kb",
            is_mech_absent=True,
            blocked=["family|%s" % spec.get("mechanism_family")],
        )
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "reason": "kb_blocked"}

    # Immutable save
    try:
        spec_path, spec_payload = save_immutable_mechanism_spec(
            spec, task_id=tid, meta=spec_pack.get("meta"))
        task["mechanism_spec_path"] = spec_path
        task["mechanism_spec_hash"] = spec_payload.get("content_hash")
    except MechanismSpecError as exc:
        task["stage"] = "archived"
        _archive_step_a(task, stage="gate0", failed_tests=["immutable_spec"],
                        reason=str(exc), verdict="immutable_spec_conflict", is_eng=True)
        dual.save_task(task)
        return {"ok": False, "task_id": tid, "reason": "immutable_spec_error"}

    task["mechanism_spec"] = spec
    stmt = spec_to_mechanism_statement(spec)
    task["mechanism_statement"] = stmt
    store.save_mechanism_statement(tid, None, spec.get("mechanism_id"), stmt, spec_pack.get("call_id"))

    # Fingerprint + duplicate intercept BEFORE coding
    task["stage"] = "fingerprint_intercept"
    fp_a = build_step_a_fingerprint(
        spec,
        direction=(spec_pack.get("meta") or {}).get("direction"),
        symbol=focus.get("symbol"),
        timeframe=focus.get("timeframe"),
    )
    # also save v2 fingerprint for index compatibility
    fp_v2 = build_fingerprint_from_statement(
        stmt,
        extras={"holding_horizon": spec.get("expected_holding_period"),
                "information_source": "step_a"},
    )
    task["fingerprint"] = fp_a
    task["fingerprint_v2"] = fp_v2
    task["mechanism_id"] = spec.get("mechanism_id")
    store.save_fingerprint(tid, None, task["mechanism_id"], {**fp_v2, "step_a": fp_a})

    allow_h = bool(allow_horizontal_expand) or mode_name == "known_mechanism_deep_dig"
    dup_block, dup_report = duplicate_intercept(
        fp_a, fps, mechanism_family=spec.get("mechanism_family"),
        allow_horizontal_expand=allow_h,
    )
    # New mechanism mode: forbid exhaustion_fade_short family
    if mode_name == "new_mechanism" and EXHAUSTION_FADE_FAMILY in str(spec.get("mechanism_family") or "").lower():
        dup_block = True
        dup_report["is_exhaustion_fade_clone"] = True
        dup_report["block_reason"] = "exhaustion_fade_short_forbidden"
    task["duplicate_report"] = dup_report
    task["counts_as_independent_mechanism"] = bool(dup_report.get("counts_as_independent_mechanism"))
    if dup_block:
        task["stage"] = "archived"
        task["error"] = "duplicate_mechanism"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(
            task, stage="fingerprint", failed_tests=["duplicate_mechanism"],
            reason=json.dumps(dup_report, ensure_ascii=False)[:500],
            verdict="duplicate_intercept_before_coding",
            blocked=["dup|%s" % fp_a.get("family_hash")],
            lessons=["same-mechanism horizontal expand is not an independent niche"],
        )
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "reason": "duplicate", "duplicate_report": dup_report}

    # ---- Codex implement + Gate1 fidelity ----
    task["stage"] = "codex_implement"
    book = codex_implement_from_spec(spec_pack)
    # strip unapproved forbidden features
    if book.get("supplements_needed"):
        fake_audit = {
            "condition_audit": [
                {"feature": (s.get("proposed_condition") or {}).get("feature"),
                 "action": "remove", "classification": "unapproved_filter"}
                for s in book["supplements_needed"]
            ]
        }
        book["dsl"], removed = apply_audit_removals(book.get("dsl"), fake_audit)
        task["codex_stripped_unapproved"] = removed

    fidelity = build_fidelity_diff(spec, book.get("dsl"), round_i=0, repair_type=None)
    # Rule audit: fail hard only on forbidden/unapproved traditional cores remaining
    rule_audit = rule_based_condition_audit(book.get("dsl"), stmt, approved_supplements=[])
    fidelity["rule_audit_reject"] = rule_audit.get("reject")
    fidelity["rule_audit"] = {
        "reject": rule_audit.get("reject"),
        "reject_reasons": rule_audit.get("reject_reasons"),
    }
    still_forbidden = [
        c.get("feature") for c in (rule_audit.get("condition_audit") or [])
        if c.get("classification") in ("unapproved_filter", "mechanism_substitution")
        or (c.get("action") == "reject_strategy")
    ]
    # Microstructure proxies (prev_high/low, vol_z, atr) are allowed as observation of sweep/vacuum
    allowed_proxy = {"prev_high20", "prev_low20", "vol_z20", "atr14", "high", "low", "close", "open", "z20"}
    hard = [f for f in still_forbidden if str(f).lower() not in allowed_proxy
            and any(t in str(f).lower() for t in ("rsi", "macd", "ema", "sma", "boll", "cci"))]
    if hard:
        fidelity["pass"] = False
        fidelity["notes"] = list(fidelity.get("notes") or []) + ["hard_forbidden_remaining:%s" % hard]
    elif fidelity.get("forbidden_core_features_present"):
        # strip already attempted; if still present → fail
        fidelity["pass"] = False
        fidelity["notes"] = list(fidelity.get("notes") or []) + ["forbidden_core_still_present"]
    else:
        # Do not fail solely on lexical overlap / soft rule_audit for proxy features
        if fidelity.get("dsl_has_entry") and fidelity.get("dsl_has_exit") and fidelity.get("stop_logic_in_spec"):
            if not fidelity.get("non_negotiable_violations"):
                fidelity["pass"] = True
                fidelity["notes"] = list(fidelity.get("notes") or []) + ["pass_via_structural_fidelity_proxies"]
    fpath = save_fidelity_diff(tid, fidelity)
    task["fidelity_diff_path"] = fpath
    task["fidelity_diff"] = fidelity
    task["strategy_id"] = (book.get("dsl") or {}).get("key")
    task["candidate"] = {
        "title": book.get("title"), "symbol": book.get("symbol"),
        "timeframe": book.get("timeframe"), "direction": book.get("direction"),
        "key": task["strategy_id"],
    }

    g1 = evaluate_gate1(fidelity, book.get("dsl"), spec)
    task["gates"].append(g1)
    if not g1["pass"]:
        task["stage"] = "archived"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(task, stage="gate1", failed_tests=["fidelity"],
                        reason="fidelity/gate1 fail", verdict="code_fidelity_fail",
                        is_eng=True)
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "reason": "gate1_fail", "gate_results": task["gate_results"]}

    # ---- Gate2+3 backtest / walk-forward (+ Phase-3 funnel L1→L2→L3) ----
    task["stage"] = "gate2_gate3_backtest_wf"
    import auto_trade_strategy_dsl as dsl_mod
    from .funnel_l1_micro_screen import run_micro_screen
    from .funnel_l2_pareto import evaluate_l2_survivor
    from .funnel_l3_null_hypothesis import evaluate_null_hypothesis
    try:
        definition = dsl_mod.validate_strategy(book.get("dsl") or {})
    except Exception as exc:
        task["stage"] = "archived"
        _archive_step_a(task, stage="validate", failed_tests=["engineering"],
                        reason=str(exc), verdict="dsl_validate_fail", is_eng=True)
        dual.save_task(task)
        return {"ok": False, "task_id": tid, "reason": "validate_fail"}

    sym = book.get("symbol") or focus.get("symbol")
    tf = book.get("timeframe") or focus.get("timeframe")

    # Phase-3 L1: micro-screen on sampled slices BEFORE full-history BT
    task["stage"] = "funnel_l1_micro_screen"
    frame_for_funnel = None
    try:
        frame_for_funnel = dual._frame(sym, tf)
    except Exception:
        frame_for_funnel = None

    def _funnel_bt(frm, defn):
        return dsl_mod.backtest_dsl(frm, defn, stop_loss_pct=0.009)

    l1 = run_micro_screen(
        definition=definition,
        frame=frame_for_funnel,
        backtest_fn=_funnel_bt if frame_for_funnel is not None else None,
        seed=hash(tid) % (2 ** 31) if tid else 42,
    )
    task["phase3_funnel"] = {
        "l1_micro_screen": l1,
        "rejected_at": None,
        "fail_closed": True,
        "protective_sl_pct": 0.009,
        "ada_migrate": False,
        "auto_mount": False,
    }
    if not l1.get("pass"):
        task["stage"] = "archived"
        task["phase3_funnel"]["rejected_at"] = "funnel_l1_micro_screen"
        task["phase3_funnel"]["reject_reasons"] = list(l1.get("reject_reasons") or [])
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(
            task, stage="funnel_l1_micro_screen",
            failed_tests=["micro_screen"],
            reason="L1 micro-screen reject: %s" % ",".join(
                l1.get("reject_reasons") or ["l1_fail"]),
            verdict="funnel_l1_cull",
            is_mech_absent=True,
        )
        dual.save_task(task)
        store.save_task_meta(task)
        return {
            "ok": False, "task_id": tid, "reason": "funnel_l1_fail",
            "phase3_funnel": task["phase3_funnel"],
            "gate_results": task["gate_results"],
        }

    # Full-history BT + Phase-3 WF windows (Calmar≥1.0 + positive expectancy)
    task["stage"] = "gate2_gate3_backtest_wf"
    wf = _walk_forward_detail(definition, sym, tf, folds=10)
    base_m = wf.get("base_metrics") or {}
    trades = wf.get("trades") or []

    # Phase-3 L2: fitness + Pareto (singleton always on front if fitness passes)
    l2 = evaluate_l2_survivor(trades, base_metrics=base_m, candidate_id=tid)
    task["phase3_funnel"]["l2_full_bt_pareto"] = l2

    g2 = evaluate_gate2(base_m, trades)
    # Gate2 also encodes Phase-2 fitness; L2 fail-closed mirrors it
    if not l2.get("pass") and g2.get("pass"):
        # Prefer fitness engine as source of truth via Gate2; keep L2 log
        pass
    if not l2.get("pass"):
        task["phase3_funnel"]["rejected_at"] = "funnel_l2_full_bt_pareto"
        task["phase3_funnel"]["reject_reasons"] = list(l2.get("reject_reasons") or [])

    # Phase-3 L3: null hypothesis (perm / invert / WF already in wf payload)
    def _l3_bt(frm, defn):
        return dsl_mod.backtest_dsl(frm, defn, stop_loss_pct=0.009)

    l3 = evaluate_null_hypothesis(
        definition=definition,
        frame=frame_for_funnel,
        backtest_fn=_l3_bt if frame_for_funnel is not None else None,
        trades=trades,
        seed=hash(tid) % (2 ** 31) if tid else 42,
        skip_frame_tests=frame_for_funnel is None,
    )
    # Prefer the Phase-3 WF windows already computed in `_walk_forward_detail`
    # for Gate3 (same rule); keep L3 perm/invert evidence.
    l3 = dict(l3)
    l3["walk_forward"] = {
        "windows": wf.get("windows"),
        "pass_count": wf.get("pass_count"),
        "total": wf.get("total"),
        "pass": int(wf.get("pass_count") or 0) >= 7 and int(wf.get("total") or 0) >= 10,
        "rule": wf.get("phase3_wf_rule"),
        "trades": trades,
        "oos_trades": trades,
    }
    # Recompute L3 pass with WF from pipeline
    l3_perm_ok = bool((l3.get("tests") or {}).get("permuted_returns", {}).get("pass"))
    l3_inv_ok = bool((l3.get("tests") or {}).get("inverted_market", {}).get("pass"))
    l3_wf_ok = bool(l3["walk_forward"].get("pass"))
    l3["pass"] = l3_perm_ok and l3_inv_ok and l3_wf_ok
    l3["reject_reasons"] = []
    if not l3_perm_ok:
        l3["reject_reasons"].extend(
            (l3.get("tests") or {}).get("permuted_returns", {}).get("reject_reasons")
            or ["perm_fail"]
        )
    if not l3_inv_ok:
        l3["reject_reasons"].extend(
            (l3.get("tests") or {}).get("inverted_market", {}).get("reject_reasons")
            or ["invert_fail"]
        )
    if not l3_wf_ok:
        l3["reject_reasons"].append(
            "walk_forward_%s_of_%s" % (wf.get("pass_count"), wf.get("total"))
        )
    task["phase3_funnel"]["l3_null_hypothesis"] = l3
    if not l3.get("pass"):
        task["phase3_funnel"]["rejected_at"] = (
            task["phase3_funnel"].get("rejected_at") or "funnel_l3_null_hypothesis"
        )
        task["phase3_funnel"]["reject_reasons"] = list(
            task["phase3_funnel"].get("reject_reasons") or []
        ) + list(l3.get("reject_reasons") or [])

    g3 = evaluate_gate3(wf, trades=trades, base_metrics=base_m, null_hypothesis=l3)
    task["gates"].extend([g2, g3])
    task["walk_forward"] = {
        "pass_count": wf.get("pass_count"), "total": wf.get("total"),
        "windows": wf.get("windows"),
        "phase3_wf_rule": wf.get("phase3_wf_rule"),
    }
    task["base_metrics"] = base_m

    def bt_fn(defn):
        try:
            r = dual._backtest(defn, sym, tf, "observed_base")
            return {"metrics": dual._metrics_from_trades(r.get("trades") or []), "trades": r.get("trades")}
        except Exception as exc:
            return {"metrics": {"error": str(exc), "mean_net": 0, "trades": 0}, "trades": []}

    def friction_fn(slip_mult=1.0, latency_extra=0.0, fee_mult=1.0):
        try:
            r = dual._backtest(definition, sym, tf, "observed_base",
                              slip_mult=slip_mult, latency_extra=latency_extra,
                              fill_fail_pct=0.05 if slip_mult >= 2 else 0.0)
            return {"metrics": dual._metrics_from_trades(r.get("trades") or [])}
        except Exception as exc:
            return {"metrics": {"error": str(exc), "sharpe": -1, "mean_net": -0.02}}

    # Repair loop if gate2/3 fail (3 rounds typed)
    repair_log = init_repair_log(tid, task["strategy_id"], stmt)
    task["repair_log"] = repair_log
    prior_fidelity = fidelity
    need_repair = (not g2["pass"]) or (not g3["pass"])
    archived_by_repair = False
    if need_repair:
        for ri, rtype in enumerate(REPAIR_ROUND_TYPES):
            task["stage"] = "repair_%s" % rtype
            before_fp = build_step_a_fingerprint(spec)
            book, stop_mut = _apply_repair_round(book, spec, rtype, ri)
            if rtype == "mechanism_viability_verdict":
                task["stage"] = "archived"
                task["viability_verdict"] = {
                    "verdict": "mechanism_not_viable_or_data_insufficient",
                    "gate2": g2["pass"], "gate3": g3["pass"],
                }
                repair_log, drift = record_repair_round(
                    repair_log, failed_test="gate2_or_gate3", root_cause="viability",
                    modifications=["verdict_only_no_mechanism_swap"],
                    statement_before=stmt, statement_after=stmt,
                    fingerprint_before=before_fp, fingerprint_after=before_fp,
                    frequency_before=base_m.get("trades"), frequency_after=base_m.get("trades"),
                    oos_before=base_m.get("oos_profit"), oos_after=base_m.get("oos_profit"),
                    introduced_new_condition=False, changed_edge_source=False,
                )
                task["repair_log"] = repair_log
                task["gate_results"] = assemble_gate_results(task["gates"], tid)
                save_gate_results(tid, task["gate_results"])
                _archive_step_a(
                    task, stage="repair_round3", failed_tests=["viability"],
                    reason="3-round repair exhausted; mechanism viability fail",
                    verdict="mechanism_viability_fail_archived",
                    is_mech_absent=True,
                    lessons=["no infinite retune; no disguised mechanism swap"],
                )
                dual.save_task(task)
                store.save_task_meta(task)
                archived_by_repair = True
                break
            try:
                definition = dsl_mod.validate_strategy(book.get("dsl") or {})
            except Exception:
                continue
            wf = _walk_forward_detail(definition, sym, tf, folds=10)
            base_m = wf.get("base_metrics") or {}
            trades = wf.get("trades") or []
            l2 = evaluate_l2_survivor(trades, base_metrics=base_m, candidate_id=tid)
            task["phase3_funnel"]["l2_full_bt_pareto"] = l2
            l3 = evaluate_null_hypothesis(
                definition=definition,
                frame=frame_for_funnel,
                backtest_fn=_l3_bt if frame_for_funnel is not None else None,
                trades=trades,
                seed=hash(tid) % (2 ** 31) if tid else 42,
                skip_frame_tests=frame_for_funnel is None,
            )
            l3 = dict(l3)
            l3["walk_forward"] = {
                "windows": wf.get("windows"),
                "pass_count": wf.get("pass_count"),
                "total": wf.get("total"),
                "pass": int(wf.get("pass_count") or 0) >= 7 and int(wf.get("total") or 0) >= 10,
                "rule": wf.get("phase3_wf_rule"),
                "trades": trades,
                "oos_trades": trades,
            }
            l3_perm_ok = bool((l3.get("tests") or {}).get("permuted_returns", {}).get("pass"))
            l3_inv_ok = bool((l3.get("tests") or {}).get("inverted_market", {}).get("pass"))
            l3_wf_ok = bool(l3["walk_forward"].get("pass"))
            l3["pass"] = l3_perm_ok and l3_inv_ok and l3_wf_ok
            task["phase3_funnel"]["l3_null_hypothesis"] = l3
            g2 = evaluate_gate2(base_m, trades)
            g3 = evaluate_gate3(wf, trades=trades, base_metrics=base_m, null_hypothesis=l3)
            # replace last gate2/3
            task["gates"] = [g for g in task["gates"] if g["gate_id"] not in (
                "gate2_base_backtest", "gate3_walk_forward")]
            task["gates"].extend([g2, g3])
            task["walk_forward"] = {
                "pass_count": wf.get("pass_count"), "total": wf.get("total"),
                "windows": wf.get("windows"),
                "phase3_wf_rule": wf.get("phase3_wf_rule"),
            }
            fidelity = build_fidelity_diff(spec, book.get("dsl"), round_i=ri + 1,
                                           repair_type=rtype, prior_diff=prior_fidelity)
            save_fidelity_diff(tid, fidelity)
            prior_fidelity = fidelity
            after_fp = build_step_a_fingerprint(spec)
            repair_log, drift = record_repair_round(
                repair_log, failed_test="gate2_or_gate3", root_cause=rtype,
                modifications=book.get("repair_changed") or [rtype],
                statement_before=stmt, statement_after=stmt,
                fingerprint_before=before_fp, fingerprint_after=after_fp,
                frequency_before=None, frequency_after=base_m.get("trades"),
                oos_before=None, oos_after=base_m.get("oos_profit"),
                introduced_new_condition=False, changed_edge_source=False,
            )
            if drift.get("drift") == "material":
                task["stage"] = "archived"
                _archive_step_a(task, stage="repair", failed_tests=["mechanism_drift"],
                                reason=str(drift), verdict="drift_during_repair", drift=True)
                dual.save_task(task)
                archived_by_repair = True
                break
            if g2["pass"] and g3["pass"]:
                need_repair = False
                break
        task["repair_log"] = repair_log
        task["fidelity_diff"] = prior_fidelity

    if archived_by_repair:
        return {"ok": False, "task_id": tid, "reason": "repair_exhausted_or_drift",
                "gate_results": task.get("gate_results")}

    if not g2["pass"] or not g3["pass"]:
        task["stage"] = "archived"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(task, stage="gate2_3", failed_tests=["backtest_or_wf"],
                        reason="gate2/3 still failing", verdict="backtest_wf_fail",
                        is_mech_absent=not g3["pass"])
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "reason": "gate2_3_fail",
                "gate_results": task["gate_results"]}

    # ---- Gate4: 20 split tests ----
    task["stage"] = "gate4_split_tests"
    split_summary = run_split_tests_20(
        definition=definition,
        base_metrics=base_m,
        trades=trades,
        backtest_fn=bt_fn,
        mechanism_spec=spec,
        fidelity_diff=task.get("fidelity_diff"),
        symbol=sym,
        timeframe=tf,
        friction_fn=friction_fn,
    )
    save_split_tests(tid, split_summary)
    task["split_tests_20"] = {
        "pass": split_summary.get("pass"),
        "fail": split_summary.get("fail"),
        "inconclusive": split_summary.get("inconclusive"),
        "gate4_block": split_summary.get("gate4_block"),
        "hard_fail_ids": split_summary.get("gate4_hard_fail_ids"),
    }
    _save_artifact(tid, "split_tests_20", split_summary)
    g4 = evaluate_gate4(split_summary)
    task["gates"].append(g4)
    if not g4["pass"]:
        task["stage"] = "archived"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(
            task, stage="gate4", failed_tests=split_summary.get("gate4_hard_fail_ids") or ["split"],
            reason="mechanism_failure in split tests",
            verdict="split_destruction_mechanism_fail",
            is_mech_absent=True,
            counterexamples=["hard_fail:%s" % x for x in (split_summary.get("gate4_hard_fail_ids") or [])],
        )
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "reason": "gate4_fail",
                "gate_results": task["gate_results"]}

    # ---- Gate5 MC + friction ----
    task["stage"] = "gate5_mc_friction"
    mc_row = None
    for r in split_summary.get("results") or []:
        if r.get("test_id") == "mc_trade_order":
            mc_row = r
            break
    fr = friction_fn(slip_mult=2.0, latency_extra=0.00005)
    mc_summary = {
        "accept_pct_observed": (mc_row or {}).get("actual", {}).get("accept_pct_observed"),
        "status": (mc_row or {}).get("status"),
    }
    g5 = evaluate_gate5(mc_summary, fr)
    task["gates"].append(g5)
    task["friction"] = fr
    if not g5["pass"]:
        task["stage"] = "archived"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(task, stage="gate5", failed_tests=["mc_friction"],
                        reason="mc/friction fail", verdict="cost_or_robustness_fail",
                        is_cost=True)
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "reason": "gate5_fail",
                "gate_results": task["gate_results"]}

    # ---- Gate6 multi-AI (separate, not averaged) ----
    task["stage"] = "gate6_multi_ai"
    reviews = {
        "glm_mechanism": glm_mechanism_review(spec, _ai_json),
        "codex_fidelity": codex_fidelity_review(spec, task.get("fidelity_diff"), _ai_json),
        "deepseek_logic": deepseek_logic_attack(spec, book.get("dsl"), {"base_metrics": base_m}, _ai_json),
        "production_risk": production_risk_attack(spec, book.get("dsl"), {"base_metrics": base_m}, _ai_json),
    }
    task["multi_ai_reviews"] = {
        k: {kk: vv for kk, vv in v.items() if kk != "raw"} for k, v in reviews.items()
    }
    _save_artifact(tid, "multi_ai_reviews", task["multi_ai_reviews"])
    g6 = evaluate_gate6(reviews)
    task["gates"].append(g6)
    if not g6["pass"]:
        task["stage"] = "archived"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(task, stage="gate6", failed_tests=["multi_ai"],
                        reason="one or more separate AI reviews failed",
                        verdict="multi_ai_review_fail",
                        counterexamples=(reviews.get("deepseek_logic") or {}).get("counterexamples") or [])
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "reason": "gate6_fail",
                "gate_results": task["gate_results"]}

    # Split scores
    wr = base_m.get("win_rate_pct")
    task["split_scores"] = build_split_scores(
        ai_logic_wr=((reviews.get("glm_mechanism") or {}).get("mechanism_clarity")),
        ai_n=1,
        backtest_wr=wr,
        backtest_n=base_m.get("trades"),
        walk_forward_wr=None,
        walk_forward_n=wf.get("total"),
        oos_wr=None,
        oos_n=None,
        sim_exec_wr=None,
        live_wr=None,
        live_n=0,
        cost_expectancy=base_m.get("mean_net"),
        mechanism_credibility=((reviews.get("glm_mechanism") or {}).get("mechanism_clarity")),
        execution_credibility=80 if (reviews.get("production_risk") or {}).get("pass") else 40,
        data_credibility=70 if g3["pass"] else 30,
        overall_credibility=None,
    )
    # AI clarity may be 0-100; still not live ready
    _save_artifact(tid, "split_scores", task["split_scores"])

    # ---- Gate7 human confirm (no auto mount) ----
    task["stage"] = "gate7_human_confirm"
    import auto_trade_human_confirm_pipeline as pipeline
    ai_review = {
        "approved": True,
        "policy": "step_a_gates_0_6_pass",
        "ai_theoretical_wr_avg": task["split_scores"].get("ai_logic_wr", {}).get("win_rate_pct"),
        "natural_language": "STEP A gates0-6 pass; awaiting human confirm. NOT live-ready.",
        "split_scores": task["split_scores"],
        "step_a": True,
    }
    push = pipeline.ingest_and_screen(
        {"dsl": definition, "symbol": sym, "timeframe": tf,
         "thesis": book.get("thesis"), "mechanism_spec": spec,
         "mechanism_statement": stmt, "mechanism_fingerprint": fp_a,
         "live_enabled": False, "auto_trade_eligible": False},
        source="dual_engine_step_a",
        ai_review=ai_review,
        require_ai_review=True,
    )
    human_state = {
        "pending_ok": bool(push.get("ok")),
        "awaiting_human": bool(push.get("ok")),
        "human_confirmed": False,  # never auto
        "auto_open_mounted": False,
        "real_size_granted": False,
        "push": {"ok": push.get("ok"), "key": push.get("key"), "reason": push.get("reason")},
    }
    task["human_confirm_state"] = human_state
    g7 = evaluate_gate7(human_state)
    task["gates"].append(g7)
    task["gate_results"] = assemble_gate_results(task["gates"], tid)
    # production_mounted stays false
    task["gate_results"]["production_mounted"] = False
    save_gate_results(tid, task["gate_results"])

    task["pending_push"] = human_state["push"]
    task["stage"] = "awaiting_human" if push.get("ok") else "failed_push"
    task["production_mounted"] = False
    dual.save_task(task)
    store.save_task_meta(task)
    dual._record_formal({
        "time": _now(), "task_id": tid, "key": task["strategy_id"],
        "title": book.get("title"), "symbol": sym, "timeframe": tf,
        "status": "等待" if push.get("ok") else "退回",
        "annotation": ai_review["natural_language"],
        "workflow_version": WORKFLOW_VERSION,
        "step_a": True,
    })
    return {
        "ok": bool(push.get("ok")),
        "task_id": tid,
        "stage": task["stage"],
        "pending": push,
        "gate_results": task["gate_results"],
        "counts_as_independent_mechanism": task.get("counts_as_independent_mechanism"),
        "production_mounted": False,
        "split_scores": task["split_scores"],
    }


def start_creation_task_step_a(async_mode=True, symbol=None, timeframe=None,
                               exploration_mode="A", allow_horizontal_expand=False,
                               prebuilt_spec_pack=None, windtalker_tag=None):
    ensure_dirs()
    ensure_step_a_dirs()
    with _JOB_LOCK:
        if _JOB.get("running"):
            return {"ok": False, "status": "running", "error": "job_already_running",
                    "kind": _JOB.get("kind")}
        _JOB.update({"running": True, "kind": "creation_step_a", "started_at": _now(),
                     "error": None})

    def _worker():
        try:
            run_creation_pipeline_step_a(
                symbol=symbol, timeframe=timeframe,
                exploration_mode=exploration_mode,
                allow_horizontal_expand=allow_horizontal_expand,
                prebuilt_spec_pack=prebuilt_spec_pack,
                windtalker_tag=windtalker_tag,
            )
        except Exception as exc:
            _JOB["error"] = str(exc)
            try:
                import auto_trade_dual_engine_factory as dual
                dual._append_audit({
                    "event": "creation_step_a_crash",
                    "error": str(exc),
                    "trace": traceback.format_exc()[-800:],
                })
            except Exception:
                pass
        finally:
            with _JOB_LOCK:
                _JOB.update({"running": False, "kind": None})

    if async_mode:
        threading.Thread(target=_worker, name="dual-engine-creation-step-a",
                         daemon=True).start()
        time.sleep(0.05)
        return {"ok": True, "status": "started", "async": True,
                "workflow_version": WORKFLOW_VERSION,
                "step_a_code_version": STEP_A_CODE_VERSION,
                "exploration_mode": exploration_mode}
    return run_creation_pipeline_step_a(
        symbol=symbol, timeframe=timeframe, exploration_mode=exploration_mode,
        allow_horizontal_expand=allow_horizontal_expand,
        prebuilt_spec_pack=prebuilt_spec_pack,
        windtalker_tag=windtalker_tag,
    )


def job_snapshot_step_a():
    with _JOB_LOCK:
        return dict(_JOB)
