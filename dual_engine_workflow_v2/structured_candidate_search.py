# -*- coding: utf-8 -*-
"""Mechanism-coherent candidate search before the formal research gates.

The former creator asked an LLM for mechanisms but evaluated only a handful of
single-factor tails.  This module turns the declared mechanism family into
complete, immutable entry events and performs a development/confirmation split.
Only the development segment ranks candidates.  The confirmation segment is
kept separate for the later DSR/PBO gate.
"""
from __future__ import print_function

import hashlib
import itertools
import json
import math
import os

from . import probe_protocol as probes


FINE_Q = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
COARSE_Q = (0.60, 0.70, 0.80, 0.90)
DEFAULT_HORIZONS = (1, 2, 3, 4, 6, 8, 10, 12, 16, 18, 20, 24, 30, 36, 48)


def _finite(value, default=None):
    try:
        value = float(value)
        return default if math.isnan(value) or math.isinf(value) else value
    except Exception:
        return default


def _mean(values):
    rows = [_finite(value) for value in (values or [])]
    rows = [value for value in rows if value is not None]
    return sum(rows) / float(len(rows)) if rows else None


def _term(factor, side, q):
    return {
        "factor": factor,
        "side": side,
        "q": float(q),
        "window": probes.CAUSAL_QUANTILE_WINDOW,
        "min_history": probes.CAUSAL_QUANTILE_MIN_HISTORY,
        "threshold_source": "prior_only_rolling_quantile",
    }


def _event(event_id, family, definitions, matrix, mask_cache):
    masks = []
    for factor, side, q in definitions:
        key = (factor, side, float(q))
        if key not in mask_cache:
            mask_cache[key] = probes._causal_quantile_mask(
                (matrix or {}).get(factor) or [], side=side, q=q,
            )
        masks.append(mask_cache[key])
    n = min([len(row) for row in masks] or [0])
    return {
        "event_id": event_id,
        "event_kind": "mechanism_preserving",
        "kind": "mechanism_preserving",
        "logic": "all",
        "family": family,
        "terms": [_term(*row) for row in definitions],
        "mask": [all(bool(row[index]) for row in masks) for index in range(n)],
    }


def _family_set(contract, brief):
    """Brief/contract-locked families only — never default to the whole union."""
    rows = set(str(value).lower() for value in ((contract or {}).get("family_hints") or []))
    text = str(brief or "").lower()
    if any(value in text for value in ("衰竭", "恐慌", "超卖", "exhaust", "panic")):
        rows.add("exhaustion")
    if any(value in text for value in (
        "回踩", "回撤", "顺势", "trend pullback", "pullback",
        "反弹后重新", "反弹后再", "反弹转弱", "反弹后转弱", "反弹衰竭",
        "反抽续", "反抽后",
    )):
        rows.add("trend_pullback")
    if any(value in text for value in ("成交量异动", "异常成交量", "放量", "volume anomaly")):
        rows.add("volume_anomaly_breakout")
    if any(value in text for value in ("唐奇安", "donchian", "通道突破", "趋势突破")):
        rows.add("donchian_trend_break")
    if rows:
        return rows
    # Fall back to mechanism trees parsed from the brief (still locked).
    from . import research_branch_manager as branch_mgr
    for tree in branch_mgr.trees_for_brief(brief) or []:
        tid = str(tree.get("tree_id") or "")
        if tid == "exhaustion_recovery":
            rows.add("exhaustion")
        elif tid == "donchian_trend_break":
            rows.add("donchian_trend_break")
        elif tid == "vol_squeeze_break":
            rows.add("volume_anomaly_breakout")
    return rows


def _quantile_grid():
    """Same-identity deepen uses denser quantiles; default stays coarse."""
    flag = str(os.environ.get("QIYU_STRUCTURED_DEEPEN") or "").strip().lower()
    return FINE_Q if flag in ("1", "true", "yes") else COARSE_Q


