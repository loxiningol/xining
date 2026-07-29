#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lean Gate2-aware probe v3b: fitness-first, then L1 on top candidates only."""
from __future__ import print_function
import copy
import gc
import json
import sys
from pathlib import Path

ROOT = Path("/root")
sys.path.insert(0, str(ROOT))

import auto_trade_strategy_dsl as d
import auto_trade_dual_engine_factory as dual
import auto_trade_human_confirm_pipeline as pipe
from dual_engine_workflow_v2.fitness_engine import evaluate_multi_objective
from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen

OUT = Path("/root/session_liq_engulf_v3_probe.json")
# Prefer symbols with native 15m; factory may resample others from 5m.
SYMS = [
    "ETH-USDT-SWAP", "XAU-USDT-SWAP", "BTC-USDT-SWAP",
    "SOL-USDT-SWAP", "DOGE-USDT-SWAP", "XRP-USDT-SWAP",
]


def _pay(pnls):
    if not pnls:
        return 0.0, 0.0, 0.0
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p <= 0]
    mean = sum(pnls) / float(len(pnls))
    pay = (sum(wins) / len(wins)) / (sum(losses) / len(losses)) if wins and losses else 0.0
    wr = len(wins) / float(len(pnls))
    return mean, pay, wr


def _branch(direction, anchor, style, volz, depth_scale, reclaim_scale):
    is_long = direction == "long"
    sweep_feat = "low" if is_long else "high"
    sweep_op = "lt" if is_long else "gt"
    reclaim_op = "gt" if is_long else "lt"
    body_op = "gt" if is_long else "lt"
    depth_op = "gt" if is_long else "lt"
    # unique ids across dual-anchor × same/next branches
    pfx = "%s_%s" % (anchor, style)

    if style == "samebar":
        kids = [
            {"id": "e_sweep_%s" % pfx, "left": {"feature": sweep_feat},
             "op": sweep_op, "right": {"feature": anchor}},
            {"id": "e_reclaim_%s" % pfx, "left": {"feature": "close"},
             "op": reclaim_op,
             "right": {"feature": anchor, "scale": float(reclaim_scale)}},
        ]
    else:
        kids = [
            {"id": "e_sweep1_%s" % pfx,
             "left": {"feature": sweep_feat, "offset": 1},
             "op": sweep_op,
             "right": {"feature": anchor, "offset": 1}},
            {"id": "e_nosettle1_%s" % pfx,
             "left": {"feature": "close", "offset": 1},
             "op": "lte" if is_long else "gte",
             "right": {"feature": anchor, "offset": 1}},
            {"id": "e_displace_%s" % pfx, "left": {"feature": "close"},
             "op": reclaim_op,
             "right": {"feature": anchor, "scale": float(reclaim_scale)}},
        ]
    kids.extend([
        {"id": "e_vol_%s" % pfx, "left": {"feature": "vol_z20"},
         "op": "gt", "right": {"value": float(volz)}},
        {"id": "e_body_%s" % pfx, "left": {"feature": "close"},
         "op": body_op, "right": {"feature": "open"}},
        {"id": "e_depth_%s" % pfx, "left": {"feature": sweep_feat},
         "op": depth_op,
         "right": {"feature": anchor, "scale": float(depth_scale)}},
    ])
    return {"all": kids}


def build_dsl(direction, symbol, volz, trail, hold, swing_lb,
              anchors, styles, depth_scale, reclaim_scale):
    branches = []
    for anchor in anchors:
        for style in styles:
            branches.append(_branch(
                direction, anchor, style, volz, depth_scale, reclaim_scale
            ))
    entry = {"any": branches} if len(branches) > 1 else branches[0]
    dsl = {
        "schema": "qiyu_strategy_dsl_v1",
        "key": "sess_liq_%s_%s_v3" % (direction, symbol.split("-")[0].lower()),
        "name": "session_liq_engulf_displace_%s" % direction,
        "direction": direction,
        "timeframe": "15m",
        "supported_instruments": [symbol],
        "entry": entry,
        "exit": {
            "any": [
                {"id": "x_atr_trail", "exit_op": "atr_trailing",
                 "n_atr": float(trail), "atr_period": 14, "role": "take_profit"},
                {"id": "x_sw_inv", "exit_op": "swing_extreme",
                 "lookback": int(swing_lb), "role": "invalidation"},
            ]
        },
        "max_hold_bars": int(hold),
        "description": "session_liq_engulf_v3|dual_anchor|1_2bar|volz|atr_trail|no_fixed_tp",
        "origin": "step_a_prebuilt_dsl",
        "version": 3,
        "live_enabled": False,
        "auto_trade_eligible": False,
    }
    return d.validate_strategy(dsl)


