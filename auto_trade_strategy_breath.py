# -*- coding: utf-8 -*-
"""Strategy breath controller — adaptive gate tightness.

Smoothly expands / contracts:
  1) primitive soft-window (2h–12h)
  2) D-grade missing-leaf tolerance (1–2)
  3) overlay retract drawdown threshold (dynamic vs recent vol)

Absolute floors/ceilings prevent over-loosening.  Hysteresis + dwell time
prevent boundary chatter.  Never touches stops, passed_all, or C+ grades.
"""
from __future__ import print_function

from datetime import datetime
from pathlib import Path
import json
import math
import os
import tempfile
import time

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
STATE_PATH = AUTO_DIR / "strategy_breath_state.json"
AUDIT_PATH = AUTO_DIR / "strategy_breath_audit.jsonl"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"
TELEMETRY_DB = AUTO_DIR / "microstructure_telemetry.db"

# Absolute envelopes (never breached).
SOFT_WINDOW_MS_FLOOR = 2 * 60 * 60 * 1000       # 2h
SOFT_WINDOW_MS_CEILING = 12 * 60 * 60 * 1000    # 12h
MISSING_LEAF_TOLERANCE_FLOOR = 1
MISSING_LEAF_TOLERANCE_CEILING = 2
OVERLAY_DD_RETRACT_FLOOR = -0.015               # -1.5% equity of probe book
OVERLAY_DD_RETRACT_CEILING = -0.050             # -5.0%
EMA_ALPHA = 0.25
SCORE_DEADBAND = 0.08
MIN_DWELL_SEC = 30 * 60
DEFAULT_SCORE = 0.45  # slightly tight of mid


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _append_audit(row):
    path = Path(AUDIT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _primitive_change_rate(lookback_sec=3600):
    """Fraction of distinct clusters reassigned in lookback (0–1 proxy)."""
    if not TELEMETRY_DB.exists():
        return 0.3
    import sqlite3
    now_ms = int(time.time() * 1000)
    since = now_ms - int(lookback_sec * 1000)
    conn = sqlite3.connect(str(TELEMETRY_DB))
    try:
        n = conn.execute(
            "SELECT COUNT(DISTINCT cluster_id) FROM "
            "microstructure_primitive_assignments WHERE end_ms>=?",
            (since,)).fetchone()[0]
        total = conn.execute(
            "SELECT COUNT(*) FROM microstructure_primitive_clusters"
        ).fetchone()[0] or 1
    finally:
        conn.close()
    return _clamp(float(n) / float(max(total, 1)), 0.0, 1.0)


def _vol_volume_proxy():
    """Lightweight proxies from controls / breath sensors file if present."""
    sensors = _read(AUTO_DIR / "strategy_breath_sensors.json", {})
    vol_pct = float(sensors.get("volatility_percentile") or 0.5)
    vol_pct = _clamp(vol_pct, 0.0, 1.0)
    volume_pct = float(sensors.get("volume_percentile") or 0.5)
    volume_pct = _clamp(volume_pct, 0.0, 1.0)
    return vol_pct, volume_pct


def _fire_and_pnl_proxy():
    sensors = _read(AUTO_DIR / "strategy_breath_sensors.json", {})
    # fires_per_day relative to target 3
    fires = float(sensors.get("recent_fires_per_day") or 0.0)
    fire_score = _clamp(fires / 3.0, 0.0, 1.5) / 1.5
    # pnl_ratio: rolling probe book net / equity; map [-3%, +3%] → [0,1]
    pnl = float(sensors.get("recent_pnl_ratio") or 0.0)
    pnl_score = _clamp((pnl + 0.03) / 0.06, 0.0, 1.0)
    return fire_score, pnl_score, fires, pnl


def compute_raw_score(inputs=None):
    """Higher score → looser breath (longer soft window, more leaf tolerance)."""
    inputs = dict(inputs or {})
    change = float(inputs.get("primitive_change_rate")
                   if "primitive_change_rate" in inputs
                   else _primitive_change_rate())
    vol_pct, volume_pct = _vol_volume_proxy()
    if "volatility_percentile" in inputs:
        vol_pct = float(inputs["volatility_percentile"])
    if "volume_percentile" in inputs:
        volume_pct = float(inputs["volume_percentile"])
    fire_score, pnl_score, fires, pnl = _fire_and_pnl_proxy()
    if "fire_score" in inputs:
        fire_score = float(inputs["fire_score"])
    if "pnl_score" in inputs:
        pnl_score = float(inputs["pnl_score"])

    # Activity wants loosen; poor PnL wants tighten.
    activity = 0.35 * change + 0.25 * vol_pct + 0.20 * volume_pct + 0.20 * fire_score
    score = 0.65 * activity + 0.35 * pnl_score
    # If fires already high and pnl weak → tighten hard.
    if fires >= 4 and pnl < 0:
        score *= 0.7
    return {
        "raw_score": _clamp(score, 0.0, 1.0),
        "components": {
            "primitive_change_rate": round(change, 4),
            "volatility_percentile": round(vol_pct, 4),
            "volume_percentile": round(volume_pct, 4),
            "fire_score": round(fire_score, 4),
            "pnl_score": round(pnl_score, 4),
            "recent_fires_per_day": fires,
            "recent_pnl_ratio": pnl,
        },
    }


def score_to_outputs(smoothed):
    s = _clamp(float(smoothed), 0.0, 1.0)
    soft_ms = int(SOFT_WINDOW_MS_FLOOR +
                  s * (SOFT_WINDOW_MS_CEILING - SOFT_WINDOW_MS_FLOOR))
    # Leaf tolerance steps at 0.62 with hysteresis handled by dwell.
    leaf = 2 if s >= 0.62 else 1
    leaf = int(_clamp(leaf, MISSING_LEAF_TOLERANCE_FLOOR,
                      MISSING_LEAF_TOLERANCE_CEILING))
    # More volatile / looser breath → allow slightly deeper overlay DD before retract.
    dd = OVERLAY_DD_RETRACT_FLOOR + s * (
        OVERLAY_DD_RETRACT_CEILING - OVERLAY_DD_RETRACT_FLOOR)
    return {
        "soft_window_ms": soft_ms,
        "soft_window_hours": round(soft_ms / 3600000.0, 3),
        "missing_leaf_tolerance": leaf,
        "overlay_dd_retract": round(dd, 4),
        "pretrigger_poll_sec": 15 if s >= 0.55 else 30,
    }


def load_state():
    state = _read(STATE_PATH, {})
    if not state:
        outs = score_to_outputs(DEFAULT_SCORE)
        state = {
            "schema": "qiyu_strategy_breath_v1",
            "smoothed_score": DEFAULT_SCORE,
            "last_change_ts": 0,
            "updated_at": _now(),
            "outputs": outs,
            "human_veto_tighten": False,
        }
    return state


def effective_soft_window_ms():
    # Breath controller removed — neutral mid window for legacy callers.
    return int((SOFT_WINDOW_MS_FLOOR + SOFT_WINDOW_MS_CEILING) / 2)


def effective_missing_leaf_tolerance():
    state = load_state()
    if state.get("human_veto_tighten"):
        return MISSING_LEAF_TOLERANCE_FLOOR
    return int((state.get("outputs") or {}).get("missing_leaf_tolerance")
               or MISSING_LEAF_TOLERANCE_FLOOR)


def effective_overlay_dd_retract():
    state = load_state()
    return float((state.get("outputs") or {}).get("overlay_dd_retract")
                 or OVERLAY_DD_RETRACT_FLOOR)


def run_once(inputs=None, force=False):
    """DISABLED: strategy breath controller removed (designer 2026-07-24)."""
    state = {
        "schema": "qiyu_strategy_breath_v1",
        "disabled": True,
        "reason": "breath_controller_removed",
        "smoothed_score": DEFAULT_SCORE,
        "outputs": {
            "soft_window_ms": effective_soft_window_ms(),
            "missing_leaf_tolerance": MISSING_LEAF_TOLERANCE_FLOOR,
            "overlay_dd_retract": OVERLAY_DD_RETRACT_FLOOR,
        },
        "updated_at": _now(),
        "policy": "disabled; soft-window/leaf-tolerance/overlay no longer active",
    }
    _atomic(STATE_PATH, state)
    return state


def _legacy_breath_run_once(inputs=None, force=False):
    prev = load_state()
    raw = compute_raw_score(inputs)
    prev_s = float(prev.get("smoothed_score") or DEFAULT_SCORE)
    smoothed = (1.0 - EMA_ALPHA) * prev_s + EMA_ALPHA * float(raw["raw_score"])
    now_ts = time.time()
    last_change = float(prev.get("last_change_ts") or 0)
    dwell_ok = force or (now_ts - last_change >= MIN_DWELL_SEC)
    delta = abs(smoothed - prev_s)
    apply = force or (dwell_ok and delta >= SCORE_DEADBAND)
    if not apply:
        # Still refresh sensors view but keep outputs.
        outs = dict(prev.get("outputs") or score_to_outputs(prev_s))
        changed = False
    else:
        outs = score_to_outputs(smoothed)
        changed = True
        last_change = now_ts
    if prev.get("human_veto_tighten"):
        outs = score_to_outputs(0.0)
        outs["soft_window_ms"] = SOFT_WINDOW_MS_FLOOR
        outs["missing_leaf_tolerance"] = MISSING_LEAF_TOLERANCE_FLOOR
    state = {
        "schema": "qiyu_strategy_breath_v1",
        "smoothed_score": round(smoothed, 5),
        "raw": raw,
        "outputs": outs,
        "last_change_ts": last_change,
        "changed": changed,
        "human_veto_tighten": bool(prev.get("human_veto_tighten")),
        "floors": {
            "soft_window_ms": [SOFT_WINDOW_MS_FLOOR, SOFT_WINDOW_MS_CEILING],
            "missing_leaf_tolerance": [
                MISSING_LEAF_TOLERANCE_FLOOR, MISSING_LEAF_TOLERANCE_CEILING],
            "overlay_dd_retract": [
                OVERLAY_DD_RETRACT_FLOOR, OVERLAY_DD_RETRACT_CEILING],
        },
        "policy": ("EMA+deadband+dwell; never relaxes stops/passed_all; "
                   "human_veto_tighten forces floor"),
        "updated_at": _now(),
    }
    _atomic(STATE_PATH, state)
    _append_audit({"time": _now(), "changed": changed, "state": state})
    return state


def set_human_veto_tighten(enabled=True):
    state = load_state()
    state["human_veto_tighten"] = bool(enabled)
    state["updated_at"] = _now()
    if enabled:
        state["outputs"] = score_to_outputs(0.0)
        state["outputs"]["soft_window_ms"] = SOFT_WINDOW_MS_FLOOR
        state["outputs"]["missing_leaf_tolerance"] = MISSING_LEAF_TOLERANCE_FLOOR
    _atomic(STATE_PATH, state)
    return state


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--veto-tighten", action="store_true")
    parser.add_argument("--clear-veto", action="store_true")
    args = parser.parse_args()
    if args.veto_tighten:
        print(json.dumps(set_human_veto_tighten(True), ensure_ascii=False, indent=2))
    elif args.clear_veto:
        print(json.dumps(set_human_veto_tighten(False), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(run_once(force=args.force), ensure_ascii=False, indent=2))
