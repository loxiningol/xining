#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused retune: ATR decline then turn-up + structural breakout."""
from __future__ import print_function
import json
import sys
from pathlib import Path

sys.path.insert(0, "/root")
import auto_trade_strategy_dsl as dsl_mod
import auto_trade_dual_engine_factory as dual
from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen

SYMBOLS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
TF = "15m"


def eval_one(fr, d):
    defn = dsl_mod.validate_strategy(d)
    bt = dsl_mod.backtest_dsl(fr, defn, stop_loss_pct=0.009)
    trades = bt.get("trades") or []
    pnls = [float(t.get("pnl_ratio") or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p <= 0]
    pay = (sum(wins) / len(wins)) / (sum(losses) / len(losses)) if wins and losses else 0.0
    wr = len(wins) / float(len(pnls)) if pnls else 0.0
    l1 = run_micro_screen(
        definition=defn,
        frame=fr,
        backtest_fn=lambda frm, dd, defn=defn: dsl_mod.backtest_dsl(frm, defn, stop_loss_pct=0.009),
        seed=42,
    )
    return {
        "n": len(pnls),
        "pay": round(pay, 3),
        "wr": round(wr, 3),
        "l1": bool(l1.get("pass")),
        "l1_pay": round(float((l1.get("metrics") or {}).get("payoff_ratio") or 0), 3),
        "l1_n": (l1.get("metrics") or {}).get("filled_entries"),
        "l1_why": (l1.get("reject_reasons") or [None])[0],
        "key": d.get("key"),
        "direction": d.get("direction"),
    }


def build(direction, lag, volz, trail, hold, use_turnup, use_level, ath, use_swing, symbol):
    if direction == "long":
        br = {"id": "e_break", "left": {"feature": "close"}, "op": "cross_above",
              "right": {"feature": "prev_high20"}}
        mom = {"id": "e_mom", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}}
    else:
        br = {"id": "e_break", "left": {"feature": "close"}, "op": "cross_below",
              "right": {"feature": "prev_low20"}}
        mom = {"id": "e_mom", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}}
    leaves = [
        {"id": "e_atr_decl", "left": {"feature": "atr14"}, "op": "lt",
         "right": {"feature": "atr14", "offset": lag}},
    ]
    if use_turnup:
        leaves.append({
            "id": "e_atr_turn", "left": {"feature": "atr14"}, "op": "gt",
            "right": {"feature": "atr14", "offset": 1},
        })
    if use_level:
        leaves.append({
            "id": "e_atr_lvl", "left": {"feature": "atr14"}, "op": "lt",
            "right": {"feature": "close", "scale": ath},
        })
    leaves.extend([br, {"id": "e_vol", "left": {"feature": "vol_z20"}, "op": "gt",
                        "right": {"value": volz}}, mom])
    exits = [{
        "id": "x_atr", "exit_op": "atr_trailing", "n_atr": trail,
        "atr_period": 14, "role": "take_profit",
    }]
    if use_swing:
        exits.append({
            "id": "x_sw", "exit_op": "swing_extreme", "lookback": 20,
            "role": "invalidation",
        })
    key = ("sq_%s_%s_l%d_v%s_t%s_h%d_%s%s%s" % (
        symbol.split("-")[0].lower(), direction, lag,
        str(volz).replace(".", ""), str(trail).replace(".", ""), hold,
        "u" if use_turnup else "n",
        ("a%s" % str(ath).replace(".", "")) if use_level else "ax",
        "s" if use_swing else "o",
    ))[:100]
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key,
        "name": "atr squeeze structural breakout retune",
        "direction": direction,
        "timeframe": TF,
        "supported_instruments": [symbol],
        "max_hold_bars": hold,
        "entry": {"all": leaves},
        "exit": {"any": exits},
        "description": "retune compression_release_structural_breakout",
    }


def main():
    rows = []
    frames = {s: dual._frame(s, TF) for s in SYMBOLS}
    for symbol, fr in frames.items():
        print("symbol", symbol, "bars", len(fr), flush=True)
        for direction in ("long", "short"):
            for lag in (5, 8):
                for volz in (0.6, 1.0, 1.4):
                    for trail in (2.5, 3.5, 4.0):
                        for hold in (24, 36):
                            for use_turnup in (True, False):
                                for use_level, ath in ((False, None), (True, 0.0018), (True, 0.0014)):
                                    for use_swing in (True, False):
                                        d = build(
                                            direction, lag, volz, trail, hold,
                                            use_turnup, use_level, ath, use_swing, symbol,
                                        )
                                        try:
                                            row = eval_one(fr, d)
                                        except Exception as exc:
                                            continue
                                        row["symbol"] = symbol
                                        row["lag"] = lag
                                        row["volz"] = volz
                                        row["trail"] = trail
                                        row["hold"] = hold
                                        row["turnup"] = use_turnup
                                        row["level"] = ath
                                        row["swing"] = use_swing
                                        if row["n"] >= 5:
                                            rows.append(row)
                                            if row["l1"] or row["pay"] >= 1.25:
                                                print("HIT", row, flush=True)
        print("after", symbol, "kept", len(rows), flush=True)

    rows.sort(key=lambda r: (r["l1"], r["pay"], r["n"]), reverse=True)
    out = {
        "any_l1": sum(1 for r in rows if r["l1"]),
        "pay_ge_1_2": sum(1 for r in rows if r["pay"] >= 1.2),
        "pay_ge_1_5": sum(1 for r in rows if r["pay"] >= 1.5),
        "top": rows[:40],
        "l1_passers": [r for r in rows if r["l1"]][:30],
    }
    Path("/root/auto_trade/dual_engine/workflow_v2/atr_squeeze_retune.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2)
    )
    print("RETUNE_DONE", out["any_l1"], out["pay_ge_1_2"], out["pay_ge_1_5"], flush=True)
    for r in out["top"][:15]:
        print("TOP", r, flush=True)


if __name__ == "__main__":
    main()
