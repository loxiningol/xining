# -*- coding: utf-8 -*-
"""Regression: creation quality refactor (factor surface / soft-pass / OOS / WR35)."""
from __future__ import print_function

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dual_engine_workflow_v2 import creation_blueprint
from dual_engine_workflow_v2 import creation_quality_doctrine as doctrine
from dual_engine_workflow_v2 import cheap_probe_stream
from dual_engine_workflow_v2 import recipe_policy
from dual_engine_workflow_v2 import research_contract
from dual_engine_workflow_v2 import research_discovery
from dual_engine_workflow_v2 import structured_candidate_search as scs


SYMBOL = "BTC-USDT-SWAP"
TIMEFRAME = "15m"


def _generic_contract():
    return research_contract.compile_contract(
        "唐奇安通道突破叠加RSI衰竭回收",
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
        data_version="creation_quality_fixture_v1",
        code_version="creation_quality_fixture_v1",
    )


def _quantile_term(factor, side="high", q=0.8):
    return {
        "factor": factor,
        "side": side,
        "q": q,
        "window": 240,
        "min_history": 80,
        "threshold_source": "prior_only_rolling_quantile",
        "join": "and",
    }


def _formal_row(terms):
    return {
        "event_kind": "mechanism_intersection",
        "event_logic": "all",
        "terms": terms,
        "execution_mapping": recipe_policy.FORMAL_EXECUTION_MAPPING,
        "horizon_bars": 6,
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


class TestCreationPipelineQualityRefactor(unittest.TestCase):
    def test_account_wr35_rejected_at_assembly_quality(self):
        # 7 wins / 13 losses ≈ 35% — the shipped garbage pattern.
        rets = [0.02] * 7 + [-0.01] * 13
        quality = creation_blueprint._admission_trade_quality(rets)
        self.assertFalse(quality.get("ok"))
        self.assertTrue(
            any("胜率" in r for r in (quality.get("reasons") or [])),
            quality.get("reasons"),
        )

    def test_expanded_factor_capability_rsi_donchian(self):
        contract = _generic_contract()
        self.assertTrue(contract.get("valid"), contract)
        row = _formal_row([
            _quantile_term("rsi_14", "low", 0.8),
            _quantile_term("donchian20_long_break", "high", 0.8),
        ])
        cap = recipe_policy.capability_from_row(row, contract)
        self.assertTrue(cap.get("ok"), cap.get("reasons"))
        self.assertEqual(
            recipe_policy.GENERIC_FACTOR_TO_DSL["rsi_14"], "rsi14",
        )
        self.assertEqual(
            recipe_policy.GENERIC_FACTOR_TO_DSL["donchian20_long_break"],
            "donchian20_break_up",
        )

    def test_soft_pass_forbidden_for_antifalsify_leakage(self):
        self.assertIn("antifalsify", research_discovery.SOFT_PASS_FORBIDDEN_GATES)
        self.assertIn("leakage", research_discovery.SOFT_PASS_FORBIDDEN_GATES)
        self.assertIn("causal", research_discovery.SOFT_PASS_FORBIDDEN_GATES)
        self.assertIn("efr", research_discovery.SOFT_PASS_ALLOWED_GATES)
        with open(research_discovery.__file__, "r", encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn('af["soft_pass"] = "handoff_floor_to_review"', src)
        self.assertNotIn('leak["soft_pass"] = "handoff_floor_to_review"', src)
        self.assertNotIn('causal["soft_pass"] = "handoff_floor_to_review"', src)
        self.assertIn('feas["soft_pass"] = "handoff_floor_to_review"', src)

    def test_no_oos_without_confirmation_returns(self):
        gate = research_discovery.evaluate_oos_confirmation_gate(
            [],
            {"mean_net": 0.01, "n": 20, "win_rate": 0.6},
        )
        self.assertFalse(gate["oos_confirmation_passed"])
        self.assertEqual(gate["hard_block"], "oos_confirmation_missing")

    def test_oos_wr35_confirmation_fails(self):
        conf = [0.02] * 7 + [-0.01] * 13
        gate = research_discovery.evaluate_oos_confirmation_gate(conf)
        self.assertFalse(gate["oos_confirmation_passed"])
        self.assertEqual(gate["hard_block"], "oos_wr_or_anti_lottery_fail")

    def test_brief_locked_families_no_default_union(self):
        families = scs._family_set({}, "BTC15m 唐奇安通道突破 + RSI衰竭回收")
        self.assertIn("donchian_trend_break", families)
        self.assertIn("exhaustion", families)
        self.assertNotIn("volume_anomaly_breakout", families)
        empty = scs._family_set({}, "无关噪声文本")
        self.assertEqual(empty, set())

    def test_lean_extra_cells_brief_locked(self):
        cells = cheap_probe_stream.build_mechanism_cells(
            brief="唐奇安通道突破", max_cells=80,
        )
        families = set(str(c.get("family") or "") for c in cells)
        self.assertIn("donchian_trend_break", families)
        self.assertNotIn("exhaustion", families)
        self.assertNotIn("vol_squeeze", families)

    def test_gate2_floors_applied_helper(self):
        # Positive mean but weak payoff/calmar should fail floors.
        rets = [0.001] * 9 + [-0.0005] * 6
        pack = doctrine.gate2_account_returns_ok(rets)
        self.assertIn("floors", pack)
        # Strong edge book should pass floors.
        strong = [0.05] * 12 + [-0.01] * 4
        ok = doctrine.gate2_account_returns_ok(strong)
        self.assertTrue(ok.get("ok"), ok)


if __name__ == "__main__":
    unittest.main()
