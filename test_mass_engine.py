# -*- coding: utf-8 -*-
"""Unit tests for mass production engine (composer + screen folds)."""
from __future__ import print_function

import os
import tempfile
import unittest
from pathlib import Path


class ComposerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["VECTOR_ROOT"] = self.tmp
        Path(self.tmp, "auto_trade").mkdir(parents=True, exist_ok=True)
        Path(self.tmp, "strategy_configs").mkdir(parents=True, exist_ok=True)
        import importlib
        import auto_trade_mass_composer as composer
        importlib.reload(composer)
        self.composer = composer

    def test_compose_one_valid(self):
        import random
        rng = random.Random(42)
        cand, err = self.composer.compose_one(
            rng=rng, forced_symbol="BTC-USDT-SWAP", forced_tf="15m")
        self.assertIsNone(err, msg=err)
        self.assertEqual(cand["status"], "draft")
        dsl = cand["dsl"]
        self.assertIn("invalidation",
                      str(dsl.get("exit")).lower())
        self.assertGreaterEqual(len((dsl.get("entry") or {}).get("all") or []), 2)
        self.assertEqual(cand["stop_loss_pct"], 0.009)

    def test_batch_creates(self):
        out = self.composer.generate_batch(n=5, seed=1, append=False)
        self.assertGreaterEqual(out["n_created"], 3)
        path = Path(self.tmp, "auto_trade", "mass_candidates.json")
        self.assertTrue(path.exists())


class ScreenerProbationTests(unittest.TestCase):
    def test_policy_hungry_when_empty(self):
        import tempfile
        import os
        from pathlib import Path
        import importlib
        tmp = tempfile.mkdtemp()
        os.environ["VECTOR_ROOT"] = tmp
        Path(tmp, "auto_trade").mkdir(parents=True, exist_ok=True)
        import auto_trade_mass_screener as s
        importlib.reload(s)
        policy = s.resolve_screen_policy()
        self.assertEqual(policy["friction"], "observed_base")
        self.assertFalse(policy["require_folds"])
        self.assertFalse(policy["require_significance"])
        self.assertGreaterEqual(policy["min_trades"], 3)
        self.assertLessEqual(policy["max_dd"], 0.55)

    def test_max_drawdown(self):
        import auto_trade_mass_screener as s
        # +10%, -20% from peak → dd = 0.2/1.1
        dd = s._max_drawdown([0.10, -0.20])
        self.assertGreater(dd, 0.15)
        self.assertLess(dd, 0.25)

    def test_fold_means_all_positive(self):
        import auto_trade_mass_screener as s
        trades = [{"entry_index": i, "pnl_ratio": 0.01} for i in range(15)]
        ok, means, meta = s._fold_means(trades, n_folds=5)
        self.assertTrue(ok)
        self.assertEqual(len(means), 5)

    def test_fold_means_rejects_negative(self):
        import auto_trade_mass_screener as s
        trades = []
        for i in range(15):
            pnl = -0.05 if (i % 5) == 2 else 0.02
            trades.append({"entry_index": i, "pnl_ratio": pnl})
        ok, means, meta = s._fold_means(trades, n_folds=5)
        self.assertFalse(ok)


class ProbeCapTests(unittest.TestCase):
    def test_grade_ratios(self):
        import auto_trade_mass_probe as p
        self.assertEqual(p.GRADE_RATIO["E"], 0.02)
        self.assertEqual(p.GRADE_RATIO["D"], 0.065)
        self.assertEqual(p.GRADE_RATIO["C"], 0.15)
        self.assertGreaterEqual(p.MAX_CONCURRENT_E, 10)


if __name__ == "__main__":
    unittest.main()
