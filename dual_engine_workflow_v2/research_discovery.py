# -*- coding: utf-8 -*-
"""Research discovery orchestrator — BEFORE strategy assembly.

Paradigm: searchable mechanism/phenomenon population → cheap probes →
antifalsify evidence → QD archive → only then hand off to factor/assembly.
"""
from __future__ import print_function

import copy
from datetime import datetime

from . import antifalsify
from . import creation_multiverse as multiverse
from . import edge_friction as efr_mod
from . import heterogeneous_committee as committee
from . import map_elites_archive as qd
from . import mechanism_graph as mgraph
from . import multiple_testing as mtest
from . import phenomenon_scanner as phscan
from . import probe_protocol as probes
from . import research_blackboard as board
from . import research_ledger as ledger
from . import symbolic_searcher as sym


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def compile_research_contract(brief, symbol, timeframe, constraints=None):
    """Stage 0: turn human brief into searchable contract + feasibility."""
    c = dict(constraints or {})
    text = str(brief or "")
    # Detect impossible hard return demands (article: do not force deliver)
    weekly_demand = c.get("minimum_weekly_return")
    forced_weekly = None
    if weekly_demand is not None and float(weekly_demand) >= 0.08:
        forced_weekly = float(weekly_demand)
    if "周收益" in text and "8%" in text:
        forced_weekly = forced_weekly or 0.08
    feasible = True
    warnings = []
    if forced_weekly and forced_weekly >= 0.08:
        feasible = False
        warnings.append(
            "硬性周收益≥8%在永续短周期上通常不具统计/经济可实现性；"
            "系统将拒绝被迫交付，改为返回无可信候选（除非证据极强）。"
        )
    contract = {
        "schema": "qiyu_research_contract_v1",
        "symbol": symbol,
        "timeframe": timeframe,
        "brief": text[:2000],
        "available_data": ["ohlcv_swap_candles", "derived_factors"],
        "forbidden_info": ["future_bars", "unrealized_label_leak"],
        "max_complexity": "probe_then_assemble",
        "max_trial_budget": int(c.get("max_trial_budget") or 200),
        "min_evidence": ["naked_probe", "antifalsify_matrix", "efr"],
        "constraints": c,
        "forced_weekly_demand": forced_weekly,
        "requirement_feasible": feasible,
        "warnings_zh": warnings,
        "at": _now(),
    }
    return contract


def build_hypothesis_population(brief, symbol, timeframe, factor_matrix, fwd_returns,
                                max_mechanisms=12, max_phenomena=20, run_id=None):
    """Independent heterogeneous committee submissions (no early pick-1, no chat)."""
    run_id = run_id or ledger.new_run_id("pop")
    director = committee.research_director_budget()
    board.write(run_id, "research_director", "budget", director)

    mech = committee.run_mechanism_scientist(
        brief, symbol, timeframe, run_id, limit=max_mechanisms,
    )
    emp = committee.run_empirical_scientist(
        factor_matrix, fwd_returns, run_id, max_phenomena=max_phenomena,
    )
    sym_pack = committee.run_symbolic_searcher(factor_matrix, fwd_returns, run_id)

    hyps = committee.merge_independent_hypotheses(mech, emp, sym_pack)
    dedup = qd.dedupe_hypotheses(hyps)
    return {
        "ok": True,
        "run_id": run_id,
        "committee": {
            "research_director": director,
            "mechanism_scientist": {"n": mech.get("n"), "saw_returns": False},
            "empirical_scientist": {"n": emp.get("n"), "wrote_trade_rules": False},
            "symbolic_searcher": {"n": sym_pack.get("n"), "llm": False},
        },
        "mechanisms": {"n": mech.get("n"), "hypotheses": mech.get("hypotheses")},
        "phenomena": emp.get("phenomena"),
        "symbolic": sym_pack.get("search"),
        "hypotheses_raw_n": len(hyps),
        "hypotheses": dedup.get("kept") or [],
        "dedupe": {"dropped": dedup.get("dropped"), "n_kept": dedup.get("n_kept")},
        "at": _now(),
    }


