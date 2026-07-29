# -*- coding: utf-8 -*-
"""Mechanism fidelity / condition_audit — GLM structured audit of every DSL leaf."""
from __future__ import print_function

from .config import CONDITION_CLASSIFICATIONS, CONDITION_ACTIONS, FORBIDDEN_CORE_FEATURES
from .mechanism import extract_dsl_conditions, feature_is_forbidden_core


def rule_based_condition_audit(dsl, mechanism_statement, approved_supplements=None):
    """Deterministic first-pass audit (always runs; GLM may refine).

    Rejects unapproved traditional filters and conditions not traceable to statement.
    """
    approved_supplements = approved_supplements or []
    approved_feats = set()
    for s in approved_supplements:
        feat = ((s.get("proposed_condition") or {}).get("feature")
                or s.get("feature") or "")
        if feat:
            approved_feats.add(str(feat).lower())

    stmt = mechanism_statement or {}
    # Do NOT include forbidden_substitutions in "mentioned" blob — listing a ban
    # must not count as approving that indicator.
    stmt_blob = " ".join(
        str(stmt.get(k) or "") for k in stmt if k != "forbidden_substitutions"
    ).lower()
    forbidden = [str(x).lower() for x in (stmt.get("forbidden_substitutions") or [])]

    audits = []
    reject = False
    reject_reasons = []
    for row in extract_dsl_conditions(dsl):
        feat = str(row.get("feature") or "")
        feat_l = feat.lower()
        classification = "mechanism_observation"
        action = "retain"
        necessity = "appears in DSL; pending causal link check"

        # Forbidden substitutions: feature token appears in any ban phrase
        hit_forbidden = False
        for fb in forbidden:
            if not fb:
                continue
            if feat_l == fb or feat_l in fb.split() or fb in feat_l or any(
                tok and tok in feat_l for tok in fb.replace("/", " ").split() if len(tok) >= 3
            ):
                classification = "mechanism_substitution"
                action = "reject_strategy"
                necessity = "matches forbidden_substitutions"
                reject = True
                hit_forbidden = True
                reject_reasons.append("forbidden_substitution:%s" % feat)
                break

        # unapproved familiar filters (RSI/MACD/… without GLM supplement approval)
        if (not hit_forbidden) and feature_is_forbidden_core(feat) and feat_l not in approved_feats:
            # allow only if explicitly named in causal fields as observation
            mentioned = feat_l in stmt_blob
            if not mentioned:
                classification = "unapproved_filter"
                action = "remove"
                necessity = "traditional indicator not in approved supplements / statement"
                reject_reasons.append("unapproved_filter:%s" % feat)
            else:
                classification = "mechanism_observation"
                action = "retain"
                necessity = "named in mechanism statement as observation"

        # traceability: core numeric gates should relate to causal_entry tokens
        if classification not in ("unapproved_filter", "mechanism_substitution"):
            tokens = [t for t in feat_l.replace(".", " ").split() if len(t) > 2]
            if tokens and not any(t in stmt_blob for t in tokens):
                # not automatically fatal — mark for revise
                if feature_is_forbidden_core(feat):
                    classification = "unapproved_filter"
                    action = "remove"
                    reject_reasons.append("untraceable_forbidden:%s" % feat)
                else:
                    classification = "approved_filter" if feat_l in approved_feats else "redundant_condition"
                    action = "revise" if classification == "redundant_condition" else "retain"
                    necessity = "feature tokens weakly linked to statement"

        if classification not in CONDITION_CLASSIFICATIONS:
            classification = "mechanism_observation"
        if action not in CONDITION_ACTIONS:
            action = "retain"

        audits.append({
            "condition_id": row["condition_id"],
            "condition_description": row["condition_description"],
            "feature": feat,
            "phase": row.get("phase"),
            "classification": classification,
            "necessity_evidence": necessity,
            "action": action,
        })

    if any(a["classification"] == "unapproved_filter" for a in audits):
        # do not count as repair round — hard reject path for fidelity
        reject = True
        if not any(r.startswith("unapproved") for r in reject_reasons):
            reject_reasons.append("unapproved_filter_present")

    if any(a["action"] == "reject_strategy" for a in audits):
        reject = True

    return {
        "ok": True,
        "pass": not reject,
        "reject": reject,
        "reject_reasons": reject_reasons,
        "condition_audit": audits,
        "counts_as_repair_round": False,
    }


def apply_audit_removals(dsl, audit_report):
    """Remove leaves marked remove; return new dsl + removed list."""
    import copy
    dsl = copy.deepcopy(dsl or {})
    remove_feats = set()
    for a in audit_report.get("condition_audit") or []:
        if a.get("action") in ("remove", "reject_strategy") and a.get("classification") in (
            "unapproved_filter", "mechanism_substitution", "redundant_condition"
        ):
            remove_feats.add(str(a.get("feature") or "").lower())

    def filter_node(node):
        if isinstance(node, dict):
            if "left" in node and "op" in node:
                left = node.get("left") or {}
                feat = str(left.get("feature") or left.get("name") or "").lower()
                if feat in remove_feats:
                    return None
                return node
            out = {}
            for k, v in node.items():
                nv = filter_node(v)
                if nv is not None:
                    out[k] = nv
            return out
        if isinstance(node, list):
            out = []
            for x in node:
                nx = filter_node(x)
                if nx is not None:
                    out.append(nx)
            return out
        return node

    dsl["entry"] = filter_node(dsl.get("entry"))
    # re-audit after removal
    return dsl, sorted(remove_feats)


def glm_refine_condition_audit(book, rule_audit, ai_json_fn=None):
    """Optional GLM refinement; falls back to rule_audit if AI unavailable."""
    if ai_json_fn is None:
        return rule_audit
    prompt = (
        "你是策略总设计师 GLM。对 DSL 条件做机制忠诚度审计。"
        "仅输出JSON：{\"condition_audit\":[...],\"reject\":true|false,\"reject_reasons\":[]}。"
        "classification枚举: mechanism_core|mechanism_observation|execution_safety|"
        "approved_filter|unapproved_filter|redundant_condition|mechanism_substitution。"
        "action枚举: retain|revise|remove|reject_strategy。"
        "出现未批准熟悉过滤器或机制替换→reject=true，且不计入修复轮次。"
    )
    payload = {
        "mechanism_statement": book.get("mechanism_statement"),
        "rule_audit": rule_audit,
        "dsl_entry": (book.get("dsl") or {}).get("entry"),
    }
    try:
        res = ai_json_fn("glm", prompt, payload, max_tokens=1600, temperature=0.1)
    except Exception as exc:
        out = dict(rule_audit)
        out["glm_error"] = str(exc)
        return out
    parsed = (res or {}).get("parsed") if isinstance(res, dict) else None
    if not isinstance(parsed, dict) or not parsed.get("condition_audit"):
        out = dict(rule_audit)
        out["glm_fallback"] = True
        out["glm_error"] = (res or {}).get("error")
        return out
    audits = parsed.get("condition_audit")
    reject = bool(parsed.get("reject"))
    # merge: if rule already reject, keep reject
    if rule_audit.get("reject"):
        reject = True
    return {
        "ok": True,
        "pass": not reject,
        "reject": reject,
        "reject_reasons": list(parsed.get("reject_reasons") or rule_audit.get("reject_reasons") or []),
        "condition_audit": audits,
        "counts_as_repair_round": False,
        "glm_refined": True,
    }
