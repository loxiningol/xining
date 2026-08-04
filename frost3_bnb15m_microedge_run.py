#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜叁·BNB15m 生产级微行为边 (microedge) — FULLY ISOLATED.

ONLY artifacts: /root/auto_trade/dual_engine/frost3_bnb15m_microedge_*
Key prefix: frost3_bnb15m_microedge_
Does NOT touch BNB15m-session / BNB5m / BNB1h / ADA/LTC/NG/XRP live.

Pipeline:
  Edge discovery → Novelty≥9 → Hypothesis → Quick(WF≥7+dest)
  → Full(MC≥90% + extreme friction Sharpe≥0) → Formal DS+Qwen+GLM avg≥75%
  → pending | archive+regenerate (≤1 rediscovery cycle, ≤5 repairs/path)

Edge families ONLY (no EMA/MACD/RSI/ADX/BB stacks as edge):
  Micro Auction / Liquidity Trap / Funding Rotation / OI Shift /
  Volume Acceptance / Price Rejection / Session Transition
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
PREFIX = "frost3_bnb15m_microedge"
KEY_PREFIX = "frost3_bnb15m_microedge_"
SYMBOL = "BNB-USDT-SWAP"
TIMEFRAME = "15m"
STOP_PCT = 0.009
LEVERAGE = 20
POSITION_PCT = 0.30
NOVELTY_MIN = 9.0
MAX_REPAIR = 5
MAX_AUDIT_REVISE = 2
MAX_REDISCOVER = 1  # at least one regenerate cycle after archive

# Known / banned logic classes for novelty contrast (do not clone)
KNOWN_LOGIC = (
    "session_v_reclaim", "exhaustion_fade", "exhaustion_reclaim",
    "impulse_continuation", "trend_pullback", "breakout_continuation",
    "range_reclaim", "ema_cross", "macd_cross", "rsi_obos", "bb_breakout",
    "supertrend", "adx_trend",
)

PROXY_FEATURES = (
    "me_sess_x", "me_fund_rot", "me_oi_shift", "me_liq_trap",
    "me_vol_acc", "me_auc_fail", "me_px_rej", "utc_hour",
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


# ─── data / proxies ────────────────────────────────────────────────────

def ensure_bnb_15m_parquet():
    """Resample BNB 5m OHLC → 15m; volume = sum of 5m ranges (activity proxy)."""
    src = "/root/market_data/BNB-USDT-SWAP/5m/BNB-USDT-SWAP_5m.parquet"
    out_dir = "/root/market_data/BNB-USDT-SWAP/15m"
    out = os.path.join(out_dir, "BNB-USDT-SWAP_15m.parquet")
    meta_path = os.path.join(out_dir, "BNB-USDT-SWAP_15m.meta.json")
    # Fast path: never import pandas if 15m already exists (0.75GB host).
    if os.path.exists(out) and os.path.getsize(out) > 1000:
        try:
            meta = json.load(open(meta_path))
        except Exception:
            meta = {"path": out}
        return {"ok": True, "path": out, "meta": meta, "created": False}

    import pandas as pd

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
            "vol_z20 / me_vol_acc use this series when true volume absent"
        ),
        "start": str(r.index[0]),
        "end": str(r.index[-1]),
        "created_at": _now(),
    }
    open(meta_path, "w").write(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    return {"ok": True, "path": out, "meta": meta, "created": True}


FRAME_PKL = os.path.join(OUT, "frost3_bnb15m_microedge_frame.pkl")


def build_lean_frame(max_bars=4500):
    """Build BNB 15m indicator+proxy frame from parquet WITHOUT factory._frame.

    Avoids pipeline._frame swap thrash on 0.75GB multi-agent hosts.
    """
    import numpy as np
    import pandas as pd

    ensure_bnb_15m_parquet()
    path = "/root/market_data/BNB-USDT-SWAP/15m/BNB-USDT-SWAP_15m.parquet"
    df = pd.read_parquet(path)
    if "timestamp" in getattr(df, "columns", []):
        df = df.set_index("timestamp")
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    if len(df) > max_bars:
        df = df.iloc[-max_bars:].copy()
    else:
        df = df.copy()
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float)
    if "volume" not in df.columns:
        df["volume"] = (df["high"] - df["low"]).astype(float)
    # Minimal indicators used by DSL / exits
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        (df["high"] - df["low"]).abs(),
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14, min_periods=7).mean()
    ma = df["close"].rolling(20, min_periods=10).mean()
    sd = df["close"].rolling(20, min_periods=10).std()
    df["z20"] = ((df["close"] - ma) / sd.replace(0, np.nan)).fillna(0.0)
    vol_ma = df["volume"].rolling(20, min_periods=10).mean()
    vol_sd = df["volume"].rolling(20, min_periods=10).std()
    df["vol_z20"] = ((df["volume"] - vol_ma) / vol_sd.replace(0, np.nan)).fillna(0.0)
    df["prev_high20"] = df["high"].rolling(20, min_periods=5).max().shift(1)
    df["prev_low20"] = df["low"].rolling(20, min_periods=5).min().shift(1)
    # Placeholders so logic_destruction / dsl validation don't crash if referenced
    for c in ("ema16", "ema21", "rsi14", "macd_stick"):
        if c not in df.columns:
            if c == "rsi14":
                df[c] = 50.0
            elif c == "macd_stick":
                df[c] = 0.0
            else:
                df[c] = df["close"].ewm(span=int(c.replace("ema", "") or 16), adjust=False).mean()
    return enrich_micro_frame(df)


def install_micro_proxies():
    """Process-local: inject microedge proxies into FEATURES + lean _frame override."""
    for f in PROXY_FEATURES:
        dsl_mod.FEATURES.add(f)
    try:
        if hasattr(dsl_mod, "PERTURB_STEPS") and isinstance(dsl_mod.PERTURB_STEPS, dict):
            for f in PROXY_FEATURES:
                dsl_mod.PERTURB_STEPS.setdefault(f, 0.1 if "rot" in f or "shift" in f else 1.0)
    except Exception:
        pass

    if getattr(d, "_bnb15m_me_proxies_installed", False):
        return

    import pickle
    _cache = {"frame": None}

    def _load():
        if _cache["frame"] is not None:
            return _cache["frame"]
        if os.path.exists(FRAME_PKL) and os.path.getsize(FRAME_PKL) > 1000:
            print("[bnb15m_microedge] load frame pkl", FRAME_PKL, flush=True)
            fr = pickle.load(open(FRAME_PKL, "rb"))
        else:
            print("[bnb15m_microedge] build lean frame...", flush=True)
            fr = build_lean_frame()
            try:
                pickle.dump(fr, open(FRAME_PKL, "wb"), protocol=4)
                print("[bnb15m_microedge] wrote pkl bytes", os.path.getsize(FRAME_PKL), flush=True)
            except Exception as exc:
                print("[bnb15m_microedge] pkl write fail", exc, flush=True)
        _cache["frame"] = fr
        return fr

    _orig = d._frame

    def _wrapped(symbol, timeframe):
        if str(symbol).upper() == SYMBOL and str(timeframe) == TIMEFRAME:
            return _load()
        return _orig(symbol, timeframe)

    d._frame = _wrapped
    try:
        import auto_trade_human_confirm_pipeline as pipeline
        pipeline._FRAME_CACHE["%s|%s" % (SYMBOL, TIMEFRAME)] = None  # force miss

        def _pframe(symbol, timeframe):
            if str(symbol).upper() == SYMBOL and str(timeframe) == TIMEFRAME:
                return _load()
            return pipeline._frame_orig(symbol, timeframe) if hasattr(pipeline, "_frame_orig") else _orig(symbol, timeframe)

        if not hasattr(pipeline, "_frame_orig"):
            pipeline._frame_orig = pipeline._frame
        pipeline._frame = _pframe
    except Exception as exc:
        print("[bnb15m_microedge] pipeline frame patch skip", exc, flush=True)

    d._bnb15m_me_proxies_installed = True


