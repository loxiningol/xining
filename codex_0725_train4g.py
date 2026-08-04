# -*- coding: utf-8 -*-
"""train4g: preload frames; hybrid H1+RSI+ema17 (topology != ADA ema21); submit best."""
from __future__ import print_function
import json, os, time, copy
from datetime import datetime
from pathlib import Path

OUT = Path("/root/auto_trade/codex_0725_train4")
OUT.mkdir(parents=True, exist_ok=True)
BARS = 8000
BAN = {"ADA-USDT-SWAP"}
SYMS = [
    ("XAU-USDT-SWAP", "xau"), ("NG-USDT-SWAP", "ng"), ("CL-USDT-SWAP", "cl"),
    ("LTC-USDT-SWAP", "ltc"), ("BNB-USDT-SWAP", "bnb"), ("XAG-USDT-SWAP", "xag"),
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
    out = []
    # Hybrid long: H1 dual EMA + slope + RSI cross + close>ema17 (NOT ema21 = different topology)
    for sym, tag in SYMS:
        for rc in (40, 42, 45):
            for z in (2.0, 2.3, 2.6):
                for hold in (12, 14, 18):
                    for tp in (60,):
                        key = "codex0725t4_%s5_h1rsi17_r%s_z%s_h%s" % (
                            tag, rc, ztag(z), hold)
                        out.append({
                            "schema": "qiyu_strategy_dsl_v1", "key": key,
                            "name": "%s5 H1顺势EMA17·0725T4" % tag.upper(),
                            "direction": "long", "timeframe": "5m",
                            "supported_instruments": [sym], "max_hold_bars": hold,
                            "description": (
                                "%s 5m H1多头顺势：h1_ema19>h1_ema53 + slope>0 + "
                                "RSI上穿%s + close>ema17 + z<%s；RSI>=%s止盈；"
                                "跌破prev_low20失效。ema17确认异于ADA ema21族。"
                                % (sym.split("-")[0], rc, z, tp)),
                            "origin": "codex_0725_train4", "version": "0725t4g",
                            "entry": {"all": [
                                {"id": "h1", "left": {"feature": "h1_ema19"},
                                 "op": "gt", "right": {"feature": "h1_ema53"}},
                                {"id": "slope", "left": {"feature": "h1_slope4"},
                                 "op": "gt", "right": {"value": 0}},
                                {"id": "rsi", "left": {"feature": "rsi14"},
                                 "op": "cross_above", "right": {"value": float(rc)}},
                                {"id": "px", "left": {"feature": "close"}, "op": "gt",
                                 "right": {"feature": "ema17"}},
                                {"id": "z", "left": {"feature": "z20"}, "op": "lt",
                                 "right": {"value": float(z)}},
                            ]},
                            "exit": {"any": [
                                {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
                                 "right": {"value": float(tp)}, "role": "take_profit"},
                                {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                                 "right": {"feature": "prev_low20"},
                                 "role": "invalidation"},
                            ]},
                        })
    # Short mirror with ema17
    for sym, tag in SYMS:
        for rc in (55, 58, 62):
            for z in (-2.0, -2.4):
                for hold in (14, 18):
                    key = "codex0725t4_%s5_h1sh17_r%s_z%s_h%s" % (
                        tag, rc, ztag(z), hold)
                    out.append({
                        "schema": "qiyu_strategy_dsl_v1", "key": key,
                        "name": "%s5 H1顺势空EMA17·0725T4" % tag.upper(),
                        "direction": "short", "timeframe": "5m",
                        "supported_instruments": [sym], "max_hold_bars": hold,
                        "description": (
                            "%s 5m H1空头：h1空 + slope<0 + RSI下穿%s + close<ema17 "
                            "+ z>%s；RSI<=40止盈。"
                            % (sym.split("-")[0], rc, z)),
                        "origin": "codex_0725_train4", "version": "0725t4g",
                        "entry": {"all": [
                            {"id": "h1", "left": {"feature": "h1_ema19"},
                             "op": "lt", "right": {"feature": "h1_ema53"}},
                            {"id": "slope", "left": {"feature": "h1_slope4"},
                             "op": "lt", "right": {"value": 0}},
                            {"id": "rsi", "left": {"feature": "rsi14"},
                             "op": "cross_below", "right": {"value": float(rc)}},
                            {"id": "px", "left": {"feature": "close"}, "op": "lt",
                             "right": {"feature": "ema17"}},
                            {"id": "z", "left": {"feature": "z20"}, "op": "gt",
                             "right": {"value": float(z)}},
                        ]},
                        "exit": {"any": [
                            {"id": "tp", "left": {"feature": "rsi14"}, "op": "lt",
                             "right": {"value": 40}, "role": "take_profit"},
                            {"id": "inv", "left": {"feature": "close"}, "op": "gt",
                             "right": {"feature": "prev_high20"},
                             "role": "invalidation"},
                        ]},
                    })
    return out

def main():
    print("TRAIN4G", now(), flush=True)
    raw = build()
    seen, uniq = set(), []
    for obj in raw:
        if obj["supported_instruments"][0] in BAN or obj["key"] in seen:
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

    # Preload all frames
    frames, frs = {}, {}
    need = {}
    for d in uniq:
        need[(d["supported_instruments"][0], d["timeframe"])] = True
    for (sym, tf) in sorted(need.keys()):
        t0 = time.time()
        f = pipeline._frame(sym, tf)
        if len(f) > BARS:
            f = f.iloc[-BARS:]
        frames[(sym, tf)] = f
        frs[sym] = eco._friction_scenario(sym, "observed_base")
        print("PRELOAD", sym, tf, len(f), "dt=%.1f" % (time.time() - t0), flush=True)

    scored = []
    for i, definition in enumerate(uniq):
        try:
            if catalog and dsl.find_near_duplicate(definition, catalog):
                continue
        except Exception:
            pass
        sym = definition["supported_instruments"][0]
        tf = definition["timeframe"]
        frame, fr = frames[(sym, tf)], frs[sym]
        try:
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
            m = metrics(res.get("trades") or [], res)
        except Exception as e:
            continue
        if (i + 1) % 40 == 0:
            print("progress", i + 1, "/", len(uniq), "pool", len(scored), flush=True)
        # soft evidence floor
        if m["trades"] < 8 or m["wr"] < 52 or m["mean"] <= 0:
            continue
        score = (m["wr"] + m["mean"] * 900 - m["streak"] * 4
                 + min(m["trades"], 200) * 0.3 + (40 if m.get("fold_ok") else 0))
        scored.append({"score": score, "dsl": definition, "metrics": m})
        print("POOL", definition["key"], sym, m, round(score, 1), flush=True)
        if len(scored) >= 12:
            print("EARLY", len(scored), flush=True)
            break

    scored.sort(key=lambda x: -x["score"])
    print("POOL_N", len(scored), flush=True)
    (OUT / "candidates_kept_g.json").write_text(json.dumps([
        {"key": r["dsl"]["key"], "score": r["score"], "metrics": r["metrics"],
         "symbol": r["dsl"]["supported_instruments"][0],
         "name": r["dsl"].get("name"), "dsl": r["dsl"]}
        for r in scored[:20]
    ], ensure_ascii=False, indent=2), encoding="utf-8")

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
        (OUT / "submit_attempts_g.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2), encoding="utf-8")
        with (OUT / "train_audit.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "submit_g", "out": slim, "time": now()},
                               ensure_ascii=False) + "\n")
        if out.get("ok") and out.get("pushed") and three_ok(ai_rev):
            passed = out
            passed["name"] = definition.get("name")
            passed["dsl"] = definition
            break
        time.sleep(2)

    (OUT / "final_status_g.json").write_text(json.dumps({
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
