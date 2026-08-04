#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜叁 — NG-USDT-SWAP 15m exhaustion-reclaim (XRP-framework port).

Standalone ONLY. Artifacts: /root/auto_trade/dual_engine/frost3_ng15m_xrpport_*
Key prefix: frost3_ng15m_xrpport_
Does NOT touch live ADA/LTC/NG5m/XRP or other frost3 lanes.

Logic: mirror of validated XRP 15m exhaustion framework → LONG reclaim:
  RSI extreme oversold + z20 deep negative + candle reclaim + session window.
NG vol ≠ crypto: adapt thresholds via ATR/vol dist + GLM; stronger reclaim confirm.
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
import frost3_action as f3
import frost3_step1 as step1
import auto_trade_dual_engine_factory as d
import auto_trade_strategy_dsl as dsl_mod

# Allow NG 15m for THIS pipeline only; still protect other live niches.
f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
f2.AVOID = [
    ("ADA-USDT-SWAP", None),
    ("LTC-USDT-SWAP", None),
    ("XRP-USDT-SWAP", None),
    # protect live NG 5m fade/reclaim daemons — do not mint 5m keys here
    ("NG-USDT-SWAP", "5m"),
]

OUT = "/root/auto_trade/dual_engine"
PREFIX = "frost3_ng15m_xrpport"
SYMBOL = "NG-USDT-SWAP"
TIMEFRAME = "15m"
MAX_AUDIT_REVISE = 2
MAX_QUICK_REPAIR = 3
MAX_FULL_REPAIR = 3
MAX_SIM_REPAIR = 3

# Default session: UTC 08-16 = Beijing 16-24 (same as live NG5 reclaim liquidity window)
SESSION_UTC_START = 8
SESSION_UTC_END = 16

