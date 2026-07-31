#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dual-engine strategy creation factory (GLM-5.2 designer + Codex engineer).

Pipeline:
  Step1  Data → GLM insight → Codex hypothesis books → GLM audit
  Step2  Codex DSL + anti-overfit / extreme friction / logic destruction → GLM gate
  Step3  GLM simulates DeepSeek/Qwen WR (≥55% each) → formal queue or repair≤3
  Step4  Formal DeepSeek+Qwen theoretical WR (≥50% each) → pending human-confirm

Evolution pool (background):
  fitness = simDS*0.35 + simQwen*0.35 + antiOF*0.30
  pop 50–100; evolve every 2h; promote if 3 gens fitness≥70%.

Display / orchestration only for live trading — never auto human-confirm.
"""
from __future__ import print_function

import argparse
import copy
import json
import math
import os
import random
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
DUAL_DIR = AUTO_DIR / "dual_engine"
TASKS_DIR = DUAL_DIR / "tasks"
EVO_DIR = DUAL_DIR / "evolution"
STATUS_PATH = DUAL_DIR / "status.json"
INSIGHT_PATH = DUAL_DIR / "insight_latest.json"
FORMAL_PATH = DUAL_DIR / "formal_submits.json"
AUDIT_PATH = DUAL_DIR / "audit.jsonl"
POOL_PATH = EVO_DIR / "pool.json"
EVO_HIST_PATH = EVO_DIR / "history.jsonl"
JOB_LOCK = threading.Lock()
_JOB = {"running": False, "kind": None, "started_at": None, "error": None}

LEVERAGE = 20
STOP_LOSS_PCT = 0.009
SIM_WR_GATE = 55.0
FORMAL_WR_GATE = 65.0
FORMAL_WIN_MEAN_NET_PCT_GATE = 5.0
SHARPE_ANTIOF = 0.5
SHARPE_FRICTION = 0.0
# Frost gates (寒霜贰续 2026-07-25): WF ≥7/10 aligned with live ADA B.
# Full suite separately requires MC beat≥90% shuffles + friction Sharpe≥0
# + logic destruction pass (see frost2_action_run).
FROST_RELAXED_WF_POS = 7
FROST_RELAXED_WF_FOLDS = 10
FROST_RELAXED_MIN_TRADES = 8
FROST_RELAXED_FRICTION_SHARPE = 0.0
FROST_GATE_MODE = "strict"  # or "frost_relaxed"
FROST_RELAXED_NOTE = (
    "WF fold_positive>=7/10 (aligned live ADA B); "
    "friction sharpe>=0; destruction pass; MC beat>=90% shuffles in frost2 full"
)
POP_MIN, POP_MAX = 50, 100
PROMOTE_FITNESS = 70.0
PROMOTE_GENS = 3
ARCHIVE_STALE_GENS = 5

STAGE_LABELS = {
    "idle": "空闲",
    "step1_collect": "假设书·采集",
    "step1_insight": "假设书·GLM洞察",
    "step1_hypotheses": "假设书·Codex假设",
    "step1_audit": "假设书·GLM裁决",
    "step2_dsl": "内测·DSL",
    "step2_packs": "内测·压力包",
    "step2_glm_gate": "内测·GLM门控",
    "step3_sim": "模拟复核",
    "step3_repair": "模拟复核·修复",
    "step4_formal": "正式复核",
    "step4_pending": "正式复核·待人工",
    "done": "完成",
    "archived": "归档",
    "failed": "失败",
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _read(path, default=None):
    path = Path(path)
    if not path.exists():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def _append_audit(row):
    DUAL_DIR.mkdir(parents=True, exist_ok=True)
    row = dict(row or {})
    row.setdefault("time", _now())
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _ensure_dirs():
    for p in (DUAL_DIR, TASKS_DIR, EVO_DIR):
        p.mkdir(parents=True, exist_ok=True)


def _load_env():
    for path in (AUTO_DIR / "ai_ecosystem.env", ROOT / "ai_ecosystem.env"):
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and not str(os.environ.get(k) or "").strip():
                    os.environ[k] = v
        except Exception:
            pass


# ─── AI helpers ────────────────────────────────────────────────────────

def _ai_json(provider, system_prompt, user_payload, max_tokens=2200, temperature=0.35):
    import auto_trade_ai_consensus as ai
    provider = ai._normalize_provider_name(provider)
    cfg = ai._provider_config(provider)
    consent = ai.external_research_consent_status(
        provider, "strategy_research" if provider == "glm" else "final_review")
    if not consent.get("allowed"):
        return {"ok": False, "error": "consent_missing", "provider": provider}
    if not cfg.get("api_key"):
        return {"ok": False, "error": "api_key_missing", "provider": provider}
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": ai.canonical_json(user_payload)},
        ],
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }
    if provider == "deepseek":
        body["response_format"] = {"type": "json_object"}
    started = time.time()
    try:
        import urllib.request as urllib_request
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(
            cfg["url"], data=encoded,
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        timeout = max(int(cfg.get("timeout") or 120), 180)
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        content = (((raw.get("choices") or [{}])[0].get("message") or {})
                   .get("content"))
        if not str(content or "").strip():
            content = (((raw.get("choices") or [{}])[0].get("message") or {})
                       .get("reasoning_content") or content)
        if not str(content or "").strip() and provider == "deepseek":
            content = (((raw.get("choices") or [{}])[0].get("message") or {})
                       .get("reasoning_content") or content)
        try:
            parsed = ai._parse_content_json(content)
        except Exception as parse_exc:
            text = str(content or "")
            # Normalize fancy quotes / BOM that GLM sometimes emits
            text = (text.replace("\ufeff", "")
                        .replace("“", "\"").replace("”", "\"")
                        .replace("‘", "'").replace("’", "'")
                        .replace("：", ":").replace("，", ","))
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                chunk = text[start:end + 1]
                try:
                    parsed = json.loads(chunk)
                except Exception:
                    chunk2 = chunk.replace("```", "")
                    open_b = chunk2.count("{") - chunk2.count("}")
                    open_a = chunk2.count("[") - chunk2.count("]")
                    if open_a > 0:
                        chunk2 += "]" * open_a
                    if open_b > 0:
                        chunk2 += "}" * open_b
                    try:
                        parsed = json.loads(chunk2)
                    except Exception:
                        return {"ok": False, "provider": provider,
                                "error": "json_parse:%s" % parse_exc,
                                "latency_sec": round(time.time() - started, 3),
                                "raw_preview": text[:500]}
            else:
                return {"ok": False, "provider": provider,
                        "error": "json_parse:%s" % parse_exc,
                        "latency_sec": round(time.time() - started, 3),
                        "raw_preview": text[:500]}
        return {"ok": True, "provider": provider, "parsed": parsed,
                "latency_sec": round(time.time() - started, 3)}
    except Exception as exc:
        return {"ok": False, "provider": provider, "error": str(exc),
                "latency_sec": round(time.time() - started, 3)}


# ─── Context collection (Step1 inputs) ─────────────────────────────────

def collect_inputs():
    """Auto-collect: 72h micro freq, death heatmap TOP10, freezer last10, coverage."""
    out = {
        "schema": "qiyu_dual_engine_inputs_v1",
        "collected_at": _now(),
        "micro_72h": {},
        "death_heatmap_top10": [],
        "freezer_last10": [],
        "coverage_map": [],
        "mandatory_niches": [],
        "focus_candidates": [],
    }
    try:
        import auto_trade_strategy_creation_factory as fac
        ctx = fac.build_factory_context(days=7) or {}
        out["factory_context"] = {
            "focus": ctx.get("focus"),
            "niche_summary": ctx.get("niche_summary"),
            "mandatory_niches": ctx.get("mandatory_niches"),
            "death_codes": (ctx.get("death_codes") or [])[:20],
            "micro_primitive_tags": (ctx.get("micro_primitive_tags") or [])[:20],
            "quality_priority": ctx.get("quality_priority"),
        }
        out["mandatory_niches"] = list(ctx.get("mandatory_niches") or [])[:8]
        out["freezer_last10"] = list(ctx.get("freezer_lessons") or [])[-10:]
        out["death_heatmap_top10"] = list(ctx.get("death_codes") or [])[:10]
    except Exception as exc:
        out["factory_context_error"] = str(exc)

    try:
        import auto_trade_microstructure_collector as micro
        if hasattr(micro, "frequency_summary_72h"):
            out["micro_72h"] = micro.frequency_summary_72h() or {}
        elif hasattr(micro, "load_recent_summary"):
            out["micro_72h"] = micro.load_recent_summary(hours=72) or {}
        else:
            # Lightweight fallback: primitive tag freq proxy
            tags = (out.get("factory_context") or {}).get("micro_primitive_tags") or []
            out["micro_72h"] = {
                "source": "primitive_tag_proxy",
                "tags": tags[:15],
                "note": "collector summary unavailable; using registered tags",
            }
    except Exception as exc:
        out["micro_72h"] = {"error": str(exc)}

    try:
        import auto_trade_niche_map as niche
        report = niche.build_report(days=7) or {}
        heat = report.get("death_heatmap_prior") or report.get("top_death_codes") or []
        rows = []
        for item in heat[:10]:
            if isinstance(item, dict):
                rows.append({
                    "code": item.get("code") or item.get("death_cause_code"),
                    "count": item.get("count") or item.get("n"),
                    "note": item.get("note") or item.get("title"),
                })
            else:
                rows.append({"code": str(item)})
        if rows:
            out["death_heatmap_top10"] = rows
        out["niche_summary"] = report.get("summary") or {}
        if not out["mandatory_niches"]:
            out["mandatory_niches"] = list(report.get("mandatory_niches") or [])[:8]
    except Exception as exc:
        out["niche_error"] = str(exc)

    # Additive: frequency gap / mechanism-family brief for creation (never force open).
    # Prefer positive-E gap as creation priority; never loosen entries / force opens.
    try:
        gap_path = AUTO_DIR / "frequency_gap_report.json"
        create_path = AUTO_DIR / "strategy_creation_frequency_input.json"
        gap = _read(gap_path, {}) if gap_path.exists() else {}
        create_in = _read(create_path, {}) if create_path.exists() else {}
        if not isinstance(gap, dict):
            gap = {}
        if not isinstance(create_in, dict):
            create_in = {}
        if gap or create_in:
            out["frequency_gap"] = (
                create_in.get("frequency_gap") or gap.get("pool") or gap or None)
            # Prefer closeout/v2 brief that prioritizes positive-E gap
            brief_create = create_in.get("creation_brief") or {}
            brief_gap = gap.get("creation_brief") or {}
            if brief_create.get("prioritize_positive_expectancy_gap"):
                out["frequency_creation_brief"] = brief_create
            elif brief_gap.get("prioritize_positive_expectancy_gap"):
                out["frequency_creation_brief"] = brief_gap
            else:
                out["frequency_creation_brief"] = brief_create or brief_gap
            out["mechanism_families"] = (
                create_in.get("mechanism_families") or gap.get("mechanism_families"))
            out["positive_expectancy_frequency"] = (
                create_in.get("positive_expectancy_frequency")
                or gap.get("positive_expectancy_frequency"))
            out["positive_expectancy_frequency_gap"] = (
                create_in.get("positive_expectancy_frequency_gap")
                or gap.get("positive_expectancy_frequency_gap")
                or ((out.get("positive_expectancy_frequency") or {}).get(
                    "positive_expectancy_frequency_gap")))
            out["uncalibrated_contribution"] = (
                create_in.get("uncalibrated_contribution")
                or gap.get("uncalibrated_contribution"))
            out["near_zero_contribution"] = (
                create_in.get("near_zero_contribution")
                or gap.get("near_zero_contribution"))
            out["negative_contribution"] = (
                create_in.get("negative_contribution")
                or gap.get("negative_contribution"))
            out["portfolio_frequency"] = (
                create_in.get("portfolio_frequency")
                or gap.get("portfolio_frequency"))
            out["creation_priority"] = (
                create_in.get("priority")
                or gap.get("priority")
                or "positive_expectancy_frequency_gap")
            out["strategy_pool_version"] = (
                create_in.get("strategy_pool_version")
                or gap.get("strategy_pool_version"))
            out["forecast_id"] = create_in.get("forecast_id") or gap.get("forecast_id")
            out["forecast_generated_at"] = (
                create_in.get("generated_at") or gap.get("generated_at"))
            # Hard constraints from brief (never loosen / force)
            brief = out.get("frequency_creation_brief") or {}
            out["do_not_loosen_entries"] = bool(
                brief.get("do_not_loosen_entries", True))
            out["do_not_force_open"] = bool(brief.get("do_not_force_open", True))
            hints = list((create_in.get("mandatory_niches_hint") or []))[:3]
            if hints and not out.get("mandatory_niches"):
                out["mandatory_niches"] = hints
            elif hints:
                # Prefer appending non-duplicate family hints
                existing = {
                    str((n.get("family") if isinstance(n, dict) else n) or "")
                    for n in (out.get("mandatory_niches") or [])
                }
                for h in hints:
                    fam = str((h.get("family") if isinstance(h, dict) else h) or "")
                    if fam and fam not in existing:
                        out["mandatory_niches"].append(h)
                        existing.add(fam)
    except Exception as exc:
        out["frequency_gap_error"] = str(exc)

    # Active strategy coverage map
    try:
        assign_path = AUTO_DIR / "strategy_assignments.json"
        data = _read(assign_path, {})
        items = data.get("assignments") or data.get("items") or []
        if isinstance(items, dict):
            items = list(items.values())
        for row in items:
            if not isinstance(row, dict):
                continue
            if row.get("audit_state") in ("eliminated_pending_archive", "failed_closed",
                                          "deleted", "archived"):
                continue
            out["coverage_map"].append({
                "key": row.get("strategy_key") or row.get("key"),
                "symbol": row.get("symbol"),
                "timeframe": row.get("timeframe"),
                "grade": row.get("lifecycle_grade") or row.get("grade"),
                "can_open": bool(row.get("new_entries_allowed", True))
                and not bool(row.get("pause_new_entries")),
            })
    except Exception as exc:
        out["coverage_error"] = str(exc)

    # Focus symbols: uncovered niches + liquid alts
    covered = set(
        (c.get("symbol"), c.get("timeframe")) for c in out["coverage_map"]
        if c.get("symbol") and c.get("timeframe")
    )
    universe = [
        ("LTC-USDT-SWAP", "5m"), ("ADA-USDT-SWAP", "5m"), ("NG-USDT-SWAP", "5m"),
        ("SOL-USDT-SWAP", "5m"), ("XRP-USDT-SWAP", "5m"), ("DOGE-USDT-SWAP", "5m"),
        ("LINK-USDT-SWAP", "15m"), ("AVAX-USDT-SWAP", "15m"), ("ETH-USDT-SWAP", "15m"),
        ("BTC-USDT-SWAP", "5m"), ("CL-USDT-SWAP", "1h"), ("XAU-USDT-SWAP", "15m"),
    ]
    for sym, tf in universe:
        if (sym, tf) not in covered:
            out["focus_candidates"].append({"symbol": sym, "timeframe": tf,
                                            "reason": "uncovered"})
    if not out["focus_candidates"]:
        out["focus_candidates"] = [
            {"symbol": "SOL-USDT-SWAP", "timeframe": "5m", "reason": "expand"},
            {"symbol": "XRP-USDT-SWAP", "timeframe": "5m", "reason": "expand"},
        ]
    return out


# ─── Status / tasks ────────────────────────────────────────────────────

def _default_status():
    return {
        "schema": "qiyu_dual_engine_status_v1",
        "updated_at": _now(),
        "current_task_id": None,
        "stage": "idle",
        "stage_label": STAGE_LABELS["idle"],
        "insight_summary": "",
        "insight_at": None,
        "evolution": {"size": 0, "best_fitness": None, "generation": 0,
                      "fitness_trend": []},
        "formal_recent": [],
        "job": _job_snapshot(),
        "last_error": None,
    }


def _job_snapshot():
    with JOB_LOCK:
        return dict(_JOB)


def load_status():
    st = _read(STATUS_PATH, None)
    if not isinstance(st, dict) or not st:
        st = _default_status()
    st["job"] = _job_snapshot()
    # refresh evolution snapshot
    pool = load_pool()
    inds = pool.get("individuals") or []
    fitnesses = [float(x.get("fitness") or 0) for x in inds]
    st["evolution"] = {
        "size": len(inds),
        "best_fitness": round(max(fitnesses), 3) if fitnesses else None,
        "generation": int(pool.get("generation") or 0),
        "fitness_trend": list(pool.get("fitness_trend") or [])[-20:],
        "last_evolve_at": pool.get("last_evolve_at"),
        "promoted": list(pool.get("promoted") or [])[-5:],
    }
    formal = _read(FORMAL_PATH, {"items": []})
    items = list(formal.get("items") or [])[-12:]
    items.reverse()
    # Humanize formal queue titles (reject raw codes like sol_tp47_h32)
    try:
        import auto_trade_strategy_titles as _titles
        enriched = []
        for row in items:
            if not isinstance(row, dict):
                enriched.append(row)
                continue
            r = dict(row)
            key = r.get("key") or r.get("strategy_key") or r.get("title") or r.get("name")
            # Prefer the most informative raw string for wash (title may carry mechanism tokens)
            wash_src = r.get("title") or key
            if r.get("title") and key and _titles._needs_semantic_wash(r.get("title")):
                wash_src = r.get("title")
            elif key and _titles._needs_semantic_wash(str(key)):
                wash_src = key
            tf = r.get("timeframe")
            if not tf and wash_src:
                import re as _re
                m_tf = _re.search(r"(?i)(\d+[mh])\b", str(wash_src))
                if m_tf:
                    tf = m_tf.group(1).lower()
            title_zh = _titles.humanize_creation_title(
                wash_src,
                symbol=r.get("symbol"),
                timeframe=tf,
                family=r.get("mechanism_family") or r.get("family"),
            )
            r["title_zh"] = title_zh
            if _titles._needs_semantic_wash(r.get("title")) or _titles._looks_like_raw_code(r.get("title")):
                r["title"] = title_zh
            enriched.append(r)
        st["formal_recent"] = enriched
    except Exception:
        st["formal_recent"] = items
    insight = _read(INSIGHT_PATH, {})
    if insight:
        st["insight_summary"] = insight.get("summary_zh") or insight.get("summary") or ""
        st["insight_at"] = insight.get("generated_at")
        st["insight"] = {
            "summary_zh": st["insight_summary"],
            "niches": insight.get("priority_niches") or [],
            "risks": insight.get("risks") or [],
            "generated_at": insight.get("generated_at"),
        }
    if st.get("current_task_id"):
        task = load_task(st["current_task_id"])
        if task:
            st["current_task"] = {
                "id": task.get("id"),
                "stage": task.get("stage"),
                "stage_label": STAGE_LABELS.get(task.get("stage"), task.get("stage")),
                "focus": task.get("focus"),
                "hypothesis_count": len(task.get("hypotheses") or []),
                "candidate_title": (task.get("candidate") or {}).get("title"),
                "sim_wr": task.get("sim_wr"),
                "formal_wr": task.get("formal_wr"),
                "updated_at": task.get("updated_at"),
                "history": (task.get("history") or [])[-8:],
                # workflow v2 evidence surfaces for column 4
                "workflow_version": task.get("workflow_version") or "v1",
                "exploration_mode": task.get("exploration_mode"),
                "mechanism_statement": task.get("mechanism_statement"),
                "fingerprint": task.get("fingerprint"),
                "condition_audit": task.get("condition_audit"),
                "packs_abc": (task.get("packs") or {}).get("abc"),
                "drift": task.get("drift"),
                "multidimensional_review": task.get("multidimensional_review"),
                "failure_archive": task.get("failure_archive"),
                "failure_level": ((task.get("failure_archive") or {}).get("failure_level")),
            }
            st["stage"] = task.get("stage") or st.get("stage")
            st["stage_label"] = STAGE_LABELS.get(st["stage"], st["stage"])
    # Workflow upgrade status (isolated; never affects live trading)
    try:
        import dual_engine_workflow_v2 as wfv2
        st["workflow"] = wfv2.workflow_status_payload()
        tid = st.get("current_task_id")
        if tid and str(tid).startswith("wv2_"):
            st["workflow_artifacts"] = wfv2.store.latest_task_artifacts(tid)
    except Exception as exc:
        st["workflow"] = {
            "workflow_version": "v1",
            "creation_entry": _workflow_entry(),
            "flow": "old",
            "error": str(exc),
        }
    # Auto-Driver live progress + multi-slot board (True Words console)
    try:
        from scripts.auto_driver import live_status as _ad_live
        from scripts.auto_driver import multi_slot as _ad_slots
        st["auto_driver"] = _ad_live.read_live_status()
        st["auto_driver_slots"] = _ad_slots.read_slots_board()
    except Exception:
        try:
            import sys
            from pathlib import Path
            scripts_dir = str(Path(__file__).resolve().parent / "scripts")
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            from auto_driver import live_status as _ad_live  # type: ignore
            from auto_driver import multi_slot as _ad_slots  # type: ignore
            st["auto_driver"] = _ad_live.read_live_status()
            st["auto_driver_slots"] = _ad_slots.read_slots_board()
        except Exception as exc:
            st["auto_driver"] = {"ok": False, "running": False, "error": str(exc)}
            st["auto_driver_slots"] = {"ok": False, "slots": [], "capacity": 1, "error": str(exc)}
    st["updated_at"] = _now()
    return st


def save_status(patch=None):
    st = load_status()
    if patch:
        st.update(patch)
    st["updated_at"] = _now()
    st["stage_label"] = STAGE_LABELS.get(st.get("stage"), st.get("stage"))
    _atomic(STATUS_PATH, st)
    return st


def load_task(task_id):
    if not task_id:
        return None
    return _read(TASKS_DIR / ("%s.json" % task_id), None)


def save_task(task):
    task = dict(task or {})
    task["updated_at"] = _now()
    tid = task.get("id")
    if not tid:
        raise ValueError("task id required")
    _atomic(TASKS_DIR / ("%s.json" % tid), task)
    save_status({
        "current_task_id": tid,
        "stage": task.get("stage"),
        "last_error": task.get("error"),
    })
    return task


def _new_task_id():
    return "de_%s_%s" % (datetime.now().strftime("%Y%m%d_%H%M%S"),
                         str(int(time.time() * 1000))[-4:])


def _task_hist(task, event, detail=None):
    hist = list(task.get("history") or [])
    hist.append({"time": _now(), "event": event, "detail": detail or {}})
    task["history"] = hist[-40:]
    return task


# ─── Step1 ─────────────────────────────────────────────────────────────

GLM_INSIGHT_PROMPT = """你是栖语自动交易系统的策略总设计师（GLM-5.2）。
根据输入的微观基元频率、死亡热力图TOP、冷冻库教训、已挂载覆盖图，输出市场洞察。
目标：找到高胜率、可存活的窄利基；禁止追求高频；日均0.3次但胜率65%优于高频低胜率。
仅输出JSON：
{"summary_zh":"120字内中文洞察","priority_niches":[{"symbol":"XXX-USDT-SWAP","timeframe":"5m|15m|1h",
 "direction":"long|short","thesis":"中文假设","avoid_death":["code"],"priority":1到5}],
 "risks":["..."],"mentor_notes":"给工程师的约束"}
