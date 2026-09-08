# -*- coding: utf-8 -*-
"""Thin-hub create policy: timeframe pool + explore budget (blueprint §1.3 / §2.1).

Python 3.6 compatible.
"""
from __future__ import print_function

import os

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

# Invent-active pool (narrow until green-pool driven activation).
TIMEFRAME_POOL = ["15m", "1h", "4h"]
# P0-D intentional ocean — preflight / green matrix use this, not only invent pool.
INTENTIONAL_TF_POOL = ["15m", "30m", "1h", "4h", "12h", "1d"]
DEFAULT_TF = "1h"
TF_ALIASES = {
    "15min": "15m",
    "15m": "15m",
    "15minute": "15m",
    "15minutes": "15m",
    "30min": "30m",
    "30m": "30m",
    "30minute": "30m",
    "30minutes": "30m",
    "1h": "1h",
    "1hour": "1h",
    "60m": "1h",
    "4h": "4h",
    "4hour": "4h",
    "240m": "4h",
    "12h": "12h",
    "12hour": "12h",
    "720m": "12h",
    "1d": "1d",
    "1day": "1d",
    "day": "1d",
    "daily": "1d",
    "24h": "1d",
}
# Cross-TF is a new research contract — never merge n/C across TFs.
CROSS_TF_MODE = "new_research_contract"
# P0-S: wider route ocean → lower per-combo budget; structure fps (not thresholds).
MIN_EVALS_PER_COMBO = 5
MIN_DISTINCT_TIMING_FPS = 3
MIN_DISTINCT_COMBO_FPS = MIN_DISTINCT_TIMING_FPS
FORCE_EXTEND_EVALS = 3  # after 5 evals with <3 structure fps, force +3 refine
ROUTE_NO_IMPROVE_LIMIT = 3
# Soft: family/symbol gated by explore depth. Hard: position geometry.
FAMILY_ORDER = [
    "xu_long", "xd_short", "pb_long", "pb_short",
    "ch_long", "ch_short", "ma_long", "ma_short",
]
# Process-wide round-robin so lanes do not all start at ema_osc.
_GLOBAL_ROUTE_RR = {"i": 0}

ROUTE_DEFAULT_FAMILY = {
    "ema_osc": "xu_long",
    "ma_family": "ma_long",
    "channel": "ch_long",
    "vol_confirm": "xu_long",
    "mtf_filter": "xu_long",
    "momentum": "xu_long",
    "mean_revert": "ma_long",
}
LOCK_KEYS_HARD = ("held", "xwin", "dwin", "fast", "slow", "z", "atr", "hold")
LOCK_KEYS_SOFT = ("family", "symbol")
GEO_RESET_KEYS = ("held", "xwin", "dwin", "atr", "hold")  # z optional; cleared on TF switch


def sync_timeframe_pool_from_green(pool=None):
    """Expand invent TIMEFRAME_POOL to intentional TFs that have ≥1 green symbol."""
    global TIMEFRAME_POOL
    if pool is None:
        try:
            from . import creation_tf_data_gate as ctg
            pool = ctg.load_green_pool() or {}
        except Exception:
            pool = {}
    active = []
    by_tf = (pool or {}).get("by_tf") or {}
    for tf in INTENTIONAL_TF_POOL:
        row = by_tf.get(tf) or {}
        n = int(row.get("green_n") or row.get("green") or 0)
        if n > 0:
            active.append(tf)
    # Always keep the historical invent core so a corrupt pool cannot empty the ocean.
    for core in ("15m", "1h", "4h"):
        if core not in active:
            active.append(core)
    # Preserve intentional order
    ordered = [tf for tf in INTENTIONAL_TF_POOL if tf in active]
    if ordered:
        TIMEFRAME_POOL = ordered
    return list(TIMEFRAME_POOL)


def resolve_tf(value, default=None):
    """Map aliases → canonical pool id.

    Unknown non-empty values return ``default`` when provided, else ``None``
    (callers that need a soft fallback should pass ``default=DEFAULT_TF``).
    Empty/missing → ``default`` or ``DEFAULT_TF``.
    """
    raw = str(value or "").strip().lower()
    if not raw:
        return default if default is not None else DEFAULT_TF
    if raw in TF_ALIASES:
        return TF_ALIASES[raw]
    if raw in TIMEFRAME_POOL:
        return raw
    return default  # may be None → reject at recipe boundary


