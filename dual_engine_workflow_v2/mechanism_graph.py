# -*- coding: utf-8 -*-
"""Alpha Mechanism Graph — structured payoff mechanisms (not free-text RAG).

A creative idea MUST complete seven fields before probe generation:
  actor, constraint, forced_trade, observable_proxy, predicted_effect,
  who_pays, failure_conditions.
"""
from __future__ import print_function

import copy
import json
import os
from datetime import datetime
from pathlib import Path


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


SEED_MECHANISMS = (
    {
        "mechanism_id": "inventory_rebalancing_001",
        "economic_actor": ["market_maker"],
        "constraint": ["inventory_limit", "risk_budget"],
        "forced_trade": "inventory_skew_forces_mean_reverting_quotes",
        "observable_proxy": ["close_z_20", "spread_proxy_range", "volume_z"],
        "predicted_effect": {
            "direction": "short_horizon_reversal",
            "horizon": "5m-30m",
            "conditional_on": ["high_range", "temporary_volume_shock"],
        },
        "who_pays": "urgent_takers_crossing_wide_spread",
        "alternative_explanations": [
            "information_arrival", "bid_ask_bounce", "data_timestamp_error",
        ],
        "capacity_limit": "low_to_medium",
        "known_failure_modes": ["persistent_informed_flow", "exchange_outage"],
        "family": "mean_reversion",
        "factor_hints": ["close_z_20", "dist_roll_low", "lower_wick_pct", "range_pct"],
    },
    {
        "mechanism_id": "stop_hunt_liquidity_sweep_001",
        "economic_actor": ["predator_liquidity", "retail_stops"],
        "constraint": ["stop_cluster", "thin_book"],
        "forced_trade": "stops_triggered_then_inventory_needs_unwind",
        "observable_proxy": ["dist_roll_high", "dist_roll_low", "upper_wick_pct", "lower_wick_pct"],
        "predicted_effect": {
            "direction": "reversal_after_sweep",
            "horizon": "15m-2h",
            "conditional_on": ["wick_dominance", "failed_break"],
        },
        "who_pays": "stop_out_participants",
        "alternative_explanations": ["true_breakout_continuation", "news_impulse"],
        "capacity_limit": "medium",
        "known_failure_modes": ["trend_day", "funding_cascade"],
        "family": "liquidity_sweep",
        "factor_hints": ["dist_roll_high", "dist_roll_low", "upper_wick_pct", "lower_wick_pct"],
    },
    {
        "mechanism_id": "vol_squeeze_break_001",
        "economic_actor": ["option_hedger", "breakout_followers"],
        "constraint": ["gamma_hedging", "range_compression"],
        "forced_trade": "hedgers_chase_after_range_expansion",
        "observable_proxy": ["atr_pct_14", "range_pct", "ret_12"],
        "predicted_effect": {
            "direction": "continuation_after_compression",
            "horizon": "30m-6h",
            "conditional_on": ["low_prior_range", "volume_expansion"],
        },
        "who_pays": "fade_traders_fighting_expansion",
        "alternative_explanations": ["random_walk_after_noise", "session_open_artifact"],
        "capacity_limit": "medium_to_high",
        "known_failure_modes": ["fake_break_in_chop"],
        "family": "vol_squeeze_break",
        "factor_hints": ["range_pct", "atr_pct_14", "ret_12", "volume_z"],
    },
    {
        "mechanism_id": "funding_crowding_fade_001",
        "economic_actor": ["perp_speculator", "basis_arb"],
        "constraint": ["funding_payment", "margin"],
        "forced_trade": "crowded_side_pays_funding_then_deleverages",
        "observable_proxy": ["ret_12", "close_z_20", "volume_z"],
        "predicted_effect": {
            "direction": "fade_after_crowding",
            "horizon": "1h-24h",
            "conditional_on": ["extended_trend", "elevated_vol"],
        },
        "who_pays": "late_momentum_entrants",
        "alternative_explanations": ["macro_trend_persist", "exchange_outage"],
        "capacity_limit": "medium",
        "known_failure_modes": ["structural_bull_or_bear"],
        "family": "crowding_fade",
        "factor_hints": ["close_z_20", "ret_12", "atr_pct_14"],
    },
    {
        "mechanism_id": "forced_liquidation_bounce_001",
        "economic_actor": ["forced_liquidator"],
        "constraint": ["margin_constraint", "liquidation_engine"],
        "forced_trade": "engine_market_sells_then_pressure_ends",
        "observable_proxy": ["volume_z", "range_pct", "lower_wick_pct", "abs_ret_1"],
        "predicted_effect": {
            "direction": "short_horizon_bounce_after_liquidation_burst",
            "horizon": "5m-60m",
            "conditional_on": ["volume_spike", "wick_reclaim"],
        },
        "who_pays": "late_sellers_after_flush",
        "alternative_explanations": ["information_cascade", "index_common_move"],
        "capacity_limit": "low",
        "known_failure_modes": ["multi_wave_liquidation"],
        "family": "liquidation_bounce",
        "factor_hints": ["volume_z", "lower_wick_pct", "range_pct", "abs_ret_1"],
    },
)