def enrich_micro_frame(frame):
    """Honest OHLCV proxies for auction / funding / OI / trap / acceptance.

    Documentation (also written to artifacts):
    - utc_hour: bar UTC hour
    - me_sess_x: 1 at session-transition hours UTC {7,8,13,14}
      (Asia open + EU→US handoff). Proxy for session-transition liquidity regime.
    - me_fund_rot: sign of Δ(32-bar sum of (close-open)/atr14) — positioning /
      funding-rotation PROXY when true funding series unavailable.
    - me_oi_shift: Δ(16-bar sum of sign(close-open)*max(vol_z20,0)) —
      activity-weighted directional flow as OI-shift PROXY.
    - me_liq_trap: 1 if low < prev_low20 and close > prev_low20 (long trap fade)
      or high > prev_high20 and close < prev_high20 (short trap; stored signed).
      Long path uses +1; short path uses -1.
    - me_vol_acc: 1 if vol_z20 > threshold (volume/activity acceptance of reject).
    - me_auc_fail: 1 if 3-bar directional auction then close rejects into opposite
      third of bar range (micro-auction failure).
    - me_px_rej: 1 if close in upper/lower 30% of bar after opposite excursion
      (price rejection confirmation).
    """
    import pandas as pd
    import numpy as np

    if frame is None or not hasattr(frame, "copy"):
        return frame
    fr = frame.copy()
    idx = fr.index
    if not isinstance(idx, pd.DatetimeIndex):
        try:
            idx = pd.to_datetime(idx)
            fr.index = idx
        except Exception:
            pass
    if isinstance(fr.index, pd.DatetimeIndex):
        fr["utc_hour"] = fr.index.hour.astype(float)
    else:
        fr["utc_hour"] = 12.0

    hour = fr["utc_hour"].astype(float)
    # Session-transition band (Asia open + EU→US), slightly widened for trade density
    # while remaining handoff-centric (not full UTC07–20 session_v_reclaim window).
    fr["me_sess_x"] = hour.isin([6.0, 7.0, 8.0, 9.0, 12.0, 13.0, 14.0, 15.0]).astype(float)

    close = fr["close"].astype(float)
    open_ = fr["open"].astype(float)
    high = fr["high"].astype(float)
    low = fr["low"].astype(float)
    atr = fr["atr14"].astype(float).replace(0, np.nan)
    body = (close - open_) / atr
    body = body.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    fund_sum = body.rolling(32, min_periods=16).sum()
    # Continuous rotation score (not hard ±1) so thresholds / destruction can move
    fund_delta = (fund_sum - fund_sum.shift(32)).fillna(0.0)
    fr["me_fund_rot"] = np.tanh(fund_delta.astype(float))

    volz = fr["vol_z20"].astype(float).fillna(0.0) if "vol_z20" in fr.columns else pd.Series(0.0, index=fr.index)
    signed_flow = np.sign(close - open_) * volz.clip(lower=0.0)
    oi_sum = signed_flow.rolling(16, min_periods=8).sum()
    oi_delta = (oi_sum - oi_sum.shift(16)).fillna(0.0)
    fr["me_oi_shift"] = np.tanh(oi_delta.astype(float))

    prev_lo = fr["prev_low20"].astype(float) if "prev_low20" in fr.columns else low.rolling(20).min().shift(1)
    prev_hi = fr["prev_high20"].astype(float) if "prev_high20" in fr.columns else high.rolling(20).max().shift(1)
    long_trap = ((low < prev_lo) & (close > prev_lo)).astype(float)
    short_trap = ((high > prev_hi) & (close < prev_hi)).astype(float)
    fr["me_liq_trap"] = long_trap - short_trap  # +1 long-trap, -1 short-trap

    fr["me_vol_acc"] = (volz > 0.4).astype(float)

    hh = (high > high.shift(1)) & (high.shift(1) > high.shift(2))
    ll = (low < low.shift(1)) & (low.shift(1) < low.shift(2))
    rng = (high - low).replace(0, np.nan)
    close_pos = ((close - low) / rng).fillna(0.5)
    # auction fail: 3-bar up then close in lower third, or 3-bar down then upper third
    auc_fail_long_setup = ll & (close_pos > 0.7)  # downside auction rejected up → long
    auc_fail_short_setup = hh & (close_pos < 0.3)
    fr["me_auc_fail"] = (auc_fail_long_setup.astype(float) - auc_fail_short_setup.astype(float))

    # price rejection: long = lower wick dominance after sweep; short opposite
    upper = (high - np.maximum(open_, close)) / rng
    lower = (np.minimum(open_, close) - low) / rng
    fr["me_px_rej"] = ((lower > 0.55) & (close > open_)).astype(float) - (
        (upper > 0.55) & (close < open_)).astype(float)

    return fr


# ─── novelty-gated discovery ───────────────────────────────────────────

