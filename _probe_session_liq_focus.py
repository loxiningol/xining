#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused density + Gate2 distribution probe for session_liq_engulf_v3."""
from __future__ import print_function
import gc, json, sys
from pathlib import Path

ROOT = Path("/root")
sys.path.insert(0, str(ROOT))
import auto_trade_strategy_dsl as d
import auto_trade_dual_engine_factory as dual
import auto_trade_human_confirm_pipeline as pipe
from dual_engine_workflow_v2.fitness_engine import evaluate_multi_objective
from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen


def _branch(direction, anchor, style, volz, depth_scale=None):
    is_long = direction == "long"
    sweep_feat = "low" if is_long else "high"
    sweep_op = "lt" if is_long else "gt"
    reclaim_op = "gt" if is_long else "lt"
    body_op = "gt" if is_long else "lt"
    pfx = "%s_%s" % (anchor, style)
    if style == "samebar":
        kids = [
            {"id": "e_sweep_%s" % pfx, "left": {"feature": sweep_feat},
             "op": sweep_op, "right": {"feature": anchor}},
            {"id": "e_reclaim_%s" % pfx, "left": {"feature": "close"},
             "op": reclaim_op, "right": {"feature": anchor}},
        ]
    else:
        kids = [
            {"id": "e_sweep1_%s" % pfx,
             "left": {"feature": sweep_feat, "offset": 1}, "op": sweep_op,
             "right": {"feature": anchor, "offset": 1}},
            {"id": "e_nosettle1_%s" % pfx,
             "left": {"feature": "close", "offset": 1},
             "op": "lte" if is_long else "gte",
             "right": {"feature": anchor, "offset": 1}},
            {"id": "e_displace_%s" % pfx, "left": {"feature": "close"},
             "op": reclaim_op, "right": {"feature": anchor}},
        ]
    kids += [
        {"id": "e_vol_%s" % pfx, "left": {"feature": "vol_z20"},
         "op": "gt", "right": {"value": float(volz)}},
        {"id": "e_body_%s" % pfx, "left": {"feature": "close"},
         "op": body_op, "right": {"feature": "open"}},
    ]
    if depth_scale is not None:
        kids.append({
            "id": "e_depth_%s" % pfx, "left": {"feature": sweep_feat},
            "op": "gt" if is_long else "lt",
            "right": {"feature": anchor, "scale": float(depth_scale)},
        })
    return {"all": kids}


