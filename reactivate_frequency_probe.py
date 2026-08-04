# -*- coding: utf-8 -*-
"""Reactivate legacy strategies as conditional frequency probes.

Default wave1 keeps the dual screen.  --force-presence unfreezes every
assignment except CL 1h and BTC 15m, at C/B position caps, without minting
passed_all certificates.
"""
from __future__ import print_function

from datetime import datetime
from pathlib import Path
import argparse
import json
import os
import tempfile

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"
AUDIT_PATH = AUTO_DIR / "live_strategy_retrospective_audit.json"
FREQ_PATH = AUTO_DIR / "live_portfolio_frequency.json"
POLICY_PATH = AUTO_DIR / "post_audit_operating_policy.json"
SNAPSHOT_DIR = AUTO_DIR

WAVE1_DEFAULT = [
    "XAG-USDT-SWAP|5m|xag5_session_breakdown_short_ai",
    "NG-USDT-SWAP|5m|ng5_session_exhaustion_reclaim_long_ai",
]

HARD_EXCLUDE_PREFIXES = (
    "CL-USDT-SWAP|1h|",
)
BTC15_PREFIX = "BTC-USDT-SWAP|15m|"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        delete=False, dir=str(path.parent), mode="w", encoding="utf-8")
    try:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        handle.close()
        Path(handle.name).replace(path)
    except Exception:
        try:
            Path(handle.name).unlink()
        except Exception:
            pass
        raise


def _hard_excluded(assignment_id):
    if str(assignment_id).startswith(BTC15_PREFIX):
        controls = _read(CONTROL_PATH, {"assignments": {}})
        row = (controls.get("assignments") or {}).get(assignment_id) or {}
        if row.get("btc15_hard_exclude_exception") and row.get(
                "env_conditional_c_probe"):
            return False
        return True
    return any(str(assignment_id).startswith(prefix)
               for prefix in HARD_EXCLUDE_PREFIXES)


def _live_row(freq, assignment_id):
    for row in freq.get("assignments") or []:
        token = "%s|%s|%s" % (row.get("symbol"), row.get("timeframe"),
                              row.get("strategy_key"))
        if token == assignment_id:
            return row
    return {}


def _audit_row(audit, assignment_id):
    for row in audit.get("results") or []:
        if row.get("assignment_id") == assignment_id:
            return row
    return {}


def screen(assignment_id, freq, audit):
    if _hard_excluded(assignment_id):
        return {"ok": False, "reason": "hard_excluded"}
    live = _live_row(freq, assignment_id)
    audited = _audit_row(audit, assignment_id)
    disposition = str(audited.get("disposition") or "")
    if disposition.startswith("suspended"):
        return {"ok": False, "reason": "audit_suspended",
                "disposition": disposition}
    bt = ((audited.get("evidence") or {}).get("backtests") or {}).get(
        "observed_base") or {}
    trades = int(live.get("trades") or 0)
    wr = float(live.get("win_rate") or 0.0)
    ret = float(live.get("return_pct") or 0.0)
    mean_net = float(bt.get("mean_net_return_pct") or -999.0)
    holdout = float(bt.get("holdout_mean_net_return_pct") or -999.0)
    streak = int(bt.get("max_loss_streak") or 99)
    checks = {
        "live_trades_ge_25": trades >= 25,
        "live_win_rate_ge_70": wr >= 70.0,
        "live_return_positive": ret > 0.0,
        "backtest_mean_net_positive": mean_net > 0.0,
        "backtest_holdout_mean_positive": holdout > 0.0,
        "max_loss_streak_le_3": streak <= 3,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "live": {"trades": trades, "win_rate": wr, "return_pct": ret},
        "backtest": {"mean_net_return_pct": mean_net,
                     "holdout_mean_net_return_pct": holdout,
                     "max_loss_streak": streak,
                     "win_rate_pct": bt.get("win_rate_pct")},
        "disposition": disposition,
    }


def _cap_for_force(live):
    """B-grade only when live sample is already strong; otherwise C-grade."""
    trades = int(live.get("trades") or 0)
    wr = float(live.get("win_rate") or 0.0)
    ret = float(live.get("return_pct") or 0.0)
    if trades >= 25 and wr >= 70.0 and ret > 0:
        return 0.30, "B"
    return 0.10, "C"