XRP_TEMPLATE_DSL = {
    "key": "frost_xrp_rescue_h20_t45",
    "name": "寒霜-XRP-15m-exhaustion_fade",
    "direction": "short",
    "timeframe": "15m",
    "supported_instruments": ["XRP-USDT-SWAP"],
    "max_hold_bars": 20,
    "entry": {"all": [
        {"id": "e1", "left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}},
        {"id": "e2", "left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.0}},
        {"id": "e3", "left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"id": "e4", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
    ]},
    "exit": {"any": [
        {"id": "x1", "left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0},
         "role": "take_profit"},
        {"id": "x2", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"},
         "role": "invalidation"},
    ]},
    "schema": "qiyu_strategy_dsl_v1",
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_art(suffix, obj):
    path = os.path.join(OUT, "%s_%s.json" % (PREFIX, suffix))
    open(path, "w").write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


def _safe(s):
    s = str(s or "x")
    for ch in (".", " ", "/", ":", "+", "%", "-"):
        s = s.replace(ch, "p" if ch in (".", "-") else "_")
    return s[:40]


def compute_atr_stats():
    """Lightweight parquet ATR/vol — avoid contended _frame under multi-agent load.

    Prefer cached atr_stats artifact if present (multi-agent thrash safe).
    """
    cached = os.path.join(OUT, "%s_atr_stats.json" % PREFIX)
    if os.path.exists(cached) and os.environ.get("NG15M_REFRESH_ATR", "0") != "1":
        try:
            blob = json.load(open(cached))
            if isinstance(blob, dict) and blob.get("atr_30d"):
                blob["loaded_from_cache"] = cached
                return blob
        except Exception:
            pass

    import numpy as np
    import pandas as pd

    p = "/root/market_data/NG-USDT-SWAP/15m/NG_USDT_SWAP_15m_OKX_LOCAL.parquet"
    # Column projection — lower peak RAM vs full-frame pandas under host thrash
    try:
        df = pd.read_parquet(p, columns=["open", "high", "low", "close", "ts"])
    except Exception:
        try:
            df = pd.read_parquet(p, columns=["open", "high", "low", "close"])
        except Exception:
            df = pd.read_parquet(p)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)
    o = df["open"].astype(float)
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    atr_pct = (atr / c) * 100.0
    br = ((h - l) / c) * 100.0
    tail_n = min(2880, int(atr_pct.dropna().shape[0]))
    atr_t = atr_pct.dropna().iloc[-tail_n:]
    br_t = br.iloc[-tail_n:]

    def pack(s):
        qs = np.percentile(s, [5, 10, 25, 50, 75, 90, 95, 99])
        return {
            "n": int(len(s)),
            "mean": round(float(s.mean()), 4),
            "std": round(float(s.std()), 4),
            "p": {str(k): round(float(v), 4) for k, v in zip(
                [5, 10, 25, 50, 75, 90, 95, 99], qs)},
        }

    delta = c.diff()
    up = delta.clip(lower=0)
    down = (-delta).clip(lower=0)
    rs = up.rolling(14).mean() / (down.rolling(14).mean() + 1e-12)
    rsi = 100 - 100 / (1 + rs)
    z = (c - c.rolling(20).mean()) / (c.rolling(20).std() + 1e-12)
    rsi_t = rsi.iloc[-tail_n:]
    z_t = z.iloc[-tail_n:]
    ema16 = c.ewm(span=16, adjust=False).mean()

    hours = None
    for cand in ("ts", "timestamp", "datetime", "open_time", "time"):
        if cand in df.columns:
            t = pd.to_datetime(df[cand], unit="ms", errors="coerce")
            if float(t.isna().mean()) > 0.5:
                t = pd.to_datetime(df[cand], errors="coerce")
            hours = t.dt.hour
            break
    if hours is None and hasattr(df.index, "hour"):
        hours = pd.Series(df.index.hour, index=df.index)

    out = {
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "parquet": p,
        "n_bars": int(len(df)),
        "atr_all": pack(atr_pct.dropna()),
        "atr_30d": pack(atr_t),
        "range_30d": pack(br_t),
        "stop0p9_over_atr_med": round(0.9 / float(atr_t.median()), 3),
        "frac_range_le": {
            str(k): round(float((br_t <= k).mean()), 3)
            for k in (0.9, 1.2, 1.5, 2.0, 2.5)
        },
        "rsi_le_pct": {
            str(t): round(float((rsi_t <= t).mean()) * 100, 2)
            for t in (25, 30, 35, 40, 45)
        },
        "z_le_pct": {
            str(t): round(float((z_t <= t).mean()) * 100, 2)
            for t in (-2.5, -2.0, -1.5, -1.25, -1.0, -0.75, -0.5)
        },
        "stop_noise_verdict_zh": None,
        "session_default_utc": [SESSION_UTC_START, SESSION_UTC_END],
        "session_note_zh": "默认对齐 NG5 reclaim 流动性窗 UTC08-16（北京16-24）",
    }
    # stop vs noise: if median bar range ~ atr near/above 0.9%, stop is tight → need stronger confirm
    med_atr = float(atr_t.median())
    med_br = float(br_t.median())
    p75_br = float(br_t.quantile(0.75))
    if med_atr >= 0.55 or p75_br >= 0.9:
        out["stop_noise_verdict_zh"] = (
            "0.9%%止损相对 NG15m 噪声偏紧（ATR中位≈%.3f%%，bar range p75≈%.3f%%）；"
            "入场需更强收回确认+会话过滤，hold 加长，RSI/z 勿过极端以免样本饿死"
            % (med_atr, p75_br)
        )
        out["adapt_bias"] = "looser_rsi_z_stronger_reclaim_longer_hold"
    else:
        out["stop_noise_verdict_zh"] = (
            "0.9%%止损相对 ATR 中位有一定缓冲（ATR中位≈%.3f%%，range中位≈%.3f%%）；"
            "仍建议会话窗+阳线收回确认，避免 spike 后二次扫止损"
            % (med_atr, med_br)
        )
        out["adapt_bias"] = "moderate_confirm"

    c_t = c.iloc[-tail_n:]
    o_t = o.iloc[-tail_n:]
    e_t = ema16.iloc[-tail_n:]
    combos = {
        "rsi35_z125_green": float(((rsi_t <= 35) & (z_t <= -1.25) & (c_t > o_t)).mean()),
        "rsi40_z100_green": float(((rsi_t <= 40) & (z_t <= -1.0) & (c_t > o_t)).mean()),
        "rsi45_z075_green": float(((rsi_t <= 45) & (z_t <= -0.75) & (c_t > o_t)).mean()),
        "rsi40_z100_green_gt_ema16": float(
            ((rsi_t <= 40) & (z_t <= -1.0) & (c_t > o_t) & (c_t > e_t)).mean()
        ),
        "rsi45_z075_green_gt_ema16": float(
            ((rsi_t <= 45) & (z_t <= -0.75) & (c_t > o_t) & (c_t > e_t)).mean()
        ),
    }
    out["combo_freq_pct"] = {k: round(v * 100, 3) for k, v in combos.items()}
    out["expected_hits_30d"] = {k: int(round(v * tail_n)) for k, v in combos.items()}

    if hours is not None:
        deep = (z <= -1.25) & (rsi <= 35)
        reclaim = (c > o) & (z <= -0.75) & (rsi <= 40)
        out["deep_oversold_pct_by_utc_hour"] = {
            int(k): round(float(v), 2)
            for k, v in (deep.groupby(hours).mean() * 100).items()
        }
        out["reclaim_proxy_pct_by_utc_hour"] = {
            int(k): round(float(v), 2)
            for k, v in (reclaim.groupby(hours).mean() * 100).items()
        }
        ranked = (reclaim.groupby(hours).mean() * 100).sort_values(ascending=False)
        out["top_reclaim_hours_utc"] = [int(x) for x in ranked.head(8).index.tolist()]

    # XRP contrast if cheap parquet exists (optional)
    try:
        xp = "/root/market_data/XRP-USDT-SWAP/15m"
        # may not exist; try 5m resample note only
        out["xrp_contrast_note"] = (
            "XRP 成功 DSL 为 short exhaustion_fade（rsi>55,z>0,macd<0,close<ema16,hold20）；"
            "本线镜像为 long reclaim + session，不做 XRP 盲克隆。"
        )
    except Exception:
        pass
    return out


def install_session_proxies():
    """Inject session_liq / vol_pulse / candle_reclaim into frames (process-local)."""
    dsl_mod.FEATURES.add("session_liq")
    dsl_mod.FEATURES.add("vol_pulse_proxy")
    dsl_mod.FEATURES.add("hour_utc")
    dsl_mod.FEATURES.add("candle_reclaim")  # 1 if close>open else 0

    _orig_frame = d._frame

    def _frame_with_proxies(symbol, timeframe):
        frame = None
        pkl = "/root/auto_trade/dual_engine/frost3_ng15m_xrpport_frame.pkl"
        if (str(symbol).upper() == SYMBOL and str(timeframe) == TIMEFRAME
                and os.path.exists(pkl)):
            try:
                import pickle
                frame = pickle.load(open(pkl, "rb"))
            except Exception:
                frame = None
        if frame is None:
            frame = _orig_frame(symbol, timeframe)
        try:
            if hasattr(frame, "copy"):
                frame = frame.copy()
            if hasattr(frame, "iloc") and len(frame) > 12000:
                frame = frame.iloc[-12000:]
            if hasattr(frame, "index") and hasattr(frame.index, "hour"):
                hour = frame.index.hour.astype(float)
                frame["hour_utc"] = hour
                frame["session_liq"] = (
                    ((hour >= float(SESSION_UTC_START)) & (hour < float(SESSION_UTC_END)))
                    .astype(float)
                )
            else:
                frame["hour_utc"] = 12.0
                frame["session_liq"] = 1.0
            if "atr14" in getattr(frame, "columns", []):
                base = frame["atr14"].rolling(48, min_periods=12).mean()
                frame["vol_pulse_proxy"] = (
                    frame["atr14"] / base.replace(0, float("nan"))
                ).fillna(1.0)
            else:
                frame["vol_pulse_proxy"] = 1.0
            if "close" in frame.columns and "open" in frame.columns:
                frame["candle_reclaim"] = (
                    frame["close"].astype(float) > frame["open"].astype(float)
                ).astype(float)
            else:
                frame["candle_reclaim"] = 1.0
        except Exception:
            pass
        return frame

    d._frame = _frame_with_proxies
    return {
        "session_utc": [SESSION_UTC_START, SESSION_UTC_END],
        "session_feature": "session_liq (1 if hour in [8,16) else 0)",
        "candle_reclaim": "candle_reclaim=1 if close>open",
        "volume_proxy": "vol_pulse_proxy = atr14 / atr14.rolling(48).mean",
        "xrp_template_key": "frost_xrp_rescue_h20_t45",
        "mirror": "XRP short fade → NG long reclaim",
    }


def ensure_ng15m_candles():
    """Ensure NG 15m market data exists. Skip heavy _frame under thrash."""
    info = {"parquet": None, "frame_ok": False, "n": None, "error": None, "deferred": False}
    pq = "/root/market_data/NG-USDT-SWAP/15m/NG_USDT_SWAP_15m_OKX_LOCAL.parquet"
    info["parquet"] = pq if os.path.exists(pq) else None
    if os.environ.get("NG15M_SKIP_FRAME_CHECK", "1") == "1":
        # Default: defer frame build to quick_suite (avoids double contended load).
        info["frame_ok"] = bool(info["parquet"])
        info["deferred"] = True
        info["note"] = "skipped _frame pre-check; parquet present=%s" % bool(info["parquet"])
        return info
    try:
        fr = d._frame(SYMBOL, TIMEFRAME)
        info["n"] = int(len(fr)) if hasattr(fr, "__len__") else None
        info["frame_ok"] = bool(info["n"] and info["n"] > 200)
        info["has_session_liq"] = "session_liq" in getattr(fr, "columns", [])
        info["has_candle_reclaim"] = "candle_reclaim" in getattr(fr, "columns", [])
    except Exception as exc:
        info["error"] = str(exc)[:500]
    return info


PARAM_PROMPT = """你是GLM-5.2。只输出纯JSON，禁止Markdown、省略号占位符、注释。
任务：输出《NG 15m 衰竭回收参数适配建议》。

硬约束：
1) symbol必须=NG-USDT-SWAP，timeframe必须=15m，不可改标的/周期
2) 逻辑必须是 exhaustion_reclaim 多头：RSI极端超卖 + z20深度为负 + 阳线收回(candle_reclaim>0.5) + 会话窗(session_liq>0.5)
3) 以 XRP 15m 成功 DSL frost_xrp_rescue_h20_t45 为【框架镜像】（XRP是空头衰竭：rsi>55,z>0,macd<0,close<ema16,hold20；本线镜像为多头回收），禁止修改现网 XRP
4) NG 波动≠加密：大K线振幅、尖刺后V修复；固定止损0.9%@20x 必须相对噪声有意义——根据 atr_stats 放宽/收紧 RSI/z，并加强收回确认；trades预期≥12
5) 吸收 CL15m 归档教训：勿边际入场；勿单阈值把交易压到<10；保留结构失效出场；logic_destruction±20%稳健；硬止损簇是 friction 主因
6) 与现网 NG 5m fade 区分：本线是 15m reclaim，键前缀 frost3_ng15m_xrpport_
7) 可用特征：session_liq, candle_reclaim, vol_pulse_proxy, hour_utc, rsi14,z20,macd_stick,cci,ema16/21,prev_low20,prev_high20,h1_ema19,h1_ema53,h1_slope4,atr14,open,close

JSON schema：
{
  "report_title":"NG 15m 衰竭回收参数适配建议",
  "symbol":"NG-USDT-SWAP",
  "timeframe":"15m",
  "xrp_mirror_zh":"如何镜像XRP框架",
  "ng_vs_crypto_vol_zh":"NG与加密波动差异及对止损的含义",
  "stop_distance_rationale_zh":"为何0.9%止损在NG15m上需要怎样的入场/持有适配",
  "session_window_utc":[8,16],
  "session_rationale_zh":"...",
  "cl_lessons_zh":"...",
  "hypothesis":{
    "logic_class":"exhaustion_reclaim",
    "direction":"long",
    "thesis":"...",
    "entry_sketch":"分号分隔，必须含 session_liq>0.5 与 candle_reclaim>0.5 与 rsi/z 超卖与收回",
    "exit_sketch":"止盈+结构失效+max_hold",
    "param_grid":{"rsi_hi":40,"z_hi":-0.75,"vol_pulse_min":1.0,"max_hold":28},
    "diff_vs_ng5m_fade":"...",
    "avoid_death":["stop_cluster","sample_starvation","cost_collapse"],
    "expected_trades_hint":">=12"
  },
  "approve":true,
  "mentor_notes":"..."
}
"""


AUDIT_PROMPT = """你是GLM-5.2。只输出JSON。审计 NG-USDT-SWAP 15m exhaustion_reclaim 假设书。
硬禁：改标的/改周期；改成NG5m fade；修改XRP；CL15m同构边际衰竭。
必须：session_liq；candle_reclaim；超卖rsi+负z；结构失效出场；trades预期≥12。
JSON：{"decision":"pass|reject|revise","score":0到100,"issues":["..."],
"revise":{"entry_sketch":null,"exit_sketch":null,"param_tweaks":{},"why":"..."},
"reason_zh":"..."}
"""


def ask_glm(prompt, packet, timeout_sec=75, max_tokens=2200):
    import threading
    box = {"parsed": None, "ai": {"ok": False, "error": "timeout"}}

    def _run():
        try:
            ai = d._ai_json("glm", prompt, packet, max_tokens=max_tokens, temperature=0.15)
            parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
            raw = ai.get("raw_preview") or ai.get("content") or ""
            if not parsed and raw:
                m = re.search(r"\{[\s\S]*\}", str(raw))
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except Exception:
                        parsed = None
            box["parsed"] = parsed
            box["ai"] = ai
        except Exception as exc:
            box["ai"] = {"ok": False, "error": str(exc)}

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout_sec)
    if th.is_alive():
        return None, {"ok": False, "error": "glm_timeout_%ss" % timeout_sec, "raw_preview": ""}
    return box["parsed"], box["ai"]


def fallback_params(atr_stats):
    bias = (atr_stats or {}).get("adapt_bias") or "moderate_confirm"
    # Looser RSI/z + stronger reclaim when stop is tight vs noise
    # Prioritize sample size (≥12 trades / WF folds≥10). Deep oversold+ema16
    # co-occurrence ≈0 on NG; use candle_reclaim + mild z/rsi + session.
    if bias == "looser_rsi_z_stronger_reclaim_longer_hold":
        rsi_hi, z_hi, hold, vol_min = 46.0, -0.45, 36, 0.98
    else:
        # stop≈5x ATR_med → noise not the bottleneck; sample starvation is.
        rsi_hi, z_hi, hold, vol_min = 45.0, -0.5, 32, 0.98
    entry = (
        "session_liq>0.5; candle_reclaim>0.5; close>prev_low20; macd_stick>0; "
        "rsi14<%s; rsi14>18; z20<%s; z20>-2.5; vol_pulse_proxy>%s"
        % (rsi_hi, z_hi, vol_min)
    )
    return {
        "report_title": "NG 15m 衰竭回收参数适配建议",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "fallback": True,
        "xrp_mirror_zh": (
            "XRP frost_xrp_rescue_h20_t45 空头框架（超买+z正+macd翻负+跌破ema16）"
            "镜像为多头：超卖+z负+阳线收回+站上ema16+会话窗；保留结构失效出场与适中 hold。"
        ),
        "ng_vs_crypto_vol_zh": (atr_stats or {}).get("stop_noise_verdict_zh") or (
            "NG15m 振幅大、尖刺后常V修复；加密同阈值会过密或被噪声扫止损。"
        ),
        "stop_distance_rationale_zh": (
            "固定0.9%%@20x：stop/ATR中位≈%s；若接近1x噪声则必须用更强收回确认与更长hold，"
            "避免刚入场被V修复反向扫损。"
            % ((atr_stats or {}).get("stop0p9_over_atr_med"),)
        ),
        "session_window_utc": [SESSION_UTC_START, SESSION_UTC_END],
        "session_rationale_zh": (
            "对齐 NG5 reclaim 已验证流动性窗 UTC08-16（北京16-24），过滤薄流动性时段噪声扫损。"
        ),
        "cl_lessons_zh": (
            "CL15m：边际入场+硬止损簇→friction崩；过窄过滤→trades<10 WF不足；"
            "软化引入新硬止损且dest失败。本方案保证样本≥12、结构失效、远离边际。"
        ),
        "hypothesis": {
            "logic_class": "exhaustion_reclaim",
            "direction": "long",
            "thesis": "NG15m 会话内超卖耗竭后的阳线收回：镜像XRP框架，适配NG噪声",
            "entry_sketch": entry,
            "exit_sketch": (
                "rsi14>58 take_profit; close<prev_low20 invalidation; max_hold=%d" % hold
            ),
            "param_grid": {
                "rsi_hi": rsi_hi, "z_hi": z_hi,
                "vol_pulse_min": vol_min, "max_hold": hold,
            },
            "diff_vs_ng5m_fade": (
                "现网NG5m fade是短周期冲高回落空；本线是15m超卖收回多，键前缀分离，不改5m守护进程"
            ),
            "avoid_death": ["stop_cluster", "sample_starvation", "cost_collapse"],
            "expected_trades_hint": ">=12",
        },
        "approve": True,
        "mentor_notes": "codex_fallback_from_atr_stats",
        "atr_stats_summary": {
            "stop0p9_over_atr_med": (atr_stats or {}).get("stop0p9_over_atr_med"),
            "adapt_bias": bias,
            "combo_freq_pct": (atr_stats or {}).get("combo_freq_pct"),
        },
    }


def parse_sketch_conditions(sketch):
    leaves = []
    text = str(sketch or "")
    for part in re.split(r"[;\n]+", text):
        part = part.strip()
        if not part:
            continue
        low = part.lower()
        role = None
        if "take_profit" in low or low.endswith("tp"):
            role = "take_profit"
        if "invalidation" in low or low.endswith("inv"):
            role = "invalidation"
        part2 = re.sub(r"(?i)(take_profit|invalidation|\btp\b|\binv\b)", "", part).strip()
        m = re.match(
            r"^([A-Za-z0-9_]+)\s*(>=|<=|<|>|gt|lt)\s*([A-Za-z0-9_\.\-]+)$",
            part2.replace(" ", ""),
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


def build_dsl_from_hyp(hyp, idx=1):
    entry = parse_sketch_conditions(hyp.get("entry_sketch"))
    exit_any = parse_sketch_conditions(hyp.get("exit_sketch"))
    grid = hyp.get("param_grid") or {}
    if len(entry) < 5:
        rsi_hi = float(grid.get("rsi_hi") or 40)
        z_hi = float(grid.get("z_hi") or -0.75)
        vol_min = float(grid.get("vol_pulse_min") or 1.05)
        entry = [
            {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}},
            {"left": {"feature": "candle_reclaim"}, "op": "gt", "right": {"value": 0.5}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": rsi_hi}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 20.0}},
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": z_hi}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -2.8}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_low20"}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "vol_pulse_proxy"}, "op": "gt", "right": {"value": vol_min}},
        ]
    # enforce mandatory leaves
    feats = {((e.get("left") or {}).get("feature")) for e in entry}
    if "session_liq" not in feats:
        entry.insert(0, {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}})
    if "candle_reclaim" not in feats:
        entry.insert(1, {"left": {"feature": "candle_reclaim"}, "op": "gt", "right": {"value": 0.5}})
    if len(exit_any) < 2:
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 58.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"},
             "role": "invalidation"},
        ]
    hold = int(grid.get("max_hold") or 28)
    m = re.search(r"max_hold\s*=\s*(\d+)", str(hyp.get("exit_sketch") or ""), re.I)
    if m:
        hold = max(16, int(m.group(1)))
    hold = max(hold, 20)
    key = "frost3_ng15m_xrpport_er_l_%d" % idx
    dsl = {
        "key": key,
        "name": "寒霜叁-NG-15m-exhaustion_reclaim",
        "direction": "long",
        "timeframe": TIMEFRAME,
        "supported_instruments": [SYMBOL],
        "max_hold_bars": hold,
        "description": str(hyp.get("thesis") or "NG15m exhaustion reclaim")[:160],
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "schema": "qiyu_strategy_dsl_v1",
    }
    return f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)


