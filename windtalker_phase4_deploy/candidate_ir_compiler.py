# -*- coding: utf-8 -*-
"""WINDTALKER PHASE 4 — Candidate IR compiler (candidate-only, fail-closed).

Isolated from production legacy_spec_to_code / OHLC family templates.
Bridge candidates ONLY. Never silent OHLC fallback.
"""
from __future__ import print_function

import copy
import hashlib
import json
import math
from datetime import datetime

COMPILER_VERSION = "windtalker_phase4_candidate_ir_compiler_v1_phase4_risk"
IR_SCHEMA_VERSION = "windtalker_candidate_ir_v1"
FORBIDDEN_PROXY_FEATURES = {
    "rsi", "ema", "sma", "macd", "cci", "vwap", "stoch", "bbands",
}
FORBIDDEN_TEMPLATE_FEATURES = {"prev_high20", "prev_low20"}  # alone as OI/taker substitute
RESEARCH_CORE = {
    "oi", "oi_z20", "oi_delta_pct", "oi_crowding",
    "taker_buy", "taker_sell", "taker_imbalance", "taker_imbalance_z20", "flow_imbalance",
    "lead_ret1", "lead_ret3", "lag_ret1", "lead_lag_corr20", "cross_sync_score",
    "funding_rate", "funding_z20", "basis_bps", "basis_z20",
}
# Phase-2: ban fixed tiny take-profit (NOT protective 0.9% SL)
FIXED_TP_MIN_PCT = 0.02
FIXED_TINY_TP_KEYS = (
    "price_take_profit_pct", "take_profit_price_ratio", "take_profit_pct",
    "fixed_tp_pct", "tp_pct", "price_tp_pct", "pct", "price_pct",
)
STRUCTURED_EXIT_OPS = {"atr_trailing", "swing_extreme", "fixed_pct_tp"}


class CompilerError(ValueError):
    pass


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(value):
    if isinstance(value, (dict, list)):
        value = canonical_json(value)
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def candidate_ir_schema():
    """Authoritative Candidate IR schema document."""
    return {
        "schema": "WINDTALKER_PHASE4_CANDIDATE_IR_SCHEMA",
        "candidate_ir_version": IR_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "generated_at": _now(),
        "pipeline_position": "research_DSL_AST → Candidate_IR → formal_implementation",
        "ban_freeform_codex": True,
        "required_sections": [
            "identity", "data_deps", "state_machine", "event_order",
            "entry", "exit", "causal_structure", "compile_bans",
            "research_risk_sizing",
        ],
        "identity": {
            "fields": [
                "candidate_id", "source_probe_id", "source_research_spec_hash",
                "source_research_ast_hash", "candidate_ir_version", "created_at",
                "promotion_status",
            ]
        },
        "research_risk_sizing": {
            "fields": [
                "enabled", "formula", "risk_pct_default", "risk_pct_min", "risk_pct_max",
                "atr_period", "target_multiplier", "dd_throttle_risk_pct",
                "dd_throttle_of_max_dd", "production_mount_unchanged",
            ],
            "formula": "(Equity * Risk_Pct) / (ATR_14 * Target_Multiplier)",
            "note": (
                "Research/incubator BT only. Production mount via CLI --confirm "
                "remains B-grade 30% / 20x / 0.9% SL until separately approved."
            ),
            "production_b_grade_position_pct": 0.30,
            "production_leverage": 20,
            "production_stop_loss_pct": 0.009,
        },
        "data_deps": {
            "fields": [
                "primary_symbol", "reference_symbol", "timeframes",
                "oi", "funding_basis", "taker_flow", "cross_asset",
                "feature_freshness", "missing_data_policy", "fail_closed_rule",
                "proxy_declarations",
            ],
            "missing_data_policy": "NaN → fail-closed (no signal)",
            "fail_closed_rule": "unsupported or missing core feature → compile FAIL or runtime no-entry",
        },
        "state_machine": {
            "fields": [
                "states", "transitions", "transition_conditions", "timeouts",
                "reset_rules", "invalidation", "cooldown",
            ],
            "required": True,
        },
        "event_order": {
            "fields": [
                "event_A", "event_B", "maximum_gap", "event_C_veto",
                "confirmation", "entry_eligibility",
            ],
            "ban_unordered_AND": True,
            "note": "Must compile to ordered event_sequence, never flat AND of same-bar conditions",
        },
        "entry": {
            "fields": [
                "regime", "setup", "trigger", "confirmation", "veto",
                "final_entry", "side", "timestamp_semantics",
            ]
        },
        "exit": {
            "fields": [
                "production_sl_0_9pct", "mechanism_invalidation", "time_stop",
                "dynamic_exit", "strategy_close", "tp", "trailing",
                "cross_asset_invalidation",
            ],
            "production_sl_adapter": 0.009,
        },
        "causal_structure": {
            "fields": [
                "core_causal_variables", "supporting_variables", "non_causal_filters",
                "forbidden_proxies", "expected_ablation_effect",
                "expected_event_order_destruction_effect",
            ]
        },
        "compile_bans": [
            "no_legacy_family_template_downgrade",
            "no_auto_add_RSI_EMA_CCI_VWAP",
            "no_single_asset_substitute_for_cross_asset",
            "no_OHLC_proxy_for_real_OI_or_taker",
            "no_delete_state_machine",
            "no_event_sequence_to_samebar_AND",
            "no_modify_exit_logic",
            "no_silent_unsupported_drop",
            "no_zero_fill_missing_data",
            "no_fixed_tiny_take_profit_lt_2pct",
            "no_silent_convert_fixed_tp_to_atr",
        ],
        "formal_implementation_modes": {
            "legacy_spec_to_code": "production / Phase1-2 path (preserved)",
            "candidate_ir_compiler": "Phase4 bridge candidates ONLY",
        },
    }


