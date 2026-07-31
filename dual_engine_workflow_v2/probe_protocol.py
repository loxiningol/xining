# -*- coding: utf-8 -*-
"""Naked probe protocol — fixed hold, fixed size, NO stops/exits/filters.

Hard rule: if bare mechanism has no economic edge, complex exits must NOT
be used to sculpt it into a backtest winner.
"""
from __future__ import print_function

import math
from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _align(factor_values, fwd_returns):
    n = min(len(factor_values or []), len(fwd_returns or []))
    f = list(factor_values or [])[-n:]
    r = list(fwd_returns or [])[-n:]
    return f, r


def _quantile(xs, q):
    vals = sorted([float(x) for x in xs if x is not None])
    if len(vals) < 10:
        return None
    i = max(0, min(len(vals) - 1, int(q * (len(vals) - 1))))
    return vals[i]


def signal_from_factor(factor_values, side="high", q=0.8):
    """Binary entry mask only — no exits, no stops, no filters."""
    f, _ = _align(factor_values, factor_values)
    if side == "high":
        thr = _quantile(f, q)
        if thr is None:
            return [0] * len(f)
        return [1 if (v is not None and float(v) >= thr) else 0 for v in f]
    thr = _quantile(f, 1.0 - q)
    if thr is None:
        return [0] * len(f)
    return [1 if (v is not None and float(v) <= thr) else 0 for v in f]


def evaluate_naked_probe(factor_values, fwd_returns, side="high", q=0.8,
                         hold_bars=None, horizons=(1, 3, 6, 12),
                         round_trip_cost=0.001):
    """Probe: enter on signal, hold fixed label horizon, flat size=1, no SL.

    fwd_returns is already a single-horizon series from miner; for multi-horizon
    we approximate decay by scaling mean effect (true multi-h needs multi fwd).
    """
    f, r = _align(factor_values, fwd_returns)
    sig = signal_from_factor(f, side=side, q=q)
    hits = [r[i] for i, s in enumerate(sig) if s and r[i] is not None]
    miss = [r[i] for i, s in enumerate(sig) if (not s) and r[i] is not None]
    if len(hits) < 12:
        return {
            "ok": False,
            "passed": False,
            "error": "insufficient_hits",
            "n_hits": len(hits),
            "at": _now(),
        }
    mean_hit = sum(hits) / float(len(hits))
    mean_miss = (sum(miss) / float(len(miss))) if miss else 0.0
    # net after one round-trip cost assumption on each hit
    mean_net = mean_hit - float(round_trip_cost)
    # crude t vs 0
    var = sum((x - mean_hit) ** 2 for x in hits) / float(max(len(hits) - 1, 1))
    se = math.sqrt(var / len(hits)) if var > 0 else 1e-12
    t = mean_hit / se if se > 0 else 0.0
    # horizon decay proxy: later horizons shrink effect toward 0
    decay = []
    for h in horizons:
        scale = 1.0 / math.sqrt(float(max(h, 1)))
        decay.append({
            "horizon_bars": int(h),
            "mean_proxy": mean_hit * scale,
            "mean_net_proxy": mean_net * scale,
        })
    # monotonicity: |effect| should not explode with noise — require mean_net>0
    # OR significant directional shift vs miss
    edge_vs_miss = mean_hit - mean_miss
    passed = bool(mean_net > 0 and abs(t) >= 1.64)
    return {
        "ok": True,
        "schema": "qiyu_naked_probe_v1",
        "passed": passed,
        "side": side,
        "q": q,
        "n_hits": len(hits),
        "n_miss": len(miss),
        "mean_hit": mean_hit,
        "mean_miss": mean_miss,
        "mean_net": mean_net,
        "edge_vs_miss": edge_vs_miss,
        "t_stat": t,
        "round_trip_cost": round_trip_cost,
        "horizon_decay": decay,
        "trade_returns": hits[:200],
        "hard_rule_zh": "裸机制无效，禁止用退出规则把它优化有效。",
        "reject_if_fail_zh": (
            None if passed else "裸探针无净优势或显著性不足，禁止组装完整策略。"
        ),
        "at": _now(),
    }


def probe_hypothesis(hypothesis, factor_matrix, fwd_returns, round_trip_cost=0.001):
    """Run naked probes on hypothesis factor hints; keep best side."""
    hints = list(hypothesis.get("factor_hints") or hypothesis.get("observable_proxy") or [])
    matrix = factor_matrix or {}
    rows = []
    best = None
    for name in hints:
        series = matrix.get(name)
        if not series:
            continue
        for side in ("high", "low"):
            pack = evaluate_naked_probe(
                series, fwd_returns, side=side, round_trip_cost=round_trip_cost,
            )
            pack["factor"] = name
            pack["hypothesis_id"] = hypothesis.get("hypothesis_id")
            rows.append(pack)
            if pack.get("ok") and pack.get("passed"):
                if best is None or float(pack.get("mean_net") or -1e9) > float(best.get("mean_net") or -1e9):
                    best = pack
    any_pass = best is not None
    return {
        "ok": True,
        "passed": any_pass,
        "hypothesis_id": hypothesis.get("hypothesis_id"),
        "n_probes": len(rows),
        "best": best,
        "probes": [
            {k: v for k, v in r.items() if k != "trade_returns"} for r in rows
        ],
        "hard_rule_zh": "裸机制无效，禁止用退出规则把它优化有效。",
        "at": _now(),
    }


def probe():
    return {
        "ok": True,
        "provider": "naked_probe_protocol_v1",
        "forbids": ["stop_loss", "complex_exit", "param_tuning", "multi_filter"],
        "at": _now(),
    }
