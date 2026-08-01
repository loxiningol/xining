# -*- coding: utf-8 -*-
"""Safe, auditable strategy DSL used by the Qiyu research ecosystem.

The DSL is deliberately data-only.  It cannot import modules, call functions,
access files/network, or change capital controls.  It supports boolean trees
over a fixed indicator allow-list and produces leaf-level explanations.
"""
from __future__ import print_function

import copy
import hashlib
import json
import math


SCHEMA = "qiyu_strategy_dsl_v1"
FEATURES = {
    "open", "high", "low", "close", "ema6", "ema7", "ema8", "ema16",
    "ema17", "ema19", "ema21", "ema23", "ema32", "ema38", "ema53",
    "ema75", "ema95", "ema200", "k", "d", "j", "cci", "macd_stick",
    "atr14", "rsi14", "z20", "vol_z20", "prev_high20", "prev_low20",
    # Rolling 4H-on-5m (48×5m) liquidity box + mild volume ratio
    "prev_high48", "prev_low48", "prev_mid48", "vol_ma20_ratio",
    # Rolling 24H-on-5m (288×5m) sweep box
    "h24_high", "h24_low", "h24_mid",
    "h1_ema19", "h1_ema53", "h1_atr14", "h1_slope4",
    # Macro SFP anchors (research, no lookahead): prior-day + 4h swing24
    "pdh", "pdl", "pdc", "h4_high24", "h4_low24",
    # Session Vol Squeeze anchors (UTC, no lookahead): Asia [00:00,08:00)
    "hour_utc", "asia_high", "asia_low", "asia_mid", "asia_range",
    "asia_range_atr_ratio",
    # NY Open Liquidity Hole Fade: London box [08:00,12:30) UTC + session VWAP
    "london_high", "london_low", "london_mid", "vwap",
}
# Research / DSL allowlist — liquid OKX USDT-SWAP universe (expanded 2026-07-25).
INSTRUMENTS = {
    # majors
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "BNB-USDT-SWAP",
    "XRP-USDT-SWAP", "ADA-USDT-SWAP", "DOGE-USDT-SWAP", "LTC-USDT-SWAP",
    # liquid alts
    "LINK-USDT-SWAP", "AVAX-USDT-SWAP", "DOT-USDT-SWAP", "ATOM-USDT-SWAP",
    "NEAR-USDT-SWAP", "APT-USDT-SWAP", "SUI-USDT-SWAP", "OP-USDT-SWAP",
    "ARB-USDT-SWAP", "FIL-USDT-SWAP", "UNI-USDT-SWAP", "AAVE-USDT-SWAP",
    "BCH-USDT-SWAP", "ETC-USDT-SWAP", "INJ-USDT-SWAP", "SEI-USDT-SWAP",
    "TIA-USDT-SWAP", "TRX-USDT-SWAP", "ICP-USDT-SWAP", "RENDER-USDT-SWAP",
    "ONDO-USDT-SWAP", "JUP-USDT-SWAP", "WLD-USDT-SWAP", "POL-USDT-SWAP",
    # memes / high-beta
    "PEPE-USDT-SWAP", "WIF-USDT-SWAP", "BONK-USDT-SWAP", "FLOKI-USDT-SWAP",
    "SHIB-USDT-SWAP", "ORDI-USDT-SWAP",
    # commodities
    "XAU-USDT-SWAP", "XAG-USDT-SWAP", "NG-USDT-SWAP", "CL-USDT-SWAP",
}
OPS = {"lt", "lte", "gt", "gte", "eq", "between", "cross_above", "cross_below"}
LOGICAL = {"all", "any", "not"}
MAX_DEPTH = 6
MAX_LEAVES = 32
MAX_LOOKBACK = 240
EXECUTION_MAPPINGS = {"bar_close", "next_bar_open"}
ENTRY_POLICY_SOURCE = "qiyu_research_contract_v2"
ENTRY_POLICY_MODE = "exact_human_contract"

# ---- Phase-2 structured exits (opt-in; fail-closed on misuse) ----
# Protective production SL (0.9%) is NOT an exit_op and is never banned here.
EXIT_OPS = {
    "atr_trailing", "swing_extreme", "fixed_pct_tp", "partial_tp_atr",
    "entry_wick_buffer", "partial_tp_feature", "max_hold_only",
}
ATR_TRAIL_N_MIN = 2.5
ATR_TRAIL_N_MAX = 5.0  # allow Macro SFP 3.5–5.0× ATR_14 harvest window
ATR_TRAIL_PERIOD_DEFAULT = 14
# Scale-out lock (N×ATR from entry, NOT fixed % TP). Separate bounds from trail.
PARTIAL_TP_ATR_N_MIN = 1.5
PARTIAL_TP_ATR_N_MAX = 4.0
PARTIAL_TP_RATIO_MIN = 0.1
PARTIAL_TP_RATIO_MAX = 0.9
SWING_LOOKBACK_MIN = 5
SWING_LOOKBACK_MAX = 60
# Signal-bar wick stop + buffer (e.g. 0.08% = 0.0008). Not a fixed-% take-profit.
ENTRY_WICK_BUFFER_MIN = 0.0003
ENTRY_WICK_BUFFER_MAX = 0.003

# ---- Phase-4 dynamic volatility sizing (RESEARCH / incubator BT only) ----
# Position Size = (Equity * Risk_Pct) / (ATR_14 * Target_Multiplier)
# Default R in [1.0%, 1.5%]; DD throttle cuts R to 0.5% when current DD is
# ≥50% of peak-to-trough max DD observed so far.
#
# IMPORTANT — production mount interaction:
#   Live mount via CLI `auto_trade_human_confirm_pipeline.py --confirm` remains
#   B-grade 30% / 20x leverage / 0.9% protective SL. Dynamic R here is for
#   candidate evaluation only and must NOT silently change live risk.
#   Enabling production dynamic sizing requires a separate human-approved change.
RISK_PCT_DEFAULT = 0.012          # 1.2% mid of 1.0–1.5%
RISK_PCT_MIN = 0.010
RISK_PCT_MAX = 0.015
DD_THROTTLE_RISK_PCT = 0.005      # 0.5% when in deep DD region
DD_THROTTLE_OF_MAX_DD = 0.50      # ≥50% of peak-to-trough max DD
ATR_SIZE_PERIOD_DEFAULT = 14
ATR_TARGET_MULTIPLIER_DEFAULT = 1.0
PRODUCTION_B_GRADE_POSITION_PCT = 0.30
PRODUCTION_LEVERAGE = 20
PRODUCTION_STOP_LOSS_PCT = 0.009

# Hard ban: fixed take-profit below 2.0% price move (e.g. 0.6%/0.9%/1.0%/1.3%).
# Does NOT silently convert — validation / compile must refuse.
FIXED_TP_MIN_PCT = 0.02
FIXED_TINY_TP_ALIASES = (
    "price_take_profit_pct", "take_profit_price_ratio", "take_profit_pct",
    "fixed_tp_pct", "tp_pct", "price_tp_pct",
)


class DSLValidationError(ValueError):
    pass


def refuse_fixed_tiny_tp(pct, context="exit"):
    """Raise if pct is a banned tiny fixed take-profit. Never converts."""
    try:
        value = float(pct)
    except Exception:
        raise DSLValidationError(
            "REFUSED: %s fixed take-profit pct is not numeric" % context
        )
    if not math.isfinite(value):
        raise DSLValidationError(
            "REFUSED: %s fixed take-profit pct is not finite" % context
        )
    # Accept either fraction (0.01) or percent-points (1.0 → 1%)
    frac = value / 100.0 if value >= 0.2 else value
    if frac < FIXED_TP_MIN_PCT:
        raise DSLValidationError(
            "REFUSED: fixed tiny take-profit %.4f%% banned under 20x+friction "
            "(need pct>=%.1f%% OR atr_trailing/swing_extreme). "
            "Does not silently convert. context=%s"
            % (frac * 100.0, FIXED_TP_MIN_PCT * 100.0, context)
        )
    return frac


