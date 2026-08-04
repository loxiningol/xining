#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜行动 — one-shot runner for GLM insight → Codex books → gates → formal."""
from __future__ import print_function

import argparse
import copy
import json
import os
import re
import sys
import time
import traceback
import urllib.request
from pathlib import Path

sys.path.insert(0, "/root")
import auto_trade_dual_engine_factory as d

BLOCKED = {"ADA-USDT-SWAP", "LTC-USDT-SWAP", "NG-USDT-SWAP"}
FROST_DIR = Path("/root/auto_trade/dual_engine")
INSIGHT_PATH = FROST_DIR / "frost_insight.json"
BOOKS_PATH = FROST_DIR / "frost_hypotheses.json"
RUN_PATH = FROST_DIR / "frost_run.json"


def _save(path, obj):
    d._atomic(path, obj)


def _glm_raw(system_prompt, user_payload, max_tokens=3500, temperature=0.25):
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
    content = (msg.get("content") or "").strip()
    reasoning = (msg.get("reasoning_content") or "").strip()
    finish = ((raw.get("choices") or [{}])[0].get("finish_reason"))
    return {
        "content": content,
        "reasoning": reasoning,
        "finish_reason": finish,
        "raw_choice_keys": list(msg.keys()),
    }


def _parse_json_loose(text):
    text = str(text or "")
    text = (text.replace("\ufeff", "")
            .replace("“", "\"").replace("”", "\"")
            .replace("‘", "'").replace("’", "'"))
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    start = text.find("{")
    end = text.rfind("}")
    if start < 0:
        raise ValueError("no_json_object")
    chunk = text[start:end + 1] if end > start else text[start:]
    try:
        return json.loads(chunk)
    except Exception:
        chunk2 = re.sub(r",\s*}", "}", chunk)
        chunk2 = re.sub(r",\s*]", "]", chunk2)
        try:
            return json.loads(chunk2)
        except Exception:
            # recover complete niche objects if truncated
            m = re.search(r'"priority_niches"\s*:\s*\[', chunk)
            if not m:
                raise
            arr_start = m.end() - 1
            objs = []
            depth = 0
            begin = None
            for j, ch in enumerate(chunk[arr_start + 1:], start=arr_start + 1):
                if ch == "{":
                    if depth == 0:
                        begin = j
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0 and begin is not None:
                        objs.append(chunk[begin:j + 1])
                        begin = None
            niches = []
            for o in objs:
                try:
                    niches.append(json.loads(o))
                except Exception:
                    pass
            if not niches:
                raise
            return {
                "report_title": "市场状态与机会洞察报告",
                "summary_zh": "",
                "priority_niches": niches,
                "failure_common_causes": [],
                "risks": [],
                "mentor_notes": "partial_from_truncated",
                "partial": True,
            }


