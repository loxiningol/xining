# -*- coding: utf-8 -*-
"""WINDTALKER PHASE 3 — research-layer DSL extension (shadow/probe only).

Does NOT replace production qiyu_strategy_dsl_v1. Production FEATURES remain
unchanged. Research probes import this module exclusively.
Fail-closed: missing/stale research features → expression False (no signal).
"""
from __future__ import print_function

import copy
import hashlib
import json
import math

import auto_trade_strategy_dsl as base

SCHEMA = "qiyu_strategy_dsl_research_v1"
BASE_FEATURES = set(base.FEATURES)

# Multi-source research features (aligned to bar ts; no lookahead in builder).
RESEARCH_FEATURES = {
    # OI
    "oi", "oi_z20", "oi_delta_pct", "oi_crowding",
    # Funding / basis
    "funding_rate", "funding_z20", "basis_bps", "basis_z20",
    # Taker / aggression flow (OKX taker-volume or microstructure imbalance)
    "taker_buy", "taker_sell", "taker_imbalance", "taker_imbalance_z20",
    "flow_imbalance",
    # Cross-asset sync
    "lead_ret1", "lead_ret3", "lag_ret1", "lead_lag_corr20", "cross_sync_score",
    # Event / state machine helpers (numeric encodings)
    "event_code", "state_id", "seq_age", "regime_ok",
}

FEATURES = BASE_FEATURES | RESEARCH_FEATURES
OPS = set(base.OPS) | {"rising_for", "falling_for"}  # sequence helpers
LOGICAL = set(base.LOGICAL)
MAX_DEPTH = 8
MAX_LEAVES = 48
MAX_LOOKBACK = 240

INSTRUMENTS = set(base.INSTRUMENTS)


class ResearchDSLError(ValueError):
    pass


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def dsl_hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _offset(value):
    try:
        value = int(value or 0)
    except Exception:
        raise ResearchDSLError("offset must be integer")
    if value < 0 or value > MAX_LOOKBACK:
        raise ResearchDSLError("offset outside 0..%d" % MAX_LOOKBACK)
    return value


def _number(value):
    if isinstance(value, bool):
        raise ResearchDSLError("boolean is not a numeric threshold")
    try:
        value = float(value)
    except Exception:
        raise ResearchDSLError("threshold must be numeric")
    if not math.isfinite(value) or abs(value) > 1e12:
        raise ResearchDSLError("threshold not finite / too large")
    return value


def _validate_operand(operand):
    if not isinstance(operand, dict):
        raise ResearchDSLError("operand must be object")
    keys = set(operand)
    if "feature" in operand:
        if keys - {"feature", "offset"}:
            raise ResearchDSLError("feature operand unknown fields")
        feature = str(operand.get("feature") or "")
        if feature not in FEATURES:
            raise ResearchDSLError("unsupported feature: %s" % feature)
        _offset(operand.get("offset", 0))
        return
    if "value" in operand:
        if keys != {"value"}:
            raise ResearchDSLError("constant operand unknown fields")
        _number(operand.get("value"))
        return
    raise ResearchDSLError("operand requires feature or value")


