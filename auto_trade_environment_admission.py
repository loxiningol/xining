# -*- coding: utf-8 -*-
"""Environment admission: bind microstructure boundaries to live strategies.

Each live-permission strategy must declare when it may open, using registered
slow micro primitives as the description language.  Out-of-boundary signals are
suppressed (not counted as performance failure).  Stop-loss / exit management
and full passed_all human approval are never relaxed.
"""
from __future__ import print_function

from datetime import datetime
from pathlib import Path
import json
import os
import sqlite3
import tempfile
import time

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"
TELEMETRY_DB = AUTO_DIR / "microstructure_telemetry.db"
STATUS_PATH = AUTO_DIR / "environment_admission_status.json"
AUDIT_LOG = AUTO_DIR / "environment_admission_audit.jsonl"
BTC15_REPORT = AUTO_DIR / "btc15_environment_reassessment.json"
MISMATCH_PATH = AUTO_DIR / "environment_mismatch_counters.json"

HARD_EXCLUDE_PREFIXES = ("CL-USDT-SWAP|1h|",)
BTC15_ASSIGNMENT = (
    "BTC-USDT-SWAP|15m|btc15_dual_cycle_downtrend_reentry_short_ai"
)
ENV_MISMATCH_SLEEP_STREAK = 24
MIN_BOUNDARY_PRIMITIVE_SAMPLES = 40
# Approximate (near-env) opens: D-grade exploratory capital, not full probe size.
APPROX_POSITION_RATIO = 0.065  # mid of 5–8%
APPROX_POSITION_GRADE = "D"
APPROX_ALL_OF_MIN_RATIO = 0.5
# Registered exact window and soft/pending window (hours).
# Soft window may be expanded by strategy breath controller (2h–12h).
PRIMITIVE_EXACT_FRESH_MS = 2 * 60 * 60 * 1000
PRIMITIVE_SOFT_FRESH_MS = 2 * 60 * 60 * 1000
PENDING_VERIFICATION_STATE = "pending_verification"


def soft_window_ms():
    """Effective soft/pending freshness from breath controller, else 2h floor."""
    try:
        import auto_trade_strategy_breath as breath
        return int(breath.effective_soft_window_ms())
    except Exception:
        return int(PRIMITIVE_SOFT_FRESH_MS)


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


def _connect():
    return sqlite3.connect(str(TELEMETRY_DB), timeout=30)


def list_registered_primitives(symbol=None, limit=50):
    if not TELEMETRY_DB.exists():
        return []
    conn = _connect()
    try:
        if symbol:
            prefix = "slow_micro_%s_" % str(symbol).split("-")[0].lower()
            rows = conn.execute(
                "SELECT cluster_id,label,description,sample_count,centroid_json "
                "FROM microstructure_primitive_clusters "
                "WHERE state='descriptive_registered' AND cluster_id LIKE ? "
                "ORDER BY sample_count DESC LIMIT ?",
                (prefix + "%", int(limit))).fetchall()
        else:
            rows = conn.execute(
                "SELECT cluster_id,label,description,sample_count,centroid_json "
                "FROM microstructure_primitive_clusters "
                "WHERE state='descriptive_registered' "
                "ORDER BY sample_count DESC LIMIT ?",
                (int(limit),)).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        out.append({
            "cluster_id": row[0], "label": row[1], "description": row[2],
            "sample_count": int(row[3] or 0),
            "centroid": json.loads(row[4] or "{}"),
        })
    return out


def degrade_stale_registered_primitives(max_age_ms=None):
    """Registered clusters older than soft window → pending_verification.

    They stop being exact-match hard dependencies and remain D-grade reference.
    """
    max_age_ms = int(max_age_ms if max_age_ms is not None else soft_window_ms())
    if not TELEMETRY_DB.exists():
        return {"ok": False, "degraded": 0, "reason": "telemetry_db_missing"}
    now_ms = int(time.time() * 1000)
    conn = _connect()
    degraded = []
    try:
        rows = conn.execute(
            "SELECT c.cluster_id, MAX(a.end_ms) AS last_end "
            "FROM microstructure_primitive_clusters c "
            "LEFT JOIN microstructure_primitive_assignments a "
            "ON a.cluster_id=c.cluster_id "
            "WHERE c.state='descriptive_registered' "
            "GROUP BY c.cluster_id"
        ).fetchall()
        for cluster_id, last_end in rows:
            last_end = int(last_end or 0)
            age = now_ms - last_end if last_end else max_age_ms + 1
            if age <= max_age_ms:
                continue
            conn.execute(
                "UPDATE microstructure_primitive_clusters SET state=?, updated_at=? "
                "WHERE cluster_id=? AND state='descriptive_registered'",
                (PENDING_VERIFICATION_STATE, _now(), cluster_id))
            degraded.append({
                "cluster_id": cluster_id,
                "age_ms": age,
                "last_end_ms": last_end or None,
            })
            _append_audit({
                "event": "primitive_degraded_pending_verification",
                "cluster_id": cluster_id,
                "age_ms": age,
                "natural_language": (
                    "基元 %s 超过2小时未获新数据验证，降级为待验证（仅作D级参考）"
                    % cluster_id),
            })
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "degraded": len(degraded), "items": degraded[:40],
            "policy": "exact_match_requires_fresh_registered;pending_is_d_grade_reference_only"}


