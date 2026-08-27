# -*- coding: utf-8 -*-
"""Typed creation-case store. Write-only memory; never a gate or direction ban.

Persists one qiyu_creation_case_v1 per job_id so parallel workers can overwrite
idempotently. Research direction is archived and must not become a cluster key.
"""
from __future__ import print_function

import json
import os
from datetime import datetime
from pathlib import Path

from .process_safe_state import atomic_write_json, process_lock
from .quality_gate import IS_NUMERIC_DIMENSIONS, STRUCTURE_CHECKS


SCHEMA = "qiyu_creation_case_v1"
NEAR_MISS_MIN_PASSED = 8
OOS_RULES = frozenset({
    "oos_sharpe_below_threshold", "oos_zero_trades", "oos_backtest_missing",
})
IS_NUMERIC_RULE_IDS = tuple(item[0] for item in IS_NUMERIC_DIMENSIONS)
STRUCTURE_RULE_IDS = tuple(item[0] for item in STRUCTURE_CHECKS)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def store_dir():
    path = _root() / "auto_trade" / "dual_engine" / "creation_learning"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cases_dir():
    path = store_dir() / "cases"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    return value if isinstance(value, list) else []


def feature_fingerprint(features):
    """Coarse AST groups. Not a research-direction key."""
    from .entry_structure_literacy import BAND, BREAK, OSC, SQUEEZE, TREND_DIR

    feats = set(features or [])
    groups = []
    if feats & TREND_DIR:
        groups.append("trend")
    if feats & BREAK:
        groups.append("break")
    if feats & OSC:
        groups.append("osc")
    if feats & BAND:
        groups.append("band")
    if feats & SQUEEZE:
        groups.append("squeeze")
    if "volume_z" in feats or "absorption_proxy" in feats:
        groups.append("vol")
    return "+".join(groups) if groups else "none"


def exit_geometry_bin(stop_pct):
    try:
        stop = float(stop_pct)
    except (TypeError, ValueError):
        return "unknown"
    if stop < 0.008:
        return "tight"
    if stop < 0.02:
        return "mid"
    return "wide"


def _unique_rules(values):
    out = []
    seen = set()
    for item in values or []:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def attempts_of(result):
    result = _as_dict(result)
    rows = result.get("attempts")
    if isinstance(rows, list) and rows:
        return rows
    cog = _as_dict(result.get("cognitive"))
    rows = cog.get("attempts")
    if isinstance(rows, list) and rows:
        return rows
    return []


def last_candidate(result):
    attempts = attempts_of(result)
    for row in reversed(attempts):
        cand = _as_dict(_as_dict(row).get("candidate"))
        if cand:
            return cand
    handoff = _as_dict(result.get("handoff") or result.get("blueprint"))
    recipe = _as_dict(handoff.get("recipe"))
    if recipe.get("entry_ast") or recipe.get("title_zh"):
        return recipe
    return _as_dict(handoff.get("candidate"))


def last_evaluation(result):
    attempts = attempts_of(result)
    if attempts:
        ev = _as_dict(_as_dict(attempts[-1]).get("evaluation"))
        if ev:
            return ev
    handoff = _as_dict(result.get("handoff") or result.get("blueprint"))
    admission = _as_dict(handoff.get("quality_gate_admission"))
    gate = admission.get("quality_gate") or handoff.get("quality_gate")
    return {
        "quality_gate": gate,
        "failed_rules": admission.get("failed_rules"),
    }


def failed_rules_of(result):
    ev = last_evaluation(result)
    gate = _as_dict(ev.get("quality_gate"))
    rules = list(gate.get("failed_rules") or gate.get("reasons") or [])
    if not rules:
        rules = list(ev.get("failed_rules") or [])
    if not rules:
        tele = _as_dict(_as_dict(result).get("creation_quality_telemetry"))
        traj = _as_list(tele.get("metric_trajectory_unranked"))
        if traj:
            rules = list(_as_dict(traj[-1]).get("failed_rules") or [])
    if not rules:
        fe = _as_dict(_as_dict(result).get("failure_evidence"))
        rules = list(fe.get("failed_rules") or [])
    return _unique_rules(rules)


def _structure_literacy(result):
    ev = last_evaluation(result)
    gate = _as_dict(ev.get("quality_gate"))
    lit = _as_dict(gate.get("structure_literacy"))
    if lit.get("family"):
        return lit
    fe = _as_dict(_as_dict(result).get("failure_evidence"))
    if fe.get("structure_family"):
        return {
            "family": fe.get("structure_family"),
            "features": list(fe.get("features") or []),
        }
    cand = last_candidate(result)
    ast = cand.get("entry_ast")
    title = cand.get("title_zh") or cand.get("name")
    direction = cand.get("direction")
    if ast or title:
        try:
            from .entry_structure_literacy import assess
            return assess(ast, direction=direction, title_zh=title) or {}
        except Exception:
            return {}
    return {}


