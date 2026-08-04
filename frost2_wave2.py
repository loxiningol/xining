#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜贰 wave2 — GLM repair templates + engineered siblings + formal push."""
from __future__ import print_function
import copy
import json
import sys
from datetime import datetime

sys.path.insert(0, "/root")
import frost2_action_run as f2

OUT = "/root/auto_trade/dual_engine/frost2_wave2.json"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def leaf_from_spec(spec, role=None):
    feat = spec.get("feature")
    if feat in ("max_hold", "hold"):
        return None
    op = spec.get("op") or "gt"
    right = {}
    if "feature_right" in spec:
        right = {"feature": spec["feature_right"]}
    elif "value" in spec:
        right = {"value": float(spec["value"])}
    else:
        return None
    row = {"left": {"feature": feat}, "op": op, "right": right}
    if role:
        # map stop_loss -> invalidation for engine
        row["role"] = "invalidation" if role in ("stop_loss", "time_stop") else role
    return row


def book_from_template(t, tag):
    symbol = t["symbol"]
    tf = f2._remap_timeframe(t.get("timeframe"))
    direction = t.get("direction") or "long"
    logic = t.get("logic_class") or "custom"
    entry = []
    for spec in t.get("entry_all") or []:
        row = leaf_from_spec(spec)
        if row:
            entry.append(row)
    exit_any = []
    for spec in t.get("exit_any") or []:
        row = leaf_from_spec(spec, role=spec.get("role"))
        if row:
            exit_any.append(row)
    if not exit_any:
        if direction == "long":
            exit_any = [
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 1.8},
                 "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"},
                 "role": "invalidation"},
            ]
        else:
            exit_any = [
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": -1.8},
                 "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"},
                 "role": "invalidation"},
            ]
    hold = int(t.get("max_hold_bars") or (24 if tf == "15m" else 12))
    key = "frost2w2_%s_%s_%s_%s" % (
        symbol.split("-")[0].lower(), tf, logic[:14], tag)
    dsl = {
        "key": key,
        "name": "寒霜贰-%s-%s-%s" % (symbol.split("-")[0], tf, logic),
        "direction": direction,
        "timeframe": tf,
        "supported_instruments": [symbol],
        "max_hold_bars": hold,
        "description": "wave2 %s" % logic,
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "schema": "qiyu_strategy_dsl_v1",
    }
    return {
        "symbol": symbol, "timeframe": tf, "direction": direction,
        "logic_class": logic,
        "thesis": "GLM repair wave2 %s" % logic,
        "dsl": f2.ensure_dsl(dsl, symbol, tf),
        "gate_mode": "frost2",
        "source": "frost2_wave2",
    }


def twin(book, symbol=None, timeframe=None, **nudge):
    b = copy.deepcopy(book)
    symbol = symbol or b["symbol"]
    timeframe = timeframe or b["timeframe"]
    b["symbol"] = symbol
    b["timeframe"] = timeframe
    dsl = b["dsl"]
    dsl["supported_instruments"] = [symbol]
    dsl["timeframe"] = timeframe
    if "hold" in nudge:
        dsl["max_hold_bars"] = int(nudge["hold"])
    # nudge numeric thresholds
    for row in (dsl.get("entry") or {}).get("all") or []:
        feat = ((row.get("left") or {}).get("feature") or "")
        right = row.get("right") or {}
        if "value" not in right:
            continue
        v = float(right["value"])
        if feat == "z20" and "z_entry" in nudge:
            right["value"] = float(nudge["z_entry"])
        if feat == "rsi14" and "rsi" in nudge:
            right["value"] = float(nudge["rsi"])
        if feat == "h1_slope4" and "slope" in nudge:
            right["value"] = float(nudge["slope"])
    for row in (dsl.get("exit") or {}).get("any") or []:
        feat = ((row.get("left") or {}).get("feature") or "")
        right = row.get("right") or {}
        if feat == "z20" and "z_tp" in nudge and "value" in right:
            right["value"] = float(nudge["z_tp"])
    dsl["key"] = "frost2w2_%s_%s_%s_%s" % (
        symbol.split("-")[0].lower(), timeframe,
        (b.get("logic_class") or "x")[:14],
        nudge.get("tag") or "twin")
    dsl["name"] = "寒霜贰-%s-%s-%s" % (
        symbol.split("-")[0], timeframe, b.get("logic_class"))
    b["dsl"] = f2.ensure_dsl(dsl, symbol, timeframe)
    return b


