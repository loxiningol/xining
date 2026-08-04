#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bootstrap NG15m ATR stats from 5m candle cache (stdlib only — thrash-safe)."""
from __future__ import print_function
import json
import math
import os
from collections import defaultdict

OUT = "/root/auto_trade/dual_engine/frost3_ng15m_xrpport_atr_stats.json"
PATH = "/root/auto_trade/formal_ng_5m_candles_cache.json"


def fget(b, *keys):
    for k in keys:
        if k in b:
            return b[k]
    return None


def pctile(xs, p):
    if not xs:
        return None
    ys = sorted(xs)
    i = int(round((p / 100.0) * (len(ys) - 1)))
    return ys[max(0, min(len(ys) - 1, i))]


def pack(xs):
    return {
        "n": len(xs),
        "mean": round(sum(xs) / float(len(xs)), 4) if xs else None,
        "p": {str(p): round(pctile(xs, p), 4) for p in (5, 10, 25, 50, 75, 90, 95, 99)},
    }


def mean(xs):
    return sum(xs) / float(len(xs)) if xs else 0.0


def sma(xs, n):
    out = [None] * len(xs)
    s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / float(n)
    return out


def main():
    raw = json.load(open(PATH))
    candles = raw
    if isinstance(raw, dict):
        for k in ("candles", "data", "bars", "ohlcv"):
            if isinstance(raw.get(k), list):
                candles = raw[k]
                break
        else:
            vals = list(raw.values())
            if vals and all(isinstance(v, dict) for v in vals[:3]):
                candles = vals

    rows = []
    for b in candles:
        if not isinstance(b, dict):
            continue
        ts = fget(b, "ts", "timestamp", "t", "time", "open_time")
        o = fget(b, "o", "open")
        h = fget(b, "h", "high")
        l = fget(b, "l", "low")
        c = fget(b, "c", "close")
        if ts is None or c is None or o is None or h is None or l is None:
            continue
        try:
            ts = int(ts)
            if ts < 1e12:
                ts *= 1000
            o, h, l, c = float(o), float(h), float(l), float(c)
        except Exception:
            continue
        rows.append((ts, o, h, l, c))
    rows.sort()
    print("5m_rows", len(rows))

    buckets = {}
    order = []
    for ts, o, h, l, c in rows:
        bucket = ts - (ts % (15 * 60 * 1000))
        if bucket not in buckets:
            buckets[bucket] = [o, h, l, c]
            order.append(bucket)
        else:
            b = buckets[bucket]
            b[1] = max(b[1], h)
            b[2] = min(b[2], l)
            b[3] = c
    bars = [buckets[k] for k in order]
    print("15m_bars", len(bars))
    if len(bars) < 100:
        raise SystemExit("too_few_bars")

    closes = [b[3] for b in bars]
    opens = [b[0] for b in bars]
    highs = [b[1] for b in bars]
    lows = [b[2] for b in bars]
    tr = []
    for i in range(len(bars)):
        if i == 0:
            tr.append(highs[i] - lows[i])
        else:
            tr.append(max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            ))
    atr = sma(tr, 14)
    atr_pct = []
    br = []
    for i in range(len(bars)):
        if atr[i] is None or closes[i] == 0:
            continue
        atr_pct.append(100.0 * atr[i] / closes[i])
        br.append(100.0 * (highs[i] - lows[i]) / closes[i])

    deltas = [0.0]
    for i in range(1, len(closes)):
        deltas.append(closes[i] - closes[i - 1])
    rsi = [None] * len(closes)
    for i in range(14, len(closes)):
        window = deltas[i - 13:i + 1]
        up = sum(x for x in window if x > 0) / 14.0
        down = sum(-x for x in window if x < 0) / 14.0
        rs = up / (down + 1e-12)
        rsi[i] = 100.0 - 100.0 / (1.0 + rs)

    z = [None] * len(closes)
    for i in range(19, len(closes)):
        w = closes[i - 19:i + 1]
        m = sum(w) / 20.0
        var = sum((x - m) ** 2 for x in w) / 20.0
        sd = math.sqrt(var) + 1e-12
        z[i] = (closes[i] - m) / sd

    ema16 = [None] * len(closes)
    k = 2.0 / 17.0
    for i, c in enumerate(closes):
        if i == 0:
            ema16[i] = c
        else:
            ema16[i] = closes[i] * k + ema16[i - 1] * (1 - k)

    tail_n = min(2880, len(atr_pct))
    atr_t = atr_pct[-tail_n:]
    br_t = br[-tail_n:]
    idx_tail = list(range(max(0, len(bars) - tail_n), len(bars)))
    rsi_t = [rsi[i] for i in idx_tail if rsi[i] is not None]
    z_t = [z[i] for i in idx_tail if z[i] is not None]

    combos = {}

    def combo(name, pred):
        hits = 0
        n = 0
        for i in idx_tail:
            if rsi[i] is None or z[i] is None or ema16[i] is None:
                continue
            n += 1
            if pred(i):
                hits += 1
        combos[name] = round(100.0 * hits / float(n), 3) if n else 0.0

    combo("rsi35_z125_green", lambda i: rsi[i] <= 35 and z[i] <= -1.25 and closes[i] > opens[i])
    combo("rsi40_z100_green", lambda i: rsi[i] <= 40 and z[i] <= -1.0 and closes[i] > opens[i])
    combo("rsi45_z075_green", lambda i: rsi[i] <= 45 and z[i] <= -0.75 and closes[i] > opens[i])
    combo("rsi40_z100_green_gt_ema16", lambda i: rsi[i] <= 40 and z[i] <= -1.0 and closes[i] > opens[i] and closes[i] > ema16[i])
    combo("rsi45_z075_green_gt_ema16", lambda i: rsi[i] <= 45 and z[i] <= -0.75 and closes[i] > opens[i] and closes[i] > ema16[i])

    med_atr = pctile(atr_t, 50)
    p75_br = pctile(br_t, 75)
    stop_ratio = round(0.9 / med_atr, 3) if med_atr else None
    if med_atr and (med_atr >= 0.55 or (p75_br or 0) >= 0.9):
        bias = "looser_rsi_z_stronger_reclaim_longer_hold"
        verdict = (
            "0.9%%止损相对NG15m噪声偏紧(ATR中位≈%.3f%%, range p75≈%.3f%%)；"
            "需更强收回确认+会话过滤+更长hold" % (med_atr, p75_br or 0)
        )
    else:
        bias = "moderate_confirm"
        verdict = (
            "0.9%%止损相对ATR中位有缓冲(ATR中位≈%.3f%%)；仍建议会话+阳线收回"
            % (med_atr or 0,)
        )

    hours = [((k // 1000) % 86400) // 3600 for k in order]
    rec_h = defaultdict(list)
    deep_h = defaultdict(list)
    for i, hr in enumerate(hours):
        if rsi[i] is None or z[i] is None:
            continue
        deep_h[hr].append(1.0 if (z[i] <= -1.25 and rsi[i] <= 35) else 0.0)
        rec_h[hr].append(1.0 if (closes[i] > opens[i] and z[i] <= -0.75 and rsi[i] <= 40) else 0.0)

    out = {
        "symbol": "NG-USDT-SWAP",
        "timeframe": "15m",
        "source": "formal_ng_5m_candles_cache_resampled_15m_stdlib",
        "n_bars_15m": len(bars),
        "atr_30d": pack(atr_t),
        "range_30d": pack(br_t),
        "stop0p9_over_atr_med": stop_ratio,
        "frac_range_le": {
            str(k): round(sum(1 for x in br_t if x <= k) / float(len(br_t)), 3)
            for k in (0.9, 1.2, 1.5, 2.0, 2.5)
        },
        "rsi_le_pct": {
            str(t): round(100.0 * sum(1 for x in rsi_t if x <= t) / float(len(rsi_t)), 2)
            for t in (25, 30, 35, 40, 45)
        },
        "z_le_pct": {
            str(t): round(100.0 * sum(1 for x in z_t if x <= t) / float(len(z_t)), 2)
            for t in (-2.5, -2.0, -1.5, -1.25, -1.0, -0.75, -0.5)
        },
        "combo_freq_pct": combos,
        "adapt_bias": bias,
        "stop_noise_verdict_zh": verdict,
        "session_default_utc": [8, 16],
        "deep_oversold_pct_by_utc_hour": {int(h): round(100.0 * mean(v), 2) for h, v in deep_h.items()},
        "reclaim_proxy_pct_by_utc_hour": {int(h): round(100.0 * mean(v), 2) for h, v in rec_h.items()},
        "top_reclaim_hours_utc": sorted(rec_h.keys(), key=lambda h: -mean(rec_h[h]))[:8],
    }
    open(OUT, "w").write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print("WROTE", OUT)
    print(json.dumps({
        "stop0p9_over_atr_med": out["stop0p9_over_atr_med"],
        "adapt_bias": out["adapt_bias"],
        "atr_med": out["atr_30d"]["p"]["50"],
        "combo_freq_pct": out["combo_freq_pct"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