def fitness_only(defn, frame):
    bt = d.backtest_dsl(frame, defn, stop_loss_pct=0.009)
    trades = list(bt.get("trades") or [])
    pnls = [float(t.get("pnl_ratio") or 0) for t in trades]
    mean, pay, wr = _pay(pnls)
    fit = evaluate_multi_objective(trades, enforce=True, min_trades=8)
    return {
        "n": len(pnls),
        "mean": round(mean, 5),
        "pay": round(pay, 3),
        "wr": round(wr, 3),
        "gate2_pass": bool(fit.get("pass")),
        "failed_checks": fit.get("failed_checks"),
        "fitness": {
            "payoff": (fit.get("metrics") or {}).get("payoff_ratio"),
            "calmar": (fit.get("metrics") or {}).get("calmar"),
            "exp_f": (fit.get("metrics") or {}).get("expectancy_factor"),
            "w5": (fit.get("metrics") or {}).get("worst5_loss_share"),
            "lottery": ((fit.get("metrics") or {}).get("remove_max_win") or {}).get("pass"),
        },
        "_defn": defn,
    }


def l1_score(defn, frame, l1_n=10):
    ok = 0
    fail_top = {}
    for i in range(l1_n):
        r = run_micro_screen(
            definition=defn, frame=frame,
            backtest_fn=lambda frm, dd, _d=defn: d.backtest_dsl(frm, _d, stop_loss_pct=0.009),
            seed=9200 + i * 7919,
        )
        if r.get("pass"):
            ok += 1
        else:
            reasons = r.get("reasons") or r.get("fail_reasons") or []
            if not reasons and r.get("reason"):
                reasons = [r.get("reason")]
            for reason in reasons:
                fail_top[str(reason)] = fail_top.get(str(reason), 0) + 1
    return ok, sorted(fail_top.items(), key=lambda x: -x[1])[:5]


def configs_for(direction):
    # depth caps sweep distance (~0.5% / ~0.9%) so protective SL can actually bind
    depths = (0.995, 0.991) if direction == "long" else (1.005, 1.009)
    # reclaim margin: close must clear anchor by ~0 / 0.1%
    reclaims = (1.0, 1.001) if direction == "long" else (1.0, 0.999)
    pd = "pdl" if direction == "long" else "pdh"
    h4 = "h4_low24" if direction == "long" else "h4_high24"
    out = []
    # Keep grid lean on 764MB host: prioritize density (dual/12) + trail 5.0.
    for anchors, styles, tag in (
        ((pd,), ("samebar", "next1"), "pd_12"),
        ((pd, h4), ("samebar", "next1"), "dual_12"),
    ):
        for volz in (1.8, 2.0):
            for trail in (5.0,):
                for swing_lb in (5, 8):
                    for depth in depths:
                        for reclaim in reclaims:
                            out.append({
                                "tag": tag, "anchors": anchors, "styles": styles,
                                "volz": volz, "trail": trail, "swing": swing_lb,
                                "hold": 48, "depth": depth, "reclaim": reclaim,
                            })
    return out


