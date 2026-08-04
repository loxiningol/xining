#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import print_function
import copy
import json
import sys

sys.path.insert(0, "/root")
import auto_trade_dual_engine_factory as d
import frost_action_run as f

OUT = "/root/auto_trade/dual_engine/frost_focus_best.json"


def full(symbol, tf, direction, logic, dsl):
    dsl = f._ensure_dsl(dsl, symbol, tf)
    book = {
        "symbol": symbol,
        "timeframe": tf,
        "direction": direction,
        "logic_class": logic,
        "dsl": dsl,
        "gate_mode": "frost_relaxed",
        "title_short": dsl.get("name"),
    }
    packs = d.run_internal_packs(book, mode="frost_relaxed")
    bm = packs.get("base_metrics") or {}
    fr = ((packs.get("extreme_friction") or {}).get("metrics") or {})
    print(
        ">>",
        symbol,
        dsl.get("key"),
        "PASS",
        packs.get("pass"),
        "fp",
        bm.get("fold_positive"),
        "tr",
        bm.get("trades"),
        "sh",
        bm.get("sharpe"),
        "wr",
        round(bm.get("win_rate_pct") or 0, 1),
        "mean",
        round(bm.get("mean_net") or 0, 5),
        "fr",
        fr.get("sharpe"),
        "dest",
        (packs.get("logic_destruction") or {}).get("pass"),
        flush=True,
    )
    return book, packs


def score_packs(packs):
    bm = packs.get("base_metrics") or {}
    return (
        (1 if packs.get("pass") else 0) * 1000
        + (bm.get("fold_positive") or 0) * 20
        + (bm.get("sharpe") or -9)
    )


