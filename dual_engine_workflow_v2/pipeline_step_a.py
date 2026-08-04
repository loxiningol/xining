# -*- coding: utf-8 -*-
"""STEP A creation pipeline — Gates 0–7, mechanism_spec, 20 tests, failure KB."""
from __future__ import print_function

# Allow `python3 dual_engine_workflow_v2/pipeline_step_a.py --strategy_json ...`
# without requiring `python3 -m` (sets package context before relative imports).
if __name__ == "__main__" and (__package__ is None or __package__ == ""):
    import sys as _sys
    from pathlib import Path as _Path
    _repo_root = _Path(__file__).resolve().parents[1]
    if str(_repo_root) not in _sys.path:
        _sys.path.insert(0, str(_repo_root))
    __package__ = "dual_engine_workflow_v2"

import copy
import hashlib
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
    MECHANISM_SPEC_FIELDS,
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
    record_pipeline_rejection,
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
from .fidelity import rule_based_condition_audit
from .mechanism import build_fingerprint_from_statement
from . import recipe_policy
from . import research_contract as research_contract_mod


_JOB = {"running": False, "kind": None, "started_at": None, "error": None}
_JOB_LOCK = threading.Lock()


def _admission_profile():
    """Canonical production profile; no strategy-specific bypasses."""
    import os
    raw = str(os.environ.get("STEP_A_ADMISSION_PROFILE") or "strict_four_review_v2").strip()
    if raw in ("legacy_funnel", "legacy", "old"):
        if str(os.environ.get("STEP_A_ALLOW_LEGACY_FUNNEL") or "").strip() in ("1", "true", "on", "yes"):
            return "legacy_funnel"
        print(
            "[pipeline_step_a] REFUSING legacy_funnel without STEP_A_ALLOW_LEGACY_FUNNEL=1; "
            "forcing strict_four_review_v2",
            flush=True,
        )
        return "strict_four_review_v2"
    if raw in ("ada_t3_calibrated_v1", "ada_t3", "reconstructed"):
        return "strict_four_review_v2"
    return raw or "strict_four_review_v2"


def _admission_v2_enabled():
    return _admission_profile() in (
        "strict_four_review_v2", "1", "true", "on",
    )


def _legacy_soft_pass_enabled():
    """Emergency migration switch only; disabled by default."""
    import os
    return str(os.environ.get("STEP_A_ALLOW_STRATEGY_SPECIFIC_SOFT_PASS") or "").strip().lower() in (
        "1", "true", "on", "yes",
    )


def _soft_skip_legacy(task, stage, detail=None):
    """Record an explicitly authorized migration-only soft skip."""
    if not _legacy_soft_pass_enabled():
        raise RuntimeError("strategy_specific_soft_pass_disabled")
    task.setdefault("admission_v2", {})
    task["admission_v2"]["profile"] = _admission_profile()
    skips = task["admission_v2"].setdefault("legacy_soft_skips", [])
    skips.append({
        "stage": stage,
        "at": _now(),
        "detail": detail or {},
        "advisory_only": True,
        "blocking": False,
    })
    print(
        "[pipeline_step_a] admission_v2 soft-skip legacy stage=%s (advisory only)"
        % stage,
        flush=True,
    )
    return True


def _evidence_metrics_from_bt(base_m, trades):
    from .review_admission_v2 import metrics_from_backtest_result
    # Prefer explicit base metrics; fall back to trade list.
    m = dict(base_m or {})
    if m.get("trades") is None and trades is not None:
        m["trades"] = len(trades or [])
    if m.get("win_rate") is None and m.get("win_rate_pct") is not None:
        m["win_rate"] = m.get("win_rate_pct")
    if m.get("win_rate") is None and m.get("win_rate_percent") is not None:
        m["win_rate"] = m.get("win_rate_percent")
    if m.get("mean_net") is None and trades:
        synth, _ = metrics_from_backtest_result({"trades": trades})
        for k, v in synth.items():
            m.setdefault(k, v)
    return m


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
                    "direction": parsed.get("direction") or (focus or {}).get("direction"),
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


def _compiler_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [x for x in value if x not in (None, "")]
    return [value]


def _compiler_contract(spec_pack, spec, meta):
    """Locate ResearchContract v2 without breaking legacy prebuilt packs."""
    for obj in (spec_pack, spec, meta):
        if not isinstance(obj, dict):
            continue
        for key in ("research_contract", "creation_contract", "contract"):
            value = obj.get(key)
            if isinstance(value, dict):
                # Some producers wrap the immutable contract once more.
                nested = value.get("research_contract") or value.get("creation_contract")
                return nested if isinstance(nested, dict) else value
    return {}


def _apply_contract_frequency_gate(theoretical_review, verification, contract):
    """Apply a stricter immutable per-task weekly-open gate before review."""
    theo = dict(theoretical_review or {})
    verified = dict(verification or {})
    performance = dict((contract or {}).get("performance_contract") or {})
    if performance.get("metric") != "theoretical_weekly_opens":
        return theo, verified
    try:
        threshold = float(performance.get("threshold"))
    except (TypeError, ValueError):
        threshold = None
    operator = str(performance.get("operator") or ">=").strip()
    try:
        observed = float(theo.get("ai_theoretical_weekly_opens_avg"))
    except (TypeError, ValueError):
        observed = None
    passed = False
    if threshold is not None and observed is not None:
        passed = observed > threshold if operator == ">" else observed >= threshold
    gate = {
        "metric": "theoretical_weekly_opens",
        "operator": operator,
        "threshold": threshold,
        "observed": observed,
        "pass": bool(passed),
        "scope": "pre_review_submission",
        "research_contract_id": (contract or {}).get("contract_id"),
    }
    theo["research_contract_frequency_gate"] = gate
    theo["approved"] = bool(theo.get("approved") and passed)
    reasons = list(verified.get("reasons") or [])
    if not passed:
        reason = "research_contract_weekly_opens_fail(got=%s,required=%s%s)" % (
            "missing" if observed is None else "%.6f" % observed,
            operator,
            "missing" if threshold is None else "%.6f" % threshold,
        )
        if reason not in reasons:
            reasons.append(reason)
    verified.update({
        "ok": bool(verified.get("ok") and passed),
        "reasons": reasons,
        "research_contract_frequency_gate": gate,
    })
    return theo, verified


def _formal_submission_authority(requested, contract, admitted_recipe):
    """Only the discovery→formal bridge may create a review submission."""
    if requested is not True:
        return {
            "authorized": False,
            "diagnostic_only": True,
            "reasons": ["formal_submission_not_requested"],
        }
    reasons = []
    integrity = research_contract_mod.verify_contract_integrity(contract)
    if not integrity.get("ok"):
        reasons.extend(integrity.get("reasons") or [])
    if not isinstance(admitted_recipe, dict) or not admitted_recipe:
        reasons.append("admitted_recipe_lock_missing")
    else:
        try:
            from .research_discovery import verify_assembly_recipe
            valid_recipe, expected_recipe_id = verify_assembly_recipe(admitted_recipe)
        except Exception as exc:
            valid_recipe, expected_recipe_id = False, None
            reasons.append("admitted_recipe_integrity_check_failed:%s" % exc)
        if not valid_recipe:
            reasons.append("admitted_recipe_hash_invalid")
        if admitted_recipe.get("research_contract_id") != contract.get("contract_id"):
            reasons.append("admitted_recipe_contract_drift")
        capability = recipe_policy.capability_from_row(admitted_recipe, contract)
        if not capability.get("ok"):
            reasons.extend(capability.get("reasons") or [])
    return {
        "authorized": not reasons,
        "diagnostic_only": False,
        "reasons": list(dict.fromkeys(reasons)),
    }


def _compiler_normalize_tf(value):
    raw = str(value or "").strip().lower().replace(" ", "")
    aliases = {
        "5min": "5m", "5minute": "5m", "5minutes": "5m",
        "15min": "15m", "15minute": "15m", "15minutes": "15m",
        "60m": "1h", "60min": "1h", "1hour": "1h", "1hr": "1h",
        "240m": "4h", "240min": "4h", "4hour": "4h", "4hr": "4h",
    }
    return aliases.get(raw, raw)


def _compiler_feature(value, condition_tf=None, primary_tf=None):
    """Map only true naming aliases; never turn one economic variable into another."""
    import re
    raw = str(value or "").strip().lower()
    prefix_match = re.match(r"^(5m|15m|1h|4h)[:/]", raw)
    double_prefix_match = re.match(r"^(5m|15m|1h|4h)__", raw)
    suffix_match = re.search(r"@(5m|15m|1h|4h)$", raw)
    declared_tf = (prefix_match.group(1) if prefix_match else
                   (double_prefix_match.group(1) if double_prefix_match else
                   (suffix_match.group(1) if suffix_match else None))
                  )
    condition_tf = condition_tf or declared_tf
    raw = re.sub(r"^(5m|15m|1h|4h)[:/]", "", raw)
    raw = re.sub(r"^(5m|15m|1h|4h)__", "", raw)
    raw = re.sub(r"@(5m|15m|1h|4h)$", "", raw)
    aliases = {
        "volume_z": "vol_z20", "vol_z": "vol_z20",
        "prev_high": "prev_high20", "prev_low": "prev_low20",
        "atr": "atr14", "rsi": "rsi14",
        "kdj_k": "k", "kdj_d": "d", "kdj_j": "j",
    }
    raw = aliases.get(raw, raw)
    ctf = _compiler_normalize_tf(condition_tf)
    ptf = _compiler_normalize_tf(primary_tf)
    if ctf and ptf and ctf != ptf:
        prefix = {"1h": "h1_", "4h": "h4_"}.get(ctf)
        if prefix and not raw.startswith(prefix):
            candidate = prefix + raw
            # Only a genuinely implemented higher-timeframe feature is accepted.
            try:
                import auto_trade_strategy_dsl as _dsl_mod
                if candidate in _dsl_mod.FEATURES:
                    raw = candidate
                else:
                    raw = "%s:%s" % (ctf, raw)
            except Exception:
                raw = "%s:%s" % (ctf, raw)
        elif not prefix:
            raw = "%s:%s" % (ctf, raw)
    return raw


def _compiler_extract_features(tree):
    used = set()

    def walk(node):
        if isinstance(node, dict):
            if node.get("exit_op") in ("atr_trailing", "partial_tp_atr"):
                used.update(("atr14", "high", "low", "close"))
            elif node.get("exit_op") == "swing_extreme":
                used.update(("high", "low", "close"))
            elif node.get("exit_op") == "entry_wick_buffer":
                used.update(("high", "low", "close"))
            elif node.get("exit_op") == "fixed_pct_tp":
                used.update(("high", "low", "close"))
            if node.get("exit_op") == "partial_tp_feature" and node.get("feature"):
                used.add(str(node.get("feature")))
                used.update(("high", "low", "close"))
            for side in ("left", "right"):
                operand = node.get(side)
                if isinstance(operand, dict) and operand.get("feature"):
                    used.add(str(operand.get("feature")))
                if isinstance(operand, dict) and operand.get("quantile_of"):
                    used.add(str(operand.get("quantile_of")))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(tree)
    return used


def _compiler_feature_rules(contract, spec, meta, primary_tf):
    feature_contract = contract.get("feature_contract") or {}
    sources = [feature_contract, contract, spec.get("feature_contract") or {}, spec, meta]

    def collect(keys):
        out = []
        for src in sources:
            if not isinstance(src, dict):
                continue
            for key in keys:
                out.extend(_compiler_list(src.get(key)))
        normalized = []
        for value in out:
            feat = _compiler_feature(value, primary_tf=primary_tf)
            if feat and feat not in normalized:
                normalized.append(feat)
        return normalized

    required = collect(("required_features", "required_core_features", "required_entry_features"))
    allowed = collect(("allowed_features", "allowed_core_features"))
    forbidden = []
    for src in sources:
        if not isinstance(src, dict):
            continue
        forbidden.extend(_compiler_list(src.get("forbidden_substitutions")))
        forbidden.extend(_compiler_list(src.get("forbidden_transformations")))
        forbidden.extend(_compiler_list(src.get("forbidden_features")))
    allowed_enforced = bool(feature_contract.get("allowed_features_enforced"))
    return (
        required,
        allowed,
        [str(x) for x in forbidden if str(x).strip()],
        allowed_enforced,
    )


def _compiler_condition_leaf(raw, index, primary_tf, errors, phase="entry"):
    import re
    try:
        import auto_trade_strategy_dsl as dsl_mod
    except Exception as exc:
        errors.append("dsl_runtime_unavailable:%s" % exc)
        return None, "and"
    if not isinstance(raw, dict):
        errors.append("required_condition_not_object:%s" % index)
        return None, "and"
    condition_tf = raw.get("timeframe") or primary_tf
    left_raw = raw.get("indicator") or raw.get("feature")
    if left_raw is None and isinstance(raw.get("left"), dict):
        left_raw = raw["left"].get("feature")
    elif left_raw is None:
        left_raw = raw.get("left")
    left = _compiler_feature(left_raw, condition_tf, primary_tf)
    if left not in dsl_mod.FEATURES:
        errors.append("required_condition_feature_unsupported:%s:%s" % (index, left or left_raw))
        return None, str(raw.get("join") or "and").lower()
    ctf = _compiler_normalize_tf(condition_tf)
    ptf = _compiler_normalize_tf(primary_tf)
    if ctf and ptf and ctf != ptf:
        prefix = {"1h": "h1_", "4h": "h4_"}.get(ctf)
        if not prefix or not left.startswith(prefix):
            errors.append("condition_timeframe_semantics_unsupported:%s:%s_on_%s" % (index, left, ctf))
            return None, str(raw.get("join") or "and").lower()
    op_alias = {
        ">": "gt", ">=": "gte", "<": "lt", "<=": "lte", "==": "eq", "=": "eq",
        "crosses_above": "cross_above", "crosses_below": "cross_below",
    }
    op_raw = str(raw.get("operator") or raw.get("op") or "").strip().lower()
    op = op_alias.get(op_raw, op_raw)
    if op not in dsl_mod.OPS:
        errors.append("required_condition_operator_unsupported:%s:%s" % (index, op_raw))
        return None, str(raw.get("join") or "and").lower()
    phase_prefix = "x" if phase == "exit" else "e"
    leaf = {
        "id": re.sub(r"[^a-zA-Z0-9_]", "_", str(raw.get("id") or "%s_contract_%d" % (phase_prefix, index)))[:80],
        "left": {"feature": left},
        "op": op,
    }
    if isinstance(raw.get("left"), dict) and raw["left"].get("offset") is not None:
        leaf["left"]["offset"] = raw["left"].get("offset")
    if op == "between":
        bounds = raw.get("value")
        lower = raw.get("lower")
        upper = raw.get("upper")
        if isinstance(bounds, (list, tuple)) and len(bounds) == 2:
            lower, upper = bounds
        try:
            leaf["lower"] = float(lower)
            leaf["upper"] = float(upper)
        except Exception:
            errors.append("required_condition_between_invalid:%s" % index)
            return None, str(raw.get("join") or "and").lower()
    else:
        value = raw.get("value") if "value" in raw else raw.get("right")
        if isinstance(value, dict):
            if value.get("feature") is not None:
                feat = _compiler_feature(value.get("feature"), condition_tf, primary_tf)
                if feat not in dsl_mod.FEATURES:
                    errors.append("required_condition_right_feature_unsupported:%s:%s" % (index, feat))
                    return None, str(raw.get("join") or "and").lower()
                leaf["right"] = {"feature": feat}
                if value.get("offset") is not None:
                    leaf["right"]["offset"] = value.get("offset")
                if value.get("scale") is not None:
                    leaf["right"]["scale"] = value.get("scale")
            elif value.get("quantile_of") is not None:
                feat = _compiler_feature(value.get("quantile_of"), condition_tf, primary_tf)
                if feat not in dsl_mod.FEATURES:
                    errors.append("required_condition_quantile_feature_unsupported:%s:%s" % (index, feat))
                    return None, str(raw.get("join") or "and").lower()
                if any(value.get(key) is None for key in ("q", "window", "min_history")):
                    errors.append("required_condition_quantile_identity_incomplete:%s" % index)
                    return None, str(raw.get("join") or "and").lower()
                leaf["right"] = {
                    "quantile_of": feat,
                    "q": value.get("q"),
                    "window": value.get("window"),
                    "min_history": value.get("min_history"),
                }
            elif "value" in value:
                leaf["right"] = {"value": value.get("value")}
            else:
                errors.append("required_condition_right_invalid:%s" % index)
                return None, str(raw.get("join") or "and").lower()
        elif isinstance(value, str):
            feature_value = _compiler_feature(value, condition_tf, primary_tf)
            if feature_value in dsl_mod.FEATURES:
                leaf["right"] = {"feature": feature_value}
            else:
                try:
                    leaf["right"] = {"value": float(value)}
                except Exception:
                    errors.append("required_condition_value_unrepresentable:%s:%s" % (index, value))
                    return None, str(raw.get("join") or "and").lower()
        elif isinstance(value, (int, float)):
            leaf["right"] = {"value": value}
        else:
            errors.append("required_condition_value_missing:%s" % index)
            return None, str(raw.get("join") or "and").lower()
    return leaf, str(raw.get("join") or "and").strip().lower()


def _compiler_join_conditions(compiled):
    if not compiled:
        return None
    expr = compiled[0][0]
    for leaf, join in compiled[1:]:
        logical = "any" if join in ("or", "any", "|") else "all"
        if isinstance(expr, dict) and set(expr) == {logical}:
            expr[logical].append(leaf)
        else:
            expr = {logical: [expr, leaf]}
    return expr


