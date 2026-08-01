# -*- coding: utf-8 -*-
"""Automatic antifalsification — negative controls, placebos, competing explanations.

Outputs an evidence matrix; NEVER claims causal proof.
"""
from __future__ import print_function

import math
import random
from datetime import datetime

from . import creation_alphalens_lite as al
from . import probe_protocol as pp


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _shift(series, k):
    n = len(series or [])
    if n == 0:
        return []
    k = int(k) % n
    return list(series[-k:] + series[:-k])


def _permute_block(series, block=8, rng=None):
    rng = rng or random.Random(0)
    xs = list(series or [])
    if len(xs) < block * 2:
        ys = list(xs)
        rng.shuffle(ys)
        return ys
    blocks = [xs[i:i + block] for i in range(0, len(xs), block)]
    rng.shuffle(blocks)
    out = []
    for b in blocks:
        out.extend(b)
    return out[: len(xs)]


def _sign_flip(series):
    return [(-float(v) if v is not None else None) for v in (series or [])]


def _effect(factor_values, fwd):
    return al.causal_pre_post(factor_values, fwd)


def _probe_net(factor_values, fwd, side="high", candles=None, symbol=None,
               timeframe=None, horizon=3, trade_direction="long",
               execution_mapping="next_bar_open"):
    """Evaluate every falsification probe under the admitted trial identity.

    The former compatibility call silently fell back to ``long`` and searched
    the default horizon set.  That made a short/horizon-specific candidate face
    controls produced by a different experiment.  Keep the full identity on
    the base, controls, placebos, and competing explanations alike.
    """
    direction = -1 if str(trade_direction or "long").lower() == "short" else 1
    return pp.evaluate_naked_probe(
        factor_values,
        fwd,
        side=side,
        candles=candles,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        execution_mapping=execution_mapping or "next_bar_open",
        horizons=(int(horizon or 3),),
    )


def _exact_event_trial(mask, fwd_returns, candles, symbol, timeframe, horizon,
                       trade_direction, execution_mapping, event_id):
    direction = -1 if str(trade_direction or "long").lower() == "short" else 1
    return pp._evaluate_trial(
        candles,
        fwd_returns,
        {
            "event_id": event_id,
            "kind": "human_contract_exact",
            "terms": [{"factor": "precomputed_contract_event"}],
            "mask": [bool(value) for value in (mask or [])],
        },
        int(horizon or 3),
        direction,
        execution_mapping or "next_bar_open",
        symbol=symbol,
        timeframe=timeframe,
    )


