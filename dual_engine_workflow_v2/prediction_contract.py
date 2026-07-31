# -*- coding: utf-8 -*-
"""Prediction Contract — register expectations BEFORE seeing outcomes.

Prevents post-hoc storytelling. Contracts are hashed and append-only.
"""
from __future__ import print_function

import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path

from . import research_ledger as ledger


_LOCK = threading.Lock()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def contracts_dir():
    d = _root() / "auto_trade" / "dual_engine" / "prediction_contracts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hash_body(body):
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def build_from_hypothesis(hypothesis, symbol=None, timeframe=None, confidence=0.5):
    """Machine-readable pre-outcome contract from a hypothesis row."""
    h = hypothesis or {}
    pe_dir = h.get("predicted_direction") or "unknown"
    side = "positive"
    text = str(pe_dir).lower()
    if any(k in text for k in ("fade", "reversal", "negative", "short", "mean")):
        side = "negative" if "continuation" not in text else "positive"
    if "continuation" in text or "break" in text or "positive_ic" in text:
        side = "positive"
    if "negative_ic" in text:
        side = "negative"
    horizon = h.get("horizon") or "label_horizon"
    contract = {
        "schema": "qiyu_prediction_contract_v1",
        "hypothesis_id": h.get("hypothesis_id"),
        "mechanism_id": h.get("mechanism_id"),
        "family": h.get("family"),
        "source": h.get("source") or h.get("path"),
        "generator": h.get("source") or "unknown_generator",
        "signal_version": "S_%s" % (h.get("hypothesis_id") or "na"),
        "symbol": symbol,
        "timeframe": timeframe,
        "expected_direction": side,
        "expected_horizons": [horizon],
        "expected_states": list(h.get("conditional_on") or [])[:6],
        "expected_non_states": list(h.get("failure_conditions") or [])[:6],
        "expected_gross_edge_bps": [2.0, 20.0],
        "expected_cost_bps": [3.0, 12.0],
        "expected_fill_rate": [0.5, 0.85],
        "expected_capacity_usd": None,
        "failure_signature": [
            "naked_probe_non_positive",
            "antifalsify_oppose",
            "multiverse_fail",
            "efr_below_floor",
        ],
        "falsification_threshold": {
            "min_mean_net": 0.0,
            "min_multiverse_profit_frac": 0.55,
        },
        "confidence": float(confidence),
        "factor_hints": list(h.get("factor_hints") or h.get("observable_proxy") or [])[:8],
        "locked": True,
        "registered_at": _now(),
    }
    contract["contract_hash"] = _hash_body({
        k: contract[k] for k in contract if k not in ("registered_at", "contract_hash")
    })
    return contract


def register(contract, run_id=None):
    """Persist immutable contract; ledger event for trial accounting."""
    c = dict(contract or {})
    if not c.get("contract_hash"):
        c["contract_hash"] = _hash_body(c)
    c.setdefault("registered_at", _now())
    c["locked"] = True
    path = contracts_dir() / ("%s.json" % (c.get("contract_hash") or "unknown"))
    with _LOCK:
        if not path.exists():
            path.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    if run_id:
        ledger.append_event({
            "event_type": "prediction_contract",
            "hypothesis_id": c.get("hypothesis_id"),
            "mechanism_id": c.get("mechanism_id"),
            "generator": c.get("generator"),
            "contract_hash": c.get("contract_hash"),
            "expected_direction": c.get("expected_direction"),
            "confidence": c.get("confidence"),
        }, run_id=run_id)
    return c


def compare_to_outcome(contract, outcome):
    """Score contract fields vs observed outcome (no rewriting contract)."""
    c = contract or {}
    o = outcome or {}
    checks = []

    mean_net = o.get("mean_net")
    direction_ok = None
    if mean_net is not None:
        exp = c.get("expected_direction")
        if exp == "positive":
            direction_ok = float(mean_net) > 0
        elif exp == "negative":
            direction_ok = float(mean_net) < 0
        else:
            direction_ok = float(mean_net) != 0
        checks.append({
            "field": "expected_direction",
            "ok": bool(direction_ok),
            "expected": exp,
            "observed_mean_net": mean_net,
        })

    edge_bps = None
    if mean_net is not None:
        edge_bps = float(mean_net) * 10000.0
        lo, hi = (c.get("expected_gross_edge_bps") or [0, 50])[:2]
        in_band = float(lo) <= abs(edge_bps) <= float(hi) * 3.0  # wide band; magnitude soft
        checks.append({
            "field": "expected_gross_edge_bps",
            "ok": in_band,
            "expected": [lo, hi],
            "observed_bps": edge_bps,
        })

    for gate_name, key in (
        ("naked_probe", "probe_passed"),
        ("antifalsify", "antifalsify_passed"),
        ("multiverse", "multiverse_passed"),
        ("efr", "efr_passed"),
        ("execution", "execution_passed"),
    ):
        if key in o:
            checks.append({
                "field": gate_name,
                "ok": bool(o.get(key)),
                "observed": o.get(key),
            })

    n_ok = sum(1 for x in checks if x.get("ok"))
    n = max(1, len(checks))
    return {
        "ok": True,
        "contract_hash": c.get("contract_hash"),
        "hypothesis_id": c.get("hypothesis_id"),
        "n_checks": len(checks),
        "n_ok": n_ok,
        "hit_rate": float(n_ok) / float(n),
        "direction_ok": direction_ok,
        "checks": checks,
        "note_zh": "事前契约对照；禁止事后改写契约正文。",
        "at": _now(),
    }


def probe():
    return {
        "ok": True,
        "provider": "prediction_contract_v1",
        "dir": str(contracts_dir()),
        "at": _now(),
    }
