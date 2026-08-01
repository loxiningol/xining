# -*- coding: utf-8 -*-
"""Two-lane job coordinator for the sole strategy-creation pipeline.

Cursor, Codex, human, Web and timer submissions all become immutable jobs.
Two named pipelines (管道1 / 管道2) may execute different research directions
concurrently while each mission retains an isolated ledger, blackboard,
artifact directory and formal-review handoff receipt.
"""
from __future__ import print_function

import argparse
import errno
import hashlib
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from .process_safe_state import atomic_write_json, process_lock, unique_id


SCHEMA = "qiyu_parallel_creation_job_v1"
ALLOWED_SOURCES = ("cursor", "codex", "human", "web", "system_timer", "direct")
PIPELINE_LABELS = {1: "管道1", 2: "管道2"}
PROGRESS_STAGES = (
    ("queued", "排队等待", 0),
    ("claimed", "已认领研究槽", 5),
    ("contract", "研究契约编译", 12),
    ("population", "机制种群生成", 22),
    ("committee", "异构委员会评估", 35),
    ("map_elites", "质量—多样性搜索（MAP-Elites）", 48),
    ("probe", "裸探测 / 摩擦检验", 60),
    ("antifalsify", "抗证伪与稳健性", 72),
    ("assembly", "蓝图装配与门控", 82),
    ("formal_review", "交予四阶段复核", 92),
    ("done", "本轮结束", 100),
)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def base_dir():
    path = _root() / "auto_trade" / "dual_engine" / "parallel_creation"
    for name in ("pending", "running", "completed", "failed", "artifacts", "locks"):
        (path / name).mkdir(parents=True, exist_ok=True)
    return path


def _normalize_identity_text(value):
    return " ".join(str(value or "").strip().lower().split())


_RUNTIME_CONTRACT_FIELDS = frozenset((
    "compiled_at", "compiled_ts",
    "verified_at", "verified_ts",
    "validated_at", "validated_ts",
    "created_at", "created_ts",
    "updated_at", "updated_ts",
    "generated_at", "generated_ts",
    "bound_at", "bound_ts",
    "loaded_at", "loaded_ts",
    "observed_at", "observed_ts",
    "last_seen_at", "last_seen_ts",
))


def _semantic_contract_identity(value):
    """Remove audit-clock metadata while retaining every research decision.

    A freshly compiled copy of an otherwise identical contract must not evade
    active-job deduplication merely because its compiler/validator timestamps
    changed.  The allowlist is deliberately narrow: targets, clauses, feature
    order, thresholds, data versions and contract ids remain identity-bearing.
    """
    if isinstance(value, dict):
        return {
            str(key): _semantic_contract_identity(item)
            for key, item in value.items()
            if str(key).strip().lower() not in _RUNTIME_CONTRACT_FIELDS
        }
    if isinstance(value, list):
        return [_semantic_contract_identity(item) for item in value]
    if isinstance(value, tuple):
        return [_semantic_contract_identity(item) for item in value]
    return value


def _direction_key(
    text, symbol="", timeframe="", direction="", brief="",
    data_version="", code_version="", research_contract=None,
    mutation_contract=None, skip_llm=None, max_loops=None,
):
    """Stable identity for the complete research request.

    The historical implementation hashed only ``research_direction``.  That
    conflated different markets while also allowing semantically identical
    jobs with a changed brief to evade identity checks.
    """
    identity = {
        "research_direction": _normalize_identity_text(text),
        "symbol": _normalize_identity_text(symbol).upper(),
        "timeframe": _normalize_identity_text(timeframe),
        "trade_direction": _normalize_identity_text(direction),
        "brief": _normalize_identity_text(brief),
        "data_version": _normalize_identity_text(data_version),
        "code_version": _normalize_identity_text(code_version),
        "research_contract": _semantic_contract_identity(research_contract),
        "mutation_contract": mutation_contract,
        "skip_llm": skip_llm,
        "max_loops": max_loops,
    }
    canonical = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _job_path(state, job_id):
    return base_dir() / state / ("%s.json" % job_id)


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _json_safe_copy(value):
    if value is None:
        return None
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _canonical_json_hash(value):
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False, default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_terminal_parent_job(parent_id):
    """Load an immutable terminal parent receipt by its recorded job id."""
    parent_id = str(parent_id or "").strip()
    if (
        not parent_id or len(parent_id) > 200
        or not re.match(r"^[0-9A-Za-z_.-]+$", parent_id)
        or parent_id in (".", "..")
    ):
        return None, None, None
    for state in ("completed", "failed"):
        direct = _job_path(state, parent_id)
        if direct.exists():
            row = _read(direct)
            if isinstance(row, dict) and str(row.get("job_id") or "") == parent_id:
                return row, state, direct
        # Legacy receipts were not always named after job_id.  Read terminal
        # records rather than trusting a caller-controlled path.
        for path in (base_dir() / state).glob("*.json"):
            if path == direct:
                continue
            row = _read(path)
            if isinstance(row, dict) and str(row.get("job_id") or "") == parent_id:
                return row, state, path
    return None, None, None


def _parent_evidence_payload(parent, state):
    """Select the result/failure evidence whose digest binds a mutation."""
    return {
        "job_id": parent.get("job_id"),
        "terminal_state": state,
        "outcome": parent.get("outcome"),
        "status_code": parent.get("status_code"),
        "error": parent.get("error"),
        "failure_evidence": parent.get("failure_evidence"),
        "result": parent.get("result"),
    }


def _gate_token(value):
    return re.sub(r"[^0-9a-zA-Z_\u4e00-\u9fff]+", "_", str(value or "").strip().lower()).strip("_")


def _evidence_mentions_gate(value, failed_gate, depth=0, visited=None,
                            failure_context=False):
    """Prove that a named gate failed; mere presence of its metric is insufficient."""
    if depth > 10:
        return False
    target = _gate_token(failed_gate)
    if not target:
        return False
    if visited is None:
        visited = [0]
    visited[0] += 1
    if visited[0] > 5000:
        return False
    if isinstance(value, dict):
        for key, item in value.items():
            key_token = _gate_token(key)
            is_failure_field = key_token in (
                "failed_gate", "fail_stage", "failed_stage", "failed_test",
                "failed_tests", "failure_code", "failure_codes", "reject_reason",
                "reject_reasons", "abort_reason", "error",
            )
            if key_token == target:
                if item is False:
                    return True
                if isinstance(item, dict) and (
                    item.get("passed") is False
                    or item.get("pass") is False
                    or item.get("ok") is False
                    or item.get("rejected") is True
                    or str(item.get("status") or "").lower() in (
                        "failed", "fail", "rejected", "blocked",
                    )
                ):
                    return True
            if _evidence_mentions_gate(
                item, target, depth + 1, visited,
                failure_context=(failure_context or is_failure_field),
            ):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(
            _evidence_mentions_gate(
                item, target, depth + 1, visited,
                failure_context=failure_context,
            )
            for item in value[:500]
        )
    if isinstance(value, str):
        if failure_context and _gate_token(value) == target:
            return True
        # Named-evidence paths use dots/brackets; prose is split only at
        # explicit separators so "dsr" cannot match "not_dsr_related".
        pieces = re.split(r"[\s\.,;:/|\[\]\(\)]+", value)
        return bool(failure_context and any(
            _gate_token(piece) == target for piece in pieces if piece
        ))
    return False


def _valid_delta_node(value, depth=0, visited=None):
    if depth > 10:
        return False
    if visited is None:
        visited = [0]
    visited[0] += 1
    if visited[0] > 2000:
        return False
    if value is None or isinstance(value, (bool, int, str)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_valid_delta_node(item, depth + 1, visited) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and bool(key.strip())
            and _valid_delta_node(item, depth + 1, visited)
            for key, item in value.items()
        )
    return False


