import time
import unittest
from unittest import mock

import backtest_engine_v2 as backtest
import auto_trade_strategy_ema6_center_down as live


NG5_KEY = "ng5_exhaustion_fade_short_ai"


class Ng5KTakeProfitTest(unittest.TestCase):
    def setUp(self):
        self.params = {
            "take_profit_price_ratio": 0.009,
            "take_profit_k_max": 20,
            "max_hold_bars": 72,
        }
        self.entry = {"price": 100.0, "entry_signal_idx": 1}

    def _exit(self, k_value, low=100.0):
        return backtest.exit_ng5_exhaustion_fade_short_ai_p(
            [100.0, 100.0], [100.0, 100.0], [100.0, low], 1,
            self.entry, self.params, k_arr=[30.0, k_value]
        )

    def test_k_equal_twenty_triggers_or_take_profit(self):
        met, info = self._exit(20.0)
        self.assertTrue(met)
        self.assertEqual("K指标止盈", info["exit_type"])
        self.assertEqual(20.0, info["k"])

    def test_k_above_twenty_does_not_trigger_new_branch(self):
        met, info = self._exit(20.01)
        self.assertFalse(met)
        self.assertEqual({}, info)

    def test_original_price_take_profit_remains(self):
        met, info = self._exit(30.0, low=99.0)
        self.assertTrue(met)
        self.assertEqual("价格止盈", info["exit_type"])

    def test_live_adapter_honors_k_exit_when_price_target_not_met(self):
        evaluation = {
            "exit": True,
            "exit_info": {
                "exit_type": "K指标止盈",
                "k": 20.0,
                "take_profit_k_max": 20.0,
            },
            "params": self.params,
            "candle_ts": 1,
            "candle_id": "NG-USDT-SWAP:5m:1",
        }
        current = {
            "entry_price": 100.0,
            "opened_at_ts": time.time(),
            "entry_data": {"price": 100.0},
        }
        ticker = {"code": "0", "data": [{"last": "99.5"}]}
        with mock.patch.object(live, "_evaluate", return_value=evaluation), mock.patch.object(
            live, "_okx_request", return_value=ticker
        ):
            result = live.should_close_position_for(
                NG5_KEY, current=current, candles=[{"ts": 1}]
            )
        self.assertTrue(result["should_close"])
        self.assertEqual("k_indicator_take_profit", result["reason"])
        self.assertEqual("K指标止盈", result["exit_info"]["exit_type"])


if __name__ == "__main__":
    unittest.main()
