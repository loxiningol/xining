# -*- coding: utf-8 -*-
"""Research discovery orchestrator — BEFORE strategy assembly.

Paradigm: searchable mechanism/phenomenon population → cheap probes →
antifalsify evidence → QD archive → only then hand off to factor/assembly.
"""
from __future__ import print_function

import copy
import os
from datetime import datetime

from . import antifalsify
from . import creation_multiverse as multiverse
from . import edge_friction as efr_mod
from . import generator_scorecard as scorecard
from . import heterogeneous_committee as committee
from . import learning_loop as learn
from . import map_elites_archive as qd
from . import mechanism_graph as mgraph
from . import multiple_testing as mtest
from . import parameter_platform as paramplat
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
        # Only treat as hard demand when not an explicit prohibition / revoke note
        if not any(k in text for k in ("禁止", "撤销", "不作", "不得", "已撤销", "不要硬凑")):
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
        "max_trial_budget": int(c.get("max_trial_budget") or 320),
        "min_evidence": [
            "naked_probe", "antifalsify_matrix", "leakage_audit",
            "causal_boundary", "execution_feasibility", "efr",
        ],
        "constraints": c,
        "forced_weekly_demand": forced_weekly,
        "requirement_feasible": feasible,
        "warnings_zh": warnings,
        "at": _now(),
    }
    return contract


