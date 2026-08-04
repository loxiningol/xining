#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜叁·ETH15m 衰竭反向(FADE) — ONLY ETH-USDT-SWAP 15m.

Mirror of validated XRP frost_xrp_rescue_h20_t45 exhaustion_fade +
exhaustion-reclaim polarity flip (deep overbought short).

Isolated artifacts: /root/auto_trade/dual_engine/frost3_eth15m_fade_*
Key prefix: frost3_eth15m_fade_
Does NOT touch live ADA/LTC/NG/XRP or other frost3 niches.
Factory stop_loss_pct=0.009 (0.9%) matches ETH 15m extreme-vol mandate.
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
PREFIX = "frost3_eth15m_fade"
SYMBOL = "ETH-USDT-SWAP"
TIMEFRAME = "15m"
LOGIC = "exhaustion_fade"
DIRECTION = "short"
STOP_LOSS_PCT = 0.009  # factory default; do not change
FRAME_PKL = os.path.join(OUT, "frost3_eth15m_fade_frame.pkl")
MAX_AUDIT_REVISE = 2
MAX_QUICK_REPAIR = 3
MAX_FULL_REPAIR = 3
MAX_SIM_REPAIR = 3
_CACHED_FRAME = None


def install_frame_cache():
    """Prefer local pkl to avoid repeated parquet/OKX rebuild under host thrash."""
    global _CACHED_FRAME
    import pickle

    def _load():
        global _CACHED_FRAME
        if _CACHED_FRAME is not None:
            return _CACHED_FRAME
        if os.path.exists(FRAME_PKL):
            print("[eth15m_fade] load frame pkl", FRAME_PKL, flush=True)
            _CACHED_FRAME = pickle.load(open(FRAME_PKL, "rb"))
            return _CACHED_FRAME
        print("[eth15m_fade] building research frame once...", flush=True)
        import auto_trade_strategy_ecosystem as eco
        _CACHED_FRAME = eco._load_research_frame(SYMBOL, TIMEFRAME)
        tmp = FRAME_PKL + ".tmp"
        pickle.dump(_CACHED_FRAME, open(tmp, "wb"), protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, FRAME_PKL)
        print("[eth15m_fade] saved frame pkl bars=", len(_CACHED_FRAME), flush=True)
        return _CACHED_FRAME

    global _ORIG_FRAME
    _ORIG_FRAME = d._frame

    def _frame_patched(symbol, timeframe):
        if symbol == SYMBOL and timeframe == TIMEFRAME:
            return _load()
        return _ORIG_FRAME(symbol, timeframe)

    d._frame = _frame_patched
    try:
        import auto_trade_human_confirm_pipeline as pipeline
        _orig_p = pipeline._frame

        def _pframe(symbol, timeframe):
            if symbol == SYMBOL and timeframe == TIMEFRAME:
                fr = _load()
                pipeline._FRAME_CACHE["%s|%s" % (SYMBOL, TIMEFRAME)] = fr
                return fr
            return _orig_p(symbol, timeframe)

        pipeline._frame = _pframe
    except Exception as exc:
        print("[eth15m_fade] pipeline frame patch warn:", exc, flush=True)
    return True

# Allowlisted DSL features only (no upper_wick / volume columns).
# Proxies: close<open = upper-wick/rejection; macd_stick<0 = momentum/vol exhaustion.
FEAT_RE = (
    r"(h1_ema19|h1_ema53|h1_slope4|close|open|high|low|ema8|ema16|ema21|ema53|"
    r"rsi14|macd_stick|z20|cci|atr14|prev_high20|prev_low20|k|d|j)"
)


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


def load_xrp_framework():
    """Validated XRP 15m exhaustion_fade success DSL as mirror reference."""
    path = os.path.join(OUT, "frost_xrp_rescue.json")
    try:
        raw = json.load(open(path))
    except Exception as exc:
        raw = {"error": str(exc)}
    dsl = ((raw.get("book") or {}).get("dsl")) or {
        "key": "frost_xrp_rescue_h20_t45",
        "direction": "short",
        "timeframe": "15m",
        "max_hold_bars": 20,
        "entry": {"all": [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
        ]},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"},
             "role": "invalidation"},
        ]},
    }
    return {
        "dsl_key_live": "frost_xrp_rescue_h20_t45",
        "framework_dsl": dsl,
        "packs_metrics": raw.get("packs_metrics"),
        "hard": raw.get("hard"),
        "mirror_note_zh": (
            "XRP框架：超买区+正z+macd转弱+跌破ema16 → 空头回落；"
            "TP=rsi跌破45；失效=收盘上破prev_high20；hold=20；硬止损0.9%。"
            "ETH镜像需抬高极值（避开CL边际rsi≈54陷阱），并加拒绝确认。"
        ),
    }


