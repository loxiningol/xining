# -*- coding: utf-8 -*-
"""Alphalens / CausalImpact style adapters (no heavy deps).

Provides IC / IR / turnover screening and a light causal check so stages
② and ④ of the creation blueprint run on the VPS without installing
alphalens / causalimpact (memory-hostile on 764MB hosts).

When real packages are present, probe() reports them; math stays local
and reproducible so QuantOracle remains the risk authority.
"""
from __future__ import print_function

import math
from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def probe():
    out = {
        "ok": True,
        "provider": "alphalens_causal_lite_v1",
        "backends": {"alphalens": False, "causalimpact": False},
        "notes": [
            "Using local IC/IR/turnover + pre/post causal adapter.",
            "Install alphalens/causalimpact later for richer plots; not required.",
        ],
        "probed_at": _now(),
    }
    for name, key in (("alphalens", "alphalens"), ("causalimpact", "causalimpact")):
        try:
            __import__(name)
            out["backends"][key] = True
        except Exception:
            pass
    return out


def _rank(xs):
    """Average ranks for ties; None skipped via mask outside."""
    indexed = sorted(enumerate(xs), key=lambda t: t[1])
    ranks = [0.0] * len(xs)
    i = 0
    n = len(xs)
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg
        i = j + 1
    return ranks


def spearman(xs, ys):
    pairs = [(float(a), float(b)) for a, b in zip(xs, ys)
             if a is not None and b is not None
             and not (isinstance(a, float) and math.isnan(a))
             and not (isinstance(b, float) and math.isnan(b))]
    if len(pairs) < 20:
        return None, len(pairs)
    ax = [p[0] for p in pairs]
    ay = [p[1] for p in pairs]
    rx = _rank(ax)
    ry = _rank(ay)
    n = float(len(pairs))
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(len(pairs)))
    denx = math.sqrt(sum((x - mx) ** 2 for x in rx))
    deny = math.sqrt(sum((y - my) ** 2 for y in ry))
    if denx < 1e-12 or deny < 1e-12:
        return 0.0, len(pairs)
    return num / (denx * deny), len(pairs)


