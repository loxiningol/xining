# -*- coding: utf-8 -*-
"""Thin-hub create policy: timeframe pool + explore budget (blueprint §1.3 / §2.1).

Python 3.6 compatible.
"""
from __future__ import print_function

try:
    from .thin_timing_atoms import default_timing, DEFAULT_TIMING, diversity_counts
except Exception:  # pragma: no cover
    DEFAULT_TIMING = (
        {"factor": "skdj_diff", "operator": "above", "value": -20, "window": 9},
        {"factor": "macd_hist", "operator": "above", "value": -1},
    )

    def default_timing():
        return [dict(x) for x in DEFAULT_TIMING]

    def diversity_counts(history_timings, new_timing):
        return True, ""

TIMEFRAME_POOL = ["15m", "1h", "4h"]
DEFAULT_TF = "1h"
TF_ALIASES = {
    "15min": "15m",
    "15m": "15m",
    "15minute": "15m",
    "15minutes": "15m",
    "1h": "1h",
    "1hour": "1h",
    "60m": "1h",
    "4h": "4h",
    "4hour": "4h",
    "240m": "4h",
}
MIN_EVALS_PER_COMBO = 8
MIN_DISTINCT_TIMING_FPS = 3

FAMILY_ORDER = ["xu_long", "xd_short", "pb_long", "pb_short"]
# Soft: family/symbol gated by explore depth. Hard: position geometry.
LOCK_KEYS_HARD = ("held", "xwin", "fast", "slow", "z", "atr", "hold")
LOCK_KEYS_SOFT = ("family", "symbol")
GEO_RESET_KEYS = ("held", "xwin", "atr", "hold")  # z optional; cleared on TF switch


def resolve_tf(value, default=None):
    """Map aliases → canonical pool id; unknown → default or DEFAULT_TF."""
    raw = str(value or "").strip().lower()
    if not raw:
        return default if default is not None else DEFAULT_TF
    if raw in TF_ALIASES:
        return TF_ALIASES[raw]
    # tolerate "15MIN" / spaces already lowered
    if raw in TIMEFRAME_POOL:
        return raw
    return default if default is not None else DEFAULT_TF


def combo_key(tf, family):
    return "%s|%s" % (resolve_tf(tf), str(family or "").strip())


def timing_fingerprint(timing):
    rows = []
    for t in timing or []:
        if not isinstance(t, dict):
            continue
        rows.append("%s|%s|%s|%s" % (
            t.get("factor"), t.get("operator"), t.get("value"), t.get("window"),
        ))
    return tuple(sorted(rows))


def fingerprint_key(fp):
    """Stable string for sets/JSON."""
    if isinstance(fp, (list, tuple)):
        return "||".join(str(x) for x in fp)
    return str(fp)


def ensure_combo_slot(state, key):
    combos = state.setdefault("combos", {})
    slot = combos.get(key)
    if not isinstance(slot, dict):
        slot = {"evals": 0, "timing_fps": [], "depth_ok": False}
        combos[key] = slot
    fps = slot.get("timing_fps")
    if isinstance(fps, set):
        slot["timing_fps"] = list(fps)
    elif not isinstance(fps, list):
        slot["timing_fps"] = []
    return slot


def depth_ok_for_slot(slot):
    if not isinstance(slot, dict):
        return False
    fps = slot.get("timing_fps") or []
    n_fp = len(fps) if not isinstance(fps, set) else len(fps)
    return (
        int(slot.get("evals") or 0) >= MIN_EVALS_PER_COMBO
        and n_fp >= MIN_DISTINCT_TIMING_FPS
    )


