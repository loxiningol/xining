# -*- coding: utf-8 -*-
"""LLM seed → probe path: EMA/ADX factors and full intersection pins."""
from __future__ import print_function

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ["VECTOR_ROOT"] = os.path.join(ROOT, "strategies", "_scratch")

from dual_engine_workflow_v2 import easyquant_bridge as eq
from dual_engine_workflow_v2 import probe_protocol as pp
from dual_engine_workflow_v2 import recipe_policy as rp
from dual_engine_workflow_v2.research_discovery import (
    _design_seed_hypotheses,
    _llm_seed_probe_fields,
)


class TestLlmSeedProbe(unittest.TestCase):
    def test_factor_matrix_has_ema_adx(self):
        candles = [
            {"open": 100 + i * 0.1, "high": 101 + i * 0.1,
             "low": 99 + i * 0.1, "close": 100 + i * 0.1, "volume": 1000}
            for i in range(300)
        ]
        matrix = eq._build_factor_matrix(candles)
        for key in ("ema_12", "ema_26", "ema_diff", "ema_ratio", "adx_14"):
            self.assertIn(key, matrix)
            self.assertGreater(len(matrix[key]), 0)

    def test_factor_matrix_has_bearish_candle_aligned(self):
        candles = [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 99.5, "volume": 1},
            {"open": 99.5, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1},
        ]
        # Bust cache with unique fingerprint via slightly different series.
        matrix = eq._build_factor_matrix_uncached(candles)
        self.assertEqual(len(matrix.get("bearish_candle") or []), 2)
        self.assertEqual(matrix["bearish_candle"][0], 1.0)
        self.assertEqual(matrix["bearish_candle"][1], 0.0)

    def test_generic_factor_mapping(self):
        for key in ("ema_12", "ema_26", "ema_diff", "ema_ratio", "adx_14"):
            self.assertIn(key, rp.GENERIC_FACTOR_TO_DSL)

    def test_llm_seed_uses_all_factors_and_pinned_quantiles(self):
        seed = {
            "seed_id": "S_test",
            "entry_rule": (
                "ema_diff > 0 AND ema_ratio > 0 AND adx_14 > 20"
            ),
            "factor_hints": ["ema_diff", "ema_ratio", "adx_14"],
            "factor_side_constraints": {
                "ema_diff": "high",
                "ema_ratio": "high",
                "adx_14": "high",
            },
            "parameters": {
                "adx_threshold": [20, 30],
            },
        }
        fields = _llm_seed_probe_fields(seed)
        self.assertEqual(
            ["ema_diff", "ema_ratio", "adx_14"],
            fields["required_factor_intersection"],
        )
        self.assertEqual(3, len(fields["llm_intersection_quantiles"]))
        self.assertTrue(fields["llm_entry_rule_parse"]["ok"])

        matrix = {
            "ema_diff": [float(i % 7) for i in range(200)],
            "ema_ratio": [float((i % 11) - 5) / 100.0 for i in range(200)],
            "adx_14": [float(10 + (i % 40)) for i in range(200)],
        }
        design = {
            "design_doc": {
                "llm_strategy_seeds": [seed],
                "direction": "long",
            }
        }
        rows = _design_seed_hypotheses(design, {"target": {"direction": "long"}})
        self.assertEqual(1, len(rows))
        hyp = rows[0]
        specs, _ = pp._candidate_events(hyp, matrix, max_specs=8)
        self.assertEqual(1, len(specs))
        self.assertTrue(specs[0].get("llm_pinned_quantiles"))
        event_id = specs[0]["event_id"]
        self.assertIn("ema_diff", event_id)
        self.assertIn("ema_ratio", event_id)
        self.assertIn("adx_14", event_id)

    def test_legacy_two_factor_seed_not_truncated_when_entry_rule_has_three(self):
        seed = {
            "seed_id": "S2",
            "entry_rule": (
                "trend_bias_50_200 > 0.7 AND close_z_20 > 0.5 AND ret_3 > 0"
            ),
            "factor_hints": [
                "trend_bias_50_200", "close_z_20", "ret_3", "atr_pct_14",
            ],
            "factor_side_constraints": {
                "trend_bias_50_200": "high",
                "close_z_20": "high",
                "ret_3": "high",
            },
            "parameters": {
                "trend_threshold": [0.5, 0.8],
                "close_z_threshold": [0, 1],
                "ret_3_threshold": [0, 0.02],
            },
        }
        fields = _llm_seed_probe_fields(seed)
        self.assertEqual(3, len(fields["required_factor_intersection"]))
        self.assertIn("ret_3", fields["required_factor_intersection"])


if __name__ == "__main__":
    unittest.main()