def _has_material_delta(value):
    if isinstance(value, dict):
        return any(_has_material_delta(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_material_delta(item) for item in value)
    return value not in (None, "")


def _delta_respects_allowlist(delta, allowed):
    """Accept explicit delta schemas only, and bind every change to the allowlist."""
    allowed_names = set(_gate_token(item) for item in allowed)
    direct_names = set(_gate_token(key) for key in delta)
    if direct_names and direct_names.issubset(allowed_names):
        return True

    metadata = set(("reason", "rationale", "note", "description", "schema"))
    if isinstance(delta.get("changes"), dict) and delta["changes"]:
        change_names = set(_gate_token(key) for key in delta["changes"])
        outer = set(_gate_token(key) for key in delta if key != "changes")
        return change_names.issubset(allowed_names) and outer.issubset(metadata)

    changed_fields = delta.get("changed_fields")
    if isinstance(changed_fields, list) and changed_fields:
        if any(not isinstance(item, str) or not item.strip() for item in changed_fields):
            return False
        changed_names = set(_gate_token(item) for item in changed_fields)
        permitted_outer = metadata | set(("changed_fields", "before", "after"))
        outer = set(_gate_token(key) for key in delta)
        return changed_names.issubset(allowed_names) and outer.issubset(permitted_outer)

    declared = delta.get("mutation") or delta.get("field")
    if isinstance(declared, str) and _gate_token(declared) in allowed_names:
        permitted_outer = metadata | set(("mutation", "field", "before", "after"))
        return set(_gate_token(key) for key in delta).issubset(permitted_outer)
    return False


_DELTA_METADATA_FIELDS = frozenset((
    "reason", "rationale", "note", "description", "schema",
))


def _identity_values_equal(left, right):
    """Canonical comparison that also ignores contract audit timestamps."""
    left = _semantic_contract_identity(left)
    right = _semantic_contract_identity(right)
    try:
        return json.dumps(
            left, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False, default=str,
        ) == json.dumps(
            right, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False, default=str,
        )
    except Exception:
        return left == right


def _delta_declarations(delta):
    """Normalize the supported structural-delta shapes into bound changes."""
    declarations = []

    def add(name, spec, fallback_before=None, fallback_after=None,
            fallback_before_present=False, fallback_after_present=False):
        token = _gate_token(name)
        if not token:
            return
        if isinstance(spec, dict):
            before_present = "before" in spec
            after_present = "after" in spec
            before = spec.get("before")
            after = spec.get("after")
        else:
            before_present = fallback_before_present
            after_present = fallback_after_present
            before = fallback_before
            after = fallback_after if fallback_after_present else spec
        declarations.append({
            "name": token,
            "before_present": bool(before_present),
            "after_present": bool(after_present),
            "before": before,
            "after": after,
        })

    changes = delta.get("changes")
    if isinstance(changes, dict) and changes:
        for name, spec in changes.items():
            add(name, spec)
        return declarations

    fields = delta.get("changed_fields")
    if isinstance(fields, list) and fields:
        before_container = delta.get("before")
        after_container = delta.get("after")
        for name in fields:
            before_present = False
            after_present = False
            before = None
            after = None
            if isinstance(before_container, dict):
                for key, value in before_container.items():
                    if _gate_token(key) == _gate_token(name):
                        before, before_present = value, True
                        break
            elif len(fields) == 1 and "before" in delta:
                before, before_present = before_container, True
            if isinstance(after_container, dict):
                for key, value in after_container.items():
                    if _gate_token(key) == _gate_token(name):
                        after, after_present = value, True
                        break
            elif len(fields) == 1 and "after" in delta:
                after, after_present = after_container, True
            add(
                name, None, fallback_before=before, fallback_after=after,
                fallback_before_present=before_present,
                fallback_after_present=after_present,
            )
        return declarations

    declared = delta.get("mutation") or delta.get("field")
    if isinstance(declared, str) and declared.strip():
        add(
            declared, None,
            fallback_before=delta.get("before"), fallback_after=delta.get("after"),
            fallback_before_present="before" in delta,
            fallback_after_present="after" in delta,
        )
        return declarations

    for name, spec in delta.items():
        if _gate_token(name) in _DELTA_METADATA_FIELDS:
            continue
        add(name, spec)
    return declarations


def _top_identity_value(field, value):
    if field == "symbol":
        return str(value or "").strip().upper()
    if field == "timeframe":
        return str(value or "").strip().lower()
    if field == "trade_direction":
        return str(value or "").strip().lower()
    if field in ("brief", "research_direction"):
        return _normalize_identity_text(value)
    return value


def _contract_identity_differences(before, after):
    """Return leaf-level semantic differences with bounded traversal."""
    before = _semantic_contract_identity(before)
    after = _semantic_contract_identity(after)
    rows = []
    visited = [0]

    def walk(left, right, path, depth=0):
        if len(rows) >= 512:
            return
        visited[0] += 1
        if depth > 16 or visited[0] > 5000:
            if not _identity_values_equal(left, right):
                rows.append({"path": path, "before": left, "after": right})
            return
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right), key=str):
                walk(left.get(key), right.get(key), "%s.%s" % (path, key), depth + 1)
            return
        if isinstance(left, list) and isinstance(right, list):
            for index in range(max(len(left), len(right))):
                left_value = left[index] if index < len(left) else None
                right_value = right[index] if index < len(right) else None
                walk(
                    left_value, right_value, "%s.%s" % (path, index), depth + 1,
                )
            return
        if not _identity_values_equal(left, right):
            rows.append({"path": path, "before": left, "after": right})

    walk(before, after, "research_contract")
    return rows


def _mutation_path_tokens(path):
    path = str(path or "")
    pieces = [piece for piece in path.split(".") if piece]
    tokens = set((_gate_token(path),))
    if pieces:
        tokens.add(_gate_token(pieces[-1]))
    if path == "trade_direction":
        tokens.update(("trade_direction", "direction"))
    if path.startswith("research_contract."):
        tokens.add(_gate_token(path[len("research_contract."):]))
    low_pieces = [_gate_token(piece) for piece in pieces]
    leaf = low_pieces[-1] if low_pieces else ""
    if "entry_conditions" in low_pieces:
        if leaf in ("value", "threshold"):
            tokens.update(("entry_threshold", "entry_condition_value"))
        elif leaf == "operator":
            tokens.add("entry_operator")
        elif leaf in ("feature", "indicator"):
            tokens.add("entry_feature")
    if "exit_conditions" in low_pieces:
        if leaf in ("value", "threshold"):
            tokens.update(("exit_threshold", "exit_condition_value"))
        elif leaf == "operator":
            tokens.add("exit_operator")
        elif leaf in ("feature", "indicator"):
            tokens.add("exit_feature")
    return set(token for token in tokens if token)


def _validate_mutation_identity(parent, child_identity, allowed, delta):
    """Bind every actual parent->child identity change to an exact declaration."""
    required = (
        "symbol", "timeframe", "trade_direction", "brief",
        "research_direction", "research_contract",
    )
    missing = [field for field in required if field not in parent]
    if missing:
        raise ValueError(
            "mutation parent identity evidence is incomplete: %s" % ",".join(missing)
        )

    actual = []
    for field in required[:-1]:
        before = _top_identity_value(field, parent.get(field))
        after = _top_identity_value(field, child_identity.get(field))
        if not _identity_values_equal(before, after):
            actual.append({"path": field, "before": before, "after": after})
    contract_before = _semantic_contract_identity(parent.get("research_contract"))
    contract_after = _semantic_contract_identity(child_identity.get("research_contract"))
    actual.extend(_contract_identity_differences(contract_before, contract_after))
    if not actual:
        raise ValueError(
            "mutation_contract.structural_delta does not match any actual child identity change"
        )

    declarations = _delta_declarations(delta)
    if not declarations:
        raise ValueError("mutation_contract.structural_delta has no bindable before/after changes")
    allowed_names = set(_gate_token(item) for item in allowed)
    used = set()

    broad_contract = []
    for index, declaration in enumerate(declarations):
        if declaration["name"] != "research_contract":
            continue
        if not declaration["before_present"] or not declaration["after_present"]:
            continue
        if (
            _identity_values_equal(declaration["before"], contract_before)
            and _identity_values_equal(declaration["after"], contract_after)
        ):
            broad_contract.append(index)

    unmatched = []
    for change in actual:
        tokens = _mutation_path_tokens(change["path"])
        matched = None
        for index, declaration in enumerate(declarations):
            name = declaration["name"]
            if name not in allowed_names:
                continue
            if name == "research_contract" and change["path"].startswith("research_contract"):
                if index in broad_contract:
                    matched = index
                    break
                continue
            if name not in tokens:
                continue
            if not declaration["before_present"] or not declaration["after_present"]:
                continue
            if (
                _identity_values_equal(declaration["before"], change["before"])
                and _identity_values_equal(declaration["after"], change["after"])
            ):
                matched = index
                break
        if matched is None:
            unmatched.append(change["path"])
        else:
            used.add(matched)
    if unmatched:
        raise ValueError(
            "mutation child identity changes are undeclared or structurally mismatched: %s"
            % ",".join(unmatched[:20])
        )

    unused = [
        declaration["name"] for index, declaration in enumerate(declarations)
        if index not in used
    ]
    if unused:
        raise ValueError(
            "mutation structural_delta is not evidenced by actual child identity: %s"
            % ",".join(unused[:20])
        )
    return [change["path"] for change in actual]


