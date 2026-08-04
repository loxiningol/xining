#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit + acceptance tests for workflow v2 (real logic, constructed cases)."""
from __future__ import print_function

import copy
import json
import os
import sys
import tempfile
import traceback

ROOT = os.environ.get("VECTOR_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
# Also allow package next to this file's parent
HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("[PASS]", name)
    else:
        FAIL.append((name, detail))
        print("[FAIL]", name, detail)


def sample_statement(**overrides):
    base = {
        "forced_actor": "forced liquidators after cascade margin calls",
        "observable_behavior": "burst of aggressive sell prints with book thinning",
        "predictable_distortion": "temporary downside overshoot then partial reclaim",
        "persistence_reason": "inventory rebuild lag on makers after toxicity",
        "counterparty": "late momentum chasers providing exit liquidity",
        "activation_regime": "high realized vol session handoff",
        "invalidation_regime": "deep book recovery within one bar",
        "causal_entry": "enter short when aggressive sell run coincides with atr expansion",
        "causal_exit": "exit when sell aggression fades and atr contracts",
        "forbidden_substitutions": ["rsi mean reversion", "macd cross", "bollinger fade"],
    }
    base.update(overrides)
    return base


def sample_dsl(with_rsi=False):
    leaves = [
        {"left": {"feature": "atr_pct"}, "op": "gt", "right": {"value": 0.002}},
        {"left": {"feature": "volume_z"}, "op": "gt", "right": {"value": 0.8}},
    ]
    if with_rsi:
        leaves.append({"left": {"feature": "rsi"}, "op": "lt", "right": {"value": 30}})
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": "wf_test_sample_1",
        "name": "test",
        "direction": "short",
        "timeframe": "5m",
        "supported_instruments": ["SOL-USDT-SWAP"],
        "entry": {"all": leaves},
        "exit": {"any": [{"left": {"feature": "atr_pct"}, "op": "lt", "right": {"value": 0.0005}}]},
        "max_hold_bars": 20,
        "description": "test",
        "origin": "unit",
        "version": 1,
        "live_enabled": False,
        "auto_trade_eligible": False,
    }


def test_protocol_and_statement():
    from dual_engine_workflow_v2.protocol import (
        validate_mechanism_statement, envelope, validate_envelope, content_hash,
    )
    ok, err, cleaned = validate_mechanism_statement(sample_statement())
    check("A1_statement_complete", ok, str(err))
    ok2, err2, _ = validate_mechanism_statement(sample_statement(forced_actor=""))
    check("A1b_statement_rejects_empty", not ok2 and any("forced_actor" in e for e in err2), str(err2))
    env = envelope("glm", {"x": 1}, purpose="test")
    validate_envelope(env, expect_role="glm")
    check("protocol_hash", env["content_hash"] == content_hash({"x": 1}))


def test_acceptance_a_fidelity_rsi():
    """Acceptance A: Codex injects RSI → fidelity rejects / removes."""
    from dual_engine_workflow_v2.fidelity import rule_based_condition_audit, apply_audit_removals
    dsl = sample_dsl(with_rsi=True)
    stmt = sample_statement()
    audit = rule_based_condition_audit(dsl, stmt, approved_supplements=[])
    check("A_unapproved_rsi_detected", audit.get("reject") is True, str(audit.get("reject_reasons")))
    check("A_rsi_classified",
          any(a.get("feature") == "rsi" and a.get("classification") in (
              "unapproved_filter", "mechanism_substitution")
              for a in audit.get("condition_audit") or []),
          json.dumps(audit.get("condition_audit"), ensure_ascii=False)[:400])
    new_dsl, removed = apply_audit_removals(dsl, audit)
    check("A_rsi_removed_or_reject", "rsi" in removed or audit.get("reject"), str(removed))


