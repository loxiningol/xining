# -*- coding: utf-8 -*-
"""Mechanism-preserving probe protocol used before strategy assembly.

The probe is intentionally simple: the production 0.9% protective stop and a
fixed-horizon close are mandatory; tuned take-profit/sizing/complex exits are
not allowed.  Unlike the legacy probe it does not confuse one failed factor
quantile with a dead mechanism family.  It records three independent evidence
axes (statistical, economic and execution), uses real forward horizons and
clusters adjacent signals into independent events.
"""
from __future__ import print_function

import bisect
import hashlib
import math
import os
import random
from datetime import datetime

from . import recipe_policy
from .creation_quality_doctrine import (
    MIN_INDEPENDENT_EVENTS as _DOCTRINE_MIN_EVENTS,
    MIN_WIN_RATE_EXCLUSIVE as _DOCTRINE_MIN_WR,
)


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
# Include denser (lower) quantiles so search can find distributed high-WR
# books instead of only rare 80/90-tail lottery breakouts.
# 更密分位：提高「每轮必有可交接候选」的搜索覆盖（仍须过胜率/反彩票底线）
DEFAULT_QUANTILES = (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90)
INTERSECTION_QUANTILES = (0.60, 0.70, 0.75, 0.80)
MIN_INDEPENDENT_EVENTS = _DOCTRINE_MIN_EVENTS  # 探针独立事件下限（与创造成交笔数对齐）
MIN_ASSEMBLY_WIN_RATE = _DOCTRINE_MIN_WR  # 组装就绪要求胜率严格大于 50%
CAUSAL_QUANTILE_WINDOW = 240
CAUSAL_QUANTILE_MIN_HISTORY = 80


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


def _causal_quantile_mask(values, side="high", q=0.8,
                          window=CAUSAL_QUANTILE_WINDOW,
                          min_history=CAUSAL_QUANTILE_MIN_HISTORY):
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
    locked = bool((hypothesis or {}).get("trade_direction_locked"))
    declared = str((hypothesis or {}).get("predicted_direction") or "").strip().lower()
    if locked and declared in ("long", "buy", "positive_shift"):
        return (1,)
    if locked and declared in ("short", "sell", "negative_shift"):
        return (-1,)
    text = " ".join([
        declared,
        str((hypothesis or {}).get("statement_zh") or ""),
        str((hypothesis or {}).get("family") or ""),
    ]).lower()
    if any(x in text for x in ("negative_shift", "short", "下行", "做空")):
        return (-1,)
    if any(x in text for x in ("positive_shift", "long", "上行", "回升", "bounce", "recovery")):
        return (1,)
    return (1, -1)


def _locked_trade_direction(hypothesis):
    """Return 'long'|'short'|None when trade direction is contract-locked."""
    if not bool((hypothesis or {}).get("trade_direction_locked")):
        return None
    declared = str((hypothesis or {}).get("predicted_direction") or "").strip().lower()
    if declared in ("long", "buy", "positive_shift"):
        return "long"
    if declared in ("short", "sell", "negative_shift"):
        return "short"
    signs = _direction_candidates(hypothesis)
    if signs == (1,):
        return "long"
    if signs == (-1,):
        return "short"
    return None


def _entry_regime(hypothesis):
    """Classify entry semantics for direction-legal factor sides."""
    blob = " ".join([
        str((hypothesis or {}).get("family") or ""),
        str((hypothesis or {}).get("mechanism_id") or ""),
        str((hypothesis or {}).get("statement_zh") or ""),
        str((hypothesis or {}).get("path") or ""),
    ]).lower()
    if any(x in blob for x in (
        "mean_reversion", "exhaust", "liquid", "衰竭", "清算", "panic",
        "reclaim", "oversold", "超卖", "反弹", "bounce", "recovery",
        "布林", "bollinger", "bb_lower",
    )):
        return "mean_reversion"
    if any(x in blob for x in (
        "break", "squeeze", "compression", "压缩", "扩张", "trend",
        "donchian", "pullback", "continuation", "突破", "顺势", "扩张",
    )):
        return "breakout"
    return "mixed"


# Factor sides that are bullish (long-entry) or bearish (short-entry) by construction.
_LONG_MEAN_REV_SIDES = {
    "rsi_14": ("low",),
    "close_z_20": ("low",),
    "bb_lower_dist": ("low",),
    "bb_mid_reclaim": ("high",),
    "bullish_reclaim": ("high",),
    "reclaim_strength": ("high",),
    "ret_3": ("low",),
    "ret_1": ("low",),
    "downside_velocity_decay": ("high",),
    "exhaustion_score": ("high",),
    "absorption_proxy": ("high",),
    "signed_volume_pressure": ("low",),
    "impact_decay_proxy": ("high",),
    "lower_wick_pct": ("high",),
}
_LONG_BREAKOUT_SIDES = {
    "donchian20_long_break": ("high",),
    "trend_bias_50_200": ("high",),
    "expansion_score": ("high",),
    "breakout_acceptance": ("high",),
    "ret_3": ("high",),
    "ret_1": ("high",),
    "ret_12": ("high",),
    "volume_z": ("high",),
    "bb_width": ("high",),
    "atr_pct_14": ("high",),
    "volatility_acceleration": ("high",),
    "squeeze_persistence": ("high",),
    "bb_mid_reclaim": ("high",),
    "close_z_20": ("high",),
    "slope_close_6": ("high",),
    "trend_efficiency_12": ("high",),
}
_SHORT_MEAN_REV_SIDES = {
    "rsi_14": ("high",),
    "close_z_20": ("high",),
    "bb_upper_dist": ("low",),
    "ret_3": ("high",),
    "ret_1": ("high",),
    "upper_wick_pct": ("high",),
    "exhaustion_score": ("high",),
    "signed_volume_pressure": ("high",),
}
_SHORT_BREAKOUT_SIDES = {
    "donchian20_short_break": ("high",),
    "trend_bias_50_200": ("low",),
    "expansion_score": ("high",),
    "breakout_acceptance": ("high",),
    "ret_3": ("low",),
    "ret_1": ("low",),
    "volume_z": ("high",),
    "bb_width": ("high",),
    "atr_pct_14": ("high",),
    "volatility_acceleration": ("high",),
    "squeeze_persistence": ("high",),
    "close_z_20": ("low",),
    "slope_close_6": ("low",),
}


def _legal_entry_sides(hypothesis, factor_name):
    """Return allowed quantile sides for a factor under locked trade direction.

    When direction is locked, overbought-long / oversold-short sides are banned.
    Unlocked hypotheses keep both sides (legacy free search).
    """
    factor = str(factor_name or "").strip()
    direction = _locked_trade_direction(hypothesis)
    if direction is None or not factor:
        return ("high", "low")
    regime = _entry_regime(hypothesis)
    allowed = set()
    if direction == "long":
        if regime in ("mean_reversion", "mixed"):
            allowed.update(_LONG_MEAN_REV_SIDES.get(factor) or ())
        if regime in ("breakout", "mixed"):
            allowed.update(_LONG_BREAKOUT_SIDES.get(factor) or ())
        # Hard ban: long never enters on classic overbought extremes.
        if factor in ("rsi_14",) and "high" in allowed and regime != "breakout":
            allowed.discard("high")
        if factor == "rsi_14" and regime == "mean_reversion":
            allowed = {"low"}
        if factor == "donchian20_short_break":
            allowed.clear()
    else:
        if regime in ("mean_reversion", "mixed"):
            allowed.update(_SHORT_MEAN_REV_SIDES.get(factor) or ())
        if regime in ("breakout", "mixed"):
            allowed.update(_SHORT_BREAKOUT_SIDES.get(factor) or ())
        if factor in ("rsi_14",) and "low" in allowed and regime != "breakout":
            allowed.discard("low")
        if factor == "rsi_14" and regime == "mean_reversion":
            allowed = {"high"}
        if factor == "donchian20_long_break":
            allowed.clear()
    if not allowed:
        # Unknown factor under lock: keep both only for pure vol state factors
        # that are not directional entries by themselves.
        if factor in (
            "bb_width", "atr_pct_14", "squeeze_persistence", "squeeze_score",
            "volatility_acceleration", "range_pct", "volume_z",
        ):
            return ("high", "low") if regime == "breakout" else (
                ("high",) if direction == "long" else ("high", "low")
            )
        return tuple()
    return tuple(side for side in ("high", "low") if side in allowed)


