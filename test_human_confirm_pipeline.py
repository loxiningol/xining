# -*- coding: utf-8 -*-
"""Unit tests for human-confirm S/A/B/C grade monitor + freeze helpers."""
from __future__ import print_function

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class HumanConfirmPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.auto = self.root / "auto_trade"
        self.auto.mkdir(parents=True)
        (self.root / "strategy_configs").mkdir(parents=True)
        self.env = {
            "VECTOR_ROOT": str(self.root),
        }
        self._patches = [
            mock.patch.dict(os.environ, self.env, clear=False),
        ]
        for p in self._patches:
            p.start()
        import importlib
        import auto_trade_human_confirm_pipeline as p
        importlib.reload(p)
        self.p = p
        self.wx = []
        self.p._wx = lambda text, kind="x", meta=None: self.wx.append(
            {"text": text, "kind": kind, "meta": meta or {}})

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def _write_controls(self, assignments):
        path = self.p.CONTROL_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"assignments": assignments}, indent=2),
                        encoding="utf-8")

    def _write_history(self, key, trades):
        path = self.auto / "formal_v6_state_test.json"
        history = []
        for i, t in enumerate(trades):
            equity = t.get("equity", 1000.0)
            pnl = t.get("pnl", -1)
            history.append({
                "strategy_key": key,
                "opened_at": t.get("opened_at") or ("2026-07-24 09:%02d:00" % i),
                "closed_at": t.get("closed_at") or ("2026-07-24 10:%02d:00" % i),
                "pnl": pnl,
                "sizing": {"account_equity_usdt": equity},
                "close_type": t.get("close_type", "交易所止损"),
                "close_reason": t.get("close_reason", "exchange_sl"),
            })
        path.write_text(json.dumps({"history": history}, indent=2),
                        encoding="utf-8")

    def test_grade_ratios(self):
        self.assertEqual(self.p.GRADE_RATIO["S"], 0.70)
        self.assertEqual(self.p.GRADE_RATIO["A"], 0.50)
        self.assertEqual(self.p.GRADE_RATIO["B"], 0.30)
        self.assertEqual(self.p.GRADE_RATIO["C"], 0.10)
        self.assertEqual(self.p.LEVERAGE, 20)

    def test_forged_approved_true_cannot_enter_pending_queue(self):
        out = self.p.enqueue_for_human(
            {"dsl": {"key": "forged", "entry": {}, "exit": {}},
             "symbol": "BTC-USDT-SWAP", "timeframe": "15m"},
            {"key": "forged", "symbol": "BTC-USDT-SWAP", "timeframe": "15m"},
            source="unit_test",
            ai_review={"approved": True,
                       "statistical_weekly_opens_expected": 99.0},
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "verified_four_review_required")
        pending = self.p.load_pending()
        self.assertFalse(any(x.get("key") == "forged"
                             for x in pending.get("items") or []))

    def test_legacy_frost2_source_cannot_enter_pending_queue(self):
        out = self.p.verify_three_ai_review_for_pending({}, source="frost2")
        self.assertFalse(out["ok"])
        self.assertIn("legacy_review_source_blocked:frost2", out.get("reasons") or [])

    def test_b_first3_two_stops_downgrade_c(self):
        key = "demo_b_strategy"
        aid = "BTC-USDT-SWAP|15m|%s" % key
        self._write_controls({
            aid: {
                "strategy_key": key,
                "symbol": "BTC-USDT-SWAP",
                "timeframe": "15m",
                "human_confirm_pipeline": True,
                "human_confirmed": True,
                "human_confirmed_at": "2026-07-24 09:00:00",
                "lifecycle_grade": "B",
                "max_position_ratio": 0.30,
                "grade_window": "B_first3",
                "grade_window_closed": [],
                "audit_state": "conditional_frequency_probe",
                "pause_new_entries": False,
            }
        })
        self._write_history(key, [
            {"closed_at": "2026-07-24 10:01:00", "pnl": -10,
             "close_type": "交易所止损"},
            {"closed_at": "2026-07-24 10:02:00", "pnl": 5,
             "close_type": "策略止盈"},
            {"closed_at": "2026-07-24 10:03:00", "pnl": -12,
             "close_type": "交易所止损"},
        ])
        out = self.p.monitor_live_grades()
        self.assertTrue(any(a.get("action") == "downgrade_c"
                            for a in (out.get("actions") or [])))
        row = json.loads(self.p.CONTROL_PATH.read_text(encoding="utf-8"))[
            "assignments"][aid]
        self.assertEqual(row["lifecycle_grade"], "C")
        self.assertEqual(row["max_position_ratio"], 0.10)

    def test_c_next3_two_stops_delete(self):
        key = "demo_c_strategy"
        aid = "BTC-USDT-SWAP|15m|%s" % key
        self._write_controls({
            aid: {
                "strategy_key": key,
                "symbol": "BTC-USDT-SWAP",
                "timeframe": "15m",
                "human_confirm_pipeline": True,
                "human_confirmed": True,
                "human_confirmed_at": "2026-07-24 08:00:00",
                "downgraded_to_c_at": "2026-07-24 09:00:00",
                "lifecycle_grade": "C",
                "max_position_ratio": 0.10,
                "grade_window": "C_next3",
                "grade_window_closed": [],
                "audit_state": "conditional_frequency_probe",
                "pause_new_entries": False,
            }
        })
        dsl_path = self.p.DSL_CONFIG_PATH
        dsl_path.write_text(json.dumps({
            "strategies": [{
                "key": key, "name": key, "live_enabled": True,
                "auto_trade_eligible": True, "timeframe": "15m",
                "supported_instruments": ["BTC-USDT-SWAP"],
                "direction": "long", "entry": {"all": []}, "exit": {},
            }]
        }), encoding="utf-8")
        (self.auto / "formal_daemon_config_btc_15m.json").write_text(
            json.dumps({"strategy_keys": [key], "enabled": True}),
            encoding="utf-8")
        self._write_history(key, [
            {"closed_at": "2026-07-24 10:11:00", "pnl": -1,
             "close_type": "交易所止损"},
            {"closed_at": "2026-07-24 10:12:00", "pnl": -2,
             "close_type": "交易所止损"},
            {"closed_at": "2026-07-24 10:13:00", "pnl": 3,
             "close_type": "策略止盈"},
        ])
        out = self.p.monitor_live_grades()
        self.assertTrue(any(a.get("action") == "delete"
                            for a in (out.get("actions") or [])))
        row = json.loads(self.p.CONTROL_PATH.read_text(encoding="utf-8"))[
            "assignments"][aid]
        self.assertEqual(row["lifecycle_grade"], "deleted")

    def test_b_promote_lane_still_downgrades_on_stops(self):
        """B must not become immune to demotion after first-3 survival."""
        key = "demo_survived"
        aid = "BTC-USDT-SWAP|15m|%s" % key
        self._write_controls({
            aid: {
                "strategy_key": key,
                "human_confirm_pipeline": True,
                "human_confirmed": True,
                "human_confirmed_at": "2026-07-24 09:00:00",
                "lifecycle_grade": "B",
                "max_position_ratio": 0.30,
                "grade_window": "B_promote",
                "promote_window_closed": [],
                "stop_window_closed": [],
                "audit_state": "conditional_frequency_probe",
            }
        })
        self._write_history(key, [
            {"closed_at": "2026-07-24 11:01:00", "pnl": -1,
             "close_type": "交易所止损"},
            {"closed_at": "2026-07-24 11:02:00", "pnl": -2,
             "close_type": "交易所止损"},
            {"closed_at": "2026-07-24 11:03:00", "pnl": -3,
             "close_type": "交易所止损"},
        ])
        out = self.p.monitor_live_grades()
        self.assertTrue(any(a.get("action") == "downgrade_c"
                            for a in (out.get("actions") or [])))
        row = json.loads(self.p.CONTROL_PATH.read_text(encoding="utf-8"))[
            "assignments"][aid]
        self.assertEqual(row["lifecycle_grade"], "C")
        self.assertEqual(row["max_position_ratio"], 0.10)

    def test_nested_exchange_fill_pnl_counts_take_profit_as_win(self):
        key = "nested_fill_strategy"
        history = [{
            "strategy_key": key,
            "symbol": "BTC-USDT-SWAP",
            "timeframe": "15m",
            "opened_at": "2026-07-24 10:00:00",
            "closed_at": "2026-07-24 10:10:00",
            "pnl": None,
            "sizing": {"account_equity_usdt": 1000.0},
            "open_order": {"filled": {"order": {"fee": "-0.10"}}},
            "close_order": {"filled": {"order": {
                "ordId": "close-1", "instId": "BTC-USDT-SWAP",
                "pnl": "8.0", "fee": "-0.10",
            }}},
            "close_reason": "strategy_take_profit_authoritative_exit",
        }]
        (self.auto / "formal_v6_state_nested.json").write_text(
            json.dumps({"history": history}), encoding="utf-8")
        rows = self.p._closed_trades_for(
            key, symbol="BTC-USDT-SWAP", timeframe="15m")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["profit"])
        self.assertAlmostEqual(rows[0]["pnl"], 7.8)
        self.assertAlmostEqual(rows[0]["account_return_pct"], 0.78)

    def test_mounted_legacy_strategy_is_initialized_b_and_managed(self):
        key = "legacy_live_strategy"
        aid = "NG-USDT-SWAP|5m|%s" % key
        self._write_controls({
            aid: {
                "strategy_key": key, "symbol": "NG-USDT-SWAP",
                "timeframe": "5m", "audit_state": "conditional_frequency_probe",
                "pause_new_entries": False, "new_entries_allowed": True,
            }
        })
        (self.auto / "formal_daemon_config_ng_5m.json").write_text(
            json.dumps({
                "enabled": True, "allow_auto_open": True,
                "symbol": "NG-USDT-SWAP", "timeframe": "5m",
                "strategy_keys": [key],
            }), encoding="utf-8")
        out = self.p.monitor_live_grades()
        self.assertTrue(any(a.get("action") == "initialized_b"
                            for a in out.get("actions") or []))
        row = json.loads(self.p.CONTROL_PATH.read_text(encoding="utf-8"))[
            "assignments"][aid]
        self.assertEqual(row["lifecycle_grade"], "B")
        self.assertEqual(row["max_position_ratio"], 0.30)
        self.assertEqual(row["grade_managed_by"], "trade_outcome_state_machine")

    def test_c_can_recover_to_b_after_four_three_wins(self):
        key = "recover_c_strategy"
        aid = "BTC-USDT-SWAP|15m|%s" % key
        self._write_controls({
            aid: {
                "strategy_key": key,
                "human_confirm_pipeline": True,
                "human_confirmed": True,
                "grade_started_at": "2026-07-24 09:00:00",
                "lifecycle_grade": "C",
                "max_position_ratio": 0.10,
                "grade_window": "C_next3",
                "promote_window_closed": [],
                "stop_window_closed": [],
                "audit_state": "conditional_frequency_probe",
            }
        })
        self._write_history(key, [
            {"closed_at": "2026-07-24 10:01:00", "pnl": 10,
             "close_type": "策略止盈"},
            {"closed_at": "2026-07-24 10:02:00", "pnl": 12,
             "close_type": "策略止盈"},
            {"closed_at": "2026-07-24 10:03:00", "pnl": -1,
             "close_type": "普通策略退出"},
            {"closed_at": "2026-07-24 10:04:00", "pnl": 8,
             "close_type": "策略止盈"},
        ])
        out = self.p.monitor_live_grades()
        self.assertTrue(any(a.get("action") == "promote_b"
                            for a in out.get("actions") or []))
        row = json.loads(self.p.CONTROL_PATH.read_text(encoding="utf-8"))[
            "assignments"][aid]
        self.assertEqual(row["lifecycle_grade"], "B")
        self.assertEqual(row["max_position_ratio"], 0.30)

    def test_failed_grade_wx_is_queued_and_retried(self):
        self.p._wx = lambda *args, **kwargs: {
            "ok": False, "sent": False, "error": "network"
        }
        result = self.p._send_grade_notification(
            "evt-1", "grade changed", "strategy_promote_a", {"key": "x"})
        self.assertFalse(result["delivered"])
        queued = json.loads(
            self.p.GRADE_NOTIFICATION_OUTBOX.read_text(encoding="utf-8"))
        self.assertEqual(len(queued["items"]), 1)
        self.p._wx = lambda *args, **kwargs: {"ok": True, "sent": True}
        retry = self.p._retry_grade_notifications()
        self.assertEqual(retry["delivered"], 1)
        queued = json.loads(
            self.p.GRADE_NOTIFICATION_OUTBOX.read_text(encoding="utf-8"))
        self.assertEqual(queued["items"], [])

    def test_b_to_a_promote(self):
        key = "demo_b_promote"
        aid = "BTC-USDT-SWAP|15m|%s" % key
        self._write_controls({
            aid: {
                "strategy_key": key,
                "human_confirm_pipeline": True,
                "human_confirmed": True,
                "human_confirmed_at": "2026-07-24 09:00:00",
                "lifecycle_grade": "B",
                "max_position_ratio": 0.30,
                "grade_window": "B_promote",
                "promote_window_closed": [],
                "audit_state": "conditional_frequency_probe",
            }
        })
        self._write_history(key, [
            {"closed_at": "2026-07-24 11:01:00", "pnl": 10,
             "close_type": "策略止盈"},
            {"closed_at": "2026-07-24 11:02:00", "pnl": 12,
             "close_type": "策略止盈"},
            {"closed_at": "2026-07-24 11:03:00", "pnl": -3,
             "close_type": "交易所止损"},
            {"closed_at": "2026-07-24 11:04:00", "pnl": 8,
             "close_type": "策略止盈"},
        ])
        out = self.p.monitor_live_grades()
        self.assertTrue(any(a.get("action") == "promote_a"
                            for a in (out.get("actions") or [])))
        row = json.loads(self.p.CONTROL_PATH.read_text(encoding="utf-8"))[
            "assignments"][aid]
        self.assertEqual(row["lifecycle_grade"], "A")
        self.assertEqual(row["max_position_ratio"], 0.50)
        self.assertTrue(any("晋升至A级" in (w.get("text") or "") for w in self.wx))

    def test_a_to_s_promote(self):
        key = "demo_a_promote"
        aid = "BTC-USDT-SWAP|15m|%s" % key
        self._write_controls({
            aid: {
                "strategy_key": key,
                "human_confirm_pipeline": True,
                "human_confirmed": True,
                "human_confirmed_at": "2026-07-24 09:00:00",
                "lifecycle_grade": "A",
                "max_position_ratio": 0.50,
                "grade_window": "A_run",
                "promote_window_closed": [],
                "stop_window_closed": [],
                "audit_state": "conditional_frequency_probe",
            }
        })
        # 4 trades, 3 wins, mean equity return = (8+9+(-1)+10)/4 = 6.5% > 5
        self._write_history(key, [
            {"closed_at": "2026-07-24 12:01:00", "pnl": 80, "equity": 1000,
             "close_type": "策略止盈"},
            {"closed_at": "2026-07-24 12:02:00", "pnl": 90, "equity": 1000,
             "close_type": "策略止盈"},
            {"closed_at": "2026-07-24 12:03:00", "pnl": -10, "equity": 1000,
             "close_type": "交易所止损"},
            {"closed_at": "2026-07-24 12:04:00", "pnl": 100, "equity": 1000,
             "close_type": "策略止盈"},
        ])
        out = self.p.monitor_live_grades()
        self.assertTrue(any(a.get("action") == "promote_s"
                            for a in (out.get("actions") or [])))
        row = json.loads(self.p.CONTROL_PATH.read_text(encoding="utf-8"))[
            "assignments"][aid]
        self.assertEqual(row["lifecycle_grade"], "S")
        self.assertEqual(row["max_position_ratio"], 0.70)
        self.assertTrue(any("晋升至S级" in (w.get("text") or "") for w in self.wx))

    def test_s_to_a_demote(self):
        key = "demo_s_demote"
        aid = "BTC-USDT-SWAP|15m|%s" % key
        self._write_controls({
            aid: {
                "strategy_key": key,
                "human_confirm_pipeline": True,
                "human_confirmed": True,
                "human_confirmed_at": "2026-07-24 09:00:00",
                "lifecycle_grade": "S",
                "max_position_ratio": 0.70,
                "grade_window": "S_run",
                "stop_window_closed": [],
                "promote_window_closed": [],
                "audit_state": "conditional_frequency_probe",
            }
        })
        self._write_history(key, [
            {"closed_at": "2026-07-24 13:01:00", "pnl": -1,
             "close_type": "交易所止损"},
            {"closed_at": "2026-07-24 13:02:00", "pnl": 5,
             "close_type": "策略止盈"},
            {"closed_at": "2026-07-24 13:03:00", "pnl": -2,
             "close_type": "交易所止损"},
        ])
        out = self.p.monitor_live_grades()
        self.assertTrue(any(a.get("action") == "downgrade_a"
                            for a in (out.get("actions") or [])))
        row = json.loads(self.p.CONTROL_PATH.read_text(encoding="utf-8"))[
            "assignments"][aid]
        self.assertEqual(row["lifecycle_grade"], "A")
        self.assertEqual(row["max_position_ratio"], 0.50)

    def test_a_to_b_demote(self):
        key = "demo_a_demote"
        aid = "BTC-USDT-SWAP|15m|%s" % key
        self._write_controls({
            aid: {
                "strategy_key": key,
                "human_confirm_pipeline": True,
                "human_confirmed": True,
                "human_confirmed_at": "2026-07-24 09:00:00",
                "lifecycle_grade": "A",
                "max_position_ratio": 0.50,
                "grade_window": "A_run",
                "stop_window_closed": [],
                "promote_window_closed": [],
                "audit_state": "conditional_frequency_probe",
            }
        })
        self._write_history(key, [
            {"closed_at": "2026-07-24 14:01:00", "pnl": -1,
             "close_type": "交易所止损"},
            {"closed_at": "2026-07-24 14:02:00", "pnl": -2,
             "close_type": "交易所止损"},
            {"closed_at": "2026-07-24 14:03:00", "pnl": 3,
             "close_type": "策略止盈"},
        ])
        out = self.p.monitor_live_grades()
        self.assertTrue(any(a.get("action") == "downgrade_b"
                            for a in (out.get("actions") or [])))
        row = json.loads(self.p.CONTROL_PATH.read_text(encoding="utf-8"))[
            "assignments"][aid]
        self.assertEqual(row["lifecycle_grade"], "B")
        self.assertEqual(row["grade_window"], "B_promote")
        self.assertEqual(row["max_position_ratio"], 0.30)

    def test_freeze_flag(self):
        with mock.patch("subprocess.call", return_value=0):
            out = self.p.freeze_mass_and_ed_probes()
        self.assertTrue(out.get("ok"))
        self.assertTrue(self.p.mass_is_frozen())


if __name__ == "__main__":
    unittest.main()