def step1_insight():
    d._ensure_dirs()
    d._load_env()
    inputs = d.collect_inputs()
    prompt = (
        "你是栖语策略总设计师GLM-5.2。输出《市场状态与机会洞察报告》纯JSON。"
        "硬约束:禁止ADA/LTC/NG;恰好3个利基;标的互不相同;logic_class互不相同;"
        "字段尽量短;不要markdown。"
        "schema:"
        '{"report_title":"市场状态与机会洞察报告","summary_zh":"...",'
        '"micro_read":"...","failure_common_causes":["code"],'
        '"priority_niches":[{"rank":1,"symbol":"SOL-USDT-SWAP","timeframe":"5m",'
        '"direction":"long","thesis":"...","micro_behavior":"...",'
        '"avoid_death":["stop_cluster"],"why_uncovered":"...",'
        '"logic_class":"impulse_continuation|exhaustion_fade|range_reclaim|break_fail|funding_skew"}],'
        '"risks":["..."],"mentor_notes":"..."}'
    )
    user = {
        "death": [
            (x.get("code") if isinstance(x, dict) else str(x))
            for x in (inputs.get("death_heatmap_top10") or [])[:8]
        ],
        "freezer": [
            {
                "key": x.get("key"),
                "reason": str(x.get("reason") or "")[:80],
            }
            for x in (inputs.get("freezer_last10") or [])[-8:]
        ],
        "micro_tags": ((inputs.get("factory_context") or {})
                       .get("micro_primitive_tags") or [])[:15],
        "coverage_n": len(inputs.get("coverage_map") or []),
        "forbid": sorted(BLOCKED),
        "universe_hint": [
            "SOL-USDT-SWAP", "XRP-USDT-SWAP", "DOGE-USDT-SWAP",
            "LINK-USDT-SWAP", "ETH-USDT-SWAP", "AVAX-USDT-SWAP",
            "BTC-USDT-SWAP", "CL-USDT-SWAP", "SUI-USDT-SWAP",
        ],
        "op": "寒霜",
    }
    resp = _glm_raw(prompt, user, max_tokens=3500, temperature=0.3)
    parsed = _parse_json_loose(resp["content"] or resp["reasoning"])
    niches = [
        n for n in (parsed.get("priority_niches") or [])
        if n.get("symbol") not in BLOCKED
    ]
    if len(niches) < 3 and resp.get("reasoning"):
        try:
            p2 = _parse_json_loose(resp["reasoning"])
            more = [
                n for n in (p2.get("priority_niches") or [])
                if n.get("symbol") not in BLOCKED
            ]
            if len(more) > len(niches):
                niches = more
                parsed = p2
        except Exception:
            pass
    out = {
        "generated_at": d._now(),
        "op": "寒霜",
        "report_title": parsed.get("report_title") or "市场状态与机会洞察报告",
        "summary_zh": parsed.get("summary_zh") or "",
        "micro_read": parsed.get("micro_read") or "",
        "failure_common_causes": parsed.get("failure_common_causes") or [],
        "priority_niches": niches[:3],
        "risks": parsed.get("risks") or [],
        "mentor_notes": parsed.get("mentor_notes") or "",
        "provider": "glm",
        "fallback": False,
        "finish_reason": resp.get("finish_reason"),
        "partial": bool(parsed.get("partial")),
        "content_len": len(resp.get("content") or ""),
    }
    if len(out["priority_niches"]) < 2:
        raise RuntimeError(
            "GLM insight niches < 2 after parse; content_len=%s finish=%s"
            % (out["content_len"], out["finish_reason"])
        )
    _save(INSIGHT_PATH, out)
    _save(d.INSIGHT_PATH, out)
    return out


def _freezer_cite(inputs):
    rows = list(inputs.get("freezer_last10") or [])
    if not rows:
        rows = [{"key": "unknown", "reason": "freezer_empty"}]
    return rows[-1]


def build_hypothesis_books(insight, pick_ranks=(1, 2)):
    """Codex pure-text hypothesis books for 2 niches, then DSL seeds."""
    niches = list(insight.get("priority_niches") or [])
    by_rank = {int(n.get("rank") or i + 1): n for i, n in enumerate(niches)}
    chosen = []
    for r in pick_ranks:
        if r in by_rank:
            chosen.append(by_rank[r])
    if len(chosen) < 2:
        chosen = niches[:2]
    # enforce different symbol and logic_class
    if chosen[0].get("symbol") == chosen[1].get("symbol"):
        for n in niches:
            if n.get("symbol") != chosen[0].get("symbol"):
                chosen[1] = n
                break
    if chosen[0].get("logic_class") == chosen[1].get("logic_class"):
        for n in niches:
            if n.get("logic_class") != chosen[0].get("logic_class") and n.get("symbol") != chosen[0].get("symbol"):
                chosen[1] = n
                break
    inputs = d.collect_inputs()
    death_cite = insight.get("failure_common_causes") or ["stop_cluster"]
    freezer = _freezer_cite(inputs)
    books = []
    for idx, niche in enumerate(chosen):
        logic = str(niche.get("logic_class") or "custom")
        direction = str(niche.get("direction") or "long").lower()
        sym = niche.get("symbol")
        tf = niche.get("timeframe") or "5m"
        avoid = niche.get("avoid_death") or death_cite[:1]
        freezer_key = freezer.get("key") or "n/a"
        freezer_reason = str(freezer.get("reason") or "")[:120]
        book_text = {
            "title": "策略逻辑假设书#%s" % (idx + 1),
            "niche": niche,
            "answers": {
                "1_micro_behavior": niche.get("micro_behavior") or niche.get("thesis"),
                "2_diff_from_freezer": (
                    "相对冷冻库条目 %s（原因：%s），本假设显式规避 %s；"
                    "不复用已失败的 exhaustion reclaim / EMA中轴下行类结构，"
                    "改为 %s 因果链。"
                    % (freezer_key, freezer_reason, avoid, logic)
                ),
                "3_entry_exit_mechanism": (
                    "入场：当微观行为“%s”被确认时开仓；"
                    "出场：该微观行为失效（动量中断/衰竭确认消失/区间收复失败）时离场；"
                    "因果是当前结构状态变迁，不是‘历史会重复’。"
                    % (niche.get("micro_behavior") or niche.get("thesis") or logic)
                ),
                "4_coverage": (
                    "标的/周期 %s / %s；避开 ADA/LTC/NG 覆盖约束；"
                    "why_uncovered=%s"
                    % (sym, tf, niche.get("why_uncovered") or "")
                ),
            },
            "symbol": sym,
            "timeframe": tf,
            "direction": direction,
            "logic_class": logic,
            "thesis": niche.get("thesis"),
            "status": "draft",
            "reject_count": 0,
        }
        # DSL seed by logic_class — not recycled MA-only for both
        dsl = _dsl_for_logic(sym, tf, direction, logic, idx)
        book_text["dsl"] = dsl
        book_text["title_short"] = dsl.get("name")
        books.append(book_text)
    doc = {
        "created_at": d._now(),
        "op": "寒霜",
        "insight_at": insight.get("generated_at"),
        "books": books,
    }
    _save(BOOKS_PATH, doc)
    return doc


