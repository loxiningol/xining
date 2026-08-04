# -*- coding: utf-8 -*-
"""7.25 train5: expanded-universe screen → soft gate → 3AI (≥70% WR target)."""
from __future__ import print_function

import json
import os
import time
from datetime import datetime
from pathlib import Path

OUT = Path("/root/auto_trade/codex_0725_train5")
OUT.mkdir(parents=True, exist_ok=True)
LOG = Path("/tmp/codex_0725_train5.log")
AUDIT = OUT / "train_audit.jsonl"
BARS = 12000
MIN_N = 20
MIN_WR = 68.0
MIN_MEAN = 0.0
TARGET_PUSH = 3
MIN_AI_AVG = 70.0
SOFT_AI_FLOOR = 65.0

# Ban ADA trendpb family param tweaks; allow other symbols/logics.
BAN_TOPO_ON = {"ADA-USDT-SWAP"}  # skip trendpb-like on ADA only via family filter

ALL_SYMS = [
    ("ETH-USDT-SWAP", "eth"), ("SOL-USDT-SWAP", "sol"), ("BNB-USDT-SWAP", "bnb"),
    ("XRP-USDT-SWAP", "xrp"), ("DOGE-USDT-SWAP", "doge"), ("LTC-USDT-SWAP", "ltc"),
    ("LINK-USDT-SWAP", "link"), ("AVAX-USDT-SWAP", "avax"), ("SUI-USDT-SWAP", "sui"),
    ("DOT-USDT-SWAP", "dot"), ("ATOM-USDT-SWAP", "atom"), ("NEAR-USDT-SWAP", "near"),
    ("APT-USDT-SWAP", "apt"), ("OP-USDT-SWAP", "op"), ("ARB-USDT-SWAP", "arb"),
    ("UNI-USDT-SWAP", "uni"), ("AAVE-USDT-SWAP", "aave"), ("BCH-USDT-SWAP", "bch"),
    ("TRX-USDT-SWAP", "trx"), ("PEPE-USDT-SWAP", "pepe"), ("WIF-USDT-SWAP", "wif"),
    ("XAG-USDT-SWAP", "xag"), ("XAU-USDT-SWAP", "xau"), ("CL-USDT-SWAP", "cl"),
    ("NG-USDT-SWAP", "ng"), ("BTC-USDT-SWAP", "btc"),
]


def local_5m_symbols():
    """Only symbols with local parquet — avoid per-cand OKX download stalls."""
    root = Path("/root/market_data")
    have = set()
    if root.is_dir():
        for p in root.iterdir():
            if not p.is_dir():
                continue
            d = p / "5m"
            if d.is_dir() and any(d.glob("*.parquet")):
                have.add(p.name)
    return [(s, t) for s, t in ALL_SYMS if s in have]

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


def three_ok(ai_rev):
    by = {r.get("provider"): r for r in (ai_rev.get("reviews") or [])}
    for p in ("deepseek", "qwen", "glm"):
        r = by.get(p) or {}
        if not r.get("ok") or str(r.get("decision") or "").upper() != "APPROVE":
            return False
    return bool(ai_rev.get("approved"))


def ai_avg_wr(ai_rev):
    by = {r.get("provider"): r for r in (ai_rev.get("reviews") or [])}
    vals = []
    for p in ("deepseek", "qwen", "glm"):
        r = by.get(p) or {}
        for k in ("theoretical_win_rate", "win_rate", "estimated_win_rate"):
            if r.get(k) is not None:
                try:
                    vals.append(float(r.get(k)))
                    break
                except Exception:
                    pass
    if not vals and ai_rev.get("avg_theoretical_win_rate") is not None:
        try:
            return float(ai_rev.get("avg_theoretical_win_rate"))
        except Exception:
            return None
    return (sum(vals) / len(vals)) if vals else None


# ---------- strategy families (distinct topologies) ----------

def fam_trendpb_long(sym, tag, rc, z, hold, tp, ema="ema21"):
    """H1 trend pullback long — transplant OK on non-ADA."""
    key = "codex0725t5_%s5_trendpb_%s_r%s_z%s_h%s" % (
        tag, ema, rc, ztag(z), hold)
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key,
        "name": "%s5顺势回踩·0725T5" % tag.upper(),
        "direction": "long", "timeframe": "5m",
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": (
            "%s 5m H1多头顺势回踩：h1多+slope>0+RSI上穿%s+close>%s+z<%s；"
            "RSI>=%s止盈；跌破prev_low20失效。异品种移植，非ADA调参。"
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


