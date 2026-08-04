# -*- coding: utf-8 -*-
import copy
import unittest

import auto_trade_strategy_dsl as dsl


BASE = {
    "schema": "qiyu_strategy_dsl_v1",
    "key": "t_base",
    "name": "base",
    "direction": "long",
    "timeframe": "5m",
    "supported_instruments": ["ADA-USDT-SWAP"],
    "max_hold_bars": 14,
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
         "right": {"value": 2.2}},
    ]},
    "exit": {"any": [
        {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
         "right": {"value": 60}, "role": "take_profit"},
        {"id": "inv", "left": {"feature": "close"}, "op": "lt",
         "right": {"feature": "prev_low20"}, "role": "invalidation"},
    ]},
}


class NearDuplicateLogicTest(unittest.TestCase):
    def test_z_tweak_is_near_dup(self):
        a = dsl.validate_strategy(BASE)
        b = copy.deepcopy(BASE)
        b["key"] = "t_near"
        b["entry"]["all"][-1]["right"] = {"value": 2.3}
        b = dsl.validate_strategy(b)
        self.assertTrue(dsl.is_near_duplicate_logic(a, b))

    def test_different_feature_not_near(self):
        a = dsl.validate_strategy(BASE)
        b = copy.deepcopy(BASE)
        b["key"] = "t_diff"
        b["entry"]["all"][-1] = {
            "id": "z", "left": {"feature": "atr14"}, "op": "lt",
            "right": {"value": 2.2},
        }
        b = dsl.validate_strategy(b)
        self.assertFalse(dsl.is_near_duplicate_logic(a, b))

    def test_find_catalog(self):
        a = dsl.validate_strategy(BASE)
        b = copy.deepcopy(BASE)
        b["key"] = "t_near"
        b["entry"]["all"][-1]["right"] = {"value": 2.3}
        hit = dsl.find_near_duplicate(b, [a])
        self.assertIsNotNone(hit)
        self.assertEqual(hit["key"], "t_base")


if __name__ == "__main__":
    unittest.main()
