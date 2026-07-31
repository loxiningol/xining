# -*- coding: utf-8 -*-
"""Multiple-testing corrections: Deflated Sharpe (DSR) + PBO-lite + CPCV-lite.

These are statistical judges — not LLM opinions.
"""
from __future__ import print_function

import math
import random
from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _finite(xs):
    out = []
    for x in xs or []:
        if x is None:
            continue
        try:
            v = float(x)
        except Exception:
            continue
        if math.isnan(v) or math.isinf(v):
            continue
        out.append(v)
    return out


def sharpe_ratio(returns):
    rets = _finite(returns)
    n = len(rets)
    if n < 5:
        return None
    mean = sum(rets) / float(n)
    var = sum((x - mean) ** 2 for x in rets) / float(max(n - 1, 1))
    vol = math.sqrt(var) if var > 0 else 0.0
    if vol <= 1e-12:
        return 0.0
    return (mean / vol) * math.sqrt(float(n))


def _norm_cdf(x):
    # Abramowitz-Stegun approximation
    return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))


def deflated_sharpe_ratio(returns, n_trials, skew=None, kurt=None):
    """Bailey & López de Prado DSR (simplified, non-annualized SR input).

    Returns probability that observed SR is due to non-skill given trials.
    """
    rets = _finite(returns)
    sr = sharpe_ratio(rets)
    if sr is None:
        return {"ok": False, "error": "insufficient", "dsr": None, "psr": None}
    n = len(rets)
    n_trials = max(1.0, float(n_trials or 1.0))
    # Expected max SR under nullity ~ (1-γ)*Z^{-1}(1-1/N) + γ*Z^{-1}(1-1/(N*e))
    # Use simple approximation: SR0 ≈ sqrt(2*log(N_trials))
    sr0 = math.sqrt(2.0 * math.log(n_trials)) if n_trials > 1 else 0.0
    # moments
    mean = sum(rets) / float(n)
    centered = [x - mean for x in rets]
    m2 = sum(x * x for x in centered) / float(n)
    m3 = sum(x ** 3 for x in centered) / float(n)
    m4 = sum(x ** 4 for x in centered) / float(n)
    if skew is None:
        skew = 0.0 if m2 <= 1e-18 else m3 / (m2 ** 1.5)
    if kurt is None:
        kurt = 3.0 if m2 <= 1e-18 else m4 / (m2 ** 2)
    # PSR / DSR variance factor
    denom = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * (sr ** 2)
    denom = max(denom, 1e-8)
    se = math.sqrt(denom / max(n - 1, 1))
    z = (sr - sr0) / se if se > 0 else 0.0
    dsr = _norm_cdf(z)
    return {
        "ok": True,
        "sharpe": sr,
        "sr0_null_max": sr0,
        "n_trials_effective": n_trials,
        "skew": skew,
        "kurtosis": kurt,
        "dsr": dsr,
        "passed": bool(dsr >= 0.95),
        "note_zh": "DSR=在给定试验次数下观察到该夏普仍属真实技能的概率近似；非因果证明。",
        "at": _now(),
    }


