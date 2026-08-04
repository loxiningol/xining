# -*- coding: utf-8 -*-
"""7.25 train5 fast path: priority symbols + proven/distinct families → 3AI."""
from __future__ import print_function

import json
import os
import time
from datetime import datetime
from pathlib import Path

OUT = Path("/root/auto_trade/codex_0725_train5")
OUT.mkdir(parents=True, exist_ok=True)
LOG = Path("/tmp/codex_0725_train5_fast.log")
AUDIT = OUT / "train_fast_audit.jsonl"
BARS = 12000
MIN_N = 20
MIN_WR = 70.0
MIN_MEAN = 0.0
TARGET_PUSH = 3
MIN_AI_AVG = 70.0

PRIORITY = [
    ("ETH-USDT-SWAP", "eth"), ("SOL-USDT-SWAP", "sol"), ("BNB-USDT-SWAP", "bnb"),
    ("LINK-USDT-SWAP", "link"), ("AVAX-USDT-SWAP", "avax"), ("SUI-USDT-SWAP", "sui"),
    ("XRP-USDT-SWAP", "xrp"), ("DOGE-USDT-SWAP", "doge"), ("LTC-USDT-SWAP", "ltc"),
    ("DOT-USDT-SWAP", "dot"), ("ATOM-USDT-SWAP", "atom"), ("NEAR-USDT-SWAP", "near"),
    ("XAG-USDT-SWAP", "xag"),
]

for line in Path("/root/auto_trade/ai_ecosystem.env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

import auto_trade_codex_strategy_review as codex
import auto_trade_human_confirm_pipeline as pipeline
import auto_trade_strategy_dsl as dsl
import auto_trade_strategy_ecosystem as eco


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ztag(z):
    return str(z).replace("-", "m").replace(".", "p")


def log(msg):
    line = "[%s] %s" % (now(), msg)
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def audit(row):
    row = dict(row)
    row["time"] = now()
    with AUDIT.open("a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


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
        "trades": n, "wr": wr, "mean": mean, "streak": mx,
        "ret": result.get("total_return_percent"), "fold_ok": fold_ok,
    }


def soft_pass(m):
    return (m["trades"] >= MIN_N and m["wr"] >= MIN_WR
            and m["mean"] > MIN_MEAN and m["fold_ok"] and m["streak"] <= 4)


def local_syms():
    root = Path("/root/market_data")
    have = set()
    for p in root.iterdir():
        if p.is_dir() and (p / "5m").is_dir() and any((p / "5m").glob("*.parquet")):
            have.add(p.name)
    return [(s, t) for s, t in PRIORITY if s in have]


def trendpb(sym, tag, rc, z, hold, tp, ema="ema21"):
    key = "codex0725t5_%s5_trendpb_%s_r%s_z%s_h%s" % (tag, ema, rc, ztag(z), hold)
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key,
        "name": "%s5顺势回踩·0725T5" % tag.upper(),
        "direction": "long", "timeframe": "5m",
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": (
            "%s 5m H1多头顺势回踩：h1多+slope>0+RSI上穿%s+close>%s+z<%s；"
            "RSI>=%s止盈；跌破prev_low20失效。异品种移植。"
            % (sym.split("-")[0], rc, ema, z, tp)),
        "origin": "codex_0725_train5", "version": "0725t5f",
        "entry": {"all": [
            {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "gt",
             "right": {"feature": "h1_ema53"}},
            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gt",
             "right": {"value": 0}},
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_above",
             "right": {"value": float(rc)}},
            {"id": "px", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": ema}},
            {"id": "z", "left": {"feature": "z20"}, "op": "lt",
             "right": {"value": float(z)}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
             "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "prev_low20"}, "role": "invalidation"},
        ]},
    }


def trendsh(sym, tag, rc, z, hold, tp):
    key = "codex0725t5_%s5_trendsh_r%s_z%s_h%s" % (tag, rc, ztag(z), hold)
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key,
        "name": "%s5顺势回落空·0725T5" % tag.upper(),
        "direction": "short", "timeframe": "5m",
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": (
            "%s 5m H1空头顺势：h1空+slope<0+RSI下穿%s+close<em21+z>%s；"
            "RSI<=%s止盈；上破prev_high20失效。"
            % (sym.split("-")[0], rc, z, tp)),
        "origin": "codex_0725_train5", "version": "0725t5f",
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
             "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
    }