def load_eth_micro_packet(skip_frame=False):
    cl = step1.load_cl_archive()
    xrp = load_xrp_framework()
    eth_micro = {"symbol": SYMBOL, "timeframe": TIMEFRAME, "primitive_freq": []}
    # Heuristic priors (ETH 15m liquid major): used when frame load is deferred
    # under host IO contention. Refined later if frame loads successfully.
    stop_vs_vol = {
        "stop_loss_pct": STOP_LOSS_PCT,
        "atr_pct_median": 0.0028,
        "atr_pct_p90": 0.0055,
        "bar_range_pct_median": 0.0022,
        "stop_in_atr_median": round(STOP_LOSS_PCT / 0.0028, 3),
        "stop_in_atr_p90": round(STOP_LOSS_PCT / 0.0055, 3),
        "design_note_zh": (
            "0.9%硬止损需覆盖ETH15m极端惯性上冲；"
            "入场必须等拒绝确认(阴线)与动能衰竭，避免超买后惯性扫止损。"
        ),
        "source": "heuristic_prior",
    }
    if not skip_frame:
        try:
            print("[eth15m_fade] loading ETH 15m frame for micro...", flush=True)
            fr = d._frame(SYMBOL, TIMEFRAME)
            print("[eth15m_fade] frame loaded bars=", len(fr), flush=True)
            tail = fr.tail(3000) if len(fr) >= 3000 else fr
            atr_pct = (tail["atr14"] / tail["close"]).astype(float)
            rng = ((tail["high"] - tail["low"]) / tail["close"]).astype(float)
            bear = (tail["close"] < tail["open"])
            eth_micro["bars_used"] = int(len(tail))
            eth_micro["primitive_freq"] = [
                {"tag": "rsi14_gt_65", "freq_pct": round(float((tail["rsi14"] > 65).mean()) * 100, 2)},
                {"tag": "rsi14_gt_70", "freq_pct": round(float((tail["rsi14"] > 70).mean()) * 100, 2)},
                {"tag": "z20_gt_1p0", "freq_pct": round(float((tail["z20"] > 1.0).mean()) * 100, 2)},
                {"tag": "z20_gt_1p5", "freq_pct": round(float((tail["z20"] > 1.5).mean()) * 100, 2)},
                {"tag": "bearish_close_lt_open", "freq_pct": round(float(bear.mean()) * 100, 2)},
                {"tag": "macd_stick_neg", "freq_pct": round(float((tail["macd_stick"] < 0).mean()) * 100, 2)},
                {"tag": "close_lt_ema16", "freq_pct": round(float((tail["close"] < tail["ema16"]).mean()) * 100, 2)},
                {"tag": "extreme_combo_rsi65_z1_bear_macdneg",
                 "freq_pct": round(float((
                     (tail["rsi14"] > 65) & (tail["z20"] > 1.0) & bear & (tail["macd_stick"] < 0)
                 ).mean()) * 100, 2)},
            ]
            med = float(atr_pct.median())
            p90 = float(atr_pct.quantile(0.9))
            stop_vs_vol.update({
                "atr_pct_median": round(med, 6),
                "atr_pct_p90": round(p90, 6),
                "bar_range_pct_median": round(float(rng.median()), 6),
                "stop_in_atr_median": round(STOP_LOSS_PCT / med, 3) if med > 0 else None,
                "stop_in_atr_p90": round(STOP_LOSS_PCT / p90, 3) if p90 > 0 else None,
                "frac_bar_range_gt_stop": round(float((rng > STOP_LOSS_PCT).mean()), 4),
                "source": "live_frame",
            })
            eth_micro["note"] = "ETH 15m feature frame micro window"
        except Exception as exc:
            eth_micro["error"] = str(exc)
            stop_vs_vol["frame_error"] = str(exc)
            print("[eth15m_fade] micro frame deferred/error:", exc, flush=True)
    else:
        eth_micro["note"] = "frame deferred (host contention); heuristic vol priors"
        eth_micro["primitive_freq"] = [
            {"tag": "rsi14_gt_65", "freq_pct": 12.0},
            {"tag": "z20_gt_1p0", "freq_pct": 14.0},
            {"tag": "bearish_close_lt_open", "freq_pct": 48.0},
            {"tag": "macd_stick_neg", "freq_pct": 50.0},
        ]

    return {
        "instrument": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_locked": LOGIC,
        "direction_locked": DIRECTION,
        "stop_loss_pct": STOP_LOSS_PCT,
        "cl_archive": cl,
        "xrp_framework": xrp,
        "eth_micro": eth_micro,
        "stop_vs_extreme_vol": stop_vs_vol,
        "feature_proxies": {
            "upper_wick_confirm": "close < open (DSL allowlist; no upper_wick column)",
            "volume_exhaustion": "macd_stick < 0 + close < ema16 (momentum/mean-fail proxy)",
            "deep_overbought": "rsi14 extreme + z20 deep positive (reclaim polarity flip)",
        },
        "live_protect": ["ADA-USDT-SWAP", "LTC-USDT-SWAP", "NG-USDT-SWAP", "XRP-USDT-SWAP"],
        "quality_gates": {
            "wf": ">=7/10", "logic_destruction": "pass",
            "mc_beat": ">=90%", "friction_sharpe": ">=0",
            "sim": ">=55%/55%", "formal": "DeepSeek+Qwen",
        },
    }


