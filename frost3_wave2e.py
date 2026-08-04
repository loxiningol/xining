#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜叁 Wave2e — port proven BTC1h slope-exhaustion to ETH/SOL/BNB/LINK 1h.

Proven pending: frost3_btc1h_xrpport_exhaustion_fade_slope
  entry: rsi>50, z>0, macd<0, close<ema16, cci>40, h1_slope4<0.001; hold20
Skip BTC (already awaiting_confirm). Avoid ADA/LTC/NG/XRP.
Serial; promote ≤2 parallel. Target +1 pending (≥2 total with BTC1h).
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
PREFIX = "frost3_wave2e"
TARGET = 1  # need one more (BTC1h already pending)


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


def make(sym, tf, entry, hold, tag, rsi_tp=45.0):
    exit_any = [
        L("rsi14", "lt", rsi_tp, role="take_profit"),
        L("close", "gt", feat2="prev_high20", role="invalidation"),
    ]
    key = ("frost3w2e_%s" % tag.replace(".", "p"))[:100]
    dsl = f2.ensure_dsl({
        "key": key,
        "name": "寒霜叁W2e-%s" % tag,
        "direction": "short",
        "description": "BTC1h slope-exhaustion port; CL-aware mild floors",
        "entry": {"all": entry}, "exit": {"any": exit_any},
        "max_hold_bars": int(hold),
    }, sym, tf)
    return {
        "title": tag, "symbol": sym, "timeframe": tf, "direction": "short",
        "logic_class": "exhaustion_fade",
        "thesis": "wave2e_btc1h_slope_port:%s" % tag, "dsl": dsl,
        "gate_mode": "frost2", "source": "frost3_wave2e", "dir_rank": 1,
    }


def candidates():
    out = []
    # Exact proven template + mild variants; NO BTC
    for sym in ["ETH-USDT-SWAP", "SOL-USDT-SWAP", "BNB-USDT-SWAP", "LINK-USDT-SWAP"]:
        base = sym.split("-")[0].lower()
        # exact clone of BTC1h winner
        out.append(make(sym, "1h", [
            L("rsi14", "gt", 50.0), L("z20", "gt", 0.0),
            L("macd_stick", "lt", 0.0), L("close", "lt", feat2="ema16"),
            L("cci", "gt", 40.0), L("h1_slope4", "lt", 0.001),
        ], 20, "%s_1h_slope_exact" % base))
        # slightly tighter rsi (anti-marginal vs CL54) keep slope
        out.append(make(sym, "1h", [
            L("rsi14", "gt", 52.0), L("z20", "gt", 0.0),
            L("macd_stick", "lt", 0.0), L("close", "lt", feat2="ema16"),
            L("cci", "gt", 45.0), L("h1_slope4", "lt", 0.001),
        ], 18, "%s_1h_slope_r52" % base))
        # slope stricter
        out.append(make(sym, "1h", [
            L("rsi14", "gt", 50.0), L("z20", "gt", 0.05),
            L("macd_stick", "lt", 0.0), L("close", "lt", feat2="ema16"),
            L("cci", "gt", 40.0), L("h1_slope4", "lt", 0.0005),
        ], 20, "%s_1h_slope_strict" % base))
        # red body add-on
        out.append(make(sym, "1h", [
            L("rsi14", "gt", 50.0), L("z20", "gt", 0.0),
            L("macd_stick", "lt", 0.0), L("close", "lt", feat2="ema16"),
            L("close", "lt", feat2="open"),
            L("cci", "gt", 40.0), L("h1_slope4", "lt", 0.001),
        ], 20, "%s_1h_slope_red" % base))
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


def existing_frost3_pending():
    try:
        d = json.load(open("/root/auto_trade/strategy_pending_human_confirm.json"))
    except Exception:
        return []
    out = []
    for it in d.get("items") or []:
        k = str(it.get("key") or "")
        st = str(it.get("status") or "")
        if "frost3" in k and st in ("awaiting_confirm", "pending", "等待"):
            wr = it.get("ai_theoretical_wr_by_provider") or {}
            out.append({
                "key": k, "status": st, "symbol": it.get("symbol"),
                "tf": it.get("timeframe"),
                "ds": wr.get("deepseek"), "qw": wr.get("qwen"),
                "avg": it.get("ai_theoretical_wr_avg"),
            })
    return out