def run_discovery(
    symbol,
    timeframe,
    brief="",
    factor_matrix=None,
    fwd_returns=None,
    candles=None,
    constraints=None,
    run_id=None,
    max_hypotheses_probe=16,
    min_efr=1.5,
):
    """Full discovery stages 0–early robustness. Returns survivors for assembly."""
    run_id = run_id or ledger.new_run_id("discover")
    stages = {}
    contract = compile_research_contract(brief, symbol, timeframe, constraints)
    stages["contract"] = contract
    ledger.append_event({
        "event_type": "contract",
        "symbol": symbol,
        "timeframe": timeframe,
        "feasible": contract.get("requirement_feasible"),
        "warnings": contract.get("warnings_zh"),
    }, run_id=run_id)

    if contract.get("requirement_feasible") is False and str(
        (constraints or {}).get("allow_unreachable_targets") or ""
    ).lower() not in ("1", "true", "yes"):
        # Still run discovery, but final deliverable must not claim success on 8% week
        stages["contract_flag"] = "unreachable_target_soft_block"

    pop = build_hypothesis_population(
        brief, symbol, timeframe, factor_matrix, fwd_returns, run_id=run_id,
    )
    stages["population"] = {
        "n_hypotheses": len(pop.get("hypotheses") or []),
        "n_mechanisms": (pop.get("mechanisms") or {}).get("n"),
        "n_phenomena": (pop.get("phenomena") or {}).get("n"),
        "n_symbolic": ((pop.get("committee") or {}).get("symbolic_searcher") or {}).get("n"),
        "committee": pop.get("committee"),
        "dedupe_dropped": len((pop.get("dedupe") or {}).get("dropped") or []),
        "bidirectional_hits": sum(
            1 for h in (pop.get("hypotheses") or []) if h.get("bidirectional_hit")
        ),
    }
    for h in (pop.get("hypotheses") or [])[:80]:
        ledger.append_event({
            "event_type": "hypothesis",
            "hypothesis_id": h.get("hypothesis_id"),
            "path": h.get("path"),
            "family": h.get("family"),
            "mechanism_id": h.get("mechanism_id"),
            "bidirectional_hit": h.get("bidirectional_hit"),
        }, run_id=run_id)

    archive = {}
    survivors = []
    probe_returns_for_pbo = []
    n_bars = len(fwd_returns or [])
    span_days = None
    if candles and len(candles) >= 2:
        try:
            from . import creation_return_hardness as rh
            span_days = rh.span_days_from_ts(candles[0].get("ts"), candles[-1].get("ts"))
        except Exception:
            span_days = None

    tested = 0
    for h in (pop.get("hypotheses") or [])[: int(max_hypotheses_probe)]:
        # completeness gate for theory path
        if h.get("path") == "theory_to_data":
            comp = h.get("completeness") or {}
            if comp and not comp.get("passed"):
                ledger.append_event({
                    "event_type": "hypothesis_rejected_incomplete",
                    "hypothesis_id": h.get("hypothesis_id"),
                }, run_id=run_id)
                continue

        pr = probes.probe_hypothesis(h, factor_matrix, fwd_returns)
        tested += 1
        ledger.append_event({
            "event_type": "probe",
            "hypothesis_id": h.get("hypothesis_id"),
            "passed": pr.get("passed"),
            "n_probes": pr.get("n_probes"),
            "best_factor": ((pr.get("best") or {}).get("factor")),
            "mean_net": ((pr.get("best") or {}).get("mean_net")),
        }, run_id=run_id)
        if not pr.get("passed"):
            continue

        best = pr.get("best") or {}
        # Early multiverse on naked probe returns
        mv = multiverse.survival_test(best.get("trade_returns") or [])
        ledger.append_event({
            "event_type": "multiverse_probe",
            "hypothesis_id": h.get("hypothesis_id"),
            "passed": mv.get("passed"),
        }, run_id=run_id)
        if not mv.get("passed"):
            continue

        af = antifalsify.run_antifalsify_battery(
            (factor_matrix or {}).get(best.get("factor")) or [],
            fwd_returns,
            side=best.get("side") or "high",
            factor_matrix=factor_matrix,
        )
        ledger.append_event({
            "event_type": "antifalsify",
            "hypothesis_id": h.get("hypothesis_id"),
            "passed": af.get("passed"),
            "support_n": af.get("support_n"),
            "oppose_n": af.get("oppose_n"),
            "causal_claim": False,
        }, run_id=run_id)
        if not af.get("passed"):
            continue

        feas = efr_mod.evaluate_early_feasibility(
            best, n_bars=n_bars, span_days=span_days, min_efr=min_efr,
        )
        ledger.append_event({
            "event_type": "efr",
            "hypothesis_id": h.get("hypothesis_id"),
            "passed": feas.get("passed"),
            "efr": ((feas.get("efr") or {}).get("efr")),
            "research_value": feas.get("research_value"),
        }, run_id=run_id)
        if not feas.get("passed"):
            continue

        # Constructive red team: opposing-family naked probe must lose
        red = committee.run_constructive_redteam(
            h, factor_matrix, fwd_returns, run_id,
        )
        if not red.get("passed"):
            continue

        # Evidence-field judge (non-LLM; Kimi optional comment only)
        judgment = committee.judge_from_evidence({
            "naked_probe_passed": True,
            "antifalsify_passed": bool(af.get("passed")),
            "efr_passed": bool(feas.get("passed")),
            "redteam_passed": bool(red.get("passed")),
            "bidirectional_hit": bool(h.get("bidirectional_hit")),
            "dsr_passed": False,  # filled after MT for top survivors
        }, run_id=run_id)
        if not judgment.get("admit_to_assembly"):
            # still allow into archive candidates but mark judge_block
            pass

        archive, elite_row, _replaced = qd.upsert(
            archive, h, probe_best=best, antifalsify=af,
            efr=feas.get("efr"), n_bars=n_bars,
        )
        row = {
            "hypothesis": h,
            "probe": {k: v for k, v in best.items() if k != "trade_returns"},
            "probe_returns": best.get("trade_returns") or [],
            "multiverse": {
                "passed": mv.get("passed"),
                "profit_frac": mv.get("profit_frac"),
                "median_total": mv.get("median_total"),
            },
            "antifalsify": {
                "passed": af.get("passed"),
                "support_n": af.get("support_n"),
                "oppose_n": af.get("oppose_n"),
                "evidence_matrix": {
                    k: {
                        "support": (v or {}).get("support"),
                        "neutral": (v or {}).get("neutral"),
                        "oppose": (v or {}).get("oppose"),
                    }
                    for k, v in ((af.get("evidence_matrix") or {}).items())
                },
                "causal_claim": False,
            },
            "feasibility": {
                "passed": feas.get("passed"),
                "efr": (feas.get("efr") or {}).get("efr"),
                "research_value": feas.get("research_value"),
                "capacity": feas.get("capacity"),
            },
            "redteam": {
                "passed": red.get("passed"),
                "main_net": red.get("main_net"),
                "opp_net": red.get("opp_net"),
                "opp_family": red.get("opp_family"),
            },
            "judge": {
                "admit_to_assembly": judgment.get("admit_to_assembly"),
                "score": judgment.get("score"),
                "kimi_enabled": ((judgment.get("kimi") or {}).get("enabled")),
            },
            "elite": elite_row,
        }
        if not judgment.get("admit_to_assembly"):
            continue
        survivors.append(row)
        if best.get("trade_returns"):
            probe_returns_for_pbo.append(list(best.get("trade_returns") or [])[:200])

    budget = ledger.effective_trial_budget(run_id=run_id)
    stages["trial_budget"] = budget

    # Multiple testing on best survivor vs peers
    mt_pack = None
    if survivors:
        survivors.sort(
            key=lambda r: float(((r.get("elite") or {}).get("quality") or 0)),
            reverse=True,
        )
        best_rets = survivors[0].get("probe_returns") or []
        peer = [s.get("probe_returns") or [] for s in survivors[:8]]
        mt_pack = mtest.evaluate_multiple_testing(
            best_rets, peer, n_trials_effective=budget.get("effective_trials") or 1,
        )
        stages["multiple_testing"] = mt_pack
        ledger.append_event({
            "event_type": "multiple_testing",
            "passed": mt_pack.get("passed"),
            "dsr": ((mt_pack.get("dsr") or {}).get("dsr")),
            "pbo": ((mt_pack.get("pbo") or {}).get("pbo")),
            "effective_trials": budget.get("effective_trials"),
        }, run_id=run_id)
        if not mt_pack.get("passed"):
            # demote: keep archive but mark assembly blocked unless DSR ok alone with note
            for s in survivors:
                s["multiple_testing_gate"] = mt_pack
            # If DSR passes but PBO weak, still allow with warning
            dsr_ok = bool((mt_pack.get("dsr") or {}).get("passed"))
            if not dsr_ok:
                survivors = []
    else:
        stages["multiple_testing"] = {
            "ok": True, "passed": False, "reason": "no_survivors",
        }

    elites = qd.elites(archive)
    stages["map_elites"] = {
        "n_cells": len(archive),
        "n_elites": len(elites),
        "cells": [
            {
                "cell": (e.get("descriptor") or {}).get("cell"),
                "hypothesis_id": e.get("hypothesis_id"),
                "quality": e.get("quality"),
                "probe_mean_net": e.get("probe_mean_net"),
            }
            for e in elites[:20]
        ],
    }

    ok = len(survivors) > 0
    handoff = None
    if ok:
        top = survivors[0]
        h = top.get("hypothesis") or {}
        handoff = {
            "hypothesis_id": h.get("hypothesis_id"),
            "mechanism_id": h.get("mechanism_id"),
            "family": h.get("family"),
            "path": h.get("path"),
            "factor_hints": h.get("factor_hints") or h.get("observable_proxy") or [],
            "core_logic_zh": h.get("statement_zh") or (
                "机制 %s / 支付方 %s / 约束 %s"
                % (h.get("mechanism_id"), h.get("payoff_payer"), h.get("constraint_used"))
            ),
            "bidirectional_hit": h.get("bidirectional_hit"),
            "probe_factor": (top.get("probe") or {}).get("factor"),
            "probe_side": (top.get("probe") or {}).get("side"),
            "efr": (top.get("feasibility") or {}).get("efr"),
            "research_value": (top.get("feasibility") or {}).get("research_value"),
        }

    return {
        "ok": ok,
        "schema": "qiyu_research_discovery_v1",
        "run_id": run_id,
        "stages": stages,
        "survivors": [
            {k: v for k, v in s.items() if k != "probe_returns"} for s in survivors
        ],
        "n_survivors": len(survivors),
        "handoff": handoff,
        "archive_elites": stages.get("map_elites"),
        "present_to_assembly": ok,
        "human_banner_zh": (
            "研究发现通过：%d 个假设经裸探针+反证矩阵+EFR+多重检验后存活，可进入组装。"
            % len(survivors)
            if ok else
            "当前搜索空间无可信候选（裸探针/反证/EFR/多重检验未通过）。禁止硬凑完整策略。"
        ),
        "at": _now(),
    }


def probe():
    return {
        "ok": True,
        "provider": "research_discovery_v1",
        "modules": [
            "research_ledger", "research_blackboard", "mechanism_graph",
            "phenomenon_scanner", "symbolic_searcher", "heterogeneous_committee",
            "probe_protocol", "antifalsify", "map_elites_archive",
            "multiple_testing", "edge_friction", "creation_multiverse",
        ],
        "at": _now(),
    }
