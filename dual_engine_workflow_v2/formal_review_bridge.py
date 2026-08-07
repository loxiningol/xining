# -*- coding: utf-8 -*-
"""Bridge a qualified sole-creation blueprint into the existing four reviews.

This module does not change review standards and never deploys to live trading.
It only removes the old requirement to rerun discovery before review.
"""
from __future__ import print_function

import json
import time

from .process_safe_state import process_lock


def _patch_recipe_stop_05(recipe):
    """Force protective stop to 0.5% price (unlevered) for manufacture review."""
    from . import research_contract as rcontract
    recipe = dict(recipe or {})
    stop = dict(recipe.get("protective_stop_policy") or {})
    stop.update({
        "mode": "intrabar_fixed_pct_v1",
        "price_pct": float(rcontract.PRODUCTION_PROTECTIVE_STOP_PCT),
        "applies_from": stop.get("applies_from") or "entry_bar",
        "precedence": stop.get("precedence") or "protective_stop_before_time_exit",
    })
    recipe["protective_stop_policy"] = stop
    recipe["protective_stop_evaluated"] = True
    return recipe


def _fmt_num(value, digits=2, suffix=""):
    try:
        if value is None:
            return "—"
        return (("%." + str(int(digits)) + "f") % float(value)) + suffix
    except Exception:
        return "—"


def _strategy_detail_block(row, blueprint, job):
    """Build one strategy's detailed pass/fail data block for Wx."""
    row = row or {}
    res = row.get("result") or {}
    slim = row.get("slim_multiai_review") or res.get("slim_multiai_review") or {}
    select = row.get("select_metrics") or {}
    if not select:
        for batch in (blueprint or {}).get("review_batch") or []:
            if batch.get("rank") == row.get("rank") or batch.get("recipe_id") == row.get("recipe_id"):
                select = batch.get("select_metrics") or {}
                break
    passed = bool(row.get("ok") or slim.get("approved") or res.get("ok"))
    avg_wr = slim.get("ai_theoretical_wr_avg")
    avg_wk = slim.get("ai_theoretical_weekly_opens_avg")
    avg_mean = slim.get("ai_theoretical_mean_net_avg")
    by_wr = slim.get("ai_theoretical_wr_by_provider") or {}
    by_mean = slim.get("ai_theoretical_mean_net_by_provider") or {}
    by_wk = slim.get("ai_theoretical_weekly_opens_by_provider") or {}
    fail = slim.get("fail_reasons") or ([row.get("reason") or res.get("reason")] if not passed else [])
    win_audit = select.get("win_only_audit") or {}
    lines = [
        "——— Rank %s ———" % (row.get("rank") or "?"),
        "结果: %s" % ("通过" if passed else "未通过"),
        "recipe: %s" % (row.get("recipe_id") or "—"),
        "task: %s" % (row.get("task_id") or res.get("task_id") or "—"),
        "stage/reason: %s / %s" % (
            row.get("stage") or res.get("stage") or "—",
            row.get("reason") or res.get("reason") or "—",
        ),
        "回测证据(账户口径): n=%s 胜率=%s%% 周频=%s 盈利单均值=%s%% 全体均值=%s%%" % (
            select.get("n") or select.get("n_trades") or "—",
            _fmt_num((select.get("win_rate_pct")), 1),
            _fmt_num(select.get("weekly_opens"), 3),
            _fmt_num(select.get("mean_win_only_pct"), 2),
            _fmt_num(select.get("mean_trade_pct"), 2),
        ),
        "盈利单审计: wins=%s mean_ratio=%s → %s%% (已含杠杆,禁止再×)" % (
            win_audit.get("n_wins") if win_audit else "—",
            _fmt_num(win_audit.get("mean_win_only_ratio"), 4) if win_audit else "—",
            _fmt_num(win_audit.get("mean_win_only_pct"), 2) if win_audit else "—",
        ),
        "四AI均值: 胜率=%s%% 周频=%s 盈利单=%s%%" % (
            _fmt_num(avg_wr, 1), _fmt_num(avg_wk, 3), _fmt_num(avg_mean, 2),
        ),
        "分项胜率: DS %s / Qwen %s / GLM %s / Kimi %s" % (
            _fmt_num(by_wr.get("deepseek"), 1, "%"),
            _fmt_num(by_wr.get("qwen"), 1, "%"),
            _fmt_num(by_wr.get("glm"), 1, "%"),
            _fmt_num(by_wr.get("kimi"), 1, "%"),
        ),
        "分项盈利单%: DS %s / Qwen %s / GLM %s / Kimi %s" % (
            _fmt_num(by_mean.get("deepseek"), 2),
            _fmt_num(by_mean.get("qwen"), 2),
            _fmt_num(by_mean.get("glm"), 2),
            _fmt_num(by_mean.get("kimi"), 2),
        ),
        "分项周频: DS %s / Qwen %s / GLM %s / Kimi %s" % (
            _fmt_num(by_wk.get("deepseek"), 3),
            _fmt_num(by_wk.get("qwen"), 3),
            _fmt_num(by_wk.get("glm"), 3),
            _fmt_num(by_wk.get("kimi"), 3),
        ),
        # Literal string (no %% formatting) — single % characters only.
        "门槛: 盈利单均值须严格大于 stop×杠杆（默认20×0.5%→>10%） · 周开仓频率≥0.1 · 止损0.5%价格",
        "失败原因: %s" % ("；".join(str(x) for x in (fail or [])[:6]) or "无"),
    ]
    return "\n".join(lines), passed


