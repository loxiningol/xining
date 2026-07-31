# -*- coding: utf-8 -*-
"""Lite parameter search platform — grid / scrambled-Sobol / local refine.

VPS-safe: no vectorbt / Optuna / Ray. Caps evaluations for ~0.7GB RAM hosts.
Records every trial into the research ledger (feeds DSR/PBO effective N).
"""
from __future__ import print_function

import math
import os
import random
from datetime import datetime

from . import research_ledger as ledger


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


DEFAULT_SPACE = {
    "entry_q": [0.70, 0.75, 0.80, 0.85, 0.90],
    "hold_bars": [1, 3, 6, 12],
    "side": ["high", "low"],
    "round_trip_cost": [0.0008, 0.0010, 0.0015],
}


def probe():
    return {
        "ok": True,
        "provider": "parameter_platform_lite_v1",
        "backends": ["grid", "scrambled_sobol_lite", "local_refine"],
        "not_installed": ["vectorbt", "optuna", "ray"],
        "note_zh": "轻量网格/类Sobol；未装 vectorbt Pro（内存/许可不足）。",
        "at": _now(),
    }


def _product(keys, lists, cap):
    """Cartesian product with hard cap (no itertools dependency issues)."""
    rows = [{}]
    for k, vals in zip(keys, lists):
        nxt = []
        for base in rows:
            for v in vals:
                row = dict(base)
                row[k] = v
                nxt.append(row)
                if len(nxt) >= int(cap):
                    return nxt
        rows = nxt
        if len(rows) >= int(cap):
            return rows[: int(cap)]
    return rows[: int(cap)]


def grid_candidates(space=None, max_evals=48):
    space = dict(space or DEFAULT_SPACE)
    keys = list(space.keys())
    lists = [list(space[k]) for k in keys]
    return _product(keys, lists, max_evals)


def _sobol_scramble(n, dim, seed=19):
    """Deterministic low-discrepancy-ish points in [0,1]^dim (lite, not true Sobol)."""
    rng = random.Random(int(seed))
    # Van der Corput-ish + scramble
    pts = []
    primes = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
    for i in range(1, int(n) + 1):
        row = []
        for d in range(int(dim)):
            base = primes[d % len(primes)]
            x = 0.0
            f = 1.0 / base
            k = i
            while k > 0:
                x += (k % base) * f
                k //= base
                f /= base
            x = (x + rng.random() * 0.07) % 1.0
            row.append(x)
        pts.append(row)
    return pts


def sobol_candidates(space=None, max_evals=32, seed=19):
    space = dict(space or DEFAULT_SPACE)
    keys = list(space.keys())
    lists = [list(space[k]) for k in keys]
    pts = _sobol_scramble(max_evals, len(keys), seed=seed)
    out = []
    for pt in pts:
        row = {}
        for j, k in enumerate(keys):
            vals = lists[j]
            idx = int(pt[j] * len(vals)) % len(vals)
            row[k] = vals[idx]
        out.append(row)
    # de-dupe
    seen = set()
    uniq = []
    for r in out:
        key = tuple(sorted((k, str(v)) for k, v in r.items()))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def local_refine(best, space=None, n_neighbors=8, seed=23):
    """Perturb categorical neighbors around a best point."""
    space = dict(space or DEFAULT_SPACE)
    rng = random.Random(int(seed))
    best = dict(best or {})
    out = [dict(best)]
    keys = list(space.keys())
    for _ in range(int(n_neighbors)):
        row = dict(best)
        k = rng.choice(keys)
        vals = list(space[k])
        if not vals:
            continue
        cur = row.get(k)
        alt = [v for v in vals if v != cur]
        if alt:
            row[k] = rng.choice(alt)
        out.append(row)
    return out


def evaluate_params_on_probe(factor_values, fwd_returns, params, probe_fn=None):
    """Evaluate one param set via naked probe protocol."""
    from . import probe_protocol as pp
    probe_fn = probe_fn or pp.evaluate_naked_probe
    side = str(params.get("side") or "high")
    q = float(params.get("entry_q") or 0.8)
    cost = float(params.get("round_trip_cost") or 0.001)
    hold = params.get("hold_bars")
    res = probe_fn(
        factor_values, fwd_returns, side=side, q=q, round_trip_cost=cost,
    )
    out = {
        "params": dict(params),
        "passed": bool((res or {}).get("passed")),
        "mean_net": (res or {}).get("mean_net"),
        "t_stat": (res or {}).get("t_stat"),
        "n_hits": (res or {}).get("n_hits"),
        "hold_bars": hold,
    }
    return out


def search(
    factor_values,
    fwd_returns,
    space=None,
    method="grid",
    max_evals=None,
    run_id=None,
    seed=19,
):
    """Run lite parameter search; every eval logged as param_eval."""
    max_default = int(os.environ.get("QIYU_PARAM_MAX_EVALS") or 36)
    max_evals = int(max_evals or max_default)
    # Hard RAM-safe cap
    max_evals = max(8, min(max_evals, 80))
    space = dict(space or DEFAULT_SPACE)
    if method == "sobol":
        cands = sobol_candidates(space, max_evals=max_evals, seed=seed)
    elif method == "refine" and space.get("_seed_best"):
        cands = local_refine(space.get("_seed_best"), space=space, seed=seed)
    else:
        cands = grid_candidates(space, max_evals=max_evals)

    rows = []
    for i, params in enumerate(cands[:max_evals]):
        ev = evaluate_params_on_probe(factor_values, fwd_returns, params)
        rows.append(ev)
        if run_id:
            ledger.append_event({
                "event_type": "param_eval",
                "method": method,
                "i": i,
                "params": params,
                "passed": ev.get("passed"),
                "mean_net": ev.get("mean_net"),
            }, run_id=run_id)

    rows.sort(
        key=lambda r: (
            1 if r.get("passed") else 0,
            float(r.get("mean_net") or -1e9),
            abs(float(r.get("t_stat") or 0)),
        ),
        reverse=True,
    )
    best = rows[0] if rows else None
    n_pass = sum(1 for r in rows if r.get("passed"))
    return {
        "ok": True,
        "schema": "qiyu_parameter_platform_lite_v1",
        "method": method,
        "n_evaluated": len(rows),
        "n_passed": n_pass,
        "best": best,
        "top": rows[:8],
        "note_zh": (
            "参数平台轻量搜索完成；有效试验已入账。"
            if rows else
            "无参数候选"
        ),
        "at": _now(),
    }
