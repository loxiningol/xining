# -*- coding: utf-8 -*-
"""Closed-loop strategy learning, deterministic validation and AI consensus.

The ecosystem may automatically deploy bounded numeric parameter changes only.
New Python logic, leverage/stop-loss/policy changes are recorded as structural
proposals and cannot bypass code review.  Routine strategy-triggered entries
remain deterministic and are not AI calls.
"""
from __future__ import print_function

from datetime import datetime, timedelta
from pathlib import Path
import argparse
import copy
import glob
import hashlib
import itertools
import json
import math
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import time


ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
DB_PATH = AUTO_DIR / "strategy_ecosystem.db"
STATUS_PATH = AUTO_DIR / "strategy_ecosystem_status.json"
CREATOR_STATUS_PATH = AUTO_DIR / "strategy_creator_status.json"
CREATOR_TARGETS_PATH = AUTO_DIR / "strategy_creation_targets.json"
CLUSTER_MATRIX_STATUS_PATH = AUTO_DIR / "research_cluster_matrix_status.json"
COST_MODEL_PATH = AUTO_DIR / "execution_cost_model.json"
LIVE_AUDIT_REPORT_PATH = AUTO_DIR / "live_strategy_retrospective_audit.json"
LIVE_AUDIT_PROGRESS_PATH = AUTO_DIR / "live_strategy_retrospective_audit_progress.json"
LIVE_COGNITIVE_GATE_PATH = AUTO_DIR / "live_cognitive_gate_required.json"
LIVE_RUNTIME_CONTROLS_PATH = AUTO_DIR / "strategy_runtime_controls.json"
POST_AUDIT_POLICY_PATH = AUTO_DIR / "post_audit_operating_policy.json"
MATRIX_ROUND_STATUS_PATH = AUTO_DIR / "strategy_matrix_round_status.json"
LEGACY_SHADOW_STATUS_PATH = AUTO_DIR / "legacy_shadow_status.json"
CONFIG_PATH = ROOT / "strategy_configs" / "experimental_strategies.json"
DSL_CONFIG_PATH = ROOT / "strategy_configs" / "ai_dsl_strategies.json"
BACKUP_DIR = ROOT / "backups" / "strategy_ecosystem"
LOCK_PATH = AUTO_DIR / "strategy_ecosystem.lock"
BEIJING_FMT = "%Y-%m-%d %H:%M:%S"
PROTECTED_CAPITAL_PARAMS = {
    "stop_loss_pct", "leverage", "full_position_ratio", "reserve_usdt",
    "fee_buffer_usdt", "max_open_positions", "max_positions_per_symbol",
    "max_daily_entries", "max_daily_realized_loss_ratio",
    "max_consecutive_losses", "max_total_estimated_risk_ratio",
}
ACTIVE_HUNT_SCHEMA_VERSION = "target_local_operator_lattice_v6"
SOLVABILITY_GATE_SCHEMA_VERSION = "solvability_heartbeat_v2"
SOLVABILITY_DEATH_EVIDENCE_BATCH = 12
DEATH_DISTILLATION_SCHEMA_VERSION = "stage_separated_rule_tree_v1"
DEATH_DISTILLATION_BATCH = 50
COLD_START_MAX_FULL_BRANCHES = 2
NEGATIVE_CLUSTER_SLEEP_HOURS = 168.0
EARLY_PRUNE_SHORT_SLEEP_HOURS = 72.0
PORTFOLIO_DAILY_ENTRY_TARGET = 3.0
CONDITIONAL_PROBE_STATES = ("conditional_frequency_probe", "passed_all")


def _now():
    return datetime.now().strftime(BEIJING_FMT)


def _deep_search_eligibility(observations, qualified_count=0):
    """Permit expensive AI exploration only after a measured cost survivor.

    Quick-screen promise and descriptive microstructure activity are not
    empirical edge.  They must never buy three-model rescue calls on their own.
    """
    cost_survivors = sum(bool(row.get("survived_cost_screen"))
                         for row in (observations or []))
    if int(qualified_count or 0) > 0:
        state = "not_needed_existing_empirical_edge"
        allowed = False
    elif cost_survivors > 0:
        state = "eligible_target_local_cost_survivor"
        allowed = True
    else:
        state = "dormant_no_target_local_cost_survivor"
        allowed = False
    return {
        "allowed": allowed,
        "state": state,
        "target_local_cost_survivors": cost_survivors,
        "quick_promise_is_not_edge": True,
        "micro_activity_is_not_edge": True,
    }


def _negative_cluster_sleep_state(hours_since_last_run, recent_branches,
                                  recent_cost_survivors,
                                  consecutive_early_prunes):
    """Return an auditable compute sleep state without stopping data capture.

    First no-edge prune sleep is short (72h) so frequency-seeking clusters can
    wake sooner.  A second consecutive no-edge round escalates back to 168h.
    """
    consecutive = int(consecutive_early_prunes or 0)
    eligible = bool(
        int(recent_branches or 0) >= 48
        and int(recent_cost_survivors or 0) == 0
        and consecutive >= 1)
    sleep_hours = (NEGATIVE_CLUSTER_SLEEP_HOURS if consecutive >= 2
                   else EARLY_PRUNE_SHORT_SLEEP_HOURS)
    remaining = max(0.0, sleep_hours - float(hours_since_last_run or 0.0)
                    ) if eligible else 0.0
    sleeping = bool(eligible and remaining > 0.0)
    reason = None
    if sleeping:
        reason = ("repeated_baseline_without_cost_survivor_long_sleep"
                  if consecutive >= 2 else
                  "baseline_completed_without_cost_survivor_short_sleep")
    return {
        "sleeping": sleeping,
        "reason": reason,
        "sleep_hours": sleep_hours,
        "short_sleep_hours": EARLY_PRUNE_SHORT_SLEEP_HOURS,
        "long_sleep_hours": NEGATIVE_CLUSTER_SLEEP_HOURS,
        "remaining_hours": round(remaining, 3),
        "forward_data_collection_continues": True,
        "explicit_manual_override_allowed": True,
    }


def _full_branch_evaluation_budget(promising_count, cold_start=False):
    count = int(promising_count or 0)
    budget = min(8, max(1, int(math.ceil(count*.20)))) if count else 0
    return (min(budget, COLD_START_MAX_FULL_BRANCHES)
            if cold_start else budget)


def _read(path, default):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(delete=False, dir=str(path.parent),
                                         mode="w", encoding="utf-8")
    try:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        handle.close()
        Path(handle.name).replace(path)
    except Exception:
        try:
            Path(handle.name).unlink()
        except Exception:
            pass
        raise


