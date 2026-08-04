# -*- coding: utf-8 -*-
"""EasyQuant-style automated factor mining on OKX candle caches.

Public eqlib is A-share oriented. This module keeps the *framework habit*
(initialize → feature matrix → rank → certify) but mines factors from local
OKX formal_*_candles_cache.json so creation is professional and reproducible.

Does NOT place trades. Output is research evidence for GLM mechanism design.
"""
from __future__ import print_function

import json
import math
import os
from datetime import datetime
from pathlib import Path


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def probe_easyquant():
    """Capability snapshot (eqlib optional + local OKX miner always available)."""
    mode = str(os.environ.get("QIYU_EASYQUANT_MODE") or "mine").strip().lower()
    out = {
        "ok": True,
        "mode": mode,
        "provider": "easyquant_style",
        "available": True,
        "backend": "okx_candle_factor_miner_v1",
        "notes": [
            "Local OKX candle factor miner active (EasyQuant-style automation).",
            "eqlib A-share package is optional and not required for crypto swaps.",
            "Prefer research R2/S3 long history when configured; formal_* remains live short window.",
        ],
        "probed_at": _now(),
    }
    if mode in ("0", "off", "false", "no", "disabled"):
        out["ok"] = False
        out["available"] = False
        out["notes"] = ["QIYU_EASYQUANT_MODE=off"]
        return out
    try:
        from . import research_candle_store as rcs
        out["research_candles"] = rcs.probe()
    except Exception as exc:
        out["research_candles"] = {"ok": False, "error": str(exc)[:120]}
    try:
        import eqlib  # noqa: F401
        out["eqlib"] = getattr(__import__("eqlib"), "__version__", "present")
        out["notes"].append("eqlib also importable (A-share); not used as OKX fill engine.")
    except Exception as exc:
        out["notes"].append("eqlib_optional_missing: %s" % exc)
    endpoint = str(os.environ.get("QIYU_EASYQUANT_ENDPOINT") or "").strip()
    if endpoint:
        out["endpoint"] = endpoint
    return out


def resolve_candle_cache(symbol, timeframe):
    """Map SYMBOL+tf → /root/auto_trade/formal_*_candles_cache.json."""
    sym = str(symbol or "").upper()
    tf = str(timeframe or "5m").lower()
    base = sym.split("-")[0].lower()
    # normalize commodity suffixes
    aliases = {
        "cl": "cl", "ng": "ng", "xau": "xau", "xag": "xag",
        "btc": "btc", "eth": "eth", "sol": "sol", "ada": "ada",
        "xrp": "xrp", "ltc": "ltc", "bnb": "bnb", "doge": "doge",
    }
    key = aliases.get(base, base)
    name = "formal_%s_%s_candles_cache.json" % (key, tf)
    path = _root() / "auto_trade" / name
    if path.exists():
        return path
    # try without swap naming quirks
    alt = _root() / "auto_trade" / ("formal_%s-%s_candles_cache.json" % (key, tf))
    return path if path.exists() else (alt if alt.exists() else path)


