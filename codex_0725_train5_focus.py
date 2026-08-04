# -*- coding: utf-8 -*-
"""Ultra-focused train5: ADA-winning param transplants + distinct alt logics."""
from __future__ import print_function

import json
import os
import time
from datetime import datetime
from pathlib import Path

OUT = Path("/root/auto_trade/codex_0725_train5")
OUT.mkdir(parents=True, exist_ok=True)
LOG = Path("/tmp/codex_0725_train5_focus.log")
BARS = 12000
TARGET = 3
MIN_N, MIN_WR, MIN_AI = 20, 68.0, 70.0

SYMS = [
    ("ETH-USDT-SWAP", "eth"), ("SOL-USDT-SWAP", "sol"), ("BNB-USDT-SWAP", "bnb"),
    ("LINK-USDT-SWAP", "link"), ("AVAX-USDT-SWAP", "avax"), ("SUI-USDT-SWAP", "sui"),
    ("XRP-USDT-SWAP", "xrp"), ("DOGE-USDT-SWAP", "doge"), ("DOT-USDT-SWAP", "dot"),
    ("ATOM-USDT-SWAP", "atom"), ("NEAR-USDT-SWAP", "near"), ("APT-USDT-SWAP", "apt"),
    ("OP-USDT-SWAP", "op"), ("ARB-USDT-SWAP", "arb"), ("PEPE-USDT-SWAP", "pepe"),
    ("WIF-USDT-SWAP", "wif"), ("UNI-USDT-SWAP", "uni"), ("BCH-USDT-SWAP", "bch"),
    ("XAG-USDT-SWAP", "xag"), ("LTC-USDT-SWAP", "ltc"),
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


def local_have():
    root = Path("/root/market_data")
    have = set()
    for p in root.iterdir():
        if p.is_dir() and any((p / "5m").glob("*.parquet")):
            have.add(p.name)
    return [(s, t) for s, t in SYMS if s in have]


def metrics(trades, result):
    pnls = [float(t.get("pnl_ratio") or 0.0) for t in trades]
    n = len(pnls)
    mean = (sum(pnls) / n) if n else 0.0
    wr = (sum(1 for p in pnls if p > 0) / n * 100) if n else 0.0
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
    return {"trades": n, "wr": wr, "mean": mean, "streak": mx,
            "ret": result.get("total_return_percent"), "fold_ok": fold_ok}


def trendpb(sym, tag, rc, z, hold, tp=60, ema="ema21"):
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": "codex0725t5_%s5_trendpb_%s_r%s_z%s_h%s" % (
            tag, ema, rc, ztag(z), hold),
        "name": "%s5顺势回踩·0725T5" % tag.upper(),
        "direction": "long", "timeframe": "5m",
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": (
            "%s 5m H1多头顺势回踩：h1多+slope>0+RSI上穿%s+close>%s+z<%s；"
            "RSI>=%s止盈；跌破prev_low20失效。异品种移植非ADA调参。"
            % (sym.split("-")[0], rc, ema, z, tp)),
        "origin": "codex_0725_train5", "version": "0725t5",
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


def trendsh(sym, tag, rc, z, hold, tp=40):
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": "codex0725t5_%s5_trendsh_r%s_z%s_h%s" % (tag, rc, ztag(z), hold),
        "name": "%s5顺势回落空·0725T5" % tag.upper(),
        "direction": "short", "timeframe": "5m",
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": (
            "%s 5m H1空头顺势回落：h1空+slope<0+RSI下穿%s+close<em21+z>%s；"
            "RSI<=%s止盈；上破prev_high20失效。"
            % (sym.split("-")[0], rc, z, tp)),
        "origin": "codex_0725_train5", "version": "0725t5",
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


