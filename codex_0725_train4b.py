# -*- coding: utf-8 -*-
"""7.25 train4b: non-ADA symbols, structurally new families, looser but AI-grade screen."""
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


def log(row):
    row = dict(row)
    row["time"] = now()
    with (OUT / "train_audit.jsonl").open("a", encoding="utf-8") as f:
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
    return {"trades": n, "wr": wr, "mean": mean, "streak": mx,
            "ret": result.get("total_return_percent"), "fold_ok": fold_ok}


def cci_fade_short(symbol, tag, cci_th, slope, hold, tp_cci):
    key = "codex0725t4_%s5_ccifade_c%s_h%s" % (tag, int(cci_th), hold)
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key,
        "name": "%s5 CCI冲高回落·0725T4" % tag.upper(),
        "direction": "short", "timeframe": "5m",
        "supported_instruments": [symbol], "max_hold_bars": int(hold),
        "description": (
            "%s 5m CCI冲高回落：CCI>=%s 阴线 H1斜率<=%s；CCI<=%s止盈；"
            "上破prev_high20失效。train4 新逻辑族。"
            % (symbol.split("-")[0], cci_th, slope, tp_cci)),
        "origin": "codex_0725_train4", "version": "0725t4b",
        "entry": {"all": [
            {"id": "cci", "left": {"feature": "cci"}, "op": "gte",
             "right": {"value": float(cci_th)}},
            {"id": "bear", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "open"}},
            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "lte",
             "right": {"value": float(slope)}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "cci"}, "op": "lte",
             "right": {"value": float(tp_cci)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
    }


def cci_reclaim_long(symbol, tag, cci_th, slope, hold, tp_cci):
    key = "codex0725t4_%s5_ccireclaim_c%s_h%s" % (
        tag, str(cci_th).replace("-", "m"), hold)
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key,
        "name": "%s5 CCI超跌反抽·0725T4" % tag.upper(),
        "direction": "long", "timeframe": "5m",
        "supported_instruments": [symbol], "max_hold_bars": int(hold),
        "description": (
            "%s 5m CCI超跌反抽：CCI<=%s 阳线 H1斜率>=%s；CCI>=%s止盈；"
            "下破prev_low20失效。train4 新逻辑族。"
            % (symbol.split("-")[0], cci_th, slope, tp_cci)),
        "origin": "codex_0725_train4", "version": "0725t4b",
        "entry": {"all": [
            {"id": "cci", "left": {"feature": "cci"}, "op": "lte",
             "right": {"value": float(cci_th)}},
            {"id": "bull", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "open"}},
            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gte",
             "right": {"value": float(slope)}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "cci"}, "op": "gte",
             "right": {"value": float(tp_cci)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "prev_low20"}, "role": "invalidation"},
        ]},
    }