def fam_trendsh_short(sym, tag, rc, z, hold, tp, ema="ema21"):
    """H1 trend continuation short — mirror topology."""
    key = "codex0725t5_%s5_trendsh_%s_r%s_z%s_h%s" % (
        tag, ema, rc, ztag(z), hold)
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key,
        "name": "%s5顺势回落空·0725T5" % tag.upper(),
        "direction": "short", "timeframe": "5m",
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": (
            "%s 5m H1空头顺势：h1空+slope<0+RSI下穿%s+close<%s+z>%s；"
            "RSI<=%s止盈；上破prev_high20失效。"
            % (sym.split("-")[0], rc, ema, z, tp)),
        "origin": "codex_0725_train5", "version": "0725t5",
        "entry": {"all": [
            {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "lt",
             "right": {"feature": "h1_ema53"}},
            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "lt",
             "right": {"value": 0}},
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_below",
             "right": {"value": float(rc)}},
            {"id": "px", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": ema}},
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


def fam_cci_reclaim_long(sym, tag, cci_th, hold, tp_rsi):
    """CCI deep washout reclaim — distinct from trendpb."""
    key = "codex0725t5_%s5_ccirec_c%s_h%s_t%s" % (tag, ztag(cci_th), hold, tp_rsi)
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key,
        "name": "%s5 CCI回流·0725T5" % tag.upper(),
        "direction": "long", "timeframe": "5m",
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": (
            "%s 5m CCI回流：cci上穿%s + close>ema21 + h1_slope>=0；"
            "RSI>=%s止盈；跌破ema53失效。异于trendpb拓扑。"
            % (sym.split("-")[0], cci_th, tp_rsi)),
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
             "right": {"value": float(tp_rsi)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "ema53"}, "role": "invalidation"},
        ]},
    }


def fam_zaccel_short(sym, tag, z, rsi_max, hold, tp):
    """Z20 acceleration breakdown short under H1 downtrend."""
    key = "codex0725t5_%s5_zaccel_z%s_r%s_h%s" % (tag, ztag(z), rsi_max, hold)
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key,
        "name": "%s5 Z加速破位空·0725T5" % tag.upper(),
        "direction": "short", "timeframe": "5m",
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": (
            "%s 5m Z加速破位：h1空+z20<=%s+rsi<%s+close<em21；"
            "RSI<=%s止盈；上破ema32失效。"
            % (sym.split("-")[0], z, rsi_max, tp)),
        "origin": "codex_0725_train5", "version": "0725t5",
        "entry": {"all": [
            {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "lt",
             "right": {"feature": "h1_ema53"}},
            {"id": "z", "left": {"feature": "z20"}, "op": "lte",
             "right": {"value": float(z)}},
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "lt",
             "right": {"value": float(rsi_max)}},
            {"id": "px", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "ema21"}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "lte",
             "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "ema32"}, "role": "invalidation"},
        ]},
    }


def fam_ema_recross_long(sym, tag, rsi_max, hold, tp):
    """Soft RSI + close cross above ema17 + H1 not strongly down."""
    key = "codex0725t5_%s5_emax_r%s_h%s_t%s" % (tag, rsi_max, hold, tp)
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key,
        "name": "%s5 EMA金叉抽·0725T5" % tag.upper(),
        "direction": "long", "timeframe": "5m",
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": (
            "%s 5m EMA金叉抽：RSI<%s + close上穿ema17 + h1_ema19>h1_ema53；"
            "RSI>%s止盈；跌破prev_low20失效。异于mass_hf/trendpb。"
            % (sym.split("-")[0], rsi_max, tp)),
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


def fam_kdj_fade_short(sym, tag, j_th, hold, tp):
    """Overbought J fade under H1 down — distinct KDJ topology."""
    key = "codex0725t5_%s5_jfade_j%s_h%s_t%s" % (tag, int(j_th), hold, tp)
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key,
        "name": "%s5 J超买回落·0725T5" % tag.upper(),
        "direction": "short", "timeframe": "5m",
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": (
            "%s 5m J超买回落：h1空 + j下穿%s + close<em21；"
            "RSI<=%s止盈；上破prev_high20失效。"
            % (sym.split("-")[0], j_th, tp)),
        "origin": "codex_0725_train5", "version": "0725t5",
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