def resolve_recipe_tf(recipe, lane_state=None):
    """Strict TF for a recipe. Returns (tf, None) or (None, error_code)."""
    lane_state = lane_state or {}
    raw = (recipe or {}).get("timeframe")
    if raw in (None, ""):
        raw = lane_state.get("tf") or DEFAULT_TF
    tf = resolve_tf(raw, default=None)
    if not tf or tf not in TIMEFRAME_POOL:
        return None, "tf_not_in_pool"
    return tf, None


def combo_key(tf, family):
    return "%s|%s" % (resolve_tf(tf, default=DEFAULT_TF), str(family or "").strip())


def timing_fingerprint(timing):
    """Full fingerprint including thresholds (legacy / diversity history)."""
    rows = []
    for t in timing or []:
        if not isinstance(t, dict):
            continue
        rows.append("%s|%s|%s|%s" % (
            t.get("factor"), t.get("operator"), t.get("value"), t.get("window"),
        ))
    return tuple(sorted(rows))


def structure_fingerprint(timing):
    """P0-S: factor|operator only — threshold tweaks do not count as new combo."""
    rows = []
    for t in timing or []:
        if not isinstance(t, dict):
            continue
        rows.append("%s|%s" % (t.get("factor"), t.get("operator")))
    return tuple(sorted(rows))


def fingerprint_key(fp):
    """Stable string for sets/JSON."""
    if isinstance(fp, (list, tuple)):
        return "||".join(str(x) for x in fp)
    return str(fp)


def route_tf_key(route, tf):
    return "%s@%s" % (str(route or "ema_osc"), resolve_tf(tf, default=DEFAULT_TF))


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
    """Record one refine eval toward (tf|family|route) explore depth."""
    tf = resolve_tf(
        (recipe or {}).get("exec_tf") or (recipe or {}).get("timeframe")
    )
    fam = str((recipe or {}).get("family") or "").strip()
    route = str((recipe or {}).get("route") or state.get("route") or "ema_osc")
    key = "%s|%s" % (combo_key(tf, fam), route)
    slot = ensure_combo_slot(state, key)
    if group_diverse:
        slot["evals"] = int(slot.get("evals") or 0) + 1
        # Structure fps for depth; threshold-only edits share one structure fp.
        fp = fingerprint_key(structure_fingerprint((recipe or {}).get("timing")))
        fps = list(slot.get("timing_fps") or [])
        if fp not in fps:
            fps.append(fp)
        slot["timing_fps"] = fps
    slot["depth_ok"] = depth_ok_for_slot(slot)
    state["last_group_diverse"] = bool(group_diverse)
    return key, slot