def current_environment(symbol, candle_ms=None, lookback_ms=30 * 60 * 1000):
    if candle_ms is None:
        candle_ms = int(time.time() * 1000)
    if not TELEMETRY_DB.exists():
        return {"available": False, "reason": "telemetry_db_missing",
                "symbol": symbol, "candle_ms": candle_ms,
                "recent_cluster_ids": [], "soft_available": False}
    conn = _connect()
    try:
        latest = conn.execute(
            "SELECT a.cluster_id,a.distance,a.end_ms,c.label,c.description,c.state,"
            "c.sample_count FROM microstructure_primitive_assignments a "
            "JOIN microstructure_primitive_clusters c ON c.cluster_id=a.cluster_id "
            "WHERE a.symbol=? AND a.end_ms<=? "
            "ORDER BY a.end_ms DESC LIMIT 1",
            (symbol, int(candle_ms))).fetchone()
        # Exact path: only descriptive_registered in lookback.
        recent_reg = conn.execute(
            "SELECT DISTINCT a.cluster_id FROM microstructure_primitive_assignments a "
            "JOIN microstructure_primitive_clusters c ON c.cluster_id=a.cluster_id "
            "WHERE a.symbol=? AND a.end_ms<=? AND a.end_ms>=? "
            "AND c.state='descriptive_registered'",
            (symbol, int(candle_ms), int(candle_ms) - int(lookback_ms))).fetchall()
        soft_ms = int(soft_window_ms())
        # Soft/pending path: any observed cluster within breath soft window.
        recent_any = conn.execute(
            "SELECT DISTINCT a.cluster_id FROM microstructure_primitive_assignments a "
            "JOIN microstructure_primitive_clusters c ON c.cluster_id=a.cluster_id "
            "WHERE a.symbol=? AND a.end_ms<=? AND a.end_ms>=? "
            "AND c.state IN ('descriptive_registered','pending_verification',"
            "'observed_unlabeled','observed_unstable')",
            (symbol, int(candle_ms),
             int(candle_ms) - soft_ms)).fetchall()
    finally:
        conn.close()
    if not latest:
        return {"available": False, "reason": "no_assignment",
                "symbol": symbol, "candle_ms": candle_ms,
                "recent_cluster_ids": [], "soft_available": False}
    soft_ms = int(soft_window_ms())
    age = int(candle_ms) - int(latest[2])
    state = str(latest[5] or "")
    registered = state == "descriptive_registered"
    pending = state in (PENDING_VERIFICATION_STATE, "observed_unlabeled",
                        "observed_unstable") or (registered and age > soft_ms)
    fresh = registered and age <= PRIMITIVE_EXACT_FRESH_MS
    soft_fresh = age <= soft_ms
    # Soft available: any recent assignment within breath window (pending/observed ok).
    soft_available = bool(soft_fresh and latest[0])
    return {
        "available": bool(fresh and not pending),
        "soft_available": soft_available,
        "pending_verification": bool(pending or not registered),
        "symbol": symbol, "candle_ms": int(candle_ms),
        "cluster_id": latest[0], "distance": latest[1],
        "observed_ms": latest[2],
        "label": (latest[3] if registered else (latest[3] or "待验证微观基元")),
        "description": latest[4] if registered else (latest[4] or ""),
        "state": state, "sample_count": int(latest[6] or 0),
        "age_ms": age, "fresh": fresh, "soft_fresh": soft_fresh,
        "soft_window_ms": soft_ms,
        "recent_cluster_ids": [r[0] for r in recent_reg],
        "recent_soft_cluster_ids": [r[0] for r in recent_any],
        "volatility_bucket": None,
        "reason": (None if fresh else (
            "registered_primitive_stale" if registered else
            ("pending_verification" if pending else "no_registered_primitive"))),
    }


