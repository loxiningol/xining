# -*- coding: utf-8 -*-
"""Deterministic market-state partitions (no AI rules)."""
from __future__ import print_function

import math


def _f(x):
    try:
        v = float(x)
    except Exception:
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _atr(candles, i, n=14):
    if i < n:
        return None
    trs = []
    for j in range(i - n + 1, i + 1):
        h = _f(candles[j].get("high"))
        l = _f(candles[j].get("low"))
        pc = _f(candles[j - 1].get("close")) if j > 0 else None
        if h is None or l is None:
            continue
        tr = h - l
        if pc is not None:
            tr = max(tr, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < n:
        return None
    return sum(trs) / float(len(trs))


def _ret(candles, i, n):
    if i < n:
        return None
    c0 = _f(candles[i - n].get("close"))
    c1 = _f(candles[i].get("close"))
    if not c0 or not c1:
        return None
    return c1 / c0 - 1.0


def _hour_utc(ts):
    # ts may be "YYYY-MM-DD HH:MM:SS" or ms
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # assume ms
        try:
            import datetime
            return datetime.datetime.utcfromtimestamp(float(ts) / 1000.0).hour
        except Exception:
            return None
    s = str(ts)
    try:
        # "... HH:MM:SS"
        part = s.split(" ")[-1]
        return int(part.split(":")[0])
    except Exception:
        return None


def session_of_hour(h):
    if h is None:
        return "UNK"
    if 0 <= h < 8:
        return "ASIA"
    if 8 <= h < 16:
        return "EU"
    return "US"


def tercile_label(rank_frac):
    if rank_frac is None:
        return "UNK"
    if rank_frac < 1.0 / 3.0:
        return "LO"
    if rank_frac < 2.0 / 3.0:
        return "MID"
    return "HI"


def build_state_series(candles, atr_n=14, trend_n=48):
    """Precompute per-bar state features + rolling ranks for terciles."""
    n = len(candles or [])
    atr_vals = [None] * n
    atr_pct = [None] * n
    trend = [None] * n
    trend_pct = [None] * n
    range_rel = [None] * n
    range_pct = [None] * n
    vol_rel = [None] * n
    vol_pct = [None] * n
    session = ["UNK"] * n

    atr_window = []
    trend_window = []
    range_window = []
    vol_window = []

    for i in range(n):
        row = candles[i]
        a = _atr(candles, i, atr_n)
        atr_vals[i] = a
        t = _ret(candles, i, trend_n)
        trend[i] = t
        c = _f(row.get("close"))
        h = _f(row.get("high"))
        l = _f(row.get("low"))
        v = _f(row.get("volume")) or 0.0
        rr = None
        if a and a > 0 and h is not None and l is not None:
            rr = (h - l) / a
        range_rel[i] = rr
        session[i] = session_of_hour(_hour_utc(row.get("ts")))

        # rolling percentile via window of last 500
        def push_rank(window, val, store, idx, maxlen=500):
            if val is None:
                store[idx] = None
                return
            window.append(val)
            if len(window) > maxlen:
                window.pop(0)
            # rank fraction
            less = sum(1 for x in window if x < val)
            store[idx] = less / float(len(window))

        push_rank(atr_window, (a / c) if (a and c) else None, atr_pct, i)
        push_rank(trend_window, t, trend_pct, i)
        push_rank(range_window, rr, range_pct, i)
        push_rank(vol_window, v, vol_pct, i)
        vol_rel[i] = v

    states = []
    for i in range(n):
        states.append({
            "i": i,
            "vol": tercile_label(atr_pct[i]),
            "trend": tercile_label(trend_pct[i]),
            "range": tercile_label(range_pct[i]),
            "volume": tercile_label(vol_pct[i]),
            "session": session[i],
            "atr_pctile": atr_pct[i],
            "trend_pctile": trend_pct[i],
        })
    return states


def cell_id(state, keys):
    parts = []
    for k in keys:
        parts.append("%s=%s" % (k, state.get(k)))
    return "|".join(parts)


# Mechanism candidate definitions: state conjunctions (market event regions)
MECHANISM_SPECS = [
    {
        "mechanism_id": "M_UNCONDITIONAL",
        "payoff_source": "unconditional_next_bar_entry_path",
        "keys": [],
        "description": "Baseline: every stride bar enters; no state filter",
    },
    {
        "mechanism_id": "M_VOL_TERCILE",
        "payoff_source": "volatility_regime_path",
        "keys": ["vol"],
        "description": "ATR%% tercile regimes",
    },
    {
        "mechanism_id": "M_TREND_TERCILE",
        "payoff_source": "trend_regime_path",
        "keys": ["trend"],
        "description": "Medium-horizon return tercile regimes",
    },
    {
        "mechanism_id": "M_SESSION",
        "payoff_source": "session_clock_path",
        "keys": ["session"],
        "description": "UTC session buckets ASIA/EU/US",
    },
    {
        "mechanism_id": "M_VOL_x_TREND",
        "payoff_source": "vol_and_trend_conjunction",
        "keys": ["vol", "trend"],
        "description": "Volatility × trend conjunction",
    },
    {
        "mechanism_id": "M_VOL_x_SESSION",
        "payoff_source": "vol_and_session_conjunction",
        "keys": ["vol", "session"],
        "description": "Volatility × session conjunction",
    },
    {
        "mechanism_id": "M_RANGE_x_TREND",
        "payoff_source": "range_and_trend_conjunction",
        "keys": ["range", "trend"],
        "description": "Intrabar range/ATR × trend",
    },
    {
        "mechanism_id": "M_VOL_x_TREND_x_SESSION",
        "payoff_source": "vol_trend_session_conjunction",
        "keys": ["vol", "trend", "session"],
        "description": "Vol × trend × session (finer region)",
    },
]