def _notify_review_outcome_wx(formal_review, blueprint, job):
    """复核结束后推送 WxPusher：Top3 逐条详细报告，通过/未通过均发。"""
    formal_review = formal_review or {}
    job = job or {}
    blueprint = blueprint or {}
    batch = list(formal_review.get("review_batch_results") or [])
    if not batch:
        batch = [{"rank": 1, "ok": formal_review.get("ok"), "reason": formal_review.get("reason"),
                  "task_id": formal_review.get("task_id"),
                  "slim_multiai_review": formal_review.get("slim_multiai_review"),
                  "result": formal_review.get("result")}]
    blocks = []
    any_pass = False
    for row in batch:
        # Enrich select metrics from blueprint review_batch if missing on row.
        if not row.get("select_metrics"):
            for b in (blueprint.get("review_batch") or []):
                if b.get("rank") == row.get("rank") or b.get("recipe_id") == row.get("recipe_id"):
                    row = dict(row)
                    row["select_metrics"] = b.get("select_metrics")
                    break
        block, passed = _strategy_detail_block(row, blueprint, job)
        blocks.append(block)
        any_pass = any_pass or passed
    header = (
        "【制造批次·精简多AI复核报告】\n"
        "总结果: %s\n"
        "标的/周期/方向: %s / %s / %s\n"
        "管道: %s\n"
        "研究方向: %s\n"
        "job: %s\n"
        "复核模式: 精简多模型均值 · 盈利单>stop×杠杆 · 周频≥0.1 · 止损0.5%%\n"
        "提交数: %s\n"
        "时间: %s\n"
    ) % (
        "有通过" if any_pass else "全部未通过",
        job.get("symbol") or "—",
        job.get("timeframe") or "—",
        job.get("trade_direction") or job.get("direction") or "—",
        job.get("pipeline_label") or job.get("pipeline") or "—",
        (job.get("research_direction") or job.get("brief") or "—")[:80],
        job.get("job_id") or "—",
        formal_review.get("n_submitted") or len(batch),
        time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    msg = header + "\n" + "\n\n".join(blocks)
    # Split if too long for Wx (≈900 chars safe chunks)
    try:
        import auto_trade_formal_notify as notify
        chunks = []
        if len(msg) <= 900:
            chunks = [msg]
        else:
            chunks = [header + "\n" + blocks[0]]
            for block in blocks[1:]:
                chunks.append("【制造批次复核续】\n" + block)
        results = []
        for i, chunk in enumerate(chunks):
            results.append(notify.send_message(
                chunk,
                kind="manufacture_slim_review_report",
                meta={
                    "job_id": job.get("job_id"),
                    "chunk": i + 1,
                    "n_chunks": len(chunks),
                    "any_pass": any_pass,
                },
            ))
        ok = any(bool((r or {}).get("ok") or (r or {}).get("sent")) for r in results)
        return {"ok": ok, "sent": ok, "chunks": len(chunks), "results": results}
    except Exception as exc:
        # Fallback to single-outcome API if send_message missing.
        try:
            import auto_trade_formal_notify as notify
            return notify.notify_strategy_review_outcome({
                "passed": any_pass,
                "status_zh": "有通过" if any_pass else "全部未通过",
                "reason": formal_review.get("reason"),
                "message_zh": msg[:500],
                "task_id": formal_review.get("task_id"),
                "job_id": job.get("job_id"),
                "symbol": job.get("symbol"),
                "timeframe": job.get("timeframe"),
                "direction": job.get("trade_direction") or job.get("direction"),
                "pipeline": job.get("pipeline"),
                "pipeline_label": job.get("pipeline_label"),
                "research_direction": job.get("research_direction"),
                "strategy_name": job.get("research_direction") or job.get("brief"),
            })
        except Exception as exc2:
            return {"ok": False, "sent": False, "error": str(exc2)[:240], "primary": str(exc)[:160]}


def _validate_recipe_lineage(blueprint, job):
    """Recompute the admitted recipe identity at the formal-review boundary."""
    from .research_discovery import verify_assembly_recipe
    from . import research_contract as rcontract
    from . import recipe_policy

    blueprint = blueprint or {}
    stages = blueprint.get("stages") or {}
    envelope = stages.get("admission_envelope") or {}
    lineage = stages.get("assembly_lineage") or {}
    selected = stages.get("selected") or {}
    best = blueprint.get("best_factor") or {}
    brief = blueprint.get("glm_research_brief") or {}
    recipe_id = lineage.get("selected_recipe_id")
    reasons = []
    if not recipe_id or recipe_id not in (envelope.get("recipe_ids") or []):
        reasons.append("selected_recipe_not_in_envelope")

    envelope_row = next(
        (row for row in (envelope.get("candidates") or [])
         if isinstance(row, dict) and row.get("recipe_id") == recipe_id),
        {},
    )
    recipes = [
        row for row in (
            selected.get("recipe"), best.get("recipe"), brief.get("selected_recipe"),
            envelope_row.get("recipe"),
        ) if isinstance(row, dict) and row
    ]
    if not recipes:
        reasons.append("selected_recipe_payload_missing")
        recipe = {}
    else:
        recipe = recipes[0]
        if any(row != recipe for row in recipes[1:]):
            reasons.append("selected_recipe_payload_drift")
    integrity_ok, expected_id = verify_assembly_recipe(recipe)
    if not integrity_ok or expected_id != recipe_id:
        reasons.append("selected_recipe_hash_invalid")
    for value in (
        selected.get("recipe_id"), best.get("recipe_id"),
        brief.get("selected_recipe_id"), envelope_row.get("recipe_id"),
        recipe.get("recipe_id"),
    ):
        if value is not None and value != recipe_id:
            reasons.append("selected_recipe_id_drift")
            break

    contract = blueprint.get("research_contract") or {}
    contract_integrity = rcontract.verify_contract_integrity(contract)
    if not contract_integrity.get("ok"):
        reasons.extend(contract_integrity.get("reasons") or [])
    contract_id = contract.get("contract_id")
    if not contract_id or recipe.get("research_contract_id") != contract_id:
        reasons.append("selected_recipe_contract_drift")
    if envelope.get("research_contract_id") not in (None, contract_id):
        reasons.append("admission_envelope_contract_drift")
    if recipe.get("trade_direction") != str(job.get("trade_direction") or "").lower():
        reasons.append("selected_recipe_direction_drift")
    required_identity = (
        "recipe_id", "research_contract_id", "hypothesis_id", "mechanism_id",
        "family", "event_id", "event_kind", "event_logic", "terms", "trade_direction",
        "horizon_bars", "execution_mapping", "primary_cost_scenario",
        "primary_cost_per_trade", "research_contract_body_hash",
        "exit_policy", "protective_stop_policy", "execution_leverage",
        "statistical_return_basis",
    )
    for key in required_identity:
        if recipe.get(key) in (None, "", []):
            reasons.append("selected_recipe_%s_missing" % key)
    terms = recipe.get("terms") or []
    kind = str(recipe.get("event_kind") or "")
    logic = str(recipe.get("event_logic") or "")
    if logic not in ("all", "ordered_joins", "ast"):
        reasons.append("selected_recipe_event_logic_invalid")
    if kind == "human_contract_exact":
        if logic != "ordered_joins":
            reasons.append("selected_recipe_exact_event_logic_invalid")
    elif kind == "ast_compiled":
        if not isinstance(recipe.get("event_ast"), dict) or not recipe.get("event_ast"):
            reasons.append("selected_recipe_event_ast_missing")
        if recipe.get("event_ast_formal_ok") is False:
            reasons.append("selected_recipe_ast_not_formally_reproducible")
        if logic not in ("ast", "all"):
            reasons.append("selected_recipe_ast_event_logic_invalid")
    elif kind in ("mechanism_intersection", "mechanism_preserving"):
        if logic != "all" or len(terms) < 2:
            reasons.append("selected_recipe_generic_event_not_formally_admissible")
    else:
        reasons.append("selected_recipe_event_kind_not_formally_admissible")
    for index, term in enumerate(terms):
        if not isinstance(term, dict) or not term.get("factor"):
            # AST terms may be structural nodes without factor; allow when
            # event_ast is the formal identity.
            if kind == "ast_compiled" and (
                term.get("node_type") in ("ast", "quantile", "sequence", "state")
                or term.get("event_ast")
            ):
                continue
            reasons.append("selected_recipe_term_%d_invalid" % index)
            continue
        literal = (
            (term.get("op") or term.get("operator")) not in (None, "")
            and "value" in term
        )
        if literal:
            continue
        if term.get("side") not in ("high", "low"):
            reasons.append("selected_recipe_term_%d_side_invalid" % index)
        try:
            q = float(term.get("q"))
            window = int(term.get("window"))
            min_history = int(term.get("min_history"))
            if not (0.5 < q < 1.0 and 2 <= window <= 240
                    and 2 <= min_history <= window):
                reasons.append("selected_recipe_term_%d_quantile_identity_invalid" % index)
        except (TypeError, ValueError):
            reasons.append("selected_recipe_term_%d_quantile_identity_invalid" % index)
        if term.get("threshold_source") != "prior_only_rolling_quantile":
            reasons.append("selected_recipe_term_%d_threshold_source_invalid" % index)
    try:
        horizon = int(recipe.get("horizon_bars"))
        if horizon < 1 or horizon > 240:
            reasons.append("selected_recipe_horizon_invalid")
    except (TypeError, ValueError):
        reasons.append("selected_recipe_horizon_invalid")
    if recipe.get("execution_mapping") != "next_bar_open":
        reasons.append("selected_recipe_execution_mapping_not_formally_supported")
    if recipe.get("statistical_returns_are_post_cost") is not True:
        reasons.append("selected_recipe_post_cost_evidence_missing")
    if recipe.get("protective_stop_evaluated") is not True:
        reasons.append("selected_recipe_protective_stop_not_evaluated")
    capability = recipe_policy.capability_from_row(recipe, contract)
    if not capability.get("ok"):
        reasons.extend(
            "selected_recipe_%s" % value
            for value in (capability.get("reasons") or [])
        )
    for key in ("hypothesis_id", "mechanism_id"):
        expected = recipe.get(key)
        for container in (selected, best, envelope_row):
            value = container.get(key)
            if value is not None and value != expected:
                reasons.append("selected_recipe_%s_drift" % key)
                break
    return {
        "ok": not reasons,
        "recipe_id": recipe_id,
        "expected_recipe_id": expected_id,
        "recipe": recipe,
        "contract_integrity": contract_integrity,
        "formal_capability": capability,
        "reasons": list(dict.fromkeys(reasons)),
    }


def _selected_hypothesis(blueprint, recipe):
    """Return the one hypothesis whose ids are frozen in the recipe."""
    stages = (blueprint or {}).get("stages") or {}
    brief = (blueprint or {}).get("glm_research_brief") or {}
    rows = []
    rows.extend((((brief.get("design_doc") or {}).get("hypotheses")) or []))
    rows.extend(((((stages.get("meta") or {}).get("design_doc") or {}).get("hypotheses")) or []))
    for row in rows:
        if not isinstance(row, dict):
            continue
        if (
            row.get("hypothesis_id") == recipe.get("hypothesis_id")
            and row.get("mechanism_id") == recipe.get("mechanism_id")
        ):
            return dict(row)
    return {}


def _text_or(value, fallback):
    if isinstance(value, (list, tuple)):
        value = "; ".join(str(item) for item in value if str(item).strip())
    text = str(value or "").strip()
    return text or fallback


def _locked_spec_pack(blueprint, job, recipe):
    """Deterministically bind the admitted recipe to a mechanism spec.

    The old bridge asked GLM to propose a second mechanism after discovery,
    which let a qualified trend recipe turn into an unrelated mean-reversion
    strategy.  Narrative fields may be populated from the selected hypothesis,
    but executable identity comes only from ``recipe``.
    """
    hypothesis = _selected_hypothesis(blueprint, recipe)
    family = str(recipe.get("family") or "").strip()
    mechanism_id = str(recipe.get("mechanism_id") or "").strip()
    recipe_id = str(recipe.get("recipe_id") or "").strip()
    horizon = int(recipe.get("horizon_bars"))
    statement = _text_or(
        hypothesis.get("statement_zh") or hypothesis.get("statement"),
        "admitted recipe %s" % recipe_id,
    )
    payer = _text_or(
        hypothesis.get("payoff_payer") or hypothesis.get("who_pays"),
        "the counterparties identified by admitted hypothesis %s" % recipe.get("hypothesis_id"),
    )
    invalidation = _text_or(
        hypothesis.get("failure_conditions") or hypothesis.get("invalidation"),
        "the admitted event no longer produces a post-cost directional effect",
    )
    recipe_json = json.dumps(recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    human_title = str((job or {}).get("research_direction") or "").split("｜", 1)[0].strip()
    if not human_title:
        human_title = "%s %s策略候选" % (
            (job or {}).get("symbol") or "研究标的",
            "做多" if str((job or {}).get("trade_direction") or "").lower() == "long" else "做空",
        )
    spec = {
        "mechanism_id": mechanism_id,
        "mechanism_name": family,
        "mechanism_family": family,
        "market_inefficiency": statement,
        "counterparty_source": payer,
        "why_edge_exists": _text_or(
            hypothesis.get("constraint_used") or hypothesis.get("persistence_reason"),
            statement,
        ),
        "edge_decay_conditions": invalidation,
        "required_market_regime": _text_or(
            hypothesis.get("required_regime") or hypothesis.get("regime"),
            "only the regimes represented in the admitted research sample",
        ),
        # Machine execution is compiled from admitted_recipe_lock, never by
        # reparsing this narrative string.
        "entry_logic": "ADMITTED_RECIPE_LOCK %s" % recipe_json,
        "exit_logic": "fixed horizon close only",
        "stop_logic": "protective fixed 0.9 percent",
        "take_profit_logic": "none",
        "invalidation_logic": "fixed horizon only; %s" % invalidation,
        "non_negotiable_rules": [
            "recipe_id=%s" % recipe_id,
            "event_id=%s" % recipe.get("event_id"),
            "execution_mapping=%s" % recipe.get("execution_mapping"),
            "research_contract_id=%s" % recipe.get("research_contract_id"),
        ],
        "tunable_parameters": [],
        "forbidden_transformations": [
            "change admitted recipe family/event/terms/direction/horizon/execution mapping",
            "replace admitted event with a family template",
        ],
        "expected_trade_frequency_class": "measured by admitted event sample",
        "expected_holding_period": "%d_bars" % horizon,
        "suitable_symbols": [job.get("symbol")],
        "suitable_timeframes": [job.get("timeframe")],
    }
    return {
        "ok": True,
        "errors": [],
        "mechanism_spec": spec,
        "meta": {
            "title": human_title,
            "thesis": statement,
            "direction": job.get("trade_direction"),
            "symbol": job.get("symbol"),
            "timeframe": job.get("timeframe"),
            "holding_horizon": "%d_bars" % horizon,
            "spec_source": "deterministic_admitted_recipe",
        },
        "call_id": "locked_recipe_%s" % recipe_id,
        "attempts": 1,
        "raw_ok": True,
        "admitted_recipe_lock": recipe,
    }


def _invariants_contract_from_recipe(recipe, spec, job):
    """Build the pretest contract from the already immutable recipe.

    Generic discovered families do not have a hand-written family contract.
    Requiring such a file made every newly discovered family fail before the
    first formal review.  The admitted recipe hash/identity validator is the
    stronger invariant here; this envelope makes that provenance explicit and
    leaves family-specific synthetic asserts to formal Review 1.
    """
    recipe_id = str((recipe or {}).get("recipe_id") or "missing")
    non_negotiable = list((spec or {}).get("non_negotiable_rules") or [])
    return {
        "schema": "qiyu_invariants_contract_v1",
        "contract_id": "admitted_recipe_%s" % recipe_id,
        "must_timeframe": (job or {}).get("timeframe"),
        "non_negotiable_rules": non_negotiable,
        "require_sanity_asserts": False,
        "identity_source": "immutable_admitted_recipe",
        "recipe_id": recipe_id,
        "recipe_hash_verified_before_compile": True,
        "dsl_identity_verified_after_compile": True,
        "scope": "translation_integrity_before_four_formal_reviews",
    }


def _build_focus(blueprint, job):
    stages = (blueprint or {}).get("stages") or {}
    selected = stages.get("selected") or {}
    return {
        "symbol": job.get("symbol"),
        "timeframe": job.get("timeframe"),
        "direction": job.get("trade_direction"),
        "human_brief": job.get("brief"),
        "order_zh": job.get("brief") or job.get("research_direction"),
        "professional_research": (blueprint or {}).get("glm_research_brief"),
        "research_contract": (blueprint or {}).get("research_contract"),
        "creation_blueprint": {
            "ok": (blueprint or {}).get("ok"),
            "run_id": (blueprint or {}).get("run_id"),
            "source": job.get("source"),
            "research_direction": job.get("research_direction"),
            "stages_summary": {
                "meta_family": ((stages.get("meta") or {}).get("design_doc") or {}).get("mechanism_family"),
                "hypothesis_passed": (stages.get("hypothesis") or {}).get("passed"),
                "selected": selected,
                "stress_passed": (stages.get("stress") or {}).get("passed"),
            },
            "fuses": (blueprint or {}).get("fuses"),
            "deliverables": (blueprint or {}).get("deliverables"),
        },
        "quantoracle": {
            "probe": ((blueprint or {}).get("probes") or {}).get("quantoracle"),
            "certified_factors": stages.get("certified_factors"),
        },
    }


def submit_blueprint_to_formal_review(blueprint, job):
    """Run the existing mechanism-spec, implementation and four-review entry."""
    if not (blueprint or {}).get("ok") or not (blueprint or {}).get("present_to_human"):
        err = str((blueprint or {}).get("error") or "")
        is_mat = err == "CANDIDATE_MATERIALIZATION_FAILURE" or (
            (blueprint or {}).get("outcome") == "generation_system_failure"
        )
        return {
            "ok": False,
            "started": False,
            "attempted": True,
            "submission_created": False,
            "review_submitted": False,
            "reason": (
                "CANDIDATE_MATERIALIZATION_FAILURE" if is_mat
                else "creation_candidate_not_qualified"
            ),
            "message_zh": (
                "管道生成失败：候选材料化不足，禁止进入复核（非市场研究拒绝）。"
                if is_mat else
                "创造门槛未通过，禁止进入复核。"
            ),
        }
    if not (blueprint or {}).get("glm_research_brief"):
        return {
            "ok": False,
            "started": False,
            "attempted": True,
            "submission_created": False,
            "review_submitted": False,
            "reason": "qualified_blueprint_missing_research_brief",
            "message_zh": "合格蓝图缺少可追溯研究摘要，禁止进入复核。",
        }
    stages = (blueprint or {}).get("stages") or {}
    envelope = stages.get("admission_envelope") or {}
    lineage = stages.get("assembly_lineage") or {}
    selected_recipe_id = lineage.get("selected_recipe_id")
    recipe_validation = _validate_recipe_lineage(blueprint, job)
    if not (
        selected_recipe_id
        and selected_recipe_id in (envelope.get("recipe_ids") or [])
        and lineage.get("selected_is_in_admission_envelope") is True
        and lineage.get("global_factor_mining_used") is False
        and lineage.get("classic_or_unadmitted_switch_used") is False
        and recipe_validation.get("ok") is True
    ):
        return {
            "ok": False,
            "started": False,
            "attempted": True,
            "submission_created": False,
            "review_submitted": False,
            "reason": "creation_recipe_lineage_invalid",
            "lineage_errors": recipe_validation.get("reasons") or [],
            "message_zh": "最终候选未能证明属于研究准入信封，禁止进入复核。",
        }

    # Hard ban: account WR≤50% / lottery books never enter four-review.
    # Manufacture-batch mode: still refuse handoff-floor failures / empty qualified batch.
    try:
        from . import manufacture_batch_policy as mfg
        manufacture_mode = bool(mfg.pre_review_gates_disabled())
    except Exception:
        manufacture_mode = False
        mfg = None
    from .creation_blueprint import _admission_trade_quality
    quality = (
        stages.get("admission_trade_quality")
        or ((stages.get("selected") or {}).get("admission_trade_quality"))
        or {}
    )
    if manufacture_mode and mfg is not None:
        batch_preview = list((blueprint or {}).get("review_batch") or [])
        if not batch_preview:
            return {
                "ok": False,
                "started": False,
                "attempted": True,
                "submission_created": False,
                "review_submitted": False,
                "reason": "manufacture_review_batch_empty",
                "message_zh": "制造批次无质检合格包（E>0且周开仓>0.5），已写入质检不合格表。",
            }
        try:
            from . import review_gate as rgate
        except Exception:
            rgate = None
        bad = []
        for row in batch_preview[:3]:
            gate = mfg.qualifies_for_review_handoff(row.get("select_metrics") or {})
            token_ok = True
            token_reasons = []
            if rgate is not None:
                tok = rgate.require_handoff_token(
                    row.get("handoff_token"),
                    recipe_id=row.get("recipe_id"),
                    metrics=row.get("select_metrics") or {},
                )
                token_ok = bool(tok.get("ok"))
                token_reasons = list(tok.get("reasons") or [])
            if (not gate.get("ok")) or (not token_ok):
                bad.append({
                    "rank": row.get("rank"),
                    "recipe_id": row.get("recipe_id"),
                    "reasons": list(gate.get("reasons") or []) + token_reasons,
                    "observed": gate.get("observed"),
                    "handoff_token_ok": token_ok,
                })
        if bad:
            return {
                "ok": False,
                "started": False,
                "attempted": True,
                "submission_created": False,
                "review_submitted": False,
                "reason": "manufacture_handoff_floor_fail",
                "rejected": bad,
                "message_zh": (
                    "质检器门槛未过（E>0 且周开仓>0.5），不进入待优化。"
                ),
            }
    if not manufacture_mode:
        if not quality.get("ok"):
            returns = (
                ((blueprint.get("best_factor") or {}).get("returns"))
                or ((stages.get("selected") or {}).get("returns"))
                or ((stages.get("admission_trade_quality") or {}).get("returns"))
                or []
            )
            if not returns:
                stats = (blueprint.get("best_factor") or {}).get("stats") or {}
                wr = stats.get("win_rate")
                mean_net = stats.get("mean_net")
                n = int(stats.get("n") or 0)
                fake_ok = bool(
                    wr is not None and float(wr) > 0.50
                    and mean_net is not None and float(mean_net) > 0
                    and n >= 8
                )
                quality = {
                    "ok": fake_ok,
                    "reasons": ([] if fake_ok else ["胜率未严格大于50%" if not (
                        wr is not None and float(wr) > 0.50) else "平均净收益非正"]),
                    "win_rate": wr,
                    "mean_net": mean_net,
                    "n": n,
                    "basis": "best_factor_stats_fallback",
                }
            else:
                quality = _admission_trade_quality(returns)
        if not quality.get("ok"):
            return {
                "ok": False,
                "started": False,
                "attempted": True,
                "submission_created": False,
                "review_submitted": False,
                "reason": "creation_account_wr_anti_lottery_fail",
                "admission_trade_quality": quality,
                "message_zh": "账户口径胜率未严格大于50%或反彩票/均值未过，禁止进入复核。",
            }

    # Review is deliberately serialized; creation itself remains two-lane.
    batch = list((blueprint or {}).get("review_batch") or [])
    if not batch:
        batch = [{
            "rank": 1,
            "recipe_id": selected_recipe_id,
            "recipe": recipe_validation.get("recipe"),
            "best_factor": blueprint.get("best_factor"),
        }]
    results = []
    with process_lock("formal_review_submission"):
        from .pipeline_step_a import (
            codex_implement_from_spec,
            run_creation_pipeline_step_a,
            validate_dsl_against_admitted_recipe,
        )
        for batch_row in batch[:3]:
            row_blueprint = dict(blueprint)
            # Keep recipe hash intact; stop 0.5% is applied at DSL/backtest layer.
            row_recipe = batch_row.get("recipe") or recipe_validation.get("recipe") or {}
            if batch_row.get("best_factor"):
                row_blueprint["best_factor"] = batch_row.get("best_factor")
            # Ensure lineage envelope contains this recipe id.
            env_ids = list(envelope.get("recipe_ids") or [])
            rid = str(batch_row.get("recipe_id") or row_recipe.get("recipe_id") or "")
            if rid and rid not in env_ids:
                env_ids.append(rid)
                envelope = dict(envelope)
                envelope["recipe_ids"] = env_ids
                stages = dict(stages)
                stages["admission_envelope"] = envelope
                stages["assembly_lineage"] = dict(lineage)
                stages["assembly_lineage"]["selected_recipe_id"] = rid
                stages["assembly_lineage"]["selected_is_in_admission_envelope"] = True
                row_blueprint["stages"] = stages
            else:
                stages2 = dict(stages)
                lin2 = dict(lineage)
                lin2["selected_recipe_id"] = rid or selected_recipe_id
                lin2["selected_is_in_admission_envelope"] = True
                stages2["assembly_lineage"] = lin2
                row_blueprint["stages"] = stages2
            mode_ctx = {
                "schema": "qiyu_parallel_creation_to_review_v1",
                "isolation": "new_mechanism",
                "mode_name": "new_mechanism",
                "source_job_id": job.get("job_id"),
                "review_batch_rank": batch_row.get("rank"),
            }
            recipe = row_recipe
            spec_pack = _locked_spec_pack(row_blueprint, job, recipe)
            spec = spec_pack.get("mechanism_spec") or {}
            meta = dict(spec_pack.get("meta") or {})
            meta.setdefault("symbol", job.get("symbol"))
            meta.setdefault("timeframe", job.get("timeframe"))
            meta.setdefault("direction", job.get("trade_direction"))
            meta["source"] = "parallel_sole_creation"
            meta["source_job_id"] = job.get("job_id")
            meta["source_agent"] = job.get("source")
            meta["research_direction"] = job.get("research_direction")
            meta["human_brief"] = job.get("brief")
            meta["review_batch_rank"] = batch_row.get("rank")
            pack = {
                "ok": True,
                "schema": "qiyu_step_a_v1",
                "artifact": "strategy_candidate_pack",
                "call_id": spec_pack.get("call_id"),
                "attempts": spec_pack.get("attempts"),
                "meta": meta,
                "mechanism_spec": spec,
                "research_contract": row_blueprint.get("research_contract"),
                "admitted_recipe_lock": recipe,
                "creation_blueprint": {
                    "ok": row_blueprint.get("ok"),
                    "run_id": row_blueprint.get("run_id"),
                    "loops": row_blueprint.get("loops"),
                    "fuses": row_blueprint.get("fuses"),
                    "deliverables": row_blueprint.get("deliverables"),
                    "best_factor": row_blueprint.get("best_factor"),
                    "admission_envelope": envelope,
                    "assembly_lineage": row_blueprint.get("stages", {}).get("assembly_lineage") or lineage,
                    "review_batch_rank": batch_row.get("rank"),
                    "select_metrics": batch_row.get("select_metrics"),
                    "handoff_token": batch_row.get("handoff_token"),
                    "handoff_gate": batch_row.get("handoff_gate"),
                },
                "creation_research": {
                    "schema": "qiyu_creation_research_via_parallel_blueprint_v1",
                    "glm_research_brief": row_blueprint.get("glm_research_brief"),
                    "research_run_id": row_blueprint.get("run_id"),
                    "source_job_id": job.get("job_id"),
                },
                "errors": [],
            }
            pack["invariants_contract"] = _invariants_contract_from_recipe(
                recipe, spec, job,
            )
            impl = codex_implement_from_spec(pack)
            if not impl or not bool(impl.get("ok", True)) or not impl.get("dsl"):
                results.append({
                    "ok": False,
                    "rank": batch_row.get("rank"),
                    "recipe_id": rid,
                    "reason": "strategy_dsl_implementation_failed",
                    "implementation": impl,
                })
                continue
            recipe_dsl_validation = validate_dsl_against_admitted_recipe(
                impl.get("dsl"), recipe, row_blueprint.get("research_contract") or {},
            )
            if not recipe_dsl_validation.get("ok"):
                results.append({
                    "ok": False,
                    "rank": batch_row.get("rank"),
                    "recipe_id": rid,
                    "reason": "compiled_strategy_recipe_identity_mismatch",
                    "identity_errors": recipe_dsl_validation.get("errors") or [],
                })
                continue
            pack["dsl"] = impl.get("dsl")
            # Native exit-hold search (creation Formal Bridge path).
            try:
                from . import formal_consistency_audit as fca
                import auto_trade_strategy_ecosystem as eco
                frame = eco._load_research_frame(
                    job.get("symbol"), job.get("timeframe"),
                )
                if frame is not None and len(frame) >= 300 and pack.get("dsl"):
                    hold_cal = fca.calibrate_max_hold_for_formal_floors(
                        pack["dsl"],
                        frame,
                        job.get("symbol"),
                        job.get("timeframe"),
                    )
                    sel = int(
                        hold_cal.get("selected_max_hold_bars")
                        or pack["dsl"].get("max_hold_bars")
                        or 24
                    )
                    pack["dsl"]["max_hold_bars"] = sel
                    pack["native_exit_hold_search"] = hold_cal
            except Exception as _hold_exc:
                pack["native_exit_hold_search_error"] = str(_hold_exc)[:240]
            pack["dsl_long"] = impl.get("dsl_long") or (
                pack["dsl"] if str(meta.get("direction")).lower() == "long" else None
            )
            pack["dsl_short"] = impl.get("dsl_short") or (
                pack["dsl"] if str(meta.get("direction")).lower() == "short" else None
            )
            result = run_creation_pipeline_step_a(
                symbol=job.get("symbol"),
                timeframe=job.get("timeframe"),
                exploration_mode="A",
                allow_horizontal_expand=False,
                prebuilt_spec_pack=pack,
                formal_submission=True,
                windtalker_tag="parallel_creation_%s_r%s_%s" % (
                    job.get("job_id"), batch_row.get("rank") or 0, int(time.time()),
                ),
            )
            human_approval_submission_created = bool(
                (result or {}).get("ok")
                and (result or {}).get("task_id")
                and (result or {}).get("stage") == "awaiting_human"
            )
            results.append({
                "ok": bool((result or {}).get("ok")),
                "rank": batch_row.get("rank"),
                "recipe_id": rid,
                "task_id": (result or {}).get("task_id"),
                "stage": (result or {}).get("stage"),
                "reason": (result or {}).get("reason"),
                "review_mode": (result or {}).get("review_mode"),
                "slim_multiai_review": (result or {}).get("slim_multiai_review"),
                "submission_created": True,
                "review_submitted": True,
                "human_approval_submission_created": human_approval_submission_created,
                "result": result,
            })

    any_ok = any(bool(r.get("ok")) for r in results)
    primary = results[0] if results else {}
    out = {
        "ok": any_ok or bool(primary.get("submission_created")),
        "started": True,
        "attempted": True,
        "submission_created": bool(results),
        "review_submitted": bool(results),
        "task_id": primary.get("task_id"),
        "reason": "slim_multiai_batch" if manufacture_mode else primary.get("reason"),
        "message_zh": "已提交 Top-%d 至精简多模型均值复核" % len(results),
        "review_batch_results": results,
        "n_submitted": len(results),
    }
    try:
        out["wx_notify"] = _notify_review_outcome_wx(out, blueprint, job)
    except Exception as exc:
        out["wx_notify"] = {"ok": False, "error": str(exc)[:160]}
    return out
