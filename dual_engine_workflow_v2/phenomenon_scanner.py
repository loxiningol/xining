# -*- coding: utf-8 -*-
"""Multi-outcome phenomenon scanner; describes evidence and writes no rules."""
from __future__ import print_function

import math
from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _finite(xs):
    out = []
    for x in xs or []:
        try:
            v = float(x)
            if not math.isnan(v) and not math.isinf(v):
                out.append(v)
        except Exception:
            pass
    return out


def _mean(xs):
    vals = _finite(xs)
    return sum(vals) / float(len(vals)) if vals else None


def _std(xs):
    vals = _finite(xs)
    if len(vals) < 2:
        return None
    mu = _mean(vals)
    return math.sqrt(max(0.0, sum((x - mu) ** 2 for x in vals) / float(len(vals) - 1)))


def _t_stat(a, b):
    a, b = _finite(a), _finite(b)
    if len(a) < 12 or len(b) < 20:
        return None
    sa, sb = _std(a), _std(b)
    se = math.sqrt((sa * sa) / len(a) + (sb * sb) / len(b))
    return ((_mean(a) - _mean(b)) / se) if se > 1e-12 else 0.0


def _quantile(xs, q):
    vals = sorted(_finite(xs))
    if not vals:
        return None
    return vals[max(0, min(len(vals) - 1, int(float(q) * (len(vals) - 1))))]


def _causal_mask(series, side, q, window=240, min_history=80):
    # Reuse the exact prior-only event definition used by the probe protocol.
    try:
        from .probe_protocol import _causal_quantile_mask
        return _causal_quantile_mask(series, side=side, q=q,
                                     window=window, min_history=min_history)
    except Exception:
        threshold = _quantile(series, q if side == "high" else 1.0 - q)
        if threshold is None:
            return [False] * len(series or [])
        return [x is not None and ((float(x) >= threshold) if side == "high"
                                   else (float(x) <= threshold)) for x in series]


def _actual_outcomes(candles, horizons):
    rows = list(candles or [])
    out = {}
    for h in horizons:
        directional, absolute = [None] * len(rows), [None] * len(rows)
        for i in range(len(rows) - int(h)):
            try:
                start = float(rows[i]["close"])
                end = float(rows[i + int(h)]["close"])
                if start:
                    directional[i] = end / start - 1.0
                    absolute[i] = abs(directional[i])
            except Exception:
                pass
        out[int(h)] = {"directional_return": directional,
                       "absolute_movement": absolute}
    return out