def pick_next_route(state):
    """Forced round-robin over active routes; skip cooled route@tf keys.

    Uses a process-wide RR index so parallel lanes do not all land on ema_osc.
    P3: soft adaptive weights with equal floor (never collapse to one route).
    P1-M: if candidate is mtf_filter and symbol lacks a green larger filter TF,
    skip that slot (do not spend a Kimi round on filter_tf_data_insufficient).
    """
    try:
        from . import creation_route_registry as reg
        active = list((reg.active_routes() or {}).get("active") or [])
    except Exception:
        reg = None
        active = ["ema_osc"]
    if not active:
        active = ["ema_osc"]
    # Expand by equal-floor (+ soft bonus) weights so RR still visits every route.
    try:
        from . import creation_slot_policy as csp
        weights = csp.weights_from_report(csp.load_route_tf_report(), active_routes=active)
        expanded = []
        for rid in active:
            w = float((weights or {}).get(rid, csp.EQUAL_FLOOR) or csp.EQUAL_FLOOR)
            expanded.extend([rid] * max(1, int(round(w))))
        if expanded:
            active_rr = expanded
        else:
            active_rr = list(active)
    except Exception:
        active_rr = list(active)
        weights = {}
    state["route_slot_weights"] = dict(weights or {})
    cool = dict(state.get("route_cooldowns") or {})
    for k in list(cool.keys()):
        cool[k] = int(cool.get(k) or 0) - 1
        if cool[k] <= 0:
            cool.pop(k, None)
    state["route_cooldowns"] = cool
    n = len(active_rr)
    idx = int(_GLOBAL_ROUTE_RR.get("i") or state.get("route_rr_index") or 0) % n
    tf = resolve_tf(state.get("tf") or DEFAULT_TF)
    symbol = str(state.get("symbol") or state.get("lane_symbol") or "").upper()
    skips = list(state.get("mtf_green_skips") or [])
    pick_hist = list(state.get("route_pick_hist") or [])
    route_evals = {}
    try:
        for key, slot in (state.get("combos") or {}).items():
            rid = str(key).split("|")[-1] if "|" in str(key) else ""
            if rid:
                route_evals[rid] = int(route_evals.get(rid) or 0) + int(
                    (slot or {}).get("evals") or 0
                )
    except Exception:
        route_evals = {}
    for step in range(n):
        rid = active_rr[(idx + step) % n]
        key = route_tf_key(rid, tf)
        if int(cool.get(key) or 0) > 0:
            continue
        try:
            from . import creation_slot_policy as csp
            if not csp.launch_share_ok(rid, pick_hist, route_evals.get(rid) or 0):
                continue
        except Exception:
            pass
        if rid == "mtf_filter" and reg is not None and symbol:
            ok_m, chosen, err_m = reg.mtf_filter_green_for_symbol(symbol, tf)
            if not ok_m:
                skips.append({"symbol": symbol, "exec_tf": tf, "error": err_m})
                state["mtf_green_skips"] = skips[-50:]
                state["mtf_green_skip_n"] = int(state.get("mtf_green_skip_n") or 0) + 1
                continue
            state["preferred_filter_tfs"] = list(chosen or [])
        nxt_i = (idx + step + 1) % n
        _GLOBAL_ROUTE_RR["i"] = nxt_i
        state["route_rr_index"] = nxt_i
        state["route"] = rid
        hist = list(state.get("route_pick_hist") or [])
        hist.append(rid)
        state["route_pick_hist"] = hist[-200:]
        return rid
    # Fallback: unique active without mtf if all cooled / skipped
    uniq = []
    for rid in active_rr:
        if rid not in uniq:
            uniq.append(rid)
    for step in range(len(uniq)):
        rid = uniq[(idx + step) % len(uniq)]
        if rid == "mtf_filter":
            continue
        try:
            from . import creation_slot_policy as csp
            if not csp.launch_share_ok(rid, pick_hist, route_evals.get(rid) or 0):
                continue
        except Exception:
            pass
        nxt_i = (idx + step + 1) % max(1, n)
        _GLOBAL_ROUTE_RR["i"] = nxt_i
        state["route_rr_index"] = nxt_i
        state["route"] = rid
        hist = list(state.get("route_pick_hist") or [])
        hist.append(rid)
        state["route_pick_hist"] = hist[-200:]
        return rid
    rid = uniq[idx % len(uniq)] if uniq else "ema_osc"
    nxt_i = (idx + 1) % max(1, n)
    _GLOBAL_ROUTE_RR["i"] = nxt_i
    state["route_rr_index"] = nxt_i
    state["route"] = rid
    hist = list(state.get("route_pick_hist") or [])
    hist.append(rid)
    state["route_pick_hist"] = hist[-200:]
    return rid


