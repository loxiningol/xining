# -*- coding: utf-8 -*-
"""ADA-T3 calibrated admission_v2 — golden sample must pass; legacy floors fail."""
from __future__ import print_function

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dual_engine_workflow_v2 import review_admission_v2 as adm  # noqa: E402


class GoldenAdaT3Tests(unittest.TestCase):
    def test_golden_passes_reconstructed_admission(self):
        out = adm.assert_golden_passes()
        self.assertTrue(out["pass"], out)
        self.assertEqual(out["calibrated_to"], adm.GOLDEN_KEY)
        self.assertTrue(out["reviews"]["r1"]["pass"])
        self.assertTrue(out["reviews"]["r2"]["pass"])
        self.assertTrue(out["reviews"]["r3"]["pass"])
        # Legacy floors remain failed but advisory
        self.assertFalse(out["legacy_advisory"]["legacy_l0_pass"])
        self.assertFalse(out["legacy_advisory"]["legacy_l1_pass"])
        self.assertFalse(out["legacy_advisory"]["legacy_gate2_pass"])
        self.assertFalse(out["legacy_advisory"]["blocking"])

    def test_review2_rejects_empty(self):
        r2 = adm.review2_evidence(metrics={"trades": 3, "win_rate": 80.0, "mean_net": 0.01})
        self.assertFalse(r2["pass"])
        self.assertTrue(any("trades_ge" in x for x in r2["reject_reasons"]))

    def test_review2_rejects_low_wr(self):
        r2 = adm.review2_evidence(metrics={"trades": 20, "win_rate": 40.0, "mean_net": 0.01})
        self.assertFalse(r2["pass"])

    def test_review2_rejects_nonpositive_mean(self):
        r2 = adm.review2_evidence(metrics={"trades": 20, "win_rate": 80.0, "mean_net": -0.01})
        self.assertFalse(r2["pass"])

    def test_prod_live_metrics_shape_passes_review2(self):
        # Fresh prod BT shape (2026-07-30): n=20 wr=80 mean_net>0
        r2 = adm.review2_evidence(metrics={
            "trades": 20,
            "win_rate": 80.0,
            "mean_net": 0.0385054318729128,
        })
        self.assertTrue(r2["pass"], r2)

    def test_legacy_payoff_would_fail_but_not_blocking(self):
        # Document: payoff 0.70 fails L1/Gate2; admission still passes via review2
        out = adm.evaluate_admission(
            definition={"key": "x", "entry": {}, "exit": {}},
            lookahead_ok=True,
            metrics={"trades": 20, "win_rate": 80.0, "mean_net": 0.038},
            ai_review={"approved": True, "ai_theoretical_wr_avg": 74.3},
            pending_ok=True,
            l1={"pass": False, "reject_reasons": ["sample_payoff_le_1.2"]},
            gate2_fitness={"pass": False, "failed_checks": ["payoff_ge_2_5", "worst5_loss_share_le_40pct"]},
        )
        self.assertTrue(out["pass"])
        self.assertIn("sample_payoff_le_1.2", out["legacy_advisory"]["legacy_l1_reasons"])


if __name__ == "__main__":
    unittest.main()
