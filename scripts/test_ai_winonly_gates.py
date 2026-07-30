# -*- coding: utf-8 -*-
"""Unit tests for win-only 3AI gates (WR≥65, win-mean≥5)."""
from __future__ import print_function

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import auto_trade_ai_consensus as ai  # noqa: E402
import auto_trade_strategy_titles as titles  # noqa: E402


class GateConstantTests(unittest.TestCase):
    def test_gates(self):
        self.assertEqual(ai.MIN_THEORETICAL_WR, 65.0)
        self.assertEqual(ai.MIN_THEORETICAL_WIN_MEAN_NET_PCT, 5.0)


class ProviderPassTests(unittest.TestCase):
    def test_pass_requires_wr65_and_win_mean5(self):
        ok = {
            "ok": True, "decision": "APPROVE",
            "theoretical_win_rate_pct": 66.0,
            "theoretical_mean_net_pct": 5.1,
            "stop_cluster_risk": "low", "stop_cluster_prob": 0.1,
        }
        self.assertTrue(ai._provider_theoretical_pass(ok))

    def test_reject_low_wr(self):
        row = {
            "ok": True, "decision": "APPROVE",
            "theoretical_win_rate_pct": 64.0,
            "theoretical_mean_net_pct": 6.0,
            "stop_cluster_risk": "low", "stop_cluster_prob": 0.1,
        }
        self.assertFalse(ai._provider_theoretical_pass(row))

    def test_reject_low_win_mean(self):
        row = {
            "ok": True, "decision": "APPROVE",
            "theoretical_win_rate_pct": 70.0,
            "theoretical_mean_net_pct": 4.9,
            "stop_cluster_risk": "low", "stop_cluster_prob": 0.1,
        }
        self.assertFalse(ai._provider_theoretical_pass(row))

    def test_reject_missing_win_mean(self):
        row = {
            "ok": True, "decision": "APPROVE",
            "theoretical_win_rate_pct": 70.0,
            "theoretical_mean_net_pct": None,
            "stop_cluster_risk": "low", "stop_cluster_prob": 0.1,
        }
        self.assertFalse(ai._provider_theoretical_pass(row))


class EmpiricWinOnlyTests(unittest.TestCase):
    def test_prefers_win_only_pct(self):
        ev = {"safety_metrics": {
            "mean_net": 0.038,
            "mean_net_win_only_pct": 7.47,
        }}
        self.assertAlmostEqual(ai._empirical_win_mean_net_pct(ev), 7.47)

    def test_ignores_all_trade_mean_net(self):
        ev = {"safety_metrics": {"mean_net": 0.038}}
        self.assertIsNone(ai._empirical_win_mean_net_pct(ev))


class CardTests(unittest.TestCase):
    def test_card_label(self):
        card = titles.format_live_strategy_card(
            "codex0725t3_ada5m_trendpb_r42_z2p3_h14",
            grade="B",
            max_position_ratio=0.3,
            ai_theoretical_wr_avg=74.0,
            ai_theoretical_mean_net_avg=6.8,
        )
        self.assertIn("三AI理论胜率 74.0%", card)
        self.assertIn("三AI理论盈利单盈利率 +6.800%", card)


if __name__ == "__main__":
    unittest.main()