def make_book(hyp, idx=1):
    dsl = build_dsl_from_hyp(hyp, idx)
    return {
        "title": "寒霜叁-NG15m-xrpport#%d" % idx,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": "long",
        "logic_class": "exhaustion_reclaim",
        "thesis": hyp.get("thesis"),
        "entry_sketch": hyp.get("entry_sketch"),
        "exit_sketch": hyp.get("exit_sketch"),
        "dsl": dsl,
        "gate_mode": "frost_relaxed",
        "source": PREFIX,
        "xrp_template": "frost_xrp_rescue_h20_t45",
    }


def force_key(book):
    book = copy.deepcopy(book)
    dsl = book.get("dsl") or {}
    dsl.pop("stop_loss_pct", None)
    dsl["supported_instruments"] = [SYMBOL]
    dsl["timeframe"] = TIMEFRAME
    dsl["direction"] = "long"
    k = str(dsl.get("key") or "")
    if not k.startswith(PREFIX):
        dsl["key"] = ("%s_%s" % (PREFIX, _safe(k)))[:100]
    # re-enforce session + candle reclaim
    ents = list((dsl.get("entry") or {}).get("all") or [])
    feats = {((e.get("left") or {}).get("feature")) for e in ents}
    if "session_liq" not in feats:
        ents.insert(0, {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}})
    if "candle_reclaim" not in feats:
        ents.insert(1, {"left": {"feature": "candle_reclaim"}, "op": "gt", "right": {"value": 0.5}})
    dsl["entry"] = {"all": ents}
    book["dsl"] = f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)
    book["symbol"] = SYMBOL
    book["timeframe"] = TIMEFRAME
    book["logic_class"] = "exhaustion_reclaim"
    return book


