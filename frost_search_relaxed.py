#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quick search for DSLs that can pass relaxed frost gates on SOL/XRP/DOGE."""
from __future__ import print_function
import copy
import itertools
import json
import sys

sys.path.insert(0, "/root")
import auto_trade_dual_engine_factory as d
import frost_action_run as f


def eval_relaxed(book):
    packs = d.run_internal_packs(book, mode="frost_relaxed")
    if not packs.get("ok"):
        return packs
    # add MC soft
    try:
        base = d._backtest(packs["definition"], packs["symbol"], packs["timeframe"], "observed_base")
        mc = f.run_monte_carlo(base.get("trades") or [])
    except Exception as exc:
        mc = {"ok": False, "error": str(exc)}
    packs["monte_carlo"] = mc
    packs["suite"] = {
        "walk_forward_8of10": (packs.get("anti_overfit") or {}).get("pass"),
        "extreme_friction": (packs.get("extreme_friction") or {}).get("pass"),
        "logic_destruction": (packs.get("logic_destruction") or {}).get("pass"),
        "monte_carlo_soft": (mc or {}).get("pass"),
    }
    return packs


def mk(symbol, tf, direction, entry, exit_any, hold, tag):
    dsl = {
        "key": "frost_search_%s_%s" % (symbol.split("-")[0].lower(), tag),
        "name": "search-%s-%s" % (symbol.split("-")[0], tag),
        "direction": direction,
        "timeframe": tf,
        "supported_instruments": [symbol],
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "max_hold_bars": hold,
        "description": "search " + tag,
    }
    dsl = f._ensure_dsl(dsl, symbol, tf)
    return {
        "symbol": symbol,
        "timeframe": tf,
        "direction": direction,
        "logic_class": tag,
        "dsl": dsl,
        "title_short": dsl["name"],
    }


def main():
    # Ensure factory supports mode first — if not, fail loudly
    import inspect
    if "mode" not in inspect.signature(d.run_internal_packs).parameters:
        print("FACTORY_NEEDS_PATCH")
        return
    cands = []
    # SOL long impulse-like selective
    for rsi_lo, rsi_hi, hold in itertools.product([45, 50, 52], [55, 58, 62], [4, 6, 8]):
        cands.append(mk(
            "SOL-USDT-SWAP", "5m", "long",
            [
                {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi_lo)}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(rsi_hi)}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
            ],
            [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
            ],
            hold, "sol_%s_%s_h%s" % (rsi_lo, rsi_hi, hold),
        ))
    # XRP short exhaustion
    for rsi_hi, hold in itertools.product([65, 70, 75], [6, 10, 14]):
        cands.append(mk(
            "XRP-USDT-SWAP", "15m", "short",
            [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi_hi)}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 100}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
            ],
            [{"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 55}}],
            hold, "xrp_%s_h%s" % (rsi_hi, hold),
        ))
    # DOGE long reclaim selective
    for rsi_hi, hold in itertools.product([40, 45, 50], [6, 10, 16]):
        cands.append(mk(
            "DOGE-USDT-SWAP", "1h", "long",
            [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(rsi_hi)}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema19"}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": -80}},
            ],
            [{"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 60}}],
            hold, "doge_%s_h%s" % (rsi_hi, hold),
        ))

    # Limit first pass: sample every N
    best = {}
    for i, book in enumerate(cands):
        if i % 3 != 0:
            continue
        packs = eval_relaxed(book)
        bm = packs.get("base_metrics") or {}
        sym = book["symbol"]
        row = {
            "tag": book["logic_class"],
            "pass": packs.get("pass"),
            "suite": packs.get("suite"),
            "tr": bm.get("trades"),
            "fp": bm.get("fold_positive"),
            "folds": bm.get("folds"),
            "sharpe": bm.get("sharpe"),
            "wr": bm.get("win_rate_pct"),
            "fr": ((packs.get("extreme_friction") or {}).get("metrics") or {}).get("sharpe"),
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
        }
        print(sym, json.dumps(row, ensure_ascii=False))
        cur = best.get(sym)
        score = (bm.get("fold_positive") or 0) * 10 + (bm.get("sharpe") or -99)
        if packs.get("pass") or cur is None or score > cur[0]:
            best[sym] = (score, book, packs, row)

    out = {}
    for sym, (score, book, packs, row) in best.items():
        out[sym] = {"score": score, "row": row, "dsl": book["dsl"]}
    open("/root/auto_trade/dual_engine/frost_search_best.json", "w").write(
        json.dumps(out, ensure_ascii=False, indent=2)
    )
    print("BEST", {k: v["row"] for k, v in out.items()})


if __name__ == "__main__":
    main()