# Single source of truth: recipe_policy.GENERIC_FACTOR_TO_DSL (+ semantics).
# Research estimators must stay point-aligned with the formal frame builder.
_RECIPE_FACTOR_TO_DSL = {
    factor: {
        "feature": dsl_feature,
        "semantics": recipe_policy.GENERIC_FACTOR_SEMANTICS.get(
            factor, "%s_v1" % dsl_feature
        ),
    }
    for factor, dsl_feature in recipe_policy.GENERIC_FACTOR_TO_DSL.items()
}
_GENERIC_RECIPE_KINDS = {
    "mechanism_intersection", "mechanism_preserving", "ast_compiled",
}


def _compiler_recipe_entry(recipe, contract, timeframe, errors):
    """Compile the exact event admitted by discovery; never infer a template.

    ``errors`` is deliberately caller-owned so this function can be used both
    while constructing a DSL and at the final formal-review boundary.  Missing
    estimator identity, unsupported research factors, and a one-leaf generic
    probe all fail closed instead of being supplemented with invented logic.
    """
    recipe = recipe if isinstance(recipe, dict) else {}
    contract = contract if isinstance(contract, dict) else {}
    evidence = {
        "recipe_id": recipe.get("recipe_id"),
        "event_kind": recipe.get("event_kind"),
        "event_logic": recipe.get("event_logic"),
        "factor_mappings": [],
    }
    contract_integrity = research_contract_mod.verify_contract_integrity(contract)
    evidence["contract_integrity"] = contract_integrity
    if not contract_integrity.get("ok"):
        errors.extend(contract_integrity.get("reasons") or [])
    try:
        from .research_discovery import verify_assembly_recipe
        integrity_ok, expected_id = verify_assembly_recipe(recipe)
    except Exception as exc:
        integrity_ok, expected_id = False, None
        errors.append("admitted_recipe_integrity_check_failed:%s" % exc)
    evidence["expected_recipe_id"] = expected_id
    evidence["recipe_hash_valid"] = bool(integrity_ok)
    if not integrity_ok:
        errors.append("admitted_recipe_hash_invalid")
    allowed_schemas = getattr(
        recipe_policy, "ALLOWED_RECIPE_SCHEMAS",
        ("qiyu_admitted_probe_recipe_v1", "qiyu_admitted_probe_recipe_v2"),
    )
    if recipe.get("schema") not in allowed_schemas:
        errors.append("admitted_recipe_schema_invalid:%s" % recipe.get("schema"))

    required_identity = (
        "recipe_id", "research_contract_id", "hypothesis_id", "mechanism_id",
        "family", "event_id", "event_kind", "event_logic", "terms",
        "trade_direction", "horizon_bars", "execution_mapping",
        "primary_cost_scenario", "primary_cost_per_trade",
        "research_contract_body_hash", "exit_policy",
        "protective_stop_policy", "execution_leverage",
        "statistical_return_basis",
    )
    for key in required_identity:
        if recipe.get(key) in (None, "", []):
            errors.append("admitted_recipe_%s_missing" % key)
    if recipe.get("statistical_returns_are_post_cost") is not True:
        errors.append("admitted_recipe_post_cost_identity_missing")
    if recipe.get("protective_stop_evaluated") is not True:
        errors.append("admitted_recipe_protective_stop_not_evaluated")
    try:
        cost = float(recipe.get("primary_cost_per_trade"))
        if cost < 0.0:
            raise ValueError("negative")
    except (TypeError, ValueError):
        errors.append("admitted_recipe_primary_cost_invalid")

    contract_id = contract.get("contract_id")
    if not contract_id or recipe.get("research_contract_id") != contract_id:
        errors.append("admitted_recipe_contract_drift")
    if recipe.get("research_contract_body_hash") != contract_integrity.get(
        "expected_contract_id"
    ):
        errors.append("admitted_recipe_contract_body_hash_drift")
    if contract.get("valid") is False:
        errors.append("admitted_recipe_contract_invalid")
    target = contract.get("target") or {}
    direction = str(recipe.get("trade_direction") or "").lower()
    if direction not in ("long", "short"):
        errors.append("admitted_recipe_direction_invalid:%s" % (direction or "missing"))
    if target.get("direction") and str(target.get("direction")).lower() != direction:
        errors.append("admitted_recipe_direction_contract_drift")
    target_tf = _compiler_normalize_tf(target.get("timeframe"))
    primary_tf = _compiler_normalize_tf(timeframe)
    if target_tf and primary_tf and target_tf != primary_tf:
        errors.append("admitted_recipe_timeframe_contract_drift:%s!=%s" % (
            target_tf, primary_tf,
        ))
    try:
        horizon = int(recipe.get("horizon_bars"))
        if horizon < 1 or horizon > 240:
            raise ValueError("outside_bounds")
    except (TypeError, ValueError):
        errors.append("admitted_recipe_horizon_invalid")
        horizon = None
    evidence["horizon_bars"] = horizon
    capability = recipe_policy.capability_from_row(recipe, contract)
    evidence["formal_capability"] = capability
    if not capability.get("ok"):
        errors.extend(
            "admitted_recipe_%s" % reason
            for reason in (capability.get("reasons") or [])
        )

    # Discovery's closed-bar event is currently reproducible only by filling
    # the next bar open.  Delayed/pullback mappings remain diagnostic evidence
    # and are intentionally barred from formal assembly.
    mapping = str(recipe.get("execution_mapping") or "")
    if mapping != "next_bar_open":
        errors.append("admitted_recipe_execution_mapping_not_formally_supported:%s" % (
            mapping or "missing",
        ))
    timing = ((contract.get("event_contract") or {}).get("entry_timing") or {})
    if isinstance(timing, str):
        timing = {"mode": timing}
    timing_mode = str((timing or {}).get("mode") or "unspecified")
    if timing_mode not in ("unspecified", mapping):
        errors.append("admitted_recipe_execution_mapping_contract_drift:%s!=%s" % (
            timing_mode, mapping,
        ))
    recipe_timing = recipe.get("required_entry_timing") or {}
    if isinstance(recipe_timing, str):
        recipe_timing = {"mode": recipe_timing}
    recipe_timing_mode = str((recipe_timing or {}).get("mode") or "unspecified")
    if recipe_timing_mode not in ("unspecified", mapping):
        errors.append("admitted_recipe_required_timing_drift:%s!=%s" % (
            recipe_timing_mode, mapping,
        ))

    event = contract.get("event_contract") or {}
    if event.get("session_window") or event.get("time_window") or contract.get("session_window"):
        # Probe masks do not yet encode session windows.  Adding one here would
        # produce a different event from the event whose returns were admitted.
        errors.append("admitted_recipe_session_window_not_researched")

    terms = recipe.get("terms") or []
    if not isinstance(terms, list):
        errors.append("admitted_recipe_terms_not_list")
        terms = []
    kind = str(recipe.get("event_kind") or "")
    logic = str(recipe.get("event_logic") or "")
    exact = kind == "human_contract_exact"
    evidence["exact_human_contract"] = exact

    compiled = []
    if exact:
        if logic != "ordered_joins":
            errors.append("admitted_recipe_exact_logic_invalid:%s" % (logic or "missing"))
        if not terms:
            errors.append("admitted_recipe_exact_terms_missing")
        for index, term in enumerate(terms, 1):
            if not isinstance(term, dict):
                errors.append("admitted_recipe_exact_term_%d_invalid" % index)
                continue
            literal = dict(term)
            literal["indicator"] = term.get("factor") or term.get("feature")
            literal["operator"] = term.get("op") or term.get("operator")
            literal["join"] = term.get("join") or ("root" if index == 1 else "and")
            if literal.get("indicator") in (None, "") or "value" not in term:
                errors.append("admitted_recipe_exact_term_%d_identity_incomplete" % index)
                continue
            leaf, join = _compiler_condition_leaf(
                literal, index, primary_tf, errors, phase="entry",
            )
            if leaf:
                compiled.append((leaf, join))
        recipe_entry = _compiler_join_conditions(compiled)

        contract_rows = _compiler_required_conditions(contract, direction)
        if not contract_rows:
            errors.append("admitted_recipe_exact_contract_conditions_missing")
        contract_compiled = []
        for index, row in enumerate(contract_rows, 1):
            leaf, join = _compiler_condition_leaf(
                row, index, primary_tf, errors, phase="entry",
            )
            if leaf:
                contract_compiled.append((leaf, join))
        contract_entry = _compiler_join_conditions(contract_compiled)
        if (
            recipe_entry is not None and contract_entry is not None
            and _compiler_logic_semantics(recipe_entry)
            != _compiler_logic_semantics(contract_entry)
        ):
            errors.append("admitted_recipe_exact_contract_semantics_drift")
            evidence["recipe_entry_semantics"] = _compiler_logic_semantics(recipe_entry)
            evidence["contract_entry_semantics"] = _compiler_logic_semantics(contract_entry)
    elif kind == "ast_compiled":
        contract_rows = _compiler_required_conditions(contract, direction)
        if contract_rows:
            errors.append("generic_recipe_cannot_replace_exact_contract_event")
        event_ast = recipe.get("event_ast")
        if not isinstance(event_ast, dict) or not event_ast:
            errors.append("admitted_recipe_event_ast_missing")
            recipe_entry = None
        else:
            try:
                from . import ast_compiler as ac
                dsl_pack = ac.compile_ast_to_dsl(event_ast)
            except Exception as exc:
                dsl_pack = {"ok": False, "formal_ok": False, "reasons": [type(exc).__name__]}
            evidence["event_ast_hash"] = (
                recipe.get("event_ast_hash") or dsl_pack.get("event_ast_hash")
            )
            evidence["ast_dsl_reasons"] = list(dsl_pack.get("reasons") or [])
            evidence["ast_formal_ok"] = bool(dsl_pack.get("formal_ok"))
            if not dsl_pack.get("ok") or dsl_pack.get("entry_tree") is None:
                errors.append("admitted_recipe_ast_dsl_compile_failed")
                recipe_entry = None
            elif not dsl_pack.get("formal_ok"):
                errors.append("admitted_recipe_ast_not_formally_reproducible")
                recipe_entry = dsl_pack.get("entry_tree")
            else:
                recipe_entry = dsl_pack.get("entry_tree")
                evidence["factor_mappings"].append({
                    "research_factor": "event_ast",
                    "dsl_feature": "ast_compiled",
                    "semantics": "event_ast_v2",
                    "event_ast_hash": evidence.get("event_ast_hash"),
                })
            # Prefer attached formal DSL snapshot when present and matching hash.
            attached = recipe.get("event_ast_dsl")
            if (
                recipe_entry is not None
                and isinstance(attached, dict)
                and recipe.get("event_ast_formal_ok") is True
            ):
                recipe_entry = attached
    else:
        contract_rows = _compiler_required_conditions(contract, direction)
        if contract_rows:
            errors.append("generic_recipe_cannot_replace_exact_contract_event")
        if kind == "single_proxy":
            errors.append("generic_single_leaf_not_formally_admissible")
        elif kind not in _GENERIC_RECIPE_KINDS:
            errors.append("admitted_recipe_event_kind_unsupported:%s" % (kind or "missing"))
        if logic != "all":
            errors.append("admitted_recipe_generic_logic_must_be_all:%s" % (logic or "missing"))
        if len(terms) < 2:
            errors.append("generic_recipe_requires_at_least_two_terms")
        for index, term in enumerate(terms, 1):
            if not isinstance(term, dict):
                errors.append("admitted_recipe_term_%d_invalid" % index)
                continue
            factor = str(term.get("factor") or "")
            registration = _RECIPE_FACTOR_TO_DSL.get(factor)
            if not registration:
                errors.append("admitted_recipe_factor_unsupported:%s" % (factor or "missing"))
                continue
            side = str(term.get("side") or "")
            if side not in ("high", "low"):
                errors.append("admitted_recipe_term_%d_side_invalid:%s" % (index, side or "missing"))
                continue
            if str(term.get("threshold_source") or "") != "prior_only_rolling_quantile":
                errors.append("admitted_recipe_term_%d_threshold_source_invalid" % index)
                continue
            if str(term.get("join") or "and").lower() not in ("and", "all"):
                errors.append("admitted_recipe_term_%d_join_conflicts_with_all" % index)
            try:
                q = float(term.get("q"))
                window_raw = term.get("window")
                history_raw = term.get("min_history")
                window = int(window_raw)
                min_history = int(history_raw)
                if float(window_raw) != float(window) or float(history_raw) != float(min_history):
                    raise ValueError("non_integer")
                if not (0.5 < q < 1.0):
                    raise ValueError("q_outside_bounds")
                if not (2 <= window <= 240 and 2 <= min_history <= window):
                    raise ValueError("history_outside_bounds")
            except (TypeError, ValueError):
                errors.append("admitted_recipe_term_%d_quantile_identity_invalid" % index)
                continue
            threshold_q = q if side == "high" else 1.0 - q
            leaf = {
                "id": "e_recipe_%d" % index,
                "left": {"feature": registration["feature"]},
                "op": "gte" if side == "high" else "lte",
                "right": {
                    "quantile_of": registration["feature"],
                    "q": threshold_q,
                    "window": window,
                    "min_history": min_history,
                },
            }
            compiled.append((leaf, "and"))
            evidence["factor_mappings"].append({
                "research_factor": factor,
                "dsl_feature": registration["feature"],
                "semantics": registration["semantics"],
                "side": side,
                "research_q": q,
                "threshold_q": threshold_q,
                "window": window,
                "min_history": min_history,
            })
        recipe_entry = {"all": [row[0] for row in compiled]} if compiled else None

    if terms and kind != "ast_compiled":
        first = terms[0] if isinstance(terms[0], dict) else {}
        if recipe.get("factor") not in (None, first.get("factor"), first.get("feature")):
            errors.append("admitted_recipe_primary_factor_drift")
        if not exact and recipe.get("side") not in (None, first.get("side")):
            errors.append("admitted_recipe_primary_side_drift")
        if not exact and recipe.get("quantile") not in (None, first.get("q")):
            errors.append("admitted_recipe_primary_quantile_drift")
    evidence["compiled_entry_semantics"] = (
        _compiler_logic_semantics(recipe_entry) if recipe_entry else None
    )
    return recipe_entry, evidence


