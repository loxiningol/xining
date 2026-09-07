# -*- coding: utf-8 -*-
"""Phase A–E small-n rigor tests (blueprint: a=calibrate, b=blunt)."""
from __future__ import print_function

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dual_engine_workflow_v2 import creation_small_n_rigor as rigor


class TestDiscount(unittest.TestCase):
    def test_factor_n30(self):
        self.assertAlmostEqual(rigor.n_discount_factor(30, kappa=50), 30.0 / 80.0)

    def test_low_n_shrinks_hard(self):
        self.assertLess(rigor.discount_metric(1.0, 10, kappa=50), 0.25)


class TestTimingBudget(unittest.TestCase):
    def test_n30_budget_3(self):
        self.assertEqual(rigor.max_timing_atoms(30), 3)

    def test_soft_over_budget_warning_not_hard(self):
        os.environ["CREATE_TIMING_BUDGET"] = "soft"
        os.environ["CREATE_PLACEBO"] = "off"
        os.environ["CREATE_LOO"] = "off"
        os.environ["CREATE_MC_SUBSET"] = "off"
        timing = [{"factor": "x", "operator": "above", "value": 1}] * 4
        out = rigor.evaluate(n=30, timing=timing, returns=[0.01] * 30)
        self.assertTrue(out["ok"])
        self.assertIn(rigor.CODE_TIMING_DIM, out["warnings"])

    def test_hard_over_budget_fails(self):
        os.environ["CREATE_TIMING_BUDGET"] = "hard"
        os.environ["CREATE_PLACEBO"] = "off"
        os.environ["CREATE_LOO"] = "off"
        os.environ["CREATE_MC_SUBSET"] = "off"
        timing = [{"factor": "a", "operator": "above", "value": 1}] * 5
        out = rigor.evaluate(n=30, timing=timing, returns=[0.01] * 30)
        self.assertFalse(out["ok"])
        self.assertIn(rigor.CODE_TIMING_DIM, out["failed_rules"])


class TestNeverWaive(unittest.TestCase):
    def test_trade_count_failure_not_cleared(self):
        out, why = rigor.never_waive_trade_count({
            "ok": False,
            "failed_rules": ["trade_count_below_threshold"],
            "E": 0.01,
        })
        self.assertIsNone(out)
        self.assertIn("trade_count", why)

    def test_clean_gate_passes(self):
        out, why = rigor.never_waive_trade_count({
            "ok": True, "failed_rules": [], "E": 0.01,
            "quality_gate": {"failed_rules": []},
        })
        self.assertIsNotNone(out)
        self.assertIsNone(why)


class TestInstallIdempotent(unittest.TestCase):
    def test_install_twice(self):
        class Fake(object):
            def _waive_trade_count_only(self, ev):
                return ev, None

            def _user_brief(self, lane, symbols):
                return "brief"

            def _pair_feedback(self, recipe, result, ident, adjusts, blast=False):
                return "pair"

            def evaluate_atom(self, cand):
                return {"ok": True, "n": 30, "failed_rules": []}

        kdh = Fake()
        os.environ["CREATE_ANTI_EVASION"] = "1"
        self.assertTrue(rigor.install_into_kdh(kdh).get("installed"))
        self.assertTrue(rigor.install_into_kdh(kdh).get("already"))
        out, _ = kdh._waive_trade_count_only({
            "ok": False,
            "failed_rules": ["trade_count_below_threshold"],
            "E": 0.01,
        })
        self.assertIsNone(out)
        self.assertIn("禁止为凑 n", kdh._user_brief("primary", ["BTC-USDT-SWAP"]))


class TestBlueprintProfiles(unittest.TestCase):
    def test_phase_b_a_calibrate_b_blunt(self):
        a = rigor.apply_profile(rigor.PROFILE_HUB_A_PHASE_B, force=True)["config"]
        b = rigor.apply_profile(rigor.PROFILE_HUB_B_PHASE_B, force=True)["config"]
        self.assertEqual(a["hub_role"], "calibrate")
        self.assertEqual(a["placebo"], "observe")
        self.assertEqual(b["hub_role"], "blunt")
        self.assertEqual(b["placebo"], "off")

    def test_frozen_a_calibrate_b_blunt(self):
        inv = rigor.frozen_invariants()
        self.assertTrue(inv["ok"], msg=inv.get("errors"))
        self.assertEqual(inv["blueprint"], "a_calibrate_b_blunt")
        self.assertEqual(rigor.FROZEN_HUB_A["CREATE_PLACEBO"], "observe")
        self.assertEqual(rigor.FROZEN_HUB_B["CREATE_PLACEBO"], "off")
        self.assertEqual(rigor.FROZEN_HUB_A["CREATE_TIMING_BUDGET"], "hard")
        self.assertEqual(rigor.FROZEN_HUB_B["CREATE_TIMING_BUDGET"], "hard")

    def test_frozen_rejects_bilateral_hard_stats(self):
        bad_a = dict(rigor.FROZEN_HUB_A)
        bad_b = dict(rigor.FROZEN_HUB_B)
        bad_a["CREATE_PLACEBO"] = "hard"
        bad_b["CREATE_PLACEBO"] = "hard"
        inv = rigor.frozen_invariants(bad_a, bad_b)
        self.assertFalse(inv["ok"])

    def test_healthy_n30_two_leaves_not_hard_killed(self):
        rigor.apply_profile(rigor.FROZEN_HUB_A, force=True)
        timing = [
            {"factor": "skdj_k", "operator": "above", "value": 50},
            {"factor": "macd_hist", "operator": "above", "value": 0},
        ]
        rets = [0.01] * 28 + [-0.002] * 2
        out = rigor.evaluate(n=30, timing=timing, returns=rets)
        self.assertTrue(out["ok"])
        self.assertEqual(out["failed_rules"], [])

    def test_evil_overleaf_hard_killed(self):
        rigor.apply_profile(rigor.FROZEN_HUB_A, force=True)
        timing = [{"factor": "f%d" % i, "operator": "above", "value": 1} for i in range(5)]
        out = rigor.evaluate(n=30, timing=timing, returns=[0.01] * 30)
        self.assertFalse(out["ok"])
        self.assertIn(rigor.CODE_TIMING_DIM, out["failed_rules"])

    def test_phase0_template(self):
        t = rigor.phase0_baseline_template()
        self.assertIn("hub_a_calibrate", t["day0_defaults"])
        self.assertEqual(t["day0_defaults"]["hub_b_blunt"]["CREATE_PLACEBO"], "off")
        self.assertTrue(t["frozen_invariants"]["ok"])


if __name__ == "__main__":
    unittest.main()
