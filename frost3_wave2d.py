#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜叁 Wave2d — dest-repair around BTC/ETH/SOL 15m XRP-port near-misses.

Wave2c best: btc_15m_xrpport tr14 fp6 wr57 destFAIL;
rescue reached fp7 but still quick_logic_destruction.
Goal: structural add-ons that pass dest while keeping WF≥7, then Full→sim→pending.
Serial; max 2 promote workers.
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
PREFIX = "frost3_wave2d"
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
    key = ("frost3w2d_%s" % tag.replace(".", "p"))[:100]
    dsl = f2.ensure_dsl({
        "key": key, "name": "寒霜叁W2d-%s" % tag, "direction": direction,
        "entry": {"all": entry}, "exit": {"any": exit_any},
        "max_hold_bars": int(hold),
    }, sym, tf)
    return {
        "title": tag, "symbol": sym, "timeframe": tf, "direction": direction,
        "logic_class": logic, "thesis": "wave2d_dest_repair:%s" % tag, "dsl": dsl,
        "gate_mode": "frost2", "source": "frost3_wave2d", "dir_rank": 1,
    }


def candidates():
    out = []
    short_exit = [
        L("rsi14", "lt", 45.0, role="take_profit"),
        L("close", "gt", feat2="prev_high20", role="invalidation"),
    ]
    # Structural variants for dest: red body, mild cci, soft h1, rsi cap, z floor
    specs = []
    for sym in ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]:
        base = sym.split("-")[0].lower()
        # V1: XRP + red bar
        specs.append((sym, "15m", "%s_xrp_red" % base, [
            L("rsi14", "gt", 55.0), L("z20", "gt", 0.0),
            L("macd_stick", "lt", 0.0), L("close", "lt", feat2="ema16"),
            L("close", "lt", feat2="open"),
        ], 22))
        # V2: XRP + soft h1 down
        specs.append((sym, "15m", "%s_xrp_h1" % base, [
            L("h1_ema19", "lt", feat2="h1_ema53"),
            L("rsi14", "gt", 55.0), L("z20", "gt", 0.0),
            L("macd_stick", "lt", 0.0), L("close", "lt", feat2="ema16"),
        ], 20))
        # V3: XRP + mild cci + red (anti-marginal)
        specs.append((sym, "15m", "%s_xrp_cci60" % base, [
            L("rsi14", "gt", 56.0), L("z20", "gt", 0.05),
            L("macd_stick", "lt", 0.0), L("close", "lt", feat2="ema16"),
            L("close", "lt", feat2="open"), L("cci", "gt", 60.0),
        ], 20))
        # V4: tighter z + rsi band (exhaustion not runaway)
        specs.append((sym, "15m", "%s_xrp_band" % base, [
            L("rsi14", "gt", 55.0), L("rsi14", "lt", 72.0),
            L("z20", "gt", 0.15), L("z20", "lt", 2.5),
            L("macd_stick", "lt", 0.0), L("close", "lt", feat2="ema16"),
            L("close", "lt", feat2="open"),
        ], 18))
        # V5: h1 + red + z0.1 (dest structural)
        specs.append((sym, "15m", "%s_h1_red_z" % base, [
            L("h1_ema19", "lt", feat2="h1_ema53"),
            L("rsi14", "gt", 56.0), L("z20", "gt", 0.1),
            L("macd_stick", "lt", 0.0), L("close", "lt", feat2="open"),
            L("close", "lt", feat2="ema16"),
        ], 20))

    # BNB 15m short band (new vs wave1 pullback fade)
    specs.append(("BNB-USDT-SWAP", "15m", "bnb_xrp_band", [
        L("rsi14", "gt", 55.0), L("rsi14", "lt", 72.0),
        L("z20", "gt", 0.1), L("macd_stick", "lt", 0.0),
        L("close", "lt", feat2="ema16"), L("close", "lt", feat2="open"),
    ], 20))

    for sym, tf, tag, entry, hold in specs:
        out.append(make(sym, tf, "short", "exhaustion_fade", entry, short_exit, hold, tag))
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


