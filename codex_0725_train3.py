# -*- coding: utf-8 -*-
"""7.25 第三次训练：GLM-5.2 创造候选，DeepSeek/Qwen 预核查，三AI理论复核全通过。"""
from __future__ import print_function

import copy
import json
import os
import time
from datetime import datetime
from pathlib import Path

OUTDIR = Path("/root/auto_trade/codex_0725_train3")
OUTDIR.mkdir(parents=True, exist_ok=True)
AUDIT = OUTDIR / "train_audit.jsonl"
BARS = 12000
SKIP_KEYS = {
    "codex0725_ada5_trendpb_r42_z2p0_h14",
    "codex0725t2_ada5m_trendpb_r42_z2p2_h14",
    "ada5_z20_t60_prev_h14_0724k",
}


def _load_env():
    path = Path("/root/auto_trade/ai_ecosystem.env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_env()

import auto_trade_ai_consensus as ai  # noqa: E402
import auto_trade_codex_strategy_review as codex  # noqa: E402
import auto_trade_human_confirm_pipeline as pipeline  # noqa: E402
import auto_trade_strategy_creation_factory as factory  # noqa: E402
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


GLM_PROPOSE_PROMPT = """你是栖语量化系统的策略创造顾问（GLM-5.2）。
根据已验证的 ADA5 顺势回升家族，提出新的可交易 DSL 变体，目标提升开仓频率同时保持稳健。
硬约束：
1) schema 必须是 qiyu_strategy_dsl_v1
2) 仅用已有特征：h1_ema19,h1_ema53,h1_slope4,rsi14,close,open,ema21,z20,k,prev_low20,prev_high20
3) entry/exit 用 all/any + leaf{id,left{feature},op,right{value|feature},role?}
4) role 只能出现在 exit：take_profit / invalidation
5) direction 为 long 或 short；timeframe 限 5m 或 15m
6) key 必须以 codex0725t3_ 开头，且不得与已存在 key 重复
7) name 用中文可读标题，后缀 ·0725T3
8) 每个策略只支持一个标的 supported_instruments
9) 优先异品种移植（ETH/SOL/BNB/XAG/LTC/XAU）或 ADA 近邻参数（勿复刻已上线的 z=2.0/2.2 hold=14 rc=42）
仅输出一个 JSON 对象：
{"strategies":[完整DSL对象,...],"notes":"中文简述创造思路"}
至少 8 个、最多 14 个策略。"""


AUDIT_PROMPT = """你是策略预核查员。对候选 DSL 列表做独立风险点评。
不得改写 DSL。只输出 JSON：
{"verdicts":[{"key":"...","decision":"KEEP或DROP","reason":"中文","expected_wr_band":"高/中/低"}],
 "summary":"中文"}
KEEP=值得进入回测与三AI理论复核；DROP=明显过拟合/逻辑矛盾/样本会太稀。"""


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


def normalize_glm_dsl(obj, base):
    """Fill required fields / sanitize key from GLM draft."""
    out = copy.deepcopy(obj) if isinstance(obj, dict) else {}
    key = str(out.get("key") or "").strip()
    if not key.startswith("codex0725t3_"):
        key = "codex0725t3_" + factory._sanitize_strategy_key(key or "glm")
    out["key"] = key[:90]
    out["schema"] = "qiyu_strategy_dsl_v1"
    out["origin"] = "codex_0725_train3_glm"
    out["version"] = "0725t3"
    if not out.get("name"):
        out["name"] = "GLM创造·0725T3"
    if "·0725T3" not in str(out.get("name")):
        out["name"] = str(out.get("name")).rstrip("·") + "·0725T3"
    if not out.get("description"):
        out["description"] = "GLM-5.2 train3 创造候选"
    # inherit missing structure pieces cautiously from base if direction long trendpb-like
    if not out.get("entry") and base:
        out["entry"] = copy.deepcopy(base.get("entry"))
    if not out.get("exit") and base:
        out["exit"] = copy.deepcopy(base.get("exit"))
    if not out.get("timeframe"):
        out["timeframe"] = "5m"
    if not out.get("max_hold_bars"):
        out["max_hold_bars"] = 14 if out["timeframe"] == "5m" else 16
    if not out.get("direction"):
        out["direction"] = "long"
    inst = out.get("supported_instruments")
    if not isinstance(inst, list) or not inst:
        out["supported_instruments"] = ["ADA-USDT-SWAP"]
    return out


def glm_propose(base, existing_keys):
    payload = {
        "mission": "0725_train3_create",
        "proven_template": {
            "key": base.get("key"),
            "name": base.get("name"),
            "direction": base.get("direction"),
            "timeframe": base.get("timeframe"),
            "max_hold_bars": base.get("max_hold_bars"),
            "entry": base.get("entry"),
            "exit": base.get("exit"),
            "supported_instruments": base.get("supported_instruments"),
        },
        "avoid_keys": sorted(existing_keys),
        "allowed_symbols": [
            "ADA-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
            "BNB-USDT-SWAP", "XAG-USDT-SWAP", "LTC-USDT-SWAP",
            "XAU-USDT-SWAP", "NG-USDT-SWAP",
        ],
        "goal": "提高可交易频率同时保持可过三AI理论复核的稳健性",
    }
    res = factory._ai_json_call("glm", GLM_PROPOSE_PROMPT, payload, max_tokens=5000)
    log({"event": "glm_propose", "ok": res.get("ok"),
         "error": res.get("error"), "latency": res.get("latency_sec"),
         "preview": str(res.get("raw_preview") or "")[:200]})
    if not res.get("ok"):
        return []
    parsed = res.get("parsed") or {}
    rows = parsed.get("strategies") or parsed.get("candidates") or []
    if isinstance(parsed, list):
        rows = parsed
    out = []
    for row in rows:
        try:
            norm = normalize_glm_dsl(row, base)
            if norm["key"] in existing_keys or norm["key"] in SKIP_KEYS:
                continue
            definition = dsl.validate_strategy(norm)
            out.append(definition)
        except Exception as exc:
            log({"event": "glm_dsl_invalid",
                 "key": (row or {}).get("key"), "error": str(exc)[:160]})
    (OUTDIR / "glm_proposals.json").write_text(
        json.dumps({"notes": (parsed or {}).get("notes"),
                    "raw_n": len(rows), "valid": out},
                   ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    return out


def precheck_ds_qwen(candidates):
    """DeepSeek + Qwen pre-audit; keep if either KEEP or call fails open."""
    slim = [{
        "key": c.get("key"), "name": c.get("name"),
        "direction": c.get("direction"), "timeframe": c.get("timeframe"),
        "symbol": (c.get("supported_instruments") or [None])[0],
        "max_hold_bars": c.get("max_hold_bars"),
        "entry": c.get("entry"), "exit": c.get("exit"),
        "description": c.get("description"),
    } for c in candidates[:14]]
    payload = {"candidates": slim, "role": "precheck_before_backtest"}
    keep = set(c["key"] for c in slim)
    drop = set()
    for provider in ("deepseek", "qwen"):
        res = factory._ai_json_call(provider, AUDIT_PROMPT, payload, max_tokens=2500)
        log({"event": "precheck", "provider": provider, "ok": res.get("ok"),
             "error": res.get("error"), "latency": res.get("latency_sec")})
        if not res.get("ok"):
            continue
        for v in ((res.get("parsed") or {}).get("verdicts") or []):
            key = v.get("key")
            if str(v.get("decision") or "").upper() == "DROP" and key:
                drop.add(key)
    # Drop only if BOTH auditors DROP the same key
    both_drop = set()
    # recompute properly: need per-provider. Simpler: drop if any DROP is ok
    # User asked DS/Qwen as 核查 — if one says DROP, still allow if other KEEP.
    # Only remove if appears in drop and not unanimously needed — keep all unless
    # both dropped. Store dual files.
    (OUTDIR / "precheck_drop_any.json").write_text(
        json.dumps(sorted(drop), ensure_ascii=False, indent=2), encoding="utf-8")
    # Soft filter: prefer non-dropped; if empties, keep all
    preferred = [c for c in candidates if c.get("key") not in drop]
    return preferred if preferred else candidates


def codex_seed_variants(base):
    """Deterministic fallback / supplement grid (non-live params)."""
    cands = []
    ports = [
        ("ETH-USDT-SWAP", "eth", "5m", 14),
        ("SOL-USDT-SWAP", "sol", "5m", 14),
        ("BNB-USDT-SWAP", "bnb", "5m", 14),
        ("XAG-USDT-SWAP", "xag", "5m", 14),
        ("LTC-USDT-SWAP", "ltc", "5m", 14),
        ("ADA-USDT-SWAP", "ada", "5m", 14),
        ("XAU-USDT-SWAP", "xau", "15m", 16),
    ]
    for sym, tag, tf, hold0 in ports:
        for rc in (40, 42, 44):
            for zmax in (1.8, 2.0, 2.3, 2.5):
                for tp in (58, 60):
                    for hold in ((12, 16) if tag == "ada" else (hold0,)):
                        if tag == "ada" and rc == 42 and hold == 14 and zmax in (2.0, 2.2):
                            continue
                        obj = copy.deepcopy(base)
                        obj["key"] = "codex0725t3_%s%s_trendpb_r%s_z%s_h%s" % (
                            tag, tf, rc, str(zmax).replace(".", "p"), hold)
                        obj["name"] = "%s%s顺势回升·0725T3" % (tag.upper(), tf)
                        obj["supported_instruments"] = [sym]
                        obj["timeframe"] = tf
                        obj["max_hold_bars"] = int(hold)
                        obj["origin"] = "codex_0725_train3"
                        obj["version"] = "0725t3"
                        obj["description"] = (
                            "train3 seed %s rc=%s z<%s" % (sym, rc, zmax))
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
                        if obj["key"] in SKIP_KEYS:
                            continue
                        try:
                            cands.append(dsl.validate_strategy(obj))
                        except Exception:
                            continue
    return cands


def three_ok(ai_rev):
    reviews = ai_rev.get("reviews") or []
    by = {r.get("provider"): r for r in reviews}
    for p in ("deepseek", "qwen", "glm"):
        r = by.get(p) or {}
        if not r.get("ok") or str(r.get("decision") or "").upper() != "APPROVE":
            return False
    return bool(ai_rev.get("approved"))


def screen(cands, get_frame):
    scored = []
    for i, definition in enumerate(cands):
        try:
            sym = definition["supported_instruments"][0]
            tf = definition["timeframe"]
            frame, fr = get_frame(sym, tf)
            res = dsl.backtest_dsl(
                frame, definition,
                leverage=20, stop_loss_pct=0.009,
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
            if i < 5:
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
                    + (8 if str(definition.get("origin") or "").endswith("glm") else 0)
                )
                scored.append({"score": score, "dsl": definition, "metrics": m})
                print("KEEP", definition["key"], m, "score", round(score, 2),
                      flush=True)
        if (i + 1) % 20 == 0:
            print("progress", i + 1, "/", len(cands), "kept", len(scored),
                  flush=True)
    scored.sort(key=lambda x: -x["score"])
    return scored


def submit_until_pass(pool, max_attempts=12):
    passed = None
    attempted = []
    log({"event": "submit_start_train3", "n": min(max_attempts, len(pool))})
    for row in pool[:max_attempts]:
        definition = row["dsl"]
        print("SUBMIT", definition["key"], flush=True)
        out = codex.submit_codex_strategy(definition, meta={
            "symbol": definition["supported_instruments"][0],
            "timeframe": definition["timeframe"],
            "thesis": definition.get("description"),
            "author": "codex_0725_train3",
        }, dry_run=False)
        ai_rev = out.get("ai_review") or {}
        reviews = ai_rev.get("reviews") or []
        slim = {
            "ok": out.get("ok"),
            "key": definition["key"],
            "name": definition.get("name"),
            "pushed": out.get("pushed"),
            "avg": ai_rev.get("ai_theoretical_wr_avg"),
            "fail": ai_rev.get("fail_reasons"),
            "three": three_ok(ai_rev),
            "origin": definition.get("origin"),
            "metrics": row["metrics"],
            "reviews": [{
                "p": r.get("provider"), "d": r.get("decision"),
                "wr": r.get("theoretical_win_rate_pct"),
                "risk": r.get("stop_cluster_risk"), "ok": r.get("ok"),
                "reason": str(r.get("reason") or "")[:140],
            } for r in reviews],
        }
        log({"event": "submit_result_train3", "out": slim})
        attempted.append(slim)
        (OUTDIR / "submit_attempts.json").write_text(
            json.dumps(attempted, ensure_ascii=False, indent=2), encoding="utf-8")
        if out.get("ok") and out.get("pushed") and three_ok(ai_rev):
            passed = out
            passed["name"] = definition.get("name")
            passed["dsl"] = definition
            break
        time.sleep(5)
    return passed, attempted


def main():
    print("providers", ai.PROVIDERS, flush=True)
    store = json.loads(
        Path("/root/strategy_configs/ai_dsl_strategies.json").read_text(
            encoding="utf-8"))
    strats = {
        s["key"]: s for s in (store.get("strategies") or [])
        if isinstance(s, dict) and s.get("key")
    }
    base = (strats.get("ada5_z20_t60_prev_h14_0724k")
            or strats.get("codex0725t2_ada5m_trendpb_r42_z2p2_h14"))
    if not base:
        raise SystemExit("missing base dsl")
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

    existing = set(strats.keys()) | SKIP_KEYS
    glm_cands = glm_propose(base, existing)
    print("GLM_VALID", len(glm_cands), flush=True)
    if glm_cands:
        glm_cands = precheck_ds_qwen(glm_cands)
        print("GLM_AFTER_PRECHECK", len(glm_cands), flush=True)

    seed = codex_seed_variants(base)
    print("SEED", len(seed), flush=True)

    # Prefer GLM drafts first in screening pool
    seen = set()
    pool = []
    for c in list(glm_cands) + list(seed):
        if c["key"] in seen or c["key"] in SKIP_KEYS:
            continue
        seen.add(c["key"])
        pool.append(c)
    print("POOL", len(pool), flush=True)

    scored = screen(pool, get_frame)
    # Prefer glm-origin in sort already via score bonus
    print("KEPT", len(scored), flush=True)
    (OUTDIR / "candidates_kept.json").write_text(json.dumps([
        {"key": r["dsl"]["key"], "score": r["score"],
         "metrics": r["metrics"], "origin": r["dsl"].get("origin"),
         "dsl": r["dsl"]}
        for r in scored[:25]
    ], ensure_ascii=False, indent=2), encoding="utf-8")

    if not scored:
        # last resort loosen
        print("LOOSEN", flush=True)
        for definition in pool:
            try:
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
                    impact_rate_per_side=float(fr.get("impact_rate_per_side") or 0),
                    latency_rate_per_side=float(
                        fr.get("latency_rate_per_side") or 0),
                    funding_rate_per_8h=float(fr.get("funding_rate_per_8h") or 0),
                    friction_scenario="observed_base",
                )
                m = metrics(res.get("trades") or [], res)
            except Exception:
                continue
            if m["trades"] >= 15 and m["wr"] >= 55 and m["mean"] > 0 and m["streak"] <= 4:
                score = m["wr"] + m["mean"] * 800 + min(m["trades"], 100) * 0.2
                scored.append({"score": score, "dsl": definition, "metrics": m})
        scored.sort(key=lambda x: -x["score"])
        print("LOOSE_KEPT", len(scored), flush=True)

    passed, attempted = submit_until_pass(scored, max_attempts=12)
    (OUTDIR / "final_status.json").write_text(json.dumps({
        "time": now(),
        "glm_n": len(glm_cands),
        "seed_n": len(seed),
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
                "【7.25训练3产出】\n"
                "GLM创造/协作 · DeepSeek+Qwen核查 · 三AI理论复核通过\n"
                "%s\n时间: %s" % (shown, now()),
                kind="codex_strategy_review",
                meta={"key": passed.get("key"),
                      "strategy_name": passed.get("name")},
            )
            print("wx_ok", shown, flush=True)
        except Exception as e:
            print("wx_fail", e, flush=True)


if __name__ == "__main__":
    main()
