# -*- coding: utf-8 -*-
"""Outcome-free discovery of slow microstructure descriptive primitives.

The process consumes only forward public telemetry already sampled by
auto_trade_microstructure_collector.py.  It deliberately receives no future
price, trade outcome, strategy or PnL label.  Clustering is deterministic and
bounded; DeepSeek may name clusters, but Qwen and OpenAI must both approve the
description before it becomes registered research vocabulary.
"""
from __future__ import print_function

import hashlib
import json
import math
import os
import sqlite3
import statistics
import time
from pathlib import Path


ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
DB_PATH = AUTO_DIR / "microstructure_telemetry.db"
STATUS_PATH = AUTO_DIR / "microstructure_primitives_status.json"
SCHEMA_VERSION = "slow_micro_primitives_v2_target_local_stability"
WINDOW_SIZE = 5
MAX_SAMPLES_PER_SYMBOL = 720
MIN_TOTAL_WINDOWS = 80

FEATURES = (
    "spread_level", "spread_trend", "depth_imbalance_level",
    "depth_imbalance_trend", "depth_imbalance_volatility",
    "flow_level", "flow_trend", "flow_volatility",
    "aggression_level", "aggression_trend", "log_depth_level",
    "log_depth_trend", "trade_activity", "flow_5s", "flow_15s",
    "flow_60s", "flow_5s_minus_60s",
)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _sha(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _connect():
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS microstructure_windows (
      window_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, symbol TEXT NOT NULL,
      start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
      features_json TEXT NOT NULL, descriptor_text TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_micro_window_symbol_time
      ON microstructure_windows(symbol,end_ms);
    CREATE TABLE IF NOT EXISTS microstructure_primitive_clusters (
      cluster_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
      fingerprint TEXT NOT NULL, state TEXT NOT NULL, label TEXT NOT NULL,
      description TEXT NOT NULL, sample_count INTEGER NOT NULL,
      centroid_json TEXT NOT NULL, exemplar_json TEXT NOT NULL,
      review_json TEXT NOT NULL, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_micro_primitive_state
      ON microstructure_primitive_clusters(state,updated_at);
    CREATE TABLE IF NOT EXISTS microstructure_primitive_assignments (
      assignment_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
      window_id TEXT NOT NULL, symbol TEXT NOT NULL, end_ms INTEGER NOT NULL,
      cluster_id TEXT NOT NULL, distance REAL NOT NULL,
      assigned_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_micro_primitive_assignment_symbol
      ON microstructure_primitive_assignments(symbol,end_ms);
    CREATE TABLE IF NOT EXISTS microstructure_primitive_runs (
      run_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL, state TEXT NOT NULL,
      samples_used INTEGER NOT NULL, windows_built INTEGER NOT NULL,
      clusters_found INTEGER NOT NULL, api_calls_used INTEGER NOT NULL,
      payload_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS microstructure_primitive_versions (
      version_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
      cluster_id TEXT NOT NULL, symbol TEXT NOT NULL,
      raw_fingerprint TEXT NOT NULL, centroid_json TEXT NOT NULL,
      identity_distance REAL, stability_json TEXT NOT NULL,
      drift_state TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_micro_primitive_version_cluster
      ON microstructure_primitive_versions(cluster_id,created_at);
    CREATE TABLE IF NOT EXISTS microstructure_event_associations (
      association_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
      symbol TEXT NOT NULL, cluster_id TEXT NOT NULL,
      event_code TEXT NOT NULL, horizon_samples INTEGER NOT NULL,
      cluster_total INTEGER NOT NULL, cluster_hits INTEGER NOT NULL,
      background_total INTEGER NOT NULL, background_hits INTEGER NOT NULL,
      lift REAL NOT NULL, lift_ci95_low REAL NOT NULL,
      eligible INTEGER NOT NULL, payload_json TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_micro_event_symbol
      ON microstructure_event_associations(symbol,created_at);
    CREATE TABLE IF NOT EXISTS microstructure_morphology_reports (
      report_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
      left_symbol TEXT NOT NULL, right_symbol TEXT NOT NULL,
      left_cluster_id TEXT NOT NULL, right_cluster_id TEXT NOT NULL,
      centroid_distance REAL NOT NULL, event_profile_distance REAL,
      state TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
    );
    """)
    try:
        os.chmod(str(DB_PATH), 0o600)
    except Exception:
        pass
    return conn


def _subflow(raw, key):
    try:
        return float((json.loads(raw or "{}").get(str(key)) or {})
                     .get("trade_flow_imbalance") or 0.0)
    except Exception:
        return 0.0


def _mean(values):
    return sum(values)/float(len(values)) if values else 0.0


def _slope(values):
    if len(values) < 2:
        return 0.0
    return (values[-1]-values[0])/float(len(values)-1)


def _std(values):
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def _load_samples(conn):
    symbols = [row[0] for row in conn.execute(
        "SELECT DISTINCT symbol FROM microstructure_samples ORDER BY symbol")]
    output = {}
    for symbol in symbols:
        rows = conn.execute(
            "SELECT observed_ms,half_spread_rate,bid_depth_usd,ask_depth_usd,"
            "depth_imbalance,trade_flow_imbalance,aggression_acceleration,"
            "trade_count,subwindows_json FROM microstructure_samples "
            "WHERE symbol=? ORDER BY observed_ms DESC LIMIT ?",
            (symbol, MAX_SAMPLES_PER_SYMBOL)).fetchall()
        output[symbol] = list(reversed(rows))
    return output


def _window_features(rows):
    spreads = [float(row[1]) for row in rows]
    depth_i = [float(row[4]) for row in rows]
    flow = [float(row[5]) for row in rows]
    aggression = [float(row[6]) for row in rows]
    depths = [math.log1p(max(0.0, float(row[2])+float(row[3]))) for row in rows]
    activity = [math.log1p(max(0, int(row[7]))) for row in rows]
    f5 = [_subflow(row[8], 5000) for row in rows]
    f15 = [_subflow(row[8], 15000) for row in rows]
    f60 = [_subflow(row[8], 60000) for row in rows]
    return {
        "spread_level": _mean(spreads), "spread_trend": _slope(spreads),
        "depth_imbalance_level": _mean(depth_i),
        "depth_imbalance_trend": _slope(depth_i),
        "depth_imbalance_volatility": _std(depth_i),
        "flow_level": _mean(flow), "flow_trend": _slope(flow),
        "flow_volatility": _std(flow),
        "aggression_level": _mean(aggression),
        "aggression_trend": _slope(aggression),
        "log_depth_level": _mean(depths), "log_depth_trend": _slope(depths),
        "trade_activity": _mean(activity), "flow_5s": _mean(f5),
        "flow_15s": _mean(f15), "flow_60s": _mean(f60),
        "flow_5s_minus_60s": _mean(f5)-_mean(f60),
    }


def _build_windows(samples, run_id):
    windows = []
    for symbol, rows in samples.items():
        for end in range(WINDOW_SIZE-1, len(rows)):
            source = rows[end-WINDOW_SIZE+1:end+1]
            # Prevent a service outage from being interpreted as a continuous
            # five-minute sequence.
            if int(source[-1][0])-int(source[0][0]) > 12*60*1000:
                continue
            features = _window_features(source)
            windows.append({"run_id": run_id, "symbol": symbol,
                            "start_ms": int(source[0][0]),
                            "end_ms": int(source[-1][0]),
                            "features": features})
    return windows


def _robust_normalize(windows):
    # Per-symbol robust scaling prevents contract size and ordinary spread
    # differences from becoming the cluster definition.
    grouped = {}
    for row in windows:
        grouped.setdefault(row["symbol"], []).append(row)
    for _symbol, rows in grouped.items():
        for feature in FEATURES:
            values = [float(row["features"][feature]) for row in rows]
            median = statistics.median(values)
            deviations = [abs(value-median) for value in values]
            mad = statistics.median(deviations) or 1e-9
            scale = 1.4826*mad
            for row, value in zip(rows, values):
                row.setdefault("vector", []).append(max(-6.0, min(6.0,
                    (value-median)/scale)))
    return windows


def _distance(left, right):
    return math.sqrt(sum((a-b)**2 for a, b in zip(left, right)))


def _mean_vector(rows):
    if not rows:
        return []
    return [sum(row[index] for row in rows)/float(len(rows))
            for index in range(len(rows[0]))]


def _kmeans(windows, k):
    vectors = [row["vector"] for row in windows]
    first = max(range(len(vectors)), key=lambda index: sum(
        value*value for value in vectors[index]))
    centroids = [vectors[first][:]]
    while len(centroids) < k:
        index = max(range(len(vectors)), key=lambda i: min(
            _distance(vectors[i], center) for center in centroids))
        centroids.append(vectors[index][:])
    assignments = [-1]*len(vectors)
    for _iteration in range(40):
        updated = [min(range(k), key=lambda c: _distance(vector, centroids[c]))
                   for vector in vectors]
        if updated == assignments:
            break
        assignments = updated
        for cluster in range(k):
            members = [vectors[index] for index, value in enumerate(assignments)
                       if value == cluster]
            if members:
                centroids[cluster] = _mean_vector(members)
    return centroids, assignments


def _medoid_reassignment(windows, centroids, assignments):
    """One bounded medoid pass, deliberately cheaper than full PAM."""
    vectors = [row["vector"] for row in windows]
    medoids = []
    for cluster, centroid in enumerate(centroids):
        members = [index for index, value in enumerate(assignments)
                   if value == cluster]
        if not members:
            medoids.append(centroid)
            continue
        best = min(members, key=lambda index: _distance(vectors[index], centroid))
        medoids.append(vectors[best])
    return [min(range(len(medoids)), key=lambda cluster:
                _distance(vector, medoids[cluster])) for vector in vectors]


def _aligned_agreement(primary, alternate, k):
    mapping = {}
    for alternate_id in range(k):
        counts = {}
        for index, value in enumerate(alternate):
            if value == alternate_id:
                counts[primary[index]] = counts.get(primary[index], 0)+1
        if counts:
            mapping[alternate_id] = max(counts, key=counts.get)
    aligned = [mapping.get(value, -1) for value in alternate]
    per_cluster = {}
    for cluster in range(k):
        members = [index for index, value in enumerate(primary)
                   if value == cluster]
        per_cluster[cluster] = (sum(aligned[index] == cluster for index in members)/
                                float(len(members)) if members else 0.0)
    return per_cluster


def _discover_target_local_clusters(windows):
    grouped = {}
    for row in windows:
        grouped.setdefault(row["symbol"], []).append(row)
    raw_clusters = []; window_clusters = {}
    for symbol, rows in sorted(grouped.items()):
        rows.sort(key=lambda row: row["end_ms"])
        k = min(6, max(2, int(round(math.sqrt(len(rows)/25.0)))))
        k = min(k, len(rows))
        centroids, assignments = _kmeans(rows, k)
        alternate = _medoid_reassignment(rows, centroids, assignments)
        agreements = _aligned_agreement(assignments, alternate, k)
        midpoint = len(rows)//2
        for cluster_index, centroid in enumerate(centroids):
            members = [index for index, value in enumerate(assignments)
                       if value == cluster_index]
            if not members:
                continue
            early = sum(index < midpoint for index in members)
            late = sum(index >= midpoint for index in members)
            stable = (len(members) >= 30 and early >= 5 and late >= 5 and
                      float(agreements.get(cluster_index) or 0.0) >= .72)
            exemplar_indices = sorted(members, key=lambda index: _distance(
                rows[index]["vector"], centroid))[:3]
            quantized = [int(max(-2, min(2, round(value)))) for value in centroid]
            fine = [round(float(value), 2) for value in centroid]
            fingerprint = _sha({"schema": SCHEMA_VERSION, "symbol": symbol,
                                "coarse_vector": quantized,
                                "fine_vector": fine})
            cluster_id = "slow_micro_%s_%s" % (
                symbol.split("-")[0].lower(), fingerprint[:10])
            stability = {
                "stable_candidate": stable,
                "cross_algorithm_agreement": round(
                    float(agreements.get(cluster_index) or 0.0), 6),
                "early_windows": early, "late_windows": late,
                "minimum_members": 30, "minimum_each_half": 5,
                "minimum_algorithm_agreement": .72,
                "algorithms": ["robust_kmeans", "bounded_medoid_reassignment"],
                "temporal_validation": "first_half_and_second_half_presence"}
            raw_clusters.append({
                "cluster_index": cluster_index, "symbol": symbol,
                "cluster_id": cluster_id, "fingerprint": fingerprint,
                "sample_count": len(members),
                "centroid_vector": centroid,
                "centroid": dict(zip(FEATURES,
                                       [round(value, 6) for value in centroid])),
                "observable_descriptor": _descriptor(centroid),
                "stability": stability,
                "exemplars": [{"symbol": symbol,
                               "start_ms": rows[index]["start_ms"],
                               "end_ms": rows[index]["end_ms"],
                               "features": rows[index]["features"]}
                              for index in exemplar_indices]})
            for index in members:
                key = (symbol, int(rows[index]["start_ms"]),
                       int(rows[index]["end_ms"]))
                window_clusters[key] = cluster_id
    return raw_clusters, window_clusters


def _reconcile_cluster_identities(conn, raw_clusters, window_clusters,
                                  maximum_distance=1.25):
    """Match minor centroid movement to a target-local canonical identity.

    Fingerprint quantization is useful for exact repeats, but a centroid close
    to a rounding boundary can otherwise acquire a new ID every run.  Matching
    is one-to-one, symbol-local and label-free.  It changes identity only; it
    never transfers an outcome or predictive claim.
    """
    existing = []
    for cluster_id, state, fingerprint, centroid_json in conn.execute(
            "SELECT cluster_id,state,fingerprint,centroid_json FROM "
            "microstructure_primitive_clusters WHERE schema_version=? AND "
            "state<>'superseded_schema'", (SCHEMA_VERSION,)).fetchall():
        try:
            centroid = json.loads(centroid_json or "{}")
            vector = [float(centroid[name]) for name in FEATURES]
        except Exception:
            continue
        symbol = cluster_id.split("_")[2].upper() if cluster_id.startswith(
            "slow_micro_") and len(cluster_id.split("_")) > 3 else ""
        existing.append({"cluster_id": cluster_id, "symbol_prefix": symbol,
                         "state": state, "fingerprint": fingerprint,
                         "vector": vector})
    pairs = []
    for raw_index, raw in enumerate(raw_clusters):
        base = raw["symbol"].split("-")[0].upper()
        for old_index, old in enumerate(existing):
            if old["symbol_prefix"] != base:
                continue
            distance = _distance(raw["centroid_vector"], old["vector"])
            if raw["fingerprint"] == old["fingerprint"] or distance <= maximum_distance:
                pairs.append((0 if raw["fingerprint"] == old["fingerprint"] else 1,
                              distance, raw_index, old_index))
    used_raw = set(); used_old = set(); mapping = {}; distances = {}
    for _exact_rank, distance, raw_index, old_index in sorted(pairs):
        if raw_index in used_raw or old_index in used_old:
            continue
        raw = raw_clusters[raw_index]; old = existing[old_index]
        provisional = raw["cluster_id"]
        raw["raw_fingerprint"] = raw["fingerprint"]
        raw["cluster_id"] = old["cluster_id"]
        raw["fingerprint"] = old["fingerprint"]
        raw["identity"] = {"matched_existing": True,
                           "distance": round(distance, 6),
                           "maximum_distance": maximum_distance,
                           "policy": "one_to_one_symbol_local_no_label_transfer"}
        mapping[provisional] = old["cluster_id"]
        distances[old["cluster_id"]] = distance
        used_raw.add(raw_index); used_old.add(old_index)
    for index, raw in enumerate(raw_clusters):
        if index not in used_raw:
            raw["raw_fingerprint"] = raw["fingerprint"]
            raw["identity"] = {"matched_existing": False, "distance": None,
                               "maximum_distance": maximum_distance,
                               "policy": "new_target_local_identity"}
            mapping[raw["cluster_id"]] = raw["cluster_id"]
            distances[raw["cluster_id"]] = None
    for key, cluster_id in list(window_clusters.items()):
        window_clusters[key] = mapping.get(cluster_id, cluster_id)
    return raw_clusters, window_clusters, distances


def _micro_event_graph(windows, window_clusters, raw_clusters, run_id, now):
    grouped = {}
    for row in windows:
        grouped.setdefault(row["symbol"], []).append(row)
    output = []
    for symbol, rows in grouped.items():
        rows.sort(key=lambda row: row["end_ms"])
        observations = []
        for index, row in enumerate(rows):
            if index+5 >= len(rows):
                continue
            cluster_id = window_clusters.get(
                (symbol, int(row["start_ms"]), int(row["end_ms"])))
            if not cluster_id:
                continue
            current = row["features"]; future = [value["features"]
                                                  for value in rows[index+1:index+6]]
            current_spread = max(1e-12, float(current["spread_level"]))
            current_depth = float(current["log_depth_level"])
            current_flow = float(current["flow_level"])
            current_activity = float(current["trade_activity"])
            events = {
                "spread_jump_5sample": max(float(value["spread_level"])
                                            for value in future) >= 1.5*current_spread,
                "depth_drop_5sample": min(float(value["log_depth_level"])
                                           for value in future) <= current_depth-.50,
                "flow_reversal_5sample": (abs(current_flow) >= .25 and any(
                    value["flow_level"]*current_flow < 0 and
                    abs(float(value["flow_level"])) >= .25 for value in future)),
                "activity_jump_5sample": max(float(value["trade_activity"])
                                              for value in future) >= current_activity+.50,
            }
            observations.append((cluster_id, events))
        background_total = len(observations)
        if not background_total:
            continue
        event_codes = sorted({code for _cluster, events in observations
                              for code in events})
        for cluster_id in sorted(set(cluster for cluster, _events in observations)):
            cluster_rows = [events for cluster, events in observations
                            if cluster == cluster_id]
            for event_code in event_codes:
                cluster_hits = sum(bool(events[event_code]) for events in cluster_rows)
                background_hits = sum(bool(events[event_code])
                                      for _cluster, events in observations)
                metrics = _risk_ratio_cell(cluster_hits, len(cluster_rows),
                                           background_hits, background_total)
                eligible = (len(cluster_rows) >= 30 and cluster_hits >= 8 and
                            background_total >= 120 and metrics["lift"] >= 1.5 and
                            metrics["lift_ci95_low"] > 1.0)
                output.append({
                    "association_id": _sha({"run": run_id, "symbol": symbol,
                                             "cluster": cluster_id,
                                             "event": event_code}),
                    "run_id": run_id, "symbol": symbol,
                    "cluster_id": cluster_id, "event_code": event_code,
                    "horizon_samples": 5, "cluster_total": len(cluster_rows),
                    "cluster_hits": cluster_hits,
                    "background_total": background_total,
                    "background_hits": background_hits,
                    "cluster_rate": round(metrics["death_rate"], 6),
                    "background_rate": round(metrics["background_rate"], 6),
                    "lift": round(metrics["lift"], 6),
                    "lift_ci95_low": round(metrics["lift_ci95_low"], 6),
                    "eligible": eligible, "outcome_label": None,
                    "causal_claim": False,
                    "boundary": "future sampled micro event; no price or PnL label",
                    "created_at": now})
    return output


def _morphology_reports(raw_clusters, event_graph, run_id, now):
    stable = [row for row in raw_clusters
              if (row.get("stability") or {}).get("stable_candidate")]
    profiles = {}
    for row in event_graph:
        profiles.setdefault(row["cluster_id"], {})[row["event_code"]] = row[
            "cluster_rate"]
    reports = []
    for left_index, left in enumerate(stable):
        for right in stable[left_index+1:]:
            if left["symbol"] == right["symbol"]:
                continue
            distance = _distance(left["centroid_vector"], right["centroid_vector"])
            if distance > 3.0:
                continue
            codes = set(profiles.get(left["cluster_id"], {})) | set(
                profiles.get(right["cluster_id"], {}))
            profile_distance = (max(abs(
                profiles.get(left["cluster_id"], {}).get(code, 0.0)-
                profiles.get(right["cluster_id"], {}).get(code, 0.0))
                for code in codes) if codes else None)
            state = ("similar_shape_different_micro_event_profile"
                     if profile_distance is not None and profile_distance >= .15
                     else "similar_shape_observation")
            payload = {"outcome_labels_used": False, "causal_claim": False,
                       "cross_target_transfer_allowed": False,
                       "left_stability": left["stability"],
                       "right_stability": right["stability"]}
            reports.append({"report_id": _sha({"run": run_id,
                                                "left": left["cluster_id"],
                                                "right": right["cluster_id"]}),
                            "run_id": run_id, "left_symbol": left["symbol"],
                            "right_symbol": right["symbol"],
                            "left_cluster_id": left["cluster_id"],
                            "right_cluster_id": right["cluster_id"],
                            "centroid_distance": round(distance, 6),
                            "event_profile_distance": (round(profile_distance, 6)
                                                       if profile_distance is not None
                                                       else None),
                            "state": state, "payload": payload,
                            "created_at": now})
    return sorted(reports, key=lambda row: row["centroid_distance"])[:96]


def _descriptor(centroid):
    pairs = sorted(zip(FEATURES, centroid), key=lambda item: abs(item[1]),
                   reverse=True)[:5]
    return "；".join("%s=%s%.2f" % (name, "+" if value >= 0 else "", value)
                    for name, value in pairs)


def _forbidden_claim(text):
    lowered = str(text or "").lower()
    forbidden = ("盈利", "胜率", "上涨", "下跌", "做多", "做空", "买入",
                 "卖出", "入场", "alpha", "predict", "market maker",
                 "做市商", "吸筹", "护盘", "撤单", "排队")
    return any(term in lowered for term in forbidden)


def _index_ms(value):
    try:
        return int(value.timestamp()*1000.0)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return 0


def _risk_ratio_cell(death_hits, death_total, background_hits, background_total):
    # Haldane correction keeps the descriptive interval finite at zero.  The
    # observations are overlapping patterns, so this is explicitly an
    # exploratory association interval, not an independent-sample p-value.
    death_rate = death_hits/float(death_total) if death_total else 0.0
    background_rate = (background_hits/float(background_total)
                       if background_total else 0.0)
    left = (death_hits+.5)/(death_total+1.0)
    right = (background_hits+.5)/(background_total+1.0)
    ratio = left/right if right > 0 else 0.0
    variance = (1.0/(death_hits+.5)-1.0/(death_total+1.0)
                +1.0/(background_hits+.5)-1.0/(background_total+1.0))
    lower = math.exp(math.log(max(ratio, 1e-12))-1.96*math.sqrt(max(0.0, variance)))
    return {"death_rate": death_rate, "background_rate": background_rate,
            "lift": ratio, "lift_ci95_low": lower}


def _mature_death_forward_outcomes(eco, target_payloads):
    """Test known logical-death signals on data that arrived after telemetry.

    This is a prospective falsification check of the death map, not proof that
    a branch is "inevitably" bad.  A positive observation is deliberately
    preserved as disconfirming evidence instead of being explained away.
    """
    import auto_trade_strategy_ecosystem as ecosystem
    existing = set(row[0] for row in eco.execute(
        "SELECT observation_id FROM death_forward_validation_outcomes").fetchall())
    rows = eco.execute(
        "SELECT observation_id,symbol,timeframe,pattern_id,signal_ts "
        "FROM death_micro_cooccurrence_observations ORDER BY signal_ts LIMIT 2048"
    ).fetchall()
    frame_cache = {}; index_cache = {}; inserted = 0
    verdicts = {"confirmed_negative": 0, "disconfirmed_positive": 0}
    for observation_id, symbol, timeframe, pattern_id, signal_ts in rows:
        if observation_id in existing:
            continue
        payload = target_payloads.get((symbol, timeframe, pattern_id)) or {}
        direction = str(payload.get("direction") or "").lower()
        horizon = max(1, min(16, int(payload.get("best_horizon_bars") or 8)))
        if direction not in ("long", "short"):
            continue
        key = (symbol, timeframe)
        if key not in frame_cache:
            try:
                frame_cache[key] = ecosystem._load_research_frame(symbol, timeframe)
                index_cache[key] = {_index_ms(value): index for index, value in
                                    enumerate(frame_cache[key].index)}
            except Exception:
                frame_cache[key] = None; index_cache[key] = {}
        frame = frame_cache[key]; index = index_cache[key].get(int(signal_ts))
        if frame is None or index is None or index+horizon >= len(frame):
            continue
        if not {"close", "high", "low"}.issubset(set(frame.columns)):
            # Minimal unit-test/legacy frames cannot support stop-path
            # reconstruction.  Skipping is safer than silently assuming that
            # no stop was touched.
            continue
        entry = float(frame["close"].iloc[index])
        future = frame.iloc[index+1:index+horizon+1]
        stopped = (float(future["low"].min()) <= entry*(1.0-.009)
                   if direction == "long" else
                   float(future["high"].max()) >= entry*(1.0+.009))
        if stopped:
            raw = -.009
        else:
            final = float(frame["close"].iloc[index+horizon])
            raw = ((final-entry)/entry if direction == "long"
                   else (entry-final)/entry)
        friction = ecosystem._friction_scenario(symbol, "observed_base")
        per_side = sum(float(friction.get(field) or 0.0) for field in (
            "fee_rate_per_side", "slippage_rate_per_side",
            "half_spread_rate_per_side", "impact_rate_per_side",
            "latency_rate_per_side"))
        hours = {"5m": 1.0/12.0, "15m": .25, "1h": 1.0}.get(timeframe, 1.0)
        funding = horizon*hours/8.0*float(
            friction.get("funding_rate_per_8h") or 0.0)
        gross_pct = raw*20.0*100.0
        net_pct = (raw-(2.0*per_side+funding))*20.0*100.0
        verdict = ("confirmed_negative" if net_pct <= 0.0
                   else "disconfirmed_positive")
        eco.execute(
            "INSERT OR IGNORE INTO death_forward_validation_outcomes "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (observation_id, symbol, timeframe, pattern_id, int(signal_ts),
             direction, horizon, int(stopped), gross_pct, net_pct, verdict,
             json.dumps(friction, sort_keys=True), json.dumps({
                 "method": "prospective_fixed_horizon_20x_0.9pct_stop",
                 "interpretation": ("single forward observation; may confirm or "
                                    "disconfirm but cannot prove inevitability"),
                 "historical_backfill": False,
                 "future_l2_used": False}, ensure_ascii=False, sort_keys=True),
             _now()))
        inserted += 1; verdicts[verdict] += 1
    eco.commit()
    totals = dict(eco.execute(
        "SELECT verdict,COUNT(*) FROM death_forward_validation_outcomes "
        "GROUP BY verdict").fetchall())
    return {"inserted": inserted, "this_run": verdicts,
            "totals": totals,
            "policy": "prospective_falsification_not_inevitability_claim"}


def _capture_death_micro_cooccurrence(micro_conn, monitor_run_id):
    """Prospectively align known death-pattern signals with sampled clusters."""
    import auto_trade_strategy_ecosystem as ecosystem
    eco = ecosystem._db(); now = _now()
    try:
        source = eco.execute(
            "SELECT symbol,timeframe,pattern_id,death_codes_json,review_json,created_at "
            "FROM prescreen_rejections WHERE stage='deterministic_atlas' "
            "ORDER BY created_at DESC,rowid DESC LIMIT 768").fetchall()
        latest = {}
        for row in source:
            key = (row[0], row[1], row[2])
            if key not in latest:
                latest[key] = row
        targets = {}
        target_payloads = {}
        for key, row in latest.items():
            payload = json.loads(row[4] or "{}")
            if not payload.get("event") or not payload.get("context"):
                continue
            targets.setdefault((row[0], row[1]), []).append((row, payload))
            target_payloads[(row[0], row[1], row[2])] = payload
        # Bound each six-hour run.  Recent map targets are favored by source
        # ordering, while all retained observations remain available later.
        target_items = list(targets.items())[:6]
        inserted = matched = 0
        for (symbol, timeframe), patterns in target_items:
            telemetry = micro_conn.execute(
                "SELECT MIN(observed_ms),MAX(observed_ms) FROM microstructure_samples "
                "WHERE symbol=?", (symbol,)).fetchone()
            if not telemetry or telemetry[0] is None:
                continue
            try:
                frame = ecosystem._load_research_frame(symbol, timeframe)
            except Exception:
                continue
            for row, payload in patterns:
                pattern_id = row[2]
                last = eco.execute(
                    "SELECT MAX(signal_ts) FROM death_micro_cooccurrence_observations "
                    "WHERE symbol=? AND timeframe=? AND pattern_id=?",
                    (symbol, timeframe, pattern_id)).fetchone()[0]
                telemetry_floor = int(telemetry[0])
                last_signal = int(last or 0)
                pattern = {"pattern_id": pattern_id,
                           "direction": payload.get("direction"),
                           "event": payload["event"], "context": payload["context"]}
                try:
                    indices = ecosystem._atlas_pattern_indices(
                        frame, pattern, maximum_horizon=0)
                except Exception:
                    continue
                for index in indices:
                    signal_ts = _index_ms(frame.index[index])
                    # The first signal may occur exactly at the first telemetry
                    # timestamp.  Only previously archived signals are strictly
                    # excluded; the telemetry boundary itself is inclusive.
                    if (signal_ts < telemetry_floor or
                            (last_signal and signal_ts <= last_signal) or
                            signal_ts > int(telemetry[1])+15*60*1000):
                        continue
                    primitive = micro_conn.execute(
                        "SELECT a.cluster_id,a.end_ms,a.distance,c.state,w.descriptor_text "
                        "FROM microstructure_primitive_assignments a "
                        "JOIN microstructure_primitive_clusters c "
                        "ON c.cluster_id=a.cluster_id "
                        "LEFT JOIN microstructure_windows w ON w.window_id=a.window_id "
                        "WHERE a.symbol=? AND a.end_ms<=? ORDER BY a.end_ms DESC LIMIT 1",
                        (symbol, signal_ts)).fetchone()
                    is_match = bool(primitive and signal_ts-int(primitive[1]) <= 15*60*1000)
                    observation_id = _sha({"symbol": symbol, "timeframe": timeframe,
                                           "pattern": pattern_id, "signal": signal_ts})
                    eco.execute(
                        "INSERT OR IGNORE INTO death_micro_cooccurrence_observations "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (observation_id, monitor_run_id, symbol, timeframe, pattern_id,
                         row[3], signal_ts, primitive[0] if is_match else None,
                         primitive[3] if is_match else None,
                         int(primitive[1]) if is_match else None,
                         float(primitive[2]) if is_match else None,
                         int(is_match), json.dumps({
                             "micro_descriptor": primitive[4] if is_match else None,
                             "direction": payload.get("direction"),
                             "best_horizon_bars": payload.get("best_horizon_bars"),
                             "outcome_label": None, "causal_claim": False,
                             "sampling_boundary": "15m contemporaneous slow window"},
                             ensure_ascii=False, sort_keys=True), now))
                    inserted += 1; matched += int(is_match)
        eco.commit()
        forward_validation = _mature_death_forward_outcomes(
            eco, target_payloads)
        reports = []
        for symbol, timeframe in targets.keys():
            observations = eco.execute(
                "SELECT death_codes_json,cluster_id,matched FROM "
                "death_micro_cooccurrence_observations WHERE symbol=? AND timeframe=?",
                (symbol, timeframe)).fetchall()
            background = micro_conn.execute(
                "SELECT cluster_id,COUNT(*) FROM microstructure_primitive_assignments "
                "WHERE symbol=? GROUP BY cluster_id", (symbol,)).fetchall()
            background_counts = dict(background); background_total = sum(
                int(row[1]) for row in background)
            totals = {}; hits = {}
            for codes_json, cluster_id, is_match in observations:
                if not is_match or not cluster_id:
                    continue
                for code in json.loads(codes_json or "[]"):
                    totals[code] = totals.get(code, 0)+1
                    hits[(code, cluster_id)] = hits.get((code, cluster_id), 0)+1
            cells = []
            for (code, cluster_id), count in hits.items():
                metrics = _risk_ratio_cell(
                    count, totals.get(code, 0), background_counts.get(cluster_id, 0),
                    background_total)
                eligible = (totals.get(code, 0) >= 20 and count >= 5
                            and background_total >= 80 and metrics["lift"] >= 1.5
                            and metrics["lift_ci95_low"] > 1.0)
                cell_id = _sha({"symbol": symbol, "timeframe": timeframe,
                                "death": code, "cluster": cluster_id})[:16]
                cells.append({"cell_id": cell_id, "death_cause_code": code,
                              "cluster_id": cluster_id, "death_hits": count,
                              "death_total_matched": totals.get(code, 0),
                              "background_hits": background_counts.get(cluster_id, 0),
                              "background_total": background_total,
                              "death_rate": round(metrics["death_rate"], 6),
                              "background_rate": round(metrics["background_rate"], 6),
                              "lift": round(metrics["lift"], 6),
                              "lift_ci95_low": round(metrics["lift_ci95_low"], 6),
                              "eligible": eligible,
                              "causal_claim": False,
                              "dependency_warning": "patterns and rolling windows overlap"})
            eligible_cells = [row for row in cells if row["eligible"]]
            state = ("awaiting_forward_signal_overlap" if not observations else
                     ("eligible_descriptive_cooccurrence" if eligible_cells
                      else "descriptive_insufficient"))
            fingerprint = _sha({"symbol": symbol, "timeframe": timeframe,
                                "cells": cells})
            previous = eco.execute(
                "SELECT payload_json,ai_analysis_json FROM "
                "death_micro_association_reports WHERE symbol=? AND timeframe=? "
                "ORDER BY created_at DESC LIMIT 1", (symbol, timeframe)).fetchone()
            previous_fingerprint = (json.loads(previous[0] or "{}").get("fingerprint")
                                    if previous else None)
            previous_count = (int(json.loads(previous[0] or "{}").get(
                "observation_count") or 0) if previous else 0)
            ai_analysis = json.loads(previous[1] or "{}") if previous else {}
            api_calls = 0
            enough_new_information = (len(observations) >=
                                      max(previous_count+5,
                                          int(math.ceil(previous_count*1.20))))
            if (eligible_cells and fingerprint != previous_fingerprint
                    and enough_new_information):
                try:
                    from auto_trade_ai_consensus import death_micro_cooccurrence_analysis
                    ai_analysis = death_micro_cooccurrence_analysis({
                        "symbol": symbol, "timeframe": timeframe,
                        "cells": cells,
                        "boundary": ("prospective sampled coexistence only; no causal, "
                                     "outcome, price-direction or pruning label"),
                    })
                    api_calls = 1
                except Exception as exc:
                    ai_analysis = {"ok": False, "error": str(exc)[:500]}
            payload = {"fingerprint": fingerprint, "cells": cells,
                       "state": state, "observation_count": len(observations),
                       "matched_count": sum(int(row[2]) for row in observations),
                       "background_windows": background_total,
                       "api_calls_used": api_calls,
                       "policy": "prospective_descriptive_cooccurrence_no_causality"}
            report_id = _sha({"symbol": symbol, "timeframe": timeframe,
                              "fingerprint": fingerprint})
            eco.execute(
                "INSERT OR IGNORE INTO death_micro_association_reports "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (report_id, symbol, timeframe, len(observations),
                 len(eligible_cells), state,
                 json.dumps(payload, ensure_ascii=False, sort_keys=True),
                 json.dumps(ai_analysis, ensure_ascii=False, sort_keys=True), now))
            reports.append({"symbol": symbol, "timeframe": timeframe,
                            "state": state, "observations": len(observations),
                            "eligible_cells": len(eligible_cells),
                            "api_calls_used": api_calls})
        eco.commit()
        return {"ok": True, "inserted": inserted, "matched": matched,
                "reports": reports,
                "reverse_death_forward_validation": forward_validation,
                "boundary": "forward signals only; historical deaths not backfilled"}
    finally:
        eco.close()


def run_once(use_ai=True):
    conn = _connect(); started = int(time.time()*1000.0)
    run_id = time.strftime("%Y%m%d_%H%M%S")+"_"+_sha(started)[:8]
    samples = _load_samples(conn); sample_count = sum(len(v) for v in samples.values())
    windows = _robust_normalize(_build_windows(samples, run_id))
    if len(windows) < MIN_TOTAL_WINDOWS:
        result = {"ok": True, "state": "collecting", "samples_used": sample_count,
                  "windows_built": len(windows), "minimum_windows": MIN_TOTAL_WINDOWS,
                  "clusters_found": 0, "api_calls_used": 0,
                  "outcome_labels_used": False}
        conn.execute("INSERT INTO microstructure_primitive_runs VALUES(?,?,?,?,?,?,?,?,?)",
                     (run_id, SCHEMA_VERSION, "collecting", sample_count,
                      len(windows), 0, 0, json.dumps(result, sort_keys=True), _now()))
        conn.commit(); conn.close()
        return result
    raw_clusters, window_clusters = _discover_target_local_clusters(windows)
    raw_clusters, window_clusters, identity_distances = (
        _reconcile_cluster_identities(conn, raw_clusters, window_clusters))
    stable_clusters = [row for row in raw_clusters
                       if row["stability"]["stable_candidate"]]
    audited = set(row[0] for row in conn.execute(
        "SELECT fingerprint FROM microstructure_primitive_clusters WHERE "
        "state IN ('descriptive_registered','observed_unlabeled')").fetchall())
    # A cluster that was previously unstable gets its first naming audit only
    # after it actually satisfies the stability gate.
    unseen = [row for row in stable_clusters
              if row["fingerprint"] not in audited]
    review = {"ok": False, "registered": [], "policy": "not_called"}
    api_calls = 0
    if unseen and use_ai:
        from auto_trade_ai_consensus import review_micro_primitive_catalog
        review = review_micro_primitive_catalog({
            "schema": SCHEMA_VERSION, "clusters": unseen,
            "sample_count": sample_count, "window_count": len(windows),
            "outcome_labels_present": False,
            "sampling_boundary": ("one minute snapshots plus latest-trade 5s/15s/60s "
                                  "subwindows; not continuous replay, queue or cancel flow"),
            "stability_gate": ("target-local; >=30 windows; present in both temporal "
                               "halves; robust-kmeans/medoid agreement >=0.72"),
            "task": "name only statistically stable repeated observable states; no price prediction",
        })
        api_calls = 1+len(review.get("audits") or [])
    proposed = {row["cluster_id"]: row for row in
                ((review.get("proposal") or {}).get("primitives") or [])}
    registered = set(row.get("cluster_id") for row in review.get("registered") or [])
    audits_by_cluster = {}
    for audit in review.get("audits") or []:
        for item in audit.get("audits") or []:
            audits_by_cluster.setdefault(item.get("cluster_id"), []).append({
                "provider": audit.get("provider"), "decision": item.get("decision"),
                "flags": item.get("flags"), "reason": item.get("reason")})
    now = _now()
    conn.execute("UPDATE microstructure_primitive_clusters SET state='superseded_schema' "
                 "WHERE schema_version<>? AND state<>'superseded_schema'",
                 (SCHEMA_VERSION,))
    for cluster in raw_clusters:
        proposal = proposed.get(cluster["cluster_id"]) or {}
        clean = not _forbidden_claim(" ".join((str(proposal.get("label") or ""),
                                                str(proposal.get("description") or ""),
                                                str(proposal.get("mechanism_hypothesis") or ""))))
        existing = conn.execute(
            "SELECT created_at,state,label,description,review_json FROM "
            "microstructure_primitive_clusters WHERE cluster_id=?",
            (cluster["cluster_id"],)).fetchone()
        created_at = existing[0] if existing else now
        previously_registered = bool(existing and
                                     existing[1] == "descriptive_registered")
        stable = bool(cluster["stability"]["stable_candidate"])
        state = ("descriptive_registered" if stable and clean and
                 (cluster["cluster_id"] in registered or previously_registered)
                 else ("observed_unlabeled" if stable else "observed_unstable"))
        label = ((proposal.get("label") or (existing[2] if existing else None))
                 if state == "descriptive_registered" else
                 "未命名慢微观基元")
        description = ((proposal.get("description") or
                        (existing[3] if existing else None))
                       if state == "descriptive_registered"
                       else cluster["observable_descriptor"])
        prior_review = json.loads(existing[4] or "{}") if existing else {}
        review_json = {"proposal": proposal or prior_review.get("proposal") or {},
                       "audits": (audits_by_cluster.get(cluster["cluster_id"], []) or
                                  prior_review.get("audits") or []),
                       "stability": cluster["stability"],
                       "outcome_labels_used": False,
                       "predictive_use_allowed": False}
        conn.execute(
            "INSERT OR REPLACE INTO microstructure_primitive_clusters "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (cluster["cluster_id"], SCHEMA_VERSION, cluster["fingerprint"],
             state, label, description, cluster["sample_count"],
             json.dumps(cluster["centroid"], sort_keys=True),
             json.dumps(cluster["exemplars"], sort_keys=True),
             json.dumps(review_json, ensure_ascii=False, sort_keys=True),
             created_at, now))
        identity_distance = identity_distances.get(cluster["cluster_id"])
        drift_state = ("new_identity" if identity_distance is None else
                       ("review_watch" if identity_distance >= .75 else
                        "within_identity_envelope"))
        version_id = _sha({"run": run_id, "cluster": cluster["cluster_id"]})
        conn.execute(
            "INSERT OR REPLACE INTO microstructure_primitive_versions "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (version_id, run_id, cluster["cluster_id"], cluster["symbol"],
             cluster.get("raw_fingerprint") or cluster["fingerprint"],
             json.dumps(cluster["centroid"], sort_keys=True),
             identity_distance,
             json.dumps(cluster["stability"], sort_keys=True), drift_state, now))
    centroid_by_cluster = {row["cluster_id"]: row["centroid_vector"]
                           for row in raw_clusters}
    for row in windows:
        cluster_id = window_clusters[(row["symbol"], int(row["start_ms"]),
                                      int(row["end_ms"]))]
        window_id = _sha({"symbol": row["symbol"], "start": row["start_ms"],
                          "end": row["end_ms"], "schema": SCHEMA_VERSION})
        conn.execute("INSERT OR REPLACE INTO microstructure_windows VALUES(?,?,?,?,?,?,?,?)",
                     (window_id, run_id, row["symbol"], row["start_ms"], row["end_ms"],
                      json.dumps(row["features"], sort_keys=True),
                      _descriptor(row["vector"]), now))
        assignment_id = _sha({"window": window_id, "cluster": cluster_id})
        conn.execute(
            "INSERT OR REPLACE INTO microstructure_primitive_assignments "
            "VALUES(?,?,?,?,?,?,?,?)",
            (assignment_id, run_id, window_id, row["symbol"], row["end_ms"],
             cluster_id, _distance(row["vector"], centroid_by_cluster[cluster_id]), now))
    event_graph = _micro_event_graph(
        windows, window_clusters, raw_clusters, run_id, now)
    for row in event_graph:
        payload = {key: row[key] for key in (
            "cluster_rate", "background_rate", "outcome_label",
            "causal_claim", "boundary")}
        conn.execute(
            "INSERT OR REPLACE INTO microstructure_event_associations "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["association_id"], run_id, row["symbol"], row["cluster_id"],
             row["event_code"], row["horizon_samples"], row["cluster_total"],
             row["cluster_hits"], row["background_total"], row["background_hits"],
             row["lift"], row["lift_ci95_low"], int(row["eligible"]),
             json.dumps(payload, ensure_ascii=False, sort_keys=True), now))
    morphology = _morphology_reports(raw_clusters, event_graph, run_id, now)
    for row in morphology:
        conn.execute(
            "INSERT OR REPLACE INTO microstructure_morphology_reports "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (row["report_id"], run_id, row["left_symbol"], row["right_symbol"],
             row["left_cluster_id"], row["right_cluster_id"],
             row["centroid_distance"], row["event_profile_distance"],
             row["state"], json.dumps(row["payload"], ensure_ascii=False,
                                      sort_keys=True), now))
    conn.commit()
    cooccurrence = None
    for attempt in range(3):
        try:
            cooccurrence = _capture_death_micro_cooccurrence(conn, run_id)
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt >= 2:
                cooccurrence = {"ok": False, "error": str(exc)[:500],
                                "attempts": attempt+1,
                                "boundary": "primitive discovery remains valid"}
                break
            # The per-minute rating/experience writers are short lived.  A
            # bounded retry preserves the co-occurrence bridge without
            # stopping or delaying any live trading process.
            time.sleep(2.0*(attempt+1))
        except Exception as exc:
            cooccurrence = {"ok": False, "error": str(exc)[:500],
                            "attempts": attempt+1,
                            "boundary": "primitive discovery remains valid"}
            break
    if cooccurrence is None:
        cooccurrence = {"ok": False, "error": "cooccurrence_retry_exhausted",
                        "attempts": 3,
                        "boundary": "primitive discovery remains valid"}
    cooccurrence_calls = sum(int(row.get("api_calls_used") or 0)
                             for row in cooccurrence.get("reports") or [])
    result = {"ok": True, "state": "descriptive_discovery_complete",
              "samples_used": sample_count, "windows_built": len(windows),
              "clusters_found": len(raw_clusters), "new_fingerprints": len(unseen),
              "stable_cluster_candidates": len(stable_clusters),
              "unstable_clusters_rejected_before_ai": (len(raw_clusters)-
                                                        len(stable_clusters)),
              "registered_descriptive": sum(1 for row in raw_clusters if
                  conn.execute("SELECT state FROM microstructure_primitive_clusters "
                               "WHERE cluster_id=?", (row["cluster_id"],)).fetchone()[0]
                  == "descriptive_registered"),
              "micro_event_associations": len(event_graph),
              "eligible_negative_micro_event_warnings": sum(
                  int(row["eligible"] and row["event_code"] in
                      ("spread_jump_5sample", "depth_drop_5sample"))
                  for row in event_graph),
              "cross_market_morphology_pairs": len(morphology),
              "canonical_identity_matches": sum(bool((row.get("identity") or {}).get(
                  "matched_existing")) for row in raw_clusters),
              "new_canonical_identities": sum(not bool((row.get("identity") or {}).get(
                  "matched_existing")) for row in raw_clusters),
              "stable_canonical_identity_matches": sum(
                  bool((row.get("identity") or {}).get("matched_existing")) and
                  bool((row.get("stability") or {}).get("stable_candidate"))
                  for row in raw_clusters),
              "new_stable_canonical_identities": sum(
                  not bool((row.get("identity") or {}).get("matched_existing")) and
                  bool((row.get("stability") or {}).get("stable_candidate"))
                  for row in raw_clusters),
              "identity_drift_review_watch": sum(
                  identity_distances.get(row["cluster_id"]) is not None and
                  identity_distances.get(row["cluster_id"]) >= .75 and
                  bool((row.get("stability") or {}).get("stable_candidate"))
                  for row in raw_clusters),
              "api_calls_used": api_calls+cooccurrence_calls,
              "primitive_review_api_calls": api_calls,
              "cooccurrence_api_calls": cooccurrence_calls,
              "death_micro_cooccurrence": cooccurrence,
              "outcome_labels_used": False,
              "predictive_use_allowed": False,
              "cluster_isolation": "symbol-local; no outcome transfer",
              "boundary": "slow sampled microstructure vocabulary and non-price event graph only"}
    conn.execute("INSERT INTO microstructure_primitive_runs VALUES(?,?,?,?,?,?,?,?,?)",
                 (run_id, SCHEMA_VERSION, result["state"], sample_count,
                  len(windows), len(raw_clusters), api_calls+cooccurrence_calls,
                  json.dumps(result, sort_keys=True), now))
    conn.commit(); conn.close()
    STATUS_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                      sort_keys=True), encoding="utf-8")
    try:
        os.chmod(str(STATUS_PATH), 0o600)
    except Exception:
        pass
    return result


if __name__ == "__main__":
    print(json.dumps(run_once(use_ai=True), ensure_ascii=False, sort_keys=True))
