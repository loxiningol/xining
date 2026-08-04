# -*- coding: utf-8 -*-
"""7.25 train4d: non-ADA on supported universe — mass_hf / breakdown / cci families."""
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
    ("BTC-USDT-SWAP", "btc"), ("ETH-USDT-SWAP", "eth"),
    ("SOL-USDT-SWAP", "sol"), ("BNB-USDT-SWAP", "bnb"),
    ("LTC-USDT-SWAP", "ltc"), ("XRP-USDT-SWAP", "xrp"),
    ("DOGE-USDT-SWAP", "doge"), ("XAU-USDT-SWAP", "xau"),
    ("XAG-USDT-SWAP", "xag"), ("NG-USDT-SWAP", "ng"),
    ("CL-USDT-SWAP", "cl"),
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
    # A) mass_hf-like but structurally distinct: RSI soft + close cross ema21 + H1 slope filter
    for sym, tag in SYMS:
        for rsi_max in (40, 45, 48):
            for tp in (55, 58):
                for hold in (36, 48):
                    for slope_min in (-0.001, 0.0):
                        key = "codex0725t4_%s5_emarec_r%s_t%s_h%s_s%s" % (
                            tag, rsi_max, tp, hold, ztag(slope_min))
                        out.append({
                            "schema": "qiyu_strategy_dsl_v1", "key": key,
                            "name": "%s5 EMA反抽·0725T4" % tag.upper(),
                            "direction": "long", "timeframe": "5m",
                            "supported_instruments": [sym], "max_hold_bars": hold,
                            "description": (
                                "%s 5m EMA反抽：RSI<%s + close上穿ema21 + h1_slope>=%s；"
                                "RSI>%s止盈；跌破ema53失效。异于mass_hf(ema17)拓扑。"
                                % (sym.split("-")[0], rsi_max, slope_min, tp)),
                            "origin": "codex_0725_train4", "version": "0725t4d",
                            "entry": {"all": [
                                {"id": "rsi", "left": {"feature": "rsi14"}, "op": "lt",
                                 "right": {"value": float(rsi_max)}},
                                {"id": "cross", "left": {"feature": "close"},
                                 "op": "cross_above", "right": {"feature": "ema21"}},
                                {"id": "slope", "left": {"feature": "h1_slope4"},
                                 "op": "gte", "right": {"value": float(slope_min)}},
                            ]},
                            "exit": {"any": [
                                {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
                                 "right": {"value": float(tp)}, "role": "take_profit"},
                                {"id": "inv", "left": {"feature": "close"},
                                 "op": "cross_below", "right": {"feature": "ema53"},
                                 "role": "invalidation"},
                            ]},
                        })

    # B) 15m breakdown short — H1 down + z low + cross below ema17
    for sym, tag in SYMS:
        for z in (-1.5, -1.8, -2.0):
            for slope in (-0.0005, -0.0008):
                for hold in (12, 16):
                    for tp_cci in (-50, -60):
                        key = "codex0725t4_%s15_bd_z%s_s%s_h%s" % (
                            tag, ztag(z), ztag(slope), hold)
                        # unique with tp
                        key = "codex0725t4_%s15_bd_z%s_s%s_h%s_c%s" % (
                            tag, ztag(z), ztag(slope), hold, ztag(tp_cci))
                        out.append({
                            "schema": "qiyu_strategy_dsl_v1", "key": key,
                            "name": "%s15 破位做空·0725T4" % tag.upper(),
                            "direction": "short", "timeframe": "15m",
                            "supported_instruments": [sym], "max_hold_bars": hold,
                            "description": (
                                "%s 15m 破位：h1_slope<%s + close<ema53 + z<%s + "
                                "close下穿ema17；CCI上穿%s止盈。"
                                % (sym.split("-")[0], slope, z, tp_cci)),
                            "origin": "codex_0725_train4", "version": "0725t4d",
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
                                 "op": "cross_above", "right": {"value": float(tp_cci)},
                                 "role": "take_profit"},
                                {"id": "inv", "left": {"feature": "close"},
                                 "op": "cross_above", "right": {"feature": "ema53"},
                                 "role": "invalidation"},
                            ]},
                        })

    # C) CCI oversold reclaim long 5m
    for sym, tag in SYMS:
        for cci_th in (-140, -160, -180):
            for hold in (24, 36):
                for tp in (0, 20):
                    key = "codex0725t4_%s5_ccirec_c%s_h%s_t%s" % (
                        tag, ztag(cci_th), hold, ztag(tp))
                    out.append({
                        "schema": "qiyu_strategy_dsl_v1", "key": key,
                        "name": "%s5 CCI超跌反抽·0725T4" % tag.upper(),
                        "direction": "long", "timeframe": "5m",
                        "supported_instruments": [sym], "max_hold_bars": hold,
                        "description": (
                            "%s 5m CCI超跌：CCI<=%s 阳线 close>ema75；CCI>=%s止盈。"
                            % (sym.split("-")[0], cci_th, tp)),
                        "origin": "codex_0725_train4", "version": "0725t4d",
                        "entry": {"all": [
                            {"id": "cci", "left": {"feature": "cci"}, "op": "lte",
                             "right": {"value": float(cci_th)}},
                            {"id": "bull", "left": {"feature": "close"}, "op": "gt",
                             "right": {"feature": "open"}},
                            {"id": "px", "left": {"feature": "close"}, "op": "gt",
                             "right": {"feature": "ema75"}},
                        ]},
                        "exit": {"any": [
                            {"id": "tp", "left": {"feature": "cci"}, "op": "gte",
                             "right": {"value": float(tp)}, "role": "take_profit"},
                            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                             "right": {"feature": "prev_low20"},
                             "role": "invalidation"},
                        ]},
                    })
    return out