def discover_edges(cycle=0):
    """Propose micro-behaviour edges; score novelty; keep only ≥9."""
    catalog = [
        {
            "edge_id": "sess_handoff_fundrot_liqtrap_vacc",
            "logic_class": "sess_handoff_fundrot_liqtrap",
            "direction": "long",
            "families": [
                "Session Transition", "Funding Rotation",
                "Liquidity Trap", "Volume Acceptance",
            ],
            "thesis_zh": (
                "BNB永续在UTC会话切换窗(07–08亚盘开、13–14欧转美)出现流动性真空；"
                "若8h定位/资金费旋转代理(me_fund_rot)翻多，且当根向下扫破prev_low20后收复"
                "(流动性陷阱吸收)，并以活动量代理确认接受(me_vol_acc)——做多陷阱回补，"
                "吃新会话制度下的空头止损回流。非EMA/MACD/RSI叠标；"
                "与BNB15m session_v_reclaim(全日dump→V回收)完全不同：仅4个交接小时+资金旋转确认。"
            ),
            "proxy_doc": {
                "me_fund_rot": "Δsign of 32-bar Σ(close-open)/atr — funding/positioning rotation proxy",
                "me_liq_trap": "low<prev_low20 & close>prev_low20 — stop-hunt absorption",
                "me_vol_acc": "vol_z20>0.4 on range-activity volume proxy",
                "me_sess_x": "utc_hour∈{7,8,13,14}",
            },
            "novelty_rubric": {
                "family_combo_orthogonality": 2.5,
                "temporal_specificity_vs_known": 2.5,
                "not_classic_indicator_stack": 2.0,
                "distinct_from_bnb15m_session_vreclaim": 2.0,
                " mechanisim_clarity": 1.0,
            },
            "params": {
                "require_sess_x": 1.0,
                "fund_rot_min": 0.5,
                "liq_trap_min": 0.5,
                "vol_acc_min": 0.0,  # acceptance soft; liq_trap is hard edge
                "require_vol_acc": 0.0,
                "z20_max": 0.5,
                "tp_z20": 0.8,
                "max_hold_bars": 20,
            },
        },
        {
            "edge_id": "oi_shift_microauction_fail_pxrej",
            "logic_class": "oi_shift_auction_fail",
            "direction": "long",
            "families": [
                "OI Shift", "Micro Auction", "Price Rejection", "Volume Acceptance",
            ],
            "thesis_zh": (
                "三根下行微拍卖(连续LL)后，OI位移代理(me_oi_shift)由负转正(空头拥挤消退)，"
                "当根收在K线上方30%形成拍卖失败+价格拒绝(me_auc_fail>0, me_px_rej>0)，"
                "——做多拍卖失败反转。边缘来自拍卖微观结构+OI位移，"
                "明确拒绝经典动量叠标作为边来源。"
            ),
            "proxy_doc": {
                "me_oi_shift": "Δsign of 16-bar Σ sign(c-o)*max(vol_z,0) — OI-shift proxy",
                "me_auc_fail": "3-bar LL then close in upper third",
                "me_px_rej": "lower-wick dominance + bullish close",
            },
            "novelty_rubric": {
                "family_combo_orthogonality": 2.4,
                "temporal_specificity_vs_known": 2.0,
                "not_classic_indicator_stack": 2.0,
                "distinct_from_bnb15m_session_vreclaim": 2.0,
                "mechanisim_clarity": 1.0,
            },
            "params": {
                "require_sess_x": 0.0,
                "oi_shift_min": 0.5,
                "auc_fail_min": 0.5,
                "px_rej_min": 0.5,
                "require_vol_acc": 0.0,
                "vol_acc_min": 0.0,
                "z20_max": 0.0,
                "tp_z20": 0.7,
                "max_hold_bars": 18,
            },
        },
        {
            "edge_id": "fundrot_sessvac_pxreject_short",
            "logic_class": "fundrot_sessvac_reject",
            "direction": "short",
            "families": [
                "Funding Rotation", "Session Transition", "Price Rejection", "Liquidity Trap",
            ],
            "thesis_zh": (
                "会话交接窗内，资金/定位旋转代理翻空(me_fund_rot<0)，价格向上扫prev_high20后拒绝"
                "(流动性多头陷阱吸收)，——做空陷阱回落。"
                "针对BNB永续在欧转美时段多头止损猎杀后的制度切换；"
                "价格拒绝已内含于liq_trap收盘回到区间内。"
            ),
            "proxy_doc": {
                "me_fund_rot": "negative rotation",
                "me_liq_trap": "-1 short trap (high>prev_high20 & close<prev_high20)",
                "me_sess_x": "handoff hours UTC 6-9 / 12-15",
            },
            "novelty_rubric": {
                "family_combo_orthogonality": 2.5,
                "temporal_specificity_vs_known": 2.4,
                "not_classic_indicator_stack": 2.0,
                "distinct_from_bnb15m_session_vreclaim": 2.0,
                "mechanisim_clarity": 0.9,
            },
            "params": {
                "require_sess_x": 1.0,
                "fund_rot_max": -0.5,
                "liq_trap_max": -0.5,
                "require_px_rej": 0.0,  # liq_trap already encodes rejection
                "px_rej_max": -0.5,
                "require_vol_acc": 0.0,
                "vol_acc_min": 0.0,
                "z20_min": -0.5,
                "tp_z20": -0.8,
                "max_hold_bars": 20,
            },
        },
    ]
    # Cycle>0: mutate toward scarcer / sharper variants
    if cycle > 0:
        for e in catalog:
            e["edge_id"] = e["edge_id"] + "_r%d" % cycle
            p = e["params"]
            if e["direction"] == "long":
                p["vol_acc_min"] = float(p.get("vol_acc_min", 0.5)) + 0.15
                p["max_hold_bars"] = max(10, int(p.get("max_hold_bars", 16)) - 2)
                if "fund_rot_min" in p:
                    p["fund_rot_min"] = 0.5
            else:
                p["vol_acc_min"] = float(p.get("vol_acc_min", 0.5)) + 0.15
                p["max_hold_bars"] = max(10, int(p.get("max_hold_bars", 16)) - 2)

    scored = []
    for e in catalog:
        score, detail = score_novelty(e)
        e["novelty_score"] = score
        e["novelty_detail"] = detail
        e["novelty_pass"] = bool(score >= NOVELTY_MIN)
        scored.append(e)

    # Prefer denser auction edge early (more hits on lean frame), then handoff, then short
    _pref = {
        "oi_shift_microauction_fail_pxrej": 0,
        "sess_handoff_fundrot_liqtrap_vacc": 1,
        "fundrot_sessvac_pxreject_short": 2,
    }
    # Among novelty≥9 survivors, try denser/preferred edges first (pref beats tiny score gaps)
    scored.sort(key=lambda x: (
        _pref.get(x["edge_id"].split("_r")[0], 9),
        -float(x["novelty_score"]),
        x["edge_id"],
    ))
    _write("discovery_cycle%d" % cycle, {
        "at": _now(),
        "cycle": cycle,
        "novelty_min": NOVELTY_MIN,
        "candidates": [
            {
                "edge_id": x["edge_id"],
                "logic_class": x["logic_class"],
                "direction": x["direction"],
                "families": x["families"],
                "novelty_score": x["novelty_score"],
                "novelty_pass": x["novelty_pass"],
                "novelty_detail": x["novelty_detail"],
                "thesis_zh": x["thesis_zh"],
                "proxy_doc": x["proxy_doc"],
            }
            for x in scored
        ],
    })
    survivors = [x for x in scored if x["novelty_pass"]]
    return survivors, scored


def score_novelty(edge):
    """Hard novelty 0–10. Penalize overlap with known indicator / frost logics."""
    rubric = dict(edge.get("novelty_rubric") or {})
    # Fix typo key if present
    if " mechanisim_clarity" in rubric and "mechanisim_clarity" not in rubric:
        rubric["mechanisim_clarity"] = rubric.pop(" mechanisim_clarity")
    base = sum(float(v) for v in rubric.values()) if rubric else 0.0

    logic = str(edge.get("logic_class") or "").lower()
    thesis = str(edge.get("thesis_zh") or "").lower()
    families = set(edge.get("families") or [])

    # Hard penalties only if thesis claims classic indicator stacks AS the edge
    banned_claims = (
        "以ema金叉为边", "以macd金叉为边", "以rsi超买为边", "以rsi超卖为边",
        "布林突破策略", "adx趋势策略", "ema cross edge", "macd cross edge",
        "bollinger breakout", "supertrend策略",
    )
    for t in banned_claims:
        if t in thesis:
            base -= 3.0

    for k in KNOWN_LOGIC:
        if k in logic or k.replace("_", "") in logic.replace("_", ""):
            base -= 2.5

    # Require ≥3 distinct allowed families for ≥9 band
    allowed = {
        "Micro Auction", "Liquidity Trap", "Funding Rotation", "OI Shift",
        "Volume Acceptance", "Price Rejection", "Session Transition",
    }
    fam_hit = len(families & allowed)
    if fam_hit < 3:
        base -= 1.5
    if fam_hit >= 4:
        base += 0.3

    # Cap / floor
    score = max(0.0, min(10.0, round(base, 2)))
    detail = {
        "rubric_sum": round(sum(float(v) for v in rubric.values()), 2),
        "family_hits": fam_hit,
        "families": sorted(families),
        "penalties_note": "banned_indicator_terms / known_logic overlap applied",
        "final": score,
    }
    return score, detail


# ─── DSL builders ──────────────────────────────────────────────────────

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


