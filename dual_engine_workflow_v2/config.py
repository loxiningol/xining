# -*- coding: utf-8 -*-
"""Feature flags and version constants for strategy workflow v2."""
from __future__ import print_function

import json
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
DUAL_DIR = AUTO_DIR / "dual_engine"
FLAGS_PATH = DUAL_DIR / "workflow_flags.json"
WF_DIR = DUAL_DIR / "workflow_v2"
ARTIFACTS_DIR = WF_DIR / "artifacts"

WORKFLOW_VERSION = "v2"
MIGRATION_VERSION = "001"
CODE_VERSION = "strategy_workflow_v2_step_a_20260726"
BACKTEST_VERSION = "backtest_engine_v2"
DATA_VERSION = "live_frames_v1"

# Production constraints for first validation task (§十三)
LEVERAGE = 20
STOP_LOSS_PCT = 0.009
INITIAL_POSITION_PCT = 0.30
TARGET_TRADES_PER_DAY = (0.5, 1.0)
MAX_REPAIR_ROUNDS = 3
AUX_WR_MEAN_GATE = 75.0  # auxiliary only; cannot override fatal

FAILURE_LEVELS = (
    "hypothesis_evidence_failure",
    "observability_failure",
    "representation_failure",
    "engineering_failure",
    "execution_cost_failure",
    "robustness_failure",
    "test_standard_conflict",
    "frequency_target_conflict",
    "mechanism_drift",
    "duplicate_mechanism",
    "insufficient_evidence",
)

EXPLORATION_MODES = ("A", "B", "C", "D")

MECHANISM_STATEMENT_FIELDS = (
    "forced_actor",
    "observable_behavior",
    "predictable_distortion",
    "persistence_reason",
    "counterparty",
    "activation_regime",
    "invalidation_regime",
    "causal_entry",
    "causal_exit",
    "forbidden_substitutions",
)

FINGERPRINT_FIELDS = (
    "forced_actor",
    "forced_behavior",
    "distortion_type",
    "information_source",
    "activation_regime",
    "entry_causality",
    "exit_causality",
    "counterparty",
    "holding_horizon",
    "failure_mode",
    "dependence_structure",
)

DUPLICATE_KEY_FIELDS = (
    "forced_actor",
    "forced_behavior",
    "distortion_type",
    "entry_causality",
    "counterparty",
)

CONDITION_CLASSIFICATIONS = (
    "mechanism_core",
    "mechanism_observation",
    "execution_safety",
    "approved_filter",
    "unapproved_filter",
    "redundant_condition",
    "mechanism_substitution",
)

CONDITION_ACTIONS = ("retain", "revise", "remove", "reject_strategy")

# Traditional indicators forbidden as *unauthorized* Codex invents.
# Formally mapped recipe factors (rsi14 / bb_*) are authorized via admitted recipe.
FORBIDDEN_CORE_FEATURES = (
    "macd", "ema", "sma", "supertrend", "adx",
    "cci",  # also used by legacy repair — blocked unless GLM-approved
    # Keep "boll"/"rsi" tokens for narrative injection bans; recipe-authorized
    # dsl features still pass via allowed_features from GENERIC_FACTOR_TO_DSL.
    "bollinger_fade",
)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _read(path, default=None):
    path = Path(path)
    if not path.exists():
        return {} if default is None else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def default_flags():
    return {
        "schema": "qiyu_workflow_flags_v1",
        "workflow_version": "v1",
        "creation_entry": "legacy",  # legacy | v2
        "migration_version": None,
        "upgrade_status": "implementing",
        "acceptance": {k: None for k in list("ABCDEFGH")},
        "acceptance_passed_at": None,
        "switched_at": None,
        "pause": {
            "non_live_creation": True,
            "evolve_timer": True,
        },
        "notes": [],
        "updated_at": _now(),
    }


def load_flags():
    DUAL_DIR.mkdir(parents=True, exist_ok=True)
    data = _read(FLAGS_PATH, None)
    if not isinstance(data, dict) or not data:
        data = default_flags()
        _atomic(FLAGS_PATH, data)
    return data


def save_flags(patch=None):
    flags = load_flags()
    if patch:
        # shallow merge + nested acceptance
        for k, v in patch.items():
            if k == "acceptance" and isinstance(v, dict):
                acc = dict(flags.get("acceptance") or {})
                acc.update(v)
                flags["acceptance"] = acc
            else:
                flags[k] = v
    flags["updated_at"] = _now()
    _atomic(FLAGS_PATH, flags)
    return flags


def creation_entry():
    return str(load_flags().get("creation_entry") or "legacy")


def set_creation_entry(entry, note=None):
    entry = str(entry or "legacy")
    if entry not in ("legacy", "v2"):
        raise ValueError("creation_entry must be legacy|v2")
    patch = {"creation_entry": entry, "workflow_version": "v2" if entry == "v2" else "v1"}
    if entry == "v2":
        patch["switched_at"] = _now()
        patch["upgrade_status"] = "switched_to_v2"
    else:
        patch["upgrade_status"] = "rolled_back_to_legacy"
    if note:
        flags = load_flags()
        notes = list(flags.get("notes") or [])
        notes.append({"at": _now(), "note": note})
        patch["notes"] = notes[-50:]
    return save_flags(patch)


def ensure_dirs():
    for p in (DUAL_DIR, WF_DIR, ARTIFACTS_DIR, WF_DIR / "migrations"):
        p.mkdir(parents=True, exist_ok=True)
