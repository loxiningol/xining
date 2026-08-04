# -*- coding: utf-8 -*-
"""P1 tests for Layer B quality_optimization."""
from __future__ import print_function

import unittest
from datetime import datetime, timedelta

from dual_engine_workflow_v2 import quality_optimization as qopt
from dual_engine_workflow_v2 import manufacture_batch_policy as mfg


class TestClassifyFailures(unittest.TestCase):
    def test_low_wr_and_path_codes(self):
        diag = qopt.classify_package_failures({
            "win_rate": 0.33,
            "mean_win_only_pct": 12.3,
            "profit_first_rate": 0.30,
            "median_mae_pct": -0.004,
            "weekly_opens": 8.0,
        })
        self.assertIn(qopt.LOW_WIN_RATE, diag["codes"])
        self.assertIn(qopt.LOW_PROFIT_FIRST_RATE, diag["codes"])
        self.assertIn(qopt.HIGH_MAE, diag["codes"])
        self.assertEqual(diag["primary"], qopt.LOW_WIN_RATE)
        self.assertIn("entry_confirmation", diag["allowed_modifications"])
        self.assertIn("change_protective_stop", diag["forbidden"])

    def test_forbidden_never_allows_stop_or_leverage(self):
        for mods in qopt.ALLOWED_MODS.values():
            self.assertNotIn("change_protective_stop", mods)
            self.assertNotIn("change_leverage", mods)

    def test_classify_batch_dominant(self):
        packs = [
            {
                "recipe_id": "a",
                "rank": 1,
                "select_metrics": {
                    "win_rate": 0.30,
                    "mean_win_only_pct": 12.0,
                    "profit_first_rate": 0.30,
                    "median_mae_pct": 0.004,
                    "weekly_opens": 2.0,
                },
            },
            {
                "recipe_id": "b",
                "rank": 2,
                "select_metrics": {
                    "win_rate": 0.32,
                    "mean_win_only_pct": 11.5,
                    "profit_first_rate": 0.28,
                    "median_mae_pct": 0.005,
                    "weekly_opens": 3.0,
                },
            },
        ]
        batch = qopt.classify_batch(packs)
        self.assertEqual(batch["n_packages"], 2)
        self.assertIn(qopt.LOW_WIN_RATE, batch["dominant_codes"])
        self.assertIn(qopt.LOW_PROFIT_FIRST_RATE, batch["dominant_codes"])
        self.assertTrue(batch["forbidden_always"])


class TestDiversity(unittest.TestCase):
    def test_inject_missing_representations(self):
        collapsed = [
            {
                "hypothesis_id": "h1",
                "family": "exhaustion",
                "factor_hints": ["rsi_14"],
                "representation_type": "threshold",
            },
            {
                "hypothesis_id": "h2",
                "family": "exhaustion",
                "factor_hints": ["rsi_14", "bb_lower_dist"],
                "representation_type": "threshold",
            },
        ]
        before = qopt.diversity_audit(collapsed)
        self.assertFalse(before["ok"])
        self.assertIn("representation_types_below_4", before["reasons"])
        out = qopt.inject_missing_representations(collapsed, direction="long", limit=8)
        self.assertGreaterEqual(int(out["injected_n"] or 0), 1)
        after = out["diversity_audit_after"]
        self.assertGreaterEqual(len(after.get("representation_counts") or {}), 3)

    def test_six_representation_types_constant(self):
        self.assertEqual(len(qopt.REPRESENTATION_TYPES), 6)


class TestRecent2yHandoff(unittest.TestCase):
    def test_handoff_rejects_explicit_recent_2y_fail(self):
        metrics = {
            "n": 20,
            "win_rate": 0.55,
            "weekly_opens": 1.0,
            "mean_win_only_pct": 12.0,
            "profit_first_rate": 0.60,
            "median_mae_pct": 0.002,
            "recent_2y_requirement_passed": False,
        }
        gate = mfg.qualifies_for_review_handoff(metrics)
        self.assertFalse(gate["ok"])
        self.assertTrue(
            any("recent_2y" in r for r in gate["reasons"]),
            gate["reasons"],
        )

    def test_handoff_missing_recent_2y_not_auto_fail(self):
        metrics = {
            "n": 20,
            "win_rate": 0.55,
            "weekly_opens": 1.0,
            "mean_win_only_pct": 12.0,
            "profit_first_rate": 0.60,
            "median_mae_pct": 0.002,
        }
        gate = mfg.qualifies_for_review_handoff(metrics)
        self.assertTrue(gate["ok"], gate)

    def test_recent_2y_gate_from_returns(self):
        as_of = datetime(2026, 8, 2, 12, 0, 0)
        trades = []
        # Recent winners below 11.11%
        for i in range(6):
            trades.append({
                "pnl_ratio": 0.05,
                "entry_time": (as_of - timedelta(days=30 + i)).strftime("%Y-%m-%d %H:%M:%S"),
            })
        # Older winners high — must not rescue recent
        for i in range(6):
            trades.append({
                "pnl_ratio": 0.20,
                "entry_time": (as_of - timedelta(days=900 + i)).strftime("%Y-%m-%d %H:%M:%S"),
            })
        gate = qopt.recent_2y_gate_from_returns(trades, as_of=as_of)
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["decision"], "reject_recent_2y_failure")


