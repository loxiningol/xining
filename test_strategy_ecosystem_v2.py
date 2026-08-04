# -*- coding: utf-8 -*-
"""Non-network safety tests for the strategy ecosystem deployment."""
from __future__ import print_function

import json
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path

import auto_trade_ai_consensus as ai
import auto_trade_execution_cost_calibrator as cost_calibrator
import auto_trade_formal_daemon as formal_daemon
import auto_trade_formal_notify as formal_notify
import auto_trade_formal_v6_executor as formal_executor
import auto_trade_microstructure_collector as micro_collector
import auto_trade_microstructure_primitives as micro_primitives
import auto_trade_shadow_validator as shadow_validator
import auto_trade_strategy_dsl as dsl
import auto_trade_strategy_ecosystem as ecosystem
import auto_trade_strategy_intelligence as intelligence


def check(value, message):
    if not value:
        raise AssertionError(message)


def test_catalog_and_convergence():
    parsed = ai._parse_content_json("{'hypotheses': [],}")
    check(parsed == {"hypotheses": []},
          "safe Python-style provider payload was not normalized")
    unsafe_blocked = False
    try:
        ai._parse_content_json("__import__('os').system('id')")
    except Exception:
        unsafe_blocked = True
    check(unsafe_blocked, "executable provider payload was not blocked")

    strategy = {
        "params": {"threshold": 5.0, "stop_loss_pct": 0.009},
        "param_meta": {"threshold": {"min": 0, "max": 10, "step": 1}},
    }
    assignment = {"strategy_key": "s", "symbol": "BTC-USDT-SWAP",
                  "timeframe": "1h"}
    results = [
        {"provider": "deepseek", "ok": True, "hypotheses": [
            {"kind": "parameter", "strategy_key": "s", "parameter": "threshold",
             "direction": 1, "step_count": 1}]},
        {"provider": "qwen", "ok": True, "hypotheses": [
            {"kind": "parameter", "strategy_key": "s", "parameter": "threshold",
             "direction": 1, "step_count": 2}]},
        {"provider": "chatgpt", "ok": True, "hypotheses": [
            {"kind": "parameter", "strategy_key": "s", "parameter": "threshold",
             "direction": -1, "step_count": 1}]},
    ]
    catalog, invalid = ecosystem._build_hypothesis_catalog(
        strategy, assignment, results
    )
    check(not invalid, "valid catalog unexpectedly rejected")
    positive = next(row for row in catalog if row["direction"] == 1)
    negative = next(row for row in catalog if row["direction"] == -1)

    def fake_review(name, batch_hash, public_catalog, context):
        votes = []
        for row in public_catalog:
            support = row["hypothesis_id"] == positive["hypothesis_id"] and name != "chatgpt"
            votes.append({"hypothesis_id": row["hypothesis_id"],
                          "decision": "SUPPORT" if support else "REJECT",
                          "reason": "unit-test"})
        return {"provider": name, "ok": True, "batch_hash": batch_hash,
                "votes": votes}

    original = ai.review_hypotheses_one
    ai.review_hypotheses_one = fake_review
    try:
        convergence = ai.hypothesis_convergence(catalog, {})
    finally:
        ai.review_hypotheses_one = original
    admitted = set(row["hypothesis_id"] for row in convergence["admitted"])
    rejected = set(row["hypothesis_id"] for row in convergence["rejected"])
    check(positive["hypothesis_id"] in admitted,
          "2-origin + 2/3-supported parameter was not admitted")
    check(negative["hypothesis_id"] in rejected,
          "single-origin parameter was not rejected")


def test_dsl_and_schema():
    definition = {
        "schema": dsl.SCHEMA, "key": "test_dsl", "name": "test",
        "direction": "long", "timeframe": "1h",
        "supported_instruments": ["BTC-USDT-SWAP"], "max_hold_bars": 8,
        "entry": {"all": [
            {"id": "close_above", "left": {"feature": "close"},
             "op": "gt", "right": {"feature": "ema6"}},
            {"id": "cci_floor", "left": {"feature": "cci"},
             "op": "gt", "right": {"value": 20}},
        ]},
        "exit": {"all": [
            {"id": "cci_exit", "left": {"feature": "cci"},
             "op": "gt", "right": {"value": 100},
             "role": "take_profit"},
        ]},
    }
    dsl.validate_strategy(definition)
    described = ecosystem._describe_expression(definition["entry"])
    check("收盘价" in described and "CCI" in described,
          "strategy creation rule description is incomplete")
    malicious = dict(definition); malicious["python"] = "import os"
    blocked = False
    try:
        dsl.validate_strategy(malicious)
    except dsl.DSLValidationError:
        blocked = True
    check(blocked, "executable DSL field was not blocked")
    check(len(dsl.mutate_strategy(definition, limit=10)) > 0,
          "safe DSL mutations were not generated")
    check(ecosystem._dsl_semantic_audit(definition)["passed"],
          "explicit take-profit semantics were rejected")
    ambiguous = json.loads(json.dumps(definition))
    ambiguous["exit"]["all"][0].pop("role")
    check(not ecosystem._dsl_semantic_audit(ambiguous)["passed"],
          "ambiguous strategy exit was allowed through semantic audit")
    promoted = ecosystem._promotion_definitions(
        definition, {}, True, mutation_limit=8)
    explored = ecosystem._promotion_definitions(
        definition, {}, False, mutation_limit=8)
    check(len(promoted) == 1 and
          dsl.executable_hash(promoted[0]) == dsl.executable_hash(definition),
          "promotable definition silently inherited an unreviewed mutation")
    check(len(explored) > 1,
          "exploration-only mutation path was accidentally disabled")
    slope_definition = json.loads(json.dumps(definition))
    slope_definition["entry"]["all"][1] = {
        "id": "h1_slope", "left": {"feature": "h1_slope4"},
        "op": "gt", "right": {"value": 0.0005},
    }
    slope_values = [
        row["entry"]["all"][1]["right"]["value"]
        for row in dsl.mutate_strategy(slope_definition, limit=4)
        if (row.get("origin") or {}).get("condition_id") == "h1_slope"
    ]
    check(0.0 in slope_values and 0.001 in slope_values,
          "h1_slope4 mutations did not use decimal-return scale")

    conn = sqlite3.connect(tempfile.mktemp(suffix=".db"))
    try:
        intelligence.ensure_schema(conn)
        names = set(row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall())
        for name in ("failure_experiences", "hypothesis_reviews",
                     "strategy_versions", "lifecycle_metrics",
                    ):
            check(name in names, "missing table: " + name)
    finally:
        conn.close()

    old_auto, old_db = ecosystem.AUTO_DIR, ecosystem.DB_PATH
    temp_root = Path(tempfile.mkdtemp())
    try:
        ecosystem.AUTO_DIR = temp_root
        ecosystem.DB_PATH = temp_root / "ecosystem.db"
        creation_conn = ecosystem._db()
        try:
            names = set(row[0] for row in creation_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall())
            check("strategy_creation_runs" in names,
                  "missing table: strategy_creation_runs")
            for name in ("active_hunt_prior_runs",
                         "active_hunt_probe_experiments",
                         "positive_core_samples", "probe_failure_autopsies",
                         "pruning_rule_memory", "positive_confirmation_candidates",
                         "shadow_trade_events", "prescreen_rejections",
                         "prescreen_death_maps", "shadow_environment_snapshots",
                         "death_micro_cooccurrence_observations",
                         "death_micro_association_reports",
                         "system_solvability_audits"):
                check(name in names, "missing table: " + name)
        finally:
            creation_conn.close()
        candidate = {"type": "dsl_strategy", "strategy_key": definition["key"],
                     "symbol": "BTC-USDT-SWAP", "timeframe": "1h",
                     "dsl": definition, "executable_hash": dsl.executable_hash(definition),
                     "parent_version_hash": None}
        evidence = {"passed": True, "gates": {"test": True}, "runs": {}}
        digest = ecosystem._store_candidate(
            candidate, evidence, "awaiting_codex_review")
        ecosystem._store_version(
            digest, candidate, evidence, "awaiting_codex_review",
            {"codex_review_required": True}, version_type="safe_dsl")
        codex_result = ecosystem.codex_review_dsl_version(
            digest, "APPROVE", "non-network gate test")
        check(codex_result.get("state") == "approved_requires_human",
              "Codex review did not advance to the separate human gate")
    finally:
        ecosystem.AUTO_DIR, ecosystem.DB_PATH = old_auto, old_db


def test_live_registry_coverage():
    expected = {
        "ng5_session_exhaustion_reclaim_long_ai",
        "xag5_session_breakdown_short_ai",
        "ltc5_exhaustion_fade_short_ai",
        "ada5_session_trend_pullback_short_ai",
    }
    root = Path(__file__).resolve().parent
    sources = {
        "daemon": (root / "auto_trade_formal_daemon.py").read_text(encoding="utf-8"),
        "adapter": (root / "auto_trade_strategy_ema6_center_down.py").read_text(encoding="utf-8"),
        "engine": (root / "backtest_engine_v2.py").read_text(encoding="utf-8"),
    }
    for key in expected:
        token = '"%s"' % key
        for component, source in sources.items():
            check(token in source,
                  "%s missing from %s registry" % (key, component))


def test_iteration_information_gain_gate():
    def bundle(trades, win_rate, expectancy, score, gates=2, streak=2):
        return {"evidence": {
            "passed": False,
            "weighted_score": score,
            "gates": {"g%d" % index: index < gates for index in range(5)},
            "runs": {"candidate": {"0.009": {
                "trades": trades, "win_rate_pct": win_rate,
                "expectancy_pct": expectancy, "holdout_win_rate_pct": win_rate,
                "max_loss_streak": streak, "max_drawdown_pct": 25.0,
            }}},
        }}
    parent = bundle(63, 25.40, -2.17, -8.0)
    regression = bundle(31, 16.13, -2.20, -9.0)
    improved = bundle(45, 35.0, 1.5, 4.0, gates=3)
    check(not ecosystem._relative_iteration_assessment(
        parent, regression)["improved"],
        "lower-win-rate second round was incorrectly accepted as information gain")
    check(ecosystem._relative_iteration_assessment(
        parent, improved)["improved"],
        "materially improved child was incorrectly rejected")


def test_creation_capacity_planner():
    targets = [
        {"symbol": "BTC-USDT-SWAP", "timeframe": "5m", "focus": "both"},
        {"symbol": "XAU-USDT-SWAP", "timeframe": "5m", "focus": "both"},
    ]
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE strategy_creation_runs (run_id TEXT, symbol TEXT, "
                 "timeframe TEXT, state TEXT, summary_json TEXT, started_at TEXT, "
                 "finished_at TEXT)")
    original = ecosystem._live_strategy_count
    ecosystem._live_strategy_count = lambda symbol, timeframe: (
        1 if symbol == "BTC-USDT-SWAP" else 0)
    try:
        selected, plan = ecosystem._plan_creation_target(conn, targets)
        check(selected["symbol"] == "XAU-USDT-SWAP",
              "coverage gap was not prioritized")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("INSERT INTO strategy_creation_runs VALUES(?,?,?,?,?,?,?)",
                     ("recent", "XAU-USDT-SWAP", "5m", "training",
                      "{}", now, now))
        selected, plan = ecosystem._plan_creation_target(conn, targets)
        check(selected["symbol"] == "BTC-USDT-SWAP",
              "recent failed branch did not receive cooldown")
    finally:
        ecosystem._live_strategy_count = original
        conn.close()


