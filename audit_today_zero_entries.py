# -*- coding: utf-8 -*-
"""Read-only authoritative replay of every live assignment for the local day."""
from __future__ import print_function

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path("/root")
AUTO_DIR = ROOT / "auto_trade"


def read_json(path, default):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value
    except Exception:
        return default


def one_assignment(symbol, timeframe, include_exchange=False):
    os.environ["VECTOR_TRADE_SYMBOL"] = symbol
    os.environ["VECTOR_TRADE_TIMEFRAME"] = timeframe
    import auto_trade_formal_daemon as daemon
    import auto_trade_strategy_ema6_center_down as strategy
    import auto_trade_strategy_rating as rating
    import auto_trade_portfolio_risk as risk

    cfg = daemon.get_config(write_back=False)
    loaded = strategy.load_closed_candles()
    output = {
        "symbol": symbol,
        "timeframe": timeframe,
        "enabled": bool(cfg.get("enabled")),
        "allow_auto_open": bool(cfg.get("allow_auto_open")),
        "formal_authorized": bool(cfg.get("formal_auto_trading_authorized")),
        "active_strategy_keys": daemon._active_strategy_keys(cfg),
        "candle": {key: loaded.get(key) for key in (
            "ok", "source", "fresh", "latest_candle_ts",
            "expected_latest_candle_ts", "cache_age_sec", "error")},
        "strategies": [],
        "risk": risk.local_risk_snapshot(),
    }
    runtime_path = AUTO_DIR / ("formal_daemon_runtime%s.json" % daemon.INSTANCE_SUFFIX)
    runtime = read_json(runtime_path, {})
    output["runtime_action"] = runtime.get("action")
    output["runtime_updated_at"] = runtime.get("updated_at")
    if not loaded.get("ok"):
        return output

    context = strategy._frame_context(loaded["candles"])
    df = context["df"]
    now = time.localtime()
    day_start_ms = int(time.mktime((now.tm_year, now.tm_mon, now.tm_mday,
                                    0, 0, 0, 0, 0, -1)) * 1000)
    indices = [index for index, stamp in enumerate(df.index)
               if int(stamp.timestamp() * 1000) >= day_start_ms]
    output["closed_bars_replayed_today"] = len(indices)
    o, c, h, low = context["open"], context["close"], context["high"], context["low"]
    kwargs = context["kwargs"]
    registry = strategy.bt.STRATEGIES_BY_TIMEFRAME[strategy.TIMEFRAME]
    for key in output["active_strategy_keys"]:
        entry_f, _exit_f, direction = registry[key]
        params = strategy.bt.load_strategy_params(key)
        hits = []
        for index in indices:
            try:
                met, info = entry_f(o, c, h, low, index, params, **kwargs)
            except Exception as exc:
                output["strategies"].append({
                    "strategy_key": key, "error": str(exc), "trigger_count_today": 0})
                hits = None
                break
            if met:
                hits.append({
                    "candle_ts": int(df.index[index].timestamp() * 1000),
                    "candle_time": str(df.index[index]),
                    "entry_info": info or {},
                })
        if hits is None:
            continue
        live_rating = rating.rating_for(symbol, timeframe, key, refresh_if_stale=False) or {}
        signal_file = (strategy.STRATEGIES.get(key) or {}).get("signal_file")
        latest_signal = read_json(signal_file, {}) if signal_file else {}
        diagnosis = latest_signal.get("not_triggered_diagnosis") or {}
        output["strategies"].append({
            "strategy_key": key,
            "strategy_name": (strategy.STRATEGIES.get(key) or {}).get("name") or key,
            "direction": direction,
            "trigger_count_today": len(hits),
            "triggers": hits,
            "latest_live_signal": latest_signal.get("signal"),
            "latest_signal_time": latest_signal.get("time"),
            "latest_reason": diagnosis.get("reason_code"),
            "latest_single_parameter_blockers": diagnosis.get("single_parameter_blockers") or [],
            "latest_indicators": diagnosis.get("indicator_snapshot") or {},
            "rating": {name: live_rating.get(name) for name in (
                "grade", "expected_win_rate_pct", "expected_return_per_trade_pct",
                "recommended_action", "sample_size")},
        })
    if include_exchange:
        import auto_trade_formal_v6_executor as executor
        output["exchange_positions"] = executor._account_wide_open_positions()
        output["exchange_pending_orders"] = executor._account_wide_pending_orders()
    return output


def all_assignments():
    assignments = []
    for path in sorted(glob.glob(str(AUTO_DIR / "formal_daemon_config*.json"))):
        cfg = read_json(path, {})
        symbol = str(cfg.get("symbol") or "").upper()
        timeframe = str(cfg.get("timeframe") or cfg.get("bar") or "1h").lower()
        if symbol and timeframe in ("1h", "15m", "5m"):
            token = (symbol, timeframe)
            if token not in assignments:
                assignments.append(token)
    rows = []
    for index, (symbol, timeframe) in enumerate(assignments):
        env = dict(os.environ)
        env["PYTHONPATH"] = "/root"
        command = [sys.executable, str(Path(__file__)), "--one",
                   "--symbol", symbol, "--timeframe", timeframe]
        if index == 0:
            command.append("--exchange")
        completed = subprocess.run(command, env=env, universal_newlines=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   timeout=180, check=False)
        if completed.returncode == 0:
            detail = json.loads(completed.stdout)
            rows.append({
                "symbol": detail.get("symbol"),
                "timeframe": detail.get("timeframe"),
                "enabled": detail.get("enabled"),
                "allow_auto_open": detail.get("allow_auto_open"),
                "formal_authorized": detail.get("formal_authorized"),
                "runtime_action": detail.get("runtime_action"),
                "runtime_updated_at": detail.get("runtime_updated_at"),
                "candle": detail.get("candle"),
                "closed_bars_replayed_today": detail.get("closed_bars_replayed_today"),
                "risk": detail.get("risk"),
                "exchange_positions": detail.get("exchange_positions"),
                "exchange_pending_orders": detail.get("exchange_pending_orders"),
                "strategies": [{
                    "strategy_key": item.get("strategy_key"),
                    "strategy_name": item.get("strategy_name"),
                    "trigger_count_today": item.get("trigger_count_today"),
                    "trigger_times": [hit.get("candle_time") for hit in item.get("triggers") or []],
                    "latest_live_signal": item.get("latest_live_signal"),
                    "latest_reason": item.get("latest_reason"),
                    "rating": item.get("rating"),
                    "error": item.get("error"),
                } for item in detail.get("strategies") or []],
            })
        else:
            rows.append({"symbol": symbol, "timeframe": timeframe,
                         "audit_error": completed.stderr.strip(),
                         "returncode": completed.returncode})
    return {"ok": all("audit_error" not in row for row in rows),
            "date": time.strftime("%Y-%m-%d"), "assignments": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--one", action="store_true")
    parser.add_argument("--exchange", action="store_true")
    parser.add_argument("--symbol")
    parser.add_argument("--timeframe")
    args = parser.parse_args()
    output = (one_assignment(args.symbol, args.timeframe, args.exchange)
              if args.one else all_assignments())
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
