# -*- coding: utf-8 -*-
from __future__ import print_function

import copy
import os
import tempfile
import unittest
from unittest import mock

import pandas as pd

import auto_trade_human_confirm_pipeline as human_confirm
import auto_trade_strategy_dsl as dsl

_TEST_ROOT = os.path.join(tempfile.gettempdir(), "qiyu_dsl_execution_repair")
os.environ.setdefault("VECTOR_BACKTEST_LOG_DIR", os.path.join(_TEST_ROOT, "logs"))
os.environ.setdefault("VECTOR_STRATEGY_CONFIG_PATH", os.path.join(_TEST_ROOT, "classic.json"))
os.environ.setdefault("VECTOR_DSL_STRATEGY_CONFIG_PATH", os.path.join(_TEST_ROOT, "dsl.json"))
import backtest_engine_v2 as backtest_engine
from dual_engine_workflow_v2.pipeline_step_a import codex_implement_from_spec


def _leaf(condition_id, feature, op, value):
    return {
        "id": condition_id,
        "left": {"feature": feature},
        "op": op,
        "right": {"value": value},
    }


def _strategy(mapping="bar_close", single=False):
    entry = _leaf("e_close", "close", "gt", 0.0)
    if not single:
        entry = {"all": [entry, _leaf("e_open", "open", "gt", 0.0)]}
    row = {
        "schema": dsl.SCHEMA,
        "key": "dsl_execution_test",
        "name": "dsl execution test",
        "direction": "long",
        "timeframe": "5m",
        "supported_instruments": ["ADA-USDT-SWAP"],
        "entry": entry,
        "exit": _leaf("x_never", "close", "lt", -1.0),
        "max_hold_bars": 1,
        "execution_mapping": mapping,
        "description": "execution test",
        "origin": "unit_test",
        "version": 1,
        "live_enabled": False,
        "auto_trade_eligible": False,
    }
    return row


def _frame(n=270):
    index = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    values = [100.0 + index_i * 0.01 for index_i in range(n)]
    frame = pd.DataFrame({
        "open": [value + 0.005 for value in values],
        "high": [value + 0.02 for value in values],
        "low": [value - 0.02 for value in values],
        "close": values,
        "cci": [0.0] * n,
        "k": [50.0] * n,
        "d": [50.0] * n,
        "j": [50.0] * n,
        "macd_stick": [0.0] * n,
    }, index=index)
    return frame


def _spec():
    return {
        "mechanism_id": "mech_single_exact_test",
        "mechanism_name": "single exact test",
        "mechanism_family": "custom_machine_contract",
        "market_inefficiency": "literal human event",
        "counterparty_source": "forced participant",
        "why_edge_exists": "delayed response",
        "edge_decay_conditions": "event vanishes",
        "required_market_regime": "declared",
        "entry_logic": "K <= 20",
        "exit_logic": "3.0x ATR trailing OR swing extreme lookback 20",
        "stop_logic": "protective fixed 0.9 percent",
        "take_profit_logic": "3.0x ATR trailing",
        "invalidation_logic": "swing extreme lookback 20",
        "non_negotiable_rules": ["literal event"],
        "tunable_parameters": [],
        "forbidden_transformations": [],
        "expected_trade_frequency_class": "low",
        "expected_holding_period": "3 bars",
        "suitable_symbols": ["ADA-USDT-SWAP"],
        "suitable_timeframes": ["5m"],
    }


def _single_contract(mapping="next_bar_open"):
    return {
        "schema": "qiyu_research_contract_v2",
        "contract_id": "rc_0123456789abcdef01234567",
        "target": {
            "symbol": "ADA-USDT-SWAP", "timeframe": "5m",
            "direction": "long", "additional_timeframes": [],
        },
        "event_contract": {
            "required_conditions": [{
                "indicator": "k", "operator": "<=", "value": 20,
                "timeframe": "5m", "join": "and",
            }],
            "entry_conditions": [{
                "indicator": "k", "operator": "<=", "value": 20,
                "timeframe": "5m", "join": "and",
            }],
            "entry_timing": {"mode": mapping, "timeframe": "5m"},
            "require_exact_event_fidelity": True,
        },
        "feature_contract": {
            "required_features": ["k"], "allowed_features": ["k", "atr14"],
            "forbidden_substitutions": [], "cross_asset_symbols": [],
        },
        "data_contract": {
            "required": ["ohlcv"], "available": ["ohlcv"], "missing": [],
        },
    }