def test_compute_budget_sleep_and_ai_rescue_gate():
    blocked = ecosystem._deep_search_eligibility([
        {"quick_promising": True, "survived_cost_screen": False}], 0)
    check(not blocked["allowed"] and
          blocked["state"] == "dormant_no_target_local_cost_survivor",
          "quick promise incorrectly purchased expensive three-AI rescue")
    allowed = ecosystem._deep_search_eligibility([
        {"survived_cost_screen": True}], 0)
    check(allowed["allowed"],
          "a measured target-local cost survivor could not unlock deep search")
    check(ecosystem._full_branch_evaluation_budget(48, cold_start=True) == 2,
          "cold-start full-evaluation budget cap is not enforced")
    check(ecosystem._full_branch_evaluation_budget(48, cold_start=False) == 8,
          "experienced target lost its adaptive evaluation budget")
    sleep = ecosystem._negative_cluster_sleep_state(12, 48, 0, 1)
    check(sleep["sleeping"] and sleep["sleep_hours"] == 72.0
          and sleep["remaining_hours"] == 60.0,
          "negative baseline cluster did not enter bounded short compute sleep")
    check(not ecosystem._negative_cluster_sleep_state(12, 48, 1, 1)["sleeping"],
          "cost-surviving cluster was incorrectly put to sleep")

    targets = [
        {"symbol": "BTC-USDT-SWAP", "timeframe": "5m", "focus": "both"},
        {"symbol": "XAU-USDT-SWAP", "timeframe": "5m", "focus": "both"},
    ]
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE strategy_creation_runs (run_id TEXT, symbol TEXT, "
                 "timeframe TEXT, state TEXT, summary_json TEXT, started_at TEXT, "
                 "finished_at TEXT)")
    conn.execute("CREATE TABLE search_branch_observations (symbol TEXT, "
                 "timeframe TEXT, credible INTEGER, survived_cost_screen INTEGER, "
                 "created_at TEXT)")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO strategy_creation_runs VALUES(?,?,?,?,?,?,?)",
                 ("negative", "BTC-USDT-SWAP", "5m",
                  "early_pruned_no_empirical_edge",
                  json.dumps({"state": "early_pruned_no_empirical_edge"}),
                  now, now))
    conn.executemany("INSERT INTO search_branch_observations VALUES(?,?,?,?,?)",
                     [("BTC-USDT-SWAP", "5m", 0, 0, now) for _ in range(48)])
    old_coverage = ecosystem._live_strategy_count
    ecosystem._live_strategy_count = lambda symbol, timeframe: 0
    try:
        selected, plan = ecosystem._plan_creation_target(conn, targets)
        check(selected["symbol"] == "XAU-USDT-SWAP",
              "sleeping negative cluster still consumed the next compute slot")
        btc = next(row for row in plan["ranking"]
                   if row["symbol"] == "BTC-USDT-SWAP")
        check(btc["meta_controller"]["compute_sleep"]["sleeping"],
              "planner did not expose auditable cluster sleep state")
    finally:
        ecosystem._live_strategy_count = old_coverage
        conn.close()


def test_mandatory_live_cognitive_gate_fails_closed():
    assignment = "BTC-USDT-SWAP|1h|legacy"
    open_controls = {"assignments": {assignment: {
        "pause_new_entries": False}}}
    check(formal_daemon._runtime_control_decision(
        open_controls, {}, assignment),
        "legacy optional lifecycle controls unexpectedly blocked entry")
    mandatory = {"enforce": True}
    check(not formal_daemon._runtime_control_decision(
        open_controls, mandatory, assignment),
        "unaudited legacy strategy bypassed the mandatory cognitive gate")
    passed = {"assignments": {assignment: {
        "pause_new_entries": False, "audit_state": "passed_all"}}}
    check(formal_daemon._runtime_control_decision(
        passed, mandatory, assignment),
        "fully audited strategy could not satisfy the mandatory gate")
    passed["assignments"][assignment]["pause_new_entries"] = True
    check(not formal_daemon._runtime_control_decision(
        passed, mandatory, assignment),
        "manual pause was bypassed by a prior audit pass")
    probe = {"assignments": {assignment: {
        "pause_new_entries": False,
        "audit_state": "conditional_frequency_probe",
        "max_position_ratio": 0.10}}}
    check(formal_daemon._runtime_control_decision(
        probe, mandatory, assignment),
        "conditional frequency probe was rejected by the cognitive gate")
    check(abs(formal_daemon._probe_position_ratio_cap(
        "missing_key_for_unit_test", 0.50) - 0.50) < 1e-9,
        "non-probe strategies unexpectedly received a position cap")

    temp_root = Path(tempfile.mkdtemp())
    old_gate = ecosystem.LIVE_COGNITIVE_GATE_PATH
    old_controls = ecosystem.LIVE_RUNTIME_CONTROLS_PATH
    try:
        ecosystem.LIVE_COGNITIVE_GATE_PATH = temp_root / "gate.json"
        ecosystem.LIVE_RUNTIME_CONTROLS_PATH = temp_root / "controls.json"
        candidate = {"symbol": "BTC-USDT-SWAP", "timeframe": "1h"}
        definition = {"key": "candidate"}
        check(not ecosystem._live_version_certificate(
            "exact_hash", candidate, definition)["ok"],
            "missing exact-version certificate did not fail closed")
        ecosystem._atomic(ecosystem.LIVE_COGNITIVE_GATE_PATH,
                          {"enforce": True})
        ecosystem._atomic(ecosystem.LIVE_RUNTIME_CONTROLS_PATH, {
            "assignments": {"BTC-USDT-SWAP|1h|candidate": {
                "pause_new_entries": False, "audit_state": "passed_all",
                "certified_version_hash": "wrong_hash",
                "full_cognitive_matrix_passed": True}}})
        check(not ecosystem._live_version_certificate(
            "exact_hash", candidate, definition)["ok"],
            "stale version hash was accepted as a live certificate")
        controls = ecosystem._read(
            ecosystem.LIVE_RUNTIME_CONTROLS_PATH, {})
        controls["assignments"]["BTC-USDT-SWAP|1h|candidate"][
            "certified_version_hash"] = "exact_hash"
        ecosystem._atomic(ecosystem.LIVE_RUNTIME_CONTROLS_PATH, controls)
        check(ecosystem._live_version_certificate(
            "exact_hash", candidate, definition)["ok"],
            "exact passed_all version certificate was rejected")
    finally:
        ecosystem.LIVE_COGNITIVE_GATE_PATH = old_gate
        ecosystem.LIVE_RUNTIME_CONTROLS_PATH = old_controls


def test_solvability_alarm_uses_natural_language():
    message = ecosystem._format_solvability_alarm_message({
        "deterministic_state": "SAT_WITNESS",
        "ai_state": "mixed_requires_codex_review",
        "trigger_reasons": [
            "rule_or_major_mutation_changed",
            "independent_death_evidence_batch",
        ],
    })
    check("研究结构发生变化" in message and "独立死亡证据" in message,
          "solvability alarm lost the natural-language trigger explanation")
    check("三家 AI 意见不一致" in message,
          "solvability alarm did not explain the mixed AI state")
    check("不会" in message and "自动放宽" in message,
          "solvability alarm omitted the no-auto-relaxation promise")
    for code in ("rule_or_major_mutation_changed",
                 "independent_death_evidence_batch",
                 "mixed_requires_codex_review"):
        check(code not in message,
              "solvability alarm still leaked machine code %s" % code)


def test_solvability_alarm_is_dry_run_outside_production_auto_dir():
    old_auto = ecosystem.AUTO_DIR
    with tempfile.TemporaryDirectory() as temp:
        ecosystem.AUTO_DIR = Path(temp)
        result = ecosystem._send_solvability_alarm(
            "unanimous_logically_feasible",
            {"deterministic_state": "SAT_WITNESS",
             "ai_state": "mixed_requires_codex_review",
             "trigger_reasons": ["rule_or_major_mutation_changed"],
             "week_bucket": "2026-W30"})
        check(result.get("dry_run") is True and result.get("sent") is False,
              "unit-test solvability alarm escaped into a real notification channel")
    ecosystem.AUTO_DIR = old_auto


def test_early_prune_uses_short_then_long_sleep():
    first = ecosystem._negative_cluster_sleep_state(10, 48, 0, 1)
    second = ecosystem._negative_cluster_sleep_state(10, 48, 0, 2)
    awake = ecosystem._negative_cluster_sleep_state(80, 48, 0, 1)
    check(first["sleeping"] and first["sleep_hours"] == 72.0,
          "first no-edge prune did not use the short sleep window")
    check(second["sleeping"] and second["sleep_hours"] == 168.0,
          "repeated no-edge prune did not escalate to the long sleep window")
    check(not awake["sleeping"],
          "short-sleep cluster remained asleep after the wake window")


def test_account_return_and_grade_ratio_are_not_double_scaled():
    closed = {
        "pnl": 0.12, "imr": 1.881,
        "sizing": {"account_equity_usdt": "20.121778"},
        "open_order": {"filled": {"order": {"fee": "-0.003"}}},
        "close_order": {"filled": {"order": {"fee": "-0.003", "pnl": "0.12"}}},
    }
    rate = formal_notify._close_rate_info(closed)
    check(6.0 < rate["pnl_rate_pct"] < 6.1,
          "margin return reconstruction is incorrect")
    check(.55 < rate["account_return_pct"] < .60,
          "account-equity return was not separately disclosed")
    check(formal_executor._effective_position_ratio(.20, .50) == .50,
          "A-grade account allocation was still multiplied by timeframe profile")
    check(formal_executor._effective_position_ratio(.20, None) == .20,
          "manual/configured allocation no longer respects its explicit ratio")


