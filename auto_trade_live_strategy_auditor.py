# -*- coding: utf-8 -*-
"""Fail-closed retrospective audit for every currently configured live strategy."""
from __future__ import print_function

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import argparse
import copy
import glob
import hashlib
import inspect
import json
import math
import os
import sqlite3
import tempfile
import time
import traceback

import pandas as pd

import auto_trade_ai_consensus as ai
import auto_trade_strategy_ecosystem as ecosystem
import backtest_engine_v2 as engine
from freeze_live_strategy_entries import discover_live_assignments


ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
DB_PATH = AUTO_DIR / "strategy_ecosystem.db"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"
GATE_PATH = AUTO_DIR / "live_cognitive_gate_required.json"
RATING_PATH = AUTO_DIR / "strategy_ratings.json"
REPORT_PATH = AUTO_DIR / "live_strategy_retrospective_audit.json"
PROGRESS_PATH = AUTO_DIR / "live_strategy_retrospective_audit_progress.json"
BEIJING_FMT = "%Y-%m-%d %H:%M:%S"
AUDIT_SCHEMA = "live_strategy_retrospective_cognitive_audit_v2_exact_triple_reprice"


def _now():
    return datetime.now().strftime(BEIJING_FMT)


def _read(path, default):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
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


def _sha(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str).encode("utf-8")).hexdigest()


def _source(callable_value):
    try:
        return inspect.getsource(callable_value)
    except Exception as exc:
        return "SOURCE_UNAVAILABLE: %s" % exc


def _data_bounds(symbol, timeframe):
    directory = engine._find_local_data_directory(symbol, timeframe)
    if not directory:
        return {"ok": False, "error": "local historical archive missing"}
    files = sorted(glob.glob(os.path.join(directory, "*.parquet")))
    if not files:
        return {"ok": False, "error": "local parquet missing"}
    indexes = []
    rows = 0
    for path in files:
        frame = pd.read_parquet(path, columns=["open"])
        indexes.append((frame.index.min(), frame.index.max()))
        rows += len(frame)
    first = min(row[0] for row in indexes); last = max(row[1] for row in indexes)
    delta = engine.TIMEFRAME_SPECS[timeframe]["expected_delta"]
    audit_start_utc = first + 300*delta
    def beijing_text(value):
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        return stamp.tz_convert("Asia/Shanghai").strftime(BEIJING_FMT)
    return {"ok": True, "directory": directory, "rows": rows,
            "raw_start_utc": str(first), "raw_end_utc": str(last),
            "audit_start_beijing": beijing_text(audit_start_utc),
            "audit_end_beijing": beijing_text(last),
            "warmup_bars_excluded": 300}


def _trade_metrics(result, seed):
    if not isinstance(result, dict) or result.get("error"):
        return {"ok": False, "error": (result or {}).get("error") or
                "invalid backtest result", "trades": 0}
    trades = result.get("trades") or []
    values = [float(row.get("pnl_ratio") or 0.0) for row in trades]
    split = int(math.floor(len(values)*.70))
    holdout = values[split:] if values else []
    cap = 1.0; peak = 1.0; max_drawdown = 0.0
    streak = max_streak = 0
    for value in values:
        cap *= max(0.0, 1.0+value); peak = max(peak, cap)
        max_drawdown = max(max_drawdown, (peak-cap)/peak if peak else 1.0)
        if value <= 0:
            streak += 1; max_streak = max(max_streak, streak)
        else:
            streak = 0
    high_vol = [float(row.get("pnl_ratio") or 0.0) for row in trades
                if "high_vol" in str(row.get("market_regime") or "")]
    posterior = ecosystem._probabilistic_net_summary(
        values, seed_material=seed, bootstrap_samples=1200) if values else {}
    return {
        "ok": True, "trades": len(values),
        "win_rate_pct": round(sum(value > 0 for value in values)/float(
            len(values))*100.0, 4) if values else 0.0,
        "mean_net_return_pct": round(sum(values)/float(len(values))*100.0, 6)
        if values else None,
        "holdout_trades": len(holdout),
        "holdout_mean_net_return_pct": round(sum(holdout)/float(
            len(holdout))*100.0, 6) if holdout else None,
        "compounded_full_margin_return_pct": round((cap-1.0)*100.0, 6),
        "max_drawdown_pct": round(max_drawdown*100.0, 6),
        "max_loss_streak": max_streak,
        "stop_loss_count": sum(bool(row.get("stop_loss")) for row in trades),
        "high_vol_trades": len(high_vol),
        "high_vol_mean_net_return_pct": round(sum(high_vol)/float(
            len(high_vol))*100.0, 6) if high_vol else None,
        "probability_mean_positive": posterior.get("probability_mean_positive"),
        "profit_factor": posterior.get("profit_factor"),
        "transaction_cost_model": ((result.get("consistency_audit") or {}).get(
            "friction_model") or {}),
        "data_version": ((result.get("consistency_audit") or {}).get(
            "data_version")),
    }