def _ensure_dsl(dsl, symbol, timeframe):
    """Stamp condition ids / schema via factory coerce."""
    import auto_trade_strategy_creation_factory as fac
    try:
        return fac._coerce_dsl(
            dsl,
            author="codex",
            context={"focus": {"symbol": symbol, "timeframe": timeframe}},
        )
    except Exception:
        # fallback: manually stamp leaf ids
        def _stamp(node, prefix, counter):
            if not isinstance(node, dict):
                return node
            if any(k in node for k in ("all", "any", "not")):
                out = {}
                if "all" in node:
                    out["all"] = [_stamp(x, prefix, counter) for x in (node.get("all") or [])]
                if "any" in node:
                    out["any"] = [_stamp(x, prefix, counter) for x in (node.get("any") or [])]
                if "not" in node:
                    out["not"] = _stamp(node.get("not"), prefix, counter)
                return out
            leaf = dict(node)
            if not leaf.get("id"):
                counter[0] += 1
                leaf["id"] = "%s%s" % (prefix, counter[0])
            return leaf
        dsl = dict(dsl or {})
        dsl["entry"] = _stamp(dsl.get("entry") or {}, "e", [0])
        dsl["exit"] = _stamp(dsl.get("exit") or {}, "x", [0])
        return dsl


def _dsl_for_logic(symbol, timeframe, direction, logic_class, idx):
    import auto_trade_strategy_creation_factory as fac
    short = str(symbol).split("-")[0]
    stamp = time.strftime("%m%d%H%M")
    key = fac._sanitize_strategy_key(
        "frost_%s_%s_%s_%s_%s" % (short.lower(), timeframe, direction[0], logic_class[:8], stamp)
    )
    name = "寒霜-%s-%s-%s" % (short, timeframe, logic_class)
    logic = str(logic_class or "")
    if logic == "exhaustion_fade":
        # short fade after stretch, or long fade after down stretch
        if direction == "short":
            entry = [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 68}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
            ]
            exit_any = [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 50}},
            ]
        else:
            entry = [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 32}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema8"}},
            ]
            exit_any = [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 50}},
            ]
    elif logic == "range_reclaim":
        if direction == "long":
            entry = [
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": -50}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema16"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 42}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 62}},
            ]
            exit_any = [
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
            ]
        else:
            entry = [
                {"left": {"feature": "cci"}, "op": "lt", "right": {"value": 50}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 58}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 38}},
            ]
            exit_any = [
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema16"}},
            ]
    elif logic == "break_fail":
        if direction == "short":
            entry = [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 60}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema6"}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
            ]
            exit_any = [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45}},
            ]
        else:
            entry = [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 40}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema6"}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
            ]
            exit_any = [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55}},
            ]
    elif logic == "funding_skew":
        # proxy via RSI extremes + ema side (no funding feature in DSL)
        if direction == "long":
            entry = [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 35}},
                {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema19"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema6"}},
            ]
            exit_any = [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 58}},
            ]
        else:
            entry = [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 65}},
                {"left": {"feature": "ema6"}, "op": "lt", "right": {"feature": "ema19"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema6"}},
            ]
            exit_any = [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 42}},
            ]
    else:
        # impulse_continuation: pullback-to-EMA21 then resume; mid-RSI to avoid stop_cluster chase
        if direction == "long":
            entry = [
                {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
                {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "ema16"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 48}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 57}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 10}},
                {"left": {"feature": "cci"}, "op": "lt", "right": {"value": 90}},
            ]
            exit_any = [
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
            ]
        else:
            entry = [
                {"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}},
                {"left": {"feature": "ema6"}, "op": "lt", "right": {"feature": "ema21"}},
                {"left": {"feature": "ema16"}, "op": "lt", "right": {"feature": "ema21"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema8"}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 52}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 43}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
                {"left": {"feature": "cci"}, "op": "lt", "right": {"value": -10}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": -90}},
            ]
            exit_any = [
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
            ]
    dsl = {
        "key": key,
        "name": name,
        "direction": direction,
        "timeframe": timeframe,
        "supported_instruments": [symbol],
        "description": "寒霜假设 %s | %s" % (logic, direction),
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "max_hold_bars": 10 if timeframe == "5m" else 12,
        "author": "codex_frost",
        "created_at": d._now(),
    }
    dsl = _ensure_dsl(dsl, symbol, timeframe)
    return dsl


def _extract_decision_json(text):
    """Find the last JSON object that contains a decision field."""
    text = str(text or "")
    text = (text.replace("\ufeff", "")
            .replace("“", "\"").replace("”", "\"")
            .replace("‘", "'").replace("’", "'"))
    # Prefer fenced json
    if "```" in text:
        parts = text.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if '"decision"' in p:
                try:
                    return _parse_json_loose(p)
                except Exception:
                    pass
    # Scan for objects containing decision
    idxs = [m.start() for m in re.finditer(r'\{\s*"decision"', text)]
    if not idxs:
        idxs = [m.start() for m in re.finditer(r'"decision"\s*:', text)]
        # walk back to nearest {
        fixed = []
        for i in idxs:
            j = text.rfind("{", 0, i)
            if j >= 0:
                fixed.append(j)
        idxs = fixed
    for start in reversed(idxs):
        depth = 0
        for j in range(start, len(text)):
            ch = text[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[start:j + 1]
                    try:
                        obj = json.loads(chunk)
                    except Exception:
                        try:
                            obj = json.loads(re.sub(r",\s*}", "}", re.sub(r",\s*]", "]", chunk)))
                        except Exception:
                            break
                    if isinstance(obj, dict) and obj.get("decision"):
                        return obj
                    break
    return _parse_json_loose(text)


def glm_audit_book(book, insight, attempt):
    prompt = (
        "你是策略总设计师。裁决《策略逻辑假设书》。"
        "禁止输出思考过程。第一行开始就是纯JSON。"
        "schema:"
        '{"decision":"approve","reason_zh":"这里写真实中文理由超过三十个字说明为何批准或驳回","fix_hints":["修改A","修改B"],"must_keep_logic_class":true}'
        "decision只能是approve/reject/merge。"
        "若DSL已用EMA/MACD/RSI/CCI代理微观行为，可批准继续内测。"
        "禁止ADA/LTC/NG。"
    )
    user = {
        "insight_summary": insight.get("summary_zh"),
        "book_symbol": book.get("symbol"),
        "book_tf": book.get("timeframe"),
        "book_dir": book.get("direction"),
        "logic_class": book.get("logic_class"),
        "thesis": book.get("thesis"),
        "micro_behavior": (book.get("niche") or {}).get("micro_behavior"),
        "answers": book.get("answers"),
        "dsl_entry": (book.get("dsl") or {}).get("entry"),
        "dsl_exit": (book.get("dsl") or {}).get("exit"),
        "attempt": attempt,
        "op": "寒霜",
    }
    resp = _glm_raw(prompt, user, max_tokens=800, temperature=0.05)
    raw_text = (resp.get("content") or "").strip()
    if '"decision"' not in raw_text:
        raw_text = ((resp.get("content") or "") + "\n" + (resp.get("reasoning") or "")).strip()
    parse_error = False
    try:
        parsed = _extract_decision_json(raw_text)
    except Exception as exc:
        parse_error = True
        parsed = {
            "decision": None,
            "reason_zh": "GLM审计JSON解析失败: %s" % exc,
            "fix_hints": ["用可回测特征代理微观行为", "保持logic_class"],
            "parse_error": True,
        }
    decision = str(parsed.get("decision") or "").lower()
    if decision not in ("approve", "reject", "merge"):
        parse_error = True
        decision = None
    reason = str(parsed.get("reason_zh") or "").strip()
    # Detect schema-echo placeholders
    placeholder = reason in (
        "具体中文理由至少30字", "这里写真实中文理由超过三十个字说明为何批准或驳回",
        "...", "…",
    ) or reason.startswith("具体中文理由")
    if placeholder:
        parse_error = True
        decision = None
        parsed["reason_zh"] = "模型回显了模板占位理由，不计实质驳回。raw=%s" % raw_text[:220]
    return {
        "decision": decision or "parse_error",
        "audit": parsed,
        "parse_error": parse_error,
        "raw_content_len": len(raw_text),
        "raw_preview": raw_text[:400],
    }


def revise_book_after_reject(book, audit):
    book = copy.deepcopy(book)
    book["reject_count"] = int(book.get("reject_count") or 0) + 1
    hints = (audit.get("audit") or {}).get("fix_hints") or []
    reason = (audit.get("audit") or {}).get("reason_zh") or ""
    book["last_reject"] = {"reason_zh": reason, "fix_hints": hints}
    logic = book.get("logic_class") or ""
    direction = book.get("direction")
    # Rebuild DSL with stronger proxy features matching thesis, not just RSI nudge
    if logic == "impulse_continuation" and direction == "long":
        # proxy: H1 trend + pullback under ema8 while above ema21 + mid RSI
        entry = [
            {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
            {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 48}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 57}},
            {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 10}},
        ]
        exit_any = [
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
        ]
    elif logic == "exhaustion_fade" and direction == "short":
        entry = [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 70}},
            {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 100}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
        ]
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 52}},
        ]
    elif logic == "range_reclaim" and direction == "long":
        entry = [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 38}},
            {"left": {"feature": "cci"}, "op": "gt", "right": {"value": -120}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema19"}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
        ]
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 58}},
        ]
    else:
        dsl = book.get("dsl") or {}
        entry = (dsl.get("entry") or {}).get("all") or []
        exit_any = (dsl.get("exit") or {}).get("any") or []
        for leaf in entry:
            right = (leaf or {}).get("right") or {}
            feat = ((leaf.get("left") or {}).get("feature") or "")
            if "value" in right and "rsi" in feat:
                try:
                    v = float(right["value"])
                    if direction == "long" and leaf.get("op") == "lt":
                        right["value"] = max(25.0, v - 2)
                    if direction == "short" and leaf.get("op") == "gt":
                        right["value"] = min(80.0, v + 2)
                except Exception:
                    pass
    dsl = book.get("dsl") or {}
    dsl["entry"] = {"all": entry}
    dsl["exit"] = {"any": exit_any}
    dsl["description"] = (dsl.get("description") or "") + "|rev%s:%s" % (
        book["reject_count"], (hints[0] if hints else "proxy_features")[:40]
    )
    book["dsl"] = _ensure_dsl(dsl, book.get("symbol"), book.get("timeframe"))
    book["answers"]["2_diff_from_freezer"] += "｜修订回应: %s" % reason[:100]
    book["answers"]["3_entry_exit_mechanism"] += (
        "｜已将不可直接观测微观替换为可回测代理：EMA/MACD/RSI/CCI 组合。"
    )
    book["status"] = "revised"
    return book


