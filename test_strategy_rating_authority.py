# -*- coding: utf-8 -*-
"""Regression tests for the single authoritative live grade projection."""
from __future__ import print_function

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class StrategyRatingAuthorityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.auto = self.root / "auto_trade"
        self.auto.mkdir(parents=True)
        (self.root / "strategy_configs").mkdir(parents=True)
        self.env = mock.patch.dict(
            os.environ, {"VECTOR_ROOT": str(self.root)}, clear=False)
        self.env.start()
        import auto_trade_strategy_rating as rating
        importlib.reload(rating)
        self.rating = rating

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_runtime_grade_overrides_statistical_performance_grade(self):
        key = "live_b"
        aid = "NG-USDT-SWAP|5m|%s" % key
        (self.auto / "formal_daemon_config_ng_5m.json").write_text(
            json.dumps({
                "enabled": True, "allow_auto_open": True,
                "symbol": "NG-USDT-SWAP", "timeframe": "5m",
                "strategy_keys": [key],
            }), encoding="utf-8")
        (self.auto / "strategy_runtime_controls.json").write_text(
            json.dumps({"assignments": {aid: {
                "lifecycle_grade": "B", "max_position_ratio": 0.30,
                "grade_managed_by": "trade_outcome_state_machine",
            }}}), encoding="utf-8")
        with mock.patch(
                "auto_trade_human_confirm_pipeline.monitor_live_grades",
                return_value={"ok": True, "actions": []}), mock.patch.object(
                    self.rating, "_grade",
                    return_value=("C", "statistical evidence is weak")):
            out = self.rating.refresh()
        row = out["ratings_by_id"][aid]
        self.assertEqual(row["grade"], "B")
        self.assertEqual(row["position_ratio"], 0.30)
        self.assertEqual(row["performance_grade"], "C")
        self.assertEqual(row["grade_source"], "strategy_runtime_controls")
        self.assertEqual(out["grade_ratios"]["C"], 0.10)


if __name__ == "__main__":
    unittest.main()