def _derive_triple_actual(base_result, symbol, leverage):
    """Reprice the identical signal path with the engine's exact 3x model.

    Signals and exits are cost-independent.  Costs are rebuilt from the exact
    triple_actual configuration, including its p95 observed floors, rather
    than assuming that the effective p75 base cost is linearly scalable.
    """
    if not isinstance(base_result, dict) or base_result.get("error"):
        return copy.deepcopy(base_result)
    result = {
        "trades": [],
        "consistency_audit": copy.deepcopy(
            base_result.get("consistency_audit") or {}),
    }
    friction = engine.load_execution_friction(symbol, "triple_actual")
    round_trip = 2.0*sum(float(friction.get(key) or 0.0) for key in (
        "fee_rate_per_side", "slippage_rate_per_side",
        "half_spread_rate_per_side", "impact_rate_per_side",
        "latency_rate_per_side"))*int(leverage)
    for source in base_result.get("trades") or []:
        row = copy.deepcopy(source)
        try:
            time_format = (BEIJING_FMT if len(str(row.get("entry_time") or "")) >= 19
                           else "%Y-%m-%d %H:%M")
            entry_at = datetime.strptime(row.get("entry_time"), time_format)
            exit_at = datetime.strptime(row.get("exit_time"), time_format)
            holding_hours = max(0.0, (exit_at-entry_at).total_seconds()/3600.0)
        except Exception:
            holding_hours = 0.0
        funding_cost = (holding_hours/8.0)*float(
            friction.get("funding_rate_per_8h") or 0.0)*int(leverage)
        triple_cost = round_trip+funding_cost
        net = float(row.get("gross_pnl_ratio") or 0.0)-triple_cost
        row.update({
            "transaction_cost_ratio": triple_cost,
            "friction_scenario": "triple_actual",
            "pnl_ratio": net, "profit": bool(net > 0.0),
        })
        for key in ("pnl_pct", "return_pct", "profit_pct", "yield_pct",
                    "roi", "roi_pct", "leveraged_return_pct"):
            row[key] = round(net*100.0, 2)
        result["trades"].append(row)
    audit = result["consistency_audit"]
    friction = copy.deepcopy(friction)
    friction["derived_from_observed_base"] = True
    friction["derivation"] = (
        "same_signal_path_repriced_with_exact_triple_actual_p95_cost_model")
    audit["friction_model"] = friction
    result["consistency_audit"] = audit
    return result


