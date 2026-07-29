#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import print_function
import json
import sys
from pathlib import Path

sys.path.insert(0, "/root")
import auto_trade_strategy_dsl as d
import auto_trade_dual_engine_factory as dual
import auto_trade_human_confirm_pipeline as pipe
from dual_engine_workflow_v2.fitness_engine import evaluate_multi_objective
from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen

pipe._FRAME_CACHE.clear()
fr = dual._frame("ETH-USDT-SWAP", "15m")


def mk(volz, trail, swing, reclaim=1.0, use_pdc=False, hold=48):
    kids = [
        {"id": "a", "left": {"feature": "low"}, "op": "lt", "right": {"feature": "pdl"}},
        {"id": "b", "left": {"feature": "close"}, "op": "gt",
         "right": {"feature": "pdl", "scale": reclaim}},
        {"id": "c", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": volz}},
        {"id": "d", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}},
        {"id": "e", "left": {"feature": "low"}, "op": "gt",
         "right": {"feature": "pdl", "scale": 0.991}},
    ]
    if use_pdc:
        kids.append({"id": "f", "left": {"feature": "close"}, "op": "gt",
                     "right": {"feature": "pdc"}})
    dsl = {
        "schema": "qiyu_strategy_dsl_v1", "key": "t", "name": "t",
        "direction": "long", "timeframe": "15m",
        "supported_instruments": ["ETH-USDT-SWAP"],
        "entry": {"all": kids},
        "exit": {"any": [
            {"id": "x", "exit_op": "atr_trailing", "n_atr": float(trail),
             "atr_period": 14, "role": "take_profit"},
            {"id": "y", "exit_op": "swing_extreme", "lookback": int(swing),
             "role": "invalidation"},
        ]},
        "max_hold_bars": hold,
    }
    return d.validate_strategy(dsl)


def main():
    rows = []
    for volz in (1.8, 2.0, 2.2):
        for trail in (5.0, 4.0, 3.5):
            for swing in (8, 12, 5):
                for reclaim in (1.0, 1.001, 1.002):
                    for use_pdc in (False, True):
                        defn = mk(volz, trail, swing, reclaim, use_pdc)
                        bt = d.backtest_dsl(fr, defn, stop_loss_pct=0.009)
                        tr = bt.get("trades") or []
                        pnls = [float(t.get("pnl_ratio") or 0) for t in tr]
                        if len(pnls) < 8:
                            continue
                        fit = evaluate_multi_objective(tr, enforce=True)
                        m = fit["metrics"]
                        mean = sum(pnls) / len(pnls)
                        row = {
                            "volz": volz, "trail": trail, "swing": swing,
                            "reclaim": reclaim, "pdc": use_pdc,
                            "n": len(pnls), "mean": round(mean, 5),
                            "pay": round(m["payoff_ratio"], 3),
                            "calmar": round(m["calmar"], 3),
                            "w5": round(m["worst5_loss_share"], 3),
                            "lottery": bool(m["remove_max_win"]["pass"]),
                            "exp": round(m["expectancy_factor"], 3),
                            "pass": bool(fit["pass"]),
                            "failed": fit["failed_checks"],
                            "maxwin": round(max(pnls), 3),
                            "minpnl": round(min(pnls), 3),
                        }
                        rows.append(row)
                        if fit["pass"] or (
                            mean > 0 and m["payoff_ratio"] >= 2.5
                            and m["worst5_loss_share"] <= 0.45
                            and m["remove_max_win"]["pass"]
                        ):
                            print("HIT", json.dumps(row), flush=True)

    rows = sorted(
        rows,
        key=lambda r: (
            -int(r["pass"]),
            -int(r["mean"] > 0),
            -int(r["lottery"]),
            -int(r["w5"] <= 0.45),
            -r["pay"],
            -r["mean"],
        ),
    )
    print("TOP12", flush=True)
    for r in rows[:12]:
        print(json.dumps(r), flush=True)

    cands = [r for r in rows if r["mean"] > 0 and r["pay"] >= 2.5]
    cands = sorted(cands, key=lambda r: (r["w5"], -int(r["lottery"]), -r["pay"]))[:6]
    print("CANDS", json.dumps(cands), flush=True)
    top_l1 = []
    for c in cands[:4]:
        defn = mk(c["volz"], c["trail"], c["swing"], c["reclaim"], c["pdc"])
        ok = 0
        for i in range(12):
            r = run_micro_screen(
                definition=defn, frame=fr,
                backtest_fn=lambda frm, dd, _d=defn: d.backtest_dsl(frm, _d, stop_loss_pct=0.009),
                seed=9400 + i * 7919,
            )
            if r.get("pass"):
                ok += 1
        c2 = dict(c)
        c2["l1_12"] = ok
        top_l1.append(c2)
        print("L1", json.dumps(c2), flush=True)

    Path("/root/eth_samebar_surgery.json").write_text(json.dumps({
        "top": rows[:40], "cands": cands, "top_l1": top_l1,
        "n_pass": sum(1 for r in rows if r["pass"]),
    }, indent=2))
    print("OUT /root/eth_samebar_surgery.json n_pass",
          sum(1 for r in rows if r["pass"]), flush=True)


if __name__ == "__main__":
    main()