def main():
    ada = json.load(open("/root/auto_trade/strategy_pending_human_confirm.json"))["items"][0]["dsl"]

    print("ADA sanity", flush=True)
    dsl = copy.deepcopy(ada)
    dsl["key"] = "ada_self_relax"
    full("ADA-USDT-SWAP", "5m", "long", "trend", dsl)

    print("SOL ada port", flush=True)
    dsl = copy.deepcopy(ada)
    dsl["key"] = "sol_ada_port"
    dsl["name"] = "寒霜-SOL-5m-impulse_continuation"
    dsl["supported_instruments"] = ["SOL-USDT-SWAP"]
    full("SOL-USDT-SWAP", "5m", "long", "impulse_continuation", dsl)

    print("DOGE focused", flush=True)
    best_doge = None
    for rsi_lo, hold, rsi_tp, zhi in [
        (35, 18, 55, 1.5),
        (35, 20, 52, 1.5),
        (35, 22, 50, 1.2),
        (34, 18, 55, 1.5),
        (36, 18, 55, 1.5),
        (35, 18, 58, 1.0),
        (35, 24, 55, 1.5),
        (33, 18, 52, 1.5),
        (35, 18, 55, 2.0),
        (37, 18, 55, 1.5),
        (35, 16, 54, 1.3),
        (32, 20, 55, 1.5),
        (35, 18, 48, 1.5),
        (30, 18, 55, 1.5),
        (38, 18, 55, 1.2),
    ]:
        dsl = {
            "key": "frost_doge_f_%s_h%s_t%s_z%s"
            % (rsi_lo, hold, rsi_tp, str(zhi).replace(".", "p")),
            "name": "寒霜-DOGE-1h-range_reclaim",
            "direction": "long",
            "timeframe": "1h",
            "supported_instruments": ["DOGE-USDT-SWAP"],
            "max_hold_bars": hold,
            "description": "DOGE range reclaim focused",
            "entry": {
                "all": [
                    {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi_lo)}},
                    {
                        "left": {"feature": "rsi14"},
                        "op": "lt",
                        "right": {"value": float(rsi_lo + 12)},
                    },
                    {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema19"}},
                    {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -1.5}},
                    {"left": {"feature": "z20"}, "op": "lt", "right": {"value": float(zhi)}},
                    {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
                ]
            },
            "exit": {
                "any": [
                    {
                        "left": {"feature": "rsi14"},
                        "op": "gt",
                        "right": {"value": float(rsi_tp)},
                        "role": "take_profit",
                    },
                    {
                        "left": {"feature": "close"},
                        "op": "lt",
                        "right": {"feature": "prev_low20"},
                        "role": "invalidation",
                    },
                ]
            },
        }
        book, packs = full("DOGE-USDT-SWAP", "1h", "long", "range_reclaim", dsl)
        sc = score_packs(packs)
        if best_doge is None or sc > best_doge[0]:
            best_doge = (sc, book, packs)
    print(
        "BEST_DOGE pass",
        best_doge[2].get("pass"),
        "fp",
        (best_doge[2].get("base_metrics") or {}).get("fold_positive"),
        flush=True,
    )

    print("XRP focused", flush=True)
    best_xrp = None
    for rsi_hi, hold, rsi_tp, zmin in [
        (55, 16, 45, 0.0),
        (58, 14, 48, 0.2),
        (60, 12, 50, 0.5),
        (52, 18, 40, 0.0),
        (62, 10, 48, 0.5),
        (50, 20, 38, 0.0),
        (57, 16, 42, 0.0),
        (63, 14, 50, 0.8),
        (56, 12, 44, 0.0),
        (59, 16, 46, 0.3),
    ]:
        dsl = {
            "key": "frost_xrp_f_%s_h%s" % (rsi_hi, hold),
            "name": "寒霜-XRP-15m-exhaustion_fade",
            "direction": "short",
            "timeframe": "15m",
            "supported_instruments": ["XRP-USDT-SWAP"],
            "max_hold_bars": hold,
            "description": "XRP exhaustion focused",
            "entry": {
                "all": [
                    {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi_hi)}},
                    {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(zmin)}},
                    {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
                    {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
                ]
            },
            "exit": {
                "any": [
                    {
                        "left": {"feature": "rsi14"},
                        "op": "lt",
                        "right": {"value": float(rsi_tp)},
                        "role": "take_profit",
                    },
                    {
                        "left": {"feature": "close"},
                        "op": "gt",
                        "right": {"feature": "prev_high20"},
                        "role": "invalidation",
                    },
                ]
            },
        }
        book, packs = full("XRP-USDT-SWAP", "15m", "short", "exhaustion_fade", dsl)
        sc = score_packs(packs)
        if best_xrp is None or sc > best_xrp[0]:
            best_xrp = (sc, book, packs)
    print(
        "BEST_XRP pass",
        best_xrp[2].get("pass"),
        "fp",
        (best_xrp[2].get("base_metrics") or {}).get("fold_positive"),
        flush=True,
    )

    print("SOL focused", flush=True)
    best_sol = None
    for rsi_e, zmax, hold, rsi_tp in [
        (42, 2.0, 14, 60),
        (42, 2.5, 16, 58),
        (40, 2.0, 18, 55),
        (45, 2.0, 12, 62),
        (42, 3.0, 14, 60),
        (38, 2.0, 14, 58),
        (42, 2.0, 20, 65),
        (48, 2.2, 10, 60),
        (42, 1.8, 14, 55),
        (35, 2.5, 16, 55),
    ]:
        dsl = {
            "key": "frost_sol_f_%s_z%s_h%s" % (rsi_e, str(zmax).replace(".", "p"), hold),
            "name": "寒霜-SOL-5m-impulse_continuation",
            "direction": "long",
            "timeframe": "5m",
            "supported_instruments": ["SOL-USDT-SWAP"],
            "max_hold_bars": hold,
            "description": "SOL impulse focused",
            "entry": {
                "all": [
                    {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
                    {"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0}},
                    {
                        "left": {"feature": "rsi14"},
                        "op": "cross_above",
                        "right": {"value": float(rsi_e)},
                    },
                    {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                    {"left": {"feature": "z20"}, "op": "lt", "right": {"value": float(zmax)}},
                ]
            },
            "exit": {
                "any": [
                    {
                        "left": {"feature": "rsi14"},
                        "op": "gt",
                        "right": {"value": float(rsi_tp)},
                        "role": "take_profit",
                    },
                    {
                        "left": {"feature": "close"},
                        "op": "lt",
                        "right": {"feature": "prev_low20"},
                        "role": "invalidation",
                    },
                ]
            },
        }
        book, packs = full("SOL-USDT-SWAP", "5m", "long", "impulse_continuation", dsl)
        sc = score_packs(packs)
        if best_sol is None or sc > best_sol[0]:
            best_sol = (sc, book, packs)
    print(
        "BEST_SOL pass",
        best_sol[2].get("pass"),
        "fp",
        (best_sol[2].get("base_metrics") or {}).get("fold_positive"),
        flush=True,
    )

    out = {
        "DOGE": {
            "pass": best_doge[2].get("pass"),
            "dsl": best_doge[1]["dsl"],
            "metrics": best_doge[2].get("base_metrics"),
            "friction": best_doge[2].get("extreme_friction"),
            "destruction": (best_doge[2].get("logic_destruction") or {}).get("pass"),
            "anti": (best_doge[2].get("anti_overfit") or {}).get("pass"),
        },
        "XRP": {
            "pass": best_xrp[2].get("pass"),
            "dsl": best_xrp[1]["dsl"],
            "metrics": best_xrp[2].get("base_metrics"),
            "friction": best_xrp[2].get("extreme_friction"),
            "destruction": (best_xrp[2].get("logic_destruction") or {}).get("pass"),
            "anti": (best_xrp[2].get("anti_overfit") or {}).get("pass"),
        },
        "SOL": {
            "pass": best_sol[2].get("pass"),
            "dsl": best_sol[1]["dsl"],
            "metrics": best_sol[2].get("base_metrics"),
            "friction": best_sol[2].get("extreme_friction"),
            "destruction": (best_sol[2].get("logic_destruction") or {}).get("pass"),
            "anti": (best_sol[2].get("anti_overfit") or {}).get("pass"),
        },
    }
    open(OUT, "w").write(json.dumps(out, ensure_ascii=False, indent=2))
    print(
        "SAVED",
        {
            k: {
                "pass": v["pass"],
                "fp": (v["metrics"] or {}).get("fold_positive"),
                "sh": (v["metrics"] or {}).get("sharpe"),
                "fr": ((v.get("friction") or {}).get("metrics") or {}).get("sharpe"),
                "anti": v.get("anti"),
                "dest": v.get("destruction"),
            }
            for k, v in out.items()
        },
        flush=True,
    )


if __name__ == "__main__":
    main()