def _compiler_logic_semantics(node):
    """Canonical executable meaning, excluding IDs and presentation order."""
    if not isinstance(node, dict):
        return node
    for logical in ("all", "any"):
        if logical in node:
            children = []
            for child in node.get(logical) or []:
                normalized = _compiler_logic_semantics(child)
                if isinstance(normalized, dict) and set(normalized) == {logical}:
                    children.extend(normalized[logical])
                else:
                    children.append(normalized)
            children.sort(key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
            return {logical: children}
    if "not" in node:
        return {"not": _compiler_logic_semantics(node.get("not"))}
    keep = {}
    for key in ("left", "op", "right", "lower", "upper", "exit_op", "role",
                "n_atr", "atr_period", "lookback", "pct", "price_pct",
                "partial_tp_ratio", "buffer_pct", "feature"):
        if key in node:
            keep[key] = copy.deepcopy(node.get(key))
    return keep


def _compiler_semantics_contains(actual, required):
    """True when required logic exists without changing its AND/OR relation."""
    if actual == required:
        return True
    if not isinstance(actual, dict) or not isinstance(required, dict):
        return False
    required_logical = next((x for x in ("all", "any") if x in required), None)
    actual_logical = next((x for x in ("all", "any") if x in actual), None)
    if required_logical:
        if actual_logical != required_logical:
            return False
        actual_children = list(actual.get(actual_logical) or [])
        return all(any(_compiler_semantics_contains(a, r) for a in actual_children)
                   for r in (required.get(required_logical) or []))
    return any(
        _compiler_semantics_contains(child, required)
        for logical in ("all", "any")
        for child in (actual.get(logical) or [])
    )


def validate_dsl_against_admitted_recipe(dsl, recipe, contract):
    """Final executable-identity lock used immediately before formal review."""
    errors = []
    contract = contract if isinstance(contract, dict) else {}
    target = contract.get("target") or {}
    timeframe = _compiler_normalize_tf(
        target.get("timeframe") or ((dsl or {}).get("timeframe"))
    )
    expected_entry, recipe_evidence = _compiler_recipe_entry(
        recipe, contract, timeframe, errors,
    )
    try:
        import auto_trade_strategy_dsl as dsl_mod
        validated = dsl_mod.validate_strategy(dsl)
    except Exception as exc:
        validated = dsl if isinstance(dsl, dict) else {}
        errors.append("compiled_dsl_validation_failed:%s" % exc)

    recipe = recipe if isinstance(recipe, dict) else {}
    expected_symbol = target.get("symbol")
    expected_timeframe = _compiler_normalize_tf(target.get("timeframe"))
    expected_direction = str(recipe.get("trade_direction") or "").lower()
    if validated.get("direction") != expected_direction:
        errors.append("compiled_dsl_direction_drift:%s!=%s" % (
            validated.get("direction"), expected_direction,
        ))
    if expected_timeframe and _compiler_normalize_tf(validated.get("timeframe")) != expected_timeframe:
        errors.append("compiled_dsl_timeframe_drift:%s!=%s" % (
            validated.get("timeframe"), expected_timeframe,
        ))
    if expected_symbol and list(validated.get("supported_instruments") or []) != [expected_symbol]:
        errors.append("compiled_dsl_symbol_drift:%s!=%s" % (
            validated.get("supported_instruments"), expected_symbol,
        ))
    try:
        if int(validated.get("max_hold_bars")) != int(recipe.get("horizon_bars")):
            errors.append("compiled_dsl_horizon_drift:%s!=%s" % (
                validated.get("max_hold_bars"), recipe.get("horizon_bars"),
            ))
    except (TypeError, ValueError):
        errors.append("compiled_dsl_horizon_invalid")
    if str(validated.get("execution_mapping") or "bar_close") != str(
        recipe.get("execution_mapping") or ""
    ):
        errors.append("compiled_dsl_execution_mapping_drift:%s!=%s" % (
            validated.get("execution_mapping") or "bar_close",
            recipe.get("execution_mapping"),
        ))
    try:
        dsl_stop = float(validated.get("protective_stop_pct"))
        recipe_stop = float(
            (recipe.get("protective_stop_policy") or {}).get("price_pct")
        )
        from . import research_contract as rcontract
        prod_stop = float(rcontract.PRODUCTION_PROTECTIVE_STOP_PCT)
        # Allow researched 0.9% recipes to compile to current 0.5% production stop.
        if abs(dsl_stop - recipe_stop) > 1e-12 and abs(dsl_stop - prod_stop) > 1e-12:
            errors.append("compiled_dsl_protective_stop_drift")
    except (TypeError, ValueError):
        errors.append("compiled_dsl_protective_stop_identity_missing")
    try:
        if int(validated.get("execution_leverage")) != int(
            recipe.get("execution_leverage")
        ):
            errors.append("compiled_dsl_execution_leverage_drift")
    except (TypeError, ValueError):
        errors.append("compiled_dsl_execution_leverage_identity_missing")
    expected_exit = {
        "exit_op": "max_hold_only", "role": "invalidation",
    }
    actual_exit = _compiler_logic_semantics(validated.get("exit") or {})
    if actual_exit != expected_exit:
        errors.append("compiled_dsl_exit_semantics_drift")

    expected_semantics = (
        _compiler_logic_semantics(expected_entry) if expected_entry else None
    )
    actual_semantics = _compiler_logic_semantics(validated.get("entry") or {})
    if expected_semantics is None or actual_semantics != expected_semantics:
        errors.append("compiled_dsl_entry_semantics_drift")
    exact = recipe.get("event_kind") == "human_contract_exact"
    if not exact and validated.get("entry_condition_policy") is not None:
        errors.append("generic_recipe_cannot_use_exact_human_entry_policy")

    evidence = {
        "recipe": recipe_evidence,
        "expected_entry_semantics": expected_semantics,
        "actual_entry_semantics": actual_semantics,
        "expected_target": {
            "symbol": expected_symbol,
            "timeframe": expected_timeframe,
            "direction": expected_direction,
            "horizon_bars": recipe.get("horizon_bars"),
            "execution_mapping": recipe.get("execution_mapping"),
        },
        "actual_target": {
            "symbols": list(validated.get("supported_instruments") or []),
            "timeframe": validated.get("timeframe"),
            "direction": validated.get("direction"),
            "horizon_bars": validated.get("max_hold_bars"),
            "execution_mapping": validated.get("execution_mapping") or "bar_close",
        },
    }
    return {
        "ok": not errors,
        "errors": list(dict.fromkeys(str(item) for item in errors if str(item))),
        "evidence": evidence,
    }


def _compiler_required_conditions(contract, direction):
    event = contract.get("event_contract") or {}
    if "entry_conditions" in event:
        rows = list(event.get("entry_conditions") or [])
    else:
        rows = [
            row for row in (event.get("required_conditions") or contract.get("required_conditions") or [])
            if not isinstance(row, dict) or str(row.get("role") or "entry").lower() != "exit"
        ]
    # Compatibility with the existing immutable invariant contracts.
    cmp_key = "required_entry_feature_cmps_short" if direction == "short" and contract.get(
        "required_entry_feature_cmps_short") else "required_entry_feature_cmps"
    for row in contract.get(cmp_key) or []:
        rows.append({"indicator": row.get("left"), "operator": row.get("op"),
                     "value": row.get("right"), "join": "and"})
    for row in contract.get("required_entry_value_cmps") or []:
        rows.append({"indicator": row.get("left"), "operator": row.get("op"),
                     "value": row.get("value"), "join": "and"})
    return rows


def _compiler_required_exit_conditions(contract):
    event = contract.get("event_contract") or {}
    if "exit_conditions" in event:
        return list(event.get("exit_conditions") or [])
    return [
        row for row in (event.get("required_conditions") or contract.get("required_conditions") or [])
        if isinstance(row, dict) and str(row.get("role") or "").lower() == "exit"
    ]


def _compiler_session_window(contract, errors):
    """Compile an explicit UTC window. Named/text-only sessions are not guessed."""
    event = contract.get("event_contract") or {}
    raw = event.get("session_window") or event.get("time_window") or contract.get("session_window")
    if not raw:
        return None
    if not isinstance(raw, dict):
        errors.append("session_window_not_structured")
        return None
    timezone = str(raw.get("timezone") or raw.get("tz") or "UTC").upper()
    if timezone not in ("UTC", "Z"):
        errors.append("session_timezone_unsupported:%s" % timezone)
        return None

    def hour(value):
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value or "").strip()
        if ":" in text:
            hh, mm = text.split(":", 1)
            return int(hh) + int(mm) / 60.0
        return float(text)

    try:
        start = hour(raw.get("start_utc", raw.get("start")))
        end = hour(raw.get("end_utc", raw.get("end")))
        if not (0.0 <= start < 24.0 and 0.0 <= end <= 24.0 and start != end):
            raise ValueError("outside_utc_day")
    except Exception as exc:
        errors.append("session_window_invalid:%s" % exc)
        return None
    lo = {"id": "e_session_start", "left": {"feature": "hour_utc"},
          "op": "gte", "right": {"value": start}}
    hi = {"id": "e_session_end", "left": {"feature": "hour_utc"},
          "op": "lt", "right": {"value": end}}
    return {"all": [lo, hi]} if start < end else {"any": [lo, hi]}


def _compiler_parse_hold(raw, timeframe):
    """Return max holding bars from a range; never multiply the first token."""
    import math
    import re
    text = str(raw or "").strip().lower()
    if not text:
        return 24, {"raw": text, "source": "default", "max_hold_bars": 24}, []
    normalized = (text.replace("至", "_to_").replace("～", "_to_")
                  .replace("~", "_to_").replace("–", "_to_").replace("—", "_to_"))
    nums = []
    unit = "bars"
    bar_match = re.search(r"(.+?)(?:bars?|candles?|k[_ ]?lines?|根(?:k线|线)?)", normalized)
    minute_match = re.search(r"(.+?)(?:minutes?|mins?|分钟)", normalized)
    hour_match = re.search(r"(.+?)(?:hours?|hrs?|小时)", normalized)
    day_match = re.search(r"(.+?)(?:days?|天)", normalized)
    match = bar_match or minute_match or hour_match or day_match
    if match:
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", match.group(1))]
        unit = "bars" if bar_match else ("minutes" if minute_match else ("hours" if hour_match else "days"))
    elif re.fullmatch(r"[\d\s_.-]+(?:to[\d\s_.-]+)?", normalized):
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", normalized)]
    if not nums:
        return None, {"raw": text, "source": "unparsed"}, ["holding_period_unparseable:%s" % text]
    upper = max(nums)
    tf_minutes = {"5m": 5.0, "15m": 15.0, "1h": 60.0, "4h": 240.0}.get(
        _compiler_normalize_tf(timeframe))
    if unit != "bars" and not tf_minutes:
        return None, {"raw": text, "unit": unit}, ["holding_period_timeframe_unsupported:%s" % timeframe]
    if unit == "minutes":
        bars = int(math.ceil(upper / tf_minutes))
    elif unit == "hours":
        bars = int(math.ceil(upper * 60.0 / tf_minutes))
    elif unit == "days":
        bars = int(math.ceil(upper * 1440.0 / tf_minutes))
    else:
        bars = int(math.ceil(upper))
    evidence = {"raw": text, "unit": unit, "upper": upper, "max_hold_bars": bars,
                "policy": "upper_bound_of_expected_range"}
    if bars < 1 or bars > 240:
        return None, evidence, ["holding_period_outside_dsl_bounds:%s" % bars]
    return bars, evidence, []


def _compiler_exits_from_spec(spec, errors):
    """Compile structured exits from their dedicated narrative fields.

    Protective stop text is never scanned for take-profit percentages.  Each
    explicit parameter is validated in its own clause and an invalid value is
    rejected rather than replaced by a convenient default.
    """
    import re
    try:
        import auto_trade_strategy_dsl as dsl_mod
    except Exception as exc:
        errors.append("dsl_runtime_unavailable:%s" % exc)
        return []

    exit_text = str(spec.get("exit_logic") or "").lower()
    take_profit_text = str(spec.get("take_profit_logic") or "").lower()
    stop_text = str(spec.get("stop_logic") or "").lower()
    invalidation_text = str(spec.get("invalidation_logic") or "").lower()

    def clauses(*values):
        out = []
        for value in values:
            for row in re.split(r"\s+(?:or|plus)\s+|[;；]|(?:或者|或)", value or ""):
                row = row.strip()
                if row and row not in ("none", "n/a", "na", "无"):
                    out.append(row)
        return out

    tp_clauses = clauses(exit_text, take_profit_text)
    invalidation_clauses = clauses(exit_text, stop_text, invalidation_text)
    leaves = []
    seen_by_op = {}

    def add_leaf(leaf):
        op = leaf.get("exit_op")
        comparable = dict(leaf)
        comparable.pop("id", None)
        marker = json.dumps(comparable, ensure_ascii=False, sort_keys=True)
        previous = seen_by_op.get(op)
        if previous is not None:
            if previous != marker:
                errors.append("exit_parameter_conflict:%s" % op)
            return
        seen_by_op[op] = marker
        leaf = dict(leaf)
        leaf["id"] = "x_%s" % op
        leaves.append(leaf)

    multiplier_re = re.compile(r"(\d+(?:\.\d+)?)\s*(?:x|\u00d7)\s*atr\b")
    for clause in tp_clauses:
        is_partial = any(token in clause for token in (
            "partial", "scale-out", "scale out", "分批", "部分止盈", "减仓",
        )) and "atr" in clause
        is_trailing = "atr" in clause and any(token in clause for token in (
            "trail", "trailing", "跟踪", "移动止盈",
        ))
        if is_partial:
            multiplier = multiplier_re.search(clause)
            ratio_match = re.search(
                r"(?:ratio|比例|仓位)[_ :=]*(\d+(?:\.\d+)?)\s*(%)?", clause,
            )
            if not multiplier:
                errors.append("partial_tp_atr_multiplier_missing")
            if not ratio_match:
                errors.append("partial_tp_ratio_missing")
            if multiplier and ratio_match:
                n_atr = float(multiplier.group(1))
                ratio = float(ratio_match.group(1))
                if ratio_match.group(2):
                    ratio /= 100.0
                if not (dsl_mod.PARTIAL_TP_ATR_N_MIN <= n_atr <= dsl_mod.PARTIAL_TP_ATR_N_MAX):
                    errors.append("partial_tp_atr_n_atr_outside_bounds:%s" % n_atr)
                if not (dsl_mod.PARTIAL_TP_RATIO_MIN <= ratio <= dsl_mod.PARTIAL_TP_RATIO_MAX):
                    errors.append("partial_tp_ratio_outside_bounds:%s" % ratio)
                add_leaf({
                    "exit_op": "partial_tp_atr", "role": "take_profit",
                    "n_atr": n_atr, "atr_period": 14,
                    "partial_tp_ratio": ratio,
                })
        if is_trailing:
            multiplier = multiplier_re.search(clause)
            if not multiplier:
                errors.append("atr_trailing_multiplier_missing")
            else:
                n_atr = float(multiplier.group(1))
                if not (dsl_mod.ATR_TRAIL_N_MIN <= n_atr <= dsl_mod.ATR_TRAIL_N_MAX):
                    errors.append("atr_trailing_n_atr_outside_bounds:%s" % n_atr)
                add_leaf({
                    "exit_op": "atr_trailing", "role": "take_profit",
                    "n_atr": n_atr, "atr_period": 14,
                })

        fixed_named = any(token in clause for token in (
            "fixed_pct_tp", "fixed tp", "fixed take profit", "固定止盈",
        ))
        if fixed_named:
            pct_match = re.search(
                r"(?:fixed_pct_tp|fixed\s+(?:tp|take profit)|固定止盈)"
                r"[_ :=]*(\d+(?:\.\d+)?)\s*(%|percent|pct)?",
                clause,
            )
            if not pct_match:
                errors.append("fixed_pct_tp_parameter_missing")
            else:
                raw_pct = float(pct_match.group(1))
                if pct_match.group(2) in ("%", "percent", "pct"):
                    raw_pct /= 100.0
                try:
                    pct = dsl_mod.refuse_fixed_tiny_tp(
                        raw_pct, context="compiler.fixed_pct_tp",
                    )
                    add_leaf({
                        "exit_op": "fixed_pct_tp", "role": "take_profit",
                        "pct": pct,
                    })
                except Exception as exc:
                    errors.append("fixed_pct_tp_%s" % exc)

    for clause in invalidation_clauses:
        if any(token in clause for token in (
            "swing", "swing extreme", "摆动极值", "前高", "前低",
        )):
            lookback_match = re.search(r"(?:lookback|回看)[_ :=]*(\d+)", clause)
            if not lookback_match:
                if "swing_extreme" not in seen_by_op:
                    errors.append("swing_extreme_lookback_missing")
            else:
                lookback = int(lookback_match.group(1))
                if not (dsl_mod.SWING_LOOKBACK_MIN <= lookback <= dsl_mod.SWING_LOOKBACK_MAX):
                    errors.append("swing_extreme_lookback_outside_bounds:%s" % lookback)
                add_leaf({
                    "exit_op": "swing_extreme", "role": "invalidation",
                    "lookback": lookback,
                })

        is_entry_wick = any(token in clause for token in (
            "entry wick", "signal-bar wick", "signal bar wick",
            "入场k线影线", "信号k线影线", "入场影线", "信号影线",
        ))
        if is_entry_wick:
            buffer_match = re.search(
                r"(?:buffer|缓冲)[_ :=]*(\d+(?:\.\d+)?)\s*(%|percent|pct)?",
                clause,
            )
            if not buffer_match:
                errors.append("entry_wick_buffer_parameter_missing")
            else:
                buffer_pct = float(buffer_match.group(1))
                if buffer_match.group(2) in ("%", "percent", "pct"):
                    buffer_pct /= 100.0
                if not (
                    dsl_mod.ENTRY_WICK_BUFFER_MIN <= buffer_pct
                    <= dsl_mod.ENTRY_WICK_BUFFER_MAX
                ):
                    errors.append("entry_wick_buffer_outside_bounds:%s" % buffer_pct)
                add_leaf({
                    "exit_op": "entry_wick_buffer", "role": "invalidation",
                    "buffer_pct": buffer_pct,
                })
    if not leaves:
        blob = " | ".join((exit_text, take_profit_text, stop_text, invalidation_text))
        errors.append("exit_semantics_unrepresentable:%s" % blob[:240])
    return leaves


def _compiler_failure(common, errors, evidence=None):
    out = dict(common)
    out.update({
        "ok": False,
        "dsl": None,
        "status": "codex_implementation_failed",
        "reason": "mechanism_fidelity_unrepresentable",
        "fidelity_errors": list(dict.fromkeys(str(x) for x in errors if str(x))),
        "fidelity_evidence": evidence or {},
        "supplements_needed": [],
        "core_features": [],
    })
    return out


def _compiler_audit_dsl(dsl, required, allowed, forbidden, contract, errors,
                        allowed_enforced=False):
    from .config import FORBIDDEN_CORE_FEATURES
    try:
        import auto_trade_strategy_dsl as dsl_mod
        dsl_mod.validate_strategy(dsl)
    except Exception as exc:
        errors.append("dsl_validation_failed:%s" % exc)
    used = _compiler_extract_features(dsl)
    supported = set(getattr(dsl_mod, "FEATURES", set())) if "dsl_mod" in locals() else set()
    for feature in required:
        if feature not in supported:
            errors.append("required_feature_unsupported:%s" % feature)
        elif feature not in used:
            errors.append("required_feature_not_consumed:%s" % feature)
    for feature in allowed:
        if feature not in supported:
            errors.append("allowed_feature_unsupported:%s" % feature)
    if allowed_enforced:
        for feature in sorted(set(required) - set(allowed)):
            errors.append("required_feature_outside_allowlist:%s" % feature)
        for feature in sorted(used - set(allowed)):
            errors.append("feature_outside_allowlist:%s" % feature)
    authorized = set(required) | set(allowed)
    for feature in used:
        if any(token in feature.lower() for token in FORBIDDEN_CORE_FEATURES) and feature not in authorized:
            errors.append("unapproved_core_feature:%s" % feature)
    forbidden_rows = [str(x).strip().lower() for x in forbidden]
    forbidden_blob = " ".join(forbidden_rows)
    for feature in used:
        fl = feature.lower()
        explicitly_forbidden = fl in forbidden_rows
        if len(fl) >= 3 and fl in forbidden_blob:
            explicitly_forbidden = True
        if any(fl.startswith(token) and token in forbidden_blob
               for token in FORBIDDEN_CORE_FEATURES):
            explicitly_forbidden = True
        if explicitly_forbidden:
            errors.append("forbidden_substitution_used:%s" % feature)
    return used