def build_candidates(syms):
    out = []
    # Priority liquid names for trendpb transplant (proven family, new symbols)
    trend_syms = list(syms)
    for sym, tag in trend_syms:
        for rc in (40, 42, 45):
            for z in (1.8, 2.0, 2.2, 2.4):
                for hold in (12, 14, 16):
                    for ema in ("ema21", "ema17"):
                        out.append(fam_trendpb_long(
                            sym, tag, rc, z, hold, 60 if rc <= 42 else 62, ema))
        for rc in (58, 60, 62):
            for z in (-1.8, -2.0, -2.2):
                for hold in (12, 14, 16):
                    out.append(fam_trendsh_short(
                        sym, tag, rc, z, hold, 40 if rc >= 60 else 42))

    # Distinct families across broader universe
    for sym, tag in syms:
        for cci_th in (-100, -120, -80):
            for hold in (16, 24, 36):
                for tp in (58, 62):
                    out.append(fam_cci_reclaim_long(sym, tag, cci_th, hold, tp))
        for z in (-1.2, -1.5, -1.8):
            for rsi_max in (45, 48):
                for hold in (12, 18):
                    out.append(fam_zaccel_short(sym, tag, z, rsi_max, hold, 35))
        for rsi_max in (38, 42, 45):
            for hold in (24, 36):
                for tp in (55, 58):
                    out.append(fam_ema_recross_long(sym, tag, rsi_max, hold, tp))
        for j_th in (80, 85, 90):
            for hold in (12, 18):
                out.append(fam_kdj_fade_short(sym, tag, j_th, hold, 38))
    return out


def soft_pass(m):
    if m["trades"] < MIN_N:
        return False
    if m["wr"] < MIN_WR:
        return False
    if m["mean"] <= MIN_MEAN:
        return False
    if not m["fold_ok"]:
        return False
    if m["streak"] > 4:
        return False
    return True


def backtest_one(definition):
    sym = (definition.get("supported_instruments") or [None])[0]
    tf = definition.get("timeframe") or "5m"
    try:
        frame = pipeline._frame(sym, tf)
    except Exception as exc:
        return None, "frame:%s" % exc
    if hasattr(frame, "iloc") and len(frame) > BARS:
        frame = frame.iloc[-BARS:]
    if len(frame) < 2000:
        return None, "short_frame:%d" % len(frame)
    try:
        fr = eco._friction_scenario(sym, "observed_base")
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
    except Exception as exc:
        return None, "bt:%s" % exc
    trades = result.get("trades") or []
    return metrics(trades, result), None


