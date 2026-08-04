#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused exit/hold search around best DOGE5m micro pockets."""
from __future__ import print_function

import json
import pickle
import sys

import numpy as np

sys.path.insert(0, "/root")
import auto_trade_dual_engine_factory as d
import auto_trade_strategy_dsl as dsl
import frost2_action_run as f2

SYMBOL = "DOGE-USDT-SWAP"
TF = "5m"
OUT = "/root/auto_trade/dual_engine/frost3_doge5m_microedge_grid2_scan.json"


def main():
    fr = pickle.load(open(
        "/root/auto_trade/codex_0725_train5/frame_DOGE_USDT_SWAP_5m.pkl", "rb"))
    fr = fr.iloc[-5000:].copy()
    hour = fr.index.hour.astype(float)
    fr["hour_utc"] = hour
    # tighter US cash hours
    fr["session_liq"] = ((hour >= 14) & (hour < 20)).astype(float)
    fr["session_wide"] = ((hour >= 13) & (hour < 22)).astype(float)
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
    fr["vol_burst"] = (
        fr["vol_accept_proxy"] / fr["vol_accept_proxy"].shift(1).replace(0, np.nan)
    ).fillna(1.0)
    # quiet→burst: prior vol below 1.0 then burst
    fr["prior_quiet"] = (fr["vol_accept_proxy"].shift(1) < 1.05).astype(float)

    d._frame = lambda s, t: fr
    for name in (
        "session_liq", "session_wide", "hour_utc", "vol_accept_proxy",
        "auction_accept", "auction_reject", "close_loc", "vol_burst", "prior_quiet",
    ):
        dsl.FEATURES.add(name)

    def run(direction, entry, hold, exit_mode="level"):
        entry = [dict(x) for x in entry]
        for i, e in enumerate(entry):
            e["id"] = "e%d" % (i + 1)
        if exit_mode == "level":
            if direction == "long":
                exit_any = [
                    {"left": {"feature": "close"}, "op": "gt",
                     "right": {"feature": "prev_high20"}, "role": "take_profit", "id": "x1"},
                    {"left": {"feature": "close"}, "op": "lt",
                     "right": {"feature": "prev_low20"}, "role": "invalidation", "id": "x2"},
                ]
            else:
                exit_any = [
                    {"left": {"feature": "close"}, "op": "lt",
                     "right": {"feature": "prev_low20"}, "role": "take_profit", "id": "x1"},
                    {"left": {"feature": "close"}, "op": "gt",
                     "right": {"feature": "prev_high20"}, "role": "invalidation", "id": "x2"},
                ]
        else:  # close_loc exit
            if direction == "long":
                exit_any = [
                    {"left": {"feature": "close_loc"}, "op": "gt",
                     "right": {"value": 0.85}, "role": "take_profit", "id": "x1"},
                    {"left": {"feature": "close_loc"}, "op": "lt",
                     "right": {"value": 0.25}, "role": "invalidation", "id": "x2"},
                ]
            else:
                exit_any = [
                    {"left": {"feature": "close_loc"}, "op": "lt",
                     "right": {"value": 0.15}, "role": "take_profit", "id": "x1"},
                    {"left": {"feature": "close_loc"}, "op": "gt",
                     "right": {"value": 0.75}, "role": "invalidation", "id": "x2"},
                ]
        key = "g2_%s_%d" % (direction, abs(hash(json.dumps([entry, hold, exit_mode], sort_keys=True))) % 10 ** 8)
        book = {
            "dsl": {
                "schema": "qiyu_strategy_dsl_v1", "key": key, "name": "g2",
                "direction": direction, "timeframe": TF,
                "supported_instruments": [SYMBOL],
                "entry": {"all": entry}, "exit": {"any": exit_any},
                "max_hold_bars": hold,
            },
            "symbol": SYMBOL, "timeframe": TF, "direction": direction,
            "gate_mode": "frost2",
        }
        packs = f2.quick_suite(book)
        bm = packs.get("base_metrics") or {}
        days = len(fr) / 288.0
        return {
            "direction": direction,
            "pass": packs.get("quick_pass"),
            "fp": bm.get("fold_positive"), "tr": bm.get("trades"),
            "wr": bm.get("win_rate_pct"), "mean": bm.get("mean_net"),
            "sharpe": bm.get("sharpe"),
            "opd": round((bm.get("trades") or 0) / max(days, 1), 3),
            "failed": packs.get("failed_step"),
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
            "hold": hold, "exit_mode": exit_mode,
        }

    rows = []
    # Pocket A: long extreme vol accept
    for sess_feat in ("session_liq", "session_wide"):
        for vol in (2.4, 2.6, 2.8, 3.0):
            for use_quiet in (False, True):
                for use_burst in (False, True):
                    for hold in (16, 24, 36, 48):
                        for exit_mode in ("level", "loc"):
                            entry = [
                                {"left": {"feature": sess_feat}, "op": "gt", "right": {"value": 0.5}},
                                {"left": {"feature": "vol_accept_proxy"}, "op": "gt", "right": {"value": float(vol)}},
                                {"left": {"feature": "auction_accept"}, "op": "gt", "right": {"value": 0.5}},
                                {"left": {"feature": "close_loc"}, "op": "gt", "right": {"value": 0.65}},
                                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_low20"}},
                            ]
                            if use_quiet:
                                entry.append({"left": {"feature": "prior_quiet"}, "op": "gt", "right": {"value": 0.5}})
                            if use_burst:
                                entry.append({"left": {"feature": "vol_burst"}, "op": "gt", "right": {"value": 1.25}})
                            try:
                                r = run("long", entry, hold, exit_mode)
                            except Exception as exc:
                                r = {"error": str(exc), "tr": 0, "fp": 0}
                            r.update({
                                "tag": "long_accept", "sess": sess_feat, "vol": vol,
                                "quiet": use_quiet, "burst": use_burst,
                            })
                            rows.append(r)

    # Pocket B: short reject + burst
    for vol in (1.7, 1.9, 2.1):
        for hold in (12, 20, 28, 40):
            for exit_mode in ("level", "loc"):
                entry = [
                    {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}},
                    {"left": {"feature": "vol_accept_proxy"}, "op": "gt", "right": {"value": float(vol)}},
                    {"left": {"feature": "auction_reject"}, "op": "gt", "right": {"value": 0.5}},
                    {"left": {"feature": "close_loc"}, "op": "lt", "right": {"value": 0.25}},
                    {"left": {"feature": "vol_burst"}, "op": "gt", "right": {"value": 1.25}},
                    {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_high20"}},
                ]
                try:
                    r = run("short", entry, hold, exit_mode)
                except Exception as exc:
                    r = {"error": str(exc), "tr": 0, "fp": 0}
                r.update({"tag": "short_reject", "vol": vol})
                rows.append(r)

    ranked = sorted(
        [r for r in rows if (r.get("tr") or 0) >= 8],
        key=lambda x: (x.get("fp") or 0, x.get("sharpe") or -99, x.get("wr") or 0),
        reverse=True,
    )
    print("n", len(rows), "ge8", len(ranked), flush=True)
    for r in ranked[:15]:
        print(json.dumps({
            k: r.get(k) for k in (
                "tag", "direction", "vol", "sess", "quiet", "burst", "hold",
                "exit_mode", "pass", "fp", "tr", "wr", "mean", "sharpe", "opd",
                "failed", "dest",
            )
        }, ensure_ascii=False), flush=True)
    open(OUT, "w").write(json.dumps({"ranked": ranked[:40], "n": len(rows)}, ensure_ascii=False, indent=2) + "\n")
    print("wrote", OUT, flush=True)
    return 0


if __name__ == "__main__":
    # fix missing direction in return for long runs
    raise SystemExit(main())