def _walk(node, depth=0, counter=None, seen_ids=None, phase=None):
    counter = counter if counter is not None else [0]
    seen_ids = seen_ids if seen_ids is not None else set()
    if depth > MAX_DEPTH or not isinstance(node, dict):
        raise ResearchDSLError("expression depth/type invalid")
    logical = [k for k in LOGICAL if k in node]
    if logical:
        if len(logical) != 1 or len(node) != 1:
            raise ResearchDSLError("logical node must contain exactly one operator")
        op = logical[0]
        children = node[op]
        if op == "not":
            _walk(children, depth + 1, counter, seen_ids, phase=phase)
        else:
            if not isinstance(children, list) or not children:
                raise ResearchDSLError("all/any requires non-empty list")
            for child in children:
                _walk(child, depth + 1, counter, seen_ids, phase=phase)
        return
    # state machine transition node
    if "state_machine" in node:
        sm = node["state_machine"]
        if not isinstance(sm, dict) or "states" not in sm or "transitions" not in sm:
            raise ResearchDSLError("state_machine requires states+transitions")
        for tr in sm["transitions"]:
            _walk(tr.get("when") or {"all": []}, depth + 1, counter, seen_ids, phase=phase)
        return
    # event sequence node: ordered conditions that must fire in order within window
    if "event_sequence" in node:
        seq = node["event_sequence"]
        if not isinstance(seq, dict) or "steps" not in seq:
            raise ResearchDSLError("event_sequence requires steps")
        for step in seq["steps"]:
            _walk(step, depth + 1, counter, seen_ids, phase=phase)
        return
    allowed = {"id", "left", "op", "right", "lower", "upper", "role", "bars"}
    if set(node) - allowed:
        raise ResearchDSLError("condition contains unknown fields")
    cid = str(node.get("id") or "")
    if not cid or len(cid) > 80:
        raise ResearchDSLError("condition id required and <=80")
    if cid in seen_ids:
        raise ResearchDSLError("condition ids must be unique")
    seen_ids.add(cid)
    op = str(node.get("op") or "")
    if op not in OPS:
        raise ResearchDSLError("unsupported operator: %s" % op)
    _validate_operand(node.get("left"))
    if op == "between":
        _number(node.get("lower"))
        _number(node.get("upper"))
    elif op in ("rising_for", "falling_for"):
        _number(node.get("bars") if "bars" in node else node.get("right", {}).get("value", 3))
    else:
        _validate_operand(node.get("right"))
    role = node.get("role")
    if role is not None:
        if phase != "exit":
            raise ResearchDSLError("role only valid on exit")
        if role not in ("take_profit", "invalidation", "dynamic"):
            raise ResearchDSLError("bad exit role")
    counter[0] += 1
    if counter[0] > MAX_LEAVES:
        raise ResearchDSLError("too many conditions")


def validate_strategy(strategy):
    if not isinstance(strategy, dict):
        raise ResearchDSLError("strategy must be object")
    allowed = {
        "schema", "key", "name", "direction", "timeframe",
        "supported_instruments", "entry", "exit", "max_hold_bars",
        "description", "origin", "version", "live_enabled",
        "auto_trade_eligible", "proxy_declarations", "multi_asset",
        "research_only", "data_sources", "state_machine_def",
        "approved_version_hash",
    }
    if set(strategy) - allowed:
        raise ResearchDSLError("unknown top-level fields: %s" % (set(strategy) - allowed))
    if strategy.get("schema") != SCHEMA:
        raise ResearchDSLError("unsupported research DSL schema")
    if not strategy.get("research_only", True):
        raise ResearchDSLError("research DSL must set research_only=true")
    if strategy.get("live_enabled") or strategy.get("auto_trade_eligible"):
        raise ResearchDSLError("research strategies cannot be live/auto_trade eligible")
    key = str(strategy.get("key") or "")
    if not key or len(key) > 100 or not all(ch.isalnum() or ch == "_" for ch in key):
        raise ResearchDSLError("invalid strategy key")
    if strategy.get("direction") not in ("long", "short"):
        raise ResearchDSLError("direction must be long or short")
    if strategy.get("timeframe") not in ("1h", "4h", "15m", "5m"):
        raise ResearchDSLError("unsupported timeframe")
    instruments = strategy.get("supported_instruments") or []
    if not isinstance(instruments, list) or not instruments or len(instruments) > 12:
        raise ResearchDSLError("supported_instruments invalid")
    if any(str(i) not in INSTRUMENTS for i in instruments):
        raise ResearchDSLError("unsupported instrument")
    hold = int(strategy.get("max_hold_bars") or 0)
    if hold < 1 or hold > 240:
        raise ResearchDSLError("max_hold_bars outside 1..240")
    # proxy declarations required when research features used
    proxies = strategy.get("proxy_declarations") or []
    if not isinstance(proxies, list):
        raise ResearchDSLError("proxy_declarations must be list")
    entry_count = [0]
    exit_count = [0]
    _walk(strategy.get("entry"), counter=entry_count, seen_ids=set(), phase="entry")
    _walk(strategy.get("exit"), counter=exit_count, seen_ids=set(), phase="exit")
    if entry_count[0] < 2:
        raise ResearchDSLError("entry requires >=2 independent conditions")
    return copy.deepcopy(strategy)


