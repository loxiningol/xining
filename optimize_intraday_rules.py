# -*- coding: utf-8 -*-
"""Walk-forward search for simple, auditable 15-minute intraday rules."""

from __future__ import print_function

import argparse
import itertools
import json
import os

import numpy as np
import pandas as pd


HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILES = {
    "BTC-USDT-SWAP": "BTC_USDT_SWAP_15m_OKX_LOCAL.parquet",
    "CL-USDT-SWAP": "CL_USDT_SWAP_15m_OKX_LOCAL.parquet",
    "XAU-USDT-SWAP": "XAU_USDT_SWAP_15m_OKX_LOCAL.parquet",
    "NG-USDT-SWAP": "NG_USDT_SWAP_15m_OKX_LOCAL.parquet",
    "ETH-USDT-SWAP": "ETH_USDT_SWAP_15m_OKX_LOCAL.parquet",
    "SOL-USDT-SWAP": "SOL_USDT_SWAP_15m_OKX_LOCAL.parquet",
    "XAG-USDT-SWAP": "XAG_USDT_SWAP_15m_OKX_LOCAL.parquet",
    "XRP-USDT-SWAP": "XRP_USDT_SWAP_15m_OKX_LOCAL.parquet",
    "DOGE-USDT-SWAP": "DOGE_USDT_SWAP_15m_OKX_LOCAL.parquet",
    "LTC-USDT-SWAP": "LTC_USDT_SWAP_15m_OKX_LOCAL.parquet",
    "ADA-USDT-SWAP": "ADA_USDT_SWAP_15m_OKX_LOCAL.parquet",
    "AVAX-USDT-SWAP": "AVAX_USDT_SWAP_15m_OKX_LOCAL.parquet",
    "SUI-USDT-SWAP": "SUI_USDT_SWAP_15m_OKX_LOCAL.parquet",
    "LINK-USDT-SWAP": "LINK_USDT_SWAP_15m_OKX_LOCAL.parquet",
}


def indicators(df):
    out = df.copy()
    o = out["open"].astype(float)
    h = out["high"].astype(float)
    l = out["low"].astype(float)
    c = out["close"].astype(float)
    for span in (6, 19, 53, 75):
        out["ema%d" % span] = c.ewm(span=span, adjust=False).mean()
    delta = c.diff()
    up = delta.clip(lower=0).ewm(alpha=1.0 / 14.0, adjust=False).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1.0 / 14.0, adjust=False).mean()
    out["rsi"] = 100.0 - 100.0 / (1.0 + up / down.replace(0, np.nan))
    tp = (h + l + c) / 3.0
    ma = tp.rolling(62).mean()
    md = tp.rolling(62).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    out["cci"] = (tp - ma) / (0.015 * md)
    ll = l.rolling(24).min()
    hh = h.rolling(24).max()
    rsv = ((c - ll) / (hh - ll) * 100.0).replace([np.inf, -np.inf], np.nan).fillna(50)
    out["k"] = rsv.ewm(alpha=1.0 / 3.0, adjust=False).mean()
    out["d"] = out["k"].ewm(alpha=1.0 / 3.0, adjust=False).mean()
    out["j"] = 3.0 * out["k"] - 2.0 * out["d"]
    prev_c = c.shift(1)
    tr = pd.concat([h-l, (h-prev_c).abs(), (l-prev_c).abs()], axis=1).max(axis=1)
    out["atr"] = tr.rolling(14).mean()
    out["atr_pct"] = out["atr"] / c
    mean20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    out["z20"] = (c - mean20) / std20.replace(0, np.nan)
    out["prev_high"] = h.shift(1).rolling(20).max()
    out["prev_low"] = l.shift(1).rolling(20).min()

    hourly = out[["open", "high", "low", "close"]].resample(
        "1h", label="left", closed="left"
    ).agg({"open":"first", "high":"max", "low":"min", "close":"last"}).dropna()
    hc = hourly["close"].astype(float)
    ctx = pd.DataFrame(index=hourly.index)
    ctx["h1_ema19"] = hc.ewm(span=19, adjust=False).mean()
    ctx["h1_ema53"] = hc.ewm(span=53, adjust=False).mean()
    ctx["h1_slope"] = ctx["h1_ema19"].pct_change(4)
    # Shift to prevent the current incomplete hour leaking into a 15m signal.
    ctx = ctx.shift(1).reindex(out.index, method="ffill")
    return out.join(ctx)