def test_acceptance_b_necessity_random():
    """Acceptance B: pseudo strategy — randomize ~ base → fail necessity."""
    from dual_engine_workflow_v2.tests_abc import run_necessity_tests

    base_m = {
        "mean_net": 0.01, "sharpe": 1.2, "oos_profit": 0.02, "profit_factor": 1.5,
        "max_dd": 0.05, "trades": 40, "fold_positive": 7, "folds": 10, "win_rate_pct": 60,
    }

    def bt(defn):
        # Ignore definition — always return similar metrics (no information)
        return {"metrics": dict(base_m)}

    nec = run_necessity_tests(sample_dsl(), base_m, bt, core_features=["atr_pct"])
    check("B_necessity_fails_pseudo", nec.get("pass") is False, str(nec.get("reasons")))


def test_acceptance_c_isolated_peak():
    """Acceptance C: only one param point profitable → high overfit, no auto pass."""
    from dual_engine_workflow_v2.tests_abc import run_parameter_stability

    def bt(defn):
        # score high only when atr threshold exactly 0.002
        try:
            v = defn["entry"]["all"][0]["right"]["value"]
        except Exception:
            v = None
        if v is not None and abs(float(v) - 0.002) < 1e-9:
            m = {"mean_net": 0.02, "sharpe": 2, "oos_profit": 0.03, "profit_factor": 2,
                 "max_dd": 0.02, "trades": 30, "fold_positive": 8, "folds": 10, "win_rate_pct": 70}
        else:
            m = {"mean_net": -0.01, "sharpe": -0.5, "oos_profit": -0.02, "profit_factor": 0.5,
                 "max_dd": 0.1, "trades": 5, "fold_positive": 1, "folds": 10, "win_rate_pct": 40}
        return {"metrics": m}

    stab = run_parameter_stability(sample_dsl(), bt, step=0.10, span=0.50)
    check("C_high_overfit_flagged", stab.get("high_overfit_risk") is True or stab.get("pass") is False,
          json.dumps({k: stab.get(k) for k in ("pass", "high_overfit_risk", "isolated_peak")}, ensure_ascii=False))


def test_acceptance_d_drift():
    from dual_engine_workflow_v2.repair_drift import init_repair_log, record_repair_round
    from dual_engine_workflow_v2.mechanism import build_fingerprint_from_statement
    stmt = sample_statement()
    log = init_repair_log("t1", "s1", stmt)
    new_stmt = sample_statement(
        forced_actor="completely different maker replenishment actors",
        counterparty="inventory managers on opposite side",
        causal_entry="enter on ema cross only",
        predictable_distortion="trend continuation after breakout",
    )
    log, drift = record_repair_round(
        log, failed_test="x", root_cause="inject_new_edge",
        modifications=["added_independent_filter"],
        statement_before=stmt, statement_after=new_stmt,
        fingerprint_before=build_fingerprint_from_statement(stmt),
        fingerprint_after=build_fingerprint_from_statement(new_stmt),
        frequency_before=20, frequency_after=18,
        oos_before=0.01, oos_after=0.02,
        introduced_new_condition=True, changed_edge_source=True,
    )
    check("D_material_drift", drift.get("drift") == "material", str(drift))
    check("D_terminate", log.get("terminated") is True, str(log.get("termination_reason")))


def test_acceptance_e_failure_archive():
    from dual_engine_workflow_v2.failure_archive import build_failure_archive
    a1 = build_failure_archive(
        strategy_id="s1", mechanism_id="m1", symbol="SOL-USDT-SWAP", timeframe="5m",
        exploration_mode="A", failure_level="execution_cost_failure",
        evidence_scope={"n": 1}, failed_test="execution_cost",
        observed_result={"sharpe": -0.2}, expected_result={"sharpe": ">0"},
        root_cause_confidence="high",
        alternative_explanations=["slip_model_overstate"],
        ruled_out_explanations=["vague_market_unsuitable_claim"],
        final_statement="毛收益存在但极端摩擦后净期望转负。",
    )
    a2 = build_failure_archive(
        strategy_id="s2", mechanism_id="m2", symbol="SOL-USDT-SWAP", timeframe="5m",
        exploration_mode="A", failure_level="frequency_target_conflict",
        evidence_scope={"n": 1}, failed_test="frequency",
        observed_result={"tpd": 0.05}, expected_result={"tpd": "0.5-1"},
        root_cause_confidence="medium",
        final_statement="逻辑在样本内有正期望，但开仓频率低于任务目标区间。",
    )
    check("E_distinct_levels", a1["failure_level"] != a2["failure_level"])
    check("E_has_exclusion_rule", bool(a1.get("reusable_exclusion_rule")))
    vague_ok = True
    try:
        build_failure_archive(
            strategy_id="s3", mechanism_id="m3", symbol="X", timeframe="5m",
            exploration_mode="A", failure_level="insufficient_evidence",
            evidence_scope={}, failed_test="x", observed_result={}, expected_result={},
            root_cause_confidence="low", final_statement="这个方向已经走到尽头",
        )
        vague_ok = False
    except ValueError:
        vague_ok = True
    check("E_rejects_vague_language", vague_ok)


