# -*- coding: utf-8 -*-
"""Cost-aware feasibility before strategy assembly.

Costs are componentised and scenario-specific.  Values are conservative
estimates unless an operator supplies measured values through environment
variables; estimates are never presented as exchange fill facts.
"""
from __future__ import print_function

import math
import os
from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _env_float(name, default):
    try:
        return float(os.environ.get(name) or default)
    except Exception:
        return float(default)


def _liquidity_tier(symbol):
    s = str(symbol or "").upper().replace("-SWAP", "").replace("USDT", "")
    if s in ("BTC", "ETH"):
        return "deep"
    if s in ("SOL", "XRP", "ADA", "DOGE", "LTC", "BCH"):
        return "medium"
    if s in ("XAU", "XAG", "CL", "NG"):
        return "commodity_proxy"
    return "thin_or_unknown"


def _component_estimates(symbol=None, hold_bars=1):
    tier = _liquidity_tier(symbol)
    table = {
        "deep": {"half_spread": 0.00005, "slippage": 0.00010, "impact": 0.00005},
        "medium": {"half_spread": 0.00010, "slippage": 0.00016, "impact": 0.00009},
        "commodity_proxy": {"half_spread": 0.00014, "slippage": 0.00022, "impact": 0.00012},
        "thin_or_unknown": {"half_spread": 0.00020, "slippage": 0.00030, "impact": 0.00018},
    }
    base = dict(table[tier])
    base["taker_fee_one_way"] = _env_float("QIYU_TAKER_FEE_ONE_WAY", 0.0005)
    base["maker_fee_one_way"] = _env_float("QIYU_MAKER_FEE_ONE_WAY", 0.0002)
    base["funding"] = _env_float("QIYU_FUNDING_PER_PROBE", 0.00002) * max(
        1.0, float(hold_bars or 1) / 8.0
    )
    base["adverse_selection"] = {
        "deep": 0.00005, "medium": 0.00008,
        "commodity_proxy": 0.00012, "thin_or_unknown": 0.00015,
    }[tier]
    return tier, base


def _scenario(name, fee, spread, slippage, impact, funding, adverse, tier,
              multiplier=1.0, order_model="unknown"):
    components = {
        "fee": float(fee) * multiplier,
        "spread": float(spread) * multiplier,
        "slippage": float(slippage) * multiplier,
        "market_impact": float(impact) * multiplier,
        "funding": float(funding) * multiplier,
        "adverse_selection": float(adverse) * multiplier,
    }
    return {
        "scenario": name,
        "order_model": order_model,
        "liquidity_tier": tier,
        "components": components,
        "total_friction": sum(components.values()),
        "evidence": "conservative_estimate_not_real_fill",
        "measured_components": [],
        "estimated_components": sorted(components.keys()),
    }


def cost_scenario_matrix(symbol=None, hold_bars=1):
    """Return comparable order/cost scenarios and a threefold stress case."""
    tier, c = _component_estimates(symbol, hold_bars)
    taker = c["taker_fee_one_way"]
    maker = c["maker_fee_one_way"]
    hs = c["half_spread"]
    # Spread below is the sum of half-spread paid across both legs.
    tt = _scenario(
        "taker_taker", taker * 2.0, hs * 2.0, c["slippage"] * 2.0,
        c["impact"], c["funding"], c["adverse_selection"], tier,
        order_model="market_entry_market_exit",
    )
    mt = _scenario(
        "maker_taker", maker + taker, hs, c["slippage"], c["impact"] * 0.75,
        c["funding"], c["adverse_selection"] * 1.15, tier,
        order_model="passive_entry_market_exit",
    )
    mm = _scenario(
        "maker_maker", maker * 2.0, 0.0, c["slippage"] * 0.25, c["impact"] * 0.40,
        c["funding"], c["adverse_selection"] * 1.35, tier,
        order_model="passive_entry_passive_exit_fill_not_guaranteed",
    )
    stress = _scenario(
        "stress_threefold", taker * 2.0, hs * 2.0, c["slippage"] * 2.0,
        c["impact"], c["funding"], c["adverse_selection"], tier,
        multiplier=3.0, order_model="market_entry_market_exit_threefold_stress",
    )
    return {"taker_taker": tt, "maker_taker": mt,
            "maker_maker": mm, "stress_threefold": stress}


def friction_bundle(fee_rt=None, spread_rt=None, slippage=None, impact=None,
                    funding=None):
    """Legacy compatibility bundle. Explicit values remain labelled estimates."""
    values = {
        "fee_rt": 0.0010 if fee_rt is None else float(fee_rt),
        "spread_rt": 0.0002 if spread_rt is None else float(spread_rt),
        "slippage": 0.0002 if slippage is None else float(slippage),
        "impact": 0.0001 if impact is None else float(impact),
        "funding": 0.00002 if funding is None else float(funding),
    }
    values["total_friction"] = sum(values.values())
    values["evidence"] = "conservative_estimate_not_real_fill"
    return values


