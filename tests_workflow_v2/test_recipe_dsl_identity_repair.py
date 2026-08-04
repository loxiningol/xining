# -*- coding: utf-8 -*-
from __future__ import print_function

import copy
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import auto_trade_strategy_dsl as strategy_dsl
from dual_engine_workflow_v2 import formal_review_bridge
from dual_engine_workflow_v2 import pipeline_step_a
from dual_engine_workflow_v2 import probe_protocol
from dual_engine_workflow_v2 import research_contract
from dual_engine_workflow_v2 import research_discovery
from dual_engine_workflow_v2 import recipe_policy


SYMBOL = "ADA-USDT-SWAP"
TIMEFRAME = "5m"


def _generic_contract():
    return research_contract.compile_contract(
        "generic admitted two-factor event",
        SYMBOL,
        TIMEFRAME,
        direction="long",
        constraints={
            "research_contract": {
                "event_contract": {
                    "required_conditions": [],
                    "entry_timing": {
                        "mode": "next_bar_open",
                        "signal_evaluation": "closed_bar",
                        "timeframe": TIMEFRAME,
                    },
                    "require_exact_event_fidelity": False,
                },
            },
        },
        available_data=["ohlcv_swap_candles", "derived_factors"],
        data_version="recipe_identity_fixture_v1",
        code_version="recipe_identity_fixture_v1",
    )


def _exact_contract():
    condition = {
        "indicator": "k",
        "operator": "<=",
        "value": 20,
        "timeframe": TIMEFRAME,
        "join": "and",
        "role": "entry",
    }
    return research_contract.compile_contract(
        "K线走完后在下一根K线开盘执行",
        SYMBOL,
        TIMEFRAME,
        direction="long",
        constraints={
            "research_contract": {
                "event_contract": {
                    "required_conditions": [condition],
                    "entry_timing": {
                        "mode": "next_bar_open",
                        "signal_evaluation": "closed_bar",
                        "timeframe": TIMEFRAME,
                    },
                    "require_exact_event_fidelity": True,
                },
                "feature_contract": {
                    "required_features": ["k"],
                    "forbidden_substitutions": ["exact_event_to_generic_quantile"],
                },
            },
        },
        available_data=["ohlcv_swap_candles", "derived_factors"],
        data_version="recipe_identity_fixture_v1",
        code_version="recipe_identity_fixture_v1",
    )


def _quantile_term(factor, side, q=0.8, **overrides):
    term = {
        "factor": factor,
        "side": side,
        "q": q,
        "window": 240,
        "min_history": 80,
        "threshold_source": "prior_only_rolling_quantile",
    }
    term.update(overrides)
    return term


def _recipe(contract, terms, event_kind="mechanism_intersection", event_logic="all",
            direction="long", horizon=3, mapping="next_bar_open"):
    hypothesis = {
        "hypothesis_id": "hyp_recipe_dsl_identity",
        "mechanism_id": "mech_recipe_dsl_identity",
        "family": "admitted_quantile_intersection",
        "required_entry_timing": copy.deepcopy(
            (contract.get("event_contract") or {}).get("entry_timing") or {}
        ),
    }
    probe = {
        "event_id": "event_recipe_dsl_identity",
        "event_kind": event_kind,
        "event_logic": event_logic,
        "terms": copy.deepcopy(terms),
        "factor": (terms or [{}])[0].get("factor"),
        "side": (terms or [{}])[0].get("side"),
        "q": (terms or [{}])[0].get("q"),
        "trade_direction": direction,
        "horizon_bars": horizon,
        "execution_mapping": mapping,
        "primary_cost_scenario": "taker_taker",
        "primary_cost_per_trade": 0.001,
        "statistical_returns_are_post_cost": True,
        "exit_policy": {
            "mode": recipe_policy.EXIT_POLICY_MODE,
            "exit_bar": "entry_plus_horizon_minus_1",
            "price": "bar_close",
            "allow_early_take_profit": False,
        },
        "protective_stop_policy": {
            "mode": recipe_policy.PROTECTIVE_STOP_MODE,
            "price_pct": recipe_policy.PROTECTIVE_STOP_PCT,
            "applies_from": "entry_bar",
            "precedence": "protective_stop_before_time_exit",
        },
        "protective_stop_evaluated": True,
        "execution_leverage": recipe_policy.EXECUTION_LEVERAGE,
        "statistical_return_basis": recipe_policy.STATISTICAL_RETURN_BASIS,
    }
    return research_discovery.assembly_recipe(hypothesis, probe, contract)


