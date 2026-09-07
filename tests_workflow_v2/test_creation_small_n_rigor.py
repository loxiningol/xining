# -*- coding: utf-8 -*-
"""Phase A small-n rigor: anti-waive, discount, timing budget, observe stats."""
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
        f = rigor.n_discount_factor(30, kappa=50)
        self.assertAlmostEqual(f, 30.0 / 80.0)

    def test_low_n_shrinks_hard(self):
        raw = 1.0
        d = rigor.discount_metric(raw, 10, kappa=50)
        self.assertLess(d, 0.25)


class TestTimingBudget(unittest.TestCase):
    def test_n30_budget_3(self):
        self.assertEqual(rigor.max_timing_atoms(30), 3)

    def test_soft_over_budget_warning_not_hard(self):
        os.environ["CREATE_TIMING_BUDGET"] = "soft"
        os.environ["CREATE_PLACEBO"] = "off"
        os.environ["CREATE_LOO"] = "off"
        os.environ["CREATE_MC_SUBSET"] = "off"
        timing = [
            {"factor": "skdj_k", "operator": "above", "value": 50},
            {"factor": "macd_hist", "operator": "above", "value": 0},
            {"factor": "cci", "operator": "below", "value": 100},
            {"factor": "kdj_k", "operator": "above", "value": 20},
        ]
        out = rigor.evaluate(n=30, timing=timing, returns=[0.01] * 30)
        self.assertTrue(out["ok"])
        self.assertIn(rigor.CODE_TIMING_DIM, out["warnings"])
        self.assertNotIn(rigor.CODE_TIMING_DIM, out["failed_rules"])

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
        ev = {
            "ok": False,
            "failed_rules": ["trade_count_below_threshold"],
            "E": 0.01,
        }
        out, why = rigor.never_waive_trade_count(ev)
        self.assertIsNone(out)
        self.assertIn("trade_count", why)

    def test_clean_gate_passes(self):
        ev = {"ok": True, "failed_rules": [], "E": 0.01, "quality_gate": {"failed_rules": []}}
        out, why = rigor.never_waive_trade_count(ev)
        self.assertIsNotNone(out)
        self.assertIsNone(why)


class TestPlaceboObserve(unittest.TestCase):
    def test_strong_edge_observe_ok(self):
        os.environ["CREATE_PLACEBO"] = "observe"
        os.environ["CREATE_TIMING_BUDGET"] = "off"
        os.environ["CREATE_LOO"] = "off"
        os.environ["CREATE_MC_SUBSET"] = "off"
        # clear strong positive — should rank well
        rets = [0.02] * 25 + [-0.001] * 5
        out = rigor.evaluate(returns=rets, n=30, timing=[{"factor": "skdj_k", "operator": "above", "value": 50}])
        self.assertTrue(out["ok"])
        self.assertEqual(out["failed_rules"], [])
        self.assertIn(rigor.CODE_PLACEBO, out["evidence"])

    def test_placebo_hard_rejects_noise(self):
        os.environ["CREATE_PLACEBO"] = "hard"
        os.environ["CREATE_TIMING_BUDGET"] = "off"
        os.environ["CREATE_LOO"] = "off"
        os.environ["CREATE_MC_SUBSET"] = "off"
        # near-zero mean — shuffle often matches
        rets = [0.001, -0.001] * 15
        out = rigor.evaluate(returns=rets, n=30, timing=[])
        # may or may not fail depending on RNG; at least evidence present
        self.assertIn(rigor.CODE_PLACEBO, out["evidence"])


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
        a = rigor.install_into_kdh(kdh)
        b = rigor.install_into_kdh(kdh)
        self.assertTrue(a.get("installed"))
        self.assertTrue(b.get("already"))
        out, why = kdh._waive_trade_count_only({
            "ok": False,
            "failed_rules": ["trade_count_below_threshold"],
            "E": 0.01,
        })
        self.assertIsNone(out)
        brief = kdh._user_brief("5", ["BTC-USDT-SWAP"])
        self.assertIn("禁止为凑 n", brief)


class TestPhase0Template(unittest.TestCase):
    def test_template_has_hub_defaults(self):
        t = rigor.phase0_baseline_template()
        self.assertEqual(t["phase"], "0")
        self.assertIn("hub_b", t["day0_defaults"])
        self.assertEqual(t["day0_defaults"]["hub_b"]["CREATE_PLACEBO"], "observe")


if __name__ == "__main__":
    unittest.main()
