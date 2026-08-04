# -*- coding: utf-8 -*-
"""Unit tests for strategy dynamic optimizer (no live market / AI)."""
from __future__ import print_function

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class DynamicOptimizerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "auto_trade").mkdir(parents=True)
        (self.root / "strategy_configs").mkdir(parents=True)
        self._patches = [
            mock.patch.dict(os.environ, {"VECTOR_ROOT": str(self.root)},
                            clear=False),
        ]
        for p in self._patches:
            p.start()
        import importlib
        import auto_trade_strategy_dynamic_optimizer as opt
        importlib.reload(opt)
        self.opt = opt
        # seed controls + dsl
        dsl = {
            "schema": "qiyu_ai_dsl_strategies_v1",
            "strategies": [{
                "schema": "qiyu_strategy_dsl_v1",
                "key": "demo_long",
                "name": "演示多",
                "direction": "long",
                "timeframe": "15m",
                "supported_instruments": ["BTC-USDT-SWAP"],
                "entry": {"all": [
                    {"id": "rsi", "left": {"feature": "rsi14"},
                     "op": "lt", "right": {"value": 30}},
                    {"id": "z", "left": {"feature": "z20"},
                     "op": "gt", "right": {"value": -1.0}},
                ]},
                "exit": {"any": [
                    {"id": "rsi_ex", "left": {"feature": "rsi14"},
                     "op": "gt", "right": {"value": 55}},
                ]},
                "max_hold_bars": 24,
                "live_enabled": True,
                "auto_trade_eligible": True,
            }],
        }
        (self.root / "strategy_configs" / "ai_dsl_strategies.json").write_text(
            json.dumps(dsl), encoding="utf-8")
        (self.root / "strategy_configs" / "experimental_strategies.json").write_text(
            json.dumps({"strategies": []}), encoding="utf-8")
        controls = {
            "assignments": {
                "BTC-USDT-SWAP|15m|demo_long": {
                    "strategy_key": "demo_long",
                    "strategy_name": "演示多",
                    "lifecycle_grade": "B",
                    "max_position_ratio": 0.30,
                    "human_confirmed": True,
                    "human_confirm_pipeline": True,
                    "pause_new_entries": False,
                }
            }
        }
        (self.root / "auto_trade" / "strategy_runtime_controls.json").write_text(
            json.dumps(controls), encoding="utf-8")

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def test_list_live_targets_b_plus(self):
        targets = self.opt.list_live_targets()
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["strategy_key"], "demo_long")

    def test_passes_screen_gates(self):
        base = {"trades": 20, "win_rate": 50.0, "total_return_pct": 10.0,
                "max_drawdown": 0.20}
        good = {"trades": 20, "win_rate": 56.0, "total_return_pct": 12.0,
                "max_drawdown": 0.18}
        ok, reason = self.opt.passes_screen(base, good)
        self.assertTrue(ok, reason)
        bad_wr = dict(good, win_rate=52.0)
        ok2, reason2 = self.opt.passes_screen(base, bad_wr)
        self.assertFalse(ok2)
        self.assertEqual(reason2, "wr_lift_lt_5pp")
        bad_dd = dict(good, max_drawdown=0.25)
        ok3, reason3 = self.opt.passes_screen(base, bad_dd)
        self.assertFalse(ok3)
        self.assertEqual(reason3, "dd_worse")

    def test_param_multipliers_cover_pm30(self):
        ms = self.opt._param_multipliers()
        self.assertIn(0.7, ms)
        self.assertIn(1.3, ms)
        self.assertNotIn(1.0, ms)

    def test_reject_cooldown(self):
        prop = {
            "id": "opt_demo_abcd1234",
            "status": "pending",
            "strategy_key": "demo_long",
            "strategy_name": "演示多",
            "fingerprint": "fp_test_1",
            "opt_type": "参数调整",
            "explanation": "test",
            "change": {"field": "x", "from": 1, "to": 2},
            "kind": "dsl",
            "definition": {"key": "demo_long"},
        }
        self.opt._atomic(self.opt.PENDING_PATH, {"items": [prop]})
        with mock.patch.object(self.opt, "_wx", return_value={"ok": True}):
            out = self.opt.reject("opt_demo_abcd1234", reason="nope")
        self.assertTrue(out.get("ok"))
        self.assertTrue(self.opt._rejected_blocked("fp_test_1"))

    def test_confirm_apply_keeps_trade_windows(self):
        # prepare pending with definition change
        loaded = self.opt.load_definition("demo_long")
        definition = loaded["definition"]
        definition["entry"]["all"][0]["right"]["value"] = 25
        prop = {
            "id": "opt_demo_apply01",
            "status": "pending",
            "assignment_id": "BTC-USDT-SWAP|15m|demo_long",
            "strategy_key": "demo_long",
            "strategy_name": "演示多",
            "symbol": "BTC-USDT-SWAP",
            "timeframe": "15m",
            "kind": "dsl",
            "opt_type": "参数调整",
            "explanation": "rsi 30→25",
            "fingerprint": "fp_apply",
            "change": {"field": "entry.rsi14.value", "from": 30, "to": 25},
            "definition": definition,
            "baseline": {},
            "candidate_metrics": {},
        }
        self.opt._atomic(self.opt.PENDING_PATH, {"items": [prop]})
        # seed windows that must survive apply
        controls = self.opt._read(self.opt.CONTROL_PATH, {})
        aid = "BTC-USDT-SWAP|15m|demo_long"
        controls["assignments"][aid]["promote_window_closed"] = ["a|1", "b|2"]
        controls["assignments"][aid]["stop_window_closed"] = ["c|3"]
        controls["assignments"][aid]["lifecycle_grade"] = "B"
        self.opt._atomic(self.opt.CONTROL_PATH, controls)

        with mock.patch.object(self.opt, "_wx", return_value={"ok": True}):
            with mock.patch("subprocess.call", return_value=0):
                out = self.opt.confirm("opt_demo_apply01")
        self.assertTrue(out.get("ok"), out)
        # dsl updated
        dsl = self.opt._read(self.opt.DSL_CONFIG_PATH, {})
        row = [s for s in dsl["strategies"] if s["key"] == "demo_long"][0]
        self.assertEqual(row["entry"]["all"][0]["right"]["value"], 25)
        # windows preserved
        controls2 = self.opt._read(self.opt.CONTROL_PATH, {})
        row2 = controls2["assignments"][aid]
        self.assertEqual(row2["promote_window_closed"], ["a|1", "b|2"])
        self.assertEqual(row2["stop_window_closed"], ["c|3"])
        self.assertEqual(row2["lifecycle_grade"], "B")
        self.assertTrue(row2.get("optimizer_last_apply_at"))

    def test_enqueue_proposal_wx_kind(self):
        target = self.opt.list_live_targets()[0]
        base = {"trades": 20, "win_rate": 50.0, "total_return_pct": 10.0,
                "max_drawdown": 0.2}
        metrics = {"trades": 22, "win_rate": 58.0, "total_return_pct": 13.0,
                   "max_drawdown": 0.15}
        variant = {
            "kind": "dsl",
            "definition": self.opt.load_definition("demo_long")["definition"],
            "opt_type": "参数调整",
            "change": {"field": "x", "from": 1, "to": 1.1},
            "explanation": "演示改动",
            "fingerprint": "fp_enqueue",
        }
        kinds = []

        def _capture(text, kind="x", meta=None):
            kinds.append(kind)
            return {"ok": True}

        with mock.patch.object(self.opt, "_wx", side_effect=_capture):
            out = self.opt.enqueue_proposal(
                target, "dsl", base, variant, metrics, trigger="test")
        self.assertTrue(out.get("pushed"))
        self.assertIn("strategy_optimizer_propose", kinds)


if __name__ == "__main__":
    unittest.main()
