#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜叁 Wave2b — SERIAL niches (new logics), avoid Wave1 dead stacks + CL traps.

Wave1 dead (do not clone): SOL5m impulse, BNB15m trend_pullback_fade, DOGE15m range_reclaim.
Avoid: ADA/LTC/NG/XRP. Gates: WF≥7, dest, MC≥90%, friction Sharpe≥0.
Max 2 parallel only at promote; probes are serial.
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
PREFIX = "frost3_wave2b"
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
    key = ("frost3w2b_%s" % tag.replace(".", "p"))[:100]
    dsl = f2.ensure_dsl({
        "key": key, "name": "寒霜叁W2b-%s" % tag, "direction": direction,
        "entry": {"all": entry}, "exit": {"any": exit_any},
        "max_hold_bars": int(hold),
    }, sym, tf)
    return {
        "title": tag, "symbol": sym, "timeframe": tf, "direction": direction,
        "logic_class": logic, "thesis": "wave2b_new_niche:%s" % tag, "dsl": dsl,
        "gate_mode": "frost2", "source": "frost3_wave2b", "dir_rank": 1,
    }


def candidates():
    """Focused NEW niches — not Wave1 clones."""
    out = []

    # D1: LINK 1h short exhaustion — H1 down + raised RSI/CCI (anti-CL marginal)
    # (Wave1 never ran LINK 1h)
    for rsi, z, cci, hold in [(60, 0.35, 85, 14), (62, 0.45, 100, 12), (58, 0.25, 70, 16)]:
        tag = "link_1h_exh_r%.0f_z%s_c%.0f" % (rsi, str(z).replace(".", "p"), cci)
        entry = [
            L("h1_ema19", "lt", feat2="h1_ema53"),
            L("rsi14", "gt", rsi), L("z20", "gt", z),
            L("macd_stick", "lt", 0.0),
            L("close", "lt", feat2="ema16"),
            L("close", "lt", feat2="open"),
            L("cci", "gt", cci),
        ]
        exit_any = [
            L("rsi14", "lt", 48.0, role="take_profit"),
            L("close", "gt", feat2="prev_high20", role="invalidation"),
        ]
        out.append(make("LINK-USDT-SWAP", "1h", "short", "exhaustion_fade",
                        entry, exit_any, hold, tag))

    # D2: AVAX 1h long impulse breakout — sparse H1 up + break prev_high (not DOGE15m reclaim)
    for rsi, z, hold in [(58, 0.4, 14), (60, 0.5, 12), (55, 0.3, 16)]:
        tag = "avax_1h_imp_r%.0f_z%s" % (rsi, str(z).replace(".", "p"))
        entry = [
            L("h1_ema19", "gt", feat2="h1_ema53"),
            L("h1_slope4", "gt", 0.0),
            L("close", "gt", feat2="ema21"),
            L("close", "gt", feat2="prev_high20"),
            L("rsi14", "gt", rsi), L("macd_stick", "gt", 0.0),
            L("z20", "gt", z),
        ]
        exit_any = [
            L("rsi14", "lt", 50.0, role="take_profit"),
            L("close", "lt", feat2="ema21", role="invalidation"),
        ]
        out.append(make("AVAX-USDT-SWAP", "1h", "long", "impulse_continuation",
                        entry, exit_any, hold, tag))

    # D3: ETH 1h short exhaustion (not ETH5m / not SOL5m) — stronger than CL RSI54
    for rsi, z, cci, hold in [(61, 0.4, 90, 14), (63, 0.5, 110, 12)]:
        tag = "eth_1h_exh_r%.0f_z%s_c%.0f" % (rsi, str(z).replace(".", "p"), cci)
        entry = [
            L("h1_ema19", "lt", feat2="h1_ema53"),
            L("rsi14", "gt", rsi), L("z20", "gt", z),
            L("macd_stick", "lt", 0.0),
            L("close", "lt", feat2="ema16"),
            L("cci", "gt", cci),
        ]
        exit_any = [
            L("rsi14", "lt", 46.0, role="take_profit"),
            L("close", "gt", feat2="prev_high20", role="invalidation"),
        ]
        out.append(make("ETH-USDT-SWAP", "1h", "short", "exhaustion_fade",
                        entry, exit_any, hold, tag))

    # D4: BTC 15m long reclaim after deep oversold — NEW vs Wave1 shorts/impulses
    # Keep enough trades for WF≥7 (CL trap: don't starve)
    for rsi_hi, z_lo, hold in [(42, -1.0, 24), (40, -1.25, 20), (45, -0.75, 28)]:
        tag = "btc_15m_reclaim_r%.0f_z%s" % (rsi_hi, str(z_lo).replace(".", "m").replace("-", ""))
        entry = [
            L("rsi14", "lt", rsi_hi),
            L("rsi14", "gt", 28.0),
            L("z20", "lt", z_lo),
            L("z20", "gt", -3.5),
            L("close", "gt", feat2="ema16"),
            L("close", "gt", feat2="open"),
            L("macd_stick", "gt", 0.0),
            L("h1_slope4", "gt", -0.01),
        ]
        exit_any = [
            L("rsi14", "gt", 58.0, role="take_profit"),
            L("close", "lt", feat2="prev_low20", role="invalidation"),
        ]
        out.append(make("BTC-USDT-SWAP", "15m", "long", "range_reclaim",
                        entry, exit_any, hold, tag))

    # D5: SOL 1h short exhaustion (NOT 5m impulse clone) — H1 down + body confirm
    for rsi, z, cci, hold in [(60, 0.35, 80, 14), (62, 0.45, 95, 12)]:
        tag = "sol_1h_exh_r%.0f_z%s_c%.0f" % (rsi, str(z).replace(".", "p"), cci)
        entry = [
            L("h1_ema19", "lt", feat2="h1_ema53"),
            L("rsi14", "gt", rsi), L("z20", "gt", z),
            L("macd_stick", "lt", 0.0),
            L("close", "lt", feat2="ema16"),
            L("close", "lt", feat2="open"),
            L("cci", "gt", cci),
        ]
        exit_any = [
            L("rsi14", "lt", 47.0, role="take_profit"),
            L("close", "gt", feat2="prev_high20", role="invalidation"),
        ]
        out.append(make("SOL-USDT-SWAP", "1h", "short", "exhaustion_fade",
                        entry, exit_any, hold, tag))

    # D6: BNB 5m long impulse — session-aware sparse (NOT BNB15m pullback fade)
    for rsi, z, hold in [(58, 0.45, 32), (60, 0.55, 28)]:
        tag = "bnb_5m_imp_r%.0f_z%s" % (rsi, str(z).replace(".", "p"))
        entry = [
            L("h1_ema19", "gt", feat2="h1_ema53"),
            L("h1_slope4", "gt", 0.0),
            L("close", "gt", feat2="ema21"),
            L("close", "gt", feat2="prev_high20"),
            L("rsi14", "gt", rsi), L("macd_stick", "gt", 0.0),
            L("z20", "gt", z),
        ]
        exit_any = [
            L("rsi14", "lt", 50.0, role="take_profit"),
            L("close", "lt", feat2="ema21", role="invalidation"),
        ]
        out.append(make("BNB-USDT-SWAP", "5m", "long", "impulse_continuation",
                        entry, exit_any, hold, tag))

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


