# -*- coding: utf-8 -*-
"""Adversarial identity tests for the creation-to-review handoff.

The suite intentionally exercises field-name migrations and immutable intent
at module boundaries.  Keep this file runnable on the production Python 3.6
runtime: no f-strings, dataclasses, or postponed-annotation dependency.
"""
from __future__ import print_function

import math
import unittest
from unittest import mock

from dual_engine_workflow_v2 import antifalsify
from dual_engine_workflow_v2 import formal_review_bridge
from dual_engine_workflow_v2 import heterogeneous_committee
from dual_engine_workflow_v2 import map_elites_archive
from dual_engine_workflow_v2 import probe_protocol
from dual_engine_workflow_v2 import research_contract
from dual_engine_workflow_v2 import research_discovery
from dual_engine_workflow_v2 import recipe_policy
from dual_engine_workflow_v2.pipeline_step_a import codex_implement_from_spec


def _mechanism_spec():
    return {
        "mechanism_id": "mech_identity_regression",
        "mechanism_name": "identity_regression",
        "mechanism_family": "trend_pullback",
        "market_inefficiency": "mechanical continuation after a pullback",
        "counterparty_source": "late counter-trend participants",
        "why_edge_exists": "order flow adjusts with a delay",
        "edge_decay_conditions": "continuation disappears",
        "required_market_regime": "declared trend regime",
        "entry_logic": "trend continuation after pullback confirmation",
        "exit_logic": "3.0x ATR trailing OR swing extreme lookback 20",
        "stop_logic": "protective fixed 0.9 percent",
        "take_profit_logic": "3.0x ATR trailing",
        "invalidation_logic": "swing extreme lookback 20",
        "non_negotiable_rules": ["preserve_research_contract"],
        "tunable_parameters": ["max_hold_bars"],
        "forbidden_transformations": [],
        "expected_trade_frequency_class": "low",
        "expected_holding_period": "3_to_18_bars_5m",
        "suitable_symbols": ["ADA-USDT-SWAP"],
        "suitable_timeframes": ["5m"],
    }


def _compiler_pack(contract):
    return {
        "mechanism_spec": _mechanism_spec(),
        "meta": {
            "symbol": "ADA-USDT-SWAP",
            "timeframe": "5m",
            "direction": "long",
            "title": "creation identity regression",
        },
        "research_contract": contract,
    }


class NewEvidenceFieldIdentityTests(unittest.TestCase):
    def test_execution_engineer_prefers_independent_event_count(self):
        result = heterogeneous_committee.run_execution_engineer({
            "mean_net": 0.0012,
            "n_independent_events": 12,
            # A stale compatibility field must not overwrite the new field.
            "n_hits": 0,
        }, efr_pack={"efr": 2.0})
        self.assertTrue(result["passed"], result.get("issues"))
        self.assertEqual(12, result["n_hits"])
        self.assertEqual("n_independent_events", result["sample_field"])

    def test_map_elites_uses_independent_events_and_hac_statistic(self):
        descriptor = map_elites_archive.behavior_descriptor(
            {"family": "trend_pullback", "predicted_direction": "long"},
            probe_best={
                "n_independent_events": 20,
                "n_hits": 0,
                "mean_net": 0.001,
            },
            n_bars=100,
        )
        self.assertEqual("frequent", descriptor["freq_bucket"])
        score = map_elites_archive.quality_score(probe_best={
            "passed": True,
            "mean_net": 0.001,
            "hac_t_stat": 2.5,
            # The legacy statistic is deliberately absurd: it must be ignored.
            "t_stat": 99.0,
        })
        self.assertAlmostEqual(3.5, score, places=8)