def matches_boundary(env, boundary, allow_approximate=True):
    """Exact match → full open; soft near-env → D-grade approx; hard miss → suppress.

    Forbidden primitives and missing/unapproved boundaries remain hard blocks.
    Approximate opens never relax stops or passed_all promotion.
    """
    if not boundary or not isinstance(boundary, dict):
        return {"ok": False, "reason": "missing_environment_boundary",
                "natural_language": "策略尚未绑定适用微观环境边界，禁止开仓"}
    if not boundary.get("final_approved"):
        return {"ok": False, "reason": "boundary_not_final_approved",
                "natural_language": "环境边界尚未通过三AI终审，禁止开仓"}
    if not env or not env.get("available"):
        # Pending/stale/observed within 2h: D-grade reference only, not exact.
        if (allow_approximate and env and env.get("soft_available")
                and env.get("cluster_id") and boundary.get("final_approved")):
            soft_env = dict(env)
            soft_env["available"] = True
            soft_recent = list(env.get("recent_soft_cluster_ids")
                               or env.get("recent_cluster_ids") or [])
            if env.get("cluster_id") and env.get("cluster_id") not in soft_recent:
                soft_recent.append(env.get("cluster_id"))
            soft_env["recent_cluster_ids"] = soft_recent
            decision = matches_boundary(
                soft_env, boundary, allow_approximate=True)
            if decision.get("ok"):
                decision["approximate"] = True
                decision["exact"] = False
                decision["match_mode"] = "pending_or_stale_approximate"
                decision["position_grade"] = APPROX_POSITION_GRADE
                decision["max_position_ratio"] = APPROX_POSITION_RATIO
                decision["reason"] = "pending_verification_approximate"
                decision["natural_language"] = (
                    "微观基元处于待验证/2小时软窗口：允许D级试探开仓（%.1f%%），"
                    "不作精确环境硬依赖" % (APPROX_POSITION_RATIO * 100.0))
                return decision
            if decision.get("reason") == "forbidden_primitive_present":
                return decision
            return {
                "ok": True, "exact": False, "approximate": True,
                "match_mode": "pending_or_stale_approximate",
                "reason": "pending_verification_blind_probe",
                "position_grade": APPROX_POSITION_GRADE,
                "max_position_ratio": APPROX_POSITION_RATIO,
                "natural_language": (
                    "微观基元待验证且不完全匹配：仅允许D级盲探开仓（%.1f%%）"
                    % (APPROX_POSITION_RATIO * 100.0)),
                "cluster_id": env.get("cluster_id"),
                "label": env.get("label"),
                "age_ms": env.get("age_ms"),
                "state": env.get("state"),
            }
        return {"ok": False, "reason": "environment_unavailable",
                "natural_language": "当前微观环境不可用或基元未注册，信号抑制"}
    current = env.get("cluster_id")
    recent = set(env.get("recent_cluster_ids") or [])
    if current:
        recent.add(current)
    all_of = list(boundary.get("all_of") or [])
    any_of = list(boundary.get("any_of") or [])
    none_of = list(boundary.get("none_of") or [])
    vol_in = list(boundary.get("volatility_in") or [])
    vol_not = list(boundary.get("volatility_not_in") or [])

    # Hard: forbidden primitives never approximate.
    if none_of and (current in none_of or recent.intersection(none_of)):
        return {"ok": False, "reason": "forbidden_primitive_present",
                "natural_language": "出现禁止基元，环境外抑制开仓",
                "hit": sorted(recent.intersection(none_of) or [current]),
                "approximate_allowed": False}

    soft = []
    exact = True
    all_ratio = 1.0
    if all_of:
        present = set(all_of) & recent
        all_ratio = len(present) / float(len(all_of))
        if all_ratio < 1.0:
            exact = False
            soft.append("all_of_partial")
        if all_ratio < APPROX_ALL_OF_MIN_RATIO:
            return {"ok": False, "reason": "required_primitives_incomplete",
                    "natural_language": "所需基元满足度过低，环境外抑制开仓",
                    "missing": sorted(set(all_of) - recent),
                    "all_of_ratio": round(all_ratio, 3),
                    "approximate_allowed": False}

    any_hit = False
    if any_of:
        any_hit = bool(current in any_of or recent.intersection(any_of))
        if not any_hit:
            exact = False
            soft.append("any_of_miss")
    else:
        any_hit = True

    vol = env.get("volatility_bucket")
    if vol_in and vol and vol not in vol_in:
        exact = False
        soft.append("volatility_not_in_allowlist")
    if vol_not and vol and vol in vol_not:
        exact = False
        soft.append("volatility_forbidden_bucket")

    if exact:
        return {"ok": True, "exact": True, "approximate": False,
                "match_mode": "exact", "reason": "in_environment",
                "natural_language": "当前微观环境符合策略适用边界",
                "cluster_id": current, "label": env.get("label")}

    # Soft near-env: allow D-grade exploratory open when not hard-forbidden.
    if allow_approximate and soft:
        # Require at least one constructive signal: partial all_of, or any_of
        # was declared (market still "nearby") or only volatility drifted.
        constructive = (
            ("all_of_partial" in soft and all_ratio >= APPROX_ALL_OF_MIN_RATIO)
            or ("any_of_miss" in soft and bool(any_of))
            or set(soft).issubset({
                "volatility_not_in_allowlist", "volatility_forbidden_bucket",
                "all_of_partial"})
        )
        if constructive:
            return {
                "ok": True, "exact": False, "approximate": True,
                "match_mode": "approximate",
                "reason": "approximate_environment",
                "position_grade": APPROX_POSITION_GRADE,
                "max_position_ratio": APPROX_POSITION_RATIO,
                "soft_penalties": soft,
                "all_of_ratio": round(all_ratio, 3),
                "any_of_hit": any_hit,
                "natural_language": (
                    "近似环境匹配（%s）：允许D级试探开仓（仓位上限%.1f%%），"
                    "不计入精确环境通过"
                    % ("、".join(soft), APPROX_POSITION_RATIO * 100.0)),
                "cluster_id": current, "label": env.get("label"),
            }

    if any_of and not any_hit:
        return {"ok": False, "reason": "current_primitive_not_allowed",
                "natural_language": "当前基元不在允许集合，环境外抑制开仓",
                "current": current, "soft_penalties": soft}
    if "volatility_not_in_allowlist" in soft:
        return {"ok": False, "reason": "volatility_bucket_not_allowed",
                "natural_language": "波动分位不在允许范围，环境外抑制开仓",
                "volatility_bucket": vol}
    if "volatility_forbidden_bucket" in soft:
        return {"ok": False, "reason": "volatility_bucket_forbidden",
                "natural_language": "处于禁止波动环境，环境外抑制开仓",
                "volatility_bucket": vol}
    return {"ok": False, "reason": "required_primitives_incomplete",
            "natural_language": "所需基元未同时满足，环境外抑制开仓",
            "missing": sorted(set(all_of) - recent) if all_of else []}


