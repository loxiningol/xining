# -*- coding: utf-8 -*-
"""Screen classic strategy variants; only print WR>=50% survivors."""
from __future__ import print_function

import json
import math
import os
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRATCH = os.path.join(ROOT, "strategies", "_scratch")
PKG = os.path.join(ROOT, "strategies", "ada5m_mom_vol_filter_v1")

SLIP = 0.0005
FEE = 0.0005
COST = SLIP + FEE
SL_PCT = 0.009
MARGIN_FRAC = 0.05
LEV = 20.0


def load_candles(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        rows = raw.get("candles") or raw.get("data") or raw.get("rows") or []
    else:
        rows = raw
    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append({
                "ts": int(r.get("ts") or r.get("timestamp")),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
            })
        elif isinstance(r, (list, tuple)) and len(r) >= 5:
            out.append({
                "ts": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
            })
    out.sort(key=lambda x: x["ts"])
    return out


def ema(closes, period):
    out = [None] * len(closes)
    k = 2.0 / (period + 1.0)
    s = None
    for i, c in enumerate(closes):
        if s is None:
            if i + 1 < period:
                continue
            s = sum(closes[i - period + 1: i + 1]) / float(period)
            out[i] = s
            continue
        s = c * k + s * (1.0 - k)
        out[i] = s
    return out


def sma(closes, period):
    out = [None] * len(closes)
    for i in range(len(closes)):
        if i + 1 < period:
            continue
        out[i] = sum(closes[i - period + 1: i + 1]) / float(period)
    return out


def atr(candles, period=14):
    out = [None] * len(candles)
    trs = []
    for i, c in enumerate(candles):
        if i == 0:
            tr = c["high"] - c["low"]
        else:
            pc = candles[i - 1]["close"]
            tr = max(c["high"] - c["low"], abs(c["high"] - pc), abs(c["low"] - pc))
        trs.append(tr)
        if i + 1 < period:
            continue
        out[i] = sum(trs[i - period + 1: i + 1]) / float(period)
    return out


def rsi(closes, period=14):
    out = [None] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
        if i < period:
            out[i] = None
            continue
        ag = sum(gains[i - period: i]) / float(period)
        al = sum(losses[i - period: i]) / float(period)
        if al < 1e-12:
            out[i] = 100.0
        else:
            rs = ag / al
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def bb(closes, period=20, k=2.0):
    mid, up, lo, width = [None] * len(closes), [None] * len(closes), [None] * len(closes), [None] * len(closes)
    for i in range(len(closes)):
        if i + 1 < period:
            continue
        w = closes[i - period + 1: i + 1]
        m = sum(w) / float(period)
        sd = (sum((x - m) ** 2 for x in w) / float(period)) ** 0.5
        mid[i] = m
        up[i] = m + k * sd
        lo[i] = m - k * sd
        width[i] = (2 * k * sd) / m if m else None
    return mid, up, lo, width


def simulate(candles, signals, hold_bars=12):
    """signals[i] in {-1,0,1} decided on bar i close; enter next open."""
    eq = 10000.0
    peak = eq
    max_dd = 0.0
    trades = []
    i = 0
    n = len(candles)
    while i < n - 2:
        sig = signals[i] if i < len(signals) else 0
        if not sig:
            i += 1
            continue
        entry_i = i + 1
        entry = float(candles[entry_i]["open"])
        side = 1 if sig > 0 else -1
        stop = entry * (1.0 - SL_PCT) if side > 0 else entry * (1.0 + SL_PCT)
        exit_i = min(entry_i + hold_bars, n - 1)
        exit_px = float(candles[exit_i]["close"])
        hit_sl = False
        for j in range(entry_i, exit_i + 1):
            lo = float(candles[j]["low"])
            hi = float(candles[j]["high"])
            if side > 0 and lo <= stop:
                exit_px = stop
                exit_i = j
                hit_sl = True
                break
            if side < 0 and hi >= stop:
                exit_px = stop
                exit_i = j
                hit_sl = True
                break
        gross = (exit_px - entry) / entry * side
        net = gross - 2.0 * COST
        # margin/leverage notional return on equity slice
        ret = net * LEV * MARGIN_FRAC
        eq *= (1.0 + ret)
        peak = max(peak, eq)
        dd = (eq - peak) / peak
        max_dd = min(max_dd, dd)
        trades.append({
            "ret": ret,
            "net": net,
            "win": ret > 0,
            "entry_ts": candles[entry_i]["ts"],
            "exit_ts": candles[exit_i]["ts"],
            "hit_sl": hit_sl,
        })
        i = exit_i + 1
    if not trades:
        return None
    rets = [t["ret"] for t in trades]
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    wr = len(wins) / float(len(trades))
    avg_w = (sum(t["ret"] for t in wins) / len(wins)) if wins else 0.0
    avg_l = (sum(t["ret"] for t in losses) / len(losses)) if losses else 0.0
    payoff = (avg_w / abs(avg_l)) if avg_l != 0 else None
    first_ts = candles[0]["ts"]
    last_ts = candles[-1]["ts"]
    a = first_ts // 1000 if first_ts > 1e12 else first_ts
    b = last_ts // 1000 if last_ts > 1e12 else last_ts
    days = max((b - a) / 86400.0, 1e-9)
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / max(len(rets) - 1, 1)
    std = math.sqrt(var) if var > 0 else 0.0
    # crude ann sharpe on trade returns * trades/year
    tpy = len(trades) / days * 365.0
    sharpe = (mean / std * math.sqrt(tpy)) if std > 1e-12 else 0.0
    return {
        "n_trades": len(trades),
        "win_rate": wr,
        "total_return": eq / 10000.0 - 1.0,
        "max_drawdown": max_dd,
        "payoff": payoff,
        "avg_trades_per_day": len(trades) / days,
        "sharpe_ann_proxy": sharpe,
        "span_days": days,
        "first_iso": datetime.utcfromtimestamp(a).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "last_iso": datetime.utcfromtimestamp(b).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


def sig_donchian(candles, n=20, ema_p=144):
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    e = ema(closes, ema_p)
    sig = [0] * len(candles)
    for i in range(len(candles)):
        if i < max(n, ema_p) or e[i] is None:
            continue
        hh = max(highs[i - n: i])  # prior n bars, no look-ahead
        ll = min(lows[i - n: i])
        c = closes[i]
        if c > hh and c > e[i]:
            sig[i] = 1
        elif c < ll and c < e[i]:
            sig[i] = -1
    return sig


def sig_dual_ma(candles, fast=10, slow=40):
    closes = [c["close"] for c in candles]
    f = ema(closes, fast)
    s = ema(closes, slow)
    at = atr(candles, 14)
    sig = [0] * len(candles)
    for i in range(1, len(candles)):
        if f[i] is None or s[i] is None or f[i - 1] is None or s[i - 1] is None or at[i] is None:
            continue
        # crossover + ATR expansion vs recent
        atr_prev = at[i - 5] if i >= 5 and at[i - 5] else None
        expanding = atr_prev is not None and at[i] > atr_prev * 1.05
        if f[i - 1] <= s[i - 1] and f[i] > s[i] and expanding:
            sig[i] = 1
        elif f[i - 1] >= s[i - 1] and f[i] < s[i] and expanding:
            sig[i] = -1
    return sig


def sig_bb_mr(candles, period=20):
    closes = [c["close"] for c in candles]
    mid, up, lo, width = bb(closes, period)
    # abandon high width quantile
    widths = [w for w in width if w is not None]
    q80 = sorted(widths)[int(0.8 * (len(widths) - 1))] if widths else 1e9
    sig = [0] * len(candles)
    for i in range(len(candles)):
        if mid[i] is None or width[i] is None:
            continue
        if width[i] >= q80:
            continue
        c = closes[i]
        if c < lo[i]:
            sig[i] = 1
        elif c > up[i]:
            sig[i] = -1
    return sig


def sig_rsi(candles, period=14, lo=30, hi=70, ema_p=144):
    closes = [c["close"] for c in candles]
    r = rsi(closes, period)
    e = ema(closes, ema_p)
    sig = [0] * len(candles)
    for i in range(len(candles)):
        if r[i] is None or e[i] is None:
            continue
        if r[i] < lo and closes[i] >= e[i] * 0.98:
            sig[i] = 1
        elif r[i] > hi and closes[i] <= e[i] * 1.02:
            sig[i] = -1
    return sig


def sig_vol_squeeze(candles, period=20, n=20):
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    mid, up, lo, width = bb(closes, period)
    widths = [w for w in width if w is not None]
    q20 = sorted(widths)[int(0.2 * (len(widths) - 1))] if widths else 0
    q80 = sorted(widths)[int(0.8 * (len(widths) - 1))] if widths else 1e9
    sig = [0] * len(candles)
    squeezed = False
    for i in range(len(candles)):
        if width[i] is None or i < n:
            continue
        if width[i] <= q20:
            squeezed = True
        if width[i] >= q80:
            squeezed = False
            continue
        if not squeezed:
            continue
        hh = max(highs[i - n: i])
        ll = min(lows[i - n: i])
        c = closes[i]
        if c > hh:
            sig[i] = 1
            squeezed = False
        elif c < ll:
            sig[i] = -1
            squeezed = False
    return sig


VARIANTS = [
    ("classic_donchian_breakout", sig_donchian, {"n": 20, "ema_p": 72}),
    ("classic_donchian_n30", lambda c: sig_donchian(c, n=30, ema_p=72), {}),
    ("classic_dual_ma_trend", sig_dual_ma, {"fast": 8, "slow": 34}),
    ("classic_dual_ma_10_40", lambda c: sig_dual_ma(c, 10, 40), {}),
    ("classic_bollinger_mean_reversion", sig_bb_mr, {"period": 20}),
    ("classic_bb_mr_28", lambda c: sig_bb_mr(c, 28), {}),
    ("classic_rsi_reversion", sig_rsi, {"period": 14, "lo": 28, "hi": 72}),
    ("classic_rsi_22_78", lambda c: sig_rsi(c, 14, 22, 78, 72), {}),
    ("classic_vol_squeeze_breakout", sig_vol_squeeze, {"period": 20, "n": 20}),
    ("classic_vol_squeeze_n12", lambda c: sig_vol_squeeze(c, 20, 12), {}),
]


def main():
    caches = []
    for name in os.listdir(SCRATCH):
        if name.startswith("formal_") and name.endswith("_candles_cache.json"):
            caches.append(os.path.join(SCRATCH, name))
    # also package ada cache
    p = os.path.join(PKG, "formal_ada_5m_candles_cache.json")
    if os.path.isfile(p):
        caches.append(p)
    # de-dupe
    caches = sorted(set(caches))
    survivors = []
    rejected = []
    for path in caches:
        candles = load_candles(path)
        if len(candles) < 200:
            continue
        label = os.path.basename(path)
        for vid, fn, kwargs in VARIANTS:
            try:
                if kwargs:
                    # only call with kwargs if fn accepts them via name in VARIANTS first forms
                    if fn in (sig_donchian, sig_dual_ma, sig_bb_mr, sig_rsi, sig_vol_squeeze):
                        sig = fn(candles, **kwargs)
                    else:
                        sig = fn(candles)
                else:
                    sig = fn(candles)
            except TypeError:
                sig = fn(candles)
            for hold in (8, 12, 24):
                st = simulate(candles, sig, hold_bars=hold)
                if not st:
                    continue
                row = {
                    "cache": label,
                    "variant": vid,
                    "hold_bars": hold,
                    **st,
                }
                if st["win_rate"] >= 0.50 and st["n_trades"] >= 10:
                    survivors.append(row)
                else:
                    rejected.append(row)
    survivors.sort(key=lambda r: (r["win_rate"], r["total_return"], r["sharpe_ann_proxy"]), reverse=True)
    out = {
        "ok": True,
        "present_to_human": bool(survivors),
        "n_survivors_wr50": len(survivors),
        "n_rejected": len(rejected),
        "survivors": survivors[:20],
        "best_rejected_near": sorted(
            rejected,
            key=lambda r: (r.get("win_rate") or 0, r.get("total_return") or -9),
            reverse=True,
        )[:10],
        "note_zh": (
            "仅列出初评胜率≥50%且成交≥10笔的经典变式；否则不展示为交付。"
            if survivors else
            "本轮短窗经典变式无一达到胜率≥50%且样本≥10；禁止交付，需更长历史或继续换方向。"
        ),
    }
    out_path = os.path.join(ROOT, "strategies", "_scratch", "classic_variant_wr50_screen.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("WROTE", out_path)
    print("SURVIVORS", len(survivors))
    for s in survivors[:8]:
        print(
            "OK", s["cache"], s["variant"], "hold", s["hold_bars"],
            "WR", round(s["win_rate"], 3),
            "ret", round(s["total_return"] * 100, 3), "%",
            "n", s["n_trades"],
            "days", round(s["span_days"], 2),
            s["first_iso"], "->", s["last_iso"],
        )
    if not survivors:
        print("NONE_PRESENTABLE")
        for s in out["best_rejected_near"][:5]:
            print(
                "NEAR", s["cache"], s["variant"],
                "WR", round(s["win_rate"], 3),
                "ret", round(s["total_return"] * 100, 3), "%",
                "n", s["n_trades"],
            )


if __name__ == "__main__":
    main()
