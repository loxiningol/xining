# -*- coding: utf-8 -*-
"""7.25 第二次独立训练：创造策略直到三AI(DeepSeek/Qwen/GLM)全通过并入队。"""
from __future__ import print_function

import copy
import json
import time
from datetime import datetime
from pathlib import Path

import auto_trade_codex_strategy_review as codex
import auto_trade_human_confirm_pipeline as pipeline
import auto_trade_strategy_dsl as dsl
import auto_trade_strategy_ecosystem as eco

OUTDIR = Path("/root/auto_trade/codex_0725_train2")
OUTDIR.mkdir(parents=True, exist_ok=True)
AUDIT = OUTDIR / "train_audit.jsonl"
BARS = 12000
EXISTING_SKIP = {
    "codex0725_ada5_trendpb_r42_z2p0_h14",
    "ada5_z20_t60_prev_h14_0724k",
}


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(row):
    row = dict(row)
    row["time"] = now()
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)


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


def fade_dsl(symbol, tag, z, rsi, slope, hold, tp):
    key = "codex0725t2_%s5_fade_z%s_r%s_h%s" % (
        tag, str(z).replace(".", "p"), int(rsi), hold)
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key,
        "name": "%s5冲高衰竭·0725T2" % tag.upper(),
        "direction": "short",
        "timeframe": "5m",
        "supported_instruments": [symbol],
        "max_hold_bars": int(hold),
        "description": (
            "%s 5m冲高衰竭做空：z20>=%s RSI>=%s 阴线 K下行 H1斜率<=%s；"
            "RSI<=%s止盈；上破prev_high20失效。Codex 0725 train2。"
            % (symbol.split("-")[0], z, rsi, slope, tp)
        ),
        "origin": "codex_0725_train2",
        "version": "0725t2",
        "entry": {"all": [
            {"id": "z", "left": {"feature": "z20"}, "op": "gte",
             "right": {"value": float(z)}},
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "gte",
             "right": {"value": float(rsi)}},
            {"id": "bear", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "open"}},
            {"id": "kdown", "left": {"feature": "k"}, "op": "lt",
             "right": {"feature": "k", "offset": 1}},
            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "lte",
             "right": {"value": float(slope)}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "lte",
             "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
    }


def reclaim_dsl(symbol, tag, z, rsi, slope, hold, tp):
    key = "codex0725t2_%s5_reclaim_z%s_r%s_h%s" % (
        tag, str(z).replace(".", "p").replace("-", "m"), int(rsi), hold)
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key,
        "name": "%s5跌深反抽·0725T2" % tag.upper(),
        "direction": "long",
        "timeframe": "5m",
        "supported_instruments": [symbol],
        "max_hold_bars": int(hold),
        "description": (
            "%s 5m跌深反抽：z20<=%s RSI<=%s 阳线 K上行 H1斜率>=%s；"
            "RSI>=%s止盈；下破prev_low20失效。Codex 0725 train2。"
            % (symbol.split("-")[0], z, rsi, slope, tp)
        ),
        "origin": "codex_0725_train2",
        "version": "0725t2",
        "entry": {"all": [
            {"id": "z", "left": {"feature": "z20"}, "op": "lte",
             "right": {"value": float(z)}},
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "lte",
             "right": {"value": float(rsi)}},
            {"id": "bull", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "open"}},
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


def all_providers_approve(ai_rev):
    reviews = ai_rev.get("reviews") or []
    by_p = {r.get("provider"): r for r in reviews}
    need = ("deepseek", "qwen", "glm")
    for p in need:
        row = by_p.get(p) or {}
        if not row.get("ok"):
            return False
        if str(row.get("decision") or "").upper() != "APPROVE":
            return False
    return bool(ai_rev.get("approved")) and len(reviews) >= 2


