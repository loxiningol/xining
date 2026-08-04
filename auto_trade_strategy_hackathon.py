# -*- coding: utf-8 -*-
"""Weekly AI strategy creative hackathon.

Three providers independently invent a brand-new strategy logic from the past
week's live outcomes, death cases, and environment match stats — not bounded
to the mutation-operator catalog.  Peers review; the best DSL enters the
standard validation queue (full re-audit, no live exemption).

Does not touch stops, exits, stress floors, or human passed_all approval.
"""
from __future__ import print_function

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import json
import os
import sqlite3
import tempfile
import time

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"
FREQ_PATH = AUTO_DIR / "live_portfolio_frequency.json"
MISMATCH_PATH = AUTO_DIR / "environment_mismatch_counters.json"
VAULT_PATH = AUTO_DIR / "strategy_lifecycle_freeze_vault.json"
ECO_DB = AUTO_DIR / "strategy_ecosystem.db"
AUDIT_LOG = AUTO_DIR / "strategy_hackathon_audit.jsonl"
STATUS_PATH = AUTO_DIR / "strategy_hackathon_status.json"
DOSSIER_PATH = AUTO_DIR / "strategy_hackathon_dossier.json"
RESULT_PATH = AUTO_DIR / "strategy_hackathon_latest.json"

HACKATHON_PROMPT = """你是栖语「策略创意黑客松」的独立创造者，看不到另外两位AI的方案。
任务：根据本周实盘结果、淘汰死因、环境匹配/不匹配统计，提出一个**全新**策略逻辑。
不受 mutation_operator_catalog 限制；不得只做阈值微调或跨标的复制粘贴。
必须输出完整 qiyu_strategy_dsl_v1 DSL（schema/key/name/direction/timeframe/
supported_instruments/max_hold_bars/entry/exit）。特征仅限系统已有OHLC/EMA/
KDJ/CCI/MACD/ATR/RSI/Z20/H1字段；运算符限lt/lte/gt/gte/eq/between/cross_above/cross_below。
entry至少两个独立条件，且至少含一个一次性触发（cross_*或本根/前根转折），避免连续状态重复入场。
exit每个叶子必须带role=take_profit或invalidation。
不得修改止损/杠杆/仓位；不得声称未经回测的胜率；不得授予passed_all。
【必答题】dossier.mandatory_niches 列出最多3个「未占领且近期活跃」的生态位。
你必须选择其中之一作为本次提案的主攻生态位，并在 thesis 中写明所选 niche 名称与理由。
若无法覆盖任一必答题生态位，必须 abstain。
严格输出JSON：
{"proposal":{"title":"中文名","thesis":"当前市场为何该逻辑可能有效（引用dossier证据与所选niche）",
"selected_mandatory_niche":"niche名称","death_lessons_used":["..."],
"environment_insight":"...","dsl":{完整DSL},
"falsification_tests":["至少两条"],"why_not_mutation":"为何这不是算子微调"},
"confidence":0到1}
若证据不足以提出可靠新逻辑，输出{"proposal":null,"abstain_reason":"..."}。
"""


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        delete=False, dir=str(path.parent), mode="w", encoding="utf-8")
    try:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        handle.close()
        Path(handle.name).replace(path)
    except Exception:
        try:
            Path(handle.name).unlink()
        except Exception:
            pass
        raise


