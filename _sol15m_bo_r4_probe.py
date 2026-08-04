#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe mid-band SOL15m trend_breakout params, then run best through gates (R4)."""
from __future__ import print_function

import json
import os
import sys
import copy
from datetime import datetime

sys.path.insert(0, "/root")
import frost2_action_run as f2
import frost3_action as f3
import frost3_sol15m_breakout_run as m
import frost3_step1 as step1
import auto_trade_dual_engine_factory as d

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
OUT = "/root/auto_trade/dual_engine"
PREFIX = "frost3_sol15m_breakout"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def w(suffix, obj):
    path = os.path.join(OUT, "%s_%s.json" % (PREFIX, suffix))
    open(path, "w").write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    print("[write]", suffix, flush=True)
    return path


def L(feat, op, val=None, feat2=None, offset=None, role=None):
    right = {"value": float(val)} if val is not None else {"feature": feat2}
    if offset is not None:
        right["offset"] = int(offset)
    o = {"left": {"feature": feat}, "op": op, "right": right}
    if role:
        o["role"] = role
    return o


def make(tag, cci_min, z_lo, z_hi, rsi_lo, rsi_hi, hold, use_atr_proxy=True):
    entry = [
        L("h1_ema19", "gt", feat2="h1_ema53"),
        L("h1_slope4", "gt", 0.0),
        L("close", "gt", feat2="prev_high20"),
        L("close", "gt", feat2="ema21"),
        L("rsi14", "gt", rsi_lo),
        L("rsi14", "lt", rsi_hi),
        L("macd_stick", "gt", 0.0),
        L("z20", "gt", z_lo),
        L("z20", "lt", z_hi),
        L("cci", "gt", cci_min),
    ]
    if use_atr_proxy:
        entry.append(L("atr14", "gte", feat2="atr14", offset=8))
    dsl = f2.ensure_dsl({
        "key": "frost3_sol15m_breakout_%s" % tag,
        "name": "寒霜叁-SOL-15m-trend_breakout",
        "direction": "long",
        "timeframe": "15m",
        "supported_instruments": ["SOL-USDT-SWAP"],
        "max_hold_bars": hold,
        "entry": {"all": entry},
        "exit": {"any": [
            L("rsi14", "gt", 74.0, role="take_profit"),
            L("close", "lt", feat2="ema21", role="invalidation"),
        ]},
        "schema": "qiyu_strategy_dsl_v1",
    }, "SOL-USDT-SWAP", "15m")
    return {
        "title": tag,
        "symbol": "SOL-USDT-SWAP",
        "timeframe": "15m",
        "direction": "long",
        "logic_class": "trend_breakout",
        "thesis": "SOL15m H1-trend breakout mid-band %s" % tag,
        "dsl": dsl,
        "gate_mode": "frost2",
        "params": {
            "cci_min": cci_min, "z_lo": z_lo, "z_hi": z_hi,
            "rsi_lo": rsi_lo, "rsi_hi": rsi_hi, "hold": hold,
            "atr_proxy": use_atr_proxy,
        },
    }


def fail_out(end, hyp, death, arch_name):
    arch = os.path.join(OUT, "archive", arch_name)
    os.makedirs(arch, exist_ok=True)
    open(os.path.join(arch, "DEATH_CAUSE.json"), "w").write(
        json.dumps(death, ensure_ascii=False, indent=2, default=str) + "\n")
    open(os.path.join(arch, "result.json"), "w").write(
        json.dumps(end, ensure_ascii=False, indent=2, default=str) + "\n")
    end["archive_path"] = arch
    end["death_cause"] = death
    end["failed_step"] = death.get("exact_death")
    w("end_report", end)
    w("archive_pointer", {"archive_path": arch, "death_cause": death, "at": now()})
    summary = (
        "SOL15m趋势突破【失败归档R4】死因=%s 详情=%s 归档=%s "
        "EMA=%s 收缩=%s 量能=%s ATR匹配=%s best_probe=%s"
        % (
            death.get("exact_death"), death.get("summary_zh"), arch,
            hyp.get("ema_period"), hyp.get("vol_contraction_definition"),
            hyp.get("volume_threshold_and_rationale"),
            ((hyp.get("atr_stop_matching") or {}).get("formula_zh")),
            hyp.get("best_probe"),
        )
    )
    w("parent_summary_zh", {"summary_zh": summary, "at": now(), "end": {
        "ok": False, "failed_step": end.get("failed_step"),
        "archive_path": arch, "key": end.get("book_key"),
    }})
    print("PARENT_SUMMARY_ZH:", summary, flush=True)
    return 1