class ImmutableResearchContractTests(unittest.TestCase):
    def test_locked_direction_ignores_adversarial_short_language(self):
        directions = probe_protocol._direction_candidates({
            "predicted_direction": "long",
            "trade_direction_locked": True,
            "statement_zh": "short-term 噪声很多，历史文本还提到做空",
            "family": "crowding_fade_short",
        })
        self.assertEqual((1,), directions)

    def test_required_features_do_not_implicitly_close_allowed_features(self):
        contract = research_contract.compile_contract(
            "趋势回撤研究",
            "ADA-USDT-SWAP",
            "5m",
            "long",
            constraints={
                "research_contract": {
                    "feature_contract": {"required_features": ["close"]},
                },
            },
        )
        features = contract["feature_contract"]
        self.assertEqual(["close"], features["required_features"])
        self.assertEqual([], features["allowed_features"])
        self.assertFalse(features["allowed_features_enforced"])

        # A family template legitimately needs more than ``close``.  An empty,
        # non-enforced allow-list therefore must not reject those features.
        compiled = codex_implement_from_spec(_compiler_pack(contract))
        self.assertTrue(compiled["ok"], compiled.get("fidelity_errors"))

    def test_optional_cross_asset_menu_does_not_require_cross_asset_data(self):
        contract = research_contract.compile_contract(
            "可选A/B/C自主选择：趋势回撤、均值回归、跨品种配对之一",
            "ADA-USDT-SWAP",
            "5m",
            "long",
        )
        self.assertTrue(contract["valid"], contract.get("validation_errors"))
        self.assertEqual([], contract["feature_contract"]["cross_asset_symbols"])
        self.assertNotIn("cross_asset_candles", contract["data_contract"]["required"])
        self.assertFalse(any(
            str(row).startswith("required_data_missing:cross_asset_candles")
            for row in contract["validation_errors"]
        ))

    def test_primary_kdj_derivation_matches_literal_formula_without_proxy(self):
        candles = []
        for index in range(30):
            close = float(index + 1)
            candles.append({
                "open": close - 0.25,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 100.0,
            })
        matrix, evidence = research_contract.derive_contract_features(
            {}, candles, "5m", ["kdj_k", "kdj_d", "kdj_j"],
        )
        # Formal/live KDJ uses 24-bar RSV and alpha=1/3 smoothing.  At the
        # first complete window RSV=96 with the synthetic OHLC sequence.
        self.assertEqual(50.0, matrix["kdj_k"][22])
        self.assertAlmostEqual(196.0 / 3.0, matrix["kdj_k"][23], places=10)
        self.assertAlmostEqual(496.0 / 9.0, matrix["kdj_d"][23], places=10)
        self.assertAlmostEqual(772.0 / 9.0, matrix["kdj_j"][23], places=10)
        self.assertEqual(["kdj_d", "kdj_j", "kdj_k"], sorted(evidence["derived_exact"]))
        self.assertFalse(evidence["proxy_substitution_used"])
        self.assertEqual([], evidence["missing_after"])
        self.assertTrue(all(math.isfinite(matrix[name][23]) for name in (
            "kdj_k", "kdj_d", "kdj_j",
        )))