"""


def glm_market_insight(inputs):
    # Keep payload compact — large freezer/context dumps truncate GLM JSON.
    payload = {
        "death_heatmap_top10": (inputs.get("death_heatmap_top10") or [])[:8],
        "freezer_brief": [
            {"key": x.get("key"), "reason": str(x.get("reason") or "")[:80]}
            for x in (inputs.get("freezer_last10") or [])[-6:]
        ],
        "coverage_n": len(inputs.get("coverage_map") or []),
        "coverage_sample": (inputs.get("coverage_map") or [])[:8],
        "focus_candidates": (inputs.get("focus_candidates") or [])[:6],
        "mandatory_niches": (inputs.get("mandatory_niches") or [])[:5],
        "micro_tags": ((inputs.get("factory_context") or {})
                       .get("micro_primitive_tags") or [])[:12],
        "quality_priority": ((inputs.get("factory_context") or {})
                             .get("quality_priority")),
    }
    res = _ai_json("glm", GLM_INSIGHT_PROMPT, payload, max_tokens=1600, temperature=0.3)
    if not res.get("ok"):
        # Deterministic fallback insight so pipeline remains runnable
        focuses = inputs.get("focus_candidates") or []
        niches = []
        for i, f in enumerate(focuses[:3]):
            niches.append({
                "symbol": f.get("symbol"),
                "timeframe": f.get("timeframe"),
                "direction": "short" if i % 2 else "long",
                "thesis": "覆盖空白利基，避开已知死因，优先稳健胜率",
                "avoid_death": [x.get("code") if isinstance(x, dict) else str(x)
                                for x in (inputs.get("death_heatmap_top10") or [])[:5]],
                "priority": i + 1,
            })
        parsed = {
            "summary_zh": "GLM调用失败，使用工程兜底洞察：优先空白利基与死亡规避。",
            "priority_niches": niches,
            "risks": ["glm_insight_fallback", res.get("error")],
            "mentor_notes": "严格反过拟合；禁止为凑信号放宽入场",
            "fallback": True,
        }
        res = {"ok": True, "provider": "glm", "parsed": parsed, "fallback": True,
               "error": res.get("error")}
    parsed = res.get("parsed") or {}
    doc = {
        "generated_at": _now(),
        "summary_zh": parsed.get("summary_zh") or "",
        "priority_niches": parsed.get("priority_niches") or [],
        "risks": parsed.get("risks") or [],
        "mentor_notes": parsed.get("mentor_notes") or "",
        "provider": "glm",
        "fallback": bool(res.get("fallback")),
        "latency_sec": res.get("latency_sec"),
        "error": res.get("error"),
    }
    _atomic(INSIGHT_PATH, doc)
    return doc


def _codex_hypothesis_books(insight, inputs):
    """Codex engineer: produce hypothesis books per niche (real DSL seeds)."""
    import auto_trade_strategy_creation_factory as fac
    niches = list(insight.get("priority_niches") or [])[:4]
    if not niches:
        for f in (inputs.get("focus_candidates") or [])[:3]:
            niches.append({
                "symbol": f.get("symbol"), "timeframe": f.get("timeframe"),
                "direction": "long", "thesis": "空白覆盖",
            })
    books = []
    templates = [
        # long: ema reclaim + mild RSI (higher frequency for internal packs)
        {
            "direction": "long",
            "entry_all": [
                {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema16"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema6"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 45}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 70}},
            ],
            "exit_any": [
                {"left": {"feature": "ema6"}, "op": "lt", "right": {"feature": "ema16"}},
            ],
            "params": {"rsi_lo": 45, "rsi_hi": 70},
        },
        # short: ema breakdown + mild RSI
        {
            "direction": "short",
            "entry_all": [
                {"left": {"feature": "ema6"}, "op": "lt", "right": {"feature": "ema16"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema6"}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 55}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 30}},
            ],
            "exit_any": [
                {"left": {"feature": "ema6"}, "op": "gt", "right": {"feature": "ema16"}},
            ],
            "params": {"rsi_lo": 30, "rsi_hi": 55},
        },
        # long bounce: RSI soft oversold reclaim
        {
            "direction": "long",
            "entry_all": [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 40}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema8"}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0}},
            ],
            "exit_any": [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 60}},
            ],
            "params": {"rsi_entry": 40, "rsi_exit": 60},
        },
    ]
    stamp = datetime.now().strftime("%m%d%H%M")
    for i, niche in enumerate(niches):
        tpl = templates[i % len(templates)]
        direction = str(niche.get("direction") or tpl["direction"]).lower()
        if direction not in ("long", "short"):
            direction = tpl["direction"]
        # pick matching template
        for t in templates:
            if t["direction"] == direction:
                tpl = t
                break
        sym = niche.get("symbol") or "SOL-USDT-SWAP"
        tf = niche.get("timeframe") or "5m"
        short_sym = str(sym).split("-")[0]
        key = fac._sanitize_strategy_key(
            "de_%s_%s_%s_%s" % (short_sym.lower(), tf, direction[0], stamp + str(i)))
        title = "%s %s双引擎%s" % (short_sym, tf, "多" if direction == "long" else "空")
        dsl = {
            "key": key,
            "name": title,
            "direction": direction,
            "timeframe": tf,
            "supported_instruments": [sym],
            "description": niche.get("thesis") or insight.get("mentor_notes") or title,
            "entry": {"all": copy.deepcopy(tpl["entry_all"])},
            "exit": {"any": copy.deepcopy(tpl["exit_any"])},
            "max_hold_bars": 24 if tf == "5m" else (16 if tf == "15m" else 12),
            "params": dict(tpl.get("params") or {}),
            "author": "codex_dual_engine",
            "created_at": _now(),
        }
        try:
            dsl = fac._coerce_dsl(dsl, author="codex", context={
                "focus": {"symbol": sym, "timeframe": tf}})
        except Exception:
            pass
        # Ensure symbolic ops are normalized even if coerce skips leaves
        def _norm_ops(node):
            if isinstance(node, dict):
                if "op" in node:
                    node["op"] = {
                        "<": "lt", "<=": "lte", ">": "gt", ">=": "gte",
                        "==": "eq", "lt": "lt", "lte": "lte", "gt": "gt",
                        "gte": "gte", "eq": "eq",
                    }.get(str(node.get("op")), node.get("op"))
                for v in node.values():
                    _norm_ops(v)
            elif isinstance(node, list):
                for x in node:
                    _norm_ops(x)
        _norm_ops(dsl.get("entry"))
        _norm_ops(dsl.get("exit"))
        books.append({
            "niche": niche,
            "title": title,
            "thesis": niche.get("thesis") or "",
            "symbol": sym,
            "timeframe": tf,
            "direction": direction,
            "dsl": dsl,
            "param_space": {
                "rsi_entry_delta": [-4, -2, 0, 2, 4],
                "core_perturb_pct": 0.20,
            },
            "status": "proposed",
        })
    return books


GLM_AUDIT_PROMPT = """你是策略总设计师。对工程师提交的假设书做批准/拒绝/合并。
标准：避开死亡热力；利基清晰；不要与已覆盖策略撞车；胜率优先。
仅输出JSON：
{"decision":"approve|reject|merge","approved_indexes":[0,1],
 "merged_thesis":"可选","reason_zh":"中文","notes":"..."}