def _extract_event_sequence(entry_node):
    """Find first event_sequence in entry AST; return (seq, path) or (None, None)."""
    if not isinstance(entry_node, dict):
        return None, None
    if "event_sequence" in entry_node:
        return entry_node["event_sequence"], ["event_sequence"]
    for key in ("all", "any"):
        if key in entry_node and isinstance(entry_node[key], list):
            for i, child in enumerate(entry_node[key]):
                seq, path = _extract_event_sequence(child)
                if seq is not None:
                    return seq, [key, i] + path
    return None, None


def _features_in_node(node, out=None):
    out = out if out is not None else set()
    if not isinstance(node, dict):
        return out
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        for c in node[key]:
            _features_in_node(c, out)
        return out
    if "not" in node:
        return _features_in_node(node["not"], out)
    if "event_sequence" in node:
        for s in (node["event_sequence"].get("steps") or []):
            _features_in_node(s, out)
        return out
    if "state_machine" in node:
        out.add("state_id")
        for tr in node["state_machine"].get("transitions") or []:
            _features_in_node(tr.get("when") or {}, out)
        return out
    for side in ("left", "right"):
        op = node.get(side) or {}
        if isinstance(op, dict) and "feature" in op:
            out.add(str(op["feature"]))
    return out


def research_strategy_to_ir(probe_id, strategy, class_name, promotion_status="bridge_candidate"):
    """Build Candidate IR from a Phase3 research probe strategy."""
    strat = copy.deepcopy(strategy)
    entry = strat.get("entry") or {}
    exit_n = strat.get("exit") or {}
    feats = sorted(_features_in_node(entry) | _features_in_node(exit_n))
    research_feats = [f for f in feats if f in RESEARCH_CORE]
    seq, _ = _extract_event_sequence(entry)

    # Derive explicit state machine from event sequence (or synthetic for AND-style)
    states = ["idle", "setup", "armed", "triggered", "in_trade", "invalidated", "cooldown"]
    transitions = []
    if seq and seq.get("steps"):
        steps = seq["steps"]
        max_gap = int(seq.get("max_bars") or 12)
        for i, step in enumerate(steps):
            fr = "idle" if i == 0 else ("setup" if i == 1 else "armed")
            to = "setup" if i == 0 else ("armed" if i == 1 else "triggered")
            if i >= 2:
                fr = "armed"
                to = "triggered"
            transitions.append({
                "from": fr if i < 2 else ("setup" if i == 0 else "armed"),
                "to": to,
                "when": step,
                "id": "t_%s" % (step.get("id") or i),
            })
        event_order = {
            "event_A": steps[0] if len(steps) > 0 else None,
            "event_B": steps[1] if len(steps) > 1 else None,
            "maximum_gap": max_gap,
            "event_C_veto": None,
            "confirmation": steps[2] if len(steps) > 2 else (steps[-1] if steps else None),
            "entry_eligibility": "all_prior_events_in_order_within_gap",
            "ordered": True,
            "ban_unordered_AND": True,
            "steps": steps,
        }
    else:
        # AND-style entry: still encode as ordered synthetic states (setup→confirm→entry)
        leaves = []
        if "all" in entry:
            leaves = [c for c in entry["all"] if isinstance(c, dict) and "op" in c]
        max_gap = 6
        for i, leaf in enumerate(leaves):
            transitions.append({
                "from": ["idle", "setup", "armed"][min(i, 2)],
                "to": ["setup", "armed", "triggered"][min(i, 2)],
                "when": leaf,
                "id": "t_%s" % (leaf.get("id") or i),
            })
        event_order = {
            "event_A": leaves[0] if leaves else None,
            "event_B": leaves[1] if len(leaves) > 1 else None,
            "maximum_gap": max_gap,
            "event_C_veto": None,
            "confirmation": leaves[2] if len(leaves) > 2 else (leaves[-1] if leaves else None),
            "entry_eligibility": "ordered_state_path_required",
            "ordered": True,
            "ban_unordered_AND": True,
            "steps": leaves,
            "note": "AND leaves lifted into ordered state path for formal IR (not unordered AND compile)",
        }

    multi = strat.get("multi_asset") or {}
    primary = (strat.get("supported_instruments") or ["BTC-USDT-SWAP"])[0]
    reference = multi.get("lead") if class_name == "Cross_asset_sync" else None

    core_causal = {
        "OI": ["oi_z20", "oi"],
        "Cross_asset_sync": ["lead_ret1", "lag_ret1", "lead_ret3", "lead_lag_corr20"],
        "Taker_flow": ["taker_imbalance", "taker_imbalance_z20"],
    }.get(class_name, research_feats[:2])
    # Only keep core vars actually present in the strategy AST
    core_causal = [c for c in core_causal if c in feats] or list(research_feats[:1])

    ir = {
        "candidate_ir_version": IR_SCHEMA_VERSION,
        "identity": {
            "candidate_id": "bridge_%s" % probe_id.replace("probe_", ""),
            "source_probe_id": probe_id,
            "source_research_spec_hash": sha(strat),
            "source_research_ast_hash": sha({"entry": entry, "exit": exit_n}),
            "candidate_ir_version": IR_SCHEMA_VERSION,
            "created_at": _now(),
            "promotion_status": promotion_status,
            "mechanism_class": class_name,
            "title": strat.get("name") or probe_id,
        },
        "data_deps": {
            "primary_symbol": primary,
            "reference_symbol": reference or multi.get("lead"),
            "timeframes": [strat.get("timeframe") or "5m"],
            "oi": class_name == "OI" or any(f.startswith("oi") for f in feats),
            "funding_basis": any(f.startswith(("funding", "basis")) for f in feats),
            "taker_flow": class_name == "Taker_flow" or any("taker" in f or f == "flow_imbalance" for f in feats),
            "cross_asset": class_name == "Cross_asset_sync" or any(
                f.startswith(("lead_", "lag_", "cross_")) for f in feats),
            "required_features": research_feats,
            "all_features": feats,
            "feature_freshness": {
                "oi_ms": 1800000, "taker_ms": 1800000, "basis_ms": 1800000,
                "funding_ms": 29400000,
            },
            "missing_data_policy": "NaN → fail-closed (no signal)",
            "fail_closed_rule": True,
            "proxy_declarations": strat.get("proxy_declarations") or [],
        },
        "state_machine": {
            "states": states,
            "transitions": transitions,
            "transition_conditions": [t.get("when") for t in transitions],
            "timeouts": {"max_hold_bars": int(strat.get("max_hold_bars") or 12)},
            "reset_rules": "return_idle_on_invalidation_or_exit",
            "invalidation": "mechanism_exit_or_regime_veto",
            "cooldown": {"bars": 2},
            "accept_states": ["triggered"],
        },
        "event_order": event_order,
        "entry": {
            "regime": {"feature": "regime_ok", "op": "gt", "right": {"value": 0.5}},
            "setup": event_order.get("event_A"),
            "trigger": event_order.get("event_B"),
            "confirmation": event_order.get("confirmation"),
            "veto": {"feature": "regime_ok", "op": "lt", "right": {"value": 0.5}},
            "final_entry": entry,
            "side": str(strat.get("direction") or "short").lower(),
            "timestamp_semantics": "bar_close_asof_past_only_no_lookahead",
        },
        "exit": {
            "production_sl_0_9pct": 0.009,
            "mechanism_invalidation": exit_n,
            "time_stop": {"max_hold_bars": int(strat.get("max_hold_bars") or 12)},
            "dynamic_exit": exit_n,
            "strategy_close": True,
            "tp": None,
            "trailing": None,
            "cross_asset_invalidation": class_name == "Cross_asset_sync",
            "research_exit_ast": exit_n,
        },
        "causal_structure": {
            "core_causal_variables": core_causal,
            "supporting_variables": [f for f in feats if f not in core_causal],
            "non_causal_filters": ["atr14", "vol_z20"],
            "forbidden_proxies": sorted(FORBIDDEN_PROXY_FEATURES),
            "expected_ablation_effect": "entries_drop_materially_when_core_removed",
            "expected_event_order_destruction_effect": "overlap_with_base_collapses",
        },
        "compile_bans": list(candidate_ir_schema()["compile_bans"]),
        "research_risk_sizing": {
            # Research/incubator evaluation only — live mount stays B/30%/20x
            # via CLI --confirm until separately approved.
            "enabled": True,
            "formula": "(Equity * Risk_Pct) / (ATR_14 * Target_Multiplier)",
            "risk_pct_default": 0.012,
            "risk_pct_min": 0.010,
            "risk_pct_max": 0.015,
            "atr_period": 14,
            "target_multiplier": 1.0,
            "dd_throttle_risk_pct": 0.005,
            "dd_throttle_of_max_dd": 0.50,
            "production_mount_unchanged": True,
            "production_b_grade_position_pct": 0.30,
            "production_leverage": 20,
            "production_stop_loss_pct": 0.009,
            "dynamic_r_applies_to_live": False,
        },
        "source_research_strategy": strat,
        "equivalence_research_strategy": None,  # filled after ordered normalization
    }
    # For equivalence: if research was AND-only, build ordered-normalized research twin
    # so Research↔IR↔Formal compare the promotion-faithful form (not silent AND collapse).
    if not seq:
        norm = copy.deepcopy(strat)
        norm["entry"] = {
            "all": [{
                "event_sequence": {
                    "max_bars": int(event_order.get("maximum_gap") or 6),
                    "steps": copy.deepcopy(event_order.get("steps") or []),
                }
            }]
        }
        # keep original exit
        ir["equivalence_research_strategy"] = norm
        ir["source_research_strategy"] = strat
        ir["identity"]["research_normalized_to_ordered"] = True
    else:
        ir["equivalence_research_strategy"] = copy.deepcopy(strat)
        ir["identity"]["research_normalized_to_ordered"] = False
    ir["identity"]["candidate_ir_hash"] = sha({
        k: ir[k] for k in (
            "identity", "data_deps", "state_machine", "event_order",
            "entry", "exit", "causal_structure", "compile_bans",
        )
    })
    return ir


