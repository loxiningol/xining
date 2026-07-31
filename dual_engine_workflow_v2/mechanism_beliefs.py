# -*- coding: utf-8 -*-
"""Mechanism belief store — Bayesian-lite posteriors by mechanism × regime × asset.

Updates are slow-feedback: creation attributions and (later) live confirmations.
Never flip to permanent true/false from a single PnL print.
"""
from __future__ import print_function

import json
import math
import os
import threading
from datetime import datetime
from pathlib import Path


_LOCK = threading.Lock()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def beliefs_path():
    d = _root() / "auto_trade" / "dual_engine" / "mechanism_beliefs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "beliefs.json"


def _blank(mid):
    return {
        "mechanism_id": mid,
        "prior_probability": 0.45,
        "posterior_probability": 0.45,
        "alpha": 2.0,  # Beta prior successes
        "beta": 2.0,   # Beta prior failures
        "evidence_by_market": {},
        "evidence_by_regime": {},
        "prediction_calibration": {"n": 0, "hit_sum": 0.0},
        "execution_realizability": {"n": 0, "ok_sum": 0.0},
        "contradiction_count": 0,
        "independent_events": 0,
        "decay_rate": 0.02,
        "last_updated": None,
    }


def load():
    path = beliefs_path()
    if not path.exists():
        return {"schema": "qiyu_mechanism_beliefs_v1", "mechanisms": {}, "updated_at": _now()}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"schema": "qiyu_mechanism_beliefs_v1", "mechanisms": {}, "updated_at": _now()}


def save(data):
    data = dict(data or {})
    data["updated_at"] = _now()
    with _LOCK:
        beliefs_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return data


def get(mechanism_id):
    data = load()
    mid = mechanism_id or "unknown"
    mechs = data.setdefault("mechanisms", {})
    if mid not in mechs:
        mechs[mid] = _blank(mid)
        save(data)
    return mechs[mid]


def _posterior(alpha, beta):
    a, b = float(alpha), float(beta)
    return a / (a + b) if (a + b) > 0 else 0.45


def update_from_attribution(attribution, symbol=None, regime=None, timescale="medium"):
    """Apply one attributed outcome. Fast timescale must NOT call this for core M."""
    if timescale == "fast":
        return {
            "ok": True,
            "skipped": True,
            "reason": "fast_feedback_cannot_update_mechanism_core",
            "at": _now(),
        }
    mid = (attribution or {}).get("mechanism_id")
    if not mid:
        fam = (attribution or {}).get("family")
        if fam:
            mid = "family:%s" % fam
        else:
            return {"ok": False, "error": "no_mechanism_id", "at": _now()}

    data = load()
    row = data.setdefault("mechanisms", {}).get(mid) or _blank(mid)
    resp = (attribution or {}).get("responsibility") or {}
    stage = (attribution or {}).get("fail_stage")
    rv = (attribution or {}).get("reward_vector") or {}

    # Soft evidence weight (not binary). Cap influence per event.
    mech_fail = float(resp.get("mechanism_failure") or 0)
    success_m = float(resp.get("success_credit_mechanism") or 0)
    exec_fail = float(resp.get("execution_failure") or 0)

    # If execution dominated, do not punish mechanism much
    if exec_fail >= 0.35 and stage in ("efr", "execution"):
        evidence = 0.15  # weak positive: direction/edge may exist
        row["alpha"] = float(row.get("alpha") or 2) + evidence
        er = row.setdefault("execution_realizability", {"n": 0, "ok_sum": 0.0})
        er["n"] = int(er.get("n") or 0) + 1
        er["ok_sum"] = float(er.get("ok_sum") or 0) + 0.0
    elif stage == "survived":
        evidence = 0.35 + 0.25 * success_m
        row["alpha"] = float(row.get("alpha") or 2) + evidence
        er = row.setdefault("execution_realizability", {"n": 0, "ok_sum": 0.0})
        er["n"] = int(er.get("n") or 0) + 1
        er["ok_sum"] = float(er.get("ok_sum") or 0) + float(rv.get("execution_accuracy") or 0.5)
    elif mech_fail >= 0.30:
        evidence = min(0.45, mech_fail)
        row["beta"] = float(row.get("beta") or 2) + evidence
        row["contradiction_count"] = int(row.get("contradiction_count") or 0) + 1
    else:
        # ambiguous — tiny noise toward prior
        row["alpha"] = float(row.get("alpha") or 2) + 0.05
        row["beta"] = float(row.get("beta") or 2) + 0.05

    # mild decay toward prior to fight non-stationarity
    decay = float(row.get("decay_rate") or 0.02)
    row["alpha"] = (1.0 - decay) * float(row["alpha"]) + decay * 2.0
    row["beta"] = (1.0 - decay) * float(row["beta"]) + decay * 2.0

    row["posterior_probability"] = _posterior(row["alpha"], row["beta"])
    row["independent_events"] = int(row.get("independent_events") or 0) + 1

    market = symbol or "unknown"
    mb = row.setdefault("evidence_by_market", {})
    mb[market] = mb.get(market) or {"n": 0, "post_sum": 0.0}
    mb[market]["n"] += 1
    mb[market]["post_sum"] += row["posterior_probability"]

    reg = regime or (attribution or {}).get("market_regime") or "unspecified"
    rb = row.setdefault("evidence_by_regime", {})
    rb[reg] = rb.get(reg) or {"n": 0, "post_sum": 0.0}
    rb[reg]["n"] += 1
    rb[reg]["post_sum"] += row["posterior_probability"]

    cal = row.setdefault("prediction_calibration", {"n": 0, "hit_sum": 0.0})
    cal["n"] = int(cal.get("n") or 0) + 1
    cal["hit_sum"] = float(cal.get("hit_sum") or 0) + float(rv.get("prediction_accuracy") or 0.5)

    row["last_updated"] = _now()
    data["mechanisms"][mid] = row
    save(data)
    return {
        "ok": True,
        "mechanism_id": mid,
        "posterior_probability": row["posterior_probability"],
        "independent_events": row["independent_events"],
        "timescale": timescale,
        "at": _now(),
    }


def ranking_boost(mechanism_id, family=None):
    """Return additive priority boost for population ranking."""
    data = load()
    mechs = data.get("mechanisms") or {}
    if mechanism_id and mechanism_id in mechs:
        post = float((mechs[mechanism_id] or {}).get("posterior_probability") or 0.45)
        return (post - 0.45) * 4.0
    if family:
        key = "family:%s" % family
        if key in mechs:
            post = float((mechs[key] or {}).get("posterior_probability") or 0.45)
            return (post - 0.45) * 3.0
    return 0.0


def probe():
    data = load()
    return {
        "ok": True,
        "provider": "mechanism_beliefs_v1",
        "n": len(data.get("mechanisms") or {}),
        "path": str(beliefs_path()),
        "at": _now(),
    }
