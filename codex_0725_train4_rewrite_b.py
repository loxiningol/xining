# -*- coding: utf-8 -*-
"""Focused rewrite probe: prioritize H1-ema32 / KDJ / RSI-fade; track top-any."""
from __future__ import print_function
import json, os, time, itertools
from datetime import datetime
from pathlib import Path

OUT = Path("/root/auto_trade/codex_0725_train4")
OUT.mkdir(parents=True, exist_ok=True)
LOG = Path("/tmp/codex_0725_train4_rewrite_b.log")
BARS = 12000
BAN = {"ADA-USDT-SWAP"}
MIN_AI_PUSH = 70.0
MIN_AI_SOFT = 65.0

SYMS = [
    ("DOGE-USDT-SWAP", "doge"),
    ("XRP-USDT-SWAP", "xrp"),
    ("BNB-USDT-SWAP", "bnb"),
    ("LTC-USDT-SWAP", "ltc"),
    ("XAU-USDT-SWAP", "xau"),
    ("XAG-USDT-SWAP", "xag"),
    ("CL-USDT-SWAP", "cl"),
    ("NG-USDT-SWAP", "ng"),
    ("SOL-USDT-SWAP", "sol"),
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
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


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


def mk(key, name, sym, direction, tf, hold, desc, entry, exit_):
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key[:90], "name": name,
        "direction": direction, "timeframe": tf,
        "supported_instruments": [sym], "max_hold_bars": hold,
        "description": desc, "origin": "codex_0725_train4_rewrite_b",
        "version": "0725t4rwb", "entry": entry, "exit": exit_,
    }


def build():
    out = []
    # Priority 1: H1 pb with non-ema21 confirmation (topology != ADA)
    for sym, tag in SYMS:
        for rc in (40, 42, 45):
            for z in (2.0, 2.4, 2.8):
                for hold in (12, 14, 18):
                    for ema in ("ema32", "ema38", "ema23"):
                        key = "codex0725t4rwb_%s5_h1pb_%s_r%s_z%s_h%s" % (
                            tag, ema, rc, ztag(z), hold)
                        out.append(mk(
                            key, "%s5 H1顺势%s·0725RWb" % (tag.upper(), ema.upper()),
                            sym, "long", "5m", hold,
                            "%s 5m H1多+RSI上穿%s+close>%s+z<%s；RSI>=60止盈。"
                            % (tag.upper(), rc, ema, z),
                            {"all": [
                                {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "gt",
                                 "right": {"feature": "h1_ema53"}},
                                {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gt",
                                 "right": {"value": 0}},
                                {"id": "rsi", "left": {"feature": "rsi14"},
                                 "op": "cross_above", "right": {"value": float(rc)}},
                                {"id": "px", "left": {"feature": "close"}, "op": "gt",
                                 "right": {"feature": ema}},
                                {"id": "z", "left": {"feature": "z20"}, "op": "lt",
                                 "right": {"value": float(z)}},
                            ]},
                            {"any": [
                                {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
                                 "right": {"value": 60.0}, "role": "take_profit"},
                                {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                                 "right": {"feature": "prev_low20"}, "role": "invalidation"},
                            ]},
                        ))
    # Priority 2: H1 short ema32/38
    for sym, tag in SYMS:
        for rc in (58, 60, 62):
            for z in (-2.0, -2.4):
                for hold in (14, 18):
                    for ema in ("ema32", "ema38"):
                        key = "codex0725t4rwb_%s5_h1sh_%s_r%s_z%s_h%s" % (
                            tag, ema, rc, ztag(z), hold)
                        out.append(mk(
                            key, "%s5 H1空%s·0725RWb" % (tag.upper(), ema.upper()),
                            sym, "short", "5m", hold,
                            "%s 5m H1空+RSI下穿%s+close<%s+z>%s；RSI<=40止盈。"
                            % (tag.upper(), rc, ema, z),
                            {"all": [
                                {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "lt",
                                 "right": {"feature": "h1_ema53"}},
                                {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "lt",
                                 "right": {"value": 0}},
                                {"id": "rsi", "left": {"feature": "rsi14"},
                                 "op": "cross_below", "right": {"value": float(rc)}},
                                {"id": "px", "left": {"feature": "close"}, "op": "lt",
                                 "right": {"feature": ema}},
                                {"id": "z", "left": {"feature": "z20"}, "op": "gt",
                                 "right": {"value": float(z)}},
                            ]},
                            {"any": [
                                {"id": "tp", "left": {"feature": "rsi14"}, "op": "lt",
                                 "right": {"value": 40.0}, "role": "take_profit"},
                                {"id": "inv", "left": {"feature": "close"}, "op": "gt",
                                 "right": {"feature": "prev_high20"}, "role": "invalidation"},
                            ]},
                        ))
    # Priority 3: KDJ with H1 filter
    for sym, tag in SYMS:
        for jth in (10, 15, 20):
            for hold in (14, 18):
                for tpj in (60, 70):
                    key = "codex0725t4rwb_%s5_h1kdj_j%s_h%s_t%s" % (
                        tag, jth, hold, tpj)
                    out.append(mk(
                        key, "%s5 H1+KDJ回收·0725RWb" % tag.upper(), sym,
                        "long", "5m", hold,
                        "%s 5m H1多+j上穿%s+close>ema23；j>=%s止盈。"
                        % (tag.upper(), jth, tpj),
                        {"all": [
                            {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "gt",
                             "right": {"feature": "h1_ema53"}},
                            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gt",
                             "right": {"value": 0}},
                            {"id": "jcross", "left": {"feature": "j"}, "op": "cross_above",
                             "right": {"value": float(jth)}},
                            {"id": "px", "left": {"feature": "close"}, "op": "gt",
                             "right": {"feature": "ema23"}},
                        ]},
                        {"any": [
                            {"id": "tp", "left": {"feature": "j"}, "op": "gte",
                             "right": {"value": float(tpj)}, "role": "take_profit"},
                            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                             "right": {"feature": "prev_low20"}, "role": "invalidation"},
                        ]},
                    ))
    return out


