# -*- coding: utf-8 -*-
from __future__ import print_function

from dual_engine_workflow_v2 import thin_create_policy as tcp
from dual_engine_workflow_v2 import thin_timing_atoms as tta
from dual_engine_workflow_v2 import timing_stage as ts


def test_resolve_tf_aliases():
    assert tcp.resolve_tf("15min") == "15m"
    assert tcp.resolve_tf("15m") == "15m"
    assert tcp.resolve_tf("1h") == "1h"
    assert tcp.resolve_tf("4h") == "4h"
    assert tcp.resolve_tf("") == tcp.DEFAULT_TF
    assert tcp.resolve_tf(None) == "1h"
    assert "15m" in tcp.TIMEFRAME_POOL
    assert "1h" in tcp.TIMEFRAME_POOL
    assert "4h" in tcp.TIMEFRAME_POOL


def test_timing_fingerprint_and_combo_key():
    a = [
        {"factor": "skdj_diff", "operator": "above", "value": -20, "window": 9},
        {"factor": "macd_hist", "operator": "above", "value": -1},
    ]
    b = list(reversed(a))
    assert tcp.timing_fingerprint(a) == tcp.timing_fingerprint(b)
    assert tcp.combo_key("15min", "xu_long") == "15m|xu_long"


def test_explore_depth_gate_and_cross_tf():
    state = tcp.empty_lane_explore_state("1h")
    recipe = {
        "family": "xu_long",
        "symbol": "NVDA-USDT-SWAP",
        "timeframe": "1h",
        "timing": tta.default_timing(),
    }
    result = {"stage": "S1_n", "n": 5}
    # First eval: not depth_ok → refine
    d0 = tcp.on_eval_done(state, recipe, result, group_diverse=True)
    assert d0["action"] == "refine_timing_diverse"
    assert tcp.may_switch_family(state, recipe) is False

    # Fill explore depth with distinct fingerprints
    variants = [
        [
            {"factor": "skdj_diff", "operator": "above", "value": -20, "window": 9},
            {"factor": "macd_hist", "operator": "above", "value": -1},
        ],
        [
            {"factor": "skdj_kd", "operator": "cross_up", "value": 0, "window": 9},
            {"factor": "macd_hist", "operator": "above", "value": 0},
        ],
        [
            {"factor": "skdj_k", "operator": "below", "value": 40, "window": 9},
            {"factor": "cci", "operator": "below", "value": -100},
        ],
        [
            {"factor": "skdj_diff", "operator": "below", "value": 0, "window": 22},
            {"factor": "macd_dd", "operator": "cross_down", "value": 0},
        ],
        [
            {"factor": "skdj_d", "operator": "above", "value": 50, "window": 9},
            {"factor": "macd_dif", "operator": "above", "value": 0},
        ],
        [
            {"factor": "skdj_kd", "operator": "above", "value": 0, "window": 12},
            {"factor": "cci", "operator": "above", "value": -50},
        ],
        [
            {"factor": "skdj_k", "operator": "above", "value": 55, "window": 22},
            {"factor": "macd_hist", "operator": "below", "value": 0},
        ],
        [
            {"factor": "skdj_diff", "operator": "above", "value": 2, "window": 9},
            {"factor": "kdj_kd", "operator": "cross_up", "value": 0},
        ],
    ]
    last = None
    for i, timing in enumerate(variants):
        recipe["timing"] = timing
        # Force diverse so depth counts
        last = tcp.on_eval_done(state, recipe, result, group_diverse=True)
    assert state["combos"]["1h|xu_long"]["depth_ok"] is True
    assert last["action"] == "new_contract"
    assert last["to_tf"] in ("15m", "4h")
    assert last["phase"] == "cross_tf_contract"
    seed = last["recipe_seed"]
    assert seed["timeframe"] == last["to_tf"]
    assert "held" not in seed
    assert "xwin" not in seed
    assert "atr" not in seed
    assert "hold" not in seed


def test_family_switch_requires_depth():
    state = tcp.empty_lane_explore_state("1h")
    recipe = {
        "family": "xu_long",
        "symbol": "NVDA-USDT-SWAP",
        "timeframe": "1h",
        "timing": tta.default_timing(),
    }
    rej = tcp.switch_family(state, recipe)
    assert rej["action"] == "reject_family_switch"
    # Mark depth ok manually
    key = tcp.combo_key("1h", "xu_long")
    state["combos"][key] = {
        "evals": tcp.MIN_EVALS_PER_COMBO,
        "timing_fps": ["a", "b", "c"],
        "depth_ok": True,
    }
    # Exhaust TF pool so on_eval_done would family-switch
    state["tf_tried"] = list(tcp.TIMEFRAME_POOL)
    ok = tcp.switch_family(state, recipe)
    assert ok["action"] == "switch_family"
    assert ok["family"] in tcp.FAMILY_ORDER
    assert ok["family"] != "xu_long"


def test_default_timing_no_cross_up():
    rows = tcp.default_timing()
    assert rows[0]["factor"] == "skdj_diff"
    assert "cross_up" not in str(rows)


def test_timing_stage_lock_split_and_s1_hint():
    assert "family" in ts.LOCK_KEYS_SOFT
    assert "held" in ts.LOCK_KEYS_HARD
    hint = ts.stage_hint("S1_n")
    assert "略放松" not in hint
    assert "证据不足" in hint
    crit = ts.build_critique(
        {"family": "xu_long", "symbol": "X", "timeframe": "1h", "timing": tta.default_timing()},
        {"stage": "S1_n", "n": 3},
        "id",
        1,
        explore_state=tcp.empty_lane_explore_state("1h"),
    )
    assert "explore" in crit
    assert "refine_timing_diverse" in (crit.get("actions_allowed") or [])
    assert "loosen_timing" in (crit.get("forbid") or [])
