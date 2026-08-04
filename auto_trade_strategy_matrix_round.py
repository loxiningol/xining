# -*- coding: utf-8 -*-
"""Run one resumable, sequential creation/prescreen round over all clusters.

The server is intentionally resource constrained.  This orchestrator never
runs clusters in parallel; each target gets the normal full bounded mission,
including deterministic cost pruning and AI review only when the existing
gates permit it.  Progress is atomically persisted after every target.
"""
from __future__ import print_function

from datetime import datetime
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time

import auto_trade_strategy_ecosystem as ecosystem


ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
STATUS_PATH = AUTO_DIR / "strategy_matrix_round_status.json"
BEIJING_FMT = "%Y-%m-%d %H:%M:%S"


def _now():
    return datetime.now().strftime(BEIJING_FMT)


def _atomic(payload):
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        delete=False, dir=str(STATUS_PATH.parent), mode="w", encoding="utf-8")
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush(); handle.close(); Path(handle.name).replace(STATUS_PATH)
    except Exception:
        try:
            Path(handle.name).unlink()
        except Exception:
            pass
        raise


def _summary(result):
    atlas = result.get("event_atlas") or {}
    active = result.get("active_hunt") or {}
    return {
        "state": result.get("state"),
        "run_id": result.get("run_id"),
        "tested_patterns": int(atlas.get("tested_patterns") or 0),
        "qualified_patterns": int(atlas.get("qualified_count") or 0),
        "target_local_cost_survivors": int(
            ((active.get("probes") or {}).get("cost_surviving_count") or
             (active.get("prior") or {}).get("target_local_cost_survivors") or
             active.get("target_local_cost_survivors") or 0)),
        "suspected_positive_count": int(active.get("suspected_positive_count") or 0),
        "ready_candidates": len(result.get("ready_candidates") or []),
        "converged_count": int(result.get("converged_count") or 0),
        "api_calls_saved": int(result.get("api_calls_saved") or 0),
        "error": result.get("error"),
    }


def run():
    targets = ecosystem._creation_targets()
    policy = ecosystem._read(
        AUTO_DIR / "post_audit_operating_policy.json", {})
    restart_cutoff = str(policy.get("effective_at") or "")
    conn = ecosystem._db()
    try:
        _selected, planner = ecosystem._plan_creation_target(conn, targets)
        completed_after_restart = {}
        if restart_cutoff:
            for symbol, timeframe, state, summary_raw, started_at in conn.execute(
                    "SELECT symbol,timeframe,state,summary_json,started_at FROM "
                    "strategy_creation_runs WHERE started_at>=? AND state NOT IN "
                    "('running','training_error') ORDER BY started_at",
                    (restart_cutoff,)).fetchall():
                try:
                    completed_after_restart[(symbol, timeframe)] = (
                        state, json.loads(summary_raw or "{}"), started_at)
                except Exception:
                    pass
    finally:
        conn.close()
    sleep_by_target = {
        (row.get("symbol"), row.get("timeframe")):
        (((row.get("meta_controller") or {}).get("compute_sleep") or {}))
        for row in (planner.get("ranking") or [])
    }
    # Wake-pool first: run highest-score awake targets before sleeping ones so
    # full-matrix rounds spend wall time on frequency-relevant compute.
    ranking_order = [
        {"symbol": row.get("symbol"), "timeframe": row.get("timeframe")}
        for row in (planner.get("ranking") or [])
        if row.get("symbol") and row.get("timeframe")
    ]
    if ranking_order:
        targets = ranking_order
    round_id = "matrix_%s" % datetime.now().strftime("%Y%m%d_%H%M%S")
    status = {
        "ok": True, "state": "running", "round_id": round_id,
        "started_at": _now(), "finished_at": None,
        "target_count": len(targets), "completed_count": 0,
        "execution_policy": "sequential_wake_pool_priority_resource_bounded",
        "live_execution_changed": False,
        "results": [],
    }
    _atomic(status)
    script = str(Path(ecosystem.__file__).resolve())
    for index, target in enumerate(targets, 1):
        completed = completed_after_restart.get(
            (target["symbol"], target["timeframe"]))
        if completed:
            prior_state, prior_result, prior_started_at = completed
            row = dict(target); row.update(_summary(prior_result))
            row.update({"state": prior_state, "error": None,
                        "reused_post_restart_run": True,
                        "source_started_at": prior_started_at,
                        "completed_at": _now()})
            status["results"].append(row)
            status["completed_count"] = index
            _atomic(status)
            continue
        sleep = sleep_by_target.get((target["symbol"], target["timeframe"])) or {}
        if sleep.get("sleeping"):
            row = dict(target)
            row.update({"state": "sleeping_preserved", "error": None,
                        "ready_candidates": 0, "qualified_patterns": 0,
                        "target_local_cost_survivors": 0,
                        "remaining_sleep_hours": sleep.get("remaining_hours"),
                        "wake_after_sleep": True, "completed_at": _now()})
            status["results"].append(row)
            status["completed_count"] = index
            _atomic(status)
            continue
        status["current"] = target
        status["current_index"] = index
        _atomic(status)
        started = time.time()
        command = [sys.executable, script, "--create-strategy",
                   "--create-symbol", target["symbol"],
                   "--create-timeframe", target["timeframe"]]
        try:
            completed = subprocess.run(
                command, cwd=str(ROOT), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, universal_newlines=True,
                timeout=4*60*60, check=False)
            output = (completed.stdout or "").strip().splitlines()
            result = json.loads(output[-1]) if output else {
                "state": "child_no_json", "error": (completed.stderr or "")[-2000:]}
            row = dict(target)
            row.update(_summary(result))
            row.update({"exit_code": completed.returncode,
                        "elapsed_sec": round(time.time()-started, 3),
                        "completed_at": _now()})
        except Exception as exc:
            row = dict(target)
            row.update({"state": "matrix_child_failed_closed", "error": str(exc),
                        "elapsed_sec": round(time.time()-started, 3),
                        "completed_at": _now(), "ready_candidates": 0})
        status["results"].append(row)
        status["completed_count"] = index
        status["current"] = None
        _atomic(status)
    rows = status["results"]
    # Expensive cognitive maintenance is batched once after breadth search,
    # rather than blocking every deterministic no-edge cluster.
    try:
        status["post_round_cognitive_maintenance"] = {
            "death_distillation": ecosystem.distill_death_knowledge(force=False),
            "solvability_heartbeat": ecosystem.run_system_solvability_audit(
                force=False),
        }
    except Exception as exc:
        status["post_round_cognitive_maintenance"] = {
            "state": "failed_closed", "error": str(exc)}
    status.update({
        "state": "completed", "finished_at": _now(),
        "clusters_with_cost_survivor": sum(
            int(row.get("target_local_cost_survivors") or 0) > 0 for row in rows),
        "clusters_with_qualified_pattern": sum(
            int(row.get("qualified_patterns") or 0) > 0 for row in rows),
        "ready_candidate_count": sum(int(row.get("ready_candidates") or 0)
                                     for row in rows),
        "failed_cluster_count": sum(bool(row.get("error")) for row in rows),
    })
    _atomic(status)
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    return status


if __name__ == "__main__":
    run()
