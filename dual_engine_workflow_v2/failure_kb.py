# -*- coding: utf-8 -*-
"""Failure knowledgebase — strategy_failure_record.json + mandatory pre-read."""
from __future__ import print_function

import copy
import json
from pathlib import Path

from .config import _now, _atomic, _read
from .step_a_config import (
    FAILURE_RECORD_DIR,
    FAILURE_KB_PATH,
    STEP_A_SCHEMA,
    STEP_A_CODE_VERSION,
    ensure_step_a_dirs,
)


def build_failure_record(*, task_id, mechanism_spec, stage, failed_tests,
                         failure_reason, is_engineering, is_cost, is_data,
                         is_mechanism_absent, mechanism_drift, repair_count,
                         final_verdict, reusable_lessons, blocked_paths,
                         counterexamples, exploration_mode=None, symbol=None,
                         timeframe=None, gate_results=None):
    spec = mechanism_spec or {}
    return {
        "schema": STEP_A_SCHEMA,
        "artifact": "strategy_failure_record",
        "code_version": STEP_A_CODE_VERSION,
        "created_at": _now(),
        "task_id": task_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "exploration_mode": exploration_mode,
        "original_mechanism": {
            "mechanism_id": spec.get("mechanism_id"),
            "mechanism_name": spec.get("mechanism_name"),
            "mechanism_family": spec.get("mechanism_family"),
            "market_inefficiency": spec.get("market_inefficiency"),
            "counterparty_source": spec.get("counterparty_source"),
            "entry_logic": spec.get("entry_logic"),
            "non_negotiable_rules": spec.get("non_negotiable_rules"),
        },
        "failure_stage": stage,
        "failed_tests": failed_tests if isinstance(failed_tests, list) else [failed_tests],
        "failure_reason": failure_reason,
        "is_engineering_issue": bool(is_engineering),
        "is_cost_issue": bool(is_cost),
        "is_data_issue": bool(is_data),
        "is_mechanism_absent": bool(is_mechanism_absent),
        "mechanism_drift_occurred": bool(mechanism_drift),
        "repair_count": int(repair_count or 0),
        "final_verdict": final_verdict,
        "reusable_lessons": reusable_lessons or [],
        "blocked_paths": blocked_paths or [],
        "counterexamples_for_future_ai": counterexamples or [],
        "gate_results_summary": gate_results or {},
    }


def save_failure_record(record):
    ensure_step_a_dirs()
    from . import step_a_config as sc
    tid = record.get("task_id") or "unknown"
    path = Path(sc.FAILURE_RECORD_DIR) / ("%s_strategy_failure_record.json" % tid)
    _atomic(path, record)
    _upsert_kb(record)
    return str(path)


def _upsert_kb(record):
    ensure_step_a_dirs()
    from . import step_a_config as sc
    kb_path = Path(sc.FAILURE_KB_PATH)
    kb = _read(kb_path, {
        "schema": STEP_A_SCHEMA,
        "artifact": "failure_knowledgebase",
        "updated_at": None,
        "blocked_paths": [],
        "blocked_families": [],
        "lessons": [],
        "records": [],
        "counterexamples": [],
    })
    kb.setdefault("blocked_paths", [])
    kb.setdefault("blocked_families", [])
    kb.setdefault("lessons", [])
    kb.setdefault("records", [])
    kb.setdefault("counterexamples", [])

    fam = ((record.get("original_mechanism") or {}).get("mechanism_family") or "").strip()
    for p in (record.get("blocked_paths") or []):
        if p and p not in kb["blocked_paths"]:
            kb["blocked_paths"].append(p)
    if record.get("is_mechanism_absent") and fam and fam not in kb["blocked_families"]:
        kb["blocked_families"].append(fam)
    for lesson in (record.get("reusable_lessons") or []):
        if lesson and lesson not in kb["lessons"]:
            kb["lessons"].append(lesson)
    for cx in (record.get("counterexamples_for_future_ai") or []):
        if cx and cx not in kb["counterexamples"]:
            kb["counterexamples"].append(cx)

    kb["records"].append({
        "task_id": record.get("task_id"),
        "stage": record.get("failure_stage"),
        "family": fam,
        "verdict": record.get("final_verdict"),
        "at": record.get("created_at"),
        "path": str(Path(sc.FAILURE_RECORD_DIR) / ("%s_strategy_failure_record.json" % record.get("task_id"))),
    })
    kb["records"] = kb["records"][-200:]
    kb["lessons"] = kb["lessons"][-100:]
    kb["counterexamples"] = kb["counterexamples"][-100:]
    kb["blocked_paths"] = kb["blocked_paths"][-200:]
    kb["updated_at"] = _now()
    _atomic(kb_path, kb)
    return kb


def load_failure_kb():
    ensure_step_a_dirs()
    from . import step_a_config as sc
    return _read(Path(sc.FAILURE_KB_PATH), {
        "schema": STEP_A_SCHEMA,
        "artifact": "failure_knowledgebase",
        "blocked_paths": [],
        "blocked_families": [],
        "lessons": [],
        "records": [],
        "counterexamples": [],
        "updated_at": None,
    })


def kb_context_for_ai(max_lessons=12, max_cx=8, max_blocked=20):
    """Mandatory context every GLM/Codex/attacker must receive before creation."""
    kb = load_failure_kb()
    return {
        "must_read": True,
        "updated_at": kb.get("updated_at"),
        "blocked_paths": list(kb.get("blocked_paths") or [])[:max_blocked],
        "blocked_families": list(kb.get("blocked_families") or [])[:max_blocked],
        "reusable_lessons": list(kb.get("lessons") or [])[:max_lessons],
        "counterexamples": list(kb.get("counterexamples") or [])[:max_cx],
        "recent_failures": list(kb.get("records") or [])[-10:],
        "instruction": (
            "You MUST respect blocked_paths and blocked_families. "
            "Do not re-run identical failed parameter paths. "
            "Do not rename the same dead mechanism. "
            "Mode4 reverse research may analyze failures but must propose a NEW independent hypothesis."
        ),
    }


def path_is_blocked(path_key, family=None):
    kb = load_failure_kb()
    if path_key and path_key in (kb.get("blocked_paths") or []):
        return True, "blocked_path"
    if family and family in (kb.get("blocked_families") or []):
        return True, "blocked_family"
    return False, None


def kb_status_payload():
    from . import step_a_config as sc
    kb = load_failure_kb()
    records = list(Path(sc.FAILURE_RECORD_DIR).glob("*_strategy_failure_record.json")) if Path(sc.FAILURE_RECORD_DIR).exists() else []
    return {
        "schema": STEP_A_SCHEMA,
        "kb_path": str(sc.FAILURE_KB_PATH),
        "updated_at": kb.get("updated_at"),
        "record_file_count": len(records),
        "blocked_paths_count": len(kb.get("blocked_paths") or []),
        "blocked_families_count": len(kb.get("blocked_families") or []),
        "lessons_count": len(kb.get("lessons") or []),
        "counterexamples_count": len(kb.get("counterexamples") or []),
        "indexed_records": len(kb.get("records") or []),
        "mandatory_preread_wired": True,
        "sample_blocked_families": list(kb.get("blocked_families") or [])[:10],
        "sample_lessons": list(kb.get("lessons") or [])[:5],
    }