def matches_boundary_legacy_hard(env, boundary):
    """Backward-compatible hard-only matcher (tests / audits)."""
    return matches_boundary(env, boundary, allow_approximate=False)


def logic_audit_boundary(boundary, primitives):
    reasons = []
    any_of = list(boundary.get("any_of") or [])
    all_of = list(boundary.get("all_of") or [])
    none_of = list(boundary.get("none_of") or [])
    allowed = set(any_of) | set(all_of)
    if not allowed and not none_of:
        reasons.append("边界为空，既无允许也无禁止条件")
    prim_map = {p["cluster_id"]: p for p in primitives}
    for cid in sorted(allowed | set(none_of)):
        row = prim_map.get(cid)
        if not row:
            reasons.append("引用了未注册基元：%s" % cid)
            continue
        if int(row.get("sample_count") or 0) < MIN_BOUNDARY_PRIMITIVE_SAMPLES:
            reasons.append("基元样本过少（<%d）：%s" % (
                MIN_BOUNDARY_PRIMITIVE_SAMPLES, cid))
    if allowed and set(none_of).intersection(allowed):
        reasons.append("同一基元同时出现在允许与禁止集合，逻辑自相矛盾")
    if boundary.get("uses_outcome_labels"):
        reasons.append("边界声明使用了结果标签，构成循环论证")
    ok = not reasons
    return {
        "ok": ok, "reasons": reasons,
        "natural_language": (
            "环境边界逻辑审计通过" if ok else
            ("环境边界逻辑审计拒绝：" + "；".join(reasons))),
    }


def split_in_out_env_metrics(trade_rows, boundary, symbol):
    in_env, out_env, unknown = [], [], []
    for row in trade_rows or []:
        entry_ms = row.get("entry_ms") or row.get("signal_ts") or row.get("ts")
        try:
            entry_ms = int(entry_ms)
        except Exception:
            unknown.append(row)
            continue
        env = current_environment(symbol, candle_ms=entry_ms)
        if not env.get("available"):
            unknown.append(dict(row, env_tag="unknown"))
            continue
        decision = matches_boundary(
            env, dict(boundary or {}, final_approved=True),
            allow_approximate=False)
        tagged = dict(row)
        tagged["env_tag"] = "in" if decision.get("ok") else "out"
        tagged["env_cluster_id"] = env.get("cluster_id")
        (in_env if decision.get("ok") else out_env).append(tagged)

    def _stats(rows):
        pnls = []
        for row in rows:
            try:
                pnls.append(float(
                    row.get("pnl_ratio") if row.get("pnl_ratio") is not None
                    else row.get("pnl") or 0.0))
            except Exception:
                continue
        if not pnls:
            return {"trades": 0, "win_rate_pct": None,
                    "mean_net_return_pct": None, "max_drawdown_pct": None}
        wins = sum(1 for value in pnls if value > 0)
        cap = 1.0
        peak = 1.0
        max_dd = 0.0
        for value in pnls:
            cap *= (1.0 + float(value))
            peak = max(peak, cap)
            max_dd = max(max_dd, (peak - cap) / peak if peak else 0.0)
        return {
            "trades": len(pnls),
            "win_rate_pct": round(100.0 * wins / float(len(pnls)), 4),
            "mean_net_return_pct": round(
                100.0 * sum(pnls) / float(len(pnls)), 6),
            "max_drawdown_pct": round(100.0 * max_dd, 6),
        }

    return {
        "in_environment": _stats(in_env),
        "out_environment": _stats(out_env),
        "unknown_environment": _stats(unknown),
        "policy": "仅适用环境内样本参与收益否决；环境外与未知样本只记录不否决",
        "in_rows": in_env, "out_rows": out_env, "unknown_rows": unknown,
    }


