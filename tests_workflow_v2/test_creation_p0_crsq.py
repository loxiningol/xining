# -*- coding: utf-8 -*-
"""P0-C/R/S/Q smoke tests (updated for §4.0 active pool, no P0-A0)."""
from __future__ import print_function

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dual_engine_workflow_v2 import creation_route_registry as reg
from dual_engine_workflow_v2 import creation_contract as cc
from dual_engine_workflow_v2 import creation_dimension_policy as cdp
from dual_engine_workflow_v2 import thin_create_policy as tcp
from dual_engine_workflow_v2.timing_stage import build_critique


def test_p0r_active_pool():
    got = reg.active_routes()
    assert "ema_osc" in got["active"], got
    assert got.get("p0_a0_mode") is False
    # channel unlocked in P1-CH when builder+cap ready
    assert "channel" in got["active"], got
    st = reg.route_status("vol_confirm")
    assert st["frozen"]


def test_p0c_contract():
    recipe = {
        "family": "xu_long", "symbol": "BTC-USDT-SWAP",
        "timeframe": "1h", "route": "ema_osc",
        "held": 28, "xwin": 8.0, "atr": 2.1, "hold": 10,
        "timing": [
            {"factor": "skdj_diff", "operator": "above", "value": -20},
            {"factor": "macd_hist", "operator": "above", "value": -1},
        ],
    }
    out, err = cc.normalize_contract(recipe, skip_data_gate=True)
    assert err is None, err
    assert out["exec_tf"] == "1h"
    assert out["route"] == "ema_osc"
    assert out["filter_tfs"] == []
    # filters only on mtf_filter
    bad2 = dict(recipe, filter_tfs=["4h"], route="ema_osc")
    _out3, err3 = cc.normalize_contract(bad2, skip_data_gate=True)
    assert err3 == "filter_tfs_only_on_mtf_filter"


def test_p0s_structure_fp_and_min_evals():
    assert tcp.MIN_EVALS_PER_COMBO == 5
    assert tcp.MIN_DISTINCT_COMBO_FPS == 3
    a = tcp.structure_fingerprint([
        {"factor": "skdj_diff", "operator": "above", "value": -20},
        {"factor": "macd_hist", "operator": "above", "value": -1},
    ])
    b = tcp.structure_fingerprint([
        {"factor": "skdj_diff", "operator": "above", "value": -5},
        {"factor": "macd_hist", "operator": "above", "value": 0},
    ])
    assert a == b  # threshold tweak ≠ new structure
    state = tcp.empty_lane_explore_state("1h")
    assert state["route"] == "ema_osc"
    recipe = {
        "family": "xu_long", "timeframe": "1h", "route": "ema_osc",
        "timing": [
            {"factor": "skdj_diff", "operator": "above", "value": -20},
            {"factor": "macd_hist", "operator": "above", "value": -1},
        ],
    }
    for i in range(5):
        recipe["timing"][0]["value"] = -20 - i
        d = tcp.on_eval_done(state, recipe, {"stage": "S1_n", "n": 10}, group_diverse=True)
    assert d["action"] == "refine_timing_diverse"
    assert state.get("force_extend_left") is not None or d.get("explore_insufficient")


def test_p0q_dimension():
    timing = [
        {"factor": "skdj_k", "operator": "above", "value": 50},
        {"factor": "macd_hist", "operator": "above", "value": 0},
        {"factor": "cci", "operator": "below", "value": -100},
        {"factor": "rsi", "operator": "between", "value": 30},
    ]
    assess = cdp.assess_dimension_stack(timing)
    assert assess["soft_warn"] is True
    state = {"dim_soft_ignore": 3}
    ok, code, _ = cdp.apply_dimension_policy(state, timing)
    assert ok is False and code == "dimension_redundant_hard"
    critique = build_critique(
        {"timing": timing, "route": "ema_osc", "timeframe": "1h"},
        {"stage": "S1_n", "n": 5},
        "id",
        1,
        explore_state={"dim_soft_ignore": 3},
    )
    assert critique.get("reject") == "dimension_redundant_hard"


if __name__ == "__main__":
    test_p0r_active_pool()
    test_p0c_contract()
    test_p0s_structure_fp_and_min_evals()
    test_p0q_dimension()
    print("ok")
