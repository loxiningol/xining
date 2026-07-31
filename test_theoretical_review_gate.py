# -*- coding: utf-8 -*-
import unittest

import auto_trade_ai_consensus as ai


def _ok(provider, wr, mean_net=6.0, weekly=0.9, risk="low", prob=0.1):
    return {
        "provider": provider,
        "ok": True,
        "decision": "APPROVE",
        "theoretical_win_rate_pct": wr,
        "theoretical_mean_net_pct": mean_net,
        "theoretical_weekly_opens": weekly,
        "stop_cluster_risk": risk,
        "stop_cluster_prob": prob,
    }


class TheoreticalReviewGateTest(unittest.TestCase):
    def test_pass_low_risk(self):
        row = _ok("x", 70.0, 6.0, 0.9, "low", 0.4)
        self.assertTrue(ai._provider_theoretical_pass(row))

    def test_pass_prob_even_if_medium(self):
        row = _ok("x", 70.0, 6.0, 0.9, "medium", 0.25)
        self.assertTrue(ai._provider_theoretical_pass(row))

    def test_fail_wr(self):
        row = _ok("x", 49.0, 6.0, 0.9, "low", 0.1)
        self.assertFalse(ai._provider_theoretical_pass(row))

    def test_fail_cluster(self):
        row = _ok("x", 70.0, 6.0, 0.9, "high", 0.5)
        self.assertFalse(ai._provider_theoretical_pass(row))

    def test_fail_weekly(self):
        row = _ok("x", 70.0, 6.0, 0.2, "low", 0.1)
        self.assertFalse(ai._provider_theoretical_pass(row))

    def test_aggregate_avg(self):
        fixed = {
            "deepseek": _ok("deepseek", 70.0, 6.0, 0.9),
            "qwen": _ok("qwen", 68.0, 5.5, 0.85),
            "glm": _ok("glm", 72.0, 5.2, 0.95),
        }
        orig = ai.theoretical_review_one
        try:
            ai.theoretical_review_one = lambda name, c, e, _retry=True: fixed[name]
            out = ai.theoretical_review_all(
                {"key": "x"},
                {"statistical_weekly_opens_expected": 1.0,
                 "frequency_method": "backtest_2y_fill_rate_proxy",
                 "span_days": 730.0, "trades": 104},
            )
            self.assertTrue(out["approved"], out.get("fail_reasons"))
            self.assertAlmostEqual(out["ai_theoretical_wr_avg"], 70.0, places=3)
            self.assertAlmostEqual(
                out["ai_theoretical_weekly_opens_avg"], 0.9, places=3)
            self.assertEqual(out["voting_providers"], ["deepseek", "qwen", "glm"])
            self.assertTrue(out["weekly_opens_gate_ok"])
            self.assertTrue(out["weekly_opens_2y_sample_ok"])
        finally:
            ai.theoretical_review_one = orig

    def test_glm_infra_failure_blocks_three_ai_unanimity(self):
        fixed = {
            "deepseek": _ok("deepseek", 70.0, 6.0, 0.9),
            "qwen": _ok("qwen", 68.0, 5.5, 0.9),
            "glm": {
                "provider": "glm", "ok": False, "decision": "REJECT",
                "theoretical_win_rate_pct": 0, "theoretical_mean_net_pct": 0,
                "theoretical_weekly_opens": None,
                "stop_cluster_risk": "high", "stop_cluster_prob": 1.0,
                "reason": "复核调用失败: HTTP Error 429: Too Many Requests",
            },
        }
        orig = ai.theoretical_review_one
        try:
            ai.theoretical_review_one = lambda name, c, e, _retry=True: fixed[name]
            out = ai.theoretical_review_all(
                {"key": "y"},
                {"statistical_weekly_opens_expected": 1.0,
                 "span_days": 730.0, "trades": 104},
            )
            self.assertFalse(out["approved"])
            self.assertEqual(out["skipped_infra_providers"], ["glm"])
            self.assertEqual(out["voting_providers"], ["deepseek", "qwen"])
            self.assertAlmostEqual(out["ai_theoretical_wr_avg"], 69.0, places=3)
            self.assertTrue(any("healthy_voters_lt_3" in x
                                for x in out.get("fail_reasons") or []))
        finally:
            ai.theoretical_review_one = orig

    def test_skip_glm_but_one_reject_fails(self):
        fixed = {
            "deepseek": _ok("deepseek", 70.0, 6.0, 0.9),
            "qwen": {
                "provider": "qwen", "ok": True, "decision": "REJECT",
                "theoretical_win_rate_pct": 40, "theoretical_mean_net_pct": 3.0,
                "theoretical_weekly_opens": 0.9,
                "stop_cluster_risk": "medium", "stop_cluster_prob": 0.45,
            },
            "glm": {
                "provider": "glm", "ok": False, "decision": "REJECT",
                "reason": "HTTP Error 429: Too Many Requests",
            },
        }
        orig = ai.theoretical_review_one
        try:
            ai.theoretical_review_one = lambda name, c, e, _retry=True: fixed[name]
            out = ai.theoretical_review_all(
                {"key": "z"},
                {"statistical_weekly_opens_expected": 1.0,
                 "span_days": 730.0, "trades": 104},
            )
            self.assertFalse(out["approved"])
            self.assertEqual(out["skipped_infra_providers"], ["glm"])
        finally:
            ai.theoretical_review_one = orig

    def test_rejects_ai_weekly_opens_below_floor(self):
        fixed = {
            "deepseek": _ok("deepseek", 70.0, 6.0, 0.2),
            "qwen": _ok("qwen", 68.0, 5.5, 0.2),
            "glm": _ok("glm", 72.0, 5.2, 0.2),
        }
        orig = ai.theoretical_review_one
        try:
            ai.theoretical_review_one = lambda name, c, e, _retry=True: fixed[name]
            out = ai.theoretical_review_all(
                {"key": "w"},
                {"statistical_weekly_opens_expected": 1.0,
                 "frequency_method": "backtest_2y_fill_rate_proxy",
                 "span_days": 730.0, "trades": 104},
            )
            self.assertFalse(out["approved"])
            self.assertFalse(out["weekly_opens_gate_ok"])
            self.assertTrue(
                any("weekly_opens_lt" in r for r in (out.get("fail_reasons") or []))
            )
        finally:
            ai.theoretical_review_one = orig

    def test_rejects_short_window_even_if_ai_high(self):
        fixed = {
            "deepseek": _ok("deepseek", 70.0, 6.0, 1.0),
            "qwen": _ok("qwen", 68.0, 5.5, 1.0),
            "glm": _ok("glm", 72.0, 5.2, 1.0),
        }
        orig = ai.theoretical_review_one
        try:
            ai.theoretical_review_one = lambda name, c, e, _retry=True: fixed[name]
            out = ai.theoretical_review_all(
                {"key": "short"},
                {"trades": 20, "span_days": 40.0},
            )
            self.assertFalse(out["approved"])
            self.assertFalse(out["weekly_opens_2y_sample_ok"])
            self.assertTrue(
                any("weekly_2y_sample_required" in r
                    for r in (out.get("fail_reasons") or []))
            )
        finally:
            ai.theoretical_review_one = orig

    def test_rejects_missing_statistical_anchor_even_if_ai_high(self):
        # Without near-2y anchor, aggregate weekly gate fails closed.
        fixed = {
            "deepseek": _ok("deepseek", 70.0, 6.0, 1.0),
            "qwen": _ok("qwen", 68.0, 5.5, 1.0),
            "glm": _ok("glm", 72.0, 5.2, 1.0),
        }
        orig = ai.theoretical_review_one
        try:
            ai.theoretical_review_one = lambda name, c, e, _retry=True: fixed[name]
            out = ai.theoretical_review_all({"key": "no_anchor"}, {})
            self.assertFalse(out["approved"])
            self.assertFalse(out["weekly_opens_gate_ok"])
        finally:
            ai.theoretical_review_one = orig


if __name__ == "__main__":
    unittest.main()