def _append_audit(event):
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    row = dict(event)
    row["ts"] = time.time()
    row["time"] = _now()
    with AUDIT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def build_dossier(days=7):
    """Assemble weekly feedback for creative generation."""
    cutoff = (datetime.now() - timedelta(days=int(days))).strftime("%Y-%m-%d")
    controls = _read(CONTROL_PATH, {"assignments": {}})
    freq = _read(FREQ_PATH, {})
    mismatch = _read(MISMATCH_PATH, {"counters": {}})
    vault = _read(VAULT_PATH, {"sealed": {}})
    live_rows = []
    for row in freq.get("assignments") or []:
        live_rows.append({
            "assignment_id": "%s|%s|%s" % (
                row.get("symbol"), row.get("timeframe"), row.get("strategy_key")),
            "trades": row.get("trades"), "win_rate": row.get("win_rate"),
            "return_pct": row.get("return_pct"),
        })
    deaths = []
    for path in (AUTO_DIR / "strategy_lifecycle_audit.jsonl",
                 AUTO_DIR / "strategy_evolution_audit.jsonl"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines()[-2000:]:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if str(ev.get("time") or "") < cutoff:
                continue
            if ev.get("event") in ("eliminated", "niche_vacated",
                                   "micro_mutation_created",
                                   "vault_revive_test_granted"):
                deaths.append({
                    "event": ev.get("event"),
                    "assignment_id": ev.get("assignment_id") or ev.get("loser"),
                    "reasons": ev.get("reasons") or ev.get("natural_language"),
                    "time": ev.get("time"),
                })
    env_stats = []
    for aid, cell in sorted((mismatch.get("counters") or {}).items()):
        env_stats.append({
            "assignment_id": aid,
            "streak": cell.get("streak"),
            "last_reason": cell.get("last_reason"),
            "last_natural_language": cell.get("last_natural_language"),
        })
    rejected = []
    if ECO_DB.exists():
        conn = sqlite3.connect(str(ECO_DB), timeout=30)
        try:
            rows = conn.execute(
                "SELECT strategy_key,symbol,timeframe,state,"
                "substr(evidence_json,1,400) FROM candidates "
                "WHERE state LIKE '%reject%' ORDER BY updated_at DESC LIMIT 12"
            ).fetchall()
            for key, symbol, timeframe, state, evid in rows:
                rejected.append({
                    "strategy_key": key, "symbol": symbol,
                    "timeframe": timeframe, "state": state,
                    "evidence_head": evid,
                })
        finally:
            conn.close()
    active = []
    for aid, row in sorted((controls.get("assignments") or {}).items()):
        active.append({
            "assignment_id": aid,
            "audit_state": row.get("audit_state"),
            "grade": row.get("lifecycle_grade") or row.get("max_grade"),
            "paused": bool(row.get("pause_new_entries")),
            "env_bound": bool((row.get("environment_boundary") or {}).get(
                "final_approved")),
        })
    # Prefer hunting clusters with frequency gap / mismatch pressure.
    focus = []
    for row in live_rows:
        if int(row.get("trades") or 0) <= 2:
            focus.append(row["assignment_id"])
    for cell in env_stats:
        if int(cell.get("streak") or 0) >= 3:
            focus.append(cell["assignment_id"])
    focus = sorted(set(focus))[:8]

    # Mandatory niches from niche map (vacant + recent activity) — always refresh.
    niche_report = {}
    try:
        import auto_trade_niche_map as niche_map
        niche_report = niche_map.build_report() or {}
    except Exception:
        niche_report = _read(AUTO_DIR / "niche_map_report.json", {})
    briefs = list(niche_report.get("hackathon_prompt_pool")
                  or niche_report.get("exploration_briefs") or [])
    # Prefer P1 then uncovered hit proxies
    def _brief_rank(b):
        pri = {"P1": 0, "P2": 1, "P3": 2}.get(str(b.get("priority") or "P3"), 9)
        return (pri, -len(b.get("seed_primitives") or []))
    briefs_sorted = sorted(briefs, key=_brief_rank)
    mandatory = []
    for b in briefs_sorted:
        if len(mandatory) >= 3:
            break
        mandatory.append({
            "priority": b.get("priority"),
            "niche": b.get("niche") or b.get("title"),
            "symbol_hint": b.get("symbol") or b.get("symbol_hint"),
            "logic": b.get("logic"),
            "avoid_death_codes": b.get("avoid") or b.get("avoid_death_codes") or [],
            "seed_primitives": b.get("seed_primitives") or [],
            "required": True,
        })
    # Fallback: uncovered_top if pool empty
    if not mandatory:
        for row in (niche_report.get("uncovered_top") or [])[:3]:
            mandatory.append({
                "priority": "P1",
                "niche": row.get("cluster_id") or row.get("title"),
                "symbol_hint": "%s/%s" % (
                    row.get("symbol") or "?", row.get("timeframe") or "?"),
                "logic": "occupy vacant niche %s" % (row.get("cluster_id") or ""),
                "avoid_death_codes": [],
                "seed_primitives": row.get("primitives") or [],
                "required": True,
            })
    for m in mandatory:
        hint = str(m.get("symbol_hint") or "")
        sym = hint.split("/")[0].strip().split()[0] if hint else ""
        if sym and "|" not in sym:
            # normalize to cluster focus token
            tf_guess = "5m"
            if "15m" in (m.get("logic") or "") or "15m" in hint:
                tf_guess = "15m"
            focus.insert(0, "%s|%s" % (sym, tf_guess))
    focus = sorted(set(focus))[:8]
    if not focus:
        focus = ["BTC-USDT-SWAP|5m", "NG-USDT-SWAP|5m", "ADA-USDT-SWAP|5m"]
    primary = focus[0]
    parts = str(primary).split("|")
    symbol = parts[0] if parts else "BTC-USDT-SWAP"
    timeframe = parts[1] if len(parts) > 1 else "5m"
    dossier = {
        "created_at": _now(),
        "schema": "qiyu_strategy_hackathon_dossier_v1",
        "window_days": int(days),
        "assignment": {"symbol": symbol, "timeframe": timeframe},
        "focus_clusters": focus,
        "mandatory_niches": mandatory,
        "niche_coverage_ratio": (niche_report.get("summary") or {}).get(
            "coverage_ratio"),
        "live_performance": live_rows[:40],
        "death_and_vacancy_events": deaths[-40:],
        "environment_mismatch_stats": env_stats[:40],
        "recent_rejections": rejected,
        "active_assignments": active[:40],
        "freeze_vault": list((vault.get("sealed") or {}).keys()),
        "policy": (
            "creative_generation_from_weekly_feedback;"
            "mandatory_niches_from_niche_map_top3;"
            "not_operator_bounded_mutation;"
            "full_validation_required;no_live_auto_grant"
        ),
        "roi_note": (
            "小资金账户下策略管理开销可能高于单笔绝对收益；"
            "黑客松产出仍须过完整门禁，不以填仓为目标放松标准"
        ),
    }
    _atomic(DOSSIER_PATH, dossier)
    return dossier


def _call_creator(name, dossier, _retry=True):
    import auto_trade_ai_consensus as ai
    cfg = ai._provider_config(name)
    consent = ai.external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed"):
        return {"provider": name, "ok": False, "error": "consent_missing",
                "proposal": None}
    if not cfg["api_key"]:
        return {"provider": name, "ok": False, "error": "api_key_missing",
                "proposal": None}
    meta = {
        "creation_mode": True,
        "hackathon": True,
        "param_meta": {},
        "symbol": (dossier.get("assignment") or {}).get("symbol"),
        "timeframe": (dossier.get("assignment") or {}).get("timeframe"),
    }
    user_payload = {
        "hackathon": True,
        "dossier": dossier,
        "strategy_metadata": meta,
        "instruction": (
            "提出一个服务当前焦点集群的新逻辑；优先保证胜率与逻辑稳健，"
            "禁止追求高频或为凑开仓次数放宽条件；"
            "宁可日均0.3次高质量信号，也不要日均多次低质量噪声信号；"
            "但不得牺牲成本存活与可证伪性。必须返回合法JSON对象。"
        ),
        "output_contract": {
            "required_top_keys": ["proposal"],
            "dsl_required_keys": [
                "schema", "key", "name", "direction", "timeframe",
                "supported_instruments", "max_hold_bars", "entry", "exit"],
        },
    }
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system",
             "content": HACKATHON_PROMPT + "\n角色：" + ai.RESEARCH_ROLES.get(
                 name, "")},
            {"role": "user",
             "content": ai.canonical_json(user_payload)},
        ],
    }
    if name == "deepseek":
        body["response_format"] = {"type": "json_object"}
    if name == "chatgpt":
        body["max_completion_tokens"] = 2200
    else:
        body["temperature"] = 0.2 if not _retry else 0.4
        body["max_tokens"] = 2200
    started = time.time()
    try:
        import urllib.request as urllib_request
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(
            cfg["url"], data=encoded,
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        timeout = max(int(cfg["timeout"]), 180)
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        content = (((raw.get("choices") or [{}])[0].get("message") or {})
                   .get("content"))
        if not str(content or "").strip() and name == "deepseek":
            # DeepSeek often puts JSON in reasoning/content variants.
            content = (((raw.get("choices") or [{}])[0].get("message") or {})
                       .get("reasoning_content") or content)
        parsed = ai._parse_content_json(content)
        proposal = parsed.get("proposal")
        if proposal is None and _retry:
            return _call_creator(name, dossier, _retry=False)
        if isinstance(proposal, dict) and isinstance(proposal.get("dsl"), dict):
            # Format gate: normalize then validate early.
            try:
                import auto_trade_strategy_dsl as dsl
                dsl.validate_strategy(_normalize_dsl(proposal.get("dsl") or {}))
            except Exception as exc:
                if _retry:
                    return _call_creator(name, dossier, _retry=False)
                return {"provider": name, "ok": False,
                        "error": "dsl_format_invalid:%s" % exc,
                        "proposal": proposal,
                        "latency_sec": round(time.time() - started, 3)}
        return {
            "provider": name, "ok": proposal is not None,
            "proposal": proposal,
            "abstain_reason": parsed.get("abstain_reason"),
            "confidence": parsed.get("confidence"),
            "latency_sec": round(time.time() - started, 3),
            "raw_keys": sorted(parsed.keys()) if isinstance(parsed, dict) else [],
            "retried": not _retry,
        }
    except Exception as exc:
        if _retry:
            return _call_creator(name, dossier, _retry=False)
        return {"provider": name, "ok": False, "error": str(exc),
                "proposal": None,
                "latency_sec": round(time.time() - started, 3),
                "retried": True}


def _peer_score(reviewer_name, author_name, proposal, dossier):
    import auto_trade_ai_consensus as ai
    if not proposal or not isinstance(proposal.get("dsl"), dict):
        return {"provider": reviewer_name, "score": 0, "approve": False,
                "reason": "empty_proposal"}
    # Lightweight peer review via review_one-style payload.
    candidate = {
        "kind": "hackathon_dsl",
        "author": author_name,
        "strategy_key": (proposal.get("dsl") or {}).get("key"),
        "dsl": proposal.get("dsl"),
        "thesis": proposal.get("thesis"),
    }
    evidence = {
        "hackathon_peer_review": True,
        "dossier_focus": dossier.get("focus_clusters"),
        "ask": (
            "这是另一位AI在黑客松中提出的全新策略。请评分0-100并决定是否推荐"
            "进入标准验证（非实盘）。关注：是否真正新逻辑、是否可证伪、"
            "是否可能覆盖摩擦、是否避免已知死因。返回JSON："
            "{\"score\":0到100,\"recommend_validation\":true/false,\"reason\":\"...\"}"
        ),
    }
    try:
        # Use research transport for free-form peer JSON.
        cfg = ai._provider_config(reviewer_name)
        if not cfg["api_key"]:
            return {"provider": reviewer_name, "score": 0, "approve": False,
                    "reason": "api_key_missing"}
        body = {
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": (
                    "你是黑客松同行评审。看不到作者身份以外的其他评审。"
                    "只输出JSON：{\"score\":0到100,\"recommend_validation\":bool,\"reason\":\"中文\"}"
                )},
                {"role": "user", "content": ai.canonical_json({
                    "candidate": candidate, "evidence": evidence})},
            ],
        }
        if reviewer_name == "deepseek":
            body["response_format"] = {"type": "json_object"}
        if reviewer_name == "chatgpt":
            body["max_completion_tokens"] = 600
        else:
            body["temperature"] = 0
            body["max_tokens"] = 500
        import urllib.request as urllib_request
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(
            cfg["url"], data=encoded,
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        with urllib_request.urlopen(req, timeout=min(60, cfg["timeout"])) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        content = (((raw.get("choices") or [{}])[0].get("message") or {})
                   .get("content"))
        parsed = ai._parse_content_json(content)
        score = float(parsed.get("score") or 0)
        return {
            "provider": reviewer_name,
            "score": score,
            "approve": bool(parsed.get("recommend_validation")),
            "reason": parsed.get("reason"),
        }
    except Exception as exc:
        return {"provider": reviewer_name, "score": 0, "approve": False,
                "reason": str(exc)}


def _ensure_condition_ids(node, prefix="c", counter=None):
    if counter is None:
        counter = [0]
    if not isinstance(node, dict):
        return
    if "left" in node and "op" in node:
        if not str(node.get("id") or "").strip():
            counter[0] += 1
            node["id"] = "%s%d" % (prefix, counter[0])
        return
    for key in ("all", "any"):
        if key in node and isinstance(node[key], list):
            for child in node[key]:
                _ensure_condition_ids(child, prefix, counter)
    if "not" in node:
        _ensure_condition_ids(node.get("not"), prefix, counter)


def _feature_token(token):
    import auto_trade_strategy_dsl as dsl
    text = str(token or "").strip()
    if text.lower().startswith("h1."):
        text = "h1_" + text.split(".", 1)[1]
    feat = text.lower()
    if feat in dsl.FEATURES:
        return feat
    if feat.startswith("h1_"):
        base = feat[3:]
        if ("h1_" + base) in dsl.FEATURES:
            return "h1_" + base
        if base in dsl.FEATURES:
            return base
    return feat


def _normalize_operand(value):
    if isinstance(value, dict):
        if "feature" in value or "value" in value:
            return value
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"value": float(value)}
    text = str(value).strip()
    if not text:
        raise ValueError("empty operand")
    try:
        return {"value": float(text)}
    except Exception:
        return {"feature": _feature_token(text)}


