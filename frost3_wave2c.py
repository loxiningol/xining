#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜叁 Wave2c — calibrated niches after wave2b zero-trade autopsy.

Findings:
- 1h frames often LACK h1_* → any h1 filter = 0 trades
- Exact XRP short (rsi>55,z>0,macd<0,close<ema16) is RARE on ETH/BTC (~10-17 bars)
  but XRP itself passed with only ~14 trades → keep exact port + looser variants
- Avoid Wave1 clones: SOL5m impulse / BNB15m trend_pullback_fade / DOGE15m reclaim
- Avoid ADA/LTC/NG/XRP. CL traps: no RSI≈54 marginal; don't starve WF; dest-stable
Serial probes; promote max 2 parallel.
"""
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
PREFIX = "frost3_wave2c"
TARGET = 2


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
    key = ("frost3w2c_%s" % tag.replace(".", "p"))[:100]
    dsl = f2.ensure_dsl({
        "key": key, "name": "寒霜叁W2c-%s" % tag, "direction": direction,
        "entry": {"all": entry}, "exit": {"any": exit_any},
        "max_hold_bars": int(hold),
    }, sym, tf)
    return {
        "title": tag, "symbol": sym, "timeframe": tf, "direction": direction,
        "logic_class": logic, "thesis": "wave2c_calibrated:%s" % tag, "dsl": dsl,
        "gate_mode": "frost2", "source": "frost3_wave2c", "dir_rank": 1,
    }


def candidates():
    out = []
    short_exit = [
        L("rsi14", "lt", 45.0, role="take_profit"),
        L("close", "gt", feat2="prev_high20", role="invalidation"),
    ]
    long_exit = [
        L("rsi14", "gt", 58.0, role="take_profit"),
        L("close", "lt", feat2="prev_low20", role="invalidation"),
    ]

    # A) Exact XRP-port short on ETH/BTC/SOL/BNB 15m (NOT XRP; NOT wave1 logics)
    for sym in ["ETH-USDT-SWAP", "BTC-USDT-SWAP", "SOL-USDT-SWAP", "BNB-USDT-SWAP"]:
        tag = "%s_15m_xrpport" % sym.split("-")[0].lower()
        entry = [
            L("rsi14", "gt", 55.0), L("z20", "gt", 0.0),
            L("macd_stick", "lt", 0.0), L("close", "lt", feat2="ema16"),
        ]
        out.append(make(sym, "15m", "short", "exhaustion_fade", entry, short_exit, 20, tag))

    # B) Looser exhaustion: drop ema16, require red bar + mild z (higher freq ~4%)
    # Anti-CL: RSI floor 58 not 54
    for sym, rsi, z in [
        ("ETH-USDT-SWAP", 58.0, 0.2),
        ("BTC-USDT-SWAP", 58.0, 0.2),
        ("SOL-USDT-SWAP", 58.0, 0.15),
        ("BNB-USDT-SWAP", 58.0, 0.2),
    ]:
        tag = "%s_15m_looseexh_r%.0f" % (sym.split("-")[0].lower(), rsi)
        entry = [
            L("rsi14", "gt", rsi), L("z20", "gt", z),
            L("macd_stick", "lt", 0.0), L("close", "lt", feat2="open"),
        ]
        out.append(make(sym, "15m", "short", "exhaustion_fade", entry, short_exit, 18, tag))

    # C) Soft-H1 exhaustion (only on 15m where h1 exists): h1 down + loose exh
    for sym in ["ETH-USDT-SWAP", "BTC-USDT-SWAP"]:
        tag = "%s_15m_h1exh" % sym.split("-")[0].lower()
        entry = [
            L("h1_ema19", "lt", feat2="h1_ema53"),
            L("rsi14", "gt", 58.0), L("z20", "gt", 0.15),
            L("macd_stick", "lt", 0.0), L("close", "lt", feat2="open"),
        ]
        out.append(make(sym, "15m", "short", "exhaustion_fade", entry, short_exit, 20, tag))

    # D) Green reclaim long — NEW vs Wave1 DOGE15m range_reclaim (different sym/filters)
    # Keep trade density: rsi<42 z<-0.5 close>open (freq ~6% on ETH)
    for sym, rsi_hi, z_lo in [
        ("ETH-USDT-SWAP", 42.0, -0.5),
        ("BTC-USDT-SWAP", 40.0, -0.75),
        ("SOL-USDT-SWAP", 42.0, -0.5),
    ]:
        tag = "%s_15m_greenreclaim" % sym.split("-")[0].lower()
        entry = [
            L("rsi14", "lt", rsi_hi), L("rsi14", "gt", 25.0),
            L("z20", "lt", z_lo), L("z20", "gt", -3.5),
            L("close", "gt", feat2="open"),
            L("macd_stick", "gt", -0.5),  # not deeply negative
        ]
        out.append(make(sym, "15m", "long", "range_reclaim", entry, long_exit, 24, tag))

    # E) BNB 5m loose short exhaustion (NOT BNB15m pullback fade from Wave1)
    tag = "bnb_5m_looseexh"
    entry = [
        L("rsi14", "gt", 58.0), L("z20", "gt", 0.25),
        L("macd_stick", "lt", 0.0), L("close", "lt", feat2="open"),
    ]
    out.append(make("BNB-USDT-SWAP", "5m", "short", "exhaustion_fade",
                    entry, short_exit, 32, tag))

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
            "packs_full": q,
            "err": q.get("error"),
        }
    except Exception as exc:
        return {"tag": book.get("title"), "quick": False, "fail": "exception",
                "reason": str(exc), "symbol": book.get("symbol"), "tf": book.get("timeframe")}


def promote(book, packs0):
    hist = []
    cur = copy.deepcopy(book)
    packs = packs0
    for ftry in range(4):
        packs = f2.full_suite(cur, packs)
        hist.append({
            "stage": "full", "try": ftry, "pass": packs.get("full_pass"),
            "fail": packs.get("failed_step"),
            "fr": (packs.get("full") or {}).get("friction_sharpe"),
            "mc": ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
        })
        if packs.get("full_pass"):
            break
        if ftry >= 3:
            return {"ok": False, "failed_step": packs.get("failed_step") or "full_exhausted",
                    "hist": hist, "book_key": (cur.get("dsl") or {}).get("key"),
                    "symbol": cur.get("symbol"), "tf": cur.get("timeframe")}
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

    for stry in range(4):
        sf = f2.run_sim_formal(cur, packs)
        hist.append({
            "stage": "sim_formal", "try": stry, "fail": sf.get("failed_step"),
            "pending": (sf.get("pending") or {}).get("key"),
            "ds": (sf.get("sim") or {}).get("wr_deepseek_sim"),
            "qw": (sf.get("sim") or {}).get("wr_qwen_sim"),
        })
        if (sf.get("pending") or {}).get("ok"):
            return {
                "ok": True, "pending": sf.get("pending"), "sim": sf.get("sim"),
                "formal": sf.get("formal"), "hist": hist,
                "book_key": (cur.get("dsl") or {}).get("key"),
                "symbol": cur.get("symbol"), "tf": cur.get("timeframe"),
            }
        fs = sf.get("failed_step")
        if fs in ("formal_review", "pending_ingest") or stry >= 3:
            return {
                "ok": False, "failed_step": fs or "sim_exhausted",
                "sim": sf.get("sim"), "formal": sf.get("formal"),
                "pending": sf.get("pending"), "hist": hist,
                "book_key": (cur.get("dsl") or {}).get("key"),
                "symbol": cur.get("symbol"), "tf": cur.get("timeframe"),
            }
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
    return {"ok": False, "failed_step": "unknown", "hist": hist,
            "symbol": book.get("symbol"), "tf": book.get("timeframe")}


def group_fail_report(results, finals):
    by_dir = {}
    for r in results:
        key = "%s|%s|%s" % (r.get("symbol"), r.get("tf"), r.get("logic"))
        by_dir.setdefault(key, []).append(r)
    dirs = []
    for key, rows in by_dir.items():
        best = sorted(
            rows,
            key=lambda x: (
                1 if x.get("quick") else 0,
                x.get("fp") or 0,
                float(x.get("tr") or 0),
                float(x.get("wr") or 0),
            ),
            reverse=True,
        )[0]
        promo = None
        for f in finals:
            if f.get("symbol") == best.get("symbol") and f.get("tf") == best.get("tf"):
                if (promo is None) or f.get("ok"):
                    promo = f
        if promo and promo.get("ok"):
            status, fail_step = "pending", None
        elif promo:
            status, fail_step = "promote_fail", promo.get("failed_step")
        else:
            status, fail_step = "probe_fail", best.get("fail") or "quick_walk_forward"
        dirs.append({
            "direction": key, "best_tag": best.get("tag"), "status": status,
            "fail_step": fail_step,
            "tr": best.get("tr"), "fp": best.get("fp"), "wr": best.get("wr"),
            "sh": best.get("sh"), "dest": best.get("dest"),
            "pending_key": ((promo or {}).get("pending") or {}).get("key"),
            "ds": ((promo or {}).get("sim") or {}).get("wr_deepseek_sim"),
            "qw": ((promo or {}).get("sim") or {}).get("wr_qwen_sim"),
        })
    return dirs


def main():
    print("[wave2c] start", _now(), flush=True)
    # archive wave2b autopsy note
    _write("frost3_wave2b_end_report.json", {
        "at": _now(), "op": "寒霜叁-wave2b", "pending_n": 0, "success": False,
        "aborted": True,
        "reason_zh": "全部候选 tr=0：1h 缺 h1_*；且叠 h1+cci 过紧。已改 wave2c 校准。",
        "fail_step": "quick_walk_forward",
    })
    _write("%s_status.json" % PREFIX, {"at": _now(), "stage": "probe"})
    cands = candidates()
    print("[wave2c] probe n=", len(cands), "SERIAL", flush=True)
    results = []
    for i, b in enumerate(cands):
        print("[wave2c] probe %d/%d %s" % (i + 1, len(cands), b["title"]), flush=True)
        row = probe_one(b)
        results.append(row)
        print("[probe]", row.get("tag"), "quick", row.get("quick"),
              "tr", row.get("tr"), "fp", row.get("fp"), "wr", row.get("wr"),
              "dest", row.get("dest"), "fail", row.get("fail"), flush=True)
        _write("%s_probe_partial.json" % PREFIX, {
            "at": _now(), "done": i + 1, "n": len(cands),
            "results": [{k: v for k, v in r.items() if k not in ("book", "packs", "packs_full")}
                        for r in results],
        })

    _write("%s_probe.json" % PREFIX, {
        "at": _now(),
        "results": [{k: v for k, v in r.items() if k not in ("book", "packs", "packs_full")}
                    for r in results],
    })
    winners = [r for r in results if r.get("quick")]
    # near: dest + fp>=6 + tr>=10
    near = sorted(
        [r for r in results if not r.get("quick") and (r.get("fp") or 0) >= 6
         and (r.get("dest") is True) and float(r.get("tr") or 0) >= 10],
        key=lambda x: (x.get("fp") or 0, float(x.get("wr") or 0)), reverse=True
    )[:4]
    print("[wave2c] winners", [w.get("tag") for w in winners], flush=True)
    print("[wave2c] near", [n.get("tag") for n in near], flush=True)

    promote_list = list(winners[:4])
    for n in near:
        if len(promote_list) >= 4:
            break
        b = n.get("book")
        if not b:
            continue
        print("[wave2c] near_repair", n.get("tag"), flush=True)
        b2 = f3.local_tweak(b, 1, "quick_walk_forward")
        repaired, _ = f3.glm_repair(b2, n.get("packs_full") or {}, "quick_walk_forward", "quick")
        if repaired:
            b2 = repaired
        q = f2.quick_suite(b2)
        if q.get("quick_pass"):
            promote_list.append({
                "tag": n["tag"] + "_nearfix", "book": b2, "packs": q, "quick": True,
                "symbol": b2["symbol"], "tf": b2["timeframe"],
            })

    if not promote_list:
        # rescue top by trades among dest-pass or any tr>=12
        rescue = sorted(
            [r for r in results if r.get("book") and float(r.get("tr") or 0) >= 12],
            key=lambda x: (1 if x.get("dest") else 0, x.get("fp") or 0, float(x.get("wr") or 0)),
            reverse=True,
        )[:3]
        for n in rescue:
            b = n["book"]
            print("[wave2c] rescue", n.get("tag"), "tr", n.get("tr"), "fp", n.get("fp"), flush=True)
            b2 = f3.local_tweak(b, 1, n.get("fail") or "quick_walk_forward")
            repaired, _ = f3.glm_repair(b2, n.get("packs_full") or {}, n.get("fail") or "quick", "quick")
            if repaired:
                b2 = repaired
            q = f2.quick_suite(b2)
            print("[wave2c] rescue_quick", n.get("tag"), q.get("quick_pass"), q.get("failed_step"),
                  (q.get("base_metrics") or {}).get("trades"),
                  (q.get("base_metrics") or {}).get("fold_positive"), flush=True)
            if q.get("quick_pass"):
                promote_list.append({
                    "tag": n["tag"] + "_rescue", "book": b2, "packs": q, "quick": True,
                    "symbol": b2["symbol"], "tf": b2["timeframe"],
                })

    finals = []
    workers = min(2, max(1, len(promote_list)))
    print("[wave2c] promote n=", len(promote_list), "workers=", workers, flush=True)
    _write("%s_status.json" % PREFIX, {
        "at": _now(), "stage": "promote",
        "tags": [p.get("tag") for p in promote_list],
    })
    if promote_list:
        with ThreadPoolExecutor(max_workers=workers) as ex:
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
                           "trace": traceback.format_exc()[-1500:],
                           "symbol": src.get("symbol"), "tf": src.get("tf")}
                res["from_tag"] = src.get("tag")
                finals.append(res)
                print("[promote]", res.get("from_tag"), "ok", res.get("ok"),
                      "fail", res.get("failed_step"),
                      "pending", (res.get("pending") or {}).get("key"),
                      "ds", ((res.get("sim") or {}).get("wr_deepseek_sim")),
                      "qw", ((res.get("sim") or {}).get("wr_qwen_sim")), flush=True)
                if sum(1 for f in finals if f.get("ok")) >= TARGET:
                    break

    pending = [r for r in finals if r.get("ok")]
    dirs = group_fail_report(results, finals)
    end = {
        "at": _now(), "op": "寒霜叁-wave2c",
        "probe_n": len(results),
        "quick_winners": [w.get("tag") for w in winners],
        "promote_tags": [p.get("tag") for p in promote_list],
        "pending_n": len(pending),
        "pending_keys": [((r.get("pending") or {}).get("key")) for r in pending],
        "pending_detail": [{
            "key": (r.get("pending") or {}).get("key"),
            "symbol": r.get("symbol"), "tf": r.get("tf"),
            "from_tag": r.get("from_tag"),
            "ds": (r.get("sim") or {}).get("wr_deepseek_sim"),
            "qw": (r.get("sim") or {}).get("wr_qwen_sim"),
        } for r in pending],
        "directions": dirs,
        "finals": finals,
        "success": len(pending) >= TARGET,
        "autopsy_from_wave2b": [
            "1h missing h1_*", "overtight h1+cci stacked",
            "switched to 15m XRP-port + looseexh + greenreclaim",
        ],
    }
    _write("%s_end_report.json" % PREFIX, end)

    lines = ["# 寒霜叁 Wave2c 报告", "", "时间: %s" % end["at"], ""]
    lines.append("pending_n=%d success=%s" % (end["pending_n"], end["success"]))
    lines.append("")
    if pending:
        lines.append("## 已入 pending")
        for p in end["pending_detail"]:
            lines.append("- `%s` (%s %s) DS=%s Qwen=%s from=%s" % (
                p.get("key"), p.get("symbol"), p.get("tf"),
                p.get("ds"), p.get("qw"), p.get("from_tag")))
        lines.append("")
    lines.append("## 各方向失败步骤 / 状态")
    for drow in dirs:
        if drow.get("status") == "pending":
            lines.append("- **%s** → pending `%s` DS=%s Qwen=%s" % (
                drow["direction"], drow.get("pending_key"), drow.get("ds"), drow.get("qw")))
        else:
            lines.append(
                "- **%s** best=`%s` → 失败步骤=`%s` (tr=%s fp=%s wr=%s dest=%s)" % (
                    drow["direction"], drow.get("best_tag"), drow.get("fail_step"),
                    drow.get("tr"), drow.get("fp"), drow.get("wr"), drow.get("dest")))
    # also wave1 summary
    lines.append("")
    lines.append("## Wave1（回顾）")
    lines.append("- SOL 5m impulse → validate/quick 失败")
    lines.append("- BNB 15m trend_pullback_fade → quick_walk_forward (fp=3)")
    lines.append("- DOGE 15m range_reclaim → quick_walk_forward (fp=4)")
    lines.append("")
    lines.append("## Wave2b（回顾）")
    lines.append("- 全部 tr=0（1h缺h1 / 过滤过紧）→ aborted")
    zh = os.path.join(OUT, "%s_报告.md" % PREFIX)
    open(zh, "w").write("\n".join(lines) + "\n")
    print("[wave2c] wrote", zh, flush=True)

    try:
        main_end = json.load(open(os.path.join(OUT, "frost3_end_report.json")))
    except Exception:
        main_end = {}
    main_end["wave2b"] = {"pending_n": 0, "aborted": True, "reason": "tr=0 overtight/missing h1"}
    main_end["wave2c"] = {
        "at": end["at"], "pending_n": end["pending_n"],
        "pending_keys": end["pending_keys"], "success": end["success"],
        "directions": dirs,
    }
    keys = [k for k in (list(main_end.get("pending_keys") or []) + end["pending_keys"]) if k]
    main_end["pending_keys_total"] = keys
    main_end["pending_n_total"] = len(keys)
    main_end["success"] = len(keys) >= TARGET
    main_end["updated_at"] = _now()
    _write("frost3_end_report.json", main_end)
    _write("%s_status.json" % PREFIX, {"at": _now(), "stage": "done", "end": {
        "pending_n": end["pending_n"], "pending_keys": end["pending_keys"],
        "success": end["success"],
    }})
    print("[wave2c] END", json.dumps({
        "pending_n": end["pending_n"], "pending_keys": end["pending_keys"],
        "success": end["success"], "winners": end["quick_winners"],
    }, ensure_ascii=False), flush=True)
    return 0 if end["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