def main():
    print("=== SOL15m breakout R4 probe+gates START ===", now(), flush=True)
    d._ensure_dirs()
    d._load_env()

    # Mid-band grid: avoid R3 extremes (tr=1 vs tr=1010)
    grid = [
        ("cci45_z015_16", 45, 0.15, 1.6, 52, 72, 24, True),
        ("cci50_z02_15", 50, 0.2, 1.5, 53, 72, 24, True),
        ("cci40_z01_18", 40, 0.1, 1.8, 50, 74, 28, True),
        ("cci55_z02_14", 55, 0.2, 1.4, 54, 70, 24, True),
        ("cci45_noatr", 45, 0.15, 1.6, 52, 72, 24, False),
        ("cci35_z01_20", 35, 0.1, 2.0, 50, 74, 28, True),
        ("cci60_z025_13", 60, 0.25, 1.35, 55, 72, 24, True),
        ("cci48_z012_17", 48, 0.12, 1.7, 52, 73, 26, True),
    ]
    results = []
    for row in grid:
        tag = row[0]
        book = make(*row)
        print("[probe]", tag, now(), flush=True)
        try:
            packs = f2.quick_suite(book)
        except Exception as exc:
            results.append({"tag": tag, "error": str(exc)})
            print(" ERR", exc, flush=True)
            continue
        bm = packs.get("base_metrics") or {}
        rec = {
            "tag": tag,
            "params": book["params"],
            "qp": packs.get("quick_pass"),
            "fail": packs.get("failed_step"),
            "tr": bm.get("trades"),
            "fp": bm.get("fold_positive"),
            "folds": bm.get("folds"),
            "wr": bm.get("win_rate_pct"),
            "sh": bm.get("sharpe"),
            "mn": bm.get("mean_net"),
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
        }
        results.append(rec)
        print("[probe]", rec, flush=True)

    # Rank: prefer qp, then fp, then sharpe, require tr>=10 ideally
    def score(r):
        if r.get("error"):
            return (-99, -99, -99)
        tr = int(r.get("tr") or 0)
        fp = int(r.get("fp") or 0)
        folds = int(r.get("folds") or 0)
        qp = 1 if r.get("qp") else 0
        dest = 1 if r.get("dest") else 0
        sh = float(r.get("sh") or -99)
        # penalize extreme overtrade
        over = 1 if tr > 400 else 0
        return (qp, dest, 1 if tr >= 10 else 0, 1 if folds >= 10 else 0, fp, sh, -over)

    ranked = sorted(results, key=score, reverse=True)
    w("r4_probe_grid", {"at": now(), "results": results, "ranked": ranked[:8]})
    best = ranked[0]
    print("BEST", best, flush=True)

    # Rebuild best book
    best_params = None
    for row in grid:
        if row[0] == best.get("tag"):
            best_params = row
            break
    if best_params is None:
        best_params = grid[0]
    book = make(*best_params)

    hyp = {
        "at": now(),
        "round": "r4_midband_probe",
        "ema_period": "h1_19/53",
        "vol_contraction_definition": "z20 in (%.2f, %.2f]" % (
            best_params[2], best_params[3]),
        "volume_threshold_and_rationale": "cci>%.0f + macd_stick>0 (mid-band vs R3 starvation/overtrade)" % best_params[1],
        "atr_stop_matching": {
            "formula_zh": "0.9% ≤ 1.5×ATR% ⇒ ATR%≥0.60%",
            "dsl_proxy_zh": "atr14>=atr14@offset8" if best_params[7] else "破高+z扩张代理（本变体未强制offset）",
            "enabled": best_params[7],
        },
        "best_probe": best,
        "r3_lesson_zh": "R3紧过滤tr=1；修复过松tr=1010/fp0；R4取中带",
    }
    w("hypothesis", hyp)
    w("picked_variant", {"tag": best_params[0], "params": book["params"], "probe": best})
    w("hypothesis_book", book)

    # If best already quick-pass, go full; else repair ≤3 from best
    hist = [{"stage": "probe", "best": best}]
    packs = None
    for qtry in range(4):
        print("[quick] try", qtry, now(), flush=True)
        w("status", {"stage": "quick", "attempt": qtry, "at": now(), "round": "r4",
                     "key": book["dsl"]["key"]})
        packs = f2.quick_suite(book)
        bm = packs.get("base_metrics") or {}
        row = {
            "pass": packs.get("quick_pass"), "failed_step": packs.get("failed_step"),
            "metrics": bm, "dest": (packs.get("logic_destruction") or {}).get("pass"),
            "key": book["dsl"]["key"],
        }
        w("quick_try%d" % qtry, row)
        hist.append({"stage": "quick", "try": qtry, "pass": row["pass"],
                     "failed_step": row.get("failed_step"), "tr": bm.get("trades"),
                     "fp": bm.get("fold_positive"), "folds": bm.get("folds"),
                     "dest": row.get("dest")})
        print("[quick]", row, flush=True)
        if packs.get("quick_pass"):
            break
        if qtry >= 3:
            break
        book = m.stamp_book_keys(f3.local_tweak(book, qtry + 1, packs.get("failed_step")), "_t")
        repaired, _ = f3.glm_repair(book, packs, packs.get("failed_step"), "quick")
        if repaired:
            repaired["logic_class"] = "trend_breakout"
            book = m.stamp_book_keys(repaired, "_r")

    end = {
        "at": now(), "round": "r4", "ok": False, "hist": hist,
        "book_key": (book.get("dsl") or {}).get("key"),
        "hypothesis": hyp, "best_probe": best,
    }
    if not packs or not packs.get("quick_pass"):
        death = {
            "exact_death": (packs or {}).get("failed_step") or "quick_exhausted",
            "metrics": (packs or {}).get("base_metrics"),
            "probe_ranked": ranked[:5],
            "summary_zh": "R4 Quick失败（中带探测后仍未过WF/dest） best=%s" % best.get("tag"),
        }
        return fail_out(end, hyp, death, "frost3_sol15m_breakout_r4_quick")

    for ftry in range(4):
        print("[full] try", ftry, now(), flush=True)
        w("status", {"stage": "full", "attempt": ftry, "at": now()})
        packs = f2.full_suite(book, packs)
        w("full_try%d" % ftry, {"pass": packs.get("full_pass"),
                                "failed_step": packs.get("failed_step"),
                                "full": packs.get("full")})
        hist.append({"stage": "full", "try": ftry, "pass": packs.get("full_pass"),
                     "failed_step": packs.get("failed_step"),
                     "full": packs.get("full")})
        print("[full]", packs.get("full_pass"), packs.get("failed_step"), packs.get("full"), flush=True)
        if packs.get("full_pass"):
            break
        if ftry >= 3:
            death = {"exact_death": packs.get("failed_step") or "full_exhausted",
                     "full": packs.get("full"),
                     "summary_zh": "R4 Full失败于 %s" % packs.get("failed_step")}
            return fail_out(end, hyp, death, "frost3_sol15m_breakout_r4_full")
        book = m.stamp_book_keys(f3.local_tweak(book, ftry + 1, packs.get("failed_step")), "_ft")
        q2 = f2.quick_suite(book)
        if not q2.get("quick_pass"):
            packs = q2
            continue
        packs = q2

    for stry in range(4):
        print("[sim] try", stry, now(), flush=True)
        w("status", {"stage": "sim_formal", "attempt": stry, "at": now()})
        sf = f2.run_sim_formal(book, packs)
        w("sim_try%d" % stry, sf)
        hist.append({"stage": "sim", "try": stry, "failed_step": sf.get("failed_step"),
                     "sim_ds": (sf.get("sim") or {}).get("wr_deepseek_sim"),
                     "sim_qw": (sf.get("sim") or {}).get("wr_qwen_sim"),
                     "pending": (sf.get("pending") or {}).get("key")})
        if (sf.get("pending") or {}).get("ok"):
            end.update({"ok": True, "pending": sf.get("pending"),
                        "sim": sf.get("sim"), "formal": sf.get("formal"), "hist": hist})
            w("end_report", end)
            summary = (
                "SOL15m趋势突破【成功pending】key=%s sim_ds/qw=%s/%s "
                "EMA=%s 收缩=%s 量能=%s ATR=%s probe=%s"
                % (
                    (sf.get("pending") or {}).get("key"),
                    (sf.get("sim") or {}).get("wr_deepseek_sim"),
                    (sf.get("sim") or {}).get("wr_qwen_sim"),
                    hyp.get("ema_period"), hyp.get("vol_contraction_definition"),
                    hyp.get("volume_threshold_and_rationale"),
                    ((hyp.get("atr_stop_matching") or {}).get("formula_zh")),
                    best.get("tag"),
                )
            )
            w("parent_summary_zh", {"summary_zh": summary, "at": now(), "end": {
                "ok": True, "pending": sf.get("pending"),
                "key": (sf.get("pending") or {}).get("key"),
            }})
            print("PARENT_SUMMARY_ZH:", summary, flush=True)
            return 0
        if sf.get("failed_step") in ("formal_review", "pending_ingest") or stry >= 3:
            death = {"exact_death": sf.get("failed_step") or "sim_exhausted",
                     "sim": sf.get("sim"), "formal": sf.get("formal"),
                     "summary_zh": "R4 sim/formal失败于 %s" % sf.get("failed_step")}
            end["sim"] = sf.get("sim")
            return fail_out(end, hyp, death, "frost3_sol15m_breakout_r4_sim")
        book = m.stamp_book_keys(f3.local_tweak(book, stry + 1, "sim"), "_st")
        q3 = f2.quick_suite(book)
        if q3.get("quick_pass"):
            packs = f2.full_suite(book, q3)

    death = {"exact_death": "unknown_exhausted", "summary_zh": "R4 unknown exhausted"}
    return fail_out(end, hyp, death, "frost3_sol15m_breakout_r4_unknown")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        print("EXCEPTION", traceback.format_exc()[-3000:], flush=True)
        sys.exit(2)
