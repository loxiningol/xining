# -*- coding: utf-8 -*-
"""Phase-3 Level-2 — full BT survivors + multi-objective Pareto front.

Objectives (maximize all):
  1. Calmar
  2. Payoff ratio
  3. Trade frequency (n trades / proxy)

Keep only non-dominated candidates. Fitness hard gates come from Phase-2
`evaluate_multi_objective` (Calmar≥1.5, payoff≥2.5, WR×payoff≥1.0, MAE, …).
"""
from __future__ import print_function

from .fitness_engine import evaluate_multi_objective, _safe_float


STAGE = "funnel_l2_full_bt_pareto"


def candidate_objectives(trades, base_metrics=None, fitness=None):
    """Extract (calmar, payoff, trade_frequency) + fitness block."""
    fit = fitness or evaluate_multi_objective(
        trades, base_metrics=base_metrics, enforce=True, min_trades=8,
    )
    m = fit.get("metrics") or {}
    n = int(m.get("n") or len(trades or []) or 0)
    # Prefer explicit frequency if present
    freq = _safe_float(
        (base_metrics or {}).get("trade_frequency")
        or (base_metrics or {}).get("trades_per_day"),
        None,
    )
    if freq is None:
        freq = float(n)  # proxy: absolute trade count
    return {
        "calmar": float(m.get("calmar") or 0.0),
        "payoff_ratio": float(m.get("payoff_ratio") or 0.0),
        "trade_frequency": float(freq),
        "n_trades": n,
        "fitness_pass": bool(fit.get("pass")),
        "fitness": fit,
    }


def dominates(a, b, keys=("calmar", "payoff_ratio", "trade_frequency")):
    """True if a weakly dominates b on all keys and strictly on ≥1."""
    ge_all = True
    gt_one = False
    for k in keys:
        av = float(a.get(k) or 0.0)
        bv = float(b.get(k) or 0.0)
        if av < bv - 1e-15:
            ge_all = False
            break
        if av > bv + 1e-15:
            gt_one = True
    return ge_all and gt_one


def pareto_front(candidates, keys=("calmar", "payoff_ratio", "trade_frequency"),
                 require_fitness_pass=True):
    """Return non-dominated candidates.

    Each candidate: dict with objectives already computed OR raw
    {id, trades, base_metrics, ...}. Enriches with objectives + on_front.
    """
    rows = []
    for i, c in enumerate(candidates or []):
        row = dict(c)
        if "calmar" not in row or "payoff_ratio" not in row:
            obj = candidate_objectives(
                row.get("trades"),
                base_metrics=row.get("base_metrics"),
                fitness=row.get("fitness"),
            )
            row.update(obj)
        if "id" not in row:
            row["id"] = row.get("candidate_id") or row.get("key") or ("c%s" % i)
        rows.append(row)

    eligible = [
        r for r in rows
        if (not require_fitness_pass) or r.get("fitness_pass")
    ]
    front_ids = set()
    for a in eligible:
        dominated = False
        for b in eligible:
            if a is b:
                continue
            if dominates(b, a, keys=keys):
                dominated = True
                break
        if not dominated:
            front_ids.add(a["id"])

    for r in rows:
        r["on_pareto_front"] = r["id"] in front_ids
        r["pareto_eligible"] = (not require_fitness_pass) or bool(r.get("fitness_pass"))

    return {
        "stage": STAGE,
        "n_input": len(rows),
        "n_fitness_pass": sum(1 for r in rows if r.get("fitness_pass")),
        "n_on_front": len(front_ids),
        "front_ids": sorted(front_ids),
        "objectives": list(keys),
        "candidates": rows,
        "require_fitness_pass": bool(require_fitness_pass),
    }


def evaluate_l2_survivor(trades, base_metrics=None, candidate_id=None):
    """Single-candidate L2 evaluation (fitness + trivial Pareto membership)."""
    obj = candidate_objectives(trades, base_metrics=base_metrics)
    pack = {
        "id": candidate_id or "singleton",
        "trades": trades,
        "base_metrics": base_metrics,
        "calmar": obj["calmar"],
        "payoff_ratio": obj["payoff_ratio"],
        "trade_frequency": obj["trade_frequency"],
        "n_trades": obj["n_trades"],
        "fitness_pass": obj["fitness_pass"],
        "fitness": obj["fitness"],
    }
    front = pareto_front([pack], require_fitness_pass=True)
    cand = (front.get("candidates") or [pack])[0]
    return {
        "pass": bool(obj["fitness_pass"]) and bool(cand.get("on_pareto_front")),
        "stage": STAGE,
        "reject_reasons": (
            [] if obj["fitness_pass"]
            else list((obj["fitness"] or {}).get("failed_checks") or ["fitness_fail"])
        ),
        "objectives": {
            "calmar": obj["calmar"],
            "payoff_ratio": obj["payoff_ratio"],
            "trade_frequency": obj["trade_frequency"],
        },
        "fitness": obj["fitness"],
        "pareto": {
            "on_front": cand.get("on_pareto_front"),
            "n_on_front": front.get("n_on_front"),
            "front_ids": front.get("front_ids"),
        },
        "fail_closed": True,
    }