def build_candidates(base):
    cands = []
    # Focused ports of proven ada family (exclude live exact key via EXISTING_SKIP)
    ports = [
        ("ADA-USDT-SWAP", "ada", "5m", 14),
        ("ETH-USDT-SWAP", "eth", "5m", 14),
        ("SOL-USDT-SWAP", "sol", "5m", 14),
        ("BNB-USDT-SWAP", "bnb", "5m", 14),
        ("XAG-USDT-SWAP", "xag", "5m", 14),
        ("LTC-USDT-SWAP", "ltc", "5m", 14),
        ("XAU-USDT-SWAP", "xau", "15m", 16),
    ]
    for sym, tag, tf, hold0 in ports:
        for rc in (40, 42, 44):
            for zmax in (1.8, 2.0, 2.2, 2.5):
                for tp in (58, 60, 62):
                    for hold in ((12, 14, 16) if tag == "ada" else (hold0,)):
                        obj = copy.deepcopy(base)
                        obj["key"] = "codex0725t2_%s%s_trendpb_r%s_z%s_h%s" % (
                            tag, tf, rc, str(zmax).replace(".", "p"), hold)
                        obj["name"] = "%s%s顺势回升·0725T2" % (tag.upper(), tf)
                        obj["supported_instruments"] = [sym]
                        obj["timeframe"] = tf
                        obj["max_hold_bars"] = int(hold)
                        obj["origin"] = "codex_0725_train2"
                        obj["version"] = "0725t2"
                        obj["description"] = (
                            "train2 ada移植 %s %s rc=%s z<%s hold=%s"
                            % (sym, tf, rc, zmax, hold)
                        )
                        for cond in obj["entry"]["all"]:
                            if cond.get("id") == "rsi":
                                cond["right"] = {"value": float(rc)}
                            if cond.get("id") == "z":
                                cond["right"] = {"value": float(zmax)}
                        for cond in obj["exit"]["any"]:
                            if (cond.get("id") == "tp"
                                    or cond.get("role") == "take_profit"):
                                if "rsi" in str(cond.get("left")):
                                    cond["right"] = {"value": float(tp)}
                        if obj["key"] in EXISTING_SKIP:
                            continue
                        # skip exact live twin under different prefix if params match
                        if (tag == "ada" and rc == 42 and float(zmax) == 2.0
                                and int(hold) == 14):
                            continue
                        cands.append(obj)

    # Lean fade / reclaim
    for sym, tag in [
        ("LTC-USDT-SWAP", "ltc"),
        ("NG-USDT-SWAP", "ng"),
        ("ETH-USDT-SWAP", "eth"),
        ("SOL-USDT-SWAP", "sol"),
    ]:
        for z in (1.15, 1.25, 1.35):
            for rsi in (72, 75, 78):
                for slope in (0.006, 0.004):
                    for hold in (36, 48):
                        for tp in (38, 40):
                            cands.append(
                                fade_dsl(sym, tag, z, rsi, slope, hold, tp))
        for z in (-1.15, -1.25, -1.35):
            for rsi in (30, 32):
                for slope in (-0.004, -0.002):
                    for hold in (36, 48):
                        for tp in (60, 62):
                            cands.append(
                                reclaim_dsl(sym, tag, z, rsi, slope, hold, tp))
    return cands


def screen(uniq, get_frame):
    scored = []
    for i, obj in enumerate(uniq):
        try:
            definition = dsl.validate_strategy(obj)
        except Exception:
            continue
        try:
            sym = definition["supported_instruments"][0]
            tf = definition["timeframe"]
            frame, fr = get_frame(sym, tf)
            t0 = time.time()
            res = dsl.backtest_dsl(
                frame, definition,
                leverage=20, stop_loss_pct=0.009,
                fee_rate_per_side=float(fr.get("fee_rate_per_side") or 0.0005),
                slippage_rate_per_side=float(
                    fr.get("slippage_rate_per_side") or 0.0002),
                half_spread_rate_per_side=float(
                    fr.get("half_spread_rate_per_side") or 0),
                impact_rate_per_side=float(fr.get("impact_rate_per_side") or 0),
                latency_rate_per_side=float(
                    fr.get("latency_rate_per_side") or 0),
                funding_rate_per_8h=float(fr.get("funding_rate_per_8h") or 0),
                friction_scenario="observed_base",
            )
            m = metrics(res.get("trades") or [], res)
            m["bt_sec"] = round(time.time() - t0, 2)
        except Exception as exc:
            if (i + 1) % 50 == 0:
                print("bt_err", i + 1, exc, flush=True)
            continue
        n, wr, mean, st = m["trades"], m["wr"], m["mean"], m["streak"]
        # Prefer AI-passable evidence
        if n < 20 or wr < 58 or mean <= 0 or st > 3:
            if (i + 1) % 40 == 0:
                print("progress", i + 1, "/", len(uniq),
                      "kept", len(scored), flush=True)
            continue
        if not m.get("fold_ok") and (n < 28 or wr < 62):
            continue
        score = (
            wr + mean * 900 - st * 4 + min(n, 200) * 0.3
            + (35 if m.get("fold_ok") else 0)
        )
        scored.append({"score": score, "dsl": definition, "metrics": m})
        print("KEEP", definition["key"], m, "score", round(score, 2), flush=True)
        if (i + 1) % 40 == 0:
            print("progress", i + 1, "/", len(uniq),
                  "kept", len(scored), flush=True)
    scored.sort(key=lambda x: -x["score"])
    return scored


