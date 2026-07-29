# -*- coding: utf-8 -*-
"""Phase-2: structured exits + multi-objective fitness engine tests.

Covers:
  - Hard ban of fixed tiny take-profit (no silent convert)
  - atr_trailing / swing_extreme validate + evaluate
  - Fitness rejects pseudo high-WR low-payoff
  - Fitness accepts payoff>=2.5 synthetic fixtures
  - MAE demotion + remove-max-win lottery reject
  - Protective 0.9% SL constant untouched in DSL backtest signature
"""
from __future__ import print_function

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import auto_trade_strategy_dsl as dsl
from dual_engine_workflow_v2 import fitness_engine as fe
from dual_engine_workflow_v2 import gates


def _base_strategy(**exit_override):
    strat = {
        "schema": "qiyu_strategy_dsl_v1",
        "key": "phase2_test_strat",
        "name": "phase2 test",
        "direction": "long",
        "timeframe": "5m",
        "supported_instruments": ["SOL-USDT-SWAP"],
        "max_hold_bars": 24,
        "entry": {"all": [
            {"id": "e1", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "ema21"}},
            {"id": "e2", "left": {"feature": "vol_z20"}, "op": "gt",
             "right": {"value": 0.5}},
        ]},
        "exit": {"any": [
            {"id": "x_atr", "exit_op": "atr_trailing", "n_atr": 3.0,
             "atr_period": 14, "role": "take_profit"},
            {"id": "x_swing", "exit_op": "swing_extreme", "lookback": 20,
             "role": "invalidation"},
        ]},
    }
    if exit_override:
        strat["exit"] = exit_override.get("exit", strat["exit"])
    return strat


class FixedTinyTpBanTests(unittest.TestCase):
    def test_refuse_1pct_fixed_tp(self):
        with self.assertRaises(dsl.DSLValidationError) as ctx:
            dsl.refuse_fixed_tiny_tp(0.01, context="unit")
        self.assertIn("REFUSED", str(ctx.exception))
        self.assertIn("silently convert", str(ctx.exception))

    def test_refuse_0_6pct_and_0_9pct_tp(self):
        for pct in (0.006, 0.009, 0.012, 0.013, 0.015, 1.0):
            with self.assertRaises(dsl.DSLValidationError):
                dsl.refuse_fixed_tiny_tp(pct, context="unit")

    def test_allow_2pct_fixed_tp_value(self):
        self.assertAlmostEqual(dsl.refuse_fixed_tiny_tp(0.02), 0.02)

    def test_validate_rejects_fixed_pct_tp_leaf(self):
        strat = _base_strategy()
        strat["exit"] = {"any": [
            {"id": "bad_tp", "exit_op": "fixed_pct_tp", "pct": 0.01,
             "role": "take_profit"},
        ]}
        with self.assertRaises(dsl.DSLValidationError) as ctx:
            dsl.validate_strategy(strat)
        self.assertIn("REFUSED", str(ctx.exception))

    def test_validate_accepts_atr_and_swing(self):
        validated = dsl.validate_strategy(_base_strategy())
        self.assertEqual(validated["key"], "phase2_test_strat")
        self.assertEqual(validated["exit"]["any"][0]["exit_op"], "atr_trailing")

    def test_atr_n_out_of_range_rejected(self):
        strat = _base_strategy()
        strat["exit"] = {"any": [
            {"id": "x", "exit_op": "atr_trailing", "n_atr": 1.0, "role": "take_profit"},
        ]}
        with self.assertRaises(dsl.DSLValidationError):
            dsl.validate_strategy(strat)

    def test_protective_sl_default_unchanged(self):
        # Signature default remains 0.009 — Phase-2 does not abolish protective SL
        import inspect
        sig = inspect.signature(dsl.backtest_dsl)
        self.assertEqual(sig.parameters["stop_loss_pct"].default, 0.009)


