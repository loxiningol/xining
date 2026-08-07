# -*- coding: utf-8 -*-
"""One capability boundary shared by discovery and formal compilation.

Research evidence may remain useful even when the current deterministic DSL
cannot reproduce it.  Such rows are diagnostics, never formal candidates.
"""
from __future__ import print_function

from . import research_contract as rcontract


RECIPE_SCHEMA = "qiyu_admitted_probe_recipe_v1"
RECIPE_SCHEMA_V2 = "qiyu_admitted_probe_recipe_v2"
FORMAL_EXECUTION_MAPPING = "next_bar_open"
EXIT_POLICY_MODE = "fixed_horizon_close_v1"
# Formally reproducible RSI feature take-profit used by live frost winners and
# by structured search for exhaustion/trend_pullback.  Not a soft gate bypass:
# research WR>50 must be measured under this exit when assembly targets it.
FEATURE_RSI_EXIT_MODE = "feature_rsi_tp_v1"
# Machine-exact barrier: first touch of fixed target% or protective stop,
# else horizon close. Used by Stage1 rhyme pin (fair RR ≈ 1).
BARRIER_PCT_EXIT_MODE = "intrabar_fixed_pct_target_v1"
ALLOWED_EXIT_POLICY_MODES = (
    EXIT_POLICY_MODE, FEATURE_RSI_EXIT_MODE, BARRIER_PCT_EXIT_MODE,
)
PROTECTIVE_STOP_MODE = "intrabar_fixed_pct_v1"
PROTECTIVE_STOP_PCT = rcontract.PRODUCTION_PROTECTIVE_STOP_PCT
EXECUTION_LEVERAGE = rcontract.PRODUCTION_EXECUTION_LEVERAGE
STATISTICAL_RETURN_BASIS = "full_size_leveraged_after_cost_v1"

# The factor name on the left is the discovery identity.  The value is the
# exact executable DSL feature; no fuzzy aliases or proxy substitution.
# Research estimators must be point-aligned with the formal frame builder
# before a mapping is added here.
GENERIC_FACTOR_TO_DSL = {
    "close_z_20": "z20",
    "volume_z": "vol_z20",
    "rsi_14": "rsi14",
    "ret_3": "ret_3",
    "trend_bias_50_200": "trend_bias_50_200",
    "donchian20_long_break": "donchian20_break_up",
    "donchian20_short_break": "donchian20_break_dn",
    "bullish_reclaim": "reclaim_strength",
    "reclaim_strength": "reclaim_strength",
    # Bollinger(20,2) — research names == formal DSL feature names
    "bb_lower_dist": "bb_lower_dist",
    "bb_upper_dist": "bb_upper_dist",
    "bb_mid_reclaim": "bb_mid_reclaim",
    "bb_width": "bb_width",
    "atr_pct_14": "atr14",
}
GENERIC_FACTOR_SEMANTICS = {
    "close_z_20": "close_z20_sample_std_v1",
    "volume_z": "volume_z20_sample_std_v1",
    "rsi_14": "rsi14_ewm_alpha_1_14_v1",
    "ret_3": "ret_3_close_to_close_v1",
    "trend_bias_50_200": "sma50_minus_sma200_over_close_v1",
    "donchian20_long_break": "close_over_prior_high20_minus_1_v1",
    "donchian20_short_break": "prior_low20_over_close_minus_1_v1",
    "bullish_reclaim": "reclaim_strength_close_location_wick_v1",
    "reclaim_strength": "reclaim_strength_close_location_wick_v1",
    "bb_lower_dist": "bb20_2_close_minus_lower_over_close_v1",
    "bb_upper_dist": "bb20_2_upper_minus_close_over_close_v1",
    "bb_mid_reclaim": "bb20_2_close_minus_mid_over_close_v1",
    "bb_width": "bb20_2_upper_minus_lower_over_mid_v1",
    "atr_pct_14": "atr14_over_close_v1",
}
GENERIC_EVENT_KINDS = ("mechanism_intersection", "mechanism_preserving", "ast_compiled")
ALLOWED_RECIPE_SCHEMAS = (
    "qiyu_admitted_probe_recipe_v1",
    "qiyu_admitted_probe_recipe_v2",
)


def _float_equal(left, right, tolerance=1e-12):
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return False


