# -*- coding: utf-8 -*-
"""Exploration modes A/B/C/D with real Mode A data isolation (not prompt-only)."""
from __future__ import print_function

import copy

from .config import EXPLORATION_MODES


# Keys stripped from inputs for Mode A (success strategy specifics)
MODE_A_FORBIDDEN_INPUT_KEYS = (
    "successful_strategy_dsl",
    "successful_strategy_params",
    "successful_strategy_source",
    "successful_filters",
    "successful_tp_structure",
    "repair_history",
    "promoted_individuals_dsl",
    "pool_individuals",
    "best_dsl",
    "winning_entry",
    "winning_exit",
    "factory_context.positive_core_samples",
    "factory_context.successful_candidates",
)

MODE_A_ALLOWED = (
    "covered_mechanism_tags",
    "mechanism_fingerprints",
    "forbidden_list",
    "freezer_death_summaries",
    "niche_vacancies",
    "symbol_tf_features",
    "death_heatmap_top10",
    "coverage_map_keys_only",
)


def lock_exploration_mode(mode):
    mode = str(mode or "A").upper()
    if mode not in EXPLORATION_MODES:
        raise ValueError("invalid exploration_mode: %s" % mode)
    return mode


def isolate_mode_a_context(inputs, fingerprint_index=None, exclusion_rules=None):
    """Return a new context dict with success specifics removed by code, not prompts."""
    src = copy.deepcopy(inputs or {})
    isolated = {
        "schema": "qiyu_exploration_mode_a_v1",
        "mode": "A",
        "isolation": "data_permission",
        "allowed_channels": list(MODE_A_ALLOWED),
        "stripped_keys": [],
        "covered_mechanism_tags": [],
        "mechanism_fingerprints": [],
        "forbidden_list": [],
        "freezer_death_summaries": [],
        "niche_vacancies": [],
        "symbol_tf_features": {},
        "focus_candidates": [],
    }

    # Death / freezer summaries only (no full winning DSL)
    for row in (src.get("death_heatmap_top10") or [])[:10]:
        if isinstance(row, dict):
            isolated["freezer_death_summaries"].append({
                "code": row.get("code") or row.get("death_cause_code"),
                "count": row.get("count") or row.get("n"),
                "note": (row.get("note") or row.get("title") or "")[:120],
            })
        else:
            isolated["freezer_death_summaries"].append({"code": str(row)})

    for row in (src.get("freezer_last10") or [])[:10]:
        if isinstance(row, dict):
            isolated["freezer_death_summaries"].append({
                "code": row.get("death_cause_code") or row.get("code"),
                "summary": str(row.get("summary") or row.get("lesson") or "")[:160],
            })
            # Explicitly drop any nested dsl/params
            if row.get("dsl") or row.get("params") or row.get("entry"):
                isolated["stripped_keys"].append("freezer_row_dsl_or_params")

    # Coverage: keys/grades only — no entry conditions
    for row in (src.get("coverage_map") or []):
        isolated.setdefault("coverage_map_keys_only", []).append({
            "key": row.get("key"),
            "symbol": row.get("symbol"),
            "timeframe": row.get("timeframe"),
            "grade": row.get("grade"),
        })
        if row.get("dsl") or row.get("entry"):
            isolated["stripped_keys"].append("coverage_dsl")

    # Niche vacancies from focus candidates
    isolated["niche_vacancies"] = list(src.get("focus_candidates") or [])[:12]
    isolated["focus_candidates"] = isolated["niche_vacancies"]

    # Fingerprints / exclusions
    for fp in (fingerprint_index or [])[:200]:
        isolated["mechanism_fingerprints"].append({
            "family_hash": (fp.get("fingerprint") or fp).get("family_hash")
            if isinstance(fp, dict) else None,
            "fingerprint_hash": (fp.get("fingerprint") or fp).get("fingerprint_hash")
            if isinstance(fp, dict) else None,
            "forced_actor": (fp.get("fingerprint") or fp).get("forced_actor")
            if isinstance(fp, dict) else None,
            "distortion_type": (fp.get("fingerprint") or fp).get("distortion_type")
            if isinstance(fp, dict) else None,
        })
        tag = (fp.get("fingerprint") or fp).get("distortion_type") if isinstance(fp, dict) else None
        if tag:
            isolated["covered_mechanism_tags"].append(tag)

    for rule in (exclusion_rules or [])[:100]:
        isolated["forbidden_list"].append(rule)

    # Micro features without strategy recipes
    micro = src.get("micro_72h") or {}
    if isinstance(micro, dict):
        isolated["symbol_tf_features"] = {
            "tags": (micro.get("tags") or [])[:20],
            "source": micro.get("source"),
            "note": "mode_a_no_success_recipes",
        }

    # Factory context: keep only death codes / niche summary — strip positive samples
    fc = src.get("factory_context") or {}
    if isinstance(fc, dict):
        isolated["factory_context_safe"] = {
            "niche_summary": fc.get("niche_summary"),
            "mandatory_niches": fc.get("mandatory_niches"),
            "death_codes": (fc.get("death_codes") or [])[:20],
            "micro_primitive_tags": (fc.get("micro_primitive_tags") or [])[:20],
        }
        for bad in ("positive_core_samples", "successful_candidates", "best_dsl",
                    "drafts", "candidates_with_dsl"):
            if bad in fc:
                isolated["stripped_keys"].append("factory_context.%s" % bad)

    # Prove isolation: scan for DSL-looking blobs
    blob = str(isolated)
    for tok in ("\"entry\":", "max_hold_bars", "supported_instruments"):
        if tok in blob and tok != "supported_instruments":
            # entry key should not appear from success recipes
            if tok == "\"entry\":":
                isolated["stripped_keys"].append("leak_token_entry")
    # Remove accidental deep success keys if caller stuffed them
    for k in list(src.keys()):
        if k in MODE_A_FORBIDDEN_INPUT_KEYS or k.startswith("successful_"):
            isolated["stripped_keys"].append(k)

    isolated["isolation_verified"] = "leak_token_entry" not in isolated["stripped_keys"]
    return isolated


