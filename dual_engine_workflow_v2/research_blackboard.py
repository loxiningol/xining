# -*- coding: utf-8 -*-
"""Append-only research blackboard — agents write evidence, never edit peers."""
from __future__ import print_function

import json
import os
import threading
from datetime import datetime
from pathlib import Path


_LOCK = threading.Lock()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def board_path(run_id):
    d = _root() / "auto_trade" / "dual_engine" / "research_blackboard"
    d.mkdir(parents=True, exist_ok=True)
    return d / ("%s.jsonl" % run_id)


def write(run_id, role, evidence_type, payload, experiment_id=None):
    """Roles may only append. payload must be JSON-serializable evidence."""
    row = {
        "at": _now(),
        "run_id": run_id,
        "role": role,
        "evidence_type": evidence_type,
        "experiment_id": experiment_id,
        "payload": payload or {},
    }
    path = board_path(run_id)
    line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    with _LOCK:
        with open(str(path), "a") as fh:
            fh.write(line)
    return row


def read_all(run_id):
    path = board_path(run_id)
    if not path.exists():
        return []
    rows = []
    with open(str(path), "r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def by_role(run_id):
    out = {}
    for row in read_all(run_id):
        role = row.get("role") or "unknown"
        out.setdefault(role, []).append(row)
    return out


def probe():
    return {
        "ok": True,
        "provider": "research_blackboard_v1",
        "rules_zh": [
            "只追加写入",
            "禁止修改他人结论",
            "每条证据须可追溯 experiment_id/数据字段",
        ],
        "at": _now(),
    }
