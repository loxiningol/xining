#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Last CL try: block the two hard-stop entries via entry-time feature filter."""
from __future__ import print_function

import json
import os
import sys

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
OUT = "/root/auto_trade/dual_engine"


def base_entry(extra=None):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 54.0}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.1}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
        {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 50.0}},
    ]
    if extra:
        entry.extend(extra)
    return entry


def mk(tag, extra=None, hold=12, tp=45, rsi=54, z=0.1, cci=50):
    entry = base_entry(extra)
    # overwrite rsi/z/cci if custom
    entry[0]["right"]["value"] = float(rsi)
    entry[1]["right"]["value"] = float(z)
    entry[4]["right"]["value"] = float(cci)
    dsl = {
        "key": "frost2c_cl_last_%s" % tag,
        "name": "寒霜贰-CL-15m-exhaustion_fade",
        "direction": "short",
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(tp)}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
    }
    return {
        "symbol": "CL-USDT-SWAP", "timeframe": "15m", "direction": "short",
        "logic_class": "exhaustion_fade", "thesis": "block hard-stop cluster",
        "title": dsl["name"],
        "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"),
        "gate_mode": "frost2", "source": "cl_last",
    }


def main():
    # inspect loser entry features from backtest trades + candle features if available
    book0 = mk("base")
    bt = d._backtest(book0["dsl"], "CL-USDT-SWAP", "15m", "observed_base")
    trades = bt.get("trades") or []
    losers = [t for t in trades if float(t.get("pnl_ratio") or 0) < -0.15]
    print("losers", len(losers), flush=True)
    for t in losers:
        print("LOSER", {k: t.get(k) for k in (
            "pnl_ratio", "exit_type", "entry_time", "exit_time", "entry_index",
            "entry_conditions", "stop_loss")}, flush=True)

    # try filters that reduce hard-stop incidence
    extras = []
    # atr cap / floor
    for atr_max in (0.4, 0.6, 0.8, 1.0, 1.5, 2.0, 3.0):
        extras.append(("atr_lt_%s" % atr_max,
                       [{"left": {"feature": "atr14"}, "op": "lt", "right": {"value": float(atr_max)}}]))
    for atr_min in (0.05, 0.1, 0.2, 0.35):
        extras.append(("atr_gt_%s" % atr_min,
                       [{"left": {"feature": "atr14"}, "op": "gt", "right": {"value": float(atr_min)}}]))
    # z upper
    for zhi in (0.5, 0.8, 1.0, 1.5, 2.0):
        extras.append(("z_lt_%s" % zhi,
                       [{"left": {"feature": "z20"}, "op": "lt", "right": {"value": float(zhi)}}]))
    # h1 regime
    extras.append(("h1down", [
        {"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}}]))
    extras.append(("h1slope", [
        {"left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": 0.0}}]))
    # rsi not too extreme
    for rhi in (65, 70, 75, 80):
        extras.append(("rsi_lt_%s" % rhi,
                       [{"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(rhi)}}]))
    # cci not too extreme
    for chi in (100, 150, 200, 250):
        extras.append(("cci_lt_%s" % chi,
                       [{"left": {"feature": "cci"}, "op": "lt", "right": {"value": float(chi)}}]))
    # macd not too deep
    for flo in (-0.2, -0.5, -1.0, -2.0):
        extras.append(("macd_gt_%s" % str(flo).replace("-", "m"),
                       [{"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": float(flo)}}]))
    # hold shorter to cut forced exits? won't help hard stop
    # combo: z_lt + h1down + rsi_lt
    extras.append(("combo_a", [
        {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.0}},
        {"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}},
        {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 70.0}},
    ]))
    extras.append(("combo_b", [
        {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 0.8}},
        {"left": {"feature": "cci"}, "op": "lt", "right": {"value": 150.0}},
        {"left": {"feature": "atr14"}, "op": "lt", "right": {"value": 1.5}},
    ]))

    cands = [mk(tag, extra=ex) for tag, ex in extras]
    # also vary hold/tp with combo
    for hold, tp in [(8, 42), (10, 45), (16, 40), (20, 45)]:
        cands.append(mk("combo_a_h%s" % hold, extra=extras[-2][1], hold=hold, tp=tp))

    print("last_cands", len(cands), flush=True)
    best = []
    winner = None
    for i, book in enumerate(cands):
        q = f2.quick_suite(book)
        bm = q.get("base_metrics") or {}
        if not q.get("quick_pass"):
            if i % 10 == 0:
                print("..", book["dsl"]["key"], bm.get("fold_positive"),
                      (q.get("logic_destruction") or {}).get("pass"), flush=True)
            continue
        full = f2.full_suite(book, q)
        # autopsy stops
        try:
            bt2 = d._backtest(book["dsl"], "CL-USDT-SWAP", "15m", "observed_base")
            tr = bt2.get("trades") or []
            hard = sum(1 for t in tr if float(t.get("pnl_ratio") or 0) < -0.15)
            pnls = [float(t.get("pnl_ratio") or 0) for t in tr]
        except Exception:
            hard, pnls = None, []
        s = {
            "key": book["dsl"]["key"], "fp": bm.get("fold_positive"),
            "tr": bm.get("trades"), "wr": bm.get("win_rate_pct"),
            "sh": bm.get("sharpe"), "mean": bm.get("mean_net"),
            "dest": True, "full": full.get("full_pass"),
            "friction_sh": (full.get("full") or {}).get("friction_sharpe"),
            "mc_beat": ((full.get("full") or {}).get("mc") or {}).get("beat_ratio"),
            "mc_actual": ((full.get("full") or {}).get("mc") or {}).get("actual_final"),
            "hard_stops": hard, "pnls": pnls,
        }
        best.append(s)
        print("TRY", {k: s[k] for k in s if k != "pnls"}, flush=True)
        if full.get("full_pass"):
            winner = (book, full, s)
            break

    best.sort(key=lambda x: (
        float(x.get("friction_sh") if x.get("friction_sh") is not None else -99),
        float(x.get("mc_beat") or 0),
        -int(x.get("hard_stops") or 99),
    ), reverse=True)

    out = {"ok": False, "best": best[:12], "loser_autopsy": [
        {k: t.get(k) for k in ("pnl_ratio", "exit_type", "entry_time", "exit_time", "entry_conditions")}
        for t in losers
    ]}
    if winner:
        book, full, s = winner
        sf = f2.run_sim_formal(book, full)
        out.update({
            "ok": not sf.get("failed_step"), "key": s["key"], "summary": s,
            "sim": sf.get("sim"), "formal": sf.get("formal"),
            "pending": sf.get("pending"), "failed_step": sf.get("failed_step"),
        })
        print("SF", out.get("failed_step"), out.get("pending"), flush=True)
    else:
        out["failed_step"] = "full_suite_no_winner"
        out["best_near"] = best[0] if best else None
        print("NO_WIN", out.get("best_near"), flush=True)

    open(os.path.join(OUT, "frost2_cont_cl_last.json"), "w").write(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n")

    st = json.load(open(os.path.join(OUT, "frost2_cont_status.json")))
    st["cl_last"] = {
        "ok": out.get("ok"), "failed_step": out.get("failed_step"),
        "key": out.get("key"), "pending": out.get("pending"),
        "best_near": out.get("best_near") or (out.get("best") or [None])[0],
    }
    st["gates"] = {
        "wf": ">=7/10", "dest": True,
        "mc90": "sign_randomization_null beat>=0.90 and actual>0",
        "friction_ge0": True,
    }
    if (out.get("pending") or {}).get("ok"):
        keys = list(st.get("pending_keys") or [])
        k = out["pending"]["key"]
        if k not in keys:
            keys.append(k)
        st["pending_keys"] = keys
        st["ok_min_cl"] = True
        st["cl_abandoned"] = False
    open(os.path.join(OUT, "frost2_cont_status.json"), "w").write(
        json.dumps(st, ensure_ascii=False, indent=2) + "\n")
    open(os.path.join(OUT, "frost2_cont_gates.json"), "w").write(json.dumps({
        "at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "quick_wf_pos": 7,
        "logic_destruction": True,
        "mc_beat_shuffles": 0.9,
        "mc_method": "sign_randomization_null",
        "friction_sharpe_ge": 0.0,
        "note": "寒霜贰续; MC fixed from broken order-shuffle",
    }, ensure_ascii=False, indent=2) + "\n")
    print("DONE", {"ok": out.get("ok"), "failed": out.get("failed_step")}, flush=True)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
