#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BNB15m 伦敦箱：补上北京时间开仓闸门，并按短窗3个月/长窗2年重测。

北京时间 15:00–23:00 = UTC 07:00–15:00（北京无夏令时）。
“伦敦箱高点”只是价位记忆；开仓还必须落在上述监控时段内。
"""
from __future__ import print_function

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "strategies" / "ada5_bopb_mutation_ports_v1" / "_scratch"
OHLC = OUT_DIR / "ohlc"
os.environ.setdefault("VECTOR_BACKTEST_LOG_DIR", str(ROOT / "_scratch" / "freq_2y" / "logs"))
os.environ.setdefault("VECTOR_MARKET_DATA_ROOT", str(OHLC))
os.environ.setdefault(
    "VECTOR_STRATEGY_CONFIG_PATH",
    str(ROOT / "_scratch" / "freq_2y" / "experimental_strategies.json"),
)
os.environ.setdefault(
    "VECTOR_DSL_STRATEGY_CONFIG_PATH",
    str(ROOT / "_scratch" / "freq_2y" / "ai_dsl_strategies.json"),
)

import numpy as np
import pandas as pd
import backtest_engine_v2 as bt
import auto_trade_strategy_dsl as dsl

# 北京 15:00–23:00 → UTC [7, 15)
BJ_LOCK_UTC_START = 7.0
BJ_LOCK_UTC_END = 15.0


def mk(key, lock_beijing_session, rsi=45.0, z=2.3, hold=14, off=24, tp=60.0):
    entry = [
        {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "gt",
         "right": {"feature": "h1_ema53"}},
        {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gt",
         "right": {"value": 0.0}},
        {"id": "bo", "left": {"feature": "close", "offset": int(off)}, "op": "gt",
         "right": {"feature": "london_high", "offset": int(off)}},
        {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_above",
         "right": {"value": float(rsi)}},
        {"id": "px", "left": {"feature": "close"}, "op": "gt",
         "right": {"feature": "ema21"}},
        {"id": "z", "left": {"feature": "z20"}, "op": "lt",
         "right": {"value": float(z)}},
    ]
    if lock_beijing_session:
        entry += [
            {"id": "bj1", "left": {"feature": "hour_utc"}, "op": "gte",
             "right": {"value": BJ_LOCK_UTC_START}},
            {"id": "bj2", "left": {"feature": "hour_utc"}, "op": "lt",
             "right": {"value": BJ_LOCK_UTC_END}},
        ]
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key,
        "name": "BNB15m伦敦箱带时段锁" if lock_beijing_session else "BNB15m伦敦箱无时段锁",
        "direction": "long",
        "timeframe": "15m",
        "supported_instruments": ["BNB-USDT-SWAP"],
        "entry": {"all": entry},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
             "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "prev_low20"}, "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
        "auto_trade_eligible": False,
        "live_enabled": False,
        "description": (
            "伦敦箱高点作突破记忆；开仓仅允许北京时间15:00-23:00"
            if lock_beijing_session else
            "伦敦箱高点作突破记忆；开仓时段未加锁（旧做法）"
        ),
    }


def summarize(name, trades, span_days):
    if not trades:
        return {
            "名字": name, "笔数": 0, "胜率百分": None,
            "平均每笔账户盈亏百分": None, "样本天数": round(span_days, 1),
        }
    pnls = [float(t["pnl_ratio"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    hours = []
    for t in trades:
        ts = pd.Timestamp(t["entry_time"])
        # K线索引按UTC naive；换算北京时间 = UTC+8
        bj = ts + pd.Timedelta(hours=8)
        hours.append(int(bj.hour))
    return {
        "名字": name,
        "笔数": len(pnls),
        "胜率百分": round(100.0 * len(wins) / len(pnls), 1),
        "平均每笔账户盈亏百分": round(100.0 * float(np.mean(pnls)), 3),
        "赢单平均百分": round(100.0 * float(np.mean(wins)), 3) if wins else None,
        "亏单平均百分": round(100.0 * float(np.mean(losses)), 3) if losses else None,
        "止损笔数": sum(1 for t in trades if t.get("stop_loss")),
        "累计近似百分": round(100.0 * (float(np.prod([1 + p for p in pnls])) - 1), 2),
        "样本天数": round(span_days, 1),
        "开仓北京小时分布": {str(h): hours.count(h) for h in sorted(set(hours))},
        "是否全部落在北京15到22点": all(15 <= h <= 22 for h in hours) if hours else None,
    }


def run_window(frame_full, end_ts, days, lock):
    start_ts = end_ts - pd.Timedelta(days=days)
    # 预热：指标用更早数据，统计只看窗口内成交
    warm_start = start_ts - pd.Timedelta(days=60)
    fr = frame_full.loc[(frame_full.index >= warm_start) & (frame_full.index <= end_ts)].copy()
    key = ("lock" if lock else "nolock") + "_d" + str(days)
    s = mk(key, lock_beijing_session=lock)
    dsl.validate_strategy(s)
    res = dsl.backtest_dsl(fr, s, stop_loss_pct=0.009, leverage=20)
    trades = [
        t for t in (res.get("trades") or [])
        if pd.Timestamp(t["entry_time"]) >= start_ts
        and pd.Timestamp(t["entry_time"]) <= end_ts
    ]
    span = (end_ts - start_ts).total_seconds() / 86400.0
    label = ("有北京时段锁" if lock else "无时段锁") + ("·短窗3个月" if days <= 100 else "·长窗2年")
    return summarize(label, trades, span), s, trades


def main():
    cache = OHLC / "BNB_USDT_SWAP_15m_okx_2y.parquet"
    fallback = OHLC / "BNB-USDT-SWAP_15m.parquet"
    if cache.exists():
        raw = pd.read_parquet(cache)
        src = str(cache)
    elif fallback.exists():
        raw = pd.read_parquet(fallback)
        src = str(fallback)
        print("警告：只有约半年本地K线，还不够2年长窗", flush=True)
    else:
        raise SystemExit("没有BNB15m行情文件，请先拉取")

    print("行情来源", src, "n", len(raw), raw.index.min(), "->", raw.index.max(), flush=True)
    frame = bt.precompute_indicators(raw, timeframe="15m")
    if "h1_slope4" not in frame.columns:
        frame["h1_slope4"] = (
            frame["ema19"].astype(float) / frame["ema19"].astype(float).shift(4) - 1.0
        )
    if "hour_utc" not in frame.columns:
        idx = frame.index
        frame["hour_utc"] = [
            float(ts.hour) + float(ts.minute) / 60.0 for ts in idx
        ]
    frame = frame.replace([float("inf"), -float("inf")], float("nan")).ffill().bfill()

    end_ts = frame.index.max()
    span_have = (frame.index.max() - frame.index.min()).total_seconds() / 86400.0
    print("可用样本约", round(span_have, 1), "天", flush=True)

    rows = []
    details = {}
    for days, tag in ((90, "短窗3个月"), (730, "长窗2年")):
        if span_have < days - 5:
            rows.append({
                "名字": tag,
                "状态": "样本不够",
                "现有天数": round(span_have, 1),
                "需要天数": days,
                "说明": "超出可用行情的部分本轮不做结论",
            })
            print("跳过", tag, "现有", round(span_have, 1), "天 <", days, flush=True)
            continue
        for lock in (False, True):
            row, s, trades = run_window(frame, end_ts, days, lock)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            details[row["名字"]] = {
                "规则说明": s["description"],
                "成交笔数": len(trades),
                "前5笔开仓时间": [t["entry_time"] for t in trades[:5]],
            }

    out = {
        "约定": {
            "短窗": "最近3个月（90天）",
            "长窗": "最近2年（730天）",
            "之外": "不考虑",
            "北京监控时段": "15:00-23:00",
            "对应UTC开仓闸门": "[07:00, 15:00)",
            "说明": "伦敦箱高点只做价位记忆；是否允许开仓由北京时段闸门单独控制",
        },
        "旧做法问题": (
            "之前只用 london_high 记住伦敦时段的高点，"
            "但开仓条件里没有 hour_utc 闸门，所以半夜也可以触发。"
        ),
        "行情跨度天": round(span_have, 1),
        "行情起止": [str(frame.index.min()), str(frame.index.max())],
        "对照结果": rows,
        "细节": details,
    }
    path = OUT_DIR / "bnb15m_london_session_lock_3m_2y.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("写入", path, flush=True)


if __name__ == "__main__":
    main()