def scan_banned_fixed_tp_params(params, context="params"):
    """Refuse known tiny fixed-TP param keys. Opt-in scan for compiler/gates."""
    if not isinstance(params, dict):
        return
    for key in FIXED_TINY_TP_ALIASES:
        if key in params and params.get(key) is not None:
            refuse_fixed_tiny_tp(params.get(key), context="%s.%s" % (context, key))


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def dsl_hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def executable_hash(strategy):
    """Identity of executable logic, excluding names/provenance/live flags."""
    validated = validate_strategy(strategy)
    value = {key: validated.get(key) for key in (
        "schema", "direction", "timeframe", "supported_instruments",
        "entry", "exit", "max_hold_bars", "execution_mapping",
        "protective_stop_pct", "execution_leverage",
    )}
    value["execution_mapping"] = validated.get("execution_mapping") or "bar_close"
    return dsl_hash(value)


def _logic_skeleton(node, nums=None):
    """Structural skeleton of a boolean tree; collect numeric thresholds."""
    nums = nums if nums is not None else []
    if not isinstance(node, dict):
        return None
    logical = [k for k in LOGICAL if k in node]
    if logical:
        op = logical[0]
        children = node[op]
        if op == "not":
            return {"op": "not", "child": _logic_skeleton(children, nums)}
        kids = []
        for child in (children or []):
            kids.append(_logic_skeleton(child, nums))
        return {"op": op, "children": kids}
    if "exit_op" in node:
        leaf = {
            "exit_op": node.get("exit_op"),
            "role": node.get("role"),
            "n_atr": node.get("n_atr"),
            "lookback": node.get("lookback"),
            "has_pct": ("pct" in node) or ("price_pct" in node),
        }
        for key in ("n_atr", "lookback", "pct", "price_pct"):
            if key in node:
                try:
                    nums.append(float(node.get(key)))
                except Exception:
                    pass
        return leaf
    left = node.get("left") or {}
    right = node.get("right") or {}
    role = node.get("role")
    leaf = {
        "left": left.get("feature"),
        "left_quantile_of": left.get("quantile_of"),
        "left_offset": int(left.get("offset") or 0),
        "op": node.get("op"),
        "right_feature": right.get("feature"),
        "right_quantile_of": right.get("quantile_of"),
        "right_quantile_window": right.get("window"),
        "right_quantile_min_history": right.get("min_history"),
        "right_offset": int(right.get("offset") or 0) if "feature" in right else None,
        "role": role,
        "has_value": "value" in right,
    }
    if "value" in right:
        try:
            nums.append(float(right.get("value")))
        except Exception:
            pass
    if "quantile_of" in right:
        try:
            nums.append(float(right.get("q")))
        except Exception:
            pass
    return leaf


def logic_topology_hash(strategy):
    """Hash of logic shape (features/ops/roles), ignoring numeric thresholds.

    Two strategies with the same topology but only threshold tweaks (e.g.
    z20<2.2 vs z20<2.3) share this hash and are treated as near-duplicates.
    """
    validated = validate_strategy(strategy)
    nums = []
    skeleton = {
        "direction": validated.get("direction"),
        "timeframe": validated.get("timeframe"),
        "execution_mapping": validated.get("execution_mapping") or "bar_close",
        "protective_stop_pct": validated.get("protective_stop_pct"),
        "execution_leverage": validated.get("execution_leverage"),
        "supported_instruments": list(validated.get("supported_instruments") or []),
        "entry": _logic_skeleton(validated.get("entry") or {}, nums),
        "exit": _logic_skeleton(validated.get("exit") or {}, nums),
    }
    # Drop collected nums — topology only.
    return dsl_hash(skeleton), nums


def _nums_near(a, b, abs_tol=0.35, rel_tol=0.15):
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        diff = abs(float(x) - float(y))
        scale = max(abs(float(x)), abs(float(y)), 1e-9)
        if diff > abs_tol and diff / scale > rel_tol:
            return False
    return True


def is_near_duplicate_logic(a, b, abs_tol=0.35, rel_tol=0.15, hold_slack=2):
    """True when two DSLs are the same family with only small param tweaks."""
    try:
        va = validate_strategy(a)
        vb = validate_strategy(b)
    except Exception:
        return False
    if va.get("direction") != vb.get("direction"):
        return False
    if va.get("timeframe") != vb.get("timeframe"):
        return False
    if list(va.get("supported_instruments") or []) != list(
            vb.get("supported_instruments") or []):
        return False
    ha, na = logic_topology_hash(va)
    hb, nb = logic_topology_hash(vb)
    if ha != hb:
        return False
    hold_a = int(va.get("max_hold_bars") or 0)
    hold_b = int(vb.get("max_hold_bars") or 0)
    if abs(hold_a - hold_b) > int(hold_slack):
        return False
    # Exact executable match OR thresholds within tolerance.
    if executable_hash(va) == executable_hash(vb):
        return True
    return _nums_near(na, nb, abs_tol=abs_tol, rel_tol=rel_tol)


def find_near_duplicate(candidate, catalog, abs_tol=0.35, rel_tol=0.15):
    """Return first catalog strategy that is a near-duplicate of candidate."""
    for row in catalog or []:
        if not isinstance(row, dict):
            continue
        other = row.get("dsl") or row
        if not isinstance(other, dict):
            continue
        key = other.get("key") or row.get("key")
        if key and candidate.get("key") and key == candidate.get("key"):
            continue
        try:
            if is_near_duplicate_logic(candidate, other,
                                       abs_tol=abs_tol, rel_tol=rel_tol):
                return {
                    "key": key,
                    "name": other.get("name") or row.get("name"),
                    "match": "near_duplicate_logic",
                }
        except Exception:
            continue
    return None


def _number(value):
    if isinstance(value, bool):
        raise DSLValidationError("boolean is not a numeric threshold")
    try:
        value = float(value)
    except Exception:
        raise DSLValidationError("threshold must be numeric")
    if not math.isfinite(value) or abs(value) > 1000000000:
        raise DSLValidationError("threshold is not finite or exceeds safety bound")
    return value


def _offset(value):
    try:
        value = int(value or 0)
    except Exception:
        raise DSLValidationError("offset must be integer")
    if value < 0 or value > MAX_LOOKBACK:
        raise DSLValidationError("offset outside 0..%d" % MAX_LOOKBACK)
    return value


def _validate_operand(operand):
    if not isinstance(operand, dict):
        raise DSLValidationError("operand must be object")
    keys = set(operand)
    if "feature" in operand:
        if keys - {"feature", "offset", "scale"}:
            raise DSLValidationError("feature operand contains unknown fields")
        feature = str(operand.get("feature") or "")
        if feature not in FEATURES:
            raise DSLValidationError("unsupported feature: %s" % feature)
        _offset(operand.get("offset", 0))
        if "scale" in operand:
            _number(operand.get("scale"))
            if float(operand["scale"]) == 0.0:
                raise DSLValidationError("feature operand scale must be non-zero")
        return
    if "value" in operand:
        if keys != {"value"}:
            raise DSLValidationError("constant operand contains unknown fields")
        _number(operand.get("value"))
        return
    if "quantile_of" in operand:
        allowed = {"quantile_of", "q", "window", "min_history"}
        if keys - allowed:
            raise DSLValidationError("quantile operand contains unknown fields")
        feature = str(operand.get("quantile_of") or "")
        if feature not in FEATURES:
            raise DSLValidationError("unsupported quantile feature: %s" % feature)
        q = _number(operand.get("q"))
        if q <= 0.0 or q >= 1.0:
            raise DSLValidationError("quantile q must be strictly between 0 and 1")
        try:
            window_raw = operand.get("window")
            history_raw = operand.get("min_history")
            window = int(window_raw)
            min_history = int(history_raw)
            if float(window_raw) != float(window) or float(history_raw) != float(min_history):
                raise ValueError("non_integer")
        except Exception:
            raise DSLValidationError("quantile window/min_history must be integers")
        if window < 2 or window > MAX_LOOKBACK:
            raise DSLValidationError("quantile window outside 2..%d" % MAX_LOOKBACK)
        if min_history < 2 or min_history > window:
            raise DSLValidationError("quantile min_history outside 2..window")
        return
    raise DSLValidationError("operand requires feature, value, or quantile_of")