def _metrics_of(result):
    ev = last_evaluation(result)
    gate = _as_dict(ev.get("quality_gate"))
    metrics = dict(gate.get("metrics") or {})
    is_gate = _as_dict(gate.get("is_gate"))
    for key, value in dict(is_gate.get("metrics") or {}).items():
        metrics.setdefault(key, value)
    tele = _as_dict(_as_dict(result).get("creation_quality_telemetry"))
    traj = _as_list(tele.get("metric_trajectory_unranked"))
    if traj:
        last_m = dict(_as_dict(traj[-1]).get("metrics") or {})
        for key, value in last_m.items():
            metrics.setdefault(key, value)
    return metrics


def near_miss_payload(failed_rules, metrics=None):
    """IS numeric near-miss only. OOS numbers never enter this object."""
    failed = set(_unique_rules(failed_rules))
    numeric_failed = [rule for rule in IS_NUMERIC_RULE_IDS if rule in failed]
    passed_n = len(IS_NUMERIC_RULE_IDS) - len(numeric_failed)
    is_near = passed_n >= NEAR_MISS_MIN_PASSED and 1 <= len(numeric_failed) <= 2
    gaps = []
    metrics = metrics or {}
    for rule_id, metric_key, label_zh in IS_NUMERIC_DIMENSIONS:
        if rule_id not in numeric_failed:
            continue
        gaps.append({
            "rule": rule_id,
            "metric": metric_key,
            "label_zh": label_zh,
            "actual": metrics.get(metric_key),
        })
    return {
        "ok": bool(is_near),
        "is_numeric_passed": int(passed_n),
        "is_numeric_total": len(IS_NUMERIC_RULE_IDS),
        "numeric_failed": numeric_failed,
        "gaps": gaps,
        "note_zh": (
            "近误只描述样本内数字缺口顺序，不能放行，也不含盲OOS数字。"
            if is_near else
            "未构成近误（需样本内10项中至少8项已过且缺口集中在1–2维）。"
        ),
    }


def retry_trajectory(result):
    sets = []
    for row in attempts_of(result):
        ev = _as_dict(_as_dict(row).get("evaluation"))
        gate = _as_dict(ev.get("quality_gate"))
        rules = [
            item for item in _unique_rules(
                gate.get("failed_rules") or gate.get("reasons") or ev.get("failed_rules")
            )
            if item not in OOS_RULES
        ]
        sets.append(frozenset(rules))
    if not sets:
        last = frozenset(
            item for item in failed_rules_of(result) if item not in OOS_RULES
        )
        sets = [last] if last else []
    frozen = [frozenset(item) for item in sets if item]
    same = bool(frozen) and all(item == frozen[0] for item in frozen)
    rotating = bool(frozen) and not same and len(frozen) >= 2
    last = frozen[-1] if frozen else frozenset()
    return {
        "attempt_n": len(attempts_of(result)) or len(sets),
        "same_rules_across_retries": same,
        "rules_rotating": rotating,
        "last_rules": sorted(last),
    }


def _strategy_key(job, result):
    job = _as_dict(job)
    result = _as_dict(result)
    for src in (
        job.get("strategy_key"), job.get("key"),
        result.get("strategy_key"),
    ):
        text = str(src or "").strip()
        if text:
            return text
    handoff = _as_dict(result.get("handoff") or result.get("blueprint"))
    admission = _as_dict(handoff.get("quality_gate_admission"))
    metrics = _as_dict(admission.get("metrics"))
    for src in (
        metrics.get("key"), handoff.get("strategy_key"),
        admission.get("recipe_identity_hash"),
        result.get("instruction_id"), job.get("job_id"),
    ):
        text = str(src or "").strip()
        if text:
            if not text.startswith("kimi_") and len(text) >= 8:
                return "kimi_%s" % text[:24]
            return text
    return None


def _hitch_class(result):
    cand = last_candidate(result)
    handoff = _as_dict(_as_dict(result).get("handoff") or _as_dict(result).get("blueprint"))
    for src in (cand.get("hitch_class"), handoff.get("hitch_class")):
        if src in ("方向型", "赔率型"):
            return src
    return None


