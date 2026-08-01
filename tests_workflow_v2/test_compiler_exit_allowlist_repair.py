# -*- coding: utf-8 -*-
"""Regression contract for Step-A exit compilation and feature allowlists.

These tests intentionally exercise the public ``codex_implement_from_spec``
boundary.  The compiler must preserve field roles (protective stop versus take
profit), validate every explicit parameter without replacing it by a default,
and audit features consumed anywhere in the entry/exit expression trees.
"""
from __future__ import print_function

import copy
import json
import unittest

import auto_trade_strategy_dsl as strategy_dsl
from dual_engine_workflow_v2 import research_contract as contract_mod
from dual_engine_workflow_v2.pipeline_step_a import codex_implement_from_spec


SYMBOL = "ADA-USDT-SWAP"
TIMEFRAME = "5m"


def _mechanism_spec(exit_logic, take_profit_logic=None,
                    stop_logic="protective fixed 0.9% stop loss",
                    invalidation_logic="none"):
    return {
        "mechanism_id": "mech_exit_allowlist_repair",
        "mechanism_name": "exit_allowlist_repair",
        "mechanism_family": "custom_machine_contract",
        "market_inefficiency": "structured event",
        "counterparty_source": "forced participant",
        "why_edge_exists": "mechanical delay",
        "edge_decay_conditions": "event disappears",
        "required_market_regime": "declared regime",
        "entry_logic": "K <= 20 at the five-minute close",
        "exit_logic": exit_logic,
        "stop_logic": stop_logic,
        "take_profit_logic": (
            exit_logic if take_profit_logic is None else take_profit_logic
        ),
        "invalidation_logic": invalidation_logic,
        "non_negotiable_rules": ["protective stop is not take profit"],
        "tunable_parameters": ["max_hold_bars"],
        "forbidden_transformations": ["do not replace KDJ with RSI"],
        "expected_trade_frequency_class": "low",
        "expected_holding_period": "3_to_18_bars_5m",
        "suitable_symbols": [SYMBOL],
        "suitable_timeframes": [TIMEFRAME],
    }


def _research_contract(allowed=None, enforce=False, include_condition_exit=False):
    entry_condition = {
        "id": "entry_k_oversold",
        "indicator": "k",
        "operator": "<=",
        "value": 20,
        "timeframe": TIMEFRAME,
        "join": "and",
        "role": "entry",
    }
    event = {
        "exact_event_text": "K <= 20 at bar close",
        "required_clauses": ["keep the exact K condition"],
        "entry_conditions": [entry_condition],
        "exit_conditions": [],
        "required_conditions": [entry_condition],
        "require_exact_event_fidelity": True,
        "entry_timing": {
            "mode": "bar_close",
            "signal_evaluation": "closed_bar",
            "timeframe": TIMEFRAME,
        },
    }
    if include_condition_exit:
        exit_condition = {
            "id": "exit_j_exhausted",
            "indicator": "j",
            "operator": ">=",
            "value": 89,
            "timeframe": TIMEFRAME,
            "join": "or",
            "role": "exit",
            "exit_role": "take_profit",
        }
        event["exit_conditions"] = [exit_condition]
        event["required_conditions"].append(exit_condition)
    return {
        "schema": "qiyu_research_contract_v2",
        "contract_id": "rc_89abcdef0123456789abcdef",
        "target": {
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "direction": "long",
            "additional_timeframes": [],
        },
        "event_contract": event,
        "feature_contract": {
            # Deliberately keep required_features empty: when the allowlist is
            # explicitly closed, an entry condition cannot bypass it merely by
            # also being described as required elsewhere in the contract.
            "required_features": [],
            "allowed_features": list(allowed or []),
            "allowed_features_enforced": bool(enforce),
            "forbidden_substitutions": [],
            "cross_asset_symbols": [],
        },
        "data_contract": {
            "required": ["ohlcv"],
            "available": ["ohlcv"],
            "missing": [],
        },
    }