def _definitions(matrix, direction, families):
    """Yield bounded, explainable condition sets for the requested families."""
    present = set((matrix or {}).keys())
    direction = str(direction or "long").lower()
    long_side = direction == "long"
    signed_side = "high" if long_side else "low"
    emitted = set()
    q_grid = _quantile_grid()

    def output(family, definitions):
        if not all(row[0] in present for row in definitions):
            return
        key = (family, tuple(definitions))
        if key in emitted:
            return
        emitted.add(key)
        yield family, tuple(definitions)

    if "exhaustion" in families and long_side:
        for q_rsi, q_z, q_reclaim in itertools.product(
            q_grid, q_grid, (0.60, 0.70, 0.80)
        ):
            base = (
                ("rsi_14", "low", q_rsi),
                ("close_z_20", "low", q_z),
                ("bullish_reclaim", "high", q_reclaim),
            )
            for item in output("exhaustion", base):
                yield item
            for extra in (
                ("downside_velocity_decay", "high", 0.60),
                ("downtrend_persistence_12", "low", 0.70),
                ("lower_wick_pct", "high", 0.70),
                ("volume_z", "high", 0.70),
            ):
                for item in output("exhaustion", base + (extra,)):
                    yield item

    if "trend_pullback" in families:
        pullback_side = "low" if long_side else "high"
        reclaim_side = "high" if long_side else "low"
        for q_trend, q_pullback, q_reclaim in itertools.product(FINE_Q, FINE_Q, FINE_Q):
            rows = (
                ("trend_bias_50_200", signed_side, q_trend),
                ("ret_3", pullback_side, q_pullback),
                ("ret_1", reclaim_side, q_reclaim),
            )
            for item in output("trend_pullback", rows):
                yield item

    if "volume_anomaly_breakout" in families:
        for q_volume, q_price in itertools.product(COARSE_Q, COARSE_Q):
            base = (
                ("volume_z", "high", q_volume),
                ("close_z_20", signed_side, q_price),
            )
            for item in output("volume_anomaly_breakout", base):
                yield item
            for context in (
                ("trend_bias_50_200", signed_side, 0.60),
                ("trend_slope_50_12", signed_side, 0.60),
                ("trend_efficiency_12", "high", 0.60),
                ("breakout_acceptance", signed_side, 0.60),
            ):
                for item in output("volume_anomaly_breakout", base + (context,)):
                    yield item

    if "donchian_trend_break" in families:
        factor = "donchian20_long_break" if long_side else "donchian20_short_break"
        for q_break, q_trend in itertools.product((0.60, 0.70, 0.80), (0.60, 0.70, 0.80)):
            base = (
                (factor, "high", q_break),
                ("trend_bias_50_200", signed_side, q_trend),
            )
            for item in output("donchian_trend_break", base):
                yield item
            for extra in (
                ("volume_z", "high", 0.60),
                ("trend_efficiency_12", "high", 0.60),
                ("trend_slope_50_12", signed_side, 0.60),
            ):
                for item in output("donchian_trend_break", base + (extra,)):
                    yield item


def _rsi_series(candles, period=14):
    """Causal RSI(period) aligned to candle closes; prefix None until warm."""
    closes = []
    for row in candles or []:
        try:
            closes.append(float(row.get("close")))
        except Exception:
            closes.append(None)
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = []
    losses = []
    for index in range(1, len(closes)):
        prev = closes[index - 1]
        cur = closes[index]
        if prev is None or cur is None:
            gains.append(None)
            losses.append(None)
            continue
        delta = cur - prev
        gains.append(delta if delta > 0 else 0.0)
        losses.append((-delta) if delta < 0 else 0.0)
    # gains/losses indexed at close[i] using move from i-1→i, length n-1
    # First RSI at candle index = period
    window_g = [value for value in gains[:period] if value is not None]
    window_l = [value for value in losses[:period] if value is not None]
    if len(window_g) < period or len(window_l) < period:
        return out
    avg_g = sum(window_g) / float(period)
    avg_l = sum(window_l) / float(period)
    if avg_l <= 1e-12:
        out[period] = 100.0
    else:
        rs = avg_g / avg_l
        out[period] = 100.0 - (100.0 / (1.0 + rs))
    for index in range(period + 1, len(closes)):
        g = gains[index - 1]
        l = losses[index - 1]
        if g is None or l is None or out[index - 1] is None:
            out[index] = None
            continue
        avg_g = (avg_g * (period - 1) + g) / float(period)
        avg_l = (avg_l * (period - 1) + l) / float(period)
        if avg_l <= 1e-12:
            out[index] = 100.0
        else:
            rs = avg_g / avg_l
            out[index] = 100.0 - (100.0 / (1.0 + rs))
    return out


