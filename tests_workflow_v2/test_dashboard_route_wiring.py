# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = (ROOT / "web_server.py").read_text(encoding="utf-8")


class DashboardRouteWiringTest(unittest.TestCase):
    def test_missing_board_paths_are_wired(self):
        for path in (
            '/api/vector/auto_trade/live_positions',
            '/api/vector/auto_trade/live_positions/close',
            '/api/vector/auto_trade/daily_summary',
            '/api/vector/auto_trade/weekly_summary',
            '/api/safety_net',
            '/api/forecast/latest',
            '/api/vector/auto_trade/status',
        ):
            self.assertIn(path, WEB, "missing handler for %s" % path)

    def test_status_cache_does_not_reenter_lock_to_seed_disk(self):
        start = WEB.find("def _vector_status_payload_cached")
        self.assertGreater(start, 0)
        chunk = WEB[start:start + 3500]
        self.assertNotIn(
            "_vector_seed_status_cache_from_disk()",
            chunk,
            "calling seed-from-disk while holding the cache lock deadlocks qiyu-web",
        )
        self.assertIn("_vector_disk_status_fallback()", chunk)


if __name__ == "__main__":
    unittest.main()