def cci_rec(sym, tag, cci_th, hold, tp):
    key = "codex0725t5_%s5_ccirec_c%s_h%s_t%s" % (tag, ztag(cci_th), hold, tp)
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key,
        "name": "%s5 CCI回流·0725T5" % tag.upper(),
        "direction": "long", "timeframe": "5m",
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": (
            "%s 5m CCI回流：cci上穿%s+close>ema21+h1_slope>=0；RSI>=%s止盈；"
            "跌破ema53失效。" % (sym.split("-")[0], cci_th, tp)),
        "origin": "codex_0725_train5", "version": "0725t5f",
        "entry": {"all": [
            {"id": "cci", "left": {"feature": "cci"}, "op": "cross_above",
             "right": {"value": float(cci_th)}},
            {"id": "px", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "ema21"}},
            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gte",
             "right": {"value": 0.0}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "gte",
             "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "ema53"}, "role": "invalidation"},
        ]},
    }


def emax_long(sym, tag, rsi_max, hold, tp):
    key = "codex0725t5_%s5_emax_r%s_h%s_t%s" % (tag, rsi_max, hold, tp)
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key,
        "name": "%s5 EMA金叉抽·0725T5" % tag.upper(),
        "direction": "long", "timeframe": "5m",
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": (
            "%s 5m EMA金叉抽：RSI<%s+close上穿ema17+h1多；RSI>%s止盈；"
            "跌破prev_low20失效。" % (sym.split("-")[0], rsi_max, tp)),
        "origin": "codex_0725_train5", "version": "0725t5f",
        "entry": {"all": [
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "lt",
             "right": {"value": float(rsi_max)}},
            {"id": "cross", "left": {"feature": "close"}, "op": "cross_above",
             "right": {"feature": "ema17"}},
            {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "gt",
             "right": {"feature": "h1_ema53"}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
             "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "prev_low20"}, "role": "invalidation"},
        ]},
    }


