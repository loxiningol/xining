# -*- coding: utf-8 -*-
"""Thin-hub timing atom whitelist (single source of truth).

PR-A of thin_blueprint_v2: flatten atoms + aliases + op groups.
"""
from __future__ import print_function

# Default seed timing (locked by blueprint §4 / PR-A).
DEFAULT_TIMING = (
    {"factor": "skdj_diff", "operator": "above", "value": -20, "window": 9},
    {"factor": "macd_hist", "operator": "above", "value": -1},
)

# operator → diversity group. "cross" is a group label, NOT "high frequency".
ATOM_OP_GROUP = {
    "cross_up": "cross",
    "cross_down": "cross",
    "above": "level",
    "below": "level",
    "between": "level",
}

# External / mistaken names → (factor, operator)
ATOM_ALIASES = {
    "macd_histogram_cross": ("macd_dd", "cross_up"),
    "macd_hist_cross": ("macd_dd", "cross_up"),
    "cci_oversold": ("cci", "below"),
    "cci_overbought": ("cci", "above"),
}

# Flat whitelist: (factor, operator) — timing leaves only (no ema geometry).
TIMING_ATOM_PAIRS = (
    ("skdj_k", "below"), ("skdj_k", "above"), ("skdj_k", "between"), ("skdj_k", "cross_up"),
    ("skdj_d", "below"), ("skdj_d", "above"), ("skdj_d", "cross_up"),
    ("skdj_kd", "above"), ("skdj_kd", "below"),
    ("skdj_kd", "cross_up"), ("skdj_kd", "cross_down"),
    ("skdj_diff", "above"), ("skdj_diff", "below"), ("skdj_diff", "between"),
    ("kdj_k", "below"), ("kdj_k", "above"), ("kdj_k", "cross_up"),
    ("kdj_d", "below"), ("kdj_d", "above"),
    ("kdj_kd", "above"), ("kdj_kd", "below"),
    ("kdj_kd", "cross_up"), ("kdj_kd", "cross_down"),
    ("macd_dd", "above"), ("macd_dd", "below"),
    ("macd_dd", "cross_up"), ("macd_dd", "cross_down"),
    ("macd_hist", "above"), ("macd_hist", "below"),
    ("macd_dif", "above"), ("macd_dif", "below"),
    ("cci", "above"), ("cci", "below"), ("cci", "cross_up"), ("cci", "cross_down"),
)

TIMING_SKDJ = frozenset(("skdj_k", "skdj_d", "skdj_kd", "skdj_diff"))
TIMING_PEER = frozenset((
    "macd_hist", "macd_dif", "macd_dd",
    "cci", "kdj_k", "kdj_d", "kdj_kd",
))

TIMING_ATOM_SET = frozenset(TIMING_ATOM_PAIRS)


def normalize_timing_item(item):
    """Apply aliases; return dict or None if illegal."""
    if not isinstance(item, dict):
        return None
    factor = str(item.get("factor") or "").strip()
    op = str(item.get("operator") or "").strip()
    alias = ATOM_ALIASES.get(factor)
    if alias:
        factor, op_default = alias
        if not op:
            op = op_default
    if (factor, op) not in TIMING_ATOM_SET:
        return None
    out = dict(item)
    out["factor"] = factor
    out["operator"] = op
    return out


def normalize_timing(timing):
    rows = []
    for item in timing or []:
        norm = normalize_timing_item(item)
        if norm is None:
            return None
        rows.append(norm)
    return rows


def timing_ok(timing):
    rows = normalize_timing(timing)
    if not rows:
        return False
    factors = set(r["factor"] for r in rows)
    return bool(factors & TIMING_SKDJ) and bool(factors & TIMING_PEER)


def timing_groups(timing):
    groups = set()
    for item in timing or []:
        op = str((item or {}).get("operator") or "")
        groups.add(ATOM_OP_GROUP.get(op, "other"))
    return groups


def diversity_counts(history_timings, new_timing):
    """Return (counts_toward_depth: bool, warn_zh: str)."""
    recent = list(history_timings or []) + [new_timing]
    recent = recent[-3:]
    if len(recent) < 3:
        return True, ""
    group_sets = [frozenset(timing_groups(t)) for t in recent]
    if all(len(g) == 1 for g in group_sets) and len(set(group_sets)) == 1:
        return False, (
            "原子类型重复，请切换至另一操作组（cross ↔ level）重新尝试，"
            "否则不计入8次探索深度。"
        )
    return True, ""


def default_timing():
    return [dict(x) for x in DEFAULT_TIMING]


# Blueprint alias
diversity_gate = diversity_counts