REQUIRED_FIELDS = (
    "economic_actor",
    "constraint",
    "forced_trade",
    "observable_proxy",
    "predicted_effect",
    "who_pays",
    "known_failure_modes",
)


def graph_dir():
    d = _root() / "auto_trade" / "dual_engine" / "mechanism_graph"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_graph():
    """Seed + optional on-disk mechanisms."""
    items = [copy.deepcopy(m) for m in SEED_MECHANISMS]
    path = graph_dir() / "mechanisms.json"
    if path.exists():
        try:
            extra = json.loads(path.read_text())
            if isinstance(extra, list):
                items.extend(extra)
            elif isinstance(extra, dict) and isinstance(extra.get("mechanisms"), list):
                items.extend(extra["mechanisms"])
        except Exception:
            pass
    # de-dupe by mechanism_id
    seen = set()
    out = []
    for m in items:
        mid = m.get("mechanism_id")
        if mid in seen:
            continue
        seen.add(mid)
        out.append(m)
    return out


def completeness_check(mechanism):
    """Seven-step gate: incomplete mechanisms never enter factor generation."""
    m = mechanism or {}
    missing = []
    for f in REQUIRED_FIELDS:
        v = m.get(f)
        if v is None or v == "" or v == [] or v == {}:
            missing.append(f)
    pe = m.get("predicted_effect") or {}
    if not pe.get("direction") or not pe.get("horizon"):
        if "predicted_effect" not in missing:
            missing.append("predicted_effect.direction_or_horizon")
    ok = len(missing) == 0
    return {
        "ok": ok,
        "passed": ok,
        "missing": missing,
        "mechanism_id": m.get("mechanism_id"),
        "note_zh": (
            "机制七步齐全，可进探针" if ok else
            ("机制不完整，禁止进因子生成: %s" % ",".join(missing))
        ),
    }


def select_for_brief(brief, symbol=None, timeframe=None, limit=12):
    """Rank seed mechanisms against brief keywords; keep population, no early pick-1."""
    text = str(brief or "").lower()
    graph = load_graph()
    scored = []
    for m in graph:
        score = 1.0
        blob = " ".join([
            str(m.get("mechanism_id") or ""),
            str(m.get("family") or ""),
            " ".join(m.get("economic_actor") or []),
            " ".join(m.get("constraint") or []),
            str(m.get("forced_trade") or ""),
            str((m.get("predicted_effect") or {}).get("direction") or ""),
        ]).lower()
        for token in (
            "回归", "反转", "mean", "reversion", "库存",
            "突破", "break", "压缩", "squeeze",
            "清算", "liquid", "资金费", "funding",
            "扫损", "sweep", "止损", "动量", "trend",
        ):
            if token in text and token in blob:
                score += 1.5
            elif token in text:
                score += 0.2
        gate = completeness_check(m)
        row = copy.deepcopy(m)
        row["match_score"] = score
        row["completeness"] = gate
        row["symbol"] = symbol
        row["timeframe"] = timeframe
        if gate.get("passed"):
            scored.append(row)
    scored.sort(key=lambda r: float(r.get("match_score") or 0), reverse=True)
    return {
        "ok": True,
        "n": len(scored[: int(limit)]),
        "mechanisms": scored[: int(limit)],
        "n_incomplete_dropped": sum(
            1 for m in graph if not completeness_check(m).get("passed")
        ),
        "at": _now(),
    }


def mechanism_to_hypothesis(mechanism, source="mechanism_graph"):
    m = mechanism or {}
    pe = m.get("predicted_effect") or {}
    return {
        "hypothesis_id": "H_%s" % (m.get("mechanism_id") or "unknown"),
        "source": source,
        "path": "theory_to_data",
        "mechanism_id": m.get("mechanism_id"),
        "family": m.get("family"),
        "payoff_payer": m.get("who_pays"),
        "constraint_used": m.get("constraint"),
        "observable_proxy": m.get("observable_proxy"),
        "predicted_direction": pe.get("direction"),
        "horizon": pe.get("horizon"),
        "conditional_on": pe.get("conditional_on"),
        "failure_conditions": m.get("known_failure_modes"),
        "capacity_limit": m.get("capacity_limit"),
        "alternative_explanations": m.get("alternative_explanations"),
        "factor_hints": m.get("factor_hints") or list(m.get("observable_proxy") or []),
        "simplest_antifalsify": "shuffle_event_time_and_sign_flip_should_kill_edge",
        "completeness": completeness_check(m),
    }


def probe():
    g = load_graph()
    return {
        "ok": True,
        "provider": "mechanism_graph_v1",
        "n_mechanisms": len(g),
        "required_fields": list(REQUIRED_FIELDS),
        "at": _now(),
    }
