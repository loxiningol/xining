#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lightweight microstructure grid scan for DOGE 5m (artifact-prefixed)."""
from __future__ import print_function

import json
import os
import pickle
import sys

import numpy as np

sys.path.insert(0, "/root")
import auto_trade_dual_engine_factory as d
import auto_trade_strategy_dsl as dsl
import frost2_action_run as f2

SYMBOL = "DOGE-USDT-SWAP"
TF = "5m"
OUT = "/root/auto_trade/dual_engine/frost3_doge5m_microedge_grid_scan.json"


def main():
    fr = pickle.load(open(
        "/root/auto_trade/codex_0725_train5/frame_DOGE_USDT_SWAP_5m.pkl", "rb"))
    fr = fr.iloc[-4000:].copy()
    hour = fr.index.hour.astype(float)
    fr["hour_utc"] = hour
    fr["session_liq"] = ((hour >= 13) & (hour < 22)).astype(float)
    hi = fr["high"].astype(float)
    lo = fr["low"].astype(float)
    cl = fr["close"].astype(float)
    op = fr["open"].astype(float)
    rng = (hi - lo).replace(0, np.nan)
    med = rng.rolling(48, min_periods=12).median()
    fr["vol_accept_proxy"] = (rng / med).fillna(1.0)
    fr["close_loc"] = ((cl - lo) / rng).clip(0, 1).fillna(0.5)
    prev_low = fr["prev_low20"]
    prev_high = fr["prev_high20"]
    fr["auction_accept"] = (
        (cl > op) & (cl > prev_low) & (fr["close_loc"] > 0.55)
    ).astype(float)
    fr["auction_reject"] = (
        (cl < op) & (cl < prev_high) & (fr["close_loc"] < 0.40)
    ).astype(float)
    ret = cl.pct_change().abs().rolling(3, min_periods=1).mean().fillna(0)
    fr["funding_abs_proxy"] = (ret * 0.15).clip(0, 0.01)
    fr["oi_pulse_proxy"] = fr["vol_accept_proxy"].rolling(3, min_periods=1).mean().fillna(1.0)
    # vol impulse: current accept vs prior bar (acceptance burst)
    fr["vol_burst"] = (
        fr["vol_accept_proxy"] / fr["vol_accept_proxy"].shift(1).replace(0, np.nan)
    ).fillna(1.0)

    d._frame = lambda sym, tf: fr
    for name in (
        "session_liq", "hour_utc", "vol_accept_proxy", "funding_abs_proxy",
        "oi_pulse_proxy", "auction_accept", "auction_reject", "close_loc", "vol_burst",
    ):
        dsl.FEATURES.add(name)

    def eval_book(direction, entry, hold=32):
        entry = [dict(e) for e in entry]
        if direction == "long":
            exit_any = [
                {"left": {"feature": "close"}, "op": "gt",
                 "right": {"feature": "prev_high20"}, "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "lt",
                 "right": {"feature": "prev_low20"}, "role": "invalidation"},
            ]
        else:
            exit_any = [
                {"left": {"feature": "close"}, "op": "lt",
                 "right": {"feature": "prev_low20"}, "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "gt",
                 "right": {"feature": "prev_high20"}, "role": "invalidation"},
            ]
        for i, e in enumerate(entry):
            e["id"] = "e%d" % (i + 1)
        for i, e in enumerate(exit_any):
            e["id"] = "x%d" % (i + 1)
        key = "grid_%s_%d" % (direction, abs(hash(json.dumps(entry, sort_keys=True))) % 10 ** 8)
        dsl_obj = {
            "schema": "qiyu_strategy_dsl_v1", "key": key, "name": "grid",
            "direction": direction, "timeframe": TF,
            "supported_instruments": [SYMBOL],
            "entry": {"all": entry}, "exit": {"any": exit_any},
            "max_hold_bars": hold,
        }
        book = {
            "dsl": dsl_obj, "symbol": SYMBOL, "timeframe": TF,
            "direction": direction, "gate_mode": "frost2",
        }
        packs = f2.quick_suite(book)
        bm = packs.get("base_metrics") or {}
        days = 4000 / 288.0
        return {
            "pass": packs.get("quick_pass"),
            "fp": bm.get("fold_positive"),
            "tr": bm.get("trades"),
            "wr": bm.get("win_rate_pct"),
            "mean": bm.get("mean_net"),
            "sharpe": bm.get("sharpe"),
            "opd": round((bm.get("trades") or 0) / days, 3),
            "failed": packs.get("failed_step"),
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
        }

    rows = []
    specs = []
    for direction, auction, loc_op, loc_v in (
        ("long", "auction_accept", "gt", 0.65),
        ("long", "auction_accept", "gt", 0.75),
        ("short", "auction_reject", "lt", 0.30),
        ("short", "auction_reject", "lt", 0.25),
    ):
        for vol in (1.8, 2.2, 2.6, 3.0, 3.5):
            for burst in (None, 1.25, 1.5):
                entry = [
                    {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}},
                    {"left": {"feature": "vol_accept_proxy"}, "op": "gt", "right": {"value": float(vol)}},
                    {"left": {"feature": auction}, "op": "gt", "right": {"value": 0.5}},
                    {"left": {"feature": "close_loc"}, "op": loc_op, "right": {"value": float(loc_v)}},
                ]
                if burst is not None:
                    entry.append({
                        "left": {"feature": "vol_burst"}, "op": "gt",
                        "right": {"value": float(burst)},
                    })
                if direction == "long":
                    entry.append({
                        "left": {"feature": "close"}, "op": "gt",
                        "right": {"feature": "prev_low20"},
                    })
                else:
                    entry.append({
                        "left": {"feature": "close"}, "op": "lt",
                        "right": {"feature": "prev_high20"},
                    })
                specs.append((direction, vol, loc_v, burst, entry))

    print("n_specs", len(specs), flush=True)
    for i, (direction, vol, loc_v, burst, entry) in enumerate(specs):
        try:
            r = eval_book(direction, entry, hold=28)
        except Exception as exc:
            r = {"error": str(exc), "tr": 0, "fp": 0}
        r.update({
            "direction": direction, "vol": vol, "loc": loc_v, "burst": burst,
        })
        rows.append(r)
        if (i + 1) % 10 == 0:
            print("progress", i + 1, flush=True)

    ranked = sorted(
        [r for r in rows if (r.get("tr") or 0) >= 8],
        key=lambda x: (
            x.get("fp") or 0,
            x.get("sharpe") or -99,
            x.get("wr") or 0,
        ),
        reverse=True,
    )
    print("TOP", flush=True)
    for r in ranked[:12]:
        print(json.dumps({
            k: r.get(k) for k in (
                "direction", "vol", "loc", "burst", "pass", "fp", "tr", "wr",
                "mean", "sharpe", "opd", "failed", "dest",
            )
        }, ensure_ascii=False), flush=True)

    open(OUT, "w").write(json.dumps({
        "ranked": ranked[:30],
        "n": len(rows),
        "n_ge8": len(ranked),
    }, ensure_ascii=False, indent=2) + "\n")
    print("wrote", OUT, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
