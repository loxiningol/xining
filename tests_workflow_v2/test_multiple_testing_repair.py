# -*- coding: utf-8 -*-
from __future__ import print_function

import math
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dual_engine_workflow_v2 import multiple_testing as mt


class TestSharpeAndDsrScale(unittest.TestCase):
    def test_sharpe_is_nonannualized_unless_frequency_is_explicit(self):
        returns = [0.01, -0.004, 0.008, -0.002, 0.012, 0.003, -0.001, 0.006]
        mean = sum(returns) / float(len(returns))
        variance = sum((value - mean) ** 2 for value in returns) / float(len(returns) - 1)
        expected = mean / math.sqrt(variance)

        raw = mt.sharpe_ratio(returns)
        annualized = mt.sharpe_ratio(returns, periods_per_year=252)

        self.assertAlmostEqual(raw, expected, places=12)
        self.assertAlmostEqual(annualized, expected * math.sqrt(252), places=12)
        self.assertAlmostEqual(mt.annualized_sharpe_ratio(returns, 252), annualized, places=12)
        self.assertNotAlmostEqual(raw, expected * math.sqrt(len(returns)), places=6)

    def test_dsr_reuses_nonannualized_sharpe_and_keeps_threshold(self):
        returns = [0.012, -0.003, 0.009, -0.002, 0.007, 0.001] * 8
        result = mt.deflated_sharpe_ratio(returns, n_trials=50)

        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["sharpe"], mt.sharpe_ratio(returns), places=12)
        self.assertEqual(result["sharpe_type"], "non_annualized_return_frequency")
        self.assertEqual(result["threshold"], 0.95)
        self.assertGreater(result["sr0_null_max"], 0)
        self.assertLess(result["sr0_null_max"], math.sqrt(2.0 * math.log(50)))
        self.assertGreaterEqual(result["dsr"], 0)
        self.assertLessEqual(result["dsr"], 1)

    def test_invalid_annualization_is_rejected(self):
        with self.assertRaises(ValueError):
            mt.sharpe_ratio([0.01, -0.01, 0.02, -0.01, 0.01], periods_per_year=0)


class TestPboAlignmentAndRanking(unittest.TestCase):
    @staticmethod
    def _series(start, stop, scale=1.0):
        return [
            (index, scale * (0.01 if index % 3 else -0.004))
            for index in range(start, stop)
        ]

    def test_timestamped_candidates_use_common_time_intersection(self):
        first = self._series(0, 32, 1.0)
        second = self._series(4, 36, 1.7)
        result = mt.pbo_cscv([first, second], n_partitions=4)

        self.assertTrue(result["ok"])
        self.assertEqual(result["alignment_mode"], "common_time_index")
        self.assertEqual(result["n_common_observations"], 28)
        self.assertEqual(result["dropped_observations_by_candidate"], [4, 4])

    def test_timezone_offsets_align_by_instant(self):
        utc = datetime(2026, 1, 1, tzinfo=timezone.utc)
        plus_eight = timezone(timedelta(hours=8))
        first = [(utc + timedelta(minutes=i), 0.01 if i % 3 else -0.004) for i in range(24)]
        second = [
            ((utc + timedelta(minutes=i)).astimezone(plus_eight), 0.008 if i % 4 else -0.003)
            for i in range(24)
        ]
        result = mt.pbo_cscv([first, second], n_partitions=4)

        self.assertTrue(result["ok"])
        self.assertEqual(result["alignment_mode"], "common_time_index")
        self.assertEqual(result["n_common_observations"], 24)

    def test_unequal_legacy_lists_are_not_truncated(self):
        result = mt.pbo_cscv([[0.01] * 20, [0.02] * 21], n_partitions=4)

        self.assertFalse(result["ok"])
        self.assertFalse(result["passed"])
        self.assertTrue(result["blocking"])
        self.assertEqual(result["error"], "unaligned_legacy_lengths")
        self.assertEqual(result["alignment_mode"], "rejected_no_time_index")

    def test_equal_legacy_lists_state_the_positional_assumption(self):
        first = [0.01 if i % 2 else -0.004 for i in range(24)]
        second = [0.008 if i % 3 else -0.006 for i in range(24)]
        result = mt.pbo_cscv([first, second], n_partitions=4)

        self.assertTrue(result["ok"])
        self.assertEqual(result["alignment_mode"], "positional_legacy_equal_length")
        self.assertEqual(result["alignment_assumption"], "caller_guarantees_same_observation_clock")

    def test_zero_sharpe_is_not_replaced_by_error_floor(self):
        flat_zero = [0.0] * 32
        consistently_bad = [-0.02 if i % 2 else -0.01 for i in range(32)]
        result = mt.pbo_cscv([flat_zero, consistently_bad], n_partitions=4)

        self.assertTrue(result["ok"])
        self.assertEqual(result["pbo"], 0.0)
        self.assertTrue(result["passed"])

    def test_tie_rank_is_average_and_candidate_order_neutral(self):
        self.assertEqual(mt._average_rank_percentile([0.0, 0.0, 0.0], 0), 0.5)
        self.assertEqual(mt._average_rank_percentile([-1.0, 0.0, 0.0, 2.0], 1), 0.5)

        base = [1.0 if i % 3 else -0.5 for i in range(32)]
        scaled = [value * 2.0 for value in base]
        result = mt.pbo_cscv([base, scaled], n_partitions=4)
        reversed_result = mt.pbo_cscv([scaled, base], n_partitions=4)
        self.assertTrue(result["ok"])
        self.assertGreater(result["n_is_tie_combos"], 0)
        self.assertEqual(result["pbo"], reversed_result["pbo"])

    def test_invalid_multi_candidate_alignment_fails_closed(self):
        best = [0.02 if i % 4 else -0.003 for i in range(40)]
        peer = [0.018 if i % 5 else -0.004 for i in range(41)]
        result = mt.evaluate_multiple_testing(best, [best, peer], n_trials_effective=2)

        self.assertFalse(result["passed"])
        self.assertEqual(result["decision_basis"], "blocked_invalid_pbo_alignment")
        self.assertEqual(result["thresholds_unchanged"], {"dsr_min": 0.95, "pbo_max": 0.4})