def _trade_observation_feature_rsi_tp(candles, signal_i, horizon, direction,
                                      rsi_series, tp_level):
    """Next-bar-open entry; exit on RSI mean-reversion TP, stop, or horizon.

    Aligns research WR口径 with live/formal feature-TP books (XRP/BTC/SOL).
    Fixed-horizon close alone is lottery-skewed and structurally rarely clears
    WR>50%; gating assembly on that exit while formal books use RSI TP is the
    creation→review empty-output root cause.
    """
    rows = candles or []
    entry_i = int(signal_i) + 1
    planned_exit_i = entry_i + int(horizon) - 1
    if entry_i >= len(rows) or planned_exit_i >= len(rows):
        return None
    entry = _finite(rows[entry_i].get("open"))
    if not entry:
        return None
    direction = 1 if int(direction) > 0 else -1
    stop_pct = probes.recipe_policy.PROTECTIVE_STOP_PCT
    stop_price = entry * (1.0 - stop_pct if direction > 0 else 1.0 + stop_pct)
    exit_reason = "fixed_horizon_close"
    exit_price = None
    exit_i = planned_exit_i
    tp = _finite(tp_level)
    for index in range(entry_i, planned_exit_i + 1):
        high = _finite(rows[index].get("high"))
        low = _finite(rows[index].get("low"))
        stopped = bool(
            (direction > 0 and low is not None and low <= stop_price)
            or (direction < 0 and high is not None and high >= stop_price)
        )
        if stopped:
            exit_i = index
            exit_price = stop_price
            exit_reason = "protective_stop"
            break
        rsi = None
        if 0 <= index < len(rsi_series or []):
            rsi = _finite(rsi_series[index])
        if tp is not None and rsi is not None:
            hit_tp = (direction > 0 and rsi >= tp) or (direction < 0 and rsi <= tp)
            if hit_tp:
                close = _finite(rows[index].get("close"))
                if close is not None:
                    exit_i = index
                    exit_price = close
                    exit_reason = "feature_rsi_tp"
                    break
    if exit_price is None:
        exit_price = _finite(rows[planned_exit_i].get("close"))
        exit_i = planned_exit_i
    if exit_price is None:
        return None
    ret = float(direction) * (exit_price / entry - 1.0)
    return {
        "return": ret,
        "signal_index": signal_i,
        "entry_index": entry_i,
        "exit_index": exit_i,
        "planned_exit_index": planned_exit_i,
        "exit_reason": exit_reason,
        "protective_stop_price": stop_price,
    }


def _stats_from_observations(observations, raw_n, independent, symbol, horizon):
    gross_returns = [float(row.get("return") or 0.0) for row in observations]
    costs = probes._cost_scenarios(symbol, horizon)
    cost = float((costs.get("taker_taker") or {}).get("total_friction") or 0.0)
    leverage = float(probes.recipe_policy.EXECUTION_LEVERAGE)
    returns = [(value - cost) * leverage for value in gross_returns]
    gross = _mean(gross_returns)
    net = _mean(returns)
    t_stat = probes._newey_west_t(returns)
    efr = (gross / cost) if gross is not None and cost > 0 else None
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    n = len(returns)
    win_rate = (len(wins) / float(n)) if n else None
    avg_win = (sum(wins) / float(len(wins))) if wins else 0.0
    avg_loss = (sum(-value for value in losses) / float(len(losses))) if losses else 0.0
    payoff = (avg_win / avg_loss) if avg_loss > 0 else (999.0 if avg_win > 0 else 0.0)
    expectancy_factor = (win_rate * payoff) if win_rate is not None else None
    if wins:
        reduced = list(returns)
        reduced.remove(max(wins))
        mean_without_max = _mean(reduced)
    else:
        mean_without_max = net
    return {
        "n": n,
        "n_raw": raw_n,
        "mean_net": net,
        "mean_gross": gross,
        "hac_t_stat": t_stat,
        "efr": efr,
        "win_rate": win_rate,
        "win_rate_pct": (None if win_rate is None else win_rate * 100.0),
        "payoff_ratio": payoff,
        "expectancy_factor": expectancy_factor,
        "mean_net_without_max_win": mean_without_max,
        "high_wr_pass": bool(win_rate is not None and win_rate > 0.50),
        "anti_lottery_pass": bool(
            expectancy_factor is not None and expectancy_factor >= 1.0
            and mean_without_max is not None and mean_without_max > 0
        ),
        "trade_returns": returns,
        "event_indices": independent,
    }


