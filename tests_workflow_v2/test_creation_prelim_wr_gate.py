# -*- coding: utf-8 -*-
"""硬门：初评胜率未严格大于 50% 时不得对人展示。"""
from __future__ import print_function

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dual_engine_workflow_v2 import creation_prelim_eval as prelim


class TestCreationPrelimWrGate(unittest.TestCase):
    def test_wr_below_50_not_presentable(self):
        v = prelim.prelim_eval(
            {"win_rate": 0.444, "n_trades": 18, "total_return": 0.0002},
            first_ts=1784985900000,
            last_ts=1785405600000,
            n_bars=1400,
            timeframe="5m",
        )
        self.assertFalse(v["present_to_human"])
        self.assertIn("win_rate_not_above_50pct", v["reject_reasons"])
        self.assertIsNotNone(v["human_banner_zh"])
        self.assertAlmostEqual(v["window"]["span_days"], 4.8576, places=3)
        self.assertIn("不是单日收益", v["metrics_note_zh"])
        self.assertIn("胜率", v["human_banner_zh"])

    def test_wr_at_50_not_presentable_must_be_strictly_above(self):
        v = prelim.prelim_eval(
            {"win_rate": 0.50, "n_trades": 20, "total_return": 0.01},
            first_ts=1784985900000,
            last_ts=1785405600000,
            n_bars=1400,
            timeframe="5m",
        )
        self.assertFalse(v["present_to_human"])
        self.assertIn("win_rate_not_above_50pct", v["reject_reasons"])

    def test_wr_missing_not_presentable(self):
        v = prelim.prelim_eval({"n_trades": 20})
        self.assertFalse(v["present_to_human"])
        self.assertIn("win_rate_missing", v["reject_reasons"])

    def test_classic_variants_cycle(self):
        tried = []
        ids = []
        while True:
            nxt = prelim.next_classic_variant(tried)
            if nxt is None:
                break
            ids.append(nxt["id"])
            tried.append(nxt["id"])
        self.assertEqual(len(ids), len(prelim.CLASSIC_VARIANTS))
        self.assertIsNone(prelim.next_classic_variant(tried))

    def test_apply_variant_rewrites_design(self):
        v = prelim.CLASSIC_VARIANTS[0]
        d = prelim.apply_variant_to_design({"mechanism_family": "old"}, v)
        self.assertEqual(d["classic_variant"]["id"], v["id"])
        self.assertEqual(d["mechanism_family"], v["family"])
        self.assertIn("胜率", d["hypotheses"][1]["statement_zh"])


if __name__ == "__main__":
    unittest.main()
