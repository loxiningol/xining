# -*- coding: utf-8 -*-
"""BTC 15m — Donchian breakout + EMA trend filter.

Creation outcome: REJECTED for delivery / review submit.
Short-window WR can look green; research ~47d window WR collapses and
return-hardness fails. Live path remains dry-run.
"""
from __future__ import print_function

import json
import os
from datetime import datetime


def load_params(path=None):
    here = os.path.dirname(os.path.abspath(__file__))
    path = path or os.path.join(here, "params.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ema(series, period):
    out = [None] * len(series)
    k = 2.0 / (period + 1.0)
    s = None
    for i, v in enumerate(series):
        if s is None:
            if i + 1 < period:
                continue
            s = sum(series[i - period + 1: i + 1]) / float(period)
            out[i] = s
            continue
        s = v * k + s * (1.0 - k)
        out[i] = s
    return out


def atr_pct(candles, period=14):
    tr = [None] * len(candles)
    for i, c in enumerate(candles):
        if i == 0:
            tr[i] = (c["high"] - c["low"]) / c["close"]
        else:
            pc = candles[i - 1]["close"]
            tr[i] = max(c["high"] - c["low"], abs(c["high"] - pc), abs(c["low"] - pc)) / c["close"]
    out = [None] * len(candles)
    for i in range(len(candles)):
        if i < period - 1:
            continue
        window = tr[i - period + 1: i + 1]
        if any(x is None for x in window):
            continue
        out[i] = sum(window) / float(period)
    return out


def load_candle_file(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    rows = raw.get("candles") if isinstance(raw, dict) else raw
    out = []
    for c in rows:
        out.append({
            "ts": int(c["ts"]),
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
        })
    return out


def run_backtest(candle_key="candle_15m_research", params=None, dir_path=None):
    params = params or load_params()
    dir_path = dir_path or os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(dir_path, params["backtest"][candle_key])
    candles = load_candle_file(path)
    don = int(params["entry"]["donchian_n"])
    ema_p = int(params["entry"]["ema_trend"])
    hold = int(params["exit"]["time_stop_bars"])
    tp_r = float(params["exit"]["take_profit_r"])
    slip = float(params["slippage_pct"])
    fee = float(params["taker_fee_pct"])
    sl = float(params["protective_sl_pct"])
    lev = float(params["leverage"])
    risk = float(params["position_sizing"]["risk_fraction_of_equity"])
    max_m = float(params["position_sizing"]["max_equity_fraction"])
    abandon_q = float(params["entry"].get("abandon_atr_quantile") or 0.85)

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    e = ema(closes, ema_p)
    atrs = atr_pct(candles, int(params["position_sizing"]["atr_period"]))
    atr_hist = [x for x in atrs if x is not None]
    thr = sorted(atr_hist)[int(abandon_q * (len(atr_hist) - 1))]

    eq = float(params["backtest"]["initial_equity"])
    peak = eq
    mdd = 0.0
    trades = []
    day_counts = {}
    i = max(don, ema_p) + 2
    while i < len(candles) - 2:
        if e[i] is None or atrs[i] is None:
            i += 1
            continue
        if atrs[i] >= thr:
            i += 1
            continue
        hh = max(highs[i - don: i])
        ll = min(lows[i - don: i])
        c = closes[i]
        side = 0
        if c > hh and c > e[i]:
            side = 1
        elif c < ll and c < e[i]:
            side = -1
        if not side:
            i += 1
            continue
        ts = candles[i]["ts"]
        t = ts // 1000 if ts > 1e12 else ts
        day = datetime.utcfromtimestamp(t).strftime("%Y-%m-%d")
        if day_counts.get(day, 0) >= int(params["frequency"]["max_trades_per_day"]):
            i += 1
            continue
        risk_cash = eq * risk
        notional = risk_cash / max(sl, atrs[i])
        margin = notional / lev
        margin = max(eq * 0.01, min(eq * max_m, margin))
        notional = margin * lev
        entry_i = i + 1
        entry = candles[entry_i]["open"] * (1 + slip if side > 0 else 1 - slip)
        stop = entry * (1 - sl) if side > 0 else entry * (1 + sl)
        tp = entry * (1 + sl * tp_r) if side > 0 else entry * (1 - sl * tp_r)
        eq -= notional * fee
        exit_i = min(entry_i + hold, len(candles) - 1)
        exit_px = candles[exit_i]["close"]
        reason = "time"
        for j in range(entry_i, exit_i + 1):
            lo = candles[j]["low"]
            hi = candles[j]["high"]
            if side > 0 and lo <= stop:
                exit_px = stop * (1 - slip)
                exit_i = j
                reason = "sl"
                break
            if side > 0 and hi >= tp:
                exit_px = tp * (1 - slip)
                exit_i = j
                reason = "tp"
                break
            if side < 0 and hi >= stop:
                exit_px = stop * (1 + slip)
                exit_i = j
                reason = "sl"
                break
            if side < 0 and lo <= tp:
                exit_px = tp * (1 + slip)
                exit_i = j
                reason = "tp"
                break
        ret = (exit_px / entry - 1) - fee if side > 0 else (entry / exit_px - 1) - fee
        pnl = notional * ret
        eq += pnl
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        trades.append({
            "side": "long" if side > 0 else "short",
            "ret": ret,
            "pnl": pnl,
            "reason": reason,
            "entry_ts": candles[entry_i]["ts"],
            "exit_ts": candles[exit_i]["ts"],
        })
        day_counts[day] = day_counts.get(day, 0) + 1
        i = exit_i + 1

    rets = [t["ret"] for t in trades]
    wr = (sum(1 for r in rets if r > 0) / float(len(rets))) if rets else None
    return {
        "ok": False,
        "present_to_human": False,
        "strategy_id": params.get("strategy_id"),
        "candle_key": candle_key,
        "n_bars": len(candles),
        "n_trades": len(trades),
        "win_rate": wr,
        "total_return": eq / float(params["backtest"]["initial_equity"]) - 1.0,
        "max_drawdown": mdd,
        "final_equity": eq,
        "note_zh": "创造门禁未过；禁止展示为交付，禁止提交复核。",
    }


if __name__ == "__main__":
    print(json.dumps({
        "research": run_backtest("candle_15m_research"),
        "formal_short": run_backtest("candle_15m_formal_short"),
    }, ensure_ascii=False, indent=2))
