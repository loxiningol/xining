#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Screen cross-symbol / cross-TF ports of ADA5 trendpb + Asia BO-PB (no auto-mount).

Seed logics:
  - ADA5顺势回升: H1 EMA stack + slope>0 + RSI cross_above + close>ema21 + z20 cap
  - 亚盘高突破回踩再进: same skeleton + close.offset(N) > asia_high.offset(N)

Mutation axes (evolution / extrapolation, not same-symbol near-dup):
  - symbol port (ETH/BNB/SOL/LTC/XRP/AVAX/LINK/DOGE/XAG)
  - TF port when frame exists (5m / 15m)
  - mild param evolution around seed (rsi / z / hold / slope / asia_offset)
"""
from __future__ import print_function

import copy
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import auto_trade_strategy_dsl as dsl_mod

FRAME_DIR = ROOT / "strategies" / "ada5_bopb_mutation_ports_v1" / "_scratch" / "frames"
OUT_DIR = ROOT / "strategies" / "ada5_bopb_mutation_ports_v1" / "_scratch"
OUT_JSON = OUT_DIR / "mutation_ports_screen_v1.json"

# Prefer local SCP'd train5 frames; also accept VPS absolute paths when run remotely.
FRAME_CATALOG = [
    ("ETH-USDT-SWAP", "5m", ["frame_ETH_USDT_SWAP_5m.pkl"]),
    ("BNB-USDT-SWAP", "5m", ["frame_BNB_USDT_SWAP_5m.pkl"]),
    ("BNB-USDT-SWAP", "15m", ["frame_BNB_USDT_SWAP_15m.pkl"]),
    ("SOL-USDT-SWAP", "5m", ["frame_SOL_USDT_SWAP_5m.pkl"]),
    ("LTC-USDT-SWAP", "5m", ["frame_LTC_USDT_SWAP_5m.pkl"]),
    ("XRP-USDT-SWAP", "5m", ["frame_XRP_USDT_SWAP_5m.pkl"]),
    ("AVAX-USDT-SWAP", "5m", ["frame_AVAX_USDT_SWAP_5m.pkl"]),
    ("LINK-USDT-SWAP", "5m", ["frame_LINK_USDT_SWAP_5m.pkl"]),
    ("DOGE-USDT-SWAP", "5m", ["frame_DOGE_USDT_SWAP_5m.pkl"]),
    ("XAG-USDT-SWAP", "5m", ["frame_XAG_USDT_SWAP_5m.pkl"]),
]

# Block exact live topologies on ADA 5m (near-duplicate of production seeds).
BLOCKED = {
    ("ADA-USDT-SWAP", "5m", "trendpb"),
    ("ADA-USDT-SWAP", "5m", "bopb"),
}

NEED_BASE = [
    "close", "open", "high", "low", "ema21", "rsi14", "z20",
    "h1_ema19", "h1_ema53", "h1_slope4", "prev_low20",
]


def ensure_asia_session(frame):
    """Inject asia_high/low (UTC Asia [00,08)) with no-lookahead rules from bt v2."""
    if frame is None:
        return frame
    if "asia_high" in frame.columns and "asia_low" in frame.columns:
        return frame
    fr = frame.copy()
    idx = fr.index
    if getattr(idx, "tz", None) is not None:
        idx_utc = idx.tz_convert("UTC")
    else:
        idx_utc = idx
    hour_utc = pd.Series(
        [float(ts.hour) + float(ts.minute) / 60.0 for ts in idx_utc],
        index=fr.index,
    )
    fr["hour_utc"] = hour_utc
    day_keys = pd.Series([ts.strftime("%Y-%m-%d") for ts in idx_utc], index=fr.index)
    asia_mask = hour_utc < 8.0
    h = fr["high"].astype(float)
    l = fr["low"].astype(float)
    asia_high_map = {}
    asia_low_map = {}
    if bool(asia_mask.any()):
        asia_df = pd.DataFrame({
            "high": h[asia_mask],
            "low": l[asia_mask],
            "day": day_keys[asia_mask].values,
        })
        grouped = asia_df.groupby("day")
        asia_high_map = grouped["high"].max().to_dict()
        asia_low_map = grouped["low"].min().to_dict()
    sorted_days = sorted(asia_high_map.keys())
    prev_day = {}
    for i, d in enumerate(sorted_days):
        prev_day[d] = sorted_days[i - 1] if i else None
    ah_vals = []
    al_vals = []
    for hr, day in zip(hour_utc.tolist(), day_keys.tolist()):
        key = day if float(hr) >= 8.0 else prev_day.get(day)
        if key is None or key not in asia_high_map:
            ah_vals.append(np.nan)
            al_vals.append(np.nan)
        else:
            ah_vals.append(float(asia_high_map[key]))
            al_vals.append(float(asia_low_map[key]))
    fr["asia_high"] = ah_vals
    fr["asia_low"] = al_vals
    fr["asia_mid"] = (fr["asia_high"] + fr["asia_low"]) * 0.5
    fr["asia_range"] = (fr["asia_high"] - fr["asia_low"]).astype(float)
    return fr


def load_frame(names):
    for name in names:
        for base in (FRAME_DIR, Path("/root/auto_trade/codex_0725_train5"), Path(".")):
            fp = base / name if not str(name).startswith("/") else Path(name)
            if fp.exists():
                obj = pickle.load(open(str(fp), "rb"))
                if hasattr(obj, "columns") and len(obj) > 500:
                    return obj, str(fp)
    return None, None


def mk_trendpb(key, name, tf, rsi, zmax, hold, slope_min):
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key,
        "name": name,
        "direction": "long",
        "timeframe": tf,
        "entry": {
            "all": [
                {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "gt",
                 "right": {"feature": "h1_ema53"}},
                {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gt",
                 "right": {"value": float(slope_min)}},
                {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_above",
                 "right": {"value": float(rsi)}},
                {"id": "px", "left": {"feature": "close"}, "op": "gt",
                 "right": {"feature": "ema21"}},
                {"id": "z", "left": {"feature": "z20"}, "op": "lt",
                 "right": {"value": float(zmax)}},
            ]
        },
        "exit": {
            "any": [
                {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
                 "right": {"value": 60.0}, "role": "take_profit"},
                {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                 "right": {"feature": "prev_low20"}, "role": "invalidation"},
            ]
        },
        "max_hold_bars": int(hold),
        "auto_trade_eligible": False,
        "live_enabled": False,
        "origin": "ada5_bopb_mutation_ports_v1",
        "description": "ADA5顺势回升异标的/异周期移植+轻度参数演化",
    }


def mk_bopb(key, name, tf, rsi, zmax, hold, slope_min, asia_off):
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key,
        "name": name,
        "direction": "long",
        "timeframe": tf,
        "entry": {
            "all": [
                {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "gt",
                 "right": {"feature": "h1_ema53"}},
                {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gt",
                 "right": {"value": float(slope_min)}},
                {"id": "bo", "left": {"feature": "close", "offset": int(asia_off)},
                 "op": "gt",
                 "right": {"feature": "asia_high", "offset": int(asia_off)}},
                {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_above",
                 "right": {"value": float(rsi)}},
                {"id": "px", "left": {"feature": "close"}, "op": "gt",
                 "right": {"feature": "ema21"}},
                {"id": "z", "left": {"feature": "z20"}, "op": "lt",
                 "right": {"value": float(zmax)}},
            ]
        },
        "exit": {
            "any": [
                {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
                 "right": {"value": 60.0}, "role": "take_profit"},
                {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                 "right": {"feature": "prev_low20"}, "role": "invalidation"},
            ]
        },
        "max_hold_bars": int(hold),
        "auto_trade_eligible": False,
        "live_enabled": False,
        "origin": "ada5_bopb_mutation_ports_v1",
        "description": "亚盘高突破回踩再进异标的/异周期移植+轻度参数演化",
    }


def metrics(res, frame):
    trades = res.get("trades") or []
    pnls = [float(t.get("pnl_ratio") or 0.0) for t in trades]
    n = len(pnls)
    if not n:
        return {"trades": 0}
    mean = sum(pnls) / float(n)
    wins = [p for p in pnls if p > 0]
    wr = len(wins) / float(n)
    try:
        span_days = max(1.0, (frame.index[-1] - frame.index[0]).total_seconds() / 86400.0)
    except Exception:
        span_days = max(1.0, len(frame) / 288.0)
    weekly = n * 7.0 / span_days
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for p in pnls:
        eq *= (1.0 + p)
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if dd > mdd:
            mdd = dd
    return {
        "trades": n,
        "wr": wr,
        "mean_net": mean,
        "total": eq - 1.0,
        "mdd": mdd,
        "span_days": round(span_days, 3),
        "weekly_opens_proxy": round(weekly, 4),
    }


def param_grid(mode="core"):
    """Two-phase grid to keep wall-clock sane on 12k-bar frames.

    core: seed-faithful + tight ring around seed (~40 variants)
    expand: wider ring used only on symbols that already show near-miss signal
    """
    rows = []
    # Seed-faithful
    rows.append(("trendpb", 42.0, 2.3, 14, 0.0, None))
    rows.append(("bopb", 42.0, 2.3, 14, 0.0, 20))
    if mode == "core":
        # tight ring: keep first-pass under ~50 variants
        for rsi in (40.0, 42.0, 45.0):
            for z in (2.0, 2.3, 2.6):
                for h in (12, 14, 18):
                    rows.append(("trendpb", rsi, z, h, 0.0, None))
                    rows.append(("bopb", rsi, z, h, 0.0, 20))
    else:
        # medium expand on promising slots only
        for rsi in (38.0, 40.0, 42.0, 45.0, 48.0):
            for z in (2.0, 2.3, 2.6):
                for h in (12, 14, 18, 24):
                    for slope in (0.0, 0.0005):
                        rows.append(("trendpb", rsi, z, h, slope, None))
                        for off in (16, 20, 24):
                            rows.append(("bopb", rsi, z, h, slope, off))
    seen = set()
    out = []
    for r in rows:
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
    return out


def _knum(x):
    return ("%g" % float(x)).replace("-", "m").replace(".", "p")


def run_on_frame(sym, tf, fr, src, variants, hits, near, counters):
    has_asia = "asia_high" in fr.columns and fr["asia_high"].notna().sum() > 100
    tag = sym.split("-")[0].lower()
    for family, rsi, z, h, slope, off in variants:
        if (sym, tf, family) in BLOCKED:
            continue
        if family == "bopb" and not has_asia:
            continue
        if family == "trendpb":
            key = "mut_tpb_%s_%s_r%s_z%s_h%s_s%s" % (
                tag, tf, _knum(rsi), _knum(z), int(h), _knum(slope))
            dsl = mk_trendpb(key, "顺势回升移植筛", tf, rsi, z, h, slope)
        else:
            key = "mut_bopb_%s_%s_o%s_r%s_z%s_h%s_s%s" % (
                tag, tf, int(off), _knum(rsi), _knum(z), int(h), _knum(slope))
            dsl = mk_bopb(key, "亚盘高突破回踩移植筛", tf, rsi, z, h, slope, off)
        dsl["supported_instruments"] = [sym]
        counters["scanned"] += 1
        try:
            dsl_mod.validate_strategy(dsl)
            # lean friction — same order of magnitude as production screen scripts
            res = dsl_mod.backtest_dsl(fr, dsl, stop_loss_pct=0.009)
        except Exception as exc:
            counters["errors"] += 1
            if counters["errors"] <= 8:
                print("ERR", key, type(exc).__name__, exc, flush=True)
            continue
        m = metrics(res, fr)
        n = int(m.get("trades") or 0)
        if n < 6:
            continue
        row = {
            "family": family, "sym": sym, "tf": tf,
            "rsi": rsi, "z": z, "hold": h, "slope": slope,
            "asia_offset": off, "key": key, "src": src, "dsl": dsl, **m,
        }
        # soft near-miss: enough sample, not catastrophic
        if n >= 8 and m.get("wr", 0) >= 0.40 and m.get("mean_net", 0) > -0.01:
            near.append({k: v for k, v in row.items() if k != "dsl"})
        if n >= 10 and m.get("wr", 0) >= 0.50 and m.get("mean_net", 0) > 0:
            hits.append(row)
            print(
                "HIT", family, sym, tf,
                "wr", round(m["wr"], 3), "n", n,
                "mean", round(m["mean_net"], 5),
                "wk", m.get("weekly_opens_proxy"),
                "r", rsi, "z", z, "h", h, "off", off,
                flush=True,
            )


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    core = param_grid("core")
    print("N_CORE_VARIANTS", len(core), flush=True)
    hits = []
    near = []
    counters = {"scanned": 0, "errors": 0}
    expand_slots = set()

    for sym, tf, names in FRAME_CATALOG:
        fr0, src = load_frame(names)
        if fr0 is None:
            print("NO_FRAME", sym, tf, flush=True)
            continue
        missing = [c for c in NEED_BASE if c not in fr0.columns]
        if missing:
            print("SKIP_COLS", sym, tf, missing, flush=True)
            continue
        fr = ensure_asia_session(fr0)
        print("FRAME", sym, tf, "n", len(fr), "src", src, flush=True)
        before_near = len(near)
        before_hits = len(hits)
        run_on_frame(sym, tf, fr, src, core, hits, near, counters)
        print(
            "DONE_CORE", sym, tf,
            "new_near", len(near) - before_near,
            "new_hits", len(hits) - before_hits,
            "scanned", counters["scanned"],
            flush=True,
        )
        # expand if any near-miss or hit on this slot
        slot_near = [
            r for r in near
            if r["sym"] == sym and r["tf"] == tf
        ]
        slot_hit = [r for r in hits if r["sym"] == sym and r["tf"] == tf]
        if slot_near or slot_hit:
            expand_slots.add((sym, tf))

    if expand_slots:
        expand = param_grid("expand")
        # only run expand variants not already in core
        core_set = set(core)
        expand = [v for v in expand if v not in core_set]
        print(
            "EXPAND_SLOTS", sorted(list(expand_slots)),
            "N_EXPAND", len(expand), flush=True,
        )
        for sym, tf, names in FRAME_CATALOG:
            if (sym, tf) not in expand_slots:
                continue
            fr0, src = load_frame(names)
            if fr0 is None:
                continue
            fr = ensure_asia_session(fr0)
            print("EXPAND_FRAME", sym, tf, flush=True)
            run_on_frame(sym, tf, fr, src, expand, hits, near, counters)
            print("DONE_EXPAND", sym, tf, "scanned", counters["scanned"], flush=True)
    else:
        print("NO_EXPAND_no_near_miss_on_core", flush=True)

    hits.sort(
        key=lambda r: (r.get("wr", 0), r.get("mean_net", 0), r.get("trades", 0)),
        reverse=True,
    )
    near.sort(
        key=lambda r: (r.get("wr", 0), r.get("mean_net", 0), r.get("trades", 0)),
        reverse=True,
    )

    best_by_slot = {}
    for h in hits:
        slot = (h["family"], h["sym"], h["tf"])
        cur = best_by_slot.get(slot)
        if cur is None or (h["wr"], h["mean_net"], h["trades"]) > (
            cur["wr"], cur["mean_net"], cur["trades"]
        ):
            best_by_slot[slot] = h

    # also keep best near-miss per slot for diagnostics
    best_near = {}
    for r in near:
        slot = (r["family"], r["sym"], r["tf"])
        cur = best_near.get(slot)
        if cur is None or (r["wr"], r["mean_net"], r["trades"]) > (
            cur["wr"], cur["mean_net"], cur["trades"]
        ):
            best_near[slot] = r

    payload = {
        "ok": True,
        "scanned": counters["scanned"],
        "errors": counters["errors"],
        "pos_hits": len(hits),
        "pos_slots": len(best_by_slot),
        "expand_slots": [
            {"sym": s, "tf": t} for s, t in sorted(expand_slots)
        ],
        "gates": {
            "min_trades": 10,
            "min_win_rate": 0.5,
            "require_positive_mean_net": True,
            "note_zh": "短窗 train5 特征帧预筛；交付前需更长窗/近2年周开仓复核",
        },
        "top": [{k: v for k, v in h.items() if k != "dsl"} for h in hits[:40]],
        "best_slots": [
            {k: v for k, v in h.items() if k != "dsl"} for h in best_by_slot.values()
        ],
        "best_near_by_slot": list(best_near.values())[:40],
        "near_miss_top": near[:40],
        "survivors_dsl": [
            {
                "slot": {"family": h["family"], "sym": h["sym"], "tf": h["tf"]},
                "metrics": {k: v for k, v in h.items() if k not in ("dsl",)},
                "dsl": h["dsl"],
            }
            for h in best_by_slot.values()
        ],
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", OUT_JSON, "pos_hits", len(hits), "slots", len(best_by_slot), flush=True)
    for h in list(best_by_slot.values())[:20]:
        print(
            "SLOT", h["family"], h["sym"], h["tf"],
            "wr", round(h["wr"], 3), "n", h["trades"],
            "mean", round(h["mean_net"], 5), "wk", h.get("weekly_opens_proxy"),
            flush=True,
        )
    if not hits and best_near:
        print("BEST_NEAR", flush=True)
        for r in list(best_near.values())[:15]:
            print(
                "NEAR", r["family"], r["sym"], r["tf"],
                "wr", round(r["wr"], 3), "n", r["trades"],
                "mean", round(r["mean_net"], 5),
                flush=True,
            )


if __name__ == "__main__":
    main()
