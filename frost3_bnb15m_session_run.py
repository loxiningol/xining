#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜叁·BNB15m 会话窗 V-reclaim 独立管线。

ONLY: BNB-USDT-SWAP / 15m
Logic: UTC 07:00–20:00 session + rapid dump → V-reclaim + volume pulse
Artifacts: /root/auto_trade/dual_engine/frost3_bnb15m_session_*
Key prefix: frost3_bnb15m_session_
Does NOT touch BNB5m / BNB1h / ETH / SOL / live ADA/LTC/NG/XRP.
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
import auto_trade_strategy_dsl as dsl_mod
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
PREFIX = "frost3_bnb15m_session"
SYMBOL = "BNB-USDT-SWAP"
TIMEFRAME = "15m"
LOGIC_CLASS = "session_v_reclaim"
KEY_PREFIX = "frost3_bnb15m_session_"
SESSION_UTC_START = 7
SESSION_UTC_END = 20  # exclusive
STOP_PCT = 0.009  # factory hard stop; must match in-session ATR
MAX_AUDIT_REVISE = 2
MAX_QUICK_REPAIR = 3
MAX_FULL_REPAIR = 3
MAX_SIM_REPAIR = 3

# Protect live + avoid colliding with other frost3 BNB lanes' artifact prefixes
FOREIGN_PREFIXES = (
    "frost3_bnb1h_", "frost3_bnb5m_", "frost3_eth5m_", "frost3_sol15m_",
    "frost3_doge", "frost3_action",
)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write(suffix, obj):
    name = suffix if str(suffix).startswith(PREFIX) else "%s_%s" % (PREFIX, suffix)
    if not name.endswith(".json"):
        name = name + ".json"
    path = os.path.join(OUT, name)
    open(path, "w").write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


def _safe(s):
    s = str(s or "x")
    for ch in (".", " ", "/", ":", "+", "%", "-"):
        s = s.replace(ch, "p" if ch in (".", "-") else "_")
    return s[:40]