def _refuse_fixed_tiny_tp(pct, context="exit"):
    try:
        value = float(pct)
    except Exception:
        raise CompilerError(
            "REFUSED: %s fixed take-profit pct is not numeric — FAIL CLOSED" % context
        )
    if not math.isfinite(value):
        raise CompilerError(
            "REFUSED: %s fixed take-profit pct is not finite — FAIL CLOSED" % context
        )
    frac = value / 100.0 if value >= 0.2 else value
    if frac < FIXED_TP_MIN_PCT:
        raise CompilerError(
            "REFUSED: fixed tiny take-profit %.4f%% banned (need >=%.1f%% OR "
            "atr_trailing/swing_extreme). Does NOT silently convert. context=%s"
            % (frac * 100.0, FIXED_TP_MIN_PCT * 100.0, context)
        )
    return frac


def _scan_exit_node_for_banned_tp(node, path="exit"):
    """Walk exit AST / IR exit dict; refuse tiny fixed TP; allow structured ops."""
    if not isinstance(node, dict):
        return
    if "exit_op" in node:
        op = str(node.get("exit_op") or "")
        if op not in STRUCTURED_EXIT_OPS:
            raise CompilerError("unsupported exit_op in IR: %s" % op)
        if op == "fixed_pct_tp":
            pct = node.get("pct", node.get("price_pct"))
            if pct is None and isinstance(node.get("params"), dict):
                pct = node["params"].get("pct") or node["params"].get("price_pct")
            _refuse_fixed_tiny_tp(pct, context="%s.exit_op.fixed_pct_tp" % path)
        return
    # IR-level tp / trailing blobs
    for key in ("tp", "trailing", "dynamic_exit", "strategy_close"):
        blob = node.get(key)
        if isinstance(blob, dict):
            btype = str(blob.get("type") or blob.get("kind") or "").lower()
            if btype in ("fixed", "fixed_pct", "fixed_pct_tp", "price_pct", "micro_tp"):
                pct = blob.get("pct", blob.get("price_pct", blob.get("value")))
                _refuse_fixed_tiny_tp(pct, context="%s.%s" % (path, key))
            for k in FIXED_TINY_TP_KEYS:
                if k in blob and blob.get(k) is not None:
                    _refuse_fixed_tiny_tp(blob.get(k), context="%s.%s.%s" % (path, key, k))
            if "exit_op" in blob or "any" in blob or "all" in blob:
                _scan_exit_node_for_banned_tp(blob, path="%s.%s" % (path, key))
    for k in FIXED_TINY_TP_KEYS:
        if k in node and node.get(k) is not None:
            # production_sl_0_9pct is protective SL — never treat as TP
            if k in ("take_profit_pct", "price_take_profit_pct", "take_profit_price_ratio",
                     "fixed_tp_pct", "tp_pct", "price_tp_pct", "pct", "price_pct"):
                # Only ban when clearly a take-profit field or under tp/trailing path
                if "tp" in path.lower() or "take_profit" in k or "tp_pct" in k or k in (
                    "price_take_profit_pct", "take_profit_price_ratio", "fixed_tp_pct",
                ):
                    _refuse_fixed_tiny_tp(node.get(k), context="%s.%s" % (path, k))
    for key in ("all", "any"):
        if key in node and isinstance(node[key], list):
            for i, child in enumerate(node[key]):
                _scan_exit_node_for_banned_tp(child, path="%s.%s[%d]" % (path, key, i))
    if "not" in node:
        _scan_exit_node_for_banned_tp(node["not"], path="%s.not" % path)


