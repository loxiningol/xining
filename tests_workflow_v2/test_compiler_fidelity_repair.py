# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import unittest

from dual_engine_workflow_v2.pipeline_step_a import (
    _compiler_finalize_fidelity,
    _compiler_parse_hold,
    codex_implement_from_spec,
)


def _spec(family="custom_machine_contract"):
    return {
        "mechanism_id": "mech_compiler_fidelity_test",
        "mechanism_name": "compiler_fidelity_test",
        "mechanism_family": family,
        "market_inefficiency": "structured event",
        "counterparty_source": "forced participant",
        "why_edge_exists": "mechanical delay",
        "edge_decay_conditions": "event disappears",
        "required_market_regime": "declared regime",
        "entry_logic": "K <= 20 OR J >= 89",
        "exit_logic": "3.0x ATR trailing OR swing extreme lookback 20",
        "stop_logic": "protective fixed 0.9 percent",
        "take_profit_logic": "3.0x ATR trailing",
        "invalidation_logic": "swing extreme lookback 20",
        "non_negotiable_rules": ["keep_exact_event"],
        "tunable_parameters": ["max_hold_bars"],
        "forbidden_transformations": ["do not replace KDJ with RSI"],
        "expected_trade_frequency_class": "low",
        "expected_holding_period": "3_to_18_bars_5m",
        "suitable_symbols": ["ADA-USDT-SWAP"],
        "suitable_timeframes": ["5m"],
    }


def _contract(**overrides):
    contract = {
        "contract_id": "rc_compiler_fidelity_v2",
        "target": {
            "symbol": "ADA-USDT-SWAP",
            "timeframe": "5m",
            "direction": "long",
            "additional_timeframes": [],
        },
        "event_contract": {
            "exact_event_text": "K <= 20 OR J >= 89, evaluated at bar close",
            "required_clauses": ["K oversold or J exhausted"],
            "required_conditions": [
                {"indicator": "k", "operator": "<=", "value": 20,
                 "timeframe": "5m", "join": "and"},
                {"indicator": "j", "operator": ">=", "value": 89,
                 "timeframe": "5m", "join": "or"},
            ],
            "entry_timing": {"mode": "bar_close", "timeframe": "5m"},
        },
        "feature_contract": {
            "required_features": ["k", "j"],
            "allowed_features": ["k", "j", "atr14"],
            "forbidden_substitutions": ["rsi14"],
            "cross_asset_symbols": [],
        },
        "data_contract": {
            "required": ["ohlcv", "k", "j"],
            "available": ["ohlcv", "k", "j"],
            "missing": [],
        },
    }
    contract.update(overrides)
    return contract


def _pack(contract=None, family="custom_machine_contract"):
    return {
        "mechanism_spec": _spec(family),
        "meta": {
            "symbol": "ADA-USDT-SWAP",
            "timeframe": "5m",
            "direction": "long",
            "title": "compiler fidelity test",
        },
        "research_contract": contract if contract is not None else _contract(),
    }