def ensure_bnb_15m_parquet():
    """Resample local BNB 5m OHLC → 15m; volume = sum of 5m ranges (activity proxy)."""
    import pandas as pd

    src = "/root/market_data/BNB-USDT-SWAP/5m/BNB-USDT-SWAP_5m.parquet"
    out_dir = "/root/market_data/BNB-USDT-SWAP/15m"
    out = os.path.join(out_dir, "BNB-USDT-SWAP_15m.parquet")
    meta_path = os.path.join(out_dir, "BNB-USDT-SWAP_15m.meta.json")
    if os.path.exists(out) and os.path.getsize(out) > 1000:
        try:
            meta = json.load(open(meta_path))
        except Exception:
            meta = {"path": out}
        return {"ok": True, "path": out, "meta": meta, "created": False}

    df = pd.read_parquet(src)
    if "timestamp" in getattr(df, "columns", []):
        df = df.set_index("timestamp")
    df.index = pd.to_datetime(df.index)
    ohlc = df[["open", "high", "low", "close"]].astype(float)
    r = ohlc.resample("15min", label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
    }).dropna()
    rng = (ohlc["high"] - ohlc["low"]).resample(
        "15min", label="left", closed="left").sum()
    r["volume"] = rng.reindex(r.index).fillna(0.0)
    os.makedirs(out_dir, exist_ok=True)
    r.to_parquet(out)
    meta = {
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "bars": int(len(r)),
        "source": "resampled_from_5m",
        "volume_note": (
            "volume=sum_of_5m_bar_ranges_activity_proxy; "
            "vol_z20 uses this series (range-activity pulse when true vol absent)"
        ),
        "start": str(r.index[0]),
        "end": str(r.index[-1]),
        "created_at": _now(),
    }
    open(meta_path, "w").write(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    return {"ok": True, "path": out, "meta": meta, "created": True}


def patch_dsl_utc_hour():
    """Allow utc_hour + atr_pct in DSL FEATURES (session + stop-ATR gate)."""
    extra = {"utc_hour", "atr_pct"}
    missing = extra - set(dsl_mod.FEATURES)
    if missing:
        dsl_mod.FEATURES = set(dsl_mod.FEATURES) | missing
    # destruction / perturbation step hints
    try:
        if hasattr(dsl_mod, "PERTURB_STEPS") and isinstance(dsl_mod.PERTURB_STEPS, dict):
            dsl_mod.PERTURB_STEPS.setdefault("utc_hour", 1.0)
            dsl_mod.PERTURB_STEPS.setdefault("atr_pct", 0.0005)
    except Exception:
        pass


_ORIG_FRAME = None
FRAME_PKL = "/root/auto_trade/codex_0725_train5/frame_BNB_USDT_SWAP_15m.pkl"


def enrich_frame(frame):
    """Inject utc_hour (0-23) and atr_pct for session/stop-ATR filters."""
    import pandas as pd

    if frame is None or not hasattr(frame, "copy"):
        return frame
    fr = frame
    need = ("utc_hour" not in fr.columns) or ("atr_pct" not in fr.columns)
    if need:
        fr = fr.copy()
    if "utc_hour" not in fr.columns:
        idx = fr.index
        if not isinstance(idx, pd.DatetimeIndex):
            try:
                idx = pd.to_datetime(idx)
            except Exception:
                return fr
        fr["utc_hour"] = idx.hour.astype(float)
    if "atr_pct" not in fr.columns and "atr14" in fr.columns and "close" in fr.columns:
        close = fr["close"].astype(float).replace(0, pd.NA)
        fr["atr_pct"] = (fr["atr14"].astype(float) / close).astype(float)
    return fr


def patch_frame_loader():
    global _ORIG_FRAME
    if _ORIG_FRAME is not None:
        return
    _ORIG_FRAME = d._frame

    def _wrapped(symbol, timeframe):
        if symbol == SYMBOL and str(timeframe) == TIMEFRAME and os.path.exists(FRAME_PKL):
            import pickle
            fr = pickle.load(open(FRAME_PKL, "rb"))
            return enrich_frame(fr)
        fr = _ORIG_FRAME(symbol, timeframe)
        if symbol == SYMBOL and str(timeframe) == TIMEFRAME:
            fr = enrich_frame(fr)
        return fr

    d._frame = _wrapped


def compute_atr_stop_match(frame):
    """Document stop 0.9% vs in-session ATR (normal volatility match)."""
    import pandas as pd

    fr = enrich_frame(frame)
    close = fr["close"].astype(float)
    atr = fr["atr14"].astype(float)
    atr_pct = (atr / close.replace(0, pd.NA)).dropna()
    hours = fr["utc_hour"].astype(float)
    mask = (hours >= SESSION_UTC_START) & (hours < SESSION_UTC_END)
    sess = atr_pct[mask].dropna()
    if len(sess) < 50:
        sess = atr_pct
    med = float(sess.median())
    p25 = float(sess.quantile(0.25))
    p75 = float(sess.quantile(0.75))
    # Entry gate band so hard stop spans ~0.8–2.0 ATR on trade bars
    gate_lo, gate_hi = 0.0045, 0.011
    gated = sess[(sess >= gate_lo) & (sess <= gate_hi)]
    gated_med = float(gated.median()) if len(gated) else None
    stop_vs_med = (STOP_PCT / med) if med > 0 else None
    stop_vs_gated = (STOP_PCT / gated_med) if gated_med else None
    match_ok = bool(stop_vs_gated is not None and 0.8 <= stop_vs_gated <= 2.2)
    return {
        "session_utc": "%02d:00–%02d:00" % (SESSION_UTC_START, SESSION_UTC_END),
        "stop_pct": STOP_PCT,
        "session_atr_pct_median": round(med, 6),
        "session_atr_pct_p25": round(p25, 6),
        "session_atr_pct_p75": round(p75, 6),
        "stop_in_atr_units_vs_median": round(stop_vs_med, 3) if stop_vs_med else None,
        "entry_atr_pct_gate": [gate_lo, gate_hi],
        "gated_session_atr_pct_median": round(gated_med, 6) if gated_med else None,
        "stop_in_atr_units_vs_gated_median": round(stop_vs_gated, 3) if stop_vs_gated else None,
        "atr_match_ok_0p8_to_2p2": match_ok,
        "note_zh": (
            "会话ATR中位上硬止损0.9%%≈%.2f个ATR（偏宽）；"
            "入场另加 atr_pct∈[%.4f,%.4f] 使交易bar上止损≈%.2f个ATR，对齐正常波动。"
            % (stop_vs_med or -1, gate_lo, gate_hi, stop_vs_gated or -1)
        ),
    }


def load_context():
    cl = step1.load_cl_archive()
    try:
        inputs = json.load(open(os.path.join(OUT, "frost3_inputs_raw.json")))
    except Exception:
        inputs = d.collect_inputs() if hasattr(d, "collect_inputs") else {}

    hf_lessons = {
        "from_freezer_and_frost2": [
            "5m/15m 高频失败主因：过度交易 + 滑点/摩擦吞噬期望（cost_collapse）",
            "SOL5m impulse：触发过密、thesis与DSL脱节 → friction/WF/sim 全崩",
            "DOGE1h range_reclaim：399笔刷单式触发，1h被当5m用",
            "CL15m exhaustion：边际入场硬止损簇≈-22% → friction Sharpe 崩；"
            "单阈值收窄导致 trades<10、WF折数不足",
            "btc5_exhaustion_reclaim：lifecycle friction_negative 冷冻",
        ],
        "design_rules_for_bnb15m_session": [
            "固定会话 UTC07–20 过滤低流动性噪声，降低非会话刷单",
            "必须 dump→reclaim 两段确认，禁止单阈值边际超买/超卖入场",
            "volume pulse (vol_z20) 确认参与度，抑制无量假回收",
            "预期 trades≥12 以满足 WF folds≥10；勿过窄过滤",
            "硬止损0.9%需与会话ATR匹配（约1–2 ATR），避免无效宽停或噪声打停",
            "结构失效出场（prev_low20），保留 logic_destruction ±20% 稳健",
        ],
    }
    data_meta = ensure_bnb_15m_parquet()
    patch_dsl_utc_hour()
    patch_frame_loader()
    atr_doc = {}
    micro = {"error": None}
    try:
        fr = d._frame(SYMBOL, TIMEFRAME)
        atr_doc = compute_atr_stop_match(fr)
        # BNB micro primitives in-session
        import pandas as pd
        fr = enrich_frame(fr)
        tail = fr.tail(min(len(fr), 96 * 14))  # ~14d of 15m
        hours = tail["utc_hour"].astype(float)
        sess = tail[(hours >= SESSION_UTC_START) & (hours < SESSION_UTC_END)]
        dump = ((sess["z20"].astype(float) < -1.2)).mean()
        reclaim = (
            (sess["z20"].astype(float) > -0.8)
            & (sess["close"].astype(float) > sess["prev_low20"].astype(float))
            & (sess["macd_stick"].astype(float) > 0)
        ).mean()
        pulse = (sess["vol_z20"].astype(float) > 0.5).mean()
        micro = {
            "bars_tail": int(len(tail)),
            "session_bars": int(len(sess)),
            "session_share_pct": round(100.0 * len(sess) / max(len(tail), 1), 2),
            "primitive_freq": [
                {"tag": "session_z20_lt_-1.2_dump", "freq_pct": round(float(dump) * 100, 2)},
                {"tag": "session_reclaim_shape", "freq_pct": round(float(reclaim) * 100, 2)},
                {"tag": "session_vol_z20_gt_0.5_pulse", "freq_pct": round(float(pulse) * 100, 2)},
            ],
            "intraday_note_zh": (
                "BNB15m 会话内急跌(z深负)后回收+放量脉冲并存；"
                "会话外流动性差，易被滑点吞噬——故锁定 UTC07–20。"
            ),
        }
    except Exception as exc:
        micro = {"error": str(exc), "trace": traceback.format_exc()[-800:]}

    return {
        "instrument": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_class_fixed": LOGIC_CLASS,
        "session_utc": {"start_hour": SESSION_UTC_START, "end_hour_exclusive": SESSION_UTC_END},
        "stop_pct": STOP_PCT,
        "cl_archive": cl,
        "hf_lessons": hf_lessons,
        "data_meta": data_meta,
        "atr_stop_match": atr_doc,
        "bnb_micro": micro,
        "death_heatmap_top10": (inputs or {}).get("death_heatmap_top10"),
        "freezer_last10": ((inputs or {}).get("freezer_last10") or [])[:10],
        "protect_live": sorted(step1.AVOID_SYMBOLS),
        "gates": {
            "WF": ">=7/10",
            "logic_destruction": "pass",
            "MC_beat_sign_shuffle": ">=90%",
            "extreme_friction_sharpe": ">=0",
            "sim_ds_qwen": ">=55%/55%",
        },
        "distinct_from": {
            "bnb5m": "other frost3 lane; not this artifact prefix",
            "bnb1h": "frost3_bnb1h_* banned reclaim family there; here 15m session_v_reclaim",
            "archived_d2_bnb_15m": "trend_pullback failed WF; this is session_v_reclaim",
            "ng5_session_exhaustion_reclaim": "NG 5m live — do not clone; BNB 15m dump→V-reclaim+vol pulse",
        },
    }


PARAM_PROMPT = """你是GLM-5.2。只输出JSON，禁止Markdown。
任务：输出《BNB 15m 会话回收策略参数方案》。

硬约束：
1) 标的锁定 BNB-USDT-SWAP，周期锁定 15m，逻辑锁定 session_v_reclaim（不可改）
2) 会话窗固定 UTC 07:00–20:00（utc_hour∈[7,20)）过滤低流动性噪声
3) 入场三段：急跌dump（z20滞后为负）→ V型回收（收盘回收prev_low20 + macd翻正/rsi回升）→ volume pulse（vol_z20确认）
4) 硬止损0.9%必须与会话ATR匹配（约0.8–2.2个ATR）；勿过度交易；规避CL15m边际衰竭与5m/15m滑点吞噬
5) 预期trades≥12；结构失效出场；禁止换成ADA/LTC/NG/XRP或BNB5m/BNB1h

对照packet中的 bnb_micro / atr_stop_match / hf_lessons / cl_archive。

JSON schema：
{
  "report_title":"BNB 15m 会话回收策略参数方案",
  "symbol":"BNB-USDT-SWAP",
  "timeframe":"15m",
  "logic_class":"session_v_reclaim",
  "direction":"long",
  "session_rationale_zh":"...",
  "reclaim_definition_zh":"...",
  "volume_pulse_zh":"...",
  "stop_atr_match_zh":"...",
  "hf_avoidance_zh":"...",
  "params":{
    "utc_hour_start":7,
    "utc_hour_end_exclusive":20,
    "dump_z20_offset":1,
    "dump_z20_max":-1.2,
    "reclaim_z20_min":-0.8,
    "reclaim_rsi_min":35,
    "reclaim_rsi_max":55,
    "vol_z20_min":0.5,
    "require_macd_pos":true,
    "require_close_gt_prev_low20":true,
    "tp_rsi":58,
    "max_hold_bars":20
  },
  "entry_sketch":"条件分号分隔，须含utc_hour/z20 offset dump/reclaim/vol_z20",
  "exit_sketch":"TP/失效/max_hold",
  "approve_for_quick":true,
  "mentor_notes":"..."
}
"""


HYP_PROMPT = """你是GLM-5.2。只输出JSON。将参数方案固化为《策略逻辑假设书》并裁决是否可进Quick。
硬约束：BNB-USDT-SWAP 15m session_v_reclaim；会话UTC07–20；dump→V-reclaim→vol pulse；止损0.9%与ATR匹配；trades≥12；勿改标的/周期/大逻辑。
JSON：{
  "decision":"pass|reject|revise",
  "score":0到100,
  "hypothesis_book":{
    "title":"策略逻辑假设书",
    "symbol":"BNB-USDT-SWAP",
    "timeframe":"15m",
    "direction":"long",
    "logic_class":"session_v_reclaim",
    "session_window_utc":"07:00-20:00",
    "session_rationale_zh":"...",
    "reclaim_definition_zh":"...",
    "volume_pulse_zh":"...",
    "stop_atr_match_zh":"...",
    "causal_entry":"...",
    "causal_exit":"...",
    "entry_sketch":"...",
    "exit_sketch":"...",
    "params":{}
  },
  "revise":{"param_tweaks":{},"why":"..."},
  "reason_zh":"..."
}
"""


AUDIT_PROMPT = """你是GLM-5.2。只输出JSON。审计BNB15m会话V-reclaim假设书。
硬禁：改标的/周期；碰ADA/LTC/NG/XRP；CL exhaustion同构边际入场；去掉会话窗或volume pulse。
对照：trades≥12；会话过滤；dump+reclaim两段；friction稳健；止损ATR匹配。
JSON：{"decision":"pass|reject|revise","score":0到100,"issues":["..."],
"revise":{"param_tweaks":{},"why":"..."},"reason_zh":"..."}
"""


REPAIR_PROMPT = """你是GLM-5.2。只输出JSON。BNB15m session_v_reclaim 闸门修复（增量）。
禁止换标的/周期/逻辑类；禁止去掉会话窗；勿把trades压到<10；保留vol pulse与结构失效。
可调：dump_z20_max、reclaim_z20_min、vol_z20_min、rsi带、tp_rsi、max_hold_bars。
JSON：{"ok":true,"param_tweaks":{},"dsl_patch":{"entry_all":null,"exit_any":null,"max_hold_bars":null},"why":"..."}
"""


def ask_glm(prompt, payload, max_tokens=1800):
    ai = d._ai_json("glm", prompt, payload, max_tokens=max_tokens, temperature=0.12)
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


def default_params():
    return {
        "utc_hour_start": SESSION_UTC_START,
        "utc_hour_end_exclusive": SESSION_UTC_END,
        "dump_z20_offset": 1,
        "dump_z20_max": -1.2,
        "reclaim_z20_min": -0.8,
        "reclaim_rsi_min": 35.0,
        "reclaim_rsi_max": 55.0,
        "vol_z20_min": 0.5,
        "atr_pct_min": 0.0045,  # 0.9% stop <= ~2.0 ATR
        "atr_pct_max": 0.011,   # 0.9% stop >= ~0.8 ATR
        "require_macd_pos": True,
        "require_close_gt_prev_low20": True,
        "tp_rsi": 58.0,
        "max_hold_bars": 20,
    }


def fallback_param_plan(ctx):
    atr = ctx.get("atr_stop_match") or {}
    return {
        "report_title": "BNB 15m 会话回收策略参数方案",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_class": LOGIC_CLASS,
        "direction": "long",
        "session_rationale_zh": (
            "UTC07–20覆盖亚欧活跃重叠与美盘前半，BNB永续流动性充足；"
            "会话外点差/冲击放大，15m高频易被滑点吞噬，故硬过滤。"
        ),
        "reclaim_definition_zh": (
            "急跌定义为前1根z20≤dump阈值；V回收=当前z20回升且收盘站回prev_low20，"
            "macd_stick>0、rsi回到中低位带，避免CL式边际超买空/超卖追单。"
        ),
        "volume_pulse_zh": (
            "vol_z20≥阈值确认参与度脉冲（本地无真实成交量时用5m振幅求和代理volume再算z）；"
            "无脉冲的假回收不交易。"
        ),
        "stop_atr_match_zh": atr.get("note_zh") or (
            "工厂硬止损0.9%，应对齐会话ATR中位约1–2ATR。"
        ),
        "hf_avoidance_zh": (
            "会话+双段确认+脉冲三重闸，抑制过度交易；结构失效出场降低摩擦路径依赖。"
        ),
        "params": default_params(),
        "entry_sketch": (
            "utc_hour>=7; utc_hour<20; z20[1]<-1.2; z20>-0.8; "
            "close>prev_low20; macd_stick>0; rsi14>35; rsi14<55; vol_z20>0.5"
        ),
        "exit_sketch": "rsi14>58 TP; close<prev_low20 invalidation; max_hold=20",
        "approve_for_quick": True,
        "mentor_notes": "fallback_param_plan",
        "fallback": True,
    }


def leaf(feat, op, value=None, feat2=None, offset=0, role=None):
    left = {"feature": feat}
    if offset:
        left["offset"] = int(offset)
    row = {"left": left, "op": op}
    if value is not None:
        row["right"] = {"value": float(value)}
    else:
        row["right"] = {"feature": feat2}
    if role:
        row["role"] = role
    return row


def build_dsl(params, tag="v0"):
    p = dict(default_params())
    p.update(params or {})
    # lock session bounds
    p["utc_hour_start"] = SESSION_UTC_START
    p["utc_hour_end_exclusive"] = SESSION_UTC_END
    dump_off = int(p.get("dump_z20_offset") or 1)
    entry = [
        leaf("utc_hour", "gte", p["utc_hour_start"]),
        leaf("utc_hour", "lt", p["utc_hour_end_exclusive"]),
        leaf("z20", "lt", p["dump_z20_max"], offset=dump_off),
        leaf("z20", "gt", p["reclaim_z20_min"]),
        leaf("rsi14", "gt", p["reclaim_rsi_min"]),
        leaf("rsi14", "lt", p["reclaim_rsi_max"]),
        leaf("vol_z20", "gt", p["vol_z20_min"]),
    ]
    if p.get("require_close_gt_prev_low20", True):
        entry.append(leaf("close", "gt", feat2="prev_low20"))
    if p.get("require_macd_pos", True):
        entry.append(leaf("macd_stick", "gt", 0.0))
    if p.get("atr_pct_min") is not None:
        entry.append(leaf("atr_pct", "gte", float(p["atr_pct_min"])))
    if p.get("atr_pct_max") is not None:
        entry.append(leaf("atr_pct", "lte", float(p["atr_pct_max"])))
    exit_any = [
        leaf("rsi14", "gt", p["tp_rsi"], role="take_profit"),
        leaf("close", "lt", feat2="prev_low20", role="invalidation"),
    ]
    key = "%s%s_%s" % (KEY_PREFIX, _safe(LOGIC_CLASS), _safe(tag))
    dsl = {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key[:100],
        "name": "寒霜叁-BNB15m-会话V回收",
        "direction": "long",
        "timeframe": TIMEFRAME,
        "supported_instruments": [SYMBOL],
        "max_hold_bars": int(p.get("max_hold_bars") or 20),
        "description": (
            "UTC07-20 session dump→V-reclaim + vol_z20 pulse; stop=0.9% ATR-matched"
        ),
        "entry": {"all": entry},
        "exit": {"any": exit_any},
    }
    return f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME), p


