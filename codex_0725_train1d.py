# -*- coding: utf-8 -*-
"""Fast Codex 0725 train1d: cache frames, skip fold in prefilter, 2-vote submit."""
from __future__ import print_function
import json
import time
from datetime import datetime
from pathlib import Path

import auto_trade_codex_strategy_review as codex
import auto_trade_human_confirm_pipeline as pipeline
import auto_trade_strategy_dsl as dsl
import auto_trade_strategy_ecosystem as eco

SCHEMA = "qiyu_strategy_dsl_v1"
OUTDIR = Path("/root/auto_trade/codex_0725_train1")
OUTDIR.mkdir(parents=True, exist_ok=True)
AUDIT = OUTDIR / "train_audit.jsonl"
LEVERAGE = getattr(pipeline, "LEVERAGE", 20)
STOP = getattr(pipeline, "STOP_LOSS_PCT", 0.009)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(row):
    row = dict(row)
    row["time"] = now()
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)


def fade(symbol, tag, z, rsi, slope, hold, tp, extra=""):
    key = "codex0725_%s5_fade_z%s_r%s_h%s%s" % (
        tag, str(z).replace(".", "p"), int(rsi), hold, extra)
    entry = [
        {"id": "z", "left": {"feature": "z20"}, "op": "gte", "right": {"value": float(z)}},
        {"id": "rsi", "left": {"feature": "rsi14"}, "op": "gte", "right": {"value": float(rsi)}},
        {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "lte", "right": {"value": float(slope)}},
        {"id": "bear", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}},
    ]
    if extra == "_kd":
        entry.append({
            "id": "kdown", "left": {"feature": "k"}, "op": "lt",
            "right": {"feature": "k", "offset": 1},
        })
    return {
        "schema": SCHEMA, "key": key, "name": "%s5冲高衰竭·0725" % tag.upper(),
        "direction": "short", "timeframe": "5m",
        "supported_instruments": [symbol], "max_hold_bars": int(hold),
        "description": (
            "%s 5m冲高衰竭做空 z>=%s rsi>=%s slope<=%s hold=%s. Codex0725 train1d."
            % (symbol.split("-")[0], z, rsi, slope, hold)),
        "origin": "codex_0725_train1", "version": "0725f",
        "entry": {"all": entry},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "lte",
             "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
    }


def reclaim(symbol, tag, z, rsi, slope, hold, tp):
    key = "codex0725_%s5_reclaim_z%s_r%s_h%s" % (
        tag, str(z).replace(".", "p").replace("-", "m"), int(rsi), hold)
    return {
        "schema": SCHEMA, "key": key, "name": "%s5跌深反抽·0725" % tag.upper(),
        "direction": "long", "timeframe": "5m",
        "supported_instruments": [symbol], "max_hold_bars": int(hold),
        "description": (
            "%s 5m跌深反抽 z<=%s rsi<=%s. Codex0725 train1d."
            % (symbol.split("-")[0], z, rsi)),
        "origin": "codex_0725_train1", "version": "0725f",
        "entry": {"all": [
            {"id": "z", "left": {"feature": "z20"}, "op": "lte", "right": {"value": float(z)}},
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "lte", "right": {"value": float(rsi)}},
            {"id": "bull", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}},
            {"id": "kup", "left": {"feature": "k"}, "op": "gt",
             "right": {"feature": "k", "offset": 1}},
            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gte",
             "right": {"value": float(slope)}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "gte",
             "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "prev_low20"}, "role": "invalidation"},
        ]},
    }