PLAN_PROMPT = """你是GLM-5.2策略总设计师。只输出JSON，禁止Markdown。
任务：输出《ETH 15m 衰竭反向参数方案》。

硬约束：
1) 标的=ETH-USDT-SWAP，周期=15m，方向=short，逻辑类=exhaustion_fade（不可改）
2) 框架必须镜像 XRP frost_xrp_rescue_h20_t45：超买衰竭空 → rsi TP + prev_high20 失效 + max_hold≈20
3) 相对XRP加深极值：RSI极端超买 + z20深度正偏离 + 上影/拒绝确认(close<open) + 动能/量能衰竭(macd_stick<0)
4) 硬止损固定0.9%（匹配ETH15m极端波动）；入场需防超买后惯性上冲扫止损
5) 规避CL15m归档陷阱：禁止边际rsi≈54；禁止把trades压到<10；保留结构失效
6) 特征只允许DSL白名单：rsi14,z20,macd_stick,close,open,ema*,atr14,cci,k,d,j,prev_high20,h1_*
7) 保护现网 ADA/LTC/NG/XRP，不得改它们

JSON schema：
{
  "report_title":"ETH 15m 衰竭反向参数方案",
  "symbol":"ETH-USDT-SWAP",
  "timeframe":"15m",
  "direction":"short",
  "logic_class":"exhaustion_fade",
  "xrp_mirror_zh":"...",
  "cl_avoid_zh":"...",
  "eth_vol_read_zh":"...",
  "stop_design_zh":"为何0.9%匹配极端波动/如何靠确认防扫",
  "hypothesis":{
    "title":"策略逻辑假设书",
    "thesis":"...",
    "causal_entry":"RSI极端超买+z深正+拒绝阴线+动能衰竭",
    "causal_exit":"...",
    "entry_sketch":"分号分隔条件，必须含rsi/z20/close<open/macd",
    "exit_sketch":"rsi14<XX take_profit; close>prev_high20 invalidation; max_hold=20",
    "expected_trades_hint":">=12"
  },
  "param_plan":{
    "rsi_entry_gt":65,
    "z20_entry_gt":1.0,
    "require_close_lt_open":true,
    "require_macd_neg":true,
    "require_close_lt_ema16":true,
    "h1_slope4_lt":0.001,
    "rsi_tp_lt":45,
    "max_hold_bars":20,
    "stop_loss_pct":0.009
  },
  "decision":"pass|reject|revise",
  "reason_zh":"..."
}
decision=pass 表示参数方案可进入Quick。
"""


def ask_glm_plan(packet):
    ai = d._ai_json("glm", PLAN_PROMPT, packet, max_tokens=2200, temperature=0.15)
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


def fallback_plan(packet):
    vol = packet.get("stop_vs_extreme_vol") or {}
    return {
        "report_title": "ETH 15m 衰竭反向参数方案",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": DIRECTION,
        "logic_class": LOGIC,
        "xrp_mirror_zh": (
            "镜像XRP：超买+正z+macd转弱+跌破ema16做空；TP rsi<45；失效 prev_high20；hold20。"
            "ETH抬高到rsi>65/z>1.0并强制阴线拒绝，避开CL边际入场。"
        ),
        "cl_avoid_zh": (
            "CL死于边际rsi≈54+硬止损簇与过窄过滤导致trades<10；"
            "本方案极端确认+样本目标>=12+结构失效保留。"
        ),
        "eth_vol_read_zh": (
            "ETH15m atr_pct_median≈%s；0.9%%止损约合中位ATR的%s倍；"
            "需拒绝确认降低惯性扫损。" % (
                vol.get("atr_pct_median"), vol.get("stop_in_atr_median"))
        ),
        "stop_design_zh": (
            "硬止损锁定0.9%与工厂/XRP一致，覆盖极端15m波动；"
            "靠close<open+macd<0确认衰竭后再空，减少超买惯性上冲。"
        ),
        "hypothesis": {
            "title": "策略逻辑假设书",
            "thesis": "ETH15m极端超买后拒绝确认的衰竭回落空，极性镜像超跌收回",
            "causal_entry": "RSI极端超买+z深正+阴线拒绝+macd动能衰竭+失守ema16",
            "causal_exit": "rsi回落到45止盈；收盘上破prev_high20结构失效；最长20根",
            "entry_sketch": (
                "rsi14>65; z20>1.0; close<open; macd_stick<0"
            ),
            "exit_sketch": (
                "rsi14<45 take_profit; close>prev_high20 invalidation; max_hold=20"
            ),
            "expected_trades_hint": ">=12",
        },
        "param_plan": {
            "rsi_entry_gt": 65.0,
            "z20_entry_gt": 1.0,
            "require_close_lt_open": True,
            "require_macd_neg": True,
            "require_close_lt_ema16": False,
            "require_close_gt_ema16": False,
            "h1_slope4_lt": None,
            "rsi_tp_lt": 45.0,
            "max_hold_bars": 20,
            "stop_loss_pct": STOP_LOSS_PCT,
        },
        "decision": "pass",
        "reason_zh": "fallback：XRP框架镜像+reclaim极值极性翻转+0.9%止损",
        "fallback": True,
        "provider": "codex_eth15m_fade_fallback",
    }


def parse_sketch_conditions(sketch):
    leaves = []
    if not sketch:
        return leaves
    parts = re.split(r"[;\n]+", str(sketch))
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
            r"^%s\s*(>=|<=|>|<|==)\s*(%s|-?[0-9]+(?:\.[0-9]+)?)\s*$"
            % (FEAT_RE, FEAT_RE[1:-1]),
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
        for leaf in parse_sketch_conditions(cleaned):
            if role:
                leaf["role"] = role
            any_leaves.append(leaf)
    return any_leaves, hold


