#!/usr/bin/env python3
import copy
import json
import sys
sys.path.insert(0, "/root")
import frost2_action_run as f2

base_entry = [
    {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 54.0}},
    {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.1}},
    {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
    {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
]
pending = []
for i, (hold, tp, inv, ema, rsi, z) in enumerate([
    (20, 45, "prev_high20", "ema16", 54, 0.1),
    (20, 42, "prev_high20", "ema16", 54, 0.1),
    (18, 45, "ema8", "ema16", 54, 0.1),
    (22, 48, "prev_high20", "ema16", 54, 0.1),
    (20, 45, "prev_high20", "ema21", 54, 0.1),
    (20, 45, "prev_high20", "ema16", 53, 0.0),
    (20, 45, "prev_high20", "ema16", 55, 0.2),
    (24, 40, "prev_high20", "ema16", 54, 0.1),
    (16, 45, "prev_high20", "ema16", 54, 0.15),
    (20, 45, "ema21", "ema16", 54, 0.1),
    (14, 50, "prev_high20", "ema16", 54, 0.1),
    (26, 45, "prev_high20", "ema16", 54, 0.0),
    (20, 45, "prev_high20", "ema16", 54, 0.05),
    (30, 45, "prev_high20", "ema16", 54, 0.1),
]):
    entry = copy.deepcopy(base_entry)
    entry[0]["right"]["value"] = float(rsi)
    entry[1]["right"]["value"] = float(z)
    entry[3]["right"] = {"feature": ema}
    dsl = {
        "key": "frost2w5b_cl_15m_%d" % i,
        "name": "寒霜贰-CL-15m-exhaustion_fade",
        "direction": "short",
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(tp)},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": inv},
             "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
        "description": "dest-repair near",
    }
    book = {
        "symbol": "CL-USDT-SWAP", "timeframe": "15m", "direction": "short",
        "logic_class": "exhaustion_fade", "thesis": "CL near fp7 dest-repair",
        "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"), "gate_mode": "frost2",
    }
    packs = f2.quick_suite(book)
    bm = packs.get("base_metrics") or {}
    s = {
        "key": dsl["key"], "pass": packs.get("quick_pass"),
        "fp": bm.get("fold_positive"), "tr": bm.get("trades"),
        "wr": round(float(bm.get("win_rate_pct") or 0), 1),
        "sh": bm.get("sharpe"),
        "dest": (packs.get("logic_destruction") or {}).get("pass"),
        "failed": packs.get("failed_step"),
    }
    print("V", s, flush=True)
    if packs.get("quick_pass"):
        packs = f2.full_suite(book, packs)
        print("FULL", packs.get("full_pass"), packs.get("full"), flush=True)
        if packs.get("full_pass"):
            sf = f2.run_sim_formal(book, packs)
            print("SF", sf.get("failed_step"),
                  (sf.get("sim") or {}).get("wr_deepseek_sim"),
                  (sf.get("sim") or {}).get("wr_qwen_sim"),
                  (sf.get("formal") or {}).get("approved"),
                  sf.get("pending"), flush=True)
            if (sf.get("pending") or {}).get("ok"):
                pending.append(sf["pending"]["key"])

out = {"pending_keys": pending, "ok": len(pending) >= 1}
open("/root/auto_trade/dual_engine/frost2_wave5b.json", "w").write(
    json.dumps(out, ensure_ascii=False, indent=2) + "\n")
print("DONE", out, flush=True)
