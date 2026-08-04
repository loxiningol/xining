# -*- coding: utf-8 -*-
"""Strategy lifecycle governor: score, downgrade, eliminate, rotate, evolve.

Binds to presence probes and research candidates.  Never relaxes stop-loss,
exit management, or full passed_all human approval.  Shadow→C-grade probe
auto-promotion is explicitly allowed (conditional_frequency_probe only).
"""
from __future__ import print_function

from datetime import datetime, timedelta
from pathlib import Path
import json
import os
import tempfile
import time

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"
FREQ_PATH = AUTO_DIR / "live_portfolio_frequency.json"
AUDIT_PATH = AUTO_DIR / "live_strategy_retrospective_audit.json"
SCORE_PATH = AUTO_DIR / "strategy_lifecycle_scores.json"
VAULT_PATH = AUTO_DIR / "strategy_lifecycle_freeze_vault.json"
REPLACE_PATH = AUTO_DIR / "strategy_lifecycle_replacements.json"
AUDIT_LOG = AUTO_DIR / "strategy_lifecycle_audit.jsonl"
STATUS_PATH = AUTO_DIR / "strategy_lifecycle_status.json"
WEEKLY_PATH = AUTO_DIR / "strategy_lifecycle_weekly_report.json"
HOUR_STATUS_PATH = AUTO_DIR / "strategy_hour_rotate_status.json"

HARD_EXCLUDE_PREFIXES = (
    "CL-USDT-SWAP|1h|",
)
# BTC 15m remains hard-excluded unless env-conditional C probe exception is set.
BTC15_PREFIX = "BTC-USDT-SWAP|15m|"

GRADE_RATIO = {"S": 0.70, "A": 0.50, "B": 0.30, "C": 0.10}
COLD_START_TRADES = 5
COLD_START_RATIO = 0.05  # 50% of C-grade
OUTCOME_GRADE_MANAGER = "trade_outcome_state_machine"
MIN_WAKE_MICRO_SAMPLES = 24


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


def _hard_excluded(assignment_id):
    if str(assignment_id).startswith(BTC15_PREFIX):
        # Env-conditional C probe exception may unlock BTC15m.
        controls = _read(CONTROL_PATH, {"assignments": {}})
        row = (controls.get("assignments") or {}).get(assignment_id) or {}
        if row.get("btc15_hard_exclude_exception") and row.get(
                "env_conditional_c_probe"):
            return False
        return True
    return any(str(assignment_id).startswith(p) for p in HARD_EXCLUDE_PREFIXES)


def _unmount_cannot_open(assignment_id, row, reason_tag):
    """Cannot-open strategies must leave daemon mounts (policy 2026-07-25).

    Lifecycle historically only set pause_new_entries / failed_closed / vault
    seal, which left keys hanging in formal_daemon_config* and showed up as
    forecast「暂停 N」. Scrub the daemon slot and mark lifecycle deleted.
    """
    row = dict(row or {})
    row["pause_new_entries"] = True
    row["new_entries_allowed"] = False
    row["max_position_ratio"] = 0.0
    if str(row.get("lifecycle_grade") or "").lower() != "deleted":
        row["lifecycle_grade"] = "deleted"
        row.setdefault("deleted_at", _now())
        row.setdefault("delete_reason", reason_tag)
    parts = str(assignment_id).split("|", 2)
    if len(parts) != 3:
        return row
    symbol, _timeframe, strategy_key = parts
    try:
        import auto_trade_legacy_strategy_rereview as rereview
        scrubbed = rereview._scrub_daemon_key_on_configs(
            strategy_key, symbol=symbol)
        if scrubbed:
            _append_audit({
                "event": "unmount_cannot_open",
                "assignment_id": assignment_id,
                "strategy_key": strategy_key,
                "daemon_configs_scrubbed": scrubbed,
                "reason": reason_tag,
            })
    except Exception as exc:
        _append_audit({
            "event": "unmount_cannot_open_fail",
            "assignment_id": assignment_id,
            "error": str(exc),
            "reason": reason_tag,
        })
    return row


def _in_vault(vault, assignment_id):
    return assignment_id in (vault.get("sealed") or {})


def _grade_of(row):
    if bool(row.get("lifecycle_shadow")) or str(row.get("audit_state") or "") in (
            "read_only_shadow", "lifecycle_shadow", "eliminated_pending_archive"):
        return "shadow"
    grade = str(row.get("lifecycle_grade") or row.get("max_grade") or "C").upper()
    if grade not in GRADE_RATIO:
        grade = "C"
    return grade


def _set_grade(row, grade):
    grade = str(grade or "C").upper()
    if grade == "SHADOW":
        row["lifecycle_grade"] = "shadow"
        row["max_grade"] = "C"
        row["max_position_ratio"] = 0.0
        row["pause_new_entries"] = True
        row["new_entries_allowed"] = False
        row["lifecycle_shadow"] = True
        row["audit_state"] = "read_only_shadow"
        return row
    row["lifecycle_grade"] = grade
    row["max_grade"] = grade
    row["max_position_ratio"] = float(GRADE_RATIO.get(grade, 0.10))
    row["lifecycle_shadow"] = False
    if str(row.get("audit_state") or "") in ("read_only_shadow", "lifecycle_shadow"):
        row["audit_state"] = "conditional_frequency_probe"
    if str(row.get("audit_state") or "") not in (
            "passed_all", "conditional_frequency_probe"):
        row["audit_state"] = "conditional_frequency_probe"
    row["pause_new_entries"] = False
    row["new_entries_allowed"] = True
    return row