def _deterministic_boundary_draft(symbol, direction_hint=None):
    primitives = list_registered_primitives(symbol, limit=12)
    if not primitives:
        primitives = list_registered_primitives(None, limit=8)
    ids = [p["cluster_id"] for p in primitives]
    none_of = []
    any_of = ids[:]
    if str(direction_hint or "").lower() in ("short", "sell", "空"):
        prefer = [p["cluster_id"] for p in primitives
                  if any(token in (str(p.get("label") or "") +
                                   str(p.get("description") or ""))
                         for token in ("卖", "流出", "净卖", "净流出"))]
        forbid = [p["cluster_id"] for p in primitives
                  if any(token in (str(p.get("label") or "") +
                                   str(p.get("description") or ""))
                         for token in ("买流", "脉冲流入", "净流入", "买方"))]
        if prefer:
            any_of = prefer
        if forbid:
            none_of = [cid for cid in forbid if cid not in any_of]
    return {
        "schema": "qiyu_env_boundary_v1",
        "any_of": any_of[:5], "all_of": [], "none_of": none_of[:3],
        "volatility_in": ["low", "mid"], "volatility_not_in": ["high"],
        "uses_outcome_labels": False,
        "natural_language": "确定性草案：仅在指定注册微观基元集合内、且非高波动分位时激活",
        "source": "deterministic_draft",
    }


def propose_audit_finalize_boundary(assignment_id, symbol, timeframe,
                                    strategy_key, direction_hint=None,
                                    skip_ai=False):
    primitives = list_registered_primitives(symbol, limit=12)
    vocab = list_registered_primitives(None, limit=8)
    context = {
        "assignment_id": assignment_id, "symbol": symbol,
        "timeframe": timeframe, "strategy_key": strategy_key,
        "direction_hint": direction_hint,
        "registered_primitives_symbol": primitives,
        "registered_primitives_global_top": vocab,
        "rules": {
            "language": "registered_micro_primitives_only",
            "no_outcome_labels": True,
            "no_circular_reasoning": True,
            "min_primitive_samples": MIN_BOUNDARY_PRIMITIVE_SAMPLES,
        },
    }
    if skip_ai or os.environ.get("QIYU_ENV_ADMISSION_SKIP_AI") == "1":
        draft = _deterministic_boundary_draft(symbol, direction_hint)
        logic = logic_audit_boundary(draft, primitives or vocab)
        draft["final_approved"] = bool(logic.get("ok"))
        draft["logic_audit"] = logic
        draft["pipeline"] = {"mode": "deterministic_dry_run"}
        draft["declared_by"] = "deterministic"
        draft["audited_by"] = "deterministic"
        draft["final_by"] = "deterministic"
        if draft["final_approved"]:
            draft["approved_at"] = _now()
        return draft
    try:
        from auto_trade_ai_consensus import (
            propose_environment_boundary,
            audit_environment_boundary,
            finalize_environment_boundary,
        )
    except Exception as exc:
        draft = _deterministic_boundary_draft(symbol, direction_hint)
        draft["final_approved"] = False
        draft["error"] = "ai_pipeline_unavailable:%s" % exc
        draft["logic_audit"] = logic_audit_boundary(draft, primitives or vocab)
        return draft
    proposal = propose_environment_boundary(context)
    if not proposal.get("ok"):
        return {"schema": "qiyu_env_boundary_v1", "final_approved": False,
                "error": proposal.get("error") or "deepseek_propose_failed",
                "pipeline": {"deepseek": proposal}}
    boundary = dict(proposal.get("boundary") or {})
    boundary["schema"] = "qiyu_env_boundary_v1"
    boundary["uses_outcome_labels"] = False
    qwen = audit_environment_boundary("qwen", context, boundary)
    chatgpt = finalize_environment_boundary("glm", context, boundary, qwen)
    logic = logic_audit_boundary(boundary, primitives or vocab)
    approved = bool(
        qwen.get("ok") and qwen.get("decision") == "APPROVE"
        and chatgpt.get("ok") and chatgpt.get("decision") == "APPROVE"
        and logic.get("ok"))
    boundary.update({
        "final_approved": approved,
        "declared_by": "deepseek", "audited_by": "qwen", "final_by": "glm",
        "approved_at": _now() if approved else None,
        "logic_audit": logic,
        "pipeline": {"deepseek": proposal, "qwen": qwen, "glm": chatgpt},
    })
    return boundary


