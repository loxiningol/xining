#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone SOL-USDT-SWAP 4h macro exhaustion creation (frost3_sol4h_macro_*).

Logic: long-TF extreme sentiment exhaustion → reverse entry
  RSI extremes + Bollinger outer close (z20) + volume/range divergence (vol_z20).

Hard constraints:
  - Symbol SOL-USDT-SWAP / timeframe 4h only
  - Artifacts: /root/auto_trade/dual_engine/frost3_sol4h_macro_*
  - Must NOT collide with frost3_sol15m_*
  - Protect live ADA/LTC/NG/XRP
  - Read CL15m archive + freezer long-TF deaths (avoid 假衰竭接飞刀)
  - Gates: WF>=7/10, dest, MC beat>=90%, friction Sharpe>=0
  - Sim DS/Qwen >=55%/55% → formal → pending
  - Quick/Full/Sim repair caps = 3

Feature proxies (documented in frost3_sol4h_macro_feature_note.json):
  - BB outer close ≈ |z20| >= ~2.0  (z20 is (close-MA20)/std20)
  - vol_z20 = volume z-score when volume present; else bar-range z-score proxy
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
PREFIX = "frost3_sol4h_macro"
SYMBOL = "SOL-USDT-SWAP"
TIMEFRAME = "4h"
MAX_AUDIT_REVISE = 2
MAX_QUICK_REPAIR = 3
MAX_FULL_REPAIR = 3
MAX_SIM_REPAIR = 3

# Do not clone live XRP/LTC/NG fade params; this is 4h macro BB+vol-div.
BANNED_CLONE_HINTS = (
    "xrp_15m_clone", "ltc_fade_clone", "ng_fade_clone", "ada_clone",
)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write(name, obj):
    base = name if str(name).startswith(PREFIX) else (PREFIX + "_" + name)
    if not base.endswith(".json"):
        base = base + ".json"
    path = os.path.join(OUT, base)
    open(path, "w").write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


def _safe(s):
    s = str(s or "x")
    for ch in (".", " ", "/", ":", "+", "%", "-"):
        s = s.replace(ch, "p" if ch in (".", "-") else "_")
    return s[:40]


FEATURE_NOTE = {
    "symbol": SYMBOL,
    "timeframe": TIMEFRAME,
    "bb_proxy": {
        "feature": "z20",
        "meaning": "(close - MA20) / std20 — Bollinger outer close when |z20|>=~2",
        "short_extreme": "z20 >= 2.0",
        "long_extreme": "z20 <= -2.0",
    },
    "volume_divergence_proxy": {
        "feature": "vol_z20",
        "true_volume": "z-score of OKX candle volume vs 20-bar when volume column present",
        "fallback": "bar-range (high-low) z-score when volume absent (range_vol_proxy)",
        "divergence_impl": (
            "vol_z20 < vol_z20[offset=2] — declining participation/expansion vs prior bars "
            "(avoids range-proxy trap where outer BB bars have high range z-score)"
        ),
        "rejection_confirm": "short: close<open; long: close>open",
    },
    "true_vs_fake_exhaustion": {
        "true": (
            "RSI extreme AND BB outer AND vol/range divergence AND macd already turning "
            "against the impulse — participation fails to confirm the extreme"
        ),
        "fake_knife_catch": (
            "RSI just-over-line or mid CCI without BB outer / without divergence — "
            "trend continuation; CL15m hard-stop pattern"
        ),
        "avoid": [
            "边际 rsi≈54 衰竭入场",
            "单阈值收窄到 trades<10",
            "无结构失效出场",
            "盲克隆 XRP/LTC/NG fade 参数",
        ],
    },
    "data_path": "/root/market_data/SOL-USDT-SWAP/4h/",
    "parallel_safe_vs_sol15m": True,
    "artifact_prefix": PREFIX,
}