def build(direction, symbol, tf, volz, trail, swing, anchors, styles, depth=None, hold=48):
    branches = [_branch(direction, a, s, volz, depth) for a in anchors for s in styles]
    entry = {"any": branches} if len(branches) > 1 else branches[0]
    dsl = {
        "schema": "qiyu_strategy_dsl_v1",
        "key": "sess_liq_focus_%s" % direction,
        "name": "session_liq_engulf_displace_%s" % direction,
        "direction": direction, "timeframe": tf,
        "supported_instruments": [symbol],
        "entry": entry,
        "exit": {"any": [
            {"id": "x_atr_trail", "exit_op": "atr_trailing", "n_atr": float(trail),
             "atr_period": 14, "role": "take_profit"},
            {"id": "x_sw_inv", "exit_op": "swing_extreme", "lookback": int(swing),
             "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
        "description": "focus_v3", "origin": "step_a_prebuilt_dsl", "version": 3,
        "live_enabled": False, "auto_trade_eligible": False,
    }
    return d.validate_strategy(dsl)


def eval_one(defn, frame, do_l1=False):
    bt = d.backtest_dsl(frame, defn, stop_loss_pct=0.009)
    trades = list(bt.get("trades") or [])
    pnls = [round(float(t.get("pnl_ratio") or 0), 5) for t in trades]
    fit = evaluate_multi_objective(trades, enforce=True, min_trades=8)
    m = fit.get("metrics") or {}
    out = {
        "n": len(pnls),
        "mean": round(sum(pnls) / len(pnls), 5) if pnls else 0.0,
        "pnls": pnls,
        "gate2_pass": bool(fit.get("pass")),
        "failed": fit.get("failed_checks"),
        "pay": m.get("payoff_ratio"),
        "calmar": m.get("calmar"),
        "exp_f": m.get("expectancy_factor"),
        "w5": m.get("worst5_loss_share"),
        "lottery": (m.get("remove_max_win") or {}).get("pass"),
        "wr": m.get("win_rate"),
    }
    if do_l1:
        ok = 0
        for i in range(12):
            r = run_micro_screen(
                definition=defn, frame=frame,
                backtest_fn=lambda frm, dd, _d=defn: d.backtest_dsl(frm, _d, stop_loss_pct=0.009),
                seed=9300 + i * 7919,
            )
            if r.get("pass"):
                ok += 1
        out["l1_12"] = ok
    return out


def main():
    pipe._FRAME_CACHE.clear()
    rows = []
    jobs = []
    for sym, tf in (
        ("ETH-USDT-SWAP", "15m"),
        ("XAU-USDT-SWAP", "15m"),
        ("SOL-USDT-SWAP", "15m"),
        ("DOGE-USDT-SWAP", "15m"),
        ("ETH-USDT-SWAP", "5m"),
    ):
        for direction in ("long", "short"):
            pd = "pdl" if direction == "long" else "pdh"
            h4 = "h4_low24" if direction == "long" else "h4_high24"
            depth = 0.991 if direction == "long" else 1.009
            for anchors, styles, tag in (
                ((pd,), ("samebar", "next1"), "pd_12"),
                ((pd, h4), ("samebar", "next1"), "dual_12"),
            ):
                for volz in (1.8,):
                    for trail in (5.0, 4.0):
                        for swing in (5,):
                            for use_depth in (False, True):
                                jobs.append({
                                    "sym": sym, "tf": tf, "dir": direction,
                                    "tag": tag, "anchors": anchors, "styles": styles,
                                    "volz": volz, "trail": trail, "swing": swing,
                                    "depth": (depth if use_depth else None),
                                })

    # Deduplicate by loading frame once per sym/tf
    frames = {}
    for job in jobs:
        key = (job["sym"], job["tf"])
        if key not in frames:
            try:
                fr = dual._frame(job["sym"], job["tf"])
            except Exception as exc:
                print("SKIP", key, str(exc)[:100], flush=True)
                frames[key] = None
                continue
            miss = [c for c in ("pdh", "pdl", "h4_high24", "h4_low24", "vol_z20") if c not in fr.columns]
            print("FRAME", key, "n", len(fr), "miss", miss, flush=True)
            frames[key] = None if miss else fr

        fr = frames.get(key)
        if fr is None:
            continue
        try:
            defn = build(
                job["dir"], job["sym"], job["tf"], job["volz"], job["trail"],
                job["swing"], job["anchors"], job["styles"], job["depth"],
                hold=64 if job["tf"] == "5m" else 48,
            )
            m = eval_one(defn, fr, do_l1=False)
        except Exception as exc:
            print("ERR", job, str(exc)[:120], flush=True)
            continue
        row = dict(job)
        row.update({k: m[k] for k in m if k != "pnls"})
        row["pnls_head"] = m["pnls"][:8]
        row["pnls_tail"] = m["pnls"][-3:]
        rows.append(row)
        if m["gate2_pass"] or (m["n"] >= 18 and m["mean"] > 0 and (m["pay"] or 0) >= 2.5):
            print("HIT", json.dumps({k: row[k] for k in row if k not in ("anchors", "styles", "pnls_head", "pnls_tail")},
                                    ensure_ascii=False, default=str), flush=True)

    ranked = sorted(rows, key=lambda r: (
        -int(bool(r.get("gate2_pass"))),
        -(1 if (r.get("mean") or 0) > 0 else 0),
        -(r.get("n") or 0) if (r.get("pay") or 0) >= 2.3 else 0,
        -(r.get("pay") or 0),
        -(r.get("mean") or -9),
    ))
    # L1 on best 8 positive dense candidates
    top = []
    for r in ranked:
        if len(top) >= 8:
            break
        if (r.get("n") or 0) < 12 or (r.get("mean") or 0) <= 0 or (r.get("pay") or 0) < 2.3:
            continue
        fr = frames.get((r["sym"], r["tf"]))
        if fr is None:
            continue
        defn = build(
            r["dir"], r["sym"], r["tf"], r["volz"], r["trail"], r["swing"],
            r["anchors"], r["styles"], r["depth"],
            hold=64 if r["tf"] == "5m" else 48,
        )
        m = eval_one(defn, fr, do_l1=True)
        slim = {k: r[k] for k in r if k not in ("pnls_head", "pnls_tail")}
        slim["l1_12"] = m.get("l1_12")
        slim["gate2_pass"] = m.get("gate2_pass")
        slim["failed"] = m.get("failed")
        slim["w5"] = m.get("w5")
        slim["lottery"] = m.get("lottery")
        top.append(slim)
        print("L1", json.dumps(slim, ensure_ascii=False, default=str)[:1000], flush=True)

    out = {
        "top_l1": top,
        "ranked": [{k: v for k, v in r.items() if k not in ("pnls_head", "pnls_tail")} for r in ranked[:60]],
        "n_total": len(rows),
        "n_gate2": sum(1 for r in rows if r.get("gate2_pass")),
    }
    Path("/root/session_liq_focus_probe.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str)
    )
    print("SUMMARY", "n", len(rows), "gate2", out["n_gate2"], flush=True)
    print("BEST", json.dumps(ranked[:5], ensure_ascii=False, default=str)[:2000], flush=True)


if __name__ == "__main__":
    main()
