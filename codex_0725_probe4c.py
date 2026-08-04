# -*- coding: utf-8 -*-
"""Lean probe for train4c — find keepable non-ADA strategies."""
from __future__ import print_function
import copy
import json
import os
from pathlib import Path

for line in Path("/root/auto_trade/ai_ecosystem.env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

import auto_trade_human_confirm_pipeline as pipeline
import auto_trade_strategy_dsl as dsl
import auto_trade_strategy_ecosystem as eco


def metrics(trades, result):
    pnls = [float(t.get("pnl_ratio") or 0.0) for t in trades]
    n = len(pnls)
    mean = (sum(pnls) / float(n)) if n else 0.0
    wr = (sum(1 for p in pnls if p > 0) / float(n) * 100.0) if n else 0.0
    st = mx = 0
    for p in pnls:
        if p <= 0:
            st += 1
            mx = max(mx, st)
        else:
            st = 0
    fold_ok = False
    try:
        fold_ok, _, _ = pipeline._five_fold_pass(trades, min_positive=4)
    except Exception:
        pass
    return {
        "n": n, "wr": round(wr, 1), "mean": round(mean, 4),
        "st": mx, "ret": round(float(result.get("total_return_percent") or 0), 1),
        "fold": fold_ok,
    }


frames, frs = {}, {}


def get(sym, tf="5m"):
    k = (sym, tf)
    if k not in frames:
        f = pipeline._frame(sym, tf)
        if len(f) > 12000:
            f = f.iloc[-12000:]
        frames[k] = f
        frs[sym] = eco._friction_scenario(sym, "observed_base")
        print("FRAME", sym, tf, len(f), flush=True)
    return frames[k], frs[sym]


def bt(defn):
    frame, fr = get(defn["supported_instruments"][0], defn["timeframe"])
    res = dsl.backtest_dsl(
        frame, defn, leverage=20, stop_loss_pct=0.009,
        fee_rate_per_side=float(fr.get("fee_rate_per_side") or 0.0005),
        slippage_rate_per_side=float(fr.get("slippage_rate_per_side") or 0.0002),
        half_spread_rate_per_side=float(fr.get("half_spread_rate_per_side") or 0),
        impact_rate_per_side=float(fr.get("impact_rate_per_side") or 0),
        latency_rate_per_side=float(fr.get("latency_rate_per_side") or 0),
        funding_rate_per_8h=float(fr.get("funding_rate_per_8h") or 0),
        friction_scenario="observed_base",
    )
    return metrics(res.get("trades") or [], res)


def keepable(m):
    return (m["n"] >= 16 and m["wr"] >= 55 and m["mean"] > 0 and m["st"] <= 4
            and (m["fold"] or (m["n"] >= 24 and m["wr"] >= 58)))


def keepable_loose(m):
    return (m["n"] >= 14 and m["wr"] >= 54 and m["mean"] > 0 and m["st"] <= 5
            and (m["fold"] or (m["n"] >= 20 and m["wr"] >= 56)))


def ztag(z):
    return str(z).replace("-", "m").replace(".", "p")


store = json.loads(Path("/root/strategy_configs/ai_dsl_strategies.json").read_text())
base = next(s for s in store["strategies"] if s.get("key") == "ada5_z20_t60_prev_h14_0724k")

syms = [
    ("ETH-USDT-SWAP", "eth"), ("BTC-USDT-SWAP", "btc"), ("SOL-USDT-SWAP", "sol"),
    ("LTC-USDT-SWAP", "ltc"), ("DOGE-USDT-SWAP", "doge"), ("XRP-USDT-SWAP", "xrp"),
    ("BNB-USDT-SWAP", "bnb"), ("LINK-USDT-SWAP", "link"), ("OP-USDT-SWAP", "op"),
]

cands = []

for sym, tag in syms:
    for rc in (38, 42, 45):
        for z in (1.8, 2.2, 2.5):
            for hold in (12, 14, 18, 24):
                for tp in (58, 60):
                    obj = copy.deepcopy(base)
                    obj["key"] = "probe_%s_tpb_r%s_z%s_h%s_t%s" % (tag, rc, ztag(z), hold, tp)
                    obj["name"] = "%s5顺势回升·probe" % tag.upper()
                    obj["supported_instruments"] = [sym]
                    obj["max_hold_bars"] = hold
                    obj["origin"] = "probe"
                    obj["version"] = "p"
                    for c in obj["entry"]["all"]:
                        if c.get("id") == "rsi":
                            c["right"] = {"value": float(rc)}
                        if c.get("id") == "z":
                            c["right"] = {"value": float(z)}
                    for c in obj["exit"]["any"]:
                        if c.get("id") == "tp":
                            c["right"] = {"value": float(tp)}
                    cands.append(("tpb", obj))

for sym, tag in syms:
    for rc in (55, 58, 62):
        for z in (-1.8, -2.2, -2.5):
            for hold in (14, 18, 24):
                key = "probe_%s_tsh_r%s_z%s_h%s" % (tag, rc, ztag(z), hold)
                obj = {
                    "schema": "qiyu_strategy_dsl_v1", "key": key,
                    "name": "%s5顺势回落·probe" % tag.upper(),
                    "direction": "short", "timeframe": "5m",
                    "supported_instruments": [sym], "max_hold_bars": hold,
                    "description": "H1空头 RSI下穿 跌破EMA21",
                    "origin": "probe", "version": "p",
                    "entry": {"all": [
                        {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "lt",
                         "right": {"feature": "h1_ema53"}},
                        {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "lt",
                         "right": {"value": 0}},
                        {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_below",
                         "right": {"value": float(rc)}},
                        {"id": "px", "left": {"feature": "close"}, "op": "lt",
                         "right": {"feature": "ema21"}},
                        {"id": "z", "left": {"feature": "z20"}, "op": "gt",
                         "right": {"value": float(z)}},
                    ]},
                    "exit": {"any": [
                        {"id": "tp", "left": {"feature": "rsi14"}, "op": "lt",
                         "right": {"value": 40}, "role": "take_profit"},
                        {"id": "inv", "left": {"feature": "close"}, "op": "gt",
                         "right": {"feature": "prev_high20"}, "role": "invalidation"},
                    ]},
                }
                cands.append(("tsh", obj))

for sym, tag in syms:
    for jth in (8, 12, 18):
        for hold in (18, 24, 36):
            key = "probe_%s_kdj_j%s_h%s" % (tag, jth, hold)
            obj = {
                "schema": "qiyu_strategy_dsl_v1", "key": key,
                "name": "%s5 KDJ超跌反抽·probe" % tag.upper(),
                "direction": "long", "timeframe": "5m",
                "supported_instruments": [sym], "max_hold_bars": hold,
                "description": "J低位上穿K",
                "origin": "probe", "version": "p",
                "entry": {"all": [
                    {"id": "jlow", "left": {"feature": "j"}, "op": "lte",
                     "right": {"value": float(jth)}},
                    {"id": "cross", "left": {"feature": "j"}, "op": "cross_above",
                     "right": {"feature": "k"}},
                    {"id": "px", "left": {"feature": "close"}, "op": "gt",
                     "right": {"feature": "ema21"}},
                ]},
                "exit": {"any": [
                    {"id": "tp", "left": {"feature": "j"}, "op": "gte",
                     "right": {"value": 80}, "role": "take_profit"},
                    {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                     "right": {"feature": "prev_low20"}, "role": "invalidation"},
                ]},
            }
            cands.append(("kdj", obj))

for sym, tag in syms:
    for stick in (0.0, -0.0003):
        for z in (1.0, 1.3):
            for hold in (24, 36):
                key = "probe_%s_macdf_s%s_z%s_h%s" % (tag, ztag(stick), ztag(z), hold)
                obj = {
                    "schema": "qiyu_strategy_dsl_v1", "key": key,
                    "name": "%s5 MACD冲高回落·probe" % tag.upper(),
                    "direction": "short", "timeframe": "5m",
                    "supported_instruments": [sym], "max_hold_bars": hold,
                    "description": "macd_stick高位回落",
                    "origin": "probe", "version": "p",
                    "entry": {"all": [
                        {"id": "stick", "left": {"feature": "macd_stick"}, "op": "lte",
                         "right": {"value": float(stick)}},
                        {"id": "z", "left": {"feature": "z20"}, "op": "gte",
                         "right": {"value": float(z)}},
                        {"id": "bear", "left": {"feature": "close"}, "op": "lt",
                         "right": {"feature": "open"}},
                        {"id": "rsi", "left": {"feature": "rsi14"}, "op": "gte",
                         "right": {"value": 58}},
                    ]},
                    "exit": {"any": [
                        {"id": "tp", "left": {"feature": "rsi14"}, "op": "lte",
                         "right": {"value": 42}, "role": "take_profit"},
                        {"id": "inv", "left": {"feature": "close"}, "op": "gt",
                         "right": {"feature": "prev_high20"}, "role": "invalidation"},
                    ]},
                }
                cands.append(("macd", obj))

for sym, tag in syms:
    for rc in (35, 40, 45):
        for z in (1.5, 2.0, 2.5):
            for hold in (16, 24):
                key = "probe_%s_rsirec_r%s_z%s_h%s" % (tag, rc, ztag(z), hold)
                obj = {
                    "schema": "qiyu_strategy_dsl_v1", "key": key,
                    "name": "%s5 RSI反抽·probe" % tag.upper(),
                    "direction": "long", "timeframe": "5m",
                    "supported_instruments": [sym], "max_hold_bars": hold,
                    "description": "RSI上穿 + close>ema75 + z限幅",
                    "origin": "probe", "version": "p",
                    "entry": {"all": [
                        {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_above",
                         "right": {"value": float(rc)}},
                        {"id": "px", "left": {"feature": "close"}, "op": "gt",
                         "right": {"feature": "ema75"}},
                        {"id": "z", "left": {"feature": "z20"}, "op": "lt",
                         "right": {"value": float(z)}},
                        {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gt",
                         "right": {"value": -0.001}},
                    ]},
                    "exit": {"any": [
                        {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
                         "right": {"value": 62}, "role": "take_profit"},
                        {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                         "right": {"feature": "prev_low20"}, "role": "invalidation"},
                    ]},
                }
                cands.append(("rsirec", obj))

print("RAW", len(cands), flush=True)
uniq, seen = [], set()
for fam, obj in cands:
    if obj["key"] in seen:
        continue
    if (obj.get("supported_instruments") or [None])[0] == "ADA-USDT-SWAP":
        continue
    seen.add(obj["key"])
    try:
        uniq.append((fam, dsl.validate_strategy(obj)))
    except Exception:
        continue
print("VALID", len(uniq), flush=True)

scored = []
for i, (fam, defn) in enumerate(uniq):
    try:
        m = bt(defn)
    except Exception:
        continue
    score = (m["wr"] + m["mean"] * 900 - m["st"] * 4
             + min(m["n"], 200) * 0.3 + (40 if m["fold"] else 0))
    row = {"fam": fam, "key": defn["key"], "sym": defn["supported_instruments"][0],
           "m": m, "score": score, "dsl": defn}
    scored.append(row)
    if keepable(m) or keepable_loose(m):
        print("HIT", "strict" if keepable(m) else "loose", fam, defn["key"],
              defn["supported_instruments"][0], m, round(score, 1), flush=True)
    if (i + 1) % 80 == 0:
        print("progress", i + 1, "/", len(uniq), "hits",
              sum(1 for r in scored if keepable(r["m"])), flush=True)

scored.sort(key=lambda x: -x["score"])
print("=== TOP25 ===", flush=True)
for r in scored[:25]:
    print(r["fam"], r["key"], r["sym"], r["m"], round(r["score"], 1), flush=True)
strict = [r for r in scored if keepable(r["m"])]
loose = [r for r in scored if keepable_loose(r["m"])]
print("STRICT", len(strict), "LOOSE", len(loose), flush=True)
Path("/root/auto_trade/codex_0725_train4/probe4c_top.json").write_text(
    json.dumps([
        {"fam": r["fam"], "key": r["key"], "sym": r["sym"], "m": r["m"],
         "score": r["score"], "dsl": r["dsl"]}
        for r in scored[:50]
    ], ensure_ascii=False, indent=2), encoding="utf-8")
print("DONE", flush=True)
