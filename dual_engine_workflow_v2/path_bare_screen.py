# -*- coding: utf-8 -*-
"""Bare entry path screen — hard feasibility before full strategy assembly.

Hard constraints are a feasible region, NOT weighted score terms.
"""
from __future__ import print_function

from . import path_outcome as po

# Hard gates (plan §2)
MIN_PROFIT_FIRST_RATE = 0.55
MIN_MEAN_WINNING_LEVERED = 0.1111
MIN_MEDIAN_WINNING_LEVERED = 0.10
MAX_MEDIAN_MAE = 0.0035
MIN_SAMPLE_FULL = 200
MIN_SAMPLE_UNDERPOWERED = 80  # may diagnose, never hand to formal review
MIN_TEMPORAL_POSITIVE = 0.70


def hard_gate(summary, allow_underpowered=False):
    """Return feasible flag + reasons. underpowered never qualifies for review."""
    s = summary or {}
    reasons = []
    n = int(s.get("n") or 0)
    underpowered = False
    if n < MIN_SAMPLE_FULL:
        if allow_underpowered and n >= MIN_SAMPLE_UNDERPOWERED:
            underpowered = True
            reasons.append("sample_underpowered_n_lt_%d" % MIN_SAMPLE_FULL)
        else:
            reasons.append("sample_count_below_%d" % MIN_SAMPLE_FULL)
    pfr = s.get("profit_first_rate")
    if pfr is None or float(pfr) < MIN_PROFIT_FIRST_RATE - 1e-12:
        reasons.append("profit_first_rate_below_%.2f" % MIN_PROFIT_FIRST_RATE)
    mean_w = s.get("mean_winning_levered")
    if mean_w is None or float(mean_w) < MIN_MEAN_WINNING_LEVERED - 1e-12:
        reasons.append("mean_winning_levered_below_%.4f" % MIN_MEAN_WINNING_LEVERED)
    med_w = s.get("median_winning_levered")
    if med_w is None or float(med_w) < MIN_MEDIAN_WINNING_LEVERED - 1e-12:
        reasons.append("median_winning_levered_below_%.2f" % MIN_MEDIAN_WINNING_LEVERED)
    med_mae = s.get("median_mae_pct")
    # mae is negative for adverse; compare magnitude
    if med_mae is None:
        reasons.append("median_mae_missing")
    else:
        # Accept either signed negative MAE or absolute.
        mae_mag = abs(float(med_mae))
        if mae_mag > MAX_MEDIAN_MAE + 1e-12:
            reasons.append("median_mae_above_%.4f" % MAX_MEDIAN_MAE)
    temporal = s.get("temporal_positive_ratio")
    if temporal is None or float(temporal) < MIN_TEMPORAL_POSITIVE - 1e-12:
        reasons.append("temporal_positive_ratio_below_%.2f" % MIN_TEMPORAL_POSITIVE)

    # Feasible for ranking only if no hard fails EXCEPT pure underpowered tag
    # when allow_underpowered — still not review-eligible.
    blocking = [r for r in reasons if not r.startswith("sample_underpowered")]
    feasible = not blocking
    review_eligible = feasible and not underpowered and n >= MIN_SAMPLE_FULL
    return {
        "ok": feasible,
        "feasible": feasible,
        "review_eligible": review_eligible,
        "underpowered": underpowered,
        "reasons": reasons,
        "thresholds": {
            "profit_first_rate": MIN_PROFIT_FIRST_RATE,
            "mean_winning_levered": MIN_MEAN_WINNING_LEVERED,
            "median_winning_levered": MIN_MEDIAN_WINNING_LEVERED,
            "median_mae_max": MAX_MEDIAN_MAE,
            "sample_full": MIN_SAMPLE_FULL,
            "sample_underpowered": MIN_SAMPLE_UNDERPOWERED,
            "temporal_positive_ratio": MIN_TEMPORAL_POSITIVE,
        },
        "observed": {
            "n": n,
            "profit_first_rate": pfr,
            "mean_winning_levered": mean_w,
            "median_winning_levered": med_w,
            "median_mae_pct": med_mae,
            "temporal_positive_ratio": temporal,
        },
    }