def test_cost_probability_causality_and_meta_pruning():
    positive = ecosystem._probabilistic_net_summary(
        [.03, .02, -.01, .025, .018, -.008, .04, .01, .015, -.006],
        seed_material="positive", bootstrap_samples=300)
    negative = ecosystem._probabilistic_net_summary(
        [.01, -.03, -.02, .005, -.025, -.018, .004, -.04, -.01, -.015],
        seed_material="negative", bootstrap_samples=300)
    check(positive["probability_mean_positive"] > .90,
          "positive net edge did not receive a high posterior probability")
    check(negative["probability_mean_positive"] < .10,
          "negative net edge escaped probabilistic pruning")
    check("mean_t_stat" in positive and "trade_sharpe" not in positive,
          "per-trade t statistic is still mislabeled as Sharpe")

    temp_model = Path(tempfile.mktemp(suffix=".json"))
    temp_model.write_text(json.dumps({
        "scenarios": {"observed_base": {
            "fee_rate_per_side": .0005, "slippage_rate_per_side": .0001,
            "half_spread_rate_per_side": .00001,
            "impact_rate_per_side": .00001, "latency_rate_per_side": .00001,
            "funding_rate_per_8h": .0001}},
        "execution_drag_multiplier": {"BTC-USDT-SWAP": 1.0},
        "capacity_reference": {"maximum_modeled_notional_usdt": 400.0},
        "instrument_observed": {"BTC-USDT-SWAP": {
            "book_samples": 30, "fill_samples": 12,
            "depth_samples": 30, "funding_samples": 20,
            "top5_bid_depth_p25_usd": 1000.0,
            "top5_ask_depth_p25_usd": 800.0,
            "absolute_funding_p75_rate": .0004,
            "half_spread_p75_rate": .00008,
            "adverse_mark_p75_rate": .00035}},
    }), encoding="utf-8")
    old_model = ecosystem.COST_MODEL_PATH
    try:
        ecosystem.COST_MODEL_PATH = temp_model
        friction = ecosystem._friction_scenario(
            "BTC-USDT-SWAP", "observed_base")
        check(friction["half_spread_rate_per_side"] == .00008,
              "observed spread did not override an optimistic proxy")
        check(friction["slippage_rate_per_side"] == .00035,
              "observed adverse execution did not override an optimistic proxy")
        check(friction["forward_calibration"]["book_samples"] == 30,
              "friction calibration provenance was not preserved")
        check(friction["funding_rate_per_8h"] == .0004,
              "observed funding did not replace the optimistic proxy")
        check(friction["forward_calibration"]["capacity_calibration"]["active"],
              "account-notional versus top-five depth capacity cost is inactive")
        check(friction["impact_rate_per_side"] > .00001,
              "depth utilization did not raise the market-impact floor")
    finally:
        ecosystem.COST_MODEL_PATH = old_model

    valid = {
        "causal_chains": [{
            "market_state": "EMA53上方的可观测顺势环境",
            "mechanism": "收盘价上穿EMA6代表短线方向重新建立",
            "observable_transition": "close_ema6由下方切换至上方",
            "why_cost_survives": "long__close_ema6__price_above_ema53图谱已显示压力成本后正均值",
            "failure_condition": "压力成本后均值或留出段均值转负即证伪",
        }],
        "falsification_tests": ["按时间三段前向检验至少两段为正",
                                "提高到严重成本情景后期望不得崩溃"],
    }
    context = {"deterministic_event_atlas": {"qualified_patterns": [
        {"pattern_id": "long__close_ema6__price_above_ema53"}]}}
    check(ecosystem._causal_hypothesis_audit(valid, context)["passed"],
          "observable atlas-anchored causal hypothesis was rejected")
    invalid = json.loads(json.dumps(valid))
    invalid["causal_chains"][0]["mechanism"] = "订单簿失衡迫使做市商撤单产生流动性真空"
    check(not ecosystem._causal_hypothesis_audit(invalid, context)["passed"],
          "unavailable order-book narrative escaped causal audit")
    precheck_failed = {"kind": "dsl", "hypothesis_id": "bad",
                       "semantic_audit": {"passed": True},
                       "causal_audit": {"passed": True},
                       "deterministic_entry_edge_screen": {"passed": False}}
    ai_rejected = {"kind": "dsl", "hypothesis_id": "useful_failure",
                   "semantic_audit": {"passed": True},
                   "causal_audit": {"passed": True},
                   "deterministic_entry_edge_screen": {"passed": True}}
    survivors = ecosystem._precheck_survivors_rejected_by_ai(
        {"rejected": [precheck_failed, ai_rejected]})
    check([row["hypothesis_id"] for row in survivors] == ["useful_failure"],
          "deterministic-rejected DSL escaped into expensive exploration")

    targets = [
        {"symbol": "BTC-USDT-SWAP", "timeframe": "5m", "focus": "both"},
        {"symbol": "XAU-USDT-SWAP", "timeframe": "5m", "focus": "both"},
    ]
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE strategy_creation_runs (run_id TEXT, symbol TEXT, "
                 "timeframe TEXT, state TEXT, summary_json TEXT, started_at TEXT, "
                 "finished_at TEXT)")
    old_coverage = ecosystem._live_strategy_count
    ecosystem._live_strategy_count = lambda symbol, timeframe: 0
    try:
        old_time = "2026-01-01 00:00:00"
        for index in range(3):
            conn.execute("INSERT INTO strategy_creation_runs VALUES(?,?,?,?,?,?,?)",
                         ("prune%d" % index, "BTC-USDT-SWAP", "5m",
                          "early_pruned_no_empirical_edge",
                          json.dumps({"state": "early_pruned_no_empirical_edge",
                                      "api_calls_saved": 3}), old_time, old_time))
        selected, plan = ecosystem._plan_creation_target(conn, targets)
        check(selected["symbol"] == "XAU-USDT-SWAP",
              "meta-controller did not move compute away from a repeatedly dead branch")
        btc = next(row for row in plan["ranking"]
                   if row["symbol"] == "BTC-USDT-SWAP")
        check(btc["meta_controller"]["consecutive_early_prunes"] == 3,
              "consecutive early-prune memory was not counted")
        check(btc["meta_controller"]["api_calls_saved"] == 9,
              "saved AI-call budget was not accounted")
    finally:
        ecosystem._live_strategy_count = old_coverage
        conn.close()


def test_branch_surrogate_budget_and_depth_math():
    old_auto, old_db = ecosystem.AUTO_DIR, ecosystem.DB_PATH
    temp_root = Path(tempfile.mkdtemp())
    try:
        ecosystem.AUTO_DIR = temp_root
        ecosystem.DB_PATH = temp_root / "ecosystem.db"
        pattern = "long__close_ema6__price_above_ema53"
        base_row = {
            "pattern_id": pattern, "direction": "long",
            "event": {"feature": "close", "op": "cross_above"},
            "context": {"feature": "close", "op": "gt"},
            "best_horizon_bars": 4, "evaluation_stage": "full",
            "full_evaluated": True, "survived_cost_screen": True,
            "credible": True, "entry_events": 30,
            "full": {"net_win_rate_pct": 60, "mean_net_pct": 2.0,
                     "probability_mean_positive": .92},
            "holdout": {"mean_net_pct": 1.0},
            "stressed": {"mean_net_pct": .5},
            "acquisition_score": 70.0,
        }
        atlas = {"instrument": "BTC-USDT-SWAP", "timeframe": "15m",
                 "qualified_count": 1, "_observations": [base_row],
                 "resource_allocation": {"quick_candidates": 48,
                                         "full_candidates": 2}}
        ecosystem._store_event_atlas_learning("run-one", atlas)
        failed = json.loads(json.dumps(base_row))
        failed.update({"survived_cost_screen": False, "credible": False,
                       "evaluation_stage": "full_rejected"})
        atlas["qualified_count"] = 0; atlas["_observations"] = [failed]
        ecosystem._store_event_atlas_learning("run-two", atlas)
        budget_pruned = json.loads(json.dumps(failed))
        budget_pruned["evaluation_stage"] = (
            "pruned_by_successive_halving_budget")
        atlas["_observations"] = [budget_pruned]
        ecosystem._store_event_atlas_learning("run-budget", atlas)
        prior = ecosystem._branch_survival_priors(
            "BTC-USDT-SWAP", "15m", [pattern])[pattern]
        check(prior["exact_attempts"] == 1,
              "overlapping same-week runs or budget pruning inflated evidence")
        check(.25 < prior["mean"] < .75,
              "hierarchical Beta posterior is not reflecting mixed outcomes")
        # Evidence for another target must have exactly zero posterior weight.
        foreign_pattern = "long__foreign_only__price_above_ema53"
        conn = ecosystem._db()
        conn.execute(
            "INSERT INTO search_branch_observations "
            "(observation_id,run_id,symbol,timeframe,pattern_id,evaluation_stage,"
            "full_evaluated,survived_cost_screen,credible,event_count,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("foreign", "foreign-run", "XAU-USDT-SWAP", "15m",
             foreign_pattern, "full", 1, 1, 1, 40,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit(); conn.close()
        isolated = ecosystem._branch_survival_priors(
            "BTC-USDT-SWAP", "15m", [foreign_pattern])[foreign_pattern]
        check(isolated["alpha"] == 1.0 and isolated["beta"] == 1.0
              and isolated["cross_target_transfer_weight"] == 0.0,
              "cross-target outcome evidence contaminated the local posterior")
        conn = ecosystem._db()
        try:
            ledger = conn.execute(
                "SELECT quick_candidates,full_candidates,ai_calls_saved "
                "FROM search_budget_ledger WHERE run_id='run-two'").fetchone()
            check(ledger == (48, 2, 3),
                  "search budget ledger did not account for early AI-call savings")
        finally:
            conn.close()
    finally:
        ecosystem.AUTO_DIR, ecosystem.DB_PATH = old_auto, old_db
    linear_base = cost_calibrator._contracts_to_usd(
        [["50000", "2"]], .01, "BTC", "linear")
    quote_value = cost_calibrator._contracts_to_usd(
        [["1", "2"]], 100.0, "USD", "inverse")
    check(linear_base == 1000.0 and quote_value == 200.0,
          "top-five contract depth was not converted to USD correctly")


def test_active_hunt_consensus_is_unlabeled_and_vetoed():
    mixed = '<think>先比较 {not valid} 后输出</think>\n```json\n{"reviews":[]}\n```'
    check(ai._parse_content_json(mixed) == {"reviews": []},
          "reasoning-prefixed fenced JSON was not recovered safely")
    pattern = {"pattern_id": "long__close_ema6__price_above_ema53",
               "direction": "long"}
    rounds = [{"round": index, "attack": "test", "verdict": "SURVIVE",
               "reason": "observable"} for index in (1, 2, 3)]
    def review(provider, decision, filters, hard_veto=False, operators=None):
        return {"provider": provider, "ok": True, "reviews": [{
            "pattern_id": pattern["pattern_id"], "decision": decision,
            "viability_score": 72, "required_filter_ids": filters,
            "mutation_operator_ids": operators or [],
            "hard_veto": hard_veto, "fatal_flaws": [],
            "adaptation_rounds": rounds,
            "counterfactual_blueprint": "unlabeled measurable blueprint"}]}
    ai_result = {"results": [
        review("deepseek", "HUNT", ["directional_body", "cci_acceleration"],
               operators=["counter_false_breakout"]),
        review("qwen", "HUNT", ["directional_body", "macd_alignment"],
               operators=["counter_false_breakout"]),
        review("chatgpt", "REJECT", [])]}
    result = ecosystem._consolidate_active_hunt_priors(
        ai_result, [pattern], {pattern["pattern_id"]: {"uncertainty": .25}})[0]
    check(result["accepted_for_probe"] and
          result["agreed_filter_ids"] == ["directional_body",
                                           "strong_directional_body",
                                           "two_bar_directional_confirmation"],
          "two-AI measurable necessity intersection was not compiled")
    check(result["agreed_mutation_operator_ids"] == ["counter_false_breakout"],
          "two-AI controlled mutation operator was not compiled")
    check(result["label_policy"] == "unlabeled_search_prior_not_positive_sample",
          "counterfactual blueprint was incorrectly labeled positive")
    ai_result["results"][2] = review("chatgpt", "REJECT", [], hard_veto=True)
    vetoed = ecosystem._consolidate_active_hunt_priors(
        ai_result, [pattern], {pattern["pattern_id"]: {"uncertainty": .25}})[0]
    check(not vetoed["accepted_for_probe"],
          "measurable hard veto did not stop the active-hunt probe")
    check(micro_collector._notional(50000, 2, .01, "linear", "BTC") == 1000
          and micro_collector._notional(1, 2, 100, "inverse", "USD") == 200,
          "microstructure contract notional conversion is incorrect")
    sub = micro_collector._trade_subwindows(
        [(94000, 100.0), (98000, -40.0), (99500, 60.0)], 100000)
    check(sub["5000"]["trade_count"] == 2
          and sub["60000"]["trade_count"] == 3,
          "forward public-trade subwindows were not time-bounded correctly")


def test_active_hunt_positive_promotion_requires_shadow():
    old_auto, old_db = ecosystem.AUTO_DIR, ecosystem.DB_PATH
    old_friction = ecosystem._friction_scenario
    with tempfile.TemporaryDirectory() as temp:
        ecosystem.AUTO_DIR = Path(temp); ecosystem.DB_PATH = Path(temp)/"eco.db"
        ecosystem._friction_scenario = lambda symbol, name="observed_base": {
            "forward_calibration": {"depth_samples": 30,
             "capacity_calibration": {"active": True, "depth_utilization": .05}}}
        conn = ecosystem._db(); pattern_id = "long__close_ema6__price_above_ema53"
        conn.execute("INSERT INTO active_hunt_probe_experiments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("probe-1", "run-1", "BTC-USDT-SWAP", "5m", pattern_id,
                      2, "[]", 12, 4, 3.0, .5, .7,
                      "suspected_positive_probe", "{}", "2026-07-22 00:00:00"))
        candidate = {"symbol": "BTC-USDT-SWAP", "timeframe": "5m",
                     "source_pattern_ids": [pattern_id]}
        primary = {"trades_per_day": .2,
                   "rolling_windows": [{"trades": 4, "expectancy_pct": 1.0}]*5,
                   "regime_performance": {"bull_high_vol": {
                       "trades": 4, "expectancy_pct": .5}}}
        evidence = {"passed": True, "runs": {"candidate": {"0.009": primary},
                    "stress": {"severe": {"expectancy_pct": .2,
                    "max_drawdown_pct": 20, "max_loss_streak": 2}}}}
        gates = ecosystem._advanced_positive_gates(candidate, evidence)
        check(gates["passed"], "valid offline confirmation precursor was rejected")
        ecosystem._register_shadow_confirmation(
            conn, candidate, evidence, "candidate-passed", gates)
        check(conn.execute("SELECT COUNT(*) FROM positive_core_samples").fetchone()[0] == 0,
              "offline pass bypassed the mandatory seven-day shadow gate")
        state = conn.execute("SELECT state FROM positive_confirmation_candidates").fetchone()[0]
        check(state == "awaiting_shadow_validation",
              "offline-qualified candidate was not registered for shadowing")
        conn.close()
    ecosystem.AUTO_DIR, ecosystem.DB_PATH = old_auto, old_db
    ecosystem._friction_scenario = old_friction