def switch_route_contract(state, recipe, new_route=None):
    """New research contract on another invent route; reset family/geometry."""
    tf = resolve_tf(
        (recipe or {}).get("exec_tf") or (recipe or {}).get("timeframe")
        or state.get("tf") or DEFAULT_TF
    )
    symbol = str((recipe or {}).get("symbol") or state.get("symbol") or "").upper()
    state = state if isinstance(state, dict) else {}
    state["tf"] = tf
    state["symbol"] = symbol
    if new_route is None:
        new_route = pick_next_route(state)
    else:
        state["route"] = new_route
    fam = ROUTE_DEFAULT_FAMILY.get(new_route, "xu_long")
    filters = []
    if new_route == "mtf_filter":
        filters = list(state.get("preferred_filter_tfs") or [])
        if not filters:
            try:
                from . import creation_route_registry as reg
                ok_m, chosen, _err = reg.mtf_filter_green_for_symbol(symbol, tf)
                if ok_m:
                    filters = list(chosen or [])
            except Exception:
                filters = []
    seed = {
        "family": fam,
        "symbol": symbol or (recipe or {}).get("symbol"),
        "route": new_route,
        "exec_tf": tf,
        "timeframe": tf,
        "filter_tfs": filters,
        "timing": default_timing(),
    }
    if new_route == "channel":
        seed["dwin"] = 20
        seed["atr"] = 2.2
        seed["hold"] = 12
    elif new_route == "ma_family":
        seed["held"] = 20
        seed["ma_kind"] = "sma"
        seed["atr"] = 2.2
        seed["hold"] = 12
    elif new_route == "vol_confirm":
        seed["held"] = 20
        seed["xwin"] = 8
        seed["atr"] = 2.2
        seed["hold"] = 12
        seed["timing"] = [
            {"factor": "skdj_diff", "operator": "above", "value": -20, "window": 9},
            {"factor": "volume_ratio", "operator": "above", "value": 1.2, "window": 20},
        ]
    elif new_route == "momentum":
        seed["held"] = 20
        seed["xwin"] = 8
        seed["atr"] = 2.2
        seed["hold"] = 12
        seed["timing"] = [
            {"factor": "skdj_diff", "operator": "above", "value": -20, "window": 9},
            {"factor": "roc", "operator": "above", "value": 0, "window": 12},
        ]
    elif new_route == "mean_revert":
        # SMA location spine (not EMA xu); wider target / tighter stop seed.
        seed["family"] = "ma_long"
        seed["ma_kind"] = "sma"
        seed["held"] = 20
        seed["xwin"] = 8.0
        seed["atr"] = 1.6
        seed["hold"] = 12
        seed["timing"] = [
            {"factor": "atr_pct", "operator": "below", "value": 0.3, "window": 200},
            {"factor": "skdj_k", "operator": "cross_up", "value": 20, "window": 9},
            {"factor": "macd_hist", "operator": "above", "value": -1},
        ]
    state["lock_recipe"] = None
    state["stalled"] = 0
    state["micro_done"] = False
    state["adjusts"] = 0
    state["force_extend_left"] = None
    state["families_tried"] = []
    state["route"] = new_route
    state["filter_tfs"] = filters
    old = str((recipe or {}).get("route") or "")
    prompt = (
        "切换创造路线为 %s（家族建议 %s）。请按该路线输出完整 recipe"
        "（含 route、family、timeframe/exec_tf、filter_tfs=%s）。"
        "禁止照搬上一路线几何。禁止文字。"
        % (new_route, fam, json_dumps_filters(filters))
    )
    return {
        "action": "switch_route",
        "recipe_seed": seed,
        "prompt": prompt,
        "from_route": old,
        "to_route": new_route,
        "phase": "switch_route",
        "note_zh": "路线轮询；几何/家族已重置",
    }


def json_dumps_filters(filters):
    try:
        import json
        return json.dumps(list(filters or []), ensure_ascii=False)
    except Exception:
        return "[]"

def note_route_pair_outcome(state, recipe, stage, prev_stage=None):
    """Failure penalty: no stage-up streak → cool that route@exec_tf."""
    route = str((recipe or {}).get("route") or state.get("route") or "ema_osc")
    tf = resolve_tf(
        (recipe or {}).get("exec_tf") or (recipe or {}).get("timeframe")
        or state.get("tf")
    )
    key = route_tf_key(route, tf)
    streaks = dict(state.get("route_no_improve") or {})
    improved = bool(stage and prev_stage and stage != prev_stage)
    # Treat S4_pass / mounted as improve
    if stage in ("S4_pass", "S4_C") and prev_stage and prev_stage != stage:
        improved = True
    if improved:
        streaks[key] = 0
    else:
        streaks[key] = int(streaks.get(key) or 0) + 1
    state["route_no_improve"] = streaks
    if streaks[key] >= ROUTE_NO_IMPROVE_LIMIT:
        try:
            from . import creation_route_registry as reg
            n_active = max(1, int((reg.active_routes() or {}).get("n_active") or 1))
        except Exception:
            n_active = 1
        cool = dict(state.get("route_cooldowns") or {})
        cool[key] = max(int(cool.get(key) or 0), n_active)
        state["route_cooldowns"] = cool
        streaks[key] = 0
        state["route_no_improve"] = streaks
        return {"cooled": key, "slots": cool[key]}
    return {"cooled": None, "streak": streaks.get(key)}