def hard_ok(m):
    return (m["trades"] >= 20 and m["wr"] >= 62 and m["mean"] > 0
            and m["streak"] <= 3 and m.get("fold_ok"))


def submit_gate(definition, m):
    out = codex.submit_codex_strategy(definition, meta={
        "symbol": definition["supported_instruments"][0],
        "timeframe": definition["timeframe"],
        "thesis": definition.get("description"),
        "author": "codex_0725_train4_rewrite_b",
    }, dry_run=False)
    ai_rev = out.get("ai_review") or {}
    avg = ai_rev.get("ai_theoretical_wr_avg")
    slim = {
        "ok": out.get("ok"), "key": definition["key"],
        "name": definition.get("name"),
        "symbol": definition["supported_instruments"][0],
        "pushed": out.get("pushed"), "stage": out.get("stage"),
        "avg": avg,
        "fail": ai_rev.get("fail_reasons") or out.get("reason"),
        "three": three_ok(ai_rev), "metrics": m,
        "reviews": [{
            "p": r.get("provider"), "d": r.get("decision"),
            "wr": r.get("theoretical_win_rate_pct"),
            "risk": r.get("stop_cluster_risk"), "ok": r.get("ok"),
            "reason": str(r.get("reason") or "")[:220],
        } for r in (ai_rev.get("reviews") or [])],
    }
    if out.get("pushed") and avg is not None and float(avg) < MIN_AI_PUSH:
        reason = ("REWRITE目标≥70%%：AI理论胜率%.1f%%，自动拒绝" % float(avg)
                  if float(avg) >= MIN_AI_SOFT else
                  "REWRITE软门槛：AI理论胜率%.1f%%<65%%，自动拒绝" % float(avg))
        try:
            pipeline.reject(definition["key"], reason=reason)
        except Exception as e:
            log("reject_err %s" % e)
        slim["pushed"] = False
        slim["auto_rejected"] = True
        slim["ok"] = False
    return slim, out, avg


