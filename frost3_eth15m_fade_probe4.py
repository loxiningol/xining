#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exit/entry micro-search to push ETH15m fade toward WF>=7/10 + dest."""
from __future__ import print_function
import json
import pickle
import sys

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d
import auto_trade_human_confirm_pipeline as pipeline

FRAME_PKL = "/root/auto_trade/dual_engine/frost3_eth15m_fade_frame.pkl"
OUT = "/root/auto_trade/dual_engine/frost3_eth15m_fade_probe4.json"

fr = pickle.load(open(FRAME_PKL, "rb"))
pipeline._FRAME_CACHE["ETH-USDT-SWAP|15m"] = fr
_orig = d._frame
d._frame = lambda s, t: fr if (s == "ETH-USDT-SWAP" and t == "15m") else _orig(s, t)
f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7


def L(f, op, v=None, f2n=None, role=None):
    row = {
        "left": {"feature": f},
        "op": op,
        "right": ({"value": float(v)} if v is not None else {"feature": f2n}),
    }
    if role:
        row["role"] = role
    return row


def run(tag, entry, exit_any, hold):
    dsl = f2.ensure_dsl({
        "key": "frost3_eth15m_fade_%s" % tag,
        "name": "p", "direction": "short", "timeframe": "15m",
        "supported_instruments": ["ETH-USDT-SWAP"], "max_hold_bars": hold,
        "entry": {"all": entry}, "exit": {"any": exit_any},
    }, "ETH-USDT-SWAP", "15m")
    book = {
        "symbol": "ETH-USDT-SWAP", "timeframe": "15m", "direction": "short",
        "logic_class": "exhaustion_fade", "dsl": dsl, "gate_mode": "frost2",
    }
    q = f2.quick_suite(book)
    bm = q.get("base_metrics") or {}
    row = {
        "tag": tag, "quick": q.get("quick_pass"), "fail": q.get("failed_step"),
        "tr": bm.get("trades"), "fp": bm.get("fold_positive"), "folds": bm.get("folds"),
        "wr": bm.get("win_rate_pct"), "sh": bm.get("sharpe"),
        "dest": (q.get("logic_destruction") or {}).get("pass"),
    }
    print(json.dumps(row, ensure_ascii=False), flush=True)
    full = None
    if q.get("quick_pass"):
        full = f2.full_suite(book, q)
        print("FULL", tag, full.get("full_pass"), full.get("failed_step"),
              json.dumps(full.get("full") or {}, ensure_ascii=False), flush=True)
        row["full_pass"] = full.get("full_pass")
        row["full"] = full.get("full")
    return row, book, q, full


def main():
    bases = []
    # stretch family (best fp so far)
    bases.append(("str60", [
        L("rsi14", "gt", 60), L("z20", "gt", 0.6),
        L("close", "lt", f2n="open"), L("macd_stick", "lt", 0),
        L("close", "gt", f2n="ema16"),
    ]))
    bases.append(("str58", [
        L("rsi14", "gt", 58), L("z20", "gt", 0.4),
        L("close", "lt", f2n="open"), L("macd_stick", "lt", 0),
        L("close", "gt", f2n="ema16"),
    ]))
    bases.append(("nos58", [
        L("rsi14", "gt", 58), L("z20", "gt", 0.3),
        L("close", "lt", f2n="open"), L("macd_stick", "lt", 0),
    ]))
    bases.append(("xrp_rej", [
        L("rsi14", "gt", 55), L("z20", "gt", 0.0),
        L("macd_stick", "lt", 0), L("close", "lt", f2n="ema16"),
        L("close", "lt", f2n="open"),
    ]))
    bases.append(("cci80", [
        L("rsi14", "gt", 58), L("z20", "gt", 0.4),
        L("close", "lt", f2n="open"), L("macd_stick", "lt", 0),
        L("cci", "gt", 80),
    ]))
    bases.append(("str62_cci", [
        L("rsi14", "gt", 62), L("z20", "gt", 0.7),
        L("close", "lt", f2n="open"), L("macd_stick", "lt", 0),
        L("close", "gt", f2n="ema16"), L("cci", "gt", 90),
    ]))

    exits = []
    for tp in (48, 50, 52, 55):
        exits.append(("tp%s_ph20" % tp, [
            L("rsi14", "lt", tp, role="take_profit"),
            L("close", "gt", f2n="prev_high20", role="invalidation"),
        ]))
    for tp in (50, 55):
        exits.append(("tp%s_ema8" % tp, [
            L("rsi14", "lt", tp, role="take_profit"),
            L("close", "gt", f2n="ema8", role="invalidation"),
        ]))

    holds = (12, 16, 20)
    results = []
    best = None
    winner = None
    # Compact ordered set (~6*6*3=108 max; stop early on full pass)
    ordered = []
    for btag, entry in bases:
        for etag, ex in exits:
            for hold in holds:
                ordered.append(("%s_%s_h%s" % (btag, etag, hold), entry, ex, hold))

    for tag, entry, ex, hold in ordered:
        row, book, q, full = run(tag, entry, ex, hold)
        results.append(row)
        sc = ((10000 if row["quick"] else 0)
              + (5000 if row.get("full_pass") else 0)
              + (1000 if row["dest"] else 0)
              + (row["fp"] or 0) * 50
              + (row["tr"] or 0)
              + float(row["sh"] or -9))
        if best is None or sc > best[0]:
            best = (sc, row, book, q, full)
            open(OUT + ".best_partial.json", "w").write(json.dumps({
                "best": row, "dsl": book["dsl"],
            }, indent=2, ensure_ascii=False))
        if row.get("quick") and full and full.get("full_pass") and winner is None:
            winner = (book, full, row)
            break  # first full-pass is enough to proceed

    open(OUT, "w").write(json.dumps({
        "results": results,
        "best": best[1] if best else None,
        "winner": winner[2] if winner else None,
        "n": len(results),
    }, indent=2, ensure_ascii=False) + "\n")
    if winner:
        open("/root/auto_trade/dual_engine/frost3_eth15m_fade_probe4_winner.json", "w").write(
            json.dumps({"row": winner[2], "dsl": winner[0]["dsl"]},
                       indent=2, ensure_ascii=False)
        )
    print("BEST", best[1] if best else None, flush=True)
    print("WINNER", winner[2] if winner else None, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