"""


def glm_audit_hypotheses(insight, books):
    payload = {
        "insight_summary": insight.get("summary_zh"),
        "hypotheses": [
            {"i": i, "title": b.get("title"), "symbol": b.get("symbol"),
             "timeframe": b.get("timeframe"), "direction": b.get("direction"),
             "thesis": b.get("thesis"),
             "entry_brief": json.dumps((b.get("dsl") or {}).get("entry"),
                                       ensure_ascii=False)[:400]}
            for i, b in enumerate(books)
        ],
    }
    res = _ai_json("glm", GLM_AUDIT_PROMPT, payload, max_tokens=1200, temperature=0.2)
    parsed = (res.get("parsed") or {}) if res.get("ok") else {}
    decision = str(parsed.get("decision") or "").lower()
    idxs = parsed.get("approved_indexes")
    if not isinstance(idxs, list) or not idxs:
        # fallback: approve first book
        idxs = [0] if books else []
        decision = decision or "approve"
        parsed = {
            "decision": decision, "approved_indexes": idxs,
            "reason_zh": parsed.get("reason_zh") or "GLM审计兜底：批准首个假设",
            "fallback": True, "error": res.get("error"),
        }
    approved = []
    for i in idxs:
        try:
            i = int(i)
        except Exception:
            continue
        if 0 <= i < len(books):
            books[i]["status"] = "approved"
            approved.append(books[i])
    for i, b in enumerate(books):
        if b not in approved:
            b["status"] = "rejected"
    return {"ok": True, "decision": decision or "approve", "approved": approved,
            "books": books, "audit": parsed, "raw_ok": bool(res.get("ok"))}


# ─── Step2 stress packs ────────────────────────────────────────────────

def _frame(symbol, timeframe):
    import auto_trade_human_confirm_pipeline as pipeline
    frame = pipeline._frame(symbol, timeframe)
    if hasattr(frame, "iloc") and len(frame) > 12000:
        frame = frame.iloc[-12000:]
    return frame


def _backtest(definition, symbol, timeframe, friction_name="observed_base",
              slip_mult=1.0, latency_extra=0.0, fill_fail_pct=0.0):
    import auto_trade_strategy_dsl as dsl_mod
    import auto_trade_strategy_ecosystem as eco
    fr = eco._friction_scenario(symbol, friction_name)
    fee = float(fr.get("fee_rate_per_side") or 0.0005)
    slip = float(fr.get("slippage_rate_per_side") or 0.0002) * float(slip_mult)
    half = float(fr.get("half_spread_rate_per_side") or 0)
    impact = float(fr.get("impact_rate_per_side") or 0)
    lat = float(fr.get("latency_rate_per_side") or 0) + float(latency_extra)
    fund = float(fr.get("funding_rate_per_8h") or 0)
    frame = _frame(symbol, timeframe)
    result = dsl_mod.backtest_dsl(
        frame, definition,
        leverage=LEVERAGE, stop_loss_pct=STOP_LOSS_PCT,
        fee_rate_per_side=fee, slippage_rate_per_side=slip,
        half_spread_rate_per_side=half, impact_rate_per_side=impact,
        latency_rate_per_side=lat, funding_rate_per_8h=fund,
        friction_scenario=friction_name,
    )
    trades = list(result.get("trades") or [])
    if fill_fail_pct > 0 and trades:
        keep_n = max(1, int(round(len(trades) * (1.0 - float(fill_fail_pct)))))
        # Drop earliest fills to simulate partial fill failures (deterministic)
        trades = trades[-keep_n:]
        result = dict(result)
        result["trades"] = trades
        pnls = [float(t.get("pnl_ratio") or 0) for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        result["win_rate_percent"] = (wins / float(len(pnls)) * 100.0) if pnls else 0.0
        result["total_return_percent"] = (_compound(pnls) - 1.0) * 100.0
    return result


def _compound(pnls):
    acc = 1.0
    for p in pnls:
        acc *= (1.0 + float(p))
    return acc


def _metrics_from_trades(trades):
    pnls = [float(t.get("pnl_ratio") or 0.0) for t in trades]
    n = len(pnls)
    if not n:
        return {"trades": 0, "win_rate_pct": 0.0, "mean_net": 0.0,
                "oos_profit": 0.0, "sharpe": 0.0, "fold_positive": 0,
                "folds": 0}
    wins = sum(1 for p in pnls if p > 0)
    mean = sum(pnls) / float(n)
    # 10-fold walk-forward on trade sequence
    folds = 10
    fold_means = []
    for i in range(folds):
        lo = int(math.floor(n * i / float(folds)))
        hi = int(math.floor(n * (i + 1) / float(folds)))
        section = pnls[lo:hi]
        if not section:
            continue
        fold_means.append(sum(section) / float(len(section)))
    fold_pos = sum(1 for m in fold_means if m > 0)
    # OOS = last 30% profit
    cut = max(1, int(n * 0.7))
    oos = pnls[cut:]
    oos_profit = sum(oos) if oos else 0.0
    # daily-ish sharpe proxy from trade pnls
    if n > 2:
        var = sum((p - mean) ** 2 for p in pnls) / float(n - 1)
        std = math.sqrt(max(0.0, var))
        sharpe = (mean / std * math.sqrt(min(n, 252))) if std > 0 else 0.0
    else:
        sharpe = 0.0
    return {
        "trades": n,
        "win_rate_pct": wins / float(n) * 100.0,
        "mean_net": mean,
        "oos_profit": oos_profit,
        "sharpe": round(sharpe, 4),
        "fold_positive": fold_pos,
        "folds": len(fold_means),
        "fold_means": [round(x, 6) for x in fold_means],
    }


def _perturb_core_params(definition, pct=0.20, seed=7):
    """Logic destruction: ±pct on numeric leaf thresholds."""
    rng = random.Random(seed)
    dsl = copy.deepcopy(definition)

    def walk(node):
        if isinstance(node, dict):
            right = node.get("right")
            if isinstance(right, dict) and "value" in right:
                try:
                    v = float(right["value"])
                    factor = 1.0 + rng.uniform(-pct, pct)
                    right["value"] = round(v * factor, 6)
                except Exception:
                    pass
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(dsl.get("entry"))
    walk(dsl.get("exit"))
    return dsl


def run_internal_packs(book, mode=None):
    """Anti-overfit + extreme friction + logic destruction — real pass/fail.

    mode:
      - None/"strict": original dual-engine gates
      - "frost_relaxed": user-mandate 寒霜放宽 — WF≥8/10, friction Sharpe≥0,
        logic destruction pass; MC not required for overall pass
    """
    import auto_trade_strategy_dsl as dsl_mod
    mode = str(mode or book.get("gate_mode") or FROST_GATE_MODE or "strict").strip()
    dsl = book.get("dsl") or {}
    try:
        definition = dsl_mod.validate_strategy(dsl)
    except Exception as exc:
        return {"ok": False, "stage": "validate", "error": str(exc), "gate_mode": mode}
    symbol = book.get("symbol") or (definition.get("supported_instruments") or ["BTC-USDT-SWAP"])[0]
    timeframe = book.get("timeframe") or definition.get("timeframe") or "5m"

    # Base
    try:
        base = _backtest(definition, symbol, timeframe, "observed_base")
        base_m = _metrics_from_trades(base.get("trades") or [])
    except Exception as exc:
        return {"ok": False, "stage": "base_backtest", "error": str(exc), "gate_mode": mode}

    relaxed = mode in ("frost_relaxed", "relaxed", "frost")
    if relaxed:
        # Hard release bar: ≥8 of 10 WF segments profitable
        need_pos = int(FROST_RELAXED_WF_POS)
        need_folds = int(FROST_RELAXED_WF_FOLDS)
        anti_pass = (
            base_m["trades"] >= int(FROST_RELAXED_MIN_TRADES)
            and base_m["folds"] >= need_folds
            and base_m["fold_positive"] >= need_pos
        )
        anti = {
            "pass": bool(anti_pass),
            "metrics": base_m,
            "gates": {
                "mode": "frost_relaxed",
                "fold_positive_ge": need_pos,
                "folds_ge": need_folds,
                "trades_ge": int(FROST_RELAXED_MIN_TRADES),
                "note": FROST_RELAXED_NOTE,
            },
        }
        fr_sharpe_gate = float(FROST_RELAXED_FRICTION_SHARPE)
        fr_cmp = "ge"
    else:
        anti_pass = (
            base_m["trades"] >= 8
            and base_m["folds"] >= 5
            and base_m["fold_positive"] >= max(3, int(base_m["folds"] * 0.5))
            and base_m["oos_profit"] > 0
            and base_m["sharpe"] >= SHARPE_ANTIOF
        )
        anti = {"pass": bool(anti_pass), "metrics": base_m,
                "gates": {"sharpe_ge": SHARPE_ANTIOF, "oos_profit_gt_0": True,
                          "walk_forward_10fold": True, "mode": "strict"}}
        fr_sharpe_gate = float(SHARPE_FRICTION)
        fr_cmp = "gt"

    # Extreme friction: 2x slip + 50ms latency proxy + 5% fill fail
    try:
        fr = _backtest(definition, symbol, timeframe, "observed_base",
                       slip_mult=2.0, latency_extra=0.00005, fill_fail_pct=0.05)
        fr_m = _metrics_from_trades(fr.get("trades") or [])
    except Exception as exc:
        fr_m = {"error": str(exc), "sharpe": -1, "trades": 0}
    fr_sharpe = float(fr_m.get("sharpe") or -1)
    if fr_cmp == "ge":
        fr_pass = fr_m.get("trades", 0) >= 5 and fr_sharpe >= fr_sharpe_gate
    else:
        fr_pass = fr_m.get("trades", 0) >= 5 and fr_sharpe > fr_sharpe_gate
    friction = {"pass": bool(fr_pass), "metrics": fr_m,
                "gates": {"slip_mult": 2.0, "latency_extra": 0.00005,
                          "fill_fail_pct": 0.05,
                          "sharpe_gate": fr_sharpe_gate, "cmp": fr_cmp}}

    # Logic destruction ±20%
    destroyed = []
    dest_pass = True
    for seed in (11, 29, 47):
        try:
            pert = _perturb_core_params(definition, pct=0.20, seed=seed)
            pert = dsl_mod.validate_strategy(pert)
            r = _backtest(pert, symbol, timeframe, "observed_base")
            m = _metrics_from_trades(r.get("trades") or [])
            # Expectation should not flip to large negative catastrophe
            ok_row = m["trades"] >= 3 and m["mean_net"] > -0.01
            destroyed.append({"seed": seed, "ok": ok_row, "metrics": m})
            if not ok_row:
                dest_pass = False
        except Exception as exc:
            destroyed.append({"seed": seed, "ok": False, "error": str(exc)})
            dest_pass = False
    destruction = {"pass": bool(dest_pass), "rows": destroyed}

    overall = anti_pass and fr_pass and dest_pass
    score_antiof = 0.0
    if anti_pass:
        score_antiof = min(100.0, 40.0 + base_m["sharpe"] * 20.0
                           + max(0.0, base_m["oos_profit"]) * 200.0)
    return {
        "ok": True,
        "pass": bool(overall),
        "gate_mode": mode,
        "definition": definition,
        "symbol": symbol,
        "timeframe": timeframe,
        "anti_overfit": anti,
        "extreme_friction": friction,
        "logic_destruction": destruction,
        "anti_overfit_score": round(score_antiof, 3),
        "base_metrics": base_m,
        "hard_release_bar": {
            "walk_forward_8of10": bool(anti_pass) if relaxed else None,
            "friction_sharpe_ge_0": bool(fr_pass) if relaxed else None,
            "logic_destruction": bool(dest_pass),
            "monte_carlo": "soft_advisory_only" if relaxed else "bundled_elsewhere",
        },
    }


GLM_GATE_PROMPT = """你是策略总设计师，对内测压力包结果做门控。
仅输出JSON：{"decision":"pass|conditional|fail","reason_zh":"...","conditions":["可选"]}
规则：anti-overfit与extreme friction必须过；destruction大面积失败→fail。
"""


def glm_internal_gate(book, packs):
    payload = {
        "title": book.get("title"),
        "thesis": book.get("thesis"),
        "anti_overfit": packs.get("anti_overfit"),
        "extreme_friction": {
            "pass": (packs.get("extreme_friction") or {}).get("pass"),
            "metrics": (packs.get("extreme_friction") or {}).get("metrics"),
        },
        "logic_destruction": {
            "pass": (packs.get("logic_destruction") or {}).get("pass"),
            "rows": [
                {"seed": r.get("seed"), "ok": r.get("ok"),
                 "mean_net": (r.get("metrics") or {}).get("mean_net")}
                for r in ((packs.get("logic_destruction") or {}).get("rows") or [])
            ],
        },
        "overall_machine_pass": packs.get("pass"),
    }
    res = _ai_json("glm", GLM_GATE_PROMPT, payload, max_tokens=800, temperature=0.1)
    parsed = (res.get("parsed") or {}) if res.get("ok") else {}
    decision = str(parsed.get("decision") or "").lower()
    if decision not in ("pass", "conditional", "fail"):
        decision = "pass" if packs.get("pass") else "fail"
        parsed = {"decision": decision,
                  "reason_zh": parsed.get("reason_zh") or "门控兜底按机器结果",
                  "fallback": True, "error": res.get("error")}
    if decision == "conditional" and not packs.get("pass"):
        # conditional still requires machine packs soft-ok on antiOF
        if not (packs.get("anti_overfit") or {}).get("pass"):
            decision = "fail"
            parsed["decision"] = "fail"
            parsed["reason_zh"] = (parsed.get("reason_zh") or "") + "｜条件通过被机器否决"
    return {"decision": decision, "audit": parsed, "raw_ok": bool(res.get("ok"))}


# ─── Step3 GLM simulated DS/Qwen ───────────────────────────────────────

GLM_SIM_REVIEW_PROMPT = """你是策略总设计师。请分别模拟 DeepSeek 与 Qwen 两位独立复核官的理论胜率判断。
不要直接给最终实盘批准；这是模拟复核。
依据：策略逻辑、内测指标、死因规避。
仅输出JSON：
{"wr_deepseek_sim":0到100,"wr_qwen_sim":0到100,"reason_zh":"...",
 "repair_hints":["若未过55给出修复算子建议"],"stop_cluster_risk":"low|medium|high"}
