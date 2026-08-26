# -*- coding: utf-8 -*-
from __future__ import print_function

import unittest

import auto_trade_formal_v6_executor as ex


class CloseTypeFromReconciledTest(unittest.TestCase):
    def test_eth_manual_market_near_stop_is_manual_not_exchange_sl(self):
        # Real incident 2026-08-27: short stop 2469.48, manual market fill 2463.6
        # was within 0.4% and mislabeled 交易所止损.
        row = {
            "avgPx": "2463.6",
            "ordType": "market",
            "category": "normal",
            "algoId": "",
            "algoClOrdId": "",
            "clOrdId": "",
            "ordId": "3867594077211987968",
            "side": "buy",
            "posSide": "short",
            "reduceOnly": "true",
        }
        cur = {
            "side": "short",
            "entry_price": 2440.2,
            "stop_loss_price": "2469.48",
            "attached_stop_loss": {
                "algoId": "3867285805812486145",
                "attachAlgoClOrdId": "fasl9ef959f8462646e3bc235a9997b6",
                "stop_loss_price": "2469.48",
                "payload": {"slTriggerPx": "2469.48"},
            },
        }
        got = ex._close_type_from_reconciled(
            row, reason="exchange_position_disappeared_reconciled", cur=cur,
        )
        self.assertEqual("手动平仓", got)

    def test_algo_conditional_fill_is_exchange_sl(self):
        row = {
            "avgPx": "2470.0",
            "ordType": "conditional",
            "category": "normal",
            "algoId": "3867285805812486145",
            "clOrdId": "",
        }
        cur = {
            "side": "short",
            "attached_stop_loss": {
                "algoId": "3867285805812486145",
                "stop_loss_price": "2469.48",
            },
        }
        self.assertEqual(
            "交易所止损",
            ex._close_type_from_reconciled(row, cur=cur),
        )

    def test_fcl_strategy_close_uses_reason(self):
        row = {"clOrdId": "fcltimed123", "ordType": "market", "category": "normal"}
        self.assertEqual(
            "定时强制平仓",
            ex._close_type_from_reconciled(row, reason="timed_forced_close", cur={}),
        )


if __name__ == "__main__":
    unittest.main()