def _validate_entry_condition_policy(policy, entry, entry_count):
    """Validate the narrow provenance exception for a one-leaf human event.

    Generic/model-created DSL remains subject to the two-condition floor.  A
    one-condition event is legal only when Step A attests that it is the exact
    machine-readable event in an immutable ResearchContract v2.  The entry
    semantics hash prevents the policy object from being copied onto changed
    executable logic without re-attestation.
    """
    if not isinstance(policy, dict):
        raise DSLValidationError("entry_condition_policy must be object")
    allowed = {
        "mode", "source", "research_contract_id", "entry_semantics_hash",
        "exact_condition_count",
    }
    if set(policy) - allowed:
        raise DSLValidationError("entry_condition_policy contains unknown fields")
    if policy.get("mode") != ENTRY_POLICY_MODE:
        raise DSLValidationError("unsupported entry_condition_policy mode")
    if policy.get("source") != ENTRY_POLICY_SOURCE:
        raise DSLValidationError("single entry policy requires ResearchContract v2")
    contract_id = str(policy.get("research_contract_id") or "")
    digest = contract_id[3:] if contract_id.startswith("rc_") else ""
    if len(digest) != 24 or any(ch not in "0123456789abcdef" for ch in digest):
        raise DSLValidationError("single entry policy research_contract_id invalid")
    try:
        declared_count = int(policy.get("exact_condition_count"))
    except Exception:
        raise DSLValidationError("single entry policy condition count invalid")
    if declared_count != int(entry_count) or declared_count != 1:
        raise DSLValidationError("single entry policy must attest exactly one condition")
    expected = dsl_hash(entry)
    supplied = str(policy.get("entry_semantics_hash") or "")
    if supplied != expected:
        raise DSLValidationError("single entry policy semantics hash mismatch")


def _walk(node, depth=0, counter=None, seen_ids=None, phase=None):
    counter = counter if counter is not None else [0]
    seen_ids = seen_ids if seen_ids is not None else set()
    if depth > MAX_DEPTH or not isinstance(node, dict):
        raise DSLValidationError("expression depth/type invalid")
    logical = [key for key in LOGICAL if key in node]
    if logical:
        if len(logical) != 1 or len(node) != 1:
            raise DSLValidationError("logical node must contain exactly one operator")
        op = logical[0]
        children = node[op]
        if op == "not":
            _walk(children, depth + 1, counter, seen_ids, phase=phase)
        else:
            if not isinstance(children, list) or not children:
                raise DSLValidationError("all/any requires non-empty list")
            for child in children:
                _walk(child, depth + 1, counter, seen_ids, phase=phase)
        return
    # Phase-2 structured exit operators (opt-in leaves)
    if "exit_op" in node:
        if phase != "exit":
            raise DSLValidationError("exit_op is only valid on exit conditions")
        allowed_exit = {
            "id", "exit_op", "role", "n_atr", "atr_period", "lookback",
            "pct", "price_pct", "partial_tp_ratio", "buffer_pct",
            "feature", "op", "params",
        }
        if set(node) - allowed_exit:
            raise DSLValidationError("exit_op node contains unknown fields")
        condition_id = str(node.get("id") or "")
        if not condition_id or len(condition_id) > 80:
            raise DSLValidationError("condition id required and <=80 chars")
        if condition_id in seen_ids:
            raise DSLValidationError("condition ids must be unique")
        seen_ids.add(condition_id)
        exit_op = str(node.get("exit_op") or "")
        if exit_op not in EXIT_OPS:
            raise DSLValidationError("unsupported exit_op: %s" % exit_op)
        role = node.get("role")
        if role is not None and role not in ("take_profit", "invalidation"):
            raise DSLValidationError("exit role must be take_profit or invalidation")
        if exit_op == "atr_trailing":
            n_atr = _number(node.get("n_atr", 3.0))
            if n_atr < ATR_TRAIL_N_MIN or n_atr > ATR_TRAIL_N_MAX:
                raise DSLValidationError(
                    "atr_trailing n_atr must be in [%.1f, %.1f] (got %s)"
                    % (ATR_TRAIL_N_MIN, ATR_TRAIL_N_MAX, n_atr)
                )
            atr_period = int(node.get("atr_period") or ATR_TRAIL_PERIOD_DEFAULT)
            if atr_period < 2 or atr_period > MAX_LOOKBACK:
                raise DSLValidationError("atr_period outside 2..%d" % MAX_LOOKBACK)
        elif exit_op == "partial_tp_atr":
            # Scale-out lock at N×ATR from entry (NOT fixed %). Fail-closed.
            if role is not None and role != "take_profit":
                raise DSLValidationError(
                    "partial_tp_atr role must be take_profit (got %s)" % role
                )
            n_atr = _number(node.get("n_atr", 2.0))
            if n_atr < PARTIAL_TP_ATR_N_MIN or n_atr > PARTIAL_TP_ATR_N_MAX:
                raise DSLValidationError(
                    "partial_tp_atr n_atr must be in [%.1f, %.1f] (got %s)"
                    % (PARTIAL_TP_ATR_N_MIN, PARTIAL_TP_ATR_N_MAX, n_atr)
                )
            atr_period = int(node.get("atr_period") or ATR_TRAIL_PERIOD_DEFAULT)
            if atr_period < 2 or atr_period > MAX_LOOKBACK:
                raise DSLValidationError("atr_period outside 2..%d" % MAX_LOOKBACK)
            ratio = _number(node.get("partial_tp_ratio", 0.5))
            if ratio < PARTIAL_TP_RATIO_MIN or ratio > PARTIAL_TP_RATIO_MAX:
                raise DSLValidationError(
                    "partial_tp_ratio must be in [%.1f, %.1f] (got %s)"
                    % (PARTIAL_TP_RATIO_MIN, PARTIAL_TP_RATIO_MAX, ratio)
                )
        elif exit_op == "swing_extreme":
            lookback = int(node.get("lookback") or 20)
            if lookback < SWING_LOOKBACK_MIN or lookback > SWING_LOOKBACK_MAX:
                raise DSLValidationError(
                    "swing_extreme lookback must be in [%d, %d]"
                    % (SWING_LOOKBACK_MIN, SWING_LOOKBACK_MAX)
                )
        elif exit_op == "entry_wick_buffer":
            # Logical stop at signal-bar extreme ± buffer. Not fixed-% TP.
            if role is not None and role != "invalidation":
                raise DSLValidationError(
                    "entry_wick_buffer role must be invalidation (got %s)" % role
                )
            buf = _number(node.get("buffer_pct", 0.0008))
            if buf < ENTRY_WICK_BUFFER_MIN or buf > ENTRY_WICK_BUFFER_MAX:
                raise DSLValidationError(
                    "entry_wick_buffer buffer_pct must be in [%.4f, %.4f] (got %s)"
                    % (ENTRY_WICK_BUFFER_MIN, ENTRY_WICK_BUFFER_MAX, buf)
                )
        elif exit_op == "partial_tp_feature":
            # Scale-out when price reaches a live feature (e.g. session VWAP).
            if role is not None and role != "take_profit":
                raise DSLValidationError(
                    "partial_tp_feature role must be take_profit (got %s)" % role
                )
            feat = str(node.get("feature") or "")
            if feat not in FEATURES:
                raise DSLValidationError(
                    "partial_tp_feature feature not allowed: %s" % feat
                )
            op = str(node.get("op") or "")
            if op not in ("gte", "lte", "gt", "lt"):
                raise DSLValidationError(
                    "partial_tp_feature op must be gte|lte|gt|lt (got %s)" % op
                )
            ratio = _number(node.get("partial_tp_ratio", 0.5))
            if ratio < PARTIAL_TP_RATIO_MIN or ratio > PARTIAL_TP_RATIO_MAX:
                raise DSLValidationError(
                    "partial_tp_ratio must be in [%.1f, %.1f] (got %s)"
                    % (PARTIAL_TP_RATIO_MIN, PARTIAL_TP_RATIO_MAX, ratio)
                )
        elif exit_op == "fixed_pct_tp":
            # Hard ban tiny fixed TP — refuse-compile / refuse-validate, no convert
            pct = node.get("pct", node.get("price_pct"))
            if pct is None and isinstance(node.get("params"), dict):
                pct = (node["params"].get("pct")
                       or node["params"].get("price_pct"))
            refuse_fixed_tiny_tp(pct, context="exit_op.fixed_pct_tp")
        elif exit_op == "max_hold_only":
            if role not in (None, "invalidation"):
                raise DSLValidationError("max_hold_only role must be invalidation")
            extra = set(node) - {"id", "exit_op", "role"}
            if extra:
                raise DSLValidationError("max_hold_only takes no parameters")
        counter[0] += 1
        if counter[0] > MAX_LEAVES:
            raise DSLValidationError("too many conditions")
        return
    allowed = {"id", "left", "op", "right", "lower", "upper", "role"}
    if set(node) - allowed:
        raise DSLValidationError("condition contains unknown fields")
    condition_id = str(node.get("id") or "")
    if not condition_id or len(condition_id) > 80:
        raise DSLValidationError("condition id required and <=80 chars")
    if condition_id in seen_ids:
        raise DSLValidationError("condition ids must be unique")
    seen_ids.add(condition_id)
    op = str(node.get("op") or "")
    if op not in OPS:
        raise DSLValidationError("unsupported operator: %s" % op)
    _validate_operand(node.get("left"))
    if op == "between":
        _number(node.get("lower")); _number(node.get("upper"))
        if float(node["lower"]) > float(node["upper"]):
            raise DSLValidationError("between lower exceeds upper")
    else:
        _validate_operand(node.get("right"))
    role = node.get("role")
    if role is not None:
        if phase != "exit":
            raise DSLValidationError("role is only valid on exit conditions")
        if role not in ("take_profit", "invalidation"):
            raise DSLValidationError("exit role must be take_profit or invalidation")
    counter[0] += 1
    if counter[0] > MAX_LEAVES:
        raise DSLValidationError("too many conditions")