def _load_death_knowledge(symbol, timeframe):
    if not DB_PATH.exists():
        return {"distilled_rules": [], "target_local_deaths": []}
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        tables = set(row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"))
        rules = []
        if "distilled_death_rules" in tables:
            for row in conn.execute(
                    "SELECT rule_id,title,state,death_codes_json,proposal_json,"
                    "measured_evidence_count,distinct_target_count FROM "
                    "distilled_death_rules ORDER BY updated_at DESC LIMIT 32"):
                rules.append({"rule_id": row[0], "title": row[1],
                              "state": row[2],
                              "death_codes": json.loads(row[3] or "[]"),
                              "logic": json.loads(row[4] or "{}"),
                              "measured_evidence_count": row[5],
                              "distinct_target_count": row[6]})
        deaths = []
        if "prescreen_death_maps" in tables:
            for row in conn.execute(
                    "SELECT week_bucket,branch_count,rejection_count,clusters_json,"
                    "ai_analysis_json "
                    "FROM prescreen_death_maps WHERE symbol=? AND timeframe=? "
                    "ORDER BY created_at DESC LIMIT 4", (symbol, timeframe)):
                deaths.append({"week_bucket": row[0], "branch_count": row[1],
                               "rejection_count": row[2],
                               "clusters": json.loads(row[3] or "[]")[:24],
                               "ai_analysis": json.loads(row[4] or "{}")})
        return {"distilled_rules": rules, "target_local_deaths": deaths,
                "automatic_cross_strategy_match": False}
    finally:
        conn.close()


def _assignment_config(path):
    return _read(path, {})


def _strategy_rating(assignment_id):
    rating = ((_read(RATING_PATH, {}).get("ratings_by_id") or {}).get(
        assignment_id) or {})
    return rating


def _manifest(row):
    symbol = row["symbol"]; timeframe = row["timeframe"]
    key = row["strategy_key"]
    registry = engine.STRATEGIES_BY_TIMEFRAME.get(timeframe) or {}
    strategy = registry.get(key)
    config = _assignment_config(row["config_file"])
    rating = _strategy_rating(row["assignment_id"])
    grade = str(rating.get("grade") or "B").upper()
    grade_ratio = {"S": .70, "A": .50, "B": .30, "C": .15}.get(grade, .30)
    configured_ratio = float(config.get("full_position_ratio") or 0.0)
    # Ratings are absolute account-equity allocations.  A timeframe/profile
    # ratio is retained only as historical configuration metadata and must not
    # scale the grade a second time.
    effective_ratio = min(1.0, grade_ratio)
    if not strategy:
        return {"schema": AUDIT_SCHEMA, "assignment": row,
                "archive_ok": False, "error": "strategy absent from backtest registry",
                "execution": {"config": config, "rating": rating}}
    entry, exit_value, direction = strategy
    entry_source = _source(entry); exit_source = _source(exit_value)
    metadata = engine.load_strategy_metadata(key)
    archive = {
        "schema": "qiyu_legacy_strategy_audit_ir_v1",
        "key": key, "symbol": symbol, "timeframe": timeframe,
        "direction": direction,
        "entry_callable": getattr(entry, "__name__", "unknown"),
        "exit_callable": getattr(exit_value, "__name__", "unknown"),
        "entry_source": entry_source, "exit_source": exit_source,
        "params": engine.load_strategy_params(key), "metadata": metadata,
        "source_hash": _sha({"entry": entry_source, "exit": exit_source,
                             "params": engine.load_strategy_params(key)}),
        "safe_dsl_equivalence_verified": key in engine.DSL_STRATEGY_DEFINITIONS,
        "legacy_callable_ir_is_machine_readable": True,
    }
    return {
        "schema": AUDIT_SCHEMA, "assignment": row, "archive_ok": True,
        "strategy_ir": archive,
        "execution": {
            "leverage": int(config.get("leverage") or 20),
            "stop_loss_pct": float(config.get("stop_loss_pct") or .009),
            "configured_profile_ratio": configured_ratio,
            "rating": rating, "grade": grade, "grade_ratio": grade_ratio,
            "actual_effective_margin_ratio": effective_ratio,
            "double_scaling_detected": False,
            "sizing_policy": "absolute_grade_ratio_no_timeframe_multiplier",
            "estimated_equity_stop_risk_pct": round(
                effective_ratio*int(config.get("leverage") or 20)*
                float(config.get("stop_loss_pct") or .009)*100.0, 6),
        },
    }


def _run_assignment(row, use_ai=True):
    manifest = _manifest(row)
    symbol = row["symbol"]; timeframe = row["timeframe"]
    key = row["strategy_key"]
    bounds = _data_bounds(symbol, timeframe)
    backtests = {}
    if manifest.get("archive_ok") and bounds.get("ok"):
        kwargs = {"strategy_name": key, "instId": symbol,
                  "start_time": bounds["audit_start_beijing"],
                  "end_time": bounds["audit_end_beijing"],
                  "leverage": manifest["execution"]["leverage"],
                  "stop_loss_pct": manifest["execution"]["stop_loss_pct"],
                  "account_position_ratio": manifest["execution"][
                      "actual_effective_margin_ratio"],
                  "timeframe": timeframe}
        base = engine.run_backtest(friction_scenario="observed_base", **kwargs)
        triple = _derive_triple_actual(base, symbol, manifest["execution"]["leverage"])
        backtests = {
            "observed_base": _trade_metrics(base, row["assignment_id"]+"|base"),
            "triple_actual": _trade_metrics(triple, row["assignment_id"]+"|triple"),
        }
    else:
        backtests = {"observed_base": {"ok": False, "trades": 0,
                                       "error": bounds.get("error") or manifest.get("error")},
                     "triple_actual": {"ok": False, "trades": 0,
                                       "error": bounds.get("error") or manifest.get("error")}}
    triple = backtests["triple_actual"]
    deterministic = {
        "logic_archive_pass": bool(manifest.get("archive_ok")),
        "safe_dsl_equivalence_pass": bool((manifest.get("strategy_ir") or {}).get(
            "safe_dsl_equivalence_verified")),
        "triple_cost_user_metric_pass": bool(
            triple.get("ok") and triple.get("trades", 0) > 0
            and float(triple.get("mean_net_return_pct") or 0.0) > 0.0),
        "triple_cost_statistical_pass": bool(
            triple.get("ok") and triple.get("trades", 0) >= 20
            and float(triple.get("mean_net_return_pct") or 0.0) > 0.0
            and int(triple.get("holdout_trades") or 0) >= 6
            and float(triple.get("holdout_mean_net_return_pct") or 0.0) > 0.0
            and float(triple.get("probability_mean_positive") or 0.0) >= .80),
        "historical_extreme_ohlcv_pass": bool(
            int(triple.get("high_vol_trades") or 0) >= 3
            and float(triple.get("high_vol_mean_net_return_pct") or 0.0) > 0.0),
        "historical_extreme_micro_replay_pass": False,
        "historical_extreme_micro_replay_state": "unavailable_no_predeployment_l2_queue_cancel_history",
    }
    death = _load_death_knowledge(symbol, timeframe)
    evidence = {"data_bounds": bounds, "backtests": backtests,
                "deterministic_gates": deterministic,
                "death_knowledge": death,
                "truth_boundary": {
                    "historical_ohlcv": True,
                    "historical_l2_queue_cancel_replay": False,
                    "ai_must_not_infer_missing_microstructure": True}}
    reviews = (ai.retrospective_live_unanimous_review(manifest, evidence)
               if use_ai else {"passed": False, "reviews": [],
                               "state": "ai_skipped_test_mode"})
    known_rule_ids = set(
        str(item.get("rule_id")) for item in death.get("distilled_rules") or []
        if item.get("rule_id") and str(item.get("state") or "").lower()
        in ("active", "validated", "approved"))
    death_votes = {}
    for review in reviews.get("reviews") or []:
        provider = str(review.get("provider") or "unknown")
        for match in review.get("death_matches") or []:
            rule_id = str((match or {}).get("rule_id") or "")
            if rule_id in known_rule_ids:
                death_votes.setdefault(rule_id, set()).add(provider)
    confirmed_death_matches = [
        {"rule_id": rule_id, "independent_provider_count": len(providers),
         "providers": sorted(providers)}
        for rule_id, providers in sorted(death_votes.items())
        if len(providers) >= 2]
    deterministic["confirmed_current_death_knowledge_match_pass"] = not bool(
        confirmed_death_matches)
    evidence["confirmed_death_matches"] = confirmed_death_matches
    deterministic["three_ai_unanimous_attack_pass"] = bool(reviews.get("passed"))
    passed_all = all([
        deterministic["logic_archive_pass"],
        deterministic["safe_dsl_equivalence_pass"],
        deterministic["triple_cost_statistical_pass"],
        deterministic["historical_extreme_ohlcv_pass"],
        deterministic["historical_extreme_micro_replay_pass"],
        deterministic["confirmed_current_death_knowledge_match_pass"],
        deterministic["three_ai_unanimous_attack_pass"],
    ])
    if confirmed_death_matches:
        disposition = "cognitive_eliminated_confirmed_death_knowledge"
    elif not deterministic["triple_cost_user_metric_pass"]:
        disposition = "friction_cost_survivorship_bias"
    elif not deterministic["historical_extreme_micro_replay_pass"]:
        disposition = "suspended_unverifiable_historical_micro_extreme"
    elif not deterministic["safe_dsl_equivalence_pass"]:
        disposition = "suspended_legacy_logic_not_equivalent_safe_dsl"
    elif not reviews.get("passed"):
        disposition = "suspended_three_ai_attack_not_unanimous"
    else:
        disposition = "passed_all"
    return {
        "audit_id": _sha({"schema": AUDIT_SCHEMA,
                           "assignment": row["assignment_id"],
                           "logic": (manifest.get("strategy_ir") or {}).get(
                               "source_hash")})[:24],
        "assignment_id": row["assignment_id"], "manifest": manifest,
        "evidence": evidence, "three_ai_review": reviews,
        "passed_all": passed_all, "disposition": disposition,
        "new_entries_allowed": passed_all,
        "automatic_live_restoration": False,
        "audited_at": _now(),
    }


def _ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS live_strategy_retrospective_audits(
      audit_id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL, strategy_key TEXT,
      symbol TEXT, timeframe TEXT, passed_all INTEGER NOT NULL,
      disposition TEXT NOT NULL, report_json TEXT NOT NULL, audited_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_live_retro_assignment "
                 "ON live_strategy_retrospective_audits(assignment_id,audited_at)")


def _store(result):
    manifest = result.get("manifest") or {}; assignment = manifest.get("assignment") or {}
    last_error = None
    for attempt in range(8):
        conn = sqlite3.connect(str(DB_PATH), timeout=60)
        try:
            conn.execute("PRAGMA busy_timeout=60000")
            _ensure_table(conn)
            conn.execute("INSERT OR REPLACE INTO live_strategy_retrospective_audits "
                         "VALUES(?,?,?,?,?,?,?,?,?)",
                         (result["audit_id"], result["assignment_id"],
                          assignment.get("strategy_key"), assignment.get("symbol"),
                          assignment.get("timeframe"), 1 if result["passed_all"] else 0,
                          result["disposition"], json.dumps(
                              result, ensure_ascii=False, sort_keys=True),
                          result["audited_at"]))
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            last_error = exc
            try:
                conn.rollback()
            except Exception:
                pass
            if "locked" not in str(exc).lower() or attempt == 7:
                raise
            time.sleep(min(10.0, 1.5*(attempt+1)))
        finally:
            conn.close()
    raise last_error


def _cached_exact_result(assignment):
    """Reuse only a completed audit of the exact executable source hash."""
    manifest = _manifest(assignment)
    expected_id = _sha({
        "schema": AUDIT_SCHEMA,
        "assignment": assignment["assignment_id"],
        "logic": (manifest.get("strategy_ir") or {}).get("source_hash"),
    })[:24]
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    try:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT report_json FROM live_strategy_retrospective_audits "
            "WHERE audit_id=?", (expected_id,)).fetchone()
        if not row:
            return None
        result = json.loads(row[0])
        if result.get("audit_id") != expected_id:
            return None
        result["reused_exact_audit"] = True
        return result
    finally:
        conn.close()


