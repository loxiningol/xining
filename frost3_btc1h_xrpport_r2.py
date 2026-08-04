#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BTC1h xrpport Round2: XRP exhaustion_fade + h1_slope4<0.001 (Quick-pass winner)."""
from __future__ import print_function

import copy
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, "/root")
import frost2_action_run as f2
import frost3_action as f3
import auto_trade_dual_engine_factory as d

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
f2.AVOID = [
    ("ADA-USDT-SWAP", None),
    ("LTC-USDT-SWAP", None),
    ("NG-USDT-SWAP", None),
    ("XRP-USDT-SWAP", None),
]
OUT = "/root/auto_trade/dual_engine"
PREFIX = "frost3_btc1h_xrpport"
SYMBOL = "BTC-USDT-SWAP"
TF = "1h"
MAX_FULL = 3
MAX_SIM = 3


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write(suf, obj):
    path = os.path.join(OUT, "%s_%s.json" % (PREFIX, suf))
    open(path, "w").write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


def make_book():
    dsl = {
        "key": "frost3_btc1h_xrpport_exhaustion_fade_slope",
        "name": "寒霜叁-BTC-1h-exhaustion_fade_xrpport",
        "direction": "short",
        "timeframe": TF,
        "supported_instruments": [SYMBOL],
        "max_hold_bars": 20,
        "description": "XRP15m exhaustion_fade port to BTC1h; h1_slope4<0.001 regime filter",
        "entry": {"all": [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 50.0}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
            {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 40.0}},
            {"left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": 0.001}},
        ]},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
        "schema": "qiyu_strategy_dsl_v1",
    }
    dsl = f2.ensure_dsl(dsl, SYMBOL, TF)
    return {
        "title": "BTC1h-xrpport-r2-exhaustion_fade",
        "symbol": SYMBOL,
        "timeframe": TF,
        "direction": "short",
        "logic_class": "exhaustion_fade",
        "thesis": (
            "Port frost_xrp_rescue_h20_t45 to BTC1h; add mild h1_slope4<0.001 "
            "to avoid strong up-slope stop clusters / 1h noise"
        ),
        "entry_sketch": "rsi14>50; z20>0; macd_stick<0; close<ema16; cci>40; h1_slope4<0.001",
        "exit_sketch": "rsi14<45 take_profit; close>prev_high20 invalidation; max_hold=20",
        "diff_vs_xrp15m": "rsi 55->50; +cci>40; +h1_slope4<0.001; hold 20 bars (20h vs XRP 5h)",
        "why_avoids_cl_traps": (
            "cci soft 40 not 90+; not marginal rsi54-only; structural prev_high20 inv; "
            "slope filter for 1h stop noise"
        ),
        "dsl": dsl,
        "gate_mode": "frost2",
        "source": "frost3_btc1h_xrpport_r2",
        "note_zh": "Round1 long reclaim same-bar freq=0; Round2 literal XRP short port + 1h slope",
    }


def local_tweak(book, n, step):
    book = copy.deepcopy(book)
    entries = list((book["dsl"].get("entry") or {}).get("all") or [])
    step = str(step or "")
    for row in entries:
        feat = (row.get("left") or {}).get("feature")
        right = row.get("right") or {}
        if "value" not in right:
            continue
        v = float(right["value"])
        op = row.get("op")
        if feat == "rsi14" and op == "gt":
            right["value"] = (
                max(48.0, v - 1.0 * n) if ("walk" in step or "friction" in step)
                else min(58.0, v + 0.5 * n)
            )
        elif feat == "z20" and op == "gt":
            right["value"] = max(-0.2, v - 0.05 * n) if "walk" in step else v + 0.05 * n
        elif feat == "cci" and op == "gt":
            right["value"] = max(20.0, v - 10 * n) if "walk" in step else min(100.0, v + 10 * n)
        elif feat == "h1_slope4" and op == "lt":
            right["value"] = (
                min(0.003, v + 0.0005 * n) if "walk" in step else max(0.0, v - 0.0003 * n)
            )
    book["dsl"]["entry"] = {"all": entries}
    hold = int(book["dsl"].get("max_hold_bars") or 20)
    if "friction" in step or "mc" in step:
        book["dsl"]["max_hold_bars"] = max(10, hold - 2 * n)
    book["dsl"]["key"] = (book["dsl"]["key"][:80] + "_t%d" % n)[:100]
    book["dsl"] = f2.ensure_dsl(book["dsl"], SYMBOL, TF)
    return book


