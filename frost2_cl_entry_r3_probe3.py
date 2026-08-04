#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Identify trades added by softening z under cci>90."""
from __future__ import print_function

import json
import sys

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

fr = d._frame("CL-USDT-SWAP", "15m")
pkg = json.load(open("/root/auto_trade/dual_engine/frost2_cl_entry_r1_package.json"))
loser_times = [x["entry_time"] for x in pkg["losers"]]
winner_times = [x["entry_time"] for x in pkg["winners_sample"]]


def make(z):
    dsl = {
        "key": "probe_delta_z_%s" % str(z).replace(".", "p"),
        "name": "p",
        "direction": "short",
        "entry": {"all": [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 54.0}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z)}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
            {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 90.0}},
        ]},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
        "max_hold_bars": 12,
    }
    return {
        "symbol": "CL-USDT-SWAP", "timeframe": "15m", "direction": "short",
        "logic_class": "exhaustion_fade", "thesis": "p", "title": "p",
        "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"),
        "gate_mode": "frost2", "source": "p",
    }


def trades_at(z):
    bt = d._backtest(make(z)["dsl"], "CL-USDT-SWAP", "15m", "observed_base")
    return bt.get("trades") or []


def feats(ei):
    row = fr.iloc[int(ei)]
    out = {}
    for k in [
        "rsi14", "z20", "cci", "atr14", "macd_stick", "k", "d", "j",
        "h1_slope4", "close", "ema16", "prev_high20", "ret1", "accel",
    ]:
        if k in fr.columns:
            out[k] = float(row[k])
    return out


base = {t["entry_time"]: t for t in trades_at(0.1)}
soft = {t["entry_time"]: t for t in trades_at(0.03)}
added = sorted(set(soft) - set(base))
print("base", len(base), "soft", len(soft), "added", added)
for et in added:
    t = soft[et]
    ei = t.get("entry_index")
    print(
        "ADD", et, "pnl", round(float(t.get("pnl_ratio") or 0), 4),
        "stop", bool(t.get("stop_loss")),
        "exit", t.get("exit_type"),
        feats(ei),
    )

# Try surgical filters that hit ONLY Mar26 among added
mar = "2026-03-26 10:00:00"
print("\nSurgical tests on soft z=0.03:")
# For each candidate extra, check which of the 3 added survive + total metrics

extras_list = [
    ("macd_gt_m07", {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": -0.07}}),
    ("macd_gt_m065", {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": -0.065}}),
    ("macd_gt_m063", {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": -0.063}}),
    ("atr_lt_0.49", {"left": {"feature": "atr14"}, "op": "lt", "right": {"value": 0.49}}),
    ("atr_lt_0.486", {"left": {"feature": "atr14"}, "op": "lt", "right": {"value": 0.486}}),
    ("h1_lt_0.0086", {"left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": 0.0086}}),
    ("h1_lt_0.00855", {"left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": 0.00855}}),
    ("j_lt_56.1", {"left": {"feature": "j"}, "op": "lt", "right": {"value": 56.1}}),
    ("j_ne_via_lt56p0", {"left": {"feature": "j"}, "op": "lt", "right": {"value": 56.0}}),
    ("k_lt_71.7", {"left": {"feature": "k"}, "op": "lt", "right": {"value": 71.7}}),
    ("k_lt_71.66", {"left": {"feature": "k"}, "op": "lt", "right": {"value": 71.66}}),
]


def make_extra(tag, extra, z=0.03):
    dsl = {
        "key": "probe_surg_%s" % tag.replace(".", "p").replace("-", "p"),
        "name": "p",
        "direction": "short",
        "entry": {"all": [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 54.0}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z)}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
            {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 90.0}},
            extra,
        ]},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
        "max_hold_bars": 12,
    }
    return {
        "symbol": "CL-USDT-SWAP", "timeframe": "15m", "direction": "short",
        "logic_class": "exhaustion_fade", "thesis": "p", "title": "p",
        "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"),
        "gate_mode": "frost2", "source": "p",
    }


for tag, extra in extras_list:
    bt = d._backtest(make_extra(tag, extra)["dsl"], "CL-USDT-SWAP", "15m", "observed_base")
    trades = bt.get("trades") or []
    m = d._metrics_from_trades(trades)
    times = set(t.get("entry_time") for t in trades)
    hard = sum(1 for t in trades if t.get("stop_loss") or float(t.get("pnl_ratio") or 0) < -0.15)
    blocked = sum(1 for t in loser_times if t not in times)
    kept = sum(1 for t in winner_times if t in times)
    add_kept = [et for et in added if et in times]
    print(
        tag, "tr", m["trades"], "fp", m["fold_positive"], "/", m["folds"],
        "hard", hard, "blocked", blocked, "kept", kept,
        "added_kept", add_kept, "mar_in", mar in times,
        "sh", m["sharpe"],
    )