def reactivate(assignment_ids=None, max_position_ratio=0.10, dry_run=False,
               force_presence=False):
    controls = _read(CONTROL_PATH, {"assignments": {}})
    assignments = dict(controls.get("assignments") or {})
    freq = _read(FREQ_PATH, {})
    audit = _read(AUDIT_PATH, {})
    if force_presence:
        assignment_ids = sorted(assignments.keys())
    else:
        assignment_ids = list(assignment_ids or WAVE1_DEFAULT)
    accepted = []
    rejected = []
    for assignment_id in assignment_ids:
        if _hard_excluded(assignment_id):
            rejected.append({"assignment_id": assignment_id,
                             "ok": False, "reason": "hard_excluded"})
            continue
        live = _live_row(freq, assignment_id)
        if force_presence:
            result = {"ok": True, "mode": "force_presence",
                      "live": {"trades": live.get("trades"),
                               "win_rate": live.get("win_rate"),
                               "return_pct": live.get("return_pct")},
                      "disposition": (_audit_row(audit, assignment_id)
                                      .get("disposition"))}
            cap, grade = _cap_for_force(live)
        else:
            result = screen(assignment_id, freq, audit)
            if not result.get("ok"):
                rejected.append({"assignment_id": assignment_id, **result})
                continue
            cap, grade = float(max_position_ratio), "C"
        symbol, timeframe, strategy_key = assignment_id.split("|", 2)
        row = dict(assignments.get(assignment_id) or {})
        row.update({
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy_key": strategy_key,
            "audit_state": "conditional_frequency_probe",
            "pause_new_entries": False,
            "new_entries_allowed": True,
            "full_cognitive_matrix_passed": False,
            "reactivation_tier": ("force_presence_wave"
                                  if force_presence else
                                  "conditional_frequency_probe_wave1"),
            "max_position_ratio": float(cap),
            "max_grade": grade,
            "automatic_live_restoration": False,
            "reason": ("人工强制在场探针：除CL1h与BTC15m外恢复开仓；"
                       "仓位限制为%s级；止损与退出管理不变；"
                       "不授予passed_all" % grade),
            "screen": result,
            "reactivated_at": _now(),
            "manual_review_required_for_full_promotion": True,
        })
        assignments[assignment_id] = row
        accepted.append({"assignment_id": assignment_id,
                         "max_position_ratio": cap, "max_grade": grade,
                         **result})
    payload = {
        "schema": "qiyu_strategy_runtime_controls_v1",
        "updated_at": _now(),
        "updated_by": "reactivate_frequency_probe.py",
        "assignments": assignments,
    }
    snapshot = {
        "schema": "qiyu_frequency_probe_reactivation_v1",
        "created_at": _now(),
        "dry_run": bool(dry_run),
        "force_presence": bool(force_presence),
        "accepted": accepted,
        "rejected": rejected,
        "hard_exclude_prefixes": list(HARD_EXCLUDE_PREFIXES),
        "policy": {
            "does_not_grant_passed_all": True,
            "stop_loss_unchanged": True,
            "human_approval_for_new_strategies_unchanged": True,
            "max_daily_entries": 5,
        },
    }
    if not dry_run:
        _atomic(CONTROL_PATH, payload)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        _atomic(SNAPSHOT_DIR / ("frequency_probe_reactivation_%s.json" % stamp),
                snapshot)
        _atomic(SNAPSHOT_DIR / "frequency_probe_reactivation_latest.json",
                snapshot)
        policy = _read(POLICY_PATH, {})
        policy["frequency_probe"] = {
            "enabled": True,
            "wave": "force_presence" if force_presence else 1,
            "activated_at": _now(),
            "assignments": [row["assignment_id"] for row in accepted],
            "excluded_prefixes": list(HARD_EXCLUDE_PREFIXES),
            "max_daily_entries": 5,
            "auto_repause_rules": [
                "consecutive_losses_ge_3",
                "rolling_10_win_rate_below_55",
            ],
            "does_not_grant_passed_all": True,
        }
        policy["updated_at"] = _now()
        _atomic(POLICY_PATH, policy)
    return {"ok": True, "dry_run": bool(dry_run),
            "force_presence": bool(force_presence),
            "accepted_count": len(accepted), "rejected_count": len(rejected),
            "accepted": accepted, "rejected": rejected,
            "controls_path": str(CONTROL_PATH)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-presence", action="store_true",
                        help="Unfreeze all except CL1h and BTC15m")
    parser.add_argument("--max-position-ratio", type=float, default=0.15)
    parser.add_argument("--assignment", action="append", default=[])
    args = parser.parse_args()
    result = reactivate(
        assignment_ids=args.assignment or None,
        max_position_ratio=args.max_position_ratio,
        dry_run=args.dry_run,
        force_presence=args.force_presence,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
