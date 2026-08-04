# -*- coding: utf-8 -*-
"""Targeted LTC 5m exhaustion-short search with chronological validation."""
from __future__ import print_function

import itertools
import json
from pathlib import Path

import numpy as np
import pandas as pd

import optimize_intraday_rules as opt
import optimize_frequency_robust_v2 as robust


ROOT = Path("/root")
DATA = ROOT / "market_data/LTC-USDT-SWAP/5m/LTC_USDT_SWAP_5m_OKX_LOCAL.parquet"
SESSIONS = (
    ("all", None, None),
    ("utc_00_08", 0, 8),
    ("utc_04_12", 4, 12),
    ("utc_08_16", 8, 16),
    ("utc_12_20", 12, 20),
    ("utc_16_24", 16, 24),
)


def session_mask(index, start, end):
    if start is None:
        return np.ones(len(index), dtype=bool)
    hour = index.hour.to_numpy()
    return (hour >= start) & (hour < end)


def main():
    frame = opt.indicators(pd.read_parquet(str(DATA)).sort_index())
    rows = []
    grid = itertools.product(
        (0.8, 1.0, 1.2, 1.4),
        (25, 27.5, 30, 32.5),
        (0.003, 0.006),
        (0.004, 0.008),
        (0.0005,),
        (0.006, 0.009),
        (0.009,),
        (48, 72),
    )
    for z, rsi, slope_limit, atr_max, atr_min, tp, sl, hold in grid:
        p = {"z": z, "rsi": rsi, "slope": 0.0,
             "slope_limit": slope_limit, "atr_max": atr_max,
             "atr_min": atr_min}
        base = opt.signal_mask(frame, "exhaustion_reversal", "short", p)
        for session, hour_start, hour_end in SESSIONS:
            mask = base & session_mask(frame.index, hour_start, hour_end)
            train = opt.simulate(frame, mask, "short", robust.START,
                                 robust.TRAIN_END, tp, sl, hold)
            valid = opt.simulate(frame, mask, "short", robust.VALID_START,
                                 robust.VALID_END, tp, sl, hold)
            test = opt.simulate(frame, mask, "short", robust.TEST_START,
                                robust.END, tp, sl, hold)
            if len(train) < 15 or len(valid) < 6 or len(test) < 8:
                continue
            mt, mv, ms = map(robust.allocation_metrics,
                             (train, valid, test))
            full = train + valid + test
            mf = robust.allocation_metrics(full)
            rate = len(full) / 128.0
            if rate < 0.35:
                continue
            if min(mt["avg_account_pct"], mv["avg_account_pct"],
                   ms["avg_account_pct"]) <= 0:
                continue
            if min(mt["win_rate"], mv["win_rate"], ms["win_rate"]) < 58:
                continue
            if mf["win_rate"] < 68 or mf["max_drawdown_pct"] > 18:
                continue
            if mf["max_consecutive_losses"] > 4:
                continue
            score = (rate * 30 + mf["win_rate"] * 0.3
                     + min(mt["avg_account_pct"], mv["avg_account_pct"],
                           ms["avg_account_pct"]) * 25
                     - mf["max_drawdown_pct"] * 0.6)
            rows.append({"family": "exhaustion_reversal", "side": "short",
                         "params": p, "session": session,
                         "utc_hour_start": hour_start,
                         "utc_hour_end": hour_end, "tp": tp, "sl": sl,
                         "max_hold": hold, "entries_per_day": rate,
                         "train": mt, "validation": mv, "test": ms,
                         "full": mf, "score": score})
    rows.sort(key=lambda x: x["score"], reverse=True)
    output = ROOT / "auto_trade/ltc_high_frequency_candidates.json"
    output.write_text(json.dumps(rows[:200], ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print(json.dumps({"qualified": len(rows), "top": rows[:20]},
                     ensure_ascii=False), flush=True)
    print("OUTPUT=" + str(output), flush=True)


if __name__ == "__main__":
    main()