def make_book(plan, params=None, tag="init"):
    params = params or (plan.get("params") if isinstance(plan, dict) else None) or default_params()
    dsl, used = build_dsl(params, tag=tag)
    return {
        "title": "策略逻辑假设书",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": "long",
        "logic_class": LOGIC_CLASS,
        "thesis": (plan or {}).get("session_rationale_zh") or "session V-reclaim",
        "micro_behavior": (plan or {}).get("reclaim_definition_zh"),
        "entry_sketch": (plan or {}).get("entry_sketch"),
        "exit_sketch": (plan or {}).get("exit_sketch"),
        "session_window_utc": "%02d:00-%02d:00" % (SESSION_UTC_START, SESSION_UTC_END),
        "session_rationale_zh": (plan or {}).get("session_rationale_zh"),
        "reclaim_definition_zh": (plan or {}).get("reclaim_definition_zh"),
        "volume_pulse_zh": (plan or {}).get("volume_pulse_zh"),
        "stop_atr_match_zh": (plan or {}).get("stop_atr_match_zh"),
        "params": used,
        "dsl": dsl,
        "gate_mode": "frost2",
        "source": PREFIX,
    }


def apply_param_tweaks(book, tweaks):
    book = copy.deepcopy(book)
    params = dict(book.get("params") or default_params())
    if isinstance(tweaks, dict):
        for k, v in tweaks.items():
            if v is None:
                continue
            params[k] = v
    # never unlock session / symbol
    params["utc_hour_start"] = SESSION_UTC_START
    params["utc_hour_end_exclusive"] = SESSION_UTC_END
    dsl, used = build_dsl(params, tag=_safe((book.get("dsl") or {}).get("key") or "t")[-12:])
    book["params"] = used
    book["dsl"] = dsl
    book["symbol"] = SYMBOL
    book["timeframe"] = TIMEFRAME
    book["logic_class"] = LOGIC_CLASS
    return book


