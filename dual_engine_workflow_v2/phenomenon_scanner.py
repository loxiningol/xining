# -*- coding: utf-8 -*-
"""Phenomenon scanner — find statistical anomalies BEFORE writing strategies.

Output is conditional distribution shifts / state breaks, never trade rules.
"""
from __future__ import print_function

import math
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


def _mean(xs):
    xs = _finite(xs)
    if not xs:
        return None
    return sum(xs) / float(len(xs))


def _std(xs):
    xs = _finite(xs)
    if len(xs) < 2:
        return None
    m = sum(xs) / float(len(xs))
    var = sum((x - m) ** 2 for x in xs) / float(len(xs) - 1)
    return math.sqrt(var) if var > 0 else 0.0


def _quantile(xs, q):
    xs = sorted(_finite(xs))
    if not xs:
        return None
    if q <= 0:
        return xs[0]
    if q >= 1:
        return xs[-1]
    i = int(q * (len(xs) - 1))
    return xs[i]


def _t_stat(a, b):
    a = _finite(a)
    b = _finite(b)
    if len(a) < 20 or len(b) < 20:
        return None
    ma, mb = _mean(a), _mean(b)
    sa, sb = _std(a), _std(b)
    if sa is None or sb is None:
        return None
    se = math.sqrt((sa * sa) / len(a) + (sb * sb) / len(b))
    if se <= 1e-12:
        return 0.0
    return (ma - mb) / se


def _mask_top(series, q=0.8):
    thr = _quantile(series, q)
    if thr is None:
        return [False] * len(series)
    return [v is not None and float(v) >= thr for v in series]


def _fwd_slice(fwd, mask):
    return [fwd[i] for i, keep in enumerate(mask) if keep and i < len(fwd)]


def _comp_slice(fwd, mask):
    return [fwd[i] for i, keep in enumerate(mask) if (not keep) and i < len(fwd)]


def scan_conditional_shifts(factor_matrix, fwd_returns, horizons_note="h=label",
                            min_abs_t=1.96, max_phenomena=30):
    """Scan each factor's extreme quantile for fwd-return distribution shift."""
    matrix = factor_matrix or {}
    fwd = list(fwd_returns or [])
    phenomena = []
    for name, series in matrix.items():
        if not series or len(series) != len(fwd):
            # allow shorter by aligning tail
            n = min(len(series or []), len(fwd))
            if n < 80:
                continue
            series = list(series)[-n:]
            fwd_use = fwd[-n:]
        else:
            fwd_use = fwd
            n = len(fwd_use)
        for q, side in ((0.8, "high"), (0.2, "low")):
            if side == "high":
                mask = _mask_top(series, q=q)
            else:
                thr = _quantile(series, q)
                mask = [v is not None and float(v) <= thr for v in series]
            treated = _fwd_slice(fwd_use, mask)
            control = _comp_slice(fwd_use, mask)
            t = _t_stat(treated, control)
            if t is None:
                continue
            if abs(float(t)) < float(min_abs_t):
                continue
            mt, mc = _mean(treated), _mean(control)
            phenomena.append({
                "phenomenon_id": "PH_%s_%s" % (name, side),
                "type": "conditional_return_shift",
                "factor": name,
                "side": side,
                "quantile": q if side == "high" else q,
                "treated_mean": mt,
                "control_mean": mc,
                "shift": (None if mt is None or mc is None else mt - mc),
                "t_stat": t,
                "n_treated": len(treated),
                "n_control": len(control),
                "horizon_note": horizons_note,
                "statement_zh": (
                    "在 %s 处于%s分位时，前瞻收益均值相对对照偏移 %.5f (t=%.2f)"
                    % (name, side, (mt - mc) if mt is not None and mc is not None else 0.0, t)
                ),
            })
    phenomena.sort(key=lambda r: abs(float(r.get("t_stat") or 0)), reverse=True)
    return {
        "ok": True,
        "n": len(phenomena[: int(max_phenomena)]),
        "phenomena": phenomena[: int(max_phenomena)],
        "at": _now(),
        "note_zh": "只报告现象，不生成交易规则。",
    }


