from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import auto_trade_daily_report as report


def _trade(index, closed=True):
    closed_at = datetime(2026, 8, 1, 12, 0, 0) + timedelta(minutes=index)
    return {
        "token": "trade-%02d" % index,
        "symbol": "BTC-USDT-SWAP",
        "timeframe": "5m",
        "opened_at": (closed_at - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
        "closed_at": closed_at.strftime("%Y-%m-%d %H:%M:%S") if closed else None,
        "net_pnl_usdt": float(index - 10),
        "pnl_rate_pct": float(index - 10) * 2,
    }


class LatestTradeRecordsTest(unittest.TestCase):
    def test_returns_only_latest_twenty_completed_trades(self):
        rows = [_trade(index) for index in range(22)] + [_trade(99, closed=False)]
        with mock.patch.object(report, "_all_positions", return_value=rows):
            records = report.latest_trade_records(limit=20)

        self.assertEqual(20, len(records))
        self.assertEqual("trade-21", records[0]["token"])
        self.assertEqual("trade-02", records[-1]["token"])
        self.assertEqual("BTC", records[0]["symbol_label"])
        self.assertEqual("5分钟", records[0]["timeframe_label"])
        self.assertEqual(records[0]["pnl_rate_pct"], records[0]["pnl_rate_leveraged_pct"])
        self.assertEqual(records[0]["net_pnl_usdt"], records[0]["profit_amount_usdt"])

    def test_sync_persists_bounded_rolling_snapshot(self):
        rows = [_trade(index) for index in range(21)]
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "latest.json"
            with mock.patch.object(report, "LATEST_LEDGER_FILE", ledger), mock.patch.object(
                report, "_all_positions", return_value=rows
            ):
                payload = report.sync_latest_trade_records(limit=20)

            saved = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual("qiyu_latest_auto_trade_records_v1", saved["schema"])
            self.assertEqual(20, payload["record_count"])
            self.assertEqual(20, saved["max_records"])
            self.assertNotIn("trade-00", [row["token"] for row in saved["records"]])


if __name__ == "__main__":
    unittest.main()