def load_candles(symbol, timeframe, max_bars=1200, prefer_research=None, lookback_days=None):
    """Load candles for creation/research.

    Preference:
      1) Research store (R2/S3/local long history) when enabled & populated
      2) formal_* short rolling live cache (never mutated by research path)

    prefer_research: None → auto from env QIYU_CREATION_PREFER_RESEARCH (default 1)
    """
    if prefer_research is None:
        prefer_research = str(os.environ.get("QIYU_CREATION_PREFER_RESEARCH") or "1").strip().lower() not in (
            "0", "false", "no", "off",
        )
    lookback_days = int(
        lookback_days
        if lookback_days is not None
        else (os.environ.get("QIYU_RESEARCH_LOOKBACK_DAYS") or 400)
    )

    research_meta = None
    require_research = str(
        os.environ.get("QIYU_CREATION_REQUIRE_RESEARCH_HISTORY") or "1"
    ).strip().lower() not in ("0", "false", "no", "off")
    if prefer_research:
        try:
            from . import research_candle_store as rcs
            # allow large max_bars for research; cap by env
            cap = int(os.environ.get("QIYU_RESEARCH_MAX_BARS") or max(int(max_bars), 50000))
            got = rcs.load_for_creation(
                symbol, timeframe, lookback_days=lookback_days, max_bars=cap,
            )
            research_meta = {
                "ok": got.get("ok"),
                "backend": got.get("backend"),
                "n": got.get("n"),
                "error": got.get("error"),
                "start_ts": got.get("start_ts"),
                "end_ts": got.get("end_ts"),
                "path": got.get("path"),
            }
            if got.get("ok") and got.get("candles"):
                rows = got["candles"]
                if max_bars and len(rows) > int(max_bars):
                    rows = rows[-int(max_bars):]
                return {
                    "ok": True,
                    "path": "research_store:%s" % got.get("backend"),
                    "n": len(rows),
                    "candles": rows,
                    "source": "research_r2_or_local",
                    "research": research_meta,
                    "fetched_at": got.get("at"),
                    "symbol": symbol,
                    "timeframe": timeframe,
                }
            if require_research:
                return {
                    "ok": False,
                    "error": "research_history_required_not_found",
                    "path": got.get("path"),
                    "candles": [],
                    "research": research_meta,
                    "hint_zh": (
                        "创造管道禁止静默回退 formal 短窗。"
                        "请提供研究长历史（R2/分片或 local/*_research.json）。"
                    ),
                    "symbol": symbol,
                    "timeframe": timeframe,
                }
        except Exception as exc:
            research_meta = {"ok": False, "error": str(exc)[:200]}
            if require_research:
                return {
                    "ok": False,
                    "error": "research_history_load_exception",
                    "path": None,
                    "candles": [],
                    "research": research_meta,
                    "hint_zh": "创造管道要求研究长历史，加载异常且禁止 formal 短窗兜底。",
                    "symbol": symbol,
                    "timeframe": timeframe,
                }

    path = resolve_candle_cache(symbol, timeframe)
    if not path.exists():
        return {
            "ok": False,
            "error": "candle_cache_missing",
            "path": str(path),
            "candles": [],
            "research": research_meta,
            "hint_zh": "短窗缺失且研究仓无数据；请配置 R2 并回填，或等待 formal 热缓存。",
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": str(path), "candles": [], "research": research_meta}
    candles = raw.get("candles") if isinstance(raw, dict) else raw
    if not isinstance(candles, list):
        return {"ok": False, "error": "bad_candle_shape", "path": str(path), "candles": [], "research": research_meta}
    rows = []
    for c in candles[-int(max_bars):]:
        if not isinstance(c, dict):
            continue
        try:
            rows.append({
                "ts": c.get("ts"),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
            })
        except Exception:
            continue
    return {
        "ok": len(rows) >= 80,
        "path": str(path),
        "n": len(rows),
        "candles": rows,
        "source": "formal_short_cache",
        "research": research_meta,
        "note_zh": (
            "使用实盘 formal 短窗；若需 2024YTD 请配置 R2 并运行 research_candles_backfill_r2.py"
            if research_meta and not research_meta.get("ok")
            else None
        ),
        "fetched_at": (raw.get("fetched_at") if isinstance(raw, dict) else None),
        "symbol": symbol,
        "timeframe": timeframe,
    }


def _sma(xs, n):
    out = [None] * len(xs)
    s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / float(n)
    return out


def _std(xs, n):
    out = [None] * len(xs)
    for i in range(len(xs)):
        if i < n - 1:
            continue
        window = xs[i - n + 1: i + 1]
        m = sum(window) / float(n)
        var = sum((v - m) ** 2 for v in window) / float(max(n - 1, 1))
        out[i] = var ** 0.5
    return out


def _delta_series(values, lag=1):
    """Change-rate feature: x[t] - x[t-lag]."""
    lag = max(1, int(lag or 1))
    values = list(values or [])
    out = [None] * len(values)
    for i in range(lag, len(values)):
        a = values[i]
        b = values[i - lag]
        if a is None or b is None:
            continue
        try:
            out[i] = float(a) - float(b)
        except (TypeError, ValueError):
            continue
    return out


def _slope_series(values, window=6):
    """Simple end-minus-start slope over a trailing window."""
    window = max(2, int(window or 6))
    values = list(values or [])
    out = [None] * len(values)
    for i in range(window - 1, len(values)):
        a = values[i]
        b = values[i - window + 1]
        if a is None or b is None:
            continue
        try:
            out[i] = (float(a) - float(b)) / float(window - 1)
        except (TypeError, ValueError):
            continue
    return out


def _build_factor_matrix(candles):
    c = [r["close"] for r in candles]
    h = [r["high"] for r in candles]
    l = [r["low"] for r in candles]
    o = [r["open"] for r in candles]
    n = len(c)
    ret1 = [None] + [(c[i] / c[i - 1] - 1.0) for i in range(1, n)]
    ret3 = [None] * 3 + [(c[i] / c[i - 3] - 1.0) for i in range(3, n)]
    ret12 = [None] * 12 + [(c[i] / c[i - 12] - 1.0) for i in range(12, n)]
    ma20 = _sma(c, 20)
    std20 = _std(c, 20)
    vol_z = []
    for i in range(n):
        if ma20[i] is None or std20[i] is None or std20[i] <= 1e-12:
            vol_z.append(None)
        else:
            vol_z.append((c[i] - ma20[i]) / std20[i])
    range_pct = [((h[i] - l[i]) / c[i]) if c[i] else None for i in range(n)]
    upper_wick = [
        ((h[i] - max(o[i], c[i])) / c[i]) if c[i] else None for i in range(n)
    ]
    lower_wick = [
        ((min(o[i], c[i]) - l[i]) / c[i]) if c[i] else None for i in range(n)
    ]
    # rolling 288-ish for 5m≈24h; scale with tf-agnostic 96 bars as portable window
    win = 96
    dist_high = [None] * n
    dist_low = [None] * n
    for i in range(n):
        if i < win - 1:
            continue
        hh = max(h[i - win + 1: i + 1])
        ll = min(l[i - win + 1: i + 1])
        dist_high[i] = (c[i] / hh - 1.0) if hh else None
        dist_low[i] = (c[i] / ll - 1.0) if ll else None
    rets_abs = [abs(x) if x is not None else None for x in ret1]
    atr_proxy = _sma([((h[i] - l[i]) / c[i]) if c[i] else 0.0 for i in range(n)], 14)

    # RSI-14 — must match backtest_engine_v2.rsi14 (ewm alpha=1/14, adjust=False)
    # so recipe_policy can formally map rsi_14 → rsi14 without estimator drift.
    rsi14 = [None] * n
    alpha = 1.0 / 14.0
    avg_gain = None
    avg_loss = None
    for i in range(1, n):
        dlt = c[i] - c[i - 1]
        g = dlt if dlt > 0 else 0.0
        lss = (-dlt) if dlt < 0 else 0.0
        if avg_gain is None:
            avg_gain = g
            avg_loss = lss
        else:
            avg_gain = (1.0 - alpha) * avg_gain + alpha * g
            avg_loss = (1.0 - alpha) * avg_loss + alpha * lss
        if avg_loss <= 1e-12:
            rsi14[i] = 100.0
        else:
            rsi14[i] = 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))

    # volume_z：有真实成交量用成交量；否则用 bar-range 代理。
    # 必须与 backtest_engine_v2.vol_z20 一致，否则唯一可正式编译的
    # close_z_20×volume_z 交集会因 volume 全空而永久 formal_capability_blocked → 存活0。
    vols = []
    for r in candles:
        v = r.get("volume")
        if v is None:
            v = r.get("vol")
        try:
            vols.append(float(v) if v is not None else None)
        except Exception:
            vols.append(None)
    volume_from_ohlc_proxy = not any(v is not None for v in vols)
    if volume_from_ohlc_proxy:
        vv = [float(h[i] - l[i]) for i in range(n)]
    else:
        vv = [float(v) if v is not None else 0.0 for v in vols]
    volume_z = [None] * n
    vma = _sma(vv, 20)
    vstd = _std(vv, 20)
    for i in range(n):
        if vma[i] is None or vstd[i] is None or vstd[i] <= 1e-12:
            volume_z[i] = None
        else:
            volume_z[i] = (vv[i] - vma[i]) / vstd[i]

    # exhaustion score: deep negative z + low RSI + long lower wick (higher = more exhausted)
    exhaustion_score = [None] * n
    for i in range(n):
        if vol_z[i] is None or rsi14[i] is None or lower_wick[i] is None:
            continue
        exhaustion_score[i] = (
            max(0.0, -float(vol_z[i])) * 0.45
            + max(0.0, (30.0 - float(rsi14[i])) / 30.0) * 0.35
            + min(3.0, float(lower_wick[i]) * 100.0) * 0.20
        )

    # vol squeeze / expansion: ATR vs its own history + range compression
    # squeeze_score high = compressed; expansion_score high = breakout energy after compress
    atr_vals = [float(x) if x is not None else None for x in atr_proxy]
    squeeze_score = [None] * n
    expansion_score = [None] * n
    win_s = 48
    for i in range(n):
        if atr_vals[i] is None or i < win_s - 1:
            continue
        window = [atr_vals[j] for j in range(i - win_s + 1, i + 1) if atr_vals[j] is not None]
        if len(window) < win_s // 2:
            continue
        ordered = sorted(window)
        # percentile rank of current ATR in window (0=most compressed)
        rank = sum(1 for v in ordered if v <= atr_vals[i]) / float(len(ordered))
        squeeze_score[i] = 1.0 - float(rank)  # high => tight vol
        # expansion after squeeze: compressed recently AND current range/abs ret elevated
        recent_sq = [
            squeeze_score[j] for j in range(max(0, i - 6), i)
            if squeeze_score[j] is not None
        ]
        prior_compress = (sum(recent_sq) / float(len(recent_sq))) if recent_sq else 0.0
        rng = float(range_pct[i] or 0.0)
        ar = float(rets_abs[i] or 0.0) if rets_abs[i] is not None else 0.0
        expansion_score[i] = prior_compress * 0.55 + min(2.0, rng * 80.0) * 0.25 + min(2.0, ar * 80.0) * 0.20

    # Mechanism-preserving OHLCV proxies.  These are explicitly derived proxies,
    # never substitutes presented as real order book / OI / liquidation data.
    close_location = [None] * n
    signed_volume_pressure = [None] * n
    impact_per_volume_proxy = [None] * n
    impact_decay_proxy = [None] * n
    downside_velocity_decay = [None] * n
    upside_velocity_decay = [None] * n
    absorption_proxy = [None] * n
    reclaim_strength = [None] * n
    squeeze_persistence = [None] * n
    volatility_acceleration = [None] * n
    trend_efficiency_12 = [None] * n
    breakout_acceptance = [None] * n
    for i in range(n):
        bar_range = float(h[i] - l[i])
        if bar_range > 1e-12:
            close_location[i] = (c[i] - l[i]) / bar_range
        vol_ratio = None
        if vma[i] is not None and float(vma[i]) > 1e-12:
            vol_ratio = float(vv[i]) / float(vma[i])
        if ret1[i] is not None and vol_ratio is not None:
            signed_volume_pressure[i] = (1.0 if ret1[i] >= 0 else -1.0) * vol_ratio
            impact_per_volume_proxy[i] = abs(float(ret1[i])) / max(vol_ratio, 0.05)
        if i >= 1 and impact_per_volume_proxy[i - 1] is not None and impact_per_volume_proxy[i] is not None:
            impact_decay_proxy[i] = float(impact_per_volume_proxy[i - 1]) - float(impact_per_volume_proxy[i])
        if i >= 3:
            prior_down = sum(max(0.0, -float(ret1[j] or 0.0)) for j in range(i - 3, i)) / 3.0
            prior_up = sum(max(0.0, float(ret1[j] or 0.0)) for j in range(i - 3, i)) / 3.0
            downside_velocity_decay[i] = prior_down - max(0.0, -float(ret1[i] or 0.0))
            upside_velocity_decay[i] = prior_up - max(0.0, float(ret1[i] or 0.0))
        if vol_ratio is not None and range_pct[i] is not None and close_location[i] is not None:
            body = abs(c[i] - o[i]) / max(bar_range, 1e-12)
            absorption_proxy[i] = vol_ratio * max(0.0, 1.0 - body) * (
                1.0 - abs(float(close_location[i]) - 0.5)
            )
            reclaim_strength[i] = (
                max(0.0, float(close_location[i]) - 0.5) *
                (1.0 + max(0.0, float(lower_wick[i] or 0.0) * 100.0))
            )
        if i >= 5:
            sq = [float(squeeze_score[j]) for j in range(i - 5, i + 1)
                  if squeeze_score[j] is not None]
            if sq:
                squeeze_persistence[i] = sum(sq) / float(len(sq))
        if i >= 1 and atr_proxy[i] is not None and atr_proxy[i - 1] is not None:
            volatility_acceleration[i] = float(atr_proxy[i]) - float(atr_proxy[i - 1])
        if i >= 12:
            path = sum(abs(c[j] - c[j - 1]) for j in range(i - 11, i + 1))
            trend_efficiency_12[i] = abs(c[i] - c[i - 12]) / path if path > 1e-12 else 0.0
            prev_hi = max(h[i - 12:i])
            prev_lo = min(l[i - 12:i])
            if c[i] > prev_hi:
                breakout_acceptance[i] = (c[i] - prev_hi) / max(c[i], 1e-12)
            elif c[i] < prev_lo:
                breakout_acceptance[i] = (c[i] - prev_lo) / max(c[i], 1e-12)
            else:
                breakout_acceptance[i] = 0.0

    # Donchian / trend bias — prior completed bars only (aligned with prev_high20/low20).
    donchian20_long_break = [None] * n
    donchian20_short_break = [None] * n
    trend_bias_50_200 = [None] * n
    sma50 = _sma(c, 50)
    sma200 = _sma(c, 200)
    for i in range(n):
        if i >= 20:
            prev_hi = max(h[i - 20:i])
            prev_lo = min(l[i - 20:i])
            if prev_hi:
                donchian20_long_break[i] = c[i] / prev_hi - 1.0
            if c[i]:
                donchian20_short_break[i] = prev_lo / c[i] - 1.0
        if sma50[i] is not None and sma200[i] is not None and c[i]:
            trend_bias_50_200[i] = (sma50[i] - sma200[i]) / c[i]

    # Bollinger(20,2) — sample std aligned with formal precompute_indicators.
    bb_lower_dist = [None] * n
    bb_upper_dist = [None] * n
    bb_mid_reclaim = [None] * n
    bb_width = [None] * n
    sma20 = _sma(c, 20)
    std20 = _std(c, 20)
    for i in range(n):
        if sma20[i] is None or std20[i] is None or not c[i]:
            continue
        mid = float(sma20[i])
        sd = float(std20[i])
        if mid == 0.0:
            continue
        lower = mid - 2.0 * sd
        upper = mid + 2.0 * sd
        bb_lower_dist[i] = (c[i] - lower) / c[i]
        bb_upper_dist[i] = (upper - c[i]) / c[i]
        bb_mid_reclaim[i] = (c[i] - mid) / c[i]
        bb_width[i] = (upper - lower) / mid

    return {
        "ret_1": ret1,
        "ret_3": ret3,
        "ret_12": ret12,
        "close_z_20": vol_z,
        "range_pct": range_pct,
        "upper_wick_pct": upper_wick,
        "lower_wick_pct": lower_wick,
        "dist_roll_high": dist_high,
        "dist_roll_low": dist_low,
        "atr_pct_14": atr_proxy,
        "abs_ret_1": rets_abs,
        "rsi_14": rsi14,
        "volume_z": volume_z,
        "exhaustion_score": exhaustion_score,
        "squeeze_score": squeeze_score,
        "expansion_score": expansion_score,
        "close_location": close_location,
        "signed_volume_pressure": signed_volume_pressure,
        "impact_per_volume_proxy": impact_per_volume_proxy,
        "impact_decay_proxy": impact_decay_proxy,
        "downside_velocity_decay": downside_velocity_decay,
        "upside_velocity_decay": upside_velocity_decay,
        "absorption_proxy": absorption_proxy,
        "reclaim_strength": reclaim_strength,
        "bullish_reclaim": reclaim_strength,
        "trend_bias_50_200": trend_bias_50_200,
        "donchian20_long_break": donchian20_long_break,
        "donchian20_short_break": donchian20_short_break,
        "bb_lower_dist": bb_lower_dist,
        "bb_upper_dist": bb_upper_dist,
        "bb_mid_reclaim": bb_mid_reclaim,
        "bb_width": bb_width,
        "squeeze_persistence": squeeze_persistence,
        "volatility_acceleration": volatility_acceleration,
        "trend_efficiency_12": trend_efficiency_12,
        "breakout_acceptance": breakout_acceptance,
        # Representation upgrades: change-rate + discrete state axes
        "delta_rsi_14": _delta_series(rsi14, 2),
        "delta_volume_z": _delta_series(volume_z, 2),
        "delta_bb_width": _delta_series(bb_width, 3),
        "slope_close_6": _slope_series(c, 6),
        "trend_state": trend_bias_50_200,
        "volatility_state_compressed": [
            (1.0 if (w is not None and sp is not None and w < 0.02 and sp > 0.5) else
             (0.0 if w is not None else None))
            for w, sp in zip(bb_width, squeeze_persistence)
        ],
    }


