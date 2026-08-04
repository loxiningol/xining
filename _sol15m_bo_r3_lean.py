#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SOL15m trend_breakout R3 lean: skip GLM, fallback DSL, run Quick→Full→Sim→pending."""
from __future__ import print_function

import json
import os
import sys
import traceback
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


def fail_out(end, hyp, death, arch_name):
    arch = os.path.join(OUT, "archive", arch_name)
    os.makedirs(arch, exist_ok=True)
    open(os.path.join(arch, "DEATH_CAUSE.json"), "w").write(
        json.dumps(death, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    open(os.path.join(arch, "result.json"), "w").write(
        json.dumps(end, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    end["archive_path"] = arch
    end["death_cause"] = death
    end["failed_step"] = death.get("exact_death")
    w("end_report", end)
    w("archive_pointer", {"archive_path": arch, "death_cause": death, "at": now()})
    summary = (
        "SOL15m趋势突破【失败归档R3】死因=%s 详情=%s 归档=%s "
        "EMA=%s 收缩=%s 量能=%s ATR匹配=%s"
        % (
            death.get("exact_death"),
            death.get("summary_zh"),
            arch,
            hyp.get("ema_period"),
            hyp.get("vol_contraction_definition"),
            hyp.get("volume_threshold_and_rationale"),
            ((hyp.get("atr_stop_matching") or {}).get("formula_zh")),
        )
    )
    w("parent_summary_zh", {"summary_zh": summary, "at": now(), "end": {
        "ok": False, "failed_step": end.get("failed_step"),
        "archive_path": arch, "key": end.get("book_key"),
    }})
    print("PARENT_SUMMARY_ZH:", summary, flush=True)
    return 1


def main():
    print("=== SOL15m breakout R3 lean START ===", now(), flush=True)
    d._ensure_dirs()
    d._load_env()
    packet = {
        "sol15m_vol": m.load_sol15m_vol_snapshot(),
        "breakout_freezer": m.load_breakout_freezer(),
        "cl_archive": step1.load_cl_archive(),
        "note": "R3 lean skip GLM; R1 validate tooling bug fixed; R2 killed mid-quick under I/O",
    }
    report = m.fallback_param_plan(packet)
    w("param_plan", report)
    w("SOL15m趋势突破参数方案", report)
    pick = report["variants"][0]
    w("picked_variant", pick)
    hyp = {
        "at": now(),
        "ema_period": pick.get("ema_period"),
        "vol_contraction_definition": pick.get("vol_contraction"),
        "volume_threshold_and_rationale": pick.get("volume_proxy"),
        "atr_stop_matching": report.get("atr_stop_matching"),
        "full_hypothesis": report.get("hypothesis"),
        "round": "r3_lean_fallback",
    }
    w("hypothesis", hyp)
    book = m.make_book(pick, 1)
    w("hypothesis_book", book)
    print("dsl", book["dsl"]["key"], "leaves", len(book["dsl"]["entry"]["all"]), flush=True)

    hist = []
    packs = None
    for qtry in range(4):
        print("[quick] try", qtry, now(), flush=True)
        w("status", {"stage": "quick", "attempt": qtry, "at": now(), "round": "r3",
                     "key": book["dsl"]["key"]})
        packs = f2.quick_suite(book)
        bm = packs.get("base_metrics") or {}
        row = {
            "pass": packs.get("quick_pass"),
            "failed_step": packs.get("failed_step"),
            "ok": packs.get("ok"),
            "error": packs.get("error"),
            "metrics": bm,
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
            "key": book["dsl"]["key"],
        }
        w("quick_try%d" % qtry, row)
        hist.append({
            "stage": "quick", "try": qtry, "pass": row["pass"],
            "failed_step": row.get("failed_step"), "dest": row.get("dest"),
            "fp": bm.get("fold_positive"), "folds": bm.get("folds"),
            "tr": bm.get("trades"), "wr": bm.get("win_rate_pct"),
        })
        print("[quick]", row["pass"], row.get("failed_step"),
              "tr", bm.get("trades"), "fp", bm.get("fold_positive"),
              "dest", row.get("dest"), flush=True)
        if packs.get("quick_pass"):
            break
        if qtry >= 3:
            break
        book = m.stamp_book_keys(f3.local_tweak(book, qtry + 1, packs.get("failed_step")), "_t")
        repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step"), "quick")
        if repaired:
            repaired["logic_class"] = m.LOGIC_FAMILY
            book = m.stamp_book_keys(repaired, "_r")

    end = {
        "at": now(), "round": "r3_lean", "ok": False, "hist": hist,
        "book_key": (book.get("dsl") or {}).get("key"),
        "logic_family": m.LOGIC_FAMILY,
        "symbol": m.SYMBOL, "timeframe": m.TIMEFRAME,
        "hypothesis": hyp,
    }
    if not packs or not packs.get("quick_pass"):
        death = {
            "exact_death": (packs or {}).get("failed_step") or "quick_exhausted",
            "reason": "R3 lean Quick failed after repairs",
            "metrics": (packs or {}).get("base_metrics"),
            "error": (packs or {}).get("error"),
            "hist": hist,
            "summary_zh": "SOL15m trend_breakout R3 Quick失败于 %s" % (
                (packs or {}).get("failed_step") or "quick_exhausted"),
        }
        return fail_out(end, hyp, death, "frost3_sol15m_breakout_r3_quick")

    for ftry in range(4):
        print("[full] try", ftry, now(), flush=True)
        w("status", {"stage": "full", "attempt": ftry, "at": now()})
        packs = f2.full_suite(book, packs)
        w("full_try%d" % ftry, {
            "pass": packs.get("full_pass"),
            "failed_step": packs.get("failed_step"),
            "full": packs.get("full"),
        })
        hist.append({
            "stage": "full", "try": ftry, "pass": packs.get("full_pass"),
            "failed_step": packs.get("failed_step"),
            "fric": (packs.get("full") or {}).get("friction_sharpe"),
            "mc": ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
        })
        print("[full]", packs.get("full_pass"), packs.get("failed_step"),
              packs.get("full"), flush=True)
        if packs.get("full_pass"):
            break
        if ftry >= 3:
            death = {
                "exact_death": packs.get("failed_step") or "full_exhausted",
                "full": packs.get("full"), "hist": hist,
                "summary_zh": "SOL15m trend_breakout R3 Full失败于 %s" % packs.get("failed_step"),
            }
            return fail_out(end, hyp, death, "frost3_sol15m_breakout_r3_full")
        book = m.stamp_book_keys(f3.local_tweak(book, ftry + 1, packs.get("failed_step")), "_ft")
        q2 = f2.quick_suite(book)
        hist.append({"stage": "re_quick", "pass": q2.get("quick_pass"),
                     "failed_step": q2.get("failed_step")})
        if not q2.get("quick_pass"):
            packs = q2
            continue
        packs = q2

    for stry in range(4):
        print("[sim] try", stry, now(), flush=True)
        w("status", {"stage": "sim_formal", "attempt": stry, "at": now()})
        sf = f2.run_sim_formal(book, packs)
        w("sim_try%d" % stry, sf)
        hist.append({
            "stage": "sim", "try": stry, "failed_step": sf.get("failed_step"),
            "sim_ds": (sf.get("sim") or {}).get("wr_deepseek_sim"),
            "sim_qw": (sf.get("sim") or {}).get("wr_qwen_sim"),
            "pending": (sf.get("pending") or {}).get("key"),
        })
        if (sf.get("pending") or {}).get("ok"):
            end.update({
                "ok": True, "pending": sf.get("pending"),
                "sim": sf.get("sim"), "formal": sf.get("formal"), "hist": hist,
            })
            w("end_report", end)
            summary = (
                "SOL15m趋势突破【成功pending】key=%s sim_ds/qw=%s/%s "
                "EMA=%s 收缩=%s 量能=%s ATR匹配=%s"
                % (
                    (sf.get("pending") or {}).get("key"),
                    (sf.get("sim") or {}).get("wr_deepseek_sim"),
                    (sf.get("sim") or {}).get("wr_qwen_sim"),
                    hyp.get("ema_period"),
                    hyp.get("vol_contraction_definition"),
                    hyp.get("volume_threshold_and_rationale"),
                    ((hyp.get("atr_stop_matching") or {}).get("formula_zh")),
                )
            )
            w("parent_summary_zh", {"summary_zh": summary, "at": now(), "end": {
                "ok": True, "pending": sf.get("pending"),
                "key": (sf.get("pending") or {}).get("key"),
            }})
            print("PARENT_SUMMARY_ZH:", summary, flush=True)
            return 0
        if sf.get("failed_step") in ("formal_review", "pending_ingest") or stry >= 3:
            death = {
                "exact_death": sf.get("failed_step") or "sim_exhausted",
                "sim": sf.get("sim"), "formal": sf.get("formal"),
                "hist": hist,
                "summary_zh": "SOL15m trend_breakout R3 sim/formal失败于 %s" % sf.get("failed_step"),
            }
            end["sim"] = sf.get("sim")
            return fail_out(end, hyp, death, "frost3_sol15m_breakout_r3_sim")
        book = m.stamp_book_keys(f3.local_tweak(book, stry + 1, "sim"), "_st")
        q3 = f2.quick_suite(book)
        if q3.get("quick_pass"):
            packs = f2.full_suite(book, q3)

    death = {"exact_death": "unknown_exhausted", "hist": hist,
             "summary_zh": "SOL15m trend_breakout R3 unknown exhausted"}
    return fail_out(end, hyp, death, "frost3_sol15m_breakout_r3_unknown")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("EXCEPTION", traceback.format_exc()[-3000:], flush=True)
        sys.exit(2)
