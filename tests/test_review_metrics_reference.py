# -*- coding: utf-8 -*-
"""P0 unit tests for independent review metrics reference calculator."""
from __future__ import print_function

import unittest
from datetime import datetime, timedelta

from dual_engine_workflow_v2 import review_metrics_reference as ref


class TestAverageProfitableTrade(unittest.TestCase):
    def test_excludes_losing_trades(self):
        # +12%, +14%, -10%, -10% → mean of winners only = 13%
        out = ref.average_profitable_trade_return([0.12, 0.14, -0.10, -0.10])
        self.assertAlmostEqual(out["average_profitable_trade_return"], 0.13, places=12)
        self.assertEqual(out["n_winning"], 2)
        self.assertEqual(out["n_trades"], 4)
        # Must NOT be all-trade mean 1.5%
        self.assertNotAlmostEqual(out["average_profitable_trade_return"], 0.015, places=6)

    def test_zero_is_not_profitable(self):
        out = ref.average_profitable_trade_return([0.0, 0.12])
        self.assertEqual(out["n_winning"], 1)
        self.assertAlmostEqual(out["average_profitable_trade_return"], 0.12, places=12)


class TestLeverageOnce(unittest.TestCase):
    def test_leverage_multiplied_once_after_costs(self):
        trade = {
            "side": "long",
            "entry_price": 100.0,
            "exit_price": 100.6,  # +0.6% gross price
            "quantity": 1.0,
            "entry_fee": 0.0,
            "exit_fee": 0.0,
            "funding": 0.0,
            "slippage_cost": 0.0,
        }
        row = ref.trade_returns_from_fill(trade, leverage=20)
        self.assertAlmostEqual(row["gross_price_return"], 0.006, places=12)
        self.assertAlmostEqual(row["net_leveraged_return"], 0.12, places=12)  # 12%
        # Must not leave as 0.6% or explode to 240%
        self.assertNotAlmostEqual(row["net_leveraged_return"], 0.006, places=6)
        self.assertNotAlmostEqual(row["net_leveraged_return"], 2.40, places=6)


class TestFeesBeforeLeverage(unittest.TestCase):
    def test_fee_deducted_on_price_layer(self):
        # gross +1.0%, cost 0.1% of notional → net price 0.9% → ×20 = 18%
        trade = {
            "side": "long",
            "entry_price": 100.0,
            "exit_price": 101.0,
            "quantity": 1.0,
            "entry_fee": 0.05,
            "exit_fee": 0.05,
            "funding": 0.0,
            "slippage_cost": 0.0,
        }
        row = ref.trade_returns_from_fill(trade, leverage=20)
        self.assertAlmostEqual(row["gross_price_return"], 0.01, places=12)
        self.assertAlmostEqual(row["total_cost_return"], 0.001, places=12)
        self.assertAlmostEqual(row["net_price_return"], 0.009, places=12)
        self.assertAlmostEqual(row["net_leveraged_return"], 0.18, places=12)


class TestWeeklyFrequency(unittest.TestCase):
    def test_140_days_20_entries(self):
        out = ref.weekly_entry_frequency(20, 140)
        self.assertAlmostEqual(out["weekly_entry_frequency"], 1.0, places=12)

    def test_rejects_invalid_days(self):
        out = ref.weekly_entry_frequency(20, 0)
        self.assertIsNone(out["weekly_entry_frequency"])
        self.assertEqual(out["error"], "invalid_observation_days")


class TestStopDistance(unittest.TestCase):
    def test_long_short_stops(self):
        self.assertAlmostEqual(ref.protective_stop_price(100.0, "long"), 99.5, places=12)
        self.assertAlmostEqual(ref.protective_stop_price(100.0, "short"), 100.5, places=12)


class TestTemporalWeight(unittest.TestCase):
    def test_recent_2y_dominates_old_average(self):
        as_of = datetime(2026, 8, 3)
        trades = []
        # Recent winners ~12% levered
        for i in range(10):
            trades.append({
                "entry_time": (as_of - timedelta(days=30 + i)).strftime("%Y-%m-%d %H:%M:%S"),
                "exit_time": (as_of - timedelta(days=29 + i)).strftime("%Y-%m-%d %H:%M:%S"),
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 100.6,
                "quantity": 1.0,
                "entry_fee": 0.0,
                "exit_fee": 0.0,
                "funding": 0.0,
                "slippage_cost": 0.0,
            })
        # Old losers / tiny wins that would dilute naive average
        for i in range(40):
            trades.append({
                "entry_time": (as_of - timedelta(days=1000 + i)).strftime("%Y-%m-%d %H:%M:%S"),
                "exit_time": (as_of - timedelta(days=999 + i)).strftime("%Y-%m-%d %H:%M:%S"),
                "side": "long",
                "entry_price": 100.0,
                "exit_price": 100.1,
                "quantity": 1.0,
                "entry_fee": 0.0,
                "exit_fee": 0.0,
                "funding": 0.0,
                "slippage_cost": 0.0,
            })
        metrics = ref.compute_trade_ledger_metrics(
            trades,
            observation_start=as_of - timedelta(days=1200),
            observation_end=as_of,
            as_of=as_of,
        )
        self.assertTrue(metrics["recent_2y_requirement_passed"])
        self.assertGreaterEqual(
            metrics["recent_2y_metrics"]["average_profitable_trade_return"], 0.1111
        )
        # Older tiny 2% levered must not be able to flip recent gate by itself
        self.assertEqual(metrics["recent_dominated_decision"], "pass")


class TestParityHelper(unittest.TestCase):
    def test_parity_pass_and_fail(self):
        ref_m = {
            "win_rate": 0.5,
            "average_profitable_trade_return": 0.12,
            "weekly_entry_frequency": 1.0,
            "profit_first_rate": 0.6,
            "median_mae": 0.002,
            "trade_count": 10,
        }
        prod_ok = {
            "win_rate": 0.5,
            "average_profitable_trade_return": 0.12,
            "weekly_opens": 1.0,
            "profit_first_rate": 0.6,
            "median_mae": 0.002,
            "n": 10,
        }
        ok = ref.parity_compare(prod_ok, ref_m)
        self.assertTrue(ok["metric_parity_passed"])
        prod_bad = dict(prod_ok)
        prod_bad["average_profitable_trade_return"] = 0.01
        bad = ref.parity_compare(prod_bad, ref_m)
        self.assertFalse(bad["metric_parity_passed"])
        self.assertEqual(bad["error"], "REVIEW_METRIC_PARITY_FAILURE")


if __name__ == "__main__":
    unittest.main()