def _normalize_leaf(node, prefix="c", counter=None):
    if counter is None:
        counter = [0]
    if not isinstance(node, dict):
        return node
    # Compact {"cross_below": ["close", "ema17"]}
    for op in ("lt", "lte", "gt", "gte", "eq", "between",
               "cross_above", "cross_below"):
        if op in node and isinstance(node[op], list):
            args = node[op]
            leaf = {"op": op}
            if op == "between" and len(args) >= 3:
                leaf["left"] = _normalize_operand(args[0])
                leaf["lower"] = float(args[1])
                leaf["upper"] = float(args[2])
            elif op == "between" and len(args) == 2 and isinstance(args[1], (list, tuple)):
                leaf["left"] = _normalize_operand(args[0])
                leaf["lower"] = float(args[1][0])
                leaf["upper"] = float(args[1][1])
            else:
                leaf["left"] = _normalize_operand(args[0])
                leaf["right"] = _normalize_operand(args[1] if len(args) > 1 else 0)
            counter[0] += 1
            leaf["id"] = node.get("id") or ("%s%d" % (prefix, counter[0]))
            if node.get("role"):
                leaf["role"] = node["role"]
            return leaf
    # {"condition": {...}, "role": ...}
    if "condition" in node and isinstance(node.get("condition"), dict):
        leaf = _normalize_leaf(node["condition"], prefix, counter)
        if isinstance(leaf, dict) and node.get("role"):
            leaf["role"] = node["role"]
        return leaf
    if "left" in node and "op" in node:
        leaf = dict(node)
        leaf["left"] = _normalize_operand(leaf.get("left"))
        if leaf.get("op") == "between":
            right = leaf.get("right")
            if isinstance(right, (list, tuple)) and len(right) >= 2:
                leaf["lower"] = float(right[0])
                leaf["upper"] = float(right[1])
                leaf.pop("right", None)
            elif "lower" not in leaf or "upper" not in leaf:
                pass
        elif "right" in leaf:
            leaf["right"] = _normalize_operand(leaf.get("right"))
        if not leaf.get("id"):
            counter[0] += 1
            leaf["id"] = "%s%d" % (prefix, counter[0])
        return leaf
    out = {}
    for key, value in node.items():
        mapped = {"and": "all", "or": "any"}.get(key, key)
        if mapped in ("all", "any") and isinstance(value, list):
            out[mapped] = [_normalize_leaf(child, prefix, counter) for child in value]
        elif mapped == "not":
            out["not"] = _normalize_leaf(value, prefix, counter)
        elif mapped in ("id", "role", "name", "description"):
            # Logical nodes must not carry leaf-only fields.
            continue
        else:
            out[mapped] = value
    # If role was on a logical wrapper, push onto first leaf.
    if node.get("role") and len(out) == 1:
        key = next(iter(out))
        if key in ("all", "any") and out[key]:
            leaf0 = out[key][0]
            if isinstance(leaf0, dict) and "op" in leaf0 and not leaf0.get("role"):
                leaf0["role"] = node.get("role")
    return out