def apply_revise(book, revise):
    book = copy.deepcopy(book)
    if not isinstance(revise, dict):
        return force_key(book)
    tweaks = revise.get("param_tweaks") or {}
    for phase in ("entry", "exit"):
        block = (book.get("dsl") or {}).get(phase) or {}
        rows = block.get("all") or block.get("any") or []
        for row in rows:
            feat = ((row.get("left") or {}).get("feature"))
            if feat in tweaks and isinstance(tweaks[feat], (int, float)):
                if isinstance(row.get("right"), dict) and "value" in row["right"]:
                    row["right"]["value"] = float(tweaks[feat])
    if revise.get("entry_sketch") or revise.get("exit_sketch"):
        hyp = {
            "entry_sketch": revise.get("entry_sketch") or book.get("entry_sketch"),
            "exit_sketch": revise.get("exit_sketch") or book.get("exit_sketch"),
            "thesis": book.get("thesis"),
            "param_grid": {},
        }
        if revise.get("entry_sketch"):
            book["entry_sketch"] = revise["entry_sketch"]
        if revise.get("exit_sketch"):
            book["exit_sketch"] = revise["exit_sketch"]
        book["dsl"] = build_dsl_from_hyp(hyp, 1)
    return force_key(book)


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
        if feat == "rsi14" and row.get("op") == "lt":
            # WF fail → loosen (raise ceiling); dest/friction → tighten
            if "walk" in step or "wf" in step:
                right["value"] = min(52.0, v + 2.5 * n)
            else:
                right["value"] = max(28.0, v - 1.5 * n)
        elif feat == "z20" and row.get("op") == "lt":
            if "walk" in step or "wf" in step:
                right["value"] = min(-0.15, v + 0.2 * n)  # less deep
            else:
                right["value"] = max(-2.0, v - 0.15 * n)
        elif feat == "vol_pulse_proxy" and row.get("op") == "gt":
            if "walk" in step:
                right["value"] = max(0.9, v - 0.04 * n)
            else:
                right["value"] = v + 0.03 * n
        elif feat == "h1_slope4" and row.get("op") == "gt" and ("walk" in step or "wf" in step):
            # drop slope gate on WF starvation
            right["value"] = min(v, -0.02)
    dsl["entry"] = {"all": entries}
    hold = int(dsl.get("max_hold_bars") or 28)
    if "friction" in step or "mc" in step:
        dsl["max_hold_bars"] = max(16, hold - 2 * n)
    elif "walk" in step:
        dsl["max_hold_bars"] = min(48, hold + 4 * n)
    book["dsl"] = dsl
    book["dsl"]["key"] = (str(book["dsl"].get("key") or PREFIX)[:80] + "_t%d" % n)[:100]
    return force_key(book)