def load_freezer_long_tf():
    """Pull freezer / failure vault lessons focusing on long-TF / exhaustion."""
    lessons = []
    try:
        from auto_trade_strategy_creation_factory import build_factory_context
        ctx = build_factory_context()
        for x in list(ctx.get("freezer_lessons") or []):
            if not isinstance(x, dict):
                continue
            s = json.dumps(x, ensure_ascii=False)
            if any(k in s.lower() for k in (
                "exhaust", "4h", "1h", "macro", "reclaim", "fade",
                "衰竭", "飞刀", "假衰", "btc5_exhaust", "cl5_exhaust",
            )):
                lessons.append(x)
        if not lessons:
            lessons = list(ctx.get("freezer_lessons") or [])[-10:]
    except Exception as exc:
        lessons = [{"error": str(exc)}]
    return lessons


def load_inputs():
    cl = step1.load_cl_archive()
    cov = step1.load_coverage()
    freezer = load_freezer_long_tf()
    try:
        ins = json.load(open(os.path.join(OUT, "insight_latest.json")))
    except Exception:
        ins = {}
    # SOL vol / extreme episode notes from insight + known deaths
    sol_vol = {
        "character_zh": (
            "SOL 高Beta：长周期极端情绪常伴随趋势惯性；纯超买/超卖反转易「假衰竭接飞刀」。"
            "4h 上需 BB 外轨+量能/波幅背离确认，且 RSI 用极端带(≥70/≤30)而非边际带。"
        ),
        "related_deaths": [
            "btc5_exhaustion_reclaim_long_ai → friction_negative / stop_cluster",
            "cl5_exhaustion_fade_short_ai → AI reject / hard-stop style",
            "frost2 CL15m exhaustion_fade → 边际rsi+硬止损簇毁 friction；WF样本饥渴",
            "frost2 SOL5m impulse 失败 — 不同逻辑但仍警示 SOL 波动与 stop_cluster",
        ],
        "extreme_episode_rule_zh": (
            "真衰竭：价格打到 BB 外轨 + RSI 极端 + vol_z20 偏弱/背离 + macd 已反向；"
            "假衰竭：仅 RSI/CCI 刚过线、趋势均线仍强、无量能背离 → 禁止入场。"
        ),
    }
    return {
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "cl_archive": cl,
        "coverage_n": len(cov),
        "freezer_long_tf": freezer[:12],
        "insight": {
            "summary_zh": ins.get("summary_zh"),
            "failure_common_causes": ins.get("failure_common_causes"),
            "micro_read": ins.get("micro_read"),
        },
        "sol_long_tf_vol": sol_vol,
        "feature_note": FEATURE_NOTE,
        "protected": sorted(step1.AVOID_SYMBOLS),
        "gates": {
            "WF": ">=7/10",
            "logic_destruction": "pass",
            "MC_beat": ">=90%",
            "friction_sharpe": ">=0",
            "sim": ">=55%/55%",
        },
        "diff_vs_sol15m": "separate artifact prefix frost3_sol4h_macro_*; native 4h bars",
        "diff_vs_live_fade": "not XRP/LTC/NG param clone; requires z20 outer + vol_z20 divergence",
    }


DIR_PROMPT = """你是GLM-5.2策略总设计师。只输出JSON。任务：输出《SOL 4h 宏观衰竭参数方案》。
硬约束：
1) 仅 SOL-USDT-SWAP / 4h；逻辑=宏观极端情绪衰竭反转（macro_sentiment_exhaustion）
2) 入场必须同时含：RSI极端(短>=70或长<=30) + BB外轨代理z20(|z|>=约2) + vol_z20背离(弱势/下降) + macd已反向；禁止边际rsi≈54
3) 必须规避「假衰竭接飞刀」：无量能/波幅背离不得入场；需结构失效出场(prev_high20/prev_low20)
4) 吸取CL15m归档：trades预期>=12；勿单阈值压到<10；硬止损簇毁friction；dest±20%稳健
5) 禁止盲克隆 XRP/LTC/NG fade 参数；禁止碰 ADA/LTC/NG/XRP
6) 给出3个互斥变体（同属宏观衰竭，但方向或确认组合不同），并指定 adopt_rank
JSON：
{"report_title":"SOL 4h 宏观衰竭参数方案",
"sol_vol_read_zh":"...","cl_lessons_zh":"...","fake_exhaustion_avoid_zh":"...",
"hypothesis_true_vs_fake_zh":"如何区分真衰竭与趋势延续",
"directions":[{"rank":1,"symbol":"SOL-USDT-SWAP","timeframe":"4h","direction":"short|long",
"logic_class":"macro_sentiment_exhaustion","thesis":"...",
"entry_sketch":"rsi14>70; z20>2.0; vol_z20<0.5; macd_stick<0; close<ema8; ...",
"exit_sketch":"rsi14<45 TP; close>prev_high20 inv; max_hold=10",
"why_not_knife_catch":"...","why_avoids_cl_traps":"...","avoid_death":["stop_cluster"],
"expected_trades_hint":">=12","diff_vs_xrp_ltc_ng_fade":"..."}],
"adopt_rank":1,"adopt_why_zh":"...","param_scheme_zh":"核心参数区间说明"}
"""