def validate_strategy(strategy):
    if not isinstance(strategy, dict):
        raise DSLValidationError("strategy must be object")
    allowed = {"schema", "key", "name", "direction", "timeframe",
               "supported_instruments", "entry", "exit", "max_hold_bars",
               "description", "origin", "version", "live_enabled",
               "approved_version_hash", "auto_trade_eligible",
               "execution_mapping", "entry_condition_policy",
               "protective_stop_pct", "execution_leverage"}
    if set(strategy) - allowed:
        raise DSLValidationError("strategy contains unknown top-level fields")
    if strategy.get("schema") != SCHEMA:
        raise DSLValidationError("unsupported DSL schema")
    key = str(strategy.get("key") or "")
    if not key or len(key) > 100 or not all(ch.isalnum() or ch == "_" for ch in key):
        raise DSLValidationError("invalid strategy key")
    if strategy.get("direction") not in ("long", "short"):
        raise DSLValidationError("direction must be long or short")
    if len(str(strategy.get("name") or "")) > 120:
        raise DSLValidationError("strategy name too long")
    if len(str(strategy.get("description") or "")) > 2000:
        raise DSLValidationError("strategy description too long")
    if strategy.get("timeframe") not in ("1h", "4h", "15m", "5m"):
        raise DSLValidationError("unsupported timeframe")
    instruments = strategy.get("supported_instruments") or []
    if not isinstance(instruments, list) or not instruments or len(instruments) > 12:
        raise DSLValidationError("supported_instruments must be non-empty list")
    if any(str(instrument) not in INSTRUMENTS for instrument in instruments):
        raise DSLValidationError("unsupported instrument")
    hold = int(strategy.get("max_hold_bars") or 0)
    if hold < 1 or hold > 240:
        raise DSLValidationError("max_hold_bars outside 1..240")
    entry_count = [0]; exit_count = [0]
    _walk(strategy.get("entry"), counter=entry_count, seen_ids=set(), phase="entry")
    _walk(strategy.get("exit"), counter=exit_count, seen_ids=set(), phase="exit")
    mapping = str(strategy.get("execution_mapping") or "bar_close")
    if mapping not in EXECUTION_MAPPINGS:
        raise DSLValidationError("unsupported execution_mapping: %s" % mapping)
    if strategy.get("protective_stop_pct") is not None:
        stop_pct = _number(strategy.get("protective_stop_pct"))
        if stop_pct <= 0.0 or stop_pct > 0.10:
            raise DSLValidationError("protective_stop_pct outside (0, 0.10]")
    if strategy.get("execution_leverage") is not None:
        leverage = _number(strategy.get("execution_leverage"))
        if leverage < 1.0 or leverage > 100.0 or int(leverage) != leverage:
            raise DSLValidationError("execution_leverage must be integer 1..100")
    if entry_count[0] < 2:
        _validate_entry_condition_policy(
            strategy.get("entry_condition_policy"),
            strategy.get("entry"),
            entry_count[0],
        )
    elif strategy.get("entry_condition_policy") is not None:
        raise DSLValidationError(
            "entry_condition_policy is only valid for one exact condition"
        )
    return copy.deepcopy(strategy)


def _series(frame, feature):
    if feature not in frame.columns:
        raise DSLValidationError("feature absent from frame: %s" % feature)
    return frame[feature]


def _operand(frame, index, operand, extra_offset=0):
    if "value" in operand:
        return float(operand["value"])
    if "quantile_of" in operand:
        # The threshold uses only bars strictly before the evaluated bar.  For
        # cross operators, ``extra_offset=1`` also shifts the history boundary,
        # so neither current nor future values can leak into the comparison.
        anchor = int(index) - int(extra_offset)
        window = int(operand["window"])
        min_history = int(operand["min_history"])
        start = max(0, anchor - window)
        values = []
        series = _series(frame, operand["quantile_of"])
        for position in range(start, anchor):
            try:
                value = float(series.iloc[position])
            except Exception:
                continue
            if math.isfinite(value):
                values.append(value)
        if len(values) < min_history:
            raise IndexError("insufficient prior-only quantile history")
        values.sort()
        q = float(operand["q"])
        q_index = max(0, min(len(values) - 1, int(q * (len(values) - 1))))
        return values[q_index]
    offset = _offset(operand.get("offset", 0)) + int(extra_offset)
    position = index - offset
    if position < 0:
        raise IndexError("insufficient lookback")
    value = float(_series(frame, operand["feature"]).iloc[position])
    if not math.isfinite(value):
        raise ValueError("indicator is not finite")
    if "scale" in operand:
        value *= float(operand["scale"])
    return value


def _atr_at(frame, index, period=14):
    """ATR_period from OHLC frame (Wilder-ish simple mean of TR)."""
    if index < period:
        raise IndexError("insufficient bars for ATR")
    trs = []
    for i in range(index - period + 1, index + 1):
        high = float(frame["high"].iloc[i])
        low = float(frame["low"].iloc[i])
        prev_close = float(frame["close"].iloc[i - 1]) if i > 0 else high
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs) / float(len(trs))


def clamp_risk_pct(risk_pct, lo=RISK_PCT_MIN, hi=RISK_PCT_MAX):
    """Clamp research R into configured band (default 1.0%–1.5%)."""
    try:
        r = float(risk_pct)
    except Exception:
        r = RISK_PCT_DEFAULT
    if not math.isfinite(r):
        r = RISK_PCT_DEFAULT
    return max(float(lo), min(float(hi), r))


def effective_risk_pct(
    base_risk_pct,
    equity,
    peak_equity,
    max_dd_so_far,
    dd_throttle_risk_pct=DD_THROTTLE_RISK_PCT,
    dd_throttle_of_max_dd=DD_THROTTLE_OF_MAX_DD,
):
    """Apply DD throttle: cut R to 0.5% when current DD ≥ 50% of max DD.

    max_dd_so_far is peak-to-trough max drawdown fraction observed so far.
    """
    base = clamp_risk_pct(base_risk_pct)
    try:
        eq = float(equity)
        peak = float(peak_equity)
        max_dd = float(max_dd_so_far or 0.0)
    except Exception:
        return base
    if peak <= 0 or max_dd <= 1e-15:
        return base
    current_dd = max(0.0, (peak - eq) / peak)
    if current_dd + 1e-15 >= float(dd_throttle_of_max_dd) * max_dd:
        return float(dd_throttle_risk_pct)
    return base


