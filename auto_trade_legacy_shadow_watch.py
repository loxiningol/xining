# -*- coding: utf-8 -*-
"""Read-only forward shadow watch for evidence-insufficient legacy strategies.

No exchange order, cancel or amend API is imported or called.  The watcher
replays the unchanged strategy on the growing local closed-candle archive,
keeps only trades whose entries occur after the prospective start, and
reprices the identical path with the exact triple-actual friction model.
"""
from __future__ import print_function

from datetime import datetime
from pathlib import Path
import json
import math
import os
import tempfile

import auto_trade_live_strategy_auditor as auditor
import auto_trade_shadow_validator as shadow
import backtest_engine_v2 as engine


ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
PLAN_PATH = AUTO_DIR / "legacy_shadow_plans.json"
STATUS_PATH = AUTO_DIR / "legacy_shadow_status.json"
BEIJING_FMT = "%Y-%m-%d %H:%M:%S"


def _now():
    return datetime.now().strftime(BEIJING_FMT)


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _atomic(path, payload):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        delete=False, dir=str(path.parent), mode="w", encoding="utf-8")
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush(); handle.close(); Path(handle.name).replace(path)
    except Exception:
        try:
            Path(handle.name).unlink()
        except Exception:
            pass
        raise


def _dt(value):
    text = str(value)
    for pattern in (BEIJING_FMT, "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            pass
    raise ValueError("unsupported Beijing timestamp: %s" % text)


def _metrics(trades, seed):
    values = [float(row.get("pnl_ratio") or 0.0) for row in trades]
    cap = peak = 1.0; drawdown = 0.0
    for value in values:
        cap *= max(0.0, 1.0+value); peak = max(peak, cap)
        drawdown = max(drawdown, (peak-cap)/peak if peak else 1.0)
    posterior = (auditor.ecosystem._probabilistic_net_summary(
        values, seed_material=seed, bootstrap_samples=2000) if values else {})
    wins = sum(value > 0 for value in values)
    gross_win = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value <= 0))
    return {
        "closed_trades": len(values),
        "win_rate_pct": round(wins/float(len(values))*100.0, 6) if values else None,
        "mean_net_return_pct": round(sum(values)/len(values)*100.0, 6) if values else None,
        "compounded_full_margin_return_pct": round((cap-1.0)*100.0, 6),
        "max_drawdown_pct": round(drawdown*100.0, 6),
        "max_loss_streak": shadow._loss_streak(values),
        "profit_factor": round(gross_win/gross_loss, 6) if gross_loss else None,
        "probability_mean_positive": posterior.get("probability_mean_positive"),
    }


def _run_plan(plan):
    assignment_id = plan["assignment_id"]
    assignments = {row["assignment_id"]: row
                   for row in auditor.discover_live_assignments()}
    row = assignments.get(assignment_id)
    if not row:
        return {"assignment_id": assignment_id, "state": "assignment_missing",
                "passed": False}
    manifest = auditor._manifest(row); bounds = auditor._data_bounds(
        row["symbol"], row["timeframe"])
    kwargs = {
        "strategy_name": row["strategy_key"], "instId": row["symbol"],
        "start_time": bounds["audit_start_beijing"],
        "end_time": bounds["audit_end_beijing"],
        "leverage": int(manifest["execution"]["leverage"]),
        "stop_loss_pct": float(manifest["execution"]["stop_loss_pct"]),
        "account_position_ratio": 1.0, "timeframe": row["timeframe"],
    }
    base = engine.run_backtest(friction_scenario="observed_base", **kwargs)
    triple = auditor._derive_triple_actual(base, row["symbol"], kwargs["leverage"])
    started_at = _dt(plan["started_at"])
    forward = [trade for trade in (triple.get("trades") or [])
               if _dt(trade.get("entry_time")) >= started_at]
    elapsed_days = max(0.0, (datetime.now()-started_at).total_seconds()/86400.0)
    metrics = _metrics(forward, assignment_id+"|prospective")
    micro = []
    for trade in forward:
        stamp = int(_dt(trade["entry_time"]).timestamp()*1000.0)
        micro.append(shadow._micro_snapshot(row["symbol"], stamp))
    observed_micro = sum(bool(item.get("available")) for item in micro)
    minimum_days = float(plan.get("minimum_days") or 180)
    minimum_trades = int(plan.get("minimum_closed_trades") or 20)
    enough = elapsed_days >= minimum_days and len(forward) >= minimum_trades
    gates = {
        "minimum_chronological_days": elapsed_days >= minimum_days,
        "minimum_closed_trades": len(forward) >= minimum_trades,
        "triple_cost_mean_positive": float(metrics.get("mean_net_return_pct") or 0) > 0,
        "triple_cost_compound_positive": float(metrics.get(
            "compounded_full_margin_return_pct") or 0) > 0,
        "probability_mean_positive_at_least_0_80": float(metrics.get(
            "probability_mean_positive") or 0) >= .80,
        "profit_factor_at_least_1_20": float(metrics.get("profit_factor") or 0) >= 1.20,
        "max_drawdown_at_most_35_pct": float(metrics.get("max_drawdown_pct") or 100) <= 35,
        "max_loss_streak_at_most_3": int(metrics.get("max_loss_streak") or 0) <= 3,
        "micro_snapshot_coverage_at_least_80_pct": (
            observed_micro/float(len(micro)) >= .80 if micro else False),
    }
    passed = bool(enough and all(gates.values()))
    return {
        "assignment_id": assignment_id,
        "classification": "evidence_insufficient_not_logically_dead",
        "state": ("shadow_passed_manual_reaudit_required" if passed else
                  ("shadow_failed" if enough else "collecting_forward_evidence")),
        "passed": passed, "automatic_live_restoration": False,
        "new_entries_allowed": False, "started_at": plan["started_at"],
        "elapsed_days": round(elapsed_days, 4),
        "minimum_days": minimum_days, "minimum_closed_trades": minimum_trades,
        "metrics": metrics, "gates": gates,
        "micro_snapshot_observed_entries": observed_micro,
        "execution_boundary": (
            "read_only exact-strategy closed-candle replay; no order endpoint; "
            "no fabricated pre-start L2/queue/cancel history"),
        "next_step_if_passed": "fresh three-AI review plus Codex/manual approval",
    }


def run():
    plans = _read(PLAN_PATH, {}).get("plans") or []
    results = []
    for plan in plans:
        if plan.get("enabled"):
            try:
                results.append(_run_plan(plan))
            except Exception as exc:
                results.append({"assignment_id": plan.get("assignment_id"),
                                "state": "shadow_error_fail_closed",
                                "passed": False, "error": str(exc),
                                "automatic_live_restoration": False})
    payload = {"ok": True, "updated_at": _now(), "read_only": True,
               "automatic_live_restoration": False, "results": results}
    _atomic(STATUS_PATH, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return payload


if __name__ == "__main__":
    run()