def _compiler_finalize_fidelity(fidelity, book, statement, audit_fn=None):
    """Merge compiler evidence into Gate1 without ever upgrading a failed diff."""
    fidelity = dict(fidelity or {})
    book = book or {}
    compiler_errors = list(book.get("fidelity_errors") or [])
    fidelity["compiler_fidelity_errors"] = compiler_errors
    fidelity["compiler_fidelity_evidence"] = book.get("fidelity_evidence") or {}
    fidelity["compiler_contract_verified"] = bool(
        (book.get("fidelity_evidence") or {}).get("structured_contract_verified")
    )
    if not book.get("ok", True) or not book.get("dsl") or compiler_errors:
        fidelity["pass"] = False
        fidelity["failure_class"] = "implementation_failure"
        fidelity["notes"] = list(fidelity.get("notes") or []) + [
            "compiler_fail_closed:%s" % compiler_errors
        ]
        rule_audit = {"reject": True, "reject_reasons": compiler_errors,
                      "condition_audit": []}
    else:
        audit_fn = audit_fn or rule_based_condition_audit
        rule_audit = audit_fn(book.get("dsl"), statement, approved_supplements=[])
        if rule_audit.get("reject"):
            fidelity["pass"] = False
            fidelity["failure_class"] = "implementation_failure"
            fidelity["notes"] = list(fidelity.get("notes") or []) + [
                "rule_audit_reject:%s" % (rule_audit.get("reject_reasons") or [])
            ]
    fidelity["rule_audit_reject"] = bool(rule_audit.get("reject"))
    fidelity["rule_audit"] = {
        "reject": bool(rule_audit.get("reject")),
        "reject_reasons": rule_audit.get("reject_reasons") or [],
    }
    return fidelity


def codex_implement_from_spec(spec_pack):
    """Compile a mechanism contract into DSL, or fail closed with fidelity errors.

    ResearchContract v2 is authoritative.  The compiler never guesses direction,
    cross-asset data, entry timing, session windows, or an unknown family.
    """
    import copy
    import uuid

    spec_pack = spec_pack if isinstance(spec_pack, dict) else {}
    meta = spec_pack.get("meta") or {}
    spec = spec_pack.get("mechanism_spec") or {}
    contract = _compiler_contract(spec_pack, spec, meta)
    target = contract.get("target") or {}
    event_contract = contract.get("event_contract") or {}
    feature_contract = contract.get("feature_contract") or {}
    data_contract = contract.get("data_contract") or {}
    admitted_recipe = spec_pack.get("admitted_recipe_lock")
    if not isinstance(admitted_recipe, dict) or not admitted_recipe:
        admitted_recipe = None
    errors = []
    contract_integrity = research_contract_mod.verify_contract_integrity(contract)
    # Legacy diagnostic packs predate ResearchContract v2.  They may still be
    # compiled for analysis, but any admitted/formal recipe requires a fully
    # signed immutable contract and is checked again in _compiler_recipe_entry.
    if admitted_recipe is not None and not contract_integrity.get("ok"):
        errors.extend(contract_integrity.get("reasons") or [])

    def resolve_locked(name, contract_value, meta_value, spec_values=None):
        def norm(value):
            if name == "timeframe":
                return _compiler_normalize_tf(value)
            if name == "direction":
                return str(value).lower()
            return str(value)

        locked = norm(contract_value) if contract_value not in (None, "") else None
        selected = norm(meta_value) if meta_value not in (None, "") else None
        suitable = [norm(x) for x in (spec_values or []) if x not in (None, "")]
        if locked and selected and locked != selected:
            errors.append("target_%s_conflict:%s|%s" % (name, locked, selected))
        resolved = locked or selected or (suitable[0] if suitable else None)
        if resolved and suitable and resolved not in suitable:
            errors.append("target_%s_outside_mechanism_spec:%s:not_in:%s" % (
                name, resolved, "|".join(suitable)))
        return resolved

    symbol = resolve_locked("symbol", target.get("symbol"), meta.get("symbol"), spec.get("suitable_symbols") or [])
    timeframe = resolve_locked("timeframe", target.get("timeframe"), meta.get("timeframe"), spec.get("suitable_timeframes") or [])
    direction = resolve_locked("direction", target.get("direction"), meta.get("direction"), [])
    direction = str(direction or "").lower()
    if not symbol:
        errors.append("target_symbol_missing")
    if not timeframe:
        errors.append("target_timeframe_missing")
    if direction not in ("long", "short"):
        errors.append("target_direction_invalid:%s" % (direction or "missing"))

    title = meta.get("title") or spec.get("mechanism_name") or "step_a_strategy"
    stmt = spec_to_mechanism_statement(spec)
    common = {
        "title": title,
        "thesis": meta.get("thesis") or spec.get("why_edge_exists"),
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "mechanism_spec": spec,
        "mechanism_statement": stmt,
        "research_contract_id": contract.get("contract_id"),
    }

    cross_symbols = _compiler_list(feature_contract.get("cross_asset_symbols"))
    fam = (str(spec.get("mechanism_family") or "") + " "
           + str(spec.get("entry_logic") or "")).lower()
    cross_semantics = bool(cross_symbols) or any(
        key in fam for key in ("cross_asset", "cross-asset", "lead_lag", "lead-lag",
                               "btc_lead", "pairs_", "cointegration", "correlation_breakdown"))
    if cross_semantics:
        errors.append("cross_asset_semantics_unsupported:no_symbol_qualified_feature_feed:%s" % (
            ",".join(str(x) for x in cross_symbols) or "declared_by_mechanism"))
    missing_data = _compiler_list(data_contract.get("missing"))
    required_data = [str(x) for x in _compiler_list(data_contract.get("required"))]
    available_data = [str(x) for x in _compiler_list(data_contract.get("available"))]
    if available_data:
        missing_data.extend(x for x in required_data if x not in available_data)
    if missing_data:
        errors.append("required_data_missing:%s" % ",".join(dict.fromkeys(str(x) for x in missing_data)))

    timing = event_contract.get("entry_timing") or contract.get("entry_timing") or {}
    if isinstance(timing, str):
        timing = {"mode": timing}
    timing_mode = str((timing or {}).get("mode") or "unspecified").strip().lower()
    timing_tf = _compiler_normalize_tf((timing or {}).get("timeframe"))
    if timing_mode != "unspecified" and timing_tf and timeframe and timing_tf != timeframe:
        errors.append("entry_timing_timeframe_conflict:%s!=%s" % (timing_tf, timeframe))
    if timing_mode not in ("unspecified", "bar_close", "next_bar_open"):
        errors.append("entry_timing_mode_unsupported:%s" % timing_mode)
    if admitted_recipe is not None:
        execution_mapping = str(admitted_recipe.get("execution_mapping") or "")
    else:
        execution_mapping = (
            timing_mode if timing_mode in ("bar_close", "next_bar_open")
            else "bar_close"
        )

    hold_raw = spec.get("expected_holding_period") or meta.get("holding_horizon")
    max_hold, hold_evidence, hold_errors = _compiler_parse_hold(hold_raw, timeframe)
    errors.extend(hold_errors)
    if admitted_recipe is not None:
        try:
            recipe_hold = int(admitted_recipe.get("horizon_bars"))
            if max_hold is not None and int(max_hold) != recipe_hold:
                errors.append("admitted_recipe_holding_period_spec_drift:%s!=%s" % (
                    max_hold, recipe_hold,
                ))
            max_hold = recipe_hold
            hold_evidence = dict(hold_evidence or {})
            hold_evidence.update({
                "source": "admitted_recipe_lock",
                "max_hold_bars": recipe_hold,
            })
        except (TypeError, ValueError):
            errors.append("admitted_recipe_horizon_invalid")
    (
        required_features, allowed_features, forbidden_substitutions,
        allowed_features_enforced,
    ) = _compiler_feature_rules(contract, spec, meta, timeframe)
    recipe_entry = None
    recipe_evidence = None
    if admitted_recipe is not None:
        recipe_entry, recipe_evidence = _compiler_recipe_entry(
            admitted_recipe, contract, timeframe, errors,
        )
        if str(admitted_recipe.get("trade_direction") or "").lower() != direction:
            errors.append("admitted_recipe_target_direction_drift")
        # Admitted-recipe mapped features are authorized even if they match
        # FORBIDDEN_CORE_FEATURES tokens (e.g. rsi14 from research rsi_14).
        authorized_from_recipe = []
        for row in (recipe_evidence or {}).get("factor_mappings") or []:
            feat = str((row or {}).get("dsl_feature") or "").strip()
            if feat:
                authorized_from_recipe.append(feat)
        for feat in recipe_policy.GENERIC_FACTOR_TO_DSL.values():
            if feat:
                authorized_from_recipe.append(str(feat))
        for feat in authorized_from_recipe:
            if feat not in allowed_features:
                allowed_features.append(feat)
            if feat not in required_features:
                # Keep as allowed only; do not force unused required features.
                pass
    evidence = {
        "contract_id": contract.get("contract_id"),
        "contract_present": bool(contract),
        "target": {"symbol": symbol, "timeframe": timeframe, "direction": direction},
        "entry_timing": {"mode": timing_mode, "timeframe": timing_tf,
                         "locked": timing_mode != "unspecified",
                         "compiler_execution_default": "bar_close",
                         "compiled_execution_mapping": execution_mapping},
        "cross_asset_symbols": [str(x) for x in cross_symbols],
        "required_features": required_features,
        "allowed_features": allowed_features,
        "allowed_features_enforced": allowed_features_enforced,
        "holding_period": hold_evidence,
        "admitted_recipe": recipe_evidence,
        "contract_integrity": contract_integrity,
    }
    if errors:
        return _compiler_failure(common, errors, evidence)

    key = ("wsa_%s_%s_%s" % (
        str(symbol).split("-")[0].lower(), timeframe, uuid.uuid4().hex[:6]
    )).replace("-", "_")

    required_conditions = _compiler_required_conditions(contract, direction)
    compiled_conditions = []
    for index, raw in enumerate(required_conditions, 1):
        leaf, join = _compiler_condition_leaf(raw, index, timeframe, errors)
        if leaf:
            compiled_conditions.append((leaf, join))
    contract_entry = _compiler_join_conditions(compiled_conditions)
    session_entry = _compiler_session_window(contract, errors)
    if session_entry and admitted_recipe is None:
        contract_entry = {"all": [session_entry, contract_entry]} if contract_entry else session_entry
    evidence["required_conditions_count"] = len(required_conditions)
    evidence["compiled_conditions_count"] = len(compiled_conditions)
    executable_condition_count = (
        len(admitted_recipe.get("terms") or [])
        if admitted_recipe is not None
        else len(compiled_conditions) + (2 if session_entry else 0)
    )
    if required_conditions and executable_condition_count < 1:
        errors.append("required_event_has_no_compilable_conditions")
    required_exit_conditions = _compiler_required_exit_conditions(contract)
    compiled_exit_conditions = []
    for index, raw in enumerate(required_exit_conditions, 1):
        leaf, join = _compiler_condition_leaf(raw, index, timeframe, errors, phase="exit")
        if leaf:
            leaf["role"] = "invalidation" if str(raw.get("exit_role") or "").lower() == "invalidation" else "take_profit"
            compiled_exit_conditions.append((leaf, join))
    contract_exit = _compiler_join_conditions(compiled_exit_conditions)
    evidence["required_exit_conditions_count"] = len(required_exit_conditions)
    evidence["compiled_exit_conditions_count"] = len(compiled_exit_conditions)
    exact_flag = event_contract.get("require_exact_event_fidelity")
    exact_event = bool(
        exact_flag if exact_flag is not None
        else (required_conditions or required_exit_conditions or session_entry)
    )
    required_clauses = _compiler_list(event_contract.get("required_clauses"))
    # Narrative clauses remain audit evidence, but they are not automatically
    # machine-exact.  Only an explicitly exact contract may require literal
    # machine conditions; otherwise ordinary natural-language briefs would be
    # rejected even though the mechanism-family compiler can represent them.
    if exact_event and not required_conditions and not session_entry and not required_exit_conditions:
        errors.append("exact_event_missing_machine_compilable_conditions")

    # Prebuilt DSLs are audited, not rewritten to conceal target drift.
    prebuilt = spec_pack.get("dsl")
    if not isinstance(prebuilt, dict):
        prebuilt = spec_pack.get("dsl_long" if direction == "long" else "dsl_short")
    if isinstance(prebuilt, dict):
        dsl = copy.deepcopy(prebuilt)
        if dsl.get("schema") != "qiyu_strategy_dsl_v1":
            errors.append("prebuilt_dsl_schema_invalid:%s" % dsl.get("schema"))
        if dsl.get("direction") != direction:
            errors.append("prebuilt_direction_drift:%s!=%s" % (dsl.get("direction"), direction))
        if _compiler_normalize_tf(dsl.get("timeframe")) != timeframe:
            errors.append("prebuilt_timeframe_drift:%s!=%s" % (dsl.get("timeframe"), timeframe))
        if list(dsl.get("supported_instruments") or []) != [symbol]:
            errors.append("prebuilt_symbol_drift:%s!=%s" % (dsl.get("supported_instruments"), symbol))
        prebuilt_mapping = str(dsl.get("execution_mapping") or "bar_close")
        if prebuilt_mapping != execution_mapping:
            errors.append("prebuilt_execution_mapping_drift:%s!=%s" % (
                prebuilt_mapping, execution_mapping))
        dsl.setdefault("key", key)
        dsl.setdefault("name", str(title)[:120])
        dsl["live_enabled"] = False
        dsl["auto_trade_eligible"] = False
        dsl["origin"] = dsl.get("origin") or "step_a_prebuilt_dsl"
        used = _compiler_audit_dsl(
            dsl, required_features, allowed_features, forbidden_substitutions,
            contract, errors, allowed_enforced=allowed_features_enforced)
        if contract_entry and (exact_event or required_conditions):
            expected_semantics = _compiler_logic_semantics(contract_entry)
            actual_semantics = _compiler_logic_semantics(dsl.get("entry") or {})
            if actual_semantics != expected_semantics:
                errors.append("prebuilt_exact_event_semantics_drift")
                evidence["expected_entry_semantics"] = expected_semantics
                evidence["actual_entry_semantics"] = actual_semantics
        if contract_exit:
            expected_exit = _compiler_logic_semantics(contract_exit)
            actual_exit = _compiler_logic_semantics(dsl.get("exit") or {})
            if not _compiler_semantics_contains(actual_exit, expected_exit):
                errors.append("prebuilt_required_exit_semantics_missing")
                evidence["expected_exit_semantics"] = expected_exit
                evidence["actual_exit_semantics"] = actual_exit
        if max_hold is not None and int(dsl.get("max_hold_bars") or 0) != int(max_hold):
            errors.append("prebuilt_holding_period_drift:%s!=%s" % (
                dsl.get("max_hold_bars"), max_hold))
        if admitted_recipe is not None:
            identity = validate_dsl_against_admitted_recipe(
                dsl, admitted_recipe, contract,
            )
            if not identity.get("ok"):
                errors.extend(identity.get("errors") or [])
            evidence["admitted_recipe_final_validation"] = identity.get("evidence") or {}
        evidence["used_features"] = sorted(used)
        evidence["structured_contract_verified"] = bool(contract) and not errors
        if errors:
            return _compiler_failure(common, errors, evidence)
        out = dict(common)
        out.update({
            "ok": True, "dsl": dsl, "status": "codex_implemented",
            "fidelity_errors": [], "fidelity_evidence": evidence,
            "supplements_needed": [], "core_features": sorted(used),
            "prebuilt_dsl": True,
        })
        return out

    entry_leaves = []
    if admitted_recipe is not None:
        entry = recipe_entry
        evidence["entry_source"] = "admitted_recipe_lock"
    elif contract_entry:
        entry = contract_entry
        evidence["entry_source"] = "research_contract_required_conditions"
    elif any(k in fam for k in ("session", "opening_range", "tod_structure", "time_structure")):
        errors.append("session_semantics_missing_structured_window_or_conditions")
        entry = None
    elif any(k in fam for k in ("trend_continuation", "trend_pullback", "momentum_continuation")):
        if direction == "long":
            entry_leaves.extend([
                {"id": "e_break_high", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}},
                {"id": "e_hold_above", "left": {"feature": "low"}, "op": "gt", "right": {"feature": "prev_low20"}},
            ])
        else:
            entry_leaves.extend([
                {"id": "e_break_low", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}},
                {"id": "e_hold_below", "left": {"feature": "high"}, "op": "lt", "right": {"feature": "prev_high20"}},
            ])
        entry_leaves.append({"id": "e_vol_ok", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.3}})
        entry = {"all": entry_leaves}
    elif any(k in fam for k in (
        "vol_regime", "volatility_expansion", "atr_regime", "vol_break",
        "volume_anomaly_breakout",
    )):
        entry_leaves.extend([
            {"id": "e_atr_expand", "left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.002}},
            {"id": "e_vol_expand", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 1.2}},
            {"id": "e_dir", "left": {"feature": "close"},
             "op": "gt" if direction == "long" else "lt", "right": {"feature": "open"}},
        ])
        entry = {"all": entry_leaves}
    elif any(k in fam for k in ("compression_release", "atr_squeeze", "structural_breakout", "squeeze_breakout", "vol_squeeze_break")):
        entry_leaves.append({"id": "e_atr_compress", "left": {"feature": "atr14"},
                             "op": "lt", "right": {"feature": "close", "scale": 0.0025}})
        entry_leaves.append({"id": "e_break", "left": {"feature": "close"},
                             "op": "cross_above" if direction == "long" else "cross_below",
                             "right": {"feature": "prev_high20" if direction == "long" else "prev_low20"}})
        entry_leaves.append({"id": "e_dir", "left": {"feature": "close"},
                             "op": "gt" if direction == "long" else "lt", "right": {"feature": "open"}})
        entry_leaves.append({"id": "e_vol_confirm", "left": {"feature": "vol_z20"},
                             "op": "gt", "right": {"value": 0.5}})
        entry = {"all": entry_leaves}
    elif any(k in fam for k in ("failed_breakout", "breakout_fail", "false_break", "liquidity_fail")):
        anchor = "prev_high20" if direction == "short" else "prev_low20"
        entry_leaves.extend([
            {"id": "e_false_break", "left": {"feature": "high" if direction == "short" else "low"},
             "op": "gt" if direction == "short" else "lt", "right": {"feature": anchor}},
            {"id": "e_fail_close", "left": {"feature": "close"},
             "op": "lt" if direction == "short" else "gt", "right": {"feature": anchor}},
            {"id": "e_vol_spike", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.6}},
        ])
        entry = {"all": entry_leaves}
    elif any(k in fam for k in ("mean_reversion", "mean_revert", "stretch_revert", "z_revert", "non_fade")):
        entry_leaves.extend([
            {"id": "e_atr_stretch", "left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.001}},
            {"id": "e_stretch", "left": {"feature": "close"},
             "op": "lt" if direction == "long" else "gt",
             "right": {"feature": "prev_low20" if direction == "long" else "prev_high20"}},
            {"id": "e_turn", "left": {"feature": "close"},
             "op": "gt" if direction == "long" else "lt", "right": {"feature": "open"}},
        ])
        entry = {"all": entry_leaves}
    elif any(k in fam for k in ("liquidity_sweep", "liquidity_vacuum", "stop_hunt",
                                "sweep_reclaim", "exhaustion_fade", "crowding_fade",
                                "liquidation_bounce", "forced_liquidation")):
        anchor = "prev_low20" if direction == "long" else "prev_high20"
        entry_leaves.extend([
            {"id": "e_sweep", "left": {"feature": "low" if direction == "long" else "high"},
             "op": "lt" if direction == "long" else "gt", "right": {"feature": anchor}},
            {"id": "e_reclaim", "left": {"feature": "close"},
             "op": "gt" if direction == "long" else "lt", "right": {"feature": anchor}},
            {"id": "e_vol_spike", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.8}},
        ])
        entry = {"all": entry_leaves}
    else:
        errors.append("mechanism_family_unimplemented:%s" % (spec.get("mechanism_family") or "missing"))
        entry = None
    evidence.setdefault("entry_source", "explicit_family_template" if entry else None)

    # Existing invariant contracts may provide a complete executable exit;
    # ResearchContract v2 exit conditions are additive to the mechanism spec.
    invariant_exit_leaves = []
    for index, raw in enumerate(contract.get("required_exit_ops") or [], 1):
        if not isinstance(raw, dict):
            errors.append("required_exit_op_not_object:%s" % index)
            continue
        leaf = copy.deepcopy(raw)
        leaf.setdefault("id", "x_contract_op_%d" % index)
        invariant_exit_leaves.append(leaf)
    exit_cmp_key = "required_exit_feature_cmps_short" if direction == "short" and contract.get(
        "required_exit_feature_cmps_short") else "required_exit_feature_cmps"
    for index, raw in enumerate(contract.get(exit_cmp_key) or [], len(invariant_exit_leaves) + 1):
        leaf, unused_join = _compiler_condition_leaf(raw, index, timeframe, errors, phase="exit")
        if leaf:
            leaf["role"] = raw.get("role") or "take_profit"
            invariant_exit_leaves.append(leaf)
    if admitted_recipe is not None:
        # Discovery admitted only the mandatory protective stop plus a fixed
        # horizon close.  The stop is a top-level execution identity; this leaf
        # records that no unresearched take-profit/invalidation may fire early.
        exit_parts = [{
            "id": "x_fixed_horizon", "exit_op": "max_hold_only",
            "role": "invalidation",
        }]
        evidence["exit_source"] = "admitted_fixed_horizon_identity"
    else:
        exit_parts = list(invariant_exit_leaves)
        if invariant_exit_leaves:
            evidence["exit_source"] = "immutable_invariant_contract"
        else:
            spec_exit_leaves = _compiler_exits_from_spec(spec, errors)
            exit_parts.extend(spec_exit_leaves)
            evidence["exit_source"] = "mechanism_spec_structured_family"
    if contract_exit and admitted_recipe is None:
        exit_parts.append(contract_exit)
        evidence["exit_source"] += "+research_contract_required_exit"
    if not exit_parts:
        errors.append("exit_compilation_failed")
        exit = None
    elif len(exit_parts) == 1:
        exit = exit_parts[0]
    else:
        exit = {"any": exit_parts}

    if errors or not entry or not exit or max_hold is None:
        return _compiler_failure(common, errors or ["entry_compilation_failed"], evidence)
    dsl = {
        "schema": "qiyu_strategy_dsl_v1", "key": key, "name": str(title)[:120],
        "direction": direction, "timeframe": timeframe,
        "supported_instruments": [symbol], "entry": entry, "exit": exit,
        "max_hold_bars": max_hold, "execution_mapping": execution_mapping,
        "protective_stop_pct": (
            float(research_contract_mod.PRODUCTION_PROTECTIVE_STOP_PCT)
            if admitted_recipe is not None else None
        ),
        "execution_leverage": (
            admitted_recipe.get("execution_leverage")
            if admitted_recipe is not None else None
        ),
        "description": ("step_a|%s|sl0.9|%s" % (
            spec.get("mechanism_family"), (spec.get("entry_logic") or "")[:80]))[:2000],
        "origin": "step_a_codex_faithful", "version": 1,
        "live_enabled": False, "auto_trade_eligible": False,
    }
    if executable_condition_count == 1 and exact_event:
        try:
            import auto_trade_strategy_dsl as dsl_mod
            dsl["entry_condition_policy"] = {
                "mode": "exact_human_contract",
                "source": "qiyu_research_contract_v2",
                "research_contract_id": contract.get("contract_id"),
                "entry_semantics_hash": dsl_mod.dsl_hash(entry),
                "exact_condition_count": 1,
            }
        except Exception as exc:
            errors.append("single_exact_entry_attestation_failed:%s" % exc)
    used = _compiler_audit_dsl(
        dsl, required_features, allowed_features, forbidden_substitutions,
        contract, errors, allowed_enforced=allowed_features_enforced)
    if admitted_recipe is not None:
        identity = validate_dsl_against_admitted_recipe(
            dsl, admitted_recipe, contract,
        )
        if not identity.get("ok"):
            errors.extend(identity.get("errors") or [])
        evidence["admitted_recipe_final_validation"] = identity.get("evidence") or {}
    evidence["used_features"] = sorted(used)
    evidence["structured_contract_verified"] = bool(contract) and not errors
    if errors:
        return _compiler_failure(common, errors, evidence)
    out = dict(common)
    out.update({
        "ok": True, "dsl": dsl, "status": "codex_implemented",
        "fidelity_errors": [], "fidelity_evidence": evidence,
        "supplements_needed": [], "core_features": sorted(used),
    })
    return out