def atr_position_size(
    equity,
    risk_pct,
    atr,
    target_multiplier=ATR_TARGET_MULTIPLIER_DEFAULT,
    entry_price=None,
):
    """ATR-based position size (quantity units).

    Position Size = (Equity * Risk_Pct) / (ATR_14 * Target_Multiplier)

    Returns dict with quantity, notional, account_fraction (notional/equity),
    and atr inputs. account_fraction is used to scale leveraged pnl in research BT.
    """
    eq = max(0.0, float(equity))
    atr_v = float(atr)
    mult = float(target_multiplier) if target_multiplier not in (None, 0) else ATR_TARGET_MULTIPLIER_DEFAULT
    risk = float(risk_pct)
    if atr_v <= 1e-15 or eq <= 0 or risk <= 0:
        return {
            "quantity": 0.0,
            "notional": 0.0,
            "account_fraction": 0.0,
            "atr": atr_v,
            "risk_pct": risk,
            "target_multiplier": mult,
            "formula": "(Equity * Risk_Pct) / (ATR_14 * Target_Multiplier)",
        }
    qty = (eq * risk) / (atr_v * mult)
    px = float(entry_price) if entry_price not in (None, 0) else 1.0
    notional = qty * px
    # Fraction of equity notionally deployed (cap at 1.0 for research BT scaling).
    # High ATR → lower fraction (lower notional risk).
    account_fraction = min(1.0, notional / eq) if eq > 0 else 0.0
    return {
        "quantity": float(qty),
        "notional": float(notional),
        "account_fraction": float(account_fraction),
        "atr": atr_v,
        "risk_pct": risk,
        "target_multiplier": mult,
        "entry_price": px,
        "formula": "(Equity * Risk_Pct) / (ATR_14 * Target_Multiplier)",
        # Explicit note: live mount stays B/30%/20x until separately approved.
        "production_mount_unchanged": True,
        "production_b_grade_position_pct": PRODUCTION_B_GRADE_POSITION_PCT,
        "production_leverage": PRODUCTION_LEVERAGE,
        "production_stop_loss_pct": PRODUCTION_STOP_LOSS_PCT,
    }


def _swing_high_low(frame, index, lookback):
    """Local extremes over last `lookback` bars excluding current bar."""
    if index < lookback:
        raise IndexError("insufficient bars for swing")
    lo = index - lookback
    hi = index  # exclusive current
    highs = frame["high"].iloc[lo:hi]
    lows = frame["low"].iloc[lo:hi]
    try:
        swing_high = float(highs.max())
        swing_low = float(lows.min())
    except Exception:
        swing_high = float(max(highs))
        swing_low = float(min(lows))
    return swing_high, swing_low


def evaluate_exit_op(frame, index, node, position, direction, explain=False):
    """Evaluate structured exit leaf. position must include price + peaks."""
    exit_op = str(node.get("exit_op") or "")
    role = node.get("role") or "take_profit"
    entry_price = float(position["price"])
    high = float(frame["high"].iloc[index])
    low = float(frame["low"].iloc[index])
    close = float(frame["close"].iloc[index])
    passed = False
    exit_price = close
    detail = {
        "condition_id": node.get("id"),
        "exit_op": exit_op,
        "role": role,
        "passed": False,
    }
    try:
        if exit_op == "atr_trailing":
            n_atr = float(node.get("n_atr") or 3.0)
            atr_period = int(node.get("atr_period") or ATR_TRAIL_PERIOD_DEFAULT)
            atr = _atr_at(frame, index, atr_period)
            peak_high = float(position.get("peak_high") or max(entry_price, high))
            peak_low = float(position.get("peak_low") or min(entry_price, low))
            if direction == "long":
                trail = peak_high - n_atr * atr
                passed = low <= trail
                exit_price = trail if passed else close
            else:
                trail = peak_low + n_atr * atr
                passed = high >= trail
                exit_price = trail if passed else close
            detail.update({
                "atr": atr, "n_atr": n_atr, "trail": trail,
                "peak_high": peak_high, "peak_low": peak_low,
            })
        elif exit_op == "partial_tp_atr":
            # Lock partial_tp_ratio at N×ATR from entry (scale-out). Not fixed %.
            if position.get("partial_taken"):
                passed = False
                exit_price = close
                detail.update({"skipped": "already_partial_taken"})
            else:
                n_atr = float(node.get("n_atr") or 2.0)
                atr_period = int(node.get("atr_period") or ATR_TRAIL_PERIOD_DEFAULT)
                ratio = float(node.get("partial_tp_ratio") or 0.5)
                atr = _atr_at(frame, index, atr_period)
                if direction == "long":
                    target = entry_price + n_atr * atr
                    passed = high >= target
                    exit_price = target if passed else close
                else:
                    target = entry_price - n_atr * atr
                    passed = low <= target
                    exit_price = target if passed else close
                detail.update({
                    "atr": atr, "n_atr": n_atr, "target": target,
                    "partial_tp_ratio": ratio, "partial_exit": True,
                })
        elif exit_op == "swing_extreme":
            lookback = int(node.get("lookback") or 20)
            swing_high, swing_low = _swing_high_low(frame, index, lookback)
            if role == "invalidation":
                if direction == "long":
                    passed = low <= swing_low
                    exit_price = swing_low if passed else close
                else:
                    passed = high >= swing_high
                    exit_price = swing_high if passed else close
            else:
                # take_profit / dynamic exit line at opposing extreme
                if direction == "long":
                    passed = high >= swing_high
                    exit_price = swing_high if passed else close
                else:
                    passed = low <= swing_low
                    exit_price = swing_low if passed else close
            detail.update({
                "lookback": lookback,
                "swing_high": swing_high,
                "swing_low": swing_low,
            })
        elif exit_op == "entry_wick_buffer":
            buf = float(node.get("buffer_pct") or 0.0008)
            entry_low = float(position.get("entry_bar_low")
                              or position.get("peak_low")
                              or entry_price)
            entry_high = float(position.get("entry_bar_high")
                               or position.get("peak_high")
                               or entry_price)
            if direction == "long":
                stop_px = entry_low * (1.0 - buf)
                passed = low <= stop_px
                exit_price = stop_px if passed else close
            else:
                stop_px = entry_high * (1.0 + buf)
                passed = high >= stop_px
                exit_price = stop_px if passed else close
            detail.update({
                "buffer_pct": buf,
                "entry_bar_low": entry_low,
                "entry_bar_high": entry_high,
                "stop": stop_px,
            })
        elif exit_op == "partial_tp_feature":
            if position.get("partial_taken"):
                passed = False
                exit_price = close
                detail.update({"skipped": "already_partial_taken"})
            else:
                feat = str(node.get("feature") or "")
                op = str(node.get("op") or "gte")
                ratio = float(node.get("partial_tp_ratio") or 0.5)
                target = float(_series(frame, feat).iloc[index])
                if op == "gte":
                    passed = close >= target or high >= target
                elif op == "gt":
                    passed = close > target or high > target
                elif op == "lte":
                    passed = close <= target or low <= target
                elif op == "lt":
                    passed = close < target or low < target
                else:
                    passed = False
                exit_price = target if passed else close
                detail.update({
                    "feature": feat, "op": op, "target": target,
                    "partial_tp_ratio": ratio, "partial_exit": True,
                })
        elif exit_op == "fixed_pct_tp":
            pct = refuse_fixed_tiny_tp(
                node.get("pct", node.get("price_pct")),
                context="runtime.fixed_pct_tp",
            )
            if direction == "long":
                target = entry_price * (1.0 + pct)
                passed = high >= target
                exit_price = target if passed else close
            else:
                target = entry_price * (1.0 - pct)
                passed = low <= target
                exit_price = target if passed else close
            detail.update({"pct": pct, "target": target})
        elif exit_op == "max_hold_only":
            # The holding boundary is evaluated centrally after all exit
            # leaves.  This leaf is an identity marker and never fires early.
            passed = False
            exit_price = close
            detail.update({"delegated_to": "holding_window_complete"})
        else:
            raise DSLValidationError("unsupported exit_op at runtime: %s" % exit_op)
    except DSLValidationError:
        raise
    except Exception as exc:
        passed = False
        detail["error"] = str(exc)
    detail["passed"] = bool(passed)
    detail["exit_price"] = float(exit_price)
    return bool(passed), [detail] if explain else [], float(exit_price)