def formal_capability(event_kind, event_logic, terms, execution_mapping,
                      horizon_bars, contract=None, exit_policy=None,
                      protective_stop_policy=None,
                      protective_stop_evaluated=None,
                      execution_leverage=None,
                      statistical_return_basis=None,
                      event_ast=None,
                      event_ast_formal_ok=None):
    """Return reasons why a researched row cannot be reproduced formally."""
    reasons = []
    contract = contract if isinstance(contract, dict) else {}
    integrity = rcontract.verify_contract_integrity(contract)
    if not integrity.get("ok"):
        reasons.extend(integrity.get("reasons") or ["research_contract_invalid"])

    if str(execution_mapping or "") != FORMAL_EXECUTION_MAPPING:
        reasons.append("execution_mapping_not_formally_supported:%s" % (
            execution_mapping or "missing",
        ))
    try:
        horizon = int(horizon_bars)
        if horizon < 1 or horizon > 240:
            raise ValueError("outside")
    except (TypeError, ValueError):
        horizon = None
        reasons.append("holding_horizon_invalid")

    holding = contract.get("holding_contract") or {}
    allowed_horizons = []
    for value in holding.get("allowed_horizons_bars") or []:
        try:
            allowed_horizons.append(int(value))
        except (TypeError, ValueError):
            pass
    exact_horizon = holding.get("exact_horizon_bars")
    if horizon is not None and allowed_horizons and horizon not in allowed_horizons:
        reasons.append("holding_horizon_outside_contract")
    if exact_horizon is not None:
        try:
            if horizon != int(exact_horizon):
                reasons.append("holding_horizon_exact_contract_drift")
        except (TypeError, ValueError):
            reasons.append("holding_horizon_exact_contract_invalid")

    event_contract = contract.get("event_contract") or {}
    if event_contract.get("session_window") or event_contract.get("time_window"):
        reasons.append("session_window_not_researched")
    if event_contract.get("exit_conditions"):
        reasons.append("required_exit_conditions_not_researched")
    unsupported_clauses = [
        row.get("clause_id") for row in event_contract.get("clause_nxt") or []
        if isinstance(row, dict) and row.get("representation_status") != "machine_exact"
    ]
    if unsupported_clauses:
        reasons.append("required_clauses_not_machine_exact:%s" % ",".join(
            str(value) for value in unsupported_clauses if value
        ))

    terms = terms if isinstance(terms, list) else []
    kind = str(event_kind or "")
    logic = str(event_logic or "")
    required_entries = list(event_contract.get("entry_conditions") or [])
    if kind == "human_contract_exact":
        if logic != "ordered_joins" or not terms:
            reasons.append("exact_event_structure_invalid")
        if not required_entries:
            reasons.append("exact_event_contract_conditions_missing")
        for index, term in enumerate(terms):
            if not isinstance(term, dict) or not (term.get("factor") or term.get("feature")):
                reasons.append("exact_event_term_%d_invalid" % index)
            elif (term.get("op") or term.get("operator")) in (None, "") or "value" not in term:
                reasons.append("exact_event_term_%d_not_literal" % index)
    elif kind == "ast_compiled":
        if required_entries:
            reasons.append("generic_event_cannot_replace_exact_contract")
        ast = event_ast if isinstance(event_ast, dict) else None
        if ast is None:
            for term in terms:
                if isinstance(term, dict) and isinstance(term.get("event_ast"), dict):
                    ast = term.get("event_ast")
                    break
        formal_flag = event_ast_formal_ok
        if formal_flag is None:
            for term in terms:
                if isinstance(term, dict) and "event_ast_formal_ok" in term:
                    formal_flag = bool(term.get("event_ast_formal_ok"))
                    break
        try:
            from . import ast_compiler as ac
            if formal_flag is None and ast is not None:
                dsl_pack = ac.compile_ast_to_dsl(ast)
                formal_flag = bool(dsl_pack.get("formal_ok"))
                if not dsl_pack.get("ok"):
                    reasons.append("ast_dsl_compile_failed")
            if formal_flag is False:
                reasons.append("ast_compiled_not_formally_reproducible")
            elif formal_flag is None and ast is None:
                quantile_terms = [
                    t for t in terms
                    if isinstance(t, dict)
                    and t.get("threshold_source") == "prior_only_rolling_quantile"
                ]
                if len(quantile_terms) >= 2 and all(
                    str(t.get("factor") or "") in GENERIC_FACTOR_TO_DSL
                    for t in quantile_terms
                ):
                    formal_flag = True
                else:
                    reasons.append("ast_compiled_missing_event_ast")
        except Exception as exc:
            reasons.append("ast_capability_check_failed:%s" % type(exc).__name__)
    elif kind in GENERIC_EVENT_KINDS:
        if required_entries:
            reasons.append("generic_event_cannot_replace_exact_contract")
        if logic != "all" or len(terms) < 2:
            reasons.append("generic_event_requires_all_and_two_terms")
        for index, term in enumerate(terms):
            factor = str((term or {}).get("factor") or "") if isinstance(term, dict) else ""
            if factor not in GENERIC_FACTOR_TO_DSL:
                reasons.append("generic_factor_not_formally_supported:%s" % (
                    factor or "term_%d" % index,
                ))
    else:
        reasons.append("event_kind_not_formally_supported:%s" % (kind or "missing"))

    exit_policy = exit_policy if isinstance(exit_policy, dict) else {}
    exit_mode = str(exit_policy.get("mode") or "")
    if exit_mode not in ALLOWED_EXIT_POLICY_MODES:
        reasons.append("exit_policy_identity_missing")
    # fixed_horizon forbids early TP; feature_rsi_tp_v1 requires explicit RSI level
    # (machine-exact DSL feature) so it is reproducible, unlike free-form early TP.
    if exit_mode == EXIT_POLICY_MODE:
        if exit_policy.get("allow_early_take_profit") is not False:
            reasons.append("early_take_profit_policy_not_reproducible")
    elif exit_mode == FEATURE_RSI_EXIT_MODE:
        try:
            tp_level = float(exit_policy.get("rsi_tp_level"))
        except (TypeError, ValueError):
            tp_level = None
        if tp_level is None or not (5.0 <= tp_level <= 95.0):
            reasons.append("feature_rsi_tp_level_invalid")
        if str(exit_policy.get("rsi_feature") or "rsi14") != "rsi14":
            reasons.append("feature_rsi_tp_feature_unsupported")
    elif exit_mode == BARRIER_PCT_EXIT_MODE:
        try:
            tp = float(exit_policy.get("target_price_pct"))
        except (TypeError, ValueError):
            tp = None
        if tp is None or not (0.005 <= tp <= 0.05):
            reasons.append("barrier_target_pct_invalid")
        if exit_policy.get("allow_early_take_profit") is True:
            reasons.append("barrier_exit_must_not_use_freeform_early_tp_flag")
    stop_policy = (
        protective_stop_policy
        if isinstance(protective_stop_policy, dict) else {}
    )
    if stop_policy.get("mode") != PROTECTIVE_STOP_MODE:
        reasons.append("protective_stop_policy_identity_missing")
    if not _float_equal(stop_policy.get("price_pct"), PROTECTIVE_STOP_PCT):
        # Migration: accept legacy 0.9% researched recipes; formal DSL still
        # compiles to the current production stop (0.5% price).
        legacy_ok = _float_equal(stop_policy.get("price_pct"), 0.009)
        if not legacy_ok:
            reasons.append("protective_stop_pct_drift")
    if protective_stop_evaluated is not True:
        reasons.append("protective_stop_not_evaluated")
    try:
        lev = int(execution_leverage)
        leverage_ok = 20 <= lev <= 50
    except (TypeError, ValueError):
        leverage_ok = False
    if not leverage_ok:
        reasons.append("execution_leverage_not_in_20_50")
    if str(statistical_return_basis or "") != STATISTICAL_RETURN_BASIS:
        reasons.append("statistical_return_basis_identity_missing")

    return {
        "ok": not reasons,
        "reasons": list(dict.fromkeys(reasons)),
        "formal_execution_mapping": FORMAL_EXECUTION_MAPPING,
        "contract_integrity": integrity,
    }



def capability_from_row(row, contract):
    row = row if isinstance(row, dict) else {}
    return formal_capability(
        row.get("event_kind"), row.get("event_logic"), row.get("terms"),
        row.get("execution_mapping"), row.get("horizon_bars"), contract=contract,
        exit_policy=row.get("exit_policy"),
        protective_stop_policy=row.get("protective_stop_policy"),
        protective_stop_evaluated=row.get("protective_stop_evaluated"),
        execution_leverage=row.get("execution_leverage"),
        statistical_return_basis=row.get("statistical_return_basis"),
        event_ast=row.get("event_ast"),
        event_ast_formal_ok=row.get("event_ast_formal_ok"),
    )
