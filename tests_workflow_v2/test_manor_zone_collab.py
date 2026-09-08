# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from dual_engine_workflow_v2 import manor_zone_collab as mz


class ManorZoneCollabTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="manor_zone_")
        self._ledger = Path(self._tmp) / "manor_zone_ledger.json"
        os.environ["MANOR_ZONE_LEDGER_PATH"] = str(self._ledger)
        os.environ["VECTOR_ROOT"] = self._tmp
        mz.clear_auto_cache()

    def tearDown(self):
        os.environ.pop("MANOR_ZONE_LEDGER_PATH", None)

    def test_catalog_has_four_zones_and_manufacture_rename(self):
        cat = mz.zone_catalog()
        self.assertTrue(cat["ok"])
        ids = [z["id"] for z in cat["zones"]]
        self.assertEqual(["live", "manufacture", "archive", "watch"], ids)
        labels = {z["id"]: z["label_zh"] for z in cat["zones"]}
        self.assertEqual("策略制造区", labels["manufacture"])
        self.assertEqual("观风区", labels["watch"])
        self.assertEqual("实盘工作区", labels["live"])
        man = [z for z in cat["zones"] if z["id"] == "manufacture"][0]
        mod_ids = [m["id"] for m in man["modules"]]
        self.assertIn("greenhouse", mod_ids)
        self.assertIn("windmill", mod_ids)
        self.assertIn("invent", mod_ids)
        self.assertIn("furnace_retired", mod_ids)
        self.assertNotIn("furnace_a", mod_ids)
        self.assertNotIn("furnace_b", mod_ids)

    def test_alchemy_alias_maps_to_manufacture(self):
        self.assertEqual("manufacture", mz._normalize_zone_id("alchemy"))
        self.assertEqual("live", mz._normalize_zone_id("craft"))

    def test_append_and_status(self):
        out = mz.append_event(
            "manufacture",
            kind="deploy",
            status="done",
            title_zh="P0 契约部署",
            summary_zh="落地 manor_zone_collab",
            actor="cursor",
            git_sha="abc123",
            doc_paths=["docs/PARALLEL_CREATE_HUBS.md"],
        )
        self.assertTrue(out["ok"], out)
        st = mz.zone_status("manufacture", include_demo=False)
        self.assertTrue(st["ok"])
        self.assertEqual(1, len(st["changes"]))
        self.assertEqual("P0 契约部署", st["changes"][0]["title_zh"])
        self.assertEqual("cursor", st["changes"][0]["actor"])
        self.assertEqual([], st["processes"])

    def test_running_process_and_upsert(self):
        first = mz.append_event(
            "manufacture",
            kind="experiment",
            status="running",
            title_zh="创造模块隔离试验",
            summary_zh="hub 边界试验",
            actor="cursor",
            event_id="trial_1",
        )
        self.assertTrue(first["ok"])
        st = mz.zone_status("manufacture", include_demo=False)
        self.assertEqual(1, len(st["processes"]))
        self.assertEqual("active", st["health"])
        done = mz.append_event(
            "manufacture",
            kind="experiment",
            status="done",
            title_zh="创造模块隔离试验",
            summary_zh="试验结束",
            actor="cursor",
            event_id="trial_1",
        )
        self.assertTrue(done["ok"])
        self.assertTrue(done["replaced"])
        st2 = mz.zone_status("manufacture", include_demo=False)
        self.assertEqual(0, len(st2["processes"]))
        self.assertEqual(1, len(st2["changes"]))

    def test_demo_process_when_empty(self):
        st = mz.zone_status("manufacture", include_demo=True)
        self.assertTrue(st["processes"])
        self.assertTrue(st["processes"][0].get("demo"))
        self.assertIn("单一创造", st["processes"][0]["title_zh"])

    def test_overview(self):
        ov = mz.zones_overview(include_demo=True)
        self.assertTrue(ov["ok"])
        self.assertEqual(4, len(ov["zones"]))
        man = [z for z in ov["zones"] if z["id"] == "manufacture"][0]
        self.assertGreaterEqual(man["process_count"], 1)

    def test_auto_manufacture_from_thin_hub(self):
        class FakeBoard(object):
            @staticmethod
            def status(slim=True):
                return {
                    "ok": True,
                    "pipelines": [
                        {
                            "lane_no": 5, "enabled": True, "working": True,
                            "public_progress": "研究中", "symbol": "AMD-USDT-SWAP",
                            "hub": "hub-b", "last_at": "2026-09-08 12:00:00",
                        },
                        {
                            "lane_no": 1, "enabled": True, "working": False,
                            "public_progress": "未达到机器基础门槛：优化次数用尽",
                            "symbol": "BNB-USDT-SWAP", "hub": "hub-a",
                        },
                    ],
                }

        import dual_engine_workflow_v2.thin_hub_lane_board as thb
        old = thb.status
        thb.status = FakeBoard.status
        mz.clear_auto_cache()
        try:
            rows = mz.auto_processes("manufacture")
        finally:
            thb.status = old
            mz.clear_auto_cache()
        self.assertEqual(1, len(rows))
        self.assertEqual("auto", rows[0].get("source"))
        self.assertIn("研究中", rows[0]["title_zh"])
        self.assertIn("AMD", rows[0]["summary_zh"])
        self.assertNotIn("BNB", rows[0]["summary_zh"])

    def test_auto_watch_empty_without_positions(self):
        class FakeHt(object):
            @staticmethod
            def trigger_board(persist=False):
                return {"ok": True, "positions": [], "alert_count": 0}

        import dual_engine_workflow_v2.manor_zone_collab as mod
        # patch import path used inside function via sys.modules style
        import types
        fake = types.ModuleType("auto_trade_hold_tribunal")
        fake.trigger_board = FakeHt.trigger_board
        sys.modules["auto_trade_hold_tribunal"] = fake
        try:
            rows = mz.auto_processes("watch")
        finally:
            sys.modules.pop("auto_trade_hold_tribunal", None)
        self.assertEqual([], rows)

    def test_zone_status_merges_auto(self):
        class FakeBoard(object):
            @staticmethod
            def status(slim=True):
                return {
                    "ok": True,
                    "pipelines": [{
                        "lane_no": 8, "enabled": True, "working": True,
                        "public_progress": "研究中", "symbol": "UNI-USDT-SWAP",
                        "hub": "hub-b", "last_at": "t",
                    }],
                }

        import dual_engine_workflow_v2.thin_hub_lane_board as thb
        old = thb.status
        thb.status = FakeBoard.status
        mz.clear_auto_cache()
        try:
            st = mz.zone_status("manufacture", include_demo=False, include_auto=True)
        finally:
            thb.status = old
            mz.clear_auto_cache()
        self.assertTrue(st["ok"])
        self.assertGreaterEqual(st.get("auto_process_count") or 0, 1)
        titles = [p.get("title_zh") for p in st.get("processes") or []]
        self.assertTrue(any("研究中" in (t or "") for t in titles))

    def test_watch_building_is_market_not_strategies(self):
        cat = mz.zone_catalog()
        watch = [z for z in cat["zones"] if z["id"] == "watch"][0]
        mods = watch["modules"]
        self.assertEqual(1, len(mods))
        self.assertEqual("market", mods[0]["manor_building"])
        self.assertEqual("#atmSectionHoldAssist", mods[0]["anchor"])
        live = [z for z in cat["zones"] if z["id"] == "live"][0]
        bids = {m["manor_building"] for m in live["modules"]}
        self.assertIn("bakery", bids)
        self.assertIn("pos_greenhouse", bids)
        self.assertNotIn("strategies", bids)
        arch = [z for z in cat["zones"] if z["id"] == "archive"][0]
        self.assertEqual("strategies", arch["modules"][0]["manor_building"])

    def test_conflict_blocks_other_running(self):
        mz.append_event(
            "watch",
            kind="experiment",
            status="running",
            title_zh="观风试验",
            actor="cursor-hub-a",
            event_id="watch_trial_a",
        )
        blocked = mz.conflict_check("watch", actor="cursor-hub-b", intent="edit")
        self.assertTrue(blocked.get("conflict"))
        self.assertFalse(blocked.get("ok"))
        self.assertTrue(blocked.get("blockers"))
        soft = mz.conflict_check("watch", actor="cursor-hub-a", intent="edit")
        self.assertFalse(soft.get("conflict"))
        self.assertTrue(soft.get("ok"))
        self.assertTrue(soft.get("warnings"))


if __name__ == "__main__":
    unittest.main()