class DSLExecutionMappingTests(unittest.TestCase):
    def test_next_bar_open_is_pending_and_audits_signal_and_fill(self):
        frame = _frame()
        close_result = dsl.backtest_dsl(
            frame, _strategy("bar_close"), leverage=1,
            stop_loss_pct=0.5, fee_rate_per_side=0.0,
            slippage_rate_per_side=0.0,
        )
        next_result = dsl.backtest_dsl(
            frame, _strategy("next_bar_open"), leverage=1,
            stop_loss_pct=0.5, fee_rate_per_side=0.0,
            slippage_rate_per_side=0.0,
        )
        close_trade = close_result["trades"][0]
        next_trade = next_result["trades"][0]
        self.assertEqual(close_trade["signal_index"], close_trade["entry_index"])
        self.assertEqual(250, next_trade["signal_index"])
        self.assertEqual(251, next_trade["entry_index"])
        self.assertEqual(251, next_trade["exit_index"])
        self.assertEqual(float(frame["open"].iloc[251]), next_trade["entry_price"])
        self.assertEqual("next_bar_open", next_trade["execution_mapping"])

        three_bar = _strategy("next_bar_open")
        three_bar["max_hold_bars"] = 3
        three_result = dsl.backtest_dsl(
            frame, three_bar, leverage=1, stop_loss_pct=0.5,
            fee_rate_per_side=0.0, slippage_rate_per_side=0.0,
        )
        three_trade = three_result["trades"][0]
        self.assertEqual(
            three_trade["entry_index"] + 3 - 1,
            three_trade["exit_index"],
        )

    def test_formal_engine_uses_the_same_next_open_mapping(self):
        frame = _frame(90)
        definition = dsl.validate_strategy(_strategy("next_bar_open"))
        key = definition["key"]
        old_registry = backtest_engine.STRATEGIES_BY_TIMEFRAME["5m"].get(key)
        old_definition = backtest_engine.DSL_STRATEGY_DEFINITIONS.get(key)
        backtest_engine.STRATEGIES_BY_TIMEFRAME["5m"][key] = (
            backtest_engine._dsl_entry_factory(definition),
            backtest_engine._dsl_exit_factory(definition),
            "long",
        )
        backtest_engine.DSL_STRATEGY_DEFINITIONS[key] = definition
        friction = {
            "fee_rate_per_side": 0.0, "slippage_rate_per_side": 0.0,
            "half_spread_rate_per_side": 0.0, "impact_rate_per_side": 0.0,
            "latency_rate_per_side": 0.0, "funding_rate_per_8h": 0.0,
        }
        try:
            with mock.patch.object(backtest_engine, "load_execution_friction", return_value=friction), \
                    mock.patch.object(backtest_engine, "load_strategy_params", return_value={}), \
                    mock.patch.object(backtest_engine, "load_strategy_metadata", return_value={}), \
                    mock.patch.object(backtest_engine, "_find_local_data_directory", return_value="local"), \
                    mock.patch.object(backtest_engine, "load_parquet_range", return_value=frame), \
                    mock.patch.object(backtest_engine, "precompute_indicators", side_effect=lambda value, timeframe=None: value), \
                    mock.patch.object(backtest_engine, "_beijing_input_to_utc_naive", return_value=frame.index[0]), \
                    mock.patch.object(backtest_engine, "_build_kwargs", return_value={"_dsl_frame": frame}), \
                    mock.patch.object(backtest_engine, "_market_regime_labels", return_value=["test"] * len(frame)), \
                    mock.patch.object(backtest_engine, "_sha256_file", return_value="hash"):
                result = backtest_engine.run_backtest(
                    key, instId="ADA-USDT-SWAP", start_time="2026-01-01",
                    end_time="2026-01-02", leverage=1,
                    stop_loss_pct=0.5, timeframe="5m",
                )
            self.assertNotIn("error", result)
            trade = result["trades"][0]
            self.assertEqual(62, trade["signal_index"])
            self.assertEqual(63, trade["entry_index"])
            self.assertEqual(63, trade["exit_index"])
            self.assertEqual(float(frame["open"].iloc[63]), trade["entry_price"])
            self.assertEqual("next_bar_open", result["consistency_audit"]["execution_mapping"])
        finally:
            if old_registry is None:
                backtest_engine.STRATEGIES_BY_TIMEFRAME["5m"].pop(key, None)
            else:
                backtest_engine.STRATEGIES_BY_TIMEFRAME["5m"][key] = old_registry
            if old_definition is None:
                backtest_engine.DSL_STRATEGY_DEFINITIONS.pop(key, None)
            else:
                backtest_engine.DSL_STRATEGY_DEFINITIONS[key] = old_definition

    def test_human_confirm_treats_mapping_as_execution_not_future_data(self):
        strategy = _strategy("next_bar_open")
        self.assertEqual((True, None), human_confirm._no_lookahead(strategy))
        changed = copy.deepcopy(strategy)
        changed["description"] = "smuggled next_bar future feature"
        self.assertEqual(False, human_confirm._no_lookahead(changed)[0])

    def test_execution_mapping_is_material_identity_with_legacy_close_default(self):
        explicit_close = _strategy("bar_close")
        legacy_close = copy.deepcopy(explicit_close)
        legacy_close.pop("execution_mapping")
        next_open = _strategy("next_bar_open")
        self.assertEqual(dsl.executable_hash(explicit_close),
                         dsl.executable_hash(legacy_close))
        self.assertNotEqual(dsl.executable_hash(explicit_close),
                            dsl.executable_hash(next_open))
        self.assertFalse(dsl.is_near_duplicate_logic(explicit_close, next_open))


