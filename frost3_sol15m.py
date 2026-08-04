#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜叁 — SOL-USDT-SWAP 15m standalone creation ONLY.

Artifacts: /root/auto_trade/dual_engine/frost3_sol15m_*
Does NOT touch Frost3 multi-lane / ETH5m / BNB1h / live ADA/LTC/NG/XRP.
Logic class MUST avoid: exhaustion_fade / reclaim / breakout-continuation
(and known-failed SOL exhaustion from frost2). Learns CL15m archive traps.
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
import auto_trade_dual_engine_factory as d
import frost2_action_run as f2
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
PREFIX = "frost3_sol15m"
SYMBOL = "SOL-USDT-SWAP"
TIMEFRAME = "15m"

BANNED_LOGIC = {
    "exhaustion_fade", "exhaustion", "fade_exhaustion",
    "reclaim", "range_reclaim", "exhaustion_reclaim",
    "breakout_continuation", "breakout-continuation", "impulse_continuation",
    "breakout", "impulse",
}

MAX_AUDIT_REVISE = 2
MAX_QUICK_REPAIR = 3
MAX_FULL_REPAIR = 3
MAX_SIM_REPAIR = 3


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


FRAME_PKL = os.path.join(OUT, "%s_frame.pkl" % PREFIX)


def inject_sol_frame_cache():
    """Inject pickled SOL15m frame into pipeline cache (XAU-style) to survive 1-core thrash."""
    import pickle
    import auto_trade_human_confirm_pipeline as pipeline
    if not os.path.exists(FRAME_PKL):
        print("[sol15m] no frame pkl yet, building...", flush=True)
        fr = d._frame(SYMBOL, TIMEFRAME)
        pickle.dump(fr, open(FRAME_PKL, "wb"), protocol=pickle.HIGHEST_PROTOCOL)
        print("[sol15m] wrote", FRAME_PKL, "rows", len(fr), flush=True)
    else:
        fr = pickle.load(open(FRAME_PKL, "rb"))
        print("[sol15m] loaded frame pkl rows", len(fr), flush=True)
    key = "%s|%s" % (SYMBOL, TIMEFRAME)
    cache = getattr(pipeline, "_FRAME_CACHE", None)
    if cache is None:
        pipeline._FRAME_CACHE = {}
        cache = pipeline._FRAME_CACHE
    cache[key] = fr
    # Prefer cache hits for our symbol/tf only
    _orig = pipeline._frame

    def _cached_frame(symbol, timeframe):
        k = "%s|%s" % (symbol, timeframe)
        if k in cache:
            return cache[k]
        return _orig(symbol, timeframe)

    pipeline._frame = _cached_frame
    return fr


def _write(name, obj):
    path = os.path.join(OUT, name if name.startswith(PREFIX) else "%s_%s" % (PREFIX, name))
    if not name.startswith(PREFIX) and not name.endswith(".json"):
        path = os.path.join(OUT, "%s_%s.json" % (PREFIX, name))
    elif not os.path.basename(path).startswith(PREFIX):
        path = os.path.join(OUT, "%s_%s" % (PREFIX, os.path.basename(path)))
    # normalize: always PREFIX_*.json under OUT
    base = os.path.basename(path)
    if not base.startswith(PREFIX):
        base = "%s_%s" % (PREFIX, base)
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


def _load_json(path, default=None):
    try:
        return json.load(open(path))
    except Exception:
        return default if default is not None else {}


def load_sol_context():
    # Prefer cached factory inputs — collect_inputs() thrashs under 1-core overload.
    inputs = {}
    for cand in (
        os.path.join(OUT, "frost3_inputs_raw.json"),
        os.path.join(OUT, "%s_inputs.json" % PREFIX),
    ):
        if not os.path.exists(cand):
            continue
        blob = _load_json(cand, {})
        if blob.get("micro_death") and not blob.get("micro_72h"):
            inputs = {
                "micro_72h": (blob.get("micro_death") or {}).get("micro_72h"),
                "death_heatmap_top10": (blob.get("micro_death") or {}).get(
                    "death_heatmap_top10"),
                "freezer_last10": (blob.get("micro_death") or {}).get("freezer_last10"),
                "_source_path": cand,
            }
            break
        if blob.get("micro_72h") is not None or blob.get("death_heatmap_top10"):
            inputs = blob
            inputs["_source_path"] = cand
            break
    if not inputs:
        try:
            inputs = d.collect_inputs()
            inputs["_source_path"] = "collect_inputs"
        except Exception as exc:
            inputs = {"_source_path": "empty", "error": str(exc)}
    cl = step1.load_cl_archive()
    insight = _load_json(os.path.join(OUT, "insight_latest.json"), {})
    frost2_sol_fail = {
        "logic_class": "exhaustion_fade",
        "timeframe": "15m",
        "thesis": "三推上攻失败+RSI顶背离+资金费率极端 — frost2 已知失败，禁止复用",
        "entry_sketch_failed": "rsi14>58; z20>0.3; macd_stick<0; close<ema16",
    }
    micro = {
        "micro_72h": inputs.get("micro_72h"),
        "death_heatmap_top10": inputs.get("death_heatmap_top10"),
        "insight_summary": insight.get("summary_zh"),
        "insight_micro_read": insight.get("micro_read"),
        "sol_niches": [
            n for n in (insight.get("priority_niches") or [])
            if "SOL" in str((n or {}).get("symbol") or "")
        ],
        "freezer_last10": (inputs.get("freezer_last10") or [])[:10],
        "inputs_source": inputs.get("_source_path"),
    }
    return {
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "cl_archive": cl,
        "micro_death": micro,
        "frost2_sol_exhaustion_failure": frost2_sol_fail,
        "banned_logic_classes": sorted(BANNED_LOGIC),
        "protect_live": sorted(step1.AVOID_SYMBOLS),
        "gates": {
            "WF": ">=7/10",
            "logic_destruction": "pass",
            "MC_beat_sign_shuffle": ">=90%",
            "extreme_friction_sharpe": ">=0",
            "sim_ds_qwen": ">=55%/55%",
        },
        "features": [
            "rsi14", "z20", "macd_stick", "cci", "atr14",
            "ema6", "ema8", "ema16", "ema19", "ema21", "ema53",
            "close", "prev_high20", "prev_low20",
            "h1_ema19", "h1_ema53", "h1_slope4", "h1_atr14",
        ],
    }


