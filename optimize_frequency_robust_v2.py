# -*- coding: utf-8 -*-
"""Search higher-frequency 5m rules with three chronological validation slices."""

from __future__ import print_function

from collections import OrderedDict
from pathlib import Path
import json
import os
import sys

import numpy as np
import pandas as pd


ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import optimize_intraday_rules as opt


SYMBOLS = ("BTC-USDT-SWAP", "CL-USDT-SWAP", "XAU-USDT-SWAP", "NG-USDT-SWAP")
OPTIMIZED_FILES = {
    "BTC-USDT-SWAP": "optimized_5m_BTC.json",
    "CL-USDT-SWAP": "optimized_5m_CL-.json",
    "XAU-USDT-SWAP": "optimized_5m_XAU.json",
    "NG-USDT-SWAP": "optimized_5m_NG-.json",
    "ETH-USDT-SWAP": "optimized_5m_ETH.json",
    "SOL-USDT-SWAP": "optimized_5m_SOL.json",
    "XAG-USDT-SWAP": "optimized_5m_XAG.json",
    "XRP-USDT-SWAP": "optimized_5m_XRP.json",
    "DOGE-USDT-SWAP": "optimized_5m_DOGE.json",
    "LTC-USDT-SWAP": "optimized_5m_LTC.json",
    "ADA-USDT-SWAP": "optimized_5m_ADA.json",
    "AVAX-USDT-SWAP": "optimized_5m_AVAX.json",
    "SUI-USDT-SWAP": "optimized_5m_SUI.json",
    "LINK-USDT-SWAP": "optimized_5m_LINK.json",
}
START = pd.Timestamp("2026-03-15 00:00:00")
TRAIN_END = pd.Timestamp("2026-05-15 23:59:59")
VALID_START = pd.Timestamp("2026-05-16 00:00:00")
VALID_END = pd.Timestamp("2026-06-20 23:59:59")
TEST_START = pd.Timestamp("2026-06-21 00:00:00")
END = pd.Timestamp("2026-07-20 23:59:59")
SESSIONS = (
    ("all", None, None),
    ("utc_00_08", 0, 8),
    ("utc_06_14", 6, 14),
    ("utc_08_16", 8, 16),
    ("utc_12_20", 12, 20),
    ("utc_16_24", 16, 24),
)


def data_path(symbol):
    return ROOT / "market_data" / symbol / "5m" / (
        symbol.replace("-", "_") + "_5m_OKX_LOCAL.parquet"
    )


def session_mask(index, start, end):
    if start is None:
        return np.ones(len(index), dtype=bool)
    hour = index.hour.to_numpy()
    return (hour >= start) & (hour < end)


def loss_cluster(trades):
    current = maximum = 0
    for row in trades:
        if row[2] <= 0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


def allocation_metrics(trades, allocation=.15, leverage=20):
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "avg_account_pct": 0.0,
                "compound_return_pct": 0.0, "max_drawdown_pct": 0.0,
                "max_consecutive_losses": 0}
    returns = np.array([row[2] * leverage * allocation for row in trades], float)
    equity = np.cumprod(np.maximum(0.0, 1.0 + returns))
    path = np.r_[1.0, equity]
    peak = np.maximum.accumulate(path)
    return {
        "trades": len(trades),
        "win_rate": float(np.mean(returns > 0) * 100.0),
        "avg_account_pct": float(np.mean(returns) * 100.0),
        "compound_return_pct": float((equity[-1] - 1.0) * 100.0),
        "max_drawdown_pct": float(np.max(1.0 - path / peak) * 100.0),
        "max_consecutive_losses": loss_cluster(trades),
    }


def ranked_candidate_specs(symbol):
    with open(str(ROOT / OPTIMIZED_FILES[symbol]), "r", encoding="utf-8") as handle:
        ranked = json.load(handle)
    seen = set()
    for row in ranked[:300]:
        token = (
            row["family"], row["side"], json.dumps(row["params"], sort_keys=True),
            float(row["tp"]), float(row["sl"]), int(row["max_hold"]),
        )
        if token in seen:
            continue
        seen.add(token)
        yield row["family"], row["side"], row["params"], float(row["tp"]), float(row["sl"]), int(row["max_hold"])


def main():
    output = {}
    requested = os.environ.get("VECTOR_SYMBOLS", "").strip()
    symbols = tuple(x.strip().upper() for x in requested.split(",") if x.strip()) or SYMBOLS
    for symbol in symbols:
        frame = opt.indicators(pd.read_parquet(str(data_path(symbol))).sort_index())
        rows = []
        for family, side, params, tp, sl, hold in ranked_candidate_specs(symbol):
            base = opt.signal_mask(frame, family, side, params)
            in_full = np.asarray((frame.index >= START) & (frame.index <= END), dtype=bool)
            if int(np.sum(base & in_full)) < 45:
                continue
            for session_name, hour_start, hour_end in SESSIONS:
                mask = base & session_mask(frame.index, hour_start, hour_end)
                if int(np.sum(mask & in_full)) < 45:
                    continue
                train = opt.simulate(frame, mask, side, START, TRAIN_END, tp, sl, hold)
                valid = opt.simulate(frame, mask, side, VALID_START, VALID_END, tp, sl, hold)
                test = opt.simulate(frame, mask, side, TEST_START, END, tp, sl, hold)
                if len(train) < 18 or len(valid) < 9 or len(test) < 8:
                    continue
                mt = allocation_metrics(train)
                mv = allocation_metrics(valid)
                ms = allocation_metrics(test)
                full = train + valid + test
                mf = allocation_metrics(full)
                per_day = len(full) / 128.0
                if per_day < .45:
                    continue
                if min(mt["avg_account_pct"], mv["avg_account_pct"], ms["avg_account_pct"]) <= 0:
                    continue
                if min(mt["win_rate"], mv["win_rate"], ms["win_rate"]) < 52.0:
                    continue
                if mf["max_drawdown_pct"] > 22.0 or mf["max_consecutive_losses"] > 5:
                    continue
                score = (
                    min(mt["avg_account_pct"], mv["avg_account_pct"], ms["avg_account_pct"]) * 35.0
                    + per_day * 18.0
                    + min(mt["win_rate"], mv["win_rate"], ms["win_rate"]) * .25
                    - mf["max_drawdown_pct"] * .5
                )
                rows.append({
                    "symbol": symbol, "family": family, "side": side,
                    "params": params, "session": session_name,
                    "utc_hour_start": hour_start, "utc_hour_end": hour_end,
                    "tp": tp, "sl": sl, "max_hold": hold,
                    "entries_per_day": per_day,
                    "train": mt, "validation": mv, "test": ms,
                    "full": mf, "score": float(score),
                })
        rows.sort(key=lambda row: row["score"], reverse=True)
        output[symbol] = rows[:100]
        print(json.dumps({"symbol": symbol, "qualified": len(rows),
                          "top": rows[:10]}, ensure_ascii=False), flush=True)
    path = ROOT / "auto_trade" / "frequency_robust_v2_candidates.json"
    with open(str(path), "w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2)
    print("OUTPUT=" + str(path), flush=True)


if __name__ == "__main__":
    main()
