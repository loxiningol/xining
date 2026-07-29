# -*- coding: utf-8 -*-
"""Structured GLM↔Codex exchange protocol — no free-text-only handoff."""
from __future__ import print_function

import hashlib
import json
import time
import uuid

from .config import (
    MECHANISM_STATEMENT_FIELDS,
    WORKFLOW_VERSION,
    CODE_VERSION,
    MIGRATION_VERSION,
    _now,
)


class ProtocolError(ValueError):
    pass


def new_call_id(prefix="mc"):
    return "%s_%s_%s" % (prefix, time.strftime("%Y%m%d%H%M%S"), uuid.uuid4().hex[:8])


def content_hash(obj):
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def envelope(role, payload, call_id=None, parent_call_id=None, purpose=None):
    """Wrap a structured message between GLM and Codex."""
    body = {
        "schema": "qiyu_glm_codex_protocol_v1",
        "workflow_version": WORKFLOW_VERSION,
        "migration_version": MIGRATION_VERSION,
        "code_version": CODE_VERSION,
        "role": role,  # glm | codex | system
        "purpose": purpose or "",
        "call_id": call_id or new_call_id(),
        "parent_call_id": parent_call_id,
        "payload": payload,
        "created_at": _now(),
    }
    body["content_hash"] = content_hash(payload)
    return body


def validate_envelope(msg, expect_role=None):
    if not isinstance(msg, dict):
        raise ProtocolError("envelope must be object")
    if msg.get("schema") != "qiyu_glm_codex_protocol_v1":
        raise ProtocolError("unsupported protocol schema")
    if expect_role and msg.get("role") != expect_role:
        raise ProtocolError("role mismatch: expect %s got %s" % (expect_role, msg.get("role")))
    payload = msg.get("payload")
    if not isinstance(payload, dict):
        raise ProtocolError("payload must be object")
    expected = content_hash(payload)
    if msg.get("content_hash") and msg.get("content_hash") != expected:
        raise ProtocolError("content_hash mismatch")
    if not msg.get("call_id"):
        raise ProtocolError("call_id required")
    return True


def validate_mechanism_statement(stmt):
    """All required fields must be non-empty; forbidden_substitutions list."""
    errors = []
    if not isinstance(stmt, dict):
        return False, ["mechanism_statement must be object"], {}
    cleaned = {}
    for field in MECHANISM_STATEMENT_FIELDS:
        val = stmt.get(field)
        if field == "forbidden_substitutions":
            if val is None:
                cleaned[field] = []
            elif isinstance(val, list):
                cleaned[field] = [str(x) for x in val if str(x).strip()]
            else:
                errors.append("forbidden_substitutions must be list")
                cleaned[field] = []
            if not cleaned[field]:
                errors.append("forbidden_substitutions empty")
        else:
            text = str(val or "").strip()
            if not text or text.lower() in ("none", "n/a", "null", "-", "todo"):
                errors.append("missing_or_empty:%s" % field)
            cleaned[field] = text
    return (len(errors) == 0), errors, cleaned


def validate_condition_supplement_request(req):
    """Codex must file this before adding any unapproved condition."""
    required = (
        "problem_solved",
        "causal_link_to_mechanism",
        "reproducible_experiment_without",
        "risk_becomes_dominant_mechanism",
    )
    if not isinstance(req, dict):
        return False, ["request must be object"], {}
    errors = []
    cleaned = dict(req)
    for k in required:
        if not str(req.get(k) or "").strip():
            errors.append("missing:%s" % k)
    cleaned.setdefault("proposed_condition", {})
    cleaned.setdefault("glm_decision", "pending")
    return (len(errors) == 0), errors, cleaned


def parse_ai_json_safe(raw, required_top_keys=None):
    """Parse model JSON; on failure return structured error (no silent success)."""
    if isinstance(raw, dict) and raw.get("parsed") is not None:
        parsed = raw.get("parsed")
        ok = bool(raw.get("ok"))
        err = raw.get("error")
    elif isinstance(raw, dict) and "ok" in raw and "parsed" in raw:
        parsed = raw.get("parsed")
        ok = bool(raw.get("ok"))
        err = raw.get("error")
    else:
        parsed = raw if isinstance(raw, dict) else None
        ok = parsed is not None
        err = None if ok else "not_dict"
    if not ok or not isinstance(parsed, dict):
        return {
            "ok": False,
            "error": err or "parse_failed",
            "parsed": None,
            "retry_allowed": True,
        }
    missing = []
    for k in (required_top_keys or []):
        if k not in parsed:
            missing.append(k)
    if missing:
        return {
            "ok": False,
            "error": "missing_keys:" + ",".join(missing),
            "parsed": parsed,
            "retry_allowed": True,
        }
    return {"ok": True, "parsed": parsed, "error": None, "retry_allowed": False}


def dedupe_guard(seen_hashes, payload):
    h = content_hash(payload)
    if h in seen_hashes:
        return False, h
    seen_hashes.add(h)
    return True, h
