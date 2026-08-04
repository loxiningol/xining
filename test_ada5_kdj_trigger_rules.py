import copy
import os
import tempfile
import unittest

import pandas as pd

import auto_trade_strategy_dsl as dsl

_HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault(
    "VECTOR_BACKTEST_LOG_DIR",
    os.path.join(tempfile.gettempdir(), "qiyu_ada5_kdj_test_logs"),
)
os.environ.setdefault(
    "VECTOR_STRATEGY_CONFIG_PATH",
    os.path.join(_HERE, "experimental_strategies.json"),
)
os.environ.setdefault(
    "VECTOR_DSL_STRATEGY_CONFIG_PATH",
    os.path.join(_HERE, "ai_dsl_strategies.json"),
)
import backtest_engine_v2 as bt


ADA5_KEY = "codex0725t3_ada5m_trendpb_r42_z2p3_h14"


def ada5_definition():
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": ADA5_KEY,
        "name": "ADA5顺势回升",
        "direction": "long",
        "timeframe": "5m",
        "supported_instruments": ["ADA-USDT-SWAP"],
        "entry": {"all": [
            {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "gt",
             "right": {"feature": "h1_ema53"}},
            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gt",
             "right": {"value": 0}},
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_above",
             "right": {"value": 42}},
            {"id": "px", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "ema21"}},
            {"id": "z", "left": {"feature": "z20"}, "op": "lt",
             "right": {"value": 2.3}},
            {"id": "trigger_k_gte_d", "left": {"feature": "k"}, "op": "gte",
             "right": {"feature": "d"}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
             "right": {"value": 60}, "role": "take_profit"},
            {"id": "tp_j_by_trigger_j", "left": {"feature": "j"}, "op": "gte",
             "right": {"entry_tiered": {
                 "feature": "j", "split": 50, "lt": 89, "gt": 102.35,
             }}, "role": "take_profit"},
            {"id": "tp_k_gt_90", "left": {"feature": "k"}, "op": "gt",
             "right": {"value": 90}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "prev_low20"}, "role": "invalidation"},
        ]},
        "max_hold_bars": 14,
        "live_enabled": True,
    }


class Ada5KdjTriggerRulesTest(unittest.TestCase):
    def setUp(self):
        self.definition = dsl.validate_strategy(ada5_definition())

    def _frame(self, **last):
        rows = [
            {"h1_ema19": 2, "h1_ema53": 1, "h1_slope4": 1,
             "rsi14": 41, "close": 10, "ema21": 9, "z20": 1,
             "k": 45, "d": 40, "j": 40, "prev_low20": 8,
             "open": 9, "high": 11, "low": 8.5},
            {"h1_ema19": 2, "h1_ema53": 1, "h1_slope4": 1,
             "rsi14": 43, "close": 10, "ema21": 9, "z20": 1,
             "k": 55, "d": 50, "j": 50, "prev_low20": 8,
             "open": 9, "high": 11, "low": 8.5},
        ]
        rows[-1].update(last)
        return pd.DataFrame(rows, index=pd.to_datetime([
            "2026-08-02T00:00:00Z", "2026-08-02T00:05:00Z"
        ]))

    def test_entry_requires_trigger_k_greater_than_or_equal_to_d(self):
        frame = self._frame(k=49, d=50)
        met, _ = dsl.evaluate_strategy(frame, 1, self.definition, phase="entry")
        self.assertFalse(met)
        frame = self._frame(k=50, d=50)
        met, _ = dsl.evaluate_strategy(frame, 1, self.definition, phase="entry")
        self.assertTrue(met)

    def test_trigger_j_below_50_uses_89(self):
        frame = self._frame(rsi14=50, k=50, j=89, close=10)
        met, details = dsl.evaluate_strategy(
            frame, 1, self.definition, phase="exit",
            explain=True,
            position={"entry_features": {"j": 49}},
        )
        self.assertTrue(met)
        row = next(x for x in details if x["condition_id"] == "tp_j_by_trigger_j")
        self.assertEqual(89.0, row["right"])

    def test_trigger_j_above_50_uses_102_35(self):
        frame = self._frame(rsi14=50, k=50, j=102.34, close=10)
        met, _ = dsl.evaluate_strategy(
            frame, 1, self.definition, phase="exit",
            position={"entry_features": {"j": 50.01}},
        )
        self.assertFalse(met)
        frame.loc[frame.index[-1], "j"] = 102.35
        met, _ = dsl.evaluate_strategy(
            frame, 1, self.definition, phase="exit",
            position={"entry_features": {"j": 50.01}},
        )
        self.assertTrue(met)

    def test_trigger_j_equal_50_does_not_choose_a_j_threshold(self):
        frame = self._frame(rsi14=50, k=50, j=120, close=10)
        met, details = dsl.evaluate_strategy(
            frame, 1, self.definition, phase="exit",
            explain=True,
            position={"entry_features": {"j": 50}},
        )
        self.assertFalse(met)
        row = next(x for x in details if x["condition_id"] == "tp_j_by_trigger_j")
        self.assertIn("strict split", row["error"])

    def test_k_strictly_above_90_is_an_independent_take_profit(self):
        frame = self._frame(rsi14=50, k=90, j=20, close=10)
        met, _ = dsl.evaluate_strategy(
            frame, 1, self.definition, phase="exit",
            position={"entry_features": {"j": 50}},
        )
        self.assertFalse(met)
        frame.loc[frame.index[-1], "k"] = 91
        met, _ = dsl.evaluate_strategy(
            frame, 1, self.definition, phase="exit",
            position={"entry_features": {"j": 50}},
        )
        self.assertTrue(met)

    def test_live_factory_freezes_trigger_snapshot_for_tiered_exit(self):
        frame = self._frame(k=55, d=50, j=49)
        entry_f = bt._dsl_entry_factory(self.definition)
        met, info = entry_f(
            frame.open.tolist(), frame.close.tolist(), frame.high.tolist(),
            frame.low.tolist(), 1, {}, _dsl_frame=frame,
        )
        self.assertTrue(met)
        self.assertEqual({"k": 55.0, "d": 50.0, "j": 49.0},
                         info["_dsl_signal_features"])
        exit_frame = copy.deepcopy(frame)
        exit_frame.loc[exit_frame.index[-1], ["rsi14", "k", "j"]] = [50, 50, 89]
        exit_f = bt._dsl_exit_factory(self.definition)
        should_exit, exit_info = exit_f(
            exit_frame.close.tolist(), exit_frame.high.tolist(),
            exit_frame.low.tolist(), 1, info, {}, _dsl_frame=exit_frame,
        )
        self.assertTrue(should_exit)
        self.assertEqual("DSL策略止盈", exit_info["exit_type"])


if __name__ == "__main__":
    unittest.main()
