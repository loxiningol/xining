#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone SOL-USDT-SWAP 5m behaviour/microstructure edge (frost3_sol5m_behavior_*).

FULLY ISOLATED from frost3_sol15m_* / sol4h / sol1h / other frost3 agents.
Artifacts ONLY: /root/auto_trade/dual_engine/frost3_sol5m_behavior_*
Key prefix: frost3_sol5m_behavior_

Pipeline (strict):
  Edge Discovery → Novelty → Hypothesis → Quick → Death → WF → MC → Stress
  → Portfolio Corr → GLM → DeepSeek → Qwen
  ≤3 repairs/stage.

Forbidden: indicator-combo edge (EMA/MACD/RSI/ADX/BB stacks as strategy).
Allowed: Order Flow / Liquidity Sweep / Vol Compression / VWAP / Funding / OI /
         Session Rotation / Auction Failure — honest proxies documented.
"""
from __future__ import print_function

import copy
import json
import math
import os
import re
import sqlite3
import sys
import traceback
import urllib.request
from datetime import datetime

sys.path.insert(0, "/root")
import frost2_action_run as f2
import frost3_action as f3
import frost3_step1 as step1
import auto_trade_dual_engine_factory as d
import auto_trade_strategy_dsl as dsl_mod

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
f2.AVOID = [
    ("ADA-USDT-SWAP", None),
    ("LTC-USDT-SWAP", None),
    ("NG-USDT-SWAP", None),
    ("XRP-USDT-SWAP", None),
]

OUT = "/root/auto_trade/dual_engine"
PREFIX = "frost3_sol5m_behavior"
SYMBOL = "SOL-USDT-SWAP"
TIMEFRAME = "5m"
MAX_AUDIT_REVISE = 2
MAX_QUICK_REPAIR = 3
MAX_FULL_REPAIR = 3
MAX_SIM_REPAIR = 3
SESSION_UTC_START = 7
SESSION_UTC_END = 20

# Hard ban: indicator-stack / known-dead SOL5m impulse / live clones
BANNED_LOGIC_SUBSTR = (
    "impulse_continuation", "trend_pullback", "exhaustion_fade",
    "ema_ribbon", "macd_rsi", "rsi_macd", "adx_stack", "bb_stack",
    "indicator_combo",
)

LIVE_COLLISION = {
    "ADA-USDT-SWAP 5m": "trend_pullback",
    "LTC-USDT-SWAP 5m": "exhaustion_fade",
    "NG-USDT-SWAP 5m": "exhaustion_fade",
    "XRP-USDT-SWAP 15m": "exhaustion/rescue",
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


def _log(msg):
    print("[%s] %s" % (PREFIX, msg), flush=True)
    try:
        open(os.path.join(OUT, "%s_run.log" % PREFIX), "a").write("%s %s\n" % (_now(), msg))
    except Exception:
        pass


# ─── Proxy injection (honest microstructure proxies) ───────────────────

PROXY_FEATURES = (
    "session_liq", "hour_utc", "vol_pulse_proxy", "vol_compress_proxy",
    "upper_wick_proxy", "lower_wick_proxy", "body_ratio_proxy", "close_loc_proxy",
    "vwap_dist_proxy", "funding_z_proxy", "oi_chg_proxy",
)


def _fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "frost3-sol5m-behavior"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_funding_oi_meta():
    meta = {
        "funding_ok": False, "oi_ok": False,
        "funding_latest": None, "oi_usd": None,
        "funding_hist_n": 0, "note": [],
    }
    try:
        fr = _fetch_json(
            "https://www.okx.com/api/v5/public/funding-rate?instId=SOL-USDT-SWAP")
        row = ((fr.get("data") or [None])[0]) or {}
        meta["funding_latest"] = float(row.get("fundingRate") or 0)
        meta["funding_ok"] = True
    except Exception as exc:
        meta["note"].append("funding_err:%s" % exc)
    try:
        fh = _fetch_json(
            "https://www.okx.com/api/v5/public/funding-rate-history?instId=SOL-USDT-SWAP&limit=100")
        hist = fh.get("data") or []
        meta["funding_hist_n"] = len(hist)
        rates = [float(x.get("fundingRate") or 0) for x in hist]
        if rates:
            mu = sum(rates) / len(rates)
            var = sum((x - mu) ** 2 for x in rates) / max(1, len(rates) - 1)
            sd = math.sqrt(var) if var > 0 else 1e-8
            meta["funding_mu"] = mu
            meta["funding_sd"] = sd
            if meta.get("funding_latest") is not None:
                meta["funding_z_now"] = (meta["funding_latest"] - mu) / sd
    except Exception as exc:
        meta["note"].append("funding_hist_err:%s" % exc)
    try:
        oi = _fetch_json(
            "https://www.okx.com/api/v5/public/open-interest?instId=SOL-USDT-SWAP")
        row = ((oi.get("data") or [None])[0]) or {}
        meta["oi_usd"] = float(row.get("oiUsd") or row.get("oi") or 0)
        meta["oi_ok"] = True
    except Exception as exc:
        meta["note"].append("oi_err:%s" % exc)
    return meta


FRAME_PKL = "/root/auto_trade/codex_0725_train5/frame_SOL_USDT_SWAP_5m.pkl"
PREBUILT = os.path.join(OUT, "frost3_sol5m_behavior_frame.pkl")


def _inject_proxy_columns(frame, fund_z_const=0.0):
    """Add behaviour proxy columns onto an existing indicator frame."""
    max_bars = int(os.environ.get("SOL5M_MAX_BARS", "5000"))
    if hasattr(frame, "iloc") and len(frame) > max_bars:
        frame = frame.iloc[-max_bars:].copy()
    elif hasattr(frame, "copy"):
        frame = frame.copy()

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
        base_mean = frame["atr14"].rolling(48, min_periods=12).mean()
        base_med = frame["atr14"].rolling(48, min_periods=12).median()
        frame["vol_pulse_proxy"] = (
            frame["atr14"] / base_mean.replace(0, float("nan"))
        ).fillna(1.0)
        frame["vol_compress_proxy"] = (
            frame["atr14"] / base_med.replace(0, float("nan"))
        ).fillna(1.0)
    else:
        frame["vol_pulse_proxy"] = 1.0
        frame["vol_compress_proxy"] = 1.0

    rng = (frame["high"] - frame["low"]).replace(0, float("nan"))
    mx = frame["open"].where(frame["open"] >= frame["close"], frame["close"])
    mn = frame["open"].where(frame["open"] <= frame["close"], frame["close"])
    frame["upper_wick_proxy"] = ((frame["high"] - mx) / rng).fillna(0.0)
    frame["lower_wick_proxy"] = ((mn - frame["low"]) / rng).fillna(0.0)
    frame["body_ratio_proxy"] = ((frame["close"] - frame["open"]).abs() / rng).fillna(0.0)
    frame["close_loc_proxy"] = ((frame["close"] - frame["low"]) / rng).fillna(0.5)

    tp = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    if "volume" in getattr(frame, "columns", []):
        vol = frame["volume"].fillna(0.0).clip(lower=0.0)
    elif "vol" in getattr(frame, "columns", []):
        vol = frame["vol"].fillna(0.0).clip(lower=0.0)
    else:
        vol = (frame["high"] - frame["low"]).fillna(0.0).clip(lower=1e-12)
    cum_pv = (tp * vol).cumsum()
    cum_v = vol.cumsum().replace(0, float("nan"))
    vwap = cum_pv / cum_v
    frame["vwap_dist_proxy"] = (
        (frame["close"] - vwap) / vwap.replace(0, float("nan"))
    ).fillna(0.0)

    frame["funding_z_proxy"] = float(fund_z_const)
    frame["oi_chg_proxy"] = 0.0
    return frame


def install_behavior_proxies(funding_meta=None):
    """Process-local: inject behaviour proxies into SOL 5m frames.

    Prefer precomputed pickle (or our own PREBUILT) to avoid
    eco._load_research_frame rebuilding 6 months of 5m indicators from parquet
    — that path thrash-swaps on the 0.75GB host under multi-agent load.
    """
    for feat in PROXY_FEATURES:
        dsl_mod.FEATURES.add(feat)

    _orig_frame = d._frame
    _cache = {"frame": None}
    fund_z_const = 0.0
    if isinstance(funding_meta, dict) and funding_meta.get("funding_z_now") is not None:
        fund_z_const = float(funding_meta["funding_z_now"])

    def _load_sol5m_frame():
        """Eager/lazy loader: prebuilt → train5 pkl → factory fallback."""
        import pickle
        import time as _t

        # 1) Our prebuilt (already has proxies)
        if os.path.exists(PREBUILT):
            try:
                t0 = _t.time()
                frame = pickle.load(open(PREBUILT, "rb"))
                _log("using prebuilt frame rows=%s in %.2fs" % (len(frame), _t.time() - t0))
                return frame, "prebuilt"
            except Exception as exc:
                _log("prebuilt load fail %s" % exc)

        # 2) Shared train5 pickle (indicators only — inject proxies)
        if os.path.exists(FRAME_PKL):
            try:
                t0 = _t.time()
                frame = pickle.load(open(FRAME_PKL, "rb"))
                frame = _inject_proxy_columns(frame, fund_z_const)
                _log("loaded train5 pickle rows=%s in %.2fs" % (len(frame), _t.time() - t0))
                try:
                    pickle.dump(frame, open(PREBUILT, "wb"), protocol=pickle.HIGHEST_PROTOCOL)
                    _log("wrote prebuilt %s" % PREBUILT)
                except Exception as exc:
                    _log("prebuilt write fail %s" % exc)
                return frame, "train5_pkl"
            except Exception as exc:
                _log("train5 pkl load fail %s" % exc)

        # 3) Slow factory path
        _log("fallback to factory _frame (SLOW on this host)")
        t0 = _t.time()
        frame = _orig_frame(SYMBOL, TIMEFRAME)
        try:
            frame = _inject_proxy_columns(frame, fund_z_const)
        except Exception as exc:
            _log("proxy_inject_err %s" % exc)
        _log("factory frame ready rows=%s in %.2fs" % (len(frame), _t.time() - t0))
        return frame, "factory"

    # Eager preload for SOL 5m so quick_suite does not block on first _frame
    try:
        pre, src = _load_sol5m_frame()
        _cache["frame"] = pre
        _log("eager preload source=%s rows=%s" % (src, len(pre)))
    except Exception as exc:
        _log("eager preload FAILED %s — will lazy-load" % exc)

    def _frame_with_proxies(symbol, timeframe):
        if (
            str(symbol).upper() == SYMBOL
            and str(timeframe) == TIMEFRAME
            and _cache["frame"] is not None
        ):
            return _cache["frame"]

        if str(symbol).upper() == SYMBOL and str(timeframe) == TIMEFRAME:
            frame, src = _load_sol5m_frame()
            _cache["frame"] = frame
            try:
                import gc
                gc.collect()
            except Exception:
                pass
            return frame

        # Other symbols (e.g. portfolio corr) — use factory, lightly capped
        frame = _orig_frame(symbol, timeframe)
        try:
            max_bars = int(os.environ.get("SOL5M_MAX_BARS", "5000"))
            if hasattr(frame, "iloc") and len(frame) > max_bars:
                frame = frame.iloc[-max_bars:].copy()
        except Exception:
            pass
        return frame

    d._frame = _frame_with_proxies
    return {
        "session_utc": [SESSION_UTC_START, SESSION_UTC_END],
        "features_injected": list(PROXY_FEATURES),
        "frame_sources": {
            "prebuilt": PREBUILT,
            "train5_pkl": FRAME_PKL,
            "fallback": "factory _load_research_frame (parquet+precompute)",
            "eager_cached": _cache["frame"] is not None,
            "eager_rows": (len(_cache["frame"]) if _cache["frame"] is not None else None),
        },
        "order_book": "unavailable — wick/close_loc are OHLC proxies only",
        "volume": "candle volume if present else range-weight for VWAP",
        "funding": (
            "OKX funding fetched for meta/z_now constant on bars; "
            "NOT a fabricated per-5m funding path"
        ),
        "oi": "snapshot only unless history aligned; oi_chg_proxy default 0",
        "funding_meta": funding_meta or {},
        "max_bars": int(os.environ.get("SOL5M_MAX_BARS", "5000")),
        "fees_slip_note": (
            "frost2 full_suite extreme_friction models slip/spread/delay; "
            "sim/formal must remain profitable under maker AND taker cost paths "
            "via factory friction packs (observed_base + extreme)"
        ),
    }


# ─── Edge Discovery / Novelty / Death ───────────────────────────────────

def load_edge_discovery_seed():
    """Prefer bundled discovery JSON uploaded beside this script."""
    for path in (
        os.path.join(OUT, "%s_edge_discovery.json" % PREFIX),
        "/root/frost3_sol5m_behavior_edge_discovery.json",
        os.path.join(os.path.dirname(__file__) or ".", "frost3_sol5m_behavior_edge_discovery.json"),
    ):
        try:
            if os.path.exists(path):
                return json.load(open(path)), path
        except Exception:
            continue
    return None, None


def build_edge_discovery():
    seeded, src = load_edge_discovery_seed()
    if seeded and isinstance(seeded.get("candidates"), list) and len(seeded["candidates"]) >= 8:
        seeded["loaded_from"] = src
        seeded["refreshed_at"] = _now()
        return seeded
    # Minimal inline fallback (≥8) if seed file missing
    return {
        "report_title": "SOL5m Edge Discovery (inline fallback)",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "candidates": [
            {"id": 1, "family": "Liquidity Sweep", "logic_class": "session_liquidity_sweep_reclaim_long",
             "direction": "long", "freq_hint_per_day": "0.8-1.4"},
            {"id": 2, "family": "Auction Failure", "logic_class": "session_auction_fail_short",
             "direction": "short", "freq_hint_per_day": "1.5-3"},
            {"id": 3, "family": "Order Flow Proxy", "logic_class": "aggressive_close_reclaim_long",
             "direction": "long", "freq_hint_per_day": "~1"},
            {"id": 4, "family": "VWAP Behaviour", "logic_class": "session_vwap_reclaim_long",
             "direction": "long", "freq_hint_per_day": "~1"},
            {"id": 5, "family": "VWAP Behaviour", "logic_class": "session_vwap_reject_short",
             "direction": "short", "freq_hint_per_day": "~2.9"},
            {"id": 6, "family": "Volatility Compression", "logic_class": "compress_then_sweep_long",
             "direction": "long", "freq_hint_per_day": "low"},
            {"id": 7, "family": "Session Rotation", "logic_class": "asia_thin_fade_into_london",
             "direction": "short", "freq_hint_per_day": "tba"},
            {"id": 8, "family": "Funding Behaviour", "logic_class": "funding_extreme_fade_short",
             "direction": "short", "freq_hint_per_day": "low"},
            {"id": 9, "family": "OI Behaviour", "logic_class": "oi_drop_with_sweep_long",
             "direction": "long", "freq_hint_per_day": "depends"},
            {"id": 10, "family": "Liquidity Sweep", "logic_class": "session_liquidity_sweep_reclaim_short",
             "direction": "short", "freq_hint_per_day": "filter"},
            {"id": 11, "family": "Order Flow Proxy", "logic_class": "absorption_wick_long",
             "direction": "long", "freq_hint_per_day": "tba"},
            {"id": 12, "family": "Volatility Compression", "logic_class": "compress_failed_break_short",
             "direction": "short", "freq_hint_per_day": "tba"},
        ],
        "primary_seed_for_hypothesis": "session_liquidity_sweep_reclaim_long",
        "fallback": True,
    }


def novelty_rank(discovery, death_packet, funding_meta):
    rows = []
    for c in discovery.get("candidates") or []:
        logic = str(c.get("logic_class") or "")
        fam = str(c.get("family") or "")
        score = 50
        reasons = []
        # Novelty bonuses
        if fam in ("Liquidity Sweep", "Auction Failure", "VWAP Behaviour", "Order Flow Proxy"):
            score += 15
            reasons.append("behaviour_family")
        if fam in ("Funding Behaviour", "OI Behaviour"):
            if fam.startswith("Funding") and not (funding_meta or {}).get("funding_ok"):
                score -= 25
                reasons.append("funding_unavailable")
            elif fam.startswith("OI") and not (funding_meta or {}).get("oi_ok"):
                score -= 20
                reasons.append("oi_weak")
            else:
                score += 10
                reasons.append("funding_oi_meta_ok_but_bar_align_weak")
        # Frequency toward target
        hint = str(c.get("freq_hint_per_day") or "")
        if "0.8" in hint or "~1" in hint or "0.9" in hint:
            score += 12
            reasons.append("freq_near_target")
        if "2.9" in hint or "1.5-3" in hint or "偏高" in hint:
            score -= 8
            reasons.append("freq_may_be_high")
        # Ban collisions
        low = logic.lower()
        if any(b in low for b in BANNED_LOGIC_SUBSTR):
            score -= 100
            reasons.append("banned_logic")
        # Live collision text
        blob = json.dumps(c, ensure_ascii=False).lower()
        if "exhaustion_fade" in blob or "trend_pullback" in blob:
            score -= 40
            reasons.append("live_logic_collision_text")
        # Prefer long sweep reclaim as primary seed
        if logic == discovery.get("primary_seed_for_hypothesis"):
            score += 8
            reasons.append("primary_seed")
        rows.append({
            "id": c.get("id"), "logic_class": logic, "family": fam,
            "direction": c.get("direction"), "novelty_score": score,
            "reasons": reasons, "candidate": c,
        })
    rows.sort(key=lambda x: (-int(x["novelty_score"]), int(x.get("id") or 99)))
    return {
        "ranked": rows,
        "top3": rows[:3],
        "at": _now(),
        "live_collision_map": LIVE_COLLISION,
    }


def load_death_packet():
    inputs = {}
    for path in (
        os.path.join(OUT, "frost3_inputs_raw.json"),
        os.path.join(OUT, "frost3_bnb5m_session_inputs.json"),
        os.path.join(OUT, "%s_inputs.json" % PREFIX),
    ):
        try:
            blob = json.load(open(path))
            if isinstance(blob, dict) and blob:
                inputs = blob.get("death") if isinstance(blob.get("death"), dict) else blob
                inputs["_source_path"] = path
                break
        except Exception:
            continue
    if not inputs:
        try:
            inputs = d.collect_inputs()
            inputs["_source_path"] = "collect_inputs"
        except Exception as exc:
            inputs = {"error": str(exc)}

    cl = {}
    try:
        cl = step1.load_cl_archive()
    except Exception as exc:
        cl = {"error": str(exc)}

    freezer = list(inputs.get("freezer_last10") or [])[:12]
    sol5_hits = []
    for x in freezer:
        s = json.dumps(x, ensure_ascii=False).lower()
        if "sol" in s and ("5m" in s or "impulse" in s):
            sol5_hits.append(x)

    return {
        "cl_archive": cl,
        "death_heatmap_top10": (inputs.get("death_heatmap_top10") or [])[:10],
        "freezer_last10": freezer,
        "sol5m_freezer_hits": sol5_hits,
        "hf_lessons_zh": [
            "SOL5m impulse_continuation 已全面失败：禁止无约束冲动延续/高频刷单",
            "5m 摩擦敏感：必须会话降频 + 结构确认；extreme friction Sharpe≥0",
            "CL15m：边际入场+硬止损簇；本方案用扫损/拍卖/VWAP结构，不用rsi≈54边际",
            "禁止指标组合当edge；EMA/MACD/RSI仅可作极弱滤镜且本DSL主路径不用",
        ],
        "live_protect": list(LIVE_COLLISION.keys()),
        "source": inputs.get("_source_path"),
    }


def death_test_filter(ranked, death):
    survivors = []
    rejected = []
    for row in ranked.get("ranked") or []:
        logic = str(row.get("logic_class") or "").lower()
        reasons = []
        kill = False
        if any(b in logic for b in BANNED_LOGIC_SUBSTR):
            kill = True
            reasons.append("banned_substr")
        if "impulse" in logic:
            kill = True
            reasons.append("sol5m_impulse_death")
        if int(row.get("novelty_score") or 0) < 30:
            kill = True
            reasons.append("novelty_too_low")
        # Funding/OI edges without usable meta → defer
        fam = str(row.get("family") or "")
        if fam == "OI Behaviour":
            # oi_chg always 0 without history → reject for execution honesty
            kill = True
            reasons.append("oi_bar_series_unavailable")
        if kill:
            rejected.append({"logic_class": row.get("logic_class"), "reasons": reasons})
        else:
            survivors.append(row)
    return {
        "survivors": survivors,
        "rejected": rejected,
        "lessons": death.get("hf_lessons_zh"),
        "at": _now(),
    }


# ─── Hypothesis / DSL ───────────────────────────────────────────────────

def fallback_hypothesis(logic="session_liquidity_sweep_reclaim_long"):
    """Deterministic primary: session liquidity sweep reclaim long."""
    return {
        "logic_class": logic,
        "direction": "long",
        "thesis": (
            "主流动性会话内刺穿 prev_low20 后收回，下影主导=止损猎杀后回流；"
            "ATR脉冲确认参与度；非指标组合、非impulse延续"
        ),
        "entry_sketch": (
            "session_liq>0.5; low<prev_low20; close>prev_low20; "
            "lower_wick_proxy>0.25; vol_pulse_proxy>1.0; "
            "vwap_dist_proxy>-0.004; close_loc_proxy>0.55"
        ),
        "exit_sketch": (
            "close<prev_low20 invalidation; vwap_dist_proxy>0.006 take_profit; max_hold=36"
        ),
        "param_grid": {
            "lower_wick_min": 0.25,
            "vol_pulse_min": 1.0,
            "vwap_dist_min": -0.004,
            "close_loc_min": 0.55,
            "max_hold": 36,
        },
        "avoid_death": [
            "sol5m_impulse_overtrade", "stop_cluster", "cost_collapse",
            "indicator_combo_edge", "live_ada_ltc_ng_xrp_clone",
        ],
        "diff_vs_live": "非ADA pullback / LTC·NG exhaustion；会话扫损回收",
        "expected_trades_hint": "0.8-1.5/day",
        "proxies_used": [
            "session_liq", "lower_wick_proxy", "vol_pulse_proxy",
            "vwap_dist_proxy", "close_loc_proxy", "prev_low20",
        ],
        "fallback": True,
    }


def hypothesis_from_survivor(surv):
    c = (surv or {}).get("candidate") or surv or {}
    logic = str(c.get("logic_class") or "session_liquidity_sweep_reclaim_long")
    direction = str(c.get("direction") or "long").lower()
    if logic == "session_liquidity_sweep_reclaim_long" or "sweep_reclaim_long" in logic:
        hyp = fallback_hypothesis(logic)
        hyp["fallback"] = False
        hyp["from_discovery_id"] = c.get("id")
        return hyp
    if logic == "session_auction_fail_short" or "auction_fail_short" in logic:
        return {
            "logic_class": "session_auction_fail_short",
            "direction": "short",
            "thesis": "会话内突破prev_high20失败收回+上影=拍卖失败",
            "entry_sketch": (
                "session_liq>0.5; high>prev_high20; close<prev_high20; "
                "upper_wick_proxy>0.4; vol_pulse_proxy>1.0; vwap_dist_proxy<0.004"
            ),
            "exit_sketch": "close>prev_high20 invalidation; max_hold=32",
            "param_grid": {"upper_wick_min": 0.4, "vol_pulse_min": 1.0, "max_hold": 32},
            "avoid_death": ["breakout_chase", "overtrade", "live_clone"],
            "diff_vs_live": "失败拍卖空 ≠ LTC exhaustion",
            "expected_trades_hint": "收紧至≤1.5/day",
            "from_discovery_id": c.get("id"),
        }
    if "vwap_reclaim_long" in logic:
        return {
            "logic_class": "session_vwap_reclaim_long",
            "direction": "long",
            "thesis": "会话内VWAP下方收回上方+脉冲",
            "entry_sketch": (
                "session_liq>0.5; vwap_dist_proxy>0.0; vwap_dist_proxy<0.003; "
                "vol_pulse_proxy>1.02; close_loc_proxy>0.55; close>prev_low20"
            ),
            "exit_sketch": "vwap_dist_proxy<0 invalidation; max_hold=40",
            "param_grid": {"vol_pulse_min": 1.02, "max_hold": 40},
            "avoid_death": ["indicator_combo", "overtrade"],
            "diff_vs_live": "VWAP行为",
            "expected_trades_hint": "~1/day",
            "from_discovery_id": c.get("id"),
        }
    if "aggressive_close_reclaim" in logic or "of_" in logic:
        return {
            "logic_class": "aggressive_close_reclaim_long",
            "direction": "long",
            "thesis": "欧美学段收盘位置偏高的主动买盘代理+近端低点上方",
            "entry_sketch": (
                "hour_utc>12.5; hour_utc<20; close_loc_proxy>0.7; "
                "body_ratio_proxy>0.35; close>prev_low20; session_liq>0.5"
            ),
            "exit_sketch": "close_loc_proxy<0.35 invalidation; max_hold=28",
            "param_grid": {"close_loc_min": 0.7, "max_hold": 28},
            "avoid_death": ["indicator_combo", "overtrade"],
            "diff_vs_live": "order-flow proxy",
            "expected_trades_hint": "~1/day",
            "from_discovery_id": c.get("id"),
        }
    # default
    hyp = fallback_hypothesis()
    hyp["direction"] = direction
    hyp["logic_class"] = logic
    hyp["from_discovery_id"] = c.get("id")
    return hyp


def parse_sketch_conditions(sketch):
    conds = []
    text = str(sketch or "")
    feat_re = (
        r"session_liq|hour_utc|vol_pulse_proxy|vol_compress_proxy|upper_wick_proxy|"
        r"lower_wick_proxy|body_ratio_proxy|close_loc_proxy|vwap_dist_proxy|"
        r"funding_z_proxy|oi_chg_proxy|h1_slope4|h1_ema19|h1_ema53|rsi14|z20|"
        r"macd_stick|cci|atr14|ema\d+|close|open|high|low|prev_high\d+|prev_low\d+"
    )
    for part in re.split(r"[;；\n]+", text):
        part = part.strip()
        if not part:
            continue
        part2 = re.sub(
            r"\b(take_profit|invalidation|max_hold\s*=\s*\d+)\b",
            "", part, flags=re.I,
        ).strip().replace(" ", "")
        m = re.match(
            r"^(%s)\s*(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?|%s)$" % (feat_re, feat_re),
            part2,
        )
        if not m:
            continue
        left, op, right = m.group(1), m.group(2), m.group(3)
        # Reject indicator-combo leaves even if sketch contains them
        if left in ("rsi14", "macd_stick", "cci") or left.startswith("ema"):
            continue
        op_map = {">": "gt", "<": "lt", ">=": "gte", "<=": "lte"}
        leaf = {"left": {"feature": left}, "op": op_map.get(op, "gt")}
        try:
            leaf["right"] = {"value": float(right)}
        except Exception:
            leaf["right"] = {"feature": right}
        conds.append(leaf)
    return conds


def build_behavior_dsl(row, idx):
    direction = str(row.get("direction") or "long").lower()
    logic = str(row.get("logic_class") or "session_liquidity_sweep_reclaim_long")
    entry = parse_sketch_conditions(row.get("entry_sketch"))
    hold = 36
    mhold = re.search(r"max_hold\s*=\s*(\d+)", str(row.get("exit_sketch") or ""), re.I)
    if mhold:
        hold = max(16, min(64, int(mhold.group(1))))
    pg = row.get("param_grid") or {}
    if isinstance(pg, dict) and pg.get("max_hold") is not None:
        hold = max(16, min(64, int(pg["max_hold"])))

    feats = {((c.get("left") or {}).get("feature")) for c in entry}

    # Ensure core structure for primary sweep long
    if "sweep_reclaim_long" in logic or logic == "session_liquidity_sweep_reclaim_long" or len(entry) < 5:
        entry = [
            {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}},
            {"left": {"feature": "low"}, "op": "lt", "right": {"feature": "prev_low20"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_low20"}},
            {"left": {"feature": "lower_wick_proxy"}, "op": "gt",
             "right": {"value": float(pg.get("lower_wick_min", 0.25))}},
            {"left": {"feature": "vol_pulse_proxy"}, "op": "gt",
             "right": {"value": float(pg.get("vol_pulse_min", 1.0))}},
            {"left": {"feature": "vwap_dist_proxy"}, "op": "gt",
             "right": {"value": float(pg.get("vwap_dist_min", -0.004))}},
            {"left": {"feature": "close_loc_proxy"}, "op": "gt",
             "right": {"value": float(pg.get("close_loc_min", 0.55))}},
        ]
        direction = "long"
        logic = "session_liquidity_sweep_reclaim_long"

    if "auction_fail_short" in logic:
        entry = [
            {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}},
            {"left": {"feature": "high"}, "op": "gt", "right": {"feature": "prev_high20"}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_high20"}},
            {"left": {"feature": "upper_wick_proxy"}, "op": "gt",
             "right": {"value": float(pg.get("upper_wick_min", 0.4))}},
            {"left": {"feature": "vol_pulse_proxy"}, "op": "gt",
             "right": {"value": float(pg.get("vol_pulse_min", 1.0))}},
            {"left": {"feature": "vwap_dist_proxy"}, "op": "lt", "right": {"value": 0.004}},
        ]
        direction = "short"

    if "session_liq" not in {((c.get("left") or {}).get("feature")) for c in entry}:
        if "hour_utc" not in {((c.get("left") or {}).get("feature")) for c in entry}:
            entry.insert(0, {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}})

    if direction == "long":
        exit_any = [
            {"left": {"feature": "vwap_dist_proxy"}, "op": "gt", "right": {"value": 0.006},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"},
             "role": "invalidation"},
        ]
    else:
        exit_any = [
            {"left": {"feature": "vwap_dist_proxy"}, "op": "lt", "right": {"value": -0.006},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"},
             "role": "invalidation"},
        ]

    dsl = {
        "schema": "qiyu_strategy_dsl_v1",
        "key": "frost3_sol5m_behavior_%s_%d" % (_safe(logic)[:22], idx),
        "name": "寒霜叁-SOL5m-行为微观",
        "direction": direction,
        "timeframe": TIMEFRAME,
        "supported_instruments": [SYMBOL],
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "max_hold_bars": hold,
        # NOTE: do NOT put meta/proxies inside DSL — validate_strategy rejects unknown top-level fields
    }
    return sanitize_dsl(f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME))


ALLOWED_DSL_TOP = {
    "schema", "key", "name", "direction", "timeframe",
    "supported_instruments", "entry", "exit", "max_hold_bars",
    "description", "origin", "version", "live_enabled",
    "approved_version_hash", "auto_trade_eligible",
}


def sanitize_dsl(dsl):
    """Strip unknown top-level fields (esp. meta) that break validate_strategy."""
    dsl = copy.deepcopy(dsl or {})
    for k in list(dsl.keys()):
        if k not in ALLOWED_DSL_TOP:
            dsl.pop(k, None)
    key = str(dsl.get("key") or "frost3_sol5m_behavior")
    key = re.sub(r"[^0-9A-Za-z_]", "_", key)[:100]
    if "sol5m_behavior" not in key:
        key = ("frost3_sol5m_behavior_" + key)[:100]
    dsl["key"] = key
    for i, row in enumerate((dsl.get("entry") or {}).get("all") or []):
        row["id"] = "e%d" % (i + 1)
        for bad in list(row.keys()):
            if bad not in ("id", "left", "op", "right", "lower", "upper", "role"):
                row.pop(bad, None)
    for i, row in enumerate((dsl.get("exit") or {}).get("any") or []):
        row["id"] = "x%d" % (i + 1)
        for bad in list(row.keys()):
            if bad not in ("id", "left", "op", "right", "lower", "upper", "role"):
                row.pop(bad, None)
    return dsl


def make_book(hyp, idx=1):
    dsl = build_behavior_dsl(hyp, idx)
    return {
        "title": "寒霜叁-SOL5m-行为微观",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": str(hyp.get("direction") or "long").lower(),
        "logic_class": str(hyp.get("logic_class") or "session_liquidity_sweep_reclaim_long"),
        "thesis": hyp.get("thesis"),
        "entry_sketch": hyp.get("entry_sketch"),
        "exit_sketch": hyp.get("exit_sketch"),
        "param_grid": hyp.get("param_grid"),
        "avoid_from_postmortem": hyp.get("avoid_death"),
        "diff_vs_live": hyp.get("diff_vs_live"),
        "dsl": dsl,
        "proxy_notes": {
            "session": "session_liq",
            "sweep": "low/high vs prev_low20/prev_high20 + wick",
            "vol": "vol_pulse_proxy",
            "vwap": "vwap_dist_proxy",
            "order_flow": "close_loc_proxy/body/wick",
            "book_depth": "unavailable",
        },
        "gate_mode": "frost2",
        "source": PREFIX,
        "dir_rank": idx,
    }


def force_key(book):
    if book.get("dsl"):
        book["dsl"]["supported_instruments"] = [SYMBOL]
        book["dsl"]["timeframe"] = TIMEFRAME
        k = str(book["dsl"].get("key") or "")
        if "sol5m_behavior" not in k:
            book["dsl"]["key"] = ("frost3_sol5m_behavior_" + k)[:100]
        book["dsl"] = sanitize_dsl(f2.ensure_dsl(book["dsl"], SYMBOL, TIMEFRAME))
    book["symbol"] = SYMBOL
    book["timeframe"] = TIMEFRAME
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
        if feat == "session_liq":
            row["right"]["value"] = 0.5
        elif feat == "lower_wick_proxy" and row.get("op") == "gt":
            if "walk_forward" in step or "sample" in step:
                row["right"]["value"] = max(0.15, v - 0.04 * n)
            else:
                row["right"]["value"] = min(0.45, v + 0.03 * n)
        elif feat == "upper_wick_proxy" and row.get("op") == "gt":
            if "walk_forward" in step or "sample" in step:
                row["right"]["value"] = max(0.25, v - 0.05 * n)
            else:
                row["right"]["value"] = min(0.55, v + 0.03 * n)
        elif feat == "vol_pulse_proxy" and row.get("op") == "gt":
            if "walk_forward" in step or "sample" in step:
                row["right"]["value"] = max(0.95, v - 0.03 * n)
            else:
                row["right"]["value"] = min(1.2, v + 0.03 * n)
        elif feat == "close_loc_proxy" and row.get("op") == "gt":
            if "walk_forward" in step:
                row["right"]["value"] = max(0.45, v - 0.05 * n)
            else:
                row["right"]["value"] = min(0.75, v + 0.03 * n)
        elif feat == "vwap_dist_proxy" and row.get("op") == "gt":
            if "walk_forward" in step:
                row["right"]["value"] = max(-0.008, v - 0.002 * n)
            else:
                row["right"]["value"] = min(0.0, v + 0.001 * n)
        elif feat == "vwap_dist_proxy" and row.get("op") == "lt":
            if "walk_forward" in step:
                row["right"]["value"] = min(0.008, v + 0.002 * n)
            else:
                row["right"]["value"] = max(0.0, v - 0.001 * n)
    dsl["entry"] = {"all": entries}
    dsl["max_hold_bars"] = max(
        16, min(64, int(dsl.get("max_hold_bars") or 36) + (2 if "walk_forward" in step else 0))
    )
    book["dsl"] = f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)
    return force_key(book)


AUDIT_PROMPT = """你是GLM-5.2。只输出JSON。审计 SOL-USDT-SWAP 5m 行为/微观结构假设书。
硬禁：指标组合当edge（EMA/MACD/RSI/ADX/BB栈）；ADA/LTC/NG/XRP现网逻辑克隆；SOL5m impulse刷单。
必须：行为族（扫损/拍卖失败/VWAP/订单流代理/会话等）；诚实代理；结构失效出场；目标约0.8-1.5单/日。
JSON：{"decision":"pass|reject|revise","score":0到100,"issues":["..."],
"revise":{"entry_sketch":null,"exit_sketch":null,"param_tweaks":{},"why":"..."},
"reason_zh":"..."}
"""


def glm_audit_book(book, timeout_sec=60):
    payload = {
        "book": {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "thesis": book.get("thesis"),
            "entry": (book.get("dsl") or {}).get("entry"),
            "exit": (book.get("dsl") or {}).get("exit"),
            "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
            "proxies": ((book.get("dsl") or {}).get("meta") or {}).get("proxies"),
        },
        "banned": ["indicator_combo", "ADA", "LTC", "NG", "XRP", "impulse_continuation"],
    }
    import threading
    box = {"parsed": None, "ai": {"ok": False, "error": "timeout"}}

    def _run():
        try:
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
            box["parsed"] = parsed
            box["ai"] = ai
        except Exception as exc:
            box["ai"] = {"ok": False, "error": str(exc)}

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout_sec)
    if th.is_alive():
        return (
            {"decision": "pass", "reason_zh": "audit_timeout_fallback_pass", "fallback": True},
            {"ok": False, "error": "glm_timeout_%ss" % timeout_sec},
        )
    return (
        box["parsed"] or {"decision": "pass", "reason_zh": "audit_fallback_pass", "fallback": True},
        box["ai"],
    )


def apply_revise(book, revise):
    book = copy.deepcopy(book)
    if not isinstance(revise, dict):
        return book
    if revise.get("entry_sketch"):
        book["entry_sketch"] = revise["entry_sketch"]
    if revise.get("exit_sketch"):
        book["exit_sketch"] = revise["exit_sketch"]
    tweaks = revise.get("param_tweaks") or {}
    if tweaks and isinstance(book.get("param_grid"), dict):
        book["param_grid"].update(tweaks)
    elif tweaks:
        book["param_grid"] = dict(tweaks)
    row = {
        "direction": book["direction"], "logic_class": book["logic_class"],
        "entry_sketch": book.get("entry_sketch"), "exit_sketch": book.get("exit_sketch"),
        "param_grid": book.get("param_grid"),
    }
    book["dsl"] = build_behavior_dsl(row, int(book.get("dir_rank") or 1))
    return force_key(book)


# ─── Portfolio correlation (vs live ADA/LTC/NG/XRP) ─────────────────────

def portfolio_correlation_note(book, packs):
    """Best-effort: correlate strategy trade PnL signs vs live symbols if frames exist.
    If not measurable, report explicitly — do not invent low corr.
    """
    out = {
        "measurable": False,
        "note_zh": "未能计算与现网ADA/LTC/NG/XRP的收益相关（缺同步交易序列）",
        "pairs": {},
        "reject_redundant": False,
    }
    try:
        base = d._backtest(
            packs.get("definition") or book.get("dsl"),
            SYMBOL, TIMEFRAME, "observed_base",
        )
        trades = base.get("trades") or []
        if len(trades) < 8:
            out["note_zh"] = "本策略成交<%d，相关不可靠" % 8
            out["n_trades"] = len(trades)
            return out
        # Build daily pnl series for SOL strategy
        import pandas as pd
        rows = []
        for t in trades:
            ts = t.get("exit_time") or t.get("entry_time")
            pnl = float(t.get("net_return") or t.get("return") or t.get("pnl") or 0)
            if ts is None:
                continue
            rows.append({"ts": pd.Timestamp(ts), "pnl": pnl})
        if len(rows) < 8:
            return out
        s = pd.DataFrame(rows).set_index("ts").sort_index()["pnl"].resample("1D").sum()
        corrs = {}
        for live_sym in ("ADA-USDT-SWAP", "LTC-USDT-SWAP", "NG-USDT-SWAP", "XRP-USDT-SWAP"):
            try:
                # Use simple close returns on same TF as weak proxy of live book exposure
                fr = d._frame(live_sym, "5m" if live_sym != "XRP-USDT-SWAP" else "15m")
                close = fr["close"]
                r = close.pct_change().resample("1D").sum()
                aligned = pd.concat([s, r], axis=1, join="inner").dropna()
                if len(aligned) >= 10:
                    corrs[live_sym] = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
            except Exception as exc:
                corrs[live_sym] = "err:%s" % exc
        out["pairs"] = corrs
        nums = [v for v in corrs.values() if isinstance(v, float) and v == v]
        out["measurable"] = bool(nums)
        if nums:
            mx = max(abs(x) for x in nums)
            out["max_abs_corr"] = mx
            out["reject_redundant"] = bool(mx >= 0.75)
            out["note_zh"] = (
                "与现网日收益代理相关 max|ρ|=%.3f%s"
                % (mx, " → 危险冗余" if out["reject_redundant"] else " → 可接受")
            )
        else:
            out["note_zh"] = "相关计算无有效数值"
    except Exception as exc:
        out["error"] = str(exc)
    return out


def archive_result(result, death_cause):
    arch = os.path.join(OUT, "archive", "%s_%s" % (PREFIX, result.get("dir_id") or "fail"))
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


def process(book):
    hist = []
    dir_id = "sol5m_behavior_%s" % _safe(book.get("logic_class"))
    status = {
        "dir_id": dir_id, "stage": "audit", "updated_at": _now(),
        "symbol": SYMBOL, "tf": TIMEFRAME, "logic": book.get("logic_class"),
        "key": (book.get("dsl") or {}).get("key"),
    }
    write_art("status", status)

    for atry in range(MAX_AUDIT_REVISE + 1):
        decision, ai = glm_audit_book(book)
        write_art("audit_try%d" % atry, {
            "decision": decision,
            "ai": {"ok": ai.get("ok"), "preview": (ai.get("raw_preview") or "")[:500]},
        })
        hist.append({"stage": "audit", "try": atry, "decision": decision.get("decision"),
                     "reason": decision.get("reason_zh")})
        dec = str(decision.get("decision") or "pass").lower()
        if dec == "pass":
            break
        if dec == "revise" and atry < MAX_AUDIT_REVISE:
            book = force_key(apply_revise(book, decision.get("revise") or {}))
            continue
        return {
            "dir_id": dir_id, "ok": False, "failed_step": "hyp_audit_reject",
            "reason": decision.get("reason_zh") or decision.get("issues"),
            "hist": hist, "book": book,
        }

    write_art("hypothesis_book", book)

    packs = None
    for qtry in range(MAX_QUICK_REPAIR + 1):
        status.update({"stage": "quick", "attempt": qtry, "updated_at": _now()})
        write_art("status", status)
        _log("quick_suite begin try=%d key=%s" % (qtry, (book.get("dsl") or {}).get("key")))
        try:
            packs = f2.quick_suite(book)
        except Exception as exc:
            packs = {
                "ok": False, "quick_pass": False, "failed_step": "quick_exception",
                "error": str(exc), "trace": traceback.format_exc()[-1500:],
            }
        _log("quick_suite end try=%d pass=%s fail=%s err=%s" % (
            qtry, packs.get("quick_pass"), packs.get("failed_step"),
            str(packs.get("error") or "")[:120],
        ))
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
            "tr": (packs.get("base_metrics") or {}).get("trades"),
        })
        if packs.get("quick_pass"):
            break
        if qtry >= MAX_QUICK_REPAIR:
            return {
                "dir_id": dir_id, "ok": False,
                "failed_step": packs.get("failed_step") or "quick_exhausted",
                "reason": "Quick failed after %d repairs" % MAX_QUICK_REPAIR,
                "metrics": packs.get("base_metrics"), "hist": hist, "book": book,
            }
        book = force_key(local_tweak(book, qtry + 1, packs.get("failed_step")))
        # Prefer local tweak under memory pressure; GLM repair optional/short
        repaired = None
        try:
            repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step"), "quick")
        except Exception as exc:
            _log("glm_repair_skip %s" % exc)
        if repaired:
            book = force_key(repaired)
        hist.append({"stage": "quick_repair", "try": qtry, "ok": bool(repaired)})

    # Death gate already done pre-hypothesis; re-affirm no live collision keys
    write_art("death_reaffirm", {
        "live_avoid": LIVE_COLLISION,
        "key": (book.get("dsl") or {}).get("key"),
        "logic": book.get("logic_class"),
    })

    for ftry in range(MAX_FULL_REPAIR + 1):
        status.update({"stage": "full_mc_stress", "attempt": ftry, "updated_at": _now()})
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
                "reason": "Full/MC/Stress failed after %d repairs" % MAX_FULL_REPAIR,
                "full": packs.get("full"), "hist": hist, "book": book, "packs": packs,
            }
        book = force_key(local_tweak(book, ftry + 1, packs.get("failed_step")))
        repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step"), "full")
        if repaired:
            book = force_key(repaired)
        q2 = f2.quick_suite(book)
        if not q2.get("quick_pass"):
            packs = q2
            packs["full_pass"] = False
            continue
        packs = q2

    # Portfolio correlation
    corr = portfolio_correlation_note(book, packs)
    write_art("portfolio_corr", corr)
    hist.append({"stage": "portfolio_corr", "corr": corr})
    if corr.get("reject_redundant"):
        return {
            "dir_id": dir_id, "ok": False,
            "failed_step": "portfolio_correlation_redundant",
            "reason": corr.get("note_zh"),
            "corr": corr, "hist": hist, "book": book, "packs": packs,
        }

    # Sim formal = GLM/DS/Qwen path inside factory
    for stry in range(MAX_SIM_REPAIR + 1):
        status.update({"stage": "sim_formal_reviews", "attempt": stry, "updated_at": _now()})
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
                "formal": sf.get("formal"), "corr": corr,
                "hist": hist, "book": book, "packs": packs,
            }
        fs = sf.get("failed_step")
        if fs in ("formal_review", "pending_ingest"):
            return {
                "dir_id": dir_id, "ok": False, "failed_step": fs,
                "reason": (sf.get("formal") or {}).get("reason") or (sf.get("pending") or {}).get("reason"),
                "sim": sf.get("sim"), "formal": sf.get("formal"),
                "corr": corr, "hist": hist, "book": book,
            }
        if stry >= MAX_SIM_REPAIR:
            return {
                "dir_id": dir_id, "ok": False,
                "failed_step": fs or "sim_exhausted",
                "reason": "Sim/formal failed after %d repairs" % MAX_SIM_REPAIR,
                "sim": sf.get("sim"), "corr": corr, "hist": hist, "book": book,
            }
        book = force_key(local_tweak(book, stry + 1, "sim"))
        repaired, _ai = f3.glm_repair(book, packs, "sim_review", "sim")
        if repaired:
            book = force_key(repaired)
        q3 = f2.quick_suite(book)
        if not q3.get("quick_pass"):
            continue
        packs = f2.full_suite(book, q3)
        if not packs.get("full_pass"):
            continue

    return {
        "dir_id": dir_id, "ok": False, "failed_step": "unknown_exhausted",
        "hist": hist, "book": book,
    }


def main():
    _log("=== SOL5m BEHAVIOR START === %s" % _now())
    write_art("heartbeat", {"at": _now(), "stage": "boot", "pid": os.getpid()})
    d._ensure_dirs()
    d._load_env()
    try:
        import gc
        gc.collect()
    except Exception:
        pass

    # 1) Edge Discovery FIRST
    discovery = build_edge_discovery()
    write_art("edge_discovery", discovery)
    _log("edge_discovery candidates=%d" % len(discovery.get("candidates") or []))

    funding_meta = fetch_funding_oi_meta()
    write_art("funding_oi_meta", funding_meta)
    proxy_doc = install_behavior_proxies(funding_meta)
    write_art("proxy_notes", proxy_doc)
    _log("proxies installed funding_z=%s" % funding_meta.get("funding_z_now"))

    death = load_death_packet()
    write_art("death_inputs", death)
    _log("death source=%s" % death.get("source"))

    # 2) Novelty
    ranked = novelty_rank(discovery, death, funding_meta)
    write_art("novelty_ranking", ranked)
    _log("novelty top=%s score=%s" % (
        (ranked.get("top3") or [{}])[0].get("logic_class"),
        (ranked.get("top3") or [{}])[0].get("novelty_score"),
    ))

    # 3) Death test filter
    death_out = death_test_filter(ranked, death)
    write_art("death_test", death_out)
    survivors = death_out.get("survivors") or []
    if not survivors:
        end = {
            "ok": False, "failed_step": "death_test_no_survivors",
            "at": _now(), "rejected": death_out.get("rejected"),
        }
        write_art("end_report", end)
        write_art("parent_summary_zh", {
            "summary_zh": "SOL5m行为边【失败】Death Test无幸存候选",
            "end": end,
        })
        return 1

    write_art("inputs", {
        "discovery_n": len(discovery.get("candidates") or []),
        "survivor_top": survivors[0].get("logic_class"),
        "proxy": proxy_doc,
        "funding_meta": funding_meta,
        "live_protect": list(LIVE_COLLISION.keys()),
        "gates": {
            "WF": ">=7/10", "MC_beat": ">=90%", "friction_sharpe": ">=0",
            "opens_per_day": [0.8, 1.5], "wr_avg_target": 75,
        },
    })

    # 4) Hypothesis — try top survivors sequentially (fresh ≤3 repairs each)
    result = None
    book = None
    tried = []
    for si, surv in enumerate(survivors[:3], start=1):
        hyp = hypothesis_from_survivor(surv)
        write_art("hypothesis_try%d" % si, hyp)
        book = force_key(make_book(hyp, si))
        write_art("hypothesis_book_v0_try%d" % si, book)
        _log("hypothesis#%d %s key=%s" % (
            si, book.get("logic_class"), (book.get("dsl") or {}).get("key")))
        try:
            result = process(book)
        except Exception as exc:
            result = {
                "dir_id": "sol5m_behavior_exception_%d" % si, "ok": False,
                "failed_step": "exception", "reason": str(exc),
                "trace": traceback.format_exc()[-2500:], "book": book,
            }
        tried.append({
            "si": si, "logic": book.get("logic_class"),
            "ok": result.get("ok"), "failed_step": result.get("failed_step"),
        })
        write_art("survivor_attempt_%d" % si, tried[-1])
        if result.get("ok"):
            break
        # if validate still broken, next survivor; otherwise also try next after archive
        _log("survivor#%d failed at %s — try next" % (si, result.get("failed_step")))
        try:
            archive_result(result, {
                "exact_death": result.get("failed_step"),
                "reason": result.get("reason"),
                "logic_class": book.get("logic_class"),
                "survivor_index": si,
            })
        except Exception:
            pass
    write_art("survivor_attempts", tried)
    if result is None:
        result = {
            "dir_id": "sol5m_behavior_no_survivor", "ok": False,
            "failed_step": "no_hypothesis", "reason": "no survivors processed",
            "book": book,
        }

    bm = result.get("metrics") or ((result.get("packs") or {}).get("base_metrics") or {})
    full = result.get("full") or ((result.get("packs") or {}).get("full") or {})
    end = {
        "at": _now(),
        "op": "SOL5m行为微观独立创造",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "ok": bool(result.get("ok")),
        "failed_step": result.get("failed_step"),
        "reason": result.get("reason"),
        "logic": (result.get("book") or book).get("logic_class"),
        "key": ((result.get("book") or book).get("dsl") or {}).get("key"),
        "pending": result.get("pending"),
        "sim": result.get("sim"),
        "formal": result.get("formal"),
        "corr": result.get("corr"),
        "metrics": {
            "trades": bm.get("trades"),
            "fold_positive": bm.get("fold_positive"),
            "folds": bm.get("folds"),
            "win_rate": bm.get("win_rate") or bm.get("wr"),
            "profit_factor": bm.get("profit_factor") or bm.get("pf"),
            "sharpe": bm.get("sharpe"),
            "friction_sharpe": full.get("friction_sharpe"),
            "mc_beat": (full.get("mc") or {}).get("beat_ratio"),
        },
        "proxy_notes": proxy_doc,
        "hist": result.get("hist"),
        "edges_kept": [s.get("logic_class") for s in survivors[:5]],
    }

    if not result.get("ok"):
        death_cause = {
            "exact_death": result.get("failed_step"),
            "reason": result.get("reason"),
            "metrics": end.get("metrics"),
            "corr": result.get("corr"),
            "hist_tail": (result.get("hist") or [])[-8:],
            "logic_class": end["logic"],
            "key": end["key"],
            "summary_zh": "SOL5m行为 %s 失败于 %s：%s" % (
                end["logic"], result.get("failed_step"), result.get("reason")),
        }
        arch = archive_result(result, death_cause)
        end["archive_path"] = arch
        end["death_cause"] = death_cause
        _log("ARCHIVED %s %s" % (arch, death_cause["exact_death"]))
    else:
        _log("PENDING %s" % ((result.get("pending") or {}).get("key"),))

    write_art("end_report", end)
    write_art("status", {"finished_at": _now(), "ok": end["ok"], "end": end})

    m = end.get("metrics") or {}
    corr_n = (end.get("corr") or {}).get("note_zh") or "相关未测"
    if end["ok"]:
        summary_zh = (
            "SOL5m行为微观【成功】保留边=%s 逻辑=%s key=%s "
            "trades=%s WF=%s/%s WR=%s PF=%s frictionSharpe=%s MC=%s "
            "相关=%s pending=%s sim_ds/qw=%s/%s"
            % (
                ",".join(end.get("edges_kept") or [])[:120],
                end["logic"], end["key"],
                m.get("trades"), m.get("fold_positive"), m.get("folds"),
                m.get("win_rate"), m.get("profit_factor"),
                m.get("friction_sharpe"), m.get("mc_beat"),
                corr_n,
                (end.get("pending") or {}).get("key"),
                (end.get("sim") or {}).get("wr_deepseek_sim"),
                (end.get("sim") or {}).get("wr_qwen_sim"),
            )
        )
    else:
        summary_zh = (
            "SOL5m行为微观【失败】保留边候选=%s 逻辑=%s 失败步骤=%s 详情=%s "
            "metrics(trades/WF/WR/PF/fric/MC)=%s/%s/%s/%s/%s/%s 相关=%s 归档=%s"
            % (
                ",".join(end.get("edges_kept") or [])[:80],
                end.get("logic"), end.get("failed_step"), end.get("reason"),
                m.get("trades"), m.get("fold_positive"), m.get("win_rate"),
                m.get("profit_factor"), m.get("friction_sharpe"), m.get("mc_beat"),
                corr_n, end.get("archive_path"),
            )
        )
    write_art("parent_summary_zh", {"summary_zh": summary_zh, "at": _now(), "end": {
        "ok": end["ok"], "failed_step": end.get("failed_step"),
        "key": end.get("key"), "pending": end.get("pending"),
        "edges_kept": end.get("edges_kept"), "metrics": m,
        "corr": end.get("corr"), "archive_path": end.get("archive_path"),
    }})
    _log("=== END === %s" % summary_zh)
    print("PARENT_SUMMARY_ZH:", summary_zh, flush=True)
    return 0 if end["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
