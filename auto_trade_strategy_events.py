# -*- coding: utf-8 -*-
"""Unified strategy_event taxonomy + append-only event stream.

Schema: qiyu_strategy_event_v1

Fail-safe: emit() never raises into the trading path.
Does NOT open/close/change SL/TP/leverage/size.
"""
from __future__ import print_function

import json
import os
import socket
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
EVENT_STREAM = AUTO_DIR / "strategy_events.jsonl"
EVENT_META = AUTO_DIR / "strategy_events_meta.json"
COVERAGE_MATRIX_PATH = AUTO_DIR / "STEP_B_event_coverage_matrix.json"

# Forecast/expectancy paths ask for the same 7d/14d funnels many times.  The
# event stream is append-only and can be hundreds of MB, so cache compact
# per-strategy summaries briefly instead of rescanning the whole JSONL once per
# strategy and once again for coverage.
_EVENT_SUMMARY_CACHE = {}
_EVENT_SUMMARY_CACHE_LOCK = threading.Lock()
_EVENT_SUMMARY_CACHE_TTL_SEC = 30.0

# Full production funnel taxonomy (required).
FUNNEL_EVENT_TYPES = (
    "market_evaluated",
    "raw_signal_generated",
    "signal_filtered",
    "risk_rejected",
    "position_conflict_rejected",
    "order_attempted",
    "order_rejected",
    "order_partially_filled",
    "order_filled",
    "position_opened",
    "exit_signal_generated",
    "stop_loss_triggered",
    "take_profit_triggered",
    "manual_close",
    "strategy_close",
    "position_closed",
)

CONTROL_EVENT_TYPES = (
    "strategy_mounted",
    "strategy_unmounted",
    "strategy_paused",
    "strategy_resumed",
    "param_version_changed",
    "auto_open_changed",
    "grade_size_permission_changed",
)

EVENT_TYPES = FUNNEL_EVENT_TYPES + CONTROL_EVENT_TYPES

# Stages that must appear at least once for "full funnel coverage" on a strategy.
FULL_COVERAGE_CORE = (
    "market_evaluated",
    "raw_signal_generated",
    "order_attempted",
    "order_filled",
    "position_opened",
    "position_closed",
)

