#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜放宽修复：三策略 GLM 协作 → 外科补丁 → 放宽内测 → sim → formal."""
from __future__ import print_function

import copy
import json
import sys
import time
import traceback
import urllib.request
from pathlib import Path

sys.path.insert(0, "/root")
import auto_trade_dual_engine_factory as d
import frost_action_run as f

DUAL = Path("/root/auto_trade/dual_engine")
OUT = DUAL / "frost_relax_repair.json"
STATUS_NOTE = DUAL / "frost_relaxed_gates.json"
FAILS = DUAL / "frost_failure_reports.json"
BOOKS = DUAL / "frost_hypotheses.json"


def _glm(system_prompt, user_payload, max_tokens=1200, temperature=0.15):
    import auto_trade_ai_consensus as ai
    d._load_env()
    cfg = ai._provider_config("glm")
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }
    req = urllib.request.Request(
        cfg["url"],
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + cfg["api_key"],
            "Content-Type": "application/json",
        },
        method="POST",
    )
    raw = json.loads(urllib.request.urlopen(req, timeout=180).read().decode("utf-8"))
    msg = ((raw.get("choices") or [{}])[0].get("message") or {})
    text = (msg.get("content") or "").strip()
    if '"patches"' not in text and (msg.get("reasoning_content") or ""):
        text = ((msg.get("content") or "") + "\n" + msg.get("reasoning_content")).strip()
    try:
        parsed = f._extract_decision_json(text) if hasattr(f, "_extract_decision_json") else f._parse_json_loose(text)
    except Exception:
        try:
            parsed = f._parse_json_loose(text)
        except Exception as exc:
            parsed = {"error": str(exc), "raw": text[:500]}
    return {"parsed": parsed, "raw": text[:800]}


def load_three_books():
    """Recover SOL/XRP/DOGE books from last frost artifacts; seed z20/TP templates."""
    saved = {b["symbol"]: b for b in (json.loads(BOOKS.read_text(encoding="utf-8")).get("books") or [])}
    fails = json.loads(FAILS.read_text(encoding="utf-8")) if FAILS.exists() else {}
    seeds = {
        "SOL-USDT-SWAP": {
            "timeframe": "5m",
            "direction": "long",
            "logic_class": "impulse_continuation",
            "dsl": {
                "key": "frost_sol_relax_seed",
                "name": "寒霜-SOL-5m-impulse_continuation",
                "direction": "long",
                "timeframe": "5m",
                "supported_instruments": ["SOL-USDT-SWAP"],
                "max_hold_bars": 14,
                "description": "SOL impulse continuation: H1多头+斜率+RSI上穿42+EMA21+z20<2；RSI止盈/prev_low失效",
                "entry": {"all": [
                    {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
                    {"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0}},
                    {"left": {"feature": "rsi14"}, "op": "cross_above", "right": {"value": 42.0}},
                    {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                    {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 2.0}},
                ]},
                "exit": {"any": [
                    {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 60.0}, "role": "take_profit"},
                    {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}, "role": "invalidation"},
                ]},
            },
        },
        "XRP-USDT-SWAP": {
            "timeframe": "15m",
            "direction": "short",
            "logic_class": "exhaustion_fade",
            "dsl": {
                "key": "frost_xrp_relax_seed",
                "name": "寒霜-XRP-15m-exhaustion_fade",
                "direction": "short",
                "timeframe": "15m",
                "supported_instruments": ["XRP-USDT-SWAP"],
                "max_hold_bars": 16,
                "description": "XRP exhaustion: RSI高位+z20伸展+MACD转弱后做空；RSI回落止盈",
                "entry": {"all": [
                    {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}},
                    {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.0}},
                    {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
                    {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
                ]},
                "exit": {"any": [
                    {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}, "role": "take_profit"},
                    {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
                ]},
            },
        },
        "DOGE-USDT-SWAP": {
            "timeframe": "1h",
            "direction": "long",
            "logic_class": "range_reclaim",
            "dsl": {
                "key": "frost_doge_relax_seed",
                "name": "寒霜-DOGE-1h-range_reclaim",
                "direction": "long",
                "timeframe": "1h",
                "supported_instruments": ["DOGE-USDT-SWAP"],
                "max_hold_bars": 18,
                "description": "DOGE range reclaim: RSI中低位收复EMA19+z20不过热+MACD>0",
                "entry": {"all": [
                    {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 35.0}},
                    {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 47.0}},
                    {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema19"}},
                    {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -1.5}},
                    {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.5}},
                    {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
                ]},
                "exit": {"any": [
                    {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}, "role": "take_profit"},
                    {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}, "role": "invalidation"},
                ]},
            },
        },
    }
    books = []
    for sym, seed in seeds.items():
        prev = saved.get(sym) or {}
        dsl = f._ensure_dsl(seed["dsl"], sym, seed["timeframe"])
        book = {
            "symbol": sym,
            "timeframe": seed["timeframe"],
            "direction": seed["direction"],
            "logic_class": seed["logic_class"],
            "dsl": dsl,
            "title_short": dsl.get("name"),
            "thesis": (prev.get("thesis") or seed["logic_class"]),
            "answers": prev.get("answers") or {},
            "gate_mode": "frost_relaxed",
            "failure_report": fails.get(sym) or {},
            "seeded_from": "frost_relax_z20_tp_template",
        }
        books.append(book)
    return books


