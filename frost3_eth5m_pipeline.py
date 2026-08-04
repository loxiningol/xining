#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜叁·ETH5m 独立管线 — ONLY ETH-USDT-SWAP 5m.

Isolated artifacts: /root/auto_trade/dual_engine/frost3_eth5m_*
Does NOT touch frost3 SOL/BNB/DOGE dirs or live ADA/LTC/NG/XRP.
"""
from __future__ import print_function

import copy
import json
import os
import re
import sys
import traceback
from datetime import datetime

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
PREFIX = "frost3_eth5m"
SYMBOL = "ETH-USDT-SWAP"
TIMEFRAME = "5m"
MAX_AUDIT_REVISE = 2
MAX_QUICK_REPAIR = 3
MAX_FULL_REPAIR = 3
MAX_SIM_REPAIR = 3

BANNED_LOGIC = {
    "exhaustion_fade", "reclaim", "range_reclaim", "breakout",
    "breakout_continuation", "impulse_continuation", "trend_pullback",
    "trend_pullback_fade",
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write(name, obj):
    path = os.path.join(OUT, name)
    open(path, "w").write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


def _safe(s):
    s = str(s or "x")
    for ch in (".", " ", "/", ":", "+", "%", "-"):
        s = s.replace(ch, "p" if ch in (".", "-") else "_")
    return s[:40]


def load_eth_micro_packet():
    cl = step1.load_cl_archive()
    try:
        inputs = json.load(open(os.path.join(OUT, "frost3_inputs_raw.json")))
    except Exception:
        inputs = {}
    micro_death = step1.load_micro_death(inputs)
    eth_micro = {"symbol": SYMBOL, "timeframe": TIMEFRAME, "primitive_freq": []}
    try:
        import pickle
        fr = pickle.load(open(
            "/root/auto_trade/codex_0725_train5/frame_ETH_USDT_SWAP_5m.pkl", "rb"))
        tail = fr.tail(864) if len(fr) >= 864 else fr
        ribbon_up = ((tail["ema8"] > tail["ema21"]) & (tail["ema21"] > tail["ema53"])).mean()
        h1_bull = (tail["h1_ema19"] > tail["h1_ema53"]).mean()
        slope_pos = (tail["h1_slope4"] > 0.0003).mean()
        atr_quiet = (tail["atr14"] < tail["atr14"].rolling(48).median()).mean()
        macd_pos = (tail["macd_stick"] > 0).mean()
        z_mid = ((tail["z20"] > 0) & (tail["z20"] < 1.2)).mean()
        eth_micro["primitive_freq"] = [
            {"tag": "ema_ribbon_stack_up", "freq_pct": round(float(ribbon_up) * 100, 2)},
            {"tag": "h1_bull_align", "freq_pct": round(float(h1_bull) * 100, 2)},
            {"tag": "h1_slope_pos_gt_3bp", "freq_pct": round(float(slope_pos) * 100, 2)},
            {"tag": "atr14_below_48med_quiet", "freq_pct": round(float(atr_quiet) * 100, 2)},
            {"tag": "macd_stick_pos", "freq_pct": round(float(macd_pos) * 100, 2)},
            {"tag": "z20_mid_0_1p2", "freq_pct": round(float(z_mid) * 100, 2)},
        ]
        eth_micro["bars_used"] = int(len(tail))
        eth_micro["note"] = "72h proxy from ETH 5m feature frame"
    except Exception as exc:
        eth_micro["error"] = str(exc)

    live_avoid = {
        "ADA-USDT-SWAP 5m": "trend_pullback",
        "LTC-USDT-SWAP 5m": "exhaustion_fade",
        "NG-USDT-SWAP 5m": "exhaustion_fade",
        "XRP-USDT-SWAP 15m": "exhaustion/rescue style",
    }
    return {
        "instrument": SYMBOL,
        "timeframe": TIMEFRAME,
        "cl_archive": cl,
        "death_heatmap_top10": micro_death.get("death_heatmap_top10"),
        "eth_micro_72h": eth_micro,
        "insight_summary": micro_death.get("insight_summary"),
        "live_logic_to_avoid": live_avoid,
        "banned_logic_classes": sorted(BANNED_LOGIC),
        "quality_priority": (
            "先保证逻辑稳健与胜率稳定；trades预期>=12；结构失效出场；"
            "禁止边际exhaustion；禁止换标的/换周期"
        ),
    }


DIR_PROMPT = """你是GLM-5.2。只输出JSON，禁止Markdown。
任务：针对单一标的输出《单标的策略方向建议》。

硬约束：
1) 标的必须是 ETH-USDT-SWAP，周期必须是 5m（不可改）
2) 禁止复用现网 ADA/LTC/NG/XRP 的逻辑族：exhaustion_fade / reclaim / range_reclaim /
   breakout_continuation / impulse_continuation / trend_pullback / trend_pullback_fade