def signal_mask(df, family, side, p):
    o, h, l, c = (df[x] for x in ("open", "high", "low", "close"))
    bullish = c > o
    bearish = c < o
    trend_up = (df.h1_ema19 > df.h1_ema53) & (df.h1_slope > p["slope"])
    trend_down = (df.h1_ema19 < df.h1_ema53) & (df.h1_slope < -p["slope"])
    k_cross_up = (df.k > df.d) & (df.k.shift(1) <= df.d.shift(1))
    k_cross_down = (df.k < df.d) & (df.k.shift(1) >= df.d.shift(1))
    atr_ok = (df.atr_pct >= p["atr_min"]) & (df.atr_pct <= p["atr_max"])

    if family == "trend_pullback":
        if side == "long":
            mask = (trend_up & (df.ema19 > df.ema53) &
                    (l <= df.ema19 * (1 + p["touch"])) &
                    (c >= df.ema6) & bullish & k_cross_up &
                    (df.k <= p["osc"]))
        else:
            mask = (trend_down & (df.ema19 < df.ema53) &
                    (h >= df.ema19 * (1 - p["touch"])) &
                    (c <= df.ema6) & bearish & k_cross_down &
                    (df.k >= 100 - p["osc"]))
    elif family == "exhaustion_reversal":
        if side == "long":
            mask = ((df.z20 <= -p["z"]) & (df.rsi <= p["rsi"]) &
                    bullish & (df.k > df.k.shift(1)) &
                    (df.h1_slope >= -p["slope_limit"]))
        else:
            mask = ((df.z20 >= p["z"]) & (df.rsi >= 100-p["rsi"]) &
                    bearish & (df.k < df.k.shift(1)) &
                    (df.h1_slope <= p["slope_limit"]))
    elif family == "breakout":
        body = (c-o).abs() / df.atr
        if side == "long":
            mask = (trend_up & (c > df.prev_high) & bullish &
                    (body >= p["body"]) & (df.cci >= p["cci"]))
        else:
            mask = (trend_down & (c < df.prev_low) & bearish &
                    (body >= p["body"]) & (df.cci <= -p["cci"]))
    else:
        raise ValueError(family)
    return (mask & atr_ok).fillna(False).to_numpy(dtype=bool)


def simulate(df, mask, side, start, end, tp, sl, max_hold, cost=0.0014):
    o = df.open.to_numpy(float)
    h = df.high.to_numpy(float)
    l = df.low.to_numpy(float)
    c = df.close.to_numpy(float)
    timestamps = df.index
    allowed = ((timestamps >= pd.Timestamp(start)) & (timestamps <= pd.Timestamp(end)))
    indices = np.flatnonzero(mask & allowed)
    trades = []
    blocked_until = -1
    n = len(df)
    for i in indices:
        if i <= blocked_until or i + 1 >= n:
            continue
        entry = c[i]
        exit_i = min(n-1, i+max_hold)
        raw = (c[exit_i]-entry)/entry if side == "long" else (entry-c[exit_i])/entry
        reason = "timeout"
        for j in range(i+1, min(n, i+max_hold+1)):
            if side == "long":
                hit_sl = l[j] <= entry*(1-sl)
                hit_tp = h[j] >= entry*(1+tp)
            else:
                hit_sl = h[j] >= entry*(1+sl)
                hit_tp = l[j] <= entry*(1-tp)
            # Conservative same-candle ordering: stop is assumed first.
            if hit_sl:
                exit_i, raw, reason = j, -sl, "stop"
                break
            if hit_tp:
                exit_i, raw, reason = j, tp, "take_profit"
                break
        net = raw - cost
        trades.append((timestamps[i], timestamps[exit_i], net, reason))
        blocked_until = exit_i
    return trades


def metrics(trades, leverage=20):
    if not trades:
        return {"trades":0, "win_rate":0.0, "return_pct":0.0,
                "avg_pct":0.0, "max_drawdown_pct":0.0}
    returns = np.array([t[2]*leverage for t in trades], dtype=float)
    equity = np.cumprod(np.maximum(0.0, 1.0+returns))
    peak = np.maximum.accumulate(np.r_[1.0, equity])
    dd = 1.0 - np.r_[1.0, equity] / peak
    return {
        "trades": len(trades),
        "win_rate": float(np.mean(returns > 0)*100.0),
        "return_pct": float((equity[-1]-1.0)*100.0),
        "avg_pct": float(np.mean(returns)*100.0),
        "max_drawdown_pct": float(np.max(dd)*100.0),
    }