def rolling_ic(factor_values, fwd_returns, window=60, step=20):
    """Rolling Spearman IC series → mean IC, IR, decay proxy."""
    n = min(len(factor_values), len(fwd_returns))
    ics = []
    for start in range(0, max(0, n - window + 1), max(1, int(step))):
        end = start + window
        ic, _ = spearman(factor_values[start:end], fwd_returns[start:end])
        if ic is not None:
            ics.append(ic)
    if not ics:
        return {
            "ok": False,
            "error": "insufficient_ic_windows",
            "ic_mean": None,
            "ir": None,
            "ic_series": [],
        }
    mean = sum(ics) / float(len(ics))
    var = sum((x - mean) ** 2 for x in ics) / float(max(len(ics) - 1, 1))
    std = var ** 0.5
    ir = (mean / std) if std > 1e-12 else 0.0
    # decay: last third vs first third
    k = max(1, len(ics) // 3)
    early = sum(ics[:k]) / float(k)
    late = sum(ics[-k:]) / float(k)
    decay = late - early
    return {
        "ok": True,
        "ic_mean": mean,
        "ir": ir,
        "n_windows": len(ics),
        "ic_std": std,
        "ic_decay": decay,
        "ic_series_tail": ics[-8:],
        "source": "alphalens_lite",
    }


def quantile_turnover(factor_values, q=0.8, lookback=1):
    """Membership churn of top/bottom quantile — Alphalens-style turnover proxy."""
    vals = [(i, float(v)) for i, v in enumerate(factor_values)
            if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(vals) < 40:
        return {"ok": False, "error": "insufficient", "turnover": None}
    # rolling membership of top quantile
    win = 60
    memberships = []
    for end in range(win, len(vals)):
        window = vals[end - win:end]
        ranked = sorted(window, key=lambda t: t[1])
        k = max(2, int(len(ranked) * (1.0 - float(q))))
        top = set(i for i, _ in ranked[-k:])
        memberships.append(top)
    if len(memberships) < 2:
        return {"ok": False, "error": "short_membership", "turnover": None}
    churns = []
    for a, b in zip(memberships[:-1], memberships[1:]):
        if not a and not b:
            continue
        union = a | b
        inter = a & b
        churns.append(1.0 - (len(inter) / float(len(union) or 1)))
    turn = sum(churns) / float(len(churns)) if churns else None
    return {
        "ok": turn is not None,
        "turnover": turn,
        "n": len(churns),
        "source": "alphalens_lite",
        "high_turnover": bool(turn is not None and turn > 0.65),
    }


def screen_factor(factor_values, fwd_returns, name="factor",
                 min_abs_ic=0.02, min_ir=0.15, max_turnover=0.65):
    """Stage ②/④ gate on one factor series."""
    ic_pack = rolling_ic(factor_values, fwd_returns)
    turn_pack = quantile_turnover(factor_values)
    ic_mean = ic_pack.get("ic_mean")
    ir = ic_pack.get("ir")
    turn = turn_pack.get("turnover")
    reasons = []
    passed = True
    if not ic_pack.get("ok"):
        passed = False
        reasons.append("ic_compute_fail")
    else:
        if abs(float(ic_mean or 0.0)) < float(min_abs_ic):
            passed = False
            reasons.append("ic_not_significant")
        if abs(float(ir or 0.0)) < float(min_ir):
            passed = False
            reasons.append("ir_too_low")
        if ic_pack.get("ic_decay") is not None and float(ic_pack["ic_decay"]) < -0.03:
            # strong decay → overfitting fuse candidate
            reasons.append("ic_decay_fast")
            if float(ic_pack["ic_decay"]) < -0.06:
                passed = False
    if turn_pack.get("high_turnover") or (turn is not None and turn > max_turnover):
        passed = False
        reasons.append("turnover_too_high")
    return {
        "ok": True,
        "name": name,
        "passed": passed,
        "reasons": reasons,
        "ic": ic_pack,
        "turnover": turn_pack,
        "thresholds": {
            "min_abs_ic": min_abs_ic,
            "min_ir": min_ir,
            "max_turnover": max_turnover,
        },
        "at": _now(),
    }


def causal_pre_post(factor_values, fwd_returns, q=0.8):
    """CausalImpact-lite: compare fwd returns in treated (extreme quantile) vs control."""
    pairs = [(float(v), float(r)) for v, r in zip(factor_values, fwd_returns)
             if v is not None and r is not None]
    if len(pairs) < 60:
        return {"ok": False, "error": "insufficient", "significant": False}
    pairs.sort(key=lambda x: x[0])
    k = max(5, int(len(pairs) * (1.0 - float(q))))
    treated = [r for _, r in pairs[-k:]]
    control = [r for _, r in pairs[:-k]]
    if not treated or not control:
        return {"ok": False, "error": "split_fail", "significant": False}
    mt = sum(treated) / float(len(treated))
    mc = sum(control) / float(len(control))
    effect = mt - mc
    # pooled std for crude t
    allr = treated + control
    m = sum(allr) / float(len(allr))
    var = sum((x - m) ** 2 for x in allr) / float(max(len(allr) - 1, 1))
    se = (var ** 0.5) * math.sqrt(1.0 / len(treated) + 1.0 / len(control))
    t = effect / se if se > 1e-12 else 0.0
    # |t| > 1.96 ≈ p<0.05 rough
    significant = abs(t) >= 1.96 and effect > 0
    return {
        "ok": True,
        "source": "causalimpact_lite",
        "treated_mean": mt,
        "control_mean": mc,
        "effect": effect,
        "t_stat": t,
        "significant": significant,
        "n_treated": len(treated),
        "n_control": len(control),
        "at": _now(),
    }


def validate_hypothesis(factor_matrix, fwd_returns, core_factors=None):
    """Stage ②: screen core hypothesis factors + causal check."""
    core_factors = list(core_factors or []) or list((factor_matrix or {}).keys())[:4]
    rows = []
    any_pass = False
    for name in core_factors:
        series = (factor_matrix or {}).get(name) or []
        screen = screen_factor(series, fwd_returns, name=name)
        causal = causal_pre_post(series, fwd_returns)
        row = {
            "factor": name,
            "screen": screen,
            "causal": causal,
            "accepted": bool(screen.get("passed")) and bool(causal.get("significant") or screen.get("passed")),
        }
        # Prefer both; allow screen-pass alone if causal weak but IC strong
        if screen.get("passed") and abs(float((screen.get("ic") or {}).get("ic_mean") or 0)) >= 0.03:
            row["accepted"] = True
        if row["accepted"]:
            any_pass = True
        rows.append(row)
    return {
        "ok": True,
        "schema": "qiyu_hypothesis_validate_v1",
        "passed": any_pass,
        "factors": rows,
        "probe": probe(),
        "at": _now(),
        "note_zh": (
            "假设验证（Alphalens/CausalImpact 风格）："
            "IC/IR/换手 + 分位因果效应；未通过则不得进入因子海量挖掘。"
        ),
    }


def rescreen_candidates(candidates, factor_matrix, fwd_returns):
    """Stage ④: re-screen mined candidates with IC/IR/turnover."""
    kept = []
    dropped = []
    for cand in candidates or []:
        name = cand.get("factor")
        series = (factor_matrix or {}).get(name) or []
        screen = screen_factor(series, fwd_returns, name=name or "unknown")
        # overfitting fuse: high turnover or fast IC decay
        fuse_overfit = (
            "turnover_too_high" in (screen.get("reasons") or [])
            or "ic_decay_fast" in (screen.get("reasons") or [])
        )
        packed = dict(cand)
        packed["alphalens_rescreen"] = screen
        packed["overfit_fuse"] = fuse_overfit
        if screen.get("passed") and not fuse_overfit:
            kept.append(packed)
        else:
            packed["drop_reasons"] = screen.get("reasons") or ["failed_rescreen"]
            dropped.append(packed)
    return {
        "ok": True,
        "schema": "qiyu_factor_rescreen_v1",
        "kept": kept,
        "dropped": dropped,
        "n_kept": len(kept),
        "n_dropped": len(dropped),
        "at": _now(),
    }
