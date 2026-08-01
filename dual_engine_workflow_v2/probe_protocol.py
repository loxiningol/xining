# -*- coding: utf-8 -*-
"""Mechanism-preserving probe protocol used before strategy assembly.

The probe is intentionally simple: no stop-loss, take-profit, sizing or complex
exit is allowed.  Unlike the legacy probe it does not confuse one failed factor
quantile with a dead mechanism family.  It records three independent evidence
axes (statistical, economic and execution), uses real forward horizons and
clusters adjacent signals into independent events.
"""
from __future__ import print_function

import bisect
import hashlib
import math
import random
from datetime import datetime


RESEARCH_STATES_ZH = {
    "NO_DIRECTIONAL_EFFECT": "未发现方向性效应",
    "DIRECTIONAL_BUT_SMALL": "存在方向性但幅度不足",
    "VOLATILITY_EFFECT_ONLY": "仅发现波动效应，尚无方向优势",
    "STATE_CONDITIONAL": "效应仅在特定状态出现",
    "HORIZON_MISMATCH": "效应周期与原假设不一致",
    "EXECUTION_MAPPING_FAILURE": "统计效应存在但执行映射无法覆盖成本",
    "PROXY_INADEQUATE": "现有代理变量不足以检验该机制",
    "DATA_INADEQUATE": "缺少检验该机制所需的数据维度",
    "SAMPLE_INADEQUATE": "独立事件样本不足",
    "MECHANISM_CONTRADICTED": "机制在充分覆盖后被重复证伪",
    "NEAR_MISS_DIAGNOSTIC": "接近门槛，进入限额近缘诊断",
    "FAMILY_EXHAUSTED": "机制族在充分覆盖后耗尽",
    "READY_FOR_ASSEMBLY": "三轴证据通过，可提交策略组装",
}

DEFAULT_HORIZONS = (1, 3, 6, 12)
DEFAULT_QUANTILES = (0.80, 0.90)
MIN_INDEPENDENT_EVENTS = 12


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _timeframe_bar_minutes(timeframe):
    text = str(timeframe or "5m").strip().lower()
    try:
        if text.endswith("m"):
            return max(1, int(float(text[:-1])))
        if text.endswith("h"):
            return max(1, int(float(text[:-1]) * 60))
        if text.endswith("d"):
            return max(1, int(float(text[:-1]) * 1440))
    except Exception:
        pass
    return 5


def _independence_gap_bars(horizon, timeframe=None):
    """Economic independence gap: hold window + ~2h refractory on the bar grid.

    A 6-bar gap on 5m data still admits ~1 event / 30m and can invent 1000+
    "independent" events on long OHLCV.  Use a harder refractory.
    """
    bar_min = _timeframe_bar_minutes(timeframe)
    # ~120 minutes of market time between independent economic events
    refractory = max(1, int(math.ceil(120.0 / float(bar_min))))
    hold = max(1, int(horizon or 1))
    return max(hold * 2, refractory, 12)


def _parse_hypothesis_horizon_bars(hypothesis, timeframe=None):
    """Map free-text horizon to preferred bar band for HORIZON_MISMATCH."""
    text = str((hypothesis or {}).get("horizon") or "").lower()
    bar_min = _timeframe_bar_minutes(timeframe)
    # defaults: prefer medium holds
    preferred = {3, 6}
    if any(k in text for k in ("秒", "second", "tick")):
        preferred = {1}
    elif any(k in text for k in ("15m", "15分", "1-12", "1–12", "短周期", "short")):
        preferred = {1, 3, 6}
    elif any(k in text for k in ("2h", "1-2h", "1h", "小时", "medium")):
        # 60–120 minutes
        lo = max(1, int(math.ceil(60.0 / bar_min)))
        hi = max(lo, int(math.ceil(120.0 / bar_min)))
        preferred = set(h for h in DEFAULT_HORIZONS if lo <= h <= hi) or {6, 12}
    elif any(k in text for k in ("4h", "日", "day", "long")):
        preferred = {12}
    return preferred


def _finite(v):
    try:
        x = float(v)
        return None if math.isnan(x) or math.isinf(x) else x
    except Exception:
        return None


def _mean(xs):
    vals = [_finite(x) for x in (xs or [])]
    vals = [x for x in vals if x is not None]
    return (sum(vals) / float(len(vals))) if vals else None


def _quantile(xs, q):
    vals = sorted([x for x in [_finite(v) for v in (xs or [])] if x is not None])
    if not vals:
        return None
    pos = max(0, min(len(vals) - 1, int(float(q) * (len(vals) - 1))))
    return vals[pos]