def _col(frame, feature):
    # pandas or dict-of-lists frame
    if hasattr(frame, "columns"):
        if feature not in frame.columns:
            raise ResearchDSLError("feature absent (fail-closed): %s" % feature)
        return frame[feature]
    if feature not in frame:
        raise ResearchDSLError("feature absent (fail-closed): %s" % feature)
    return frame[feature]


def _at(series, position):
    if hasattr(series, "iloc"):
        return float(series.iloc[position])
    return float(series[position])


def _operand(frame, index, operand, extra_offset=0):
    if "value" in operand:
        return float(operand["value"])
    offset = _offset(operand.get("offset", 0)) + int(extra_offset)
    position = index - offset
    if position < 0:
        raise IndexError("insufficient lookback")
    value = _at(_col(frame, operand["feature"]), position)
    if not math.isfinite(value):
        raise ValueError("indicator not finite (fail-closed)")
    return value


def _eval_leaf(frame, index, node, explain=False):
    op = node["op"]
    try:
        left = _operand(frame, index, node["left"])
        if op == "between":
            lo, hi = float(node["lower"]), float(node["upper"])
            passed = lo <= left <= hi
            right = [lo, hi]
        elif op in ("rising_for", "falling_for"):
            bars = int(node.get("bars") or (node.get("right") or {}).get("value") or 3)
            ok = True
            for k in range(bars):
                a = _operand(frame, index, node["left"], k)
                b = _operand(frame, index, node["left"], k + 1)
                if op == "rising_for" and not (a > b):
                    ok = False
                    break
                if op == "falling_for" and not (a < b):
                    ok = False
                    break
            passed = ok
            right = bars
        else:
            right = _operand(frame, index, node["right"])
            if op == "lt":
                passed = left < right
            elif op == "lte":
                passed = left <= right
            elif op == "gt":
                passed = left > right
            elif op == "gte":
                passed = left >= right
            elif op == "eq":
                passed = abs(left - right) <= 1e-12
            elif op == "cross_above":
                pl = _operand(frame, index, node["left"], 1)
                pr = _operand(frame, index, node["right"], 1)
                passed = pl <= pr and left > right
            elif op == "cross_below":
                pl = _operand(frame, index, node["left"], 1)
                pr = _operand(frame, index, node["right"], 1)
                passed = pl >= pr and left < right
            else:
                passed = False
        detail = {"condition_id": node.get("id"), "passed": bool(passed),
                  "left": left, "operator": op, "right": right}
    except Exception as exc:
        # fail-closed
        passed = False
        detail = {"condition_id": node.get("id"), "passed": False,
                  "operator": op, "error": str(exc), "fail_closed": True}
    return bool(passed), [detail] if explain else []


