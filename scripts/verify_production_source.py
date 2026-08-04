# -*- coding: utf-8 -*-
"""Build/verify the one canonical production deployment manifest.

Examples:
  python3 scripts/verify_production_source.py snapshot
  python3 scripts/verify_production_source.py write --output deployment_manifest.json
  python3 scripts/verify_production_source.py verify --manifest deployment_manifest.json
"""
from __future__ import print_function

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "production_source_policy.json"


def _sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(*args):
    try:
        return subprocess.check_output(
            ["git"] + list(args), cwd=str(ROOT), stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return None


def load_policy():
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def build_snapshot():
    policy = load_policy()
    files = {}
    missing = []
    for rel in policy.get("critical_files") or []:
        path = ROOT / rel
        if not path.is_file():
            missing.append(rel)
            continue
        files[rel] = {"sha256": _sha256(path), "size": path.stat().st_size}
    dirty = []
    status = _git("status", "--porcelain", "--", *(policy.get("critical_files") or []))
    if status:
        dirty = [line for line in status.splitlines() if line.strip()]
    return {
        "schema": "qiyu_production_deployment_manifest_v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_root": str(ROOT),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_commit": _git("rev-parse", "HEAD"),
        "policy_sha256": _sha256(POLICY_PATH),
        "policy": policy,
        "critical_files": files,
        "missing_critical_files": missing,
        "dirty_critical_files": dirty,
        "content_complete": not missing,
    }


def verify(manifest):
    expected = json.loads(Path(manifest).read_text(encoding="utf-8"))
    current = build_snapshot()
    failures = []
    if expected.get("schema") != "qiyu_production_deployment_manifest_v1":
        failures.append("manifest_schema_invalid")
    if expected.get("policy_sha256") != current.get("policy_sha256"):
        failures.append("policy_hash_mismatch")
    expected_files = expected.get("critical_files") or {}
    current_files = current.get("critical_files") or {}
    for rel in current.get("policy", {}).get("critical_files") or []:
        want = (expected_files.get(rel) or {}).get("sha256")
        got = (current_files.get(rel) or {}).get("sha256")
        if want is None:
            failures.append("manifest_missing:%s" % rel)
        elif got is None:
            failures.append("runtime_missing:%s" % rel)
        elif want != got:
            failures.append("hash_mismatch:%s" % rel)
    return {
        "ok": not failures,
        "failures": failures,
        "manifest_git_commit": expected.get("git_commit"),
        "runtime_git_commit": current.get("git_commit"),
        "verified_file_count": len(current_files) - sum(
            1 for x in failures if x.startswith("hash_mismatch:")),
        "current": current,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("snapshot", "write", "verify"))
    parser.add_argument("--output", default="deployment_manifest.json")
    parser.add_argument("--manifest", default="deployment_manifest.json")
    args = parser.parse_args()
    if args.command == "snapshot":
        out = build_snapshot()
    elif args.command == "write":
        out = build_snapshot()
        if not out.get("content_complete"):
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 2
        Path(args.output).write_text(
            json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        out = {"ok": True, "output": str(Path(args.output).resolve()), "snapshot": out}
    else:
        out = verify(args.manifest)
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if out.get("ok", out.get("content_complete", False)) else 1


if __name__ == "__main__":
    sys.exit(main())