class ExactEventExecutionIdentityTests(unittest.TestCase):
    def test_statistical_return_sample_is_not_silently_truncated(self):
        size = 9000
        mask = [False] * size
        for index in range(80, size - 2, 25):
            mask[index] = True
        event = {
            "event_id": "full_sample_identity",
            "kind": "human_contract_exact",
            "terms": [{"factor": "kdj_k"}],
            "mask": mask,
        }
        zero_cost = {
            "taker_taker": {"total_friction": 0.0},
            "maker_taker": {"total_friction": 0.0},
            "stress_threefold": {"total_friction": 0.0},
        }
        with mock.patch.object(
            probe_protocol, "_cost_scenarios", return_value=zero_cost,
        ):
            result = probe_protocol._evaluate_trial(
                None, [0.001] * size, event, 1, 1, "next_bar_open",
                symbol="ADA-USDT-SWAP", timeframe="5m",
            )
        self.assertGreater(result["n_filled_events"], 300)
        self.assertEqual(result["n_filled_events"], len(result["trade_returns"]))
        self.assertEqual(result["n_filled_events"], result["n_statistical_returns"])
        self.assertAlmostEqual(0.02, result["trade_returns"][0])
        self.assertEqual(20, result["execution_leverage"])
        self.assertEqual(
            "full_size_leveraged_after_cost_v1",
            result["statistical_return_basis"],
        )

    def test_probe_applies_production_stop_before_fixed_horizon_close(self):
        candles = [
            {"ts": 0, "open": 100, "high": 100, "low": 100, "close": 100},
            {"ts": 1, "open": 100, "high": 100.2, "low": 98.8, "close": 99.8},
            {"ts": 2, "open": 99.8, "high": 101, "low": 99.5, "close": 101},
        ]
        observation = probe_protocol._trade_observation(
            candles, 0, 2, 1, "next_bar_open",
        )
        self.assertEqual("protective_stop", observation["exit_reason"])
        self.assertEqual(1, observation["exit_index"])
        self.assertEqual(2, observation["planned_exit_index"])
        self.assertAlmostEqual(-0.009, observation["return"])

    def test_exact_antifalsify_preserves_original_boolean_event_mask(self):
        original = [False, True, False, False, True, True, False, True]
        calls = []

        def fake_trial(candles, fwd_returns, event, horizon, direction, mapping,
                       symbol=None, timeframe=None):
            calls.append({
                "event_id": event.get("event_id"),
                "mask": list(event.get("mask") or []),
                "horizon": horizon,
                "direction": direction,
                "mapping": mapping,
            })
            return {
                "ok": True,
                "passed": True,
                "mean_net": 0.001,
                "hac_t_stat": 2.0,
                "n_independent_events": sum(1 for value in event.get("mask") or [] if value),
                "trade_returns": [0.001] * 8,
                "pbo_bar_returns": [0.0] * len(event.get("mask") or []),
                "event_mask": list(event.get("mask") or []),
            }

        with mock.patch.object(
            antifalsify.pp, "_evaluate_trial", side_effect=fake_trial,
        ), mock.patch.object(
            antifalsify, "_probe_net",
            side_effect=AssertionError("exact-event path must not quantile-probe"),
        ):
            result = antifalsify.run_antifalsify_battery(
                original,
                [0.0] * len(original),
                precomputed_event_mask=True,
                factor_matrix={},
                horizon=3,
                trade_direction="long",
                execution_mapping="next_bar_open",
            )

        self.assertEqual("exact_contract_base", calls[0]["event_id"])
        self.assertEqual(original, calls[0]["mask"])
        self.assertTrue(result["precomputed_event_mask"])
        self.assertFalse(result["event_mask_requantiled"])
        self.assertEqual(sum(1 for value in original if value), result["event_count"])

    def test_bar_close_contract_only_tests_next_bar_open_mapping(self):
        mappings = []

        def fake_evaluate(candles, fwd_returns, event, horizon, direction, mapping,
                          symbol=None, timeframe=None):
            mappings.append(mapping)
            return {
                "ok": True,
                "passed": False,
                "research_state": "SAMPLE_INADEQUATE",
                "failure_codes": ["sample_insufficient"],
                "event_id": event.get("event_id"),
                "event_kind": event.get("kind"),
                "horizon_bars": horizon,
                "trade_direction": "long" if direction > 0 else "short",
                "execution_mapping": mapping,
                "n_independent_events": 0,
                "n_raw_triggers": 0,
                "mean_net": None,
                "hac_t_stat": 0.0,
                "evidence_axes": {},
            }

        hypothesis = {
            "hypothesis_id": "hyp_bar_close_identity",
            "predicted_direction": "long",
            "trade_direction_locked": True,
            "factor_hints": ["kdj_k"],
            "required_event_conditions": [
                {"feature": "kdj_k", "operator": "<=", "value": 20.0},
            ],
            "required_entry_timing": {"mode": "bar_close", "timeframe": "5m"},
        }
        with mock.patch.object(
            probe_protocol, "_evaluate_trial", side_effect=fake_evaluate,
        ):
            result = probe_protocol.probe_hypothesis(
                hypothesis,
                {"kdj_k": [10.0, 30.0, 15.0, 40.0]},
                [0.0, 0.0, 0.0, 0.0],
                candles=None,
                symbol="ADA-USDT-SWAP",
                timeframe="5m",
                max_trials=6,
            )
        self.assertTrue(mappings)
        self.assertEqual({"next_bar_open"}, set(mappings))
        self.assertTrue(result["entry_timing_mapping_locked"])
        self.assertEqual(["next_bar_open"], result["coverage"]["execution_mappings_tested"])

    def test_unimplemented_execution_mapping_cannot_become_admitted_best(self):
        def fake_evaluate(candles, fwd_returns, event, horizon, direction, mapping,
                          symbol=None, timeframe=None):
            passed = mapping == "pullback_limit"
            return {
                "ok": True, "passed": passed,
                "research_state": "READY_FOR_ASSEMBLY" if passed else "DIRECTIONAL_BUT_SMALL",
                "failure_codes": [] if passed else ["economic_magnitude_insufficient"],
                "event_id": event.get("event_id"), "event_kind": event.get("kind"),
                "event_logic": event.get("logic"),
                "terms": event.get("terms"), "factor": "z20", "side": "high", "q": 0.8,
                "horizon_bars": horizon,
                "trade_direction": "long" if direction > 0 else "short",
                "execution_mapping": mapping,
                "n_independent_events": 20, "n_raw_triggers": 20,
                "mean_net": 0.01 if passed else 0.001,
                "hac_t_stat": 3.0 if passed else 1.0,
                "evidence_axes": {},
                "exit_policy": {
                    "mode": recipe_policy.EXIT_POLICY_MODE,
                    "allow_early_take_profit": False,
                },
                "protective_stop_policy": {
                    "mode": recipe_policy.PROTECTIVE_STOP_MODE,
                    "price_pct": recipe_policy.PROTECTIVE_STOP_PCT,
                },
                "protective_stop_evaluated": True,
                "execution_leverage": recipe_policy.EXECUTION_LEVERAGE,
                "statistical_return_basis": recipe_policy.STATISTICAL_RETURN_BASIS,
            }

        contract = research_contract.compile_contract(
            "开仓K<=20", "ADA-USDT-SWAP", "5m", "long",
        )
        hypothesis = research_contract.apply_to_hypothesis({
            "hypothesis_id": "hyp_mapping_boundary",
            "predicted_direction": "long", "trade_direction_locked": True,
            "factor_hints": ["kdj_k"],
        }, contract)
        with mock.patch.object(
            probe_protocol, "_evaluate_trial", side_effect=fake_evaluate,
        ):
            result = probe_protocol.probe_hypothesis(
                hypothesis, {"kdj_k": [float(i % 13) for i in range(400)]},
                [0.0] * 400, candles=[{}] * 400,
                symbol="ADA-USDT-SWAP", timeframe="5m", max_trials=12,
            )
        self.assertEqual("next_bar_open", result["best"]["execution_mapping"])
        self.assertFalse(result["passed"])
        self.assertEqual("pullback_limit", result["diagnostic_best"]["execution_mapping"])


