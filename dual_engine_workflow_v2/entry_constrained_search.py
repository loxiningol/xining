# -*- coding: utf-8 -*-
"""Layer-1 entry constrained search: path-hit feasible region first.

May only search: entry quantile, hold horizon, side/direction.
Hard-rejects any change to stop (0.5%) or leverage (20x).
avg_winning_levered < 0.1111 → reject (feasible-region, not a score term).
"""
from __future__ import print_function

from datetime import datetime

from . import path_bare_screen as pbs
from . import path_outcome as po
from . import parameter_platform as pp
from . import research_ledger as ledger


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


ENTRY_SPACE = {
    "entry_q": [0.70, 0.75, 0.80, 0.85, 0.90],
    "hold_bars": list(po.HORIZONS),
    "side": ["high", "low"],
}

FIXED_STOP_PCT = po.STOP_PRICE_PCT
FIXED_LEVERAGE = po.LEVERAGE
FIXED_TARGET_PCT = po.TARGET_PRICE_PCT


def _mask_from_factor(factor_values, side, q):
    from .probe_protocol import _causal_quantile_mask
    return _causal_quantile_mask(factor_values or [], side=side, q=float(q))


def evaluate_entry_params(candles, factor_values, params, direction=1, mapping="next_bar_open"):
    """Evaluate one entry param set on bare path screen."""
    params = dict(params or {})
    # Hard identity locks.
    if abs(float(params.get("stop_pct", FIXED_STOP_PCT)) - FIXED_STOP_PCT) > 1e-12:
        return {
            "params": params,
            "feasible": False,
            "passed": False,
            "reject_reason": "stop_must_equal_0p5pct",
        }
    if abs(float(params.get("leverage", FIXED_LEVERAGE)) - FIXED_LEVERAGE) > 1e-12:
        return {
            "params": params,
            "feasible": False,
            "passed": False,
            "reject_reason": "leverage_must_equal_20x",
        }
    side = str(params.get("side") or "high")
    q = float(params.get("entry_q") or 0.8)
    hold = int(params.get("hold_bars") or 12)
    if hold not in po.HORIZONS:
        return {
            "params": params,
            "feasible": False,
            "passed": False,
            "reject_reason": "hold_bars_outside_path_horizons",
        }
    mask = _mask_from_factor(factor_values, side, q)
    signal_indices = [i for i, flag in enumerate(mask or []) if flag]
    # Independence gap ≈ hold
    gap = max(3, hold)
    indep = []
    last = -10 ** 9
    for i in signal_indices:
        if i - last >= gap:
            indep.append(i)
            last = i
    screen = pbs.screen_signals(
        candles, indep, direction, hold, mapping=mapping, allow_underpowered=True,
    )
    summary = screen.get("summary") or {}
    gate = screen.get("gate") or {}
    mean_w = summary.get("mean_winning_levered")
    if mean_w is None or float(mean_w) < po.MIN_LEVERED_WIN_RETURN - 1e-12:
        gate = dict(gate)
        reasons = list(gate.get("reasons") or [])
        if "mean_winning_levered_below_0.1111" not in reasons:
            reasons.append("mean_winning_levered_below_0.1111")
        gate["reasons"] = reasons
        gate["feasible"] = False
        gate["review_eligible"] = False
    rank = pbs.entry_score(summary, gate=gate)
    return {
        "params": {
            "entry_q": q,
            "hold_bars": hold,
            "side": side,
            "stop_pct": FIXED_STOP_PCT,
            "leverage": FIXED_LEVERAGE,
            "target_pct": FIXED_TARGET_PCT,
        },
        "feasible": bool(gate.get("feasible")),
        "passed": bool(gate.get("review_eligible")),
        "summary": summary,
        "gate": gate,
        "rank": rank,
        "profit_first_rate": summary.get("profit_first_rate"),
        "mean_winning_levered": summary.get("mean_winning_levered"),
        "median_mae_pct": summary.get("median_mae_pct"),
        "n": summary.get("n"),
        "entry_score": rank.get("score"),
    }


def search_entry(
    candles,
    factor_values,
    direction=1,
    space=None,
    method="grid",
    max_evals=None,
    run_id=None,
    seed=19,
    mapping="next_bar_open",
):
    """Constrained entry search — reject infeasible, then rank by entry_score."""
    max_evals = int(max_evals or 36)
    max_evals = max(8, min(max_evals, 64))
    space = dict(space or ENTRY_SPACE)
    # Never expose stop/leverage as searchable knobs.
    space.pop("stop_pct", None)
    space.pop("leverage", None)
    space.pop("round_trip_cost", None)
    if method == "sobol":
        cands = pp.sobol_candidates(space, max_evals=max_evals, seed=seed)
    else:
        cands = pp.grid_candidates(space, max_evals=max_evals)

    rows = []
    for i, params in enumerate(cands[:max_evals]):
        ev = evaluate_entry_params(
            candles, factor_values, params, direction=direction, mapping=mapping,
        )
        rows.append(ev)
        if run_id:
            ledger.append_event({
                "event_type": "entry_path_param_eval",
                "method": method,
                "i": i,
                "params": ev.get("params"),
                "feasible": ev.get("feasible"),
                "passed": ev.get("passed"),
                "profit_first_rate": ev.get("profit_first_rate"),
                "entry_score": ev.get("entry_score"),
            }, run_id=run_id)

    feasible = [r for r in rows if r.get("feasible")]
    feasible.sort(
        key=lambda r: (
            1 if r.get("passed") else 0,
            float(r.get("profit_first_rate") or -1.0),
            float(r.get("entry_score") or -1.0),
            float(r.get("mean_winning_levered") or -1.0),
        ),
        reverse=True,
    )
    best = feasible[0] if feasible else None
    return {
        "ok": True,
        "schema": "qiyu_entry_constrained_search_v1",
        "method": method,
        "n_evaluated": len(rows),
        "n_feasible": len(feasible),
        "n_review_eligible": sum(1 for r in feasible if r.get("passed")),
        "best": best,
        "top": feasible[:8],
        "fixed": {
            "stop_pct": FIXED_STOP_PCT,
            "leverage": FIXED_LEVERAGE,
            "target_pct": FIXED_TARGET_PCT,
        },
        "note_zh": (
            "入场层约束搜索：先硬门(profit_first/盈利单杠杆/MAE)，再按 entry_score 排序。"
            if rows else "无参数候选"
        ),
        "at": _now(),
    }


def patch_parameter_platform_search():
    """Monkey-friendly: prefer path search when candles are supplied via space."""
    return True


def probe():
    return {
        "ok": True,
        "fixed_stop": FIXED_STOP_PCT,
        "fixed_leverage": FIXED_LEVERAGE,
        "horizons": list(po.HORIZONS),
        "min_levered_win": po.MIN_LEVERED_WIN_RETURN,
    }
