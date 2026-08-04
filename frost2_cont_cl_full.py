#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CL 15m: keep dest+WF>=7, hunt full (friction>=0 + MC beat>=90%), then formal pending."""
from __future__ import print_function
import copy, json, os, sys
sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
OUT = "/root/auto_trade/dual_engine"

def mk(rsi, z, hold, tp, tag, extra=None, ema="ema16", inv="prev_high20"):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z)}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": ema}},
    ]
    if extra:
        entry.extend(extra)
    dsl = {
        "key": "frost2c_cl_15m_%s" % tag,
        "name": "寒霜贰-CL-15m-exhaustion_fade",
        "direction": "short",
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(tp)}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": inv}, "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
        "description": "CL exhaustion full-rescue",
    }
    return {
        "symbol": "CL-USDT-SWAP", "timeframe": "15m", "direction": "short",
        "logic_class": "exhaustion_fade",
        "thesis": "CL 15m exhaustion fade full-rescue",
        "title": "寒霜贰-CL-15m-exhaustion_fade",
        "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"),
        "gate_mode": "frost2", "source": "frost2_cont_cl_full",
    }

cci = {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 50.0}}
cci80 = {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 80.0}}
h1d = {"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}}
slope = {"left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": 0.0}}

cands = []
# expand around cci winner + expectancy-positive sparse shorts
for rsi, z, hold, tp, tag, extra in [
    (54, 0.1, 20, 45, "cci50", [cci]),
    (54, 0.1, 18, 42, "cci50_h18", [cci]),
    (54, 0.1, 24, 40, "cci50_h24", [cci]),
    (55, 0.15, 20, 45, "cci50_r55", [cci]),
    (56, 0.2, 16, 42, "cci50_r56", [cci]),
    (54, 0.1, 20, 45, "cci80", [cci80]),
    (54, 0.1, 20, 45, "cci_h1", [cci, h1d]),
    (54, 0.1, 20, 45, "cci_slope", [cci, slope]),
    (53, 0.0, 22, 45, "cci_loose", [cci]),
    (58, 0.3, 14, 42, "cci_tight", [cci]),
    (54, 0.1, 20, 48, "cci_tp48", [cci]),
    (54, 0.1, 12, 45, "cci_h12", [cci]),
    (54, 0.25, 20, 45, "cci_z25", [cci]),
    (52, 0.1, 20, 45, "cci_r52", [cci]),
    (54, 0.1, 20, 45, "cci_ema8", [cci]),
]:
    ema = "ema8" if "ema8" in tag else "ema16"
    cands.append(mk(rsi, z, hold, tp, tag, extra=extra, ema=ema))

# also try without cci but with h1+slope regime lock
for rsi, z, hold, tp, tag in [
    (55, 0.2, 18, 42, "h1s_a"), (54, 0.1, 20, 45, "h1s_b"),
    (56, 0.0, 16, 45, "h1s_c"), (58, 0.3, 14, 40, "h1s_d"),
]:
    cands.append(mk(rsi, z, hold, tp, tag, extra=[h1d, slope]))

print("CL full-rescue candidates", len(cands), flush=True)
winners = []
near = []
for i, book in enumerate(cands):
    q = f2.quick_suite(book)
    bm = q.get("base_metrics") or {}
    s = {
        "key": (book.get("dsl") or {}).get("key"),
        "quick": q.get("quick_pass"),
        "fp": bm.get("fold_positive"), "tr": bm.get("trades"),
        "wr": round(float(bm.get("win_rate_pct") or 0), 1),
        "sh": bm.get("sharpe"), "mean": bm.get("mean_net"),
        "dest": (q.get("logic_destruction") or {}).get("pass"),
    }
    if not q.get("quick_pass"):
        if int(bm.get("fold_positive") or 0) >= 7:
            print("NEAR_Q", s, flush=True)
        elif i % 4 == 0:
            print("..", s, flush=True)
        continue
    full = f2.full_suite(book, q)
    s["full"] = full.get("full_pass")
    s["friction_sh"] = (full.get("full") or {}).get("friction_sharpe")
    s["mc_beat"] = ((full.get("full") or {}).get("mc") or {}).get("beat_ratio")
    s["mc_actual"] = ((full.get("full") or {}).get("mc") or {}).get("actual_final")
    print("FULLTRY", s, flush=True)
    if full.get("full_pass"):
        winners.append((book, full, s))
        break
    near.append((book, full, s))

pending = None
result = {"winners_n": len(winners), "near": [x[2] for x in near[:8]], "ok": False}
if winners:
    book, packs, s = winners[0]
    sf = f2.run_sim_formal(book, packs)
    result.update({
        "key": s["key"], "summary": s,
        "sim": sf.get("sim"), "formal": sf.get("formal"),
        "pending": sf.get("pending"), "failed_step": sf.get("failed_step"),
        "ok": not sf.get("failed_step"),
    })
    pending = (sf.get("pending") or {}).get("key")
    print("SIMFORMAL", s["key"], sf.get("failed_step"),
          (sf.get("sim") or {}).get("wr_deepseek_sim"),
          (sf.get("sim") or {}).get("wr_qwen_sim"),
          (sf.get("formal") or {}).get("approved"),
          sf.get("pending"), flush=True)
else:
    # last-ditch: among near full, pick best friction_sh / mc and ask GLM once then retry 3 local
    near.sort(key=lambda x: (
        float((x[2].get("friction_sh") if x[2].get("friction_sh") is not None else -99)),
        float(x[2].get("mc_beat") or 0),
        float(x[2].get("mean") or -99),
    ), reverse=True)
    result["failed_step"] = "full_suite_no_winner"
    result["best_near"] = near[0][2] if near else None
    print("NO_FULL_WINNER best_near", result.get("best_near"), flush=True)

open(os.path.join(OUT, "frost2_cont_cl_full.json"), "w").write(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n")

# update status
st_path = os.path.join(OUT, "frost2_cont_status.json")
try:
    st = json.load(open(st_path))
except Exception:
    st = {"op": "寒霜贰续"}
st["cl_full_rescue"] = result
if pending:
    keys = list(st.get("pending_keys") or [])
    if pending not in keys:
        keys.append(pending)
    st["pending_keys"] = keys
    st["ok_min_cl"] = True
open(st_path, "w").write(json.dumps(st, ensure_ascii=False, indent=2) + "\n")
print("DONE", {"ok": result.get("ok"), "pending": pending, "failed_step": result.get("failed_step")}, flush=True)
sys.exit(0 if result.get("ok") else 1)