def _resolve_matrix_symbols(spec=None, meta=None, primary=None, enable=False):
    """Whitelist for multi-symbol matrix eval. Primary stays first (seed focus)."""
    primary = primary or None
    if not enable:
        return [primary] if primary else []
    spec = spec or {}
    meta = meta or {}
    raw = list(spec.get("suitable_symbols") or [])
    if not raw:
        raw = list(meta.get("matrix_generalization") or [])
    if not raw and primary:
        raw = [primary]
    out = []
    seen = set()
    if primary:
        out.append(primary)
        seen.add(str(primary))
    for s in raw:
        s = str(s or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _load_matrix_frames(symbols, timeframe, dual_mod, chunk_gc_every=4):
    """Load frames for matrix symbols; skip missing; GC periodically for low RAM."""
    frames = []
    errors = []
    _mg = None
    try:
        from auto_driver import memory_guard as _mg  # noqa: N814
    except Exception:
        _mg = None
    for i, sym in enumerate(symbols or []):
        try:
            if _mg is not None:
                total, avail = _mg.read_meminfo_mb()
                if avail < 100:
                    _mg.force_release("matrix_load_abort")
                    errors.append({"symbol": sym, "error": "ram_abort_avail_%d" % avail})
                    break
            frm = dual_mod._frame(sym, timeframe)
            if frm is None or (hasattr(frm, "__len__") and len(frm) == 0):
                errors.append({"symbol": sym, "error": "empty_frame"})
                continue
            frames.append((sym, frm))
            if chunk_gc_every and (i + 1) % int(chunk_gc_every) == 0:
                import gc
                gc.collect()
        except Exception as exc:
            errors.append({"symbol": sym, "error": str(exc)})
    return frames, errors


def _matrix_pool_backtest(definition, symbols, timeframe, dual_mod, friction="observed_base"):
    """Full-history BT across matrix; pool trades. Primary (first) errors are fatal."""
    all_trades = []
    per = []
    primary_err = None
    for i, sym in enumerate(symbols or []):
        try:
            # Bail out early if RAM collapses mid-pool
            try:
                from auto_driver import memory_guard as _mg
                _, avail = _mg.read_meminfo_mb()
                if avail < 90 and i > 0:
                    per.append({"symbol": sym, "n_trades": 0, "ok": False, "error": "ram_abort"})
                    _mg.force_release("matrix_pool_abort")
                    break
            except Exception:
                pass
            r = dual_mod._backtest(definition, sym, timeframe, friction)
            trades = list((r or {}).get("trades") or [])
            for t in trades:
                if isinstance(t, dict):
                    tt = dict(t)
                    tt.setdefault("matrix_symbol", sym)
                    all_trades.append(tt)
                else:
                    all_trades.append(t)
            per.append({"symbol": sym, "n_trades": len(trades), "ok": True})
            if (i + 1) % 3 == 0:
                import gc
                gc.collect()
        except Exception as exc:
            per.append({"symbol": sym, "n_trades": 0, "ok": False, "error": str(exc)})
            if i == 0:
                primary_err = str(exc)
    metrics = dual_mod._metrics_from_trades(all_trades) if all_trades else {}
    return {
        "trades": all_trades,
        "base_metrics": metrics,
        "per_symbol": per,
        "primary_error": primary_err,
        "n_symbols": len(symbols or []),
        "n_trades": len(all_trades),
    }


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
        contract = _compiler_contract(spec, spec, {})
        feature_contract = contract.get("feature_contract") or {}
        primary_tf = ((contract.get("target") or {}).get("timeframe"))
        locked_features = set(
            _compiler_feature(value, primary_tf=primary_tf)
            for value in (feature_contract.get("required_features") or [])
        )
        locked_features.discard(None)
        dsl = copy.deepcopy(book.get("dsl") or {})
        # Only explicitly tunable, non-contract leaves may move.  Empty
        # tunables means no numeric mutation; it never means “tune all”.
        changed = []

        def walk(node):
            if isinstance(node, dict):
                right = node.get("right")
                if isinstance(right, dict) and "value" in right:
                    feat = str(((node.get("left") or {}).get("feature")) or "")
                    explicitly_tunable = bool(
                        allowed and any(a.lower() in feat.lower() for a in allowed)
                    )
                    if explicitly_tunable and feat not in locked_features:
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
        book["repair_locked_features"] = sorted(locked_features)
        return book, False
    # mechanism_viability_verdict — no further mutation
    book = dict(book)
    book["repair_type"] = round_type
    book["viability_verdict_pending"] = True
    return book, True


def _implementation_fingerprint(spec, dsl):
    raw = json.dumps(
        {"mechanism_spec": spec or {}, "dsl": dsl or {}},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
                                 windtalker_tag=None, enable_multi_symbol_matrix=False,
                                 formal_submission=False):
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
        "enable_multi_symbol_matrix": bool(enable_multi_symbol_matrix),
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

    spec = dict(spec_pack["mechanism_spec"])
    if isinstance(spec_pack.get("research_contract"), dict):
        spec.setdefault("research_contract", spec_pack.get("research_contract"))
    spec_pack["mechanism_spec"] = spec
    # Bind formal recipe identity in the Step-A orchestration scope.  The
    # compiler has its own local bindings; repair and final-handoff checks must
    # not rely on those locals or the pipeline will either NameError or skip the
    # identity lock after the initial compile.
    contract = _compiler_contract(
        spec_pack, spec, spec_pack.get("meta") or {},
    )
    admitted_recipe = spec_pack.get("admitted_recipe_lock")
    if not isinstance(admitted_recipe, dict) or not admitted_recipe:
        admitted_recipe = None
    submission_authority = _formal_submission_authority(
        formal_submission, contract, admitted_recipe,
    )
    task["formal_submission_authority"] = submission_authority
    if formal_submission is True and not submission_authority.get("authorized"):
        task["stage"] = "archived"
        task["error"] = "formal_recipe_provenance_missing"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(
            task, stage="formal_submission_authority",
            failed_tests=["formal_recipe_provenance"],
            reason=";".join(submission_authority.get("reasons") or []),
            verdict="formal_submission_denied",
            drift=True,
        )
        dual.save_task(task)
        store.save_task_meta(task)
        return {
            "ok": False, "task_id": tid, "stage": "archived",
            "reason": "formal_recipe_provenance_missing",
            "authority_errors": submission_authority.get("reasons") or [],
            "submission_created": False, "review_submitted": False,
        }
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
    fidelity = build_fidelity_diff(spec, book.get("dsl"), round_i=0, repair_type=None)
    # This helper only preserves or downgrades the independent fidelity result.
    # Entry/exit/stop presence is not evidence sufficient to upgrade a failure.
    fidelity = _compiler_finalize_fidelity(fidelity, book, stmt)
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
    try:
        from . import manufacture_batch_policy as mfg
        slim_only = bool(formal_submission) and bool(mfg.slim_multiai_review_only())
    except Exception:
        slim_only = False
    if (not g1["pass"]) and (not slim_only):
        task["stage"] = "archived"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(task, stage="gate1", failed_tests=["fidelity"],
                        reason=("fidelity/gate1 fail; compiler_errors=%s" %
                                (fidelity.get("compiler_fidelity_errors") or []))[:1000],
                        verdict="code_fidelity_fail",
                        is_eng=True)
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "reason": "gate1_fail",
                "fidelity_errors": fidelity.get("compiler_fidelity_errors") or [],
                "gate_results": task["gate_results"]}
    if slim_only and not g1.get("pass"):
        g1 = dict(g1)
        g1["soft_pass"] = "slim_multiai_skip_deterministic_gate1"
        g1["pass"] = True
        task["gates"][-1] = g1

    # ---- Gate2+3 backtest / walk-forward (+ Phase-3 funnel L0→L1→L2→L3) ----
    task["stage"] = "gate2_gate3_backtest_wf"
    import auto_trade_strategy_dsl as dsl_mod
    from .funnel_l0_density import run_l0_density
    from .funnel_l1_micro_screen import run_micro_screen, run_micro_screen_matrix
    from .funnel_l2_pareto import evaluate_l2_survivor
    from .funnel_l3_null_hypothesis import evaluate_null_hypothesis
    from . import incubation_soft_gate as soft_gate
    try:
        from auto_driver import memory_guard as mem_guard
    except Exception:
        mem_guard = None
    try:
        definition = dsl_mod.validate_strategy(book.get("dsl") or {})
    except Exception as exc:
        task["stage"] = "archived"
        _archive_step_a(task, stage="validate", failed_tests=["engineering"],
                        reason=str(exc), verdict="dsl_validate_fail", is_eng=True)
        dual.save_task(task)
        return {"ok": False, "task_id": tid, "reason": "validate_fail"}

    # ---- Slim multi-AI-only formal review (skip all deterministic gates) ----
    # slim_only already resolved above Gate1.
    if slim_only:
        import auto_trade_ai_consensus as ai_cons
        import auto_trade_human_confirm_pipeline as pipeline
        task["stage"] = "slim_multiai_average_review"
        task["review_mode"] = "slim_multiai_average_only"
        sym = book.get("symbol") or focus.get("symbol")
        tf = book.get("timeframe") or focus.get("timeframe")
        try:
            frame = dual._frame(sym, tf)
            bt = dsl_mod.backtest_dsl(frame, definition, stop_loss_pct=0.005)
        except Exception as exc:
            task["stage"] = "archived"
            dual.save_task(task)
            return {"ok": False, "task_id": tid, "reason": "slim_backtest_fail",
                    "error": str(exc)[:240]}
        trades = bt.get("trades") if isinstance(bt, dict) else bt
        metrics = bt.get("metrics") if isinstance(bt, dict) else {}
        n_trades = len(trades or [])
        win_pack = mfg.levered_win_only_mean_pct(trades=trades)
        span_days = None
        try:
            span_days = ai_cons.resolve_backtest_observation_span_days(
                {"symbol": sym, "timeframe": tf},
                {"trades": n_trades, "trades_list": trades},
                trades=trades,
            )
        except Exception:
            span_days = None
        # P0: independent reference parity before any four-AI call.
        try:
            from dual_engine_workflow_v2 import review_gate as rgate
            prod_metrics = {
                "n": n_trades,
                "mean_win_only_pct": win_pack.get("mean_win_only_pct"),
                "average_profitable_trade_return": win_pack.get("mean_win_only_ratio"),
                "win_rate": (metrics or {}).get("win_rate"),
                "leverage": mfg.EXECUTION_LEVERAGE,
                "stop_distance": mfg.PROTECTIVE_STOP_PRICE_PCT,
            }
            if span_days:
                prod_metrics["weekly_opens"] = (
                    (n_trades * 7.0 / float(span_days)) if span_days else None
                )
            handoff_token = (
                (pack.get("creation_blueprint") or {}).get("handoff_token")
                or ((task.get("meta") or {}).get("handoff_token"))
            )
            # Parity is always required when trades exist.
            parity = rgate.validate_review_metrics_parity(
                trades,
                production_metrics=prod_metrics,
            )
            task["metric_parity"] = {
                "production_metrics": parity.get("production_metrics"),
                "reference_metrics": parity.get("reference_metrics"),
                "absolute_diff": parity.get("absolute_diff"),
                "relative_diff": parity.get("relative_diff"),
                "metric_parity_passed": parity.get("metric_parity_passed"),
                "failed_keys": parity.get("failed_keys"),
                "pnl_ratio_reference": parity.get("pnl_ratio_reference"),
            }
            if not parity.get("metric_parity_passed"):
                task["stage"] = "archived"
                dual.save_task(task)
                return {
                    "ok": False,
                    "task_id": tid,
                    "reason": "REVIEW_METRIC_PARITY_FAILURE",
                    "metric_parity": task["metric_parity"],
                    "message_zh": "生产指标与独立真值计算器不一致，禁止提交四AI复核。",
                }
            if handoff_token:
                tok = rgate.require_handoff_token(
                    handoff_token,
                    recipe_id=((pack.get("admitted_recipe_lock") or {}).get("recipe_id")),
                    metrics=(pack.get("creation_blueprint") or {}).get("select_metrics"),
                )
                task["handoff_token_check"] = tok
                if not tok.get("ok"):
                    task["stage"] = "archived"
                    dual.save_task(task)
                    return {
                        "ok": False,
                        "task_id": tid,
                        "reason": "HANDOFF_TOKEN_INVALID",
                        "handoff_token_check": tok,
                        "message_zh": "缺少或无效 handoff token，禁止提交四AI复核。",
                    }
            elif slim_only:
                # Manufacture / slim path must always carry a handoff token.
                task["stage"] = "archived"
                dual.save_task(task)
                return {
                    "ok": False,
                    "task_id": tid,
                    "reason": "HANDOFF_TOKEN_INVALID",
                    "message_zh": "精简四AI复核缺少 handoff token，禁止提交。",
                }
        except Exception as exc:
            task["metric_parity_error"] = str(exc)[:240]
            task["stage"] = "archived"
            dual.save_task(task)
            return {
                "ok": False,
                "task_id": tid,
                "reason": "REVIEW_METRIC_PARITY_FAILURE",
                "error": str(exc)[:240],
                "message_zh": "指标对账执行失败，禁止提交四AI复核。",
            }
        evidence = {
            "symbol": sym,
            "timeframe": tf,
            "trades": n_trades,
            "trades_list": trades,
            "metrics": metrics,
            "span_days": span_days,
            "observation_days": span_days,
            "weekly_opens_require_2y": False,
            "slim_multiai_only": True,
            "protective_stop_price_pct": mfg.PROTECTIVE_STOP_PRICE_PCT,
            "safety_metrics": {
                "trades": n_trades,
                "span_days": span_days,
                "observation_days": span_days,
                "trades_list": trades,
                "mean_net": (metrics or {}).get("mean_net"),
                "win_rate": (metrics or {}).get("win_rate_pct") or (metrics or {}).get("win_rate"),
                "mean_net_win_only": win_pack.get("mean_win_only_ratio"),
                "mean_net_win_only_pct": win_pack.get("mean_win_only_pct"),
                "win_only_audit": win_pack,
                "leverage": mfg.EXECUTION_LEVERAGE,
                "unit_note": "mean_net_win_only_pct is percentage points after leverage+fees; do not ×20 again",
            },
        }
        candidate = {
            "symbol": sym,
            "timeframe": tf,
            "direction": book.get("direction") or "long",
            "key": definition.get("key"),
            "dsl": definition,
            "metrics": metrics,
            "trades": trades,
        }
        theo = ai_cons.theoretical_review_all(candidate, evidence) or {}
        verification = ai_cons.validate_theoretical_review_result(theo)
        theo = dict(theo)
        theo["approved"] = bool(verification.get("ok") and theo.get("approved"))
        theo["review_verification"] = verification
        theo["slim_multiai_only"] = True
        theo["four_review_admission"] = {
            "schema": "qiyu_slim_multiai_admission_v1",
            "pass": bool(theo.get("approved")),
            "slim_multiai_only": True,
            "review_mode": "slim_multiai_average_only",
            "task_id": tid,
            "pipeline_handoff": "sole_pipeline_step_a_slim",
            "reviews": {
                "r4": {
                    "pass": bool(theo.get("approved")),
                    "gate": "slim_multiai_average",
                },
            },
        }
        task["slim_multiai_review"] = theo
        task["gates"] = [{
            "gate_id": "slim_multiai_average",
            "name": "精简多AI均值复核",
            "gate": "slim_multiai_average",
            "pass": bool(theo.get("approved")),
            "evidence": {
                "fail_reasons": theo.get("fail_reasons"),
                "avg_wr": theo.get("ai_theoretical_wr_avg"),
                "avg_weekly": theo.get("ai_theoretical_weekly_opens_avg"),
                "avg_mean_net": theo.get("ai_theoretical_mean_net_avg"),
            },
            "detail": {
                "fail_reasons": theo.get("fail_reasons"),
                "avg_wr": theo.get("ai_theoretical_wr_avg"),
                "avg_weekly": theo.get("ai_theoretical_weekly_opens_avg"),
                "avg_mean_net": theo.get("ai_theoretical_mean_net_avg"),
            },
        }]
        # Do not expand to Gate0–7; sole gate is slim multi-AI average.
        task["gate_results"] = {
            "schema": "qiyu_slim_multiai_gate_results_v1",
            "artifact": "gate_results",
            "task_id": tid,
            "review_mode": "slim_multiai_average_only",
            "gates": task["gates"],
            "all_gates_pass": bool(theo.get("approved")),
            "first_fail_gate": None if theo.get("approved") else "slim_multiai_average",
            "passed_count": 1 if theo.get("approved") else 0,
            "total_gates": 1,
            "production_mounted": False,
        }
        save_gate_results(tid, task["gate_results"])
        if not theo.get("approved"):
            task["stage"] = "archived"
            dual.save_task(task)
            store.save_task_meta(task)
            return {
                "ok": False,
                "task_id": tid,
                "reason": "slim_multiai_average_fail",
                "slim_multiai_review": theo,
                "gate_results": task["gate_results"],
                "review_mode": "slim_multiai_average_only",
            }
        push = pipeline.ingest_and_screen(
            {
                "dsl": definition,
                "symbol": sym,
                "timeframe": tf,
                "thesis": book.get("thesis"),
                "mechanism_spec": spec,
                "live_enabled": False,
                "auto_trade_eligible": False,
                "production_mounted": False,
            },
            source="dual_engine_step_a_slim_multiai",
            ai_review=theo,
            require_ai_review=True,
        )
        task["pending_push"] = {
            "ok": push.get("ok"),
            "key": push.get("key"),
            "reason": push.get("reason"),
        }
        task["human_confirm_state"] = {
            "pending_ok": bool(push.get("ok")),
            "awaiting_human": bool(push.get("ok")),
            "human_confirmed": False,
            "auto_open_mounted": False,
            "real_size_granted": False,
            "push": task["pending_push"],
            "slim_multiai_only": True,
        }
        task["stage"] = "awaiting_human" if push.get("ok") else "failed_push"
        dual.save_task(task)
        store.save_task_meta(task)
        return {
            "ok": bool(push.get("ok")),
            "task_id": tid,
            "reason": (
                "slim_multiai_average_pass"
                if push.get("ok") else (push.get("reason") or "slim_push_fail")
            ),
            "slim_multiai_review": theo,
            "gate_results": task["gate_results"],
            "review_mode": "slim_multiai_average_only",
            "stage": task["stage"],
            "pending": push,
        }

    # ---- Pretest quality: contract + sanity asserts BEFORE L0 (anti-屎上雕花) ----
    task["stage"] = "pretest_quality"
    try:
        from .pretest_quality import run_pretest_quality
        pretest_pack = {
            "meta": book if isinstance(book, dict) else {},
            "mechanism_spec": spec,
            "dsl": definition,
            "dsl_long": (prebuilt_spec_pack or {}).get("dsl_long") if isinstance(prebuilt_spec_pack, dict) else None,
            "dsl_short": (prebuilt_spec_pack or {}).get("dsl_short") if isinstance(prebuilt_spec_pack, dict) else None,
        }
        # Prefer embedded / family contract from prebuilt pack meta
        if isinstance(prebuilt_spec_pack, dict):
            pretest_pack["meta"] = dict(prebuilt_spec_pack.get("meta") or {})
            pretest_pack["meta"].setdefault("direction", book.get("direction") or "long")
            pretest_pack["meta"].setdefault("timeframe", book.get("timeframe"))
            if prebuilt_spec_pack.get("invariants_contract"):
                pretest_pack["invariants_contract"] = prebuilt_spec_pack.get("invariants_contract")
        pretest = run_pretest_quality(
            pretest_pack,
            direction=book.get("direction") or "long",
        )
    except Exception as exc:
        pretest = {
            "pass": False,
            "stage": "pretest_quality",
            "quality": "SHIT_TRANSLATION",
            "verdict": "RESET_REQUIRED",
            "reason": "pretest_exception:%s" % exc,
            "message_zh": "预检异常，fail-closed",
        }
    task["pretest_quality"] = pretest
    print(
        "[pipeline_step_a] pretest_quality pass=%s quality=%s verdict=%s reason=%s"
        % (pretest.get("pass"), pretest.get("quality"), pretest.get("verdict"), pretest.get("reason")),
        flush=True,
    )
    if not pretest.get("pass"):
        task["stage"] = "archived"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(
            task, stage="pretest_quality",
            failed_tests=["invariants_or_sanity"],
            reason=pretest.get("message_zh") or pretest.get("reason") or "pretest_fail",
            verdict="shit_translation_reset",
            is_eng=True,
        )
        dual.save_task(task)
        store.save_task_meta(task)
        return {
            "ok": False,
            "task_id": tid,
            "reason": "pretest_quality_fail",
            "pretest_quality": pretest,
            "gate_results": task["gate_results"],
        }

    sym = book.get("symbol") or focus.get("symbol")
    tf = book.get("timeframe") or focus.get("timeframe")
    matrix_syms_full = _resolve_matrix_symbols(
        spec=spec,
        meta=(prebuilt_spec_pack or {}).get("meta") if isinstance(prebuilt_spec_pack, dict) else {},
        primary=sym,
        enable=bool(enable_multi_symbol_matrix),
    )
    # RAM-adaptive subset: primary / BTC·ETH·SOL anchors / chunked / full
    if mem_guard is not None and enable_multi_symbol_matrix:
        matrix_syms, ram_budget = mem_guard.select_matrix_subset(
            matrix_syms_full, primary=sym, budget=mem_guard.matrix_budget(),
        )
    else:
        matrix_syms, ram_budget = list(matrix_syms_full), {"mode": "legacy", "max_symbols": len(matrix_syms_full)}
    task["matrix_eval"] = {
        "enabled": bool(enable_multi_symbol_matrix),
        "primary": sym,
        "symbols_full": list(matrix_syms_full),
        "symbols": list(matrix_syms),
        "n_symbols": len(matrix_syms),
        "n_symbols_full": len(matrix_syms_full),
        "ram_budget": ram_budget,
        "lightweight_funnel": True,
    }

    # Phase-3 L1: micro-screen on sampled slices BEFORE full-history BT
    task["stage"] = "funnel_l1_micro_screen"
    frame_for_funnel = None
    try:
        frame_for_funnel = dual._frame(sym, tf)
    except Exception:
        frame_for_funnel = None

    def _funnel_bt(frm, defn):
        return dsl_mod.backtest_dsl(frm, defn, stop_loss_pct=0.005)

    # ---- L0 density pre-check (ms) — kill AND-clog before any matrix IO ----
    task["stage"] = "funnel_l0_density"
    l0 = run_l0_density(definition=definition, frame=frame_for_funnel)
    task["phase3_funnel"] = {
        "l0_density": l0,
        "l1_micro_screen": None,
        "rejected_at": None,
        "fail_closed": True,
        "protective_sl_pct": 0.005,
        "ada_migrate": False,
        "auto_mount": False,
        "multi_symbol_matrix": bool(enable_multi_symbol_matrix),
        "matrix_symbols": list(matrix_syms) if enable_multi_symbol_matrix else [sym],
        "lightweight_funnel": True,
    }
    print(
        "[pipeline_step_a] 第一次复核·密度 pass=%s triggers=%s/%s wall=%.1fms"
        % (
            l0.get("pass"),
            (l0.get("metrics") or {}).get("triggers"),
            (l0.get("metrics") or {}).get("evaluated_bars"),
            float(l0.get("wall_time_ms") or 0),
        ),
        flush=True,
    )
    if not l0.get("pass"):
        if _legacy_soft_pass_enabled():
            _soft_skip_legacy(task, "funnel_l0_density", {
                "reject_reasons": list(l0.get("reject_reasons") or []),
                "metrics": l0.get("metrics"),
            })
            task["phase3_funnel"]["l0_advisory_fail"] = True
            task["phase3_funnel"]["reject_reasons_advisory"] = list(
                l0.get("reject_reasons") or []
            )
        else:
            task["stage"] = "archived"
            task["phase3_funnel"]["rejected_at"] = "funnel_l0_density"
            task["phase3_funnel"]["reject_reasons"] = list(l0.get("reject_reasons") or [])
            task["gate_results"] = assemble_gate_results(task["gates"], tid)
            save_gate_results(tid, task["gate_results"])
            _archive_step_a(
                task, stage="funnel_l0_density",
                failed_tests=["density_precheck"],
                reason="第一次复核·密度拒绝: %s" % ",".join(l0.get("reject_reasons") or ["l0_fail"]),
                verdict="funnel_l0_cull",
                is_mech_absent=False,
            )
            record_pipeline_rejection(
                task_id=tid, stage="funnel_l0_density",
                failed_tests=["density_precheck"],
                reject_reasons=l0.get("reject_reasons") or ["REJECT_TOO_RARE"],
                dsl=book.get("dsl"), symbol=sym, timeframe=tf,
            )
            dual.save_task(task)
            store.save_task_meta(task)
            return {
                "ok": False, "task_id": tid, "reason": "funnel_l0_fail",
                "phase3_funnel": task["phase3_funnel"],
                "gate_results": task["gate_results"],
                "matrix_eval": task.get("matrix_eval"),
            }

    l1_seed = hash(tid) % (2 ** 31) if tid else 42
    task["stage"] = "funnel_l1_micro_screen"
    if enable_multi_symbol_matrix and len(matrix_syms) > 1:
        # Stage A: anchor L1 (BTC/ETH/SOL ∩ budget) — never jump to 38 cold
        if mem_guard is not None:
            anchor_syms = mem_guard.resolve_anchor_symbols(matrix_syms_full, primary=sym)
        else:
            anchor_syms = list(matrix_syms[:3])
        # Respect RAM budget cap
        max_n = int((ram_budget or {}).get("max_symbols") or len(matrix_syms))
        stage_syms = list(anchor_syms)[:max(1, min(3, max_n))]
        # If budget allows more than anchors and L0 passed, expand after anchors pass
        expand_after_anchor = (
            bool(enable_multi_symbol_matrix)
            and max_n > len(stage_syms)
            and (ram_budget or {}).get("mode") in ("chunked", "full")
        )
        matrix_frames, frame_errs = _load_matrix_frames(stage_syms, tf, dual)
        print(
            "[pipeline_step_a] 第二次复核·锚点 n_syms=%d frames=%d primary=%s mode=%s"
            % (len(stage_syms), len(matrix_frames), sym, (ram_budget or {}).get("mode")),
            flush=True,
        )
        l1 = run_micro_screen_matrix(
            definition=definition,
            frames_by_symbol=matrix_frames,
            backtest_fn=_funnel_bt if matrix_frames else None,
            seed=l1_seed,
        )
        l1["anchor_symbols"] = list(stage_syms)
        if frame_errs:
            sm = dict(l1.get("sample_meta") or {})
            sm["frame_errors"] = frame_errs[:20]
            l1["sample_meta"] = sm
        # Expand to budgeted matrix only if anchors passed
        if l1.get("pass") and expand_after_anchor:
            if mem_guard is not None:
                mem_guard.ensure_headroom(min_avail_mb=160, label="pre_matrix_expand")
            expand_syms = list(matrix_syms)
            # drop already-tested anchors from reload set? keep full subset for pool consistency
            matrix_frames2, frame_errs2 = _load_matrix_frames(expand_syms, tf, dual)
            print(
                "[pipeline_step_a] 第二次复核·扩展 n_syms=%d frames=%d"
                % (len(expand_syms), len(matrix_frames2)),
                flush=True,
            )
            l1_exp = run_micro_screen_matrix(
                definition=definition,
                frames_by_symbol=matrix_frames2,
                backtest_fn=_funnel_bt if matrix_frames2 else None,
                seed=l1_seed + 17,
            )
            l1_exp["anchor_symbols"] = list(stage_syms)
            l1_exp["expanded_from_anchor"] = True
            if frame_errs2:
                sm = dict(l1_exp.get("sample_meta") or {})
                sm["frame_errors"] = frame_errs2[:20]
                l1_exp["sample_meta"] = sm
            l1 = l1_exp
            matrix_syms = list(expand_syms)
            task["matrix_eval"]["symbols"] = list(matrix_syms)
            task["matrix_eval"]["n_symbols"] = len(matrix_syms)
        # free frames refs
        matrix_frames = None
        try:
            import gc
            gc.collect()
        except Exception:
            pass
    else:
        l1 = run_micro_screen(
            definition=definition,
            frame=frame_for_funnel,
            backtest_fn=_funnel_bt if frame_for_funnel is not None else None,
            seed=l1_seed,
        )
    task["phase3_funnel"]["l1_micro_screen"] = l1
    task["phase3_funnel"]["matrix_symbols"] = list(matrix_syms) if enable_multi_symbol_matrix else [sym]
    if not l1.get("pass"):
        if _legacy_soft_pass_enabled():
            _soft_skip_legacy(task, "funnel_l1_micro_screen", {
                "reject_reasons": list(l1.get("reject_reasons") or []),
                "metrics": l1.get("metrics"),
            })
            task["phase3_funnel"]["l1_advisory_fail"] = True
            task["phase3_funnel"]["reject_reasons_advisory"] = list(
                task["phase3_funnel"].get("reject_reasons_advisory") or []
            ) + list(l1.get("reject_reasons") or [])
        else:
            task["stage"] = "archived"
            task["phase3_funnel"]["rejected_at"] = "funnel_l1_micro_screen"
            task["phase3_funnel"]["reject_reasons"] = list(l1.get("reject_reasons") or [])
            task["gate_results"] = assemble_gate_results(task["gates"], tid)
            save_gate_results(tid, task["gate_results"])
            _archive_step_a(
                task, stage="funnel_l1_micro_screen",
                failed_tests=["micro_screen"],
                reason="第二次复核拒绝: %s" % ",".join(
                    l1.get("reject_reasons") or ["l1_fail"]),
                verdict="funnel_l1_cull",
                # Sample-slice cull is NOT proof of mechanism absence — do not
                # family-block via failure KB (would poison later calibrated retries).
                is_mech_absent=False,
            )
            record_pipeline_rejection(
                task_id=tid, stage="funnel_l1_micro_screen",
                failed_tests=["micro_screen"],
                reject_reasons=l1.get("reject_reasons") or ["l1_fail"],
                dsl=book.get("dsl"), symbol=sym, timeframe=tf,
            )
            dual.save_task(task)
            store.save_task_meta(task)
            return {
                "ok": False, "task_id": tid, "reason": "funnel_l1_fail",
                "phase3_funnel": task["phase3_funnel"],
                "gate_results": task["gate_results"],
                "matrix_eval": task.get("matrix_eval"),
            }

    # Full-history BT + Phase-3 WF windows (Calmar≥1.0 + positive expectancy)
    # WF stays on primary seed; Gate2 fitness uses pooled matrix trades when enabled.
    task["stage"] = "gate2_gate3_backtest_wf"
    wf = _walk_forward_detail(definition, sym, tf, folds=10)
    base_m = wf.get("base_metrics") or {}
    trades = wf.get("trades") or []
    # Soft cull: if primary Calmar < 0.5, skip expensive matrix pool (formal Gate2 still runs on primary)
    skip_pool = soft_gate.should_skip_full_matrix(base_m)
    soft_snap = soft_gate.soft_progress(base_m)
    task["matrix_eval"]["soft_incubation"] = soft_snap
    task["matrix_eval"]["skip_full_matrix_pool"] = bool(skip_pool)
    if enable_multi_symbol_matrix and len(matrix_syms) > 1 and not skip_pool:
        print(
            "[pipeline_step_a] 第三次复核·矩阵池 n_syms=%d primary=%s mode=%s"
            % (len(matrix_syms), sym, (ram_budget or {}).get("mode")),
            flush=True,
        )
        pooled = _matrix_pool_backtest(definition, matrix_syms, tf, dual)
        task["matrix_eval"]["gate2_pool"] = {
            "n_trades": pooled.get("n_trades"),
            "per_symbol": pooled.get("per_symbol"),
            "primary_error": pooled.get("primary_error"),
        }
        if pooled.get("trades"):
            trades = pooled["trades"]
            base_m = pooled.get("base_metrics") or dual._metrics_from_trades(trades)
            wf = dict(wf)
            wf["base_metrics"] = base_m
            wf["trades"] = trades
            wf["matrix_pooled"] = True
    elif skip_pool:
        print(
            "[pipeline_step_a] 跳过第三次复核全矩阵池（主标的 calmar soft-kill）；仅主标的跑抗离群",
            flush=True,
        )
        task["matrix_eval"]["gate2_pool"] = {
            "skipped": True,
            "reason": "primary_calmar_lt_%.2f" % soft_gate.ANCHOR_CALMAR_KILL,
            "soft_incubation": soft_snap,
        }

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
        return dsl_mod.backtest_dsl(frm, defn, stop_loss_pct=0.005)

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

    # Phase-4 incubator only when L1–L3 all pass (fail-closed overfit cull)
    phase3_ok = (
        bool(l1.get("pass")) and bool(l2.get("pass")) and bool(l3.get("pass"))
    )
    incubator = None
    if phase3_ok:
        from .incubator import run_incubator, metrics_from_trades as _inc_metrics
        incub_base = dict(base_m)
        incub_extra = _inc_metrics(trades)
        for _k in ("calmar", "payoff_ratio", "classic_expectancy", "mean_mae", "trades"):
            if incub_base.get(_k) is None:
                incub_base[_k] = incub_extra.get(_k)
        incubator = run_incubator(
            definition=definition,
            target_symbol=sym,
            timeframe=tf,
            baseline_trades=trades,
            baseline_metrics=incub_base,
            baseline_frame=frame_for_funnel,
            backtest_fn=_funnel_bt,
            stop_loss_pct=0.005,
        )
        task["phase4_incubator"] = incubator
        task["phase3_funnel"]["phase4_incubator"] = {
            "pass": incubator.get("pass"),
            "cross_asset_score": incubator.get("cross_asset_score"),
            "rejected_at": incubator.get("rejected_at"),
        }
        if not incubator.get("pass"):
            if _legacy_soft_pass_enabled():
                _soft_skip_legacy(task, "phase4_incubator", {
                    "reject_reasons": list(incubator.get("reject_reasons") or []),
                    "cross_asset_score": incubator.get("cross_asset_score"),
                })
            else:
                task["stage"] = "archived"
                task["gate_results"] = assemble_gate_results(task["gates"], tid)
                save_gate_results(tid, task["gate_results"])
                _archive_step_a(
                    task, stage="phase4_incubator",
                    failed_tests=["incubator"],
                    reason="Phase4 incubator reject: %s" % ",".join(
                        incubator.get("reject_reasons") or ["incubator_fail"]),
                    verdict="phase4_incubator_reject",
                    is_mech_absent=True,
                )
                record_pipeline_rejection(
                    task_id=tid, stage="phase4_incubator",
                    failed_tests=["incubator"],
                    reject_reasons=incubator.get("reject_reasons") or ["incubator_fail"],
                    dsl=definition, symbol=sym, timeframe=tf,
                )
                dual.save_task(task)
                store.save_task_meta(task)
                return {
                    "ok": False, "task_id": tid, "reason": "phase4_incubator_fail",
                    "phase4_incubator": incubator,
                    "phase3_funnel": task["phase3_funnel"],
                    "gate_results": task["gate_results"],
                    "production_mounted": False,
                }

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
    # Reconstructed admission: Gate2/3 fitness is advisory — do NOT burn
    # viability-archive repair rounds that would reject ADA-T3-class strategies.
    if need_repair and _legacy_soft_pass_enabled():
        _soft_skip_legacy(task, "gate2_3_repair_loop", {
            "g2_pass": bool(g2.get("pass")),
            "g3_pass": bool(g3.get("pass")),
            "note": "skip_viability_archive_under_admission_v2",
        })
        need_repair = False
    archived_by_repair = False
    if need_repair:
        for ri, rtype in enumerate(REPAIR_ROUND_TYPES):
            task["stage"] = "repair_%s" % rtype
            before_fp = _implementation_fingerprint(spec, book.get("dsl"))
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
            repair_audit_pack = {
                "mechanism_spec": spec,
                "research_contract": _compiler_contract(spec, spec, {}),
                "meta": {
                    "symbol": book.get("symbol"),
                    "timeframe": book.get("timeframe"),
                    "direction": book.get("direction"),
                    "title": book.get("title"),
                    "thesis": book.get("thesis"),
                },
                "dsl": book.get("dsl"),
            }
            if admitted_recipe is not None:
                repair_audit_pack["admitted_recipe_lock"] = admitted_recipe
            repair_contract_audit = codex_implement_from_spec(repair_audit_pack)
            if not repair_contract_audit.get("ok"):
                fidelity["pass"] = False
                fidelity["failure_class"] = "implementation_failure"
                fidelity["repair_contract_errors"] = (
                    repair_contract_audit.get("fidelity_errors") or []
                )
            else:
                book["fidelity_errors"] = []
                book["fidelity_evidence"] = repair_contract_audit.get("fidelity_evidence") or {}
                fidelity = _compiler_finalize_fidelity(fidelity, book, stmt)
            save_fidelity_diff(tid, fidelity)
            prior_fidelity = fidelity
            if not fidelity.get("pass"):
                task["stage"] = "archived"
                task["fidelity_diff"] = fidelity
                _archive_step_a(
                    task,
                    stage="repair_contract_reaudit",
                    failed_tests=["fidelity", "research_contract"],
                    reason=(
                        "repair changed locked mechanism semantics: %s"
                        % (fidelity.get("repair_contract_errors") or fidelity.get("notes") or [])
                    )[:1000],
                    verdict="repair_contract_fidelity_fail",
                    drift=True,
                )
                dual.save_task(task)
                archived_by_repair = True
                break
            after_fp = _implementation_fingerprint(spec, book.get("dsl"))
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

    # Defense in depth: no later gate may evaluate or submit a repaired DSL
    # unless it is still the exact event admitted by discovery.  This catches
    # future mutation sites even if they forget to invoke the per-round audit.
    if admitted_recipe is not None:
        final_recipe_identity = validate_dsl_against_admitted_recipe(
            definition, admitted_recipe, contract,
        )
        task["admitted_recipe_final_identity"] = final_recipe_identity
        _save_artifact(tid, "admitted_recipe_final_identity", final_recipe_identity)
        if not final_recipe_identity.get("ok"):
            task["stage"] = "archived"
            task["gate_results"] = assemble_gate_results(task["gates"], tid)
            save_gate_results(tid, task["gate_results"])
            _archive_step_a(
                task,
                stage="admitted_recipe_final_identity",
                failed_tests=["admitted_recipe_identity"],
                reason=("final executable drifted from admitted recipe: %s" %
                        (final_recipe_identity.get("errors") or []))[:1000],
                verdict="admitted_recipe_identity_drift",
                drift=True,
            )
            dual.save_task(task)
            store.save_task_meta(task)
            return {
                "ok": False,
                "task_id": tid,
                "reason": "admitted_recipe_identity_drift",
                "identity_errors": final_recipe_identity.get("errors") or [],
                "gate_results": task["gate_results"],
            }

    if not g2["pass"] or not g3["pass"]:
        if _legacy_soft_pass_enabled():
            from .review_admission_v2 import (
                review2_single_symbol_stability,
                review3_matrix_outlier,
            )
            ev = _evidence_metrics_from_bt(base_m, trades)
            r2 = review2_single_symbol_stability(metrics=ev, trades=trades)
            r3 = review3_matrix_outlier(
                gate2_fitness=(g2.get("evidence") or {}).get("fitness")
                if isinstance(g2, dict) else None,
                soft_pass=True,
            )
            task.setdefault("admission_v2", {})
            task["admission_v2"]["review2"] = r2
            task["admission_v2"]["review3"] = r3
            task["admission_v2"]["legacy_gate2"] = {
                "pass": bool(g2.get("pass")),
                "evidence": g2.get("evidence"),
            }
            task["admission_v2"]["legacy_gate3"] = {
                "pass": bool(g3.get("pass")),
                "evidence": g3.get("evidence") if isinstance(g3, dict) else {},
            }
            if r2.get("pass"):
                # R2 hard-pass; R3 soft-pass under ADA-T3 → continue (no hard block)
                _soft_skip_legacy(task, "gate2_3", {
                    "g2_pass": bool(g2.get("pass")),
                    "g3_pass": bool(g3.get("pass")),
                    "review2_pass": True,
                    "review3_soft_passed": bool(r3.get("soft_passed")),
                    "review2_metrics": r2.get("metrics"),
                })
            else:
                task["stage"] = "archived"
                task["gate_results"] = assemble_gate_results(task["gates"], tid)
                save_gate_results(tid, task["gate_results"])
                _archive_step_a(
                    task, stage="review2_single_symbol",
                    failed_tests=list(r2.get("reject_reasons") or ["stability_fail"]),
                    reason="admission_v2 review2 fail: %s" % ",".join(
                        r2.get("reject_reasons") or []),
                    verdict="review2_evidence_fail",
                )
                dual.save_task(task)
                store.save_task_meta(task)
                return {
                    "ok": False, "task_id": tid,
                    "reason": "review2_evidence_fail",
                    "admission_v2": task.get("admission_v2"),
                    "gate_results": task["gate_results"],
                }
        else:
            task["stage"] = "archived"
            task["gate_results"] = assemble_gate_results(task["gates"], tid)
            save_gate_results(tid, task["gate_results"])
            _archive_step_a(task, stage="gate2_3", failed_tests=["backtest_or_wf"],
                            reason="第三次复核仍未通过", verdict="backtest_wf_fail",
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
        if _legacy_soft_pass_enabled():
            _soft_skip_legacy(task, "gate4_split_tests", {
                "hard_fail_ids": split_summary.get("gate4_hard_fail_ids"),
            })
        else:
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
        if _legacy_soft_pass_enabled():
            _soft_skip_legacy(task, "gate5_mc_friction", {"friction": fr, "mc": mc_summary})
        else:
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
        if _legacy_soft_pass_enabled():
            _soft_skip_legacy(task, "gate6_multi_ai", {
                "reviews": {
                    k: bool((v or {}).get("pass")) for k, v in (reviews or {}).items()
                },
            })
        else:
            task["stage"] = "archived"
            task["gate_results"] = assemble_gate_results(task["gates"], tid)
            save_gate_results(tid, task["gate_results"])
            _archive_step_a(task, stage="gate6", failed_tests=["multi_ai"],
                            reason="one or more separate AI reviews failed",
                            verdict="multi_ai_review_fail",
                            counterexamples=(reviews.get("deepseek_logic") or {}).get("counterexamples") or [])
            record_pipeline_rejection(
                task_id=tid, stage="gate6_multi_ai",
                failed_tests=["multi_ai_review"],
                reject_reasons=["gate6_ai_review_fail"],
                dsl=book.get("dsl"), symbol=sym, timeframe=tf,
            )
            dual.save_task(task)
            store.save_task_meta(task)
            return {"ok": False, "task_id": tid, "reason": "gate6_fail",
                    "gate_results": task["gate_results"]}

    # ---- Phase 5: 3-party AI unanimous consensus ----
    task["stage"] = "phase5_3party_consensus"
    from .formal_4d import run_phase5_consensus
    p5_evidence = {
        "base_metrics": base_m,
        "walk_forward": task.get("walk_forward"),
        "split_scores": task.get("split_scores"),
        "phase3_funnel": {
            "l1_pass": bool(l1.get("pass")),
            "l2_pass": bool(l2.get("pass")),
            "l3_pass": bool(l3.get("pass")),
        },
        "phase4_incubator": {
            "pass": bool((incubator or {}).get("pass")),
            "cross_asset_score": (incubator or {}).get("cross_asset_score"),
        },
        "gate6_reviews": {
            k: {kk: vv for kk, vv in v.items() if kk != "raw"}
            for k, v in reviews.items()
        },
    }
    p5_candidate = {
        "dsl": definition,
        "mechanism_statement": stmt,
        "mechanism_spec_summary": {
            "mechanism_family": spec.get("mechanism_family"),
            "market_inefficiency": spec.get("market_inefficiency"),
            "entry_logic": spec.get("entry_logic"),
        },
    }
    p5_result = run_phase5_consensus(p5_candidate, p5_evidence)
    task["phase5_consensus"] = {
        "approved": p5_result.get("approved"),
        "fatal_any": p5_result.get("fatal_any"),
        "fail_reasons": p5_result.get("fail_reasons"),
        "policy": p5_result.get("policy"),
        "reviews": [
            {k: v for k, v in r.items() if k != "raw"}
            for r in (p5_result.get("reviews") or [])
        ],
    }
    _save_artifact(tid, "phase5_consensus", task["phase5_consensus"])

    if not p5_result.get("approved"):
        if _legacy_soft_pass_enabled():
            _soft_skip_legacy(task, "phase5_3party_consensus", {
                "fail_reasons": p5_result.get("fail_reasons"),
                "fatal_any": p5_result.get("fatal_any"),
            })
        else:
            task["stage"] = "archived"
            task["gate_results"] = assemble_gate_results(task["gates"], tid)
            save_gate_results(tid, task["gate_results"])
            _archive_step_a(
                task, stage="phase5_3party_consensus",
                failed_tests=["phase5_unanimous"],
                reason="Phase5 3-party AI consensus rejected: %s" % ", ".join(
                    p5_result.get("fail_reasons") or ["consensus_fail"]),
                verdict="phase5_consensus_reject",
                counterexamples=[
                    "%s:%s" % (r.get("dimension"), r.get("reason") or "")
                    for r in (p5_result.get("reviews") or [])
                    if r.get("decision") != "APPROVE"
                ][:5],
            )
            record_pipeline_rejection(
                task_id=tid, stage="phase5_3party_consensus",
                failed_tests=["phase5_unanimous"],
                reject_reasons=p5_result.get("fail_reasons") or ["consensus_fail"],
                dsl=definition, symbol=sym, timeframe=tf,
            )
            dual.save_task(task)
            store.save_task_meta(task)
            return {
                "ok": False, "task_id": tid, "reason": "phase5_consensus_fail",
                "phase5_consensus": task["phase5_consensus"],
                "gate_results": task["gate_results"],
                "production_mounted": False,
            }

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

    # A public/direct Step-A call is a diagnostic execution.  Only the
    # discovery formal bridge can set ``formal_submission=True`` with a valid
    # immutable recipe; otherwise no review row or human-approval task exists.
    if not submission_authority.get("authorized"):
        task["stage"] = "diagnostic_completed"
        task["technical_completed"] = True
        task["review_submitted"] = False
        task["submission_created"] = False
        dual.save_task(task)
        store.save_task_meta(task)
        return {
            "ok": True, "task_id": tid, "stage": "diagnostic_completed",
            "technical_completed": True,
            "candidate_ready": False,
            "submission_created": False,
            "review_submitted": False,
            "reason": "diagnostic_only_no_formal_submission_authority",
            "gate_results": task.get("gate_results"),
            "production_mounted": False,
        }

    # ---- 第四次复核（三AI）+ 人工确认签发（no auto mount）----
    task["stage"] = "review4_three_ai"
    import auto_trade_human_confirm_pipeline as pipeline
    from .incubator import metrics_from_trades as _inc_metrics_g7
    from .review_admission_v2 import (
        evaluate_admission,
        review1_syntax_assert_density,
        review2_single_symbol_stability,
        review3_matrix_outlier,
        review4_three_ai,
        human_confirm_gate,
    )
    incub = task.get("phase4_incubator") or {}
    incub_m = incub.get("baseline_metrics") or _inc_metrics_g7(trades)
    ev_m = _evidence_metrics_from_bt(base_m, trades)
    l0_ev = (task.get("phase3_funnel") or {}).get("l0_density")
    g2_fit = (g2.get("evidence") or {}).get("fitness") if isinstance(g2, dict) else None
    task.setdefault("admission_v2", {})
    if not task["admission_v2"].get("review1"):
        task["admission_v2"]["review1"] = review1_syntax_assert_density(
            definition, lookahead_ok=True, l0=l0_ev,
            require_density=not _legacy_soft_pass_enabled(),
        )
    if not task["admission_v2"].get("review2"):
        task["admission_v2"]["review2"] = review2_single_symbol_stability(
            metrics=ev_m, trades=trades,
        )
    if not task["admission_v2"].get("review3"):
        task["admission_v2"]["review3"] = review3_matrix_outlier(
            gate2_fitness=g2_fit, soft_pass=_legacy_soft_pass_enabled(),
        )
    r1 = task["admission_v2"].get("review1") or {}
    r2 = task["admission_v2"].get("review2") or {}
    r3 = task["admission_v2"].get("review3") or {}
    if _admission_v2_enabled() and not r2.get("pass"):
        task["stage"] = "archived"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        dual.save_task(task)
        store.save_task_meta(task)
        return {
            "ok": False, "task_id": tid, "reason": "review2_evidence_fail",
            "admission_v2": task.get("admission_v2"),
            "gate_results": task["gate_results"],
            "production_mounted": False,
        }
    if _admission_v2_enabled() and (not r1.get("pass") or not r3.get("pass")):
        task["stage"] = "archived"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        dual.save_task(task)
        store.save_task_meta(task)
        return {
            "ok": False, "task_id": tid, "reason": "admission_v2_fail",
            "admission_v2": task.get("admission_v2"),
            "gate_results": task["gate_results"],
            "production_mounted": False,
        }
    # Canonical fourth review: real 3AI calls + near-2y weekly-frequency
    # discount gate.  split_scores can never substitute for votes.
    n_trades = int((base_m or {}).get("trades") or len(trades or []) or 0)
    span_days = None
    for key in ("observation_days", "span_days", "eval_lookback_days"):
        try:
            v = (base_m or {}).get(key)
            if v is not None:
                span_days = float(v)
                break
        except Exception:
            pass
    if span_days is None:
        bars = (base_m or {}).get("bars_used") or (base_m or {}).get("n_bars")
        bars_per_day = {"5m": 288.0, "15m": 96.0, "1h": 24.0, "4h": 6.0}.get(
            str(tf), 24.0)
        try:
            if bars is not None:
                span_days = float(bars) / float(bars_per_day)
        except Exception:
            span_days = None
    import auto_trade_ai_consensus as ai_cons
    bars_used = (base_m or {}).get("bars_used") or (base_m or {}).get("n_bars")
    # Do not seed claimed near-2y lookback; only physical bars/trades/frame.
    span_days, span_source = ai_cons.resolve_backtest_observation_span_days(
        definition,
        {
            "symbol": sym,
            "timeframe": tf,
            "trades": n_trades,
            "bars_used": bars_used,
            "safety_metrics": {
                "trades": n_trades,
                "bars_used": bars_used,
            },
        },
        trades=trades,
    )
    win_only_pct = None
    try:
        pnls = [float((t or {}).get("pnl_ratio") or 0.0) for t in (trades or [])]
        wins = [p for p in pnls if p > 0]
        if wins:
            win_only_pct = sum(wins) / float(len(wins)) * 100.0
    except Exception:
        pass
    try:
        theo_evidence = {
            "symbol": sym, "timeframe": tf,
            "strategy_key": (definition or {}).get("key"),
            "trades": n_trades, "span_days": span_days,
            "observation_days": span_days,
            "span_source": span_source,
            "bars_used": bars_used,
            "weekly_opens_require_2y": True,
            "trades_list": trades,
            "safety_metrics": {
                "trades": n_trades,
                "span_days": span_days,
                "observation_days": span_days,
                "bars_used": bars_used,
                "trades_list": trades,
                "mean_net": (base_m or {}).get("mean_net"),
                "win_rate": ((base_m or {}).get("win_rate_pct")
                             or (base_m or {}).get("win_rate")),
                "mean_net_win_only_pct": win_only_pct,
            },
        }
        theo = ai_cons.theoretical_review_all(definition, theo_evidence) or {}
        verification = ai_cons.validate_theoretical_review_result(theo)
        theo, verification = _apply_contract_frequency_gate(
            theo, verification, contract,
        )
        _save_artifact(tid, "theoretical_review_all", theo)
    except Exception as exc:
        theo = {"approved": False, "error": str(exc)[:240]}
        verification = {"ok": False, "reasons": [str(exc)[:200]]}
    ai_review = dict(theo)
    ai_review.update({
        "approved": bool(verification.get("ok")),
        "review_verification": verification,
        "split_scores": task["split_scores"],
        "step_a": True,
        "phase4_incubator": True,
        "admission_v2": bool(_admission_v2_enabled()),
        "calmar": incub.get("calmar") or incub_m.get("calmar") or base_m.get("calmar"),
        "payoff": incub.get("payoff_ratio") or incub_m.get("payoff_ratio") or base_m.get("payoff_ratio"),
        "cross_asset_score": incub.get("cross_asset_score"),
        "mean_mae": incub.get("mean_mae") or incub_m.get("mean_mae"),
    })
    r4 = review4_three_ai(ai_review=ai_review)
    task["admission_v2"]["review4"] = r4
    if _admission_v2_enabled() and not r4.get("pass"):
        task["stage"] = "archived"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        dual.save_task(task)
        store.save_task_meta(task)
        return {
            "ok": False, "task_id": tid, "reason": "review4_ai_fail",
            "admission_v2": task.get("admission_v2"),
            "gate_results": task["gate_results"],
            "production_mounted": False,
        }
    task["stage"] = "pending_human_confirm"
    ai_review["four_review_admission"] = {
        "schema": "qiyu_four_review_admission_v2",
        "profile": _admission_profile(),
        "pass": bool(
            r1.get("pass") and r2.get("pass") and r3.get("pass") and r4.get("pass")
        ),
        "reviews": {"r1": r1, "r2": r2, "r3": r3, "r4": r4},
        "task_id": tid,
        "pipeline_handoff": "sole_pipeline_step_a",
    }
    push = pipeline.ingest_and_screen(
        {"dsl": definition, "symbol": sym, "timeframe": tf,
         "thesis": book.get("thesis"), "mechanism_spec": spec,
         "mechanism_statement": stmt, "mechanism_fingerprint": fp_a,
         "live_enabled": False, "auto_trade_eligible": False,
         "production_mounted": False,
         "phase4_incubator": incub},
        source="dual_engine_step_a_admission_v2" if _admission_v2_enabled() else "dual_engine_step_a",
        ai_review=ai_review,
        require_ai_review=True,
    )
    hc = human_confirm_gate(pending_ok=bool(push.get("ok")), human_confirmed=False)
    task["admission_v2"]["human_confirm"] = hc
    adm = evaluate_admission(
        definition=definition,
        lookahead_ok=True,
        death_reason=None,
        metrics=ev_m,
        trades=trades,
        ai_review=ai_review,
        pending_ok=bool(push.get("ok")),
        l0=l0_ev,
        l1=l1,
        gate2_fitness=g2_fit,
    )
    task["admission_v2"].update({
        "final": adm,
        "review1": adm.get("reviews", {}).get("r1") or r1,
        "review2": adm.get("reviews", {}).get("r2") or r2,
        "review3": adm.get("reviews", {}).get("r3") or r3,
        "review4": adm.get("reviews", {}).get("r4") or r4,
        "human_confirm": adm.get("human_confirm") or hc,
    })
    human_state = {
        "pending_ok": bool(push.get("ok")),
        "awaiting_human": bool(push.get("ok")),
        "human_confirmed": False,  # never auto
        "auto_open_mounted": False,
        "real_size_granted": False,
        "push": {"ok": push.get("ok"), "key": push.get("key"), "reason": push.get("reason")},
        "admission_v2": True,
        "admission_profile": _admission_profile(),
    }
    task["human_confirm_state"] = human_state
    g7 = evaluate_gate7(human_state)
    task["gates"].append(g7)
    task["gate_results"] = assemble_gate_results(task["gates"], tid)
    # production_mounted stays false until CLI --confirm (B grade 30% only)
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
        "phase4_incubator_pass": bool(incub.get("pass")),
        "production_mounted": False,
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
        "phase4_incubator": incub,
    }