def glm_param_scheme(packet):
    ai = d._ai_json("glm", DIR_PROMPT, packet, max_tokens=2000, temperature=0.2)
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


def fallback_scheme():
    return {
        "report_title": "SOL 4h 宏观衰竭参数方案",
        "sol_vol_read_zh": (
            "SOL长周期高波动：极端后常趋势延续；必须用BB外轨+vol_z20背离过滤假衰竭。"
        ),
        "cl_lessons_zh": (
            "CL15m：边际rsi+中等cci→硬止损≈-22%；收窄过滤→WF折数不足；dest对软边界敏感。"
        ),
        "fake_exhaustion_avoid_zh": (
            "禁止仅凭rsi刚超买做空；无vol_z20弱势背离=接飞刀；失效用prev_high20而非纯时间止损。"
        ),
        "hypothesis_true_vs_fake_zh": (
            "真衰竭=价格外轨极端+参与度/波幅背离+动能已翻；假衰竭=趋势均线仍顺+量能同步放大。"
        ),
        "directions": [
            {
                "rank": 1,
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "direction": "short",
                "logic_class": "macro_sentiment_exhaustion",
                "thesis": "4h顶部：RSI极端超买+收盘打到BB上轨(z20)且vol_z20偏弱，macd已转负后反手做空",
                "entry_sketch": (
                    "rsi14>68; z20>1.8; vol_z20<vol_z20[2]; macd_stick<0; "
                    "close<open; close<ema8"
                ),
                "exit_sketch": "rsi14<48 take_profit; close>prev_high20 invalidation; max_hold=10",
                "why_not_knife_catch": "要求vol_z20相对前2根下降+阴线确认+macd已翻，而非追涨杀跌",
                "why_avoids_cl_traps": "RSI用70极端带非54；多条件共振保样本；结构失效出场",
                "avoid_death": ["stop_cluster", "sample_starvation", "cost_collapse"],
                "expected_trades_hint": ">=12",
                "diff_vs_xrp_ltc_ng_fade": "强制z20外轨+vol_z20；非15m fade参数迁移",
            },
            {
                "rank": 2,
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "direction": "long",
                "logic_class": "macro_sentiment_exhaustion",
                "thesis": "4h底部对称：RSI极端超卖+BB下轨+vol背离+macd转正后反手做多",
                "entry_sketch": (
                    "rsi14<32; z20<-1.8; vol_z20<vol_z20[2]; macd_stick>0; "
                    "close>open; close>ema8"
                ),
                "exit_sketch": "rsi14>52 take_profit; close<prev_low20 invalidation; max_hold=10",
                "why_not_knife_catch": "底部需动能转正+vol_z20相对下降+阳线确认，避免下跌中继接刀",
                "why_avoids_cl_traps": "极端带+外轨+背离三重确认；结构失效",
                "avoid_death": ["stop_cluster", "negative_net_expectancy"],
                "expected_trades_hint": ">=12",
                "diff_vs_xrp_ltc_ng_fade": "多头宏观衰竭，非短周期fade克隆",
            },
            {
                "rank": 3,
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "direction": "short",
                "logic_class": "macro_sentiment_exhaustion",
                "thesis": "更严顶部：外轨+极端RSI+vol明显萎缩(vol_z20<0)+cci极端，拒绝中继",
                "entry_sketch": (
                    "rsi14>70; z20>2.0; vol_z20<vol_z20[3]; macd_stick<0; "
                    "cci>100; close<open; close<ema16"
                ),
                "exit_sketch": "rsi14<45 take_profit; close>prev_high20 invalidation; max_hold=8",
                "why_not_knife_catch": "vol_z20相对3根前下降+阴线+cci极端，过滤量价齐升加速",
                "why_avoids_cl_traps": "更极端阈值但用多特征保持可交易样本；禁单阈值",
                "avoid_death": ["stop_cluster", "holdout_collapse"],
                "expected_trades_hint": ">=12",
                "diff_vs_xrp_ltc_ng_fade": "宏观外轨衰竭，非live fade镜像",
            },
        ],
        "adopt_rank": 1,
        "adopt_why_zh": (
            "空头宏观衰竭变体因果最清晰：外轨+极端RSI+vol背离+macd翻负，最能区分真衰竭与接飞刀。"
        ),
        "param_scheme_zh": (
            "rsi short>=68 / long<=32；|z20|>=1.8；vol_z20<vol_z20[offset2]；"
            "阴阳线确认；macd反向；max_hold 8~10根4h；TP中性RSI；inv=结构高低点。"
        ),
        "fallback": True,
    }