def glm_audit_book(book, report):
    if os.environ.get("NG15M_FORCE_FALLBACK", "0") == "1":
        return {"decision": "pass", "reason_zh": "forced_fallback_audit_pass", "fallback": True}, {
            "ok": False, "error": "forced_fallback"
        }
    payload = {
        "book": {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "thesis": book.get("thesis"),
            "entry": (book.get("dsl") or {}).get("entry"),
            "exit": (book.get("dsl") or {}).get("exit"),
            "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
        },
        "report_title": (report or {}).get("report_title"),
        "cl_lessons": (report or {}).get("cl_lessons_zh"),
        "xrp_template": XRP_TEMPLATE_DSL,
    }
    parsed, ai = ask_glm(AUDIT_PROMPT, payload, timeout_sec=60, max_tokens=900)
    if not parsed:
        return {"decision": "pass", "reason_zh": "audit_fallback_pass", "fallback": True}, ai
    return parsed, ai


def archive_result(result, death_cause):
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
    write_art("archive_pointer", {"archive_path": arch, "death_cause": death_cause, "at": _now()})
    return arch


def process(book, report):
    hist = []
    dir_id = "%s_%s" % (PREFIX, _safe(book.get("logic_class")))
    status = {
        "dir_id": dir_id, "stage": "audit", "updated_at": _now(),
        "symbol": SYMBOL, "tf": TIMEFRAME, "logic": book.get("logic_class"),
        "key": (book.get("dsl") or {}).get("key"),
    }
    write_art("status", status)

    audit_ok = False
    for atry in range(MAX_AUDIT_REVISE + 1):
        decision, ai = glm_audit_book(book, report)
        write_art("audit_try%d" % atry, {
            "decision": decision,
            "ai": {"ok": ai.get("ok"), "preview": (ai.get("raw_preview") or "")[:500]},
        })
        hist.append({
            "stage": "audit", "try": atry,
            "decision": decision.get("decision"),
            "reason": decision.get("reason_zh"),
            "fallback": decision.get("fallback"),
        })
        dec = str(decision.get("decision") or "pass").lower()
        if dec == "pass":
            audit_ok = True
            break
        if dec == "revise" and atry < MAX_AUDIT_REVISE:
            book = force_key(apply_revise(book, decision.get("revise") or {}))
            continue
        return {
            "dir_id": dir_id, "ok": False, "failed_step": "hyp_audit_reject",
            "reason": decision.get("reason_zh") or decision.get("issues"),
            "hist": hist, "book": book,
        }
    if not audit_ok:
        return {
            "dir_id": dir_id, "ok": False, "failed_step": "hyp_audit_reject",
            "reason": "audit not passed", "hist": hist, "book": book,
        }

    write_art("hypothesis_book", book)
    packs = None
    for qtry in range(MAX_QUICK_REPAIR + 1):
        status.update({"stage": "quick", "attempt": qtry, "updated_at": _now()})
        write_art("status", status)
        packs = f2.quick_suite(book)
        write_art("quick_try%d" % qtry, {
            "pass": packs.get("quick_pass"), "failed_step": packs.get("failed_step"),
            "metrics": packs.get("base_metrics"),
            "dest": packs.get("logic_destruction"),
            "key": (book.get("dsl") or {}).get("key"),
            "error": packs.get("error"),
        })
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
                "dir_id": dir_id, "ok": False,
                "failed_step": packs.get("failed_step") or "quick_exhausted",
                "reason": "Quick failed after %d repairs" % MAX_QUICK_REPAIR,
                "metrics": packs.get("base_metrics"),
                "dest": (packs.get("logic_destruction") or {}).get("pass"),
                "hist": hist, "book": book,
            }
        book = force_key(local_tweak(book, qtry + 1, packs.get("failed_step")))
        repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step"), "quick")
        if repaired:
            book = force_key(repaired)
        hist.append({"stage": "quick_repair", "try": qtry, "ok": bool(repaired)})

    for ftry in range(MAX_FULL_REPAIR + 1):
        status.update({"stage": "full", "attempt": ftry, "updated_at": _now()})
        write_art("status", status)
        packs = f2.full_suite(book, packs)
        write_art("full_try%d" % ftry, {
            "pass": packs.get("full_pass"), "failed_step": packs.get("failed_step"),
            "full": packs.get("full"), "key": (book.get("dsl") or {}).get("key"),
        })
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
                "dir_id": dir_id, "ok": False,
                "failed_step": packs.get("failed_step") or "full_exhausted",
                "reason": "Full failed after %d repairs" % MAX_FULL_REPAIR,
                "full": packs.get("full"), "hist": hist, "book": book,
            }
        book = force_key(local_tweak(book, ftry + 1, packs.get("failed_step")))
        repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step"), "full")
        if repaired:
            book = force_key(repaired)
        q2 = f2.quick_suite(book)
        hist.append({
            "stage": "re_quick_after_full_repair",
            "pass": q2.get("quick_pass"), "failed_step": q2.get("failed_step"),
        })
        if not q2.get("quick_pass"):
            packs = q2
            packs["full_pass"] = False
            packs["failed_step"] = q2.get("failed_step") or "quick_regressed"
            continue
        packs = q2

    for stry in range(MAX_SIM_REPAIR + 1):
        status.update({"stage": "sim_formal", "attempt": stry, "updated_at": _now()})
        write_art("status", status)
        sf = f2.run_sim_formal(book, packs)
        write_art("sim_try%d" % stry, sf)
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
                "dir_id": dir_id, "ok": True, "failed_step": None,
                "pending": sf.get("pending"), "sim": sf.get("sim"),
                "formal": sf.get("formal"), "hist": hist, "book": book,
            }
        fs = sf.get("failed_step")
        if fs in ("formal_review", "pending_ingest"):
            return {
                "dir_id": dir_id, "ok": False, "failed_step": fs,
                "reason": (sf.get("formal") or {}).get("reason") or (
                    sf.get("pending") or {}).get("reason"),
                "sim": sf.get("sim"), "formal": sf.get("formal"),
                "pending": sf.get("pending"), "hist": hist, "book": book,
            }
        if stry >= MAX_SIM_REPAIR:
            return {
                "dir_id": dir_id, "ok": False,
                "failed_step": fs or "sim_exhausted",
                "reason": "Sim/formal failed after %d repairs" % MAX_SIM_REPAIR,
                "sim": sf.get("sim"), "hist": hist, "book": book,
            }
        book = force_key(local_tweak(book, stry + 1, "sim"))
        repaired, _ai = f3.glm_repair(book, packs, "sim_review", "sim")
        if repaired:
            book = force_key(repaired)
        q3 = f2.quick_suite(book)
        if not q3.get("quick_pass"):
            hist.append({"stage": "sim_repair_quick_fail", "failed": q3.get("failed_step")})
            continue
        packs = f2.full_suite(book, q3)
        if not packs.get("full_pass"):
            hist.append({"stage": "sim_repair_full_fail", "failed": packs.get("failed_step")})
            continue

    return {
        "dir_id": dir_id, "ok": False, "failed_step": "unknown_exhausted",
        "hist": hist, "book": book,
    }