def start_creation_task_step_a(async_mode=True, symbol=None, timeframe=None,
                               exploration_mode="A", allow_horizontal_expand=False,
                               prebuilt_spec_pack=None, windtalker_tag=None,
                               enable_multi_symbol_matrix=False,
                               formal_submission=False):
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
                enable_multi_symbol_matrix=enable_multi_symbol_matrix,
                formal_submission=formal_submission,
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
        enable_multi_symbol_matrix=enable_multi_symbol_matrix,
        formal_submission=formal_submission,
    )


def job_snapshot_step_a():
    with _JOB_LOCK:
        return dict(_JOB)


def _load_strategy_json_pack(path):
    """Load a handcraft / Windtalker strategy JSON into prebuilt_spec_pack shape."""
    import json as _json
    from pathlib import Path as _Path

    p = _Path(path)
    if not p.is_file():
        raise FileNotFoundError("strategy_json not found: %s" % path)
    pack = _json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(pack, dict):
        raise ValueError("strategy_json must be a JSON object")
    if not isinstance(pack.get("mechanism_spec"), dict):
        raise ValueError("strategy_json missing mechanism_spec object")
    pack = dict(pack)
    pack.setdefault("ok", True)
    pack.setdefault("errors", [])
    meta = dict(pack.get("meta") or {})
    pack["meta"] = meta
    return pack


