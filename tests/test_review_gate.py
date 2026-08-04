# -*- coding: utf-8 -*-
"""P0 tests for review_gate handoff token + parity preflight."""
from __future__ import print_function

import unittest

from dual_engine_workflow_v2 import review_gate as rgate
from dual_engine_workflow_v2 import manufacture_batch_policy as mfg
import auto_trade_ai_consensus as ai


class TestHandoffToken(unittest.TestCase):
    def test_issue_and_require(self):
        metrics = {
            "n": 20,
            "win_rate": 0.50,
            "win_rate_pct": 50.0,
            "weekly_opens": 1.0,
            "mean_win_only_pct": 12.0,
            "profit_first_rate": 0.60,
            "median_mae_pct": 0.002,
        }
        tok = rgate.issue_handoff_token(
            recipe_id="recipe_test", metrics=metrics, job_id="job1",
        )
        self.assertTrue(tok.get("ok"))
        self.assertTrue(tok.get("token"))
        check = rgate.require_handoff_token(
            tok, recipe_id="recipe_test", metrics=metrics,
        )
        self.assertTrue(check.get("ok"))

    def test_missing_token_blocked(self):
        check = rgate.require_handoff_token(None, recipe_id="x", metrics={})
        self.assertFalse(check.get("ok"))
        self.assertEqual(check.get("error"), "HANDOFF_TOKEN_INVALID")

    def test_garbage_metrics_no_token(self):
        metrics = {
            "n": 20,
            "win_rate": 0.30,
            "weekly_opens": 1.0,
            "mean_win_only_pct": 6.0,
            "profit_first_rate": 0.30,
            "median_mae_pct": 0.01,
        }
        tok = rgate.issue_handoff_token(recipe_id="bad", metrics=metrics)
        self.assertFalse(tok.get("ok"))


class TestParityPnlRatio(unittest.TestCase):
    def test_pnl_ratio_parity_passes(self):
        trades = [
            {"pnl_ratio": 0.12},
            {"pnl_ratio": 0.14},
            {"pnl_ratio": -0.10},
        ]
        win_pack = mfg.levered_win_only_mean_pct(trades=trades)
        prod = {
            "n": 3,
            "mean_win_only_pct": win_pack.get("mean_win_only_pct"),
            "average_profitable_trade_return": win_pack.get("mean_win_only_ratio"),
        }
        parity = rgate.validate_review_metrics_parity(trades, production_metrics=prod)
        self.assertTrue(parity.get("metric_parity_passed"), parity)


class TestFourAiProviders(unittest.TestCase):
    def test_providers_locked(self):
        self.assertEqual(
            tuple(ai.PROVIDERS),
            ("deepseek", "qwen", "glm", "kimi"),
        )
        self.assertEqual(tuple(ai.STANDBY_PROVIDERS), ())


if __name__ == "__main__":
    unittest.main()