class CompilerAndHandoffBoundaryTests(unittest.TestCase):
    def test_formal_boundary_recomputes_recipe_hash_contract_and_direction(self):
        contract = research_contract.compile_contract(
            "generic formal identity", "ADA-USDT-SWAP", "5m", "long",
        )
        hypothesis = {
            "hypothesis_id": "hyp_formal", "mechanism_id": "mech_formal",
            "family": "trend_pullback",
        }
        probe = {
            "event_id": "event_formal", "event_kind": "mechanism_intersection",
            "event_logic": "all",
            "terms": [{
                "factor": "close_z_20", "side": "high", "q": 0.8,
                "window": 240, "min_history": 80,
                "threshold_source": "prior_only_rolling_quantile",
            }, {
                "factor": "volume_z", "side": "low", "q": 0.8,
                "window": 240, "min_history": 80,
                "threshold_source": "prior_only_rolling_quantile",
            }], "factor": "close_z_20", "side": "high",
            "q": 0.8, "trade_direction": "long", "horizon_bars": 3,
            "execution_mapping": "next_bar_open", "primary_cost_scenario": "taker_taker",
            "primary_cost_per_trade": 0.001, "statistical_returns_are_post_cost": True,
            "exit_policy": {
                "mode": recipe_policy.EXIT_POLICY_MODE,
                "allow_early_take_profit": False,
            },
            "protective_stop_policy": {
                "mode": recipe_policy.PROTECTIVE_STOP_MODE,
                "price_pct": recipe_policy.PROTECTIVE_STOP_PCT,
            },
            "protective_stop_evaluated": True,
            "execution_leverage": recipe_policy.EXECUTION_LEVERAGE,
            "statistical_return_basis": recipe_policy.STATISTICAL_RETURN_BASIS,
        }
        recipe = research_discovery.assembly_recipe(hypothesis, probe, contract)
        recipe_id = recipe["recipe_id"]
        identity = {
            "recipe_id": recipe_id, "recipe": recipe,
            "hypothesis_id": "hyp_formal", "mechanism_id": "mech_formal",
        }
        blueprint = {
            "research_contract": contract,
            "best_factor": dict(identity),
            "glm_research_brief": {
                "selected_recipe_id": recipe_id, "selected_recipe": recipe,
            },
            "stages": {
                "selected": dict(identity),
                "admission_envelope": {
                    "research_contract_id": contract["contract_id"],
                    "recipe_ids": [recipe_id], "candidates": [dict(identity)],
                },
                "assembly_lineage": {"selected_recipe_id": recipe_id},
            },
        }
        job = {"trade_direction": "long"}
        self.assertTrue(
            formal_review_bridge._validate_recipe_lineage(blueprint, job)["ok"],
        )
        forged = dict(blueprint)
        forged["best_factor"] = dict(identity, hypothesis_id="forged")
        validation = formal_review_bridge._validate_recipe_lineage(forged, job)
        self.assertFalse(validation["ok"])
        self.assertIn("selected_recipe_hypothesis_id_drift", validation["reasons"])

    def test_formal_spec_is_deterministically_bound_to_admitted_recipe(self):
        recipe = {
            "schema": "qiyu_admitted_probe_recipe_v1",
            "recipe_id": "recipe_locked_test",
            "research_contract_id": "rc_locked_test",
            "hypothesis_id": "hyp_locked_test",
            "mechanism_id": "mech_locked_test",
            "family": "trend_pullback",
            "event_id": "event_locked_test",
            "event_kind": "single_proxy",
            "event_logic": "all",
            "terms": [{
                "factor": "z20", "side": "high", "q": 0.8,
                "window": 240, "min_history": 80,
                "threshold_source": "prior_only_rolling_quantile",
            }],
            "trade_direction": "long",
            "horizon_bars": 3,
            "execution_mapping": "next_bar_open",
        }
        blueprint = {
            "glm_research_brief": {
                "design_doc": {"hypotheses": [{
                    "hypothesis_id": "hyp_locked_test",
                    "mechanism_id": "mech_locked_test",
                    "statement_zh": "已准入的趋势回撤机制",
                    "payoff_payer": "late counter-trend traders",
                }]},
            },
        }
        job = {
            "symbol": "ADA-USDT-SWAP", "timeframe": "5m",
            "trade_direction": "long",
        }
        pack = formal_review_bridge._locked_spec_pack(blueprint, job, recipe)
        spec = pack["mechanism_spec"]
        self.assertEqual("deterministic_admitted_recipe", pack["meta"]["spec_source"])
        self.assertEqual("trend_pullback", spec["mechanism_family"])
        self.assertEqual("3_bars", spec["expected_holding_period"])
        self.assertIn("recipe_locked_test", spec["entry_logic"])
        self.assertEqual(recipe, pack["admitted_recipe_lock"])

    def test_narrative_clauses_are_not_misclassified_as_exact_machine_event(self):
        contract = research_contract.compile_contract(
            "趋势回撤研究",
            "ADA-USDT-SWAP",
            "5m",
            "long",
            constraints={
                "research_contract": {
                    "event_contract": {
                        "diagnostic_clauses": ["保留趋势结构与回撤确认的机制叙事"],
                        "required_conditions": [],
                        "entry_conditions": [],
                        "exit_conditions": [],
                        "require_exact_event_fidelity": False,
                    },
                },
            },
        )
        self.assertFalse(
            contract["event_contract"]["require_exact_event_fidelity"],
        )
        compiled = codex_implement_from_spec(_compiler_pack(contract))
        self.assertTrue(compiled["ok"], compiled.get("fidelity_errors"))
        self.assertNotIn(
            "exact_event_missing_machine_compilable_conditions",
            compiled.get("fidelity_errors") or [],
        )

    def test_formal_handoff_rejects_recipe_outside_admission_envelope(self):
        blueprint = {
            "ok": True,
            "present_to_human": True,
            "glm_research_brief": {"summary": "qualified research"},
            "research_contract": {"contract_id": "rc_identity"},
            "stages": {
                "admission_envelope": {
                    "contract_id": "rc_identity",
                    "recipe_ids": ["recipe_admitted"],
                },
                "assembly_lineage": {
                    "selected_recipe_id": "recipe_unadmitted",
                    "selected_is_in_admission_envelope": True,
                    "global_factor_mining_used": False,
                    "classic_or_unadmitted_switch_used": False,
                },
            },
        }
        result = formal_review_bridge.submit_blueprint_to_formal_review(
            blueprint,
            {
                "job_id": "job_identity",
                "symbol": "ADA-USDT-SWAP",
                "timeframe": "5m",
                "trade_direction": "long",
                "brief": "identity boundary",
            },
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["started"])
        self.assertFalse(result["submission_created"])
        self.assertFalse(result["review_submitted"])
        self.assertEqual("creation_recipe_lineage_invalid", result["reason"])


if __name__ == "__main__":
    unittest.main()