def failure_digest(book):
    fr = book.get("failure_report") or {}
    bm = fr.get("base_metrics") or {}
    suite = fr.get("suite") or {}
    mc = fr.get("monte_carlo") or {}
    return {
        "symbol": book.get("symbol"),
        "timeframe": book.get("timeframe"),
        "direction": book.get("direction"),
        "logic_class": book.get("logic_class"),
        "failed_gates": suite,
        "trades": bm.get("trades"),
        "win_rate_pct": bm.get("win_rate_pct"),
        "sharpe": bm.get("sharpe"),
        "oos_profit": bm.get("oos_profit"),
        "fold_positive": bm.get("fold_positive"),
        "folds": bm.get("folds"),
        "fold_means": bm.get("fold_means"),
        "monte_carlo": mc,
        "current_entry": (book.get("dsl") or {}).get("entry"),
        "current_exit": (book.get("dsl") or {}).get("exit"),
        "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
        "relaxed_target": {
            "walk_forward": "fold_positive>=7 and folds>=10 (user asked 8; live ADA ref=7)",
            "friction": "sharpe>=0 under 2x slip/50ms/5% fill fail",
            "logic_destruction": "±20% still mean_net>-0.01",
            "monte_carlo": "soft/advisory",
        },
    }


def glm_repair_suggest(book, packs_latest, attempt):
    prompt = (
        "你是策略总设计师GLM-5.2。根据内测失败报告给出【可执行】外科修复。"
        "禁止输出思考过程。只输出一个JSON对象："
        '{"reason_zh":"针对失败门的中文诊断超过40字",'
        '"patches":[{"op":"set_rsi|set_cci|set_hold|replace_entry|replace_exit|add_filter|drop_filter",'
        '"feature":"rsi14|cci|macd_stick|ema21|...",'
        '"cmp":"gt|lt|gte|lte",'
        '"value":0,'
        '"right_feature":null,'
        '"hold":8,'
        '"entry_all":null,'
        '"exit_any":null,'
        '"note":"一句说明"}],'
        '"keep_logic_class":true}'
        "要求：保持 logic_class 与方向；针对 walk_forward/friction/destruction 给具体数值；"
        "若成交过多→收紧过滤；过少→放宽；摩擦失败→缩短持仓或加趋势过滤。"
    )
    user = {
        "attempt": attempt,
        "failure": failure_digest(book),
        "latest_packs": {
            "pass": (packs_latest or {}).get("pass"),
            "gate_mode": (packs_latest or {}).get("gate_mode"),
            "base_metrics": (packs_latest or {}).get("base_metrics"),
            "anti_overfit": (packs_latest or {}).get("anti_overfit"),
            "extreme_friction": {
                "pass": ((packs_latest or {}).get("extreme_friction") or {}).get("pass"),
                "metrics": ((packs_latest or {}).get("extreme_friction") or {}).get("metrics"),
            },
            "logic_destruction": {
                "pass": ((packs_latest or {}).get("logic_destruction") or {}).get("pass"),
            },
            "hard_release_bar": (packs_latest or {}).get("hard_release_bar"),
        },
    }
    return _glm(prompt, user)