def _causal_quantile_mask(values, side="high", q=0.8, window=240, min_history=80):
    """Prior-only rolling quantile mask; current/future values never set threshold."""
    values = list(values or [])
    out = [False] * len(values)
    ordered = []
    history = []
    for i, raw in enumerate(values):
        v = _finite(raw)
        if len(ordered) >= int(min_history) and v is not None:
            qq = float(q) if side == "high" else (1.0 - float(q))
            idx = max(0, min(len(ordered) - 1, int(qq * (len(ordered) - 1))))
            threshold = ordered[idx]
            out[i] = (v >= threshold) if side == "high" else (v <= threshold)
        history.append(v)
        if v is not None:
            bisect.insort(ordered, v)
        if len(history) > int(window):
            old = history.pop(0)
            if old is not None:
                j = bisect.bisect_left(ordered, old)
                if j < len(ordered):
                    ordered.pop(j)
    return out


def signal_from_factor(factor_values, side="high", q=0.8):
    """Compatibility wrapper; signal threshold is causal and prior-only."""
    return [1 if x else 0 for x in _causal_quantile_mask(factor_values, side, q)]


def _direction_candidates(hypothesis):
    text = " ".join([
        str((hypothesis or {}).get("predicted_direction") or ""),
        str((hypothesis or {}).get("statement_zh") or ""),
        str((hypothesis or {}).get("family") or ""),
    ]).lower()
    if any(x in text for x in ("negative_shift", "short", "下行", "做空")):
        return (-1,)
    if any(x in text for x in ("positive_shift", "long", "上行", "回升", "bounce", "recovery")):
        return (1,)
    return (1, -1)


def _candidate_events(hypothesis, factor_matrix, max_specs=18):
    hints = list((hypothesis or {}).get("factor_hints") or
                 (hypothesis or {}).get("observable_proxy") or [])
    available = [h for h in hints if h in (factor_matrix or {})]
    specs = []
    cache = {}

    def mask(name, side, q):
        key = (name, side, q)
        if key not in cache:
            cache[key] = _causal_quantile_mask(factor_matrix.get(name) or [], side, q)
        return cache[key]

    for name in available:
        for q in DEFAULT_QUANTILES:
            for side in ("high", "low"):
                specs.append({
                    "event_id": "%s_%s_q%s" % (name, side, int(q * 100)),
                    "terms": [{"factor": name, "side": side, "q": q}],
                    "mask": mask(name, side, q),
                    "kind": "single_proxy",
                })

    # Intersections preserve a mechanism better than stripping it to one proxy.
    pairs = []
    for i in range(min(len(available), 5)):
        for j in range(i + 1, min(len(available), 5)):
            pairs.append((available[i], available[j]))
    for a, b in pairs:
        for sa, sb in (("high", "high"), ("low", "low"), ("high", "low"), ("low", "high")):
            ma, mb = mask(a, sa, 0.8), mask(b, sb, 0.8)
            n = min(len(ma), len(mb))
            specs.append({
                "event_id": "%s_%s_AND_%s_%s" % (a, sa, b, sb),
                "terms": [
                    {"factor": a, "side": sa, "q": 0.8},
                    {"factor": b, "side": sb, "q": 0.8},
                ],
                "mask": [bool(ma[k] and mb[k]) for k in range(n)],
                "kind": "mechanism_intersection",
            })

    # Specialised, still-minimal mechanism events. These remain probes, not strategies.
    family = str((hypothesis or {}).get("family") or "").lower()
    mech = str((hypothesis or {}).get("mechanism_id") or "").lower()
    blob = family + " " + mech + " " + str((hypothesis or {}).get("statement_zh") or "").lower()
    specialised = []
    if any(x in blob for x in ("exhaust", "liquid", "衰竭", "清算", "panic")):
        combos = [
            ("exhaustion_score", "high", "absorption_proxy", "high", "exhaustion_absorption"),
            ("downside_velocity_decay", "high", "reclaim_strength", "high", "exhaustion_reclaim"),
            ("signed_volume_pressure", "low", "impact_decay_proxy", "high", "sell_pressure_decay"),
        ]
        specialised.extend(combos)
    if any(x in blob for x in ("squeeze", "compression", "压缩", "扩张", "break")):
        combos = [
            ("squeeze_persistence", "high", "volatility_acceleration", "high", "squeeze_volatility"),
            ("expansion_score", "high", "breakout_acceptance", "high", "break_acceptance"),
            ("expansion_score", "high", "breakout_acceptance", "low", "fake_break_reversion"),
        ]
        specialised.extend(combos)
    for a, sa, b, sb, name in specialised:
        if a not in factor_matrix or b not in factor_matrix:
            continue
        ma, mb = mask(a, sa, 0.8), mask(b, sb, 0.8)
        n = min(len(ma), len(mb))
        specs.insert(0, {
            "event_id": name,
            "terms": [{"factor": a, "side": sa, "q": 0.8},
                      {"factor": b, "side": sb, "q": 0.8}],
            "mask": [bool(ma[k] and mb[k]) for k in range(n)],
            "kind": "mechanism_preserving",
        })

    # Stable order and bounded diagnostic budget.
    seen, unique = set(), []
    for row in specs:
        if row["event_id"] in seen:
            continue
        seen.add(row["event_id"])
        unique.append(row)
    return unique[: int(max_specs)], available


