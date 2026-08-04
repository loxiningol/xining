import unittest
from unittest import mock

import auto_trade_formal_daemon as daemon


ADA5_KEY = "codex0725t3_ada5m_trendpb_r42_z2p3_h14"
CLONE_KEYS = (
    "sol5_trend_rebound_ada5_clone_v1",
    "btc5_trend_rebound_ada5_clone_v1",
    "eth5_trend_rebound_ada5_clone_v1",
    "xrp5_trend_rebound_ada5_clone_v1",
)


class Ada5KlineCloseDelayTest(unittest.TestCase):
    def test_signal_is_staged_until_next_five_minute_boundary(self):
        cfg = {}
        signal = {
            "ok": True,
            "strategy_key": ADA5_KEY,
            "signal": "long",
            "signal_candle_id": "ada5:example",
            "entry_info": {"rsi14": 45.0},
        }

        candidate, state, changed = daemon._kline_close_delay_transition(
            cfg, signal, now_ts=30000 + 35
        )
        self.assertIsNone(candidate)
        self.assertTrue(changed)
        self.assertEqual("staged_until_kline_close", state["status"])
        self.assertEqual(30300, state["execute_after_ts"])

        candidate, state, changed = daemon._kline_close_delay_transition(
            cfg, signal, now_ts=30299.99
        )
        self.assertIsNone(candidate)
        self.assertFalse(changed)
        self.assertEqual("waiting_kline_close", state["status"])

        candidate, state, changed = daemon._kline_close_delay_transition(
            cfg, None, now_ts=30300
        )
        self.assertEqual(signal, candidate)
        self.assertFalse(changed)
        self.assertEqual("ready_after_kline_close", state["status"])

    def test_other_strategy_is_not_delayed(self):
        cfg = {}
        signal = {"strategy_key": "another_strategy", "signal": "long"}
        candidate, state, changed = daemon._kline_close_delay_transition(
            cfg, signal, now_ts=30035
        )
        self.assertIs(signal, candidate)
        self.assertIsNone(state)
        self.assertFalse(changed)

    def test_all_direct_clones_use_the_same_five_minute_close_delay(self):
        for key in CLONE_KEYS:
            with self.subTest(strategy_key=key):
                cfg = {}
                signal = {
                    "strategy_key": key,
                    "signal": "long",
                    "signal_candle_id": key + ":example",
                }
                candidate, state, changed = (
                    daemon._kline_close_delay_transition(
                        cfg, signal, now_ts=30035
                    )
                )
                self.assertIsNone(candidate)
                self.assertTrue(changed)
                self.assertEqual("staged_until_kline_close", state["status"])
                self.assertEqual(30300, state["execute_after_ts"])

    def test_stale_pending_signal_expires_after_one_execution_window(self):
        cfg = {}
        signal = {
            "strategy_key": ADA5_KEY,
            "signal": "long",
            "signal_candle_id": "ada5:stale",
        }
        daemon._kline_close_delay_transition(cfg, signal, now_ts=30035)
        candidate, state, changed = daemon._kline_close_delay_transition(
            cfg, None, now_ts=30600
        )
        self.assertIsNone(candidate)
        self.assertTrue(changed)
        self.assertEqual("expired", state["status"])
        self.assertNotIn(
            ADA5_KEY, cfg[daemon._KLINE_CLOSE_PENDING_FIELD]
        )

    def test_clone_pending_signal_is_cleared_when_position_already_exists(self):
        class Executor:
            @staticmethod
            def get_status():
                return {"current": {"strategy_key": "another_strategy"}}

            @staticmethod
            def manage_current_position(policy=None):
                return {"ok": True, "action": "observe"}

        key = CLONE_KEYS[0]
        cfg = {
            "enabled": True,
            daemon._KLINE_CLOSE_PENDING_FIELD: {
                key: {"candidate": {"strategy_key": key}}
            },
        }
        result = daemon._multi_tick_core(
            object(), Executor, cfg, persist=False, candles=[], signals=[]
        )
        self.assertEqual("position_exists_other_strategy_instance", result["action"])
        self.assertNotIn(key, cfg[daemon._KLINE_CLOSE_PENDING_FIELD])

    def test_multi_tick_does_not_submit_before_boundary_then_submits(self):
        class Executor:
            submit_calls = 0

            @staticmethod
            def get_status():
                return {"current": None}

            @staticmethod
            def enable_gate(*args, **kwargs):
                return {"ok": True}

            @staticmethod
            def gate_confirm_text():
                return "gate"

            @staticmethod
            def confirm_text(side):
                return "confirm-" + side

            @classmethod
            def submit_entry(cls, **kwargs):
                cls.submit_calls += 1
                return {"ok": True, "formal_executor": True}

        cfg = {
            "enabled": True,
            "allow_auto_open": True,
            "formal_auto_trading_authorized": True,
            "cooldown_sec_after_open": 0,
            "last_open_ts": 0,
            "strategy_keys": [],
        }
        signal = {
            "ok": True,
            "strategy_key": ADA5_KEY,
            "strategy_name": "ADA5顺势回升",
            "signal": "long",
            "signal_candle_id": "ada5:integration",
            "entry_info": {},
            "strategy_rating": {"grade": "B"},
        }
        with mock.patch.object(daemon, "_strategy_runtime_allowed", return_value=True):
            staged = daemon._multi_tick_core(
                object(), Executor, cfg, persist=False, candles=[], signals=[signal]
            )
        self.assertEqual("signal_waiting_kline_close", staged["action"])
        self.assertEqual(0, Executor.submit_calls)

        pending = cfg[daemon._KLINE_CLOSE_PENDING_FIELD][ADA5_KEY]
        pending["execute_after_ts"] = 1
        pending["expires_at_ts"] = 4102444800
        due = daemon._multi_tick_core(
            object(), Executor, cfg, persist=False, candles=[], signals=[]
        )
        self.assertEqual("auto_open_attempted_gate_authorized", due["action"])
        self.assertEqual("ready_after_kline_close", due["kline_close_entry_delay"]["status"])
        self.assertEqual(1, Executor.submit_calls)


if __name__ == "__main__":
    unittest.main()
