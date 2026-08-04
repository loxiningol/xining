#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import print_function
import copy
import json
import sys

sys.path.insert(0, "/root")
import auto_trade_strategy_dsl as dsl_mod
from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen
from dual_engine_workflow_v2.fitness_engine import payoff_stats, _pnls
import auto_trade_dual_engine_factory as dual

dual._load_env()
frame = dual._frame("BTC-USDT-SWAP", "15m")
print("frame", len(frame), "volz", "vol_z20" in frame.columns, flush=True)
base = json.load(open("/root/strategy_htf_ltf_pullback_v1.json"))["dsl_long"]


def mk(cci=-100, volz=1.2, trail=4.0, hold=48, slope=0.001, swing=20, both=True, use_swing=False):
    d = copy.deepcopy(base)
    entry = [
        {"id": "e_htf_ema_align", "left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
        {"id": "e_ltf_ema53_gt_ema200", "left": {"feature": "ema53"}, "op": "gt", "right": {"feature": "ema200"}},
        {"id": "e_htf_slope_pos", "left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": float(slope)}},
        {"id": "e_cci_pullback", "left": {"feature": "cci"}, "op": "lt", "right": {"value": float(cci)}},
    ]
    if both:
        entry.append({"id": "e_ema21_touch", "left": {"feature": "low"}, "op": "lte", "right": {"feature": "ema21"}})
    entry += [
        {"id": "e_reclaim_ema21", "left": {"feature": "close"}, "op": "cross_above", "right": {"feature": "ema21"}},
        {"id": "e_vol_confirm", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": float(volz)}},
        {"id": "e_dir_candle", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}},
        {"id": "e_atr_room", "left": {"feature": "atr14"}, "op": "gt", "right": {"feature": "close", "scale": 0.0025}},
    ]
    d["entry"] = {"all": entry}
    exits = [{"id": "x_atr_trail", "exit_op": "atr_trailing", "n_atr": float(trail), "atr_period": 14, "role": "take_profit"}]
    if use_swing:
        exits.append({"id": "x_swing_inv", "exit_op": "swing_extreme", "lookback": int(swing), "role": "invalidation"})
    d["exit"] = {"any": exits}
    d["max_hold_bars"] = int(hold)
    d["key"] = "htf_pb_v2_probe"
    dsl_mod.validate_strategy(d)
    return d


def probe(d, seeds=(369614901, 42, 7, 99, 123)):
    def bt(frm, defn):
        return dsl_mod.backtest_dsl(frm, defn, stop_loss_pct=0.009)

    rows = []
    for seed in seeds:
        l1 = run_micro_screen(definition=d, frame=frame, backtest_fn=bt, seed=seed)
        m = l1.get("metrics") or {}
        rows.append({
            "pass": bool(l1.get("pass")),
            "reasons": l1.get("reject_reasons"),
            "n": m.get("filled_entries"),
            "pay": round(float(m.get("payoff_ratio") or 0), 3),
            "wr": round(float(m.get("win_rate") or 0), 3),
        })
    return rows


cfgs = [
    dict(cci=-100, volz=1.2, trail=4.0, hold=48, slope=0.001, both=True, use_swing=False),
    dict(cci=-120, volz=1.2, trail=4.0, hold=48, slope=0.001, both=True, use_swing=False),
    dict(cci=-100, volz=1.5, trail=4.0, hold=48, slope=0.001, both=True, use_swing=False),
    dict(cci=-100, volz=1.2, trail=4.0, hold=48, slope=0.0, both=True, use_swing=False),
    dict(cci=-100, volz=0.8, trail=4.0, hold=48, slope=0.001, both=True, use_swing=False),
    dict(cci=-150, volz=1.0, trail=4.0, hold=48, slope=0.001, both=True, use_swing=False),
    dict(cci=-100, volz=1.2, trail=4.0, hold=48, slope=0.001, both=False, use_swing=False),
    dict(cci=-120, volz=1.5, trail=4.0, hold=60, slope=0.001, both=True, use_swing=False),
    dict(cci=-100, volz=1.2, trail=4.0, hold=48, slope=0.001, both=True, use_swing=True),
    dict(cci=-80, volz=1.2, trail=4.0, hold=48, slope=0.001, both=True, use_swing=False),
]

best = None
for c in cfgs:
    d = mk(**c)
    rows = probe(d)
    passes = sum(1 for r in rows if r["pass"])
    print("CFG", c, "passes", passes, "rows", rows, flush=True)
    trades = dsl_mod.backtest_dsl(frame, d, stop_loss_pct=0.009)
    pn = _pnls(trades)
    st = payoff_stats(pn)
    print(
        "FULL n", len(pn),
        "pay", round(st["payoff_ratio"], 3),
        "wr", round(st["win_rate"], 3),
        "exp", round(st["classic_expectancy_mean_pnl"], 5),
        flush=True,
    )
    score = (passes, st["payoff_ratio"], len(pn))
    if best is None or score > best[0]:
        best = (score, c, rows, st, len(pn))

print("BEST", best[0], best[1], "l1", best[2], "full_pay", best[3]["payoff_ratio"], "n", best[4], flush=True)
print("DONE", flush=True)