def test_active_hunt_probe_memory_updates_prior():
    old_auto, old_db = ecosystem.AUTO_DIR, ecosystem.DB_PATH
    with tempfile.TemporaryDirectory() as temp:
        ecosystem.AUTO_DIR = Path(temp)
        ecosystem.DB_PATH = Path(temp) / "ecosystem.db"
        conn = ecosystem._db()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pattern_id = "long__j_20__price_above_ema53"
        conn.execute("INSERT INTO active_hunt_probe_experiments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("probe-memory", "run-memory", "BTC-USDT-SWAP", "5m",
                      pattern_id, 2, '["directional_body"]', 9, 3, 3.0,
                      -1.0, .1, "probe_rejected", "{}", now))
        conn.commit(); conn.close()
        history = ecosystem._active_hunt_probe_history(
            {"symbol": "BTC-USDT-SWAP", "timeframe": "5m"})
        prior = ecosystem._branch_survival_priors(
            "BTC-USDT-SWAP", "5m", [pattern_id])[pattern_id]
        check(history and history[0]["state"] == "probe_rejected",
              "prior probe was not returned to the next AI hunt context")
        check(prior["probe_attempts"] == 1 and prior["beta"] > 1.5,
              "failed active-hunt probe did not update the branch posterior")
    ecosystem.AUTO_DIR, ecosystem.DB_PATH = old_auto, old_db


def test_shadow_runner_is_read_only_when_empty():
    old_auto, old_db = ecosystem.AUTO_DIR, ecosystem.DB_PATH
    with tempfile.TemporaryDirectory() as temp:
        ecosystem.AUTO_DIR = Path(temp); ecosystem.DB_PATH = Path(temp)/"eco.db"
        result = shadow_validator.run_once()
        check(result["ok"] and result["research_only"]
              and result["pending_candidates"] == 0,
              "empty shadow runner was not a bounded read-only no-op")
        check("no exchange order endpoint" in result["boundary"],
              "shadow execution boundary was not explicit")
    ecosystem.AUTO_DIR, ecosystem.DB_PATH = old_auto, old_db


def test_prescreen_death_map_blocks_unanswered_repeat():
    pattern = {"pattern_id": "long__close_ema6__price_above_ema53",
               "direction": "long"}
    rounds = [{"round": value, "verdict": "SURVIVE"} for value in (1, 2, 3)]
    def panel(provider):
        return {"provider": provider, "ok": True, "reviews": [{
            "pattern_id": pattern["pattern_id"], "decision": "HUNT",
            "viability_score": 80, "required_filter_ids": ["directional_body"],
            "mutation_operator_ids": ["counter_false_breakout"],
            "hard_veto": False, "adaptation_rounds": rounds,
            "death_avoidance_explanation": "only addresses stop_cluster"}]}
    result = ecosystem._consolidate_active_hunt_priors(
        {"results": [panel("deepseek"), panel("qwen"), panel("chatgpt")]},
        [pattern], {pattern["pattern_id"]: {"uncertainty": .2}},
        death_map=[{"pattern_id": pattern["pattern_id"], "record_count": 4,
                    "death_codes": {"negative_net_expectancy": 4}}])[0]
    check(not result["accepted_for_probe"]
          and result["unmitigated_repeated_death_codes"] ==
          ["negative_net_expectancy"],
          "repeat death without a matching counter-operator was not blocked")


def test_prescreen_death_archive_separates_stages():
    old_auto, old_db = ecosystem.AUTO_DIR, ecosystem.DB_PATH
    old_analysis = ai.prescreen_death_analysis
    with tempfile.TemporaryDirectory() as temp:
        ecosystem.AUTO_DIR = Path(temp); ecosystem.DB_PATH = Path(temp)/"eco.db"
        ai.prescreen_death_analysis = lambda context: {
            "ok": True, "terrain_cells": [], "policy": "test_no_label"}
        assignment = {"symbol": "BTC-USDT-SWAP", "timeframe": "5m"}
        observation = {"pattern_id": "long__close_ema6__price_above_ema53",
                       "credible": False, "evaluation_stage": "test",
                       "entry_events": 20,
                       "full": {"events": 20, "mean_net_pct": -1,
                                "stop_hit_rate_pct": 25},
                       "holdout": {"mean_net_pct": -2},
                       "stressed": {"mean_net_pct": -3}}
        prior = {"review_panels": [{"provider": "deepseek", "ok": True,
                  "reviews": [{"pattern_id": observation["pattern_id"],
                               "decision": "REJECT", "hard_veto": False,
                               "fatal_flaws": ["negative_net_expectancy"],
                               "reason": "recorded reasoning"}]}]}
        result = ecosystem._archive_prescreen_deaths(
            "run-test", assignment, [observation], prior)
        conn = ecosystem._db()
        stages = dict(conn.execute(
            "SELECT stage,COUNT(*) FROM prescreen_rejections GROUP BY stage"))
        maps = conn.execute("SELECT COUNT(*) FROM prescreen_death_maps").fetchone()[0]
        conn.close()
        check(result["ok"] and stages == {"deterministic_atlas": 1,
                                           "three_ai_prescreen": 1}
              and maps == 1,
              "deterministic deaths and AI vetoes were not archived separately")
    ai.prescreen_death_analysis = old_analysis
    ecosystem.AUTO_DIR, ecosystem.DB_PATH = old_auto, old_db


def test_cached_ai_panel_recomputes_current_death_memory():
    old_auto, old_db = ecosystem.AUTO_DIR, ecosystem.DB_PATH
    with tempfile.TemporaryDirectory() as temp:
        ecosystem.AUTO_DIR = Path(temp); ecosystem.DB_PATH = Path(temp)/"eco.db"
        conn = ecosystem._db()
        pattern_id = "long__close_ema6__price_above_ema53"
        rounds = [{"round": value, "verdict": "SURVIVE"} for value in (1, 2, 3)]
        panels = []
        for provider in ("deepseek", "qwen", "chatgpt"):
            panels.append({"provider": provider, "ok": True, "reviews": [{
                "pattern_id": pattern_id, "decision": "HUNT",
                "viability_score": 80, "required_filter_ids": ["directional_body"],
                "mutation_operator_ids": ["counter_false_breakout"],
                "hard_veto": False, "adaptation_rounds": rounds}]})
        iso = datetime.now().isocalendar(); week = "%04d-W%02d" % (iso[0], iso[1])
        cached = {"schema_version": ecosystem.ACTIVE_HUNT_SCHEMA_VERSION,
                  "operator_lattice_fingerprint": ecosystem._sha({}),
                  "distilled_rule_fingerprint": ecosystem._sha(
                      ecosystem._distillation_rule_context(
                          {"symbol": "BTC-USDT-SWAP", "timeframe": "5m"})),
                  "probe_catalog": [{"pattern_id": pattern_id, "direction": "long"}],
                  "review_panels": panels, "consolidated": []}
        conn.execute("INSERT INTO active_hunt_prior_runs VALUES(?,?,?,?,?,?)",
                     ("prior", "BTC-USDT-SWAP", "5m", week,
                      json.dumps(cached), "2026-07-22 00:00:00"))
        for index in range(2):
            conn.execute("INSERT INTO prescreen_rejections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         ("death-%d" % index, "run-%d" % index,
                          "BTC-USDT-SWAP", "5m", pattern_id,
                          "deterministic_atlas", "deterministic", "REJECT", 0,
                          '["negative_net_expectancy"]', "measured",
                          json.dumps({"full": {"mean_net_pct": -1-index},
                                      "holdout": {"mean_net_pct": -2-index},
                                      "stressed": {"mean_net_pct": -3-index}}),
                          "2026-07-22 00:00:00"))
        conn.commit(); conn.close()
        result = ecosystem._active_hunt_prior(
            None, {"symbol": "BTC-USDT-SWAP", "timeframe": "5m"}, {})
        proposal = result["consolidated"][0]
        check(result["cached"] and result.get("dynamic_memory_recomputed_at")
              and not proposal["accepted_for_probe"]
              and proposal["unmitigated_repeated_death_codes"] ==
              ["negative_net_expectancy"],
              "weekly AI cache delayed current death-memory enforcement")
    ecosystem.AUTO_DIR, ecosystem.DB_PATH = old_auto, old_db


def test_outcome_free_micro_primitive_discovery():
    old_root, old_auto, old_db, old_status = (
        micro_primitives.ROOT, micro_primitives.AUTO_DIR,
        micro_primitives.DB_PATH, micro_primitives.STATUS_PATH)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); auto = root/"auto_trade"; auto.mkdir()
        micro_primitives.ROOT = root; micro_primitives.AUTO_DIR = auto
        micro_primitives.DB_PATH = auto/"microstructure_telemetry.db"
        micro_primitives.STATUS_PATH = auto/"microstructure_primitives_status.json"
        conn = sqlite3.connect(str(micro_primitives.DB_PATH))
        conn.execute("""CREATE TABLE microstructure_samples(
          sample_key TEXT PRIMARY KEY,symbol TEXT,observed_ms INTEGER,bid REAL,ask REAL,
          half_spread_rate REAL,bid_depth_usd REAL,ask_depth_usd REAL,
          depth_imbalance REAL,trade_count INTEGER,buy_notional_usd REAL,
          sell_notional_usd REAL,trade_flow_imbalance REAL,
          aggression_acceleration REAL,trade_window_ms INTEGER,created_at TEXT,
          subwindows_json TEXT)""")
        for index in range(100):
            flow = ((index % 11)-5)/5.0; depth = ((index % 9)-4)/4.0
            sub = json.dumps({str(window): {"trade_flow_imbalance": flow}
                              for window in (5000, 15000, 60000)})
            conn.execute("INSERT INTO microstructure_samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (str(index), "BTC-USDT-SWAP", 1000000+index*60000,
                          100, 100.1, .0005+(index % 4)*.0001, 1000+index,
                          1100-index, depth, 20+index % 7, 100, 90, flow,
                          flow/2.0, 60000, "2026-07-22 00:00:00", sub))
        conn.commit(); conn.close()
        result = micro_primitives.run_once(use_ai=False)
        repeat = micro_primitives.run_once(use_ai=False)
        conn = sqlite3.connect(str(micro_primitives.DB_PATH))
        states = dict(conn.execute(
            "SELECT state,COUNT(*) FROM microstructure_primitive_clusters GROUP BY state"))
        assignments = conn.execute(
            "SELECT COUNT(*) FROM microstructure_primitive_assignments").fetchone()[0]
        conn.close()
        check(result["outcome_labels_used"] is False
              and result["predictive_use_allowed"] is False
              and states.get("observed_unlabeled", 0) >= 2 and assignments >= 80,
              "micro primitive discovery leaked labels or failed to remain descriptive")
        check(repeat["canonical_identity_matches"] >= 2
              and repeat["new_canonical_identities"] == 0
              and repeat["new_fingerprints"] == 0,
              "stable cluster identities were recreated and would repeat AI review")
    (micro_primitives.ROOT, micro_primitives.AUTO_DIR,
     micro_primitives.DB_PATH, micro_primitives.STATUS_PATH) = (
        old_root, old_auto, old_db, old_status)