def _run_exact_event_battery(event_mask, fwd_returns, seed, factor_matrix,
                             candles, symbol, timeframe, horizon,
                             trade_direction, execution_mapping):
    """Antifalsify an already compiled event without re-quantiling it."""
    rng = random.Random(int(seed))

    def trial(mask, name):
        return _exact_event_trial(
            mask, fwd_returns, candles, symbol, timeframe, horizon,
            trade_direction, execution_mapping, name,
        )

    base_probe = trial(event_mask, "exact_contract_base")
    base_net = float(base_probe.get("mean_net") or 0.0)
    base_t = float(base_probe.get("hac_t_stat") or 0.0)
    controls = [
        ("time_shift_plus_5", _shift(event_mask, 5), "negative_control"),
        ("time_shift_minus_5", _shift(event_mask, -5), "negative_control"),
        ("time_shift_plus_12", _shift(event_mask, 12), "negative_control"),
        ("block_permute", _permute_block(event_mask, block=10, rng=rng), "negative_control"),
        ("block_permute_24", _permute_block(event_mask, block=24, rng=rng), "placebo"),
    ]
    shuffled = list(bool(x) for x in (event_mask or []))
    rng.shuffle(shuffled)
    controls.append(("event_location_placebo", shuffled, "placebo"))
    rows = []
    for name, mask, family in controls:
        result = trial(mask, name)
        net = float(result.get("mean_net") or 0.0)
        t_stat = float(result.get("hac_t_stat") or 0.0)
        if base_net > net and abs(base_t) >= abs(t_stat):
            verdict = "support"
        elif net >= base_net and abs(t_stat) >= abs(base_t):
            verdict = "oppose"
        else:
            verdict = "neutral"
        rows.append({
            "test": name,
            "family": family,
            "support": verdict,
            "mean_net": net,
            "t_stat": t_stat,
            "event_count": result.get("n_independent_events"),
        })

    returns = list(base_probe.get("trade_returns") or [])
    if len(returns) >= 8:
        middle = len(returns) // 2
        mean_a = sum(returns[:middle]) / float(max(middle, 1))
        mean_b = sum(returns[middle:]) / float(max(len(returns) - middle, 1))
        rows.append({
            "test": "trade_path_half_stability",
            "family": "stability",
            "support": "support" if mean_a > 0 and mean_b > 0 else (
                "oppose" if mean_a * mean_b < 0 else "neutral"
            ),
            "half_a": mean_a,
            "half_b": mean_b,
            "mean_net": (mean_a + mean_b) / 2.0,
            "t_stat": None,
        })

    for cname in ("atr_pct_14", "range_pct", "abs_ret_1", "ret_12", "volume_z"):
        cseries = (factor_matrix or {}).get(cname) or []
        if not cseries:
            continue
        competitor = _probe_net(
            cseries, fwd_returns, side="high", candles=candles,
            symbol=symbol, timeframe=timeframe, horizon=horizon,
            trade_direction=trade_direction,
            execution_mapping=execution_mapping,
        )
        cnet = float((competitor or {}).get("mean_net") or 0.0)
        rows.append({
            "test": "compete_%s" % cname,
            "family": "competing_explanation",
            "support": "oppose" if cnet >= base_net and cnet > 0 else (
                "support" if base_net > cnet else "neutral"
            ),
            "mean_net": cnet,
            "t_stat": competitor.get("hac_t_stat") or competitor.get("t_stat"),
            "competitor": cname,
        })
        if sum(1 for row in rows if row.get("family") == "competing_explanation") >= 4:
            break

    def bucket(family):
        subset = [row for row in rows if row.get("family") == family]
        return {
            "support": sum(1 for row in subset if row.get("support") == "support"),
            "neutral": sum(1 for row in subset if row.get("support") == "neutral"),
            "oppose": sum(1 for row in subset if row.get("support") == "oppose"),
            "tests": subset,
        }

    matrix = {
        "mechanism_consistency": {
            "support": 1 if base_probe.get("passed") else 0,
            "neutral": 0 if base_probe.get("passed") else 1,
            "oppose": 0,
            "note_zh": "直接使用已编译事件掩码，未重新做分位数解释。",
        },
        "negative_control": bucket("negative_control"),
        "placebo": bucket("placebo"),
        "stability": bucket("stability"),
        "competing_explanation": bucket("competing_explanation"),
        "cost_after_edge": {
            "support": 1 if base_net > 0 else 0,
            "neutral": 0,
            "oppose": 0 if base_net > 0 else 1,
            "mean_net": base_net,
        },
    }
    oppose_n = sum(int(matrix[key].get("oppose") or 0) for key in (
        "negative_control", "placebo", "stability", "competing_explanation", "cost_after_edge",
    ))
    support_n = sum(int(matrix[key].get("support") or 0) for key in (
        "mechanism_consistency", "negative_control", "placebo", "stability",
        "competing_explanation", "cost_after_edge",
    ))
    passed = bool(base_net > 0 and support_n > oppose_n and oppose_n <= 3)
    return {
        "ok": True,
        "schema": "qiyu_antifalsify_evidence_matrix_v2",
        "passed": passed,
        "causal_claim": False,
        "credibility": "elevated_if_pass_not_proven",
        "precomputed_event_mask": True,
        "event_mask_requantiled": False,
        "event_count": sum(1 for value in (event_mask or []) if value),
        "base_probe": {
            key: value for key, value in base_probe.items()
            if key not in ("trade_returns", "pbo_bar_returns", "event_mask")
        },
        "base_effect": {"mean_net": base_net, "t_stat": base_t},
        "evidence_matrix": matrix,
        "rows": rows,
        "support_n": support_n,
        "oppose_n": oppose_n,
        "note_zh": "精确事件按原布尔掩码、原周期、原方向和原执行映射反证；未转换成通用分位事件。",
        "at": _now(),
    }