def _validate_mutation_parent(mutation_contract, child_identity=None):
    """Validate a mutation against a real terminal parent receipt.

    Contracts without ``parent_id`` retain the historical first-round
    behaviour.  A declared parent, however, is a hard lineage claim and must
    be proven before the child can enter the queue.
    """
    if not isinstance(mutation_contract, dict):
        # Parent-less/legacy requests remain outside this lineage check.
        return None
    parent_id = mutation_contract.get("parent_id")
    if parent_id in (None, ""):
        return None
    if not isinstance(parent_id, str) or not parent_id.strip():
        raise ValueError("mutation_contract.parent_id must be a non-empty string")
    parent_id = parent_id.strip()
    failed_gate = mutation_contract.get("failed_gate")
    if not isinstance(failed_gate, str) or not failed_gate.strip():
        raise ValueError("mutation_contract.failed_gate must be a non-empty string")
    allowed = mutation_contract.get("allowed_mutations")
    normalized_allowed = [_gate_token(item) for item in allowed] if isinstance(allowed, list) else []
    if (
        not isinstance(allowed, list) or not allowed
        or any(not isinstance(item, str) or not item.strip() for item in allowed)
        or any(not item for item in normalized_allowed)
        or len(set(normalized_allowed)) != len(allowed)
    ):
        raise ValueError("mutation_contract.allowed_mutations must be a non-empty unique string list")
    delta = mutation_contract.get("structural_delta")
    if (
        not isinstance(delta, dict) or not delta
        or not _valid_delta_node(delta) or not _has_material_delta(delta)
    ):
        raise ValueError("mutation_contract.structural_delta must be a non-empty JSON object")
    if not _delta_respects_allowlist(delta, allowed):
        raise ValueError("mutation_contract.structural_delta changes must match allowed_mutations")
    parent, state, path = _load_terminal_parent_job(parent_id)
    if parent is None:
        raise ValueError("mutation parent job does not exist in completed/failed: %s" % parent_id)
    evidence = _parent_evidence_payload(parent, state)
    if not _evidence_mentions_gate(evidence, failed_gate):
        raise ValueError(
            "mutation_contract.failed_gate is absent from parent evidence: %s" % failed_gate
        )
    if not isinstance(child_identity, dict):
        raise ValueError("mutation child identity is required for a parent-linked mutation")
    actual_changes = _validate_mutation_identity(
        parent, child_identity, allowed, delta,
    )
    evidence_hash = _canonical_json_hash(evidence)
    return {
        "verified": True,
        "parent_job_id": parent_id,
        "parent_terminal_state": state,
        "parent_outcome": parent.get("outcome") or ((parent.get("result") or {}).get("outcome")),
        "failed_gate": failed_gate.strip(),
        "parent_evidence_hash": evidence_hash,
        "parent_receipt_path": str(path),
        "actual_identity_changes": actual_changes,
        "verified_at": _now(),
    }


def _source_digest_version():
    """Derive a code version without requiring a git checkout on production."""
    configured = str(os.environ.get("QIYU_CREATION_CODE_VERSION") or "").strip()
    if configured:
        return configured, "env:QIYU_CREATION_CODE_VERSION"
    package_dir = Path(__file__).resolve().parent
    # Hash the complete Python package instead of maintaining a fragile hand
    # list.  Antifalsification, friction, stress, compiler or helper changes can
    # all change a result and therefore must invalidate a completed-job
    # cooldown.  Include the external DSL/backtest boundary used by STEP A too.
    paths = sorted(package_dir.glob("*.py"), key=lambda item: item.name)
    root_dir = package_dir.parent
    for name in (
        "auto_trade_strategy_dsl.py",
        "backtest_engine_v2.py",
        "auto_trade_human_confirm_pipeline.py",
    ):
        path = root_dir / name
        if path.is_file():
            paths.append(path)
    digest = hashlib.sha256()
    read_n = 0
    for path in paths:
        try:
            try:
                label = str(path.relative_to(root_dir))
            except Exception:
                label = str(path)
            digest.update(label.encode("utf-8"))
            digest.update(path.read_bytes())
            read_n += 1
        except Exception:
            continue
    if read_n:
        return "src-%s" % digest.hexdigest()[:16], "source_digest"
    return "unversioned-code", "unavailable"


def _data_snapshot_version(symbol, timeframe):
    """Best-effort, read-only data snapshot identity used by deduplication."""
    configured = str(os.environ.get("QIYU_RESEARCH_DATA_VERSION") or "").strip()
    if configured:
        return configured, "env:QIYU_RESEARCH_DATA_VERSION"

    # Prefer a local research-store manifest because it changes when long
    # history is refreshed.  Remote-only installations may provide the env
    # version above; the formal cache fallback still prevents stale identity.
    tf = str(timeframe or "5m").strip()
    if tf.upper() != "1H":
        tf = tf.lower()
    research_root = Path(
        os.environ.get("QIYU_RESEARCH_LOCAL_ROOT")
        or (_root() / "auto_trade" / "research_candle_store")
    )
    manifest = research_root / "okx" / "candles" / str(symbol).upper() / tf / "v1" / "manifest.json"
    if manifest.exists():
        try:
            body = manifest.read_bytes()
            return "manifest-%s" % hashlib.sha256(body).hexdigest()[:16], "research_manifest"
        except Exception:
            pass

    try:
        from . import easyquant_bridge as eq
        cache_path = eq.resolve_candle_cache(symbol, timeframe)
        if cache_path.exists():
            stat = cache_path.stat()
            stamp = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1000000000))
            raw = "%s:%s:%s" % (cache_path.name, stat.st_size, stamp)
            return "cache-%s" % hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], "formal_cache_stat"
    except Exception:
        pass
    return "unversioned-data", "unavailable"


def _resolved_versions(symbol, timeframe, data_version=None, code_version=None):
    if data_version not in (None, ""):
        data_value, data_source = str(data_version), "request"
    else:
        data_value, data_source = _data_snapshot_version(symbol, timeframe)
    if code_version not in (None, ""):
        code_value, code_source = str(code_version), "request"
    else:
        code_value, code_source = _source_digest_version()
    return {
        "data_version": data_value,
        "data_version_source": data_source,
        "code_version": code_value,
        "code_version_source": code_source,
    }


def _configured_cooldown(value=None):
    raw = value
    if raw is None:
        raw = os.environ.get("QIYU_CREATION_DEDUPE_COOLDOWN_SECONDS") or 21600
    try:
        return max(0, min(int(float(raw)), 30 * 86400))
    except Exception:
        return 21600


