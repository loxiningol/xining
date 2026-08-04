#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Find feature separator for Mar26 hardstop vs other cci90+z03 trades."""
from __future__ import print_function

import json
import sys

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

fr = d._frame("CL-USDT-SWAP", "15m")
pkg = json.load(open("/root/auto_trade/dual_engine/frost2_cl_entry_r1_package.json"))
loser_times = set(x["entry_time"] for x in pkg["losers"])
winner_times = set(x["entry_time"] for x in pkg["winners_sample"])

dsl = {
    "key": "probe_sep_z03",
    "name": "p",
    "direction": "short",
    "entry": {"all": [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 54.0}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.03}},
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
book = {
    "symbol": "CL-USDT-SWAP", "timeframe": "15m", "direction": "short",
    "logic_class": "exhaustion_fade", "thesis": "p", "title": "p",
    "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"),
    "gate_mode": "frost2", "source": "p",
}
trades = d._backtest(book["dsl"], "CL-USDT-SWAP", "15m", "observed_base").get("trades") or []
keys = [
    "rsi14", "z20", "cci", "atr14", "macd_stick", "k", "d", "j",
    "h1_slope4", "ret1", "accel", "close", "ema16", "prev_high20",
]
print("n", len(trades))
rows = []
for t in trades:
    ei = int(t["entry_index"])
    row = fr.iloc[ei]
    feats = {k: float(row[k]) for k in keys if k in fr.columns}
    feats["pnl"] = float(t.get("pnl_ratio") or 0)
    feats["entry_time"] = t["entry_time"]
    feats["hard"] = bool(t.get("stop_loss") or feats["pnl"] < -0.15)
    feats["is_winner_orig"] = t["entry_time"] in winner_times
    # derived
    feats["dist_ema16"] = feats["ema16"] - feats["close"]
    feats["dist_prev_high"] = feats["prev_high20"] - feats["close"]
    feats["dist_prev_high_atr"] = feats["dist_prev_high"] / max(feats["atr14"], 1e-9)
    feats["dist_ema16_atr"] = feats["dist_ema16"] / max(feats["atr14"], 1e-9)
    rows.append(feats)
    print(
        t["entry_time"], "pnl", round(feats["pnl"], 4), "hard", feats["hard"],
        "z", round(feats["z20"], 4), "cci", round(feats["cci"], 2),
        "macd", round(feats["macd_stick"], 4), "atr", round(feats["atr14"], 4),
        "j", round(feats["j"], 2), "k", round(feats["k"], 2),
        "h1", round(feats["h1_slope4"], 5),
        "dPrevATR", round(feats["dist_prev_high_atr"], 3),
        "dEmaATR", round(feats["dist_ema16_atr"], 3),
    )

hard = [r for r in rows if r["hard"]][0]
others = [r for r in rows if not r["hard"]]
print("\nHard vs others ranges:")
for k in [
    "z20", "cci", "macd_stick", "atr14", "j", "k", "d", "h1_slope4",
    "rsi14", "dist_prev_high_atr", "dist_ema16_atr", "ret1", "accel",
]:
    vals = [r[k] for r in others]
    print(
        k, "hard", round(hard[k], 6),
        "oth_min", round(min(vals), 6), "oth_max", round(max(vals), 6),
        "separable_lt", hard[k] > max(vals),
        "separable_gt", hard[k] < min(vals),
    )

# Try: macd between -0.0635 and 0? No that keeps hard.
# Try combinations of 2 conditions via grid on soft book

# Also test: keep z>0.1 for "quality" OR... can't.

# What if we use cci>90, z>0.03, AND atr14 < 0.4865 OR j < 56 — still AND only.

# Idea: raise cci for soft-z path? Mar26 cci=127 - high. Doesn't help.

# Idea: require closer to ema (dist_ema16_atr small)? hard=0.243
print("\nTry friction on z03 despite hardstop:")
f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
q = f2.quick_suite(book)
fr_m = ((q.get("extreme_friction") or {}).get("metrics") or {})
print("quick", q.get("quick_pass"), "dest", (q.get("logic_destruction") or {}).get("pass"),
      "fp", (q.get("base_metrics") or {}).get("fold_positive"),
      "folds", (q.get("base_metrics") or {}).get("folds"),
      "fr_sh", fr_m.get("sharpe"), "failed", q.get("failed_step"))

# Alternative: max_hold shorter to avoid hard stop? Mar26 exit at 止损 - hold bars?
print("Mar26 exit", [t for t in trades if t["entry_time"].startswith("2026-03-26")][0])