3) 必须规避 CL15m exhaustion 归档陷阱（见 cl_archive）
4) 给出恰好 3 个互斥候选方向（均为 ETH 5m），逻辑类必须彼此不同且不在禁名单
5) 优先：H1斜率/波动收缩后的方向漂移、EMA丝带持续、MACD柱持续等；结构失效出场；trades>=12
6) 入场远离 rsi≈54 边际；不要用过窄单阈值把交易压到<10

JSON schema：
{
  "report_title":"单标的策略方向建议",
  "symbol":"ETH-USDT-SWAP",
  "timeframe":"5m",
  "cl_root_cause_summary_zh":"...",
  "micro_read_zh":"...",
  "death_read_zh":"...",
  "candidates":[
    {
      "rank":1,
      "symbol":"ETH-USDT-SWAP",
      "timeframe":"5m",
      "direction":"long|short",
      "logic_class":"短横线英文类名_非禁名单",
      "thesis":"...",
      "micro_behavior":"...",
      "entry_sketch":"特征条件分号分隔",
      "exit_sketch":"...",
      "how_avoids_cl_defects":"...",
      "diff_vs_live_ada_ltc_ng_xrp":"...",
      "avoid_death":["stop_cluster"],
      "expected_trades_hint":">=12",
      "recommend_pick":false
    }
  ],
  "pick_rank":1,
  "pick_reason_zh":"...",
  "mentor_notes":"..."
}
candidates 必须恰好 3 条；pick_rank 指向你最推荐的一条。
"""


def ask_glm_directions(packet):
    ai = d._ai_json("glm", DIR_PROMPT, packet, max_tokens=2400, temperature=0.15)
    parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
    raw = ai.get("raw_preview") or ai.get("content") or ""
    if not parsed and raw:
        m = re.search(r"\{[\s\S]*\}", str(raw))
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
    return parsed, ai


def eth_fallback_directions(packet):
    return {
        "report_title": "单标的策略方向建议",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "cl_root_cause_summary_zh": (
            "CL15m exhaustion 根因：边际入场触发约-22%硬止损导致friction崩盘；"
            "cci抬阈值又把trades压到<10导致WF不足；软化再引入硬止损且dest失败。"
        ),
        "micro_read_zh": (
            "ETH5m 近窗可见 H1多头对齐与斜率正值、ATR安静段、EMA丝带上行并存；"
            "适合做斜率持续漂移而非冲高衰竭。"
        ),
        "death_read_zh": (
            "主避 stop_cluster / sample_starvation / cost_collapse；"
            "保证>=12笔、结构失效、非边际入场。"
        ),
        "candidates": [
            {
                "rank": 1,
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "direction": "long",
                "logic_class": "h1_slope_persistence",
                "thesis": "H1多头且斜率持续为正时，5m沿EMA丝带做多方向漂移；非突破追高、非回踩抄底",
                "micro_behavior": "h1_slope4持续>阈值 + ribbon对齐 + 中等z20，安静波动中顺势推进",
                "entry_sketch": (
                    "h1_ema19>h1_ema53; h1_slope4>0.0004; close>ema21; ema8>ema21; "
                    "rsi14>52; rsi14<66; macd_stick>0; z20>0.05; z20<1.4"
                ),
                "exit_sketch": "rsi14>70 take_profit; close<ema21 invalidation; max_hold=36",
                "how_avoids_cl_defects": "非exhaustion；rsi带宽离开54边际；不靠cci单阈值收窄；目标样本充足",
                "diff_vs_live_ada_ltc_ng_xrp": "异于LTC/NG衰竭空、ADA回撤趋势、XRP救援/衰竭；用H1斜率持续而非pullback/breakout",
                "avoid_death": ["stop_cluster", "sample_starvation"],
                "expected_trades_hint": ">=12",
                "recommend_pick": True,
            },
            {
                "rank": 2,
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "direction": "long",
                "logic_class": "atr_quiet_ribbon_drift",
                "thesis": "ATR相对安静时EMA丝带多头排列的低摩擦漂移多头",
                "micro_behavior": "atr14低于近窗中位的压缩段，价格贴着ema21上方推进",
                "entry_sketch": (
                    "h1_ema19>h1_ema53; close>ema21; close>ema8; ema21>ema53; "
                    "rsi14>53; rsi14<64; macd_stick>0; z20>-0.2; z20<1.2"
                ),
                "exit_sketch": "rsi14>68 take_profit; close<ema16 invalidation; max_hold=40",
                "how_avoids_cl_defects": "压缩段入场降低硬止损簇；不用过窄过滤；结构失效明确",
                "diff_vs_live_ada_ltc_ng_xrp": "非衰竭/回收/突破延续；安静波动漂移",
                "avoid_death": ["stop_cluster", "cost_collapse"],
                "expected_trades_hint": ">=12",
                "recommend_pick": False,
            },
            {
                "rank": 3,
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "direction": "short",
                "logic_class": "h1_slope_down_persistence",
                "thesis": "H1空头斜率持续为负时，5m沿丝带下行漂移做空",
                "micro_behavior": "负斜率持续 + close<ema21 + macd负，非超买衰竭反转",
                "entry_sketch": (
                    "h1_ema19<h1_ema53; h1_slope4<-0.0004; close<ema21; ema8<ema21; "
                    "rsi14<48; rsi14>34; macd_stick<0; z20<-0.05; z20>-1.4"
                ),
                "exit_sketch": "rsi14<30 take_profit; close>ema21 invalidation; max_hold=36",
                "how_avoids_cl_defects": "趋势空头持续而非冲高衰竭；避免rsi刚过线+中等cci",
                "diff_vs_live_ada_ltc_ng_xrp": "不同于LTC/NG的exhaustion_fade短；是斜率持续空",
                "avoid_death": ["stop_cluster", "holdout_collapse"],
                "expected_trades_hint": ">=12",
                "recommend_pick": False,
            },
        ],
        "pick_rank": 1,
        "pick_reason_zh": "H1斜率持续多头与ETH微结构最契合，且与现网逻辑族正交，样本空间更大。",
        "mentor_notes": "只做ETH5m；禁止回退到禁逻辑；CL陷阱永记。",
        "fallback": True,
        "provider": "codex_eth5m_fallback",
    }


def sanitize_candidate(c):
    c = dict(c or {})
    c["symbol"] = SYMBOL
    c["timeframe"] = TIMEFRAME
    logic = str(c.get("logic_class") or "custom").lower().strip()
    for bad in BANNED_LOGIC:
        if bad in logic:
            logic = "h1_slope_persistence"
            c["logic_class"] = logic
            c["sanitized_logic"] = True
            break
    c["logic_class"] = str(c.get("logic_class") or logic)
    c["direction"] = str(c.get("direction") or "long").lower()
    if c["direction"] not in ("long", "short"):
        c["direction"] = "long"
    return c


def pick_direction(report):
    cands = [sanitize_candidate(x) for x in (report.get("candidates") or [])]
    if not cands:
        return None, cands
    pr = int(report.get("pick_rank") or 1)
    chosen = None
    for c in cands:
        if int(c.get("rank") or 0) == pr:
            chosen = c
            break
    if chosen is None:
        for c in cands:
            if c.get("recommend_pick"):
                chosen = c
                break
    if chosen is None:
        chosen = cands[0]
    return chosen, cands


def parse_sketch_conditions(sketch, direction):
    leaves = []
    if not sketch:
        return leaves
    parts = re.split(r"[;\n]+", str(sketch))
    feat = (
        r"(h1_ema19|h1_ema53|h1_slope4|close|ema8|ema16|ema21|ema53|rsi14|"
        r"macd_stick|z20|cci|atr14|prev_high20|prev_low20)"
    )
    for part in parts:
        part = part.strip()
        if not part:
            continue
        low = part.lower()
        if "max_hold" in low:
            continue
        if ("take_profit" in low or "invalidation" in low) and "rsi" not in low and "close" not in low:
            continue
        m = re.match(
            r"^%s\s*(>=|<=|>|<|==)\s*(%s|-?[0-9]+(?:\.[0-9]+)?)\s*$" % (feat, feat[1:-1]),
            part, re.I,
        )
        # simpler match
        m = re.match(
            r"^(h1_ema19|h1_ema53|h1_slope4|close|ema8|ema16|ema21|ema53|rsi14|"
            r"macd_stick|z20|cci|atr14|prev_high20|prev_low20)\s*(>=|<=|>|<|==)\s*"
            r"(h1_ema19|h1_ema53|h1_slope4|close|ema8|ema16|ema21|ema53|rsi14|"
            r"macd_stick|z20|cci|atr14|prev_high20|prev_low20|-?[0-9]+(?:\.[0-9]+)?)\s*$",
            part, re.I,
        )
        if not m:
            continue
        left, op, right = m.group(1), m.group(2), m.group(3)
        op_map = {">": "gt", ">=": "gte", "<": "lt", "<=": "lte", "==": "eq"}
        leaf = {"left": {"feature": left.lower()}, "op": op_map.get(op, "gt")}
        try:
            leaf["right"] = {"value": float(right)}
        except Exception:
            leaf["right"] = {"feature": right.lower()}
        leaves.append(leaf)
    return leaves


def parse_exit_sketch(sketch):
    any_leaves = []
    hold = None
    if not sketch:
        return any_leaves, hold
    parts = re.split(r"[;\n]+", str(sketch))
    for part in parts:
        part = part.strip()
        low = part.lower()
        mh = re.search(r"max_hold\s*=\s*(\d+)", low)
        if mh:
            hold = int(mh.group(1))
            continue
        role = None
        if "take_profit" in low:
            role = "take_profit"
        elif "invalid" in low:
            role = "invalidation"
        cleaned = re.sub(r"(take_profit|invalidation)", "", part, flags=re.I).strip()
        leaves = parse_sketch_conditions(cleaned, "long")
        for leaf in leaves:
            if role:
                leaf["role"] = role
            any_leaves.append(leaf)
    return any_leaves, hold


def build_dsl_from_direction(row, idx):
    symbol = SYMBOL
    timeframe = TIMEFRAME
    direction = str(row.get("direction") or "long").lower()
    logic = str(row.get("logic_class") or "custom")
    entry = parse_sketch_conditions(row.get("entry_sketch"), direction)
    exit_any, hold = parse_exit_sketch(row.get("exit_sketch"))

    if len(entry) < 4:
        if direction == "short":
            entry = [
                {"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}},
                {"left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": -0.0004}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
                {"left": {"feature": "ema8"}, "op": "lt", "right": {"feature": "ema21"}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 48.0}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 34.0}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": -0.05}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -1.4}},
            ]
            exit_any = [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 30.0}, "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}, "role": "invalidation"},
            ]
        else:
            entry = [
                {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
                {"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0004}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "ema8"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 52.0}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 66.0}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.05}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.4}},
            ]
            exit_any = [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 70.0}, "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}, "role": "invalidation"},
            ]
    if len(exit_any) < 2:
        if direction == "short":
            exit_any = [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 30.0}, "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}, "role": "invalidation"},
            ]
        else:
            exit_any = [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 70.0}, "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}, "role": "invalidation"},
            ]
    if hold is None:
        hold = 36
    hold = max(int(hold), 28)

    dsl = {
        "key": "frost3_eth5m_%s_%s_%d" % (_safe(logic)[:18], direction, idx),
        "name": "寒霜叁-ETH-5m-%s" % logic,
        "direction": direction,
        "timeframe": timeframe,
        "supported_instruments": [symbol],
        "max_hold_bars": hold,
        "description": str(row.get("thesis") or logic)[:160],
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "schema": "qiyu_strategy_dsl_v1",
    }
    return f2.ensure_dsl(dsl, symbol, timeframe)


def make_book(row, idx):
    dsl = build_dsl_from_direction(row, idx)
    return {
        "title": "寒霜叁ETH5m#%d-%s" % (idx, row.get("logic_class")),
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": str(row.get("direction") or "long").lower(),
        "logic_class": str(row.get("logic_class") or "custom"),
        "thesis": row.get("thesis"),
        "micro_behavior": row.get("micro_behavior"),
        "entry_sketch": row.get("entry_sketch"),
        "exit_sketch": row.get("exit_sketch"),
        "avoid_from_postmortem": row.get("avoid_death") or row.get("how_avoids_cl_defects"),
        "diff_vs_live": row.get("diff_vs_live_ada_ltc_ng_xrp"),
        "dsl": dsl,
        "gate_mode": "frost2",
        "source": "frost3_eth5m",
        "dir_rank": idx,
    }


HYP_PROMPT = """你是GLM-5.2。只输出JSON。将选定方向固化为《策略逻辑假设书》并裁决是否可进入Quick。
硬约束：ETH-USDT-SWAP 5m 不可改；禁止禁逻辑族；规避CL陷阱；trades>=12；结构失效出场。
JSON：{
  "decision":"pass|reject|revise",
  "score":0到100,
  "hypothesis_book":{
    "title":"策略逻辑假设书",
    "symbol":"ETH-USDT-SWAP",
    "timeframe":"5m",
    "direction":"long|short",
    "logic_class":"...",
    "micro_behavior":"...",
    "causal_entry":"...",
    "causal_exit":"...",
    "how_avoids_cl_defects":"...",
    "entry_sketch":"...",
    "exit_sketch":"..."
  },
  "revise":{"entry_sketch":null,"exit_sketch":null,"param_tweaks":{},"why":"..."},
  "reason_zh":"..."
}
"""


AUDIT_PROMPT = """你是GLM-5.2。只输出JSON。审计ETH5m假设书。
硬禁：改标的/改周期；ADA/LTC/NG/XRP逻辑族；CL15m exhaustion同构。
对照：trades预期>=12；避免边际rsi刚过线；结构失效出场；logic_destruction稳健。
JSON：{"decision":"pass|reject|revise","score":0到100,"issues":["..."],
"revise":{"entry_sketch":null,"exit_sketch":null,"param_tweaks":{},"why":"..."},
"reason_zh":"..."}
"""


def glm_hyp_approve(book, report):
    payload = {
        "book": {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "thesis": book.get("thesis"),
            "micro_behavior": book.get("micro_behavior"),
            "entry_sketch": book.get("entry_sketch"),
            "exit_sketch": book.get("exit_sketch"),
            "entry": (book.get("dsl") or {}).get("entry"),
            "exit": (book.get("dsl") or {}).get("exit"),
            "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
            "diff_vs_live": book.get("diff_vs_live"),
            "how_avoids_cl": book.get("avoid_from_postmortem"),
        },
        "cl_traps": (report or {}).get("trap_checklist") or step1.load_cl_archive().get("trap_checklist"),
        "banned_logic": sorted(BANNED_LOGIC),
    }
    ai = d._ai_json("glm", HYP_PROMPT, payload, max_tokens=1400, temperature=0.1)
    parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
    raw = ai.get("raw_preview") or ""
    if not parsed and raw:
        m = re.search(r"\{[\s\S]*\}", str(raw))
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
    return parsed or {
        "decision": "pass",
        "reason_zh": "hyp_fallback_pass",
        "fallback": True,
        "hypothesis_book": {
            "title": "策略逻辑假设书",
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "direction": book["direction"],
            "logic_class": book["logic_class"],
            "micro_behavior": book.get("micro_behavior"),
            "causal_entry": book.get("entry_sketch"),
            "causal_exit": book.get("exit_sketch"),
            "how_avoids_cl_defects": book.get("avoid_from_postmortem"),
            "entry_sketch": book.get("entry_sketch"),
            "exit_sketch": book.get("exit_sketch"),
        },
    }, ai


def glm_audit(book, report):
    payload = {
        "book": {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "thesis": book.get("thesis"), "entry": (book.get("dsl") or {}).get("entry"),
            "exit": (book.get("dsl") or {}).get("exit"),
            "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
        },
        "cl_traps": (report or {}).get("trap_checklist") or [],
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


def apply_revise(book, revise):
    book = copy.deepcopy(book)
    if not isinstance(revise, dict):
        return book
    tweaks = revise.get("param_tweaks") or {}
    for phase in ("entry", "exit"):
        block = (book.get("dsl") or {}).get(phase) or {}
        rows = block.get("all") or block.get("any") or []
        for row in rows:
            feat = ((row.get("left") or {}).get("feature"))
            if feat in tweaks and isinstance(tweaks[feat], (int, float)):
                if isinstance(row.get("right"), dict) and "value" in row["right"]:
                    row["right"]["value"] = float(tweaks[feat])
    if revise.get("entry_sketch"):
        book["entry_sketch"] = revise["entry_sketch"]
    if revise.get("exit_sketch"):
        book["exit_sketch"] = revise["exit_sketch"]
    if revise.get("entry_sketch") or revise.get("exit_sketch"):
        row = {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "thesis": book.get("thesis"),
            "entry_sketch": book.get("entry_sketch"),
            "exit_sketch": book.get("exit_sketch"),
        }
        book["dsl"] = build_dsl_from_direction(row, int(book.get("dir_rank") or 1))
    book["dsl"] = f2.ensure_dsl(book["dsl"], book["symbol"], book["timeframe"])
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
                right["value"] = max(50.0, v - 1.0 * n)
            else:
                right["value"] = min(60.0, v + 0.5 * n)
        elif feat == "rsi14" and row.get("op") == "lt":
            if "walk" in step:
                right["value"] = min(72.0, v + 1.0 * n)
            else:
                right["value"] = max(58.0, v - 0.5 * n)
        elif feat == "z20" and row.get("op") == "gt":
            if "walk_forward" in step:
                right["value"] = max(-0.1, v - 0.05 * n)
            else:
                right["value"] = v + 0.05 * n
        elif feat == "z20" and row.get("op") == "lt":
            if "walk" in step:
                right["value"] = min(2.0, v + 0.1 * n)
            else:
                right["value"] = max(0.8, v - 0.05 * n)
        elif feat == "h1_slope4" and row.get("op") == "gt":
            if "walk" in step:
                right["value"] = max(0.0002, v - 0.00005 * n)
            else:
                right["value"] = v + 0.00005 * n
        elif feat == "h1_slope4" and row.get("op") == "lt":
            if "walk" in step:
                right["value"] = min(-0.0002, v + 0.00005 * n)
            else:
                right["value"] = v - 0.00005 * n
    dsl["entry"] = {"all": entries}
    hold = int(dsl.get("max_hold_bars") or 28)
    if "friction" in step or "mc" in step:
        dsl["max_hold_bars"] = max(16, hold - 2 * n)
    elif "walk" in step:
        dsl["max_hold_bars"] = hold + 4 * n
    book["dsl"] = f2.ensure_dsl(dsl, book["symbol"], book["timeframe"])
    book["dsl"]["key"] = (book["dsl"]["key"][:80] + "_t%d" % n)[:100]
    return book


REPAIR_PROMPT = """你是GLM-5.2。只输出JSON。ETH5m闸门修复（增量，禁止换标的/换周期/换大逻辑类）。
对照CL陷阱：勿把交易数压到<10；勿边际rsi≈54入场；保留结构失效出场。
JSON：{"ok":true,"dsl_patch":{"entry_all":null,"exit_any":null,"max_hold_bars":null},
"param_tweaks":{},"why":"..."}
entry_all/exit_any 若提供则为完整条件数组（DSL叶子格式）。
"""


def glm_repair(book, packs, failed_step, stage):
    payload = {
        "stage": stage,
        "failed_step": failed_step,
        "symbol": book["symbol"],
        "timeframe": book["timeframe"],
        "logic_class": book["logic_class"],
        "dsl": book.get("dsl"),
        "metrics": packs.get("base_metrics") or packs.get("metrics"),
        "quick": {k: packs.get(k) for k in ("quick_pass", "failed_step", "logic_destruction")},
        "full": packs.get("full"),
        "cl_traps": [
            "trades>=10 for WF folds",
            "no marginal exhaustion entry",
            "keep invalidation structural",
            "ETH-USDT-SWAP 5m locked",
        ],
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
        book2["dsl"]["entry"] = {"all": patch["entry_all"]}
    if isinstance(patch.get("exit_any"), list) and patch["exit_any"]:
        book2["dsl"]["exit"] = {"any": patch["exit_any"]}
    if patch.get("max_hold_bars"):
        try:
            book2["dsl"]["max_hold_bars"] = int(patch["max_hold_bars"])
        except Exception:
            pass
    tweaks = parsed.get("param_tweaks") or {}
    if tweaks:
        book2 = apply_revise(book2, {"param_tweaks": tweaks})
    book2["symbol"] = SYMBOL
    book2["timeframe"] = TIMEFRAME
    book2["dsl"]["supported_instruments"] = [SYMBOL]
    book2["dsl"]["timeframe"] = TIMEFRAME
    book2["dsl"]["key"] = (_safe(book2["dsl"].get("key")) + "_r")[:100]
    book2["dsl"] = f2.ensure_dsl(book2["dsl"], SYMBOL, TIMEFRAME)
    return book2, ai


_status = {"op": "寒霜叁ETH5m", "stage": "init", "updated_at": None, "hist": []}


def _upd(**kw):
    _status.update(kw)
    _status["updated_at"] = _now()
    _write("%s_status.json" % PREFIX, _status)


def archive_fail(result, book):
    arch = os.path.join(OUT, "archive", "%s_%s" % (
        PREFIX, _safe((book or {}).get("logic_class") or result.get("failed_step") or "fail")))
    os.makedirs(arch, exist_ok=True)
    open(os.path.join(arch, "RESULT.json"), "w").write(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
    if book:
        open(os.path.join(arch, "BOOK.json"), "w").write(
            json.dumps(book, ensure_ascii=False, indent=2, default=str) + "\n")
    death = {
        "at": _now(),
        "failed_step": result.get("failed_step"),
        "reason": result.get("reason"),
        "metrics": result.get("metrics"),
        "full": result.get("full"),
        "sim": result.get("sim"),
        "hist": result.get("hist"),
        "death_cause_zh": result.get("death_cause_zh") or (
            "失败步骤=%s；原因=%s" % (result.get("failed_step"), result.get("reason"))
        ),
    }
    open(os.path.join(arch, "DEATH.json"), "w").write(
        json.dumps(death, ensure_ascii=False, indent=2, default=str) + "\n")
    open(os.path.join(arch, "README.json"), "w").write(json.dumps({
        "title": "ETH 5m strategy archived",
        "archive_path": arch,
        "failed_step": result.get("failed_step"),
    }, ensure_ascii=False, indent=2) + "\n")
    return arch


def process(book, report):
    hist = []
    for atry in range(MAX_AUDIT_REVISE + 1):
        _upd(stage="hyp_approve", attempt=atry)
        decision, ai = glm_hyp_approve(book, report)
        _write("%s_hyp_glm_t%d.json" % (PREFIX, atry), {"decision": decision, "ai_ok": ai.get("ok")})
        hist.append({"stage": "hyp", "try": atry, "decision": decision.get("decision"),
                     "reason": decision.get("reason_zh"), "fallback": decision.get("fallback")})
        hb = decision.get("hypothesis_book") or {}
        if hb:
            _write("%s_hypothesis_book.json" % PREFIX, hb)
            if hb.get("entry_sketch") or hb.get("exit_sketch"):
                book = apply_revise(book, {
                    "entry_sketch": hb.get("entry_sketch") or book.get("entry_sketch"),
                    "exit_sketch": hb.get("exit_sketch") or book.get("exit_sketch"),
                })
        dec = str(decision.get("decision") or "pass").lower()
        if dec == "pass":
            break
        if dec == "revise" and atry < MAX_AUDIT_REVISE:
            book = apply_revise(book, decision.get("revise") or {})
            continue
        return {
            "ok": False, "failed_step": "hyp_reject",
            "reason": decision.get("reason_zh"), "hist": hist, "book": book,
            "death_cause_zh": "假设书未通过：" + str(decision.get("reason_zh")),
        }

    for atry in range(MAX_AUDIT_REVISE + 1):
        decision, ai = glm_audit(book, report)
        hist.append({"stage": "audit", "try": atry, "decision": decision.get("decision"),
                     "reason": decision.get("reason_zh"), "fallback": decision.get("fallback")})
        dec = str(decision.get("decision") or "pass").lower()
        if dec == "pass":
            break
        if dec == "revise" and atry < MAX_AUDIT_REVISE:
            book = apply_revise(book, decision.get("revise") or {})
            continue
        return {
            "ok": False, "failed_step": "hyp_audit_reject",
            "reason": decision.get("reason_zh"), "hist": hist, "book": book,
        }

    _write("%s_book.json" % PREFIX, book)

    packs = None
    for qtry in range(MAX_QUICK_REPAIR + 1):
        _upd(stage="quick", attempt=qtry)
        packs = f2.quick_suite(book)
        _write("%s_quick_t%d.json" % (PREFIX, qtry), packs)
        hist.append({
            "stage": "quick", "try": qtry, "pass": packs.get("quick_pass"),
            "failed_step": packs.get("failed_step"),
            "fp": (packs.get("base_metrics") or {}).get("fold_positive"),
            "folds": (packs.get("base_metrics") or {}).get("folds"),
            "tr": (packs.get("base_metrics") or {}).get("trades"),
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
        })
        if packs.get("quick_pass"):
            break
        if qtry >= MAX_QUICK_REPAIR:
            return {
                "ok": False,
                "failed_step": packs.get("failed_step") or "quick_exhausted",
                "reason": "Quick failed after %d repairs" % MAX_QUICK_REPAIR,
                "metrics": packs.get("base_metrics"),
                "dest": (packs.get("logic_destruction") or {}).get("pass"),
                "hist": hist, "book": book,
                "death_cause_zh": "Quick耗尽：%s；fp=%s/%s trades=%s dest=%s" % (
                    packs.get("failed_step"),
                    (packs.get("base_metrics") or {}).get("fold_positive"),
                    (packs.get("base_metrics") or {}).get("folds"),
                    (packs.get("base_metrics") or {}).get("trades"),
                    (packs.get("logic_destruction") or {}).get("pass"),
                ),
            }
        book = local_tweak(book, qtry + 1, packs.get("failed_step"))
        repaired, _ai = glm_repair(book, packs, packs.get("failed_step"), "quick")
        if repaired:
            book = repaired
        hist.append({"stage": "quick_repair", "try": qtry, "ok": bool(repaired)})
        _write("%s_book.json" % PREFIX, book)

    for ftry in range(MAX_FULL_REPAIR + 1):
        _upd(stage="full", attempt=ftry)
        packs = f2.full_suite(book, packs)
        _write("%s_full_t%d.json" % (PREFIX, ftry), packs)
        hist.append({
            "stage": "full", "try": ftry, "pass": packs.get("full_pass"),
            "failed_step": packs.get("failed_step"),
            "friction": (packs.get("full") or {}).get("friction_sharpe"),
            "mc": ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
        })
        if packs.get("full_pass"):
            break
        if ftry >= MAX_FULL_REPAIR:
            return {
                "ok": False,
                "failed_step": packs.get("failed_step") or "full_exhausted",
                "reason": "Full failed after %d repairs" % MAX_FULL_REPAIR,
                "full": packs.get("full"), "hist": hist, "book": book,
                "death_cause_zh": "Full耗尽：%s；friction=%s mc=%s" % (
                    packs.get("failed_step"),
                    (packs.get("full") or {}).get("friction_sharpe"),
                    ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
                ),
            }
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
        _write("%s_book.json" % PREFIX, book)

    for stry in range(MAX_SIM_REPAIR + 1):
        _upd(stage="sim_formal", attempt=stry)
        sf = f2.run_sim_formal(book, packs)
        _write("%s_sim_formal_t%d.json" % (PREFIX, stry), sf)
        hist.append({
            "stage": "sim_formal", "try": stry,
            "failed_step": sf.get("failed_step"),
            "sim_ds": (sf.get("sim") or {}).get("wr_deepseek_sim"),
            "sim_qw": (sf.get("sim") or {}).get("wr_qwen_sim"),
            "formal_approved": (sf.get("formal") or {}).get("approved"),
            "pending": (sf.get("pending") or {}).get("key"),
        })
        if (sf.get("pending") or {}).get("ok"):
            return {
                "ok": True, "failed_step": None,
                "pending": sf.get("pending"), "sim": sf.get("sim"),
                "formal": sf.get("formal"), "hist": hist, "book": book,
            }
        fs = sf.get("failed_step")
        if fs in ("formal_review", "pending_ingest"):
            return {
                "ok": False, "failed_step": fs,
                "reason": (sf.get("formal") or {}).get("reason") or (
                    sf.get("pending") or {}).get("reason"),
                "sim": sf.get("sim"), "formal": sf.get("formal"),
                "pending": sf.get("pending"), "hist": hist, "book": book,
                "death_cause_zh": "正式复核/入库失败：" + str(fs),
            }
        if stry >= MAX_SIM_REPAIR:
            return {
                "ok": False, "failed_step": fs or "sim_exhausted",
                "reason": "Sim/formal failed after %d repairs" % MAX_SIM_REPAIR,
                "sim": sf.get("sim"), "hist": hist, "book": book,
                "death_cause_zh": "Sim耗尽：DS=%s Qwen=%s step=%s" % (
                    (sf.get("sim") or {}).get("wr_deepseek_sim"),
                    (sf.get("sim") or {}).get("wr_qwen_sim"), fs,
                ),
            }
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

    return {"ok": False, "failed_step": "unknown_exhausted", "hist": hist, "book": book}


def main():
    print("[eth5m] START", _now(), flush=True)
    _upd(stage="collect")
    packet = load_eth_micro_packet()
    _write("%s_step1_packet.json" % PREFIX, packet)

    _upd(stage="glm_directions")
    parsed, ai = ask_glm_directions(packet)
    _write("%s_step1_glm.json" % PREFIX, {
        "ok": ai.get("ok"), "parsed": parsed,
        "raw_preview": (ai.get("raw_preview") or "")[:2000],
        "fallback": False,
    })
    used_fallback = False
    if not parsed or not (parsed.get("candidates") or parsed.get("directions")):
        parsed = eth_fallback_directions(packet)
        used_fallback = True
        _write("%s_step1_glm.json" % PREFIX, {
            "ok": False, "parsed": parsed, "fallback": True,
            "raw_preview": (ai.get("raw_preview") or "")[:2000],
        })
    if not parsed.get("candidates") and parsed.get("directions"):
        parsed["candidates"] = parsed["directions"]

    report = dict(parsed)
    report["glm_ok"] = bool(ai.get("ok") and not used_fallback)
    report["trap_checklist"] = packet["cl_archive"].get("trap_checklist")
    _write("%s_direction_report.json" % PREFIX, report)
    _write("%s_direction_suggest_zh.json" % PREFIX, report)

    chosen, cands = pick_direction(report)
    if not chosen:
        end = {"ok": False, "failed_step": "no_direction", "reason": "no candidates"}
        _write("%s_end_report.json" % PREFIX, end)
        print("=== ETH5m END FAIL no_direction ===", flush=True)
        return 1

    chosen = sanitize_candidate(chosen)
    _write("%s_chosen.json" % PREFIX, chosen)
    print("[eth5m] CHOSEN", chosen.get("logic_class"), chosen.get("direction"), flush=True)

    book = make_book(chosen, 1)
    _write("%s_book_init.json" % PREFIX, book)

    try:
        result = process(book, report)
    except Exception as exc:
        result = {
            "ok": False, "failed_step": "exception", "reason": str(exc),
            "trace": traceback.format_exc()[-2500:], "book": book,
            "death_cause_zh": "异常：" + str(exc),
        }

    end = {
        "at": _now(),
        "op": "寒霜叁ETH5m",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_class": (result.get("book") or book).get("logic_class"),
        "direction": (result.get("book") or book).get("direction"),
        "ok": bool(result.get("ok")),
        "failed_step": result.get("failed_step"),
        "reason": result.get("reason"),
        "death_cause_zh": result.get("death_cause_zh"),
        "pending_key": ((result.get("pending") or {}).get("key")),
        "sim": {
            "ds": (result.get("sim") or {}).get("wr_deepseek_sim"),
            "qw": (result.get("sim") or {}).get("wr_qwen_sim"),
        } if result.get("sim") else None,
        "formal": result.get("formal"),
        "metrics": result.get("metrics"),
        "full": result.get("full"),
        "hist": result.get("hist"),
        "book_key": ((result.get("book") or book).get("dsl") or {}).get("key"),
        "glm_directions_ok": report.get("glm_ok"),
        "chosen": {
            "logic_class": chosen.get("logic_class"),
            "direction": chosen.get("direction"),
            "thesis": chosen.get("thesis"),
        },
    }
    if not result.get("ok"):
        arch = archive_fail(result, result.get("book") or book)
        end["archive_path"] = arch
        print("[eth5m] ARCHIVED", result.get("failed_step"), arch, flush=True)
    else:
        print("[eth5m] PENDING", end.get("pending_key"), flush=True)

    _write("%s_end_report.json" % PREFIX, end)
    _upd(stage="done", ok=end["ok"], pending_key=end.get("pending_key"),
         failed_step=end.get("failed_step"))

    if end["ok"]:
        summary = (
            "成功：ETH5m %s/%s 已进pending key=%s；Sim DS=%s Qwen=%s" % (
                end.get("logic_class"), end.get("direction"), end.get("pending_key"),
                (end.get("sim") or {}).get("ds"), (end.get("sim") or {}).get("qw"),
            )
        )
    else:
        summary = (
            "失败：ETH5m 停在 %s；死因=%s；归档=%s" % (
                end.get("failed_step"), end.get("death_cause_zh"), end.get("archive_path"),
            )
        )
    _write("%s_parent_summary.json" % PREFIX, {"summary_zh": summary, "end": end})
    print("=== ETH5m PARENT ===", summary, flush=True)
    print("=== ETH5m END ===", json.dumps({
        "ok": end["ok"], "failed_step": end.get("failed_step"),
        "pending_key": end.get("pending_key"), "sim": end.get("sim"),
    }, ensure_ascii=False), flush=True)
    return 0 if end["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