def test_acceptance_f_4d_isolation():
    from dual_engine_workflow_v2.formal_4d import run_multidimensional_review

    def fake_ai(provider, prompt, payload, max_tokens=1000, temperature=0.1):
        if provider == "deepseek":
            return {"ok": True, "parsed": {
                "causal_fatal_issue": ["correlation_not_causation"],
                "causal_major_issue": [], "causal_minor_issue": [], "causal_pass": False,
            }}
        if provider == "qwen":
            return {"ok": True, "parsed": {
                "game_theory_fatal_issue": [], "game_theory_major_issue": [],
                "game_theory_minor_issue": ["crowding_soft"], "game_theory_pass": True,
            }}
        if provider == "glm":
            return {"ok": True, "parsed": {
                "execution_fatal_issue": [], "execution_major_issue": [],
                "execution_minor_issue": [], "execution_pass": True,
                "net_metrics_after_friction": {"sharpe": 0.2},
            }}
        return {"ok": False, "error": "unexpected"}

    book = {"mechanism_statement": sample_statement(), "symbol": "SOL-USDT-SWAP", "repair_round": 0}
    packs = {
        "base_metrics": {"trades": 20, "folds": 8, "oos_profit": 0.01, "sharpe": 0.8},
        "extreme_friction": {"pass": True, "metrics": {"sharpe": 0.2, "trades": 15}},
        "abc": {"necessity": {"pass": True}},
    }
    review = run_multidimensional_review(book, packs, sample_dsl(), fake_ai, aux_wr={"ai_theoretical_wr_avg": 90})
    check("F_dims_distinct", "causal" in review and "game_theory" in review
          and "backtest_integrity" in review and "execution_friction" in review)
    check("F_fatal_blocks_pass", review.get("approved") is False and "causal" in (review.get("fatal_blocks") or []),
          str(review.get("fatal_blocks")))
    check("F_aux_wr_cannot_override",
          (review.get("auxiliary_wr") or {}).get("pass") is True and review.get("approved") is False)
    check("F_backtest_executor_recorded",
          (review.get("backtest_integrity") or {}).get("executor") == "local_backtest_integrity_v1")
    check("F_openai_not_forged",
          (review.get("backtest_integrity") or {}).get("openai_forged") is False)


def test_acceptance_g_mode_a_isolation():
    from dual_engine_workflow_v2.exploration import isolate_mode_a_context
    inputs = {
        "death_heatmap_top10": [{"code": "stop_cluster", "count": 3}],
        "freezer_last10": [{"code": "friction", "summary": "cost ate edge",
                            "dsl": {"entry": {"all": []}}, "params": {"rsi": 30}}],
        "coverage_map": [{"key": "ada_b", "symbol": "ADA-USDT-SWAP", "timeframe": "5m",
                          "grade": "B", "dsl": {"entry": {"all": [{"left": {"feature": "rsi"}}]}},
                          "entry": {"all": []}}],
        "focus_candidates": [{"symbol": "SOL-USDT-SWAP", "timeframe": "5m"}],
        "successful_strategy_dsl": {"entry": {}},
        "factory_context": {"death_codes": ["x"], "positive_core_samples": [{"dsl": 1}]},
        "micro_72h": {"tags": ["aggressor_run"]},
    }
    ctx = isolate_mode_a_context(inputs, fingerprint_index=[{
        "fingerprint": {"family_hash": "abc", "fingerprint_hash": "def",
                        "forced_actor": "x", "distortion_type": "y"}
    }], exclusion_rules=[{"exclude_if": {"x": 1}}])
    blob = json.dumps(ctx, ensure_ascii=False)
    check("G_no_success_dsl_key", "successful_strategy_dsl" not in ctx)
    check("G_fingerprints_readable", len(ctx.get("mechanism_fingerprints") or []) >= 1)
    check("G_death_summaries_readable", len(ctx.get("freezer_death_summaries") or []) >= 1)
    check("G_stripped_recorded", len(ctx.get("stripped_keys") or []) >= 1, str(ctx.get("stripped_keys")))
    # coverage should not keep nested entry recipes
    cov = ctx.get("coverage_map_keys_only") or []
    check("G_coverage_keys_only", cov and "dsl" not in cov[0] and "entry" not in cov[0], str(cov[:1]))