def _side_legal_for_hypothesis(hypothesis, factor_name, side):
    return str(side or "") in set(_legal_entry_sides(hypothesis, factor_name))


def _candidate_events(hypothesis, factor_matrix, max_specs=18):
    hints = list((hypothesis or {}).get("factor_hints") or
                 (hypothesis or {}).get("observable_proxy") or [])
    available = [h for h in hints if h in (factor_matrix or {})]
    specs = []
    cache = {}
    compile_failures = []

    def mask(name, side, q):
        key = (name, side, q)
        if key not in cache:
            cache[key] = _causal_quantile_mask(factor_matrix.get(name) or [], side, q)
        return cache[key]

    def quantile_term(name, side, q):
        # The rolling estimator is executable identity, not an implementation
        # default.  Persist it in every admitted recipe so formal compilation
        # can reproduce the exact research event.
        return {
            "factor": name,
            "side": side,
            "q": q,
            "window": CAUSAL_QUANTILE_WINDOW,
            "min_history": CAUSAL_QUANTILE_MIN_HISTORY,
            "threshold_source": "prior_only_rolling_quantile",
        }

    # P2: prefer deterministic event_ast when present.
    event_ast = (hypothesis or {}).get("event_ast")
    pinned_mask = (hypothesis or {}).get("pinned_event_mask")
    if pinned_mask is not None:
        # Stage1 rhyme arm lock: exact research identity. Keep Formal AST too.
        pin_terms = [{"node_type": "pinned_rhyme_arm", "family_id": (hypothesis or {}).get("family_id")}]
        pin_spec = {
            "event_id": "rhyme_pinned_%s" % ((hypothesis or {}).get("family_id") or "arm"),
            "terms": pin_terms,
            "mask": list(pinned_mask),
            "kind": "human_contract_exact",
            "logic": "pinned_stage1_arm",
            "pinned_rhyme_arm": True,
        }
        if isinstance(event_ast, dict) and event_ast:
            try:
                from . import ast_compiler as ac
                dsl_pack = ac.compile_ast_to_dsl(event_ast)
                pin_spec["event_ast"] = event_ast
                pin_spec["event_ast_hash"] = dsl_pack.get("event_ast_hash")
                pin_spec["event_ast_formal_ok"] = bool(dsl_pack.get("formal_ok"))
                pin_spec["event_ast_dsl"] = dsl_pack.get("entry_tree")
                if dsl_pack.get("formal_ok"):
                    pin_spec["kind"] = "ast_compiled"
                    pin_spec["logic"] = "ast"
                    pin_spec["terms"] = list(dsl_pack.get("terms") or pin_terms)
            except Exception:
                pass
        specs.append(pin_spec)
        # Exact pin wins: do not dilute with quantile remine.
        return specs[: int(max_specs)], available

    if isinstance(event_ast, dict) and event_ast:
        try:
            from . import ast_compiler as ac
            compiled = ac.compile_ast_to_mask(event_ast, factor_matrix)
            if compiled.get("ok") and compiled.get("mask") is not None:
                dsl_pack = ac.compile_ast_to_dsl(event_ast)
                terms = list(dsl_pack.get("terms") or [])
                if not terms:
                    terms = [{"node_type": "ast", "event_ast_hash": compiled.get("event_ast_hash")}]
                specs.append({
                    "event_id": "ast_%s" % (
                        (hypothesis or {}).get("event_ast_hash")
                        or compiled.get("event_ast_hash")
                        or "compiled"
                    )[:48],
                    "terms": terms,
                    "mask": list(compiled.get("mask") or []),
                    "kind": "ast_compiled",
                    "logic": "ast",
                    "event_ast": compiled.get("normalized") or event_ast,
                    "event_ast_hash": compiled.get("event_ast_hash"),
                    "event_ast_formal_ok": bool(dsl_pack.get("formal_ok")),
                    "event_ast_dsl": dsl_pack.get("entry_tree"),
                    "ast_compile_report": compiled.get("compile_report"),
                })
            else:
                compile_failures.append({
                    "candidate_id": (hypothesis or {}).get("hypothesis_id"),
                    "compile_stage": "mask",
                    "report": compiled.get("compile_report"),
                    "source_payload": {
                        "event_ast_hash": (hypothesis or {}).get("event_ast_hash"),
                    },
                })
        except Exception as exc:
            compile_failures.append({
                "candidate_id": (hypothesis or {}).get("hypothesis_id"),
                "compile_stage": "mask",
                "exception_type": type(exc).__name__,
                "detail": str(exc)[:240],
            })

    # Exact human-contract comparisons take precedence over generic quantiles.
    # The contract compiler already rejects missing required features; this
    # branch preserves numeric thresholds and AND/OR structure during probing.
    required_conditions = [
        row for row in ((hypothesis or {}).get("required_event_conditions") or [])
        if isinstance(row, dict)
    ]
    if required_conditions:
        exact_mask = None
        exact_terms = []
        n = min([
            len((factor_matrix or {}).get(row.get("feature") or row.get("indicator")) or [])
            for row in required_conditions
        ] or [0])
        for index, condition in enumerate(required_conditions):
            feature = condition.get("feature") or condition.get("indicator")
            values = list((factor_matrix or {}).get(feature) or [])[:n]
            op = str(condition.get("operator") or "")
            threshold = _finite(condition.get("value"))
            current = []
            for value in values:
                value = _finite(value)
                if value is None or threshold is None:
                    current.append(False)
                elif op == ">=":
                    current.append(value >= threshold)
                elif op == "<=":
                    current.append(value <= threshold)
                elif op == ">":
                    current.append(value > threshold)
                elif op == "<":
                    current.append(value < threshold)
                elif op in ("=", "=="):
                    current.append(value == threshold)
                else:
                    current.append(False)
            if exact_mask is None:
                exact_mask = current
            elif str(condition.get("join") or "and").lower() == "or":
                exact_mask = [bool(exact_mask[i] or current[i]) for i in range(n)]
            else:
                exact_mask = [bool(exact_mask[i] and current[i]) for i in range(n)]
            exact_terms.append({
                "factor": feature,
                "op": op,
                "value": threshold,
                "timeframe": condition.get("timeframe"),
                "join": condition.get("join") or ("root" if index == 0 else "and"),
            })
        specs.append({
            "event_id": "research_contract_exact_event",
            "terms": exact_terms,
            "mask": exact_mask or [],
            "kind": "human_contract_exact",
            "logic": "ordered_joins",
        })
        return specs[: int(max_specs)], available

    # Structured search freezes a complete composite event before this formal
    # probe.  Rebuild that exact rolling-quantile identity instead of falling
    # back to the first factor or silently re-mining new thresholds.
    required_quantile_terms = [
        row for row in ((hypothesis or {}).get("required_quantile_terms") or [])
        if isinstance(row, dict)
    ]
    if required_quantile_terms:
        masks = []
        frozen_terms = []
        for row in required_quantile_terms:
            name = row.get("factor")
            side = str(row.get("side") or "")
            q = _finite(row.get("q"))
            if name not in (factor_matrix or {}) or side not in ("high", "low") or q is None:
                return [], available
            if not _side_legal_for_hypothesis(hypothesis, name, side):
                return [], available
            masks.append(mask(name, side, q))
            frozen_terms.append({
                "factor": name,
                "side": side,
                "q": q,
                "window": int(row.get("window") or CAUSAL_QUANTILE_WINDOW),
                "min_history": int(row.get("min_history") or CAUSAL_QUANTILE_MIN_HISTORY),
                "threshold_source": "prior_only_rolling_quantile",
            })
        n = min([len(row) for row in masks] or [0])
        specs.append({
            "event_id": str(
                (hypothesis or {}).get("mechanism_id")
                or (hypothesis or {}).get("hypothesis_id")
                or "structured_composite_event"
            ),
            "terms": frozen_terms,
            "mask": [all(bool(row[index]) for row in masks) for index in range(n)],
            "kind": "mechanism_preserving",
            "logic": "all",
            "structured_identity_locked": True,
        })
        return specs, [row.get("factor") for row in frozen_terms]

    def _resolve_side(factor_name, declared_direction):
        constraints = (hypothesis or {}).get("factor_side_constraints") or {}
        rule = str(constraints.get(factor_name) or "").strip().lower()
        if rule in ("high", "low"):
            return rule
        if rule == "trade_direction":
            if declared_direction in ("long", "buy", "positive_shift"):
                return "high"
            if declared_direction in ("short", "sell", "negative_shift"):
                return "low"
        return None

    required_ix = [
        str(name) for name in ((hypothesis or {}).get("required_factor_intersection") or [])
        if str(name) in (factor_matrix or {})
    ]
    # Locked mechanism intersections: only the required pair/sides, no single-proxy dump.
    if len(required_ix) >= 2:
        declared = str((hypothesis or {}).get("predicted_direction") or "").strip().lower()
        directions = _direction_candidates(hypothesis)
        dir_labels = []
        for sign in directions:
            dir_labels.append("long" if int(sign) > 0 else "short")
        if declared in ("long", "short"):
            dir_labels = [declared]
        for direction_label in dir_labels:
            sides = []
            ok = True
            for name in required_ix:
                side = _resolve_side(name, direction_label)
                if side is None:
                    ok = False
                    break
                sides.append(side)
            if not ok:
                continue
            for q_ix in INTERSECTION_QUANTILES:
                masks = [mask(required_ix[i], sides[i], q_ix) for i in range(len(required_ix))]
                n = min([len(row) for row in masks] or [0])
                specs.append({
                    "event_id": "%s_q%s" % (
                        "_AND_".join(
                            "%s_%s" % (required_ix[i], sides[i])
                            for i in range(len(required_ix))
                        ),
                        int(float(q_ix) * 100),
                    ),
                    "terms": [
                        quantile_term(required_ix[i], sides[i], q_ix)
                        for i in range(len(required_ix))
                    ],
                    "mask": [all(bool(row[index]) for row in masks) for index in range(n)],
                    "kind": "mechanism_intersection",
                    "logic": "all",
                })
        # Prefer formal mapped composites; skip unrelated single_proxy / free pairs.
        seen, unique = set(), []
        for row in specs:
            if row["event_id"] in seen:
                continue
            seen.add(row["event_id"])
            unique.append(row)

        def _formal_factor_ok(term):
            return str((term or {}).get("factor") or "") in recipe_policy.GENERIC_FACTOR_TO_DSL

        unique.sort(
            key=lambda row: (
                0 if all(_formal_factor_ok(t) for t in (row.get("terms") or [])) else 1,
                str(row.get("event_id") or ""),
            )
        )
        return unique[: int(max_specs)], available

    for name in available:
        legal_sides = _legal_entry_sides(hypothesis, name)
        if not legal_sides:
            continue
        for q in DEFAULT_QUANTILES:
            for side in legal_sides:
                specs.append({
                    "event_id": "%s_%s_q%s" % (name, side, int(q * 100)),
                    "terms": [quantile_term(name, side, q)],
                    "mask": mask(name, side, q),
                    "kind": "single_proxy",
                    "logic": "all",
                })

    # Intersections preserve a mechanism better than stripping it to one proxy.
    pairs = []
    for i in range(min(len(available), 5)):
        for j in range(i + 1, min(len(available), 5)):
            pairs.append((available[i], available[j]))
    for a, b in pairs:
        sides_a = _legal_entry_sides(hypothesis, a)
        sides_b = _legal_entry_sides(hypothesis, b)
        if not sides_a or not sides_b:
            continue
        side_pairs = [(sa, sb) for sa in sides_a for sb in sides_b]
        for q_ix in INTERSECTION_QUANTILES:
            for sa, sb in side_pairs:
                ma, mb = mask(a, sa, q_ix), mask(b, sb, q_ix)
                n = min(len(ma), len(mb))
                specs.append({
                    "event_id": "%s_%s_AND_%s_%s_q%s" % (
                        a, sa, b, sb, int(float(q_ix) * 100),
                    ),
                    "terms": [
                        quantile_term(a, sa, q_ix),
                        quantile_term(b, sb, q_ix),
                    ],
                    "mask": [bool(ma[k] and mb[k]) for k in range(n)],
                    "kind": "mechanism_intersection",
                    "logic": "all",
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
        if not _side_legal_for_hypothesis(hypothesis, a, sa):
            continue
        if not _side_legal_for_hypothesis(hypothesis, b, sb):
            continue
        ma, mb = mask(a, sa, 0.8), mask(b, sb, 0.8)
        n = min(len(ma), len(mb))
        specs.insert(0, {
            "event_id": name,
            "terms": [quantile_term(a, sa, 0.8),
                      quantile_term(b, sb, 0.8)],
            "mask": [bool(ma[k] and mb[k]) for k in range(n)],
            "kind": "mechanism_preserving",
            "logic": "all",
        })

    # Exhaustion recovery is a conjunctive mechanism, not a one-factor tail.
    # Preserve the human research identity from the first probe: extreme
    # oversold state + deep negative displacement + a completed bullish reclaim.
    # Successive variants then test whether selling-velocity decay and a low
    # persistent-downtrend score separate true exhaustion from a trend relay.
    if any(x in blob for x in (
        "exhaust", "liquid", "衰竭", "清算", "panic", "mean_reversion",
    )):
        exhaustion_events = (
            (
                "rsi_z_bullish_reclaim_core",
                (("rsi_14", "low", 0.80),
                 ("close_z_20", "low", 0.80),
                 ("bullish_reclaim", "high", 0.80)),
            ),
            (
                "rsi_z_bullish_reclaim_core_dense70",
                (("rsi_14", "low", 0.70),
                 ("close_z_20", "low", 0.70),
                 ("bullish_reclaim", "high", 0.70)),
            ),
            (
                "rsi_z_reclaim_with_velocity_decay",
                (("rsi_14", "low", 0.80),
                 ("close_z_20", "low", 0.80),
                 ("bullish_reclaim", "high", 0.80),
                 ("downside_velocity_decay", "high", 0.80)),
            ),
            (
                "rsi_z_reclaim_with_velocity_decay_dense70",
                (("rsi_14", "low", 0.70),
                 ("close_z_20", "low", 0.70),
                 ("bullish_reclaim", "high", 0.70),
                 ("downside_velocity_decay", "high", 0.70)),
            ),
            (
                "rsi_z_reclaim_anti_falling_knife",
                (("rsi_14", "low", 0.80),
                 ("close_z_20", "low", 0.80),
                 ("bullish_reclaim", "high", 0.80),
                 ("downside_velocity_decay", "high", 0.80),
                 ("downtrend_persistence_12", "low", 0.80)),
            ),
        )
        # Bollinger mean-reversion identity (formal bb_* factors). Prefer these
        # when the brief/family names 布林/bollinger so discovery does not collapse
        # onto rsi×z20 lottery clones that already fail the win-only gate.
        if any(x in blob for x in (
            "布林", "bollinger", "bb_lower", "bb_mid", "bb_width", "boll",
        )):
            exhaustion_events = exhaustion_events + (
                (
                    "rsi_bb_lower_mid_reclaim_core",
                    (("rsi_14", "low", 0.80),
                     ("bb_lower_dist", "low", 0.80),
                     ("bb_mid_reclaim", "high", 0.70)),
                ),
                (
                    "rsi_bb_lower_mid_reclaim_dense70",
                    (("rsi_14", "low", 0.70),
                     ("bb_lower_dist", "low", 0.70),
                     ("bb_mid_reclaim", "high", 0.65)),
                ),
                (
                    "rsi_bb_lower_mid_width_guard",
                    (("rsi_14", "low", 0.80),
                     ("bb_lower_dist", "low", 0.80),
                     ("bb_mid_reclaim", "high", 0.70),
                     ("bb_width", "low", 0.70)),
                ),
                (
                    "rsi_bb_lower_reclaim_velocity",
                    (("rsi_14", "low", 0.80),
                     ("bb_lower_dist", "low", 0.80),
                     ("bb_mid_reclaim", "high", 0.70),
                     ("downside_velocity_decay", "high", 0.75)),
                ),
            )
        for event_name, definitions in reversed(exhaustion_events):
            if not all(name in (factor_matrix or {}) for name, _, _ in definitions):
                continue
            if any(
                not _side_legal_for_hypothesis(hypothesis, name, side)
                for name, side, _q in definitions
            ):
                continue
            term_rows = [
                quantile_term(name, side, q)
                for name, side, q in definitions
            ]
            masks = [mask(name, side, q) for name, side, q in definitions]
            n = min([len(row) for row in masks] or [0])
            is_bb = "bb_" in event_name or "boll" in event_name
            specs.insert(0, {
                "event_id": event_name,
                "terms": term_rows,
                "mask": [all(bool(row[index]) for row in masks) for index in range(n)],
                "kind": "mechanism_preserving",
                "logic": "all",
                "mechanism_identity": (
                    "rsi_bollinger_mid_reclaim" if is_bb else "rsi_zscore_candle_reclaim"
                ),
                "anti_falling_knife_tested": bool(
                    event_name.endswith("anti_falling_knife")
                ),
            })

    # Materialization repair waves: force state/combo representations even when
    # the first direct-mechanism probes look weak. Controlled by env wave id.
    try:
        from . import candidate_materialization as mat
        wave = str(os.environ.get("QIYU_MATERIALIZATION_WAVE") or "").strip()
        if not wave:
            # Always inject wave2 state forms so first pass is not single-threshold only.
            wave = mat.WAVE_STATE
        for event_name, definitions in mat.forced_probe_event_defs(wave):
            if not all(name in (factor_matrix or {}) for name, _, _ in definitions):
                continue
            if any(row.get("event_id") == event_name for row in specs):
                continue
            term_rows = [quantile_term(name, side, q) for name, side, q in definitions]
            masks = [mask(name, side, q) for name, side, q in definitions]
            n = min([len(row) for row in masks] or [0])
            specs.insert(0, {
                "event_id": event_name,
                "terms": term_rows,
                "mask": [all(bool(row[index]) for row in masks) for index in range(n)],
                "kind": "mechanism_preserving",
                "logic": "all",
                "mechanism_identity": "materialization_%s" % wave,
                "materialization_wave": wave,
            })
    except Exception:
        pass

    # Layer B quality repairs: confirmation + exclusion (stop/leverage untouched).
    try:
        from . import quality_optimization as qopt
        qcodes = str(os.environ.get("QIYU_QUALITY_FAILURE_CODES") or "").strip()
        code_list = [c for c in qcodes.split(",") if c] if qcodes else [
            qopt.LOW_WIN_RATE, qopt.LOW_PROFIT_FIRST_RATE, qopt.HIGH_MAE,
        ]
        for event_name, definitions, rtype in qopt.quality_repair_event_defs(code_list):
            if not all(name in (factor_matrix or {}) for name, _, _ in definitions):
                continue
            if any(row.get("event_id") == event_name for row in specs):
                continue
            term_rows = [quantile_term(name, side, q) for name, side, q in definitions]
            masks = [mask(name, side, q) for name, side, q in definitions]
            n = min([len(row) for row in masks] or [0])
            specs.insert(0, {
                "event_id": event_name,
                "terms": term_rows,
                "mask": [all(bool(row[index]) for row in masks) for index in range(n)],
                "kind": "mechanism_preserving",
                "logic": "all",
                "mechanism_identity": "quality_repair",
                "representation_type": rtype,
                "quality_repair": True,
            })
    except Exception:
        pass

    # Stable order and bounded diagnostic budget.
    # 关键：密分位单因子会占满 max_specs，可正式编译的 close_z×volume_z
    # 交集永远进不了预算 → best=None / formal_capability_blocked / 存活0。
    seen, unique = set(), []
    for row in specs:
        if row["event_id"] in seen:
            continue
        seen.add(row["event_id"])
        unique.append(row)

    def _formal_factor_ok(term):
        return str((term or {}).get("factor") or "") in recipe_policy.GENERIC_FACTOR_TO_DSL

    def _priority(row):
        kind = str(row.get("kind") or "")
        terms = list(row.get("terms") or [])
        all_formal = bool(terms) and all(_formal_factor_ok(t) for t in terms)
        if kind == "human_contract_exact":
            return 0
        if kind == "ast_compiled" and row.get("event_ast_formal_ok"):
            return 1
        if kind == "ast_compiled":
            return 2
        if kind in ("mechanism_intersection", "mechanism_preserving") and all_formal:
            return 3
        if kind in ("mechanism_intersection", "mechanism_preserving"):
            return 4
        if kind == "single_proxy" and all_formal:
            return 5
        return 6

    unique.sort(key=_priority)
    if compile_failures and isinstance(hypothesis, dict):
        hypothesis["ast_compile_failures"] = list(compile_failures)
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


def _trade_observation(candles, signal_i, horizon, direction, mapping,
                       stop_pct=None, target_pct=None, barrier_exit=False):
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
    planned_exit_i = exit_i
    if not entry:
        return None
    if stop_pct is None:
        stop_pct = recipe_policy.PROTECTIVE_STOP_PCT
    stop_pct = float(stop_pct)
    stop_price = entry * (1.0 - stop_pct if direction > 0 else 1.0 + stop_pct)
    target_price = None
    if target_pct is not None and float(target_pct) > 0:
        tp = float(target_pct)
        target_price = entry * (1.0 + tp if direction > 0 else 1.0 - tp)
    exit_reason = "fixed_horizon_close"
    exit_price = None
    for index in range(entry_i, planned_exit_i + 1):
        high = _finite(rows[index].get("high"))
        low = _finite(rows[index].get("low"))
        hit_stop = bool(
            (direction > 0 and low is not None and low <= stop_price)
            or (direction < 0 and high is not None and high >= stop_price)
        )
        hit_target = bool(
            barrier_exit and target_price is not None and (
                (direction > 0 and high is not None and high >= target_price)
                or (direction < 0 and low is not None and low <= target_price)
            )
        )
        if barrier_exit and hit_stop and hit_target:
            # Same-bar ambiguity: treat as stop (conservative), matching path_outcome.
            exit_i = index
            exit_price = stop_price
            exit_reason = "protective_stop"
            break
        if hit_stop:
            exit_i = index
            exit_price = stop_price
            exit_reason = "protective_stop"
            break
        if hit_target:
            exit_i = index
            exit_price = target_price
            exit_reason = "barrier_target"
            break
    if exit_price is None:
        exit_price = _finite(rows[planned_exit_i].get("close"))
        exit_i = planned_exit_i
    if exit_price is None:
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
    # Path-hit label: target-before-stop (creation objective).
    path_label = None
    try:
        from . import path_outcome as path_out
        path_target = float(target_pct) if target_pct is not None else path_out.TARGET_PRICE_PCT
        path_label = path_out.label_path(
            rows, entry_i, direction, horizon,
            target_pct=path_target,
            stop_pct=stop_pct,
        )
    except Exception:
        path_label = None
    out = {
        "return": ret, "mfe": mfe, "mae": mae,
        "signal_index": signal_i, "entry_index": entry_i, "exit_index": exit_i,
        "planned_exit_index": planned_exit_i, "exit_reason": exit_reason,
        "protective_stop_price": stop_price,
        "signal_ts": rows[signal_i].get("ts"),
        "entry_ts": rows[entry_i].get("ts"),
        "exit_ts": rows[exit_i].get("ts"),
    }
    if path_label:
        out["profit_first"] = path_label.get("profit_first")
        out["first_touch"] = path_label.get("first_touch")
        out["mfe_pct"] = path_label.get("mfe_pct")
        out["mae_pct"] = path_label.get("mae_pct")
        out["bars_to_target"] = path_label.get("bars_to_target")
        out["bars_to_stop"] = path_label.get("bars_to_stop")
        out["path_label"] = path_label
    return out


def _holding_exec_params(hypothesis=None, contract=None):
    """Resolve stop/target/leverage/barrier from hyp or research contract."""
    hyp = hypothesis if isinstance(hypothesis, dict) else {}
    holding = hyp.get("holding_contract") or {}
    if not holding and isinstance(contract, dict):
        holding = contract.get("holding_contract") or {}
    stop_pol = holding.get("protective_stop_policy") or {}
    exit_pol = holding.get("exit_policy") or {}
    try:
        stop_pct = float(stop_pol.get("price_pct"))
    except (TypeError, ValueError):
        stop_pct = recipe_policy.PROTECTIVE_STOP_PCT
    target_pct = holding.get("target_price_distance")
    if target_pct is None:
        target_pct = exit_pol.get("target_price_pct")
    if target_pct is None and isinstance(hyp.get("rhyme_selected_spec"), dict):
        target_pct = hyp["rhyme_selected_spec"].get("target_price_distance")
    try:
        target_pct = float(target_pct) if target_pct is not None else None
    except (TypeError, ValueError):
        target_pct = None
    try:
        leverage = float(holding.get("execution_leverage"))
    except (TypeError, ValueError):
        leverage = None
    if leverage is None and isinstance(hyp.get("rhyme_selected_spec"), dict):
        try:
            leverage = float(hyp["rhyme_selected_spec"].get("leverage"))
        except (TypeError, ValueError):
            leverage = None
    if leverage is None:
        leverage = float(recipe_policy.EXECUTION_LEVERAGE)
    barrier = (
        str(exit_pol.get("mode") or "") == recipe_policy.BARRIER_PCT_EXIT_MODE
        or bool(hyp.get("pinned_rhyme_arm"))
        or (
            isinstance((contract or {}).get("pinned_rhyme_arm"), dict)
            and bool((contract or {}).get("pinned_rhyme_arm"))
        )
    )
    if barrier and target_pct is None:
        target_pct = stop_pct
    return {
        "stop_pct": stop_pct,
        "target_pct": target_pct,
        "leverage": leverage,
        "barrier_exit": bool(barrier and target_pct is not None),
        "exit_policy": dict(exit_pol) if exit_pol else None,
        "protective_stop_policy": dict(stop_pol) if stop_pol else None,
    }


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
                    symbol=None, timeframe=None, exec_params=None):
    xp = exec_params if isinstance(exec_params, dict) else {}
    stop_pct = xp.get("stop_pct", recipe_policy.PROTECTIVE_STOP_PCT)
    target_pct = xp.get("target_pct")
    barrier_exit = bool(xp.get("barrier_exit"))
    leverage = float(xp.get("leverage") or recipe_policy.EXECUTION_LEVERAGE)
    # Stage1 pin identity: do not thin signals with independence clustering.
    if barrier_exit or bool(xp.get("pin_dense_events")):
        gap = 1
        merge = 1
    else:
        gap = _independence_gap_bars(horizon, timeframe=timeframe)
        merge = max(3, int(horizon or 1))
    raw, independent = _independent_events(
        event.get("mask") or [], gap, merge_bars=merge,
    )
    obs = []
    if candles:
        for i in independent:
            row = _trade_observation(
                candles, i, horizon, direction, mapping,
                stop_pct=stop_pct, target_pct=target_pct,
                barrier_exit=barrier_exit,
            )
            if row is not None:
                obs.append(row)
    else:
        # Compatibility path: the label is real only for its source horizon.
        rr = list(fallback_returns or [])
        for i in independent:
            if i < len(rr) and _finite(rr[i]) is not None:
                obs.append({"return": direction * float(rr[i]), "mfe": None, "mae": None,
                            "signal_index": i, "entry_index": i, "exit_index": i})
    gross_rets = [x["return"] for x in obs]
    gross = _mean(gross_rets)
    costs = _cost_scenarios(symbol, horizon)
    primary = "maker_taker" if mapping == "pullback_limit" else "taker_taker"
    primary_cost = float((costs.get(primary) or {}).get("total_friction") or 0.0)
    # Pin/barrier Stage1 identity uses the rhyme round-trip cost model so
    # mean_win > stop*L stays consistent with research admission.
    if barrier_exit:
        try:
            from project_prometheus import rhyme_contract as _rc
            primary_cost = float(_rc.ROUND_TRIP_PRICE_COST)
        except Exception:
            pass
    # Every statistical gate must see realizable post-cost returns.  Keeping
    # gross returns here made DSR/PBO significant even when friction erased the
    # edge.
    # DSR/PBO and final formal execution now consume the same account-return
    # basis: full-size leveraged gross PnL minus the same leveraged friction estimate.
    rets = [
        (float(value) - primary_cost) * float(leverage)
        for value in gross_rets
    ]
    # PBO must compare candidates on one market clock.  Keep trade-level returns
    # for DSR and a separate full bar-clock PnL vector for cross-candidate PBO.
    if candles:
        bar_timestamps = [row.get("ts") for row in candles]
        bar_returns = [0.0] * len(candles)
        for item in obs:
            idx = int(item.get("exit_index") or 0)
            if 0 <= idx < len(bar_returns):
                bar_returns[idx] += (
                    float(item.get("return") or 0.0) - primary_cost
                ) * float(leverage)
        if all(ts is not None for ts in bar_timestamps) and len(set(bar_timestamps)) == len(bar_timestamps):
            pbo_returns = {"timestamps": bar_timestamps, "returns": bar_returns}
        else:
            # All candidates in this discovery share the same candle array, so
            # equal-length bar positions are an explicit common-clock fallback.
            pbo_returns = bar_returns
    else:
        bar_returns = [0.0] * len(fallback_returns or [])
        for item in obs:
            idx = int(item.get("exit_index") or 0)
            if 0 <= idx < len(bar_returns):
                bar_returns[idx] += (
                    float(item.get("return") or 0.0) - primary_cost
                ) * float(leverage)
        pbo_returns = bar_returns
    mfes = [x["mfe"] for x in obs if x.get("mfe") is not None]
    maes = [x["mae"] for x in obs if x.get("mae") is not None]
    ci = _block_bootstrap_ci(rets, "%s:%s:%s:%s" % (
        event.get("event_id"), horizon, direction, mapping))
    t = _newey_west_t(rets)
    gross_t = _newey_west_t(gross_rets)
    event_abs = [abs(float(x)) for x in gross_rets]
    control_abs = _control_absolute_moves(
        candles, event.get("mask") or [], horizon, mapping, gap,
    )
    volatility_t = _two_sample_t(event_abs, control_abs)
    volatility_effect = bool(
        len(event_abs) >= MIN_INDEPENDENT_EVENTS and len(control_abs) >= 20 and
        (_mean(event_abs) or 0.0) > (_mean(control_abs) or 0.0) and volatility_t >= 1.64
    )
    scenario_rows = {}
    for name, pack in costs.items():
        total = float((pack or {}).get("total_friction") or 0.0)
        scenario_rows[name] = dict(pack or {})
        scenario_rows[name]["mean_net"] = None if gross is None else gross - total
        scenario_rows[name]["efr"] = None if gross is None or total <= 0 else gross / total
    pp = scenario_rows.get(primary) or {}
    n = len(rets)
    net_mean = _mean(rets)
    wins = [v for v in rets if v is not None and float(v) > 0]
    losses = [v for v in rets if v is not None and float(v) < 0]
    win_rate = (len(wins) / float(n)) if n else None
    # Barrier / Stage1 pin: WR = path profit-first among resolved (exclude
    # unresolved horizon closes), matching rhyme admission identity.
    if barrier_exit:
        pf_rets = []
        lf_rets = []
        for item, ret in zip(obs, rets):
            v = item.get("profit_first")
            if v is None:
                continue
            if int(v) == 1:
                pf_rets.append(ret)
            elif int(v) == 0:
                lf_rets.append(ret)
        resolved_n = len(pf_rets) + len(lf_rets)
        if resolved_n > 0:
            win_rate = len(pf_rets) / float(resolved_n)
            wins = list(pf_rets)
            losses = list(lf_rets)
            n = resolved_n
    avg_win = (sum(wins) / float(len(wins))) if wins else 0.0
    avg_loss_mag = (sum(-float(v) for v in losses) / float(len(losses))) if losses else 0.0
    if avg_loss_mag > 0:
        payoff_ratio = avg_win / avg_loss_mag
    else:
        payoff_ratio = 999.0 if avg_win > 0 else 0.0
    expectancy_factor = (win_rate * payoff_ratio) if win_rate is not None else None
    # Anti-lottery: removing the single largest win must not erase expectancy.
    if wins:
        max_win = max(wins)
        reduced = list(rets)
        reduced.remove(max_win)
        mean_without_max_win = _mean(reduced)
    else:
        mean_without_max_win = net_mean
    high_wr = bool(win_rate is not None and win_rate > float(MIN_ASSEMBLY_WIN_RATE))
    anti_lottery = bool(
        expectancy_factor is not None and expectancy_factor >= 1.0
        and mean_without_max_win is not None and mean_without_max_win > 0
    )
    # Manufacture-batch mode: still cancel old WR/Gate vetoes, but path-hit
    # bare screen is mandatory — no more "sample adequacy alone → READY".
    try:
        from . import manufacture_batch_policy as mfg
        manufacture_mode = bool(mfg.pre_review_gates_disabled())
    except Exception:
        manufacture_mode = False
    path_screen = None
    try:
        from . import path_bare_screen as pbs
        path_screen = pbs.screen_from_observations(
            obs, horizon=horizon, direction=direction,
        )
    except Exception:
        path_screen = None
    path_packaging_ok = bool((path_screen or {}).get("packaging_ok"))
    path_review_ok = bool((path_screen or {}).get("passed"))
    path_summary = dict((path_screen or {}).get("summary") or {})
    if barrier_exit:
        # path_bare_screen still defaults to 0.5555%/20x; pin identity uses
        # researched target/stop/leverage already computed into win_rate/wins.
        pf_n = len(wins) if win_rate is not None else 0
        lf_n = len(losses) if win_rate is not None else 0
        resolved = pf_n + lf_n
        if resolved > 0 and win_rate is not None:
            path_summary["profit_first_rate"] = float(win_rate)
            path_summary["loss_first_rate"] = float(lf_n) / float(resolved)
            path_summary["n_profit_first"] = int(pf_n)
            path_summary["n_loss_first"] = int(lf_n)
            path_summary["n"] = int(resolved)
            path_summary["barrier_target_pct"] = float(target_pct) if target_pct is not None else None
            path_summary["mean_winning_levered"] = float(avg_win) if wins else None
            path_summary["path_identity"] = "rhyme_barrier_resolved"
            path_summary["win_rate_basis"] = "path_profit_first_resolved"
            path_summary["mean_win_basis"] = "path_barrier_winning_levered"
            path_summary["win_rate"] = float(win_rate)
    # 毛收益仅作诊断；交接/组装必须用账户口径（含摩擦）胜率>50% + 反彩票。
    # 禁止「毛胜率好看、账户胜率≈35%」的屎策略混进复核。
    # 屏障/钉扎臂另用 path_profit_first_resolved，并在 select_metrics 标明口径。
    gross_wins = [v for v in gross_rets if v is not None and float(v) > 0]
    gross_losses = [v for v in gross_rets if v is not None and float(v) < 0]
    gross_win_rate = (len(gross_wins) / float(n)) if n else None
    gross_avg_win = (sum(gross_wins) / float(len(gross_wins))) if gross_wins else 0.0
    gross_avg_loss_mag = (
        sum(-float(v) for v in gross_losses) / float(len(gross_losses))
        if gross_losses else 0.0
    )
    if gross_avg_loss_mag > 0:
        gross_payoff = gross_avg_win / gross_avg_loss_mag
    else:
        gross_payoff = 999.0 if gross_avg_win > 0 else 0.0
    gross_expectancy_factor = (
        (gross_win_rate * gross_payoff) if gross_win_rate is not None else None
    )
    if gross_wins:
        g_reduced = list(gross_rets)
        g_reduced.remove(max(gross_wins))
        gross_mean_without_max_win = _mean(g_reduced)
    else:
        gross_mean_without_max_win = gross
    gross_high_wr = bool(
        gross_win_rate is not None and gross_win_rate > float(MIN_ASSEMBLY_WIN_RATE)
    )
    gross_anti_lottery = bool(
        gross_mean_without_max_win is not None and gross_mean_without_max_win > 0
    )
    statistical = bool(
        n >= MIN_INDEPENDENT_EVENTS and net_mean is not None
        and net_mean > 0 and t >= 1.64
    )
    economic = bool(gross is not None and gross > 0 and (_mean(mfes) or 0.0) >=
                    float((costs.get("maker_taker") or {}).get("total_friction") or 0.0))
    execution = bool((pp.get("mean_net") or -1.0) > 0 and (pp.get("efr") or 0.0) >= 1.20)
    # 生产交接底线：账户胜率严格>50% + 去最大盈利后不崩 + 平均净收益>0 + 样本够。
    handoff_floor = bool(
        n >= MIN_INDEPENDENT_EVENTS
        and net_mean is not None and net_mean > 0
        and high_wr and anti_lottery
    )
    regime_conditional, regime_pack = _regime_split_conditional(candles, obs)
    if n < MIN_INDEPENDENT_EVENTS:
        state = "SAMPLE_INADEQUATE"
    elif manufacture_mode and n >= MIN_INDEPENDENT_EVENTS and net_mean is not None and net_mean > 0:
        # Creation hard floor restored: account/path WR must clear 35%.
        # Positive mean alone is not READY (stops random-noise survivors).
        if win_rate is not None and float(win_rate) > 0.35:
            state = "READY_FOR_ASSEMBLY"
        else:
            state = "NEAR_MISS_DIAGNOSTIC"
    elif manufacture_mode and n >= MIN_INDEPENDENT_EVENTS:
        state = "NO_DIRECTIONAL_EFFECT" if (net_mean is None or net_mean <= 0) else "NEAR_MISS_DIAGNOSTIC"
    elif path_review_ok and statistical and economic and execution and high_wr and anti_lottery:
        state = "READY_FOR_ASSEMBLY"
    elif path_review_ok and handoff_floor:
        state = "READY_FOR_ASSEMBLY"
    elif statistical and economic and execution and high_wr and anti_lottery:
        state = "READY_FOR_ASSEMBLY"
    elif handoff_floor:
        state = "READY_FOR_ASSEMBLY"
    elif statistical and economic and execution and not (high_wr and anti_lottery):
        # Positive mean with rare big wins — keep as diagnostic, never assemble.
        state = "NEAR_MISS_DIAGNOSTIC"
    elif statistical and economic:
        state = "EXECUTION_MAPPING_FAILURE"
    elif regime_conditional and (statistical or (net_mean is not None and net_mean > 0 and t >= 1.0)):
        state = "STATE_CONDITIONAL"
    elif statistical:
        state = "DIRECTIONAL_BUT_SMALL"
    elif volatility_effect:
        state = "VOLATILITY_EFFECT_ONLY"
    elif net_mean is not None and net_mean > 0 and (t >= 1.0 or ((pp.get("mean_net") or -1) > -0.0005)):
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
        "event_logic": event.get("logic"),
        "terms": event.get("terms"),
        "event_ast": event.get("event_ast"),
        "event_ast_hash": event.get("event_ast_hash"),
        "event_ast_formal_ok": event.get("event_ast_formal_ok"),
        "event_ast_dsl": event.get("event_ast_dsl"),
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
        "mean_net": net_mean,
        "win_rate": win_rate,
        "win_rate_pct": (None if win_rate is None else win_rate * 100.0),
        "payoff_ratio": payoff_ratio,
        "expectancy_factor": expectancy_factor,
        "mean_net_without_max_win": mean_without_max_win,
        "high_wr_pass": high_wr,
        "anti_lottery_pass": anti_lottery,
        "gross_win_rate": gross_win_rate,
        "gross_win_rate_pct": (
            None if gross_win_rate is None else gross_win_rate * 100.0
        ),
        "gross_payoff_ratio": gross_payoff,
        "gross_expectancy_factor": gross_expectancy_factor,
        "gross_mean_without_max_win": gross_mean_without_max_win,
        "gross_high_wr_pass": gross_high_wr,
        "gross_anti_lottery_pass": gross_anti_lottery,
        "handoff_floor": handoff_floor,
        "handoff_return_basis": "account_net_after_friction_v1",
        "mean_mfe": mean_mfe,
        "mean_mae": mean_mae,
        "path_bare_screen": path_screen,
        "profit_first_rate": path_summary.get("profit_first_rate"),
        "loss_first_rate": path_summary.get("loss_first_rate"),
        "unresolved_rate": path_summary.get("unresolved_rate"),
        "median_mfe_pct": path_summary.get("median_mfe_pct"),
        "median_mae_pct": path_summary.get("median_mae_pct"),
        "mean_winning_levered": path_summary.get("mean_winning_levered"),
        "median_winning_levered": path_summary.get("median_winning_levered"),
        "failure_path_breakdown": (path_screen or {}).get("failure_path_breakdown"),
        "dominant_failure_path": path_summary.get("dominant_failure_path"),
        "path_labels": list((path_screen or {}).get("path_labels") or [])[:500],
        "path_packaging_ok": path_packaging_ok,
        "path_review_eligible": path_review_ok,
        "path_entry_score": ((path_screen or {}).get("rank") or {}).get("score"),
        "hac_t_stat": t,
        "gross_hac_t_stat": gross_t,
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
        "primary_cost_per_trade": primary_cost,
        "statistical_returns_are_post_cost": True,
        "exit_policy": (
            {
                "mode": recipe_policy.BARRIER_PCT_EXIT_MODE,
                "target_price_pct": float(target_pct),
                "exit_bar": "first_touch_target_or_stop_else_horizon",
                "price": "barrier_or_bar_close",
                "allow_early_take_profit": False,
            }
            if barrier_exit and target_pct is not None
            else {
                "mode": recipe_policy.EXIT_POLICY_MODE,
                "exit_bar": "entry_plus_horizon_minus_1",
                "price": "bar_close",
                "allow_early_take_profit": False,
            }
        ),
        "protective_stop_policy": {
            "mode": recipe_policy.PROTECTIVE_STOP_MODE,
            "price_pct": float(stop_pct),
            "applies_from": "entry_bar",
            "precedence": "protective_stop_before_time_exit",
        },
        "protective_stop_evaluated": bool(candles),
        "execution_leverage": leverage,
        "statistical_return_basis": recipe_policy.STATISTICAL_RETURN_BASIS,
        # DSR/assembly must see the same complete independent-event sample as
        # the reported n_filled_events.  Truncating the first 300 observations
        # silently changed both sample size and market regime.
        "trade_returns": rets,
        "n_statistical_returns": len(rets),
        "pbo_bar_returns": pbo_returns,
        "event_mask": list(event.get("mask") or []),
    }


