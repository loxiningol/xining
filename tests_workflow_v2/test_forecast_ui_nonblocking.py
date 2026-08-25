# -*- coding: utf-8 -*-
from __future__ import print_function

import inspect
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class ForecastUiNonblockingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qiyu_forecast_ui_")
        self.auto = Path(self.temp.name) / "auto_trade"
        self.auto.mkdir(parents=True, exist_ok=True)
        import auto_trade_forecast_closeout as closeout
        self.closeout = closeout
        self.patches = [
            mock.patch.object(closeout, "LATEST_PATH", self.auto / "system_forecast_latest.json"),
            mock.patch.object(closeout, "STALE_FLAG_PATH", self.auto / "forecast_stale_flag.json"),
            mock.patch.object(closeout, "STATE_PATH", self.auto / "system_forecast_state.json"),
        ]
        for p in self.patches:
            p.start()
        closeout._SCHEDULE_REFRESH_PENDING = False

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def _write_latest(self, **extra):
        report = {
            "generated_at": "2026-08-25 20:34:41",
            "strategy_pool_version": "pool_abc",
            "strategy_pool_mounted_count": 35,
            "active_strategy_count": 35,
            "stale": False,
            "portfolio_combo": {
                "missing": False,
                "weekly_geometric_growth": 0.273748,
                "weekly_geometric_growth_oos": 0.132062,
            },
            "overall": {"daily_opens_expected": 0.81, "weekly_opens_expected": 5.7},
        }
        report.update(extra)
        self.closeout.LATEST_PATH.write_text(json.dumps(report), encoding="utf-8")
        return report

    def test_get_latest_does_not_recompute_combo(self):
        src = inspect.getsource(self.closeout.load_latest_for_ui)
        self.assertNotIn("attach_to_report", src)
        self._write_latest()
        pool = {"strategy_pool_version": "pool_abc", "mounted_count": 35}
        attach = mock.Mock(side_effect=AssertionError("GET must not recompute combo"))
        with mock.patch("auto_trade_strategy_events.strategy_pool_version", return_value=pool), \
             mock.patch("auto_trade_portfolio_combo_metrics.attach_to_report", attach), \
             mock.patch.object(self.closeout, "schedule_lightweight_refresh") as sched:
            out = self.closeout.load_latest_for_ui()
        attach.assert_not_called()
        sched.assert_not_called()
        self.assertEqual(out["generated_at"], "2026-08-25 20:34:41")
        self.assertAlmostEqual(out["portfolio_combo"]["weekly_geometric_growth"], 0.273748)
        self.assertFalse(out["stale"])

    def test_stale_get_clears_frequency_and_schedules_background(self):
        self._write_latest()
        pool = {"strategy_pool_version": "pool_new", "mounted_count": 34}
        with mock.patch("auto_trade_strategy_events.strategy_pool_version", return_value=pool), \
             mock.patch.object(self.closeout, "schedule_lightweight_refresh",
                               return_value={"ok": True, "scheduled": True}) as sched:
            out = self.closeout.load_latest_for_ui()
        sched.assert_called_once()
        self.assertTrue(out["stale"])
        self.assertIsNone(out["overall"]["daily_opens_expected"])
        self.assertAlmostEqual(out["portfolio_combo"]["weekly_geometric_growth"], 0.273748)

    def test_schedule_does_not_run_refresh_inline(self):
        with mock.patch.object(self.closeout, "run_lightweight_statistical_refresh") as run, \
             mock.patch.object(self.closeout.threading, "Thread") as thread_cls:
            out = self.closeout.schedule_lightweight_refresh(reason="manual_ui")
        run.assert_not_called()
        thread_cls.assert_called_once()
        thread_cls.return_value.start.assert_called_once()
        self.assertTrue(out["scheduled"])
        self.closeout._SCHEDULE_REFRESH_PENDING = False

    def test_notify_pool_change_does_not_block_on_refresh(self):
        with mock.patch.object(self.closeout, "mark_forecast_stale", return_value={"stale": True}), \
             mock.patch.object(self.closeout, "schedule_lightweight_refresh",
                               return_value={"ok": True, "scheduled": True}) as sched, \
             mock.patch.object(self.closeout, "run_lightweight_statistical_refresh") as run:
            out = self.closeout.notify_pool_change("unmount", detail={"n": 1})
        sched.assert_called_once()
        run.assert_not_called()
        self.assertTrue(out["refresh"]["scheduled"])


if __name__ == "__main__":
    unittest.main()
