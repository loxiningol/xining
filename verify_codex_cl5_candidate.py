# -*- coding: utf-8 -*-
"""Codex final-review diagnostics for the CL 5m candidate (research only)."""
from __future__ import print_function

import json
from collections import defaultdict

import numpy as np

import auto_trade_strategy_ecosystem as ecosystem
import research_codex_cl5_strategy as search


def summarize(rows):
    if not rows:
        return {"trades": 0, "win_rate_pct": 0.0, "expectancy_pct": 0.0,
                "max_loss_streak": 0}
    streak = maximum = 0
    for row in rows:
        streak = 0 if row["profit"] else streak + 1
        maximum = max(maximum, streak)
    return {
        "trades": len(rows),
        "win_rate_pct": sum(row["profit"] for row in rows) / len(rows) * 100.0,
        "expectancy_pct": sum(row["pnl_ratio"] for row in rows) / len(rows) * 100.0,
        "max_loss_streak": maximum,
    }


def main():
    frame = ecosystem._load_research_frame(search.SYMBOL, search.TIMEFRAME)
    close = frame["close"].to_numpy(float)
    ema53 = frame["ema53"].to_numpy(float)
    slope = frame["h1_slope4"].to_numpy(float)
    macd = frame["macd_stick"].to_numpy(float)
    rsi = frame["rsi14"].to_numpy(float)
    k = frame["k"].to_numpy(float)
    d = frame["d"].to_numpy(float)
    j = frame["j"].to_numpy(float)
    macd_up = (macd > 0) & (np.roll(macd, 1) <= 0)
    macd_up[0] = False
    exit_next = search.next_true(j > 82.0)
    result = []
    for slope_min in (.0032, .0035, .0038):
        for osc_max in (55.0, 58.0, 61.0):
            for macd_min in (.034, .038, .042):
                entry = (macd_up & (macd >= macd_min) & (slope > slope_min) &
                         (close > ema53) & (rsi > 42.0) &
                         (k < osc_max) & (d < osc_max))
                rows = search.simulate(frame, entry, exit_next, "long", 24, .009)
                result.append({"params": {"slope_min": slope_min,
                                            "osc_max": osc_max,
                                            "macd_min": macd_min},
                               "metrics": summarize(rows)})

    center_entry = (macd_up & (macd >= .038) & (slope > .0035) &
                    (close > ema53) & (rsi > 42.0) & (k < 58.0) & (d < 58.0))
    center_rows = search.simulate(frame, center_entry, exit_next, "long", 24, .009)
    start, end = frame.index[0], frame.index[-1]
    span = end - start
    boundaries = [start, start + span / 3, start + span * 2 / 3, end]
    thirds = []
    for index in range(3):
        lower, upper = boundaries[index], boundaries[index + 1]
        rows = [row for row in center_rows
                if lower <= frame.index[row["entry_index"]]
                and (frame.index[row["entry_index"]] < upper or index == 2)]
        thirds.append({"from": str(lower), "to": str(upper), **summarize(rows)})

    months = defaultdict(list)
    for row in center_rows:
        months[str(frame.index[row["entry_index"]])[:7]].append(row)
    monthly = [{"month": month, **summarize(rows)}
               for month, rows in sorted(months.items())]
    output = {
        "period": {"from": str(start), "to": str(end)},
        "center": summarize(center_rows),
        "time_thirds": thirds,
        "monthly": monthly,
        "neighbourhood": result,
        "trades": center_rows,
    }
    path = "/root/auto_trade/codex_cl5_candidate_verification.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2)
    print(json.dumps({key: output[key] for key in
                      ("period", "center", "time_thirds", "monthly", "neighbourhood")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