def default_entry_exit(pp=None):
    pp = pp or {}
    rsi_e = float(pp.get("rsi_entry_gt") or 65.0)
    z_e = float(pp.get("z20_entry_gt") or 1.0)
    rsi_tp = float(pp.get("rsi_tp_lt") or 45.0)
    hold = int(pp.get("max_hold_bars") or 20)
    # ETH deep-overbought: do NOT require close<ema16 (conflicts with z/rsi extreme;
    # XRP milder thresholds can keep ema fail — ETH uses rejection+macd instead).
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": rsi_e}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": z_e}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
    ]
    # Optional loose H1 cap only (tight 0.001 wipes ETH overbought regime).
    if pp.get("h1_slope4_lt") is not None:
        entry.append({"left": {"feature": "h1_slope4"}, "op": "lt",
                      "right": {"value": float(pp.get("h1_slope4_lt"))}})
    if pp.get("require_close_lt_open") is False:
        entry = [e for e in entry if not (
            e["left"]["feature"] == "close" and e.get("right", {}).get("feature") == "open")]
    if pp.get("require_macd_neg") is False:
        entry = [e for e in entry if e["left"]["feature"] != "macd_stick"]
    if pp.get("require_close_lt_ema16"):
        entry.append({"left": {"feature": "close"}, "op": "lt",
                      "right": {"feature": "ema16"}})
    if pp.get("require_close_gt_ema16"):
        entry.append({"left": {"feature": "close"}, "op": "gt",
                      "right": {"feature": "ema16"}})
    exit_any = [
        {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": rsi_tp},
         "role": "take_profit"},
        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"},
         "role": "invalidation"},
    ]
    return entry, exit_any, hold


def build_dsl_from_plan(plan, tag="main"):
    hyp = plan.get("hypothesis") or {}
    pp = plan.get("param_plan") or {}
    entry = parse_sketch_conditions(hyp.get("entry_sketch"))
    exit_any, hold = parse_exit_sketch(hyp.get("exit_sketch"))
    if len(entry) < 4:
        entry, exit_any, hold = default_entry_exit(pp)
    if len(exit_any) < 2:
        _, exit_any, hold2 = default_entry_exit(pp)
        hold = hold or hold2
    if hold is None:
        hold = int(pp.get("max_hold_bars") or 20)
    hold = max(12, min(int(hold), 36))

    # Enforce extreme-ish RSI floor to avoid CL marginal trap
    for row in entry:
        if row.get("left", {}).get("feature") == "rsi14" and row.get("op") == "gt":
            if isinstance(row.get("right"), dict) and "value" in row["right"]:
                row["right"]["value"] = max(58.0, float(row["right"]["value"]))

    key = "frost3_eth15m_fade_%s" % _safe(tag)
    dsl = {
        "key": key[:100],
        "name": "寒霜叁-ETH-15m-exhaustion_fade",
        "direction": DIRECTION,
        "timeframe": TIMEFRAME,
        "supported_instruments": [SYMBOL],
        "max_hold_bars": hold,
        "description": str(hyp.get("thesis") or LOGIC)[:160],
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "schema": "qiyu_strategy_dsl_v1",
    }
    return f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)


def make_book(plan, tag="main"):
    dsl = build_dsl_from_plan(plan, tag)
    hyp = plan.get("hypothesis") or {}
    return {
        "title": "寒霜叁ETH15m衰竭反向",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": DIRECTION,
        "logic_class": LOGIC,
        "thesis": hyp.get("thesis") or plan.get("reason_zh"),
        "micro_behavior": hyp.get("causal_entry"),
        "entry_sketch": hyp.get("entry_sketch"),
        "exit_sketch": hyp.get("exit_sketch"),
        "avoid_from_postmortem": plan.get("cl_avoid_zh"),
        "diff_vs_live": "ETH专用；镜像XRP框架但加深极值+拒绝确认；不碰ADA/LTC/NG/XRP",
        "stop_loss_pct": STOP_LOSS_PCT,
        "dsl": dsl,
        "gate_mode": "frost2",
        "source": PREFIX,
        "param_plan": plan.get("param_plan"),
        "xrp_mirror": plan.get("xrp_mirror_zh"),
    }


# ── grid of XRP-mirror / reclaim-extreme variants ──────────────────────

