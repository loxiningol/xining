# -*- coding: utf-8 -*-
"""Unit tests for win-only 3AI gates (WR≥65, win-mean≥5, weekly discount≥0.5)."""
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
        self.assertEqual(ai.MIN_THEORETICAL_WEEKLY_OPENS, 0.5)
        self.assertEqual(ai.WEEKLY_OPENS_STAT_METHOD, "backtest_2y_fill_rate_proxy")
        self.assertEqual(ai.WEEKLY_OPENS_LOOKBACK_DAYS, 730)
        self.assertEqual(ai.WEEKLY_OPENS_MIN_SPAN_DAYS, 600)
        self.assertTrue(hasattr(ai, "WEEKLY_OPENS_MAX_REL_DISCOUNT"))
        self.assertTrue(hasattr(ai, "WEEKLY_OPENS_MAX_ABS_DISCOUNT"))


class ProviderPassTests(unittest.TestCase):
    def test_pass_requires_wr65_and_win_mean5_and_weekly(self):
        ok = {
            "ok": True, "decision": "APPROVE",
            "theoretical_win_rate_pct": 66.0,
            "theoretical_mean_net_pct": 5.1,
            "theoretical_weekly_opens": 0.8,
            "stop_cluster_risk": "low", "stop_cluster_prob": 0.1,
        }
        self.assertTrue(ai._provider_theoretical_pass(ok))

    def test_reject_low_wr(self):
        row = {
            "ok": True, "decision": "APPROVE",
            "theoretical_win_rate_pct": 64.0,
            "theoretical_mean_net_pct": 6.0,
            "theoretical_weekly_opens": 1.0,
            "stop_cluster_risk": "low", "stop_cluster_prob": 0.1,
        }
        self.assertFalse(ai._provider_theoretical_pass(row))

    def test_reject_low_win_mean(self):
        row = {
            "ok": True, "decision": "APPROVE",
            "theoretical_win_rate_pct": 70.0,
            "theoretical_mean_net_pct": 4.9,
            "theoretical_weekly_opens": 1.0,
            "stop_cluster_risk": "low", "stop_cluster_prob": 0.1,
        }
        self.assertFalse(ai._provider_theoretical_pass(row))

    def test_reject_missing_win_mean(self):
        row = {
            "ok": True, "decision": "APPROVE",
            "theoretical_win_rate_pct": 70.0,
            "theoretical_mean_net_pct": None,
            "theoretical_weekly_opens": 1.0,
            "stop_cluster_risk": "low", "stop_cluster_prob": 0.1,
        }
        self.assertFalse(ai._provider_theoretical_pass(row))

    def test_reject_low_weekly(self):
        row = {
            "ok": True, "decision": "APPROVE",
            "theoretical_win_rate_pct": 70.0,
            "theoretical_mean_net_pct": 6.0,
            "theoretical_weekly_opens": 0.4,
            "stop_cluster_risk": "low", "stop_cluster_prob": 0.1,
        }
        self.assertFalse(ai._provider_theoretical_pass(row))

    def test_reject_missing_weekly(self):
        row = {
            "ok": True, "decision": "APPROVE",
            "theoretical_win_rate_pct": 70.0,
            "theoretical_mean_net_pct": 6.0,
            "theoretical_weekly_opens": None,
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


class WeeklyFrequencyAndConsensusTests(unittest.TestCase):
    def test_r4_prefers_near_2y_backtest_density(self):
        pack = ai.resolve_statistical_weekly_opens(
            {"key": "codex0725t3_ada5m_trendpb_r42_z2p3_h14"},
            {"trades": 104, "span_days": 728.0, "weekly_opens_require_2y": True},
        )
        self.assertAlmostEqual(pack["expected_weekly_fills"], 1.0, places=4)
        self.assertEqual(pack["method"], "backtest_2y_fill_rate_proxy")
        self.assertTrue(pack["sample_2y_ok"])
        self.assertTrue(ai.weekly_opens_2y_sample_ok(pack))
        self.assertEqual(
            pack["calculation"],
            "hybrid:statistical_anchor;three_ai_limited_discount",
        )
        self.assertFalse(pack["ai_may_override"])
        self.assertTrue(pack["ai_may_discount"])
        self.assertEqual(pack["ai_role"], "limited_discount")

    def test_live_14d_rejected_when_2y_required(self):
        pack = ai.resolve_statistical_weekly_opens(
            {"key": "x"},
            {"live_14d_fills": 2, "live_observation_days": 14,
             "regime_factor": 1.0, "weekly_opens_require_2y": True},
        )
        self.assertIsNone(pack["expected_weekly_fills"])
        self.assertFalse(ai.weekly_opens_2y_sample_ok(pack))

    def test_live_14d_allowed_when_2y_not_required(self):
        pack = ai.resolve_statistical_weekly_opens(
            {"key": "x"},
            {"live_14d_fills": 2, "live_observation_days": 14,
             "regime_factor": 1.0, "weekly_opens_require_2y": False},
        )
        self.assertAlmostEqual(pack["expected_weekly_fills"], 1.0)
        self.assertEqual(pack["method"], "live_14d_fill_rate")

    def test_ai_cannot_inflate_above_statistical_anchor(self):
        # normalize clamps inflation
        self.assertEqual(
            ai._normalize_theoretical_weekly_opens(99.0, statistical_anchor=0.0),
            0.0,
        )
        self.assertEqual(
            ai._normalize_theoretical_weekly_opens(1.2, statistical_anchor=1.0),
            1.0,
        )
        self.assertEqual(
            ai._normalize_theoretical_weekly_opens(0.8, statistical_anchor=1.0),
            0.8,
        )

    @staticmethod
    def _passing_vote(name, weekly=0.9):
        return {
            "provider": name, "ok": True, "decision": "APPROVE",
            "theoretical_win_rate_pct": 70.0,
            "theoretical_mean_net_pct": 6.0,
            "theoretical_weekly_opens": weekly,
            "stop_cluster_risk": "low", "stop_cluster_prob": 0.1,
        }

    def test_aggregate_requires_ai_weekly_discount_and_all_three_votes(self):
        orig = ai.theoretical_review_one
        try:
            ai.theoretical_review_one = (
                lambda name, c, e, _retry=True: self._passing_vote(name, 0.9))
            out = ai.theoretical_review_all(
                {"key": "x"},
                {"statistical_weekly_opens_expected": 1.0,
                 "frequency_method": "backtest_2y_fill_rate_proxy",
                 "span_days": 730.0, "trades": 104},
            )
            self.assertTrue(out["approved"], out.get("fail_reasons"))
            self.assertAlmostEqual(out["ai_theoretical_weekly_opens_avg"], 0.9)
            self.assertAlmostEqual(out["statistical_weekly_opens_expected"], 1.0)
            self.assertTrue(out["weekly_opens_2y_sample_ok"])
            self.assertTrue(ai.validate_theoretical_review_result(out)["ok"])

            # AI discounts below floor → fail even if statistical anchor is high
            ai.theoretical_review_one = (
                lambda name, c, e, _retry=True: self._passing_vote(name, 0.2))
            low = ai.theoretical_review_all(
                {"key": "x"},
                {"statistical_weekly_opens_expected": 1.0,
                 "frequency_method": "backtest_2y_fill_rate_proxy",
                 "span_days": 730.0, "trades": 104},
            )
            self.assertFalse(low["approved"])
            self.assertFalse(low["weekly_opens_gate_ok"])
        finally:
            ai.theoretical_review_one = orig

    def test_unavailable_third_ai_cannot_be_skipped(self):
        def fake(name, c, e, _retry=True):
            if name == "glm":
                return {"provider": name, "ok": False, "decision": "REJECT",
                        "reason": "HTTP Error 429", "theoretical_win_rate_pct": 0,
                        "theoretical_mean_net_pct": None,
                        "theoretical_weekly_opens": None,
                        "stop_cluster_risk": "high", "stop_cluster_prob": 1.0}
            return self._passing_vote(name)
        orig = ai.theoretical_review_one
        try:
            ai.theoretical_review_one = fake
            out = ai.theoretical_review_all(
                {"key": "x"},
                {"statistical_weekly_opens_expected": 1.0,
                 "frequency_method": "backtest_2y_fill_rate_proxy",
                 "span_days": 730.0, "trades": 104},
            )
            self.assertFalse(out["approved"])
            self.assertFalse(ai.validate_theoretical_review_result(out)["ok"])
            self.assertTrue(any("healthy_voters_lt_3" in x
                                for x in out["fail_reasons"]))
        finally:
            ai.theoretical_review_one = orig

    def test_forged_approved_true_is_rejected(self):
        verified = ai.validate_theoretical_review_result({
            "approved": True,
            "statistical_weekly_opens_expected": 1.0,
            "ai_theoretical_weekly_opens_avg": 1.0,
        })
        self.assertFalse(verified["ok"])
        self.assertTrue(any("schema" in x for x in verified["reasons"]))


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
