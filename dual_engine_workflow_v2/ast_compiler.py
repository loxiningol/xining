# -*- coding: utf-8 -*-
"""P2 minimal AST schema + deterministic compiler (no free-form code).

AI / materialization may only emit JSON AST. This module:
  validate_ast → compile_ast_to_mask (probe) → compile_ast_to_dsl (formal).
"""
from __future__ import print_function

import hashlib
import json
import math
from datetime import datetime

COMPARE_OPS = ("lt", "lte", "gt", "gte", "eq", "between", "cross_above", "cross_below")
BOOL_TYPES = ("all", "any", "not")
DELTA_TYPES = ("delta", "slope", "rate_of_change")
NODE_TYPES = (
    "compare", "quantile", "all", "any", "not",
    "delta", "slope", "rate_of_change",
    "state", "sequence", "relative", "exclude",
)
MAX_AST_DEPTH = 6
MAX_SEQUENCE_STEPS = 3
DEFAULT_QUANTILE_WINDOW = 240
DEFAULT_QUANTILE_MIN_HISTORY = 80

# Research-only features usable in probes; formal path needs GENERIC_FACTOR_TO_DSL.
RESEARCH_FEATURES = (
    "rsi_14", "bb_lower_dist", "bb_upper_dist", "bb_mid_reclaim", "bb_width",
    "close_z_20", "volume_z", "ret_3", "trend_bias_50_200",
    "bullish_reclaim", "reclaim_strength",
    "donchian20_long_break", "donchian20_short_break",
    "delta_rsi_14", "downside_velocity_decay", "squeeze_persistence",
    "volatility_acceleration", "exhaustion_score", "absorption_proxy",
    "expansion_score", "close_location", "btc_return_3", "asset_return_3",
)

