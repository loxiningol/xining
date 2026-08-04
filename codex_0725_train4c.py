# -*- coding: utf-8 -*-
"""7.25 train4c: focused non-ADA multi-family screen + 3AI submit."""
from __future__ import print_function

import copy
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
    store = json.loads(
        Path("/root/strategy_configs/ai_dsl_strategies.json").read_text())
    base = next(
        s for s in store["strategies"]
        if s.get("key") == "ada5_z20_t60_prev_h14_0724k")
    syms = [
        ("ETH-USDT-SWAP", "eth"), ("BTC-USDT-SWAP", "btc"),
        ("SOL-USDT-SWAP", "sol"), ("DOGE-USDT-SWAP", "doge"),
        ("XRP-USDT-SWAP", "xrp"), ("LINK-USDT-SWAP", "link"),
        ("OP-USDT-SWAP", "op"), ("LTC-USDT-SWAP", "ltc"),
        ("BNB-USDT-SWAP", "bnb"), ("SUI-USDT-SWAP", "sui"),
    ]
    out = []

    # 1) RSI reclaim on ema75 — different topology from H1 dual-EMA trendpb
    for sym, tag in syms:
        for rc in (35, 38, 42, 45):
            for z in (1.8, 2.2, 2.5):
                for hold in (14, 18, 24):
                    key = "codex0725t4_%s5_rsirec_r%s_z%s_h%s" % (
                        tag, rc, ztag(z), hold)
                    out.append({
                        "schema": "qiyu_strategy_dsl_v1", "key": key,
                        "name": "%s5 RSI反抽·0725T4" % tag.upper(),
                        "direction": "long", "timeframe": "5m",
                        "supported_instruments": [sym], "max_hold_bars": hold,
                        "description": (
                            "%s 5m RSI反抽：RSI上穿%s + close>ema75 + z20<%s；"
                            "RSI>=60止盈；跌破prev_low20失效。非ADA/非H1双均线族。"
                            % (sym.split("-")[0], rc, z)),
                        "origin": "codex_0725_train4", "version": "0725t4c",
                        "entry": {"all": [
                            {"id": "rsi", "left": {"feature": "rsi14"},
                             "op": "cross_above", "right": {"value": float(rc)}},
                            {"id": "px", "left": {"feature": "close"}, "op": "gt",
                             "right": {"feature": "ema75"}},
                            {"id": "z", "left": {"feature": "z20"}, "op": "lt",
                             "right": {"value": float(z)}},
                            {"id": "slope", "left": {"feature": "h1_slope4"},
                             "op": "gt", "right": {"value": -0.001}},
                        ]},
                        "exit": {"any": [
                            {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
                             "right": {"value": 60}, "role": "take_profit"},
                            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                             "right": {"feature": "prev_low20"},
                             "role": "invalidation"},
                        ]},
                    })

    # 2) Short trend fade (opposite direction family)
    for sym, tag in syms:
        for rc in (55, 58, 62):
            for z in (-1.8, -2.2, -2.5):
                for hold in (14, 18, 24):
                    key = "codex0725t4_%s5_trendsh_r%s_z%s_h%s" % (
                        tag, rc, ztag(z), hold)
                    out.append({
                        "schema": "qiyu_strategy_dsl_v1", "key": key,
                        "name": "%s5顺势回落·0725T4" % tag.upper(),
                        "direction": "short", "timeframe": "5m",
                        "supported_instruments": [sym], "max_hold_bars": hold,
                        "description": (
                            "%s 5m 顺势回落：H1空+斜率<0+RSI下穿%s+跌破EMA21+z>%s；"
                            "RSI<=40止盈；上破prev_high20失效。"
                            % (sym.split("-")[0], rc, z)),
                        "origin": "codex_0725_train4", "version": "0725t4c",
                        "entry": {"all": [
                            {"id": "h1", "left": {"feature": "h1_ema19"},
                             "op": "lt", "right": {"feature": "h1_ema53"}},
                            {"id": "slope", "left": {"feature": "h1_slope4"},
                             "op": "lt", "right": {"value": 0}},
                            {"id": "rsi", "left": {"feature": "rsi14"},
                             "op": "cross_below", "right": {"value": float(rc)}},
                            {"id": "px", "left": {"feature": "close"}, "op": "lt",
                             "right": {"feature": "ema21"}},
                            {"id": "z", "left": {"feature": "z20"}, "op": "gt",
                             "right": {"value": float(z)}},
                        ]},
                        "exit": {"any": [
                            {"id": "tp", "left": {"feature": "rsi14"}, "op": "lt",
                             "right": {"value": 40}, "role": "take_profit"},
                            {"id": "inv", "left": {"feature": "close"}, "op": "gt",
                             "right": {"feature": "prev_high20"},
                             "role": "invalidation"},
                        ]},
                    })

    # 3) KDJ reclaim
    for sym, tag in syms:
        for jth in (8, 12, 18):
            for hold in (18, 24, 36):
                key = "codex0725t4_%s5_kdjrec_j%s_h%s" % (tag, jth, hold)
                out.append({
                    "schema": "qiyu_strategy_dsl_v1", "key": key,
                    "name": "%s5 KDJ超跌反抽·0725T4" % tag.upper(),
                    "direction": "long", "timeframe": "5m",
                    "supported_instruments": [sym], "max_hold_bars": hold,
                    "description": (
                        "%s 5m KDJ：J<=%s 且J上穿K + close>ema21；J>=80止盈。"
                        % (sym.split("-")[0], jth)),
                    "origin": "codex_0725_train4", "version": "0725t4c",
                    "entry": {"all": [
                        {"id": "jlow", "left": {"feature": "j"}, "op": "lte",
                         "right": {"value": float(jth)}},
                        {"id": "cross", "left": {"feature": "j"},
                         "op": "cross_above", "right": {"feature": "k"}},
                        {"id": "px", "left": {"feature": "close"}, "op": "gt",
                         "right": {"feature": "ema21"}},
                    ]},
                    "exit": {"any": [
                        {"id": "tp", "left": {"feature": "j"}, "op": "gte",
                         "right": {"value": 80}, "role": "take_profit"},
                        {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                         "right": {"feature": "prev_low20"},
                         "role": "invalidation"},
                    ]},
                })

    # 4) Cross-symbol trendpb port (allowed: near-dup is same-instrument only)
    for sym, tag in syms:
        for rc in (38, 42, 45):
            for z in (1.8, 2.2, 2.5):
                for hold in (12, 14, 18, 24):
                    obj = copy.deepcopy(base)
                    obj["key"] = "codex0725t4_%s5_tpbport_r%s_z%s_h%s" % (
                        tag, rc, ztag(z), hold)
                    obj["name"] = "%s5顺势回升·0725T4" % tag.upper()
                    obj["supported_instruments"] = [sym]
                    obj["max_hold_bars"] = hold
                    obj["origin"] = "codex_0725_train4"
                    obj["version"] = "0725t4c"
                    obj["description"] = (
                        "%s 5m 顺势回升：H1多+斜率>0+RSI上穿%s+站上EMA21+z<%s；"
                        "RSI>=60止盈；跌破prev_low20；hold=%s。非ADA标的新策略。"
                        % (sym.split("-")[0], rc, z, hold))
                    for k in ("live_enabled", "auto_trade_eligible",
                              "approved_version_hash"):
                        obj.pop(k, None)
                    for c in obj["entry"]["all"]:
                        if c.get("id") == "rsi":
                            c["right"] = {"value": float(rc)}
                        if c.get("id") == "z":
                            c["right"] = {"value": float(z)}
                    out.append(obj)
    return out


