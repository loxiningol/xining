# -*- coding: utf-8 -*-
"""Learning loop orchestrator — result → attribution → belief → budget.

MVP closed loop for creation-time medium feedback.
Holdout / permanent retention zones are flagged; generators must not read them.
"""
from __future__ import print_function

import json
import os
from datetime import datetime
from pathlib import Path

from . import budget_allocator as allocator
from . import generator_scorecard as scorecard
from . import mechanism_beliefs as beliefs
from . import outcome_attribution as attrib
from . import prediction_contract as pcontract
from . import research_ledger as ledger
from . import shadow_feedback as shadow


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def isolation_path():
    d = _root() / "auto_trade" / "dual_engine" / "data_isolation"
    d.mkdir(parents=True, exist_ok=True)
    return d / "zones.json"


def ensure_isolation_zones():
    path = isolation_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    zones = {
        "schema": "qiyu_data_isolation_v1",
        "zones": {
            "train_memory": {"writable_by_generators": True, "note_zh": "允许更新机制/生成器"},
            "dev_eval": {"writable_by_generators": False, "note_zh": "候选筛选"},
            "permanent_holdout": {
                "writable_by_generators": False,
                "readable_by_generators": False,
                "note_zh": "生成与调参不可访问",
            },
            "live_realtime": {
                "summary_only_release": True,
                "note_zh": "仅固定周期摘要后释放",
            },
            "audit": {"note_zh": "记录 Agent 看过哪些数据"},
        },
        "knowledge_cutoff_required": True,
        "updated_at": _now(),
    }
    path.write_text(json.dumps(zones, ensure_ascii=False, indent=2), encoding="utf-8")
    return zones


def register_and_probe_prepare(hypothesis, symbol, timeframe, run_id, confidence=0.55):
    """Step A of loop: lock prediction contract before outcomes."""
    c = pcontract.build_from_hypothesis(
        hypothesis, symbol=symbol, timeframe=timeframe, confidence=confidence,
    )
    return pcontract.register(c, run_id=run_id)


def close_creation_outcome(
    hypothesis,
    contract,
    fail_stage,
    run_id=None,
    symbol=None,
    regime=None,
    probe=None,
    multiverse=None,
    antifalsify=None,
    leakage=None,
    efr=None,
    execution=None,
    redteam=None,
    timescale="medium",
):
    """Full medium-timescale update after one hypothesis terminal state."""
    ensure_isolation_zones()
    outcome_obs = {
        "mean_net": (probe or {}).get("mean_net"),
        "probe_passed": fail_stage not in ("naked_probe", "incomplete_mechanism"),
        "antifalsify_passed": bool((antifalsify or {}).get("passed")) if antifalsify else None,
        "multiverse_passed": bool((multiverse or {}).get("passed")) if multiverse else None,
        "efr_passed": bool((efr or {}).get("passed")) if efr else None,
        "execution_passed": bool((execution or {}).get("passed")) if execution else None,
    }
    # clear Nones for compare
    outcome_obs = {k: v for k, v in outcome_obs.items() if v is not None or k == "mean_net"}
    if "mean_net" in ((probe or {}) or {}):
        outcome_obs["mean_net"] = (probe or {}).get("mean_net")

    cc = pcontract.compare_to_outcome(contract, outcome_obs)
    att = attrib.attribute_creation_outcome(
        hypothesis,
        contract_compare=cc,
        probe=probe,
        multiverse=multiverse,
        antifalsify=antifalsify,
        leakage=leakage,
        efr=efr,
        execution=execution,
        redteam=redteam,
        fail_stage=fail_stage,
        regime=regime,
    )
    att = attrib.persist(att, run_id=run_id)

    gen = (hypothesis or {}).get("source") or (contract or {}).get("generator")
    sc = scorecard.record_outcome(gen, att, contract_compare=cc, novelty=0.55)
    bel = beliefs.update_from_attribution(
        att, symbol=symbol, regime=regime, timescale=timescale,
    )
    bud = allocator.observe_attribution(att, contract_compare=cc)

    if run_id:
        ledger.append_event({
            "event_type": "learning_update",
            "hypothesis_id": (hypothesis or {}).get("hypothesis_id"),
            "fail_stage": fail_stage,
            "primary_attribution": att.get("primary_attribution"),
            "mechanism_posterior": (bel or {}).get("posterior_probability"),
            "timescale": timescale,
        }, run_id=run_id)

    return {
        "ok": True,
        "contract_compare": cc,
        "attribution": {
            "outcome_id": att.get("outcome_id"),
            "primary": att.get("primary_attribution"),
            "fail_stage": att.get("fail_stage"),
            "responsibility": att.get("responsibility"),
            "reward_vector": att.get("reward_vector"),
        },
        "scorecard": sc,
        "belief": bel,
        "budget_observe": bud,
        "at": _now(),
    }


def plan_next_budget(context=None):
    ensure_isolation_zones()
    return allocator.allocate(context=context or {})


def apply_population_priors(hypotheses, family_priority=None):
    """Re-rank population using mechanism beliefs + family budget priority."""
    family_priority = list(family_priority or [])
    fam_rank = {f: i for i, f in enumerate(family_priority)}
    scored = []
    for h in hypotheses or []:
        boost = float(h.get("priority_boost") or 0)
        boost += beliefs.ranking_boost(h.get("mechanism_id"), h.get("family"))
        fam = h.get("family")
        if fam in fam_rank:
            boost += 1.5 - 0.15 * float(fam_rank[fam])
        row = dict(h)
        row["priority_boost"] = boost
        row["learning_boost"] = boost - float(h.get("priority_boost") or 0)
        scored.append(row)
    scored.sort(
        key=lambda x: (
            1 if x.get("bidirectional_hit") else 0,
            float(x.get("priority_boost") or 0),
        ),
        reverse=True,
    )
    return scored


def end_of_discovery_learning(run_id, symbol=None):
    """Finalize: shadow ingest (fast only) + next budget plan."""
    sh = shadow.ingest_shadow_status(run_id=run_id)
    plan = plan_next_budget(context={"symbol": symbol})
    if run_id:
        ledger.append_event({
            "event_type": "budget_allocation_next",
            "knobs": plan.get("knobs"),
            "family_priority": plan.get("family_priority"),
        }, run_id=run_id)
    return {
        "ok": True,
        "shadow": {"available": sh.get("available"), "may_update_mechanism_core": False},
        "next_budget": plan,
        "isolation": ensure_isolation_zones().get("zones"),
        "at": _now(),
    }


def probe():
    return {
        "ok": True,
        "provider": "learning_loop_v1",
        "mvp": [
            "prediction_contract",
            "outcome_attribution",
            "generator_scorecard",
            "shadow_canary_gaps",
            "budget_allocator",
        ],
        "closed_loop": "result→attribution→belief→budget→next_population",
        "at": _now(),
    }
