# -*- coding: utf-8 -*-
"""train4 REWRITE: discard 50% junk; hunt non-ADA high-WR candidates (n>=20, WR>=62, fold).
Soft AI gate: only push if 3AI avg theoretical WR >= 70 (hard reject <65).
"""
from __future__ import print_function
import json, os, time, itertools
from datetime import datetime
from pathlib import Path

OUT = Path("/root/auto_trade/codex_0725_train4")
OUT.mkdir(parents=True, exist_ok=True)
LOG = Path("/tmp/codex_0725_train4_rewrite.log")
BARS = 12000
BAN_SYM = {"ADA-USDT-SWAP"}
SKIP_KEYS = {
    "codex0725t3_ada5m_trendpb_r42_z2p3_h14",
    "codex0725_ada5_trendpb_r42_z2p0_h14",
    "codex0725t2_ada5m_trendpb_r42_z2p2_h14",
    "ada5_z20_t60_prev_h14_0724k",
    "codex0725t4_ng5_h1sh17_r62_zm2p0_h18",
    "ng5_exhaustion_fade_short_ai",
    "ltc5_exhaustion_fade_short_ai",
}
MIN_TRADES = 20
MIN_WR = 62.0
MIN_AI_PUSH = 70.0
MIN_AI_SOFT = 65.0
MAX_STREAK = 3

