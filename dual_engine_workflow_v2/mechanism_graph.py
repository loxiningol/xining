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
    {
        "mechanism_id": "trend_pullback_continuation_001",
        "economic_actor": ["trend_follower", "late_fader"],
        "constraint": ["higher_timeframe_bias", "pullback_depth"],
        "forced_trade": "trend_followers_reenter_after_shallow_pullback",
        "observable_proxy": ["ret_12", "ret_3", "close_z_20", "dist_roll_low"],
        "predicted_effect": {
            "direction": "continuation_after_pullback",
            "horizon": "30m-4h",
            "conditional_on": ["htf_momentum_alive", "not_overextended"],
        },
        "who_pays": "countertrend_faders_into_pullback",
        "alternative_explanations": ["mean_reversion_regime", "news_reversal"],
        "capacity_limit": "medium_to_high",
        "known_failure_modes": ["trend_exhaustion", "regime_flip"],
        "family": "trend_pullback",
        "factor_hints": ["ret_12", "ret_3", "close_z_20", "dist_roll_low"],
    },
    {
        "mechanism_id": "asia_range_break_001",
        "economic_actor": ["session_breakout_trader", "asia_range_marker"],
        "constraint": ["session_liquidity", "range_boundary"],
        "forced_trade": "london_ny_flow_breaks_asia_box_then_follows",
        "observable_proxy": ["dist_roll_high", "dist_roll_low", "range_pct", "ret_12"],
        "predicted_effect": {
            "direction": "continuation_after_session_box_break",
            "horizon": "1h-8h",
            "conditional_on": ["asia_compressed", "volume_expansion"],
        },
        "who_pays": "fade_traders_defending_asia_high_low",
        "alternative_explanations": ["random_range_noise", "macro_headline"],
        "capacity_limit": "medium",
        "known_failure_modes": ["fake_break_into_asia_reclaim"],
        "family": "vol_squeeze_break",
        "factor_hints": ["dist_roll_high", "dist_roll_low", "range_pct", "volume_z"],
    },
    {
        "mechanism_id": "vol_squeeze_expansion_001",
        "economic_actor": ["breakout_follower", "option_hedger", "range_trader"],
        "constraint": ["realized_vol_compression", "gamma_hedging", "range_box"],
        "forced_trade": "after_vol_squeeze_expansion_forces_hedgers_and_breakout_flow_to_chase",
        "observable_proxy": [
            "squeeze_score", "expansion_score", "atr_pct_14", "range_pct", "ret_12", "volume_z",
        ],
        "predicted_effect": {
            "direction": "continuation_after_compression_break",
            "horizon": "30m-6h",
            "conditional_on": [
                "prior_atr_compression",
                "range_expansion",
                "volume_confirmation",
                "not_fake_break_in_chop",
            ],
        },
        "who_pays": "fade_traders_fighting_first_expansion_leg",
        "alternative_explanations": [
            "random_walk_after_noise",
            "session_open_artifact",
            "news_impulse_unrelated_to_squeeze",
        ],
        "capacity_limit": "medium_to_high",
        "known_failure_modes": [
            "fake_break_in_chop",
            "expansion_without_follow_through",
            "two_sided_whipsaw_after_squeeze",
        ],
        "family": "vol_squeeze_break",
        "factor_hints": [
            "squeeze_score", "expansion_score", "atr_pct_14", "range_pct",
            "ret_12", "volume_z", "dist_roll_high", "abs_ret_1",
        ],
    },
    {
        "mechanism_id": "session_open_auction_fade_001",
        "economic_actor": ["auction_participant", "overnight_gap_trader"],
        "constraint": ["open_auction_imbalance", "inventory_reset"],
        "forced_trade": "open_impulse_overshoots_then_inventory_rebalances",
        "observable_proxy": ["abs_ret_1", "range_pct", "upper_wick_pct", "lower_wick_pct"],
        "predicted_effect": {
            "direction": "fade_after_open_impulse",
            "horizon": "15m-2h",
            "conditional_on": ["large_open_range", "wick_reclaim"],
        },
        "who_pays": "chase_entrants_at_open_extreme",
        "alternative_explanations": ["true_gap_continuation", "news_open"],
        "capacity_limit": "low_to_medium",
        "known_failure_modes": ["trend_open_day"],
        "family": "mean_reversion",
        "factor_hints": ["abs_ret_1", "upper_wick_pct", "lower_wick_pct", "range_pct"],
    },
    {
        "mechanism_id": "orderflow_imbalance_proxy_001",
        "economic_actor": ["aggressive_taker", "passive_absorber"],
        "constraint": ["short_horizon_imbalance", "absorption"],
        "forced_trade": "one_sided_aggression_then_passive_absorb_or_continue",
        "observable_proxy": ["volume_z", "abs_ret_1", "range_pct", "close_z_20"],
        "predicted_effect": {
            "direction": "short_horizon_continuation_or_absorb_fade",
            "horizon": "5m-45m",
            "conditional_on": ["volume_spike", "range_expansion"],
        },
        "who_pays": "late_chasing_or_premature_fade",
        "alternative_explanations": ["pure_volatility_burst", "index_beta"],
        "capacity_limit": "low",
        "known_failure_modes": ["missing_true_l2_orderflow"],
        "family": "data_driven",
        "factor_hints": ["volume_z", "abs_ret_1", "range_pct", "close_z_20"],
    },
    {
        "mechanism_id": "overnight_gap_fade_001",
        "economic_actor": ["gap_trader", "inventory_manager"],
        "constraint": ["gap_size", "prior_close_anchor"],
        "forced_trade": "gaps_attract_fade_until_filled_or_accepted",
        "observable_proxy": ["abs_ret_1", "close_z_20", "dist_roll_high", "dist_roll_low"],
        "predicted_effect": {
            "direction": "partial_gap_fill",
            "horizon": "30m-6h",
            "conditional_on": ["gap_not_news_driven"],
        },
        "who_pays": "gap_chasers_without_confirmation",
        "alternative_explanations": ["gap_and_go_trend", "weekend_risk_repricing"],
        "capacity_limit": "medium",
        "known_failure_modes": ["structural_gap_acceptance"],
        "family": "mean_reversion",
        "factor_hints": ["abs_ret_1", "close_z_20", "dist_roll_low", "dist_roll_high"],
    },
    {
        "mechanism_id": "correlation_breakdown_001",
        "economic_actor": ["cross_asset_arb", "beta_hedger"],
        "constraint": ["correlation_band", "hedge_ratio"],
        "forced_trade": "temporary_decorrelation_forces_hedge_rebalance",
        "observable_proxy": ["ret_12", "close_z_20", "atr_pct_14", "volume_z"],
        "predicted_effect": {
            "direction": "relative_value_reversion_or_lead_lag",
            "horizon": "1h-24h",
            "conditional_on": ["elevated_idiosyncratic_vol"],
        },
        "who_pays": "naive_beta_chasers",
        "alternative_explanations": ["true_regime_decoupling", "single_name_news"],
        "capacity_limit": "medium",
        "known_failure_modes": ["permanent_correlation_shift"],
        "family": "crowding_fade",
        "factor_hints": ["ret_12", "close_z_20", "atr_pct_14"],
    },
    {
        "mechanism_id": "failed_breakout_trap_001",
        "economic_actor": ["breakout_follower", "trap_liquidity"],
        "constraint": ["break_level", "follow_through_volume"],
        "forced_trade": "break_without_follow_through_forces_unwind",
        "observable_proxy": ["dist_roll_high", "upper_wick_pct", "volume_z", "ret_3"],
        "predicted_effect": {
            "direction": "reversal_after_failed_break",
            "horizon": "15m-3h",
            "conditional_on": ["wick_reject", "volume_not_confirming"],
        },
        "who_pays": "breakout_chasers",
        "alternative_explanations": ["delayed_continuation", "news_pause"],
        "capacity_limit": "medium",
        "known_failure_modes": ["second_leg_breakout"],
        "family": "liquidity_sweep",
        "factor_hints": ["dist_roll_high", "upper_wick_pct", "volume_z", "ret_3"],
    },
    {
        "mechanism_id": "volatility_mean_reversion_001",
        "economic_actor": ["vol_seller", "option_hedger"],
        "constraint": ["realized_vol_spike", "mean_reverting_vol"],
        "forced_trade": "after_vol_spike_hedgers_reduce_gamma_chase",
        "observable_proxy": ["atr_pct_14", "range_pct", "abs_ret_1", "close_z_20"],
        "predicted_effect": {
            "direction": "range_compression_after_vol_spike",
            "horizon": "1h-12h",
            "conditional_on": ["no_new_information_impulse"],
        },
        "who_pays": "late_vol_chasers",
        "alternative_explanations": ["vol_regime_shift_up", "clustered_jumps"],
        "capacity_limit": "medium",
        "known_failure_modes": ["vol_of_vol_uptrend"],
        "family": "mean_reversion",
        "factor_hints": ["atr_pct_14", "range_pct", "abs_ret_1", "close_z_20"],
    },
    {
        "mechanism_id": "panic_exhaustion_recovery_001",
        "economic_actor": ["panic_seller", "forced_liquidator", "mean_reversion_liquidity"],
        "constraint": ["margin_stress", "inventory_shock", "stop_cascade"],
        "forced_trade": "panic_sell_exhausts_then_inventory_and_opportunistic_buyers_reprice",
        "observable_proxy": ["rsi_14", "close_z_20", "lower_wick_pct", "exhaustion_score", "volume_z"],
        "predicted_effect": {
            "direction": "short_horizon_recovery_after_exhaustion",
            "horizon": "15m-2h",
            "conditional_on": [
                "rsi_extreme_oversold",
                "deep_negative_z",
                "wick_or_engulf_confirm",
                "not_trend_continuation",
            ],
        },
        "who_pays": "late_panic_sellers_and_breakout_chasers_into_flush",
        "alternative_explanations": [
            "trend_continuation_after_pause",
            "informed_flow_still_selling",
            "news_driven_repricing",
        ],
        "capacity_limit": "low_to_medium",
        "known_failure_modes": [
            "falling_knife_in_persistent_trend",
            "multi_wave_liquidation",
            "catching_mid_impulse_without_confirmation",
        ],
        "family": "mean_reversion",
        "factor_hints": [
            "rsi_14", "close_z_20", "lower_wick_pct", "exhaustion_score",
            "volume_z", "abs_ret_1", "ret_3",
        ],
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
            "突破", "break", "压缩", "squeeze", "扩张", "收缩", "波动率",
            "清算", "liquid", "资金费", "funding",
            "扫损", "sweep", "止损", "动量", "trend",
            "衰竭", "超卖", "rsi", "回收", "恐慌", "飞刀", "exhaust",
        ):
            if token in text and token in blob:
                score += 1.5
            elif token in text:
                score += 0.2
        # boost explicit exhaustion mechanism when brief asks for it
        if any(k in text for k in ("衰竭", "超卖", "rsi", "回收", "恐慌")):
            if "exhaustion" in str(m.get("mechanism_id") or "") or "exhaustion" in blob:
                score += 3.0
            if m.get("family") == "mean_reversion":
                score += 0.8
            if m.get("family") == "trend_pullback":
                score -= 0.5  # opposing family for knife risk
        if any(k in text for k in ("压缩", "扩张", "squeeze", "波动率收缩", "收缩扩张")):
            if "squeeze" in str(m.get("mechanism_id") or "") or "squeeze" in blob or "expansion" in blob:
                score += 3.0
            if m.get("family") == "vol_squeeze_break":
                score += 1.2
            if m.get("family") == "mean_reversion":
                score -= 0.3  # opposing fade risk after true expansion
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
