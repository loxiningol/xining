# -*- coding: utf-8 -*-
"""Codex-authored deterministic neighborhood search for a CL 5m event strategy."""
from __future__ import print_function

import itertools
import json
import math
from pathlib import Path

import numpy as np

import auto_trade_strategy_ecosystem as ecosystem


SYMBOL = "CL-USDT-SWAP"
TIMEFRAME = "5m"
LEVERAGE = 20
COST = 2.0 * (0.0005 + 0.0002) * LEVERAGE


def next_true(mask):
    result = np.full(len(mask), len(mask), dtype=np.int64)
    nxt = len(mask)
    for index in range(len(mask)-1, -1, -1):
        if mask[index]: nxt = index
        result[index] = nxt
    return result


def simulate(frame, entry_mask, exit_next, direction, hold, stop):
    close = frame["close"].to_numpy(float)
    low = frame["low"].to_numpy(float)
    high = frame["high"].to_numpy(float)
    entries = np.flatnonzero(entry_mask)
    trades = []; blocked = -1; size = len(frame)
    for entry_index in entries:
        entry_index = int(entry_index)
        if entry_index <= blocked or entry_index + 1 >= size: continue
        terminal = min(size-1, entry_index+int(hold),
                       int(exit_next[entry_index+1]))
        entry_price = close[entry_index]
        stop_price = entry_price * (1.0-stop if direction == "long" else 1.0+stop)
        exit_index = terminal; stopped = False
        for index in range(entry_index+1, terminal+1):
            hit = low[index] <= stop_price if direction == "long" else high[index] >= stop_price
            if hit:
                exit_index = index; stopped = True; break
        exit_price = stop_price if stopped else close[exit_index]
        raw = ((exit_price-entry_price)/entry_price if direction == "long"
               else (entry_price-exit_price)/entry_price)
        net = raw*LEVERAGE-COST
        trades.append({"entry_index": entry_index, "exit_index": exit_index,
                       "entry_time": str(frame.index[entry_index]),
                       "exit_time": str(frame.index[exit_index]),
                       "pnl_ratio": float(net), "profit": bool(net > 0),
                       "stop_loss": stopped,
                       "exit_type": "止损" if stopped else
                                    ("定时强制平仓" if exit_index == entry_index+hold else "策略止盈")})
        blocked = exit_index
    return trades


def metrics(trades, observation_days):
    if not trades:
        return {"trades": 0, "win_rate_pct": 0.0, "expectancy_pct": 0.0,
                "holdout_trades": 0, "holdout_win_rate_pct": 0.0,
                "max_loss_streak": 0, "max_drawdown_pct": 0.0,
                "trades_per_day": 0.0}
    split = max(1, int(len(trades)*.7)); holdout = trades[split:]
    values = [row["pnl_ratio"]*100.0 for row in trades]
    capital = peak = 1.0; drawdown = 0.0; streak = max_streak = 0
    for row in trades:
        capital *= max(0.0, 1.0+row["pnl_ratio"]); peak = max(peak, capital)
        drawdown = max(drawdown, (peak-capital)/peak if peak else 1.0)
        streak = 0 if row["profit"] else streak+1; max_streak = max(max_streak, streak)
    return {"trades": len(trades),
            "win_rate_pct": sum(1 for row in trades if row["profit"])/float(len(trades))*100.0,
            "expectancy_pct": sum(values)/float(len(values)),
            "holdout_trades": len(holdout),
            "holdout_win_rate_pct": (sum(1 for row in holdout if row["profit"])/float(len(holdout))*100.0
                                      if holdout else 0.0),
            "max_loss_streak": max_streak, "max_drawdown_pct": drawdown*100.0,
            "trades_per_day": len(trades)/observation_days}


def leaf(identifier, feature, op, right_feature=None, value=None):
    right = {"feature": right_feature} if right_feature else {"value": value}
    return {"id": identifier, "left": {"feature": feature}, "op": op, "right": right}


