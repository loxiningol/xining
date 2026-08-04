# -*- coding: utf-8 -*-
"""Search an XAG 5m replacement that fills 0-2 entry portfolio days.

The score is deliberately portfolio-aware: trades on days already at the
five-entry cap receive no frequency reward. Chronological train/validation/
test slices and conservative stop-first same-candle handling are retained.
"""
from collections import Counter
from datetime import datetime, timedelta
from itertools import product
from pathlib import Path
import json

import numpy as np
import pandas as pd

import backtest_engine_v2 as bt
import evaluate_live_portfolio_frequency as freq


ROOT = Path("/root")
START = pd.Timestamp("2026-03-15 00:00:00")
TRAIN_END = pd.Timestamp("2026-05-15 23:59:59")
VALID_START = pd.Timestamp("2026-05-16 00:00:00")
VALID_END = pd.Timestamp("2026-06-20 23:59:59")
TEST_START = pd.Timestamp("2026-06-21 00:00:00")
END = pd.Timestamp("2026-07-20 23:59:59")
DATA = ROOT / "market_data/XAG-USDT-SWAP/5m/XAG_USDT_SWAP_5m_OKX_LOCAL.parquet"
SESSIONS = ((0, 8), (0, 10), (0, 12), (2, 10), (4, 12),
            (8, 16), (12, 20), (16, 24), (0, 24))


def simulate(mask, close, high, low, index, tp, hold, sl=.009):
    trades = []
    i = 80
    n = len(close)
    while i < n:
        if not mask[i]:
            i += 1
            continue
        entry_i = i
        entry = float(close[i])
        target = entry * (1.0 - tp)
        stop = entry * (1.0 + sl)
        i += 1
        exit_i = None
        exit_price = None
        while i < n:
            # Same-candle ambiguity is resolved against the strategy.
            if float(high[i]) >= stop:
                exit_i, exit_price = i, stop
                break
            if float(low[i]) <= target:
                exit_i, exit_price = i, target
                break
            if i - entry_i >= hold:
                exit_i, exit_price = i, float(close[i])
                break
            i += 1
        if exit_i is None:
            break
        raw = (entry - exit_price) / entry
        net = raw * 20.0 - .028
        trades.append((index[entry_i], index[exit_i], net))
        i = exit_i + 1
    return trades


def metrics(trades, start, end):
    rows = [row for row in trades if start <= row[0] <= end]
    returns = np.asarray([row[2] * .15 for row in rows], dtype=float)
    if not rows:
        return {"trades": 0, "win_rate": 0.0, "avg": 0.0,
                "max_drawdown": 100.0, "max_loss_cluster": 99}
    equity = np.cumprod(1.0 + returns)
    path = np.r_[1.0, equity]
    peak = np.maximum.accumulate(path)
    cluster = maximum = 0
    for value in returns:
        cluster = 0 if value > 0 else cluster + 1
        maximum = max(maximum, cluster)
    return {
        "trades": len(rows),
        "win_rate": float(np.mean(returns > 0) * 100.0),
        "avg": float(np.mean(returns) * 100.0),
        "max_drawdown": float(np.max(1.0 - path / peak) * 100.0),
        "max_loss_cluster": maximum,
    }


def as_portfolio_rows(trades):
    return [{"symbol": "XAG-USDT-SWAP", "timeframe": "5m",
             "strategy_key": "xag5_session_breakdown_short_ai",
             "entry": a.to_pydatetime(), "exit": b.to_pydatetime(),
             "profit": value > 0, "pnl_ratio": float(value),
             "risk_ratio": .15 * 20 * .009}
            for a, b, value in trades]


