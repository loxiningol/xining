# -*- coding: utf-8 -*-
"""Machine-checkable invariants contract — Stage 1 of pretest quality pipeline.

A contract is the only allowed translation target for Cursor. Anything not in
the contract is freestyle and must fail closed before L0/L1/Gate2.
"""
from __future__ import print_function

import json
from pathlib import Path


CONTRACT_SCHEMA = "qiyu_invariants_contract_v1"


def _features_used(node, out=None):
    out = out if out is not None else set()
    if not isinstance(node, dict):
        return out
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        for child in node.get(key) or []:
            _features_used(child, out)
        return out
    if "not" in node:
        return _features_used(node.get("not") or {}, out)
    if "exit_op" in node:
        out.add("exit_op:%s" % node.get("exit_op"))
        return out
    for side in ("left", "right"):
        opd = node.get(side)
        if isinstance(opd, dict) and opd.get("feature"):
            out.add(str(opd.get("feature")))
    return out


def _exit_ops(node, out=None):
    out = out if out is not None else set()
    if not isinstance(node, dict):
        return out
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        for child in node.get(key) or []:
            _exit_ops(child, out)
        return out
    if "not" in node:
        return _exit_ops(node.get("not") or {}, out)
    if node.get("exit_op"):
        out.add(str(node.get("exit_op")))
    return out


def _has_feature_cmp(node, left_feat, op, right_feat):
    if not isinstance(node, dict):
        return False
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        return any(
            _has_feature_cmp(c, left_feat, op, right_feat)
            for c in (node.get(key) or [])
        )
    if "not" in node:
        return _has_feature_cmp(node.get("not") or {}, left_feat, op, right_feat)
    if node.get("exit_op"):
        return False
    left = node.get("left") or {}
    right = node.get("right") or {}
    return (
        str(left.get("feature") or "") == left_feat
        and str(node.get("op") or "") == op
        and str(right.get("feature") or "") == right_feat
    )


def _has_value_cmp(node, left_feat, op, value, tol=1e-9):
    if not isinstance(node, dict):
        return False
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        return any(
            _has_value_cmp(c, left_feat, op, value, tol=tol)
            for c in (node.get(key) or [])
        )
    if "not" in node:
        return _has_value_cmp(node.get("not") or {}, left_feat, op, value, tol=tol)
    if node.get("exit_op"):
        return False
    left = node.get("left") or {}
    right = node.get("right") or {}
    if str(left.get("feature") or "") != left_feat:
        return False
    if str(node.get("op") or "") != op:
        return False
    if "value" not in right:
        return False
    try:
        return abs(float(right.get("value")) - float(value)) <= tol
    except Exception:
        return False


def _has_exit_op(node, exit_op, **attrs):
    if not isinstance(node, dict):
        return False
    if "all" in node or "any" in node:
        key = "all" if "all" in node else "any"
        return any(_has_exit_op(c, exit_op, **attrs) for c in (node.get(key) or []))
    if "not" in node:
        return _has_exit_op(node.get("not") or {}, exit_op, **attrs)
    if str(node.get("exit_op") or "") != exit_op:
        return False
    for k, v in attrs.items():
        if k == "n_atr":
            try:
                if abs(float(node.get("n_atr")) - float(v)) > 1e-9:
                    return False
            except Exception:
                return False
        elif k == "buffer_pct":
            try:
                if abs(float(node.get("buffer_pct")) - float(v)) > 1e-12:
                    return False
            except Exception:
                return False
        elif node.get(k) != v:
            return False
    return True


def load_contract(path_or_dict):
    if isinstance(path_or_dict, dict):
        return dict(path_or_dict)
    path = Path(path_or_dict)
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_contract_for_pack(pack, search_roots=None):
    """Load contract embedded in pack or from contracts/<family>.contract.json."""
    if not isinstance(pack, dict):
        return None, "pack_not_dict"
    if isinstance(pack.get("invariants_contract"), dict):
        return pack.get("invariants_contract"), "embedded"
    meta = pack.get("meta") or {}
    spec = pack.get("mechanism_spec") or {}
    family = (
        meta.get("contract_id")
        or spec.get("mechanism_family")
        or spec.get("mechanism_name")
        or ""
    )
    if not family:
        return None, "no_family"
    roots = list(search_roots or [])
    if not roots:
        roots = [Path.cwd(), Path("/root")]
        try:
            roots.insert(0, Path(__file__).resolve().parents[1])
        except Exception:
            pass
    # Also try known contract aliases for clean-family renames.
    candidates = [family]
    if "_ad" in family:
        candidates.append(family.rsplit("_ad", 1)[0])
    if "rolling_4h_sweep" in family and "rolling_4h_sweep_5m_v1" not in candidates:
        candidates.append("rolling_4h_sweep_5m_v1")
    for root in roots:
        for cand_name in candidates:
            cand = Path(root) / "contracts" / ("%s.contract.json" % cand_name)
            if cand.exists():
                return load_contract(cand), str(cand)
    return None, "contract_missing:%s" % family


