# -*- coding: utf-8 -*-
"""Port proven ada5/ltc-fade DSLs to other symbols; submit with 2-vote gate."""
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


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(row):
    row = dict(row)
    row["time"] = now()
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)


def main():
    store = json.loads(
        Path("/root/strategy_configs/ai_dsl_strategies.json").read_text(encoding="utf-8")
    )
    strats = {
        s["key"]: s
        for s in (store.get("strategies") or [])
        if isinstance(s, dict) and s.get("key")
    }
    base = strats.get("ada5_z20_t60_prev_h14_0724k")
    fade = strats.get("ltc5_exhaustion_fade_short_ai")
    if not base:
        raise SystemExit("missing ada5 base")
    print("base_ok", base["key"], "fade", bool(fade), flush=True)

    frames = {}

    def frm(sym, tf):
        k = (sym, tf)
        if k not in frames:
            f = pipeline._frame(sym, tf)
            if len(f) > 8000:
                f = f.iloc[-8000:]
            frames[k] = f
            print("frame", sym, tf, len(f), flush=True)
        return frames[k]

    scored = []
    targets = [
        ("ETH-USDT-SWAP", "eth"),
        ("SOL-USDT-SWAP", "sol"),
        ("BNB-USDT-SWAP", "bnb"),
        ("XAG-USDT-SWAP", "xag"),
        ("LTC-USDT-SWAP", "ltc"),
        ("XAU-USDT-SWAP", "xau"),
    ]
    for sym, tag in targets:
        tf = "15m" if tag == "xau" else "5m"
        hold = 16 if tag == "xau" else 14
        for rc in (40, 42, 44):
            for zmax in (1.8, 2.0, 2.2, 2.5):
                for tp in (58, 60, 62):
                    obj = copy.deepcopy(base)
                    obj["key"] = "codex0725_%s%s_adaport_r%s_z%s_h%s" % (
                        tag, tf, rc, str(zmax).replace(".", "p"), hold)
                    obj["name"] = "%s%s顺势回升移植·0725" % (tag.upper(), tf)
                    obj["supported_instruments"] = [sym]
                    obj["timeframe"] = tf
                    obj["max_hold_bars"] = hold
                    obj["origin"] = "codex_0725_train1"
                    obj["version"] = "0725g"
                    obj["description"] = (
                        "ada5移植 %s %s rc=%s z<%s Codex0725" % (sym, tf, rc, zmax))
                    for cond in obj["entry"]["all"]:
                        if cond.get("id") == "rsi":
                            cond["right"] = {"value": float(rc)}
                        if cond.get("id") == "z":
                            cond["right"] = {"value": float(zmax)}
                    for cond in obj["exit"]["any"]:
                        if cond.get("id") == "tp" or cond.get("role") == "take_profit":
                            if "rsi" in str(cond.get("left")):
                                cond["right"] = {"value": float(tp)}
                    try:
                        definition = dsl.validate_strategy(obj)
                    except Exception:
                        continue
                    fr = eco._friction_scenario(sym, "observed_base")
                    t0 = time.time()
                    res = dsl.backtest_dsl(
                        frm(sym, tf), definition,
                        leverage=20, stop_loss_pct=0.009,
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
                    trades = res.get("trades") or []
                    pnls = [float(t.get("pnl_ratio") or 0) for t in trades]
                    n = len(pnls)
                    if n < 20:
                        continue
                    wr = sum(1 for p in pnls if p > 0) / float(n) * 100
                    mean = sum(pnls) / float(n)
                    st = mx = 0
                    for p in pnls:
                        if p <= 0:
                            st += 1
                            mx = max(mx, st)
                        else:
                            st = 0
                    if wr < 56 or mean <= 0 or mx > 4:
                        continue
                    fold_ok, _, _ = pipeline._five_fold_pass(trades, min_positive=4)
                    score = (
                        wr + mean * 900 - mx * 4 + min(n, 200) * 0.25
                        + (25 if fold_ok else 0)
                    )
                    row = {
                        "score": score,
                        "dsl": definition,
                        "metrics": {
                            "trades": n, "wr": wr, "mean": mean, "streak": mx,
                            "ret": res.get("total_return_percent"),
                            "fold_ok": fold_ok,
                            "bt_sec": round(time.time() - t0, 2),
                        },
                    }
                    scored.append(row)
                    print("KEEP", definition["key"], row["metrics"],
                          round(score, 2), flush=True)

    if fade:
        for sym, tag in [
            ("ETH-USDT-SWAP", "eth"), ("SOL-USDT-SWAP", "sol"),
            ("XAG-USDT-SWAP", "xag"), ("CL-USDT-SWAP", "cl"),
            ("BNB-USDT-SWAP", "bnb"),
        ]:
            for z in (1.15, 1.25, 1.35):
                for rsi in (72, 75, 78):
                    for slope in (0.004, 0.006):
                        for hold in (36, 48):
                            for tp in (38, 40):
                                obj = copy.deepcopy(fade)
                                obj["key"] = "codex0725_%s5_fadeport_z%s_r%s_h%s" % (
                                    tag, str(z).replace(".", "p"), int(rsi), hold)
                                obj["name"] = "%s5冲高衰竭移植·0725" % tag.upper()
                                obj["supported_instruments"] = [sym]
                                obj["max_hold_bars"] = hold
                                obj["origin"] = "codex_0725_train1"
                                obj["version"] = "0725g"
                                obj["description"] = (
                                    "ltc fade移植 %s z>=%s rsi>=%s" % (sym, z, rsi))
                                for cond in obj["entry"]["all"]:
                                    feat = str((cond.get("left") or {}).get("feature"))
                                    if feat == "z20":
                                        cond["right"] = {"value": float(z)}
                                    if feat == "rsi14" and cond.get("op") in ("gte", "gt"):
                                        cond["right"] = {"value": float(rsi)}
                                    if feat == "h1_slope4":
                                        cond["right"] = {"value": float(slope)}
                                for cond in obj["exit"]["any"]:
                                    if (cond.get("role") == "take_profit"
                                            or cond.get("id") == "tp"):
                                        cond["right"] = {"value": float(tp)}
                                try:
                                    definition = dsl.validate_strategy(obj)
                                except Exception:
                                    continue
                                fr = eco._friction_scenario(sym, "observed_base")
                                res = dsl.backtest_dsl(
                                    frm(sym, "5m"), definition,
                                    leverage=20, stop_loss_pct=0.009,
                                    fee_rate_per_side=float(
                                        fr.get("fee_rate_per_side") or 0.0005),
                                    slippage_rate_per_side=float(
                                        fr.get("slippage_rate_per_side") or 0.0002),
                                    half_spread_rate_per_side=float(
                                        fr.get("half_spread_rate_per_side") or 0),
                                    impact_rate_per_side=float(
                                        fr.get("impact_rate_per_side") or 0),
                                    latency_rate_per_side=float(
                                        fr.get("latency_rate_per_side") or 0),
                                    funding_rate_per_8h=float(
                                        fr.get("funding_rate_per_8h") or 0),
                                    friction_scenario="observed_base",
                                )
                                trades = res.get("trades") or []
                                pnls = [float(t.get("pnl_ratio") or 0) for t in trades]
                                n = len(pnls)
                                if n < 20:
                                    continue
                                wr = sum(1 for p in pnls if p > 0) / float(n) * 100
                                mean = sum(pnls) / float(n)
                                st = mx = 0
                                for p in pnls:
                                    if p <= 0:
                                        st += 1
                                        mx = max(mx, st)
                                    else:
                                        st = 0
                                if wr < 56 or mean <= 0 or mx > 4:
                                    continue
                                fold_ok, _, _ = pipeline._five_fold_pass(
                                    trades, min_positive=4)
                                score = (
                                    wr + mean * 900 - mx * 4 + min(n, 200) * 0.25
                                    + (25 if fold_ok else 0)
                                )
                                scored.append({
                                    "score": score,
                                    "dsl": definition,
                                    "metrics": {
                                        "trades": n, "wr": wr, "mean": mean,
                                        "streak": mx,
                                        "ret": res.get("total_return_percent"),
                                        "fold_ok": fold_ok,
                                    },
                                })
                                print(
                                    "KEEP", definition["key"],
                                    scored[-1]["metrics"], round(score, 2),
                                    flush=True,
                                )

    scored.sort(key=lambda x: -x["score"])
    print("TOTAL_KEPT", len(scored), flush=True)
    (OUTDIR / "candidates_kept_g.json").write_text(json.dumps([
        {
            "key": r["dsl"]["key"], "score": r["score"],
            "metrics": r["metrics"], "dsl": r["dsl"],
        }
        for r in scored[:20]
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    passed = None
    attempted = []
    log({"event": "submit_start_1g", "n": min(8, len(scored))})
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
        log({"event": "submit_result_1g", "out": slim})
        attempted.append(slim)
        (OUTDIR / "submit_attempts_1g.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2), encoding="utf-8")
        if out.get("ok") and out.get("pushed"):
            passed = out
            break
        time.sleep(5)

    (OUTDIR / "final_status_1g.json").write_text(json.dumps({
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
