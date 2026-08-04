# -*- coding: utf-8 -*-
"""Download, validate and document complete confirmed OKX candle archives."""
import argparse
import hashlib
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ENDPOINT = "https://www.okx.com/api/v5/market/history-candles"
TIMEFRAMES = {
    "1h": {"bar": "1H", "delta": pd.Timedelta(hours=1)},
    "15m": {"bar": "15m", "delta": pd.Timedelta(minutes=15)},
    "5m": {"bar": "5m", "delta": pd.Timedelta(minutes=5)},
}


def request_page(instrument, bar, cursor=None, attempts=6):
    params = {"instId": instrument, "bar": bar, "limit": "300"}
    if cursor is not None:
        params["after"] = str(cursor)
    url = ENDPOINT + "?" + urllib.parse.urlencode(params)
    last_error = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "qiyu-dual-timeframe-history/1.0"}
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if str(payload.get("code")) != "0":
                raise RuntimeError(
                    payload.get("msg") or "OKX returned non-zero code"
                )
            return payload.get("data") or []
        except Exception as exc:
            last_error = exc
            time.sleep(min(3.0, 0.35 * (attempt + 1)))
    raise RuntimeError("OKX history request failed: %s" % last_error)


def download(instrument, timeframe, max_pages):
    spec = TIMEFRAMES[timeframe]
    rows = {}
    cursor = None
    for page in range(1, max_pages + 1):
        batch = request_page(instrument, spec["bar"], cursor)
        oldest = None
        added = 0
        for item in batch:
            if (
                not isinstance(item, list)
                or len(item) < 9
                or str(item[8]) != "1"
            ):
                continue
            timestamp_ms = int(item[0])
            oldest = (
                timestamp_ms
                if oldest is None
                else min(oldest, timestamp_ms)
            )
            if timestamp_ms not in rows:
                rows[timestamp_ms] = {
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                }
                added += 1
        if oldest is None or added == 0 or oldest == cursor:
            break
        cursor = oldest
        if page % 20 == 0:
            print(
                "instrument=%s timeframe=%s page=%d rows=%d earliest=%s"
                % (
                    instrument,
                    timeframe,
                    page,
                    len(rows),
                    pd.to_datetime(
                        cursor, unit="ms", utc=True
                    ).isoformat(),
                ),
                flush=True,
            )
        time.sleep(0.13)
    if not rows:
        raise RuntimeError("OKX returned no confirmed candles")
    frame = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    frame.index = (
        pd.to_datetime(frame.index, unit="ms", utc=True)
        .tz_localize(None)
    )
    frame.index.name = "timestamp"
    return frame


def validate(frame, timeframe, existing=None):
    expected = TIMEFRAMES[timeframe]["delta"]
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("timestamp index is duplicated or unsorted")
    values = frame[["open", "high", "low", "close"]]
    if values.isnull().values.any():
        raise ValueError("OHLC contains NaN")
    if (values <= 0).values.any():
        raise ValueError("OHLC contains non-positive values")
    if (frame["high"] < frame[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("high price invariant failed")
    if (frame["low"] > frame[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("low price invariant failed")
    deltas = frame.index.to_series().diff().dropna()
    gaps = deltas[deltas != expected]
    overlap = {
        "rows": 0,
        "mismatched_rows": 0,
        "max_abs_difference": 0.0,
    }
    if existing and Path(existing).exists():
        old = pd.read_parquet(existing)
        if not isinstance(old.index, pd.DatetimeIndex):
            old["timestamp"] = pd.to_datetime(old["timestamp"])
            old = old.set_index("timestamp")
        common = frame.index.intersection(old.index)
        if len(common):
            left = frame.loc[
                common, ["open", "high", "low", "close"]
            ].astype(float)
            right = old.loc[
                common, ["open", "high", "low", "close"]
            ].astype(float)
            difference = (left - right).abs()
            row_mismatch = (difference > 1e-9).any(axis=1)
            overlap = {
                "rows": int(len(common)),
                "mismatched_rows": int(row_mismatch.sum()),
                "max_abs_difference": float(difference.max().max()),
                "revision_policy": (
                    "use_current_official_okx_archive_and_record_revision"
                ),
            }
    return {
        "rows": int(len(frame)),
        "earliest_utc": frame.index.min().isoformat(),
        "latest_utc": frame.index.max().isoformat(),
        "expected_interval_seconds": float(expected.total_seconds()),
        "non_expected_gap_count": int(len(gaps)),
        "largest_gap_seconds": (
            float(deltas.max().total_seconds()) if len(deltas) else 0.0
        ),
        "overlap": overlap,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instrument", required=True)
    parser.add_argument(
        "--timeframe", required=True, choices=sorted(TIMEFRAMES)
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--existing")
    parser.add_argument("--max-pages", type=int, default=1200)
    args = parser.parse_args()

    instrument = args.instrument.strip().upper()
    frame = download(instrument, args.timeframe, args.max_pages)
    report = validate(frame, args.timeframe, existing=args.existing)
    output = Path(args.output)
    metadata = Path(args.metadata)
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output)
    report.update(
        {
            "source": "OKX public REST API",
            "endpoint": ENDPOINT,
            "instrument": instrument,
            "timeframe": args.timeframe,
            "bar": TIMEFRAMES[args.timeframe]["bar"],
            "confirmed_candles_only": True,
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "parquet_sha256": hashlib.sha256(
                output.read_bytes()
            ).hexdigest(),
        }
    )
    metadata.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
