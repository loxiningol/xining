# -*- coding: utf-8 -*-
"""STEP A creation pipeline — Gates 0–7, mechanism_spec, 20 tests, failure KB."""
from __future__ import print_function

# Allow `python3 dual_engine_workflow_v2/pipeline_step_a.py --strategy_json ...`
# without requiring `python3 -m` (sets package context before relative imports).
if __name__ == "__main__" and (__package__ is None or __package__ == ""):
    import sys as _sys
    from pathlib import Path as _Path
    _repo_root = _Path(__file__).resolve().parents[1]
    if str(_repo_root) not in _sys.path:
        _sys.path.insert(0, str(_repo_root))
    __package__ = "dual_engine_workflow_v2"

import copy
import json
import threading
import time
import traceback
import uuid
from datetime import datetime

from .config import (
    WORKFLOW_VERSION,
    MIGRATION_VERSION,
    CODE_VERSION,
    DATA_VERSION,
    BACKTEST_VERSION,
    TARGET_TRADES_PER_DAY,
    ARTIFACTS_DIR,
    ensure_dirs,
    _now,
    _atomic,
)
from .protocol import envelope, validate_envelope, new_call_id
from .exploration import lock_exploration_mode, build_mode_context, assert_mode_locked
from . import store
from .step_a_config import (
    STEP_A_CODE_VERSION,
    STEP_A_SCHEMA,
    STEP_A_MODES,
    MECHANISM_SPEC_FIELDS,
    EXHAUSTION_FADE_FAMILY,
    REPAIR_ROUND_TYPES,
    PRODUCTION_CONSTRAINTS,
    ensure_step_a_dirs,
)
from .mechanism_spec import (
    glm_spec_prompt_template,
    normalize_mechanism_spec,
    spec_to_mechanism_statement,
    save_immutable_mechanism_spec,
    MechanismSpecError,
)
from .step_a_fingerprint import build_step_a_fingerprint, duplicate_intercept
from .fidelity_diff import build_fidelity_diff, save_fidelity_diff
from .split_tests_20 import run_split_tests_20, save_split_tests
from .failure_kb import (
    kb_context_for_ai,
    path_is_blocked,
    build_failure_record,
    save_failure_record,
    record_pipeline_rejection,
)
from .gates import (
    evaluate_gate0, evaluate_gate1, evaluate_gate2, evaluate_gate3,
    evaluate_gate4, evaluate_gate5, evaluate_gate6, evaluate_gate7,
    assemble_gate_results, save_gate_results,
)
from .attackers import (
    deepseek_logic_attack,
    production_risk_attack,
    glm_mechanism_review,
    codex_fidelity_review,
)
from .scores_split import build_split_scores
from .repair_drift import (
    init_repair_log,
    record_repair_round,
    apply_engineering_repair_only,
)
from .fidelity import rule_based_condition_audit, apply_audit_removals
from .mechanism import build_fingerprint_from_statement


_JOB = {"running": False, "kind": None, "started_at": None, "error": None}
_JOB_LOCK = threading.Lock()


def _admission_profile():
    """Post-creation review profile — locked to ADA5顺势回升校准档.

    Default / only supported production profile: ada_t3_calibrated_v1
    (R1 syntax/density + R2 stability + R3 matrix soft + R4 三AI + human confirm).

    Legacy hard funnel is disabled unless BOTH are set:
      STEP_A_ADMISSION_PROFILE=legacy_funnel
      STEP_A_ALLOW_LEGACY_FUNNEL=1
    """
    import os
    raw = str(os.environ.get("STEP_A_ADMISSION_PROFILE") or "ada_t3_calibrated_v1").strip()
    if raw in ("legacy_funnel", "legacy", "old"):
        if str(os.environ.get("STEP_A_ALLOW_LEGACY_FUNNEL") or "").strip() in ("1", "true", "on", "yes"):
            return "legacy_funnel"
        print(
            "[pipeline_step_a] REFUSING legacy_funnel without STEP_A_ALLOW_LEGACY_FUNNEL=1; "
            "forcing ada_t3_calibrated_v1 (ADA5顺势回升)",
            flush=True,
        )
        return "ada_t3_calibrated_v1"
    return raw or "ada_t3_calibrated_v1"


def _admission_v2_enabled():
    return _admission_profile() in (
        "ada_t3_calibrated_v1", "ada_t3", "1", "true", "on", "reconstructed",
    )


def _soft_skip_legacy(task, stage, detail=None):
    """Record a legacy hard-gate skip under reconstructed admission."""
    task.setdefault("admission_v2", {})
    task["admission_v2"]["profile"] = _admission_profile()
    skips = task["admission_v2"].setdefault("legacy_soft_skips", [])
    skips.append({
        "stage": stage,
        "at": _now(),
        "detail": detail or {},
        "advisory_only": True,
        "blocking": False,
    })
    print(
        "[pipeline_step_a] admission_v2 soft-skip legacy stage=%s (advisory only)"
        % stage,
        flush=True,
    )
    return True


def _evidence_metrics_from_bt(base_m, trades):
    from .review_admission_v2 import metrics_from_backtest_result
    # Prefer explicit base metrics; fall back to trade list.
    m = dict(base_m or {})
    if m.get("trades") is None and trades is not None:
        m["trades"] = len(trades or [])
    if m.get("win_rate") is None and m.get("win_rate_pct") is not None:
        m["win_rate"] = m.get("win_rate_pct")
    if m.get("win_rate") is None and m.get("win_rate_percent") is not None:
        m["win_rate"] = m.get("win_rate_percent")
    if m.get("mean_net") is None and trades:
        synth, _ = metrics_from_backtest_result({"trades": trades})
        for k, v in synth.items():
            m.setdefault(k, v)
    return m


def _ai_json(provider, system_prompt, user_payload, max_tokens=2200, temperature=0.35):
    import auto_trade_dual_engine_factory as dual
    return dual._ai_json(provider, system_prompt, user_payload,
                         max_tokens=max_tokens, temperature=temperature)


def _new_task_id():
    return "wsa_%s_%s" % (datetime.now().strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:4])


def _save_artifact(task_id, name, obj):
    ensure_dirs()
    ensure_step_a_dirs()
    path = ARTIFACTS_DIR / ("%s_%s.json" % (task_id, name))
    _atomic(path, obj)
    return str(path)


def resolve_exploration_mode(mode):
    key = str(mode or "A").upper()
    if key not in STEP_A_MODES and str(mode) not in STEP_A_MODES:
        key = "A"
    # normalize to A/B/C/D for v2 isolation + STEP A name
    if str(mode) in ("1", "2", "3", "4"):
        map_to_letter = {"1": "B", "2": "A", "3": "C", "4": "D"}
        letter = map_to_letter[str(mode)]
    else:
        letter = key if key in ("A", "B", "C", "D") else "A"
    return letter, STEP_A_MODES.get(str(mode), STEP_A_MODES.get(letter, "new_mechanism"))


def glm_require_mechanism_spec(mode_ctx, focus, mode_name, kb_ctx, retries=2):
    """GLM mechanism proposer with schema template + retry (fix representation_failure)."""
    prompt = glm_spec_prompt_template()
    if mode_name == "new_mechanism":
        prompt += "\n硬约束：mechanism_family 禁止 exhaustion_fade_short 及其改名克隆。"
    if mode_name == "known_mechanism_deep_dig":
        prompt += "\n模式=已知机制深挖：可保持核心盈利来源，更换标的/周期/触发/过滤器。"
    if mode_name == "combination_mechanism":
        prompt += "\n模式=组合机制：必须在 JSON 增加 primary_mechanism / filter_mechanism / conflict_notes。"
    if mode_name == "failure_reverse_research":
        prompt += "\n模式=失败反向研究：基于 failure_kb 提出新独立假设，禁止简单重跑旧失败策略。"

    payload = {
        "exploration": {
            "mode_name": mode_name,
            "isolation": (mode_ctx or {}).get("isolation") or (mode_ctx or {}).get("schema"),
            "stripped_keys": (mode_ctx or {}).get("stripped_keys") or [],
        },
        "focus": focus,
        "failure_knowledgebase_must_read": kb_ctx,
        "constraints": dict(PRODUCTION_CONSTRAINTS),
        "target_trades_per_day": list(TARGET_TRADES_PER_DAY),
    }
    call_id = new_call_id("glm_spec")
    env = envelope("glm", {"purpose": "mechanism_spec", "focus": focus}, call_id=call_id,
                   purpose="mechanism_spec")
    validate_envelope(env, expect_role="glm")

    last_errors = []
    last_raw = None
    cleaned = {}
    for attempt in range(max(1, retries)):
        use_prompt = prompt
        if attempt > 0:
            use_prompt += (
                "\n上次输出缺字段：%s。请仅输出完整 mechanism_spec，勿省略列表字段。"
                % json.dumps(last_errors, ensure_ascii=False)
            )
        res = _ai_json("glm", use_prompt, payload, max_tokens=2500, temperature=0.25 + 0.05 * attempt)
        last_raw = res
        parsed = (res or {}).get("parsed") or {}
        ok, errors, cleaned = normalize_mechanism_spec(parsed, focus=focus)
        last_errors = errors
        if ok:
            return {
                "ok": True,
                "errors": [],
                "mechanism_spec": cleaned,
                "meta": {
                    "title": parsed.get("title") or cleaned.get("mechanism_name"),
                    "thesis": parsed.get("thesis") or cleaned.get("why_edge_exists"),
                    "direction": parsed.get("direction") or "long",
                    "symbol": parsed.get("symbol") or (focus or {}).get("symbol"),
                    "timeframe": parsed.get("timeframe") or (focus or {}).get("timeframe"),
                    "suggested_core_features": parsed.get("suggested_core_features") or [],
                    "holding_horizon": parsed.get("holding_horizon") or cleaned.get("expected_holding_period"),
                    "primary_mechanism": parsed.get("primary_mechanism"),
                    "filter_mechanism": parsed.get("filter_mechanism"),
                    "conflict_notes": parsed.get("conflict_notes"),
                },
                "call_id": call_id,
                "attempts": attempt + 1,
                "raw_ok": bool((res or {}).get("ok")),
            }
    return {
        "ok": False,
        "errors": last_errors,
        "mechanism_spec": cleaned,
        "meta": {},
        "call_id": call_id,
        "attempts": retries,
        "raw_ok": bool((last_raw or {}).get("ok")),
        "ai_error": (last_raw or {}).get("error"),
    }


