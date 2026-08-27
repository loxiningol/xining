#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Idempotent patch: wire record_creation_outcome into quality_inspector ingest.

Used on production so we do not overwrite the entire quality_inspector.py.
"""
from __future__ import print_function

import sys
from pathlib import Path


MARKER = "from .creation_case_store import record_creation_outcome"
HOOK = """
    try:
        from .creation_case_store import record_creation_outcome
        record_creation_outcome(job, payload)
    except Exception:
        pass
""".rstrip() + "\n"


def patch(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return {"ok": True, "changed": False, "reason": "already_patched"}
    needle = "    _mark_creation_job_synced(job.get(\"job_id\"))\n    return {\n"
    if needle not in text:
        # tolerate single quotes
        needle = "    _mark_creation_job_synced(job.get('job_id'))\n    return {\n"
    if needle not in text:
        return {"ok": False, "error": "ingest_hook_anchor_not_found"}
    text2 = text.replace(
        needle,
        "    _mark_creation_job_synced(job.get(\"job_id\"))\n" + HOOK + "    return {\n",
        1,
    )
    if text2 == text:
        return {"ok": False, "error": "replace_noop"}
    bak = path.with_suffix(path.suffix + ".bak_learning_hook")
    if not bak.exists():
        bak.write_text(text, encoding="utf-8")
    path.write_text(text2, encoding="utf-8")
    return {"ok": True, "changed": True, "backup": str(bak)}


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "/root/dual_engine_workflow_v2/quality_inspector.py"
    out = patch(target)
    print(out)
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
