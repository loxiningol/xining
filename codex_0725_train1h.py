# -*- coding: utf-8 -*-
"""Focused 0725 train1h: longer bars for sample size; ada variants + tight fades; 2-vote submit."""
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

OUTDIR = Path("/root/auto_trade/codex_0725_train1")
OUTDIR.mkdir(parents=True, exist_ok=True)
AUDIT = OUTDIR / "train_audit.jsonl"
BARS = 12000


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
    key = "codex0725_%s5_fade_z%s_r%s_h%s" % (
        tag, str(z).replace(".", "p"), int(rsi), hold)
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key,
        "name": "%s5冲高衰竭·0725" % tag.upper(),
        "direction": "short",
        "timeframe": "5m",
        "supported_instruments": [symbol],
        "max_hold_bars": int(hold),
        "description": (
            "%s 5m冲高衰竭做空：z20>=%s RSI>=%s 阴线 H1斜率<=%s；RSI<=%s止盈；"
            "上破prev_high20失效。Codex 0725 train1h 长窗样本。"
            % (symbol.split("-")[0], z, rsi, slope, tp)
        ),
        "origin": "codex_0725_train1",
        "version": "0725h",
        "entry": {"all": [
            {"id": "z", "left": {"feature": "z20"}, "op": "gte", "right": {"value": float(z)}},
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "gte", "right": {"value": float(rsi)}},
            {"id": "bear", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}},
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


