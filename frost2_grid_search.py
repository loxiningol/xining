#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜贰 focused grid search after postmortem directions fail initial DSL."""
from __future__ import print_function
import copy
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

OUT = "/root/auto_trade/dual_engine/frost2_grid_search.json"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def mk_short_exh(symbol, tf, rsi_lo, z_lo, hold, tp, ema="ema16", extra=None):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi_lo)}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z_lo)}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": ema}},
    ]
    if extra:
        entry.extend(extra)
    key = "frost2_%s_%s_exh_r%s_z%s_h%s_t%s" % (
        symbol.split("-")[0].lower(), tf,
        str(rsi_lo).replace(".", "p"),
        str(z_lo).replace(".", "p").replace("-", "m"),
        hold, tp)
    dsl = {
        "key": key,
        "name": "寒霜贰-%s-%s-exhaustion_fade" % (symbol.split("-")[0], tf),
        "direction": "short",
        "timeframe": tf,
        "supported_instruments": [symbol],
        "max_hold_bars": int(hold),
        "description": "postmortem exhaustion grid",
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(tp)},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"},
             "role": "invalidation"},
        ]},
        "schema": "qiyu_strategy_dsl_v1",
    }
    return {
        "symbol": symbol, "timeframe": tf, "direction": "short",
        "logic_class": "exhaustion_fade",
        "thesis": "GLM postmortem exhaustion_fade + XRP-success mirror grid",
        "dsl": f2.ensure_dsl(dsl, symbol, tf),
        "gate_mode": "frost2",
        "source": "frost2_grid",
    }


def mk_doge_reclaim(rsi_lo, rsi_hi, z_hi, hold, tp):
    symbol, tf = "DOGE-USDT-SWAP", "1h"
    key = "frost2_doge_1h_rec_r%s_%s_z%s_h%s_t%s" % (
        rsi_lo, rsi_hi, str(z_hi).replace(".", "p"), hold, tp)
    dsl = {
        "key": key,
        "name": "寒霜贰-DOGE-1h-range_reclaim",
        "direction": "long",
        "timeframe": tf,
        "supported_instruments": [symbol],
        "max_hold_bars": int(hold),
        "description": "postmortem doge 4h→1h sparse reclaim grid",
        "entry": {"all": [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi_lo)}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(rsi_hi)}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_low20"}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -1.0}},
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": float(z_hi)}},
        ]},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(tp)},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"},
             "role": "invalidation"},
        ]},
        "schema": "qiyu_strategy_dsl_v1",
    }
    return {
        "symbol": symbol, "timeframe": tf, "direction": "long",
        "logic_class": "range_reclaim",
        "thesis": "GLM postmortem DOGE remap sparse reclaim",
        "dsl": f2.ensure_dsl(dsl, symbol, tf),
        "gate_mode": "frost2",
        "source": "frost2_grid",
    }


def eval_quick(book):
    packs = f2.quick_suite(book)
    bm = packs.get("base_metrics") or {}
    return {
        "key": (book.get("dsl") or {}).get("key"),
        "quick_pass": packs.get("quick_pass"),
        "failed_step": packs.get("failed_step"),
        "fp": bm.get("fold_positive"),
        "folds": bm.get("folds"),
        "trades": bm.get("trades"),
        "wr": bm.get("win_rate_pct"),
        "sharpe": bm.get("sharpe"),
        "dest": ((packs.get("logic_destruction") or {}).get("pass")),
        "packs": packs,
        "book": book,
    }