def test_shadow_environment_snapshot_schema():
    old_auto, old_db = ecosystem.AUTO_DIR, ecosystem.DB_PATH
    with tempfile.TemporaryDirectory() as temp:
        ecosystem.AUTO_DIR = Path(temp); ecosystem.DB_PATH = Path(temp)/"eco.db"
        conn = ecosystem._db()
        shadow_validator._environment_event(
            conn, "candidate", "BTC-USDT-SWAP", "5m", 1234, "entry",
            {"volatility_bucket": "mid"}, {"available": True},
            {"cluster_id": "slow_micro_test", "predictive_label": False},
            {"position_open": True})
        conn.commit()
        row = conn.execute(
            "SELECT phase,primitive_json FROM shadow_environment_snapshots").fetchone()
        conn.close()
        check(row[0] == "entry" and
              json.loads(row[1])["predictive_label"] is False,
              "shadow environment context was not persisted independently")
    ecosystem.AUTO_DIR, ecosystem.DB_PATH = old_auto, old_db


def test_death_micro_association_is_descriptive_only():
    strong = micro_primitives._risk_ratio_cell(15, 20, 20, 100)
    weak = micro_primitives._risk_ratio_cell(2, 20, 20, 100)
    check(strong["lift"] > 3 and strong["lift_ci95_low"] > 1
          and weak["lift_ci95_low"] < 1,
          "descriptive cooccurrence interval did not separate strong/weak counts")


def test_forward_death_micro_alignment_without_outcome():
    try:
        import pandas as pd
    except ImportError:
        # The production project environment includes pandas; the lightweight
        # local safety runner may not. Server verification exercises this path.
        return
    old_eco_auto, old_eco_db = ecosystem.AUTO_DIR, ecosystem.DB_PATH
    old_loader = ecosystem._load_research_frame
    old_primitive_values = (micro_primitives.ROOT, micro_primitives.AUTO_DIR,
                            micro_primitives.DB_PATH, micro_primitives.STATUS_PATH)
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp); auto = root/"auto_trade"; auto.mkdir()
        ecosystem.AUTO_DIR = auto; ecosystem.DB_PATH = auto/"eco.db"
        micro_primitives.ROOT = root; micro_primitives.AUTO_DIR = auto
        micro_primitives.DB_PATH = auto/"micro.db"
        micro_primitives.STATUS_PATH = auto/"status.json"
        micro = micro_primitives._connect()
        micro.execute("""CREATE TABLE microstructure_samples(
          sample_key TEXT PRIMARY KEY,symbol TEXT,observed_ms INTEGER,bid REAL,ask REAL,
          half_spread_rate REAL,bid_depth_usd REAL,ask_depth_usd REAL,
          depth_imbalance REAL,trade_count INTEGER,buy_notional_usd REAL,
          sell_notional_usd REAL,trade_flow_imbalance REAL,
          aggression_acceleration REAL,trade_window_ms INTEGER,created_at TEXT,
          subwindows_json TEXT)""")
        base_ms = int(time.time()*1000)-300*5*60*1000
        signal_ms = base_ms+280*5*60*1000
        micro.execute("INSERT INTO microstructure_samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("sample", "BTC-USDT-SWAP", signal_ms, 100, 101, .001,
                       1000, 1000, 0, 10, 100, 100, 0, 0, 1000,
                       "2026-07-22 00:00:00", "{}"))
        micro.execute("INSERT INTO microstructure_primitive_clusters VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("cluster", "v", "fingerprint", "observed_unlabeled", "未命名",
                       "observable", 1, "{}", "[]", "{}",
                       "2026-07-22 00:00:00", "2026-07-22 00:00:00"))
        micro.execute("INSERT INTO microstructure_windows VALUES(?,?,?,?,?,?,?,?)",
                      ("window", "run", "BTC-USDT-SWAP", signal_ms-60000,
                       signal_ms, "{}", "observable", "2026-07-22 00:00:00"))
        micro.execute("INSERT INTO microstructure_primitive_assignments VALUES(?,?,?,?,?,?,?,?)",
                      ("assign", "run", "window", "BTC-USDT-SWAP", signal_ms,
                       "cluster", .1, "2026-07-22 00:00:00"))
        micro.commit()
        eco = ecosystem._db()
        payload = {"direction": "long",
                   "event": {"feature": "close", "op": "cross_above",
                             "right": {"feature": "ema6"}},
                   "context": {"feature": "close", "op": "gt",
                               "right": {"feature": "ema53"}}}
        eco.execute("INSERT INTO prescreen_rejections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("death", "run", "BTC-USDT-SWAP", "5m", "pattern",
                     "deterministic_atlas", "deterministic", "REJECT", 0,
                     '["negative_net_expectancy"]', "measured",
                     json.dumps(payload), "2026-07-22 00:00:00"))
        eco.commit(); eco.close()
        index = pd.to_datetime([base_ms+i*5*60*1000 for i in range(300)],
                               unit="ms", utc=True).tz_convert("Asia/Shanghai")
        frame = pd.DataFrame({"close": [100.0]*300, "ema6": [101.0]*300,
                              "ema53": [90.0]*300}, index=index)
        frame.iloc[280, frame.columns.get_loc("close")] = 102.0
        ecosystem._load_research_frame = lambda symbol, timeframe: frame
        result = micro_primitives._capture_death_micro_cooccurrence(micro, "monitor")
        eco = ecosystem._db()
        stored = eco.execute(
            "SELECT matched,payload_json FROM death_micro_cooccurrence_observations").fetchone()
        eco.close(); micro.close()
        check(result["inserted"] == 1 and stored[0] == 1
              and json.loads(stored[1])["outcome_label"] is None,
              "forward death/micro alignment fabricated an outcome or missed a match")
    ecosystem.AUTO_DIR, ecosystem.DB_PATH = old_eco_auto, old_eco_db
    ecosystem._load_research_frame = old_loader
    (micro_primitives.ROOT, micro_primitives.AUTO_DIR,
     micro_primitives.DB_PATH, micro_primitives.STATUS_PATH) = old_primitive_values


def test_system_solvability_witness_and_cached_audit():
    witness = ecosystem._deterministic_solvability_witness()
    check(witness["state"] == "SAT_WITNESS" and all(witness["checks"].values()),
          "current gates became structurally contradictory")
    old_auto, old_db = ecosystem.AUTO_DIR, ecosystem.DB_PATH
    old_reviews = ai.system_solvability_reviews
    with tempfile.TemporaryDirectory() as temp:
        ecosystem.AUTO_DIR = Path(temp); ecosystem.DB_PATH = Path(temp)/"eco.db"
        ai.system_solvability_reviews = lambda context: {
            "ok": True, "unanimous_feasible": True, "unanimous_empty": False,
            "results": [{"provider": name, "ok": True, "verdict": "FEASIBLE"}
                        for name in ("deepseek", "qwen", "chatgpt")]}
        first = ecosystem.run_system_solvability_audit()
        second = ecosystem.run_system_solvability_audit()
        check(first["ai_state"] == "unanimous_logically_feasible"
              and first["api_calls_used"] == 3 and second["cached"]
              and second["api_calls_used"] == 0,
              "weekly solvability audit was not unanimous/cached safely")
        conn = ecosystem._db()
        conn.execute("INSERT INTO pruning_rule_memory VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("rule", "BTC-USDT-SWAP", "5m", "pattern",
                      "directional_body", "negative_net_expectancy", "active", 2,
                      "{}", "{}", "2026-07-22 00:00:00",
                      "2026-07-22 00:00:00"))
        conn.commit(); conn.close()
        # Material change first yields a mixed AI panel; history must keep it.
        ai.system_solvability_reviews = lambda context: {
            "ok": True, "unanimous_feasible": False, "unanimous_empty": False,
            "results": [
                {"provider": "deepseek", "ok": True, "verdict": "FEASIBLE"},
                {"provider": "qwen", "ok": True, "verdict": "UNCERTAIN"},
                {"provider": "chatgpt", "ok": True, "verdict": "FEASIBLE"},
            ]}
        mixed = ecosystem.run_system_solvability_audit()
        check(mixed["ai_state"] == "mixed_requires_codex_review"
              and mixed.get("snapshot_path")
              and Path(mixed["snapshot_path"]).exists()
              and mixed.get("automatic_rule_relaxation") is False,
              "mixed solvability panel was not fail-closed with durable snapshot")
        ai.system_solvability_reviews = lambda context: {
            "ok": True, "unanimous_feasible": True, "unanimous_empty": False,
            "results": [{"provider": name, "ok": True, "verdict": "FEASIBLE"}
                        for name in ("deepseek", "qwen", "chatgpt")]}
        # Force another write so the mixed row must survive append-only storage.
        changed = ecosystem.run_system_solvability_audit(force=True)
        stable = ecosystem.run_system_solvability_audit()
        conn = ecosystem._db()
        history = conn.execute(
            "SELECT ai_state FROM system_solvability_audits "
            "ORDER BY created_at ASC").fetchall()
        conn.close()
        check(not changed["cached"] and changed["api_calls_used"] == 3
              and changed["ai_state"] == "unanimous_logically_feasible"
              and stable["cached"] and stable["api_calls_used"] == 0
              and any(row[0] == "mixed_requires_codex_review" for row in history)
              and any(row[0] == "unanimous_logically_feasible" for row in history),
              "material rule change overwrote mixed audit history")
    ai.system_solvability_reviews = old_reviews
    ecosystem.AUTO_DIR, ecosystem.DB_PATH = old_auto, old_db


def test_prescreen_rerun_does_not_multiply_evidence():
    measured = {"evaluation_stage": "full", "entry_events": 42,
                "full": {"mean_net_pct": -1.0},
                "holdout": {"mean_net_pct": -1.2},
                "stressed": {"mean_net_pct": -1.5},
                "multiple_testing": {"credible": False}}
    enriched = dict(measured)
    enriched.update({"event": {"feature": "close", "op": "cross_above"},
                     "context": {"feature": "close", "op": "gt"}})
    deterministic = [
        ("pattern", "deterministic_atlas", "deterministic",
         '["negative_net_expectancy"]', 0, "first", json.dumps(measured)),
        ("pattern", "deterministic_atlas", "deterministic",
         '["negative_net_expectancy"]', 0, "rerun", json.dumps(enriched)),
    ]
    ai_reruns = [
        ("pattern", "three_ai_prescreen", "deepseek",
         '["ai_logic_rejection"]', 0, "wording one", "{}"),
        ("pattern", "three_ai_prescreen", "deepseek",
         '["ai_logic_rejection"]', 0, "wording two", '{"extra":"cached"}'),
    ]
    check(len(ecosystem._dedupe_prescreen_evidence(deterministic)) == 1,
          "metadata-enrichment rerun multiplied deterministic evidence")
    check(len(ecosystem._dedupe_prescreen_evidence(ai_reruns)) == 1,
          "replayed provider opinion multiplied an AI vote")