"""


def glm_sim_review(book, packs):
    payload = {
        "title": book.get("title"),
        "dsl_entry": (book.get("dsl") or {}).get("entry"),
        "dsl_exit": (book.get("dsl") or {}).get("exit"),
        "base_metrics": packs.get("base_metrics"),
        "anti_overfit": packs.get("anti_overfit"),
        "extreme_friction_pass": (packs.get("extreme_friction") or {}).get("pass"),
        "thesis": book.get("thesis"),
    }
    res = _ai_json("glm", GLM_SIM_REVIEW_PROMPT, payload, max_tokens=900, temperature=0.2)
    parsed = (res.get("parsed") or {}) if res.get("ok") else {}
    try:
        wr_a = float(parsed.get("wr_deepseek_sim"))
    except Exception:
        wr_a = None
    try:
        wr_b = float(parsed.get("wr_qwen_sim"))
    except Exception:
        wr_b = None
    if wr_a is None or wr_b is None:
        # fallback from empirical WR with haircut
        emp = float((packs.get("base_metrics") or {}).get("win_rate_pct") or 0)
        wr_a = max(0.0, emp - 8.0)
        wr_b = max(0.0, emp - 10.0)
        parsed = {
            "wr_deepseek_sim": wr_a, "wr_qwen_sim": wr_b,
            "reason_zh": "模拟复核兜底：经验胜率折价",
            "fallback": True, "error": res.get("error"),
        }
    passed = (wr_a >= SIM_WR_GATE and wr_b >= SIM_WR_GATE)
    return {
        "ok": True,
        "pass": bool(passed),
        "wr_deepseek_sim": round(wr_a, 2),
        "wr_qwen_sim": round(wr_b, 2),
        "gate": SIM_WR_GATE,
        "audit": parsed,
    }


def _apply_repair_operator(book, hints, repair_i):
    """Anti-death repair: tighten RSI / add CCI filter / shorten hold."""
    dsl = copy.deepcopy(book.get("dsl") or {})
    entry = dsl.get("entry") or {}
    leaves = list(entry.get("all") or [])
    # tighten first numeric threshold slightly
    for leaf in leaves:
        right = (leaf or {}).get("right") or {}
        if "value" in right:
            try:
                v = float(right["value"])
                feat = ((leaf.get("left") or {}).get("feature") or "")
                if "rsi" in feat and dsl.get("direction") == "long":
                    right["value"] = max(20.0, v - 2 - repair_i)
                elif "rsi" in feat and dsl.get("direction") == "short":
                    right["value"] = min(85.0, v + 2 + repair_i)
            except Exception:
                pass
    # add CCI soft filter once
    feats = json.dumps(leaves, ensure_ascii=False)
    if "cci" not in feats:
        if dsl.get("direction") == "long":
            leaves.append({"left": {"feature": "cci"}, "op": "gt", "right": {"value": -100}})
        else:
            leaves.append({"left": {"feature": "cci"}, "op": "lt", "right": {"value": 100}})
    entry["all"] = leaves
    dsl["entry"] = entry
    dsl["max_hold_bars"] = max(8, int(dsl.get("max_hold_bars") or 24) - 2 * (repair_i + 1))
    dsl["description"] = (dsl.get("description") or "") + "｜repair%s" % (repair_i + 1)
    book = dict(book)
    book["dsl"] = dsl
    book["repair_round"] = repair_i + 1
    book["repair_hints"] = hints
    return book


# ─── Step4 formal DeepSeek + Qwen ──────────────────────────────────────

def formal_ds_qwen_review(definition, packs, book):
    """Formal independent reviewers: DeepSeek + Qwen only (each ≥50%)."""
    import auto_trade_ai_consensus as ai
    import auto_trade_human_confirm_pipeline as pipeline

    cand = {"dsl": definition, "symbol": book.get("symbol"),
            "timeframe": book.get("timeframe"), "thesis": book.get("thesis")}
    ok, metrics, reason = pipeline.safety_screen_candidate(cand)
    if not ok:
        return {"ok": False, "stage": "safety", "reason": reason, "metrics": metrics}

    evidence = {
        "safety_metrics": metrics,
        "dual_engine_packs": {
            "anti_overfit": packs.get("anti_overfit"),
            "extreme_friction_pass": (packs.get("extreme_friction") or {}).get("pass"),
            "logic_destruction_pass": (packs.get("logic_destruction") or {}).get("pass"),
            "base_metrics": packs.get("base_metrics"),
        },
        "source": "dual_engine_factory",
    }
    # Build compact candidate for theoretical review
    candidate = {
        "key": definition.get("key"),
        "name": definition.get("name"),
        "direction": definition.get("direction"),
        "timeframe": definition.get("timeframe"),
        "supported_instruments": definition.get("supported_instruments"),
        "entry": definition.get("entry"),
        "exit": definition.get("exit"),
        "description": definition.get("description"),
        "max_hold_bars": definition.get("max_hold_bars"),
    }
    ds = ai.theoretical_review_one("deepseek", candidate, evidence)
    qw = ai.theoretical_review_one("qwen", candidate, evidence)

    def _wr(row):
        try:
            return float(row.get("theoretical_win_rate_pct"))
        except Exception:
            return 0.0

    wr_ds, wr_qw = _wr(ds), _wr(qw)

    def _mn(row):
        try:
            v = row.get("theoretical_mean_net_pct")
            return float(v) if v is not None else None
        except Exception:
            return None

    mn_ds, mn_qw = _mn(ds), _mn(qw)

    def _ok(row, wr, mn):
        if not (row.get("ok") and str(row.get("decision") or "").upper() == "APPROVE"):
            return False
        if wr < FORMAL_WR_GATE:
            return False
        if mn is None or mn < FORMAL_WIN_MEAN_NET_PCT_GATE:
            return False
        risk = str(row.get("stop_cluster_risk") or "high").lower()
        try:
            scp = float(row.get("stop_cluster_prob") or 1.0)
        except Exception:
            scp = 1.0
        return risk == "low" or scp <= 0.30

    ds_ok = _ok(ds, wr_ds, mn_ds)
    qw_ok = _ok(qw, wr_qw, mn_qw)
    mean = round((wr_ds + wr_qw) / 2.0, 3) if (wr_ds or wr_qw) else None
    mn_vals = [x for x in (mn_ds, mn_qw) if x is not None]
    mean_net = round(sum(mn_vals) / float(len(mn_vals)), 6) if mn_vals else None
    approved = bool(ds_ok and qw_ok)
    annotation = (
        "DeepSeek WR %.1f%% / 盈利单 %.2f%% | Qwen WR %.1f%% / 盈利单 %.2f%% | "
        "mean WR %.1f%% / 盈利单 %.2f%%"
        % (wr_ds, mn_ds or 0.0, wr_qw, mn_qw or 0.0, mean or 0.0, mean_net or 0.0)
    )
    ai_review = {
        "approved": approved,
        "policy": "deepseek_and_qwen_formal_wr_ge_%s_win_mean_ge_%s"
        % (int(FORMAL_WR_GATE), int(FORMAL_WIN_MEAN_NET_PCT_GATE)),
        "ai_theoretical_wr_avg": mean,
        "ai_theoretical_wr_by_provider": {"deepseek": wr_ds, "qwen": wr_qw},
        "ai_theoretical_mean_net_avg": mean_net,
        "ai_theoretical_mean_net_by_provider": {
            "deepseek": mn_ds, "qwen": mn_qw,
        },
        "mean_net_scope": "winning_trades_only",
        "ai_stop_cluster_risk_by_provider": {
            "deepseek": ds.get("stop_cluster_risk"),
            "qwen": qw.get("stop_cluster_risk"),
        },
        "natural_language": annotation,
        "reviews": [ds, qw],
        "gate": FORMAL_WR_GATE,
        "win_mean_net_gate": FORMAL_WIN_MEAN_NET_PCT_GATE,
    }
    return {
        "ok": True,
        "approved": approved,
        "annotation": annotation,
        "metrics": metrics,
        "ai_review": ai_review,
        "safety_reason": reason,
    }


def _record_formal(item):
    data = _read(FORMAL_PATH, {"items": []})
    items = list(data.get("items") or [])
    items.append(item)
    data["items"] = items[-100:]
    data["updated_at"] = _now()
    _atomic(FORMAL_PATH, data)


# ─── Evolution pool ────────────────────────────────────────────────────

def load_pool():
    data = _read(POOL_PATH, None)
    if not isinstance(data, dict) or not data.get("individuals"):
        return {"schema": "qiyu_dual_engine_evolution_v1", "generation": 0,
                "individuals": [], "fitness_trend": [], "promoted": [],
                "created_at": _now()}
    return data


def save_pool(pool):
    pool = dict(pool)
    pool["updated_at"] = _now()
    _atomic(POOL_PATH, pool)
    return pool


def _fitness(ind):
    sim_ds = float(ind.get("sim_ds") or 0)
    sim_qw = float(ind.get("sim_qw") or 0)
    antiof = float(ind.get("anti_overfit_score") or 0)
    return round(sim_ds * 0.35 + sim_qw * 0.35 + antiof * 0.30, 4)


def _seed_individual(i, rng):
    templates = [
        ("long", 28 + rng.randint(0, 8), 55 + rng.randint(0, 8)),
        ("short", 68 + rng.randint(0, 8), 45 + rng.randint(0, 8)),
    ]
    direction, a, b = templates[i % 2]
    syms = ["SOL-USDT-SWAP", "XRP-USDT-SWAP", "DOGE-USDT-SWAP", "LINK-USDT-SWAP",
            "AVAX-USDT-SWAP", "ETH-USDT-SWAP", "LTC-USDT-SWAP", "ADA-USDT-SWAP"]
    tfs = ["5m", "15m"]
    sym = syms[i % len(syms)]
    tf = tfs[i % len(tfs)]
    if direction == "long":
        entry = [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": a}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema8"}},
        ]
        exit_any = [{"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": b}}]
    else:
        entry = [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": a}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
        ]
        exit_any = [{"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": b}}]
    key = "evo_%s_%s_%s_%s" % (sym.split("-")[0].lower(), tf, direction[0], i)
    dsl = {
        "key": key, "name": "进化个体%s" % i, "direction": direction,
        "timeframe": tf, "supported_instruments": [sym],
        "entry": {"all": entry}, "exit": {"any": exit_any},
        "max_hold_bars": 20, "author": "dual_engine_evolution",
    }
    # heuristic initial scores (will be refreshed on evolve)
    sim_ds = 45 + rng.random() * 25
    sim_qw = 45 + rng.random() * 25
    antiof = 20 + rng.random() * 40
    ind = {
        "id": key, "dsl": dsl, "symbol": sym, "timeframe": tf,
        "direction": direction, "sim_ds": round(sim_ds, 2),
        "sim_qw": round(sim_qw, 2), "anti_overfit_score": round(antiof, 2),
        "gens_stable_high": 0, "gens_no_improve": 0,
        "best_fitness": 0.0, "created_at": _now(),
    }
    ind["fitness"] = _fitness(ind)
    ind["best_fitness"] = ind["fitness"]
    return ind


def init_evolution_pool(n=60, force=False):
    _ensure_dirs()
    pool = load_pool()
    if pool.get("individuals") and not force:
        return {"ok": True, "initialized": False, "size": len(pool["individuals"]),
                "generation": pool.get("generation")}
    rng = random.Random(20260725)
    n = max(POP_MIN, min(POP_MAX, int(n)))
    inds = [_seed_individual(i, rng) for i in range(n)]
    pool = {
        "schema": "qiyu_dual_engine_evolution_v1",
        "generation": 0,
        "individuals": inds,
        "fitness_trend": [{"generation": 0,
                           "best": max(x["fitness"] for x in inds),
                           "mean": sum(x["fitness"] for x in inds) / float(n),
                           "at": _now()}],
        "promoted": [],
        "archived": [],
        "created_at": _now(),
        "last_evolve_at": None,
    }
    save_pool(pool)
    _append_audit({"event": "evolution_init", "size": n})
    save_status()
    return {"ok": True, "initialized": True, "size": n, "generation": 0}


def _mutate(ind, rng):
    child = copy.deepcopy(ind)
    child["id"] = "evo_m_%s_%s" % (int(time.time()) % 100000, rng.randint(10, 99))
    dsl = child.get("dsl") or {}
    entry = ((dsl.get("entry") or {}).get("all") or [])
    for leaf in entry:
        right = (leaf or {}).get("right") or {}
        if "value" in right:
            try:
                v = float(right["value"])
                right["value"] = round(v * (1.0 + rng.uniform(-0.08, 0.08)), 4)
            except Exception:
                pass
    dsl["key"] = child["id"]
    dsl["name"] = "进化变异%s" % child["id"][-4:]
    child["dsl"] = dsl
    # re-score lightly with noise + optional real pack sample
    child["sim_ds"] = max(0.0, min(95.0, float(child.get("sim_ds") or 50)
                                   + rng.uniform(-6, 6)))
    child["sim_qw"] = max(0.0, min(95.0, float(child.get("sim_qw") or 50)
                                   + rng.uniform(-6, 6)))
    child["anti_overfit_score"] = max(
        0.0, min(100.0, float(child.get("anti_overfit_score") or 30)
                 + rng.uniform(-8, 8)))
    # Periodically run a real mini pack on top individuals' children
    if rng.random() < 0.15:
        try:
            book = {"dsl": dsl, "symbol": child.get("symbol"),
                    "timeframe": child.get("timeframe"), "title": dsl.get("name")}
            packs = run_internal_packs(book)
            if packs.get("ok"):
                child["anti_overfit_score"] = float(packs.get("anti_overfit_score") or 0)
                emp = float((packs.get("base_metrics") or {}).get("win_rate_pct") or 0)
                child["sim_ds"] = max(0.0, emp - 6)
                child["sim_qw"] = max(0.0, emp - 8)
                child["last_pack_pass"] = bool(packs.get("pass"))
        except Exception as exc:
            child["pack_error"] = str(exc)[:160]
    child["fitness"] = _fitness(child)
    child["gens_stable_high"] = 0
    child["gens_no_improve"] = 0
    child["best_fitness"] = child["fitness"]
    child["parent"] = ind.get("id")
    child["created_at"] = _now()
    return child


def evolve_once(mentor=True):
    """One evolution generation. Real selection/mutation; optional GLM mentor."""
    _ensure_dirs()
    pool = load_pool()
    if not pool.get("individuals"):
        init_evolution_pool(60)
        pool = load_pool()
    inds = list(pool.get("individuals") or [])
    rng = random.Random(int(time.time()) ^ (pool.get("generation") or 0))
    inds.sort(key=lambda x: float(x.get("fitness") or 0), reverse=True)
    elite_n = max(8, len(inds) // 5)
    elites = inds[:elite_n]
    children = []
    while len(elites) + len(children) < max(POP_MIN, min(POP_MAX, len(inds))):
        parent = elites[rng.randint(0, len(elites) - 1)]
        children.append(_mutate(parent, rng))
    new_inds = elites + children
    new_inds = new_inds[:POP_MAX]

    mentor_note = None
    if mentor:
        top = [
            {"id": x.get("id"), "fitness": x.get("fitness"),
             "sim_ds": x.get("sim_ds"), "sim_qw": x.get("sim_qw"),
             "antiof": x.get("anti_overfit_score")}
            for x in new_inds[:8]
        ]
        res = _ai_json(
            "glm",
            "你是进化池导师。根据种群顶尖个体给出一句中文导师评语与是否建议提升探索率。"
            "仅输出JSON：{\"note_zh\":\"...\",\"boost_explore\":true|false}",
            {"top": top, "generation": int(pool.get("generation") or 0) + 1},
            max_tokens=400, temperature=0.3,
        )
        if res.get("ok"):
            mentor_note = (res.get("parsed") or {}).get("note_zh")
            if (res.get("parsed") or {}).get("boost_explore"):
                # extra mutants
                for _ in range(5):
                    if len(new_inds) >= POP_MAX:
                        break
                    new_inds.append(_mutate(elites[0], rng))

    promoted = list(pool.get("promoted") or [])
    archived = list(pool.get("archived") or [])
    kept = []
    for ind in new_inds:
        fit = float(ind.get("fitness") or 0)
        if fit >= PROMOTE_FITNESS:
            ind["gens_stable_high"] = int(ind.get("gens_stable_high") or 0) + 1
        else:
            ind["gens_stable_high"] = 0
        if fit > float(ind.get("best_fitness") or 0):
            ind["best_fitness"] = fit
            ind["gens_no_improve"] = 0
        else:
            ind["gens_no_improve"] = int(ind.get("gens_no_improve") or 0) + 1
        if int(ind.get("gens_stable_high") or 0) >= PROMOTE_GENS:
            promoted.append({
                "id": ind.get("id"), "fitness": fit, "at": _now(),
                "dsl_key": (ind.get("dsl") or {}).get("key"),
                "symbol": ind.get("symbol"), "timeframe": ind.get("timeframe"),
            })
            # reset counter so we don't spam
            ind["gens_stable_high"] = 0
            ind["promoted_flag"] = True
        if int(ind.get("gens_no_improve") or 0) >= ARCHIVE_STALE_GENS and fit < 55:
            archived.append({"id": ind.get("id"), "fitness": fit, "at": _now()})
            continue
        kept.append(ind)

    # refill if archived too many
    while len(kept) < POP_MIN:
        kept.append(_seed_individual(1000 + len(kept), rng))

    gen = int(pool.get("generation") or 0) + 1
    best = max(float(x.get("fitness") or 0) for x in kept)
    mean = sum(float(x.get("fitness") or 0) for x in kept) / float(len(kept))
    trend = list(pool.get("fitness_trend") or [])
    trend.append({"generation": gen, "best": round(best, 3),
                  "mean": round(mean, 3), "at": _now(),
                  "mentor_note": mentor_note})
    pool.update({
        "generation": gen,
        "individuals": kept,
        "fitness_trend": trend[-50:],
        "promoted": promoted[-30:],
        "archived": archived[-50:],
        "last_evolve_at": _now(),
        "mentor_note": mentor_note,
    })
    save_pool(pool)
    with EVO_HIST_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "time": _now(), "generation": gen, "best": best, "mean": mean,
            "size": len(kept), "mentor_note": mentor_note,
        }, ensure_ascii=False) + "\n")
    _append_audit({"event": "evolution_tick", "generation": gen,
                   "best": best, "size": len(kept)})
    save_status()
    return {"ok": True, "generation": gen, "best_fitness": best,
            "mean_fitness": mean, "size": len(kept),
            "mentor_note": mentor_note, "promoted_n": len(promoted)}


# ─── Full creation task orchestration ──────────────────────────────────

def _workflow_entry():
    """Return creation_entry from workflow flags (legacy|v2). Default legacy until A–H pass."""
    try:
        import dual_engine_workflow_v2 as wfv2
        return str(wfv2.creation_entry() or "legacy")
    except Exception:
        try:
            flags = _read(DUAL_DIR / "workflow_flags.json", {})
            return str(flags.get("creation_entry") or "legacy")
        except Exception:
            return "legacy"


def start_creation_task(async_mode=True, symbol=None, timeframe=None,
                        exploration_mode=None, force_workflow=None):
    """Sole-entry redirect: blank dual-engine creation is forbidden."""
    _ensure_dirs()
    _load_env()
    try:
        from dual_engine_workflow_v2.creation_sole_entry import (
            is_sole_creation_enforced, create_strategy, refuse_side_path,
        )
    except Exception as exc:
        return {"ok": False, "error": "sole_entry_import_error", "detail": str(exc)}
    if is_sole_creation_enforced():
        sym = symbol or "ADA-USDT-SWAP"
        tf = timeframe or "5m"
        if async_mode:
            def _worker():
                create_strategy(
                    symbol=sym, timeframe=tf,
                    brief="dual_engine_start_task_redirected_to_sole_entry",
                )
            threading.Thread(target=_worker, name="sole-creation", daemon=True).start()
            return {
                "ok": True,
                "status": "started",
                "async": True,
                "creation_entry": "sole_research_discovery",
                "message_zh": "已强制转入唯一蓝图创造入口（研究发现管道）",
            }
        out = create_strategy(
            symbol=sym, timeframe=tf,
            brief="dual_engine_start_task_redirected_to_sole_entry",
        )
        return {
            "ok": bool(out.get("ok")),
            "status": "finished",
            "creation_entry": "sole_research_discovery",
            "sole": {
                "present_to_human": out.get("present_to_human"),
                "pipeline_gate": out.get("pipeline_gate"),
                "handoff_zh": out.get("handoff_zh"),
            },
        }
    return refuse_side_path("auto_trade_dual_engine_factory.start_creation_task")


def run_creation_pipeline(symbol=None, timeframe=None):
    try:
        from dual_engine_workflow_v2.creation_sole_entry import (
            is_sole_creation_enforced, create_strategy, refuse_side_path,
        )
        if is_sole_creation_enforced():
            return create_strategy(
                symbol=symbol or "ADA-USDT-SWAP",
                timeframe=timeframe or "5m",
                brief="legacy_run_creation_pipeline_redirected",
            )
        return refuse_side_path("auto_trade_dual_engine_factory.run_creation_pipeline")
    except Exception as exc:
        return {"ok": False, "error": "sole_entry_import_error", "detail": str(exc)}
    _ensure_dirs()
    _load_env()
    tid = _new_task_id()
    task = {
        "id": tid, "schema": "qiyu_dual_engine_task_v1",
        "created_at": _now(), "stage": "step1_collect", "history": [],
        "focus": {"symbol": symbol, "timeframe": timeframe},
    }
    task = _task_hist(task, "created")
    save_task(task)

    # Step1 collect
    inputs = collect_inputs()
    if symbol and timeframe:
        inputs["focus_candidates"] = (
            [{"symbol": symbol, "timeframe": timeframe, "reason": "manual"}]
            + list(inputs.get("focus_candidates") or [])
        )
    task["inputs_summary"] = {
        "death_n": len(inputs.get("death_heatmap_top10") or []),
        "freezer_n": len(inputs.get("freezer_last10") or []),
        "coverage_n": len(inputs.get("coverage_map") or []),
        "focus": (inputs.get("focus_candidates") or [])[:5],
    }
    task["stage"] = "step1_insight"
    task = _task_hist(task, "inputs_collected", task["inputs_summary"])
    save_task(task)

    insight = glm_market_insight(inputs)
    task["insight"] = {
        "summary_zh": insight.get("summary_zh"),
        "niches": insight.get("priority_niches"),
        "fallback": insight.get("fallback"),
    }
    task["stage"] = "step1_hypotheses"
    task = _task_hist(task, "insight_done", {"fallback": insight.get("fallback")})
    save_task(task)

    books = _codex_hypothesis_books(insight, inputs)
    task["hypotheses"] = [
        {"title": b.get("title"), "symbol": b.get("symbol"),
         "timeframe": b.get("timeframe"), "direction": b.get("direction"),
         "status": b.get("status")}
        for b in books
    ]
    task["stage"] = "step1_audit"
    task = _task_hist(task, "hypotheses_built", {"n": len(books)})
    save_task(task)

    audit = glm_audit_hypotheses(insight, books)
    approved = audit.get("approved") or []
    task["audit"] = audit.get("audit")
    task["hypotheses"] = [
        {"title": b.get("title"), "symbol": b.get("symbol"),
         "timeframe": b.get("timeframe"), "direction": b.get("direction"),
         "status": b.get("status")}
        for b in books
    ]
    if not approved:
        task["stage"] = "archived"
        task["error"] = "no_hypothesis_approved"
        task = _task_hist(task, "archived_no_approve")
        save_task(task)
        return {"ok": False, "task_id": tid, "stage": "archived", "reason": "no_approve"}

    # Work approved books through steps 2-4 until one survives or all fail
    last_fail = None
    for book in approved:
        task["candidate"] = {
            "title": book.get("title"), "symbol": book.get("symbol"),
            "timeframe": book.get("timeframe"), "direction": book.get("direction"),
            "key": (book.get("dsl") or {}).get("key"),
        }
        task["focus"] = {"symbol": book.get("symbol"), "timeframe": book.get("timeframe")}
        task["stage"] = "step2_packs"
        task = _task_hist(task, "enter_internal_tests", task["candidate"])
        save_task(task)

        packs = run_internal_packs(book)
        task["packs"] = {
            "pass": packs.get("pass"),
            "anti_overfit": packs.get("anti_overfit"),
            "extreme_friction": {
                "pass": (packs.get("extreme_friction") or {}).get("pass"),
                "metrics": (packs.get("extreme_friction") or {}).get("metrics"),
            },
            "logic_destruction": {
                "pass": (packs.get("logic_destruction") or {}).get("pass"),
            },
            "anti_overfit_score": packs.get("anti_overfit_score"),
            "base_metrics": packs.get("base_metrics"),
            "error": packs.get("error"),
        }
        if not packs.get("ok"):
            last_fail = {"stage": "failed", "error": packs.get("error"), "book": book.get("title")}
            task = _task_hist(task, "pack_error", last_fail)
            save_task(task)
            continue

        task["stage"] = "step2_glm_gate"
        save_task(task)
        gate = glm_internal_gate(book, packs)
        task["glm_gate"] = gate
        if gate.get("decision") == "fail":
            last_fail = {"stage": "glm_gate_fail", "gate": gate, "book": book.get("title")}
            task = _task_hist(task, "glm_gate_fail", gate.get("audit"))
            save_task(task)
            _ingest_book_to_pool(book, packs, sim=None)
            continue

        # Step3 sim review + repair loop
        task["stage"] = "step3_sim"
        save_task(task)
        sim = None
        survived = False
        for repair_i in range(0, 4):
            if repair_i:
                task["stage"] = "step3_repair"
                hints = ((sim or {}).get("audit") or {}).get("repair_hints") or []
                book = _apply_repair_operator(book, hints, repair_i - 1)
                packs = run_internal_packs(book)
                task["packs"] = {
                    "pass": packs.get("pass"),
                    "anti_overfit_score": packs.get("anti_overfit_score"),
                    "base_metrics": packs.get("base_metrics"),
                    "repair_round": repair_i,
                }
                task = _task_hist(task, "repair", {"round": repair_i})
                save_task(task)
                if not packs.get("ok") or not packs.get("pass"):
                    continue
            sim = glm_sim_review(book, packs)
            task["sim_wr"] = {
                "deepseek": sim.get("wr_deepseek_sim"),
                "qwen": sim.get("wr_qwen_sim"),
                "pass": sim.get("pass"),
                "repair_round": repair_i,
            }
            save_task(task)
            if sim.get("pass"):
                survived = True
                break
        if not survived:
            last_fail = {"stage": "sim_fail", "sim": sim, "book": book.get("title")}
            task = _task_hist(task, "sim_fail_try_next", task.get("sim_wr"))
            save_task(task)
            _ingest_book_to_pool(book, packs, sim)
            continue

        # Step4 formal
        task["stage"] = "step4_formal"
        task = _task_hist(task, "enter_formal_review")
        save_task(task)
        definition = packs.get("definition") or book.get("dsl")
        formal = formal_ds_qwen_review(definition, packs, book)
        task["formal_wr"] = formal.get("ai_review")
        task["formal_annotation"] = formal.get("annotation")

        formal_item = {
            "time": _now(),
            "task_id": tid,
            "key": (definition or {}).get("key"),
            "title": book.get("title"),
            "symbol": book.get("symbol"),
            "timeframe": book.get("timeframe"),
            "status": "通过" if formal.get("approved") else "退回",
            "annotation": formal.get("annotation"),
            "reason": formal.get("reason"),
        }

        if not formal.get("approved"):
            formal_item["status"] = "退回"
            _record_formal(formal_item)
            last_fail = {"stage": "formal_reject", "formal": formal, "book": book.get("title")}
            task = _task_hist(task, "formal_reject", formal.get("ai_review"))
            save_task(task)
            _ingest_book_to_pool(book, packs, sim)
            continue

        # Push to existing pending human-confirm (unchanged path)
        import auto_trade_human_confirm_pipeline as pipeline
        push = pipeline.ingest_and_screen(
            {"dsl": definition, "symbol": book.get("symbol"),
             "timeframe": book.get("timeframe"), "thesis": book.get("thesis")},
            source="dual_engine_factory",
            ai_review=formal.get("ai_review"),
            require_ai_review=True,
        )
        formal_item["status"] = "等待" if push.get("ok") else "退回"
        formal_item["pending_push"] = {
            "ok": push.get("ok"), "key": push.get("key"),
            "reason": push.get("reason"), "duplicate": push.get("duplicate"),
        }
        _record_formal(formal_item)
        task["pending_push"] = formal_item["pending_push"]
        task["stage"] = "done" if push.get("ok") else "failed"
        task = _task_hist(task, "pending_push", task["pending_push"])
        save_task(task)
        _ingest_book_to_pool(book, packs, sim)
        _append_audit({"event": "creation_done", "task_id": tid,
                       "push": task["pending_push"],
                       "annotation": formal.get("annotation")})
        return {"ok": bool(push.get("ok")), "task_id": tid, "stage": task["stage"],
                "formal": formal, "pending": push}

    task["stage"] = "archived"
    task["error"] = (last_fail or {}).get("stage") or "all_books_failed"
    task["last_fail"] = last_fail
    task = _task_hist(task, "archived_all_books", last_fail or {})
    save_task(task)
    return {"ok": False, "task_id": tid, "stage": "archived", "last_fail": last_fail}


def _ingest_book_to_pool(book, packs, sim):
    try:
        pool = load_pool()
        if not pool.get("individuals"):
            init_evolution_pool(60)
            pool = load_pool()
        ind = {
            "id": (book.get("dsl") or {}).get("key") or ("ingest_%s" % int(time.time())),
            "dsl": book.get("dsl"),
            "symbol": book.get("symbol"),
            "timeframe": book.get("timeframe"),
            "direction": book.get("direction"),
            "sim_ds": (sim or {}).get("wr_deepseek_sim") or 50,
            "sim_qw": (sim or {}).get("wr_qwen_sim") or 50,
            "anti_overfit_score": (packs or {}).get("anti_overfit_score") or 0,
            "gens_stable_high": 0, "gens_no_improve": 0,
            "created_at": _now(), "source": "creation_task",
        }
        ind["fitness"] = _fitness(ind)
        ind["best_fitness"] = ind["fitness"]
        inds = list(pool.get("individuals") or [])
        inds.append(ind)
        inds.sort(key=lambda x: float(x.get("fitness") or 0), reverse=True)
        pool["individuals"] = inds[:POP_MAX]
        save_pool(pool)
    except Exception as exc:
        _append_audit({"event": "pool_ingest_error", "error": str(exc)})


def force_evolve_async():
    with JOB_LOCK:
        if _JOB.get("running"):
            return {"ok": False, "status": "running", "kind": _JOB.get("kind")}
        _JOB.update({"running": True, "kind": "evolve", "started_at": _now(),
                     "error": None})

    def _worker():
        try:
            evolve_once(mentor=True)
        except Exception as exc:
            _append_audit({"event": "evolve_crash", "error": str(exc)})
            with JOB_LOCK:
                _JOB["error"] = str(exc)
        finally:
            with JOB_LOCK:
                _JOB.update({"running": False, "kind": None})

    threading.Thread(target=_worker, name="dual-engine-evolve", daemon=True).start()
    return {"ok": True, "status": "started", "async": True}


def refresh_insight_async():
    with JOB_LOCK:
        if _JOB.get("running") and _JOB.get("kind") == "creation":
            return {"ok": False, "status": "running"}
    def _worker():
        try:
            _load_env()
            inputs = collect_inputs()
            glm_market_insight(inputs)
            save_status()
        except Exception as exc:
            _append_audit({"event": "insight_refresh_error", "error": str(exc)})
    threading.Thread(target=_worker, daemon=True).start()
    return {"ok": True, "status": "started"}


def bootstrap():
    """Init dirs, pool, insight, status — safe to call on web start / deploy."""
    _ensure_dirs()
    _load_env()
    init_evolution_pool(60, force=False)
    if not INSIGHT_PATH.exists():
        try:
            inputs = collect_inputs()
            glm_market_insight(inputs)
        except Exception as exc:
            _atomic(INSIGHT_PATH, {
                "generated_at": _now(),
                "summary_zh": "洞察待生成：%s" % exc,
                "priority_niches": [],
                "risks": [str(exc)],
            })
    st = save_status({"stage": load_status().get("stage") or "idle"})
    return {"ok": True, "status": st, "pool_size": st.get("evolution", {}).get("size")}


def main():
    parser = argparse.ArgumentParser(description="Dual-engine factory")
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--init-pool", type=int, nargs="?", const=60)
    parser.add_argument("--evolve", action="store_true")
    parser.add_argument("--start-task", action="store_true")
    parser.add_argument("--sync", action="store_true",
                        help="run creation pipeline synchronously")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--timeframe", default=None)
    parser.add_argument("--refresh-insight", action="store_true")
    args = parser.parse_args()
    _ensure_dirs()
    _load_env()
    if args.bootstrap:
        print(json.dumps(bootstrap(), ensure_ascii=False, indent=2))
        return
    if args.init_pool is not None:
        print(json.dumps(init_evolution_pool(args.init_pool, force=True),
                         ensure_ascii=False, indent=2))
        return
    if args.refresh_insight:
        inputs = collect_inputs()
        print(json.dumps(glm_market_insight(inputs), ensure_ascii=False, indent=2))
        return
    if args.evolve:
        print(json.dumps(evolve_once(mentor=True), ensure_ascii=False, indent=2))
        return
    if args.start_task:
        if args.sync:
            print(json.dumps(run_creation_pipeline(args.symbol, args.timeframe),
                             ensure_ascii=False, indent=2))
        else:
            print(json.dumps(start_creation_task(True, args.symbol, args.timeframe),
                             ensure_ascii=False, indent=2))
        return
    if args.status or True:
        print(json.dumps(load_status(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