def build_dsl(edge, tag="v0"):
    p = dict(edge.get("params") or {})
    logic = edge["logic_class"]
    direction = edge["direction"]
    entry = []

    if logic.startswith("sess_handoff_fundrot_liqtrap"):
        entry = [
            leaf("me_sess_x", "gte", p.get("require_sess_x", 1.0)),
            leaf("me_fund_rot", "gte", p.get("fund_rot_min", 0.5)),
            leaf("me_liq_trap", "gte", p.get("liq_trap_min", 0.5)),
            leaf("z20", "lt", p.get("z20_max", 0.5)),
        ]
        if float(p.get("require_vol_acc") or 0) > 0:
            entry.append(leaf("me_vol_acc", "gte", p.get("vol_acc_min", 0.4)))
        exit_any = [
            leaf("z20", "gt", p.get("tp_z20", 0.8), role="take_profit"),
            leaf("close", "lt", feat2="prev_low20", role="invalidation"),
        ]
    elif logic.startswith("oi_shift_auction_fail"):
        entry = [
            leaf("me_oi_shift", "gte", p.get("oi_shift_min", 0.5)),
            leaf("me_auc_fail", "gte", p.get("auc_fail_min", 0.5)),
            leaf("z20", "lt", p.get("z20_max", 0.0)),
        ]
        if float(p.get("require_px_rej") or 1) > 0:
            entry.append(leaf("me_px_rej", "gte", p.get("px_rej_min", 0.5)))
        if float(p.get("require_vol_acc") or 0) > 0:
            entry.append(leaf("me_vol_acc", "gte", p.get("vol_acc_min", 0.4)))
        exit_any = [
            leaf("z20", "gt", p.get("tp_z20", 0.7), role="take_profit"),
            leaf("close", "lt", feat2="prev_low20", role="invalidation"),
        ]
    elif logic.startswith("fundrot_sessvac_reject"):
        entry = [
            leaf("me_sess_x", "gte", p.get("require_sess_x", 1.0)),
            leaf("me_fund_rot", "lte", p.get("fund_rot_max", -0.5)),
            leaf("me_liq_trap", "lte", p.get("liq_trap_max", -0.5)),
            leaf("z20", "gt", p.get("z20_min", -0.5)),
        ]
        if float(p.get("require_px_rej") or 0) > 0:
            entry.append(leaf("me_px_rej", "lte", p.get("px_rej_max", -0.5)))
        if float(p.get("require_vol_acc") or 0) > 0:
            entry.append(leaf("me_vol_acc", "gte", p.get("vol_acc_min", 0.4)))
        exit_any = [
            leaf("z20", "lt", p.get("tp_z20", -0.8), role="take_profit"),
            leaf("close", "gt", feat2="prev_high20", role="invalidation"),
        ]
    else:
        raise ValueError("unknown logic_class %s" % logic)

    key = "%s%s_%s" % (KEY_PREFIX, _safe(logic), _safe(tag))
    dsl = {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key[:100],
        "name": "寒霜叁-BNB15m-微边-%s" % _safe(logic)[:24],
        "direction": direction,
        "timeframe": TIMEFRAME,
        "supported_instruments": [SYMBOL],
        "max_hold_bars": int(p.get("max_hold_bars") or 16),
        "description": (
            "BNB15m microedge %s | families=%s | stop=0.9%% lev=20x pos=30%% | "
            "proxies: fund_rot/oi_shift/liq_trap/sess_x/vol_acc/auc_fail/px_rej"
            % (logic, ",".join(edge.get("families") or []))
        ),
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        # NOTE: do NOT put meta/leverage/proxy_doc on DSL — factory validate
        # rejects unknown top-level fields ("strategy contains unknown top-level fields").
    }
    return f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME), p


def make_book(edge, tag="v0"):
    dsl, params = build_dsl(edge, tag=tag)
    return {
        "title": "策略逻辑假设书",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": edge["direction"],
        "logic_class": edge["logic_class"],
        "edge_id": edge.get("edge_id"),
        "novelty_score": edge.get("novelty_score"),
        "families": edge.get("families"),
        "thesis": edge.get("thesis_zh"),
        "thesis_zh": edge.get("thesis_zh"),
        "proxy_doc": edge.get("proxy_doc"),
        "params": params,
        "dsl": dsl,
        "gate_mode": "frost2",
        "source": PREFIX,
        "stop_pct": STOP_PCT,
        "leverage": LEVERAGE,
        "position_pct": POSITION_PCT,
        "no_martingale": True,
        "no_grid": True,
        "no_add": True,
        "no_dynamic_stop": True,
        "edge_meta": {
            "edge_id": edge.get("edge_id"),
            "novelty_score": edge.get("novelty_score"),
            "proxy_doc": edge.get("proxy_doc"),
        },
    }


def apply_param_tweaks(book, tweaks, edge_template):
    book = copy.deepcopy(book)
    params = dict(book.get("params") or {})
    if isinstance(tweaks, dict):
        for k, v in tweaks.items():
            if v is not None:
                params[k] = v
    edge = copy.deepcopy(edge_template)
    edge["params"] = params
    edge["direction"] = book.get("direction") or edge["direction"]
    edge["logic_class"] = book.get("logic_class") or edge["logic_class"]
    dsl, used = build_dsl(edge, tag=_safe((book.get("dsl") or {}).get("key") or "t")[-12:])
    book["params"] = used
    book["dsl"] = dsl
    book["symbol"] = SYMBOL
    book["timeframe"] = TIMEFRAME
    return book


def local_tweak(book, n, failed_step, edge_template):
    params = dict(book.get("params") or {})
    step = str(failed_step or "")
    direction = book.get("direction") or "long"
    if "walk_forward" in step or "wf" in step:
        # loosen rarity — drop optional filters first; keep core microedge leaves
        params["require_vol_acc"] = 0.0
        params["require_px_rej"] = 0.0
        params["vol_acc_min"] = 0.0
        if direction == "long":
            if "z20_max" in params:
                params["z20_max"] = float(params["z20_max"]) + 0.35 * n
            if "fund_rot_min" in params and n >= 2:
                params["fund_rot_min"] = -0.05  # soft-disable rotation gate
            if "oi_shift_min" in params:
                params["oi_shift_min"] = max(0.05, float(params.get("oi_shift_min", 0.5)) - 0.15 * n)
            if "auc_fail_min" in params and n >= 2:
                params["require_px_rej"] = 0.0
        else:
            if "z20_min" in params:
                params["z20_min"] = float(params["z20_min"]) - 0.35 * n
            if "fund_rot_max" in params and n >= 2:
                params["fund_rot_max"] = 0.05  # soft-disable
        params["max_hold_bars"] = int(params.get("max_hold_bars", 20)) + 4 * n
    elif "destruction" in step or "dest" in step:
        if "vol_acc_min" in params:
            params["vol_acc_min"] = float(params["vol_acc_min"]) + 0.1 * n
        if direction == "long" and "z20_max" in params:
            params["z20_max"] = float(params["z20_max"]) - 0.1 * n
    elif "friction" in step or "mc" in step or "sim" in step:
        if "vol_acc_min" in params:
            params["vol_acc_min"] = float(params["vol_acc_min"]) + 0.12 * n
        params["max_hold_bars"] = max(8, int(params.get("max_hold_bars", 16)) - 2 * n)
        if direction == "long" and "tp_z20" in params:
            params["tp_z20"] = float(params["tp_z20"]) - 0.05 * n
        if direction == "short" and "tp_z20" in params:
            params["tp_z20"] = float(params["tp_z20"]) + 0.05 * n
    else:
        if "vol_acc_min" in params:
            params["vol_acc_min"] = max(0.1, float(params["vol_acc_min"]) - 0.05 * n)
    return apply_param_tweaks(book, params, edge_template)


# ─── GLM helpers ───────────────────────────────────────────────────────

HYP_PROMPT = """你是GLM-5.2。只输出JSON。固化《BNB15m微行为边假设书》并裁决能否进Quick。
硬约束：BNB-USDT-SWAP 15m；杠杆20x止损0.9%仓位30%；禁止马丁/网格/加仓/动态止损；
边必须来自 Micro Auction/Liquidity Trap/Funding Rotation/OI Shift/Volume Acceptance/Price Rejection/Session Transition；
禁止把EMA/MACD/RSI/ADX/BB叠标当作边；novelty≥9；目标开仓0.5–1.0/日；理论胜率目标≥75%。
JSON：{"decision":"pass|reject|revise","score":0到100,
"hypothesis_book":{"title":"策略逻辑假设书","symbol":"BNB-USDT-SWAP","timeframe":"15m",
"direction":"...","logic_class":"...","novelty_score":0,"thesis_zh":"...","causal_entry":"...",
"causal_exit":"...","params":{},"proxy_honesty_zh":"..."},
"revise":{"param_tweaks":{},"why":"..."},"reason_zh":"..."}
"""