STATE_VALUE_MAP = {
    ("volatility_state", "compressed"): {
        "type": "quantile", "feature": "bb_width", "side": "low", "q": 0.70,
    },
    ("volatility_state", "expanded"): {
        "type": "quantile", "feature": "bb_width", "side": "high", "q": 0.70,
    },
    ("squeeze_state", "on"): {
        "type": "quantile", "feature": "squeeze_persistence", "side": "high", "q": 0.70,
    },
    ("trend_state", "bullish"): {
        "type": "quantile", "feature": "trend_bias_50_200", "side": "high", "q": 0.55,
    },
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _f(x, default=None):
    try:
        if x is None:
            return default
        out = float(x)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def ast_fingerprint(ast):
    return hashlib.sha256(_canonical(ast or {}).encode("utf-8")).hexdigest()[:24]


def event_ast_hash(ast):
    return "ast_%s" % ast_fingerprint(ast)


def feature_registry():
    """Union of formal + research feature names."""
    try:
        from . import recipe_policy as rp
        formal = set(rp.GENERIC_FACTOR_TO_DSL.keys())
    except Exception:
        formal = set()
    return sorted(formal | set(RESEARCH_FEATURES))


def formal_feature_map():
    try:
        from . import recipe_policy as rp
        return dict(rp.GENERIC_FACTOR_TO_DSL)
    except Exception:
        return {}


def _err(code, detail=""):
    return {"code": code, "detail": str(detail or "")[:240]}


def validate_ast(ast, feature_registry_list=None, depth=0):
    """Strict schema validation. Returns {ok, errors, normalized}."""
    registry = set(feature_registry_list or feature_registry())
    errors = []

    def walk(node, d):
        if d > MAX_AST_DEPTH:
            errors.append(_err("ast_depth_exceeded", d))
            return None
        if not isinstance(node, dict):
            errors.append(_err("unsupported_node", "not_object"))
            return None
        ntype = str(node.get("type") or "")
        if ntype not in NODE_TYPES:
            errors.append(_err("unsupported_node", ntype or "missing"))
            return None
        if ntype == "compare":
            feature = str(node.get("feature") or "")
            op = str(node.get("op") or "")
            if feature not in registry:
                errors.append(_err("missing_feature", feature))
            if op not in COMPARE_OPS:
                errors.append(_err("unsupported_operator", op))
            out = {
                "type": "compare", "feature": feature, "op": op,
            }
            if op == "between":
                out["lower"] = _f(node.get("lower"), node.get("value"))
                out["upper"] = _f(node.get("upper"))
                if out["lower"] is None or out["upper"] is None:
                    errors.append(_err("unsupported_operator", "between_needs_lower_upper"))
            else:
                out["value"] = _f(node.get("value"))
                if out["value"] is None and op not in ("cross_above", "cross_below"):
                    # cross_* may use right_feature
                    if node.get("right_feature"):
                        rf = str(node.get("right_feature"))
                        if rf not in registry:
                            errors.append(_err("missing_feature", rf))
                        out["right_feature"] = rf
                    else:
                        errors.append(_err("unsupported_operator", "compare_missing_value"))
            return out
        if ntype == "quantile":
            feature = str(node.get("feature") or "")
            side = str(node.get("side") or "high")
            q = _f(node.get("q"), 0.75)
            if feature not in registry:
                errors.append(_err("missing_feature", feature))
            if side not in ("high", "low"):
                errors.append(_err("unsupported_operator", side))
            if q is None or not (0.5 < q < 1.0):
                errors.append(_err("unsupported_operator", "q_bounds"))
            return {
                "type": "quantile",
                "feature": feature,
                "side": side,
                "q": q,
                "window": int(node.get("window") or DEFAULT_QUANTILE_WINDOW),
                "min_history": int(node.get("min_history") or DEFAULT_QUANTILE_MIN_HISTORY),
            }
        if ntype in BOOL_TYPES:
            if ntype == "not":
                child = walk(node.get("child") or node.get("condition") or node.get("children"), d + 1)
                return {"type": "not", "child": child}
            children_in = node.get("children") or []
            if not isinstance(children_in, list) or not children_in:
                errors.append(_err("unsupported_node", "%s_empty" % ntype))
                return {"type": ntype, "children": []}
            children = [walk(c, d + 1) for c in children_in]
            return {"type": ntype, "children": children}
        if ntype in DELTA_TYPES:
            feature = str(node.get("feature") or "")
            op = str(node.get("op") or "gt")
            bars = int(node.get("bars") or 3)
            if feature not in registry and ("delta_%s" % feature) not in registry:
                # allow base feature; delta computed at mask time
                if feature not in registry:
                    errors.append(_err("missing_feature", feature))
            if op not in COMPARE_OPS or op in ("between", "cross_above", "cross_below"):
                if op not in ("lt", "lte", "gt", "gte", "eq"):
                    errors.append(_err("unsupported_operator", op))
            if bars < 1 or bars > 60:
                errors.append(_err("unsupported_operator", "delta_bars"))
            return {
                "type": ntype,
                "feature": feature,
                "bars": bars,
                "op": op,
                "value": _f(node.get("value"), 0.0),
            }
        if ntype == "state":
            sf = str(node.get("state_feature") or "")
            op = str(node.get("op") or "eq")
            value = node.get("value")
            mapped = STATE_VALUE_MAP.get((sf, str(value)))
            if mapped is None:
                # allow direct feature compare
                if sf not in registry:
                    errors.append(_err("missing_feature", sf))
                return {
                    "type": "state",
                    "state_feature": sf,
                    "op": op,
                    "value": value,
                    "expanded": {
                        "type": "compare",
                        "feature": sf,
                        "op": "gt" if op == "eq" else op,
                        "value": _f(value, 0.0),
                    },
                }
            return {
                "type": "state",
                "state_feature": sf,
                "op": op,
                "value": value,
                "expanded": walk(mapped, d + 1),
            }
        if ntype == "sequence":
            steps = node.get("steps") or []
            if not isinstance(steps, list) or len(steps) < 2:
                errors.append(_err("sequence_too_deep", "need_>=2_steps"))
                return {"type": "sequence", "steps": []}
            if len(steps) > MAX_SEQUENCE_STEPS:
                errors.append(_err("sequence_too_deep", len(steps)))
            out_steps = []
            for index, step in enumerate(steps[:MAX_SEQUENCE_STEPS]):
                if not isinstance(step, dict):
                    errors.append(_err("unsupported_node", "sequence_step"))
                    continue
                event = step.get("event") or step.get("ast") or step
                if isinstance(event, str):
                    # named stub → expand via known templates
                    event = _named_event_ast(event)
                child = walk(event, d + 1)
                within = int(step.get("within_bars") or (3 if index > 0 else 0))
                out_steps.append({"event": child, "within_bars": within})
            return {"type": "sequence", "steps": out_steps}
        if ntype == "relative":
            left = str(node.get("left_feature") or "")
            right = str(node.get("right_feature") or "")
            op = str(node.get("op") or "gt")
            if left not in registry:
                errors.append(_err("relative_feature_missing", left))
            if right not in registry:
                errors.append(_err("relative_feature_missing", right))
            if op not in ("lt", "lte", "gt", "gte", "eq"):
                errors.append(_err("unsupported_operator", op))
            return {
                "type": "relative",
                "left_feature": left,
                "right_feature": right,
                "op": op,
                "offset": _f(node.get("offset"), 0.0) or 0.0,
            }
        if ntype == "exclude":
            cond = node.get("condition") or node.get("child") or {}
            if isinstance(cond, dict) and cond.get("type") == "event" and cond.get("name"):
                cond = _named_event_ast(str(cond.get("name")))
            child = walk(cond, d + 1)
            return {"type": "exclude", "condition": child}
        errors.append(_err("unsupported_node", ntype))
        return None

    normalized = walk(ast, depth)
    return {
        "ok": not errors and normalized is not None,
        "errors": errors,
        "normalized": normalized,
        "at": _now(),
    }


def _named_event_ast(name):
    name = str(name or "")
    catalog = {
        "oversold_event": {
            "type": "all",
            "children": [
                {"type": "quantile", "feature": "rsi_14", "side": "low", "q": 0.75},
                {"type": "quantile", "feature": "bb_lower_dist", "side": "low", "q": 0.75},
            ],
        },
        "reclaim_event": {
            "type": "quantile", "feature": "bb_mid_reclaim", "side": "high", "q": 0.70,
        },
        "confirmation_event": {
            "type": "quantile", "feature": "bullish_reclaim", "side": "high", "q": 0.70,
        },
        "failure_path_breakdown": {
            "type": "all",
            "children": [
                {"type": "quantile", "feature": "expansion_score", "side": "high", "q": 0.75},
                {"type": "quantile", "feature": "bb_width", "side": "high", "q": 0.75},
            ],
        },
    }
    return catalog.get(name) or {
        "type": "quantile", "feature": "rsi_14", "side": "low", "q": 0.75,
    }


def _series_delta(values, bars=3):
    values = list(values or [])
    out = [None] * len(values)
    b = max(1, int(bars))
    for i in range(len(values)):
        if i < b:
            continue
        a = _f(values[i])
        c = _f(values[i - b])
        if a is None or c is None:
            continue
        out[i] = a - c
    return out


def _series_slope(values, bars=3):
    """Simple end-point slope = delta / bars."""
    b = max(1, int(bars))
    delta = _series_delta(values, b)
    return [None if v is None else (v / float(b)) for v in delta]


def _compare_mask(left_vals, op, right_vals_or_scalar, right_is_series=False):
    n = len(left_vals)
    out = [False] * n
    for i in range(n):
        left = _f(left_vals[i])
        if left is None:
            continue
        if right_is_series:
            right = _f(right_vals_or_scalar[i] if i < len(right_vals_or_scalar) else None)
        else:
            right = _f(right_vals_or_scalar)
        if right is None and op not in ("cross_above", "cross_below"):
            continue
        if op == "lt":
            out[i] = left < right
        elif op == "lte":
            out[i] = left <= right
        elif op == "gt":
            out[i] = left > right
        elif op == "gte":
            out[i] = left >= right
        elif op == "eq":
            out[i] = abs(left - right) <= 1e-12
        elif op == "between":
            lo, hi = right_vals_or_scalar
            out[i] = lo <= left <= hi
        elif op == "cross_above" and i > 0 and right_is_series:
            pl = _f(left_vals[i - 1])
            pr = _f(right_vals_or_scalar[i - 1])
            if pl is not None and pr is not None and right is not None:
                out[i] = pl <= pr and left > right
        elif op == "cross_below" and i > 0 and right_is_series:
            pl = _f(left_vals[i - 1])
            pr = _f(right_vals_or_scalar[i - 1])
            if pl is not None and pr is not None and right is not None:
                out[i] = pl >= pr and left < right
    return out


def _was_true_within_mask(child_mask, bars):
    """Causal: True at i if child was True in any of i-bars .. i-1 (not i)."""
    n = len(child_mask)
    b = max(1, int(bars))
    out = [False] * n
    for i in range(n):
        lo = max(0, i - b)
        hi = i  # exclusive current
        for k in range(lo, hi):
            if child_mask[k]:
                out[i] = True
                break
    return out


def compile_ast_to_mask(ast, factor_matrix, feature_registry_list=None):
    """Deterministic probe mask from AST."""
    validated = validate_ast(ast, feature_registry_list=feature_registry_list)
    if not validated.get("ok"):
        return {
            "ok": False,
            "mask": [],
            "compile_report": {
                "stage": "validate",
                "errors": validated.get("errors"),
            },
        }
    matrix = factor_matrix or {}
    cache = {}

    def get_series(name):
        if name in cache:
            return cache[name]
        vals = list(matrix.get(name) or [])
        cache[name] = vals
        return vals

    def eval_node(node):
        if not isinstance(node, dict):
            return []
        ntype = node.get("type")
        if ntype == "compare":
            left = get_series(node["feature"])
            op = node["op"]
            if op == "between":
                return _compare_mask(left, "between", (node["lower"], node["upper"]))
            if node.get("right_feature"):
                right = get_series(node["right_feature"])
                return _compare_mask(left, op, right, right_is_series=True)
            return _compare_mask(left, op, node.get("value"))
        if ntype == "quantile":
            from .probe_protocol import _causal_quantile_mask
            return _causal_quantile_mask(
                get_series(node["feature"]),
                side=node.get("side") or "high",
                q=node.get("q") or 0.75,
                window=node.get("window") or DEFAULT_QUANTILE_WINDOW,
                min_history=node.get("min_history") or DEFAULT_QUANTILE_MIN_HISTORY,
            )
        if ntype == "all":
            children = [eval_node(c) for c in (node.get("children") or [])]
            if not children:
                return []
            n = min(len(c) for c in children)
            return [all(c[i] for c in children) for i in range(n)]
        if ntype == "any":
            children = [eval_node(c) for c in (node.get("children") or [])]
            if not children:
                return []
            n = min(len(c) for c in children)
            return [any(c[i] for c in children) for i in range(n)]
        if ntype == "not":
            child = eval_node(node.get("child"))
            return [not x for x in child]
        if ntype in DELTA_TYPES:
            base = node["feature"]
            pref = "delta_%s" % base if ntype == "delta" else None
            if pref and pref in matrix:
                series = get_series(pref)
            elif ntype == "slope":
                series = _series_slope(get_series(base), node.get("bars") or 3)
            else:
                series = _series_delta(get_series(base), node.get("bars") or 3)
            return _compare_mask(series, node.get("op") or "gt", node.get("value") or 0.0)
        if ntype == "state":
            return eval_node(node.get("expanded") or {})
        if ntype == "sequence":
            steps = node.get("steps") or []
            if len(steps) < 2:
                return []
            masks = [eval_node(s.get("event")) for s in steps]
            n = min(len(m) for m in masks) if masks else 0
            masks = [m[:n] for m in masks]
            # last step must be true now; prior steps within windows walking back
            out = list(masks[-1])
            for i in range(len(steps) - 2, -1, -1):
                within = int((steps[i + 1] or {}).get("within_bars") or 3)
                prior = masks[i]
                nxt = out
                rebuilt = [False] * n
                for j in range(n):
                    if not nxt[j]:
                        continue
                    if any(prior[k] for k in range(max(0, j - within), j)):
                        rebuilt[j] = True
                out = rebuilt
            return out
        if ntype == "relative":
            left_name = node["left_feature"]
            right_name = node["right_feature"]
            if left_name not in matrix:
                raise RuntimeError("relative_feature_missing:%s" % left_name)
            if right_name not in matrix:
                raise RuntimeError("relative_feature_missing:%s" % right_name)
            left = get_series(left_name)
            right = get_series(right_name)
            if not left or not right:
                raise RuntimeError("relative_feature_missing:%s|%s" % (left_name, right_name))
            offset = float(node.get("offset") or 0.0)
            adjusted = [
                None if _f(r) is None else (_f(r) + offset) for r in right
            ]
            return _compare_mask(left, node.get("op") or "gt", adjusted, right_is_series=True)
        if ntype == "exclude":
            child = eval_node(node.get("condition"))
            return [not x for x in child]
        return []

    try:
        mask = eval_node(validated["normalized"])
    except Exception as exc:
        detail = str(exc)[:240]
        code = "mask_eval_error"
        if "relative_feature_missing" in detail:
            code = "relative_feature_missing"
        elif "missing_feature" in detail:
            code = "missing_feature"
        return {
            "ok": False,
            "mask": [],
            "compile_report": {
                "stage": "mask_eval",
                "code": code,
                "exception_type": type(exc).__name__,
                "detail": detail,
            },
        }
    return {
        "ok": True,
        "mask": mask,
        "normalized": validated["normalized"],
        "event_ast_hash": event_ast_hash(validated["normalized"]),
        "compile_report": {"stage": "mask_ok", "n": len(mask), "true_n": sum(1 for x in mask if x)},
    }


def _dsl_id(prefix, counter):
    counter[0] += 1
    return ("%s_%d" % (prefix, counter[0]))[:80]


def _map_feature(name, formal_map, reasons):
    name = str(name or "")
    if name in formal_map:
        return formal_map[name], True
    reasons.append("generic_factor_not_formally_supported:%s" % name)
    return name, False


def compile_ast_to_dsl(ast, feature_registry_list=None):
    """Compile AST → formal DSL entry tree. formal_ok only if all features mapped."""
    validated = validate_ast(ast, feature_registry_list=feature_registry_list)
    if not validated.get("ok"):
        return {
            "ok": False,
            "formal_ok": False,
            "entry_tree": None,
            "terms": [],
            "reasons": ["ast_validation_failed"],
            "errors": validated.get("errors"),
        }
    formal_map = formal_feature_map()
    reasons = []
    counter = [0]
    terms = []
    exclusions = []

    def to_dsl(node):
        if not isinstance(node, dict):
            return None
        ntype = node.get("type")
        if ntype == "compare":
            feat, ok = _map_feature(node["feature"], formal_map, reasons)
            leaf = {
                "id": _dsl_id("e_cmp", counter),
                "left": {"feature": feat},
                "op": node["op"],
            }
            if node["op"] == "between":
                leaf["lower"] = node["lower"]
                leaf["upper"] = node["upper"]
            elif node.get("right_feature"):
                rfeat, rok = _map_feature(node["right_feature"], formal_map, reasons)
                leaf["right"] = {"feature": rfeat}
                ok = ok and rok
            else:
                leaf["right"] = {"value": node.get("value")}
            terms.append({
                "factor": node["feature"], "op": node["op"],
                "value": node.get("value"), "node_type": "compare",
            })
            return leaf
        if ntype == "quantile":
            feat, ok = _map_feature(node["feature"], formal_map, reasons)
            side = node.get("side") or "high"
            q = float(node.get("q") or 0.75)
            threshold_q = q if side == "high" else 1.0 - q
            window = int(node.get("window") or DEFAULT_QUANTILE_WINDOW)
            min_history = int(node.get("min_history") or DEFAULT_QUANTILE_MIN_HISTORY)
            terms.append({
                "factor": node["feature"],
                "side": side,
                "q": q,
                "window": window,
                "min_history": min_history,
                "threshold_source": "prior_only_rolling_quantile",
                "node_type": "quantile",
            })
            return {
                "id": _dsl_id("e_q", counter),
                "left": {"feature": feat},
                "op": "gte" if side == "high" else "lte",
                "right": {
                    "quantile_of": feat,
                    "q": threshold_q,
                    "window": window,
                    "min_history": min_history,
                },
            }
        if ntype == "all":
            children = [to_dsl(c) for c in (node.get("children") or [])]
            children = [c for c in children if c is not None]
            return {"all": children} if children else None
        if ntype == "any":
            children = [to_dsl(c) for c in (node.get("children") or [])]
            children = [c for c in children if c is not None]
            return {"any": children} if children else None
        if ntype == "not":
            child = to_dsl(node.get("child"))
            return {"not": child} if child is not None else None
        if ntype in DELTA_TYPES:
            # Prefer precomputed delta_* formal feature if registered; else research-only.
            base = node["feature"]
            delta_name = "delta_%s" % base if ntype == "delta" else base
            feat, ok = _map_feature(delta_name if delta_name in formal_map else base, formal_map, reasons)
            if not ok and base in formal_map:
                # approximate with offset difference via left offset — formal partial
                feat = formal_map[base]
                reasons.append("delta_approximated_with_feature_offset:%s" % base)
                leaf = {
                    "id": _dsl_id("e_delta", counter),
                    "left": {"feature": feat, "offset": 0},
                    "op": node.get("op") or "gt",
                    "right": {
                        "feature": feat,
                        "offset": int(node.get("bars") or 3),
                        "scale": -1.0 if (node.get("op") or "gt") in ("gt", "gte") else 1.0,
                    },
                }
                # Simpler: compare delta feature if present in research only → formal_ok false
                terms.append({
                    "factor": base, "node_type": ntype, "bars": node.get("bars"),
                    "op": node.get("op"), "value": node.get("value"),
                })
                # Use literal threshold on mapped feature change via value leaf when possible
                return {
                    "id": _dsl_id("e_delta", counter),
                    "left": {"feature": feat},
                    "op": node.get("op") or "gt",
                    "right": {"value": float(node.get("value") or 0.0)},
                }
            terms.append({
                "factor": delta_name if delta_name in formal_map else base,
                "node_type": ntype,
                "bars": node.get("bars"),
                "op": node.get("op"),
                "value": node.get("value"),
            })
            return {
                "id": _dsl_id("e_delta", counter),
                "left": {"feature": feat},
                "op": node.get("op") or "gt",
                "right": {"value": float(node.get("value") or 0.0)},
            }
        if ntype == "state":
            return to_dsl(node.get("expanded") or {})
        if ntype == "sequence":
            steps = node.get("steps") or []
            if len(steps) < 2:
                reasons.append("sequence_too_short")
                return None
            # last step now AND prior steps via was_true_within nesting
            expr = to_dsl(steps[-1].get("event"))
            for i in range(len(steps) - 2, -1, -1):
                within = int((steps[i + 1] or {}).get("within_bars") or 3)
                prior = to_dsl(steps[i].get("event"))
                if expr is None or prior is None:
                    reasons.append("sequence_step_compile_failed")
                    return None
                expr = {
                    "all": [
                        expr,
                        {"was_true_within": {"bars": within, "expr": prior}},
                    ]
                }
            return expr
        if ntype == "relative":
            left, ok_l = _map_feature(node["left_feature"], formal_map, reasons)
            right, ok_r = _map_feature(node["right_feature"], formal_map, reasons)
            offset = float(node.get("offset") or 0.0)
            terms.append({
                "factor": node["left_feature"],
                "right_feature": node["right_feature"],
                "node_type": "relative",
                "op": node.get("op"),
                "offset": offset,
            })
            if abs(offset) > 1e-15:
                reasons.append("relative_offset_not_formally_supported")
            return {
                "id": _dsl_id("e_rel", counter),
                "left": {"feature": left},
                "op": node.get("op") or "gt",
                "right": {"feature": right},
            }
        if ntype == "exclude":
            child = to_dsl(node.get("condition"))
            exclusions.append({"node_type": "exclude"})
            return {"not": child} if child is not None else None
        reasons.append("unsupported_node_for_dsl:%s" % ntype)
        return None

    tree = to_dsl(validated["normalized"])
    formal_ok = bool(tree) and not any(
        r.startswith("generic_factor_not_formally_supported")
        or r.startswith("relative_feature_missing")
        or r.startswith("delta_approximated")
        or r.startswith("relative_offset")
        or r.startswith("sequence_")
        or r.startswith("unsupported_node")
        for r in reasons
    )
    # Require at least some structure
    if tree is None:
        formal_ok = False
        reasons.append("dsl_tree_empty")
    return {
        "ok": tree is not None,
        "formal_ok": formal_ok,
        "entry_tree": tree,
        "terms": terms,
        "exclusions": exclusions,
        "reasons": list(dict.fromkeys(reasons)),
        "normalized": validated["normalized"],
        "event_ast_hash": event_ast_hash(validated["normalized"]),
        "at": _now(),
    }


def representation_asts_for_direction(direction="long"):
    """Six logically distinct AST templates (P2 §六表征)."""
    direction = str(direction or "long").lower()
    longish = direction in ("long", "buy")
    oversold = {
        "type": "all",
        "children": [
            {"type": "quantile", "feature": "rsi_14", "side": "low", "q": 0.75},
            {"type": "quantile", "feature": "bb_lower_dist", "side": "low", "q": 0.75},
        ],
    }
    reclaim = {"type": "quantile", "feature": "bb_mid_reclaim", "side": "high", "q": 0.70}
    confirm = {"type": "quantile", "feature": "bullish_reclaim", "side": "high", "q": 0.70}
    failure = {
        "type": "all",
        "children": [
            {"type": "quantile", "feature": "expansion_score", "side": "high", "q": 0.75},
            {"type": "quantile", "feature": "bb_width", "side": "high", "q": 0.75},
        ],
    }
    templates = [
        (
            "threshold",
            {
                "type": "all",
                "children": [
                    {"type": "compare", "feature": "rsi_14", "op": "lt", "value": 35.0},
                    {"type": "compare", "feature": "bb_mid_reclaim", "op": "gt", "value": 0.0},
                ],
            },
        ),
        (
            "rank",
            {
                "type": "all",
                "children": [
                    {"type": "quantile", "feature": "rsi_14", "side": "low", "q": 0.80},
                    {"type": "quantile", "feature": "bb_lower_dist", "side": "low", "q": 0.80},
                    {"type": "quantile", "feature": "bb_mid_reclaim", "side": "high", "q": 0.70},
                ],
            },
        ),
        (
            "delta",
            {
                "type": "all",
                "children": [
                    {"type": "quantile", "feature": "rsi_14", "side": "low", "q": 0.75},
                    {"type": "delta", "feature": "rsi_14", "bars": 3, "op": "gt", "value": 0.0},
                    {"type": "quantile", "feature": "bb_mid_reclaim", "side": "high", "q": 0.65},
                ],
            },
        ),
        (
            "sequence",
            {
                "type": "sequence",
                "steps": [
                    {"event": oversold},
                    {"event": reclaim, "within_bars": 3},
                    {"event": confirm, "within_bars": 2},
                ],
            },
        ),
        (
            "state",
            {
                "type": "all",
                "children": [
                    oversold,
                    {
                        "type": "state",
                        "state_feature": "volatility_state",
                        "op": "eq",
                        "value": "compressed",
                    },
                    reclaim,
                ],
            },
        ),
        (
            "relative_exclusion",
            {
                "type": "all",
                "children": [
                    oversold,
                    reclaim,
                    {
                        "type": "exclude",
                        "condition": failure,
                    },
                ],
            },
        ),
    ]
    if not longish:
        # Mirror: overbought fade for shorts — keep structure, flip sides.
        templates = [
            (
                "threshold",
                {
                    "type": "all",
                    "children": [
                        {"type": "compare", "feature": "rsi_14", "op": "gt", "value": 65.0},
                        {"type": "compare", "feature": "bb_upper_dist", "op": "lt", "value": 0.0},
                    ],
                },
            ),
            (
                "rank",
                {
                    "type": "all",
                    "children": [
                        {"type": "quantile", "feature": "rsi_14", "side": "high", "q": 0.80},
                        {"type": "quantile", "feature": "bb_upper_dist", "side": "low", "q": 0.80},
                    ],
                },
            ),
            (
                "delta",
                {
                    "type": "all",
                    "children": [
                        {"type": "quantile", "feature": "rsi_14", "side": "high", "q": 0.75},
                        {"type": "delta", "feature": "rsi_14", "bars": 3, "op": "lt", "value": 0.0},
                    ],
                },
            ),
            (
                "sequence",
                {
                    "type": "sequence",
                    "steps": [
                        {"event": {"type": "quantile", "feature": "rsi_14", "side": "high", "q": 0.75}},
                        {"event": {"type": "quantile", "feature": "bb_upper_dist", "side": "low", "q": 0.70}, "within_bars": 3},
                    ],
                },
            ),
            (
                "state",
                {
                    "type": "all",
                    "children": [
                        {"type": "quantile", "feature": "rsi_14", "side": "high", "q": 0.75},
                        {"type": "state", "state_feature": "volatility_state", "op": "eq", "value": "compressed"},
                    ],
                },
            ),
            (
                "relative_exclusion",
                {
                    "type": "all",
                    "children": [
                        {"type": "quantile", "feature": "rsi_14", "side": "high", "q": 0.75},
                        {"type": "exclude", "condition": failure},
                    ],
                },
            ),
        ]
    out = []
    for rtype, tree in templates:
        v = validate_ast(tree)
        out.append({
            "representation_type": rtype,
            "ast": v.get("normalized") or tree,
            "event_ast_hash": event_ast_hash(v.get("normalized") or tree),
            "relative_skipped": rtype == "relative_exclusion",
        })
    return out


def attach_relative_if_available(ast_row, factor_matrix):
    """If btc_return_3 & asset_return_3 exist, wrap relative_exclusion with relative node."""
    matrix = factor_matrix or {}
    if "btc_return_3" not in matrix or "asset_return_3" not in matrix:
        return ast_row
    if (ast_row or {}).get("representation_type") != "relative_exclusion":
        return ast_row
    base = dict(ast_row)
    tree = dict(base.get("ast") or {})
    rel = {
        "type": "relative",
        "left_feature": "asset_return_3",
        "op": "gt",
        "right_feature": "btc_return_3",
        "offset": 0.0,
    }
    if tree.get("type") == "all":
        children = list(tree.get("children") or [])
        children.insert(0, rel)
        tree = {"type": "all", "children": children}
    else:
        tree = {"type": "all", "children": [rel, tree]}
    v = validate_ast(tree)
    base["ast"] = v.get("normalized") or tree
    base["event_ast_hash"] = event_ast_hash(base["ast"])
    base["relative_skipped"] = False
    return base


def probe():
    sample = representation_asts_for_direction("long")[3]
    v = validate_ast(sample["ast"])
    d = compile_ast_to_dsl(sample["ast"])
    return {
        "ok": bool(v.get("ok")),
        "n_templates": len(representation_asts_for_direction("long")),
        "sequence_formal_ok": d.get("formal_ok"),
        "node_types": list(NODE_TYPES),
    }
