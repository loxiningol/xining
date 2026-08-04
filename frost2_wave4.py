#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜贰 wave4 — hunt fold_positive>=8 outside forbidden niches."""
from __future__ import print_function
import json
import sys
from datetime import datetime

sys.path.insert(0, "/root")
import frost2_action_run as f2

OUT = "/root/auto_trade/dual_engine/frost2_wave4.json"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def exh_short(sym, tf, rsi, z, hold, tp, tag):
    dsl = {
        "key": "frost2w4_%s_%s_exh_%s" % (sym.split("-")[0].lower(), tf, tag),
        "name": "寒霜贰-%s-%s-exhaustion_fade" % (sym.split("-")[0], tf),
        "direction": "short",
        "entry": {"all": [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z)}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
        ]},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(tp)},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"},
             "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
        "description": "wave4 xrp-mirror exhaustion",
    }
    return {
        "symbol": sym, "timeframe": tf, "direction": "short",
        "logic_class": "exhaustion_fade",
        "thesis": "postmortem: prefer sparse exhaustion; XRP15m works, try other TF/symbol",
        "dsl": f2.ensure_dsl(dsl, sym, tf), "gate_mode": "frost2", "source": "wave4",
    }


def trendpb(sym, tf, rsi, z, hold, tag):
    dsl = {
        "key": "frost2w4_%s_%s_tpb_%s" % (sym.split("-")[0].lower(), tf, tag),
        "name": "寒霜贰-%s-%s-trend_pullback" % (sym.split("-")[0], tf),
        "direction": "long",
        "entry": {"all": [
            {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
            {"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": float(z)}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
        ]},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 70.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"},
             "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
        "description": "wave4 ada-structure port",
    }
    return {
        "symbol": sym, "timeframe": tf, "direction": "long",
        "logic_class": "trend_pullback",
        "thesis": "ada-like trendpb on non-ADA symbols",
        "dsl": f2.ensure_dsl(dsl, sym, tf), "gate_mode": "frost2", "source": "wave4",
    }


def main():
    books = []
    # XRP 5m allowed (15m forbidden)
    for rsi, z, hold, tp, tag in [
        (55, 0.0, 20, 45, "a"), (55, 0.0, 24, 45, "b"), (52, 0.0, 18, 48, "c"),
        (58, 0.2, 16, 42, "d"), (55, -0.2, 22, 45, "e"), (60, 0.0, 20, 40, "f"),
        (50, 0.0, 28, 50, "g"), (56, 0.1, 20, 44, "h"),
    ]:
        books.append(exh_short("XRP-USDT-SWAP", "5m", rsi, z, hold, tp, tag))

    # XAU / CL / XAG exhaustion (commodity)
    for sym, tf in [("XAU-USDT-SWAP", "15m"), ("XAU-USDT-SWAP", "1h"),
                    ("CL-USDT-SWAP", "15m"), ("CL-USDT-SWAP", "1h"),
                    ("XAG-USDT-SWAP", "5m"), ("XAG-USDT-SWAP", "15m")]:
        for rsi, z, hold, tp, tag in [
            (55, 0.0, 20, 45, "a"), (55, 0.0, 24, 42, "b"), (58, 0.2, 18, 45, "c"),
            (52, 0.0, 22, 48, "d"), (60, 0.3, 16, 42, "e"),
        ]:
            books.append(exh_short(sym, tf, rsi, z, hold, tp, tag))

    # trendpb ports (looser than wave2 sandwich)
    for sym, tf in [("BTC-USDT-SWAP", "5m"), ("ETH-USDT-SWAP", "5m"),
                    ("SOL-USDT-SWAP", "5m"), ("DOGE-USDT-SWAP", "5m"),
                    ("BTC-USDT-SWAP", "15m"), ("ETH-USDT-SWAP", "15m"),
                    ("CL-USDT-SWAP", "5m"), ("XAG-USDT-SWAP", "5m")]:
        for rsi, z, hold, tag in [
            (42, 2.3, 14, "a"), (40, 2.0, 12, "b"), (45, 2.5, 16, "c"),
            (42, 1.5, 10, "d"), (38, 2.8, 18, "e"), (48, 2.0, 12, "f"),
        ]:
            books.append(trendpb(sym, tf, rsi, z, hold, tag))

    # filter avoid
    books = [b for b in books if not f2.avoided(b["symbol"], b["timeframe"])]
    print("wave4 candidates", len(books), flush=True)

    winners = []
    best = []
    for i, book in enumerate(books):
        packs = f2.quick_suite(book)
        bm = packs.get("base_metrics") or {}
        s = {
            "key": (book.get("dsl") or {}).get("key"),
            "symbol": book["symbol"], "tf": book["timeframe"],
            "logic": book.get("logic_class"),
            "quick_pass": packs.get("quick_pass"),
            "fp": bm.get("fold_positive"), "folds": bm.get("folds"),
            "trades": bm.get("trades"),
            "wr": round(float(bm.get("win_rate_pct") or 0), 2),
            "sharpe": bm.get("sharpe"),
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
            "failed_step": packs.get("failed_step"),
        }
        best.append(s)
        if packs.get("quick_pass"):
            print("WIN", s, flush=True)
            winners.append({"book": book, "packs": packs, "summary": s})
            if len(winners) >= 6:
                break
        elif int(bm.get("fold_positive") or 0) >= 7:
            print("NEAR7", s, flush=True)
        elif i % 8 == 0:
            print("..", s, flush=True)

    best_sorted = sorted(best, key=lambda x: (
        int(x.get("fp") or 0),
        1 if x.get("dest") else 0,
        float(x.get("sharpe") or -99),
    ), reverse=True)[:15]

    pending = []
    survivors = []
    for row in winners:
        book, packs = row["book"], row["packs"]
        packs = f2.full_suite(book, packs)
        print("FULL", row["summary"]["key"], packs.get("full_pass"), packs.get("full"), flush=True)
        if not packs.get("full_pass"):
            survivors.append({**row["summary"], "ok": False,
                              "failed_step": packs.get("failed_step"), "stage": "full"})
            continue
        sf = f2.run_sim_formal(book, packs)
        print("SF", row["summary"]["key"], sf.get("failed_step"),
              (sf.get("sim") or {}).get("wr_deepseek_sim"),
              (sf.get("sim") or {}).get("wr_qwen_sim"),
              (sf.get("formal") or {}).get("approved"), sf.get("pending"), flush=True)
        item = {
            **row["summary"],
            "ok": not sf.get("failed_step"),
            "failed_step": sf.get("failed_step"),
            "sim": sf.get("sim"),
            "formal": sf.get("formal"),
            "pending": sf.get("pending"),
        }
        survivors.append(item)
        if (sf.get("pending") or {}).get("ok"):
            pending.append(sf["pending"]["key"])
        if len(pending) >= 2:
            break

    report = {
        "op": "寒霜贰·wave4",
        "started_at": now(),
        "candidates": len(books),
        "winners_n": len(winners),
        "best15": best_sorted,
        "survivors": survivors,
        "pending_keys": pending,
        "ok": len(pending) >= 2,
        "note": "ADA live itself scores fp=7 under WF>=8 gate; XRP15m mirror is rare",
        "finished_at": now(),
    }
    with open(OUT, "w") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    run = json.load(open("/root/auto_trade/dual_engine/frost2_run.json"))
    run["wave4"] = report
    run["pending_keys"] = pending
    run["ok"] = report["ok"]
    run["phase"] = "wave4"
    run["finished_at"] = now()
    # death summary for parent
    run["death_report"] = {
        "postmortem_ok": True,
        "target": ">=2 formal-queued",
        "achieved": len(pending),
        "waves": {
            "hypotheses": "all died quick_walk_forward",
            "grid_xrp_mirror": "0 winners (SOL/BTC/ETH/DOGE)",
            "wave2_trendpb_breakdown": "0 winners",
            "wave3_ada_port": "0 winners; ADA itself fp=7 under gate8",
            "wave4": report,
        },
    }
    with open("/root/auto_trade/dual_engine/frost2_run.json", "w") as fh:
        json.dump(run, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(json.dumps({"ok": report["ok"], "pending": pending,
                      "winners": len(winners), "best3": best_sorted[:3]},
                     ensure_ascii=False, indent=2), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
