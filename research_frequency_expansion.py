# -*- coding: utf-8 -*-
"""Validate a small set of auditable 5m frequency-expansion candidates."""

from __future__ import print_function

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
import json
import os
import sys

import pandas as pd


ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import optimize_intraday_rules as opt
import evaluate_live_portfolio_frequency as freq


START = pd.Timestamp("2026-03-15 00:00:00")
END = pd.Timestamp("2026-07-20 23:59:59")
DATA = {
    symbol: ROOT / "market_data" / symbol / "5m" /
    (symbol.replace("-", "_") + "_5m_OKX_LOCAL.parquet")
    for symbol in ("BTC-USDT-SWAP", "CL-USDT-SWAP", "XAU-USDT-SWAP", "NG-USDT-SWAP")
}
CANDIDATES = [
    {
        "key": "xau5_h1_breakdown_short_ai",
        "name": "XAU 5分钟顺势破位下行（AI创造）",
        "symbol": "XAU-USDT-SWAP", "family": "breakout", "side": "short",
        "params": {"atr_max": .015, "atr_min": .0008, "body": .35,
                   "cci": 90, "slope": 0.0},
        "tp": .009, "sl": .009, "max_hold": 48,
    },
    {
        "key": "cl5_exhaustion_fade_short_hf_ai",
        "name": "CL 5分钟冲高衰竭回落·高频版（AI创造）",
        "symbol": "CL-USDT-SWAP", "family": "exhaustion_reversal", "side": "short",
        "params": {"atr_max": .015, "atr_min": .0008, "rsi": 40,
                   "slope": 0.0, "slope_limit": .006, "z": 1.6},
        "tp": .009, "sl": .009, "max_hold": 24,
    },
    {
        "key": "ng5_exhaustion_reclaim_long_hf_ai",
        "name": "NG 5分钟超跌衰竭收回（AI创造）",
        "symbol": "NG-USDT-SWAP", "family": "exhaustion_reversal", "side": "long",
        "params": {"atr_max": .004, "atr_min": .0008, "rsi": 35,
                   "slope": 0.0, "slope_limit": .006, "z": 1.25},
        "tp": .009, "sl": .009, "max_hold": 72,
    },
]


def parse_optimizer_trade(row, candidate):
    return {
        "symbol": candidate["symbol"],
        "timeframe": "5m",
        "strategy_key": candidate["key"],
        "entry": row[0].to_pydatetime(),
        "exit": row[1].to_pydatetime(),
        "profit": bool(row[2] * 20 > 0),
        "pnl_ratio": float(row[2] * 20),
        "risk_ratio": .15 * 20 * candidate["sl"],
    }


def max_consecutive_losses(trades):
    current = maximum = 0
    for row in trades:
        if row[2] * 20 <= 0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


def segment_metrics(trades, start, end):
    rows = [row for row in trades if start <= row[0] <= end]
    result = opt.metrics(rows)
    result["max_consecutive_losses"] = max_consecutive_losses(rows)
    result["entries_per_day"] = len(rows) / float(max(1, (end.date() - start.date()).days + 1))
    return result


def candidate_report(candidate):
    frame = pd.read_parquet(str(DATA[candidate["symbol"]])).sort_index()
    frame = opt.indicators(frame)
    mask = opt.signal_mask(frame, candidate["family"], candidate["side"], candidate["params"])
    trades = opt.simulate(frame, mask, candidate["side"], START, END,
                          candidate["tp"], candidate["sl"], candidate["max_hold"])
    segments = {}
    cursor = START
    while cursor <= END:
        seg_end = min(END, cursor + pd.offsets.MonthEnd(0) + pd.Timedelta(hours=23, minutes=59))
        segments[cursor.strftime("%Y-%m")] = segment_metrics(trades, cursor, seg_end)
        cursor = (cursor + pd.offsets.MonthBegin(1)).normalize()
    return trades, {
        "key": candidate["key"], "name": candidate["name"],
        "symbol": candidate["symbol"], "side": candidate["side"],
        "family": candidate["family"], "params": candidate["params"],
        "tp": candidate["tp"], "sl": candidate["sl"],
        "max_hold": candidate["max_hold"],
        "full": segment_metrics(trades, START, END),
        "last_60d": segment_metrics(trades, max(START, END-pd.Timedelta(days=59)), END),
        "last_30d": segment_metrics(trades, max(START, END-pd.Timedelta(days=29)), END),
        "monthly": segments,
    }


def load_current_accepted():
    data = json.load(open(str(ROOT / "auto_trade" / "live_portfolio_frequency.json"),
                          "r", encoding="utf-8"))
    rows = []
    for row in data.get("accepted") or []:
        item = dict(row)
        item["entry"] = freq.parse_time(item["entry"])
        item["exit"] = freq.parse_time(item["exit"])
        rows.append(item)
    return rows


def main():
    all_candidate_trades = []
    reports = []
    for candidate in CANDIDATES:
        trades, report = candidate_report(candidate)
        reports.append(report)
        all_candidate_trades.extend(parse_optimizer_trade(row, candidate) for row in trades)
        print(json.dumps(report, ensure_ascii=False, default=str), flush=True)

    current = load_current_accepted()
    policy = json.load(open(str(ROOT / "auto_trade" / "portfolio_risk_policy.json"),
                            "r", encoding="utf-8"))
    combined, rejected = freq.simulate(current + all_candidate_trades, policy)
    start_dt = START.to_pydatetime()
    end_dt = END.to_pydatetime()
    periods = {
        "full": freq.period_metrics(combined, start_dt, end_dt),
        "last_90d": freq.period_metrics(combined, max(start_dt, end_dt-timedelta(days=89)), end_dt),
        "last_60d": freq.period_metrics(combined, max(start_dt, end_dt-timedelta(days=59)), end_dt),
        "last_30d": freq.period_metrics(combined, max(start_dt, end_dt-timedelta(days=29)), end_dt),
    }
    result = {
        "ok": True,
        "candidates": reports,
        "candidate_trade_count": len(all_candidate_trades),
        "current_accepted_count": len(current),
        "combined_accepted_count": len(combined),
        "combined_rejected": dict(rejected),
        "periods": periods,
        "accepted_by_strategy": dict(Counter(row["strategy_key"] for row in combined)),
    }
    output = ROOT / "auto_trade" / "frequency_expansion_research.json"
    with open(str(output), "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, default=str)
    print("RESULT=" + json.dumps(result, ensure_ascii=False, default=str), flush=True)


if __name__ == "__main__":
    main()