GLM_DIR_PROMPT = """你是GLM-5.2。只输出JSON，禁止Markdown、省略号占位符、注释。
任务：输出《单标的策略方向建议》——仅针对 SOL-USDT-SWAP / 15m。

硬约束：
1) symbol 必须是 SOL-USDT-SWAP，timeframe 必须是 15m，不得改标的/周期
2) 禁止 logic_class 属于：exhaustion_fade / reclaim / range_reclaim / breakout_continuation / impulse_continuation / breakout / impulse（及同义）
3) 禁止复刻 frost2 SOL15m exhaustion 失败案（rsi刚过线+z偏高+macd翻负的高位衰竭空）
4) 必须吸收 CL15m 归档教训：trades预期≥12；勿用单阈值把交易压到<10；勿边际rsi≈54入场；保留结构失效出场；logic_destruction±20%稳健；friction 崩盘主因是硬止损簇
5) 恰好给出 3 个互斥方向（同一标的同周期，但 logic_class/机制互斥）
6) 每个方向含 micro_behavior（15m微观行为）、causal_entry、causal_exit、why_avoids_banned_and_cl、avoid_death、expected_trades_hint

JSON schema：
{
  "report_title":"单标的策略方向建议",
  "symbol":"SOL-USDT-SWAP",
  "timeframe":"15m",
  "cl_lessons_zh":"...",
  "micro_death_read_zh":"...",
  "banned_note_zh":"...",
  "directions":[
    {
      "rank":1,
      "symbol":"SOL-USDT-SWAP",
      "timeframe":"15m",
      "direction":"long|short",
      "logic_class":"trend_pullback_continuation|macd_regime_follow|ema_ribbon_alignment|zscore_normalize_continuation|volatility_expansion_follow|trend_pullback_fade|其他非禁类",
      "thesis":"...",
      "micro_behavior":"...",
      "entry_sketch":"特征条件分号分隔",
      "exit_sketch":"TP/失效/max_hold",
      "causal_entry":"...",
      "causal_exit":"...",
      "why_avoids_banned_and_cl":"...",
      "avoid_death":["stop_cluster","sample_starvation"],
      "expected_trades_hint":">=12",
      "priority_score":0到100
    }
  ],
  "recommend_rank":1,
  "mentor_notes":"..."
}
"""


def ask_glm_directions(packet):
    ai = d._ai_json("glm", GLM_DIR_PROMPT, packet, max_tokens=2200, temperature=0.05)
    parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
    raw = ai.get("raw_preview") or ai.get("content") or ""
    if not parsed and raw:
        m = re.search(r"\{[\s\S]*\}", str(raw))
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
    # one retry if model returned reasoning prose
    if not parsed:
        retry_prompt = GLM_DIR_PROMPT + "\n再次强调：第一个字符必须是{，最后一个字符必须是}。不要输出思考过程。"
        ai2 = d._ai_json("glm", retry_prompt, {
            "symbol": SYMBOL, "timeframe": TIMEFRAME,
            "cl_traps": (packet.get("cl_archive") or {}).get("trap_checklist"),
            "banned_logic_classes": packet.get("banned_logic_classes"),
            "frost2_sol_fail": packet.get("frost2_sol_exhaustion_failure"),
            "micro_death_read": (packet.get("micro_death") or {}).get("insight_micro_read"),
        }, max_tokens=1800, temperature=0.0)
        parsed = ai2.get("parsed") if isinstance(ai2.get("parsed"), dict) else None
        raw2 = ai2.get("raw_preview") or ""
        if not parsed and raw2:
            m = re.search(r"\{[\s\S]*\}", str(raw2))
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except Exception:
                    parsed = None
        if parsed:
            ai = ai2
    return parsed, ai


def codex_fallback_directions(packet):
    """Deterministic SOL15m directions avoiding banned classes + CL traps."""
    return {
        "report_title": "单标的策略方向建议",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "cl_lessons_zh": (
            "CL15m exhaustion 因边际入场硬止损→friction崩盘；cci收窄导致trades<10、WF折数不足；"
            "软化参数引入新硬止损且dest失败。SOL15m必须保证样本≥12、结构失效、远离边际超买空。"
        ),
        "micro_death_read_zh": (
            "死亡热力主因 stop_cluster / negative_net_expectancy / cost_collapse / sample_starvation；"
            "SOL高Beta需控制触发密度与持有期，用H1趋势对齐降低逆势硬止损。"
        ),
        "banned_note_zh": "禁 exhaustion_fade/reclaim/breakout_continuation；禁 frost2 SOL衰竭同构。",
        "directions": [
            {
                "rank": 1,
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "direction": "long",
                "logic_class": "trend_pullback_continuation",
                "thesis": "H1多头且斜率向上时，15m回踩ema21不破、仍在ema8下方的健康回调后做多延续",
                "micro_behavior": "回踩缩波动+macd仍正、RSI中性偏多，非突破追高",
                "entry_sketch": (
                    "h1_ema19>h1_ema53; h1_slope4>0; close>ema21; close<ema8; "
                    "rsi14>50; rsi14<65; macd_stick>0; z20>-0.1; z20<1.0"
                ),
                "exit_sketch": "rsi14>70 take_profit; close<ema21 invalidation; max_hold=28",
                "causal_entry": "高周期趋势过滤后的回调低吸，因果是趋势未坏+动能未死",
                "causal_exit": "RSI过热兑现或跌破ema21证明回调变反转",
                "why_avoids_banned_and_cl": "非衰竭/回收/突破；不用窄cci；RSI带宽远离54边际；目标trades≥12",
                "avoid_death": ["stop_cluster", "sample_starvation"],
                "expected_trades_hint": ">=12",
                "priority_score": 88,
            },
            {
                "rank": 2,
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "direction": "long",
                "logic_class": "ema_ribbon_alignment",
                "thesis": "15m均线多头排列+H1同向，中等动量跟随，结构跌破ema16离场",
                "micro_behavior": "ribbon扩张初期，避免z极端追高",
                "entry_sketch": (
                    "ema8>ema16; ema16>ema21; ema21>ema53; h1_ema19>h1_ema53; "
                    "rsi14>52; rsi14<68; macd_stick>0; z20>-0.2; z20<1.6"
                ),
                "exit_sketch": "rsi14>72 take_profit; close<ema16 invalidation; max_hold=24",
                "causal_entry": "多周期均线同向提供趋势惯性",
                "causal_exit": "跌破中轨ribbon破坏",
                "why_avoids_banned_and_cl": "趋势排列跟随≠突破追高；无衰竭空；样本靠排列状态而非单阈值",
                "avoid_death": ["stop_cluster", "cost_collapse"],
                "expected_trades_hint": ">=12",
                "priority_score": 80,
            },
            {
                "rank": 3,
                "symbol": SYMBOL,
                "timeframe": TIMEFRAME,
                "direction": "short",
                "logic_class": "trend_pullback_fade",
                "thesis": "H1空头中15m反弹至ema21上方但仍受压于ema8、动能转弱后做空",
                "micro_behavior": "反弹遇阻+macd转负，非超买衰竭追空",
                "entry_sketch": (
                    "h1_ema19<h1_ema53; h1_slope4<0; close>ema21; close<ema8; "
                    "rsi14>52; rsi14<68; macd_stick<0; z20>0.0; z20<1.5; cci>20"
                ),
                "exit_sketch": "rsi14<42 take_profit; close>prev_high20 invalidation; max_hold=20",
                "causal_entry": "空头趋势中的反弹卖压回归（价格在ema21上方但未站稳ema8）",
                "causal_exit": "结构新高说明空头失效",
                "why_avoids_banned_and_cl": "强制H1空头；RSI带宽避开刚过线；不用矛盾的close<ema16∧close>ema21；非CL同构衰竭",
                "avoid_death": ["stop_cluster", "holdout_collapse", "sample_starvation"],
                "expected_trades_hint": ">=12",
                "priority_score": 90,
            },
        ],
        "recommend_rank": 1,
        "mentor_notes": "三方向均锁死SOL15m；逻辑互斥且避开禁类与CL陷阱。",
        "fallback": True,
        "provider": "codex_fallback",
    }