# Map legacy daemon / executor event names → taxonomy where possible.
LEGACY_MAP = {
    "tick_no_signal": "market_evaluated",
    "no_signal": "market_evaluated",
    "signal_short_auto_open_disabled": "risk_rejected",
    "signal_short_but_auto_open_disabled": "risk_rejected",
    "signal_long_but_auto_open_disabled": "risk_rejected",
    "signal_short_cooldown": "risk_rejected",
    "signal_short_gate_disabled": "risk_rejected",
    "signal_short_but_formal_auto_trading_not_authorized": "risk_rejected",
    "signal_long_but_formal_auto_trading_not_authorized": "risk_rejected",
    "signal_short_but_cooldown": "risk_rejected",
    "signal_long_but_cooldown": "risk_rejected",
    "signal_short_but_already_processed": "signal_filtered",
    "signal_long_but_already_processed": "signal_filtered",
    "signal_short_but_missing_candle_id": "signal_filtered",
    "signal_long_but_missing_candle_id": "signal_filtered",
    "signal_scan_waiting_fresh_closed_candle": "signal_filtered",
    "signal_blocked_by_lifecycle": "risk_rejected",
    "signal_blocked_by_grade": "risk_rejected",
    "signal_blocked_priority_coordinator_error": "risk_rejected",
    "signal_suppressed_by_global_priority": "signal_filtered",
    "signal_waiting_global_priority_arbitration": "signal_filtered",
    "signal_suppressed_by_portfolio_risk_policy": "risk_rejected",
    "auto_opened_short": "order_filled",
    "auto_opened_long": "order_filled",
    "auto_open_failed": "order_rejected",
    "auto_open_attempted_gate_authorized": "order_attempted",
    "strategy_opened": "position_opened",
    "strategy_open_failed": "order_rejected",
    "strategy_open_rejected": "order_rejected",
    "strategy_open_unknown": "order_rejected",
    "protective_close_attempted": "stop_loss_triggered",
    "protective_close_attempted_once": "stop_loss_triggered",
    "strategy_close_attempted": "strategy_close",
    "strategy_take_profit_authoritative_exit": "take_profit_triggered",
    "strategy_take_profit_close_attempted": "take_profit_triggered",
    "strategy_timed_forced_close": "strategy_close",
    "strategy_closed": "position_closed",
    "strategy_closed_reconciled": "position_closed",
    "formal_manual_close": "manual_close",
    "position_exists_managed": "position_conflict_rejected",
    "position_exists_other_strategy_instance": "position_conflict_rejected",
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(text):
    if isinstance(text, (int, float)):
        raw = float(text)
        if raw > 1e12:
            raw /= 1000.0
        try:
            return datetime.fromtimestamp(raw)
        except Exception:
            return None
    text = str(text or "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except Exception:
            continue
    return None


def _daemon_instance_id(cfg=None):
    cfg = cfg or {}
    host = socket.gethostname() or "host"
    sym = str(cfg.get("symbol") or os.environ.get("VECTOR_TRADE_SYMBOL") or "")
    tf = str(cfg.get("timeframe") or os.environ.get("VECTOR_TRADE_TIMEFRAME") or "")
    pid = os.getpid()
    return "%s:%s:%s:%s" % (host, sym, tf, pid)


def _runtime_config_version(cfg=None):
    cfg = cfg or {}
    try:
        import hashlib
        blob = json.dumps({
            "symbol": cfg.get("symbol"),
            "timeframe": cfg.get("timeframe"),
            "strategy_keys": cfg.get("strategy_keys"),
            "stop_loss_pct": cfg.get("stop_loss_pct"),
            "leverage": cfg.get("leverage"),
            "allow_auto_open": cfg.get("allow_auto_open"),
            "full_position_ratio": cfg.get("full_position_ratio"),
        }, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]
    except Exception:
        return ""


def make_event(event_type, strategy_id="", symbol="", timeframe="",
               strategy_version="", reason_code="", order_id="",
               position_id="", source_data_version="",
               daemon_instance_id="", runtime_config_version="",
               extra=None, cfg=None):
    et = str(event_type or "")
    if et in LEGACY_MAP:
        et = LEGACY_MAP[et]
    cfg = cfg if isinstance(cfg, dict) else {}
    payload = {
        "schema": "qiyu_strategy_event_v1",
        "event_id": str(uuid.uuid4()),
        "strategy_id": str(strategy_id or ""),
        "strategy_version": str(strategy_version or cfg.get("strategy_version") or ""),
        "symbol": str(symbol or cfg.get("symbol") or ""),
        "timeframe": str(timeframe or cfg.get("timeframe") or ""),
        "event_type": et,
        "reason_code": str(reason_code or ""),
        "timestamp": _now(),
        "ts": time.time(),
        "source_data_version": str(source_data_version or ""),
        "order_id": str(order_id or ""),
        "position_id": str(position_id or ""),
        "daemon_instance_id": str(
            daemon_instance_id or _daemon_instance_id(cfg)),
        "runtime_config_version": str(
            runtime_config_version or _runtime_config_version(cfg)),
    }
    if isinstance(extra, dict) and extra:
        payload["extra"] = extra
    return payload


def emit(**kwargs):
    """Append one strategy_event. Never raises."""
    try:
        row = make_event(**kwargs)
        EVENT_STREAM.parent.mkdir(parents=True, exist_ok=True)
        with EVENT_STREAM.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return row
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def emit_from_executor(event_type, strategy_key="", symbol="", timeframe="",
                       order_id="", position_id="", reason_code="",
                       stop_loss_pct=None, extra=None):
    """Bridge formal_v6_executor events → unified taxonomy. Fail-safe."""
    try:
        cfg = {
            "symbol": symbol or os.environ.get("VECTOR_TRADE_SYMBOL") or "",
            "timeframe": timeframe or os.environ.get("VECTOR_TRADE_TIMEFRAME") or "",
            "stop_loss_pct": stop_loss_pct,
        }
        raw = str(event_type or "")
        et = LEGACY_MAP.get(raw, raw)
        # Always emit the mapped taxonomy event.
        emit(
            event_type=et,
            strategy_id=strategy_key,
            symbol=cfg["symbol"],
            timeframe=cfg["timeframe"],
            reason_code=reason_code or raw,
            order_id=order_id,
            position_id=position_id,
            cfg=cfg,
            extra=dict(extra or {}, executor_event=raw),
        )
        # Enrich open/close chains with adjacent funnel stages.
        if raw in ("strategy_opened",):
            emit(
                event_type="order_filled",
                strategy_id=strategy_key,
                symbol=cfg["symbol"],
                timeframe=cfg["timeframe"],
                reason_code=raw,
                order_id=order_id,
                position_id=position_id,
                cfg=cfg,
                extra={"from": "executor_open_chain"},
            )
        if raw in ("strategy_closed", "strategy_closed_reconciled"):
            # already mapped to position_closed; also tag exit flavor if reason known
            pass
        if raw in ("strategy_open_failed", "strategy_open_rejected",
                   "strategy_open_unknown"):
            emit(
                event_type="order_attempted",
                strategy_id=strategy_key,
                symbol=cfg["symbol"],
                timeframe=cfg["timeframe"],
                reason_code=raw,
                order_id=order_id,
                cfg=cfg,
                extra={"from": "executor_reject_chain"},
            )
        return True
    except Exception:
        return False


def emit_from_daemon_action(action, cfg=None, signal=None, result=None):
    """Best-effort bridge from formal_daemon tick actions → taxonomy."""
    try:
        cfg = cfg or {}
        signal = signal or {}
        result = result or {}
        sk = (
            signal.get("strategy_key")
            or (result.get("selected_signal") or {}).get("strategy_key")
            or cfg.get("strategy_key")
            or ((cfg.get("strategy_keys") or [None])[0])
            or ""
        )
        symbol = cfg.get("symbol") or ""
        timeframe = str(cfg.get("timeframe") or "")
        action = str(action or result.get("action") or "")
        open_result = result.get("open_result") if isinstance(result, dict) else {}
        if not isinstance(open_result, dict):
            open_result = {}
        close_result = result.get("close_result") if isinstance(result, dict) else {}
        if not isinstance(close_result, dict):
            close_result = {}
        order_id = str(
            open_result.get("ordId")
            or (open_result.get("ack") or {}).get("ordId")
            or ""
        )
        position_id = str(
            open_result.get("position_id")
            or (open_result.get("current") or {}).get("position_id")
            or ""
        )

        common = dict(
            strategy_id=sk,
            symbol=symbol,
            timeframe=timeframe,
            cfg=cfg,
            order_id=order_id,
            position_id=position_id,
        )

        # --- Open chain ---
        if action in ("auto_open_attempted_gate_authorized",
                      "auto_open_attempt", "opening", "order_attempted"):
            emit(event_type="order_attempted", reason_code=action,
                 extra={"daemon_action": action}, **common)
            if open_result.get("ok"):
                emit(event_type="order_filled", reason_code=action,
                     extra={"daemon_action": action, "open_ok": True}, **common)
                emit(event_type="position_opened", reason_code=action,
                     extra={"daemon_action": action}, **common)
            else:
                # Still emit rejection even if open_result missing (unknown fail)
                emit(event_type="order_rejected", reason_code=action,
                     extra={
                         "daemon_action": action,
                         "error": open_result.get("error"),
                         "open_ok": False,
                     }, **common)
            return True

        if action in ("auto_opened_short", "auto_opened_long"):
            emit(event_type="order_attempted", reason_code=action, **common)
            emit(event_type="order_filled", reason_code=action, **common)
            emit(event_type="position_opened", reason_code=action, **common)
            return True

        if action in ("auto_open_failed",):
            emit(event_type="order_attempted", reason_code=action, **common)
            emit(event_type="order_rejected", reason_code=action, **common)
            return True

        # --- Exit chain ---
        close_check = result.get("strategy_close_check") if isinstance(result, dict) else None
        if isinstance(close_check, dict) and close_check.get("should_close"):
            emit(event_type="exit_signal_generated", reason_code=action,
                 extra={"exit_info": close_check.get("exit_info")}, **common)

        if action in ("strategy_close_attempted", "strategy_timed_forced_close"):
            emit(event_type="strategy_close", reason_code=action,
                 extra={"daemon_action": action}, **common)
            if close_result.get("ok") or close_result.get("position_absent_verified"):
                emit(event_type="position_closed", reason_code=action,
                     extra={"daemon_action": action}, **common)
            return True

        if action in ("strategy_take_profit_authoritative_exit",
                      "strategy_take_profit_close_attempted"):
            emit(event_type="take_profit_triggered", reason_code=action, **common)
            if close_result.get("ok") or close_result.get("position_absent_verified"):
                emit(event_type="position_closed", reason_code=action, **common)
            return True

        if action in ("protective_close_attempted",
                      "protective_close_attempted_once"):
            emit(event_type="stop_loss_triggered", reason_code=action, **common)
            if close_result.get("ok") or (result.get("manage_result") or {}).get("ok"):
                emit(event_type="position_closed", reason_code=action, **common)
            return True

        # --- Standard map ---
        et = LEGACY_MAP.get(action)
        reason = action
        if signal.get("signal") in ("short", "long", True) and action in (
                "no_signal", ""):
            et = "raw_signal_generated"
        if action in ("no_signal", "tick_no_signal"):
            et = "market_evaluated"
        elif action in ("observe_only",) and signal.get("signal") in ("short", "long"):
            et = "raw_signal_generated"
        elif ("position_exists" in action) or ("conflict" in action):
            et = "position_conflict_rejected"
        elif action.startswith("signal_") and (
                "but" in action or "disabled" in action or "blocked" in action
                or "suppressed" in action or "cooldown" in action
                or "not_authorized" in action):
            if "already_processed" in action or "missing_candle" in action \
                    or "waiting" in action or "priority" in action:
                et = "signal_filtered"
            else:
                et = "risk_rejected"

        if not et:
            if action in ("daemon_disabled", "candles_not_ready", "position_managed",
                          "daemon_started", "daemon_stopped", "daemon_error",
                          "tick_daemon_disabled"):
                return None
            et = LEGACY_MAP.get(action)
            if not et:
                if action.startswith("signal_"):
                    et = "risk_rejected" if (
                        "but" in action or "disabled" in action
                    ) else "signal_filtered"
                else:
                    return None

        return emit(
            event_type=et,
            reason_code=reason,
            extra={"daemon_action": action, "signal": signal.get("signal")},
            **common
        )
    except Exception:
        return None


def iter_events(since=None, strategy_id=None, limit=None):
    if not EVENT_STREAM.exists():
        return []
    out = []
    try:
        with EVENT_STREAM.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if strategy_id and str(row.get("strategy_id") or "") != str(strategy_id):
                    continue
                if since:
                    ts = _parse_dt(row.get("timestamp")) or (
                        datetime.fromtimestamp(row["ts"])
                        if isinstance(row.get("ts"), (int, float)) else None)
                    if ts and ts < since:
                        continue
                out.append(row)
                if limit and len(out) >= limit:
                    break
    except Exception:
        return out
    return out


def _new_event_summary_bucket():
    return {
        "counts": defaultdict(int),
        "event_n": 0,
        "first_ts": None,
        "last_ts": None,
        "open_ts": None,
        "hold_hours": [],
    }


def _update_event_summary_bucket(bucket, row, parsed_ts):
    event_type = row.get("event_type") or ""
    bucket["counts"][event_type] += 1
    bucket["event_n"] += 1
    if parsed_ts:
        first_ts = bucket.get("first_ts")
        last_ts = bucket.get("last_ts")
        bucket["first_ts"] = parsed_ts if first_ts is None else min(first_ts, parsed_ts)
        bucket["last_ts"] = parsed_ts if last_ts is None else max(last_ts, parsed_ts)
    if event_type in ("order_filled", "position_opened"):
        bucket["open_ts"] = parsed_ts
    elif event_type in (
        "position_closed", "stop_loss_triggered", "take_profit_triggered",
        "strategy_close", "manual_close",
    ):
        opened = bucket.get("open_ts")
        if opened and parsed_ts:
            bucket["hold_hours"].append(
                max(0.0, (parsed_ts - opened).total_seconds() / 3600.0)
            )
        bucket["open_ts"] = None


def _event_summaries(days=7):
    """Scan the event stream once per window and retain compact summaries."""
    try:
        days = max(1, int(days or 7))
    except Exception:
        days = 7
    now_ts = time.time()
    with _EVENT_SUMMARY_CACHE_LOCK:
        cached = _EVENT_SUMMARY_CACHE.get(days)
        if (
            isinstance(cached, dict)
            and now_ts - float(cached.get("loaded_at") or 0)
            < _EVENT_SUMMARY_CACHE_TTL_SEC
        ):
            return cached.get("summaries") or {}

        after = datetime.now() - timedelta(days=days)
        summaries = {}
        if EVENT_STREAM.exists():
            try:
                with EVENT_STREAM.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except Exception:
                            continue
                        parsed_ts = _parse_dt(row.get("timestamp")) or (
                            datetime.fromtimestamp(row["ts"])
                            if isinstance(row.get("ts"), (int, float)) else None
                        )
                        if parsed_ts and parsed_ts < after:
                            continue
                        strategy_id = str(row.get("strategy_id") or "")
                        if not strategy_id:
                            continue
                        symbol = str(row.get("symbol") or "").upper()
                        strategy = summaries.setdefault(strategy_id, {})
                        for bucket_key in ("*", symbol):
                            bucket = strategy.setdefault(
                                bucket_key, _new_event_summary_bucket())
                            _update_event_summary_bucket(bucket, row, parsed_ts)
            except Exception:
                summaries = summaries or {}

        for strategy in summaries.values():
            for bucket in strategy.values():
                bucket.pop("open_ts", None)
                bucket["counts"] = dict(bucket.get("counts") or {})
        _EVENT_SUMMARY_CACHE[days] = {
            "loaded_at": now_ts,
            "summaries": summaries,
        }
        return summaries


def _summary_for_strategy(strategy_id, days=7, symbol=None):
    strategy = _event_summaries(days).get(str(strategy_id or "")) or {}
    if symbol is None:
        return strategy.get("*") or _new_event_summary_bucket()
    symbol = str(symbol or "").upper()
    buckets = []
    if strategy.get(symbol):
        buckets.append(strategy[symbol])
    if symbol and strategy.get(""):
        buckets.append(strategy[""])
    if not buckets:
        return _new_event_summary_bucket()
    if len(buckets) == 1:
        return buckets[0]
    merged = _new_event_summary_bucket()
    for bucket in buckets:
        for event_type, count in (bucket.get("counts") or {}).items():
            merged["counts"][event_type] += int(count or 0)
        merged["event_n"] += int(bucket.get("event_n") or 0)
        if bucket.get("first_ts"):
            merged["first_ts"] = (
                bucket["first_ts"] if merged["first_ts"] is None
                else min(merged["first_ts"], bucket["first_ts"])
            )
        if bucket.get("last_ts"):
            merged["last_ts"] = (
                bucket["last_ts"] if merged["last_ts"] is None
                else max(merged["last_ts"], bucket["last_ts"])
            )
        merged["hold_hours"].extend(bucket.get("hold_hours") or [])
    merged.pop("open_ts", None)
    merged["counts"] = dict(merged["counts"])
    return merged


def coverage_for_strategy(strategy_id, days=7):
    """Return wired/emitted/never-seen matrix row + 7d coverage quality."""
    summary = _summary_for_strategy(strategy_id, days=days)
    counts = defaultdict(int, summary.get("counts") or {})
    first_ts = summary.get("first_ts")
    last_ts = summary.get("last_ts")
    event_n = int(summary.get("event_n") or 0)
    span_days = 0.0
    if first_ts and last_ts:
        span_days = max(0.0, (last_ts - first_ts).total_seconds() / 86400.0)

    per_type = {}
    for et in FUNNEL_EVENT_TYPES:
        n = int(counts.get(et) or 0)
        per_type[et] = {
            "wired": True,  # taxonomy + emit bridges present after STEP B
            "emitted": n > 0,
            "count": n,
            "status": "emitted" if n > 0 else "never-seen",
        }

    core_seen = sum(1 for et in FULL_COVERAGE_CORE if counts.get(et))
    full_taxonomy = all(counts.get(et) for et in FULL_COVERAGE_CORE)
    # event_n>0 alone is NOT a 7-day base. Require span + core stages.
    sufficient_7d = bool(
        full_taxonomy
        and span_days >= 6.5
        and event_n >= 50
    )
    source_layer = (
        "event-derived" if sufficient_7d
        else ("legacy-derived" if event_n else "prior-derived")
    )
    if not event_n:
        source_layer = "uncalibrated"
    return {
        "strategy_id": strategy_id,
        "window_days": days,
        "event_n": event_n,
        "span_days": round(span_days, 4),
        "core_stages_seen": core_seen,
        "core_stages_required": len(FULL_COVERAGE_CORE),
        "full_taxonomy_seen": full_taxonomy,
        "sufficient_7d_event_coverage": sufficient_7d,
        "source_layer": source_layer,
        "insufficient_source": not sufficient_7d,
        "per_type": per_type,
        "note": (
            "event_n>0 alone ≠ 7-day full coverage base; "
            "legacy remains primary until sufficient_7d_event_coverage"
        ),
    }


def build_event_coverage_matrix(strategy_ids=None, days=7):
    """strategy × event_type matrix for STEP_B deliverable."""
    if not strategy_ids:
        strategy_ids = []
        try:
            import glob as _glob
            for path in sorted(_glob.glob(str(AUTO_DIR / "formal_daemon_config*.json"))):
                try:
                    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(cfg, dict) or cfg.get("enabled") is False:
                    continue
                if not cfg.get("allow_auto_open"):
                    continue
                for key in (cfg.get("strategy_keys") or []):
                    if key and key not in strategy_ids:
                        strategy_ids.append(key)
        except Exception:
            pass
    matrix = {
        "schema": "qiyu_step_b_event_coverage_matrix_v1",
        "updated_at": _now(),
        "window_days": days,
        "required_event_types": list(FUNNEL_EVENT_TYPES),
        "full_coverage_core": list(FULL_COVERAGE_CORE),
        "strategies": {},
        "summary": {},
    }
    any_sufficient = False
    for sid in strategy_ids:
        row = coverage_for_strategy(sid, days=days)
        matrix["strategies"][sid] = row
        any_sufficient = any_sufficient or row.get("sufficient_7d_event_coverage")
    matrix["summary"] = {
        "strategy_count": len(strategy_ids),
        "any_sufficient_7d_full_coverage": any_sufficient,
        "legacy_still_primary": not any_sufficient,
        "stream_path": str(EVENT_STREAM),
        "stream_exists": EVENT_STREAM.exists(),
    }
    try:
        EVENT_META.parent.mkdir(parents=True, exist_ok=True)
        EVENT_META.write_text(
            json.dumps(matrix["summary"], ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception:
        pass
    return matrix


def classify_frequency_source_layer(strategy_id, event_funnel=None, legacy_funnel=None,
                                    prior_method=None):
    """Layer display: event-derived / legacy-derived / prior-derived / uncalibrated.

    Stop using legacy as primary only after ≥7 days full event coverage.
    """
    cov = coverage_for_strategy(strategy_id, days=7)
    if cov.get("sufficient_7d_event_coverage"):
        return {
            "source_layer": "event-derived",
            "primary": "strategy_events",
            "insufficient_source": False,
            "coverage": cov,
        }
    event_n = int((event_funnel or {}).get("event_n") or cov.get("event_n") or 0)
    legacy = legacy_funnel or {}
    if legacy.get("legacy_fallback") or legacy.get("source", "").startswith("legacy"):
        return {
            "source_layer": "legacy-derived",
            "primary": "legacy_signal_files_plus_fills",
            "insufficient_source": True,
            "coverage": cov,
            "note": "event stream thin/incomplete; legacy still primary",
        }
    if prior_method in ("research_accepted_density", "weak_prior_new_mount"):
        return {
            "source_layer": "prior-derived" if prior_method != "weak_prior_new_mount"
            else "uncalibrated",
            "primary": prior_method,
            "insufficient_source": True,
            "coverage": cov,
        }
    if event_n > 0:
        return {
            "source_layer": "legacy-derived",
            "primary": "hybrid_events_incomplete",
            "insufficient_source": True,
            "coverage": cov,
            "note": "event_n>0 but not ≥7d full taxonomy — do not treat as event primary",
        }
    return {
        "source_layer": "uncalibrated",
        "primary": "none",
        "insufficient_source": True,
        "coverage": cov,
    }


def funnel_from_events(strategy_id, symbol=None, timeframe=None, days=7):
    """Recompute funnel rates from strategy_events primary stream."""
    summary = _summary_for_strategy(
        strategy_id, days=days, symbol=symbol if symbol else None)
    counts = defaultdict(int, summary.get("counts") or {})
    hold_hours = list(summary.get("hold_hours") or [])
    event_n = int(summary.get("event_n") or 0)

    raw = counts["raw_signal_generated"]
    filtered = counts["signal_filtered"]
    risk = counts["risk_rejected"]
    conflict = counts["position_conflict_rejected"]
    attempted = counts["order_attempted"] + counts["order_filled"] + counts["order_rejected"]
    filled = counts["order_filled"] + counts["position_opened"]
    # Deduplicate fill counting when both order_filled and position_opened fire.
    filled = max(counts["order_filled"], counts["position_opened"])
    exits = (counts["position_closed"] + counts["stop_loss_triggered"]
             + counts["take_profit_triggered"] + counts["strategy_close"]
             + counts["manual_close"])
    evaluated = counts["market_evaluated"] + raw
    cov = coverage_for_strategy(strategy_id, days=days)
    sufficient = bool(cov.get("sufficient_7d_event_coverage"))

    def _rate(num, den):
        if den <= 0:
            return None
        return round(float(num) / float(den), 6)

    return {
        "schema": "qiyu_event_funnel_v1",
        "source": "strategy_events.jsonl",
        "window_days": days,
        "strategy_id": strategy_id,
        "counts": dict(counts),
        "raw_signal_frequency": raw,
        "market_evaluated": evaluated,
        "filter_pass_rate": _rate(raw - filtered, raw) if raw else None,
        "risk_rejection_rate": _rate(risk, max(raw, 1)),
        "position_conflict_rate": _rate(conflict, max(raw, 1)),
        "order_attempt_rate": _rate(attempted, max(raw, 1)),
        "fill_rate": _rate(filled, max(attempted, 1)),
        "exit_rate": _rate(exits, max(filled, 1)),
        "signals": raw,
        "attempts": attempted,
        "fills": filled,
        "exits": exits,
        "blocked": risk + conflict,
        "filtered": filtered,
        "avg_holding_duration_hours": (
            round(sum(hold_hours) / float(len(hold_hours)), 4) if hold_hours else None),
        "event_n": event_n,
        # primary only when ≥7d full coverage — not merely event_n>0
        "primary": sufficient,
        "sufficient_7d_event_coverage": sufficient,
        "source_layer": cov.get("source_layer"),
        "insufficient_source": not sufficient,
        "note": (
            "primary event stream (≥7d full taxonomy)"
            if sufficient else
            "events present but coverage thin; caller should keep legacy/prior primary"
        ),
    }


def strategy_pool_version(controls=None, daemon_globs=None):
    """Hash of mounted keys + pause/auto_open/grade/size — for forecast freshness."""
    import hashlib
    import glob as _glob
    controls = controls or {}
    if not controls:
        try:
            controls = json.loads(
                (AUTO_DIR / "strategy_runtime_controls.json").read_text(encoding="utf-8"))
        except Exception:
            controls = {}
    assignments = controls.get("assignments") or {}
    parts = []
    paths = daemon_globs or list(_glob.glob(str(AUTO_DIR / "formal_daemon_config*.json")))
    for path in sorted(paths):
        try:
            cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(cfg, dict) or cfg.get("enabled") is False:
            continue
        symbol = cfg.get("symbol")
        tf = str(cfg.get("timeframe") or "")
        allow = bool(cfg.get("allow_auto_open"))
        for key in (cfg.get("strategy_keys") or []):
            aid = "%s|%s|%s" % (symbol, tf, key)
            row = assignments.get(aid) or {}
            parts.append("|".join([
                str(symbol), str(tf), str(key),
                "1" if allow else "0",
                "1" if row.get("pause_new_entries") else "0",
                str(row.get("lifecycle_grade") or ""),
                str(row.get("max_position_ratio") or ""),
                str(row.get("stop_loss_pct") or cfg.get("stop_loss_pct") or ""),
                str(row.get("param_version") or row.get("strategy_version") or ""),
            ]))
    digest = hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]
    return {
        "strategy_pool_version": digest,
        "mounted_count": len(parts),
        "fingerprint_lines": parts,
        "computed_at": _now(),
    }