def group_fail_report(results, finals):
    """Per-direction fail step for Chinese end report."""
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
                float(x.get("wr") or 0),
            ),
            reverse=True,
        )[0]
        # find promote outcome for this symbol/tf
        promo = None
        for f in finals:
            if f.get("symbol") == best.get("symbol") and f.get("tf") == best.get("tf"):
                promo = f
                break
        if promo and promo.get("ok"):
            fail_step = None
            status = "pending"
        elif promo:
            fail_step = promo.get("failed_step")
            status = "promote_fail"
        else:
            fail_step = best.get("fail") or "quick_walk_forward"
            status = "probe_fail"
        dirs.append({
            "direction": key,
            "best_tag": best.get("tag"),
            "status": status,
            "fail_step": fail_step,
            "tr": best.get("tr"), "fp": best.get("fp"),
            "wr": best.get("wr"), "sh": best.get("sh"),
            "dest": best.get("dest"),
            "pending_key": ((promo or {}).get("pending") or {}).get("key"),
            "ds": ((promo or {}).get("sim") or {}).get("wr_deepseek_sim"),
            "qw": ((promo or {}).get("sim") or {}).get("wr_qwen_sim"),
        })
    return dirs


def main():
    print("[wave2b] start", _now(), flush=True)
    _write("%s_status.json" % PREFIX, {"at": _now(), "stage": "probe", "load_hint": "serial"})
    cands = candidates()
    print("[wave2b] probe n=", len(cands), "SERIAL", flush=True)
    results = []
    # SERIAL probes to avoid overload
    for i, b in enumerate(cands):
        print("[wave2b] probe %d/%d %s" % (i + 1, len(cands), b["title"]), flush=True)
        row = probe_one(b)
        results.append(row)
        print("[probe]", row.get("tag"), "quick", row.get("quick"),
              "tr", row.get("tr"), "fp", row.get("fp"), "dest", row.get("dest"),
              "fail", row.get("fail"), flush=True)
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
    near = sorted(
        [r for r in results if not r.get("quick") and (r.get("fp") or 0) >= 6
         and (r.get("dest") is True) and float(r.get("wr") or 0) >= 55],
        key=lambda x: (x.get("fp") or 0, x.get("sh") or -9), reverse=True
    )[:4]
    print("[wave2b] quick_winners", len(winners), [w.get("tag") for w in winners], flush=True)
    print("[wave2b] near", [n.get("tag") for n in near], flush=True)

    promote_list = list(winners[:4])
    for n in near:
        if len(promote_list) >= 4:
            break
        b = n.get("book")
        if not b:
            continue
        print("[wave2b] near_repair", n.get("tag"), flush=True)
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
            print("[wave2b] near became quick", n.get("tag"), flush=True)

    # If still empty, try one GLM repair on top-2 by fp among dest-pass
    if not promote_list:
        rescue = sorted(
            [r for r in results if r.get("book") and (r.get("dest") is True)],
            key=lambda x: (x.get("fp") or 0, float(x.get("wr") or 0)), reverse=True
        )[:2]
        for n in rescue:
            b = n["book"]
            print("[wave2b] rescue_repair", n.get("tag"), "fp", n.get("fp"), flush=True)
            b2 = f3.local_tweak(b, 1, n.get("fail") or "quick_walk_forward")
            repaired, _ = f3.glm_repair(b2, n.get("packs_full") or {}, n.get("fail") or "quick", "quick")
            if repaired:
                b2 = repaired
            q = f2.quick_suite(b2)
            if q.get("quick_pass"):
                promote_list.append({
                    "tag": n["tag"] + "_rescue", "book": b2, "packs": q, "quick": True,
                    "symbol": b2["symbol"], "tf": b2["timeframe"],
                })

    finals = []
    # promote max 2 parallel
    workers = min(2, max(1, len(promote_list)))
    print("[wave2b] promote n=", len(promote_list), "workers=", workers, flush=True)
    _write("%s_status.json" % PREFIX, {
        "at": _now(), "stage": "promote", "n": len(promote_list),
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
                # early stop if target met
                if sum(1 for f in finals if f.get("ok")) >= TARGET:
                    break

    pending = [r for r in finals if r.get("ok")]
    dirs = group_fail_report(results, finals)
    end = {
        "at": _now(),
        "op": "寒霜叁-wave2b",
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
        "finals": [{k: v for k, v in r.items() if k != "formal" or True} for r in finals],
        "success": len(pending) >= TARGET,
        "cl_traps_applied": [
            "no_marginal_rsi54", "keep_trade_count_for_wf", "raised_cci_floors",
            "avoid_wave1_sol5m_bnb15m_doge15m_clones",
        ],
    }
    _write("%s_end_report.json" % PREFIX, end)

    # Chinese summary artifact
    lines = ["# 寒霜叁 Wave2b 报告", "", "时间: %s" % end["at"], ""]
    lines.append("## 目标: ≥2 入正式 pending")
    lines.append("结果: pending_n=%d success=%s" % (end["pending_n"], end["success"]))
    lines.append("")
    if pending:
        lines.append("## 已入 pending")
        for p in end["pending_detail"]:
            lines.append("- `%s` (%s %s) DS=%.1f%% Qwen=%.1f%%  from=%s" % (
                p.get("key"), p.get("symbol"), p.get("tf"),
                float(p.get("ds") or 0), float(p.get("qw") or 0), p.get("from_tag")))
        lines.append("")
    lines.append("## 各方向失败步骤 / 状态")
    for drow in dirs:
        if drow.get("status") == "pending":
            lines.append("- **%s** → pending `%s` DS=%s Qwen=%s" % (
                drow["direction"], drow.get("pending_key"), drow.get("ds"), drow.get("qw")))
        else:
            lines.append(
                "- **%s** best=`%s` → 失败步骤=`%s` (tr=%s fp=%s wr=%s dest=%s sh=%s)" % (
                    drow["direction"], drow.get("best_tag"), drow.get("fail_step"),
                    drow.get("tr"), drow.get("fp"), drow.get("wr"),
                    drow.get("dest"), drow.get("sh")))
    zh_path = os.path.join(OUT, "%s_报告.md" % PREFIX)
    open(zh_path, "w").write("\n".join(lines) + "\n")
    print("[wave2b] wrote", zh_path, flush=True)

    try:
        main_end = json.load(open(os.path.join(OUT, "frost3_end_report.json")))
    except Exception:
        main_end = {}
    main_end["wave2b"] = {
        "at": end["at"], "pending_n": end["pending_n"],
        "pending_keys": end["pending_keys"], "success": end["success"],
        "directions": dirs,
    }
    main_end["pending_n_total"] = int(main_end.get("pending_n") or 0) + end["pending_n"]
    main_end["pending_keys_total"] = list(main_end.get("pending_keys") or []) + end["pending_keys"]
    main_end["success"] = len([k for k in main_end["pending_keys_total"] if k]) >= TARGET
    main_end["updated_at"] = _now()
    _write("frost3_end_report.json", main_end)
    _write("%s_status.json" % PREFIX, {"at": _now(), "stage": "done", "end": end})

    print("[wave2b] END", json.dumps({
        "pending_n": end["pending_n"], "pending_keys": end["pending_keys"],
        "success": end["success"], "quick_winners": end["quick_winners"],
        "dirs": [(d["direction"], d.get("fail_step") or d.get("status")) for d in dirs],
    }, ensure_ascii=False), flush=True)
    return 0 if end["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
