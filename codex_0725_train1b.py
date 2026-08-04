# -*- coding: utf-8 -*-
from __future__ import print_function
import json, time
from pathlib import Path
from datetime import datetime
import auto_trade_human_confirm_pipeline as pipeline
import auto_trade_strategy_dsl as dsl
import auto_trade_codex_strategy_review as codex
import auto_trade_ai_consensus as ai

SCHEMA = "qiyu_strategy_dsl_v1"
OUTDIR = Path("/root/auto_trade/codex_0725_train1")
OUTDIR.mkdir(parents=True, exist_ok=True)
AUDIT = OUTDIR / "train_audit.jsonl"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(row):
    row = dict(row)
    row["time"] = now()
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)


def ada_like(symbol, tag, tf, rsi_cross, zmax, hold, tp, slope_min=0.0):
    key = "codex0725_%s%s_trendpb_r%s_z%s_h%s" % (
        tag, tf, int(rsi_cross), str(zmax).replace(".", "p"), hold)
    return {
        "schema": SCHEMA, "key": key,
        "name": "%s%s顺势回升·0725" % (tag.upper(), tf),
        "direction": "long", "timeframe": tf,
        "supported_instruments": [symbol], "max_hold_bars": int(hold),
        "description": (
            "%s %s 顺势回升（移植ada5门）：H1 ema19>ema53 + slope>%s + RSI上穿%s + close>ema21 + z20<%s；"
            "RSI>=%s止盈；close<prev_low20失效。Codex 2026-07-25 train1b。"
            % (symbol.split("-")[0], tf, slope_min, rsi_cross, zmax, tp)),
        "origin": "codex_0725_train1", "version": "0725d",
        "entry": {"all": [
            {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": float(slope_min)}},
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_above", "right": {"value": float(rsi_cross)}},
            {"id": "px", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"id": "z", "left": {"feature": "z20"}, "op": "lt", "right": {"value": float(zmax)}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "gte", "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}, "role": "invalidation"},
        ]},
    }


def exh_short(symbol, tag, z, rsi, slope, hold, tp):
    key = "codex0725_%s5_fade_z%s_r%s_h%s" % (tag, str(z).replace(".", "p"), int(rsi), hold)
    return {
        "schema": SCHEMA, "key": key,
        "name": "%s5冲高衰竭·0725" % tag.upper(),
        "direction": "short", "timeframe": "5m",
        "supported_instruments": [symbol], "max_hold_bars": int(hold),
        "description": (
            "%s 5m冲高衰竭做空：z20>=%s RSI>=%s 阴线 K下行 H1斜率<=%s；RSI<=%s止盈；上破prev_high20失效。Codex 0725 train1b。"
            % (symbol.split("-")[0], z, rsi, slope, tp)),
        "origin": "codex_0725_train1", "version": "0725d",
        "entry": {"all": [
            {"id": "z", "left": {"feature": "z20"}, "op": "gte", "right": {"value": float(z)}},
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "gte", "right": {"value": float(rsi)}},
            {"id": "bear", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}},
            {"id": "kdown", "left": {"feature": "k"}, "op": "lt", "right": {"feature": "k", "offset": 1}},
            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "lte", "right": {"value": float(slope)}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "lte", "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
    }


def short_pb(symbol, tag, tf, rsi_cross, zmin, hold, tp):
    key = "codex0725_%s%s_trendfade_r%s_z%s_h%s" % (
        tag, tf, int(rsi_cross), str(zmin).replace(".", "p"), hold)
    return {
        "schema": SCHEMA, "key": key,
        "name": "%s%s顺势回落·0725" % (tag.upper(), tf),
        "direction": "short", "timeframe": tf,
        "supported_instruments": [symbol], "max_hold_bars": int(hold),
        "description": (
            "%s %s 顺势回落：H1 ema19<ema53 + slope<0 + RSI下穿%s + close<ema21 + z20>%s；"
            "RSI<=%s止盈；上破prev_high20失效。Codex 0725 train1b。"
            % (symbol.split("-")[0], tf, rsi_cross, zmin, tp)),
        "origin": "codex_0725_train1", "version": "0725d",
        "entry": {"all": [
            {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}},
            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": 0}},
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_below", "right": {"value": float(rsi_cross)}},
            {"id": "px", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
            {"id": "z", "left": {"feature": "z20"}, "op": "gt", "right": {"value": float(zmin)}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "lte", "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
    }


def main():
    cands = []
    for sym, tag, tf, hold in [
        ("BTC-USDT-SWAP", "btc", "5m", 14),
        ("ETH-USDT-SWAP", "eth", "5m", 14),
        ("XAU-USDT-SWAP", "xau", "15m", 16),
        ("XAG-USDT-SWAP", "xag", "5m", 14),
        ("CL-USDT-SWAP", "cl", "5m", 14),
        ("SOL-USDT-SWAP", "sol", "5m", 14),
        ("BNB-USDT-SWAP", "bnb", "5m", 14),
    ]:
        for rc in (40, 42, 44, 38):
            for zmax in (1.6, 2.0, 2.2):
                for tp in (58, 60, 62):
                    cands.append(ada_like(sym, tag, tf, rc, zmax, hold, tp, 0.0))
                    if tf == "5m":
                        cands.append(ada_like(sym, tag, tf, rc, zmax, hold, tp, 0.0002))

    for sym, tag, tf, hold in [
        ("BTC-USDT-SWAP", "btc", "5m", 14),
        ("BTC-USDT-SWAP", "btc", "15m", 16),
        ("XAU-USDT-SWAP", "xau", "15m", 16),
        ("CL-USDT-SWAP", "cl", "5m", 14),
    ]:
        for rc in (58, 60, 55):
            for zmin in (-0.5, 0.0, 0.5):
                cands.append(short_pb(sym, tag, tf, rc, zmin, hold, 40 if tf == "5m" else 42))

    for sym, tag in [("LTC-USDT-SWAP", "ltc"), ("CL-USDT-SWAP", "cl"),
                     ("XAG-USDT-SWAP", "xag"), ("BTC-USDT-SWAP", "btc")]:
        for z, rsi, slope, hold, tp in [
            (1.25, 75, 0.006, 48, 40), (1.25, 75, 0.006, 36, 38),
            (1.35, 78, 0.005, 36, 40), (1.15, 72, 0.008, 48, 42),
            (1.5, 80, 0.004, 24, 35),
        ]:
            cands.append(exh_short(sym, tag, z, rsi, slope, hold, tp))

    seen = set(); uniq = []
    for c in cands:
        if c["key"] in seen:
            continue
        seen.add(c["key"]); uniq.append(c)
    cands = uniq
    print("CANDS", len(cands), flush=True)

    scored = []
    for i, obj in enumerate(cands):
        try:
            definition = dsl.validate_strategy(obj)
        except Exception:
            continue
        ok, metrics, reason = pipeline.safety_screen_candidate({
            "dsl": definition,
            "symbol": definition["supported_instruments"][0],
            "timeframe": definition["timeframe"],
        })
        if not ok:
            continue
        n = int(metrics.get("trades") or 0)
        wr = float(metrics.get("win_rate") or 0)
        mean = float(metrics.get("mean_net") or 0)
        st = int(metrics.get("max_loss_streak") or 99)
        if n < 10 or wr < 54 or mean <= 0 or st > 4:
            continue
        score = wr + mean * 900 - st * 4 + min(n, 120) * 0.1 + (
            20 if metrics.get("fold_ok_advisory") else 0)
        scored.append({
            "score": score, "dsl": definition,
            "metrics": {
                "trades": n, "wr": wr, "mean": mean, "streak": st,
                "ret": metrics.get("total_return_pct"),
                "fold_ok": metrics.get("fold_ok_advisory"),
            },
        })
        if (i + 1) % 25 == 0:
            print("screened", i + 1, "kept", len(scored), flush=True)

    scored.sort(key=lambda x: -x["score"])
    print("KEPT", len(scored), flush=True)
    for row in scored[:15]:
        d = row["dsl"]; m = row["metrics"]
        print("CAND", d["key"], d["supported_instruments"][0], d["timeframe"],
              m, "score", round(row["score"], 2), flush=True)

    (OUTDIR / "candidates_kept_b.json").write_text(json.dumps([
        {"key": r["dsl"]["key"], "score": r["score"],
         "metrics": r["metrics"], "dsl": r["dsl"]}
        for r in scored[:25]
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    def wait_glm(max_min=10):
        """Probe GLM briefly; do not block on dead ChatGPT 429 loops."""
        deadline = time.time() + max_min * 60
        while time.time() < deadline:
            row = ai.theoretical_review_one(
                "glm", {"key": "ping725b", "name": "ping"},
                {"safety_metrics": {
                    "trades": 20, "win_rate": 62,
                    "empirical_win_rate": 62, "max_loss_streak": 2},
                 "instrument": {"symbol": "BTC-USDT-SWAP", "timeframe": "5m"}})
            ok = bool(row.get("ok"))
            reason = str(row.get("reason") or "")
            log({"event": "glm_probe", "ok": ok, "reason": reason[:160],
                 "model": row.get("model")})
            if ok:
                return True
            # Only retry rate-limits; other errors fail fast.
            if "429" not in reason and "Too Many" not in reason:
                return False
            time.sleep(30)
        return False

    passed = None
    attempted = []
    picked = []
    seen_sym = set()
    for r in scored:
        sym = r["dsl"]["supported_instruments"][0]
        if sym not in seen_sym or len(picked) < 12:
            picked.append(r)
            seen_sym.add(sym)
        if len(picked) >= 12:
            break

    cg_ready = wait_glm(10)
    log({"event": "glm_ready", "ok": cg_ready})

    for row in picked:
        definition = row["dsl"]
        print("SUBMIT", definition["key"], flush=True)
        out = codex.submit_codex_strategy(definition, meta={
            "symbol": definition["supported_instruments"][0],
            "timeframe": definition["timeframe"],
            "thesis": definition.get("description"),
            "author": "codex_0725_train1",
        }, dry_run=False)
        ai_rev = out.get("ai_review") or {}
        reviews = ai_rev.get("reviews") or []
        infra = any(
            (not r.get("ok")) and (
                "429" in str(r.get("reason") or "")
                or "调用失败" in str(r.get("reason") or ""))
            for r in reviews)
        slim = {
            "ok": out.get("ok"), "key": definition["key"],
            "pushed": out.get("pushed"),
            "avg": ai_rev.get("ai_theoretical_wr_avg"),
            "fail": ai_rev.get("fail_reasons"), "infra": infra,
            "metrics": row["metrics"],
            "reviews": [{
                "p": r.get("provider"), "d": r.get("decision"),
                "wr": r.get("theoretical_win_rate_pct"),
                "risk": r.get("stop_cluster_risk"),
                "prob": r.get("stop_cluster_prob"), "ok": r.get("ok"),
                "reason": str(r.get("reason") or "")[:80],
            } for r in reviews],
        }
        log({"event": "submit_result", "out": slim})
        attempted.append(slim)
        (OUTDIR / "submit_attempts_b.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2), encoding="utf-8")
        if out.get("ok") and out.get("pushed"):
            passed = out
            break
        if infra:
            print("INFRA wait 15m then retry same", flush=True)
            time.sleep(900)
            out2 = codex.submit_codex_strategy(definition, meta={
                "symbol": definition["supported_instruments"][0],
                "timeframe": definition["timeframe"],
                "thesis": definition.get("description"),
                "author": "codex_0725_train1",
            }, dry_run=False)
            ai2 = out2.get("ai_review") or {}
            slim2 = {
                "ok": out2.get("ok"), "key": definition["key"],
                "pushed": out2.get("pushed"),
                "avg": ai2.get("ai_theoretical_wr_avg"),
                "fail": ai2.get("fail_reasons"),
            }
            log({"event": "submit_retry", "out": slim2})
            attempted.append(slim2)
            if out2.get("ok") and out2.get("pushed"):
                passed = out2
                break
            continue
        time.sleep(10)

    (OUTDIR / "final_status_b.json").write_text(json.dumps({
        "time": now(), "kept": len(scored), "attempted_n": len(attempted),
        "passed": bool(passed), "passed_key": (passed or {}).get("key"),
        "queued": (passed or {}).get("queued"), "attempted": attempted,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("FINAL_PASSED", bool(passed), (passed or {}).get("key"), flush=True)
    if passed:
        try:
            import auto_trade_formal_notify as n
            
            try:
                import auto_trade_strategy_titles as titles
                shown = titles.short_strategy_title(
                    passed.get("key"),
                    ((passed.get("dsl") or {}).get("name")
                     or (passed.get("queued") or {}).get("name")))
            except Exception:
                shown = passed.get("key")
            n.send_message(
                "【7.25训练1产出】\n策略已三AI通过并进入人工确认队列\n%s\n时间: %s"
                % (shown, now()),
                kind="codex_strategy_review",
                meta={"key": passed.get("key")},
            )
        except Exception as e:
            print("wx_fail", e, flush=True)


if __name__ == "__main__":
    main()