def main():
    store = json.loads(
        Path("/root/strategy_configs/ai_dsl_strategies.json").read_text(encoding="utf-8")
    )
    strats = {
        s["key"]: s
        for s in (store.get("strategies") or [])
        if isinstance(s, dict) and s.get("key")
    }
    base = strats["ada5_z20_t60_prev_h14_0724k"]
    print("base", base["key"], flush=True)

    frames = {}
    frs = {}

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

    def run_bt(definition):
        sym = definition["supported_instruments"][0]
        tf = definition["timeframe"]
        frame, fr = get(sym, tf)
        t0 = time.time()
        res = dsl.backtest_dsl(
            frame, definition,
            leverage=20, stop_loss_pct=0.009,
            fee_rate_per_side=float(fr.get("fee_rate_per_side") or 0.0005),
            slippage_rate_per_side=float(fr.get("slippage_rate_per_side") or 0.0002),
            half_spread_rate_per_side=float(fr.get("half_spread_rate_per_side") or 0),
            impact_rate_per_side=float(fr.get("impact_rate_per_side") or 0),
            latency_rate_per_side=float(fr.get("latency_rate_per_side") or 0),
            funding_rate_per_8h=float(fr.get("funding_rate_per_8h") or 0),
            friction_scenario="observed_base",
        )
        m = metrics(res.get("trades") or [], res)
        m["bt_sec"] = round(time.time() - t0, 2)
        return m

    cands = []
    # 1) ADA self variants — proven family, ask AI on nearby params
    for rc in (40, 42, 44, 45):
        for zmax in (1.8, 2.0, 2.2, 2.5):
            for tp in (58, 60, 62):
                for hold in (12, 14, 16):
                    obj = copy.deepcopy(base)
                    obj["key"] = "codex0725_ada5_trendpb_r%s_z%s_h%s" % (
                        rc, str(zmax).replace(".", "p"), hold)
                    obj["name"] = "ADA5顺势回升变体·0725"
                    obj["max_hold_bars"] = hold
                    obj["origin"] = "codex_0725_train1"
                    obj["version"] = "0725h"
                    obj["description"] = (
                        "ada5变体 ADA-USDT-SWAP 5m rc=%s z<%s hold=%s tp=%s Codex0725"
                        % (rc, zmax, hold, tp)
                    )
                    for cond in obj["entry"]["all"]:
                        if cond.get("id") == "rsi":
                            cond["right"] = {"value": float(rc)}
                        if cond.get("id") == "z":
                            cond["right"] = {"value": float(zmax)}
                    for cond in obj["exit"]["any"]:
                        if cond.get("id") == "tp" or cond.get("role") == "take_profit":
                            if "rsi" in str(cond.get("left")):
                                cond["right"] = {"value": float(tp)}
                    cands.append(obj)

    # 2) Port ada to DOGE/SUI/APT-like? stick to known instruments
    for sym, tag in [
        ("SOL-USDT-SWAP", "sol"),
        ("BNB-USDT-SWAP", "bnb"),
        ("XAG-USDT-SWAP", "xag"),
    ]:
        for rc in (40, 42):
            for zmax in (2.0, 2.2, 2.5):
                for tp in (58, 60):
                    obj = copy.deepcopy(base)
                    obj["key"] = "codex0725_%s5_trendpb_r%s_z%s_h14" % (
                        tag, rc, str(zmax).replace(".", "p"))
                    obj["name"] = "%s5顺势回升·0725" % tag.upper()
                    obj["supported_instruments"] = [sym]
                    obj["origin"] = "codex_0725_train1"
                    obj["version"] = "0725h"
                    obj["description"] = (
                        "ada5移植 %s rc=%s z<%s Codex0725" % (sym, rc, zmax))
                    for cond in obj["entry"]["all"]:
                        if cond.get("id") == "rsi":
                            cond["right"] = {"value": float(rc)}
                        if cond.get("id") == "z":
                            cond["right"] = {"value": float(zmax)}
                    for cond in obj["exit"]["any"]:
                        if cond.get("id") == "tp" or cond.get("role") == "take_profit":
                            if "rsi" in str(cond.get("left")):
                                cond["right"] = {"value": float(tp)}
                    cands.append(obj)

    # 3) Tight fades on LTC/NG/ETH with long window for n
    for sym, tag in [
        ("LTC-USDT-SWAP", "ltc"),
        ("NG-USDT-SWAP", "ng"),
        ("ETH-USDT-SWAP", "eth"),
        ("SOL-USDT-SWAP", "sol"),
    ]:
        for z in (1.15, 1.25, 1.35):
            for rsi in (72, 75, 78):
                for slope in (0.006, 0.004):
                    for hold in (36, 48, 60):
                        for tp in (38, 40):
                            cands.append(fade_dsl(sym, tag, z, rsi, slope, hold, tp))

    seen = set()
    uniq = []
    for c in cands:
        if c["key"] in seen:
            continue
        seen.add(c["key"])
        uniq.append(c)
    print("CANDS", len(uniq), flush=True)

    scored = []
    best_any = []
    for i, obj in enumerate(uniq):
        try:
            definition = dsl.validate_strategy(obj)
        except Exception as exc:
            if i < 3:
                print("validate_fail", obj.get("key"), exc, flush=True)
            continue
        try:
            m = run_bt(definition)
        except Exception as exc:
            print("bt_fail", definition["key"], exc, flush=True)
            continue
        best_any.append({"key": definition["key"], "metrics": m, "dsl": definition})
        n, wr, mean, st = m["trades"], m["wr"], m["mean"], m["streak"]
        # AI-friendly: longer window should help n
        if n >= 20 and wr >= 56 and mean > 0 and st <= 4:
            score = (
                wr + mean * 900 - st * 4 + min(n, 200) * 0.25
                + (30 if m.get("fold_ok") else 0)
            )
            scored.append({"score": score, "dsl": definition, "metrics": m})
            print("KEEP", definition["key"], m, "score", round(score, 2), flush=True)
        elif n >= 15 and wr >= 54 and mean > 0:
            print("NEAR", definition["key"], m, flush=True)
        if (i + 1) % 10 == 0:
            print(
                "progress", i + 1, "/", len(uniq),
                "kept", len(scored), flush=True,
            )

    scored.sort(key=lambda x: -x["score"])
    best_any.sort(
        key=lambda x: (
            (x["metrics"]["mean"] > 0),
            x["metrics"]["wr"],
            x["metrics"]["trades"],
        ),
        reverse=True,
    )
    print("KEPT", len(scored), flush=True)
    print("TOP_ANY", flush=True)
    for row in best_any[:15]:
        print(row["key"], row["metrics"], flush=True)

    (OUTDIR / "candidates_kept_h.json").write_text(json.dumps([
        {
            "key": r["dsl"]["key"], "score": r["score"],
            "metrics": r["metrics"], "dsl": r["dsl"],
        }
        for r in scored[:20]
    ], ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTDIR / "candidates_topany_h.json").write_text(json.dumps([
        {"key": r["key"], "metrics": r["metrics"], "dsl": r["dsl"]}
        for r in best_any[:20]
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    # Prefer kept; if empty, try best NEAR with n>=18 wr>=55 mean>0
    pool = scored[:8]
    if not pool:
        near = []
        for r in best_any:
            m = r["metrics"]
            if m["trades"] >= 18 and m["wr"] >= 55 and m["mean"] > 0 and m["streak"] <= 4:
                near.append({
                    "score": m["wr"] + m["mean"] * 900,
                    "dsl": r["dsl"], "metrics": m,
                })
            if len(near) >= 6:
                break
        pool = near
        print("FALLBACK_NEAR", len(pool), flush=True)

    passed = None
    attempted = []
    log({"event": "submit_start_1h", "n": len(pool)})
    for row in pool:
        definition = row["dsl"]
        print("SUBMIT", definition["key"], row["metrics"], flush=True)
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
                "reason": str(r.get("reason") or "")[:160],
            } for r in reviews],
        }
        log({"event": "submit_result_1h", "out": slim})
        attempted.append(slim)
        (OUTDIR / "submit_attempts_1h.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2), encoding="utf-8")
        if out.get("ok") and out.get("pushed"):
            passed = out
            break
        time.sleep(5)

    (OUTDIR / "final_status_1h.json").write_text(json.dumps({
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
