#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tiny RAM-safe retune for atr squeeze on BTC/ETH/SOL 15m."""
from __future__ import print_function
import gc
import json
import sys
from pathlib import Path

sys.path.insert(0, "/root")
import auto_trade_strategy_dsl as dsl_mod
import auto_trade_dual_engine_factory as dual
from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen


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
    del bt, trades, pnls, wins, losses
    return {
        "n": len(getattr(defn, "key", "") and []) or None,
        "n_trades": None,
        "pay": round(pay, 3),
        "wr": round(wr, 3),
        "l1": bool(l1.get("pass")),
        "l1_pay": round(float((l1.get("metrics") or {}).get("payoff_ratio") or 0), 3),
        "l1_n": (l1.get("metrics") or {}).get("filled_entries"),
        "l1_why": (l1.get("reject_reasons") or [None])[0],
        "filled": int((l1.get("metrics") or {}).get("filled_entries") or 0),
        "key": d.get("key"),
        "direction": d.get("direction"),
        "_n": None,
    }


def eval_one_fixed(fr, d):
    defn = dsl_mod.validate_strategy(d)
    bt = dsl_mod.backtest_dsl(fr, defn, stop_loss_pct=0.009)
    trades = list(bt.get("trades") or [])
    n = len(trades)
    pnls = [float(t.get("pnl_ratio") or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p <= 0]
    pay = (sum(wins) / len(wins)) / (sum(losses) / len(losses)) if wins and losses else 0.0
    wr = len(wins) / float(n) if n else 0.0
    l1 = run_micro_screen(
        definition=defn,
        frame=fr,
        backtest_fn=lambda frm, dd, defn=defn: dsl_mod.backtest_dsl(frm, defn, stop_loss_pct=0.009),
        seed=42,
    )
    row = {
        "n": n,
        "pay": round(pay, 3),
        "wr": round(wr, 3),
        "l1": bool(l1.get("pass")),
        "l1_pay": round(float((l1.get("metrics") or {}).get("payoff_ratio") or 0), 3),
        "l1_n": (l1.get("metrics") or {}).get("filled_entries"),
        "l1_why": (l1.get("reject_reasons") or [None])[0],
        "key": d.get("key"),
        "direction": d.get("direction"),
    }
    del bt, trades, pnls, wins, losses, l1, defn
    gc.collect()
    return row


def build(symbol, direction, lag, volz, trail, hold, turnup, ath, swing):
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
    if turnup:
        leaves.append({"id": "e_atr_turn", "left": {"feature": "atr14"}, "op": "gt",
                       "right": {"feature": "atr14", "offset": 1}})
    if ath is not None:
        leaves.append({"id": "e_atr_lvl", "left": {"feature": "atr14"}, "op": "lt",
                       "right": {"feature": "close", "scale": ath}})
    leaves.extend([br,
                   {"id": "e_vol", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": volz}},
                   mom])
    exits = [{"id": "x_atr", "exit_op": "atr_trailing", "n_atr": trail,
              "atr_period": 14, "role": "take_profit"}]
    if swing:
        exits.append({"id": "x_sw", "exit_op": "swing_extreme", "lookback": 20,
                      "role": "invalidation"})
    key = ("t_%s_%s_l%d_v%s_t%s" % (
        symbol.split("-")[0].lower(), direction, lag,
        str(volz).replace(".", ""), str(trail).replace(".", "")))[:90]
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key,
        "name": "atr squeeze retune tiny",
        "direction": direction,
        "timeframe": "15m",
        "supported_instruments": [symbol],
        "max_hold_bars": hold,
        "entry": {"all": leaves},
        "exit": {"any": exits},
        "description": "tiny retune",
    }


def main():
    # Keep only one frame in memory at a time.
    configs = []
    for direction in ("long", "short"):
        for lag in (5, 8):
            for volz in (0.8, 1.2):
                for trail in (2.5, 4.0):
                    for turnup in (True,):
                        for ath in (None, 0.0015):
                            for swing in (False, True):
                                configs.append((direction, lag, volz, trail, 24, turnup, ath, swing))
    print("configs_per_symbol", len(configs), flush=True)
    rows = []
    for symbol in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"):
        fr = dual._frame(symbol, "15m")
        print("symbol", symbol, "bars", len(fr), flush=True)
        for i, cfg in enumerate(configs):
            direction, lag, volz, trail, hold, turnup, ath, swing = cfg
            d = build(symbol, direction, lag, volz, trail, hold, turnup, ath, swing)
            try:
                row = eval_one_fixed(fr, d)
            except Exception as exc:
                print("ERR", d["key"], exc, flush=True)
                continue
            row.update({
                "symbol": symbol, "lag": lag, "volz": volz, "trail": trail,
                "turnup": turnup, "ath": ath, "swing": swing,
            })
            if row["n"] >= 5:
                rows.append(row)
            if row.get("l1") or row.get("pay", 0) >= 1.3:
                print("HIT", row, flush=True)
            if (i + 1) % 8 == 0:
                print("progress", symbol, i + 1, "kept", len(rows), flush=True)
        del fr
        gc.collect()
        print("done_symbol", symbol, "kept", len(rows), flush=True)

    rows.sort(key=lambda r: (r["l1"], r["pay"], r["n"]), reverse=True)
    out = {
        "any_l1": sum(1 for r in rows if r["l1"]),
        "pay_ge_1_2": sum(1 for r in rows if r["pay"] >= 1.2),
        "top": rows[:30],
        "l1_passers": [r for r in rows if r["l1"]][:20],
    }
    Path("/root/auto_trade/dual_engine/workflow_v2/atr_squeeze_retune.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2)
    )
    print("RETUNE_DONE", out["any_l1"], out["pay_ge_1_2"], flush=True)
    for r in out["top"][:12]:
        print("TOP", r, flush=True)


if __name__ == "__main__":
    main()