def ada(symbol, tag, tf, rc, zmax, hold, tp):
    key = "codex0725_%s%s_trendpb_r%s_z%s_h%s" % (
        tag, tf, int(rc), str(zmax).replace(".", "p"), hold)
    return {
        "schema": SCHEMA, "key": key,
        "name": "%s%s顺势回升·0725" % (tag.upper(), tf),
        "direction": "long", "timeframe": tf,
        "supported_instruments": [symbol], "max_hold_bars": int(hold),
        "description": (
            "%s %s ada-like rc=%s z<%s. Codex0725 train1d."
            % (symbol.split("-")[0], tf, rc, zmax)),
        "origin": "codex_0725_train1", "version": "0725f",
        "entry": {"all": [
            {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "gt",
             "right": {"feature": "h1_ema53"}},
            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gt",
             "right": {"value": 0.0}},
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_above",
             "right": {"value": float(rc)}},
            {"id": "px", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "ema21"}},
            {"id": "z", "left": {"feature": "z20"}, "op": "lt",
             "right": {"value": float(zmax)}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "gte",
             "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "prev_low20"}, "role": "invalidation"},
        ]},
    }


def _metrics_from_trades(trades, result):
    pnls = [float(t.get("pnl_ratio") or 0.0) for t in trades]
    n = len(pnls)
    mean = (sum(pnls) / float(n)) if n else 0.0
    wr = (sum(1 for p in pnls if p > 0) / float(n) * 100.0) if n else 0.0
    streak = mx = 0
    for p in pnls:
        if p <= 0:
            streak += 1
            mx = max(mx, streak)
        else:
            streak = 0
    return {
        "trades": n, "wr": wr, "mean": mean, "streak": mx,
        "ret": result.get("total_return_percent"),
    }