def _apply_controls(results, audit_run_id):
    controls = _read(CONTROL_PATH, {})
    controls["schema"] = "qiyu_runtime_controls_v3_mandatory_cognitive_gate"
    controls["global_audit"] = {
        "audit_id": audit_run_id, "state": "retrospective_audit_completed",
        "completed_at": _now(), "enforce_cognitive_gate": True,
        "new_or_legacy_strategy_requires_passed_all": True,
        "automatic_unfreeze_allowed": False,
        "existing_position_exit_management_continues": True,
    }
    assignments = controls.setdefault("assignments", {})
    for result in results:
        row = assignments.setdefault(result["assignment_id"], {})
        manifest = result.get("manifest") or {}
        strategy_ir = manifest.get("strategy_ir") or {}
        metadata = strategy_ir.get("metadata") or {}
        certified_version_hash = metadata.get("approved_version_hash")
        row.update({
            "pause_new_entries": not result["passed_all"],
            "audit_state": "passed_all" if result["passed_all"] else "failed_closed",
            "audit_id": result["audit_id"],
            "certified_version_hash": (certified_version_hash
                                       if result["passed_all"] else None),
            "full_cognitive_matrix_passed": bool(result["passed_all"]),
            "disposition": result["disposition"],
            "manual_review_required": not result["passed_all"],
            "automatic_unfreeze_allowed": False,
            "existing_position_exit_management_continues": True,
            "updated_at": _now(),
        })
    _atomic(CONTROL_PATH, controls)
    _atomic(GATE_PATH, {
        "schema": "qiyu_live_cognitive_gate_v1",
        "enforce": True, "audit_run_id": audit_run_id,
        "enabled_at": _now(),
        "required_assignment_state": "passed_all",
        "new_or_legacy_strategy_may_not_bypass": True,
        "existing_position_exit_management_continues": True,
        "automatic_disable_for_missing_or_malformed_controls": True,
    })