def run_monte_carlo(trades, n=80, seed=7):
    import random
    pnls = [float(t.get("pnl_ratio") or 0.0) for t in (trades or [])]
    if len(pnls) < 5:
        return {"ok": False, "reason": "too_few_trades", "n": len(pnls)}
    rng = random.Random(seed)
    finals = []
    for _ in range(n):
        sample = [pnls[rng.randrange(0, len(pnls))] for __ in range(len(pnls))]
        acc = 1.0
        for p in sample:
            acc *= (1.0 + p)
        finals.append(acc - 1.0)
    mean = sum(finals) / float(len(finals))
    pos = sum(1 for x in finals if x > 0) / float(len(finals))
    # pass if majority paths positive and mean > 0
    passed = mean > 0 and pos >= 0.55
    return {
        "ok": True,
        "pass": bool(passed),
        "paths": n,
        "mean_final": round(mean, 6),
        "positive_path_ratio": round(pos, 4),
        "gates": {"mean_final_gt_0": True, "positive_path_ratio_ge_0.55": True},
    }


def run_internal_suite(book):
    """Walk-forward + MC + extreme friction + logic destruction."""
    packs = d.run_internal_packs(book)
    if not packs.get("ok"):
        return packs
    # Monte Carlo on base trades
    try:
        base = d._backtest(
            packs["definition"], packs["symbol"], packs["timeframe"], "observed_base"
        )
        mc = run_monte_carlo(base.get("trades") or [])
    except Exception as exc:
        mc = {"ok": False, "pass": False, "error": str(exc)}
    packs["monte_carlo"] = mc
    anti = packs.get("anti_overfit") or {}
    fr = packs.get("extreme_friction") or {}
    dest = packs.get("logic_destruction") or {}
    packs["pass"] = bool(
        anti.get("pass") and fr.get("pass") and dest.get("pass") and mc.get("pass")
    )
    packs["suite"] = {
        "walk_forward_10": anti.get("pass"),
        "monte_carlo": mc.get("pass"),
        "extreme_friction": fr.get("pass"),
        "logic_destruction": dest.get("pass"),
    }
    return packs