def grids(timeframe="15m"):
    common = {
        "atr_min": ([0.0003, 0.0005, 0.0008] if timeframe == "5m" else [0.0008, 0.0012, 0.0018]),
        "atr_max": ([0.004, 0.008, 0.015] if timeframe == "5m" else [0.008, 0.015, 0.03]),
    }
    for family in ("trend_pullback", "exhaustion_reversal", "breakout"):
        if family == "trend_pullback":
            specific = {"slope":[0.0, .0005, .001], "touch":[0.0,.0015], "osc":[45,55,65]}
        elif family == "exhaustion_reversal":
            specific = {"slope":[0.0], "z":[1.25,1.6,2.0], "rsi":[25,30,35,40],
                        "slope_limit":[.001,.003,.006]}
        else:
            specific = {"slope":[0.0,.0005,.001], "body":[.35,.6,.9], "cci":[50,90,130]}
        fields = dict(common)
        fields.update(specific)
        keys = sorted(fields)
        for values in itertools.product(*(fields[k] for k in keys)):
            yield family, dict(zip(keys, values))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--timeframe", choices=("15m", "5m"), default="15m")
    ap.add_argument("--output", default="")
    args = ap.parse_args()
    symbol = args.symbol.upper()
    filename = DATA_FILES[symbol].replace("_15m_", "_%s_" % args.timeframe)
    path = os.path.join(HERE, "data", filename)
    if not os.path.exists(path):
        path = os.path.join(
            HERE, "market_data", symbol, args.timeframe, filename
        )
    df = indicators(pd.read_parquet(path).sort_index())
    if args.timeframe == "5m" and symbol in ("CL-USDT-SWAP", "NG-USDT-SWAP"):
        train_start, split = pd.Timestamp("2026-03-15"), pd.Timestamp("2026-06-15")
    elif args.timeframe == "5m":
        train_start, split = pd.Timestamp("2025-12-15"), pd.Timestamp("2026-06-01")
    elif symbol in ("CL-USDT-SWAP", "NG-USDT-SWAP"):
        train_start, split = pd.Timestamp("2026-03-15"), pd.Timestamp("2026-06-01")
    else:
        train_start, split = pd.Timestamp("2025-01-01"), pd.Timestamp("2026-04-01")
    end = pd.Timestamp("2026-07-20 11:45")
    rows = []
    for family, params in grids(args.timeframe):
        for side in ("long", "short"):
            mask = signal_mask(df, family, side, params)
            if args.timeframe == "5m" and os.environ.get("VECTOR_FAST_ROBUST") == "1":
                exit_grid = itertools.product((.006,.009), (.006,.009), (48,72))
            else:
                exit_grid = (
                    itertools.product((.0025,.004,.006,.009), (.003,.006,.009), (12,24,48,72))
                    if args.timeframe == "5m"
                    else itertools.product((.004,.006,.009,.012), (.006,.009), (8,16,24,32))
                )
            for tp, sl, hold in exit_grid:
                train = simulate(df, mask, side, train_start, split-pd.Timedelta(minutes=15), tp, sl, hold)
                valid = simulate(df, mask, side, split, end, tp, sl, hold)
                mt, mv = metrics(train), metrics(valid)
                if mt["trades"] < 12 or mv["trades"] < 5:
                    continue
                # Ranking rewards validation expectancy and repeatability, not raw compounded return.
                score = (mv["avg_pct"] * min(mv["trades"],40) +
                         mt["avg_pct"] * min(mt["trades"],40) * .35 -
                         abs(mt["win_rate"]-mv["win_rate"]) * .15 -
                         mv["max_drawdown_pct"] * .08)
                rows.append({
                    "symbol":symbol, "timeframe":args.timeframe,
                    "family":family, "side":side,
                    "params":params, "tp":tp, "sl":sl, "max_hold":hold,
                    "train":mt, "validation":mv, "score":float(score),
                })
    rows.sort(key=lambda x:x["score"], reverse=True)
    output = args.output or os.path.join(HERE,"optimized_%s.json" % symbol.split("-")[0])
    with open(output,"w",encoding="utf-8") as handle:
        json.dump(rows[:300],handle,ensure_ascii=False,indent=2)
    for row in rows[:20]:
        print(json.dumps(row,ensure_ascii=False,sort_keys=True))
    print("OUTPUT="+output)


if __name__ == "__main__":
    main()