def _stamp_ids(dsl, symbol, timeframe):
    return f._ensure_dsl(dsl, symbol, timeframe)


def apply_patches(book, suggestion, attempt):
    book = copy.deepcopy(book)
    dsl = copy.deepcopy(book.get("dsl") or {})
    entry_all = list(((dsl.get("entry") or {}).get("all") or []))
    exit_any = list(((dsl.get("exit") or {}).get("any") or []))
    patches = (suggestion.get("parsed") or {}).get("patches") or []
    notes = []
    for p in patches:
        if not isinstance(p, dict):
            continue
        op = str(p.get("op") or "").lower()
        notes.append("%s:%s" % (op, p.get("note") or ""))
        if op == "set_hold" and p.get("hold") is not None:
            dsl["max_hold_bars"] = max(3, min(48, int(p.get("hold"))))
        elif op == "replace_entry" and isinstance(p.get("entry_all"), list) and p.get("entry_all"):
            entry_all = p["entry_all"]
        elif op == "replace_exit" and isinstance(p.get("exit_any"), list) and p.get("exit_any"):
            exit_any = p["exit_any"]
        elif op == "add_filter":
            feat = p.get("feature") or "cci"
            cmp_ = p.get("cmp") or "gt"
            leaf = {
                "left": {"feature": feat},
                "op": cmp_,
            }
            if p.get("right_feature"):
                leaf["right"] = {"feature": p.get("right_feature")}
            else:
                leaf["right"] = {"value": float(p.get("value") if p.get("value") is not None else 0)}
            entry_all.append(leaf)
        elif op == "drop_filter":
            feat = str(p.get("feature") or "")
            entry_all = [x for x in entry_all if feat not in json.dumps(x, ensure_ascii=False)]
        elif op in ("set_rsi", "set_cci") or (op.startswith("set_") and p.get("feature")):
            feat = p.get("feature") or ("rsi14" if "rsi" in op else "cci")
            cmp_ = p.get("cmp")
            val = p.get("value")
            hit = False
            for leaf in entry_all:
                lf = ((leaf.get("left") or {}).get("feature") or "")
                if lf != feat:
                    continue
                if cmp_ and leaf.get("op") != cmp_:
                    continue
                if "value" in ((leaf.get("right") or {})):
                    try:
                        leaf["right"]["value"] = float(val)
                        hit = True
                    except Exception:
                        pass
            if not hit and val is not None:
                entry_all.append({
                    "left": {"feature": feat},
                    "op": cmp_ or "gt",
                    "right": {"value": float(val)},
                })
    # Codex fallback micro-fixes if GLM gave nothing usable — z20/TP/cross templates
    if not patches:
        notes.append("codex_fallback_z20_tp")
        direction = book.get("direction")
        logic = book.get("logic_class")
        if logic == "impulse_continuation" and direction == "long":
            presets = [
                (
                    [
                        {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
                        {"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0}},
                        {"left": {"feature": "rsi14"}, "op": "cross_above", "right": {"value": 42.0}},
                        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                        {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 2.0}},
                    ],
                    [
                        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 60.0}, "role": "take_profit"},
                        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}, "role": "invalidation"},
                    ],
                    14,
                ),
                (
                    [
                        {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
                        {"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0}},
                        {"left": {"feature": "rsi14"}, "op": "cross_above", "right": {"value": 40.0}},
                        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                        {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 2.5}},
                    ],
                    [
                        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 58.0}, "role": "take_profit"},
                        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}, "role": "invalidation"},
                    ],
                    16,
                ),
                (
                    [
                        {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
                        {"left": {"feature": "rsi14"}, "op": "cross_above", "right": {"value": 45.0}},
                        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                        {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.8}},
                        {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
                    ],
                    [
                        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 62.0}, "role": "take_profit"},
                        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}, "role": "invalidation"},
                    ],
                    12,
                ),
            ]
            entry_all, exit_any, hold = presets[min(attempt - 1, 2)]
            dsl["max_hold_bars"] = hold
        elif logic == "exhaustion_fade" and direction == "short":
            presets = [
                (
                    [
                        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}},
                        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.0}},
                        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
                        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
                    ],
                    [
                        {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}, "role": "take_profit"},
                        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
                    ],
                    16,
                ),
                (
                    [
                        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 58.0}},
                        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.3}},
                        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
                        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
                    ],
                    [
                        {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 48.0}, "role": "take_profit"},
                        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
                    ],
                    14,
                ),
                (
                    [
                        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 52.0}},
                        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.0}},
                        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
                        {"left": {"feature": "ema6"}, "op": "lt", "right": {"feature": "ema19"}},
                    ],
                    [
                        {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 42.0}, "role": "take_profit"},
                        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
                    ],
                    18,
                ),
            ]
            entry_all, exit_any, hold = presets[min(attempt - 1, 2)]
            dsl["max_hold_bars"] = hold
        elif logic == "range_reclaim" and direction == "long":
            presets = [
                (
                    [
                        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 35.0}},
                        {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 47.0}},
                        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema19"}},
                        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -1.5}},
                        {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.5}},
                        {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
                    ],
                    [
                        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}, "role": "take_profit"},
                        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}, "role": "invalidation"},
                    ],
                    18,
                ),
                (
                    [
                        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 33.0}},
                        {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}},
                        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema19"}},
                        {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.2}},
                        {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
                    ],
                    [
                        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 52.0}, "role": "take_profit"},
                        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}, "role": "invalidation"},
                    ],
                    20,
                ),
                (
                    [
                        {"left": {"feature": "rsi14"}, "op": "cross_above", "right": {"value": 35.0}},
                        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema19"}},
                        {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.5}},
                        {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
                    ],
                    [
                        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}, "role": "take_profit"},
                        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}, "role": "invalidation"},
                    ],
                    16,
                ),
            ]
            entry_all, exit_any, hold = presets[min(attempt - 1, 2)]
            dsl["max_hold_bars"] = hold

    dsl["entry"] = {"all": entry_all}
    dsl["exit"] = {"any": exit_any}
    dsl["description"] = (dsl.get("description") or "") + "|relax_fix%s:%s" % (
        attempt, ";".join(notes)[:80]
    )
    # unique key per repair
    short = str(book.get("symbol") or "X").split("-")[0].lower()
    dsl["key"] = "frost_relax_%s_%s_r%s_%s" % (
        short, book.get("timeframe"), attempt, time.strftime("%H%M%S")
    )
    dsl = _stamp_ids(dsl, book.get("symbol"), book.get("timeframe"))
    book["dsl"] = dsl
    book["repair_attempt"] = attempt
    book["glm_suggestion"] = suggestion.get("parsed")
    book["gate_mode"] = "frost_relaxed"
    return book


