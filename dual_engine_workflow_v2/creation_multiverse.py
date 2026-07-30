# -*- coding: utf-8 -*-
"""Multi-universe survival test — Monte Carlo synthetic markets.

After a hypothesis has a trade-return series (or bar returns), generate many
price-path-like return universes from the empirical distribution and ask:
does the edge stay positive across most universes?

Default n_paths=200 (VPS-safe); set QIYU_MULTIVERSE_PATHS=1000 when RAM allows.
"""
from __future__ import print_function

import math
import os
import random
from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def probe():
    return {
        "ok": True,
        "provider": "multiverse_montecarlo_lite_v1",
        "default_paths": int(os.environ.get("QIYU_MULTIVERSE_PATHS") or 200),
        "note_zh": "基于实证收益分布的合成路径；逼假设加环境过滤而非极致特化。",
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


def _bootstrap_path(sample, length, rng):
    return [sample[rng.randrange(0, len(sample))] for _ in range(length)]


def _gaussian_path(mean, std, length, rng):
    # Box-Muller
    out = []
    for _ in range(length):
        u1 = max(rng.random(), 1e-12)
        u2 = rng.random()
        z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        out.append(mean + std * z)
    return out


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
                  min_profit_frac=0.55, min_median_return=0.0):
    """Return multiverse survival verdict for a hypothesis/strategy edge."""
    n_paths = int(n_paths or os.environ.get("QIYU_MULTIVERSE_PATHS") or 200)
    # Cap for tiny VPS
    n_paths = max(20, min(int(n_paths), 1000))
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
    for i in range(n_paths):
        if i % 2 == 0:
            path = _bootstrap_path(mom["sample"], length, rng)
        else:
            path = _gaussian_path(mom["mean"], mom["std"], length, rng)
        totals.append(_path_total(path))
        mdds.append(_path_mdd(path))

    ordered = sorted(totals)
    profit_frac = sum(1 for x in totals if x > 0) / float(len(totals))
    median = ordered[len(ordered) // 2]
    p10 = ordered[max(0, int(0.10 * (len(ordered) - 1)))]
    p90 = ordered[min(len(ordered) - 1, int(0.90 * (len(ordered) - 1)))]
    mean_tot = sum(totals) / float(len(totals))

    passed = (
        profit_frac >= float(min_profit_frac)
        and median >= float(min_median_return)
        and mean_tot > 0
    )
    # Soft hint: if only minority profitable → need regime filter
    need_regime_filter = profit_frac < 0.70

    return {
        "ok": True,
        "schema": "qiyu_multiverse_survival_v1",
        "passed": passed,
        "n_paths": n_paths,
        "path_len": length,
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
