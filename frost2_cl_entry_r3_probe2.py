#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R3 follow-up: soften z under cci>90 while excluding Mar26 hardstop."""
from __future__ import print_function

import json
import sys

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

pkg = json.load(open("/root/auto_trade/dual_engine/frost2_cl_entry_r1_package.json"))
loser_times = [x["entry_time"] for x in pkg["losers"]]
winner_times = [x["entry_time"] for x in pkg["winners_sample"]]


def _safe_tag(tag):
    s = str(tag or "r3")
    for ch in (".", " ", "/", ":", "+", "%", "-"):
        s = s.replace(ch, "p" if ch in (".", "-") else "_")
    return s[:48]


def base_entry(cci=90.0, rsi=54.0, z=0.1, extras=None, macd_lt=0.0):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z)}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": float(macd_lt)}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
        {"left": {"feature": "cci"}, "op": "gt", "right": {"value": float(cci)}},
    ]
    if extras:
        entry.extend(extras)
    return entry


def make(tag, **kw):
    dsl = {
        "key": "probe_r3b_%s" % _safe_tag(tag),
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


def score(tag, book, run_quick=False):
    bt = d._backtest(book["dsl"], "CL-USDT-SWAP", "15m", "observed_base")
    trades = bt.get("trades") or []
    m = d._metrics_from_trades(trades)
    times = set(t.get("entry_time") for t in trades)
    blocked = sum(1 for t in loser_times if t not in times)
    kept = sum(1 for t in winner_times if t in times)
    hard = [t for t in trades if t.get("stop_loss") or float(t.get("pnl_ratio") or 0) < -0.15]
    print(
        "%s tr=%s wr=%.1f sh=%s folds=%s fp=%s blocked=%s/%s kept=%s/%s hard=%s fm=%s" % (
            tag, m["trades"], m["win_rate_pct"], m["sharpe"], m["folds"], m["fold_positive"],
            blocked, len(loser_times), kept, len(winner_times), len(hard), m.get("fold_means"),
        )
    )
    for t in hard:
        print("  HARD", t.get("entry_time"), t.get("pnl_ratio"))
    out = {
        "tag": tag, "m": m, "blocked": blocked, "kept": kept, "hard": len(hard),
        "book": book, "trades": trades,
    }
    if run_quick and blocked == 2 and kept >= 6 and m["folds"] >= 10 and m["fold_positive"] >= 7 and len(hard) == 0:
        f2.QUICK_WF_POS = 7
        d.FROST_RELAXED_WF_POS = 7
        q = f2.quick_suite(book)
        fr_m = ((q.get("extreme_friction") or {}).get("metrics") or {})
        print(
            "  QUICK pass=%s dest=%s fr_sh=%s failed=%s" % (
                q.get("quick_pass"),
                (q.get("logic_destruction") or {}).get("pass"),
                fr_m.get("sharpe"),
                q.get("failed_step"),
            )
        )
        out["quick"] = q
        out["friction_sharpe"] = fr_m.get("sharpe")
        if q.get("quick_pass"):
            full = f2.full_suite(book, q)
            print(
                "  FULL pass=%s fr=%s mc=%s failed=%s" % (
                    full.get("full_pass"),
                    (full.get("full") or {}).get("friction_sharpe"),
                    ((full.get("full") or {}).get("mc") or {}).get("beat_ratio"),
                    full.get("failed_step"),
                )
            )
            out["full"] = full
    return out


def main():
    # Mar26 hard: z=0.078, macd=-0.064, atr=0.487, j=56.05, h1_slope=0.0086
    cands = []
    for z in [0.03, 0.02, 0.01, 0.0]:
        # macd floor (exclude very negative)
        for mlo in [-0.06, -0.055, -0.05, -0.045]:
            cands.append((
                "z%.2f_macd_gt%.3f" % (z, mlo),
                dict(
                    cci=90, z=z,
                    extras=[{
                        "left": {"feature": "macd_stick"}, "op": "gt",
                        "right": {"value": float(mlo)},
                    }],
                ),
            ))
        # atr ceiling
        for ahi in [0.48, 0.45, 0.42]:
            cands.append((
                "z%.2f_atr_lt%.2f" % (z, ahi),
                dict(
                    cci=90, z=z,
                    extras=[{
                        "left": {"feature": "atr14"}, "op": "lt",
                        "right": {"value": float(ahi)},
                    }],
                ),
            ))
        # h1_slope ceiling
        for hs in [0.0085, 0.008, 0.007]:
            cands.append((
                "z%.2f_h1lt%.4f" % (z, hs),
                dict(
                    cci=90, z=z,
                    extras=[{
                        "left": {"feature": "h1_slope4"}, "op": "lt",
                        "right": {"value": float(hs)},
                    }],
                ),
            ))
        # j ceiling
        for jhi in [56.0, 55.0, 54.0]:
            cands.append((
                "z%.2f_jlt%.0f" % (z, jhi),
                dict(
                    cci=90, z=z,
                    extras=[{
                        "left": {"feature": "j"}, "op": "lt",
                        "right": {"value": float(jhi)},
                    }],
                ),
            ))
        # macd less negative only via tightening macd_lt (macd < -0.01 means more negative required - wrong direction)
        # require k < 71.5
        cands.append((
            "z%.2f_klt71p5" % z,
            dict(
                cci=90, z=z,
                extras=[{
                    "left": {"feature": "k"}, "op": "lt",
                    "right": {"value": 71.5},
                }],
            ),
        ))

    # also: raise cci slightly while softening z to avoid Mar26 (cci=127 - won't help)
    # cci90 + z0.03 + d < 79
    for z in [0.03, 0.0]:
        cands.append((
            "z%.2f_dlt79" % z,
            dict(
                cci=90, z=z,
                extras=[{
                    "left": {"feature": "d"}, "op": "lt",
                    "right": {"value": 79.0},
                }],
            ),
        ))

    survivors = []
    for tag, kw in cands:
        book = make(tag, **kw)
        r = score(tag, book, run_quick=False)
        if (
            r["blocked"] == 2 and r["kept"] >= 6 and r["hard"] == 0
            and r["m"]["folds"] >= 10 and r["m"]["fold_positive"] >= 7
        ):
            survivors.append((tag, kw, r["m"]["sharpe"], r["m"]["win_rate_pct"]))
            print("  >>> SURVIVOR", tag)

    print("SURVIVORS", survivors)
    # run gates on survivors
    for tag, kw, sh, wr in survivors:
        print("\nGATING", tag)
        score(tag + "_gate", make(tag + "_g", **kw), run_quick=True)

    # also try alt path: cci50 + j>60 (info) — only if no survivor, for archive note
    if not survivors:
        print("\nALT info cci50_j60")
        score(
            "cci50_j60",
            make(
                "cci50_j60",
                cci=50,
                extras=[{
                    "left": {"feature": "j"}, "op": "gt",
                    "right": {"value": 60.0},
                }],
            ),
            run_quick=True,
        )


if __name__ == "__main__":
    main()
