#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import copy
import re
import sys

sys.path.insert(0, "/root")
import frost_action_run as f


def main():
    text = open("/root/auto_trade_strategy_dsl.py", "r", encoding="utf-8", errors="ignore").read()
    feats = sorted(set(re.findall(r"[\"']([a-z][a-z0-9_]{1,24})[\"']", text)))
    print(
        "feat-ish",
        [
            x
            for x in feats
            if any(k in x for k in ["ema", "rsi", "macd", "cci", "vol", "atr", "bb", "obv", "vwap", "body", "wick", "range"])
        ][:60],
    )

    variants = []
    variants.append(
        (
            "A_pullback",
            [
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema19"}},
                {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema19"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 48}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 62}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": -20}},
                {"left": {"feature": "cci"}, "op": "lt", "right": {"value": 120}},
            ],
            [{"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema19"}}],
            12,
        )
    )
    variants.append(
        (
            "B_tight",
            [
                {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema16"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema19"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema6"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 45}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 58}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
            ],
            [
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema19"}},
            ],
            10,
        )
    )
    variants.append(
        (
            "C_cci",
            [
                {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema16"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema16"}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": -50}},
                {"left": {"feature": "cci"}, "op": "lt", "right": {"value": 50}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 50}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 65}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
            ],
            [{"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 48}}],
            14,
        )
    )
    variants.append(
        (
            "D_sel",
            [
                {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema19"}},
                {"left": {"feature": "ema16"}, "op": "gt", "right": {"feature": "ema19"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema19"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 46}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 56}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 0}},
            ],
            [{"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema19"}}],
            8,
        )
    )
    variants.append(
        (
            "E_reclaim",
            [
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema19"}},
                {"left": {"feature": "ema8"}, "op": "gt", "right": {"feature": "ema19"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 52}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 60}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 20}},
                {"left": {"feature": "cci"}, "op": "lt", "right": {"value": 100}},
            ],
            [
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45}},
            ],
            9,
        )
    )

    base_dsl = f._dsl_for_logic("SOL-USDT-SWAP", "5m", "long", "impulse_continuation", 0)
    for name, entry, exit_any, hold in variants:
        dsl = copy.deepcopy(base_dsl)
        dsl["key"] = base_dsl["key"] + "_" + name
        dsl["entry"] = {"all": entry}
        dsl["exit"] = {"any": exit_any}
        dsl["max_hold_bars"] = hold
        book = {
            "symbol": "SOL-USDT-SWAP",
            "timeframe": "5m",
            "direction": "long",
            "logic_class": "impulse_continuation",
            "dsl": dsl,
        }
        try:
            packs = f.run_internal_suite(book)
            bm = packs.get("base_metrics") or {}
            print(
                name,
                "pass",
                packs.get("pass"),
                "suite",
                packs.get("suite"),
                "tr",
                bm.get("trades"),
                "wr",
                round(bm.get("win_rate_pct") or 0, 2),
                "sharpe",
                bm.get("sharpe"),
                "oos",
                round(bm.get("oos_profit") or 0, 4),
                "fp",
                bm.get("fold_positive"),
            )
        except Exception as exc:
            print(name, "ERR", exc)


if __name__ == "__main__":
    main()