def test_acceptance_h_protection_markers():
    """H: flags/pause must not disable formal daemons; entry still legacy until acceptance."""
    from dual_engine_workflow_v2.config import load_flags, default_flags, creation_entry
    # Use temp flags path via monkeypatch of FLAGS_PATH would be ideal; check defaults
    d = default_flags()
    check("H_default_entry_legacy", d.get("creation_entry") == "legacy")
    check("H_pause_non_live_only", d.get("pause", {}).get("non_live_creation") is True)


def test_logic_destruction_deprecated_marker():
    from dual_engine_workflow_v2.tests_abc import run_abc_battery
    base_m = {
        "mean_net": 0.01, "sharpe": 1.0, "oos_profit": 0.01, "profit_factor": 1.4,
        "max_dd": 0.04, "trades": 25, "fold_positive": 6, "folds": 10, "win_rate_pct": 55,
    }

    def bt(defn):
        # slightly worse variants
        return {"metrics": {
            "mean_net": 0.0, "sharpe": 0.1, "oos_profit": 0.0, "profit_factor": 1.0,
            "max_dd": 0.08, "trades": 20, "fold_positive": 4, "folds": 10, "win_rate_pct": 50,
        }}

    abc = run_abc_battery(sample_dsl(), base_m, bt, core_features=["atr_pct"])
    check("ABC_replaced_flag", abc.get("replaced_logic_destruction") is True)


def main():
    tests = [
        test_protocol_and_statement,
        test_acceptance_a_fidelity_rsi,
        test_acceptance_b_necessity_random,
        test_acceptance_c_isolated_peak,
        test_acceptance_d_drift,
        test_acceptance_e_failure_archive,
        test_acceptance_f_4d_isolation,
        test_acceptance_g_mode_a_isolation,
        test_acceptance_h_protection_markers,
        test_logic_destruction_deprecated_marker,
    ]
    for fn in tests:
        try:
            fn()
        except Exception:
            check(fn.__name__ + "_exception", False, traceback.format_exc()[-500:])
    print("\n=== SUMMARY %d passed, %d failed ===" % (len(PASS), len(FAIL)))
    for name, detail in FAIL:
        print(" -", name, detail)
    # Write machine-readable result
    out = {
        "passed": PASS,
        "failed": [{"name": n, "detail": d} for n, d in FAIL],
        "acceptance": {
            "A": "A_unapproved_rsi_detected" in PASS and "A_rsi_classified" in PASS,
            "B": "B_necessity_fails_pseudo" in PASS,
            "C": "C_high_overfit_flagged" in PASS,
            "D": "D_material_drift" in PASS and "D_terminate" in PASS,
            "E": "E_distinct_levels" in PASS and "E_rejects_vague_language" in PASS,
            "F": "F_fatal_blocks_pass" in PASS and "F_aux_wr_cannot_override" in PASS,
            "G": "G_no_success_dsl_key" in PASS and "G_fingerprints_readable" in PASS,
            "H": "H_default_entry_legacy" in PASS,
        },
    }
    path = os.path.join(PARENT, "docs", "workflow_v2_acceptance_result.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("wrote", path)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
