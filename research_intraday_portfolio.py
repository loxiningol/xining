# -*- coding: utf-8 -*-
"""Screen existing rules across instruments/timeframes with production costs."""

from __future__ import print_function

import argparse
import importlib
import json
import os
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end", default="2026-07-20 19:45")
    parser.add_argument("--leverage", type=int, default=20)
    parser.add_argument("--stop", type=float, default=0.006)
    parser.add_argument("--symbol", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    bt = importlib.import_module("backtest_engine_v2")
    bt.MARKET_DATA_ROOT = os.path.join(HERE, "market_data")
    bt.CONFIG_PATH = os.path.join(HERE, "experimental_strategies.json")
    bt.LOG_DIR = os.path.join(HERE, "research_logs")
    os.makedirs(bt.LOG_DIR, exist_ok=True)

    symbols = [
        "BTC-USDT-SWAP",
        "CL-USDT-SWAP",
        "XAU-USDT-SWAP",
        "NG-USDT-SWAP",
    ]
    if args.symbol:
        symbols = [args.symbol.strip().upper()]
    registry = bt.STRATEGIES_BY_TIMEFRAME[bt.normalize_timeframe(args.timeframe)]
    results = []
    for symbol in symbols:
        for key in sorted(registry):
            result = bt.run_backtest(
                key,
                instId=symbol,
                start_time=args.start,
                end_time=args.end,
                leverage=args.leverage,
                stop_loss_pct=args.stop,
                timeframe=args.timeframe,
            )
            if result.get("error"):
                print(json.dumps({
                    "symbol": symbol,
                    "strategy_key": key,
                    "error": result.get("error"),
                }, ensure_ascii=False, sort_keys=True), flush=True)
                continue
            trades = result.get("trades") or []
            wins = [t for t in trades if t.get("profit")]
            losses = [t for t in trades if not t.get("profit")]
            row = {
                "symbol": symbol,
                "timeframe": args.timeframe,
                "strategy_key": key,
                "trades": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": result.get("win_rate_percent"),
                "return_pct": result.get("total_return_percent"),
                "avg_trade_pct": (
                    sum(float(t.get("pnl_ratio") or 0) for t in trades)
                    / len(trades) * 100.0
                    if trades else 0.0
                ),
            }
            results.append(row)
            print(json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)

    results.sort(key=lambda item: (
        item["return_pct"], item["win_rate"], item["trades"]
    ), reverse=True)
    output = args.output or os.path.join(
        HERE, "research_intraday_portfolio.result.json"
    )
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print("OUTPUT=" + output)


if __name__ == "__main__":
    main()