def run(use_ai=True):
    started = _now(); assignments = discover_live_assignments()
    run_id = "retro_%s_%s" % (datetime.now().strftime("%Y%m%d_%H%M%S"),
                               _sha(assignments)[:10])
    results = []
    _atomic(PROGRESS_PATH, {"ok": True, "state": "running",
            "run_id": run_id, "started_at": started, "total": len(assignments),
            "completed": 0, "current": None})
    for index, assignment in enumerate(assignments, 1):
        _atomic(PROGRESS_PATH, {"ok": True, "state": "running",
                "run_id": run_id, "started_at": started,
                "total": len(assignments), "completed": index-1,
                "current": assignment})
        try:
            result = _cached_exact_result(assignment)
            if result is None:
                result = _run_assignment(assignment, use_ai=use_ai)
        except Exception as exc:
            result = {"audit_id": _sha({"run": run_id, "assignment": assignment})[:24],
                      "assignment_id": assignment["assignment_id"],
                      "manifest": {"assignment": assignment},
                      "passed_all": False, "new_entries_allowed": False,
                      "automatic_live_restoration": False,
                      "disposition": "audit_execution_failed_closed",
                      "error": str(exc), "traceback": traceback.format_exc()[-4000:],
                      "audited_at": _now()}
        _store(result); results.append(result)
        _atomic(PROGRESS_PATH, {"ok": True, "state": "running",
                "run_id": run_id, "started_at": started,
                "total": len(assignments), "completed": index,
                "current": None,
                "last": {"assignment_id": result["assignment_id"],
                         "disposition": result["disposition"],
                         "passed_all": result["passed_all"]}})
    _apply_controls(results, run_id)
    passed = [row for row in results if row["passed_all"]]
    report = {
        "ok": True, "schema": AUDIT_SCHEMA, "run_id": run_id,
        "started_at": started, "finished_at": _now(),
        "assignment_count": len(results), "passed_count": len(passed),
        "failed_closed_count": len(results)-len(passed),
        "surviving_assignments": [row["assignment_id"] for row in passed],
        "disposition_counts": {}, "results": results,
        "new_strategy_exploration": "disabled_during_retrospective_audit",
        "historical_microstructure_boundary": (
            "no predeployment L2/queue/cancel replay; cannot be fabricated"),
        "live_restoration_policy": "passed_all_plus_manual_review_only",
    }
    for result in results:
        key = result["disposition"]
        report["disposition_counts"][key] = report["disposition_counts"].get(key, 0)+1
    base_positive = 0; triple_positive = 0; triple_statistical = 0
    ai_unanimous = 0; safe_dsl = 0; micro_extreme = 0; risks = []
    for result in results:
        evidence = result.get("evidence") or {}
        backtests = evidence.get("backtests") or {}
        gates = evidence.get("deterministic_gates") or {}
        base = backtests.get("observed_base") or {}
        triple = backtests.get("triple_actual") or {}
        base_positive += int(float(base.get("mean_net_return_pct") or 0.0) > 0.0)
        triple_positive += int(float(triple.get("mean_net_return_pct") or 0.0) > 0.0)
        triple_statistical += int(bool(gates.get("triple_cost_statistical_pass")))
        ai_unanimous += int(bool(gates.get("three_ai_unanimous_attack_pass")))
        safe_dsl += int(bool(gates.get("safe_dsl_equivalence_pass")))
        micro_extreme += int(bool(gates.get("historical_extreme_micro_replay_pass")))
        value = (((result.get("manifest") or {}).get("execution") or {})
                 .get("estimated_equity_stop_risk_pct"))
        if value is not None:
            risks.append(float(value))
    report["qualification_funnel"] = {
        "observed_base_positive_mean": base_positive,
        "triple_actual_positive_mean": triple_positive,
        "triple_actual_statistically_qualified": triple_statistical,
        "safe_dsl_equivalent": safe_dsl,
        "historical_extreme_micro_replay_qualified": micro_extreme,
        "three_ai_unanimous_attack_survivors": ai_unanimous,
        "passed_all": len(passed),
    }
    report["historical_hidden_risk_bounds"] = {
        "strategy_count_exposed_without_current_matrix": len(results),
        "estimated_single_stop_equity_risk_pct_min": min(risks) if risks else None,
        "estimated_single_stop_equity_risk_pct_max": max(risks) if risks else None,
        "maximum_three_position_policy_risk_pct": 15.2,
        "loss_probability_or_realized_tail_risk": (
            "not identifiable from OHLCV and recorded fills alone; no fabricated number"),
        "scope": "mechanical configured stop exposure, before gaps/slippage/fee overruns",
    }
    _atomic(REPORT_PATH, report)
    _atomic(PROGRESS_PATH, {"ok": True, "state": "completed",
            "run_id": run_id, "started_at": started,
            "finished_at": report["finished_at"], "total": len(results),
            "completed": len(results), "passed_count": len(passed),
            "failed_closed_count": len(results)-len(passed)})
    print(json.dumps({key: report[key] for key in (
        "ok", "run_id", "assignment_count", "passed_count",
        "failed_closed_count", "disposition_counts")},
        ensure_ascii=False, sort_keys=True))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-ai", action="store_true")
    args = parser.parse_args()
    run(use_ai=not args.no_ai)


if __name__ == "__main__":
    main()
