#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜叁 wave2 — focused Quick-pass hunt then full pipeline on winners."""
from __future__ import print_function

import copy
import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d
import frost3_action as f3

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
f2.AVOID = [
    ("ADA-USDT-SWAP", None), ("LTC-USDT-SWAP", None),
    ("NG-USDT-SWAP", None), ("XRP-USDT-SWAP", None),
]
OUT = "/root/auto_trade/dual_engine"
PREFIX = "frost3_wave2"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write(name, obj):
    path = os.path.join(OUT, name)
    open(path, "w").write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


def L(feat, op, val=None, feat2=None, role=None):
    right = {"value": float(val)} if val is not None else {"feature": feat2}
    o = {"left": {"feature": feat}, "op": op, "right": right}
    if role:
        o["role"] = role
    return o


def make(sym, tf, direction, logic, entry, exit_any, hold, tag):
    key = ("frost3w2_%s" % tag.replace(".", "p"))[:100]
    dsl = f2.ensure_dsl({
        "key": key, "name": "寒霜叁W2-%s" % tag, "direction": direction,
        "entry": {"all": entry}, "exit": {"any": exit_any},
        "max_hold_bars": int(hold),
    }, sym, tf)
    return {
        "title": tag, "symbol": sym, "timeframe": tf, "direction": direction,
        "logic_class": logic, "thesis": tag, "dsl": dsl,
        "gate_mode": "frost2", "source": "frost3_wave2", "dir_rank": 1,
    }


def candidates():
    out = []
    # XRP-proven short exhaustion + H1 down, raised floors (anti-CL marginal)
    for sym, tf in [
        ("ETH-USDT-SWAP", "15m"), ("SOL-USDT-SWAP", "15m"),
        ("BNB-USDT-SWAP", "15m"), ("LINK-USDT-SWAP", "15m"),
        ("AVAX-USDT-SWAP", "15m"), ("BTC-USDT-SWAP", "1h"),
        ("ETH-USDT-SWAP", "5m"), ("SOL-USDT-SWAP", "5m"),
    ]:
        for rsi, z, cci, hold in [
            (58, 0.3, 70, 20), (60, 0.35, 80, 18), (62, 0.4, 90, 16),
            (56, 0.2, 60, 24),
        ]:
            tag = "%s_%s_exh_r%.0f_z%s_c%.0f" % (
                sym.split("-")[0].lower(), tf, rsi, str(z).replace(".", "p"), cci)
            entry = [
                L("h1_ema19", "lt", feat2="h1_ema53"),
                L("rsi14", "gt", rsi), L("z20", "gt", z),
                L("macd_stick", "lt", 0.0),
                L("close", "lt", feat2="ema16"),
                L("cci", "gt", cci),
            ]
            exit_any = [
                L("rsi14", "lt", 45.0, role="take_profit"),
                L("close", "gt", feat2="prev_high20", role="invalidation"),
            ]
            out.append(make(sym, tf, "short", "exhaustion_fade", entry, exit_any, hold, tag))

        # long H1 trend + breakout confirm (sparse)
        tag = "%s_%s_imp_brk" % (sym.split("-")[0].lower(), tf)
        entry = [
            L("h1_ema19", "gt", feat2="h1_ema53"),
            L("h1_slope4", "gt", 0.0),
            L("close", "gt", feat2="ema21"),
            L("close", "gt", feat2="prev_high20"),
            L("rsi14", "gt", 58.0), L("macd_stick", "gt", 0.0),
            L("z20", "gt", 0.35),
        ]
        exit_any = [
            L("rsi14", "lt", 50.0, role="take_profit"),
            L("close", "lt", feat2="ema21", role="invalidation"),
        ]
        hold = 36 if tf == "5m" else (16 if tf == "1h" else 24)
        out.append(make(sym, tf, "long", "impulse_continuation", entry, exit_any, hold, tag))
    return out


def probe_one(book):
    try:
        q = f2.quick_suite(book)
        bm = q.get("base_metrics") or {}
        return {
            "tag": book["title"], "symbol": book["symbol"], "tf": book["timeframe"],
            "logic": book["logic_class"], "quick": bool(q.get("quick_pass")),
            "fail": q.get("failed_step"),
            "tr": bm.get("trades"), "fp": bm.get("fold_positive"),
            "folds": bm.get("folds"), "wr": bm.get("win_rate_pct"),
            "sh": bm.get("sharpe"),
            "dest": (q.get("logic_destruction") or {}).get("pass"),
            "book": book, "packs": q if q.get("quick_pass") else None,
        }
    except Exception as exc:
        return {"tag": book.get("title"), "quick": False, "fail": "exception",
                "reason": str(exc)}