def run_antifalsify_battery(factor_values, fwd_returns, side="high", seed=7,
                            competing_factors=None, factor_matrix=None,
                            precomputed_event_mask=False, candles=None,
                            symbol=None, timeframe=None, horizon=3,
                            trade_direction="long", execution_mapping="next_bar_open"):
    """Build evidence matrix for one signal series."""
    if precomputed_event_mask:
        return _run_exact_event_battery(
            factor_values, fwd_returns, seed, factor_matrix,
            candles, symbol, timeframe, horizon,
            trade_direction, execution_mapping,
        )
    rng = random.Random(int(seed))
    identity = {
        "candles": candles,
        "symbol": symbol,
        "timeframe": timeframe,
        "horizon": horizon,
        "trade_direction": trade_direction,
        "execution_mapping": execution_mapping,
    }
    base_probe = _probe_net(factor_values, fwd_returns, side=side, **identity)
    base_eff = _effect(factor_values, fwd_returns)
    base_net = float((base_probe or {}).get("mean_net") or 0.0)
    base_t = float((base_eff or {}).get("t_stat") or (base_probe or {}).get("t_stat") or 0.0)

    rows = []

    # Negative controls
    for name, series in (
        ("time_shift_plus_5", _shift(factor_values, 5)),
        ("time_shift_minus_5", _shift(factor_values, -5)),
        ("time_shift_plus_12", _shift(factor_values, 12)),
        ("sign_flip", _sign_flip(factor_values)),
        ("block_permute", _permute_block(factor_values, block=10, rng=rng)),
        ("block_permute_24", _permute_block(factor_values, block=24, rng=rng)),
    ):
        p = _probe_net(series, fwd_returns, side=side, **identity)
        e = _effect(series, fwd_returns)
        net = float((p or {}).get("mean_net") or 0.0)
        # original should beat negative control
        support = base_net > net and abs(base_t) >= abs(float((e or {}).get("t_stat") or 0))
        rows.append({
            "test": name,
            "family": "negative_control",
            "support": "support" if support else ("oppose" if net >= base_net and abs(float((e or {}).get("t_stat") or 0)) >= abs(base_t) else "neutral"),
            "mean_net": net,
            "t_stat": (e or {}).get("t_stat"),
        })

    # Lag-1 factor should not dominate (leakage / look-ahead smell)
    lag1 = [None] + list(factor_values or [])[:-1]
    p_lag = _probe_net(lag1, fwd_returns, side=side, **identity)
    lag_net = float((p_lag or {}).get("mean_net") or 0.0)
    rows.append({
        "test": "lag1_factor_dominance",
        "family": "negative_control",
        "support": "support" if base_net > lag_net else ("oppose" if lag_net > base_net * 1.05 else "neutral"),
        "mean_net": lag_net,
        "t_stat": (p_lag or {}).get("t_stat"),
    })

    # Placebo: shuffle fwd labels in blocks (keep autocorr-ish)
    fwd_placebo = _permute_block(fwd_returns, block=12, rng=rng)
    p = _probe_net(factor_values, fwd_placebo, side=side, **identity)
    e = _effect(factor_values, fwd_placebo)
    rows.append({
        "test": "fwd_block_placebo",
        "family": "placebo",
        "support": (
            "support" if float((p or {}).get("mean_net") or 0) < base_net * 0.5 else "oppose"
        ),
        "mean_net": (p or {}).get("mean_net"),
        "t_stat": (e or {}).get("t_stat"),
    })
    # Calendar / stride placebo: take every k-th label scrambled
    fwd_stride = list(fwd_returns or [])
    if len(fwd_stride) >= 40:
        odd = fwd_stride[1::2]
        even = fwd_stride[0::2]
        rng.shuffle(odd)
        merged = []
        for i in range(len(fwd_stride)):
            if i % 2 == 0:
                merged.append(even[i // 2] if i // 2 < len(even) else None)
            else:
                merged.append(odd[i // 2] if i // 2 < len(odd) else None)
        p2 = _probe_net(factor_values, merged, side=side, **identity)
        rows.append({
            "test": "calendar_stride_placebo",
            "family": "placebo",
            "support": (
                "support" if float((p2 or {}).get("mean_net") or 0) < base_net * 0.55 else "oppose"
            ),
            "mean_net": (p2 or {}).get("mean_net"),
            "t_stat": (p2 or {}).get("t_stat"),
        })

    # Rolling half-sample stability (not causal proof)
    n = min(len(factor_values or []), len(fwd_returns or []))
    if n >= 80:
        mid = n // 2
        f = list(factor_values or [])[-n:]
        r = list(fwd_returns or [])[-n:]
        # Align the candle slices with each half.  Passing the complete candle
        # array for the second half would preserve labels in metadata only while
        # actually replaying prices from the first half.
        half_identity_a = dict(identity)
        half_identity_b = dict(identity)
        if candles:
            aligned_candles = list(candles or [])[-n:]
            half_identity_a["candles"] = aligned_candles[:mid]
            half_identity_b["candles"] = aligned_candles[mid:]
        p_a = _probe_net(f[:mid], r[:mid], side=side, **half_identity_a)
        p_b = _probe_net(f[mid:], r[mid:], side=side, **half_identity_b)
        net_a = float((p_a or {}).get("mean_net") or 0.0)
        net_b = float((p_b or {}).get("mean_net") or 0.0)
        both_pos = net_a > 0 and net_b > 0
        rows.append({
            "test": "half_sample_stability",
            "family": "stability",
            "support": "support" if both_pos else ("oppose" if net_a * net_b < 0 else "neutral"),
            "mean_net": (net_a + net_b) / 2.0,
            "t_stat": None,
            "half_a": net_a,
            "half_b": net_b,
        })

    # Competing explanations: if competing factor explains more, mark oppose
    competing_factors = list(competing_factors or [])
    if not competing_factors and factor_matrix:
        for cname in ("atr_pct_14", "range_pct", "abs_ret_1", "ret_12", "volume_z"):
            if cname in factor_matrix:
                competing_factors.append(cname)
    for cname in competing_factors[:4]:
        cseries = (factor_matrix or {}).get(cname) or []
        if not cseries:
            continue
        cp = _probe_net(cseries, fwd_returns, side=side, **identity)
        cnet = float((cp or {}).get("mean_net") or 0.0)
        # residual proxy: original net should exceed competitor
        if cnet >= base_net and cnet > 0:
            verdict = "oppose"
        elif base_net > cnet:
            verdict = "support"
        else:
            verdict = "neutral"
        rows.append({
            "test": "compete_%s" % cname,
            "family": "competing_explanation",
            "support": verdict,
            "mean_net": cnet,
            "t_stat": (cp or {}).get("t_stat"),
            "competitor": cname,
        })

    # Evidence matrix summary
    def _bucket(family):
        sub = [r for r in rows if r.get("family") == family]
        return {
            "support": sum(1 for r in sub if r.get("support") == "support"),
            "neutral": sum(1 for r in sub if r.get("support") == "neutral"),
            "oppose": sum(1 for r in sub if r.get("support") == "oppose"),
            "tests": sub,
        }

    matrix = {
        "mechanism_consistency": {
            "support": 1 if base_probe.get("passed") else 0,
            "neutral": 0 if base_probe.get("passed") else 1,
            "oppose": 0 if base_probe.get("passed") else 0,
            "note_zh": "裸探针方向/净优势是否存在（非因果证明）",
        },
        "negative_control": _bucket("negative_control"),
        "placebo": _bucket("placebo"),
        "stability": _bucket("stability"),
        "competing_explanation": _bucket("competing_explanation"),
        "cost_after_edge": {
            "support": 1 if base_net > 0 else 0,
            "neutral": 0,
            "oppose": 0 if base_net > 0 else 1,
            "mean_net": base_net,
        },
    }
    oppose_n = sum(
        int((matrix[k].get("oppose") or 0))
        for k in ("negative_control", "placebo", "stability",
                  "competing_explanation", "cost_after_edge")
    )
    support_n = sum(
        int((matrix[k].get("support") or 0))
        for k in ("mechanism_consistency", "negative_control", "placebo",
                  "stability", "competing_explanation", "cost_after_edge")
    )
    # Pass if more support than oppose AND cost edge positive — still NOT causal proof
    passed = bool(base_net > 0 and support_n > oppose_n and oppose_n <= 3)
    return {
        "ok": True,
        "schema": "qiyu_antifalsify_evidence_matrix_v1",
        "passed": passed,
        "causal_claim": False,
        "credibility": "elevated_if_pass_not_proven",
        "base_probe": {k: v for k, v in (base_probe or {}).items() if k != "trade_returns"},
        "base_effect": base_eff,
        "evidence_matrix": matrix,
        "rows": rows,
        "support_n": support_n,
        "oppose_n": oppose_n,
        "note_zh": (
            "多种替代解释未能推翻 → 提高可信度；仍不宣称完成因果证明。"
            if passed else
            "反证/安慰剂/竞争解释未通过；不得进入策略组装。"
        ),
        "at": _now(),
    }


def probe():
    return {
        "ok": True,
        "provider": "antifalsify_battery_v1",
        "causal_claim": False,
        "at": _now(),
    }
