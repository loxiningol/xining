# -*- coding: utf-8 -*-
"""7.25 第四次训练：异标的全新逻辑族（禁止 ADA 顺势回升近邻）。"""
from __future__ import print_function

import json
import os
import time
from datetime import datetime
from pathlib import Path

OUTDIR = Path("/root/auto_trade/codex_0725_train4")
OUTDIR.mkdir(parents=True, exist_ok=True)
AUDIT = OUTDIR / "train_audit.jsonl"
BARS = 12000
# Hard ban: do not create ADA trend-pullback family this round
BAN_SYMBOLS = {"ADA-USDT-SWAP"}


def _load_env():
    path = Path("/root/auto_trade/ai_ecosystem.env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


_load_env()

import auto_trade_codex_strategy_review as codex  # noqa: E402
import auto_trade_human_confirm_pipeline as pipeline  # noqa: E402
import auto_trade_strategy_dsl as dsl  # noqa: E402
import auto_trade_strategy_ecosystem as eco  # noqa: E402


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


def fade_short(symbol, tag, z, rsi, slope, hold, tp):
    """冲高衰竭做空 — 与 ADA 顺势回升结构完全不同。"""
    key = "codex0725t4_%s5_fade_z%s_r%s_h%s" % (
        tag, str(z).replace(".", "p"), int(rsi), hold)
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key,
        "name": "%s5冲高衰竭·0725T4" % tag.upper(),
        "direction": "short",
        "timeframe": "5m",
        "supported_instruments": [symbol],
        "max_hold_bars": int(hold),
        "description": (
            "%s 5m冲高衰竭做空：z20>=%s RSI>=%s 阴线 K下行 H1斜率<=%s；"
            "RSI<=%s止盈；上破prev_high20失效。Codex train4 全新逻辑。"
            % (symbol.split("-")[0], z, rsi, slope, tp)
        ),
        "origin": "codex_0725_train4",
        "version": "0725t4",
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


def reclaim_long(symbol, tag, z, rsi, slope, hold, tp):
    """跌深反抽做多 — 无 H1 均线交叉、无 RSI 上穿，异于 ADA trendpb。"""
    key = "codex0725t4_%s5_reclaim_z%s_r%s_h%s" % (
        tag, str(z).replace(".", "p").replace("-", "m"), int(rsi), hold)
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key,
        "name": "%s5跌深反抽·0725T4" % tag.upper(),
        "direction": "long",
        "timeframe": "5m",
        "supported_instruments": [symbol],
        "max_hold_bars": int(hold),
        "description": (
            "%s 5m跌深反抽：z20<=%s RSI<=%s 阳线 K上行 H1斜率>=%s；"
            "RSI>=%s止盈；下破prev_low20失效。Codex train4 全新逻辑。"
            % (symbol.split("-")[0], z, rsi, slope, tp)
        ),
        "origin": "codex_0725_train4",
        "version": "0725t4",
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


def downtrend_break_short(symbol, tag, tf, z, rsi_cross, hold, tp):
    """H1 空头排列下破做空 — 与 ADA 多头回升相反。"""
    key = "codex0725t4_%s%s_brkdn_z%s_r%s_h%s" % (
        tag, tf, str(z).replace(".", "p").replace("-", "m"), int(rsi_cross), hold)
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key,
        "name": "%s%s空头破位·0725T4" % (tag.upper(), tf),
        "direction": "short",
        "timeframe": tf,
        "supported_instruments": [symbol],
        "max_hold_bars": int(hold),
        "description": (
            "%s %s 空头破位：H1 ema19<em53 slope<0 RSI下穿%s z20<=%s close<em21；"
            "RSI<=%s止盈；上破prev_high20失效。Codex train4。"
            % (symbol.split("-")[0], tf, rsi_cross, z, tp)
        ),
        "origin": "codex_0725_train4",
        "version": "0725t4",
        "entry": {"all": [
            {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "lt",
             "right": {"feature": "h1_ema53"}},
            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "lt",
             "right": {"value": 0}},
            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_below",
             "right": {"value": float(rsi_cross)}},
            {"id": "z", "left": {"feature": "z20"}, "op": "lte",
             "right": {"value": float(z)}},
            {"id": "px", "left": {"feature": "close"}, "op": "lt",
             "right": {"feature": "ema21"}},
        ]},
        "exit": {"any": [
            {"id": "tp", "left": {"feature": "rsi14"}, "op": "lte",
             "right": {"value": float(tp)}, "role": "take_profit"},
            {"id": "inv", "left": {"feature": "close"}, "op": "gt",
             "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
    }


def build_candidates():
    cands = []
    # Lean but structurally distinct families on non-ADA symbols
    for sym, tag in [
        ("LTC-USDT-SWAP", "ltc"),
        ("NG-USDT-SWAP", "ng"),
        ("ETH-USDT-SWAP", "eth"),
        ("SOL-USDT-SWAP", "sol"),
        ("XAG-USDT-SWAP", "xag"),
        ("CL-USDT-SWAP", "cl"),
    ]:
        for z in (1.2, 1.35, 1.5):
            for rsi in (72, 75, 78):
                for slope in (0.006, 0.004):
                    for hold in (36, 48):
                        for tp in (38, 40):
                            cands.append(
                                fade_short(sym, tag, z, rsi, slope, hold, tp))
        for z in (-1.2, -1.35, -1.5):
            for rsi in (28, 30, 32):
                for slope in (-0.004, -0.002):
                    for hold in (36, 48):
                        for tp in (60, 62):
                            cands.append(
                                reclaim_long(sym, tag, z, rsi, slope, hold, tp))

    for sym, tag, tf, hold in [
        ("BTC-USDT-SWAP", "btc", "5m", 14),
        ("ETH-USDT-SWAP", "eth", "5m", 14),
        ("SOL-USDT-SWAP", "sol", "5m", 14),
        ("XAU-USDT-SWAP", "xau", "15m", 16),
    ]:
        for z in (-0.5, 0.0, 0.5):
            for rc in (55, 58, 60):
                for tp in (40, 42):
                    cands.append(
                        downtrend_break_short(sym, tag, tf, z, rc, hold, tp))
    return cands


def three_ok(ai_rev):
    by = {r.get("provider"): r for r in (ai_rev.get("reviews") or [])}
    for p in ("deepseek", "qwen", "glm"):
        r = by.get(p) or {}
        if not r.get("ok") or str(r.get("decision") or "").upper() != "APPROVE":
            return False
    return bool(ai_rev.get("approved"))


def near_dup_blocked(definition):
    try:
        catalog = codex._existing_strategy_catalog()
        hit = dsl.find_near_duplicate(definition, catalog)
        return hit
    except Exception as exc:
        log({"event": "near_dup_err", "error": str(exc)[:120]})
        return None


def main():
    print("TRAIN4_START", now(), flush=True)
    raw = build_candidates()
    seen = set()
    uniq = []
    for obj in raw:
        sym = (obj.get("supported_instruments") or [None])[0]
        if sym in BAN_SYMBOLS:
            continue
        if obj["key"] in seen:
            continue
        seen.add(obj["key"])
        try:
            uniq.append(dsl.validate_strategy(obj))
        except Exception:
            continue
    print("CANDS", len(uniq), flush=True)

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

    scored = []
    blocked_n = 0
    for i, definition in enumerate(uniq):
        hit = near_dup_blocked(definition)
        if hit:
            blocked_n += 1
            continue
        try:
            frame, fr = get_frame(
                definition["supported_instruments"][0], definition["timeframe"])
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
        except Exception as exc:
            if i < 3:
                print("bt_fail", definition.get("key"), exc, flush=True)
            continue
        n, wr, mean, st = m["trades"], m["wr"], m["mean"], m["streak"]
        if n >= 18 and wr >= 56 and mean > 0 and st <= 4:
            if (not m.get("fold_ok")) and (n < 26 or wr < 60):
                pass
            else:
                score = (
                    wr + mean * 900 - st * 4 + min(n, 200) * 0.3
                    + (35 if m.get("fold_ok") else 0)
                )
                scored.append({"score": score, "dsl": definition, "metrics": m})
                print("KEEP", definition["key"],
                      definition["supported_instruments"][0], m,
                      "score", round(score, 2), flush=True)
        if (i + 1) % 40 == 0:
            print("progress", i + 1, "/", len(uniq),
                  "kept", len(scored), "near_blocked", blocked_n, flush=True)

    scored.sort(key=lambda x: -x["score"])
    print("KEPT", len(scored), "near_blocked", blocked_n, flush=True)
    (OUTDIR / "candidates_kept.json").write_text(json.dumps([
        {"key": r["dsl"]["key"], "score": r["score"], "metrics": r["metrics"],
         "symbol": r["dsl"]["supported_instruments"][0],
         "name": r["dsl"].get("name"), "dsl": r["dsl"]}
        for r in scored[:25]
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    # Prefer diversity across symbols in submit order
    pool = []
    seen_sym = set()
    for row in scored:
        sym = row["dsl"]["supported_instruments"][0]
        if sym not in seen_sym or len(pool) < 4:
            pool.append(row)
            seen_sym.add(sym)
        if len(pool) >= 12:
            break
    if not pool:
        pool = scored[:12]

    passed = None
    attempted = []
    log({"event": "submit_start_train4", "n": len(pool)})
    for row in pool:
        definition = row["dsl"]
        # re-check near dup right before submit
        hit = near_dup_blocked(definition)
        if hit:
            log({"event": "submit_skip_near", "key": definition["key"],
                 "dup": hit})
            continue
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
            "ok": out.get("ok"),
            "key": definition["key"],
            "name": definition.get("name"),
            "symbol": definition["supported_instruments"][0],
            "pushed": out.get("pushed"),
            "stage": out.get("stage"),
            "avg": ai_rev.get("ai_theoretical_wr_avg"),
            "fail": ai_rev.get("fail_reasons") or out.get("reason"),
            "three": three_ok(ai_rev),
            "metrics": row["metrics"],
            "reviews": [{
                "p": r.get("provider"), "d": r.get("decision"),
                "wr": r.get("theoretical_win_rate_pct"),
                "risk": r.get("stop_cluster_risk"), "ok": r.get("ok"),
                "reason": str(r.get("reason") or "")[:120],
            } for r in (ai_rev.get("reviews") or [])],
        }
        log({"event": "submit_result_train4", "out": slim})
        attempted.append(slim)
        (OUTDIR / "submit_attempts.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2), encoding="utf-8")
        if out.get("ok") and out.get("pushed") and three_ok(ai_rev):
            passed = out
            passed["name"] = definition.get("name")
            passed["dsl"] = definition
            break
        time.sleep(5)

    (OUTDIR / "final_status.json").write_text(json.dumps({
        "time": now(),
        "kept": len(scored),
        "near_blocked": blocked_n,
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
            sym = (passed.get("dsl") or {}).get("supported_instruments", [""])[0]
            n.send_message(
                "【7.25训练4产出】\n"
                "异标的全新逻辑 · 三AI通过\n"
                "%s\n标的: %s\n时间: %s" % (shown, sym, now()),
                kind="codex_strategy_review",
                meta={"key": passed.get("key"),
                      "strategy_name": passed.get("name")},
            )
            print("wx_ok", shown, flush=True)
        except Exception as e:
            print("wx_fail", e, flush=True)


if __name__ == "__main__":
    main()
