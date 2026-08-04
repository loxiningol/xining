# -*- coding: utf-8 -*-
"""Freeze new entries for every enabled live assignment without touching exits."""
from __future__ import print_function

from datetime import datetime
from pathlib import Path
import glob
import json
import os
import tempfile


ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"
BEIJING_FMT = "%Y-%m-%d %H:%M:%S"


def _read(path, default):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        delete=False, dir=str(path.parent), mode="w", encoding="utf-8")
    try:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush(); handle.close()
        Path(handle.name).replace(path)
    except Exception:
        try:
            Path(handle.name).unlink()
        except Exception:
            pass
        raise


def discover_live_assignments():
    assignments = []
    for raw_path in sorted(glob.glob(str(AUTO_DIR / "formal_daemon_config*.json"))):
        path = Path(raw_path); config = _read(path, {})
        if not isinstance(config, dict):
            continue
        if config.get("enabled") is not True or config.get("allow_auto_open") is not True:
            continue
        symbol = str(config.get("symbol") or "").upper()
        timeframe = str(config.get("timeframe") or config.get("bar") or "1h").lower()
        keys = config.get("strategy_keys")
        if not isinstance(keys, list):
            keys = [config.get("strategy_key")]
        for key in keys:
            key = str(key or "").strip()
            if symbol and key:
                assignments.append({
                    "assignment_id": "%s|%s|%s" % (symbol, timeframe, key),
                    "symbol": symbol, "timeframe": timeframe,
                    "strategy_key": key, "config_file": str(path),
                })
    unique = {}
    for row in assignments:
        unique[row["assignment_id"]] = row
    return [unique[key] for key in sorted(unique)]


def current_positions():
    rows = []
    for raw_path in sorted(glob.glob(str(AUTO_DIR / "formal_v6_state*.json"))):
        state = _read(raw_path, {})
        current = state.get("current") if isinstance(state, dict) else None
        if isinstance(current, dict) and current.get("status") not in (
                "closed", "failed", "cancelled"):
            rows.append({"state_file": raw_path, "current": current})
    return rows


def freeze(audit_id="retrospective_cognitive_audit_20260723"):
    now = datetime.now().strftime(BEIJING_FMT)
    assignments = discover_live_assignments()
    controls = _read(CONTROL_PATH, {})
    if not isinstance(controls, dict):
        controls = {}
    controls["schema"] = "qiyu_runtime_controls_v2_retrospective_audit"
    controls["global_audit"] = {
        "audit_id": audit_id, "state": "freeze_new_entries_pending_audit",
        "started_at": now, "existing_position_exit_management_continues": True,
        "automatic_unfreeze_allowed": False,
    }
    bucket = controls.setdefault("assignments", {})
    for row in assignments:
        bucket[row["assignment_id"]] = {
            "pause_new_entries": True,
            "reason": "现存实盘策略尚未通过当前认知矩阵回溯审计",
            "audit_id": audit_id, "paused_at": now,
            "manual_review_required": True,
            "existing_position_exit_management_continues": True,
            "source_config": row["config_file"],
        }
    _atomic(CONTROL_PATH, controls)
    verified = _read(CONTROL_PATH, {})
    missing = [row["assignment_id"] for row in assignments
               if not ((verified.get("assignments") or {}).get(
                   row["assignment_id"]) or {}).get("pause_new_entries")]
    result = {
        "ok": not missing, "audit_id": audit_id, "frozen_at": now,
        "assignment_count": len(assignments), "assignments": assignments,
        "missing_freezes": missing, "current_positions": current_positions(),
        "control_path": str(CONTROL_PATH),
        "policy": "new_entries_frozen_existing_position_exits_unchanged",
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    freeze()