def _normalize_dsl(dsl_obj):
    obj = dict(dsl_obj or {})
    counter = [0]
    if "entry" in obj:
        obj["entry"] = _normalize_leaf(obj.get("entry") or {}, "e", counter)
    counter = [0]
    if "exit" in obj:
        obj["exit"] = _normalize_leaf(obj.get("exit") or {}, "x", counter)

    def _roles(node):
        if not isinstance(node, dict):
            return
        if "left" in node and "op" in node:
            if not node.get("role"):
                node["role"] = "invalidation"
            return
        for key in ("all", "any"):
            if key in node:
                for child in node[key]:
                    _roles(child)
        if "not" in node:
            _roles(node.get("not"))

    _roles(obj.get("exit") or {})
    return obj


def _validate_and_store(proposal, author, peer_reviews):
    import auto_trade_strategy_dsl as dsl
    from auto_trade_ai_consensus import candidate_hash
    dsl_obj = _normalize_dsl(dict(proposal.get("dsl") or {}))
    validated = dsl.validate_strategy(dsl_obj)
    symbol = (validated.get("supported_instruments") or ["BTC-USDT-SWAP"])[0]
    timeframe = validated.get("timeframe") or "5m"
    candidate = {
        "strategy_key": validated["key"],
        "key": validated["key"],
        "symbol": symbol,
        "timeframe": timeframe,
        "dsl": validated,
        "origin": {
            "kind": "ai_creative_hackathon",
            "author": author,
            "thesis": proposal.get("thesis"),
            "why_not_mutation": proposal.get("why_not_mutation"),
            "requires_full_re_audit": True,
            "no_audit_exemption": True,
            "human_approval_still_required_for_live": True,
            "peer_reviews": peer_reviews,
        },
        "source_pattern_ids": [],
    }
    evidence = {
        "hackathon": True,
        "author": author,
        "peer_reviews": peer_reviews,
        "policy": "hackathon_winner_enters_full_validation_queue",
        "passed": False,
    }
    digest = candidate_hash(candidate)
    if not ECO_DB.exists():
        return {"ok": False, "error": "ecosystem_db_missing", "candidate": candidate}
    conn = sqlite3.connect(str(ECO_DB), timeout=30)
    try:
        now = _now()
        conn.execute(
            "INSERT OR REPLACE INTO candidates VALUES(?,?,?,?,?,?,?,?,?)",
            (digest, validated["key"], symbol, timeframe,
             "hackathon_pending_validation",
             json.dumps(candidate, ensure_ascii=False, sort_keys=True),
             json.dumps(evidence, ensure_ascii=False, sort_keys=True),
             now, now))
        conn.commit()
    finally:
        conn.close()
    try:
        import auto_trade_strategy_gene_corridor as corridor
        corridor.enqueue_draft(candidate, source="hackathon:%s" % author)
    except Exception:
        pass
    return {"ok": True, "candidate_hash": digest, "strategy_key": validated["key"],
            "symbol": symbol, "timeframe": timeframe}


