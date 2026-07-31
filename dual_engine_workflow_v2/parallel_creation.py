# -*- coding: utf-8 -*-
"""Two-lane job coordinator for the sole strategy-creation pipeline.

Cursor, Codex, human, Web and timer submissions all become immutable jobs.
Two named pipelines (管道1 / 管道2) may execute different research directions
concurrently while each mission retains an isolated ledger, blackboard,
artifact directory and formal-review handoff receipt.
"""
from __future__ import print_function

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from .process_safe_state import atomic_write_json, process_lock, unique_id


SCHEMA = "qiyu_parallel_creation_job_v1"
ALLOWED_SOURCES = ("cursor", "codex", "human", "web", "system_timer", "direct")
PIPELINE_LABELS = {1: "管道1", 2: "管道2"}
PROGRESS_STAGES = (
    ("queued", "排队等待", 0),
    ("claimed", "已认领研究槽", 5),
    ("contract", "研究契约编译", 12),
    ("population", "机制种群生成", 22),
    ("committee", "异构委员会评估", 35),
    ("map_elites", "质量—多样性搜索（MAP-Elites）", 48),
    ("probe", "裸探测 / 摩擦检验", 60),
    ("antifalsify", "抗证伪与稳健性", 72),
    ("assembly", "蓝图装配与门控", 82),
    ("formal_review", "交予四阶段复核", 92),
    ("done", "本轮结束", 100),
)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def base_dir():
    path = _root() / "auto_trade" / "dual_engine" / "parallel_creation"
    for name in ("pending", "running", "completed", "failed", "artifacts", "locks"):
        (path / name).mkdir(parents=True, exist_ok=True)
    return path