def build_hypothesis_population(brief, symbol, timeframe, factor_matrix, fwd_returns,
                                max_mechanisms=14, max_phenomena=24, run_id=None,
                                budget_plan=None):
    """Independent heterogeneous committee submissions (no early pick-1, no chat).

    budget_plan (from learning allocator) reshapes counts and re-ranks by beliefs.
    """
    run_id = run_id or ledger.new_run_id("pop")
    budget_plan = budget_plan or learn.plan_next_budget(context={
        "base_max_mechanisms": max_mechanisms,
        "base_max_phenomena": max_phenomena,
        "symbol": symbol,
    })
    knobs = budget_plan.get("knobs") or {}
    max_mechanisms = int(knobs.get("max_mechanisms") or max_mechanisms)
    max_phenomena = int(knobs.get("max_phenomena") or max_phenomena)

    director = committee.research_director_budget()
    director["learned_allocation"] = {
        "knobs": knobs,
        "family_priority": budget_plan.get("family_priority"),
        "weights": budget_plan.get("weights"),
    }
    board.write(run_id, "research_director", "budget", director)

    mech = committee.run_mechanism_scientist(
        brief, symbol, timeframe, run_id, limit=max_mechanisms,
    )
    emp = committee.run_empirical_scientist(
        factor_matrix, fwd_returns, run_id, max_phenomena=max_phenomena,
    )
    # optional symbolic pop boost via env for allocator weight
    if knobs.get("sym_pop_boost"):
        os.environ["QIYU_SYM_POP"] = str(int(60 * float(knobs.get("sym_pop_boost") or 1.0)))
    sym_pack = committee.run_symbolic_searcher(factor_matrix, fwd_returns, run_id)

    for pack, gen_name in (
        (mech, "mechanism_scientist"),
        (emp, "empirical_scientist"),
        (sym_pack, "symbolic_searcher"),
    ):
        scorecard.record_hypothesis(gen_name, n=int(pack.get("n") or 0))

    hyps = committee.merge_independent_hypotheses(mech, emp, sym_pack)
    hyps = learn.apply_population_priors(
        hyps, family_priority=budget_plan.get("family_priority"),
    )
    dedup = qd.dedupe_hypotheses(hyps)
    return {
        "ok": True,
        "run_id": run_id,
        "population_first": True,
        "early_pick_one": False,
        "budget_plan": {
            "knobs": knobs,
            "family_priority": budget_plan.get("family_priority"),
            "top_arms": (budget_plan.get("top_arms") or [])[:5],
        },
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


def _emit_progress(stage_key, detail="", percent=None, extras=None):
    """Best-effort live progress for 管道1/管道2 frontend."""
    try:
        from .parallel_creation import report_progress
        report_progress(stage_key, detail=detail, percent=percent, extras=extras)
    except Exception:
        pass


def run_discovery(
    symbol,
    timeframe,
    brief="",
    factor_matrix=None,
    fwd_returns=None,
    candles=None,
    constraints=None,
    run_id=None,
    max_hypotheses_probe=None,
    min_efr=1.5,
):
    """Full discovery stages 0–early robustness. Returns survivors for assembly."""
    run_id = run_id or ledger.new_run_id("discover")
    max_hypotheses_probe = int(
        max_hypotheses_probe
        or (constraints or {}).get("max_hypotheses_probe")
        or os.environ.get("QIYU_MAX_HYP_PROBE")
        or 28
    )
    # Tiny-VPS cap
    max_hypotheses_probe = max(8, min(int(max_hypotheses_probe), 40))
    stages = {}
    _emit_progress("contract", detail="编译研究契约 · %s %s" % (symbol, timeframe), percent=12)
    contract = compile_research_contract(brief, symbol, timeframe, constraints)
    stages["contract"] = contract
    ledger.append_event({
        "event_type": "contract",
        "symbol": symbol,
        "timeframe": timeframe,
        "feasible": contract.get("requirement_feasible"),
        "warnings": contract.get("warnings_zh"),
        "experiment_id": ledger.experiment_id(run_id),
        "git_hash": ledger.git_hash_short(),
    }, run_id=run_id)

    if contract.get("requirement_feasible") is False and str(
        (constraints or {}).get("allow_unreachable_targets") or ""
    ).lower() not in ("1", "true", "yes"):
        stages["contract_flag"] = "unreachable_target_soft_block"

    # Learning: allocate next budget BEFORE population (closes prior loop)
    budget_plan = learn.plan_next_budget(context={
        "base_max_mechanisms": int((constraints or {}).get("max_mechanisms") or 14),
        "base_max_phenomena": int((constraints or {}).get("max_phenomena") or 24),
        "base_max_hyp_probe": max_hypotheses_probe,
        "symbol": symbol,
    })
    stages["budget_allocation"] = {
        "knobs": budget_plan.get("knobs"),
        "family_priority": budget_plan.get("family_priority"),
        "top_arms": (budget_plan.get("top_arms") or [])[:5],
    }
    max_hypotheses_probe = int(
        (budget_plan.get("knobs") or {}).get("max_hypotheses_probe") or max_hypotheses_probe
    )
    max_hypotheses_probe = max(8, min(int(max_hypotheses_probe), 40))

    _emit_progress("population", detail="生成机制种群（委员会并行提交）", percent=22)
    pop = build_hypothesis_population(
        brief, symbol, timeframe, factor_matrix, fwd_returns,
        max_mechanisms=int((constraints or {}).get("max_mechanisms") or 14),
        max_phenomena=int((constraints or {}).get("max_phenomena") or 24),
        run_id=run_id,
        budget_plan=budget_plan,
    )
    stages["population"] = {
        "n_hypotheses": len(pop.get("hypotheses") or []),
        "n_mechanisms": (pop.get("mechanisms") or {}).get("n"),
        "n_phenomena": (pop.get("phenomena") or {}).get("n"),
        "n_symbolic": ((pop.get("committee") or {}).get("symbolic_searcher") or {}).get("n"),
        "committee": pop.get("committee"),
        "population_first": True,
        "early_pick_one": False,
        "budget_plan": pop.get("budget_plan"),
        "dedupe_dropped": len((pop.get("dedupe") or {}).get("dropped") or []),
        "bidirectional_hits": sum(
            1 for h in (pop.get("hypotheses") or []) if h.get("bidirectional_hit")
        ),
    }
    _emit_progress(
        "committee",
        detail="异构委员会完成 · 假设 %s" % len(pop.get("hypotheses") or []),
        percent=35,
        extras={"n_hypotheses": len(pop.get("hypotheses") or [])},
    )
    _emit_progress("probe", detail="裸探测 / 摩擦检验进行中", percent=60)
    for h in (pop.get("hypotheses") or [])[:120]:
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
    learning_updates = []
    n_bars = len(fwd_returns or [])
    span_days = None
    if candles and len(candles) >= 2:
        try:
            from . import creation_return_hardness as rh
            span_days = rh.span_days_from_ts(candles[0].get("ts"), candles[-1].get("ts"))
        except Exception:
            span_days = None

    def _learn_close(hyp, pcon, stage, **kw):
        upd = learn.close_creation_outcome(
            hyp, pcon, stage, run_id=run_id, symbol=symbol, timescale="medium", **kw
        )
        learning_updates.append({
            "hypothesis_id": (hyp or {}).get("hypothesis_id"),
            "fail_stage": stage,
            "primary": ((upd.get("attribution") or {}).get("primary")),
        })
        return upd

    tested = 0
    for h in (pop.get("hypotheses") or [])[: int(max_hypotheses_probe)]:
        pcon = learn.register_and_probe_prepare(h, symbol, timeframe, run_id)

        # completeness gate for theory path
        if h.get("path") == "theory_to_data":
            comp = h.get("completeness") or {}
            if comp and not comp.get("passed"):
                ledger.append_event({
                    "event_type": "hypothesis_rejected_incomplete",
                    "hypothesis_id": h.get("hypothesis_id"),
                }, run_id=run_id)
                _learn_close(h, pcon, "incomplete_mechanism")
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
            _learn_close(h, pcon, "naked_probe", probe=pr.get("best") or pr)
            continue

        best = pr.get("best") or {}
        # Early multiverse on naked probe returns (multi-generator)
        mv = multiverse.survival_test(best.get("trade_returns") or [])
        ledger.append_event({
            "event_type": "multiverse_probe",
            "hypothesis_id": h.get("hypothesis_id"),
            "passed": mv.get("passed"),
            "generators": mv.get("generators"),
        }, run_id=run_id)
        if not mv.get("passed"):
            _learn_close(h, pcon, "multiverse", probe=best, multiverse=mv)
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
            _learn_close(h, pcon, "antifalsify", probe=best, multiverse=mv, antifalsify=af)
            continue

        # Leakage / causal / execution named roles
        leak = committee.run_leakage_auditor(
            (factor_matrix or {}).get(best.get("factor")) or [],
            fwd_returns,
            side=best.get("side") or "high",
            run_id=run_id,
        )
        if not leak.get("passed"):
            _learn_close(
                h, pcon, "leakage", probe=best, multiverse=mv, antifalsify=af, leakage=leak,
            )
            continue
        causal = committee.run_causal_auditor(af, causal_claim_flag=False, run_id=run_id)
        if not causal.get("passed"):
            _learn_close(
                h, pcon, "antifalsify", probe=best, multiverse=mv, antifalsify=af, leakage=leak,
            )
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
            _learn_close(
                h, pcon, "efr", probe=best, multiverse=mv, antifalsify=af,
                leakage=leak, efr=feas,
            )
            continue

        exe = committee.run_execution_engineer(
            best, efr_pack=feas, run_id=run_id, min_efr=min_efr,
        )
        if not exe.get("passed"):
            _learn_close(
                h, pcon, "execution", probe=best, multiverse=mv, antifalsify=af,
                leakage=leak, efr=feas, execution=exe,
            )
            continue

        # Lite parameter platform around surviving factor
        psearch = paramplat.search(
            (factor_matrix or {}).get(best.get("factor")) or [],
            fwd_returns,
            method="sobol",
            max_evals=int(os.environ.get("QIYU_PARAM_MAX_EVALS") or 24),
            run_id=run_id,
        )
        if psearch.get("best") and psearch["best"].get("passed"):
            best = dict(best)
            best["param_platform_best"] = psearch.get("best")

        # Constructive red team: opposing-family naked probe must lose
        red = committee.run_constructive_redteam(
            h, factor_matrix, fwd_returns, run_id,
        )
        if not red.get("passed"):
            _learn_close(
                h, pcon, "redteam", probe=best, multiverse=mv, antifalsify=af,
                leakage=leak, efr=feas, execution=exe, redteam=red,
            )
            continue

        # Evidence-field judge (non-LLM; Kimi optional comment only)
        judgment = committee.judge_from_evidence({
            "naked_probe_passed": True,
            "antifalsify_passed": bool(af.get("passed")),
            "efr_passed": bool(feas.get("passed")),
            "redteam_passed": bool(red.get("passed")),
            "leakage_passed": bool(leak.get("passed")),
            "causal_boundary_passed": bool(causal.get("passed")),
            "execution_passed": bool(exe.get("passed")),
            "bidirectional_hit": bool(h.get("bidirectional_hit")),
            "dsr_passed": False,
        }, run_id=run_id)

        archive, elite_row, _replaced = qd.upsert(
            archive, h, probe_best=best, antifalsify=af,
            efr=feas.get("efr"), n_bars=n_bars,
        )
        row = {
            "hypothesis": h,
            "prediction_contract_hash": pcon.get("contract_hash"),
            "probe": {k: v for k, v in best.items() if k != "trade_returns"},
            "probe_returns": best.get("trade_returns") or [],
            "multiverse": {
                "passed": mv.get("passed"),
                "profit_frac": mv.get("profit_frac"),
                "median_return": mv.get("median_return"),
                "generators": mv.get("generators"),
                "generator_summary": mv.get("generator_summary"),
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
            "leakage": {
                "passed": leak.get("passed"),
                "issues": leak.get("issues"),
            },
            "causal_boundary": {
                "passed": causal.get("passed"),
                "causal_claim_allowed": False,
            },
            "execution": {
                "passed": exe.get("passed"),
                "issues": exe.get("issues"),
            },
            "parameter_platform": {
                "n_evaluated": psearch.get("n_evaluated"),
                "n_passed": psearch.get("n_passed"),
                "best_params": ((psearch.get("best") or {}).get("params")),
                "best_mean_net": ((psearch.get("best") or {}).get("mean_net")),
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
            _learn_close(
                h, pcon, "judge", probe=best, multiverse=mv, antifalsify=af,
                leakage=leak, efr=feas, execution=exe, redteam=red,
            )
            continue
        learn_ok = _learn_close(
            h, pcon, "survived", probe=best, multiverse=mv, antifalsify=af,
            leakage=leak, efr=feas, execution=exe, redteam=red,
        )
        row["learning"] = {
            "primary": ((learn_ok.get("attribution") or {}).get("primary")),
            "outcome_id": ((learn_ok.get("attribution") or {}).get("outcome_id")),
        }
        survivors.append(row)
        if best.get("trade_returns"):
            probe_returns_for_pbo.append(list(best.get("trade_returns") or [])[:200])

    budget = ledger.effective_trial_budget(run_id=run_id)
    stages["trial_budget"] = budget
    stages["n_probed"] = tested
    stages["learning_updates_n"] = len(learning_updates)

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
            for s in survivors:
                s["multiple_testing_gate"] = mt_pack
            dsr_ok = bool((mt_pack.get("dsr") or {}).get("passed"))
            if not dsr_ok:
                # demote: learning update as multiple_testing fail (overfit risk)
                for s in list(survivors):
                    hyp = s.get("hypothesis") or {}
                    learn.close_creation_outcome(
                        hyp,
                        {"contract_hash": s.get("prediction_contract_hash"),
                         "hypothesis_id": hyp.get("hypothesis_id"),
                         "generator": hyp.get("source"),
                         "expected_direction": "positive",
                         "confidence": 0.5},
                        "multiple_testing",
                        run_id=run_id,
                        symbol=symbol,
                        probe=s.get("probe"),
                        multiverse=s.get("multiverse"),
                        antifalsify=s.get("antifalsify"),
                        timescale="medium",
                    )
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
            for e in elites[:30]
        ],
    }
    _emit_progress(
        "map_elites",
        detail="质量—多样性搜索（MAP-Elites）· 填充格 %s · 精英 %s" % (
            len(archive), len(elites),
        ),
        percent=55,
        extras={"filled_cells": len(archive), "n_elites": len(elites)},
    )

    # Close loop: shadow ingest (fast-only) + next-round budget snapshot
    learning_end = learn.end_of_discovery_learning(run_id, symbol=symbol)
    stages["learning_loop"] = {
        "updates_n": len(learning_updates),
        "updates_sample": learning_updates[:12],
        "next_budget": (learning_end.get("next_budget") or {}).get("knobs"),
        "family_priority": (learning_end.get("next_budget") or {}).get("family_priority"),
        "shadow_available": ((learning_end.get("shadow") or {}).get("available")),
        "isolation_zones": list((learning_end.get("isolation") or {}).keys()),
    }

    ok = len(survivors) > 0
    # Population handoff: ranked survivors, NOT a forced early pick-1
    handoff = None
    handoff_population = []
    if ok:
        for top in survivors[:8]:
            h = top.get("hypothesis") or {}
            handoff_population.append({
                "hypothesis_id": h.get("hypothesis_id"),
                "mechanism_id": h.get("mechanism_id"),
                "family": h.get("family"),
                "path": h.get("path"),
                "factor_hints": h.get("factor_hints") or h.get("observable_proxy") or [],
                "quality": ((top.get("elite") or {}).get("quality")),
                "probe_factor": (top.get("probe") or {}).get("factor"),
                "efr": (top.get("feasibility") or {}).get("efr"),
            })
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
            "rank": 1,
            "population_size": len(survivors),
            "selection_mode": "post_gate_rank_not_early_pick",
            "population_peers": handoff_population,
        }

    return {
        "ok": ok,
        "schema": "qiyu_research_discovery_v3",
        "run_id": run_id,
        "experiment_id": ledger.experiment_id(run_id),
        "git_hash": ledger.git_hash_short(),
        "stages": stages,
        "survivors": [
            {k: v for k, v in s.items() if k != "probe_returns"} for s in survivors
        ],
        "n_survivors": len(survivors),
        "handoff": handoff,
        "handoff_population": handoff_population,
        "archive_elites": stages.get("map_elites"),
        "learning_loop": stages.get("learning_loop"),
        "present_to_assembly": ok,
        "human_banner_zh": (
            "研究发现通过：%d 个假设经裸探针+反证+泄漏/因果边界+执行+EFR+多重检验后存活；"
            "学习闭环已写入归因/记分卡/机制后验/下轮预算。"
            % len(survivors)
            if ok else
            "当前搜索空间无可信候选。失败已分层归因并反馈到生成器记分与预算（禁止硬凑）。"
        ),
        "at": _now(),
    }


def probe():
    return {
        "ok": True,
        "provider": "research_discovery_v3",
        "modules": [
            "research_ledger", "research_blackboard", "mechanism_graph",
            "phenomenon_scanner", "symbolic_searcher", "heterogeneous_committee",
            "probe_protocol", "antifalsify", "map_elites_archive",
            "multiple_testing", "edge_friction", "creation_multiverse",
            "parameter_platform", "prediction_contract", "outcome_attribution",
            "generator_scorecard", "mechanism_beliefs", "budget_allocator",
            "shadow_feedback", "learning_loop",
        ],
        "roles": list(committee.ROLE_CONTRACTS.keys()),
        "learning_mvp": learn.probe().get("mvp"),
        "at": _now(),
    }
