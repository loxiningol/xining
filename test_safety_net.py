# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import safety_net as sn


class SafetyNetSnapshotTest(unittest.TestCase):
    def test_empty_state_shows_full_quotas(self):
        out = sn.status_snapshot(now_ts=1787241600.0, state={})
        self.assertTrue(out["ok"])
        self.assertEqual("安全网", out["module"])
        self.assertEqual(2, out["manual_open"]["limit"])
        self.assertEqual(2, out["manual_open"]["remaining"])
        self.assertEqual(3, out["hitch_extra"]["limit"])
        self.assertEqual(3, out["hitch_extra"]["remaining"])
        self.assertFalse(out["hitch_extra"]["usable"])
        self.assertFalse(out["locked"])
        self.assertIn("手动开仓限额", out["manual_open"]["label"])
        self.assertIn("顺风车开仓限额", out["hitch_extra"]["label"])
        self.assertIn("超过75%", out["rules"]["manual_over_force_close"])
        self.assertIn("不计入危险信号", out["rules"]["manual_size_warn"])

    def test_same_day_counts_and_hitch_usable_after_regular_full(self):
        out = sn.status_snapshot(now_ts=1787300000.0, state={
            "beijing_date": sn.beijing_date(1787300000.0),
            "manual_open_count": 2,
            "hitch_extra_count": 1,
            "danger_count": 1,
            "lock_to_funding": False,
            "updated_at": "2026-08-21 18:00:00",
        })
        self.assertEqual(0, out["manual_open"]["remaining"])
        self.assertEqual(2, out["hitch_extra"]["remaining"])
        self.assertTrue(out["hitch_extra"]["usable"])
        self.assertEqual(1, out["danger"]["used"])
        self.assertIn("顺风车 1/3", out["summary"])

    def test_stale_date_resets_display_without_touching_file(self):
        out = sn.status_snapshot(now_ts=1787300000.0, state={
            "beijing_date": "1999-01-01",
            "manual_open_count": 3,
            "hitch_extra_count": 3,
            "lock_to_funding": True,
        })
        self.assertEqual(0, out["manual_open"]["used"])
        self.assertEqual(0, out["hitch_extra"]["used"])
        self.assertFalse(out["locked"])

    def test_reads_state_file_from_vector_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            auto = Path(tmp) / "auto_trade"
            auto.mkdir()
            today = sn.beijing_date(1787300000.0)
            (auto / "force_protect_state.json").write_text(
                '{"beijing_date":"%s","manual_open_count":2,"hitch_extra_count":0}' % today,
                encoding="utf-8",
            )
            with mock.patch.object(sn, "STATE_PATH", auto / "force_protect_state.json"):
                out = sn.status_snapshot(now_ts=1787300000.0)
            self.assertEqual(2, out["manual_open"]["used"])
            self.assertEqual(0, out["manual_open"]["remaining"])
            self.assertEqual(3, out["hitch_extra"]["remaining"])


if __name__ == "__main__":
    unittest.main()