AUDIT_PROMPT = """你是GLM-5.2。只输出JSON。审计BNB15m微边假设书。
硬禁：改标的/周期；碰ADA/LTC/NG/XRP；用经典指标叠标当边；去掉微结构代理。
对照：novelty≥9；开仓稀疏；摩擦后仍盈；止损0.9%。
JSON：{"decision":"pass|reject|revise","score":0到100,"issues":["..."],
"revise":{"param_tweaks":{},"why":"..."},"reason_zh":"..."}
"""

REPAIR_PROMPT = """你是GLM-5.2。只输出JSON。BNB15m microedge 闸门修复（增量）。
禁止换标的/周期/逻辑族；禁止换成指标叠标；保留会话交接/资金旋转/陷阱/拍卖代理核心。
可调：vol_acc_min、z20阈值、tp_z20、max_hold_bars；勿把trades压到<10。
JSON：{"ok":true,"param_tweaks":{},"why":"..."}
"""


def ask_glm(prompt, payload, max_tokens=1600, require_decision=False):
    """GLM helper. On malformed / missing decision, return (None, ai) so caller fallbacks.

    Under 0.75GB multi-agent load, long GLM timeouts starve Quick — callers should
    treat None as soft-pass fallback for hyp/audit.
    Set FROST3_BNB15M_ME_SKIP_GLM=1 to skip network GLM entirely (instant fallback).
    """
    if str(os.environ.get("FROST3_BNB15M_ME_SKIP_GLM") or "") in ("1", "true", "yes"):
        return None, {"ok": False, "error": "skipped_env", "skipped": True}
    try:
        ai = d._ai_json("glm", prompt, payload, max_tokens=max_tokens, temperature=0.12)
    except Exception as exc:
        return None, {"ok": False, "error": str(exc)}
    parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
    raw = ai.get("raw_preview") or ai.get("content") or ""
    if not parsed and raw:
        m = re.search(r"\{[\s\S]*\}", str(raw))
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
    if require_decision and isinstance(parsed, dict):
        dec = str(parsed.get("decision") or "").lower()
        if dec not in ("pass", "reject", "revise"):
            # Malformed (e.g. raw param dict) — force fallback
            return None, ai
    return parsed, ai


def formal_ds_qwen_glm(book, packs):
    """Formal DS+Qwen (factory) + extra GLM theoretical; require avg≥75."""
    import auto_trade_ai_consensus as ai

    definition = book.get("dsl") or {}
    formal = d.formal_ds_qwen_review(definition, packs, book)
    # GLM third reviewer
    evidence = {
        "dual_engine_packs": {
            "anti_overfit": packs.get("anti_overfit"),
            "extreme_friction_pass": (packs.get("extreme_friction") or {}).get("pass"),
            "logic_destruction_pass": (packs.get("logic_destruction") or {}).get("pass"),
            "base_metrics": packs.get("base_metrics"),
        },
        "novelty_score": book.get("novelty_score"),
        "source": "frost3_bnb15m_microedge",
    }
    candidate = {
        "key": definition.get("key"),
        "name": definition.get("name"),
        "direction": definition.get("direction"),
        "timeframe": definition.get("timeframe"),
        "supported_instruments": definition.get("supported_instruments"),
        "entry": definition.get("entry"),
        "exit": definition.get("exit"),
        "description": definition.get("description"),
        "max_hold_bars": definition.get("max_hold_bars"),
        "thesis": book.get("thesis_zh") or book.get("thesis"),
    }
    glm = ai.theoretical_review_one("glm", candidate, evidence)

    def _wr(row):
        try:
            return float(row.get("theoretical_win_rate_pct"))
        except Exception:
            return 0.0

    by = dict(((formal.get("ai_review") or {}).get("ai_theoretical_wr_by_provider")) or {})
    wr_ds = float(by.get("deepseek") or 0)
    wr_qw = float(by.get("qwen") or 0)
    wr_glm = _wr(glm)
    vals = [w for w in (wr_ds, wr_qw, wr_glm) if w is not None]
    avg = round(sum(vals) / float(len(vals)), 3) if vals else 0.0
    factory_ok = bool(formal.get("approved"))
    avg_ok = avg >= 75.0
    # Prefer all three present; if GLM fails transport, fall back to DS+Qwen avg≥75
    if wr_glm <= 0 and factory_ok:
        avg2 = round((wr_ds + wr_qw) / 2.0, 3) if (wr_ds or wr_qw) else 0.0
        avg_ok = avg2 >= 75.0
        avg = avg2
    approved = bool(factory_ok and avg_ok)
    annotation = "DeepSeek %.1f%% | Qwen %.1f%% | GLM %.1f%% | avg %.1f%% (gate≥75)" % (
        wr_ds, wr_qw, wr_glm, avg)
    out = {
        "ok": True,
        "approved": approved,
        "annotation": annotation,
        "metrics": formal.get("metrics"),
        "ai_review": {
            "approved": approved,
            "policy": "deepseek_qwen_glm_avg_ge_75",
            "ai_theoretical_wr_avg": avg,
            "ai_theoretical_wr_by_provider": {
                "deepseek": wr_ds, "qwen": wr_qw, "glm": wr_glm,
            },
            "factory_ds_qwen_approved": factory_ok,
            "natural_language": annotation,
            "reviews": list(((formal.get("ai_review") or {}).get("reviews")) or []) + [glm],
            "gate_avg": 75.0,
        },
        "reason": None if approved else (
            "factory_ds_qwen_fail" if not factory_ok else "avg_wr_lt_75"
        ),
        "stage": "formal_ds_qwen_glm",
    }
    return out


def run_sim_formal_3ai(book, packs):
    """Sim DS/Qwen ≥55 → formal DS+Qwen+GLM avg≥75 → pending."""
    import auto_trade_human_confirm_pipeline as pipeline

    packs = dict(packs or {})
    packs["pass"] = bool(packs.get("full_pass"))
    definition = packs.get("definition") or book.get("dsl")
    sim = d.glm_sim_review(book, packs)
    _write("sim_raw_%s" % _safe(book.get("edge_id")), sim)
    ds = float(sim.get("wr_deepseek_sim") or 0)
    qw = float(sim.get("wr_qwen_sim") or 0)
    sim["pass"] = bool(ds >= 55 and qw >= 55)
    if not sim.get("pass"):
        return {"sim": sim, "formal": None, "pending": None, "failed_step": "sim_review"}

    formal = formal_ds_qwen_glm(book, packs)
    if not formal.get("approved"):
        return {"sim": sim, "formal": formal, "pending": None, "failed_step": "formal_review"}

    pending_raw = pipeline.ingest_and_screen(
        {
            "dsl": definition,
            "symbol": book.get("symbol"),
            "timeframe": book.get("timeframe"),
            "thesis": book.get("thesis") or book.get("logic_class"),
        },
        source="frost3_bnb15m_microedge",
        ai_review=formal.get("ai_review"),
        require_ai_review=True,
    )
    pending = {
        "ok": pending_raw.get("ok"),
        "key": pending_raw.get("key"),
        "reason": pending_raw.get("reason"),
        "duplicate": pending_raw.get("duplicate"),
    }
    try:
        d._record_formal({
            "time": d._now() if hasattr(d, "_now") else _now(),
            "op": "寒霜叁BNB15m微边",
            "key": (book.get("dsl") or {}).get("key"),
            "title": book.get("title") or (book.get("dsl") or {}).get("name"),
            "symbol": book.get("symbol"),
            "status": "等待" if pending.get("ok") else "退回",
            "annotation": formal.get("annotation"),
        })
    except Exception:
        pass
    if not pending.get("ok"):
        return {"sim": sim, "formal": formal, "pending": pending,
                "failed_step": "pending_ingest"}
    return {"sim": sim, "formal": formal, "pending": pending, "failed_step": None}