def scan_vol_reaction_asymmetry(factor_matrix, fwd_returns):
    """After high-range bars, check upside vs downside fwd asymmetry."""
    rng = (factor_matrix or {}).get("range_pct") or (factor_matrix or {}).get("atr_pct_14")
    if not rng:
        return {"ok": False, "error": "no_range_proxy"}
    mask = _mask_top(rng, q=0.8)
    fwd = list(fwd_returns or [])
    up = [fwd[i] for i, k in enumerate(mask) if k and i < len(fwd) and fwd[i] is not None and fwd[i] > 0]
    dn = [abs(fwd[i]) for i, k in enumerate(mask) if k and i < len(fwd) and fwd[i] is not None and fwd[i] < 0]
    mu, md = _mean(up), _mean(dn)
    if mu is None or md is None:
        return {"ok": False, "error": "insufficient"}
    return {
        "ok": True,
        "type": "vol_reaction_asymmetry",
        "up_mean": mu,
        "down_abs_mean": md,
        "asymmetry": mu - md,
        "statement_zh": (
            "高波动后上行反应均值=%.5f，下行绝对均值=%.5f，不对称度=%.5f"
            % (mu, md, mu - md)
        ),
        "at": _now(),
    }


def scan_all(factor_matrix, fwd_returns, max_phenomena=30):
    shifts = scan_conditional_shifts(
        factor_matrix, fwd_returns, max_phenomena=max_phenomena,
    )
    asym = scan_vol_reaction_asymmetry(factor_matrix, fwd_returns)
    extra = []
    if asym.get("ok") and abs(float(asym.get("asymmetry") or 0)) > 1e-6:
        extra.append({
            "phenomenon_id": "PH_vol_reaction_asymmetry",
            "type": asym.get("type"),
            "statement_zh": asym.get("statement_zh"),
            "t_stat": None,
            "shift": asym.get("asymmetry"),
            "factor": "range_pct",
            "side": "high_vol",
        })
    merged = list(shifts.get("phenomena") or []) + extra
    return {
        "ok": True,
        "n": len(merged),
        "phenomena": merged[: int(max_phenomena)],
        "asymmetry": asym,
        "at": _now(),
    }


def phenomenon_to_hypothesis(phenomenon, rank=0):
    ph = phenomenon or {}
    return {
        "hypothesis_id": "H_%s" % (ph.get("phenomenon_id") or ("ph_%d" % rank)),
        "source": "phenomenon_scanner",
        "path": "data_to_theory",
        "mechanism_id": None,
        "family": "data_driven",
        "payoff_payer": "unknown_pending_mechanism_link",
        "constraint_used": ["empirical_conditional_shift"],
        "observable_proxy": [ph.get("factor")] if ph.get("factor") else [],
        "predicted_direction": (
            "positive_shift" if float(ph.get("shift") or 0) > 0 else "negative_shift"
        ),
        "horizon": ph.get("horizon_note") or "label_horizon",
        "conditional_on": ["%s_%s" % (ph.get("factor"), ph.get("side"))],
        "failure_conditions": ["shift_disappears_oos", "driven_by_one_regime"],
        "capacity_limit": "unknown",
        "alternative_explanations": [
            "multiple_testing_artifact", "regime_sampling_bias", "bid_ask_bounce",
        ],
        "factor_hints": [ph.get("factor")] if ph.get("factor") else [],
        "simplest_antifalsify": "time_shift_and_cross_asset_placebo",
        "phenomenon": ph,
        "statement_zh": ph.get("statement_zh"),
    }


def probe():
    return {
        "ok": True,
        "provider": "phenomenon_scanner_v1",
        "tools_zh": "条件收益偏移 / 波动反应不对称；无交易规则输出",
        "at": _now(),
    }
