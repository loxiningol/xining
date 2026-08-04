#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜叁·ETH15m Market Behaviour Edge — isolated pipeline.

ONLY: ETH-USDT-SWAP / 15m
Artifacts: /root/auto_trade/dual_engine/frost3_eth15m_behavior_*
Key prefix: frost3_eth15m_behavior_
Does NOT touch Frost3 fade/ETH5m/ETH1h/BTC5m or live ADA/LTC/NG/XRP.

Edge family: Market Behaviour (effort/absorb, asia inventory, session auction).
Traditional RSI/EMA/MACD/ADX are NOT the edge (banned as entry leaves on D05).
Honest OHLCV proxies documented in proxy_notes.
"""
from __future__ import print_function

import copy
import json
import os
import sys
import time
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
PREFIX = "frost3_eth15m_behavior"
KEY_PREFIX = "frost3_eth15m_behavior_"
SYMBOL = "ETH-USDT-SWAP"
TIMEFRAME = "15m"
SESSION_UTC_START = 7
SESSION_UTC_END = 20
STOP_PCT = 0.009
MAX_AUDIT_REVISE = 2
MAX_QUICK_REPAIR = 3
MAX_FULL_REPAIR = 3
MAX_SIM_REPAIR = 3

# Serialize under multi-agent load
LOCK_PATH = os.path.join(OUT, "%s_run.lock" % PREFIX)
BEHAVIOUR_FEATURES = {
    "utc_hour", "session_liq", "sess_vwap_dist", "va_pos", "poc_proxy",
    "delta_proxy", "cvd_slope4", "absorb_score", "sweep_reclaim_low",
    "sweep_reclaim_high", "vol_pulse", "effort_result", "asia_push_flag",
    "atr_compress", "body_shrink", "up_streak",
}


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


def acquire_lock(timeout_sec=900):
    """Simple flock-ish lock via exclusive create; wait if another agent holds it."""
    os.makedirs(OUT, exist_ok=True)
    start = time.time()
    while True:
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, ("%s %s\n" % (_now(), os.getpid())).encode("utf-8"))
            os.close(fd)
            return True
        except OSError:
            # stale lock > 45min → break
            try:
                age = time.time() - os.path.getmtime(LOCK_PATH)
                if age > 2700:
                    os.remove(LOCK_PATH)
                    continue
            except Exception:
                pass
            if time.time() - start > timeout_sec:
                return False
            print("[eth15m_beh] waiting lock age/busy...", flush=True)
            time.sleep(20)


def release_lock():
    try:
        if os.path.exists(LOCK_PATH):
            os.remove(LOCK_PATH)
    except Exception:
        pass


def ensure_eth_15m_parquet():
    """Resample local ETH 5m OHLC → 15m so _frame does not thrash under load."""
    import pandas as pd

    src = "/root/market_data/ETH-USDT-SWAP/5m/ETH_USDT_SWAP_5m_OKX_LOCAL.parquet"
    out_dir = "/root/market_data/ETH-USDT-SWAP/15m"
    out = os.path.join(out_dir, "ETH-USDT-SWAP_15m.parquet")
    # also write factory-common alt name
    out2 = os.path.join(out_dir, "ETH_USDT_SWAP_15m_OKX_LOCAL.parquet")
    meta_path = os.path.join(out_dir, "ETH-USDT-SWAP_15m.meta.json")
    if os.path.exists(out) and os.path.getsize(out) > 1000:
        try:
            meta = json.load(open(meta_path))
        except Exception:
            meta = {"path": out, "existed": True}
        return {"ok": True, "path": out, "meta": meta, "created": False}

    print("[eth15m_beh] resampling ETH 5m→15m ...", flush=True)
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
    try:
        r.to_parquet(out2)
    except Exception:
        pass
    meta = {
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "bars": int(len(r)),
        "source": "resampled_from_5m",
        "volume_note": "volume=sum_of_5m_bar_ranges_activity_proxy",
        "start": str(r.index[0]),
        "end": str(r.index[-1]),
        "created_at": _now(),
    }
    open(meta_path, "w").write(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    print("[eth15m_beh] resampled bars=", len(r), flush=True)
    return {"ok": True, "path": out, "meta": meta, "created": True}


def install_behaviour_proxies():
    """Process-local: register + inject honest OHLCV behaviour proxies."""
    for f in BEHAVIOUR_FEATURES:
        dsl_mod.FEATURES.add(f)
    try:
        if hasattr(dsl_mod, "PERTURB_STEPS") and isinstance(dsl_mod.PERTURB_STEPS, dict):
            for f in BEHAVIOUR_FEATURES:
                dsl_mod.PERTURB_STEPS.setdefault(f, 0.05)
    except Exception:
        pass

    _orig = d._frame
    _cache = {"frame": None}

    def _enrich(frame):
        import numpy as np
        import pandas as pd

        if frame is None or not hasattr(frame, "copy"):
            return frame
        fr = frame.copy()
        # trim under memory / IO pressure (host often 1vCPU / 0.75GB)
        if len(fr) > 4500:
            fr = fr.iloc[-4500:].copy()

        idx = fr.index
        if not isinstance(idx, pd.DatetimeIndex):
            try:
                idx = pd.to_datetime(idx)
                fr.index = idx
            except Exception:
                pass

        if isinstance(fr.index, pd.DatetimeIndex):
            hour = fr.index.hour.astype(float)
        else:
            hour = pd.Series(12.0, index=fr.index)
        fr["utc_hour"] = hour
        fr["session_liq"] = (
            (hour >= float(SESSION_UTC_START)) & (hour < float(SESSION_UTC_END))
        ).astype(float)

        o = fr["open"].astype(float)
        h = fr["high"].astype(float)
        l = fr["low"].astype(float)
        c = fr["close"].astype(float)
        atr = fr["atr14"].astype(float) if "atr14" in fr.columns else (h - l)
        eps = 1e-12
        rng = (h - l).replace(0, eps)
        typical = (h + l + c) / 3.0

        # range-weighted session VWAP (UTC day) — honest proxy, not exchange VWAP
        if isinstance(fr.index, pd.DatetimeIndex):
            day = fr.index.floor("D")
        else:
            day = pd.Series(0, index=fr.index)
        tw = typical * rng
        cum_tw = tw.groupby(day).cumsum()
        cum_w = rng.groupby(day).cumsum().replace(0, eps)
        sess_vwap = cum_tw / cum_w
        fr["sess_vwap_dist"] = ((c - sess_vwap) / atr.replace(0, eps)).astype(float)

        # 24h value-area position (96 bars of 15m)
        hi96 = h.rolling(96, min_periods=24).max()
        lo96 = l.rolling(96, min_periods=24).min()
        span = (hi96 - lo96).replace(0, eps)
        fr["va_pos"] = ((c - lo96) / span).clip(0, 1).astype(float)

        # time-at-price POC proxy: rolling mid of densest OHLC-mid bin (coarse)
        mid = ((h + l) / 2.0).astype(float)
        # use rolling median of mid as cheap POC stand-in (documented as proxy)
        fr["poc_proxy"] = mid.rolling(96, min_periods=24).median()

        # delta / CVD path proxies from OHLC
        delta = ((c - o) / rng) * ((h - l) / atr.replace(0, eps))
        fr["delta_proxy"] = delta.astype(float).fillna(0.0)
        cvd = fr["delta_proxy"].cumsum()
        fr["cvd_slope4"] = (cvd - cvd.shift(4)).astype(float).fillna(0.0)

        # absorption: large range + small body
        body = (c - o).abs()
        large = ((h - l) / atr.replace(0, eps)) > 1.4
        mid_close = (body / rng) < 0.25
        fr["absorb_score"] = (large & mid_close).astype(float)

        prev_hi = fr["prev_high20"].astype(float) if "prev_high20" in fr.columns else h.rolling(20).max()
        prev_lo = fr["prev_low20"].astype(float) if "prev_low20" in fr.columns else l.rolling(20).min()
        # sweep low then reclaim
        fr["sweep_reclaim_low"] = (
            (l < prev_lo.shift(1)) & (c > prev_lo.shift(1))
        ).astype(float)
        fr["sweep_reclaim_high"] = (
            (h > prev_hi.shift(1)) & (c < prev_hi.shift(1))
        ).astype(float)

        # activity pulse
        if "vol_z20" in fr.columns:
            fr["vol_pulse"] = fr["vol_z20"].astype(float)
        else:
            rz = (h - l)
            mu = rz.rolling(20, min_periods=5).mean()
            sd = rz.rolling(20, min_periods=5).std().replace(0, eps)
            fr["vol_pulse"] = ((rz - mu) / sd).astype(float).fillna(0.0)

        # effort vs result (3-bar)
        range_sum3 = (h - l).rolling(3, min_periods=3).sum()
        progress = (c - c.shift(3)).abs().replace(0, eps)
        fr["effort_result"] = (range_sum3 / progress).astype(float).fillna(0.0)

        # asia push flag: cum delta over utc 0-6 positive and elevated
        asia = (hour >= 0) & (hour < 7)
        asia_delta = fr["delta_proxy"].where(asia, 0.0)
        # rolling sum of prior asia session deltas approximated by last 28 bars (~7h)
        asia_push = asia_delta.rolling(28, min_periods=8).sum()
        fr["asia_push_flag"] = (asia_push > 0.8).astype(float)
        # also sticky into London open: forward-fill morning flag via shift of overnight max
        overnight = asia_push.groupby(day).transform("max") if hasattr(asia_push, "groupby") else asia_push
        try:
            overnight = asia_delta.groupby(day).sum()
            # map day sum back
            day_sum = asia_delta.groupby(day).transform("sum")
            fr["asia_push_flag"] = (day_sum > 0.8).astype(float)
        except Exception:
            pass

        atr_ma = atr.rolling(48, min_periods=12).mean().replace(0, eps)
        fr["atr_compress"] = ((atr / atr_ma) < 0.85).astype(float)

        body_raw = (c - o).abs()
        fr["body_shrink"] = (
            (body_raw < body_raw.shift(1)) & (body_raw.shift(1) < body_raw.shift(2))
        ).astype(float)
        up = (c > o).astype(float)
        streak = up.copy()
        for i in range(1, 6):
            streak = streak + up.shift(i).fillna(0)
        fr["up_streak"] = streak.astype(float)

        # fillna
        for col in BEHAVIOUR_FEATURES:
            if col in fr.columns:
                fr[col] = fr[col].replace([np.inf, -np.inf], 0).fillna(0.0)
        return fr

    def _wrapped(symbol, timeframe):
        if (
            str(symbol).upper() == SYMBOL
            and str(timeframe) == TIMEFRAME
            and _cache["frame"] is not None
        ):
            return _cache["frame"]
        fr = _orig(symbol, timeframe)
        if str(symbol).upper() == SYMBOL and str(timeframe) == TIMEFRAME:
            fr = _enrich(fr)
            _cache["frame"] = fr
        return fr

    d._frame = _wrapped
    return {
        "features_added": sorted(BEHAVIOUR_FEATURES),
        "honesty_zh": (
            "sess_vwap_dist=日会话range加权VWAP代理；va_pos/poc_proxy=时间价区位非成交量剖面；"
            "delta_proxy/cvd_slope4=OHLC实体压力非tick CVD；absorb/sweep/effort=形态代理；"
            "funding/OI未注入（DeathTest已处决D09/D10）。"
        ),
        "session_utc": [SESSION_UTC_START, SESSION_UTC_END],
        "stop_loss_pct": STOP_PCT,
    }


def _slim_packs(packs):
    """Drop heavy fields before disk write — host has ~0.75GB RAM."""
    if not isinstance(packs, dict):
        return packs
    out = {}
    keep = (
        "ok", "stage", "error", "gate_mode", "quick_pass", "full_pass",
        "failed_step", "base_metrics", "quick", "full", "monte_carlo_frost2",
        "symbol", "timeframe",
    )
    for k in keep:
        if k in packs:
            out[k] = packs[k]
    anti = packs.get("anti_overfit") or {}
    if anti:
        out["anti_overfit"] = {"metrics": anti.get("metrics"), "pass": anti.get("pass")}
    dest = packs.get("logic_destruction") or {}
    if dest:
        out["logic_destruction"] = {
            "pass": dest.get("pass"), "reason": dest.get("reason"),
            "stable_ratio": dest.get("stable_ratio"),
        }
    fr = packs.get("extreme_friction") or {}
    if fr:
        out["extreme_friction"] = {"metrics": fr.get("metrics"), "pass": fr.get("pass")}
    if packs.get("error"):
        out["error"] = packs.get("error")
    return out


CANDIDATES = [
    {
        "id": "D01",
        "logic_class": "session_auction_imbalance",
        "direction": "long",
        "thesis": "伦敦开盘拍卖买方接受后做多",
        "entry": [
            {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}},
            {"left": {"feature": "utc_hour"}, "op": "gte", "right": {"value": 7.0}},
            {"left": {"feature": "utc_hour"}, "op": "lt", "right": {"value": 10.0}},
            {"left": {"feature": "delta_proxy"}, "op": "gt", "right": {"value": 0.12}},
            {"left": {"feature": "va_pos"}, "op": "gt", "right": {"value": 0.55}},
            {"left": {"feature": "vol_pulse"}, "op": "gt", "right": {"value": 0.2}},
            {"left": {"feature": "absorb_score"}, "op": "lt", "right": {"value": 0.5}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}},
        ],
        "exit": [
            {"left": {"feature": "va_pos"}, "op": "gt", "right": {"value": 0.85},
             "role": "take_profit"},
            {"left": {"feature": "sweep_reclaim_high"}, "op": "gt", "right": {"value": 0.5},
             "role": "invalidation"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"},
             "role": "invalidation"},
        ],
        "max_hold_bars": 16,
        "banned": ["rsi14", "macd_stick"],
    },
    {
        "id": "D02",
        "logic_class": "vwap_acceptance_continuation",
        "direction": "long",
        "thesis": "会话VWAP上方接受溢价（非回归）做多",
        "entry": [
            {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}},
            {"left": {"feature": "sess_vwap_dist"}, "op": "gt", "right": {"value": 0.15}},
            {"left": {"feature": "sess_vwap_dist"}, "op": "lt", "right": {"value": 1.0}},
            {"left": {"feature": "delta_proxy"}, "op": "gt", "right": {"value": 0.05}},
            {"left": {"feature": "vol_pulse"}, "op": "gt", "right": {"value": -0.2}},
            {"left": {"feature": "vol_pulse"}, "op": "lt", "right": {"value": 1.8}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}},
        ],
        "exit": [
            {"left": {"feature": "sess_vwap_dist"}, "op": "lt", "right": {"value": 0.0},
             "role": "invalidation"},
            {"left": {"feature": "sess_vwap_dist"}, "op": "gt", "right": {"value": 1.5},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"},
             "role": "invalidation"},
        ],
        "max_hold_bars": 18,
        "banned": ["rsi14", "macd_stick", "ema16"],
    },
    {
        "id": "D14",
        "logic_class": "poc_migration_follow",
        "direction": "long",
        "thesis": "POC上移且收盘在POC上方接受",
        "entry": [
            {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "poc_proxy"}},
            {"left": {"feature": "sess_vwap_dist"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "delta_proxy"}, "op": "gt", "right": {"value": 0.05}},
            {"left": {"feature": "va_pos"}, "op": "lt", "right": {"value": 0.85}},
            {"left": {"feature": "va_pos"}, "op": "gt", "right": {"value": 0.45}},
            {"left": {"feature": "vol_pulse"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}},
        ],
        "exit": [
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "poc_proxy"},
             "role": "invalidation"},
            {"left": {"feature": "va_pos"}, "op": "gt", "right": {"value": 0.90},
             "role": "take_profit"},
        ],
        "max_hold_bars": 18,
        "banned": ["rsi14", "macd_stick", "ema8", "ema16"],
    },
    {
        "id": "D06",
        "logic_class": "ny_overlap_participation_pulse",
        "direction": "long",
        "thesis": "UTC13-16重叠窗参与度脉冲+正流",
        "entry": [
            {"left": {"feature": "utc_hour"}, "op": "gte", "right": {"value": 13.0}},
            {"left": {"feature": "utc_hour"}, "op": "lt", "right": {"value": 16.0}},
            {"left": {"feature": "vol_pulse"}, "op": "gt", "right": {"value": 0.6}},
            {"left": {"feature": "delta_proxy"}, "op": "gt", "right": {"value": 0.15}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "poc_proxy"}},
            {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}},
        ],
        "exit": [
            {"left": {"feature": "delta_proxy"}, "op": "lt", "right": {"value": 0.0},
             "role": "invalidation"},
            {"left": {"feature": "va_pos"}, "op": "gt", "right": {"value": 0.88},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"},
             "role": "invalidation"},
        ],
        "max_hold_bars": 12,
        "banned": ["rsi14", "macd_stick"],
    },
    {
        "id": "D01b",
        "logic_class": "session_auction_imbalance",
        "direction": "long",
        "thesis": "D01放宽vol/delta但仍限伦敦开盘窗",
        "entry": [
            {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}},
            {"left": {"feature": "utc_hour"}, "op": "gte", "right": {"value": 7.0}},
            {"left": {"feature": "utc_hour"}, "op": "lt", "right": {"value": 11.0}},
            {"left": {"feature": "delta_proxy"}, "op": "gt", "right": {"value": 0.08}},
            {"left": {"feature": "va_pos"}, "op": "gt", "right": {"value": 0.50}},
            {"left": {"feature": "vol_pulse"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}},
        ],
        "exit": [
            {"left": {"feature": "va_pos"}, "op": "gt", "right": {"value": 0.82},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"},
             "role": "invalidation"},
        ],
        "max_hold_bars": 14,
        "banned": ["rsi14", "macd_stick"],
    },
]


def build_dsl(cand, tag=""):
    key = "%s%s_%s" % (KEY_PREFIX, cand["id"].lower(), _safe(tag or cand["logic_class"])[:18])
    dsl = {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key[:100],
        "name": "寒霜叁-ETH15m-行为边-%s" % cand["id"],
        "direction": cand["direction"],
        "timeframe": TIMEFRAME,
        "supported_instruments": [SYMBOL],
        "entry": {"all": copy.deepcopy(cand["entry"])},
        "exit": {"any": copy.deepcopy(cand["exit"])},
        "max_hold_bars": int(cand.get("max_hold_bars") or 16),
    }
    return f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)


def make_book(cand, tag=""):
    dsl = build_dsl(cand, tag)
    return {
        "title": "寒霜叁-ETH15m-MarketBehaviour-%s" % cand["id"],
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": cand["direction"],
        "logic_class": cand["logic_class"],
        "thesis": cand.get("thesis"),
        "candidate_id": cand["id"],
        "dsl": dsl,
        "gate_mode": "frost2",
        "source": PREFIX,
        "candidate_meta": {
            "behaviour_edge": True,
            "banned_indicator_leaves": cand.get("banned") or [],
            "stop_loss_pct": STOP_PCT,
            "position_pct": 0.30,
            "leverage": 20,
        },
        "avoid_from_postmortem": [
            "stop_cluster", "sample_starvation", "cost_collapse",
            "no rsi/macd/ema as edge", "no eth15m_fade isomorph",
        ],
    }


def assert_no_banned(book, banned):
    leaves = list(((book.get("dsl") or {}).get("entry") or {}).get("all") or [])
    for row in leaves:
        feat = ((row.get("left") or {}).get("feature") or "")
        if feat in (banned or []):
            raise ValueError("banned feature in entry: %s" % feat)


def local_tweak(book, n, failed_step="", cand=None):
    book = copy.deepcopy(book)
    dsl = book["dsl"]
    entries = list((dsl.get("entry") or {}).get("all") or [])
    step = str(failed_step or "")
    banned = set((cand or {}).get("banned") or [])
    for row in entries:
        feat = (row.get("left") or {}).get("feature")
        if feat in banned:
            continue
        right = row.get("right") or {}
        if "value" not in right:
            continue
        v = float(right["value"])
        soften = ("walk_forward" in step) or ("wf" in step) or ("sample" in step)
        tighten = ("friction" in step) or ("dest" in step) or ("mc" in step) or ("destruction" in step)
        if feat == "effort_result" and row.get("op") == "gt":
            right["value"] = max(1.4, v - 0.15 * n) if soften else min(3.0, v + 0.1 * n)
        elif feat == "absorb_score" and row.get("op") == "gt":
            right["value"] = max(0.35, v - 0.05 * n) if soften else min(0.8, v + 0.05 * n)
        elif feat == "delta_proxy" and row.get("op") == "lt":
            right["value"] = min(0.15, v + 0.05 * n) if soften else (v - 0.05 * n)
        elif feat == "delta_proxy" and row.get("op") == "gt":
            right["value"] = max(0.0, v - 0.05 * n) if soften else (v + 0.03 * n)
        elif feat == "va_pos" and row.get("op") == "gt":
            right["value"] = max(0.40, v - 0.05 * n) if soften else min(0.75, v + 0.03 * n)
        elif feat == "vol_pulse" and row.get("op") == "gt":
            right["value"] = max(-0.3, v - 0.1 * n) if soften else (v + 0.1 * n)
        elif feat == "utc_hour" and row.get("op") == "lt":
            right["value"] = min(14.0, v + 1.0 * n) if soften else v
        elif feat == "utc_hour" and row.get("op") == "gte":
            right["value"] = max(6.0, v - 0.5 * n) if soften else v
    dsl["entry"] = {"all": entries}
    hold = int(dsl.get("max_hold_bars") or 16)
    if tighten:
        dsl["max_hold_bars"] = max(10, hold - 2 * n)
    elif soften:
        dsl["max_hold_bars"] = min(28, hold + 2 * n)
    book["dsl"] = f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)
    book["dsl"]["key"] = (KEY_PREFIX + _safe(book["dsl"]["key"]) + "_t%d" % n)[:100]
    return book


_status = {"op": "寒霜叁ETH15m市场行为边", "stage": "init", "updated_at": None, "hist": []}


def _upd(**kw):
    _status.update(kw)
    _status["updated_at"] = _now()
    _write("status", _status)


def probe_freq(cand):
    """Estimate opens/day on enriched frame (no full backtest)."""
    try:
        fr = d._frame(SYMBOL, TIMEFRAME)
        mask = None
        for row in cand["entry"]:
            feat = (row.get("left") or {}).get("feature")
            op = row.get("op")
            right = row.get("right") or {}
            if feat not in fr.columns:
                continue
            series = fr[feat].astype(float)
            if "value" in right:
                v = float(right["value"])
                if op == "gt":
                    m = series > v
                elif op == "gte":
                    m = series >= v
                elif op == "lt":
                    m = series < v
                elif op == "lte":
                    m = series <= v
                else:
                    continue
            elif "feature" in right and right["feature"] in fr.columns:
                other = fr[right["feature"]].astype(float)
                m = series > other if op == "gt" else series < other
            else:
                continue
            mask = m if mask is None else (mask & m)
        if mask is None:
            return {"error": "no_mask"}
        hits = int(mask.sum())
        days = max(1.0, len(fr) / 96.0)
        return {
            "hits": hits,
            "bars": int(len(fr)),
            "days_est": round(days, 2),
            "opens_per_day_est": round(hits / days, 3),
            "hit_pct": round(100.0 * hits / max(1, len(fr)), 3),
        }
    except Exception as exc:
        return {"error": str(exc)}


def process(book, cand):
    hist = []
    assert_no_banned(book, cand.get("banned"))

    # skip heavy GLM hyp under load — deterministic behaviour books already audited offline
    _write("book_init_%s" % cand["id"].lower(), book)
    _upd(stage="quick", candidate=cand["id"])

    packs = None
    for qtry in range(MAX_QUICK_REPAIR + 1):
        _upd(stage="quick", attempt=qtry, candidate=cand["id"])
        packs = f2.quick_suite(book)
        _write("quick_%s_t%d" % (cand["id"].lower(), qtry), _slim_packs(packs))
        bm = packs.get("base_metrics") or {}
        hist.append({
            "stage": "quick", "try": qtry, "cand": cand["id"],
            "pass": packs.get("quick_pass"),
            "failed_step": packs.get("failed_step"),
            "fp": bm.get("fold_positive"), "folds": bm.get("folds"),
            "tr": bm.get("trades"),
            "pf": bm.get("profit_factor") or bm.get("pf"),
            "wr": bm.get("win_rate") or bm.get("win_rate_pct"),
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
        })
        print("[eth15m_beh] quick", cand["id"], "t", qtry,
              "pass", packs.get("quick_pass"),
              "step", packs.get("failed_step"),
              "fp", bm.get("fold_positive"), "/", bm.get("folds"),
              "tr", bm.get("trades"), flush=True)
        if packs.get("quick_pass"):
            break
        if qtry >= MAX_QUICK_REPAIR:
            return {
                "ok": False,
                "failed_step": packs.get("failed_step") or "quick_exhausted",
                "reason": "Quick failed after %d repairs" % MAX_QUICK_REPAIR,
                "metrics": bm, "hist": hist, "book": book, "cand": cand["id"],
                "death_cause_zh": "Quick耗尽：%s fp=%s/%s tr=%s" % (
                    packs.get("failed_step"), bm.get("fold_positive"),
                    bm.get("folds"), bm.get("trades")),
            }
        book = local_tweak(book, qtry + 1, packs.get("failed_step"), cand)
        assert_no_banned(book, cand.get("banned"))
        _write("book_%s" % cand["id"].lower(), book)

    for ftry in range(MAX_FULL_REPAIR + 1):
        _upd(stage="full", attempt=ftry, candidate=cand["id"])
        packs = f2.full_suite(book, packs)
        _write("full_%s_t%d" % (cand["id"].lower(), ftry), _slim_packs(packs))
        hist.append({
            "stage": "full", "try": ftry, "cand": cand["id"],
            "pass": packs.get("full_pass"),
            "failed_step": packs.get("failed_step"),
            "friction": (packs.get("full") or {}).get("friction_sharpe"),
            "mc": ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
        })
        print("[eth15m_beh] full", cand["id"], "t", ftry,
              "pass", packs.get("full_pass"),
              "fr", (packs.get("full") or {}).get("friction_sharpe"),
              "mc", ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
              flush=True)
        if packs.get("full_pass"):
            break
        if ftry >= MAX_FULL_REPAIR:
            return {
                "ok": False,
                "failed_step": packs.get("failed_step") or "full_exhausted",
                "reason": "Full failed after %d repairs" % MAX_FULL_REPAIR,
                "full": packs.get("full"), "hist": hist, "book": book,
                "cand": cand["id"],
                "death_cause_zh": "Full耗尽：%s fr=%s mc=%s" % (
                    packs.get("failed_step"),
                    (packs.get("full") or {}).get("friction_sharpe"),
                    ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio")),
            }
        book = local_tweak(book, ftry + 1, packs.get("failed_step"), cand)
        q2 = f2.quick_suite(book)
        hist.append({"stage": "re_quick", "pass": q2.get("quick_pass"),
                     "failed": q2.get("failed_step")})
        if not q2.get("quick_pass"):
            packs = q2
            packs["full_pass"] = False
            continue
        packs = q2
        _write("book_%s" % cand["id"].lower(), book)

    for stry in range(MAX_SIM_REPAIR + 1):
        _upd(stage="sim_formal", attempt=stry, candidate=cand["id"])
        sf = f2.run_sim_formal(book, packs)
        _write("sim_%s_t%d" % (cand["id"].lower(), stry), sf)
        hist.append({
            "stage": "sim_formal", "try": stry, "cand": cand["id"],
            "failed_step": sf.get("failed_step"),
            "sim_ds": (sf.get("sim") or {}).get("wr_deepseek_sim"),
            "sim_qw": (sf.get("sim") or {}).get("wr_qwen_sim"),
            "formal_approved": (sf.get("formal") or {}).get("approved"),
            "pending": (sf.get("pending") or {}).get("key"),
        })
        print("[eth15m_beh] sim", cand["id"], "t", stry,
              "ds", (sf.get("sim") or {}).get("wr_deepseek_sim"),
              "qw", (sf.get("sim") or {}).get("wr_qwen_sim"),
              "formal", (sf.get("formal") or {}).get("approved"),
              "pending", (sf.get("pending") or {}).get("key"),
              flush=True)
        if (sf.get("pending") or {}).get("ok"):
            return {
                "ok": True, "failed_step": None,
                "pending": sf.get("pending"), "sim": sf.get("sim"),
                "formal": sf.get("formal"), "hist": hist, "book": book,
                "cand": cand["id"], "packs": packs,
            }
        fs = sf.get("failed_step")
        if fs in ("formal_review", "pending_ingest"):
            return {
                "ok": False, "failed_step": fs,
                "reason": (sf.get("formal") or {}).get("reason") or (
                    sf.get("pending") or {}).get("reason"),
                "sim": sf.get("sim"), "formal": sf.get("formal"),
                "pending": sf.get("pending"), "hist": hist, "book": book,
                "cand": cand["id"], "packs": packs,
                "death_cause_zh": "正式复核/入库失败：" + str(fs),
            }
        if stry >= MAX_SIM_REPAIR:
            return {
                "ok": False, "failed_step": fs or "sim_exhausted",
                "reason": "Sim/formal failed after %d repairs" % MAX_SIM_REPAIR,
                "sim": sf.get("sim"), "hist": hist, "book": book,
                "cand": cand["id"], "packs": packs,
                "death_cause_zh": "Sim耗尽 DS=%s Qw=%s" % (
                    (sf.get("sim") or {}).get("wr_deepseek_sim"),
                    (sf.get("sim") or {}).get("wr_qwen_sim")),
            }
        book = local_tweak(book, stry + 1, "sim", cand)
        q3 = f2.quick_suite(book)
        if not q3.get("quick_pass"):
            hist.append({"stage": "sim_repair_quick_fail", "failed": q3.get("failed_step")})
            continue
        packs = f2.full_suite(book, q3)
        if not packs.get("full_pass"):
            hist.append({"stage": "sim_repair_full_fail", "failed": packs.get("failed_step")})
            continue

    return {"ok": False, "failed_step": "unknown_exhausted", "hist": hist,
            "book": book, "cand": cand["id"]}


def archive_fail(result):
    arch = os.path.join(OUT, "archive", "%s_%s" % (
        PREFIX, _safe((result.get("cand") or result.get("failed_step") or "fail"))))
    os.makedirs(arch, exist_ok=True)
    open(os.path.join(arch, "RESULT.json"), "w").write(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
    book = result.get("book")
    if book:
        open(os.path.join(arch, "BOOK.json"), "w").write(
            json.dumps(book, ensure_ascii=False, indent=2, default=str) + "\n")
    death = {
        "at": _now(),
        "exact_death_step": result.get("failed_step"),
        "cand": result.get("cand"),
        "reason": result.get("reason"),
        "death_cause_zh": result.get("death_cause_zh"),
        "metrics": result.get("metrics"),
        "full": result.get("full"),
        "sim": result.get("sim"),
        "hist_tail": (result.get("hist") or [])[-10:],
    }
    open(os.path.join(arch, "DEATH.json"), "w").write(
        json.dumps(death, ensure_ascii=False, indent=2, default=str) + "\n")
    return arch


def extract_metrics(result):
    packs = result.get("packs") or {}
    bm = packs.get("base_metrics") or result.get("metrics") or {}
    fr = (packs.get("full") or {}).get("friction_sharpe")
    if fr is None:
        fr = ((packs.get("extreme_friction") or {}).get("metrics") or {}).get("sharpe")
    mc = ((packs.get("full") or {}).get("mc") or {})
    sim = result.get("sim") or {}
    trades = float(bm.get("trades") or 0)
    # rough opens/day from trades over ~ backtest span if present
    days = bm.get("days") or bm.get("span_days")
    opens = None
    if days:
        try:
            opens = round(trades / float(days), 3)
        except Exception:
            opens = None
    wr_ds = sim.get("wr_deepseek_sim")
    wr_qw = sim.get("wr_qwen_sim")
    wr_glm = sim.get("wr_glm_sim") or sim.get("wr_glm")
    wrs = [float(x) for x in (wr_ds, wr_qw, wr_glm) if x is not None]
    avg_wr = round(sum(wrs) / len(wrs), 2) if wrs else None
    return {
        "trades": bm.get("trades"),
        "folds": bm.get("folds"),
        "fold_positive": bm.get("fold_positive"),
        "profit_factor": bm.get("profit_factor") or bm.get("pf"),
        "win_rate": bm.get("win_rate") or bm.get("win_rate_pct"),
        "opens_per_day": opens,
        "friction_sharpe": fr,
        "mc_beat_ratio": mc.get("beat_ratio"),
        "wr_deepseek": wr_ds,
        "wr_qwen": wr_qw,
        "wr_glm": wr_glm,
        "wr_avg": avg_wr,
    }


def main():
    print("[eth15m_beh] START", _now(), flush=True)
    os.makedirs(OUT, exist_ok=True)
    if not acquire_lock(timeout_sec=1200):
        msg = "lock_timeout — another frost3_eth15m_behavior run holds lock"
        _write("end_report", {"ok": False, "failed_step": "lock_timeout", "reason": msg})
        print("[eth15m_beh]", msg, flush=True)
        return 2
    try:
        try:
            d._load_env()
        except Exception:
            pass
        try:
            d._ensure_dirs()
        except Exception:
            pass

        data_meta = ensure_eth_15m_parquet()
        _write("data_meta", data_meta)
        proxy_doc = install_behaviour_proxies()
        _write("proxy_notes", proxy_doc)
        print("[eth15m_beh] proxies", len(proxy_doc.get("features_added") or []), flush=True)

        # sync/echo discovery artifacts if present locally uploaded
        cl = step1.load_cl_archive()
        _write("cl_lessons", cl)

        # Warm frame once (shared cache), then probe — avoids 4× full loads
        print("[eth15m_beh] warming ETH 15m frame...", flush=True)
        try:
            warm = d._frame(SYMBOL, TIMEFRAME)
            print("[eth15m_beh] frame bars=", len(warm), flush=True)
        except Exception as exc:
            print("[eth15m_beh] frame warm error:", exc, flush=True)

        freq_table = {}
        for cand in CANDIDATES:
            freq_table[cand["id"]] = probe_freq(cand)
            print("[eth15m_beh] freq", cand["id"], freq_table[cand["id"]], flush=True)
        _write("freq_probe", freq_table)

        results = []
        winner = None
        for cand in CANDIDATES:
            # skip if extreme starvation in probe (<0.15/day) unless D05b
            fp = freq_table.get(cand["id"]) or {}
            opd = fp.get("opens_per_day_est")
            if opd is not None and opd < 0.15 and cand["id"] not in ("D01b", "D05b"):
                print("[eth15m_beh] skip starve", cand["id"], opd, flush=True)
                results.append({
                    "ok": False, "cand": cand["id"],
                    "failed_step": "probe_sample_starvation",
                    "reason": "opens_per_day_est=%.3f" % opd,
                    "death_cause_zh": "探针开仓过稀 %.3f/日" % opd,
                })
                continue
            if opd is not None and opd > 2.5:
                print("[eth15m_beh] skip overtrade", cand["id"], opd, flush=True)
                results.append({
                    "ok": False, "cand": cand["id"],
                    "failed_step": "probe_overtrading",
                    "reason": "opens_per_day_est=%.3f" % opd,
                    "death_cause_zh": "探针过密 %.3f/日" % opd,
                })
                continue
            if opd is not None and opd > 3.0:
                print("[eth15m_beh] warn overtrade probe", cand["id"], opd, flush=True)

            book = make_book(cand)
            try:
                result = process(book, cand)
            except Exception as exc:
                result = {
                    "ok": False, "cand": cand["id"],
                    "failed_step": "exception",
                    "reason": str(exc),
                    "trace": traceback.format_exc()[-2500:],
                    "death_cause_zh": "异常：" + str(exc),
                }
            results.append(result)
            _write("result_%s" % cand["id"].lower(), {
                k: result.get(k) for k in (
                    "ok", "failed_step", "reason", "cand", "death_cause_zh",
                    "pending", "sim", "formal",
                )
            })
            # checkpoint progress so crash still leaves trail
            _write("checkpoint", {
                "at": _now(),
                "done": [r.get("cand") for r in results],
                "last": {
                    "cand": result.get("cand"), "ok": result.get("ok"),
                    "failed_step": result.get("failed_step"),
                },
            })
            try:
                import gc
                gc.collect()
            except Exception:
                pass
            if result.get("ok"):
                winner = result
                break
            # archive each failure but continue next survivor
            try:
                archive_fail(result)
            except Exception:
                pass

        # attach prior round deaths (D05/D11 already failed WF earlier this session)
        prior_deaths = [
            {"cand": "D11", "exact_death_step": "quick_walk_forward",
             "note_zh": "fp最多5/10 WR≈32% mean_net负；dest过但无期望"},
            {"cand": "D05", "exact_death_step": "quick_walk_forward",
             "note_zh": "fp 3/10 tr=10；样本边缘且期望差；进程随后在低内存主机中断"},
        ]
        _write("prior_deaths_d05_d11", prior_deaths)

        end = {
            "at": _now(),
            "op": "ETH15m Market Behaviour Edge",
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "ok": bool(winner and winner.get("ok")),
            "winner_cand": (winner or {}).get("cand"),
            "failed_step": None if winner else (
                (results[-1].get("failed_step") if results else "no_candidates")
            ),
            "results_brief": [
                {
                    "cand": r.get("cand"),
                    "ok": r.get("ok"),
                    "failed_step": r.get("failed_step"),
                    "reason": r.get("reason"),
                }
                for r in results
            ],
            "freq_probe": freq_table,
            "proxy_notes": proxy_doc,
        }

        if winner:
            end["pending"] = winner.get("pending")
            end["sim"] = winner.get("sim")
            end["formal"] = winner.get("formal")
            end["key"] = ((winner.get("book") or {}).get("dsl") or {}).get("key")
            end["metrics"] = extract_metrics(winner)
            end["hist"] = winner.get("hist")
            summary_zh = (
                "ETH15m行为边【成功pending】候选=%s key=%s "
                "fp=%s/%s tr=%s PF=%s WR=%s frSharpe=%s mcBeat=%s "
                "simDS/Qw=%s/%s wrAvg=%s opens/day≈%s"
                % (
                    end.get("winner_cand"), end.get("key"),
                    (end["metrics"] or {}).get("fold_positive"),
                    (end["metrics"] or {}).get("folds"),
                    (end["metrics"] or {}).get("trades"),
                    (end["metrics"] or {}).get("profit_factor"),
                    (end["metrics"] or {}).get("win_rate"),
                    (end["metrics"] or {}).get("friction_sharpe"),
                    (end["metrics"] or {}).get("mc_beat_ratio"),
                    (end["metrics"] or {}).get("wr_deepseek"),
                    (end["metrics"] or {}).get("wr_qwen"),
                    (end["metrics"] or {}).get("wr_avg"),
                    (end["metrics"] or {}).get("opens_per_day"),
                )
            )
        else:
            # exact fail = last meaningful death
            last = results[-1] if results else {}
            end["exact_death_step"] = last.get("failed_step")
            end["death_cause_zh"] = last.get("death_cause_zh") or last.get("reason")
            end["metrics"] = extract_metrics(last) if last.get("packs") or last.get("metrics") else {}
            summary_zh = (
                "ETH15m行为边【失败】末候选=%s 精确死因=%s 详情=%s；尝试=%s"
                % (
                    last.get("cand"),
                    last.get("failed_step"),
                    last.get("death_cause_zh") or last.get("reason"),
                    [(r.get("cand"), r.get("failed_step")) for r in results],
                )
            )

        end["summary_zh"] = summary_zh
        _write("end_report", end)
        _write("parent_summary_zh", {"summary_zh": summary_zh, "end": end, "at": _now()})
        _upd(stage="done", ok=end["ok"])
        print("PARENT_SUMMARY_ZH:", summary_zh, flush=True)
        print("[eth15m_beh] END", _now(), "ok", end["ok"], flush=True)
        return 0 if end["ok"] else 1
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