def pbo_cscv(returns_matrix, n_partitions=8, seed=11):
    """Probability of Backtest Overfitting via Combinatorial Symmetric CV (lite).

    returns_matrix: list of candidate return series (equal length preferred).
    """
    rng = random.Random(int(seed))
    cands = [ _finite(r) for r in (returns_matrix or []) ]
    cands = [r for r in cands if len(r) >= 16]
    if len(cands) < 2:
        return {
            "ok": False,
            "error": "need_>=2_candidates",
            "pbo": None,
            "passed": False,
            "at": _now(),
        }
    n = min(len(r) for r in cands)
    cands = [r[:n] for r in cands]
    n_partitions = max(4, min(int(n_partitions), 12))
    # ensure even
    if n_partitions % 2 == 1:
        n_partitions += 1
    part = max(1, n // n_partitions)
    # build partitions indices
    idxs = list(range(n_partitions))
    # sample combinations of half partitions as train
    from itertools import combinations
    half = n_partitions // 2
    combos = list(combinations(idxs, half))
    if len(combos) > 40:
        rng.shuffle(combos)
        combos = combos[:40]
    overfit = 0
    total = 0
    for train_parts in combos:
        train_set = set(train_parts)
        test_set = [i for i in idxs if i not in train_set]
        # IS performance per candidate
        is_scores = []
        oos_scores = []
        for series in cands:
            is_rets = []
            oos_rets = []
            for p in range(n_partitions):
                a = p * part
                b = n if p == n_partitions - 1 else min(n, (p + 1) * part)
                chunk = series[a:b]
                if p in train_set:
                    is_rets.extend(chunk)
                else:
                    oos_rets.extend(chunk)
            is_scores.append(sharpe_ratio(is_rets) or -999.0)
            oos_scores.append(sharpe_ratio(oos_rets) or -999.0)
        # rank IS best
        best_i = max(range(len(is_scores)), key=lambda i: is_scores[i])
        # PBO: OOS rank of IS-best is below median
        oos_rank = sorted(oos_scores).index(oos_scores[best_i])
        # higher sharpe better — if best IS has OOS below median → overfit token
        if oos_rank < len(oos_scores) / 2.0:
            overfit += 1
        total += 1
    pbo = float(overfit) / float(max(total, 1))
    return {
        "ok": True,
        "pbo": pbo,
        "n_candidates": len(cands),
        "n_combos": total,
        "n_partitions": n_partitions,
        "passed": bool(pbo <= 0.4),
        "note_zh": "PBO≈选中样本内最优后在样本外仍差的概率；越高越像数据挖掘。",
        "at": _now(),
    }


def purged_walk_slices(n, n_folds=5, embargo=3):
    """Yield (train_idx, test_idx) with purge/embargo gaps for time series."""
    n = int(n)
    n_folds = max(2, int(n_folds))
    embargo = max(0, int(embargo))
    fold = max(1, n // n_folds)
    slices = []
    for i in range(n_folds):
        test_a = i * fold
        test_b = n if i == n_folds - 1 else min(n, (i + 1) * fold)
        test_idx = list(range(test_a, test_b))
        purge_a = max(0, test_a - embargo)
        purge_b = min(n, test_b + embargo)
        train_idx = [j for j in range(n) if j < purge_a or j >= purge_b]
        if len(train_idx) >= 10 and len(test_idx) >= 5:
            slices.append({"train": train_idx, "test": test_idx, "fold": i})
    return slices


def cpcv_oos_sharpes(returns, n_folds=5, embargo=3):
    rets = _finite(returns)
    slices = purged_walk_slices(len(rets), n_folds=n_folds, embargo=embargo)
    oos = []
    for sl in slices:
        test = [rets[i] for i in sl["test"]]
        sr = sharpe_ratio(test)
        if sr is not None:
            oos.append(sr)
    if not oos:
        return {"ok": False, "error": "no_folds", "oos_sharpes": []}
    return {
        "ok": True,
        "oos_sharpes": oos,
        "median_oos_sharpe": sorted(oos)[len(oos) // 2],
        "n_folds": len(oos),
        "embargo": embargo,
        "at": _now(),
    }


def evaluate_multiple_testing(best_returns, candidate_returns_list, n_trials_effective):
    dsr = deflated_sharpe_ratio(best_returns, n_trials=n_trials_effective)
    matrix = list(candidate_returns_list or [])
    if best_returns and best_returns not in matrix:
        matrix = [best_returns] + matrix
    pbo = pbo_cscv(matrix)
    cpcv = cpcv_oos_sharpes(best_returns)
    passed = bool(dsr.get("ok") and dsr.get("passed") and (not pbo.get("ok") or pbo.get("passed")))
    # if pbo unavailable (single cand), rely on DSR + CPCV median>0
    if not pbo.get("ok"):
        passed = bool(dsr.get("passed") and (cpcv.get("median_oos_sharpe") or 0) > 0)
    return {
        "ok": True,
        "passed": passed,
        "dsr": dsr,
        "pbo": pbo,
        "cpcv": cpcv,
        "n_trials_effective": n_trials_effective,
        "note_zh": "统计裁判：DSR/PBO/CPCV-lite；不接受漂亮故事替代。",
        "at": _now(),
    }


def probe():
    return {
        "ok": True,
        "provider": "multiple_testing_lite_v1",
        "methods": ["DSR", "PBO_CSCV", "purged_walk_CPCV_lite"],
        "at": _now(),
    }