def _normalize_structured_exits(exit_section):
    """Preserve research exit AST; attach structured exit metadata (opt-in)."""
    exit_section = copy.deepcopy(exit_section or {})
    formal_exit = copy.deepcopy(
        exit_section.get("research_exit_ast")
        or exit_section.get("mechanism_invalidation")
        or exit_section.get("dynamic_exit")
        or {"any": []}
    )
    _scan_exit_node_for_banned_tp(formal_exit, path="research_exit_ast")
    _scan_exit_node_for_banned_tp(exit_section, path="exit")
    # Optional structured trailing / swing from IR exit.trailing / exit.tp
    extras = []
    trailing = exit_section.get("trailing")
    if isinstance(trailing, dict) and trailing.get("exit_op") == "atr_trailing":
        n_atr = float(trailing.get("n_atr") or 3.0)
        if n_atr < 2.5 or n_atr > 4.0:
            raise CompilerError("atr_trailing n_atr must be in [2.5, 4.0]")
        extras.append({
            "id": "ir_atr_trail",
            "exit_op": "atr_trailing",
            "n_atr": n_atr,
            "atr_period": int(trailing.get("atr_period") or 14),
            "role": "take_profit",
        })
    swing = exit_section.get("swing") or (
        exit_section.get("tp") if isinstance(exit_section.get("tp"), dict)
        and exit_section["tp"].get("exit_op") == "swing_extreme" else None
    )
    if isinstance(swing, dict) and swing.get("exit_op") == "swing_extreme":
        lookback = int(swing.get("lookback") or 20)
        if lookback < 5 or lookback > 60:
            raise CompilerError("swing_extreme lookback must be in [5, 60]")
        extras.append({
            "id": "ir_swing_extreme",
            "exit_op": "swing_extreme",
            "lookback": lookback,
            "role": str(swing.get("role") or "take_profit"),
        })
    if extras:
        if isinstance(formal_exit, dict) and "any" in formal_exit:
            formal_exit["any"] = list(formal_exit["any"]) + extras
        elif isinstance(formal_exit, dict) and formal_exit.get("exit_op"):
            formal_exit = {"any": [formal_exit] + extras}
        else:
            formal_exit = {"any": extras if not formal_exit else [formal_exit] + extras}
    return formal_exit


