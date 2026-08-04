# -*- coding: utf-8 -*-
"""STEP A unit/acceptance tests — local, no live trading side effects."""
from __future__ import print_function

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_mechanism_spec_normalize_and_aliases():
    from dual_engine_workflow_v2.mechanism_spec import normalize_mechanism_spec, spec_to_mechanism_statement
    raw = {
        "title": "t",
        "counterparty": "late longs",
        "forced_actor": "liquidated longs",
        "inefficiency": "cascade vacuum",
        "market_inefficiency": "cascade vacuum then reclaim",
        "why_edge_exists": "mechanical stops",
        "persistence_reason": "mechanical stops",
        "edge_decay_conditions": "depth recovers",
        "required_market_regime": "high vol thin book",
        "entry_logic": "short after cascade stall",
        "exit_logic": "depth normalizes",
        "stop_logic": "0.9% SL",
        "take_profit_logic": "vacuum mid",
        "invalidation_logic": "no cascade",
        "non_negotiable_rules": ["no RSI swap"],
        "tunable_parameters": ["lookback"],
        "forbidden_transformations": ["rsi replace"],
        "expected_trade_frequency_class": "0.5_to_1",
        "expected_holding_period": "5_bars",
        "mechanism_name": "liquidity_vacuum_reclaim",
        "mechanism_family": "liquidity_vacuum_reclaim",
        "suitable_symbols": ["SOL-USDT-SWAP"],
        "suitable_timeframes": ["5m"],
    }
    ok, errors, cleaned = normalize_mechanism_spec(raw, focus={"symbol": "SOL-USDT-SWAP", "timeframe": "5m"})
    assert ok, errors
    assert cleaned["mechanism_id"]
    stmt = spec_to_mechanism_statement(cleaned)
    assert stmt["counterparty"]
    assert stmt["forbidden_substitutions"]
    print("PASS test_mechanism_spec_normalize_and_aliases")


def test_immutable_spec_refuse_overwrite(tmp_path=None):
    import dual_engine_workflow_v2.step_a_config as sc
    import dual_engine_workflow_v2.mechanism_spec as ms
    td = Path(tempfile.mkdtemp())
    sc.MECHANISM_SPEC_DIR = td
    sc.FIDELITY_DIFF_DIR = td / "fidelity"
    sc.FAILURE_RECORD_DIR = td / "fail"
    sc.GATES_DIR = td / "gates"
    sc.SPLIT_TESTS_DIR = td / "split"
    sc.ARTIFACTS_DIR = td / "art"
    sc.ensure_step_a_dirs()
    spec = {
        "mechanism_id": "mech_test_1",
        "mechanism_name": "t",
        "mechanism_family": "fam",
        "market_inefficiency": "x",
        "counterparty_source": "y",
        "why_edge_exists": "z",
        "edge_decay_conditions": "d",
        "required_market_regime": "r",
        "entry_logic": "e",
        "exit_logic": "x",
        "stop_logic": "s",
        "take_profit_logic": "tp",
        "invalidation_logic": "i",
        "non_negotiable_rules": ["a"],
        "tunable_parameters": ["b"],
        "forbidden_transformations": ["c"],
        "expected_trade_frequency_class": "low",
        "expected_holding_period": "5",
        "suitable_symbols": ["SOL"],
        "suitable_timeframes": ["5m"],
    }
    ms.save_immutable_mechanism_spec(spec, task_id="t1")
    spec2 = dict(spec)
    spec2["entry_logic"] = "CHANGED"
    try:
        ms.save_immutable_mechanism_spec(spec2, task_id="t2")
        raise AssertionError("should refuse overwrite")
    except ms.MechanismSpecError:
        pass
    print("PASS test_immutable_spec_refuse_overwrite")


def test_split_tests_20_structure():
    from dual_engine_workflow_v2.split_tests_20 import run_split_tests_20, SPLIT_TEST_IDS
    trades = [{"net_pnl_pct": 0.01 if i % 3 else -0.005, "hour": i % 24} for i in range(30)]
    base = {"mean_net": 0.004, "sharpe": 0.8, "trades": 30, "win_rate_pct": 55}
    dsl = {"entry": {"all": [{"left": {"feature": "atr_pct"}, "op": "gt", "right": {"value": 0.001}}]},
           "exit": {"any": [{"left": {"feature": "atr_pct"}, "op": "lt", "right": {"value": 0.0005}}]}}
    spec = {"stop_logic": "0.9%", "exit_logic": "x", "take_profit_logic": "tp",
            "non_negotiable_rules": ["no rsi"]}

    def bt_fn(d):
        return {"metrics": base, "trades": trades}

    def fr_fn(**kw):
        return {"metrics": {"mean_net": 0.001, "sharpe": 0.3, "trades": 25}}

    summary = run_split_tests_20(
        definition=dsl, base_metrics=base, trades=trades,
        backtest_fn=bt_fn, mechanism_spec=spec, fidelity_diff={"pass": True, "feature_set": ["atr_pct"]},
        friction_fn=fr_fn,
    )
    assert summary["n_tests"] == 20
    ids = [r["test_id"] for r in summary["results"]]
    assert ids == list(SPLIT_TEST_IDS)
    for r in summary["results"]:
        assert r["status"] in ("PASS", "FAIL", "INCONCLUSIVE")
        assert "inputs" in r and "threshold" in r and "actual" in r
    print("PASS test_split_tests_20_structure")


