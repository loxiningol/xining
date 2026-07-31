# -*- coding: utf-8 -*-
"""Outcome Attribution — layered credit assignment (rules + evidence, not LLM).

Decomposes a creation/live outcome into mechanism / proxy / signal / strategy /
execution / regime / noise responsibilities. Prevents single-PnL blame.
"""
from __future__ import print_function

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from . import research_ledger as ledger


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def attributions_dir():
    d = _root() / "auto_trade" / "dual_engine" / "outcome_attributions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _norm(probs):
    s = sum(max(0.0, float(v)) for v in probs.values()) or 1.0
    return {k: max(0.0, float(v)) / s for k, v in probs.items()}


def attribute_creation_outcome(
    hypothesis,
    contract_compare=None,
    probe=None,
    multiverse=None,
    antifalsify=None,
    leakage=None,
    efr=None,
    execution=None,
    redteam=None,
    fail_stage=None,
    regime=None,
):
    """Rule engine: assign soft probabilities across failure/success layers."""
    h = hypothesis or {}
    fail_stage = fail_stage or "survived"
    probs = {
        "mechanism_failure": 0.10,
        "proxy_failure": 0.10,
        "signal_failure": 0.10,
        "strategy_param_failure": 0.05,
        "execution_failure": 0.10,
        "regime_mismatch": 0.10,
        "data_quality_issue": 0.05,
        "statistical_noise": 0.15,
        "generator_process_issue": 0.05,
        "success_credit_mechanism": 0.0,
        "success_credit_signal": 0.0,
        "success_credit_execution_model": 0.0,
    }

    if fail_stage == "incomplete_mechanism":
        probs["mechanism_failure"] += 0.55
    elif fail_stage == "naked_probe":
        probs["signal_failure"] += 0.25
        probs["proxy_failure"] += 0.25
        probs["mechanism_failure"] += 0.05
    elif fail_stage == "NO_DIRECTIONAL_EFFECT":
        probs["signal_failure"] += 0.30
        probs["proxy_failure"] += 0.25
        probs["statistical_noise"] += 0.15
    elif fail_stage == "DIRECTIONAL_BUT_SMALL":
        probs["strategy_param_failure"] += 0.20
        probs["execution_failure"] += 0.25
        probs["success_credit_mechanism"] += 0.20
    elif fail_stage == "VOLATILITY_EFFECT_ONLY":
        probs["proxy_failure"] += 0.20
        probs["signal_failure"] += 0.20
        probs["success_credit_mechanism"] += 0.15
    elif fail_stage == "STATE_CONDITIONAL":
        probs["regime_mismatch"] += 0.45
        probs["success_credit_mechanism"] += 0.10
    elif fail_stage == "HORIZON_MISMATCH":
        probs["signal_failure"] += 0.25
        probs["strategy_param_failure"] += 0.30
    elif fail_stage == "EXECUTION_MAPPING_FAILURE":
        probs["execution_failure"] += 0.55
        probs["success_credit_mechanism"] += 0.15
    elif fail_stage == "PROXY_INADEQUATE":
        probs["proxy_failure"] += 0.60
        probs["mechanism_failure"] *= 0.20
    elif fail_stage == "DATA_INADEQUATE":
        probs["data_quality_issue"] += 0.65
        probs["mechanism_failure"] *= 0.10
    elif fail_stage == "SAMPLE_INADEQUATE":
        probs["statistical_noise"] += 0.55
        probs["data_quality_issue"] += 0.15
        probs["mechanism_failure"] *= 0.20
    elif fail_stage == "NEAR_MISS_DIAGNOSTIC":
        probs["statistical_noise"] += 0.30
        probs["execution_failure"] += 0.15
        probs["success_credit_mechanism"] += 0.10
    elif fail_stage in ("MECHANISM_CONTRADICTED", "FAMILY_EXHAUSTED"):
        # These states may only be emitted by aggregate coverage logic, never a leaf probe.
        probs["mechanism_failure"] += 0.70
    elif fail_stage == "multiverse":
        probs["regime_mismatch"] += 0.30
        probs["signal_failure"] += 0.20
        probs["statistical_noise"] += 0.15
    elif fail_stage == "antifalsify":
        probs["mechanism_failure"] += 0.25
        probs["proxy_failure"] += 0.25
        oppose = int((antifalsify or {}).get("oppose_n") or 0)
        if oppose >= 2:
            probs["mechanism_failure"] += 0.15
    elif fail_stage == "leakage":
        probs["data_quality_issue"] += 0.55
        probs["signal_failure"] += 0.20
    elif fail_stage == "efr":
        probs["execution_failure"] += 0.35
        probs["strategy_param_failure"] += 0.15
    elif fail_stage == "execution":
        probs["execution_failure"] += 0.50
    elif fail_stage == "redteam":
        probs["mechanism_failure"] += 0.20
        probs["generator_process_issue"] += 0.25
        probs["signal_failure"] += 0.15
    elif fail_stage == "judge":
        probs["generator_process_issue"] += 0.20
        probs["statistical_noise"] += 0.20
    elif fail_stage == "multiple_testing":
        probs["statistical_noise"] += 0.25
        probs["generator_process_issue"] += 0.25
        probs["signal_failure"] += 0.15
    elif fail_stage in ("survived", "READY_FOR_ASSEMBLY"):
        probs["success_credit_mechanism"] = 0.35
        probs["success_credit_signal"] = 0.30
        probs["success_credit_execution_model"] = 0.15
        probs["statistical_noise"] = 0.10
        # zero out failure mass for survival path baseline
        for k in list(probs.keys()):
            if k.startswith("success_") or k == "statistical_noise":
                continue
            probs[k] *= 0.15

    # Evidence adjustments
    mean_net = (probe or {}).get("mean_net")
    axes = (probe or {}).get("evidence_axes") or {}
    if axes.get("statistical_direction"):
        probs["success_credit_mechanism"] += 0.15
        probs["signal_failure"] *= 0.55
    if axes.get("economic_magnitude") and not axes.get("execution_feasibility"):
        probs["execution_failure"] += 0.20
        probs["mechanism_failure"] *= 0.50
    if mean_net is not None and float(mean_net) > 0 and fail_stage in (
        "efr", "execution",
    ):
        # edge exists but friction/exec killed it — do NOT punish mechanism hard
        probs["mechanism_failure"] *= 0.4
        probs["proxy_failure"] *= 0.5
        probs["execution_failure"] += 0.25

    if (leakage or {}).get("passed") is False:
        probs["data_quality_issue"] += 0.2

    if (multiverse or {}).get("need_regime_filter"):
        probs["regime_mismatch"] += 0.15

    cc = contract_compare or {}
    if cc.get("direction_ok") is False:
        probs["mechanism_failure"] += 0.12
        probs["signal_failure"] += 0.12
    elif cc.get("direction_ok") is True and fail_stage in ("efr", "execution"):
        probs["success_credit_mechanism"] = max(
            probs.get("success_credit_mechanism") or 0, 0.2
        )

    probs = _norm(probs)
    primary = max(probs.items(), key=lambda kv: kv[1])[0]

    failure_codes = list(
        (probe or {}).get("failure_codes")
        or []
    )
    # Fine codes adjust soft mass without overriding stage logic
    if "data_insufficient" in failure_codes:
        probs["data_quality_issue"] = float(probs.get("data_quality_issue") or 0) + 0.08
        probs["mechanism_failure"] = float(probs.get("mechanism_failure") or 0) * 0.85
    if "execution_mapping_failure" in failure_codes or "spread_dominated" in failure_codes:
        probs["execution_failure"] = float(probs.get("execution_failure") or 0) + 0.08
        probs["mechanism_failure"] = float(probs.get("mechanism_failure") or 0) * 0.85
    if "state_conditional_only" in failure_codes:
        probs["regime_mismatch"] = float(probs.get("regime_mismatch") or 0) + 0.08
    if "horizon_mismatch" in failure_codes:
        probs["strategy_param_failure"] = float(probs.get("strategy_param_failure") or 0) + 0.08
    if failure_codes:
        probs = _norm(probs)
        primary = max(probs.items(), key=lambda kv: kv[1])[0]

    # Reward vector (NOT collapsed to PnL)
    reward_vector = {
        "prediction_accuracy": 1.0 if cc.get("direction_ok") else (
            0.0 if cc.get("direction_ok") is False else 0.5
        ),
        "calibration": float(cc.get("hit_rate") or 0.5),
        "mechanism_consistency": 1.0 if (antifalsify or {}).get("passed") else 0.0,
        "oos_reproducibility": 1.0 if (multiverse or {}).get("passed") else 0.0,
        "net_edge": max(0.0, min(1.0, float(mean_net or 0) * 500.0)),
        "execution_accuracy": 1.0 if (execution or {}).get("passed") else (
            0.0 if execution is not None else 0.5
        ),
        "capacity_accuracy": 1.0 if (efr or {}).get("passed") else (
            0.0 if efr is not None else 0.5
        ),
        "novelty": 0.5,
        "portfolio_complementarity": 0.5,
        "research_cost": 0.3,
        "overfitting_risk": 0.7 if fail_stage == "multiple_testing" else 0.3,
    }

    out = {
        "ok": True,
        "schema": "qiyu_outcome_attribution_v1",
        "hypothesis_id": h.get("hypothesis_id"),
        "mechanism_id": h.get("mechanism_id"),
        "mechanism_tree_id": h.get("mechanism_tree_id"),
        "mechanism_branch_id": h.get("mechanism_branch_id"),
        "generator": h.get("source"),
        "family": h.get("family"),
        "fail_stage": fail_stage,
        "failure_codes": failure_codes,
        "market_regime": regime or "unspecified",
        "responsibility": probs,
        "primary_attribution": primary,
        "reward_vector": reward_vector,
        "gross_prediction_error": (
            None if mean_net is None else -float(mean_net)
            if fail_stage not in ("survived", "READY_FOR_ASSEMBLY") else 0.0
        ),
        "llm_decides_blame": False,
        "note_zh": (
            "分层归因（规则引擎）；禁止用总PnL给所有模块打同一分。"
            "主因=%s" % primary
        ),
        "at": _now(),
    }
    return out


def persist(attribution, run_id=None):
    a = dict(attribution or {})
    oid = "O_%s_%s_%s_%s" % (
        (a.get("hypothesis_id") or "na")[:40],
        datetime.now().strftime("%H%M%S_%f"),
        str(run_id or "run")[-16:],
        uuid.uuid4().hex[:6],
    )
    a["outcome_id"] = oid
    path = attributions_dir() / ("%s.json" % oid)
    path.write_text(json.dumps(a, ensure_ascii=False, indent=2), encoding="utf-8")
    if run_id:
        ledger.append_event({
            "event_type": "outcome_attribution",
            "outcome_id": oid,
            "hypothesis_id": a.get("hypothesis_id"),
            "primary": a.get("primary_attribution"),
            "fail_stage": a.get("fail_stage"),
        }, run_id=run_id)
    return a


def probe():
    return {
        "ok": True,
        "provider": "outcome_attribution_v1",
        "llm_decides_blame": False,
        "dir": str(attributions_dir()),
        "at": _now(),
    }