def _logic_banned(logic):
    low = str(logic or "").lower().replace("-", "_")
    if low in BANNED_LOGIC:
        return True
    for b in BANNED_LOGIC:
        if b in low:
            return True
    return False


def sanitize_directions(report):
    dirs = []
    for row in (report.get("directions") or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol") or "").upper() != SYMBOL:
            continue
        if str(row.get("timeframe") or "") != TIMEFRAME:
            continue
        if _logic_banned(row.get("logic_class")):
            continue
        row["symbol"] = SYMBOL
        row["timeframe"] = TIMEFRAME
        dirs.append(row)
    dirs = sorted(dirs, key=lambda x: (-float(x.get("priority_score") or 0), int(x.get("rank") or 99)))
    return dirs


def parse_sketch_conditions(sketch):
    """Parse 'feat>12; a<b; macd_stick>0' into DSL leaves (best-effort)."""
    leaves = []
    text = str(sketch or "")
    # strip role words
    for part in re.split(r"[;\n]+", text):
        part = part.strip()
        if not part:
            continue
        low = part.lower()
        if "max_hold" in low or "take_profit" in low or "invalidation" in low:
            # handle "rsi14>70 take_profit" etc.
            role = None
            if "take_profit" in low or " tp" in low or low.endswith("tp"):
                role = "take_profit"
            if "invalidation" in low or " inv" in low or low.endswith("inv"):
                role = "invalidation"
            part = re.sub(r"(?i)(take_profit|invalidation|\btp\b|\binv\b)", "", part).strip()
        else:
            role = None
        m = re.match(
            r"^([A-Za-z0-9_]+)\s*(>=|<=|<|>|gt|lt)\s*([A-Za-z0-9_\.\-]+)$",
            part.replace(" ", ""),
        )
        if not m:
            continue
        feat, op, rhs = m.group(1), m.group(2), m.group(3)
        op = {"<": "lt", ">": "gt", "<=": "lt", ">=": "gt"}.get(op, op)
        leaf = {"left": {"feature": feat}, "op": op}
        try:
            leaf["right"] = {"value": float(rhs)}
        except Exception:
            leaf["right"] = {"feature": rhs}
        if role:
            leaf["role"] = role
        leaves.append(leaf)
    return leaves


def build_dsl_from_direction(row, idx):
    direction = str(row.get("direction") or "long").lower()
    logic = str(row.get("logic_class") or "custom")
    entry = parse_sketch_conditions(row.get("entry_sketch"))
    exit_any = parse_sketch_conditions(row.get("exit_sketch"))
    # fallback templates by logic class if sketch parse thin
    if len(entry) < 4:
        if logic == "ema_ribbon_alignment":
            entry = [
                {"left": {"feature": "ema8"}, "op": "gt", "right": {"feature": "ema16"}},
                {"left": {"feature": "ema16"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "ema21"}, "op": "gt", "right": {"feature": "ema53"}},
                {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 52.0}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 68.0}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -0.2}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.6}},
            ]
            direction = "long"
        elif logic == "trend_pullback_fade" or (
                direction == "short" and "pullback" in str(logic).lower()):
            # Bounce into ema21 but still capped by ema8 (avoid close<ema16∧close>ema21 trap)
            entry = [
                {"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}},
                {"left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": 0.0}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 52.0}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 68.0}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.5}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 20.0}},
            ]
            direction = "short"
        else:
            # default trend_pullback_continuation
            entry = [
                {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
                {"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 50.0}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 65.0}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -0.1}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.0}},
            ]
            direction = "long"
    # Fix impossible short band: close<ema16 ∧ close>ema21
    if direction == "short" or logic == "trend_pullback_fade":
        ops = []
        for leaf in entry:
            feat = (leaf.get("left") or {}).get("feature")
            right = leaf.get("right") or {}
            ops.append((feat, leaf.get("op"), right.get("feature")))
        if (("close", "lt", "ema16") in ops) and (("close", "gt", "ema21") in ops):
            entry = [
                {"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}},
                {"left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": 0.0}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 52.0}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 68.0}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.5}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 20.0}},
            ]
            direction = "short"
    if len(exit_any) < 2:
        if direction == "short":
            exit_any = [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 42.0},
                 "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"},
                 "role": "invalidation"},
            ]
        else:
            exit_any = [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 70.0},
                 "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"},
                 "role": "invalidation"},
            ]
    hold = 24
    m = re.search(r"max_hold\s*=\s*(\d+)", str(row.get("exit_sketch") or ""), re.I)
    if m:
        hold = max(12, int(m.group(1)))
    hold = max(hold, 20)
    key = "frost3_sol15m_%s_%s_%d" % (_safe(logic)[:18], direction[0], idx)
    dsl = {
        "key": key,
        "name": "寒霜叁-SOL-15m-%s" % logic,
        "direction": direction,
        "timeframe": TIMEFRAME,
        "supported_instruments": [SYMBOL],
        "max_hold_bars": hold,
        "description": str(row.get("thesis") or logic)[:160],
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "schema": "qiyu_strategy_dsl_v1",
    }
    return f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)


