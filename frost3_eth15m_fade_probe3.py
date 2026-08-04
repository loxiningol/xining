#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe ETH15m fade WITHOUT tight h1_slope filter (kills overbought samples)."""
from __future__ import print_function
import json
import pickle
import sys

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d
import auto_trade_human_confirm_pipeline as pipeline

FRAME_PKL = "/root/auto_trade/dual_engine/frost3_eth15m_fade_frame.pkl"
OUT = "/root/auto_trade/dual_engine/frost3_eth15m_fade_probe3.json"

fr = pickle.load(open(FRAME_PKL, "rb"))
pipeline._FRAME_CACHE["ETH-USDT-SWAP|15m"] = fr
_orig = d._frame
d._frame = lambda s, t: fr if (s == "ETH-USDT-SWAP" and t == "15m") else _orig(s, t)
f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7


def L(f, op, v=None, f2n=None):
    return {
        "left": {"feature": f},
        "op": op,
        "right": ({"value": float(v)} if v is not None else {"feature": f2n}),
    }


def run(tag, entry, hold=20, tp=45.0):
    dsl = f2.ensure_dsl({
        "key": "frost3_eth15m_fade_%s" % tag,
        "name": "p",
        "direction": "short",
        "timeframe": "15m",
        "supported_instruments": ["ETH-USDT-SWAP"],
        "max_hold_bars": hold,
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(tp)},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"},
             "role": "invalidation"},
        ]},
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
    cands = []
    for rsi, z, hold, tp, tag in [
        (65, 1.0, 20, 45, "nos_r65_z1"),
        (65, 1.2, 18, 48, "nos_r65_z1p2"),
        (62, 0.8, 22, 45, "nos_r62_z0p8"),
        (60, 0.5, 20, 45, "nos_r60_z0p5"),
        (68, 1.5, 16, 50, "nos_r68_z1p5"),
        (58, 0.3, 20, 45, "nos_r58_z0p3"),
        (70, 1.5, 14, 50, "nos_r70_z1p5"),
        (63, 1.0, 24, 42, "nos_r63_z1"),
        (55, 0.0, 20, 45, "xrp_exact"),
    ]:
        if tag == "xrp_exact":
            entry = [
                L("rsi14", "gt", 55), L("z20", "gt", 0),
                L("macd_stick", "lt", 0), L("close", "lt", f2n="ema16"),
            ]
        else:
            entry = [
                L("rsi14", "gt", rsi), L("z20", "gt", z),
                L("close", "lt", f2n="open"), L("macd_stick", "lt", 0),
            ]
        cands.append((tag, entry, hold, tp))
    for rsi, z, tag in [(65, 1.0, "str_r65"), (62, 0.8, "str_r62"), (60, 0.6, "str_r60")]:
        entry = [
            L("rsi14", "gt", rsi), L("z20", "gt", z),
            L("close", "lt", f2n="open"), L("macd_stick", "lt", 0),
            L("close", "gt", f2n="ema16"),
        ]
        cands.append((tag, entry, 20, 45))
    for slope, tag in [(0.01, "softslope_r65"), (0.005, "midslope_r65")]:
        entry = [
            L("rsi14", "gt", 65), L("z20", "gt", 1.0),
            L("close", "lt", f2n="open"), L("macd_stick", "lt", 0),
            L("h1_slope4", "lt", slope),
        ]
        cands.append((tag, entry, 20, 45))
    # cci assist no slope
    for cci, tag in [(100, "cci100"), (80, "cci80")]:
        entry = [
            L("rsi14", "gt", 60), L("z20", "gt", 0.8),
            L("close", "lt", f2n="open"), L("macd_stick", "lt", 0),
            L("cci", "gt", float(cci)),
        ]
        cands.append((tag, entry, 20, 45))

    best = None
    results = []
    winner = None
    for tag, entry, hold, tp in cands:
        row, book, q, full = run(tag, entry, hold, tp)
        results.append(row)
        sc = ((10000 if row["quick"] else 0)
              + (1000 if row["dest"] else 0)
              + (row["fp"] or 0) * 50
              + (row["tr"] or 0)
              + float(row["sh"] or -9))
        if best is None or sc > best[0]:
            best = (sc, row, book, q, full)
        if row.get("quick") and full and full.get("full_pass") and winner is None:
            winner = (book, full, row)

    open(OUT, "w").write(json.dumps({
        "results": results,
        "best": best[1] if best else None,
        "winner": winner[2] if winner else None,
    }, indent=2, ensure_ascii=False) + "\n")
    if winner:
        open("/root/auto_trade/dual_engine/frost3_eth15m_fade_probe3_winner.json", "w").write(
            json.dumps({"row": winner[2], "dsl": winner[0]["dsl"]},
                       indent=2, ensure_ascii=False)
        )
    print("BEST", best[1] if best else None, flush=True)
    print("WINNER", winner[2] if winner else None, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