def test_cognitive_blank_is_search_priority_not_gate_relaxation():
    patterns = [{"pattern_id": "blank", "direction": "long"},
                {"pattern_id": "known", "direction": "long"}]
    rounds = [{"round": value, "verdict": "SURVIVE"}
              for value in (1, 2, 3)]
    results = []
    for provider in ("deepseek", "qwen", "chatgpt"):
        results.append({"provider": provider, "ok": True, "reviews": [
            {"pattern_id": pattern_id, "decision": "HUNT",
             "viability_score": 80,
             "required_filter_ids": ["directional_body"],
             "mutation_operator_ids": [], "hard_veto": False,
             "adaptation_rounds": rounds,
             "micro_background_plan": {"avoid_cluster_ids": [],
                                         "observation_only": True}}
            for pattern_id in ("blank", "known")]})
    consolidated = ecosystem._consolidate_active_hunt_priors(
        {"results": results}, patterns,
        {"blank": {"uncertainty": .1}, "known": {"uncertainty": .1}},
        death_map=[{"pattern_id": "known", "record_count": 1,
                    "death_codes": {"negative_net_expectancy": 1}}])
    check(consolidated[0]["pattern_id"] == "blank"
          and consolidated[0]["cognitive_blank"]
          and consolidated[0]["accepted_for_probe"]
          and consolidated[1]["accepted_for_probe"],
          "cognitive blank was not prioritized or improperly changed gates")


def test_operator_lattice_is_dormant_without_local_cost_survivor():
    result = ecosystem._operator_lattice_screen(
        None, {"symbol": "LTC-USDT-SWAP", "timeframe": "15m"},
        [{"pattern_id": "p", "quick_promising": True,
          "survived_cost_screen": False}])
    check(result["state"] == "dormant_no_target_local_cost_survivor"
          and result["tested_combinations"] == 0
          and result["positive_labels_created"] == 0
          and result["sealed_holdout_used"] is False,
          "operator lattice ran without a target-local cost survivor")


def test_global_death_evidence_keeps_target_boundary():
    old_auto, old_db = ecosystem.AUTO_DIR, ecosystem.DB_PATH
    with tempfile.TemporaryDirectory() as temp:
        ecosystem.AUTO_DIR = Path(temp); ecosystem.DB_PATH = Path(temp)/"eco.db"
        conn = ecosystem._db()
        measured = json.dumps({"evaluation_stage": "full",
                               "entry_events": 20,
                               "full": {"mean_net_pct": -1.0},
                               "holdout": {"mean_net_pct": -1.0},
                               "stressed": {"mean_net_pct": -2.0}})
        for index, symbol in enumerate(("BTC-USDT-SWAP", "ADA-USDT-SWAP")):
            conn.execute("INSERT INTO prescreen_rejections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         ("r%d" % index, "run%d" % index, symbol, "15m", "p",
                          "deterministic_atlas", "deterministic", "REJECT", 0,
                          '["negative_net_expectancy"]', "measured", measured,
                          "2026-07-23 00:00:00"))
        conn.commit()
        rows = ecosystem._global_prescreen_evidence(conn)
        conn.close()
        check(len(rows) == 2 and len(set(row["evidence_id"] for row in rows)) == 2
              and len(set(row["experiment_id"] for row in rows)) == 2,
              "global death distillation collapsed independent targets")
    ecosystem.AUTO_DIR, ecosystem.DB_PATH = old_auto, old_db


def test_distilled_rules_are_navigation_not_cross_target_pruning():
    old_auto, old_db = ecosystem.AUTO_DIR, ecosystem.DB_PATH
    with tempfile.TemporaryDirectory() as temp:
        ecosystem.AUTO_DIR = Path(temp); ecosystem.DB_PATH = Path(temp)/"eco.db"
        conn = ecosystem._db()
        sources = [{"symbol": "ADA-USDT-SWAP", "timeframe": "15m",
                    "stage": "deterministic_atlas", "experiment_id": "run1"},
                   {"symbol": "BTC-USDT-SWAP", "timeframe": "15m",
                    "stage": "deterministic_atlas", "experiment_id": "run2"}]
        conn.execute("INSERT INTO distilled_death_rules VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("rule", "run", "R1", "负期望病理", "empirical_cost_failure",
                      "navigation_only", "navigation_approved", 2, 0, 2,
                      '["negative_net_expectancy"]', json.dumps(sources),
                      '{"logic_statement":"measured"}', '[]',
                      "2026-07-23 00:00:00", "2026-07-23 00:00:00"))
        conn.commit(); conn.close()
        result = ecosystem._distillation_rule_context(
            {"symbol": "ADA-USDT-SWAP", "timeframe": "15m"})
        check(result["rules"][0]["local_measured_support"] == 1
              and result["rules"][0]["local_independent_experiments"] == 1
              and result["rules"][0]["automatic_pruning_allowed"] is False,
              "cross-target distilled knowledge became an executable gate")
    ecosystem.AUTO_DIR, ecosystem.DB_PATH = old_auto, old_db


def test_strategy_lifecycle_downgrade_vault_and_cold_start():
    import auto_trade_strategy_lifecycle as lifecycle
    old = {
        "ROOT": lifecycle.ROOT,
        "AUTO_DIR": lifecycle.AUTO_DIR,
        "CONTROL_PATH": lifecycle.CONTROL_PATH,
        "FREQ_PATH": lifecycle.FREQ_PATH,
        "AUDIT_PATH": lifecycle.AUDIT_PATH,
        "SCORE_PATH": lifecycle.SCORE_PATH,
        "VAULT_PATH": lifecycle.VAULT_PATH,
        "REPLACE_PATH": lifecycle.REPLACE_PATH,
        "AUDIT_LOG": lifecycle.AUDIT_LOG,
        "STATUS_PATH": lifecycle.STATUS_PATH,
        "WEEKLY_PATH": lifecycle.WEEKLY_PATH,
        "HOUR_STATUS_PATH": lifecycle.HOUR_STATUS_PATH,
    }
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        auto = root / "auto_trade"
        auto.mkdir()
        lifecycle.ROOT = root
        lifecycle.AUTO_DIR = auto
        lifecycle.CONTROL_PATH = auto / "strategy_runtime_controls.json"
        lifecycle.FREQ_PATH = auto / "live_portfolio_frequency.json"
        lifecycle.AUDIT_PATH = auto / "live_strategy_retrospective_audit.json"
        lifecycle.SCORE_PATH = auto / "strategy_lifecycle_scores.json"
        lifecycle.VAULT_PATH = auto / "strategy_lifecycle_freeze_vault.json"
        lifecycle.REPLACE_PATH = auto / "strategy_lifecycle_replacements.json"
        lifecycle.AUDIT_LOG = auto / "strategy_lifecycle_audit.jsonl"
        lifecycle.STATUS_PATH = auto / "strategy_lifecycle_status.json"
        lifecycle.WEEKLY_PATH = auto / "strategy_lifecycle_weekly_report.json"
        lifecycle.HOUR_STATUS_PATH = auto / "strategy_hour_rotate_status.json"
        weak = "NG-USDT-SWAP|5m|weak_probe"
        strong = "NG-USDT-SWAP|5m|strong_paused"
        lifecycle._atomic(lifecycle.CONTROL_PATH, {
            "assignments": {
                weak: {
                    "symbol": "NG-USDT-SWAP", "timeframe": "5m",
                    "strategy_key": "weak_probe",
                    "audit_state": "conditional_frequency_probe",
                    "pause_new_entries": False, "max_grade": "B",
                    "max_position_ratio": 0.30,
                },
                strong: {
                    "symbol": "NG-USDT-SWAP", "timeframe": "5m",
                    "strategy_key": "strong_paused",
                    "audit_state": "conditional_frequency_probe",
                    "pause_new_entries": True, "max_grade": "C",
                    "max_position_ratio": 0.10, "backfill_eligible": True,
                    "ai_consensus_score": 9,
                },
                "BTC-USDT-SWAP|15m|shadow_keep": {
                    "audit_state": "failed_closed", "pause_new_entries": True,
                },
            }})
        lifecycle._atomic(lifecycle.FREQ_PATH, {"assignments": [
            {"symbol": "NG-USDT-SWAP", "timeframe": "5m",
             "strategy_key": "weak_probe", "trades": 22, "win_rate": 40.0,
             "return_pct": -12.0},
            {"symbol": "NG-USDT-SWAP", "timeframe": "5m",
             "strategy_key": "strong_paused", "trades": 30, "win_rate": 78.0,
             "return_pct": 120.0},
        ]})
        lifecycle._atomic(lifecycle.AUDIT_PATH, {"results": []})
        # Seed declining scores so ladder can also fire on history.
        lifecycle._atomic(lifecycle.SCORE_PATH, {"assignments": {
            weak: {"history": [
                {"at": "2026-07-23 15:00:00", "score": 70},
                {"at": "2026-07-23 16:00:00", "score": 60},
                {"at": "2026-07-23 17:00:00", "score": 50},
            ]}}})
        status = lifecycle.run_once(skip_ai=True)
        controls = json.loads(lifecycle.CONTROL_PATH.read_text(encoding="utf-8"))
        weak_row = controls["assignments"][weak]
        check(weak_row.get("audit_state") in (
            "eliminated_pending_archive", "read_only_shadow",
            "conditional_frequency_probe"),
              "weak probe was not lifecycle-managed")
        # Negative 20+ trade live return is a kill redline → vault.
        vault = json.loads(lifecycle.VAULT_PATH.read_text(encoding="utf-8"))
        check(weak in (vault.get("sealed") or {}),
              "kill redline did not seal weak probe into freeze vault")
        check(weak_row.get("pause_new_entries") is True,
              "eliminated probe still allows new entries")
        # Live-only kill: offline audit negativity alone must not vault.
        check(lifecycle._triple_cost_mean_negative(
            weak, "weak_probe",
            {"results": [{"assignment_id": weak, "evidence": {"backtests": {
                "observed_base": {"mean_net_return_pct": -9.0, "trades": 99}}}}]},
            {"trades": 5, "return_pct": 10.0})[0] is False,
              "offline audit negativity incorrectly triggered live kill redline")
        # Backfill should activate strong paused under cold-start.
        strong_row = controls["assignments"][strong]
        check(strong_row.get("pause_new_entries") is False
              and strong_row.get("audit_state") == "conditional_frequency_probe",
              "preemptive backfill did not activate replacement probe")
        check(int(strong_row.get("cold_start_trades_remaining") or 0) == 5,
              "replacement probe missing cold-start protection")
        check(abs(lifecycle.cold_start_position_ratio(strong_row, 0.10) - 0.05) < 1e-9,
              "cold-start ratio is not half of C-grade")
        check(status.get("natural_language"),
              "lifecycle status missing natural language summary")
        # Hard exclude remains blocked.
        check(not formal_daemon._runtime_control_decision(
            controls, {"enforce": True}, "BTC-USDT-SWAP|15m|shadow_keep"),
              "hard-excluded BTC15m unexpectedly allowed")
    for key, value in old.items():
        setattr(lifecycle, key, value)