def _fast_trial(candles, event, horizon, direction, symbol, timeframe,
                exit_mode="fixed_horizon", rsi_series=None, tp_level=None):
    direction_num = 1 if str(direction).lower() == "long" else -1
    gap = probes._independence_gap_bars(horizon, timeframe=timeframe)
    raw, independent = probes._independent_events(
        event.get("mask") or [], gap, merge_bars=max(3, int(horizon)),
    )
    observations = []
    for index in independent:
        if exit_mode == "feature_rsi_tp" and rsi_series is not None and tp_level is not None:
            row = _trade_observation_feature_rsi_tp(
                candles, index, horizon, direction_num, rsi_series, tp_level,
            )
        else:
            row = probes._trade_observation(
                candles, index, horizon, direction_num, "next_bar_open",
            )
        if row is not None:
            observations.append(row)
    stats = _stats_from_observations(
        observations, len(raw), independent, symbol, horizon,
    )
    stats["exit_mode"] = (
        "feature_rsi_tp_v1" if exit_mode == "feature_rsi_tp" else "fixed_horizon_close_v1"
    )
    if exit_mode == "feature_rsi_tp":
        stats["feature_tp_level"] = _finite(tp_level)
    return stats


# Families whose live/formal winners use RSI feature take-profit.  Research must
# evaluate WR on the same exit口径 or the WR>50 gate is structurally empty.
FEATURE_TP_FAMILIES = set(("exhaustion", "trend_pullback"))
FEATURE_TP_LEVELS_LONG = (55.0, 58.0, 60.0, 65.0)
FEATURE_TP_LEVELS_SHORT = (45.0, 47.0, 42.0, 40.0)


def _slice_event(event, start, end):
    row = dict(event)
    row["mask"] = list(event.get("mask") or [])[start:end]
    return row


def _pbo_clock(candles, stats):
    values = [0.0] * len(candles or [])
    for index, value in zip(stats.get("event_indices") or [], stats.get("trade_returns") or []):
        if 0 <= int(index) < len(values):
            values[int(index)] += float(value)
    timestamps = [row.get("ts") for row in (candles or [])]
    if timestamps and all(value is not None for value in timestamps) and len(set(timestamps)) == len(timestamps):
        return {"timestamps": timestamps, "returns": values}
    return values