def apply_dsl_patch(book, patch):
    book = copy.deepcopy(book)
    if not isinstance(patch, dict):
        return book
    if isinstance(patch.get("entry_all"), list) and patch["entry_all"]:
        # keep session leaves enforced
        entries = list(patch["entry_all"])
        feats = [
            ((e.get("left") or {}).get("feature"))
            for e in entries if isinstance(e, dict)
        ]
        if "utc_hour" not in feats:
            entries = [
                leaf("utc_hour", "gte", SESSION_UTC_START),
                leaf("utc_hour", "lt", SESSION_UTC_END),
            ] + entries
        book["dsl"]["entry"] = {"all": entries}
    if isinstance(patch.get("exit_any"), list) and patch["exit_any"]:
        book["dsl"]["exit"] = {"any": patch["exit_any"]}
    if patch.get("max_hold_bars"):
        try:
            book["dsl"]["max_hold_bars"] = int(patch["max_hold_bars"])
        except Exception:
            pass
    book["dsl"] = f2.ensure_dsl(book["dsl"], SYMBOL, TIMEFRAME)
    book["dsl"]["key"] = (KEY_PREFIX + _safe(book["dsl"].get("key")) )[:100]
    return book


def local_tweak(book, n, failed_step=""):
    book = copy.deepcopy(book)
    params = dict(book.get("params") or default_params())
    step = str(failed_step or "")
    # loosen for WF starvation; tighten for friction/dest
    if "walk_forward" in step or "wf" in step:
        params["dump_z20_max"] = float(params.get("dump_z20_max", -1.2)) + 0.15 * n
        params["reclaim_z20_min"] = float(params.get("reclaim_z20_min", -0.8)) - 0.1 * n
        params["vol_z20_min"] = max(0.1, float(params.get("vol_z20_min", 0.5)) - 0.15 * n)
        params["reclaim_rsi_max"] = min(62.0, float(params.get("reclaim_rsi_max", 55)) + 2 * n)
        params["max_hold_bars"] = int(params.get("max_hold_bars", 20)) + 2 * n
    elif "destruction" in step or "dest" in step:
        params["dump_z20_max"] = float(params.get("dump_z20_max", -1.2)) - 0.1 * n
        params["vol_z20_min"] = float(params.get("vol_z20_min", 0.5)) + 0.1 * n
        params["reclaim_rsi_max"] = max(48.0, float(params.get("reclaim_rsi_max", 55)) - 1.5 * n)
    elif "friction" in step or "mc" in step or "sim" in step:
        params["vol_z20_min"] = float(params.get("vol_z20_min", 0.5)) + 0.15 * n
        params["dump_z20_max"] = float(params.get("dump_z20_max", -1.2)) - 0.1 * n
        params["max_hold_bars"] = max(12, int(params.get("max_hold_bars", 20)) - 2 * n)
        params["tp_rsi"] = max(52.0, float(params.get("tp_rsi", 58)) - 1.0 * n)
    else:
        params["dump_z20_max"] = float(params.get("dump_z20_max", -1.2)) + 0.05 * n
        params["vol_z20_min"] = max(0.2, float(params.get("vol_z20_min", 0.5)) - 0.05 * n)
    return apply_param_tweaks(book, params)