def macd_fade_short(symbol, tag, stick, z, hold, tp_rsi):
    key = "codex0725t4_%s5_macdfade_s%s_z%s_h%s" % (
        tag, str(stick).replace(".", "p").replace("-", "m"),
        str(z).replace(".", "p"), hold)
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key,
        "name": "%s5 MACD冲高回落·0725T4" % tag.upper(),
        "direction": "short", "timeframe": "5m",
        "supported_instruments": [symbol], "max_hold_bars": int(hold),
        "description": (
            "%s 5m MACD柱转弱做空：macd_stick<=%s z20>=%s 阴线；"
            "RSI<=%s止盈；上破prev_high20失效。train4 新逻辑族。"
            % (symbol.split("-")[0], stick, z, tp_rsi)),
        "origin": "codex_0725_train4", "version": "0725t4b",
        "entry": {"all": [
            {"id": "macd", "left": {"feature": "macd_stick"}, "op": "lte",
             "right": {"value": float(stick)}},
            {"id": "z", "left": {"feature": "z20"}, "op": "gte",
             "right": {"value": float(z)}},
            {"id": "bear", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "open"}},
            {"id": "macd_down", "left": {"feature": "macd_stick"}, "op": "lt",
             "right": {"feature": "macd_stick", "offset": 1}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "lte",
             "right": {"value": float(tp_rsi)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
    }


def loose_fade(symbol, tag, z, rsi, slope, hold, tp):
    """Fewer filters → more samples; still not ADA topology."""
    key = "codex0725t4_%s5_loosefade_z%s_r%s_h%s" % (
        tag, str(z).replace(".", "p"), int(rsi), hold)
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key,
        "name": "%s5宽松衰竭·0725T4" % tag.upper(),
        "direction": "short", "timeframe": "5m",
        "supported_instruments": [symbol], "max_hold_bars": int(hold),
        "description": (
            "%s 5m宽松冲高衰竭：z>=%s rsi>=%s slope<=%s 阴线；RSI<=%s止盈。"
            % (symbol.split("-")[0], z, rsi, slope, tp)),
        "origin": "codex_0725_train4", "version": "0725t4b",
        "entry": {"all": [
            {"id": "z", "left": {"feature": "z20"}, "op": "gte",
             "right": {"value": float(z)}},
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "gte",
             "right": {"value": float(rsi)}},
            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "lte",
             "right": {"value": float(slope)}},
            {"id": "bear", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "open"}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "lte",
             "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
    }


def build():
    cands = []
    symbols = [
        ("LTC-USDT-SWAP", "ltc"), ("NG-USDT-SWAP", "ng"),
        ("ETH-USDT-SWAP", "eth"), ("SOL-USDT-SWAP", "sol"),
        ("XAG-USDT-SWAP", "xag"), ("CL-USDT-SWAP", "cl"),
        ("BNB-USDT-SWAP", "bnb"), ("BTC-USDT-SWAP", "btc"),
    ]
    for sym, tag in symbols:
        for cci_th in (100, 120, 140):
            for slope in (0.008, 0.004, 0.0):
                for hold in (24, 36, 48):
                    cands.append(cci_fade_short(sym, tag, cci_th, slope, hold, 40))
        for cci_th in (-100, -120, -140):
            for slope in (-0.008, -0.004, 0.0):
                for hold in (24, 36, 48):
                    cands.append(cci_reclaim_long(sym, tag, cci_th, slope, hold, -40))
        for stick in (0.0, -0.0005, -0.001):
            for z in (1.0, 1.2, 1.4):
                for hold in (24, 36):
                    cands.append(macd_fade_short(sym, tag, stick, z, hold, 42))
        for z in (1.0, 1.1, 1.2):
            for rsi in (68, 70, 72):
                for slope in (0.01, 0.006):
                    for hold in (36, 48, 60):
                        cands.append(loose_fade(sym, tag, z, rsi, slope, hold, 42))
    return cands


def three_ok(ai_rev):
    by = {r.get("provider"): r for r in (ai_rev.get("reviews") or [])}
    for p in ("deepseek", "qwen", "glm"):
        r = by.get(p) or {}
        if not r.get("ok") or str(r.get("decision") or "").upper() != "APPROVE":
            return False
    return bool(ai_rev.get("approved"))


def main():
    print("TRAIN4B", now(), flush=True)
    raw = build()
    seen = set()
    uniq = []
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

    scored = []
    for i, definition in enumerate(uniq):
        try:
            catalog = codex._existing_strategy_catalog()
            if dsl.find_near_duplicate(definition, catalog):
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
        except Exception:
            continue
        n, wr, mean, st = m["trades"], m["wr"], m["mean"], m["streak"]
        # AI-friendly but achievable
        ok = (n >= 16 and wr >= 55 and mean > 0 and st <= 4)
        if ok and (m.get("fold_ok") or (n >= 24 and wr >= 58)):
            score = (wr + mean * 900 - st * 4 + min(n, 200) * 0.3
                     + (40 if m.get("fold_ok") else 0))
            scored.append({"score": score, "dsl": definition, "metrics": m})
            print("KEEP", definition["key"],
                  definition["supported_instruments"][0], m,
                  round(score, 2), flush=True)
        if (i + 1) % 50 == 0:
            print("progress", i + 1, "/", len(uniq), "kept", len(scored),
                  flush=True)
        # Early exit if enough strong keeps across symbols
        if len(scored) >= 15:
            syms = {r["dsl"]["supported_instruments"][0] for r in scored}
            if len(syms) >= 3:
                print("EARLY_STOP kept", len(scored), "syms", sorted(syms),
                      flush=True)
                break

    scored.sort(key=lambda x: -x["score"])
    print("KEPT", len(scored), flush=True)
    (OUT / "candidates_kept_b.json").write_text(json.dumps([
        {"key": r["dsl"]["key"], "score": r["score"], "metrics": r["metrics"],
         "symbol": r["dsl"]["supported_instruments"][0],
         "name": r["dsl"].get("name"), "dsl": r["dsl"]}
        for r in scored[:20]
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    # diversify submit
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
                "reason": str(r.get("reason") or "")[:120],
            } for r in (ai_rev.get("reviews") or [])],
        }
        log({"event": "submit_b", "out": slim})
        attempted.append(slim)
        (OUT / "submit_attempts_b.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2), encoding="utf-8")
        if out.get("ok") and out.get("pushed") and three_ok(ai_rev):
            passed = out
            passed["name"] = definition.get("name")
            passed["dsl"] = definition
            break
        time.sleep(5)

    (OUT / "final_status_b.json").write_text(json.dumps({
        "time": now(), "kept": len(scored), "passed": bool(passed),
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
            "【7.25训练4产出】\n异标的全新逻辑 · 三AI通过\n%s\n标的: %s\n时间: %s"
            % (shown, sym, now()),
            kind="codex_strategy_review",
            meta={"key": passed.get("key"), "strategy_name": passed.get("name")},
        )
        print("wx_ok", shown, flush=True)


if __name__ == "__main__":
    main()