def cci_long(sym, tag, cci_th, hold, tp=60):
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": "codex0725t5_%s5_ccirec_c%s_h%s" % (tag, ztag(cci_th), hold),
        "name": "%s5 CCI回流·0725T5" % tag.upper(),
        "direction": "long", "timeframe": "5m",
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": (
            "%s 5m CCI回流：cci上穿%s+close>ema21+h1_slope>=0；RSI>=%s止盈；"
            "跌破ema53失效。" % (sym.split("-")[0], cci_th, tp)),
        "origin": "codex_0725_train5", "version": "0725t5",
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


def emax(sym, tag, rsi_max, hold, tp=58):
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": "codex0725t5_%s5_emax_r%s_h%s" % (tag, rsi_max, hold),
        "name": "%s5 EMA金叉抽·0725T5" % tag.upper(),
        "direction": "long", "timeframe": "5m",
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": (
            "%s 5m EMA金叉抽：RSI<%s+close上穿ema17+h1多；RSI>%s止盈；"
            "跌破prev_low20失效。" % (sym.split("-")[0], rsi_max, tp)),
        "origin": "codex_0725_train5", "version": "0725t5",
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


def build(syms):
    out = []
    for sym, tag in syms:
        # ADA-proven neighborhood only (tight)
        for rc, z, hold in [
            (42, 2.0, 14), (42, 2.2, 14), (42, 2.3, 14), (42, 2.4, 14),
            (40, 2.0, 14), (40, 2.2, 12), (45, 2.0, 16), (45, 2.2, 14),
            (42, 1.8, 14), (42, 2.0, 12), (42, 2.0, 16), (42, 2.2, 16),
        ]:
            out.append(trendpb(sym, tag, rc, z, hold, 60))
            out.append(trendpb(sym, tag, rc, z, hold, 60, "ema17"))
        for rc, z, hold in [
            (60, -2.0, 14), (62, -2.0, 14), (58, -2.0, 14),
            (60, -2.2, 14), (60, -1.8, 12), (62, -2.2, 16),
        ]:
            out.append(trendsh(sym, tag, rc, z, hold, 40))
        for cci_th, hold in [(-100, 24), (-120, 16), (-80, 24)]:
            out.append(cci_long(sym, tag, cci_th, hold))
        for rsi_max, hold in [(40, 24), (45, 36), (38, 24)]:
            out.append(emax(sym, tag, rsi_max, hold))
    return out


def bt(frame, definition, fr):
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
    return metrics(result.get("trades") or [], result)


