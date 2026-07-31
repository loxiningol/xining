#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused rescue on near-miss mutation slots (BNB15m / XAG5m). No auto-mount.

Grid kept tiny: only exit/hold/session-box knobs around the best near-misses.
"""
from __future__ import print_function

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from screen_ada5_bopb_mutation_ports import (  # noqa: E402
    ensure_asia_session, load_frame, metrics, _knum, mk_trendpb, mk_bopb,
)
import auto_trade_strategy_dsl as dsl_mod

OUT = (
    ROOT / "strategies" / "ada5_bopb_mutation_ports_v1" / "_scratch"
    / "mutation_ports_rescue_v1.json"
)


def ensure_london(frame):
    import numpy as np
    import pandas as pd
    if "london_high" in frame.columns:
        return frame
    fr = frame.copy()
    idx = fr.index
    idx_utc = idx.tz_convert("UTC") if getattr(idx, "tz", None) is not None else idx
    hour_utc = pd.Series(
        [float(ts.hour) + float(ts.minute) / 60.0 for ts in idx_utc], index=fr.index
    )
    day_keys = pd.Series([ts.strftime("%Y-%m-%d") for ts in idx_utc], index=fr.index)
    mask = (hour_utc >= 8.0) & (hour_utc < 12.5)
    h = fr["high"].astype(float)
    l = fr["low"].astype(float)
    hi_map, lo_map = {}, {}
    if bool(mask.any()):
        g = pd.DataFrame({"high": h[mask], "low": l[mask], "day": day_keys[mask].values})
        gg = g.groupby("day")
        hi_map = gg["high"].max().to_dict()
        lo_map = gg["low"].min().to_dict()
    days = sorted(hi_map)
    prev = {d: days[i - 1] if i else None for i, d in enumerate(days)}
    hv, lv = [], []
    for hr, day in zip(hour_utc.tolist(), day_keys.tolist()):
        key = day if float(hr) >= 12.5 else prev.get(day)
        if key is None or key not in hi_map:
            hv.append(np.nan)
            lv.append(np.nan)
        else:
            hv.append(float(hi_map[key]))
            lv.append(float(lo_map[key]))
    fr["london_high"] = hv
    fr["london_low"] = lv
    return fr


def with_tp(dsl, tp_rsi):
    out = json.loads(json.dumps(dsl))
    for cond in out.get("exit", {}).get("any", []):
        if cond.get("id") == "tp":
            cond["right"] = {"value": float(tp_rsi)}
    return out


def run_cases(cases, hits, near, counters):
    for case in cases:
        sym, tf, names = case["sym"], case["tf"], case["names"]
        fr0, src = load_frame(names)
        if fr0 is None:
            print("NO_FRAME", sym, tf, flush=True)
            continue
        fr = ensure_london(ensure_asia_session(fr0))
        tag = sym.split("-")[0].lower()
        print("FRAME", sym, tf, "n", len(fr), "n_cases", len(case["grid"]), flush=True)
        for family, rsi, z, h, slope, off, tp, use_london in case["grid"]:
            box = "lon" if use_london else "asia"
            if family == "trendpb":
                key = "res_tpb_%s_%s_r%s_z%s_h%s_tp%s" % (
                    tag, tf, _knum(rsi), _knum(z), int(h), _knum(tp))
                dsl = mk_trendpb(key, "突变救援顺势", tf, rsi, z, h, slope)
            else:
                key = "res_bopb_%s_%s_%s_o%s_r%s_z%s_h%s_tp%s" % (
                    tag, tf, box, int(off), _knum(rsi), _knum(z), int(h), _knum(tp))
                dsl = mk_bopb(key, "突变救援突破回踩", tf, rsi, z, h, slope, off)
                if use_london:
                    for cond in dsl["entry"]["all"]:
                        if cond.get("id") == "bo":
                            cond["right"]["feature"] = "london_high"
            dsl = with_tp(dsl, tp)
            dsl["supported_instruments"] = [sym]
            dsl["origin"] = "ada5_bopb_mutation_ports_v1_rescue"
            counters["scanned"] += 1
            try:
                dsl_mod.validate_strategy(dsl)
                res = dsl_mod.backtest_dsl(fr, dsl, stop_loss_pct=0.009)
            except Exception as exc:
                counters["errors"] += 1
                if counters["errors"] <= 6:
                    print("ERR", key, type(exc).__name__, exc, flush=True)
                continue
            m = metrics(res, fr)
            n = int(m.get("trades") or 0)
            if n < 8:
                continue
            row = {
                "family": family, "sym": sym, "tf": tf,
                "rsi": rsi, "z": z, "hold": h, "slope": slope,
                "asia_offset": off, "tp_rsi": tp, "session_box": box,
                "key": key, "src": src, **m,
            }
            if n >= 8 and m.get("wr", 0) >= 0.45 and m.get("mean_net", 0) > -0.005:
                near.append(row)
            if n >= 10 and m.get("wr", 0) >= 0.50 and m.get("mean_net", 0) > 0:
                hits.append(row)
                print(
                    "HIT", family, sym, tf, box,
                    "wr", round(m["wr"], 3), "n", n,
                    "mean", round(m["mean_net"], 5),
                    "tp", tp, "h", h, "r", rsi, "off", off,
                    flush=True,
                )
        print("DONE", sym, tf, "scanned", counters["scanned"], flush=True)


def main():
    # Around best nears from core/expand screen.
    bnb_grid = []
    for family in ("trendpb", "bopb"):
        for rsi in (42.0, 45.0, 48.0):
            for z in (2.3, 2.6):
                for h in (12, 14, 18, 24, 36):
                    for tp in (55.0, 58.0, 60.0, 65.0):
                        if family == "trendpb":
                            bnb_grid.append((family, rsi, z, h, 0.0, None, tp, False))
                        else:
                            for off in (16, 20, 24):
                                for lon in (False, True):
                                    bnb_grid.append(
                                        (family, rsi, z, h, 0.0, off, tp, lon)
                                    )

    xag_grid = []
    for family in ("trendpb", "bopb"):
        for rsi in (38.0, 40.0, 42.0, 45.0):
            for z in (2.0, 2.3, 2.6, 3.0):
                for h in (12, 14, 18, 24):
                    for tp in (55.0, 58.0, 60.0):
                        if family == "trendpb":
                            xag_grid.append((family, rsi, z, h, 0.0, None, tp, False))
                        else:
                            for off in (12, 16, 20, 24, 28):
                                xag_grid.append(
                                    (family, rsi, z, h, 0.0, off, tp, False)
                                )

    cases = [
        {
            "sym": "BNB-USDT-SWAP",
            "tf": "15m",
            "names": ["frame_BNB_USDT_SWAP_15m.pkl"],
            "grid": bnb_grid,
        },
        {
            "sym": "XAG-USDT-SWAP",
            "tf": "5m",
            "names": ["frame_XAG_USDT_SWAP_5m.pkl"],
            "grid": xag_grid,
        },
    ]
    print("N_BNB", len(bnb_grid), "N_XAG", len(xag_grid), flush=True)
    hits, near = [], []
    counters = {"scanned": 0, "errors": 0}
    run_cases(cases, hits, near, counters)
    hits.sort(key=lambda r: (r["wr"], r["mean_net"], r["trades"]), reverse=True)
    near.sort(key=lambda r: (r["wr"], r["mean_net"], r["trades"]), reverse=True)
    payload = {
        "ok": True,
        "scanned": counters["scanned"],
        "errors": counters["errors"],
        "pos_hits": len(hits),
        "top": hits[:30],
        "near_miss_top": near[:40],
        "note_zh": "救援网格：围绕 BNB15m / XAG5m near-miss 调节 TP/持有/会话箱",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", OUT, "pos", len(hits), flush=True)
    for r in (hits or near)[:12]:
        print(
            ("HIT" if r in hits else "NEAR"),
            r["family"], r["sym"], r["tf"], r.get("session_box"),
            "wr", round(r["wr"], 3), "n", r["trades"],
            "mean", round(r["mean_net"], 5),
            "tp", r["tp_rsi"], "h", r["hold"], "r", r["rsi"],
            flush=True,
        )


if __name__ == "__main__":
    main()