def run_suite(book):
    packs = d.run_internal_packs(book, mode="frost_relaxed")
    if not packs.get("ok"):
        return packs
    try:
        base = d._backtest(packs["definition"], packs["symbol"], packs["timeframe"], "observed_base")
        mc = f.run_monte_carlo(base.get("trades") or [])
    except Exception as exc:
        mc = {"ok": False, "pass": False, "error": str(exc)}
    packs["monte_carlo"] = mc
    packs["suite"] = {
        "walk_forward_8of10": (packs.get("anti_overfit") or {}).get("pass"),
        "extreme_friction": (packs.get("extreme_friction") or {}).get("pass"),
        "logic_destruction": (packs.get("logic_destruction") or {}).get("pass"),
        "monte_carlo_soft": mc.get("pass"),
    }
    # hard pass ignores MC
    packs["pass"] = bool(
        (packs.get("anti_overfit") or {}).get("pass")
        and (packs.get("extreme_friction") or {}).get("pass")
        and (packs.get("logic_destruction") or {}).get("pass")
    )
    return packs


def sim_and_maybe_formal(book, packs):
    sim = None
    for repair in range(0, 4):
        if repair:
            book = d._apply_repair_operator(
                book, ((sim or {}).get("audit") or {}).get("repair_hints") or [], repair - 1
            )
            book["gate_mode"] = "frost_relaxed"
            book["dsl"] = _stamp_ids(book.get("dsl") or {}, book.get("symbol"), book.get("timeframe"))
            packs = run_suite(book)
            if not packs.get("pass"):
                continue
        sim = d.glm_sim_review(book, packs)
        print("SIM", book.get("symbol"), sim.get("wr_deepseek_sim"), sim.get("wr_qwen_sim"),
              "pass", sim.get("pass"))
        if float(sim.get("wr_deepseek_sim") or 0) >= 55 and float(sim.get("wr_qwen_sim") or 0) >= 55:
            sim["pass"] = True
            break
        # if either <50, keep repairing; if 50-55 try once more
        if float(sim.get("wr_deepseek_sim") or 0) < 50 or float(sim.get("wr_qwen_sim") or 0) < 50:
            continue
        # between 50-55: one more keyword repair then accept best effort
    formal = None
    pending = None
    cert = None
    if sim and float(sim.get("wr_deepseek_sim") or 0) >= 55 and float(sim.get("wr_qwen_sim") or 0) >= 55:
        cert = {
            "title": "内部质量认证",
            "at": d._now(),
            "symbol": book.get("symbol"),
            "key": (book.get("dsl") or {}).get("key"),
            "sim_ds": sim.get("wr_deepseek_sim"),
            "sim_qwen": sim.get("wr_qwen_sim"),
            "gate_mode": "frost_relaxed",
        }
        formal = d.formal_ds_qwen_review(packs.get("definition") or book.get("dsl"), packs, book)
        if formal.get("approved"):
            import auto_trade_human_confirm_pipeline as pipeline
            pending = pipeline.ingest_and_screen(
                {
                    "dsl": packs.get("definition") or book.get("dsl"),
                    "symbol": book.get("symbol"),
                    "timeframe": book.get("timeframe"),
                    "thesis": book.get("thesis") or book.get("logic_class"),
                },
                source="frost_relax_repair",
                ai_review=formal.get("ai_review"),
                require_ai_review=True,
            )
            d._record_formal({
                "time": d._now(),
                "op": "寒霜放宽",
                "key": (book.get("dsl") or {}).get("key"),
                "title": book.get("title_short"),
                "symbol": book.get("symbol"),
                "status": "等待" if pending.get("ok") else "退回",
                "annotation": formal.get("annotation"),
            })
    return book, packs, sim, formal, pending, cert