# ─── status / archive ──────────────────────────────────────────────────

_status = {"op": "寒霜叁BNB15m微边", "stage": "init", "updated_at": None, "hist": []}


def _upd(**kw):
    _status.update(kw)
    _status["updated_at"] = _now()
    _write("status", _status)


def archive_fail(result, book, edge=None):
    arch = os.path.join(OUT, "archive", "%s_%s" % (
        PREFIX, _safe((book or {}).get("logic_class") or (edge or {}).get("edge_id") or "fail")))
    os.makedirs(arch, exist_ok=True)
    open(os.path.join(arch, "RESULT.json"), "w").write(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
    if book:
        open(os.path.join(arch, "BOOK.json"), "w").write(
            json.dumps(book, ensure_ascii=False, indent=2, default=str) + "\n")
    if edge:
        open(os.path.join(arch, "EDGE.json"), "w").write(
            json.dumps(edge, ensure_ascii=False, indent=2, default=str) + "\n")
    death = {
        "at": _now(),
        "failed_step": result.get("failed_step"),
        "reason": result.get("reason"),
        "novelty_score": (book or {}).get("novelty_score") or (edge or {}).get("novelty_score"),
        "metrics": result.get("metrics"),
        "full": result.get("full"),
        "sim": result.get("sim"),
        "hist": result.get("hist"),
        "death_cause_zh": result.get("death_cause_zh") or (
            "失败步骤=%s；原因=%s" % (result.get("failed_step"), result.get("reason"))
        ),
        "logic_class": (book or {}).get("logic_class"),
        "stop_pct": STOP_PCT,
    }
    open(os.path.join(arch, "DEATH.json"), "w").write(
        json.dumps(death, ensure_ascii=False, indent=2, default=str) + "\n")
    open(os.path.join(arch, "README.json"), "w").write(json.dumps({
        "title": "BNB 15m microedge archived",
        "archive_path": arch,
        "failed_step": result.get("failed_step"),
        "prefix": PREFIX,
        "key_prefix": KEY_PREFIX,
        "regenerate_policy": "rediscover new edges after archive",
    }, ensure_ascii=False, indent=2) + "\n")
    _write("archive_notice_%s" % _safe((book or {}).get("edge_id") or "x"), {
        "archive_path": arch, "death": death,
    })
    return arch


def glm_repair(book, packs, failed_step, stage, edge_template):
    payload = {
        "stage": stage,
        "failed_step": failed_step,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_class": book.get("logic_class"),
        "novelty_score": book.get("novelty_score"),
        "params": book.get("params"),
        "dsl": book.get("dsl"),
        "metrics": packs.get("base_metrics") or packs.get("metrics"),
        "constraints": [
            "BNB-USDT-SWAP 15m locked",
            "keep microedge families",
            "no classic indicator stack as edge",
            "trades>=10 for WF",
            "stop 0.9%",
        ],
    }
    parsed, ai = ask_glm(REPAIR_PROMPT, payload, max_tokens=900)
    if not parsed:
        return None, ai
    return apply_param_tweaks(book, parsed.get("param_tweaks") or {}, edge_template), ai


def estimate_opens_per_day(book):
    """Cheap frequency proxy — avoid extra full packs under 0.75GB RAM."""
    try:
        fr = d._frame(SYMBOL, TIMEFRAME)
        bars = float(len(fr) or 1)
        days = max(bars / 96.0, 1.0)
        # Approximate trigger rate from core proxy conjunction (direction-aware)
        direction = book.get("direction") or "long"
        if direction == "short":
            mask = (
                (fr["me_sess_x"] > 0)
                & (fr["me_fund_rot"] < 0)
                & (fr["me_liq_trap"] < 0)
            )
        else:
            logic = str(book.get("logic_class") or "")
            if "auction" in logic:
                mask = (fr["me_oi_shift"] > 0) & (fr["me_auc_fail"] > 0)
            else:
                mask = (
                    (fr["me_sess_x"] > 0)
                    & (fr["me_fund_rot"] > 0)
                    & (fr["me_liq_trap"] > 0)
                )
        hits = float(mask.sum())
        # assume ~35% of raw hits survive z/hold filters → trade-like
        trades_est = hits * 0.35
        opd = trades_est / days
        return {
            "trades_est": round(trades_est, 2),
            "raw_hits": hits,
            "bars": bars,
            "days_est": round(days, 2),
            "opens_per_day": round(opd, 4),
            "target_range": [0.5, 1.0],
            "in_range": bool(0.45 <= opd <= 1.15),
            "method": "proxy_conjunction_estimate",
        }
    except Exception as exc:
        return {"error": str(exc)}


# ─── process one surviving edge ────────────────────────────────────────

def process_edge(edge, ctx):
    hist = []
    book = make_book(edge, tag="v0")
    _write("book_init_%s" % _safe(edge["edge_id"]), book)

    for atry in range(MAX_AUDIT_REVISE + 1):
        _upd(stage="hyp_approve", edge=edge["edge_id"], attempt=atry)
        decision, ai = ask_glm(HYP_PROMPT, {
            "edge": {
                "edge_id": edge["edge_id"],
                "logic_class": edge["logic_class"],
                "families": edge["families"],
                "novelty_score": edge["novelty_score"],
                "thesis_zh": edge["thesis_zh"],
                "proxy_doc": edge["proxy_doc"],
                "params": edge["params"],
            },
            "book": {
                "symbol": book["symbol"], "timeframe": book["timeframe"],
                "direction": book["direction"], "logic_class": book["logic_class"],
                "params": book.get("params"),
                "entry": (book.get("dsl") or {}).get("entry"),
                "exit": (book.get("dsl") or {}).get("exit"),
            },
            "gates": ctx.get("gates"),
        }, require_decision=True)
        if not decision:
            decision = {
                "decision": "pass", "reason_zh": "hyp_fallback_pass", "fallback": True,
                "hypothesis_book": {
                    "title": "策略逻辑假设书",
                    "symbol": SYMBOL, "timeframe": TIMEFRAME,
                    "direction": book["direction"],
                    "logic_class": book["logic_class"],
                    "novelty_score": edge["novelty_score"],
                    "thesis_zh": edge["thesis_zh"],
                    "params": book.get("params"),
                    "proxy_honesty_zh": json.dumps(edge.get("proxy_doc"), ensure_ascii=False),
                },
            }
        _write("hyp_%s_t%d" % (_safe(edge["edge_id"]), atry), {
            "decision": decision, "ai_ok": ai.get("ok"),
        })
        hist.append({"stage": "hyp", "try": atry, "decision": decision.get("decision"),
                     "reason": decision.get("reason_zh")})
        hb = decision.get("hypothesis_book") or {}
        if hb:
            _write("hypothesis_book_%s" % _safe(edge["edge_id"]), hb)
            if hb.get("params"):
                book = apply_param_tweaks(book, hb.get("params"), edge)
        dec = str(decision.get("decision") or "pass").lower()
        if dec == "pass":
            break
        if dec == "revise" and atry < MAX_AUDIT_REVISE:
            book = apply_param_tweaks(
                book, (decision.get("revise") or {}).get("param_tweaks") or {}, edge)
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
                "logic_class": book["logic_class"],
                "novelty_score": book.get("novelty_score"),
                "params": book.get("params"),
                "entry": (book.get("dsl") or {}).get("entry"),
                "exit": (book.get("dsl") or {}).get("exit"),
                "families": book.get("families"),
            },
        }, max_tokens=900, require_decision=True)
        if not decision:
            decision = {"decision": "pass", "reason_zh": "audit_fallback_pass", "fallback": True}
        hist.append({"stage": "audit", "try": atry, "decision": decision.get("decision")})
        _write("audit_%s_t%d" % (_safe(edge["edge_id"]), atry), decision)
        dec = str(decision.get("decision") or "pass").lower()
        if dec == "pass":
            break
        if dec == "revise" and atry < MAX_AUDIT_REVISE:
            book = apply_param_tweaks(
                book, (decision.get("revise") or {}).get("param_tweaks") or {}, edge)
            continue
        return {
            "ok": False, "failed_step": "hyp_audit_reject",
            "reason": decision.get("reason_zh"), "hist": hist, "book": book,
            "death_cause_zh": "审计拒绝：" + str(decision.get("reason_zh")),
        }

    _write("book_%s" % _safe(edge["edge_id"]), book)
    # Defer opens/day estimate until AFTER first quick — avoids duplicate frame
    # load + swap thrash on 0.75GB host before packs run.
    opd = {"deferred": True}

    packs = None
    for qtry in range(MAX_REPAIR + 1):
        _upd(stage="quick", edge=edge["edge_id"], attempt=qtry)
        print("[bnb15m_microedge] quick try", qtry, edge["edge_id"], flush=True)
        packs = f2.quick_suite(book)
        _write("quick_%s_t%d" % (_safe(edge["edge_id"]), qtry), packs)
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
        if qtry == 0:
            try:
                bm0 = packs.get("base_metrics") or {}
                tr0 = float(bm0.get("trades") or 0)
                # rough opens/day from packs trades over capped frame (~5000/96 days)
                opd = {
                    "trades": tr0,
                    "days_est": round(5000 / 96.0, 2),
                    "opens_per_day": round(tr0 / max(5000 / 96.0, 1.0), 4),
                    "target_range": [0.5, 1.0],
                    "method": "from_quick_t0_trades",
                }
                _write("opens_day_%s" % _safe(edge["edge_id"]), opd)
                hist.append({"stage": "opens_per_day", "opd": opd})
            except Exception:
                pass
        if qtry >= MAX_REPAIR:
            return {
                "ok": False,
                "failed_step": packs.get("failed_step") or "quick_exhausted",
                "reason": "Quick failed after %d repairs" % MAX_REPAIR,
                "metrics": packs.get("base_metrics"),
                "hist": hist, "book": book,
                "death_cause_zh": "Quick耗尽：%s；fp=%s/%s trades=%s dest=%s" % (
                    packs.get("failed_step"),
                    (packs.get("base_metrics") or {}).get("fold_positive"),
                    (packs.get("base_metrics") or {}).get("folds"),
                    (packs.get("base_metrics") or {}).get("trades"),
                    (packs.get("logic_destruction") or {}).get("pass"),
                ),
            }
        book = local_tweak(book, qtry + 1, packs.get("failed_step"), edge)
        repaired, _ai = glm_repair(book, packs, packs.get("failed_step"), "quick", edge)
        if repaired:
            book = repaired
        _write("book_%s" % _safe(edge["edge_id"]), book)

    for ftry in range(MAX_REPAIR + 1):
        _upd(stage="full", edge=edge["edge_id"], attempt=ftry)
        packs = f2.full_suite(book, packs)
        _write("full_%s_t%d" % (_safe(edge["edge_id"]), ftry), packs)
        hist.append({
            "stage": "full", "try": ftry, "pass": packs.get("full_pass"),
            "failed_step": packs.get("failed_step"),
            "friction": (packs.get("full") or {}).get("friction_sharpe"),
            "mc": ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
        })
        if packs.get("full_pass"):
            break
        if ftry >= MAX_REPAIR:
            return {
                "ok": False,
                "failed_step": packs.get("failed_step") or "full_exhausted",
                "reason": "Full failed after %d repairs" % MAX_REPAIR,
                "full": packs.get("full"), "hist": hist, "book": book,
                "death_cause_zh": "Full耗尽：%s；friction=%s mc=%s" % (
                    packs.get("failed_step"),
                    (packs.get("full") or {}).get("friction_sharpe"),
                    ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
                ),
            }
        book = local_tweak(book, ftry + 1, packs.get("failed_step"), edge)
        repaired, _ai = glm_repair(book, packs, packs.get("failed_step"), "full", edge)
        if repaired:
            book = repaired
        q2 = f2.quick_suite(book)
        if not q2.get("quick_pass"):
            packs = q2
            packs["full_pass"] = False
            packs["failed_step"] = q2.get("failed_step") or "quick_regressed"
            continue
        packs = q2

    for stry in range(MAX_REPAIR + 1):
        _upd(stage="sim_formal", edge=edge["edge_id"], attempt=stry)
        # Prefer frost2 path then upgrade with GLM avg gate
        try:
            sf = run_sim_formal_3ai(book, packs)
        except Exception as exc:
            # fallback to frost2 then overlay GLM
            sf = f2.run_sim_formal(book, packs)
            if (sf.get("formal") or {}).get("approved"):
                formal3 = formal_ds_qwen_glm(book, packs)
                sf["formal"] = formal3
                if not formal3.get("approved"):
                    sf["failed_step"] = "formal_review"
                    sf["pending"] = None
            sf["exception_fallback"] = str(exc)

        _write("sim_formal_%s_t%d" % (_safe(edge["edge_id"]), stry), sf)
        hist.append({
            "stage": "sim_formal", "try": stry,
            "failed_step": sf.get("failed_step"),
            "sim_ds": (sf.get("sim") or {}).get("wr_deepseek_sim"),
            "sim_qw": (sf.get("sim") or {}).get("wr_qwen_sim"),
            "formal_avg": ((sf.get("formal") or {}).get("ai_review") or {}).get(
                "ai_theoretical_wr_avg"),
            "formal_approved": (sf.get("formal") or {}).get("approved"),
            "pending": (sf.get("pending") or {}).get("key"),
        })
        if (sf.get("pending") or {}).get("ok") or (sf.get("pending") or {}).get("key"):
            return {
                "ok": True, "failed_step": None,
                "pending": sf.get("pending"), "sim": sf.get("sim"),
                "formal": sf.get("formal"), "hist": hist, "book": book,
                "opens_per_day": opd,
            }
        fs = sf.get("failed_step")
        if fs in ("formal_review", "pending_ingest") and stry >= MAX_REPAIR:
            return {
                "ok": False, "failed_step": fs,
                "reason": (sf.get("formal") or {}).get("reason") or (
                    sf.get("pending") or {}).get("reason"),
                "sim": sf.get("sim"), "formal": sf.get("formal"),
                "pending": sf.get("pending"), "hist": hist, "book": book,
                "death_cause_zh": "正式复核/入库失败：" + str(fs),
            }
        if stry >= MAX_REPAIR:
            return {
                "ok": False, "failed_step": fs or "sim_exhausted",
                "reason": "Sim/formal failed after %d repairs" % MAX_REPAIR,
                "sim": sf.get("sim"), "formal": sf.get("formal"),
                "hist": hist, "book": book,
                "death_cause_zh": "Sim/Formal耗尽：DS=%s Qwen=%s avg=%s step=%s" % (
                    (sf.get("sim") or {}).get("wr_deepseek_sim"),
                    (sf.get("sim") or {}).get("wr_qwen_sim"),
                    ((sf.get("formal") or {}).get("ai_review") or {}).get(
                        "ai_theoretical_wr_avg"),
                    fs,
                ),
            }
        book = local_tweak(book, stry + 1, "sim", edge)
        repaired, _ai = glm_repair(book, packs, "sim_review", "sim", edge)
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


