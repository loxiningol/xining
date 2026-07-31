# -*- coding: utf-8 -*-
"""Early Edge-to-Friction Ratio (EFR) + coarse capacity estimator.

Must run BEFORE full strategy assembly. EFR clearly > 1 required.
"""
from __future__ import print_function

import math
from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# Conservative crypto-perp friction defaults (fraction of notional)
DEFAULT_FEE_RT = 0.0005          # taker 2.5bp * 2
DEFAULT_SPREAD_RT = 0.0004       # half-spread * 2 crossing
DEFAULT_SLIPPAGE = 0.0003
DEFAULT_IMPACT = 0.0002
DEFAULT_FUNDING_PER_HOLD = 0.0001


def friction_bundle(fee_rt=None, spread_rt=None, slippage=None, impact=None,
                    funding=None):
    fee_rt = DEFAULT_FEE_RT if fee_rt is None else float(fee_rt)
    spread_rt = DEFAULT_SPREAD_RT if spread_rt is None else float(spread_rt)
    slippage = DEFAULT_SLIPPAGE if slippage is None else float(slippage)
    impact = DEFAULT_IMPACT if impact is None else float(impact)
    funding = DEFAULT_FUNDING_PER_HOLD if funding is None else float(funding)
    total = fee_rt + spread_rt + slippage + impact + funding
    return {
        "fee_rt": fee_rt,
        "spread_rt": spread_rt,
        "slippage": slippage,
        "impact": impact,
        "funding": funding,
        "total_friction": total,
    }


def edge_to_friction(gross_edge_per_trade, friction=None, min_efr=1.5):
    if friction is None:
        fr = friction_bundle()
    else:
        fr = friction_bundle(
            fee_rt=friction.get("fee_rt"),
            spread_rt=friction.get("spread_rt"),
            slippage=friction.get("slippage"),
            impact=friction.get("impact"),
            funding=friction.get("funding"),
        )
    edge = float(gross_edge_per_trade or 0.0)
    tot = float(fr.get("total_friction") or 0.0)
    efr = None if tot <= 1e-12 else edge / tot
    passed = bool(efr is not None and efr >= float(min_efr))
    return {
        "ok": True,
        "schema": "qiyu_efr_v1",
        "gross_edge_per_trade": edge,
        "friction": fr,
        "efr": efr,
        "min_efr": float(min_efr),
        "passed": passed,
        "note_zh": (
            "EFR=保守单笔毛优势/摩擦合计；进入完整组装前应明显大于1并留余量。"
            if passed else
            "EFR不足：禁止靠杠杆放大；不得进入完整策略组装。"
        ),
        "at": _now(),
    }


def capacity_estimate(gross_edge, turnover_per_day, adv_notional=None,
                      impact_elasticity=0.5, max_participation=0.05):
    """Very coarse capacity: participation * ADV until edge eaten by impact."""
    edge = float(gross_edge or 0.0)
    turn = max(float(turnover_per_day or 0.0), 1e-9)
    adv = float(adv_notional) if adv_notional not in (None, 0, 0.0) else None
    # If ADV unknown, return relative capacity score only
    if adv is None:
        # relative: edge / (turnover * elasticity)
        rel = edge / (turn * float(impact_elasticity) + 1e-12)
        return {
            "ok": True,
            "estimated_capacity": None,
            "relative_capacity_score": rel,
            "turnover_per_day": turn,
            "impact_elasticity": impact_elasticity,
            "max_participation": max_participation,
            "note_zh": "无ADV时仅给相对容量分；有成交额后再估绝对容量。",
            "at": _now(),
        }
    # capacity ≈ ADV * max_participation * edge / (edge + impact_term)
    cap = adv * float(max_participation)
    # degrade if turnover high
    cap *= 1.0 / (1.0 + turn * float(impact_elasticity))
    half_life = None
    if edge > 0:
        half_life = math.log(2) / max(turn * 0.1, 1e-6)
    return {
        "ok": True,
        "gross_edge_per_trade": edge,
        "expected_net_edge": edge * 0.6,
        "turnover": turn,
        "estimated_capacity": cap,
        "impact_elasticity": impact_elasticity,
        "leverage_survival_region": [1, 5, 10],
        "expected_edge_half_life": half_life,
        "at": _now(),
    }


def research_value(net_edge_cred, capacity, mechanism_life, state_coverage,
                   complementarity, complexity):
    """Priority score — not raw return ranking."""
    num = (
        float(net_edge_cred or 0) *
        float(capacity or 1.0) *
        float(mechanism_life or 1.0) *
        float(state_coverage or 1.0) *
        float(complementarity or 1.0)
    )
    den = max(float(complexity or 1.0), 0.1)
    return num / den


def evaluate_early_feasibility(probe_best, n_bars=None, span_days=None,
                               min_efr=1.5, adv_notional=None):
    if not probe_best or not probe_best.get("ok"):
        return {
            "ok": False,
            "passed": False,
            "error": "no_probe",
            "at": _now(),
        }
    # gross edge before cost: mean_hit; probe mean_net already costed with RT
    gross = float(probe_best.get("mean_hit") or 0.0)
    # if mean_hit missing, back out from mean_net + cost
    if abs(gross) < 1e-15:
        gross = float(probe_best.get("mean_net") or 0.0) + float(
            probe_best.get("round_trip_cost") or 0.001
        )
    efr = edge_to_friction(gross, min_efr=min_efr)
    n_hits = float(probe_best.get("n_hits") or 0)
    days = float(span_days or 1.0)
    turn = (n_hits / max(days, 1.0)) if days else 0.0
    cap = capacity_estimate(gross, turn, adv_notional=adv_notional)
    rv = research_value(
        net_edge_cred=1.0 if efr.get("passed") else 0.2,
        capacity=cap.get("relative_capacity_score") or (
            (cap.get("estimated_capacity") or 1.0) / 1e6
        ),
        mechanism_life=1.0,
        state_coverage=0.8,
        complementarity=1.0,
        complexity=1.0 + 0.1 * len(str(probe_best.get("factor") or "")),
    )
    passed = bool(efr.get("passed"))
    return {
        "ok": True,
        "passed": passed,
        "efr": efr,
        "capacity": cap,
        "research_value": rv,
        "at": _now(),
    }


def probe():
    return {
        "ok": True,
        "provider": "edge_friction_capacity_v1",
        "min_efr_default": 1.5,
        "at": _now(),
    }
