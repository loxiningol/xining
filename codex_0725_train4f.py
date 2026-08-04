# -*- coding: utf-8 -*-
"""Ultra-focused train4f: small grids, print every metric, submit best to 3AI."""
from __future__ import print_function
import json, os, time
from datetime import datetime
from pathlib import Path

OUT = Path("/root/auto_trade/codex_0725_train4")
OUT.mkdir(parents=True, exist_ok=True)
BAN = {"ADA-USDT-SWAP"}

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
            st += 1; mx = max(mx, st)
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
    # Only symbols that often behave differently from majors + BTC
    syms = [
        ("XAU-USDT-SWAP", "xau"), ("NG-USDT-SWAP", "ng"),
        ("CL-USDT-SWAP", "cl"), ("XAG-USDT-SWAP", "xag"),
        ("LTC-USDT-SWAP", "ltc"), ("BNB-USDT-SWAP", "bnb"),
    ]
    out = []
    # KDJ lean
    for sym, tag in syms:
        for jth in (8, 15):
            for hold in (24, 36):
                key = "codex0725t4_%s5_kdjrec_j%s_h%s" % (tag, jth, hold)
                out.append({
                    "schema": "qiyu_strategy_dsl_v1", "key": key,
                    "name": "%s5 KDJ超跌反抽·0725T4" % tag.upper(),
                    "direction": "long", "timeframe": "5m",
                    "supported_instruments": [sym], "max_hold_bars": hold,
                    "description": "%s KDJ J<=%s 上穿K + close>ema21" % (tag.upper(), jth),
                    "origin": "codex_0725_train4", "version": "0725t4f",
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
                })
    # 15m breakdown
    for sym, tag in syms:
        for z in (-1.5, -2.0):
            for hold in (12, 16):
                key = "codex0725t4_%s15_bdsh_z%s_h%s" % (tag, ztag(z), hold)
                out.append({
                    "schema": "qiyu_strategy_dsl_v1", "key": key,
                    "name": "%s15 破位做空·0725T4" % tag.upper(),
                    "direction": "short", "timeframe": "15m",
                    "supported_instruments": [sym], "max_hold_bars": hold,
                    "description": "%s 15m breakdown short z<%s" % (tag.upper(), z),
                    "origin": "codex_0725_train4", "version": "0725t4f",
                    "entry": {"all": [
                        {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "lt",
                         "right": {"value": -0.0005}},
                        {"id": "px", "left": {"feature": "close"}, "op": "lt",
                         "right": {"feature": "ema53"}},
                        {"id": "z", "left": {"feature": "z20"}, "op": "lt",
                         "right": {"value": float(z)}},
                        {"id": "cross", "left": {"feature": "close"},
                         "op": "cross_below", "right": {"feature": "ema17"}},
                    ]},
                    "exit": {"any": [
                        {"id": "tp", "left": {"feature": "cci"}, "op": "cross_above",
                         "right": {"value": -50.0}, "role": "take_profit"},
                        {"id": "inv", "left": {"feature": "close"}, "op": "cross_above",
                         "right": {"feature": "ema53"}, "role": "invalidation"},
                    ]},
                })
    # RSI fade
    for sym, tag in syms:
        for rsi in (70, 74):
            for z in (1.3, 1.6):
                for hold in (24, 36):
                    key = "codex0725t4_%s5_rsifade_r%s_z%s_h%s" % (
                        tag, rsi, ztag(z), hold)
                    out.append({
                        "schema": "qiyu_strategy_dsl_v1", "key": key,
                        "name": "%s5 RSI冲高回落·0725T4" % tag.upper(),
                        "direction": "short", "timeframe": "5m",
                        "supported_instruments": [sym], "max_hold_bars": hold,
                        "description": "%s RSI>=%s z>=%s bear fade" % (tag.upper(), rsi, z),
                        "origin": "codex_0725_train4", "version": "0725t4f",
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
    # MACD stick reclaim long
    for sym, tag in syms:
        for stick in (-0.0002, 0.0):
            for hold in (24, 40):
                key = "codex0725t4_%s5_macdrec_s%s_h%s" % (tag, ztag(stick), hold)
                out.append({
                    "schema": "qiyu_strategy_dsl_v1", "key": key,
                    "name": "%s5 MACD翻红·0725T4" % tag.upper(),
                    "direction": "long", "timeframe": "5m",
                    "supported_instruments": [sym], "max_hold_bars": hold,
                    "description": "%s macd_stick cross above %s + close>ema21" % (
                        tag.upper(), stick),
                    "origin": "codex_0725_train4", "version": "0725t4f",
                    "entry": {"all": [
                        {"id": "cross", "left": {"feature": "macd_stick"},
                         "op": "cross_above", "right": {"value": float(stick)}},
                        {"id": "px", "left": {"feature": "close"}, "op": "gt",
                         "right": {"feature": "ema21"}},
                        {"id": "rsi", "left": {"feature": "rsi14"}, "op": "lt",
                         "right": {"value": 55}},
                    ]},
                    "exit": {"any": [
                        {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
                         "right": {"value": 62}, "role": "take_profit"},
                        {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                         "right": {"feature": "prev_low20"}, "role": "invalidation"},
                    ]},
                })
    return out

def main():
    print("TRAIN4F", now(), flush=True)
    raw = build()
    seen, uniq = set(), []
    for obj in raw:
        if obj["supported_instruments"][0] in BAN or obj["key"] in seen:
            continue
        seen.add(obj["key"])
        try:
            uniq.append(dsl.validate_strategy(obj))
        except Exception as e:
            print("val_fail", obj["key"], e, flush=True)
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
            if len(f) > 12000:
                f = f.iloc[-12000:]
            frames[k] = f
            frs[sym] = eco._friction_scenario(sym, "observed_base")
            print("FRAME", sym, tf, len(f), flush=True)
        return frames[k], frs[sym]

    scored = []
    for i, definition in enumerate(uniq):
        try:
            if catalog and dsl.find_near_duplicate(definition, catalog):
                print("DUP", definition["key"], flush=True)
                continue
        except Exception:
            pass
        try:
            frame, fr = get(definition["supported_instruments"][0],
                            definition["timeframe"])
            t0 = time.time()
            res = dsl.backtest_dsl(
                frame, definition, leverage=20, stop_loss_pct=0.009,
                fee_rate_per_side=float(fr.get("fee_rate_per_side") or 0.0005),
                slippage_rate_per_side=float(fr.get("slippage_rate_per_side") or 0.0002),
                half_spread_rate_per_side=float(fr.get("half_spread_rate_per_side") or 0),
                impact_rate_per_side=float(fr.get("impact_rate_per_side") or 0),
                latency_rate_per_side=float(fr.get("latency_rate_per_side") or 0),
                funding_rate_per_8h=float(fr.get("funding_rate_per_8h") or 0),
                friction_scenario="observed_base",
            )
            dt = time.time() - t0
            m = metrics(res.get("trades") or [], res)
        except Exception as e:
            print("bt_fail", definition["key"], e, flush=True)
            continue
        score = (m["wr"] + m["mean"] * 900 - m["streak"] * 4
                 + min(m["trades"], 200) * 0.3 + (40 if m.get("fold_ok") else 0))
        print("BT", definition["key"], definition["supported_instruments"][0],
              "n=%s wr=%.1f mean=%.4f st=%s fold=%s ret=%s score=%.1f dt=%.1fs" % (
                  m["trades"], m["wr"], m["mean"], m["streak"], m["fold_ok"],
                  m["ret"], score, dt), flush=True)
        if m["trades"] >= 10 and m["wr"] >= 50 and m["mean"] > 0:
            scored.append({"score": score, "dsl": definition, "metrics": m})
            print("POOL", definition["key"], flush=True)

    scored.sort(key=lambda x: -x["score"])
    print("POOL_N", len(scored), flush=True)
    (OUT / "candidates_kept_f.json").write_text(json.dumps([
        {"key": r["dsl"]["key"], "score": r["score"], "metrics": r["metrics"],
         "symbol": r["dsl"]["supported_instruments"][0],
         "name": r["dsl"].get("name"), "dsl": r["dsl"]}
        for r in scored[:20]
    ], ensure_ascii=False, indent=2), encoding="utf-8")
    # always save top absolute even if not pooled
    # (done via BT logs)

    passed, attempted = None, []
    for row in scored[:10]:
        definition = row["dsl"]
        print("SUBMIT", definition["key"], row["metrics"], flush=True)
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
                "reason": str(r.get("reason") or "")[:200],
            } for r in (ai_rev.get("reviews") or [])],
        }
        print("SUBMIT_OUT", json.dumps(slim, ensure_ascii=False), flush=True)
        attempted.append(slim)
        (OUT / "submit_attempts_f.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2), encoding="utf-8")
        with (OUT / "train_audit.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "submit_f", "out": slim, "time": now()},
                               ensure_ascii=False) + "\n")
        if out.get("ok") and out.get("pushed") and three_ok(ai_rev):
            passed = out
            passed["name"] = definition.get("name")
            passed["dsl"] = definition
            break
        time.sleep(2)

    (OUT / "final_status_f.json").write_text(json.dumps({
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
        shown = titles.short_strategy_title(passed.get("key"), passed.get("name"))
        sym = (passed.get("dsl") or {}).get("supported_instruments", [""])[0]
        n.send_message(
            "【7.25训练4产出】\n异标的策略 · 三AI通过\n%s\n标的: %s\n时间: %s"
            % (shown, sym, now()),
            kind="codex_strategy_review",
            meta={"key": passed.get("key"), "strategy_name": passed.get("name")},
        )
        print("wx_ok", shown, flush=True)

if __name__ == "__main__":
    main()
