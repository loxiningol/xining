# -*- coding: utf-8 -*-
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import auto_trade_force_protect as fp

DIR_KEY = "kimi_6e2080e4b78b6362d5b42bc5"  # 方向型
ODDS_KEY = "kimi_c361a198338e20b159799485"  # 赔率型


def _pos(**kwargs):
    row = {
        "position_id": "ETH-USDT-SWAP|long|isolated",
        "inst_id": "ETH-USDT-SWAP",
        "symbol_label": "ETH",
        "side": "long",
        "direction_zh": "做多",
        "manual": False,
        "source": "formal_auto_trade",
        "strategy_key": "kimi_example",
        "strategy_title": "测试策略",
        "leverage": 20,
        "margin_usdt": 100.0,
        "notional_usd": 2000.0,
        "mgn_mode": "isolated",
    }
    row.update(kwargs)
    return row


class ForceProtectRulesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._auto = Path(self._tmp)
        self._state = self._auto / "force_protect_state.json"
        self._events = self._auto / "force_protect_events.jsonl"
        import auto_trade_session_clock as session_clock
        self._patches = [
            mock.patch.object(fp, "AUTO_DIR", self._auto),
            mock.patch.object(fp, "STATE_PATH", self._state),
            mock.patch.object(fp, "EVENT_PATH", self._events),
            mock.patch.object(fp, "_live_usdc_available", return_value=None),
            mock.patch.object(session_clock, "in_session", return_value=True),
            mock.patch.object(session_clock, "session_week_id", return_value="test-open-week"),
        ]
        for p in self._patches:
            p.start()
        seeded = fp._load_state()
        seeded["session_open_notified_week"] = "test-open-week"
        fp._persist_state(seeded)

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_manual_between_half_and_75_is_advisory_not_violation(self):
        pos = _pos(manual=True, source="manual_okx", strategy_key="", margin_usdt=501)
        self.assertEqual([], fp.violation_reasons(pos, 1000.0))
        self.assertEqual(["manual_size_warn"], fp.advisory_reasons(pos, 1000.0))

    def test_manual_exactly_half_is_allowed(self):
        pos = _pos(manual=True, source="manual_okx", strategy_key="", margin_usdt=500)
        self.assertEqual([], fp.violation_reasons(pos, 1000.0))
        self.assertEqual([], fp.advisory_reasons(pos, 1000.0))

    def test_manual_exactly_75_is_advisory_not_close(self):
        pos = _pos(manual=True, source="manual_okx", strategy_key="", margin_usdt=750)
        self.assertEqual([], fp.violation_reasons(pos, 1000.0))
        self.assertEqual(["manual_size_warn"], fp.advisory_reasons(pos, 1000.0))

    def test_manual_over_75_is_force_close(self):
        pos = _pos(manual=True, source="manual_okx", strategy_key="", margin_usdt=751)
        self.assertEqual(["manual_over_force_close"], fp.violation_reasons(pos, 1000.0))
        self.assertEqual([], fp.advisory_reasons(pos, 1000.0))

    def test_auto_macaron_half_is_allowed(self):
        pos = _pos(manual=False, margin_usdt=500, leverage=20)
        self.assertEqual([], fp.violation_reasons(pos, 1000.0))

    def test_makeup_source_with_strategy_key_is_not_manual(self):
        pos = _pos(
            manual=False,
            source="manual_makeup_occupancy_skip_20260823_2201",
            strategy_key=DIR_KEY,
            leverage=10,
            margin_usdt=80,
        )
        self.assertFalse(fp.is_manual_position(pos))

    def test_leverage_31_trips_even_for_auto(self):
        pos = _pos(manual=False, leverage=31, margin_usdt=100)
        self.assertEqual(["leverage_gt_30x"], fp.violation_reasons(pos, 1000.0))

    def test_leverage_30_is_allowed(self):
        pos = _pos(manual=True, source="manual_okx", leverage=30, margin_usdt=100)
        self.assertEqual([], fp.violation_reasons(pos, 1000.0))

    def test_manual_over_75_and_high_lev(self):
        pos = _pos(manual=True, source="manual_okx", strategy_key="", leverage=50, margin_usdt=800)
        self.assertEqual(
            ["leverage_gt_30x", "manual_over_force_close"],
            fp.violation_reasons(pos, 1000.0),
        )

    def test_value_falls_back_to_notional_over_leverage(self):
        pos = _pos(manual=True, source="manual_okx", strategy_key="",
                   margin_usdt=None, notional_usd=12000, leverage=10)
        self.assertAlmostEqual(0.6, fp.occupancy_ratio(pos, 2000.0))
        self.assertEqual([], fp.violation_reasons(pos, 2000.0))
        self.assertEqual(["manual_size_warn"], fp.advisory_reasons(pos, 2000.0))

    def test_message_starts_with_intervene_line(self):
        pos = _pos(manual=True, source="manual_okx", strategy_key="",
                   strategy_title="手动开仓", leverage=50, margin_usdt=800)
        msg = fp.build_intervene_message(pos, 1000.0, ["leverage_gt_30x", "manual_over_force_close"], True)
        self.assertTrue(msg.startswith("🚫 小鸡毛的急刹车："))
        self.assertNotIn("监测到危险操作", msg)
        self.assertIn("已无条件强制平仓", msg)
        self.assertIn("杠杆大于30x", msg)
        self.assertIn("手动单笔占用超过75%", msg)

    def test_lock_and_inbound_messages_are_plain_facts(self):
        lock = fp.build_lock_message(3, True, 35.52)
        self.assertEqual(
            lock,
            "今日风险行为已达3次：本日账上锁\n"
            "已全仓买入 USDC 现货\n"
            "拦截金额 35.52 USDT",
        )
        inbound = fp.build_inbound_block_message(46.34, True)
        self.assertEqual(
            inbound,
            "本日账户仍在上锁\n"
            "已再次全仓买入 USDC 现货\n"
            "拦截金额 46.34 USDT",
        )
        for msg in (lock, inbound):
            self.assertNotIn("庄园保鲜箱", msg)
            self.assertNotIn("嘟嘟", msg)
            self.assertNotIn("小管家", msg)

    def test_protect_once_closes_and_notifies(self):
        pos = _pos(manual=True, source="manual_okx", strategy_key="", margin_usdt=800, leverage=50)
        closed = []
        notes = []

        def close_fn(inst_id, side, reason=None, mgn_mode=None):
            closed.append((inst_id, side, reason, mgn_mode))
            return {"ok": True, "closed": True}

        def notify_fn(message, meta):
            notes.append((message, meta))
            return {"ok": True, "sent": True}

        listed = {"ok": True, "position_count": 1, "positions": [pos]}
        equity = {"ok": True, "equity_usdt": 1000.0}
        out = fp.protect_once(
            close_fn=close_fn, notify_fn=notify_fn, listed=listed, equity=equity,
            transfer_fn=lambda *a, **k: {"ok": True, "skipped": True},
            trading_fn=lambda: {"ok": True, "equity_usdt": 1000.0, "available_usdt": 200.0},
        )
        self.assertTrue(out["ok"])
        self.assertTrue(out["intervened"])
        self.assertEqual(1, len(closed))
        self.assertEqual("ETH-USDT-SWAP", closed[0][0])
        self.assertTrue(closed[0][2].startswith("force_protect:"))
        self.assertEqual(2, len(notes))
        self.assertTrue(notes[0][0].startswith("🍡小管家轻轻提醒："))
        self.assertIn("今天还剩 1 次手动敲门机会", notes[0][0])
        self.assertIn("祝主人开仓顺利", notes[0][0])
        self.assertNotIn("抓到啦", notes[0][0])
        self.assertNotIn("开仓标的", notes[0][0])
        self.assertTrue(notes[1][0].startswith("🚫 小鸡毛的急刹车："))
        self.assertNotIn("监测到危险操作", notes[1][0])

    def test_protect_once_ignores_clean_book(self):
        pos = _pos(manual=False, margin_usdt=250, leverage=5.5)
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("should not close"),
            notify_fn=lambda *a, **k: self.fail("should not notify"),
            listed={"ok": True, "position_count": 1, "positions": [pos]},
            equity={"ok": True, "equity_usdt": 1000.0, "available_usdt": 750.0},
            transfer_fn=lambda *a, **k: self.fail("should not transfer"),
            trading_fn=lambda: {"ok": True, "equity_usdt": 1000.0, "available_usdt": 750.0},
        )
        self.assertTrue(out["ok"])
        self.assertFalse(out["intervened"])
        self.assertEqual([], out["actions"])
        self.assertEqual(0, out["danger_count"])
        self.assertFalse(out["lock_to_funding"])
        self.assertEqual(0, out["manual_open_count"])
        self.assertEqual(2, out["manual_remaining"])

    def test_manual_60_percent_warns_without_close_or_danger(self):
        pos = _pos(manual=True, source="manual_okx", strategy_key="",
                   margin_usdt=600, leverage=10)
        closed = []
        notes = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: closed.append(a) or self.fail("must not close"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True, "sent": True},
            listed=self._listed([pos]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertFalse(out["intervened"])
        self.assertEqual([], closed)
        self.assertEqual(0, out["danger_count"])
        self.assertEqual(1, len(out.get("advisories") or []))
        warn = [n for n in notes if n[1].get("kind") == "force_protect_manual_size_warn"]
        self.assertEqual(1, len(warn))
        self.assertTrue(warn[0][0].startswith("⚠️ 盘子装得太满啦："))
        self.assertIn("本次仅提醒，未干预交易，不计入危险信号", warn[0][0])
        self.assertNotIn("监测到危险操作", warn[0][0])
        self.assertNotIn("监测到手动开仓占用偏高", warn[0][0])
        self.assertFalse(warn[0][1].get("danger"))

    def test_size_warn_sends_once_per_manual_open(self):
        pos = _pos(manual=True, source="manual_okx", strategy_key="",
                   pos_id="zec-open-1", inst_id="ZEC-USDT-SWAP",
                   symbol_label="ZEC", side="short", direction_zh="做空",
                   position_id="ZEC-USDT-SWAP|short|isolated",
                   margin_usdt=57.15, leverage=10)
        notes = []
        t0 = 1787155200.0  # 2026-08-20 Beijing
        kwargs = dict(
            close_fn=lambda *a, **k: self.fail("must not close"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True, "sent": True},
            listed=self._listed([pos]),
            equity={"ok": True, "equity_usdt": 105.86, "available_usdt": 40.0},
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: {"ok": True, "equity_usdt": 105.86, "available_usdt": 40.0},
        )

        def _warns():
            return [n for n in notes if n[1].get("kind") == "force_protect_manual_size_warn"]

        first = fp.protect_once(now_ts=t0, **kwargs)
        self.assertEqual(1, len(_warns()))
        self.assertTrue(first["advisories"][0]["notified"])
        dip = dict(pos, margin_usdt=40.0)
        second = fp.protect_once(
            now_ts=t0 + 700.0,
            close_fn=kwargs["close_fn"],
            notify_fn=kwargs["notify_fn"],
            listed=self._listed([dip]),
            equity=kwargs["equity"],
            transfer_fn=kwargs["transfer_fn"],
            trading_fn=kwargs["trading_fn"],
        )
        self.assertEqual([], second.get("advisories") or [])
        third = fp.protect_once(now_ts=t0 + 800.0, **kwargs)
        self.assertEqual(1, len(_warns()))
        self.assertFalse(third["advisories"][0]["notified"])
        across_day = fp.protect_once(now_ts=1787241601.0, **kwargs)
        self.assertEqual("2026-08-21", across_day["beijing_date"])
        self.assertEqual(1, len(_warns()))
        self.assertFalse(across_day["advisories"][0]["notified"])
        closed = fp.protect_once(
            now_ts=1787241700.0,
            close_fn=kwargs["close_fn"],
            notify_fn=kwargs["notify_fn"],
            listed=self._listed([]),
            equity=kwargs["equity"],
            transfer_fn=kwargs["transfer_fn"],
            trading_fn=kwargs["trading_fn"],
        )
        self.assertEqual([], closed.get("advisories") or [])
        fp.protect_once(
            now_ts=1787328001.0,
            close_fn=kwargs["close_fn"],
            notify_fn=kwargs["notify_fn"],
            listed=self._listed([]),
            equity=kwargs["equity"],
            transfer_fn=kwargs["transfer_fn"],
            trading_fn=kwargs["trading_fn"],
        )
        reopen = dict(pos, pos_id="zec-open-2")
        fourth = fp.protect_once(
            now_ts=1787328100.0,
            close_fn=kwargs["close_fn"],
            notify_fn=kwargs["notify_fn"],
            listed=self._listed([reopen]),
            equity=kwargs["equity"],
            transfer_fn=kwargs["transfer_fn"],
            trading_fn=kwargs["trading_fn"],
        )
        self.assertEqual(2, len(_warns()))
        self.assertTrue(fourth["advisories"][0]["notified"])

    def test_legacy_size_warn_cooldown_does_not_resend(self):
        pos = _pos(manual=True, source="manual_okx", strategy_key="",
                   pos_id="3859229265411182593", inst_id="ZEC-USDT-SWAP",
                   symbol_label="ZEC", side="short", direction_zh="做空",
                   position_id="ZEC-USDT-SWAP|short|cross",
                   mgn_mode="cross", margin_usdt=57.15, leverage=10)
        today = fp.beijing_date(1787155200.0)
        fp._persist_state({
            "beijing_date": today,
            "last_notify_ts": {
                "ZEC-USDT-SWAP|short|cross|manual_size_warn": 1787155200.0,
            },
        })
        notes = []
        out = fp.protect_once(
            now_ts=1787155900.0,
            close_fn=lambda *a, **k: self.fail("must not close"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True, "sent": True},
            listed=self._listed([pos]),
            equity={"ok": True, "equity_usdt": 105.86, "available_usdt": 40.0},
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: {"ok": True, "equity_usdt": 105.86, "available_usdt": 40.0},
        )
        warn = [n for n in notes if n[1].get("kind") == "force_protect_manual_size_warn"]
        self.assertEqual([], warn)
        self.assertFalse(out["advisories"][0]["notified"])
        self.assertIn("3859229265411182593", fp._read_json(fp.STATE_PATH, {}).get(
            "size_warn_notified_ids") or [])

    def test_size_warn_notified_ids_survive_day_rollover(self):
        old = {
            "beijing_date": "2026-08-20",
            "size_warn_notified_ids": ["zec-open-1"],
        }
        new = fp.normalize_day_state(old, now_ts=1787241601.0)
        self.assertEqual("2026-08-21", new["beijing_date"])
        self.assertEqual(["zec-open-1"], new["size_warn_notified_ids"])

    def test_manual_over_75_closes_and_counts_danger(self):
        pos = _pos(manual=True, source="manual_okx", strategy_key="",
                   margin_usdt=800, leverage=10)
        closed = []
        notes = []
        out = fp.protect_once(
            close_fn=lambda inst_id, side, reason=None, mgn_mode=None: (
                closed.append(reason) or {"ok": True, "closed": True}),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True, "sent": True},
            listed=self._listed([pos]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertTrue(out["intervened"])
        self.assertEqual(1, len(closed))
        self.assertIn("manual_over_force_close", closed[0])
        self.assertEqual(1, out["danger_count"])
        intervene = [n for n in notes if n[1].get("kind") == "force_protect_intervene"]
        self.assertEqual(1, len(intervene))
        self.assertTrue(intervene[0][0].startswith("🚫 小鸡毛的急刹车："))
        self.assertNotIn("监测到危险操作", intervene[0][0])
        self.assertIn("已无条件强制平仓", intervene[0][0])

    def test_third_danger_locks_and_evacuates_to_funding(self):
        notes = []
        transfers = []
        positions = [
            _pos(position_id="p%s" % i, leverage=50, margin_usdt=40)
            for i in range(3)
        ]

        def close_fn(inst_id, side, reason=None, mgn_mode=None):
            return {"ok": True, "closed": True}

        def notify_fn(message, meta):
            notes.append((message, meta))
            return {"ok": True, "sent": True}

        def transfer_fn(amt, src, dst):
            transfers.append((amt, src, dst))
            return {"ok": True, "amt": amt, "from": src, "to": dst}

        out = fp.protect_once(
            close_fn=close_fn,
            notify_fn=notify_fn,
            listed={"ok": True, "position_count": 3, "positions": positions},
            equity={"ok": True, "equity_usdt": 1000.0, "available_usdt": 220.0},
            transfer_fn=transfer_fn,
            trading_fn=lambda: {"ok": True, "equity_usdt": 1000.0, "available_usdt": 220.0},
        )
        self.assertEqual(3, out["danger_count"])
        self.assertTrue(out["lock_to_funding"])
        self.assertEqual(1, len(transfers))
        self.assertEqual(("6"), transfers[0][2])
        self.assertEqual(fp.ACCT_TRADING, transfers[0][1])
        lock_notes = [n for n in notes if n[1].get("kind") == "day_lock"]
        self.assertEqual(1, len(lock_notes))
        self.assertTrue("本日账上锁" in lock_notes[0][0])
        self.assertNotIn("监测到危险操作", lock_notes[0][0])
        self.assertIn("今日风险行为已达3次：本日账上锁", lock_notes[0][0])
        self.assertIn("USDC", lock_notes[0][0])
        self.assertNotIn("庄园保鲜箱", lock_notes[0][0])
        self.assertNotIn("嘟嘟", lock_notes[0][0])

    def test_two_dangers_do_not_lock(self):
        positions = [
            _pos(position_id="p%s" % i, leverage=50, margin_usdt=40)
            for i in range(2)
        ]
        out = fp.protect_once(
            close_fn=lambda *a, **k: {"ok": True, "closed": True},
            notify_fn=lambda *a, **k: {"ok": True},
            listed={"ok": True, "position_count": 2, "positions": positions},
            equity={"ok": True, "equity_usdt": 1000.0, "available_usdt": 800.0},
            transfer_fn=lambda *a, **k: self.fail("should not transfer yet"),
            trading_fn=lambda: {"ok": True, "available_usdt": 800.0},
        )
        self.assertEqual(2, out["danger_count"])
        self.assertFalse(out["lock_to_funding"])

    def test_already_three_dangers_lock_on_next_tick(self):
        today = fp.beijing_date()
        fp._persist_state({
            "beijing_date": today,
            "danger_count": 3,
            "lock_to_funding": False,
            "lock_evacuated": False,
            "last_notify_ts": {},
        })
        notes = []
        transfers = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("no open positions"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed={"ok": True, "position_count": 0, "positions": []},
            equity={"ok": True, "equity_usdt": 180.0, "available_usdt": 180.0},
            transfer_fn=lambda amt, src, dst: transfers.append((amt, src, dst)) or {"ok": True, "amt": amt},
            trading_fn=lambda: {"ok": True, "equity_usdt": 180.0, "available_usdt": 180.0},
        )
        self.assertTrue(out["lock_to_funding"])
        self.assertEqual(1, len(transfers))
        self.assertEqual(fp.ACCT_FUNDING, transfers[0][2])
        lock_notes = [n for n in notes if n[1].get("kind") == "day_lock"]
        self.assertEqual(1, len(lock_notes))
        self.assertIn("今日风险行为已达3次：本日账上锁", lock_notes[0][0])

    def test_transfer_failure_still_locks_and_notifies(self):
        today = fp.beijing_date()
        fp._persist_state({
            "beijing_date": today,
            "danger_count": 3,
            "lock_to_funding": False,
            "lock_evacuated": False,
            "last_notify_ts": {},
            "session_open_notified_week": "test-open-week",
        })
        notes = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("no open positions"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed={"ok": True, "position_count": 0, "positions": []},
            equity={"ok": True, "equity_usdt": 180.0, "available_usdt": 180.0},
            transfer_fn=lambda *a, **k: {"ok": False, "error": "no transfer permission"},
            trading_fn=lambda: {"ok": True, "equity_usdt": 180.0, "available_usdt": 180.0},
        )
        self.assertTrue(out["lock_to_funding"])
        self.assertFalse(out["lock_result"]["transferred"])
        self.assertEqual(1, len(notes))
        self.assertIn("本日账上锁", notes[0][0])
        self.assertIn("买入尚未成功", notes[0][0])
        self.assertNotIn("庄园保鲜箱", notes[0][0])

    def test_lock_blocks_funding_to_trading_same_day(self):
        today = fp.beijing_date()
        fp._persist_state({
            "beijing_date": today,
            "danger_count": 3,
            "lock_to_funding": True,
            "lock_evacuated": True,
            "lock_at": "2026-08-20 09:00:00",
            "lock_at_ts": time.time() - 60,
            "last_notify_ts": {},
            "session_open_notified_week": "test-open-week",
        })
        notes = []
        transfers = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("no positions"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed={"ok": True, "position_count": 0, "positions": []},
            equity={"ok": True, "equity_usdt": 80.0, "available_usdt": 80.0},
            transfer_fn=lambda amt, src, dst: transfers.append((amt, src, dst)) or {"ok": True, "amt": amt},
            trading_fn=lambda: {"ok": True, "equity_usdt": 80.0, "available_usdt": 80.0},
            bills_fn=lambda lock_ts: [{"from": "6", "to": "18", "sz": "80", "ts": "1"}],
        )
        self.assertTrue(out["lock_to_funding"])
        self.assertEqual(1, len(transfers))
        self.assertEqual(fp.ACCT_FUNDING, transfers[0][2])
        self.assertEqual(1, len(notes))
        self.assertIn("本日账户仍在上锁", notes[0][0])
        self.assertIn("已再次全仓买入 USDC 现货", notes[0][0])
        self.assertIn("拦截金额", notes[0][0])
        self.assertNotIn("由于账户处在锁定状态", notes[0][0])
        self.assertNotIn("庄园保鲜箱", notes[0][0])
        self.assertNotIn("嘟嘟", notes[0][0])

    def test_auto_close_while_locked_reparks_without_danger_warning(self):
        today = fp.beijing_date()
        fp._persist_state({
            "beijing_date": today,
            "danger_count": 3,
            "lock_to_funding": True,
            "lock_evacuated": True,
            "lock_at": "2026-08-24 13:51:18",
            "lock_at_ts": time.time() - 60,
            "last_notify_ts": {},
            "session_open_notified_week": "test-open-week",
        })
        fp.begin_auto_close_repark_window(snap={
            "inst_id": "BTC-USDT-SWAP",
            "strategy_title": "趋势回调买入优化V5",
        })
        notes = []
        transfers = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("no positions"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True, "sent": True},
            listed={"ok": True, "position_count": 0, "positions": []},
            equity={"ok": True, "equity_usdt": 80.0, "available_usdt": 36.0},
            transfer_fn=lambda amt, src, dst: transfers.append((amt, src, dst)) or {"ok": True, "amt": amt},
            trading_fn=lambda: {"ok": True, "equity_usdt": 80.0, "available_usdt": 36.0},
        )
        self.assertTrue(out["lock_to_funding"])
        self.assertEqual(1, len(transfers))
        self.assertEqual("auto_close_lock_repark", (out.get("lock_result") or {}).get("kind"))
        self.assertEqual(1, len(notes))
        self.assertEqual("force_protect_auto_close_repark", (notes[0][1] or {}).get("kind"))
        self.assertIn("由于账户处在锁定状态 自动平仓后资金已转为usdc", notes[0][0])
        self.assertNotIn("监测到危险操作", notes[0][0])
        self.assertNotIn("危险的小动作", notes[0][0])
        self.assertFalse(fp.auto_close_repark_window_active())

    def test_beijing_date_rollover_clears_lock(self):
        fp._persist_state({
            "beijing_date": "1999-01-01",
            "danger_count": 9,
            "lock_to_funding": True,
            "lock_evacuated": True,
        })
        state = fp._load_state()
        self.assertEqual(fp.beijing_date(), state["beijing_date"])
        self.assertEqual(0, state["danger_count"])
        self.assertFalse(state["lock_to_funding"])
        self.assertEqual(0, state["manual_open_count"])
        self.assertEqual([], state["manual_open_ids"])
        self.assertTrue(state["unlock_notify_pending"])

    def test_funding_bill_helper_filters_lock_time(self):
        bills = {
            "code": "0",
            "data": [
                {"from": "6", "to": "18", "ts": "1000", "sz": "10"},
                {"from": "6", "to": "18", "ts": "5000", "sz": "20"},
                {"from": "18", "to": "6", "ts": "6000", "sz": "3"},
            ],
        }
        hits = fp.funding_to_trading_bills(4.0, raw_bills=bills)
        self.assertEqual(1, len(hits))
        self.assertEqual("20", hits[0]["sz"])

    def _listed(self, positions):
        return {"ok": True, "position_count": len(positions), "positions": positions}

    def _equity(self):
        return {"ok": True, "equity_usdt": 1000.0, "available_usdt": 800.0}

    def test_first_manual_open_notifies_remaining(self):
        pos = _pos(manual=True, source="manual_okx", strategy_key="",
                   margin_usdt=200, leverage=10)
        notes = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("allowed manual should stay"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed=self._listed([pos]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertFalse(out["intervened"])
        self.assertEqual(1, out["manual_open_count"])
        self.assertEqual(1, out["manual_remaining"])
        self.assertEqual(1, len(notes))
        self.assertTrue(notes[0][0].startswith("🍡小管家轻轻提醒："))
        self.assertIn("主人手动做多 ETH", notes[0][0])
        self.assertIn("200.00 USDT", notes[0][0])
        self.assertIn("今天还剩 1 次手动敲门机会", notes[0][0])
        self.assertIn("祝主人开仓顺利", notes[0][0])
        self.assertNotIn("开仓标的", notes[0][0])
        self.assertEqual("manual_open_detect", notes[0][1].get("kind"))

    def test_failed_manual_notification_retries_without_recount(self):
        pos = _pos(
            position_id="BEAT-USDT-SWAP|long|isolated",
            inst_id="BEAT-USDT-SWAP", symbol_label="BEAT", side="long",
            direction_zh="做多", manual=True, source="manual_okx",
            strategy_key="", pos_id="p-retry", margin_usdt=100,
            leverage=10,
        )
        calls = []

        def notify_fn(message, meta):
            calls.append((message, dict(meta)))
            if len(calls) == 1:
                return {"ok": False, "sent": False,
                        "notification_error": "read timeout"}
            return {"ok": True, "sent": True}

        first = fp.protect_once(
            close_fn=lambda *a, **k: {"ok": True, "closed": False},
            notify_fn=notify_fn,
            listed={"ok": True, "positions": [pos]},
            equity={"ok": True, "equity_usdt": 1000.0},
            now_ts=1000.0,
        )
        self.assertEqual(1, first["manual_open_count"])
        self.assertEqual(1, first["pending_manual_notification_count"])
        self.assertEqual(1, len(calls))

        # Before the retry deadline: no duplicate attempt and no recount.
        second = fp.protect_once(
            close_fn=lambda *a, **k: {"ok": True, "closed": False},
            notify_fn=notify_fn,
            listed={"ok": True, "positions": [pos]},
            equity={"ok": True, "equity_usdt": 1000.0},
            now_ts=1005.0,
        )
        self.assertEqual(1, second["manual_open_count"])
        self.assertEqual(1, len(calls))

        # A later poll resumes the persisted item and removes it only after a
        # positive sent receipt.
        third = fp.protect_once(
            close_fn=lambda *a, **k: {"ok": True, "closed": False},
            notify_fn=notify_fn,
            listed={"ok": True, "positions": [pos]},
            equity={"ok": True, "equity_usdt": 1000.0},
            now_ts=1011.0,
        )
        self.assertEqual(1, third["manual_open_count"])
        self.assertEqual(0, third["pending_manual_notification_count"])
        self.assertEqual(2, len(calls))
        self.assertTrue(calls[-1][1]["retry"])
        self.assertEqual(2, calls[-1][1]["attempt"])

    def test_wxpusher_accepted_counts_as_manual_notify_success(self):
        pos = _pos(manual=True, source="manual_okx", strategy_key="",
                   pos_id="p-acc", margin_usdt=100, leverage=10)
        notes = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: {"ok": True, "closed": False},
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {
                "ok": True, "sent": False, "accepted": True,
                "delivery_pending": True,
            },
            listed=self._listed([pos]),
            equity=self._equity(),
        )
        self.assertEqual(1, len(notes))
        self.assertEqual(0, out["pending_manual_notification_count"])

    def test_listing_failure_still_retries_pending_manual_notify(self):
        today = fp.beijing_date()
        fp._persist_state({
            "beijing_date": today,
            "manual_open_ids": ["p-retry"],
            "manual_open_count": 1,
            "pending_manual_notifications": {
                "p-retry": {
                    "event_id": today + "|p-retry",
                    "fingerprint": "p-retry",
                    "message": "监测到手动开仓",
                    "attempts": 1,
                    "next_retry_ts": 0,
                }
            },
        })
        notes = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("no positions"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {
                "ok": True, "sent": True},
            listed={"ok": False, "error": "OKX HTTP error 401"},
            equity=self._equity(),
            now_ts=2000.0,
        )
        self.assertFalse(out["ok"])
        self.assertEqual(1, len(notes))
        self.assertEqual(0, out["pending_manual_notification_count"])

    def test_pending_manual_notification_survives_day_rollover(self):
        old = {
            "beijing_date": "2026-08-20",
            "manual_open_count": 1,
            "manual_open_ids": ["p-old"],
            "pending_manual_notifications": {
                "p-old": {"event_id": "2026-08-20|p-old"},
            },
        }
        new = fp.normalize_day_state(old, now_ts=1787241601.0)
        self.assertEqual("2026-08-21", new["beijing_date"])
        self.assertEqual(0, new["manual_open_count"])
        self.assertEqual([], new["manual_open_ids"])
        self.assertIn("p-old", new["pending_manual_notifications"])

    def test_same_manual_not_counted_twice(self):
        pos = _pos(manual=True, source="manual_okx", strategy_key="",
                   pos_id="p1", margin_usdt=200, leverage=10)
        notes = []

        def notify_fn(msg, meta):
            notes.append((msg, meta))
            return {"ok": True}

        kwargs = dict(
            close_fn=lambda *a, **k: self.fail("should not close"),
            notify_fn=notify_fn,
            listed=self._listed([pos]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        fp.protect_once(**kwargs)
        out = fp.protect_once(**kwargs)
        self.assertEqual(1, out["manual_open_count"])
        self.assertEqual(1, len(notes))

    def test_three_manuals_allowed_fourth_closed(self):
        notes = []
        closed = []

        def close_fn(inst_id, side, reason=None, mgn_mode=None):
            closed.append((inst_id, side, reason))
            return {"ok": True, "closed": True}

        def notify_fn(msg, meta):
            notes.append((msg, meta))
            return {"ok": True}

        positions = [
            _pos(manual=True, source="manual_okx", strategy_key="",
                 position_id="m%s" % i, pos_id="okx%s" % i,
                 inst_id="COIN%s-USDT-SWAP" % i, symbol_label="C%s" % i,
                 margin_usdt=100, leverage=10)
            for i in range(3)
        ]
        out = fp.protect_once(
            close_fn=close_fn,
            notify_fn=notify_fn,
            listed=self._listed(positions),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual(2, out["manual_open_count"])
        self.assertEqual(0, out["manual_remaining"])
        self.assertEqual(1, len(closed))
        self.assertEqual("COIN2-USDT-SWAP", closed[0][0])
        self.assertIn("manual_open_over_limit", closed[0][2])
        self.assertEqual(1, out["danger_count"])
        self.assertFalse(out["lock_to_funding"])
        detect = [n for n in notes if n[1].get("kind") == "manual_open_detect"]
        self.assertEqual(2, len(detect))
        self.assertTrue(detect[-1][0].startswith("🍡小管家轻轻提醒："))
        self.assertIn("今日 2 次手动额度已经用完", detect[-1][0])
        intervene = [n for n in notes if "今日手动开仓超过" in n[0]]
        self.assertEqual(1, len(intervene))
        self.assertTrue(intervene[0][0].startswith("🚫 小鸡毛的急刹车："))
        self.assertNotIn("监测到危险操作", intervene[0][0])
        self.assertIn("开仓标的 C2", intervene[0][0])
        self.assertIn("仓位估算", intervene[0][0])
        self.assertIn("当天剩余开仓次数 0", intervene[0][0])

    def test_three_over_limit_closes_lock_account(self):
        notes = []
        transfers = []
        positions = [
            _pos(manual=True, source="manual_okx", strategy_key="",
                 position_id="m%s" % i, pos_id="okx%s" % i,
                 inst_id="COIN%s-USDT-SWAP" % i, symbol_label="C%s" % i,
                 margin_usdt=80, leverage=10)
            for i in range(6)
        ]
        out = fp.protect_once(
            close_fn=lambda *a, **k: {"ok": True, "closed": True},
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed=self._listed(positions),
            equity=self._equity(),
            transfer_fn=lambda amt, src, dst: transfers.append((amt, src, dst)) or {"ok": True, "amt": amt},
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual(2, out["manual_open_count"])
        self.assertEqual(4, out["danger_count"])
        self.assertTrue(out["lock_to_funding"])
        self.assertEqual(1, len(transfers))
        lock_notes = [n for n in notes if n[1].get("kind") == "day_lock"]
        self.assertEqual(1, len(lock_notes))
        self.assertIn("本日账上锁", lock_notes[0][0])

    def test_quota_does_not_count_auto_positions(self):
        pos = _pos(manual=False, margin_usdt=200, leverage=10)
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("should not close"),
            notify_fn=lambda *a, **k: self.fail("should not notify"),
            listed=self._listed([pos]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual(0, out["manual_open_count"])
        self.assertEqual(2, out["manual_remaining"])

    def test_hitchhiker_matches_auto_inst_and_side(self):
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key=DIR_KEY)
        hitch = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="h1", position_id="ETH-USDT-SWAP|long|cross", mgn_mode="cross",
        )
        other = _pos(
            manual=True, source="manual_okx", strategy_key="",
            inst_id="BTC-USDT-SWAP", symbol_label="BTC", side="short",
            direction_zh="做空", pos_id="h2",
        )
        self.assertTrue(fp.is_hitchhiker(hitch, [auto, hitch]))
        self.assertFalse(fp.is_hitchhiker(other, [auto, other]))
        self.assertFalse(fp.is_hitchhiker(auto, [auto, hitch]))
        closed_auto = _pos(
            manual=False, source="formal_auto_trade", strategy_key=DIR_KEY,
            contracts=0,
        )
        self.assertFalse(fp.is_hitchhiker(hitch, [closed_auto, hitch]))

    def test_odds_auto_same_side_is_manual_not_hitch(self):
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key=ODDS_KEY)
        manual = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="m1", position_id="ETH-USDT-SWAP|long|cross", mgn_mode="cross",
        )
        self.assertFalse(fp.auto_position_allows_hitch(auto))
        self.assertFalse(fp.is_hitchhiker(manual, [auto, manual]))
        self.assertEqual(set(), fp.auto_side_keys([auto, manual]))

    def test_unclassified_auto_same_side_is_not_hitch(self):
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key="kimi_unknown")
        manual = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="m2", position_id="ETH-USDT-SWAP|long|cross", mgn_mode="cross",
        )
        self.assertFalse(fp.is_hitchhiker(manual, [auto, manual]))

    def test_odds_auto_after_quota_full_closes_as_manual(self):
        today = fp.beijing_date()
        fp._persist_state({
            "beijing_date": today,
            "manual_open_ids": ["a", "b"],
            "manual_open_count": 2,
        })
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key=ODDS_KEY)
        manual = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="late", position_id="ETH-USDT-SWAP|long|cross",
            mgn_mode="cross", margin_usdt=80, leverage=10,
        )
        closed = []
        notes = []
        out = fp.protect_once(
            close_fn=lambda inst_id, side, reason=None, mgn_mode=None: (
                closed.append(reason) or {"ok": True, "closed": True}),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed=self._listed([auto, manual]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: {"ok": True, "skipped": True},
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual(1, len(closed))
        self.assertIn("manual_open_over_limit", closed[0])
        self.assertEqual(0, out["hitch_extra_count"])
        self.assertEqual(1, out["danger_count"])
        for msg, _meta in notes:
            self.assertNotIn("识别为顺风车", msg)

    def test_no_extra_quota_if_auto_not_holding(self):
        today = fp.beijing_date()
        fp._persist_state({
            "beijing_date": today,
            "manual_open_ids": ["a", "b"],
            "manual_open_count": 2,
        })
        closed_auto = _pos(
            manual=False, source="formal_auto_trade", strategy_key=DIR_KEY,
            contracts=0,
        )
        manual = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="late", position_id="ETH-USDT-SWAP|long|cross",
            mgn_mode="cross", margin_usdt=80, leverage=10,
        )
        closed = []
        out = fp.protect_once(
            close_fn=lambda inst_id, side, reason=None, mgn_mode=None: (
                closed.append(reason) or {"ok": True, "closed": True}),
            notify_fn=lambda *a, **k: {"ok": True},
            listed=self._listed([closed_auto, manual]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual(1, len(closed))
        self.assertIn("manual_open_over_limit", closed[0])
        self.assertEqual(0, out["hitch_extra_count"])
        self.assertEqual(1, out["danger_count"])

    def test_hitch_after_regular_quota_uses_extra_slot(self):
        today = fp.beijing_date()
        fp._persist_state({
            "beijing_date": today,
            "manual_open_ids": ["a", "b"],
            "manual_open_count": 2,
            "hitch_extra_ids": [],
            "last_notify_ts": {},
            "session_open_notified_week": "test-open-week",
        })
        notes = []
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key=DIR_KEY)
        hitch = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="hitch1", position_id="ETH-USDT-SWAP|long|cross",
            mgn_mode="cross", margin_usdt=80, leverage=10,
        )
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("hitch extra should stay"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed=self._listed([auto, hitch]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual(2, out["manual_open_count"])
        self.assertEqual(0, out["manual_remaining"])
        self.assertEqual(1, out["hitch_extra_count"])
        self.assertEqual(2, out["hitch_extra_remaining"])
        self.assertFalse(out["intervened"])
        self.assertEqual(1, len(notes))
        self.assertIn("识别为顺风车", notes[0][0])
        self.assertIn("顺风车额外剩余开仓次数 2", notes[0][0])

    def test_non_hitch_still_closed_when_regular_quota_used(self):
        today = fp.beijing_date()
        fp._persist_state({
            "beijing_date": today,
            "manual_open_ids": ["a", "b"],
            "manual_open_count": 2,
        })
        extra = _pos(
            manual=True, source="manual_okx", strategy_key="",
            inst_id="SOL-USDT-SWAP", symbol_label="SOL", pos_id="solo",
            margin_usdt=80, leverage=10,
        )
        closed = []
        out = fp.protect_once(
            close_fn=lambda inst_id, side, reason=None, mgn_mode=None: (
                closed.append(inst_id) or {"ok": True, "closed": True}),
            notify_fn=lambda *a, **k: {"ok": True},
            listed=self._listed([extra]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual(["SOL-USDT-SWAP"], closed)
        self.assertEqual(1, out["danger_count"])
        self.assertEqual(0, out["hitch_extra_count"])

    def test_fourth_hitch_extra_is_closed(self):
        today = fp.beijing_date()
        fp._persist_state({
            "beijing_date": today,
            "manual_open_ids": ["a", "b"],
            "manual_open_count": 2,
            "hitch_extra_ids": ["h1", "h2", "h3"],
            "hitch_extra_count": 3,
        })
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key=DIR_KEY)
        hitch = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="h4", position_id="ETH-USDT-SWAP|long|cross",
            mgn_mode="cross", margin_usdt=80, leverage=10,
        )
        closed = []
        out = fp.protect_once(
            close_fn=lambda inst_id, side, reason=None, mgn_mode=None: (
                closed.append(reason) or {"ok": True, "closed": True}),
            notify_fn=lambda *a, **k: {"ok": True},
            listed=self._listed([auto, hitch]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: {"ok": True, "skipped": True},
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual(1, len(closed))
        self.assertIn("manual_open_over_limit", closed[0])
        self.assertEqual(4, out["hitch_extra_count"])
        self.assertEqual(0, out["hitch_extra_remaining"])
        self.assertEqual(1, out["danger_count"])

    def test_hitch_before_quota_full_uses_regular_slot(self):
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key=DIR_KEY)
        hitch = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="h0", position_id="ETH-USDT-SWAP|long|cross",
            mgn_mode="cross", margin_usdt=80, leverage=10,
        )
        notes = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("should stay"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed=self._listed([auto, hitch]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual(1, out["manual_open_count"])
        self.assertEqual(1, out["manual_remaining"])
        self.assertEqual(0, out["hitch_extra_count"])
        self.assertIn("识别为顺风车", notes[0][0])
        self.assertNotIn("计入顺风车额外次数", notes[0][0])
        self.assertIn("顺风车额外剩余开仓次数 3", notes[0][0])

    def test_midnight_unlock_restores_funding_and_notifies(self):
        fp._persist_state({
            "beijing_date": "1999-01-01",
            "danger_count": 3,
            "lock_to_funding": True,
            "lock_evacuated": True,
            "evacuated_amt": 177.42,
        })
        notes = []
        transfers = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("no positions"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed=self._listed([]),
            equity=self._equity(),
            transfer_fn=lambda amt, src, dst: transfers.append((amt, src, dst)) or {"ok": True, "amt": amt},
            trading_fn=lambda: self._equity(),
        )
        self.assertFalse(out["lock_to_funding"])
        self.assertEqual(0, out["danger_count"])
        self.assertEqual(1, len(transfers))
        self.assertEqual(fp.ACCT_FUNDING, transfers[0][1])
        self.assertEqual(fp.ACCT_TRADING, transfers[0][2])
        self.assertAlmostEqual(177.42, transfers[0][0])
        self.assertTrue(out["unlock_result"]["transferred"])
        self.assertIn("昨日锁定已到期", notes[0][0])
        self.assertIn("已将锁定的 USDC 现货卖回 USDT", notes[0][0])
        self.assertIn("177.42 USDC", notes[0][0])
        self.assertEqual("day_unlock", notes[0][1].get("kind"))

    def test_midnight_unlock_without_evacuated_still_notifies(self):
        fp._persist_state({
            "beijing_date": "1999-01-01",
            "danger_count": 3,
            "lock_to_funding": True,
            "lock_evacuated": False,
            "evacuated_amt": 0,
            "session_open_notified_week": "test-open-week",
        })
        notes = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("no positions"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed=self._listed([]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("nothing to restore"),
            trading_fn=lambda: self._equity(),
        )
        self.assertFalse(out["lock_to_funding"])
        self.assertEqual(1, len(notes))
        self.assertIn("昨日锁定已到期", notes[0][0])
        self.assertIn("无需卖回", notes[0][0])

    def test_existing_hitch_backfills_remaining_once(self):
        today = fp.beijing_date()
        hitch = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="h0", position_id="ETH-USDT-SWAP|long|cross",
            mgn_mode="cross", margin_usdt=80, leverage=10,
        )
        fp._persist_state({
            "beijing_date": today,
            "manual_open_ids": ["h0"],
            "manual_open_count": 1,
            "hitch_extra_ids": [],
            "hitch_notified_ids": [],
            "last_notify_ts": {},
            "session_open_notified_week": "test-open-week",
        })
        notes = []
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key=DIR_KEY)
        kwargs = dict(
            close_fn=lambda *a, **k: self.fail("should stay"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed=self._listed([auto, hitch]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        first = fp.protect_once(**kwargs)
        self.assertEqual(1, first["manual_open_count"])
        self.assertEqual(1, len(notes))
        self.assertTrue(notes[0][0].startswith("🚲 顺风车小铃铛："))
        self.assertIn("顺风车额外剩余开仓次数 3", notes[0][0])
        second = fp.protect_once(**kwargs)
        self.assertEqual(1, len(notes))
        self.assertEqual(1, second["manual_open_count"])

    def test_overflow_regular_ids_trimmed_to_denied(self):
        today = fp.beijing_date()
        fp._persist_state({
            "beijing_date": today,
            "manual_open_ids": ["a", "b", "c", "d"],
            "manual_open_count": 4,
            "hitch_extra_ids": [],
            "manual_denied_ids": [],
            "last_notify_ts": {},
        })
        out = fp.protect_once(
            close_fn=lambda *a, **k: {"ok": True, "closed": False},
            notify_fn=lambda *a, **k: {"ok": True},
            listed=self._listed([]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual(2, out["manual_open_count"])
        self.assertEqual(0, out["manual_remaining"])
        self.assertEqual(3, out["hitch_extra_remaining"])
        state = fp._load_state()
        self.assertEqual(["a", "b"], state["manual_open_ids"])
        self.assertEqual(["c", "d"], state["manual_denied_ids"])

    def test_pending_restore_retries_and_notifies(self):
        today = fp.beijing_date()
        fp._persist_state({
            "beijing_date": today,
            "pending_restore_amt": 50.0,
            "unlock_notify_pending": False,
            "unlock_from_date": "1999-01-01",
        })
        notes = []
        transfers = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("no positions"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed=self._listed([]),
            equity=self._equity(),
            transfer_fn=lambda amt, src, dst: transfers.append((amt, src, dst)) or {"ok": True, "amt": amt},
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual(1, len(transfers))
        self.assertEqual(fp.ACCT_FUNDING, transfers[0][1])
        self.assertEqual(fp.ACCT_TRADING, transfers[0][2])
        self.assertTrue(out["unlock_result"]["transferred"])
        self.assertIn("已将锁定的 USDC 现货卖回 USDT", notes[0][0])

    def test_restore_sells_live_usdc_not_inflated_ledger(self):
        today = fp.beijing_date()
        fp._persist_state({
            "beijing_date": today,
            "pending_restore_usdc": 360.91,
            "pending_restore_amt": 360.91,
            "unlock_notify_pending": False,
        })
        transfers = []
        with mock.patch.object(fp, "_live_usdc_available", return_value=118.79):
            out = fp.protect_once(
                close_fn=lambda *a, **k: self.fail("no positions"),
                notify_fn=lambda msg, meta: {"ok": True},
                listed=self._listed([]),
                equity=self._equity(),
                transfer_fn=lambda amt, src, dst: transfers.append(amt) or {"ok": True, "amt": amt},
                trading_fn=lambda: self._equity(),
            )
        self.assertEqual([118.79], transfers)
        self.assertTrue(out["unlock_result"]["transferred"])
        state = fp._load_state()
        self.assertEqual(0.0, state["pending_restore_usdc"])
        self.assertEqual(0.0, state["pending_restore_amt"])

    def test_unpark_caps_sell_to_live_usdc(self):
        with mock.patch.object(fp, "_live_usdc_available", return_value=118.796):
            with mock.patch.object(fp, "place_usdc_spot", return_value={"ok": True, "sz": "118.79"}) as place:
                out = fp.unpark_usdc_to_usdt(360.91)
        self.assertTrue(out["ok"])
        place.assert_called_once_with("sell", "118.79", "base_ccy")

    def test_spot_sz_floors_to_cents(self):
        self.assertEqual("177.41", fp._spot_sz(177.416968))
        self.assertEqual("0", fp._spot_sz(0))

    def test_lock_convert_buys_then_sells_usdc(self):
        calls = []

        def fake_place(side, sz, tgt_ccy):
            calls.append((side, sz, tgt_ccy))
            return {"ok": True, "amt": float(sz), "usdc": float(sz), "side": side}

        with mock.patch.object(fp, "place_usdc_spot", fake_place):
            buy = fp.lock_convert(12.349, fp.ACCT_TRADING, fp.ACCT_FUNDING)
            sell = fp.lock_convert(12.34, fp.ACCT_FUNDING, fp.ACCT_TRADING)
        self.assertTrue(buy["ok"])
        self.assertTrue(sell["ok"])
        self.assertEqual(("buy", "12.34", "quote_ccy"), calls[0])
        self.assertEqual(("sell", "12.34", "base_ccy"), calls[1])

    def test_place_usdc_spot_sends_ccy_and_prefers_cash(self):
        calls = []

        def fake_okx(method, path, body=None, **kwargs):
            calls.append(dict(body or {}))
            return {
                "code": "0",
                "data": [{"sCode": "0", "ordId": "1", "accFillSz": body["sz"]}],
            }

        with mock.patch("auto_trade_okx._okx_request", fake_okx):
            out = fp.place_usdc_spot("buy", "122.40", "quote_ccy")
        self.assertTrue(out["ok"])
        self.assertEqual(1, len(calls))
        self.assertEqual("cash", calls[0]["tdMode"])
        self.assertEqual("USDT", calls[0]["ccy"])
        self.assertEqual("USDC-USDT", calls[0]["instId"])
        self.assertEqual("quote_ccy", calls[0]["tgtCcy"])

    def test_place_usdc_spot_retries_cash_after_cross_ccy_error(self):
        calls = []

        def fake_okx(method, path, body=None, **kwargs):
            calls.append(dict(body or {}))
            if body.get("tdMode") == "cash" and not body.get("ccy"):
                return {
                    "code": "1",
                    "data": [{"sCode": "51000", "sMsg": "Parameter ccy can not be empty."}],
                }
            if body.get("tdMode") == "cash":
                return {
                    "code": "1",
                    "data": [{"sCode": "51000", "sMsg": "Parameter tdMode error"}],
                }
            return {
                "code": "0",
                "data": [{"sCode": "0", "ordId": "9", "accFillSz": body["sz"]}],
            }

        with mock.patch("auto_trade_okx._okx_request", fake_okx):
            out = fp.place_usdc_spot("buy", "122.40", "quote_ccy")
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(len(calls), 1)
        self.assertTrue(any(c.get("tdMode") == "cross" and c.get("ccy") == "USDT" for c in calls)
                        or calls[0].get("tdMode") == "cash")

    def test_place_usdc_spot_does_not_abort_on_ccy_empty(self):
        calls = []

        def fake_okx(method, path, body=None, **kwargs):
            calls.append(dict(body or {}))
            if len(calls) == 1:
                return {
                    "code": "1",
                    "data": [{"sCode": "51000", "sMsg": "Parameter ccy can not be empty."}],
                }
            return {
                "code": "0",
                "data": [{"sCode": "0", "ordId": "2", "accFillSz": body["sz"]}],
            }

        with mock.patch("auto_trade_okx._okx_request", fake_okx):
            out = fp.place_usdc_spot("buy", "10.00", "quote_ccy")
        self.assertTrue(out["ok"])
        self.assertEqual(2, len(calls))
        self.assertEqual("USDT", calls[1]["ccy"])


    def test_cash_window_skips_inbound_park(self):
        today = fp.beijing_date()
        fp._persist_state({
            "beijing_date": today,
            "danger_count": 3,
            "lock_to_funding": True,
            "lock_evacuated": True,
        })
        fp.begin_auto_open_cash_window(ttl_sec=30)
        transfers = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("no close"),
            notify_fn=lambda *a, **k: {"ok": True},
            listed={"ok": True, "position_count": 0, "positions": []},
            equity={"ok": True, "equity_usdt": 1000.0, "available_usdt": 80.0},
            transfer_fn=lambda amt, src, dst: transfers.append((amt, src, dst)) or {"ok": True, "amt": amt},
            trading_fn=lambda: {"ok": True, "available_usdt": 80.0},
        )
        self.assertEqual([], transfers)
        self.assertTrue(fp.cash_window_active())
        self.assertTrue(out["lock_to_funding"])
        self.assertEqual("cash_window_hold", (out.get("lock_result") or {}).get("kind"))

    def test_day_lock_flattens_manual_but_not_auto(self):
        today = fp.beijing_date()
        fp._persist_state({
            "beijing_date": today,
            "danger_count": 3,
            "lock_to_funding": True,
            "lock_evacuated": True,
            "last_notify_ts": {"lock_arm|%s" % today: time.time()},
        })
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key=DIR_KEY,
                    leverage=10, margin_usdt=80)
        manual = _pos(manual=True, source="manual_okx", strategy_key="",
                      position_id="m1", pos_id="m1", leverage=10, margin_usdt=80)
        closed = []
        transfers = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: closed.append((a, k)) or {"ok": True, "closed": True},
            notify_fn=lambda *a, **k: {"ok": True},
            listed=self._listed([auto, manual]),
            equity={"ok": True, "equity_usdt": 1000.0, "available_usdt": 50.0},
            transfer_fn=lambda amt, src, dst: transfers.append((amt, src, dst)) or {"ok": True, "amt": amt},
            trading_fn=lambda: {"ok": True, "available_usdt": 50.0},
        )
        self.assertTrue(out["lock_to_funding"])
        self.assertEqual(1, len(closed))
        self.assertEqual(1, len(transfers))

    def test_release_and_repark_usdc_for_auto_open(self):
        fp._persist_state({
            "beijing_date": fp.beijing_date(),
            "danger_count": 3,
            "lock_to_funding": True,
            "lock_evacuated": True,
        })
        with mock.patch.object(fp, "trading_ccy", return_value={"ok": True, "available": 120.0}):
            with mock.patch.object(fp, "unpark_usdc_to_usdt", return_value={"ok": True, "amt": 120}) as sell:
                with mock.patch.object(fp, "trading_usdt", return_value={"ok": True, "available_usdt": 40.0}):
                    with mock.patch.object(fp, "park_usdt_as_usdc", return_value={"ok": True, "amt": 40}) as buy:
                        out = fp.release_usdc_for_auto_open()
                        self.assertTrue(out.get("window"))
                        self.assertTrue(fp.cash_window_active())
                        self.assertTrue(sell.called)
                        back = fp.repark_usdt_after_auto_open()
                        self.assertFalse(fp.cash_window_active())
                        self.assertTrue(buy.called)
                        self.assertTrue(back.get("ok"))

    def test_release_usdc_skips_when_unlocked(self):
        out = fp.release_usdc_for_auto_open()
        self.assertTrue(out.get("skipped"))
        self.assertFalse(fp.cash_window_active())

    def test_executor_cash_helpers_only_when_locked(self):
        import auto_trade_formal_v6_executor as ex
        with mock.patch.object(fp, "day_lock_active", return_value=False):
            self.assertFalse(ex._day_lock_cash_release_for_open())
        with mock.patch.object(fp, "day_lock_active", return_value=True):
            with mock.patch.object(fp, "release_usdc_for_auto_open",
                                   return_value={"ok": True, "window": {"until_ts": 1}}):
                self.assertTrue(ex._day_lock_cash_release_for_open())
        with mock.patch.object(fp, "repark_usdt_after_auto_open") as repark:
            ex._day_lock_cash_repark_after_open(False)
            repark.assert_not_called()
            ex._day_lock_cash_repark_after_open(True)
            repark.assert_called_once()

    def test_hitch_binding_stored_while_auto_open(self):
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key=DIR_KEY)
        hitch = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="hitch1", position_id="ETH-USDT-SWAP|long|cross",
            mgn_mode="cross", margin_usdt=80, leverage=10,
        )
        fp.protect_once(
            close_fn=lambda *a, **k: self.fail("hitch should stay while auto holds"),
            notify_fn=lambda *a, **k: {"ok": True},
            listed=self._listed([auto, hitch]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        state = fp._load_state()
        bind = (state.get("hitch_bindings") or {}).get("hitch1") or {}
        self.assertEqual("ETH-USDT-SWAP", bind.get("inst_id"))
        self.assertEqual("long", bind.get("side"))
        self.assertIn(DIR_KEY, bind.get("sponsor_keys") or [])

    def test_hitch_follow_close_when_direction_auto_exits(self):
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key=DIR_KEY)
        hitch = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="hitch1", position_id="ETH-USDT-SWAP|long|cross",
            mgn_mode="cross", margin_usdt=80, leverage=10,
        )
        fp.protect_once(
            close_fn=lambda *a, **k: self.fail("should stay on first tick"),
            notify_fn=lambda *a, **k: {"ok": True},
            listed=self._listed([auto, hitch]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        closed = []
        notes = []
        out = fp.protect_once(
            close_fn=lambda inst_id, side, reason=None, mgn_mode=None: (
                closed.append((inst_id, side, reason, mgn_mode))
                or {"ok": True, "closed": True}),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed=self._listed([hitch]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual(1, len(closed))
        self.assertEqual("ETH-USDT-SWAP", closed[0][0])
        self.assertEqual("force_protect:hitch_follow_close", closed[0][2])
        self.assertEqual(0, out["danger_count"])
        self.assertEqual(1, len(out.get("hitch_follow_closes") or []))
        self.assertEqual([], out.get("actions") or [])
        follow = [n for n in notes if n[1].get("kind") == "force_protect_hitch_follow_close"]
        self.assertEqual(1, len(follow))
        self.assertIn("顺风车到站", follow[0][0])
        self.assertIn("止盈 / 止损 / 失效", follow[0][0])
        self.assertNotIn("急刹车", follow[0][0])
        self.assertNotIn("危险操作", follow[0][0])
        state = fp._load_state()
        self.assertNotIn("hitch1", state.get("hitch_bindings") or {})

    def test_odds_auto_exit_does_not_follow_close_manual(self):
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key=ODDS_KEY)
        manual = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="m-odds", position_id="ETH-USDT-SWAP|long|cross",
            mgn_mode="cross", margin_usdt=80, leverage=10,
        )
        fp.protect_once(
            close_fn=lambda *a, **k: self.fail("odds same-side is not hitch"),
            notify_fn=lambda *a, **k: {"ok": True},
            listed=self._listed([auto, manual]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("odds exit must not pull manual"),
            notify_fn=lambda *a, **k: {"ok": True},
            listed=self._listed([manual]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual([], out.get("hitch_follow_closes") or [])
        self.assertEqual(0, out["danger_count"])
        self.assertEqual(1, out["manual_open_count"])

    def test_hitch_follow_close_fail_keeps_binding_and_retries(self):
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key=DIR_KEY)
        hitch = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="hitch-retry", position_id="ETH-USDT-SWAP|long|cross",
            mgn_mode="cross", margin_usdt=80, leverage=10,
        )
        fp.protect_once(
            close_fn=lambda *a, **k: self.fail("should stay on first tick"),
            notify_fn=lambda *a, **k: {"ok": True},
            listed=self._listed([auto, hitch]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        closed = []
        out_fail = fp.protect_once(
            close_fn=lambda *a, **k: closed.append("fail") or {"ok": False, "error": "timeout"},
            notify_fn=lambda *a, **k: {"ok": True},
            listed=self._listed([hitch]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual(1, len(closed))
        self.assertFalse((out_fail.get("hitch_follow_closes") or [{}])[0].get("closed"))
        self.assertIn("hitch-retry", (fp._load_state().get("hitch_bindings") or {}))
        closed2 = []
        notes = []
        out_ok = fp.protect_once(
            close_fn=lambda inst_id, side, reason=None, mgn_mode=None: (
                closed2.append(reason) or {"ok": True, "closed": True}),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed=self._listed([hitch]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual(["force_protect:hitch_follow_close"], closed2)
        self.assertTrue((out_ok.get("hitch_follow_closes") or [{}])[0].get("closed"))
        self.assertNotIn("hitch-retry", (fp._load_state().get("hitch_bindings") or {}))
        self.assertTrue(any(
            n[1].get("kind") == "force_protect_hitch_follow_close" for n in notes))

    def test_hitch_follow_close_skips_danger_intervene(self):
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key=DIR_KEY)
        hitch_small = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="hitch-big", position_id="ETH-USDT-SWAP|long|cross",
            mgn_mode="cross", margin_usdt=80, leverage=10,
        )
        fp.protect_once(
            close_fn=lambda *a, **k: self.fail("small hitch should stay while auto holds"),
            notify_fn=lambda *a, **k: {"ok": True},
            listed=self._listed([auto, hitch_small]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        hitch_big = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="hitch-big", position_id="ETH-USDT-SWAP|long|cross",
            mgn_mode="cross", margin_usdt=800, leverage=10,
        )
        closed = []
        notes = []
        out = fp.protect_once(
            close_fn=lambda inst_id, side, reason=None, mgn_mode=None: (
                closed.append(reason) or {"ok": True, "closed": True}),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed=self._listed([hitch_big]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertEqual(["force_protect:hitch_follow_close"], closed)
        self.assertEqual(0, out["danger_count"])
        self.assertEqual([], out.get("actions") or [])
        self.assertTrue(any(
            n[1].get("kind") == "force_protect_hitch_follow_close" for n in notes))
        self.assertFalse(any(
            n[1].get("kind") == "force_protect_intervene" for n in notes))

    def test_reclass_sponsor_to_odds_does_not_follow_close(self):
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key=DIR_KEY)
        hitch = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="hitch-reclass", position_id="ETH-USDT-SWAP|long|cross",
            mgn_mode="cross", margin_usdt=80, leverage=10,
        )
        fp.protect_once(
            close_fn=lambda *a, **k: self.fail("should stay while auto is 方向型"),
            notify_fn=lambda *a, **k: {"ok": True},
            listed=self._listed([auto, hitch]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        self.assertIn("hitch-reclass", (fp._load_state().get("hitch_bindings") or {}))
        with mock.patch.object(fp, "auto_strategy_hitch_class", return_value="赔率型"):
            out = fp.protect_once(
                close_fn=lambda *a, **k: self.fail("reclass must not pull hitch"),
                notify_fn=lambda *a, **k: {"ok": True},
                listed=self._listed([auto, hitch]),
                equity=self._equity(),
                transfer_fn=lambda *a, **k: self.fail("no transfer"),
                trading_fn=lambda: self._equity(),
            )
        self.assertEqual([], out.get("hitch_follow_closes") or [])
        self.assertIn("hitch-reclass", (fp._load_state().get("hitch_bindings") or {}))

    def test_already_absent_while_hitch_listed_is_not_gone(self):
        auto = _pos(manual=False, source="formal_auto_trade", strategy_key=DIR_KEY)
        hitch = _pos(
            manual=True, source="manual_okx", strategy_key="",
            pos_id="hitch-ghost", position_id="ETH-USDT-SWAP|long|cross",
            mgn_mode="cross", margin_usdt=80, leverage=10,
        )
        fp.protect_once(
            close_fn=lambda *a, **k: self.fail("should stay on first tick"),
            notify_fn=lambda *a, **k: {"ok": True},
            listed=self._listed([auto, hitch]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        notes = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: {
                "ok": True, "closed": False, "reason": "position_already_absent"},
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True},
            listed=self._listed([hitch]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
        )
        row = (out.get("hitch_follow_closes") or [{}])[0]
        self.assertFalse(row.get("closed"))
        self.assertIn("hitch-ghost", (fp._load_state().get("hitch_bindings") or {}))
        self.assertTrue(any("未确认" in n[0] for n in notes))
        self.assertFalse(any("已一并平仓" in n[0] for n in notes))


class SessionWindowProtectTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._auto = Path(self._tmp)
        self._state = self._auto / "force_protect_state.json"
        self._events = self._auto / "force_protect_events.jsonl"
        self._patches = [
            mock.patch.object(fp, "AUTO_DIR", self._auto),
            mock.patch.object(fp, "STATE_PATH", self._state),
            mock.patch.object(fp, "EVENT_PATH", self._events),
            mock.patch.object(fp, "_live_usdc_available", return_value=None),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _listed(self, positions):
        return {"ok": True, "position_count": len(positions), "positions": positions}

    def _equity(self):
        return {"ok": True, "equity_usdt": 1000.0, "available_usdt": 800.0}

    def _sat_after_close(self):
        from datetime import datetime, timedelta, timezone
        return datetime(2026, 8, 29, 5, 0, tzinfo=timezone(timedelta(hours=8))).timestamp()

    def _mon_in_session(self):
        from datetime import datetime, timedelta, timezone
        return datetime(2026, 8, 24, 10, 0, tzinfo=timezone(timedelta(hours=8))).timestamp()

    def test_cutoff_flattens_leftovers_without_danger(self):
        auto = _pos(manual=False, margin_usdt=200, leverage=10)
        manual = _pos(
            manual=True, source="manual_okx", strategy_key="",
            margin_usdt=120, leverage=8, position_id="manual-weekend",
        )
        closed = []
        notes = []

        def close_fn(inst_id, side, reason=None, mgn_mode=None):
            closed.append((inst_id, side, reason, mgn_mode))
            return {"ok": True, "closed": True}

        out = fp.protect_once(
            close_fn=close_fn,
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True, "sent": True},
            listed=self._listed([auto, manual]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: {"ok": True, "skipped": True},
            trading_fn=lambda: self._equity(),
            now_ts=self._sat_after_close(),
        )
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("session_close"))
        self.assertEqual(2, len(closed))
        self.assertTrue(all(
            (row[2] or "").startswith("force_protect:session_close") for row in closed
        ))
        self.assertEqual(0, int(out.get("danger_count") or 0))
        self.assertTrue(any(
            (meta or {}).get("kind") == "force_protect_session_close" for _, meta in notes
        ))
        self.assertTrue(any("收盘时间到" in msg for msg, _ in notes))
        self.assertFalse(any("监测到危险操作" in msg for msg, _ in notes))

    def test_new_manual_after_flatten_is_danger(self):
        leftover = _pos(manual=False, margin_usdt=200, leverage=10)
        fp.protect_once(
            close_fn=lambda *a, **k: {"ok": True, "closed": True},
            notify_fn=lambda *a, **k: {"ok": True, "sent": True},
            listed=self._listed([leftover]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: {"ok": True, "skipped": True},
            trading_fn=lambda: self._equity(),
            now_ts=self._sat_after_close(),
        )
        newbie = _pos(
            manual=True, source="manual_okx", strategy_key="",
            margin_usdt=90, leverage=5, position_id="manual-after-close",
        )
        closed = []
        notes = []
        out = fp.protect_once(
            close_fn=lambda inst_id, side, reason=None, mgn_mode=None: (
                closed.append((inst_id, side, reason)) or {"ok": True, "closed": True}
            ),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True, "sent": True},
            listed=self._listed([newbie]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: {"ok": True, "skipped": True},
            trading_fn=lambda: self._equity(),
            now_ts=self._sat_after_close() + 600,
        )
        self.assertEqual(1, len(closed))
        self.assertTrue((closed[0][2] or "").endswith("manual_open_outside_session"))
        self.assertGreaterEqual(int(out.get("danger_count") or 0), 1)
        self.assertTrue(any(
            (meta or {}).get("kind") == "force_protect_intervene" for _, meta in notes
        ))

    def test_in_session_does_not_flatten_clean_book(self):
        auto = _pos(manual=False, margin_usdt=200, leverage=10)
        notes = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("should not close in session"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True, "sent": True},
            listed=self._listed([auto]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
            now_ts=self._mon_in_session(),
        )
        self.assertTrue(out.get("ok"))
        self.assertFalse(out.get("session_close"))
        self.assertFalse(out.get("intervened"))
        self.assertTrue(out.get("session_open"))
        self.assertEqual(1, len(notes))
        self.assertEqual("force_protect_session_open", (notes[0][1] or {}).get("kind"))
        self.assertIn("交易日开始", notes[0][0])

    def test_session_open_notifies_once_per_week(self):
        notes = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("should not close on open bell"),
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True, "sent": True},
            listed=self._listed([]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
            now_ts=self._mon_in_session(),
        )
        self.assertTrue(out.get("session_open"))
        self.assertEqual(1, len(notes))
        self.assertEqual("force_protect_session_open", (notes[0][1] or {}).get("kind"))
        self.assertIn("交易日开始", notes[0][0])
        self.assertEqual(
            "2026-08-24", fp._load_state().get("session_open_notified_week"))
        later = []
        out2 = fp.protect_once(
            close_fn=lambda *a, **k: self.fail("should not close on open bell"),
            notify_fn=lambda msg, meta: later.append((msg, meta)) or {"ok": True, "sent": True},
            listed=self._listed([]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: self.fail("no transfer"),
            trading_fn=lambda: self._equity(),
            now_ts=self._mon_in_session() + 3600,
        )
        self.assertFalse(out2.get("session_open"))
        self.assertEqual([], later)

    def test_session_close_notifies_on_empty_book(self):
        notes = []
        closed = []
        out = fp.protect_once(
            close_fn=lambda *a, **k: closed.append(a) or {"ok": True, "closed": True},
            notify_fn=lambda msg, meta: notes.append((msg, meta)) or {"ok": True, "sent": True},
            listed=self._listed([]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: {"ok": True, "skipped": True},
            trading_fn=lambda: self._equity(),
            now_ts=self._sat_after_close(),
        )
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("session_close"))
        self.assertEqual([], closed)
        self.assertEqual(1, len(notes))
        self.assertEqual("force_protect_session_close", (notes[0][1] or {}).get("kind"))
        self.assertIn("收盘时间到", notes[0][0])
        self.assertIn("没有持仓", notes[0][0])
        self.assertEqual(
            "2026-08-24", fp._load_state().get("session_close_notified_week"))
        later = []
        fp.protect_once(
            close_fn=lambda *a, **k: self.fail("empty book after flatten"),
            notify_fn=lambda msg, meta: later.append((msg, meta)) or {"ok": True, "sent": True},
            listed=self._listed([]),
            equity=self._equity(),
            transfer_fn=lambda *a, **k: {"ok": True, "skipped": True},
            trading_fn=lambda: self._equity(),
            now_ts=self._sat_after_close() + 600,
        )
        self.assertEqual([], later)


if __name__ == "__main__":
    unittest.main()