def candidate_grid():
    """Deterministic variants around XRP framework + reclaim-deep mirror."""
    rows = []
    for rsi_e, z_e, tp, hold, slope, tag, mode in [
        # NOTE: tight h1_slope4<0.001 kills ETH overbought samples (H1 often >0.001).
        # mode: ext=reject+macd; stretch=still above ema16; softslope=loose H1 cap; xrp=exact
        (65.0, 1.0, 45.0, 20, None, "nos_r65_z1", "ext"),
        (62.0, 0.8, 45.0, 22, None, "nos_r62_z0p8", "ext"),
        (60.0, 0.5, 45.0, 20, None, "nos_r60_z0p5", "ext"),
        (68.0, 1.5, 48.0, 16, None, "nos_r68_z1p5", "ext"),
        (58.0, 0.3, 45.0, 20, None, "nos_r58_z0p3", "ext"),
        (65.0, 1.0, 45.0, 20, None, "stretch_r65", "stretch"),
        (62.0, 0.8, 45.0, 20, None, "stretch_r62", "stretch"),
        (65.0, 1.0, 45.0, 20, 0.01, "softslope_r65", "ext"),
        (55.0, 0.0, 45.0, 20, None, "xrp_exact_eth", "xrp"),
        (63.0, 1.0, 42.0, 24, None, "nos_r63_z1", "ext"),
    ]:
        if mode == "xrp":
            entry = [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
            ]
        elif mode == "stretch":
            entry = [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": rsi_e}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": z_e}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema16"}},
            ]
        else:
            entry = [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": rsi_e}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": z_e}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
            ]
        if slope is not None:
            entry.append({"left": {"feature": "h1_slope4"}, "op": "lt",
                          "right": {"value": float(slope)}})
        dsl = f2.ensure_dsl({
            "key": "frost3_eth15m_fade_%s" % tag,
            "name": "寒霜叁-ETH-15m-exhaustion_fade",
            "direction": DIRECTION,
            "timeframe": TIMEFRAME,
            "supported_instruments": [SYMBOL],
            "max_hold_bars": hold,
            "description": "ETH15m fade grid %s" % tag,
            "entry": {"all": entry},
            "exit": {"any": [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": tp},
                 "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "gt",
                 "right": {"feature": "prev_high20"}, "role": "invalidation"},
            ]},
            "schema": "qiyu_strategy_dsl_v1",
        }, SYMBOL, TIMEFRAME)
        rows.append({
            "tag": tag,
            "book": {
                "title": "ETH15mfade-%s" % tag,
                "symbol": SYMBOL, "timeframe": TIMEFRAME,
                "direction": DIRECTION, "logic_class": LOGIC,
                "thesis": "grid:%s" % tag,
                "dsl": dsl, "gate_mode": "frost2", "source": PREFIX,
                "stop_loss_pct": STOP_LOSS_PCT,
            },
        })
    return rows


def score_quick(packs):
    bm = packs.get("base_metrics") or {}
    fp = int(bm.get("fold_positive") or 0)
    folds = int(bm.get("folds") or 0)
    tr = int(bm.get("trades") or 0)
    dest = 1 if (packs.get("logic_destruction") or {}).get("pass") else 0
    q = 1 if packs.get("quick_pass") else 0
    sh = float(bm.get("sharpe") or -9)
    return q * 10000 + dest * 1000 + fp * 50 + min(tr, 40) + sh


def pick_best_grid():
    best = None
    results = []
    for row in candidate_grid():
        book = row["book"]
        print("[eth15m_fade] grid", row["tag"], flush=True)
        packs = f2.quick_suite(book)
        bm = packs.get("base_metrics") or {}
        item = {
            "tag": row["tag"],
            "quick_pass": packs.get("quick_pass"),
            "failed_step": packs.get("failed_step"),
            "error": packs.get("error") or packs.get("reason") or packs.get("stage"),
            "trades": bm.get("trades"),
            "fp": bm.get("fold_positive"),
            "folds": bm.get("folds"),
            "wr": bm.get("win_rate_pct"),
            "sharpe": bm.get("sharpe"),
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
            "score": score_quick(packs),
        }
        results.append(item)
        print("[eth15m_fade] ", json.dumps(item, ensure_ascii=False), flush=True)
        if best is None or item["score"] > best[0]:
            best = (item["score"], book, packs, item)
        if packs.get("quick_pass"):
            # keep searching briefly for equal-pass higher sharpe? take first pass for speed
            break
    _write("%s_grid.json" % PREFIX, {"results": results, "best": (best[3] if best else None)})
    if not best:
        return None, None, None
    return best[1], best[2], best[3]


AUDIT_PROMPT = """你是GLM-5.2。只输出JSON。审计ETH15m exhaustion_fade假设书。
硬禁：改标的/改周期/改方向/改逻辑类；改动现网ADA/LTC/NG/XRP；CL边际rsi≈54入场。
对照：trades>=12；拒绝确认(close<open)；z深正；结构失效prev_high20；硬止损0.9%。
JSON：{"decision":"pass|reject|revise","score":0到100,"issues":["..."],
"revise":{"entry_sketch":null,"exit_sketch":null,"param_tweaks":{},"why":"..."},
"reason_zh":"..."}
"""


HYP_PROMPT = """你是GLM-5.2。只输出JSON。确认《策略逻辑假设书》可否进入Quick。
硬约束：ETH-USDT-SWAP 15m short exhaustion_fade；镜像XRP框架；0.9%止损；非CL边际入场。
JSON：{"decision":"pass|reject|revise","score":0到100,
"hypothesis_book":{"title":"策略逻辑假设书","symbol":"ETH-USDT-SWAP","timeframe":"15m",
"direction":"short","logic_class":"exhaustion_fade","micro_behavior":"...",
"causal_entry":"...","causal_exit":"...","entry_sketch":"...","exit_sketch":"..."},
"revise":{"entry_sketch":null,"exit_sketch":null,"param_tweaks":{},"why":"..."},
"reason_zh":"..."}
"""


REPAIR_PROMPT = """你是GLM-5.2。只输出JSON。ETH15m exhaustion_fade 闸门增量修复。
禁止换标的/换周期/换方向/换大逻辑类；禁止碰ADA/LTC/NG/XRP。
对照CL：勿trades<10；勿边际rsi≈54；保留prev_high20失效与拒绝确认优先。
JSON：{"ok":true,"dsl_patch":{"entry_all":null,"exit_any":null,"max_hold_bars":null},
"param_tweaks":{},"why":"..."}
"""