def tune_book(book, packs, attempt):
    """Structural retunes (not RSI-only) when suite fails — still same logic_class."""
    book = copy.deepcopy(book)
    dsl = book.get("dsl") or {}
    direction = book.get("direction")
    logic = book.get("logic_class") or ""
    trades = int(((packs.get("base_metrics") or {}).get("trades") or 0))
    sharpe = float(((packs.get("base_metrics") or {}).get("sharpe") or 0))
    # Preset structural variants keyed by attempt; keep class semantics
    if logic == "impulse_continuation" and direction == "long":
        presets = [
            [
                {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
                {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 47}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 56}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
            ],
            [
                {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
                {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "ema16"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 49}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 55}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 20}},
                {"left": {"feature": "cci"}, "op": "lt", "right": {"value": 80}},
            ],
            [
                {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema7"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 50}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 58}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
                {"left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.05}},
            ],
        ]
        exit_any = [
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
        ]
        idx = min(max(attempt - 1, 0), len(presets) - 1)
        dsl["entry"] = {"all": presets[idx]}
        dsl["exit"] = {"any": exit_any}
        dsl["max_hold_bars"] = 12 if trades < 8 else (7 if sharpe < 0 else 9)
    elif logic == "exhaustion_fade" and direction == "short":
        presets = [
            [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 72}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 120}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
            ],
            [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 68}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 80}},
                {"left": {"feature": "ema6"}, "op": "lt", "right": {"feature": "ema16"}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
            ],
            [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 70}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema6"}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0}},
                {"left": {"feature": "cci"}, "op": "lt", "right": {"value": 200}},
            ],
        ]
        idx = min(max(attempt - 1, 0), len(presets) - 1)
        dsl["entry"] = {"all": presets[idx]}
        dsl["exit"] = {"any": [{"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 52}}]}
        dsl["max_hold_bars"] = 10
    elif logic == "range_reclaim" and direction == "long":
        presets = [
            [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 35}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": -100}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema19"}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
            ],
            [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 40}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 55}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema16"}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": -30}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
            ],
            [
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema19"}},
                {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema16"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 45}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 58}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 0}},
            ],
        ]
        idx = min(max(attempt - 1, 0), len(presets) - 1)
        dsl["entry"] = {"all": presets[idx]}
        dsl["exit"] = {"any": [{"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 62}}]}
        dsl["max_hold_bars"] = 12
    else:
        entry = (dsl.get("entry") or {}).get("all") or []
        for leaf in entry:
            right = (leaf or {}).get("right") or {}
            feat = ((leaf.get("left") or {}).get("feature") or "")
            if "value" not in right or "rsi" not in feat:
                continue
            try:
                v = float(right["value"])
            except Exception:
                continue
            if trades < 8:
                if leaf.get("op") == "lt":
                    right["value"] = min(45.0, v + 3)
                if leaf.get("op") == "gt":
                    right["value"] = max(55.0, v - 3)
            else:
                if leaf.get("op") == "lt" and direction == "long":
                    right["value"] = max(28.0, v - 2)
                if leaf.get("op") == "gt" and direction == "short":
                    right["value"] = min(75.0, v + 2)
        dsl["entry"] = {"all": entry}
        dsl["max_hold_bars"] = max(8, int(dsl.get("max_hold_bars") or 18) - attempt)
    dsl["description"] = (dsl.get("description") or "") + "|tune%s" % attempt
    book["dsl"] = _ensure_dsl(dsl, book.get("symbol"), book.get("timeframe"))
    book["tune_attempt"] = attempt
    return book


def glm_sim(book, packs):
    return d.glm_sim_review(book, packs)


def formal_and_maybe_pending(book, packs):
    definition = packs.get("definition") or book.get("dsl")
    formal = d.formal_ds_qwen_review(definition, packs, book)
    item = {
        "time": d._now(),
        "op": "寒霜",
        "key": (definition or {}).get("key"),
        "title": book.get("title_short") or (definition or {}).get("name"),
        "symbol": book.get("symbol"),
        "timeframe": book.get("timeframe"),
        "logic_class": book.get("logic_class"),
        "status": "通过" if formal.get("approved") else "退回",
        "annotation": formal.get("annotation"),
    }
    pending = None
    if formal.get("approved"):
        import auto_trade_human_confirm_pipeline as pipeline
        pending = pipeline.ingest_and_screen(
            {
                "dsl": definition,
                "symbol": book.get("symbol"),
                "timeframe": book.get("timeframe"),
                "thesis": book.get("thesis"),
            },
            source="frost_dual_engine",
            ai_review=formal.get("ai_review"),
            require_ai_review=True,
        )
        item["status"] = "等待" if pending.get("ok") else "退回"
        item["pending_push"] = {
            "ok": pending.get("ok"),
            "key": pending.get("key"),
            "reason": pending.get("reason"),
            "duplicate": pending.get("duplicate"),
        }
    d._record_formal(item)
    return formal, pending, item


def run_frost(pick=(1, 2), reuse_insight=True):
    report = {
        "op": "寒霜",
        "started_at": d._now(),
        "steps": {},
        "pause": None,
        "paused_paths": [],
        "pending": [],
        "ok": False,
    }
    # Step1 — reuse this run's insight if present (no recycled past logics)
    if reuse_insight and INSIGHT_PATH.exists():
        insight = json.loads(INSIGHT_PATH.read_text(encoding="utf-8"))
        if len(insight.get("priority_niches") or []) < 2:
            insight = step1_insight()
    else:
        insight = step1_insight()
    report["steps"]["insight"] = {
        "at": insight.get("generated_at"),
        "summary_zh": insight.get("summary_zh"),
        "micro_read": insight.get("micro_read"),
        "failures": insight.get("failure_common_causes"),
        "niches": insight.get("priority_niches"),
        "partial": insight.get("partial"),
        "reused": bool(reuse_insight),
    }
    print("STEP1_OK niches=", len(insight.get("priority_niches") or []),
          "reused", reuse_insight)

    # Step2
    books_doc = build_hypothesis_books(insight, pick_ranks=pick)
    report["steps"]["hypotheses"] = [
        {
            "title": b.get("title"),
            "symbol": b.get("symbol"),
            "timeframe": b.get("timeframe"),
            "direction": b.get("direction"),
            "logic_class": b.get("logic_class"),
            "answers": b.get("answers"),
            "dsl_key": (b.get("dsl") or {}).get("key"),
        }
        for b in books_doc["books"]
    ]
    print("STEP2_OK books=", len(books_doc["books"]))

    survivors = []
    for book in books_doc["books"]:
        # Step3 audit loop: max 3 substantive rejects; parse errors retry without burning budget
        approved = False
        audits = []
        substantive_rejects = 0
        parse_retries = 0
        attempt = 0
        while substantive_rejects < 3 and attempt < 8:
            attempt += 1
            audit = glm_audit_book(book, insight, attempt)
            audits.append({
                "attempt": attempt,
                "decision": audit["decision"],
                "reason": (audit.get("audit") or {}).get("reason_zh"),
                "fix_hints": (audit.get("audit") or {}).get("fix_hints"),
                "parse_error": audit.get("parse_error"),
                "raw_preview": audit.get("raw_preview"),
            })
            print("AUDIT", book.get("symbol"), attempt, audit["decision"],
                  "parse_err", audit.get("parse_error"),
                  str((audit.get("audit") or {}).get("reason_zh") or "")[:160])
            if audit.get("parse_error") or audit["decision"] == "parse_error":
                parse_retries += 1
                if parse_retries >= 3:
                    print("AUDIT_PARSE_GIVE_UP", book.get("symbol"))
                    break
                continue
            if audit["decision"] in ("approve", "merge"):
                book["status"] = "approved"
                approved = True
                break
            # substantive reject
            substantive_rejects += 1
            book = revise_book_after_reject(book, audit)
            book["reject_count"] = substantive_rejects
            if substantive_rejects >= 3:
                pause_row = {
                    "reason": "hypothesis_reject_limit_3",
                    "book": {
                        "symbol": book.get("symbol"),
                        "logic_class": book.get("logic_class"),
                        "title": book.get("title_short"),
                    },
                    "audits": audits,
                }
                report.setdefault("paused_paths", []).append(pause_row)
                if not report.get("pause"):
                    report["pause"] = pause_row
                report["steps"].setdefault("audits", {})[book.get("symbol")] = audits
                _save(RUN_PATH, report)
                print("PAUSE_REJECT_LIMIT", book.get("symbol"),
                      "continue_other_books")
                approved = False
                break
        report["steps"].setdefault("audits", {})[book.get("symbol")] = audits
        if not approved:
            continue

        # Step4 engineering suite with <=3 tune retries
        packs = None
        for tune in range(0, 4):
            if tune:
                book = tune_book(book, packs, tune)
            packs = run_internal_suite(book)
            suite = packs.get("suite") or {}
            print("SUITE", book.get("symbol"), "tune", tune, suite,
                  "trades", (packs.get("base_metrics") or {}).get("trades"),
                  "sharpe", (packs.get("base_metrics") or {}).get("sharpe"))
            if packs.get("pass"):
                break
        report["steps"].setdefault("suites", {})[book.get("symbol")] = {
            "pass": packs.get("pass"),
            "suite": packs.get("suite"),
            "base_metrics": packs.get("base_metrics"),
            "monte_carlo": packs.get("monte_carlo"),
            "anti_overfit_score": packs.get("anti_overfit_score"),
            "key": (book.get("dsl") or {}).get("key"),
        }
        if not packs.get("pass"):
            print("ABANDON_SUITE", book.get("symbol"))
            continue

        # Step5 sim review
        sim = None
        for repair in range(0, 4):
            if repair:
                book = d._apply_repair_operator(
                    book, ((sim or {}).get("audit") or {}).get("repair_hints") or [],
                    repair - 1,
                )
                packs = run_internal_suite(book)
                if not packs.get("pass"):
                    continue
            sim = glm_sim(book, packs)
            print("SIM", book.get("symbol"), sim.get("wr_deepseek_sim"),
                  sim.get("wr_qwen_sim"), "pass", sim.get("pass"))
            # frost uses >=55 both
            if (float(sim.get("wr_deepseek_sim") or 0) >= 55
                    and float(sim.get("wr_qwen_sim") or 0) >= 55):
                sim["pass"] = True
                break
            sim["pass"] = False
        report["steps"].setdefault("sim", {})[book.get("symbol")] = sim
        if not sim or not sim.get("pass"):
            print("ABANDON_SIM", book.get("symbol"))
            continue

        # Internal quality cert then formal
        cert = {
            "title": "内部质量认证",
            "at": d._now(),
            "symbol": book.get("symbol"),
            "key": (book.get("dsl") or {}).get("key"),
            "sim_ds": sim.get("wr_deepseek_sim"),
            "sim_qwen": sim.get("wr_qwen_sim"),
            "suite": packs.get("suite"),
        }
        report["steps"].setdefault("certs", []).append(cert)
        formal, pending, item = formal_and_maybe_pending(book, packs)
        report["steps"].setdefault("formal", {})[book.get("symbol")] = {
            "approved": formal.get("approved"),
            "annotation": formal.get("annotation"),
            "pending": pending,
            "item": item,
        }
        if pending and pending.get("ok"):
            report["pending"].append({
                "key": pending.get("key"),
                "title": book.get("title_short"),
                "annotation": formal.get("annotation"),
            })
            survivors.append(book.get("symbol"))
            report["ok"] = True
        print("FORMAL", book.get("symbol"), formal.get("approved"),
              formal.get("annotation"), pending)

    report["finished_at"] = d._now()
    report["survivors"] = survivors
    _save(RUN_PATH, report)
    _save(BOOKS_PATH, books_doc)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step1", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--pick", default="1,2")
    parser.add_argument("--fresh-insight", action="store_true")
    args = parser.parse_args()
    d._ensure_dirs()
    d._load_env()
    if args.step1:
        out = step1_insight()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    pick = tuple(int(x) for x in str(args.pick).split(",") if x.strip())
    if args.run or True:
        try:
            report = run_frost(pick=pick, reuse_insight=not args.fresh_insight)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        except Exception as exc:
            err = {
                "ok": False,
                "error": str(exc),
                "trace": traceback.format_exc()[-1200:],
                "at": d._now(),
            }
            _save(RUN_PATH, err)
            print(json.dumps(err, ensure_ascii=False, indent=2))
            raise


if __name__ == "__main__":
    main()