def _exact_recipe(contract):
    return _recipe(
        contract,
        [{
            "factor": "k",
            "op": "<=",
            "value": 20,
            "timeframe": TIMEFRAME,
            "join": "root",
        }],
        event_kind="human_contract_exact",
        event_logic="ordered_joins",
    )


def _compile(recipe, contract):
    pack = formal_review_bridge._locked_spec_pack(
        {},
        {
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "trade_direction": recipe.get("trade_direction"),
        },
        recipe,
    )
    pack["research_contract"] = contract
    return pipeline_step_a.codex_implement_from_spec(pack)


def _validate(dsl, recipe, contract):
    return pipeline_step_a.validate_dsl_against_admitted_recipe(
        dsl, recipe, contract,
    )


def _error_blob(result):
    return json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)


def _assert_has_any(testcase, result, tokens):
    blob = _error_blob(result).lower()
    testcase.assertTrue(
        any(str(token).lower() in blob for token in tokens),
        "none of %s found in %s" % (tokens, blob),
    )


class GenericAdmittedRecipeCompilationTests(unittest.TestCase):
    def setUp(self):
        self.contract = _generic_contract()

    def test_high_and_low_terms_compile_to_prior_only_quantiles_under_all(self):
        recipe = _recipe(
            self.contract,
            [
                _quantile_term("close_z_20", "high", q=0.8),
                _quantile_term("volume_z", "low", q=0.8),
            ],
        )
        compiled = _compile(recipe, self.contract)
        self.assertTrue(compiled.get("ok"), compiled.get("fidelity_errors"))
        definition = compiled["dsl"]
        self.assertEqual("next_bar_open", definition["execution_mapping"])
        self.assertEqual(3, definition["max_hold_bars"])
        self.assertEqual("max_hold_only", definition["exit"]["exit_op"])
        self.assertAlmostEqual(0.009, definition["protective_stop_pct"])
        self.assertEqual(20, definition["execution_leverage"])
        self.assertIn("all", definition["entry"])
        self.assertNotIn("any", definition["entry"])
        self.assertEqual(2, len(definition["entry"]["all"]))

        leaves = {
            leaf["left"]["feature"]: leaf
            for leaf in definition["entry"]["all"]
        }
        self.assertEqual({"z20", "vol_z20"}, set(leaves))
        high = leaves["z20"]
        low = leaves["vol_z20"]
        self.assertEqual("gte", high["op"])
        self.assertEqual("lte", low["op"])
        self.assertEqual("z20", high["right"]["quantile_of"])
        self.assertEqual("vol_z20", low["right"]["quantile_of"])
        self.assertAlmostEqual(0.8, float(high["right"]["q"]))
        self.assertAlmostEqual(0.2, float(low["right"]["q"]))
        for leaf in (high, low):
            self.assertEqual(240, leaf["right"]["window"])
            self.assertEqual(80, leaf["right"]["min_history"])

        identity = _validate(definition, recipe, self.contract)
        self.assertTrue(identity.get("ok"), identity.get("errors"))

        # Re-evaluate every bar through the formal DSL operand implementation;
        # the admitted research masks and executable AND event must be identical.
        z_values = [float((index * 7) % 31) - 15.0 for index in range(360)]
        volume_values = [float((index * 11) % 37) - 18.0 for index in range(360)]
        expected_high = probe_protocol._causal_quantile_mask(
            z_values, "high", 0.8, window=240, min_history=80,
        )
        expected_low = probe_protocol._causal_quantile_mask(
            volume_values, "low", 0.8, window=240, min_history=80,
        )
        frame = pd.DataFrame({"z20": z_values, "vol_z20": volume_values})
        actual = [
            strategy_dsl.evaluate_expression(frame, index, definition["entry"])[0]
            for index in range(len(frame))
        ]
        self.assertEqual(
            [bool(a and b) for a, b in zip(expected_high, expected_low)],
            actual,
        )

    def test_event_logic_and_quantile_estimator_identity_are_mandatory(self):
        complete = [
            _quantile_term("close_z_20", "high"),
            _quantile_term("volume_z", "low"),
        ]
        cases = []
        cases.append(("event_logic", _recipe(
            self.contract, complete, event_logic=None,
        )))
        for missing in ("window", "min_history"):
            terms = copy.deepcopy(complete)
            terms[0].pop(missing)
            cases.append((missing, _recipe(self.contract, terms)))

        for expected_token, recipe in cases:
            with self.subTest(missing=expected_token):
                compiled = _compile(recipe, self.contract)
                self.assertFalse(compiled.get("ok"), _error_blob(compiled))
                self.assertIn(expected_token, _error_blob(compiled))

    def test_unsupported_factor_is_rejected_instead_of_aliased(self):
        recipe = _recipe(
            self.contract,
            [
                _quantile_term("atr_pct_14", "high"),
                _quantile_term("volume_z", "low"),
            ],
        )
        compiled = _compile(recipe, self.contract)
        self.assertFalse(compiled.get("ok"), _error_blob(compiled))
        self.assertIn("atr_pct_14", _error_blob(compiled))

    def test_generic_single_leaf_recipe_is_not_formally_admissible(self):
        recipe = _recipe(
            self.contract,
            [_quantile_term("close_z_20", "high")],
            event_kind="single_proxy",
        )
        compiled = _compile(recipe, self.contract)
        self.assertFalse(compiled.get("ok"), _error_blob(compiled))
        _assert_has_any(
            self, compiled,
            ("single", "at_least_two", "two_terms", "two terms"),
        )

    def test_formal_submit_passes_the_same_recipe_compiled_dsl_to_step_a(self):
        recipe = _recipe(
            self.contract,
            [
                _quantile_term("close_z_20", "high"),
                _quantile_term("volume_z", "low"),
            ],
        )
        # Account-quality vector required by formal-review bridge hard floor.
        account_returns = [0.04] * 12 + [-0.01] * 4
        identity = {
            "recipe_id": recipe["recipe_id"],
            "recipe": recipe,
            "hypothesis_id": recipe["hypothesis_id"],
            "mechanism_id": recipe["mechanism_id"],
            "returns": list(account_returns),
        }
        blueprint = {
            "ok": True,
            "present_to_human": True,
            "run_id": "recipe_formal_handoff_test",
            "research_contract": self.contract,
            "best_factor": dict(identity),
            "glm_research_brief": {
                "selected_recipe_id": recipe["recipe_id"],
                "selected_recipe": recipe,
            },
            "stages": {
                "selected": dict(identity),
                "admission_envelope": {
                    "research_contract_id": self.contract["contract_id"],
                    "recipe_ids": [recipe["recipe_id"]],
                    "candidates": [dict(identity)],
                },
                "assembly_lineage": {
                    "selected_recipe_id": recipe["recipe_id"],
                    "selected_is_in_admission_envelope": True,
                    "global_factor_mining_used": False,
                    "classic_or_unadmitted_switch_used": False,
                },
            },
        }
        job = {
            "job_id": "job_recipe_handoff",
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "trade_direction": "long",
            "source": "codex",
            "brief": "test",
        }
        captured = {}

        def fake_step_a(**kwargs):
            captured.update(kwargs)
            return {
                "ok": True, "task_id": "formal_task", "reason": "pending",
                "stage": "awaiting_human",
            }

        with tempfile.TemporaryDirectory() as root, mock.patch.dict(
            os.environ, {"VECTOR_ROOT": root}, clear=False,
        ), mock.patch.object(
            pipeline_step_a, "run_creation_pipeline_step_a", side_effect=fake_step_a,
        ):
            result = formal_review_bridge.submit_blueprint_to_formal_review(
                blueprint, job,
            )
        self.assertTrue(result.get("ok"), _error_blob(result))
        self.assertTrue(result.get("submission_created"), _error_blob(result))
        self.assertTrue(captured.get("formal_submission"))
        handed = captured["prebuilt_spec_pack"]
        self.assertEqual(recipe, handed["admitted_recipe_lock"])
        compiler_entry = handed["dsl"]["entry"]
        self.assertEqual(
            compiler_entry,
            handed["dsl_long"]["entry"],
        )
        self.assertIn("all", compiler_entry)