def main():
    log("REWRITE_B START")
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
    log("CANDS %s" % len(uniq))
    try:
        catalog = codex._existing_strategy_catalog()
    except Exception:
        catalog = []

    frames, frs = {}, {}
    need = {(d["supported_instruments"][0], d["timeframe"]) for d in uniq}
    for (sym, tf) in sorted(need):
        t0 = time.time()
        f = pipeline._frame(sym, tf)
        if len(f) > BARS:
            f = f.iloc[-BARS:]
        frames[(sym, tf)] = f
        frs[sym] = eco._friction_scenario(sym, "observed_base")
        log("PRELOAD %s %s n=%s dt=%.1f" % (sym, tf, len(f), time.time() - t0))

    scored, topany = [], []
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
        except Exception:
            continue
        score = (m["wr"] + m["mean"] * 1000 - m["streak"] * 6
                 + min(m["trades"], 80) * 0.5 + (50 if m.get("fold_ok") else 0))
        row = {"score": score, "dsl": definition, "metrics": m,
               "symbol": sym, "name": definition.get("name"),
               "key": definition["key"]}
        if m["trades"] >= 8:
            topany.append(row)
        if hard_ok(m):
            scored.append(row)
            log("KEEP %s %s %s score=%.1f" % (definition["key"], sym, m, score))
        if (i + 1) % 60 == 0:
            topany.sort(key=lambda x: -x["score"])
            best = topany[0] if topany else None
            log("progress %s/%s keep=%s top=%s" % (
                i + 1, len(uniq), len(scored),
                None if not best else (best["key"], best["metrics"])))
        if len(scored) >= 25:
            break

    scored.sort(key=lambda x: -x["score"])
    topany.sort(key=lambda x: -x["score"])
    (OUT / "candidates_kept_rwb.json").write_text(json.dumps([
        {"key": r["key"], "score": r["score"], "metrics": r["metrics"],
         "symbol": r["symbol"], "name": r["name"], "dsl": r["dsl"]}
        for r in scored[:25]
    ], ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "candidates_topany_rwb.json").write_text(json.dumps([
        {"key": r["key"], "score": r["score"], "metrics": r["metrics"],
         "symbol": r["symbol"], "name": r["name"]}
        for r in topany[:40]
    ], ensure_ascii=False, indent=2), encoding="utf-8")
    log("POOL %s TOPANY %s" % (len(scored), len(topany)))
    if topany[:5]:
        for r in topany[:5]:
            log("TOP %s %s %s" % (r["key"], r["symbol"], r["metrics"]))

    # Submit pool: hard first, else best topany with n>=18 wr>=60 fold
    pool = list(scored)
    if not pool:
        pool = [r for r in topany
                if r["metrics"]["trades"] >= 18 and r["metrics"]["wr"] >= 60
                and r["metrics"].get("fold_ok") and r["metrics"]["mean"] > 0
                and r["metrics"]["streak"] <= 3]
    # last resort: n>=15 wr>=65 fold
    if not pool:
        pool = [r for r in topany
                if r["metrics"]["trades"] >= 15 and r["metrics"]["wr"] >= 65
                and r["metrics"].get("fold_ok") and r["metrics"]["mean"] > 0]

    passed, attempted = None, []
    for row in pool[:10]:
        definition, m = row["dsl"], row["metrics"]
        log("SUBMIT %s %s" % (definition["key"], m))
        slim, out, avg = submit_gate(definition, m)
        log("SUBMIT_OUT %s" % json.dumps(slim, ensure_ascii=False))
        attempted.append(slim)
        (OUT / "submit_attempts_rwb.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2), encoding="utf-8")
        if (slim.get("ok") and slim.get("pushed") and slim.get("three")
                and avg is not None and float(avg) >= MIN_AI_PUSH):
            passed = {"key": definition["key"], "name": definition.get("name"),
                      "ai_avg": avg, "metrics": m, "dsl": definition,
                      "reviews": slim.get("reviews")}
            break
        time.sleep(2)

    status = {
        "time": now(), "pool": len(scored), "topany": len(topany),
        "passed": bool(passed),
        "passed_key": (passed or {}).get("key"),
        "passed_name": (passed or {}).get("name"),
        "ai_avg": (passed or {}).get("ai_avg"),
        "metrics": (passed or {}).get("metrics"),
        "reviews": (passed or {}).get("reviews"),
        "attempted": attempted,
        "top5": [{"key": r["key"], "metrics": r["metrics"], "symbol": r["symbol"]}
                 for r in topany[:5]],
    }
    (OUT / "final_status_rwb.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    log("FINAL %s" % json.dumps(
        {k: status[k] for k in status if k not in ("attempted", "reviews")},
        ensure_ascii=False))
    if passed:
        try:
            import auto_trade_formal_notify as n
            import auto_trade_strategy_titles as titles
            shown = titles.short_strategy_title(passed.get("key"), passed.get("name"))
            n.send_message(
                "【7.25训练4·REWRITE-B】\n高胜率 · 三AI通过\n%s\nAI理论WR: %s\n时间: %s"
                % (shown, passed.get("ai_avg"), now()),
                kind="codex_strategy_review",
                meta={"key": passed.get("key")},
            )
        except Exception as e:
            log("wx_err %s" % e)


if __name__ == "__main__":
    main()