def bind_boundary(assignment_id, boundary, extra=None):
    controls = _read(CONTROL_PATH, {"assignments": {}})
    assignments = dict(controls.get("assignments") or {})
    row = dict(assignments.get(assignment_id) or {})
    parts = assignment_id.split("|", 2)
    if len(parts) == 3:
        row.setdefault("symbol", parts[0])
        row.setdefault("timeframe", parts[1])
        row.setdefault("strategy_key", parts[2])
    row["environment_boundary"] = boundary
    row["environment_boundary_bound_at"] = _now()
    if extra:
        row.update(extra)
    assignments[assignment_id] = row
    controls["assignments"] = assignments
    controls["updated_at"] = _now()
    controls["updated_by"] = "auto_trade_environment_admission.bind_boundary"
    _atomic(CONTROL_PATH, controls)
    _append_audit({
        "event": "boundary_bound", "assignment_id": assignment_id,
        "final_approved": bool(boundary.get("final_approved")),
        "natural_language": (
            "%s 已绑定适用微观环境边界（终审%s）"
            % (assignment_id,
               "通过" if boundary.get("final_approved") else "未通过")),
    })
    return row


def _clear_mismatch(assignment_id):
    doc = _read(MISMATCH_PATH, {"counters": {}})
    counters = dict(doc.get("counters") or {})
    if assignment_id in counters:
        counters[assignment_id] = {
            "streak": 0, "cleared_at": _now(), "last_at": _now()}
        doc["counters"] = counters
        doc["updated_at"] = _now()
        _atomic(MISMATCH_PATH, doc)


def _note_mismatch(assignment_id, decision):
    doc = _read(MISMATCH_PATH, {"counters": {}})
    counters = dict(doc.get("counters") or {})
    row = dict(counters.get(assignment_id) or {})
    streak = int(row.get("streak") or 0) + 1
    row.update({
        "streak": streak, "last_reason": decision.get("reason"),
        "last_at": _now(),
        "last_natural_language": decision.get("natural_language"),
    })
    counters[assignment_id] = row
    doc["counters"] = counters
    doc["updated_at"] = _now()
    _atomic(MISMATCH_PATH, doc)
    if streak < ENV_MISMATCH_SLEEP_STREAK:
        return {"sleep": False, "streak": streak}
    controls = _read(CONTROL_PATH, {"assignments": {}})
    assignments = dict(controls.get("assignments") or {})
    item = dict(assignments.get(assignment_id) or {})
    item["pause_new_entries"] = True
    item["new_entries_allowed"] = False
    item["environment_dormant"] = True
    item["environment_dormant_at"] = _now()
    item["environment_dormant_reason"] = (
        "连续%d次因环境不匹配抑制开仓，进入休眠观察" % streak)
    assignments[assignment_id] = item
    controls["assignments"] = assignments
    controls["updated_at"] = _now()
    controls["updated_by"] = "auto_trade_environment_admission.mismatch_sleep"
    _atomic(CONTROL_PATH, controls)
    _append_audit({
        "event": "environment_dormant", "assignment_id": assignment_id,
        "streak": streak,
        "natural_language": item["environment_dormant_reason"],
    })
    return {"sleep": True, "streak": streak}


def runtime_gate(assignment_id, symbol=None, candle_ms=None,
                 volatility_bucket=None):
    """DISABLED: environment admission removed (designer 2026-07-24)."""
    return {
        "ok": True,
        "bypassed": True,
        "reason": "environment_admission_removed",
        "assignment_id": assignment_id,
        "natural_language": "环境准入门禁已移除，开仓不再检查基元匹配",
    }


def _legacy_runtime_gate(assignment_id, symbol=None, candle_ms=None,
                         volatility_bucket=None):
    controls = _read(CONTROL_PATH, {"assignments": {}})
    row = (controls.get("assignments") or {}).get(assignment_id) or {}
    boundary = row.get("environment_boundary") or {}
    symbol = symbol or row.get("symbol") or assignment_id.split("|", 1)[0]
    env = current_environment(symbol, candle_ms=candle_ms)
    if volatility_bucket:
        env["volatility_bucket"] = volatility_bucket
    decision = matches_boundary(env, boundary, allow_approximate=True)
    decision["environment"] = {
        "cluster_id": env.get("cluster_id"), "label": env.get("label"),
        "available": env.get("available"),
        "volatility_bucket": env.get("volatility_bucket"),
    }
    decision["assignment_id"] = assignment_id
    if decision.get("ok"):
        _clear_mismatch(assignment_id)
        if decision.get("approximate"):
            _append_audit({
                "event": "approximate_environment_open_allowed",
                "assignment_id": assignment_id,
                "max_position_ratio": decision.get("max_position_ratio"),
                "soft_penalties": decision.get("soft_penalties"),
                "natural_language": decision.get("natural_language"),
            })
    else:
        decision["mismatch_sleep"] = _note_mismatch(assignment_id, decision)
    return decision


