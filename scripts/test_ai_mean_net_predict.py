# -*- coding: utf-8 -*-
"""Unit tests for 3AI theoretical mean-net (单笔盈利率) aggregation."""
from __future__ import print_function

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import auto_trade_ai_consensus as ai  # noqa: E402
import auto_trade_strategy_titles as titles  # noqa: E402


class MeanNetNormalizeTests(unittest.TestCase):
    def test_percent_points_passthrough(self):
        self.assertAlmostEqual(
            ai._normalize_mean_net_pct(3.5, empirical_mean_net=0.038), 3.5)

    def test_decimal_ratio_converted(self):
        self.assertAlmostEqual(
            ai._normalize_mean_net_pct(0.035, empirical_mean_net=0.038), 3.5)


class CardTests(unittest.TestCase):
    def test_card_includes_mean_net(self):
        card = titles.format_live_strategy_card(
            "codex0725t3_ada5m_trendpb_r42_z2p3_h14",
            grade="B",
            max_position_ratio=0.3,
            ai_theoretical_wr_avg=74.667,
            ai_theoretical_mean_net_avg=3.2,
        )
        self.assertIn("三AI理论胜率 74.7%", card)
        self.assertIn("三AI理论盈利单盈利率 +3.200%", card)


if __name__ == "__main__":
    unittest.main()