def promote(book, packs0):
    """Full → sim → formal from a Quick-passed book, with limited repairs."""
    hist = []
    cur = copy.deepcopy(book)
    packs = packs0
    # Full ≤3
    for ftry in range(4):
        packs = f2.full_suite(cur, packs)
        hist.append({"stage": "full", "try": ftry, "pass": packs.get("full_pass"),
                     "fail": packs.get("failed_step"),
                     "fr": (packs.get("full") or {}).get("friction_sharpe"),
                     "mc": ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio")})
        if packs.get("full_pass"):
            break
        if ftry >= 3:
            return {"ok": False, "failed_step": packs.get("failed_step") or "full_exhausted",
                    "hist": hist, "book_key": (cur.get("dsl") or {}).get("key")}
        cur = f3.local_tweak(cur, ftry + 1, packs.get("failed_step") or "full")
        repaired, _ = f3.glm_repair(cur, packs, packs.get("failed_step"), "full")
        if repaired:
            cur = repaired
        q = f2.quick_suite(cur)
        hist.append({"stage": "re_quick", "pass": q.get("quick_pass"), "fail": q.get("failed_step")})
        if not q.get("quick_pass"):
            packs = q
            packs["full_pass"] = False
            continue
        packs = q

    # Sim/formal ≤3
    for stry in range(4):
        sf = f2.run_sim_formal(cur, packs)
        hist.append({"stage": "sim_formal", "try": stry, "fail": sf.get("failed_step"),
                     "pending": (sf.get("pending") or {}).get("key"),
                     "ds": (sf.get("sim") or {}).get("wr_deepseek_sim"),
                     "qw": (sf.get("sim") or {}).get("wr_qwen_sim")})
        if (sf.get("pending") or {}).get("ok"):
            return {"ok": True, "pending": sf.get("pending"), "sim": sf.get("sim"),
                    "formal": sf.get("formal"), "hist": hist,
                    "book_key": (cur.get("dsl") or {}).get("key"),
                    "symbol": cur.get("symbol"), "tf": cur.get("timeframe")}
        fs = sf.get("failed_step")
        if fs in ("formal_review", "pending_ingest") or stry >= 3:
            return {"ok": False, "failed_step": fs or "sim_exhausted",
                    "sim": sf.get("sim"), "formal": sf.get("formal"),
                    "pending": sf.get("pending"), "hist": hist,
                    "book_key": (cur.get("dsl") or {}).get("key"),
                    "symbol": cur.get("symbol"), "tf": cur.get("timeframe")}
        cur = f3.local_tweak(cur, stry + 1, "sim")
        repaired, _ = f3.glm_repair(cur, packs, "sim_review", "sim")
        if repaired:
            cur = repaired
        q = f2.quick_suite(cur)
        if not q.get("quick_pass"):
            continue
        packs = f2.full_suite(cur, q)
        if not packs.get("full_pass"):
            continue
    return {"ok": False, "failed_step": "unknown", "hist": hist}


def main():
    print("[wave2] start", _now(), flush=True)
    cands = candidates()
    print("[wave2] probe n=", len(cands), flush=True)
    results = []
    # parallel probe
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(probe_one, b) for b in cands]
        for fut in as_completed(futs):
            row = fut.result()
            results.append(row)
            print("[probe]", row.get("tag"), "quick", row.get("quick"),
                  "tr", row.get("tr"), "fp", row.get("fp"), "dest", row.get("dest"),
                  "fail", row.get("fail"), flush=True)

    _write("%s_probe.json" % PREFIX, {
        "at": _now(),
        "results": [{k: v for k, v in r.items() if k not in ("book", "packs")} for r in results],
    })
    winners = [r for r in results if r.get("quick")]
    # also take near: dest pass + fp>=6 + wr>=55 as secondary try
    near = sorted(
        [r for r in results if not r.get("quick") and (r.get("fp") or 0) >= 6
         and (r.get("dest") is True) and float(r.get("wr") or 0) >= 55],
        key=lambda x: (x.get("fp") or 0, x.get("sh") or -9), reverse=True
    )[:3]
    print("[wave2] quick_winners", len(winners), [w.get("tag") for w in winners], flush=True)
    print("[wave2] near", [n.get("tag") for n in near], flush=True)

    promote_list = winners[:4]
    # if <2 quick winners, try repair near once then full
    for n in near:
        if len(promote_list) >= 4:
            break
        b = n.get("book")
        if not b:
            continue
        b2 = f3.local_tweak(b, 1, "quick_walk_forward")
        q = f2.quick_suite(b2)
        if q.get("quick_pass"):
            promote_list.append({
                "tag": n["tag"] + "_nearfix", "book": b2, "packs": q, "quick": True,
                "symbol": b2["symbol"], "tf": b2["timeframe"],
            })

    finals = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {
            ex.submit(promote, row["book"], row["packs"]): row
            for row in promote_list if row.get("book") and row.get("packs")
        }
        for fut in as_completed(futs):
            src = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:
                res = {"ok": False, "failed_step": "exception", "reason": str(exc),
                       "trace": traceback.format_exc()[-1500:]}
            res["from_tag"] = src.get("tag")
            finals.append(res)
            print("[promote]", res.get("from_tag"), "ok", res.get("ok"),
                  "fail", res.get("failed_step"),
                  "pending", (res.get("pending") or {}).get("key"), flush=True)

    pending = [r for r in finals if r.get("ok")]
    end = {
        "at": _now(),
        "op": "寒霜叁-wave2",
        "probe_n": len(results),
        "quick_winners": [w.get("tag") for w in winners],
        "pending_n": len(pending),
        "pending_keys": [((r.get("pending") or {}).get("key")) for r in pending],
        "finals": finals,
        "success": len(pending) >= 2,
    }
    _write("%s_end_report.json" % PREFIX, end)
    # merge into main frost3 end
    try:
        main_end = json.load(open(os.path.join(OUT, "frost3_end_report.json")))
    except Exception:
        main_end = {}
    main_end["wave2"] = end
    main_end["pending_n_total"] = len(pending) + int(main_end.get("pending_n") or 0)
    main_end["pending_keys_total"] = list(main_end.get("pending_keys") or []) + end["pending_keys"]
    main_end["success"] = len(main_end["pending_keys_total"]) >= 2
    main_end["updated_at"] = _now()
    _write("frost3_end_report.json", main_end)
    print("[wave2] END", json.dumps({
        "pending_n": end["pending_n"], "pending_keys": end["pending_keys"],
        "success": end["success"], "quick_winners": end["quick_winners"],
    }, ensure_ascii=False), flush=True)
    return 0 if end["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