def _validate_ir(ir):
    rejected = []
    unsupported = []
    if not ir or not isinstance(ir, dict):
        raise CompilerError("IR missing")
    for sec in ("identity", "data_deps", "state_machine", "event_order",
                "entry", "exit", "causal_structure", "compile_bans"):
        if sec not in ir:
            raise CompilerError("IR missing section: %s" % sec)
    # Phase-4 additive: default research dynamic R metadata if absent (legacy IR ok)
    if "research_risk_sizing" not in ir:
        ir["research_risk_sizing"] = {
            "enabled": True,
            "formula": "(Equity * Risk_Pct) / (ATR_14 * Target_Multiplier)",
            "risk_pct_default": 0.012,
            "risk_pct_min": 0.010,
            "risk_pct_max": 0.015,
            "atr_period": 14,
            "target_multiplier": 1.0,
            "dd_throttle_risk_pct": 0.005,
            "dd_throttle_of_max_dd": 0.50,
            "production_mount_unchanged": True,
            "dynamic_r_applies_to_live": False,
        }

    feats = set(ir["data_deps"].get("all_features") or [])
    for f in feats:
        fl = f.lower()
        if any(b in fl for b in FORBIDDEN_PROXY_FEATURES):
            rejected.append({"clause": "forbidden_proxy", "feature": f})
            unsupported.append(f)

    core = set(ir["causal_structure"].get("core_causal_variables") or [])
    if not core:
        raise CompilerError("core_causal_variables empty — FAIL CLOSED")
    if not (core & RESEARCH_CORE) and not (core & feats):
        raise CompilerError("core causal vars not in research set — FAIL CLOSED")

    eo = ir["event_order"]
    if not eo.get("ordered") or not eo.get("ban_unordered_AND"):
        raise CompilerError("event_order must be ordered / ban unordered AND")

    sm = ir["state_machine"]
    if not sm.get("states") or not sm.get("transitions"):
        raise CompilerError("state_machine required — cannot delete")

    deps = ir["data_deps"]
    cls_hint = ir["identity"].get("mechanism_class")
    if cls_hint == "OI" and not deps.get("oi"):
        raise CompilerError("OI class requires oi data dep")
    if cls_hint == "Taker_flow" and not deps.get("taker_flow"):
        raise CompilerError("Taker class requires taker_flow data dep")
    if cls_hint == "Cross_asset_sync":
        if not deps.get("cross_asset"):
            raise CompilerError("Cross-asset class requires cross_asset data dep")
        if not deps.get("reference_symbol"):
            raise CompilerError("Cross-asset requires reference_symbol — no single-asset substitute")

    # Phase-2 exit ban (fail-closed, no silent convert)
    _scan_exit_node_for_banned_tp(ir.get("exit") or {}, path="exit")

    return rejected, unsupported