def main():
    print("[wave2e] start", _now(), flush=True)
    already = existing_frost3_pending()
    print("[wave2e] already_pending", already, flush=True)
    _write("%s_already_pending.json" % PREFIX, {"at": _now(), "items": already})

    cands = candidates()
    print("[wave2e] n=", len(cands), "SERIAL", flush=True)
    results = []
    for i, b in enumerate(cands):
        print("[wave2e] probe %d/%d %s" % (i + 1, len(cands), b["title"]), flush=True)
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

    winners = [r for r in results if r.get("quick")]
    near = sorted(
        [r for r in results if not r.get("quick") and (r.get("fp") or 0) >= 6
         and float(r.get("tr") or 0) >= 10],
        key=lambda x: (1 if x.get("dest") else 0, x.get("fp") or 0, float(x.get("wr") or 0)),
        reverse=True,
    )[:4]
    print("[wave2e] winners", [w.get("tag") for w in winners], flush=True)

    promote_list = list(winners[:3])
    for n in near:
        if len(promote_list) >= 3:
            break
        b = n.get("book")
        if not b:
            continue
        fail = n.get("fail") or "quick_walk_forward"
        b2 = f3.local_tweak(b, 1, fail)
        repaired, _ = f3.glm_repair(b2, n.get("packs_full") or {}, fail, "quick")
        if repaired:
            b2 = repaired
        q = f2.quick_suite(b2)
        print("[wave2e] near_fix", n.get("tag"), q.get("quick_pass"), q.get("failed_step"),
              (q.get("base_metrics") or {}).get("fold_positive"),
              (q.get("logic_destruction") or {}).get("pass"), flush=True)
        if q.get("quick_pass"):
            promote_list.append({
                "tag": n["tag"] + "_fix", "book": b2, "packs": q,
                "symbol": b2["symbol"], "tf": b2["timeframe"],
            })

    finals = []
    workers = min(2, max(1, len(promote_list)))
    print("[wave2e] promote", len(promote_list), "workers", workers, flush=True)
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
                if sum(1 for f in finals if f.get("ok")) >= TARGET:
                    break

    pending_new = [r for r in finals if r.get("ok")]
    already2 = existing_frost3_pending()
    all_keys = [a["key"] for a in already2] + [
        ((r.get("pending") or {}).get("key")) for r in pending_new]
    all_keys = [k for k in all_keys if k]

    # per-direction summary
    by = {}
    for r in results:
        k = "%s|%s" % (r.get("symbol"), r.get("tf"))
        by.setdefault(k, []).append(r)
    dirs = []
    for k, rows in by.items():
        best = sorted(rows, key=lambda x: (
            1 if x.get("quick") else 0, x.get("fp") or 0, float(x.get("wr") or 0)), reverse=True)[0]
        promo = next((f for f in finals if f.get("symbol") == best.get("symbol") and f.get("ok")), None)
        if not promo:
            promo = next((f for f in finals if f.get("symbol") == best.get("symbol")), None)
        if promo and promo.get("ok"):
            dirs.append({"direction": k, "status": "pending",
                         "pending_key": (promo.get("pending") or {}).get("key"),
                         "ds": (promo.get("sim") or {}).get("wr_deepseek_sim"),
                         "qw": (promo.get("sim") or {}).get("wr_qwen_sim"),
                         "best_tag": best.get("tag")})
        else:
            dirs.append({
                "direction": k, "status": "fail",
                "fail_step": (promo or {}).get("failed_step") or best.get("fail"),
                "best_tag": best.get("tag"),
                "tr": best.get("tr"), "fp": best.get("fp"), "wr": best.get("wr"),
                "dest": best.get("dest"),
            })

    end = {
        "at": _now(), "op": "寒霜叁-wave2e",
        "already_pending": already,
        "already_pending_after": already2,
        "probe_n": len(results),
        "quick_winners": [w.get("tag") for w in winners],
        "promote_tags": [p.get("tag") for p in promote_list],
        "pending_n_new": len(pending_new),
        "pending_keys_new": [((r.get("pending") or {}).get("key")) for r in pending_new],
        "pending_detail_new": [{
            "key": (r.get("pending") or {}).get("key"),
            "symbol": r.get("symbol"), "tf": r.get("tf"),
            "from_tag": r.get("from_tag"),
            "ds": (r.get("sim") or {}).get("wr_deepseek_sim"),
            "qw": (r.get("sim") or {}).get("wr_qwen_sim"),
        } for r in pending_new],
        "frost3_pending_total": len(all_keys),
        "frost3_pending_keys": all_keys,
        "directions": dirs,
        "finals": finals,
        "success": len(all_keys) >= 2,
        "probe_results": [{k: v for k, v in r.items() if k not in ("book", "packs", "packs_full")}
                          for r in results],
    }
    _write("%s_end_report.json" % PREFIX, end)

    lines = ["# 寒霜叁 Wave2 最终中文报告", "", "时间: %s" % end["at"], ""]
    lines.append("## 正式 pending 汇总（frost3）")
    lines.append("合计: **%d** keys → success=%s" % (len(all_keys), end["success"]))
    for a in already2:
        lines.append("- `%s` status=%s %s %s DS=%s%% Qwen=%s%%" % (
            a.get("key"), a.get("status"), a.get("symbol"), a.get("tf"),
            a.get("ds"), a.get("qw")))
    for p in end["pending_detail_new"]:
        lines.append("- `%s` (NEW) %s %s DS=%s Qwen=%s from=%s" % (
            p.get("key"), p.get("symbol"), p.get("tf"),
            p.get("ds"), p.get("qw"), p.get("from_tag")))
    lines.append("")
    lines.append("## Wave2e 各方向")
    for drow in dirs:
        if drow.get("status") == "pending":
            lines.append("- **%s** → pending `%s` DS=%s Qwen=%s" % (
                drow["direction"], drow.get("pending_key"), drow.get("ds"), drow.get("qw")))
        else:
            lines.append("- **%s** best=`%s` → 失败步骤=`%s` (tr=%s fp=%s wr=%s dest=%s)" % (
                drow["direction"], drow.get("best_tag"), drow.get("fail_step"),
                drow.get("tr"), drow.get("fp"), drow.get("wr"), drow.get("dest")))
    lines.append("")
    lines.append("## 先前波次失败摘要")
    lines.append("- Wave1: SOL5m→validate; BNB15m→quick_wf(fp3); DOGE15m→quick_wf(fp4)")
    lines.append("- Wave2b: 全tr=0（1h缺h1_ema / 过紧）")
    lines.append("- Wave2c: BTC15m xrpport 近(fp6→rescue fp7) 卡 dest")
    lines.append("- Wave2d: dest结构加严后交易更稀，无 quick pass")
    lines.append("- Wave2e: 移植已过 pending 的 BTC1h slope 模板到 ETH/SOL/BNB/LINK")
    zh = os.path.join(OUT, "frost3_wave2_最终报告.md")
    open(zh, "w").write("\n".join(lines) + "\n")
    open(os.path.join(OUT, "%s_报告.md" % PREFIX), "w").write("\n".join(lines) + "\n")
    print("[wave2e] wrote", zh, flush=True)

    try:
        main_end = json.load(open(os.path.join(OUT, "frost3_end_report.json")))
    except Exception:
        main_end = {}
    main_end["wave2e"] = end
    main_end["pending_keys_total"] = all_keys
    main_end["pending_n_total"] = len(all_keys)
    main_end["success"] = len(all_keys) >= 2
    main_end["updated_at"] = _now()
    main_end["final_report_zh"] = zh
    _write("frost3_end_report.json", main_end)
    print("[wave2e] END", json.dumps({
        "pending_total": len(all_keys), "keys": all_keys,
        "new": end["pending_keys_new"], "success": end["success"],
    }, ensure_ascii=False), flush=True)
    return 0 if end["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
