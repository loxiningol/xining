# -*- coding: utf-8 -*-
"""Multi-universe survival test — Monte Carlo synthetic markets.

Generators (lite, numpy-free):
  bootstrap, gaussian, gbm, ou, jump, block_bootstrap, regime, extreme

After a hypothesis has a trade-return series, generate many return universes
and ask: does the edge stay positive across most universes?

Default n_paths=200 (VPS-safe); set QIYU_MULTIVERSE_PATHS=1000 when RAM allows.
Not installed on this host: GAN / HMM / heavy SDEs (RAM / deps).
"""
from __future__ import print_function

import math
import os
import random
from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


GENERATORS = (
    "bootstrap", "gaussian", "gbm", "ou", "jump",
    "block_bootstrap", "regime", "extreme",
)


def probe():
    return {
        "ok": True,
        "provider": "multiverse_montecarlo_lite_v2",
        "generators": list(GENERATORS),
        "default_paths": int(os.environ.get("QIYU_MULTIVERSE_PATHS") or 200),
        "not_installed": ["gan", "hmmlearn", "torch"],
        "note_zh": "多生成器合成路径；GAN/HMM 因内存与依赖未装。",
        "at": _now(),
    }


def _moments(returns):
    rets = [float(x) for x in (returns or []) if x is not None]
    if len(rets) < 8:
        return None
    mean = sum(rets) / float(len(rets))
    var = sum((x - mean) ** 2 for x in rets) / float(max(len(rets) - 1, 1))
    return {
        "n": len(rets),
        "mean": mean,
        "std": math.sqrt(var) if var > 0 else 0.0,
        "sample": rets,
    }


def _box_muller(rng):
    u1 = max(rng.random(), 1e-12)
    u2 = rng.random()
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _bootstrap_path(sample, length, rng):
    return [sample[rng.randrange(0, len(sample))] for _ in range(length)]


def _gaussian_path(mean, std, length, rng):
    return [mean + std * _box_muller(rng) for _ in range(length)]


def _gbm_path(mean, std, length, rng):
    """GBM-like log-return increments around empirical drift/vol."""
    out = []
    # map mean/std of simple returns to approx log increments
    mu = mean - 0.5 * (std ** 2)
    for _ in range(length):
        z = _box_muller(rng)
        out.append(math.exp(mu + std * z) - 1.0)
    return out


def _ou_path(mean, std, length, rng, kappa=0.35):
    """Ornstein-Uhlenbeck on returns (mean-reverting shocks)."""
    x = mean
    out = []
    for _ in range(length):
        x = x + kappa * (mean - x) + std * _box_muller(rng) * math.sqrt(max(1.0 - kappa, 0.05))
        out.append(x)
    return out


def _jump_path(mean, std, length, rng, jump_prob=0.04, jump_scale=3.0):
    out = []
    for _ in range(length):
        r = mean + std * _box_muller(rng)
        if rng.random() < jump_prob:
            r += jump_scale * std * (1.0 if rng.random() < 0.5 else -1.0)
        out.append(r)
    return out


def _block_bootstrap_path(sample, length, rng, block=8):
    xs = list(sample)
    if len(xs) < block * 2:
        return _bootstrap_path(xs, length, rng)
    out = []
    while len(out) < length:
        start = rng.randrange(0, max(1, len(xs) - block))
        out.extend(xs[start:start + block])
    return out[:length]


def _regime_path(mean, std, length, rng):
    """Two-regime: calm vs stress vol."""
    out = []
    stress = False
    for _ in range(length):
        if rng.random() < 0.08:
            stress = not stress
        s = std * (2.2 if stress else 0.7)
        m = mean * (0.3 if stress else 1.0)
        out.append(m + s * _box_muller(rng))
    return out


def _extreme_path(mean, std, length, rng):
    """Fat-tail mix: mostly gaussian, occasional 5–8σ shocks."""
    out = []
    for _ in range(length):
        if rng.random() < 0.03:
            out.append(mean + std * (5.0 + 3.0 * rng.random()) * (1 if rng.random() < 0.5 else -1))
        else:
            out.append(mean + std * _box_muller(rng))
    return out


