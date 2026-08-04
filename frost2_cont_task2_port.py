#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Port live-proven XRP exhaustion template across allowed niches under WF≥7."""
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


def mk_xrp_port(sym, tf, tag, rsi=55, z=0.0, hold=20, tp=45, cci=None, h1=False):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z)}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
    ]
    if cci is not None:
        entry.append({"left": {"feature": "cci"}, "op": "gt", "right": {"value": float(cci)}})
    if h1:
        entry.append({"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}})
    key = "frost2t2x_%s_%s_%s" % (sym.split("-")[0].lower(), tf, tag)
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
        "thesis": "XRP-proven exhaustion port",
        "title": dsl["name"],
        "dsl": f2.ensure_dsl(dsl, sym, tf),
        "gate_mode": "frost2", "source": "task2_xrp_port",
    }


def mk_reclaim(sym, tf, tag, rsi=38, hold=12, tp=58):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(rsi)}},
        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema8"}},
        {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -1.0}},
        {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
    ]
    key = "frost2t2x_%s_%s_rec_%s" % (sym.split("-")[0].lower(), tf, tag)
    dsl = {
        "key": key,
        "name": "寒霜贰续-%s-%s-range_reclaim" % (sym.split("-")[0], tf),
        "direction": "long",
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(tp)}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}, "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
    }
    return {
        "symbol": sym, "timeframe": tf, "direction": "long",
        "logic_class": "range_reclaim",
        "thesis": "GLM DOGE reclaim direction",
        "title": dsl["name"],
        "dsl": f2.ensure_dsl(dsl, sym, tf),
        "gate_mode": "frost2", "source": "task2_xrp_port",
    }


def eval_one(book):
    q = f2.quick_suite(book)
    bm = q.get("base_metrics") or {}
    s = {
        "key": book["dsl"]["key"], "symbol": book["symbol"], "tf": book["timeframe"],
        "logic": book["logic_class"],
        "quick": bool(q.get("quick_pass")),
        "fp": bm.get("fold_positive"), "tr": bm.get("trades"),
        "wr": round(float(bm.get("win_rate_pct") or 0), 1),
        "sh": bm.get("sharpe"), "mean": bm.get("mean_net"),
        "dest": (q.get("logic_destruction") or {}).get("pass"),
    }
    if not q.get("quick_pass"):
        return None, s, q
    full = f2.full_suite(book, q)
    s["full"] = full.get("full_pass")
    s["friction_sh"] = (full.get("full") or {}).get("friction_sharpe")
    mc = (full.get("full") or {}).get("mc") or {}
    s["mc_beat"] = mc.get("beat_ratio")
    s["mc_actual"] = mc.get("actual_final")
    return (full if full.get("full_pass") else None), s, full


