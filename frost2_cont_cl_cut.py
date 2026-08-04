#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, sys
sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7


def mk(tag, rsi=54, z=0.1, hold=12, tp=45, cci=50, inv="prev_high20", extra=None, ema="ema16"):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z)}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": ema}},
        {"left": {"feature": "cci"}, "op": "gt", "right": {"value": float(cci)}},
    ]
    if extra:
        entry.extend(extra)
    dsl = {
        "key": "frost2c_cl_cut_%s" % tag,
        "name": "寒霜贰-CL-15m-exhaustion_fade",
        "direction": "short",
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(tp)}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": inv}, "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
    }
    if tag.startswith("zinv"):
        dsl["exit"]["any"].append(
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 0.0}, "role": "invalidation"})
    return {
        "symbol": "CL-USDT-SWAP", "timeframe": "15m", "direction": "short",
        "logic_class": "exhaustion_fade", "thesis": "cut stop-cluster losses",
        "title": "寒霜贰-CL-15m-exhaustion_fade",
        "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"),
        "gate_mode": "frost2", "source": "cl_cut",
    }


def main():
    cands = []
    for inv in ["ema8", "ema6", "ema21"]:
        for hold, tp in [(8, 42), (10, 45), (12, 40), (12, 45), (6, 45)]:
            cands.append(mk("inv_%s_h%s_t%s" % (inv, hold, tp), inv=inv, hold=hold, tp=tp))
    for rsi, z, hold, tp in [(58, 0.5, 10, 42), (60, 0.4, 12, 40), (56, 0.3, 8, 42), (55, 0.2, 10, 45)]:
        cands.append(mk("strict_r%s" % rsi, rsi=rsi, z=z, hold=hold, tp=tp, inv="ema8"))
        cands.append(mk("zinv_r%s" % rsi, rsi=rsi, z=z, hold=hold, tp=tp, inv="ema8"))
    h1 = {"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}}
    for hold in (8, 10, 12):
        cands.append(mk("h1ema8_h%s" % hold, hold=hold, tp=42, inv="ema8", extra=[h1]))

    print("cut_cands", len(cands), flush=True)
    winner = None
    best = []
    for i, book in enumerate(cands):
        q = f2.quick_suite(book)
        bm = q.get("base_metrics") or {}
        if not q.get("quick_pass"):
            if i % 8 == 0:
                print("..", book["dsl"]["key"], bm.get("fold_positive"),
                      (q.get("logic_destruction") or {}).get("pass"), flush=True)
            continue
        full = f2.full_suite(book, q)
        s = {
            "key": book["dsl"]["key"], "fp": bm.get("fold_positive"),
            "tr": bm.get("trades"), "wr": round(float(bm.get("win_rate_pct") or 0), 1),
            "mean": bm.get("mean_net"), "sh": bm.get("sharpe"),
            "fr": (full.get("full") or {}).get("friction_sharpe"),
            "mc": ((full.get("full") or {}).get("mc") or {}).get("beat_ratio"),
            "act": ((full.get("full") or {}).get("mc") or {}).get("actual_final"),
            "full": full.get("full_pass"),
        }
        best.append(s)
        print("TRY", s, flush=True)
        if full.get("full_pass"):
            winner = (book, full, s)
            break

    best.sort(key=lambda x: (
        float(x.get("fr") if x.get("fr") is not None else -9),
        float(x.get("mc") or 0),
        float(x.get("act") or -9),
    ), reverse=True)
    out = {"best": best[:8], "ok": False}
    if winner:
        book, full, s = winner
        sf = f2.run_sim_formal(book, full)
        out.update({
            "ok": not sf.get("failed_step"), "key": s["key"], "summary": s,
            "sim": sf.get("sim"), "formal": sf.get("formal"),
            "pending": sf.get("pending"), "failed_step": sf.get("failed_step"),
        })
        print("SF", out["failed_step"], out.get("pending"), flush=True)
    else:
        out["failed_step"] = "full_suite_no_winner"
        out["best_near"] = best[0] if best else None
        print("NO_WIN", out.get("best_near"), flush=True)

    open("/root/auto_trade/dual_engine/frost2_cont_cl_cut.json", "w").write(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    st = json.load(open("/root/auto_trade/dual_engine/frost2_cont_status.json"))
    st["cl_cut"] = {
        "ok": out.get("ok"), "failed_step": out.get("failed_step"),
        "key": out.get("key"), "pending": out.get("pending"),
        "best_near": out.get("best_near") or (out.get("best") or [None])[0],
    }
    if (out.get("pending") or {}).get("ok"):
        keys = list(st.get("pending_keys") or [])
        k = out["pending"]["key"]
        if k not in keys:
            keys.append(k)
        st["pending_keys"] = keys
        st["ok_min_cl"] = True
    open("/root/auto_trade/dual_engine/frost2_cont_status.json", "w").write(
        json.dumps(st, ensure_ascii=False, indent=2) + "\n")
    print("DONE", {"ok": out.get("ok"), "pending": (out.get("pending") or {}).get("key"),
                   "failed": out.get("failed_step")}, flush=True)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
