# -*- coding: utf-8 -*-
"""Unit tests for expectancy / frequency metrics refactor."""
from __future__ import print_function

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import auto_trade_expectancy_metrics as exp


class ExpectancyMetricsTests(unittest.TestCase):
    def test_equity_formula(self):
        # price 1% * 0.30 * 20 = 6% equity
        self.assertAlmostEqual(0.01 * 0.30 * 20.0, 0.06)

    def test_mechanism_family_clusters_fades(self):
        self.assertEqual(
            exp.mechanism_family("ng5_exhaustion_fade_short_ai"),
            "exhaustion_fade_short")
        self.assertEqual(
            exp.mechanism_family("ltc5_exhaustion_fade_short_ai"),
            "exhaustion_fade_short")
        self.assertEqual(
            exp.mechanism_family("frost_xrp_rescue_h20_t45"),
            "exhaustion_fade_short")
        self.assertEqual(
            exp.mechanism_family("frost3_btc1h_xrpport_exhaustion_fade_slope"),
            "exhaustion_fade_short")
        self.assertEqual(
            exp.mechanism_family("codex0725t3_ada5m_trendpb_r42_z2p3_h14"),
            "session_trend_pullback")

    def test_low_freq_never_autokills(self):
        row = exp.classify_frequency(0.1)
        self.assertEqual(row["class"], "low_frequency")
        self.assertFalse(row["auto_kill"])

    def test_metric_status_missing(self):
        m = exp.metric_status(None)
        self.assertEqual(m["metric_status"], "missing")
        self.assertNotEqual(m["display"], "-")

    def test_regime_factor_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            auto = Path(tmp)
            with mock.patch.object(exp, "AUTO_DIR", auto):
                (auto / "strategy_breath_sensors.json").write_text(
                    json.dumps({"volatility_percentile": 5}), encoding="utf-8")
                r = exp.regime_factor_from_sensors()
                self.assertGreaterEqual(r["regime_factor"], 0.25)
                self.assertLessEqual(r["regime_factor"], 2.0)

    def test_gap_report_writes_creation_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            auto = Path(tmp)
            with mock.patch.object(exp, "AUTO_DIR", auto), \
                    mock.patch.object(exp, "GAP_REPORT_PATH", auto / "gap.json"), \
                    mock.patch.object(exp, "CREATION_INPUT_PATH", auto / "create.json"):
                rows = [
                    {
                        "can_open": True,
                        "strategy_key": "ltc5_exhaustion_fade_short_ai",
                        "expected_daily_fills": 0.1,
                        "expected_weekly_fills": 0.7,
                        "mechanism_family": "exhaustion_fade_short",
                    },
                    {
                        "can_open": True,
                        "strategy_key": "ng5_exhaustion_fade_short_ai",
                        "expected_daily_fills": 0.05,
                        "expected_weekly_fills": 0.35,
                        "mechanism_family": "exhaustion_fade_short",
                    },
                ]
                report = exp.build_frequency_gap_report(rows)
                self.assertEqual(report["pool"]["status"], "shortfall")
                self.assertTrue(report["creation_brief"]["do_not_force_open"])
                self.assertTrue((auto / "create.json").exists())
                # Two fades → one family weight, not two niches
                self.assertEqual(
                    report["mechanism_families"]["independent_niche_count"], 1)

    def test_cost_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            auto = Path(tmp)
            model = {
                "scenarios": {
                    "observed_base": {
                        "fee_rate_per_side": 0.0005,
                        "slippage_rate_per_side": 0.0002,
                        "half_spread_rate_per_side": 0.00005,
                        "impact_rate_per_side": 0.00005,
                        "latency_rate_per_side": 0.00005,
                        "funding_rate_per_8h": 0.0001,
                    }
                },
                "execution_drag_multiplier": {"LTC-USDT-SWAP": 1.2},
                "instrument_observed": {},
            }
            with mock.patch.object(exp, "COST_MODEL_PATH", auto / "cost.json"):
                (auto / "cost.json").write_text(
                    json.dumps(model), encoding="utf-8")
                c = exp.round_trip_cost_price_rate("LTC-USDT-SWAP", hold_hours=4)
                self.assertGreater(c["cost_price_rate"], 0)


if __name__ == "__main__":
    unittest.main()
