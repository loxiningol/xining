# -*- coding: utf-8 -*-
"""Train3 wave-B: GLM proposes compact param variants; Codex builds DSL; 3AI submit."""
from __future__ import print_function

import copy
import json
import os
import time
from datetime import datetime
from pathlib import Path

OUT = Path("/root/auto_trade/codex_0725_train3")
OUT.mkdir(parents=True, exist_ok=True)
SKIP = {
    "codex0725_ada5_trendpb_r42_z2p0_h14",
    "codex0725t2_ada5m_trendpb_r42_z2p2_h14",
    "ada5_z20_t60_prev_h14_0724k",
}

for line in Path("/root/auto_trade/ai_ecosystem.env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

import auto_trade_ai_consensus as ai
import auto_trade_codex_strategy_review as codex
import auto_trade_human_confirm_pipeline as pipeline
import auto_trade_strategy_creation_factory as factory
import auto_trade_strategy_dsl as dsl
import auto_trade_strategy_ecosystem as eco
import urllib.request as ur

BARS = 12000


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(row):
    row = dict(row)
    row["time"] = now()
    with (OUT / "train_audit.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)


def glm_param_variants():
    cfg = ai._provider_config("glm")
    prompt = (
        "你是量化参数设计器。只输出一个JSON对象，不要推理过程。"
        '格式严格为: {"variants":[{"symbol":"ETH-USDT-SWAP","tag":"eth",'
        '"rc":40,"zmax":2.3,"hold":16,"tp":60,"name":"ETH5顺势回升"},...],'
        '"notes":"一句中文"}。'
        "给出12个异于已上线ADA(rc=42,z=2.0/2.2,hold=14)的变体。"
        "symbol仅限: ETH-USDT-SWAP,SOL-USDT-SWAP,BNB-USDT-SWAP,"
        "XAG-USDT-SWAP,LTC-USDT-SWAP,ADA-USDT-SWAP,XAU-USDT-SWAP。"
        "XAU用hold=16，其余hold取12/14/16。rc取38-45，zmax取1.6-2.6，tp取55-62。"
    )
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": '{"task":"0725_train3_param_variants","n":12}'},
        ],
        "temperature": 0.15,
        "max_tokens": 2000,
    }
    req = ur.Request(
        cfg["url"], data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + cfg["api_key"],
                 "Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    with ur.urlopen(req, timeout=150) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    msg = (raw.get("choices") or [{}])[0].get("message") or {}
    content = msg.get("content") or ""
    (OUT / "glm_params_raw.txt").write_text(content, encoding="utf-8")
    log({"event": "glm_params", "latency": round(time.time() - t0, 2),
         "content_len": len(content)})
    parsed = None
    try:
        parsed = ai._parse_content_json(content)
    except Exception:
        s = content.find("{")
        e = content.rfind("}")
        if s >= 0 and e > s:
            try:
                parsed = json.loads(content[s:e + 1])
            except Exception:
                parsed = None
    variants = []
    if isinstance(parsed, dict):
        variants = parsed.get("variants") or parsed.get("strategies") or []
    (OUT / "glm_params.json").write_text(
        json.dumps(parsed or {"raw": content[:2000]}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return variants if isinstance(variants, list) else []


def precheck(variants):
    if not variants:
        return []
    payload = {"variants": variants[:14], "role": "param_precheck"}
    prompt = (
        "你是策略参数预核查员。对variants点评。"
        '只输出JSON: {"verdicts":[{"index":0,"decision":"KEEP或DROP","reason":"..."}],'
        '"summary":"..."}'
    )
    drop_idx = set()
    votes = {i: [] for i in range(len(variants[:14]))}
    for provider in ("deepseek", "qwen"):
        res = factory._ai_json_call(provider, prompt, payload, max_tokens=1800)
        log({"event": "param_precheck", "provider": provider,
             "ok": res.get("ok"), "error": res.get("error")})
        if not res.get("ok"):
            continue
        for v in ((res.get("parsed") or {}).get("verdicts") or []):
            try:
                idx = int(v.get("index"))
            except Exception:
                continue
            votes.setdefault(idx, []).append(
                str(v.get("decision") or "").upper())
    kept = []
    for i, row in enumerate(variants[:14]):
        ds = votes.get(i) or []
        if ds.count("DROP") >= 2:
            continue
        kept.append(row)
    return kept or variants[:12]


def build_from_variants(base, variants):
    cands = []
    for row in variants:
        try:
            sym = str(row.get("symbol") or "ADA-USDT-SWAP")
            tag = str(row.get("tag") or sym.split("-")[0].lower())[:8]
            rc = float(row.get("rc") or 42)
            zmax = float(row.get("zmax") or 2.0)
            hold = int(row.get("hold") or 14)
            tp = float(row.get("tp") or 60)
            tf = "15m" if "XAU" in sym else "5m"
            if tf == "15m":
                hold = 16
            key = "codex0725t3_%s%s_glm_r%s_z%s_h%s" % (
                tag, tf, int(rc), str(zmax).replace(".", "p"), hold)
            if key in SKIP:
                continue
            if (tag.startswith("ada") and int(rc) == 42 and hold == 14
                    and float(zmax) in (2.0, 2.2)):
                continue
            obj = copy.deepcopy(base)
            obj["key"] = key
            name = str(row.get("name") or ("%s顺势回升" % tag.upper()))
            if "·0725T3" not in name:
                name = name + "·0725T3"
            obj["name"] = name
            obj["supported_instruments"] = [sym]
            obj["timeframe"] = tf
            obj["max_hold_bars"] = hold
            obj["origin"] = "codex_0725_train3_glm"
            obj["version"] = "0725t3"
            obj["description"] = (
                "GLM参数创造 train3 %s rc=%s z<%s hold=%s" % (sym, rc, zmax, hold))
            for cond in obj["entry"]["all"]:
                if cond.get("id") == "rsi":
                    cond["right"] = {"value": float(rc)}
                if cond.get("id") == "z":
                    cond["right"] = {"value": float(zmax)}
            for cond in obj["exit"]["any"]:
                if cond.get("id") == "tp" or cond.get("role") == "take_profit":
                    if "rsi" in str(cond.get("left")):
                        cond["right"] = {"value": float(tp)}
            cands.append(dsl.validate_strategy(obj))
        except Exception as exc:
            log({"event": "build_fail", "row": row, "error": str(exc)[:120]})
    return cands


def seed_extra(base):
    """High-probability ADA/ETH neighbors not yet live."""
    specs = [
        ("ADA-USDT-SWAP", "ada", 40, 2.0, 14, 60),
        ("ADA-USDT-SWAP", "ada", 44, 2.0, 14, 60),
        ("ADA-USDT-SWAP", "ada", 42, 2.3, 14, 60),
        ("ADA-USDT-SWAP", "ada", 42, 2.5, 14, 60),
        ("ADA-USDT-SWAP", "ada", 42, 1.8, 16, 58),
        ("ADA-USDT-SWAP", "ada", 40, 2.2, 16, 60),
        ("ETH-USDT-SWAP", "eth", 42, 2.0, 14, 60),
        ("ETH-USDT-SWAP", "eth", 42, 2.3, 14, 60),
        ("SOL-USDT-SWAP", "sol", 42, 2.0, 14, 60),
        ("BNB-USDT-SWAP", "bnb", 42, 2.0, 14, 60),
        ("XAG-USDT-SWAP", "xag", 42, 2.0, 14, 60),
        ("LTC-USDT-SWAP", "ltc", 42, 2.0, 14, 60),
    ]
    out = []
    for sym, tag, rc, zmax, hold, tp in specs:
        obj = copy.deepcopy(base)
        obj["key"] = "codex0725t3_%s5m_trendpb_r%s_z%s_h%s" % (
            tag, rc, str(zmax).replace(".", "p"), hold)
        if obj["key"] in SKIP:
            continue
        obj["name"] = "%s5顺势回升·0725T3" % tag.upper()
        obj["supported_instruments"] = [sym]
        obj["max_hold_bars"] = hold
        obj["origin"] = "codex_0725_train3"
        obj["version"] = "0725t3"
        obj["description"] = "train3 seed %s" % obj["key"]
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
            out.append(dsl.validate_strategy(obj))
        except Exception:
            pass
    return out


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


def main():
    store = json.loads(
        Path("/root/strategy_configs/ai_dsl_strategies.json").read_text())
    strats = {s["key"]: s for s in store.get("strategies") or []
              if isinstance(s, dict) and s.get("key")}
    base = strats.get("ada5_z20_t60_prev_h14_0724k")
    print("base", base["key"], flush=True)

    variants = glm_param_variants()
    print("GLM_VARIANTS", len(variants), flush=True)
    if variants:
        variants = precheck(variants)
        print("AFTER_PRECHECK", len(variants), flush=True)
    glm_dsl = build_from_variants(base, variants) if variants else []
    seed = seed_extra(base)
    print("GLM_DSL", len(glm_dsl), "SEED", len(seed), flush=True)

    pool = []
    seen = set()
    for c in list(glm_dsl) + list(seed):
        if c["key"] in seen or c["key"] in SKIP:
            continue
        seen.add(c["key"])
        pool.append(c)

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
    for definition in pool:
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
            print("bt_fail", definition["key"], exc, flush=True)
            continue
        if m["trades"] >= 18 and m["wr"] >= 56 and m["mean"] > 0 and m["streak"] <= 4:
            if (not m.get("fold_ok")) and (m["trades"] < 26 or m["wr"] < 60):
                print("NEAR", definition["key"], m, flush=True)
                continue
            score = (m["wr"] + m["mean"] * 900 - m["streak"] * 4
                     + min(m["trades"], 200) * 0.3
                     + (35 if m.get("fold_ok") else 0)
                     + (10 if "glm" in str(definition.get("origin")) else 0))
            scored.append({"score": score, "dsl": definition, "metrics": m})
            print("KEEP", definition["key"], m, round(score, 2), flush=True)
        else:
            print("SKIP", definition["key"], m, flush=True)
    scored.sort(key=lambda x: -x["score"])
    print("KEPT", len(scored), flush=True)
    (OUT / "candidates_kept_b.json").write_text(json.dumps([
        {"key": r["dsl"]["key"], "score": r["score"], "metrics": r["metrics"],
         "origin": r["dsl"].get("origin"), "dsl": r["dsl"]}
        for r in scored[:20]
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    passed = None
    attempted = []
    for row in scored[:12]:
        definition = row["dsl"]
        print("SUBMIT", definition["key"], flush=True)
        out = codex.submit_codex_strategy(definition, meta={
            "symbol": definition["supported_instruments"][0],
            "timeframe": definition["timeframe"],
            "thesis": definition.get("description"),
            "author": "codex_0725_train3",
        }, dry_run=False)
        ai_rev = out.get("ai_review") or {}
        slim = {
            "ok": out.get("ok"), "key": definition["key"],
            "name": definition.get("name"), "pushed": out.get("pushed"),
            "avg": ai_rev.get("ai_theoretical_wr_avg"),
            "fail": ai_rev.get("fail_reasons"), "three": three_ok(ai_rev),
            "origin": definition.get("origin"), "metrics": row["metrics"],
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
        "time": now(), "passed": bool(passed),
        "passed_key": (passed or {}).get("key"),
        "passed_name": (passed or {}).get("name"),
        "queued": (passed or {}).get("queued"),
        "attempted": attempted,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("FINAL_PASSED", bool(passed), (passed or {}).get("key"),
          (passed or {}).get("name"), flush=True)
    if passed:
        import auto_trade_formal_notify as n
        import auto_trade_strategy_titles as titles
        shown = titles.short_strategy_title(passed.get("key"), passed.get("name"))
        n.send_message(
            "【7.25训练3产出】\n"
            "GLM参数创造 · DeepSeek/Qwen预核查 · 三AI理论复核通过\n"
            "%s\n时间: %s" % (shown, now()),
            kind="codex_strategy_review",
            meta={"key": passed.get("key"), "strategy_name": passed.get("name")},
        )
        print("wx_ok", shown, flush=True)


if __name__ == "__main__":
    main()