def test_environment_admission_boundary_gate_and_split():
    import auto_trade_environment_admission as env
    old = {k: getattr(env, k) for k in (
        "ROOT", "AUTO_DIR", "CONTROL_PATH", "TELEMETRY_DB", "STATUS_PATH",
        "AUDIT_LOG", "BTC15_REPORT", "MISMATCH_PATH")}
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        auto = root / "auto_trade"
        auto.mkdir()
        env.ROOT = root
        env.AUTO_DIR = auto
        env.CONTROL_PATH = auto / "strategy_runtime_controls.json"
        env.TELEMETRY_DB = auto / "microstructure_telemetry.db"
        env.STATUS_PATH = auto / "environment_admission_status.json"
        env.AUDIT_LOG = auto / "environment_admission_audit.jsonl"
        env.BTC15_REPORT = auto / "btc15_environment_reassessment.json"
        env.MISMATCH_PATH = auto / "environment_mismatch_counters.json"
        # Tiny telemetry DB with one registered primitive.
        conn = env._connect()
        conn.executescript("""
        CREATE TABLE microstructure_primitive_clusters (
          cluster_id TEXT PRIMARY KEY, schema_version TEXT, fingerprint TEXT,
          state TEXT, label TEXT, description TEXT, sample_count INTEGER,
          centroid_json TEXT, exemplar_json TEXT, review_json TEXT,
          created_at TEXT, updated_at TEXT);
        CREATE TABLE microstructure_primitive_assignments (
          assignment_id TEXT PRIMARY KEY, run_id TEXT, window_id TEXT,
          symbol TEXT, end_ms INTEGER, cluster_id TEXT, distance REAL,
          assigned_at TEXT);
        """)
        now_ms = int(time.time() * 1000)
        conn.execute(
            "INSERT INTO microstructure_primitive_clusters VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("slow_micro_ng_aaa", "v", "fp", "descriptive_registered",
             "温和买流", "desc", 120, "{}", "{}", "{}", "t", "t"))
        conn.execute(
            "INSERT INTO microstructure_primitive_clusters VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("slow_micro_ng_bbb", "v", "fp2", "descriptive_registered",
             "净流出", "desc2", 90, "{}", "{}", "{}", "t", "t"))
        conn.execute(
            "INSERT INTO microstructure_primitive_assignments VALUES(?,?,?,?,?,?,?,?)",
            ("a1", "r", "w", "NG-USDT-SWAP", now_ms, "slow_micro_ng_aaa", 0.1, "t"))
        conn.commit(); conn.close()
        boundary = {
            "schema": "qiyu_env_boundary_v1", "final_approved": True,
            "any_of": ["slow_micro_ng_aaa"], "all_of": [],
            "none_of": ["slow_micro_ng_bbb"],
            "volatility_in": ["low", "mid"], "volatility_not_in": ["high"],
            "uses_outcome_labels": False,
        }
        logic = env.logic_audit_boundary(
            boundary, env.list_registered_primitives("NG-USDT-SWAP"))
        check(logic["ok"], "valid boundary failed logic audit")
        env_now = env.current_environment("NG-USDT-SWAP", candle_ms=now_ms)
        check(env_now.get("available"), "registered env unavailable")
        ok = env.matches_boundary(env_now, boundary)
        check(ok.get("ok") and ok.get("exact") is not False,
              "in-env match failed: %s" % ok)
        bad = env.matches_boundary(env_now, dict(boundary, any_of=["slow_micro_ng_bbb"]))
        # With approximate matching, any_of miss can become D-grade approx.
        check(bad.get("ok") and bad.get("approximate"),
              "near-env any_of miss should allow approximate D-grade: %s" % bad)
        check(abs(float(bad.get("max_position_ratio") or 0) - 0.065) < 1e-9,
              "approximate ratio is not D-grade 6.5%%")
        hard = env.matches_boundary(
            env_now, dict(boundary, any_of=["slow_micro_ng_bbb"]),
            allow_approximate=False)
        check(not hard.get("ok"), "hard matcher incorrectly allowed out-of-env")
        forbid = env.matches_boundary(
            env_now, dict(boundary, none_of=["slow_micro_ng_aaa"]))
        check(not forbid.get("ok"), "forbidden primitive incorrectly approximated")
        split = env.split_in_out_env_metrics([
            {"entry_ms": now_ms, "pnl_ratio": 0.01},
            {"entry_ms": now_ms - 3600 * 1000, "pnl_ratio": -0.02},
        ], boundary, "NG-USDT-SWAP")
        check(split["in_environment"]["trades"] >= 1,
              "in-env metrics missing trades")
        check("只记录不否决" in split["policy"],
              "split policy missing out-of-env non-veto rule")
        # Runtime gate fail-closed without bound boundary.
        env._atomic(env.CONTROL_PATH, {"assignments": {
            "NG-USDT-SWAP|5m|demo": {"symbol": "NG-USDT-SWAP"}}})
        gate = env.runtime_gate("NG-USDT-SWAP|5m|demo", symbol="NG-USDT-SWAP",
                                candle_ms=now_ms)
        check(not gate.get("ok"), "missing boundary did not fail closed")
        # Bound + approx path via runtime_gate.
        env._atomic(env.CONTROL_PATH, {"assignments": {
            "NG-USDT-SWAP|5m|demo": {
                "symbol": "NG-USDT-SWAP",
                "environment_boundary": dict(boundary, any_of=["slow_micro_ng_bbb"]),
            }}})
        gate2 = env.runtime_gate("NG-USDT-SWAP|5m|demo", symbol="NG-USDT-SWAP",
                                 candle_ms=now_ms)
        check(gate2.get("ok") and gate2.get("approximate"),
              "runtime gate did not allow approximate open")
    for key, value in old.items():
        setattr(env, key, value)


