# -*- coding: utf-8 -*-
"""L0 density pre-check — kill AND-clogged strategies before any matrix BT.

Counts bars where the DSL entry expression is true (no exits / no fills).
Rejects when historical trigger density is too rare to ever clear L1 sample floors.
Target wall time: tens of ms on a 12k-bar primary frame (stride sampling).
"""
from __future__ import print_function

import time


# Absolute trigger floor on the evaluated window (after stride).
# With stride=2 on ~12k bars ≈ 6k samples; n<30 ⇒ hopeless for L1 filled≥5.
MIN_TRIGGERS = 30
# Relative density floor (triggers / evaluated bars)
MIN_DENSITY = 0.0005  # 0.05%
MAX_LOOKBACK_START = 250
DEFAULT_STRIDE = 2


def count_entry_triggers(frame, definition, stride=DEFAULT_STRIDE, start=None):
    """Return (n_triggers, n_evaluated, wall_ms). Fail-closed on errors → 0."""
    t0 = time.perf_counter()
    try:
        import auto_trade_strategy_dsl as dsl_mod
        strategy = definition if isinstance(definition, dict) else {}
        if not strategy.get("entry"):
            return 0, 0, (time.perf_counter() - t0) * 1000.0
        # validate once
        strategy = dsl_mod.validate_strategy(strategy)
        n = len(frame) if frame is not None else 0
        if n <= 0:
            return 0, 0, (time.perf_counter() - t0) * 1000.0
        stride = max(1, int(stride or 1))
        i0 = int(start if start is not None else MAX_LOOKBACK_START)
        i0 = max(i0, MAX_LOOKBACK_START)
        triggers = 0
        evaluated = 0
        evaluate = dsl_mod.evaluate_expression
        entry = strategy["entry"]
        for index in range(i0, n, stride):
            evaluated += 1
            try:
                ok, _ = evaluate(frame, index, entry, explain=False)
            except Exception:
                continue
            if ok:
                triggers += 1
        wall_ms = (time.perf_counter() - t0) * 1000.0
        return int(triggers), int(evaluated), wall_ms
    except Exception:
        wall_ms = (time.perf_counter() - t0) * 1000.0
        return 0, 0, wall_ms


def run_l0_density(definition=None, frame=None, stride=DEFAULT_STRIDE,
                   min_triggers=MIN_TRIGGERS, min_density=MIN_DENSITY):
    """L0 verdict. pass=False → REJECT_TOO_RARE (do not enter matrix)."""
    triggers, evaluated, wall_ms = count_entry_triggers(
        frame, definition, stride=stride,
    )
    density = (float(triggers) / float(evaluated)) if evaluated else 0.0
    reasons = []
    if evaluated <= 0 or frame is None:
        reasons.append("l0_no_frame")
    if triggers < int(min_triggers):
        reasons.append("REJECT_TOO_RARE")
        reasons.append("l0_triggers_lt_%d" % int(min_triggers))
    if evaluated > 0 and density < float(min_density):
        reasons.append("l0_density_lt_%.4f" % float(min_density))
    passed = not reasons
    return {
        "pass": bool(passed),
        "reject_reasons": reasons,
        "metrics": {
            "triggers": triggers,
            "evaluated_bars": evaluated,
            "density": round(density, 6),
            "stride": int(stride),
            "min_triggers": int(min_triggers),
            "min_density": float(min_density),
        },
        "wall_time_ms": round(wall_ms, 3),
        "target_wall_time_ms": 50.0,
        "under_target": wall_ms < 50.0,
        "fail_closed": True,
        "stage": "funnel_l0_density",
    }