def main():
    frame = ecosystem._load_research_frame(SYMBOL, TIMEFRAME)
    days = max(1.0, (frame.index[-1]-frame.index[0]).total_seconds()/86400.0)
    c = frame["close"].to_numpy(float); ema53 = frame["ema53"].to_numpy(float)
    slope = frame["h1_slope4"].to_numpy(float); macd = frame["macd_stick"].to_numpy(float)
    rsi = frame["rsi14"].to_numpy(float); k = frame["k"].to_numpy(float)
    d = frame["d"].to_numpy(float); j = frame["j"].to_numpy(float)
    z = frame["z20"].to_numpy(float)
    macd_up = (macd > 0) & (np.roll(macd, 1) <= 0); macd_up[0] = False
    macd_down = (macd < 0) & (np.roll(macd, 1) >= 0); macd_down[0] = False
    kd_down = (k < d) & (np.roll(k, 1) >= np.roll(d, 1)); kd_down[0] = False
    results = []
    # Round two deliberately searches only the informative neighbourhood from
    # round one.  Fine steps are used here; this is verification of a local
    # effect, not another broad parameter lottery.
    entry_grid = itertools.product(
        (.0020, .0025, .0030, .0035, .0040),
        (42.0, 45.0, 48.0, 51.0),
        (42.0, 46.0, 50.0, 54.0, 58.0),
        (.018, .022, .026, .030, .034, .038),
        (-99.0,),
    )
    exit_specs = []
    for threshold in (78.0, 82.0, 85.0, 88.0, 92.0):
        exit_specs.append(("j", threshold, next_true(j > threshold)))
        exit_specs.append(("j_or_kd", threshold, next_true((j > threshold) | kd_down)))
    exit_specs.append(("macd", 0.0, next_true(macd_down)))
    for slope_min, rsi_min, osc_max, macd_min, z_min in entry_grid:
        entry = (macd_up & (macd >= macd_min) & (slope > slope_min) &
                 (c > ema53) & (rsi > rsi_min) & (k < osc_max) &
                 (d < osc_max) & (z > z_min))
        if int(entry.sum()) < 15: continue
        for exit_name, exit_threshold, exit_next in exit_specs:
            for hold in (24, 30, 36, 42):
                run = {}
                for stop in (.009, .006):
                    rows = simulate(frame, entry, exit_next, "long", hold, stop)
                    run[str(stop)] = metrics(rows, days)
                primary = run["0.009"]; auxiliary = run["0.006"]
                gates = {
                    "minimum_sample": primary["trades"] >= 15,
                    "frequency": primary["trades_per_day"] >= .075,
                    "win_rate": primary["win_rate_pct"] >= 70.0,
                    "positive_both": primary["expectancy_pct"] > 0 and auxiliary["expectancy_pct"] > 0,
                    "holdout": primary["holdout_trades"] >= 2 and primary["holdout_win_rate_pct"] >= 55.0,
                    "loss_streak": primary["max_loss_streak"] <= 3,
                    "drawdown": primary["max_drawdown_pct"] <= 35.0,
                }
                score = (primary["win_rate_pct"] + primary["holdout_win_rate_pct"]*.5 +
                         primary["expectancy_pct"]*2 + auxiliary["expectancy_pct"] -
                         primary["max_loss_streak"]*2 - primary["max_drawdown_pct"]*.1)
                results.append({"params": {"slope_min": slope_min, "rsi_min": rsi_min,
                                             "osc_max": osc_max, "macd_min": macd_min,
                                             "z_min": z_min, "exit": exit_name,
                                             "exit_threshold": exit_threshold,
                                             "max_hold_bars": hold},
                                "runs": run, "gates": gates,
                                "passed": all(gates.values()), "score": score})
    results.sort(key=lambda row: (row["passed"], sum(row["gates"].values()), row["score"]), reverse=True)
    output = {"ok": True, "symbol": SYMBOL, "timeframe": TIMEFRAME,
              "tested": len(results), "passed_count": sum(1 for row in results if row["passed"]),
              "top": results[:30]}
    path = Path("/root/auto_trade/codex_cl5_strategy_search_round2.json")
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
