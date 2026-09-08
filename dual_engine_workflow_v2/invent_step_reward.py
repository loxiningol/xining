# -*- coding: utf-8 -*-
"""Step-wise invent rewards toward dual-C / stage progress (not mount authority)."""
from __future__ import print_function

import os

from dual_engine_workflow_v2.invent_element_ops import diff_elements
from dual_engine_workflow_v2.timing_stage import classify_stage, stage_need

STAGE_ORDER = (
    "S1_n", "S2_hitch", "S3_E", "S4_C", "S4_pass",
)


def _f(v, default=None):
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _env_weight(name, default):
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


WEIGHTS = {
    "stage_up": _env_weight("KDH_REWARD_STAGE_UP", 3.0),
    "dual_c_sync_up": _env_weight("KDH_REWARD_DUAL_C", 2.0),
    "single_c_up": _env_weight("KDH_REWARD_SINGLE_C", 0.5),
    "usable_new": _env_weight("KDH_REWARD_USABLE", 1.0),
    "blocker_clear": _env_weight("KDH_REWARD_BLOCKER", 1.0),
    "element_effective": _env_weight("KDH_REWARD_ELEMENT", 2.0),
    "noop_or_clone": _env_weight("KDH_REWARD_NOOP", -2.0),
    "hit_floor_bonus": _env_weight("KDH_REWARD_HIT_FLOOR", 10.0),
}
SINGLE_C_CAP = _env_weight("KDH_REWARD_SINGLE_C_CAP", 1.0)
EPS_C = 1e-6


def stage_rank(stage):
    stage = str(stage or "")
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return 0


def _blocker_flags(row):
    """Map stage_need style blockers currently failing."""
    row = row or {}
    stage = classify_stage(row)
    need = stage_need(stage) or {}
    flags = {}
    n = _i(row.get("n"), 0)
    weekly = _f(row.get("weekly"))
    hitch = _f(row.get("hitch"))
    e = _f(row.get("E"))
    if e is None:
        e = _f(row.get("E_path"))
    c = _f(row.get("C_week_pct"))
    co = _f(row.get("C_week_oos_pct"))
    if "n_ge" in need:
        flags["n"] = n >= int(need["n_ge"])
    if "weekly_ge" in need and weekly is not None:
        flags["weekly"] = weekly >= float(need["weekly_ge"])
    if "hitch_lt" in need and hitch is not None:
        flags["hitch"] = hitch < float(need["hitch_lt"])
    if "E_ge" in need and e is not None:
        flags["E"] = e >= float(need["E_ge"])
    if "C_week_pct" in need:
        flags["C"] = (c is not None and c >= float(need["C_week_pct"]))
    if "C_week_oos_pct" in need:
        flags["C_oos"] = (co is not None and co >= float(need["C_week_oos_pct"]))
    if need.get("usable"):
        flags["usable"] = bool(row.get("usable"))
    if need.get("hit_floor"):
        flags["hit_floor"] = bool(row.get("hit_floor"))
    # Always expose raw machine-gate style flags for clear detection
    flags.setdefault("n", n >= 30)
    if weekly is not None:
        flags.setdefault("weekly", weekly >= 0.50)
    if hitch is not None:
        flags.setdefault("hitch", hitch < 0.30)
    if e is not None:
        flags.setdefault("E", e >= 0.003)
    return flags


def score_step(parent_eval, child_eval, parent_recipe=None, child_recipe=None,
               intent=None):
    """Return reward breakdown relative to parent eval/recipe.

    Mount / hit_floor thresholds are NOT changed here — this only shapes
    invent step selection and critique.
    """
    parent_eval = parent_eval if isinstance(parent_eval, dict) else {}
    child_eval = child_eval if isinstance(child_eval, dict) else {}
    diff = diff_elements(parent_recipe, child_recipe)
    parts = {}
    tags = []

    p_stage = classify_stage(parent_eval) if parent_eval else "S1_n"
    c_stage = classify_stage(child_eval)
    if stage_rank(c_stage) > stage_rank(p_stage):
        parts["stage_up"] = WEIGHTS["stage_up"]
        tags.append("stage_up")

    p_c = _f(parent_eval.get("C_week_pct"), 0.0) or 0.0
    c_c = _f(child_eval.get("C_week_pct"), 0.0) or 0.0
    p_co = _f(parent_eval.get("C_week_oos_pct"), 0.0) or 0.0
    c_co = _f(child_eval.get("C_week_oos_pct"), 0.0) or 0.0
    d_c = c_c - p_c
    d_co = c_co - p_co
    dual = d_c > EPS_C and d_co > EPS_C
    if dual:
        parts["dual_c_sync_up"] = WEIGHTS["dual_c_sync_up"]
        tags.append("dual_c_sync_up")
    else:
        single = 0.0
        if d_c > EPS_C:
            single += WEIGHTS["single_c_up"]
        if d_co > EPS_C:
            single += WEIGHTS["single_c_up"]
        if single > 0:
            parts["single_c_up"] = min(single, SINGLE_C_CAP)
            tags.append("single_c_up")

    if (not parent_eval.get("usable")) and child_eval.get("usable"):
        parts["usable_new"] = WEIGHTS["usable_new"]
        tags.append("usable_new")
    if (not parent_eval.get("oos_usable")) and child_eval.get("oos_usable"):
        parts["usable_new_oos"] = WEIGHTS["usable_new"]
        tags.append("usable_new")

    p_flags = _blocker_flags(parent_eval) if parent_eval else {}
    c_flags = _blocker_flags(child_eval)
    cleared = 0
    for key, ok in c_flags.items():
        if ok and not p_flags.get(key):
            cleared += 1
    if cleared:
        parts["blocker_clear"] = WEIGHTS["blocker_clear"] * cleared
        tags.append("blocker_clear")

    structural = bool(diff.get("has_diff")) and (
        diff.get("location_changed")
        or diff.get("relation_changed")
        or diff.get("timing_structure_changed")
        or diff.get("route_changed")
        or diff.get("family_changed")
    )
    intent_s = str(intent or "").strip()
    addish = intent_s in (
        "add_location", "add_relation", "add_timing", "replace_spine",
    ) or structural
    if addish and dual and structural:
        parts["element_effective"] = WEIGHTS["element_effective"]
        tags.append("element_effective")

    clone = (not diff.get("has_diff")) or (
        abs(d_c) <= EPS_C and abs(d_co) <= EPS_C and not structural
    )
    if clone or intent_s in ("noop", ""):
        if not parent_eval:
            pass
        elif abs(d_c) <= EPS_C and abs(d_co) <= EPS_C:
            parts["noop_or_clone"] = WEIGHTS["noop_or_clone"]
            tags.append("noop_or_clone")

    if child_eval.get("hit_floor"):
        parts["hit_floor_bonus"] = WEIGHTS["hit_floor_bonus"]
        tags.append("hit_floor_bonus")

    total = 0.0
    for v in parts.values():
        total += float(v)

    return {
        "total": round(total, 4),
        "parts": parts,
        "tags": tags,
        "delta": {
            "C_week_pct": round(d_c, 6),
            "C_week_oos_pct": round(d_co, 6),
            "stage": "%s->%s" % (p_stage, c_stage),
        },
        "diff": diff,
        "stage": c_stage,
        "parent_stage": p_stage,
        "intent": intent_s or None,
    }