def main():
    syms = local_5m_symbols()
    log("TRAIN5_START instruments=%d local5m=%d %s" % (
        len(dsl.INSTRUMENTS), len(syms), [t for _, t in syms]))
    if not syms:
        log("NO_LOCAL_DATA")
        return
    raw = build_candidates(syms)
    seen = set()
    uniq = []
    for obj in raw:
        if obj["key"] in seen:
            continue
        seen.add(obj["key"])
        try:
            uniq.append(dsl.validate_strategy(obj))
        except Exception:
            continue
    log("CANDS %d" % len(uniq))

    # Preload frames once per symbol for speed
    frame_cache = {}
    for sym, _tag in syms:
        try:
            fr = pipeline._frame(sym, "5m")
            if hasattr(fr, "iloc") and len(fr) > BARS:
                fr = fr.iloc[-BARS:]
            frame_cache[sym] = fr
            log("FRAME %s n=%d" % (sym, len(fr)))
        except Exception as exc:
            log("FRAME_FAIL %s %s" % (sym, exc))

    catalog = codex._existing_strategy_catalog()
    survivors = []
    frame_fail = 0
    soft_fail = 0
    dup_fail = 0

    def backtest_cached(definition):
        sym = (definition.get("supported_instruments") or [None])[0]
        frame = frame_cache.get(sym)
        if frame is None:
            return None, "no_frame"
        try:
            fr = eco._friction_scenario(sym, "observed_base")
            result = dsl.backtest_dsl(
                frame, definition,
                leverage=pipeline.LEVERAGE,
                stop_loss_pct=pipeline.STOP_LOSS_PCT,
                fee_rate_per_side=float(fr.get("fee_rate_per_side") or 0.0005),
                slippage_rate_per_side=float(fr.get("slippage_rate_per_side") or 0.0002),
                half_spread_rate_per_side=float(
                    fr.get("half_spread_rate_per_side") or 0),
                impact_rate_per_side=float(fr.get("impact_rate_per_side") or 0),
                latency_rate_per_side=float(fr.get("latency_rate_per_side") or 0),
                funding_rate_per_8h=float(fr.get("funding_rate_per_8h") or 0),
                friction_scenario="observed_base",
            )
        except Exception as exc:
            return None, "bt:%s" % exc
        return metrics(result.get("trades") or [], result), None

    for i, definition in enumerate(uniq):
        hit = dsl.find_near_duplicate(definition, catalog)
        if hit:
            dup_fail += 1
            continue
        m, err = backtest_cached(definition)
        if err:
            frame_fail += 1
            continue
        if not soft_pass(m):
            soft_fail += 1
            continue
        survivors.append({"dsl": definition, "metrics": m})
        catalog.append(definition)  # prevent intra-batch near-dups
        if len(survivors) >= 80:
            break
        if (i + 1) % 200 == 0:
            log("progress i=%d survivors=%d frame_fail=%d soft_fail=%d dup=%d"
                % (i + 1, len(survivors), frame_fail, soft_fail, dup_fail))

    survivors.sort(
        key=lambda x: (x["metrics"]["wr"], x["metrics"]["mean"],
                       x["metrics"]["trades"]),
        reverse=True)
    (OUT / "survivors.json").write_text(
        json.dumps(survivors[:40], ensure_ascii=False, indent=2))
    log("SURVIVORS %d (top wr=%.1f)" % (
        len(survivors),
        survivors[0]["metrics"]["wr"] if survivors else 0))

    pushed = []
    for row in survivors:
        if len(pushed) >= TARGET_PUSH:
            break
        definition = row["dsl"]
        m = row["metrics"]
        # Prefer high empirical WR for AI ~70%
        if m["wr"] < 68.0 and len(pushed) == 0:
            # still try best ones
            pass
        log("SUBMIT %s wr=%.1f n=%d mean=%.4f" % (
            definition["key"], m["wr"], m["trades"], m["mean"]))
        try:
            out = codex.submit_codex_strategy(
                definition,
                meta={
                    "symbol": definition["supported_instruments"][0],
                    "timeframe": definition["timeframe"],
                    "thesis": definition.get("description"),
                    "author": "codex_0725_train5",
                    "notes": "train5 expanded-universe screen; soft n>=20 WR>=62 fold_ok",
                },
            )
        except Exception as exc:
            audit({"event": "submit_exc", "key": definition["key"],
                   "error": str(exc)[:300]})
            log("SUBMIT_EXC %s %s" % (definition["key"], exc))
            continue
        audit({"event": "submit", "key": definition["key"], "out": {
            k: out.get(k) for k in (
                "ok", "stage", "reason", "status", "key", "error")
            if k in out
        }, "metrics": m})
        ai = out.get("ai_review") or {}
        avg = ai.get("ai_theoretical_wr_avg")
        if avg is None:
            avg = ai_avg_wr(ai)
        try:
            avg = float(avg) if avg is not None else None
        except Exception:
            avg = None
        wr_by = ai.get("ai_theoretical_wr_by_provider") or {}
        is_pushed = bool(out.get("ok") and out.get("pushed"))
        if is_pushed:
            if avg is not None and avg < SOFT_AI_FLOOR:
                log("SOFT_REJECT_LOW_AI %s avg=%.1f — rejecting" % (
                    definition["key"], avg))
                try:
                    pipeline.reject(
                        definition["key"],
                        reason="train5_ai_wr_below_65_soft_reject")
                except Exception as exc:
                    log("reject_fail %s" % exc)
                continue
            if avg is not None and avg < MIN_AI_AVG:
                log("WARN_AI_BELOW_70 %s avg=%.1f wr_by=%s (kept >=65)" % (
                    definition["key"], avg, wr_by))
                # Quality bar: do not keep mid-60s junk; reject and keep searching
                try:
                    pipeline.reject(
                        definition["key"],
                        reason="train5_ai_wr_below_70_quality_bar")
                except Exception as exc:
                    log("reject_fail %s" % exc)
                continue
            pushed.append({
                "key": definition["key"],
                "name": definition.get("name"),
                "symbol": definition["supported_instruments"][0],
                "metrics": m,
                "ai_avg": avg,
                "ai_wr_by": wr_by,
                "out": {"ok": out.get("ok"), "pushed": out.get("pushed"),
                        "key": out.get("key")},
            })
            log("PUSHED %s ai_avg=%s wr_by=%s" % (
                definition["key"], avg, wr_by))
        else:
            log("NOT_PUSHED %s stage=%s reason=%s avg=%s fail=%s" % (
                definition["key"], out.get("stage"),
                out.get("reason") or (ai.get("fail_reasons")),
                avg, (ai.get("fail_reasons") or [])[:2]))
        time.sleep(1.5)

    (OUT / "pushed.json").write_text(
        json.dumps(pushed, ensure_ascii=False, indent=2))
    log("DONE pushed=%d / target=%d" % (len(pushed), TARGET_PUSH))
    print(json.dumps({"pushed": len(pushed), "items": pushed},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
