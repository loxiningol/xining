#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe Round-3 incremental patches on cci>90 stack."""
from __future__ import print_function

import json
import sys

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

pkg = json.load(open("/root/auto_trade/dual_engine/frost2_cl_entry_r1_package.json"))
loser_times = [x["entry_time"] for x in pkg["losers"]]
winner_times = [x["entry_time"] for x in pkg["winners_sample"]]
fr = d._frame("CL-USDT-SWAP", "15m")


def base_entry(cci=90.0, rsi=54.0, z=0.1, extras=None):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z)}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
        {"left": {"feature": "cci"}, "op": "gt", "right": {"value": float(cci)}},
    ]
    if extras:
        entry.extend(extras)
    return entry


def _safe_tag(tag):
    s = str(tag or "r3")
    for ch in (".", " ", "/", ":", "+", "%"):
        s = s.replace(ch, "p" if ch == "." else "_")
    return s[:48]


def make(tag, **kw):
    dsl = {
        "key": "probe_r3_%s" % _safe_tag(tag),
        "name": "p",
        "direction": "short",
        "entry": {"all": base_entry(**kw)},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
        "max_hold_bars": 12,
    }
    return {
        "symbol": "CL-USDT-SWAP", "timeframe": "15m", "direction": "short",
        "logic_class": "exhaustion_fade", "thesis": "p", "title": "p",
        "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"),
        "gate_mode": "frost2", "source": "p",
    }


def inspect(tag, book):
    bt = d._backtest(book["dsl"], "CL-USDT-SWAP", "15m", "observed_base")
    trades = bt.get("trades") or []
    m = d._metrics_from_trades(trades)
    times = set(t.get("entry_time") for t in trades)
    blocked = sum(1 for t in loser_times if t not in times)
    kept = sum(1 for t in winner_times if t in times)
    hard_n = sum(
        1 for t in trades
        if t.get("stop_loss") or float(t.get("pnl_ratio") or 0) < -0.15
    )
    print(
        "===", tag, "tr", m["trades"], "fp", m["fold_positive"], "/", m["folds"],
        "wr", round(m["win_rate_pct"], 1), "sh", m["sharpe"],
        "blocked", blocked, "kept", kept, "hard", hard_n
    )
    for t in trades:
        if t.get("stop_loss") or float(t.get("pnl_ratio") or 0) < -0.15:
            ei = t.get("entry_index")
            feats = {}
            if ei is not None:
                row = fr.iloc[int(ei)]
                for k in [
                    "rsi14", "z20", "cci", "atr14", "macd_stick",
                    "k", "d", "j", "close", "ema16", "prev_high20", "h1_slope4",
                ]:
                    if k in fr.columns:
                        feats[k] = float(row[k])
            print(
                " HARD", t.get("entry_time"),
                "pnl", round(float(t.get("pnl_ratio") or 0), 4),
                "orig_loser", t.get("entry_time") in loser_times,
                feats,
            )
    return m, trades, blocked, kept, hard_n


def main():
    grid = []
    for z in [0.1, 0.09, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01, 0.0]:
        grid.append(("z_%.2f" % z, dict(cci=90, z=z)))
    for rsi in [54.0, 53.8, 53.5, 53.2, 53.0, 52.8, 52.5]:
        grid.append(("rsi_%.1f" % rsi, dict(cci=90, rsi=rsi)))
    for rsi, z in [
        (53.5, 0.08), (53.5, 0.05), (53.0, 0.08), (53.0, 0.05),
        (53.8, 0.05), (52.5, 0.08), (53.2, 0.07),
    ]:
        grid.append(("rsi%.1f_z%.2f" % (rsi, z), dict(cci=90, rsi=rsi, z=z)))
    for a in [0.25, 0.28, 0.32, 0.35]:
        grid.append((
            "cci90_atr%.2f" % a,
            dict(cci=90, extras=[{
                "left": {"feature": "atr14"}, "op": "gt",
                "right": {"value": a},
            }]),
        ))
    for z, a in [
        (0.0, 0.32), (0.0, 0.35), (0.05, 0.32), (0.05, 0.30),
        (0.0, 0.40), (0.02, 0.32), (0.0, 0.31),
    ]:
        grid.append((
            "z%.2f_atr%.2f" % (z, a),
            dict(cci=90, z=z, extras=[{
                "left": {"feature": "atr14"}, "op": "gt",
                "right": {"value": a},
            }]),
        ))
    # atr ceiling to avoid? unlikely
    # j floor under soften
    for z, jlo in [(0.0, 50), (0.0, 55), (0.05, 50), (0.0, 45)]:
        grid.append((
            "z%.2f_j%.0f" % (z, jlo),
            dict(cci=90, z=z, extras=[{
                "left": {"feature": "j"}, "op": "gt",
                "right": {"value": float(jlo)},
            }]),
        ))
    # k floor
    for z, klo in [(0.0, 65), (0.0, 70), (0.05, 65)]:
        grid.append((
            "z%.2f_k%.0f" % (z, klo),
            dict(cci=90, z=z, extras=[{
                "left": {"feature": "k"}, "op": "gt",
                "right": {"value": float(klo)},
            }]),
        ))

    good = []
    near = []
    for tag, kw in grid:
        book = make(tag, **kw)
        m, trades, blocked, kept, hard_n = inspect(tag, book)
        if (
            blocked == 2 and kept >= 6 and m["folds"] >= 10
            and m["fold_positive"] >= 7 and hard_n == 0
        ):
            good.append(tag)
            print("  >>> CANDIDATE", tag)
        if (
            blocked == 2 and kept >= 6 and m["folds"] >= 10
            and m["fold_positive"] >= 7
        ):
            near.append((tag, hard_n, m["sharpe"], m["win_rate_pct"]))
    print("GOOD", good)
    print("NEAR_WF7", near)

    print("\nDETAIL z00")
    inspect("z00", make("z00", cci=90, z=0.0))
    print("\nDETAIL rsi53")
    inspect("rsi53", make("rsi53", cci=90, rsi=53))


if __name__ == "__main__":
    main()
