#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import copy
import sys

sys.path.insert(0, "/root")
import frost_action_run as f


VARIANTS = [
    (
        "F_ema21",
        [
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 48}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 57}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
            {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 10}},
            {"left": {"feature": "cci"}, "op": "lt", "right": {"value": 90}},
        ],
        [
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
        ],
        8,
    ),
    (
        "G_atr_filter",
        [
            {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema7"}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 50}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 58}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
            {"left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.05}},
        ],
        [{"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45}}],
        6,
    ),
    (
        "H_h1_align",
        [
            {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
            {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 47}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 56}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
        ],
        [
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
        ],
        10,
    ),
    (
        "I_ultra",
        [
            {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
            {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "ema16"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 49}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 55}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
            {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 20}},
            {"left": {"feature": "cci"}, "op": "lt", "right": {"value": 80}},
        ],
        [
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 46}},
        ],
        7,
    ),
    ("J_default", None, None, None),
]


def main():
    base = f._dsl_for_logic("SOL-USDT-SWAP", "5m", "long", "impulse_continuation", 0)
    for name, entry, exit_any, hold in VARIANTS:
        if name == "J_default":
            dsl = copy.deepcopy(base)
        else:
            dsl = copy.deepcopy(base)
            dsl["key"] = base["key"] + "_" + name
            dsl["entry"] = {"all": entry}
            dsl["exit"] = {"any": exit_any}
            dsl["max_hold_bars"] = hold
            dsl = f._ensure_dsl(dsl, "SOL-USDT-SWAP", "5m")
        book = {
            "symbol": "SOL-USDT-SWAP",
            "timeframe": "5m",
            "direction": "long",
            "logic_class": "impulse_continuation",
            "dsl": dsl,
        }
        packs = f.run_internal_suite(book)
        if not packs.get("ok"):
            print(name, "FAIL_OK", packs.get("stage"), packs.get("error"))
            continue
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
            round(float(bm.get("win_rate_pct") or 0), 2),
            "sharpe",
            bm.get("sharpe"),
            "oos",
            round(float(bm.get("oos_profit") or 0), 4),
            "fp",
            bm.get("fold_positive"),
            "mc",
            (packs.get("monte_carlo") or {}).get("pass"),
        )


if __name__ == "__main__":
    main()
