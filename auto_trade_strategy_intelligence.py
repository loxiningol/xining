# -*- coding: utf-8 -*-
"""Experience, attribution, failure-memory and lifecycle layer."""
from __future__ import print_function

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import calendar
import glob
import hashlib
import json
import os
import tempfile
import time


ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
CONFIG_PATH = ROOT / "strategy_configs" / "experimental_strategies.json"
DSL_CONFIG_PATH = ROOT / "strategy_configs" / "ai_dsl_strategies.json"
RATING_PATH = AUTO_DIR / "strategy_ratings.json"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"
FEATURES = ["open", "high", "low", "close", "ema6", "ema17", "ema19",
            "ema53", "ema75", "ema95", "k", "d", "j", "cci",
            "macd_stick", "atr14", "rsi14", "z20", "h1_slope4"]


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read(path, default):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(delete=False, dir=str(path.parent),
                                         mode="w", encoding="utf-8")
    try:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush(); handle.close(); Path(handle.name).replace(path)
    except Exception:
        try: Path(handle.name).unlink()
        except Exception: pass
        raise


def _sha(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def ensure_schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS market_snapshots (
      symbol TEXT NOT NULL, timeframe TEXT NOT NULL, candle_ts INTEGER NOT NULL,
      candle_time TEXT NOT NULL, regime TEXT NOT NULL, pattern_json TEXT NOT NULL,
      indicators_json TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL,
      PRIMARY KEY(symbol,timeframe,candle_ts)
    );
    CREATE TABLE IF NOT EXISTS signal_observations (
      observation_id TEXT PRIMARY KEY, symbol TEXT, timeframe TEXT,
      strategy_key TEXT, candle_ts INTEGER, signal TEXT, triggered INTEGER,
      not_triggered_reason TEXT, regime TEXT, indicators_json TEXT,
      payload_json TEXT NOT NULL, observed_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS condition_attributions (
      attribution_id TEXT PRIMARY KEY, symbol TEXT, timeframe TEXT,
      strategy_key TEXT, condition_key TEXT, condition_label TEXT,
      direction TEXT, delta_trades REAL, delta_win_rate REAL,
      delta_expectancy REAL, delta_drawdown REAL, delta_loss_streak REAL,
      baseline_json TEXT NOT NULL, variant_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS failure_experiences (
      fingerprint TEXT PRIMARY KEY, stage TEXT NOT NULL, strategy_key TEXT,
      symbol TEXT, timeframe TEXT, reason_code TEXT NOT NULL,
      payload_json TEXT NOT NULL, occurrences INTEGER NOT NULL,
      first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS research_hypotheses (
      hypothesis_hash TEXT PRIMARY KEY, provider TEXT NOT NULL,
      strategy_key TEXT, hypothesis_type TEXT NOT NULL, state TEXT NOT NULL,
      hypothesis_json TEXT NOT NULL, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS hypothesis_reviews (
      batch_hash TEXT NOT NULL, hypothesis_id TEXT NOT NULL,
      provider TEXT NOT NULL, decision TEXT NOT NULL,
      review_json TEXT NOT NULL, reviewed_at TEXT NOT NULL,
      PRIMARY KEY(batch_hash,hypothesis_id,provider)
    );
    CREATE TABLE IF NOT EXISTS strategy_versions (
      version_hash TEXT PRIMARY KEY, strategy_key TEXT NOT NULL,
      version_type TEXT NOT NULL, state TEXT NOT NULL, parent_hash TEXT,
      definition_json TEXT NOT NULL, evidence_json TEXT,
      consensus_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS lifecycle_metrics (
      metric_id TEXT PRIMARY KEY, version_hash TEXT NOT NULL,
      strategy_key TEXT NOT NULL, symbol TEXT, timeframe TEXT,
      expected_json TEXT, actual_json TEXT, regime TEXT,
      deviation_state TEXT NOT NULL, action TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    """)
    conn.commit()


def _pattern(row, regime):
    values = {key: _float(row.get(key)) for key in FEATURES}
    ema = [values.get("ema6"), values.get("ema17"), values.get("ema19"),
           values.get("ema53"), values.get("ema75")]
    if all(value is not None for value in ema):
        if ema == sorted(ema, reverse=True): order = "short_to_long_descending"
        elif ema == sorted(ema): order = "short_to_long_ascending"
        else: order = "mixed"
    else: order = "unknown"
    open_px, close_px = values.get("open"), values.get("close")
    candle = "bullish" if close_px is not None and open_px is not None and close_px > open_px else "bearish"
    k, d, cci = values.get("k"), values.get("d"), values.get("cci")
    oscillator = ("oversold" if cci is not None and cci <= -100 else
                  "overbought" if cci is not None and cci >= 100 else "neutral")
    return {"regime": regime, "ema_order": order, "candle": candle,
            "oscillator_zone": oscillator,
            "kd_relation": "k_above_d" if k is not None and d is not None and k > d else "k_not_above_d"}


def _float(value, default=None):
    try:
        value = float(value)
        return value if value == value and abs(value) != float("inf") else default
    except Exception:
        return default


def _cache_path(symbol, timeframe):
    instance = str(symbol).split("-")[0].lower()
    return AUTO_DIR / ("formal_%s_%s_candles_cache.json" % (instance, timeframe))


def _fallback_regime(frame, index):
    """Short-cache regime label used when the long-window engine is undecided."""
    try:
        close = float(frame["close"].iloc[index])
        ema53 = float(frame["ema53"].iloc[index])
        older = float(frame["ema53"].iloc[max(0, index-12)])
        atr = float(frame["atr14"].iloc[index])
        start = max(0, index-96)
        median_atr = float(frame["atr14"].iloc[start:index+1].median())
        volatility = "high_vol" if median_atr > 0 and atr >= median_atr*1.35 else "normal_vol"
        slope = (ema53-older)/older if older else 0.0
        trend = "uptrend" if close > ema53 and slope > 0.001 else (
            "downtrend" if close < ema53 and slope < -0.001 else "range"
        )
        return "%s_%s" % (trend, volatility)
    except Exception:
        return "unknown"


def ingest_market_snapshots(conn, assignments, lookback=1400):
    ensure_schema(conn)
    import backtest_engine_v2 as bt
    inserted = 0; errors = []; seen = set()
    for assignment in assignments:
        symbol = str(assignment.get("symbol") or "").upper()
        timeframe = str(assignment.get("timeframe") or "1h").lower()
        token = (symbol, timeframe)
        if not symbol or token in seen: continue
        seen.add(token)
        cache = _read(_cache_path(symbol, timeframe), {})
        candles = list(cache.get("candles") or [])[-int(lookback):]
        if len(candles) < 80:
            errors.append({"symbol": symbol, "timeframe": timeframe,
                           "error": "candle cache insufficient"}); continue
        try:
            previous = conn.execute(
                "SELECT MAX(candle_ts) FROM market_snapshots WHERE symbol=? AND timeframe=?",
                (symbol, timeframe)).fetchone()
            previous_ts = int(previous[0] or 0)
            latest_cache_ts = int((candles[-1] or {}).get("ts") or 0)
            # The daemon rewrites signal/cache files every minute.  Avoid all
            # pandas/indicator work until a genuinely new closed candle exists.
            if latest_cache_ts and latest_cache_ts <= previous_ts:
                continue
            import pandas as pd
            frame = pd.DataFrame(candles)
            frame.index = pd.to_datetime(frame.pop("ts"), unit="ms", utc=True)
            frame = bt.precompute_indicators(frame[["open", "high", "low", "close"]], timeframe=timeframe)
            frame = frame.replace([float("inf"), -float("inf")], float("nan")).ffill().bfill()
            regimes = bt._market_regime_labels(frame, timeframe=timeframe)
            for index in range(len(frame)):
                row = {key: _float(frame.iloc[index].get(key)) for key in FEATURES if key in frame.columns}
                candle_ts = int(frame.index[index].timestamp()*1000)
                if candle_ts <= previous_ts:
                    continue
                regime = regimes[index]
                if not regime or regime == "unknown":
                    regime = _fallback_regime(frame, index)
                before = conn.total_changes
                conn.execute("INSERT OR IGNORE INTO market_snapshots VALUES(?,?,?,?,?,?,?,?,?)",
                             (symbol, timeframe, candle_ts, str(frame.index[index]), regime,
                              json.dumps(_pattern(row, regime), ensure_ascii=False, sort_keys=True),
                              json.dumps(row, ensure_ascii=False, sort_keys=True),
                              str(cache.get("source") or "candle_cache"), _now()))
                inserted += conn.total_changes-before
        except Exception as exc:
            errors.append({"symbol": symbol, "timeframe": timeframe, "error": str(exc)})
    conn.commit()
    return {"inserted": inserted, "markets": len(seen), "errors": errors}


def ingest_signal_observations(conn):
    ensure_schema(conn); inserted = 0
    for path in sorted(glob.glob(str(AUTO_DIR / "formal_*signal*.json"))):
        payload = _read(path, {})
        if not isinstance(payload, dict) or not payload.get("strategy_key"): continue
        symbol = str(payload.get("symbol") or "").upper()
        timeframe = str(payload.get("timeframe") or "1h").lower()
        candle_ts = int(payload.get("signal_candle_ts") or 0)
        if not symbol or not candle_ts: continue
        snapshot = conn.execute(
            "SELECT regime,indicators_json FROM market_snapshots WHERE symbol=? AND timeframe=? AND candle_ts<=? ORDER BY candle_ts DESC LIMIT 1",
            (symbol, timeframe, candle_ts)).fetchone()
        regime, indicators = snapshot if snapshot else ("unknown", "{}")
        signal = str(payload.get("signal") or "none")
        diagnosis = payload.get("not_triggered_diagnosis") or {}
        reason = payload.get("reason") or (
            "triggered" if signal in ("long", "short")
            else diagnosis.get("reason_code") or "entry_predicate_false"
        )
        obs_id = _sha({"path": path, "strategy": payload.get("strategy_key"),
                       "candle_ts": candle_ts, "signal": signal,
                       "params": payload.get("params") or {},
                       "dsl_hash": (payload.get("entry_info") or {}).get("dsl_hash")})
        before = conn.total_changes
        conn.execute("INSERT OR IGNORE INTO signal_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (obs_id, symbol, timeframe, payload.get("strategy_key"), candle_ts,
                      signal, 1 if signal in ("long", "short") else 0, str(reason),
                      regime, indicators,
                      json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), _now()))
        inserted += conn.total_changes-before
    conn.commit(); return {"inserted": inserted}


def enrich_trade_cases(conn):
    ensure_schema(conn); updated = 0
    rows = conn.execute("SELECT case_id,symbol,timeframe,payload_json FROM cases").fetchall()
    for case_id, symbol, timeframe, raw in rows:
        try: payload = json.loads(raw)
        except Exception: continue
        if payload.get("experience_enrichment_version") == 1: continue
        timeframe = timeframe or (payload.get("entry_data") or {}).get("timeframe") or "1h"
        def snapshot_for(value, fallback=None):
            if not value:
                return None
            try:
                stamp = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
                utc_stamp = stamp-timedelta(hours=8)
                ts = int(calendar.timegm(utc_stamp.timetuple())*1000)
            except Exception: return None
            row = conn.execute("SELECT candle_ts,regime,pattern_json,indicators_json FROM market_snapshots WHERE symbol=? AND timeframe=? AND candle_ts<=? ORDER BY candle_ts DESC LIMIT 1",
                               (symbol, timeframe, ts)).fetchone()
            if not row:
                indicators = {}
                for key in FEATURES:
                    value = _float((fallback or {}).get(key))
                    if value is not None:
                        indicators[key] = value
                return ({"candle_ts": ts, "regime": "unknown",
                         "pattern": _pattern(indicators, "unknown"),
                         "indicators": indicators,
                         "source": "trade_payload_fallback"}
                        if indicators else None)
            return {"candle_ts": row[0], "regime": row[1],
                    "pattern": json.loads(row[2]), "indicators": json.loads(row[3])}
        payload["entry_snapshot"] = snapshot_for(
            payload.get("opened_at"), payload.get("entry_data") or {}
        )
        payload["exit_snapshot"] = snapshot_for(
            payload.get("closed_at"), payload.get("close_data") or {}
        )
        payload["execution_process"] = {
            "open_order": payload.get("open_order") or payload.get("entry_order_payload"),
            "close_order": payload.get("close_order") or payload.get("close_reconciliation"),
            "attached_stop_loss": payload.get("attached_stop_loss"),
            "stop_loss_verification": payload.get("exchange_side_stop_verified"),
            "close_type": payload.get("close_type"), "close_reason": payload.get("close_reason"),
            "order_id": payload.get("order_id"), "close_order_id": payload.get("close_order_id")}
        payload["experience_enrichment_version"] = 1
        conn.execute("UPDATE cases SET payload_json=? WHERE case_id=?",
                     (json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), case_id)); updated += 1
    conn.commit(); return {"updated": updated}


def record_attributions(conn, assignment, strategy_row, baseline_metrics, evaluated):
    ensure_schema(conn); count = 0
    labels = strategy_row.get("param_meta") or {}
    for variant, evidence in evaluated:
        candidate = (evidence.get("runs") or {}).get("candidate", {}).get("0.009") or {}
        if not candidate: continue
        key = variant.get("changed_parameter")
        direction = "relax_or_lower" if variant.get("new_value", 0) < variant.get("old_value", 0) else "tighten_or_raise"
        payload = {"assignment": assignment, "variant": variant,
                   "failed_gates": [name for name, passed in (evidence.get("gates") or {}).items() if not passed]}
        attr_id = _sha({"assignment": assignment, "key": key,
                        "new": variant.get("new_value"), "baseline": baseline_metrics})
        conn.execute("INSERT OR REPLACE INTO condition_attributions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (attr_id, assignment.get("symbol"), assignment.get("timeframe"),
                      assignment.get("strategy_key"), key,
                      (labels.get(key) or {}).get("label") or key, direction,
                      _float(candidate.get("trades"), 0)-_float(baseline_metrics.get("trades"), 0),
                      _float(candidate.get("win_rate_pct"), 0)-_float(baseline_metrics.get("win_rate_pct"), 0),
                      _float(candidate.get("expectancy_pct"), 0)-_float(baseline_metrics.get("expectancy_pct"), 0),
                      _float(candidate.get("max_drawdown_pct"), 0)-_float(baseline_metrics.get("max_drawdown_pct"), 0),
                      _float(candidate.get("max_loss_streak"), 0)-_float(baseline_metrics.get("max_loss_streak"), 0),
                      json.dumps(baseline_metrics, ensure_ascii=False, sort_keys=True),
                      json.dumps(payload, ensure_ascii=False, sort_keys=True), _now())); count += 1
    conn.commit(); return count


def record_dsl_attributions(conn, assignment, baseline_definition,
                            baseline_metrics, evaluated_definitions):
    """Exact leave-one/change-one attribution for validated DSL conditions."""
    ensure_schema(conn); count = 0
    for definition, evidence in evaluated_definitions:
        origin = definition.get("origin") or {}
        mutation = origin.get("mutation")
        condition_key = origin.get("condition_id")
        if mutation not in ("threshold", "remove_condition", "add_condition",
                            "condition_combination"):
            continue
        candidate = (evidence.get("runs") or {}).get("candidate", {}).get("0.009") or {}
        if not candidate:
            continue
        attr_id = _sha({"assignment": assignment,
                        "base": baseline_definition.get("key"),
                        "candidate": definition.get("key"),
                        "condition": condition_key, "mutation": mutation})
        variant_payload = {
            "mutation": mutation, "condition_id": condition_key,
            "candidate_key": definition.get("key"),
            "candidate_hash": _sha(definition),
        }
        conn.execute("INSERT OR REPLACE INTO condition_attributions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (attr_id, assignment.get("symbol"), assignment.get("timeframe"),
                      baseline_definition.get("key"), condition_key or mutation,
                      condition_key or mutation, mutation,
                      _float(candidate.get("trades"), 0)-_float(baseline_metrics.get("trades"), 0),
                      _float(candidate.get("win_rate_pct"), 0)-_float(baseline_metrics.get("win_rate_pct"), 0),
                      _float(candidate.get("expectancy_pct"), 0)-_float(baseline_metrics.get("expectancy_pct"), 0),
                      _float(candidate.get("max_drawdown_pct"), 0)-_float(baseline_metrics.get("max_drawdown_pct"), 0),
                      _float(candidate.get("max_loss_streak"), 0)-_float(baseline_metrics.get("max_loss_streak"), 0),
                      json.dumps(baseline_metrics, ensure_ascii=False, sort_keys=True),
                      json.dumps(variant_payload, ensure_ascii=False, sort_keys=True), _now()))
        count += 1
    conn.commit(); return count


def record_failure(conn, stage, strategy_key, symbol, timeframe, reason_code, payload):
    ensure_schema(conn)
    normalized = {"stage": stage, "strategy_key": strategy_key, "symbol": symbol,
                  "timeframe": timeframe, "reason_code": reason_code,
                  "provider": payload.get("provider") if isinstance(payload, dict) else None,
                  "hypothesis_id": payload.get("hypothesis_id") if isinstance(payload, dict) else None,
                  "candidate_hash": payload.get("candidate_hash") if isinstance(payload, dict) else None,
                  "failed_gates": sorted(payload.get("failed_gates") or []) if isinstance(payload, dict) else []}
    fingerprint = _sha(normalized); now = _now()
    existing = conn.execute("SELECT occurrences FROM failure_experiences WHERE fingerprint=?", (fingerprint,)).fetchone()
    if existing:
        conn.execute("UPDATE failure_experiences SET occurrences=?,payload_json=?,last_seen=? WHERE fingerprint=?",
                     (int(existing[0])+1, json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), now, fingerprint))
    else:
        conn.execute("INSERT INTO failure_experiences VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (fingerprint, stage, strategy_key, symbol, timeframe, reason_code,
                      json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), 1, now, now))
    conn.commit(); return fingerprint


def research_context(conn, assignment, limit=24):
    ensure_schema(conn); key = assignment.get("strategy_key")
    symbol = assignment.get("symbol"); timeframe = assignment.get("timeframe") or "1h"
    attrs = conn.execute("SELECT condition_key,condition_label,direction,delta_trades,delta_win_rate,delta_expectancy,delta_drawdown,delta_loss_streak FROM condition_attributions WHERE strategy_key=? AND symbol=? AND timeframe=? ORDER BY created_at DESC LIMIT ?", (key, symbol, timeframe, limit)).fetchall()
    failures = conn.execute("SELECT stage,reason_code,occurrences,payload_json FROM failure_experiences WHERE strategy_key=? AND symbol=? AND timeframe=? ORDER BY last_seen DESC LIMIT ?", (key, symbol, timeframe, limit)).fetchall()
    cases = conn.execute("SELECT payload_json FROM cases WHERE strategy_key=? AND symbol=? AND COALESCE(timeframe,'1h')=? ORDER BY occurred_at DESC LIMIT 15", (key, symbol, timeframe)).fetchall()
    observations = conn.execute(
        "SELECT triggered,not_triggered_reason,regime,indicators_json,payload_json "
        "FROM signal_observations WHERE strategy_key=? AND symbol=? AND timeframe=? ORDER BY candle_ts DESC LIMIT ?",
        (key, symbol, timeframe, limit)).fetchall()
    regimes = Counter()
    outcomes = []
    for (raw,) in cases:
        try:
            payload = json.loads(raw); snap = payload.get("entry_snapshot") or {}
            regimes[snap.get("regime") or "unknown"] += 1
            outcomes.append({"regime": snap.get("regime"), "pattern": snap.get("pattern"),
                             "pnl": payload.get("pnl"), "close_type": payload.get("close_type")})
        except Exception: pass
    recent_observations = []
    for triggered, reason, regime, indicators_raw, payload_raw in observations:
        try:
            payload = json.loads(payload_raw)
            diagnosis = payload.get("not_triggered_diagnosis") or {}
            recent_observations.append({
                "triggered": bool(triggered), "reason": reason, "regime": regime,
                "indicators": json.loads(indicators_raw or "{}"),
                "single_parameter_blockers": diagnosis.get("single_parameter_blockers") or [],
            })
        except Exception:
            pass
    return {"assignment": assignment, "recent_real_cases": outcomes,
            "real_case_regimes": dict(regimes),
            "recent_signal_observations": recent_observations,
            "condition_attributions": [dict(zip(("condition_key", "label", "direction", "delta_trades", "delta_win_rate", "delta_expectancy", "delta_drawdown", "delta_loss_streak"), row)) for row in attrs],
            "failure_memory": [{"stage": row[0], "reason": row[1], "occurrences": row[2],
                                "detail": json.loads(row[3])} for row in failures]}


def store_hypotheses(conn, provider_results):
    ensure_schema(conn); stored = 0
    for result in provider_results:
        provider = result.get("provider")
        for item in result.get("hypotheses") or []:
            digest = _sha({"provider": provider, "hypothesis": item})
            before = conn.total_changes
            conn.execute("INSERT OR IGNORE INTO research_hypotheses VALUES(?,?,?,?,?,?,?,?)",
                         (digest, provider, item.get("strategy_key"), item.get("kind") or "unknown",
                          "proposed", json.dumps(item, ensure_ascii=False, sort_keys=True), _now(), _now()))
            stored += conn.total_changes-before
    conn.commit(); return stored


def store_hypothesis_convergence(conn, convergence):
    ensure_schema(conn); stored = 0
    batch_hash = convergence.get("batch_hash")
    for hypothesis in (convergence.get("admitted") or []) + (convergence.get("rejected") or []):
        hid = hypothesis.get("hypothesis_id")
        state = "research_converged" if hypothesis in (convergence.get("admitted") or []) else "research_rejected"
        conn.execute(
            "INSERT OR REPLACE INTO research_hypotheses VALUES(?,?,?,?,?,?,?,?)",
            (hid, "consensus", hypothesis.get("strategy_key"),
             hypothesis.get("kind") or "unknown", state,
             json.dumps(hypothesis, ensure_ascii=False, sort_keys=True, default=str),
             _now(), _now()),
        )
        for vote in hypothesis.get("votes") or []:
            conn.execute(
                "INSERT OR REPLACE INTO hypothesis_reviews VALUES(?,?,?,?,?,?)",
                (batch_hash, hid, vote.get("provider"), vote.get("decision") or "REJECT",
                 json.dumps(vote, ensure_ascii=False, sort_keys=True, default=str), _now()),
            )
            stored += 1
    conn.commit(); return stored


def runtime_paused(symbol, timeframe, strategy_key):
    controls = _read(CONTROL_PATH, {})
    aid = "%s|%s|%s" % (str(symbol).upper(), str(timeframe).lower(), strategy_key)
    row = (controls.get("assignments") or {}).get(aid) or {}
    return bool(row.get("pause_new_entries")), row


def _has_active_position(strategy_key):
    for path in glob.glob(str(AUTO_DIR / "formal_v6_state*.json")):
        current = (_read(path, {}) or {}).get("current")
        if isinstance(current, dict) and current.get("strategy_key") == strategy_key and current.get("status") not in ("closed", "failed", "cancelled"):
            return True
    return False


def _trade_is_win(payload):
    if isinstance(payload.get("profit"), bool):
        return payload.get("profit")
    for key in ("net_return_ratio", "pnl_ratio", "profit_rate", "return_ratio",
                "realized_pnl", "pnl"):
        value = _float(payload.get(key))
        if value is not None:
            return value > 0
    return None


def _post_deployment_actual(conn, strategy_key, symbol, timeframe, deployed_at):
    rows = conn.execute(
        "SELECT payload_json FROM cases WHERE strategy_key=? AND kind='real_trade' "
        "AND symbol=? AND COALESCE(timeframe,'1h')=? AND occurred_at>=? ORDER BY occurred_at",
        (strategy_key, symbol, timeframe or "1h", deployed_at)).fetchall()
    values = []; regimes = Counter()
    for (raw,) in rows:
        try:
            payload = json.loads(raw); win = _trade_is_win(payload)
            if win is not None:
                values.append(bool(win))
                snapshot = payload.get("entry_snapshot") or {}
                regimes[snapshot.get("regime") or "unknown"] += 1
        except Exception:
            pass
    streak = best = 0
    for win in values:
        if win: streak = 0
        else:
            streak += 1; best = max(best, streak)
    def window_rate(size):
        sample = values[-size:]
        return (sum(1 for value in sample if value)/float(len(sample))*100.0
                if len(sample) >= size else None)
    return {"trades": len(values), "wins": sum(1 for value in values if value),
            "losses": sum(1 for value in values if not value),
            "win_rate_pct": (sum(1 for value in values if value)/float(len(values))*100.0
                             if values else None),
            "max_loss_streak": best, "recent_loss_streak": streak,
            "win_rate_5_pct": window_rate(5),
            "win_rate_10_pct": window_rate(10),
            "win_rate_15_pct": window_rate(15),
            "entry_regimes": dict(regimes)}


def lifecycle_monitor(conn):
    ensure_schema(conn); ratings = _read(RATING_PATH, {}).get("ratings_by_id") or {}
    controls = _read(CONTROL_PATH, {"schema": "qiyu_runtime_controls_v1", "assignments": {}})
    actions = []
    deployments = conn.execute("SELECT d.candidate_hash,d.strategy_key,d.deployed_at,c.candidate_json,c.evidence_json FROM deployments d JOIN candidates c ON c.candidate_hash=d.candidate_hash WHERE d.rolled_back_at IS NULL").fetchall()
    for version_hash, key, deployed_at, candidate_raw, evidence_raw in deployments:
        candidate = json.loads(candidate_raw); evidence = json.loads(evidence_raw or "{}")
        aid = "%s|%s|%s" % (candidate.get("symbol"), candidate.get("timeframe"), key)
        rating = ratings.get(aid) or {}
        actual = _post_deployment_actual(
            conn, key, candidate.get("symbol"), candidate.get("timeframe"), deployed_at
        )
        losses = int(actual.get("losses") or 0); real_n = int(actual.get("trades") or 0)
        expected = ((evidence.get("runs") or {}).get("candidate") or {}).get("0.009") or {}
        expected_win = _float(expected.get("win_rate_pct"), 0)
        actual_win = _float(actual.get("win_rate_pct"))
        latest_regime = conn.execute(
            "SELECT regime FROM market_snapshots WHERE symbol=? AND timeframe=? "
            "ORDER BY candle_ts DESC LIMIT 1",
            (candidate.get("symbol"), candidate.get("timeframe"))).fetchone()
        regime = latest_regime[0] if latest_regime else "unknown"
        regime_perf = expected.get("regime_performance") or {}
        favorable_regimes = set(
            name for name, bucket in regime_perf.items()
            if int(bucket.get("trades") or 0) >= 2
            and _float(bucket.get("expectancy_pct"), 0) > 0
        )
        regime_mismatch = bool(
            favorable_regimes and regime not in favorable_regimes
            and regime != "unknown"
        )
        def worsened(size):
            value = actual.get("win_rate_%d_pct" % size)
            return value is not None and _float(value, 0) < expected_win-20
        bad5, bad10, bad15 = worsened(5), worsened(10), worsened(15)
        warning_gap = bad5 and not bad10
        decay_observe = bad5 and bad10 and int(actual.get("recent_loss_streak") or 0) >= 2
        decayed = bad5 and bad10 and bad15 and regime_mismatch
        action = "observe"
        if decayed:
            controls.setdefault("assignments", {})[aid] = {"pause_new_entries": True,
                "reason": "ai_version_live_decay", "version_hash": version_hash, "updated_at": _now()}
            action = "pause_new_entries"
            if not _has_active_position(key):
                if candidate.get("type") == "dsl_strategy":
                    config = _read(DSL_CONFIG_PATH, {})
                    target = next((row for row in config.get("strategies") or []
                                   if row.get("key") == key), None)
                    if target and target.get("live_enabled") is True:
                        target["live_enabled"] = False
                        target["auto_trade_eligible"] = False
                        _atomic(DSL_CONFIG_PATH, config)
                        conn.execute("UPDATE deployments SET rolled_back_at=?,rollback_reason=? WHERE candidate_hash=?",
                                     (_now(), "live_decay", version_hash))
                        action = "paused_and_dsl_disabled"
                else:
                    config = _read(CONFIG_PATH, {}); target = next((row for row in config.get("strategies") or [] if row.get("key") == key), None)
                    name = candidate.get("changed_parameter")
                    if target and (target.get("params") or {}).get(name) == candidate.get("new_value"):
                        target["params"][name] = candidate.get("old_value")
                        target["modified_at_beijing"] = _now()
                        target["version"] = str(target.get("version") or "") + "+rollback-" + version_hash[:8]
                        _atomic(CONFIG_PATH, config)
                        conn.execute("UPDATE deployments SET rolled_back_at=?,rollback_reason=? WHERE candidate_hash=?",
                                     (_now(), "live_decay", version_hash)); action = "paused_and_rolled_back"
            record_failure(conn, "live_lifecycle", key, candidate.get("symbol"),
                           candidate.get("timeframe"), "live_decay",
                           {"expected": expected, "actual": actual,
                            "failed_gates": ["post_deployment_live_gap"]})
        elif decay_observe:
            action = "degradation_observe"
        elif warning_gap:
            action = "degradation_warning"
        actual_with_rating = dict(actual); actual_with_rating["current_rating"] = rating
        actual_with_rating["current_regime"] = regime
        actual_with_rating["expected_favorable_regimes"] = sorted(favorable_regimes)
        actual_with_rating["regime_mismatch"] = regime_mismatch
        metric_id = _sha({"version": version_hash, "time": _now()[:13], "actual": actual_with_rating})
        conn.execute("INSERT OR REPLACE INTO lifecycle_metrics VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (metric_id, version_hash, key, candidate.get("symbol"), candidate.get("timeframe"),
                      json.dumps(expected, ensure_ascii=False, sort_keys=True),
                      json.dumps(actual_with_rating, ensure_ascii=False, sort_keys=True),
                      regime, "decayed" if decayed else ("decay_observe" if decay_observe else ("warning" if warning_gap else "normal")), action, _now()))
        actions.append({"strategy_key": key, "version_hash": version_hash, "action": action})
    _atomic(CONTROL_PATH, controls); conn.commit(); return actions
