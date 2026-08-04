#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R3 dest-stable search on cci>90 + soft z + dist_ema16_atr."""
from __future__ import print_function

import json
import sys

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d
import auto_trade_strategy_dsl as dsl_mod

dsl_mod.FEATURES = set(dsl_mod.FEATURES) | {"dist_ema16_atr"}
_orig = d._frame


def _aug(symbol, timeframe):
    fr = _orig(symbol, timeframe).copy()
    fr["dist_ema16_atr"] = (fr["ema16"] - fr["close"]) / fr["atr14"].replace(0, 1e-9)
    return fr


d._frame = _aug

pkg = json.load(open("/root/auto_trade/dual_engine/frost2_cl_entry_r1_package.json"))
loser_times = [x["entry_time"] for x in pkg["losers"]]
winner_times = [x["entry_time"] for x in pkg["winners_sample"]]
f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7


def _safe(tag):
    s = str(tag)
    for ch in (".", "-", " ", "/", ":", "+", "%"):
        s = s.replace(ch, "p" if ch in (".", "-") else "_")
    return s[:40]


def make(cci, z, dthr, rsi=54.0):
    dsl = {
        "key": "r3s_%s" % _safe("%.0f_%.3f_%.2f_%.1f" % (cci, z, dthr, rsi)),
        "name": "p",
        "direction": "short",
        "entry": {"all": [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z)}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
            {"left": {"feature": "cci"}, "op": "gt", "right": {"value": float(cci)}},
            {"left": {"feature": "dist_ema16_atr"}, "op": "lt", "right": {"value": float(dthr)}},
        ]},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
        "max_hold_bars": 12,
    }
    return {
        "symbol": "CL-USDT-SWAP", "timeframe": "15m", "direction": "short",
        "logic_class": "exhaustion_fade", "thesis": "r3", "title": "r3",
        "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"),
        "gate_mode": "frost2", "source": "r3",
    }


def evaluate(cci, z, dthr, rsi=54.0, run_full=False):
    book = make(cci, z, dthr, rsi)
    bt = d._backtest(book["dsl"], "CL-USDT-SWAP", "15m", "observed_base")
    trades = bt.get("trades") or []
    m = d._metrics_from_trades(trades)
    times = set(t.get("entry_time") for t in trades)
    hard = sum(1 for t in trades if t.get("stop_loss") or float(t.get("pnl_ratio") or 0) < -0.15)
    blocked = sum(1 for t in loser_times if t not in times)
    kept = sum(1 for t in winner_times if t in times)
    shape = (
        blocked == 2 and kept >= 6 and hard == 0
        and m["folds"] >= 10 and m["fold_positive"] >= 7
    )
    if not shape:
        return None
    q = f2.quick_suite(book)
    fr = ((q.get("extreme_friction") or {}).get("metrics") or {}).get("sharpe")
    out = {
        "cci": cci, "z": z, "dthr": dthr, "rsi": rsi,
        "tr": m["trades"], "fp": m["fold_positive"], "folds": m["folds"],
        "wr": m["win_rate_pct"], "sh": m["sharpe"], "hard": hard,
        "blocked": blocked, "kept": kept,
        "quick": bool(q.get("quick_pass")),
        "dest": bool((q.get("logic_destruction") or {}).get("pass")),
        "fr": fr, "fail": q.get("failed_step"),
        "book": book, "q": q,
    }
    print(
        "HIT", out["cci"], out["z"], out["dthr"], out["rsi"],
        "tr", out["tr"], "fp", out["fp"], "quick", out["quick"],
        "dest", out["dest"], "fr", out["fr"], "fail", out["fail"],
    )
    if run_full and out["quick"]:
        full = f2.full_suite(book, q)
        out["full"] = bool(full.get("full_pass"))
        out["full_fr"] = (full.get("full") or {}).get("friction_sharpe")
        out["mc"] = ((full.get("full") or {}).get("mc") or {}).get("beat_ratio")
        print(" FULL", out["full"], "fr", out["full_fr"], "mc", out["mc"])
    return out


def shape_only(cci, z, dthr, rsi=54.0):
    book = make(cci, z, dthr, rsi)
    bt = d._backtest(book["dsl"], "CL-USDT-SWAP", "15m", "observed_base")
    trades = bt.get("trades") or []
    m = d._metrics_from_trades(trades)
    times = set(t.get("entry_time") for t in trades)
    hard = sum(1 for t in trades if t.get("stop_loss") or float(t.get("pnl_ratio") or 0) < -0.15)
    blocked = sum(1 for t in loser_times if t not in times)
    kept = sum(1 for t in winner_times if t in times)
    ok = (
        blocked == 2 and kept >= 6 and hard == 0
        and m["folds"] >= 10 and m["fold_positive"] >= 7
    )
    return ok, m, blocked, kept, hard, book


def main():
    shape_cfgs = []
    for cci in [90, 92, 94, 95, 97]:
        for z in [0.025, 0.03, 0.035, 0.04]:
            for dthr in [0.205, 0.22, 0.235, 0.25]:
                for rsi in [54.0, 54.3]:
                    ok, m, blocked, kept, hard, book = shape_only(cci, z, dthr, rsi)
                    if ok:
                        shape_cfgs.append({
                            "cci": cci, "z": z, "dthr": dthr, "rsi": rsi,
                            "tr": m["trades"], "fp": m["fold_positive"],
                            "sh": m["sharpe"], "book": book,
                        })
                        print("SHAPE", cci, z, dthr, rsi, "tr", m["trades"], "fp", m["fold_positive"], "sh", m["sharpe"])
    print("SHAPE_HITS", len(shape_cfgs))

    dest_ok = []
    for cfg in shape_cfgs:
        q = f2.quick_suite(cfg["book"])
        fr = ((q.get("extreme_friction") or {}).get("metrics") or {}).get("sharpe")
        row = {
            "cci": cfg["cci"], "z": cfg["z"], "dthr": cfg["dthr"], "rsi": cfg["rsi"],
            "tr": cfg["tr"], "fp": cfg["fp"], "sh": cfg["sh"],
            "quick": bool(q.get("quick_pass")),
            "dest": bool((q.get("logic_destruction") or {}).get("pass")),
            "fr": fr, "fail": q.get("failed_step"),
            "book": cfg["book"], "q": q,
        }
        print(
            "GATE", row["cci"], row["z"], row["dthr"], row["rsi"],
            "quick", row["quick"], "dest", row["dest"], "fr", row["fr"], "fail", row["fail"],
        )
        if row["dest"]:
            dest_ok.append(row)
    print("DEST_OK", len(dest_ok))
    for h in dest_ok:
        if h["quick"]:
            full = f2.full_suite(h["book"], h["q"])
            print(
                "FULL_CAND", h["cci"], h["z"], h["dthr"], h["rsi"],
                "full", full.get("full_pass"),
                "fr", (full.get("full") or {}).get("friction_sharpe"),
                "mc", ((full.get("full") or {}).get("mc") or {}).get("beat_ratio"),
            )
            h["full"] = bool(full.get("full_pass"))
            h["full_obj"] = full

    open("/root/auto_trade/dual_engine/frost2_cl_entry_r3_dest_search.json", "w").write(
        json.dumps({
            "shape_hits": [
                {k: v for k, v in h.items() if k != "book"} for h in shape_cfgs
            ],
            "dest_ok": [
                {k: v for k, v in h.items() if k not in ("book", "q", "full_obj")}
                for h in dest_ok
            ],
        }, ensure_ascii=False, indent=2, default=str)
    )


if __name__ == "__main__":
    main()
