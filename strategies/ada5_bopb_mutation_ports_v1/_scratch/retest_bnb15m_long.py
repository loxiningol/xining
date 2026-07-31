#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Longer-window retest for BNB15m mutation survivors (VPS market_data)."""
from __future__ import print_function
import json, os, sys, gc
from pathlib import Path

sys.path.insert(0, "/root")
sys.path.insert(0, "/root/auto_trade")
os.chdir("/root")
os.environ.setdefault("VECTOR_BACKTEST_LOG_DIR", "/tmp/mut_retest_logs")
Path("/tmp/mut_retest_logs").mkdir(exist_ok=True)

import pandas as pd
import backtest_engine_v2 as bt
import auto_trade_strategy_dsl as dsl

PARQUET = "/root/market_data/BNB-USDT-SWAP/15m/BNB-USDT-SWAP_15m.parquet"
OUT = "/tmp/bnb15m_mutation_long_retest.json"


def mk_bopb(key, rsi, z, hold, off, tp, box="asia"):
    feat = "london_high" if box == "lon" else "asia_high"
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key,
        "name": "BNB15m突变再测",
        "direction": "long",
        "timeframe": "15m",
        "supported_instruments": ["BNB-USDT-SWAP"],
        "entry": {"all": [
            {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "gt",
             "right": {"feature": "h1_ema53"}},
            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gt",
             "right": {"value": 0.0}},
            {"id": "bo", "left": {"feature": "close", "offset": int(off)},
             "op": "gt", "right": {"feature": feat, "offset": int(off)}},
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_above",
             "right": {"value": float(rsi)}},
            {"id": "px", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "ema21"}},
            {"id": "z", "left": {"feature": "z20"}, "op": "lt",
             "right": {"value": float(z)}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
             "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "prev_low20"}, "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
        "auto_trade_eligible": False,
        "live_enabled": False,
        "origin": "ada5_bopb_mutation_ports_v1",
        "description": "亚盘/伦敦箱突破记忆+RSI回升再进·BNB15m移植",
    }


CANDIDATES = [
    # best short-window POS (rescue)
    dict(key="mut_bopb_bnb_15m_lon_o24_r45_z2p3_h14_tp60", rsi=45, z=2.3, hold=14, off=24, tp=60, box="lon"),
    dict(key="mut_bopb_bnb_15m_asia_o20_r45_z2p3_h14_tp55", rsi=45, z=2.3, hold=14, off=20, tp=55, box="asia"),
    dict(key="mut_bopb_bnb_15m_asia_o24_r45_z2p3_h12_tp55", rsi=45, z=2.3, hold=12, off=24, tp=55, box="asia"),
    # seed-faithful port
    dict(key="mut_bopb_bnb_15m_asia_o20_r42_z2p3_h14_tp60", rsi=42, z=2.3, hold=14, off=20, tp=60, box="asia"),
]


def main():
    raw = pd.read_parquet(PARQUET)
    print("raw", len(raw), raw.index.min(), raw.index.max(), flush=True)
    frame = bt.precompute_indicators(raw, timeframe="15m")
    if "h1_slope4" not in frame.columns:
        frame["h1_slope4"] = (
            frame["ema19"].astype(float) / frame["ema19"].astype(float).shift(4) - 1.0
        )
    frame = frame.replace([float("inf"), -float("inf")], float("nan")).ffill().bfill()
    span_days = (frame.index[-1] - frame.index[0]).total_seconds() / 86400.0
    rows = []
    for c in CANDIDATES:
        s = mk_bopb(**c)
        dsl.validate_strategy(s)
        res = dsl.backtest_dsl(frame, s, stop_loss_pct=0.009)
        trades = res.get("trades") or []
        n = len(trades)
        pnls = [float(t.get("pnl_ratio") or 0) for t in trades]
        wr = (sum(1 for p in pnls if p > 0) / n) if n else 0.0
        mean = (sum(pnls) / n) if n else 0.0
        weekly = n * 7.0 / span_days if span_days > 0 else None
        row = {
            **c,
            "n": n, "wr": wr, "mean_net": mean,
            "span_days": round(span_days, 3),
            "weekly_opens_proxy": round(weekly, 4) if weekly is not None else None,
            "frame_start": str(frame.index[0]),
            "frame_end": str(frame.index[-1]),
            "pos": bool(n >= 10 and wr >= 0.5 and mean > 0),
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        del res, trades
        gc.collect()
    Path(OUT).write_text(json.dumps({"ok": True, "rows": rows}, ensure_ascii=False, indent=2))
    print("WROTE", OUT, flush=True)


if __name__ == "__main__":
    main()