def archive(result, death):
    arch = os.path.join(
        OUT, "archive", "%s_r2_%s" % (PREFIX, result.get("failed_step") or "fail")
    )
    os.makedirs(arch, exist_ok=True)
    open(os.path.join(arch, "result.json"), "w").write(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    open(os.path.join(arch, "DEATH_CAUSE.json"), "w").write(
        json.dumps(death, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    write("archive_pointer_r2", {"archive_path": arch, "death_cause": death, "at": now()})
    return arch


def main():
    print("=== BTC1h_xrpport R2 START ===", now(), flush=True)
    d._ensure_dirs()
    d._load_env()
    book = make_book()
    write("r2_hypothesis_book", book)
    hist = []

    packs = f2.quick_suite(book)
    write("r2_quick", packs)
    hist.append({
        "stage": "quick",
        "pass": packs.get("quick_pass"),
        "fp": (packs.get("base_metrics") or {}).get("fold_positive"),
        "tr": (packs.get("base_metrics") or {}).get("trades"),
        "dest": (packs.get("logic_destruction") or {}).get("pass"),
    })
    print("quick", packs.get("quick_pass"), packs.get("base_metrics"), flush=True)

    if not packs.get("quick_pass"):
        book2 = make_book()
        book2["dsl"]["max_hold_bars"] = 12
        book2["dsl"]["exit"] = {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 40.0}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]}
        book2["dsl"]["entry"] = {"all": [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 50.0}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
            {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 40.0}},
        ]}
        book2["dsl"]["key"] = "frost3_btc1h_xrpport_exhaustion_fade_h12tp40"
        book2["dsl"] = f2.ensure_dsl(book2["dsl"], SYMBOL, TF)
        packs = f2.quick_suite(book2)
        write("r2_quick_alt", packs)
        hist.append({"stage": "quick_alt", "pass": packs.get("quick_pass"),
                     "metrics": packs.get("base_metrics")})
        print("quick_alt", packs.get("quick_pass"), packs.get("base_metrics"), flush=True)
        if packs.get("quick_pass"):
            book = book2
        else:
            death = {"exact_death": "quick_walk_forward", "reason": "r2 quick failed", "hist": hist}
            arch = archive(
                {"ok": False, "failed_step": "quick_walk_forward", "hist": hist, "book": book},
                death,
            )
            summary = "BTC1h_xrpport【失败归档R2】死因=quick 归档=%s" % arch
            write("parent_summary_zh", {"summary_zh": summary, "at": now()})
            print("PARENT_SUMMARY_ZH:", summary, flush=True)
            return 1

    write("r2_book_after_quick", book)

    for ftry in range(MAX_FULL + 1):
        packs = f2.full_suite(book, packs)
        write("r2_full_try%d" % ftry, {
            "pass": packs.get("full_pass"),
            "failed_step": packs.get("failed_step"),
            "full": packs.get("full"),
        })
        hist.append({
            "stage": "full", "try": ftry, "pass": packs.get("full_pass"),
            "fail": packs.get("failed_step"),
            "friction": (packs.get("full") or {}).get("friction_sharpe"),
            "mc": ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
        })
        print("full", ftry, packs.get("full_pass"), packs.get("failed_step"),
              packs.get("full"), flush=True)
        if packs.get("full_pass"):
            break
        if ftry >= MAX_FULL:
            death = {
                "exact_death": packs.get("failed_step") or "full_exhausted",
                "reason": "Full failed after %d" % MAX_FULL,
                "full": packs.get("full"), "hist": hist[-6:], "key": book["dsl"]["key"],
            }
            arch = archive({
                "ok": False, "failed_step": death["exact_death"],
                "hist": hist, "book": book, "full": packs.get("full"),
            }, death)
            summary = (
                "BTC1h_xrpport【失败归档R2】逻辑=exhaustion_fade 死因=%s 归档=%s"
                % (death["exact_death"], arch)
            )
            write("end_report_r2", {
                "ok": False, "failed_step": death["exact_death"],
                "archive_path": arch, "hist": hist, "book": book,
            })
            write("parent_summary_zh", {"summary_zh": summary, "at": now()})
            print("PARENT_SUMMARY_ZH:", summary, flush=True)
            return 1
        book = local_tweak(book, ftry + 1, packs.get("failed_step") or "full")
        repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step"), "full")
        if repaired:
            repaired["symbol"] = SYMBOL
            repaired["timeframe"] = TF
            repaired["dsl"]["supported_instruments"] = [SYMBOL]
            repaired["dsl"]["timeframe"] = TF
            k = str(repaired["dsl"].get("key") or "")
            if PREFIX not in k:
                repaired["dsl"]["key"] = ("%s_%s" % (PREFIX, k))[:100]
            book = repaired
            book["dsl"] = f2.ensure_dsl(book["dsl"], SYMBOL, TF)
        q2 = f2.quick_suite(book)
        hist.append({"stage": "re_quick", "pass": q2.get("quick_pass"),
                     "fail": q2.get("failed_step")})
        if not q2.get("quick_pass"):
            packs = q2
            packs["full_pass"] = False
            packs["failed_step"] = q2.get("failed_step") or "quick_regressed"
            continue
        packs = q2

    for stry in range(MAX_SIM + 1):
        sf = f2.run_sim_formal(book, packs)
        write("r2_sim_try%d" % stry, sf)
        hist.append({
            "stage": "sim", "try": stry, "fail": sf.get("failed_step"),
            "ds": (sf.get("sim") or {}).get("wr_deepseek_sim"),
            "qw": (sf.get("sim") or {}).get("wr_qwen_sim"),
            "pending": (sf.get("pending") or {}).get("key"),
        })
        print(
            "sim", stry, sf.get("failed_step"),
            (sf.get("sim") or {}).get("wr_deepseek_sim"),
            (sf.get("sim") or {}).get("wr_qwen_sim"),
            flush=True,
        )
        if (sf.get("pending") or {}).get("ok"):
            end = {
                "ok": True, "pending": sf.get("pending"), "sim": sf.get("sim"),
                "formal": sf.get("formal"), "key": book["dsl"]["key"],
                "logic": "exhaustion_fade", "hist": hist, "at": now(),
            }
            write("end_report_r2", end)
            summary = (
                "BTC1h_xrpport【成功R2】逻辑=exhaustion_fade key=%s pending=%s sim=%s/%s"
                % (
                    end["key"], (end["pending"] or {}).get("key"),
                    (end["sim"] or {}).get("wr_deepseek_sim"),
                    (end["sim"] or {}).get("wr_qwen_sim"),
                )
            )
            write("parent_summary_zh", {"summary_zh": summary, "at": now(), "end": end})
            print("PARENT_SUMMARY_ZH:", summary, flush=True)
            return 0
        fs = sf.get("failed_step")
        if fs in ("formal_review", "pending_ingest"):
            death = {
                "exact_death": fs,
                "reason": (sf.get("formal") or {}).get("reason")
                or (sf.get("pending") or {}).get("reason"),
                "sim": sf.get("sim"), "formal": sf.get("formal"), "hist": hist[-6:],
            }
            arch = archive({
                "ok": False, "failed_step": fs, "hist": hist,
                "book": book, "sim_formal": sf,
            }, death)
            summary = "BTC1h_xrpport【失败归档R2】死因=%s 归档=%s" % (fs, arch)
            write("end_report_r2", {
                "ok": False, "failed_step": fs, "archive_path": arch, "hist": hist,
            })
            write("parent_summary_zh", {"summary_zh": summary, "at": now()})
            print("PARENT_SUMMARY_ZH:", summary, flush=True)
            return 1
        if stry >= MAX_SIM:
            death = {
                "exact_death": fs or "sim_exhausted",
                "reason": "Sim failed after %d" % MAX_SIM,
                "sim": sf.get("sim"), "hist": hist[-8:],
            }
            arch = archive({
                "ok": False, "failed_step": death["exact_death"],
                "hist": hist, "book": book,
            }, death)
            summary = "BTC1h_xrpport【失败归档R2】死因=%s 归档=%s" % (
                death["exact_death"], arch
            )
            write("end_report_r2", {
                "ok": False, "failed_step": death["exact_death"],
                "archive_path": arch, "hist": hist,
            })
            write("parent_summary_zh", {"summary_zh": summary, "at": now()})
            print("PARENT_SUMMARY_ZH:", summary, flush=True)
            return 1
        book = local_tweak(book, stry + 1, "sim")
        repaired, _ai = f3.glm_repair(book, packs, "sim_review", "sim")
        if repaired:
            repaired["symbol"] = SYMBOL
            repaired["timeframe"] = TF
            repaired["dsl"]["supported_instruments"] = [SYMBOL]
            book = repaired
            book["dsl"] = f2.ensure_dsl(book["dsl"], SYMBOL, TF)
        q3 = f2.quick_suite(book)
        if not q3.get("quick_pass"):
            continue
        packs = f2.full_suite(book, q3)
        if not packs.get("full_pass"):
            continue
    return 1


if __name__ == "__main__":
    sys.exit(main())