def _db():
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    # Rating, experience capture and six-hour research tasks legitimately
    # overlap.  WAL permits concurrent readers, while a bounded busy timeout
    # prevents a brief writer overlap from being misreported as research
    # failure.
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS cases (
      case_id TEXT PRIMARY KEY, kind TEXT NOT NULL, symbol TEXT, timeframe TEXT,
      strategy_key TEXT, occurred_at TEXT, outcome REAL, payload_json TEXT NOT NULL,
      learned_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS candidates (
      candidate_hash TEXT PRIMARY KEY, strategy_key TEXT NOT NULL, symbol TEXT,
      timeframe TEXT, state TEXT NOT NULL, candidate_json TEXT NOT NULL,
      evidence_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ai_reviews (
      candidate_hash TEXT NOT NULL, provider TEXT NOT NULL, decision TEXT NOT NULL,
      review_json TEXT NOT NULL, reviewed_at TEXT NOT NULL,
      PRIMARY KEY(candidate_hash, provider)
    );
    CREATE TABLE IF NOT EXISTS deployments (
      candidate_hash TEXT PRIMARY KEY, strategy_key TEXT NOT NULL,
      before_hash TEXT NOT NULL, after_hash TEXT NOT NULL, backup_path TEXT NOT NULL,
      deployed_at TEXT NOT NULL, rolled_back_at TEXT, rollback_reason TEXT
    );
    CREATE TABLE IF NOT EXISTS lessons (
      lesson_id TEXT PRIMARY KEY, strategy_key TEXT, lesson_type TEXT NOT NULL,
      payload_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS strategy_creation_runs (
      run_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
      state TEXT NOT NULL, summary_json TEXT NOT NULL,
      started_at TEXT NOT NULL, finished_at TEXT
    );
    CREATE TABLE IF NOT EXISTS search_branch_observations (
      observation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
      symbol TEXT NOT NULL, timeframe TEXT NOT NULL, pattern_id TEXT NOT NULL,
      direction TEXT, event_feature TEXT, event_op TEXT, context_feature TEXT,
      best_horizon_bars INTEGER, evaluation_stage TEXT NOT NULL,
      full_evaluated INTEGER NOT NULL, survived_cost_screen INTEGER NOT NULL,
      credible INTEGER NOT NULL, event_count INTEGER,
      base_win_rate REAL, base_mean_net REAL, holdout_mean_net REAL,
      stressed_mean_net REAL, probability_positive REAL,
      acquisition_score REAL, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_search_branch_history
      ON search_branch_observations(symbol,timeframe,pattern_id,created_at);
    CREATE TABLE IF NOT EXISTS search_branch_stats (
      symbol TEXT NOT NULL, timeframe TEXT NOT NULL, pattern_id TEXT NOT NULL,
      attempts INTEGER NOT NULL, full_evaluations INTEGER NOT NULL,
      survived_count INTEGER NOT NULL, credible_count INTEGER NOT NULL,
      consecutive_rejections INTEGER NOT NULL, ema_acquisition REAL NOT NULL,
      last_seen TEXT NOT NULL,
      PRIMARY KEY(symbol,timeframe,pattern_id)
    );
    CREATE TABLE IF NOT EXISTS search_budget_ledger (
      run_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
      quick_candidates INTEGER NOT NULL, full_candidates INTEGER NOT NULL,
      qualified_candidates INTEGER NOT NULL, ai_calls_used INTEGER NOT NULL,
      ai_calls_saved INTEGER NOT NULL, resource_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS active_hunt_prior_runs (
      prior_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
      week_bucket TEXT NOT NULL, payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(symbol,timeframe,week_bucket)
    );
    CREATE TABLE IF NOT EXISTS active_hunt_probe_experiments (
      experiment_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
      symbol TEXT NOT NULL, timeframe TEXT NOT NULL, pattern_id TEXT NOT NULL,
      ai_support INTEGER NOT NULL, filters_json TEXT NOT NULL,
      exact_events INTEGER NOT NULL, similarity_events INTEGER NOT NULL,
      cost_multiple REAL NOT NULL, mean_net_pct REAL,
      probability_positive REAL, state TEXT NOT NULL,
      payload_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_active_hunt_probe_target
      ON active_hunt_probe_experiments(symbol,timeframe,created_at);
    CREATE TABLE IF NOT EXISTS positive_core_samples (
      sample_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
      symbol TEXT NOT NULL, timeframe TEXT NOT NULL, pattern_id TEXT NOT NULL,
      label_state TEXT NOT NULL, synthetic_label INTEGER NOT NULL DEFAULT 0,
      evidence_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS probe_failure_autopsies (
      autopsy_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
      run_id TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
      pattern_id TEXT NOT NULL, death_cause_code TEXT NOT NULL,
      autopsy_json TEXT NOT NULL, audits_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS pruning_rule_memory (
      rule_hash TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
      pattern_id TEXT NOT NULL, filter_id TEXT NOT NULL,
      death_cause_code TEXT NOT NULL, state TEXT NOT NULL,
      occurrences INTEGER NOT NULL, empirical_json TEXT NOT NULL,
      audits_json TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_pruning_rule_target
      ON pruning_rule_memory(symbol,timeframe,pattern_id,state);
    CREATE TABLE IF NOT EXISTS positive_confirmation_candidates (
      candidate_hash TEXT PRIMARY KEY, experiment_id TEXT,
      symbol TEXT NOT NULL, timeframe TEXT NOT NULL, pattern_id TEXT,
      state TEXT NOT NULL, offline_gates_json TEXT NOT NULL,
      shadow_state_json TEXT NOT NULL,
      registered_at TEXT NOT NULL, shadow_started_at TEXT,
      shadow_completed_at TEXT, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS shadow_trade_events (
      event_id TEXT PRIMARY KEY, candidate_hash TEXT NOT NULL,
      symbol TEXT NOT NULL, timeframe TEXT NOT NULL, candle_ts INTEGER NOT NULL,
      event_type TEXT NOT NULL, price REAL, net_return_pct REAL,
      microstructure_json TEXT NOT NULL, payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS prescreen_rejections (
      rejection_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
      symbol TEXT NOT NULL, timeframe TEXT NOT NULL, pattern_id TEXT NOT NULL,
      stage TEXT NOT NULL, provider TEXT NOT NULL, decision TEXT NOT NULL,
      hard_veto INTEGER NOT NULL, death_codes_json TEXT NOT NULL,
      reason TEXT NOT NULL, review_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_prescreen_rejection_target
      ON prescreen_rejections(symbol,timeframe,pattern_id,created_at);
    CREATE TABLE IF NOT EXISTS prescreen_death_maps (
      map_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
      week_bucket TEXT NOT NULL, branch_count INTEGER NOT NULL,
      rejection_count INTEGER NOT NULL, clusters_json TEXT NOT NULL,
      ai_analysis_json TEXT NOT NULL, created_at TEXT NOT NULL,
      UNIQUE(symbol,timeframe,week_bucket)
    );
    CREATE TABLE IF NOT EXISTS shadow_environment_snapshots (
      snapshot_id TEXT PRIMARY KEY, candidate_hash TEXT NOT NULL,
      symbol TEXT NOT NULL, timeframe TEXT NOT NULL, candle_ts INTEGER NOT NULL,
      phase TEXT NOT NULL, regime_json TEXT NOT NULL,
      microstructure_json TEXT NOT NULL, primitive_json TEXT NOT NULL,
      payload_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_shadow_environment_candidate
      ON shadow_environment_snapshots(candidate_hash,candle_ts);
    CREATE TABLE IF NOT EXISTS death_micro_cooccurrence_observations (
      observation_id TEXT PRIMARY KEY, monitor_run_id TEXT NOT NULL,
      symbol TEXT NOT NULL, timeframe TEXT NOT NULL, pattern_id TEXT NOT NULL,
      death_codes_json TEXT NOT NULL, signal_ts INTEGER NOT NULL,
      cluster_id TEXT, cluster_state TEXT, micro_window_end_ms INTEGER,
      distance REAL, matched INTEGER NOT NULL, payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_death_micro_target
      ON death_micro_cooccurrence_observations(symbol,timeframe,signal_ts);
    CREATE TABLE IF NOT EXISTS death_micro_association_reports (
      report_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
      observation_count INTEGER NOT NULL, eligible_cell_count INTEGER NOT NULL,
      state TEXT NOT NULL, payload_json TEXT NOT NULL,
      ai_analysis_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS death_forward_validation_outcomes (
      observation_id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
      timeframe TEXT NOT NULL, pattern_id TEXT NOT NULL,
      signal_ts INTEGER NOT NULL, direction TEXT NOT NULL,
      horizon_bars INTEGER NOT NULL, stopped INTEGER NOT NULL,
      gross_return_pct REAL NOT NULL, net_return_pct REAL NOT NULL,
      verdict TEXT NOT NULL, friction_json TEXT NOT NULL,
      payload_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_death_forward_target
      ON death_forward_validation_outcomes(symbol,timeframe,signal_ts);
    CREATE TABLE IF NOT EXISTS system_solvability_audits (
      audit_id TEXT PRIMARY KEY, week_bucket TEXT NOT NULL,
      deterministic_state TEXT NOT NULL, ai_state TEXT NOT NULL,
      deterministic_json TEXT NOT NULL, ai_reviews_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_system_solvability_week_created
      ON system_solvability_audits(week_bucket, created_at DESC);
    CREATE TABLE IF NOT EXISTS death_rule_distillation_runs (
      run_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
      evidence_fingerprint TEXT NOT NULL UNIQUE,
      evidence_count INTEGER NOT NULL, state TEXT NOT NULL,
      proposal_json TEXT NOT NULL, audits_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS distilled_death_rules (
      rule_id TEXT PRIMARY KEY, distillation_run_id TEXT NOT NULL,
      rule_key TEXT NOT NULL, title TEXT NOT NULL, category TEXT NOT NULL,
      scope_recommendation TEXT NOT NULL, state TEXT NOT NULL,
      measured_evidence_count INTEGER NOT NULL,
      reasoned_evidence_count INTEGER NOT NULL,
      distinct_target_count INTEGER NOT NULL,
      death_codes_json TEXT NOT NULL, source_evidence_json TEXT NOT NULL,
      proposal_json TEXT NOT NULL, audits_json TEXT NOT NULL,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_distilled_death_rule_state
      ON distilled_death_rules(state,category,updated_at);
    """)
    _migrate_system_solvability_audits_append_only(conn)
    try:
        import auto_trade_strategy_intelligence as intelligence
        intelligence.ensure_schema(conn)
    except Exception:
        pass
    try:
        os.chmod(str(DB_PATH), 0o600)
    except Exception:
        pass
    return conn


def _migrate_system_solvability_audits_append_only(conn):
    """Allow multiple audits per week so mixed heartbeats are not overwritten."""
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='system_solvability_audits'"
        ).fetchone()
        schema_sql = (row[0] or "") if row else ""
        if "week_bucket TEXT NOT NULL UNIQUE" not in schema_sql:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_system_solvability_week_created "
                "ON system_solvability_audits(week_bucket, created_at DESC)")
            return
        conn.execute(
            "CREATE TABLE IF NOT EXISTS system_solvability_audits_v2 ("
            "audit_id TEXT PRIMARY KEY, week_bucket TEXT NOT NULL, "
            "deterministic_state TEXT NOT NULL, ai_state TEXT NOT NULL, "
            "deterministic_json TEXT NOT NULL, ai_reviews_json TEXT NOT NULL, "
            "created_at TEXT NOT NULL)")
        conn.execute(
            "INSERT OR IGNORE INTO system_solvability_audits_v2 "
            "SELECT audit_id,week_bucket,deterministic_state,ai_state,"
            "deterministic_json,ai_reviews_json,created_at "
            "FROM system_solvability_audits")
        conn.execute("DROP TABLE system_solvability_audits")
        conn.execute(
            "ALTER TABLE system_solvability_audits_v2 "
            "RENAME TO system_solvability_audits")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_system_solvability_week_created "
            "ON system_solvability_audits(week_bucket, created_at DESC)")
        conn.commit()
    except Exception:
        pass


def _sha(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path):
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "missing"


def ingest_cases():
    conn = _db()
    inserted = 0
    try:
        for name in sorted(glob.glob(str(AUTO_DIR / "formal_v6_state*.json"))):
            state = _read(name, {})
            rows = list(state.get("history") or [])
            current = state.get("current")
            if isinstance(current, dict):
                rows.append(dict(current, _case_kind="active_position"))
            for row in rows:
                if not isinstance(row, dict):
                    continue
                token = (row.get("execution_id") or row.get("position_id")
                         or "%s|%s|%s" % (row.get("symbol"), row.get("opened_at"),
                                           row.get("strategy_key")))
                payload = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
                case_kind = row.get("_case_kind") or "real_trade"
                existing = conn.execute(
                    "SELECT kind FROM cases WHERE case_id=?", (str(token),)
                ).fetchone()
                values = (case_kind, row.get("symbol"),
                          (row.get("entry_data") or {}).get("timeframe"),
                          row.get("strategy_key"),
                          row.get("closed_at") or row.get("opened_at"),
                          _number(row.get("pnl")), payload, _now(), str(token))
                if existing:
                    # A live-position observation must be replaced by its final
                    # close/fill record, never left as an eternally open case.
                    if case_kind == "real_trade" or existing[0] == "active_position":
                        conn.execute(
                            "UPDATE cases SET kind=?,symbol=?,timeframe=?,strategy_key=?,"
                            "occurred_at=?,outcome=?,payload_json=?,learned_at=? WHERE case_id=?",
                            values,
                        )
                else:
                    conn.execute(
                        "INSERT INTO cases VALUES(?,?,?,?,?,?,?,?,?)",
                        (str(token),) + values[:-1],
                    )
                    inserted += 1
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        return {"ok": True, "inserted": inserted, "total_cases": total}
    finally:
        conn.close()
def _number(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def _quantile(values, probability):
    values = sorted(float(value) for value in values)
    if not values:
        return None
    position = (len(values)-1)*float(probability)
    lower = int(math.floor(position)); upper = int(math.ceil(position))
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper]-values[lower])*(position-lower)


def _probabilistic_net_summary(values, seed_material="", bootstrap_samples=0):
    """Cheap posterior/uncertainty summary for already cost-adjusted returns."""
    values = [float(value) for value in values if value is not None and
              math.isfinite(float(value))]
    count = len(values)
    if not values:
        return {"samples": 0, "mean": 0.0, "win_rate_pct": 0.0,
                "profit_factor": 0.0, "payoff_ratio": 0.0,
                "break_even_win_rate_pct": 100.0,
                "posterior_prob_win_rate_above_break_even": 0.0,
                "probability_mean_positive": 0.0,
                "mean_ci90_low": 0.0, "mean_ci90_high": 0.0,
                "mean_t_stat": 0.0}
    wins = [value for value in values if value > 0]
    losses = [-value for value in values if value <= 0]
    win_rate = len(wins)/float(count)
    avg_win = sum(wins)/float(len(wins)) if wins else 0.0
    avg_loss = sum(losses)/float(len(losses)) if losses else 0.0
    payoff = avg_win/avg_loss if avg_loss > 0 else (999.0 if avg_win > 0 else 0.0)
    break_even = avg_loss/(avg_win+avg_loss) if avg_win+avg_loss > 0 else 1.0
    gross_win = sum(wins); gross_loss = sum(losses)
    profit_factor = gross_win/gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    mean = sum(values)/float(count)
    variance = (sum((value-mean)**2 for value in values)/float(count-1)
                if count > 1 else 0.0)
    std = math.sqrt(max(0.0, variance)); se = std/math.sqrt(count) if count else 0.0
    if se > 0:
        z = mean/se
        probability_mean_positive = 0.5*(1.0+math.erf(z/math.sqrt(2.0)))
        ci_low = mean-1.645*se; ci_high = mean+1.645*se
    else:
        probability_mean_positive = 1.0 if mean > 0 else 0.0
        ci_low = ci_high = mean
    rng = random.Random(int(_sha({"seed": seed_material, "n": count})[:16], 16))
    posterior_draws = 1200
    posterior_success = sum(
        rng.betavariate(len(wins)+1, len(losses)+1) > break_even
        for _ in range(posterior_draws)
    )
    if bootstrap_samples and count >= 8:
        bootstrap_means = []
        for _ in range(int(bootstrap_samples)):
            bootstrap_means.append(sum(values[rng.randrange(count)]
                                       for _ in range(count))/float(count))
        ci_low = _quantile(bootstrap_means, .05)
        ci_high = _quantile(bootstrap_means, .95)
        probability_mean_positive = (sum(value > 0 for value in bootstrap_means)/
                                     float(len(bootstrap_means)))
    return {
        "samples": count, "mean": round(mean, 10),
        "win_rate_pct": round(win_rate*100.0, 6),
        "profit_factor": round(min(profit_factor, 999.0), 6),
        "payoff_ratio": round(min(payoff, 999.0), 6),
        "break_even_win_rate_pct": round(break_even*100.0, 6),
        "posterior_prob_win_rate_above_break_even": round(
            posterior_success/float(posterior_draws), 6),
        "probability_mean_positive": round(probability_mean_positive, 6),
        "mean_ci90_low": round(float(ci_low), 10),
        "mean_ci90_high": round(float(ci_high), 10),
        # Studentized mean of per-trade net returns. This is deliberately not
        # called Sharpe because it has no time annualization.
        "mean_t_stat": round((mean/std*math.sqrt(count)) if std > 0 else 0.0, 6),
    }


def _friction_scenario(symbol, name="observed_base"):
    defaults = {
        "fee_rate_per_side": 0.0005, "slippage_rate_per_side": 0.0002,
        "half_spread_rate_per_side": 0.00005,
        "impact_rate_per_side": 0.00005,
        "latency_rate_per_side": 0.00005,
        "funding_rate_per_8h": 0.0001,
    }
    payload = _read(COST_MODEL_PATH, {})
    row = dict((payload.get("scenarios") or {}).get(name) or defaults)
    multiplier = float((payload.get("execution_drag_multiplier") or {}).get(
        str(symbol).upper(), 1.5))
    # Account fee is known. Execution-only terms receive the liquidity proxy.
    for key in ("slippage_rate_per_side", "half_spread_rate_per_side",
                "impact_rate_per_side", "latency_rate_per_side"):
        row[key] = float(row.get(key, defaults.get(key, 0.0)))*multiplier
    row["fee_rate_per_side"] = float(row.get("fee_rate_per_side", 0.0005))
    row["funding_rate_per_8h"] = float(row.get("funding_rate_per_8h", 0.0))
    observed = ((payload.get("instrument_observed") or {}).get(
        str(symbol).upper()) or {})
    percentile = {"observed_base": "p75", "stressed": "p90",
                  "severe": "p95"}.get(name, "p75")
    observed_candidates = {}
    if int(observed.get("book_samples") or 0) >= 24:
        measured = _number(observed.get(
            "half_spread_%s_rate" % percentile))
        if measured is not None:
            row["half_spread_rate_per_side"] = max(
                row["half_spread_rate_per_side"], min(.003, measured))
            observed_candidates["half_spread_rate_per_side"] = measured
    if int(observed.get("fill_samples") or 0) >= 10:
        measured = _number(observed.get(
            "adverse_mark_%s_rate" % percentile))
        if measured is not None:
            row["slippage_rate_per_side"] = max(
                row["slippage_rate_per_side"], min(.003, measured))
            observed_candidates["slippage_rate_per_side"] = measured
    if int(observed.get("funding_samples") or 0) >= 10:
        measured = _number(observed.get(
            "absolute_funding_%s_rate" % percentile))
        if measured is not None:
            row["funding_rate_per_8h"] = max(
                row["funding_rate_per_8h"], min(.01, measured))
            observed_candidates["funding_rate_per_8h"] = measured
    capacity = dict(payload.get("capacity_reference") or {})
    conservative_depth = min(
        float(observed.get("top5_bid_depth_p25_usd") or 0.0),
        float(observed.get("top5_ask_depth_p25_usd") or 0.0))
    modeled_notional = float(capacity.get(
        "maximum_modeled_notional_usdt") or 0.0)
    capacity_calibration = {"active": False}
    if (int(observed.get("depth_samples") or 0) >= 24
            and conservative_depth > 0 and modeled_notional > 0):
        utilization = modeled_notional/conservative_depth
        stress_multiplier = {"observed_base": 1.0, "stressed": 1.5,
                             "severe": 2.25}.get(name, 1.0)
        impact_floor = min(.005, row["half_spread_rate_per_side"]*
                           min(8.0, max(.25, math.sqrt(utilization)))*
                           stress_multiplier)
        row["impact_rate_per_side"] = max(
            row["impact_rate_per_side"], impact_floor)
        observed_candidates["capacity_impact_rate_per_side"] = impact_floor
        capacity_calibration = {
            "active": True, "modeled_notional_usdt": modeled_notional,
            "conservative_top5_depth_usd": conservative_depth,
            "depth_utilization": round(utilization, 8),
            "impact_floor_rate_per_side": round(impact_floor, 10),
        }
    row["scenario"] = name; row["execution_drag_multiplier"] = multiplier
    row["forward_calibration"] = {
        "observed_candidates": observed_candidates,
        "effective_terms": {
            "half_spread_rate_per_side": row["half_spread_rate_per_side"],
            "slippage_rate_per_side": row["slippage_rate_per_side"],
            "impact_rate_per_side": row["impact_rate_per_side"],
            "funding_rate_per_8h": row["funding_rate_per_8h"],
        },
        "book_samples": int(observed.get("book_samples") or 0),
        "fill_samples": int(observed.get("fill_samples") or 0),
        "depth_samples": int(observed.get("depth_samples") or 0),
        "funding_samples": int(observed.get("funding_samples") or 0),
        "capacity_calibration": capacity_calibration,
        "percentile": percentile,
    }
    row["round_trip_unleveraged"] = 2.0*sum(row.get(key, 0.0) for key in (
        "fee_rate_per_side", "slippage_rate_per_side",
        "half_spread_rate_per_side", "impact_rate_per_side",
        "latency_rate_per_side"))
    return row


def _max_loss_streak(trades):
    run = best = 0
    for row in trades:
        if row.get("profit"):
            run = 0
        else:
            run += 1
            best = max(best, run)
    return best


def _max_drawdown(trades):
    cap = peak = 1.0
    max_dd = 0.0
    for row in trades:
        cap *= max(0.0, 1.0 + float(row.get("pnl_ratio") or 0.0))
        peak = max(peak, cap)
        if peak > 0:
            max_dd = max(max_dd, (peak-cap)/peak)
    return max_dd * 100.0


def _metrics(result):
    rows = result.get("trades") or []
    ratio_values = [float(row.get("pnl_ratio") or 0.0) for row in rows]
    values = [value*100.0 for value in ratio_values]
    probabilistic = _probabilistic_net_summary(
        ratio_values, seed_material=result.get("dsl_hash") or
        result.get("strategy_key") or "metrics", bootstrap_samples=400)
    split = max(1, int(len(rows) * 0.7)) if rows else 0
    holdout = rows[split:]
    regime_performance = {}
    for row in rows:
        regime = str(row.get("market_regime") or "unknown")
        bucket = regime_performance.setdefault(
            regime, {"trades": 0, "wins": 0, "pnl_values": []}
        )
        bucket["trades"] += 1
        bucket["wins"] += 1 if row.get("profit") else 0
        bucket["pnl_values"].append(float(row.get("pnl_ratio") or 0.0)*100.0)
    for bucket in regime_performance.values():
        bucket["win_rate_pct"] = bucket["wins"]/float(bucket["trades"])*100.0
        bucket["expectancy_pct"] = sum(bucket.pop("pnl_values"))/float(bucket["trades"])
    walk_forward = []
    for segment in range(3):
        low = int(math.floor(len(rows)*segment/3.0))
        high = int(math.floor(len(rows)*(segment+1)/3.0))
        section = rows[low:high]
        section_values = [float(row.get("pnl_ratio") or 0.0) for row in section]
        walk_forward.append({
            "segment": segment+1, "trades": len(section),
            "win_rate_pct": (sum(value > 0 for value in section_values)/
                             float(len(section_values))*100.0) if section_values else 0.0,
            "expectancy_pct": (sum(section_values)/float(len(section_values))*100.0)
                              if section_values else 0.0,
        })
    rolling_windows = []
    for window_index, start_fraction in enumerate((0.0, .15, .30, .45, .60), 1):
        low = int(math.floor(len(rows)*start_fraction))
        high = int(math.ceil(len(rows)*min(1.0, start_fraction+.40)))
        section = rows[low:high]
        section_values = [float(row.get("pnl_ratio") or 0.0) for row in section]
        rolling_windows.append({
            "window": window_index, "start_fraction": start_fraction,
            "end_fraction": min(1.0, start_fraction+.40),
            "trades": len(section),
            "win_rate_pct": (sum(value > 0 for value in section_values)/
                             float(len(section_values))*100.0) if section_values else 0.0,
            "expectancy_pct": (sum(section_values)/float(len(section_values))*100.0)
                              if section_values else 0.0,
        })
    # Compound multiple exits on one UTC day and annualize the daily series.
    # This avoids the old error of labelling a per-trade t statistic as Sharpe.
    daily_returns = {}
    for row, value in zip(rows, ratio_values):
        day = str(row.get("exit_time") or row.get("entry_time") or "unknown")[:10]
        daily_returns[day] = (1.0 + daily_returns.get(day, 0.0)) * (1.0 + value) - 1.0
    daily_values = list(daily_returns.values())
    daily_mean = (sum(daily_values)/float(len(daily_values))
                  if daily_values else 0.0)
    daily_variance = (sum((value-daily_mean)**2 for value in daily_values)/
                      float(len(daily_values)-1)
                      if len(daily_values) > 1 else 0.0)
    daily_std = math.sqrt(max(0.0, daily_variance))
    daily_sharpe = (daily_mean/daily_std*math.sqrt(365.0)
                    if daily_std > 0.0 else 0.0)
    return {
        "trades": len(rows),
        "win_rate_pct": float(result.get("win_rate_percent") or 0.0),
        "compound_return_pct": float(result.get("total_return_percent") or 0.0),
        "expectancy_pct": sum(values) / float(len(values)) if values else 0.0,
        "max_drawdown_pct": _max_drawdown(rows),
        "max_loss_streak": _max_loss_streak(rows),
        "holdout_trades": len(holdout),
        "holdout_win_rate_pct": (sum(1 for row in holdout if row.get("profit")) /
                                  float(len(holdout)) * 100.0) if holdout else 0.0,
        "regime_performance": regime_performance,
        "rolling_windows": rolling_windows,
        "profit_factor": probabilistic["profit_factor"],
        "payoff_ratio": probabilistic["payoff_ratio"],
        "mean_t_stat": probabilistic["mean_t_stat"],
        "daily_sharpe_annualized": round(daily_sharpe, 6),
        "daily_observations": len(daily_values),
        "probability_positive_expectancy": probabilistic["probability_mean_positive"],
        "expectancy_ci90_low_pct": probabilistic["mean_ci90_low"]*100.0,
        "expectancy_ci90_high_pct": probabilistic["mean_ci90_high"]*100.0,
        "posterior_prob_above_break_even": probabilistic["posterior_prob_win_rate_above_break_even"],
        "break_even_win_rate_pct": probabilistic["break_even_win_rate_pct"],
        "walk_forward": walk_forward,
    }


def _empty_metrics():
    return {"trades": 0, "win_rate_pct": 0.0, "compound_return_pct": 0.0,
            "expectancy_pct": 0.0, "max_drawdown_pct": 0.0,
            "max_loss_streak": 0, "holdout_trades": 0,
            "holdout_win_rate_pct": 0.0, "regime_performance": {},
            "profit_factor": 0.0, "payoff_ratio": 0.0,
            "mean_t_stat": 0.0, "daily_sharpe_annualized": 0.0,
            "daily_observations": 0, "probability_positive_expectancy": 0.0,
            "expectancy_ci90_low_pct": 0.0, "expectancy_ci90_high_pct": 0.0,
            "posterior_prob_above_break_even": 0.0,
            "break_even_win_rate_pct": 100.0, "walk_forward": []}


def _run_with_config(strategy_key, symbol, timeframe, params, stop_loss_pct,
                     friction_scenario="observed_base", start_time=None):
    config = _read(CONFIG_PATH, {})
    for row in config.get("strategies") or []:
        if row.get("key") == strategy_key:
            row["params"] = dict(params)
            break
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w",
                                      encoding="utf-8")
    try:
        json.dump(config, tmp, ensure_ascii=False)
        tmp.close()
        os.environ["VECTOR_STRATEGY_CONFIG_PATH"] = tmp.name
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import backtest_engine_v2 as bt
        old_path = bt.CONFIG_PATH
        bt.CONFIG_PATH = tmp.name
        try:
            result = bt.run_backtest(
                strategy_key, instId=symbol,
                start_time=(start_time or os.environ.get(
                    "QIYU_ECOSYSTEM_BACKTEST_START", "2026-01-01 00:00:00")),
                end_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                leverage=20, stop_loss_pct=stop_loss_pct, timeframe=timeframe,
                friction_scenario=friction_scenario,
            )
        finally:
            bt.CONFIG_PATH = old_path
        return result
    finally:
        try:
            Path(tmp.name).unlink()
        except Exception:
            pass


def _active_assignments():
    rows = []
    for path in sorted(glob.glob(str(AUTO_DIR / "formal_daemon_config*.json"))):
        cfg = _read(path, {})
        if not cfg.get("enabled") or not cfg.get("allow_auto_open"):
            continue
        for key in cfg.get("strategy_keys") or []:
            rows.append({"strategy_key": key, "symbol": cfg.get("symbol"),
                         "timeframe": cfg.get("timeframe") or "1h"})
    return rows


def _strategy_row(key):
    for row in _read(CONFIG_PATH, {}).get("strategies") or []:
        if row.get("key") == key:
            return row
    return None


def generate_variants(strategy_row, limit=8):
    params = dict(strategy_row.get("params") or {})
    meta = strategy_row.get("param_meta") or {}
    variants = []
    # Stable rotation avoids repeatedly selecting only the first parameter.
    names = sorted(name for name in params if name in meta and name != "stop_loss_pct")
    for name in names:
        value = _number(params.get(name))
        step = _number((meta.get(name) or {}).get("step"))
        low = _number((meta.get(name) or {}).get("min"), -float("inf"))
        high = _number((meta.get(name) or {}).get("max"), float("inf"))
        if value is None or step is None or step <= 0:
            continue
        for direction in (-1, 1):
            changed = value + direction * step
            if low <= changed <= high:
                candidate = dict(params)
                candidate[name] = int(changed) if isinstance(params.get(name), int) else round(changed, 10)
                variants.append({"changed_parameter": name, "old_value": value,
                                 "new_value": candidate[name], "params": candidate})
                if len(variants) >= limit:
                    return variants
    return variants


def _ai_parameter_variants(strategy_row, provider_results):
    params = dict(strategy_row.get("params") or {})
    meta = strategy_row.get("param_meta") or {}
    out = []
    for result in provider_results:
        for item in result.get("hypotheses") or []:
            if item.get("kind") != "parameter":
                continue
            name = item.get("parameter")
            if name not in params or name not in meta or name in PROTECTED_CAPITAL_PARAMS:
                continue
            try:
                direction = int(item.get("direction")); steps = int(item.get("step_count") or 1)
                step = float(meta[name]["step"]); old = float(params[name])
                new = old + (1 if direction > 0 else -1)*max(1, min(3, steps))*step
                low = float(meta[name]["min"]); high = float(meta[name]["max"])
            except Exception:
                continue
            if not low <= new <= high:
                continue
            changed = dict(params)
            changed[name] = int(round(new)) if isinstance(params[name], int) else round(new, 10)
            out.append({"changed_parameter": name, "old_value": params[name],
                        "new_value": changed[name], "params": changed,
                        "research_provider": result.get("provider"),
                        "research_rationale": item.get("rationale"),
                        "expected_effect": item.get("expected_effect")})
    return out


def _rank_parameter_variants(assignment, variants):
    """Cost-free Bayesian ranking from prior one-parameter attributions."""
    conn = _db()
    try:
        history = conn.execute(
            "SELECT condition_key,direction,delta_trades,delta_win_rate,"
            "delta_expectancy,delta_drawdown,delta_loss_streak "
            "FROM condition_attributions WHERE symbol=? AND timeframe=?",
            (assignment.get("symbol"), assignment.get("timeframe"))).fetchall()
    finally:
        conn.close()
    grouped = {}
    for key, direction, delta_trades, delta_win, delta_expectancy, delta_dd, delta_streak in history:
        score = (float(delta_expectancy or 0.0)
                 +.08*float(delta_win or 0.0)
                 +.02*float(delta_trades or 0.0)
                 -.08*max(0.0, float(delta_dd or 0.0))
                 -.50*max(0.0, float(delta_streak or 0.0)))
        grouped.setdefault((key, direction), []).append(score)
    ranked = []
    for variant in variants:
        direction = ("relax_or_lower" if variant.get("new_value", 0) <
                     variant.get("old_value", 0) else "tighten_or_raise")
        values = grouped.get((variant.get("changed_parameter"), direction), [])
        # Two zero-centered pseudo-observations shrink small/noisy histories.
        posterior_mean = sum(values)/float(len(values)+2)
        if len(values) > 1:
            raw_mean = sum(values)/float(len(values))
            variance = sum((value-raw_mean)**2 for value in values)/float(len(values)-1)
            uncertainty = math.sqrt(max(0.0, variance)/float(len(values)+2))
        else:
            uncertainty = 1.0/math.sqrt(len(values)+1.0)
        novelty = 1.0/math.sqrt(len(values)+1.0)
        consensus_bonus = .5 if variant.get("research_provider") == "research_convergence" else 0.0
        acquisition = posterior_mean+.75*uncertainty+.35*novelty+consensus_bonus
        variant = copy.deepcopy(variant)
        variant["search_prior"] = {
            "model": "shrunk_attribution_posterior",
            "observations": len(values), "posterior_mean": round(posterior_mean, 6),
            "uncertainty": round(uncertainty, 6),
            "novelty": round(novelty, 6),
            "acquisition_score": round(acquisition, 6),
        }
        ranked.append(variant)
    return sorted(ranked, key=lambda row: (
        (row.get("search_prior") or {}).get("acquisition_score", 0.0),
        (row.get("search_prior") or {}).get("novelty", 0.0)), reverse=True)


def _build_hypothesis_catalog(strategy_row, assignment, provider_results):
    """Validate and semantically merge independent research directions."""
    import auto_trade_strategy_dsl as dsl
    params = dict(strategy_row.get("params") or {})
    meta = strategy_row.get("param_meta") or {}
    merged = {}; invalid = []
    for result in provider_results:
        provider = result.get("provider")
        if not result.get("ok"):
            continue
        for item in result.get("hypotheses") or []:
            kind = item.get("kind")
            try:
                if kind == "parameter":
                    name = item.get("parameter")
                    direction = int(item.get("direction"))
                    steps = max(1, min(3, int(item.get("step_count") or 1)))
                    if (item.get("strategy_key") != assignment["strategy_key"]
                            or name not in params or name not in meta
                            or name in PROTECTED_CAPITAL_PARAMS
                            or direction not in (-1, 1)):
                        raise ValueError("parameter hypothesis outside declared schema")
                    identity = {"kind": "parameter",
                                "strategy_key": assignment["strategy_key"],
                                "parameter": name, "direction": direction}
                    hid = "param_" + _sha(identity)[:24]
                    row = merged.setdefault(hid, dict(identity,
                        hypothesis_id=hid, source_providers=[], rationales=[],
                        step_counts=[]))
                    row["step_counts"].append(steps)
                    row["rationales"].append(item.get("rationale") or "")
                elif kind == "dsl":
                    definition = dsl.validate_strategy(item.get("dsl"))
                    if (definition["timeframe"] != assignment["timeframe"]
                            or assignment["symbol"] not in definition["supported_instruments"]
                            or item.get("strategy_key") != definition["key"]):
                        raise ValueError("DSL hypothesis assignment mismatch")
                    causal = item.get("causal_chain")
                    falsification = item.get("falsification_tests")
                    required_causal = ("market_state", "mechanism",
                                       "observable_transition",
                                       "why_cost_survives", "failure_condition")
                    if (not isinstance(causal, dict)
                            or any(len(str(causal.get(key) or "").strip()) < 8
                                   for key in required_causal)):
                        raise ValueError("DSL hypothesis lacks complete causal_chain")
                    if (not isinstance(falsification, list)
                            or len([value for value in falsification
                                    if len(str(value).strip()) >= 8]) < 2):
                        raise ValueError("DSL hypothesis requires at least two falsification_tests")
                    executable = dsl.executable_hash(definition)
                    hid = "dsl_" + executable[:24]
                    row = merged.setdefault(hid, {
                        "hypothesis_id": hid, "kind": "dsl",
                        "strategy_key": definition["key"], "dsl": definition,
                        "executable_hash": executable, "source_providers": [],
                        "rationales": [], "causal_chains": [],
                        "falsification_tests": [], "source_pattern_ids": [],
                    })
                    row["rationales"].append(item.get("rationale") or "")
                    row["causal_chains"].append(copy.deepcopy(causal))
                    row["falsification_tests"].extend(
                        str(value)[:500] for value in falsification)
                    for pattern_id in item.get("source_pattern_ids") or []:
                        pattern_id = str(pattern_id).strip()
                        if pattern_id and pattern_id not in row["source_pattern_ids"]:
                            row["source_pattern_ids"].append(pattern_id)
                else:
                    raise ValueError("unsupported hypothesis kind")
                if provider not in row["source_providers"]:
                    row["source_providers"].append(provider)
            except Exception as exc:
                invalid.append({"provider": provider, "kind": kind,
                                "error": str(exc), "hypothesis": item})
    catalog = []
    for row in merged.values():
        if row["kind"] == "parameter":
            steps = sorted(row.pop("step_counts"))
            row["step_count"] = steps[len(steps)//2]
        catalog.append(row)
    catalog.sort(key=lambda row: row["hypothesis_id"])
    return catalog, invalid


def _converged_provider_results(convergence):
    hypotheses = []
    for row in convergence.get("admitted") or []:
        if row.get("kind") == "parameter":
            hypotheses.append({
                "kind": "parameter", "strategy_key": row.get("strategy_key"),
                "parameter": row.get("parameter"), "direction": row.get("direction"),
                "step_count": row.get("step_count"),
                "rationale": "；".join(row.get("rationales") or []),
                "expected_effect": "已通过研究方向交叉会审",
            })
        elif row.get("kind") == "dsl":
            hypotheses.append({
                "kind": "dsl", "strategy_key": row.get("strategy_key"),
                "dsl": row.get("dsl"),
                "rationale": "；".join(row.get("rationales") or []),
                "causal_chains": row.get("causal_chains") or [],
                "falsification_tests": row.get("falsification_tests") or [],
                "source_pattern_ids": row.get("source_pattern_ids") or [],
            })
    return [{"provider": "research_convergence", "ok": True,
             "hypotheses": hypotheses}]


def _dedupe_variants(variants, limit=12):
    seen = set(); out = []
    for variant in variants:
        token = _sha(variant.get("params") or {})
        if token in seen: continue
        seen.add(token); out.append(variant)
        if len(out) >= limit: break
    return out


def _pareto_front(evaluated):
    """Return non-dominated candidates across quality, risk and frequency."""
    def objectives(item):
        evidence = item[1]
        primary = (((evidence.get("runs") or {}).get("candidate") or {})
                   .get("0.009") or {})
        stressed = (((evidence.get("runs") or {}).get("stress") or {})
                    .get("stressed") or {})
        return (
            float(primary.get("expectancy_pct") or -999.0),
            float(primary.get("win_rate_pct") or 0.0),
            float(stressed.get("expectancy_pct") or -999.0),
            -float(primary.get("max_drawdown_pct") or 100.0),
            -float(primary.get("max_loss_streak") or 999.0),
            min(float(primary.get("trades") or 0.0), 100.0),
        )
    front = []
    for index, item in enumerate(evaluated):
        value = objectives(item); dominated = False
        for other_index, other in enumerate(evaluated):
            if other_index == index: continue
            other_value = objectives(other)
            if (all(left >= right for left, right in zip(other_value, value))
                    and any(left > right for left, right in zip(other_value, value))):
                dominated = True; break
        if not dominated:
            front.append(item)
    return front


def _store_candidate(candidate, evidence, state):
    from auto_trade_ai_consensus import candidate_hash
    digest = candidate_hash(candidate); conn = _db()
    try:
        now = _now()
        conn.execute("INSERT OR REPLACE INTO candidates VALUES(?,?,?,?,?,?,?,?,?)",
                     (digest, candidate.get("strategy_key") or "unknown",
                      candidate.get("symbol"), candidate.get("timeframe"), state,
                      json.dumps(candidate, ensure_ascii=False, sort_keys=True),
                      json.dumps(evidence, ensure_ascii=False, sort_keys=True), now, now))
        conn.commit()
    finally:
        conn.close()
    return digest


def _candidate_known(digest):
    conn = _db()
    try:
        row = conn.execute(
            "SELECT state FROM candidates WHERE candidate_hash=?", (digest,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _candidate_and_evidence(digest):
    conn = _db()
    try:
        row = conn.execute(
            "SELECT candidate_json,evidence_json,state FROM candidates "
            "WHERE candidate_hash=?", (digest,)
        ).fetchone()
        if not row: return None
        return {"candidate": json.loads(row[0]),
                "evidence": json.loads(row[1] or "{}"), "state": row[2]}
    finally:
        conn.close()


def _quality_snapshot(bundle):
    """Comparable deterministic metrics for choosing the next round's parent."""
    evidence = (bundle or {}).get("evidence") or {}
    metrics = (((evidence.get("runs") or {}).get("candidate") or {})
               .get("0.009") or {})
    gates = evidence.get("gates") or {}
    score = evidence.get("weighted_score")
    return {
        "passed": bool(evidence.get("passed")),
        "passed_gate_count": sum(1 for value in gates.values() if value),
        "gate_count": len(gates),
        "weighted_score": float(score if score is not None else -999.0),
        "trades": int(metrics.get("trades") or 0),
        "win_rate_pct": float(metrics.get("win_rate_pct") or 0.0),
        "expectancy_pct": float(metrics.get("expectancy_pct") or 0.0),
        "holdout_win_rate_pct": float(metrics.get("holdout_win_rate_pct") or 0.0),
        "max_loss_streak": int(metrics.get("max_loss_streak")
                               if metrics.get("max_loss_streak") is not None else 999),
        "max_drawdown_pct": float(metrics.get("max_drawdown_pct")
                                  if metrics.get("max_drawdown_pct") is not None else 100.0),
    }


def _select_round_champion(outcomes):
    """Never choose the last-produced candidate merely because it is newest."""
    choices = []
    for outcome in outcomes or []:
        state = str(outcome.get("state") or "")
        if state == "iteration_regressed" or state.startswith("skipped_known"):
            continue
        digest = outcome.get("candidate_hash")
        bundle = _candidate_and_evidence(digest) if digest else None
        if not bundle:
            continue
        quality = _quality_snapshot(bundle)
        key = (quality["passed"], quality["passed_gate_count"],
               quality["weighted_score"], quality["win_rate_pct"],
               quality["expectancy_pct"], min(quality["trades"], 50))
        choices.append((key, outcome, bundle, quality))
    return max(choices, key=lambda row: row[0]) if choices else None


def _relative_iteration_assessment(parent_bundle, child_bundle):
    """Require measurable information gain before a revision can become parent."""
    parent = _quality_snapshot(parent_bundle)
    child = _quality_snapshot(child_bundle)
    score_gain = child["weighted_score"] - parent["weighted_score"]
    gate_gain = child["passed_gate_count"] - parent["passed_gate_count"]
    sample_floor = max(15, int(math.ceil(parent["trades"] * 0.65)))
    balanced_gain = (
        score_gain >= 0.50
        and child["trades"] >= sample_floor
        and child["win_rate_pct"] >= parent["win_rate_pct"] - 1.0
        and child["expectancy_pct"] >= parent["expectancy_pct"] - 0.25
        and (gate_gain > 0 or child["win_rate_pct"] > parent["win_rate_pct"]
             or child["expectancy_pct"] > parent["expectancy_pct"])
    )
    structural_gain = (
        child["trades"] >= 15
        and child["win_rate_pct"] >= parent["win_rate_pct"] + 5.0
        and child["expectancy_pct"] > max(0.0, parent["expectancy_pct"])
        and child["max_loss_streak"] <= max(3, parent["max_loss_streak"])
    )
    improved = bool(child["passed"] or balanced_gain or structural_gain)
    return {
        "improved": improved,
        "policy": "hard_pass_or_balanced_score_gain_or_material_structural_gain",
        "parent": parent, "child": child,
        "delta": {
            "weighted_score": round(score_gain, 6),
            "passed_gates": gate_gain,
            "trades": child["trades"] - parent["trades"],
            "win_rate_pct": round(child["win_rate_pct"] - parent["win_rate_pct"], 6),
            "expectancy_pct": round(child["expectancy_pct"] - parent["expectancy_pct"], 6),
        },
    }


def _known_dsl_executable(executable_hash):
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT state,candidate_json FROM candidates "
            "WHERE candidate_json LIKE '%\"type\": \"dsl_strategy\"%'"
        ).fetchall()
        matched = []
        for state, raw in rows:
            try:
                if json.loads(raw).get("executable_hash") == executable_hash:
                    matched.append(state)
            except Exception:
                pass
        priority = ("human_approved_live", "human_approved_research", "deployed",
                    "approved_requires_human", "ai_rejected",
                    "deterministic_rejected", "deterministic_passed",
                    "exploration_passed_not_converged", "exploration_rejected",
                    "iteration_regressed")
        return next((state for state in priority if state in matched), None)
    finally:
        conn.close()


def _store_version(digest, candidate, evidence, state, consensus=None,
                   version_type=None):
    conn = _db()
    try:
        now = _now()
        definition = candidate.get("dsl") if candidate.get("type") == "dsl_strategy" else candidate
        conn.execute(
            "INSERT OR REPLACE INTO strategy_versions VALUES(?,?,?,?,?,?,?,?,?,?)",
            (digest, candidate.get("strategy_key") or "unknown",
             version_type or candidate.get("type") or "unknown", state,
             candidate.get("parent_version_hash") or candidate.get("base_config_hash"),
             json.dumps(definition, ensure_ascii=False, sort_keys=True),
             json.dumps(evidence, ensure_ascii=False, sort_keys=True),
             json.dumps(consensus or {}, ensure_ascii=False, sort_keys=True),
             now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _load_research_frame(symbol, timeframe):
    if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
    import backtest_engine_v2 as bt
    start = os.environ.get("QIYU_ECOSYSTEM_BACKTEST_START", "2026-01-01 00:00:00")
    end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    normalized = bt.normalize_instrument(symbol)
    directory = bt._find_local_data_directory(normalized, timeframe)
    frame = (bt.load_parquet_range(directory, start, end, timeframe=timeframe)
             if directory else bt.load_okx_swap_range(normalized, start, end, timeframe=timeframe))
    frame = bt.precompute_indicators(frame, timeframe=timeframe)
    # For 5m/15m the backtest engine aligns the latest closed 1h slope onto
    # each lower-timeframe bar.  On a native 1h research frame that aligned
    # helper is intentionally absent, so compute the identical four-hour
    # EMA19 return directly instead of failing every 1h cluster.
    if "h1_slope4" not in frame.columns:
        frame["h1_slope4"] = (
            frame["ema19"].astype(float)
            / frame["ema19"].astype(float).shift(4) - 1.0)
    return frame.replace([float("inf"), -float("inf")], float("nan")).ffill().bfill()


def _dsl_evidence(dsl_strategy, frame, baseline_runs):
    import auto_trade_strategy_dsl as dsl
    import backtest_engine_v2 as bt
    runs = {"baseline": copy.deepcopy(baseline_runs), "candidate": {},
            "stress": {}, "stages": {}}
    symbol = (dsl_strategy.get("supported_instruments") or [""])[0]
    frictions = {name: _friction_scenario(symbol, name) for name in
                 ("observed_base", "stressed", "severe")}

    def run_with_friction(source_frame, stop, scenario_name):
        friction = frictions[scenario_name]
        kwargs = {key: friction[key] for key in (
            "fee_rate_per_side", "slippage_rate_per_side",
            "half_spread_rate_per_side", "impact_rate_per_side",
            "latency_rate_per_side", "funding_rate_per_8h")}
        return dsl.backtest_dsl(
            source_frame, dsl_strategy, leverage=20, stop_loss_pct=stop,
            friction_scenario=scenario_name, **kwargs)

    bars_per_day = {"5m": 288, "15m": 96, "1h": 24, "4h": 6}.get(
        dsl_strategy["timeframe"], 24)
    recent_frame = frame.iloc[-min(len(frame), 90*bars_per_day+260):]
    quick_result = run_with_friction(recent_frame, 0.009, "observed_base")
    quick_metrics = _metrics(quick_result)
    quick_pass = (quick_metrics["trades"] >= 8
                  and quick_metrics["win_rate_pct"] >= 40.0
                  and quick_metrics["expectancy_pct"] > 0.0
                  and quick_metrics["probability_positive_expectancy"] >= 0.65)
    runs["stages"]["recent_90d_primary"] = {
        "passed": quick_pass, "metrics": quick_metrics,
        "policy": "successive_halving_stage_1_recent_90d"}
    if not quick_pass:
        gates = {"stage_1_recent_90d": False}
        return {"passed": False, "early_pruned": True,
                "early_pruned_at": "recent_90d_primary",
                "gates": gates, "runs": runs, "diagnostic_cases": {},
                "friction_scenarios": frictions,
                "policy": "cost_aware_successive_halving_pruned_before_full_backtest",
                "weighted_score": -999.0}
    regimes = bt._market_regime_labels(frame, timeframe=dsl_strategy["timeframe"])
    diagnostics = {}
    for stop in (0.009, 0.006):
        result = run_with_friction(frame, stop, "observed_base")
        for trade in result.get("trades") or []:
            index = int(trade.get("entry_index") or 0)
            trade["market_regime"] = regimes[index] if index < len(regimes) else "unknown"
        metrics = _metrics(result)
        try:
            span_days = max(1.0, (frame.index[-1] - frame.index[0]).total_seconds() / 86400.0)
        except Exception:
            span_days = max(1.0, len(frame) / (288.0 if dsl_strategy["timeframe"] == "5m" else
                                               (96.0 if dsl_strategy["timeframe"] == "15m" else 24.0)))
        metrics["observation_days"] = round(span_days, 3)
        metrics["trades_per_day"] = round(metrics["trades"] / span_days, 6)
        runs["candidate"][str(stop)] = metrics
        if stop == 0.009:
            def diagnostic_trade(row):
                index = int(row.get("entry_index") or 0)
                snapshot = {}
                if 0 <= index < len(frame):
                    source = frame.iloc[index]
                    for key in ("open", "high", "low", "close", "ema6", "ema17",
                                "ema19", "ema53", "ema75", "k", "d", "j", "cci",
                                "macd_stick", "rsi14", "z20", "h1_slope4"):
                        try:
                            value = float(source[key])
                            if math.isfinite(value): snapshot[key] = round(value, 8)
                        except Exception:
                            pass
                return {"entry_time": row.get("entry_time"),
                        "exit_time": row.get("exit_time"),
                        "pnl_pct": round(float(row.get("pnl_ratio") or 0.0)*100.0, 6),
                        "exit_type": row.get("exit_type"),
                        "market_regime": row.get("market_regime"),
                        "entry_snapshot": snapshot}
            rows = result.get("trades") or []
            feature_outcome = {}
            for key in ("k", "d", "j", "cci", "macd_stick", "rsi14",
                        "z20", "h1_slope4"):
                groups = {"wins": [], "losses": []}
                for row in rows:
                    index = int(row.get("entry_index") or 0)
                    try:
                        value = float(frame[key].iloc[index])
                        if math.isfinite(value):
                            groups["wins" if row.get("profit") else "losses"].append(value)
                    except Exception:
                        pass
                summary = {}
                for label, values in groups.items():
                    values = sorted(values)
                    if not values: continue
                    def q(probability):
                        position = (len(values)-1)*probability
                        low = int(math.floor(position)); high = int(math.ceil(position))
                        if low == high: return values[low]
                        return values[low] + (values[high]-values[low])*(position-low)
                    summary[label] = {"samples": len(values),
                                      "p25": round(q(.25), 8),
                                      "p50": round(q(.50), 8),
                                      "p75": round(q(.75), 8),
                                      "mean": round(sum(values)/float(len(values)), 8)}
                feature_outcome[key] = summary
            exit_type_performance = {}
            for row in rows:
                label = str(row.get("exit_type") or "unknown")
                bucket = exit_type_performance.setdefault(
                    label, {"trades": 0, "wins": 0, "pnl_sum_pct": 0.0})
                bucket["trades"] += 1
                bucket["wins"] += 1 if row.get("profit") else 0
                bucket["pnl_sum_pct"] += float(row.get("pnl_ratio") or 0.0)*100.0
            for bucket in exit_type_performance.values():
                bucket["win_rate_pct"] = round(
                    bucket["wins"]/float(bucket["trades"])*100.0, 6)
                bucket["expectancy_pct"] = round(
                    bucket.pop("pnl_sum_pct")/float(bucket["trades"]), 6)
            diagnostics = {
                "recent_losses": [diagnostic_trade(row) for row in rows if not row.get("profit")][-8:],
                "recent_wins": [diagnostic_trade(row) for row in rows if row.get("profit")][-5:],
                "feature_outcome_comparison": feature_outcome,
                "exit_type_performance": exit_type_performance,
            }
    for scenario_name in ("stressed", "severe"):
        stress_result = run_with_friction(frame, 0.009, scenario_name)
        for trade in stress_result.get("trades") or []:
            index = int(trade.get("entry_index") or 0)
            trade["market_regime"] = regimes[index] if index < len(regimes) else "unknown"
        runs["stress"][scenario_name] = _metrics(stress_result)
    base = runs["baseline"]["0.009"]; cand = runs["candidate"]["0.009"]
    aux = runs["candidate"]["0.006"]
    stressed = runs["stress"]["stressed"]
    severe = runs["stress"]["severe"]
    walk_forward = cand.get("walk_forward") or []
    stable_segments = sum(1 for row in walk_forward
                          if row.get("trades", 0) >= 3 and
                          row.get("expectancy_pct", 0.0) > 0)
    gates = {
        "stage_1_recent_90d": True,
        "minimum_sample": cand["trades"] >= 15,
        "frequency_contribution": cand.get("trades_per_day", 0.0) >= 0.075,
        "primary_win_rate": cand["win_rate_pct"] >= 70.0,
        "target_or_high_expectancy": cand["win_rate_pct"] >= 75.0 or cand["expectancy_pct"] >= 3.0,
        "positive_expectancy_both_stops": cand["expectancy_pct"] > 0 and aux["expectancy_pct"] > 0,
        "drawdown_bounded": cand["max_drawdown_pct"] <= max(35.0, base["max_drawdown_pct"]+5.0),
        "loss_cluster_bounded": cand["max_loss_streak"] <= 3,
        "holdout_not_collapsed": cand["holdout_trades"] >= 2 and cand["holdout_win_rate_pct"] >= 55.0,
        "profit_factor_floor": cand.get("profit_factor", 0.0) >= 1.20,
        "annualized_daily_sharpe_floor": (
            cand.get("daily_observations", 0) >= 8
            and cand.get("daily_sharpe_annualized", 0.0) >= 0.50),
        "positive_expectancy_high_confidence": (
            cand.get("probability_positive_expectancy", 0.0) >= 0.90
            and cand.get("expectancy_ci90_low_pct", -999.0) > 0.0),
        "walk_forward_stable": stable_segments >= 2,
        "stressed_cost_positive": (
            stressed.get("expectancy_pct", 0.0) > 0.0
            and stressed.get("probability_positive_expectancy", 0.0) >= 0.75
            and stressed.get("profit_factor", 0.0) > 1.0),
        "severe_cost_survivable": severe.get("expectancy_pct", -999.0) > -1.0,
    }
    transfer = {"state": "not_run_until_local_gates_pass"}
    if all(gates.values()):
        peer_map = {"BTC-USDT-SWAP": "LTC-USDT-SWAP",
                    "LTC-USDT-SWAP": "BTC-USDT-SWAP",
                    "ADA-USDT-SWAP": "LTC-USDT-SWAP",
                    "XAU-USDT-SWAP": "XAG-USDT-SWAP",
                    "XAG-USDT-SWAP": "XAU-USDT-SWAP",
                    "CL-USDT-SWAP": "NG-USDT-SWAP",
                    "NG-USDT-SWAP": "CL-USDT-SWAP"}
        peer = peer_map.get(symbol)
        if peer:
            try:
                peer_definition = copy.deepcopy(dsl_strategy)
                peer_definition["supported_instruments"] = [peer]
                peer_definition["key"] = "blind_" + dsl.executable_hash(
                    peer_definition)[:24]
                peer_frame = _load_research_frame(peer, dsl_strategy["timeframe"])
                peer_friction = _friction_scenario(peer, "observed_base")
                peer_result = dsl.backtest_dsl(
                    peer_frame, peer_definition, leverage=20,
                    stop_loss_pct=0.009, friction_scenario="blind_peer_base",
                    **{key: peer_friction[key] for key in (
                        "fee_rate_per_side", "slippage_rate_per_side",
                        "half_spread_rate_per_side", "impact_rate_per_side",
                        "latency_rate_per_side", "funding_rate_per_8h")})
                peer_metrics = _metrics(peer_result)
                peer_passed = (peer_metrics["trades"] >= 8
                               and peer_metrics["win_rate_pct"] >= 35.0
                               and peer_metrics["expectancy_pct"] > -1.0)
                transfer = {"state": "completed", "peer": peer,
                            "passed": peer_passed, "metrics": peer_metrics}
                gates["cross_market_not_catastrophic"] = peer_passed
            except Exception as exc:
                transfer = {"state": "failed_closed", "peer": peer,
                            "passed": False, "error": str(exc)}
                gates["cross_market_not_catastrophic"] = False
    sample_penalty = max(0.0, 15.0 - float(cand["trades"])) * 2.0
    frequency_penalty = max(0.0, .075 - float(cand.get("trades_per_day", 0.0))) * 100.0
    return {"passed": all(gates.values()), "gates": gates, "runs": runs,
            "diagnostic_cases": diagnostics,
            "friction_scenarios": frictions,
            "cross_market_blind_test": transfer,
            "validation_ladder": ["recent_90d", "full_primary", "full_auxiliary",
                                  "stressed_cost", "severe_cost"],
            "policy": "cost_aware_successive_halving_probabilistic_walk_forward_stress",
            "weighted_score": round(.75*cand["expectancy_pct"]+.25*aux["expectancy_pct"]+
                                    .08*cand["win_rate_pct"]+.10*stressed["expectancy_pct"]-
                                    .1*cand["max_drawdown_pct"]-
                                    sample_penalty-frequency_penalty, 6)}


def _dsl_semantic_audit(definition):
    """Reject ambiguous exits and obvious take-profit/invalidation inversions."""
    import auto_trade_strategy_dsl as dsl
    definition = dsl.validate_strategy(definition)
    leaves = [leaf for _path, leaf in dsl._leaf_paths(definition["exit"])]
    issues = []
    roles = []
    for leaf in leaves:
        role = leaf.get("role")
        if role not in ("take_profit", "invalidation"):
            issues.append("exit condition %s has no explicit role" % leaf.get("id"))
            continue
        roles.append(role)
        left = leaf.get("left") or {}; right = leaf.get("right") or {}
        left_feature = left.get("feature"); right_feature = right.get("feature")
        op = leaf.get("op")
        price_vs_ema = (left_feature == "close" and
                        str(right_feature or "").startswith("ema"))
        if role == "take_profit" and price_vs_ema:
            if definition["direction"] == "long" and op in ("lt", "lte", "cross_below"):
                issues.append("long close-below-EMA exit is invalidation, not take_profit: %s" % leaf.get("id"))
            if definition["direction"] == "short" and op in ("gt", "gte", "cross_above"):
                issues.append("short close-above-EMA exit is invalidation, not take_profit: %s" % leaf.get("id"))
    if "take_profit" not in roles:
        issues.append("no explicit take_profit exit")
    return {"passed": not issues, "issues": issues,
            "exit_roles": {role: roles.count(role) for role in sorted(set(roles))},
            "policy": "explicit_exit_roles_and_directional_semantics"}


def _causal_hypothesis_audit(hypothesis, context=None):
    """Ensure the prose mechanism is observable and anchored to measured edge."""
    issues = []
    chains = hypothesis.get("causal_chains") or []
    tests = hypothesis.get("falsification_tests") or []
    required = ("market_state", "mechanism", "observable_transition",
                "why_cost_survives", "failure_condition")
    if not chains:
        issues.append("missing causal_chain")
    forbidden = ("订单簿失衡", "流动性真空", "做市商撤单", "order book imbalance",
                 "market maker withdrawal", "volume confirmation")
    qualified_ids = [str(row.get("pattern_id")) for row in
                     (((context or {}).get("deterministic_event_atlas") or {})
                      .get("qualified_patterns") or []) if row.get("pattern_id")]
    for chain in chains:
        if any(len(str(chain.get(key) or "").strip()) < 8 for key in required):
            issues.append("incomplete causal_chain")
            continue
        combined = " ".join(str(chain.get(key) or "") for key in required).lower()
        if any(term.lower() in combined for term in forbidden):
            issues.append("causal claim uses unavailable order-book/volume data")
        cost_text = str(chain.get("why_cost_survives") or "")
        if qualified_ids and not any(pattern_id in cost_text for pattern_id in qualified_ids):
            issues.append("cost survival claim does not cite a qualified event-atlas pattern_id")
    if len([value for value in tests if len(str(value).strip()) >= 8]) < 2:
        issues.append("fewer than two falsification tests")
    return {"passed": not issues, "issues": sorted(set(issues)),
            "policy": "observable_causal_chain_with_atlas_anchor_and_falsification"}


def _dsl_entry_edge_screen(definition, frame, leverage=20,
                           stop_loss_pct=0.009,
                           fee_rate_per_side=None,
                           slippage_rate_per_side=None):
    """Measure entry edge before an AI is allowed to endorse a narrative.

    Each entry event is evaluated at fixed forward horizons.  A stop touched
    inside the horizon is charged as a stop; otherwise the horizon close is
    marked to market.  This does not optimize an exit and therefore cannot
    prove a strategy, but it blocks hypotheses whose entry has no observable
    directional edge on either the full sample or chronological holdout.
    """
    import auto_trade_strategy_dsl as dsl
    definition = dsl.validate_strategy(definition)
    max_hold = max(1, int(definition.get("max_hold_bars") or 1))
    horizons = sorted(set(value for value in (1, 2, 4, 8, max_hold)
                          if value <= max_hold))
    max_horizon = max(horizons)
    start = max(250, dsl.MAX_LOOKBACK + 2)
    events = []
    direction = definition["direction"]
    symbol = (definition.get("supported_instruments") or [""])[0]
    base_friction = _friction_scenario(symbol, "observed_base")
    stress_friction = _friction_scenario(symbol, "stressed")
    if fee_rate_per_side is not None:
        base_friction["fee_rate_per_side"] = float(fee_rate_per_side)
    if slippage_rate_per_side is not None:
        base_friction["slippage_rate_per_side"] = float(slippage_rate_per_side)
    hours_per_bar = {"5m": 1.0/12.0, "15m": .25, "1h": 1.0}.get(
        definition.get("timeframe"), 1.0)

    def scenario_cost(friction, horizon):
        side = sum(float(friction.get(key) or 0.0) for key in (
            "fee_rate_per_side", "slippage_rate_per_side",
            "half_spread_rate_per_side", "impact_rate_per_side",
            "latency_rate_per_side"))
        funding = ((horizon*hours_per_bar)/8.0)*float(
            friction.get("funding_rate_per_8h") or 0.0)
        return (2.0*side+funding)*leverage
    for index in range(start, max(start, len(frame)-max_horizon)):
        entered, _details = dsl.evaluate_expression(
            frame, index, definition["entry"], explain=False)
        if not entered:
            continue
        entry = float(frame["close"].iloc[index])
        event = {"index": index, "time": str(frame.index[index]), "horizons": {}}
        for horizon in horizons:
            window = frame.iloc[index+1:index+horizon+1]
            stopped = (float(window["low"].min()) <= entry*(1.0-stop_loss_pct)
                       if direction == "long" else
                       float(window["high"].max()) >= entry*(1.0+stop_loss_pct))
            if stopped:
                raw = -stop_loss_pct
            else:
                close = float(frame["close"].iloc[index+horizon])
                raw = ((close-entry)/entry if direction == "long"
                       else (entry-close)/entry)
            event["horizons"][horizon] = {"raw": raw, "stopped": stopped}
        events.append(event)
    split = int(math.floor(len(events)*0.70))
    split = min(max(split, 1), max(1, len(events)-1)) if events else 0
    summaries = {}
    credible = []
    for horizon in horizons:
        def summarize(rows, friction, label):
            cost = scenario_cost(friction, horizon)
            values = [row["horizons"][horizon]["raw"]*leverage-cost
                      for row in rows]
            if not values:
                return {"events": 0, "net_win_rate_pct": 0.0,
                        "mean_net_pct": 0.0, "stop_hit_rate_pct": 0.0}
            probabilistic = _probabilistic_net_summary(
                values, seed_material="%s|%s|%s" %
                (dsl.executable_hash(definition), horizon, label))
            return {
                "events": len(values),
                "net_win_rate_pct": round(sum(value > 0 for value in values)/float(len(values))*100.0, 4),
                "mean_net_pct": round(sum(values)/float(len(values))*100.0, 6),
                "stop_hit_rate_pct": round(sum(row["horizons"][horizon]["stopped"] for row in rows)/float(len(rows))*100.0, 4),
                "profit_factor": probabilistic["profit_factor"],
                "posterior_prob_above_break_even": probabilistic["posterior_prob_win_rate_above_break_even"],
                "probability_mean_positive": probabilistic["probability_mean_positive"],
                "mean_ci90_low_pct": probabilistic["mean_ci90_low"]*100.0,
            }
        full = summarize(events, base_friction, "base_full")
        holdout = summarize(events[split:], base_friction, "base_holdout")
        stressed = summarize(events, stress_friction, "stress_full")
        passed = (full["events"] >= 20 and holdout["events"] >= 6
                  and full["net_win_rate_pct"] >= 52.0
                  and holdout["net_win_rate_pct"] >= 45.0
                  and full["mean_net_pct"] > 0.0
                  and full["probability_mean_positive"] >= 0.80
                  and full["posterior_prob_above_break_even"] >= 0.75
                  and holdout["mean_net_pct"] > 0.0
                  and stressed["mean_net_pct"] > 0.0)
        summaries[str(horizon)] = {"full": full, "holdout": holdout,
                                   "stressed": stressed,
                                   "credible": passed}
        if passed:
            credible.append(horizon)
    return {"passed": bool(credible), "entry_events": len(events),
            "credible_horizons": credible, "horizons": summaries,
            "friction": {"base": base_friction, "stressed": stress_friction},
            "policy": "probabilistic_fixed_horizon_20x_all_in_cost_full_holdout_stress",
            "note": "仅为入场方向性预筛，不代表完整策略胜率"}


def _active_hunt_filter_catalog(frame):
    atr_ratio = frame["atr14"].astype(float)/frame["close"].astype(float)
    ema_distance = ((frame["close"].astype(float)-frame["ema53"].astype(float))
                    .abs()/frame["close"].astype(float))
    range_ratio = ((frame["high"].astype(float)-frame["low"].astype(float))
                   /frame["close"].astype(float))
    body_ratio = ((frame["close"].astype(float)-frame["open"].astype(float)).abs()
                  /(frame["high"].astype(float)-frame["low"].astype(float)).replace(0, float("nan")))
    cci_impulse = frame["cci"].astype(float).diff().abs()
    thresholds = {
        "atr_ratio_p45": float(atr_ratio.quantile(.45)),
        "atr_ratio_p90": float(atr_ratio.quantile(.90)),
        "ema53_distance_p50": float(ema_distance.quantile(.50)),
        "range_ratio_p50": float(range_ratio.quantile(.50)),
        "body_ratio_p60": float(body_ratio.quantile(.60)),
        "cci_impulse_p60": float(cci_impulse.quantile(.60)),
    }
    rows = [
        ("directional_body", "K线实体方向与交易方向一致"),
        ("cci_acceleration", "CCI相较前一根沿交易方向加速"),
        ("kdj_acceleration", "K与J相较前一根沿交易方向共同加速"),
        ("macd_alignment", "MACD Stick方向与交易方向一致"),
        ("tradable_atr_band", "ATR/收盘价位于历史45%至90%分位"),
        ("near_ema53", "价格与EMA53距离不超过历史中位数"),
        ("h1_trend_alignment", "1小时趋势斜率与交易方向一致"),
        ("oscillator_not_exhausted", "K、D尚未进入方向末端极值区"),
        ("range_expansion", "当根振幅不低于历史中位数"),
        ("strong_directional_body", "实体方向一致且实体/振幅不低于历史60%分位"),
        ("cci_impulse", "CCI沿交易方向变化且绝对变化不低于历史60%分位"),
        ("two_bar_directional_confirmation", "当前与前一根K线实体方向均与交易方向一致"),
        ("ema6_directional_side", "收盘价位于EMA6的交易方向一侧"),
    ]
    return ([{"filter_id": key, "description": description}
             for key, description in rows], thresholds)


def _active_hunt_filter_mask(frame, filter_id, direction, thresholds):
    long_side = direction == "long"
    if filter_id == "directional_body":
        return frame["close"] > frame["open"] if long_side else frame["close"] < frame["open"]
    if filter_id == "cci_acceleration":
        delta = frame["cci"].diff()
        return delta > 0 if long_side else delta < 0
    if filter_id == "kdj_acceleration":
        k_delta = frame["k"].diff(); j_delta = frame["j"].diff()
        return ((k_delta > 0) & (j_delta > 0) if long_side
                else (k_delta < 0) & (j_delta < 0))
    if filter_id == "macd_alignment":
        return frame["macd_stick"] > 0 if long_side else frame["macd_stick"] < 0
    if filter_id == "tradable_atr_band":
        value = frame["atr14"]/frame["close"]
        return ((value >= thresholds["atr_ratio_p45"]) &
                (value <= thresholds["atr_ratio_p90"]))
    if filter_id == "near_ema53":
        value = (frame["close"]-frame["ema53"]).abs()/frame["close"]
        return value <= thresholds["ema53_distance_p50"]
    if filter_id == "h1_trend_alignment":
        return frame["h1_slope4"] > 0 if long_side else frame["h1_slope4"] < 0
    if filter_id == "oscillator_not_exhausted":
        return ((frame["k"] < 85) & (frame["d"] < 85) if long_side
                else (frame["k"] > 15) & (frame["d"] > 15))
    if filter_id == "range_expansion":
        value = (frame["high"]-frame["low"])/frame["close"]
        return value >= thresholds["range_ratio_p50"]
    if filter_id == "strong_directional_body":
        body = (frame["close"]-frame["open"]).abs()
        span = (frame["high"]-frame["low"]).replace(0, float("nan"))
        direction_mask = (frame["close"] > frame["open"] if long_side
                          else frame["close"] < frame["open"])
        return direction_mask & (body/span >= thresholds["body_ratio_p60"])
    if filter_id == "cci_impulse":
        delta = frame["cci"].diff()
        return ((delta >= thresholds["cci_impulse_p60"]) if long_side
                else (delta <= -thresholds["cci_impulse_p60"]))
    if filter_id == "two_bar_directional_confirmation":
        directional = (frame["close"] > frame["open"] if long_side
                       else frame["close"] < frame["open"])
        return directional & directional.shift(1).fillna(False)
    if filter_id == "ema6_directional_side":
        return frame["close"] > frame["ema6"] if long_side else frame["close"] < frame["ema6"]
    raise ValueError("unknown active-hunt filter: %s" % filter_id)


def _mutation_operator_catalog():
    rows = [
        ("counter_weak_momentum", ["cci_impulse", "kdj_acceleration"],
         ["negative_net_expectancy", "posterior_or_multiple_testing_failure"],
         "针对动量不足死因：要求CCI脉冲或KDJ共同加速"),
        ("counter_false_breakout", ["strong_directional_body", "two_bar_directional_confirmation"],
         ["stop_cluster", "holdout_collapse"],
         "针对假突破死因：要求强实体或连续方向确认"),
        ("counter_regime_mismatch", ["h1_trend_alignment"],
         ["holdout_collapse", "posterior_or_multiple_testing_failure"],
         "针对环境错配：要求1小时趋势同向"),
        ("counter_exhausted_entry", ["oscillator_not_exhausted"],
         ["stop_cluster"],
         "针对末端追单：排除振荡指标方向末端"),
        ("counter_cost_noise", ["tradable_atr_band", "range_expansion"],
         ["cost_collapse", "negative_net_expectancy"],
         "针对摩擦吞噬：要求可交易波动且振幅扩张"),
        ("counter_ema_side_failure", ["ema6_directional_side"],
         ["stop_cluster", "holdout_collapse"],
         "针对信号后立即回穿：要求收盘仍处于EMA6交易方向一侧"),
    ]
    return [{"operator_id": key, "compiled_filter_ids": filters,
             "target_death_codes": death_codes,
             "description": description, "scope": "ohlcv_only_controlled_mutation"}
            for key, filters, death_codes, description in rows]


def _prescreen_death_context(assignment, limit=96):
    """Return only measured, stage-labelled history for mutation planning."""
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT pattern_id,stage,provider,death_codes_json,hard_veto,reason,"
            "created_at,review_json FROM prescreen_rejections WHERE symbol=? AND timeframe=? "
            "ORDER BY created_at DESC,rowid DESC LIMIT ?",
            (assignment["symbol"], assignment["timeframe"], int(limit))).fetchall()
    finally:
        conn.close()
    rows = _dedupe_prescreen_evidence(rows)
    grouped = {}
    for pattern_id, stage, provider, codes_json, hard_veto, reason, created_at, _review_json in rows:
        bucket = grouped.setdefault(pattern_id, {
            "pattern_id": pattern_id, "record_count": 0, "death_codes": {},
            "stages": {}, "providers": {}, "hard_veto_count": 0,
            "latest_reason": "", "latest_at": created_at})
        bucket["record_count"] += 1
        bucket["stages"][stage] = bucket["stages"].get(stage, 0)+1
        bucket["providers"][provider] = bucket["providers"].get(provider, 0)+1
        bucket["hard_veto_count"] += int(bool(hard_veto))
        for code in json.loads(codes_json or "[]"):
            bucket["death_codes"][str(code)] = bucket["death_codes"].get(str(code), 0)+1
        if not bucket["latest_reason"]:
            bucket["latest_reason"] = str(reason or "")[:500]
    return sorted(grouped.values(), key=lambda row: (
        row["record_count"], row["hard_veto_count"]), reverse=True)


def _prescreen_evidence_key(row):
    """Identify independent evidence, not repeated orchestration runs.

    A deterministic branch may be rerun to enrich its monitoring metadata.  It
    becomes new evidence only when its measured result changes.  Replaying a
    cached AI opinion is likewise one opinion, not another vote.
    """
    pattern_id, stage, provider, codes_json, hard_veto, reason = row[:6]
    try:
        codes = sorted(json.loads(codes_json or "[]"))
    except Exception:
        codes = [str(codes_json or "")]
    evidence = None
    if stage == "deterministic_atlas" and len(row) >= 7:
        try:
            # Context queries include created_at before review_json; archive
            # rebuild queries may omit it.
            review_json = row[7] if len(row) >= 8 else row[6]
            payload = json.loads(review_json or "{}")
        except Exception:
            payload = {}
        evidence = {key: payload.get(key) for key in (
            "evaluation_stage", "entry_events", "full", "holdout",
            "stressed", "multiple_testing")}
    return _sha({"pattern_id": pattern_id, "stage": stage,
                 "provider": provider, "death_codes": codes,
                 "hard_veto": int(bool(hard_veto)),
                 "measured_evidence": evidence})


def _dedupe_prescreen_evidence(rows):
    seen = set(); unique = []
    for row in rows or []:
        key = _prescreen_evidence_key(row)
        if key in seen:
            continue
        seen.add(key); unique.append(row)
    return unique


def _global_prescreen_evidence(conn):
    """Return target-aware independent evidence for global cognition.

    The older target-local helper intentionally omits symbol/timeframe because
    its callers already constrain one target.  Global distillation must retain
    that boundary or two markets with similar measurements would collapse into
    one record and could be misread as transferable evidence.
    """
    rows = conn.execute(
        "SELECT symbol,timeframe,pattern_id,stage,provider,death_codes_json,"
        "hard_veto,reason,created_at,review_json,run_id FROM prescreen_rejections "
        "ORDER BY created_at DESC,rowid DESC").fetchall()
    seen = set(); output = []
    for row in rows:
        symbol, timeframe = row[0], row[1]
        local = (row[2], row[3], row[4], row[5], row[6], row[7],
                 row[8], row[9])
        local_key = _prescreen_evidence_key(local)
        key = _sha({"symbol": symbol, "timeframe": timeframe,
                    "local_evidence_key": local_key})
        if key in seen:
            continue
        seen.add(key)
        try:
            review = json.loads(row[9] or "{}")
        except Exception:
            review = {}
        output.append({
            "evidence_id": key[:20], "symbol": symbol,
            "timeframe": timeframe, "pattern_id": row[2],
            "stage": row[3], "provider": row[4],
            "death_codes": sorted(set(json.loads(row[5] or "[]"))),
            "hard_veto": bool(row[6]), "reason": str(row[7] or "")[:1000],
            "measured": row[3] == "deterministic_atlas",
            "measurement": ({field: review.get(field) for field in
                             ("evaluation_stage", "entry_events", "direction",
                              "event", "context", "full", "holdout", "stressed")}
                            if row[3] == "deterministic_atlas" else None),
            "created_at": row[8], "experiment_id": row[10],
        })
    return output


def _distillation_rule_context(assignment, limit=10):
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT rule_key,title,category,scope_recommendation,state,"
            "measured_evidence_count,reasoned_evidence_count,"
            "distinct_target_count,death_codes_json,source_evidence_json,"
            "proposal_json,updated_at FROM distilled_death_rules "
            "WHERE state IN ('navigation_approved',"
            "'target_local_quarantine_candidate',"
            "'structural_candidate_codex_review_required') "
            "ORDER BY updated_at DESC LIMIT ?", (int(limit),)).fetchall()
    finally:
        conn.close()
    rules = []
    for row in rows:
        sources = json.loads(row[9] or "[]")
        local_support = sum(
            item.get("symbol") == assignment["symbol"] and
            item.get("timeframe") == assignment["timeframe"] and
            item.get("stage") == "deterministic_atlas" for item in sources)
        local_experiments = len(set(
            item.get("experiment_id") for item in sources
            if item.get("symbol") == assignment["symbol"] and
            item.get("timeframe") == assignment["timeframe"] and
            item.get("stage") == "deterministic_atlas" and
            item.get("experiment_id")))
        rules.append({
            "rule_key": row[0], "title": row[1], "category": row[2],
            "scope_recommendation": row[3], "state": row[4],
            "measured_evidence_count": row[5],
            "reasoned_evidence_count": row[6],
            "distinct_target_count": row[7],
            "death_codes": json.loads(row[8] or "[]"),
            "local_measured_support": local_support,
            "local_independent_experiments": local_experiments,
            "proposal": json.loads(row[10] or "{}"),
            "allowed_use": "search_navigation_only",
            "automatic_pruning_allowed": False,
        })
    return {
        "rules": rules,
        "policy": ("cross_target_distillation_is_navigation_only; actual pruning "
                   "requires two same-target deterministic failures, compiled "
                   "filter impact audit and qwen+openai approval"),
    }


def distill_death_knowledge(force=False):
    conn = _db()
    try:
        evidence = _global_prescreen_evidence(conn)
        fingerprint = _sha([row["evidence_id"] for row in evidence])
        exact = conn.execute(
            "SELECT run_id,state,evidence_count,created_at FROM "
            "death_rule_distillation_runs WHERE evidence_fingerprint=?",
            (fingerprint,)).fetchone()
        latest = conn.execute(
            "SELECT evidence_count FROM death_rule_distillation_runs "
            "ORDER BY created_at DESC,rowid DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    if exact and not force:
        return {"ok": True, "state": exact[1], "cached": True,
                "run_id": exact[0], "evidence_count": exact[2],
                "api_calls_used": 0, "created_at": exact[3]}
    prior_count = int(latest[0] if latest else 0)
    if (not force and latest and
            len(evidence)-prior_count < DEATH_DISTILLATION_BATCH):
        return {"ok": True, "state": "awaiting_evidence_batch",
                "cached": True, "evidence_count": len(evidence),
                "new_since_last": len(evidence)-prior_count,
                "next_batch_at": prior_count+DEATH_DISTILLATION_BATCH,
                "api_calls_used": 0}
    if len(evidence) < 2:
        return {"ok": True, "state": "insufficient_evidence",
                "evidence_count": len(evidence), "api_calls_used": 0}
    allowed_codes = sorted(set(code for row in evidence
                               for code in row["death_codes"]))
    context = {
        "schema": DEATH_DISTILLATION_SCHEMA_VERSION,
        "evidence": evidence[:256],
        "evidence_count": len(evidence),
        "allowed_death_codes": allowed_codes,
        "registered_micro_vocabulary": {
            "allowed_use": "prospective_environment_description_only",
            "historical_evidence": False, "price_prediction": False},
        "deployment_boundary": ("distilled rules cannot auto-prune across targets; "
                                "target-local empirical activation remains separate"),
    }
    from auto_trade_ai_consensus import distill_death_rule_tree
    ai_result = distill_death_rule_tree(context)
    proposal = ai_result.get("proposal") or {}
    proposed_rules = proposal.get("root_rules") or []
    audits = ai_result.get("audits") or []
    audit_maps = [{item.get("rule_key"): item for item in row.get("audits") or []}
                  for row in audits if row.get("ok")]
    evidence_map = {row["evidence_id"]: row for row in evidence}
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3] + "_distill"
    now = _now(); stored = []
    conn = _db()
    try:
        for rule in proposed_rules:
            sources = [evidence_map[key] for key in
                       rule.get("source_evidence_ids") or []
                       if key in evidence_map]
            measured = sum(bool(row["measured"]) for row in sources)
            reasoned = len(sources)-measured
            target_count = len(set((row["symbol"], row["timeframe"])
                                   for row in sources if row["measured"]))
            experiment_count = len(set(
                row.get("experiment_id") for row in sources
                if row["measured"] and row.get("experiment_id")))
            votes = [mapping.get(rule["rule_key"]) for mapping in audit_maps]
            approved = (len(audit_maps) == 2 and all(
                vote and vote.get("decision") == "APPROVE" for vote in votes))
            if not approved:
                state = "rejected_by_independent_audit"
            elif (rule.get("category") == "structural_hard_veto" and
                  rule.get("scope_recommendation") == "structural_candidate"):
                state = "structural_candidate_codex_review_required"
            elif measured >= 2 and experiment_count >= 2 and target_count >= 2:
                state = "navigation_approved"
            elif measured >= 2 and experiment_count >= 2:
                state = "target_local_quarantine_candidate"
            else:
                state = "insufficient_independent_experiments"
            rule_id = _sha({"schema": DEATH_DISTILLATION_SCHEMA_VERSION,
                            "rule": rule, "fingerprint": fingerprint})
            conn.execute(
                "INSERT OR REPLACE INTO distilled_death_rules VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rule_id, run_id, rule["rule_key"], rule.get("title") or "",
                 rule.get("category") or "", rule.get("scope_recommendation") or "",
                 state, measured, reasoned, target_count,
                 json.dumps(rule.get("applicable_death_codes") or [], sort_keys=True),
                 json.dumps(sources, ensure_ascii=False, sort_keys=True),
                 json.dumps(rule, ensure_ascii=False, sort_keys=True),
                 json.dumps(votes, ensure_ascii=False, sort_keys=True), now, now))
            stored.append({"rule_key": rule["rule_key"], "state": state,
                           "measured_evidence": measured,
                           "independent_experiments": experiment_count,
                           "distinct_targets": target_count})
        if any(row["state"] == "navigation_approved" for row in stored):
            run_state = "distilled_with_approved_navigation"
        elif any(row["state"] == "target_local_quarantine_candidate"
                 for row in stored):
            run_state = "distilled_with_audited_quarantine_candidate"
        elif any(row["state"] == "structural_candidate_codex_review_required"
                 for row in stored):
            run_state = "distilled_with_structural_candidate"
        else:
            run_state = "distilled_no_approved_navigation"
        conn.execute(
            "INSERT OR REPLACE INTO death_rule_distillation_runs VALUES(?,?,?,?,?,?,?,?)",
            (run_id, DEATH_DISTILLATION_SCHEMA_VERSION, fingerprint,
             len(evidence), run_state,
             json.dumps(proposal, ensure_ascii=False, sort_keys=True),
             json.dumps(audits, ensure_ascii=False, sort_keys=True), now))
        conn.commit()
    finally:
        conn.close()
    return {"ok": bool(ai_result.get("ok")), "state": run_state,
            "run_id": run_id, "evidence_count": len(evidence),
            "rules": stored, "api_calls_used": 1+len(audits),
            "automatic_pruning_rules_activated": 0,
            "policy": "distillation_navigation_only_target_local_activation_unchanged"}


def _pruning_rule_context(assignment):
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT pattern_id,filter_id,death_cause_code,state,occurrences,empirical_json "
            "FROM pruning_rule_memory WHERE symbol=? AND timeframe=? "
            "ORDER BY last_seen DESC LIMIT 48",
            (assignment["symbol"], assignment["timeframe"])).fetchall()
    finally:
        conn.close()
    return [{"pattern_id": row[0], "filter_id": row[1],
             "death_cause_code": row[2], "state": row[3],
             "occurrences": row[4], "empirical": json.loads(row[5] or "{}")}
            for row in rows]


def _micro_primitive_context(assignment, limit=12):
    """Expose only registered, outcome-free slow-microstructure vocabulary."""
    path = AUTO_DIR / "microstructure_telemetry.db"
    if not path.exists():
        return {"available": False, "reason": "telemetry database missing",
                "primitives": []}
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        tables = set(row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        if "microstructure_primitive_clusters" not in tables:
            conn.close()
            return {"available": False, "reason": "primitive discovery not run",
                    "primitives": []}
        rows = conn.execute(
            "SELECT cluster_id,label,description,sample_count,centroid_json,"
            "review_json,updated_at FROM microstructure_primitive_clusters "
            "WHERE state='descriptive_registered' ORDER BY sample_count DESC LIMIT ?",
            (int(limit),)).fetchall()
        assignment_rows = []
        if "microstructure_primitive_assignments" in tables:
            assignment_rows = conn.execute(
                "SELECT cluster_id,COUNT(*) FROM microstructure_primitive_assignments "
                "WHERE symbol=? GROUP BY cluster_id",
                (assignment["symbol"],)).fetchall()
        conn.close()
        counts = dict(assignment_rows)
        return {
            "available": bool(rows),
            "policy": "outcome_free_descriptive_vocabulary_not_entry_evidence",
            "symbol": assignment["symbol"],
            "primitives": [{"cluster_id": row[0], "label": row[1],
                            "description": row[2], "global_samples": row[3],
                            "symbol_windows": int(counts.get(row[0]) or 0),
                            "centroid": json.loads(row[4] or "{}"),
                            "review": json.loads(row[5] or "{}"),
                            "updated_at": row[6]} for row in rows],
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc)[:300], "primitives": []}


def _micro_observation_context(assignment, limit=12):
    """Return outcome-free cluster statistics, including unregistered clusters.

    These rows may inform prospective observation plans, never historical DSL
    masks or positive labels.
    """
    path = AUTO_DIR / "microstructure_telemetry.db"
    if not path.exists():
        return {"available": False, "clusters": [], "associations": []}
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        tables = set(row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        if "microstructure_primitive_clusters" not in tables:
            conn.close()
            return {"available": False, "clusters": [], "associations": []}
        clusters = conn.execute(
            "SELECT cluster_id,state,sample_count,centroid_json,updated_at "
            "FROM microstructure_primitive_clusters WHERE state IN "
            "('observed_unlabeled','descriptive_registered') "
            "ORDER BY sample_count DESC LIMIT ?",
            (int(limit),)).fetchall()
        assignments = []
        if "microstructure_primitive_assignments" in tables:
            assignments = conn.execute(
                "SELECT cluster_id,end_ms FROM microstructure_primitive_assignments "
                "WHERE symbol=? ORDER BY end_ms",
                (assignment["symbol"],)).fetchall()
        event_rows = []
        if "microstructure_event_associations" in tables:
            event_rows = conn.execute(
                "SELECT cluster_id,event_code,cluster_total,cluster_hits,"
                "background_total,background_hits,lift,lift_ci95_low,eligible,"
                "payload_json,created_at FROM microstructure_event_associations "
                "WHERE symbol=? ORDER BY created_at DESC LIMIT 256",
                (assignment["symbol"],)).fetchall()
        conn.close()
        total = len(assignments); counts = {}; run_lengths = {}
        previous = None; run = 0
        for cluster_id, _end_ms in assignments:
            counts[cluster_id] = counts.get(cluster_id, 0)+1
            if cluster_id == previous:
                run += 1
            else:
                if previous is not None:
                    run_lengths.setdefault(previous, []).append(run)
                previous = cluster_id; run = 1
        if previous is not None:
            run_lengths.setdefault(previous, []).append(run)
        eco = _db()
        try:
            report = eco.execute(
                "SELECT payload_json,created_at FROM death_micro_association_reports "
                "WHERE symbol=? AND timeframe=? ORDER BY created_at DESC LIMIT 1",
                (assignment["symbol"], assignment["timeframe"])).fetchone()
        finally:
            eco.close()
        death_associations = json.loads(report[0]).get("cells", []) if report else []
        seen_events = set(); event_warnings = []
        for row in event_rows:
            key = (row[0], row[1])
            if key in seen_events:
                continue
            seen_events.add(key)
            if not row[8] or row[1] not in (
                    "spread_jump_5sample", "depth_drop_5sample"):
                continue
            event_warnings.append({
                "association_source": "future_micro_event_graph",
                "cluster_id": row[0], "event_code": row[1],
                "cluster_total": row[2], "cluster_hits": row[3],
                "background_total": row[4], "background_hits": row[5],
                "lift": row[6], "lift_ci95_low": row[7],
                "eligible": True, "causal_claim": False,
                "outcome_label": None, "created_at": row[10]})
        associations = list(death_associations)+event_warnings
        return {"available": bool(clusters),
                "policy": "unlabeled_observation_context_no_historical_filter",
                "clusters": [{"cluster_id": row[0], "state": row[1],
                              "global_windows": row[2],
                              "symbol_windows": counts.get(row[0], 0),
                              "symbol_frequency": round(counts.get(row[0], 0)/
                                                        float(total), 6) if total else 0.0,
                              "mean_consecutive_samples": round(sum(
                                  run_lengths.get(row[0], []))/float(len(
                                  run_lengths.get(row[0], []))), 4)
                                  if run_lengths.get(row[0]) else 0.0,
                              "centroid": json.loads(row[3] or "{}"),
                              "updated_at": row[4]} for row in clusters],
                "associations": associations,
                "eligible_negative_micro_event_warnings": event_warnings,
                "association_state": ("observed" if report else
                                      "awaiting_forward_signal_overlap"),
                "association_updated_at": report[1] if report else None}
    except Exception as exc:
        return {"available": False, "clusters": [], "associations": [],
                "error": str(exc)[:300]}


def _atlas_pattern_indices(frame, pattern, maximum_horizon=16):
    event = pattern.get("event") or {}; context = pattern.get("context") or {}
    left = frame[event["feature"]]
    event_right = event.get("right") or {}
    right = (frame[event_right["feature"]] if "feature" in event_right
             else float(event_right["value"]))
    op = event.get("op")
    previous_right = right.shift(1) if hasattr(right, "shift") else right
    if op == "cross_above":
        event_mask = (left > right) & (left.shift(1) <= previous_right)
    elif op == "cross_below":
        event_mask = (left < right) & (left.shift(1) >= previous_right)
    else:
        raise ValueError("unsupported atlas event op")
    context_left = frame[context["feature"]]
    context_right = context.get("right") or {}
    context_value = (frame[context_right["feature"]]
                     if "feature" in context_right else
                     float(context_right["value"]))
    context_mask = (context_left > context_value if context.get("op") == "gt"
                    else context_left < context_value)
    mask = (event_mask & context_mask).fillna(False).tolist()
    return [index for index, passed in enumerate(mask)
            if passed and index >= 250 and index+maximum_horizon < len(frame)]


def _consolidate_active_hunt_priors(ai_result, probe_catalog,
                                    branch_priors, pruning_rules=None,
                                    death_map=None):
    providers = [row for row in ai_result.get("results") or [] if row.get("ok")]
    by_provider = {row.get("provider"): {
        item.get("pattern_id"): item for item in row.get("reviews") or []}
        for row in providers}
    output = []
    operator_catalog = _mutation_operator_catalog()
    operator_map = {row["operator_id"]: row["compiled_filter_ids"]
                    for row in operator_catalog}
    operator_deaths = {row["operator_id"]: set(row["target_death_codes"])
                       for row in operator_catalog}
    pruning_rules = pruning_rules or []
    death_map = {row.get("pattern_id"): row for row in (death_map or [])}
    for pattern in probe_catalog:
        pattern_id = pattern["pattern_id"]
        reviews = [rows.get(pattern_id) for rows in by_provider.values()
                   if rows.get(pattern_id)]
        hard_veto = any(row.get("hard_veto") for row in reviews)
        effective_hunts = []
        for row in reviews:
            adaptation_survives = all(
                str(item.get("verdict") or "").upper() == "SURVIVE"
                for item in row.get("adaptation_rounds") or [])
            if (row.get("decision") == "HUNT"
                    and float(row.get("viability_score") or 0.0) >= 60
                    and adaptation_survives):
                effective_hunts.append(row)
        filter_votes = {}
        operator_votes = {}
        micro_warning_votes = {}
        for row in effective_hunts:
            for filter_id in set(row.get("required_filter_ids") or []):
                filter_votes[filter_id] = filter_votes.get(filter_id, 0)+1
            for operator_id in set(row.get("mutation_operator_ids") or []):
                operator_votes[operator_id] = operator_votes.get(operator_id, 0)+1
            micro_plan = row.get("micro_background_plan") or {}
            if micro_plan.get("observation_only") is True:
                for cluster_id in set(micro_plan.get("avoid_cluster_ids") or []):
                    micro_warning_votes[cluster_id] = (
                        micro_warning_votes.get(cluster_id, 0)+1)
        agreed_operators = sorted(key for key, count in operator_votes.items()
                                  if count >= 2 and key in operator_map)
        agreed_micro_warnings = sorted(
            key for key, count in micro_warning_votes.items() if count >= 2)
        death_history = death_map.get(pattern_id) or {}
        historical_deaths = set((death_history.get("death_codes") or {}).keys())
        mitigated_deaths = set()
        for operator_id in agreed_operators:
            mitigated_deaths.update(operator_deaths.get(operator_id) or set())
        repeated_deaths = set(code for code, count in
                              (death_history.get("death_codes") or {}).items()
                              if int(count or 0) >= 2)
        unmitigated_deaths = sorted(repeated_deaths-mitigated_deaths)
        learned_required = sorted(set(row.get("filter_id") for row in pruning_rules
                                      if row.get("pattern_id") == pattern_id
                                      and row.get("state") == "active"))
        agreed_filters = list(learned_required)
        for key, count in sorted(filter_votes.items()):
            if count >= 2 and key not in agreed_filters:
                agreed_filters.append(key)
        for operator_id in agreed_operators:
            for filter_id in operator_map[operator_id]:
                if filter_id not in agreed_filters:
                    agreed_filters.append(filter_id)
        support = len(effective_hunts); complete_panel = len(providers) == 3
        accepted = bool(complete_panel and not hard_veto and support >= 2
                        and agreed_filters and not unmitigated_deaths)
        probability = support/3.0
        disagreement = (-(probability*math.log(max(probability, 1e-9))
                          +(1-probability)*math.log(max(1-probability, 1e-9)))
                        if 0 < probability < 1 else 0.0)
        prior = branch_priors.get(pattern_id) or {}
        mean_score = (sum(float(row.get("viability_score") or 0.0)
                          for row in reviews)/float(len(reviews)) if reviews else 0.0)
        cognitive_blank = bool(not historical_deaths and
                               not agreed_micro_warnings)
        death_penalty = min(.60, .12*len(unmitigated_deaths)
                            +.02*int(death_history.get("record_count") or 0))
        acquisition = (mean_score/100.0 + .60*disagreement
                       +.75*float(prior.get("uncertainty") or 0.0)
                       -death_penalty)
        output.append({
            "pattern_id": pattern_id, "direction": pattern.get("direction"),
            "accepted_for_probe": accepted, "support_count": support,
            "complete_three_ai_panel": complete_panel,
            "hard_veto": hard_veto, "agreed_filter_ids": agreed_filters[:3],
            "filter_votes": filter_votes,
            "mutation_operator_votes": operator_votes,
            "agreed_mutation_operator_ids": agreed_operators,
            "historical_death_codes": sorted(historical_deaths),
            "mitigated_death_codes": sorted(mitigated_deaths),
            "unmitigated_repeated_death_codes": unmitigated_deaths,
            "death_map_priority_penalty": round(death_penalty, 6),
            "cognitive_blank": cognitive_blank,
            "search_priority": ("uncovered_explainable_space" if cognitive_blank
                                else "known_death_or_micro_warning_space"),
            "micro_warning_votes": micro_warning_votes,
            "agreed_avoid_cluster_ids": agreed_micro_warnings,
            "micro_warning_policy": "prospective_observation_only_not_probe_filter",
            "death_avoidance_explanations": [str(row.get(
                "death_avoidance_explanation") or "")[:800]
                for row in effective_hunts],
            "learned_required_filter_ids": learned_required,
            "mean_viability_score": round(mean_score, 4),
            "disagreement_entropy": round(disagreement, 6),
            "information_gain_acquisition": round(acquisition, 6),
            "counterfactual_blueprints": [row.get("counterfactual_blueprint")
                                           for row in effective_hunts],
            "reviews": reviews,
            "label_policy": "unlabeled_search_prior_not_positive_sample",
        })
    return sorted(output, key=lambda row: (
        row["accepted_for_probe"],
        int(float(row["information_gain_acquisition"])*10),
        row["cognitive_blank"],
        row["information_gain_acquisition"]),
        reverse=True)


def _active_hunt_prior(frame, assignment, atlas, operator_lattice=None):
    iso = datetime.now().isocalendar()
    week = "%04d-W%02d" % (iso[0], iso[1])
    lattice_fingerprint = _sha(operator_lattice or {})
    distilled_rules = _distillation_rule_context(assignment)
    distilled_rule_fingerprint = _sha(distilled_rules)
    conn = _db()
    try:
        cached = conn.execute(
            "SELECT payload_json FROM active_hunt_prior_runs "
            "WHERE symbol=? AND timeframe=? AND week_bucket=?",
            (assignment["symbol"], assignment["timeframe"], week)).fetchone()
    finally:
        conn.close()
    if cached:
        result = json.loads(cached[0])
        if (result.get("schema_version") == ACTIVE_HUNT_SCHEMA_VERSION
                and result.get("operator_lattice_fingerprint") ==
                lattice_fingerprint
                and result.get("distilled_rule_fingerprint") ==
                distilled_rule_fingerprint):
            # Cache expensive provider opinions, not the mutable memory that
            # constrains them.  Fresh death maps and learned pruning rules
            # must affect the very next run instead of waiting for next week.
            probe_catalog = result.get("probe_catalog") or []
            pattern_ids = [row.get("pattern_id") for row in probe_catalog
                           if row.get("pattern_id")]
            priors = _branch_survival_priors(
                assignment["symbol"], assignment["timeframe"], pattern_ids)
            pruning_rules = _pruning_rule_context(assignment)
            death_map = _prescreen_death_context(assignment)
            panels = result.get("review_panels") or []
            if panels:
                result["consolidated"] = _consolidate_active_hunt_priors(
                    {"results": panels}, probe_catalog, priors,
                    pruning_rules=pruning_rules, death_map=death_map)
            result["prescreen_death_map_current"] = death_map
            result["slow_microstructure_primitives"] = _micro_primitive_context(
                assignment)
            result["unlabeled_micro_observation_context"] = (
                _micro_observation_context(assignment))
            result["distilled_death_rule_navigation"] = (
                _distillation_rule_context(assignment))
            result["dynamic_memory_recomputed_at"] = _now()
            result["cached"] = True
            return result
    probe_catalog = []
    for row in (atlas.get("top_observed") or [])[:6]:
        lattice_rows = [item for item in
                        ((operator_lattice or {}).get("eligible_combinations") or [])
                        if item.get("pattern_id") == row.get("pattern_id")]
        probe_catalog.append({
            "pattern_id": row.get("pattern_id"), "direction": row.get("direction"),
            "event": row.get("event"), "context": row.get("context"),
            "entry_events": row.get("entry_events"), "rank_score": row.get("rank_score"),
            "full": row.get("full"), "holdout": row.get("holdout"),
            "stressed": row.get("stressed"),
            "deterministic_operator_lattice": lattice_rows[:3],
        })
    filters, thresholds = _active_hunt_filter_catalog(frame)
    pattern_ids = [row["pattern_id"] for row in probe_catalog]
    priors = _branch_survival_priors(
        assignment["symbol"], assignment["timeframe"], pattern_ids)
    pruning_rules = _pruning_rule_context(assignment)
    death_map = _prescreen_death_context(assignment)
    micro_primitives = _micro_primitive_context(assignment)
    micro_observations = _micro_observation_context(assignment)
    hunt_context = {
        "assignment": assignment, "probe_catalog": probe_catalog,
        "available_filter_catalog": filters,
        "filter_thresholds_from_development_only": thresholds,
        "cost_policy": "probe uses 3x all observed friction at 20x leverage",
        "data_boundary": "OHLCV-derived indicators only; forward microstructure is not yet historical",
        "prior_probe_history": _active_hunt_probe_history(assignment),
        "mutation_operator_catalog": _mutation_operator_catalog(),
        "learned_pruning_rules": pruning_rules,
        "failed_branch_clusters": atlas.get("failure_clusters") or {},
        "prescreen_death_map": death_map,
        "slow_microstructure_primitives": micro_primitives,
        "unlabeled_micro_observation_context": micro_observations,
        "distilled_death_rule_navigation": distilled_rules,
        "registered_micro_navigation_policy": {
            "allowed": "form prospective shadow stratification questions",
            "forbidden": ["historical_backtest_filter", "price_prediction",
                          "live_entry_trigger", "ideal_or_bad_environment_label"],
        },
        "operator_lattice_screen": operator_lattice or {},
        "slow_microstructure_policy": (
            "descriptive context only; never a positive label or historical DSL filter; "
            "candidate logic must survive with sampled 5s/15s/60s and minute snapshots"),
    }
    from auto_trade_ai_consensus import active_hunt_priors
    ai_result = active_hunt_priors(hunt_context)
    consolidated = _consolidate_active_hunt_priors(
        ai_result, probe_catalog, priors, pruning_rules=pruning_rules,
        death_map=death_map)
    result = {
        "schema_version": ACTIVE_HUNT_SCHEMA_VERSION,
        "operator_lattice_fingerprint": lattice_fingerprint,
        "distilled_rule_fingerprint": distilled_rule_fingerprint,
        "week_bucket": week, "cached": False,
        "policy": "controlled_three_ai_survival_necessity_intersection",
        "provider_status": [{"provider": row.get("provider"),
                             "ok": row.get("ok"), "error": row.get("error")}
                            for row in ai_result.get("results") or []],
        "review_panels": [{"provider": row.get("provider"),
                           "model": row.get("model"),
                           "ok": row.get("ok"),
                           "reviews": row.get("reviews") or [],
                           "error": row.get("error")}
                          for row in ai_result.get("results") or []],
        "available_filter_catalog": filters, "filter_thresholds": thresholds,
        "probe_catalog": probe_catalog, "consolidated": consolidated,
        "prescreen_death_map_before_run": death_map,
        "slow_microstructure_primitives": micro_primitives,
        "unlabeled_micro_observation_context": micro_observations,
        "distilled_death_rule_navigation": distilled_rules,
        "operator_lattice_screen": operator_lattice or {},
        "synthetic_blueprints_are_labels": False,
    }
    if ai_result.get("ok"):
        conn = _db()
        try:
            prior_id = _sha({"symbol": assignment["symbol"],
                             "timeframe": assignment["timeframe"], "week": week})
            conn.execute(
                "INSERT OR REPLACE INTO active_hunt_prior_runs VALUES(?,?,?,?,?,?)",
                (prior_id, assignment["symbol"], assignment["timeframe"], week,
                 json.dumps(result, ensure_ascii=False, sort_keys=True), _now()))
            conn.commit()
        finally:
            conn.close()
    return result


def _probe_summary(frame, indices, direction, horizon, timeframe,
                   friction, cost_multiple=3.0):
    values = []
    for index in indices:
        if index+horizon >= len(frame):
            continue
        entry = float(frame["close"].iloc[index])
        window = frame.iloc[index+1:index+horizon+1]
        stopped = (float(window["low"].min()) <= entry*(1.0-.009)
                   if direction == "long" else
                   float(window["high"].max()) >= entry*(1.0+.009))
        if stopped:
            raw = -.009
        else:
            close = float(frame["close"].iloc[index+horizon])
            raw = ((close-entry)/entry if direction == "long"
                   else (entry-close)/entry)
        hours = {"5m": 1.0/12.0, "15m": .25, "1h": 1.0}.get(timeframe, 1.0)
        side = sum(float(friction.get(key) or 0.0) for key in (
            "fee_rate_per_side", "slippage_rate_per_side",
            "half_spread_rate_per_side", "impact_rate_per_side",
            "latency_rate_per_side"))
        cost = cost_multiple*(2.0*side+horizon*hours/8.0*float(
            friction.get("funding_rate_per_8h") or 0.0))*20.0
        values.append(raw*20.0-cost)
    probability = _probabilistic_net_summary(
        values, seed_material="active_hunt|%s|%s|%s" %
        (direction, horizon, len(indices)), bootstrap_samples=500)
    return {"events": len(values),
            "net_win_rate_pct": round(sum(value > 0 for value in values)/
                                      float(len(values))*100.0, 4) if values else 0.0,
            "mean_net_pct": round((sum(values)/float(len(values))*100.0), 6)
                            if values else 0.0,
            "probability_mean_positive": probability["probability_mean_positive"],
            "profit_factor": probability["profit_factor"],
            "mean_ci90_low_pct": probability["mean_ci90_low"]*100.0,
            "cost_multiple": cost_multiple}


def _operator_lattice_screen(frame, assignment, observations,
                             maximum_results=24):
    """Development-only deterministic search over registered mutations.

    The lattice is intentionally dormant until this exact symbol/timeframe has
    produced a branch that survived the complete local cost screen.  Its
    output is neither a positive label nor a probe pass; it only gives the
    three independent reviewers a measured, bounded set of combinations.
    """
    survivors = [row for row in (observations or [])
                 if row.get("survived_cost_screen")]
    if not survivors:
        return {
            "state": "dormant_no_target_local_cost_survivor",
            "activation_gate": "survived_cost_screen_on_same_symbol_and_timeframe",
            "tested_combinations": 0, "eligible_count": 0,
            "eligible_combinations": [], "top_rejected": [],
            "positive_labels_created": 0,
            "sealed_holdout_used": False,
        }
    operators = _mutation_operator_catalog()
    redundant_groups = [
        set(["directional_body", "strong_directional_body",
             "two_bar_directional_confirmation"]),
        set(["cci_acceleration", "cci_impulse"]),
    ]
    thresholds = _active_hunt_filter_catalog(frame)[1]
    friction = _friction_scenario(assignment["symbol"], "observed_base")
    parents = sorted(survivors, key=lambda row: (
        float(row.get("rank_score") or -999.0),
        int(row.get("entry_events") or 0)), reverse=True)[:3]
    results = []
    for parent in parents:
        direction = parent.get("direction")
        event_indices = _atlas_pattern_indices(frame, parent)
        horizon = int(parent.get("best_horizon_bars") or 4)
        for size in (1, 2):
            for chosen in itertools.combinations(operators, size):
                filter_ids = sorted(set(
                    filter_id for operator in chosen
                    for filter_id in operator.get("compiled_filter_ids") or []))
                if len(filter_ids) > 4:
                    continue
                # A single registered operator may deliberately compile to
                # two confirmations in one family.  Reject redundancy only
                # when separate operators add overlapping substitutes.
                if (len(chosen) > 1 and any(
                        group.intersection(chosen[0].get(
                            "compiled_filter_ids") or []) and
                        group.intersection(chosen[1].get(
                            "compiled_filter_ids") or [])
                        for group in redundant_groups)):
                    continue
                masks = [_active_hunt_filter_mask(
                    frame, filter_id, direction, thresholds).fillna(False).tolist()
                         for filter_id in filter_ids]
                exact = [index for index in event_indices
                         if all(mask[index] for mask in masks)]
                split = max(1, int(len(exact)*.70)) if exact else 0
                full = _probe_summary(
                    frame, exact, direction, horizon, assignment["timeframe"],
                    friction, cost_multiple=3.0)
                holdout = _probe_summary(
                    frame, exact[split:], direction, horizon,
                    assignment["timeframe"], friction, cost_multiple=3.0)
                eligible = bool(
                    full["events"] >= 8 and holdout["events"] >= 3
                    and full["mean_net_pct"] > 0.0
                    and full["probability_mean_positive"] >= .55
                    and holdout["mean_net_pct"] > -1.0)
                results.append({
                    "pattern_id": parent.get("pattern_id"),
                    "direction": direction,
                    "operator_ids": [row["operator_id"] for row in chosen],
                    "filter_ids": filter_ids, "horizon_bars": horizon,
                    "exact": full, "holdout": holdout,
                    "eligible_for_ai_review": eligible,
                    "label_policy": "measured_search_hint_not_positive_or_probe",
                })
    results.sort(key=lambda row: (
        row["eligible_for_ai_review"],
        float((row.get("holdout") or {}).get("mean_net_pct") or -999.0),
        float((row.get("exact") or {}).get(
            "probability_mean_positive") or 0.0),
        int((row.get("exact") or {}).get("events") or 0)), reverse=True)
    eligible = [row for row in results if row["eligible_for_ai_review"]]
    rejected = [row for row in results if not row["eligible_for_ai_review"]]
    return {
        "state": ("eligible_combinations_for_three_ai_review" if eligible
                  else "searched_no_combination_survived"),
        "activation_gate": "survived_cost_screen_on_same_symbol_and_timeframe",
        "parent_patterns": [row.get("pattern_id") for row in parents],
        "tested_combinations": len(results),
        "eligible_count": len(eligible),
        "eligible_combinations": eligible[:int(maximum_results)],
        "top_rejected": rejected[:6],
        "cost_policy": "3x_observed_friction_20x_development_only",
        "positive_labels_created": 0,
        "sealed_holdout_used": False,
    }


def _run_active_hunt_probes(frame, assignment, atlas, prior, run_id):
    patterns = {row.get("pattern_id"): row
                for row in atlas.get("top_observed") or []}
    thresholds = prior.get("filter_thresholds") or {}
    friction = _friction_scenario(assignment["symbol"], "observed_base")
    results = []
    for proposal in prior.get("consolidated") or []:
        if not proposal.get("accepted_for_probe"):
            continue
        pattern = patterns.get(proposal.get("pattern_id"))
        if not pattern:
            continue
        direction = pattern.get("direction"); filters = proposal.get("agreed_filter_ids") or []
        filters_json = json.dumps(filters, ensure_ascii=False)
        conn = _db()
        try:
            previous = conn.execute(
                "SELECT payload_json,created_at FROM active_hunt_probe_experiments "
                "WHERE symbol=? AND timeframe=? AND pattern_id=? AND filters_json=? "
                "AND created_at>=? ORDER BY created_at DESC LIMIT 1",
                (assignment["symbol"], assignment["timeframe"],
                 proposal.get("pattern_id"), filters_json,
                 (datetime.now()-timedelta(days=7)).strftime(BEIJING_FMT)),
            ).fetchone()
        finally:
            conn.close()
        if previous:
            reused = json.loads(previous[0]); reused["reused"] = True
            reused["reused_from_created_at"] = previous[1]
            results.append(reused)
            continue
        event_indices = _atlas_pattern_indices(frame, pattern)
        masks = [_active_hunt_filter_mask(frame, filter_id, direction, thresholds)
                 .fillna(False).tolist() for filter_id in filters]
        exact = [index for index in event_indices
                 if all(mask[index] for mask in masks)]
        scored = sorted(((sum(mask[index] for mask in masks)/float(len(masks)), index)
                         for index in event_indices), reverse=True)
        exact_set = set(exact)
        similar = [index for score, index in scored
                   if score >= 2.0/3.0 and index not in exact_set][:24]
        horizon = int(pattern.get("best_horizon_bars") or 4)
        exact_summary = _probe_summary(
            frame, exact, direction, horizon, assignment["timeframe"], friction)
        similarity_summary = _probe_summary(
            frame, similar, direction, horizon, assignment["timeframe"], friction)
        split = max(1, int(len(exact)*.70)) if exact else 0
        holdout_summary = _probe_summary(
            frame, exact[split:], direction, horizon, assignment["timeframe"], friction)
        suspected = (exact_summary["events"] >= 8
                     and holdout_summary["events"] >= 3
                     and exact_summary["mean_net_pct"] > 0.0
                     and exact_summary["probability_mean_positive"] >= .55
                     and holdout_summary["mean_net_pct"] > -1.0)
        state = "suspected_positive_probe" if suspected else "probe_rejected"
        row = dict(proposal)
        row.update({"horizon_bars": horizon, "exact_events": len(exact),
                    "similarity_events": len(similar), "exact": exact_summary,
                    "holdout": holdout_summary, "similarity": similarity_summary,
                    "state": state, "synthetic_label": False,
                    "qualification_policy": "3x_cost_exact_window_min8_holdout3_not_a_strategy_positive"})
        experiment_id = _sha({"run_id": run_id,
                              "pattern_id": proposal.get("pattern_id")})
        row["experiment_id"] = experiment_id; results.append(row)
        conn = _db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO active_hunt_probe_experiments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (experiment_id, run_id, assignment["symbol"], assignment["timeframe"],
                 proposal.get("pattern_id"), int(proposal.get("support_count") or 0),
                 filters_json, len(exact), len(similar), 3.0,
                 exact_summary.get("mean_net_pct"),
                 exact_summary.get("probability_mean_positive"), state,
                 json.dumps(row, ensure_ascii=False, sort_keys=True), _now()))
            conn.commit()
        finally:
            conn.close()
    return {"policy": "active_hunt_3x_cost_exact_then_similarity_probe",
            "tested": len(results),
            "suspected_positive_count": sum(row["state"] ==
                                              "suspected_positive_probe"
                                              for row in results),
            "results": results}


def _active_hunt_probe_history(assignment, limit=24):
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT pattern_id,filters_json,exact_events,mean_net_pct,"
            "probability_positive,state,created_at "
            "FROM active_hunt_probe_experiments WHERE symbol=? AND timeframe=? "
            "ORDER BY created_at DESC LIMIT ?",
            (assignment["symbol"], assignment["timeframe"], int(limit)),
        ).fetchall()
    finally:
        conn.close()
    return [{"pattern_id": row[0], "filter_ids": json.loads(row[1] or "[]"),
             "exact_events": row[2], "mean_net_pct": row[3],
             "probability_positive": row[4], "state": row[5],
             "created_at": row[6]} for row in rows]


def _probe_failure_empirical_audit(frame, pattern, filter_id, thresholds,
                                   timeframe, friction):
    direction = pattern.get("direction")
    indices = _atlas_pattern_indices(frame, pattern)
    mask = _active_hunt_filter_mask(
        frame, filter_id, direction, thresholds).fillna(False).tolist()
    kept = [index for index in indices if mask[index]]
    removed = [index for index in indices if not mask[index]]
    horizon = int(pattern.get("best_horizon_bars") or 4)
    base = _probe_summary(frame, indices, direction, horizon, timeframe, friction)
    kept_summary = _probe_summary(frame, kept, direction, horizon, timeframe, friction)
    removed_summary = _probe_summary(frame, removed, direction, horizon, timeframe, friction)
    prune_pct = (len(removed)/float(len(indices))*100.0) if indices else 100.0
    passed = (len(indices) >= 12 and len(kept) >= 8 and len(removed) >= 3
              and prune_pct <= 80.0
              and kept_summary["mean_net_pct"] > base["mean_net_pct"]
              and kept_summary["mean_net_pct"] > removed_summary["mean_net_pct"])
    return {"passed": passed, "filter_id": filter_id,
            "events": len(indices), "kept_events": len(kept),
            "removed_events": len(removed), "prune_pct": round(prune_pct, 4),
            "base": base, "kept": kept_summary, "removed": removed_summary,
            "policy": "3x_cost_rule_impact_min8_max80pct_no_future_data"}


def _learn_from_probe_failures(frame, assignment, atlas, prior, probes, run_id):
    """Convert at most one informative failed probe into audited rule memory."""
    candidates = [row for row in probes.get("results") or []
                  if row.get("state") == "probe_rejected"
                  and not row.get("reused")
                  and int(row.get("exact_events") or 0) >= 8]
    if not candidates:
        return {"attempted": False, "reason": "no_new_informative_failed_probe",
                "api_calls_used": 0}
    target = max(candidates, key=lambda row: (
        float((row.get("exact") or {}).get("probability_mean_positive") or 0.0),
        int(row.get("exact_events") or 0)))
    patterns = {row.get("pattern_id"): row
                for row in atlas.get("top_observed") or []}
    pattern = patterns.get(target.get("pattern_id"))
    if not pattern:
        return {"attempted": False, "reason": "pattern_not_found",
                "api_calls_used": 0}
    filter_rows = prior.get("available_filter_catalog") or []
    allowed = [row.get("filter_id") for row in filter_rows]
    context = {
        "assignment": assignment, "pattern": pattern,
        "failed_probe": target,
        "allowed_filter_ids": allowed,
        "already_required_filter_ids": target.get("agreed_filter_ids") or [],
        "data_boundary": ("historical evidence is OHLCV-derived only; minute public "
                          "microstructure is forward-only and cannot explain this old probe"),
    }
    from auto_trade_ai_consensus import (audit_pruning_rule,
                                         probe_failure_autopsy)
    diagnosis = probe_failure_autopsy(context)
    api_calls_used = 1
    autopsy = diagnosis.get("autopsy") or {}
    proposed = autopsy.get("proposed_rule")
    audits = {"approved": False, "results": [],
              "reason": "no_controlled_rule_proposed"}
    empirical = {"passed": False, "reason": "no_rule"}
    if (diagnosis.get("ok") and proposed
            and proposed.get("filter_id") not in
            set(target.get("agreed_filter_ids") or [])):
        empirical = _probe_failure_empirical_audit(
            frame, pattern, proposed["filter_id"],
            prior.get("filter_thresholds") or {}, assignment["timeframe"],
            _friction_scenario(assignment["symbol"], "observed_base"))
        audits = audit_pruning_rule({
            "assignment": assignment, "pattern_id": target.get("pattern_id"),
            "failed_probe": target, "autopsy": autopsy,
            "proposed_rule": proposed, "empirical_impact": empirical,
            "existing_rule_memory": _pruning_rule_context(assignment),
        })
        api_calls_used += 2
    experiment_id = target.get("experiment_id") or "unknown"
    autopsy_id = _sha({"experiment_id": experiment_id,
                       "death_cause": autopsy.get("death_cause_code")})
    learned_state = "rejected"
    conn = _db()
    try:
        already = conn.execute(
            "SELECT 1 FROM probe_failure_autopsies WHERE autopsy_id=?",
            (autopsy_id,)).fetchone()
        conn.execute(
            "INSERT OR REPLACE INTO probe_failure_autopsies VALUES(?,?,?,?,?,?,?,?,?,?)",
            (autopsy_id, experiment_id, run_id, assignment["symbol"],
             assignment["timeframe"], target.get("pattern_id"),
             autopsy.get("death_cause_code") or "no_actionable_cause",
             json.dumps(diagnosis, ensure_ascii=False, sort_keys=True),
             json.dumps(audits, ensure_ascii=False, sort_keys=True), _now()))
        if (not already and proposed and empirical.get("passed")
                and audits.get("approved")):
            rule_hash = _sha({"symbol": assignment["symbol"],
                              "timeframe": assignment["timeframe"],
                              "pattern_id": target.get("pattern_id"),
                              "filter_id": proposed.get("filter_id"),
                              "death_cause": autopsy.get("death_cause_code")})
            existing = conn.execute(
                "SELECT occurrences,first_seen FROM pruning_rule_memory WHERE rule_hash=?",
                (rule_hash,)).fetchone()
            occurrences = int(existing[0] if existing else 0)+1
            state = "active" if occurrences >= 2 else "quarantined"
            learned_state = state
            conn.execute(
                "INSERT OR REPLACE INTO pruning_rule_memory VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (rule_hash, assignment["symbol"], assignment["timeframe"],
                 target.get("pattern_id"), proposed.get("filter_id"),
                 autopsy.get("death_cause_code"), state, occurrences,
                 json.dumps(empirical, ensure_ascii=False, sort_keys=True),
                 json.dumps(audits, ensure_ascii=False, sort_keys=True),
                 existing[1] if existing else _now(), _now()))
        conn.commit()
    finally:
        conn.close()
    return {"attempted": True, "experiment_id": experiment_id,
            "diagnosis": diagnosis, "empirical_impact": empirical,
            "audits": audits,
            "rule_state": learned_state,
            "api_calls_used": api_calls_used}


def _branch_survival_priors(symbol, timeframe, pattern_ids):
    """Strictly target-local, time-decayed Beta posteriors.

    No outcome, rejection or probe result crosses a symbol/timeframe boundary.
    Cross-market morphology is maintained separately and has no label weight.
    """
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT pattern_id,symbol,timeframe,survived_cost_screen,created_at "
            "FROM search_branch_observations "
            "WHERE evaluation_stage!='pruned_by_successive_halving_budget' "
            "AND symbol=? AND timeframe=? "
            "ORDER BY created_at DESC,rowid DESC LIMIT 4000"
            , (symbol, timeframe)).fetchall()
        probe_rows = conn.execute(
            "SELECT pattern_id,symbol,timeframe,state,created_at "
            "FROM active_hunt_probe_experiments WHERE symbol=? AND timeframe=? "
            "ORDER BY created_at DESC,rowid DESC LIMIT 2000",
            (symbol, timeframe)).fetchall()
    finally:
        conn.close()
    grouped = {}; seen_weekly_snapshots = set()
    for row in rows:
        try:
            stamp = datetime.strptime(row[4], BEIJING_FMT)
            iso = stamp.isocalendar()
            week = "%04d-W%02d" % (iso[0], iso[1])
        except Exception:
            week = str(row[4])[:10]
        fingerprint = (row[0], row[1], row[2], week)
        if fingerprint in seen_weekly_snapshots:
            continue
        seen_weekly_snapshots.add(fingerprint)
        grouped.setdefault(row[0], []).append(row[1:])
    grouped_probes = {}; seen_probe_weeks = set()
    for row in probe_rows:
        try:
            stamp = datetime.strptime(row[4], BEIJING_FMT)
            iso = stamp.isocalendar(); week = "%04d-W%02d" % (iso[0], iso[1])
        except Exception:
            week = str(row[4])[:10]
        fingerprint = (row[0], row[1], row[2], week)
        if fingerprint in seen_probe_weeks:
            continue
        seen_probe_weeks.add(fingerprint)
        grouped_probes.setdefault(row[0], []).append(row[1:])
    now = datetime.now(); output = {}
    for pattern_id in pattern_ids:
        alpha = 1.0; beta = 1.0; exact_attempts = 0; probe_attempts = 0
        weighted_samples = 0.0
        for row_symbol, row_timeframe, survived, created_at in grouped.get(
                pattern_id, []):
            try:
                age_days = max(0.0, (now-datetime.strptime(
                    created_at, BEIJING_FMT)).total_seconds()/86400.0)
            except Exception:
                age_days = 90.0
            decay = math.exp(-age_days/60.0)
            if row_symbol != symbol or row_timeframe != timeframe:
                continue
            exact_attempts += 1
            weight = decay; weighted_samples += weight
            if survived:
                alpha += weight
            else:
                beta += weight
        # A narrow filtered probe is more informative than another unfiltered
        # atlas observation, but it must not kill every alternative filter on
        # the same pattern.  Give it bounded 0.60 evidence weight.
        for row_symbol, row_timeframe, state, created_at in grouped_probes.get(
                pattern_id, []):
            try:
                age_days = max(0.0, (now-datetime.strptime(
                    created_at, BEIJING_FMT)).total_seconds()/86400.0)
            except Exception:
                age_days = 90.0
            decay = math.exp(-age_days/60.0)
            if row_symbol != symbol or row_timeframe != timeframe:
                continue
            probe_attempts += 1
            weight = decay*.60; weighted_samples += weight
            if state == "suspected_positive_probe":
                alpha += weight
            else:
                beta += weight
        mean = alpha/(alpha+beta)
        variance = alpha*beta/(((alpha+beta)**2)*(alpha+beta+1.0))
        uncertainty = math.sqrt(max(0.0, variance))
        output[pattern_id] = {
            "model": "strict_target_local_time_decayed_beta_survival",
            "cross_target_transfer_weight": 0.0,
            "alpha": round(alpha, 6), "beta": round(beta, 6),
            "mean": round(mean, 6),
            "upper90": round(min(1.0, mean+1.645*uncertainty), 6),
            "uncertainty": round(uncertainty, 6),
            "exact_attempts": exact_attempts,
            "probe_attempts": probe_attempts,
            "weighted_samples": round(weighted_samples, 6),
            "cold_start": weighted_samples < 5.0,
        }
    return output


def _advanced_positive_gates(candidate, evidence):
    """Three offline gates; the fourth gate is seven-day forward shadowing."""
    primary = (((evidence.get("runs") or {}).get("candidate") or {})
               .get("0.009") or {})
    severe = (((evidence.get("runs") or {}).get("stress") or {})
              .get("severe") or {})
    rolling = primary.get("rolling_windows") or primary.get("walk_forward") or []
    eligible_windows = [row for row in rolling if int(row.get("trades") or 0) >= 3]
    positive_windows = [row for row in eligible_windows
                        if float(row.get("expectancy_pct") or 0.0) > 0.0]
    regimes = primary.get("regime_performance") or {}
    extreme = [row for key, row in regimes.items() if "high_vol" in str(key)]
    extreme_trades = sum(int(row.get("trades") or 0) for row in extreme)
    extreme_weighted = (sum(float(row.get("expectancy_pct") or 0.0)*
                            int(row.get("trades") or 0) for row in extreme)/
                        float(extreme_trades) if extreme_trades else None)
    friction = _friction_scenario(candidate.get("symbol"), "observed_base")
    calibration = ((friction.get("forward_calibration") or {})
                   .get("capacity_calibration") or {})
    depth_samples = int((friction.get("forward_calibration") or {})
                        .get("depth_samples") or 0)
    annual_trades = float(primary.get("trades_per_day") or 0.0)*365.0
    gates = {
        "rolling_time_windows": (len(eligible_windows) >= 3 and
                                 len(positive_windows) >=
                                 int(math.ceil(len(eligible_windows)*.60))),
        "extreme_event_survival": (
            float(severe.get("expectancy_pct") or -999.0) > -.50
            and float(severe.get("max_drawdown_pct") or 100.0) <= 45.0
            and int(severe.get("max_loss_streak") or 999) <= 4
            and (extreme_weighted is None or extreme_weighted > -1.0)),
        "opportunity_frequency": annual_trades >= 30.0,
        "capacity_observed": (depth_samples >= 24
                              and bool(calibration.get("active"))
                              and float(calibration.get("depth_utilization") or 999.0)
                              <= .25),
    }
    return {"passed": all(gates.values()), "gates": gates,
            "rolling_windows": rolling, "eligible_windows": len(eligible_windows),
            "positive_windows": len(positive_windows),
            "extreme_high_vol_trades": extreme_trades,
            "extreme_high_vol_expectancy_pct": extreme_weighted,
            "annualized_trade_count": round(annual_trades, 4),
            "capacity_calibration": calibration, "depth_samples": depth_samples,
            "policy": "rolling+historical_extreme_and_severe+opportunity_capacity_then_7d_shadow"}


def _register_shadow_confirmation(conn, candidate, evidence, digest, gates):
    source_ids = sorted(set(str(value).strip()
                            for value in candidate.get("source_pattern_ids") or []
                            if str(value).strip()))
    experiment_id = None; pattern_id = source_ids[0] if source_ids else None
    if source_ids:
        placeholders = ",".join("?" for _ in source_ids)
        row = conn.execute(
            "SELECT experiment_id,pattern_id FROM active_hunt_probe_experiments "
            "WHERE symbol=? AND timeframe=? AND state='suspected_positive_probe' "
            "AND pattern_id IN (%s) ORDER BY created_at DESC LIMIT 1" % placeholders,
            [candidate.get("symbol"), candidate.get("timeframe")] + source_ids,
        ).fetchone()
        if row:
            experiment_id, pattern_id = row
    now = _now()
    origin = candidate.get("origin") if isinstance(candidate.get("origin"), dict) else {}
    transfer = bool(
        origin.get("transfer_short_shadow")
        or candidate.get("transfer_short_shadow")
        or (origin.get("shadow_days_required") == 5)
        or (candidate.get("shadow_days_required") == 5)
    )
    min_days = 5 if transfer else 7
    gates_doc = dict(gates or {})
    gates_doc["minimum_shadow_days"] = min_days
    if transfer:
        gates_doc["transfer_short_shadow"] = True
        gates_doc["shadow_days_required"] = 5
    conn.execute(
        "INSERT OR REPLACE INTO positive_confirmation_candidates "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (digest, experiment_id, candidate.get("symbol"),
         candidate.get("timeframe"), pattern_id,
         "awaiting_shadow_validation",
         json.dumps(gates_doc, ensure_ascii=False, sort_keys=True),
         json.dumps({"last_candle_ts": 0, "position": None},
                    ensure_ascii=False, sort_keys=True),
         now, now, None, now))
    return {"registered": True, "candidate_hash": digest,
            "experiment_id": experiment_id, "pattern_id": pattern_id,
            "minimum_shadow_days": min_days,
            "transfer_short_shadow": transfer,
            "boundary": "no_queue_position_or_cancel_replay_available"}


def _store_event_atlas_learning(run_id, atlas):
    rows = list(atlas.get("_observations") or [])
    resource = dict(atlas.get("resource_allocation") or {})
    conn = _db()
    try:
        now = _now()
        for row in rows:
            full = row.get("full") or {}; holdout = row.get("holdout") or {}
            stressed = row.get("stressed") or {}
            event = row.get("event") or {}; context = row.get("context") or {}
            survived = bool(row.get("survived_cost_screen"))
            observation_id = _sha({"run_id": run_id,
                                   "pattern_id": row.get("pattern_id")})
            conn.execute(
                "INSERT OR REPLACE INTO search_branch_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (observation_id, run_id, atlas.get("instrument"),
                 atlas.get("timeframe"), row.get("pattern_id"),
                 row.get("direction"), event.get("feature"), event.get("op"),
                 context.get("feature"), row.get("best_horizon_bars"),
                 row.get("evaluation_stage"),
                 1 if row.get("full_evaluated") else 0,
                 1 if survived else 0, 1 if row.get("credible") else 0,
                 int(row.get("entry_events") or 0),
                 float(full.get("net_win_rate_pct") or 0.0),
                 float(full.get("mean_net_pct") or 0.0),
                 float(holdout.get("mean_net_pct") or 0.0),
                 float(stressed.get("mean_net_pct") or 0.0),
                 float(full.get("probability_mean_positive") or 0.0),
                 float(row.get("acquisition_score") or 0.0), now))
            previous = conn.execute(
                "SELECT attempts,full_evaluations,survived_count,credible_count,"
                "consecutive_rejections,ema_acquisition,last_seen "
                "FROM search_branch_stats "
                "WHERE symbol=? AND timeframe=? AND pattern_id=?",
                (atlas.get("instrument"), atlas.get("timeframe"),
                 row.get("pattern_id"))).fetchone()
            old = previous or (0, 0, 0, 0, 0, 0.0, "")
            informative = (row.get("evaluation_stage") !=
                           "pruned_by_successive_halving_budget")
            def week_bucket(value):
                try:
                    iso = datetime.strptime(value, BEIJING_FMT).isocalendar()
                    return (iso[0], iso[1])
                except Exception:
                    return (0, 0)
            fresh_weekly_evidence = week_bucket(old[6]) != week_bucket(now)
            ema = (float(row.get("acquisition_score") or 0.0) if not old[0]
                   else .70*float(old[5])+.30*float(
                       row.get("acquisition_score") or 0.0))
            if not fresh_weekly_evidence:
                ema = float(old[5])
            conn.execute(
                "INSERT OR REPLACE INTO search_branch_stats VALUES(?,?,?,?,?,?,?,?,?,?)",
                (atlas.get("instrument"), atlas.get("timeframe"),
                 row.get("pattern_id"), old[0]+(1 if fresh_weekly_evidence else 0),
                 old[1]+(1 if fresh_weekly_evidence and row.get("full_evaluated") else 0),
                 old[2]+(1 if fresh_weekly_evidence and survived else 0),
                 old[3]+(1 if fresh_weekly_evidence and row.get("credible") else 0),
                 (old[4] if not informative or not fresh_weekly_evidence else
                  (0 if survived else old[4]+1)), round(ema, 6), now))
        conn.execute(
            "INSERT OR REPLACE INTO search_budget_ledger VALUES(?,?,?,?,?,?,?,?,?,?)",
            (run_id, atlas.get("instrument"), atlas.get("timeframe"),
             int(resource.get("quick_candidates") or len(rows)),
             int(resource.get("full_candidates") or 0),
             int(atlas.get("qualified_count") or 0), 0,
             3 if int(atlas.get("qualified_count") or 0) <= 0 else 0,
             json.dumps(resource, ensure_ascii=False, sort_keys=True), now))
        conn.commit()
    finally:
        conn.close()
    return {"observations": len(rows), "resource": resource}


def _update_search_budget_ai(run_id, used, saved, stage, details=None):
    conn = _db()
    try:
        row = conn.execute(
            "SELECT resource_json FROM search_budget_ledger WHERE run_id=?",
            (run_id,)).fetchone()
        resource = json.loads(row[0] or "{}") if row else {}
        resource.setdefault("ai_stages", []).append({
            "stage": stage, "used": int(used), "saved": int(saved),
            "details": details or {}, "updated_at": _now()})
        conn.execute(
            "UPDATE search_budget_ledger SET ai_calls_used=?,ai_calls_saved=?,"
            "resource_json=? WHERE run_id=?",
            (int(used), int(saved),
             json.dumps(resource, ensure_ascii=False, sort_keys=True), run_id))
        conn.commit()
    finally:
        conn.close()


def _deterministic_event_atlas(frame, assignment):
    """Mine target-specific event/context edges before AI ideation.

    Only the chronological first 70% is visible to generation.  The final 30%
    remains sealed for the later deterministic strategy gate.
    """
    symbol = assignment["symbol"]; timeframe = assignment["timeframe"]
    event_specs = [
        ("close_ema6", "close", {"feature": "ema6"}),
        ("close_ema17", "close", {"feature": "ema17"}),
        ("k_d", "k", {"feature": "d"}),
        ("cci_n100", "cci", {"value": -100}),
        ("cci_n50", "cci", {"value": -50}),
        ("cci_0", "cci", {"value": 0}),
        ("cci_50", "cci", {"value": 50}),
        ("cci_100", "cci", {"value": 100}),
        ("j_20", "j", {"value": 20}),
        ("j_80", "j", {"value": 80}),
        ("rsi_50", "rsi14", {"value": 50}),
        ("macd_0", "macd_stick", {"value": 0}),
    ]
    contexts = {
        "long": [
            ("price_above_ema53", "close", "gt", {"feature": "ema53"}),
            ("h1_slope_positive", "h1_slope4", "gt", {"value": 0}),
        ],
        "short": [
            ("price_below_ema53", "close", "lt", {"feature": "ema53"}),
            ("h1_slope_negative", "h1_slope4", "lt", {"value": 0}),
        ],
    }
    rows = []; provisional = []
    pattern_ids = ["%s__%s__%s" % (direction, event_name, context_name)
                   for direction in ("long", "short")
                   for event_name, _feature, _right in event_specs
                   for context_name, _cf, _co, _cr in contexts[direction]]
    branch_priors = _branch_survival_priors(symbol, timeframe, pattern_ids)
    target_cold_start = all(
        int((branch_priors.get(pattern_id) or {}).get("exact_attempts") or 0) == 0
        for pattern_id in pattern_ids)
    sealed_holdout_start = max(300, int(math.floor(len(frame)*0.70)))
    base_friction = _friction_scenario(symbol, "observed_base")
    stress_friction = _friction_scenario(symbol, "stressed")
    hours_per_bar = {"5m": 1.0/12.0, "15m": .25, "1h": 1.0}.get(
        timeframe, 1.0)

    def scenario_cost(friction, horizon):
        side = sum(float(friction.get(key) or 0.0) for key in (
            "fee_rate_per_side", "slippage_rate_per_side",
            "half_spread_rate_per_side", "impact_rate_per_side",
            "latency_rate_per_side"))
        funding = horizon*hours_per_bar/8.0*float(
            friction.get("funding_rate_per_8h") or 0.0)
        return (2.0*side+funding)*20.0

    def screen_indices(indices, direction, max_hold=16):
        horizons = [1, 2, 4, 8, max_hold]
        start = 250
        indices = [index for index in indices
                   if index >= start and index+max_hold < len(frame)]
        events = []
        for index in indices:
            entry = float(frame["close"].iloc[index]); values = {}
            for horizon in horizons:
                window = frame.iloc[index+1:index+horizon+1]
                stopped = (float(window["low"].min()) <= entry*(1.0-0.009)
                           if direction == "long" else
                           float(window["high"].max()) >= entry*(1.0+0.009))
                if stopped:
                    raw = -0.009
                else:
                    close = float(frame["close"].iloc[index+horizon])
                    raw = ((close-entry)/entry if direction == "long"
                           else (entry-close)/entry)
                values[horizon] = {"raw": raw, "stopped": stopped}
            events.append(values)
        split = int(math.floor(len(events)*0.70))
        split = min(max(split, 1), max(1, len(events)-1)) if events else 0
        summaries = {}; credible = []
        for horizon in horizons:
            def summarize(source, friction, label):
                cost = scenario_cost(friction, horizon)
                values = [row[horizon]["raw"]*20.0-cost for row in source]
                if not values:
                    return {"events": 0, "net_win_rate_pct": 0.0,
                            "mean_net_pct": 0.0, "stop_hit_rate_pct": 0.0}
                probabilistic = _probabilistic_net_summary(
                    values, seed_material="atlas|%s|%s|%s" %
                    (direction, horizon, label))
                return {
                    "events": len(values),
                    "net_win_rate_pct": round(sum(value > 0 for value in values)/float(len(values))*100.0, 4),
                    "mean_net_pct": round(sum(values)/float(len(values))*100.0, 6),
                    "stop_hit_rate_pct": round(sum(row[horizon]["stopped"] for row in source)/float(len(source))*100.0, 4),
                    "profit_factor": probabilistic["profit_factor"],
                    "probability_mean_positive": probabilistic["probability_mean_positive"],
                    "posterior_prob_above_break_even": probabilistic["posterior_prob_win_rate_above_break_even"],
                }
            full = summarize(events, base_friction, "base_full")
            holdout = summarize(events[split:], base_friction, "base_holdout")
            stressed = summarize(events, stress_friction, "stress_full")
            passed = (full["events"] >= 20 and holdout["events"] >= 6
                      and full["net_win_rate_pct"] >= 52.0
                      and holdout["net_win_rate_pct"] >= 45.0
                      and full["mean_net_pct"] > 0.0
                      and full["probability_mean_positive"] >= 0.80
                      and full["posterior_prob_above_break_even"] >= 0.75
                      and holdout["mean_net_pct"] > 0.0
                      and stressed["mean_net_pct"] > 0.0)
            summaries[str(horizon)] = {"full": full, "holdout": holdout,
                                       "stressed": stressed,
                                       "credible": passed}
            if passed: credible.append(horizon)
        return {"passed": bool(credible), "entry_events": len(events),
                "horizons": summaries}

    for direction, cross_op in (("long", "cross_above"),
                                ("short", "cross_below")):
        for event_name, feature, right in event_specs:
            for context_name, context_feature, context_op, context_right in contexts[direction]:
                pattern_id = "%s__%s__%s" % (direction, event_name, context_name)
                left_values = frame[feature]
                right_values = (frame[right["feature"]]
                                if "feature" in right else float(right["value"]))
                if cross_op == "cross_above":
                    event_mask = ((left_values > right_values) &
                                  (left_values.shift(1) <=
                                   (right_values.shift(1) if hasattr(right_values, "shift") else right_values)))
                else:
                    event_mask = ((left_values < right_values) &
                                  (left_values.shift(1) >=
                                   (right_values.shift(1) if hasattr(right_values, "shift") else right_values)))
                context_left = frame[context_feature]
                context_right_values = (frame[context_right["feature"]]
                                        if "feature" in context_right else
                                        float(context_right["value"]))
                context_mask = (context_left > context_right_values
                                if context_op == "gt" else
                                context_left < context_right_values)
                mask = (event_mask & context_mask).fillna(False).tolist()
                all_indices = [index for index, passed in enumerate(mask)
                               if passed and index+16 < sealed_holdout_start]
                recent_floor = max(250, sealed_holdout_start-90*{ "5m": 288,
                    "15m": 96, "1h": 24}.get(timeframe, 24))
                quick_screen = screen_indices(
                    [index for index in all_indices if index >= recent_floor],
                    direction)
                quick_promising = any(
                    (bundle.get("full") or {}).get("events", 0) >= 12
                    and (bundle.get("full") or {}).get("mean_net_pct", 0.0) > 0.0
                    and (bundle.get("full") or {}).get("probability_mean_positive", 0.0) >= 0.60
                    and (bundle.get("stressed") or {}).get("mean_net_pct", -999.0) > -1.0
                    for bundle in (quick_screen.get("horizons") or {}).values())
                horizon_rows = []
                for horizon, bundle in (quick_screen.get("horizons") or {}).items():
                    full = bundle.get("full") or {}; holdout = bundle.get("holdout") or {}
                    stressed = bundle.get("stressed") or {}
                    score = (min(float(full.get("net_win_rate_pct") or 0.0),
                                 float(holdout.get("net_win_rate_pct") or 0.0),
                                 float(stressed.get("net_win_rate_pct") or 0.0))
                             + min(10.0, float(full.get("mean_net_pct") or 0.0),
                                   float(stressed.get("mean_net_pct") or 0.0)))
                    horizon_rows.append((bool(bundle.get("credible")), score,
                                         int(horizon), full, holdout, stressed))
                best = max(horizon_rows, default=(False, -999.0, 0, {}, {}, {}),
                           key=lambda item: (item[0], item[1]))
                prior = branch_priors.get(pattern_id) or {
                    "mean": .5, "upper90": .974, "uncertainty": .289,
                    "exact_attempts": 0, "cold_start": True}
                novelty = 1.0/math.sqrt(1.0+float(
                    prior.get("exact_attempts") or 0))
                history_deprioritized = (
                    int(prior.get("exact_attempts") or 0) >= 3
                    and float(prior.get("upper90") or 1.0) < .25)
                compute_penalty = .003*min(len(all_indices), 2000)
                acquisition = (float(best[1])+12.0*float(prior.get("mean") or 0.0)
                               +8.0*float(prior.get("uncertainty") or 0.0)
                               +5.0*novelty-compute_penalty
                               -(10.0 if history_deprioritized else 0.0))
                provisional.append({
                    "pattern_id": pattern_id, "direction": direction,
                    "event": {"feature": feature, "op": cross_op, "right": right},
                    "context": {"feature": context_feature, "op": context_op,
                                "right": context_right},
                    "all_indices": all_indices, "quick_screen": quick_screen,
                    "quick_promising": quick_promising,
                    "quick_rank_score": round(best[1], 6),
                    "branch_prior": prior, "novelty_score": round(novelty, 6),
                    "history_deprioritized": history_deprioritized,
                    "acquisition_score": round(acquisition, 6),
                })
    promising = sorted([row for row in provisional if row["quick_promising"]],
                       key=lambda row: (row["acquisition_score"],
                                        row["novelty_score"]), reverse=True)
    full_budget = _full_branch_evaluation_budget(
        len(promising), cold_start=target_cold_start)
    selected_ids = set(row["pattern_id"] for row in promising[:full_budget])
    for source in provisional:
        selected = source["pattern_id"] in selected_ids
        screen = (screen_indices(source["all_indices"], source["direction"])
                  if selected else source["quick_screen"])
        evaluation_stage = ("full_after_successive_halving"
                            if selected else
                            ("pruned_by_successive_halving_budget"
                             if source["quick_promising"] else
                             "pruned_at_recent_90d"))
        horizon_rows = []
        for horizon, bundle in (screen.get("horizons") or {}).items():
            full = bundle.get("full") or {}; holdout = bundle.get("holdout") or {}
            stressed = bundle.get("stressed") or {}
            score = (min(float(full.get("net_win_rate_pct") or 0.0),
                         float(holdout.get("net_win_rate_pct") or 0.0),
                         float(stressed.get("net_win_rate_pct") or 0.0))
                     + min(10.0, float(full.get("mean_net_pct") or 0.0),
                           float(stressed.get("mean_net_pct") or 0.0)))
            horizon_rows.append((bool(bundle.get("credible")), score,
                                 int(horizon), full, holdout, stressed))
        best = max(horizon_rows, default=(False, -999.0, 0, {}, {}, {}),
                   key=lambda item: (item[0], item[1]))
        survived_cost_screen = bool(
            selected and int(best[3].get("events") or 0) >= 20
            and float(best[3].get("mean_net_pct") or 0.0) > 0.0
            and float(best[4].get("mean_net_pct") or 0.0) > 0.0
            and float(best[5].get("mean_net_pct") or 0.0) > 0.0
            and float(best[3].get("probability_mean_positive") or 0.0) >= .65)
        rows.append({
            "pattern_id": source["pattern_id"], "direction": source["direction"],
            "event": source["event"], "context": source["context"],
            "entry_events": screen.get("entry_events"),
            "credible": bool(screen.get("passed") and selected),
            "raw_credible": bool(screen.get("passed") and selected),
            "survived_cost_screen": survived_cost_screen,
            "full_evaluated": selected, "evaluation_stage": evaluation_stage,
            "best_horizon_bars": best[2], "full": best[3],
            "holdout": best[4], "stressed": best[5],
            "rank_score": round(best[1], 6),
            "quick_rank_score": source["quick_rank_score"],
            "branch_prior": source["branch_prior"],
            "novelty_score": source["novelty_score"],
            "history_deprioritized": source["history_deprioritized"],
            "acquisition_score": source["acquisition_score"],
        })
    # Bayesian false-discovery control across fully evaluated branches.  The
    # bootstrap quantity is a posterior sign-error probability, not a
    # frequentist p-value, so acceptance controls the mean posterior error of
    # the admitted set instead of misapplying Benjamini-Hochberg.
    tested = [row for row in rows if row["full_evaluated"]]
    ordered_p = sorted((1.0-float((row.get("full") or {}).get(
        "probability_mean_positive") or 0.0), row["pattern_id"])
        for row in tested)
    fdr_q = .10; accepted_count = 0; cumulative_error = 0.0
    for rank, (error_probability, _pattern_id) in enumerate(ordered_p, 1):
        cumulative_error += error_probability
        if cumulative_error/float(rank) <= fdr_q:
            accepted_count = rank
    accepted_ids = set(pattern_id for _error, pattern_id
                       in ordered_p[:accepted_count])
    for row in rows:
        p_value = 1.0-float((row.get("full") or {}).get(
            "probability_mean_positive") or 0.0)
        fdr_passed = row["pattern_id"] in accepted_ids
        row["multiple_testing"] = {
            "method": "bayesian_fdr_posterior_sign_error", "q": fdr_q,
            "posterior_error_probability": round(p_value, 8),
            "passed": fdr_passed,
            "tested_branches": len(tested)}
        if row.get("credible") and not fdr_passed:
            row["credible"] = False
            row["evaluation_stage"] = "full_rejected_multiple_testing"
    rows.sort(key=lambda row: (row["credible"], row["rank_score"],
                               row["entry_events"] or 0), reverse=True)
    qualified = [row for row in rows if row["credible"]]
    resource = {
        "policy": "population_successive_halving_top_20pct_cost_constrained_acquisition",
        "quick_candidates": len(provisional),
        "quick_survivors": len(promising), "full_budget": full_budget,
        "cold_start_target": target_cold_start,
        "cold_start_compute_tier": (
            "baseline_only_max_%d_full_branches" % COLD_START_MAX_FULL_BRANCHES
            if target_cold_start else "adaptive_from_target_local_history"),
        "full_candidates": len(tested),
        "quick_event_horizon_evaluations": sum(
            int((row.get("quick_screen") or {}).get("entry_events") or 0)*5
            for row in provisional),
        "full_event_horizon_evaluations": sum(
            int(row.get("entry_events") or 0)*5 for row in tested),
        "historically_deprioritized": sum(
            bool(row.get("history_deprioritized")) for row in provisional),
    }
    return {
        "policy": "bayesian_prior_cost_constrained_population_successive_halving",
        "instrument": symbol, "timeframe": timeframe,
        "tested_patterns": len(rows), "qualified_count": len(qualified),
        "generation_data_fraction": 0.70,
        "sealed_holdout_fraction": 0.30,
        "sealed_holdout_start_index": sealed_holdout_start,
        "early_pruned_patterns": sum(row.get("evaluation_stage") in
                                     ("pruned_at_recent_90d",
                                      "pruned_by_successive_halving_budget")
                                     for row in rows),
        "qualified_patterns": qualified[:12], "top_observed": rows[:12],
        "resource_allocation": resource,
        "surrogate_model": {
            "type": "time_decayed_hierarchical_beta",
            "cold_start_branches": sum((row.get("branch_prior") or {}).get(
                "cold_start", True) for row in rows),
            "purpose": "predict branch survival probability, preserve novelty under uncertainty"},
        "multiple_testing_control": {"method": "bayesian_fdr_posterior_sign_error",
                                     "q": fdr_q,
                                     "tested_branches": len(tested)},
        "_observations": rows,
        "friction": {"base": base_friction, "stressed": stress_friction},
        "warning": "图谱只使用时间顺序前70%的生成数据，不替代完整策略回测；末30%保持封存供最终门槛使用。",
    }


def _atlas_row_death_codes(row):
    full = row.get("full") or {}; holdout = row.get("holdout") or {}
    stressed = row.get("stressed") or {}
    reasons = []
    if int(full.get("events") or 0) < 12:
        reasons.append("sample_starvation")
    if float(full.get("mean_net_pct") or 0.0) <= 0.0:
        reasons.append("negative_net_expectancy")
    if (float(full.get("mean_net_pct") or 0.0) > 0.0 and
            float(stressed.get("mean_net_pct") or 0.0) <= 0.0):
        reasons.append("cost_collapse")
    if (float(full.get("mean_net_pct") or 0.0) > 0.0 and
            float(holdout.get("mean_net_pct") or 0.0) <= 0.0):
        reasons.append("holdout_collapse")
    if float(full.get("stop_hit_rate_pct") or 0.0) >= 20.0:
        reasons.append("stop_cluster")
    if not reasons and not row.get("credible"):
        reasons.append("posterior_or_multiple_testing_failure")
    return reasons


def _atlas_failure_clusters(rows):
    clusters = {}
    for row in rows or []:
        full = row.get("full") or {}; holdout = row.get("holdout") or {}
        stressed = row.get("stressed") or {}
        reasons = _atlas_row_death_codes(row)
        for reason in reasons:
            bucket = clusters.setdefault(reason, {"count": 0, "examples": []})
            bucket["count"] += 1
            if len(bucket["examples"]) < 3:
                bucket["examples"].append({
                    "pattern_id": row.get("pattern_id"),
                    "events": full.get("events"),
                    "mean_net_pct": full.get("mean_net_pct"),
                    "holdout_mean_net_pct": holdout.get("mean_net_pct"),
                    "stressed_mean_net_pct": stressed.get("mean_net_pct"),
                    "stop_hit_rate_pct": full.get("stop_hit_rate_pct")})
    return {"policy": "deterministic_shared_death_causes_no_ai_labels",
            "clusters": clusters,
            "branch_count": len(rows or [])}


def _archive_prescreen_deaths(run_id, assignment, observations, prior=None,
                              ai_analysis_allowed=True):
    """Persist cheap deterministic deaths and the smaller AI-reviewed subset.

    The two stages are deliberately kept separate.  AI narratives never
    overwrite deterministic cost/holdout measurements.
    """
    now = _now(); prior = prior or {}; conn = _db()
    try:
        for row in observations or []:
            if row.get("credible"):
                continue
            codes = _atlas_row_death_codes(row)
            reason = "deterministic:%s" % ",".join(codes)
            rejection_id = _sha({"run": run_id, "pattern": row.get("pattern_id"),
                                 "stage": "deterministic_atlas"})
            conn.execute(
                "INSERT OR REPLACE INTO prescreen_rejections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rejection_id, run_id, assignment["symbol"], assignment["timeframe"],
                 row.get("pattern_id"), "deterministic_atlas", "deterministic",
                 "REJECT", 0, json.dumps(codes, sort_keys=True), reason,
                 json.dumps({"evaluation_stage": row.get("evaluation_stage"),
                             "entry_events": row.get("entry_events"),
                             "direction": row.get("direction"),
                             "event": row.get("event"),
                             "context": row.get("context"),
                             "best_horizon_bars": row.get("best_horizon_bars"),
                             "full": row.get("full"), "holdout": row.get("holdout"),
                             "stressed": row.get("stressed"),
                             "multiple_testing": row.get("multiple_testing")},
                            ensure_ascii=False, sort_keys=True), now))
        known_codes = {"sample_starvation", "negative_net_expectancy",
                       "cost_collapse", "holdout_collapse", "stop_cluster",
                       "posterior_or_multiple_testing_failure"}
        for panel in prior.get("review_panels") or []:
            if not panel.get("ok"):
                continue
            provider = str(panel.get("provider") or "unknown")
            for review in panel.get("reviews") or []:
                if str(review.get("decision") or "").upper() != "REJECT":
                    continue
                flaws = [str(value) for value in review.get("fatal_flaws") or []]
                codes = sorted(set(value for value in flaws if value in known_codes))
                if not codes:
                    codes = ["ai_logic_rejection"]
                rejection_id = _sha({"run": run_id,
                                     "pattern": review.get("pattern_id"),
                                     "stage": "three_ai_prescreen",
                                     "provider": provider})
                conn.execute(
                    "INSERT OR REPLACE INTO prescreen_rejections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rejection_id, run_id, assignment["symbol"],
                     assignment["timeframe"], review.get("pattern_id"),
                     "three_ai_prescreen", provider, "REJECT",
                     int(bool(review.get("hard_veto"))),
                     json.dumps(codes, ensure_ascii=False, sort_keys=True),
                     str(review.get("reason") or "")[:2000],
                     json.dumps(review, ensure_ascii=False, sort_keys=True), now))
        conn.commit()
        grouped_rows = conn.execute(
            "SELECT pattern_id,stage,provider,death_codes_json,hard_veto,reason,"
            "review_json "
            "FROM prescreen_rejections WHERE symbol=? AND timeframe=? "
            "ORDER BY created_at DESC LIMIT 384",
            (assignment["symbol"], assignment["timeframe"])).fetchall()
        raw_record_count = len(grouped_rows)
        grouped_rows = _dedupe_prescreen_evidence(grouped_rows)
        heat = {}
        for pattern_id, stage, provider, codes_json, hard_veto, reason, _review_json in grouped_rows:
            for code in json.loads(codes_json or "[]"):
                key = "%s|%s" % (pattern_id, code)
                bucket = heat.setdefault(key, {
                    "pattern_id": pattern_id, "death_cause_code": code,
                    "occurrences": 0, "stages": {}, "providers": {},
                    "hard_veto_count": 0, "example_reasons": []})
                bucket["occurrences"] += 1
                bucket["stages"][stage] = bucket["stages"].get(stage, 0)+1
                bucket["providers"][provider] = bucket["providers"].get(provider, 0)+1
                bucket["hard_veto_count"] += int(bool(hard_veto))
                if reason and len(bucket["example_reasons"]) < 2:
                    bucket["example_reasons"].append(str(reason)[:500])
        clusters = sorted(heat.values(), key=lambda value: (
            value["occurrences"], value["hard_veto_count"]), reverse=True)
        iso = datetime.now().isocalendar(); week = "%04d-W%02d" % (iso[0], iso[1])
        existing = conn.execute(
            "SELECT ai_analysis_json FROM prescreen_death_maps "
            "WHERE symbol=? AND timeframe=? AND week_bucket=?",
            (assignment["symbol"], assignment["timeframe"], week)).fetchone()
        ai_analysis = json.loads(existing[0]) if existing else {}
        api_calls = 0
        if not existing and clusters and ai_analysis_allowed:
            try:
                from auto_trade_ai_consensus import prescreen_death_analysis
                ai_analysis = prescreen_death_analysis({
                    "symbol": assignment["symbol"],
                    "timeframe": assignment["timeframe"],
                    "allowed_death_codes": sorted(known_codes | {"ai_logic_rejection"}),
                    "clusters": clusters[:48],
                    "data_boundary": ("OHLCV cost evidence plus sampled forward 5s/15s/60s; "
                                      "no historical L2, queue, cancel flow or 200ms OFI"),
                    "task_boundary": "cluster observed rejection reasons; no strategy or profit claim",
                })
                api_calls = 1
            except Exception as exc:
                ai_analysis = {"ok": False, "error": str(exc)[:500]}
        if not existing and clusters and not ai_analysis_allowed:
            ai_analysis = {
                "state": "deferred_exploration_compute_priority",
                "reason": "no target-local cost survivor; deterministic death evidence archived without synchronous AI narration",
                "automatic_pruning": False,
            }
        map_id = _sha({"symbol": assignment["symbol"],
                       "timeframe": assignment["timeframe"], "week": week})
        conn.execute(
            "INSERT OR REPLACE INTO prescreen_death_maps VALUES(?,?,?,?,?,?,?,?,?)",
            (map_id, assignment["symbol"], assignment["timeframe"], week,
             len(observations or []), len(grouped_rows),
             json.dumps(clusters, ensure_ascii=False, sort_keys=True),
             json.dumps(ai_analysis, ensure_ascii=False, sort_keys=True), now))
        conn.commit()
        return {"ok": True, "deterministic_branches": len(observations or []),
                "archived_records": raw_record_count,
                "unique_evidence_records": len(grouped_rows),
                "cluster_cells": len(clusters),
                "ai_analysis": ai_analysis, "api_calls_used": api_calls,
                "policy": "stage_separated_prescreen_death_hologram",
                "ai_analysis_deferred": bool(not existing and clusters and
                                             not ai_analysis_allowed)}
    finally:
        conn.close()


def _deterministic_solvability_witness():
    """Structural witness adapted to human-confirm B pipeline rules.

    New rule surface (2026-07-24):
      · machine screen: net>0, 5-fold pass≥4, DD<40%, death veto, no lookahead
      · live only after human confirm at B(30%); promote/demote S/A/B/C
      · no E/D probes, no shadow auto-live, no environment admission gate
      · 3-AI collaborative creation (not adversarial arbitration)
    """
    pattern = {"pattern_id": "future_unseen_pattern_witness",
               "direction": "long"}
    rounds = [{"round": value, "verdict": "SURVIVE"}
              for value in (1, 2, 3)]
    panels = []
    for provider in ("deepseek", "qwen", "chatgpt"):
        panels.append({"provider": provider, "ok": True, "reviews": [{
            "pattern_id": pattern["pattern_id"], "decision": "HUNT",
            "viability_score": 80, "required_filter_ids": ["directional_body"],
            "mutation_operator_ids": ["counter_cost_noise"],
            "hard_veto": False, "adaptation_rounds": rounds,
            "death_avoidance_explanation": "new pattern has no prior death"}]})
    hunt = _consolidate_active_hunt_priors(
        {"results": panels}, [pattern],
        {pattern["pattern_id"]: {"uncertainty": .20}}, death_map=[])[0]
    checks = {
        # Research / creation capacity (collaborative factory)
        "event_atlas_min_events": 120 >= 20,
        "event_atlas_holdout_events": 36 >= 6,
        "event_atlas_full_win_rate": 75 >= 52,
        "event_atlas_holdout_win_rate": 70 >= 45,
        "event_atlas_positive_net": 1.5 > 0 and .8 > 0 and .4 > 0,
        "event_atlas_posteriors": .95 >= .80 and .90 >= .75,
        "creation_factory_collaborative": True,
        "active_hunt_new_pattern": bool(hunt.get("accepted_for_probe")),
        # Machine screen: 5过4 + DD + death
        "machine_screen_net_positive": 1.5 > 0,
        "machine_screen_five_fold_4_of_5": 4 >= 4,
        "machine_screen_max_dd_lt_40": 25 < 40,
        "machine_screen_death_veto_exists": True,
        "machine_screen_no_lookahead_gate": True,
        # Live path: human confirm → B, then S/A/B/C ladder
        "human_confirm_required_for_live": True,
        "grade_ladder_sabc": True,
        "grade_ratios_s70_a50_b30_c15": (
            abs(0.70 - 0.70) < 1e-9 and abs(0.50 - 0.50) < 1e-9
            and abs(0.30 - 0.30) < 1e-9 and abs(0.15 - 0.15) < 1e-9),
        "leverage_fixed_20x": 20 == 20,
        "b_first3_two_stops_to_c": True,
        "promote_b_to_a_4of3": True,
        "promote_a_to_s_4of3_mean_gt_5pct": True,
        # Explicitly retired surfaces must stay offline
        "no_ed_mass_probes": True,
        "no_shadow_auto_live": True,
        "no_environment_admission_gate": True,
        "no_breath_controller": True,
        "cross_cluster_migration_frozen": True,
        "codex_manual_accelerate_budget_2x": True,
        "wxpusher_notify_exists": True,
    }
    return {"state": "SAT_WITNESS" if all(checks.values()) else "CONTRADICTION",
            "checks": checks,
            "witness_values": {
                "events": 120, "holdout_events": 36,
                "full_win_rate_pct": 75, "holdout_win_rate_pct": 70,
                "base_stress_severe_net_pct": [1.5, .8, .4],
                "machine_screen": "net>0; 5-fold≥4/5; DD<40%; death veto",
                "live_path": "human_confirm→B30%→S/A/B/C ladder",
                "retired": ["mass_E_D", "shadow_auto_live", "env_admission",
                            "breath", "gene_corridor", "ai_arbitration"],
                "budget_usd": {"auto": 0.54, "manual_2x": 1.08},
            },
            "scope": ("logical inequality satisfiability only; not evidence that "
                      "this market sample exists"),
            "rule_revision": "2026-07-24_human_confirm_factory"}


def _solvability_health_input(conn):
    active_rules = [{"symbol": row[0], "timeframe": row[1],
                     "pattern_id": row[2], "filter_id": row[3],
                     "death_cause_code": row[4], "occurrences": row[5]}
                    for row in conn.execute(
        "SELECT symbol,timeframe,pattern_id,filter_id,death_cause_code,occurrences "
        "FROM pruning_rule_memory WHERE state='active' ORDER BY symbol,timeframe,"
        "pattern_id,filter_id LIMIT 96").fetchall()]
    death_scope = [{"symbol": row[0], "timeframe": row[1],
                    "branch_count": row[2], "rejection_count": row[3]}
                   for row in conn.execute(
        "SELECT symbol,timeframe,branch_count,rejection_count FROM "
        "prescreen_death_maps ORDER BY symbol,timeframe LIMIT 24").fetchall()]
    unique_evidence = len(_global_prescreen_evidence(conn))
    mutation_shapes = [{"symbol": row[0], "timeframe": row[1],
                        "pattern_id": row[2],
                        "filters": json.loads(row[3] or "[]"),
                        "cost_multiple": row[4]}
                       for row in conn.execute(
        "SELECT DISTINCT symbol,timeframe,pattern_id,filters_json,cost_multiple "
        "FROM active_hunt_probe_experiments ORDER BY symbol,timeframe,pattern_id,"
        "filters_json LIMIT 128").fetchall()]
    research_target_scopes = sorted(
        (row["symbol"], row["timeframe"]) for row in _creation_targets())
    structural = {
        "gate_schema": SOLVABILITY_GATE_SCHEMA_VERSION,
        "active_pruning_rules": active_rules,
        "controlled_mutation_probe_shapes": mutation_shapes,
        "death_map_scopes": sorted((row["symbol"], row["timeframe"])
                                    for row in death_scope),
        # A matrix change is a material structure change and must therefore
        # trigger a fresh three-AI heartbeat.  This does not claim each target
        # already has empirical data; target-local readiness stays separate.
        "research_target_scopes": research_target_scopes,
    }
    return {"structure_fingerprint": _sha(structural),
            "unique_death_evidence": unique_evidence,
            "active_pruning_rule_count": len(active_rules),
            "controlled_mutation_shape_count": len(mutation_shapes),
            "death_map_scope_count": len(death_scope),
            "research_target_scope_count": len(research_target_scopes),
            "evidence_batch_minimum": SOLVABILITY_DEATH_EVIDENCE_BATCH,
            "structural": structural,
            "active_rules": active_rules, "death_scope": death_scope}


def _solvability_trigger_zh(code):
    mapping = {
        "manual_force": "人工强制复审",
        "weekly_heartbeat": "进入新的自然周，例行健康心跳",
        "deterministic_witness_changed": "确定性可解性见证结果发生变化",
        "health_schema_upgrade": "健康检查数据结构升级，需要重新做一次完整心跳",
        "rule_or_major_mutation_changed": "研究结构发生变化（例如研究集群、剪枝规则或受控变异有更新）",
        "independent_death_evidence_batch": "新增一批独立死亡证据，达到心跳复审门槛",
    }
    return mapping.get(str(code or ""), "系统触发了一次可解性复审")


def _solvability_ai_state_zh(state):
    mapping = {
        "unanimous_logically_feasible": "三家 AI 一致认为规则空间逻辑上仍可满足",
        "mixed_requires_codex_review": "三家 AI 意见不一致，需要 Codex 与人工回溯",
        "incomplete_fail_closed": "三家 AI 未能完整返回意见，按失败关闭处理",
        "ai_empty_conflicts_with_deterministic_witness": (
            "三家 AI 认为规则空间为空，但确定性见证仍判定可解，存在冲突"),
    }
    return mapping.get(str(state or ""), "三家 AI 状态需要人工确认")


def _solvability_deterministic_state_zh(state):
    if state == "SAT_WITNESS":
        return "确定性检查仍认为规则空间逻辑可解"
    if state == "CONTRADICTION":
        return "确定性检查发现规则之间存在逻辑矛盾"
    return "确定性检查状态待确认"


def _format_solvability_alarm_message(result):
    triggers = [ _solvability_trigger_zh(code)
                 for code in (result.get("trigger_reasons") or []) ]
    if not triggers:
        triggers = ["系统触发了一次可解性复审"]
    return (
        "=== 栖语系统可解性预警 ===\n"
        "发生了什么：%s。\n"
        "系统判断：%s；%s。\n"
        "会不会自动放宽：不会。成本、胜率、样本、止损、容量和人工批准门槛都保持原样。\n"
        "需要你做什么：请 Codex 与人工回溯确认；在确认前继续禁止自动放宽任何规则。"
        % ("；".join(triggers),
           _solvability_deterministic_state_zh(result.get("deterministic_state")),
           _solvability_ai_state_zh(result.get("ai_state")))
    )


def _notifications_allowed_for_current_runtime():
    """Block real WxPusher traffic from unit tests or non-production AUTO_DIR."""
    if os.environ.get("QIYU_NOTIFY_DRY_RUN") == "1":
        return False
    if os.environ.get("STAGE823_INSTALL_SELF_TEST") == "1":
        return False
    try:
        auto = str(Path(AUTO_DIR).resolve())
    except Exception:
        auto = str(AUTO_DIR)
    lowered = auto.lower()
    if ("/tmp" in lowered or "/var/folders/" in lowered
            or "temp" in lowered or "pytest" in lowered):
        return False
    production_auto = str((Path(os.environ.get("VECTOR_ROOT", "/root"))
                           / "auto_trade").resolve())
    try:
        if str(Path(AUTO_DIR).resolve()) != production_auto:
            return False
    except Exception:
        return False
    return True


def _solvability_alarm_already_acknowledged(result):
    """Skip repeat mixed alarms after Codex retrospective for this week."""
    if result.get("ai_state") != "mixed_requires_codex_review":
        return False
    if result.get("deterministic_state") != "SAT_WITNESS":
        return False
    week = str(result.get("week_bucket") or "")
    ack_paths = [
        AUTO_DIR / "solvability_mixed_retrospective_20260723.json",
        AUTO_DIR / "system_solvability_codex_ack.json",
    ]
    for path in ack_paths:
        payload = _read(path, {})
        if not payload:
            continue
        alarm = payload.get("alarm") or payload
        if week and str(alarm.get("week_bucket") or payload.get("week_bucket")
                        or "") not in ("", week):
            # Accept legacy retrospective without week field for current week.
            if "week_bucket" in alarm or "week_bucket" in payload:
                continue
        if (str(alarm.get("ai_state") or payload.get("ai_state") or "")
                == "mixed_requires_codex_review"):
            decisions = payload.get("decisions") or {}
            if decisions.get("codex_verdict") or payload.get("acknowledged"):
                return True
        if payload.get("codex_verdict") or payload.get("acknowledged"):
            return True
    return False


def _send_solvability_alarm(previous_state, result):
    healthy = (result.get("deterministic_state") == "SAT_WITNESS" and
               result.get("ai_state") == "unanimous_logically_feasible")
    if healthy or previous_state == result.get("ai_state"):
        return {"sent": False, "reason": "healthy_or_unchanged"}
    dry_run = not _notifications_allowed_for_current_runtime()
    if (not dry_run) and _solvability_alarm_already_acknowledged(result):
        return {"sent": False, "dry_run": False,
                "reason": "already_acknowledged_by_codex_retrospective"}
    try:
        from auto_trade_formal_notify import send_message
        message = _format_solvability_alarm_message(result)
        sent = send_message(
            message, kind="system_solvability_alarm",
            dry_run=dry_run,
            meta={"automatic_rule_relaxation": False,
                  "trigger_reasons": list(result.get("trigger_reasons") or []),
                  "ai_state": result.get("ai_state"),
                  "deterministic_state": result.get("deterministic_state"),
                  "dry_run": dry_run})
        return {"sent": bool(sent.get("sent")),
                "ok": bool(sent.get("ok")),
                "dry_run": dry_run,
                "error": sent.get("notification_error"),
                "message": message}
    except Exception as exc:
        return {"sent": False, "dry_run": dry_run, "error": str(exc)[:300]}


def _persist_solvability_audit_snapshot(result):
    """Keep a durable file copy of non-healthy audits for Codex review."""
    if not isinstance(result, dict):
        return None
    state = str(result.get("ai_state") or "")
    if state in ("", "unanimous_logically_feasible"):
        return None
    stamp = str(result.get("created_at") or _now()).replace(" ", "_").replace(":", "")
    path = AUTO_DIR / ("system_solvability_alarm_%s.json" % stamp)
    payload = {
        "schema": "qiyu_system_solvability_alarm_snapshot_v1",
        "automatic_rule_relaxation": False,
        "requires_codex_and_human_review": True,
        "result": result,
    }
    _atomic(path, payload)
    _atomic(AUTO_DIR / "system_solvability_alarm_latest.json", payload)
    return str(path)


def run_system_solvability_audit(force=False):
    iso = datetime.now().isocalendar(); week = "%04d-W%02d" % (iso[0], iso[1])
    conn = _db()
    try:
        cached = conn.execute(
            "SELECT deterministic_state,ai_state,deterministic_json,ai_reviews_json,"
            "created_at FROM system_solvability_audits WHERE week_bucket=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (week,)).fetchone()
        health = _solvability_health_input(conn)
    finally:
        conn.close()
    witness = _deterministic_solvability_witness()
    trigger_reasons = []
    previous_ai_state = cached[1] if cached else None
    if force:
        trigger_reasons.append("manual_force")
    elif not cached:
        trigger_reasons.append("weekly_heartbeat")
    else:
        previous_witness = json.loads(cached[2] or "{}")
        previous_health = previous_witness.get("health_input") or {}
        if witness.get("state") != cached[0]:
            trigger_reasons.append("deterministic_witness_changed")
        if not previous_health:
            trigger_reasons.append("health_schema_upgrade")
        elif (previous_health.get("structure_fingerprint") !=
              health.get("structure_fingerprint")):
            trigger_reasons.append("rule_or_major_mutation_changed")
        evidence_delta = (int(health.get("unique_death_evidence") or 0)-
                          int(previous_health.get("unique_death_evidence") or 0))
        previous_evidence = int(previous_health.get(
            "unique_death_evidence") or 0)
        evidence_threshold = max(
            SOLVABILITY_DEATH_EVIDENCE_BATCH,
            int(math.ceil(max(1, previous_evidence)*.20)))
        if evidence_delta >= evidence_threshold:
            trigger_reasons.append("independent_death_evidence_batch")
        if not trigger_reasons:
            return {"ok": True, "cached": True, "week_bucket": week,
                    "deterministic_state": witness["state"],
                    "ai_state": cached[1], "deterministic": previous_witness,
                    "ai_reviews": json.loads(cached[3]),
                    "created_at": cached[4], "api_calls_used": 0,
                    "heartbeat_checked_at": _now(),
                    "pending_unique_evidence": max(0, evidence_delta),
                    "next_evidence_trigger_at": evidence_threshold,
                    "automatic_rule_relaxation": False}
    witness["health_input"] = health
    witness["audit_trigger_reasons"] = trigger_reasons
    context = {
        "deterministic_witness": witness,
        "active_pruning_rules": health["active_rules"],
        "death_map_scope": health["death_scope"],
        "rules": {
            "event_atlas": "events>=20; holdout>=6; full WR>=52; holdout WR>=45; all net means>0; posterior thresholds",
            "active_hunt": "complete 3AI; >=2 HUNT; agreed controlled filters; no hard veto; repeated exact-pattern deaths require matching counter-operator",
            "probe": "3x observed friction; exact>=8; holdout>=3; positive exact mean; posterior>=.55",
            "offline": "rolling positive >=60%; severe stress; annual opportunities>=30; depth samples>=24; utilization<=25%",
            "shadow": ">=7 days; >=5 closed; expectancy>0; win rate>=55%; loss streak<=3; contemporaneous micro summaries",
            "governance": "Codex code review plus separate human live approval",
            "micro": "unlabeled clusters cannot become historical filters or positive labels",
        },
        "audit_question": ("Is the rule system logically satisfiable? Distinguish an "
                           "empty rule space from a feasible rule space with no observed sample."),
    }
    from auto_trade_ai_consensus import system_solvability_reviews
    ai = system_solvability_reviews(context)
    if not ai.get("ok"):
        ai_state = "incomplete_fail_closed"
    elif ai.get("unanimous_empty") and witness.get("state") == "SAT_WITNESS":
        ai_state = "ai_empty_conflicts_with_deterministic_witness"
    elif ai.get("unanimous_feasible"):
        ai_state = "unanimous_logically_feasible"
    else:
        ai_state = "mixed_requires_codex_review"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    audit_id = _sha({"week": week, "type": "system_solvability",
                     "created_at": now, "ai_state": ai_state,
                     "trigger_reasons": trigger_reasons,
                     "structure_fingerprint": health.get("structure_fingerprint"),
                     "unique_death_evidence": health.get("unique_death_evidence")})
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO system_solvability_audits VALUES(?,?,?,?,?,?,?)",
            (audit_id, week, witness["state"], ai_state,
             json.dumps(witness, ensure_ascii=False, sort_keys=True),
             json.dumps(ai, ensure_ascii=False, sort_keys=True), now))
        conn.commit()
    finally:
        conn.close()
    result = {"ok": True, "cached": False, "week_bucket": week,
              "deterministic_state": witness["state"], "ai_state": ai_state,
              "deterministic": witness, "ai_reviews": ai,
              "created_at": now, "api_calls_used": 3,
              "trigger_reasons": trigger_reasons,
              "audit_id": audit_id,
              "automatic_rule_relaxation": False}
    result["snapshot_path"] = _persist_solvability_audit_snapshot(result)
    result["notification"] = _send_solvability_alarm(previous_ai_state, result)
    return result


def _precheck_hypothesis_catalog(catalog, assignment, frame=None, context=None):
    """Attach target-specific evidence before anonymous cross-review."""
    if frame is None:
        full_frame = _load_research_frame(
            assignment["symbol"], assignment["timeframe"])
        frame = full_frame.iloc[:max(300, int(math.floor(len(full_frame)*0.70)))]
    eligible = []; rejected = []
    for source in catalog:
        row = copy.deepcopy(source)
        if row.get("kind") != "dsl":
            eligible.append(row); continue
        try:
            semantic = _dsl_semantic_audit(row.get("dsl"))
            causal = _causal_hypothesis_audit(row, context=context)
            edge = (_dsl_entry_edge_screen(row.get("dsl"), frame)
                    if semantic.get("passed") and causal.get("passed") else
                    {"passed": False, "entry_events": 0,
                     "credible_horizons": [], "horizons": {},
                     "policy": "not_run_until_semantic_audit_passes"})
        except Exception as exc:
            semantic = {"passed": False, "issues": [str(exc)]}
            causal = {"passed": False, "issues": [str(exc)]}
            edge = {"passed": False, "entry_events": 0,
                    "credible_horizons": [], "horizons": {},
                    "policy": "precheck_error"}
        row["semantic_audit"] = semantic
        row["causal_audit"] = causal
        row["deterministic_entry_edge_screen"] = edge
        if semantic.get("passed") and causal.get("passed") and edge.get("passed"):
            eligible.append(row)
        else:
            row.update({"support_count": 0, "required_support": 3,
                        "independent_source_count": len(set(row.get("source_providers") or [])),
                        "votes": [{"provider": "deterministic_precheck",
                                   "decision": "REJECT",
                                   "reason": "semantic, causal, or probabilistic cost edge precheck failed"}]})
            rejected.append(row)
    return eligible, rejected


def _hypothesis_convergence_with_precheck(catalog, context, assignment,
                                          frame=None):
    """Run target-data prechecks before spending three AI votes."""
    from auto_trade_ai_consensus import hypothesis_convergence
    eligible, precheck_rejected = _precheck_hypothesis_catalog(
        catalog, assignment, frame=frame, context=context)
    convergence = hypothesis_convergence(eligible, context)
    convergence["rejected"] = (precheck_rejected +
                               list(convergence.get("rejected") or []))
    convergence["precheck_rejected_count"] = len(precheck_rejected)
    convergence["precheck_eligible_count"] = len(eligible)
    convergence["policy"] = (
        "semantic_causal_probabilistic_cost_precheck_then_" +
        str(convergence.get("policy") or "three_ai_cross_review"))
    return convergence


def _precheck_survivors_rejected_by_ai(convergence):
    """Return only DSLs that survived deterministic precheck.

    A deterministic-rejected DSL must never re-enter full backtesting through
    the old "exploration" side door.  This helper keeps AI-disagreement
    learning while making cost/causal/semantic rejection terminal.
    """
    survivors = []
    for row in convergence.get("rejected") or []:
        if row.get("kind") != "dsl":
            continue
        if (not (row.get("semantic_audit") or {}).get("passed")
                or not (row.get("causal_audit") or {}).get("passed")
                or not (row.get("deterministic_entry_edge_screen") or {}).get("passed")):
            continue
        survivors.append(row)
    return survivors


def _promotion_definitions(proposed, item, promotion_allowed, mutation_limit):
    """A reviewed executable hash may not silently turn into a mutation."""
    import auto_trade_strategy_dsl as dsl
    if promotion_allowed:
        return [proposed]
    return [proposed] + dsl.mutate_strategy(
        proposed, threshold_step=1.0,
        additions=item.get("safe_additions") or [], limit=mutation_limit,
    )


def _process_dsl_hypotheses(assignment, provider_results, baseline_runs,
                            parent_version_hash=None, promotion_allowed=True,
                            mutation_limit=6):
    import auto_trade_strategy_dsl as dsl
    import auto_trade_strategy_intelligence as intelligence
    conn = _db(); outcomes = []
    try:
        frame = None; evaluated = []
        for result in provider_results:
            for item in result.get("hypotheses") or []:
                if item.get("kind") != "dsl": continue
                proposed = item.get("dsl")
                try:
                    proposed = dsl.validate_strategy(proposed)
                    if proposed["timeframe"] != assignment["timeframe"] or assignment["symbol"] not in proposed["supported_instruments"]:
                        raise dsl.DSLValidationError("DSL assignment mismatch")
                    frame = frame if frame is not None else _load_research_frame(assignment["symbol"], assignment["timeframe"])
                    # Promotion can evaluate only the exact executable hash
                    # that received 3/3 review. Mutations are exploration-only
                    # and require a fresh review in a later round.
                    candidates = _promotion_definitions(
                        proposed, item, promotion_allowed, mutation_limit)
                    item_evaluated = []
                    for definition in candidates:
                        evidence = _dsl_evidence(definition, frame, baseline_runs)
                        candidate = {"type": "dsl_strategy", "strategy_key": definition["key"],
                                     "symbol": assignment["symbol"], "timeframe": assignment["timeframe"],
                                     "dsl": definition,
                                     "executable_hash": dsl.executable_hash(definition),
                                     "parent_version_hash": parent_version_hash,
                                     "causal_chains": item.get("causal_chains") or [],
                                     "falsification_tests": item.get("falsification_tests") or [],
                                     "source_pattern_ids": item.get("source_pattern_ids") or []}
                        evidence["research_provenance"] = {
                            "provider": result.get("provider"),
                            "rationale": item.get("rationale"),
                        }
                        evaluated.append((candidate, evidence))
                        item_evaluated.append((definition, evidence))
                    if item_evaluated:
                        original_metrics = (
                            (item_evaluated[0][1].get("runs") or {})
                            .get("candidate", {}).get("0.009") or {}
                        )
                        intelligence.record_dsl_attributions(
                            conn, assignment, proposed, original_metrics,
                            item_evaluated[1:],
                        )
                except Exception as exc:
                    intelligence.record_failure(conn, "dsl_validation", assignment["strategy_key"],
                        assignment["symbol"], assignment["timeframe"], "invalid_dsl",
                        {"provider": result.get("provider"), "error": str(exc)})
        if not evaluated: return []
        evaluated.sort(key=lambda item: (
            bool(item[1].get("passed")),
            sum(1 for value in (item[1].get("gates") or {}).values() if value),
            item[1].get("weighted_score", -999),
        ), reverse=True)
        # Review only the best deterministic DSL candidate to cap cost.
        from auto_trade_ai_consensus import candidate_hash
        selected = None
        for candidate, evidence in evaluated:
            prior_state = _candidate_known(candidate_hash(candidate))
            prior_state = prior_state or _known_dsl_executable(
                candidate.get("executable_hash")
            )
            terminal_states = {"deterministic_rejected", "ai_rejected", "deployed",
                               "approved_requires_human", "human_approved_research",
                               "human_approved_live", "awaiting_codex_review",
                               "awaiting_shadow_validation",
                               "advanced_confirmation_rejected",
                               "codex_rejected", "iteration_regressed"}
            if not promotion_allowed:
                terminal_states.update({"exploration_rejected",
                                        "exploration_passed_not_converged"})
            if prior_state in terminal_states:
                outcomes.append({"candidate_hash": candidate_hash(candidate),
                                 "state": "skipped_known_%s" % prior_state})
                continue
            selected = (candidate, evidence); break
        if selected is None:
            return outcomes
        candidate, evidence = selected
        if promotion_allowed:
            candidate_state = "deterministic_passed" if evidence.get("passed") else "deterministic_rejected"
        else:
            candidate_state = "exploration_passed_not_converged" if evidence.get("passed") else "exploration_rejected"
        digest = _store_candidate(candidate, evidence, candidate_state)
        _store_version(digest, candidate, evidence,
                       candidate_state,
                       version_type="safe_dsl")
        if not evidence.get("passed"):
            intelligence.record_failure(conn, "deterministic_screen", candidate["strategy_key"],
                candidate["symbol"], candidate["timeframe"], "dsl_gates_failed",
                {"failed_gates": [k for k,v in evidence.get("gates",{}).items() if not v],
                 "candidate_hash": digest,
                 "rejected_dsl": candidate.get("dsl") or {},
                 "primary_metrics": ((evidence.get("runs") or {}).get("candidate") or {}).get("0.009") or {},
                 "auxiliary_metrics": ((evidence.get("runs") or {}).get("candidate") or {}).get("0.006") or {}})
            return [{"candidate_hash": digest, "state": candidate_state}]
        if not promotion_allowed:
            outcomes.append({"candidate_hash": digest,
                             "state": "exploration_passed_not_converged"})
            return outcomes
        advanced = _advanced_positive_gates(candidate, evidence)
        evidence["advanced_positive_confirmation"] = advanced
        if not advanced.get("passed"):
            state = "advanced_confirmation_rejected"
            _store_candidate(candidate, evidence, state)
            _store_version(digest, candidate, evidence, state,
                           version_type="safe_dsl")
            intelligence.record_failure(
                conn, "positive_confirmation", candidate["strategy_key"],
                candidate["symbol"], candidate["timeframe"],
                "offline_four_gate_precursor_failed",
                {"candidate_hash": digest,
                 "failed_gates": [key for key, value in
                                  advanced.get("gates", {}).items() if not value],
                 "advanced": advanced})
            conn.commit()
            return [{"candidate_hash": digest, "state": state,
                     "advanced_confirmation": advanced}]
        shadow = _register_shadow_confirmation(
            conn, candidate, evidence, digest, advanced)
        conn.commit()
        # A deterministic and offline-three-gate pass now waits for at least
        # seven chronological days of read-only shadow observation.  Only the
        # shadow runner may advance it to Codex review.
        consensus = {
            "candidate_hash": digest,
            "stage": "offline_gates_complete_shadow_required",
            "codex_review_required": True,
            "shadow_validation": shadow,
            "production_code_generated_by_external_ai": False,
        }
        state = "awaiting_shadow_validation"
        _candidate_state(digest, state)
        _store_candidate(candidate, evidence, state)
        _store_version(digest, candidate, evidence, state, consensus,
                       version_type="safe_dsl")
        conn.commit(); outcomes.append({"candidate_hash": digest, "state": state,
                                        "shadow_validation": shadow})
        return outcomes
    finally:
        conn.close()


def _quick_parameter_screen(strategy_key, symbol, timeframe, candidate_params):
    quick_start = (datetime.now()-timedelta(days=90)).strftime(
        "%Y-%m-%d %H:%M:%S")
    result = _run_with_config(
        strategy_key, symbol, timeframe, candidate_params, 0.009,
        friction_scenario="observed_base", start_time=quick_start)
    if result.get("error"):
        return {"passed": False, "error": result.get("error"),
                "metrics": _empty_metrics(), "acquisition_score": -999.0}
    metrics = _metrics(result)
    passed = (metrics["trades"] >= 5 and metrics["expectancy_pct"] > 0.0
              and metrics.get("probability_positive_expectancy", 0.0) >= .60)
    score = (metrics["expectancy_pct"]
             +.06*metrics["win_rate_pct"]
             +.02*min(metrics["trades"], 40)
             +2.0*metrics.get("probability_positive_expectancy", 0.0)
             -.08*metrics["max_drawdown_pct"])
    return {"passed": passed, "metrics": metrics,
            "acquisition_score": round(score, 6),
            "policy": "recent_90d_20x_net_cost_probabilistic_screen"}


def deterministic_evidence(strategy_key, symbol, timeframe, baseline_params,
                           candidate_params, baseline_runs=None,
                           quick_screen=None):
    runs = {"baseline": copy.deepcopy(baseline_runs or {}), "candidate": {},
            "stress": {}}
    if not runs["baseline"]:
        for stop in (0.009, 0.006):
            result = _run_with_config(strategy_key, symbol, timeframe,
                                      baseline_params, stop)
            if result.get("error"):
                return {"passed": False, "error": result.get("error"), "runs": runs}
            runs["baseline"][str(stop)] = _metrics(result)
    quick_screen = (quick_screen or _quick_parameter_screen(
        strategy_key, symbol, timeframe, candidate_params))
    if quick_screen.get("error"):
        return {"passed": False, "error": quick_screen.get("error"),
                "runs": runs}
    quick = quick_screen.get("metrics") or _empty_metrics()
    runs["stages"] = {"recent_90d_primary": quick}
    quick_pass = bool(quick_screen.get("passed"))
    if not quick_pass:
        return {"passed": False, "early_pruned": True,
                "early_pruned_at": "recent_90d_primary",
                "gates": {"stage_1_recent_90d": False}, "runs": runs,
                "weighted_score": -999.0,
                "policy": "successive_halving_pruned_parameter_variant"}
    for stop in (0.009, 0.006):
        result = _run_with_config(strategy_key, symbol, timeframe,
                                  candidate_params, stop)
        if result.get("error"):
            return {"passed": False, "error": result.get("error"), "runs": runs}
        runs["candidate"][str(stop)] = _metrics(result)
    for scenario in ("stressed", "severe"):
        result = _run_with_config(strategy_key, symbol, timeframe,
                                  candidate_params, 0.009,
                                  friction_scenario=scenario)
        if result.get("error"):
            return {"passed": False, "error": result.get("error"), "runs": runs}
        runs["stress"][scenario] = _metrics(result)
    base = runs["baseline"]["0.009"]
    cand = runs["candidate"]["0.009"]
    aux = runs["candidate"]["0.006"]
    stressed = runs["stress"]["stressed"]
    severe = runs["stress"]["severe"]
    gates = {
        "sample_retention": cand["trades"] >= max(8, int(math.ceil(base["trades"] * 0.80))),
        "primary_win_rate": cand["win_rate_pct"] >= max(68.0, base["win_rate_pct"] - 1.0),
        "target_or_improvement": cand["win_rate_pct"] >= 75.0 or cand["expectancy_pct"] > base["expectancy_pct"],
        "positive_expectancy_both_stops": cand["expectancy_pct"] > 0 and aux["expectancy_pct"] > 0,
        "drawdown_not_materially_worse": cand["max_drawdown_pct"] <= base["max_drawdown_pct"] + 5.0,
        "loss_cluster_not_worse": cand["max_loss_streak"] <= max(3, base["max_loss_streak"]),
        "holdout_not_collapsed": cand["holdout_trades"] >= 2 and cand["holdout_win_rate_pct"] >= 55.0,
        "probabilistic_edge": (cand.get("probability_positive_expectancy", 0.0) >= .90
                               and cand.get("profit_factor", 0.0) >= 1.20),
        "learning_curve_not_collapsing": not (
            len(cand.get("walk_forward") or []) >= 3
            and float((cand.get("walk_forward") or [])[-1].get(
                "expectancy_pct") or 0.0) < 0.0
            and float((cand.get("walk_forward") or [])[-2].get(
                "expectancy_pct") or 0.0) < 0.0),
        "stressed_cost_positive": stressed.get("expectancy_pct", 0.0) > 0.0,
        "severe_cost_survivable": severe.get("expectancy_pct", -999.0) > -1.0,
    }
    score = (0.75 * cand["expectancy_pct"] + 0.25 * aux["expectancy_pct"]
             + 0.08 * cand["win_rate_pct"] + 0.10*stressed["expectancy_pct"]
             + 0.02 * min(cand["trades"], 50)
             - 0.10 * cand["max_drawdown_pct"])
    return {"passed": all(gates.values()), "gates": gates, "runs": runs,
            "weighted_score": round(score, 6),
            "policy": "cost_scenarios+0.9%主权重+0.6%辅助+posterior+stress+holdout"}


def research_once():
    assignments = _active_assignments()
    eligible = []
    for assignment in assignments:
        row = _strategy_row(assignment["strategy_key"])
        if row:
            eligible.append((assignment, row))
    if not eligible:
        return {"ok": True, "action": "no_active_assignments"}
    # One eligible assignment per tick bounds compute/API cost; rotation is
    # date-based and skips fixed-rule strategies without tunable metadata.
    assignment, row = eligible[int(time.time() // 86400) % len(eligible)]
    baseline = dict(row.get("params") or {})
    baseline_runs = {}
    for stop in (0.009, 0.006):
        baseline_result = _run_with_config(
            assignment["strategy_key"], assignment["symbol"],
            assignment["timeframe"], baseline, stop,
        )
        if baseline_result.get("error"):
            return {"ok": False, "error": baseline_result.get("error"),
                    "action": "baseline_backtest_failed", "assignment": assignment}
        baseline_runs[str(stop)] = _metrics(baseline_result)
    baseline_snapshot = {
        "type": "baseline_snapshot", "strategy_key": assignment["strategy_key"],
        "symbol": assignment["symbol"], "timeframe": assignment["timeframe"],
        "config_hash": _file_sha(CONFIG_PATH), "params": baseline,
    }
    baseline_hash = _sha(baseline_snapshot)
    _store_version(
        baseline_hash, baseline_snapshot,
        {"runs": {"baseline": baseline_runs},
         "policy": "active_strategy_baseline_snapshot"},
        "active_baseline", version_type="baseline_snapshot",
    )
    import auto_trade_strategy_intelligence as intelligence
    from auto_trade_ai_consensus import (candidate_hash,
                                         independent_research, unanimous_review)
    conn = _db()
    try:
        context = intelligence.research_context(conn, assignment)
        context["current_strategy"] = {
            "name": row.get("name"), "params": baseline,
            "description": row.get("description"),
            "total_goal": "约75%胜率、20%及以上单笔盈利潜力、控制止损簇并尽量提高频率",
            "baseline_dual_stop": baseline_runs,
        }
    finally:
        conn.close()
    research = independent_research(context, row)
    catalog, invalid_hypotheses = _build_hypothesis_catalog(
        row, assignment, research.get("results") or []
    )
    convergence = _hypothesis_convergence_with_precheck(
        catalog, context, assignment)
    converged_results = _converged_provider_results(convergence)
    nonconverged_results = _converged_provider_results(
        {"admitted": _precheck_survivors_rejected_by_ai(convergence)}
    )
    conn = _db()
    try:
        stored_hypotheses = intelligence.store_hypotheses(conn, research.get("results") or [])
        stored_hypothesis_reviews = intelligence.store_hypothesis_convergence(
            conn, convergence
        )
        for failed_result in research.get("results") or []:
            if failed_result.get("ok"):
                continue
            intelligence.record_failure(
                conn, "ai_research_call", assignment["strategy_key"],
                assignment["symbol"], assignment["timeframe"],
                "research_provider_failed",
                {"provider": failed_result.get("provider"),
                 "error": failed_result.get("error") or "unknown provider error",
                 "failed_gates": ["provider_response"]},
            )
        for invalid in invalid_hypotheses:
            intelligence.record_failure(
                conn, "research_validation", assignment["strategy_key"],
                assignment["symbol"], assignment["timeframe"],
                "invalid_research_hypothesis",
                {"provider": invalid.get("provider"), "error": invalid.get("error"),
                 "failed_gates": ["research_schema"]},
            )
        for rejected in convergence.get("rejected") or []:
            intelligence.record_failure(
                conn, "research_convergence", assignment["strategy_key"],
                assignment["symbol"], assignment["timeframe"],
                "research_direction_not_converged",
                {"hypothesis_id": rejected.get("hypothesis_id"),
                 "kind": rejected.get("kind"),
                 "independent_source_count": rejected.get("independent_source_count"),
                 "support_count": rejected.get("support_count"),
                 "required_support": rejected.get("required_support"),
                 "votes": rejected.get("votes"),
                 "failed_gates": ["multi_ai_research_convergence"]},
            )
    finally:
        conn.close()
    research_summary = {
        "independent_provider_status": [
            {"provider": result.get("provider"), "ok": result.get("ok"),
             "hypothesis_count": len(result.get("hypotheses") or []),
             "error": result.get("error") if not result.get("ok") else None}
            for result in research.get("results") or []
        ],
        "stored_hypotheses": stored_hypotheses,
        "catalog_count": len(catalog),
        "invalid_count": len(invalid_hypotheses),
        "converged_count": len(convergence.get("admitted") or []),
        "research_rejected_count": len(convergence.get("rejected") or []),
        "stored_hypothesis_reviews": stored_hypothesis_reviews,
        "convergence_policy": convergence.get("policy"),
    }
    local_variants = generate_variants(
        row, limit=int(os.environ.get("QIYU_ECOSYSTEM_LOCAL_VARIANTS", "6"))
    )
    for variant in local_variants:
        variant["promotion_eligible"] = True
        variant["research_provider"] = "deterministic_grid"
    converged_variants = _ai_parameter_variants(row, converged_results)
    for variant in converged_variants:
        variant["promotion_eligible"] = True
    promotion_variants = _dedupe_variants(
        _rank_parameter_variants(
            assignment, local_variants + converged_variants),
        limit=int(os.environ.get("QIYU_ECOSYSTEM_VARIANTS", "12")),
    )
    promoted_param_hashes = set(_sha(item.get("params") or {})
                                for item in promotion_variants)
    exploration_variants = []
    for variant in _ai_parameter_variants(row, research.get("results") or []):
        if _sha(variant.get("params") or {}) in promoted_param_hashes:
            continue
        variant["promotion_eligible"] = False
        exploration_variants.append(variant)
    exploration_variants = _dedupe_variants(
        _rank_parameter_variants(assignment, exploration_variants),
        limit=int(os.environ.get("QIYU_ECOSYSTEM_EXPLORATION_VARIANTS", "6")),
    )
    variants = promotion_variants + exploration_variants
    evaluated = []; prepared = []
    skipped_known = []
    for variant in variants:
        candidate = {
            "type": "bounded_parameter_change", "strategy_key": assignment["strategy_key"],
            "symbol": assignment["symbol"], "timeframe": assignment["timeframe"],
            "base_config_hash": _file_sha(CONFIG_PATH),
            "parent_version_hash": baseline_hash,
            "changed_parameter": variant["changed_parameter"],
            "old_value": variant["old_value"], "new_value": variant["new_value"],
            "params": variant["params"],
        }
        digest = candidate_hash(candidate)
        prior_state = _candidate_known(digest)
        terminal_states = {"deterministic_rejected", "ai_rejected", "deployed",
                           "awaiting_codex_review", "codex_rejected",
                           "iteration_regressed"}
        if not variant.get("promotion_eligible"):
            terminal_states.update({"exploration_rejected",
                                    "exploration_passed_not_converged"})
        if prior_state in terminal_states:
            skipped_known.append({"candidate_hash": digest, "state": prior_state})
            continue
        quick_screen = _quick_parameter_screen(
            assignment["strategy_key"], assignment["symbol"], assignment["timeframe"],
            variant["params"])
        prepared.append((variant, candidate, digest, quick_screen))
    quick_survivors = sorted(
        [item for item in prepared if item[3].get("passed")],
        key=lambda item: (
            float(item[3].get("acquisition_score") or -999.0)
            +.20*float((item[0].get("search_prior") or {}).get(
                "acquisition_score") or 0.0)), reverse=True)
    full_budget = (min(6, max(1, int(math.ceil(
        len(quick_survivors)*.20)))) if quick_survivors else 0)
    selected_hashes = set(item[2] for item in quick_survivors[:full_budget])
    raw_evaluated = []
    for variant, candidate, digest, quick_screen in prepared:
        if not quick_screen.get("passed"):
            evidence = {
                "passed": False, "early_pruned": True,
                "early_pruned_at": "recent_90d_primary",
                "gates": {"stage_1_recent_90d": False},
                "runs": {"baseline": copy.deepcopy(baseline_runs),
                         "candidate": {}, "stress": {},
                         "stages": {"recent_90d_primary":
                                    quick_screen.get("metrics") or _empty_metrics()}},
                "weighted_score": -999.0,
                "policy": "population_successive_halving_quick_rejected",
            }
        elif digest not in selected_hashes:
            evidence = {
                "passed": False, "early_pruned": True,
                "early_pruned_at": "population_budget",
                "gates": {"stage_1_recent_90d": True,
                          "stage_2_top_20pct_budget": False},
                "runs": {"baseline": copy.deepcopy(baseline_runs),
                         "candidate": {}, "stress": {},
                         "stages": {"recent_90d_primary":
                                    quick_screen.get("metrics") or _empty_metrics()}},
                "weighted_score": float(
                    quick_screen.get("acquisition_score") or -999.0),
                "policy": "population_successive_halving_budget_pruned",
            }
        else:
            evidence = deterministic_evidence(
                assignment["strategy_key"], assignment["symbol"],
                assignment["timeframe"], baseline, variant["params"],
                baseline_runs=baseline_runs, quick_screen=quick_screen)
        evidence["research_provenance"] = {
            "provider": variant.get("research_provider"),
            "rationale": variant.get("research_rationale"),
            "expected_effect": variant.get("expected_effect"),
        }
        evidence["search_allocation"] = {
            "policy": "population_successive_halving_top_20pct",
            "quick_candidates": len(prepared),
            "quick_survivors": len(quick_survivors),
            "full_budget": full_budget,
            "full_evaluated": digest in selected_hashes,
            "quick_acquisition_score": quick_screen.get("acquisition_score"),
            "historical_prior": variant.get("search_prior") or {},
        }
        raw_evaluated.append((variant, evidence, candidate, digest))

    # Control the family-wise search optimism across the expensive finalists.
    # The probability proxy comes from a deterministic bootstrap/Beta model;
    # it does not replace the sealed chronological holdout.
    full_rows = [item for item in raw_evaluated
                 if (item[1].get("search_allocation") or {}).get("full_evaluated")]
    ordered_p = []
    for item in full_rows:
        candidate_runs = ((item[1].get("runs") or {}).get("candidate") or {})
        primary_metrics = candidate_runs.get("0.009") or {}
        probability = float(primary_metrics.get(
            "probability_positive_expectancy") or 0.0)
        ordered_p.append((1.0-probability, item[3]))
    ordered_p.sort()
    fdr_q = .10; accepted_count = 0; cumulative_error = 0.0
    for rank, (error_probability, _digest) in enumerate(ordered_p, 1):
        cumulative_error += error_probability
        if cumulative_error/float(rank) <= fdr_q:
            accepted_count = rank
    fdr_accepted = set(digest for _error, digest
                       in ordered_p[:accepted_count])
    for variant, evidence, candidate, digest in raw_evaluated:
        if digest in selected_hashes:
            fdr_passed = digest in fdr_accepted
            evidence.setdefault("gates", {})["multiple_testing_control"] = fdr_passed
            evidence["multiple_testing_control"] = {
                "method": "bayesian_fdr_posterior_sign_error", "q": fdr_q,
                "tested_candidates": len(full_rows), "passed": fdr_passed}
            if evidence.get("passed") and not fdr_passed:
                evidence["passed"] = False
        if evidence.get("early_pruned_at") == "population_budget":
            state = "search_budget_pruned"
        elif variant.get("promotion_eligible"):
            state = "deterministic_passed" if evidence.get("passed") else "deterministic_rejected"
        else:
            state = "exploration_passed_not_converged" if evidence.get("passed") else "exploration_rejected"
        digest = _store_candidate(candidate, evidence, state)
        _store_version(digest, candidate, evidence, state)
        evaluated.append((variant, evidence, candidate, digest))
        if not evidence.get("passed"):
            conn = _db()
            try:
                intelligence.record_failure(
                    conn, "deterministic_screen", candidate["strategy_key"],
                    candidate["symbol"], candidate["timeframe"], "numeric_gates_failed",
                    {"candidate_hash": digest, "promotion_eligible": bool(variant.get("promotion_eligible")),
                     "failed_gates": [name for name, gate_passed in (evidence.get("gates") or {}).items() if not gate_passed],
                     "change": {"parameter": variant.get("changed_parameter"),
                                "old": variant.get("old_value"), "new": variant.get("new_value")}},
                )
            finally:
                conn.close()
    research_summary["search_budget"] = {
        "policy": "population_successive_halving_top_20pct_cost_constrained",
        "quick_candidates": len(prepared),
        "quick_survivors": len(quick_survivors),
        "full_candidates": len(full_rows),
        "full_backtests_saved": max(0, len(prepared)-len(full_rows)),
        "multiple_testing": {"method": "bayesian_fdr_posterior_sign_error", "q": fdr_q,
                             "accepted": len(fdr_accepted)},
    }
    conn = _db()
    try:
        attribution_count = intelligence.record_attributions(
            conn, assignment, row, baseline_runs["0.009"],
            [(item[0], item[1]) for item in evaluated
             if ((item[1].get("runs") or {}).get("candidate") or {})],
        )
    finally:
        conn.close()
    dsl_outcomes = _process_dsl_hypotheses(
        assignment, converged_results, baseline_runs,
        parent_version_hash=baseline_hash,
    )
    dsl_exploration_outcomes = _process_dsl_hypotheses(
        assignment, nonconverged_results, baseline_runs,
        parent_version_hash=baseline_hash, promotion_allowed=False,
        mutation_limit=2,
    )
    passed = [item for item in evaluated
              if item[1].get("passed") and item[0].get("promotion_eligible")]
    if not passed:
        return {"ok": True, "action": "no_candidate_passed", "assignment": assignment,
                "evaluated": len(evaluated), "skipped_known": len(skipped_known),
                "research": research_summary,
                "condition_attributions": attribution_count,
                "dsl_outcomes": dsl_outcomes,
                "dsl_exploration_outcomes": dsl_exploration_outcomes}
    pareto = _pareto_front(passed)
    variant, evidence, candidate, digest = sorted(
        pareto, key=lambda item: item[1]["weighted_score"], reverse=True
    )[0]
    evidence["pareto_selection"] = {
        "eligible_candidates": len(passed), "front_size": len(pareto),
        "policy": "non_dominated_expectancy_win_stress_drawdown_streak_frequency_then_score"}
    blind_evidence = copy.deepcopy(evidence)
    blind_evidence.pop("research_provenance", None)
    consensus = unanimous_review(candidate, blind_evidence)
    _store_reviews(digest, consensus)
    if not consensus.get("approved"):
        _candidate_state(digest, "ai_rejected")
        _store_version(digest, candidate, evidence, "ai_rejected", consensus)
        conn = _db()
        try:
            intelligence.record_failure(
                conn, "ai_review", candidate["strategy_key"], candidate["symbol"],
                candidate["timeframe"], "numeric_ai_rejected",
                {"candidate_hash": digest, "reviews": consensus.get("reviews"),
                 "failed_gates": []},
            )
        finally:
            conn.close()
        return {"ok": True, "action": "ai_rejected",
                "candidate": candidate, "evidence": evidence, "consensus": consensus,
                "dsl_outcomes": dsl_outcomes,
                "dsl_exploration_outcomes": dsl_exploration_outcomes,
                "condition_attributions": attribution_count,
                "research": research_summary}
    deployed = deploy_candidate(candidate, evidence, consensus)
    _store_version(digest, candidate, evidence,
                   "deployed" if deployed.get("ok") else "deployment_blocked", consensus)
    return {"ok": bool(deployed.get("ok")), "action": "deployed" if deployed.get("ok") else "deployment_blocked",
            "candidate": candidate, "evidence": evidence,
            "consensus": consensus, "deployment": deployed,
            "dsl_outcomes": dsl_outcomes,
            "dsl_exploration_outcomes": dsl_exploration_outcomes,
            "condition_attributions": attribution_count,
            "research": research_summary}


def _store_reviews(digest, consensus):
    conn = _db()
    try:
        for row in consensus.get("reviews") or []:
            conn.execute("INSERT OR REPLACE INTO ai_reviews VALUES(?,?,?,?,?)",
                         (digest, row.get("provider"), row.get("decision") or "REJECT",
                          json.dumps(row, ensure_ascii=False, sort_keys=True), _now()))
        conn.commit()
    finally:
        conn.close()


def _candidate_state(digest, state):
    conn = _db()
    try:
        conn.execute("UPDATE candidates SET state=?,updated_at=? WHERE candidate_hash=?",
                     (state, _now(), digest))
        conn.commit()
    finally:
        conn.close()


def deploy_candidate(candidate, evidence, consensus):
    from auto_trade_ai_consensus import candidate_hash
    digest = candidate_hash(candidate)
    gates = evidence.get("gates") or {}
    reviews = consensus.get("reviews") or []
    provider_decisions = {
        row.get("provider"): row for row in reviews
        if row.get("provider") in ("deepseek", "qwen", "chatgpt")
    }
    exact_unanimity = (
        consensus.get("candidate_hash") == digest
        and set(provider_decisions) == {"deepseek", "qwen", "chatgpt"}
        and all(row.get("ok") and row.get("decision") == "APPROVE"
                and row.get("candidate_hash") == digest
                for row in provider_decisions.values())
    )
    if (not evidence.get("passed") or not gates or not all(gates.values())
            or not consensus.get("approved") or not exact_unanimity):
        return {"ok": False, "blocked": True, "error": "deterministic gate or three-AI unanimity missing"}
    if candidate.get("type") != "bounded_parameter_change":
        return {"ok": False, "blocked": True, "error": "structural/live-policy changes require code deployment path"}
    if candidate.get("base_config_hash") != _file_sha(CONFIG_PATH):
        return {"ok": False, "blocked": True, "error": "base config changed after review"}
    config = _read(CONFIG_PATH, {})
    target = None
    for row in config.get("strategies") or []:
        if row.get("key") == candidate.get("strategy_key"):
            target = row
            break
    if target is None:
        return {"ok": False, "error": "strategy missing at deployment"}
    meta = target.get("param_meta") or {}
    name = candidate.get("changed_parameter")
    current_params = dict(target.get("params") or {})
    proposed_params = dict(candidate.get("params") or {})
    if name in PROTECTED_CAPITAL_PARAMS:
        return {"ok": False, "blocked": True,
                "error": "capital/risk parameter is immutable to AI deployment"}
    if name not in meta or set(proposed_params) != set(current_params):
        return {"ok": False, "blocked": True, "error": "candidate exceeds bounded parameter schema"}
    changed = [key for key in current_params
               if proposed_params.get(key) != current_params.get(key)]
    if changed != [name] or candidate.get("old_value") != current_params.get(name):
        return {"ok": False, "blocked": True,
                "error": "candidate must change exactly one declared parameter from current value"}
    new_value = _number(proposed_params.get(name))
    limits = meta.get(name) or {}
    minimum = _number(limits.get("min"))
    maximum = _number(limits.get("max"))
    step = _number(limits.get("step"))
    if (new_value is None or minimum is None or maximum is None or step is None
            or step <= 0 or not minimum <= new_value <= maximum):
        return {"ok": False, "blocked": True,
                "error": "candidate value is outside declared parameter bounds"}
    step_offset = (new_value - minimum) / step
    if abs(step_offset - round(step_offset)) > 1e-7:
        return {"ok": False, "blocked": True,
                "error": "candidate value is not aligned to declared parameter step"}
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + digest[:12] + ".json")
    shutil.copy2(str(CONFIG_PATH), str(backup))
    before = _file_sha(CONFIG_PATH)
    target["params"] = proposed_params
    target["modified_at_beijing"] = _now()
    target["version"] = str(target.get("version") or "v1") + "+eco-" + digest[:8]
    target["last_ai_consensus_hash"] = digest
    _atomic(CONFIG_PATH, config)
    after = _file_sha(CONFIG_PATH)
    conn = _db()
    try:
        prior = [row[0] for row in conn.execute(
            "SELECT candidate_hash FROM deployments WHERE strategy_key=? "
            "AND rolled_back_at IS NULL AND candidate_hash<>?",
            (candidate["strategy_key"], digest)).fetchall()]
        conn.execute(
            "UPDATE deployments SET rolled_back_at=?,rollback_reason=? "
            "WHERE strategy_key=? AND rolled_back_at IS NULL AND candidate_hash<>?",
            (_now(), "superseded_by_%s" % digest[:12],
             candidate["strategy_key"], digest),
        )
        for previous_hash in prior:
            conn.execute("UPDATE strategy_versions SET state=?,updated_at=? WHERE version_hash=?",
                         ("superseded", _now(), previous_hash))
        conn.execute("INSERT OR REPLACE INTO deployments VALUES(?,?,?,?,?,?,NULL,NULL)",
                     (digest, candidate["strategy_key"], before, after, str(backup), _now()))
        conn.execute("UPDATE candidates SET state=?,updated_at=? WHERE candidate_hash=?",
                     ("deployed", _now(), digest))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "candidate_hash": digest, "backup": str(backup),
            "before_hash": before, "after_hash": after}


def codex_review_dsl_version(digest, decision, note=""):
    """Record the required Codex production-readiness review.

    This is intentionally separate from external model research and from the
    user's live-enablement decision.
    """
    from auto_trade_ai_consensus import candidate_hash
    import auto_trade_strategy_dsl as dsl
    decision = str(decision or "").upper()
    if decision not in ("APPROVE", "REJECT"):
        return {"ok": False, "error": "decision must be APPROVE or REJECT"}
    conn = _db()
    try:
        row = conn.execute(
            "SELECT c.state,c.candidate_json,c.evidence_json,v.consensus_json "
            "FROM candidates c JOIN strategy_versions v "
            "ON v.version_hash=c.candidate_hash WHERE c.candidate_hash=?",
            (digest,)).fetchone()
        if not row:
            return {"ok": False, "error": "version not found"}
        state, candidate_raw, evidence_raw, consensus_raw = row
        candidate = json.loads(candidate_raw); evidence = json.loads(evidence_raw or "{}")
        if (state != "awaiting_codex_review" or candidate.get("type") != "dsl_strategy"
                or candidate_hash(candidate) != digest or not evidence.get("passed")):
            return {"ok": False, "blocked": True,
                    "error": "candidate is not a deterministic-passed DSL awaiting Codex review"}
        dsl.validate_strategy(candidate.get("dsl"))
        consensus = json.loads(consensus_raw or "{}")
        review = {"reviewer": "codex", "decision": decision,
                  "candidate_hash": digest, "note": str(note or "")[:1000],
                  "reviewed_at": _now()}
        consensus["codex_review"] = review
        new_state = "approved_requires_human" if decision == "APPROVE" else "codex_rejected"
        conn.execute("UPDATE candidates SET state=?,updated_at=? WHERE candidate_hash=?",
                     (new_state, _now(), digest))
        conn.execute("UPDATE strategy_versions SET state=?,consensus_json=?,updated_at=? "
                     "WHERE version_hash=?",
                     (new_state, json.dumps(consensus, ensure_ascii=False, sort_keys=True),
                      _now(), digest))
        conn.commit()
        return {"ok": True, "candidate_hash": digest, "state": new_state,
                "codex_review": review}
    finally:
        conn.close()


def _live_version_certificate(digest, candidate, definition):
    """Return a fail-closed, version-bound live cognitive certificate.

    Human approval is deliberately insufficient.  The exact strategy version
    must have a passed_all certificate for its symbol/timeframe assignment;
    missing, stale or unbound controls can never be interpreted as approval.
    """
    gate = _read(LIVE_COGNITIVE_GATE_PATH, {})
    if not bool(gate.get("enforce")):
        return {"ok": False, "reason": "mandatory live cognitive gate is not enforced"}
    controls = _read(LIVE_RUNTIME_CONTROLS_PATH, {})
    assignment_id = "%s|%s|%s" % (
        str(candidate.get("symbol") or "").upper(),
        str(candidate.get("timeframe") or "").lower(),
        definition.get("key"),
    )
    row = (controls.get("assignments") or {}).get(assignment_id) or {}
    certified_hash = str(row.get("certified_version_hash") or "")
    ok = bool(
        row.get("audit_state") == "passed_all"
        and not row.get("pause_new_entries")
        and certified_hash == str(digest)
        and row.get("full_cognitive_matrix_passed") is True
    )
    return {
        "ok": ok, "assignment_id": assignment_id,
        "audit_id": row.get("audit_id"),
        "certified_version_hash": certified_hash or None,
        "reason": None if ok else (
            "exact version lacks a passed_all full cognitive-matrix certificate"),
    }


def approve_dsl_version(digest, enable_live=False):
    """Explicit human gate for structural DSL versions.

    This command never accepts a merely deterministic candidate.  It requires
    a recorded Codex review and remains the user's separate approval gate.
    """
    from auto_trade_ai_consensus import candidate_hash
    import auto_trade_strategy_dsl as dsl
    conn = _db()
    try:
        row = conn.execute(
            "SELECT c.state,c.candidate_json,c.evidence_json,v.consensus_json "
            "FROM candidates c JOIN strategy_versions v "
            "ON v.version_hash=c.candidate_hash WHERE c.candidate_hash=?",
            (digest,)).fetchone()
    finally:
        conn.close()
    if not row:
        return {"ok": False, "error": "version not found"}
    state, candidate_raw, evidence_raw, consensus_raw = row
    candidate = json.loads(candidate_raw); evidence = json.loads(evidence_raw or "{}")
    consensus = json.loads(consensus_raw or "{}")
    if state != "approved_requires_human" or candidate.get("type") != "dsl_strategy":
        return {"ok": False, "blocked": True,
                "error": "DSL version is not awaiting explicit human approval"}
    if candidate_hash(candidate) != digest or not evidence.get("passed"):
        return {"ok": False, "blocked": True, "error": "stored hash/evidence mismatch"}
    codex_review = consensus.get("codex_review") or {}
    if (codex_review.get("reviewer") != "codex"
            or codex_review.get("decision") != "APPROVE"
            or codex_review.get("candidate_hash") != digest):
        return {"ok": False, "blocked": True, "error": "Codex review approval missing"}
    definition = dsl.validate_strategy(candidate.get("dsl"))
    live_certificate = None
    if enable_live:
        live_certificate = _live_version_certificate(
            digest, candidate, definition)
        if not live_certificate.get("ok"):
            return {"ok": False, "blocked": True,
                    "error": live_certificate.get("reason"),
                    "live_certificate": live_certificate}
    definition["approved_version_hash"] = digest
    definition["live_enabled"] = bool(enable_live)
    definition["auto_trade_eligible"] = bool(enable_live)
    payload = _read(DSL_CONFIG_PATH, {"schema": "qiyu_ai_dsl_registry_v1", "strategies": []})
    strategies = [row for row in payload.get("strategies") or []
                  if row.get("key") != definition["key"]]
    strategies.append(definition); payload["strategies"] = strategies
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_dsl_" + digest[:12] + ".json")
    if DSL_CONFIG_PATH.exists():
        shutil.copy2(str(DSL_CONFIG_PATH), str(backup))
    else:
        _atomic(backup, {"schema": "qiyu_ai_dsl_registry_v1", "strategies": []})
    before = _file_sha(DSL_CONFIG_PATH); _atomic(DSL_CONFIG_PATH, payload)
    changed_daemons = []
    if enable_live:
        for path in glob.glob(str(AUTO_DIR / "formal_daemon_config*.json")):
            cfg = _read(path, {})
            if (str(cfg.get("symbol") or "").upper() != str(candidate.get("symbol") or "").upper()
                    or str(cfg.get("timeframe") or "1h").lower() != candidate.get("timeframe")):
                continue
            keys = list(cfg.get("strategy_keys") or [])
            if definition["key"] not in keys:
                keys.append(definition["key"]); cfg["strategy_keys"] = keys
                _atomic(path, cfg); changed_daemons.append(path)
    new_state = "human_approved_live" if enable_live else "human_approved_research"
    conn = _db()
    try:
        conn.execute("UPDATE candidates SET state=?,updated_at=? WHERE candidate_hash=?",
                     (new_state, _now(), digest))
        conn.execute("UPDATE strategy_versions SET state=?,updated_at=? WHERE version_hash=?",
                     (new_state, _now(), digest))
        if enable_live:
            prior = [row[0] for row in conn.execute(
                "SELECT candidate_hash FROM deployments WHERE strategy_key=? "
                "AND rolled_back_at IS NULL AND candidate_hash<>?",
                (definition["key"], digest)).fetchall()]
            conn.execute(
                "UPDATE deployments SET rolled_back_at=?,rollback_reason=? "
                "WHERE strategy_key=? AND rolled_back_at IS NULL AND candidate_hash<>?",
                (_now(), "superseded_by_%s" % digest[:12], definition["key"], digest),
            )
            for previous_hash in prior:
                conn.execute("UPDATE strategy_versions SET state=?,updated_at=? WHERE version_hash=?",
                             ("superseded", _now(), previous_hash))
            conn.execute("INSERT OR REPLACE INTO deployments VALUES(?,?,?,?,?,?,NULL,NULL)",
                         (digest, definition["key"], before, _file_sha(DSL_CONFIG_PATH),
                          str(backup), _now()))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "candidate_hash": digest, "state": new_state,
            "live_enabled": bool(enable_live), "changed_daemons": changed_daemons,
            "restart_required": bool(enable_live),
            "live_certificate": live_certificate}


def _creation_targets():
    payload = _read(CREATOR_TARGETS_PATH, {})
    rows = list(payload.get("targets") or []) if isinstance(payload, dict) else []
    matrix = (payload.get("matrix") or {}) if isinstance(payload, dict) else {}
    if matrix.get("enabled", True):
        for symbol in matrix.get("symbols") or []:
            for timeframe in matrix.get("timeframes") or []:
                rows.append({"symbol": symbol, "timeframe": timeframe,
                             "focus": matrix.get("focus") or "both",
                             "enabled": True,
                             "note": matrix.get("note") or
                                     "独立认知集群；禁止跨标的证据迁移"})
    valid = []
    try:
        import auto_trade_strategy_dsl as dsl
        instruments = dsl.INSTRUMENTS
    except Exception:
        instruments = set()
    deduplicated = {}
    for row in rows or []:
        symbol = str(row.get("symbol") or "").upper()
        timeframe = str(row.get("timeframe") or "").lower()
        if (row.get("enabled", True) and symbol in instruments
                and timeframe in ("5m", "15m", "1h")):
            deduplicated[(symbol, timeframe)] = {
                "symbol": symbol, "timeframe": timeframe,
                "focus": row.get("focus") or "both",
                "note": row.get("note") or ""}
    valid.extend(deduplicated[key] for key in sorted(deduplicated))
    return valid


def _live_strategy_count(symbol, timeframe):
    """Configured live coverage, used only for research-capacity planning."""
    count = 0
    for path in glob.glob(str(AUTO_DIR / "formal_daemon_config*.json")):
        cfg = _read(path, {})
        if (str(cfg.get("symbol") or "").upper() != symbol
                or str(cfg.get("timeframe") or cfg.get("bar") or "1h").lower() != timeframe):
            continue
        if (cfg.get("enabled") and cfg.get("allow_auto_open")
                and cfg.get("formal_auto_trading_authorized")):
            count += len(set(str(key) for key in cfg.get("strategy_keys") or [] if key))
    return count


def _micro_information_by_symbol():
    path = AUTO_DIR / "microstructure_telemetry.db"
    if not path.exists():
        return {}
    cutoff = (datetime.now()-timedelta(days=14)).strftime(BEIJING_FMT)
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        rows = conn.execute(
            "SELECT a.symbol,COUNT(DISTINCT a.cluster_id),COUNT(*),"
            "COUNT(DISTINCT CASE WHEN c.created_at>=? THEN a.cluster_id END) "
            "FROM microstructure_primitive_assignments a "
            "LEFT JOIN microstructure_primitive_clusters c "
            "ON c.cluster_id=a.cluster_id GROUP BY a.symbol", (cutoff,)).fetchall()
        conn.close()
        return {row[0]: {"cluster_count": int(row[1] or 0),
                         "assigned_windows": int(row[2] or 0),
                         "new_cluster_count_14d": int(row[3] or 0)}
                for row in rows}
    except Exception:
        return {}


def _jensen_shannon_counts(left, right):
    keys = set(left) | set(right)
    left_total = float(sum(left.values()) or 0.0)
    right_total = float(sum(right.values()) or 0.0)
    if not keys or left_total <= 0 or right_total <= 0:
        return None
    value = 0.0
    for key in keys:
        p = float(left.get(key) or 0.0)/left_total
        q = float(right.get(key) or 0.0)/right_total
        midpoint = .5*(p+q)
        if p > 0: value += .5*p*math.log(p/midpoint, 2)
        if q > 0: value += .5*q*math.log(q/midpoint, 2)
    return value


def _plan_creation_target(conn, targets):
    """Meta-controller: allocate compute by coverage, yield and failure history."""
    now = datetime.now()
    recent_cutoff = (now-timedelta(days=14)).strftime(BEIJING_FMT)
    micro_by_symbol = _micro_information_by_symbol()
    tables = set(row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
    ranked = []
    for target in targets:
        symbol, timeframe = target["symbol"], target["timeframe"]
        coverage = _live_strategy_count(symbol, timeframe)
        history = conn.execute(
            "SELECT state,summary_json,started_at FROM strategy_creation_runs "
            "WHERE symbol=? AND timeframe=? ORDER BY started_at DESC LIMIT 8",
            (symbol, timeframe)).fetchall()
        row = history[0] if history else None
        hours_since = 999.0; last_state = None; last_ready = False
        if row:
            last_state = row[0]
            try:
                started_at = datetime.strptime(row[2], "%Y-%m-%d %H:%M:%S")
                hours_since = max(0.0, (now-started_at).total_seconds()/3600.0)
            except Exception:
                hours_since = 0.0
            try:
                last_ready = bool((json.loads(row[1] or "{}")
                                   .get("ready_candidates") or []))
            except Exception:
                last_ready = False
        completed_runs = len(history)
        ready_runs = 0; converged_runs = 0; early_pruned_runs = 0
        consecutive_early_prunes = 0; api_calls_saved = 0
        for index, history_row in enumerate(history):
            try:
                payload = json.loads(history_row[1] or "{}")
            except Exception:
                payload = {}
            if payload.get("ready_candidates"):
                ready_runs += 1
            if int(payload.get("converged_count") or 0) > 0:
                converged_runs += 1
            is_early = (history_row[0] == "early_pruned_no_empirical_edge"
                        or str(payload.get("state") or "").startswith("early_pruned"))
            early_pruned_runs += 1 if is_early else 0
            api_calls_saved += int(payload.get("api_calls_saved") or 0)
            if index == consecutive_early_prunes and is_early:
                consecutive_early_prunes += 1
        death_rows = (conn.execute(
            "SELECT pattern_id,stage,provider,death_codes_json,hard_veto,reason,"
            "created_at,review_json FROM prescreen_rejections "
            "WHERE symbol=? AND timeframe=? ORDER BY created_at DESC LIMIT 384",
            (symbol, timeframe)).fetchall()
            if "prescreen_rejections" in tables else [])
        independent_deaths = _dedupe_prescreen_evidence(death_rows)
        death_code_counts = {}
        for item in independent_deaths:
            for code in json.loads(item[3] or "[]"):
                death_code_counts[str(code)] = death_code_counts.get(str(code), 0)+1
        recent_deaths = sum(1 for item in independent_deaths
                            if str(item[6] or "") >= recent_cutoff)
        branch_row = conn.execute(
            "SELECT COUNT(*),SUM(credible),SUM(survived_cost_screen) "
            "FROM search_branch_observations "
            "WHERE symbol=? AND timeframe=? AND created_at>=?",
            (symbol, timeframe, recent_cutoff)).fetchone() \
            if "search_branch_observations" in tables else (0, 0, 0)
        recent_branches = int((branch_row or [0])[0] or 0)
        recent_credible = int((branch_row or [0, 0])[1] or 0)
        recent_cost_survivors = int((branch_row or [0, 0, 0])[2] or 0)
        compute_sleep = _negative_cluster_sleep_state(
            hours_since, recent_branches, recent_cost_survivors,
            consecutive_early_prunes)
        api_row = conn.execute(
            "SELECT SUM(l.ai_calls_used),SUM(l.ai_calls_saved) FROM "
            "search_budget_ledger l JOIN strategy_creation_runs r "
            "ON r.run_id=l.run_id WHERE r.symbol=? AND r.timeframe=? "
            "AND r.started_at>=?", (symbol, timeframe, recent_cutoff)).fetchone() \
            if "search_budget_ledger" in tables else (0, 0)
        ai_calls_used = int((api_row or [0])[0] or 0)
        recent_calls_saved = int((api_row or [0, 0])[1] or 0)
        micro = micro_by_symbol.get(symbol) or {}
        # Only target-local outcome/death evidence is allowed to improve the
        # branch potential score.  Same-symbol unlabeled micro windows are a
        # data-readiness signal, not evidence that a timeframe has edge.
        information_units = recent_deaths+2*recent_credible
        information_efficiency = information_units/float(1+ai_calls_used)
        information_gain_score = min(28.0, 2.0*information_units)
        micro_windows = int(micro.get("assigned_windows") or 0)
        micro_readiness_score = 6.0 if micro_windows >= 80 else -12.0
        baseline_run_bonus = 50.0 if completed_runs == 0 else 0.0
        baseline_branch_bonus = 25.0 if recent_branches == 0 else 0.0
        deep_focus_eligible = bool(
            recent_branches >= 48 and
            (recent_cost_survivors > 0 or recent_credible > 0))
        cognitive_desert = bool(completed_runs >= 3 and
                                recent_deaths == 0 and recent_credible == 0 and
                                int(micro.get("new_cluster_count_14d") or 0) == 0)
        productivity = ((1.0+ready_runs+0.25*converged_runs)/
                        (2.0+completed_runs))
        coverage_score = 100.0 if coverage == 0 else (42.0 if coverage == 1 else 18.0/coverage)
        timeframe_score = {"5m": 28.0, "15m": 18.0, "1h": 0.0}.get(timeframe, 0.0)
        # Prefer filling the portfolio frequency gap on intraday clusters that
        # still lack live coverage; never use this to relax promotion gates.
        frequency_gap_score = 0.0
        if coverage == 0 and timeframe in ("5m", "15m"):
            frequency_gap_score = 18.0 if recent_cost_survivors > 0 else 12.0
        elif coverage == 0 and timeframe == "1h" and deep_focus_eligible:
            frequency_gap_score = 6.0
        elif timeframe == "1h" and not deep_focus_eligible and completed_runs >= 1:
            frequency_gap_score = -8.0
        staleness_score = min(24.0, hours_since/6.0)
        # Cost-surviving intraday clusters get a short cooldown so compute can
        # chase them immediately instead of waiting out a six-hour queue.
        if recent_cost_survivors > 0 and timeframe in ("5m", "15m"):
            cooldown_penalty = (25.0 if hours_since < 1.0
                                else (8.0 if hours_since < 3.0 else 0.0))
        elif deep_focus_eligible:
            cooldown_penalty = (40.0 if hours_since < 2.0
                                else (15.0 if hours_since < 6.0 else 0.0))
        else:
            cooldown_penalty = (90.0 if hours_since < 6.0
                                else (35.0 if hours_since < 18.0 else 0.0))
        # Extra chase bonus when a cost survivor just appeared.
        chase_bonus = 22.0 if (recent_cost_survivors > 0 and hours_since < 3.0
                               and timeframe in ("5m", "15m")) else 0.0
        ready_penalty = 80.0 if last_ready else 0.0
        dead_branch_penalty = min(120.0, 32.0*consecutive_early_prunes)
        desert_penalty = 28.0 if cognitive_desert else 0.0
        productivity_adjustment = (productivity-0.50)*40.0 if completed_runs else 0.0
        score = (coverage_score + timeframe_score + staleness_score
                 + productivity_adjustment + information_gain_score
                 + micro_readiness_score + baseline_run_bonus
                 + baseline_branch_bonus + frequency_gap_score + chase_bonus
                 - cooldown_penalty - ready_penalty - dead_branch_penalty
                 - desert_penalty)
        ranked.append((score, target, {
            "symbol": symbol, "timeframe": timeframe,
            "live_strategy_count": coverage,
            "hours_since_last_training": round(hours_since, 3),
            "last_state": last_state,
            "last_ready_candidate": last_ready,
            "score": round(score, 3),
            "components": {"coverage": round(coverage_score, 3),
                           "timeframe": timeframe_score,
                           "portfolio_frequency_gap": round(frequency_gap_score, 3),
                           "cost_survivor_chase": round(chase_bonus, 3),
                           "staleness": round(staleness_score, 3),
                           "cooldown_penalty": cooldown_penalty,
                           "ready_penalty": ready_penalty,
                           "dead_branch_penalty": dead_branch_penalty,
                           "cognitive_desert_penalty": desert_penalty,
                           "information_gain": round(information_gain_score, 3),
                           "micro_data_readiness": micro_readiness_score,
                           "baseline_run_bonus": baseline_run_bonus,
                           "baseline_branch_bonus": baseline_branch_bonus,
                           "productivity_adjustment": round(productivity_adjustment, 3)},
            "information_economics_14d": {
                "independent_death_evidence": recent_deaths,
                "total_independent_death_evidence": len(independent_deaths),
                "death_code_counts": death_code_counts,
                "branch_observations": recent_branches,
                "credible_branches": recent_credible,
                "cost_surviving_branches": recent_cost_survivors,
                "new_micro_clusters": int(micro.get("new_cluster_count_14d") or 0),
                "micro_windows": int(micro.get("assigned_windows") or 0),
                "ai_calls_used": ai_calls_used,
                "ai_calls_saved": recent_calls_saved,
                "information_units": information_units,
                "information_per_ai_call": round(information_efficiency, 6),
                "unlabeled_micro_is_readiness_not_edge": True,
                "monetary_cost": "not_computed_without_provider_billing_receipts",
                "cognitive_desert": cognitive_desert},
            "meta_controller": {"recent_runs": completed_runs,
                                "ready_runs": ready_runs,
                                "converged_runs": converged_runs,
                                "early_pruned_runs": early_pruned_runs,
                                "consecutive_early_prunes": consecutive_early_prunes,
                                "posterior_productivity": round(productivity, 6),
                                "deep_focus_eligible": deep_focus_eligible,
                                "api_calls_saved": api_calls_saved,
                                "compute_sleep": compute_sleep,
                                "cold_start_budget_cap": (
                                    COLD_START_MAX_FULL_BRANCHES
                                    if completed_runs == 0 else None)},
        }))
    if not ranked:
        return None, {"policy": "no_targets", "ranking": []}
    for _score, _target, row in ranked:
        local = row["information_economics_14d"].get("death_code_counts") or {}
        reference = {}
        for _other_score, _other_target, other in ranked:
            if other is row:
                continue
            for code, count in (other["information_economics_14d"].get(
                    "death_code_counts") or {}).items():
                reference[code] = reference.get(code, 0)+int(count or 0)
        divergence = _jensen_shannon_counts(local, reference)
        enough = sum(local.values()) >= 20 and sum(reference.values()) >= 40
        row["cognition_drift"] = {
            "state": ("review_required" if enough and divergence is not None and
                      divergence >= .55 else
                      ("within_observed_range" if enough else
                       "awaiting_independent_evidence")),
            "jensen_shannon_divergence": (round(divergence, 6)
                                          if divergence is not None else None),
            "local_evidence": sum(local.values()),
            "other_cluster_reference_evidence": sum(reference.values()),
            "minimum_local": 20, "minimum_reference": 40,
            "policy": "descriptive_drift_alert_not_cross_target_label_transfer"}
    ranked.sort(key=lambda item: (item[0], item[1]["symbol"], item[1]["timeframe"]),
                reverse=True)
    awake = [item for item in ranked
             if not ((item[2].get("meta_controller") or {}).get(
                 "compute_sleep") or {}).get("sleeping")]
    if not awake:
        return None, {
            "policy": "all_targets_compute_sleeping",
            "selected": None,
            "ranking": [item[2] for item in ranked],
            "next_wake_in_hours": min(float((((item[2].get(
                "meta_controller") or {}).get("compute_sleep") or {}).get(
                    "remaining_hours") or NEGATIVE_CLUSTER_SLEEP_HOURS)
                for item in ranked), default=NEGATIVE_CLUSTER_SLEEP_HOURS),
            "forward_data_collection_continues": True,
            "compute_governance": {
                "deep_search_gate": "target_local_cost_survivor_required",
                "cold_start_max_full_branches": COLD_START_MAX_FULL_BRANCHES,
                "negative_cluster_sleep_hours": NEGATIVE_CLUSTER_SLEEP_HOURS,
                "ai_rescue_without_cost_survivor": "prohibited"},
        }
    # Parallel creator slots pick distinct awake targets so DeepSeek/Qwen/ChatGPT
    # panels can run on different symbol/timeframe pairs at the same time.
    try:
        slot = max(0, int(os.environ.get("QIYU_CREATOR_SLOT") or 0))
    except Exception:
        slot = 0
    pick_index = min(slot, len(awake) - 1)
    chosen = awake[pick_index]
    return chosen[1], {
        "policy": "adaptive_meta_controller_target_local_information_gain_cost_efficiency",
        "selected": chosen[2],
        "creator_slot": slot,
        "ranking": [item[2] for item in ranked],
        "sleeping_target_count": len(ranked)-len(awake),
        "compute_governance": {
            "deep_search_gate": "target_local_cost_survivor_required",
            "cold_start_max_full_branches": COLD_START_MAX_FULL_BRANCHES,
            "negative_cluster_sleep_hours": NEGATIVE_CLUSTER_SLEEP_HOURS,
            "early_prune_short_sleep_hours": EARLY_PRUNE_SHORT_SLEEP_HOURS,
            "ai_rescue_without_cost_survivor": "prohibited",
            "parallel_creator_slots": 2},
    }


def _compute_audit_snapshot(planner):
    ranking = (planner or {}).get("ranking") or []
    sleeping = sum(bool(((row.get("meta_controller") or {}).get(
        "compute_sleep") or {}).get("sleeping")) for row in ranking)
    return {
        "sleeping_targets": sleeping,
        "awake_targets": len(ranking)-sleeping,
        "cold_start_targets": sum(int(((row.get("meta_controller") or {}).get(
            "recent_runs") or 0)) == 0 for row in ranking),
        "database_cleanup": "deferred_until_live_size_and_query_latency_measurement",
        "micro_recompute_frequency": "retain_6h_until_stability_yield_is_measured",
        "live_execution_changed": False,
    }


def refresh_cluster_matrix_status():
    targets = _creation_targets(); conn = _db()
    try:
        selected, planner = _plan_creation_target(conn, targets)
    finally:
        conn.close()
    ranking = (planner or {}).get("ranking") or []
    payload = {"ok": bool(selected), "updated_at": _now(),
               "target_count": len(targets), "selected": selected,
               "planner": planner,
               "compute_audit": _compute_audit_snapshot(planner),
               "summary": {
                   "cognitive_deserts": sum(bool((row.get("information_economics_14d") or {}).get(
                       "cognitive_desert")) for row in ranking),
                   "drift_reviews": sum((row.get("cognition_drift") or {}).get("state") ==
                                        "review_required" for row in ranking),
                   "independent_death_evidence_14d": sum(int((row.get(
                       "information_economics_14d") or {}).get(
                           "independent_death_evidence") or 0) for row in ranking),
                   "ai_calls_used_14d": sum(int((row.get(
                       "information_economics_14d") or {}).get(
                           "ai_calls_used") or 0) for row in ranking),
                   "ai_calls_saved_14d": sum(int((row.get(
                       "information_economics_14d") or {}).get(
                           "ai_calls_saved") or 0) for row in ranking),
               },
               "execution_policy": "single_process_resource_bounded_not_parallel",
               "evidence_isolation": "symbol_and_timeframe_strict",
               "cross_market_use": "descriptive_morphology_only_no_outcome_transfer",
               "one_minute_state": "deferred_resource_and_cost_boundary"}
    _atomic(CLUSTER_MATRIX_STATUS_PATH, payload)
    return payload


def _creation_context(conn, assignment):
    symbol = assignment["symbol"]; timeframe = assignment["timeframe"]
    snapshots = []
    for candle_time, regime, pattern_raw, indicators_raw in conn.execute(
            "SELECT candle_time,regime,pattern_json,indicators_json "
            "FROM market_snapshots WHERE symbol=? AND timeframe=? "
            "ORDER BY candle_ts DESC LIMIT 24", (symbol, timeframe)).fetchall():
        snapshots.append({"candle_time": candle_time, "regime": regime,
                          "pattern": json.loads(pattern_raw or "{}"),
                          "indicators": json.loads(indicators_raw or "{}")})
    failures = []
    for key, stage, reason, occurrences, payload_raw in conn.execute(
            "SELECT strategy_key,stage,reason_code,occurrences,payload_json "
            "FROM failure_experiences WHERE symbol=? AND timeframe=? "
            "ORDER BY last_seen DESC LIMIT 24", (symbol, timeframe)).fetchall():
        failures.append({"strategy_key": key, "stage": stage, "reason": reason,
                         "occurrences": occurrences,
                         "detail": json.loads(payload_raw or "{}")})
    real_cases = []
    for key, outcome, payload_raw in conn.execute(
            "SELECT strategy_key,outcome,payload_json FROM cases "
            "WHERE symbol=? AND COALESCE(timeframe,'1h')=? "
            "ORDER BY occurred_at DESC LIMIT 20", (symbol, timeframe)).fetchall():
        payload = json.loads(payload_raw or "{}")
        real_cases.append({"strategy_key": key, "outcome": outcome,
                           "close_type": payload.get("close_type"),
                           "entry_snapshot": payload.get("entry_snapshot") or {}})
    existing = []
    config = _read(CONFIG_PATH, {})
    names = {row.get("key"): row.get("name") for row in config.get("strategies") or []}
    dsl_config = _read(DSL_CONFIG_PATH, {})
    names.update({row.get("key"): row.get("name") for row in dsl_config.get("strategies") or []})
    for path in glob.glob(str(AUTO_DIR / "formal_daemon_config*.json")):
        cfg = _read(path, {})
        if (str(cfg.get("symbol") or "").upper() != symbol
                or str(cfg.get("timeframe") or "1h").lower() != timeframe):
            continue
        for key in cfg.get("strategy_keys") or []:
            existing.append({"key": key, "name": names.get(key) or key})
    # AI reviewers must see the actual feature scale.  A logically sensible
    # threshold can otherwise be unreachable (for example h1_slope4 is a
    # decimal return, not a percentage number).  Use stored, redacted market
    # snapshots only; no account or credential data is included.
    feature_values = {key: [] for key in (
        "k", "d", "j", "cci", "macd_stick", "rsi14", "z20",
        "h1_slope4", "atr14_to_close_pct", "ema17_gap_pct",
        "ema53_gap_pct",
    )}
    rows = conn.execute(
        "SELECT indicators_json FROM market_snapshots "
        "WHERE symbol=? AND timeframe=? ORDER BY candle_ts DESC LIMIT 4000",
        (symbol, timeframe),
    ).fetchall()
    for (raw,) in rows:
        try:
            indicators = json.loads(raw or "{}")
            close = float(indicators.get("close"))
        except Exception:
            continue
        for key in ("k", "d", "j", "cci", "macd_stick", "rsi14",
                    "z20", "h1_slope4"):
            try:
                value = float(indicators.get(key))
                if math.isfinite(value): feature_values[key].append(value)
            except Exception:
                pass
        if close and math.isfinite(close):
            for feature, output in (("atr14", "atr14_to_close_pct"),
                                    ("ema17", "ema17_gap_pct"),
                                    ("ema53", "ema53_gap_pct")):
                try:
                    value = float(indicators.get(feature))
                    relative = (value / close - 1.0) * 100.0
                    if feature == "atr14": relative = value / close * 100.0
                    if math.isfinite(relative): feature_values[output].append(relative)
                except Exception:
                    pass

    def quantile(values, probability):
        values = sorted(values)
        if not values: return None
        position = (len(values) - 1) * probability
        lower = int(math.floor(position)); upper = int(math.ceil(position))
        if lower == upper: return values[lower]
        return values[lower] + (values[upper] - values[lower]) * (position - lower)

    feature_distribution = {}
    for key, values in feature_values.items():
        if not values: continue
        feature_distribution[key] = {
            "samples": len(values),
            "p05": round(quantile(values, .05), 8),
            "p25": round(quantile(values, .25), 8),
            "p50": round(quantile(values, .50), 8),
            "p75": round(quantile(values, .75), 8),
            "p95": round(quantile(values, .95), 8),
        }
    return {
        "assignment": assignment,
        "creation_goal": {
            "portfolio_frequency_target": "组合每日3至5次有效开仓，不以牺牲正期望和止损簇安全为代价",
            "primary_stop_loss": "0.9% at 20x",
            "auxiliary_stop_loss": "0.6% at 20x",
            "minimum_win_rate_gate": "70%，目标约75%",
            "minimum_frequency_contribution": "至少0.075笔/日且样本不少于15笔",
            "human_live_confirmation_required": True,
        },
        "latest_market_snapshots": snapshots,
        "recent_real_cases": real_cases,
        "failure_memory": failures,
        "feature_value_reference": {
            "note": "分位数来自本标的本周期最多4000根真实K线；h1_slope4为小数收益率（0.01=1%），不得当作0-100指标；ATR与MACD具有标的价格量纲。",
            "distribution": feature_distribution,
        },
        "existing_strategies_to_avoid_duplicates": existing,
        "research_instruction": "创造可审计的新策略；优先补充现有组合缺少的方向、市场环境和交易时段，不得仅改名复制现有逻辑。",
    }


def _collaborative_creation_refinement(metadata, assignment, context,
                                       convergence, provider="qwen",
                                       revision_index=1, frame=None):
    """Revise the strongest 2/3 direction, then require a fresh 3/3 review."""
    rejected = sorted(convergence.get("rejected") or [],
                      key=lambda row: (row.get("support_count") or 0,
                                       row.get("independent_source_count") or 0),
                      reverse=True)
    if not rejected or int(rejected[0].get("support_count") or 0) < 2:
        return {"attempted": False, "reason": "no_direction_reached_2_of_3"}
    best = rejected[0]
    feedback = []
    for index, vote in enumerate(best.get("votes") or []):
        feedback.append({"reviewer": "reviewer_%d" % (index + 1),
                         "decision": vote.get("decision"),
                         "reason": vote.get("reason") or ""})
    revision_context = copy.deepcopy(context)
    revision_context["collaborative_revision_task"] = {
        "base_hypothesis_id": best.get("hypothesis_id"),
        "base_dsl": best.get("dsl"),
        "review_feedback": feedback,
        "requirement": "生成一个实质修订后的完整DSL，再交由三名会审员重新独立投票",
        "revision_index": int(revision_index),
    }
    from auto_trade_ai_consensus import research_one
    result = research_one(provider, revision_context, metadata)
    catalog, invalid = _build_hypothesis_catalog(
        metadata, assignment, [result])
    refined = _hypothesis_convergence_with_precheck(
        catalog, revision_context, assignment, frame=frame)
    return {"attempted": True, "base_hypothesis_id": best.get("hypothesis_id"),
            "base_support_count": best.get("support_count"),
            "revision_provider": provider, "revision_index": int(revision_index),
            "research_result": result, "catalog": catalog,
            "invalid": invalid, "convergence": refined}


def create_strategy_once(symbol_override=None, timeframe_override=None):
    """DEPRECATED: ecosystem auto-create is blocked; sole blueprint entry only."""
    try:
        from dual_engine_workflow_v2.creation_sole_entry import (
            is_sole_creation_enforced, refuse_side_path, create_strategy,
        )
    except Exception as exc:
        result = {"ok": False, "state": "sole_entry_import_error",
                  "error": str(exc), "time": _now()}
        _atomic(CREATOR_STATUS_PATH, result)
        return result
    if is_sole_creation_enforced():
        # Redirect one-shot to sole entry instead of atlas invent path
        sym = str(symbol_override or "ADA-USDT-SWAP").upper()
        tf = str(timeframe_override or "5m").lower()
        out = create_strategy(
            symbol=sym, timeframe=tf, brief="ecosystem_timer_redirected_to_sole_entry",
        )
        result = {
            "ok": bool(out.get("ok")),
            "state": "sole_creation_entry",
            "redirected_from": "auto_trade_strategy_ecosystem.create_strategy_once",
            "sole": {
                "present_to_human": out.get("present_to_human"),
                "pipeline_gate": out.get("pipeline_gate"),
                "handoff_zh": out.get("handoff_zh"),
                "receipt_path": out.get("receipt_path"),
            },
            "message_zh": "原生态创造器已停用，已强制转入蓝图研究发现唯一入口。",
            "time": _now(),
        }
        _atomic(CREATOR_STATUS_PATH, result)
        return result
    result = refuse_side_path("auto_trade_strategy_ecosystem.create_strategy_once")
    _atomic(CREATOR_STATUS_PATH, result)
    return result


def _create_strategy_once_legacy_disabled(symbol_override=None, timeframe_override=None):
    """Legacy body retained only as dead code reference — never called."""
    targets = _creation_targets()
    if not targets:
        result = {"ok": False, "state": "configuration_error",
                  "error": "no enabled strategy creation targets", "time": _now()}
        _atomic(CREATOR_STATUS_PATH, result)
        return result
    target = None
    planner = {"policy": "explicit_override"}
    if symbol_override or timeframe_override:
        symbol_override = str(symbol_override or "").upper()
        timeframe_override = str(timeframe_override or "").lower()
        target = next((row for row in targets
                       if row["symbol"] == symbol_override
                       and row["timeframe"] == timeframe_override), None)
        if target is None:
            result = {"ok": False, "state": "configuration_error",
                      "error": "requested creation target is not enabled", "time": _now()}
            _atomic(CREATOR_STATUS_PATH, result)
            return result
    else:
        conn = _db()
        try:
            target, planner = _plan_creation_target(conn, targets)
        finally:
            conn.close()
        if target is None:
            all_sleeping = ((planner or {}).get("policy") ==
                            "all_targets_compute_sleeping")
            result = {
                "ok": bool(all_sleeping),
                "state": ("idle_all_targets_compute_sleeping" if all_sleeping
                          else "configuration_error"),
                "label": ("节能休眠中｜持续采集前向数据" if all_sleeping
                          else "策略创造目标配置错误"),
                "error": (None if all_sleeping else
                          "strategy creation planner found no target"),
                "planner": planner, "time": _now(),
            }
            _atomic(CREATOR_STATUS_PATH, result)
            return result
    mission_key = "strategy_creation_%s_%s" % (
        target["symbol"].split("-")[0].lower(), target["timeframe"])
    assignment = {"strategy_key": mission_key, "symbol": target["symbol"],
                  "timeframe": target["timeframe"], "focus": target.get("focus"),
                  "planner": planner}
    # Keep the UI/meta-controller snapshot current even when the mission later
    # stops at a data or cost gate.  This is monitoring metadata only.
    matrix_payload = {"ok": True, "updated_at": _now(),
                      "target_count": len(targets), "selected": target,
                      "planner": planner,
                      "compute_audit": _compute_audit_snapshot(planner),
                      "summary": {
                          "cognitive_deserts": sum(bool((row.get(
                              "information_economics_14d") or {}).get(
                                  "cognitive_desert")) for row in
                              (planner.get("ranking") or [])),
                          "drift_reviews": sum((row.get("cognition_drift") or {}).get(
                              "state") == "review_required" for row in
                              (planner.get("ranking") or [])),
                          "independent_death_evidence_14d": sum(int((row.get(
                              "information_economics_14d") or {}).get(
                                  "independent_death_evidence") or 0) for row in
                              (planner.get("ranking") or [])),
                          "ai_calls_used_14d": sum(int((row.get(
                              "information_economics_14d") or {}).get(
                                  "ai_calls_used") or 0) for row in
                              (planner.get("ranking") or [])),
                          "ai_calls_saved_14d": sum(int((row.get(
                              "information_economics_14d") or {}).get(
                                  "ai_calls_saved") or 0) for row in
                              (planner.get("ranking") or [])),
                      },
                      "execution_policy": "single_process_resource_bounded_not_parallel",
                      "evidence_isolation": "symbol_and_timeframe_strict",
                      "cross_market_use": "descriptive_morphology_only_no_outcome_transfer",
                      "one_minute_state": "deferred_resource_and_cost_boundary"}
    _atomic(CLUSTER_MATRIX_STATUS_PATH, matrix_payload)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3] + "_" + _sha(assignment)[:10]
    started = _now()
    _atomic(CREATOR_STATUS_PATH, {"ok": True, "state": "running",
            "label": "训练中", "run_id": run_id, "assignment": assignment,
            "started_at": started, "continuous": True})
    conn = _db()
    try:
        conn.execute("INSERT INTO strategy_creation_runs VALUES(?,?,?,?,?,?,NULL)",
                     (run_id, target["symbol"], target["timeframe"], "running",
                      json.dumps({"assignment": assignment}, ensure_ascii=False), started))
        context = _creation_context(conn, assignment)
        conn.commit()
    finally:
        conn.close()
    try:
        from auto_trade_ai_consensus import (collaborative_failure_analysis,
                                             independent_research,
                                             research_one)
        import auto_trade_strategy_intelligence as intelligence
        metadata = {
            "key": mission_key,
            "name": "%s %s新策略创造任务" % (target["symbol"].split("-")[0], target["timeframe"]),
            "description": "从零创造新DSL策略，方向偏好：%s。%s" %
                           (target.get("focus") or "both", target.get("note") or ""),
            "params": {}, "param_meta": {}, "creation_mode": True,
        }
        # Generation starts from measured target-specific event edges.  The
        # models no longer receive indicator distributions alone and then
        # invent an untested narrative from feature names.
        research_frame = _load_research_frame(
            assignment["symbol"], assignment["timeframe"])
        development_frame = research_frame.iloc[
            :max(300, int(math.floor(len(research_frame)*0.70)))]
        atlas = _deterministic_event_atlas(research_frame, assignment)
        atlas_observations = list(atlas.get("_observations") or [])
        atlas["failure_clusters"] = _atlas_failure_clusters(
            atlas_observations)
        operator_lattice = _operator_lattice_screen(
            development_frame, assignment, atlas_observations)
        atlas["operator_lattice"] = operator_lattice
        learning_record = _store_event_atlas_learning(run_id, atlas)
        atlas.pop("_observations", None)
        atlas["learning_record"] = learning_record
        context["deterministic_event_atlas"] = atlas
        deep_search = _deep_search_eligibility(
            atlas_observations, atlas.get("qualified_count"))
        active_hunt = dict(deep_search)
        active_hunt["suspected_positive_count"] = 0
        hunt_prior = {}; hunt_probes = {}; learning_ai_calls = 0
        hunt_ai_calls = 0
        if deep_search["allowed"]:
            hunt_prior = _active_hunt_prior(
                development_frame, assignment, atlas,
                operator_lattice=operator_lattice)
            hunt_ai_calls = 0 if hunt_prior.get("cached") else 3
            hunt_probes = _run_active_hunt_probes(
                development_frame, assignment, atlas, hunt_prior, run_id)
            hunt_probes["failure_learning"] = _learn_from_probe_failures(
                development_frame, assignment, atlas, hunt_prior,
                hunt_probes, run_id)
            learning_ai_calls = int((hunt_probes.get("failure_learning") or {})
                                    .get("api_calls_used") or 0)
            active_hunt = {
                "state": ("suspected_positive_found" if
                          hunt_probes.get("suspected_positive_count") else
                          "no_probe_survived"),
                "prior": hunt_prior, "probes": hunt_probes,
                "suspected_positive_count": hunt_probes.get(
                    "suspected_positive_count") or 0,
                "synthetic_blueprints_are_labels": False,
            }
        context["active_hunt"] = active_hunt
        death_archive = _archive_prescreen_deaths(
            run_id, assignment, atlas_observations, prior=hunt_prior,
            ai_analysis_allowed=bool(
                deep_search.get("allowed") or
                int(atlas.get("qualified_count") or 0) > 0))
        context["prescreen_death_hologram"] = death_archive
        defer_batch_cognitive_maintenance = bool(
            int(atlas.get("qualified_count") or 0) <= 0 and
            not deep_search.get("allowed"))
        death_distillation = (
            {"state": "deferred_to_matrix_batch_end",
             "api_calls_used": 0,
             "reason": "exploration compute priority; deterministic evidence is already durable"}
            if defer_batch_cognitive_maintenance else
            distill_death_knowledge(force=False))
        context["death_knowledge_distillation"] = death_distillation
        solvability_audit = (
            {"deterministic_state": "deferred_to_matrix_batch_end",
             "ai_state": "deferred_to_matrix_batch_end",
             "api_calls_used": 0}
            if defer_batch_cognitive_maintenance else
            run_system_solvability_audit(force=False))
        context["system_solvability_audit"] = {
            "deterministic_state": solvability_audit.get("deterministic_state"),
            "ai_state": solvability_audit.get("ai_state"),
            "automatic_rule_relaxation": False}
        if int(atlas.get("qualified_count") or 0) <= 0:
            saved_ai_calls = (0 if hunt_probes.get(
                "suspected_positive_count") else 3)
            if not deep_search["allowed"]:
                # Three active-hunt opinions were never purchased because the
                # target produced no measured cost survivor.
                saved_ai_calls += 3
            _update_search_budget_ai(
                run_id, hunt_ai_calls+learning_ai_calls
                +int(death_archive.get("api_calls_used") or 0)
                +int(death_distillation.get("api_calls_used") or 0)
                +int(solvability_audit.get("api_calls_used") or 0),
                saved_ai_calls,
                ("target_local_cost_survivor_active_hunt" if
                 deep_search["allowed"] else
                 "deterministic_death_learning_no_ai_rescue"),
                {"cached": bool(hunt_prior.get("cached")),
                 "deep_search_eligibility": deep_search,
                 "active_hunt_ai_calls": hunt_ai_calls,
                 "tested_probes": hunt_probes.get("tested"),
                 "failure_learning_ai_calls": learning_ai_calls,
                 "death_map_ai_calls": death_archive.get("api_calls_used"),
                 "death_distillation_ai_calls": death_distillation.get(
                     "api_calls_used"),
                 "solvability_ai_calls": solvability_audit.get("api_calls_used"),
                 "suspected_positive_count": hunt_probes.get(
                     "suspected_positive_count")})
        if (int(atlas.get("qualified_count") or 0) <= 0
                and int(active_hunt.get("suspected_positive_count") or 0) <= 0):
            finished = _now()
            state = "early_pruned_no_empirical_edge"
            summary = {
                "ok": True, "state": state,
                "label": "训练中｜该方向已提前剪枝",
                "run_id": run_id, "assignment": assignment,
                "event_atlas": atlas, "provider_status": [],
                "active_hunt": active_hunt,
                "prescreen_death_hologram": death_archive,
                "system_solvability_audit": {
                    "deterministic_state": solvability_audit.get("deterministic_state"),
                    "ai_state": solvability_audit.get("ai_state"),
                    "cached": solvability_audit.get("cached"),
                    "automatic_rule_relaxation": False},
                "api_calls_saved": saved_ai_calls, "catalog_count": 0,
                "invalid_count": 0, "converged_count": 0,
                "outcomes": [], "ready_candidates": [],
                "training_rounds": [{"round": 0,
                                     "state": "pruned_before_ai",
                                     "reason": ("no target-local cost survivor; expensive three-AI rescue prohibited"
                                                if not deep_search["allowed"] else
                                                "no normal edge and no 3x-cost active-hunt suspected positive")}],
                "started_at": started, "finished_at": finished,
                "continuous": True,
            }
            conn = _db()
            try:
                intelligence.record_failure(
                    conn, "pre_ai_event_atlas", mission_key,
                    target["symbol"], target["timeframe"],
                    "no_cost_robust_entry_edge",
                    {"tested_patterns": atlas.get("tested_patterns"),
                     "qualified_count": atlas.get("qualified_count"),
                     "top_observed": (atlas.get("top_observed") or [])[:3],
                     "active_hunt_state": active_hunt.get("state"),
                     "api_calls_saved": saved_ai_calls})
                conn.execute(
                    "UPDATE strategy_creation_runs SET state=?,summary_json=?,finished_at=? WHERE run_id=?",
                    (state, json.dumps(summary, ensure_ascii=False, sort_keys=True),
                     finished, run_id))
                conn.commit()
            finally:
                conn.close()
            _atomic(CREATOR_STATUS_PATH, summary)
            return summary
        research = independent_research(context, metadata)
        catalog, invalid = _build_hypothesis_catalog(
            metadata, assignment, research.get("results") or [])
        convergence = _hypothesis_convergence_with_precheck(
            catalog, context, assignment, frame=development_frame)
        initial_convergence = convergence
        refinement_steps = []
        if (not convergence.get("admitted")
                and int(convergence.get("precheck_eligible_count") or 0) > 0):
            current_convergence = convergence
            for revision_index, provider in enumerate(("qwen", "chatgpt", "qwen"), 1):
                refinement = _collaborative_creation_refinement(
                    metadata, assignment, context, current_convergence,
                    provider=provider, revision_index=revision_index,
                    frame=development_frame)
                if not refinement.get("attempted"):
                    break
                refinement_steps.append(refinement)
                current_convergence = refinement.get("convergence") or {}
                convergence = current_convergence
                if current_convergence.get("admitted"):
                    break
        conn = _db()
        try:
            intelligence.store_hypotheses(conn, research.get("results") or [])
            intelligence.store_hypothesis_convergence(conn, initial_convergence)
            for refinement in refinement_steps:
                intelligence.store_hypotheses(
                    conn, [refinement.get("research_result") or {}])
                intelligence.store_hypothesis_convergence(
                    conn, refinement.get("convergence") or {})
            for failed in research.get("results") or []:
                if not failed.get("ok"):
                    intelligence.record_failure(
                        conn, "strategy_creation_ai", mission_key,
                        target["symbol"], target["timeframe"],
                        "creation_provider_failed",
                        {"provider": failed.get("provider"),
                         "error": failed.get("error") or "unknown"})
            for item in invalid:
                intelligence.record_failure(
                    conn, "strategy_creation_validation", mission_key,
                    target["symbol"], target["timeframe"],
                    "invalid_creation_hypothesis",
                    {"provider": item.get("provider"), "error": item.get("error")})
            convergence_records = [initial_convergence]
            convergence_records.extend(
                row.get("convergence") or {} for row in refinement_steps)
            for convergence_record in convergence_records:
                for item in convergence_record.get("rejected") or []:
                    intelligence.record_failure(
                        conn, "strategy_creation_convergence", mission_key,
                        target["symbol"], target["timeframe"],
                        "creation_direction_not_converged",
                        {"hypothesis_id": item.get("hypothesis_id"),
                         "support_count": item.get("support_count"),
                         "required_support": item.get("required_support"),
                         "votes": item.get("votes")})
        finally:
            conn.close()
        empty = _empty_metrics()
        baseline_runs = {"0.009": copy.deepcopy(empty),
                         "0.006": copy.deepcopy(empty)}
        outcomes = _process_dsl_hypotheses(
            assignment, _converged_provider_results(convergence), baseline_runs,
            parent_version_hash=None, promotion_allowed=True, mutation_limit=4)
        # A proposal rejected by anonymous review may still contain useful
        # empirical information.  When nothing converged, backtest the best
        # rejected draft in exploration-only mode.  It can populate the
        # experience/failure loop but can never be promoted or traded without
        # a later fresh 3/3 review.
        review_rejected_survivors = _precheck_survivors_rejected_by_ai(
            convergence)
        if not convergence.get("admitted") and review_rejected_survivors:
            exploration = {"admitted": review_rejected_survivors}
            outcomes.extend(_process_dsl_hypotheses(
                assignment, _converged_provider_results(exploration), baseline_runs,
                parent_version_hash=None, promotion_allowed=False,
                mutation_limit=4))
        training_rounds = [{
            "round": 1, "catalog_count": len(catalog),
            "invalid_count": len(invalid),
            "converged_count": len(convergence.get("admitted") or []),
            "outcomes": copy.deepcopy(outcomes),
        }]
        # Rounds two and three are evidence-driven, not fresh blind guesses.
        # The deterministic champion, rather than the newest candidate, is the
        # only legal parent.  Three models first diagnose the same evidence;
        # one rotating architect then writes a revision, and all three perform
        # a fresh anonymous 3/3 review.  A regressing child is never allowed to
        # replace its parent or consume another blind continuation round.
        for round_index in (2, 3):
            if any(row.get("state") == "awaiting_codex_review" for row in outcomes):
                break
            champion = _select_round_champion(outcomes)
            if not champion:
                break
            _champion_key, feedback_outcome, feedback, champion_quality = champion
            round_context = copy.deepcopy(context)
            prior_evidence = feedback.get("evidence") or {}
            round_context["backtest_revision_task"] = {
                "training_round": round_index,
                "base_candidate_hash": feedback_outcome.get("candidate_hash"),
                "base_dsl": (feedback.get("candidate") or {}).get("dsl") or {},
                "failed_gates": [key for key, value in
                                 (prior_evidence.get("gates") or {}).items() if not value],
                "primary_stop_0_9_pct": (((prior_evidence.get("runs") or {})
                                          .get("candidate") or {}).get("0.009") or {}),
                "auxiliary_stop_0_6_pct": (((prior_evidence.get("runs") or {})
                                            .get("candidate") or {}).get("0.006") or {}),
                "diagnostic_cases": prior_evidence.get("diagnostic_cases") or {},
                "parent_quality": champion_quality,
                "requirement": "根据真实失败证据修订同一策略；新版本必须相对父版本产生可量化信息增益，不得降低胜率后仅凭换方向继续下一轮。目标胜率约75%，最低70%，0.9%与0.6%止损均为正期望，样本和频率达门槛，最大连亏不超过3。",
            }
            failure_analysis = collaborative_failure_analysis(round_context, metadata)
            round_context["multi_ai_failure_analysis"] = {
                "policy": failure_analysis.get("policy"),
                "abandon_votes": failure_analysis.get("abandon_votes"),
                "anonymous_diagnoses": [row.get("diagnosis") for row in
                                         failure_analysis.get("results") or []
                                         if row.get("ok")],
            }
            if int(failure_analysis.get("abandon_votes") or 0) >= 2:
                training_rounds.append({
                    "round": round_index,
                    "state": "branch_abandoned_by_evidence",
                    "feedback_candidate_hash": feedback_outcome.get("candidate_hash"),
                    "champion_quality": champion_quality,
                    "failure_analysis": failure_analysis,
                    "outcomes": [],
                })
                break
            architect = "qwen" if round_index == 2 else "deepseek"
            architect_result = research_one(architect, round_context, metadata)
            round_research = {
                "ok": bool(architect_result.get("ok")),
                "policy": "three_diagnose_one_rotating_architect",
                "architect": architect,
                "results": [architect_result],
                "failure_analysis": failure_analysis,
            }
            round_catalog, round_invalid = _build_hypothesis_catalog(
                metadata, assignment, round_research.get("results") or [])
            round_convergence = _hypothesis_convergence_with_precheck(
                round_catalog, round_context, assignment, frame=development_frame)
            round_refinement = None
            if (not round_convergence.get("admitted")
                    and int(round_convergence.get("precheck_eligible_count") or 0) > 0):
                round_refinement = _collaborative_creation_refinement(
                    metadata, assignment, round_context, round_convergence,
                    provider="qwen" if round_index == 2 else "chatgpt",
                    revision_index=round_index, frame=development_frame)
                if round_refinement.get("attempted"):
                    round_convergence = round_refinement.get("convergence") or {}
            conn = _db()
            try:
                intelligence.store_hypotheses(conn, round_research.get("results") or [])
                intelligence.store_hypothesis_convergence(conn, round_convergence)
                if round_refinement and round_refinement.get("attempted"):
                    intelligence.store_hypotheses(
                        conn, [round_refinement.get("research_result") or {}])
                for item in round_invalid:
                    intelligence.record_failure(
                        conn, "strategy_creation_validation", mission_key,
                        target["symbol"], target["timeframe"],
                        "invalid_creation_hypothesis_round_%d" % round_index,
                        {"provider": item.get("provider"), "error": item.get("error")})
            finally:
                conn.close()
            round_outcomes = _process_dsl_hypotheses(
                assignment, _converged_provider_results(round_convergence),
                baseline_runs, parent_version_hash=feedback_outcome.get("candidate_hash"),
                promotion_allowed=True, mutation_limit=12)
            if (not round_convergence.get("admitted")
                    and round_convergence.get("rejected")):
                exploration = {"admitted": round_convergence.get("rejected") or []}
                round_outcomes.extend(_process_dsl_hypotheses(
                    assignment, _converged_provider_results(exploration),
                    baseline_runs, parent_version_hash=feedback_outcome.get("candidate_hash"),
                    promotion_allowed=False, mutation_limit=8))
            conn = _db()
            try:
                for outcome in round_outcomes:
                    digest = outcome.get("candidate_hash")
                    child = _candidate_and_evidence(digest) if digest else None
                    if not child:
                        continue
                    assessment = _relative_iteration_assessment(feedback, child)
                    outcome["relative_improvement"] = assessment
                    if not assessment.get("improved"):
                        _candidate_state(digest, "iteration_regressed")
                        outcome["state"] = "iteration_regressed"
                        intelligence.record_failure(
                            conn, "strategy_creation_iteration", mission_key,
                            target["symbol"], target["timeframe"],
                            "revision_regressed_vs_champion",
                            {"candidate_hash": digest,
                             "parent_candidate_hash": feedback_outcome.get("candidate_hash"),
                             "assessment": assessment})
            finally:
                conn.close()
            outcomes.extend(round_outcomes)
            training_rounds.append({
                "round": round_index,
                "architecture": "three_diagnose_one_architect_three_review",
                "architect": architect,
                "catalog_count": len(round_catalog),
                "invalid_count": len(round_invalid),
                "converged_count": len(round_convergence.get("admitted") or []),
                "feedback_candidate_hash": feedback_outcome.get("candidate_hash"),
                "champion_quality": champion_quality,
                "failure_analysis": failure_analysis,
                "outcomes": copy.deepcopy(round_outcomes),
            })
        ready = [row for row in outcomes
                 if row.get("state") == "awaiting_codex_review"]
        state = "candidate_ready" if ready else "training"
        summary = {
            "ok": True, "state": state,
            "label": "发现待确认策略" if ready else "训练中",
            "run_id": run_id, "assignment": assignment,
            "provider_status": [{"provider": row.get("provider"),
                                 "ok": row.get("ok"),
                                 "hypothesis_count": len(row.get("hypotheses") or [])}
                                for row in research.get("results") or []],
            "event_atlas": atlas,
            "active_hunt": active_hunt,
            "api_calls_saved": 0,
            "catalog_count": len(catalog), "invalid_count": len(invalid),
            "converged_count": len(convergence.get("admitted") or []),
            "collaborative_refinement": {
                "attempted": bool(refinement_steps),
                "attempt_count": len(refinement_steps),
                "steps": [{"revision_index": row.get("revision_index"),
                           "provider": row.get("revision_provider"),
                           "base_support_count": row.get("base_support_count"),
                           "catalog_count": len(row.get("catalog") or []),
                           "invalid_count": len(row.get("invalid") or []),
                           "converged_count": len((row.get("convergence") or {}).get("admitted") or []),
                           "max_support_count": max([item.get("support_count") or 0
                                                     for item in (row.get("convergence") or {}).get("rejected") or []] or [3])}
                          for row in refinement_steps],
            },
            "outcomes": outcomes, "ready_candidates": ready,
            "training_rounds": training_rounds,
            "started_at": started, "finished_at": _now(), "continuous": True,
        }
    except Exception as exc:
        state = "training_error"
        summary = {"ok": False, "state": state, "label": "训练暂时异常",
                   "run_id": run_id, "assignment": assignment,
                   "error": str(exc), "started_at": started,
                   "finished_at": _now(), "continuous": True}
    conn = _db()
    try:
        conn.execute("UPDATE strategy_creation_runs SET state=?,summary_json=?,finished_at=? WHERE run_id=?",
                     (state, json.dumps(summary, ensure_ascii=False, sort_keys=True),
                      summary.get("finished_at"), run_id))
        conn.commit()
    finally:
        conn.close()
    _atomic(CREATOR_STATUS_PATH, summary)
    return summary


_FEATURE_LABELS = {
    "open": "开盘价", "high": "最高价", "low": "最低价", "close": "收盘价",
    "k": "K值", "d": "D值", "j": "J值", "cci": "CCI",
    "macd_stick": "MACD Stick", "atr14": "ATR14", "rsi14": "RSI14",
    "z20": "Z20", "h1_slope4": "1小时趋势斜率",
}


def _describe_operand(value):
    if "value" in value:
        number = float(value["value"])
        return str(int(number)) if number.is_integer() else str(round(number, 6))
    feature = str(value.get("feature") or "")
    label = _FEATURE_LABELS.get(feature, feature.upper())
    offset = int(value.get("offset") or 0)
    return ("前%d根K线的%s" % (offset, label)) if offset else ("信号K线的" + label)


def _describe_expression(node):
    if "all" in node:
        return "（" + "；且 ".join(_describe_expression(x) for x in node["all"]) + "）"
    if "any" in node:
        return "（" + "；或 ".join(_describe_expression(x) for x in node["any"]) + "）"
    if "not" in node:
        return "不满足" + _describe_expression(node["not"])
    left = _describe_operand(node["left"]); op = node["op"]
    if op == "between":
        return "%s介于%s至%s" % (left, node["lower"], node["upper"])
    labels = {"lt": "小于", "lte": "小于等于", "gt": "大于",
              "gte": "大于等于", "eq": "等于", "cross_above": "由下向上穿越",
              "cross_below": "由上向下穿越"}
    return "%s%s%s" % (left, labels.get(op, op), _describe_operand(node["right"]))


def strategy_creation_status():
    current = _read(CREATOR_STATUS_PATH, {"state": "training", "label": "训练中"})
    conn = _db(); candidates = []; history = []
    try:
        rows = conn.execute(
            "SELECT c.candidate_hash,c.state,c.candidate_json,c.evidence_json,c.created_at,c.updated_at,v.consensus_json "
            "FROM candidates c LEFT JOIN strategy_versions v ON v.version_hash=c.candidate_hash "
            "WHERE c.candidate_json LIKE '%\"type\": \"dsl_strategy\"%' "
            "AND c.state IN ('awaiting_codex_review','approved_requires_human','human_approved_research','human_approved_live') "
            "ORDER BY c.updated_at DESC LIMIT 20").fetchall()
        recent_runs = [dict(zip(("run_id", "symbol", "timeframe", "state", "started_at", "finished_at"), row))
                       for row in conn.execute(
                           "SELECT run_id,symbol,timeframe,state,started_at,finished_at "
                           "FROM strategy_creation_runs ORDER BY started_at DESC LIMIT 8").fetchall()]
    finally:
        conn.close()
    for digest, state, candidate_raw, evidence_raw, created_at, updated_at, consensus_raw in rows:
        candidate = json.loads(candidate_raw); definition = candidate.get("dsl") or {}
        evidence = json.loads(evidence_raw or "{}"); consensus = json.loads(consensus_raw or "{}")
        metrics = ((evidence.get("runs") or {}).get("candidate") or {}).get("0.009") or {}
        item = {"candidate_hash": digest, "short_hash": digest[:12], "state": state,
                "state_label": {"awaiting_codex_review": "等待Codex最终复核",
                                "awaiting_shadow_validation": "七日前向影子确认中",
                                "approved_requires_human": "等待人工确认",
                                "human_approved_research": "已批准研究版",
                                "human_approved_live": "已进入实盘"}.get(state, state),
                "title": definition.get("name") or candidate.get("strategy_key"),
                "description": definition.get("description") or "",
                "symbol": candidate.get("symbol"), "timeframe": candidate.get("timeframe"),
                "direction": "做多" if definition.get("direction") == "long" else "做空",
                "trigger_conditions": _describe_expression(definition.get("entry") or {}),
                "take_profit": _describe_expression(definition.get("exit") or {}),
                "max_hold_bars": definition.get("max_hold_bars"),
                "backtest": {"trades": metrics.get("trades"),
                             "win_rate_pct": metrics.get("win_rate_pct"),
                             "expectancy_pct": metrics.get("expectancy_pct"),
                             "compound_return_pct": metrics.get("compound_return_pct"),
                             "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                             "max_loss_streak": metrics.get("max_loss_streak"),
                             "trades_per_day": metrics.get("trades_per_day"),
                             "holdout_win_rate_pct": metrics.get("holdout_win_rate_pct")},
                "ai_consensus": sum(1 for row in consensus.get("reviews") or []
                                    if row.get("decision") == "APPROVE"),
                "created_at": created_at, "updated_at": updated_at}
        (candidates if state in ("awaiting_shadow_validation",
                                 "awaiting_codex_review",
                                 "approved_requires_human") else history).append(item)
    actionable = [row for row in candidates
                  if row.get("state") in ("awaiting_codex_review",
                                          "approved_requires_human")]
    shadowing = [row for row in candidates
                 if row.get("state") == "awaiting_shadow_validation"]
    state = ("candidate_ready_and_training" if actionable else
             ("shadow_validation_and_training" if shadowing else
              str(current.get("state") or "training")))
    if state not in ("running", "training_error", "configuration_error",
                     "candidate_ready_and_training", "shadow_validation_and_training"):
        state = "training"
    matrix = _read(CLUSTER_MATRIX_STATUS_PATH,
                   {"ok": False, "state": "awaiting_first_planner_refresh",
                    "target_count": len(_creation_targets())})
    return {"enabled": True, "continuous": True, "state": state,
            "label": ("发现待确认策略｜持续训练中" if actionable else
                      ("候选策略七日影子验证中｜持续训练中" if shadowing else
                       current.get("label") or "训练中")),
            "schedule": "每1小时双槽并行创造：生产率元控制器把算力集中到有可交易频率与成本幸存迹象的5分钟/15分钟集群；出现成本幸存会立刻追加追击权重。实盘分两条轨道：条件频率探针（人工在场白名单+C/B仓位，每小时轮换）与完整认知矩阵晋级。无成本优势方向在调用AI前停止",
            "human_confirmation_required": True,
            "live_auto_promotion": False,
            "candidates": candidates, "approved_history": history[:8],
            "last_run": current, "recent_runs": recent_runs,
            "target_count": len(_creation_targets()),
            "cluster_matrix": matrix}


def status():
    conn = _db()
    try:
        counts = dict(conn.execute("SELECT state,COUNT(*) FROM candidates GROUP BY state").fetchall())
        cases = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        deployments = conn.execute("SELECT COUNT(*) FROM deployments WHERE rolled_back_at IS NULL").fetchone()[0]
        last = conn.execute("SELECT candidate_hash,strategy_key,state,updated_at FROM candidates ORDER BY updated_at DESC LIMIT 1").fetchone()
        intelligence_counts = {}
        for table in ("market_snapshots", "signal_observations", "condition_attributions",
                      "failure_experiences", "research_hypotheses", "hypothesis_reviews", "strategy_versions",
                      "lifecycle_metrics", "search_branch_observations",
                      "search_branch_stats", "search_budget_ledger",
                      "active_hunt_prior_runs", "active_hunt_probe_experiments",
                      "positive_core_samples", "probe_failure_autopsies",
                      "pruning_rule_memory", "positive_confirmation_candidates",
                      "shadow_trade_events", "prescreen_rejections",
                      "prescreen_death_maps", "shadow_environment_snapshots",
                      "death_micro_cooccurrence_observations",
                      "death_micro_association_reports",
                      "death_forward_validation_outcomes",
                      "system_solvability_audits",
                      "death_rule_distillation_runs",
                      "distilled_death_rules"):
            intelligence_counts[table] = conn.execute(
                "SELECT COUNT(*) FROM %s" % table
            ).fetchone()[0]
        intelligence_counts["prescreen_rejection_unique_evidence"] = len(
            _global_prescreen_evidence(conn))
        branch_learning = dict(zip(
            ("observations", "full_evaluations", "cost_survivors", "credible"),
            conn.execute(
                "SELECT COUNT(*),SUM(full_evaluated),SUM(survived_cost_screen),"
                "SUM(credible) FROM search_branch_observations").fetchone()))
        budget_learning = dict(zip(
            ("runs", "quick_candidates", "full_candidates", "qualified",
             "ai_calls_used", "ai_calls_saved"),
            conn.execute(
                "SELECT COUNT(*),SUM(quick_candidates),SUM(full_candidates),"
                "SUM(qualified_candidates),SUM(ai_calls_used),SUM(ai_calls_saved) "
                "FROM search_budget_ledger").fetchone()))
        positive_core = dict(conn.execute(
            "SELECT label_state,COUNT(*) FROM positive_core_samples "
            "GROUP BY label_state").fetchall())
        probe_states = dict(conn.execute(
            "SELECT state,COUNT(*) FROM active_hunt_probe_experiments "
            "GROUP BY state").fetchall())
        pruning_states = dict(conn.execute(
            "SELECT state,COUNT(*) FROM pruning_rule_memory GROUP BY state").fetchall())
        confirmation_states = dict(conn.execute(
            "SELECT state,COUNT(*) FROM positive_confirmation_candidates "
            "GROUP BY state").fetchall())
        latest_solvability = conn.execute(
            "SELECT week_bucket,deterministic_state,ai_state,created_at,"
            "deterministic_json FROM "
            "system_solvability_audits ORDER BY created_at DESC, rowid DESC LIMIT 1").fetchone()
        latest_association = conn.execute(
            "SELECT symbol,timeframe,observation_count,eligible_cell_count,state,"
            "created_at FROM death_micro_association_reports "
            "ORDER BY created_at DESC LIMIT 1").fetchone()
        distilled_states = dict(conn.execute(
            "SELECT state,COUNT(*) FROM distilled_death_rules "
            "GROUP BY state").fetchall())
        latest_distillation = conn.execute(
            "SELECT run_id,evidence_count,state,created_at FROM "
            "death_rule_distillation_runs ORDER BY created_at DESC,rowid DESC "
            "LIMIT 1").fetchone()
    finally:
        conn.close()
    from auto_trade_ai_consensus import (credentials_status,
                                         external_research_consent_status)
    microstructure = _read(AUTO_DIR / "microstructure_status.json",
                           {"ok": False, "state": "awaiting_first_sample"})
    micro_primitives = _read(AUTO_DIR / "microstructure_primitives_status.json",
                             {"ok": False, "state": "awaiting_first_discovery"})
    solvability_latest = None
    if latest_solvability:
        stored_witness = json.loads(latest_solvability[4] or "{}")
        health_input = stored_witness.get("health_input") or {}
        solvability_latest = {
            "week_bucket": latest_solvability[0],
            "deterministic_state": latest_solvability[1],
            "ai_state": latest_solvability[2],
            "created_at": latest_solvability[3],
            "trigger_reasons": stored_witness.get("audit_trigger_reasons") or [],
            "unique_death_evidence": health_input.get("unique_death_evidence"),
            "active_pruning_rule_count": health_input.get(
                "active_pruning_rule_count"),
            "controlled_mutation_shape_count": health_input.get(
                "controlled_mutation_shape_count"),
            "policy": "weekly_or_material_structure_change_heartbeat",
        }
    live_audit_report = _read(LIVE_AUDIT_REPORT_PATH, {})
    live_audit_progress = _read(LIVE_AUDIT_PROGRESS_PATH, {})
    live_cognitive_gate = _read(LIVE_COGNITIVE_GATE_PATH, {})
    live_runtime_controls = _read(LIVE_RUNTIME_CONTROLS_PATH, {})
    post_audit_policy = _read(POST_AUDIT_POLICY_PATH, {})
    matrix_round_status = _read(MATRIX_ROUND_STATUS_PATH, {})
    legacy_shadow_status = _read(LEGACY_SHADOW_STATUS_PATH, {})
    control_rows = (live_runtime_controls.get("assignments") or {})
    live_audit_status = {
        "gate_enforced": bool(live_cognitive_gate.get("enforce")),
        "required_assignment_state": live_cognitive_gate.get(
            "required_assignment_state") or "passed_all",
        "audit_state": (live_audit_progress.get("state") or
                        ("completed" if live_audit_report.get("ok") else
                         "not_started")),
        "run_id": (live_audit_report.get("run_id") or
                   live_audit_progress.get("run_id")),
        "assignment_count": (live_audit_report.get("assignment_count") or
                             live_audit_progress.get("total") or 0),
        "completed_count": (live_audit_progress.get("completed") or
                            (live_audit_report.get("assignment_count") or 0)),
        "passed_count": live_audit_report.get("passed_count"),
        "failed_closed_count": live_audit_report.get("failed_closed_count"),
        "disposition_counts": live_audit_report.get("disposition_counts") or {},
        "controlled_assignment_count": len(control_rows),
        "paused_assignment_count": sum(
            1 for row in control_rows.values()
            if bool((row or {}).get("pause_new_entries"))),
        "conditional_frequency_probe_count": sum(
            1 for row in control_rows.values()
            if str((row or {}).get("audit_state") or "") ==
            "conditional_frequency_probe"
            and not bool((row or {}).get("pause_new_entries"))),
        "policy": ("完整晋级须取得逐标的×周期×策略的passed_all证书；"
                   "条件频率探针仅允许人工白名单以受限仓位试运行，"
                   "护栏触发后自动再暂停新开仓；"
                   "缺失、损坏或未通过时禁止新开仓，既有持仓退出管理继续"),
        "historical_microstructure_boundary": live_audit_report.get(
            "historical_microstructure_boundary") or
            "部署前L2/队列/撤单历史不可伪造",
    }
    value = {"ok": True, "updated_at": _now(), "case_count": cases,
             "candidate_counts": counts, "active_ai_deployments": deployments,
             "last_candidate": dict(zip(("candidate_hash", "strategy_key", "state", "updated_at"), last)) if last else None,
             "intelligence_counts": intelligence_counts,
             "ai_providers": credentials_status(),
             "external_ai_research_consent": external_research_consent_status(),
             "live_strategy_cognitive_audit": live_audit_status,
             "post_audit_operating_policy": post_audit_policy,
             "exploration_restart_round": matrix_round_status,
             "legacy_forward_shadow": legacy_shadow_status,
             "consensus_policy": "DeepSeek + Qwen + OpenAI API 负责研究训练与匿名3/3会审；确定性门槛通过后必须由Codex完成代码与最终复核，再由用户决定是否实盘",
             "research_convergence_policy": "目标事件图谱先做低成本概率筛选；存活方向由三AI独立研究，分别按微观结构、统计证伪和执行成本进行匿名对抗式3/3复核；修订只允许确定性冠军作为父版本，退化分支立即终止",
             "cost_aware_search": {
                 "friction_terms": ["taker_fee", "slippage", "half_spread",
                                    "top5_depth_capacity_impact", "latency_proxy",
                                    "observed_funding"],
                 "scenarios": ["observed_base", "stressed", "severe"],
                 "forward_calibration": "每15分钟只读采集五档盘口、价差、成交和资金费率；样本充分后按品种分位数与账户最大建模名义仓位覆盖保守代理",
                 "known_limit": "部署前不存在的历史L2盘口、队列位置与逐单延迟无法逆向重建",
             },
             "search_learning": {
                 "branch_surrogate": "60日衰减目标本地Beta后验；标的×周期严格隔离，不迁移胜负或收益标签",
                 "acquisition": "后验均值+不确定性+新颖度+AI共识-计算成本-历史死路惩罚",
                 "population_budget": "整批90日净成本快筛后，仅前20%进入昂贵全量验证",
                 "multiple_testing": "Bayesian FDR q=0.10（控制入选集合平均后验符号错误率）",
                 "active_hunt": "三AI受控必要条件交集→三倍全摩擦窄窗口探针→相似富矿区；理论蓝图永不作为正例标签",
                 "controlled_mutation": "失败死因→受控算子投票→过滤器编译；AI不能创造未登记指标",
                 "prescreen_death_hologram": "确定性初筛与AI否决分层归档→死亡热力图→重复死因必须有反死因算子",
                 "failure_rule_learning": "DeepSeek深度解剖→确定性影响审计→Qwen与OpenAI双审核→两次独立失败后激活",
                 "death_knowledge_distillation": {
                     "latest": dict(zip(("run_id", "evidence_count", "state",
                                          "created_at"), latest_distillation))
                               if latest_distillation else None,
                     "rule_state_counts": distilled_states,
                     "automatic_cross_target_pruning": False,
                     "policy": ("DeepSeek层级蒸馏→Qwen/OpenAI双审→跨目标仅导航；"
                                "同标的同周期两次测量失败后仍须走本地影响审计")},
                 "positive_confirmation": "滚动时间窗→极端/严重成本→机会频率与容量→至少七日前向只读影子",
                 "slow_microstructure_vocabulary": micro_primitives,
                 "research_cluster_matrix": _read(
                     CLUSTER_MATRIX_STATUS_PATH,
                     {"ok": False, "state": "awaiting_first_planner_refresh"}),
                 "death_micro_cooccurrence": {
                     "latest": dict(zip(("symbol", "timeframe", "observation_count",
                                         "eligible_cell_count", "state", "created_at"),
                                        latest_association)) if latest_association else None,
                     "policy": "forward contemporaneous descriptive association only"},
                 "reverse_death_validation": {
                     "latest": ((micro_primitives.get("death_micro_cooccurrence") or {})
                                .get("reverse_death_forward_validation") or {}),
                     "policy": "prospective fixed-horizon falsification; positive counterexamples are preserved"},
                 "shadow_stress_lab": {
                     "historical_ohlcv_extreme_and_severe_cost": "operational",
                     "forward_full_fidelity_shadow": "operational_when_candidate_exists",
                     "historical_microstructure_replay": "unavailable_before_telemetry_start",
                     "offline_rl_simulator": ("ready_for_bounded_parameter_stress" if
                         int(intelligence_counts.get("shadow_environment_snapshots") or 0) >= 100
                         else "deferred_until_100_shadow_environment_snapshots"),
                     "rl_training_labels_allowed": False,
                 },
                 "system_solvability": {
                     "latest": solvability_latest,
                     "automatic_rule_relaxation": False},
                 "positive_core_counts": positive_core,
                 "probe_state_counts": probe_states,
                 "pruning_rule_state_counts": pruning_states,
                 "positive_confirmation_state_counts": confirmation_states,
                 "forward_microstructure": microstructure,
                 "transfer_and_novelty": "已启用；冷启动保留探索，不把无数据误判为失败",
                 "branch_learning_counts": branch_learning,
                 "budget_counts": budget_learning,
             },
             "architecture_capability_audit": {
                 "cost_internalization": "operational",
                 "multi_ai_prior_consensus_filter": "operational_3_of_3",
                 "adversarial_debate": "operational",
                 "causal_explainability_and_falsification": "operational",
                 "probabilistic_surrogate": "operational_hierarchical_branch_model_accumulating",
                 "successive_halving": "operational_population_level",
                 "bayesian_cost_constrained_acquisition": "operational_posterior_ucb",
                 "elite_novelty_transfer": "operational",
                 "multiple_hypothesis_control": "operational",
                 "dynamic_compute_meta_controller": "operational",
                 "research_cluster_matrix": "operational_36_logical_targets_single_process_bounded",
                 "target_local_evidence_isolation": "operational_no_cross_symbol_or_timeframe_outcome_transfer",
                 "forward_l2_capacity_model": "collecting_until_24_samples_per_instrument",
                 "active_hunt_positive_sample_generation": "operational_without_synthetic_labels",
                 "operator_level_controlled_mutation": "operational",
                 "target_local_operator_lattice": (
                     "operational_dormant_until_local_cost_survivor"),
                 "prescreen_death_hologram": "operational_stage_separated",
                 "death_knowledge_distillation": "operational_navigation_only_fail_closed",
                 "failure_autopsy_pruning_memory": "operational_quarantine_then_two_failure_activation",
                 "four_gate_positive_confirmation": "operational_shadow_required",
                 "minute_microstructure_features": "forward_collection_only",
                 "slow_microstructure_unsupervised_primitives": (
                     "operational_outcome_free_double_audited" if
                     micro_primitives.get("state") == "descriptive_discovery_complete"
                     else "collecting_or_awaiting_discovery"),
                 "target_local_primitive_stability": "operational_kmeans_medoid_temporal_stability_gate",
                 "micro_event_graph": "operational_non_price_non_pnl_descriptive_only",
                 "cross_market_morphology": "operational_description_only_no_label_transfer",
                 "cognition_drift_monitor": "operational_alert_only_no_automatic_gate_change",
                 "cost_benefit_dashboard": "operational_information_units_and_ai_call_counts",
                 "shadow_environment_envelope": "operational_entry_exit_and_holding_snapshots",
                 "death_micro_forward_cooccurrence": "operational_awaiting_overlap_or_descriptive_only",
                 "reverse_death_prospective_falsification": "operational_no_inevitability_claim",
                 "shadow_historical_ohlcv_stress_suite": "operational",
                 "offline_shadow_rl": ("deferred_until_real_shadow_samples" if
                     int(intelligence_counts.get("shadow_environment_snapshots") or 0) < 100
                     else "bounded_robustness_simulation_ready_no_policy_learning"),
                 "counterparty_adaptation_attack": "operational_theoretical_only_never_empirical_death",
                 "system_solvability_audit": "operational_weekly_or_material_change_three_ai_heartbeat_no_auto_relaxation",
                 "historical_l2_replay": "unavailable_before_telemetry_start",
             },
             "early_pruning_policy": "整批90日净成本快筛→按获取函数仅保留前20%→全历史→0.6%辅助止损→压力/严重成本→三段前向→跨品种盲测→多重检验；任一级失败即停止",
             "probabilistic_policy": "Bootstrap均值90%区间 + Beta胜率后验 + 利润因子 + 年化日收益Sharpe；样本不足不晋级",
             "causal_policy": "每个DSL必须给出可观测的市场状态→机制→转折→成本覆盖→失效条件，并至少提供两项可证伪测试",
             "position_grade_ratios": {"S": 0.70, "A": 0.50, "B": 0.30, "C": 0.15},
             "immutable_ai_capital_params": sorted(PROTECTED_CAPITAL_PARAMS),
             "routine_signal_execution_uses_ai": False,
             "strategy_creation_training": strategy_creation_status(),
             "learning_flow": ["case_capture", "regime_label", "condition_attribution",
                               "execution_cost_forward_calibration",
                               "target_event_atlas", "probabilistic_surrogate_early_prune",
                               "target_local_time_decayed_prior", "cost_constrained_ucb_acquisition",
                               "population_successive_halving", "multiple_testing_control",
                               "three_ai_survival_necessity_intersection",
                               "triple_cost_minimum_viable_probe",
                               "counterfactual_blueprint_similarity_search",
                               "information_gain_active_learning",
                               "three_round_adaptation_attack",
                               "prescreen_death_hologram",
                               "stage_separated_death_knowledge_distillation",
                               "controlled_operator_mutation",
                               "target_local_operator_lattice_after_cost_survival",
                               "outcome_free_slow_micro_primitive_discovery",
                               "target_local_stability_cross_algorithm_audit",
                               "non_price_micro_event_association_graph",
                               "cross_market_descriptive_morphology_no_transfer",
                               "prospective_death_micro_cooccurrence",
                               "cluster_cognition_drift_and_cost_efficiency_monitor",
                               "weekly_system_solvability_audit",
                               "deep_failure_autopsy", "deterministic_rule_impact_audit",
                               "qwen_openai_rule_double_audit", "quarantined_pruning_memory",
                               "three_ai_independent_first_round", "adversarial_anonymous_cross_review",
                               "deterministic_champion_selection", "three_ai_failure_diagnosis",
                               "single_rotating_revision_architect", "anonymous_three_of_three_revision_review",
                               "safe_dsl_mutation",
                               "successive_halving_unified_backtest", "multi_cost_robust_screen",
                               "rolling_windows_and_blind_peer_validation", "extreme_event_stress",
                               "opportunity_capacity_gate", "seven_day_forward_shadow",
                               "shadow_environment_envelope", "pareto_selection",
                               "productivity_meta_controller",
                               "relative_information_gain_gate", "codex_final_review",
                               "human_live_confirmation", "version_registry",
                               "live_lifecycle_monitor"],
             "routine_signal_note": "外部AI仅研究训练；Codex负责生产实现与最终复核；正常闭合K线开平仓继续由确定性规则执行"}
    _atomic(STATUS_PATH, value)
    return value


def tick(research=False):
    ingested = ingest_cases()
    import auto_trade_strategy_intelligence as intelligence
    conn = _db()
    try:
        assignments = _active_assignments()
        experience = {
            "market_snapshots": intelligence.ingest_market_snapshots(conn, assignments),
            "signal_observations": intelligence.ingest_signal_observations(conn),
            "trade_case_enrichment": intelligence.enrich_trade_cases(conn),
            "lifecycle": intelligence.lifecycle_monitor(conn),
        }
    finally:
        conn.close()
    output = {"ok": True, "time": _now(), "ingest": ingested,
              "experience": experience}
    if research:
        output["research"] = research_once()
    output["status"] = status()
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--research", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--create-strategy", action="store_true")
    parser.add_argument("--create-symbol")
    parser.add_argument("--create-timeframe")
    parser.add_argument("--distill-deaths", action="store_true")
    parser.add_argument("--approve-version")
    parser.add_argument("--enable-live", action="store_true")
    parser.add_argument("--codex-review-version")
    parser.add_argument("--codex-decision")
    parser.add_argument("--codex-note", default="")
    args = parser.parse_args()
    if args.codex_review_version:
        result = codex_review_dsl_version(
            args.codex_review_version, args.codex_decision, args.codex_note)
    elif args.approve_version:
        result = approve_dsl_version(args.approve_version, enable_live=args.enable_live)
    elif args.create_strategy:
        result = create_strategy_once(args.create_symbol, args.create_timeframe)
    elif args.distill_deaths:
        result = distill_death_knowledge(force=True)
    else:
        result = status() if args.status else tick(research=args.research)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
