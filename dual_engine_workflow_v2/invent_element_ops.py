# -*- coding: utf-8 -*-
"""Invent recipe element catalog + parent/child structural diff."""
from __future__ import print_function

import json

LOCATION_FAMILIES = frozenset((
    "xu_long", "xd_short", "pb_long", "pb_short",
    "ma_long", "ma_short", "ch_long", "ch_short",
))
GEOMETRY_KEYS = (
    "held", "xwin", "dwin", "fast", "slow", "z", "atr", "hold", "ma_kind",
)
RELATION_HINTS = frozenset((
    "ema", "sma", "wma", "hma", "above_ema", "below_ema",
))
ADD_INTENTS = frozenset((
    "add_location", "add_relation", "add_timing",
    "adjust_geometry", "replace_spine", "refine_timing", "switch_route",
))
NEXT_INTENTS_DEFAULT = (
    "add_location", "add_relation", "add_timing",
    "adjust_geometry", "replace_spine",
)


def _timing_fp(timing):
    rows = []
    for leaf in list(timing or []):
        if not isinstance(leaf, dict):
            continue
        rows.append("%s|%s|%s|%s" % (
            leaf.get("factor"), leaf.get("operator"),
            leaf.get("value"), leaf.get("window"),
        ))
    return tuple(sorted(rows))


def _timing_structure_fp(timing):
    rows = []
    for leaf in list(timing or []):
        if not isinstance(leaf, dict):
            continue
        rows.append("%s|%s" % (leaf.get("factor"), leaf.get("operator")))
    return tuple(sorted(rows))


def location_spine(recipe):
    """Compact location / relation spine fingerprint."""
    if not isinstance(recipe, dict):
        return ""
    fam = str(recipe.get("family") or "").strip()
    route = str(recipe.get("route") or "").strip()
    ma = str(recipe.get("ma_kind") or "").strip()
    return "%s|%s|%s|held=%s|xwin=%s" % (
        route, fam, ma, recipe.get("held"), recipe.get("xwin"),
    )


def recipe_structure_fp(recipe):
    if not isinstance(recipe, dict):
        return ""
    return json.dumps({
        "route": recipe.get("route"),
        "family": recipe.get("family"),
        "symbol": str(recipe.get("symbol") or "").upper(),
        "tf": recipe.get("exec_tf") or recipe.get("timeframe"),
        "geom": dict((k, recipe.get(k)) for k in GEOMETRY_KEYS if k in recipe),
        "timing": list(_timing_structure_fp(recipe.get("timing"))),
    }, sort_keys=True, ensure_ascii=False, default=str)


