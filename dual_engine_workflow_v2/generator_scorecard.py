# -*- coding: utf-8 -*-
"""Generator Scorecard — long-run maker performance (not only strategy PnL).

Tracks mechanism / empirical / symbolic / redteam / family arms with
calibration and survival rates. Feeds budget allocator.
"""
from __future__ import print_function

import json
import os
import threading
from datetime import datetime
from pathlib import Path


_LOCK = threading.Lock()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def scorecard_path():
    d = _root() / "auto_trade" / "dual_engine" / "generator_scorecards"
    d.mkdir(parents=True, exist_ok=True)
    return d / "scorecards.json"


def _blank(name):
    return {
        "generator": name,
        "hypotheses_created": 0,
        "contracts_registered": 0,
        "probe_attempts": 0,
        "probe_survivals": 0,
        "antifalsify_survivals": 0,
        "assembly_admissions": 0,
        "direction_hits": 0,
        "direction_tries": 0,
        "cost_underestimation_hits": 0,
        "novelty_proxy_sum": 0.0,
        "calibration_abs_err_sum": 0.0,
        "calibration_n": 0,
        "last_updated": None,
    }


def load():
    path = scorecard_path()
    if not path.exists():
        return {"schema": "qiyu_generator_scorecard_v1", "generators": {}, "updated_at": _now()}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"schema": "qiyu_generator_scorecard_v1", "generators": {}, "updated_at": _now()}


def save(data):
    path = scorecard_path()
    data = dict(data or {})
    data["updated_at"] = _now()
    with _LOCK:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _ensure(data, name):
    gens = data.setdefault("generators", {})
    if name not in gens:
        gens[name] = _blank(name)
    return gens[name]


def record_hypothesis(generator, n=1):
    data = load()
    row = _ensure(data, generator or "unknown")
    row["hypotheses_created"] = int(row.get("hypotheses_created") or 0) + int(n)
    row["last_updated"] = _now()
    save(data)
    return row


def record_outcome(generator, attribution, contract_compare=None, novelty=0.5):
    """Update scorecard from one attributed creation outcome."""
    data = load()
    name = generator or (attribution or {}).get("generator") or "unknown"
    row = _ensure(data, name)
    row["probe_attempts"] = int(row.get("probe_attempts") or 0) + 1
    stage = (attribution or {}).get("fail_stage")

    # Gate ladder credit: how far did this generator's hyp get?
    past_probe = stage not in ("incomplete_mechanism", "naked_probe")
    past_anti = stage in (
        "efr", "execution", "redteam", "judge", "multiple_testing", "survived",
    )
    if past_probe:
        row["probe_survivals"] = int(row.get("probe_survivals") or 0) + 1
    if past_anti or stage == "survived":
        row["antifalsify_survivals"] = int(row.get("antifalsify_survivals") or 0) + 1
    if stage == "survived":
        row["assembly_admissions"] = int(row.get("assembly_admissions") or 0) + 1

    resp = (attribution or {}).get("responsibility") or {}
    if float(resp.get("execution_failure") or 0) >= 0.35 and stage in ("efr", "execution"):
        row["cost_underestimation_hits"] = int(row.get("cost_underestimation_hits") or 0) + 1

    cc = contract_compare or {}
    if cc.get("direction_ok") is not None:
        row["direction_tries"] = int(row.get("direction_tries") or 0) + 1
        if cc.get("direction_ok"):
            row["direction_hits"] = int(row.get("direction_hits") or 0) + 1
        err = abs(1.0 - float(cc.get("hit_rate") or 0.5))
        row["calibration_abs_err_sum"] = float(row.get("calibration_abs_err_sum") or 0) + err
        row["calibration_n"] = int(row.get("calibration_n") or 0) + 1

    row["novelty_proxy_sum"] = float(row.get("novelty_proxy_sum") or 0) + float(novelty)
    row["last_updated"] = _now()

    fam = (attribution or {}).get("family")
    if fam:
        frow = _ensure(data, "family:%s" % fam)
        frow["probe_attempts"] = int(frow.get("probe_attempts") or 0) + 1
        if past_probe:
            frow["probe_survivals"] = int(frow.get("probe_survivals") or 0) + 1
        if stage == "survived":
            frow["assembly_admissions"] = int(frow.get("assembly_admissions") or 0) + 1
        frow["last_updated"] = _now()

    save(data)
    return summarize_row(row)


def summarize_row(row):
    row = row or {}
    attempts = max(1, int(row.get("probe_attempts") or 0))
    created = max(1, int(row.get("hypotheses_created") or attempts))
    cal_n = max(1, int(row.get("calibration_n") or 0))
    return {
        "generator": row.get("generator"),
        "hypotheses_created": row.get("hypotheses_created"),
        "probe_attempts": row.get("probe_attempts"),
        "probe_survival_rate": float(row.get("probe_survivals") or 0) / float(attempts),
        "assembly_rate": float(row.get("assembly_admissions") or 0) / float(attempts),
        "direction_accuracy": (
            float(row.get("direction_hits") or 0) / float(max(1, row.get("direction_tries") or 0))
        ),
        "calibration_error": float(row.get("calibration_abs_err_sum") or 0) / float(cal_n),
        "cost_underestimation_bias": float(row.get("cost_underestimation_hits") or 0) / float(attempts),
        "novelty_score": float(row.get("novelty_proxy_sum") or 0) / float(created),
        "last_updated": row.get("last_updated"),
    }


def all_summaries():
    data = load()
    out = {}
    for name, row in (data.get("generators") or {}).items():
        out[name] = summarize_row(row)
    return out


def probe():
    return {
        "ok": True,
        "provider": "generator_scorecard_v1",
        "path": str(scorecard_path()),
        "n_generators": len((load().get("generators") or {})),
        "at": _now(),
    }