def _event_id(family, definitions):
    text = json.dumps(definitions, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "%s_structured_%s" % (
        family, hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    )


def search(candles, factor_matrix, symbol, timeframe, direction, contract,
           brief="", max_finalists=6, development_ratio=0.65):
    candles = list(candles or [])
    if len(candles) < 800:
        return {
            "ok": True, "n_finalists": 0, "hypotheses": [],
            "reason": "insufficient_candles_for_development_confirmation_split",
        }
    cut = max(500, min(len(candles) - 240, int(len(candles) * float(development_ratio))))
    development_candles = candles[:cut]
    confirmation_candles = candles[cut:]
    holding = (contract or {}).get("holding_contract") or {}
    horizons = [int(value) for value in (holding.get("allowed_horizons_bars") or DEFAULT_HORIZONS)]
    horizons = [value for value in horizons if 1 <= value <= 240]
    if not horizons:
        horizons = list(DEFAULT_HORIZONS)
    families = _family_set(contract, brief)
    mask_cache = {}
    trials = []
    definitions_tested = 0
    direction_l = str(direction or "long").lower()
    rsi_dev = _rsi_series(development_candles)
    rsi_all = _rsi_series(candles)
    tp_levels = (
        FEATURE_TP_LEVELS_LONG if direction_l == "long" else FEATURE_TP_LEVELS_SHORT
    )
    for family, definitions in _definitions(factor_matrix, direction, families):
        definitions_tested += 1
        event = _event(_event_id(family, definitions), family, definitions, factor_matrix, mask_cache)
        dev_event = _slice_event(event, 0, cut)
        best_dev = None
        use_feature_tp = family in FEATURE_TP_FAMILIES
        for horizon in horizons:
            if use_feature_tp:
                # Search a small RSI-TP grid; keep cost/stop/horizon identity.
                for tp_level in tp_levels:
                    stats = _fast_trial(
                        development_candles, dev_event, horizon, direction, symbol,
                        timeframe, exit_mode="feature_rsi_tp", rsi_series=rsi_dev,
                        tp_level=tp_level,
                    )
                    row = {
                        "family": family,
                        "definitions": definitions,
                        "event": event,
                        "horizon": horizon,
                        "development": stats,
                        "feature_tp_level": tp_level,
                        "exit_mode": "feature_rsi_tp_v1",
                    }
                    if best_dev is None or (
                        bool(stats.get("high_wr_pass")),
                        bool(stats.get("anti_lottery_pass")),
                        bool(stats.get("mean_net") is not None and stats.get("mean_net") > 0),
                        float(stats.get("win_rate") or -1e9),
                        float(stats.get("hac_t_stat") or -1e9),
                        float(stats.get("mean_net") or -1e9),
                    ) > (
                        bool((best_dev.get("development") or {}).get("high_wr_pass")),
                        bool((best_dev.get("development") or {}).get("anti_lottery_pass")),
                        bool((best_dev.get("development") or {}).get("mean_net") is not None
                             and (best_dev.get("development") or {}).get("mean_net") > 0),
                        float((best_dev.get("development") or {}).get("win_rate") or -1e9),
                        float((best_dev.get("development") or {}).get("hac_t_stat") or -1e9),
                        float((best_dev.get("development") or {}).get("mean_net") or -1e9),
                    ):
                        best_dev = row
            else:
                stats = _fast_trial(
                    development_candles, dev_event, horizon, direction, symbol, timeframe,
                )
                row = {
                    "family": family,
                    "definitions": definitions,
                    "event": event,
                    "horizon": horizon,
                    "development": stats,
                    "exit_mode": "fixed_horizon_close_v1",
                }
                if best_dev is None or (
                    bool(stats.get("mean_net") is not None and stats.get("mean_net") > 0),
                    float(stats.get("hac_t_stat") or -1e9),
                    float(stats.get("mean_net") or -1e9),
                ) > (
                    bool((best_dev.get("development") or {}).get("mean_net") is not None
                         and (best_dev.get("development") or {}).get("mean_net") > 0),
                    float((best_dev.get("development") or {}).get("hac_t_stat") or -1e9),
                    float((best_dev.get("development") or {}).get("mean_net") or -1e9),
                ):
                    best_dev = row
        if best_dev is None:
            continue
        stats = best_dev["development"]
        if not (
            int(stats.get("n") or 0) >= probes.MIN_INDEPENDENT_EVENTS
            and float(stats.get("mean_net") or -1e9) > 0
            and float(stats.get("hac_t_stat") or -1e9) >= 1.0
            and float(stats.get("efr") or -1e9) >= 1.20
            # Prefer dense books early: development must already clear WR>50%
            # and anti-lottery so confirmation is not wasted on lottery tails.
            # For exhaustion/trend_pullback this WR is measured under feature RSI TP
            # (formal-reproducible), not fixed-horizon lottery closes.
            and bool(stats.get("high_wr_pass"))
            and bool(stats.get("anti_lottery_pass"))
        ):
            continue
        trials.append(best_dev)

    # Selection uses development evidence only.  Confirmation is never part of
    # the rank key and cannot promote a weak development candidate.
    # Rank high-WR / higher expectancy first so formal review sees distributed
    # payoff structures rather than rare fat-tail mean_net winners.
    trials.sort(key=lambda row: (
        float((row.get("development") or {}).get("win_rate") or -1e9),
        float((row.get("development") or {}).get("expectancy_factor") or -1e9),
        float((row.get("development") or {}).get("hac_t_stat") or -1e9),
        float((row.get("development") or {}).get("mean_net") or -1e9),
        int((row.get("development") or {}).get("n") or 0),
    ), reverse=True)
    # The former top-3x funnel let only 18 of hundreds of development-positive
    # candidates touch the untouched confirmation segment.  This made the
    # creator structurally brittle: a valid but slightly lower-ranked
    # development candidate could never prove itself out of sample.  Freeze a
    # broader, still bounded set.  Every frozen row remains in
    # confirmation_trial_count and the downstream DSR/PBO penalty, so this is
    # search breadth rather than a relaxed statistical gate.
    # Broader confirmation funnel: high-WR books are rarer than mean_net>0
    # lotteries, so inspect more development survivors out-of-sample.
    confirmation_budget = max(48, int(max_finalists) * 12)
    confirmation_budget = min(96, confirmation_budget)
    preselected = trials[:max(1, confirmation_budget)]
    finalists = []
    confirmation_competitors = []
    for rank, row in enumerate(preselected, 1):
        event = row["event"]
        confirmation_event = _slice_event(event, cut, len(candles))
        exit_mode = "feature_rsi_tp" if row.get("exit_mode") == "feature_rsi_tp_v1" else "fixed_horizon"
        tp_level = row.get("feature_tp_level")
        confirmation = _fast_trial(
            confirmation_candles, confirmation_event, row["horizon"], direction,
            symbol, timeframe,
            exit_mode=exit_mode,
            rsi_series=rsi_all[cut:] if exit_mode == "feature_rsi_tp" else None,
            tp_level=tp_level,
        )
        confirmation_competitors.append({
            "event_id": event.get("event_id"),
            "trade_returns": confirmation.get("trade_returns") or [],
            "pbo_returns": _pbo_clock(confirmation_candles, confirmation),
        })
        if not (
            int(confirmation.get("n") or 0) >= 8
            and float(confirmation.get("mean_net") or -1e9) > 0
            and float(confirmation.get("hac_t_stat") or -1e9) >= 1.0
            and float(confirmation.get("efr") or -1e9) >= 1.20
            and bool(confirmation.get("high_wr_pass"))
            and bool(confirmation.get("anti_lottery_pass"))
        ):
            continue
        definitions = row["definitions"]
        hypothesis_id = "H_structured_%s_%02d" % (event["event_id"], rank)
        hypothesis = {
            "hypothesis_id": hypothesis_id,
            "mechanism_id": event["event_id"],
            "family": row["family"],
            "path": "structured_candidate_search",
            "source": "deterministic_mechanism_search",
            "statement_zh": (
                "机制一致的完整复合入场事件；开发段选型、后段独立确认。"
                + ("出场口径=RSI特征止盈（与正式/实盘一致）。" if exit_mode == "feature_rsi_tp" else "")
            ),
            "factor_hints": [value[0] for value in definitions],
            "observable_proxy": [value[0] for value in definitions],
            "required_quantile_terms": [_term(*value) for value in definitions],
            "required_horizons_bars": [int(row["horizon"])],
            "predicted_direction": str(direction).lower(),
            "trade_direction_locked": True,
            "required_entry_timing": {"mode": "next_bar_open"},
            "exit_mode": row.get("exit_mode") or "fixed_horizon_close_v1",
            "feature_tp_level": tp_level,
            "contract_relevant": True,
            "contract_family_match": True,
            "structured_search_candidate": True,
            "priority_boost": 100.0 - rank,
            "development_confirmation": {
                "selection_rank": rank,
                "development_start": development_candles[0].get("ts"),
                "development_end": development_candles[-1].get("ts"),
                "confirmation_start": confirmation_candles[0].get("ts"),
                "confirmation_end": confirmation_candles[-1].get("ts"),
                "development": {
                    key: row["development"].get(key)
                    for key in (
                        "n", "mean_net", "mean_gross", "hac_t_stat", "efr",
                        "win_rate", "win_rate_pct", "payoff_ratio",
                        "expectancy_factor", "mean_net_without_max_win",
                        "high_wr_pass", "anti_lottery_pass",
                    )
                },
                "confirmation": {
                    key: confirmation.get(key)
                    for key in (
                        "n", "mean_net", "mean_gross", "hac_t_stat", "efr",
                        "win_rate", "win_rate_pct", "payoff_ratio",
                        "expectancy_factor", "mean_net_without_max_win",
                        "high_wr_pass", "anti_lottery_pass",
                    )
                },
                "confirmation_returns": confirmation.get("trade_returns") or [],
                "confirmation_pbo_returns": _pbo_clock(confirmation_candles, confirmation),
                "selection_used_confirmation": False,
                "confirmation_trial_count": len(preselected),
            },
        }
        if len(finalists) < int(max_finalists):
            finalists.append(hypothesis)

    return {
        "ok": True,
        "schema": "qiyu_structured_candidate_search_v1",
        "families": sorted(families),
        "development_ratio": float(development_ratio),
        "development_bars": len(development_candles),
        "confirmation_bars": len(confirmation_candles),
        "event_definitions_tested": definitions_tested,
        "horizon_trials": definitions_tested * len(horizons),
        "development_positive_n": len(trials),
        "preselected_n": len(preselected),
        "n_finalists": len(finalists),
        "hypotheses": finalists,
        "confirmation_competitors": confirmation_competitors,
        "selection_used_confirmation": False,
        "note_zh": (
            "完整复合事件先在开发段选型，再以冻结身份进入后段确认；"
            "确认段不参与候选排序。"
        ),
    }


def probe():
    return {
        "ok": True,
        "provider": "structured_candidate_search_v1",
        "development_confirmation_split": True,
        "complete_event_identity": True,
        "selection_uses_confirmation": False,
    }