def update_combo_explore(state, recipe, group_diverse=True):
    """Record one refine eval toward (tf|family) explore depth."""
    tf = resolve_tf((recipe or {}).get("timeframe"))
    fam = str((recipe or {}).get("family") or "").strip()
    key = combo_key(tf, fam)
    slot = ensure_combo_slot(state, key)
    if group_diverse:
        slot["evals"] = int(slot.get("evals") or 0) + 1
        fp = fingerprint_key(timing_fingerprint((recipe or {}).get("timing")))
        fps = list(slot.get("timing_fps") or [])
        if fp not in fps:
            fps.append(fp)
        slot["timing_fps"] = fps
    slot["depth_ok"] = depth_ok_for_slot(slot)
    state["last_group_diverse"] = bool(group_diverse)
    return key, slot


def may_switch_family(state, recipe):
    key = combo_key(
        (recipe or {}).get("timeframe"),
        (recipe or {}).get("family"),
    )
    slot = (state.get("combos") or {}).get(key) or {}
    return bool(slot.get("depth_ok"))


def may_switch_tf(state, recipe):
    return may_switch_family(state, recipe)


def next_tf(state, current_tf):
    tried = list(state.get("tf_tried") or [])
    cur = resolve_tf(current_tf)
    if cur not in tried:
        tried.append(cur)
    state["tf_tried"] = tried
    tried_set = set(tried)
    for tf in TIMEFRAME_POOL:
        if tf not in tried_set:
            return tf
    return None


def next_family(state, current_family):
    tried = list(state.get("families_tried") or [])
    cur = str(current_family or "").strip()
    if cur and cur not in tried:
        tried.append(cur)
    state["families_tried"] = tried
    tried_set = set(tried)
    for fam in FAMILY_ORDER:
        if fam not in tried_set:
            return fam
    return None


def switch_tf_contract(state, recipe, new_tf):
    """Build a new research contract on another TF; clear inherited geometry."""
    new_tf = resolve_tf(new_tf)
    seed = {
        "family": (recipe or {}).get("family"),
        "symbol": (recipe or {}).get("symbol"),
        "timeframe": new_tf,
        "timing": default_timing(),
    }
    # Explicitly omit held/xwin/atr/hold — LLM must recompute.
    state["lock_recipe"] = None
    state["stalled"] = 0
    state["micro_done"] = False
    state["adjusts"] = 0
    tried = list(state.get("tf_tried") or [])
    old_tf = resolve_tf((recipe or {}).get("timeframe"))
    if old_tf and old_tf not in tried:
        tried.append(old_tf)
    if new_tf not in tried:
        tried.append(new_tf)
    state["tf_tried"] = tried
    state["tf"] = new_tf
    prompt = (
        "新周期为%s，请根据该周期波动与K线密度重新计算 held/xwin/atr/hold"
        "（例如15m持有根数应小于1h），禁止直接复制上一周期的整数。"
        "输出完整 recipe（含 timeframe=%s）。" % (new_tf, new_tf)
    )
    return {
        "action": "new_contract",
        "recipe_seed": seed,
        "prompt": prompt,
        "from_tf": old_tf,
        "to_tf": new_tf,
        "phase": "cross_tf_contract",
        "note_zh": "跨周期验证；几何已重置",
    }


def switch_family(state, recipe):
    if not may_switch_family(state, recipe):
        return {
            "action": "reject_family_switch",
            "reason": "explore_depth_not_met",
            "message_zh": (
                "家族已锁：当前周期+家族须完成最小探索深度"
                "（≥%d 次评估且 ≥%d 种 timing 指纹）后方可换族。"
                % (MIN_EVALS_PER_COMBO, MIN_DISTINCT_TIMING_FPS)
            ),
        }
    nxt = next_family(state, (recipe or {}).get("family"))
    if not nxt:
        return {"action": "close", "reason": "evidence_exhausted"}
    state["stalled"] = 0
    state["micro_done"] = False
    state["adjusts"] = 0
    state["lock_recipe"] = None
    old = str((recipe or {}).get("family") or "")
    return {
        "action": "switch_family",
        "family": nxt,
        "from_family": old,
        "timing": default_timing(),
        "phase": "family_switch",
        "timeframe": resolve_tf((recipe or {}).get("timeframe")),
        "symbol": (recipe or {}).get("symbol"),
    }


