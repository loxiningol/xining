#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task2 focused niches under WF≥7: SOL15m / BTC15m / DOGE1h / ETH15m / XAU1h."""
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
AVOID = {("ADA-USDT-SWAP", None), ("LTC-USDT-SWAP", None), ("NG-USDT-SWAP", None),
         ("XRP-USDT-SWAP", "15m")}


def avoided(sym, tf):
    for s, t in AVOID:
        if sym == s and (t is None or t == tf):
            return True
    return False


def mk_exh(sym, tf, tag, rsi=54, z_lo=0.1, z_hi=None, hold=12, tp=45, cci=50,
           direction="short", h1_down=False):
    if direction == "short":
        entry = [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z_lo)}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
            {"left": {"feature": "cci"}, "op": "gt", "right": {"value": float(cci)}},
        ]
        if z_hi is not None:
            entry.append({"left": {"feature": "z20"}, "op": "lt", "right": {"value": float(z_hi)}})
        if h1_down:
            entry.append({"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}})
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(tp)}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]
    else:
        entry = [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(rsi)}},
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": float(-abs(z_lo))}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema16"}},
            {"left": {"feature": "cci"}, "op": "lt", "right": {"value": float(-abs(cci))}},
        ]
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(tp)}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}, "role": "invalidation"},
        ]
    key = "frost2t2f_%s_%s_%s" % (sym.split("-")[0].lower(), tf, tag)
    dsl = {
        "key": key,
        "name": "寒霜贰续-%s-%s-exhaustion" % (sym.split("-")[0], tf),
        "direction": direction,
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "max_hold_bars": int(hold),
    }
    return {
        "symbol": sym, "timeframe": tf, "direction": direction,
        "logic_class": "exhaustion_fade",
        "thesis": "frost2 cont task2 focused",
        "title": dsl["name"],
        "dsl": f2.ensure_dsl(dsl, sym, tf),
        "gate_mode": "frost2", "source": "task2_focus",
    }


