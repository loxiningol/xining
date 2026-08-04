#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task2 under WF>=7: broader niches beyond exhausted SOL/BTC/DOGE clones."""
from __future__ import print_function
import json, os, sys
sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
OUT = "/root/auto_trade/dual_engine"


def exh(sym, tf, rsi, z, hold, tp, tag, extra=None):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z)}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
    ]
    if extra:
        entry.extend(extra)
    dsl = {
        "key": "frost2t2_%s_%s_exh_%s" % (sym.split("-")[0].lower(), tf, tag),
        "name": "寒霜贰-%s-%s-exhaustion_fade" % (sym.split("-")[0], tf),
        "direction": "short",
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(tp)}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
        "description": "task2 exhaustion",
    }
    return {
        "symbol": sym, "timeframe": tf, "direction": "short",
        "logic_class": "exhaustion_fade",
        "thesis": "postmortem exhaustion family under WF7",
        "title": dsl["name"],
        "dsl": f2.ensure_dsl(dsl, sym, tf), "gate_mode": "frost2",
        "source": "frost2_cont_task2b",
    }


def tpb(sym, tf, rsi, z, hold, tag):
    dsl = {
        "key": "frost2t2_%s_%s_tpb_%s" % (sym.split("-")[0].lower(), tf, tag),
        "name": "寒霜贰-%s-%s-trend_pullback" % (sym.split("-")[0], tf),
        "direction": "long",
        "entry": {"all": [
            {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
            {"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": float(z)}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
        ]},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 68.0}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}, "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
        "description": "task2 trendpb",
    }
    return {
        "symbol": sym, "timeframe": tf, "direction": "long",
        "logic_class": "trend_pullback",
        "thesis": "ada-structure port non-ADA",
        "title": dsl["name"],
        "dsl": f2.ensure_dsl(dsl, sym, tf), "gate_mode": "frost2",
        "source": "frost2_cont_task2b",
    }


def main():
    cci = {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 50.0}}
    books = []
    # commodity / majors exhaustion with cci stabilizer (CL lesson)
    for sym, tf in [
        ("CL-USDT-SWAP", "15m"), ("CL-USDT-SWAP", "1h"),
        ("XAU-USDT-SWAP", "15m"), ("XAU-USDT-SWAP", "1h"),
        ("XAG-USDT-SWAP", "5m"), ("XAG-USDT-SWAP", "15m"),
        ("BTC-USDT-SWAP", "1h"), ("ETH-USDT-SWAP", "15m"),
        ("ETH-USDT-SWAP", "5m"), ("SOL-USDT-SWAP", "5m"),
        ("DOGE-USDT-SWAP", "5m"), ("XRP-USDT-SWAP", "5m"),  # 5m allowed
    ]:
        for rsi, z, hold, tp, tag in [
            (54, 0.1, 12, 45, "a"), (55, 0.1, 16, 42, "b"),
            (54, 0.15, 20, 45, "c"), (56, 0.2, 14, 42, "d"),
            (52, 0.0, 18, 48, "e"),
        ]:
            books.append(exh(sym, tf, rsi, z, hold, tp, tag, extra=[cci]))

    for sym, tf in [
        ("BTC-USDT-SWAP", "5m"), ("ETH-USDT-SWAP", "5m"),
        ("SOL-USDT-SWAP", "5m"), ("DOGE-USDT-SWAP", "5m"),
        ("CL-USDT-SWAP", "5m"), ("XAG-USDT-SWAP", "5m"),
    ]:
        for rsi, z, hold, tag in [
            (42, 2.3, 14, "a"), (40, 2.0, 12, "b"), (45, 2.5, 16, "c"),
        ]:
            books.append(tpb(sym, tf, rsi, z, hold, tag))

    books = [b for b in books if not f2.avoided(b["symbol"], b["timeframe"])]
    print("task2b candidates", len(books), flush=True)

    pending = []
    survivors = []
    near_quick = []
    for i, book in enumerate(books):
        q = f2.quick_suite(book)
        bm = q.get("base_metrics") or {}
        s = {
            "key": (book.get("dsl") or {}).get("key"),
            "symbol": book["symbol"], "tf": book["timeframe"],
            "logic": book.get("logic_class"),
            "quick": q.get("quick_pass"),
            "fp": bm.get("fold_positive"), "tr": bm.get("trades"),
            "wr": round(float(bm.get("win_rate_pct") or 0), 1),
            "sh": bm.get("sharpe"), "dest": (q.get("logic_destruction") or {}).get("pass"),
        }
        if not q.get("quick_pass"):
            if int(bm.get("fold_positive") or 0) >= 7:
                near_quick.append(s)
                print("NEAR", s, flush=True)
            elif i % 15 == 0:
                print("..", s, flush=True)
            survivors.append({**s, "ok": False, "failed_step": q.get("failed_step")})
            continue
        print("QUICK_PASS", s, flush=True)
        full = f2.full_suite(book, q)
        s["full"] = full.get("full_pass")
        s["friction_sh"] = (full.get("full") or {}).get("friction_sharpe")
        s["mc_beat"] = ((full.get("full") or {}).get("mc") or {}).get("beat_ratio")
        if not full.get("full_pass"):
            survivors.append({**s, "ok": False, "failed_step": full.get("failed_step")})
            print("FULL_FAIL", s, flush=True)
            continue
        sf = f2.run_sim_formal(book, full)
        item = {
            **s, "ok": not sf.get("failed_step"),
            "failed_step": sf.get("failed_step"),
            "sim": sf.get("sim"), "formal": sf.get("formal"),
            "pending": sf.get("pending"),
        }
        survivors.append(item)
        print("SF", item["key"], item["failed_step"],
              (item.get("sim") or {}).get("wr_deepseek_sim"),
              (item.get("formal") or {}).get("approved"),
              item.get("pending"), flush=True)
        if (item.get("pending") or {}).get("ok"):
            pending.append(item["pending"]["key"])
        if len(pending) >= 2:
            break

    out = {
        "pending_keys": pending,
        "ok": len(pending) >= 1,
        "near_quick": near_quick[:10],
        "survivors_pass": [x for x in survivors if x.get("ok")],
        "deaths": [
            {"key": x.get("key"), "symbol": x.get("symbol"), "tf": x.get("tf"),
             "failed_step": x.get("failed_step"), "fp": x.get("fp"), "dest": x.get("dest")}
            for x in survivors if not x.get("ok")
        ][:40],
        "n": len(books),
    }
    open(os.path.join(OUT, "frost2_cont_task2b.json"), "w").write(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    st_path = os.path.join(OUT, "frost2_cont_status.json")
    try:
        st = json.load(open(st_path))
    except Exception:
        st = {"op": "寒霜贰续"}
    st["task2b"] = out
    keys = list(st.get("pending_keys") or [])
    for k in pending:
        if k not in keys:
            keys.append(k)
    st["pending_keys"] = keys
    open(st_path, "w").write(json.dumps(st, ensure_ascii=False, indent=2) + "\n")
    print("DONE", {"ok": out["ok"], "pending": pending, "near": len(near_quick)}, flush=True)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