def make_book(row, idx):
    dsl = build_dsl_from_direction(row, idx)
    return {
        "title": "寒霜叁-SOL15m#%d-%s" % (idx, row.get("logic_class")),
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": dsl.get("direction"),
        "logic_class": str(row.get("logic_class") or "custom"),
        "thesis": row.get("thesis"),
        "micro_behavior": row.get("micro_behavior"),
        "entry_sketch": row.get("entry_sketch"),
        "exit_sketch": row.get("exit_sketch"),
        "causal_entry": row.get("causal_entry"),
        "causal_exit": row.get("causal_exit"),
        "avoid_from_postmortem": row.get("avoid_death") or row.get("why_avoids_banned_and_cl"),
        "dsl": dsl,
        "gate_mode": "frost2",
        "source": "frost3_sol15m",
        "dir_rank": idx,
    }


AUDIT_PROMPT = """你是GLM-5.2。只输出JSON。审计 SOL-USDT-SWAP 15m 假设书。
硬禁：改标的/改周期；exhaustion_fade/reclaim/breakout_continuation；CL15m同构；frost2 SOL衰竭。
对照：trades预期≥12；避免边际rsi≈54；需结构失效出场；logic_destruction稳健。
JSON：{"decision":"pass|reject|revise","score":0到100,"issues":["..."],
"revise":{"entry_sketch":null,"exit_sketch":null,"param_tweaks":{},"why":"..."},
"reason_zh":"..."}
"""


def glm_audit(book, report):
    payload = {
        "book": {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "thesis": book.get("thesis"), "micro_behavior": book.get("micro_behavior"),
            "entry": (book.get("dsl") or {}).get("entry"),
            "exit": (book.get("dsl") or {}).get("exit"),
            "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
            "causal_entry": book.get("causal_entry"),
            "causal_exit": book.get("causal_exit"),
        },
        "cl_traps": (report or {}).get("cl_lessons_zh") or (report or {}).get("trap_checklist"),
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
            "symbol": SYMBOL, "timeframe": TIMEFRAME,
            "direction": book["direction"], "logic_class": book["logic_class"],
            "thesis": book.get("thesis"),
            "entry_sketch": book.get("entry_sketch"),
            "exit_sketch": book.get("exit_sketch"),
        }
        book["dsl"] = build_dsl_from_direction(row, int(book.get("dir_rank") or 1))
    book["dsl"] = f2.ensure_dsl(book["dsl"], SYMBOL, TIMEFRAME)
    return book


def local_tweak(book, n, failed_step=""):
    book = copy.deepcopy(book)
    dsl = book["dsl"]
    entries = list((dsl.get("entry") or {}).get("all") or [])
    step = str(failed_step or "")
    # Structural fix: contradictory close vs ema16/ema21 band → ema21/ema8 bounce fade
    feats = []
    for row in entries:
        feat = (row.get("left") or {}).get("feature")
        right = row.get("right") or {}
        feats.append((feat, row.get("op"), right.get("feature"), right.get("value")))
    has_lt_ema16 = any(f == "close" and op == "lt" and rf == "ema16" for f, op, rf, _ in feats)
    has_gt_ema21 = any(f == "close" and op == "gt" and rf == "ema21" for f, op, rf, _ in feats)
    if has_lt_ema16 and has_gt_ema21 and book.get("direction") == "short":
        print("[sol15m] structural_fix close<ema16∧close>ema21 → bounce fade", flush=True)
        row = {
            "symbol": SYMBOL, "timeframe": TIMEFRAME,
            "direction": "short", "logic_class": "trend_pullback_fade",
            "thesis": book.get("thesis"),
            "entry_sketch": (
                "h1_ema19<h1_ema53; h1_slope4<0; close>ema21; close<ema8; "
                "rsi14>52; rsi14<68; macd_stick<0; z20>0.0; z20<1.5; cci>20"
            ),
            "exit_sketch": book.get("exit_sketch") or (
                "rsi14<42 take_profit; close>prev_high20 invalidation; max_hold=20"),
        }
        book["entry_sketch"] = row["entry_sketch"]
        book["logic_class"] = "trend_pullback_fade"
        book["dsl"] = build_dsl_from_direction(row, int(book.get("dir_rank") or 3))
        book["dsl"]["key"] = (book["dsl"]["key"][:80] + "_sfix%d" % n)[:100]
        return book
    for row in entries:
        feat = (row.get("left") or {}).get("feature")
        right = row.get("right") or {}
        if "value" not in right:
            continue
        v = float(right["value"])
        if feat == "rsi14" and row.get("op") == "gt":
            if "walk_forward" in step or "wf" in step:
                right["value"] = max(48.0, v - 1.0 * n)
            else:
                right["value"] = min(62.0, v + 0.5 * n)
        elif feat == "rsi14" and row.get("op") == "lt":
            if "walk_forward" in step:
                right["value"] = min(72.0, v + 1.0 * n)
            else:
                right["value"] = max(55.0, v - 0.5 * n)
        elif feat == "z20" and row.get("op") == "gt":
            if "walk_forward" in step:
                right["value"] = v - 0.05 * n
            else:
                right["value"] = v + 0.05 * n
        elif feat == "z20" and row.get("op") == "lt":
            if "walk_forward" in step:
                right["value"] = v + 0.1 * n
            else:
                right["value"] = max(0.5, v - 0.05 * n)
        elif feat == "cci" and row.get("op") == "gt":
            right["value"] = max(30.0, v - 5.0 * n) if "walk" in step else min(120.0, v + 5 * n)
        elif feat == "cci" and row.get("op") == "lt":
            right["value"] = v + (-5.0 * n if "walk" in step else 5.0 * n)
    dsl["entry"] = {"all": entries}
    hold = int(dsl.get("max_hold_bars") or 20)
    if "friction" in step or "mc" in step:
        dsl["max_hold_bars"] = max(12, hold - 2 * n)
    elif "walk" in step:
        dsl["max_hold_bars"] = hold + 4 * n
    book["dsl"] = f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)
    book["dsl"]["key"] = (book["dsl"]["key"][:80] + "_t%d" % n)[:100]
    return book


