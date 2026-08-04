#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Finalize ETH15m fade: one repair-budget process attempt then archive if fail.

Evidence: fast_search 0/216 reached fp>=6 (need WF>=7/10).
"""
from __future__ import print_function
import json
import os
import pickle
import sys
import traceback
from datetime import datetime

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d
import auto_trade_human_confirm_pipeline as pipeline
import frost3_eth15m_fade as m

OUT = "/root/auto_trade/dual_engine"
PREFIX = "frost3_eth15m_fade"
FRAME_PKL = os.path.join(OUT, "frost3_eth15m_fade_frame.pkl")


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    try:
        d._load_env()
    except Exception:
        pass
    m.install_frame_cache()
    f2.QUICK_WF_POS = 7
    d.FROST_RELAXED_WF_POS = 7

    # Best-known structural book (str_r60 from probe3): still only fp≈5/10
    plan = m.fallback_plan({"stop_vs_extreme_vol": {"atr_pct_median": 0.0028,
                                                    "stop_in_atr_median": 3.2}})
    plan["param_plan"] = {
        "rsi_entry_gt": 60.0,
        "z20_entry_gt": 0.6,
        "require_close_lt_open": True,
        "require_macd_neg": True,
        "require_close_lt_ema16": False,
        "require_close_gt_ema16": True,
        "h1_slope4_lt": None,
        "rsi_tp_lt": 45.0,
        "max_hold_bars": 20,
        "stop_loss_pct": 0.009,
    }
    plan["hypothesis"]["entry_sketch"] = (
        "rsi14>60; z20>0.6; close<open; macd_stick<0; close>ema16"
    )
    plan["hypothesis"]["exit_sketch"] = (
        "rsi14<45 take_profit; close>prev_high20 invalidation; max_hold=20"
    )
    plan["decision"] = "pass"
    plan["reason_zh"] = (
        "在216组快速筛查均无法达到fp>=6的前提下，采用probe3最佳结构str_r60进入"
        "正式修复预算（Quick≤3）；预期仍难达WF≥7。"
    )
    plan["screen_evidence"] = {
        "fast_search_hits_fp_ge_6": 0,
        "fast_search_total": 216,
        "probe3_best": {
            "tag": "str_r60", "fp": 5, "folds": 10, "tr": 59,
            "wr": 45.76, "sh": -1.05, "dest": False,
        },
        "xrp_exact_eth": {
            "tr": 11, "fp": 3, "wr": 36.4, "sh": -1.65, "dest": False,
        },
    }
    m._write("%s_参数方案.json" % PREFIX, plan)
    m._write("%s_direction_report.json" % PREFIX, plan)

    book = m.make_book(plan, tag="str60_final")
    # Force stretch entry explicitly
    book["dsl"] = f2.ensure_dsl({
        "key": "frost3_eth15m_fade_str60_final",
        "name": "寒霜叁-ETH-15m-exhaustion_fade",
        "direction": "short",
        "timeframe": "15m",
        "supported_instruments": ["ETH-USDT-SWAP"],
        "max_hold_bars": 20,
        "description": "ETH15m fade stretch-overbought reject",
        "entry": {"all": [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 60.0}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.6}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema16"}},
        ]},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"},
             "role": "invalidation"},
        ]},
    }, "ETH-USDT-SWAP", "15m")
    m._write("%s_book_init.json" % PREFIX, book)

    print("[finalize] process start", book["dsl"]["key"], flush=True)
    try:
        result = m.process(book, plan)
    except Exception as exc:
        result = {
            "ok": False, "failed_step": "exception", "reason": str(exc),
            "trace": traceback.format_exc()[-2000:], "book": book,
            "death_cause_zh": "异常：" + str(exc),
        }

    # Augment death with screen evidence
    if not result.get("ok"):
        result["death_cause_zh"] = (
            (result.get("death_cause_zh") or "")
            + "｜筛查证据：fast_search 0/216 达到 fp>=6；probe3最优 str_r60 仅 fp5/10 "
            "且 sharpe<0/dest失败；XRP同构移植 ETH 仅 fp3/10。根因：ETH15m 超买衰竭空 "
            "在拒绝确认结构下无法同时满足样本折数与逻辑破坏稳健（类CL15m陷阱）。"
        )
        arch = m.archive_fail(result, result.get("book") or book)
        # richer DEATH
        death_path = os.path.join(arch, "DEATH.json")
        try:
            death = json.load(open(death_path))
        except Exception:
            death = {}
        death.update({
            "screen_evidence": plan["screen_evidence"],
            "xrp_ref": "frost_xrp_rescue_h20_t45",
            "cl_ref": "frost2_cl_15m_entry_exhausted_r3",
            "stop_loss_pct": 0.009,
            "recommendation_zh": (
                "停止ETH-USDT-SWAP 15m exhaustion_fade 同构救援；"
                "勿再堆阈值。若需ETH空头边缘，应换逻辑类或周期。"
            ),
        })
        open(death_path, "w").write(json.dumps(death, ensure_ascii=False, indent=2) + "\n")
    else:
        arch = None

    end = {
        "at": _now(),
        "op": "寒霜叁ETH15m衰竭反向",
        "symbol": "ETH-USDT-SWAP",
        "timeframe": "15m",
        "logic_class": "exhaustion_fade",
        "direction": "short",
        "stop_loss_pct": 0.009,
        "ok": bool(result.get("ok")),
        "failed_step": result.get("failed_step"),
        "reason": result.get("reason"),
        "death_cause_zh": result.get("death_cause_zh"),
        "pending_key": ((result.get("pending") or {}).get("key")),
        "sim": result.get("sim"),
        "formal": result.get("formal"),
        "metrics": result.get("metrics"),
        "hist": result.get("hist"),
        "book_key": ((result.get("book") or book).get("dsl") or {}).get("key"),
        "archive_path": arch,
        "xrp_ref": "frost_xrp_rescue_h20_t45",
        "cl_ref": "frost2_cl_15m_entry_exhausted_r3",
        "screen_evidence": plan["screen_evidence"],
    }
    m._write("%s_end_report.json" % PREFIX, end)

    if end["ok"]:
        summary = (
            "成功：ETH15m exhaustion_fade 已进pending key=%s" % end.get("pending_key")
        )
    else:
        summary = (
            "失败归档：ETH-USDT-SWAP 15m exhaustion_fade（镜像XRP frost_xrp_rescue_h20_t45）"
            "未过闸。失败步骤=%s。死因=%s。筛查0/216达fp≥6；最优仅fp5/10。"
            "硬止损0.9%%已锁定。未改动现网ADA/LTC/NG/XRP。归档=%s"
            % (end.get("failed_step"), end.get("death_cause_zh"), end.get("archive_path"))
        )
    m._write("%s_parent_summary.json" % PREFIX, {"summary_zh": summary, "end": end})
    m._upd(stage="done", ok=end["ok"], failed_step=end.get("failed_step"),
           pending_key=end.get("pending_key"))
    print("=== ETH15m_FADE PARENT ===", summary, flush=True)
    print("=== ETH15m_FADE END ===", json.dumps({
        "ok": end["ok"], "failed_step": end.get("failed_step"),
        "archive": end.get("archive_path"), "pending_key": end.get("pending_key"),
    }, ensure_ascii=False), flush=True)
    return 0 if end["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
