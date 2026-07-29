#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import print_function
import copy
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, "/root")
import auto_trade_strategy_dsl as dsl_mod
import auto_trade_dual_engine_factory as dual
from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen

pack = json.load(open("/root/strategy_atr_squeeze_structural_breakout.json"))
base = pack["dsl_long"]
fr = dual._frame("BTC-USDT-SWAP", "15m")
print("bars", len(fr), flush=True)


def make(ath, volz, trail, hold, swing=20):
    d = copy.deepcopy(base)
    d["key"] = "probe_%d" % (abs(hash((ath, volz, trail, hold, swing))) % 100000000)
    d["name"] = "probe"
    d["max_hold_bars"] = hold
    for leaf in d["entry"]["all"]:
        if leaf["id"] == "e_atr_compress":
            leaf["right"] = {"feature": "close", "scale": ath}
        if leaf["id"] == "e_vol_confirm":
            leaf["right"] = {"value": volz}
    for leaf in d["exit"]["any"]:
        if leaf.get("exit_op") == "atr_trailing":
            leaf["n_atr"] = trail
        if leaf.get("exit_op") == "swing_extreme":
            leaf["lookback"] = swing
    return d


rows = []
grid = list(itertools.product(
    [0.0011, 0.0013, 0.0015, 0.0018, 0.0020],
    [0.4, 0.6, 0.9, 1.2],
    [2.5, 3.0, 3.5, 4.0],
    [18, 24, 36],
    [12, 20],
))
print("grid", len(grid), flush=True)
for i, (ath, volz, trail, hold, swing) in enumerate(grid):
    d = make(ath, volz, trail, hold, swing)
    try:
        defn = dsl_mod.validate_strategy(d)
    except Exception:
        continue
    bt = dsl_mod.backtest_dsl(fr, defn, stop_loss_pct=0.009)
    trades = bt.get("trades") or []
    pnls = [float(t.get("pnl_ratio") or 0) for t in trades]
    if len(pnls) < 8:
        continue
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p <= 0]
    if not wins or not losses:
        continue
    pay = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
    wr = len(wins) / float(len(pnls))
    l1 = run_micro_screen(
        definition=defn,
        frame=fr,
        backtest_fn=lambda frm, dd, defn=defn: dsl_mod.backtest_dsl(frm, defn, stop_loss_pct=0.009),
        seed=42,
    )
    rows.append({
        "ath": ath, "volz": volz, "trail": trail, "hold": hold, "swing": swing,
        "n": len(pnls), "pay": round(pay, 3), "wr": round(wr, 3),
        "l1": bool(l1.get("pass")),
        "l1_pay": round(float((l1.get("metrics") or {}).get("payoff_ratio") or 0), 3),
        "l1_n": (l1.get("metrics") or {}).get("filled_entries"),
        "l1_why": (l1.get("reject_reasons") or [None])[0],
    })
    if (i + 1) % 20 == 0:
        print("progress", i + 1, "kept", len(rows), flush=True)

rows.sort(key=lambda r: (r["l1"], r["pay"], r["n"]), reverse=True)
out = {
    "any_l1_pass": sum(1 for r in rows if r["l1"]),
    "full_pay_ge_1.2": sum(1 for r in rows if r["pay"] >= 1.2),
    "full_pay_ge_1.5": sum(1 for r in rows if r["pay"] >= 1.5),
    "top": rows[:30],
    "best_full": sorted([r for r in rows if r["n"] >= 15], key=lambda r: r["pay"], reverse=True)[:25],
    "l1_passers": [r for r in rows if r["l1"]][:20],
}
Path("/root/auto_trade/dual_engine/workflow_v2/atr_squeeze_grid.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2)
)
print("DONE", out["any_l1_pass"], out["full_pay_ge_1.2"], out["full_pay_ge_1.5"], flush=True)
for r in out["top"][:12]:
    print(r, flush=True)
for r in out["l1_passers"][:10]:
    print("L1PASS", r, flush=True)