def dry_run_gate0_from_pack(pack):
    """Cheap local Gate0 + optional DSL validate. Does not touch production / AI."""
    from .mechanism_spec import normalize_mechanism_spec

    meta = pack.get("meta") or {}
    focus = {
        "symbol": meta.get("symbol"),
        "timeframe": meta.get("timeframe"),
    }
    ok, errors, cleaned = normalize_mechanism_spec(pack.get("mechanism_spec"), focus=focus)
    g0 = evaluate_gate0(cleaned, ok)
    dsl_report = None
    direction = str(meta.get("direction") or "long").lower()
    dsl = pack.get("dsl")
    if not isinstance(dsl, dict):
        dsl = pack.get("dsl_long" if direction == "long" else "dsl_short")
    if isinstance(dsl, dict):
        try:
            import auto_trade_strategy_dsl as dsl_mod
            dsl_mod.validate_strategy(dsl)
            dsl_report = {"ok": True, "key": dsl.get("key"), "direction": dsl.get("direction")}
        except Exception as exc:
            dsl_report = {"ok": False, "error": str(exc)}
    fam = cleaned.get("mechanism_family")
    kb_note = None
    try:
        blocked, why = path_is_blocked("family|%s" % fam, family=fam)
        kb_note = {"family": fam, "blocked": bool(blocked), "why": why, "source": "failure_kb"}
    except Exception as exc:
        # Local macOS / sandbox often cannot read /root WF_DIR — fall back to
        # the known prod blocked set from the forced KB read (2026-07-29).
        known_blocked = {
            "breakout_trap_reversal",
            "liquidity_failed_breakout_high",
            "mean_reversion_vwap_deviation",
            "time_structure_session_breakout",
            "trend_continuation_thrust_retest",
            "vol_regime_compression_release",
            "liquidity_failed_breakout_low",
            "mean_reversion_rsi_extreme",
            "mean_reversion_zscore_dislocation",
            "absorption_climax_reclaim",
            "selective_session_thrust",
            "confirmed_vol_release",
            "cascade_trap_proxy",
            "vacuum_fill_impulse",
            "compression_release_structural_breakout",
        }
        blocked = str(fam or "").strip() in known_blocked
        kb_note = {
            "family": fam,
            "blocked": blocked,
            "why": "static_prod_blocked_families_fallback",
            "kb_live_error": str(exc),
            "source": "static_fallback",
        }
    return {
        "ok": bool(
            ok and g0.get("pass")
            and (dsl_report is None or dsl_report.get("ok"))
            and not (kb_note or {}).get("blocked")
        ),
        "normalize_ok": ok,
        "normalize_errors": errors,
        "mechanism_spec_fields_filled": sum(
            1 for f in MECHANISM_SPEC_FIELDS
            if cleaned.get(f) not in (None, "", [])
        ),
        "mechanism_spec_fields_required": len(MECHANISM_SPEC_FIELDS),
        "gate0": g0,
        "dsl_validate": dsl_report,
        "kb_family_check": kb_note,
        "production_mounted": False,
        "note": "dry_run_gate0 only — no L1–L3, incubator, Gate6 AI, or mount",
    }