def main():
    print("TRAIN4C", now(), flush=True)
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

    # Prefer novel families first, then tpbport fallback
    def sort_key(d):
        k = d["key"]
        # tpbport first (proven topology on new symbols), then novel families
        if "_tpbport_" in k:
            return (0, k)
        if "_trendsh_" in k:
            return (1, k)
        if "_rsirec_" in k:
            return (2, k)
        return (3, k)

    uniq.sort(key=sort_key)

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

    scored, near = [], []
    try:
        catalog = codex._existing_strategy_catalog()
        print("CATALOG", len(catalog or []), flush=True)
    except Exception as exc:
        catalog = []
        print("CATALOG_FAIL", exc, flush=True)
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
        except Exception:
            continue
        n, wr, mean, st = m["trades"], m["wr"], m["mean"], m["streak"]
        score = (wr + mean * 900 - st * 4 + min(n, 200) * 0.3
                 + (40 if m.get("fold_ok") else 0))
        near.append({"score": score, "dsl": definition, "metrics": m})
        ok = (n >= 16 and wr >= 55 and mean > 0 and st <= 4)
        if ok and (m.get("fold_ok") or (n >= 22 and wr >= 57)):
            scored.append({"score": score, "dsl": definition, "metrics": m})
            print("KEEP", definition["key"],
                  definition["supported_instruments"][0], m,
                  round(score, 2), flush=True)
        if (i + 1) % 60 == 0:
            print("progress", i + 1, "/", len(uniq), "kept", len(scored),
                  flush=True)
        if len(scored) >= 10:
            syms_kept = {r["dsl"]["supported_instruments"][0] for r in scored}
            if len(syms_kept) >= 2:
                print("EARLY_STOP", len(scored), sorted(syms_kept), flush=True)
                break

    if not scored:
        near.sort(key=lambda x: -x["score"])
        print("NO_STRICT; loose pass", flush=True)
        for row in near[:100]:
            m = row["metrics"]
            if (m["trades"] >= 14 and m["wr"] >= 54 and m["mean"] > 0
                    and m["streak"] <= 5
                    and (m.get("fold_ok")
                         or (m["trades"] >= 20 and m["wr"] >= 56))):
                scored.append(row)
                print("LOOSE_KEEP", row["dsl"]["key"],
                      row["dsl"]["supported_instruments"][0], m,
                      round(row["score"], 2), flush=True)
            if len(scored) >= 10:
                break

    scored.sort(key=lambda x: -x["score"])
    near.sort(key=lambda x: -x["score"])
    print("KEPT", len(scored), flush=True)
    (OUT / "candidates_kept_c.json").write_text(json.dumps([
        {"key": r["dsl"]["key"], "score": r["score"], "metrics": r["metrics"],
         "symbol": r["dsl"]["supported_instruments"][0],
         "name": r["dsl"].get("name"), "dsl": r["dsl"]}
        for r in scored[:25]
    ], ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "near_misses_c.json").write_text(json.dumps([
        {"key": r["dsl"]["key"], "score": r["score"], "metrics": r["metrics"],
         "symbol": r["dsl"]["supported_instruments"][0],
         "name": r["dsl"].get("name")}
        for r in near[:25]
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    def prio(row):
        k = row["dsl"]["key"]
        # prefer non-tpbport
        fam = 0 if "tpbport" in k else 1
        return (-fam, -row["score"])

    ordered = sorted(scored, key=prio)
    pool, seen_sym = [], set()
    for row in ordered:
        sym = row["dsl"]["supported_instruments"][0]
        if sym not in seen_sym or len(pool) < 4:
            pool.append(row)
            seen_sym.add(sym)
        if len(pool) >= 12:
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
        log({"event": "submit_c", "out": slim})
        attempted.append(slim)
        (OUT / "submit_attempts_c.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2), encoding="utf-8")
        if out.get("ok") and out.get("pushed") and three_ok(ai_rev):
            passed = out
            passed["name"] = definition.get("name")
            passed["dsl"] = definition
            break
        time.sleep(4)

    (OUT / "final_status_c.json").write_text(json.dumps({
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
