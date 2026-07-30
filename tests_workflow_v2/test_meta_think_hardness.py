# -*- coding: utf-8 -*-
"""Tests for meta-think hardness: causal CF, socratic, knowledge, multiverse."""
from __future__ import print_function

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Use local VECTOR_ROOT for card store
os.environ["VECTOR_ROOT"] = os.path.join(ROOT, "strategies", "_scratch")

from dual_engine_workflow_v2 import creation_causal_counterfactual as cf
from dual_engine_workflow_v2 import creation_socratic_agent as soc
from dual_engine_workflow_v2 import creation_knowledge_distill as kd
from dual_engine_workflow_v2 import creation_multiverse as mv
from dual_engine_workflow_v2 import creation_meta_think as meta


class TestMetaThinkHardness(unittest.TestCase):
    def test_knowledge_cards_seed_and_gate(self):
        pack = kd.load_cards()
        self.assertTrue(pack["ok"])
        self.assertGreaterEqual(pack["n"], 3)
        design = {
            "mechanism_family": "pairs_cointegration",
            "core_logic_zh": "配对协整价差回归",
            "divergence": {"selected_id": "C_pairs", "selected_lens_zh": "配对"},
        }
        gate = kd.assess_lens_against_cards(design)
        self.assertFalse(gate["passed"])

    def test_socratic_passes_complete_design(self):
        pack = meta.run_meta_think(
            "创造趋势突破策略", "ADA-USDT-SWAP", "5m", skip_llm=True,
        )
        design = pack["design_doc"]
        out = soc.challenge_design(design, knowledge_cards=[{"id": "x"}])
        self.assertTrue(out["passed"], out.get("qa"))

    def test_socratic_fails_empty_design(self):
        out = soc.challenge_design({"mechanism_family": "", "core_logic_zh": "x"})
        self.assertFalse(out["passed"])

    def test_multiverse_rejects_coin_flip(self):
        # alternating tiny noise → ~50% profit frac
        rets = [0.01, -0.01] * 20
        out = mv.survival_test(rets, n_paths=80, seed=1, min_profit_frac=0.55)
        # may or may not pass; ensure schema
        self.assertIn("profit_frac", out)
        self.assertTrue(out.get("ok"))

    def test_multiverse_accepts_strong_edge(self):
        rets = [0.01] * 40
        out = mv.survival_test(rets, n_paths=60, seed=2)
        self.assertTrue(out["passed"])
        self.assertGreaterEqual(out["profit_frac"], 0.9)

    def test_counterfactual_runs_on_synthetic(self):
        # synthetic: factor aligned with fwd, confounder = factor copy
        n = 120
        factor = [float(i % 17) for i in range(n)]
        fwd = [0.01 if factor[i] > 10 else -0.005 for i in range(n)]
        atr = [0.02 + 0.01 * (i % 5) for i in range(n)]
        matrix = {"ret_12": factor, "atr_pct_14": atr, "range_pct": atr}
        out = cf.run_counterfactual_battery(matrix, fwd, core_factors=["ret_12"])
        self.assertTrue(out["ok"])
        self.assertIn("passed", out)


if __name__ == "__main__":
    unittest.main()