class TestPathStabilityNaming(unittest.TestCase):
    def test_path_diagnostic_does_not_claim_full_cpcv(self):
        returns = [0.01 if i % 3 else -0.004 for i in range(60)]
        result = mt.path_stability_sharpes(returns, n_folds=5, embargo=3)
        compatibility = mt.cpcv_oos_sharpes(returns, n_folds=5, embargo=3)

        self.assertTrue(result["ok"])
        self.assertEqual(result["method"], "walk_forward_path_stability")
        self.assertFalse(result["is_full_cpcv"])
        self.assertFalse(result["selection_retrained_each_fold"])
        self.assertIn("median_path_sharpe", result)
        self.assertEqual(compatibility["compatibility_alias"], "cpcv_oos_sharpes")


class TestEffectiveTrialEstimator(unittest.TestCase):
    @staticmethod
    def _descriptor(index=0, family="momentum"):
        return {
            "family": family,
            "mechanism_id": "mechanism_%s" % index,
            "normalized_event": "event_%s" % index,
            "factors": ["factor_%s" % index],
            "direction": "long" if index % 2 == 0 else "short",
            "horizon": "%sm" % (index + 1),
            "execution_mapping": {"entry": "close_%s" % index},
        }

    def test_exact_duplicates_count_once(self):
        descriptor = self._descriptor()
        result = mt.estimate_effective_trials([descriptor] * 10, raw_trials=10)

        self.assertTrue(result["ok"])
        self.assertEqual(result["raw_trials"], 10)
        self.assertEqual(result["unique_signatures"], 1)
        self.assertEqual(result["duplicate_trials"], 9)
        self.assertEqual(result["effective_trials"], 1.0)

    def test_family_related_unique_trials_are_downweighted_with_evidence(self):
        descriptors = [self._descriptor(index) for index in range(4)]
        result = mt.estimate_effective_trials(descriptors, raw_trials=4)

        self.assertEqual(result["unique_signatures"], 4)
        self.assertGreaterEqual(result["effective_trials"], math.sqrt(4))
        self.assertLess(result["effective_trials"], 4)
        self.assertGreater(result["pairwise_similarity_mean"], 0)
        self.assertEqual(result["family_counts"], {"momentum": 4})
        self.assertIn("similarity_weights", result)
        self.assertEqual(len(result["signature_clusters"]), 4)

    def test_missing_descriptors_retain_full_independent_weight(self):
        descriptor = self._descriptor()
        result = mt.estimate_effective_trials([descriptor, descriptor], raw_trials=10)

        self.assertEqual(result["unclassified_trials_full_weight"], 8)
        self.assertEqual(result["effective_trials"], 9.0)
        self.assertEqual(result["coverage_ratio"], 0.2)

    def test_empty_descriptors_cannot_reduce_penalty(self):
        result = mt.estimate_effective_trials([{}, {}, {}], raw_trials=3)
        self.assertEqual(result["classified_trials"], 0)
        self.assertEqual(result["effective_trials"], 3.0)


if __name__ == "__main__":
    unittest.main()