def diff_elements(parent, child):
    """Return structural element delta between parent and child recipes."""
    parent = parent if isinstance(parent, dict) else {}
    child = child if isinstance(child, dict) else {}
    out = {
        "has_diff": False,
        "location_changed": False,
        "relation_changed": False,
        "timing_changed": False,
        "timing_structure_changed": False,
        "geometry_changed": False,
        "route_changed": False,
        "symbol_changed": False,
        "family_changed": False,
        "elements_added": [],
        "categories": [],
    }
    if not parent:
        out["has_diff"] = True
        out["elements_added"].append("initial")
        out["categories"].append("initial")
        return out

    p_fam = str(parent.get("family") or "").strip()
    c_fam = str(child.get("family") or "").strip()
    if p_fam != c_fam:
        out["family_changed"] = True
        out["location_changed"] = True
        out["elements_added"].append("family:%s->%s" % (p_fam, c_fam))
        out["categories"].append("location")

    p_sym = str(parent.get("symbol") or "").strip().upper()
    c_sym = str(child.get("symbol") or "").strip().upper()
    if p_sym != c_sym:
        out["symbol_changed"] = True
        out["elements_added"].append("symbol:%s->%s" % (p_sym, c_sym))
        out["categories"].append("symbol")

    p_route = str(parent.get("route") or "").strip()
    c_route = str(child.get("route") or "").strip()
    if p_route != c_route:
        out["route_changed"] = True
        out["elements_added"].append("route:%s->%s" % (p_route, c_route))
        out["categories"].append("route")

    p_ma = str(parent.get("ma_kind") or "").strip()
    c_ma = str(child.get("ma_kind") or "").strip()
    if p_ma != c_ma:
        out["relation_changed"] = True
        out["elements_added"].append("ma_kind:%s->%s" % (p_ma, c_ma))
        out["categories"].append("relation")

    for key in GEOMETRY_KEYS:
        if key == "ma_kind":
            continue
        if parent.get(key) != child.get(key) and (
            key in parent or key in child
        ):
            out["geometry_changed"] = True
            out["elements_added"].append(
                "%s:%s->%s" % (key, parent.get(key), child.get(key))
            )
            if "geometry" not in out["categories"]:
                out["categories"].append("geometry")

    p_t = _timing_fp(parent.get("timing"))
    c_t = _timing_fp(child.get("timing"))
    if p_t != c_t:
        out["timing_changed"] = True
        out["elements_added"].append("timing_values")
        if "timing" not in out["categories"]:
            out["categories"].append("timing")
    p_ts = _timing_structure_fp(parent.get("timing"))
    c_ts = _timing_structure_fp(child.get("timing"))
    if p_ts != c_ts:
        out["timing_structure_changed"] = True
        out["elements_added"].append("timing_structure")
        if "timing" not in out["categories"]:
            out["categories"].append("timing")
        # new factors look like location/relation peers (volume / atr / ema)
        new_factors = set(x.split("|")[0] for x in c_ts) - set(
            x.split("|")[0] for x in p_ts
        )
        for fac in sorted(new_factors):
            if any(h in str(fac) for h in RELATION_HINTS):
                out["relation_changed"] = True
                if "relation" not in out["categories"]:
                    out["categories"].append("relation")
            out["elements_added"].append("timing_factor:%s" % fac)

    if location_spine(parent) != location_spine(child):
        out["location_changed"] = True
        if "location" not in out["categories"]:
            out["categories"].append("location")

    out["has_diff"] = bool(out["elements_added"])
    # unique categories preserve order
    seen = set()
    cats = []
    for c in out["categories"]:
        if c not in seen:
            seen.add(c)
            cats.append(c)
    out["categories"] = cats
    return out


def infer_intent(parent, child, declared=None):
    """Prefer declared intent; else infer from structural diff."""
    declared = str(declared or "").strip()
    if declared in ADD_INTENTS:
        return declared
    d = diff_elements(parent, child)
    if d.get("route_changed") or d.get("family_changed"):
        return "replace_spine"
    if d.get("location_changed"):
        return "add_location"
    if d.get("relation_changed"):
        return "add_relation"
    if d.get("timing_structure_changed"):
        return "add_timing"
    if d.get("geometry_changed"):
        return "adjust_geometry"
    if d.get("timing_changed"):
        return "refine_timing"
    return "noop"


def next_intents_for(stage, diff, reward_parts):
    """Suggest next invent intents for critique feedback."""
    stage = str(stage or "")
    intents = list(NEXT_INTENTS_DEFAULT)
    if stage in ("S1_n",):
        # need more trades: location / relation / tf-ish spine first
        intents = [
            "add_location", "add_relation", "replace_spine", "add_timing",
        ]
    elif stage in ("S2_hitch",):
        intents = [
            "adjust_geometry", "add_timing", "add_location", "add_relation",
        ]
    elif stage in ("S3_E", "S4_C"):
        intents = [
            "add_location", "add_relation", "add_timing", "adjust_geometry",
        ]
    if reward_parts and reward_parts.get("noop_or_clone"):
        # force structural add after clone
        intents = ["add_location", "add_relation", "replace_spine"] + intents
    # de-dup
    seen = set()
    out = []
    for i in intents:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out[:6]