SYMS = [
    ("DOGE-USDT-SWAP", "doge"),
    ("XRP-USDT-SWAP", "xrp"),
    ("BNB-USDT-SWAP", "bnb"),
    ("LTC-USDT-SWAP", "ltc"),
    ("SOL-USDT-SWAP", "sol"),
    ("ETH-USDT-SWAP", "eth"),
    ("BTC-USDT-SWAP", "btc"),
    ("XAU-USDT-SWAP", "xau"),
    ("XAG-USDT-SWAP", "xag"),
    ("CL-USDT-SWAP", "cl"),
    ("NG-USDT-SWAP", "ng"),
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


def log(msg):
    line = "[%s] %s" % (now(), msg)
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


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


def three_ok(ai_rev):
    by = {r.get("provider"): r for r in (ai_rev.get("reviews") or [])}
    for p in ("deepseek", "qwen", "glm"):
        r = by.get(p) or {}
        if not r.get("ok") or str(r.get("decision") or "").upper() != "APPROVE":
            return False
    return bool(ai_rev.get("approved"))


def mk(key, name, sym, direction, tf, hold, desc, entry, exit_):
    return {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key[:90],
        "name": name,
        "direction": direction,
        "timeframe": tf,
        "supported_instruments": [sym],
        "max_hold_bars": hold,
        "description": desc,
        "origin": "codex_0725_train4_rewrite",
        "version": "0725t4rw",
        "entry": entry,
        "exit": exit_,
    }


def build():
    out = []
    # F1: CCI extreme reclaim long
    for sym, tag in SYMS:
        for cci_th in (-160, -180, -200, -220):
            for zmax in (0.5, 1.0, 1.5):
                for hold in (12, 16, 20):
                    for tp in (-50, -30, 0):
                        key = "codex0725t4rw_%s5_ccibounce_c%s_z%s_h%s_t%s" % (
                            tag, ztag(cci_th), ztag(zmax), hold, ztag(tp))
                        out.append(mk(
                            key, "%s5 CCI超卖反弹·0725RW" % tag.upper(), sym,
                            "long", "5m", hold,
                            "%s 5m CCI超卖反弹：cci<=%s + z20<%s + close>ema17 + h1_slope>-0.01；cci>=%s止盈。"
                            % (tag.upper(), cci_th, zmax, tp),
                            {"all": [
                                {"id": "cci", "left": {"feature": "cci"}, "op": "lte",
                                 "right": {"value": float(cci_th)}},
                                {"id": "z", "left": {"feature": "z20"}, "op": "lt",
                                 "right": {"value": float(zmax)}},
                                {"id": "px", "left": {"feature": "close"}, "op": "gt",
                                 "right": {"feature": "ema17"}},
                                {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "gt",
                                 "right": {"value": -0.01}},
                            ]},
                            {"any": [
                                {"id": "tp", "left": {"feature": "cci"}, "op": "gte",
                                 "right": {"value": float(tp)}, "role": "take_profit"},
                                {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                                 "right": {"feature": "prev_low20"}, "role": "invalidation"},
                            ]},
                        ))

    # F2: KDJ J reclaim long
    for sym, tag in SYMS:
        for jth in (12, 15, 18, 22):
            for hold in (12, 16, 20):
                for tpj in (55, 65, 75):
                    key = "codex0725t4rw_%s5_kdjrec_j%s_h%s_t%s" % (
                        tag, jth, hold, tpj)
                    out.append(mk(
                        key, "%s5 KDJ超卖回收·0725RW" % tag.upper(), sym,
                        "long", "5m", hold,
                        "%s 5m KDJ：j cross_above %s + close>ema19 + z20<2.2；j>=%s止盈。"
                        % (tag.upper(), jth, tpj),
                        {"all": [
                            {"id": "jcross", "left": {"feature": "j"}, "op": "cross_above",
                             "right": {"value": float(jth)}},
                            {"id": "px", "left": {"feature": "close"}, "op": "gt",
                             "right": {"feature": "ema19"}},
                            {"id": "z", "left": {"feature": "z20"}, "op": "lt",
                             "right": {"value": 2.2}},
                        ]},
                        {"any": [
                            {"id": "tp", "left": {"feature": "j"}, "op": "gte",
                             "right": {"value": float(tpj)}, "role": "take_profit"},
                            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                             "right": {"feature": "prev_low20"}, "role": "invalidation"},
                        ]},
                    ))

    # F3: MACD stick reclaim long with ema32
    for sym, tag in SYMS:
        for stick in (-0.02, -0.01, 0.0):
            for hold in (14, 18):
                for tpr in (55, 62):
                    key = "codex0725t4rw_%s5_macdrec_s%s_h%s_r%s" % (
                        tag, ztag(stick), hold, tpr)
                    out.append(mk(
                        key, "%s5 MACD柱回收·0725RW" % tag.upper(), sym,
                        "long", "5m", hold,
                        "%s 5m macd_stick上穿%s + close>ema32 + rsi<%s；rsi>=68止盈。"
                        % (tag.upper(), stick, tpr),
                        {"all": [
                            {"id": "macd", "left": {"feature": "macd_stick"},
                             "op": "cross_above", "right": {"value": float(stick)}},
                            {"id": "px", "left": {"feature": "close"}, "op": "gt",
                             "right": {"feature": "ema32"}},
                            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "lt",
                             "right": {"value": float(tpr)}},
                        ]},
                        {"any": [
                            {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
                             "right": {"value": 68.0}, "role": "take_profit"},
                            {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                             "right": {"feature": "ema53"}, "role": "invalidation"},
                        ]},
                    ))

    # F4: 15m H1 breakdown short
    for sym, tag in SYMS:
        for z in (-1.6, -1.8, -2.0, -2.2):
            for hold in (10, 14, 18):
                for tp_cci in (-40, -20, 0):
                    key = "codex0725t4rw_%s15_bdsh_z%s_h%s_t%s" % (
                        tag, ztag(z), hold, ztag(tp_cci))
                    out.append(mk(
                        key, "%s15 H1跌破做空·0725RW" % tag.upper(), sym,
                        "short", "15m", hold,
                        "%s 15m breakdown：h1空 + slope<0 + z20<%s + close<ema17；cci上穿%s止盈。"
                        % (tag.upper(), z, tp_cci),
                        {"all": [
                            {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "lt",
                             "right": {"feature": "h1_ema53"}},
                            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "lt",
                             "right": {"value": 0}},
                            {"id": "z", "left": {"feature": "z20"}, "op": "lt",
                             "right": {"value": float(z)}},
                            {"id": "px", "left": {"feature": "close"}, "op": "lt",
                             "right": {"feature": "ema17"}},
                        ]},
                        {"any": [
                            {"id": "tp", "left": {"feature": "cci"}, "op": "cross_above",
                             "right": {"value": float(tp_cci)}, "role": "take_profit"},
                            {"id": "inv", "left": {"feature": "close"}, "op": "gt",
                             "right": {"feature": "prev_high20"}, "role": "invalidation"},
                        ]},
                    ))

    # F5: RSI exhaustion fade short (ema75 topology, distinct from live fades)
    for sym, tag in SYMS:
        for rsi_th in (72, 75, 78):
            for zmin in (1.6, 1.9, 2.2):
                for hold in (10, 14, 18):
                    key = "codex0725t4rw_%s5_rsifade_r%s_z%s_h%s" % (
                        tag, rsi_th, ztag(zmin), hold)
                    out.append(mk(
                        key, "%s5 RSI超买衰减空·0725RW" % tag.upper(), sym,
                        "short", "5m", hold,
                        "%s 5m RSI超买衰减：rsi下穿%s + z20>%s + close<ema75 + h1_slope<0.002；rsi<=45止盈。"
                        % (tag.upper(), rsi_th, zmin),
                        {"all": [
                            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_below",
                             "right": {"value": float(rsi_th)}},
                            {"id": "z", "left": {"feature": "z20"}, "op": "gt",
                             "right": {"value": float(zmin)}},
                            {"id": "px", "left": {"feature": "close"}, "op": "lt",
                             "right": {"feature": "ema75"}},
                            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "lt",
                             "right": {"value": 0.002}},
                        ]},
                        {"any": [
                            {"id": "tp", "left": {"feature": "rsi14"}, "op": "lt",
                             "right": {"value": 45.0}, "role": "take_profit"},
                            {"id": "inv", "left": {"feature": "close"}, "op": "gt",
                             "right": {"feature": "prev_high20"}, "role": "invalidation"},
                        ]},
                    ))

    # F6: H1 + RSI + ema32 long (topology != ADA ema21)
    for sym, tag in SYMS:
        if tag in ("eth", "sol", "btc"):
            rcs = (38, 40)
            zs = (1.8, 2.0)
        else:
            rcs = (38, 40, 42, 45)
            zs = (1.8, 2.0, 2.4, 2.8)
        for rc, z, hold in itertools.product(rcs, zs, (12, 14, 18)):
            key = "codex0725t4rw_%s5_h1pb32_r%s_z%s_h%s" % (
                tag, rc, ztag(z), hold)
            out.append(mk(
                key, "%s5 H1顺势回踩EMA32·0725RW" % tag.upper(), sym,
                "long", "5m", hold,
                "%s 5m H1多+RSI上穿%s+close>ema32+z<%s；RSI>=62止盈。ema32族≠ADA ema21。"
                % (tag.upper(), rc, z),
                {"all": [
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
                {"any": [
                    {"id": "tp", "left": {"feature": "rsi14"}, "op": "gt",
                     "right": {"value": 62.0}, "role": "take_profit"},
                    {"id": "inv", "left": {"feature": "close"}, "op": "lt",
                     "right": {"feature": "prev_low20"}, "role": "invalidation"},
                ]},
            ))

    # F7: H1 short + ema32
    for sym, tag in SYMS:
        for rc in (55, 58, 60, 62):
            for z in (-1.8, -2.0, -2.4):
                for hold in (12, 14, 18):
                    key = "codex0725t4rw_%s5_h1sh32_r%s_z%s_h%s" % (
                        tag, rc, ztag(z), hold)
                    out.append(mk(
                        key, "%s5 H1顺势空EMA32·0725RW" % tag.upper(), sym,
                        "short", "5m", hold,
                        "%s 5m H1空+RSI下穿%s+close<ema32+z>%s；RSI<=38止盈。"
                        % (tag.upper(), rc, z),
                        {"all": [
                            {"id": "h1", "left": {"feature": "h1_ema19"}, "op": "lt",
                             "right": {"feature": "h1_ema53"}},
                            {"id": "slope", "left": {"feature": "h1_slope4"}, "op": "lt",
                             "right": {"value": 0}},
                            {"id": "rsi", "left": {"feature": "rsi14"}, "op": "cross_below",
                             "right": {"value": float(rc)}},
                            {"id": "px", "left": {"feature": "close"}, "op": "lt",
                             "right": {"feature": "ema32"}},
                            {"id": "z", "left": {"feature": "z20"}, "op": "gt",
                             "right": {"value": float(z)}},
                        ]},
                        {"any": [
                            {"id": "tp", "left": {"feature": "rsi14"}, "op": "lt",
                             "right": {"value": 38.0}, "role": "take_profit"},
                            {"id": "inv", "left": {"feature": "close"}, "op": "gt",
                             "right": {"feature": "prev_high20"}, "role": "invalidation"},
                        ]},
                    ))
    return out


def hard_ok(m):
    return (
        m["trades"] >= MIN_TRADES
        and m["wr"] >= MIN_WR
        and m["mean"] > 0
        and m["streak"] <= MAX_STREAK
        and m.get("fold_ok")
    )


def main():
    log("REWRITE START")
    raw = build()
    seen, uniq = set(), []
    for obj in raw:
        if obj["supported_instruments"][0] in BAN_SYM:
            continue
        if obj["key"] in SKIP_KEYS or obj["key"] in seen:
            continue
        seen.add(obj["key"])
        try:
            uniq.append(dsl.validate_strategy(obj))
        except Exception:
            continue
    log("CANDS %s" % len(uniq))
    try:
        catalog = codex._existing_strategy_catalog()
    except Exception:
        catalog = []
    log("CATALOG %s" % len(catalog or []))

    frames, frs = {}, {}
    need = {}
    for d in uniq:
        need[(d["supported_instruments"][0], d["timeframe"])] = True
    for (sym, tf) in sorted(need.keys()):
        t0 = time.time()
        f = pipeline._frame(sym, tf)
        if len(f) > BARS:
            f = f.iloc[-BARS:]
        frames[(sym, tf)] = f
        frs[sym] = eco._friction_scenario(sym, "observed_base")
        log("PRELOAD %s %s n=%s dt=%.1f" % (sym, tf, len(f), time.time() - t0))

    scored, near_miss = [], []
    for i, definition in enumerate(uniq):
        try:
            if catalog and dsl.find_near_duplicate(definition, catalog):
                continue
        except Exception:
            pass
        sym = definition["supported_instruments"][0]
        tf = definition["timeframe"]
        frame, fr = frames[(sym, tf)], frs[sym]
        try:
            res = dsl.backtest_dsl(
                frame, definition, leverage=20, stop_loss_pct=0.009,
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
        if (i + 1) % 80 == 0:
            log("progress %s/%s pool=%s miss=%s" % (
                i + 1, len(uniq), len(scored), len(near_miss)))
        score = (m["wr"] + m["mean"] * 1000 - m["streak"] * 6
                 + min(m["trades"], 80) * 0.5 + (50 if m.get("fold_ok") else 0))
        row = {"score": score, "dsl": definition, "metrics": m,
               "symbol": sym, "name": definition.get("name")}
        if hard_ok(m):
            scored.append(row)
            log("KEEP %s %s %s score=%.1f" % (definition["key"], sym, m, score))
        elif m["trades"] >= 16 and m["wr"] >= 58 and m["mean"] > 0:
            near_miss.append(row)
        if len(scored) >= 40:
            log("POOL_CAP 40")
            break

    scored.sort(key=lambda x: -x["score"])
    near_miss.sort(key=lambda x: -x["score"])
    log("POOL_N %s NEAR %s" % (len(scored), len(near_miss)))
    (OUT / "candidates_kept_rw.json").write_text(json.dumps([
        {"key": r["dsl"]["key"], "score": r["score"], "metrics": r["metrics"],
         "symbol": r["symbol"], "name": r["name"], "dsl": r["dsl"]}
        for r in scored[:30]
    ], ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "near_misses_rw.json").write_text(json.dumps([
        {"key": r["dsl"]["key"], "score": r["score"], "metrics": r["metrics"],
         "symbol": r["symbol"], "name": r["name"]}
        for r in near_miss[:40]
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    pool = scored
    if not pool:
        log("RELAX_POOL from near_miss")
        pool = [r for r in near_miss
                if r["metrics"]["trades"] >= 18 and r["metrics"]["wr"] >= 60
                and r["metrics"].get("fold_ok") and r["metrics"]["streak"] <= 3]
        pool.sort(key=lambda x: -x["score"])

    passed, attempted = None, []
    for row in pool[:12]:
        definition = row["dsl"]
        m = row["metrics"]
        if m["trades"] < 18 or m["wr"] < 60 or not m.get("fold_ok"):
            log("SKIP_WEAK %s %s" % (definition["key"], m))
            continue
        log("SUBMIT %s %s" % (definition["key"], m))
        out = codex.submit_codex_strategy(definition, meta={
            "symbol": definition["supported_instruments"][0],
            "timeframe": definition["timeframe"],
            "thesis": definition.get("description"),
            "author": "codex_0725_train4_rewrite",
        }, dry_run=False)
        ai_rev = out.get("ai_review") or {}
        avg = ai_rev.get("ai_theoretical_wr_avg")
        slim = {
            "ok": out.get("ok"), "key": definition["key"],
            "name": definition.get("name"),
            "symbol": definition["supported_instruments"][0],
            "pushed": out.get("pushed"), "stage": out.get("stage"),
            "avg": avg,
            "fail": ai_rev.get("fail_reasons") or out.get("reason"),
            "three": three_ok(ai_rev), "metrics": m,
            "reviews": [{
                "p": r.get("provider"), "d": r.get("decision"),
                "wr": r.get("theoretical_win_rate_pct"),
                "risk": r.get("stop_cluster_risk"), "ok": r.get("ok"),
                "reason": str(r.get("reason") or "")[:220],
            } for r in (ai_rev.get("reviews") or [])],
        }
        if out.get("pushed") and avg is not None and float(avg) < MIN_AI_SOFT:
            log("AI_TOO_LOW reject %s avg=%s" % (definition["key"], avg))
            try:
                pipeline.reject(
                    definition["key"],
                    reason="REWRITE软门槛：AI理论胜率%.1f%%<65%%，自动拒绝不进人工确认" % float(avg),
                )
            except Exception as e:
                log("reject_err %s" % e)
            slim["pushed"] = False
            slim["auto_rejected"] = True
            slim["ok"] = False
        elif out.get("pushed") and avg is not None and float(avg) < MIN_AI_PUSH:
            log("AI_BELOW_TARGET reject %s avg=%s" % (definition["key"], avg))
            try:
                pipeline.reject(
                    definition["key"],
                    reason="REWRITE目标≥70%%：当前AI理论胜率%.1f%%，继续重写" % float(avg),
                )
            except Exception as e:
                log("reject_err %s" % e)
            slim["pushed"] = False
            slim["auto_rejected"] = True
            slim["ok"] = False
        log("SUBMIT_OUT %s" % json.dumps(slim, ensure_ascii=False))
        attempted.append(slim)
        (OUT / "submit_attempts_rw.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2), encoding="utf-8")
        with (OUT / "train_audit.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"event": "submit_rw", "out": slim, "time": now()},
                               ensure_ascii=False) + "\n")
        if (slim.get("ok") and slim.get("pushed") and slim.get("three")
                and avg is not None and float(avg) >= MIN_AI_PUSH):
            passed = out
            passed["name"] = definition.get("name")
            passed["dsl"] = definition
            passed["ai_avg"] = avg
            passed["metrics"] = m
            break
        time.sleep(2)

    status = {
        "time": now(), "pool": len(scored), "near": len(near_miss),
        "passed": bool(passed),
        "passed_key": (passed or {}).get("key"),
        "passed_name": (passed or {}).get("name"),
        "ai_avg": (passed or {}).get("ai_avg"),
        "metrics": (passed or {}).get("metrics"),
        "attempted": attempted,
    }
    (OUT / "final_status_rw.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    log("FINAL %s" % json.dumps(
        {k: status[k] for k in status if k != "attempted"}, ensure_ascii=False))
    if passed:
        try:
            import auto_trade_formal_notify as n
            import auto_trade_strategy_titles as titles
            shown = titles.short_strategy_title(passed.get("key"), passed.get("name"))
            sym = (passed.get("dsl") or {}).get("supported_instruments", [""])[0]
            n.send_message(
                "【7.25训练4·REWRITE】\n高胜率异标的 · 三AI通过\n%s\n标的: %s\nAI理论WR: %s\n时间: %s"
                % (shown, sym, passed.get("ai_avg"), now()),
                kind="codex_strategy_review",
                meta={"key": passed.get("key"), "strategy_name": passed.get("name")},
            )
        except Exception as e:
            log("wx_err %s" % e)


if __name__ == "__main__":
    main()