def main():
    frame = pd.read_parquet(str(DATA)).sort_index()
    frame = bt.precompute_indicators(frame[["open", "high", "low", "close"]],
                                     timeframe="5m")
    frame = frame.replace([np.inf, -np.inf], np.nan).ffill().bfill()
    within = np.asarray((frame.index >= START) & (frame.index <= END))
    close = frame["close"].to_numpy(float)
    open_ = frame["open"].to_numpy(float)
    high = frame["high"].to_numpy(float)
    low = frame["low"].to_numpy(float)
    atr = frame["atr14"].to_numpy(float)
    cci = frame["cci"].to_numpy(float)
    h1e19 = frame["h1_ema19"].to_numpy(float)
    h1e53 = frame["h1_ema53"].to_numpy(float)
    slope = frame["h1_slope4"].to_numpy(float)
    prev_low = frame["prev_low20"].to_numpy(float)
    atr_pct = atr / np.maximum(close, 1e-12)
    body_atr = np.abs(close - open_) / np.maximum(atr, 1e-12)
    hour = frame.index.hour.to_numpy()

    live = json.load(open(str(ROOT / "auto_trade/live_portfolio_frequency.json"),
                          "r", encoding="utf-8"))
    current = []
    for row in live.get("accepted") or []:
        if row.get("strategy_key") == "xag5_session_breakdown_short_ai":
            continue
        item = dict(row)
        item["entry"] = freq.parse_time(item["entry"])
        item["exit"] = freq.parse_time(item["exit"])
        current.append(item)
    policy = json.load(open(str(ROOT / "auto_trade/portfolio_risk_policy.json"),
                            "r", encoding="utf-8"))

    rows = []
    for session, slope_min, body_min, cci_min, atr_max, tp, hold in product(
        SESSIONS, (.0003, .0005, .0007), (.4, .5, .6), (40, 50, 60),
        (.004, .006), (.006, .009), (48, 72)
    ):
        hs, he = session
        session_ok = (hour >= hs) & (hour < he)
        mask = (within & session_ok & (h1e19 < h1e53)
                & (slope < -slope_min) & (close < prev_low)
                & (close < open_) & (body_atr >= body_min)
                & (cci <= -cci_min) & (atr_pct >= .0008)
                & (atr_pct <= atr_max))
        trades = simulate(mask, close, high, low, frame.index, tp, hold)
        train = metrics(trades, START, TRAIN_END)
        valid = metrics(trades, VALID_START, VALID_END)
        test = metrics(trades, TEST_START, END)
        full = metrics(trades, START, END)
        if min(train["trades"], valid["trades"], test["trades"]) < 8:
            continue
        if min(train["avg"], valid["avg"], test["avg"]) <= 0:
            continue
        if min(train["win_rate"], valid["win_rate"], test["win_rate"]) < 65:
            continue
        if full["win_rate"] < 72 or full["max_drawdown"] > 18:
            continue
        if full["max_loss_cluster"] > 4:
            continue
        combined, rejected = freq.simulate(current + as_portfolio_rows(trades), policy)
        p30 = freq.period_metrics(combined, TEST_START.to_pydatetime(),
                                  END.to_pydatetime())
        pfull = freq.period_metrics(combined, START.to_pydatetime(),
                                    END.to_pydatetime())
        score = (p30["entries_per_calendar_day"] * 30
                 + p30["days_with_3_or_more_pct"] * .25
                 + min(train["win_rate"], valid["win_rate"], test["win_rate"]) * .2
                 + full["avg"] * 20 - full["max_drawdown"] * .5)
        rows.append({"session": session, "h1_slope_min": slope_min,
                     "body_atr_min": body_min, "cci_abs_min": cci_min,
                     "atr_pct_min": .0008, "atr_pct_max": atr_max,
                     "take_profit_price_ratio": tp, "max_hold_bars": hold,
                     "train": train, "validation": valid, "test": test,
                     "full": full, "portfolio_last30": p30,
                     "portfolio_full": pfull,
                     "portfolio_rejected": dict(rejected), "score": score})
    rows.sort(key=lambda row: row["score"], reverse=True)
    path = ROOT / "auto_trade/xag_deficit_fill_candidates.json"
    path.write_text(json.dumps(rows[:100], ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(json.dumps({"qualified": len(rows), "top": rows[:10]},
                     ensure_ascii=False), flush=True)
    print("OUTPUT=" + str(path), flush=True)


if __name__ == "__main__":
    main()