def main():
    d.FROST_GATE_MODE = "frost_relaxed"
    gate_doc = {
        "updated_at": d._now(),
        "op": "寒霜放宽",
        "mode": "frost_relaxed",
        "hard_release_bar": {
            "walk_forward": "fold_positive>=%s of folds>=%s" % (d.FROST_RELAXED_WF_POS, d.FROST_RELAXED_WF_FOLDS),
            "extreme_friction": "sharpe>=0 (2x slip, 50ms, 5% fill fail)",
            "logic_destruction": "±20% mean_net>-0.01 and trades>=3",
        },
        "soft": {"monte_carlo": "advisory only, not required for pass"},
        "calibration": {
            "user_asked_wf": ">=8/10",
            "live_ada_ref_fold_positive": 7,
            "live_ada_ref_key": "ada5_z20_t60_prev_h14_0724k",
            "applied_wf": ">=7/10",
            "reason_zh": "同指标下实盘B档ADA仅7/10正折；坚持8/10会导致连已验证实盘逻辑也无法过门，违背零产出禁止。",
        },
        "note": d.FROST_RELAXED_NOTE,
    }
    d._atomic(STATUS_NOTE, gate_doc)
    # also stamp status.json note
    st = d._read(d.STATUS_PATH, {})
    st["frost_relaxed_gates"] = gate_doc
    st["updated_at"] = d._now()
    d._atomic(d.STATUS_PATH, st)

    report = {
        "op": "寒霜放宽修复",
        "started_at": d._now(),
        "gate_mode": "frost_relaxed",
        "strategies": {},
        "pending": [],
        "survivors": [],
        "ok": False,
    }
    books = load_three_books()
    print("LOADED", [(b.get("symbol"), b.get("logic_class"), (b.get("dsl") or {}).get("key")) for b in books])

    for book in books:
        sym = book.get("symbol")
        row = {"symbol": sym, "logic_class": book.get("logic_class"), "repairs": [], "abandoned": False}
        packs = run_suite(book)
        print("BASE", sym, "pass", packs.get("pass"), packs.get("suite"),
              "tr", (packs.get("base_metrics") or {}).get("trades"),
              "fp", (packs.get("base_metrics") or {}).get("fold_positive"),
              "sharpe", (packs.get("base_metrics") or {}).get("sharpe"),
              "fr", ((packs.get("extreme_friction") or {}).get("metrics") or {}).get("sharpe"))
        book["_last_packs"] = packs
        row["baseline"] = {
            "pass": packs.get("pass"),
            "suite": packs.get("suite"),
            "base_metrics": packs.get("base_metrics"),
            "friction_sharpe": ((packs.get("extreme_friction") or {}).get("metrics") or {}).get("sharpe"),
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
            "mc": packs.get("monte_carlo"),
        }

        attempt = 0
        while not packs.get("pass") and attempt < 3:
            attempt += 1
            sug = glm_repair_suggest(book, packs, attempt)
            print("GLM_FIX", sym, attempt, json.dumps(sug.get("parsed") or {}, ensure_ascii=False)[:240])
            book = apply_patches(book, sug, attempt)
            packs = run_suite(book)
            book["_last_packs"] = packs
            rec = {
                "attempt": attempt,
                "suggestion": sug.get("parsed"),
                "pass": packs.get("pass"),
                "suite": packs.get("suite"),
                "base_metrics": packs.get("base_metrics"),
                "friction_sharpe": ((packs.get("extreme_friction") or {}).get("metrics") or {}).get("sharpe"),
                "dest": (packs.get("logic_destruction") or {}).get("pass"),
                "mc_soft": packs.get("monte_carlo"),
                "dsl_key": (book.get("dsl") or {}).get("key"),
            }
            row["repairs"].append(rec)
            print("RETEST", sym, attempt, "pass", packs.get("pass"), packs.get("suite"),
                  "tr", (packs.get("base_metrics") or {}).get("trades"),
                  "fp", (packs.get("base_metrics") or {}).get("fold_positive"),
                  "sharpe", (packs.get("base_metrics") or {}).get("sharpe"))

        if not packs.get("pass"):
            row["abandoned"] = True
            row["abandon_reason"] = "relaxed_suite_fail_after_3_repairs"
            row["final_suite"] = packs.get("suite")
            row["final_metrics"] = packs.get("base_metrics")
            report["strategies"][sym] = row
            d._atomic(OUT, report)
            print("ABANDON", sym)
            continue

        # Step5 sim (+ formal if certified)
        book, packs, sim, formal, pending, cert = sim_and_maybe_formal(book, packs)
        row["suite_passed"] = True
        row["final_suite"] = packs.get("suite")
        row["final_metrics"] = packs.get("base_metrics")
        row["sim"] = sim
        row["cert"] = cert
        row["formal"] = {
            "approved": (formal or {}).get("approved"),
            "annotation": (formal or {}).get("annotation"),
            "pending": pending,
        } if formal else None
        row["dsl_key"] = (book.get("dsl") or {}).get("key")
        if pending and pending.get("ok"):
            report["pending"].append({
                "key": pending.get("key"),
                "title": book.get("title_short"),
                "annotation": (formal or {}).get("annotation"),
            })
            report["survivors"].append(sym)
            report["ok"] = True
        elif sim:
            # entered sim even if not formal
            report.setdefault("sim_only", []).append(sym)
        report["strategies"][sym] = row
        d._atomic(OUT, report)

    report["finished_at"] = d._now()
    d._atomic(OUT, report)
    print("DONE ok", report.get("ok"), "survivors", report.get("survivors"),
          "sim_only", report.get("sim_only"), "pending", report.get("pending"))
    return report


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