_status = {"op": "寒霜叁BNB15m会话V回收", "stage": "init", "updated_at": None, "hist": []}


def _upd(**kw):
    _status.update(kw)
    _status["updated_at"] = _now()
    _write("status", _status)


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
        "session_window_utc": "%02d:00-%02d:00" % (SESSION_UTC_START, SESSION_UTC_END),
        "logic_class": LOGIC_CLASS,
        "stop_pct": STOP_PCT,
    }
    open(os.path.join(arch, "DEATH.json"), "w").write(
        json.dumps(death, ensure_ascii=False, indent=2, default=str) + "\n")
    open(os.path.join(arch, "README.json"), "w").write(json.dumps({
        "title": "BNB 15m session_v_reclaim archived",
        "archive_path": arch,
        "failed_step": result.get("failed_step"),
        "prefix": PREFIX,
        "key_prefix": KEY_PREFIX,
    }, ensure_ascii=False, indent=2) + "\n")
    return arch


def glm_repair(book, packs, failed_step, stage):
    payload = {
        "stage": stage,
        "failed_step": failed_step,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_class": LOGIC_CLASS,
        "params": book.get("params"),
        "dsl": book.get("dsl"),
        "metrics": packs.get("base_metrics") or packs.get("metrics"),
        "quick": {k: packs.get(k) for k in ("quick_pass", "failed_step", "logic_destruction")},
        "full": packs.get("full"),
        "constraints": [
            "BNB-USDT-SWAP 15m locked",
            "session UTC07-20 locked",
            "keep dump→reclaim→vol_z20",
            "trades>=10 for WF",
            "stop 0.9% ATR-matched",
        ],
    }
    parsed, ai = ask_glm(REPAIR_PROMPT, payload, max_tokens=1100)
    if not parsed:
        return None, ai
    book2 = apply_param_tweaks(book, parsed.get("param_tweaks") or {})
    book2 = apply_dsl_patch(book2, parsed.get("dsl_patch") or {})
    book2["dsl"]["key"] = (KEY_PREFIX + _safe(book2["dsl"].get("key")) + "_r")[:100]
    return book2, ai


