# -*- coding: utf-8 -*-
"""Repair ≤3 rounds with root-cause logging + mechanism drift detection."""
from __future__ import print_function

import copy

from .config import MAX_REPAIR_ROUNDS
from .mechanism import (
    build_fingerprint_from_statement,
    drift_compare,
    fingerprint_hash,
)


def init_repair_log(task_id, strategy_id, orig_statement):
    return {
        "task_id": task_id,
        "strategy_id": strategy_id,
        "max_rounds": MAX_REPAIR_ROUNDS,
        "rounds": [],
        "orig_statement": copy.deepcopy(orig_statement or {}),
        "orig_fingerprint": build_fingerprint_from_statement(orig_statement or {}),
        "terminated": False,
        "termination_reason": None,
    }


def record_repair_round(log, *, failed_test, root_cause, modifications,
                        statement_before, statement_after,
                        fingerprint_before, fingerprint_after,
                        frequency_before, frequency_after,
                        oos_before, oos_after,
                        introduced_new_condition, changed_edge_source):
    if len(log.get("rounds") or []) >= MAX_REPAIR_ROUNDS:
        log["terminated"] = True
        log["termination_reason"] = "repair_cap_reached"
        return log, {"drift": "none", "blocked": True, "reason": "repair_cap"}

    round_i = len(log["rounds"]) + 1
    drift = drift_compare(log.get("orig_statement"), statement_after)
    # Independent profitability of new conditions → material drift signal
    if introduced_new_condition and changed_edge_source:
        drift = {
            "drift": "material",
            "changed_fields": list(drift.get("changed_fields") or []) + ["new_condition_independent_edge"],
            "action": "terminate_reopen_as_new",
        }

    row = {
        "round": round_i,
        "failed_test": failed_test,
        "root_cause": root_cause,
        "modifications": modifications,
        "fingerprint_before": fingerprint_before,
        "fingerprint_after": fingerprint_after,
        "frequency_before": frequency_before,
        "frequency_after": frequency_after,
        "oos_before": oos_before,
        "oos_after": oos_after,
        "introduced_new_condition": bool(introduced_new_condition),
        "changed_edge_source": bool(changed_edge_source),
        "drift": drift,
    }
    log.setdefault("rounds", []).append(row)

    if drift.get("drift") == "material":
        log["terminated"] = True
        log["termination_reason"] = "mechanism_drift_material"
        return log, drift
    if drift.get("drift") == "mild":
        # statement version bump expected by caller
        return log, drift
    if round_i >= MAX_REPAIR_ROUNDS and not log.get("success"):
        log["terminated"] = True
        log["termination_reason"] = "repair_cap_reached"
    return log, drift


def apply_engineering_repair_only(book, root_cause, repair_i):
    """Mode D / engineering-only: fix hold bars / validate fields — no new filters."""
    dsl = copy.deepcopy(book.get("dsl") or {})
    # Only tighten max_hold if root cause is hold-related engineering
    if "hold" in str(root_cause or "").lower() or "timeout" in str(root_cause or "").lower():
        dsl["max_hold_bars"] = max(4, int(dsl.get("max_hold_bars") or 24) - 1)
    dsl["description"] = (dsl.get("description") or "") + "|eng_repair_%s" % (repair_i + 1)
    book = dict(book)
    book["dsl"] = dsl
    book["repair_round"] = repair_i + 1
    book["repair_engineering_only"] = True
    return book


def forbidden_legacy_repair(book, hints, repair_i):
    """Explicitly blocked: old RSI tighten / CCI inject path."""
    raise RuntimeError(
        "legacy_repair_operator_forbidden_in_v2: hints=%s round=%s" % (hints, repair_i)
    )


def detect_frequency_exit(target_range, trades, bars, timeframe):
    """Return True if frequency fell outside task requirement after repair."""
    # rough bars-per-day by timeframe
    tf = str(timeframe or "5m")
    bp = {"1m": 1440, "5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}.get(tf, 288)
    days = max(float(bars or 0) / float(bp), 1.0)
    tpd = float(trades or 0) / days
    lo, hi = target_range
    return tpd < lo * 0.5 or tpd > hi * 3, tpd
