#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fast fold_positive scout (base metrics only) for SOL/XRP/DOGE."""
from __future__ import print_function
import copy
import json
import sys

sys.path.insert(0, "/root")
import auto_trade_strategy_dsl as dsl_mod
import auto_trade_dual_engine_factory as d
import frost_action_run as f


def base_metrics(symbol, tf, direction, entry, exit_any, hold, tag):
    dsl = {
        "key": "scout_%s_%s" % (symbol.split("-")[0].lower(), tag),
        "name": tag,
        "direction": direction,
        "timeframe": tf,
        "supported_instruments": [symbol],
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "max_hold_bars": hold,
        "description": tag,
    }
    dsl = f._ensure_dsl(dsl, symbol, tf)
    definition = dsl_mod.validate_strategy(dsl)
    r = d._backtest(definition, symbol, tf, "observed_base")
    m = d._metrics_from_trades(r.get("trades") or [])
    return m, dsl


def main():
    best = {}
    trials = []
    # SOL
    for rsi_lo in (40, 45, 48, 50, 52):
        for rsi_hi in (55, 58, 62, 68):
            if rsi_lo >= rsi_hi:
                continue
            for hold in (4, 6, 10, 16):
                for use_h1 in (True, False):
                    entry = [
                        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi_lo)}},
                        {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(rsi_hi)}},
                        {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
                    ]
                    if use_h1:
                        entry.insert(0, {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}})
                    # pullback
                    entry.append({"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}})
                    m, dsl = base_metrics(
                        "SOL-USDT-SWAP", "5m", "long", entry,
                        [{"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}}],
                        hold, "sol_%s_%s_h%s_%s" % (rsi_lo, rsi_hi, hold, int(use_h1)),
                    )
                    trials.append(("SOL", m, dsl))
    # XRP shorts
    for rsi_hi in (60, 65, 68, 72, 78):
        for hold in (4, 8, 12, 20):
            for cci in (50, 80, 100, 150):
                entry = [
                    {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi_hi)}},
                    {"left": {"feature": "cci"}, "op": "gt", "right": {"value": float(cci)}},
                    {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
                    {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
                ]
                m, dsl = base_metrics(
                    "XRP-USDT-SWAP", "15m", "short", entry,
                    [{"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 50}}],
                    hold, "xrp_%s_%s_h%s" % (rsi_hi, cci, hold),
                )
                trials.append(("XRP", m, dsl))
    # DOGE
    for rsi_hi in (30, 35, 40, 45, 50):
        for hold in (4, 8, 12, 24):
            entry = [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(rsi_hi)}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema19"}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
            ]
            m, dsl = base_metrics(
                "DOGE-USDT-SWAP", "1h", "long", entry,
                [{"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55}}],
                hold, "doge_%s_h%s" % (rsi_hi, hold),
            )
            trials.append(("DOGE", m, dsl))

    # rank
    ranked = sorted(trials, key=lambda x: (x[1].get("fold_positive") or 0, x[1].get("sharpe") or -99), reverse=True)
    print("TOP20")
    for sym, m, dsl in ranked[:20]:
        print(sym, "fp", m.get("fold_positive"), "folds", m.get("folds"), "tr", m.get("trades"),
              "sharpe", m.get("sharpe"), "wr", round(m.get("win_rate_pct") or 0, 1), "oos", round(m.get("oos_profit") or 0, 3),
              dsl.get("key"))
    # best per family with fp>=5
    for fam in ("SOL", "XRP", "DOGE"):
        rows = [x for x in ranked if x[0] == fam]
        print("BEST", fam, "fp", rows[0][1].get("fold_positive"), "tr", rows[0][1].get("trades"),
              "sharpe", rows[0][1].get("sharpe"), rows[0][2].get("key"))
        best[fam] = {"metrics": rows[0][1], "dsl": rows[0][2]}
    # also any with fp>=8
    ge8 = [x for x in ranked if (x[1].get("fold_positive") or 0) >= 8]
    print("GE8_COUNT", len(ge8))
    for sym, m, dsl in ge8[:10]:
        print("GE8", sym, m, "key", dsl.get("key"))
    open("/root/auto_trade/dual_engine/frost_scout_best.json", "w").write(
        json.dumps(best, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