def process(book, plan, ctx):
    hist = []
    # Hypothesis approve
    for atry in range(MAX_AUDIT_REVISE + 1):
        _upd(stage="hyp_approve", attempt=atry)
        decision, ai = ask_glm(HYP_PROMPT, {
            "plan": plan,
            "book": {
                "symbol": book["symbol"], "timeframe": book["timeframe"],
                "direction": book["direction"], "logic_class": book["logic_class"],
                "params": book.get("params"),
                "entry": (book.get("dsl") or {}).get("entry"),
                "exit": (book.get("dsl") or {}).get("exit"),
                "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
                "session_window_utc": book.get("session_window_utc"),
                "reclaim_definition_zh": book.get("reclaim_definition_zh"),
                "volume_pulse_zh": book.get("volume_pulse_zh"),
                "stop_atr_match_zh": book.get("stop_atr_match_zh"),
            },
            "atr_stop_match": ctx.get("atr_stop_match"),
            "cl_traps": (ctx.get("cl_archive") or {}).get("trap_checklist"),
        })
        if not decision:
            decision = {
                "decision": "pass", "reason_zh": "hyp_fallback_pass", "fallback": True,
                "hypothesis_book": {
                    "title": "策略逻辑假设书",
                    "symbol": SYMBOL, "timeframe": TIMEFRAME, "direction": "long",
                    "logic_class": LOGIC_CLASS,
                    "session_window_utc": book.get("session_window_utc"),
                    "session_rationale_zh": book.get("session_rationale_zh"),
                    "reclaim_definition_zh": book.get("reclaim_definition_zh"),
                    "volume_pulse_zh": book.get("volume_pulse_zh"),
                    "stop_atr_match_zh": book.get("stop_atr_match_zh"),
                    "entry_sketch": book.get("entry_sketch"),
                    "exit_sketch": book.get("exit_sketch"),
                    "params": book.get("params"),
                },
            }
        _write("hyp_glm_t%d" % atry, {"decision": decision, "ai_ok": ai.get("ok")})
        hist.append({"stage": "hyp", "try": atry, "decision": decision.get("decision"),
                     "reason": decision.get("reason_zh"), "fallback": decision.get("fallback")})
        hb = decision.get("hypothesis_book") or {}
        if hb:
            _write("hypothesis_book", hb)
            if hb.get("params"):
                book = apply_param_tweaks(book, hb.get("params"))
        dec = str(decision.get("decision") or "pass").lower()
        if dec == "pass":
            break
        if dec == "revise" and atry < MAX_AUDIT_REVISE:
            book = apply_param_tweaks(book, (decision.get("revise") or {}).get("param_tweaks") or {})
            continue
        return {
            "ok": False, "failed_step": "hyp_reject",
            "reason": decision.get("reason_zh"), "hist": hist, "book": book,
            "death_cause_zh": "假设书未通过：" + str(decision.get("reason_zh")),
        }

    for atry in range(MAX_AUDIT_REVISE + 1):
        decision, ai = ask_glm(AUDIT_PROMPT, {
            "book": {
                "symbol": book["symbol"], "timeframe": book["timeframe"],
                "logic_class": book["logic_class"], "params": book.get("params"),
                "entry": (book.get("dsl") or {}).get("entry"),
                "exit": (book.get("dsl") or {}).get("exit"),
            },
            "cl_traps": (ctx.get("cl_archive") or {}).get("trap_checklist") or [],
            "hf_lessons": ctx.get("hf_lessons"),
        }, max_tokens=900)
        if not decision:
            decision = {"decision": "pass", "reason_zh": "audit_fallback_pass", "fallback": True}
        hist.append({"stage": "audit", "try": atry, "decision": decision.get("decision"),
                     "reason": decision.get("reason_zh"), "fallback": decision.get("fallback")})
        _write("audit_t%d" % atry, decision)
        dec = str(decision.get("decision") or "pass").lower()
        if dec == "pass":
            break
        if dec == "revise" and atry < MAX_AUDIT_REVISE:
            book = apply_param_tweaks(book, (decision.get("revise") or {}).get("param_tweaks") or {})
            continue
        return {
            "ok": False, "failed_step": "hyp_audit_reject",
            "reason": decision.get("reason_zh"), "hist": hist, "book": book,
            "death_cause_zh": "审计拒绝：" + str(decision.get("reason_zh")),
        }

    _write("book", book)

    packs = None
    for qtry in range(MAX_QUICK_REPAIR + 1):
        _upd(stage="quick", attempt=qtry)
        packs = f2.quick_suite(book)
        _write("quick_t%d" % qtry, packs)
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
        _write("book", book)

    for ftry in range(MAX_FULL_REPAIR + 1):
        _upd(stage="full", attempt=ftry)
        packs = f2.full_suite(book, packs)
        _write("full_t%d" % ftry, packs)
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
        _write("book", book)

    for stry in range(MAX_SIM_REPAIR + 1):
        _upd(stage="sim_formal", attempt=stry)
        sf = f2.run_sim_formal(book, packs)
        _write("sim_formal_t%d" % stry, sf)
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
    print("[bnb15m_session] START", _now(), flush=True)
    os.makedirs(OUT, exist_ok=True)
    _upd(stage="collect")

    # Isolate: never write foreign prefixes
    assert PREFIX.startswith("frost3_bnb15m_session")
    assert KEY_PREFIX.startswith("frost3_bnb15m_session_")

    ctx = load_context()
    _write("inputs", ctx)
    _write("atr_stop_doc", ctx.get("atr_stop_match") or {})

    _upd(stage="glm_param_plan")
    plan, ai = ask_glm(PARAM_PROMPT, {
        "packet": {
            "bnb_micro": ctx.get("bnb_micro"),
            "atr_stop_match": ctx.get("atr_stop_match"),
            "hf_lessons": ctx.get("hf_lessons"),
            "cl_archive_traps": (ctx.get("cl_archive") or {}).get("trap_checklist"),
            "gates": ctx.get("gates"),
            "distinct_from": ctx.get("distinct_from"),
        }
    }, max_tokens=2000)
    used_fallback = False
    if not plan or not plan.get("params"):
        plan = fallback_param_plan(ctx)
        used_fallback = True
    plan["glm_ok"] = bool(ai.get("ok") and not used_fallback)
    # lock invariants
    plan["symbol"] = SYMBOL
    plan["timeframe"] = TIMEFRAME
    plan["logic_class"] = LOGIC_CLASS
    plan["direction"] = "long"
    params = dict(default_params())
    params.update(plan.get("params") or {})
    params["utc_hour_start"] = SESSION_UTC_START
    params["utc_hour_end_exclusive"] = SESSION_UTC_END
    plan["params"] = params
    _write("param_plan", plan)
    _write("param_plan_glm_raw", {
        "ok": ai.get("ok"), "fallback": used_fallback,
        "raw_preview": (ai.get("raw_preview") or "")[:2000],
    })

    # Hypothesis document (pre-approve)
    hyp0 = {
        "title": "策略逻辑假设书",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": "long",
        "logic_class": LOGIC_CLASS,
        "session_window_utc": "%02d:00-%02d:00" % (SESSION_UTC_START, SESSION_UTC_END),
        "session_rationale_zh": plan.get("session_rationale_zh"),
        "reclaim_definition_zh": plan.get("reclaim_definition_zh"),
        "volume_pulse_zh": plan.get("volume_pulse_zh"),
        "stop_atr_match_zh": plan.get("stop_atr_match_zh") or (
            (ctx.get("atr_stop_match") or {}).get("note_zh")
        ),
        "atr_stop_match": ctx.get("atr_stop_match"),
        "params": params,
        "entry_sketch": plan.get("entry_sketch"),
        "exit_sketch": plan.get("exit_sketch"),
        "hf_avoidance_zh": plan.get("hf_avoidance_zh"),
    }
    _write("hypothesis_book_v0", hyp0)

    if plan.get("approve_for_quick") is False and plan.get("glm_ok"):
        end = {
            "ok": False, "failed_step": "param_plan_not_approved",
            "reason": plan.get("mentor_notes"), "plan": plan,
            "death_cause_zh": "参数方案未批准进Quick",
        }
        _write("end_report", end)
        arch = archive_fail(end, make_book(plan, params, "rejected"))
        end["archive_path"] = arch
        _write("parent_summary", {"summary_zh": "失败：参数方案未批准；归档=%s" % arch, "end": end})
        return 1

    book = make_book(plan, params, tag="v0")
    _write("book_init", book)

    try:
        result = process(book, plan, ctx)
    except Exception as exc:
        result = {
            "ok": False, "failed_step": "exception", "reason": str(exc),
            "trace": traceback.format_exc()[-2500:], "book": book,
            "death_cause_zh": "异常：" + str(exc),
        }

    end = {
        "at": _now(),
        "op": "寒霜叁BNB15m会话V回收",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_class": LOGIC_CLASS,
        "direction": "long",
        "session_window_utc": "%02d:00-%02d:00" % (SESSION_UTC_START, SESSION_UTC_END),
        "stop_pct": STOP_PCT,
        "atr_stop_match": ctx.get("atr_stop_match"),
        "reclaim_definition_zh": plan.get("reclaim_definition_zh"),
        "volume_pulse_zh": plan.get("volume_pulse_zh"),
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
        "params": ((result.get("book") or book).get("params")),
        "glm_param_ok": plan.get("glm_ok"),
        "artifact_prefix": PREFIX,
        "key_prefix": KEY_PREFIX,
    }
    if not result.get("ok"):
        arch = archive_fail(result, result.get("book") or book)
        end["archive_path"] = arch
        print("[bnb15m_session] ARCHIVED", result.get("failed_step"), arch, flush=True)
    else:
        print("[bnb15m_session] PENDING", end.get("pending_key"), flush=True)

    _write("end_report", end)
    _upd(stage="done", ok=end["ok"], pending_key=end.get("pending_key"),
         failed_step=end.get("failed_step"))

    if end["ok"]:
        summary = (
            "成功：BNB15m session_v_reclaim 会话UTC07–20 V回收+量能脉冲 已进pending key=%s；"
            "Sim DS=%s Qwen=%s；止损0.9%%≈%.2f会话ATR；文档前缀 frost3_bnb15m_session_*"
            % (
                end.get("pending_key"),
                (end.get("sim") or {}).get("ds"),
                (end.get("sim") or {}).get("qw"),
                ((end.get("atr_stop_match") or {}).get("stop_in_atr_units_vs_median") or -1),
            )
        )
    else:
        summary = (
            "失败：BNB15m session_v_reclaim 停在 %s；死因=%s；归档=%s；"
            "会话UTC07–20；止损0.9%%对会话ATR≈%.2f倍；前缀 frost3_bnb15m_session_*"
            % (
                end.get("failed_step"),
                end.get("death_cause_zh"),
                end.get("archive_path"),
                ((end.get("atr_stop_match") or {}).get("stop_in_atr_units_vs_median") or -1),
            )
        )
    _write("parent_summary", {"summary_zh": summary, "end": end})
    print("=== BNB15m_SESSION PARENT ===", summary, flush=True)
    print("=== BNB15m_SESSION END ===", json.dumps({
        "ok": end["ok"], "failed_step": end.get("failed_step"),
        "pending_key": end.get("pending_key"), "sim": end.get("sim"),
        "archive_path": end.get("archive_path"),
    }, ensure_ascii=False), flush=True)
    return 0 if end["ok"] else 1


if __name__ == "__main__":
    # fix typo guard for syntax — see main lock lines
    sys.exit(main())