def group_dirs(results, finals):
    by = {}
    for r in results:
        k = "%s|%s|%s" % (r.get("symbol"), r.get("tf"), r.get("logic"))
        by.setdefault(k, []).append(r)
    dirs = []
    for k, rows in by.items():
        best = sorted(rows, key=lambda x: (
            1 if x.get("quick") else 0, 1 if x.get("dest") else 0,
            x.get("fp") or 0, float(x.get("wr") or 0)), reverse=True)[0]
        promo = None
        for f in finals:
            if f.get("symbol") == best.get("symbol") and f.get("tf") == best.get("tf"):
                if promo is None or f.get("ok"):
                    promo = f
        if promo and promo.get("ok"):
            status, fail = "pending", None
        elif promo:
            status, fail = "promote_fail", promo.get("failed_step")
        else:
            status, fail = "probe_fail", best.get("fail") or "quick_walk_forward"
        dirs.append({
            "direction": k, "best_tag": best.get("tag"), "status": status,
            "fail_step": fail, "tr": best.get("tr"), "fp": best.get("fp"),
            "wr": best.get("wr"), "dest": best.get("dest"),
            "pending_key": ((promo or {}).get("pending") or {}).get("key"),
            "ds": ((promo or {}).get("sim") or {}).get("wr_deepseek_sim"),
            "qw": ((promo or {}).get("sim") or {}).get("wr_qwen_sim"),
        })
    return dirs


