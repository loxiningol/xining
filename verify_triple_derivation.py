# -*- coding: utf-8 -*-
"""One-off production equivalence check for exact triple-cost repricing."""
from __future__ import print_function

import json
import os
import tempfile
from pathlib import Path

import auto_trade_live_strategy_auditor as auditor
import backtest_engine_v2 as engine


OUTPUT = Path(os.environ.get(
    "TRIPLE_VERIFY_OUTPUT",
    "/root/auto_trade/triple_derivation_verification.json"))


def main():
    symbol = "XAU-USDT-SWAP"; timeframe = "15m"
    strategy = "xau15_h1_breakout_long_ai"
    bounds = auditor._data_bounds(symbol, timeframe)
    kwargs = {
        "strategy_name": strategy, "instId": symbol,
        "start_time": bounds["audit_start_beijing"],
        "end_time": bounds["audit_end_beijing"],
        "leverage": 20, "stop_loss_pct": .009,
        "account_position_ratio": .10, "timeframe": timeframe,
    }
    base = engine.run_backtest(friction_scenario="observed_base", **kwargs)
    native = engine.run_backtest(friction_scenario="triple_actual", **kwargs)
    derived = auditor._derive_triple_actual(base, symbol, 20)
    left = [float(row.get("pnl_ratio")) for row in native.get("trades") or []]
    right = [float(row.get("pnl_ratio")) for row in derived.get("trades") or []]
    deltas = [abs(a-b) for a, b in zip(left, right)]
    result = {
        "ok": bool(len(left) == len(right) and deltas
                   and max(deltas) <= 1e-12),
        "native_trades": len(left), "repriced_trades": len(right),
        "max_abs_pnl_delta": max(deltas) if deltas else None,
        "native_mean_pct": sum(left)/len(left)*100.0 if left else None,
        "repriced_mean_pct": sum(right)/len(right)*100.0 if right else None,
        "native_friction": ((native.get("consistency_audit") or {})
                            .get("friction_model") or {}),
        "repriced_friction": ((derived.get("consistency_audit") or {})
                              .get("friction_model") or {}),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        delete=False, dir=str(OUTPUT.parent), mode="w", encoding="utf-8")
    json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.flush(); handle.close(); Path(handle.name).replace(OUTPUT)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