class TestQualityRepair(unittest.TestCase):
    def test_repair_hyps_never_touch_stop(self):
        hyps = qopt.build_quality_repair_hypotheses(
            direction="long",
            failure_codes=[qopt.LOW_PROFIT_FIRST_RATE, qopt.HIGH_MAE],
        )
        self.assertGreaterEqual(len(hyps), 1)
        for h in hyps:
            self.assertIn("change_protective_stop", h["forbidden_modifications"])
            self.assertIn(h["representation_type"], qopt.REPRESENTATION_TYPES)


class TestP4ExtendedDiagnostics(unittest.TestCase):
    def test_all_twelve_failure_codes_defined(self):
        self.assertEqual(len(qopt.ALL_FAILURE_CODES), 12)
        for code in qopt.ALL_FAILURE_CODES:
            self.assertIn(code, qopt.ALLOWED_MODS)
            for mod in qopt.ALLOWED_MODS[code]:
                self.assertNotIn(mod, qopt.FORBIDDEN_ALWAYS)

    def test_classify_cost_delay_spike_regime_concentration(self):
        diag = qopt.classify_package_failures({
            "win_rate": 0.55,
            "mean_win_only_pct": 12.0,
            "profit_first_rate": 0.60,
            "median_mae_pct": 0.002,
            "weekly_opens": 1.0,
            "parameter_plateau_score": 0.1,
            "cost_stress_passed": False,
            "delay_stress_passed": False,
            "top_trade_pnl_share": 0.7,
            "regime_slice_pass_rate": 0.2,
        })
        for code in (
            qopt.PARAMETER_SPIKE, qopt.COST_FAILURE, qopt.DELAY_FAILURE,
            qopt.PNL_CONCENTRATION, qopt.REGIME_INSTABILITY,
        ):
            self.assertIn(code, diag["codes"], diag)

    def test_parameter_plateau_score_spike(self):
        spike = qopt.parameter_plateau_score([
            {"params": {"rsi_threshold": 35}, "score": 1.0},
            {"params": {"rsi_threshold": 32}, "score": 0.1},
            {"params": {"rsi_threshold": 38}, "score": 0.15},
        ])
        self.assertTrue(spike["parameter_spike"])
        self.assertLess(spike["parameter_plateau_score"], qopt.PLATEAU_MIN_SCORE)
        flat = qopt.parameter_plateau_score([
            {"params": {"rsi_threshold": 35}, "score": 1.0},
            {"params": {"rsi_threshold": 32}, "score": 0.95},
            {"params": {"rsi_threshold": 38}, "score": 0.92},
        ])
        self.assertFalse(flat["parameter_spike"])

    def test_expand_plateau_never_mentions_stop(self):
        neighbors = qopt.expand_adjacent_param_plateau(limit=6)
        self.assertGreaterEqual(len(neighbors), 2)
        blob = json_dumps_safe(neighbors)
        self.assertNotIn("protective_stop", blob)
        self.assertNotIn("leverage", blob)

    def test_directed_repair_plan_actions(self):
        plan = qopt.directed_repair_plan(
            failure_codes=[qopt.LOW_FREQUENCY, qopt.LOW_PROFIT_FIRST_RATE],
            direction="long",
        )
        self.assertTrue(plan["actions"])
        self.assertGreaterEqual(len(plan["hypotheses"]), 1)
        self.assertIn("change_protective_stop", plan["forbidden"])

    def test_handoff_rejects_cost_and_concentration(self):
        base = {
            "n": 20,
            "win_rate": 0.55,
            "weekly_opens": 1.0,
            "mean_win_only_pct": 12.0,
            "profit_first_rate": 0.60,
            "median_mae_pct": 0.002,
        }
        g1 = mfg.qualifies_for_review_handoff(dict(base, cost_stress_passed=False))
        self.assertFalse(g1["ok"])
        g2 = mfg.qualifies_for_review_handoff(dict(base, top_trade_pnl_share=0.8))
        self.assertFalse(g2["ok"])

    def test_select_top_never_pads_junk(self):
        packs = [
            {
                "recipe_id": "junk1",
                "returns": [0.02] * 5,
                "select_metrics": {
                    "n": 5,
                    "win_rate": 0.30,
                    "weekly_opens": 0.2,
                    "mean_win_only_pct": 6.0,
                    "profit_first_rate": 0.30,
                    "median_mae_pct": 0.005,
                },
            },
            {
                "recipe_id": "junk2",
                "returns": [0.02] * 8,
                "select_metrics": {
                    "n": 8,
                    "win_rate": 0.32,
                    "weekly_opens": 0.3,
                    "mean_win_only_pct": 6.5,
                    "profit_first_rate": 0.28,
                    "median_mae_pct": 0.004,
                },
            },
        ]
        top, ranked = mfg.select_top_for_review(packs, top_n=3)
        self.assertEqual(len(top), 0)
        audit = mfg.handoff_bypass_audit(top, ranked)
        self.assertTrue(audit["ok"])
        self.assertFalse(audit["padded_with_junk"])


def json_dumps_safe(obj):
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)


if __name__ == "__main__":
    unittest.main()
