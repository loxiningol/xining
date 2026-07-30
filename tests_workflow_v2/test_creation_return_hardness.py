# -*- coding: utf-8 -*-
"""Tests for creation return-hardness + degeneration fuses."""
from __future__ import print_function

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dual_engine_workflow_v2 import creation_return_hardness as rh
from dual_engine_workflow_v2 import creation_meta_think as meta


class TestReturnHardness(unittest.TestCase):
    def test_shit_package_fails_hardness(self):
        # +0.02% over 4.86 days → weekly proxy ~0.029% << 8%
        v = rh.evaluate_return_hardness(
            total_return=0.0002,
            max_drawdown=-0.0145,
            span_days=4.86,
            trade_returns=[0.0001] * 18,
            n_bars=1400,
            hold_bars=3,
        )
        self.assertFalse(v["passed"])
        self.assertIn("weekly_return_below_8pct", v["reject_reasons"])
        self.assertIn("window_total_return_below_1pct", v["reject_reasons"])

    def test_strong_week_passes(self):
        # ~8% in 7 days with decent MDD ratio and non-tiny trades
        rets = [0.004] * 25  # compound >> 8%, avg 40bp
        v = rh.evaluate_return_hardness(
            trade_returns=rets,
            span_days=7.0,
            n_bars=200,
            hold_bars=6,
        )
        self.assertTrue(v["passed"], v.get("reject_reasons"))
        self.assertGreaterEqual(v["metrics"]["weekly_return_proxy"], 0.08)

    def test_factor_ls_weekly_filter(self):
        # near-zero mean trades fail 3% weekly levered floor
        ok, row = rh.filter_factor_by_ls_weekly(
            {"factor": "x", "returns": [0.00005] * 40},
            span_days=5.0,
        )
        self.assertFalse(ok)
        self.assertTrue(row.get("ls_weekly_reject"))

    def test_degeneration_low_exposure(self):
        d = rh.detect_degeneration(
            trade_returns=[0.01, 0.01],
            n_bars=2000,
            hold_bars=3,
            total_return=0.02,
        )
        self.assertTrue(d["degenerated"])
        self.assertIn("exposure_below_floor", d["reasons"])

    def test_meta_includes_return_anchors(self):
        pack = meta.run_meta_think(
            brief="创造ADA策略，波动35%，回撤18%",
            symbol="ADA-USDT-SWAP",
            timeframe="5m",
            skip_llm=True,
        )
        design = pack["design_doc"]
        self.assertIn("expected_annual_return_range", design)
        self.assertEqual(design["minimum_acceptable_annual_return"], 0.06)
        self.assertGreaterEqual(
            design["constraints"]["minimum_weekly_return"], 0.08
        )
        pers = design["divergence"]["perspectives"]
        self.assertTrue(all("max_annual_net_estimate" in p for p in pers))
        # inventory/mean-reversion should not beat higher-capacity trend if both present
        chosen_cap = None
        for p in pers:
            if p["id"] == design["divergence"]["selected_id"]:
                chosen_cap = p["max_annual_net_estimate"]
        self.assertIsNotNone(chosen_cap)
        self.assertGreaterEqual(chosen_cap, 0.06)


if __name__ == "__main__":
    unittest.main()