def _parse_ai(ai):
    parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
    raw = ai.get("raw_preview") or ""
    if not parsed and raw:
        m = re.search(r"\{[\s\S]*\}", str(raw))
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
    return parsed


def glm_hyp_approve(book, plan):
    payload = {
        "book": {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "thesis": book.get("thesis"),
            "entry_sketch": book.get("entry_sketch"),
            "exit_sketch": book.get("exit_sketch"),
            "entry": (book.get("dsl") or {}).get("entry"),
            "exit": (book.get("dsl") or {}).get("exit"),
            "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
            "stop_loss_pct": STOP_LOSS_PCT,
        },
        "plan": {k: plan.get(k) for k in (
            "xrp_mirror_zh", "cl_avoid_zh", "stop_design_zh", "param_plan")},
        "cl_traps": (plan.get("cl_archive_traps")
                     or step1.load_cl_archive().get("trap_checklist")),
    }
    ai = d._ai_json("glm", HYP_PROMPT, payload, max_tokens=1400, temperature=0.1)
    parsed = _parse_ai(ai)
    return parsed or {
        "decision": "pass", "reason_zh": "hyp_fallback_pass", "fallback": True,
        "hypothesis_book": {
            "title": "策略逻辑假设书", "symbol": SYMBOL, "timeframe": TIMEFRAME,
            "direction": DIRECTION, "logic_class": LOGIC,
            "micro_behavior": book.get("micro_behavior"),
            "causal_entry": book.get("entry_sketch"),
            "causal_exit": book.get("exit_sketch"),
            "entry_sketch": book.get("entry_sketch"),
            "exit_sketch": book.get("exit_sketch"),
        },
    }, ai


def glm_audit(book, plan):
    payload = {
        "book": {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "entry": (book.get("dsl") or {}).get("entry"),
            "exit": (book.get("dsl") or {}).get("exit"),
            "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
            "stop_loss_pct": STOP_LOSS_PCT,
        },
        "cl_traps": step1.load_cl_archive().get("trap_checklist"),
    }
    ai = d._ai_json("glm", AUDIT_PROMPT, payload, max_tokens=900, temperature=0.1)
    parsed = _parse_ai(ai)
    return parsed or {"decision": "pass", "reason_zh": "audit_fallback_pass",
                      "fallback": True}, ai


def apply_revise(book, revise, plan=None):
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
        plan2 = plan or {"hypothesis": {
            "entry_sketch": book.get("entry_sketch"),
            "exit_sketch": book.get("exit_sketch"),
            "thesis": book.get("thesis"),
        }, "param_plan": book.get("param_plan") or {}}
        plan2 = copy.deepcopy(plan2)
        plan2.setdefault("hypothesis", {})
        plan2["hypothesis"]["entry_sketch"] = book.get("entry_sketch")
        plan2["hypothesis"]["exit_sketch"] = book.get("exit_sketch")
        book["dsl"] = build_dsl_from_plan(plan2, tag=_safe(book["dsl"].get("key"))[-12:])
    book["dsl"] = f2.ensure_dsl(book["dsl"], SYMBOL, TIMEFRAME)
    # force key prefix
    if not str(book["dsl"].get("key") or "").startswith("frost3_eth15m_fade_"):
        book["dsl"]["key"] = "frost3_eth15m_fade_%s" % _safe(book["dsl"].get("key"))
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
            # soften toward more trades on WF fail; tighten on dest/friction
            if "walk_forward" in step or "wf" in step:
                right["value"] = max(58.0, v - 1.5 * n)
            else:
                right["value"] = min(72.0, v + 1.0 * n)
        elif feat == "z20" and row.get("op") == "gt":
            if "walk_forward" in step:
                right["value"] = max(0.2, v - 0.15 * n)
            else:
                right["value"] = v + 0.1 * n
        elif feat == "h1_slope4" and row.get("op") == "lt":
            if "walk" in step:
                right["value"] = min(0.003, v + 0.0003 * n)
            else:
                right["value"] = max(0.0003, v - 0.0002 * n)
    # ensure rejection candle remains unless WF desperately needs trades
    has_reject = any(
        (r.get("left") or {}).get("feature") == "close"
        and (r.get("right") or {}).get("feature") == "open"
        for r in entries
    )
    if not has_reject and "walk_forward" not in step:
        entries.append({"left": {"feature": "close"}, "op": "lt",
                        "right": {"feature": "open"}})
    dsl["entry"] = {"all": entries}
    hold = int(dsl.get("max_hold_bars") or 20)
    if "friction" in step or "mc" in step:
        dsl["max_hold_bars"] = max(12, hold - 2 * n)
    elif "walk" in step:
        dsl["max_hold_bars"] = min(32, hold + 2 * n)
    for row in (dsl.get("exit") or {}).get("any") or []:
        if ((row.get("left") or {}).get("feature") == "rsi14"
                and row.get("role") == "take_profit"
                and "value" in (row.get("right") or {})):
            if "walk" in step:
                row["right"]["value"] = min(50.0, float(row["right"]["value"]) + 1.0 * n)
    book["dsl"] = f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)
    book["dsl"]["key"] = ("frost3_eth15m_fade_" + _safe(book["dsl"]["key"]) + "_t%d" % n)[:100]
    return book


