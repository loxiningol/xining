# -*- coding: utf-8 -*-
"""7.25 train4e: rank-by-score submit (safety gates retired; 3AI is the gate).
Focus non-ADA supported universe; prefer novel families; submit best evidence.
"""
from __future__ import print_function

import json
import os
import time
from datetime import datetime
from pathlib import Path

OUT = Path("/root/auto_trade/codex_0725_train4")
OUT.mkdir(parents=True, exist_ok=True)
BARS = 12000
BAN = {"ADA-USDT-SWAP"}
SYMS = [
    ("XAU-USDT-SWAP", "xau"), ("NG-USDT-SWAP", "ng"),
    ("CL-USDT-SWAP", "cl"), ("XAG-USDT-SWAP", "xag"),
    ("LTC-USDT-SWAP", "ltc"), ("BNB-USDT-SWAP", "bnb"),
    ("BTC-USDT-SWAP", "btc"), ("ETH-USDT-SWAP", "eth"),
    ("SOL-USDT-SWAP", "sol"), ("XRP-USDT-SWAP", "xrp"),
    ("DOGE-USDT-SWAP", "doge"),
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
    return {"trades": n, "wr": wr, "mean": mean, "streak": mx,
            "ret": result.get("total_return_percent"), "fold_ok": fold_ok}


def three_ok(ai_rev):
    by = {r.get("provider"): r for r in (ai_rev.get("reviews") or [])}
    for p in ("deepseek", "qwen", "glm"):
        r = by.get(p) or {}
        if not r.get("ok") or str(r.get("decision") or "").upper() != "APPROVE":
            return False
    return bool(ai_rev.get("approved"))


def build():
    out = []
    # Family 1: KDJ J reclaim + ema21 (novel vs ADA trendpb)
    for sym, tag in SYMS:
        for jth in (5, 10, 15, 20):
            for hold in (18, 28, 40):
                for tpj in (75, 85):
                    key = "codex0725t4_%s5_kdjrec_j%s_h%s_t%s" % (
                        tag, jth, hold, tpj)
                    out.append({
                        "schema": "qiyu_strategy_dsl_v1", "key": key,
                        "name": "%s5 KDJ超跌反抽·0725T4" % tag.upper(),
                        "direction": "long", "timeframe": "5m",
                        "supported_instruments": [sym], "max_hold_bars": hold,
                        "description": (
                            "%s 5m KDJ超跌反抽：J<=%s 且 J上穿K + close>ema21；"
                            "J>=%s止盈；跌破prev_low20失效。非ADA趋势族。"
                            % (sym.split("-")[0], jth, tpj)),
                        "origin": "codex_0725_train4", "version": "0725t4e",
                        "entry": {"all": [
                            {"id": "jlow", "left": {"feature": "j"}, "op": "lte",
                             "right": {"value": float(jth)}},
                            {"id": "cross", "left": {"feature": "j"},
                             "op": "cross_above", "right": {"feature": "k"}},
                            {"id": "px", "left": {"feature": "close"}, "op": "gt",
                             "right": {"feature": "ema21"}},
                        ]},
                        "exit": {"any": [
                            {"id": "tp", "left": {"feature": "j"}, "op": "gte",
                             "right": {"value": float(tpj)}, "role": "take_profit"},
                            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                             "right": {"feature": "prev_low20"},
                             "role": "invalidation"},
                        ]},
                    })

    # Family 2: 15m breakdown short (commodity-friendly)
    for sym, tag in SYMS:
        for z in (-1.3, -1.6, -2.0):
            for slope in (-0.0004, -0.0007):
                for hold in (12, 16, 24):
                    key = "codex0725t4_%s15_bdsh_z%s_s%s_h%s" % (
                        tag, ztag(z), ztag(slope), hold)
                    out.append({
                        "schema": "qiyu_strategy_dsl_v1", "key": key,
                        "name": "%s15 破位做空·0725T4" % tag.upper(),
                        "direction": "short", "timeframe": "15m",
                        "supported_instruments": [sym], "max_hold_bars": hold,
                        "description": (
                            "%s 15m 破位做空：h1_slope<%s + close<ema53 + z<%s + "
                            "close下穿ema17；CCI上穿-50止盈；上穿ema53失效。"
                            % (sym.split("-")[0], slope, z)),
                        "origin": "codex_0725_train4", "version": "0725t4e",
                        "entry": {"all": [
                            {"id": "slope", "left": {"feature": "h1_slope4"},
                             "op": "lt", "right": {"value": float(slope)}},
                            {"id": "px", "left": {"feature": "close"}, "op": "lt",
                             "right": {"feature": "ema53"}},
                            {"id": "z", "left": {"feature": "z20"}, "op": "lt",
                             "right": {"value": float(z)}},
                            {"id": "cross", "left": {"feature": "close"},
                             "op": "cross_below", "right": {"feature": "ema17"}},
                        ]},
                        "exit": {"any": [
                            {"id": "tp", "left": {"feature": "cci"},
                             "op": "cross_above", "right": {"value": -50.0},
                             "role": "take_profit"},
                            {"id": "inv", "left": {"feature": "close"},
                             "op": "cross_above", "right": {"feature": "ema53"},
                             "role": "invalidation"},
                        ]},
                    })

    # Family 3: RSI fade short — RSI high + bear bar + z high (not trendpb)
    for sym, tag in SYMS:
        for rsi in (68, 72, 76):
            for z in (1.2, 1.5, 1.8):
                for hold in (18, 28, 40):
                    key = "codex0725t4_%s5_rsifade_r%s_z%s_h%s" % (
                        tag, rsi, ztag(z), hold)
                    out.append({
                        "schema": "qiyu_strategy_dsl_v1", "key": key,
                        "name": "%s5 RSI冲高回落·0725T4" % tag.upper(),
                        "direction": "short", "timeframe": "5m",
                        "supported_instruments": [sym], "max_hold_bars": hold,
                        "description": (
                            "%s 5m RSI冲高回落：RSI>=%s + z20>=%s + 阴线；"
                            "RSI<=45止盈；上破prev_high20失效。"
                            % (sym.split("-")[0], rsi, z)),
                        "origin": "codex_0725_train4", "version": "0725t4e",
                        "entry": {"all": [
                            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "gte",
                             "right": {"value": float(rsi)}},
                            {"id": "z", "left": {"feature": "z20"}, "op": "gte",
                             "right": {"value": float(z)}},
                            {"id": "bear", "left": {"feature": "close"}, "op": "lt",
                             "right": {"feature": "open"}},
                        ]},
                        "exit": {"any": [
                            {"id": "tp", "left": {"feature": "rsi14"}, "op": "lte",
                             "right": {"value": 45}, "role": "take_profit"},
                            {"id": "inv", "left": {"feature": "close"}, "op": "gt",
                             "right": {"feature": "prev_high20"},
                             "role": "invalidation"},
                        ]},
                    })
    return out


