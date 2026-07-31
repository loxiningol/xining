# -*- coding: utf-8 -*-
"""Research experiment ledger — trial budget & multiple-testing inputs.

Every hypothesis / probe / parameter / window attempt MUST be appended here
before any Sharpe-looking number is trusted. Feeds DSR / PBO / effective N.
"""
from __future__ import print_function

import hashlib
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path


_LOCK = threading.Lock()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def ledger_path(run_id=None):
    base = _root() / "auto_trade" / "dual_engine" / "research_ledger"
    base.mkdir(parents=True, exist_ok=True)
    if run_id:
        return base / ("%s.jsonl" % run_id)
    return base / "research_ledger.jsonl"


def new_run_id(prefix="create"):
    return "%s_%s" % (prefix, datetime.now().strftime("%Y%m%d_%H%M%S"))


def _hash_payload(payload):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def append_event(event, run_id=None, path=None):
    """Append one research decision/trial. Never deletes prior rows."""
    row = dict(event or {})
    row.setdefault("at", _now())
    row.setdefault("ts", time.time())
    if run_id:
        row["run_id"] = run_id
    row.setdefault("event_id", _hash_payload(row))
    target = Path(path) if path else ledger_path(run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, default=str) + "\n"
    with _LOCK:
        with open(str(target), "a") as fh:
            fh.write(line)
    return row


def count_trials(run_id=None, path=None, kinds=None):
    """Count recorded trials; kinds filters event_type prefix/list."""
    target = Path(path) if path else ledger_path(run_id)
    if not target.exists():
        return {"ok": True, "n": 0, "by_type": {}, "path": str(target)}
    kinds = set(kinds or [])
    by_type = {}
    n = 0
    with open(str(target), "r") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if run_id and row.get("run_id") != run_id:
                continue
            et = str(row.get("event_type") or "unknown")
            if kinds and et not in kinds and not any(et.startswith(k) for k in kinds):
                continue
            by_type[et] = by_type.get(et, 0) + 1
            n += 1
    return {"ok": True, "n": n, "by_type": by_type, "path": str(target)}


def effective_trial_budget(run_id=None, path=None):
    """Map ledger rows → effective independent trials for DSR/PBO."""
    stats = count_trials(run_id=run_id, path=path)
    by = stats.get("by_type") or {}
    # Weight: each probe/hypothesis/param/window as one trial unit.
    weights = {
        "hypothesis": 1.0,
        "probe": 1.0,
        "expression": 1.0,
        "param_eval": 0.5,
        "window_slice": 0.5,
        "antifalsify": 0.25,
        "mine_factor": 0.5,
    }
    effective = 0.0
    for et, cnt in by.items():
        w = 1.0
        for key, val in weights.items():
            if et.startswith(key):
                w = val
                break
        effective += float(cnt) * w
    return {
        "ok": True,
        "n_rows": stats.get("n") or 0,
        "by_type": by,
        "effective_trials": max(1.0, effective),
        "path": stats.get("path"),
        "note_zh": "有效试验次数来自注册表加权；不得用最终夏普假装单次试验。",
    }


def probe():
    p = ledger_path()
    return {
        "ok": True,
        "provider": "research_ledger_v1",
        "path": str(p),
        "exists": p.exists(),
        "at": _now(),
    }