def main():
    # sanity: XRP template on XRP should still pass quick under WF7 (avoid pushing XRP15m)
    probe = mk_xrp_port("XRP-USDT-SWAP", "15m", "probe_only")
    pq = f2.quick_suite(probe)
    pbm = pq.get("base_metrics") or {}
    print("XRP_PROBE", {
        "quick": pq.get("quick_pass"), "fp": pbm.get("fold_positive"),
        "wr": pbm.get("win_rate_pct"), "sh": pbm.get("sharpe"),
        "dest": (pq.get("logic_destruction") or {}).get("pass"),
    }, flush=True)

    cands = []
    niches = [
        ("SOL-USDT-SWAP", "15m"),
        ("BTC-USDT-SWAP", "15m"),
        ("ETH-USDT-SWAP", "15m"),
        ("DOGE-USDT-SWAP", "15m"),
        ("DOGE-USDT-SWAP", "1h"),
        ("SOL-USDT-SWAP", "1h"),
        ("BTC-USDT-SWAP", "1h"),
        ("ETH-USDT-SWAP", "1h"),
        ("XAU-USDT-SWAP", "1h"),
        ("XAG-USDT-SWAP", "1h"),
        ("XRP-USDT-SWAP", "5m"),  # not 15m
        ("XRP-USDT-SWAP", "1h"),
    ]
    for sym, tf in niches:
        for rsi, z, hold, tp in [
            (55, 0.0, 20, 45),
            (54, 0.1, 12, 45),
            (56, 0.0, 16, 42),
            (58, 0.2, 20, 45),
            (55, 0.0, 24, 48),
            (52, 0.0, 20, 45),
            (60, 0.3, 14, 40),
        ]:
            tag = "r%s_z%s_h%s" % (rsi, str(z).replace(".", "p"), hold)
            cands.append(mk_xrp_port(sym, tf, tag, rsi=rsi, z=z, hold=hold, tp=tp))
        cands.append(mk_xrp_port(sym, tf, "cci50_h20", rsi=55, z=0.0, hold=20, tp=45, cci=50))
        cands.append(mk_xrp_port(sym, tf, "h1_r55", rsi=55, z=0.0, hold=20, tp=45, h1=True))

    for rsi, hold, tp in [(35, 10, 55), (38, 12, 58), (40, 14, 60), (42, 10, 55)]:
        cands.append(mk_reclaim("DOGE-USDT-SWAP", "1h", "r%s" % rsi, rsi=rsi, hold=hold, tp=tp))
        cands.append(mk_reclaim("SOL-USDT-SWAP", "1h", "r%s" % rsi, rsi=rsi, hold=hold, tp=tp))

    print("port_cands", len(cands), flush=True)
    winners = []
    near = []
    for i, book in enumerate(cands):
        # skip forbidden XRP 15m for formal path (probe already done)
        if book["symbol"] == "XRP-USDT-SWAP" and book["timeframe"] == "15m":
            continue
        passed, s, packs = eval_one(book)
        if s.get("quick") and s.get("full"):
            print("FULL", s, flush=True)
            sf = f2.run_sim_formal(book, packs)
            row = {
                "summary": s,
                "sim": sf.get("sim"),
                "formal": sf.get("formal"),
                "pending": sf.get("pending"),
                "failed_step": sf.get("failed_step"),
                "ok": not sf.get("failed_step"),
            }
            winners.append(row)
            print("SF", book["dsl"]["key"], row.get("failed_step"),
                  (row.get("pending") or {}).get("key"), flush=True)
            if sum(1 for w in winners if w.get("ok")) >= 2:
                break
        elif s.get("quick"):
            near.append(s)
            print("NEAR", s, flush=True)
        elif i % 20 == 0:
            print("..", s.get("key"), s.get("fp"), s.get("dest"), flush=True)

    near.sort(key=lambda x: (
        float(x.get("friction_sh") if x.get("friction_sh") is not None else -99),
        float(x.get("mc_beat") or 0),
        float(x.get("mean") or -99),
    ), reverse=True)

    pending_keys = [w["pending"]["key"] for w in winners
                    if (w.get("pending") or {}).get("ok")]
    out = {
        "ok": len(pending_keys) > 0,
        "pending_keys": pending_keys,
        "winners": winners,
        "near": near[:20],
        "n_cands": len(cands),
        "xrp_probe": {
            "quick": pq.get("quick_pass"),
            "fp": pbm.get("fold_positive"),
            "wr": pbm.get("win_rate_pct"),
            "sh": pbm.get("sharpe"),
        },
    }
    open(os.path.join(OUT, "frost2_cont_task2_port.json"), "w").write(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n")

    st_path = os.path.join(OUT, "frost2_cont_status.json")
    try:
        st = json.load(open(st_path))
    except Exception:
        st = {"op": "寒霜贰续"}
    st["cl_abandoned"] = True
    st["cl_abandon_reason"] = "full_friction_and_mc_unrecoverable_after_dest_fix_and_grids"
    st["task2_port"] = {
        "ok": out["ok"],
        "pending_keys": pending_keys,
        "near": near[:5],
        "n_winners_attempted": len(winners),
    }
    keys = list(st.get("pending_keys") or [])
    for k in pending_keys:
        if k not in keys:
            keys.append(k)
    st["pending_keys"] = keys
    open(st_path, "w").write(json.dumps(st, ensure_ascii=False, indent=2) + "\n")
    print("DONE", {"ok": out["ok"], "pending": pending_keys,
                   "near0": near[0] if near else None}, flush=True)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
