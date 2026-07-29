# -*- coding: utf-8 -*-
"""STEP A mechanism fingerprint + duplicate intercept (pre-coding)."""
from __future__ import print_function

import re

from .protocol import content_hash
from .step_a_config import (
    STEP_A_FINGERPRINT_FIELDS,
    DUPLICATE_SIMILARITY_THRESHOLD,
    EXHAUSTION_FADE_FAMILY,
)


def _norm(s):
    s = str(s or "").strip().lower()
    return re.sub(r"\s+", " ", s)[:400]


def _infer_mr_vs_trend(spec):
    text = " ".join([
        str(spec.get("mechanism_family") or ""),
        str(spec.get("entry_logic") or ""),
        str(spec.get("market_inefficiency") or ""),
    ]).lower()
    if any(k in text for k in ("fade", "reclaim", "mean", "revert", "vacuum", "exhaust")):
        return "mean_reversion"
    if any(k in text for k in ("breakout", "trend", "momentum", "continuation")):
        return "trend"
    return "unspecified"


def build_step_a_fingerprint(spec, direction=None, symbol=None, timeframe=None):
    spec = spec or {}
    fp = {
        "directionality": _norm(direction or spec.get("direction") or "unspecified"),
        "regime_dependency": _norm(spec.get("required_market_regime")),
        "signal_origin": _norm(spec.get("market_inefficiency")),
        "entry_trigger_type": _norm(spec.get("entry_logic"))[:120],
        "exit_trigger_type": _norm(spec.get("exit_logic"))[:120],
        "mean_reversion_vs_trend": _infer_mr_vs_trend(spec),
        "volatility_dependency": "high" if "vol" in _norm(spec.get("required_market_regime")) else "unspecified",
        "liquidity_dependency": "high" if any(
            k in _norm(spec.get("market_inefficiency") + " " + spec.get("counterparty_source", ""))
            for k in ("liquid", "depth", "book", "cascade")
        ) else "unspecified",
        "holding_period": _norm(spec.get("expected_holding_period")),
        "symbol_dependency": _norm(symbol or ",".join(spec.get("suitable_symbols") or [])),
        "timeframe_dependency": _norm(timeframe or ",".join(spec.get("suitable_timeframes") or [])),
        "counterparty_type": _norm(spec.get("counterparty_source")),
        "mechanism_family": _norm(spec.get("mechanism_family")),
        "mechanism_id": spec.get("mechanism_id"),
    }
    for k in STEP_A_FINGERPRINT_FIELDS:
        fp.setdefault(k, "")
    core = {k: fp.get(k) for k in STEP_A_FINGERPRINT_FIELDS}
    fp["fingerprint_hash"] = content_hash(core)[:32]
    # family excludes symbol/timeframe so symbol clones collide
    family_keys = (
        "directionality", "signal_origin", "entry_trigger_type",
        "mean_reversion_vs_trend", "counterparty_type", "mechanism_family",
    )
    fp["family_hash"] = content_hash({k: fp.get(k) for k in family_keys})[:32]
    return fp


def similarity_step_a(fp_a, fp_b):
    if not fp_a or not fp_b:
        return 0.0
    scores = []
    for k in STEP_A_FINGERPRINT_FIELDS:
        ta = set(_norm(fp_a.get(k)).split())
        tb = set(_norm(fp_b.get(k)).split())
        if not ta and not tb:
            scores.append(1.0)
            continue
        if not ta or not tb:
            scores.append(0.0)
            continue
        scores.append(len(ta & tb) / float(len(ta | tb)))
    return round(sum(scores) / max(len(scores), 1), 4)


def duplicate_intercept(fp_new, existing_fps, mechanism_family=None,
                        allow_horizontal_expand=False, threshold=None):
    """Return (blocked, report). Horizontal expand must be explicit and not count as niche."""
    threshold = DUPLICATE_SIMILARITY_THRESHOLD if threshold is None else threshold
    hits = []
    fam = _norm(mechanism_family or (fp_new or {}).get("mechanism_family"))
    for row in existing_fps or []:
        fp = row.get("fingerprint") if isinstance(row, dict) and "fingerprint" in row else row
        if not isinstance(fp, dict):
            continue
        if fp.get("family_hash") and fp_new.get("family_hash") and fp["family_hash"] == fp_new["family_hash"]:
            hits.append({
                "score": 1.0,
                "reason": "family_hash_exact",
                "horizontal_expand_only": True,
                "ref": {"family_hash": fp.get("family_hash"), "mechanism_id": fp.get("mechanism_id")},
            })
            continue
        score = similarity_step_a(fp_new, fp)
        if score >= threshold:
            hits.append({
                "score": score,
                "reason": "similarity",
                "horizontal_expand_only": score >= 0.85,
                "ref": {"family_hash": fp.get("family_hash"), "mechanism_id": fp.get("mechanism_id"),
                        "score": score},
            })
    hits.sort(key=lambda x: -x["score"])

    is_exhaustion_clone = EXHAUSTION_FADE_FAMILY in fam and any(
        EXHAUSTION_FADE_FAMILY in _norm((h.get("ref") or {}).get("mechanism_id"))
        or EXHAUSTION_FADE_FAMILY in _norm(str(h.get("reason")))
        for h in hits
    ) or fam == EXHAUSTION_FADE_FAMILY

    report = {
        "hits": hits[:10],
        "is_parameter_variant": bool(hits and hits[0]["score"] >= 0.9),
        "is_symbol_clone": bool(hits and hits[0].get("horizontal_expand_only")),
        "is_independent_niche": not bool(hits),
        "is_exhaustion_fade_clone": bool(fam == EXHAUSTION_FADE_FAMILY),
        "allow_horizontal_expand": bool(allow_horizontal_expand),
        "counts_as_independent_mechanism": (
            (not hits) and fam != EXHAUSTION_FADE_FAMILY
        ) if not allow_horizontal_expand else False,
    }
    blocked = bool(hits) and not allow_horizontal_expand
    if fam == EXHAUSTION_FADE_FAMILY and not allow_horizontal_expand:
        # new-mechanism mode must not propose another exhaustion_fade_short
        blocked = True
        report["is_exhaustion_fade_clone"] = True
        report["block_reason"] = "exhaustion_fade_short_clone_forbidden_in_new_mechanism_mode"
    return blocked, report