def main():
    print("[wave2d] start", _now(), flush=True)
    cands = candidates()
    print("[wave2d] n=", len(cands), "SERIAL", flush=True)
    results = []
    for i, b in enumerate(cands):
        print("[wave2d] probe %d/%d %s" % (i + 1, len(cands), b["title"]), flush=True)
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
        # early promote if we already have 2 quick winners spanning symbols
        if sum(1 for r in results if r.get("quick")) >= 2 and i >= 5:
            # finish a few more? keep going for diversity but allow early promote path later
            pass

    winners = [r for r in results if r.get("quick")]
    # dest-pass near for repair
    dest_near = sorted(
        [r for r in results if (r.get("dest") is True) and (r.get("fp") or 0) >= 5],
        key=lambda x: (x.get("fp") or 0, float(x.get("wr") or 0)), reverse=True
    )[:4]
    print("[wave2d] winners", [w.get("tag") for w in winners], flush=True)
    print("[wave2d] dest_near", [(n.get("tag"), n.get("fp"), n.get("tr")) for n in dest_near], flush=True)

    promote_list = list(winners[:4])
    for n in dest_near:
        if len(promote_list) >= 4:
            break
        if any(p.get("tag") == n.get("tag") for p in promote_list):
            continue
        b = n.get("book")
        if not b:
            continue
        # if dest ok but wf short, loosen once
        if not n.get("quick"):
            b2 = f3.local_tweak(b, 1, "quick_walk_forward")
            q = f2.quick_suite(b2)
            print("[wave2d] dest_near_tweak", n.get("tag"), q.get("quick_pass"),
                  q.get("failed_step"), (q.get("base_metrics") or {}).get("fold_positive"), flush=True)
            if q.get("quick_pass"):
                promote_list.append({
                    "tag": n["tag"] + "_wftweak", "book": b2, "packs": q,
                    "symbol": b2["symbol"], "tf": b2["timeframe"],
                })

    # dest-fail but fp>=7: try dest-oriented tweak (tighten)
    if len(promote_list) < 2:
        hi = sorted(
            [r for r in results if (r.get("fp") or 0) >= 6 and r.get("book")],
            key=lambda x: (x.get("fp") or 0, float(x.get("wr") or 0)), reverse=True
        )[:3]
        for n in hi:
            b = f3.local_tweak(n["book"], 1, "quick_logic_destruction")
            # add structural red if missing
            feats = {((e.get("left") or {}).get("feature"), e.get("op"))
                     for e in (b["dsl"].get("entry") or {}).get("all") or []}
            if ("close", "lt") not in feats:
                b["dsl"]["entry"]["all"].append(L("close", "lt", feat2="open"))
                b["dsl"] = f2.ensure_dsl(b["dsl"], b["symbol"], b["timeframe"])
                b["dsl"]["key"] = (b["dsl"]["key"] + "_red")[:100]
            repaired, _ = f3.glm_repair(b, n.get("packs_full") or {}, "quick_logic_destruction", "quick")
            if repaired:
                b = repaired
            q = f2.quick_suite(b)
            print("[wave2d] dest_repair", n.get("tag"), q.get("quick_pass"), q.get("failed_step"),
                  (q.get("base_metrics") or {}).get("trades"),
                  (q.get("base_metrics") or {}).get("fold_positive"),
                  (q.get("logic_destruction") or {}).get("pass"), flush=True)
            if q.get("quick_pass"):
                promote_list.append({
                    "tag": n["tag"] + "_destfix", "book": b, "packs": q,
                    "symbol": b["symbol"], "tf": b["timeframe"],
                })

    finals = []
    workers = min(2, max(1, len(promote_list)))
    print("[wave2d] promote", len(promote_list), "workers", workers, flush=True)
    if promote_list:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(promote, r["book"], r["packs"]): r for r in promote_list
                    if r.get("book") and r.get("packs")}
            for fut in as_completed(futs):
                src = futs[fut]
                try:
                    res = fut.result()
                except Exception as exc:
                    res = {"ok": False, "failed_step": "exception", "reason": str(exc),
                           "symbol": src.get("symbol"), "tf": src.get("tf")}
                res["from_tag"] = src.get("tag")
                finals.append(res)
                print("[promote]", res.get("from_tag"), "ok", res.get("ok"),
                      "fail", res.get("failed_step"),
                      "pending", (res.get("pending") or {}).get("key"),
                      "ds", ((res.get("sim") or {}).get("wr_deepseek_sim")),
                      "qw", ((res.get("sim") or {}).get("wr_qwen_sim")), flush=True)

    pending = [r for r in finals if r.get("ok")]
    dirs = group_dirs(results, finals)
    end = {
        "at": _now(), "op": "寒霜叁-wave2d",
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
        "probe_results": [{k: v for k, v in r.items() if k not in ("book", "packs", "packs_full")}
                          for r in results],
    }
    _write("%s_end_report.json" % PREFIX, end)

    lines = ["# 寒霜叁 总结报告（Wave1+2b+2c+2d）", "", "时间: %s" % end["at"], ""]
    lines.append("## 总目标 ≥2 pending → pending_n=%d success=%s" % (end["pending_n"], end["success"]))
    lines.append("")
    if pending:
        lines.append("## 已入正式 pending")
        for p in end["pending_detail"]:
            lines.append("- `%s` (%s %s) DeepSeek=%.1f%% Qwen=%.1f%% from=%s" % (
                p.get("key"), p.get("symbol"), p.get("tf"),
                float(p.get("ds") or 0), float(p.get("qw") or 0), p.get("from_tag")))
        lines.append("")
    lines.append("## Wave2d 各方向")
    for drow in dirs:
        if drow.get("status") == "pending":
            lines.append("- **%s** → pending `%s` DS=%s Qwen=%s" % (
                drow["direction"], drow.get("pending_key"), drow.get("ds"), drow.get("qw")))
        else:
            lines.append("- **%s** best=`%s` → `%s` (tr=%s fp=%s wr=%s dest=%s)" % (
                drow["direction"], drow.get("best_tag"), drow.get("fail_step"),
                drow.get("tr"), drow.get("fp"), drow.get("wr"), drow.get("dest")))
    lines.append("")
    lines.append("## Wave1")
    lines.append("- SOL5m impulse → validate/quick 失败")
    lines.append("- BNB15m trend_pullback_fade → quick_walk_forward (fp≈3)")
    lines.append("- DOGE15m range_reclaim → quick_walk_forward (fp≈4)")
    lines.append("## Wave2b")
    lines.append("- 全部 tr=0（1h缺h1 / 过滤过紧）→ aborted")
    lines.append("## Wave2c")
    lines.append("- 最佳 btc_15m_xrpport tr14 fp6 wr57 destFAIL；rescue fp7 仍 destFAIL")
    lines.append("- 全部方向 → quick_walk_forward 或 quick_logic_destruction")
    zh = os.path.join(OUT, "frost3_wave2_最终报告.md")
    open(zh, "w").write("\n".join(lines) + "\n")
    open(os.path.join(OUT, "%s_报告.md" % PREFIX), "w").write("\n".join(lines) + "\n")
    print("[wave2d] wrote", zh, flush=True)

    try:
        main_end = json.load(open(os.path.join(OUT, "frost3_end_report.json")))
    except Exception:
        main_end = {}
    main_end["wave2d"] = {
        "at": end["at"], "pending_n": end["pending_n"],
        "pending_keys": end["pending_keys"], "success": end["success"],
        "directions": dirs,
    }
    keys = [k for k in (list(main_end.get("pending_keys_total") or main_end.get("pending_keys") or [])
                        + end["pending_keys"]) if k]
    # dedupe
    seen = set()
    uniq = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    main_end["pending_keys_total"] = uniq
    main_end["pending_n_total"] = len(uniq)
    main_end["success"] = len(uniq) >= TARGET
    main_end["updated_at"] = _now()
    main_end["final_report_zh"] = zh
    _write("frost3_end_report.json", main_end)
    print("[wave2d] END", json.dumps({
        "pending_n": end["pending_n"], "pending_keys": end["pending_keys"],
        "success": end["success"], "winners": end["quick_winners"],
    }, ensure_ascii=False), flush=True)
    return 0 if end["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
