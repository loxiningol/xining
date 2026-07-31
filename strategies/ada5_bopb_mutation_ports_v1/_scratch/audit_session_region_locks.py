#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""未挂载的区域性策略：有/无开仓时段锁，短窗3个月 vs 长窗2年对照。

排除已过复核、在自动交易的策略。
用大白话指标：笔数、胜率、平均每笔盈亏；长窗为正且样本够才谈进入复核。
"""
from __future__ import print_function

import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "strategies" / "ada5_bopb_mutation_ports_v1" / "_scratch"
OHLC = OUT / "ohlc"
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
import auto_trade_strategy_dsl as dsl_mod

# 进入复核的硬门槛（长窗）
MIN_N_LONG = 10
MIN_WR = 0.50


def load_pack_dsl(path, which="dsl"):
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if which in obj and isinstance(obj[which], dict) and obj[which].get("schema"):
        return obj[which]
    if which == "dsl_short" and "dsl_short" in obj:
        return obj["dsl_short"]
    if which == "dsl_long" and "dsl_long" in obj:
        return obj["dsl_long"]
    return obj.get("dsl")


def strip_hour_gates(strategy):
    """去掉 hour_utc 开仓闸，其余逻辑不动——用于对照。"""
    s = copy.deepcopy(strategy)
    entry = s.get("entry") or {}
    all_conds = entry.get("all") or []
    kept = []
    for c in all_conds:
        feat = ((c.get("left") or {}).get("feature") or "")
        if feat == "hour_utc":
            continue
        kept.append(c)
    s["entry"] = {"all": kept}
    s["key"] = (s.get("key") or "x")[:80] + "_nolock"
    # 键只能字母数字下划线
    s["key"] = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in s["key"])[:100]
    s["live_enabled"] = False
    s["auto_trade_eligible"] = False
    return s


def ensure_hour_gates(strategy, utc_start, utc_end):
    """若没有 hour_utc 闸，补上；若已有则保持原闸（不改策略原设计窗）。"""
    s = copy.deepcopy(strategy)
    entry = s.get("entry") or {}
    all_conds = list(entry.get("all") or [])
    has = any(((c.get("left") or {}).get("feature") == "hour_utc") for c in all_conds)
    if not has:
        all_conds = [
            {"id": "lock_ge", "left": {"feature": "hour_utc"}, "op": "gte",
             "right": {"value": float(utc_start)}},
            {"id": "lock_lt", "left": {"feature": "hour_utc"}, "op": "lt",
             "right": {"value": float(utc_end)}},
        ] + all_conds
        s["entry"] = {"all": all_conds}
        s["key"] = ("".join(ch if ch.isalnum() or ch == "_" else "_" for ch in (s.get("key") or "x")) + "_locked")[:100]
    s["live_enabled"] = False
    s["auto_trade_eligible"] = False
    # 去掉未知顶层字段以免校验失败
    allowed = {
        "schema", "key", "name", "direction", "timeframe", "supported_instruments",
        "entry", "exit", "max_hold_bars", "description", "origin", "version",
        "live_enabled", "approved_version_hash", "auto_trade_eligible",
    }
    for k in list(s.keys()):
        if k not in allowed:
            s.pop(k, None)
    return s


def mk_bopb(sym, tf, box, off, rsi, z, hold, tp, lock_utc=None):
    feat = "london_high" if box == "london" else "asia_high"
    tag = sym.split("-")[0].lower()
    key = "audit_bopb_%s_%s_%s_o%s_r%s_z%s" % (
        tag, tf, box[:3], off, int(rsi), str(z).replace(".", "p"),
    )
    if lock_utc:
        key += "_L%s_%s" % (int(lock_utc[0]), int(lock_utc[1]))
    key = key.replace(".", "p")[:100]
    entry = [
        {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "gt",
         "right": {"feature": "h1_ema53"}},
        {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gt",
         "right": {"value": 0.0}},
        {"id": "bo", "left": {"feature": "close", "offset": int(off)}, "op": "gt",
         "right": {"feature": feat, "offset": int(off)}},
        {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_above",
         "right": {"value": float(rsi)}},
        {"id": "px", "left": {"feature": "close"}, "op": "gt",
         "right": {"feature": "ema21"}},
        {"id": "z", "left": {"feature": "z20"}, "op": "lt",
         "right": {"value": float(z)}},
    ]
    if lock_utc:
        entry = [
            {"id": "lock_ge", "left": {"feature": "hour_utc"}, "op": "gte",
             "right": {"value": float(lock_utc[0])}},
            {"id": "lock_lt", "left": {"feature": "hour_utc"}, "op": "lt",
             "right": {"value": float(lock_utc[1])}},
        ] + entry
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key,
        "name": "区域突破回踩审计",
        "direction": "long",
        "timeframe": tf,
        "supported_instruments": [sym],
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
        "description": "区域箱突破记忆+RSI再进；可带开仓时段锁",
    }


def prep_frame(path, tf):
    raw = pd.read_parquet(path)
    frame = bt.precompute_indicators(raw, timeframe=tf)
    if "h1_slope4" not in frame.columns and "ema19" in frame.columns:
        frame["h1_slope4"] = (
            frame["ema19"].astype(float) / frame["ema19"].astype(float).shift(4) - 1.0
        )
    if "hour_utc" not in frame.columns:
        frame["hour_utc"] = [float(ts.hour) + float(ts.minute) / 60.0 for ts in frame.index]
    frame = frame.replace([float("inf"), -float("inf")], float("nan")).ffill().bfill()
    return frame


def summarize(trades, span_days):
    if not trades:
        return {
            "笔数": 0, "胜率百分": None, "平均每笔百分": None,
            "样本天数": round(span_days, 1), "可进入复核": False,
        }
    pnls = [float(t["pnl_ratio"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    n = len(pnls)
    wr = len(wins) / float(n)
    mean = float(np.mean(pnls))
    return {
        "笔数": n,
        "胜率百分": round(100.0 * wr, 1),
        "平均每笔百分": round(100.0 * mean, 3),
        "赢单平均百分": round(100.0 * float(np.mean(wins)), 3) if wins else None,
        "亏单平均百分": round(
            100.0 * float(np.mean([p for p in pnls if p <= 0])), 3
        ) if any(p <= 0 for p in pnls) else None,
        "止损笔数": sum(1 for t in trades if t.get("stop_loss")),
        "样本天数": round(span_days, 1),
        "可进入复核": bool(n >= MIN_N_LONG and wr >= MIN_WR and mean > 0),
    }


FRAME_CACHE = {}


def get_frame(sym, tf, prefer_2y=True):
    key = (sym, tf)
    if key in FRAME_CACHE:
        return FRAME_CACHE[key]
    tag = sym.replace("-", "_")
    cands = []
    if prefer_2y:
        cands.append(OHLC / ("%s_%s_okx_2y.parquet" % (tag, tf)))
    cands += [
        OHLC / ("%s_%s.parquet" % (tag, tf)),
        OHLC / ("%s-%s.parquet" % (sym, tf)),
    ]
    # BNB naming
    cands.append(OHLC / ("BNB_USDT_SWAP_15m_okx_2y.parquet" if tf == "15m" else "x"))
    path = None
    for p in cands:
        if p.exists():
            path = p
            break
    if path is None:
        return None, None
    print("加载", sym, tf, path.name, flush=True)
    fr = prep_frame(path, tf)
    FRAME_CACHE[key] = (fr, path)
    return fr, path


def run_one(name, region, strategy, frame, end_ts, days):
    start_ts = end_ts - pd.Timedelta(days=days)
    warm = start_ts - pd.Timedelta(days=45)
    fr = frame.loc[(frame.index >= warm) & (frame.index <= end_ts)].copy()
    need_days = days
    have = (fr.index.max() - fr.index.min()).total_seconds() / 86400.0
    if have < need_days - 15:
        return {
            "名字": name, "区域": region, "窗口天": days,
            "状态": "样本不够", "现有大约天数": round(have, 1),
        }
    s = copy.deepcopy(strategy)
    # 校验前清洗
    allowed = {
        "schema", "key", "name", "direction", "timeframe", "supported_instruments",
        "entry", "exit", "max_hold_bars", "description", "origin", "version",
        "live_enabled", "approved_version_hash", "auto_trade_eligible",
    }
    for k in list(s.keys()):
        if k not in allowed:
            s.pop(k, None)
    s["key"] = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(s.get("key") or "k"))[:100]
    try:
        dsl_mod.validate_strategy(s)
        res = dsl_mod.backtest_dsl(fr, s, stop_loss_pct=0.009, leverage=20)
    except Exception as exc:
        return {"名字": name, "区域": region, "窗口天": days, "状态": "回测失败", "原因": str(exc)[:200]}
    trades = [
        t for t in (res.get("trades") or [])
        if start_ts <= pd.Timestamp(t["entry_time"]) <= end_ts
    ]
    row = summarize(trades, days)
    row.update({"名字": name, "区域": region, "窗口天": days, "状态": "ok"})
    return row


def main():
    rows = []
    # —— 已有完整DSL且自带时段闸的包 ——
    packs = [
        {
            "名字": "亚盘极值扫单反手·多",
            "区域": "亚盘箱+欧美窗",
            "path": ROOT / "strategy_asia_sweep_fade_v1.json",
            "which": "dsl_long",
            "sym": "ETH-USDT-SWAP", "tf": "15m",
            "原设计已有时段锁": True,
        },
        {
            "名字": "亚盘极值扫单反手·空",
            "区域": "亚盘箱+欧美窗",
            "path": ROOT / "strategy_asia_sweep_fade_v1.json",
            "which": "dsl_short",
            "sym": "ETH-USDT-SWAP", "tf": "15m",
            "原设计已有时段锁": True,
        },
        {
            "名字": "亚盘挤压欧美扩张·多",
            "区域": "亚盘挤压+欧美打开",
            "path": ROOT / "strategy_session_vol_squeeze_v1.json",
            "which": "dsl_long",
            "sym": "ETH-USDT-SWAP", "tf": "15m",
            "原设计已有时段锁": True,
        },
        {
            "名字": "亚盘挤压欧美扩张·空",
            "区域": "亚盘挤压+欧美打开",
            "path": ROOT / "strategy_session_vol_squeeze_v1.json",
            "which": "dsl_short",
            "sym": "ETH-USDT-SWAP", "tf": "15m",
            "原设计已有时段锁": True,
        },
        {
            "名字": "纽约开盘流动性空洞·多",
            "区域": "美盘开盘+伦敦箱",
            "path": ROOT / "strategy_ny_open_liq_fade_clean_v1.json",
            "which": "dsl_long",
            "sym": "ETH-USDT-SWAP", "tf": "5m",
            "原设计已有时段锁": True,
        },
        {
            "名字": "纽约开盘流动性空洞·空",
            "区域": "美盘开盘+伦敦箱",
            "path": ROOT / "strategy_ny_open_liq_fade_clean_v1.json",
            "which": "dsl_short",
            "sym": "ETH-USDT-SWAP", "tf": "5m",
            "原设计已有时段锁": True,
        },
    ]

    for p in packs:
        base = load_pack_dsl(p["path"], p["which"])
        if not base:
            print("缺DSL", p["名字"], flush=True)
            continue
        fr, src = get_frame(p["sym"], p["tf"])
        if fr is None:
            print("缺行情", p["sym"], p["tf"], flush=True)
            continue
        end_ts = fr.index.max()
        locked = ensure_hour_gates(base, 8.0, 16.0)  # 若已有闸则保持
        unlocked = strip_hour_gates(base)
        for days, wname in ((90, "短窗3个月"), (730, "长窗2年")):
            r1 = run_one(p["名字"] + "·有时段锁·" + wname, p["区域"], locked, fr, end_ts, days)
            r0 = run_one(p["名字"] + "·无时段锁·" + wname, p["区域"], unlocked, fr, end_ts, days)
            rows.append(r1)
            rows.append(r0)
            print(json.dumps(r1, ensure_ascii=False), flush=True)
            print(json.dumps(r0, ensure_ascii=False), flush=True)

    # —— 变异族：原先没锁的突破回踩 ——
    bopbs = [
        {
            "名字": "BNB15m伦敦箱突破回踩",
            "区域": "伦敦",
            "sym": "BNB-USDT-SWAP", "tf": "15m",
            "box": "london", "off": 24, "rsi": 45, "z": 2.3, "hold": 14, "tp": 60,
            # 北京15-23 → UTC7-15
            "lock": (7.0, 15.0),
        },
        {
            "名字": "BNB15m亚盘箱突破回踩",
            "区域": "亚盘",
            "sym": "BNB-USDT-SWAP", "tf": "15m",
            "box": "asia", "off": 20, "rsi": 45, "z": 2.3, "hold": 14, "tp": 55,
            # 亚盘结束后做回踩：北京16-24 → UTC8-16
            "lock": (8.0, 16.0),
        },
    ]
    for b in bopbs:
        fr, src = get_frame(b["sym"], b["tf"])
        if fr is None:
            print("缺行情", b["名字"], flush=True)
            continue
        end_ts = fr.index.max()
        locked = mk_bopb(b["sym"], b["tf"], b["box"], b["off"], b["rsi"], b["z"], b["hold"], b["tp"], b["lock"])
        unlocked = mk_bopb(b["sym"], b["tf"], b["box"], b["off"], b["rsi"], b["z"], b["hold"], b["tp"], None)
        for days, wname in ((90, "短窗3个月"), (730, "长窗2年")):
            r1 = run_one(b["名字"] + "·有时段锁·" + wname, b["区域"], locked, fr, end_ts, days)
            r0 = run_one(b["名字"] + "·无时段锁·" + wname, b["区域"], unlocked, fr, end_ts, days)
            rows.append(r1)
            rows.append(r0)
            print(json.dumps(r1, ensure_ascii=False), flush=True)
            print(json.dumps(r0, ensure_ascii=False), flush=True)

    # 汇总：长窗可进入复核？有锁是否显著好于无锁？
    long_locked = [r for r in rows if r.get("窗口天") == 730 and "有时段锁" in str(r.get("名字")) and r.get("状态") == "ok"]
    enter = [r for r in long_locked if r.get("可进入复核")]
    improved = []
    for r in long_locked:
        base_name = r["名字"].replace("·有时段锁·长窗2年", "")
        twin = next(
            (x for x in rows if x.get("名字") == base_name + "·无时段锁·长窗2年" and x.get("状态") == "ok"),
            None,
        )
        if not twin:
            continue
        a = r.get("平均每笔百分")
        b = twin.get("平均每笔百分")
        if a is None or b is None:
            continue
        # 显著：有锁期望提升至少 1 个百分点，或从负翻正
        flip = (b <= 0 and a > 0)
        better = (a - b) >= 1.0
        improved.append({
            "策略": base_name,
            "有锁平均每笔百分": a,
            "无锁平均每笔百分": b,
            "提升百分点": round(a - b, 3),
            "从负翻正": flip,
            "提升够明显": bool(flip or better),
            "有锁可进入复核": r.get("可进入复核"),
        })

    out = {
        "约定": {
            "短窗": "3个月",
            "长窗": "2年",
            "之外": "不考虑",
            "排除": "已过复核且在自动交易的策略（含ADA5顺势回升、ADA亚盘高突破回踩再进、以及eligible=true的时段策略）",
            "进入复核门槛_长窗": "至少10笔、胜率不少于50%、平均每笔为正",
        },
        "全部对照": rows,
        "长窗有锁可进入复核": enter,
        "有锁相对无锁是否明显变好": improved,
        "一句话": (
            "若长窗有锁可进入复核非空，则可提交四阶段复核；否则时段锁未能把区域策略救进复核门。"
            if enter else
            "在锁时段之后，仍没有策略同时满足长窗笔数/胜率/正期望，不能进入复核。"
        ),
    }
    path = OUT / "session_region_lock_audit_3m_2y.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("写入", path, flush=True)
    print("可进入复核条数", len(enter), flush=True)
    for x in improved:
        print("对照", json.dumps(x, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