def classify_exit_details(exit_details, position=None):
    """Split passed exit leaves into partial vs full. Fail-closed on unknown."""
    partial_rows = []
    full_rows = []
    for row in (exit_details or []):
        if not row.get("passed"):
            continue
        op = row.get("exit_op")
        if op in ("partial_tp_atr", "partial_tp_feature"):
            if position is not None and position.get("partial_taken"):
                continue
            partial_rows.append(row)
        else:
            # atr_trailing / swing / wick / fixed / legacy condition leaves → full exit
            full_rows.append(row)
    return partial_rows, full_rows


def evaluate_expression(frame, index, node, explain=False,
                        position=None, direction=None):
    if "all" in node:
        children = [evaluate_expression(frame, index, child, explain=explain,
                                        position=position, direction=direction)
                    for child in node["all"]]
        passed = all(item[0] for item in children)
        details = sum((item[1] for item in children), [])
        return passed, details
    if "any" in node:
        children = [evaluate_expression(frame, index, child, explain=explain,
                                        position=position, direction=direction)
                    for child in node["any"]]
        passed = any(item[0] for item in children)
        details = sum((item[1] for item in children), [])
        return passed, details
    if "not" in node:
        passed, details = evaluate_expression(
            frame, index, node["not"], explain=explain,
            position=position, direction=direction,
        )
        if explain:
            details = [dict(item, passed=not bool(item.get("passed")), negated=True)
                       for item in details]
        return not passed, details
    if "exit_op" in node:
        if position is None or direction is None:
            # Fail-closed: structured exits require position context
            detail = {"condition_id": node.get("id"), "exit_op": node.get("exit_op"),
                      "passed": False, "error": "exit_op_requires_position_context"}
            return False, [detail] if explain else []
        passed, details, _price = evaluate_exit_op(
            frame, index, node, position, direction, explain=explain,
        )
        return passed, details
    op = node["op"]
    try:
        left = _operand(frame, index, node["left"])
        if op == "between":
            right = [float(node["lower"]), float(node["upper"])]
            passed = right[0] <= left <= right[1]
        else:
            right = _operand(frame, index, node["right"])
            if op == "lt": passed = left < right
            elif op == "lte": passed = left <= right
            elif op == "gt": passed = left > right
            elif op == "gte": passed = left >= right
            elif op == "eq": passed = abs(left-right) <= 1e-12
            elif op == "cross_above":
                previous_left = _operand(frame, index, node["left"], 1)
                previous_right = _operand(frame, index, node["right"], 1)
                passed = previous_left <= previous_right and left > right
            elif op == "cross_below":
                previous_left = _operand(frame, index, node["left"], 1)
                previous_right = _operand(frame, index, node["right"], 1)
                passed = previous_left >= previous_right and left < right
            else:
                passed = False
        detail = {"condition_id": node["id"], "passed": bool(passed),
                  "left": left, "operator": op, "right": right}
        if node.get("role"):
            detail["role"] = node.get("role")
    except Exception as exc:
        passed = False
        detail = {"condition_id": node.get("id"), "passed": False,
                  "operator": op, "error": str(exc)}
    return bool(passed), [detail] if explain else []


def evaluate_strategy(frame, index, strategy, phase="entry", explain=False,
                      position=None):
    validate_strategy(strategy)
    if phase not in ("entry", "exit"):
        raise DSLValidationError("phase must be entry or exit")
    return evaluate_expression(
        frame, index, strategy[phase], explain=explain,
        position=position,
        direction=strategy.get("direction") if phase == "exit" else None,
    )


def holding_window_complete(entry_index, current_index, max_hold_bars,
                            execution_mapping="bar_close"):
    """Return whether the executable holding horizon has completed.

    A next-open fill owns the entry bar from its open through its close, so an
    admitted horizon of N bars exits at ``entry_index + N - 1``.  A legacy
    same-close fill has no exposure to its already-completed entry bar and
    therefore retains the historical ``entry_index + N`` boundary.
    """
    entry_index = int(entry_index)
    current_index = int(current_index)
    hold = max(1, int(max_hold_bars))
    mapping = str(execution_mapping or "bar_close")
    if mapping == "next_bar_open":
        return current_index - entry_index + 1 >= hold
    return current_index - entry_index >= hold


def _leaf_paths(node, path=()):
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        out = []
        for index, child in enumerate(node[key]):
            out.extend(_leaf_paths(child, path + (key, index)))
        return out
    if "not" in node:
        return _leaf_paths(node["not"], path + ("not",))
    return [(path, node)]


def _at(root, path):
    current = root
    for part in path:
        current = current[part]
    return current


def mutate_strategy(strategy, threshold_step=1.0, additions=None, limit=24):
    """Return bounded, validated add/remove/threshold mutations."""
    base = validate_strategy(strategy)
    variants = []
    entry_leaves = _leaf_paths(base["entry"])
    for path, leaf in entry_leaves:
        if len(variants) >= limit: break
        for field in ("right",):
            operand = leaf.get(field)
            if not isinstance(operand, dict) or "value" not in operand:
                continue
            feature = str((leaf.get("left") or {}).get("feature") or "")
            # Numeric features do not share a scale.  In particular,
            # h1_slope4 is a decimal return where 0.001 means 0.1%; applying
            # the legacy +/-1 step makes the condition unreachable.  Keep
            # mutations local to the observed indicator's natural scale.
            feature_steps = {
                "h1_slope4": 0.0005,
                "z20": 0.1,
                "vol_z20": 0.1,
                "vol_ma20_ratio": 0.05,
                "cci": 5.0,
                "macd_stick": 5.0,
                "atr14": 1.0,
                "rsi14": 1.0,
                "k": 1.0,
                "d": 1.0,
                "j": 1.0,
            }
            step = float(feature_steps.get(feature, threshold_step))
            for direction in (-1, 1):
                changed = copy.deepcopy(base)
                target = _at(changed["entry"], path)
                target[field]["value"] = round(float(operand["value"]) + direction*step, 10)
                changed["key"] = base["key"] + "_m" + dsl_hash(changed)[:10]
                changed["origin"] = {"mutation": "threshold", "condition_id": leaf["id"]}
                try:
                    variants.append(validate_strategy(changed))
                except DSLValidationError:
                    pass
        if leaf.get("op") == "between":
            for field in ("lower", "upper"):
                for direction in (-1, 1):
                    changed = copy.deepcopy(base)
                    target = _at(changed["entry"], path)
                    target[field] = round(float(leaf[field]) + direction*threshold_step, 10)
                    changed["key"] = base["key"] + "_m" + dsl_hash(changed)[:10]
                    changed["origin"] = {"mutation": "threshold",
                                         "condition_id": leaf["id"], "field": field}
                    try:
                        variants.append(validate_strategy(changed))
                    except DSLValidationError:
                        pass
    if isinstance(base.get("entry"), dict) and "all" in base["entry"] and len(base["entry"]["all"]) > 2:
        for index, child in enumerate(list(base["entry"]["all"])):
            if len(variants) >= limit: break
            changed = copy.deepcopy(base)
            removed = changed["entry"]["all"].pop(index)
            changed["key"] = base["key"] + "_m" + dsl_hash(changed)[:10]
            changed["origin"] = {"mutation": "remove_condition",
                                 "condition_id": removed.get("id") if isinstance(removed, dict) else None}
            try:
                variants.append(validate_strategy(changed))
            except DSLValidationError:
                pass
    # Exit logic is part of strategy quality, not a fixed capital-risk
    # parameter.  Explore bounded threshold changes and removals so an exit
    # that is already true at entry cannot silently dominate every trade.
    exit_leaves = _leaf_paths(base["exit"])
    for path, leaf in exit_leaves:
        if len(variants) >= limit: break
        operand = leaf.get("right")
        if not isinstance(operand, dict) or "value" not in operand:
            continue
        feature = str((leaf.get("left") or {}).get("feature") or "")
        step = float({
            "h1_slope4": 0.0005, "z20": 0.1, "vol_z20": 0.1, "cci": 5.0,
            "macd_stick": 5.0, "atr14": 1.0, "rsi14": 1.0,
            "k": 1.0, "d": 1.0, "j": 1.0,
        }.get(feature, threshold_step))
        for direction in (-1, 1):
            changed = copy.deepcopy(base)
            target = _at(changed["exit"], path)
            target["right"]["value"] = round(
                float(operand["value"]) + direction * step, 10)
            changed["key"] = base["key"] + "_m" + dsl_hash(changed)[:10]
            changed["origin"] = {"mutation": "exit_threshold",
                                 "condition_id": leaf["id"]}
            try:
                variants.append(validate_strategy(changed))
            except DSLValidationError:
                pass
    exit_root = base.get("exit") or {}
    exit_key = "all" if "all" in exit_root else ("any" if "any" in exit_root else None)
    if exit_key and len(exit_root[exit_key]) > 1:
        for index, child in enumerate(list(exit_root[exit_key])):
            if len(variants) >= limit: break
            changed = copy.deepcopy(base)
            removed = changed["exit"][exit_key].pop(index)
            changed["key"] = base["key"] + "_m" + dsl_hash(changed)[:10]
            changed["origin"] = {"mutation": "remove_exit_condition",
                                 "condition_id": removed.get("id") if isinstance(removed, dict) else None}
            try:
                variants.append(validate_strategy(changed))
            except DSLValidationError:
                pass
        # Also test each exit independently.  This is bounded and auditable,
        # and is essential when one sibling exit conflicts with the entry.
        for child in list(exit_root[exit_key]):
            if len(variants) >= limit: break
            changed = copy.deepcopy(base)
            changed["exit"] = copy.deepcopy(child)
            changed["key"] = base["key"] + "_m" + dsl_hash(changed)[:10]
            changed["origin"] = {"mutation": "isolate_exit_condition",
                                 "condition_id": child.get("id") if isinstance(child, dict) else None}
            try:
                variants.append(validate_strategy(changed))
            except DSLValidationError:
                pass
    for hold_delta in (-4, 4):
        if len(variants) >= limit: break
        changed = copy.deepcopy(base)
        changed["max_hold_bars"] = max(1, int(base["max_hold_bars"]) + hold_delta)
        changed["key"] = base["key"] + "_m" + dsl_hash(changed)[:10]
        changed["origin"] = {"mutation": "max_hold_bars",
                             "delta": hold_delta}
        try:
            variants.append(validate_strategy(changed))
        except DSLValidationError:
            pass
    for addition in additions or []:
        if len(variants) >= limit: break
        if "all" not in base["entry"]:
            break
        changed = copy.deepcopy(base)
        changed["entry"]["all"].append(copy.deepcopy(addition))
        changed["key"] = base["key"] + "_m" + dsl_hash(changed)[:10]
        changed["origin"] = {"mutation": "add_condition",
                             "condition_id": addition.get("id") if isinstance(addition, dict) else None}
        try:
            variants.append(validate_strategy(changed))
        except DSLValidationError:
            pass
    if len(variants) < limit and ("all" in base["entry"] or "any" in base["entry"]):
        changed = copy.deepcopy(base)
        old = "all" if "all" in changed["entry"] else "any"
        new = "any" if old == "all" else "all"
        changed["entry"] = {new: changed["entry"][old]}
        changed["key"] = base["key"] + "_m" + dsl_hash(changed)[:10]
        changed["origin"] = {"mutation": "condition_combination",
                             "from": old, "to": new}
        try:
            variants.append(validate_strategy(changed))
        except DSLValidationError:
            pass
    seen = set(); out = []
    for variant in variants:
        digest = dsl_hash(variant)
        if digest not in seen:
            seen.add(digest); out.append(variant)
    return out[:limit]