class CompilerFidelityRepairTests(unittest.TestCase):
    def test_v2_exact_or_event_is_compiled_and_range_uses_upper_bound(self):
        out = codex_implement_from_spec(_pack())
        self.assertTrue(out["ok"], out.get("fidelity_errors"))
        self.assertEqual(out["dsl"]["max_hold_bars"], 18)
        self.assertIn("any", out["dsl"]["entry"])
        leaves = out["dsl"]["entry"]["any"]
        self.assertEqual([(x["left"]["feature"], x["op"], x["right"]["value"])
                          for x in leaves], [("k", "lte", 20), ("j", "gte", 89)])
        self.assertEqual(out["fidelity_errors"], [])
        self.assertTrue(out["fidelity_evidence"]["structured_contract_verified"])

    def test_unknown_family_no_longer_defaults_to_sweep_reclaim(self):
        contract = _contract()
        contract["event_contract"] = {"entry_timing": {"mode": "bar_close", "timeframe": "5m"}}
        contract["feature_contract"] = {"required_features": [], "allowed_features": [],
                                        "forbidden_substitutions": [], "cross_asset_symbols": []}
        out = codex_implement_from_spec(_pack(contract, family="alien_unimplemented_alpha"))
        self.assertFalse(out["ok"])
        self.assertIsNone(out["dsl"])
        self.assertTrue(any(x.startswith("mechanism_family_unimplemented:")
                            for x in out["fidelity_errors"]))

    def test_cross_asset_mechanism_fails_instead_of_using_same_symbol_proxy(self):
        contract = _contract()
        contract["feature_contract"] = copy.deepcopy(contract["feature_contract"])
        contract["feature_contract"]["cross_asset_symbols"] = ["BTC-USDT-SWAP"]
        out = codex_implement_from_spec(_pack(contract, family="btc_lead_lag"))
        self.assertFalse(out["ok"])
        self.assertIsNone(out["dsl"])
        self.assertTrue(any(x.startswith("cross_asset_semantics_unsupported:")
                            for x in out["fidelity_errors"]))

    def test_next_bar_open_is_compiled_as_an_explicit_execution_mapping(self):
        contract = _contract()
        contract["event_contract"] = copy.deepcopy(contract["event_contract"])
        contract["event_contract"]["entry_timing"] = {
            "mode": "next_bar_open", "timeframe": "5m"}
        out = codex_implement_from_spec(_pack(contract))
        self.assertTrue(out["ok"], out.get("fidelity_errors"))
        self.assertEqual("next_bar_open", out["dsl"]["execution_mapping"])
        self.assertEqual(
            "next_bar_open",
            out["fidelity_evidence"]["entry_timing"]["compiled_execution_mapping"],
        )

    def test_unavailable_higher_timeframe_indicator_is_not_downgraded_to_5m(self):
        contract = _contract()
        contract["event_contract"] = copy.deepcopy(contract["event_contract"])
        contract["event_contract"]["required_conditions"][0] = {
            "indicator": "j", "operator": "<=", "value": 20,
            "timeframe": "1h", "join": "and",
        }
        contract["target"] = copy.deepcopy(contract["target"])
        contract["target"]["additional_timeframes"] = ["1h"]
        out = codex_implement_from_spec(_pack(contract))
        self.assertFalse(out["ok"])
        self.assertTrue(any(x.startswith("required_condition_feature_unsupported:1:1h:j")
                            for x in out["fidelity_errors"]))

    def test_structured_utc_session_is_preserved(self):
        contract = _contract()
        contract["event_contract"] = {
            "exact_event_text": "NY window and London high reclaim",
            "required_clauses": ["keep UTC window"],
            "required_conditions": [
                {"indicator": "close", "operator": ">", "value": "london_high",
                 "timeframe": "5m", "join": "and"},
                {"indicator": "vol_ma20_ratio", "operator": ">", "value": 1.2,
                 "timeframe": "5m", "join": "and"},
            ],
            "session_window": {"start": "12:30", "end": "15:30", "timezone": "UTC"},
            "entry_timing": {"mode": "bar_close", "timeframe": "5m"},
        }
        contract["feature_contract"] = {
            "required_features": ["hour_utc", "london_high", "vol_ma20_ratio"],
            "allowed_features": ["hour_utc", "london_high", "vol_ma20_ratio", "atr14"],
            "forbidden_substitutions": [], "cross_asset_symbols": [],
        }
        out = codex_implement_from_spec(_pack(contract, family="session_open_breakout"))
        self.assertTrue(out["ok"], out.get("fidelity_errors"))
        self.assertIn("hour_utc", out["fidelity_evidence"]["used_features"])
        self.assertEqual(out["dsl"]["entry"]["all"][0]["all"][0]["right"]["value"], 12.5)
        self.assertEqual(out["dsl"]["entry"]["all"][0]["all"][1]["right"]["value"], 15.5)

    def test_exit_role_condition_is_compiled_as_or_exit_not_entry(self):
        contract = _contract()
        contract["event_contract"] = copy.deepcopy(contract["event_contract"])
        exit_condition = {
            "indicator": "j", "operator": ">=", "value": 89,
            "timeframe": "5m", "join": "or", "role": "exit",
        }
        contract["event_contract"]["required_conditions"].append(exit_condition)
        contract["event_contract"]["entry_conditions"] = list(
            contract["event_contract"]["required_conditions"][:2])
        contract["event_contract"]["exit_conditions"] = [exit_condition]
        out = codex_implement_from_spec(_pack(contract))
        self.assertTrue(out["ok"], out.get("fidelity_errors"))
        self.assertEqual(len(out["dsl"]["entry"]["any"]), 2)
        exit_blob = str(out["dsl"]["exit"])
        self.assertIn("x_contract_1", exit_blob)
        self.assertIn("'feature': 'j'", exit_blob)

    def test_required_feature_must_be_consumed(self):
        contract = _contract()
        contract["feature_contract"] = copy.deepcopy(contract["feature_contract"])
        contract["feature_contract"]["required_features"].append("cci")
        contract["feature_contract"]["allowed_features"].append("cci")
        out = codex_implement_from_spec(_pack(contract))
        self.assertFalse(out["ok"])
        self.assertIn("required_feature_not_consumed:cci", out["fidelity_errors"])

    def test_prebuilt_dsl_cannot_change_or_to_and(self):
        good = codex_implement_from_spec(_pack())
        self.assertTrue(good["ok"], good.get("fidelity_errors"))
        prebuilt = copy.deepcopy(good["dsl"])
        prebuilt["entry"] = {"all": prebuilt["entry"].pop("any")}
        pack = _pack()
        pack["dsl"] = prebuilt
        out = codex_implement_from_spec(pack)
        self.assertFalse(out["ok"])
        self.assertIn("prebuilt_exact_event_semantics_drift", out["fidelity_errors"])

    def test_holding_period_unit_and_range_parsing(self):
        self.assertEqual(_compiler_parse_hold("3_to_18_bars_5m", "5m")[0], 18)
        self.assertEqual(_compiler_parse_hold("30_to_90_minutes", "5m")[0], 18)
        self.assertEqual(_compiler_parse_hold("1-2 hours", "15m")[0], 8)
        self.assertIsNone(_compiler_parse_hold("several bars", "5m")[0])

    def test_structural_presence_never_upgrades_failed_fidelity(self):
        base = {
            "pass": False, "dsl_has_entry": True, "dsl_has_exit": True,
            "stop_logic_in_spec": True, "notes": ["lexical_or_semantic_failure"],
        }
        book = {
            "ok": True, "dsl": {"entry": {"all": []}, "exit": {"any": []}},
            "fidelity_errors": [],
            "fidelity_evidence": {"structured_contract_verified": True},
        }

        def clean_audit(*args, **kwargs):
            return {"reject": False, "reject_reasons": [], "condition_audit": []}

        out = _compiler_finalize_fidelity(base, book, {}, audit_fn=clean_audit)
        self.assertFalse(out["pass"])
        self.assertNotIn("pass_via_structural_fidelity_proxies", out.get("notes") or [])


if __name__ == "__main__":
    unittest.main()