class FitnessEngineTests(unittest.TestCase):
    def _mk_trades(self, pnls, mae=None):
        trades = []
        for i, p in enumerate(pnls):
            row = {
                "pnl_ratio": float(p),
                "entry_time": "2026-01-%02d 00:00:00" % (1 + (i % 28)),
                "exit_time": "2026-01-%02d 04:00:00" % (1 + (i % 28)),
                "leverage": 20,
            }
            if mae is not None:
                # list or scalar
                if isinstance(mae, (list, tuple)):
                    row["mae_price_pct"] = float(mae[i])
                else:
                    row["mae_price_pct"] = float(mae)
            trades.append(row)
        return trades

    def test_rejects_high_wr_low_payoff(self):
        # 90% WR but tiny wins vs large losses → payoff << 2.5, expectancy factor < 1
        pnls = [0.01] * 9 + [-0.10]
        # avg_win=0.01, avg_loss=0.10, payoff=0.1, WR=0.9, WR*payoff=0.09 < 1
        result = fe.evaluate_multi_objective(self._mk_trades(pnls), enforce=True)
        self.assertFalse(result["pass"])
        self.assertIn("payoff_ge_2_5", result["failed_checks"])
        self.assertIn("pseudo_high_WR_low_payoff", result["verdict_tags"])

    def test_passing_strategy_meets_payoff_2_5(self):
        # Uniform wins, many losses → payoff>=2.5, worst5 share <40%, stable without max win
        # 16 wins * 0.08, 16 losses * -0.03 → WR=0.5, payoff≈2.67, factor≈1.33
        pnls = [0.08, -0.03] * 16
        trades = self._mk_trades(pnls)
        result = fe.evaluate_multi_objective(trades, enforce=True)
        metrics = result["metrics"]
        self.assertGreaterEqual(metrics["payoff_ratio"], 2.5)
        self.assertGreaterEqual(metrics["expectancy_factor"], 1.0)
        self.assertTrue(result["pass"], msg=result.get("failed_checks"))

    def test_mae_dead_hold_demotion(self):
        # Healthy payoff but one trade MAE >> 2*avg_win (price space)
        pnls = [0.08, -0.03] * 8
        # avg_win=0.08 leveraged → avg_win_price=0.004 at 20x
        # mae 0.05 >> 2*0.004
        mae = [0.001] * 15 + [0.05]
        result = fe.evaluate_multi_objective(
            self._mk_trades(pnls, mae=mae), enforce=True
        )
        self.assertFalse(result["pass"])
        self.assertIn("mae_dead_hold_clear", result["failed_checks"])
        self.assertIn("dead_hold_to_BE", result["verdict_tags"])

    def test_remove_max_win_lottery_reject(self):
        # One huge win dominates; remove it → Calmar/Sharpe collapse
        pnls = [5.0] + [-0.05] * 10 + [0.02] * 2
        result = fe.evaluate_multi_objective(self._mk_trades(pnls), enforce=True)
        self.assertFalse(result["pass"])
        self.assertTrue(
            "remove_max_win_stable" in result["failed_checks"]
            or "lottery_overfitting" in result["verdict_tags"]
            or "calmar_ge_1_5" in result["failed_checks"]
            or "payoff_ge_2_5" in result["failed_checks"]
        )

    def test_both_expectancy_formulas_reported(self):
        pnls = [0.1, 0.1, -0.04, -0.04, 0.1, -0.04, 0.1, 0.1]
        m = fe.compute_fitness_metrics(self._mk_trades(pnls))
        self.assertIn("expectancy_factor", m)
        self.assertIn("classic_expectancy", m)
        self.assertIn("win_rate * ", m["formulas"]["expectancy_factor"])


class Gate2FitnessWiringTests(unittest.TestCase):
    def test_gate2_rejects_pseudo_edge(self):
        trades = [{"pnl_ratio": 0.01}] * 9 + [{"pnl_ratio": -0.12}]
        base = {"trades": 10, "mean_net": 0.001, "win_rate_pct": 90.0}
        g = gates.evaluate_gate2(base, trades)
        self.assertFalse(g["pass"])
        self.assertIn("fitness", g["evidence"])
        self.assertFalse(g["evidence"]["fitness"]["pass"])

    def test_gate2_passes_strong_payoff(self):
        pnls = [0.08, -0.03] * 16
        trades = [{
            "pnl_ratio": p,
            "entry_time": "2026-02-%02d 00:00:00" % (1 + (i % 27)),
            "exit_time": "2026-02-%02d 06:00:00" % (1 + (i % 27)),
            "leverage": 20,
        } for i, p in enumerate(pnls)]
        mean_net = sum(pnls) / float(len(pnls))
        g = gates.evaluate_gate2(
            {"trades": len(trades), "mean_net": mean_net, "win_rate_pct": 50.0},
            trades,
        )
        self.assertTrue(g["pass"], msg=g["evidence"].get("fitness"))
        self.assertGreaterEqual(g["evidence"]["fitness"]["payoff_ratio"], 2.5)


class CompilerTinyTpBanTests(unittest.TestCase):
    def test_compiler_refuses_fixed_tiny_tp_blob(self):
        from windtalker_phase4_deploy import candidate_ir_compiler as cic
        with self.assertRaises(cic.CompilerError) as ctx:
            cic._refuse_fixed_tiny_tp(0.01, context="test")
        self.assertIn("REFUSED", str(ctx.exception))
        self.assertIn("NOT silently convert", str(ctx.exception))

    def test_compiler_scan_exit_tp_dict(self):
        from windtalker_phase4_deploy import candidate_ir_compiler as cic
        with self.assertRaises(cic.CompilerError):
            cic._scan_exit_node_for_banned_tp({
                "tp": {"type": "fixed_pct", "pct": 0.009},
                "production_sl_0_9pct": 0.009,
            }, path="exit")


class ExitOpEvalSmokeTests(unittest.TestCase):
    def test_evaluate_exit_op_atr_requires_position(self):
        # Minimal frame duck-type (no pandas dependency in unit tests)
        class _Col(list):
            @property
            def iloc(self):
                return self

        class _Frame(dict):
            pass

        n = 80
        frame = _Frame({
            "open": _Col([100.0 + i * 0.1 for i in range(n)]),
            "high": _Col([100.5 + i * 0.1 for i in range(n)]),
            "low": _Col([99.5 + i * 0.1 for i in range(n)]),
            "close": _Col([100.0 + i * 0.1 for i in range(n)]),
        })
        node = {"id": "x", "exit_op": "atr_trailing", "n_atr": 3.0, "role": "take_profit"}
        pos = {"price": 100.0, "peak_high": 105.0, "peak_low": 99.0}
        passed, details, px = dsl.evaluate_exit_op(
            frame, 50, node, pos, "long", explain=True
        )
        self.assertIsInstance(passed, bool)
        self.assertTrue(details)
        self.assertTrue(math.isfinite(px))


if __name__ == "__main__":
    unittest.main()
