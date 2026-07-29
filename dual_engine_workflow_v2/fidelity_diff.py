# -*- coding: utf-8 -*-
"""Codex fidelity diff — mechanism_fidelity_diff.json each implementation/repair round."""
from __future__ import print_function

import copy
import hashlib
import json
import re
from pathlib import Path

from .config import _now, _atomic
from .mechanism import extract_dsl_conditions, feature_is_forbidden_core
from .step_a_config import (
    FIDELITY_DIFF_DIR,
    STEP_A_SCHEMA,
    STEP_A_CODE_VERSION,
    ensure_step_a_dirs,
)


def _tokens(s):
    return set(re.findall(r"[a-z0-9_\u4e00-\u9fff]+", str(s or "").lower()))


def _overlap(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def build_fidelity_diff(mechanism_spec, dsl, round_i=0, repair_type=None, prior_diff=None):
    """Compare implementation against immutable GLM mechanism_spec."""
    spec = mechanism_spec or {}
    dsl = dsl or {}
    conditions = extract_dsl_conditions(dsl)
    features = [c.get("feature") for c in conditions]

    entry_overlap = _overlap(spec.get("entry_logic"), json.dumps(dsl.get("entry"), ensure_ascii=False))
    exit_overlap = _overlap(spec.get("exit_logic"), json.dumps(dsl.get("exit"), ensure_ascii=False))

    forbidden_hits = []
    for f in features:
        if feature_is_forbidden_core(f):
            # check against forbidden_transformations
            forbidden_hits.append(f)

    missing_sl = not any(
        "stop" in str(x).lower() or "sl" == str(x).lower()
        for x in (features + [spec.get("stop_logic"), dsl.get("stop_loss"), dsl.get("sl_pct")])
    )
    # Production constraint: SL is attached at live layer; DSL may omit — mark as warning not auto-fail
    sl_present_in_spec = bool(str(spec.get("stop_logic") or "").strip())
    tp_present_in_spec = bool(str(spec.get("take_profit_logic") or "").strip())

    non_neg = [str(x).lower() for x in (spec.get("non_negotiable_rules") or [])]
    violated = []
    for rule in non_neg:
        if "rsi" in rule and any("rsi" in str(f).lower() for f in features):
            violated.append(rule)
        if "macd" in rule and any("macd" in str(f).lower() for f in features):
            violated.append(rule)

    # Drift vs prior
    drift_flags = []
    if prior_diff and prior_diff.get("feature_set"):
        prev = set(prior_diff.get("feature_set") or [])
        cur = set(str(f) for f in features)
        added = sorted(cur - prev)
        removed = sorted(prev - cur)
        if added or removed:
            drift_flags.append({"added": added, "removed": removed})

    fidelity_score = round(
        0.35 * entry_overlap
        + 0.25 * exit_overlap
        + (0.2 if not forbidden_hits else 0.0)
        + (0.1 if sl_present_in_spec else 0.05)
        + (0.1 if tp_present_in_spec else 0.05)
        - 0.15 * len(violated),
        4,
    )
    pass_fidelity = (
        fidelity_score >= 0.35
        and not violated
        and entry_overlap >= 0.05  # weak lexical; structural gate still required
        and bool(conditions)
    )

    diff = {
        "schema": STEP_A_SCHEMA,
        "artifact": "mechanism_fidelity_diff",
        "code_version": STEP_A_CODE_VERSION,
        "round": int(round_i),
        "repair_type": repair_type,
        "created_at": _now(),
        "mechanism_id": spec.get("mechanism_id"),
        "mechanism_family": spec.get("mechanism_family"),
        "feature_set": [str(f) for f in features],
        "condition_count": len(conditions),
        "entry_logic_overlap": round(entry_overlap, 4),
        "exit_logic_overlap": round(exit_overlap, 4),
        "forbidden_core_features_present": forbidden_hits,
        "non_negotiable_violations": violated,
        "stop_logic_in_spec": sl_present_in_spec,
        "take_profit_logic_in_spec": tp_present_in_spec,
        "dsl_has_entry": bool(dsl.get("entry")),
        "dsl_has_exit": bool(dsl.get("exit")),
        "feature_drift_vs_prior": drift_flags,
        "fidelity_score": fidelity_score,
        "pass": bool(pass_fidelity),
        "failure_class": None if pass_fidelity else (
            "implementation_failure" if violated or forbidden_hits else "implementation_failure"
        ),
        "notes": [],
    }
    if missing_sl and not sl_present_in_spec:
        diff["notes"].append("stop_logic_missing_in_spec")
    if not conditions:
        diff["notes"].append("no_dsl_conditions")
        diff["pass"] = False
    return diff


def save_fidelity_diff(task_id, diff):
    ensure_step_a_dirs()
    from . import step_a_config as sc
    path = Path(sc.FIDELITY_DIFF_DIR) / ("%s_r%s_mechanism_fidelity_diff.json" % (
        task_id, diff.get("round", 0)))
    _atomic(path, diff)
    return str(path)