def ensure_boundaries_for_active(skip_ai=False):
    controls = _read(CONTROL_PATH, {"assignments": {}})
    assignments = dict(controls.get("assignments") or {})
    bound, failed = [], []
    for assignment_id, row in sorted(assignments.items()):
        if any(assignment_id.startswith(p) for p in HARD_EXCLUDE_PREFIXES):
            continue
        state = str(row.get("audit_state") or "")
        if state not in ("conditional_frequency_probe", "passed_all"):
            continue
        existing = row.get("environment_boundary") or {}
        if (existing.get("final_approved")
                and existing.get("schema") == "qiyu_env_boundary_v1"):
            bound.append({"assignment_id": assignment_id,
                          "status": "already_bound"})
            continue
        symbol = row.get("symbol") or assignment_id.split("|")[0]
        timeframe = row.get("timeframe") or assignment_id.split("|")[1]
        strategy_key = row.get("strategy_key") or assignment_id.split("|", 2)[-1]
        direction = ("short" if "short" in strategy_key else
                     ("long" if "long" in strategy_key else None))
        boundary = propose_audit_finalize_boundary(
            assignment_id, symbol, timeframe, strategy_key,
            direction_hint=direction, skip_ai=skip_ai)
        bind_boundary(assignment_id, boundary)
        if boundary.get("final_approved"):
            # Clear temporary env-block if previously paused only for boundary.
            controls = _read(CONTROL_PATH, {"assignments": {}})
            item = dict((controls.get("assignments") or {}).get(assignment_id) or {})
            if item.get("environment_boundary_block"):
                item["environment_boundary_block"] = False
                if not item.get("environment_dormant"):
                    item["pause_new_entries"] = False
                    item["new_entries_allowed"] = True
                controls.setdefault("assignments", {})[assignment_id] = item
                controls["updated_at"] = _now()
                _atomic(CONTROL_PATH, controls)
            bound.append({"assignment_id": assignment_id,
                          "status": "bound_approved"})
        else:
            controls = _read(CONTROL_PATH, {"assignments": {}})
            item = dict((controls.get("assignments") or {}).get(assignment_id) or {})
            item["pause_new_entries"] = True
            item["new_entries_allowed"] = False
            item["environment_boundary_block"] = True
            item["environment_boundary_block_reason"] = (
                "适用环境边界未终审通过，暂停新开仓（已有仓位继续管理）")
            controls.setdefault("assignments", {})[assignment_id] = item
            controls["updated_at"] = _now()
            _atomic(CONTROL_PATH, controls)
            failed.append({"assignment_id": assignment_id,
                           "error": boundary.get("error")
                           or boundary.get("logic_audit")})
    status = {
        "ok": True, "created_at": _now(), "bound": bound, "failed": failed,
        "natural_language": (
            "环境准入绑定完成：已绑定/确认 %d 条，未通过终审暂停 %d 条。"
            "环境外信号只抑制不开仓，不作为策略淘汰依据。"
            % (len(bound), len(failed))),
    }
    _atomic(STATUS_PATH, status)
    return status