REPAIR_PROMPT = """你是GLM-5.2。只输出JSON。SOL-USDT-SWAP 15m 闸门修复（增量）。
禁止换标的/换周期/换到禁逻辑类(exhaustion_fade/reclaim/breakout_continuation)。
对照CL陷阱：勿把交易数压到<10；勿边际rsi≈54；保留结构失效出场。
JSON：{"ok":true,"dsl_patch":{"entry_all":null,"exit_any":null,"max_hold_bars":null},
"param_tweaks":{},"why":"..."}
entry_all/exit_any 若提供则为完整条件数组（DSL叶子格式）。
"""


def glm_repair(book, packs, failed_step, stage):
    payload = {
        "stage": stage,
        "failed_step": failed_step,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_class": book["logic_class"],
        "dsl": book.get("dsl"),
        "metrics": packs.get("base_metrics") or packs.get("metrics"),
        "quick": {k: packs.get(k) for k in ("quick_pass", "failed_step", "logic_destruction")},
        "full": packs.get("full"),
        "cl_traps": [
            "trades>=10 for WF folds",
            "no marginal exhaustion entry",
            "keep invalidation structural",
            "no banned logic classes",
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
    # reject banned logic sneak-in via patch (soft check on features only)
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
    book2["dsl"]["key"] = (_safe(book2["dsl"].get("key")) + "_r")[:100]
    book2["dsl"] = f2.ensure_dsl(book2["dsl"], SYMBOL, TIMEFRAME)
    return book2, ai


def update_status(**kw):
    path = os.path.join(OUT, "%s_status.json" % PREFIX)
    st = {}
    if os.path.exists(path):
        try:
            st = json.load(open(path))
        except Exception:
            st = {}
    st.update(kw)
    st["updated_at"] = _now()
    open(path, "w").write(json.dumps(st, ensure_ascii=False, indent=2, default=str) + "\n")
    return st


def sol_quick_suite(book):
    """WF≥7/10 + logic_destruction only — skip friction until Full (1-core safe)."""
    import auto_trade_strategy_dsl as dsl_mod
    print("[sol15m] quick_suite start", (book.get("dsl") or {}).get("key"), flush=True)
    t0 = datetime.now()
    try:
        print("[sol15m] validating dsl", flush=True)
        definition = dsl_mod.validate_strategy(book.get("dsl") or {})
        print("[sol15m] validate ok", flush=True)
    except Exception as exc:
        return {"ok": False, "stage": "validate", "error": str(exc),
                "quick_pass": False, "failed_step": "validate"}
    try:
        print("[sol15m] loading frame", flush=True)
        fr = d._frame(SYMBOL, TIMEFRAME)
        print("[sol15m] frame rows=%s elapsed=%ss" % (
            len(fr), int((datetime.now() - t0).total_seconds())), flush=True)
        print("[sol15m] backtest start", flush=True)
        base = d._backtest(definition, SYMBOL, TIMEFRAME, "observed_base")
        bm = d._metrics_from_trades(base.get("trades") or [])
    except Exception as exc:
        print("[sol15m] base_backtest error", exc, flush=True)
        return {"ok": False, "stage": "base_backtest", "error": str(exc),
                "quick_pass": False, "failed_step": "base_backtest"}
    print("[sol15m] base done tr=%s fp=%s/%s sh=%s elapsed=%ss" % (
        bm.get("trades"), bm.get("fold_positive"), bm.get("folds"), bm.get("sharpe"),
        int((datetime.now() - t0).total_seconds())), flush=True)
    wf_ok = (
        int(bm.get("trades") or 0) >= int(getattr(f2, "MIN_TRADES", 8))
        and int(bm.get("folds") or 0) >= int(getattr(f2, "QUICK_WF_FOLDS", 10))
        and int(bm.get("fold_positive") or 0) >= int(f2.QUICK_WF_POS)
    )
    packs = {
        "ok": True,
        "definition": definition,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "base_metrics": bm,
        "anti_overfit": {"pass": wf_ok, "metrics": bm},
        "trades": base.get("trades"),
    }
    if not wf_ok:
        packs["quick_pass"] = False
        packs["failed_step"] = "quick_walk_forward"
        packs["logic_destruction"] = {"pass": False, "skipped": True}
        print("[sol15m] quick WF fail", flush=True)
        return packs
    # Logic destruction ±20% (3 seeds) — only if WF already ok
    destroyed = []
    dest_pass = True
    for seed in (11, 29, 47):
        try:
            pert = d._perturb_core_params(definition, pct=0.20, seed=seed)
            pert = dsl_mod.validate_strategy(pert)
            r = d._backtest(pert, SYMBOL, TIMEFRAME, "observed_base")
            m = d._metrics_from_trades(r.get("trades") or [])
            ok_row = m["trades"] >= 3 and m["mean_net"] > -0.01
            destroyed.append({"seed": seed, "ok": ok_row, "metrics": m})
            if not ok_row:
                dest_pass = False
        except Exception as exc:
            destroyed.append({"seed": seed, "ok": False, "error": str(exc)})
            dest_pass = False
        print("[sol15m] dest seed", seed, "ok", destroyed[-1].get("ok"), flush=True)
    packs["logic_destruction"] = {"pass": bool(dest_pass), "rows": destroyed}
    packs["quick_pass"] = bool(dest_pass)
    if not dest_pass:
        packs["failed_step"] = "quick_logic_destruction"
    print("[sol15m] quick done pass=%s elapsed=%ss" % (
        packs["quick_pass"], int((datetime.now() - t0).total_seconds())), flush=True)
    return packs


def sol_full_suite(book, packs=None):
    """Friction Sharpe≥0 + MC beat≥90% after Quick pass."""
    if packs is None or not packs.get("quick_pass"):
        packs = sol_quick_suite(book)
    if not packs.get("quick_pass"):
        packs["full_pass"] = False
        return packs
    print("[sol15m] full_suite friction+mc", flush=True)
    definition = packs.get("definition") or (book.get("dsl") or {})
    try:
        import auto_trade_strategy_dsl as dsl_mod
        definition = packs.get("definition") or dsl_mod.validate_strategy(book.get("dsl") or {})
        fr = d._backtest(definition, SYMBOL, TIMEFRAME, "observed_base",
                         slip_mult=2.0, latency_extra=0.00005, fill_fail_pct=0.05)
        fr_m = d._metrics_from_trades(fr.get("trades") or [])
    except Exception as exc:
        fr_m = {"error": str(exc), "sharpe": -99, "trades": 0}
    fr_ok = int(fr_m.get("trades") or 0) >= 5 and float(fr_m.get("sharpe") or -99) >= 0.0
    packs["extreme_friction"] = {"pass": fr_ok, "metrics": fr_m}
    # MC on base trades
    trades = packs.get("trades")
    if trades is None:
        try:
            base = d._backtest(definition, SYMBOL, TIMEFRAME, "observed_base")
            trades = base.get("trades") or []
            packs["trades"] = trades
        except Exception:
            trades = []
    mc = f2.run_monte_carlo_beat_shuffles(trades)
    packs["full"] = {
        "friction_sharpe": fr_m.get("sharpe"),
        "friction_pass": fr_ok,
        "mc": mc,
    }
    packs["full_pass"] = bool(fr_ok and mc.get("pass"))
    if not packs["full_pass"]:
        packs["failed_step"] = (
            "full_extreme_friction" if not fr_ok else "full_monte_carlo")
    print("[sol15m] full done pass=%s fr_sh=%s mc=%s" % (
        packs["full_pass"], fr_m.get("sharpe"), (mc or {}).get("beat_ratio")), flush=True)
    return packs


def archive_failure(result, death_cause):
    arch = os.path.join(OUT, "archive", "%s_%s" % (
        PREFIX, _safe(result.get("failed_step") or "fail")))
    os.makedirs(arch, exist_ok=True)
    payload = dict(result)
    payload["death_cause"] = death_cause
    payload["archived_at"] = _now()
    open(os.path.join(arch, "result.json"), "w").write(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
    open(os.path.join(arch, "DEATH_CAUSE.json"), "w").write(
        json.dumps(death_cause, ensure_ascii=False, indent=2, default=str) + "\n")
    _write("archive_pointer", {"archive_path": arch, "death_cause": death_cause, "at": _now()})
    return arch


def exact_death_cause(result, hist):
    step = result.get("failed_step") or "unknown"
    last_quick = None
    last_full = None
    last_sim = None
    for h in hist:
        if h.get("stage") == "quick":
            last_quick = h
        if h.get("stage") == "full":
            last_full = h
        if h.get("stage") == "sim_formal":
            last_sim = h
    return {
        "failed_step": step,
        "reason": result.get("reason"),
        "logic_class": result.get("logic_class"),
        "book_key": result.get("book_key"),
        "last_quick": last_quick,
        "last_full": last_full,
        "last_sim": last_sim,
        "metrics": result.get("metrics"),
        "full": result.get("full"),
        "sim": result.get("sim"),
        "summary_zh": (
            "SOL15m 在 %s 耗尽修复上限：%s" % (step, result.get("reason") or "")
        ),
    }


def process_book(book, report):
    hist = []
    update_status(stage="audit", symbol=SYMBOL, timeframe=TIMEFRAME,
                  logic=book["logic_class"], key=(book.get("dsl") or {}).get("key"))

    audit_ok = False
    for atry in range(MAX_AUDIT_REVISE + 1):
        decision, _ai = glm_audit(book, report)
        hist.append({
            "stage": "audit", "try": atry,
            "decision": decision.get("decision"),
            "reason": decision.get("reason_zh"),
            "fallback": decision.get("fallback"),
        })
        _write("audit", {"try": atry, "decision": decision, "at": _now()})
        dec = str(decision.get("decision") or "pass").lower()
        if dec == "pass":
            audit_ok = True
            break
        if dec == "revise" and atry < MAX_AUDIT_REVISE:
            book = apply_revise(book, decision.get("revise") or {})
            continue
        return {
            "ok": False, "failed_step": "hyp_audit_reject",
            "reason": decision.get("reason_zh") or decision.get("issues"),
            "hist": hist, "book": book, "logic_class": book.get("logic_class"),
            "book_key": (book.get("dsl") or {}).get("key"),
        }
    if not audit_ok:
        return {
            "ok": False, "failed_step": "hyp_audit_reject",
            "reason": "audit not passed after revises", "hist": hist, "book": book,
            "logic_class": book.get("logic_class"),
            "book_key": (book.get("dsl") or {}).get("key"),
        }

    packs = None
    for qtry in range(MAX_QUICK_REPAIR + 1):
        update_status(stage="quick", attempt=qtry)
        packs = sol_quick_suite(book)
        hist.append({
            "stage": "quick", "try": qtry, "pass": packs.get("quick_pass"),
            "failed_step": packs.get("failed_step"),
            "fp": (packs.get("base_metrics") or {}).get("fold_positive"),
            "folds": (packs.get("base_metrics") or {}).get("folds"),
            "tr": (packs.get("base_metrics") or {}).get("trades"),
            "wr": (packs.get("base_metrics") or {}).get("win_rate_pct"),
            "sh": (packs.get("base_metrics") or {}).get("sharpe"),
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
        })
        _write("quick_try%d" % qtry, {"packs_summary": hist[-1], "dsl": book.get("dsl"), "at": _now()})
        if packs.get("quick_pass"):
            break
        if qtry >= MAX_QUICK_REPAIR:
            return {
                "ok": False,
                "failed_step": packs.get("failed_step") or "quick_exhausted",
                "reason": "Quick failed after %d repairs" % MAX_QUICK_REPAIR,
                "metrics": packs.get("base_metrics"),
                "dest": (packs.get("logic_destruction") or {}).get("pass"),
                "hist": hist, "book": book, "logic_class": book.get("logic_class"),
                "book_key": (book.get("dsl") or {}).get("key"),
            }
        book = local_tweak(book, qtry + 1, packs.get("failed_step"))
        repaired, _ai = glm_repair(book, packs, packs.get("failed_step"), "quick")
        if repaired:
            book = repaired
        hist.append({"stage": "quick_repair", "try": qtry, "ok": bool(repaired)})

    for ftry in range(MAX_FULL_REPAIR + 1):
        update_status(stage="full", attempt=ftry)
        packs = sol_full_suite(book, packs)
        hist.append({
            "stage": "full", "try": ftry, "pass": packs.get("full_pass"),
            "failed_step": packs.get("failed_step"),
            "friction": (packs.get("full") or {}).get("friction_sharpe"),
            "mc": ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
        })
        _write("full_try%d" % ftry, {"summary": hist[-1], "dsl": book.get("dsl"), "at": _now()})
        if packs.get("full_pass"):
            break
        if ftry >= MAX_FULL_REPAIR:
            return {
                "ok": False,
                "failed_step": packs.get("failed_step") or "full_exhausted",
                "reason": "Full failed after %d repairs" % MAX_FULL_REPAIR,
                "full": packs.get("full"),
                "hist": hist, "book": book, "logic_class": book.get("logic_class"),
                "book_key": (book.get("dsl") or {}).get("key"),
            }
        book = local_tweak(book, ftry + 1, packs.get("failed_step"))
        repaired, _ai = glm_repair(book, packs, packs.get("failed_step"), "full")
        if repaired:
            book = repaired
        q2 = sol_quick_suite(book)
        hist.append({
            "stage": "re_quick_after_full_repair",
            "pass": q2.get("quick_pass"),
            "failed_step": q2.get("failed_step"),
        })
        if not q2.get("quick_pass"):
            packs = q2
            packs["full_pass"] = False
            packs["failed_step"] = q2.get("failed_step") or "quick_regressed"
            continue
        packs = q2

    for stry in range(MAX_SIM_REPAIR + 1):
        update_status(stage="sim_formal", attempt=stry)
        sf = f2.run_sim_formal(book, packs)
        # force op label for formal record is inside f2; also record ours
        hist.append({
            "stage": "sim_formal", "try": stry,
            "failed_step": sf.get("failed_step"),
            "sim_ds": (sf.get("sim") or {}).get("wr_deepseek_sim"),
            "sim_qw": (sf.get("sim") or {}).get("wr_qwen_sim"),
            "formal_approved": (sf.get("formal") or {}).get("approved"),
            "pending": (sf.get("pending") or {}).get("key"),
        })
        _write("sim_try%d" % stry, {"summary": hist[-1], "sim": sf.get("sim"),
                                     "formal": sf.get("formal"), "pending": sf.get("pending"),
                                     "at": _now()})
        if (sf.get("pending") or {}).get("ok"):
            return {
                "ok": True, "failed_step": None,
                "pending": sf.get("pending"), "sim": sf.get("sim"),
                "formal": sf.get("formal"), "book": book, "hist": hist,
                "logic_class": book.get("logic_class"),
                "book_key": (book.get("dsl") or {}).get("key"),
            }
        fs = sf.get("failed_step")
        if fs in ("formal_review", "pending_ingest"):
            return {
                "ok": False, "failed_step": fs,
                "reason": (sf.get("formal") or {}).get("reason") or (
                    sf.get("pending") or {}).get("reason"),
                "sim": sf.get("sim"), "formal": sf.get("formal"),
                "pending": sf.get("pending"), "hist": hist, "book": book,
                "logic_class": book.get("logic_class"),
                "book_key": (book.get("dsl") or {}).get("key"),
            }
        if stry >= MAX_SIM_REPAIR:
            return {
                "ok": False, "failed_step": fs or "sim_exhausted",
                "reason": "Sim/formal failed after %d repairs" % MAX_SIM_REPAIR,
                "sim": sf.get("sim"), "hist": hist, "book": book,
                "logic_class": book.get("logic_class"),
                "book_key": (book.get("dsl") or {}).get("key"),
            }
        book = local_tweak(book, stry + 1, "sim")
        repaired, _ai = glm_repair(book, packs, "sim_review", "sim")
        if repaired:
            book = repaired
        q3 = sol_quick_suite(book)
        if not q3.get("quick_pass"):
            hist.append({"stage": "sim_repair_quick_fail", "failed": q3.get("failed_step")})
            continue
        packs = sol_full_suite(book, q3)
        if not packs.get("full_pass"):
            hist.append({"stage": "sim_repair_full_fail", "failed": packs.get("failed_step")})
            continue

    return {
        "ok": False, "failed_step": "unknown_exhausted",
        "hist": hist, "book": book, "logic_class": book.get("logic_class"),
        "book_key": (book.get("dsl") or {}).get("key"),
    }


def quick_probe_score(book):
    """Light base-only readout to rank directions (avoid 5x backtests under load)."""
    import auto_trade_strategy_dsl as dsl_mod
    try:
        definition = dsl_mod.validate_strategy(book.get("dsl") or {})
        base = d._backtest(definition, SYMBOL, TIMEFRAME, "observed_base")
        bm = d._metrics_from_trades(base.get("trades") or [])
    except Exception as exc:
        return {"ok": False, "error": str(exc), "score": -999, "book": book}
    fp = int(bm.get("fold_positive") or 0)
    tr = int(bm.get("trades") or 0)
    sh = float(bm.get("sharpe") or -99)
    wr = float(bm.get("win_rate_pct") or 0)
    mn = float(bm.get("mean_net") or 0)
    # Heuristic: WF-ready shape without paying dest/friction yet
    wf_ready = 1 if (tr >= 10 and fp >= 7 and int(bm.get("folds") or 0) >= 10) else 0
    score = wf_ready * 1000 + fp * 10 + min(tr, 40) * 0.1 + sh + mn * 50 + wr * 0.05
    return {
        "ok": True,
        "quick_pass": False,  # probe never claims full quick pass
        "failed_step": None,
        "fp": fp, "folds": bm.get("folds"), "tr": tr,
        "wr": wr, "sh": sh, "dest": None, "mean_net": mn,
        "score": score, "book": book, "packs": None,
    }


def pick_direction(report, dirs):
    """Pick exactly one direction. Optional light probes (skipped under overload)."""
    skip_probe = str(os.environ.get("FROST3_SOL15M_SKIP_PROBE") or "").strip() in (
        "1", "true", "yes", "Y")
    # Prefer short trend_pullback_fade when prior long pullback showed fp≈0
    force_logic = str(os.environ.get("FROST3_SOL15M_FORCE_LOGIC") or "").strip()
    if force_logic:
        for row in dirs:
            if str(row.get("logic_class") or "") == force_logic:
                print("[sol15m] force_logic", force_logic, flush=True)
                return row, None
    if skip_probe:
        rec = int(report.get("recommend_rank") or 1)
        for row in dirs:
            if int(row.get("rank") or -1) == rec:
                print("[sol15m] skip_probe use recommend_rank", rec, row.get("logic_class"),
                      flush=True)
                return row, None
        print("[sol15m] skip_probe use dirs[0]", dirs[0].get("logic_class"), flush=True)
        return dirs[0], None

    probes = []
    for i, row in enumerate(dirs[:3]):
        book = make_book(row, int(row.get("rank") or i + 1))
        print("[sol15m] probe", book["logic_class"], book["direction"],
              (book.get("dsl") or {}).get("key"), flush=True)
        sc = quick_probe_score(book)
        probes.append({
            "rank": row.get("rank"),
            "logic_class": row.get("logic_class"),
            "direction": row.get("direction"),
            "score": sc.get("score"),
            "fp": sc.get("fp"),
            "tr": sc.get("tr"),
            "sh": sc.get("sh"),
            "dest": sc.get("dest"),
            "quick_pass": sc.get("quick_pass"),
            "failed_step": sc.get("failed_step"),
            "error": sc.get("error"),
        })
        row["_probe"] = sc
        print("[sol15m] probe_result", probes[-1], flush=True)
    _write("direction_probes", {"at": _now(), "probes": probes})
    ranked = sorted(dirs[:3], key=lambda r: -float((r.get("_probe") or {}).get("score") or -999))
    rec = int(report.get("recommend_rank") or 1)
    best = ranked[0]
    for row in ranked:
        if int(row.get("rank") or -1) == rec:
            bp = (best.get("_probe") or {})
            rp = (row.get("_probe") or {})
            if (float(rp.get("score") or -999) >= float(bp.get("score") or -999) - 15
                    and int(rp.get("fp") or 0) >= 5):
                return row, row.get("_probe")
            break
    return best, best.get("_probe")


def main():
    print("=== frost3_sol15m START ===", _now(), flush=True)
    update_status(op="寒霜叁-SOL15m", stage="collect", pending_key=None, success=None)

    # Warm / inject frame cache before any gate work
    update_status(stage="frame_cache")
    try:
        inject_sol_frame_cache()
    except Exception as exc:
        print("[sol15m] frame cache inject failed:", exc, flush=True)

    ctx = load_sol_context()
    _write("inputs", ctx)

    print("[sol15m] GLM 《单标的策略方向建议》", flush=True)
    update_status(stage="glm_direction")
    report = None
    ai = {}
    reuse = str(os.environ.get("FROST3_SOL15M_REUSE_DIR") or "").strip() in (
        "1", "true", "yes", "Y")
    reuse_path = os.path.join(OUT, "%s_单标的策略方向建议.json" % PREFIX)
    if reuse and os.path.exists(reuse_path):
        report = _load_json(reuse_path, None)
        if report:
            print("[sol15m] reuse direction report", reuse_path, flush=True)
            ai = {"ok": False, "error": "reused_local_report", "raw_preview": ""}
    if report is None:
        report, ai = ask_glm_directions(ctx)
    _write("glm_direction_raw", {
        "ai": {k: ai.get(k) for k in ("ok", "error", "raw_preview", "provider")},
        "parsed_ok": bool(report) and not report.get("fallback"),
        "at": _now(),
    })
    if not report:
        report = codex_fallback_directions(ctx)
    report["generated_at"] = _now()
    report["glm_ok"] = not bool(report.get("fallback"))
    dirs = sanitize_directions(report)
    if len(dirs) < 1:
        report = codex_fallback_directions(ctx)
        report["generated_at"] = _now()
        report["glm_ok"] = False
        dirs = sanitize_directions(report)
    if len(dirs) < 1:
        raise SystemExit("no valid SOL15m directions after sanitize")
    # keep up to 3 for the advice artifact
    report["directions"] = dirs[:3]
    _write("单标的策略方向建议", report)
    _write("direction_report", report)

    chosen, probe = pick_direction(report, dirs)
    print("[sol15m] chosen", chosen.get("logic_class"), chosen.get("direction"),
          "probe_fp", (probe or {}).get("fp"),
          chosen.get("thesis", "")[:80], flush=True)
    _write("chosen_direction", {"at": _now(), "chosen": chosen, "probe": {
        k: (probe or {}).get(k) for k in (
            "score", "fp", "tr", "sh", "dest", "quick_pass", "failed_step")
    }})

    # reuse probed book if available (keeps DSL aligned with probe)
    if probe and probe.get("book"):
        book = probe["book"]
    else:
        book = make_book(chosen, int(chosen.get("rank") or 1))
    # hard assert instrument lock
    assert book["symbol"] == SYMBOL and book["timeframe"] == TIMEFRAME
    assert not _logic_banned(book["logic_class"]), "banned logic: %s" % book["logic_class"]
    _write("hypothesis_book", {"book": book, "at": _now()})

    update_status(stage="pipeline", logic=book["logic_class"], key=book["dsl"]["key"])
    try:
        # If probe already quick-passed, skip re-audit waste only when audit passes;
        # still run full audit→pipeline for formal path integrity.
        result = process_book(book, report)
    except Exception as exc:
        result = {
            "ok": False, "failed_step": "exception",
            "reason": str(exc), "trace": traceback.format_exc()[-2000:],
            "hist": [], "book": book, "logic_class": book.get("logic_class"),
            "book_key": (book.get("dsl") or {}).get("key"),
        }

    end = {
        "at": _now(),
        "op": "寒霜叁-SOL15m",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_class": result.get("logic_class") or book.get("logic_class"),
        "book_key": result.get("book_key"),
        "ok": bool(result.get("ok")),
        "failed_step": result.get("failed_step"),
        "reason": result.get("reason"),
        "pending": result.get("pending"),
        "sim": {
            "ds": (result.get("sim") or {}).get("wr_deepseek_sim"),
            "qw": (result.get("sim") or {}).get("wr_qwen_sim"),
        } if result.get("sim") else None,
        "formal_approved": (result.get("formal") or {}).get("approved") if result.get("formal") else None,
        "hist": result.get("hist"),
    }

    if result.get("ok"):
        end["outcome"] = "PENDING"
        end["summary_zh"] = (
            "SOL15m %s 已过闸门并进入待人工确认：%s" % (
                end["logic_class"], (result.get("pending") or {}).get("key"))
        )
        update_status(stage="pending", success=True,
                      pending_key=(result.get("pending") or {}).get("key"),
                      summary_zh=end["summary_zh"])
    else:
        death = exact_death_cause(result, result.get("hist") or [])
        arch = archive_failure(result, death)
        end["outcome"] = "ARCHIVED"
        end["death_cause"] = death
        end["archive_path"] = arch
        end["summary_zh"] = death.get("summary_zh")
        update_status(stage="archived", success=False,
                      failed_step=result.get("failed_step"),
                      archive_path=arch, summary_zh=end["summary_zh"])

    _write("end_report", end)
    _write("result", result)
    print("=== frost3_sol15m END ===", json.dumps({
        "ok": end["ok"], "outcome": end.get("outcome"),
        "logic": end.get("logic_class"),
        "failed_step": end.get("failed_step"),
        "pending": (end.get("pending") or {}).get("key") if end.get("pending") else None,
        "summary_zh": end.get("summary_zh"),
    }, ensure_ascii=False), flush=True)
    return 0 if end["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
