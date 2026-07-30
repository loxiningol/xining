# -*- coding: utf-8 -*-
"""Alphalens / CausalImpact style adapters (no heavy deps).

Provides IC / IR / turnover screening and a light causal check so stages
② and ④ of the creation blueprint run on the VPS without installing
alphalens / causalimpact (memory-hostile on 764MB hosts).

Baseline Alphalens hygiene (MUST NOT be omitted):
  - winsorize: clip extreme factor values at configurable quantiles
  - neutralize: residualize factor vs exposure columns (mkt/sector proxies)

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
        "baseline_ops": ["winsorize", "neutralize"],
        "notes": [
            "Using local IC/IR/turnover + pre/post causal adapter.",
            "winsorize + neutralize are mandatory baseline factor hygiene.",
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


def winsorize(values, limits=(0.01, 0.01)):
    """Alphalens-style winsorize — clip extremes at lower/upper quantiles.

    limits: (lower_frac, upper_frac), e.g. (0.01, 0.01) → clip to [1%, 99%].
    None entries preserved. This is a retained baseline — do not omit.
    """
    if limits is None:
        limits = (0.01, 0.01)
    lo_f, hi_f = float(limits[0]), float(limits[1])
    finite = []
    for v in values or []:
        if v is None:
            continue
        try:
            fv = float(v)
        except Exception:
            continue
        if math.isnan(fv) or math.isinf(fv):
            continue
        finite.append(fv)
    if len(finite) < 10:
        return list(values or []), {"ok": False, "reason": "insufficient", "n": len(finite)}
    ordered = sorted(finite)
    n = len(ordered)
    lo_i = max(0, int(math.floor(n * lo_f)))
    hi_i = min(n - 1, int(math.ceil(n * (1.0 - hi_f)) - 1))
    if hi_i < lo_i:
        hi_i = lo_i
    lo_v = ordered[lo_i]
    hi_v = ordered[hi_i]
    out = []
    n_clip = 0
    for v in values or []:
        if v is None:
            out.append(None)
            continue
        try:
            fv = float(v)
        except Exception:
            out.append(None)
            continue
        if math.isnan(fv) or math.isinf(fv):
            out.append(None)
            continue
        if fv < lo_v:
            out.append(lo_v)
            n_clip += 1
        elif fv > hi_v:
            out.append(hi_v)
            n_clip += 1
        else:
            out.append(fv)
    return out, {
        "ok": True,
        "op": "winsorize",
        "limits": [lo_f, hi_f],
        "lo": lo_v,
        "hi": hi_v,
        "n_clip": n_clip,
        "n": n,
    }


def _ols_residual(y, x_cols):
    """Multi-exposure OLS residual via normal equations (no numpy required).

    y: list[float], x_cols: list[list[float]] same length, each exposure series.
    Returns residuals list (aligned) or None on failure.
    """
    n = len(y)
    if n < 10:
        return None
    # Design matrix columns: intercept + exposures
    k = 1 + len(x_cols)
    # Build X'X and X'y
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for i in range(n):
        row = [1.0] + [float(col[i]) for col in x_cols]
        yi = float(y[i])
        for a in range(k):
            xty[a] += row[a] * yi
            for b in range(k):
                xtx[a][b] += row[a] * row[b]
    # Gaussian elimination
    aug = [xtx[r][:] + [xty[r]] for r in range(k)]
    for col in range(k):
        pivot = col
        for r in range(col + 1, k):
            if abs(aug[r][col]) > abs(aug[pivot][col]):
                pivot = r
        if abs(aug[pivot][col]) < 1e-12:
            return None
        if pivot != col:
            aug[col], aug[pivot] = aug[pivot], aug[col]
        div = aug[col][col]
        for j in range(col, k + 1):
            aug[col][j] /= div
        for r in range(k):
            if r == col:
                continue
            factor = aug[r][col]
            for j in range(col, k + 1):
                aug[r][j] -= factor * aug[col][j]
    beta = [aug[r][k] for r in range(k)]
    resid = []
    for i in range(n):
        pred = beta[0]
        for j, col in enumerate(x_cols):
            pred += beta[j + 1] * float(col[i])
        resid.append(float(y[i]) - pred)
    return resid


def neutralize(factor_values, exposures=None, exposure_names=None):
    """Alphalens-style neutralize — residualize factor vs exposures.

    Classic equity usage: neutralize vs market-cap + industry dummies.
    Crypto / single-swap fallback exposures (when exposures is None/empty):
      - ones drift already in intercept
      - ret_1 market proxy (pass via exposures dict key 'mkt' / 'ret_1')
      - abs_ret_1 or atr as 'vol' proxy
      - range_pct as 'liquidity_proxy'

    This is a retained baseline — do not omit from the adapter.
    """
    n = len(factor_values or [])
    if n == 0:
        return list(factor_values or []), {"ok": False, "reason": "empty", "op": "neutralize"}

    # Build exposure columns aligned to factor; drop rows with any None
    exp_map = {}
    if isinstance(exposures, dict):
        exp_map = exposures
    elif isinstance(exposures, (list, tuple)) and exposures and isinstance(exposures[0], (list, tuple)):
        names = exposure_names or ["e%d" % i for i in range(len(exposures))]
        exp_map = {names[i]: exposures[i] for i in range(len(exposures))}

    names = list(exp_map.keys())
    y_idx = []
    y = []
    cols = [[] for _ in names]
    for i, v in enumerate(factor_values or []):
        if v is None:
            continue
        try:
            fv = float(v)
        except Exception:
            continue
        if math.isnan(fv) or math.isinf(fv):
            continue
        row_ok = True
        row_x = []
        for name in names:
            series = exp_map[name]
            if i >= len(series) or series[i] is None:
                row_ok = False
                break
            try:
                xv = float(series[i])
            except Exception:
                row_ok = False
                break
            if math.isnan(xv) or math.isinf(xv):
                row_ok = False
                break
            row_x.append(xv)
        if not row_ok:
            continue
        y_idx.append(i)
        y.append(fv)
        for j, xv in enumerate(row_x):
            cols[j].append(xv)

    out = [None] * n
    meta = {
        "ok": False,
        "op": "neutralize",
        "exposures": names,
        "n_used": len(y),
    }
    if not names:
        # No exposures: demean only (intercept-only neutralize)
        finite = [float(v) for v in (factor_values or []) if v is not None]
        if len(finite) < 10:
            meta["reason"] = "insufficient_no_exposure"
            return list(factor_values or []), meta
        mu = sum(finite) / float(len(finite))
        for i, v in enumerate(factor_values or []):
            if v is None:
                continue
            try:
                out[i] = float(v) - mu
            except Exception:
                out[i] = None
        meta["ok"] = True
        meta["mode"] = "demean_only"
        meta["n_used"] = len(finite)
        return out, meta

    if len(y) < max(20, 5 * (1 + len(names))):
        meta["reason"] = "insufficient_rows"
        # fall back to demean on available y
        if len(y) >= 10:
            mu = sum(y) / float(len(y))
            for i, yi in zip(y_idx, y):
                out[i] = yi - mu
            meta["ok"] = True
            meta["mode"] = "demean_fallback"
            return out, meta
        return list(factor_values or []), meta

    resid = _ols_residual(y, cols)
    if resid is None:
        meta["reason"] = "ols_fail"
        return list(factor_values or []), meta
    for i, r in zip(y_idx, resid):
        out[i] = r
    meta["ok"] = True
    meta["mode"] = "ols_residual"
    return out, meta


def prepare_factor(factor_values, exposures=None, winsor_limits=(0.01, 0.01)):
    """Mandatory hygiene pipeline: winsorize → neutralize.

    Returns cleaned series + audit dict. Call before IC/IR screening.
    """
    w_vals, w_meta = winsorize(factor_values, limits=winsor_limits)
    n_vals, n_meta = neutralize(w_vals, exposures=exposures)
    return n_vals, {
        "winsorize": w_meta,
        "neutralize": n_meta,
        "pipeline": ["winsorize", "neutralize"],
    }


def default_crypto_exposures(factor_matrix):
    """Build neutralize exposures from OKX factor matrix proxies.

    Equity Alphalens uses mkt-cap + industry; here:
      mkt   ← ret_1 (market/own-return proxy)
      vol   ← abs_ret_1 or atr_pct_14
      liq   ← range_pct
    """
    fm = factor_matrix or {}
    exp = {}
    if fm.get("ret_1"):
        exp["mkt"] = fm["ret_1"]
    if fm.get("abs_ret_1"):
        exp["vol"] = fm["abs_ret_1"]
    elif fm.get("atr_pct_14"):
        exp["vol"] = fm["atr_pct_14"]
    if fm.get("range_pct"):
        exp["liq"] = fm["range_pct"]
    return exp


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
                 min_abs_ic=0.02, min_ir=0.15, max_turnover=0.65,
                 exposures=None, winsor_limits=(0.01, 0.01),
                 apply_hygiene=True):
    """Stage ②/④ gate on one factor series (winsorize+neutralize first)."""
    hygiene = None
    series = factor_values
    if apply_hygiene:
        series, hygiene = prepare_factor(
            factor_values, exposures=exposures, winsor_limits=winsor_limits,
        )
    ic_pack = rolling_ic(series, fwd_returns)
    turn_pack = quantile_turnover(series)
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
        "hygiene": hygiene,
        "thresholds": {
            "min_abs_ic": min_abs_ic,
            "min_ir": min_ir,
            "max_turnover": max_turnover,
            "winsor_limits": list(winsor_limits) if winsor_limits else None,
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


def validate_hypothesis(factor_matrix, fwd_returns, core_factors=None,
                        exposures=None, winsor_limits=(0.01, 0.01)):
    """Stage ②: winsorize+neutralize → screen core factors + causal check."""
    core_factors = list(core_factors or []) or list((factor_matrix or {}).keys())[:4]
    if exposures is None:
        exposures = default_crypto_exposures(factor_matrix)
    rows = []
    any_pass = False
    for name in core_factors:
        series = (factor_matrix or {}).get(name) or []
        # Exclude self-exposure when factor equals an exposure column
        exp_use = {k: v for k, v in (exposures or {}).items() if k != name and name not in (k,)}
        # Also drop exposure series that is identical key to factor name variants
        if name in exp_use:
            exp_use = dict(exp_use)
            exp_use.pop(name, None)
        screen = screen_factor(
            series, fwd_returns, name=name,
            exposures=exp_use, winsor_limits=winsor_limits,
        )
        cleaned, _ = prepare_factor(series, exposures=exp_use, winsor_limits=winsor_limits)
        causal = causal_pre_post(cleaned, fwd_returns)
        # Prefer BOTH screen + causal; do not accept IC-only correlation illusions
        row["accepted"] = bool(screen.get("passed")) and bool(causal.get("significant"))
        if row["accepted"]:
            any_pass = True
        rows.append(row)
    return {
        "ok": True,
        "schema": "qiyu_hypothesis_validate_v1",
        "passed": any_pass,
        "factors": rows,
        "hygiene_baseline": ["winsorize", "neutralize"],
        "exposures_used": list((exposures or {}).keys()),
        "probe": probe(),
        "at": _now(),
        "note_zh": (
            "假设验证（Alphalens/CausalImpact 风格）："
            "先 winsorize 去极值、再 neutralize 中性化，然后 IC/IR/换手 + 因果显著；"
            "仅有高 IC 无因果支撑不得进入因子挖掘。"
        ),
    }


def rescreen_candidates(candidates, factor_matrix, fwd_returns,
                        exposures=None, winsor_limits=(0.01, 0.01)):
    """Stage ④: re-screen mined candidates with winsorize+neutralize → IC/IR/turnover."""
    if exposures is None:
        exposures = default_crypto_exposures(factor_matrix)
    kept = []
    dropped = []
    for cand in candidates or []:
        name = cand.get("factor")
        series = (factor_matrix or {}).get(name) or []
        exp_use = {k: v for k, v in (exposures or {}).items() if k != name}
        screen = screen_factor(
            series, fwd_returns, name=name or "unknown",
            exposures=exp_use, winsor_limits=winsor_limits,
        )
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
        "hygiene_baseline": ["winsorize", "neutralize"],
        "at": _now(),
    }