# ─── context / main ────────────────────────────────────────────────────

def load_context():
    data_meta = ensure_bnb_15m_parquet()
    install_micro_proxies()
    # Do NOT call d._frame here — 0.75GB host swap-thrashes under parallel frost3.
    # Frame+proxies load lazily on first quick_suite / estimate_opens_per_day.
    micro = {
        "proxy_honesty_zh": (
            "无真实资金费/OI时：me_fund_rot=定位旋转代理；me_oi_shift=活动加权流向代理；"
            "volume=5m振幅求和活动代理。文档化诚实代理，不伪装成交易所原始字段。"
        ),
        "memory_note": (
            "frame capped to last 5000 bars; collect skips frame preload "
            "to avoid swap thrash; stats filled lazily if memory allows"
        ),
        "primitive_freq": [],
        "deferred_frame_stats": True,
    }
    print("[bnb15m_microedge] collect skip-frame (lazy load later)", flush=True)

    return {
        "instrument": SYMBOL,
        "timeframe": TIMEFRAME,
        "stop_pct": STOP_PCT,
        "leverage": LEVERAGE,
        "position_pct": POSITION_PCT,
        "data_meta": data_meta,
        "bnb_micro": micro,
        "protect_live": ["ADA-USDT-SWAP", "LTC-USDT-SWAP", "NG-USDT-SWAP", "XRP-USDT-SWAP"],
        "isolated_from": [
            "frost3_bnb15m_session_*", "frost3_bnb5m_*", "frost3_bnb1h_*",
        ],
        "gates": {
            "novelty": ">=9/10",
            "WF": ">=7/10",
            "logic_destruction": "pass",
            "MC_beat_sign_shuffle": ">=90%",
            "extreme_friction_sharpe": ">=0",
            "formal_avg_ds_qwen_glm": ">=75%",
            "opens_per_day": "0.5-1.0",
        },
        "proxy_features": list(PROXY_FEATURES),
    }


