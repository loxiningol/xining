# -*- coding: utf-8 -*-
"""Cross-process primitives used by parallel strategy-creation workers."""
from __future__ import print_function

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def lock_dir():
    path = _root() / "auto_trade" / "dual_engine" / "parallel_creation" / "locks"
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def process_lock(name, blocking=True):
    path = lock_dir() / ("%s.lock" % str(name))
    fh = open(str(path), "a+")
    acquired = False
    try:
        if fcntl is None:
            acquired = True
        else:
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(fh.fileno(), flags)
                acquired = True
            except (IOError, OSError):
                acquired = False
        yield fh if acquired else None
    finally:
        if acquired and fcntl is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        fh.close()


def atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".%s.%s.%s.tmp" % (
        path.name, os.getpid(), uuid.uuid4().hex[:8],
    ))
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(str(tmp), str(path))
    return path


def unique_id(prefix):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    micros = int((time.time() % 1.0) * 1000000)
    return "%s_%s_%06d_%s" % (prefix, stamp, micros, uuid.uuid4().hex[:8])