def load_frame(sym):
    cache_path = OUT / ("frame_%s_5m.pkl" % sym.replace("-", "_"))
    if cache_path.exists():
        try:
            import pickle
            with cache_path.open("rb") as f:
                fr = pickle.load(f)
            if hasattr(fr, "__len__") and len(fr) >= 5000:
                return fr
        except Exception:
            pass
    fr = pipeline._frame(sym, "5m")
    if len(fr) > BARS:
        fr = fr.iloc[-BARS:].copy()
    try:
        import pickle
        with cache_path.open("wb") as f:
            pickle.dump(fr, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass
    return fr


def main():
    syms = local_have()
    log("FOCUS_START n_sym=%d %s" % (len(syms), [t for _, t in syms]))
    cache = {}
    for sym, tag in syms:
        t0 = time.time()
        try:
            fr = load_frame(sym)
        except Exception as e:
            log("FRAME_FAIL %s %s" % (sym, e))
            continue
        cache[sym] = fr
        log("FRAME %s %d in %.1fs" % (sym, len(fr), time.time() - t0))

    raw = build([(s, t) for s, t in syms if s in cache])
    uniq, seen = [], set()
    for obj in raw:
        if obj["key"] in seen:
            continue
        seen.add(obj["key"])
        try:
            uniq.append(dsl.validate_strategy(obj))
        except Exception as e:
            log("VAL_FAIL %s %s" % (obj["key"], e))
    log("CANDS %d" % len(uniq))

    catalog = codex._existing_strategy_catalog()
    survivors = []
    for i, definition in enumerate(uniq):
        if dsl.find_near_duplicate(definition, catalog):
            continue
        sym = definition["supported_instruments"][0]
        fric = eco._friction_scenario(sym, "observed_base")
        try:
            m = bt(cache[sym], definition, fric)
        except Exception as e:
            continue
        if (m["trades"] >= MIN_N and m["wr"] >= MIN_WR and m["mean"] > 0
                and m["fold_ok"] and m["streak"] <= 4):
            survivors.append({"dsl": definition, "metrics": m})
            catalog.append(definition)
            log("HIT %s wr=%.1f n=%d mean=%.4f fold=%s" % (
                definition["key"], m["wr"], m["trades"], m["mean"], m["fold_ok"]))
        if (i + 1) % 50 == 0:
            log("prog %d/%d hits=%d" % (i + 1, len(uniq), len(survivors)))

    survivors.sort(key=lambda x: (x["metrics"]["wr"], x["metrics"]["mean"]),
                   reverse=True)
    # diversify: prefer distinct symbols/families
    picked = []
    used_sym, used_fam = set(), set()
    for row in survivors:
        key = row["dsl"]["key"]
        sym = row["dsl"]["supported_instruments"][0]
        fam = "trendpb" if "trendpb" in key else (
            "trendsh" if "trendsh" in key else (
                "cci" if "ccirec" in key else (
                    "emax" if "emax" in key else "other")))
        # allow max 1 per symbol initially for diversity
        if sym in used_sym and len(picked) < TARGET:
            continue
        picked.append(row)
        used_sym.add(sym)
        used_fam.add(fam)
        if len(picked) >= max(TARGET * 3, 9):
            break
    # fill remainder by score
    for row in survivors:
        if row in picked:
            continue
        picked.append(row)
        if len(picked) >= 15:
            break

    (OUT / "survivors_focus.json").write_text(json.dumps([
        {"key": r["dsl"]["key"], "symbol": r["dsl"]["supported_instruments"][0],
         "name": r["dsl"]["name"], "metrics": r["metrics"]}
        for r in picked
    ], ensure_ascii=False, indent=2))
    log("SURVIVORS %d picked=%d" % (len(survivors), len(picked)))

    pushed = []
    for row in picked:
        if len(pushed) >= TARGET:
            break
        definition, m = row["dsl"], row["metrics"]
        log("SUBMIT %s wr=%.1f n=%d" % (definition["key"], m["wr"], m["trades"]))
        out = codex.submit_codex_strategy(definition, meta={
            "symbol": definition["supported_instruments"][0],
            "timeframe": "5m",
            "thesis": definition.get("description"),
            "author": "codex_0725_train5_focus",
        })
        ai = out.get("ai_review") or {}
        avg = ai.get("ai_theoretical_wr_avg")
        wr_by = ai.get("ai_theoretical_wr_by_provider") or {}
        try:
            avg_f = float(avg) if avg is not None else None
        except Exception:
            avg_f = None
        if out.get("ok") and out.get("pushed"):
            if avg_f is not None and avg_f < MIN_AI:
                log("REJECT_AI %s avg=%.1f %s" % (definition["key"], avg_f, wr_by))
                try:
                    pipeline.reject(definition["key"], reason="train5_ai_wr_lt_70")
                except Exception as e:
                    log("rej_err %s" % e)
                continue
            pushed.append({
                "key": definition["key"], "name": definition["name"],
                "symbol": definition["supported_instruments"][0],
                "metrics": m, "ai_avg": avg_f, "ai_wr_by": wr_by,
            })
            log("PUSHED %s ai=%.1f %s" % (definition["key"], avg_f or -1, wr_by))
        else:
            log("FAIL %s stage=%s avg=%s reason=%s" % (
                definition["key"], out.get("stage"), avg,
                ai.get("fail_reasons") or out.get("reason")))
        time.sleep(1)

    (OUT / "pushed.json").write_text(json.dumps(pushed, ensure_ascii=False, indent=2))
    log("DONE pushed=%d" % len(pushed))
    print(json.dumps({"pushed": pushed, "survivor_count": len(survivors)},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
