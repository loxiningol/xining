# -*- coding: utf-8 -*-
"""One capability boundary shared by discovery and formal compilation.

Research evidence may remain useful even when the current deterministic DSL
cannot reproduce it.  Such rows are diagnostics, never formal candidates.
"""
from __future__ import print_function

from . import research_contract as rcontract


RECIPE_SCHEMA = "qiyu_admitted_probe_recipe_v1"
FORMAL_EXECUTION_MAPPING = "next_bar_open"
EXIT_POLICY_MODE = "fixed_horizon_close_v1"
PROTECTIVE_STOP_MODE = "intrabar_fixed_pct_v1"
PROTECTIVE_STOP_PCT = rcontract.PRODUCTION_PROTECTIVE_STOP_PCT
EXECUTION_LEVERAGE = rcontract.PRODUCTION_EXECUTION_LEVERAGE
STATISTICAL_RETURN_BASIS = "full_size_leveraged_after_cost_v1"

# The factor name on the left is the discovery identity.  The value is the
# exact executable DSL feature; no fuzzy aliases or proxy substitution.
GENERIC_FACTOR_TO_DSL = {
    "close_z_20": "z20",
    "volume_z": "vol_z20",
}
GENERIC_EVENT_KINDS = ("mechanism_intersection", "mechanism_preserving")


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
                      statistical_return_basis=None):
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
        row.get("clause_id") for row in event_contract.get("clause_representations") or []
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
    if exit_policy.get("mode") != EXIT_POLICY_MODE:
        reasons.append("exit_policy_identity_missing")
    if exit_policy.get("allow_early_take_profit") is not False:
        reasons.append("early_take_profit_policy_not_reproducible")
    stop_policy = (
        protective_stop_policy
        if isinstance(protective_stop_policy, dict) else {}
    )
    if stop_policy.get("mode") != PROTECTIVE_STOP_MODE:
        reasons.append("protective_stop_policy_identity_missing")
    if not _float_equal(stop_policy.get("price_pct"), PROTECTIVE_STOP_PCT):
        reasons.append("protective_stop_pct_drift")
    if protective_stop_evaluated is not True:
        reasons.append("protective_stop_not_evaluated")
    try:
        leverage_ok = int(execution_leverage) == EXECUTION_LEVERAGE
    except (TypeError, ValueError):
        leverage_ok = False
    if not leverage_ok:
        reasons.append("execution_leverage_identity_missing")
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
    )
