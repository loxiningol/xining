# -*- coding: utf-8 -*-
"""Unit checks for adaptive-engine modules (breath / corridor / arbitration)."""
from __future__ import print_function

import json
import os
import tempfile
import unittest
from pathlib import Path


class BreathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["VECTOR_ROOT"] = self.tmp
        Path(self.tmp, "auto_trade").mkdir(parents=True, exist_ok=True)
        import importlib
        import auto_trade_strategy_breath as breath
        importlib.reload(breath)
        self.breath = breath

    def test_floors_ceilings(self):
        outs_lo = self.breath.score_to_outputs(0.0)
        outs_hi = self.breath.score_to_outputs(1.0)
        self.assertEqual(outs_lo["soft_window_ms"], self.breath.SOFT_WINDOW_MS_FLOOR)
        self.assertEqual(outs_hi["soft_window_ms"], self.breath.SOFT_WINDOW_MS_CEILING)
        self.assertEqual(outs_lo["missing_leaf_tolerance"], 1)
        self.assertEqual(outs_hi["missing_leaf_tolerance"], 2)

    def test_run_once_persists(self):
        state = self.breath.run_once(inputs={
            "primitive_change_rate": 0.8,
            "volatility_percentile": 0.9,
            "volume_percentile": 0.8,
            "fire_score": 0.2,
            "pnl_score": 0.7,
        }, force=True)
        self.assertIn("outputs", state)
        self.assertGreaterEqual(state["outputs"]["soft_window_ms"],
                                self.breath.SOFT_WINDOW_MS_FLOOR)
        self.assertTrue(Path(self.breath.STATE_PATH).exists())


class CorridorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["VECTOR_ROOT"] = self.tmp
        Path(self.tmp, "auto_trade").mkdir(parents=True, exist_ok=True)
        import importlib
        import auto_trade_strategy_gene_corridor as corridor
        importlib.reload(corridor)
        self.corridor = corridor

    def test_logic_rejects_cci_tp_short(self):
        draft = {
            "dsl": {
                "schema": "qiyu_strategy_dsl_v1",
                "key": "test_short_cci_tp",
                "name": "test",
                "direction": "short",
                "timeframe": "15m",
                "supported_instruments": ["BTC-USDT-SWAP"],
                "max_hold_bars": 16,
                "entry": {"all": [
                    {"id": "e1", "left": {"feature": "h1_slope4"}, "op": "lt",
                     "right": {"value": -0.0005}},
                    {"id": "e2", "left": {"feature": "z20"}, "op": "lt",
                     "right": {"value": -1.5}},
                    {"id": "e3", "left": {"feature": "close"},
                     "op": "cross_below", "right": {"feature": "ema17"}},
                ]},
                "exit": {"any": [
                    {"id": "x1", "left": {"feature": "cci"},
                     "op": "cross_below", "right": {"value": -100.0},
                     "role": "take_profit"},
                    {"id": "x2", "left": {"feature": "close"},
                     "op": "cross_above", "right": {"feature": "ema53"},
                     "role": "invalidation"},
                ]},
            }
        }
        item = self.corridor.enqueue_draft(draft, source="test")
        status, reason = self.corridor.logic_screen(item)
        self.assertEqual(status, "repair")
        self.assertIn("cci", reason)
        variants = self.corridor.build_repair_variants(
            draft["dsl"], reason, max_n=3)
        self.assertGreaterEqual(len(variants), 1)
        for v in variants:
            blob = json.dumps(v["dsl"].get("exit"), ensure_ascii=False)
            self.assertIn("take_profit", blob)
            self.assertNotIn("-100", blob)
        # starvation on surgical child → forward E probe, not archive
        child = {
            "draft_hash": "testhash",
            "source": "repair:tp_momentum_contradiction_cci:cci_reclaim_m50_tp",
            "stage": "stats_screen",
            "draft": {
                "dsl": variants[0]["dsl"],
                "origin": {"kind": "corridor_repair_micro_mutation"},
            },
            "metrics": {"stats": {
                "trades": 0, "n": 0, "mean_net_return_pct": -999.0,
            }},
            "history": [],
        }
        # stub promote to avoid filesystem/DB
        self.corridor.promote_corridor_item = lambda it: it
        out = self.corridor.advance_item(child)
        self.assertEqual(out.get("stage"), "probe_armed")
        self.assertEqual(out.get("grade"), "D")

    def test_grade_assign(self):
        g, r = self.corridor.assign_grade({
            "trades": 12, "mean_net_return_pct": 0.4,
            "win_rate": 60, "max_loss_streak": 1,
        })
        self.assertEqual(g, "C")
        self.assertAlmostEqual(r, 0.15)
        g2, r2 = self.corridor.assign_grade({
            "trades": 6, "mean_net_return_pct": 0.1,
            "win_rate": 50, "max_loss_streak": 3,
        })
        self.assertEqual(g2, "E")
        self.assertAlmostEqual(r2, 0.02)


class ArbitrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["VECTOR_ROOT"] = self.tmp
        Path(self.tmp, "auto_trade").mkdir(parents=True, exist_ok=True)
        import importlib
        import auto_trade_ai_arbitration as arb
        importlib.reload(arb)
        self.arb = arb

    def test_degraded_pair(self):
        seq = {
            "deepseek": {"ok": False, "decision": "REJECT"},
            "qwen": {"ok": True, "decision": "APPROVE"},
            "chatgpt": {"ok": True, "decision": "APPROVE"},
        }

        def call_fn(provider):
            return dict(seq[provider], provider=provider)

        # Force no successful role-swap fill by making filler also return same
        out = self.arb.arbitrate_reviews(call_fn)
        self.assertIn(out["mode"], ("degraded_pair", "unanimous", "human_hold", "split"))
        self.assertIn(out["effective_decision"], ("APPROVE", "REJECT", "HOLD"))

    def test_grade_penalty(self):
        self.assertEqual(self.arb.apply_grade_penalty("D", 1), "E")
        self.assertEqual(self.arb.apply_grade_penalty("C", 2), "E")


class SurvivalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["VECTOR_ROOT"] = self.tmp
        Path(self.tmp, "auto_trade").mkdir(parents=True, exist_ok=True)
        import importlib
        import auto_trade_capital_survival as surv
        importlib.reload(surv)
        self.surv = surv

    def test_breakeven_viable(self):
        report = self.surv.build_report(equity_usd=17.0)
        self.assertTrue(report["ok"])
        self.assertTrue(report["target_point"]["viable"])
        self.assertLess(report["profiles"]["thrifty"]["cost"]["total_usd"],
                        report["profiles"]["aggressive"]["cost"]["total_usd"])


if __name__ == "__main__":
    unittest.main()
