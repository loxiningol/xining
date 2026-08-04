# -*- coding: utf-8 -*-
"""Locked contract for Phase0-region → Formal conversion (WR floor 0.65)."""
from __future__ import print_function

LEVERAGE = 20
STOP_PRICE_PCT = 0.005
TARGET_PRICE_PCT = 0.005555

WIN_RATE_MIN = 0.65  # locked; do NOT raise to 0.70
MEAN_WIN_LEVERED_NET_MIN = 0.1111
WEEKLY_FREQ_MIN = 0.10  # locked; do NOT raise to 0.5
MIN_RESOLVED = 20  # soft for small OOS folds; region-level still reports raw n

FEE_RATE_PER_SIDE = 0.0005
SLIP_RATE_PER_SIDE = 0.0002
ROUND_TRIP_PRICE_COST = 2.0 * (FEE_RATE_PER_SIDE + SLIP_RATE_PER_SIDE)
LEVERED_FEE_DRAG = ROUND_TRIP_PRICE_COST * LEVERAGE

REGIONS = {
    "A": {
        "region_id": "A",
        "symbol": "ETH-USDT-SWAP",
        "timeframe": "5m",
        "direction": "long",
        "direction_sign": 1,
        "vol": "LO",
        "trend": "MID",
        "session": "EU",
        "horizon": 96,
        "phase0_observed": {
            "win_rate": 0.665,
            "sample_count": 194,
            "mean_win_net": 0.129,
            "weekly": 34.6,
        },
    },
    "B": {
        "region_id": "B",
        "symbol": "ETH-USDT-SWAP",
        "timeframe": "5m",
        "direction": "short",
        "direction_sign": -1,
        "vol": "MID",
        "trend": "LO",
        "session": "US",
        "horizon": 12,
        "phase0_observed": {
            "win_rate": 0.660,
            "sample_count": 53,
            "mean_win_net": 0.123,
            "weekly": 25.2,
        },
    },
}