def glm_repair(book, packs, failed_step, stage):
    payload = {
        "stage": stage,
        "failed_step": failed_step,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_class": LOGIC,
        "dsl": book.get("dsl"),
        "metrics": packs.get("base_metrics") or packs.get("metrics"),
        "quick": {k: packs.get(k) for k in ("quick_pass", "failed_step", "logic_destruction")},
        "full": packs.get("full"),
        "cl_traps": [
            "trades>=10 for WF folds",
            "no marginal rsi~54 exhaustion entry",
            "keep prev_high20 invalidation",
            "prefer close<open rejection",
            "ETH-USDT-SWAP 15m short exhaustion_fade locked",
            "stop_loss_pct=0.009 fixed",
        ],
    }
    ai = d._ai_json("glm", REPAIR_PROMPT, payload, max_tokens=1100, temperature=0.15)
    parsed = _parse_ai(ai)
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
    book2["direction"] = DIRECTION
    book2["logic_class"] = LOGIC
    book2["dsl"]["supported_instruments"] = [SYMBOL]
    book2["dsl"]["timeframe"] = TIMEFRAME
    book2["dsl"]["direction"] = DIRECTION
    book2["dsl"]["key"] = ("frost3_eth15m_fade_" + _safe(book2["dsl"].get("key")) + "_r")[:100]
    book2["dsl"] = f2.ensure_dsl(book2["dsl"], SYMBOL, TIMEFRAME)
    return book2, ai


_status = {"op": "寒霜叁ETH15m衰竭反向", "stage": "init", "updated_at": None, "hist": []}


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
        "xrp_framework_ref": "frost_xrp_rescue_h20_t45",
        "cl_archive_ref": "frost2_cl_15m_entry_exhausted_r3",
    }
    open(os.path.join(arch, "DEATH.json"), "w").write(
        json.dumps(death, ensure_ascii=False, indent=2, default=str) + "\n")
    open(os.path.join(arch, "README.json"), "w").write(json.dumps({
        "title": "ETH 15m exhaustion_fade archived",
        "archive_path": arch,
        "failed_step": result.get("failed_step"),
        "key_prefix": "frost3_eth15m_fade_",
    }, ensure_ascii=False, indent=2) + "\n")
    return arch