def check_invariants(pack, contract=None, direction=None):
    """Return {pass, violations[], contract_id}. Fail-closed."""
    violations = []
    if contract is None:
        contract, src = resolve_contract_for_pack(pack)
        if contract is None:
            return {
                "pass": False,
                "violations": ["CONTRACT_MISSING:%s" % src],
                "contract_id": None,
                "stage": "invariants_contract",
            }
    else:
        src = "provided"

    cid = contract.get("contract_id") or contract.get("id")
    if contract.get("schema") and contract.get("schema") != CONTRACT_SCHEMA:
        violations.append("CONTRACT_SCHEMA_MISMATCH")

    spec = pack.get("mechanism_spec") or {}
    meta = pack.get("meta") or {}
    direction = direction or meta.get("direction") or "long"
    dsl = pack.get("dsl_%s" % direction) or pack.get("dsl") or {}

    # timeframe lock
    must_tf = contract.get("must_timeframe")
    if must_tf:
        tf = dsl.get("timeframe") or meta.get("timeframe")
        if str(tf) != str(must_tf):
            violations.append("TIMEFRAME_DRIFT:%s!=%s" % (tf, must_tf))

    # required entry feature comparisons
    entry = dsl.get("entry") or {}
    entry_cmps = contract.get("required_entry_feature_cmps") or []
    if str(direction) == "short" and contract.get("required_entry_feature_cmps_short"):
        entry_cmps = contract.get("required_entry_feature_cmps_short") or []
    for req in entry_cmps:
        if not _has_feature_cmp(entry, req["left"], req["op"], req["right"]):
            violations.append(
                "MISSING_ENTRY:%s %s %s" % (req["left"], req["op"], req["right"])
            )
    for req in contract.get("required_entry_value_cmps") or []:
        if not _has_value_cmp(entry, req["left"], req["op"], req["value"]):
            violations.append(
                "MISSING_ENTRY_VALUE:%s %s %s" % (req["left"], req["op"], req["value"])
            )

    # forbidden features / exit ops (context drift detectors)
    used = _features_used(entry)
    used |= _features_used(dsl.get("exit") or {})
    for bad in contract.get("forbidden_features") or []:
        if bad in used:
            violations.append("FORBIDDEN_FEATURE:%s" % bad)
    for bad in contract.get("forbidden_exit_ops") or []:
        if bad in _exit_ops(dsl.get("exit") or {}):
            violations.append("FORBIDDEN_EXIT_OP:%s" % bad)

    # required exits
    exit_tree = dsl.get("exit") or {}
    for req in contract.get("required_exit_ops") or []:
        attrs = {k: v for k, v in req.items() if k != "exit_op"}
        if not _has_exit_op(exit_tree, req["exit_op"], **attrs):
            violations.append("MISSING_EXIT_OP:%s" % req["exit_op"])
    exit_cmps = contract.get("required_exit_feature_cmps") or []
    if str(direction) == "short" and contract.get("required_exit_feature_cmps_short"):
        exit_cmps = contract.get("required_exit_feature_cmps_short") or []
    for req in exit_cmps:
        if not _has_feature_cmp(exit_tree, req["left"], req["op"], req["right"]):
            violations.append(
                "MISSING_EXIT:%s %s %s" % (req["left"], req["op"], req["right"])
            )

    # fake-proxy ban: swing_extreme as stand-in for entry_wick_buffer
    if contract.get("ban_swing_as_wick_proxy"):
        if "swing_extreme" in _exit_ops(exit_tree) and not _has_exit_op(
            exit_tree, "entry_wick_buffer"
        ):
            violations.append("FAKE_WICK_PROXY:swing_extreme_without_entry_wick_buffer")

    # non-negotiable echo
    for rule in contract.get("non_negotiable_rules") or []:
        nn = list(spec.get("non_negotiable_rules") or [])
        if rule not in nn:
            violations.append("SPEC_MISSING_NON_NEGOTIABLE:%s" % rule)

    return {
        "pass": not violations,
        "violations": violations,
        "contract_id": cid,
        "contract_source": src,
        "stage": "invariants_contract",
        "quality": "ok" if not violations else "SHIT_TRANSLATION",
    }
