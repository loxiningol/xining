# -*- coding: utf-8 -*-
import unittest

import auto_trade_ai_consensus as ai


class TheoreticalReviewGateTest(unittest.TestCase):
    def test_pass_low_risk(self):
        row = {
            "ok": True, "decision": "APPROVE",
            "theoretical_win_rate_pct": 55,
            "stop_cluster_risk": "low", "stop_cluster_prob": 0.4,
        }
        self.assertTrue(ai._provider_theoretical_pass(row))

    def test_pass_prob_even_if_medium(self):
        row = {
            "ok": True, "decision": "APPROVE",
            "theoretical_win_rate_pct": 60,
            "stop_cluster_risk": "medium", "stop_cluster_prob": 0.25,
        }
        self.assertTrue(ai._provider_theoretical_pass(row))

    def test_fail_wr(self):
        row = {
            "ok": True, "decision": "APPROVE",
            "theoretical_win_rate_pct": 49,
            "stop_cluster_risk": "low", "stop_cluster_prob": 0.1,
        }
        self.assertFalse(ai._provider_theoretical_pass(row))

    def test_fail_cluster(self):
        row = {
            "ok": True, "decision": "APPROVE",
            "theoretical_win_rate_pct": 70,
            "stop_cluster_risk": "high", "stop_cluster_prob": 0.5,
        }
        self.assertFalse(ai._provider_theoretical_pass(row))

    def test_aggregate_avg(self):
        # monkeypatch theoretical_review_one
        fixed = {
            "deepseek": {"provider": "deepseek", "ok": True, "decision": "APPROVE",
                         "theoretical_win_rate_pct": 60, "stop_cluster_risk": "low",
                         "stop_cluster_prob": 0.1},
            "qwen": {"provider": "qwen", "ok": True, "decision": "APPROVE",
                     "theoretical_win_rate_pct": 50, "stop_cluster_risk": "low",
                     "stop_cluster_prob": 0.2},
            "glm": {"provider": "glm", "ok": True, "decision": "APPROVE",
                        "theoretical_win_rate_pct": 70, "stop_cluster_risk": "low",
                        "stop_cluster_prob": 0.15},
        }
        orig = ai.theoretical_review_one
        try:
            ai.theoretical_review_one = lambda name, c, e, _retry=True: fixed[name]
            out = ai.theoretical_review_all({"key": "x"}, {})
            self.assertTrue(out["approved"])
            self.assertAlmostEqual(out["ai_theoretical_wr_avg"], 60.0, places=3)
            self.assertEqual(out["voting_providers"], ["deepseek", "qwen", "glm"])
        finally:
            ai.theoretical_review_one = orig

    def test_skip_glm_infra_two_votes_pass(self):
        fixed = {
            "deepseek": {"provider": "deepseek", "ok": True, "decision": "APPROVE",
                         "theoretical_win_rate_pct": 58, "stop_cluster_risk": "low",
                         "stop_cluster_prob": 0.2},
            "qwen": {"provider": "qwen", "ok": True, "decision": "APPROVE",
                     "theoretical_win_rate_pct": 55, "stop_cluster_risk": "low",
                     "stop_cluster_prob": 0.18},
            "glm": {"provider": "glm", "ok": False, "decision": "REJECT",
                        "theoretical_win_rate_pct": 0, "stop_cluster_risk": "high",
                        "stop_cluster_prob": 1.0,
                        "reason": "复核调用失败: HTTP Error 429: Too Many Requests"},
        }
        orig = ai.theoretical_review_one
        try:
            ai.theoretical_review_one = lambda name, c, e, _retry=True: fixed[name]
            out = ai.theoretical_review_all({"key": "y"}, {})
            self.assertTrue(out["approved"])
            self.assertEqual(out["skipped_infra_providers"], ["glm"])
            self.assertEqual(out["voting_providers"], ["deepseek", "qwen"])
            self.assertAlmostEqual(out["ai_theoretical_wr_avg"], 56.5, places=3)
        finally:
            ai.theoretical_review_one = orig

    def test_skip_glm_but_one_reject_fails(self):
        fixed = {
            "deepseek": {"provider": "deepseek", "ok": True, "decision": "APPROVE",
                         "theoretical_win_rate_pct": 60, "stop_cluster_risk": "low",
                         "stop_cluster_prob": 0.1},
            "qwen": {"provider": "qwen", "ok": True, "decision": "REJECT",
                     "theoretical_win_rate_pct": 40, "stop_cluster_risk": "medium",
                     "stop_cluster_prob": 0.45},
            "glm": {"provider": "glm", "ok": False, "decision": "REJECT",
                        "reason": "HTTP Error 429: Too Many Requests"},
        }
        orig = ai.theoretical_review_one
        try:
            ai.theoretical_review_one = lambda name, c, e, _retry=True: fixed[name]
            out = ai.theoretical_review_all({"key": "z"}, {})
            self.assertFalse(out["approved"])
            self.assertEqual(out["skipped_infra_providers"], ["glm"])
        finally:
            ai.theoretical_review_one = orig


if __name__ == "__main__":
    unittest.main()