def _pack(exit_logic, take_profit_logic=None, stop_logic=None,
          invalidation_logic="none", allowed=None, enforce=False,
          include_condition_exit=False):
    kwargs = {
        "exit_logic": exit_logic,
        "take_profit_logic": take_profit_logic,
        "invalidation_logic": invalidation_logic,
    }
    if stop_logic is not None:
        kwargs["stop_logic"] = stop_logic
    return {
        "mechanism_spec": _mechanism_spec(**kwargs),
        "meta": {
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "direction": "long",
            "title": "compiler exit allowlist repair",
        },
        "research_contract": _research_contract(
            allowed=allowed,
            enforce=enforce,
            include_condition_exit=include_condition_exit,
        ),
    }


def _exit_op_leaves(tree):
    leaves = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("exit_op"):
                leaves.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(tree)
    return leaves


def _errors(result):
    return list(result.get("fidelity_errors") or [])


class ClosedFeatureAllowlistTests(unittest.TestCase):
    def test_required_feature_cannot_bypass_real_compiled_closed_allowlist(self):
        contract = contract_mod.compile_contract(
            "开仓K<=20",
            SYMBOL, TIMEFRAME, "long",
            constraints={
                "research_contract": {
                    "feature_contract": {
                        "allowed_features": ["j"],
                        "allowed_features_enforced": True,
                    },
                },
            },
        )
        pack = _pack("3.0x ATR trailing")
        pack["research_contract"] = contract
        out = codex_implement_from_spec(pack)
        self.assertFalse(out.get("ok"), out)
        errors = _errors(out)
        self.assertIn("required_feature_outside_allowlist:k", errors)
        self.assertIn("feature_outside_allowlist:k", errors)

    def test_closed_allowlist_audits_condition_exit_and_exit_op_dependencies(self):
        out = codex_implement_from_spec(_pack(
            "3.0x ATR trailing",
            allowed=["k"],
            enforce=True,
            include_condition_exit=True,
        ))
        self.assertFalse(out.get("ok"), out)
        self.assertIsNone(out.get("dsl"))
        errors = _errors(out)
        # j lives only in the condition-style exit.  atr14/high/low/close are
        # implicit runtime dependencies of the structured ATR exit operator.
        for feature in ("j", "atr14", "high", "low", "close"):
            self.assertIn("feature_outside_allowlist:%s" % feature, errors)
        self.assertNotIn("feature_outside_allowlist:k", errors)

    def test_explicitly_empty_closed_allowlist_is_not_treated_as_open(self):
        out = codex_implement_from_spec(_pack(
            "3.0x ATR trailing",
            allowed=[],
            enforce=True,
            include_condition_exit=True,
        ))
        self.assertFalse(out.get("ok"), out)
        errors = _errors(out)
        for feature in ("k", "j", "atr14", "high", "low", "close"):
            self.assertIn("feature_outside_allowlist:%s" % feature, errors)

    def test_complete_entry_exit_allowlist_is_accepted(self):
        out = codex_implement_from_spec(_pack(
            "3.0x ATR trailing",
            allowed=["k", "j", "atr14", "high", "low", "close"],
            enforce=True,
            include_condition_exit=True,
        ))
        self.assertTrue(out.get("ok"), _errors(out))
        self.assertEqual([], _errors(out))
        self.assertEqual(
            {"k", "j", "atr14", "high", "low", "close"},
            set(out["fidelity_evidence"]["used_features"]),
        )