def main():
    print("TRAIN4D", now(), flush=True)
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
        print("CATALOG", len(catalog or []), flush=True)
    except Exception as exc:
        catalog = []
        print("CATALOG_FAIL", exc, flush=True)

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

    # Prefer emarec first (often more trades), then bd, then cci
    def sk(d):
        k = d["key"]
        if "_emarec_" in k:
            return (0, k)
        if "_bd_" in k:
            return (1, k)
        return (2, k)

    uniq.sort(key=sk)

    scored, near = [], []
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
        except Exception as e:
            if i < 5:
                print("bt_fail", definition["key"], e, flush=True)
            continue
        score = (m["wr"] + m["mean"] * 900 - m["streak"] * 4
                 + min(m["trades"], 200) * 0.3 + (40 if m.get("fold_ok") else 0))
        near.append({"score": score, "dsl": definition, "metrics": m})
        if m["trades"] >= 12 and m["wr"] >= 52 and m["mean"] > 0:
            print("NEAR", definition["key"],
                  definition["supported_instruments"][0],
                  {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                   for kk, vv in m.items()},
                  round(score, 1), flush=True)
        ok = (m["trades"] >= 16 and m["wr"] >= 55 and m["mean"] > 0
              and m["streak"] <= 4)
        if ok and (m.get("fold_ok") or (m["trades"] >= 22 and m["wr"] >= 57)):
            scored.append({"score": score, "dsl": definition, "metrics": m})
            print("KEEP", definition["key"],
                  definition["supported_instruments"][0], m,
                  round(score, 2), flush=True)
        if (i + 1) % 40 == 0:
            print("progress", i + 1, "/", len(uniq), "kept", len(scored),
                  flush=True)
        if len(scored) >= 8:
            syms = {r["dsl"]["supported_instruments"][0] for r in scored}
            print("EARLY_STOP", len(scored), sorted(syms), flush=True)
            break

    if not scored:
        near.sort(key=lambda x: -x["score"])
        print("NO_STRICT; top10:", flush=True)
        for r in near[:10]:
            print(" ", r["dsl"]["key"], r["dsl"]["supported_instruments"][0],
                  r["metrics"], round(r["score"], 1), flush=True)
        for row in near[:80]:
            m = row["metrics"]
            if (m["trades"] >= 14 and m["wr"] >= 53 and m["mean"] > 0
                    and m["streak"] <= 5
                    and (m.get("fold_ok")
                         or (m["trades"] >= 20 and m["wr"] >= 55))):
                scored.append(row)
                print("LOOSE_KEEP", row["dsl"]["key"],
                      row["dsl"]["supported_instruments"][0], m, flush=True)
            if len(scored) >= 8:
                break

    scored.sort(key=lambda x: -x["score"])
    near.sort(key=lambda x: -x["score"])
    print("KEPT", len(scored), flush=True)
    (OUT / "candidates_kept_d.json").write_text(json.dumps([
        {"key": r["dsl"]["key"], "score": r["score"], "metrics": r["metrics"],
         "symbol": r["dsl"]["supported_instruments"][0],
         "name": r["dsl"].get("name"), "dsl": r["dsl"]}
        for r in scored[:20]
    ], ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "near_misses_d.json").write_text(json.dumps([
        {"key": r["dsl"]["key"], "score": r["score"], "metrics": r["metrics"],
         "symbol": r["dsl"]["supported_instruments"][0]}
        for r in near[:25]
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    pool, seen_sym = [], set()
    for row in scored:
        sym = row["dsl"]["supported_instruments"][0]
        if sym not in seen_sym or len(pool) < 3:
            pool.append(row)
            seen_sym.add(sym)
        if len(pool) >= 10:
            break

    passed, attempted = None, []
    for row in pool:
        definition = row["dsl"]
        print("SUBMIT", definition["key"],
              definition["supported_instruments"][0], flush=True)
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
                "reason": str(r.get("reason") or "")[:160],
            } for r in (ai_rev.get("reviews") or [])],
        }
        print("SUBMIT_OUT", json.dumps(slim, ensure_ascii=False), flush=True)
        attempted.append(slim)
        (OUT / "submit_attempts_d.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2), encoding="utf-8")
        with (OUT / "train_audit.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "submit_d", "out": slim, "time": now()},
                               ensure_ascii=False) + "\n")
        if out.get("ok") and out.get("pushed") and three_ok(ai_rev):
            passed = out
            passed["name"] = definition.get("name")
            passed["dsl"] = definition
            break
        time.sleep(3)

    (OUT / "final_status_d.json").write_text(json.dumps({
        "time": now(), "kept": len(scored), "passed": bool(passed),
        "passed_key": (passed or {}).get("key"),
        "passed_name": (passed or {}).get("name"),
        "queued": (passed or {}).get("queued"), "attempted": attempted,
        "top_near": [
            {"key": r["dsl"]["key"], "metrics": r["metrics"],
             "symbol": r["dsl"]["supported_instruments"][0]}
            for r in near[:8]
        ],
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