def main():
    print("TRAIN4E", now(), flush=True)
    raw = build()
    seen, uniq = set(), []
    for obj in raw:
        if (obj.get("supported_instruments") or [None])[0] in BAN:
            continue
        if obj["key"] in seen:
            continue
        seen.add(obj["key"])
        try:
            uniq.append(dsl.validate_strategy(obj))
        except Exception:
            continue
    print("CANDS", len(uniq), flush=True)
    try:
        catalog = codex._existing_strategy_catalog()
    except Exception:
        catalog = []
    print("CATALOG", len(catalog or []), flush=True)

    frames, frs = {}, {}

    def get(sym, tf):
        k = (sym, tf)
        if k not in frames:
            f = pipeline._frame(sym, tf)
            if len(f) > BARS:
                f = f.iloc[-BARS:]
            frames[k] = f
            frs[sym] = eco._friction_scenario(sym, "observed_base")
            print("FRAME", sym, tf, len(f), flush=True)
        return frames[k], frs[sym]

    scored = []
    for i, definition in enumerate(uniq):
        try:
            if catalog and dsl.find_near_duplicate(definition, catalog):
                continue
        except Exception:
            pass
        try:
            frame, fr = get(definition["supported_instruments"][0],
                            definition["timeframe"])
            res = dsl.backtest_dsl(
                frame, definition, leverage=20, stop_loss_pct=0.009,
                fee_rate_per_side=float(fr.get("fee_rate_per_side") or 0.0005),
                slippage_rate_per_side=float(
                    fr.get("slippage_rate_per_side") or 0.0002),
                half_spread_rate_per_side=float(
                    fr.get("half_spread_rate_per_side") or 0),
                impact_rate_per_side=float(fr.get("impact_rate_per_side") or 0),
                latency_rate_per_side=float(fr.get("latency_rate_per_side") or 0),
                funding_rate_per_8h=float(fr.get("funding_rate_per_8h") or 0),
                friction_scenario="observed_base",
            )
            m = metrics(res.get("trades") or [], res)
        except Exception:
            continue
        # Soft evidence floor for AI-friendly submit pool
        if m["trades"] < 10 or m["wr"] < 50 or m["mean"] <= 0:
            if (i + 1) % 50 == 0:
                print("progress", i + 1, "/", len(uniq), "pool", len(scored),
                      flush=True)
            continue
        score = (m["wr"] + m["mean"] * 900 - m["streak"] * 4
                 + min(m["trades"], 200) * 0.3 + (40 if m.get("fold_ok") else 0))
        scored.append({"score": score, "dsl": definition, "metrics": m})
        print("POOL", definition["key"],
              definition["supported_instruments"][0], m, round(score, 1),
              flush=True)
        if (i + 1) % 50 == 0:
            print("progress", i + 1, "/", len(uniq), "pool", len(scored),
                  flush=True)
        if len(scored) >= 15:
            print("EARLY_POOL", len(scored), flush=True)
            break

    scored.sort(key=lambda x: -x["score"])
    print("POOL_N", len(scored), flush=True)
    (OUT / "candidates_kept_e.json").write_text(json.dumps([
        {"key": r["dsl"]["key"], "score": r["score"], "metrics": r["metrics"],
         "symbol": r["dsl"]["supported_instruments"][0],
         "name": r["dsl"].get("name"), "dsl": r["dsl"]}
        for r in scored[:20]
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    pool, seen_sym = [], set()
    for row in scored:
        sym = row["dsl"]["supported_instruments"][0]
        if sym not in seen_sym or len(pool) < 4:
            pool.append(row)
            seen_sym.add(sym)
        if len(pool) >= 12:
            break

    passed, attempted = None, []
    for row in pool:
        definition = row["dsl"]
        print("SUBMIT", definition["key"],
              definition["supported_instruments"][0], row["metrics"], flush=True)
        out = codex.submit_codex_strategy(definition, meta={
            "symbol": definition["supported_instruments"][0],
            "timeframe": definition["timeframe"],
            "thesis": definition.get("description"),
            "author": "codex_0725_train4",
        }, dry_run=False)
        ai_rev = out.get("ai_review") or {}
        slim = {
            "ok": out.get("ok"), "key": definition["key"],
            "name": definition.get("name"),
            "symbol": definition["supported_instruments"][0],
            "pushed": out.get("pushed"), "stage": out.get("stage"),
            "avg": ai_rev.get("ai_theoretical_wr_avg"),
            "fail": ai_rev.get("fail_reasons") or out.get("reason"),
            "three": three_ok(ai_rev), "metrics": row["metrics"],
            "reviews": [{
                "p": r.get("provider"), "d": r.get("decision"),
                "wr": r.get("theoretical_win_rate_pct"),
                "risk": r.get("stop_cluster_risk"), "ok": r.get("ok"),
                "reason": str(r.get("reason") or "")[:180],
            } for r in (ai_rev.get("reviews") or [])],
        }
        print("SUBMIT_OUT", json.dumps(slim, ensure_ascii=False), flush=True)
        attempted.append(slim)
        (OUT / "submit_attempts_e.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2), encoding="utf-8")
        with (OUT / "train_audit.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "submit_e", "out": slim, "time": now()},
                               ensure_ascii=False) + "\n")
        if out.get("ok") and out.get("pushed") and three_ok(ai_rev):
            passed = out
            passed["name"] = definition.get("name")
            passed["dsl"] = definition
            break
        time.sleep(3)

    (OUT / "final_status_e.json").write_text(json.dumps({
        "time": now(), "pool": len(scored), "passed": bool(passed),
        "passed_key": (passed or {}).get("key"),
        "passed_name": (passed or {}).get("name"),
        "queued": (passed or {}).get("queued"), "attempted": attempted,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("FINAL_PASSED", bool(passed), (passed or {}).get("key"),
          (passed or {}).get("name"), flush=True)
    if passed:
        import auto_trade_formal_notify as n
        import auto_trade_strategy_titles as titles
        shown = titles.short_strategy_title(
            passed.get("key"), passed.get("name"))
        sym = (passed.get("dsl") or {}).get("supported_instruments", [""])[0]
        n.send_message(
            "【7.25训练4产出】\n异标的策略 · 三AI通过\n%s\n标的: %s\n时间: %s"
            % (shown, sym, now()),
            kind="codex_strategy_review",
            meta={"key": passed.get("key"),
                  "strategy_name": passed.get("name")},
        )
        print("wx_ok", shown, flush=True)


if __name__ == "__main__":
    main()