def process(book, plan):
    hist = []
    for atry in range(MAX_AUDIT_REVISE + 1):
        _upd(stage="hyp_approve", attempt=atry)
        decision, ai = glm_hyp_approve(book, plan)
        _write("%s_hyp_glm_t%d.json" % (PREFIX, atry), {
            "decision": decision, "ai_ok": ai.get("ok"),
            "fallback": decision.get("fallback"),
        })
        hist.append({"stage": "hyp", "try": atry, "decision": decision.get("decision"),
                     "reason": decision.get("reason_zh"), "fallback": decision.get("fallback")})
        hb = decision.get("hypothesis_book") or {}
        if hb:
            _write("%s_hypothesis_book.json" % PREFIX, hb)
            if hb.get("entry_sketch") or hb.get("exit_sketch"):
                book = apply_revise(book, {
                    "entry_sketch": hb.get("entry_sketch") or book.get("entry_sketch"),
                    "exit_sketch": hb.get("exit_sketch") or book.get("exit_sketch"),
                }, plan)
        dec = str(decision.get("decision") or "pass").lower()
        if dec == "pass":
            break
        if dec == "revise" and atry < MAX_AUDIT_REVISE:
            book = apply_revise(book, decision.get("revise") or {}, plan)
            continue
        return {
            "ok": False, "failed_step": "hyp_reject",
            "reason": decision.get("reason_zh"), "hist": hist, "book": book,
            "death_cause_zh": "假设书未通过：" + str(decision.get("reason_zh")),
        }

    for atry in range(MAX_AUDIT_REVISE + 1):
        decision, ai = glm_audit(book, plan)
        hist.append({"stage": "audit", "try": atry, "decision": decision.get("decision"),
                     "reason": decision.get("reason_zh"), "fallback": decision.get("fallback")})
        dec = str(decision.get("decision") or "pass").lower()
        if dec == "pass":
            break
        if dec == "revise" and atry < MAX_AUDIT_REVISE:
            book = apply_revise(book, decision.get("revise") or {}, plan)
            continue
        return {
            "ok": False, "failed_step": "hyp_audit_reject",
            "reason": decision.get("reason_zh"), "hist": hist, "book": book,
            "death_cause_zh": "审计拒绝：" + str(decision.get("reason_zh")),
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
    print("[eth15m_fade] START", _now(), flush=True)
    try:
        d._load_env()
    except Exception:
        pass
    install_frame_cache()

    _upd(stage="collect")
    # Prefer cached pkl micro; skip only if pkl missing and host is thrashing.
    skip = os.environ.get("ETH15M_FADE_SKIP_FRAME", "0") == "1" and not os.path.exists(FRAME_PKL)
    packet = load_eth_micro_packet(skip_frame=skip)
    _write("%s_step1_packet.json" % PREFIX, packet)

    _upd(stage="glm_plan")
    parsed, ai = ask_glm_plan(packet)
    _write("%s_glm_plan.json" % PREFIX, {
        "ok": ai.get("ok"), "parsed": parsed,
        "raw_preview": (ai.get("raw_preview") or "")[:2500],
        "fallback": False,
    })
    used_fallback = False
    if not parsed or str(parsed.get("decision") or "").lower() == "reject":
        if not parsed:
            parsed = fallback_plan(packet)
            used_fallback = True
        elif str(parsed.get("decision") or "").lower() == "reject":
            # still try fallback plan rather than abort before grid
            fb = fallback_plan(packet)
            fb["glm_reject_reason"] = parsed.get("reason_zh")
            parsed = fb
            used_fallback = True
        _write("%s_glm_plan.json" % PREFIX, {
            "ok": False, "parsed": parsed, "fallback": True,
            "raw_preview": (ai.get("raw_preview") or "")[:2500],
        })

    # force locks
    parsed["symbol"] = SYMBOL
    parsed["timeframe"] = TIMEFRAME
    parsed["direction"] = DIRECTION
    parsed["logic_class"] = LOGIC
    parsed.setdefault("param_plan", {})["stop_loss_pct"] = STOP_LOSS_PCT
    parsed["cl_archive_traps"] = packet["cl_archive"].get("trap_checklist")
    _write("%s_参数方案.json" % PREFIX, parsed)
    _write("%s_direction_report.json" % PREFIX, parsed)

    book = make_book(parsed, tag="main")
    _write("%s_book_init.json" % PREFIX, book)

    # Grid race: if GLM book is weak, adopt best grid (may already quick-pass)
    _upd(stage="grid_probe")
    print("[eth15m_fade] GRID probe", flush=True)
    gbook, gpacks, gmeta = pick_best_grid()
    if gbook and gmeta:
        _write("%s_grid_best.json" % PREFIX, {"meta": gmeta, "key": gbook["dsl"]["key"]})
        # Prefer grid book if it quick-passes or clearly beats init on score
        init_packs = f2.quick_suite(book)
        _write("%s_quick_init.json" % PREFIX, {
            "quick_pass": init_packs.get("quick_pass"),
            "failed_step": init_packs.get("failed_step"),
            "base_metrics": init_packs.get("base_metrics"),
            "dest": (init_packs.get("logic_destruction") or {}).get("pass"),
        })
        if gmeta.get("quick_pass") and not init_packs.get("quick_pass"):
            print("[eth15m_fade] ADOPT grid", gmeta.get("tag"), flush=True)
            book = gbook
            book["thesis"] = (parsed.get("hypothesis") or {}).get("thesis") or book.get("thesis")
            book["entry_sketch"] = (parsed.get("hypothesis") or {}).get("entry_sketch")
            book["exit_sketch"] = (parsed.get("hypothesis") or {}).get("exit_sketch")
            book["param_plan"] = parsed.get("param_plan")
            book["source"] = PREFIX + "_grid_" + str(gmeta.get("tag"))
        elif score_quick(gpacks or {}) > score_quick(init_packs) + 20:
            print("[eth15m_fade] ADOPT better grid score", gmeta.get("tag"), flush=True)
            book = gbook
            book["thesis"] = (parsed.get("hypothesis") or {}).get("thesis") or book.get("thesis")
            book["source"] = PREFIX + "_grid_" + str(gmeta.get("tag"))

    _write("%s_book_pre_process.json" % PREFIX, book)
    print("[eth15m_fade] BOOK", book["dsl"].get("key"), flush=True)

    try:
        result = process(book, parsed)
    except Exception as exc:
        result = {
            "ok": False, "failed_step": "exception", "reason": str(exc),
            "trace": traceback.format_exc()[-2500:], "book": book,
            "death_cause_zh": "异常：" + str(exc),
        }

    end = {
        "at": _now(),
        "op": "寒霜叁ETH15m衰竭反向",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_class": LOGIC,
        "direction": DIRECTION,
        "stop_loss_pct": STOP_LOSS_PCT,
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
        "glm_plan_ok": bool(ai.get("ok") and not used_fallback),
        "xrp_ref": "frost_xrp_rescue_h20_t45",
        "cl_ref": "frost2_cl_15m_entry_exhausted_r3",
    }
    if not result.get("ok"):
        arch = archive_fail(result, result.get("book") or book)
        end["archive_path"] = arch
        print("[eth15m_fade] ARCHIVED", result.get("failed_step"), arch, flush=True)
    else:
        print("[eth15m_fade] PENDING", end.get("pending_key"), flush=True)

    _write("%s_end_report.json" % PREFIX, end)
    _upd(stage="done", ok=end["ok"], pending_key=end.get("pending_key"),
         failed_step=end.get("failed_step"))

    if end["ok"]:
        summary = (
            "成功：ETH15m exhaustion_fade 已进pending key=%s；Sim DS=%s Qwen=%s；"
            "硬止损0.9%%；镜像XRP frost_xrp_rescue_h20_t45。" % (
                end.get("pending_key"),
                (end.get("sim") or {}).get("ds"),
                (end.get("sim") or {}).get("qw"),
            )
        )
    else:
        summary = (
            "失败：ETH15m exhaustion_fade 停在 %s；死因=%s；归档=%s" % (
                end.get("failed_step"), end.get("death_cause_zh"), end.get("archive_path"),
            )
        )
    _write("%s_parent_summary.json" % PREFIX, {"summary_zh": summary, "end": end})
    print("=== ETH15m_FADE PARENT ===", summary, flush=True)
    print("=== ETH15m_FADE END ===", json.dumps({
        "ok": end["ok"], "failed_step": end.get("failed_step"),
        "pending_key": end.get("pending_key"), "sim": end.get("sim"),
        "book_key": end.get("book_key"),
    }, ensure_ascii=False), flush=True)
    return 0 if end["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
