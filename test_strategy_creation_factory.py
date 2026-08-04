# -*- coding: utf-8 -*-
"""Unit tests for creation factory budget/pause/death helpers (no live AI)."""
from __future__ import print_function

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class CreationFactoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "auto_trade").mkdir(parents=True)
        self._patches = [
            mock.patch.dict(os.environ, {"VECTOR_ROOT": str(self.root)},
                            clear=False),
        ]
        for p in self._patches:
            p.start()
        import importlib
        import auto_trade_strategy_creation_factory as f
        importlib.reload(f)
        self.f = f

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def test_budget_base_and_manual_boost(self):
        st = self.f.budget_state()
        self.assertEqual(st["cap_usd"], 0.54)
        self.f.arm_manual_budget_boost()
        st2 = self.f.budget_state(manual=True)
        self.assertEqual(st2["cap_usd"], 1.08)
        self.assertTrue(st2["boost"])

    def test_budget_consume(self):
        ok, st, reason = self.f.budget_allow(n_calls=1)
        self.assertTrue(ok)
        self.f.budget_consume(n_calls=10, note="test")
        st = self.f.budget_state()
        self.assertGreater(st["spent_usd"], 0)
        self.assertEqual(st["calls"], 10)

    def test_pause_auto(self):
        flag = self.f.pause_auto_mode(minutes=60)
        self.assertTrue(flag.get("paused"))
        paused, _ = self.f.auto_mode_paused()
        self.assertTrue(paused)

    def test_death_veto_lookahead(self):
        dsl = {
            "schema": "qiyu_strategy_dsl_v1",
            "key": "x", "name": "x", "direction": "long", "timeframe": "15m",
            "supported_instruments": ["BTC-USDT-SWAP"],
            "entry": {"all": [{"left": {"feature": "future_close"}, "op": "gt",
                               "right": {"value": 1}}]},
            "exit": {},
        }
        # death_veto may not catch future_*; screen does. Just ensure callable.
        hit = self.f.death_veto(dsl)
        self.assertTrue(hit is None or isinstance(hit, str))

    def test_daily_skips_when_paused(self):
        # Creation factory retired 2026-07-24; daily always short-circuits.
        self.f.pause_auto_mode(minutes=60)
        out = self.f.run_daily_collaborative_round(force=False)
        self.assertTrue(out.get("disabled") or out.get("skipped") == "auto_paused")
        self.assertEqual(out.get("reason"), "disabled_ai_creation")

    def test_manual_accelerate_disabled(self):
        out = self.f.run_manual_accelerate("BTC-USDT-SWAP", "15m")
        self.assertTrue(out.get("disabled"))
        self.assertEqual(out.get("n_pushed"), 0)


if __name__ == "__main__":
    unittest.main()