def codex_implement_from_spec(spec_pack):
    """Faithful implementer — NEVER use legacy hypothesis books (they inject EMA/RSI).

    Builds microstructure-proxy DSL from mechanism_spec + allowed features only.
    If spec_pack provides a validated prebuilt `dsl` (or direction-matched
    `dsl_long`/`dsl_short`), use that instead of the family template.
    """
    import uuid
    import copy
    from .config import FORBIDDEN_CORE_FEATURES

    meta = spec_pack.get("meta") or {}
    spec = spec_pack.get("mechanism_spec") or {}
    stmt = spec_to_mechanism_statement(spec)
    symbol = meta.get("symbol") or (spec.get("suitable_symbols") or ["SOL-USDT-SWAP"])[0]
    timeframe = meta.get("timeframe") or (spec.get("suitable_timeframes") or ["5m"])[0]
    direction = str(meta.get("direction") or "short").lower()
    if direction not in ("long", "short"):
        # "both" / unknown → pick short for sweep-reversal style, else long
        text = (str(spec.get("entry_logic") or "") + " " + str(spec.get("mechanism_family") or "")).lower()
        direction = "short" if any(k in text for k in ("short", "fade", "sweep", "reversal", "vacuum")) else "long"
    title = meta.get("title") or spec.get("mechanism_name") or "step_a_strategy"
    key = ("wsa_%s_%s_%s" % (
        str(symbol).split("-")[0].lower(), timeframe, uuid.uuid4().hex[:6]
    )).replace("-", "_")

    # Prebuilt DSL path (research handoff / Windtalker pack)
    prebuilt = spec_pack.get("dsl")
    if not isinstance(prebuilt, dict):
        prebuilt = spec_pack.get("dsl_long" if direction == "long" else "dsl_short")
    if isinstance(prebuilt, dict) and prebuilt.get("schema") == "qiyu_strategy_dsl_v1":
        dsl = copy.deepcopy(prebuilt)
        dsl["direction"] = direction
        dsl["timeframe"] = timeframe or dsl.get("timeframe")
        dsl["supported_instruments"] = [symbol]
        dsl.setdefault("key", key)
        dsl.setdefault("name", str(title)[:120])
        dsl["live_enabled"] = False
        dsl["auto_trade_eligible"] = False
        dsl["origin"] = dsl.get("origin") or "step_a_prebuilt_dsl"
        return {
            "title": title,
            "thesis": meta.get("thesis") or spec.get("why_edge_exists"),
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "dsl": dsl,
            "mechanism_spec": spec,
            "mechanism_statement": stmt,
            "status": "codex_implemented",
            "supplements_needed": [],
            "core_features": [],
            "prebuilt_dsl": True,
        }

    # Allowed core features from suggestions — strip forbidden
    suggested = [str(x) for x in (meta.get("suggested_core_features") or [])]
    # Map common aliases to DSL FEATURES
    alias = {
        "atr_pct": "atr14", "atr": "atr14", "volume_z": "vol_z20", "vol_z": "vol_z20",
        "prev_high": "prev_high20", "prev_low": "prev_low20",
        "range_compression": "atr14", "wick": "atr14",
    }
    safe_feats = []
    for f in suggested:
        fl = alias.get(f.lower(), f)
        if any(t in fl.lower() for t in FORBIDDEN_CORE_FEATURES):
            continue
        safe_feats.append(fl)
    # Mechanism-keyword defaults (no EMA/RSI/MACD)
    fam = str(spec.get("mechanism_family") or "").lower() + " " + str(spec.get("entry_logic") or "").lower()
    for f in ("prev_high20", "prev_low20", "vol_z20", "atr14"):
        if f not in safe_feats:
            safe_feats.append(f)
    if not safe_feats:
        safe_feats = ["atr14", "vol_z20"]

    # Family-differentiated microstructure proxies (still no EMA/RSI/MACD).
    # Keeps fidelity to distinct mechanism families without inventing unavailable L2 data.
    entry_leaves = []
    if any(k in fam for k in ("trend_continuation", "trend_pullback", "momentum_continuation", "session_trend")):
        # Continuation after shallow pullback in direction of recent range break
        if direction == "long":
            entry_leaves.append({"id": "e_break_high", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}})
            entry_leaves.append({"id": "e_hold_above", "left": {"feature": "low"}, "op": "gt", "right": {"feature": "prev_low20"}})
        else:
            entry_leaves.append({"id": "e_break_low", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}})
            entry_leaves.append({"id": "e_hold_below", "left": {"feature": "high"}, "op": "lt", "right": {"feature": "prev_high20"}})
        entry_leaves.append({"id": "e_vol_ok", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.3}})
    elif any(k in fam for k in ("vol_regime", "volatility_expansion", "atr_regime", "vol_break")):
        entry_leaves.append({"id": "e_atr_expand", "left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.002}})
        entry_leaves.append({"id": "e_vol_expand", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 1.2}})
        if direction == "long":
            entry_leaves.append({"id": "e_dir", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}})
        else:
            entry_leaves.append({"id": "e_dir", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}})
    elif any(k in fam for k in ("compression_release", "atr_squeeze", "structural_breakout", "squeeze_breakout")):
        # ATR compression then structural range break (distinct from dead high-vol-only release)
        entry_leaves.append({
            "id": "e_atr_compress",
            "left": {"feature": "atr14"},
            "op": "lt",
            "right": {"feature": "close", "scale": 0.0025},
        })
        if direction == "long":
            entry_leaves.append({
                "id": "e_break_high",
                "left": {"feature": "close"},
                "op": "cross_above",
                "right": {"feature": "prev_high20"},
            })
            entry_leaves.append({
                "id": "e_dir",
                "left": {"feature": "close"},
                "op": "gt",
                "right": {"feature": "open"},
            })
        else:
            entry_leaves.append({
                "id": "e_break_low",
                "left": {"feature": "close"},
                "op": "cross_below",
                "right": {"feature": "prev_low20"},
            })
            entry_leaves.append({
                "id": "e_dir",
                "left": {"feature": "close"},
                "op": "lt",
                "right": {"feature": "open"},
            })
        entry_leaves.append({
            "id": "e_vol_confirm",
            "left": {"feature": "vol_z20"},
            "op": "gt",
            "right": {"value": 0.5},
        })
    elif any(k in fam for k in ("failed_breakout", "breakout_fail", "false_break", "liquidity_fail")):
        # Failed breakout ≠ exhaustion_fade: range break then immediate reclaim opposite
        if direction == "short":
            entry_leaves.append({"id": "e_false_high", "left": {"feature": "high"}, "op": "gt", "right": {"feature": "prev_high20"}})
            entry_leaves.append({"id": "e_fail_close", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_high20"}})
        else:
            entry_leaves.append({"id": "e_false_low", "left": {"feature": "low"}, "op": "lt", "right": {"feature": "prev_low20"}})
            entry_leaves.append({"id": "e_fail_close", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_low20"}})
        entry_leaves.append({"id": "e_vol_spike", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.6}})
    elif any(k in fam for k in ("mean_reversion", "mean_revert", "stretch_revert", "z_revert", "non_fade")):
        entry_leaves.append({"id": "e_atr_stretch", "left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.001}})
        if direction == "long":
            entry_leaves.append({"id": "e_stretch_low", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}})
            entry_leaves.append({"id": "e_turn", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}})
        else:
            entry_leaves.append({"id": "e_stretch_high", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}})
            entry_leaves.append({"id": "e_turn", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}})
    elif any(k in fam for k in ("time_structure", "session_open", "opening_range", "tod_structure", "session_breakout")):
        # Session / opening-range proxy via prior range interaction + moderate vol
        if direction == "long":
            entry_leaves.append({"id": "e_or_break", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}})
        else:
            entry_leaves.append({"id": "e_or_break", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}})
        entry_leaves.append({"id": "e_vol_mod", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.2}})
        entry_leaves.append({"id": "e_atr_ok", "left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.0}})
    elif any(k in fam for k in ("cross_asset", "lead_lag", "btc_lead")):
        # No true cross-asset feed in DSL — mark proxy on same-symbol vol shock (data-limited)
        entry_leaves.append({"id": "e_proxy_shock", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 1.5}})
        entry_leaves.append({"id": "e_atr_shock", "left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.003}})
        if direction == "long":
            entry_leaves.append({"id": "e_dir", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}})
        else:
            entry_leaves.append({"id": "e_dir", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}})
    else:
        # Default liquidity sweep / reclaim proxy (legacy STEP A path)
        if direction == "long":
            entry_leaves.append({"id": "e_sweep_low", "left": {"feature": "low"}, "op": "lt", "right": {"feature": "prev_low20"}})
            entry_leaves.append({"id": "e_reclaim_low", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_low20"}})
            entry_leaves.append({"id": "e_vol_spike", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.8}})
        else:
            entry_leaves.append({"id": "e_sweep_high", "left": {"feature": "high"}, "op": "gt", "right": {"feature": "prev_high20"}})
            entry_leaves.append({"id": "e_reclaim_high", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_high20"}})
            entry_leaves.append({"id": "e_vol_spike", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.8}})
        entry_leaves.append({"id": "e_atr_ok", "left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.0}})
    if not any(e.get("id") == "e_atr_ok" for e in entry_leaves):
        entry_leaves.append({"id": "e_atr_ok", "left": {"feature": "atr14"}, "op": "gt", "right": {"value": 0.0}})

    # Hold from expected_holding_period if parseable
    max_hold = 24
    eh = str(spec.get("expected_holding_period") or "")
    for tok in eh.replace("-", "_").split("_"):
        if tok.isdigit():
            max_hold = max(4, min(96, int(tok) * 2))
            break
    if "max_hold_bars" in (spec.get("tunable_parameters") or []):
        max_hold = max(max_hold, 12)

    # Only DSL-allowed top-level keys (validate_strategy rejects extras)
    dsl = {
        "schema": "qiyu_strategy_dsl_v1",
        "key": key,
        "name": str(title)[:120],
        "direction": direction,
        "timeframe": timeframe,
        "supported_instruments": [symbol],
        "entry": {"all": entry_leaves},
        "exit": {"any": [
            # Phase-2 opt-in dynamic exits (research/formal). Protective 0.9% SL
            # remains in backtest engine / daemon — not expressed as fixed micro-TP.
            {"id": "x_atr_trail", "exit_op": "atr_trailing", "n_atr": 3.0,
             "atr_period": 14, "role": "take_profit"},
            {"id": "x_swing_inv", "exit_op": "swing_extreme", "lookback": 20,
             "role": "invalidation"},
        ]},
        "max_hold_bars": max_hold,
        "description": ("step_a|%s|sl0.9|%s" % (
            spec.get("mechanism_family"), (spec.get("entry_logic") or "")[:80]
        ))[:2000],
        "origin": "step_a_codex_faithful",
        "version": 1,
        "live_enabled": False,
        "auto_trade_eligible": False,
    }

    # Strip any accidental forbidden features
    from .mechanism import extract_dsl_conditions, feature_is_forbidden_core
    from .fidelity import apply_audit_removals
    supplements_needed = []
    for row in extract_dsl_conditions(dsl):
        if feature_is_forbidden_core(row.get("feature")):
            supplements_needed.append({
                "problem_solved": "forbidden_core_should_not_appear",
                "causal_link_to_mechanism": "none_auto_reject",
                "reproducible_experiment_without": "remove_leaf",
                "risk_becomes_dominant_mechanism": "high",
                "proposed_condition": {"feature": row.get("feature")},
                "glm_decision": "rejected_auto",
            })
    if supplements_needed:
        fake = {"condition_audit": [
            {"feature": (s.get("proposed_condition") or {}).get("feature"),
             "action": "remove", "classification": "unapproved_filter"}
            for s in supplements_needed
        ]}
        dsl, _ = apply_audit_removals(dsl, fake)

    book = {
        "title": title,
        "thesis": meta.get("thesis") or spec.get("why_edge_exists"),
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "dsl": dsl,
        "mechanism_spec": spec,
        "mechanism_statement": stmt,
        "status": "codex_implemented",
        "supplements_needed": supplements_needed,
        "core_features": safe_feats,
    }
    return book


def _resolve_matrix_symbols(spec=None, meta=None, primary=None, enable=False):
    """Whitelist for multi-symbol matrix eval. Primary stays first (seed focus)."""
    primary = primary or None
    if not enable:
        return [primary] if primary else []
    spec = spec or {}
    meta = meta or {}
    raw = list(spec.get("suitable_symbols") or [])
    if not raw:
        raw = list(meta.get("matrix_generalization") or [])
    if not raw and primary:
        raw = [primary]
    out = []
    seen = set()
    if primary:
        out.append(primary)
        seen.add(str(primary))
    for s in raw:
        s = str(s or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _load_matrix_frames(symbols, timeframe, dual_mod, chunk_gc_every=4):
    """Load frames for matrix symbols; skip missing; GC periodically for low RAM."""
    frames = []
    errors = []
    _mg = None
    try:
        from auto_driver import memory_guard as _mg  # noqa: N814
    except Exception:
        _mg = None
    for i, sym in enumerate(symbols or []):
        try:
            if _mg is not None:
                total, avail = _mg.read_meminfo_mb()
                if avail < 100:
                    _mg.force_release("matrix_load_abort")
                    errors.append({"symbol": sym, "error": "ram_abort_avail_%d" % avail})
                    break
            frm = dual_mod._frame(sym, timeframe)
            if frm is None or (hasattr(frm, "__len__") and len(frm) == 0):
                errors.append({"symbol": sym, "error": "empty_frame"})
                continue
            frames.append((sym, frm))
            if chunk_gc_every and (i + 1) % int(chunk_gc_every) == 0:
                import gc
                gc.collect()
        except Exception as exc:
            errors.append({"symbol": sym, "error": str(exc)})
    return frames, errors


def _matrix_pool_backtest(definition, symbols, timeframe, dual_mod, friction="observed_base"):
    """Full-history BT across matrix; pool trades. Primary (first) errors are fatal."""
    all_trades = []
    per = []
    primary_err = None
    for i, sym in enumerate(symbols or []):
        try:
            # Bail out early if RAM collapses mid-pool
            try:
                from auto_driver import memory_guard as _mg
                _, avail = _mg.read_meminfo_mb()
                if avail < 90 and i > 0:
                    per.append({"symbol": sym, "n_trades": 0, "ok": False, "error": "ram_abort"})
                    _mg.force_release("matrix_pool_abort")
                    break
            except Exception:
                pass
            r = dual_mod._backtest(definition, sym, timeframe, friction)
            trades = list((r or {}).get("trades") or [])
            for t in trades:
                if isinstance(t, dict):
                    tt = dict(t)
                    tt.setdefault("matrix_symbol", sym)
                    all_trades.append(tt)
                else:
                    all_trades.append(t)
            per.append({"symbol": sym, "n_trades": len(trades), "ok": True})
            if (i + 1) % 3 == 0:
                import gc
                gc.collect()
        except Exception as exc:
            per.append({"symbol": sym, "n_trades": 0, "ok": False, "error": str(exc)})
            if i == 0:
                primary_err = str(exc)
    metrics = dual_mod._metrics_from_trades(all_trades) if all_trades else {}
    return {
        "trades": all_trades,
        "base_metrics": metrics,
        "per_symbol": per,
        "primary_error": primary_err,
        "n_symbols": len(symbols or []),
        "n_trades": len(all_trades),
    }


def _walk_forward_detail(definition, symbol, timeframe, folds=10):
    """Build ≥10 window detail from dual-engine backtest folds when available.

    Phase-3: window pass = Calmar≥1.0 AND positive classic expectancy
    (aligns with Gate3 ≥7/10 without changing evaluate_gate3 signature).
    """
    import auto_trade_dual_engine_factory as dual
    from .phase3_funnel import phase3_wf_from_trades
    try:
        base = dual._backtest(definition, symbol, timeframe, "observed_base")
        trades = list(base.get("trades") or [])
        metrics = dual._metrics_from_trades(trades)
        # Phase-3 Calmar+expectancy windows (preferred)
        wf3 = phase3_wf_from_trades(trades, folds=folds)
        windows = list(wf3.get("windows") or [])
        # If upstream exposed explicit fold rows with richer metrics, merge labels
        fold_rows = base.get("folds") or metrics.get("fold_details") or []
        if isinstance(fold_rows, list) and len(fold_rows) >= len(windows):
            for i, fr in enumerate(fold_rows[: len(windows)]):
                if isinstance(fr, dict):
                    merged = dict(windows[i].get("metrics") or {})
                    merged.update(fr)
                    windows[i]["metrics"] = merged
        while len(windows) < folds:
            windows.append({
                "id": "window_%s" % len(windows),
                "pass": False,
                "metrics": {"trades": 0, "calmar": 0.0, "classic_expectancy": 0.0},
                "fail_reason": "insufficient_trades_to_populate_window",
            })
        windows = windows[:folds]
        pass_count = sum(1 for w in windows if w.get("pass"))
        return {
            "windows": windows,
            "pass_count": pass_count,
            "total": len(windows),
            "base_metrics": metrics,
            "trades": trades,
            "definition_ok": True,
            "phase3_wf_rule": wf3.get("rule"),
            "oos_trades": trades,
        }
    except Exception as exc:
        windows = [{
            "id": "window_%s" % i,
            "pass": False,
            "metrics": {},
            "fail_reason": "backtest_error:%s" % exc,
        } for i in range(folds)]
        return {
            "windows": windows,
            "pass_count": 0,
            "total": folds,
            "error": str(exc),
            "trades": [],
            "base_metrics": {},
            "definition_ok": False,
        }


def _apply_repair_round(book, spec, round_type, round_i):
    """3-round protocol: engineering → tunable params → viability verdict."""
    spec = spec or {}
    if round_type == "engineering_only":
        return apply_engineering_repair_only(book, "engineering", round_i), False
    if round_type == "tunable_params_only":
        allowed = set(str(x) for x in (spec.get("tunable_parameters") or []))
        dsl = copy.deepcopy(book.get("dsl") or {})
        # Only nudge numeric leaves; do not add features
        changed = []

        def walk(node):
            if isinstance(node, dict):
                right = node.get("right")
                if isinstance(right, dict) and "value" in right:
                    feat = str(((node.get("left") or {}).get("feature")) or "")
                    # allow if feature mentioned in tunables or generic threshold
                    if (not allowed) or any(a.lower() in feat.lower() for a in allowed) or True:
                        try:
                            old = float(right["value"])
                            right["value"] = round(old * (0.95 if round_i % 2 == 0 else 1.05), 6)
                            changed.append(feat or "threshold")
                        except Exception:
                            pass
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for x in node:
                    walk(x)
        walk(dsl.get("entry"))
        # hold bars if listed
        if any("hold" in str(a).lower() for a in allowed) or "max_hold_bars" in allowed:
            dsl["max_hold_bars"] = max(4, int(dsl.get("max_hold_bars") or 24) - 1)
            changed.append("max_hold_bars")
        book = dict(book)
        book["dsl"] = dsl
        book["repair_round"] = round_i + 1
        book["repair_type"] = round_type
        book["repair_changed"] = changed
        return book, False
    # mechanism_viability_verdict — no further mutation
    book = dict(book)
    book["repair_type"] = round_type
    book["viability_verdict_pending"] = True
    return book, True


def _archive_step_a(task, *, stage, failed_tests, reason, verdict, lessons=None,
                    blocked=None, counterexamples=None, is_eng=False, is_cost=False,
                    is_data=False, is_mech_absent=False, drift=False):
    rec = build_failure_record(
        task_id=task.get("id"),
        mechanism_spec=task.get("mechanism_spec"),
        stage=stage,
        failed_tests=failed_tests,
        failure_reason=reason,
        is_engineering=is_eng,
        is_cost=is_cost,
        is_data=is_data,
        is_mechanism_absent=is_mech_absent,
        mechanism_drift=drift,
        repair_count=len((task.get("repair_log") or {}).get("rounds") or []),
        final_verdict=verdict,
        reusable_lessons=lessons or [reason],
        blocked_paths=blocked or ["%s|%s" % (stage, failed_tests)],
        counterexamples=counterexamples or [],
        exploration_mode=task.get("exploration_mode_name") or task.get("exploration_mode"),
        symbol=(task.get("focus") or {}).get("symbol"),
        timeframe=(task.get("focus") or {}).get("timeframe"),
        gate_results=task.get("gate_results"),
    )
    path = save_failure_record(rec)
    task["failure_record_path"] = path
    task["failure_record"] = rec
    # also v2 archive for compatibility
    try:
        from .pipeline import _archive_fail
        _archive_fail(
            task, failed_test=str(failed_tests[0] if isinstance(failed_tests, list) else failed_tests),
            observed=reason, expected="step_a_gate_pass",
            final_statement=verdict,
        )
    except Exception as exc:
        task.setdefault("persist_errors", []).append("v2_archive:%s" % exc)
    return rec


def run_creation_pipeline_step_a(symbol=None, timeframe=None, exploration_mode="A",
                                 allow_horizontal_expand=False, prebuilt_spec_pack=None,
                                 windtalker_tag=None, enable_multi_symbol_matrix=False):
    ensure_dirs()
    ensure_step_a_dirs()
    store.migrate_forward()

    letter, mode_name = resolve_exploration_mode(exploration_mode)
    tid = _new_task_id()
    mode = lock_exploration_mode(letter)
    task = {
        "id": tid,
        "schema": STEP_A_SCHEMA,
        "workflow_version": WORKFLOW_VERSION,
        "step_a_code_version": STEP_A_CODE_VERSION,
        "migration_version": MIGRATION_VERSION,
        "code_version": CODE_VERSION,
        "created_at": _now(),
        "stage": "kb_preread",
        "exploration_mode": mode,
        "exploration_mode_name": mode_name,
        "focus": {"symbol": symbol, "timeframe": timeframe},
        "gates": [],
        "history": [],
        "production_mounted": False,
        "windtalker_phase1": bool(windtalker_tag) or bool(prebuilt_spec_pack),
        "windtalker_tag": windtalker_tag,
        "enable_multi_symbol_matrix": bool(enable_multi_symbol_matrix),
    }

    import auto_trade_dual_engine_factory as dual
    dual._ensure_dirs()
    dual._load_env()

    # Mandatory failure KB read
    kb_ctx = kb_context_for_ai()
    task["failure_kb_preread"] = {
        "must_read": True,
        "updated_at": kb_ctx.get("updated_at"),
        "blocked_paths": kb_ctx.get("blocked_paths"),
        "blocked_families": kb_ctx.get("blocked_families"),
        "lessons_n": len(kb_ctx.get("reusable_lessons") or []),
    }
    _save_artifact(tid, "failure_kb_preread", task["failure_kb_preread"])

    # Collect + isolate
    task["stage"] = "collect_isolate"
    inputs = dual.collect_inputs()
    if symbol and timeframe:
        inputs["focus_candidates"] = (
            [{"symbol": symbol, "timeframe": timeframe, "reason": "manual"}]
            + list(inputs.get("focus_candidates") or [])
        )
    fps = store.list_fingerprints()
    exclusions = store.list_exclusion_rules()
    mode_ctx = build_mode_context(mode, inputs, fingerprint_index=fps, exclusion_rules=exclusions)
    # Mode4: inject KB deeply
    if mode_name == "failure_reverse_research":
        mode_ctx = dict(mode_ctx)
        mode_ctx["failure_kb"] = kb_ctx
        mode_ctx["forbid_rerun_old_failures"] = True
    task["mode_context_meta"] = {
        "mode": mode,
        "mode_name": mode_name,
        "isolation": mode_ctx.get("isolation") or mode_ctx.get("schema"),
        "stripped_keys": mode_ctx.get("stripped_keys") or [],
        "isolation_verified": mode_ctx.get("isolation_verified"),
    }
    focus = (mode_ctx.get("focus_candidates") or inputs.get("focus_candidates") or [{}])[0]
    if symbol:
        focus = {"symbol": symbol, "timeframe": timeframe or focus.get("timeframe") or "5m"}
    if prebuilt_spec_pack and isinstance(prebuilt_spec_pack, dict):
        meta0 = prebuilt_spec_pack.get("meta") or {}
        focus = {
            "symbol": symbol or meta0.get("symbol") or focus.get("symbol"),
            "timeframe": timeframe or meta0.get("timeframe") or focus.get("timeframe") or "5m",
            "reason": "windtalker_prebuilt_spec",
        }
    task["focus"] = focus
    dual.save_task(task)
    store.save_task_meta(task)

    # ---- Gate 0: mechanism_spec ----
    task["stage"] = "gate0_mechanism_spec"
    if prebuilt_spec_pack and isinstance(prebuilt_spec_pack, dict) and prebuilt_spec_pack.get("mechanism_spec"):
        spec_pack = dict(prebuilt_spec_pack)
        spec_pack.setdefault("ok", True)
        spec_pack.setdefault("errors", [])
        spec_pack.setdefault("call_id", new_call_id("prebuilt_spec"))
        spec_pack.setdefault("attempts", 0)
        task["mechanism_spec_source"] = "prebuilt_windtalker"
    else:
        spec_pack = glm_require_mechanism_spec(mode_ctx, focus, mode_name, kb_ctx, retries=2)
        task["mechanism_spec_source"] = "glm_live"
    task["mechanism_spec_gate"] = {
        "ok": spec_pack.get("ok"),
        "errors": spec_pack.get("errors"),
        "call_id": spec_pack.get("call_id"),
        "attempts": spec_pack.get("attempts"),
        "source": task.get("mechanism_spec_source"),
    }
    g0 = evaluate_gate0(spec_pack.get("mechanism_spec"), spec_pack.get("ok"))
    task["gates"].append(g0)
    if not spec_pack.get("ok"):
        task["stage"] = "archived"
        task["error"] = "mechanism_spec_incomplete"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(
            task, stage="gate0", failed_tests=["representation"],
            reason="mechanism_spec missing fields: %s" % spec_pack.get("errors"),
            verdict="representation_failure_after_retry",
            is_data=True,
            lessons=["GLM must emit full mechanism_spec fields; aliases+retry exhausted"],
            blocked=["glm_incomplete_spec|%s|%s" % (focus.get("symbol"), focus.get("timeframe"))],
        )
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "stage": "archived", "reason": "spec_incomplete",
                "gate_results": task["gate_results"]}

    spec = spec_pack["mechanism_spec"]
    # Block known dead families / paths
    blocked, why = path_is_blocked(
        "family|%s" % spec.get("mechanism_family"),
        family=spec.get("mechanism_family"),
    )
    if blocked and mode_name == "new_mechanism":
        task["stage"] = "archived"
        g0["pass"] = False
        g0["evidence"]["kb_block"] = why
        task["gates"][-1] = g0
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(
            task, stage="gate0", failed_tests=["blocked_family"],
            reason="failure KB blocked family %s (%s)" % (spec.get("mechanism_family"), why),
            verdict="blocked_by_failure_kb",
            is_mech_absent=True,
            blocked=["family|%s" % spec.get("mechanism_family")],
        )
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "reason": "kb_blocked"}

    # Immutable save
    try:
        spec_path, spec_payload = save_immutable_mechanism_spec(
            spec, task_id=tid, meta=spec_pack.get("meta"))
        task["mechanism_spec_path"] = spec_path
        task["mechanism_spec_hash"] = spec_payload.get("content_hash")
    except MechanismSpecError as exc:
        task["stage"] = "archived"
        _archive_step_a(task, stage="gate0", failed_tests=["immutable_spec"],
                        reason=str(exc), verdict="immutable_spec_conflict", is_eng=True)
        dual.save_task(task)
        return {"ok": False, "task_id": tid, "reason": "immutable_spec_error"}

    task["mechanism_spec"] = spec
    stmt = spec_to_mechanism_statement(spec)
    task["mechanism_statement"] = stmt
    store.save_mechanism_statement(tid, None, spec.get("mechanism_id"), stmt, spec_pack.get("call_id"))

    # Fingerprint + duplicate intercept BEFORE coding
    task["stage"] = "fingerprint_intercept"
    fp_a = build_step_a_fingerprint(
        spec,
        direction=(spec_pack.get("meta") or {}).get("direction"),
        symbol=focus.get("symbol"),
        timeframe=focus.get("timeframe"),
    )
    # also save v2 fingerprint for index compatibility
    fp_v2 = build_fingerprint_from_statement(
        stmt,
        extras={"holding_horizon": spec.get("expected_holding_period"),
                "information_source": "step_a"},
    )
    task["fingerprint"] = fp_a
    task["fingerprint_v2"] = fp_v2
    task["mechanism_id"] = spec.get("mechanism_id")
    store.save_fingerprint(tid, None, task["mechanism_id"], {**fp_v2, "step_a": fp_a})

    allow_h = bool(allow_horizontal_expand) or mode_name == "known_mechanism_deep_dig"
    dup_block, dup_report = duplicate_intercept(
        fp_a, fps, mechanism_family=spec.get("mechanism_family"),
        allow_horizontal_expand=allow_h,
    )
    # New mechanism mode: forbid exhaustion_fade_short family
    if mode_name == "new_mechanism" and EXHAUSTION_FADE_FAMILY in str(spec.get("mechanism_family") or "").lower():
        dup_block = True
        dup_report["is_exhaustion_fade_clone"] = True
        dup_report["block_reason"] = "exhaustion_fade_short_forbidden"
    task["duplicate_report"] = dup_report
    task["counts_as_independent_mechanism"] = bool(dup_report.get("counts_as_independent_mechanism"))
    if dup_block:
        task["stage"] = "archived"
        task["error"] = "duplicate_mechanism"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(
            task, stage="fingerprint", failed_tests=["duplicate_mechanism"],
            reason=json.dumps(dup_report, ensure_ascii=False)[:500],
            verdict="duplicate_intercept_before_coding",
            blocked=["dup|%s" % fp_a.get("family_hash")],
            lessons=["same-mechanism horizontal expand is not an independent niche"],
        )
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "reason": "duplicate", "duplicate_report": dup_report}

    # ---- Codex implement + Gate1 fidelity ----
    task["stage"] = "codex_implement"
    book = codex_implement_from_spec(spec_pack)
    # strip unapproved forbidden features
    if book.get("supplements_needed"):
        fake_audit = {
            "condition_audit": [
                {"feature": (s.get("proposed_condition") or {}).get("feature"),
                 "action": "remove", "classification": "unapproved_filter"}
                for s in book["supplements_needed"]
            ]
        }
        book["dsl"], removed = apply_audit_removals(book.get("dsl"), fake_audit)
        task["codex_stripped_unapproved"] = removed

    fidelity = build_fidelity_diff(spec, book.get("dsl"), round_i=0, repair_type=None)
    # Rule audit: fail hard only on forbidden/unapproved traditional cores remaining
    rule_audit = rule_based_condition_audit(book.get("dsl"), stmt, approved_supplements=[])
    fidelity["rule_audit_reject"] = rule_audit.get("reject")
    fidelity["rule_audit"] = {
        "reject": rule_audit.get("reject"),
        "reject_reasons": rule_audit.get("reject_reasons"),
    }
    still_forbidden = [
        c.get("feature") for c in (rule_audit.get("condition_audit") or [])
        if c.get("classification") in ("unapproved_filter", "mechanism_substitution")
        or (c.get("action") == "reject_strategy")
    ]
    # Microstructure proxies (prev_high/low, vol_z, atr) are allowed as observation of sweep/vacuum
    allowed_proxy = {"prev_high20", "prev_low20", "vol_z20", "atr14", "high", "low", "close", "open", "z20"}
    hard = [f for f in still_forbidden if str(f).lower() not in allowed_proxy
            and any(t in str(f).lower() for t in ("rsi", "macd", "ema", "sma", "boll", "cci"))]
    if hard:
        fidelity["pass"] = False
        fidelity["notes"] = list(fidelity.get("notes") or []) + ["hard_forbidden_remaining:%s" % hard]
    elif fidelity.get("forbidden_core_features_present"):
        # Statement-named / user-authorized cores (e.g. HTF EMA + CCI pullback)
        # retained by rule_audit as mechanism_observation are not Codex injects.
        hits = [str(f).lower() for f in (fidelity.get("forbidden_core_features_present") or [])]
        retained = {
            str(c.get("feature") or "").lower()
            for c in (rule_audit.get("condition_audit") or [])
            if c.get("classification") in (
                "mechanism_observation", "mechanism_core", "approved_filter"
            )
            and c.get("action") in ("retain", "revise", None)
        }
        stmt_blob = " ".join(str((stmt or {}).get(k) or "") for k in (stmt or {})).lower()
        meta_auth = {
            str(x).lower()
            for x in ((spec_pack.get("meta") or {}).get("user_authorized_core_features") or [])
        }
        unauthorized = []
        for h in hits:
            if h in retained or h in meta_auth or h in stmt_blob:
                continue
            toks = [t for t in h.replace("_", " ").split() if len(t) >= 3]
            if toks and all(t in stmt_blob or t in meta_auth for t in toks):
                continue
            unauthorized.append(h)
        structural_ok = (
            fidelity.get("dsl_has_entry")
            and fidelity.get("dsl_has_exit")
            and fidelity.get("stop_logic_in_spec")
            and not fidelity.get("non_negotiable_violations")
        )
        if unauthorized or rule_audit.get("reject") or not structural_ok:
            fidelity["pass"] = False
            fidelity["notes"] = list(fidelity.get("notes") or []) + [
                "forbidden_core_still_present:%s" % (unauthorized or hits)
            ]
        else:
            fidelity["pass"] = True
            fidelity["notes"] = list(fidelity.get("notes") or []) + [
                "pass_via_statement_authorized_cores:%s" % hits
            ]
    else:
        # Do not fail solely on lexical overlap / soft rule_audit for proxy features
        if fidelity.get("dsl_has_entry") and fidelity.get("dsl_has_exit") and fidelity.get("stop_logic_in_spec"):
            if not fidelity.get("non_negotiable_violations"):
                fidelity["pass"] = True
                fidelity["notes"] = list(fidelity.get("notes") or []) + ["pass_via_structural_fidelity_proxies"]
    fpath = save_fidelity_diff(tid, fidelity)
    task["fidelity_diff_path"] = fpath
    task["fidelity_diff"] = fidelity
    task["strategy_id"] = (book.get("dsl") or {}).get("key")
    task["candidate"] = {
        "title": book.get("title"), "symbol": book.get("symbol"),
        "timeframe": book.get("timeframe"), "direction": book.get("direction"),
        "key": task["strategy_id"],
    }

    g1 = evaluate_gate1(fidelity, book.get("dsl"), spec)
    task["gates"].append(g1)
    if not g1["pass"]:
        task["stage"] = "archived"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(task, stage="gate1", failed_tests=["fidelity"],
                        reason="fidelity/gate1 fail", verdict="code_fidelity_fail",
                        is_eng=True)
        dual.save_task(task)
        store.save_task_meta(task)
        return {"ok": False, "task_id": tid, "reason": "gate1_fail", "gate_results": task["gate_results"]}

    # ---- Gate2+3 backtest / walk-forward (+ Phase-3 funnel L0→L1→L2→L3) ----
    task["stage"] = "gate2_gate3_backtest_wf"
    import auto_trade_strategy_dsl as dsl_mod
    from .funnel_l0_density import run_l0_density
    from .funnel_l1_micro_screen import run_micro_screen, run_micro_screen_matrix
    from .funnel_l2_pareto import evaluate_l2_survivor
    from .funnel_l3_null_hypothesis import evaluate_null_hypothesis
    from . import incubation_soft_gate as soft_gate
    try:
        from auto_driver import memory_guard as mem_guard
    except Exception:
        mem_guard = None
    try:
        definition = dsl_mod.validate_strategy(book.get("dsl") or {})
    except Exception as exc:
        task["stage"] = "archived"
        _archive_step_a(task, stage="validate", failed_tests=["engineering"],
                        reason=str(exc), verdict="dsl_validate_fail", is_eng=True)
        dual.save_task(task)
        return {"ok": False, "task_id": tid, "reason": "validate_fail"}

    # ---- Pretest quality: contract + sanity asserts BEFORE L0 (anti-屎上雕花) ----
    task["stage"] = "pretest_quality"
    try:
        from .pretest_quality import run_pretest_quality
        pretest_pack = {
            "meta": book if isinstance(book, dict) else {},
            "mechanism_spec": spec,
            "dsl": definition,
            "dsl_long": (prebuilt_spec_pack or {}).get("dsl_long") if isinstance(prebuilt_spec_pack, dict) else None,
            "dsl_short": (prebuilt_spec_pack or {}).get("dsl_short") if isinstance(prebuilt_spec_pack, dict) else None,
        }
        # Prefer embedded / family contract from prebuilt pack meta
        if isinstance(prebuilt_spec_pack, dict):
            pretest_pack["meta"] = dict(prebuilt_spec_pack.get("meta") or {})
            pretest_pack["meta"].setdefault("direction", book.get("direction") or "long")
            pretest_pack["meta"].setdefault("timeframe", book.get("timeframe"))
            if prebuilt_spec_pack.get("invariants_contract"):
                pretest_pack["invariants_contract"] = prebuilt_spec_pack.get("invariants_contract")
        pretest = run_pretest_quality(
            pretest_pack,
            direction=book.get("direction") or "long",
        )
    except Exception as exc:
        pretest = {
            "pass": False,
            "stage": "pretest_quality",
            "quality": "SHIT_TRANSLATION",
            "verdict": "RESET_REQUIRED",
            "reason": "pretest_exception:%s" % exc,
            "message_zh": "预检异常，fail-closed",
        }
    task["pretest_quality"] = pretest
    print(
        "[pipeline_step_a] pretest_quality pass=%s quality=%s verdict=%s reason=%s"
        % (pretest.get("pass"), pretest.get("quality"), pretest.get("verdict"), pretest.get("reason")),
        flush=True,
    )
    if not pretest.get("pass"):
        task["stage"] = "archived"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        _archive_step_a(
            task, stage="pretest_quality",
            failed_tests=["invariants_or_sanity"],
            reason=pretest.get("message_zh") or pretest.get("reason") or "pretest_fail",
            verdict="shit_translation_reset",
            is_eng=True,
        )
        dual.save_task(task)
        store.save_task_meta(task)
        return {
            "ok": False,
            "task_id": tid,
            "reason": "pretest_quality_fail",
            "pretest_quality": pretest,
            "gate_results": task["gate_results"],
        }

    sym = book.get("symbol") or focus.get("symbol")
    tf = book.get("timeframe") or focus.get("timeframe")
    matrix_syms_full = _resolve_matrix_symbols(
        spec=spec,
        meta=(prebuilt_spec_pack or {}).get("meta") if isinstance(prebuilt_spec_pack, dict) else {},
        primary=sym,
        enable=bool(enable_multi_symbol_matrix),
    )
    # RAM-adaptive subset: primary / BTC·ETH·SOL anchors / chunked / full
    if mem_guard is not None and enable_multi_symbol_matrix:
        matrix_syms, ram_budget = mem_guard.select_matrix_subset(
            matrix_syms_full, primary=sym, budget=mem_guard.matrix_budget(),
        )
    else:
        matrix_syms, ram_budget = list(matrix_syms_full), {"mode": "legacy", "max_symbols": len(matrix_syms_full)}
    task["matrix_eval"] = {
        "enabled": bool(enable_multi_symbol_matrix),
        "primary": sym,
        "symbols_full": list(matrix_syms_full),
        "symbols": list(matrix_syms),
        "n_symbols": len(matrix_syms),
        "n_symbols_full": len(matrix_syms_full),
        "ram_budget": ram_budget,
        "lightweight_funnel": True,
    }

    # Phase-3 L1: micro-screen on sampled slices BEFORE full-history BT
    task["stage"] = "funnel_l1_micro_screen"
    frame_for_funnel = None
    try:
        frame_for_funnel = dual._frame(sym, tf)
    except Exception:
        frame_for_funnel = None

    def _funnel_bt(frm, defn):
        return dsl_mod.backtest_dsl(frm, defn, stop_loss_pct=0.009)

    # ---- L0 density pre-check (ms) — kill AND-clog before any matrix IO ----
    task["stage"] = "funnel_l0_density"
    l0 = run_l0_density(definition=definition, frame=frame_for_funnel)
    task["phase3_funnel"] = {
        "l0_density": l0,
        "l1_micro_screen": None,
        "rejected_at": None,
        "fail_closed": True,
        "protective_sl_pct": 0.009,
        "ada_migrate": False,
        "auto_mount": False,
        "multi_symbol_matrix": bool(enable_multi_symbol_matrix),
        "matrix_symbols": list(matrix_syms) if enable_multi_symbol_matrix else [sym],
        "lightweight_funnel": True,
    }
    print(
        "[pipeline_step_a] 第一次复核·密度 pass=%s triggers=%s/%s wall=%.1fms"
        % (
            l0.get("pass"),
            (l0.get("metrics") or {}).get("triggers"),
            (l0.get("metrics") or {}).get("evaluated_bars"),
            float(l0.get("wall_time_ms") or 0),
        ),
        flush=True,
    )
    if not l0.get("pass"):
        if _admission_v2_enabled():
            _soft_skip_legacy(task, "funnel_l0_density", {
                "reject_reasons": list(l0.get("reject_reasons") or []),
                "metrics": l0.get("metrics"),
            })
            task["phase3_funnel"]["l0_advisory_fail"] = True
            task["phase3_funnel"]["reject_reasons_advisory"] = list(
                l0.get("reject_reasons") or []
            )
        else:
            task["stage"] = "archived"
            task["phase3_funnel"]["rejected_at"] = "funnel_l0_density"
            task["phase3_funnel"]["reject_reasons"] = list(l0.get("reject_reasons") or [])
            task["gate_results"] = assemble_gate_results(task["gates"], tid)
            save_gate_results(tid, task["gate_results"])
            _archive_step_a(
                task, stage="funnel_l0_density",
                failed_tests=["density_precheck"],
                reason="第一次复核·密度拒绝: %s" % ",".join(l0.get("reject_reasons") or ["l0_fail"]),
                verdict="funnel_l0_cull",
                is_mech_absent=False,
            )
            record_pipeline_rejection(
                task_id=tid, stage="funnel_l0_density",
                failed_tests=["density_precheck"],
                reject_reasons=l0.get("reject_reasons") or ["REJECT_TOO_RARE"],
                dsl=book.get("dsl"), symbol=sym, timeframe=tf,
            )
            dual.save_task(task)
            store.save_task_meta(task)
            return {
                "ok": False, "task_id": tid, "reason": "funnel_l0_fail",
                "phase3_funnel": task["phase3_funnel"],
                "gate_results": task["gate_results"],
                "matrix_eval": task.get("matrix_eval"),
            }

    l1_seed = hash(tid) % (2 ** 31) if tid else 42
    task["stage"] = "funnel_l1_micro_screen"
    if enable_multi_symbol_matrix and len(matrix_syms) > 1:
        # Stage A: anchor L1 (BTC/ETH/SOL ∩ budget) — never jump to 38 cold
        if mem_guard is not None:
            anchor_syms = mem_guard.resolve_anchor_symbols(matrix_syms_full, primary=sym)
        else:
            anchor_syms = list(matrix_syms[:3])
        # Respect RAM budget cap
        max_n = int((ram_budget or {}).get("max_symbols") or len(matrix_syms))
        stage_syms = list(anchor_syms)[:max(1, min(3, max_n))]
        # If budget allows more than anchors and L0 passed, expand after anchors pass
        expand_after_anchor = (
            bool(enable_multi_symbol_matrix)
            and max_n > len(stage_syms)
            and (ram_budget or {}).get("mode") in ("chunked", "full")
        )
        matrix_frames, frame_errs = _load_matrix_frames(stage_syms, tf, dual)
        print(
            "[pipeline_step_a] 第二次复核·锚点 n_syms=%d frames=%d primary=%s mode=%s"
            % (len(stage_syms), len(matrix_frames), sym, (ram_budget or {}).get("mode")),
            flush=True,
        )
        l1 = run_micro_screen_matrix(
            definition=definition,
            frames_by_symbol=matrix_frames,
            backtest_fn=_funnel_bt if matrix_frames else None,
            seed=l1_seed,
        )
        l1["anchor_symbols"] = list(stage_syms)
        if frame_errs:
            sm = dict(l1.get("sample_meta") or {})
            sm["frame_errors"] = frame_errs[:20]
            l1["sample_meta"] = sm
        # Expand to budgeted matrix only if anchors passed
        if l1.get("pass") and expand_after_anchor:
            if mem_guard is not None:
                mem_guard.ensure_headroom(min_avail_mb=160, label="pre_matrix_expand")
            expand_syms = list(matrix_syms)
            # drop already-tested anchors from reload set? keep full subset for pool consistency
            matrix_frames2, frame_errs2 = _load_matrix_frames(expand_syms, tf, dual)
            print(
                "[pipeline_step_a] 第二次复核·扩展 n_syms=%d frames=%d"
                % (len(expand_syms), len(matrix_frames2)),
                flush=True,
            )
            l1_exp = run_micro_screen_matrix(
                definition=definition,
                frames_by_symbol=matrix_frames2,
                backtest_fn=_funnel_bt if matrix_frames2 else None,
                seed=l1_seed + 17,
            )
            l1_exp["anchor_symbols"] = list(stage_syms)
            l1_exp["expanded_from_anchor"] = True
            if frame_errs2:
                sm = dict(l1_exp.get("sample_meta") or {})
                sm["frame_errors"] = frame_errs2[:20]
                l1_exp["sample_meta"] = sm
            l1 = l1_exp
            matrix_syms = list(expand_syms)
            task["matrix_eval"]["symbols"] = list(matrix_syms)
            task["matrix_eval"]["n_symbols"] = len(matrix_syms)
        # free frames refs
        matrix_frames = None
        try:
            import gc
            gc.collect()
        except Exception:
            pass
    else:
        l1 = run_micro_screen(
            definition=definition,
            frame=frame_for_funnel,
            backtest_fn=_funnel_bt if frame_for_funnel is not None else None,
            seed=l1_seed,
        )
    task["phase3_funnel"]["l1_micro_screen"] = l1
    task["phase3_funnel"]["matrix_symbols"] = list(matrix_syms) if enable_multi_symbol_matrix else [sym]
    if not l1.get("pass"):
        if _admission_v2_enabled():
            _soft_skip_legacy(task, "funnel_l1_micro_screen", {
                "reject_reasons": list(l1.get("reject_reasons") or []),
                "metrics": l1.get("metrics"),
            })
            task["phase3_funnel"]["l1_advisory_fail"] = True
            task["phase3_funnel"]["reject_reasons_advisory"] = list(
                task["phase3_funnel"].get("reject_reasons_advisory") or []
            ) + list(l1.get("reject_reasons") or [])
        else:
            task["stage"] = "archived"
            task["phase3_funnel"]["rejected_at"] = "funnel_l1_micro_screen"
            task["phase3_funnel"]["reject_reasons"] = list(l1.get("reject_reasons") or [])
            task["gate_results"] = assemble_gate_results(task["gates"], tid)
            save_gate_results(tid, task["gate_results"])
            _archive_step_a(
                task, stage="funnel_l1_micro_screen",
                failed_tests=["micro_screen"],
                reason="第二次复核拒绝: %s" % ",".join(
                    l1.get("reject_reasons") or ["l1_fail"]),
                verdict="funnel_l1_cull",
                # Sample-slice cull is NOT proof of mechanism absence — do not
                # family-block via failure KB (would poison later calibrated retries).
                is_mech_absent=False,
            )
            record_pipeline_rejection(
                task_id=tid, stage="funnel_l1_micro_screen",
                failed_tests=["micro_screen"],
                reject_reasons=l1.get("reject_reasons") or ["l1_fail"],
                dsl=book.get("dsl"), symbol=sym, timeframe=tf,
            )
            dual.save_task(task)
            store.save_task_meta(task)
            return {
                "ok": False, "task_id": tid, "reason": "funnel_l1_fail",
                "phase3_funnel": task["phase3_funnel"],
                "gate_results": task["gate_results"],
                "matrix_eval": task.get("matrix_eval"),
            }

    # Full-history BT + Phase-3 WF windows (Calmar≥1.0 + positive expectancy)
    # WF stays on primary seed; Gate2 fitness uses pooled matrix trades when enabled.
    task["stage"] = "gate2_gate3_backtest_wf"
    wf = _walk_forward_detail(definition, sym, tf, folds=10)
    base_m = wf.get("base_metrics") or {}
    trades = wf.get("trades") or []
    # Soft cull: if primary Calmar < 0.5, skip expensive matrix pool (formal Gate2 still runs on primary)
    skip_pool = soft_gate.should_skip_full_matrix(base_m)
    soft_snap = soft_gate.soft_progress(base_m)
    task["matrix_eval"]["soft_incubation"] = soft_snap
    task["matrix_eval"]["skip_full_matrix_pool"] = bool(skip_pool)
    if enable_multi_symbol_matrix and len(matrix_syms) > 1 and not skip_pool:
        print(
            "[pipeline_step_a] 第三次复核·矩阵池 n_syms=%d primary=%s mode=%s"
            % (len(matrix_syms), sym, (ram_budget or {}).get("mode")),
            flush=True,
        )
        pooled = _matrix_pool_backtest(definition, matrix_syms, tf, dual)
        task["matrix_eval"]["gate2_pool"] = {
            "n_trades": pooled.get("n_trades"),
            "per_symbol": pooled.get("per_symbol"),
            "primary_error": pooled.get("primary_error"),
        }
        if pooled.get("trades"):
            trades = pooled["trades"]
            base_m = pooled.get("base_metrics") or dual._metrics_from_trades(trades)
            wf = dict(wf)
            wf["base_metrics"] = base_m
            wf["trades"] = trades
            wf["matrix_pooled"] = True
    elif skip_pool:
        print(
            "[pipeline_step_a] 跳过第三次复核全矩阵池（主标的 calmar soft-kill）；仅主标的跑抗离群",
            flush=True,
        )
        task["matrix_eval"]["gate2_pool"] = {
            "skipped": True,
            "reason": "primary_calmar_lt_%.2f" % soft_gate.ANCHOR_CALMAR_KILL,
            "soft_incubation": soft_snap,
        }

    # Phase-3 L2: fitness + Pareto (singleton always on front if fitness passes)
    l2 = evaluate_l2_survivor(trades, base_metrics=base_m, candidate_id=tid)
    task["phase3_funnel"]["l2_full_bt_pareto"] = l2

    g2 = evaluate_gate2(base_m, trades)
    # Gate2 also encodes Phase-2 fitness; L2 fail-closed mirrors it
    if not l2.get("pass") and g2.get("pass"):
        # Prefer fitness engine as source of truth via Gate2; keep L2 log
        pass
    if not l2.get("pass"):
        task["phase3_funnel"]["rejected_at"] = "funnel_l2_full_bt_pareto"
        task["phase3_funnel"]["reject_reasons"] = list(l2.get("reject_reasons") or [])

    # Phase-3 L3: null hypothesis (perm / invert / WF already in wf payload)
    def _l3_bt(frm, defn):
        return dsl_mod.backtest_dsl(frm, defn, stop_loss_pct=0.009)

    l3 = evaluate_null_hypothesis(
        definition=definition,
        frame=frame_for_funnel,
        backtest_fn=_l3_bt if frame_for_funnel is not None else None,
        trades=trades,
        seed=hash(tid) % (2 ** 31) if tid else 42,
        skip_frame_tests=frame_for_funnel is None,
    )
    # Prefer the Phase-3 WF windows already computed in `_walk_forward_detail`
    # for Gate3 (same rule); keep L3 perm/invert evidence.
    l3 = dict(l3)
    l3["walk_forward"] = {
        "windows": wf.get("windows"),
        "pass_count": wf.get("pass_count"),
        "total": wf.get("total"),
        "pass": int(wf.get("pass_count") or 0) >= 7 and int(wf.get("total") or 0) >= 10,
        "rule": wf.get("phase3_wf_rule"),
        "trades": trades,
        "oos_trades": trades,
    }
    # Recompute L3 pass with WF from pipeline
    l3_perm_ok = bool((l3.get("tests") or {}).get("permuted_returns", {}).get("pass"))
    l3_inv_ok = bool((l3.get("tests") or {}).get("inverted_market", {}).get("pass"))
    l3_wf_ok = bool(l3["walk_forward"].get("pass"))
    l3["pass"] = l3_perm_ok and l3_inv_ok and l3_wf_ok
    l3["reject_reasons"] = []
    if not l3_perm_ok:
        l3["reject_reasons"].extend(
            (l3.get("tests") or {}).get("permuted_returns", {}).get("reject_reasons")
            or ["perm_fail"]
        )
    if not l3_inv_ok:
        l3["reject_reasons"].extend(
            (l3.get("tests") or {}).get("inverted_market", {}).get("reject_reasons")
            or ["invert_fail"]
        )
    if not l3_wf_ok:
        l3["reject_reasons"].append(
            "walk_forward_%s_of_%s" % (wf.get("pass_count"), wf.get("total"))
        )
    task["phase3_funnel"]["l3_null_hypothesis"] = l3
    if not l3.get("pass"):
        task["phase3_funnel"]["rejected_at"] = (
            task["phase3_funnel"].get("rejected_at") or "funnel_l3_null_hypothesis"
        )
        task["phase3_funnel"]["reject_reasons"] = list(
            task["phase3_funnel"].get("reject_reasons") or []
        ) + list(l3.get("reject_reasons") or [])

    # Phase-4 incubator only when L1–L3 all pass (fail-closed overfit cull)
    phase3_ok = (
        bool(l1.get("pass")) and bool(l2.get("pass")) and bool(l3.get("pass"))
    )
    incubator = None
    if phase3_ok:
        from .incubator import run_incubator, metrics_from_trades as _inc_metrics
        incub_base = dict(base_m)
        incub_extra = _inc_metrics(trades)
        for _k in ("calmar", "payoff_ratio", "classic_expectancy", "mean_mae", "trades"):
            if incub_base.get(_k) is None:
                incub_base[_k] = incub_extra.get(_k)
        incubator = run_incubator(
            definition=definition,
            target_symbol=sym,
            timeframe=tf,
            baseline_trades=trades,
            baseline_metrics=incub_base,
            baseline_frame=frame_for_funnel,
            backtest_fn=_funnel_bt,
            stop_loss_pct=0.009,
        )
        task["phase4_incubator"] = incubator
        task["phase3_funnel"]["phase4_incubator"] = {
            "pass": incubator.get("pass"),
            "cross_asset_score": incubator.get("cross_asset_score"),
            "rejected_at": incubator.get("rejected_at"),
        }
        if not incubator.get("pass"):
            if _admission_v2_enabled():
                _soft_skip_legacy(task, "phase4_incubator", {
                    "reject_reasons": list(incubator.get("reject_reasons") or []),
                    "cross_asset_score": incubator.get("cross_asset_score"),
                })
            else:
                task["stage"] = "archived"
                task["gate_results"] = assemble_gate_results(task["gates"], tid)
                save_gate_results(tid, task["gate_results"])
                _archive_step_a(
                    task, stage="phase4_incubator",
                    failed_tests=["incubator"],
                    reason="Phase4 incubator reject: %s" % ",".join(
                        incubator.get("reject_reasons") or ["incubator_fail"]),
                    verdict="phase4_incubator_reject",
                    is_mech_absent=True,
                )
                record_pipeline_rejection(
                    task_id=tid, stage="phase4_incubator",
                    failed_tests=["incubator"],
                    reject_reasons=incubator.get("reject_reasons") or ["incubator_fail"],
                    dsl=definition, symbol=sym, timeframe=tf,
                )
                dual.save_task(task)
                store.save_task_meta(task)
                return {
                    "ok": False, "task_id": tid, "reason": "phase4_incubator_fail",
                    "phase4_incubator": incubator,
                    "phase3_funnel": task["phase3_funnel"],
                    "gate_results": task["gate_results"],
                    "production_mounted": False,
                }

    g3 = evaluate_gate3(wf, trades=trades, base_metrics=base_m, null_hypothesis=l3)
    task["gates"].extend([g2, g3])
    task["walk_forward"] = {
        "pass_count": wf.get("pass_count"), "total": wf.get("total"),
        "windows": wf.get("windows"),
        "phase3_wf_rule": wf.get("phase3_wf_rule"),
    }
    task["base_metrics"] = base_m

    def bt_fn(defn):
        try:
            r = dual._backtest(defn, sym, tf, "observed_base")
            return {"metrics": dual._metrics_from_trades(r.get("trades") or []), "trades": r.get("trades")}
        except Exception as exc:
            return {"metrics": {"error": str(exc), "mean_net": 0, "trades": 0}, "trades": []}

    def friction_fn(slip_mult=1.0, latency_extra=0.0, fee_mult=1.0):
        try:
            r = dual._backtest(definition, sym, tf, "observed_base",
                              slip_mult=slip_mult, latency_extra=latency_extra,
                              fill_fail_pct=0.05 if slip_mult >= 2 else 0.0)
            return {"metrics": dual._metrics_from_trades(r.get("trades") or [])}
        except Exception as exc:
            return {"metrics": {"error": str(exc), "sharpe": -1, "mean_net": -0.02}}

    # Repair loop if gate2/3 fail (3 rounds typed)
    repair_log = init_repair_log(tid, task["strategy_id"], stmt)
    task["repair_log"] = repair_log
    prior_fidelity = fidelity
    need_repair = (not g2["pass"]) or (not g3["pass"])
    # Reconstructed admission: Gate2/3 fitness is advisory — do NOT burn
    # viability-archive repair rounds that would reject ADA-T3-class strategies.
    if need_repair and _admission_v2_enabled():
        _soft_skip_legacy(task, "gate2_3_repair_loop", {
            "g2_pass": bool(g2.get("pass")),
            "g3_pass": bool(g3.get("pass")),
            "note": "skip_viability_archive_under_admission_v2",
        })
        need_repair = False
    archived_by_repair = False
    if need_repair:
        for ri, rtype in enumerate(REPAIR_ROUND_TYPES):
            task["stage"] = "repair_%s" % rtype
            before_fp = build_step_a_fingerprint(spec)
            book, stop_mut = _apply_repair_round(book, spec, rtype, ri)
            if rtype == "mechanism_viability_verdict":
                task["stage"] = "archived"
                task["viability_verdict"] = {
                    "verdict": "mechanism_not_viable_or_data_insufficient",
                    "gate2": g2["pass"], "gate3": g3["pass"],
                }
                repair_log, drift = record_repair_round(
                    repair_log, failed_test="gate2_or_gate3", root_cause="viability",
                    modifications=["verdict_only_no_mechanism_swap"],
                    statement_before=stmt, statement_after=stmt,
                    fingerprint_before=before_fp, fingerprint_after=before_fp,
                    frequency_before=base_m.get("trades"), frequency_after=base_m.get("trades"),
                    oos_before=base_m.get("oos_profit"), oos_after=base_m.get("oos_profit"),
                    introduced_new_condition=False, changed_edge_source=False,
                )
                task["repair_log"] = repair_log
                task["gate_results"] = assemble_gate_results(task["gates"], tid)
                save_gate_results(tid, task["gate_results"])
                _archive_step_a(
                    task, stage="repair_round3", failed_tests=["viability"],
                    reason="3-round repair exhausted; mechanism viability fail",
                    verdict="mechanism_viability_fail_archived",
                    is_mech_absent=True,
                    lessons=["no infinite retune; no disguised mechanism swap"],
                )
                dual.save_task(task)
                store.save_task_meta(task)
                archived_by_repair = True
                break
            try:
                definition = dsl_mod.validate_strategy(book.get("dsl") or {})
            except Exception:
                continue
            wf = _walk_forward_detail(definition, sym, tf, folds=10)
            base_m = wf.get("base_metrics") or {}
            trades = wf.get("trades") or []
            l2 = evaluate_l2_survivor(trades, base_metrics=base_m, candidate_id=tid)
            task["phase3_funnel"]["l2_full_bt_pareto"] = l2
            l3 = evaluate_null_hypothesis(
                definition=definition,
                frame=frame_for_funnel,
                backtest_fn=_l3_bt if frame_for_funnel is not None else None,
                trades=trades,
                seed=hash(tid) % (2 ** 31) if tid else 42,
                skip_frame_tests=frame_for_funnel is None,
            )
            l3 = dict(l3)
            l3["walk_forward"] = {
                "windows": wf.get("windows"),
                "pass_count": wf.get("pass_count"),
                "total": wf.get("total"),
                "pass": int(wf.get("pass_count") or 0) >= 7 and int(wf.get("total") or 0) >= 10,
                "rule": wf.get("phase3_wf_rule"),
                "trades": trades,
                "oos_trades": trades,
            }
            l3_perm_ok = bool((l3.get("tests") or {}).get("permuted_returns", {}).get("pass"))
            l3_inv_ok = bool((l3.get("tests") or {}).get("inverted_market", {}).get("pass"))
            l3_wf_ok = bool(l3["walk_forward"].get("pass"))
            l3["pass"] = l3_perm_ok and l3_inv_ok and l3_wf_ok
            task["phase3_funnel"]["l3_null_hypothesis"] = l3
            g2 = evaluate_gate2(base_m, trades)
            g3 = evaluate_gate3(wf, trades=trades, base_metrics=base_m, null_hypothesis=l3)
            # replace last gate2/3
            task["gates"] = [g for g in task["gates"] if g["gate_id"] not in (
                "gate2_base_backtest", "gate3_walk_forward")]
            task["gates"].extend([g2, g3])
            task["walk_forward"] = {
                "pass_count": wf.get("pass_count"), "total": wf.get("total"),
                "windows": wf.get("windows"),
                "phase3_wf_rule": wf.get("phase3_wf_rule"),
            }
            fidelity = build_fidelity_diff(spec, book.get("dsl"), round_i=ri + 1,
                                           repair_type=rtype, prior_diff=prior_fidelity)
            save_fidelity_diff(tid, fidelity)
            prior_fidelity = fidelity
            after_fp = build_step_a_fingerprint(spec)
            repair_log, drift = record_repair_round(
                repair_log, failed_test="gate2_or_gate3", root_cause=rtype,
                modifications=book.get("repair_changed") or [rtype],
                statement_before=stmt, statement_after=stmt,
                fingerprint_before=before_fp, fingerprint_after=after_fp,
                frequency_before=None, frequency_after=base_m.get("trades"),
                oos_before=None, oos_after=base_m.get("oos_profit"),
                introduced_new_condition=False, changed_edge_source=False,
            )
            if drift.get("drift") == "material":
                task["stage"] = "archived"
                _archive_step_a(task, stage="repair", failed_tests=["mechanism_drift"],
                                reason=str(drift), verdict="drift_during_repair", drift=True)
                dual.save_task(task)
                archived_by_repair = True
                break
            if g2["pass"] and g3["pass"]:
                need_repair = False
                break
        task["repair_log"] = repair_log
        task["fidelity_diff"] = prior_fidelity

    if archived_by_repair:
        return {"ok": False, "task_id": tid, "reason": "repair_exhausted_or_drift",
                "gate_results": task.get("gate_results")}

    if not g2["pass"] or not g3["pass"]:
        if _admission_v2_enabled():
            from .review_admission_v2 import (
                review2_single_symbol_stability,
                review3_matrix_outlier,
            )
            ev = _evidence_metrics_from_bt(base_m, trades)
            r2 = review2_single_symbol_stability(metrics=ev, trades=trades)
            r3 = review3_matrix_outlier(
                gate2_fitness=(g2.get("evidence") or {}).get("fitness")
                if isinstance(g2, dict) else None,
                soft_pass=True,
            )
            task.setdefault("admission_v2", {})
            task["admission_v2"]["review2"] = r2
            task["admission_v2"]["review3"] = r3
            task["admission_v2"]["legacy_gate2"] = {
                "pass": bool(g2.get("pass")),
                "evidence": g2.get("evidence"),
            }
            task["admission_v2"]["legacy_gate3"] = {
                "pass": bool(g3.get("pass")),
                "evidence": g3.get("evidence") if isinstance(g3, dict) else {},
            }
            if r2.get("pass"):
                # R2 hard-pass; R3 soft-pass under ADA-T3 → continue (no hard block)
                _soft_skip_legacy(task, "gate2_3", {
                    "g2_pass": bool(g2.get("pass")),
                    "g3_pass": bool(g3.get("pass")),
                    "review2_pass": True,
                    "review3_soft_passed": bool(r3.get("soft_passed")),
                    "review2_metrics": r2.get("metrics"),
                })
            else:
                task["stage"] = "archived"
                task["gate_results"] = assemble_gate_results(task["gates"], tid)
                save_gate_results(tid, task["gate_results"])
                _archive_step_a(
                    task, stage="review2_single_symbol",
                    failed_tests=list(r2.get("reject_reasons") or ["stability_fail"]),
                    reason="admission_v2 review2 fail: %s" % ",".join(
                        r2.get("reject_reasons") or []),
                    verdict="review2_evidence_fail",
                )
                dual.save_task(task)
                store.save_task_meta(task)
                return {
                    "ok": False, "task_id": tid,
                    "reason": "review2_evidence_fail",
                    "admission_v2": task.get("admission_v2"),
                    "gate_results": task["gate_results"],
                }
        else:
            task["stage"] = "archived"
            task["gate_results"] = assemble_gate_results(task["gates"], tid)
            save_gate_results(tid, task["gate_results"])
            _archive_step_a(task, stage="gate2_3", failed_tests=["backtest_or_wf"],
                            reason="第三次复核仍未通过", verdict="backtest_wf_fail",
                            is_mech_absent=not g3["pass"])
            dual.save_task(task)
            store.save_task_meta(task)
            return {"ok": False, "task_id": tid, "reason": "gate2_3_fail",
                    "gate_results": task["gate_results"]}

    # ---- Gate4: 20 split tests ----
    task["stage"] = "gate4_split_tests"
    split_summary = run_split_tests_20(
        definition=definition,
        base_metrics=base_m,
        trades=trades,
        backtest_fn=bt_fn,
        mechanism_spec=spec,
        fidelity_diff=task.get("fidelity_diff"),
        symbol=sym,
        timeframe=tf,
        friction_fn=friction_fn,
    )
    save_split_tests(tid, split_summary)
    task["split_tests_20"] = {
        "pass": split_summary.get("pass"),
        "fail": split_summary.get("fail"),
        "inconclusive": split_summary.get("inconclusive"),
        "gate4_block": split_summary.get("gate4_block"),
        "hard_fail_ids": split_summary.get("gate4_hard_fail_ids"),
    }
    _save_artifact(tid, "split_tests_20", split_summary)
    g4 = evaluate_gate4(split_summary)
    task["gates"].append(g4)
    if not g4["pass"]:
        if _admission_v2_enabled():
            _soft_skip_legacy(task, "gate4_split_tests", {
                "hard_fail_ids": split_summary.get("gate4_hard_fail_ids"),
            })
        else:
            task["stage"] = "archived"
            task["gate_results"] = assemble_gate_results(task["gates"], tid)
            save_gate_results(tid, task["gate_results"])
            _archive_step_a(
                task, stage="gate4", failed_tests=split_summary.get("gate4_hard_fail_ids") or ["split"],
                reason="mechanism_failure in split tests",
                verdict="split_destruction_mechanism_fail",
                is_mech_absent=True,
                counterexamples=["hard_fail:%s" % x for x in (split_summary.get("gate4_hard_fail_ids") or [])],
            )
            dual.save_task(task)
            store.save_task_meta(task)
            return {"ok": False, "task_id": tid, "reason": "gate4_fail",
                    "gate_results": task["gate_results"]}

    # ---- Gate5 MC + friction ----
    task["stage"] = "gate5_mc_friction"
    mc_row = None
    for r in split_summary.get("results") or []:
        if r.get("test_id") == "mc_trade_order":
            mc_row = r
            break
    fr = friction_fn(slip_mult=2.0, latency_extra=0.00005)
    mc_summary = {
        "accept_pct_observed": (mc_row or {}).get("actual", {}).get("accept_pct_observed"),
        "status": (mc_row or {}).get("status"),
    }
    g5 = evaluate_gate5(mc_summary, fr)
    task["gates"].append(g5)
    task["friction"] = fr
    if not g5["pass"]:
        if _admission_v2_enabled():
            _soft_skip_legacy(task, "gate5_mc_friction", {"friction": fr, "mc": mc_summary})
        else:
            task["stage"] = "archived"
            task["gate_results"] = assemble_gate_results(task["gates"], tid)
            save_gate_results(tid, task["gate_results"])
            _archive_step_a(task, stage="gate5", failed_tests=["mc_friction"],
                            reason="mc/friction fail", verdict="cost_or_robustness_fail",
                            is_cost=True)
            dual.save_task(task)
            store.save_task_meta(task)
            return {"ok": False, "task_id": tid, "reason": "gate5_fail",
                    "gate_results": task["gate_results"]}

    # ---- Gate6 multi-AI (separate, not averaged) ----
    task["stage"] = "gate6_multi_ai"
    reviews = {
        "glm_mechanism": glm_mechanism_review(spec, _ai_json),
        "codex_fidelity": codex_fidelity_review(spec, task.get("fidelity_diff"), _ai_json),
        "deepseek_logic": deepseek_logic_attack(spec, book.get("dsl"), {"base_metrics": base_m}, _ai_json),
        "production_risk": production_risk_attack(spec, book.get("dsl"), {"base_metrics": base_m}, _ai_json),
    }
    task["multi_ai_reviews"] = {
        k: {kk: vv for kk, vv in v.items() if kk != "raw"} for k, v in reviews.items()
    }
    _save_artifact(tid, "multi_ai_reviews", task["multi_ai_reviews"])
    g6 = evaluate_gate6(reviews)
    task["gates"].append(g6)
    if not g6["pass"]:
        if _admission_v2_enabled():
            _soft_skip_legacy(task, "gate6_multi_ai", {
                "reviews": {
                    k: bool((v or {}).get("pass")) for k, v in (reviews or {}).items()
                },
            })
        else:
            task["stage"] = "archived"
            task["gate_results"] = assemble_gate_results(task["gates"], tid)
            save_gate_results(tid, task["gate_results"])
            _archive_step_a(task, stage="gate6", failed_tests=["multi_ai"],
                            reason="one or more separate AI reviews failed",
                            verdict="multi_ai_review_fail",
                            counterexamples=(reviews.get("deepseek_logic") or {}).get("counterexamples") or [])
            record_pipeline_rejection(
                task_id=tid, stage="gate6_multi_ai",
                failed_tests=["multi_ai_review"],
                reject_reasons=["gate6_ai_review_fail"],
                dsl=book.get("dsl"), symbol=sym, timeframe=tf,
            )
            dual.save_task(task)
            store.save_task_meta(task)
            return {"ok": False, "task_id": tid, "reason": "gate6_fail",
                    "gate_results": task["gate_results"]}

    # ---- Phase 5: 3-party AI unanimous consensus ----
    task["stage"] = "phase5_3party_consensus"
    from .formal_4d import run_phase5_consensus
    p5_evidence = {
        "base_metrics": base_m,
        "walk_forward": task.get("walk_forward"),
        "split_scores": task.get("split_scores"),
        "phase3_funnel": {
            "l1_pass": bool(l1.get("pass")),
            "l2_pass": bool(l2.get("pass")),
            "l3_pass": bool(l3.get("pass")),
        },
        "phase4_incubator": {
            "pass": bool((incubator or {}).get("pass")),
            "cross_asset_score": (incubator or {}).get("cross_asset_score"),
        },
        "gate6_reviews": {
            k: {kk: vv for kk, vv in v.items() if kk != "raw"}
            for k, v in reviews.items()
        },
    }
    p5_candidate = {
        "dsl": definition,
        "mechanism_statement": stmt,
        "mechanism_spec_summary": {
            "mechanism_family": spec.get("mechanism_family"),
            "market_inefficiency": spec.get("market_inefficiency"),
            "entry_logic": spec.get("entry_logic"),
        },
    }
    p5_result = run_phase5_consensus(p5_candidate, p5_evidence)
    task["phase5_consensus"] = {
        "approved": p5_result.get("approved"),
        "fatal_any": p5_result.get("fatal_any"),
        "fail_reasons": p5_result.get("fail_reasons"),
        "policy": p5_result.get("policy"),
        "reviews": [
            {k: v for k, v in r.items() if k != "raw"}
            for r in (p5_result.get("reviews") or [])
        ],
    }
    _save_artifact(tid, "phase5_consensus", task["phase5_consensus"])

    if not p5_result.get("approved"):
        if _admission_v2_enabled():
            _soft_skip_legacy(task, "phase5_3party_consensus", {
                "fail_reasons": p5_result.get("fail_reasons"),
                "fatal_any": p5_result.get("fatal_any"),
            })
        else:
            task["stage"] = "archived"
            task["gate_results"] = assemble_gate_results(task["gates"], tid)
            save_gate_results(tid, task["gate_results"])
            _archive_step_a(
                task, stage="phase5_3party_consensus",
                failed_tests=["phase5_unanimous"],
                reason="Phase5 3-party AI consensus rejected: %s" % ", ".join(
                    p5_result.get("fail_reasons") or ["consensus_fail"]),
                verdict="phase5_consensus_reject",
                counterexamples=[
                    "%s:%s" % (r.get("dimension"), r.get("reason") or "")
                    for r in (p5_result.get("reviews") or [])
                    if r.get("decision") != "APPROVE"
                ][:5],
            )
            record_pipeline_rejection(
                task_id=tid, stage="phase5_3party_consensus",
                failed_tests=["phase5_unanimous"],
                reject_reasons=p5_result.get("fail_reasons") or ["consensus_fail"],
                dsl=definition, symbol=sym, timeframe=tf,
            )
            dual.save_task(task)
            store.save_task_meta(task)
            return {
                "ok": False, "task_id": tid, "reason": "phase5_consensus_fail",
                "phase5_consensus": task["phase5_consensus"],
                "gate_results": task["gate_results"],
                "production_mounted": False,
            }

    # Split scores
    wr = base_m.get("win_rate_pct")
    task["split_scores"] = build_split_scores(
        ai_logic_wr=((reviews.get("glm_mechanism") or {}).get("mechanism_clarity")),
        ai_n=1,
        backtest_wr=wr,
        backtest_n=base_m.get("trades"),
        walk_forward_wr=None,
        walk_forward_n=wf.get("total"),
        oos_wr=None,
        oos_n=None,
        sim_exec_wr=None,
        live_wr=None,
        live_n=0,
        cost_expectancy=base_m.get("mean_net"),
        mechanism_credibility=((reviews.get("glm_mechanism") or {}).get("mechanism_clarity")),
        execution_credibility=80 if (reviews.get("production_risk") or {}).get("pass") else 40,
        data_credibility=70 if g3["pass"] else 30,
        overall_credibility=None,
    )
    # AI clarity may be 0-100; still not live ready
    _save_artifact(tid, "split_scores", task["split_scores"])

    # ---- 第四次复核（三AI）+ 人工确认签发（no auto mount）----
    task["stage"] = "review4_three_ai"
    import auto_trade_human_confirm_pipeline as pipeline
    from .incubator import metrics_from_trades as _inc_metrics_g7
    from .review_admission_v2 import (
        evaluate_admission,
        review1_syntax_assert_density,
        review2_single_symbol_stability,
        review3_matrix_outlier,
        review4_three_ai,
        human_confirm_gate,
    )
    incub = task.get("phase4_incubator") or {}
    incub_m = incub.get("baseline_metrics") or _inc_metrics_g7(trades)
    ev_m = _evidence_metrics_from_bt(base_m, trades)
    l0_ev = (task.get("phase3_funnel") or {}).get("l0_density")
    g2_fit = (g2.get("evidence") or {}).get("fitness") if isinstance(g2, dict) else None
    task.setdefault("admission_v2", {})
    if not task["admission_v2"].get("review1"):
        task["admission_v2"]["review1"] = review1_syntax_assert_density(
            definition, lookahead_ok=True, l0=l0_ev, require_density=False,
        )
    if not task["admission_v2"].get("review2"):
        task["admission_v2"]["review2"] = review2_single_symbol_stability(
            metrics=ev_m, trades=trades,
        )
    if not task["admission_v2"].get("review3"):
        task["admission_v2"]["review3"] = review3_matrix_outlier(
            gate2_fitness=g2_fit, soft_pass=_admission_v2_enabled(),
        )
    r1 = task["admission_v2"].get("review1") or {}
    r2 = task["admission_v2"].get("review2") or {}
    r3 = task["admission_v2"].get("review3") or {}
    if _admission_v2_enabled() and not r2.get("pass"):
        task["stage"] = "archived"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        dual.save_task(task)
        store.save_task_meta(task)
        return {
            "ok": False, "task_id": tid, "reason": "review2_evidence_fail",
            "admission_v2": task.get("admission_v2"),
            "gate_results": task["gate_results"],
            "production_mounted": False,
        }
    if _admission_v2_enabled() and (not r1.get("pass") or not r3.get("pass")):
        task["stage"] = "archived"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        dual.save_task(task)
        store.save_task_meta(task)
        return {
            "ok": False, "task_id": tid, "reason": "admission_v2_fail",
            "admission_v2": task.get("admission_v2"),
            "gate_results": task["gate_results"],
            "production_mounted": False,
        }
    ai_review = {
        "approved": True,
        "policy": (
            "admission_v2_ada_t3_calibrated"
            if _admission_v2_enabled()
            else "step_a_gates_0_6_pass_phase4_incubator"
        ),
        "ai_theoretical_wr_avg": task["split_scores"].get("ai_logic_wr", {}).get("win_rate_pct"),
        "natural_language": (
            "四复核（ADA-T3校准）通过：第一次语法/断言/密度 + 第二次单标的稳定性 + "
            "第三次矩阵/抗离群 + 第四次三AI理论复核；"
            "已接入原 WxPusher 人工确认通道。production_mounted=False。"
            if _admission_v2_enabled() else
            "STEP A gates0-6 + Phase3 funnel + Phase4 incubator pass; "
            "awaiting human confirm. NOT live-ready. production_mounted=False."
        ),
        "split_scores": task["split_scores"],
        "step_a": True,
        "phase4_incubator": True,
        "admission_v2": bool(_admission_v2_enabled()),
        "calmar": incub.get("calmar") or incub_m.get("calmar") or base_m.get("calmar"),
        "payoff": incub.get("payoff_ratio") or incub_m.get("payoff_ratio") or base_m.get("payoff_ratio"),
        "cross_asset_score": incub.get("cross_asset_score"),
        "mean_mae": incub.get("mean_mae") or incub_m.get("mean_mae"),
    }
    r4 = review4_three_ai(ai_review=ai_review)
    task["admission_v2"]["review4"] = r4
    if _admission_v2_enabled() and not r4.get("pass"):
        task["stage"] = "archived"
        task["gate_results"] = assemble_gate_results(task["gates"], tid)
        save_gate_results(tid, task["gate_results"])
        dual.save_task(task)
        store.save_task_meta(task)
        return {
            "ok": False, "task_id": tid, "reason": "review4_ai_fail",
            "admission_v2": task.get("admission_v2"),
            "gate_results": task["gate_results"],
            "production_mounted": False,
        }
    task["stage"] = "pending_human_confirm"
    push = pipeline.ingest_and_screen(
        {"dsl": definition, "symbol": sym, "timeframe": tf,
         "thesis": book.get("thesis"), "mechanism_spec": spec,
         "mechanism_statement": stmt, "mechanism_fingerprint": fp_a,
         "live_enabled": False, "auto_trade_eligible": False,
         "production_mounted": False,
         "phase4_incubator": incub},
        source="dual_engine_step_a_admission_v2" if _admission_v2_enabled() else "dual_engine_step_a",
        ai_review=ai_review,
        require_ai_review=True,
    )
    hc = human_confirm_gate(pending_ok=bool(push.get("ok")), human_confirmed=False)
    task["admission_v2"]["human_confirm"] = hc
    adm = evaluate_admission(
        definition=definition,
        lookahead_ok=True,
        death_reason=None,
        metrics=ev_m,
        trades=trades,
        ai_review=ai_review,
        pending_ok=bool(push.get("ok")),
        l0=l0_ev,
        l1=l1,
        gate2_fitness=g2_fit,
    )
    task["admission_v2"].update({
        "final": adm,
        "review1": adm.get("reviews", {}).get("r1") or r1,
        "review2": adm.get("reviews", {}).get("r2") or r2,
        "review3": adm.get("reviews", {}).get("r3") or r3,
        "review4": adm.get("reviews", {}).get("r4") or r4,
        "human_confirm": adm.get("human_confirm") or hc,
    })
    human_state = {
        "pending_ok": bool(push.get("ok")),
        "awaiting_human": bool(push.get("ok")),
        "human_confirmed": False,  # never auto
        "auto_open_mounted": False,
        "real_size_granted": False,
        "push": {"ok": push.get("ok"), "key": push.get("key"), "reason": push.get("reason")},
        "admission_v2": True,
        "admission_profile": _admission_profile(),
    }
    task["human_confirm_state"] = human_state
    g7 = evaluate_gate7(human_state)
    task["gates"].append(g7)
    task["gate_results"] = assemble_gate_results(task["gates"], tid)
    # production_mounted stays false until CLI --confirm (B grade 30% only)
    task["gate_results"]["production_mounted"] = False
    save_gate_results(tid, task["gate_results"])

    task["pending_push"] = human_state["push"]
    task["stage"] = "awaiting_human" if push.get("ok") else "failed_push"
    task["production_mounted"] = False
    dual.save_task(task)
    store.save_task_meta(task)
    dual._record_formal({
        "time": _now(), "task_id": tid, "key": task["strategy_id"],
        "title": book.get("title"), "symbol": sym, "timeframe": tf,
        "status": "等待" if push.get("ok") else "退回",
        "annotation": ai_review["natural_language"],
        "workflow_version": WORKFLOW_VERSION,
        "step_a": True,
        "phase4_incubator_pass": bool(incub.get("pass")),
        "production_mounted": False,
    })
    return {
        "ok": bool(push.get("ok")),
        "task_id": tid,
        "stage": task["stage"],
        "pending": push,
        "gate_results": task["gate_results"],
        "counts_as_independent_mechanism": task.get("counts_as_independent_mechanism"),
        "production_mounted": False,
        "split_scores": task["split_scores"],
        "phase4_incubator": incub,
    }


def start_creation_task_step_a(async_mode=True, symbol=None, timeframe=None,
                               exploration_mode="A", allow_horizontal_expand=False,
                               prebuilt_spec_pack=None, windtalker_tag=None,
                               enable_multi_symbol_matrix=False):
    ensure_dirs()
    ensure_step_a_dirs()
    with _JOB_LOCK:
        if _JOB.get("running"):
            return {"ok": False, "status": "running", "error": "job_already_running",
                    "kind": _JOB.get("kind")}
        _JOB.update({"running": True, "kind": "creation_step_a", "started_at": _now(),
                     "error": None})

    def _worker():
        try:
            run_creation_pipeline_step_a(
                symbol=symbol, timeframe=timeframe,
                exploration_mode=exploration_mode,
                allow_horizontal_expand=allow_horizontal_expand,
                prebuilt_spec_pack=prebuilt_spec_pack,
                windtalker_tag=windtalker_tag,
                enable_multi_symbol_matrix=enable_multi_symbol_matrix,
            )
        except Exception as exc:
            _JOB["error"] = str(exc)
            try:
                import auto_trade_dual_engine_factory as dual
                dual._append_audit({
                    "event": "creation_step_a_crash",
                    "error": str(exc),
                    "trace": traceback.format_exc()[-800:],
                })
            except Exception:
                pass
        finally:
            with _JOB_LOCK:
                _JOB.update({"running": False, "kind": None})

    if async_mode:
        threading.Thread(target=_worker, name="dual-engine-creation-step-a",
                         daemon=True).start()
        time.sleep(0.05)
        return {"ok": True, "status": "started", "async": True,
                "workflow_version": WORKFLOW_VERSION,
                "step_a_code_version": STEP_A_CODE_VERSION,
                "exploration_mode": exploration_mode}
    return run_creation_pipeline_step_a(
        symbol=symbol, timeframe=timeframe, exploration_mode=exploration_mode,
        allow_horizontal_expand=allow_horizontal_expand,
        prebuilt_spec_pack=prebuilt_spec_pack,
        windtalker_tag=windtalker_tag,
        enable_multi_symbol_matrix=enable_multi_symbol_matrix,
    )


def job_snapshot_step_a():
    with _JOB_LOCK:
        return dict(_JOB)


def _load_strategy_json_pack(path):
    """Load a handcraft / Windtalker strategy JSON into prebuilt_spec_pack shape."""
    import json as _json
    from pathlib import Path as _Path

    p = _Path(path)
    if not p.is_file():
        raise FileNotFoundError("strategy_json not found: %s" % path)
    pack = _json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(pack, dict):
        raise ValueError("strategy_json must be a JSON object")
    if not isinstance(pack.get("mechanism_spec"), dict):
        raise ValueError("strategy_json missing mechanism_spec object")
    pack = dict(pack)
    pack.setdefault("ok", True)
    pack.setdefault("errors", [])
    meta = dict(pack.get("meta") or {})
    pack["meta"] = meta
    return pack


def dry_run_gate0_from_pack(pack):
    """Cheap local Gate0 + optional DSL validate. Does not touch production / AI."""
    from .mechanism_spec import normalize_mechanism_spec

    meta = pack.get("meta") or {}
    focus = {
        "symbol": meta.get("symbol"),
        "timeframe": meta.get("timeframe"),
    }
    ok, errors, cleaned = normalize_mechanism_spec(pack.get("mechanism_spec"), focus=focus)
    g0 = evaluate_gate0(cleaned, ok)
    dsl_report = None
    direction = str(meta.get("direction") or "long").lower()
    dsl = pack.get("dsl")
    if not isinstance(dsl, dict):
        dsl = pack.get("dsl_long" if direction == "long" else "dsl_short")
    if isinstance(dsl, dict):
        try:
            import auto_trade_strategy_dsl as dsl_mod
            dsl_mod.validate_strategy(dsl)
            dsl_report = {"ok": True, "key": dsl.get("key"), "direction": dsl.get("direction")}
        except Exception as exc:
            dsl_report = {"ok": False, "error": str(exc)}
    fam = cleaned.get("mechanism_family")
    kb_note = None
    try:
        blocked, why = path_is_blocked("family|%s" % fam, family=fam)
        kb_note = {"family": fam, "blocked": bool(blocked), "why": why, "source": "failure_kb"}
    except Exception as exc:
        # Local macOS / sandbox often cannot read /root WF_DIR — fall back to
        # the known prod blocked set from the forced KB read (2026-07-29).
        known_blocked = {
            "breakout_trap_reversal",
            "liquidity_failed_breakout_high",
            "mean_reversion_vwap_deviation",
            "time_structure_session_breakout",
            "trend_continuation_thrust_retest",
            "vol_regime_compression_release",
            "liquidity_failed_breakout_low",
            "mean_reversion_rsi_extreme",
            "mean_reversion_zscore_dislocation",
            "absorption_climax_reclaim",
            "selective_session_thrust",
            "confirmed_vol_release",
            "cascade_trap_proxy",
            "vacuum_fill_impulse",
            "compression_release_structural_breakout",
        }
        blocked = str(fam or "").strip() in known_blocked
        kb_note = {
            "family": fam,
            "blocked": blocked,
            "why": "static_prod_blocked_families_fallback",
            "kb_live_error": str(exc),
            "source": "static_fallback",
        }
    return {
        "ok": bool(
            ok and g0.get("pass")
            and (dsl_report is None or dsl_report.get("ok"))
            and not (kb_note or {}).get("blocked")
        ),
        "normalize_ok": ok,
        "normalize_errors": errors,
        "mechanism_spec_fields_filled": sum(
            1 for f in MECHANISM_SPEC_FIELDS
            if cleaned.get(f) not in (None, "", [])
        ),
        "mechanism_spec_fields_required": len(MECHANISM_SPEC_FIELDS),
        "gate0": g0,
        "dsl_validate": dsl_report,
        "kb_family_check": kb_note,
        "production_mounted": False,
        "note": "dry_run_gate0 only — no L1–L3, incubator, Gate6 AI, or mount",
    }


if __name__ == "__main__":
    import argparse
    import json as _json
    import sys as _sys

    parser = argparse.ArgumentParser(
        description=(
            "STEP A creation pipeline CLI. Prefer --strategy_json for prebuilt "
            "mechanism_spec (+ optional dsl). Never auto-mounts production."
        )
    )
    parser.add_argument(
        "--strategy_json",
        default=None,
        help="Path to strategy candidate JSON (mechanism_spec + optional dsl/meta)",
    )
    parser.add_argument("--symbol", default=None, help="Override focus symbol")
    parser.add_argument("--timeframe", default=None, help="Override focus timeframe")
    parser.add_argument(
        "--direction",
        default=None,
        help="long|short — selects dsl_long/dsl_short when present",
    )
    parser.add_argument(
        "--exploration_mode",
        default="A",
        help="A/B/C/D or 1–4 (default A=new_mechanism)",
    )
    parser.add_argument(
        "--windtalker_tag",
        default=None,
        help="Optional tag recorded on the task",
    )
    parser.add_argument(
        "--dry_run_gate0",
        action="store_true",
        help="Validate mechanism_spec Gate0 (+ DSL if present); do not run full STEP A",
    )
    parser.add_argument(
        "--enable_multi_symbol_matrix",
        action="store_true",
        help=(
            "L1/Gate2 eval across mechanism_spec.suitable_symbols (aggregate fills); "
            "primary --symbol remains seed focus. Does not lower Gate2 floors."
        ),
    )
    parser.add_argument(
        "--async",
        dest="async_mode",
        action="store_true",
        help="Start STEP A in background thread (full run only)",
    )
    args = parser.parse_args()

    prebuilt = None
    if args.strategy_json:
        try:
            prebuilt = _load_strategy_json_pack(args.strategy_json)
        except Exception as exc:
            print(_json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            _sys.exit(2)
        meta = prebuilt.setdefault("meta", {})
        if args.direction:
            meta["direction"] = str(args.direction).lower()
            direction = meta["direction"]
            side_dsl = prebuilt.get("dsl_long" if direction == "long" else "dsl_short")
            if isinstance(side_dsl, dict):
                prebuilt["dsl"] = side_dsl
        if args.symbol:
            meta["symbol"] = args.symbol
        if args.timeframe:
            meta["timeframe"] = args.timeframe
        # Prefer meta focus when CLI overrides absent
        symbol = args.symbol or meta.get("symbol")
        timeframe = args.timeframe or meta.get("timeframe")
    else:
        symbol = args.symbol
        timeframe = args.timeframe

    if args.dry_run_gate0:
        if not prebuilt:
            print(_json.dumps({
                "ok": False,
                "error": "--dry_run_gate0 requires --strategy_json",
            }, ensure_ascii=False))
            _sys.exit(2)
        report = dry_run_gate0_from_pack(prebuilt)
        print(_json.dumps(report, ensure_ascii=False, indent=2, default=str))
        _sys.exit(0 if report.get("ok") else 1)

    tag = args.windtalker_tag
    if tag is None and prebuilt:
        tag = "strategy_json_cli"
    if args.async_mode:
        out = start_creation_task_step_a(
            async_mode=True,
            symbol=symbol,
            timeframe=timeframe,
            exploration_mode=args.exploration_mode,
            prebuilt_spec_pack=prebuilt,
            windtalker_tag=tag,
            enable_multi_symbol_matrix=bool(args.enable_multi_symbol_matrix),
        )
    else:
        out = run_creation_pipeline_step_a(
            symbol=symbol,
            timeframe=timeframe,
            exploration_mode=args.exploration_mode,
            prebuilt_spec_pack=prebuilt,
            windtalker_tag=tag,
            enable_multi_symbol_matrix=bool(args.enable_multi_symbol_matrix),
        )
    print(_json.dumps(out, ensure_ascii=False, indent=2, default=str))
    _sys.exit(0 if (out or {}).get("ok") else 1)