if __name__ == "__main__":
    import argparse
    import json as _json
    import sys as _sys

    parser = argparse.ArgumentParser(
        description=(
            "STEP A diagnostic creation CLI. --strategy_json runs diagnostics "
            "only; formal review submission requires the discovery bridge."
        )
    )
    parser.add_argument(
        "--strategy_json",
        default=None,
        help="Path to strategy candidate JSON (mechanism_spec + optional dsl/meta)",
    )
    parser.add_argument("--symbol", default=None, help="Override focus symbol")
    parser.add_argument("--timeframe", default=None, help="Override focus timeframe")
    parser.add_argument(
        "--direction",
        default=None,
        help="long|short — selects dsl_long/dsl_short when present",
    )
    parser.add_argument(
        "--exploration_mode",
        default="A",
        help="A/B/C/D or 1–4 (default A=new_mechanism)",
    )
    parser.add_argument(
        "--windtalker_tag",
        default=None,
        help="Optional tag recorded on the task",
    )
    parser.add_argument(
        "--dry_run_gate0",
        action="store_true",
        help="Validate mechanism_spec Gate0 (+ DSL if present); do not run full STEP A",
    )
    parser.add_argument(
        "--enable_multi_symbol_matrix",
        action="store_true",
        help=(
            "L1/Gate2 eval across mechanism_spec.suitable_symbols (aggregate fills); "
            "primary --symbol remains seed focus. Does not lower Gate2 floors."
        ),
    )
    parser.add_argument(
        "--async",
        dest="async_mode",
        action="store_true",
        help="Start STEP A in background thread (full run only)",
    )
    args = parser.parse_args()

    prebuilt = None
    if args.strategy_json:
        try:
            prebuilt = _load_strategy_json_pack(args.strategy_json)
        except Exception as exc:
            print(_json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            _sys.exit(2)
        meta = prebuilt.setdefault("meta", {})
        if args.direction:
            meta["direction"] = str(args.direction).lower()
            direction = meta["direction"]
            side_dsl = prebuilt.get("dsl_long" if direction == "long" else "dsl_short")
            if isinstance(side_dsl, dict):
                prebuilt["dsl"] = side_dsl
        if args.symbol:
            meta["symbol"] = args.symbol
        if args.timeframe:
            meta["timeframe"] = args.timeframe
        # Prefer meta focus when CLI overrides absent
        symbol = args.symbol or meta.get("symbol")
        timeframe = args.timeframe or meta.get("timeframe")
    else:
        symbol = args.symbol
        timeframe = args.timeframe

    if args.dry_run_gate0:
        if not prebuilt:
            print(_json.dumps({
                "ok": False,
                "error": "--dry_run_gate0 requires --strategy_json",
            }, ensure_ascii=False))
            _sys.exit(2)
        report = dry_run_gate0_from_pack(prebuilt)
        print(_json.dumps(report, ensure_ascii=False, indent=2, default=str))
        _sys.exit(0 if report.get("ok") else 1)

    tag = args.windtalker_tag
    if tag is None and prebuilt:
        tag = "strategy_json_cli"
    if args.async_mode:
        out = start_creation_task_step_a(
            async_mode=True,
            symbol=symbol,
            timeframe=timeframe,
            exploration_mode=args.exploration_mode,
            prebuilt_spec_pack=prebuilt,
            windtalker_tag=tag,
            enable_multi_symbol_matrix=bool(args.enable_multi_symbol_matrix),
        )
    else:
        out = run_creation_pipeline_step_a(
            symbol=symbol,
            timeframe=timeframe,
            exploration_mode=args.exploration_mode,
            prebuilt_spec_pack=prebuilt,
            windtalker_tag=tag,
            enable_multi_symbol_matrix=bool(args.enable_multi_symbol_matrix),
        )
    print(_json.dumps(out, ensure_ascii=False, indent=2, default=str))
    _sys.exit(0 if (out or {}).get("ok") else 1)
