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
SYM = sys.argv[1] if len(sys.argv) > 1 else "BTC-USDT-SWAP"
TF = sys.argv[2] if len(sys.argv) > 2 else "15m"
frame = dual._frame(SYM, TF)
print("frame", SYM, TF, len(frame), flush=True)
base = json.load(open("/root/strategy_htf_ltf_pullback_v1.json"))["dsl_long"]


def mk(
    cci=-100,
    cci_offset=1,
    volz=0.8,
    trail=4.0,
    hold=48,
    slope=0.0,
    swing=16,
    use_swing=False,
    mode="cci_offset_or_touch",
    atr_scale=0.002,
):
    d = copy.deepcopy(base)
    d["supported_instruments"] = [SYM]
    entry = [
        {"id": "e_htf_ema_align", "left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
        {"id": "e_ltf_ema53_gt_ema200", "left": {"feature": "ema53"}, "op": "gt", "right": {"feature": "ema200"}},
        {"id": "e_htf_slope_pos", "left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": float(slope)}},
    ]
    if mode == "cci_offset_only":
        entry.append({
            "id": "e_cci_pullback_recent",
            "left": {"feature": "cci", "offset": int(cci_offset)},
            "op": "lt",
            "right": {"value": float(cci)},
        })
    elif mode == "cci_offset_and_touch":
        entry += [
            {
                "id": "e_cci_pullback_recent",
                "left": {"feature": "cci", "offset": int(cci_offset)},
                "op": "lt",
                "right": {"value": float(cci)},
            },
            {
                "id": "e_ema21_touch_recent",
                "left": {"feature": "low", "offset": int(cci_offset)},
                "op": "lte",
                "right": {"feature": "ema21", "offset": int(cci_offset)},
            },
        ]
    else:  # cci_offset_or_touch
        entry.append({
            "any": [
                {
                    "id": "e_cci_pullback_recent",
                    "left": {"feature": "cci", "offset": int(cci_offset)},
                    "op": "lt",
                    "right": {"value": float(cci)},
                },
                {
                    "id": "e_ema21_touch_recent",
                    "left": {"feature": "low", "offset": int(cci_offset)},
                    "op": "lte",
                    "right": {"feature": "ema21", "offset": int(cci_offset)},
                },
            ]
        })
    entry += [
        {"id": "e_reclaim_ema21", "left": {"feature": "close"}, "op": "cross_above", "right": {"feature": "ema21"}},
        {"id": "e_vol_confirm", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": float(volz)}},
        {"id": "e_dir_candle", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}},
        {"id": "e_atr_room", "left": {"feature": "atr14"}, "op": "gt", "right": {"feature": "close", "scale": float(atr_scale)}},
    ]
    d["entry"] = {"all": entry}
    exits = [{"id": "x_atr_trail", "exit_op": "atr_trailing", "n_atr": float(trail), "atr_period": 14, "role": "take_profit"}]
    if use_swing:
        exits.append({"id": "x_swing_inv", "exit_op": "swing_extreme", "lookback": int(swing), "role": "invalidation"})
    d["exit"] = {"any": exits}
    d["max_hold_bars"] = int(hold)
    d["key"] = "htf_pb_v2_%s" % SYM.split("-")[0].lower()
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


cfgs = []
for mode in ("cci_offset_only", "cci_offset_and_touch", "cci_offset_or_touch"):
    for cci in (-100, -120):
        for off in (1, 2):
            for volz in (0.5, 0.8, 1.2):
                for slope in (0.0, 0.001):
                    for use_swing in (False,):
                        cfgs.append(dict(
                            mode=mode, cci=cci, cci_offset=off, volz=volz,
                            trail=4.0, hold=48, slope=slope, use_swing=use_swing, atr_scale=0.002,
                        ))

print("n_cfgs", len(cfgs), flush=True)
ranked = []
for c in cfgs:
    d = mk(**c)
    rows = probe(d)
    passes = sum(1 for r in rows if r["pass"])
    trades = dsl_mod.backtest_dsl(frame, d, stop_loss_pct=0.009)
    pn = _pnls(trades)
    st = payoff_stats(pn)
    row = {
        "cfg": c,
        "passes": passes,
        "l1": rows,
        "full_n": len(pn),
        "full_pay": round(st["payoff_ratio"], 3),
        "full_wr": round(st["win_rate"], 3),
        "full_exp": round(st["classic_expectancy_mean_pnl"], 5),
    }
    ranked.append(row)
    if passes or st["payoff_ratio"] >= 1.2:
        print("HIT", passes, "full_pay", row["full_pay"], "n", row["full_n"], c, rows, flush=True)

ranked.sort(key=lambda x: (-x["passes"], -x["full_pay"], -x["full_n"]))
print("TOP15", flush=True)
for r in ranked[:15]:
    print(r["passes"], r["full_pay"], r["full_n"], r["full_wr"], r["cfg"], r["l1"], flush=True)
out = "/tmp/htf_pb_l1_probe_rank.json"
json.dump(ranked[:30], open(out, "w"), ensure_ascii=False, indent=2)
print("WROTE", out, flush=True)
print("DONE", flush=True)