def _find_duplicate_job(job_key, cooldown_seconds, now_ts=None):
    """Return an active or recently completed identical job, without mutation."""
    now_ts = float(now_ts if now_ts is not None else time.time())
    for state in ("running", "pending"):
        for path in (base_dir() / state).glob("*.json"):
            row = _read(path) or {}
            if (row.get("job_key") or row.get("direction_key")) == job_key:
                return row, state, None
    if cooldown_seconds <= 0:
        return None, None, None
    completed = sorted(
        (base_dir() / "completed").glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in completed:
        row = _read(path) or {}
        if (row.get("job_key") or row.get("direction_key")) != job_key:
            continue
        finished_ts = float(row.get("finished_ts") or path.stat().st_mtime)
        age = max(0.0, now_ts - finished_ts)
        if age <= float(cooldown_seconds):
            return row, "completed", max(0, int(round(cooldown_seconds - age)))
        # Sorted newest-first: an older match cannot be inside the cooldown.
        break
    return None, None, None


def _normalize_pipeline(pipeline):
    if pipeline in (None, "", "auto", "any"):
        return None
    try:
        n = int(pipeline)
    except Exception:
        text = str(pipeline).strip()
        if text in ("管道1", "pipeline1", "p1", "slot0"):
            n = 1
        elif text in ("管道2", "pipeline2", "p2", "slot1"):
            n = 2
        else:
            raise ValueError("pipeline must be 1 or 2")
    if n not in (1, 2):
        raise ValueError("pipeline must be 1 or 2")
    return n


def pipeline_from_slot(slot):
    try:
        return int(slot) + 1
    except Exception:
        return None


def slot_from_pipeline(pipeline):
    p = _normalize_pipeline(pipeline)
    return None if p is None else (p - 1)


def _progress_payload(stage_key, detail="", percent=None, extras=None):
    label = stage_key
    pct = percent
    for key, zh, default_pct in PROGRESS_STAGES:
        if key == stage_key:
            label = zh
            if pct is None:
                pct = default_pct
            break
    if pct is None:
        pct = 0
    out = {
        "stage": stage_key,
        "stage_zh": label,
        "detail": str(detail or label),
        "percent": max(0, min(100, int(round(float(pct))))),
        "updated_at": _now(),
        "updated_ts": time.time(),
    }
    if extras:
        out.update(extras)
    return out


def update_job_progress(job_or_path, stage_key, detail="", percent=None, extras=None):
    """Persist live progress onto a running/pending job JSON."""
    path = None
    job = None
    if isinstance(job_or_path, dict):
        job = dict(job_or_path)
        path = job.get("running_path") or job.get("queue_path")
        if not path and job.get("job_id"):
            for state in ("running", "pending"):
                candidate = _job_path(state, job["job_id"])
                if candidate.exists():
                    path = str(candidate)
                    break
    else:
        path = str(job_or_path)
        job = _read(path) or {}
    if not path:
        return None
    progress = _progress_payload(stage_key, detail=detail, percent=percent, extras=extras)
    job["progress"] = progress
    job["heartbeat_at"] = progress["updated_at"]
    job["heartbeat_ts"] = progress["updated_ts"]
    job["status"] = job.get("status") or "研究中"
    if stage_key == "formal_review":
        job["status"] = "正式复核中"
    atomic_write_json(path, job)
    return progress


def report_progress(stage_key, detail="", percent=None, extras=None):
    """Worker-side helper: update the job pointed by QIYU_JOB_PROGRESS_PATH."""
    path = os.environ.get("QIYU_JOB_PROGRESS_PATH")
    if not path:
        return None
    return update_job_progress(path, stage_key, detail=detail, percent=percent, extras=extras)


def start_workers(preferred_pipeline=None):
    """Wake bounded systemd slots; optionally prefer one pipeline."""
    try:
        units = ["qiyu-creation-worker@0.service", "qiyu-creation-worker@1.service"]
        slot = slot_from_pipeline(preferred_pipeline)
        if slot is not None:
            units = ["qiyu-creation-worker@%s.service" % slot] + [
                u for u in units if u != ("qiyu-creation-worker@%s.service" % slot)
            ]
        subprocess.Popen(
            ["systemctl", "start", "--no-block"] + units,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        return True
    except Exception:
        return False


def submit_job(
    source,
    research_direction,
    symbol="ADA-USDT-SWAP",
    timeframe="5m",
    direction="long",
    brief="",
    skip_llm=True,
    max_loops=5,
    wake_workers=True,
    pipeline=None,
    research_contract=None,
    mutation_contract=None,
    data_version=None,
    code_version=None,
    cooldown_seconds=None,
    force=False,
):
    source = str(source or "human").strip().lower()
    if source not in ALLOWED_SOURCES:
        raise ValueError("source must be one of: %s" % ", ".join(ALLOWED_SOURCES))
    research_direction = str(research_direction or brief or "").strip()
    if not research_direction:
        raise ValueError("research_direction is required")
    symbol = str(symbol).upper()
    timeframe = str(timeframe).lower()
    direction = str(direction).strip().lower()
    if direction not in ("long", "short"):
        raise ValueError("direction must be long or short; bidirectional research must be split into two jobs")
    brief = str(brief or research_direction)
    research_contract = _json_safe_copy(research_contract)
    mutation_contract = _json_safe_copy(mutation_contract)
    mutation_parent_validation = _validate_mutation_parent(
        mutation_contract,
        child_identity={
            "symbol": symbol,
            "timeframe": timeframe,
            "trade_direction": direction,
            "brief": brief,
            "research_direction": research_direction,
            "research_contract": research_contract,
        },
    )
    preferred = _normalize_pipeline(pipeline)
    versions = _resolved_versions(
        symbol, timeframe, data_version=data_version, code_version=code_version,
    )
    cooldown_seconds = _configured_cooldown(cooldown_seconds)
    mutation_identity = mutation_contract
    if mutation_parent_validation:
        mutation_identity = {
            "contract": mutation_contract,
            "parent_evidence_hash": mutation_parent_validation["parent_evidence_hash"],
        }
    job_key = _direction_key(
        research_direction,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        brief=brief,
        data_version=versions["data_version"],
        code_version=versions["code_version"],
        research_contract=research_contract,
        mutation_contract=mutation_identity,
        skip_llm=bool(skip_llm),
        max_loops=max(1, min(int(max_loops), 12)),
    )
    job_id = unique_id("creation")
    job = {
        "schema": SCHEMA,
        "job_id": job_id,
        "status": "等待研究",
        "source": source,
        "research_direction": research_direction,
        # direction_key is retained for old workers; both keys now represent
        # the complete immutable request rather than free text alone.
        "direction_key": job_key,
        "job_key": job_key,
        "job_fingerprint_schema": "qiyu_creation_job_fingerprint_v2",
        "symbol": symbol,
        "timeframe": timeframe,
        "trade_direction": direction,
        "brief": brief,
        "research_contract": research_contract,
        "mutation_contract": mutation_contract,
        "mutation_parent_validation": mutation_parent_validation,
        "parent_evidence_hash": (
            mutation_parent_validation.get("parent_evidence_hash")
            if mutation_parent_validation else None
        ),
        "data_version": versions["data_version"],
        "data_version_source": versions["data_version_source"],
        "code_version": versions["code_version"],
        "code_version_source": versions["code_version_source"],
        "dedupe_cooldown_seconds": cooldown_seconds,
        "skip_llm": bool(skip_llm),
        "max_loops": max(1, min(int(max_loops), 12)),
        "preferred_pipeline": preferred,
        "pipeline": preferred,
        "pipeline_label": PIPELINE_LABELS.get(preferred),
        "submitted_at": _now(),
        "submitted_ts": time.time(),
        "formal_review_started": False,
        "live_execution_changed": False,
        "progress": _progress_payload(
            "queued",
            detail="已进入唯一创造入口排队" + (
                (" · 指定%s" % PIPELINE_LABELS[preferred]) if preferred else ""
            ),
            percent=0,
        ),
    }
    with process_lock("parallel_queue_submit"):
        duplicate = None
        duplicate_state = None
        cooldown_remaining = None
        if not bool(force):
            duplicate, duplicate_state, cooldown_remaining = _find_duplicate_job(
                job_key, cooldown_seconds,
            )
        if duplicate:
            existing_id = duplicate.get("job_id")
            return {
                "ok": True,
                "accepted": False,
                "deduplicated": True,
                "job_id": existing_id,
                "duplicate_of": existing_id,
                "duplicate_state": duplicate_state,
                "cooldown_remaining_seconds": cooldown_remaining,
                "status": "重复任务冷却中" if duplicate_state == "completed" else "相同任务已在队列中",
                "outcome": "deduplicated",
                "status_code": "deduplicated",
                "job_key": job_key,
                "source": source,
                "research_direction": research_direction,
                "pipeline": duplicate.get("pipeline"),
                "pipeline_label": duplicate.get("pipeline_label"),
                "queue_path": duplicate.get("queue_path") or duplicate.get("running_path"),
                "workers_woken": False,
                "progress": duplicate.get("progress"),
                "data_version": versions["data_version"],
                "code_version": versions["code_version"],
                "parent_evidence_hash": (
                    mutation_parent_validation.get("parent_evidence_hash")
                    if mutation_parent_validation else None
                ),
            }
        path = _job_path("pending", job_id)
        job["queue_path"] = str(path)
        atomic_write_json(path, job)
    woke = start_workers(preferred_pipeline=preferred) if wake_workers else False
    return {
        "ok": True,
        "accepted": True,
        "deduplicated": False,
        "job_id": job_id,
        "job_key": job_key,
        "status": job["status"],
        "source": source,
        "research_direction": research_direction,
        "pipeline": preferred,
        "pipeline_label": job.get("pipeline_label"),
        "queue_path": str(path),
        "workers_woken": woke,
        "progress": job.get("progress"),
        "data_version": versions["data_version"],
        "code_version": versions["code_version"],
        "parent_evidence_hash": (
            mutation_parent_validation.get("parent_evidence_hash")
            if mutation_parent_validation else None
        ),
    }


def _try_direction_lock(direction_key):
    path = base_dir() / "locks" / ("direction_%s.lock" % direction_key)
    fh = open(str(path), "a+")
    if fcntl is None:
        return fh
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except (IOError, OSError):
        fh.close()
        return None


def _release_file_lock(fh):
    if fh is None:
        return
    if fcntl is not None:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
    fh.close()


def claim_next(slot):
    """Atomically claim the oldest eligible job for this worker slot/pipeline."""
    slot = int(slot)
    pipeline = pipeline_from_slot(slot)
    with process_lock("parallel_queue_claim"):
        pending = sorted(
            (base_dir() / "pending").glob("*.json"),
            key=lambda p: (p.stat().st_mtime, p.name),
        )
        # Prefer jobs that explicitly asked for this pipeline.
        ordered = []
        for path in pending:
            job = _read(path)
            if not job:
                continue
            preferred = _normalize_pipeline(job.get("preferred_pipeline") or job.get("pipeline"))
            ordered.append((0 if preferred == pipeline else 1, path, job, preferred))
        ordered.sort(key=lambda row: (row[0], row[1].stat().st_mtime, row[1].name))
        for _prio, path, job, preferred in ordered:
            if preferred is not None and preferred != pipeline:
                continue
            direction_lock = _try_direction_lock(job.get("direction_key") or "unknown")
            if direction_lock is None:
                continue
            target = _job_path("running", job["job_id"])
            try:
                os.replace(str(path), str(target))
            except OSError:
                _release_file_lock(direction_lock)
                continue
            claimed_progress = _progress_payload(
                "claimed",
                detail="%s 已开始研究：%s" % (
                    PIPELINE_LABELS.get(pipeline),
                    job.get("research_direction") or "",
                ),
                percent=5,
            )
            job.update({
                "status": "研究中",
                "worker_slot": slot,
                "worker_pid": os.getpid(),
                "worker_pid_start_token": _pid_start_token(os.getpid()),
                "worker_host": socket.gethostname(),
                "pipeline": pipeline,
                "pipeline_label": PIPELINE_LABELS.get(pipeline),
                "started_at": _now(),
                "started_ts": time.time(),
                "running_path": str(target),
                "heartbeat_at": claimed_progress["updated_at"],
                "heartbeat_ts": claimed_progress["updated_ts"],
                "progress": claimed_progress,
            })
            atomic_write_json(target, job)
            return job, target, direction_lock
    return None, None, None


def _pid_start_token(pid):
    """Return Linux process birth identity, not merely its reusable PID."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 1:
        return None
    try:
        raw = Path("/proc/%s/stat" % pid).read_text(encoding="utf-8")
        # comm is parenthesized and may contain spaces or ')'; fields after
        # its final ')' begin at Linux proc field 3. starttime is field 22.
        tail = raw[raw.rfind(")") + 1:].strip().split()
        starttime = tail[19]
        try:
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
                encoding="utf-8"
            ).strip()
        except Exception:
            boot_id = "unknown-boot"
        return "linux-proc:%s:%s" % (boot_id, starttime)
    except Exception:
        return None


def _pid_is_alive(pid):
    """Read-only local PID probe; PID 0/-1 must never reach os.kill."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    except Exception:
        return False
    return True


def _live_local_worker_owns_lease(job):
    if str(job.get("worker_host") or "") != socket.gethostname():
        return False
    pid = job.get("worker_pid")
    if not _pid_is_alive(pid):
        return False
    recorded = job.get("worker_pid_start_token")
    current = _pid_start_token(pid)
    if current is None:
        # /proc is unavailable (for example macOS development).  Retain the
        # PID-only safety fallback rather than stealing work from a live PID.
        return True
    # On Linux, a missing/mismatched birth token is PID reuse, not ownership.
    return bool(recorded) and str(recorded) == current


def _job_last_activity_ts(job, path):
    candidates = []
    for value in (
        job.get("started_ts"),
        job.get("heartbeat_ts"),
        (job.get("progress") or {}).get("updated_ts"),
    ):
        try:
            candidates.append(float(value))
        except (TypeError, ValueError):
            pass
    try:
        candidates.append(float(Path(path).stat().st_mtime))
    except Exception:
        pass
    return max(candidates) if candidates else 0.0


def recover_stale_jobs(max_age_seconds=5 * 3600, max_retries=2):
    """Recover abandoned jobs without stealing work from a live local PID."""
    recovered = []
    now = time.time()
    with process_lock("parallel_queue_claim"):
        for path in sorted((base_dir() / "running").glob("*.json")):
            job = _read(path)
            if not job:
                continue
            last_activity = _job_last_activity_ts(job, path)
            if now - last_activity < float(max_age_seconds):
                continue
            # Finalization writes the terminal receipt before deleting the
            # running lease.  A crash in that small window must be reconciled
            # as already complete, never re-enqueued for a second execution.
            terminal, _terminal_state, _terminal_path = _load_terminal_parent_job(
                job.get("job_id")
            )
            if terminal is not None:
                try:
                    path.unlink()
                except Exception:
                    pass
                continue
            if _live_local_worker_owns_lease(job):
                continue
            retries = int(job.get("recovery_count") or 0) + 1
            job["recovery_count"] = retries
            job["recovered_at"] = _now()
            if retries > int(max_retries):
                job["status"] = "研究进程异常且重试次数已用尽"
                job["outcome"] = "technical_failed"
                job["status_code"] = "technical_failed"
                job["technical_completed"] = False
                job["outcome_status"] = {
                    "technical_completed": False,
                    "research_rejected": False,
                    "data_blocked": False,
                    "candidate_ready": False,
                    "review_submitted": False,
                }
                job["progress"] = _progress_payload("done", detail=job["status"], percent=100)
                target = _job_path("failed", job["job_id"])
            else:
                job["status"] = "等待恢复研究"
                job.pop("worker_slot", None)
                job.pop("worker_pid", None)
                job.pop("worker_pid_start_token", None)
                job.pop("worker_host", None)
                job.pop("started_at", None)
                job.pop("started_ts", None)
                job.pop("heartbeat_at", None)
                job.pop("heartbeat_ts", None)
                job["pipeline"] = job.get("preferred_pipeline")
                job["pipeline_label"] = PIPELINE_LABELS.get(job.get("pipeline"))
                job["progress"] = _progress_payload(
                    "queued", detail="异常恢复后重新排队", percent=0,
                )
                target = _job_path("pending", job["job_id"])
            atomic_write_json(target, job)
            try:
                path.unlink()
            except Exception:
                pass
            recovered.append(job["job_id"])
    return recovered


def _bounded_evidence(value, depth=0, max_depth=5, max_items=20, max_string=1200):
    """JSON-safe bounded copy for receipts; never retain full return series."""
    if depth >= max_depth:
        if isinstance(value, (dict, list, tuple)):
            return "<truncated>"
    if isinstance(value, dict):
        out = {}
        for i, (key, item) in enumerate(value.items()):
            if i >= max_items:
                out["_truncated_items"] = len(value) - max_items
                break
            if str(key).lower() in ("candles", "trade_returns", "returns", "matrix"):
                try:
                    out[str(key) + "_n"] = len(item)
                except Exception:
                    out[str(key) + "_omitted"] = True
                continue
            out[str(key)] = _bounded_evidence(
                item, depth=depth + 1, max_depth=max_depth,
                max_items=max_items, max_string=max_string,
            )
        return out
    if isinstance(value, (list, tuple)):
        rows = [
            _bounded_evidence(
                item, depth=depth + 1, max_depth=max_depth,
                max_items=max_items, max_string=max_string,
            )
            for item in list(value)[:max_items]
        ]
        if len(value) > max_items:
            rows.append({"_truncated_items": len(value) - max_items})
        return rows
    if isinstance(value, str):
        return value[:max_string]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:max_string]


def _collect_named_evidence(value, max_matches=24):
    """Find statistical/judge evidence even as upstream envelopes evolve."""
    found = {}
    visited = [0]
    skip = {"candles", "trade_returns", "returns", "matrix", "factor_matrix"}

    def walk(node, path="root", depth=0):
        if depth > 7 or visited[0] >= 800 or len(found) >= max_matches:
            return
        visited[0] += 1
        if isinstance(node, dict):
            # Capture direct evidence keys before descending into an unrelated
            # large branch, so the traversal budget cannot hide later DSR/PBO
            # or Kimi fields.
            for key, item in node.items():
                low = str(key).lower()
                if low in skip:
                    continue
                child_path = "%s.%s" % (path, key)
                if (
                    low in ("dsr", "pbo", "kimi", "near_miss", "near_misses")
                    or low.startswith("dsr_")
                    or low.startswith("pbo_")
                    or "kimi" in low
                    or "near_miss" in low
                ):
                    found[child_path] = _bounded_evidence(
                        item, max_depth=3, max_items=8, max_string=600,
                    )
                    if len(found) >= max_matches:
                        return
            for key, item in node.items():
                low = str(key).lower()
                if low in skip:
                    continue
                child_path = "%s.%s" % (path, key)
                walk(item, child_path, depth + 1)
        elif isinstance(node, (list, tuple)):
            for i, item in enumerate(node[:20]):
                walk(item, "%s[%s]" % (path, i), depth + 1)

    walk(value)
    return found


def _compact_result(result):
    blueprint = (result or {}).get("blueprint") or {}
    discovery = (blueprint.get("stages") or {}).get("research_discovery") or {}
    map_elites = discovery.get("map_elites") or discovery.get("archive_elites") or {}
    top_error = (result or {}).get("error") or blueprint.get("error")
    detail = (result or {}).get("detail")
    if detail is None:
        detail = blueprint.get("detail")
    technical_completed = (result or {}).get("technical_completed")
    if technical_completed is None:
        technical_completed = isinstance(result, dict) and bool(result)
    present = bool((result or {}).get("present_to_human"))
    business_ok = bool((result or {}).get("ok") and present)
    outcome = (result or {}).get("outcome")
    error_text = str(top_error or "").lower()
    detail_error = str((detail or {}).get("error") or "").lower() if isinstance(detail, dict) else ""
    if not outcome:
        if any(x in (error_text + " " + detail_error) for x in (
            "candle", "data_missing", "no_data", "manifest_missing", "store_unavailable",
        )):
            outcome = "data_blocked"
        elif business_ok:
            outcome = "candidate_ready"
        elif technical_completed:
            outcome = "research_rejected"
        else:
            outcome = "technical_failed"
    outcome_status = dict((result or {}).get("outcome_status") or {})
    outcome_status.update({
        "technical_completed": bool(technical_completed),
        "research_rejected": outcome == "research_rejected",
        "data_blocked": outcome == "data_blocked",
        "candidate_ready": outcome == "candidate_ready",
        "review_submitted": outcome == "review_submitted",
    })
    stages = blueprint.get("stages") or {}
    failure_evidence = {
        "error": top_error,
        "detail": _bounded_evidence(detail),
        "fuses": _bounded_evidence(blueprint.get("fuses")),
        "abort_reason": (blueprint.get("fuses") or {}).get("abort_reason"),
        "human_banner_zh": discovery.get("human_banner_zh"),
        "contract": _bounded_evidence(discovery.get("contract")),
        "trial_budget": _bounded_evidence(discovery.get("trial_budget")),
        "multiple_testing": _bounded_evidence(discovery.get("multiple_testing")),
        "research_state_counts": _bounded_evidence(
            discovery.get("research_state_counts")
            or (discovery.get("handoff") or {}).get("research_state_counts")
        ),
        "near_miss_diagnostics": _bounded_evidence(
            discovery.get("near_miss_diagnostics")
            or stages.get("near_miss_diagnostics")
            or blueprint.get("near_miss_diagnostics")
        ),
        "failure_lineage": _bounded_evidence(
            discovery.get("failure_lineage") or stages.get("failure_lineage")
        ),
        "family_closures": _bounded_evidence(
            discovery.get("family_closures")
            or (discovery.get("handoff") or {}).get("family_closures")
        ),
        "named_statistical_and_judge_evidence": _collect_named_evidence(result or {}),
    }
    # Keep the schema stable without writing pages of nulls into every receipt.
    failure_evidence = {
        key: value for key, value in failure_evidence.items()
        if value not in (None, {}, [])
    }
    compact = {
        "ok": business_ok,
        "technical_completed": bool(technical_completed),
        "outcome": outcome,
        "status_code": outcome,
        "outcome_status": outcome_status,
        "present_to_human": present,
        "pipeline_gate": _bounded_evidence((result or {}).get("pipeline_gate")),
        "handoff_zh": _bounded_evidence((result or {}).get("handoff_zh")),
        "receipt_path": (result or {}).get("receipt_path"),
        "run_id": blueprint.get("run_id"),
        "blueprint_ok": blueprint.get("ok"),
        "n_survivors": discovery.get("n_survivors"),
        "map_elites": {
            "filled_cells": (
                map_elites.get("filled_cells")
                if map_elites.get("filled_cells") is not None else map_elites.get("n_filled")
            ),
            "archive_size": (
                map_elites.get("archive_size")
                if map_elites.get("archive_size") is not None else map_elites.get("n_elites")
            ),
        },
        "deliverables": _bounded_evidence(blueprint.get("deliverables")),
        "failure_evidence": failure_evidence,
    }
    max_receipt_bytes = 128 * 1024
    try:
        encoded_size = len(json.dumps(compact, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        encoded_size = max_receipt_bytes + 1
    if encoded_size > max_receipt_bytes:
        named = failure_evidence.get("named_statistical_and_judge_evidence") or {}
        compact["failure_evidence"] = {
            "error": failure_evidence.get("error"),
            "detail": _bounded_evidence(
                failure_evidence.get("detail"), max_depth=2, max_items=8, max_string=500,
            ),
            "fuses": _bounded_evidence(
                failure_evidence.get("fuses"), max_depth=2, max_items=8, max_string=500,
            ),
            "abort_reason": failure_evidence.get("abort_reason"),
            "trial_budget": _bounded_evidence(
                failure_evidence.get("trial_budget"), max_depth=2, max_items=8, max_string=500,
            ),
            "multiple_testing": _bounded_evidence(
                failure_evidence.get("multiple_testing"), max_depth=3, max_items=8, max_string=500,
            ),
            "near_miss_diagnostics": _bounded_evidence(
                failure_evidence.get("near_miss_diagnostics"), max_depth=2,
                max_items=5, max_string=500,
            ),
            "named_statistical_and_judge_evidence": {
                key: _bounded_evidence(value, max_depth=2, max_items=8, max_string=500)
                for key, value in list(named.items())[:12]
            },
            "receipt_truncated": True,
            "original_estimated_bytes": encoded_size,
        }
        compact["pipeline_gate"] = _bounded_evidence(
            compact.get("pipeline_gate"), max_depth=3, max_items=16, max_string=500,
        )
        compact["deliverables"] = _bounded_evidence(
            compact.get("deliverables"), max_depth=2, max_items=12, max_string=500,
        )
        try:
            reduced_size = len(json.dumps(compact, ensure_ascii=False, default=str).encode("utf-8"))
        except Exception:
            reduced_size = max_receipt_bytes + 1
        if reduced_size > max_receipt_bytes:
            gate_source = (result or {}).get("pipeline_gate") or {}
            compact["pipeline_gate"] = {
                "ok": gate_source.get("ok"),
                "passed": gate_source.get("passed"),
                "note_zh": str(gate_source.get("note_zh") or "")[:500],
                "failed": _bounded_evidence(
                    gate_source.get("failed"), max_depth=2, max_items=12, max_string=300,
                ),
            }
            compact["failure_evidence"] = {
                "error": failure_evidence.get("error"),
                "detail": _bounded_evidence(
                    failure_evidence.get("detail"), max_depth=2, max_items=4, max_string=300,
                ),
                "abort_reason": failure_evidence.get("abort_reason"),
                "multiple_testing": _bounded_evidence(
                    failure_evidence.get("multiple_testing"), max_depth=3,
                    max_items=4, max_string=300,
                ),
                "named_statistical_and_judge_evidence": {
                    key: _bounded_evidence(value, max_depth=2, max_items=3, max_string=240)
                    for key, value in list(named.items())[:8]
                },
                "receipt_truncated": True,
                "original_estimated_bytes": encoded_size,
            }
    return compact


def execute_claimed(job, running_path, slot):
    artifact_dir = base_dir() / "artifacts" / job["job_id"]
    artifact_dir.mkdir(parents=True, exist_ok=True)
    pipeline = pipeline_from_slot(slot)
    os.environ["QIYU_JOB_PROGRESS_PATH"] = str(running_path)
    os.environ["QIYU_CREATION_PIPELINE"] = str(pipeline or "")
    update_job_progress(
        running_path, "contract",
        detail="%s 进入唯一创造管道：编译研究契约" % PIPELINE_LABELS.get(pipeline, ""),
        percent=12,
    )
    if os.environ.get("QIYU_PARALLEL_CREATION_SELFTEST") == "1":
        time.sleep(float(os.environ.get("QIYU_PARALLEL_SELFTEST_DELAY") or 1.0))
        update_job_progress(running_path, "map_elites", detail="自测：质量—多样性搜索", percent=48)
        time.sleep(0.2)
        result = {
            "ok": True,
            "present_to_human": False,
            "handoff_zh": "并行隔离自测完成",
            "blueprint": {"run_id": job["job_id"], "stages": {}},
        }
    else:
        from .creation_sole_entry import create_strategy
        update_job_progress(
            running_path, "population",
            detail="机制种群 / 委员会 / 质量—多样性搜索进行中",
            percent=22,
        )
        result = create_strategy(
            symbol=job["symbol"],
            timeframe=job["timeframe"],
            direction=job["trade_direction"],
            brief=job["brief"],
            skip_llm=job["skip_llm"],
            max_loops=job["max_loops"],
            mission_id=job["job_id"],
            source=job["source"],
            research_direction=job["research_direction"],
            out_dir=artifact_dir,
            research_contract=job.get("research_contract"),
            mutation_contract=job.get("mutation_contract"),
            data_version=job.get("data_version"),
            code_version=job.get("code_version"),
        )
    summary = _compact_result(result)
    finished = dict(job)
    ready = bool(summary.get("ok") and summary.get("present_to_human"))
    formal_review = {
        "ok": False,
        "started": False,
        "attempted": False,
        "submission_created": False,
        "review_submitted": False,
        "reason": "creation_candidate_not_qualified",
    }
    if ready:
        atomic_write_json(artifact_dir / "qualified_blueprint.json", result.get("blueprint") or {})
        atomic_write_json(artifact_dir / "formal_review_handoff.json", {
            "schema": "qiyu_formal_review_handoff_v1",
            "job_id": job["job_id"],
            "pipeline": pipeline,
            "pipeline_label": PIPELINE_LABELS.get(pipeline),
            "source": job["source"],
            "research_direction": job["research_direction"],
            "job_key": job.get("job_key"),
            "research_contract": _bounded_evidence(
                (result.get("blueprint") or {}).get("research_contract")
                or job.get("research_contract")
            ),
            "mutation_contract": _bounded_evidence(job.get("mutation_contract")),
            "mutation_parent_validation": _bounded_evidence(
                job.get("mutation_parent_validation")
            ),
            "parent_evidence_hash": job.get("parent_evidence_hash"),
            "data_version": job.get("data_version"),
            "code_version": job.get("code_version"),
            "status": "已交由四阶段复核",
            "qualified_blueprint": str(artifact_dir / "qualified_blueprint.json"),
            "result_receipt": summary.get("receipt_path"),
            "deliverables": summary.get("deliverables"),
            "created_at": _now(),
            "automatic_live_deployment": False,
        })
        update_job_progress(
            running_path, "formal_review",
            detail="合格产出已交予四阶段复核",
            percent=92,
            extras={"handoff_ready": True},
        )
        if str(os.environ.get("QIYU_AUTO_FORMAL_REVIEW") or "1").lower() not in (
            "0", "false", "no", "off",
        ):
            from .formal_review_bridge import submit_blueprint_to_formal_review
            formal_review = submit_blueprint_to_formal_review(result.get("blueprint") or {}, job)
        else:
            formal_review = {
                "ok": True,
                "started": False,
                "attempted": False,
                "submission_created": False,
                "review_submitted": False,
                "reason": "automatic_formal_review_disabled",
            }
    review_submitted = bool(
        ready
        and formal_review.get("submission_created") is True
        and formal_review.get("review_submitted") is True
        and formal_review.get("task_id")
    )
    if review_submitted:
        final_status = "等待人工审批" if formal_review.get("ok") else "正式复核未通过"
        final_outcome = "review_submitted"
    elif ready:
        final_status = "等待正式复核"
        final_outcome = "candidate_ready"
    elif summary.get("outcome") == "data_blocked":
        final_status = "研究数据不可用"
        final_outcome = "data_blocked"
    elif summary.get("technical_completed"):
        final_status = "本轮研究已拒绝：未形成可信候选"
        final_outcome = "research_rejected"
    else:
        final_status = "研究执行未完成"
        final_outcome = "technical_failed"
    outcome_status = {
        "technical_completed": bool(summary.get("technical_completed")),
        "research_rejected": final_outcome == "research_rejected",
        "data_blocked": final_outcome == "data_blocked",
        "candidate_ready": final_outcome == "candidate_ready",
        "review_submitted": final_outcome == "review_submitted",
    }
    summary["outcome"] = final_outcome
    summary["status_code"] = final_outcome
    summary["outcome_status"] = outcome_status
    me = summary.get("map_elites") or {}
    detail = final_status
    if me.get("filled_cells") is not None:
        detail = "%s · MAP-Elites 填充格 %s" % (final_status, me.get("filled_cells"))
    finished.update({
        "status": final_status,
        "outcome": final_outcome,
        "status_code": final_outcome,
        "outcome_status": outcome_status,
        "technical_completed": bool(summary.get("technical_completed")),
        "pipeline": pipeline,
        "pipeline_label": PIPELINE_LABELS.get(pipeline),
        "worker_slot": int(slot),
        "finished_at": _now(),
        "finished_ts": time.time(),
        "duration_seconds": round(time.time() - float(job.get("started_ts") or time.time()), 3),
        "result": summary,
        "artifact_dir": str(artifact_dir),
        "formal_review_handoff_ready": ready,
        "formal_review_started": review_submitted,
        "formal_review_attempted": bool(formal_review.get("attempted")),
        "formal_submission_created": review_submitted,
        "formal_review": formal_review,
        "live_execution_changed": False,
        "progress": _progress_payload("done", detail=detail, percent=100),
    })
    state = "completed" if summary.get("technical_completed") else "failed"
    final_path = _job_path(state, job["job_id"])
    # Serialize terminal commit with stale-lease recovery.  If the process
    # dies after the receipt write but before unlink, recovery observes the
    # immutable terminal receipt and only removes the leftover running lease.
    with process_lock("parallel_queue_claim"):
        atomic_write_json(final_path, finished)
        try:
            Path(running_path).unlink()
        except Exception:
            pass
    os.environ.pop("QIYU_JOB_PROGRESS_PATH", None)
    return finished


def worker(slot, drain=True):
    slot = int(slot)
    if slot not in (0, 1):
        raise ValueError("worker slot must be 0 or 1")
    with process_lock("creation_capacity_%s" % slot, blocking=False) as capacity_lock:
        if capacity_lock is None:
            return {
                "ok": True,
                "status": "研究槽已占用",
                "slot": slot,
                "pipeline": pipeline_from_slot(slot),
                "pipeline_label": PIPELINE_LABELS.get(pipeline_from_slot(slot)),
                "processed": 0,
            }
        old = os.environ.get("QIYU_CREATION_SLOT_HELD")
        os.environ["QIYU_CREATION_SLOT_HELD"] = str(slot)
        recover_stale_jobs()
        processed = []
        empty_checks = 0
        try:
            while True:
                job, running_path, direction_lock = claim_next(slot)
                if not job:
                    empty_checks += 1
                    if empty_checks < 4:
                        time.sleep(0.5)
                        continue
                    break
                empty_checks = 0
                try:
                    processed.append(execute_claimed(job, running_path, slot))
                except Exception as exc:
                    failed = dict(job)
                    failed.update({
                        "status": "研究执行异常",
                        "outcome": "technical_failed",
                        "status_code": "technical_failed",
                        "technical_completed": False,
                        "outcome_status": {
                            "technical_completed": False,
                            "research_rejected": False,
                            "data_blocked": False,
                            "candidate_ready": False,
                            "review_submitted": False,
                        },
                        "error": str(exc),
                        "pipeline": pipeline_from_slot(slot),
                        "pipeline_label": PIPELINE_LABELS.get(pipeline_from_slot(slot)),
                        "finished_at": _now(),
                        "finished_ts": time.time(),
                        "formal_review_started": False,
                        "live_execution_changed": False,
                        "progress": _progress_payload(
                            "done", detail="研究执行异常：%s" % exc, percent=100,
                        ),
                    })
                    atomic_write_json(_job_path("failed", job["job_id"]), failed)
                    try:
                        Path(running_path).unlink()
                    except Exception:
                        pass
                    processed.append(failed)
                finally:
                    _release_file_lock(direction_lock)
                if not drain:
                    break
        finally:
            if old is None:
                os.environ.pop("QIYU_CREATION_SLOT_HELD", None)
            else:
                os.environ["QIYU_CREATION_SLOT_HELD"] = old
        return {
            "ok": True,
            "status": "研究槽本轮完成",
            "slot": slot,
            "pipeline": pipeline_from_slot(slot),
            "pipeline_label": PIPELINE_LABELS.get(pipeline_from_slot(slot)),
            "processed": len(processed),
            "job_ids": [j.get("job_id") for j in processed],
        }


def _row_from_job(row):
    pipeline = row.get("pipeline")
    if pipeline is None and row.get("worker_slot") is not None:
        pipeline = pipeline_from_slot(row.get("worker_slot"))
    progress = row.get("progress") or {}
    return {
        "job_id": row.get("job_id"),
        "job_key": row.get("job_key") or row.get("direction_key"),
        "status": row.get("status"),
        "outcome": row.get("outcome") or ((row.get("result") or {}).get("outcome")),
        "status_code": row.get("status_code") or ((row.get("result") or {}).get("status_code")),
        "outcome_status": row.get("outcome_status") or ((row.get("result") or {}).get("outcome_status")),
        "technical_completed": (
            row.get("technical_completed")
            if row.get("technical_completed") is not None
            else ((row.get("result") or {}).get("technical_completed"))
        ),
        "source": row.get("source"),
        "research_direction": row.get("research_direction"),
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "trade_direction": row.get("trade_direction"),
        "data_version": row.get("data_version"),
        "code_version": row.get("code_version"),
        "parent_evidence_hash": row.get("parent_evidence_hash"),
        "mutation_parent_validation": row.get("mutation_parent_validation"),
        "worker_slot": row.get("worker_slot"),
        "pipeline": pipeline,
        "pipeline_label": row.get("pipeline_label") or PIPELINE_LABELS.get(pipeline),
        "preferred_pipeline": row.get("preferred_pipeline"),
        "progress": progress,
        "submitted_at": row.get("submitted_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "formal_review_started": row.get("formal_review_started"),
        "formal_review": row.get("formal_review"),
        "result": row.get("result"),
        "error": row.get("error"),
    }


def _build_pipelines(pending, running, completed, failed):
    pipelines = []
    for pipeline in (1, 2):
        slot = pipeline - 1
        active = [r for r in running if r.get("pipeline") == pipeline or r.get("worker_slot") == slot]
        queued = [
            r for r in pending
            if (r.get("preferred_pipeline") or r.get("pipeline")) in (pipeline, None)
            or r.get("preferred_pipeline") is None
        ]
        # queued for display: jobs assigned to this pipe OR unassigned waiting
        if pipeline == 1:
            pipe_queued = [
                r for r in pending
                if r.get("preferred_pipeline") in (1, None) and r.get("pipeline") in (1, None)
            ]
        else:
            pipe_queued = [
                r for r in pending
                if r.get("preferred_pipeline") in (2, None) and r.get("pipeline") in (2, None)
            ]
        # Avoid double-counting unassigned pending on both pipes: show unassigned only on pipe1 idle hint
        if pipeline == 2:
            pipe_queued = [r for r in pending if r.get("preferred_pipeline") == 2]
        else:
            pipe_queued = [r for r in pending if r.get("preferred_pipeline") in (1, None)]
        recent = [
            r for r in (completed + failed)
            if r.get("pipeline") == pipeline or r.get("worker_slot") == slot
        ][:8]
        current = active[0] if active else None
        working = bool(current)
        progress = (current or {}).get("progress") or (
            {"stage": "idle", "stage_zh": "空闲", "detail": "等待研究方向", "percent": 0}
        )
        pipelines.append({
            "pipeline": pipeline,
            "pipeline_label": PIPELINE_LABELS[pipeline],
            "worker_slot": slot,
            "working": working,
            "state": "研究中" if working else ("排队中" if pipe_queued else "空闲"),
            "current_job": current,
            "queued": pipe_queued[:10],
            "recent": recent,
            "progress": progress,
            "research_direction": (current or {}).get("research_direction")
            or ((pipe_queued[0].get("research_direction") if pipe_queued else None)),
            "source": (current or {}).get("source"),
        })
    return pipelines


def status():
    out = {
        "ok": True,
        "schema": SCHEMA,
        "maximum_parallel_missions": 2,
        "entry": "唯一创造入口",
        "module_zh": "策略创造演进模块",
        "updated_at": _now(),
    }
    bags = {}
    for state in ("pending", "running", "completed", "failed"):
        rows = []
        for path in sorted((base_dir() / state).glob("*.json"), reverse=True)[:50]:
            row = _read(path) or {}
            rows.append(_row_from_job(row))
        bags[state] = rows
        out[state] = rows
    out["pipelines"] = _build_pipelines(
        bags["pending"], bags["running"], bags["completed"], bags["failed"],
    )
    out["working_count"] = sum(1 for p in out["pipelines"] if p.get("working"))
    return out


def review_status():
    """Four-stage review board linked to the dual creation pipelines."""
    try:
        from . import review_lexicon as lex
        stages = [
            {"id": 1, "name": lex.REVIEW_1, "scope": lex.REVIEW_1_SCOPE},
            {"id": 2, "name": lex.REVIEW_2, "scope": lex.REVIEW_2_SCOPE},
            {"id": 3, "name": lex.REVIEW_3, "scope": lex.REVIEW_3_SCOPE},
            {"id": 4, "name": lex.REVIEW_4, "scope": lex.REVIEW_4_SCOPE},
        ]
        human_gate = lex.HUMAN_CONFIRM_GATE
        note = lex.HUMAN_CONFIRM_NOTE
    except Exception:
        stages = [
            {"id": 1, "name": "第一次复核", "scope": "基础语法、逻辑断言、开仓密度预检"},
            {"id": 2, "name": "第二次复核", "scope": "单标的近2年加权回测与样本收益稳定性"},
            {"id": 3, "name": "第三次复核", "scope": "多标的近2年矩阵验证与抗风险离群测试"},
            {"id": 4, "name": "第四次复核", "scope": "三AI理论复核"},
        ]
        human_gate = "人工确认签发"
        note = "四阶段复核通过后进入人工确认；永不自动上线"

    st = status()
    handoffs = []
    for state in ("running", "completed"):
        for row in st.get(state) or []:
            fr = row.get("formal_review") or {}
            if row.get("formal_review_started") or fr.get("started") or row.get("status") in (
                "正式复核中", "等待人工审批", "等待正式复核", "正式复核未通过",
            ):
                handoffs.append({
                    "job_id": row.get("job_id"),
                    "pipeline": row.get("pipeline"),
                    "pipeline_label": row.get("pipeline_label"),
                    "research_direction": row.get("research_direction"),
                    "source": row.get("source"),
                    "status": row.get("status"),
                    "formal_review": fr,
                    "progress": row.get("progress"),
                    "symbol": row.get("symbol"),
                    "timeframe": row.get("timeframe"),
                })

    formal_recent = []
    try:
        import auto_trade_dual_engine_factory as dual
        dual_status = dual.load_status() or {}
        formal_recent = dual_status.get("formal_recent") or []
    except Exception:
        formal_recent = []

    return {
        "ok": True,
        "schema": "qiyu_strategy_creation_review_board_v1",
        "module_zh": "策略创造复核模块",
        "review_name_zh": "四阶段复核",
        "stages": stages,
        "human_confirm_gate": human_gate,
        "note_zh": note,
        "connected_module_zh": "策略创造演进模块",
        "pipelines": st.get("pipelines") or [],
        "handoffs_from_pipelines": handoffs[:30],
        "formal_recent": formal_recent[:20],
        "automatic_live_deployment": False,
        "updated_at": _now(),
    }


def self_test():
    temp_root = tempfile.mkdtemp(prefix="qiyu_parallel_creation_")
    env = dict(os.environ)
    env["VECTOR_ROOT"] = temp_root
    env["QIYU_PARALLEL_CREATION_SELFTEST"] = "1"
    env["QIYU_PARALLEL_SELFTEST_DELAY"] = "1.2"
    old_root = os.environ.get("VECTOR_ROOT")
    os.environ["VECTOR_ROOT"] = temp_root
    try:
        one = submit_job(
            "cursor", "衰竭回收型策略方向", brief="并行自测一",
            wake_workers=False, pipeline=1,
        )
        two = submit_job(
            "codex", "成交量异动型策略方向", brief="并行自测二",
            wake_workers=False, pipeline=2,
        )
        p0 = subprocess.Popen(
            [sys.executable, "-m", __package__ + ".parallel_creation", "worker", "--slot", "0"],
            env=env,
        )
        p1 = subprocess.Popen(
            [sys.executable, "-m", __package__ + ".parallel_creation", "worker", "--slot", "1"],
            env=env,
        )
        rc0, rc1 = p0.wait(), p1.wait()
        rows = []
        for jid in (one["job_id"], two["job_id"]):
            rows.append(_read(_job_path("completed", jid)) or {})
        overlap = False
        if len(rows) == 2 and all(r.get("started_ts") and r.get("finished_ts") for r in rows):
            overlap = max(r["started_ts"] for r in rows) < min(r["finished_ts"] for r in rows)
        board = status()
        return {
            "ok": rc0 == 0 and rc1 == 0 and overlap and len(board.get("pipelines") or []) == 2,
            "two_distinct_jobs": one["job_id"] != two["job_id"],
            "sources": sorted([r.get("source") for r in rows]),
            "worker_slots": sorted([r.get("worker_slot") for r in rows]),
            "pipelines": sorted([r.get("pipeline") for r in rows]),
            "pipeline_labels": [p.get("pipeline_label") for p in board.get("pipelines") or []],
            "execution_overlapped": overlap,
            "formal_review_started": False,
            "live_execution_changed": False,
            "test_root": temp_root,
        }
    finally:
        if old_root is None:
            os.environ.pop("VECTOR_ROOT", None)
        else:
            os.environ["VECTOR_ROOT"] = old_root


def main():
    ap = argparse.ArgumentParser(description="唯一创造入口的双研究槽协调器（管道1/管道2）")
    sub = ap.add_subparsers(dest="command")
    sp = sub.add_parser("submit")
    sp.add_argument("--source", required=True, choices=ALLOWED_SOURCES)
    sp.add_argument("--research-direction", required=True)
    sp.add_argument("--symbol", default="ADA-USDT-SWAP")
    sp.add_argument("--timeframe", default="5m")
    sp.add_argument("--direction", default="long", choices=("long", "short", "both"))
    sp.add_argument("--brief", default="")
    sp.add_argument("--brief-file", default="")
    sp.add_argument("--with-llm", action="store_true")
    sp.add_argument("--max-loops", type=int, default=5)
    sp.add_argument("--pipeline", default="", help="1/2 或 管道1/管道2；空=自动分配")
    sp.add_argument("--research-contract-file", default="")
    sp.add_argument("--mutation-contract-file", default="")
    sp.add_argument("--data-version", default="")
    sp.add_argument("--code-version", default="")
    sp.add_argument("--cooldown-seconds", type=int, default=None)
    sp.add_argument("--force", action="store_true", help="显式绕过重复任务冷却")
    wp = sub.add_parser("worker")
    wp.add_argument("--slot", required=True, type=int, choices=(0, 1))
    wp.add_argument("--one", action="store_true")
    sub.add_parser("status")
    sub.add_parser("review-status")
    sub.add_parser("self-test")
    args = ap.parse_args()

    if args.command == "submit":
        brief = args.brief
        if args.brief_file:
            brief = Path(args.brief_file).read_text(encoding="utf-8")
        research_contract = None
        mutation_contract = None
        if args.research_contract_file:
            research_contract = json.loads(Path(args.research_contract_file).read_text(encoding="utf-8"))
        if args.mutation_contract_file:
            mutation_contract = json.loads(Path(args.mutation_contract_file).read_text(encoding="utf-8"))
        out = submit_job(
            args.source, args.research_direction,
            symbol=args.symbol, timeframe=args.timeframe, direction=args.direction,
            brief=brief, skip_llm=not args.with_llm, max_loops=args.max_loops,
            pipeline=(args.pipeline or None),
            research_contract=research_contract,
            mutation_contract=mutation_contract,
            data_version=(args.data_version or None),
            code_version=(args.code_version or None),
            cooldown_seconds=args.cooldown_seconds,
            force=args.force,
        )
    elif args.command == "worker":
        out = worker(args.slot, drain=not args.one)
    elif args.command == "status":
        out = status()
    elif args.command == "review-status":
        out = review_status()
    elif args.command == "self-test":
        out = self_test()
    else:
        ap.error("command is required")
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main() or 0)