def compile_candidate_ir(ir, allow_partial=False):
    """Deterministic compile IR → formal strategy definition. FAIL CLOSED."""
    trace = {
        "compiler_version": COMPILER_VERSION,
        "started_at": _now(),
        "steps": [],
        "legacy_fallback_used": False,
        "rejected_clauses": [],
        "unsupported_feature_list": [],
        "feature_mapping": {},
        "state_mapping": {},
        "event_order_mapping": {},
        "exit_mapping": {},
    }
    try:
        rejected, unsupported = _validate_ir(ir)
        trace["rejected_clauses"] = rejected
        trace["unsupported_feature_list"] = unsupported
        if unsupported and not allow_partial:
            raise CompilerError("unsupported features: %s" % unsupported)
        trace["steps"].append("validate_ir_ok")

        # Feature mapping: identity (research features preserved)
        feats = ir["data_deps"].get("all_features") or []
        for f in feats:
            if any(b in f.lower() for b in FORBIDDEN_PROXY_FEATURES):
                raise CompilerError("refuse proxy feature %s" % f)
            # Ban replacing core research with OHLC template features
            trace["feature_mapping"][f] = {"from": f, "to": f, "kind": (
                "research" if f in RESEARCH_CORE else "base")}
        core = ir["causal_structure"]["core_causal_variables"]
        for c in core:
            if c not in trace["feature_mapping"] and c in RESEARCH_CORE:
                trace["feature_mapping"][c] = {"from": c, "to": c, "kind": "research_core"}
            if c not in RESEARCH_CORE and c in FORBIDDEN_TEMPLATE_FEATURES:
                raise CompilerError("OHLC template feature cannot be core causal: %s" % c)
        trace["steps"].append("feature_mapping_ok")

        # State mapping
        sm = ir["state_machine"]
        for st in sm["states"]:
            trace["state_mapping"][st] = st
        trace["steps"].append("state_mapping_ok")

        # Event order → must remain event_sequence (never flatten to unordered AND)
        eo = ir["event_order"]
        steps = eo.get("steps") or []
        if not steps:
            raise CompilerError("event_order.steps empty — FAIL CLOSED")
        max_gap = int(eo.get("maximum_gap") or 12)
        ordered_entry_seq = {
            "event_sequence": {
                "max_bars": max_gap,
                "steps": copy.deepcopy(steps),
            }
        }
        # Preserve additional non-sequence conjuncts from research entry if present
        # (do NOT re-add leaves that were lifted into event_order.steps — unique ids required)
        src_entry = copy.deepcopy(ir["entry"].get("final_entry") or {})
        step_ids = set()
        for st in steps:
            if isinstance(st, dict) and st.get("id"):
                step_ids.add(str(st["id"]))
        extra = []
        if "all" in src_entry:
            for child in src_entry["all"]:
                if isinstance(child, dict) and "event_sequence" not in child:
                    cid = str(child.get("id") or "")
                    if cid and cid in step_ids:
                        continue  # already in ordered sequence
                    if "op" in child or "all" in child or "any" in child:
                        extra.append(child)
        entry_all = [ordered_entry_seq]
        entry_all.extend(extra)
        formal_entry = {"all": entry_all}
        trace["event_order_mapping"] = {
            "preserved_as": "event_sequence",
            "flattened_to_unordered_AND": False,
            "max_bars": max_gap,
            "n_steps": len(steps),
        }
        trace["steps"].append("event_order_mapping_ok")

        # Exit mapping — preserve research exit + production SL adapter metadata
        # Phase-2: refuse fixed tiny TP; wire optional atr_trailing / swing_extreme
        formal_exit = _normalize_structured_exits(ir["exit"])
        # Never touch production protective SL adapter
        production_sl = float(ir["exit"].get("production_sl_0_9pct") or 0.009)
        if abs(production_sl - 0.009) > 1e-9:
            # Allow explicit 0.9% only for production adapter; other values recorded
            # but research must not abolish the protective chain default.
            pass
        trace["exit_mapping"] = {
            "research_exit_preserved": True,
            "production_sl_0_9pct": production_sl,
            "time_stop": ir["exit"].get("time_stop"),
            "structured_exits_opt_in": True,
            "fixed_tiny_tp_banned": True,
            "fixed_tp_min_pct": FIXED_TP_MIN_PCT,
            "modified": False,
        }
        trace["steps"].append("exit_mapping_ok")

        src = ir.get("source_research_strategy") or {}
        formal = {
            "schema": "qiyu_strategy_dsl_research_v1",
            "formal_implementation_mode": "candidate_ir_compiler",
            "key": ir["identity"]["candidate_id"],
            "name": ir["identity"].get("title") or ir["identity"]["candidate_id"],
            "direction": ir["entry"]["side"],
            "timeframe": (ir["data_deps"].get("timeframes") or ["5m"])[0],
            "supported_instruments": [ir["data_deps"]["primary_symbol"]],
            "entry": formal_entry,
            "exit": formal_exit,
            "max_hold_bars": int((ir["exit"].get("time_stop") or {}).get("max_hold_bars") or 12),
            "stop_loss_pct": float(ir["exit"].get("production_sl_0_9pct") or 0.009),
            "research_only": True,
            "live_enabled": False,
            "auto_trade_eligible": False,
            "bridge_candidate": True,
            "candidate_ir_hash": ir["identity"].get("candidate_ir_hash"),
            "source_probe_id": ir["identity"]["source_probe_id"],
            "proxy_declarations": ir["data_deps"].get("proxy_declarations") or [],
            "data_sources": src.get("data_sources") or [],
            "multi_asset": ({
                "lead": ir["data_deps"].get("reference_symbol"),
                "lag": ir["data_deps"]["primary_symbol"],
            } if ir["data_deps"].get("reference_symbol") else None),
            "state_machine_def": {
                "states": sm["states"],
                "transitions": sm["transitions"],
                "accept": sm.get("accept_states") or ["triggered"],
            },
            "compiler_version": COMPILER_VERSION,
            "legacy_fallback_used": False,
            "description": "phase4 bridge formal from candidate IR",
            "origin": "windtalker_phase4_bridge",
            "version": 1,
            # Phase-4 research dynamic R metadata (NOT live mount sizing)
            "research_risk_sizing": ir.get("research_risk_sizing") or {
                "enabled": True,
                "formula": "(Equity * Risk_Pct) / (ATR_14 * Target_Multiplier)",
                "production_mount_unchanged": True,
                "dynamic_r_applies_to_live": False,
            },
            "dynamic_risk_sizing_default": True,
        }
        # Strip None multi_asset
        if formal["multi_asset"] is None:
            del formal["multi_asset"]

        code_hash = sha(formal)
        trace["generated_code_hash"] = code_hash
        trace["finished_at"] = _now()
        trace["verdict"] = "PASS"
        trace["steps"].append("emit_formal_ok")
        return {
            "ok": True,
            "formal_definition": formal,
            "compilation_trace": trace,
            "legacy_fallback_used": False,
            "compiler_verdict": "PASS",
            "generated_code_hash": code_hash,
            "source_ir_hash": ir["identity"].get("candidate_ir_hash"),
        }
    except Exception as exc:
        trace["finished_at"] = _now()
        trace["verdict"] = "FAIL_CLOSED"
        trace["error"] = str(exc)
        trace["legacy_fallback_used"] = False  # never fall back
        return {
            "ok": False,
            "formal_definition": None,
            "compilation_trace": trace,
            "legacy_fallback_used": False,
            "compiler_verdict": "FAIL_CLOSED",
            "generated_code_hash": None,
            "error": str(exc),
        }