def may_switch_family(state, recipe):
    if str(os.environ.get("KDH_FREE_CREATE") or "1").strip().lower() not in (
        "0", "false", "no", "off",
    ):
        return True
    tf = (recipe or {}).get("exec_tf") or (recipe or {}).get("timeframe")
    fam = (recipe or {}).get("family")
    route = (recipe or {}).get("route") or (state or {}).get("route") or "ema_osc"
    key = "%s|%s" % (combo_key(tf, fam), route)
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
        "route": (recipe or {}).get("route") or state.get("route") or "ema_osc",
        "exec_tf": new_tf,
        "timeframe": new_tf,
        "filter_tfs": [],
        "timing": default_timing(),
    }
    # Explicitly omit held/xwin/atr/hold — LLM must recompute.
    state["lock_recipe"] = None
    state["stalled"] = 0
    state["micro_done"] = False
    state["adjusts"] = 0
    state["force_extend_left"] = None
    tried = list(state.get("tf_tried") or [])
    old_tf = resolve_tf(
        (recipe or {}).get("exec_tf") or (recipe or {}).get("timeframe")
    )
    if old_tf and old_tf not in tried:
        tried.append(old_tf)
    if new_tf not in tried:
        tried.append(new_tf)
    state["tf_tried"] = tried
    state["tf"] = new_tf
    state["exec_tf"] = new_tf
    prompt = (
        "新周期为%s，请按该周期K线密度重算 held/xwin/atr/hold。"
        "注意：同墙钟持有时间在更密周期上对应更多根K线"
        "（例：1h hold=20 墙钟≈20小时 → 15m 约为 hold=80，不是除以4成5）。"
        "为抬高样本密度可缩短墙钟持有，但禁止照抄上一周期整数，也禁止为凑笔数放宽 timing。"
        "输出完整 recipe（含 route、timeframe/exec_tf=%s）。" % (new_tf, new_tf)
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
    nxt = next_family(state, (recipe or {}).get("family"))
    if nxt:
        return switch_family(state, recipe)
    # Families exhausted on this route → rotate invent route (equal floor).
    return switch_route_contract(state, recipe)


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
    """After one Mac/VPS eval: update explore depth; decide refine / TF / family / route."""
    if group_diverse is None:
        group_diverse = bool(state.get("last_group_diverse", True))
        hist = list(state.get("timing_history") or [])
        cur = list((recipe or {}).get("timing") or [])
        if cur:
            counts, _warn = diversity_counts(hist, cur)
            group_diverse = bool(counts)
    key, slot = update_combo_explore(state, recipe, group_diverse=group_diverse)
    cur_timing = list((recipe or {}).get("timing") or [])
    if cur_timing:
        hist = list(state.get("timing_history") or [])
        hist.append(cur_timing)
        state["timing_history"] = hist[-12:]

    evals = int(slot.get("evals") or 0)
    fps_n = len(slot.get("timing_fps") or [])
    s1 = is_s1(result)

    if (
        s1
        and evals >= MIN_EVALS_PER_COMBO
        and fps_n < MIN_DISTINCT_COMBO_FPS
        and state.get("force_extend_left") is None
    ):
        state["force_extend_left"] = int(FORCE_EXTEND_EVALS)
        state["explore_insufficient"] = True

    extend_left = state.get("force_extend_left")
    if extend_left is not None and int(extend_left) > 0 and s1 and not slot.get("depth_ok"):
        state["force_extend_left"] = int(extend_left) - 1
        return {
            "action": "refine_timing_diverse",
            "combo": key,
            "slot": {
                "evals": evals,
                "timing_fps_n": fps_n,
                "depth_ok": False,
            },
            "explore_insufficient": True,
            "force_extend_left": state["force_extend_left"],
            "explore_hint_zh": (
                "探索不充分：已满%d次评估但结构指纹仅%d（阈值微调不计）。"
                "强制续探中（剩余%d）；禁止切路线/周期/换族。"
                % (MIN_EVALS_PER_COMBO, fps_n, state["force_extend_left"])
            ),
        }

    if s1 and not slot.get("depth_ok"):
        return {
            "action": "refine_timing_diverse",
            "combo": key,
            "slot": {
                "evals": evals,
                "timing_fps_n": fps_n,
                "depth_ok": False,
            },
            "explore_hint_zh": (
                "证据不足：同一路线+周期+家族须≥%d次评估且≥%d种不同结构 timing 指纹后，"
                "方可换周期/换族/换路线或结案。阈值微调不计入新指纹。"
                % (MIN_EVALS_PER_COMBO, MIN_DISTINCT_COMBO_FPS)
            ),
        }
    if s1 and slot.get("depth_ok"):
        state["force_extend_left"] = None
        state["explore_insufficient"] = False
        nxt = next_tf(state, (recipe or {}).get("exec_tf") or (recipe or {}).get("timeframe"))
        if nxt:
            return switch_tf_contract(state, recipe, nxt)
        return try_switch_family_or_close(state, recipe)
    return {
        "action": "continue",
        "combo": key,
        "slot": {
            "evals": evals,
            "timing_fps_n": fps_n,
            "depth_ok": bool(slot.get("depth_ok")),
        },
    }


def explore_actions_for_critique(state, recipe, stage):
    """Hints for timing_stage.build_critique."""
    tf = (recipe or {}).get("exec_tf") or (recipe or {}).get("timeframe")
    fam = (recipe or {}).get("family")
    route = (recipe or {}).get("route") or (state or {}).get("route") or "ema_osc"
    key = "%s|%s" % (combo_key(tf, fam), route)
    slot = ensure_combo_slot(state, key) if state is not None else {}
    depth_ok = bool(slot.get("depth_ok"))
    out = {
        "combo": key,
        "route": route,
        "evals": int(slot.get("evals") or 0),
        "timing_fps_n": len(slot.get("timing_fps") or []),
        "min_evals": MIN_EVALS_PER_COMBO,
        "min_timing_fps": MIN_DISTINCT_COMBO_FPS,
        "depth_ok": depth_ok,
        "actions_allowed": [],
        "force_extend_left": (state or {}).get("force_extend_left"),
    }
    if stage == "S1_n":
        if str(os.environ.get("KDH_FREE_CREATE") or "1").strip().lower() not in (
            "0", "false", "no", "off",
        ):
            out["actions_allowed"] = [
                "refine_timing_diverse", "refine_geometry_rr",
                "switch_tf", "switch_family", "switch_route", "close",
            ]
            out["forbid_extra"] = ["loosen_timing"]
        elif not depth_ok:
            out["actions_allowed"] = ["refine_timing_diverse"]
            out["forbid_extra"] = ["switch_tf", "switch_family", "close", "loosen_timing", "switch_route"]
        else:
            out["actions_allowed"] = ["switch_tf", "switch_family", "switch_route", "close"]
            out["forbid_extra"] = ["loosen_timing"]
    elif stage == "S2_hitch":
        out["actions_allowed"] = ["refine_geometry_rr", "refine_timing_structure", "switch_family", "switch_route"]
        out["forbid_extra"] = ["loosen_timing", "value_only_tweak", "widen_atr"]
    else:
        out["actions_allowed"] = ["refine_timing", "refine_geometry_rr", "switch_family", "switch_route"]
        out["forbid_extra"] = ["loosen_timing"]
    return out


def empty_lane_explore_state(tf=None, route=None, symbol=None):
    tf = resolve_tf(tf or DEFAULT_TF)
    st = {
        "tf": tf,
        "exec_tf": tf,
        "symbol": str(symbol or "").upper(),
        "route_cooldowns": {},
        "route_rr_index": int(_GLOBAL_ROUTE_RR.get("i") or 0),
    }
    if route is None:
        try:
            route = pick_next_route(st)
        except Exception:
            route = "ema_osc"
    filters = list(st.get("preferred_filter_tfs") or []) if route == "mtf_filter" else []
    return {
        "tf": tf,
        "exec_tf": tf,
        "route": route,
        "filter_tfs": filters,
        "symbol": st.get("symbol"),
        "combos": {},
        "tf_tried": [tf],
        "families_tried": [],
        "timing_history": [],
        "last_group_diverse": True,
        "route_rr_index": int(st.get("route_rr_index") or _GLOBAL_ROUTE_RR.get("i") or 0),
        "route_cooldowns": {},
        "route_no_improve": {},
        "force_extend_left": None,
        "dim_soft_ignore": 0,
        "preferred_filter_tfs": filters,
        "mtf_green_skip_n": int(st.get("mtf_green_skip_n") or 0),
        "neg_summaries": [],
    }
