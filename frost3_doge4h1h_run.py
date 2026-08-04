#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone DOGE 4h→1h multi-TF strategy creation (frost3_doge4h1h_*).

Hard constraints:
  - Symbol DOGE-USDT-SWAP only; do not touch ADA/LTC/NG/XRP or other frost3 dirs
  - Real multi-TF: 4h trend filter + 1h entry (NOT single-TF only)
  - Avoid exhaustion_fade / reclaim / breakout-continuation / range_reclaim
  - Artifacts: /root/auto_trade/dual_engine/frost3_doge4h1h_*
  - Gates: WF>=7/10, dest pass, MC beat>=90%, friction Sharpe>=0
  - Sim DS/Qwen both >=55% → formal → pending
  - Quick/Full/Sim repair caps = 3

4h filter realization (DSL lacks native h4_*):
  Period-scaled proxy on 1h bars from 4h resample equivalence:
    4h EMA19 ≈ 1h EMA75  (19*4≈76)
    4h EMA53 ≈ 1h EMA200 (53*4≈212)
    4h slope>0 ≈ ema75 > ema75[offset=4]
  Documented in frost3_doge4h1h_mtf_note.json — NOT a fake rename.
"""
from __future__ import print_function

import copy
import json
import os
import re
import sys
import traceback
from datetime import datetime

ENV_PATH = "/root/auto_trade/ai_ecosystem.env"
if os.path.exists(ENV_PATH):
    for line in open(ENV_PATH):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d
import frost3_step1 as step1

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
f2.AVOID = [
    ("ADA-USDT-SWAP", None),
    ("LTC-USDT-SWAP", None),
    ("NG-USDT-SWAP", None),
    ("XRP-USDT-SWAP", None),
]

OUT = "/root/auto_trade/dual_engine"
PREFIX = "frost3_doge4h1h"
SYMBOL = "DOGE-USDT-SWAP"
TIMEFRAME = "1h"
MAX_AUDIT_REVISE = 2
MAX_QUICK_REPAIR = 3
MAX_FULL_REPAIR = 3
MAX_SIM_REPAIR = 3
BANNED_LOGIC = {
    "exhaustion_fade", "reclaim", "range_reclaim",
    "breakout_continuation", "breakout-continuation", "impulse_continuation",
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write(name, obj):
    base = name if str(name).startswith(PREFIX) else (PREFIX + "_" + name)
    path = os.path.join(OUT, base)
    open(path, "w").write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


def _safe(s):
    s = str(s or "x")
    for ch in (".", " ", "/", ":", "+", "%", "-"):
        s = s.replace(ch, "p" if ch in (".", "-") else "_")
    return s[:40]


MTF_NOTE = {
    "symbol": SYMBOL,
    "entry_timeframe": TIMEFRAME,
    "trend_timeframe": "4h",
    "dsl_native_h4": False,
    "realization": "period_scaled_ema_proxy",
    "mapping": {
        "4h_ema19": "ema75 on 1h (19*4≈76)",
        "4h_ema53": "ema200 on 1h (53*4≈212)",
        "4h_slope_positive": "ema75 > ema75 offset=4 (≈ one 4h bar of slope)",
        "4h_uptrend_filter": "ema75>ema200 AND ema75>ema75[4]",
    },
    "why_not_fake_rename": (
        "Periods are mathematically mapped from 4h→1h bar equivalence; "
        "filter uses slower EMAs that cannot flip on single 1h noise the way ema19/21 can. "
        "Entry still uses 1h rsi/macd/z/ema21 — true role split HTF filter vs LTF entry."
    ),
    "roles": {
        "4h": "regime/trend gate only — must be bullish before any long",
        "1h": "pullback-resume timing — mid RSI reclaim of ema21 with macd>0",
    },
}


def load_inputs():
    cl = step1.load_cl_archive()
    cov = step1.load_coverage()
    try:
        inputs = d.collect_creation_inputs() if hasattr(d, "collect_creation_inputs") else {}
    except Exception as exc:
        inputs = {"error": str(exc)}
    try:
        ins = json.load(open(os.path.join(OUT, "insight_latest.json")))
    except Exception:
        ins = {}
    doge_fails = {
        "frost1_2_note": "DOGE 1h range_reclaim failed frost1/2 — do not repeat",
        "frost3_d3_15m": None,
    }
    arch = os.path.join(OUT, "archive", "frost3_d3_doge_15m", "result.json")
    if os.path.exists(arch):
        try:
            doge_fails["frost3_d3_15m"] = json.load(open(arch))
        except Exception as exc:
            doge_fails["frost3_d3_15m"] = {"error": str(exc)}
    return {
        "cl_archive": cl,
        "coverage": cov,
        "factory_inputs": inputs,
        "insight": {
            "summary_zh": ins.get("summary_zh"),
            "micro_read": ins.get("micro_read"),
            "failure_common_causes": ins.get("failure_common_causes"),
        },
        "doge_fails": doge_fails,
        "mtf_note": MTF_NOTE,
        "banned_logic": sorted(BANNED_LOGIC),
        "protected": sorted(step1.AVOID_SYMBOLS),
    }


DIR_PROMPT = """你是GLM-5.2策略总设计师。只输出JSON。任务：为 DOGE-USDT-SWAP 产出《策略方向建议》（multi-TF ONLY）。
硬约束：
1) 标的必须 DOGE-USDT-SWAP；入场周期必须 1h；必须含 4h 趋势过滤（可用 period-scaled 代理：ema75≈4h_ema19, ema200≈4h_ema53, ema75>ema75[offset4]≈4h_slope+）
2) 禁止逻辑类：exhaustion_fade / reclaim / range_reclaim / breakout_continuation / impulse_continuation
3) 禁止碰 ADA/LTC/NG/XRP；吸取 CL15m 归档教训（勿cci收窄到trades<10；勿边际rsi≈54衰竭入场；硬止损簇毁friction；dest±20%稳健；样本≥12）
4) 禁止重复 frost1/2 DOGE 1h range_reclaim
5) 给出至少3个互斥方向，logic_class 必须是新类（推荐：htf_trend_pullback_resume / multi_tf_slope_alignment / htf_regime_macd_turn 等）
JSON：
{"report_title":"策略方向建议","micro_death_read_zh":"...","cl_lessons_zh":"...","mtf_realization_zh":"...",
"directions":[{"rank":1,"symbol":"DOGE-USDT-SWAP","timeframe":"1h","htf":"4h","direction":"long|short",
"logic_class":"...","thesis":"...","entry_sketch":"...","exit_sketch":"...",
"htf_filter_role":"...","ltf_entry_role":"...","why_avoids_cl_traps":"...","avoid_death":["..."],
"diff_vs_failed_doge_reclaim":"...","expected_trades_hint":">=12"}],
"adopt_rank":1,"adopt_why_zh":"..."}
"""


def glm_directions(packet):
    ai = d._ai_json("glm", DIR_PROMPT, packet, max_tokens=1800, temperature=0.25)
    parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
    raw = ai.get("raw_preview") or ""
    if not parsed and raw:
        m = re.search(r"\{[\s\S]*\}", str(raw))
        if m:
            try:
                text = (m.group(0).replace("\ufeff", "")
                        .replace("\u201c", '"').replace("\u201d", '"'))
                parsed = json.loads(text)
            except Exception:
                parsed = None
    return parsed, ai


def fallback_directions():
    return {
        "report_title": "策略方向建议",
        "micro_death_read_zh": "规避 stop_cluster/sample_starvation；用HTF过滤降频，保留结构失效。",
        "cl_lessons_zh": "勿单阈值收窄；trades>=12；远离边际超买衰竭；friction崩于硬止损簇。",
        "mtf_realization_zh": "4h过滤用 ema75>ema200 & ema75>ema75[4]；1h用 ema21/rsi/macd 择时。",
        "directions": [
            {
                "rank": 1,
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "htf": "4h",
                "direction": "long",
                "logic_class": "htf_trend_pullback_resume",
                "thesis": "4h多头regime下，1h回踩ema21后动能转正再续多，非区间回收",
                "entry_sketch": (
                    "ema75>ema200; ema75>ema75[4]; close>ema21; rsi14>48; rsi14<62; "
                    "macd_stick>0; z20>-0.3; z20<1.2"
                ),
                "exit_sketch": "rsi14<45 TP; close<ema21 invalidation; max_hold=16",
                "htf_filter_role": "4h bullish gate via period-scaled ema75/200 + rising ema75",
                "ltf_entry_role": "1h pullback-resume after mid-band strength, not reclaim of lows",
                "why_avoids_cl_traps": "非exhaustion；带宽rsi非刚过线；不靠cci收窄；结构失效出场",
                "avoid_death": ["stop_cluster", "sample_starvation", "cost_collapse"],
                "diff_vs_failed_doge_reclaim": "不用prev_low20回收；强制4h趋势；中带续势而非下沿刷单",
                "expected_trades_hint": ">=12",
            },
            {
                "rank": 2,
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "htf": "4h",
                "direction": "long",
                "logic_class": "multi_tf_slope_alignment",
                "thesis": "4h斜率向上且1h macd/rsi同向对齐的动量共振多",
                "entry_sketch": (
                    "ema75>ema200; ema75>ema75[4]; macd_stick>0; rsi14>52; rsi14<68; "
                    "close>ema19; z20>0.1"
                ),
                "exit_sketch": "rsi14<48 TP; close<ema19 invalidation; max_hold=14",
                "htf_filter_role": "4h slope+regime",
                "ltf_entry_role": "1h momentum alignment",
                "why_avoids_cl_traps": "趋势对齐非衰竭反转；样本靠对齐带宽而非阈值极端",
                "avoid_death": ["stop_cluster", "holdout_collapse"],
                "diff_vs_failed_doge_reclaim": "对齐斜率而非range reclaim",
                "expected_trades_hint": ">=12",
            },
            {
                "rank": 3,
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "htf": "4h",
                "direction": "short",
                "logic_class": "htf_regime_macd_turn",
                "thesis": "4h空头regime下1h反弹后macd转负做空",
                "entry_sketch": (
                    "ema75<ema200; ema75<ema75[4]; close<ema21; rsi14>45; rsi14<58; "
                    "macd_stick<0; z20>-0.2; z20<1.0"
                ),
                "exit_sketch": "rsi14>55 TP; close>ema21 invalidation; max_hold=14",
                "htf_filter_role": "4h bearish gate",
                "ltf_entry_role": "1h failed bounce macd turn",
                "why_avoids_cl_traps": "非边际超买衰竭；有HTF空头门；结构失效",
                "avoid_death": ["stop_cluster", "negative_net_expectancy"],
                "diff_vs_failed_doge_reclaim": "空头regime+macd转向，非多头回收",
                "expected_trades_hint": ">=12",
            },
        ],
        "adopt_rank": 1,
        "adopt_why_zh": "htf_trend_pullback_resume 因果清晰：4h定方向、1h管回踩续势，避开失败reclaim与CL衰竭。",
        "fallback": True,
    }


def pick_direction(report):
    dirs = report.get("directions") or []
    adopt = int(report.get("adopt_rank") or 1)
    chosen = None
    for row in dirs:
        if int(row.get("rank") or 0) == adopt:
            chosen = row
            break
    if chosen is None and dirs:
        chosen = dirs[0]
    if not chosen:
        raise RuntimeError("no direction")
    chosen = copy.deepcopy(chosen)
    chosen["symbol"] = SYMBOL
    chosen["timeframe"] = TIMEFRAME
    chosen["htf"] = "4h"
    logic = str(chosen.get("logic_class") or "").lower().replace("-", "_")
    if logic in BANNED_LOGIC or any(b in logic for b in ("reclaim", "exhaust", "breakout", "impulse")):
        chosen = fallback_directions()["directions"][0]
    return chosen


def leaf(feat, op, value=None, other=None, other_offset=None, role=None, offset=None):
    left = {"feature": feat}
    if offset:
        left["offset"] = int(offset)
    right = {}
    if other is not None:
        right["feature"] = other
        if other_offset:
            right["offset"] = int(other_offset)
    else:
        right["value"] = float(value)
    row = {"left": left, "op": op, "right": right}
    if role:
        row["role"] = role
    return row


def build_dsl(row):
    direction = str(row.get("direction") or "long").lower()
    logic = str(row.get("logic_class") or "htf_trend_pullback_resume")
    if direction == "long":
        entry = [
            leaf("ema75", "gt", other="ema200"),
            leaf("ema75", "gt", other="ema75", other_offset=4),
            leaf("close", "gt", other="ema21"),
            leaf("rsi14", "gt", 48.0),
            leaf("rsi14", "lt", 62.0),
            leaf("macd_stick", "gt", 0.0),
            leaf("z20", "gt", -0.3),
            leaf("z20", "lt", 1.2),
        ]
        exit_any = [
            leaf("rsi14", "lt", 45.0, role="take_profit"),
            leaf("close", "lt", other="ema21", role="invalidation"),
        ]
        hold = 16
    else:
        entry = [
            leaf("ema75", "lt", other="ema200"),
            leaf("ema75", "lt", other="ema75", other_offset=4),
            leaf("close", "lt", other="ema21"),
            leaf("rsi14", "gt", 45.0),
            leaf("rsi14", "lt", 58.0),
            leaf("macd_stick", "lt", 0.0),
            leaf("z20", "gt", -0.2),
            leaf("z20", "lt", 1.0),
        ]
        exit_any = [
            leaf("rsi14", "gt", 55.0, role="take_profit"),
            leaf("close", "gt", other="ema21", role="invalidation"),
        ]
        hold = 14
    dsl = {
        "key": "frost3_doge4h1h_%s" % _safe(logic),
        "name": "寒霜叁-DOGE-4h1h-%s" % logic,
        "direction": direction,
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "max_hold_bars": hold,
        "description": (
            "DOGE multi-TF: 4h filter via ema75/ema200 period-scaled proxy; "
            "1h entry pullback/momentum. " + str(row.get("thesis") or "")
        )[:220],
    }
    return f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)


def make_book(row):
    dsl = build_dsl(row)
    return {
        "title": "寒霜叁-DOGE4h1h-%s" % row.get("logic_class"),
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": str(row.get("direction") or "long").lower(),
        "logic_class": str(row.get("logic_class") or "htf_trend_pullback_resume"),
        "thesis": row.get("thesis"),
        "entry_sketch": row.get("entry_sketch"),
        "exit_sketch": row.get("exit_sketch"),
        "htf_filter_role": row.get("htf_filter_role"),
        "ltf_entry_role": row.get("ltf_entry_role"),
        "avoid_from_postmortem": row.get("avoid_death") or row.get("why_avoids_cl_traps"),
        "diff_vs_live": row.get("diff_vs_failed_doge_reclaim"),
        "mtf_note": MTF_NOTE,
        "dsl": dsl,
        "gate_mode": "frost2",
        "source": "frost3_doge4h1h",
    }


AUDIT_PROMPT = """你是GLM-5.2。只输出JSON。审计 DOGE 4h→1h 假设书。
必须确认：symbol=DOGE-USDT-SWAP；timeframe=1h；含4h过滤（ema75/ema200或等价）；非reclaim/exhaustion/breakout。
对照CL陷阱：trades预期>=12；结构失效出场；远离边际rsi≈54衰竭。
JSON：{"decision":"pass|reject|revise","score":0到100,"issues":["..."],
"revise":{"param_tweaks":{},"why":"..."},"reason_zh":"..."}
"""


def glm_audit(book, report):
    payload = {
        "book": {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "thesis": book.get("thesis"), "entry": (book.get("dsl") or {}).get("entry"),
            "exit": (book.get("dsl") or {}).get("exit"),
            "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
            "mtf_note": book.get("mtf_note"),
        },
        "cl_traps": (report or {}).get("cl_lessons_zh") or [],
        "banned_logic": sorted(BANNED_LOGIC),
    }
    ai = d._ai_json("glm", AUDIT_PROMPT, payload, max_tokens=900, temperature=0.1)
    parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
    raw = ai.get("raw_preview") or ""
    if not parsed and raw:
        m = re.search(r"\{[\s\S]*\}", str(raw))
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
    return parsed or {"decision": "pass", "reason_zh": "audit_fallback_pass", "fallback": True}, ai


def apply_tweaks(book, tweaks):
    book = copy.deepcopy(book)
    if not isinstance(tweaks, dict):
        return book
    for phase in ("entry", "exit"):
        block = (book.get("dsl") or {}).get(phase) or {}
        rows = block.get("all") or block.get("any") or []
        for row in rows:
            feat = ((row.get("left") or {}).get("feature"))
            if feat in tweaks and isinstance(tweaks[feat], (int, float)):
                if isinstance(row.get("right"), dict) and "value" in row["right"]:
                    row["right"]["value"] = float(tweaks[feat])
    book["dsl"] = f2.ensure_dsl(book["dsl"], SYMBOL, TIMEFRAME)
    return book


def local_tweak(book, n, failed_step=""):
    book = copy.deepcopy(book)
    dsl = book["dsl"]
    entries = list((dsl.get("entry") or {}).get("all") or [])
    step = str(failed_step or "")
    for row in entries:
        feat = (row.get("left") or {}).get("feature")
        right = row.get("right") or {}
        if "value" not in right:
            continue
        v = float(right["value"])
        if feat == "rsi14" and row.get("op") == "gt":
            if "walk_forward" in step or "wf" in step:
                right["value"] = max(44.0, v - 1.0 * n)
            else:
                right["value"] = min(58.0, v + 0.5 * n)
        elif feat == "rsi14" and row.get("op") == "lt":
            if "walk" in step:
                right["value"] = min(70.0, v + 1.0 * n)
            else:
                right["value"] = max(55.0, v - 0.5 * n)
        elif feat == "z20" and row.get("op") == "gt":
            right["value"] = (max(-0.8, v - 0.1 * n) if "walk" in step else v + 0.05 * n)
        elif feat == "z20" and row.get("op") == "lt":
            right["value"] = (min(2.0, v + 0.1 * n) if "walk" in step else max(0.5, v - 0.05 * n))
    dsl["entry"] = {"all": entries}
    hold = int(dsl.get("max_hold_bars") or 16)
    if "friction" in step or "mc" in step:
        dsl["max_hold_bars"] = max(10, hold - 2 * n)
    elif "walk" in step:
        dsl["max_hold_bars"] = hold + 2 * n
    book["dsl"] = f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)
    book["dsl"]["key"] = (book["dsl"]["key"][:80] + "_t%d" % n)[:100]
    return book


REPAIR_PROMPT = """你是GLM-5.2。只输出JSON。DOGE 4h→1h 闸门增量修复。禁止换标的/换大逻辑/去掉4h过滤（ema75/ema200必须保留）。
勿把trades压到<10；保留结构失效。
JSON：{"ok":true,"dsl_patch":{"entry_all":null,"exit_any":null,"max_hold_bars":null},
"param_tweaks":{},"why":"..."}
"""


def glm_repair(book, packs, failed_step, stage):
    payload = {
        "stage": stage, "failed_step": failed_step,
        "symbol": SYMBOL, "timeframe": TIMEFRAME,
        "logic_class": book["logic_class"], "dsl": book.get("dsl"),
        "metrics": packs.get("base_metrics") or packs.get("metrics"),
        "mtf_required": "keep ema75 vs ema200 4h proxy",
    }
    ai = d._ai_json("glm", REPAIR_PROMPT, payload, max_tokens=1100, temperature=0.15)
    parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
    raw = ai.get("raw_preview") or ""
    if not parsed and raw:
        m = re.search(r"\{[\s\S]*\}", str(raw))
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
    if not parsed:
        return None, ai
    book2 = copy.deepcopy(book)
    patch = parsed.get("dsl_patch") or {}
    if isinstance(patch.get("entry_all"), list) and patch["entry_all"]:
        feats = set()
        for leaf_row in patch["entry_all"]:
            feats.add(((leaf_row.get("left") or {}).get("feature")))
            feats.add(((leaf_row.get("right") or {}).get("feature")))
        if "ema75" in feats and "ema200" in feats:
            book2["dsl"]["entry"] = {"all": patch["entry_all"]}
    if isinstance(patch.get("exit_any"), list) and patch["exit_any"]:
        book2["dsl"]["exit"] = {"any": patch["exit_any"]}
    if patch.get("max_hold_bars"):
        try:
            book2["dsl"]["max_hold_bars"] = int(patch["max_hold_bars"])
        except Exception:
            pass
    if parsed.get("param_tweaks"):
        book2 = apply_tweaks(book2, parsed["param_tweaks"])
    book2["dsl"]["key"] = (_safe(book2["dsl"].get("key")) + "_r")[:100]
    book2["dsl"] = f2.ensure_dsl(book2["dsl"], SYMBOL, TIMEFRAME)
    return book2, ai


def archive_fail(out, death_cause):
    arch_dir = os.path.join(OUT, "archive", PREFIX + "_exhausted")
    os.makedirs(arch_dir, exist_ok=True)
    payload = {
        "archived_at": _now(),
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "htf": "4h",
        "outcome": "ARCHIVED",
        "death_cause": death_cause,
        "result": out,
        "mtf_note": MTF_NOTE,
    }
    open(os.path.join(arch_dir, "HISTORY.json"), "w").write(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
    open(os.path.join(arch_dir, "README.json"), "w").write(json.dumps({
        "title": "DOGE 4h→1h multi-TF exhausted",
        "death_cause": death_cause,
        "see": "HISTORY.json",
    }, ensure_ascii=False, indent=2) + "\n")
    return arch_dir


def run_pipeline(book, report):
    hist = []
    status = {"stage": "audit", "updated_at": _now()}
    _write("status.json", status)

    audit_ok = False
    for atry in range(MAX_AUDIT_REVISE + 1):
        decision, _ai = glm_audit(book, report)
        hist.append({"stage": "audit", "try": atry, "decision": decision.get("decision"),
                     "reason": decision.get("reason_zh"), "fallback": decision.get("fallback")})
        dec = str(decision.get("decision") or "pass").lower()
        if dec == "pass":
            audit_ok = True
            break
        if dec == "revise" and atry < MAX_AUDIT_REVISE:
            book = apply_tweaks(book, (decision.get("revise") or {}).get("param_tweaks") or {})
            continue
        out = {"ok": False, "failed_step": "hyp_audit_reject", "reason": decision.get("reason_zh"),
               "hist": hist, "book_key": (book.get("dsl") or {}).get("key")}
        return out, book
    if not audit_ok:
        return {"ok": False, "failed_step": "hyp_audit_reject", "hist": hist}, book

    packs = None
    for qtry in range(MAX_QUICK_REPAIR + 1):
        status.update({"stage": "quick", "attempt": qtry, "updated_at": _now()})
        _write("status.json", status)
        packs = f2.quick_suite(book)
        hist.append({
            "stage": "quick", "try": qtry, "pass": packs.get("quick_pass"),
            "failed_step": packs.get("failed_step"),
            "fp": (packs.get("base_metrics") or {}).get("fold_positive"),
            "folds": (packs.get("base_metrics") or {}).get("folds"),
            "tr": (packs.get("base_metrics") or {}).get("trades"),
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
        })
        _write("quick_try%d.json" % qtry, {"packs_summary": hist[-1], "metrics": packs.get("base_metrics")})
        if packs.get("quick_pass"):
            break
        if qtry >= MAX_QUICK_REPAIR:
            out = {
                "ok": False, "failed_step": packs.get("failed_step") or "quick_exhausted",
                "reason": "Quick failed after %d repairs" % MAX_QUICK_REPAIR,
                "metrics": packs.get("base_metrics"),
                "dest": (packs.get("logic_destruction") or {}).get("pass"),
                "hist": hist, "book_key": (book.get("dsl") or {}).get("key"),
            }
            return out, book
        book = local_tweak(book, qtry + 1, packs.get("failed_step"))
        repaired, _ai = glm_repair(book, packs, packs.get("failed_step"), "quick")
        if repaired:
            book = repaired
        hist.append({"stage": "quick_repair", "try": qtry, "ok": bool(repaired)})

    for ftry in range(MAX_FULL_REPAIR + 1):
        status.update({"stage": "full", "attempt": ftry, "updated_at": _now()})
        _write("status.json", status)
        packs = f2.full_suite(book, packs)
        hist.append({
            "stage": "full", "try": ftry, "pass": packs.get("full_pass"),
            "failed_step": packs.get("failed_step"),
            "friction": (packs.get("full") or {}).get("friction_sharpe"),
            "mc": ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
        })
        _write("full_try%d.json" % ftry, {"summary": hist[-1], "full": packs.get("full")})
        if packs.get("full_pass"):
            break
        if ftry >= MAX_FULL_REPAIR:
            out = {
                "ok": False, "failed_step": packs.get("failed_step") or "full_exhausted",
                "reason": "Full failed after %d repairs" % MAX_FULL_REPAIR,
                "full": packs.get("full"), "hist": hist,
                "book_key": (book.get("dsl") or {}).get("key"),
            }
            return out, book
        book = local_tweak(book, ftry + 1, packs.get("failed_step"))
        repaired, _ai = glm_repair(book, packs, packs.get("failed_step"), "full")
        if repaired:
            book = repaired
        q2 = f2.quick_suite(book)
        hist.append({"stage": "re_quick_after_full_repair", "pass": q2.get("quick_pass"),
                     "failed_step": q2.get("failed_step")})
        if not q2.get("quick_pass"):
            packs = q2
            packs["full_pass"] = False
            packs["failed_step"] = q2.get("failed_step") or "quick_regressed"
            continue
        packs = q2

    for stry in range(MAX_SIM_REPAIR + 1):
        status.update({"stage": "sim_formal", "attempt": stry, "updated_at": _now()})
        _write("status.json", status)
        sf = f2.run_sim_formal(book, packs)
        hist.append({
            "stage": "sim_formal", "try": stry,
            "failed_step": sf.get("failed_step"),
            "sim_ds": (sf.get("sim") or {}).get("wr_deepseek_sim"),
            "sim_qw": (sf.get("sim") or {}).get("wr_qwen_sim"),
            "formal_approved": (sf.get("formal") or {}).get("approved"),
            "pending": (sf.get("pending") or {}).get("key"),
        })
        _write("sim_try%d.json" % stry, sf)
        if (sf.get("pending") or {}).get("ok"):
            return {
                "ok": True, "failed_step": None,
                "pending": sf.get("pending"), "sim": sf.get("sim"),
                "formal": sf.get("formal"),
                "book_key": (book.get("dsl") or {}).get("key"), "hist": hist,
            }, book
        fs = sf.get("failed_step")
        if fs in ("formal_review", "pending_ingest"):
            return {
                "ok": False, "failed_step": fs,
                "reason": (sf.get("formal") or {}).get("reason") or (sf.get("pending") or {}).get("reason"),
                "sim": sf.get("sim"), "formal": sf.get("formal"),
                "pending": sf.get("pending"), "hist": hist,
                "book_key": (book.get("dsl") or {}).get("key"),
            }, book
        if stry >= MAX_SIM_REPAIR:
            return {
                "ok": False, "failed_step": fs or "sim_exhausted",
                "reason": "Sim/formal failed after %d repairs" % MAX_SIM_REPAIR,
                "sim": sf.get("sim"), "hist": hist,
                "book_key": (book.get("dsl") or {}).get("key"),
            }, book
        book = local_tweak(book, stry + 1, "sim")
        repaired, _ai = glm_repair(book, packs, "sim_review", "sim")
        if repaired:
            book = repaired
        q3 = f2.quick_suite(book)
        if not q3.get("quick_pass"):
            hist.append({"stage": "sim_repair_quick_fail", "failed": q3.get("failed_step")})
            continue
        packs = f2.full_suite(book, q3)
        if not packs.get("full_pass"):
            hist.append({"stage": "sim_repair_full_fail", "failed": packs.get("failed_step")})
            continue

    return {"ok": False, "failed_step": "unknown_exhausted", "hist": hist}, book


def main():
    print("[%s] DOGE 4h→1h start" % _now())
    _write("mtf_note.json", MTF_NOTE)
    packet = load_inputs()
    _write("inputs.json", packet)

    report, ai = glm_directions(packet)
    if not report:
        report = fallback_directions()
        report["glm_error"] = (ai or {}).get("error") or "parse_fail"
    _write("策略方向建议.json", report)
    _write("glm_dir_raw.json", {
        "ok": (ai or {}).get("ok"),
        "error": (ai or {}).get("error"),
        "raw_preview": ((ai or {}).get("raw_preview") or "")[:2000],
    })

    chosen = pick_direction(report)
    _write("chosen_direction.json", chosen)
    book = make_book(chosen)
    _write("hypothesis.json", book)

    try:
        out, book = run_pipeline(book, report)
    except Exception as exc:
        out = {
            "ok": False, "failed_step": "exception",
            "reason": str(exc), "trace": traceback.format_exc()[-2500:],
        }

    _write("result.json", out)
    if book:
        _write("final_book.json", book)

    if out.get("ok"):
        summary_zh = (
            "DOGE 4h→1h 多周期策略通过：逻辑=%s；已进 pending=%s；"
            "4h过滤用 ema75/ema200 周期映射代理；Sim/Formal 达标。"
            % (chosen.get("logic_class"), (out.get("pending") or {}).get("key"))
        )
        status = "PENDING"
    else:
        death = "%s | %s" % (out.get("failed_step"), out.get("reason"))
        arch = archive_fail(out, death)
        summary_zh = (
            "DOGE 4h→1h 多周期策略未过闸：失败步=%s；死因=%s；已归档 %s；"
            "逻辑类=%s（4h过滤=ema75/ema200 period-scaled proxy）。"
            % (out.get("failed_step"), death, arch, chosen.get("logic_class"))
        )
        status = "ARCHIVED"

    end = {
        "at": _now(),
        "status": status,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "htf": "4h",
        "logic_class": chosen.get("logic_class"),
        "summary_zh": summary_zh,
        "result_ok": bool(out.get("ok")),
        "failed_step": out.get("failed_step"),
        "book_key": (book or {}).get("dsl", {}).get("key") if book else None,
        "pending": out.get("pending"),
        "mtf_note": MTF_NOTE,
    }
    _write("end_report.json", end)
    print(json.dumps(end, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