def _downgrade_one(grade):
    grade = str(grade or "C").lower()
    if grade == "s":
        return "A"
    if grade == "a":
        return "B"
    if grade == "b":
        return "C"
    return "shadow"


def _merge_outcome_managed_rows(latest_controls, computed_assignments):
    """Prevent a long hourly run from overwriting a newer trade transition.

    Lifecycle scoring can take minutes while the one-minute grade monitor is
    recording a promotion, demotion, or C-grade removal.  For outcome-managed
    rows, merge only lifecycle observation fields into the newest on-disk row.
    """
    latest_assignments = (latest_controls.get("assignments") or {})
    merged = dict(computed_assignments or {})
    observation_fields = (
        "rotation_score", "rotation_updated_at", "lifecycle_advisory_reasons",
        "lifecycle_advisory_at",
    )
    for assignment_id, computed in list(merged.items()):
        latest = latest_assignments.get(assignment_id)
        if not isinstance(latest, dict):
            continue
        if (latest.get("grade_managed_by") != OUTCOME_GRADE_MANAGER
                and (computed or {}).get("grade_managed_by")
                != OUTCOME_GRADE_MANAGER):
            continue
        safe = dict(latest)
        for field in observation_fields:
            if field in (computed or {}):
                safe[field] = computed.get(field)
        merged[assignment_id] = safe
    return merged


def _env_fit_score(assignment_id):
    """Environment adaptation term for hourly rescoring (0..10)."""
    try:
        from auto_trade_strategy_evolution import env_fit_score
        return float(env_fit_score(assignment_id))
    except Exception:
        return 0.0


def _score_live(live, micro_fit=0.0, env_fit=0.0):
    """Score from cost posterior, recent win-rate, and environment fit."""
    trades = float(live.get("trades") or 0)
    wr = float(live.get("win_rate") or 0)
    ret = float(live.get("return_pct") or 0)
    sample = min(1.0, trades / 25.0)
    cost_hint = float(live.get("mean_net_return_pct")
                      or live.get("cost_posterior") or 0.0)
    score = (wr * 0.40) + (max(-50.0, min(50.0, ret)) * 0.30) + (sample * 18.0)
    score += max(-15.0, min(15.0, cost_hint)) * 0.5
    score += max(0.0, min(10.0, float(micro_fit or 0.0)))
    # 环境适配度：与微观适配度并列计入小时评分。
    score += max(0.0, min(10.0, float(env_fit or 0.0))) * 0.8
    return round(score, 3)


def _live_row(freq, assignment_id):
    for row in freq.get("assignments") or []:
        token = "%s|%s|%s" % (row.get("symbol"), row.get("timeframe"),
                              row.get("strategy_key"))
        if token == assignment_id:
            return row
    return {}


def _audit_evidence(audit, assignment_id):
    for row in audit.get("results") or []:
        if row.get("assignment_id") == assignment_id:
            return row
    return {}