def mk_breakdown_short(symbol, tf, tag, rsi_hi=48, z_lo=-0.2, hold=20):
    dsl = {
        "key": "frost2w2_%s_%s_bd_%s" % (symbol.split("-")[0].lower(), tf, tag),
        "name": "寒霜贰-%s-%s-breakdown_short" % (symbol.split("-")[0], tf),
        "direction": "short",
        "entry": {"all": [
            {"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}},
            {"left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(rsi_hi)}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": float(z_lo)}},
        ]},
        "exit": {"any": [
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": -2.2},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"},
             "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
        "description": "wave2 breakdown continuation short",
    }
    return {
        "symbol": symbol, "timeframe": tf, "direction": "short",
        "logic_class": "breakdown_continuation",
        "thesis": "H1 downtrend + close break prev_low20 sparse short",
        "dsl": f2.ensure_dsl(dsl, symbol, tf),
        "gate_mode": "frost2", "source": "frost2_wave2",
    }


def mk_trend_pb(symbol, tf, tag, z_entry=-1.0, z_tp=1.8, hold=36, need_h1=True):
    entry = []
    if need_h1:
        entry.extend([
            {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
            {"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0}},
        ])
    else:
        entry.append({"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0}})
    entry.extend([
        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
        {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
        {"left": {"feature": "z20"}, "op": "lt", "right": {"value": float(z_entry)}},
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 40.0}},
        {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 55.0}},
    ])
    dsl = {
        "key": "frost2w2_%s_%s_tpb_%s" % (symbol.split("-")[0].lower(), tf, tag),
        "name": "寒霜贰-%s-%s-trend_pullback" % (symbol.split("-")[0], tf),
        "direction": "long",
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z_tp)},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"},
             "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
        "description": "wave2 trend pullback long",
    }
    return {
        "symbol": symbol, "timeframe": tf, "direction": "long",
        "logic_class": "trend_pullback",
        "thesis": "GLM repair: H1 up + pullback into ema8/z20 dip",
        "dsl": f2.ensure_dsl(dsl, symbol, tf),
        "gate_mode": "frost2", "source": "frost2_wave2",
    }


def main():
    # load glm single template if present
    try:
        raw = json.load(open("/root/auto_trade/dual_engine/frost2_glm_repair_dirs.json"))
        parsed = raw.get("parsed") or {}
    except Exception:
        parsed = {}

    books = []
    if parsed.get("symbol") and parsed.get("entry_all"):
        books.append(book_from_template(parsed, "glm1"))
    for t in (parsed.get("dsl_templates") or []):
        if isinstance(t, dict) and t.get("symbol"):
            books.append(book_from_template(t, "glm%d" % (len(books) + 1)))

    # Engineered siblings from repair diagnosis + traps
    for sym, tf, tag, z_e, z_tp, hold in [
        ("BTC-USDT-SWAP", "15m", "a", -1.0, 1.8, 48),
        ("BTC-USDT-SWAP", "15m", "b", -1.2, 2.0, 36),
        ("BTC-USDT-SWAP", "15m", "c", -0.8, 1.5, 40),
        ("BTC-USDT-SWAP", "1h", "d", -1.0, 1.6, 16),
        ("ETH-USDT-SWAP", "15m", "a", -1.0, 1.8, 40),
        ("ETH-USDT-SWAP", "15m", "b", -1.3, 2.0, 32),
        ("ETH-USDT-SWAP", "5m", "c", -1.0, 1.8, 24),
        ("SOL-USDT-SWAP", "5m", "a", -1.0, 1.8, 20),
        ("SOL-USDT-SWAP", "15m", "b", -1.2, 2.0, 36),
        ("CL-USDT-SWAP", "15m", "a", -1.0, 1.8, 36),
        ("CL-USDT-SWAP", "5m", "b", -0.8, 1.6, 24),
        ("XAG-USDT-SWAP", "5m", "a", -1.0, 1.8, 24),
        ("DOGE-USDT-SWAP", "5m", "a", -1.0, 1.8, 20),
        ("BTC-USDT-SWAP", "5m", "e", -1.1, 1.9, 28),
    ]:
        books.append(mk_trend_pb(sym, tf, tag, z_e, z_tp, hold))

    for sym, tf, tag, rsi, z, hold in [
        ("BTC-USDT-SWAP", "15m", "s1", 48, -0.2, 24),
        ("BTC-USDT-SWAP", "1h", "s2", 45, -0.3, 14),
        ("ETH-USDT-SWAP", "15m", "s1", 48, -0.2, 24),
        ("SOL-USDT-SWAP", "15m", "s1", 50, -0.1, 20),
        ("CL-USDT-SWAP", "15m", "s1", 48, -0.2, 22),
        ("XAG-USDT-SWAP", "5m", "s1", 48, -0.2, 18),
        ("DOGE-USDT-SWAP", "15m", "s1", 48, -0.2, 20),
    ]:
        books.append(mk_breakdown_short(sym, tf, tag, rsi, z, hold))

    # de-dup / avoid
    uniq = []
    seen = set()
    for b in books:
        if f2.avoided(b["symbol"], b["timeframe"]):
            continue
        k = (b.get("dsl") or {}).get("key")
        if k in seen:
            continue
        seen.add(k)
        uniq.append(b)
    books = uniq
    print("wave2 candidates", len(books), flush=True)

    winners = []
    near = []
    for i, book in enumerate(books):
        packs = f2.quick_suite(book)
        bm = packs.get("base_metrics") or {}
        summary = {
            "key": (book.get("dsl") or {}).get("key"),
            "symbol": book["symbol"], "tf": book["timeframe"],
            "logic": book.get("logic_class"),
            "quick_pass": packs.get("quick_pass"),
            "fp": bm.get("fold_positive"), "trades": bm.get("trades"),
            "wr": bm.get("win_rate_pct"), "sharpe": bm.get("sharpe"),
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
            "failed_step": packs.get("failed_step"),
        }
        print("W2", i + 1, summary, flush=True)
        if packs.get("quick_pass"):
            winners.append({"book": book, "packs": packs, "summary": summary})
            if len(winners) >= 8:
                break
        elif int(bm.get("fold_positive") or 0) >= 7:
            near.append({"book": book, "packs": packs, "summary": summary})

    pending = []
    survivors = []
    # promote winners
    queue = list(winners)
    # try local rewrite on best near misses
    for row in near[:6]:
        nb = f2.local_structural_rewrite(row["book"], "quick_walk_forward")
        # keep as trend/breakdown: prefer param tweak of original instead
        nb = twin(row["book"], tag="nearfix", z_entry=-1.4, z_tp=2.2, hold=44)
        packs = f2.quick_suite(nb)
        if packs.get("quick_pass"):
            queue.append({"book": nb, "packs": packs,
                          "summary": {"key": (nb.get("dsl") or {}).get("key"),
                                      "quick_pass": True, "rescued_near": True}})

    for row in queue:
        book, packs = row["book"], row["packs"]
        packs = f2.full_suite(book, packs)
        print("FULL", (book.get("dsl") or {}).get("key"), packs.get("full_pass"),
              packs.get("full"), flush=True)
        if not packs.get("full_pass"):
            survivors.append({"key": (book.get("dsl") or {}).get("key"),
                              "ok": False, "failed_step": packs.get("failed_step"),
                              "symbol": book["symbol"], "tf": book["timeframe"],
                              "stage": "full"})
            continue
        sf = f2.run_sim_formal(book, packs)
        print("SIMFORMAL", (book.get("dsl") or {}).get("key"),
              sf.get("failed_step"),
              (sf.get("sim") or {}).get("wr_deepseek_sim"),
              (sf.get("sim") or {}).get("wr_qwen_sim"),
              (sf.get("formal") or {}).get("approved"),
              sf.get("pending"), flush=True)
        item = {
            "key": (book.get("dsl") or {}).get("key"),
            "ok": not sf.get("failed_step"),
            "failed_step": sf.get("failed_step"),
            "sim": sf.get("sim"),
            "formal": sf.get("formal"),
            "pending": sf.get("pending"),
            "symbol": book["symbol"],
            "tf": book["timeframe"],
            "logic": book.get("logic_class"),
            "metrics": packs.get("base_metrics"),
        }
        survivors.append(item)
        if (sf.get("pending") or {}).get("ok"):
            pending.append((sf.get("pending") or {}).get("key"))
        if len(pending) >= 2:
            break

    report = {
        "op": "寒霜贰·wave2",
        "started_at": now(),
        "candidates": len(books),
        "winners_n": len(winners),
        "near_n": len(near),
        "winner_summaries": [w["summary"] for w in winners],
        "near_summaries": [n["summary"] for n in near[:10]],
        "survivors": survivors,
        "pending_keys": pending,
        "ok": len(pending) >= 2,
        "finished_at": now(),
    }
    with open(OUT, "w") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    # update frost2_run
    try:
        run = json.load(open("/root/auto_trade/dual_engine/frost2_run.json"))
    except Exception:
        run = {"op": "寒霜贰"}
    run["wave2"] = report
    run["pending_keys"] = pending
    run["ok"] = report["ok"]
    run["finished_at"] = now()
    run["phase"] = "wave2"
    with open("/root/auto_trade/dual_engine/frost2_run.json", "w") as fh:
        json.dump(run, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(json.dumps({"ok": report["ok"], "pending": pending,
                      "winners": len(winners), "near": len(near)},
                     ensure_ascii=False, indent=2), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