def reassess_btc15(skip_ai=False):
    symbol = "BTC-USDT-SWAP"
    timeframe = "15m"
    strategy_key = "btc15_dual_cycle_downtrend_reentry_short_ai"
    assignment_id = BTC15_ASSIGNMENT
    primitives = list_registered_primitives(symbol, limit=12)
    boundary = propose_audit_finalize_boundary(
        assignment_id, symbol, timeframe, strategy_key,
        direction_hint="short", skip_ai=skip_ai)
    audit = _read(AUTO_DIR / "live_strategy_retrospective_audit.json", {})
    audit_row = {}
    for row in audit.get("results") or []:
        if row.get("assignment_id") == assignment_id:
            audit_row = row
            break
    bt = ((audit_row.get("evidence") or {}).get("backtests") or {})
    triple = bt.get("triple_actual") or {}
    observed = bt.get("observed_base") or {}
    sell_ids = set(boundary.get("any_of") or [])
    forbid_ids = set(boundary.get("none_of") or [])
    occupancy = {"in_env_windows": 0, "out_env_windows": 0, "total": 0}
    if TELEMETRY_DB.exists():
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT a.cluster_id FROM microstructure_primitive_assignments a "
                "JOIN microstructure_primitive_clusters c ON c.cluster_id=a.cluster_id "
                "WHERE a.symbol=? AND c.state='descriptive_registered' "
                "ORDER BY a.end_ms DESC LIMIT 2000", (symbol,)).fetchall()
        finally:
            conn.close()
        for (cluster_id,) in rows:
            occupancy["total"] += 1
            if cluster_id in forbid_ids:
                occupancy["out_env_windows"] += 1
            elif not sell_ids or cluster_id in sell_ids:
                occupancy["in_env_windows"] += 1
            else:
                occupancy["out_env_windows"] += 1
    logic = boundary.get("logic_audit") or logic_audit_boundary(
        boundary, primitives)
    mean_ok = float(triple.get("mean_net_return_pct") or -999) > 0
    dd = float(triple.get("max_drawdown_pct") or 0)
    adverse_share = (
        occupancy["out_env_windows"] / float(occupancy["total"])
        if occupancy["total"] else 0.0)
    # Operator rule: if a valid environment boundary can be framed
    # (allow + forbid primitives, logic audit pass, triple-cost mean still >0),
    # grant C-grade conditional probe.  Occupancy share is supporting evidence,
    # not a hard blocker — historical L2 extreme replay is unavailable.
    framed = (
        bool(boundary.get("final_approved")) and logic.get("ok") and mean_ok
        and bool(boundary.get("any_of") or boundary.get("all_of"))
        and bool(boundary.get("none_of") or boundary.get("volatility_not_in"))
        and occupancy["in_env_windows"] >= 20
    )
    frames_dd = framed and (dd >= 50.0 or adverse_share >= 0.20)
    granted = False
    if frames_dd:
        bind_boundary(assignment_id, boundary, extra={
            "audit_state": "conditional_frequency_probe",
            "pause_new_entries": False, "new_entries_allowed": True,
            "max_grade": "C", "max_position_ratio": 0.15,
            "cold_start_trades_remaining": 5,
            "cold_start_position_ratio": 0.075,
            "env_conditional_c_probe": True,
            "btc15_hard_exclude_exception": True,
            "lifecycle_shadow": False,
            "reason": (
                "BTC15m 经环境准入重估：高回撤主要落在可定义恶劣/禁入微观环境；"
                "适用环境边界已终审通过，授予C级条件探针（冷启动）"),
        })
        granted = True
    else:
        bind_boundary(assignment_id, boundary, extra={
            "btc15_reassessed_at": _now(),
            "btc15_hard_exclude_exception": False,
            "pause_new_entries": True, "new_entries_allowed": False,
            "reason": "BTC15m 环境重估未达C级解锁条件，维持只读影子",
        })
    report = {
        "ok": True, "created_at": _now(), "assignment_id": assignment_id,
        "boundary": boundary, "logic_audit": logic,
        "retrospective": {
            "disposition": audit_row.get("disposition"),
            "observed_base": {
                "trades": observed.get("trades"),
                "mean_net_return_pct": observed.get("mean_net_return_pct"),
                "max_drawdown_pct": observed.get("max_drawdown_pct"),
                "win_rate_pct": observed.get("win_rate_pct"),
            },
            "triple_actual": {
                "trades": triple.get("trades"),
                "mean_net_return_pct": triple.get("mean_net_return_pct"),
                "max_drawdown_pct": triple.get("max_drawdown_pct"),
                "win_rate_pct": triple.get("win_rate_pct"),
            },
            "note": (
                "三倍摩擦最大回撤约 %.2f%%；历史L2极端重放不可用，"
                "本次用前向注册微观基元框定适用/禁入环境。" % dd),
        },
        "occupancy_proxy": occupancy,
        "adverse_env_share": round(adverse_share, 4),
        "c_probe_granted": granted,
        "natural_language": (
            ("BTC15分钟策略环境重估完成：已框定卖压类基元为适用环境、"
             "买流脉冲/高波动为禁入。三倍成本下最大回撤约%.1f%%。%s")
            % (dd,
               ("已授予C级条件探针实盘测试资格（冷启动仓位）。"
                if granted else
                "尚未满足C级解锁条件，继续只读影子观察。"))),
    }
    _atomic(BTC15_REPORT, report)
    _append_audit({
        "event": "btc15_environment_reassessment",
        "assignment_id": assignment_id, "c_probe_granted": granted,
        "natural_language": report["natural_language"],
    })
    return report


def run_once(skip_ai=False, include_btc15=True):
    """DISABLED: environment admission removed (designer 2026-07-24)."""
    status = {
        "ok": True,
        "disabled": True,
        "reason": "environment_admission_removed",
        "created_at": _now(),
        "natural_language": "环境准入门禁已停用；开仓不再检查基元匹配。",
    }
    _atomic(STATUS_PATH, status)
    return status


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-ai", action="store_true")
    parser.add_argument("--btc15-only", action="store_true")
    parser.add_argument("--bind-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_once(skip_ai=True),
                     ensure_ascii=False, indent=2, sort_keys=True))