class SingleExactEntryPolicyTests(unittest.TestCase):
    def test_generic_single_entry_remains_rejected(self):
        with self.assertRaises(dsl.DSLValidationError):
            dsl.validate_strategy(_strategy(single=True))

    def test_step_a_attests_one_exact_human_condition(self):
        contract = _single_contract()
        result = codex_implement_from_spec({
            "mechanism_spec": _spec(),
            "meta": {
                "symbol": "ADA-USDT-SWAP", "timeframe": "5m",
                "direction": "long", "title": "single exact",
            },
            "research_contract": contract,
        })
        self.assertTrue(result["ok"], result.get("fidelity_errors"))
        compiled = result["dsl"]
        self.assertEqual("next_bar_open", compiled["execution_mapping"])
        self.assertEqual("exact_human_contract",
                         compiled["entry_condition_policy"]["mode"])
        self.assertEqual(compiled, dsl.validate_strategy(compiled))
        changed = copy.deepcopy(compiled)
        changed["entry"]["right"]["value"] = 21
        with self.assertRaises(dsl.DSLValidationError):
            dsl.validate_strategy(changed)


class PriorOnlyQuantileOperandTests(unittest.TestCase):
    def test_quantile_threshold_uses_only_strictly_prior_values(self):
        frame = pd.DataFrame({
            "open": [1.0] * 7,
            "close": [0.0, 1.0, 2.0, 3.0, 4.0, 1000.0, -1000.0],
        })
        expression = {
            "id": "e_prior_q",
            "left": {"feature": "close"},
            "op": "gte",
            "right": {
                "quantile_of": "close", "q": 0.8,
                "window": 5, "min_history": 5,
            },
        }
        passed, details = dsl.evaluate_expression(frame, 5, expression, explain=True)
        self.assertTrue(passed)
        self.assertEqual(3.0, details[0]["right"])
        changed = frame.copy()
        changed.loc[5, "close"] = 1000000000.0
        passed_changed, changed_details = dsl.evaluate_expression(
            changed, 5, expression, explain=True,
        )
        self.assertTrue(passed_changed)
        self.assertEqual(3.0, changed_details[0]["right"])
        insufficient, insufficient_details = dsl.evaluate_expression(
            frame, 4, expression, explain=True,
        )
        self.assertFalse(insufficient)
        self.assertIn("insufficient prior-only quantile history",
                      insufficient_details[0]["error"])

    def test_quantile_operand_is_bounded_by_validator(self):
        strategy = _strategy()
        strategy["entry"]["all"][0]["right"] = {
            "quantile_of": "close", "q": 0.8,
            "window": 240, "min_history": 80,
        }
        self.assertEqual(strategy, dsl.validate_strategy(strategy))
        strategy["entry"]["all"][0]["right"]["window"] = 241
        with self.assertRaises(dsl.DSLValidationError):
            dsl.validate_strategy(strategy)
        strategy["entry"]["all"][0]["right"]["window"] = 5.5
        with self.assertRaises(dsl.DSLValidationError):
            dsl.validate_strategy(strategy)


if __name__ == "__main__":
    unittest.main()
