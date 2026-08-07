# -*- coding: utf-8 -*-
"""Live creation-job progress without circular imports.

research_discovery used to call ``parallel_creation.report_progress``, but
discovery is imported *while* parallel_creation is still loading →
``report_progress`` missing / partial module → exception swallowed → UI frozen
at population 22% while CPU burns.
"""
from __future__ import print_function

import os
import time
from datetime import datetime
from pathlib import Path

from .process_safe_state import atomic_write_json

PROGRESS_STAGES = (
    ("queued", "排队等待", 0),
    ("claimed", "已认领研究槽", 5),
    ("contract", "研究契约编译", 12),
    ("population", "机制种群生成", 22),
    ("committee", "异构委员会评估", 35),
    ("map_elites", "质量—多样性搜索（MAP-Elites）", 48),
    ("probe", "裸探测", 60),
    ("antifalsify", "稳健性诊断", 72),
    ("assembly", "蓝图装配", 82),
    ("formal_review", "交予质检器", 92),
    ("done", "本轮结束", 100),
)
_PROGRESS_FLOOR = {key: pct for key, _zh, pct in PROGRESS_STAGES}
_PROGRESS_THROTTLE_SEC = 1.0
_LAST = {"ts": 0.0, "stage": None, "path": None}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def stage_percent_band(stage_key):
    keys = [k for k, _zh, _pct in PROGRESS_STAGES]
    lo = float(_PROGRESS_FLOOR.get(stage_key, 0))
    try:
        idx = keys.index(stage_key)
    except ValueError:
        return lo, min(100.0, lo + 8.0)
    if idx + 1 < len(keys):
        hi = float(_PROGRESS_FLOOR[keys[idx + 1]])
    else:
        hi = 100.0
    if hi <= lo:
        hi = min(100.0, lo + 1.0)
    return lo, hi


def progress_payload(stage_key, detail="", percent=None, extras=None, done=None, total=None):
    label = stage_key
    pct = percent
    for key, zh, default_pct in PROGRESS_STAGES:
        if key == stage_key:
            label = zh
            if pct is None:
                pct = default_pct
            break
    extras = dict(extras or {})
    if done is not None:
        extras["done"] = int(done)
    if total is not None:
        extras["total"] = int(total)
    if pct is None:
        pct = 0
    if done is not None and total is not None and int(total) > 0 and percent is None:
        lo, hi = stage_percent_band(stage_key)
        frac = max(0.0, min(1.0, float(done) / float(total)))
        pct = lo + (hi - lo) * frac
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


def _read_json(path):
    try:
        import json
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_job_progress(
    path, stage_key, detail="", percent=None, extras=None, done=None, total=None,
):
    """Persist progress+heartbeat onto a running job JSON path."""
    if not path:
        return None
    job = _read_json(path) or {}
    progress = progress_payload(
        stage_key, detail=detail, percent=percent, extras=extras,
        done=done, total=total,
    )
    job["progress"] = progress
    job["heartbeat_at"] = progress["updated_at"]
    job["heartbeat_ts"] = progress["updated_ts"]
    if stage_key == "formal_review":
        job["status"] = "复核中"
    elif not job.get("status") or job.get("status") in ("等待研究", "排队等待"):
        job["status"] = "研究中"
    atomic_write_json(path, job)
    return progress


def report_progress(
    stage_key, detail="", percent=None, extras=None,
    done=None, total=None, force=False, min_interval_sec=None,
):
    """Worker helper driven by QIYU_JOB_PROGRESS_PATH (no parallel_creation import)."""
    path = os.environ.get("QIYU_JOB_PROGRESS_PATH")
    if not path:
        return None
    now = time.time()
    interval = float(
        min_interval_sec
        if min_interval_sec is not None
        else os.environ.get("QIYU_PROGRESS_THROTTLE_SEC") or _PROGRESS_THROTTLE_SEC
    )
    last = _LAST
    same_path = last.get("path") == path
    same_stage = last.get("stage") == stage_key
    if (
        not force
        and same_path
        and same_stage
        and (now - float(last.get("ts") or 0.0)) < interval
    ):
        return None
    progress = write_job_progress(
        path, stage_key, detail=detail, percent=percent, extras=extras,
        done=done, total=total,
    )
    if progress is not None:
        last["ts"] = now
        last["stage"] = stage_key
        last["path"] = path
    return progress
