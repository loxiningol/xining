#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VPS-side seal: write deployed.sha + invent manifest; clear emergency stamp.

Also stamps manor zone ledger (P2) so region status cards show the deploy.

Usage on VPS:
  sudo DEPLOY_SHA=<full_git_sha> python3 /root/scripts/write_deployed_sha_vps.py
  # or: sudo python3 /root/scripts/write_deployed_sha.sh <sha>
  # Optional: DEPLOY_ZONE=manufacture,live  DEPLOY_SUMMARY='…'  DEPLOY_ACTOR=cursor
"""
from __future__ import print_function

import json
import os
import sys

# Allow running as write_deployed_sha.sh via python shebang copy
sys.path.insert(0, "/root")
sys.path.insert(0, "/root/scripts")

from creation_deploy_health_check import (  # noqa: E402
    AUTO, EMERGENCY, SHA_PATH, MANIFEST, MAINT, build_manifest, _now,
)


def _note_zones(sha):
    """Append deploy change(s) to manor zone ledger. Best-effort."""
    notes = []
    try:
        from dual_engine_workflow_v2 import manor_zone_collab as mz
    except Exception as exc:
        return [{"ok": False, "error": "import_manor_zone_collab:%s" % exc}]
    raw_zones = os.environ.get("DEPLOY_ZONE") or os.environ.get("DEPLOY_ZONES") or "manufacture"
    zones = [z.strip() for z in str(raw_zones).split(",") if z.strip()]
    if not zones:
        zones = ["manufacture"]
    summary = (
        os.environ.get("DEPLOY_SUMMARY")
        or os.environ.get("DEPLOY_TITLE")
        or ("部署封印 deployed.sha=%s" % (sha[:12],))
    )
    actor = os.environ.get("DEPLOY_ACTOR") or "cursor"
    branch = os.environ.get("DEPLOY_BRANCH") or None
    detail = os.environ.get("DEPLOY_DETAIL") or (
        "write_deployed_sha 自动登记。共享发明链以制造区为主账。"
    )
    for zone in zones:
        out = mz.append_event(
            zone,
            kind="deploy",
            status="done",
            title_zh="部署封印",
            summary_zh=summary[:240],
            detail_zh=detail[:4000],
            actor=actor,
            git_sha=sha,
            branch=branch,
            services=["deployed.sha", "qiyu-web"],
            doc_paths=[
                "docs/PARALLEL_CREATE_HUBS.md",
                "docs/ci/GITHUB_ROLE_AND_DEPLOY_SEAL.md",
            ],
            related_modules=["invent", "greenhouse"],
            event_id="deploy_seal_%s_%s" % (zone, sha[:12]),
        )
        notes.append({
            "zone": zone,
            "ok": bool(out.get("ok")),
            "id": (out.get("entry") or {}).get("id"),
        })
    return notes


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    sha = os.environ.get("DEPLOY_SHA") or (argv[0] if argv else "")
    sha = str(sha or "").strip()
    if not sha or len(sha) < 7:
        print("FAIL: pass full git SHA as DEPLOY_SHA or argv[0]", file=sys.stderr)
        return 2
    AUTO.mkdir(parents=True, exist_ok=True)
    SHA_PATH.write_text(sha + "\n", encoding="utf-8")
    os.chmod(str(SHA_PATH), 0o644)
    man = build_manifest(sha=sha)
    man["sealed_at"] = _now()
    man["cleared_emergency"] = EMERGENCY.exists()
    MANIFEST.write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if EMERGENCY.exists():
        try:
            EMERGENCY.unlink()
        except Exception:
            pass
    if MAINT.exists():
        try:
            MAINT.unlink()
        except Exception:
            pass
    zone_notes = _note_zones(sha)
    print(json.dumps({
        "ok": True,
        "deployed_sha": sha,
        "manifest": str(MANIFEST),
        "maintenance_cleared": True,
        "manor_zone_notes": zone_notes,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