def edge_to_friction(gross_edge_per_trade, friction=None, min_efr=1.5):
    fr = friction_bundle() if friction is None else friction_bundle(
        fee_rt=friction.get("fee_rt"), spread_rt=friction.get("spread_rt"),
        slippage=friction.get("slippage"), impact=friction.get("impact"),
        funding=friction.get("funding"),
    )
    edge = float(gross_edge_per_trade or 0.0)
    total = float(fr.get("total_friction") or 0.0)
    ratio = None if total <= 1e-12 else edge / total
    passed = bool(ratio is not None and ratio >= float(min_efr) and edge - total > 0)
    return {
        "ok": True, "schema": "qiyu_edge_friction_v2",
        "gross_edge_per_trade": edge, "friction": fr, "efr": ratio,
        "mean_net": edge - total, "min_efr": float(min_efr), "passed": passed,
        "note_zh": "净收益必须为正且优势/摩擦比达到门槛；杠杆不能修复负期望。",
        "at": _now(),
    }


def capacity_estimate(gross_edge, turnover_per_day, adv_notional=None,
                      impact_elasticity=0.5, max_participation=0.05):
    edge = float(gross_edge or 0.0)
    turn = max(float(turnover_per_day or 0.0), 1e-9)
    adv = float(adv_notional) if adv_notional not in (None, 0, 0.0) else None
    if adv is None:
        return {
            "ok": True, "estimated_capacity": None,
            "relative_capacity_score": edge / (turn * float(impact_elasticity) + 1e-12),
            "turnover_per_day": turn, "impact_elasticity": impact_elasticity,
            "max_participation": max_participation,
            "note_zh": "缺少真实成交额与盘口深度，只报告相对容量，不伪造绝对容量。",
            "at": _now(),
        }
    cap = adv * float(max_participation) / (1.0 + turn * float(impact_elasticity))
    return {
        "ok": True, "gross_edge_per_trade": edge,
        "expected_net_edge": None, "turnover": turn, "estimated_capacity": cap,
        "impact_elasticity": impact_elasticity, "leverage_survival_region": None,
        "expected_edge_half_life": (math.log(2) / max(turn * 0.1, 1e-6)) if edge > 0 else None,
        "note_zh": "容量仍为估计，未取得真实盘口成交率前不得作为实盘保证。",
        "at": _now(),
    }


def research_value(net_edge_cred, capacity, mechanism_life, state_coverage,
                   complementarity, complexity):
    num = (float(net_edge_cred or 0) * float(capacity or 1.0) *
           float(mechanism_life or 1.0) * float(state_coverage or 1.0) *
           float(complementarity or 1.0))
    return num / max(float(complexity or 1.0), 0.1)


def evaluate_early_feasibility(probe_best, n_bars=None, span_days=None,
                               min_efr=1.5, adv_notional=None, symbol=None):
    if not probe_best or not probe_best.get("ok"):
        return {"ok": False, "passed": False, "error": "no_probe", "at": _now()}
    gross = float(probe_best.get("mean_hit") or 0.0)
    matrix = probe_best.get("cost_scenarios") or cost_scenario_matrix(
        symbol=symbol, hold_bars=probe_best.get("horizon_bars") or 1,
    )
    primary_name = probe_best.get("primary_cost_scenario") or "taker_taker"
    primary = matrix.get(primary_name) or {}
    total = float(primary.get("total_friction") or 0.0)
    ratio = None if total <= 1e-12 else gross / total
    mean_net = gross - total
    passed = bool(
        ((probe_best.get("evidence_axes") or {}).get("statistical_direction")) and
        ((probe_best.get("evidence_axes") or {}).get("economic_magnitude")) and
        mean_net > 0 and ratio is not None and ratio >= float(min_efr)
    )
    efr = {
        "ok": True, "passed": passed, "efr": ratio, "mean_net": mean_net,
        "gross_edge_per_trade": gross, "min_efr": float(min_efr),
        "primary_scenario": primary_name, "cost_scenarios": matrix,
        "stress_threefold_positive": bool(
            gross - float((matrix.get("stress_threefold") or {}).get("total_friction") or 0) > 0
        ),
    }
    days = max(float(span_days or 1.0), 1.0)
    n_events = float(probe_best.get("n_independent_events") or
                     probe_best.get("n_hits") or 0.0)
    cap = capacity_estimate(gross, n_events / days, adv_notional=adv_notional)
    rv = research_value(
        1.0 if passed else 0.2,
        cap.get("relative_capacity_score") or ((cap.get("estimated_capacity") or 1.0) / 1e6),
        1.0, 0.8, 1.0, 1.0 + 0.1 * len(str(probe_best.get("event_id") or "")),
    )
    return {"ok": True, "passed": passed, "efr": efr, "capacity": cap,
            "research_value": rv, "cost_scenarios": matrix, "at": _now()}


def probe():
    return {
        "ok": True, "provider": "cost_scenario_matrix_v2",
        "scenarios": ["市价进出", "被动进场市价退出", "被动进出", "三倍摩擦压力"],
        "costs_are_labelled_estimates": True, "min_efr_default": 1.5,
        "at": _now(),
    }