def build_mode_context(mode, inputs, **kwargs):
    mode = lock_exploration_mode(mode)
    if mode == "A":
        return isolate_mode_a_context(
            inputs,
            fingerprint_index=kwargs.get("fingerprint_index"),
            exclusion_rules=kwargs.get("exclusion_rules"),
        )
    if mode == "B":
        # Anti-inheritance: identify dominant family then propose opposite at mechanism layer
        fps = kwargs.get("fingerprint_index") or []
        dominant = _dominant_family(fps)
        return {
            "schema": "qiyu_exploration_mode_b_v1",
            "mode": "B",
            "dominant_family": dominant,
            "anti_thesis_required_fields": [
                "opposite_core_hypothesis",
                "why_valid_in_regime",
                "anti_regression_guards",
            ],
            "inputs": inputs,
        }
    if mode == "C":
        unused = kwargs.get("unused_data_families") or [
            "trade_interval_change", "aggressive_flow_run", "oi_price_lag",
            "mark_index_divergence", "perp_spot_basis", "book_cancel_speed",
            "post_trade_refill", "liquidation_density", "session_handoff_reprice",
        ]
        return {
            "schema": "qiyu_exploration_mode_c_v1",
            "mode": "C",
            "allowed_data_family": (unused[0] if unused else "trade_interval_change"),
            "forbidden_core_indicators": [
                "rsi", "macd", "ma", "boll", "supertrend", "adx"
            ],
            "inputs": inputs,
        }
    # Mode D
    return {
        "schema": "qiyu_exploration_mode_d_v1",
        "mode": "D",
        "repair_policy": "engineering_errors_only",
        "max_repair_rounds": 0,
        "inputs": inputs,
    }


def _dominant_family(fps):
    counts = {}
    for row in fps or []:
        fp = row.get("fingerprint") if isinstance(row, dict) and "fingerprint" in row else row
        if not isinstance(fp, dict):
            continue
        key = fp.get("family_hash") or fp.get("distortion_type") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return {"family": None, "count": 0}
    fam = max(counts.items(), key=lambda x: x[1])
    return {"family": fam[0], "count": fam[1]}


def assert_mode_locked(task, attempted_mode):
    locked = str((task or {}).get("exploration_mode") or "").upper()
    if locked and str(attempted_mode or "").upper() != locked:
        raise RuntimeError(
            "exploration_mode_locked:%s attempted=%s" % (locked, attempted_mode)
        )
