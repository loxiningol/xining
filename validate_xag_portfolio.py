# -*- coding: utf-8 -*-
"""Evaluate the selected XAG 5m candidate inside the live portfolio policy."""
from __future__ import print_function

from datetime import timedelta
import json
from pathlib import Path

import pandas as pd

import evaluate_live_portfolio_frequency as freq
import optimize_intraday_rules as opt


ROOT = Path("/root")
START = pd.Timestamp("2026-03-15 00:00:00")
END = pd.Timestamp("2026-07-20 23:59:59")
CANDIDATE = {
    "key": "xag5_session_breakdown_short_ai",
    "name": "XAG 5分钟时段顺势破位（AI创造）",
    "symbol": "XAG-USDT-SWAP",
    "family": "breakout",
    "side": "short",
    "params": {"atr_max": 0.004, "atr_min": 0.0008,
               "body": 0.6, "cci": 50, "slope": 0.0005},
    "utc_hour_start": 0,
    "utc_hour_end": 8,
    "tp": 0.006,
    "sl": 0.009,
    "max_hold": 72,
    "full_position_ratio": 0.15,
}


def main():
    path = ROOT / "market_data/XAG-USDT-SWAP/5m/XAG_USDT_SWAP_5m_OKX_LOCAL.parquet"
    frame = opt.indicators(pd.read_parquet(str(path)).sort_index())
    mask = opt.signal_mask(frame, CANDIDATE["family"], CANDIDATE["side"],
                           CANDIDATE["params"])
    hour = frame.index.hour.to_numpy()
    mask &= (hour >= CANDIDATE["utc_hour_start"]) & (hour < CANDIDATE["utc_hour_end"])
    trades = opt.simulate(frame, mask, CANDIDATE["side"], START, END,
                          CANDIDATE["tp"], CANDIDATE["sl"],
                          CANDIDATE["max_hold"])
    candidate_rows = [{
        "symbol": CANDIDATE["symbol"], "timeframe": "5m",
        "strategy_key": CANDIDATE["key"],
        "entry": row[0].to_pydatetime(), "exit": row[1].to_pydatetime(),
        "profit": row[2] > 0, "pnl_ratio": float(row[2] * 20),
        "risk_ratio": CANDIDATE["full_position_ratio"] * 20 * CANDIDATE["sl"],
    } for row in trades]
    current_data = json.load(open(str(ROOT / "auto_trade/live_portfolio_frequency.json"),
                                  "r", encoding="utf-8"))
    current = []
    for row in current_data.get("accepted", []):
        item = dict(row)
        item["entry"] = freq.parse_time(item["entry"])
        item["exit"] = freq.parse_time(item["exit"])
        current.append(item)
    policy = json.load(open(str(ROOT / "auto_trade/portfolio_risk_policy.json"),
                            "r", encoding="utf-8"))
    combined, rejected = freq.simulate(current + candidate_rows, policy)
    start, end = START.to_pydatetime(), END.to_pydatetime()
    periods = {}
    for name, days in (("full", 127), ("last_90d", 89),
                       ("last_60d", 59), ("last_30d", 29)):
        period_start = start if name == "full" else max(start, end-timedelta(days=days))
        periods[name] = freq.period_metrics(combined, period_start, end)
    result = {"candidate": CANDIDATE, "candidate_trades": len(candidate_rows),
              "candidate_wins": sum(1 for x in candidate_rows if x["profit"]),
              "combined_accepted": len(combined), "rejected": dict(rejected),
              "periods": periods}
    output = ROOT / "auto_trade/xag_portfolio_validation.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                      encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