def _make_path(kind, mom, length, rng):
    sample, mean, std = mom["sample"], mom["mean"], mom["std"]
    if kind == "bootstrap":
        return _bootstrap_path(sample, length, rng)
    if kind == "gaussian":
        return _gaussian_path(mean, std, length, rng)
    if kind == "gbm":
        return _gbm_path(mean, std, length, rng)
    if kind == "ou":
        return _ou_path(mean, std, length, rng)
    if kind == "jump":
        return _jump_path(mean, std, length, rng)
    if kind == "block_bootstrap":
        return _block_bootstrap_path(sample, length, rng)
    if kind == "regime":
        return _regime_path(mean, std, length, rng)
    if kind == "extreme":
        return _extreme_path(mean, std, length, rng)
    return _bootstrap_path(sample, length, rng)


def _path_total(rets):
    eq = 1.0
    for r in rets:
        eq *= (1.0 + float(r))
    return eq - 1.0


def _path_mdd(rets):
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in rets:
        eq *= (1.0 + float(r))
        if eq > peak:
            peak = eq
        dd = eq / peak - 1.0
        if dd < mdd:
            mdd = dd
    return mdd


def survival_test(trade_returns, n_paths=None, path_len=None, seed=42,
                  min_profit_frac=0.55, min_median_return=0.0,
                  generators=None):
    """Return multiverse survival verdict for a hypothesis/strategy edge."""
    n_paths = int(n_paths or os.environ.get("QIYU_MULTIVERSE_PATHS") or 200)
    n_paths = max(20, min(int(n_paths), 1000))
    gens = list(generators or GENERATORS)
    mom = _moments(trade_returns)
    if not mom:
        return {
            "ok": False,
            "passed": False,
            "error": "insufficient_returns",
            "at": _now(),
        }
    length = int(path_len or max(20, min(mom["n"], 80)))
    rng = random.Random(int(seed))
    totals = []
    mdds = []
    by_gen = {g: {"n": 0, "profit": 0} for g in gens}
    for i in range(n_paths):
        kind = gens[i % len(gens)]
        path = _make_path(kind, mom, length, rng)
        tot = _path_total(path)
        totals.append(tot)
        mdds.append(_path_mdd(path))
        by_gen[kind]["n"] += 1
        if tot > 0:
            by_gen[kind]["profit"] += 1

    ordered = sorted(totals)
    profit_frac = sum(1 for x in totals if x > 0) / float(len(totals))
    median = ordered[len(ordered) // 2]
    p10 = ordered[max(0, int(0.10 * (len(ordered) - 1)))]
    p90 = ordered[min(len(ordered) - 1, int(0.90 * (len(ordered) - 1)))]
    mean_tot = sum(totals) / float(len(totals))

    gen_summary = {}
    for g, st in by_gen.items():
        n = max(1, int(st["n"]))
        gen_summary[g] = {
            "n": st["n"],
            "profit_frac": float(st["profit"]) / float(n),
        }

    # Require overall pass AND at least half of generators individually ≥0.45
    gen_ok = sum(1 for g in gen_summary.values() if g["profit_frac"] >= 0.45)
    passed = (
        profit_frac >= float(min_profit_frac)
        and median >= float(min_median_return)
        and mean_tot > 0
        and gen_ok >= max(2, len(gens) // 2)
    )
    need_regime_filter = profit_frac < 0.70

    return {
        "ok": True,
        "schema": "qiyu_multiverse_survival_v2",
        "passed": passed,
        "n_paths": n_paths,
        "path_len": length,
        "generators": gens,
        "generator_summary": gen_summary,
        "profit_frac": profit_frac,
        "median_return": median,
        "mean_return": mean_tot,
        "p10_return": p10,
        "p90_return": p90,
        "median_mdd": sorted(mdds)[len(mdds) // 2],
        "thresholds": {
            "min_profit_frac": float(min_profit_frac),
            "min_median_return": float(min_median_return),
        },
        "need_regime_filter": need_regime_filter,
        "empirical_moments": {
            "n": mom["n"], "mean": mom["mean"], "std": mom["std"],
        },
        "at": _now(),
        "human_banner_zh": (
            None if passed else
            "【多宇宙生存未通过】仅在 %.0f%% 合成路径盈利（门槛≥%.0f%%），"
            "假设过特化；需增加环境过滤或换视角。"
            % (100 * profit_frac, 100 * float(min_profit_frac))
        ),
        "note_zh": (
            "若少数路径才赚钱，不要直接当成功；先进化假设加过滤。"
            if need_regime_filter else
            "多数宇宙可盈利；仍须过收益硬度与胜率门禁。"
        ),
    }
