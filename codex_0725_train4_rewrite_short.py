# -*- coding: utf-8 -*-
"""Focused rewrite: ADA short trend mirror + ema32/inv variant; AI gate >=70."""
from __future__ import print_function
import os, json, time
from pathlib import Path
from datetime import datetime

OUT = Path("/root/auto_trade/codex_0725_train4")
LOG = Path("/tmp/codex_0725_rw_short.log")
MIN_AI = 70.0
ADA = "ADA-USDT-SWAP"

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


def build():
    cands = []
    # ADA short mirror — opposite direction of live longs => not near-dup
    for rc in (55, 58, 60, 62, 65):
        for z in (-1.8, -2.0, -2.2, -2.4, -2.6, -2.8):
            for hold in (12, 14, 16, 18):
                for ema in ("ema21", "ema17", "ema23"):
                    for tp in (40, 38, 42):
                        key = "codex0725t4rw_ada5_trendsh_%s_r%s_z%s_h%s_t%s" % (
                            ema, rc, ztag(z), hold, tp)
                        cands.append({
                            "schema": "qiyu_strategy_dsl_v1",
                            "key": key,
                            "name": "ADA5顺势回落空·0725RW",
                            "direction": "short",
                            "timeframe": "5m",
                            "supported_instruments": [ADA],
                            "max_hold_bars": hold,
                            "description": (
                                "ADA 5m H1空头顺势：h1空+slope<0+RSI下穿%s+close<%s+z>%s；"
                                "RSI<=%s止盈。与现有多头trendpb方向相反，属新拓扑。"
                                % (rc, ema, z, tp)),
                            "origin": "codex_0725_train4_rewrite",
                            "version": "0725t4rw",
                            "entry": {"all": [
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
                            "exit": {"any": [
                                {"id": "tp", "left": {"feature": "rsi14"}, "op": "lt",
                                 "right": {"value": float(tp)}, "role": "take_profit"},
                                {"id": "inv", "left": {"feature": "close"}, "op": "gt",
                                 "right": {"feature": "prev_high20"}, "role": "invalidation"},
                            ]},
                        })
    # Long variant with ema32 + ema53 invalidation (topology change vs prev_low20)
    for rc in (40, 42, 45):
        for z in (2.0, 2.3, 2.6):
            for hold in (12, 14, 16):
                key = "codex0725t4rw_ada5_h1pb32inv_r%s_z%s_h%s" % (rc, ztag(z), hold)
                cands.append({
                    "schema": "qiyu_strategy_dsl_v1",
                    "key": key,
                    "name": "ADA5顺势EMA32失效变体·0725RW",
                    "direction": "long",
                    "timeframe": "5m",
                    "supported_instruments": [ADA],
                    "max_hold_bars": hold,
                    "description": (
                        "ADA H1多+RSI上穿%s+close>ema32+z<%s；RSI>=65止盈；跌破ema53失效。"
                        "拓扑异于ema21+prev_low20。" % (rc, z)),
                    "origin": "codex_0725_train4_rewrite",
                    "version": "0725t4rw",
                    "entry": {"all": [
                        {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "gt",
                         "right": {"feature": "h1_ema53"}},
                        {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gt",
                         "right": {"value": 0}},
                        {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_above",
                         "right": {"value": float(rc)}},
                        {"id": "px", "left": {"feature": "close"}, "op": "gt",
                         "right": {"feature": "ema32"}},
                        {"id": "z", "left": {"feature": "z20"}, "op": "lt",
                         "right": {"value": float(z)}},
                    ]},
                    "exit": {"any": [
                        {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
                         "right": {"value": 65.0}, "role": "take_profit"},
                        {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                         "right": {"feature": "ema53"}, "role": "invalidation"},
                    ]},
                })
    return cands


def hard_ok(m):
    return (
        m["trades"] >= 20 and m["wr"] >= 65 and m["mean"] > 0
        and m["streak"] <= 3 and m.get("fold_ok")
    )


def main():
    log("RW_SHORT START")
    cands = build()
    log("CANDS %s" % len(cands))
    f = pipeline._frame(ADA, "5m")
    if len(f) > 12000:
        f = f.iloc[-12000:]
    fr = eco._friction_scenario(ADA, "observed_base")
    try:
        catalog = codex._existing_strategy_catalog()
    except Exception:
        catalog = []
    log("FRAME %s CAT %s" % (len(f), len(catalog or [])))

    scored, topany = [], []
    for i, obj in enumerate(cands):
        try:
            d = dsl.validate_strategy(obj)
        except Exception:
            continue
        try:
            if catalog and dsl.find_near_duplicate(d, catalog):
                continue
        except Exception:
            pass
        try:
            res = dsl.backtest_dsl(
                f, d, leverage=20, stop_loss_pct=0.009,
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
        row = {"score": score, "dsl": d, "metrics": m, "key": d["key"], "name": d.get("name")}
        if m["trades"] >= 10:
            topany.append(row)
        if hard_ok(m):
            scored.append(row)
            log("KEEP %s %s" % (d["key"], m))
        if (i + 1) % 100 == 0:
            topany.sort(key=lambda x: -x["score"])
            b = topany[0] if topany else None
            log("progress %s/%s keep=%s top=%s" % (
                i + 1, len(cands), len(scored),
                None if not b else (b["key"], b["metrics"])))
        if len(scored) >= 12:
            break

    scored.sort(key=lambda x: -x["score"])
    topany.sort(key=lambda x: -x["score"])
    (OUT / "candidates_kept_rw_short.json").write_text(json.dumps([
        {"key": r["key"], "score": r["score"], "metrics": r["metrics"],
         "name": r["name"], "dsl": r["dsl"]}
        for r in scored[:20]
    ], ensure_ascii=False, indent=2))
    (OUT / "candidates_topany_rw_short.json").write_text(json.dumps([
        {"key": r["key"], "score": r["score"], "metrics": r["metrics"], "name": r["name"]}
        for r in topany[:30]
    ], ensure_ascii=False, indent=2))
    log("POOL %s TOPANY %s" % (len(scored), len(topany)))
    for r in topany[:10]:
        log("TOP %s %s" % (r["key"], r["metrics"]))

    pool = list(scored)
    if not pool:
        pool = [r for r in topany
                if r["metrics"]["trades"] >= 18 and r["metrics"]["wr"] >= 62
                and r["metrics"].get("fold_ok") and r["metrics"]["mean"] > 0]
    if not pool:
        pool = [r for r in topany
                if r["metrics"]["trades"] >= 15 and r["metrics"]["wr"] >= 68
                and r["metrics"]["mean"] > 0 and r["metrics"].get("fold_ok")]

    passed, attempted = None, []
    for row in pool[:8]:
        d, m = row["dsl"], row["metrics"]
        log("SUBMIT %s %s" % (d["key"], m))
        out = codex.submit_codex_strategy(d, meta={
            "symbol": ADA, "timeframe": "5m",
            "thesis": d.get("description"),
            "author": "codex_0725_train4_rewrite",
        }, dry_run=False)
        ai = out.get("ai_review") or {}
        avg = ai.get("ai_theoretical_wr_avg")
        slim = {
            "ok": out.get("ok"), "key": d["key"], "name": d.get("name"),
            "pushed": out.get("pushed"), "stage": out.get("stage"),
            "avg": avg, "three": three_ok(ai), "metrics": m,
            "fail": ai.get("fail_reasons") or out.get("reason"),
            "reviews": [{
                "p": r.get("provider"), "d": r.get("decision"),
                "wr": r.get("theoretical_win_rate_pct"),
                "risk": r.get("stop_cluster_risk"), "ok": r.get("ok"),
                "reason": str(r.get("reason") or "")[:240],
            } for r in (ai.get("reviews") or [])],
        }
        if out.get("pushed") and avg is not None and float(avg) < MIN_AI:
            try:
                pipeline.reject(
                    d["key"],
                    reason="REWRITE目标≥70%%：AI理论胜率%.1f%%，自动拒绝" % float(avg),
                )
            except Exception as e:
                log("rej_err %s" % e)
            slim.update(pushed=False, auto_rejected=True, ok=False)
        log("SUBMIT_OUT %s" % json.dumps(slim, ensure_ascii=False))
        attempted.append(slim)
        (OUT / "submit_attempts_rw_short.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2))
        if (slim.get("ok") and slim.get("pushed") and slim.get("three")
                and avg is not None and float(avg) >= MIN_AI):
            passed = {
                "key": d["key"], "name": d.get("name"), "ai_avg": avg,
                "metrics": m, "reviews": slim["reviews"],
            }
            break
        time.sleep(2)

    status = {
        "time": now(), "pool": len(scored), "passed": bool(passed),
        "passed_key": (passed or {}).get("key"),
        "passed_name": (passed or {}).get("name"),
        "ai_avg": (passed or {}).get("ai_avg"),
        "metrics": (passed or {}).get("metrics"),
        "reviews": (passed or {}).get("reviews"),
        "attempted": attempted,
        "top10": [{"key": r["key"], "metrics": r["metrics"]} for r in topany[:10]],
    }
    (OUT / "final_status_rw_short.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2))
    log("FINAL %s" % json.dumps(
        {k: status[k] for k in status if k not in ("attempted", "reviews", "top10")},
        ensure_ascii=False))
    if passed:
        try:
            import auto_trade_formal_notify as n
            import auto_trade_strategy_titles as titles
            shown = titles.short_strategy_title(passed["key"], passed.get("name"))
            n.send_message(
                "【7.25训练4·REWRITE】\n%s\nAI理论WR: %s\n时间: %s"
                % (shown, passed.get("ai_avg"), now()),
                kind="codex_strategy_review",
                meta={"key": passed["key"]},
            )
            log("wx_ok %s" % shown)
        except Exception as e:
            log("wx_err %s" % e)


if __name__ == "__main__":
    main()