def _closed_outcomes(strategy_key, symbol=None, limit=40):
    outcomes = []
    for path in sorted(AUTO_DIR.glob("formal_v6_state*.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in list(state.get("history") or [])[::-1]:
            if not isinstance(row, dict):
                continue
            if str(row.get("strategy_key") or "") != str(strategy_key or ""):
                continue
            if symbol and str(row.get("symbol") or "") not in ("", symbol):
                continue
            if row.get("closed_at") in (None, ""):
                continue
            try:
                pnl = float(row.get("pnl"))
            except Exception:
                pnl = None
            close_type = str(row.get("close_type") or row.get("close_reason") or "")
            outcomes.append({
                "pnl": pnl,
                "close_type": close_type,
                "closed_at": row.get("closed_at"),
                "is_stop": ("止损" in close_type) or ("stop" in close_type.lower()),
            })
            if len(outcomes) >= int(limit):
                return outcomes
    return outcomes


def _triple_cost_mean_negative(assignment_id, strategy_key, audit, live):
    """Kill redline: recent ~20 *live* trades with negative net mean.

    Offline retrospective backtests alone never seal the freeze vault; they may
    only inform downgrade / research.  The operator redline is forward live.
    """
    outcomes = _closed_outcomes(strategy_key, limit=20)
    pnls = [o["pnl"] for o in outcomes if o.get("pnl") is not None]
    if len(pnls) >= 20 and (sum(pnls) / float(len(pnls))) < 0:
        return True, "近20笔实盘净盈亏均值为负"
    trades = int(live.get("trades") or 0)
    ret = float(live.get("return_pct") or 0)
    if trades >= 20 and ret < 0:
        return True, "实盘累计收益为负且样本不少于20笔"
    return False, None


def _stop_loss_frequency_elevated(strategy_key):
    outcomes = _closed_outcomes(strategy_key, limit=40)
    if len(outcomes) < 12:
        return False, None
    recent = outcomes[:12]
    baseline = outcomes[12:40] or outcomes[6:12]
    recent_rate = sum(1 for o in recent if o.get("is_stop")) / float(len(recent))
    base_rate = (sum(1 for o in baseline if o.get("is_stop")) /
                 float(len(baseline))) if baseline else 0.0
    if recent_rate >= 0.5 and recent_rate >= (base_rate + 0.25):
        return True, ("连续止损频率显著高于历史基线：近期%.0f%% vs 基线%.0f%%"
                      % (recent_rate * 100.0, base_rate * 100.0))
    return False, None


def _death_precursor(assignment_id, audit, live):
    audited = _audit_evidence(audit, assignment_id)
    disposition = str(audited.get("disposition") or "")
    if "death" in disposition or disposition.startswith("suspended"):
        return True, "审计处置显示死亡/暂停前兆：%s" % disposition
    matches = audited.get("death_matches") or audited.get("confirmed_death_matches") or []
    if matches:
        return True, "命中死亡热力图已记录死因前兆"
    trades = int(live.get("trades") or 0)
    wr = float(live.get("win_rate") or 0)
    ret = float(live.get("return_pct") or 0)
    if trades >= 15 and wr < 50.0 and ret < 0:
        return True, "近期胜率与收益同时恶化，接近死亡热力图典型形态"
    return False, None


def _dual_ai_logic_failure(assignment_id, row, live):
    if os.environ.get("QIYU_LIFECYCLE_SKIP_AI") == "1":
        return False, None
    try:
        from auto_trade_ai_consensus import unanimous_review
    except Exception:
        return False, None
    evidence = {
        "assignment_id": assignment_id,
        "live": live,
        "question": "疑似逻辑失效",
        "ask": (
            "仅判断该实盘策略是否疑似逻辑失效。"
            "返回 JSON：logic_failed(bool), confidence(0-1), reason(str)。"
            "不得建议放宽止损或人工门禁。"
        ),
    }
    candidate = {
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "strategy_key": row.get("strategy_key"),
        "title": assignment_id,
        "lifecycle_logic_failure_check": True,
    }
    try:
        result = unanimous_review(candidate, evidence)
    except Exception as exc:
        return False, "AI复核不可用：%s" % exc
    reviews = result.get("reviews") or result.get("provider_results") or []
    failed = []
    for item in reviews:
        payload = item.get("payload") or item.get("json") or item
        if not isinstance(payload, dict):
            continue
        if payload.get("logic_failed") is True or str(
                payload.get("verdict") or "").lower() in (
                    "logic_failed", "失效", "reject"):
            failed.append(item.get("provider") or item.get("name") or "ai")
    if len(failed) >= 2:
        return True, "多AI复核同意疑似逻辑失效：%s" % ",".join(failed[:3])
    if result.get("logic_failed") is True and int(result.get("agree_count") or 0) >= 2:
        return True, "多AI复核同意疑似逻辑失效"
    return False, None


def _score_declined_three_hours(history):
    if len(history) < 4:
        return False
    recent = history[-4:]
    return (recent[1]["score"] < recent[0]["score"] and
            recent[2]["score"] < recent[1]["score"] and
            recent[3]["score"] < recent[2]["score"])


def _replacement_allowed(replacements, symbol, now_dt=None):
    now_dt = now_dt or datetime.now()
    bucket = replacements.setdefault("by_symbol", {})
    rows = list(bucket.get(symbol) or [])
    cutoff = (now_dt - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    rows = [r for r in rows if str(r.get("at") or "") >= cutoff]
    bucket[symbol] = rows
    return len(rows) < 1, rows


def _record_replacement(replacements, symbol, old_id, new_id, reason):
    bucket = replacements.setdefault("by_symbol", {})
    rows = list(bucket.get(symbol) or [])
    rows.append({"at": _now(), "old": old_id, "new": new_id, "reason": reason})
    bucket[symbol] = rows[-20:]
    replacements["updated_at"] = _now()
    return replacements


def _mark_cold_start(row):
    row["cold_start_trades_remaining"] = COLD_START_TRADES
    row["cold_start_position_ratio"] = COLD_START_RATIO
    row["cold_start_started_at"] = _now()
    return row


def cold_start_position_ratio(row, base_ratio):
    remaining = int(row.get("cold_start_trades_remaining") or 0)
    if remaining <= 0:
        return float(base_ratio)
    return min(float(base_ratio), float(
        row.get("cold_start_position_ratio") or COLD_START_RATIO))


def note_opened_trade(assignment_id):
    controls = _read(CONTROL_PATH, {"assignments": {}})
    assignments = dict(controls.get("assignments") or {})
    row = dict(assignments.get(assignment_id) or {})
    remaining = int(row.get("cold_start_trades_remaining") or 0)
    if remaining <= 0:
        return {"ok": True, "changed": False}
    remaining -= 1
    row["cold_start_trades_remaining"] = remaining
    if remaining <= 0:
        row["cold_start_completed_at"] = _now()
        row.pop("cold_start_position_ratio", None)
    assignments[assignment_id] = row
    controls["assignments"] = assignments
    controls["updated_at"] = _now()
    controls["updated_by"] = "auto_trade_strategy_lifecycle.note_opened_trade"
    _atomic(CONTROL_PATH, controls)
    _append_audit({"event": "cold_start_trade", "assignment_id": assignment_id,
                   "remaining": remaining})
    return {"ok": True, "changed": True, "remaining": remaining}


def _seal_to_vault(vault, assignment_id, reasons):
    sealed = dict(vault.get("sealed") or {})
    sealed[assignment_id] = {
        "sealed_at": _now(),
        "reasons": list(reasons),
        "state": "eliminated_pending_archive",
        "auto_revive_forbidden": True,
    }
    vault["sealed"] = sealed
    vault["updated_at"] = _now()
    return vault


def _candidate_backfill_pool(assignments, freq, vault, scores_map):
    pool = []
    for assignment_id, row in assignments.items():
        if _hard_excluded(assignment_id) or _in_vault(vault, assignment_id):
            continue
        if bool(row.get("pause_new_entries")) is False:
            continue
        if str(row.get("audit_state") or "") in (
                "eliminated_pending_archive", "failed_closed"):
            if not row.get("backfill_eligible"):
                continue
        live = _live_row(freq, assignment_id)
        score = _score_live(live)
        hist = (scores_map.get(assignment_id) or {}).get("history") or []
        if hist:
            score = float(hist[-1].get("score") or score)
        if str(row.get("audit_state") or "") == "read_only_shadow" and not row.get(
                "shadow_promotion_ready"):
            continue
        pool.append({
            "assignment_id": assignment_id,
            "score": score,
            "row": row,
            "ai_consensus": float(row.get("ai_consensus_score") or 0),
        })
    pool.sort(key=lambda item: (item["ai_consensus"], item["score"]), reverse=True)
    return pool


def _activate_probe(row, assignment_id, grade="C", cold_start=True):
    symbol, timeframe, strategy_key = assignment_id.split("|", 2)
    row = dict(row or {})
    row.update({
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy_key": strategy_key,
        "audit_state": "conditional_frequency_probe",
        "full_cognitive_matrix_passed": False,
        "manual_review_required_for_full_promotion": True,
        "automatic_live_restoration": False,
        "reactivated_at": _now(),
        "reason": "生命周期抢占补位/影子晋升：仅授予条件探针，不授予passed_all",
    })
    _set_grade(row, grade)
    if cold_start:
        _mark_cold_start(row)
    return row


def _try_wake_dormant(events):
    matrix = _read(AUTO_DIR / "research_cluster_matrix_status.json", {})
    micro = _read(AUTO_DIR / "microstructure_status.json", {})
    samples = micro.get("sample_counts") or micro.get("counts") or {}
    woken = []
    targets = matrix.get("targets") or matrix.get("clusters") or []
    if isinstance(targets, dict):
        targets = list(targets.values())
    for row in targets:
        if not isinstance(row, dict):
            continue
        sleeping = bool((row.get("compute_sleep") or {}).get("sleeping")
                        or row.get("sleeping"))
        if not sleeping:
            continue
        symbol = row.get("symbol")
        timeframe = row.get("timeframe")
        key = "%s|%s" % (symbol, timeframe)
        count = 0
        if isinstance(samples, dict):
            count = int(samples.get(key) or samples.get(symbol) or 0)
            if not count and isinstance(samples.get(symbol), dict):
                count = int((samples.get(symbol) or {}).get(timeframe) or 0)
        if count < MIN_WAKE_MICRO_SAMPLES:
            continue
        woken.append({"symbol": symbol, "timeframe": timeframe,
                      "micro_samples": count})
        events.append({
            "event": "dormant_wake",
            "symbol": symbol,
            "timeframe": timeframe,
            "micro_samples": count,
            "natural_language": (
                "%s %s 因微观样本已达 %d 条，自动唤醒进入候选评分池"
                % (symbol, timeframe, count)),
        })
    if woken:
        wake_doc = _read(AUTO_DIR / "strategy_lifecycle_wake_requests.json", {})
        wake_doc["updated_at"] = _now()
        wake_doc["requests"] = (wake_doc.get("requests") or []) + woken
        wake_doc["requests"] = wake_doc["requests"][-100:]
        _atomic(AUTO_DIR / "strategy_lifecycle_wake_requests.json", wake_doc)
    return woken


def _shadow_ready_for_c_probe():
    ready = []
    try:
        import auto_trade_strategy_ecosystem as ecosystem
        conn = ecosystem._db()
        rows = conn.execute(
            "SELECT candidate_hash,experiment_id,symbol,timeframe,pattern_id,state,"
            "shadow_state_json,registered_at,shadow_completed_at "
            "FROM positive_confirmation_candidates "
            "WHERE state IN ('shadow_verified_positive','awaiting_codex_review') "
            "ORDER BY shadow_completed_at DESC LIMIT 20"
        ).fetchall()
        for row in rows:
            digest, _exp, symbol, timeframe, pattern_id, state = row[:6]
            cand = conn.execute(
                "SELECT candidate_json,state FROM candidates WHERE candidate_hash=?",
                (digest,)).fetchone()
            if not cand:
                continue
            payload = json.loads(cand[0] or "{}")
            strategy_key = (payload.get("strategy_key")
                            or payload.get("key")
                            or ("shadow_" + digest[:10]))
            ready.append({
                "candidate_hash": digest,
                "symbol": symbol,
                "timeframe": timeframe,
                "strategy_key": strategy_key,
                "pattern_id": pattern_id,
                "state": state,
                "candidate": payload,
            })
        conn.close()
    except Exception:
        return ready
    return ready


def _triple_ai_shadow_ok(item):
    if os.environ.get("QIYU_LIFECYCLE_SKIP_AI") == "1":
        return False, "测试跳过AI"
    try:
        from auto_trade_ai_consensus import unanimous_review
        evidence = {
            "shadow_promotion": True,
            "ask": (
                "该策略已完成至少7日只读影子验证。是否同意授予C级条件探针实盘测试资格？"
                "不得授予passed_all，不得放松止损。返回 approve_c_probe(bool)。"
            ),
        }
        result = unanimous_review(item.get("candidate") or item, evidence)
        if result.get("unanimous") is True or result.get("ok") is True:
            return True, "三AI一致同意C级探针"
        reviews = result.get("reviews") or []
        agrees = 0
        for rev in reviews:
            payload = rev.get("payload") or rev.get("json") or rev
            if isinstance(payload, dict) and payload.get("approve_c_probe") is True:
                agrees += 1
        if agrees >= 3 or (agrees >= 2 and len(reviews) <= 2):
            return True, "三AI复核同意C级探针（%d票）" % agrees
        return False, "三AI未一致同意C级探针"
    except Exception as exc:
        return False, "三AI复核失败：%s" % exc


def promote_shadow_to_c_probes(assignments, events, vault):
    promoted = []
    for item in _shadow_ready_for_c_probe():
        assignment_id = "%s|%s|%s" % (
            item["symbol"], item["timeframe"], item["strategy_key"])
        if _hard_excluded(assignment_id) or _in_vault(vault, assignment_id):
            continue
        existing = assignments.get(assignment_id) or {}
        if (str(existing.get("audit_state") or "") == "conditional_frequency_probe"
                and not existing.get("pause_new_entries")):
            continue
        ok, reason = _triple_ai_shadow_ok(item)
        if not ok:
            events.append({"event": "shadow_promotion_deferred",
                           "assignment_id": assignment_id, "reason": reason})
            continue
        row = _activate_probe(existing, assignment_id, grade="C", cold_start=True)
        row["shadow_auto_promoted_at"] = _now()
        row["shadow_promotion_reason"] = reason
        row["candidate_hash"] = item.get("candidate_hash")
        assignments[assignment_id] = row
        promoted.append(assignment_id)
        events.append({
            "event": "shadow_promoted_to_c_probe",
            "assignment_id": assignment_id,
            "reason": reason,
            "natural_language": (
                "%s 影子验证满7天且三AI复核通过，自动获得C级条件探针资格（冷启动仓位）"
                % assignment_id),
        })
        _append_audit(events[-1])
    return promoted


def _preemptive_backfill(assignments, freq, vault, scores_map, replacements,
                         symbol, old_id, events, backfilled, reason):
    allowed, _ = _replacement_allowed(replacements, symbol)
    if not allowed:
        return replacements
    pool = _candidate_backfill_pool(assignments, freq, vault, scores_map)
    pick = next((p for p in pool if p["assignment_id"].startswith(symbol + "|")
                 and p["assignment_id"] != old_id), None)
    if not pick:
        return replacements
    new_id = pick["assignment_id"]
    assignments[new_id] = _activate_probe(
        pick["row"], new_id, grade="C", cold_start=True)
    replacements = _record_replacement(
        replacements, symbol, old_id, new_id, reason)
    backfilled.append({"from": old_id, "to": new_id})
    ev = {
        "event": "preemptive_backfill",
        "old": old_id,
        "new": new_id,
        "natural_language": "%s 后，%s 以C级冷启动抢占补位" % (reason, new_id),
    }
    events.append(ev)
    _append_audit(ev)
    return replacements


def run_once(preemptive=False, skip_ai=False):
    if skip_ai:
        os.environ["QIYU_LIFECYCLE_SKIP_AI"] = "1"
    controls = _read(CONTROL_PATH, {"assignments": {}})
    assignments = dict(controls.get("assignments") or {})
    freq = _read(FREQ_PATH, {})
    audit = _read(AUDIT_PATH, {})
    scores_doc = _read(SCORE_PATH, {"assignments": {}})
    scores_map = dict(scores_doc.get("assignments") or {})
    vault = _read(VAULT_PATH, {"sealed": {}})
    replacements = _read(REPLACE_PATH, {"by_symbol": {}})

    events = []
    ranked = []
    downgraded = []
    eliminated = []
    kept = []
    backfilled = []

    for assignment_id, row in list(assignments.items()):
        if _hard_excluded(assignment_id):
            row = dict(row)
            if str(row.get("audit_state") or "") not in (
                    "failed_closed", "read_only_shadow",
                    "eliminated_pending_archive"):
                row["audit_state"] = "failed_closed"
            row["rotation_note"] = "永久排除：CL1h封存或BTC15m只读影子"
            # Policy: cannot open → unmount/delete, do not hang as 暂停.
            row = _unmount_cannot_open(
                assignment_id, row,
                "lifecycle_hard_exclude_unmount")
            if str(row.get("audit_state") or "") == "failed_closed":
                row["audit_state"] = "eliminated_pending_archive"
            assignments[assignment_id] = row

    for assignment_id, row in list(assignments.items()):
        if _hard_excluded(assignment_id):
            continue
        if _in_vault(vault, assignment_id):
            row = _unmount_cannot_open(
                assignment_id, row,
                "lifecycle_vault_sealed_unmount")
            row["audit_state"] = "eliminated_pending_archive"
            assignments[assignment_id] = row
            continue
        state = str(row.get("audit_state") or "")
        open_perm = (
            state in ("conditional_frequency_probe", "passed_all",
                      "read_only_shadow")
            or not bool(row.get("pause_new_entries"))
        )
        if not open_perm and state not in (
                "conditional_frequency_probe", "passed_all", "read_only_shadow"):
            continue

        live = _live_row(freq, assignment_id)
        env_fit = _env_fit_score(assignment_id)
        score = _score_live(live, env_fit=env_fit)
        hist_wrap = dict(scores_map.get(assignment_id) or {})
        history = list(hist_wrap.get("history") or [])
        history.append({"at": _now(), "score": score, "env_fit": env_fit})
        history = history[-48:]
        hist_wrap["history"] = history
        hist_wrap["env_fit"] = env_fit
        hist_wrap["updated_at"] = _now()
        scores_map[assignment_id] = hist_wrap

        symbol, timeframe, strategy_key = assignment_id.split("|", 2)
        row = dict(row)
        row["rotation_score"] = score
        row["rotation_updated_at"] = _now()
        row.setdefault("symbol", symbol)
        row.setdefault("timeframe", timeframe)
        row.setdefault("strategy_key", strategy_key)

        kill_reasons = []
        neg, why = _triple_cost_mean_negative(
            assignment_id, strategy_key, audit, live)
        if neg:
            kill_reasons.append(why)
        elev, why = _stop_loss_frequency_elevated(strategy_key)
        if elev:
            kill_reasons.append(why)
        stressed = bool(kill_reasons) or _score_declined_three_hours(history)
        if stressed:
            failed, why = _dual_ai_logic_failure(assignment_id, row, live)
            if failed:
                kill_reasons.append(why)

        outcome_grade_managed = (
            row.get("grade_managed_by") == OUTCOME_GRADE_MANAGER)
        if kill_reasons and outcome_grade_managed:
            # Hourly scoring remains advisory for mounted S/A/B/C strategies.
            # Only the closed-trade state machine may change their grade or
            # remove them, which guarantees the required WxPusher transition.
            row["lifecycle_advisory_reasons"] = kill_reasons
            row["lifecycle_advisory_at"] = _now()
            _append_audit({
                "event": "managed_grade_advisory_only",
                "assignment_id": assignment_id,
                "reasons": kill_reasons,
            })
            kill_reasons = []

        if kill_reasons and state != "read_only_shadow":
            row["lifecycle_eliminated_at"] = _now()
            row["lifecycle_eliminate_reasons"] = kill_reasons
            # Policy: cannot open → unmount/delete, do not hang as 暂停.
            row = _unmount_cannot_open(
                assignment_id, row,
                "lifecycle_eliminated:" + ";".join(kill_reasons)[:180])
            row["audit_state"] = "eliminated_pending_archive"
            vault = _seal_to_vault(vault, assignment_id, kill_reasons)
            assignments[assignment_id] = row
            eliminated.append({"assignment_id": assignment_id,
                               "reasons": kill_reasons})
            ev = {
                "event": "eliminated",
                "assignment_id": assignment_id,
                "reasons": kill_reasons,
                "natural_language": (
                    "%s 触发淘汰红线，已冻结开仓并卸载挂载：%s"
                    % (assignment_id, "；".join(kill_reasons))),
            }
            events.append(ev)
            _append_audit(ev)
            replacements = _preemptive_backfill(
                assignments, freq, vault, scores_map, replacements,
                symbol, assignment_id, events, backfilled, "淘汰")
            continue

        if (state in ("conditional_frequency_probe", "passed_all")
                and not row.get("pause_new_entries")
                and outcome_grade_managed):
            if not row.get("max_position_ratio"):
                _set_grade(row, row.get("max_grade") or "B")
            kept.append({
                "assignment_id": assignment_id, "score": score,
                "grade": _grade_of(row),
                "grade_decision": "advisory_only_outcome_state_machine_owns",
            })
        elif state in ("conditional_frequency_probe", "passed_all") and not row.get(
                "pause_new_entries"):
            need_down = False
            down_reasons = []
            if _score_declined_three_hours(history):
                need_down = True
                down_reasons.append("评分连续3小时下滑")
            precursor, why = _death_precursor(assignment_id, audit, live)
            if precursor:
                need_down = True
                down_reasons.append(why)
            trades = int(live.get("trades") or 0)
            wr = float(live.get("win_rate") or 0)
            ret = float(live.get("return_pct") or 0)
            if trades >= 10 and wr < 55.0:
                need_down = True
                down_reasons.append("滚动胜率低于55%")
            if trades >= 10 and ret < 0:
                need_down = True
                down_reasons.append("实盘累计收益为负")

            if need_down:
                current = _grade_of(row)
                nxt = _downgrade_one(current)
                before = current
                _set_grade(row, nxt)
                row["lifecycle_downgrade_reasons"] = down_reasons
                row["lifecycle_downgraded_at"] = _now()
                downgraded.append({
                    "assignment_id": assignment_id,
                    "from": before, "to": nxt, "reasons": down_reasons})
                ev = {
                    "event": "downgraded",
                    "assignment_id": assignment_id,
                    "from_grade": before,
                    "to_grade": nxt,
                    "reasons": down_reasons,
                    "natural_language": (
                        "%s 从 %s 降到 %s：%s"
                        % (assignment_id, before, nxt, "；".join(down_reasons))),
                }
                events.append(ev)
                _append_audit(ev)
                if nxt == "shadow":
                    replacements = _preemptive_backfill(
                        assignments, freq, vault, scores_map, replacements,
                        symbol, assignment_id, events, backfilled,
                        "降级至影子")
            else:
                if not row.get("max_position_ratio"):
                    _set_grade(row, row.get("max_grade") or "C")
                kept.append({"assignment_id": assignment_id, "score": score,
                             "grade": _grade_of(row)})

        assignments[assignment_id] = row
        ranked.append({"assignment_id": assignment_id, "score": score,
                       "grade": _grade_of(row),
                       "paused": bool(row.get("pause_new_entries"))})

    ranked.sort(key=lambda item: item["score"], reverse=True)
    woken = _try_wake_dormant(events)
    promoted = promote_shadow_to_c_probes(assignments, events, vault)

    # Re-read just before commit: never let this long-running hourly snapshot
    # clobber a grade/window/removal written by the one-minute monitor.
    latest_controls = _read(CONTROL_PATH, {"assignments": {}})
    assignments = _merge_outcome_managed_rows(latest_controls, assignments)
    controls["assignments"] = assignments
    controls["updated_at"] = _now()
    controls["updated_by"] = "auto_trade_strategy_lifecycle.py"
    controls["lifecycle"] = {
        "schema": "qiyu_strategy_lifecycle_v1",
        "preemptive": bool(preemptive),
        "cold_start_trades": COLD_START_TRADES,
        "cold_start_ratio": COLD_START_RATIO,
        "replacement_limit_per_symbol_hour": 1,
        "shadow_auto_c_probe": True,
        "passed_all_still_requires_human": True,
    }
    _atomic(CONTROL_PATH, controls)
    try:
        import auto_trade_forecast_closeout as closeout
        closeout.notify_pool_change(
            "grade_size_permission_changed",
            detail={"source": "auto_trade_strategy_lifecycle",
                    "preemptive": bool(preemptive)},
            auto_refresh=True,
        )
    except Exception:
        pass
    scores_doc["assignments"] = scores_map
    scores_doc["updated_at"] = _now()
    _atomic(SCORE_PATH, scores_doc)
    _atomic(VAULT_PATH, vault)
    _atomic(REPLACE_PATH, replacements)

    nl = (
        "本轮策略生命治理已完成：在场策略已按成本后验、近期胜率与环境适配度重新打分。"
        "降级 %d 条，淘汰入冷冻库 %d 条，抢占补位 %d 条，影子晋升C级 %d 条，休眠唤醒 %d 组。"
        "止损与退出管理未改；完整晋级仍须人工批准；单标的每小时最多替换一次；"
        "新补位策略前5笔使用C级一半仓位。"
        % (len(downgraded), len(eliminated), len(backfilled), len(promoted),
           len(woken))
    )
    status = {
        "ok": True,
        "created_at": _now(),
        "policy": "lifecycle_metabolism_without_gate_relaxation",
        "preemptive": bool(preemptive),
        "ranked": ranked[:40],
        "kept_active": kept,
        "downgraded": downgraded,
        "eliminated": eliminated,
        "backfilled": backfilled,
        "shadow_promoted": promoted,
        "woken": woken,
        "events": events[-30:],
        "natural_language": nl,
    }
    _atomic(STATUS_PATH, status)
    _atomic(HOUR_STATUS_PATH, {
        "ok": True,
        "created_at": _now(),
        "policy": status["policy"],
        "ranked": ranked[:30],
        "kept_active": kept,
        "paused": [{"assignment_id": x["assignment_id"],
                    "reasons": x.get("reasons")} for x in eliminated] + [
            {"assignment_id": x["assignment_id"], "reasons": x.get("reasons"),
             "to": x.get("to")} for x in downgraded],
        "natural_language": nl,
    })
    maybe_write_weekly_report(push_wx=False)
    # Strategy validity countdown (未来2年TTL)：标注/倒数/到期删除+WxPusher
    validity_tick = None
    try:
        import auto_trade_strategy_validity as validity
        validity_tick = validity.tick(dry_run=False, annotate_missing=True)
        status["strategy_validity"] = {
            "active_n": validity_tick.get("active_n"),
            "expired_n": validity_tick.get("expired_n"),
            "default_validity_days": validity_tick.get("default_validity_days"),
        }
    except Exception as exc:
        status["strategy_validity"] = {"ok": False, "error": str(exc)[:200]}
        _append_audit({
            "event": "strategy_validity_tick_fail",
            "error": str(exc)[:300],
        })
    _atomic(STATUS_PATH, status)
    return status


def maybe_write_weekly_report(force=False, push_wx=True, batch_time=None):
    now = datetime.now()
    week = now.strftime("%Y-W%W")
    existing = _read(WEEKLY_PATH, {})
    if existing.get("week_bucket") == week and not force:
        if push_wx and not existing.get("wx_sent_at"):
            footer = batch_time or _now()
            try:
                from common import send_wx
                send_wx(existing["natural_language"] + "\n时间: %s" % footer)
                existing["wx_sent_at"] = footer
                existing["wx_sent"] = True
                _atomic(WEEKLY_PATH, existing)
            except Exception:
                existing["wx_sent"] = False
        return existing
    eliminated = []
    downgraded = []
    backfilled = []
    promoted = []
    if AUDIT_LOG.exists():
        cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines()[-5000:]:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if str(row.get("time") or "") < cutoff:
                continue
            ev = row.get("event")
            if ev == "eliminated":
                eliminated.append(row)
            elif ev == "downgraded":
                downgraded.append(row)
            elif ev == "preemptive_backfill":
                backfilled.append(row)
            elif ev == "shadow_promoted_to_c_probe":
                promoted.append(row)
    lines = [
        "=== 栖语策略生态演化周报 ===",
        "周期：%s" % week,
        "本周淘汰 %d 条、降级 %d 条、补位 %d 条、影子晋升C级 %d 条。"
        % (len(eliminated), len(downgraded), len(backfilled), len(promoted)),
    ]
    for row in eliminated[-8:]:
        lines.append("· 淘汰 %s：%s" % (
            row.get("assignment_id"),
            "；".join(row.get("reasons") or [row.get("natural_language") or ""])))
    for row in downgraded[-8:]:
        lines.append("· 降级 %s：%s → %s（%s）" % (
            row.get("assignment_id"), row.get("from_grade"), row.get("to_grade"),
            "；".join(row.get("reasons") or [])))
    for row in backfilled[-8:]:
        lines.append("· 补位 %s → %s" % (row.get("old"), row.get("new")))
    for row in promoted[-8:]:
        lines.append("· 影子晋升 %s" % row.get("assignment_id"))
    lines.append("说明：冷冻库策略默认永不自动复活；完整晋级仍须人工批准；"
                 "止损与退出管理全程未改。")
    report = {
        "ok": True,
        "week_bucket": week,
        "created_at": _now(),
        "natural_language": "\n".join(lines),
        "counts": {
            "eliminated": len(eliminated),
            "downgraded": len(downgraded),
            "backfilled": len(backfilled),
            "promoted": len(promoted),
        },
    }
    wx_sent = False
    if push_wx:
        try:
            from common import send_wx
            footer = batch_time or report["created_at"]
            send_wx(report["natural_language"] + "\n时间: %s" % footer)
            report["wx_sent_at"] = footer
            wx_sent = True
        except Exception:
            pass
    report["wx_sent"] = wx_sent
    _atomic(WEEKLY_PATH, report)
    snap = AUTO_DIR / ("strategy_lifecycle_weekly_report_%s.json"
                       % week.replace("-", ""))
    _atomic(snap, report)
    return report


def repair_false_vault_seals(freq=None, audit=None):
    """One-shot repair: unseal vault rows that fail the live-only kill test.

    Irreversibility still applies to true live kill redlines.  This only undoes
    seals created by the offline-audit false positive.
    """
    vault = _read(VAULT_PATH, {"sealed": {}})
    controls = _read(CONTROL_PATH, {"assignments": {}})
    assignments = dict(controls.get("assignments") or {})
    freq = freq or _read(FREQ_PATH, {})
    audit = audit or _read(AUDIT_PATH, {})
    sealed = dict(vault.get("sealed") or {})
    restored = []
    for assignment_id, meta in list(sealed.items()):
        live = _live_row(freq, assignment_id)
        strategy_key = assignment_id.split("|", 2)[-1]
        still_kill, why = _triple_cost_mean_negative(
            assignment_id, strategy_key, audit, live)
        elev, why2 = _stop_loss_frequency_elevated(strategy_key)
        if still_kill or elev:
            # True live redline still holds — irreversibility preserved.
            continue
        reasons = meta.get("reasons") or []
        # Unseal only when current live evidence no longer meets kill redlines.
        sealed.pop(assignment_id, None)
        row = dict(assignments.get(assignment_id) or {})
        symbol, timeframe, strategy_key = assignment_id.split("|", 2)
        row.update({
            "symbol": symbol, "timeframe": timeframe,
            "strategy_key": strategy_key,
            "audit_state": "conditional_frequency_probe",
            "pause_new_entries": False,
            "new_entries_allowed": True,
            "lifecycle_false_vault_repaired_at": _now(),
            "reason": "冷冻库误封修复：淘汰红线已改为仅看实盘近20笔",
            "prior_false_seal_reasons": reasons,
        })
        row.pop("lifecycle_eliminate_reasons", None)
        row.pop("lifecycle_eliminated_at", None)
        if not row.get("max_position_ratio"):
            _set_grade(row, row.get("max_grade") or "C")
        else:
            row["lifecycle_shadow"] = False
            row["max_grade"] = row.get("max_grade") or "C"
        assignments[assignment_id] = row
        restored.append(assignment_id)
        _append_audit({
            "event": "vault_false_seal_repaired",
            "assignment_id": assignment_id,
            "natural_language": (
                "%s 冷冻库误封已修复并恢复为条件探针（原由：%s）"
                % (assignment_id, "；".join(reasons) or "未知")),
        })
    vault["sealed"] = sealed
    vault["updated_at"] = _now()
    vault["last_repair_at"] = _now()
    controls["assignments"] = assignments
    controls["updated_at"] = _now()
    controls["updated_by"] = "auto_trade_strategy_lifecycle.repair_false_vault_seals"
    _atomic(VAULT_PATH, vault)
    _atomic(CONTROL_PATH, controls)
    return {"ok": True, "restored": restored, "still_sealed": list(sealed.keys())}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--preemptive", action="store_true")
    parser.add_argument("--skip-ai", action="store_true")
    parser.add_argument("--weekly-report", action="store_true")
    parser.add_argument("--repair-false-vault", action="store_true")
    args = parser.parse_args()
    if args.weekly_report:
        print(json.dumps(maybe_write_weekly_report(force=True, push_wx=False),
                         ensure_ascii=False, indent=2, sort_keys=True))
    elif args.repair_false_vault:
        print(json.dumps(repair_false_vault_seals(),
                         ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(run_once(preemptive=args.preemptive,
                                  skip_ai=args.skip_ai),
                         ensure_ascii=False, indent=2, sort_keys=True))
