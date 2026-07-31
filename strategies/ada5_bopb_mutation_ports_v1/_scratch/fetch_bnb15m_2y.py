#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取 BNB 15m 约2年K线（外加预热）。"""
from __future__ import print_function
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "strategies" / "ada5_bopb_mutation_ports_v1" / "_scratch" / "ohlc"
OUT_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("VECTOR_BACKTEST_LOG_DIR", str(ROOT / "_scratch" / "freq_2y" / "logs"))
os.environ.setdefault("VECTOR_MARKET_DATA_ROOT", str(OUT_DIR))
os.environ.setdefault(
    "VECTOR_STRATEGY_CONFIG_PATH",
    str(ROOT / "_scratch" / "freq_2y" / "experimental_strategies.json"),
)
os.environ.setdefault(
    "VECTOR_DSL_STRATEGY_CONFIG_PATH",
    str(ROOT / "_scratch" / "freq_2y" / "ai_dsl_strategies.json"),
)
(Path(ROOT) / "_scratch" / "freq_2y" / "logs").mkdir(parents=True, exist_ok=True)

import pandas as pd
import backtest_engine_v2 as bt

cache = OUT_DIR / "BNB_USDT_SWAP_15m_okx_2y.parquet"
end = pd.Timestamp.utcnow().tz_localize(None)
start = end - pd.Timedelta(days=730)
warm = start - pd.Timedelta(days=90)
print("需要", warm, "->", end, flush=True)

if cache.exists():
    df = pd.read_parquet(cache)
    print("已有缓存", len(df), df.index.min(), df.index.max(), flush=True)
    if df.index.min() <= warm + pd.Timedelta(days=5) and df.index.max() >= end - pd.Timedelta(days=5):
        print("缓存足够，跳过拉取", flush=True)
        raise SystemExit(0)

spec = bt.TIMEFRAME_SPECS["15m"]
old = spec.get("request_max_pages")
spec["request_max_pages"] = max(int(old or 0), 1200)
t0 = time.time()
try:
    df = bt.load_okx_swap_range(
        "BNB-USDT-SWAP",
        warm.strftime("%Y-%m-%d %H:%M:%S"),
        end.strftime("%Y-%m-%d %H:%M:%S"),
        timeframe="15m",
    )
finally:
    spec["request_max_pages"] = old

print(
    "拉取完成 n=", len(df), "耗时秒", round(time.time() - t0, 1),
    "跨度", df.index.min(), "->", df.index.max(), flush=True,
)
df = df[["open", "high", "low", "close"]].copy()
df.to_parquet(cache)
print("写入", cache, "MB", round(cache.stat().st_size / 1e6, 2), flush=True)
print(json.dumps({
    "n": len(df),
    "start": str(df.index.min()),
    "end": str(df.index.max()),
    "span_days": round((df.index.max() - df.index.min()).total_seconds() / 86400, 2),
}, ensure_ascii=False), flush=True)
