#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""After MC bootstrap fix: retest XRP/CL Full, then push any Full winners to pending."""
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


def mk_exh(sym, tf, key, rsi, z, hold, tp, cci=None):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z)}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
    ]
    if cci is not None:
        entry.append({"left": {"feature": "cci"}, "op": "gt", "right": {"value": float(cci)}})
    dsl = {
        "key": key,
        "name": "寒霜贰续-%s-%s-exhaustion_fade" % (sym.split("-")[0], tf),
        "direction": "short",
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(tp)}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
    }
    return {
        "symbol": sym, "timeframe": tf, "direction": "short",
        "logic_class": "exhaustion_fade",
        "thesis": "post-MC-fix",
        "title": dsl["name"],
        "dsl": f2.ensure_dsl(dsl, sym, tf),
        "gate_mode": "frost2", "source": "mcfix",
    }


def run_one(book, push=True):
    q = f2.quick_suite(book)
    bm = q.get("base_metrics") or {}
    s = {
        "key": book["dsl"]["key"], "symbol": book["symbol"], "tf": book["timeframe"],
        "quick": q.get("quick_pass"), "fp": bm.get("fold_positive"),
        "tr": bm.get("trades"), "wr": bm.get("win_rate_pct"), "sh": bm.get("sharpe"),
        "dest": (q.get("logic_destruction") or {}).get("pass"),
    }
    if not q.get("quick_pass"):
        s["failed_step"] = q.get("failed_step")
        return s
    full = f2.full_suite(book, q)
    s["full"] = full.get("full_pass")
    s["friction_sh"] = (full.get("full") or {}).get("friction_sharpe")
    mc = (full.get("full") or {}).get("mc") or {}
    s["mc_beat"] = mc.get("beat_ratio")
    s["mc_actual"] = mc.get("actual_final")
    s["mc_method"] = mc.get("method")
    s["failed_step"] = full.get("failed_step")
    if full.get("full_pass") and push:
        # skip forbidden niches for pending
        if book["symbol"] in ("ADA-USDT-SWAP", "LTC-USDT-SWAP", "NG-USDT-SWAP"):
            s["skipped"] = "forbidden_live"
            return s
        if book["symbol"] == "XRP-USDT-SWAP" and book["timeframe"] == "15m":
            s["skipped"] = "xrp15_already_live"
            return s
        sf = f2.run_sim_formal(book, full)
        s["sim"] = sf.get("sim")
        s["formal"] = sf.get("formal")
        s["pending"] = sf.get("pending")
        s["sf_failed"] = sf.get("failed_step")
        s["ok"] = not sf.get("failed_step")
    return s


def main():
    results = {}
    # calibrate
    results["xrp_live_template"] = run_one(
        mk_exh("XRP-USDT-SWAP", "15m", "mcfix_xrp_probe", 55, 0.0, 20, 45), push=False)
    print("XRP", results["xrp_live_template"], flush=True)
    results["cl_cci_h12"] = run_one(
        mk_exh("CL-USDT-SWAP", "15m", "frost2c_cl_15m_cci_h12", 54, 0.1, 12, 45, cci=50),
        push=True)
    print("CL", results["cl_cci_h12"], flush=True)

    # hunt ports that can pass new MC + friction
    cands = []
    for sym, tf in [
        ("SOL-USDT-SWAP", "15m"), ("BTC-USDT-SWAP", "15m"), ("ETH-USDT-SWAP", "15m"),
        ("DOGE-USDT-SWAP", "15m"), ("DOGE-USDT-SWAP", "1h"),
        ("SOL-USDT-SWAP", "1h"), ("XAU-USDT-SWAP", "1h"), ("XAG-USDT-SWAP", "1h"),
        ("XRP-USDT-SWAP", "5m"), ("XRP-USDT-SWAP", "1h"),
        ("CL-USDT-SWAP", "15m"), ("CL-USDT-SWAP", "1h"),
    ]:
        for rsi, z, hold, tp, cci in [
            (55, 0.0, 20, 45, None),
            (54, 0.1, 12, 45, 50),
            (56, 0.0, 16, 42, None),
            (58, 0.2, 20, 45, None),
            (55, 0.0, 24, 48, 40),
            (60, 0.3, 14, 40, 60),
            (52, 0.0, 20, 45, None),
        ]:
            tag = "%s_%s_r%s_h%s" % (sym.split("-")[0].lower(), tf, rsi, hold)
            if cci is not None:
                tag += "_c%s" % cci
            cands.append(mk_exh(sym, tf, "mcfix_" + tag, rsi, z, hold, tp, cci=cci))

    print("hunt_cands", len(cands), flush=True)
    winners, near = [], []
    for i, book in enumerate(cands):
        s = run_one(book, push=True)
        if s.get("full"):
            print("FULL", s, flush=True)
            winners.append(s)
            if (s.get("pending") or {}).get("ok") and len([
                    w for w in winners if (w.get("pending") or {}).get("ok")]) >= 2:
                break
        elif s.get("quick"):
            near.append(s)
            print("NEAR", {k: s.get(k) for k in (
                "key", "symbol", "fp", "friction_sh", "mc_beat", "mc_actual", "failed_step")},
                  flush=True)
        elif i % 15 == 0:
            print("..", s.get("key"), s.get("fp"), s.get("dest"), s.get("failed_step"), flush=True)

    near.sort(key=lambda x: (
        float(x.get("friction_sh") if x.get("friction_sh") is not None else -99),
        float(x.get("mc_beat") or 0),
    ), reverse=True)

    pending_keys = [w["pending"]["key"] for w in winners if (w.get("pending") or {}).get("ok")]
    # also CL if it made pending
    cl = results.get("cl_cci_h12") or {}
    if (cl.get("pending") or {}).get("ok"):
        k = cl["pending"]["key"]
        if k not in pending_keys:
            pending_keys.insert(0, k)

    out = {
        "ok": len(pending_keys) > 0,
        "pending_keys": pending_keys,
        "results_probe": results,
        "winners": winners,
        "near": near[:15],
        "mc_method": "bootstrap_with_replacement",
    }
    open(os.path.join(OUT, "frost2_cont_mcfix.json"), "w").write(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n")

    st_path = os.path.join(OUT, "frost2_cont_status.json")
    try:
        st = json.load(open(st_path))
    except Exception:
        st = {"op": "寒霜贰续"}
    st["mc_fix"] = {
        "method": "bootstrap_with_replacement",
        "reason": "order-shuffle MC was path-independent; live XRP failed beat=0.1",
        "xrp_probe": results.get("xrp_live_template"),
        "cl": results.get("cl_cci_h12"),
        "pending_keys": pending_keys,
        "ok": out["ok"],
    }
    st["gates"] = {"wf": ">=7/10", "dest": True, "mc90_bootstrap": True, "friction_ge0": True}
    if (cl.get("ok") or (cl.get("pending") or {}).get("ok")):
        st["ok_min_cl"] = True
        st["cl_abandoned"] = False
    else:
        st["cl_abandoned"] = not bool(cl.get("full"))
        st["cl_best_near"] = {k: cl.get(k) for k in (
            "key", "quick", "fp", "friction_sh", "mc_beat", "mc_actual", "failed_step")}
    keys = list(st.get("pending_keys") or [])
    for k in pending_keys:
        if k not in keys:
            keys.append(k)
    st["pending_keys"] = keys
    open(st_path, "w").write(json.dumps(st, ensure_ascii=False, indent=2) + "\n")
    print("DONE", out["ok"], pending_keys, flush=True)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
