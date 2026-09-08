#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Register a manor zone collaboration event (agent / deploy hook).

Usage:
  PYTHONPATH=/root VECTOR_ROOT=/root python3 scripts/manor_zone_note.py \\
    --zone manufacture --kind deploy --status done \\
    --title '共享发明链热更' --summary 'kimi_provider failover' --actor cursor
"""
from __future__ import print_function

import argparse
import json
import sys

sys.path.insert(0, "/root")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone", required=True)
    ap.add_argument("--kind", default="change",
                    choices=["process", "change", "deploy", "repair", "experiment"])
    ap.add_argument("--status", default="done",
                    choices=["running", "done", "aborted", "blocked"])
    ap.add_argument("--title", default="")
    ap.add_argument("--summary", default="")
    ap.add_argument("--detail", default="")
    ap.add_argument("--actor", default="cursor")
    ap.add_argument("--sha", default="")
    ap.add_argument("--branch", default="")
    ap.add_argument("--doc", action="append", default=[])
    ap.add_argument("--service", action="append", default=[])
    ap.add_argument("--module", action="append", default=[],
                    help="related_modules id (repeatable), e.g. invent")
    ap.add_argument("--pr", default="", help="pull request URL")
    ap.add_argument("--id", default="")
    args = ap.parse_args(argv)
    from dual_engine_workflow_v2 import manor_zone_collab as mz
    out = mz.append_event(
        args.zone,
        kind=args.kind,
        status=args.status,
        title_zh=args.title,
        summary_zh=args.summary or args.title,
        detail_zh=args.detail,
        actor=args.actor,
        services=args.service,
        git_sha=args.sha or None,
        branch=args.branch or None,
        pr_url=args.pr or None,
        doc_paths=args.doc,
        related_modules=args.module,
        event_id=args.id or None,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
