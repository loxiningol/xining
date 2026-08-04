# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import auto_trade_strategy_events as events


def _event(strategy, symbol, event_type, when):
    return {
        "strategy_id": strategy,
        "symbol": symbol,
        "timeframe": "5m",
        "event_type": event_type,
        "timestamp": when.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": when.timestamp(),
    }


class StrategyEventSummaryCacheTest(unittest.TestCase):
    def test_funnel_and_coverage_share_compact_cached_scan(self):
        now = datetime.now()
        rows = [
            _event("alpha", "SOL-USDT-SWAP", "raw_signal_generated", now - timedelta(hours=2)),
            _event("alpha", "SOL-USDT-SWAP", "order_filled", now - timedelta(hours=1)),
            _event("alpha", "SOL-USDT-SWAP", "position_opened", now - timedelta(minutes=55)),
            _event("alpha", "SOL-USDT-SWAP", "position_closed", now - timedelta(minutes=5)),
            _event("beta", "ETH-USDT-SWAP", "raw_signal_generated", now - timedelta(hours=1)),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            stream = Path(tmp) / "strategy_events.jsonl"
            stream.write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(events, "EVENT_STREAM", stream):
                events._EVENT_SUMMARY_CACHE.clear()
                funnel = events.funnel_from_events(
                    "alpha", symbol="SOL-USDT-SWAP", timeframe="5m", days=7)
                coverage = events.coverage_for_strategy("alpha", days=7)

                self.assertEqual(4, funnel["event_n"])
                self.assertEqual(1, funnel["signals"])
                self.assertEqual(1, funnel["fills"])
                self.assertEqual(4, coverage["event_n"])
                self.assertIn(7, events._EVENT_SUMMARY_CACHE)

                with stream.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(_event(
                        "alpha", "SOL-USDT-SWAP", "raw_signal_generated", now
                    )) + "\n")
                # Within the short cache window, every strategy in one forecast
                # sees the same coherent event snapshot.
                self.assertEqual(4, events.funnel_from_events(
                    "alpha", symbol="SOL-USDT-SWAP", days=7)["event_n"])
                events._EVENT_SUMMARY_CACHE.clear()
                self.assertEqual(5, events.funnel_from_events(
                    "alpha", symbol="SOL-USDT-SWAP", days=7)["event_n"])


if __name__ == "__main__":
    unittest.main()