def test_gates_sequential_and_no_avg():
    from dual_engine_workflow_v2 import gates
    g0 = gates.evaluate_gate0({"counterparty_source": "x", "edge_decay_conditions": "y",
                               "entry_logic": "z", "mechanism_family": "f"}, True)
    assert g0["pass"]
    g6 = gates.evaluate_gate6({
        "glm_mechanism": {"pass": True},
        "codex_fidelity": {"pass": True},
        "deepseek_logic": {"pass": False},
        "production_risk": {"pass": True},
    })
    assert g6["pass"] is False
    assert g6["evidence"]["averaged_score_forbidden"] is True
    g7 = gates.evaluate_gate7({
        "pending_ok": True, "awaiting_human": True,
        "human_confirmed": False, "auto_open_mounted": False,
    })
    assert g7["pass"]
    g7b = gates.evaluate_gate7({
        "pending_ok": True, "human_confirmed": False, "auto_open_mounted": True,
    })
    assert g7b["pass"] is False
    payload = gates.assemble_gate_results([g0, g6], task_id="t")
    assert payload["all_gates_pass"] is False
    assert payload["production_mounted"] is False
    print("PASS test_gates_sequential_and_no_avg")


def test_duplicate_exhaustion_block():
    from dual_engine_workflow_v2.step_a_fingerprint import build_step_a_fingerprint, duplicate_intercept
    spec = {
        "mechanism_id": "x", "mechanism_family": "exhaustion_fade_short",
        "market_inefficiency": "fade", "counterparty_source": "late",
        "entry_logic": "fade short", "exit_logic": "cover",
        "required_market_regime": "exhaust", "expected_holding_period": "5",
        "suitable_symbols": ["SOL"], "suitable_timeframes": ["5m"],
    }
    fp = build_step_a_fingerprint(spec, direction="short")
    blocked, report = duplicate_intercept(fp, [], mechanism_family="exhaustion_fade_short",
                                          allow_horizontal_expand=False)
    assert blocked
    assert report.get("is_exhaustion_fade_clone")
    print("PASS test_duplicate_exhaustion_block")


def test_failure_kb_preread_and_record():
    import dual_engine_workflow_v2.step_a_config as sc
    import dual_engine_workflow_v2.failure_kb as fkb
    td = Path(tempfile.mkdtemp())
    sc.FAILURE_RECORD_DIR = td
    sc.FAILURE_KB_PATH = td / "failure_knowledgebase.json"
    sc.MECHANISM_SPEC_DIR = td / "specs"
    sc.FIDELITY_DIFF_DIR = td / "fid"
    sc.GATES_DIR = td / "gates"
    sc.SPLIT_TESTS_DIR = td / "split"
    sc.ARTIFACTS_DIR = td / "art"
    sc.ensure_step_a_dirs()
    rec = fkb.build_failure_record(
        task_id="t_fail", mechanism_spec={"mechanism_id": "m", "mechanism_family": "dead_fam"},
        stage="gate4", failed_tests=["random_signal_control"], failure_reason="no edge",
        is_engineering=False, is_cost=False, is_data=False, is_mechanism_absent=True,
        mechanism_drift=False, repair_count=3, final_verdict="dead",
        reusable_lessons=["do not retry dead_fam"], blocked_paths=["family|dead_fam"],
        counterexamples=["random equals original"],
    )
    fkb.save_failure_record(rec)
    ctx = fkb.kb_context_for_ai()
    assert ctx["must_read"] is True
    assert "family|dead_fam" in ctx["blocked_paths"] or "dead_fam" in ctx["blocked_families"]
    blocked, why = fkb.path_is_blocked("family|dead_fam", family="dead_fam")
    assert blocked
    print("PASS test_failure_kb_preread_and_record")


def test_split_scores_ai_not_live_ready():
    from dual_engine_workflow_v2.scores_split import build_split_scores
    s = build_split_scores(ai_logic_wr=80, ai_n=5, backtest_wr=60, backtest_n=40)
    assert s["live_ready"] is False
    assert s["warning"] == "ai_wr_ge_75_but_not_live_ready"
    assert s["backtest_wr"]["sample_size"] == 40
    print("PASS test_split_scores_ai_not_live_ready")


def test_fidelity_diff_round():
    from dual_engine_workflow_v2.fidelity_diff import build_fidelity_diff
    spec = {
        "mechanism_id": "m", "mechanism_family": "fam",
        "entry_logic": "atr volume cascade", "exit_logic": "atr drop",
        "stop_logic": "0.9%", "take_profit_logic": "mid",
        "non_negotiable_rules": ["must observe cascade"],
    }
    dsl = {
        "entry": {"all": [
            {"left": {"feature": "atr_pct"}, "op": "gt", "right": {"value": 0.001}},
            {"left": {"feature": "volume_z"}, "op": "gt", "right": {"value": 0.5}},
        ]},
        "exit": {"any": [{"left": {"feature": "atr_pct"}, "op": "lt", "right": {"value": 0.0005}}]},
    }
    d0 = build_fidelity_diff(spec, dsl, round_i=0)
    assert "pass" in d0
    dsl2 = copy_dsl_add(dsl)
    d1 = build_fidelity_diff(spec, dsl2, round_i=1, prior_diff=d0)
    assert isinstance(d1.get("feature_drift_vs_prior"), list)
    print("PASS test_fidelity_diff_round")


def copy_dsl_add(dsl):
    import copy
    d = copy.deepcopy(dsl)
    d["entry"]["all"].append({"left": {"feature": "range_compression"}, "op": "gt", "right": {"value": 0.2}})
    return d


def main():
    test_mechanism_spec_normalize_and_aliases()
    test_immutable_spec_refuse_overwrite()
    test_split_tests_20_structure()
    test_gates_sequential_and_no_avg()
    test_duplicate_exhaustion_block()
    test_failure_kb_preread_and_record()
    test_split_scores_ai_not_live_ready()
    test_fidelity_diff_round()
    print("ALL_STEP_A_UNIT_TESTS_PASSED")


if __name__ == "__main__":
    main()