def search():
    # Sanity: XRP live mirror under frost2 gates
    xrp = mk_short_exh("XRP-USDT-SWAP", "15m", 55, 0.0, 20, 45)
    # temporarily allow for probe only
    print("PROBE XRP live-mirror under frost2 quick...", flush=True)
    # Don't use avoided for probe
    px = eval_quick(xrp)
    print("XRP probe", {k: px[k] for k in ("quick_pass", "fp", "trades", "wr", "sharpe", "dest")}, flush=True)

    cands = []
    # SOL / BTC exhaustion grids (XRP-success neighborhood + looser)
    for sym in ("SOL-USDT-SWAP", "BTC-USDT-SWAP", "ETH-USDT-SWAP"):
        for rsi_lo, z_lo, hold, tp in [
            (55, 0.0, 20, 45),
            (55, 0.0, 24, 45),
            (55, 0.2, 20, 42),
            (58, 0.0, 22, 45),
            (52, 0.0, 18, 48),
            (60, 0.3, 20, 42),
            (55, -0.2, 26, 45),
            (57, 0.1, 16, 40),
            (50, 0.0, 28, 50),
            (55, 0.0, 20, 40),
            (56, 0.0, 22, 42),
            (54, 0.0, 24, 45),
        ]:
            cands.append(mk_short_exh(sym, "15m", rsi_lo, z_lo, hold, tp))
        # also with close<ema8 confirmation
        for rsi_lo, z_lo, hold, tp in [(55, 0.0, 20, 45), (58, 0.2, 22, 42)]:
            cands.append(mk_short_exh(
                sym, "15m", rsi_lo, z_lo, hold, tp, ema="ema16",
                extra=[{"left": {"feature": "close"}, "op": "lt",
                        "right": {"feature": "ema8"}}]))

    # DOGE reclaim grids (sparse)
    for rsi_lo, rsi_hi, z_hi, hold, tp in [
        (40, 52, 0.8, 14, 58),
        (38, 50, 0.6, 12, 56),
        (42, 50, 0.5, 10, 55),
        (35, 48, 1.0, 16, 55),
        (40, 48, 0.8, 18, 60),
        (42, 52, 0.8, 14, 58),
        (36, 50, 0.7, 12, 54),
        (40, 55, 1.2, 20, 62),
        (45, 55, 0.5, 10, 60),
        (38, 52, 0.9, 14, 57),
    ]:
        cands.append(mk_doge_reclaim(rsi_lo, rsi_hi, z_hi, hold, tp))

    # ETH 1h short trend fade as extra postmortem-safe niche
    for rsi_lo, z_lo, hold, tp in [(55, 0.0, 14, 45), (58, 0.2, 16, 42), (52, 0.0, 18, 48)]:
        cands.append(mk_short_exh("ETH-USDT-SWAP", "1h", rsi_lo, z_lo, hold, tp))

    winners = []
    near = []
    report = {
        "op": "寒霜贰·网格",
        "started_at": now(),
        "xrp_probe": {k: px[k] for k in ("quick_pass", "fp", "trades", "wr", "sharpe", "dest", "failed_step")},
        "tried": 0,
        "winners": [],
        "near_misses": [],
    }
    for i, book in enumerate(cands):
        if f2.avoided(book["symbol"], book["timeframe"]):
            continue
        row = eval_quick(book)
        report["tried"] += 1
        summary = {k: row[k] for k in (
            "key", "quick_pass", "fp", "folds", "trades", "wr", "sharpe", "dest", "failed_step")}
        summary["symbol"] = book["symbol"]
        summary["tf"] = book["timeframe"]
        print("GRID", i + 1, summary, flush=True)
        if row["quick_pass"]:
            winners.append(row)
            report["winners"].append(summary)
            # continue collecting a few winners across symbols
            syms = {w["book"]["symbol"] for w in winners}
            if len(winners) >= 6 or len(syms) >= 3:
                break
        else:
            fp = int(row.get("fp") or 0)
            if fp >= 6 and row.get("dest"):
                near.append(row)
                report["near_misses"].append(summary)
            elif fp >= 7:
                near.append(row)
                report["near_misses"].append(summary)

    # Promote winners through full → sim → formal
    pending = []
    survivors = []
    for row in winners:
        book = row["book"]
        packs = f2.full_suite(book, row["packs"])
        print("FULL", (book.get("dsl") or {}).get("key"), packs.get("full_pass"),
              packs.get("full"), flush=True)
        if not packs.get("full_pass"):
            survivors.append({
                "key": (book.get("dsl") or {}).get("key"),
                "ok": False, "failed_step": packs.get("failed_step"),
                "quick": True, "full": packs.get("full"),
                "symbol": book["symbol"], "tf": book["timeframe"],
            })
            continue
        sf = f2.run_sim_formal(book, packs)
        print("SIMFORMAL", (book.get("dsl") or {}).get("key"),
              sf.get("failed_step"), (sf.get("sim") or {}).get("wr_deepseek_sim"),
              (sf.get("sim") or {}).get("wr_qwen_sim"),
              (sf.get("formal") or {}).get("approved"),
              sf.get("pending"), flush=True)
        item = {
            "key": (book.get("dsl") or {}).get("key"),
            "ok": not sf.get("failed_step"),
            "failed_step": sf.get("failed_step"),
            "sim": sf.get("sim"),
            "formal": sf.get("formal"),
            "pending": sf.get("pending"),
            "symbol": book["symbol"],
            "tf": book["timeframe"],
            "metrics": packs.get("base_metrics"),
            "full": packs.get("full"),
        }
        survivors.append(item)
        if (sf.get("pending") or {}).get("ok"):
            pending.append((sf.get("pending") or {}).get("key"))
        if len(pending) >= 2:
            break

    # If not enough winners, try near-misses with local rewrite + one full pass
    if len(pending) < 2:
        for row in near[:8]:
            book = f2.local_structural_rewrite(row["book"], "quick_walk_forward")
            # keep class: for exhaustion keep short grid-ish by re-using book dsl with looser rsi
            q = eval_quick(book)
            print("NEAR_REWRITE", q.get("key"), q.get("quick_pass"), q.get("fp"), q.get("dest"), flush=True)
            if not q.get("quick_pass"):
                survivors.append({"key": q.get("key"), "ok": False,
                                  "failed_step": q.get("failed_step"),
                                  "symbol": book["symbol"], "tf": book["timeframe"]})
                continue
            packs = f2.full_suite(book, q["packs"])
            if not packs.get("full_pass"):
                survivors.append({"key": q.get("key"), "ok": False,
                                  "failed_step": packs.get("failed_step"),
                                  "symbol": book["symbol"], "tf": book["timeframe"]})
                continue
            sf = f2.run_sim_formal(book, packs)
            item = {
                "key": (book.get("dsl") or {}).get("key"),
                "ok": not sf.get("failed_step"),
                "failed_step": sf.get("failed_step"),
                "sim": sf.get("sim"),
                "formal": sf.get("formal"),
                "pending": sf.get("pending"),
                "symbol": book["symbol"],
                "tf": book["timeframe"],
            }
            survivors.append(item)
            if (sf.get("pending") or {}).get("ok"):
                pending.append((sf.get("pending") or {}).get("key"))
            if len(pending) >= 2:
                break

    report["survivors"] = survivors
    report["pending_keys"] = pending
    report["ok"] = len(pending) >= 2
    report["finished_at"] = now()
    with open(OUT, "w") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    # merge into frost2_run.json
    run_path = "/root/auto_trade/dual_engine/frost2_run.json"
    try:
        with open(run_path) as fh:
            run = json.load(fh)
    except Exception:
        run = {"op": "寒霜贰"}
    run["grid"] = {
        "ok": report["ok"],
        "pending_keys": pending,
        "winners_n": len(report["winners"]),
        "near_n": len(report["near_misses"]),
        "xrp_probe": report["xrp_probe"],
        "survivors": survivors,
    }
    run["pending_keys"] = pending
    run["ok"] = report["ok"]
    run["finished_at"] = now()
    run["phase"] = "grid_search"
    with open(run_path, "w") as fh:
        json.dump(run, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(json.dumps({"ok": report["ok"], "pending": pending,
                      "winners": len(report["winners"]),
                      "near": len(report["near_misses"]),
                      "xrp_probe": report["xrp_probe"]},
                     ensure_ascii=False, indent=2), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(search())