def backtest_dsl(frame, strategy, leverage=20, stop_loss_pct=0.009,
                 fee_rate_per_side=0.0005, slippage_rate_per_side=0.0002,
                 half_spread_rate_per_side=0.0, impact_rate_per_side=0.0,
                 latency_rate_per_side=0.0, funding_rate_per_8h=0.0,
                 friction_scenario="legacy",
                 dynamic_risk_sizing=False,
                 risk_pct=RISK_PCT_DEFAULT,
                 atr_target_multiplier=ATR_TARGET_MULTIPLIER_DEFAULT,
                 atr_size_period=ATR_SIZE_PERIOD_DEFAULT,
                 dd_throttle_risk_pct=DD_THROTTLE_RISK_PCT,
                 dd_throttle_of_max_dd=DD_THROTTLE_OF_MAX_DD,
                 initial_equity=1.0):
    """Backtest DSL strategy.

    Protective stop_loss_pct default remains 0.9%.

    dynamic_risk_sizing (Phase-4 research only):
      Size = (Equity * Risk_Pct) / (ATR_14 * Target_Multiplier), with DD
      throttle to 0.5% R when current DD ≥ 50% of peak-to-trough max DD.
      Scales account capital impact by account_fraction — does NOT change
      production B-grade 30%/20x mount path (CLI --confirm).
    """
    strategy = validate_strategy(strategy)
    if strategy.get("execution_leverage") is not None and abs(
        float(strategy.get("execution_leverage")) - float(leverage)
    ) > 1e-12:
        raise DSLValidationError("backtest leverage differs from strategy identity")
    if strategy.get("protective_stop_pct") is not None and abs(
        float(strategy.get("protective_stop_pct")) - float(stop_loss_pct)
    ) > 1e-12:
        raise DSLValidationError("backtest stop differs from strategy identity")
    direction = strategy["direction"]
    execution_rate_per_side = (fee_rate_per_side + slippage_rate_per_side +
                               half_spread_rate_per_side +
                               impact_rate_per_side + latency_rate_per_side)
    round_cost = 2.0 * execution_rate_per_side * leverage
    hours_per_bar = {"5m": 1.0/12.0, "15m": 0.25, "1h": 1.0}.get(
        strategy.get("timeframe"), 1.0)
    execution_mapping = strategy.get("execution_mapping") or "bar_close"
    trades = []; position = None; pending_signal = None
    capital = float(initial_equity) if initial_equity else 1.0
    peak_equity = capital
    max_dd_so_far = 0.0
    base_risk = clamp_risk_pct(risk_pct)
    start = max(250, MAX_LOOKBACK + 2)

    def position_from_signal(signal_index, entry_index, price, details):
        size_meta = {
            "account_fraction": 1.0,
            "risk_pct_used": None,
            "dynamic_risk_sizing": bool(dynamic_risk_sizing),
        }
        if dynamic_risk_sizing:
            try:
                # Sizing uses only information available when the signal bar
                # closed.  For next-open execution this deliberately excludes
                # the entry bar's later high/low/close.
                atr = _atr_at(frame, signal_index, int(atr_size_period or 14))
            except Exception:
                atr = max(float(price) * 0.01, 1e-9)
            r_eff = effective_risk_pct(
                base_risk, capital, peak_equity, max_dd_so_far,
                dd_throttle_risk_pct=dd_throttle_risk_pct,
                dd_throttle_of_max_dd=dd_throttle_of_max_dd,
            )
            size_meta = atr_position_size(
                capital, r_eff, atr,
                target_multiplier=atr_target_multiplier,
                entry_price=price,
            )
            size_meta["risk_pct_used"] = r_eff
            size_meta["dynamic_risk_sizing"] = True
            size_meta["dd_throttled"] = bool(
                abs(float(r_eff) - float(dd_throttle_risk_pct)) < 1e-12
            )
        return {
            "index": int(entry_index),
            "signal_index": int(signal_index),
            "price": float(price),
            "conditions": details,
            "peak_high": float(price),
            "peak_low": float(price),
            # Frozen triggering-bar extremes, distinct from the actual fill
            # bar when execution_mapping=next_bar_open.
            "entry_bar_high": float(frame["high"].iloc[signal_index]),
            "entry_bar_low": float(frame["low"].iloc[signal_index]),
            "mae_price_pct": 0.0,
            "size_meta": size_meta,
            "execution_mapping": execution_mapping,
        }

    for index in range(start, len(frame)):
        if position is None:
            if pending_signal is not None:
                # The prior bar's close-confirmed signal is filled at this
                # bar's open.  No expression is evaluated against this bar
                # before the fill.
                position = position_from_signal(
                    pending_signal["signal_index"], index,
                    float(frame["open"].iloc[index]),
                    pending_signal["conditions"],
                )
                pending_signal = None
                # Continue below so a stop/exit occurring after this bar's
                # open is included; skipping the fill bar would be optimistic.
            else:
                # The complete definition was validated once above.
                entered, details = evaluate_expression(
                    frame, index, strategy["entry"], explain=True
                )
                if entered and execution_mapping == "next_bar_open":
                    pending_signal = {
                        "signal_index": int(index),
                        "conditions": details,
                    }
                    continue
                if entered:
                    position = position_from_signal(
                        index, index, float(frame["close"].iloc[index]), details,
                    )
                # A close fill cannot be exposed to the signal bar's already
                # completed intrabar range.
                continue
        entry_price = position["price"]
        low = float(frame["low"].iloc[index]); high = float(frame["high"].iloc[index])
        # Track MFE/MAE peaks for trailing exits + fitness MAE demotion
        position["peak_high"] = max(float(position.get("peak_high") or entry_price), high)
        position["peak_low"] = min(float(position.get("peak_low") or entry_price), low)
        if direction == "long":
            adverse = max(0.0, (entry_price - low) / entry_price)
        else:
            adverse = max(0.0, (high - entry_price) / entry_price)
        position["mae_price_pct"] = max(float(position.get("mae_price_pct") or 0.0), adverse)
        # Protective SL chain — production 0.9% default; NEVER abolished by Phase-2/4
        stop = entry_price * (1-stop_loss_pct if direction == "long" else 1+stop_loss_pct)
        stopped = low <= stop if direction == "long" else high >= stop
        exited, exit_details = evaluate_expression(
            frame, index, strategy["exit"], explain=True,
            position=position, direction=direction,
        )
        timed = holding_window_complete(
            position["index"], index, strategy["max_hold_bars"],
            execution_mapping=execution_mapping,
        )
        partial_rows, full_rows = classify_exit_details(exit_details, position)
        do_full = bool(stopped or timed or full_rows)
        do_partial = bool(
            (not do_full) and partial_rows and not position.get("partial_taken")
        )
        if not do_full and not do_partial:
            continue
        # Prefer full-exit leaf price; for partial use partial leaf price
        structured_px = None
        prefer_rows = full_rows if do_full else partial_rows
        for row in prefer_rows:
            if row.get("exit_price") is not None and row.get("exit_op"):
                structured_px = float(row["exit_price"])
                break
        if do_full and not prefer_rows:
            for row in (exit_details or []):
                if row.get("passed") and row.get("exit_price") is not None and row.get("exit_op"):
                    if row.get("exit_op") == "partial_tp_atr":
                        continue
                    structured_px = float(row["exit_price"])
                    break
        exit_price = stop if stopped else (
            structured_px if structured_px is not None
            else float(frame["close"].iloc[index])
        )
        raw = ((exit_price-entry_price)/entry_price if direction == "long"
               else (entry_price-exit_price)/entry_price)
        holding_hours = max(0, index-position["index"])*hours_per_bar
        funding_cost = (holding_hours/8.0)*funding_rate_per_8h*leverage
        net_full = raw*leverage-round_cost-funding_cost
        size_meta = position.get("size_meta") or {"account_fraction": 1.0}
        base_frac = float(size_meta.get("account_fraction") or 1.0)
        if not dynamic_risk_sizing:
            base_frac = 1.0
        remaining = float(position.get("remaining_frac", 1.0))
        if do_partial:
            ratio = float(partial_rows[0].get("partial_tp_ratio") or 0.5)
            ratio = max(PARTIAL_TP_RATIO_MIN, min(PARTIAL_TP_RATIO_MAX, ratio))
            close_frac = base_frac * remaining * ratio
            remain_after = remaining * (1.0 - ratio)
            net = net_full * close_frac
            capital *= max(0.0, 1.0+net)
            if capital > peak_equity:
                peak_equity = capital
            if peak_equity > 0:
                dd = (peak_equity - capital) / peak_equity
                if dd > max_dd_so_far:
                    max_dd_so_far = dd
            trades.append({
                "signal_index": position["signal_index"],
                "entry_index": position["index"], "exit_index": index,
                "signal_time": str(frame.index[position["signal_index"]]),
                "entry_time": str(frame.index[position["index"]]),
                "exit_time": str(frame.index[index]), "pnl_ratio": net,
                "entry_price": float(entry_price),
                "exit_price": float(exit_price),
                "pnl_ratio_full_size": net_full,
                "transaction_cost_ratio": (round_cost+funding_cost) * close_frac,
                "friction_scenario": friction_scenario,
                "profit": net > 0, "stop_loss": False,
                "exit_type": "ATR分批止盈",
                "leverage": int(leverage),
                "mae_price_pct": float(position.get("mae_price_pct") or 0.0),
                "account_fraction": close_frac,
                "partial_tp_ratio": ratio,
                "partial_exit": True,
                "execution_mapping": execution_mapping,
                "sizing": size_meta,
                "entry_conditions": position["conditions"],
                "exit_conditions": exit_details,
            })
            position["partial_taken"] = True
            position["remaining_frac"] = remain_after
            continue
        # Full exit (remaining fraction only if scale-out already hit)
        frac = base_frac * remaining
        net = net_full * frac
        capital *= max(0.0, 1.0+net)
        if capital > peak_equity:
            peak_equity = capital
        if peak_equity > 0:
            dd = (peak_equity - capital) / peak_equity
            if dd > max_dd_so_far:
                max_dd_so_far = dd
        passed_roles = set(
            row.get("role") for row in (exit_details or [])
            if row.get("passed") and row.get("role")
        )
        passed_ops = set(
            row.get("exit_op") for row in (exit_details or [])
            if row.get("passed") and row.get("exit_op")
        )
        if stopped:
            exit_type = "止损"
        elif timed:
            exit_type = "定时强制平仓"
        elif "atr_trailing" in passed_ops:
            exit_type = "ATR动态追踪退出"
        elif "swing_extreme" in passed_ops:
            exit_type = "Swing极值退出"
        elif "invalidation" in passed_roles:
            exit_type = "策略失效退出"
        elif "take_profit" in passed_roles:
            exit_type = "策略止盈"
        else:
            # Legacy/experimental DSLs without an explicit semantic role
            # are deliberately not reported as take-profit events.
            exit_type = "策略规则退出"
        trades.append({"signal_index": position["signal_index"],
                       "entry_index": position["index"], "exit_index": index,
                       "signal_time": str(frame.index[position["signal_index"]]),
                       "entry_time": str(frame.index[position["index"]]),
                       "exit_time": str(frame.index[index]), "pnl_ratio": net,
                       "entry_price": float(entry_price),
                       "exit_price": float(exit_price),
                       "pnl_ratio_full_size": net_full,
                       "transaction_cost_ratio": (round_cost+funding_cost) * frac,
                       "friction_scenario": friction_scenario,
                       "profit": net > 0, "stop_loss": stopped,
                       "exit_type": exit_type,
                       "leverage": int(leverage),
                       "mae_price_pct": float(position.get("mae_price_pct") or 0.0),
                       "account_fraction": frac,
                       "execution_mapping": execution_mapping,
                       "sizing": size_meta,
                       "entry_conditions": position["conditions"],
                       "exit_conditions": exit_details})
        position = None
    wins = sum(1 for row in trades if row["profit"])
    return {"strategy_key": strategy["key"], "total_trades": len(trades),
            "win_rate_percent": wins/float(len(trades))*100.0 if trades else 0.0,
            "total_return_percent": (capital-float(initial_equity or 1.0))*100.0
            if initial_equity else (capital-1.0)*100.0,
            "trades": trades,
            "dsl_hash": dsl_hash(strategy), "dsl_schema": SCHEMA,
            "execution_mapping": execution_mapping,
            "stop_loss_pct": float(stop_loss_pct),
            "structured_exits_enabled": True,
            "dynamic_risk_sizing": bool(dynamic_risk_sizing),
            "risk_pct": float(base_risk) if dynamic_risk_sizing else None,
            "max_drawdown_observed": float(max_dd_so_far),
            # Live mount sizing remains human-confirm B-grade until separately approved.
            "production_mount_sizing": {
                "grade": "B",
                "position_pct": PRODUCTION_B_GRADE_POSITION_PCT,
                "leverage": PRODUCTION_LEVERAGE,
                "stop_loss_pct": PRODUCTION_STOP_LOSS_PCT,
                "dynamic_r_applies_to_live": False,
            }}
