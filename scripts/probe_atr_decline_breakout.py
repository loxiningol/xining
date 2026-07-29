#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import print_function
import json
import sys

sys.path.insert(0, "/root")
import auto_trade_strategy_dsl as dsl_mod
import auto_trade_dual_engine_factory as dual
from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen

fr = dual._frame("BTC-USDT-SWAP", "15m")
print("bars", len(fr), flush=True)


def eval_dsl(d, tag):
    defn = dsl_mod.validate_strategy(d)
    bt = dsl_mod.backtest_dsl(fr, defn, stop_loss_pct=0.009)
    trades = bt.get("trades") or []
    pnls = [float(t.get("pnl_ratio") or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p <= 0]
    pay = (sum(wins) / len(wins)) / (sum(losses) / len(losses)) if wins and losses else 0
    wr = len(wins) / float(len(pnls)) if pnls else 0
    l1 = run_micro_screen(
        definition=defn,
        frame=fr,
        backtest_fn=lambda frm, dd, defn=defn: dsl_mod.backtest_dsl(frm, defn, stop_loss_pct=0.009),
        seed=42,
    )
    row = {
        "tag": tag,
        "n": len(pnls),
        "pay": round(pay, 3),
        "wr": round(wr, 3),
        "l1": bool(l1.get("pass")),
        "l1_pay": round(float((l1.get("metrics") or {}).get("payoff_ratio") or 0), 3),
        "l1_why": (l1.get("reject_reasons") or [None])[0],
    }
    print(row, flush=True)
    return row


rows = []
for direction, break_id, break_feat, break_op, mom_op in [
    ("long", "e_break_high", "prev_high20", "cross_above", "gt"),
    ("short", "e_break_low", "prev_low20", "cross_below", "lt"),
]:
    for lag in (3, 5, 8):
        for volz in (0.5, 0.8, 1.0):
            for trail in (2.5, 3.5, 4.0):
                for ath in (None, 0.0020, 0.0015):
                    leaves = [
                        {
                            "id": "e_atr_decl",
                            "left": {"feature": "atr14"},
                            "op": "lt",
                            "right": {"feature": "atr14", "offset": lag},
                        },
                        {
                            "id": break_id,
                            "left": {"feature": "close"},
                            "op": break_op,
                            "right": {"feature": break_feat},
                        },
                        {
                            "id": "e_vol",
                            "left": {"feature": "vol_z20"},
                            "op": "gt",
                            "right": {"value": volz},
                        },
                        {
                            "id": "e_mom",
                            "left": {"feature": "close"},
                            "op": mom_op,
                            "right": {"feature": "open"},
                        },
                    ]
                    if ath is not None:
                        leaves.insert(0, {
                            "id": "e_atr_level",
                            "left": {"feature": "atr14"},
                            "op": "lt",
                            "right": {"feature": "close", "scale": ath},
                        })
                    key = "atr_decl_%s_l%d_v%s_t%s_a%s" % (
                        direction, lag, str(volz).replace(".", ""),
                        str(trail).replace(".", ""),
                        "x" if ath is None else str(ath).replace(".", ""),
                    )
                    d = {
                        "schema": "qiyu_strategy_dsl_v1",
                        "key": key[:100],
                        "name": "atr decline breakout",
                        "direction": direction,
                        "timeframe": "15m",
                        "supported_instruments": ["BTC-USDT-SWAP"],
                        "max_hold_bars": 24,
                        "entry": {"all": leaves},
                        "exit": {"any": [
                            {
                                "id": "x_atr",
                                "exit_op": "atr_trailing",
                                "n_atr": trail,
                                "atr_period": 14,
                                "role": "take_profit",
                            },
                            {
                                "id": "x_sw",
                                "exit_op": "swing_extreme",
                                "lookback": 20,
                                "role": "invalidation",
                            },
                        ]},
                        "description": "probe atr decline structural breakout",
                    }
                    try:
                        rows.append(eval_dsl(d, key))
                    except Exception as exc:
                        print({"tag": key, "error": str(exc)}, flush=True)

rows.sort(key=lambda r: (r.get("l1"), r.get("pay", 0), r.get("n", 0)), reverse=True)
out = {
    "any_l1": sum(1 for r in rows if r.get("l1")),
    "pay_ge_1_2": sum(1 for r in rows if r.get("pay", 0) >= 1.2),
    "top": rows[:25],
    "l1_passers": [r for r in rows if r.get("l1")][:20],
}
Path = __import__("pathlib").Path
Path("/root/auto_trade/dual_engine/workflow_v2/atr_squeeze_decl.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2)
)
print("DECL_DONE", out["any_l1"], out["pay_ge_1_2"], flush=True)
for r in out["top"][:10]:
    print("TOP", r, flush=True)
