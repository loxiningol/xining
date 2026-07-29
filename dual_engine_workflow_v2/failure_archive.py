# -*- coding: utf-8 -*-
"""Structured failure archive — failure_level enum only, no vague language."""
from __future__ import print_function

import re

from .config import FAILURE_LEVELS, _now


VAGUE_PATTERNS = (
    r"达到极限",
    r"走到尽头",
    r"市场不适合",
    r"无法继续优化",
    r"策略自身的问题",
    r"多轮进步但最终失败",
    r"已经无法",
    r"客观不存在",
)


def sanitize_statement(text):
    text = str(text or "").strip()
    for pat in VAGUE_PATTERNS:
        if re.search(pat, text):
            raise ValueError("vague_failure_language_forbidden: %s" % pat)
    return text


def build_failure_archive(
    *,
    strategy_id,
    mechanism_id,
    symbol,
    timeframe,
    exploration_mode,
    failure_level,
    evidence_scope,
    failed_test,
    observed_result,
    expected_result,
    root_cause_confidence,
    alternative_explanations=None,
    ruled_out_explanations=None,
    unresolved_questions=None,
    reusable_exclusion_rule=None,
    retry_conditions=None,
    final_statement=None,
    task_id=None,
    model_call_id=None,
    code_version=None,
    data_version=None,
    backtest_version=None,
):
    if failure_level not in FAILURE_LEVELS:
        raise ValueError("invalid failure_level: %s" % failure_level)
    final_statement = sanitize_statement(final_statement or "")
    if failure_level == "hypothesis_evidence_failure":
        # must not claim market logic never exists
        if re.search(r"永远不成立|客观不存在|永不可能", final_statement):
            raise ValueError("hypothesis_evidence_failure cannot claim permanent nonexistence")

    rule = reusable_exclusion_rule or {
        "exclude_if": {
            "mechanism_family": mechanism_id,
            "failed_test": failed_test,
            "symbol": symbol,
            "timeframe": timeframe,
        },
        "note": "machine-readable exclusion from structured archive",
    }
    return {
        "schema": "qiyu_failure_archive_v1",
        "strategy_id": strategy_id,
        "mechanism_id": mechanism_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "exploration_mode": exploration_mode,
        "failure_level": failure_level,
        "evidence_scope": evidence_scope,
        "failed_test": failed_test,
        "observed_result": observed_result,
        "expected_result": expected_result,
        "root_cause_confidence": root_cause_confidence,
        "alternative_explanations": list(alternative_explanations or []),
        "ruled_out_explanations": list(ruled_out_explanations or []),
        "unresolved_questions": list(unresolved_questions or []),
        "reusable_exclusion_rule": rule,
        "retry_conditions": list(retry_conditions or []),
        "final_statement": final_statement,
        "task_id": task_id,
        "model_call_id": model_call_id,
        "code_version": code_version,
        "data_version": data_version,
        "backtest_version": backtest_version,
        "created_at": _now(),
    }


def map_failed_test_to_level(failed_test, packs=None):
    """Heuristic mapping for automatic archival."""
    ft = str(failed_test or "")
    packs = packs or {}
    if ft in ("duplicate_mechanism", "fingerprint_duplicate"):
        return "duplicate_mechanism"
    if ft in ("mechanism_drift", "drift_material"):
        return "mechanism_drift"
    if ft in ("necessity", "mechanism_necessity", "pseudo_mechanism"):
        return "hypothesis_evidence_failure"
    if ft in ("parameter_stability", "isolated_peak", "robustness"):
        return "robustness_failure"
    if ft in ("extreme_friction", "execution_friction", "execution_cost"):
        return "execution_cost_failure"
    if ft in ("frequency", "frequency_target"):
        return "frequency_target_conflict"
    if ft in ("fidelity", "unapproved_filter", "representation"):
        return "representation_failure"
    if ft in ("engineering", "validate", "dsl_error"):
        return "engineering_failure"
    if ft in ("observability", "data_grain"):
        return "observability_failure"
    if ft in ("test_standard", "wf_gate"):
        return "test_standard_conflict"
    if ft in ("formal_fatal",):
        return "hypothesis_evidence_failure"
    return "insufficient_evidence"
