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
        # Tiny total return over short span → fails annualized + window floor
        v = rh.evaluate_return_hardness(
            total_return=0.0002,
            max_drawdown=-0.0145,
            span_days=4.86,
            trade_returns=[0.0001] * 18,
            n_bars=1400,
            hold_bars=3,
        )
        self.assertFalse(v["passed"])
        self.assertIn("annualized_return_below_floor", v["reject_reasons"])
        self.assertIn("window_total_return_below_1pct", v["reject_reasons"])

    def test_strong_annualized_passes(self):
        # Strong edge over ~60 days with decent MDD ratio and non-tiny trades
        rets = [0.004] * 40
        v = rh.evaluate_return_hardness(
            trade_returns=rets,
            span_days=60.0,
            n_bars=400,
            hold_bars=6,
        )
        self.assertTrue(v["passed"], v.get("reject_reasons"))
        self.assertGreaterEqual(v["metrics"]["annualized_return_proxy"], 0.06)

    def test_weekly_floor_only_when_explicit(self):
        rets = [0.002] * 30
        v = rh.evaluate_return_hardness(
            trade_returns=rets,
            span_days=30.0,
            n_bars=300,
            hold_bars=5,
            constraints={"minimum_weekly_return": 0.08},
        )
        self.assertFalse(v["passed"])
        self.assertIn("weekly_return_below_floor", v["reject_reasons"])

    def test_factor_ls_weekly_filter(self):
        ok, row = rh.filter_factor_by_ls_weekly(
            {"factor": "x", "returns": [0.00005] * 40},
            span_days=5.0,
        )
        self.assertFalse(ok)
        self.assertTrue(row.get("ls_weekly_reject"))

    def test_degeneration_tiny_trade(self):
        d = rh.detect_degeneration(
            trade_returns=[0.00001, 0.00001],
            n_bars=2000,
            hold_bars=3,
            total_return=0.00002,
        )
        self.assertTrue(d["degenerated"])
        self.assertTrue(
            "avg_trade_below_1bp" in d["reasons"]
            or "window_total_return_below_1pct" in d["reasons"]
        )

    def test_defaults_revoke_weekly_8pct(self):
        c = rh.default_return_constraints()
        self.assertEqual(c["minimum_weekly_return"], 0.0)
        self.assertEqual(c["minimum_exposure"], 0.0)
        self.assertIn("已撤销", c["note_zh"])

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
        self.assertEqual(design["constraints"]["minimum_weekly_return"], 0.0)
        pers = design["divergence"]["perspectives"]
        self.assertTrue(all("max_annual_net_estimate" in p for p in pers))


if __name__ == "__main__":
    unittest.main()