def _direction_key(text):
    normalized = " ".join(str(text or "").strip().lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def _job_path(state, job_id):
    return base_dir() / state / ("%s.json" % job_id)


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _normalize_pipeline(pipeline):
    if pipeline in (None, "", "auto", "any"):
        return None
    try:
        n = int(pipeline)
    except Exception:
        text = str(pipeline).strip()
        if text in ("管道1", "pipeline1", "p1", "slot0"):
            n = 1
        elif text in ("管道2", "pipeline2", "p2", "slot1"):
            n = 2
        else:
            raise ValueError("pipeline must be 1 or 2")
    if n not in (1, 2):
        raise ValueError("pipeline must be 1 or 2")
    return n


def pipeline_from_slot(slot):
    try:
        return int(slot) + 1
    except Exception:
        return None


def slot_from_pipeline(pipeline):
    p = _normalize_pipeline(pipeline)
    return None if p is None else (p - 1)


def _progress_payload(stage_key, detail="", percent=None, extras=None):
    label = stage_key
    pct = percent
    for key, zh, default_pct in PROGRESS_STAGES:
        if key == stage_key:
            label = zh
            if pct is None:
                pct = default_pct
            break
    if pct is None:
        pct = 0
    out = {
        "stage": stage_key,
        "stage_zh": label,
        "detail": str(detail or label),
        "percent": max(0, min(100, int(round(float(pct))))),
        "updated_at": _now(),
        "updated_ts": time.time(),
    }
    if extras:
        out.update(extras)
    return out


def update_job_progress(job_or_path, stage_key, detail="", percent=None, extras=None):
    """Persist live progress onto a running/pending job JSON."""
    path = None
    job = None
    if isinstance(job_or_path, dict):
        job = dict(job_or_path)
        path = job.get("running_path") or job.get("queue_path")
        if not path and job.get("job_id"):
            for state in ("running", "pending"):
                candidate = _job_path(state, job["job_id"])
                if candidate.exists():
                    path = str(candidate)
                    break
    else:
        path = str(job_or_path)
        job = _read(path) or {}
    if not path:
        return None
    progress = _progress_payload(stage_key, detail=detail, percent=percent, extras=extras)
    job["progress"] = progress
    job["status"] = job.get("status") or "研究中"
    if stage_key == "formal_review":
        job["status"] = "正式复核中"
    atomic_write_json(path, job)
    return progress


def report_progress(stage_key, detail="", percent=None, extras=None):
    """Worker-side helper: update the job pointed by QIYU_JOB_PROGRESS_PATH."""
    path = os.environ.get("QIYU_JOB_PROGRESS_PATH")
    if not path:
        return None
    return update_job_progress(path, stage_key, detail=detail, percent=percent, extras=extras)


def start_workers(preferred_pipeline=None):
    """Wake bounded systemd slots; optionally prefer one pipeline."""
    try:
        units = ["qiyu-creation-worker@0.service", "qiyu-creation-worker@1.service"]
        slot = slot_from_pipeline(preferred_pipeline)
        if slot is not None:
            units = ["qiyu-creation-worker@%s.service" % slot] + [
                u for u in units if u != ("qiyu-creation-worker@%s.service" % slot)
            ]
        subprocess.Popen(
            ["systemctl", "start", "--no-block"] + units,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return True
    except Exception:
        return False


def submit_job(
    source,
    research_direction,
    symbol="ADA-USDT-SWAP",
    timeframe="5m",
    direction="long",
    brief="",
    skip_llm=True,
    max_loops=5,
    wake_workers=True,
    pipeline=None,
):
    source = str(source or "human").strip().lower()
    if source not in ALLOWED_SOURCES:
        raise ValueError("source must be one of: %s" % ", ".join(ALLOWED_SOURCES))
    research_direction = str(research_direction or brief or "").strip()
    if not research_direction:
        raise ValueError("research_direction is required")
    preferred = _normalize_pipeline(pipeline)
    job_id = unique_id("creation")
    job = {
        "schema": SCHEMA,
        "job_id": job_id,
        "status": "等待研究",
        "source": source,
        "research_direction": research_direction,
        "direction_key": _direction_key(research_direction),
        "symbol": str(symbol).upper(),
        "timeframe": str(timeframe).lower(),
        "trade_direction": str(direction).lower(),
        "brief": str(brief or research_direction),
        "skip_llm": bool(skip_llm),
        "max_loops": max(1, min(int(max_loops), 12)),
        "preferred_pipeline": preferred,
        "pipeline": preferred,
        "pipeline_label": PIPELINE_LABELS.get(preferred),
        "submitted_at": _now(),
        "submitted_ts": time.time(),
        "formal_review_started": False,
        "live_execution_changed": False,
        "progress": _progress_payload(
            "queued",
            detail="已进入唯一创造入口排队" + (
                (" · 指定%s" % PIPELINE_LABELS[preferred]) if preferred else ""
            ),
            percent=0,
        ),
    }
    path = _job_path("pending", job_id)
    job["queue_path"] = str(path)
    atomic_write_json(path, job)
    woke = start_workers(preferred_pipeline=preferred) if wake_workers else False
    return {
        "ok": True,
        "job_id": job_id,
        "status": job["status"],
        "source": source,
        "research_direction": research_direction,
        "pipeline": preferred,
        "pipeline_label": job.get("pipeline_label"),
        "queue_path": str(path),
        "workers_woken": woke,
        "progress": job.get("progress"),
    }


def _try_direction_lock(direction_key):
    path = base_dir() / "locks" / ("direction_%s.lock" % direction_key)
    fh = open(str(path), "a+")
    if fcntl is None:
        return fh
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except (IOError, OSError):
        fh.close()
        return None


def _release_file_lock(fh):
    if fh is None:
        return
    if fcntl is not None:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
    fh.close()


def claim_next(slot):
    """Atomically claim the oldest eligible job for this worker slot/pipeline."""
    slot = int(slot)
    pipeline = pipeline_from_slot(slot)
    with process_lock("parallel_queue_claim"):
        pending = sorted(
            (base_dir() / "pending").glob("*.json"),
            key=lambda p: (p.stat().st_mtime, p.name),
        )
        # Prefer jobs that explicitly asked for this pipeline.
        ordered = []
        for path in pending:
            job = _read(path)
            if not job:
                continue
            preferred = _normalize_pipeline(job.get("preferred_pipeline") or job.get("pipeline"))
            ordered.append((0 if preferred == pipeline else 1, path, job, preferred))
        ordered.sort(key=lambda row: (row[0], row[1].stat().st_mtime, row[1].name))
        for _prio, path, job, preferred in ordered:
            if preferred is not None and preferred != pipeline:
                continue
            direction_lock = _try_direction_lock(job.get("direction_key") or "unknown")
            if direction_lock is None:
                continue
            target = _job_path("running", job["job_id"])
            try:
                os.replace(str(path), str(target))
            except OSError:
                _release_file_lock(direction_lock)
                continue
            job.update({
                "status": "研究中",
                "worker_slot": slot,
                "pipeline": pipeline,
                "pipeline_label": PIPELINE_LABELS.get(pipeline),
                "started_at": _now(),
                "started_ts": time.time(),
                "running_path": str(target),
                "progress": _progress_payload(
                    "claimed",
                    detail="%s 已开始研究：%s" % (
                        PIPELINE_LABELS.get(pipeline),
                        job.get("research_direction") or "",
                    ),
                    percent=5,
                ),
            })
            atomic_write_json(target, job)
            return job, target, direction_lock
    return None, None, None


def recover_stale_jobs(max_age_seconds=5 * 3600, max_retries=2):
    """Recover jobs left in running after a killed/OOM worker."""
    recovered = []
    now = time.time()
    with process_lock("parallel_queue_claim"):
        for path in sorted((base_dir() / "running").glob("*.json")):
            job = _read(path)
            if not job:
                continue
            started = float(job.get("started_ts") or path.stat().st_mtime)
            if now - started < float(max_age_seconds):
                continue
            retries = int(job.get("recovery_count") or 0) + 1
            job["recovery_count"] = retries
            job["recovered_at"] = _now()
            if retries > int(max_retries):
                job["status"] = "研究进程异常且重试次数已用尽"
                job["progress"] = _progress_payload("done", detail=job["status"], percent=100)
                target = _job_path("failed", job["job_id"])
            else:
                job["status"] = "等待恢复研究"
                job.pop("worker_slot", None)
                job.pop("started_at", None)
                job.pop("started_ts", None)
                job["pipeline"] = job.get("preferred_pipeline")
                job["pipeline_label"] = PIPELINE_LABELS.get(job.get("pipeline"))
                job["progress"] = _progress_payload(
                    "queued", detail="异常恢复后重新排队", percent=0,
                )
                target = _job_path("pending", job["job_id"])
            atomic_write_json(target, job)
            try:
                path.unlink()
            except Exception:
                pass
            recovered.append(job["job_id"])
    return recovered


def _compact_result(result):
    blueprint = (result or {}).get("blueprint") or {}
    discovery = (blueprint.get("stages") or {}).get("research_discovery") or {}
    map_elites = discovery.get("map_elites") or discovery.get("archive_elites") or {}
    return {
        "ok": bool((result or {}).get("ok")),
        "present_to_human": bool((result or {}).get("present_to_human")),
        "pipeline_gate": (result or {}).get("pipeline_gate"),
        "handoff_zh": (result or {}).get("handoff_zh"),
        "receipt_path": (result or {}).get("receipt_path"),
        "run_id": blueprint.get("run_id"),
        "n_survivors": discovery.get("n_survivors"),
        "map_elites": {
            "filled_cells": map_elites.get("filled_cells") or map_elites.get("n_filled"),
            "archive_size": map_elites.get("archive_size") or map_elites.get("n_elites"),
        },
        "deliverables": blueprint.get("deliverables"),
    }


def execute_claimed(job, running_path, slot):
    artifact_dir = base_dir() / "artifacts" / job["job_id"]
    artifact_dir.mkdir(parents=True, exist_ok=True)
    pipeline = pipeline_from_slot(slot)
    os.environ["QIYU_JOB_PROGRESS_PATH"] = str(running_path)
    os.environ["QIYU_CREATION_PIPELINE"] = str(pipeline or "")
    update_job_progress(
        running_path, "contract",
        detail="%s 进入唯一创造管道：编译研究契约" % PIPELINE_LABELS.get(pipeline, ""),
        percent=12,
    )
    if os.environ.get("QIYU_PARALLEL_CREATION_SELFTEST") == "1":
        time.sleep(float(os.environ.get("QIYU_PARALLEL_SELFTEST_DELAY") or 1.0))
        update_job_progress(running_path, "map_elites", detail="自测：质量—多样性搜索", percent=48)
        time.sleep(0.2)
        result = {
            "ok": True,
            "present_to_human": False,
            "handoff_zh": "并行隔离自测完成",
            "blueprint": {"run_id": job["job_id"], "stages": {}},
        }
    else:
        from .creation_sole_entry import create_strategy
        update_job_progress(
            running_path, "population",
            detail="机制种群 / 委员会 / 质量—多样性搜索进行中",
            percent=22,
        )
        result = create_strategy(
            symbol=job["symbol"],
            timeframe=job["timeframe"],
            direction=job["trade_direction"],
            brief=job["brief"],
            skip_llm=job["skip_llm"],
            max_loops=job["max_loops"],
            mission_id=job["job_id"],
            source=job["source"],
            research_direction=job["research_direction"],
            out_dir=artifact_dir,
        )
    summary = _compact_result(result)
    finished = dict(job)
    ready = bool(summary.get("ok") and summary.get("present_to_human"))
    formal_review = {
        "ok": False,
        "started": False,
        "reason": "creation_candidate_not_qualified",
    }
    if ready:
        atomic_write_json(artifact_dir / "qualified_blueprint.json", result.get("blueprint") or {})
        atomic_write_json(artifact_dir / "formal_review_handoff.json", {
            "schema": "qiyu_formal_review_handoff_v1",
            "job_id": job["job_id"],
            "pipeline": pipeline,
            "pipeline_label": PIPELINE_LABELS.get(pipeline),
            "source": job["source"],
            "research_direction": job["research_direction"],
            "status": "已交由四阶段复核",
            "qualified_blueprint": str(artifact_dir / "qualified_blueprint.json"),
            "result_receipt": summary.get("receipt_path"),
            "deliverables": summary.get("deliverables"),
            "created_at": _now(),
            "automatic_live_deployment": False,
        })
        update_job_progress(
            running_path, "formal_review",
            detail="合格产出已交予四阶段复核",
            percent=92,
            extras={"handoff_ready": True},
        )
        if str(os.environ.get("QIYU_AUTO_FORMAL_REVIEW") or "1").lower() not in (
            "0", "false", "no", "off",
        ):
            from .formal_review_bridge import submit_blueprint_to_formal_review
            formal_review = submit_blueprint_to_formal_review(result.get("blueprint") or {}, job)
        else:
            formal_review = {
                "ok": True,
                "started": False,
                "reason": "automatic_formal_review_disabled",
            }
    if ready and formal_review.get("started"):
        final_status = "等待人工审批" if formal_review.get("ok") else "正式复核未通过"
    elif ready:
        final_status = "等待正式复核"
    else:
        final_status = "本轮未形成可信候选"
    me = summary.get("map_elites") or {}
    detail = final_status
    if me.get("filled_cells") is not None:
        detail = "%s · MAP-Elites 填充格 %s" % (final_status, me.get("filled_cells"))
    finished.update({
        "status": final_status,
        "pipeline": pipeline,
        "pipeline_label": PIPELINE_LABELS.get(pipeline),
        "worker_slot": int(slot),
        "finished_at": _now(),
        "finished_ts": time.time(),
        "duration_seconds": round(time.time() - float(job.get("started_ts") or time.time()), 3),
        "result": summary,
        "artifact_dir": str(artifact_dir),
        "formal_review_handoff_ready": ready,
        "formal_review_started": bool(formal_review.get("started")),
        "formal_review": formal_review,
        "live_execution_changed": False,
        "progress": _progress_payload("done", detail=detail, percent=100),
    })
    state = "completed" if result is not None else "failed"
    final_path = _job_path(state, job["job_id"])
    atomic_write_json(final_path, finished)
    try:
        Path(running_path).unlink()
    except Exception:
        pass
    os.environ.pop("QIYU_JOB_PROGRESS_PATH", None)
    return finished


def worker(slot, drain=True):
    slot = int(slot)
    if slot not in (0, 1):
        raise ValueError("worker slot must be 0 or 1")
    with process_lock("creation_capacity_%s" % slot, blocking=False) as capacity_lock:
        if capacity_lock is None:
            return {
                "ok": True,
                "status": "研究槽已占用",
                "slot": slot,
                "pipeline": pipeline_from_slot(slot),
                "pipeline_label": PIPELINE_LABELS.get(pipeline_from_slot(slot)),
                "processed": 0,
            }
        old = os.environ.get("QIYU_CREATION_SLOT_HELD")
        os.environ["QIYU_CREATION_SLOT_HELD"] = str(slot)
        recover_stale_jobs()
        processed = []
        empty_checks = 0
        try:
            while True:
                job, running_path, direction_lock = claim_next(slot)
                if not job:
                    empty_checks += 1
                    if empty_checks < 4:
                        time.sleep(0.5)
                        continue
                    break
                empty_checks = 0
                try:
                    processed.append(execute_claimed(job, running_path, slot))
                except Exception as exc:
                    failed = dict(job)
                    failed.update({
                        "status": "研究执行异常",
                        "error": str(exc),
                        "pipeline": pipeline_from_slot(slot),
                        "pipeline_label": PIPELINE_LABELS.get(pipeline_from_slot(slot)),
                        "finished_at": _now(),
                        "finished_ts": time.time(),
                        "formal_review_started": False,
                        "live_execution_changed": False,
                        "progress": _progress_payload(
                            "done", detail="研究执行异常：%s" % exc, percent=100,
                        ),
                    })
                    atomic_write_json(_job_path("failed", job["job_id"]), failed)
                    try:
                        Path(running_path).unlink()
                    except Exception:
                        pass
                    processed.append(failed)
                finally:
                    _release_file_lock(direction_lock)
                if not drain:
                    break
        finally:
            if old is None:
                os.environ.pop("QIYU_CREATION_SLOT_HELD", None)
            else:
                os.environ["QIYU_CREATION_SLOT_HELD"] = old
        return {
            "ok": True,
            "status": "研究槽本轮完成",
            "slot": slot,
            "pipeline": pipeline_from_slot(slot),
            "pipeline_label": PIPELINE_LABELS.get(pipeline_from_slot(slot)),
            "processed": len(processed),
            "job_ids": [j.get("job_id") for j in processed],
        }


def _row_from_job(row):
    pipeline = row.get("pipeline")
    if pipeline is None and row.get("worker_slot") is not None:
        pipeline = pipeline_from_slot(row.get("worker_slot"))
    progress = row.get("progress") or {}
    return {
        "job_id": row.get("job_id"),
        "status": row.get("status"),
        "source": row.get("source"),
        "research_direction": row.get("research_direction"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "worker_slot": row.get("worker_slot"),
        "pipeline": pipeline,
        "pipeline_label": row.get("pipeline_label") or PIPELINE_LABELS.get(pipeline),
        "preferred_pipeline": row.get("preferred_pipeline"),
        "progress": progress,
        "submitted_at": row.get("submitted_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "formal_review_started": row.get("formal_review_started"),
        "formal_review": row.get("formal_review"),
        "result": row.get("result"),
        "error": row.get("error"),
    }


def _build_pipelines(pending, running, completed, failed):
    pipelines = []
    for pipeline in (1, 2):
        slot = pipeline - 1
        active = [r for r in running if r.get("pipeline") == pipeline or r.get("worker_slot") == slot]
        queued = [
            r for r in pending
            if (r.get("preferred_pipeline") or r.get("pipeline")) in (pipeline, None)
            or r.get("preferred_pipeline") is None
        ]
        # queued for display: jobs assigned to this pipe OR unassigned waiting
        if pipeline == 1:
            pipe_queued = [
                r for r in pending
                if r.get("preferred_pipeline") in (1, None) and r.get("pipeline") in (1, None)
            ]
        else:
            pipe_queued = [
                r for r in pending
                if r.get("preferred_pipeline") in (2, None) and r.get("pipeline") in (2, None)
            ]
        # Avoid double-counting unassigned pending on both pipes: show unassigned only on pipe1 idle hint
        if pipeline == 2:
            pipe_queued = [r for r in pending if r.get("preferred_pipeline") == 2]
        else:
            pipe_queued = [r for r in pending if r.get("preferred_pipeline") in (1, None)]
        recent = [
            r for r in (completed + failed)
            if r.get("pipeline") == pipeline or r.get("worker_slot") == slot
        ][:8]
        current = active[0] if active else None
        working = bool(current)
        progress = (current or {}).get("progress") or (
            {"stage": "idle", "stage_zh": "空闲", "detail": "等待研究方向", "percent": 0}
        )
        pipelines.append({
            "pipeline": pipeline,
            "pipeline_label": PIPELINE_LABELS[pipeline],
            "worker_slot": slot,
            "working": working,
            "state": "研究中" if working else ("排队中" if pipe_queued else "空闲"),
            "current_job": current,
            "queued": pipe_queued[:10],
            "recent": recent,
            "progress": progress,
            "research_direction": (current or {}).get("research_direction")
            or ((pipe_queued[0].get("research_direction") if pipe_queued else None)),
            "source": (current or {}).get("source"),
        })
    return pipelines


def status():
    out = {
        "ok": True,
        "schema": SCHEMA,
        "maximum_parallel_missions": 2,
        "entry": "唯一创造入口",
        "module_zh": "策略创造演进模块",
        "updated_at": _now(),
    }
    bags = {}
    for state in ("pending", "running", "completed", "failed"):
        rows = []
        for path in sorted((base_dir() / state).glob("*.json"), reverse=True)[:50]:
            row = _read(path) or {}
            rows.append(_row_from_job(row))
        bags[state] = rows
        out[state] = rows
    out["pipelines"] = _build_pipelines(
        bags["pending"], bags["running"], bags["completed"], bags["failed"],
    )
    out["working_count"] = sum(1 for p in out["pipelines"] if p.get("working"))
    return out


def review_status():
    """Four-stage review board linked to the dual creation pipelines."""
    try:
        from . import review_lexicon as lex
        stages = [
            {"id": 1, "name": lex.REVIEW_1, "scope": lex.REVIEW_1_SCOPE},
            {"id": 2, "name": lex.REVIEW_2, "scope": lex.REVIEW_2_SCOPE},
            {"id": 3, "name": lex.REVIEW_3, "scope": lex.REVIEW_3_SCOPE},
            {"id": 4, "name": lex.REVIEW_4, "scope": lex.REVIEW_4_SCOPE},
        ]
        human_gate = lex.HUMAN_CONFIRM_GATE
        note = lex.HUMAN_CONFIRM_NOTE
    except Exception:
        stages = [
            {"id": 1, "name": "第一次复核", "scope": "基础语法、逻辑断言、开仓密度预检"},
            {"id": 2, "name": "第二次复核", "scope": "单标的近2年加权回测与样本收益稳定性"},
            {"id": 3, "name": "第三次复核", "scope": "多标的近2年矩阵验证与抗风险离群测试"},
            {"id": 4, "name": "第四次复核", "scope": "三AI理论复核"},
        ]
        human_gate = "人工确认签发"
        note = "四阶段复核通过后进入人工确认；永不自动上线"

    st = status()
    handoffs = []
    for state in ("running", "completed"):
        for row in st.get(state) or []:
            fr = row.get("formal_review") or {}
            if row.get("formal_review_started") or fr.get("started") or row.get("status") in (
                "正式复核中", "等待人工审批", "等待正式复核", "正式复核未通过",
            ):
                handoffs.append({
                    "job_id": row.get("job_id"),
                    "pipeline": row.get("pipeline"),
                    "pipeline_label": row.get("pipeline_label"),
                    "research_direction": row.get("research_direction"),
                    "source": row.get("source"),
                    "status": row.get("status"),
                    "formal_review": fr,
                    "progress": row.get("progress"),
                    "symbol": row.get("symbol"),
                    "timeframe": row.get("timeframe"),
                })

    formal_recent = []
    try:
        import auto_trade_dual_engine_factory as dual
        dual_status = dual.load_status() or {}
        formal_recent = dual_status.get("formal_recent") or []
    except Exception:
        formal_recent = []

    return {
        "ok": True,
        "schema": "qiyu_strategy_creation_review_board_v1",
        "module_zh": "策略创造复核模块",
        "review_name_zh": "四阶段复核",
        "stages": stages,
        "human_confirm_gate": human_gate,
        "note_zh": note,
        "connected_module_zh": "策略创造演进模块",
        "pipelines": st.get("pipelines") or [],
        "handoffs_from_pipelines": handoffs[:30],
        "formal_recent": formal_recent[:20],
        "automatic_live_deployment": False,
        "updated_at": _now(),
    }


def self_test():
    temp_root = tempfile.mkdtemp(prefix="qiyu_parallel_creation_")
    env = dict(os.environ)
    env["VECTOR_ROOT"] = temp_root
    env["QIYU_PARALLEL_CREATION_SELFTEST"] = "1"
    env["QIYU_PARALLEL_SELFTEST_DELAY"] = "1.2"
    old_root = os.environ.get("VECTOR_ROOT")
    os.environ["VECTOR_ROOT"] = temp_root
    try:
        one = submit_job(
            "cursor", "衰竭回收型策略方向", brief="并行自测一",
            wake_workers=False, pipeline=1,
        )
        two = submit_job(
            "codex", "成交量异动型策略方向", brief="并行自测二",
            wake_workers=False, pipeline=2,
        )
        p0 = subprocess.Popen(
            [sys.executable, "-m", __package__ + ".parallel_creation", "worker", "--slot", "0"],
            env=env,
        )
        p1 = subprocess.Popen(
            [sys.executable, "-m", __package__ + ".parallel_creation", "worker", "--slot", "1"],
            env=env,
        )
        rc0, rc1 = p0.wait(), p1.wait()
        rows = []
        for jid in (one["job_id"], two["job_id"]):
            rows.append(_read(_job_path("completed", jid)) or {})
        overlap = False
        if len(rows) == 2 and all(r.get("started_ts") and r.get("finished_ts") for r in rows):
            overlap = max(r["started_ts"] for r in rows) < min(r["finished_ts"] for r in rows)
        board = status()
        return {
            "ok": rc0 == 0 and rc1 == 0 and overlap and len(board.get("pipelines") or []) == 2,
            "two_distinct_jobs": one["job_id"] != two["job_id"],
            "sources": sorted([r.get("source") for r in rows]),
            "worker_slots": sorted([r.get("worker_slot") for r in rows]),
            "pipelines": sorted([r.get("pipeline") for r in rows]),
            "pipeline_labels": [p.get("pipeline_label") for p in board.get("pipelines") or []],
            "execution_overlapped": overlap,
            "formal_review_started": False,
            "live_execution_changed": False,
            "test_root": temp_root,
        }
    finally:
        if old_root is None:
            os.environ.pop("VECTOR_ROOT", None)
        else:
            os.environ["VECTOR_ROOT"] = old_root


def main():
    ap = argparse.ArgumentParser(description="唯一创造入口的双研究槽协调器（管道1/管道2）")
    sub = ap.add_subparsers(dest="command")
    sp = sub.add_parser("submit")
    sp.add_argument("--source", required=True, choices=ALLOWED_SOURCES)
    sp.add_argument("--research-direction", required=True)
    sp.add_argument("--symbol", default="ADA-USDT-SWAP")
    sp.add_argument("--timeframe", default="5m")
    sp.add_argument("--direction", default="long", choices=("long", "short", "both"))
    sp.add_argument("--brief", default="")
    sp.add_argument("--brief-file", default="")
    sp.add_argument("--with-llm", action="store_true")
    sp.add_argument("--max-loops", type=int, default=5)
    sp.add_argument("--pipeline", default="", help="1/2 或 管道1/管道2；空=自动分配")
    wp = sub.add_parser("worker")
    wp.add_argument("--slot", required=True, type=int, choices=(0, 1))
    wp.add_argument("--one", action="store_true")
    sub.add_parser("status")
    sub.add_parser("review-status")
    sub.add_parser("self-test")
    args = ap.parse_args()

    if args.command == "submit":
        brief = args.brief
        if args.brief_file:
            brief = Path(args.brief_file).read_text(encoding="utf-8")
        out = submit_job(
            args.source, args.research_direction,
            symbol=args.symbol, timeframe=args.timeframe, direction=args.direction,
            brief=brief, skip_llm=not args.with_llm, max_loops=args.max_loops,
            pipeline=(args.pipeline or None),
        )
    elif args.command == "worker":
        out = worker(args.slot, drain=not args.one)
    elif args.command == "status":
        out = status()
    elif args.command == "review-status":
        out = review_status()
    elif args.command == "self-test":
        out = self_test()
    else:
        ap.error("command is required")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main() or 0)