APPROVE_PROMPT = """你是GLM-5.2。只输出JSON。审批《SOL 4h 宏观衰竭参数方案》是否可进入回测。
必须确认：4h、SOL、含z20外轨+vol_z20背离+RSI极端、非假衰竭接飞刀、非XRP克隆、非CL边际入场。
JSON：{"decision":"approve|reject|revise","score":0到100,"issues":["..."],
"revise":{"adopt_rank":null,"param_tweaks":{},"why":"..."},"reason_zh":"..."}
"""


def glm_approve(report):
    ai = d._ai_json("glm", APPROVE_PROMPT, {"report": report, "feature_note": FEATURE_NOTE},
                    max_tokens=800, temperature=0.1)
    parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
    raw = ai.get("raw_preview") or ""
    if not parsed and raw:
        m = re.search(r"\{[\s\S]*\}", str(raw))
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
    return parsed or {"decision": "approve", "reason_zh": "approve_fallback", "fallback": True}, ai


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
        chosen = fallback_scheme()["directions"][0]
    chosen = copy.deepcopy(chosen)
    chosen["symbol"] = SYMBOL
    chosen["timeframe"] = TIMEFRAME
    chosen["logic_class"] = "macro_sentiment_exhaustion"
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
    direction = str(row.get("direction") or "short").lower()
    logic = "macro_sentiment_exhaustion"
    if direction == "long":
        entry = [
            leaf("rsi14", "lt", 32.0),
            leaf("z20", "lt", -1.8),
            # declining vol/range vs 2 bars ago (divergence)
            leaf("vol_z20", "lt", other="vol_z20", other_offset=2),
            leaf("macd_stick", "gt", 0.0),
            leaf("close", "gt", other="open"),
            leaf("close", "gt", other="ema8"),
        ]
        exit_any = [
            leaf("rsi14", "gt", 52.0, role="take_profit"),
            leaf("close", "lt", other="prev_low20", role="invalidation"),
        ]
        hold = 10
    else:
        entry = [
            leaf("rsi14", "gt", 68.0),
            leaf("z20", "gt", 1.8),
            leaf("vol_z20", "lt", other="vol_z20", other_offset=2),
            leaf("macd_stick", "lt", 0.0),
            leaf("close", "lt", other="open"),
            leaf("close", "lt", other="ema8"),
        ]
        exit_any = [
            leaf("rsi14", "lt", 48.0, role="take_profit"),
            leaf("close", "gt", other="prev_high20", role="invalidation"),
        ]
        hold = 10
    # optional sketch overrides for numeric thresholds only
    sketch = str(row.get("entry_sketch") or "")
    m = re.search(r"rsi14\s*>\s*([0-9.]+)", sketch, re.I)
    if m and direction == "short":
        entry[0] = leaf("rsi14", "gt", max(66.0, float(m.group(1))))
    m = re.search(r"rsi14\s*<\s*([0-9.]+)", sketch, re.I)
    if m and direction == "long":
        entry[0] = leaf("rsi14", "lt", min(34.0, float(m.group(1))))
    m = re.search(r"z20\s*>\s*([0-9.]+)", sketch, re.I)
    if m and direction == "short":
        entry[1] = leaf("z20", "gt", max(1.6, float(m.group(1))))
    m = re.search(r"z20\s*<\s*-?([0-9.]+)", sketch, re.I)
    if m and direction == "long":
        entry[1] = leaf("z20", "lt", -max(1.6, abs(float(m.group(1)))))
    m = re.search(r"max_hold\s*=\s*(\d+)", str(row.get("exit_sketch") or ""), re.I)
    if m:
        hold = max(6, int(m.group(1)))
    dsl = {
        "key": "frost3_sol4h_macro_%s_%s" % (direction[0], _safe(logic)[:18]),
        "name": "寒霜叁-SOL-4h-宏观衰竭",
        "direction": direction,
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "max_hold_bars": hold,
        "description": (
            "SOL 4h macro exhaustion: RSI extreme + z20 BB-outer + vol_z20 divergence. "
            + str(row.get("thesis") or "")
        )[:220],
    }
    return f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)