def entry_score(summary, gate=None):
    """Soft rank only after hard feasibility (or for diagnostics)."""
    s = summary or {}
    gate = gate or hard_gate(s)
    pfr = float(s.get("profit_first_rate") or 0.0)
    mean_w = float(s.get("mean_winning_levered") or 0.0)
    med_mae = abs(float(s.get("median_mae_pct") or 1.0))
    temporal = float(s.get("temporal_positive_ratio") or 0.0)
    n = int(s.get("n") or 0)
    win_pass = 1.0 if mean_w >= MIN_MEAN_WINNING_LEVERED else max(
        0.0, mean_w / MIN_MEAN_WINNING_LEVERED,
    )
    low_mae = max(0.0, min(1.0, 1.0 - (med_mae / MAX_MEDIAN_MAE)))
    n_score = max(0.0, min(1.0, n / float(MIN_SAMPLE_FULL)))
    score = (
        0.35 * min(1.0, pfr / MIN_PROFIT_FIRST_RATE)
        + 0.25 * win_pass
        + 0.15 * low_mae
        + 0.15 * temporal
        + 0.10 * n_score
    )
    return {
        "score": round(score, 6),
        "feasible": bool(gate.get("feasible")),
        "review_eligible": bool(gate.get("review_eligible")),
        "components": {
            "profit_first": pfr,
            "win_avg_pass": win_pass,
            "low_mae": low_mae,
            "temporal": temporal,
            "n_score": n_score,
        },
    }


def screen_signals(
    candles,
    signal_indices,
    direction,
    horizon,
    mapping="next_bar_open",
    allow_underpowered=False,
):
    summary = po.summarize_signals(
        candles, signal_indices, direction, horizon, mapping=mapping,
    )
    # Drop raw labels from return to keep payloads small unless needed.
    labels = summary.pop("labels", None)
    gate = hard_gate(summary, allow_underpowered=allow_underpowered)
    rank = entry_score(summary, gate=gate)
    return {
        "ok": True,
        "schema": "qiyu_path_bare_screen_v1",
        "summary": summary,
        "gate": gate,
        "rank": rank,
        "passed": bool(gate.get("review_eligible")),
        "packaging_ok": bool(gate.get("feasible")),
        "n_labels": len(labels or []),
    }


def screen_from_observations(observations, horizon=None, direction=None):
    """When probe already produced path-enriched observations."""
    labels = []
    for row in observations or []:
        if not isinstance(row, dict):
            continue
        if row.get("profit_first") is None and row.get("path_label"):
            row = dict(row)
            row.update(row.get("path_label") or {})
        if row.get("profit_first") is None:
            continue
        labels.append({
            "ok": True,
            "profit_first": row.get("profit_first"),
            "mfe_pct": row.get("mfe_pct", row.get("mfe")),
            "mae_pct": row.get("mae_pct", row.get("mae")),
            "bars_to_target": row.get("bars_to_target"),
            "bars_to_stop": row.get("bars_to_stop"),
            "first_touch": row.get("first_touch"),
        })
    summary = po.summarize_labels(labels)
    if horizon is not None:
        summary["horizon"] = horizon
    if direction is not None:
        summary["direction"] = direction
    failure_path_breakdown = None
    try:
        from . import failure_path_analyzer as fpa
        failure_path_breakdown = fpa.breakdown_from_labels(labels)
        summary["failure_path_breakdown"] = failure_path_breakdown.get(
            "failure_breakdown"
        )
        summary["dominant_failure_path"] = failure_path_breakdown.get(
            "dominant_failure_path"
        )
        summary["classified_failure_ratio"] = failure_path_breakdown.get(
            "classified_failure_ratio"
        )
    except Exception:
        failure_path_breakdown = None
    gate = hard_gate(summary, allow_underpowered=True)
    rank = entry_score(summary, gate=gate)
    return {
        "ok": True,
        "schema": "qiyu_path_bare_screen_v1",
        "summary": summary,
        "failure_path_breakdown": failure_path_breakdown,
        "gate": gate,
        "rank": rank,
        "passed": bool(gate.get("review_eligible")),
        "packaging_ok": bool(gate.get("feasible")),
        "path_labels": labels,
    }


def probe():
    # Shit package profile should fail hard gates.
    shit = {
        "n": 163,
        "profit_first_rate": 0.31,
        "mean_winning_levered": 0.0658,
        "median_winning_levered": 0.05,
        "median_mae_pct": -0.006,
        "temporal_positive_ratio": 0.4,
    }
    good = {
        "n": 220,
        "profit_first_rate": 0.60,
        "mean_winning_levered": 0.12,
        "median_winning_levered": 0.11,
        "median_mae_pct": -0.0025,
        "temporal_positive_ratio": 0.8,
    }
    return {
        "ok": True,
        "shit_feasible": hard_gate(shit).get("feasible"),
        "shit_review": hard_gate(shit).get("review_eligible"),
        "good_feasible": hard_gate(good).get("feasible"),
        "good_review": hard_gate(good).get("review_eligible"),
    }