def extract_case(job, result):
    job = _as_dict(job)
    result = _as_dict(result)
    cand = last_candidate(result)
    lit = _structure_literacy(result)
    features = list(lit.get("features") or [])
    if not features and cand.get("entry_ast"):
        try:
            from .entry_structure_literacy import assess
            got = assess(
                cand.get("entry_ast"),
                direction=cand.get("direction") or job.get("trade_direction"),
                title_zh=cand.get("title_zh"),
            )
            features = list((got or {}).get("features") or [])
            if not lit.get("family"):
                lit = got or lit
        except Exception:
            pass
    failed = failed_rules_of(result)
    metrics = _metrics_of(result)
    traj = retry_trajectory(result)
    outcome = str(result.get("outcome") or job.get("outcome") or "")
    family = lit.get("family") or None
    fp = feature_fingerprint(features)
    return {
        "schema": SCHEMA,
        "at": _now(),
        "job_id": job.get("job_id") or result.get("instruction_id") or result.get("mission_id"),
        "strategy_key": _strategy_key(job, result),
        "symbol": job.get("symbol") or result.get("symbol") or cand.get("symbol"),
        "timeframe": job.get("timeframe") or result.get("timeframe") or cand.get("timeframe"),
        "side": job.get("trade_direction") or result.get("direction") or cand.get("direction"),
        "research_direction": job.get("research_direction") or result.get("brief"),
        "outcome": outcome,
        "structure_family": family,
        "hitch_class": _hitch_class(result),
        "features": features[:24],
        "feature_fingerprint": fp,
        "failed_rules": failed,
        "near_miss": near_miss_payload(failed, metrics),
        "retry_trajectory": traj,
        "exit_geometry_bin": exit_geometry_bin(
            cand.get("protective_stop_pct")
            or (_as_dict(cand.get("exit_plan")).get("protective_stop_pct"))
        ),
        "title_zh": cand.get("title_zh") or cand.get("name"),
        "not_a_gate": True,
        "research_direction_not_a_cluster_key": True,
    }


def kimi_receipt_evidence(result):
    """Small fields for completed/*.json. Never dumps attempts or OOS numbers."""
    result = _as_dict(result)
    if not result:
        return {}
    case = extract_case({}, result)
    failed = list(case.get("failed_rules") or [])
    family = case.get("structure_family")
    near = case.get("near_miss") or {}
    if not failed and not family and not near.get("ok"):
        if not case.get("feature_fingerprint") or case.get("feature_fingerprint") == "none":
            if not case.get("retry_trajectory", {}).get("last_rules"):
                return {}
    payload = {
        "failed_rules": failed,
        "structure_family": family,
        "feature_fingerprint": case.get("feature_fingerprint"),
        "near_miss": {
            "ok": bool(near.get("ok")),
            "is_numeric_passed": near.get("is_numeric_passed"),
            "numeric_failed": list(near.get("numeric_failed") or []),
            "note_zh": near.get("note_zh"),
        },
        "retry_trajectory": case.get("retry_trajectory"),
        "exit_geometry_bin": case.get("exit_geometry_bin"),
        "not_a_gate": True,
        "note_zh": "共同因子证据。禁止把研究方向或做多做空本身写成禁令。",
    }
    return {
        key: value for key, value in payload.items()
        if value not in (None, {}, [])
    }


def case_path(job_id):
    jid = str(job_id or "").strip()
    if not jid:
        raise ValueError("job_id required")
    return cases_dir() / ("%s.json" % jid)


def save_case(case):
    case = dict(case or {})
    jid = str(case.get("job_id") or "").strip()
    if not jid:
        return None
    case["schema"] = SCHEMA
    case.setdefault("at", _now())
    path = case_path(jid)
    with process_lock("creation_case_store"):
        atomic_write_json(path, case)
    return path


def load_cases():
    rows = []
    folder = cases_dir()
    for path in sorted(folder.glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(row, dict) and row.get("job_id"):
            rows.append(row)
    return rows


def record_creation_outcome(job, result):
    """Persist the case and refresh the advisory pack. Never raises to callers."""
    try:
        from .creation_learning_pack import learning_enabled
        if not learning_enabled():
            return {"ok": True, "skipped": "QIYU_CREATION_LEARNING=0"}
        case = extract_case(job, result)
        if not case.get("job_id"):
            return {"ok": False, "error": "missing_job_id"}
        has_signal = bool(
            case.get("failed_rules")
            or case.get("structure_family")
            or str(case.get("outcome") or "") in ("candidate_ready", "review_submitted")
        )
        if not has_signal:
            return {"ok": True, "skipped": "no_learning_signal", "job_id": case.get("job_id")}
        save_case(case)
        pack = None
        try:
            from .creation_learning_pack import refresh_pack
            pack = refresh_pack()
        except Exception:
            pack = None
        return {
            "ok": True,
            "job_id": case.get("job_id"),
            "structure_family": case.get("structure_family"),
            "pack_version": (pack or {}).get("version") if isinstance(pack, dict) else None,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:240]}
