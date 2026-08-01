# -*- coding: utf-8 -*-
"""Bridge a qualified sole-creation blueprint into the existing four reviews.

This module does not change review standards and never deploys to live trading.
It only removes the old requirement to rerun discovery before review.
"""
from __future__ import print_function

import json
import time

from .process_safe_state import process_lock


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
    if logic not in ("all", "ordered_joins"):
        reasons.append("selected_recipe_event_logic_invalid")
    if kind == "human_contract_exact":
        if logic != "ordered_joins":
            reasons.append("selected_recipe_exact_event_logic_invalid")
    elif kind in ("mechanism_intersection", "mechanism_preserving"):
        if logic != "all" or len(terms) < 2:
            reasons.append("selected_recipe_generic_event_not_formally_admissible")
    else:
        reasons.append("selected_recipe_event_kind_not_formally_admissible")
    for index, term in enumerate(terms):
        if not isinstance(term, dict) or not term.get("factor"):
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
            "title": "%s_%s" % (family, recipe_id),
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
        return {
            "ok": False,
            "started": False,
            "attempted": True,
            "submission_created": False,
            "review_submitted": False,
            "reason": "creation_candidate_not_qualified",
            "message_zh": "创造门槛未通过，禁止进入正式复核。",
        }
    if not (blueprint or {}).get("glm_research_brief"):
        return {
            "ok": False,
            "started": False,
            "attempted": True,
            "submission_created": False,
            "review_submitted": False,
            "reason": "qualified_blueprint_missing_research_brief",
            "message_zh": "合格蓝图缺少可追溯研究摘要，禁止进入正式复核。",
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
            "message_zh": "最终候选未能证明属于研究准入信封，禁止进入正式复核。",
        }

    # Review is deliberately serialized; creation itself remains two-lane.
    with process_lock("formal_review_submission"):
        from .pipeline_step_a import (
            codex_implement_from_spec,
            run_creation_pipeline_step_a,
            validate_dsl_against_admitted_recipe,
        )
        focus = _build_focus(blueprint, job)
        mode_ctx = {
            "schema": "qiyu_parallel_creation_to_review_v1",
            "isolation": "new_mechanism",
            "mode_name": "new_mechanism",
            "source_job_id": job.get("job_id"),
        }
        # Discovery already selected and statistically admitted the mechanism.
        # Re-running a proposer here creates a second, unreviewed strategy.
        recipe = recipe_validation.get("recipe") or {}
        spec_pack = _locked_spec_pack(blueprint, job, recipe)

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
        pack = {
            "ok": True,
            "schema": "qiyu_step_a_v1",
            "artifact": "strategy_candidate_pack",
            "call_id": spec_pack.get("call_id"),
            "attempts": spec_pack.get("attempts"),
            "meta": meta,
            "mechanism_spec": spec,
            "research_contract": blueprint.get("research_contract"),
            "admitted_recipe_lock": recipe,
            "creation_blueprint": {
                "ok": blueprint.get("ok"),
                "run_id": blueprint.get("run_id"),
                "loops": blueprint.get("loops"),
                "fuses": blueprint.get("fuses"),
                "deliverables": blueprint.get("deliverables"),
                "best_factor": blueprint.get("best_factor"),
                "admission_envelope": envelope,
                "assembly_lineage": lineage,
            },
            "creation_research": {
                "schema": "qiyu_creation_research_via_parallel_blueprint_v1",
                "glm_research_brief": blueprint.get("glm_research_brief"),
                "research_run_id": blueprint.get("run_id"),
                "source_job_id": job.get("job_id"),
            },
            "errors": [],
        }
        impl = codex_implement_from_spec(pack)
        if not impl or not bool(impl.get("ok", True)) or not impl.get("dsl"):
            return {
                "ok": False,
                "started": False,
                "attempted": True,
                "submission_created": False,
                "review_submitted": False,
                "reason": "strategy_dsl_implementation_failed",
                "implementation": impl,
            }
        recipe_dsl_validation = validate_dsl_against_admitted_recipe(
            impl.get("dsl"), recipe, blueprint.get("research_contract") or {},
        )
        if not recipe_dsl_validation.get("ok"):
            return {
                "ok": False,
                "started": False,
                "attempted": True,
                "submission_created": False,
                "review_submitted": False,
                "reason": "compiled_strategy_recipe_identity_mismatch",
                "recipe_id": recipe.get("recipe_id"),
                "identity_errors": recipe_dsl_validation.get("errors") or [],
                "identity_evidence": recipe_dsl_validation.get("evidence") or {},
            }
        pack["dsl"] = impl.get("dsl")
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
            windtalker_tag="parallel_creation_%s_%s" % (
                job.get("job_id"), int(time.time()),
            ),
        )
        submission_created = bool(
            (result or {}).get("ok")
            and (result or {}).get("task_id")
            and (result or {}).get("stage") == "awaiting_human"
        )
        return {
            "ok": bool((result or {}).get("ok")),
            "started": submission_created,
            "attempted": True,
            "submission_created": submission_created,
            "review_submitted": submission_created,
            "reason": (result or {}).get("reason"),
            "task_id": (result or {}).get("task_id"),
            "human_confirm_state": (result or {}).get("human_confirm_state"),
            "admission": (result or {}).get("admission_v2"),
            "live_execution_changed": False,
        }