def jfade(sym, tag, j_th, hold, tp):
    key = "codex0725t5_%s5_jfade_j%s_h%s_t%s" % (tag, int(j_th), hold, tp)
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key,
        "name": "%s5 J超买回落·0725T5" % tag.upper(),
        "direction": "short", "timeframe": "5m",
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": (
            "%s 5m J超买回落：h1空+j下穿%s+close<em21；RSI<=%s止盈；"
            "上破prev_high20失效。" % (sym.split("-")[0], j_th, tp)),
        "origin": "codex_0725_train5", "version": "0725t5f",
        "entry": {"all": [
            {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "lt",
             "right": {"feature": "h1_ema53"}},
            {"id": "j", "left": {"feature": "j"}, "op": "cross_below",
             "right": {"value": float(j_th)}},
            {"id": "px", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "ema21"}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "lte",
             "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
    }


def build(syms):
    out = []
    for sym, tag in syms:
        for rc in (40, 42, 45):
            for z in (1.8, 2.0, 2.2, 2.4):
                for hold in (12, 14, 16):
                    out.append(trendpb(sym, tag, rc, z, hold, 60 if rc <= 42 else 62))
                    out.append(trendpb(sym, tag, rc, z, hold, 60 if rc <= 42 else 62, "ema17"))
        for rc in (58, 60, 62):
            for z in (-1.8, -2.0, -2.2):
                for hold in (12, 14, 16):
                    out.append(trendsh(sym, tag, rc, z, hold, 40))
        for cci_th in (-100, -120):
            for hold in (16, 24):
                out.append(cci_rec(sym, tag, cci_th, hold, 60))
        for rsi_max in (40, 45):
            for hold in (24, 36):
                out.append(emax_long(sym, tag, rsi_max, hold, 58))
        for j_th in (85, 90):
            out.append(jfade(sym, tag, j_th, 14, 38))
    return out


def main():
    syms = local_syms()
    log("FAST_START local=%s" % [t for _, t in syms])
    # Preload frames
    cache = {}
    for sym, tag in syms:
        try:
            fr = pipeline._frame(sym, "5m")
            if len(fr) > BARS:
                fr = fr.iloc[-BARS:]
            cache[sym] = fr
            log("FRAME %s %d" % (sym, len(fr)))
        except Exception as e:
            log("FRAME_FAIL %s %s" % (sym, e))
    raw = build([(s, t) for s, t in syms if s in cache])
    uniq = []
    seen = set()
    for obj in raw:
        if obj["key"] in seen:
            continue
        seen.add(obj["key"])
        try:
            uniq.append(dsl.validate_strategy(obj))
        except Exception:
            continue
    log("CANDS %d" % len(uniq))
    catalog = codex._existing_strategy_catalog()
    survivors = []
    for i, definition in enumerate(uniq):
        if dsl.find_near_duplicate(definition, catalog):
            continue
        sym = definition["supported_instruments"][0]
        frame = cache[sym]
        fr = eco._friction_scenario(sym, "observed_base")
        try:
            result = dsl.backtest_dsl(
                frame, definition,
                leverage=pipeline.LEVERAGE,
                stop_loss_pct=pipeline.STOP_LOSS_PCT,
                fee_rate_per_side=float(fr.get("fee_rate_per_side") or 0.0005),
                slippage_rate_per_side=float(fr.get("slippage_rate_per_side") or 0.0002),
                half_spread_rate_per_side=float(fr.get("half_spread_rate_per_side") or 0),
                impact_rate_per_side=float(fr.get("impact_rate_per_side") or 0),
                latency_rate_per_side=float(fr.get("latency_rate_per_side") or 0),
                funding_rate_per_8h=float(fr.get("funding_rate_per_8h") or 0),
                friction_scenario="observed_base",
            )
        except Exception:
            continue
        m = metrics(result.get("trades") or [], result)
        if not soft_pass(m):
            continue
        survivors.append({"dsl": definition, "metrics": m})
        catalog.append(definition)
        log("HIT %s wr=%.1f n=%d mean=%.4f" % (
            definition["key"], m["wr"], m["trades"], m["mean"]))
        if len(survivors) >= 30:
            break
        if (i + 1) % 150 == 0:
            log("prog %d hits=%d" % (i + 1, len(survivors)))

    survivors.sort(key=lambda x: (x["metrics"]["wr"], x["metrics"]["mean"]),
                   reverse=True)
    (OUT / "survivors_fast.json").write_text(
        json.dumps([{
            "key": r["dsl"]["key"], "name": r["dsl"]["name"],
            "symbol": r["dsl"]["supported_instruments"][0],
            "metrics": r["metrics"],
        } for r in survivors], ensure_ascii=False, indent=2))
    log("SURVIVORS %d" % len(survivors))

    pushed = []
    for row in survivors:
        if len(pushed) >= TARGET_PUSH:
            break
        definition = row["dsl"]
        m = row["metrics"]
        log("SUBMIT %s wr=%.1f n=%d" % (definition["key"], m["wr"], m["trades"]))
        out = codex.submit_codex_strategy(definition, meta={
            "symbol": definition["supported_instruments"][0],
            "timeframe": "5m",
            "thesis": definition.get("description"),
            "author": "codex_0725_train5_fast",
        })
        ai = out.get("ai_review") or {}
        avg = ai.get("ai_theoretical_wr_avg")
        wr_by = ai.get("ai_theoretical_wr_by_provider") or {}
        audit({"event": "submit", "key": definition["key"], "ok": out.get("ok"),
               "pushed": out.get("pushed"), "avg": avg, "wr_by": wr_by,
               "metrics": m, "fail": ai.get("fail_reasons")})
        if out.get("ok") and out.get("pushed"):
            try:
                avg_f = float(avg) if avg is not None else None
            except Exception:
                avg_f = None
            if avg_f is not None and avg_f < MIN_AI_AVG:
                log("REJECT_LOW_AI %s avg=%.1f" % (definition["key"], avg_f))
                try:
                    pipeline.reject(definition["key"],
                                    reason="train5_ai_wr_below_70")
                except Exception as e:
                    log("reject_err %s" % e)
                continue
            pushed.append({
                "key": definition["key"], "name": definition["name"],
                "symbol": definition["supported_instruments"][0],
                "metrics": m, "ai_avg": avg_f, "ai_wr_by": wr_by,
            })
            log("PUSHED %s ai=%.1f %s" % (definition["key"], avg_f or -1, wr_by))
        else:
            log("FAIL %s stage=%s avg=%s reasons=%s" % (
                definition["key"], out.get("stage"), avg,
                (ai.get("fail_reasons") or out.get("reason"))[:2]
                if isinstance(ai.get("fail_reasons") or out.get("reason"), list)
                else (ai.get("fail_reasons") or out.get("reason"))))
        time.sleep(1.0)

    (OUT / "pushed_fast.json").write_text(
        json.dumps(pushed, ensure_ascii=False, indent=2))
    log("DONE pushed=%d" % len(pushed))
    print(json.dumps(pushed, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
