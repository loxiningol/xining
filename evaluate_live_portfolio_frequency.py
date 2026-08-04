# -*- coding: utf-8 -*-
"""Evaluate live strategy frequency after account-wide portfolio constraints."""

from __future__ import print_function

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import glob
import json
import os
import sys


ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backtest_engine_v2 as bt


START = os.environ.get("VECTOR_EVAL_START", "2026-03-15 00:00:00")
END = os.environ.get("VECTOR_EVAL_END", "2026-07-20 23:59:59")
PRIORITY = {"BTC-USDT-SWAP": 0, "CL-USDT-SWAP": 1,
            "XAU-USDT-SWAP": 2, "NG-USDT-SWAP": 3,
            "XAG-USDT-SWAP": 4, "LTC-USDT-SWAP": 5,
            "ADA-USDT-SWAP": 6}
TF_PRIORITY = {"5m": 0, "15m": 1, "1h": 2}


def read_json(path, default):
    try:
        with open(str(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def parse_time(value):
    text = str(value)
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            pass
    raise ValueError("unsupported trade time: %s" % text)


def active_assignments():
    rows = []
    for path in sorted(glob.glob(str(AUTO_DIR / "formal_daemon_config*.json"))):
        cfg = read_json(path, {})
        if not isinstance(cfg, dict):
            continue
        if not cfg.get("enabled") or not cfg.get("allow_auto_open"):
            continue
        symbol = str(cfg.get("symbol") or "").upper()
        timeframe = str(cfg.get("timeframe") or "1h").lower()
        keys = list(cfg.get("strategy_keys") or [])
        for key in keys:
            rows.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "strategy_key": key,
                "stop_loss_pct": float(cfg.get("stop_loss_pct", 0.009)),
                "full_position_ratio": float(cfg.get("full_position_ratio", 1.0)),
                "leverage": int(cfg.get("leverage", 20)),
                "config_file": path,
            })
    return rows


def backtest_assignments(assignments):
    candidates = []
    summaries = []
    for assignment in assignments:
        result = bt.run_backtest(
            assignment["strategy_key"],
            instId=assignment["symbol"],
            start_time=START,
            end_time=END,
            leverage=assignment["leverage"],
            stop_loss_pct=assignment["stop_loss_pct"],
            timeframe=assignment["timeframe"],
        )
        if result.get("error"):
            summaries.append(dict(assignment, error=result.get("error")))
            print(json.dumps(summaries[-1], ensure_ascii=False), flush=True)
            continue
        trades = result.get("trades") or []
        wins = sum(1 for trade in trades if trade.get("profit"))
        summary = dict(
            assignment,
            trades=len(trades),
            wins=wins,
            win_rate=(wins / float(len(trades)) * 100.0 if trades else 0.0),
            return_pct=float(result.get("total_return_percent") or 0.0),
        )
        summaries.append(summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        risk = (assignment["full_position_ratio"] * assignment["leverage"] *
                assignment["stop_loss_pct"])
        for trade in trades:
            candidates.append({
                "symbol": assignment["symbol"],
                "timeframe": assignment["timeframe"],
                "strategy_key": assignment["strategy_key"],
                "entry": parse_time(trade["entry_time"]),
                "exit": parse_time(trade["exit_time"]),
                "profit": bool(trade.get("profit")),
                "pnl_ratio": float(trade.get("pnl_ratio") or 0.0),
                "risk_ratio": risk,
            })
    return candidates, summaries


def simulate(candidates, policy):
    accepted = []
    rejected = Counter()
    active = []
    daily_entries = Counter()
    ordered = sorted(candidates, key=lambda item: (
        item["entry"], PRIORITY.get(item["symbol"], 99),
        TF_PRIORITY.get(item["timeframe"], 99), item["strategy_key"],
    ))
    max_positions = int(policy.get("max_open_positions", 3))
    max_daily = int(policy.get("max_daily_entries", 5))
    max_risk = float(policy.get("max_total_estimated_risk_ratio", 0.152))
    for trade in ordered:
        active = [row for row in active if row["exit"] > trade["entry"]]
        day = trade["entry"].date().isoformat()
        if any(row["symbol"] == trade["symbol"] for row in active):
            rejected["same_symbol_active"] += 1
            continue
        if len(active) >= max_positions:
            rejected["max_open_positions"] += 1
            continue
        if daily_entries[day] >= max_daily:
            rejected["max_daily_entries"] += 1
            continue
        combined_risk = sum(row["risk_ratio"] for row in active) + trade["risk_ratio"]
        if combined_risk > max_risk + 1e-12:
            rejected["max_total_estimated_risk"] += 1
            continue
        accepted.append(trade)
        active.append(trade)
        daily_entries[day] += 1
    return accepted, rejected


def period_metrics(trades, start, end):
    rows = [row for row in trades if start <= row["entry"] <= end]
    days = max(1, (end.date() - start.date()).days + 1)
    daily = Counter(row["entry"].date().isoformat() for row in rows)
    distribution = Counter(daily.get((start.date() + timedelta(days=i)).isoformat(), 0)
                           for i in range(days))
    wins = sum(1 for row in rows if row["profit"])
    return {
        "start": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end": end.strftime("%Y-%m-%d %H:%M:%S"),
        "calendar_days": days,
        "entries": len(rows),
        "entries_per_calendar_day": len(rows) / float(days),
        "win_rate": wins / float(len(rows)) * 100.0 if rows else 0.0,
        "days_with_3_or_more": sum(value for count, value in distribution.items() if count >= 3),
        "days_with_3_or_more_pct": sum(value for count, value in distribution.items() if count >= 3) / float(days) * 100.0,
        "daily_entry_distribution": dict(sorted(distribution.items())),
        "by_symbol": dict(Counter(row["symbol"] for row in rows)),
        "by_timeframe": dict(Counter(row["timeframe"] for row in rows)),
    }


def main():
    assignments = active_assignments()
    candidates, summaries = backtest_assignments(assignments)
    policy = read_json(AUTO_DIR / "portfolio_risk_policy.json", {})
    accepted, rejected = simulate(candidates, policy)
    end = parse_time(END)
    start = parse_time(START)
    periods = {
        "full": period_metrics(accepted, start, end),
        "last_90d": period_metrics(accepted, max(start, end - timedelta(days=89)), end),
        "last_60d": period_metrics(accepted, max(start, end - timedelta(days=59)), end),
        "last_30d": period_metrics(accepted, max(start, end - timedelta(days=29)), end),
    }
    output = {
        "ok": True,
        "method": "individual_backtests_then_portfolio_constraints",
        "policy": policy,
        "assignments": summaries,
        "raw_candidate_trades": len(candidates),
        "accepted_trades": len(accepted),
        "rejected": dict(rejected),
        "periods": periods,
        "accepted": [dict(row, entry=row["entry"].strftime("%Y-%m-%d %H:%M:%S"),
                          exit=row["exit"].strftime("%Y-%m-%d %H:%M:%S"))
                     for row in accepted],
    }
    output_path = Path(os.environ.get(
        "VECTOR_EVAL_OUTPUT", str(AUTO_DIR / "live_portfolio_frequency.json")
    ))
    with open(str(output_path), "w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2, default=str)
    print("RESULT=" + json.dumps({
        "raw_candidate_trades": len(candidates),
        "accepted_trades": len(accepted),
        "rejected": dict(rejected),
        "periods": periods,
        "output": str(output_path),
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
