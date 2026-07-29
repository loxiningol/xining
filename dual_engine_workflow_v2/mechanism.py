# -*- coding: utf-8 -*-
"""Mechanism statement, fingerprint, duplicate detection, drift helpers."""
from __future__ import print_function

import hashlib
import json
import re

from .config import (
    FINGERPRINT_FIELDS,
    DUPLICATE_KEY_FIELDS,
    FORBIDDEN_CORE_FEATURES,
)
from .protocol import validate_mechanism_statement, content_hash


def build_fingerprint_from_statement(stmt, extras=None):
    stmt = stmt or {}
    fp = {
        "forced_actor": stmt.get("forced_actor") or "",
        "forced_behavior": stmt.get("observable_behavior") or "",
        "distortion_type": stmt.get("predictable_distortion") or "",
        "information_source": (extras or {}).get("information_source") or "market_microstructure",
        "activation_regime": stmt.get("activation_regime") or "",
        "entry_causality": stmt.get("causal_entry") or "",
        "exit_causality": stmt.get("causal_exit") or "",
        "counterparty": stmt.get("counterparty") or "",
        "holding_horizon": (extras or {}).get("holding_horizon") or "",
        "failure_mode": stmt.get("invalidation_regime") or "",
        "dependence_structure": (extras or {}).get("dependence_structure") or "single_market",
    }
    for k in FINGERPRINT_FIELDS:
        fp.setdefault(k, "")
        fp[k] = _norm_text(fp[k])
    fp["fingerprint_hash"] = fingerprint_hash(fp)
    fp["family_hash"] = family_hash(fp)
    return fp


def _norm_text(s):
    s = str(s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s[:500]


def fingerprint_hash(fp):
    keys = list(FINGERPRINT_FIELDS)
    payload = {k: _norm_text(fp.get(k)) for k in keys}
    return content_hash(payload)[:32]


def family_hash(fp):
    payload = {k: _norm_text(fp.get(k)) for k in DUPLICATE_KEY_FIELDS}
    return content_hash(payload)[:32]


def similarity_score(fp_a, fp_b):
    """Jaccard-like token overlap on duplicate key fields; 0..1."""
    if not fp_a or not fp_b:
        return 0.0
    scores = []
    for k in DUPLICATE_KEY_FIELDS:
        ta = set(_norm_text(fp_a.get(k)).split())
        tb = set(_norm_text(fp_b.get(k)).split())
        if not ta and not tb:
            scores.append(1.0)
            continue
        if not ta or not tb:
            scores.append(0.0)
            continue
        scores.append(len(ta & tb) / float(len(ta | tb)))
    return round(sum(scores) / max(len(scores), 1), 4)


def is_duplicate(fp_new, existing_fps, threshold=0.72):
    hits = []
    for row in existing_fps or []:
        fp = row.get("fingerprint") if isinstance(row, dict) and "fingerprint" in row else row
        if not isinstance(fp, dict):
            continue
        if fp.get("family_hash") and fp_new.get("family_hash") and fp["family_hash"] == fp_new["family_hash"]:
            hits.append({"score": 1.0, "reason": "family_hash_exact", "ref": row})
            continue
        score = similarity_score(fp_new, fp)
        if score >= threshold:
            hits.append({"score": score, "reason": "similarity", "ref": row})
    hits.sort(key=lambda x: -x["score"])
    return bool(hits), hits


def extract_dsl_conditions(dsl):
    """Flatten entry/exit leaf conditions into auditable rows."""
    rows = []
    dsl = dsl or {}

    def walk(node, phase, path):
        if isinstance(node, dict):
            if "left" in node and "op" in node:
                left = node.get("left") or {}
                feat = left.get("feature") or left.get("name") or json.dumps(left, ensure_ascii=False)[:80]
                right = node.get("right")
                rows.append({
                    "condition_id": "%s_%d" % (phase, len(rows) + 1),
                    "phase": phase,
                    "path": path,
                    "feature": str(feat),
                    "op": node.get("op"),
                    "right": right,
                    "condition_description": "%s %s %s" % (feat, node.get("op"), right),
                })
                return
            for k, v in node.items():
                walk(v, phase, path + "/" + str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, phase, path + "[%d]" % i)

    walk(dsl.get("entry"), "entry", "entry")
    walk(dsl.get("exit"), "exit", "exit")
    return rows


def feature_is_forbidden_core(feature):
    f = str(feature or "").lower()
    return any(tok in f for tok in FORBIDDEN_CORE_FEATURES)


def drift_compare(orig_stmt, new_stmt):
    """Return drift classification: none | mild | material | duplicate_copy."""
    fields = (
        "forced_actor", "predictable_distortion", "counterparty",
        "causal_entry", "causal_exit",
    )
    changed = []
    for f in fields:
        a = _norm_text((orig_stmt or {}).get(f))
        b = _norm_text((new_stmt or {}).get(f))
        if a != b:
            changed.append(f)
    if not changed:
        return {"drift": "none", "changed_fields": [], "action": "continue_repair"}
    # material if actor/counterparty/causal_entry change
    material_keys = {"forced_actor", "counterparty", "causal_entry", "predictable_distortion"}
    if set(changed) & material_keys and len(changed) >= 2:
        return {
            "drift": "material",
            "changed_fields": changed,
            "action": "terminate_reopen_as_new",
        }
    if "forced_actor" in changed and "counterparty" in changed:
        return {
            "drift": "material",
            "changed_fields": changed,
            "action": "terminate_reopen_as_new",
        }
    return {
        "drift": "mild",
        "changed_fields": changed,
        "action": "update_statement_version",
    }


def require_statement_or_reject(stmt):
    ok, errors, cleaned = validate_mechanism_statement(stmt)
    return ok, errors, cleaned
