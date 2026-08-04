#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fast base-metric screen then quick/full on top ETH15m fade candidates."""
from __future__ import print_function
import copy
import json
import pickle
import sys

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d
import auto_trade_human_confirm_pipeline as pipeline

FRAME_PKL = "/root/auto_trade/dual_engine/frost3_eth15m_fade_frame.pkl"
OUT = "/root/auto_trade/dual_engine/frost3_eth15m_fade_fast_search.json"
PREFIX = "frost3_eth15m_fade"

print("[fast] boot", flush=True)
fr = pickle.load(open(FRAME_PKL, "rb"))
print("[fast] frame", len(fr), flush=True)
pipeline._FRAME_CACHE["ETH-USDT-SWAP|15m"] = fr
_orig = d._frame
d._frame = lambda s, t: fr if (s == "ETH-USDT-SWAP" and t == "15m") else _orig(s, t)
f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
print("[fast] patched", flush=True)


def L(f, op, v=None, f2n=None, role=None):
    row = {
        "left": {"feature": f}, "op": op,
        "right": ({"value": float(v)} if v is not None else {"feature": f2n}),
    }
    if role:
        row["role"] = role
    return row


def mk_dsl(tag, entry, exit_any, hold):
    return f2.ensure_dsl({
        "key": "%s_%s" % (PREFIX, tag),
        "name": "寒霜叁-ETH-15m-exhaustion_fade",
        "direction": "short", "timeframe": "15m",
        "supported_instruments": ["ETH-USDT-SWAP"],
        "max_hold_bars": hold,
        "entry": {"all": entry}, "exit": {"any": exit_any},
    }, "ETH-USDT-SWAP", "15m")


def base_metrics(dsl):
    try:
        res = d._backtest(dsl, "ETH-USDT-SWAP", "15m", "observed_base")
        trades = res.get("trades") or []
        m = d._metrics_from_trades(trades)
        m["error"] = res.get("error")
        return m
    except Exception as exc:
        return {"trades": 0, "fold_positive": 0, "folds": 0, "error": str(exc)}


def main():
    entries = []
    for rsi in (58, 60, 62):
        for z in (0.3, 0.5, 0.8):
            for mode in ("nos", "stretch"):
                if mode == "nos":
                    entry = [L("rsi14", "gt", rsi), L("z20", "gt", z),
                             L("close", "lt", f2n="open"), L("macd_stick", "lt", 0)]
                else:
                    entry = [L("rsi14", "gt", rsi), L("z20", "gt", z),
                             L("close", "lt", f2n="open"), L("macd_stick", "lt", 0),
                             L("close", "gt", f2n="ema16")]
                entries.append(("e_r%s_z%s_%s" % (rsi, str(z).replace(".", "p"), mode), entry))

    exits = []
    for tp in (50, 55):
        exits.append(("tp%s_ph20" % tp, [
            L("rsi14", "lt", tp, role="take_profit"),
            L("close", "gt", f2n="prev_high20", role="invalidation"),
        ]))
        exits.append(("tp%s_ema8" % tp, [
            L("rsi14", "lt", tp, role="take_profit"),
            L("close", "gt", f2n="ema8", role="invalidation"),
        ]))

    screened = []
    n = 0
    total = len(entries) * len(exits) * 3
    print("[fast] grid_size", total, "entries", len(entries), flush=True)
    for etag, entry in entries:
        for xtag, ex in exits:
            for hold in (12, 16, 20):
                n += 1
                tag = "%s_%s_h%s" % (etag, xtag, hold)
                dsl = mk_dsl(tag, entry, ex, hold)
                m = base_metrics(dsl)
                fp = int(m.get("fold_positive") or 0)
                folds = int(m.get("folds") or 0)
                tr = int(m.get("trades") or 0)
                sh = float(m.get("sharpe") or -9)
                wr = float(m.get("win_rate_pct") or 0)
                if tr >= 10 and folds >= 10 and fp >= 6:
                    row = {"tag": tag, "tr": tr, "fp": fp, "folds": folds,
                           "wr": wr, "sh": sh, "entry_tag": etag, "exit_tag": xtag,
                           "hold": hold}
                    screened.append((fp, sh, wr, tr, row, dsl))
                    print("HIT", json.dumps(row, ensure_ascii=False), flush=True)
                if n % 20 == 0 or n == 1:
                    print("progress", n, "/", total, "hits", len(screened),
                          "last_fp", fp, "tr", tr, flush=True)

    screened.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)
    top = screened[:25]
    print("SCREEN_TOP", len(screened), "of", n, flush=True)

    quick_results = []
    winner = None
    best_quick = None
    for fp, sh, wr, tr, row, dsl in top:
        book = {
            "symbol": "ETH-USDT-SWAP", "timeframe": "15m", "direction": "short",
            "logic_class": "exhaustion_fade", "dsl": copy.deepcopy(dsl),
            "gate_mode": "frost2", "thesis": "fast_search:" + row["tag"],
            "stop_loss_pct": 0.009,
        }
        q = f2.quick_suite(book)
        bm = q.get("base_metrics") or {}
        qrow = {
            "tag": row["tag"], "quick": q.get("quick_pass"),
            "fail": q.get("failed_step"),
            "tr": bm.get("trades"), "fp": bm.get("fold_positive"),
            "folds": bm.get("folds"), "wr": bm.get("win_rate_pct"),
            "sh": bm.get("sharpe"),
            "dest": (q.get("logic_destruction") or {}).get("pass"),
            "screen": row,
        }
        print("QUICK", json.dumps(qrow, ensure_ascii=False), flush=True)
        quick_results.append(qrow)
        if best_quick is None or (
            (1 if qrow["quick"] else 0, qrow.get("fp") or 0, float(qrow.get("sh") or -9))
            > (1 if best_quick["quick"] else 0, best_quick.get("fp") or 0,
               float(best_quick.get("sh") or -9))
        ):
            best_quick = qrow
            open(OUT + ".best_book.json", "w").write(json.dumps({
                "row": qrow, "dsl": book["dsl"],
            }, indent=2, ensure_ascii=False))
        if q.get("quick_pass"):
            full = f2.full_suite(book, q)
            qrow["full_pass"] = full.get("full_pass")
            qrow["full"] = full.get("full")
            print("FULL", row["tag"], full.get("full_pass"), full.get("failed_step"),
                  json.dumps(full.get("full") or {}, ensure_ascii=False), flush=True)
            if full.get("full_pass") and winner is None:
                winner = {"row": qrow, "dsl": book["dsl"], "book": book, "packs": full}
                break

    out = {
        "screened_hits": len(screened),
        "screened_total": n,
        "top_screen": [t[4] for t in top],
        "quick_results": quick_results,
        "best_quick": best_quick,
        "winner": (winner["row"] if winner else None),
    }
    open(OUT, "w").write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    if winner:
        open("/root/auto_trade/dual_engine/frost3_eth15m_fade_fast_winner.json", "w").write(
            json.dumps({"row": winner["row"], "dsl": winner["dsl"]},
                       indent=2, ensure_ascii=False)
        )
    print("DONE best_quick", best_quick, "winner", out["winner"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