def _independent_events(mask, gap_bars, merge_bars=None):
    """Collapse signal runs into economically independent event onsets.

    Steps:
      1) collect raw hits
      2) contiguous run → single onset
      3) soft-merge onsets within merge_bars (same episode)
      4) enforce refractory gap so holding windows do not overlap in economic time
    """
    raw = [i for i, hit in enumerate(mask or []) if hit]
    if not raw:
        return [], []
    onsets = [raw[0]]
    prev = raw[0]
    for i in raw[1:]:
        if i > prev + 1:
            onsets.append(i)
        prev = i
    merge = max(1, int(merge_bars if merge_bars is not None else max(3, int(gap_bars or 1) // 2)))
    episodes = [onsets[0]]
    for i in onsets[1:]:
        if i - episodes[-1] > merge:
            episodes.append(i)
    gap = max(12, int(gap_bars) or 0)
    kept, last = [], -10 ** 9
    for i in episodes:
        if i - last >= gap:
            kept.append(i)
            last = i
    return raw, kept


def _derive_failure_codes(state, gross, t_stat, statistical, economic, execution,
                          volatility_effect, mean_mfe, mean_mae, primary_cost,
                          n_raw, n_indep, n_filled):
    codes = []
    if state == "SAMPLE_INADEQUATE":
        codes.append("sample_insufficient")
    if state == "PROXY_INADEQUATE":
        codes.append("proxy_failure")
    if state == "DATA_INADEQUATE":
        codes.append("data_insufficient")
    if gross is None or abs(float(gross or 0.0)) < 1e-8:
        codes.append("gross_edge_absent")
    elif gross is not None and float(gross) > 0 and not statistical:
        codes.append("gross_edge_unstable")
    if volatility_effect and not statistical:
        codes.append("direction_unresolved")
    if statistical and not execution:
        if primary_cost == "taker_taker":
            codes.append("execution_taker_only")
        codes.append("execution_mapping_failure")
        codes.append("spread_dominated")
    if mean_mae is not None and mean_mfe is not None and abs(float(mean_mae)) > abs(float(mean_mfe)) * 0.85:
        if gross is not None and float(gross) <= 0:
            codes.append("adverse_selection")
    if n_raw and n_indep and int(n_raw) > max(8, int(n_indep) * 4):
        codes.append("event_dilution")
        codes.append("signal_redundancy")
    if state == "STATE_CONDITIONAL":
        codes.append("state_conditional_only")
    if state == "HORIZON_MISMATCH":
        codes.append("horizon_mismatch")
    if state == "NEAR_MISS_DIAGNOSTIC":
        codes.append("near_miss")
    if state == "MECHANISM_CONTRADICTED":
        codes.append("mechanism_contradicted")
    if state == "FAMILY_EXHAUSTED":
        codes.append("family_exhausted")
    # de-dupe preserve order
    out, seen = [], set()
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _regime_split_conditional(candles, obs):
    """Detect STATE_CONDITIONAL when only one ATR regime carries the edge."""
    if not candles or len(obs or []) < (MIN_INDEPENDENT_EVENTS * 2):
        return False, {}
    atr = []
    for row in candles:
        try:
            high = float(row.get("high"))
            low = float(row.get("low"))
            close = float(row.get("close") or 0.0)
            atr.append((high - low) / close if close else None)
        except Exception:
            atr.append(None)
    vals = [x for x in atr if x is not None]
    if len(vals) < 80:
        return False, {}
    med = sorted(vals)[len(vals) // 2]
    high_rets, low_rets = [], []
    for row in obs:
        idx = int(row.get("signal_index") or 0)
        if idx >= len(atr) or atr[idx] is None:
            continue
        if atr[idx] >= med:
            high_rets.append(row.get("return"))
        else:
            low_rets.append(row.get("return"))
    if len(high_rets) < MIN_INDEPENDENT_EVENTS or len(low_rets) < MIN_INDEPENDENT_EVENTS:
        return False, {}
    t_hi = _newey_west_t(high_rets)
    t_lo = _newey_west_t(low_rets)
    m_hi, m_lo = _mean(high_rets), _mean(low_rets)
    hi_ok = bool(m_hi is not None and m_hi > 0 and t_hi >= 1.64)
    lo_ok = bool(m_lo is not None and m_lo > 0 and t_lo >= 1.64)
    conditional = (hi_ok and not lo_ok) or (lo_ok and not hi_ok)
    return conditional, {
        "atr_median": med,
        "high_vol": {"n": len(high_rets), "mean": m_hi, "t": t_hi, "ok": hi_ok},
        "low_vol": {"n": len(low_rets), "mean": m_lo, "t": t_lo, "ok": lo_ok},
    }


def _trade_observation(candles, signal_i, horizon, direction, mapping):
    rows = candles or []
    if mapping == "delayed_confirmation":
        entry_i = signal_i + 2
    else:
        entry_i = signal_i + 1
    exit_i = entry_i + int(horizon) - 1
    if entry_i >= len(rows) or exit_i >= len(rows):
        return None
    signal_close = _finite(rows[signal_i].get("close"))
    if mapping == "pullback_limit":
        if signal_close is None:
            return None
        hi = _finite(rows[entry_i].get("high"))
        lo = _finite(rows[entry_i].get("low"))
        if hi is None or lo is None or not (lo <= signal_close <= hi):
            return None
        entry = signal_close
    else:
        entry = _finite(rows[entry_i].get("open"))
    exit_price = _finite(rows[exit_i].get("close"))
    if not entry or exit_price is None:
        return None
    ret = float(direction) * (exit_price / entry - 1.0)
    highs = [_finite(rows[j].get("high")) for j in range(entry_i, exit_i + 1)]
    lows = [_finite(rows[j].get("low")) for j in range(entry_i, exit_i + 1)]
    highs = [x for x in highs if x is not None]
    lows = [x for x in lows if x is not None]
    if direction > 0:
        mfe = (max(highs) / entry - 1.0) if highs else ret
        mae = (min(lows) / entry - 1.0) if lows else ret
    else:
        mfe = (1.0 - min(lows) / entry) if lows else ret
        mae = (1.0 - max(highs) / entry) if highs else ret
    return {"return": ret, "mfe": mfe, "mae": mae,
            "signal_index": signal_i, "entry_index": entry_i, "exit_index": exit_i}


def _newey_west_t(xs):
    vals = [_finite(x) for x in (xs or [])]
    vals = [x for x in vals if x is not None]
    n = len(vals)
    if n < 3:
        return 0.0
    mu = sum(vals) / float(n)
    dev = [x - mu for x in vals]
    lag = max(1, min(6, int(n ** 0.25)))
    long_var = sum(x * x for x in dev) / float(n)
    for k in range(1, lag + 1):
        cov = sum(dev[i] * dev[i - k] for i in range(k, n)) / float(n)
        long_var += 2.0 * (1.0 - k / float(lag + 1)) * cov
    se = math.sqrt(max(long_var, 0.0) / float(n))
    return mu / se if se > 1e-12 else 0.0


def _two_sample_t(a, b):
    a = [x for x in [_finite(v) for v in (a or [])] if x is not None]
    b = [x for x in [_finite(v) for v in (b or [])] if x is not None]
    if len(a) < 12 or len(b) < 20:
        return 0.0
    ma, mb = _mean(a), _mean(b)
    va = sum((x - ma) ** 2 for x in a) / float(max(1, len(a) - 1))
    vb = sum((x - mb) ** 2 for x in b) / float(max(1, len(b) - 1))
    se = math.sqrt(va / len(a) + vb / len(b))
    return (ma - mb) / se if se > 1e-12 else 0.0


def _control_absolute_moves(candles, event_mask, horizon, mapping, gap_bars,
                            maximum=400):
    if not candles:
        return []
    candidates, last = [], -10 ** 9
    for i in range(80, min(len(event_mask or []), len(candles))):
        if event_mask[i] or i - last <= int(gap_bars):
            continue
        candidates.append(i)
        last = i
    if len(candidates) > int(maximum):
        step = len(candidates) / float(maximum)
        candidates = [candidates[int(k * step)] for k in range(int(maximum))]
    out = []
    for i in candidates:
        row = _trade_observation(candles, i, horizon, 1, mapping)
        if row is not None:
            out.append(abs(float(row.get("return") or 0.0)))
    return out


def _block_bootstrap_ci(xs, seed_text="probe", samples=240):
    vals = [_finite(x) for x in (xs or [])]
    vals = [x for x in vals if x is not None]
    n = len(vals)
    if n < 4:
        return [None, None]
    seed = int(hashlib.sha1(str(seed_text).encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed)
    block = max(2, int(math.sqrt(n)))
    means = []
    for _ in range(int(samples)):
        draw = []
        while len(draw) < n:
            start = rng.randint(0, max(0, n - block))
            draw.extend(vals[start:start + block])
        means.append(sum(draw[:n]) / float(n))
    means.sort()
    return [means[int(0.05 * (len(means) - 1))], means[int(0.95 * (len(means) - 1))]]


def _cost_scenarios(symbol, horizon):
    try:
        from . import edge_friction
        return edge_friction.cost_scenario_matrix(symbol=symbol, hold_bars=horizon)
    except Exception:
        return {
            "taker_taker": {"total_friction": 0.0015, "evidence": "fallback_estimate"},
            "maker_taker": {"total_friction": 0.0011, "evidence": "fallback_estimate"},
            "stress_threefold": {"total_friction": 0.0045, "evidence": "fallback_estimate"},
        }


def _evaluate_trial(candles, fallback_returns, event, horizon, direction, mapping,
                    symbol=None, timeframe=None):
    gap = _independence_gap_bars(horizon, timeframe=timeframe)
    merge = max(3, int(horizon or 1))
    raw, independent = _independent_events(
        event.get("mask") or [], gap, merge_bars=merge,
    )
    obs = []
    if candles:
        for i in independent:
            row = _trade_observation(candles, i, horizon, direction, mapping)
            if row is not None:
                obs.append(row)
    else:
        # Compatibility path: the label is real only for its source horizon.
        rr = list(fallback_returns or [])
        for i in independent:
            if i < len(rr) and _finite(rr[i]) is not None:
                obs.append({"return": direction * float(rr[i]), "mfe": None, "mae": None,
                            "signal_index": i, "entry_index": i, "exit_index": i})
    rets = [x["return"] for x in obs]
    mfes = [x["mfe"] for x in obs if x.get("mfe") is not None]
    maes = [x["mae"] for x in obs if x.get("mae") is not None]
    gross = _mean(rets)
    ci = _block_bootstrap_ci(rets, "%s:%s:%s:%s" % (
        event.get("event_id"), horizon, direction, mapping))
    t = _newey_west_t(rets)
    event_abs = [abs(float(x)) for x in rets]
    control_abs = _control_absolute_moves(
        candles, event.get("mask") or [], horizon, mapping, gap,
    )
    volatility_t = _two_sample_t(event_abs, control_abs)
    volatility_effect = bool(
        len(event_abs) >= MIN_INDEPENDENT_EVENTS and len(control_abs) >= 20 and
        (_mean(event_abs) or 0.0) > (_mean(control_abs) or 0.0) and volatility_t >= 1.64
    )
    costs = _cost_scenarios(symbol, horizon)
    scenario_rows = {}
    for name, pack in costs.items():
        total = float((pack or {}).get("total_friction") or 0.0)
        scenario_rows[name] = dict(pack or {})
        scenario_rows[name]["mean_net"] = None if gross is None else gross - total
        scenario_rows[name]["efr"] = None if gross is None or total <= 0 else gross / total
    primary = "maker_taker" if mapping == "pullback_limit" else "taker_taker"
    pp = scenario_rows.get(primary) or {}
    n = len(rets)
    statistical = bool(n >= MIN_INDEPENDENT_EVENTS and gross is not None and gross > 0 and t >= 1.64)
    economic = bool(gross is not None and gross > 0 and (_mean(mfes) or 0.0) >=
                    float((costs.get("maker_taker") or {}).get("total_friction") or 0.0))
    execution = bool((pp.get("mean_net") or -1.0) > 0 and (pp.get("efr") or 0.0) >= 1.20)
    regime_conditional, regime_pack = _regime_split_conditional(candles, obs)
    if n < MIN_INDEPENDENT_EVENTS:
        state = "SAMPLE_INADEQUATE"
    elif statistical and economic and execution:
        state = "READY_FOR_ASSEMBLY"
    elif statistical and economic:
        state = "EXECUTION_MAPPING_FAILURE"
    elif regime_conditional and (statistical or (gross is not None and gross > 0 and t >= 1.0)):
        state = "STATE_CONDITIONAL"
    elif statistical:
        state = "DIRECTIONAL_BUT_SMALL"
    elif volatility_effect:
        state = "VOLATILITY_EFFECT_ONLY"
    elif gross is not None and gross > 0 and (t >= 1.0 or ((pp.get("mean_net") or -1) > -0.0005)):
        state = "NEAR_MISS_DIAGNOSTIC"
    else:
        state = "NO_DIRECTIONAL_EFFECT"
    mean_mfe = _mean(mfes)
    mean_mae = _mean(maes)
    failure_codes = _derive_failure_codes(
        state, gross, t, statistical, economic, execution, volatility_effect,
        mean_mfe, mean_mae, primary, len(raw), len(independent), n,
    )
    return {
        "ok": True,
        "passed": state == "READY_FOR_ASSEMBLY",
        "research_state": state,
        "research_state_zh": RESEARCH_STATES_ZH[state],
        "failure_codes": failure_codes,
        "regime_split": regime_pack,
        "event_id": event.get("event_id"),
        "event_kind": event.get("kind"),
        "terms": event.get("terms"),
        "factor": ((event.get("terms") or [{}])[0]).get("factor"),
        "side": ((event.get("terms") or [{}])[0]).get("side"),
        "q": ((event.get("terms") or [{}])[0]).get("q"),
        "horizon_bars": int(horizon),
        "trade_direction": "long" if direction > 0 else "short",
        "execution_mapping": mapping,
        "n_raw_triggers": len(raw),
        "n_event_clusters": len(independent),
        "n_independent_events": len(independent),
        "independence_gap_bars": gap,
        "independence_merge_bars": merge,
        "n_filled_events": n,
        "effective_sample_size": n,
        "mean_hit": gross,
        "mean_net": pp.get("mean_net"),
        "mean_mfe": mean_mfe,
        "mean_mae": mean_mae,
        "hac_t_stat": t,
        "volatility_effect_t_stat": volatility_t,
        "mean_event_absolute_move": _mean(event_abs),
        "mean_control_absolute_move": _mean(control_abs),
        "bootstrap_mean_ci_80": ci,
        "evidence_axes": {
            "statistical_direction": statistical,
            "economic_magnitude": economic,
            "execution_feasibility": execution,
            "volatility_effect": volatility_effect,
        },
        "cost_scenarios": scenario_rows,
        "primary_cost_scenario": primary,
        "trade_returns": rets[:300],
    }


def evaluate_naked_probe(factor_values, fwd_returns, side="high", q=0.8,
                         hold_bars=None, horizons=DEFAULT_HORIZONS,
                         round_trip_cost=0.001, candles=None, symbol=None,
                         direction=1, execution_mapping="next_bar_open"):
    """Compatibility API backed by the new real-horizon evaluator."""
    event = {"event_id": "single_factor_%s_q%s" % (side, int(q * 100)),
             "terms": [{"factor": "factor", "side": side, "q": q}],
             "mask": _causal_quantile_mask(factor_values, side, q),
             "kind": "single_proxy"}
    rows = [_evaluate_trial(candles, fwd_returns, event, h, direction,
                            execution_mapping, symbol=symbol) for h in horizons]
    rows.sort(key=lambda x: (1 if x.get("passed") else 0,
                             float(x.get("mean_net") or -1e9)), reverse=True)
    best = rows[0] if rows else {"ok": False, "passed": False,
                                "research_state": "SAMPLE_INADEQUATE"}
    best["side"] = side
    best["q"] = q
    best["round_trip_cost"] = round_trip_cost
    best["horizon_results"] = [{k: v for k, v in r.items() if k != "trade_returns"}
                                for r in rows]
    return best


def probe_hypothesis(hypothesis, factor_matrix, fwd_returns, round_trip_cost=0.001,
                     candles=None, symbol=None, timeframe=None, max_trials=18):
    """Search a bounded set of mechanism-preserving minimal probes.

    A failed leaf is diagnostic evidence only.  This function never declares a
    mechanism family contradicted or exhausted; that requires aggregate coverage.
    """
    matrix = factor_matrix or {}
    specs, available = _candidate_events(hypothesis, matrix, max_specs=max_trials)
    requested = list((hypothesis or {}).get("factor_hints") or
                     (hypothesis or {}).get("observable_proxy") or [])
    if requested and not available:
        return {
            "ok": True, "passed": False, "hypothesis_id": hypothesis.get("hypothesis_id"),
            "research_state": "PROXY_INADEQUATE",
            "research_state_zh": RESEARCH_STATES_ZH["PROXY_INADEQUATE"],
            "n_probes": 0, "best": None, "probes": [],
            "missing_proxies": requested, "available_proxies": sorted(matrix.keys()),
            "family_closed": False, "at": _now(),
        }
    required_data = list((hypothesis or {}).get("required_data") or [])
    # Snapshots from microstructure_bridge count as partial book/flow evidence.
    micro_ok = any(
        str(k).startswith("micro_") for k in (matrix or {}).keys()
    ) or bool((hypothesis or {}).get("micro_data_available"))
    allowed_data = {
        "ohlcv", "ohlcv_swap_candles", "derived_ohlcv_proxy", "derived_factors",
    }
    if micro_ok:
        allowed_data.update({
            "level2_order_book", "level2_order_book_snapshot",
            "trade_side_flow", "trade_side_flow_snapshot",
            "microstructure_forward_samples",
        })
    unavailable = [x for x in required_data if x not in allowed_data]
    # Keep true derivatives feeds as hard blocks even when book snapshots exist.
    hard_missing = [
        x for x in unavailable
        if x in ("open_interest", "liquidation_flow", "historical_funding",
                 "cross_exchange_basis", "historical_l2_replay")
    ]
    if hard_missing:
        return {
            "ok": True, "passed": False, "hypothesis_id": hypothesis.get("hypothesis_id"),
            "research_state": "DATA_INADEQUATE",
            "research_state_zh": RESEARCH_STATES_ZH["DATA_INADEQUATE"],
            "failure_codes": ["data_insufficient"],
            "n_probes": 0, "best": None, "probes": [], "missing_data": hard_missing,
            "family_closed": False, "at": _now(),
        }
    if unavailable and not micro_ok:
        return {
            "ok": True, "passed": False, "hypothesis_id": hypothesis.get("hypothesis_id"),
            "research_state": "DATA_INADEQUATE",
            "research_state_zh": RESEARCH_STATES_ZH["DATA_INADEQUATE"],
            "failure_codes": ["data_insufficient"],
            "n_probes": 0, "best": None, "probes": [], "missing_data": unavailable,
            "family_closed": False, "at": _now(),
        }

    rows = []
    mappings = ("next_bar_open", "delayed_confirmation", "pullback_limit")
    horizons = DEFAULT_HORIZONS if candles else (3,)
    budget = max(1, int(max_trials))
    # Round-robin coverage: first cover distinct event definitions, then horizons,
    # then alternate execution mappings.  A small budget therefore stays diverse.
    directions = _direction_candidates(hypothesis)
    plan = []
    preferred_horizons = [h for h in (3, 1, 6, 12) if h in horizons]
    dimension_cycle = []
    # Latin-style order gives a tiny budget immediate horizon/mapping breadth.
    max_len = max(len(preferred_horizons), len(mappings))
    for offset in range(max_len * max_len):
        dimension_cycle.append((
            preferred_horizons[offset % len(preferred_horizons)],
            mappings[(offset + offset // len(preferred_horizons)) % len(mappings)],
        ))
    for round_i in range(len(dimension_cycle)):
        for spec_i, spec in enumerate(specs):
            horizon, mapping = dimension_cycle[(spec_i + round_i) % len(dimension_cycle)]
            direction = directions[(spec_i + round_i) % len(directions)]
            plan.append((spec, horizon, direction, mapping))
    # If direction is not fixed by the hypothesis, also test the opposite direction.
    if len(directions) > 1:
        for horizon in preferred_horizons:
            for spec_i, spec in enumerate(specs):
                plan.append((spec, horizon, directions[(spec_i + 1) % len(directions)],
                             "next_bar_open"))
    for spec, horizon, direction, mapping in plan[:budget]:
        rows.append(_evaluate_trial(
            candles, fwd_returns, spec, horizon, direction, mapping,
            symbol=symbol, timeframe=timeframe,
        ))

    # Horizon mismatch vs hypothesis statement band (not search-order bias).
    preferred_band = _parse_hypothesis_horizon_bars(hypothesis, timeframe=timeframe)
    by_key = {}
    for row in rows:
        key = (row.get("event_id"), row.get("trade_direction"), row.get("execution_mapping"))
        by_key.setdefault(key, []).append(row)
    for group in by_key.values():
        if len(group) < 2:
            continue
        good = []
        weak_pref = []
        for row in group:
            axes = row.get("evidence_axes") or {}
            h = int(row.get("horizon_bars") or 0)
            strong = bool(
                axes.get("statistical_direction") or (
                    row.get("mean_hit") is not None and float(row.get("mean_hit") or 0) > 0
                    and float(row.get("hac_t_stat") or 0) >= 1.64
                )
            )
            if strong:
                good.append(row)
            if h in preferred_band and (
                axes.get("statistical_direction")
                or (row.get("mean_hit") is not None and float(row.get("mean_hit") or 0) > 0
                    and float(row.get("hac_t_stat") or 0) >= 1.0)
            ):
                weak_pref.append(row)
        if not good:
            continue
        good_h = set(int(r.get("horizon_bars") or 0) for r in good)
        if good_h and preferred_band and good_h.isdisjoint(preferred_band) and not weak_pref:
            pick = max(good, key=lambda r: float(r.get("hac_t_stat") or 0))
            if pick.get("research_state") not in (
                "READY_FOR_ASSEMBLY", "EXECUTION_MAPPING_FAILURE",
            ):
                pick["research_state"] = "HORIZON_MISMATCH"
                pick["research_state_zh"] = RESEARCH_STATES_ZH["HORIZON_MISMATCH"]
                codes = list(pick.get("failure_codes") or [])
                if "horizon_mismatch" not in codes:
                    codes.append("horizon_mismatch")
                pick["failure_codes"] = codes

    def rank(row):
        state_rank = {
            "READY_FOR_ASSEMBLY": 7, "EXECUTION_MAPPING_FAILURE": 6,
            "DIRECTIONAL_BUT_SMALL": 5, "NEAR_MISS_DIAGNOSTIC": 4,
            "STATE_CONDITIONAL": 3, "HORIZON_MISMATCH": 3,
            "MECHANISM_CONTRADICTED": 1,
            "VOLATILITY_EFFECT_ONLY": 2,
            "NO_DIRECTIONAL_EFFECT": 0, "SAMPLE_INADEQUATE": -1,
        }
        return (state_rank.get(row.get("research_state"), -1),
                float(row.get("mean_net") or -1e9),
                int(row.get("n_independent_events") or 0))
    ordered = sorted(rows, key=rank, reverse=True)
    best = ordered[0] if ordered else None
    passed = bool(best and best.get("passed"))
    state = (best or {}).get("research_state") or "SAMPLE_INADEQUATE"

    # Hypothesis-level contradiction under leaf coverage (NOT family exhaustion).
    event_n = len(set(r.get("event_id") for r in rows if r.get("event_id")))
    map_n = len(set(r.get("execution_mapping") for r in rows if r.get("execution_mapping")))
    hor_n = len(set(r.get("horizon_bars") for r in rows if r.get("horizon_bars") is not None))
    evaluable = [
        r for r in rows
        if r.get("research_state") not in (
            "SAMPLE_INADEQUATE", "PROXY_INADEQUATE", "DATA_INADEQUATE",
        )
    ]
    dead = [
        r for r in evaluable
        if r.get("research_state") in ("NO_DIRECTIONAL_EFFECT", "MECHANISM_CONTRADICTED")
    ]
    nearish = [
        r for r in evaluable
        if r.get("research_state") in (
            "NEAR_MISS_DIAGNOSTIC", "DIRECTIONAL_BUT_SMALL", "VOLATILITY_EFFECT_ONLY",
            "EXECUTION_MAPPING_FAILURE", "STATE_CONDITIONAL", "HORIZON_MISMATCH",
            "READY_FOR_ASSEMBLY",
        )
    ]
    if (
        not passed
        and event_n >= 2 and map_n >= 2 and hor_n >= 2
        and len(evaluable) >= 6
        and len(dead) >= max(4, int(0.75 * len(evaluable)))
        and not nearish
        and state in ("NO_DIRECTIONAL_EFFECT", "SAMPLE_INADEQUATE")
    ):
        state = "MECHANISM_CONTRADICTED"
        if best is not None:
            best["research_state"] = state
            best["research_state_zh"] = RESEARCH_STATES_ZH[state]
            codes = list(best.get("failure_codes") or [])
            if "mechanism_contradicted" not in codes:
                codes.append("mechanism_contradicted")
            best["failure_codes"] = codes

    event_kinds = sorted(set(r.get("event_kind") for r in rows if r.get("event_kind")))
    coverage = {
        "event_definitions_tested": event_n,
        "event_kinds_tested": event_kinds,
        "horizons_tested": sorted(set(r.get("horizon_bars") for r in rows)),
        "execution_mappings_tested": sorted(set(r.get("execution_mapping") for r in rows)),
        "directions_tested": sorted(set(r.get("trade_direction") for r in rows)),
        "independent_events_max": max([int(r.get("n_independent_events") or 0) for r in rows] or [0]),
        "raw_triggers_max": max([int(r.get("n_raw_triggers") or 0) for r in rows] or [0]),
        "independence_gap_bars": _independence_gap_bars(
            preferred_horizons[0] if preferred_horizons else 3, timeframe=timeframe,
        ),
        "trial_budget_used": len(rows),
        "trial_budget_limit": budget,
        "leaf_coverage_enough_for_contradiction": bool(state == "MECHANISM_CONTRADICTED"),
        "sufficient_to_close_family": False,
    }
    return {
        "ok": True,
        "passed": passed,
        "hypothesis_id": hypothesis.get("hypothesis_id"),
        "research_state": state,
        "research_state_zh": RESEARCH_STATES_ZH.get(state, state),
        "failure_codes": list((best or {}).get("failure_codes") or []),
        "n_probes": len(rows),
        "best": best,
        "probes": [{k: v for k, v in r.items() if k != "trade_returns"} for r in ordered],
        "coverage": coverage,
        "family_closed": False,
        "hard_rule_zh": "三轴未同时通过时禁止组装；单个窄探针失败不得宣判整个机制族死亡。",
        "at": _now(),
    }


def probe():
    return {
        "ok": True,
        "provider": "mechanism_preserving_probe_v2",
        "forbids": ["stop_loss", "take_profit", "position_sizing", "complex_exit"],
        "evidence_axes": ["统计方向", "经济幅度", "执行可行性"],
        "research_states": RESEARCH_STATES_ZH,
        "uses_real_horizons": True,
        "clusters_independent_events": True,
        "family_close_from_single_probe": False,
        "at": _now(),
    }