def codex_implement_from_candidate_ir(spec_pack):
    """Drop-in for pipeline_step_a when formal_implementation_mode=candidate_ir_compiler.

    FAIL CLOSED — never invokes legacy OHLC family templates.
    """
    meta = (spec_pack or {}).get("meta") or {}
    ir = (spec_pack or {}).get("candidate_ir") or meta.get("candidate_ir")
    if not ir:
        return {
            "ok": False,
            "error": "candidate_ir missing — FAIL CLOSED (no legacy fallback)",
            "legacy_fallback_used": False,
            "compiler_verdict": "FAIL_CLOSED",
        }
    result = compile_candidate_ir(ir)
    if not result.get("ok"):
        return result
    formal = result["formal_definition"]
    # Shape similar to legacy implementer output for pipeline compatibility
    return {
        "ok": True,
        "definition": formal,
        "dsl": formal,
        "key": formal["key"],
        "symbol": formal["supported_instruments"][0],
        "timeframe": formal["timeframe"],
        "direction": formal["direction"],
        "compiler_version": COMPILER_VERSION,
        "legacy_fallback_used": False,
        "compilation_trace": result["compilation_trace"],
        "generated_code_hash": result["generated_code_hash"],
        "formal_implementation_mode": "candidate_ir_compiler",
        "compiler_verdict": "PASS",
    }