def try_switch_family_or_close(state, recipe):
    if not may_switch_family(state, recipe):
        return {
            "action": "refine_timing_diverse",
            "reason": "explore_depth_not_met",
        }
    return switch_family(state, recipe)


def is_s1(result):
    if not isinstance(result, dict):
        return True
    if result.get("stage") == "S1_n":
        return True
    try:
        n = int(result.get("n") or 0)
    except Exception:
        n = 0
    return n < 30


def on_eval_done(state, recipe, result, group_diverse=None):
    """After one Mac/VPS eval: update explore depth; decide refine / TF / family."""
    if group_diverse is None:
        group_diverse = bool(state.get("last_group_diverse", True))
        # Prefer live diversity gate on current timing vs history
        hist = list(state.get("timing_history") or [])
        cur = list((recipe or {}).get("timing") or [])
        if cur:
            counts, _warn = diversity_counts(hist, cur)
            group_diverse = bool(counts)
    key, slot = update_combo_explore(state, recipe, group_diverse=group_diverse)
    # Append timing history for §4.3
    cur_timing = list((recipe or {}).get("timing") or [])
    if cur_timing:
        hist = list(state.get("timing_history") or [])
        hist.append(cur_timing)
        state["timing_history"] = hist[-12:]

    s1 = is_s1(result)
    if s1 and not slot.get("depth_ok"):
        return {
            "action": "refine_timing_diverse",
            "combo": key,
            "slot": {
                "evals": slot.get("evals"),
                "timing_fps_n": len(slot.get("timing_fps") or []),
                "depth_ok": False,
            },
            "explore_hint_zh": (
                "证据不足：同一周期+家族须≥%d次评估且≥%d种不同 timing 指纹后，"
                "方可换周期/换族或结案。请换不同合法 timing 原子组合。"
                % (MIN_EVALS_PER_COMBO, MIN_DISTINCT_TIMING_FPS)
            ),
        }
    if s1 and slot.get("depth_ok"):
        nxt = next_tf(state, (recipe or {}).get("timeframe"))
        if nxt:
            return switch_tf_contract(state, recipe, nxt)
        return try_switch_family_or_close(state, recipe)
    return {
        "action": "continue",
        "combo": key,
        "slot": {
            "evals": slot.get("evals"),
            "timing_fps_n": len(slot.get("timing_fps") or []),
            "depth_ok": bool(slot.get("depth_ok")),
        },
    }


def explore_actions_for_critique(state, recipe, stage):
    """Hints for timing_stage.build_critique."""
    key = combo_key(
        (recipe or {}).get("timeframe"),
        (recipe or {}).get("family"),
    )
    slot = ensure_combo_slot(state, key) if state is not None else {}
    depth_ok = bool(slot.get("depth_ok"))
    out = {
        "combo": key,
        "evals": int(slot.get("evals") or 0),
        "timing_fps_n": len(slot.get("timing_fps") or []),
        "min_evals": MIN_EVALS_PER_COMBO,
        "min_timing_fps": MIN_DISTINCT_TIMING_FPS,
        "depth_ok": depth_ok,
        "actions_allowed": [],
    }
    if stage == "S1_n":
        if not depth_ok:
            out["actions_allowed"] = ["refine_timing_diverse"]
            out["forbid_extra"] = ["switch_tf", "switch_family", "close", "loosen_timing"]
        else:
            out["actions_allowed"] = ["switch_tf", "switch_family", "close"]
            out["forbid_extra"] = ["loosen_timing"]
    else:
        out["actions_allowed"] = ["refine_timing"]
        out["forbid_extra"] = ["loosen_timing"]
    return out


def empty_lane_explore_state(tf=None):
    tf = resolve_tf(tf or DEFAULT_TF)
    return {
        "tf": tf,
        "combos": {},
        "tf_tried": [tf],
        "families_tried": [],
        "timing_history": [],
        "last_group_diverse": True,
    }