def evaluate_expression(frame, index, node, explain=False):
    if "all" in node:
        children = [evaluate_expression(frame, index, c, explain=explain) for c in node["all"]]
        passed = all(x[0] for x in children)
        details = sum((x[1] for x in children), [])
        return passed, details
    if "any" in node:
        children = [evaluate_expression(frame, index, c, explain=explain) for c in node["any"]]
        passed = any(x[0] for x in children)
        details = sum((x[1] for x in children), [])
        return passed, details
    if "not" in node:
        passed, details = evaluate_expression(frame, index, node["not"], explain=explain)
        return (not passed), details
    if "event_sequence" in node:
        # Ordered steps within max_bars window ending at index (no lookahead).
        seq = node["event_sequence"]
        steps = seq.get("steps") or []
        max_bars = int(seq.get("max_bars") or 12)
        cursor = index - max_bars
        if cursor < 0:
            cursor = 0
        details = []
        ok = True
        for step in steps:
            found = False
            while cursor <= index:
                p, d = evaluate_expression(frame, cursor, step, explain=explain)
                if explain:
                    details.extend(d)
                if p:
                    found = True
                    cursor += 1
                    break
                cursor += 1
            if not found:
                ok = False
                break
        return ok, details
    if "state_machine" in node:
        # Evaluate whether current state_id matches accepting state after transitions
        # using precomputed state_id column (builder responsibility).
        sm = node["state_machine"]
        accept = set(sm.get("accept") or [])
        try:
            sid = int(_at(_col(frame, "state_id"), index))
            passed = sid in {int(x) for x in accept}
        except Exception as exc:
            passed = False
            details = [{"error": str(exc), "fail_closed": True}] if explain else []
            return False, details
        return passed, [{"state_id": sid, "passed": passed}] if explain else []
    return _eval_leaf(frame, index, node, explain=explain)


def evaluate_strategy(frame, index, strategy, phase="entry", explain=False):
    validate_strategy(strategy)
    return evaluate_expression(frame, index, strategy[phase], explain=explain)


def extract_features(node, out=None):
    out = out if out is not None else set()
    if not isinstance(node, dict):
        return out
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        for c in node[key]:
            extract_features(c, out)
        return out
    if "not" in node:
        return extract_features(node["not"], out)
    if "event_sequence" in node:
        for s in (node["event_sequence"].get("steps") or []):
            extract_features(s, out)
        return out
    if "state_machine" in node:
        out.add("state_id")
        for tr in node["state_machine"].get("transitions") or []:
            extract_features(tr.get("when") or {}, out)
        return out
    for side in ("left", "right"):
        op = node.get(side) or {}
        if isinstance(op, dict) and "feature" in op:
            out.add(op["feature"])
    return out


def research_feature_usage(strategy):
    feats = set()
    extract_features(strategy.get("entry") or {}, feats)
    extract_features(strategy.get("exit") or {}, feats)
    return {
        "all_features": sorted(feats),
        "research_features": sorted(feats & RESEARCH_FEATURES),
        "base_features": sorted(feats & BASE_FEATURES),
        "uses_new_data": bool(feats & RESEARCH_FEATURES),
    }


def topology_fingerprint(strategy):
    """AST-like fingerprint for independence checks."""
    validated = validate_strategy(strategy)

    def skel(node):
        if not isinstance(node, dict):
            return None
        if "all" in node or "any" in node:
            key = "all" if "all" in node else "any"
            return {"op": key, "kids": [skel(c) for c in node[key]]}
        if "not" in node:
            return {"op": "not", "kid": skel(node["not"])}
        if "event_sequence" in node:
            return {"op": "event_sequence",
                    "steps": [skel(s) for s in node["event_sequence"].get("steps") or []],
                    "max_bars": node["event_sequence"].get("max_bars")}
        if "state_machine" in node:
            return {"op": "state_machine",
                    "accept": node["state_machine"].get("accept")}
        left = node.get("left") or {}
        right = node.get("right") or {}
        return {
            "left": left.get("feature"),
            "op": node.get("op"),
            "right_f": right.get("feature"),
            "has_value": "value" in right,
            "role": node.get("role"),
        }

    payload = {
        "direction": validated.get("direction"),
        "timeframe": validated.get("timeframe"),
        "instruments": validated.get("supported_instruments"),
        "entry": skel(validated.get("entry")),
        "exit": skel(validated.get("exit")),
        "data_sources": validated.get("data_sources"),
        "proxies": validated.get("proxy_declarations"),
    }
    return dsl_hash(payload)


def executable_hash(strategy):
    v = validate_strategy(strategy)
    payload = {k: v.get(k) for k in (
        "schema", "direction", "timeframe", "supported_instruments",
        "entry", "exit", "max_hold_bars", "data_sources",
    )}
    return dsl_hash(payload)