class ExactRecipeAndFinalIdentityValidationTests(unittest.TestCase):
    def setUp(self):
        self.contract = _exact_contract()
        self.recipe = _exact_recipe(self.contract)
        self.compiled = _compile(self.recipe, self.contract)

    def test_only_valid_admitted_recipe_can_authorize_formal_submission(self):
        diagnostic = pipeline_step_a._formal_submission_authority(
            False, self.contract, self.recipe,
        )
        self.assertFalse(diagnostic["authorized"])
        self.assertTrue(diagnostic["diagnostic_only"])
        missing = pipeline_step_a._formal_submission_authority(
            True, self.contract, None,
        )
        self.assertFalse(missing["authorized"])
        authorized = pipeline_step_a._formal_submission_authority(
            True, self.contract, self.recipe,
        )
        self.assertTrue(authorized["authorized"], authorized["reasons"])
        tampered_contract = copy.deepcopy(self.contract)
        tampered_contract["target"]["direction"] = "short"
        tampered = pipeline_step_a._formal_submission_authority(
            True, tampered_contract, self.recipe,
        )
        self.assertFalse(tampered["authorized"])
        self.assertIn("research_contract_body_hash_mismatch", tampered["reasons"])

    def test_exact_human_single_leaf_passes_but_threshold_drift_fails(self):
        self.assertTrue(
            self.compiled.get("ok"), self.compiled.get("fidelity_errors"),
        )
        definition = self.compiled["dsl"]
        self.assertEqual("exact_human_contract",
                         definition["entry_condition_policy"]["mode"])
        self.assertEqual("k", definition["entry"]["left"]["feature"])
        self.assertEqual("lte", definition["entry"]["op"])
        self.assertEqual(20, definition["entry"]["right"]["value"])
        self.assertTrue(
            _validate(definition, self.recipe, self.contract).get("ok"),
        )

        changed = copy.deepcopy(definition)
        changed["entry"]["right"]["value"] = 21
        # Keep the DSL's own single-condition attestation internally coherent;
        # the admitted-recipe validator must still catch the semantic drift.
        changed["entry_condition_policy"]["entry_semantics_hash"] = (
            strategy_dsl.dsl_hash(changed["entry"])
        )
        strategy_dsl.validate_strategy(changed)
        validation = _validate(changed, self.recipe, self.contract)
        self.assertFalse(validation.get("ok"), _error_blob(validation))
        _assert_has_any(self, validation, ("entry", "semantic"))

    def test_direction_horizon_and_execution_mapping_drift_each_fail(self):
        self.assertTrue(self.compiled.get("ok"), _error_blob(self.compiled))
        definition = self.compiled["dsl"]
        mutations = (
            ("direction", "short", ("direction",)),
            ("max_hold_bars", definition["max_hold_bars"] + 1,
             ("horizon", "max_hold")),
            ("execution_mapping", "bar_close",
             ("execution", "mapping")),
        )
        for field, value, expected_tokens in mutations:
            with self.subTest(field=field):
                changed = copy.deepcopy(definition)
                changed[field] = value
                validation = _validate(changed, self.recipe, self.contract)
                self.assertFalse(validation.get("ok"), _error_blob(validation))
                _assert_has_any(self, validation, expected_tokens)

    def test_recipe_hash_tamper_fails_compilation_and_final_validation(self):
        self.assertTrue(self.compiled.get("ok"), _error_blob(self.compiled))
        tampered = copy.deepcopy(self.recipe)
        tampered["terms"][0]["value"] = 19
        integrity_ok, expected_id = research_discovery.verify_assembly_recipe(tampered)
        self.assertFalse(integrity_ok)
        self.assertNotEqual(tampered["recipe_id"], expected_id)

        compiled = _compile(tampered, self.contract)
        self.assertFalse(compiled.get("ok"), _error_blob(compiled))
        _assert_has_any(self, compiled, ("hash", "integrity"))
        validation = _validate(self.compiled["dsl"], tampered, self.contract)
        self.assertFalse(validation.get("ok"), _error_blob(validation))
        _assert_has_any(self, validation, ("hash", "integrity"))


if __name__ == "__main__":
    unittest.main()