def main():
    pipe._FRAME_CACHE.clear()
    ranked = []
    frames = {}
    for sym in SYMS:
        try:
            fr = dual._frame(sym, "15m")
        except Exception as exc:
            print("SKIP_FRAME", sym, str(exc)[:120], flush=True)
            continue
        need = ("pdh", "pdl", "h4_high24", "h4_low24", "vol_z20")
        missing = [c for c in need if c not in fr.columns]
        print("FRAME", sym, "n", len(fr), "missing", missing, flush=True)
        if missing:
            continue
        frames[sym] = fr
        for direction in ("long", "short"):
            for cfg in configs_for(direction):
                try:
                    defn = build_dsl(
                        direction, sym, cfg["volz"], cfg["trail"], cfg["hold"],
                        cfg["swing"], cfg["anchors"], cfg["styles"], cfg["depth"],
                        cfg["reclaim"],
                    )
                    m = fitness_only(defn, fr)
                except Exception as exc:
                    print("ERR", sym, direction, cfg["tag"], str(exc)[:140], flush=True)
                    continue
                row = {
                    "sym": sym, "dir": direction,
                    "tag": cfg["tag"], "volz": cfg["volz"], "trail": cfg["trail"],
                    "swing": cfg["swing"], "hold": cfg["hold"], "depth": cfg["depth"],
                    "reclaim": cfg["reclaim"],
                    "anchors": list(cfg["anchors"]), "styles": list(cfg["styles"]),
                    "n": m["n"], "mean": m["mean"], "pay": m["pay"], "wr": m["wr"],
                    "gate2_pass": m["gate2_pass"], "failed_checks": m["failed_checks"],
                    "fitness": m["fitness"],
                }
                # keep defn only for promising rows to limit memory
                if (m["n"] >= 8 and m["mean"] > 0 and m["pay"] >= 2.2) or m["gate2_pass"]:
                    row["_defn"] = m["_defn"]
                ranked.append(row)
                if m["gate2_pass"] or (m["n"] >= 10 and m["mean"] > 0 and m["pay"] >= 2.5):
                    print("HIT", json.dumps({
                        k: row[k] for k in (
                            "sym", "dir", "tag", "volz", "trail", "swing", "n",
                            "mean", "pay", "wr", "gate2_pass", "failed_checks", "fitness"
                        )
                    }, ensure_ascii=False, default=str), flush=True)
        gc.collect()

    ranked_s = sorted(
        ranked,
        key=lambda r: (
            -int(bool(r.get("gate2_pass"))),
            -(1 if (r.get("mean") or 0) > 0 else 0),
            -(r.get("pay") or 0),
            -(r.get("mean") or -99),
            -(r.get("n") or 0),
        ),
    )
    # L1 only on top candidates that look Gate2-viable
    top = []
    for row in ranked_s:
        if len(top) >= 12:
            break
        if (row.get("n") or 0) < 8:
            continue
        if (row.get("mean") or 0) <= 0:
            continue
        if (row.get("pay") or 0) < 2.3:
            continue
        defn = row.get("_defn")
        fr = frames.get(row["sym"])
        if defn is None or fr is None:
            continue
        l1_ok, fail_top = l1_score(defn, fr, l1_n=12)
        slim = {k: v for k, v in row.items() if k != "_defn"}
        slim["l1_12"] = l1_ok
        slim["fail_top"] = fail_top
        top.append(slim)
        print("L1", json.dumps(slim, ensure_ascii=False, default=str)[:900], flush=True)

    # strip defns before dump
    dump_ranked = []
    for r in ranked_s:
        dump_ranked.append({k: v for k, v in r.items() if k != "_defn"})

    OUT.write_text(json.dumps({
        "top_l1": top,
        "ranked": dump_ranked[:80],
        "n_total": len(dump_ranked),
        "n_gate2": sum(1 for r in dump_ranked if r.get("gate2_pass")),
        "n_pos_pay25": sum(1 for r in dump_ranked if (r.get("mean") or 0) > 0 and (r.get("pay") or 0) >= 2.5 and (r.get("n") or 0) >= 8),
    }, ensure_ascii=False, indent=2, default=str))
    print("SUMMARY n_total", len(dump_ranked),
          "gate2", sum(1 for r in dump_ranked if r.get("gate2_pass")),
          "pos_pay25", sum(1 for r in dump_ranked if (r.get("mean") or 0) > 0 and (r.get("pay") or 0) >= 2.5 and (r.get("n") or 0) >= 8),
          flush=True)
    print("OUT", OUT, flush=True)


if __name__ == "__main__":
    main()
