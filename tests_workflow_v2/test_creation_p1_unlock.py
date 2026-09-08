# -*- coding: utf-8 -*-
"""P1 invent unlock smoke tests (no pytest required)."""
from __future__ import print_function

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dual_engine_workflow_v2 import creation_route_registry as reg
from dual_engine_workflow_v2 import creation_contract as cc
from dual_engine_workflow_v2 import thin_create_policy as tcp
from dual_engine_workflow_v2 import semantic_executor as se


def test_no_p0_a0_gate():
    got = reg.active_routes()
    assert got.get("p0_a0_mode") is False
    assert got.get("invent_gate") == "section_4_active_pool"
    assert "ema_osc" in got["active"]
    assert "channel" in got["active"], got
    assert "ma_family" in got["active"], got
    assert "momentum" in got["active"], got
    assert "mtf_filter" in got["active"], got
    # vol_confirm active only when true_volume + volume_ratio caps are on
    if reg.cap_ok("true_volume") and reg.cap_ok("volume_ratio_atom"):
        assert "vol_confirm" in got["active"], got
    else:
        assert "vol_confirm" not in got["active"]
        st = reg.route_status("vol_confirm")
        assert st["frozen"]
        assert "true_volume" in (st.get("missing_caps") or []) or st.get("missing_builder")


def test_channel_contract_ok():
    recipe = {
        "family": "ch_long", "symbol": "BTC-USDT-SWAP",
        "timeframe": "1h", "route": "channel",
        "dwin": 20, "atr": 2.1, "hold": 12,
        "timing": [
            {"factor": "skdj_diff", "operator": "above", "value": -20},
            {"factor": "macd_hist", "operator": "above", "value": -1},
        ],
    }
    out, err = cc.normalize_contract(recipe, skip_data_gate=True)
    assert err is None, err
    assert out["route"] == "channel"
    assert "dwin" in cc.contract_identity(out) or "20" in cc.contract_identity(out)


def test_mtf_requires_unlock_and_route():
    recipe = {
        "family": "xu_long", "symbol": "BTC-USDT-SWAP",
        "timeframe": "1h", "route": "ema_osc", "filter_tfs": ["4h"],
        "held": 28, "xwin": 8.0, "atr": 2.1, "hold": 10,
        "timing": [
            {"factor": "skdj_diff", "operator": "above", "value": -20},
            {"factor": "macd_hist", "operator": "above", "value": -1},
        ],
    }
    _out, err = cc.normalize_contract(recipe, skip_data_gate=True)
    assert err == "filter_tfs_only_on_mtf_filter", err
    recipe2 = dict(recipe, route="mtf_filter")
    out2, err2 = cc.normalize_contract(recipe2, skip_data_gate=True)
    assert err2 is None, err2
    assert out2["filter_tfs"] == ["4h"]


def test_atoms_registered():
    assert ("sma", "held") in se.EXECUTABLE_ATOM_KEYS
    assert ("hma", "held") in se.EXECUTABLE_ATOM_KEYS
    assert ("roc", "above") in se.EXECUTABLE_ATOM_KEYS
    assert ("volume_ratio", "above") in se.EXECUTABLE_ATOM_KEYS
    assert ("donchian", "breakout_up") in se.EXECUTABLE_ATOM_KEYS


def test_pick_skips_mtf_when_no_green_filter():
    state = {"tf": "1h", "route_rr_index": 0, "route_cooldowns": {}, "symbol": "ZZZ-NOPE-SWAP"}
    # Force active to include mtf first by monkeypatching
    orig = reg.active_routes
    reg.active_routes = lambda: {"active": ["mtf_filter", "ema_osc"], "n_active": 2}
    try:
        rid = tcp.pick_next_route(state)
        assert rid == "ema_osc", (rid, state.get("mtf_green_skip_n"))
        assert int(state.get("mtf_green_skip_n") or 0) >= 1
    finally:
        reg.active_routes = orig


def test_sma_held_mask():
    import pandas as pd
    df = pd.DataFrame({
        "open": [1, 2, 3, 4, 5],
        "high": [1, 2, 3, 4, 5],
        "low": [1, 2, 3, 4, 5],
        "close": [1.0, 2.0, 3.0, 4.0, 5.0],
        "volume": [10, 10, 10, 10, 10],
    })
    class A(object):
        factor = "sma"
        operator = "held"
        window = 2
        value = None
        status = "supported"
    from dual_engine_workflow_v2.strategy_ir import ATOM_STATUS_SUPPORTED
    A.status = ATOM_STATUS_SUPPORTED
    prepped, _ = se.prepare_factors(df, ir=type("IR", (), {"entry_atoms": [A], "exit_atoms": []})())
    mask = se.evaluate_atom_mask(prepped, A, direction="long")
    assert mask is not None
    assert bool(mask.iloc[-1]) is True


if __name__ == "__main__":
    test_no_p0_a0_gate()
    test_channel_contract_ok()
    test_mtf_requires_unlock_and_route()
    test_atoms_registered()
    test_pick_skips_mtf_when_no_green_filter()
    test_sma_held_mask()
    print("ok")
