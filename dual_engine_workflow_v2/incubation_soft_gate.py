# -*- coding: utf-8 -*-
"""Soft incubation progress (does NOT lower formal Gate2 floors).

Formal success still requires fitness_engine hard floors
(payoff≥2.5 / calmar≥1.5 / …). Soft bands only score whether a gene has
positive causal bud — used for AI guidance / early cull messaging.
"""
from __future__ import print_function

# Soft budding thresholds (guidance only)
SOFT_PAYOFF_MIN = 1.3
SOFT_CALMAR_MIN = 0.8
# Anchor pre-matrix cull: if primary full-BT calmar is below this, skip 38-matrix
ANCHOR_CALMAR_KILL = 0.5


def soft_progress(metrics):
    """Return soft-gate snapshot from metrics dict."""
    metrics = metrics or {}
    payoff = metrics.get("payoff_ratio")
    calmar = metrics.get("calmar")
    if calmar is None:
        calmar = metrics.get("calmar_ratio")
    n = metrics.get("sample_size") or metrics.get("n_trades") or metrics.get("filled_entries")
    try:
        payoff_f = float(payoff) if payoff is not None else None
    except Exception:
        payoff_f = None
    try:
        calmar_f = float(calmar) if calmar is not None else None
    except Exception:
        calmar_f = None
    try:
        n_i = int(n) if n is not None else None
    except Exception:
        n_i = None
    budding = bool(
        (payoff_f is not None and payoff_f >= SOFT_PAYOFF_MIN)
        and (calmar_f is not None and calmar_f >= SOFT_CALMAR_MIN)
        and (n_i is None or n_i >= 8)
    )
    return {
        "budding": budding,
        "soft_payoff_min": SOFT_PAYOFF_MIN,
        "soft_calmar_min": SOFT_CALMAR_MIN,
        "payoff": payoff_f,
        "calmar": calmar_f,
        "n": n_i,
        "formal_floors_unchanged": True,
        "formal_payoff_min": 2.5,
        "formal_calmar_min": 1.5,
        "note_zh": (
            "萌芽门槛仅用于指导演进与提前淘汰；正式通关仍须 Gate2 "
            "Payoff≥2.5 / Calmar≥1.5，不得下调。"
        ),
    }


def should_skip_full_matrix(primary_metrics):
    """True → do not burn RAM on 38-symbol Gate2 pool."""
    m = primary_metrics or {}
    calmar = m.get("calmar")
    if calmar is None:
        calmar = m.get("calmar_ratio")
    try:
        c = float(calmar)
    except Exception:
        return False  # unknown — let formal path decide
    return c < ANCHOR_CALMAR_KILL