class ExitFieldSeparationTests(unittest.TestCase):
    def test_protective_0_9_percent_stop_is_never_compiled_as_fixed_tp(self):
        out = codex_implement_from_spec(_pack(
            "3.0x ATR trailing",
            take_profit_logic="3.0x ATR trailing",
            stop_logic="protective fixed 0.9% stop loss",
        ))
        self.assertTrue(out.get("ok"), _errors(out))
        leaves = _exit_op_leaves(out["dsl"]["exit"])
        self.assertEqual(["atr_trailing"], [x["exit_op"] for x in leaves])
        self.assertNotIn("fixed_pct_tp", json.dumps(leaves, sort_keys=True))

    def test_fixed_two_percent_take_profit_compiles(self):
        out = codex_implement_from_spec(_pack(
            "fixed_pct_tp 2.0%",
            take_profit_logic="fixed_pct_tp 2.0%",
        ))
        self.assertTrue(out.get("ok"), _errors(out))
        leaves = _exit_op_leaves(out["dsl"]["exit"])
        self.assertEqual(1, len(leaves))
        self.assertEqual("fixed_pct_tp", leaves[0]["exit_op"])
        self.assertAlmostEqual(
            0.02,
            strategy_dsl.refuse_fixed_tiny_tp(
                leaves[0].get("pct", leaves[0].get("price_pct")),
                context="compiled_fixture",
            ),
        )

    def test_fixed_take_profit_below_two_percent_is_refused_not_converted(self):
        out = codex_implement_from_spec(_pack(
            "fixed_pct_tp 1.9%",
            take_profit_logic="fixed_pct_tp 1.9%",
        ))
        self.assertFalse(out.get("ok"), out)
        self.assertIsNone(out.get("dsl"))
        blob = json.dumps(_errors(out), ensure_ascii=False)
        self.assertIn("fixed_pct_tp", blob)
        self.assertIn("REFUSED", blob)


class StructuredExitParameterTests(unittest.TestCase):
    def test_partial_trailing_and_swing_parameters_are_preserved_and_deduplicated(self):
        # The same exits are deliberately repeated across their dedicated
        # fields.  Compilation must emit one leaf per semantic exit, and the
        # partial's earlier ATR number must not contaminate the trailing parser.
        out = codex_implement_from_spec(_pack(
            "partial 2.0x ATR ratio 50% OR 3.5x ATR trailing "
            "OR swing extreme lookback 17",
            take_profit_logic=(
                "partial 2.0x ATR ratio 50%; 3.5x ATR trailing"
            ),
            stop_logic=(
                "protective fixed 0.9% stop loss plus "
                "swing extreme lookback 17 invalidation"
            ),
            invalidation_logic="swing extreme lookback 17",
        ))
        self.assertTrue(out.get("ok"), _errors(out))
        leaves = _exit_op_leaves(out["dsl"]["exit"])
        self.assertEqual(3, len(leaves), leaves)
        self.assertEqual(
            {"partial_tp_atr", "atr_trailing", "swing_extreme"},
            set(x["exit_op"] for x in leaves),
        )
        by_op = {x["exit_op"]: x for x in leaves}

        partial = by_op["partial_tp_atr"]
        self.assertEqual("take_profit", partial["role"])
        self.assertAlmostEqual(2.0, float(partial["n_atr"]))
        self.assertEqual(14, int(partial["atr_period"]))
        self.assertAlmostEqual(0.5, float(partial["partial_tp_ratio"]))

        trailing = by_op["atr_trailing"]
        self.assertEqual("take_profit", trailing["role"])
        self.assertAlmostEqual(3.5, float(trailing["n_atr"]))
        self.assertEqual(14, int(trailing["atr_period"]))

        swing = by_op["swing_extreme"]
        self.assertEqual("invalidation", swing["role"])
        self.assertEqual(17, int(swing["lookback"]))

    def test_explicit_out_of_range_parameters_are_rejected_not_defaulted(self):
        cases = [
            (
                "6.0x ATR trailing",
                "6.0x ATR trailing",
                "none",
                "atr_trailing",
            ),
            (
                "partial 1.0x ATR ratio 50%",
                "partial 1.0x ATR ratio 50%",
                "none",
                "partial_tp_atr",
            ),
            (
                "partial 2.0x ATR ratio 95%",
                "partial 2.0x ATR ratio 95%",
                "none",
                "partial_tp_ratio",
            ),
            (
                "swing extreme lookback 4",
                "none",
                "swing extreme lookback 4",
                "swing_extreme",
            ),
        ]
        for exit_logic, take_profit, invalidation, marker in cases:
            with self.subTest(exit_logic=exit_logic):
                out = codex_implement_from_spec(_pack(
                    exit_logic,
                    take_profit_logic=take_profit,
                    invalidation_logic=invalidation,
                ))
                self.assertFalse(out.get("ok"), out)
                self.assertIsNone(out.get("dsl"))
                self.assertIn(
                    marker,
                    json.dumps(_errors(out), ensure_ascii=False),
                )


if __name__ == "__main__":
    unittest.main()