def evaluate_naked_probe(factor_values, fwd_returns, side="high", q=0.8,
                         hold_bars=None, horizons=DEFAULT_HORIZONS,
                         round_trip_cost=0.001, candles=None, symbol=None,
                         direction=1, execution_mapping="next_bar_open",
                         timeframe=None):
    """Compatibility API backed by the new real-horizon evaluator."""
    event = {"event_id": "single_factor_%s_q%s" % (side, int(q * 100)),
             "terms": [{
                 "factor": "factor", "side": side, "q": q,
                 "window": CAUSAL_QUANTILE_WINDOW,
                 "min_history": CAUSAL_QUANTILE_MIN_HISTORY,
                 "threshold_source": "prior_only_rolling_quantile",
             }],
             "mask": _causal_quantile_mask(factor_values, side, q),
             "kind": "single_proxy", "logic": "all"}
    rows = [_evaluate_trial(
        candles, fwd_returns, event, h, direction,
        execution_mapping, symbol=symbol, timeframe=timeframe,
    ) for h in horizons]
    rows.sort(key=lambda x: (1 if x.get("passed") else 0,
                             float(x.get("mean_net") or -1e9)), reverse=True)
    best = rows[0] if rows else {"ok": False, "passed": False,
                                "research_state": "SAMPLE_INADEQUATE"}
    best["side"] = side
    best["q"] = q
    best["round_trip_cost"] = round_trip_cost
    best["horizon_results"] = [{k: v for k, v in r.items() if k not in ("trade_returns", "pbo_bar_returns", "event_mask")}
                                for r in rows]
    best["hard_rule_zh"] = (
        "三轴未同时通过时禁止组装；单个窄探针失败不得宣判整个机制族死亡。"
    )
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
    timing_mode = str(
        (((hypothesis or {}).get("required_entry_timing") or {}).get("mode"))
        or "unspecified"
    ).strip().lower()
    if timing_mode in ("bar_close", "next_bar_open"):
        # A signal is evaluated only after the triggering candle is complete;
        # the first realizable fill is the next candle open.  Do not let a
        # delayed or pullback mapping win when timing is contract-locked.
        mappings = ("next_bar_open",)
    else:
        mappings = ("next_bar_open", "delayed_confirmation", "pullback_limit")
    holding = (hypothesis or {}).get("holding_contract") or {}
    horizons = tuple((hypothesis or {}).get("required_horizons_bars") or ())
    if not horizons:
        horizons = tuple(holding.get("allowed_horizons_bars") or ())
    if not horizons:
        horizons = DEFAULT_HORIZONS if candles else (3,)
    horizons = tuple(sorted(set(
        int(value) for value in horizons if 1 <= int(value) <= 240
    )))
    budget = max(1, int(max_trials))
    # Round-robin coverage: first cover distinct event definitions, then horizons,
    # then alternate execution mappings.  A small budget therefore stays diverse.
    directions = _direction_candidates(hypothesis)
    plan = []
    horizon_order = (3, 1, 6, 12, 2, 4, 8, 10, 16, 18, 20, 24, 30, 36, 48)
    preferred_horizons = [h for h in horizon_order if h in horizons]
    preferred_horizons.extend(
        h for h in sorted(horizons) if h not in preferred_horizons
    )
    # Candidate-first grid: keep the full event identity fixed while comparing
    # its holding periods.  The old Latin traversal tested each event only once
    # at an arbitrary horizon, so "multi-horizon search" was merely telemetry.
    exec_params = _holding_exec_params(
        hypothesis,
        (hypothesis or {}).get("research_contract"),
    )
    for spec_i, spec in enumerate(specs):
        for horizon in preferred_horizons:
            for mapping_i, mapping in enumerate(mappings):
                direction = directions[(spec_i + mapping_i) % len(directions)]
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
            symbol=symbol, timeframe=timeframe, exec_params=exec_params,
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
        return (
            state_rank.get(row.get("research_state"), -1),
            1 if row.get("path_review_eligible") else 0,
            float(row.get("profit_first_rate") or -1.0),
            float(row.get("path_entry_score") or -1.0),
            float(row.get("mean_net") or -1e9),
            int(row.get("n_independent_events") or 0),
        )
    ordered = sorted(rows, key=rank, reverse=True)
    # Only an execution mapping implemented by the formal DSL may become the
    # admitted recipe.  Other mappings remain valuable diagnostics, but
    # allowing one to win here creates a candidate that can never be submitted
    # without changing its execution identity downstream.
    contract = (hypothesis or {}).get("research_contract") or {}
    for row in ordered:
        capability = recipe_policy.capability_from_row(row, contract)
        row["formal_capability"] = capability
    formally_executable = [
        row for row in ordered if (row.get("formal_capability") or {}).get("ok")
    ]
    # 可正式复现的候选优先；其中已过交接底线的再优先，避免被诊断态单因子掩盖。
    formal_ready = [
        row for row in formally_executable
        if row.get("passed") or row.get("handoff_floor")
        or row.get("research_state") == "READY_FOR_ASSEMBLY"
    ]
    best = (
        formal_ready[0] if formal_ready
        else (formally_executable[0] if formally_executable else None)
    )
    diagnostic_best = ordered[0] if ordered else None
    passed = bool(best and best.get("passed"))
    state = (best or diagnostic_best or {}).get("research_state") or "SAMPLE_INADEQUATE"

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
    ast_rows = [r for r in rows if r.get("event_kind") == "ast_compiled"]
    ast_hashes = sorted(set(
        str(r.get("event_ast_hash") or "") for r in ast_rows if r.get("event_ast_hash")
    ))
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
        "ast_compiled_n": len(ast_rows),
        "ast_formal_ok_n": sum(1 for r in ast_rows if r.get("event_ast_formal_ok")),
        "ast_hashes": ast_hashes[:24],
        "ast_compile_failures_n": len((hypothesis or {}).get("ast_compile_failures") or []),
    }
    return {
        "ok": True,
        "passed": passed,
        "hypothesis_id": hypothesis.get("hypothesis_id"),
        "research_state": state,
        "research_state_zh": RESEARCH_STATES_ZH.get(state, state),
        "failure_codes": list((best or diagnostic_best or {}).get("failure_codes") or []) + (
            ["formal_capability_blocked"]
            if best is None and diagnostic_best is not None else []
        ),
        "n_probes": len(rows),
        "best": best,
        "diagnostic_best": (
            {k: v for k, v in (diagnostic_best or {}).items()
             if k not in ("trade_returns", "pbo_bar_returns", "event_mask")}
            if diagnostic_best is not None else None
        ),
        "formal_execution_mappings": ["next_bar_open"],
        # The formal status of the admitted/best row must not inherit reasons
        # from unrelated diagnostic single-factor probes.  Keep the aggregate
        # list separately for audit, while the primary field describes exactly
        # the row that could advance.
        "formal_capability_reasons": list(
            ((best or diagnostic_best or {}).get("formal_capability") or {}).get(
                "reasons"
            ) or []
        ),
        "diagnostic_formal_capability_reasons": sorted(set(
            reason for row in ordered
            for reason in ((row.get("formal_capability") or {}).get("reasons") or [])
        )),
        "probes": [{k: v for k, v in r.items() if k not in ("trade_returns", "pbo_bar_returns", "event_mask")} for r in ordered],
        "coverage": coverage,
        "ast_compile": {
            "attempted": bool((hypothesis or {}).get("event_ast")),
            "compiled_n": len(ast_rows),
            "formal_ok_n": sum(1 for r in ast_rows if r.get("event_ast_formal_ok")),
            "hashes": ast_hashes[:24],
            "failures": list((hypothesis or {}).get("ast_compile_failures") or [])[:8],
        },
        "required_entry_timing": (hypothesis or {}).get("required_entry_timing") or {},
        "entry_timing_mapping_locked": timing_mode in ("bar_close", "next_bar_open"),
        "family_closed": False,
        "hard_rule_zh": "三轴未同时通过时禁止组装；单个窄探针失败不得宣判整个机制族死亡。",
        "at": _now(),
    }


def probe():
    return {
        "ok": True,
        "provider": "mechanism_preserving_probe_v2",
        "forbids": ["tuned_take_profit", "position_sizing", "complex_exit"],
        "mandatory_exit_identity": {
            "exit_policy": recipe_policy.EXIT_POLICY_MODE,
            "protective_stop_pct": recipe_policy.PROTECTIVE_STOP_PCT,
            "execution_leverage": recipe_policy.EXECUTION_LEVERAGE,
        },
        "evidence_axes": ["统计方向", "经济幅度", "执行可行性"],
        "research_states": RESEARCH_STATES_ZH,
        "uses_real_horizons": True,
        "clusters_independent_events": True,
        "family_close_from_single_probe": False,
        "at": _now(),
    }