def scan_all(factor_matrix, fwd_returns, max_phenomena=30, candles=None,
             horizons=(1, 3, 6, 12), timeframe=None):
    """Scan real horizons and separate directional from volatility effects."""
    matrix = factor_matrix or {}
    if candles:
        outcomes = _actual_outcomes(candles, horizons)
    else:
        outcomes = {3: {"directional_return": list(fwd_returns or []),
                        "absolute_movement": [abs(x) if x is not None else None
                                              for x in (fwd_returns or [])]}}
    phenomena = []
    # Bound CPU but include new mechanism proxies first.
    preferred = [
        "exhaustion_score", "absorption_proxy", "impact_decay_proxy",
        "downside_velocity_decay", "reclaim_strength", "squeeze_persistence",
        "volatility_acceleration", "breakout_acceptance", "signed_volume_pressure",
    ]
    names = [x for x in preferred if x in matrix]
    names += [x for x in sorted(matrix.keys()) if x not in names]
    for name in names[:28]:
        series = matrix.get(name) or []
        for side in ("high", "low"):
            for q in (0.80, 0.90):
                mask = _causal_mask(series, side, q)
                for horizon, outcome_pack in outcomes.items():
                    for outcome_name, values in outcome_pack.items():
                        n = min(len(mask), len(values))
                        treated = [values[i] for i in range(n)
                                   if mask[i] and values[i] is not None]
                        control = [values[i] for i in range(n)
                                   if (not mask[i]) and values[i] is not None]
                        t = _t_stat(treated, control)
                        if t is None:
                            continue
                        mt, mc = _mean(treated), _mean(control)
                        shift = (mt - mc) if mt is not None and mc is not None else None
                        # Keep strong evidence and a limited descriptive frontier.
                        evidence = "formal_signal" if abs(t) >= 1.96 else (
                            "descriptive_candidate" if abs(t) >= 1.0 else "weak_observation"
                        )
                        if evidence == "weak_observation":
                            continue
                        phenomena.append({
                            "phenomenon_id": "PH_%s_%s_q%s_h%s_%s" % (
                                name, side, int(q * 100), horizon, outcome_name),
                            "type": "conditional_distribution_shift",
                            "outcome_type": outcome_name,
                            "factor": name, "side": side, "quantile": q,
                            "treated_mean": mt, "control_mean": mc, "shift": shift,
                            "t_stat": t, "n_treated": len(_finite(treated)),
                            "n_control": len(_finite(control)),
                            "horizon_bars": int(horizon), "horizon_note": "真实前瞻周期",
                            "evidence_level": evidence,
                            "causal_claim": False,
                            "statement_zh": (
                                "%s处于%s侧%.0f%%事件时，真实向前%d根的%s相对对照偏移%.6f；"
                                "当前仅为%s，不作因果或盈利承诺。"
                                % (name, side, q * 100, int(horizon), outcome_name,
                                   float(shift or 0.0), evidence)
                            ),
                        })
    phenomena.sort(key=lambda r: (
        1 if r.get("evidence_level") == "formal_signal" else 0,
        abs(float(r.get("t_stat") or 0.0)),
        int(r.get("n_treated") or 0),
    ), reverse=True)
    # Preserve breadth: take the strongest observation from each
    # factor × outcome cell before filling remaining slots by strength.
    frontier, rest = [], []
    seen_cells = set()
    for row in phenomena:
        cell = (row.get("factor"), row.get("outcome_type"))
        if cell not in seen_cells:
            seen_cells.add(cell)
            frontier.append(row)
        else:
            rest.append(row)
    kept = (frontier + rest)[: int(max_phenomena)]
    return {
        "ok": True, "n": len(kept), "phenomena": kept,
        "outcomes_scanned": ["方向收益", "绝对波动"],
        "horizons_scanned": sorted(outcomes.keys()),
        "uses_actual_horizons": bool(candles),
        "descriptive_is_not_tradable": True,
        "note_zh": "现象层只描述真实周期上的方向/波动偏移，不生成交易规则。",
        "at": _now(),
    }


def scan_conditional_shifts(factor_matrix, fwd_returns, horizons_note="h=label",
                            min_abs_t=1.96, max_phenomena=30):
    # Compatibility wrapper.
    return scan_all(factor_matrix, fwd_returns, max_phenomena=max_phenomena)


def scan_vol_reaction_asymmetry(factor_matrix, fwd_returns):
    rows = scan_all({"range_pct": (factor_matrix or {}).get("range_pct") or []},
                    fwd_returns, max_phenomena=4)
    return {"ok": bool(rows.get("n")), "observations": rows.get("phenomena") or [],
            "at": _now()}


def phenomenon_to_hypothesis(phenomenon, rank=0):
    ph = phenomenon or {}
    direction = "volatility_only" if ph.get("outcome_type") == "absolute_movement" else (
        "positive_shift" if float(ph.get("shift") or 0) > 0 else "negative_shift"
    )
    return {
        "hypothesis_id": "H_%s" % (ph.get("phenomenon_id") or ("ph_%d" % rank)),
        "source": "phenomenon_scanner", "path": "data_to_theory",
        "mechanism_id": None, "family": "data_driven",
        "payoff_payer": "unknown_pending_mechanism_link",
        "constraint_used": ["empirical_conditional_shift"],
        "observable_proxy": [ph.get("factor")] if ph.get("factor") else [],
        "predicted_direction": direction,
        "horizon": ph.get("horizon_bars") or ph.get("horizon_note"),
        "conditional_on": ["%s_%s" % (ph.get("factor"), ph.get("side"))],
        "failure_conditions": ["shift_disappears_oos", "driven_by_one_regime"],
        "capacity_limit": "unknown",
        "alternative_explanations": ["multiple_testing_artifact", "regime_bias", "cost"],
        "factor_hints": [ph.get("factor")] if ph.get("factor") else [],
        "simplest_antifalsify": "time_shift_and_cross_asset_placebo",
        "phenomenon": ph, "statement_zh": ph.get("statement_zh"),
    }


def probe():
    return {
        "ok": True, "provider": "multi_outcome_phenomenon_scanner_v2",
        "tools_zh": "真实多周期方向偏移 / 波动偏移 / 描述证据分级；不生成交易规则",
        "at": _now(),
    }