def main():
    # kill note: caller should stop train1c first
    cands = []
    for sym, tag in [
        ("LTC-USDT-SWAP", "ltc"), ("NG-USDT-SWAP", "ng"), ("ETH-USDT-SWAP", "eth"),
        ("SOL-USDT-SWAP", "sol"), ("XAG-USDT-SWAP", "xag"), ("CL-USDT-SWAP", "cl"),
    ]:
        for z in (1.05, 1.15, 1.25, 1.35):
            for rsi in (72, 74, 76, 78):
                for slope in (0.006, 0.004, 0.002):
                    for hold in (36, 48):
                        for tp in (38, 40):
                            cands.append(fade(sym, tag, z, rsi, slope, hold, tp, ""))
                            cands.append(fade(sym, tag, z, rsi, slope, hold, tp, "_kd"))
        for z in (-1.05, -1.15, -1.25, -1.35):
            for rsi in (28, 30, 32):
                for slope in (-0.004, -0.002, 0.0):
                    for hold in (36, 48):
                        for tp in (60, 62):
                            cands.append(reclaim(sym, tag, z, rsi, slope, hold, tp))
    for sym, tag, tf, hold in [
        ("BTC-USDT-SWAP", "btc", "5m", 14),
        ("ETH-USDT-SWAP", "eth", "5m", 14),
        ("LTC-USDT-SWAP", "ltc", "5m", 14),
        ("SOL-USDT-SWAP", "sol", "5m", 14),
        ("XAU-USDT-SWAP", "xau", "15m", 16),
    ]:
        for rc in (40, 42, 44):
            for zmax in (1.6, 2.0, 2.2):
                for tp in (58, 60):
                    cands.append(ada(sym, tag, tf, rc, zmax, hold, tp))

    seen = set()
    uniq = []
    for c in cands:
        if c["key"] in seen:
            continue
        seen.add(c["key"])
        uniq.append(c)
    print("CANDS", len(uniq), flush=True)

    frame_cache = {}
    fr_cache = {}

    def get_frame(symbol, timeframe):
        k = (symbol, timeframe)
        if k not in frame_cache:
            frame = pipeline._frame(symbol, timeframe)
            if hasattr(frame, "iloc") and len(frame) > 8000:
                frame = frame.iloc[-8000:]
            frame_cache[k] = frame
            fr_cache[symbol] = eco._friction_scenario(symbol, "observed_base")
            print("FRAME", symbol, timeframe, len(frame), flush=True)
        return frame_cache[k], fr_cache[symbol]

    scored = []
    t0 = time.time()
    for i, obj in enumerate(uniq):
        try:
            definition = dsl.validate_strategy(obj)
        except Exception:
            continue
        symbol = definition["supported_instruments"][0]
        timeframe = definition["timeframe"]
        try:
            frame, fr = get_frame(symbol, timeframe)
            result = dsl.backtest_dsl(
                frame, definition,
                leverage=LEVERAGE, stop_loss_pct=STOP,
                fee_rate_per_side=float(fr.get("fee_rate_per_side") or 0.0005),
                slippage_rate_per_side=float(fr.get("slippage_rate_per_side") or 0.0002),
                half_spread_rate_per_side=float(fr.get("half_spread_rate_per_side") or 0),
                impact_rate_per_side=float(fr.get("impact_rate_per_side") or 0),
                latency_rate_per_side=float(fr.get("latency_rate_per_side") or 0),
                funding_rate_per_8h=float(fr.get("funding_rate_per_8h") or 0),
                friction_scenario="observed_base",
            )
            m = _metrics_from_trades(result.get("trades") or [], result)
        except Exception as exc:
            if (i + 1) % 20 == 0:
                print("err", i + 1, exc, flush=True)
            continue
        n, wr, mean, st = m["trades"], m["wr"], m["mean"], m["streak"]
        # AI-friendly bar: enough samples + solid WR
        if n < 22 or wr < 57 or mean <= 0 or st > 4:
            continue
        score = wr + mean * 900 - st * 4 + min(n, 200) * 0.25
        scored.append({"score": score, "dsl": definition, "metrics": m})
        print(
            "KEEP", definition["key"], m, "score", round(score, 2), flush=True,
        )
        if (i + 1) % 25 == 0:
            print(
                "screened", i + 1, "kept", len(scored),
                "sec", round(time.time() - t0, 1), flush=True,
            )

    scored.sort(key=lambda x: -x["score"])
    print("KEPT", len(scored), "sec", round(time.time() - t0, 1), flush=True)
    for row in scored[:15]:
        d = row["dsl"]
        print(
            "CAND", d["key"], d["supported_instruments"][0],
            row["metrics"], "score", round(row["score"], 2), flush=True,
        )
    (OUTDIR / "candidates_kept_d.json").write_text(json.dumps([
        {
            "key": r["dsl"]["key"], "score": r["score"],
            "metrics": r["metrics"], "dsl": r["dsl"],
        }
        for r in scored[:25]
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    passed = None
    attempted = []
    log({"event": "submit_start_1d", "n": min(8, len(scored))})
    for row in scored[:8]:
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
        slim = {
            "ok": out.get("ok"),
            "key": definition["key"],
            "pushed": out.get("pushed"),
            "queued": out.get("queued"),
            "avg": ai_rev.get("ai_theoretical_wr_avg"),
            "fail": ai_rev.get("fail_reasons"),
            "voters": ai_rev.get("voting_providers"),
            "skipped": ai_rev.get("skipped_infra_providers"),
            "metrics": row["metrics"],
            "reviews": [{
                "p": r.get("provider"), "d": r.get("decision"),
                "wr": r.get("theoretical_win_rate_pct"),
                "risk": r.get("stop_cluster_risk"),
                "prob": r.get("stop_cluster_prob"), "ok": r.get("ok"),
                "reason": str(r.get("reason") or "")[:140],
            } for r in reviews],
        }
        log({"event": "submit_result_1d", "out": slim})
        attempted.append(slim)
        (OUTDIR / "submit_attempts_1d.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2), encoding="utf-8")
        if out.get("ok") and out.get("pushed"):
            passed = out
            break
        time.sleep(5)

    (OUTDIR / "final_status_1d.json").write_text(json.dumps({
        "time": now(), "kept": len(scored), "passed": bool(passed),
        "passed_key": (passed or {}).get("key"),
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
                "【7.25训练1产出】\n策略已两票通过并进入人工确认队列（ChatGPT忽略）\n%s\n时间: %s"
                % (shown, now()),
                kind="codex_strategy_review",
                meta={"key": passed.get("key")},
            )
            print("wx_ok", flush=True)
        except Exception as e:
            print("wx_fail", e, flush=True)


if __name__ == "__main__":
    main()