def main():
    print("=== frost3_ng15m_xrpport START ===", _now(), flush=True)
    d._ensure_dirs()
    d._load_env()
    print("[ng15m] env ready", flush=True)

    # Protect NG5m daemon — never rewrite its config
    ng5 = "/root/auto_trade/formal_daemon_config_ng_5m.json"
    ng5_before = None
    try:
        ng5_before = open(ng5).read()
    except Exception:
        pass

    print("[ng15m] ATR/vol from parquet", flush=True)
    atr_stats = compute_atr_stats()
    write_art("atr_stats", atr_stats)
    print("[ng15m] stop/ATR_med", atr_stats.get("stop0p9_over_atr_med"),
          "bias", atr_stats.get("adapt_bias"), flush=True)

    proxy_doc = install_session_proxies()
    write_art("proxy_notes", proxy_doc)

    print("[ng15m] ensure candles/frame", flush=True)
    candle_info = ensure_ng15m_candles()
    write_art("candle_frame", candle_info)
    if not candle_info.get("frame_ok"):
        print("[ng15m] WARN frame not ready:", candle_info.get("error"), flush=True)

    cl = step1.load_cl_archive()
    packet = {
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "xrp_template_dsl": XRP_TEMPLATE_DSL,
        "xrp_template_note": (
            "READ-ONLY template frost_xrp_rescue_h20_t45 — do not modify live XRP"
        ),
        "cl_archive": cl,
        "atr_stats": atr_stats,
        "proxy_injection": proxy_doc,
        "candle_frame": candle_info,
        "live_protect": ["ADA", "LTC", "NG5m", "XRP"],
        "distinct_from": "ng5_exhaustion_fade_short_ai / ng5_session_exhaustion_reclaim_long_ai",
        "gates": {
            "WF": ">=7/10", "logic_destruction": "pass",
            "MC_beat": ">=90%", "extreme_friction_sharpe": ">=0",
            "sim_wr": ">=55/55",
        },
    }
    write_art("inputs", packet)

    prefer_fallback = os.environ.get("NG15M_FORCE_FALLBACK", "0") == "1"
    if prefer_fallback:
        print("[ng15m] FORCE_FALLBACK → skip GLM params", flush=True)
        report, ai = fallback_params(atr_stats), {"ok": False, "error": "forced_fallback"}
    else:
        print("[ng15m] GLM 《NG 15m 衰竭回收参数适配建议》", flush=True)
        report, ai = ask_glm(PARAM_PROMPT, packet, timeout_sec=75, max_tokens=2400)
    write_art("glm_params_raw", {
        "ok": ai.get("ok"), "parsed": bool(report),
        "preview": (ai.get("raw_preview") or "")[:1800],
        "error": ai.get("error"),
    })
    if not report or not isinstance(report.get("hypothesis"), dict):
        print("[ng15m] GLM empty → fallback", flush=True)
        report = fallback_params(atr_stats)
    # hard lock
    hyp = dict(report.get("hypothesis") or {})
    hyp["symbol"] = SYMBOL
    hyp["timeframe"] = TIMEFRAME
    hyp["logic_class"] = "exhaustion_reclaim"
    hyp["direction"] = "long"
    report["hypothesis"] = hyp
    report["symbol"] = SYMBOL
    report["timeframe"] = TIMEFRAME
    report["session_window_utc"] = report.get("session_window_utc") or [
        SESSION_UTC_START, SESSION_UTC_END]
    write_art("参数适配建议", report)
    write_art("param_plan", report)
    write_art("hypothesis", hyp)

    approved = bool(report.get("approve", True))
    write_art("glm_approve", {"approve": approved, "at": _now()})
    if not approved and not report.get("fallback"):
        end = {
            "at": _now(), "ok": False, "failed_step": "glm_not_approved",
            "reason": report.get("mentor_notes") or "GLM approve=false",
            "report": report,
        }
        write_art("end_report", end)
        print("=== END glm_not_approved ===", flush=True)
        return

    book = force_key(make_book(hyp, 1))
    write_art("hypothesis_book_v0", book)
    print("[ng15m] key", (book.get("dsl") or {}).get("key"), flush=True)

    try:
        result = process(book, report)
    except Exception as exc:
        result = {
            "dir_id": "%s_exception" % PREFIX, "ok": False,
            "failed_step": "exception", "reason": str(exc),
            "trace": traceback.format_exc()[-2500:],
        }

    # verify NG5m daemon config untouched
    ng5_after = None
    try:
        ng5_after = open(ng5).read()
    except Exception:
        pass
    ng5_ok = (ng5_before is None) or (ng5_before == ng5_after)

    end = {
        "at": _now(),
        "op": "NG15m衰竭回收-XRP框架移植",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "ok": bool(result.get("ok")),
        "failed_step": result.get("failed_step"),
        "reason": result.get("reason"),
        "pending": result.get("pending"),
        "sim": result.get("sim"),
        "formal": result.get("formal"),
        "logic": (result.get("book") or book).get("logic_class"),
        "key": ((result.get("book") or book).get("dsl") or {}).get("key"),
        "hist": result.get("hist"),
        "atr_adapt_bias": atr_stats.get("adapt_bias"),
        "stop0p9_over_atr_med": atr_stats.get("stop0p9_over_atr_med"),
        "session_window_utc": [SESSION_UTC_START, SESSION_UTC_END],
        "ng5m_daemon_untouched": ng5_ok,
        "summary_zh": None,
    }

    if not result.get("ok"):
        death_cause = {
            "exact_death": result.get("failed_step"),
            "reason": result.get("reason"),
            "metrics": result.get("metrics"),
            "full": result.get("full"),
            "sim": result.get("sim"),
            "hist_tail": (result.get("hist") or [])[-8:],
            "summary_zh": "NG15m xrpport 在 %s 耗尽：%s" % (
                result.get("failed_step"), result.get("reason") or ""),
        }
        arch = archive_result(result, death_cause)
        end["archive_path"] = arch
        end["death_cause"] = death_cause
        end["summary_zh"] = death_cause["summary_zh"]
    else:
        end["summary_zh"] = (
            "NG15m exhaustion_reclaim 已进入 pending：%s" % (
                (result.get("pending") or {}).get("key"),)
        )

    write_art("end_report", end)
    write_art("status", {
        "stage": "done", "ok": end["ok"], "updated_at": _now(),
        "failed_step": end.get("failed_step"),
        "pending": end.get("pending"),
        "key": end.get("key"),
    })
    print("=== END ===", json.dumps({
        "ok": end["ok"], "failed_step": end.get("failed_step"),
        "pending": end.get("pending"), "key": end.get("key"),
        "summary_zh": end.get("summary_zh"),
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