def _forward_returns(closes, horizon=3):
    n = len(closes)
    out = [None] * n
    for i in range(n - horizon):
        if closes[i]:
            out[i] = closes[i + horizon] / closes[i] - 1.0
    return out


def _rank_factor(values, fwd, direction="long_high", q=0.8):
    """Quantile long/short on factor → trade returns list + stats."""
    pairs = []
    for v, r in zip(values, fwd):
        if v is None or r is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            continue
        pairs.append((float(v), float(r)))
    if len(pairs) < 40:
        return None
    pairs.sort(key=lambda x: x[0])
    k = max(5, int(len(pairs) * (1.0 - float(q))))
    if direction == "long_high":
        selected = pairs[-k:]
        sign = 1.0
    elif direction == "long_low":
        selected = pairs[:k]
        sign = 1.0
    elif direction == "short_high":
        selected = pairs[-k:]
        sign = -1.0
    else:  # short_low
        selected = pairs[:k]
        sign = -1.0
    rets = [sign * r for _, r in selected]
    if not rets:
        return None
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    wr = len(wins) / float(len(rets))
    avg_win = (sum(wins) / float(len(wins))) if wins else 0.0
    avg_loss = (sum(losses) / float(len(losses))) if losses else 0.0
    mean = sum(rets) / float(len(rets))
    # equity curve from unit-notional trades
    eq = [1.0]
    for r in rets:
        eq.append(eq[-1] * (1.0 + r))
    payoff = (avg_win / abs(avg_loss)) if avg_loss < 0 else None
    return {
        "n": len(rets),
        "win_rate": wr,
        "mean_net": mean,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff": payoff,
        "returns": rets,
        "equity_curve": eq,
        "direction_rule": direction,
        "quantile": q,
    }


