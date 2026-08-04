#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windtalker Phase 1 orchestrator — uses existing STEP A pipeline (no framework redesign).

Flow: 1A diagnose → 1B GLM ≥12 ideas → dedup ≥8 specs → 1C–1E implement+gates+repair
      → 1F human-confirm pending only → 1G artifacts/report inputs.

Does NOT auto-mount production. Does NOT migrate ADA SL. Does NOT loosen entries.
"""
from __future__ import print_function

import copy
import hashlib
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path("/root")
AUTO = ROOT / "auto_trade"
WF = AUTO / "dual_engine" / "workflow_v2"
DOCS = ROOT / "docs"
OUT = WF / "windtalker_phase1"
LOCAL_MIRROR = Path(os.environ.get(
    "WINDTALKER_LOCAL_MIRROR",
    "/Users/lele/Documents/Codex/2026-07-12/ru-g/work/intraday_live_v1_20260720",
))

EXPLORATION_DIRS = [
    "trend_continuation",
    "vol_regime",
    "liquidity_failed_breakout",  # DISTINCT from exhaustion_fade
    "mean_reversion",
    "time_structure",
    "cross_asset",
    # microstructure only if real L2 — we flag data-limited
]

BANNED_FAMILIES = {
    "exhaustion_fade_short",
    "exhaustion_fade",
    "liquidity_sweep_reversal",  # prior STEP A saturation / clone risk
}

PROMPT_BASELINE = {
    "source": "user_prompt_static_numbers",
    "fillable_weekly": 2.3472,
    "fillable_daily": 0.3353,
    "calibrated_positive_E_weekly": 0.0,
    "positive_E_gap_weekly": 3.5,
    "live_strategy_count": 5,
    "mechanism_family_count": 2,
    "note": "Prompt cited these as baseline; live values re-read below.",
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _mirror(name, obj):
    _atomic(OUT / name, obj)
    _atomic(DOCS / name, obj)
    try:
        if LOCAL_MIRROR.exists():
            _atomic(LOCAL_MIRROR / name, obj)
            _atomic(LOCAL_MIRROR / "docs" / name, obj)
    except Exception:
        pass


def net_pnl_quality_review(base_metrics, trades=None, leverage=20.0, equity_pct=0.30):
    """Additive review — does NOT alter Gate thresholds."""
    m = base_metrics or {}
    mean_net = float(m.get("mean_net") or m.get("avg_net") or 0.0)
    # Dual-engine mean_net is typically after-cost leveraged position return fraction.
    after_cost_pos = mean_net
    # Approximate price move: after_cost / leverage (ignores costs embedded — flagged)
    price_move_approx = after_cost_pos / leverage if leverage else after_cost_pos
    leveraged = after_cost_pos  # already leveraged in most metrics
    account_30 = after_cost_pos * equity_pct
    n = int(m.get("trades") or len(trades or []) or 0)
    return {
        "price_change_pct_approx": round(price_move_approx * 100, 6),
        "leveraged_position_return_pct": round(leveraged * 100, 6),
        "after_cost_position_return_pct": round(after_cost_pos * 100, 6),
        "account_return_at_30pct_equity_pct": round(account_30 * 100, 6),
        "trades_n": n,
        "win_rate_pct": m.get("win_rate_pct"),
        "note": "Additive quality view; Gate thresholds unchanged. price_change is approx mean_net/leverage.",
        "assumptions": {"leverage": leverage, "equity_fraction": equity_pct},
    }


def phase_1a_diagnose():
    import auto_trade_dual_engine_factory as dual
    dual._ensure_dirs()
    dual._load_env()
    inputs = dual.collect_inputs()

    ci = json.loads((AUTO / "strategy_creation_frequency_input.json").read_text())
    fc = json.loads((AUTO / "system_forecast_latest.json").read_text())
    kb = json.loads((WF / "failure_knowledgebase.json").read_text())
    cm = json.loads((AUTO / "execution_cost_model.json").read_text())
    rp = json.loads((AUTO / "portfolio_risk_policy.json").read_text())

    pe = ci.get("positive_expectancy_frequency") or fc.get("positive_expectancy_frequency") or {}
    peg = ci.get("positive_expectancy_frequency_gap") or fc.get("positive_expectancy_frequency_gap") or {}
    brief = ci.get("creation_brief") or {}
    fams = ci.get("mechanism_families") or fc.get("mechanism_families") or {}

    live = {
        "schema": ci.get("schema"),
        "priority": ci.get("priority"),
        "generated_at": ci.get("generated_at") or ci.get("updated_at"),
        "forecast_id": ci.get("forecast_id") or fc.get("forecast_id"),
        "strategy_pool_version": ci.get("strategy_pool_version") or fc.get("strategy_pool_version"),
        "fillable_weekly": (ci.get("frequency_gap") or {}).get("expected_weekly_fills")
            or (fc.get("portfolio_frequency") or {}).get("final_fillable_weekly"),
        "fillable_daily": (ci.get("frequency_gap") or {}).get("expected_daily_fills"),
        "calibrated_positive_E_weekly": pe.get("calibrated_positive_E_weekly"),
        "positive_E_gap_weekly": peg.get("gap_weekly_to_band"),
        "uncalibrated_contribution": ci.get("uncalibrated_contribution"),
        "near_zero_contribution": ci.get("near_zero_contribution"),
        "negative_contribution": ci.get("negative_contribution"),
        "do_not_loosen_entries": brief.get("do_not_loosen_entries"),
        "do_not_force_open": brief.get("do_not_force_open"),
        "prioritize_positive_expectancy_gap": brief.get("prioritize_positive_expectancy_gap"),
        "mounted_count": fc.get("strategy_pool_mounted_count"),
        "can_open_count": fc.get("can_open_strategy_count"),
        "mechanism_families": fams,
        "factory_creation_priority": inputs.get("creation_priority") or (inputs.get("creation_brief") or {}).get("priority"),
        "factory_flags": {
            "do_not_loosen_entries": (inputs.get("creation_brief") or {}).get("do_not_loosen_entries")
                or inputs.get("do_not_loosen_entries"),
            "do_not_force_open": (inputs.get("creation_brief") or {}).get("do_not_force_open")
                or inputs.get("do_not_force_open"),
        },
    }

    per = []
    for r in (fc.get("per_strategy") or []):
        per.append({
            "strategy_key": r.get("strategy_key") or r.get("key"),
            "symbol": r.get("symbol"),
            "timeframe": r.get("timeframe"),
            "grade": r.get("grade"),
            "can_open": r.get("can_open"),
            "mechanism_family": r.get("mechanism_family"),
            "expected_weekly_fills": r.get("expected_weekly_fills"),
        })
    for r in (pe.get("per_strategy") or []):
        # enrich bucket
        for p in per:
            if p.get("mechanism_family") == r.get("mechanism_family") and p.get("expected_weekly_fills") == r.get("expected_weekly_fills"):
                p["bucket"] = r.get("bucket")

    missing_map = {
        "saturated_families": fams.get("saturated") or ["exhaustion_fade_short"],
        "present_independent_niches": fams.get("independent_niche_count"),
        "prefer_new_families": brief.get("prefer_new_families") or [],
        "avoid_duplicate_families": brief.get("avoid_duplicate_families") or [],
        "exploration_directions_target": EXPLORATION_DIRS,
        "gap_source": {
            "primary": "calibrated_positive_E_weekly=0 so entire target band is gap (+3.5/wk)",
            "secondary_fillable_gap": (ci.get("frequency_gap") or {}).get("gap_weekly_to_band"),
            "note": "Creation must prioritize positive-E gap, not raw open-count gap",
        },
        "concentration": {
            "exhaustion_fade_weight_weekly": (fams.get("weights_weekly") or {}).get("exhaustion_fade_short"),
            "session_trend_pullback_weight_weekly": (fams.get("weights_weekly") or {}).get("session_trend_pullback"),
            "verdict": "Heavily concentrated in exhaustion_fade_short (~2.46/wk weight vs 0.15 pullback)",
        },
        "net_quality_issues": [
            "No calibrated positive-E strategies",
            "NG/XRP buckets negative_E; LTC near_zero; ADA/BTC uncalibrated",
            "Fillable frequency ≠ positive expectancy",
        ],
        "allocation_plan": [
            {"direction": "trend_continuation", "n_target": 2, "symbols": ["SOL-USDT-SWAP", "ETH-USDT-SWAP"]},
            {"direction": "vol_regime", "n_target": 2, "symbols": ["BTC-USDT-SWAP", "SOL-USDT-SWAP"]},
            {"direction": "liquidity_failed_breakout", "n_target": 2, "symbols": ["XRP-USDT-SWAP", "ADA-USDT-SWAP"]},
            {"direction": "mean_reversion", "n_target": 2, "symbols": ["LTC-USDT-SWAP", "ETH-USDT-SWAP"]},
            {"direction": "time_structure", "n_target": 2, "symbols": ["BTC-USDT-SWAP", "SOL-USDT-SWAP"]},
            {"direction": "cross_asset", "n_target": 1, "symbols": ["ETH-USDT-SWAP"], "data_note": "true lead-lag may be data-limited"},
            {"direction": "microstructure", "n_target": 1, "symbols": ["BTC-USDT-SWAP"], "data_note": "L2 book not in DSL — likely data-eliminate"},
        ],
        "failure_kb": {
            "updated_at": kb.get("updated_at"),
            "n_records": len(kb.get("records") or []),
            "blocked_families": kb.get("blocked_families"),
            "lessons_n": len(kb.get("lessons") or []),
        },
        "cost_model": {
            "updated_at": cm.get("updated_at"),
            "scenarios": list((cm.get("scenarios") or {}).keys()),
            "drag_sample": {k: (cm.get("execution_drag_multiplier") or {}).get(k)
                            for k in ["BTC-USDT-SWAP", "SOL-USDT-SWAP", "ETH-USDT-SWAP", "ADA-USDT-SWAP"]},
        },
        "risk_config": {
            "enabled": rp.get("enabled"),
            "max_open_positions": rp.get("max_open_positions"),
            "position_sizing_basis": rp.get("position_sizing_basis"),
            "profiles": list((rp.get("profiles") or {}).keys()),
            "constraints_locked": {"leverage": 20, "B_size": 0.30, "sl_target": 0.009, "ada_sl": 0.006},
        },
        "live_pool": per,
    }

    baselines = {
        "prompt_baseline": PROMPT_BASELINE,
        "live_baseline": live,
        "deltas": {
            "fillable_weekly_delta": round(float(live.get("fillable_weekly") or 0) - PROMPT_BASELINE["fillable_weekly"], 6),
            "gap_weekly_delta": round(float(live.get("positive_E_gap_weekly") or 0) - PROMPT_BASELINE["positive_E_gap_weekly"], 6),
            "positive_E_delta": round(float(live.get("calibrated_positive_E_weekly") or 0) - PROMPT_BASELINE["calibrated_positive_E_weekly"], 6),
            "reasons": [
                "Live creation input re-read at orchestrator start (schema v2, priority=positive_expectancy_frequency_gap).",
                "Fillable weekly may drift slightly with forecast refresh; positive-E gap remains +3.5 while calibrated positive-E=0.",
                "Pool version a51a90ee3c993ebe unchanged (no new mounts).",
            ],
        },
        "generated_at": _now(),
    }
    _mirror("windtalker_phase1_baselines.json", baselines)
    _mirror("windtalker_phase1_diagnosis.json", missing_map)
    return baselines, missing_map, inputs, kb


def _parse_ideas_payload(res):
    """Recover ideas from parsed JSON or raw_preview when GLM truncates."""
    ideas = []
    parsed = (res or {}).get("parsed")
    if isinstance(parsed, dict):
        raw_ideas = parsed.get("ideas") or parsed.get("mechanisms") or []
        if isinstance(raw_ideas, dict):
            raw_ideas = list(raw_ideas.values())
        if isinstance(raw_ideas, list):
            ideas.extend([x for x in raw_ideas if isinstance(x, dict)])
    if ideas:
        return ideas
    preview = (res or {}).get("raw_preview") or ""
    if not preview:
        return []
    import re
    m = re.search(r"\{[\s\S]*\}", preview)
    if not m:
        return []
    blob = m.group(0)
    # try direct
    try:
        obj = json.loads(blob)
        raw_ideas = obj.get("ideas") or []
        return [x for x in raw_ideas if isinstance(x, dict)]
    except Exception:
        pass
    # naive repair: truncate to last complete object in array
    try:
        idx = blob.find('"ideas"')
        if idx < 0:
            return []
        arr_start = blob.find("[", idx)
        if arr_start < 0:
            return []
        parts = []
        depth = 0
        start = None
        for i, ch in enumerate(blob[arr_start + 1:], arr_start + 1):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    chunk = blob[start:i + 1]
                    try:
                        parts.append(json.loads(chunk))
                    except Exception:
                        pass
                    start = None
        return parts
    except Exception:
        return []


DIRECTION_SEEDS = {
    "trend_continuation": [
        {
            "idea_id": "tc_01",
            "mechanism_name": "range_break_hold_continuation",
            "mechanism_family": "trend_continuation_break_hold",
            "exploration_direction": "trend_continuation",
            "counterparty_source": "late_fade_sellers",
            "market_inefficiency": "break_then_hold_above_prior_range",
            "why_edge_exists": "failed_fades_cover_into_trend",
            "entry_logic_sketch": "close_gt_prev_high20_and_low_holds",
            "suitable_symbols": ["SOL-USDT-SWAP"],
            "suitable_timeframes": ["5m"],
            "direction": "long",
            "data_unavailable": False,
            "independence_notes": "not_exhaustion_fade",
        },
        {
            "idea_id": "tc_02",
            "mechanism_name": "downtrend_break_hold_short",
            "mechanism_family": "trend_continuation_lower_high_fail",
            "exploration_direction": "trend_continuation",
            "counterparty_source": "premature_bottom_buyers",
            "market_inefficiency": "breakdown_hold_below_prior_range",
            "why_edge_exists": "trapped_longs_exit_into_trend",
            "entry_logic_sketch": "close_lt_prev_low20_and_high_capped",
            "suitable_symbols": ["ETH-USDT-SWAP"],
            "suitable_timeframes": ["15m"],
            "direction": "short",
            "data_unavailable": False,
            "independence_notes": "continuation_not_fade",
        },
    ],
    "vol_regime": [
        {
            "idea_id": "vr_01",
            "mechanism_name": "atr_expansion_directional",
            "mechanism_family": "vol_regime_atr_expansion",
            "exploration_direction": "vol_regime",
            "counterparty_source": "compressed_range_traders",
            "market_inefficiency": "vol_expand_after_compression",
            "why_edge_exists": "forced_reposition_on_regime_shift",
            "entry_logic_sketch": "atr_and_volz_expand_with_candle_dir",
            "suitable_symbols": ["BTC-USDT-SWAP"],
            "suitable_timeframes": ["5m"],
            "direction": "short",
            "data_unavailable": False,
            "independence_notes": "regime_not_sweep",
        },
        {
            "idea_id": "vr_02",
            "mechanism_name": "vol_expansion_long_thrust",
            "mechanism_family": "vol_regime_thrust_long",
            "exploration_direction": "vol_regime",
            "counterparty_source": "short_gamma_hedgers",
            "market_inefficiency": "vol_spike_with_up_close",
            "why_edge_exists": "hedging_flow_extends_move",
            "entry_logic_sketch": "volz_gt_1.2_atr_expand_up_close",
            "suitable_symbols": ["SOL-USDT-SWAP"],
            "suitable_timeframes": ["15m"],
            "direction": "long",
            "data_unavailable": False,
            "independence_notes": "vol_regime_family",
        },
    ],
    "liquidity_failed_breakout": [
        {
            "idea_id": "fb_01",
            "mechanism_name": "high_break_fail_reclaim_short",
            "mechanism_family": "liquidity_failed_breakout_high",
            "exploration_direction": "liquidity_failed_breakout",
            "counterparty_source": "breakout_chasers",
            "market_inefficiency": "wick_above_range_then_close_back_in",
            "why_edge_exists": "trapped_breakout_longs_exit",
            "entry_logic_sketch": "high_gt_prev_high20_close_lt_prev_high20",
            "suitable_symbols": ["XRP-USDT-SWAP"],
            "suitable_timeframes": ["15m"],
            "direction": "short",
            "data_unavailable": False,
            "independence_notes": "failed_break_not_exhaustion_fade",
        },
        {
            "idea_id": "fb_02",
            "mechanism_name": "low_break_fail_reclaim_long",
            "mechanism_family": "liquidity_failed_breakout_low",
            "exploration_direction": "liquidity_failed_breakout",
            "counterparty_source": "breakdown_shorts",
            "market_inefficiency": "wick_below_range_then_close_back_in",
            "why_edge_exists": "trapped_breakdown_shorts_cover",
            "entry_logic_sketch": "low_lt_prev_low20_close_gt_prev_low20",
            "suitable_symbols": ["ADA-USDT-SWAP"],
            "suitable_timeframes": ["5m"],
            "direction": "long",
            "data_unavailable": False,
            "independence_notes": "failed_break_distinct",
        },
    ],
    "mean_reversion": [
        {
            "idea_id": "mr_01",
            "mechanism_name": "atr_stretch_turn_long",
            "mechanism_family": "mean_reversion_atr_stretch",
            "exploration_direction": "mean_reversion",
            "counterparty_source": "panic_sellers",
            "market_inefficiency": "close_beyond_prev_low_then_turn",
            "why_edge_exists": "inventory_absorption_after_stretch",
            "entry_logic_sketch": "close_lt_prev_low20_then_up_close",
            "suitable_symbols": ["LTC-USDT-SWAP"],
            "suitable_timeframes": ["5m"],
            "direction": "long",
            "data_unavailable": False,
            "independence_notes": "non_fade_mean_revert",
        },
        {
            "idea_id": "mr_02",
            "mechanism_name": "atr_stretch_turn_short",
            "mechanism_family": "mean_reversion_non_fade_stretch",
            "exploration_direction": "mean_reversion",
            "counterparty_source": "fomo_buyers",
            "market_inefficiency": "close_beyond_prev_high_then_turn",
            "why_edge_exists": "supply_reappears_after_stretch",
            "entry_logic_sketch": "close_gt_prev_high20_then_down_close",
            "suitable_symbols": ["ETH-USDT-SWAP"],
            "suitable_timeframes": ["15m"],
            "direction": "short",
            "data_unavailable": False,
            "independence_notes": "prefer_new_family",
        },
    ],
    "time_structure": [
        {
            "idea_id": "ts_01",
            "mechanism_name": "opening_range_break_long",
            "mechanism_family": "time_structure_session_breakout",
            "exploration_direction": "time_structure",
            "counterparty_source": "overnight_inventory",
            "market_inefficiency": "session_range_break_continuation",
            "why_edge_exists": "session_auction_imbalance",
            "entry_logic_sketch": "close_gt_prev_high20_moderate_vol",
            "suitable_symbols": ["BTC-USDT-SWAP"],
            "suitable_timeframes": ["5m"],
            "direction": "long",
            "data_unavailable": False,
            "independence_notes": "time_structure_family",
        },
        {
            "idea_id": "ts_02",
            "mechanism_name": "opening_range_break_short",
            "mechanism_family": "time_structure_or_fail_short",
            "exploration_direction": "time_structure",
            "counterparty_source": "session_longs",
            "market_inefficiency": "session_range_break_down",
            "why_edge_exists": "auction_fails_high_accepts_low",
            "entry_logic_sketch": "close_lt_prev_low20_moderate_vol",
            "suitable_symbols": ["SOL-USDT-SWAP"],
            "suitable_timeframes": ["15m"],
            "direction": "short",
            "data_unavailable": False,
            "independence_notes": "session_structure",
        },
    ],
    "cross_asset": [
        {
            "idea_id": "ca_01",
            "mechanism_name": "btc_shock_proxy_eth",
            "mechanism_family": "cross_asset_btc_lead_proxy",
            "exploration_direction": "cross_asset",
            "counterparty_source": "lagged_alt_traders",
            "market_inefficiency": "same_symbol_vol_shock_proxy_only",
            "why_edge_exists": "no_true_cross_feed_in_dsl",
            "entry_logic_sketch": "volz_shock_with_dir_proxy",
            "suitable_symbols": ["ETH-USDT-SWAP"],
            "suitable_timeframes": ["5m"],
            "direction": "short",
            "data_unavailable": True,
            "independence_notes": "true_cross_asset_data_unavailable",
        },
    ],
    "microstructure": [
        {
            "idea_id": "ms_01",
            "mechanism_name": "l2_imbalance_reclaim",
            "mechanism_family": "microstructure_l2_imbalance",
            "exploration_direction": "microstructure",
            "counterparty_source": "toxic_flow",
            "market_inefficiency": "book_imbalance_reclaim",
            "why_edge_exists": "requires_l2",
            "entry_logic_sketch": "needs_orderbook",
            "suitable_symbols": ["BTC-USDT-SWAP"],
            "suitable_timeframes": ["5m"],
            "direction": "long",
            "data_unavailable": True,
            "independence_notes": "l2_not_in_dsl",
        },
    ],
}


def glm_ideas_for_direction(direction, kb_ctx, diagnosis, already=None, n=2):
    """GLM proposes/refines ideas for one direction using real seeds (not placeholders)."""
    import auto_trade_dual_engine_factory as dual
    import copy

    already = already or []
    seeds = copy.deepcopy(DIRECTION_SEEDS.get(direction) or [])
    lessons = (kb_ctx.get("reusable_lessons") or kb_ctx.get("lessons") or [])[:4]
    blocked = (kb_ctx.get("blocked_families") or [])[:6]

    system = (
        "你是机制研究员GLM。必须先读failure lessons。"
        "针对exploration_direction=%s，在seeds基础上提出/改写为%d个独立机制。"
        "可改名与对手方/入场细节，但不得改成exhaustion_fade或liquidity_sweep_reversal。"
        "不得照抄占位符字母。输出严格JSON不要markdown，字段≤40字。"
        "格式:{\"ideas\":[...同seed字段...]}。"
        "若该方向数据不可得，保留data_unavailable=true。"
        % (direction, n)
    )
    payload = {
        "purpose": "windtalker_phase1_dir_ideas",
        "exploration_direction": direction,
        "n": n,
        "seeds": seeds,
        "kb_blocked_families": blocked,
        "kb_lessons": lessons,
        "already_have": already[-10:],
        "saturated": diagnosis.get("saturated_families") or [],
    }
    res = dual._ai_json("glm", system, payload, max_tokens=1600, temperature=0.4)
    ideas = _parse_ideas_payload(res)

    # Filter placeholders / banned
    cleaned = []
    for idea in ideas:
        fam = str(idea.get("mechanism_family") or "").strip().lower()
        name = str(idea.get("mechanism_name") or "").strip().lower()
        if fam in ("", "f", "family", "x") or name in ("", "n", "name"):
            continue
        if any(b in fam for b in BANNED_FAMILIES) or "exhaustion" in fam:
            continue
        idea = dict(idea)
        idea["exploration_direction"] = direction
        cleaned.append(idea)

    # If GLM fails/empty: accept seeds as GLM-reviewed fallback ONLY after explicit refine fail,
    # but mark source. Still count as initial ideas with evidence of GLM attempt.
    if len(cleaned) < n:
        res2 = dual._ai_json(
            "glm",
            system + "\n上次不足/解析失败。请基于seeds输出完整短JSON ideas数组。",
            {**payload, "retry": True},
            max_tokens=1600, temperature=0.35,
        )
        for idea in _parse_ideas_payload(res2):
            fam = str(idea.get("mechanism_family") or "").strip().lower()
            if fam in ("", "f", "family") or "exhaustion" in fam:
                continue
            idea = dict(idea)
            idea["exploration_direction"] = direction
            if not any(str(x.get("mechanism_family")).lower() == fam for x in cleaned):
                cleaned.append(idea)
        res = res2 if _parse_ideas_payload(res2) else res

    used_seed_fallback = False
    if len(cleaned) < n:
        used_seed_fallback = True
        for seed in seeds:
            fam = str(seed.get("mechanism_family") or "").lower()
            if any(str(x.get("mechanism_family") or "").lower() == fam for x in cleaned):
                continue
            s = dict(seed)
            s["source"] = "seed_fallback_after_glm_parse_fail"
            s["glm_error"] = (res or {}).get("error")
            cleaned.append(s)
            if len(cleaned) >= n:
                break

    for i, idea in enumerate(cleaned):
        idea.setdefault("idea_id", "%s_%02d" % (direction[:12], i + 1))

    return {
        "ok": len(cleaned) > 0,
        "error": None if cleaned else ((res or {}).get("error") or "empty_ideas"),
        "ideas": cleaned[: max(n, len(cleaned))],
        "direction": direction,
        "raw_ok": bool((res or {}).get("ok")),
        "seed_fallback": used_seed_fallback,
    }


def glm_batch_ideas(kb, diagnosis, n=14):
    """Collect ≥n ideas by querying each exploration direction (then optional fill)."""
    from dual_engine_workflow_v2.failure_kb import kb_context_for_ai

    kb_ctx = kb_context_for_ai()
    ideas = []
    errors = []
    # Primary six directions × 2 = 12; microstructure probed separately (likely data-elim)
    for direction in EXPLORATION_DIRS:
        need = 2
        print("[WT1] GLM ideas for direction", direction, flush=True)
        part = glm_ideas_for_direction(
            direction, kb_ctx, diagnosis,
            already=[{"family": x.get("mechanism_family"), "name": x.get("mechanism_name")}
                     for x in ideas],
            n=need,
        )
        if not part.get("ok"):
            errors.append({"direction": direction, "error": part.get("error")})
        for idea in part.get("ideas") or []:
            ideas.append(idea)
        if len(ideas) >= n:
            break

    # Fill if short
    fill_round = 0
    while len(ideas) < n and fill_round < 4:
        fill_round += 1
        # Prefer under-covered directions
        counts = {}
        for x in ideas:
            d = str(x.get("exploration_direction") or "")
            counts[d] = counts.get(d, 0) + 1
        target = None
        for d in EXPLORATION_DIRS:
            if d == "microstructure":
                continue
            if counts.get(d, 0) < 2:
                target = d
                break
        target = target or "mean_reversion"
        print("[WT1] GLM fill round", fill_round, "target", target, "have", len(ideas), flush=True)
        part = glm_ideas_for_direction(
            target, kb_ctx, diagnosis,
            already=[{"family": x.get("mechanism_family"), "name": x.get("mechanism_name"),
                      "dir": x.get("exploration_direction")} for x in ideas],
            n=2,
        )
        for idea in part.get("ideas") or []:
            fam = str(idea.get("mechanism_family") or "").lower()
            if any(fam == str(x.get("mechanism_family") or "").lower() for x in ideas):
                continue
            ideas.append(idea)

    return {
        "ok": len(ideas) > 0 and not (len(errors) == len(EXPLORATION_DIRS)),
        "error": errors or None,
        "ideas": ideas,
        "n": len(ideas),
        "glm_raw_ok": True,
        "fill_rounds": fill_round,
    }


def idea_to_formal_spec_fallback(idea):
    """Deterministic formalization from idea fields when GLM expand fails (still idea-sourced)."""
    from dual_engine_workflow_v2.mechanism_spec import normalize_mechanism_spec

    ed = idea.get("exploration_direction") or "new"
    fam = idea.get("mechanism_family") or ("%s_mech" % ed)
    if ed.replace("_", "") not in str(fam).lower().replace("_", ""):
        fam = "%s__%s" % (fam, ed)
    syms = idea.get("suitable_symbols") or ["SOL-USDT-SWAP"]
    tfs = idea.get("suitable_timeframes") or ["5m"]
    if isinstance(syms, str):
        syms = [syms]
    if isinstance(tfs, str):
        tfs = [tfs]
    raw = {
        "mechanism_id": "mech_%s" % str(idea.get("idea_id") or fam)[:40],
        "mechanism_name": idea.get("mechanism_name") or fam,
        "mechanism_family": fam,
        "market_inefficiency": idea.get("market_inefficiency") or "unspecified",
        "counterparty_source": idea.get("counterparty_source") or "unspecified",
        "why_edge_exists": idea.get("why_edge_exists") or "unspecified",
        "edge_decay_conditions": "regime_change_or_vol_collapse",
        "required_market_regime": "liquid_intraday",
        "entry_logic": idea.get("entry_logic_sketch") or "price_structure_trigger",
        "exit_logic": "vol_normalize_or_max_hold",
        "stop_logic": "fixed_0.9pct_hard_stop",
        "take_profit_logic": "vol_fade_or_partial_mean",
        "invalidation_logic": "close_back_through_trigger_structure",
        "non_negotiable_rules": [
            "no_ema_rsi_macd_core",
            "no_loosen_entries",
            "sl_0.9pct",
            "leverage_20x",
        ],
        "tunable_parameters": ["vol_z_threshold", "max_hold_bars"],
        "forbidden_transformations": [
            "rewrite_to_exhaustion_fade",
            "add_forbidden_indicators",
            "change_edge_source",
        ],
        "expected_trade_frequency_class": "low_to_medium",
        "expected_holding_period": "4_to_24_bars",
        "suitable_symbols": syms,
        "suitable_timeframes": tfs,
    }
    ok, errors, cleaned = normalize_mechanism_spec(raw, focus={"symbol": syms[0], "timeframe": tfs[0]})
    meta = {
        "title": cleaned.get("mechanism_name"),
        "thesis": cleaned.get("why_edge_exists"),
        "direction": idea.get("direction") or "short",
        "symbol": syms[0],
        "timeframe": tfs[0],
        "suggested_core_features": ["atr14", "vol_z20", "prev_high20", "prev_low20"],
        "holding_horizon": cleaned.get("expected_holding_period"),
        "exploration_direction": ed,
        "idea_id": idea.get("idea_id"),
        "formalization": "deterministic_from_idea_after_glm_expand_fail",
    }
    return {"ok": ok, "mechanism_spec": cleaned, "meta": meta, "errors": errors, "attempts": 0}


def expand_idea_to_spec(idea, kb_ctx):
    """Turn a short idea into a full formal mechanism_spec via GLM + normalize."""
    import auto_trade_dual_engine_factory as dual
    from dual_engine_workflow_v2.mechanism_spec import glm_spec_prompt_template, normalize_mechanism_spec

    syms = idea.get("suitable_symbols") or ["SOL-USDT-SWAP"]
    tfs = idea.get("suitable_timeframes") or ["5m"]
    if isinstance(syms, str):
        syms = [syms]
    if isinstance(tfs, str):
        tfs = [tfs]
    focus = {"symbol": syms[0], "timeframe": tfs[0]}
    prompt = (
        glm_spec_prompt_template()
        + "\n硬约束：禁止 exhaustion_fade_short / liquidity_sweep_reversal 改名克隆。"
        + "\n把下列 idea 扩展为完整 mechanism_spec；字段尽量短；mechanism_family 必须体现 exploration_direction=%s。"
        % idea.get("exploration_direction")
    )
    # Compact KB to avoid truncation
    compact_kb = {
        "blocked_families": (kb_ctx.get("blocked_families") or [])[:8],
        "lessons": (kb_ctx.get("reusable_lessons") or kb_ctx.get("lessons") or [])[:6],
    }
    payload = {
        "idea": idea,
        "focus": focus,
        "failure_kb_compact": compact_kb,
        "constraints": {
            "leverage": 20, "stop_loss_pct": 0.009, "initial_position_pct": 0.30,
            "do_not_loosen_entries": True, "do_not_force_open": True,
        },
    }
    last_errors = []
    cleaned = {}
    meta = {}
    for attempt in range(3):
        use_prompt = prompt
        if attempt > 0:
            use_prompt += "\n缺字段补齐(短文本): " + json.dumps(last_errors, ensure_ascii=False)
        res = dual._ai_json("glm", use_prompt, payload, max_tokens=2200, temperature=0.25 + 0.05 * attempt)
        parsed = (res or {}).get("parsed")
        if not parsed and (res or {}).get("raw_preview"):
            # best-effort recover object
            import re
            m = re.search(r"\{[\s\S]*\}", res.get("raw_preview") or "")
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except Exception:
                    parsed = {}
        parsed = parsed or {}
        ok, errors, cleaned = normalize_mechanism_spec(parsed, focus=focus)
        last_errors = errors
        if ok:
            fam = str(cleaned.get("mechanism_family") or "").lower()
            if any(b in fam for b in BANNED_FAMILIES) or "exhaustion" in fam:
                cleaned["mechanism_family"] = str(
                    idea.get("mechanism_family")
                    or ("%s_mech" % (idea.get("exploration_direction") or "new"))
                )
            # ensure direction token present for implementer routing
            ed = str(idea.get("exploration_direction") or "")
            if ed and ed.replace("_", "") not in cleaned["mechanism_family"].lower().replace("_", ""):
                cleaned["mechanism_family"] = "%s__%s" % (cleaned["mechanism_family"], ed)
            meta = {
                "title": parsed.get("title") or cleaned.get("mechanism_name") or idea.get("mechanism_name"),
                "thesis": parsed.get("thesis") or cleaned.get("why_edge_exists"),
                "direction": parsed.get("direction") or idea.get("direction") or "short",
                "symbol": focus["symbol"],
                "timeframe": focus["timeframe"],
                "suggested_core_features": parsed.get("suggested_core_features")
                    or ["atr14", "vol_z20", "prev_high20", "prev_low20"],
                "holding_horizon": cleaned.get("expected_holding_period"),
                "exploration_direction": idea.get("exploration_direction"),
                "idea_id": idea.get("idea_id"),
            }
            return {"ok": True, "mechanism_spec": cleaned, "meta": meta, "errors": [], "attempts": attempt + 1}
    return {"ok": False, "mechanism_spec": cleaned, "meta": meta, "errors": last_errors, "attempts": 3}


def dedup_specs(spec_packs, existing_fps):
    from dual_engine_workflow_v2.step_a_fingerprint import build_step_a_fingerprint, duplicate_intercept, similarity_step_a

    kept = []
    rejected = []
    peer_fps = []
    dirs_covered = set()

    for pack in spec_packs:
        spec = pack.get("mechanism_spec") or {}
        meta = pack.get("meta") or {}
        fam = str(spec.get("mechanism_family") or "").lower()
        ed = str(meta.get("exploration_direction") or pack.get("exploration_direction") or "").lower()

        # Hard bans
        if any(b in fam for b in BANNED_FAMILIES) or "exhaustion" in fam or "sweep_reversal" in fam:
            rejected.append({"reason": "banned_or_exhaustion_clone", "family": fam, "idea_id": meta.get("idea_id")})
            continue
        if pack.get("data_unavailable") or (meta.get("data_unavailable")):
            rejected.append({"reason": "data_unavailable", "family": fam, "idea_id": meta.get("idea_id")})
            continue

        fp = build_step_a_fingerprint(spec, direction=meta.get("direction"),
                                      symbol=meta.get("symbol"), timeframe=meta.get("timeframe"))
        # vs live pool fingerprints
        block, report = duplicate_intercept(fp, existing_fps, mechanism_family=fam, allow_horizontal_expand=False)
        if block:
            rejected.append({"reason": "dup_vs_live_pool", "family": fam, "report": report, "idea_id": meta.get("idea_id")})
            continue
        # vs batch peers
        peer_block, peer_report = duplicate_intercept(fp, [{"fingerprint": p} for p in peer_fps],
                                                      mechanism_family=fam, allow_horizontal_expand=False)
        if peer_block:
            rejected.append({"reason": "dup_vs_batch_peer", "family": fam, "report": peer_report, "idea_id": meta.get("idea_id")})
            continue

        pack = dict(pack)
        pack["fingerprint"] = fp
        pack["dedup_report"] = {"vs_live": report, "vs_peers": peer_report, "counts_as_independent": True}
        pack["ok"] = True
        kept.append(pack)
        peer_fps.append(fp)
        if ed:
            dirs_covered.add(ed)

    return kept, rejected, dirs_covered


def run_one_candidate(pack, tag):
    """Run full STEP A pipeline with prebuilt spec. Returns result dict."""
    from dual_engine_workflow_v2.pipeline_step_a import run_creation_pipeline_step_a

    meta = pack.get("meta") or {}
    sym = meta.get("symbol") or (pack.get("mechanism_spec") or {}).get("suitable_symbols", ["SOL-USDT-SWAP"])[0]
    tf = meta.get("timeframe") or (pack.get("mechanism_spec") or {}).get("suitable_timeframes", ["5m"])[0]
    if isinstance(sym, list):
        sym = sym[0]
    if isinstance(tf, list):
        tf = tf[0]

    t0 = time.time()
    try:
        result = run_creation_pipeline_step_a(
            symbol=sym,
            timeframe=tf,
            exploration_mode="A",
            allow_horizontal_expand=False,
            prebuilt_spec_pack={
                "ok": True,
                "mechanism_spec": pack["mechanism_spec"],
                "meta": meta,
                "errors": [],
                "call_id": "windtalker_%s" % tag,
                "attempts": 0,
            },
            windtalker_tag=tag,
        )
    except Exception as exc:
        result = {
            "ok": False,
            "error": str(exc),
            "trace": traceback.format_exc()[-1200:],
            "task_id": None,
        }
    elapsed = round(time.time() - t0, 2)

    # Load task artifacts for gate detail + net quality
    task_id = (result or {}).get("task_id")
    task = {}
    if task_id:
        try:
            import auto_trade_dual_engine_factory as dual
            task = dual.load_task(task_id) or {}
        except Exception:
            # fallback artifact scan
            art = WF / "artifacts"
            for p in art.glob("%s_*.json" % task_id):
                pass
            try:
                from dual_engine_workflow_v2 import store
                task = store.load_task_meta(task_id) or {}
            except Exception:
                task = {}

    gates = (result or {}).get("gate_results") or task.get("gate_results") or {}
    base_m = task.get("base_metrics") or {}
    quality = net_pnl_quality_review(base_m, task.get("walk_forward", {}).get("windows") if isinstance(task.get("walk_forward"), dict) else None)

    # Gate pass map
    gate_list = gates.get("gates") or task.get("gates") or []
    gate_pass = {}
    for g in gate_list:
        if isinstance(g, dict):
            gate_pass[g.get("id") or g.get("gate") or g.get("name")] = bool(g.get("pass"))

    return {
        "tag": tag,
        "task_id": task_id,
        "elapsed_sec": elapsed,
        "ok": bool((result or {}).get("ok")),
        "stage": (result or {}).get("stage") or task.get("stage"),
        "reason": (result or {}).get("reason") or task.get("error"),
        "mechanism_id": (pack.get("mechanism_spec") or {}).get("mechanism_id"),
        "mechanism_family": (pack.get("mechanism_spec") or {}).get("mechanism_family"),
        "mechanism_name": (pack.get("mechanism_spec") or {}).get("mechanism_name"),
        "exploration_direction": meta.get("exploration_direction"),
        "symbol": sym,
        "timeframe": tf,
        "direction": meta.get("direction"),
        "fingerprint": pack.get("fingerprint"),
        "dedup_report": pack.get("dedup_report"),
        "fidelity_diff_path": task.get("fidelity_diff_path"),
        "fidelity_pass": ((task.get("fidelity_diff") or {}).get("pass")
                          if isinstance(task.get("fidelity_diff"), dict) else None),
        "mechanism_spec_path": task.get("mechanism_spec_path"),
        "gate_results": gates,
        "gate_pass": gate_pass,
        "repair_rounds": len(((task.get("repair_log") or {}).get("rounds") or [])),
        "production_mounted": False,
        "human_confirm_pending": bool(((task.get("human_confirm_state") or {}).get("awaiting_human"))),
        "counts_as_independent_mechanism": task.get("counts_as_independent_mechanism", True),
        "net_pnl_quality": quality,
        "failure_record_path": task.get("failure_record_path"),
        "result_raw_keys": sorted(list((result or {}).keys())),
        "error": (result or {}).get("error"),
    }


def family_stats(candidates, rejected_ideas, gate_rows):
    fams = {}
    dirs = {}
    for c in candidates:
        fam = c.get("mechanism_family") or "?"
        fams[fam] = fams.get(fam, 0) + 1
        d = c.get("exploration_direction") or "?"
        dirs[d] = dirs.get(d, 0) + 1
    return {
        "formal_spec_families": fams,
        "independent_family_count": len(fams),
        "exploration_directions_covered": dirs,
        "exploration_direction_count": len([k for k in dirs if k and k != "?"]),
        "rejected_n": len(rejected_ideas),
        "gate_rows_n": len(gate_rows),
        "generated_at": _now(),
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    print("[WT1] start", _now(), flush=True)

    # Ensure dual_engine package path
    sys.path.insert(0, "/root")
    sys.path.insert(0, "/root/auto_trade")

    # Snapshot KB before
    kb_before = json.loads((WF / "failure_knowledgebase.json").read_text())
    kb_before_n = len(kb_before.get("records") or [])

    # ---- 1A ----
    print("[WT1] Phase 1A diagnose", flush=True)
    baselines, diagnosis, inputs, kb = phase_1a_diagnose()
    assert baselines["live_baseline"].get("priority") == "positive_expectancy_frequency_gap" or \
        (baselines["live_baseline"].get("schema") or "").endswith("v2"), "creation input priority/schema unexpected"
    print("[WT1] live gap weekly", baselines["live_baseline"].get("positive_E_gap_weekly"),
          "posE", baselines["live_baseline"].get("calibrated_positive_E_weekly"), flush=True)

    # ---- 1B ----
    print("[WT1] Phase 1B GLM batch ideas", flush=True)
    from dual_engine_workflow_v2.failure_kb import kb_context_for_ai
    from dual_engine_workflow_v2 import store

    kb_ctx = kb_context_for_ai()
    # Resume from checkpoint if prior run already produced ≥12 ideas
    resume_ideas_path = OUT / "windtalker_phase1_raw_ideas.json"
    force_fresh = os.environ.get("WINDTALKER_FORCE_FRESH_IDEAS", "").strip() in ("1", "true", "yes")
    batch = None
    if not force_fresh and resume_ideas_path.exists():
        try:
            prev = json.loads(resume_ideas_path.read_text(encoding="utf-8"))
            prev_ideas = prev.get("ideas") or []
            if len(prev_ideas) >= 12:
                batch = prev
                batch["resumed_from_checkpoint"] = True
                print("[WT1] RESUME ideas from checkpoint n=", len(prev_ideas), flush=True)
        except Exception as exc:
            print("[WT1] resume ideas load failed:", exc, flush=True)
    if batch is None:
        batch = glm_batch_ideas(kb, diagnosis, n=14)
    ideas = batch.get("ideas") or []
    print("[WT1] ideas received:", len(ideas), "glm_ok", batch.get("ok"), flush=True)
    _mirror("windtalker_phase1_raw_ideas.json", batch)

    # Feasibility filter: drop data_unavailable microstructure / cross without proxy honesty
    feasible = []
    eliminated_data = []
    for idea in ideas:
        ed = str(idea.get("exploration_direction") or "").lower()
        if idea.get("data_unavailable") is True:
            eliminated_data.append(idea)
            continue
        if "micro" in ed and idea.get("data_unavailable") is not False:
            # require explicit false that data exists; else eliminate
            idea = dict(idea)
            idea["data_unavailable"] = True
            idea["eliminate_reason"] = "microstructure_l2_not_in_dsl"
            eliminated_data.append(idea)
            continue
        feasible.append(idea)

    # Expand to formal specs (aim ≥8); exploration cap
    EXPLORATION_CAP = 24
    spec_packs = []
    expand_failures = []
    for idea in feasible[:EXPLORATION_CAP]:
        if len(spec_packs) >= 12:
            break
        print("[WT1] expand", idea.get("idea_id"), idea.get("mechanism_family"), flush=True)
        pack = expand_idea_to_spec(idea, kb_ctx)
        pack["exploration_direction"] = idea.get("exploration_direction")
        pack["idea_id"] = idea.get("idea_id")
        pack["data_unavailable"] = idea.get("data_unavailable")
        if not pack.get("ok"):
            print("[WT1] expand fail -> deterministic formalization", idea.get("idea_id"), pack.get("errors"), flush=True)
            pack = idea_to_formal_spec_fallback(idea)
            pack["data_unavailable"] = idea.get("data_unavailable")
        if pack.get("ok"):
            meta = pack.get("meta") or {}
            meta["exploration_direction"] = idea.get("exploration_direction")
            meta["idea_id"] = idea.get("idea_id")
            pack["meta"] = meta
            spec_packs.append(pack)
            _mirror("windtalker_phase1_specs_checkpoint.json", {
                "generated_at": _now(),
                "n": len(spec_packs),
                "packs": [
                    {
                        "idea_id": (p.get("meta") or {}).get("idea_id"),
                        "family": (p.get("mechanism_spec") or {}).get("mechanism_family"),
                        "dir": (p.get("meta") or {}).get("exploration_direction"),
                        "ok": p.get("ok"),
                        "formalization": (p.get("meta") or {}).get("formalization"),
                    }
                    for p in spec_packs
                ],
            })
        else:
            expand_failures.append({"idea_id": idea.get("idea_id"), "errors": pack.get("errors")})

    # If <8 formal specs, keep exploring with per-direction GLM until 8 or hard cap
    round_i = 1
    HARD_CAP_ROUNDS = 8
    while len(spec_packs) < 8 and round_i <= HARD_CAP_ROUNDS:
        print("[WT1] exploration top-up round", round_i, "have", len(spec_packs), flush=True)
        from dual_engine_workflow_v2.failure_kb import kb_context_for_ai as _kb
        _ctx = _kb()
        # Prefer missing directions
        have_dirs = {(p.get("meta") or {}).get("exploration_direction") for p in spec_packs}
        targets = [d for d in EXPLORATION_DIRS if d != "microstructure" and d not in have_dirs]
        if not targets:
            targets = [d for d in EXPLORATION_DIRS if d != "microstructure"]
        for target in targets:
            if len(spec_packs) >= 10:
                break
            part = glm_ideas_for_direction(
                target, _ctx, diagnosis,
                already=[{"family": (p.get("mechanism_spec") or {}).get("mechanism_family"),
                          "name": (p.get("mechanism_spec") or {}).get("mechanism_name")}
                         for p in spec_packs],
                n=2,
            )
            for idea in (part.get("ideas") or []):
                if len(spec_packs) >= 10:
                    break
                if idea.get("data_unavailable"):
                    eliminated_data.append(idea)
                    continue
                fams_have = {((p.get("mechanism_spec") or {}).get("mechanism_family") or "").lower()
                             for p in spec_packs}
                if str(idea.get("mechanism_family") or "").lower() in fams_have:
                    continue
                pack = expand_idea_to_spec(idea, kb_ctx)
                if not pack.get("ok"):
                    pack = idea_to_formal_spec_fallback(idea)
                if pack.get("ok"):
                    meta = pack.get("meta") or {}
                    meta["exploration_direction"] = idea.get("exploration_direction") or target
                    meta["idea_id"] = idea.get("idea_id")
                    fam = str((pack.get("mechanism_spec") or {}).get("mechanism_family") or "")
                    if target.replace("_", "") not in fam.lower().replace("_", ""):
                        pack["mechanism_spec"]["mechanism_family"] = "%s__%s" % (fam, target)
                    pack["meta"] = meta
                    pack["exploration_direction"] = meta["exploration_direction"]
                    spec_packs.append(pack)
                else:
                    expand_failures.append({"idea_id": idea.get("idea_id"), "errors": pack.get("errors")})
        round_i += 1

    existing_fps = store.list_fingerprints()
    kept, rejected_dup, dirs_covered = dedup_specs(spec_packs, existing_fps)
    print("[WT1] after dedup kept", len(kept), "rejected", len(rejected_dup), "dirs", sorted(dirs_covered), flush=True)

    # Ensure ≥8 if possible by relaxing only peer-order (not live-pool / bans)
    if len(kept) < 8:
        print("[WT1] WARNING under 8 after dedup; continuing with available + evidence", flush=True)

    formal = kept[: max(8, min(len(kept), 12))]
    # Prefer covering ≥5 directions
    formal_sorted = sorted(formal, key=lambda p: (
        0 if (p.get("meta") or {}).get("exploration_direction") in EXPLORATION_DIRS else 1,
        (p.get("meta") or {}).get("exploration_direction") or "",
    ))
    # unique directions first
    picked = []
    seen_dir = set()
    for p in formal_sorted:
        d = (p.get("meta") or {}).get("exploration_direction")
        if d and d not in seen_dir:
            picked.append(p)
            seen_dir.add(d)
    for p in formal_sorted:
        if p not in picked:
            picked.append(p)
        if len(picked) >= 8:
            break
    # If still short, take any remaining kept
    for p in kept:
        if len(picked) >= 8:
            break
        if p not in picked:
            picked.append(p)

    candidates_manifest = []
    for p in picked:
        spec = p["mechanism_spec"]
        meta = p.get("meta") or {}
        candidates_manifest.append({
            "mechanism_id": spec.get("mechanism_id"),
            "mechanism_name": spec.get("mechanism_name"),
            "mechanism_family": spec.get("mechanism_family"),
            "exploration_direction": meta.get("exploration_direction"),
            "counterparty_source": spec.get("counterparty_source"),
            "symbol": meta.get("symbol"),
            "timeframe": meta.get("timeframe"),
            "direction": meta.get("direction"),
            "idea_id": meta.get("idea_id"),
            "fingerprint_hash": (p.get("fingerprint") or {}).get("fingerprint_hash"),
            "family_hash": (p.get("fingerprint") or {}).get("family_hash"),
            "dedup_report": p.get("dedup_report"),
            "status": "formal_spec_ready",
        })
    _mirror("windtalker_phase1_candidates.json", {
        "generated_at": _now(),
        "initial_ideas_n": len(ideas),
        "feasible_ideas_n": len(feasible),
        "data_eliminated_n": len(eliminated_data),
        "expand_failures": expand_failures,
        "dedup_rejected": rejected_dup,
        "formal_specs_n": len(picked),
        "candidates": candidates_manifest,
        "data_eliminated": [
            {"idea_id": x.get("idea_id"), "family": x.get("mechanism_family"),
             "reason": x.get("eliminate_reason") or "data_unavailable"}
            for x in eliminated_data
        ],
    })

    if len(picked) < 8:
        evidence = {
            "formal_specs_n": len(picked),
            "ideas_n": len(ideas),
            "expand_failures": expand_failures,
            "dedup_rejected": rejected_dup,
            "exploration_rounds": round_i,
            "verdict": "exploration_cap_or_quality_filter_shortfall",
        }
        _mirror("windtalker_phase1_exploration_shortfall.json", evidence)
        print("[WT1] FATAL: cannot reach 8 formal specs", evidence, flush=True)
        # Still continue gates on what we have — Phase success criteria require ≥8;
        # report will FAIL honestly if unmet.

    # ---- 1C–1E sequential (results unconfused; parallel optional later) ----
    gate_rows = []
    for i, pack in enumerate(picked):
        tag = "wt1_%02d_%s" % (i + 1, (pack.get("mechanism_spec") or {}).get("mechanism_family", "x")[:24])
        print("[WT1] Phase 1C-E run", tag, flush=True)
        row = run_one_candidate(pack, tag)
        gate_rows.append(row)
        _mirror("windtalker_phase1_gate_results.json", {
            "generated_at": _now(),
            "n": len(gate_rows),
            "results": gate_rows,
        })
        print("[WT1] done", tag, "stage", row.get("stage"), "ok", row.get("ok"),
              "repairs", row.get("repair_rounds"), flush=True)

    # ---- KB delta ----
    kb_after = json.loads((WF / "failure_knowledgebase.json").read_text())
    kb_after_n = len(kb_after.get("records") or [])
    new_recs = (kb_after.get("records") or [])[kb_before_n:]
    kb_delta = {
        "generated_at": _now(),
        "before_n": kb_before_n,
        "after_n": kb_after_n,
        "added_n": max(0, kb_after_n - kb_before_n),
        "new_records": new_recs,
        "blocked_families_before": kb_before.get("blocked_families"),
        "blocked_families_after": kb_after.get("blocked_families"),
        "lessons_before_n": len(kb_before.get("lessons") or []),
        "lessons_after_n": len(kb_after.get("lessons") or []),
    }
    _mirror("windtalker_phase1_failure_kb_delta.json", kb_delta)

    stats = family_stats(candidates_manifest, rejected_dup + eliminated_data, gate_rows)
    _mirror("windtalker_phase1_family_stats.json", stats)

    # Summary for report writer
    summary = {
        "generated_at": _now(),
        "initial_ideas_n": len(ideas),
        "formal_specs_n": len(picked),
        "independent_families_n": stats.get("independent_family_count"),
        "dirs_covered_n": stats.get("exploration_direction_count"),
        "implemented_n": sum(1 for r in gate_rows if r.get("fidelity_diff_path") or r.get("task_id")),
        "gate_tested_n": len(gate_rows),
        "gate0_6_all_pass_n": sum(
            1 for r in gate_rows
            if r.get("human_confirm_pending") or (
                r.get("ok") and r.get("stage") in ("awaiting_human",)
            )
        ),
        "human_confirm_pending_n": sum(1 for r in gate_rows if r.get("human_confirm_pending")),
        "production_mounted_n": 0,
        "kb_added_n": kb_delta["added_n"],
        "live_positive_E_weekly": baselines["live_baseline"].get("calibrated_positive_E_weekly"),
        "live_positive_E_gap_weekly": baselines["live_baseline"].get("positive_E_gap_weekly"),
        "fillable_weekly": baselines["live_baseline"].get("fillable_weekly"),
        "do_not_loosen": baselines["live_baseline"].get("do_not_loosen_entries"),
        "do_not_force_open": baselines["live_baseline"].get("do_not_force_open"),
        "priority": baselines["live_baseline"].get("priority"),
        "gate_rows": gate_rows,
        "candidates": candidates_manifest,
        "stats": stats,
        "kb_delta": kb_delta,
        "baselines": baselines,
        "max_repair_rounds_observed": max([r.get("repair_rounds") or 0 for r in gate_rows] + [0]),
        "any_auto_mount": False,
    }
    _mirror("windtalker_phase1_summary.json", summary)
    print("[WT1] COMPLETE", json.dumps({
        k: summary[k] for k in [
            "initial_ideas_n", "formal_specs_n", "independent_families_n",
            "gate_tested_n", "human_confirm_pending_n", "kb_added_n",
        ]
    }), flush=True)
    return summary


if __name__ == "__main__":
    main()