def mk_reclaim(sym, tf, tag, rsi=40, hold=10, tp=55):
    # DOGE 4h→1h reclaim long proxy
    entry = [
        {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(rsi)}},
        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema8"}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -0.5}},
        {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
        {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
    ]
    exit_any = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(tp)}, "role": "take_profit"},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}, "role": "invalidation"},
    ]
    key = "frost2t2f_%s_%s_rec_%s" % (sym.split("-")[0].lower(), tf, tag)
    dsl = {
        "key": key,
        "name": "寒霜贰续-%s-%s-reclaim" % (sym.split("-")[0], tf),
        "direction": "long",
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "max_hold_bars": int(hold),
    }
    return {
        "symbol": sym, "timeframe": tf, "direction": "long",
        "logic_class": "range_reclaim",
        "thesis": "DOGE-style reclaim under new gates",
        "title": dsl["name"],
        "dsl": f2.ensure_dsl(dsl, sym, tf),
        "gate_mode": "frost2", "source": "task2_focus",
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
        "sh": bm.get("sharpe"), "dest": (q.get("logic_destruction") or {}).get("pass"),
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
    cands = []
    # SOL 15m exhaustion (GLM direction)
    for rsi, z_lo, z_hi, hold, tp, cci in [
        (54, 0.1, 1.0, 12, 45, 50),
        (56, 0.15, 1.2, 10, 42, 60),
        (58, 0.2, 1.0, 14, 40, 50),
        (55, 0.1, 0.8, 16, 45, 55),
        (60, 0.25, None, 12, 42, 70),
        (52, 0.05, 0.9, 20, 45, 40),
    ]:
        cands.append(mk_exh("SOL-USDT-SWAP", "15m", "a%s" % rsi, rsi=rsi, z_lo=z_lo,
                            z_hi=z_hi, hold=hold, tp=tp, cci=cci))
        cands.append(mk_exh("SOL-USDT-SWAP", "15m", "h1_%s" % rsi, rsi=rsi, z_lo=z_lo,
                            z_hi=z_hi, hold=hold, tp=tp, cci=cci, h1_down=True))
    # BTC 15m exhaustion
    for rsi, z_hi, hold, tp in [
        (54, 1.0, 12, 45), (56, 0.8, 10, 42), (58, 1.2, 14, 40),
        (55, 1.0, 20, 45), (60, 1.5, 12, 42),
    ]:
        cands.append(mk_exh("BTC-USDT-SWAP", "15m", "b%s" % rsi, rsi=rsi, z_hi=z_hi,
                            hold=hold, tp=tp))
    # ETH 15m
    for rsi, hold, tp in [(54, 12, 45), (56, 10, 42), (58, 14, 40)]:
        cands.append(mk_exh("ETH-USDT-SWAP", "15m", "e%s" % rsi, rsi=rsi, z_hi=1.0,
                            hold=hold, tp=tp))
    # DOGE 1h reclaim
    for rsi, hold, tp in [(35, 8, 55), (40, 10, 58), (42, 12, 60), (38, 10, 55), (45, 14, 62)]:
        cands.append(mk_reclaim("DOGE-USDT-SWAP", "1h", "r%s" % rsi, rsi=rsi, hold=hold, tp=tp))
    # XAU / XAG 1h exhaustion short
    for sym in ("XAU-USDT-SWAP", "XAG-USDT-SWAP"):
        for rsi, hold in [(54, 10), (56, 12), (58, 8)]:
            cands.append(mk_exh(sym, "1h", "x%s" % rsi, rsi=rsi, z_hi=1.0, hold=hold, tp=45))
    # XRP 5m only (15m avoided)
    for rsi, hold in [(54, 16), (56, 12), (58, 20)]:
        cands.append(mk_exh("XRP-USDT-SWAP", "5m", "xrp5_%s" % rsi, rsi=rsi, z_hi=1.0,
                            hold=hold, tp=45))

    # dedupe avoid
    filtered = []
    for b in cands:
        if avoided(b["symbol"], b["timeframe"]):
            continue
        filtered.append(b)
    print("task2_focus_cands", len(filtered), flush=True)

    winners = []
    near = []
    failed = []
    for i, book in enumerate(filtered):
        passed, s, packs = eval_one(book)
        if s.get("quick") and s.get("full"):
            print("FULL", s, flush=True)
            sf = f2.run_sim_formal(book, packs)
            row = {
                "summary": s, "sim": sf.get("sim"), "formal": sf.get("formal"),
                "pending": sf.get("pending"), "failed_step": sf.get("failed_step"),
                "ok": not sf.get("failed_step"),
            }
            winners.append(row)
            print("SF", row.get("failed_step"), row.get("pending"), flush=True)
            if len(winners) >= 2:
                break
        elif s.get("quick"):
            near.append(s)
            print("QUICK_NOT_FULL", s, flush=True)
        else:
            failed.append(s)
            if i % 12 == 0:
                print("..", s.get("key"), s.get("fp"), s.get("dest"), flush=True)

    near.sort(key=lambda x: (
        float(x.get("friction_sh") if x.get("friction_sh") is not None else -99),
        float(x.get("mc_beat") or 0),
    ), reverse=True)

    pending_keys = []
    for w in winners:
        if (w.get("pending") or {}).get("ok"):
            pending_keys.append(w["pending"]["key"])

    out = {
        "ok": len(pending_keys) > 0,
        "pending_keys": pending_keys,
        "winners": winners,
        "near": near[:15],
        "n_failed_quick": len(failed),
        "n_cands": len(filtered),
    }
    open(os.path.join(OUT, "frost2_cont_task2_focus.json"), "w").write(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n")

    st_path = os.path.join(OUT, "frost2_cont_status.json")
    try:
        st = json.load(open(st_path))
    except Exception:
        st = {"op": "寒霜贰续"}
    st["task2_focus"] = {
        "ok": out["ok"],
        "pending_keys": pending_keys,
        "n_winners": len(winners),
        "near": near[:5],
        "n_failed_quick": len(failed),
    }
    keys = list(st.get("pending_keys") or [])
    for k in pending_keys:
        if k not in keys:
            keys.append(k)
    st["pending_keys"] = keys
    open(st_path, "w").write(json.dumps(st, ensure_ascii=False, indent=2) + "\n")
    print("DONE", out["ok"], pending_keys, "near", (near[0] if near else None), flush=True)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