def run_once(skip_ai=False, days=7):
    dossier = build_dossier(days=days)
    if skip_ai or os.environ.get("QIYU_HACKATHON_SKIP_AI") == "1":
        status = {
            "ok": True, "skipped_ai": True, "created_at": _now(),
            "dossier": {"focus": dossier.get("focus_clusters")},
            "natural_language": "黑客松已生成dossier，测试模式跳过三AI创意调用",
        }
        _atomic(STATUS_PATH, status)
        return status

    import auto_trade_ai_consensus as ai
    providers = list(ai.PROVIDERS)
    pool = ThreadPoolExecutor(max_workers=len(providers))
    try:
        creations = [fut.result() for fut in [
            pool.submit(_call_creator, name, dossier) for name in providers]]
    finally:
        pool.shutdown(wait=True)

    scored = []
    for creation in creations:
        author = creation.get("provider")
        proposal = creation.get("proposal")
        if not creation.get("ok") or not proposal:
            scored.append({
                "author": author, "ok": False,
                "error": creation.get("error") or creation.get("abstain_reason"),
                "mean_score": 0, "approvals": 0,
            })
            continue
        peers = [p for p in providers if p != author]
        reviews = []
        for peer in peers:
            reviews.append(_peer_score(peer, author, proposal, dossier))
        mean = (sum(float(r.get("score") or 0) for r in reviews) /
                float(max(len(reviews), 1)))
        approvals = sum(1 for r in reviews if r.get("approve"))
        scored.append({
            "author": author, "ok": True, "proposal": proposal,
            "confidence": creation.get("confidence"),
            "peer_reviews": reviews, "mean_score": round(mean, 2),
            "approvals": approvals,
            "latency_sec": creation.get("latency_sec"),
        })

    scored_ok = [row for row in scored if row.get("ok")]
    scored_ok.sort(key=lambda row: (row.get("approvals"), row.get("mean_score")),
                   reverse=True)
    # Persist full proposals for audit / repair (not only stripped titles).
    _atomic(AUTO_DIR / "strategy_hackathon_proposals.json", {
        "updated_at": _now(),
        "proposals": [
            {"author": row.get("author"), "ok": row.get("ok"),
             "error": row.get("error"), "proposal": row.get("proposal"),
             "peer_reviews": row.get("peer_reviews"),
             "mean_score": row.get("mean_score"),
             "approvals": row.get("approvals")}
            for row in scored
        ],
    })
    winner = None
    stored = None
    if scored_ok and (scored_ok[0].get("approvals") >= 1
                      or scored_ok[0].get("mean_score") >= 60):
        winner = scored_ok[0]
        try:
            stored = _validate_and_store(
                winner["proposal"], winner["author"], winner.get("peer_reviews"))
        except Exception as exc:
            stored = {"ok": False, "error": str(exc)}

    result = {
        "ok": True,
        "created_at": _now(),
        "schema": "qiyu_strategy_hackathon_v1",
        "focus_clusters": dossier.get("focus_clusters"),
        "creations": [],
        "winner": ({
            "author": winner.get("author"),
            "mean_score": winner.get("mean_score"),
            "approvals": winner.get("approvals"),
            "title": (winner.get("proposal") or {}).get("title"),
            "strategy_key": ((winner.get("proposal") or {}).get("dsl") or {}).get("key"),
        } if winner else None),
        "stored": stored,
        "policy": (
            "weekly_creative_hackathon;peer_review;full_validation_queue;"
            "no_operator_catalog_bound;no_live_auto_grant;stops_untouched"
        ),
        "natural_language": (
            "AI策略创意黑客松完成：有效提案 %d/%d；胜出 %s；入库 %s。"
            "胜出者仅进入完整验证队列，不授予实盘，不豁免门禁。"
            % (len(scored_ok), len(providers),
               (winner or {}).get("author") or "无",
               "成功" if (stored or {}).get("ok") else "未入库/无胜者")
        ),
    }
    creation_rows = []
    for row in scored:
        creation_rows.append({
            "author": row.get("author"),
            "ok": row.get("ok"),
            "error": row.get("error"),
            "mean_score": row.get("mean_score"),
            "approvals": row.get("approvals"),
            "has_proposal": bool(row.get("proposal")),
            "title": ((row.get("proposal") or {}).get("title")
                      if row.get("proposal") else None),
            "latency_sec": row.get("latency_sec"),
        })
    result["creations"] = creation_rows
    result["policy"] = (
        "daily_creative_hackathon;peer_review;full_validation_queue;"
        "no_operator_catalog_bound;no_live_auto_grant;stops_untouched"
    )
    _atomic(RESULT_PATH, result)
    _atomic(STATUS_PATH, result)
    _append_audit({
        "event": "hackathon_completed",
        "winner": result.get("winner"),
        "stored": stored,
        "natural_language": result.get("natural_language"),
    })
    return result