def test_strategy_ecosystem_self_evolution_loop():
    import auto_trade_strategy_evolution as evo
    import auto_trade_strategy_dsl as dsl
    import auto_trade_shadow_validator as shadow
    old = {k: getattr(evo, k) for k in (
        "ROOT", "AUTO_DIR", "CONTROL_PATH", "FREQ_PATH", "SCORE_PATH",
        "VAULT_PATH", "MISMATCH_PATH", "AUDIT_LOG", "STATUS_PATH",
        "PORTFOLIO_PATH", "MUTATION_QUEUE", "TRANSFER_QUEUE", "REVIVE_PATH",
        "ECO_DB")}
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        auto = root / "auto_trade"
        auto.mkdir()
        evo.ROOT = root
        evo.AUTO_DIR = auto
        evo.CONTROL_PATH = auto / "strategy_runtime_controls.json"
        evo.FREQ_PATH = auto / "live_portfolio_frequency.json"
        evo.SCORE_PATH = auto / "strategy_lifecycle_scores.json"
        evo.VAULT_PATH = auto / "strategy_lifecycle_freeze_vault.json"
        evo.MISMATCH_PATH = auto / "environment_mismatch_counters.json"
        evo.AUDIT_LOG = auto / "strategy_evolution_audit.jsonl"
        evo.STATUS_PATH = auto / "strategy_evolution_status.json"
        evo.PORTFOLIO_PATH = auto / "strategy_portfolio_evolution.json"
        evo.MUTATION_QUEUE = auto / "strategy_evolution_mutation_queue.json"
        evo.TRANSFER_QUEUE = auto / "strategy_evolution_transfer_queue.json"
        evo.REVIVE_PATH = auto / "strategy_evolution_revive_queue.json"
        evo.ECO_DB = auto / "strategy_ecosystem.db"

        boundary = {
            "any_of": ["slow_micro_ng_aaa", "slow_micro_ng_bbb"],
            "none_of": ["slow_micro_ng_ccc"],
            "volatility_in": ["mid"],
            "natural_language": "NG卖压衰竭环境",
        }
        strong = "NG-USDT-SWAP|5m|strong_niche"
        weak = "NG-USDT-SWAP|5m|weak_niche"
        survivor = "XAU-USDT-SWAP|15m|xau_survivor"
        evo._atomic(evo.CONTROL_PATH, {"assignments": {
            strong: {
                "symbol": "NG-USDT-SWAP", "timeframe": "5m",
                "strategy_key": "strong_niche",
                "audit_state": "conditional_frequency_probe",
                "pause_new_entries": False, "max_grade": "B",
                "lifecycle_grade": "B", "max_position_ratio": 0.30,
                "environment_boundary": boundary,
                "rotation_score": 80,
            },
            weak: {
                "symbol": "NG-USDT-SWAP", "timeframe": "5m",
                "strategy_key": "weak_niche",
                "audit_state": "conditional_frequency_probe",
                "pause_new_entries": False, "max_grade": "C",
                "lifecycle_grade": "C", "max_position_ratio": 0.10,
                "environment_boundary": boundary,
                "rotation_score": 40,
            },
            survivor: {
                "symbol": "XAU-USDT-SWAP", "timeframe": "15m",
                "strategy_key": "xau_survivor",
                "audit_state": "conditional_frequency_probe",
                "pause_new_entries": False, "max_grade": "B",
                "lifecycle_grade": "B", "max_position_ratio": 0.30,
                "environment_boundary": {
                    "any_of": ["slow_micro_xau_1"], "none_of": [],
                    "natural_language": "黄金突破环境",
                },
            },
        }})
        evo._atomic(evo.FREQ_PATH, {"assignments": [
            {"symbol": "NG-USDT-SWAP", "timeframe": "5m",
             "strategy_key": "strong_niche", "trades": 20, "win_rate": 72.0,
             "return_pct": 40.0},
            {"symbol": "NG-USDT-SWAP", "timeframe": "5m",
             "strategy_key": "weak_niche", "trades": 18, "win_rate": 55.0,
             "return_pct": 5.0},
            {"symbol": "XAU-USDT-SWAP", "timeframe": "15m",
             "strategy_key": "xau_survivor", "trades": 16, "win_rate": 70.0,
             "return_pct": 55.0},
        ]})
        # Absolute edge evidence via retrospective audit (triple-cost mean > 0).
        evo._atomic(auto / "live_strategy_retrospective_audit.json", {"results": [
            {"assignment_id": strong, "evidence": {"backtests": {
                "triple_actual": {"mean_net_return_pct": 0.12, "trades": 40}}}},
            {"assignment_id": weak, "evidence": {"backtests": {
                "triple_actual": {"mean_net_return_pct": -0.05, "trades": 40}}}},
            {"assignment_id": survivor, "evidence": {"backtests": {
                "triple_actual": {"mean_net_return_pct": 0.2, "trades": 30}}}},
        ]})
        # Negative-edge niche must vacate rather than crown a loser.
        vac_a = "BTC-USDT-SWAP|1h|neg_a"
        vac_b = "BTC-USDT-SWAP|1h|neg_b"
        vac_boundary = {
            "any_of": ["slow_micro_btc_x"], "none_of": [],
            "natural_language": "负期望生态位",
        }
        controls0 = json.loads(evo.CONTROL_PATH.read_text(encoding="utf-8"))
        controls0["assignments"][vac_a] = {
            "symbol": "BTC-USDT-SWAP", "timeframe": "1h",
            "strategy_key": "neg_a",
            "audit_state": "conditional_frequency_probe",
            "pause_new_entries": False, "max_grade": "C",
            "environment_boundary": vac_boundary, "rotation_score": 30,
        }
        controls0["assignments"][vac_b] = {
            "symbol": "BTC-USDT-SWAP", "timeframe": "1h",
            "strategy_key": "neg_b",
            "audit_state": "conditional_frequency_probe",
            "pause_new_entries": False, "max_grade": "C",
            "environment_boundary": vac_boundary, "rotation_score": 20,
        }
        evo._atomic(evo.CONTROL_PATH, controls0)
        audit_doc = json.loads((auto / "live_strategy_retrospective_audit.json").read_text())
        audit_doc["results"].extend([
            {"assignment_id": vac_a, "evidence": {"backtests": {
                "triple_actual": {"mean_net_return_pct": -0.2, "trades": 30}}}},
            {"assignment_id": vac_b, "evidence": {"backtests": {
                "triple_actual": {"mean_net_return_pct": -0.1, "trades": 30}}}},
        ])
        evo._atomic(auto / "live_strategy_retrospective_audit.json", audit_doc)
        evo._atomic(evo.SCORE_PATH, {"assignments": {
            strong: {"history": [{"at": "t", "score": 80}]},
            weak: {"history": [{"at": "t", "score": 40}]},
            survivor: {"history": [{"at": "t", "score": 75}]},
            vac_a: {"history": [{"at": "t", "score": 30}]},
            vac_b: {"history": [{"at": "t", "score": 20}]},
        }})
        evo._atomic(evo.VAULT_PATH, {"sealed": {
            "BTC-USDT-SWAP|5m|dead_probe": {
                "auto_revive_forbidden": True,
                "reasons": ["近20笔三倍摩擦后净均值为负"],
                "allow_microstructure_revive_review": True,
            }}})
        evo._atomic(evo.MISMATCH_PATH, {"counters": {
            weak: {"streak": 8, "last_reason": "out_of_boundary"},
            strong: {"streak": 0, "last_reason": "in_env"},
        }})
        # Seed near-miss candidate for micro-mutation.
        conn = sqlite3.connect(str(evo.ECO_DB))
        conn.execute(
            "CREATE TABLE candidates ("
            "candidate_hash TEXT PRIMARY KEY, strategy_key TEXT NOT NULL,"
            "symbol TEXT, timeframe TEXT, state TEXT NOT NULL,"
            "candidate_json TEXT NOT NULL, evidence_json TEXT,"
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        sample_dsl = {
            "schema": "qiyu_strategy_dsl_v1",
            "key": "near_miss_parent",
            "direction": "short",
            "timeframe": "5m",
            "supported_instruments": ["NG-USDT-SWAP"],
            "max_hold_bars": 24,
            "entry": {"all": [
                {"id": "e1", "left": {"feature": "z20"}, "op": "gte",
                 "right": {"value": 1.5}},
                {"id": "e2", "left": {"feature": "rsi14"}, "op": "gte",
                 "right": {"value": 65}},
            ]},
            "exit": {"any": [
                {"id": "x1", "left": {"feature": "rsi14"}, "op": "lte",
                 "right": {"value": 45}},
            ]},
        }
        sample_dsl = dsl.validate_strategy(sample_dsl)
        cand = {
            "strategy_key": "near_miss_parent",
            "symbol": "NG-USDT-SWAP",
            "timeframe": "5m",
            "dsl": sample_dsl,
        }
        evid = {
            "passed": False,
            "gates": {"a": True, "b": True, "c": False},
            "runs": {"candidate": {"0.009": {
                "trades": 12, "win_rate_pct": 58.0, "expectancy_pct": 0.05}}},
        }
        now = "2026-07-23 12:00:00"
        conn.execute(
            "INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?,?)",
            ("parenthash", "near_miss_parent", "NG-USDT-SWAP", "5m",
             "deterministic_rejected",
             json.dumps(cand), json.dumps(evid), now, now))
        conn.commit()
        conn.close()

        check(evo.env_fit_score(weak) < evo.env_fit_score(strong),
              "env fit score did not penalize mismatch streak")
        status = evo.run_once(skip_ai=True, include_revive=True,
                              include_mutations=True)
        controls = json.loads(evo.CONTROL_PATH.read_text(encoding="utf-8"))
        weak_row = controls["assignments"][weak]
        strong_row = controls["assignments"][strong]
        check(weak_row.get("lifecycle_shadow") is True
              and weak_row.get("pause_new_entries") is True,
              "niche loser was not demoted to read-only shadow")
        check(strong_row.get("niche_competition_won_at"),
              "niche winner missing win stamp")
        check(weak_row.get("niche_competition_winner") == strong,
              "niche loser did not record winner")
        controls2 = json.loads(evo.CONTROL_PATH.read_text(encoding="utf-8"))
        check(controls2["assignments"][vac_a].get("lifecycle_shadow") is True
              and controls2["assignments"][vac_b].get("lifecycle_shadow") is True,
              "negative-edge niche was not vacated")
        check(len(status.get("niche", {}).get("vacated") or []) >= 1,
              "vacated niches missing from status")
        # skip_ai must not grant revive / live.
        vault = json.loads(evo.VAULT_PATH.read_text(encoding="utf-8"))
        check("BTC-USDT-SWAP|5m|dead_probe" in (vault.get("sealed") or {}),
              "skip-ai incorrectly unsealed vaulted strategy")
        check((controls["assignments"].get("BTC-USDT-SWAP|5m|dead_probe") or {})
              .get("audit_state") != "conditional_frequency_probe",
              "revive incorrectly granted live probe without 3AI")
        mutations = status.get("mutations") or {}
        check(len(mutations.get("mutations") or []) >= 1,
              "near-miss micro-mutation was not generated")
        mut_state = sqlite3.connect(str(evo.ECO_DB)).execute(
            "SELECT state FROM candidates WHERE state='evolution_micro_mutation'"
        ).fetchone()
        check(mut_state and mut_state[0] == "evolution_micro_mutation",
              "mutation candidate missing evolution_micro_mutation state")
        portfolio = json.loads(evo.PORTFOLIO_PATH.read_text(encoding="utf-8"))
        check("portfolio_sharpe_proxy" in portfolio
              and "complementary_pairs" in portfolio,
              "portfolio evolution metrics missing")
        transfers = json.loads(evo.TRANSFER_QUEUE.read_text(encoding="utf-8"))
        check(len(transfers.get("items") or []) >= 1
              and float((transfers["items"][0]).get("shadow_days_required")) == 5.0
              and (transfers["items"][0]).get("requires_full_re_audit") is True,
              "cross-cluster transfer missing 5d shadow / full re-audit flags")
        check(status.get("sat_witness") is not None,
              "SAT_WITNESS heartbeat missing from evolution status")
        # Transfer short shadow helper.
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE candidates ("
            "candidate_hash TEXT PRIMARY KEY, candidate_json TEXT, evidence_json TEXT)")
        conn.execute(
            "INSERT INTO candidates VALUES(?,?,?)",
            ("xfer1", json.dumps({
                "origin": {"transfer_short_shadow": True,
                           "shadow_days_required": 5}}), "{}"))
        days = shadow._required_shadow_days(
            conn, "xfer1",
            json.dumps({"transfer_short_shadow": True, "shadow_days_required": 5}))
        check(abs(days - 5.0) < 1e-9,
              "transfer shadow days not shortened to 5")
        days7 = shadow._required_shadow_days(conn, "missing", "{}")
        check(abs(days7 - 7.0) < 1e-9,
              "default shadow days drifted from 7")
        conn.close()
        check("生态" in (status.get("natural_language") or ""),
              "evolution status missing natural language")
        # Hackathon dossier builds without AI.
        import auto_trade_strategy_hackathon as hack
        old_h = {k: getattr(hack, k) for k in (
            "ROOT", "AUTO_DIR", "CONTROL_PATH", "FREQ_PATH", "MISMATCH_PATH",
            "VAULT_PATH", "ECO_DB", "AUDIT_LOG", "STATUS_PATH", "DOSSIER_PATH",
            "RESULT_PATH")}
        try:
            hack.ROOT = root
            hack.AUTO_DIR = auto
            hack.CONTROL_PATH = evo.CONTROL_PATH
            hack.FREQ_PATH = evo.FREQ_PATH
            hack.MISMATCH_PATH = evo.MISMATCH_PATH
            hack.VAULT_PATH = evo.VAULT_PATH
            hack.ECO_DB = evo.ECO_DB
            hack.AUDIT_LOG = auto / "strategy_hackathon_audit.jsonl"
            hack.STATUS_PATH = auto / "strategy_hackathon_status.json"
            hack.DOSSIER_PATH = auto / "strategy_hackathon_dossier.json"
            hack.RESULT_PATH = auto / "strategy_hackathon_latest.json"
            dossier = hack.build_dossier(days=7)
            check(dossier.get("live_performance") is not None,
                  "hackathon dossier missing live performance")
            dry = hack.run_once(skip_ai=True)
            check(dry.get("skipped_ai") is True, "hackathon skip-ai failed")
        finally:
            for key, value in old_h.items():
                setattr(hack, key, value)
    for key, value in old.items():
        setattr(evo, key, value)


if __name__ == "__main__":
    test_catalog_and_convergence()
    test_dsl_and_schema()
    test_live_registry_coverage()
    test_iteration_information_gain_gate()
    test_creation_capacity_planner()
    test_compute_budget_sleep_and_ai_rescue_gate()
    test_mandatory_live_cognitive_gate_fails_closed()
    test_solvability_alarm_uses_natural_language()
    test_solvability_alarm_is_dry_run_outside_production_auto_dir()
    test_early_prune_uses_short_then_long_sleep()
    test_account_return_and_grade_ratio_are_not_double_scaled()
    test_cost_probability_causality_and_meta_pruning()
    test_branch_surrogate_budget_and_depth_math()
    test_active_hunt_consensus_is_unlabeled_and_vetoed()
    test_active_hunt_positive_promotion_requires_shadow()
    test_active_hunt_probe_memory_updates_prior()
    test_shadow_runner_is_read_only_when_empty()
    test_prescreen_death_map_blocks_unanswered_repeat()
    test_prescreen_death_archive_separates_stages()
    test_cached_ai_panel_recomputes_current_death_memory()
    test_outcome_free_micro_primitive_discovery()
    test_shadow_environment_snapshot_schema()
    test_death_micro_association_is_descriptive_only()
    test_strategy_lifecycle_downgrade_vault_and_cold_start()
    test_forward_death_micro_alignment_without_outcome()
    test_system_solvability_witness_and_cached_audit()
    test_prescreen_rerun_does_not_multiply_evidence()
    test_cognitive_blank_is_search_priority_not_gate_relaxation()
    test_operator_lattice_is_dormant_without_local_cost_survivor()
    test_global_death_evidence_keeps_target_boundary()
    test_distilled_rules_are_navigation_not_cross_target_pruning()
    test_environment_admission_boundary_gate_and_split()
    test_strategy_ecosystem_self_evolution_loop()
    print(json.dumps({"ok": True,
                      "tests": ["provider_payload_safety", "catalog_convergence",
                                "dsl_sandbox", "dsl_exit_semantics",
                                "exact_hash_promotion", "database_schema",
                                "strategy_creation_display",
                                "live_registry_coverage",
                                "iteration_information_gain_gate",
                                "creation_capacity_planner",
                                "compute_budget_sleep_ai_rescue_gate",
                                "mandatory_live_cognitive_gate_fail_closed",
                                "solvability_alarm_natural_language",
                                "solvability_alarm_test_isolation",
                                "early_prune_short_then_long_sleep",
                                "account_return_and_direct_grade_ratio",
                                "cost_probability_causal_meta_pruning",
                                "branch_surrogate_budget_depth",
                                "active_hunt_unlabeled_consensus_veto",
                                "active_hunt_seven_day_shadow_gate",
                                "active_hunt_probe_memory_prior",
                                "empty_shadow_runner_read_only",
                                "prescreen_death_map_countermeasure_gate",
                                "prescreen_death_archive_stage_separation",
                                "cached_panel_dynamic_death_memory",
                                "outcome_free_micro_primitive_discovery",
                                "shadow_environment_snapshot",
                                "death_micro_descriptive_association",
                                "forward_death_micro_alignment_no_outcome",
                                "system_solvability_witness_and_cache",
                                "prescreen_rerun_evidence_deduplication",
                                "solvability_material_change_heartbeat",
                                "cognitive_blank_search_priority_only",
                                "operator_lattice_target_local_dormancy",
                                "global_death_evidence_target_boundary",
                                "distilled_rule_navigation_only",
                                "strategy_lifecycle_downgrade_vault_cold_start",
                                "environment_admission_boundary_gate_split",
                                "strategy_ecosystem_self_evolution_loop"]},
                     sort_keys=True))