def mine_factors(symbol, timeframe, horizon=3, top_k=5, max_bars=1200):
    """Automated factor dig → ranked candidates with trade-return series."""
    loaded = load_candles(symbol, timeframe, max_bars=max_bars)
    if not loaded.get("ok"):
        return {
            "ok": False,
            "error": loaded.get("error") or "load_fail",
            "path": loaded.get("path"),
            "factors": [],
            "at": _now(),
        }
    candles = loaded["candles"]
    closes = [r["close"] for r in candles]
    matrix = _build_factor_matrix(candles)
    fwd = _forward_returns(closes, horizon=int(horizon))
    specs = [
        ("close_z_20", "long_low", "均值回归：价格低于均线过深后回升"),
        ("close_z_20", "short_high", "均值回归：价格高于均线过深后回落"),
        ("dist_roll_low", "long_low", "滚动低点外延后收回（扫荡多）"),
        ("dist_roll_high", "short_high", "滚动高点外延后收回（扫荡空）"),
        ("lower_wick_pct", "long_high", "长下影线后多头回收"),
        ("upper_wick_pct", "short_high", "长上影线后空头回收"),
        ("ret_12", "long_high", "短动量延续"),
        ("ret_12", "short_high", "短动量衰竭反手"),
        ("range_pct", "long_low", "压缩后方向选择（低波）"),
        ("atr_pct_14", "long_high", "波动扩张环境"),
    ]
    scored = []
    for name, rule, thesis in specs:
        series = matrix.get(name) or []
        stats = _rank_factor(series, fwd, direction=rule, q=0.8)
        if not stats:
            continue
        # score: mean_net * sqrt(n) * wr emphasis, punish negative mean
        if stats["mean_net"] <= 0:
            score = stats["mean_net"] * 10.0
        else:
            score = stats["mean_net"] * (stats["n"] ** 0.5) * (0.5 + stats["win_rate"])
        scored.append({
            "factor": name,
            "rule": rule,
            "thesis_zh": thesis,
            "score": score,
            "stats": {
                "n": stats["n"],
                "win_rate": stats["win_rate"],
                "mean_net": stats["mean_net"],
                "avg_win": stats["avg_win"],
                "avg_loss": stats["avg_loss"],
                "payoff": stats["payoff"],
            },
            "returns": stats["returns"],
            "equity_curve": stats["equity_curve"],
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[: int(top_k)]
    return {
        "ok": bool(top),
        "schema": "qiyu_easyquant_factor_mine_v1",
        "symbol": symbol,
        "timeframe": timeframe,
        "horizon_bars": int(horizon),
        "candle_path": loaded.get("path"),
        "n_bars": loaded.get("n"),
        "n_scored": len(scored),
        "factors": top,
        "all_scores": [
            {"factor": x["factor"], "rule": x["rule"], "score": x["score"],
             "win_rate": x["stats"]["win_rate"], "mean_net": x["stats"]["mean_net"], "n": x["stats"]["n"]}
            for x in scored[:12]
        ],
        "at": _now(),
        "note_zh": (
            "EasyQuant 风格自动化因子挖掘：基于本地 OKX K 线分位数信号，"
            "尚未经 STEP A 成交摩擦；仅作创造阶段研究输入。"
        ),
    }


def modeling_envelope(brief=None, focus=None, probe=None, mine=None):
    probe = probe if probe is not None else probe_easyquant()
    focus = focus or {}
    mine = mine or {}
    tops = []
    for f in (mine.get("factors") or [])[:5]:
        tops.append({
            "factor": f.get("factor"),
            "rule": f.get("rule"),
            "thesis_zh": f.get("thesis_zh"),
            "win_rate": (f.get("stats") or {}).get("win_rate"),
            "mean_net": (f.get("stats") or {}).get("mean_net"),
            "n": (f.get("stats") or {}).get("n"),
            "score": f.get("score"),
        })
    return {
        "schema": "qiyu_easyquant_modeling_envelope_v1",
        "brief": str(brief or "").strip(),
        "focus": {
            "symbol": focus.get("symbol"),
            "timeframe": focus.get("timeframe"),
            "direction": focus.get("direction"),
        },
        "easyquant": probe,
        "factor_mine": {
            "ok": bool(mine.get("ok")),
            "n_bars": mine.get("n_bars"),
            "top_factors": tops,
            "note_zh": mine.get("note_zh"),
        },
        "guidance_zh": (
            "创造阶段必须优先使用已挖掘因子的可复现统计，禁止空口编造胜率。"
            "mechanism_spec 的 entry_logic 应锚定 top_factors 的因果叙述；"
            "正式过关仍以 ADA5 四复核 + OKX 摩擦回测为准。"
        ),
        "built_at": _now(),
    }