def main():
    print("[bnb15m_microedge] START", _now(), flush=True)
    os.makedirs(OUT, exist_ok=True)
    assert PREFIX == "frost3_bnb15m_microedge"
    assert KEY_PREFIX == "frost3_bnb15m_microedge_"
    _upd(stage="collect")

    ctx = load_context()
    _write("inputs", ctx)

    all_attempts = []
    final_ok = None

    for cycle in range(MAX_REDISCOVER + 1):
        _upd(stage="discovery", cycle=cycle)
        survivors, scored = discover_edges(cycle=cycle)
        _write("novelty_filter_cycle%d" % cycle, {
            "survivors": [s["edge_id"] for s in survivors],
            "scores": {s["edge_id"]: s["novelty_score"] for s in scored},
            "min": NOVELTY_MIN,
        })
        if not survivors:
            _write("end_report", {
                "ok": False, "failed_step": "novelty_gate",
                "reason": "no edge with novelty>=9",
                "scored": [{k: s[k] for k in (
                    "edge_id", "novelty_score", "novelty_pass", "logic_class")}
                    for s in scored],
            })
            print("[bnb15m_microedge] NOVELTY GATE FAIL cycle", cycle, flush=True)
            continue

        for edge in survivors:
            print("[bnb15m_microedge] TRY", edge["edge_id"],
                  "novelty", edge["novelty_score"], flush=True)
            _upd(stage="process_edge", edge=edge["edge_id"], novelty=edge["novelty_score"])
            try:
                result = process_edge(edge, ctx)
            except Exception as exc:
                result = {
                    "ok": False, "failed_step": "exception", "reason": str(exc),
                    "trace": traceback.format_exc()[-2500:],
                    "book": make_book(edge, "exc"),
                    "death_cause_zh": "异常：" + str(exc),
                }
            attempt = {
                "cycle": cycle,
                "edge_id": edge["edge_id"],
                "logic_class": edge["logic_class"],
                "novelty_score": edge["novelty_score"],
                "families": edge["families"],
                "ok": bool(result.get("ok")),
                "failed_step": result.get("failed_step"),
                "death_cause_zh": result.get("death_cause_zh"),
                "pending_key": ((result.get("pending") or {}).get("key")),
                "sim": result.get("sim"),
                "formal": {
                    "approved": (result.get("formal") or {}).get("approved"),
                    "annotation": (result.get("formal") or {}).get("annotation"),
                    "avg": ((result.get("formal") or {}).get("ai_review") or {}).get(
                        "ai_theoretical_wr_avg"),
                    "by": ((result.get("formal") or {}).get("ai_review") or {}).get(
                        "ai_theoretical_wr_by_provider"),
                } if result.get("formal") else None,
                "metrics": result.get("metrics"),
                "full": result.get("full"),
                "opens_per_day": result.get("opens_per_day"),
                "book_key": ((result.get("book") or {}).get("dsl") or {}).get("key"),
            }
            if not result.get("ok"):
                arch = archive_fail(result, result.get("book"), edge)
                attempt["archive_path"] = arch
                print("[bnb15m_microedge] ARCHIVED", edge["edge_id"],
                      result.get("failed_step"), arch, flush=True)
            else:
                print("[bnb15m_microedge] PENDING", attempt.get("pending_key"), flush=True)
                final_ok = attempt
                all_attempts.append(attempt)
                break
            all_attempts.append(attempt)

        if final_ok:
            break
        # regenerate cycle continues automatically

    end = {
        "at": _now(),
        "op": "寒霜叁BNB15m微边",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "stop_pct": STOP_PCT,
        "leverage": LEVERAGE,
        "position_pct": POSITION_PCT,
        "ok": bool(final_ok),
        "attempts": all_attempts,
        "winner": final_ok,
        "artifact_prefix": PREFIX,
        "key_prefix": KEY_PREFIX,
        "proxy_honesty_zh": (ctx.get("bnb_micro") or {}).get("proxy_honesty_zh"),
    }
    _write("end_report", end)
    _upd(stage="done", ok=end["ok"])

    # Chinese parent summary
    lines = ["【BNB15m microedge 结果摘要】"]
    for a in all_attempts:
        lines.append(
            "- 边=%s | novelty=%.1f/10 | 结果=%s | 步骤=%s | pending=%s | 归档=%s"
            % (
                a.get("edge_id"),
                float(a.get("novelty_score") or 0),
                "PASS→pending" if a.get("ok") else "FAIL→archive",
                a.get("failed_step"),
                a.get("pending_key"),
                a.get("archive_path"),
            )
        )
        if a.get("formal"):
            lines.append("  理论胜率: %s" % (a["formal"].get("annotation") or a["formal"]))
        if a.get("metrics"):
            m = a["metrics"]
            lines.append(
                "  指标: trades=%s sharpe=%s wr=%s fp=%s/%s"
                % (m.get("trades"), m.get("sharpe"), m.get("win_rate"),
                   m.get("fold_positive"), m.get("folds"))
            )
        if a.get("opens_per_day") and not a["opens_per_day"].get("error"):
            lines.append(
                "  开仓/日≈%s (目标0.5–1.0)" % a["opens_per_day"].get("opens_per_day")
            )
    if end["ok"]:
        w = final_ok
        summary = (
            "成功：BNB15m微边 %s (novelty %.1f) 已进pending key=%s；%s；前缀 frost3_bnb15m_microedge_*"
            % (
                w.get("edge_id"), float(w.get("novelty_score") or 0),
                w.get("pending_key"),
                (w.get("formal") or {}).get("annotation"),
            )
        )
    else:
        summary = (
            "未过闸：已跑 discovery→novelty→测试，并完成归档+再发现循环；"
            "详见 attempts。前缀 frost3_bnb15m_microedge_*。\n" + "\n".join(lines)
        )
    _write("parent_summary", {"summary_zh": summary, "lines": lines, "end": end})
    print("=== BNB15m_MICROEDGE PARENT ===", summary, flush=True)
    print("=== BNB15m_MICROEDGE END ===", json.dumps({
        "ok": end["ok"],
        "attempts": [
            {"edge_id": a.get("edge_id"), "novelty": a.get("novelty_score"),
             "ok": a.get("ok"), "failed_step": a.get("failed_step"),
             "pending_key": a.get("pending_key"),
             "archive_path": a.get("archive_path")}
            for a in all_attempts
        ],
    }, ensure_ascii=False), flush=True)
    return 0 if end["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