def submit_until_pass(pool, max_attempts=12):
    passed = None
    attempted = []
    log({"event": "submit_start_train2", "n": min(max_attempts, len(pool))})
    for row in pool[:max_attempts]:
        definition = row["dsl"]
        print("SUBMIT", definition["key"], flush=True)
        out = codex.submit_codex_strategy(definition, meta={
            "symbol": definition["supported_instruments"][0],
            "timeframe": definition["timeframe"],
            "thesis": definition.get("description"),
            "author": "codex_0725_train2",
        }, dry_run=False)
        ai_rev = out.get("ai_review") or {}
        reviews = ai_rev.get("reviews") or []
        slim = {
            "ok": out.get("ok"),
            "key": definition["key"],
            "name": definition.get("name"),
            "pushed": out.get("pushed"),
            "queued": out.get("queued"),
            "avg": ai_rev.get("ai_theoretical_wr_avg"),
            "fail": ai_rev.get("fail_reasons"),
            "voters": ai_rev.get("voting_providers"),
            "three_approve": all_providers_approve(ai_rev),
            "metrics": row["metrics"],
            "reviews": [{
                "p": r.get("provider"), "d": r.get("decision"),
                "wr": r.get("theoretical_win_rate_pct"),
                "risk": r.get("stop_cluster_risk"),
                "prob": r.get("stop_cluster_prob"), "ok": r.get("ok"),
                "reason": str(r.get("reason") or "")[:140],
            } for r in reviews],
        }
        log({"event": "submit_result_train2", "out": slim})
        attempted.append(slim)
        (OUTDIR / "submit_attempts.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2), encoding="utf-8")
        if (out.get("ok") and out.get("pushed")
                and all_providers_approve(ai_rev)):
            passed = out
            passed["name"] = definition.get("name")
            passed["dsl"] = definition
            break
        time.sleep(6)
    return passed, attempted


def main():
    store = json.loads(
        Path("/root/strategy_configs/ai_dsl_strategies.json").read_text(
            encoding="utf-8")
    )
    strats = {
        s["key"]: s
        for s in (store.get("strategies") or [])
        if isinstance(s, dict) and s.get("key")
    }
    base = strats.get("ada5_z20_t60_prev_h14_0724k") or strats.get(
        "codex0725_ada5_trendpb_r42_z2p0_h14")
    if not base:
        raise SystemExit("missing ada base dsl")
    print("base", base["key"], flush=True)

    frames = {}
    frs = {}

    def get_frame(sym, tf):
        k = (sym, tf)
        if k not in frames:
            f = pipeline._frame(sym, tf)
            if len(f) > BARS:
                f = f.iloc[-BARS:]
            frames[k] = f
            frs[sym] = eco._friction_scenario(sym, "observed_base")
            print("FRAME", sym, tf, len(f), flush=True)
        return frames[k], frs[sym]

    cands = build_candidates(base)
    seen = set()
    uniq = []
    for c in cands:
        if c["key"] in seen or c["key"] in EXISTING_SKIP:
            continue
        seen.add(c["key"])
        uniq.append(c)
    print("CANDS", len(uniq), flush=True)

    scored = screen(uniq, get_frame)
    print("KEPT", len(scored), flush=True)
    for row in scored[:15]:
        print("CAND", row["dsl"]["key"], row["metrics"],
              "score", round(row["score"], 2), flush=True)
    (OUTDIR / "candidates_kept.json").write_text(json.dumps([
        {
            "key": r["dsl"]["key"], "score": r["score"],
            "metrics": r["metrics"], "dsl": r["dsl"],
        }
        for r in scored[:30]
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    # If strict keep empty, loosen once and merge (do not discard strong keeps)
    if len(scored) < 3:
        print("LOOSEN_SCREEN_APPEND", flush=True)
        have = {r["dsl"]["key"] for r in scored}
        loose = []
        for i, obj in enumerate(uniq):
            if obj.get("key") in have:
                continue
            try:
                definition = dsl.validate_strategy(obj)
                frame, fr = get_frame(
                    definition["supported_instruments"][0],
                    definition["timeframe"])
                res = dsl.backtest_dsl(
                    frame, definition, leverage=20, stop_loss_pct=0.009,
                    fee_rate_per_side=float(fr.get("fee_rate_per_side") or 0.0005),
                    slippage_rate_per_side=float(
                        fr.get("slippage_rate_per_side") or 0.0002),
                    half_spread_rate_per_side=float(
                        fr.get("half_spread_rate_per_side") or 0),
                    impact_rate_per_side=float(
                        fr.get("impact_rate_per_side") or 0),
                    latency_rate_per_side=float(
                        fr.get("latency_rate_per_side") or 0),
                    funding_rate_per_8h=float(fr.get("funding_rate_per_8h") or 0),
                    friction_scenario="observed_base",
                )
                m = metrics(res.get("trades") or [], res)
            except Exception:
                continue
            if (m["trades"] >= 18 and m["wr"] >= 56
                    and m["mean"] > 0 and m["streak"] <= 4):
                score = m["wr"] + m["mean"] * 900 - m["streak"] * 4 + min(
                    m["trades"], 200) * 0.25 + (25 if m.get("fold_ok") else 0)
                loose.append({"score": score, "dsl": definition, "metrics": m})
                print("LOOSE_KEEP", definition["key"], m, flush=True)
            if (i + 1) % 50 == 0:
                print("loose_progress", i + 1, "kept", len(loose), flush=True)
            # Enough extras for submit queue
            if len(scored) + len(loose) >= 12:
                break
        scored = scored + loose
        scored.sort(key=lambda x: -x["score"])
        print("MERGED_KEPT", len(scored), flush=True)

    passed, attempted = submit_until_pass(scored, max_attempts=15)

    # Second wave: if still fail, try top fold_ok only with more attempts later
    if not passed and scored:
        print("RETRY_TOP_FOLD", flush=True)
        fold_pool = [r for r in scored if r["metrics"].get("fold_ok")] or scored
        passed2, attempted2 = submit_until_pass(fold_pool[:8], max_attempts=8)
        attempted.extend(attempted2)
        if passed2:
            passed = passed2

    (OUTDIR / "final_status.json").write_text(json.dumps({
        "time": now(),
        "kept": len(scored),
        "passed": bool(passed),
        "passed_key": (passed or {}).get("key"),
        "passed_name": (passed or {}).get("name"),
        "queued": (passed or {}).get("queued"),
        "attempted": attempted,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("FINAL_PASSED", bool(passed), (passed or {}).get("key"),
          (passed or {}).get("name"), flush=True)

    if passed:
        try:
            import auto_trade_formal_notify as n
            import auto_trade_strategy_titles as titles
            shown = titles.short_strategy_title(
                passed.get("key"), passed.get("name"))
            n.send_message(
                "【7.25训练2产出】\n"
                "策略已三AI(DeepSeek/Qwen/GLM)通过并进入人工确认队列\n"
                "%s\n时间: %s" % (shown, now()),
                kind="codex_strategy_review",
                meta={"key": passed.get("key"), "strategy_name": passed.get("name")},
            )
            print("wx_ok", shown, flush=True)
        except Exception as e:
            print("wx_fail", e, flush=True)


if __name__ == "__main__":
    main()