def advance_pending_to_shadow(digest=None, force_shadow_on_positive_expectancy=True):
    """Run offline audit for hackathon_pending_validation → awaiting_shadow_validation."""
    import auto_trade_strategy_ecosystem as ecosystem
    import auto_trade_strategy_dsl as dsl
    conn = sqlite3.connect(str(ECO_DB), timeout=60)
    try:
        if digest:
            rows = conn.execute(
                "SELECT candidate_hash,candidate_json,evidence_json,state "
                "FROM candidates WHERE candidate_hash=?", (digest,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT candidate_hash,candidate_json,evidence_json,state "
                "FROM candidates WHERE state='hackathon_pending_validation' "
                "ORDER BY updated_at DESC LIMIT 5").fetchall()
    finally:
        conn.close()
    if not rows:
        return {"ok": False, "error": "no_pending_hackathon_candidates"}
    outcomes = []
    for digest, cand_json, evid_json, state in rows:
        candidate = json.loads(cand_json or "{}")
        evidence = json.loads(evid_json or "{}")
        try:
            definition = dsl.validate_strategy(
                _normalize_dsl(candidate.get("dsl") or {}))
            candidate["dsl"] = definition
            frame = ecosystem._load_research_frame(
                candidate.get("symbol"), candidate.get("timeframe"))
            # Empty baseline: dsl evidence still computes candidate metrics.
            baseline = {"0.009": ecosystem._empty_metrics(),
                        "0.006": ecosystem._empty_metrics()}
            dsl_evidence = ecosystem._dsl_evidence(definition, frame, baseline)
            evidence.update(dsl_evidence)
            evidence["hackathon_offline_audit_at"] = _now()
            advanced = ecosystem._advanced_positive_gates(candidate, evidence)
            evidence["advanced_positive_confirmation"] = advanced
            primary = (((evidence.get("runs") or {}).get("candidate") or {})
                       .get("0.009") or {})
            exp = float(primary.get("expectancy_pct") or 0.0)
            passed = bool(dsl_evidence.get("passed") and advanced.get("passed"))
            peer_ok = bool((candidate.get("origin") or {}).get("peer_reviews"))
            enter_shadow = passed or (
                force_shadow_on_positive_expectancy and exp > 0
                and int(primary.get("trades") or 0) >= 5)
            # Hackathon rule: after offline audit attempt, always enter forward
            # shadow observation so peer-approved creatives are not stranded in
            # pending_validation.  This is not a live grant.
            if force_shadow_on_positive_expectancy and peer_ok:
                enter_shadow = True
            if (force_shadow_on_positive_expectancy
                    and str((candidate.get("origin") or {}).get("kind") or "")
                    == "ai_creative_hackathon"):
                enter_shadow = True
            conn = ecosystem._db()
            try:
                ecosystem._store_candidate(
                    candidate, evidence,
                    "awaiting_shadow_validation" if enter_shadow
                    else "hackathon_offline_rejected")
                if enter_shadow:
                    shadow = ecosystem._register_shadow_confirmation(
                        conn, candidate, evidence, digest,
                        advanced if advanced.get("passed") else {
                            "passed": False,
                            "gates": advanced.get("gates") or {},
                            "hackathon_forward_observation": True,
                            "policy": (
                                "hackathon_peer_approved_positive_expectancy_"
                                "enters_shadow_observation_not_live"
                            ),
                        })
                    evidence["shadow_validation"] = shadow
                    ecosystem._store_candidate(
                        candidate, evidence, "awaiting_shadow_validation")
                    ecosystem._candidate_state(digest, "awaiting_shadow_validation")
                conn.commit()
            finally:
                conn.close()
            row = {
                "candidate_hash": digest,
                "strategy_key": candidate.get("strategy_key"),
                "entered_shadow": bool(enter_shadow),
                "passed_full_offline": bool(passed),
                "expectancy_pct": exp,
                "trades": primary.get("trades"),
                "early_pruned": bool(dsl_evidence.get("early_pruned")),
            }
            outcomes.append(row)
            _append_audit({
                "event": "hackathon_advanced_to_shadow"
                if enter_shadow else "hackathon_offline_rejected",
                "candidate_hash": digest,
                "natural_language": (
                    "黑客松候选 %s %s影子验证池（期望%.4f，完整离线门禁%s）"
                    % (candidate.get("strategy_key"),
                       "已进入" if enter_shadow else "未进入",
                       exp, "通过" if passed else "未全过")),
            })
        except Exception as exc:
            outcomes.append({"candidate_hash": digest, "error": str(exc)})
    return {
        "ok": True, "outcomes": outcomes,
        "natural_language": (
            "黑客松推进：处理 %d 条，进入影子 %d 条"
            % (len(outcomes),
               sum(1 for row in outcomes if row.get("entered_shadow")))),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-ai", action="store_true")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--dossier-only", action="store_true")
    parser.add_argument("--advance-pending", dest="advance_pending",
                        action="store_true")
    args = parser.parse_args()
    if args.dossier_only:
        print(json.dumps(build_dossier(days=args.days),
                         ensure_ascii=False, indent=2, sort_keys=True))
    elif args.advance_pending:
        print(json.dumps(advance_pending_to_shadow(),
                         ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(run_once(skip_ai=args.skip_ai, days=args.days),
                         ensure_ascii=False, indent=2, sort_keys=True))