def make_book(row):
    dsl = build_dsl(row)
    return {
        "title": "寒霜叁-SOL4h宏观衰竭-%s" % row.get("direction"),
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": str(row.get("direction") or "short").lower(),
        "logic_class": "macro_sentiment_exhaustion",
        "thesis": row.get("thesis"),
        "entry_sketch": row.get("entry_sketch"),
        "exit_sketch": row.get("exit_sketch"),
        "hypothesis_true_vs_fake": row.get("why_not_knife_catch"),
        "avoid_from_postmortem": row.get("avoid_death") or row.get("why_avoids_cl_traps"),
        "diff_vs_live": row.get("diff_vs_xrp_ltc_ng_fade"),
        "feature_note": FEATURE_NOTE,
        "dsl": dsl,
        "gate_mode": "frost2",
        "source": "frost3_sol4h_macro",
    }


AUDIT_PROMPT = """你是GLM-5.2。只输出JSON。审计 SOL 4h 宏观衰竭假设书。
必须：symbol=SOL-USDT-SWAP；timeframe=4h；含rsi极端+z20外轨+vol_z20；非边际衰竭；有结构失效。
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
            "feature_note": FEATURE_NOTE,
        },
        "cl_traps": (report or {}).get("cl_lessons_zh"),
        "fake_avoid": (report or {}).get("fake_exhaustion_avoid_zh"),
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
    """Loosen extremes carefully for WF sample; never to marginal RSI≈54."""
    book = copy.deepcopy(book)
    dsl = book["dsl"]
    entries = list((dsl.get("entry") or {}).get("all") or [])
    step = str(failed_step or "")
    direction = str(book.get("direction") or "short")
    for row in entries:
        feat = (row.get("left") or {}).get("feature")
        right = row.get("right") or {}
        if "value" not in right:
            continue
        v = float(right["value"])
        if feat == "rsi14" and row.get("op") == "gt":
            # short: loosen toward 66 floor (still extreme-ish), never to 54
            if "walk" in step or "wf" in step:
                right["value"] = max(66.0, v - 1.0 * n)
            else:
                right["value"] = min(78.0, v + 0.5 * n)
        elif feat == "rsi14" and row.get("op") == "lt":
            if "walk" in step:
                right["value"] = min(34.0, v + 1.0 * n)
            else:
                right["value"] = max(22.0, v - 0.5 * n)
        elif feat == "z20" and row.get("op") == "gt":
            if "walk" in step:
                right["value"] = max(1.6, v - 0.1 * n)  # still near outer
            else:
                right["value"] = v + 0.1 * n
        elif feat == "z20" and row.get("op") == "lt":
            if "walk" in step:
                right["value"] = min(-1.6, v + 0.1 * n)
            else:
                right["value"] = v - 0.1 * n
    dsl["entry"] = {"all": entries}
    hold = int(dsl.get("max_hold_bars") or 10)
    if "friction" in step or "mc" in step:
        dsl["max_hold_bars"] = max(6, hold - n)
    elif "walk" in step:
        dsl["max_hold_bars"] = min(16, hold + n)
    book["dsl"] = f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)
    book["dsl"]["key"] = (book["dsl"]["key"][:80] + "_t%d" % n)[:100]
    return book


REPAIR_PROMPT = """你是GLM-5.2。只输出JSON。SOL 4h宏观衰竭闸门增量修复。
禁止换标的/换周期；必须保留 rsi极端+z20外轨+vol_z20；勿降到边际rsi≈54；trades勿压到<10。
JSON：{"ok":true,"dsl_patch":{"entry_all":null,"exit_any":null,"max_hold_bars":null},
"param_tweaks":{},"why":"..."}
"""


def glm_repair(book, packs, failed_step, stage):
    payload = {
        "stage": stage, "failed_step": failed_step,
        "symbol": SYMBOL, "timeframe": TIMEFRAME,
        "logic_class": book["logic_class"], "dsl": book.get("dsl"),
        "metrics": packs.get("base_metrics") or packs.get("metrics"),
        "must_keep": ["rsi14 extreme", "z20 outer", "vol_z20"],
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
        # require core triad
        if "rsi14" in feats and "z20" in feats and "vol_z20" in feats:
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
        "outcome": "ARCHIVED",
        "death_cause": death_cause,
        "result": out,
        "feature_note": FEATURE_NOTE,
    }
    open(os.path.join(arch_dir, "HISTORY.json"), "w").write(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
    open(os.path.join(arch_dir, "README.json"), "w").write(json.dumps({
        "title": "SOL 4h macro exhaustion exhausted",
        "death_cause": death_cause,
        "see": "HISTORY.json",
    }, ensure_ascii=False, indent=2) + "\n")
    return arch_dir


def ensure_4h_frame_ready():
    """Verify native 4h research frame loads with vol_z20."""
    fr = d._frame(SYMBOL, TIMEFRAME)
    n = len(fr) if fr is not None else 0
    cols = list(fr.columns) if fr is not None else []
    note = {
        "rows": n,
        "has_vol_z20": "vol_z20" in cols,
        "has_z20": "z20" in cols,
        "has_volume_col": "volume" in cols,
        "index_start": str(fr.index[0]) if n else None,
        "index_end": str(fr.index[-1]) if n else None,
        "vol_proxy_mode": "true_volume" if "volume" in cols else "range_vol_proxy",
    }
    _write("frame_probe.json", note)
    if n < 80:
        raise RuntimeError("SOL 4h frame too short: %s" % note)
    if "vol_z20" not in cols or "z20" not in cols:
        raise RuntimeError("missing BB/vol features: %s" % note)
    return note


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
        return {
            "ok": False, "failed_step": "hyp_audit_reject",
            "reason": decision.get("reason_zh"), "hist": hist,
            "book_key": (book.get("dsl") or {}).get("key"),
        }, book
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
        _write("quick_try%d.json" % qtry, {
            "packs_summary": hist[-1], "metrics": packs.get("base_metrics"),
            "key": (book.get("dsl") or {}).get("key"),
        })
        if packs.get("quick_pass"):
            break
        if qtry >= MAX_QUICK_REPAIR:
            return {
                "ok": False, "failed_step": packs.get("failed_step") or "quick_exhausted",
                "reason": "Quick failed after %d repairs" % MAX_QUICK_REPAIR,
                "metrics": packs.get("base_metrics"),
                "dest": (packs.get("logic_destruction") or {}).get("pass"),
                "hist": hist, "book_key": (book.get("dsl") or {}).get("key"),
            }, book
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
            return {
                "ok": False, "failed_step": packs.get("failed_step") or "full_exhausted",
                "reason": "Full failed after %d repairs" % MAX_FULL_REPAIR,
                "full": packs.get("full"), "hist": hist,
                "book_key": (book.get("dsl") or {}).get("key"),
            }, book
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
                "reason": (sf.get("formal") or {}).get("reason") or (
                    sf.get("pending") or {}).get("reason"),
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
    print("[%s] SOL 4h macro exhaustion start" % _now(), flush=True)
    _write("feature_note.json", FEATURE_NOTE)

    # Ensure 4h timeframe supported + data present
    frame_note = ensure_4h_frame_ready()
    print("[sol4h] frame", frame_note, flush=True)

    packet = load_inputs()
    _write("inputs.json", packet)

    report, ai = glm_param_scheme(packet)
    if not report:
        report = fallback_scheme()
        report["glm_error"] = (ai or {}).get("error") or "parse_fail"
    _write("SOL_4h_宏观衰竭参数方案.json", report)
    _write("glm_scheme_raw.json", {
        "ok": (ai or {}).get("ok"),
        "error": (ai or {}).get("error"),
        "raw_preview": ((ai or {}).get("raw_preview") or "")[:2500],
    })

    approve, ai_ap = glm_approve(report)
    _write("glm_approve.json", {"decision": approve, "ai_ok": (ai_ap or {}).get("ok")})
    dec = str(approve.get("decision") or "approve").lower()
    if dec == "reject":
        out = {
            "ok": False, "failed_step": "glm_scheme_reject",
            "reason": approve.get("reason_zh") or approve.get("issues"),
        }
        _write("result.json", out)
        arch = archive_fail(out, out["failed_step"] + " | " + str(out.get("reason")))
        end = {
            "at": _now(), "status": "ARCHIVED", "summary_zh": (
                "SOL 4h 宏观衰竭方案被GLM否决：%s；已归档 %s" % (out.get("reason"), arch)
            ),
        }
        _write("end_report.json", end)
        print(json.dumps(end, ensure_ascii=False, indent=2))
        return end
    if dec == "revise" and approve.get("revise"):
        rev = approve["revise"]
        if rev.get("adopt_rank"):
            report["adopt_rank"] = rev["adopt_rank"]
        _write("SOL_4h_宏观衰竭参数方案.json", report)

    chosen = pick_direction(report)
    _write("chosen_direction.json", chosen)
    book = make_book(chosen)
    if approve.get("revise") and (approve["revise"].get("param_tweaks")):
        book = apply_tweaks(book, approve["revise"]["param_tweaks"])
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
            "SOL 4h 宏观衰竭策略通过闸门：逻辑=macro_sentiment_exhaustion；"
            "方向=%s；已进 pending=%s；代理：z20=BB外轨，vol_z20=%s。"
            % (
                chosen.get("direction"),
                (out.get("pending") or {}).get("key"),
                frame_note.get("vol_proxy_mode"),
            )
        )
        status = "PENDING"
        arch = None
    else:
        death = "%s | %s" % (out.get("failed_step"), out.get("reason"))
        arch = archive_fail(out, death)
        summary_zh = (
            "SOL 4h 宏观衰竭未过闸：失败步=%s；死因=%s；已归档 %s；"
            "假设=%s/%s（RSI极端+z20外轨+vol_z20背离）。"
            % (out.get("failed_step"), death, arch, chosen.get("direction"),
               chosen.get("logic_class"))
        )
        status = "ARCHIVED"

    end = {
        "at": _now(),
        "status": status,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_class": "macro_sentiment_exhaustion",
        "direction": chosen.get("direction"),
        "summary_zh": summary_zh,
        "result_ok": bool(out.get("ok")),
        "failed_step": out.get("failed_step"),
        "book_key": (book or {}).get("dsl", {}).get("key") if book else None,
        "pending": out.get("pending"),
        "archive": arch,
        "feature_note": FEATURE_NOTE,
        "frame_note": frame_note,
        "hist_tail": (out.get("hist") or [])[-8:],
    }
    _write("end_report.json", end)
    print(json.dumps({"status": status, "summary_zh": summary_zh,
                      "failed_step": out.get("failed_step"),
                      "pending": out.get("pending")}, ensure_ascii=False, indent=2))
    return end


if __name__ == "__main__":
    main()
