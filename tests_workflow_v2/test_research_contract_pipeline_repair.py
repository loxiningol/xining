# -*- coding: utf-8 -*-
from __future__ import print_function

import math
import os
import tempfile
import unittest
from unittest import mock

from dual_engine_workflow_v2 import creation_blueprint
from dual_engine_workflow_v2 import creation_meta_think
from dual_engine_workflow_v2 import multiple_testing
from dual_engine_workflow_v2 import probe_protocol
from dual_engine_workflow_v2 import research_contract
from dual_engine_workflow_v2 import research_discovery


class ResearchContractRepairTests(unittest.TestCase):
    def test_contract_body_tamper_is_detected_even_when_id_is_unchanged(self):
        contract = research_contract.compile_contract(
            "开仓K<=20；K线走完后再开仓",
            "ADA-USDT-SWAP", "5m", "long",
        )
        self.assertTrue(research_contract.verify_contract_integrity(contract)["ok"])
        tampered = dict(contract)
        tampered["holding_contract"] = dict(contract["holding_contract"])
        tampered["holding_contract"]["allowed_horizons_bars"] = [99]
        audit = research_contract.verify_contract_integrity(tampered)
        self.assertFalse(audit["ok"])
        self.assertIn("research_contract_body_hash_mismatch", audit["reasons"])

    def test_unrepresentable_strong_clause_blocks_research(self):
        contract = research_contract.compile_contract(
            "必须EMA19上穿EMA53才允许开仓",
            "ADA-USDT-SWAP", "5m", "long",
        )
        self.assertFalse(contract["valid"])
        representations = contract["event_contract"]["clause_representations"]
        self.assertEqual("unsupported", representations[0]["representation_status"])
        self.assertTrue(any(
            row.startswith("required_clause_unrepresentable:")
            for row in contract["validation_errors"]
        ))

    def test_exact_holding_and_exit_policy_are_immutable(self):
        contract = research_contract.compile_contract(
            "固定持有12根K线；K线走完后再开仓",
            "ADA-USDT-SWAP", "5m", "long",
        )
        holding = contract["holding_contract"]
        self.assertTrue(contract["valid"], contract["validation_errors"])
        self.assertEqual(12, holding["exact_horizon_bars"])
        self.assertEqual([12], holding["allowed_horizons_bars"])
        self.assertEqual("fixed_horizon_close_v1", holding["exit_policy"]["mode"])
        self.assertAlmostEqual(0.009, holding["protective_stop_policy"]["price_pct"])
        self.assertEqual(20, holding["execution_leverage"])
        self.assertTrue(research_contract.verify_contract_integrity(contract)["ok"])

    def test_bidirectional_job_must_be_split_before_research(self):
        contract = research_contract.compile_contract(
            "双向策略", "ADA-USDT-SWAP", "5m", "both",
        )
        self.assertFalse(contract["valid"])
        self.assertIn(
            "bidirectional_contract_must_be_split_into_long_and_short_jobs",
            contract["validation_errors"],
        )

    def test_entry_exit_roles_thresholds_or_and_bar_close_are_preserved(self):
        brief = (
            "开仓条件：（5min）的k小于等于20；"
            "止盈除原条件外增加或条件：（5min）的j大于等于89；"
            "K线触发后等到该K线收盘再开仓"
        )
        contract = research_contract.compile_contract(
            brief, "ADA-USDT-SWAP", "5m", "long",
        )
        event = contract["event_contract"]
        self.assertTrue(contract["valid"])
        self.assertEqual("next_bar_open", event["entry_timing"]["mode"])
        self.assertEqual("closed_bar", event["entry_timing"]["signal_evaluation"])
        self.assertEqual("kdj_k", event["entry_conditions"][0]["feature"])
        self.assertEqual("<=", event["entry_conditions"][0]["operator"])
        self.assertEqual(20.0, event["entry_conditions"][0]["value"])
        self.assertEqual("kdj_j", event["exit_conditions"][0]["feature"])
        self.assertEqual(">=", event["exit_conditions"][0]["operator"])
        self.assertEqual("or", event["exit_conditions"][0]["join"])

    def test_comma_separated_entry_and_exit_are_attributed_per_condition(self):
        for separator in ("，", ","):
            contract = research_contract.compile_contract(
                "开仓K<=20%s止盈J>=89" % separator,
                "ADA-USDT-SWAP", "5m", "long",
            )
            event = contract["event_contract"]
            self.assertEqual(["kdj_k"], [x["feature"] for x in event["entry_conditions"]])
            self.assertEqual(["kdj_j"], [x["feature"] for x in event["exit_conditions"]])

    def test_direction_conflict_and_incomplete_mutation_fail_closed(self):
        supplied = {
            "target": {"symbol": "ADA-USDT-SWAP", "timeframe": "5m", "direction": "short"},
            "mutation_contract": {"parent_id": "parent-1"},
        }
        contract = research_contract.compile_contract(
            "测试", "ADA-USDT-SWAP", "5m", "long",
            constraints={"research_contract": supplied},
        )
        self.assertFalse(contract["valid"])
        self.assertTrue(any(x.startswith("target_direction_conflict:") for x in contract["validation_errors"]))
        self.assertIn("mutation_failed_gate_missing", contract["validation_errors"])
        self.assertIn("mutation_allowlist_missing", contract["validation_errors"])
        self.assertIn("mutation_structural_delta_missing", contract["validation_errors"])

    def test_primary_kdj_is_derived_exactly_and_higher_tf_is_not_substituted(self):
        candles = []
        for index in range(40):
            close = 100.0 + index * 0.2 + math.sin(index / 3.0)
            candles.append({"high": close + 1.0, "low": close - 1.0, "close": close})
        matrix, evidence = research_contract.derive_contract_features(
            {}, candles, "5m", ["kdj_k", "kdj_j", "15m__kdj_k"],
        )
        self.assertIn("kdj_k", matrix)
        self.assertIn("kdj_j", matrix)
        self.assertNotIn("15m__kdj_k", matrix)
        self.assertFalse(evidence["proxy_substitution_used"])
        self.assertIn("15m__kdj_k", evidence["missing_after"])
        self.assertTrue(any(x.startswith("higher_timeframe_feature_not_derived:") for x in evidence["errors"]))

    def test_research_kdj_matches_formal_backtest_frame_point_for_point(self):
        import pandas as pd
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            "os.environ",
            {
                "VECTOR_BACKTEST_LOG_DIR": tmp,
                "VECTOR_STRATEGY_CONFIG_PATH": os.path.join(tmp, "missing.json"),
                "VECTOR_DSL_STRATEGY_CONFIG_PATH": os.path.join(tmp, "missing_dsl.json"),
            },
            clear=False,
        ):
            import backtest_engine_v2
            candles = []
            for index in range(400):
                close = 100.0 + index * 0.02 + ((index % 7) - 3) * 0.1
                candles.append({
                    "open": close - 0.05, "high": close + 0.4,
                    "low": close - 0.35, "close": close,
                    "volume": 100.0 + index % 11,
                })
            frame = pd.DataFrame(
                candles,
                index=pd.date_range("2026-01-01", periods=len(candles), freq="5min"),
            )
            formal = backtest_engine_v2.precompute_indicators(frame, timeframe="5m")
            matrix, evidence = research_contract.derive_contract_features(
                {}, candles, "5m", ["kdj_k", "kdj_d", "kdj_j"],
            )
        self.assertTrue(evidence["ok"], evidence)
        for research_name, formal_name in (
            ("kdj_k", "k"), ("kdj_d", "d"), ("kdj_j", "j"),
        ):
            self.assertLess(max(
                abs(float(left) - float(right))
                for left, right in zip(matrix[research_name], formal[formal_name].tolist())
            ), 1e-10)

    def test_revoked_weekly_target_is_not_reintroduced(self):
        revoked = creation_meta_think._parse_constraints("禁止周收益≥8%作为硬门，改用年化门槛")
        explicit = creation_meta_think._parse_constraints("要求周收益至少8%")
        self.assertEqual(0.0, revoked["minimum_weekly_return"])
        self.assertEqual(0.08, explicit["minimum_weekly_return"])

    def test_all_common_weekly_revocation_phrases_win_in_both_stages(self):
        phrases = (
            "不再要求周收益8%", "无需周收益8%", "不要求周收益8%",
            "周收益8%仅作诊断", "周收益8%仅作观察",
        )
        for phrase in phrases:
            constraints = creation_meta_think._parse_constraints(phrase)
            self.assertEqual(0.0, constraints["minimum_weekly_return"], phrase)
            compiled = research_discovery.compile_research_contract(
                phrase, "ADA-USDT-SWAP", "5m", constraints=constraints,
            )
            self.assertIsNone(compiled["forced_weekly_demand"], phrase)
            self.assertTrue(compiled["requirement_feasible"], phrase)

    def test_unreachable_hard_target_stops_before_population(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(
            "os.environ", {"VECTOR_ROOT": root}, clear=False,
        ), mock.patch.object(
            research_discovery, "build_hypothesis_population",
            side_effect=AssertionError("population must not run"),
        ):
            result = research_discovery.run_discovery(
                "ADA-USDT-SWAP", "5m", brief="要求周收益至少8%",
                factor_matrix={}, fwd_returns=[], candles=[],
            )
        self.assertEqual("research_rejected", result["outcome"])
        self.assertEqual("unreachable_hard_requirement", result["error"])
        self.assertFalse(result["present_to_assembly"])


class PipelineOrderRepairTests(unittest.TestCase):
    def test_data_failure_still_persists_compiled_intent_contract(self):
        with tempfile.TemporaryDirectory() as out_dir:
            with mock.patch.object(
                creation_blueprint, "_load_matrix",
                return_value={"ok": False, "error": "manifest_missing"},
            ):
                result = creation_blueprint.run_creation_blueprint(
                    "ADA-USDT-SWAP", "5m", direction="long",
                    brief="只做顺势回撤", out_dir=out_dir, run_id="data-contract",
                )
        self.assertEqual("data_blocked", result["outcome"])
        self.assertTrue(result["research_contract"]["valid"])
        self.assertEqual(
            result["research_contract"]["contract_id"],
            result["stages"]["research_contract"]["contract_id"],
        )
        self.assertTrue(result.get("failure_artifact"))

    def test_local_structured_seed_is_not_mislabeled_as_ai_generated(self):
        contract = research_contract.compile_contract(
            "趋势回撤", "ADA-USDT-SWAP", "5m", "long",
        )
        seed = {
            "design_doc": {
                "divergence": {
                    "perspectives": [{
                        "id": "local_1", "family": "trend_pullback",
                        "thesis_zh": "回撤后续涨", "factor_hints": ["ret_3"],
                    }],
                },
            },
        }
        rows = research_discovery._design_seed_hypotheses(seed, contract)
        self.assertEqual(1, len(rows))
        self.assertFalse(rows[0]["ai_generated_before_discovery"])
        self.assertTrue(rows[0]["structured_generated_before_discovery"])

    def test_meta_design_runs_before_discovery_and_is_passed_as_population_seed(self):
        calls = []
        meta_pack = {"ok": True, "design_doc": {"divergence": {"perspectives": []}}}

        def fake_meta(**kwargs):
            calls.append("meta")
            self.assertIn("research_contract", kwargs)
            return meta_pack

        def fake_discovery(**kwargs):
            calls.append("discovery")
            self.assertIs(meta_pack, kwargs["design_seed"])
            return {
                "ok": False,
                "outcome": "research_rejected",
                "present_to_assembly": False,
                "n_survivors": 0,
                "run_id": "order-test",
                "detail": {"reason": "test_stop"},
                "stages": {"contract": kwargs["research_contract"]},
                "human_banner_zh": "test stop",
            }

        data = {
            "ok": True,
            "candles": [{"ts": 1, "open": 1, "high": 1, "low": 1, "close": 1}],
            "matrix": {}, "fwd": [None], "n_bars": 1, "path": "test",
            "source": "test", "research": {}, "note_zh": "test",
        }
        with tempfile.TemporaryDirectory() as out_dir:
            with mock.patch.dict("os.environ", {
                    "VECTOR_ROOT": out_dir,
                    "QIYU_RESEARCH_CANDLES_MODE": "local",
                    "QIYU_RESEARCH_LOCAL_ROOT": os.path.join(out_dir, "research_candles"),
                    "QIYU_RESEARCH_CACHE_ROOT": os.path.join(out_dir, "research_cache"),
                 }, clear=False), \
                    mock.patch.object(creation_blueprint, "_load_matrix", return_value=data), \
                    mock.patch.object(
                        creation_blueprint.meta, "run_meta_think", side_effect=fake_meta,
                    ), mock.patch.object(
                        creation_blueprint.discovery, "run_discovery", side_effect=fake_discovery,
                    ):
                result = creation_blueprint.run_creation_blueprint(
                    "ADA-USDT-SWAP", "5m", brief="测试顺序", out_dir=out_dir,
                    run_id="order-test",
                )
        self.assertEqual(["meta", "discovery"], calls)
        self.assertEqual("research_rejected", result["outcome"])
        self.assertTrue(result.get("failure_artifact"))

    def test_exact_contract_entry_uses_literal_threshold_not_quantile(self):
        hypothesis = {
            "required_event_conditions": [
                {"feature": "kdj_k", "operator": "<=", "value": 20.0, "join": "and"},
                {"feature": "kdj_j", "operator": ">=", "value": 89.0, "join": "or"},
            ]
        }
        specs, _available = probe_protocol._candidate_events(
            hypothesis,
            {"kdj_k": [10, 30, 30], "kdj_j": [20, 90, 20]},
        )
        self.assertEqual(1, len(specs))
        self.assertEqual("human_contract_exact", specs[0]["kind"])
        self.assertEqual([True, True, False], specs[0]["mask"])
        self.assertEqual([20.0, 89.0], [row["value"] for row in specs[0]["terms"]])

    def test_generic_recipe_persists_causal_quantile_estimator_identity(self):
        specs, _available = probe_protocol._candidate_events(
            {"factor_hints": ["close_z_20"]},
            {"close_z_20": [float(index % 17) for index in range(400)]},
            max_specs=2,
        )
        self.assertTrue(specs)
        term = specs[0]["terms"][0]
        self.assertEqual(240, term["window"])
        self.assertEqual(80, term["min_history"])
        self.assertEqual("prior_only_rolling_quantile", term["threshold_source"])

    def test_python36_normal_inverse_fallback_matches_runtime_result(self):
        expected = multiple_testing._norm_ppf(0.975)
        with mock.patch.object(multiple_testing, "NormalDist", None):
            fallback = multiple_testing._norm_ppf(0.975)
        self.assertAlmostEqual(expected, fallback, places=6)


if __name__ == "__main__":
    unittest.main()
