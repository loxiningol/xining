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
from . import research_branch_manager as branch_mgr
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
        "unavailable_data": [
            "level2_order_book", "open_interest", "liquidation_flow",
            "historical_funding", "cross_exchange_basis",
        ],
        "proxy_policy_zh": (
            "OHLCV衍生量只可标为代理证据；缺少直接数据时返回数据不足，"
            "不得用代理失败宣判对应微观机制死亡。"
        ),
        "forbidden_info": ["future_bars", "unrealized_label_leak"],
        "max_complexity": "probe_then_assemble",
        "max_trial_budget": int(c.get("max_trial_budget") or 400),
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
                                budget_plan=None, candles=None):
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
        candles=candles, timeframe=timeframe,
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
    prefer = set(branch_mgr.preferred_mechanism_ids(brief))
    annotated = []
    for h in hyps:
        row = branch_mgr.annotate_hypothesis(h, brief)
        if prefer and str(row.get("mechanism_id") or "") in prefer:
            row["priority_boost"] = float(row.get("priority_boost") or 0) + 2.0
            row["tree_priority"] = True
        annotated.append(row)
    hyps = annotated
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
        "mechanism_trees": [t.get("tree_id") for t in branch_mgr.trees_for_brief(brief)],
        "preferred_mechanism_ids": list(prefer)[:16],
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
    max_hypotheses_probe=None,
    min_efr=1.5,
):
    """Full discovery stages 0–early robustness. Returns survivors for assembly."""
    run_id = run_id or ledger.new_run_id("discover")
    max_hypotheses_probe = int(
        max_hypotheses_probe
        or (constraints or {}).get("max_hypotheses_probe")
        or os.environ.get("QIYU_MAX_HYP_PROBE")
        or 56
    )
    # Tiny-VPS cap: more breadth, but a strict global trial budget below.
    max_hypotheses_probe = max(12, min(int(max_hypotheses_probe), 80))
    stages = {}
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
        "base_max_mechanisms": int((constraints or {}).get("max_mechanisms") or 20),
        "base_max_phenomena": int((constraints or {}).get("max_phenomena") or 36),
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
    max_hypotheses_probe = max(12, min(int(max_hypotheses_probe), 80))

    pop = build_hypothesis_population(
        brief, symbol, timeframe, factor_matrix, fwd_returns,
        max_mechanisms=int((constraints or {}).get("max_mechanisms") or 20),
        max_phenomena=int((constraints or {}).get("max_phenomena") or 36),
        run_id=run_id,
        budget_plan=budget_plan,
        candles=candles,
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
        "mechanism_trees": pop.get("mechanism_trees"),
        "preferred_mechanism_ids": pop.get("preferred_mechanism_ids"),
        "dedupe_dropped": len((pop.get("dedupe") or {}).get("dropped") or []),
        "bidirectional_hits": sum(
            1 for h in (pop.get("hypotheses") or []) if h.get("bidirectional_hit")
        ),
    }
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
    near_miss_diagnostics = []
    state_counts = {}
    failure_lineage = []
    coverage_events = set()
    coverage_horizons = set()
    coverage_mappings = set()
    coverage_directions = set()
    global_trial_limit = int(contract.get("max_trial_budget") or 400)
    global_trials_used = 0
    base_trials_per_hypothesis = max(
        6, min(12, int(global_trial_limit / float(max(max_hypotheses_probe, 1))))
    )
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
    probe_population = (pop.get("hypotheses") or [])[: int(max_hypotheses_probe)]
    for hypothesis_index, h in enumerate(probe_population):
        if global_trials_used >= global_trial_limit:
            break
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

        remaining = max(1, global_trial_limit - global_trials_used)
        initial_budget = min(base_trials_per_hypothesis, remaining)
        pr = probes.probe_hypothesis(
            h, factor_matrix, fwd_returns, candles=candles, symbol=symbol,
            timeframe=timeframe, max_trials=initial_budget,
        )
        global_trials_used += int(pr.get("n_probes") or 0)
        first_state = pr.get("research_state")
        # Near-miss diagnostic budget is bounded. It may compare neighbouring
        # event definitions/horizons/mappings, but still cannot add exits or sizing.
        if first_state in (
            "NEAR_MISS_DIAGNOSTIC", "DIRECTIONAL_BUT_SMALL",
            "VOLATILITY_EFFECT_ONLY", "EXECUTION_MAPPING_FAILURE",
            "PROXY_INADEQUATE", "SAMPLE_INADEQUATE",
            "STATE_CONDITIONAL", "HORIZON_MISMATCH",
            "MECHANISM_CONTRADICTED",
        ) and global_trials_used < global_trial_limit:
            remaining_hypotheses = max(0, len(probe_population) - hypothesis_index - 1)
            reserved_for_breadth = remaining_hypotheses * base_trials_per_hypothesis
            diagnostic_available = max(
                0, global_trial_limit - global_trials_used - reserved_for_breadth
            )
            diagnostic_total = min(
                20, initial_budget + 12,
                initial_budget + diagnostic_available,
            )
            if diagnostic_total > initial_budget:
                expanded = probes.probe_hypothesis(
                    h, factor_matrix, fwd_returns, candles=candles, symbol=symbol,
                    timeframe=timeframe, max_trials=diagnostic_total,
                )
                added = max(0, int(expanded.get("n_probes") or 0) - int(pr.get("n_probes") or 0))
                global_trials_used += added
                pr = expanded
                near_miss_diagnostics.append({
                    "hypothesis_id": h.get("hypothesis_id"),
                    "initial_state": first_state,
                    "final_state": pr.get("research_state"),
                    "extra_trials": added,
                    "best_event": ((pr.get("best") or {}).get("event_id")),
                })
        tested += 1
        research_state = pr.get("research_state") or "NO_DIRECTIONAL_EFFECT"
        state_counts[research_state] = int(state_counts.get(research_state) or 0) + 1
        cov = pr.get("coverage") or {}
        coverage_events.update(
            r.get("event_id") for r in (pr.get("probes") or []) if r.get("event_id")
        )
        coverage_horizons.update(cov.get("horizons_tested") or [])
        coverage_mappings.update(cov.get("execution_mappings_tested") or [])
        coverage_directions.update(cov.get("directions_tested") or [])
        ledger.append_event({
            "event_type": "probe",
            "hypothesis_id": h.get("hypothesis_id"),
            "passed": pr.get("passed"),
            "research_state": research_state,
            "n_probes": pr.get("n_probes"),
            "best_factor": ((pr.get("best") or {}).get("factor")),
            "best_event": ((pr.get("best") or {}).get("event_id")),
            "independent_events": ((pr.get("best") or {}).get("n_independent_events")),
            "mean_net": ((pr.get("best") or {}).get("mean_net")),
        }, run_id=run_id)
        if not pr.get("passed"):
            best_fail = pr.get("best") or {}
            fail_codes = list(
                best_fail.get("failure_codes")
                or pr.get("failure_codes")
                or []
            )
            failure_lineage.append({
                "hypothesis_id": h.get("hypothesis_id"),
                "mechanism_id": h.get("mechanism_id"),
                "family": h.get("family"),
                "mechanism_tree_id": h.get("mechanism_tree_id"),
                "mechanism_branch_id": h.get("mechanism_branch_id"),
                "mechanism_branch_zh": h.get("mechanism_branch_zh"),
                "requires_micro_data": bool(h.get("requires_micro_data")),
                "research_state": research_state,
                "research_state_zh": pr.get("research_state_zh"),
                "failure_codes": fail_codes,
                "failure_codes_zh": [
                    branch_mgr.FAILURE_CODE_ZH.get(str(c), str(c)) for c in fail_codes
                ],
                "best_event": best_fail.get("event_id"),
                "best_horizon": best_fail.get("horizon_bars"),
                "best_mapping": best_fail.get("execution_mapping"),
                "evidence_axes": best_fail.get("evidence_axes"),
                "mean_net": best_fail.get("mean_net"),
                "independent_events": best_fail.get("n_independent_events"),
                "n_event_clusters": best_fail.get("n_event_clusters") or best_fail.get(
                    "n_independent_events"
                ),
                "family_closed": False,
            })
            _learn_close(h, pcon, research_state, probe=best_fail or pr)
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
            symbol=symbol,
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
    budget["effective_trials"] = max(
        float(budget.get("effective_trials") or 0.0), float(global_trials_used)
    )
    budget["probe_trials_used"] = global_trials_used
    budget["probe_trial_limit"] = global_trial_limit
    budget["base_trials_per_hypothesis"] = base_trials_per_hypothesis
    budget["near_miss_extra_trials"] = sum(
        int(x.get("extra_trials") or 0) for x in near_miss_diagnostics
    )
    stages["trial_budget"] = budget
    stages["n_probed"] = tested
    stages["learning_updates_n"] = len(learning_updates)
    coverage_score = (
        min(1.0, len(coverage_events) / 12.0) * 0.35 +
        min(1.0, len(coverage_horizons) / 4.0) * 0.25 +
        min(1.0, len(coverage_mappings) / 3.0) * 0.25 +
        min(1.0, len(coverage_directions) / 2.0) * 0.15
    )
    stages["mechanism_space_coverage"] = {
        "score": coverage_score,
        "event_definitions": len(coverage_events),
        "horizons": sorted(coverage_horizons),
        "execution_mappings": sorted(coverage_mappings),
        "directions": sorted(coverage_directions),
        "available_data": contract.get("available_data"),
        "missing_high_information_data": [
            "真实订单簿", "持仓量", "资金费率历史", "清算流", "跨交易所基差",
        ],
        "family_exhaustion_allowed": False,
        "reason_zh": (
            "当前覆盖可评价具体代理与执行映射，但仅有OHLCV衍生代理，"
            "不足以宣判需要订单簿、持仓量或清算流的整个机制族耗尽。"
        ),
    }
    stages["research_state_counts"] = state_counts
    stages["near_miss_diagnostics"] = near_miss_diagnostics[:40]
    stages["failure_lineage"] = failure_lineage[:120]
    stages["family_closures"] = branch_mgr.evaluate_family_closures(
        failure_lineage,
        stages.get("mechanism_space_coverage") or {},
        contract=contract,
        brief=brief,
    )
    # Mark lineage rows if their tree was exhausted (aggregate-only).
    exhausted_trees = {
        c.get("tree_id")
        for c in ((stages.get("family_closures") or {}).get("closures") or [])
        if c.get("research_state") == "FAMILY_EXHAUSTED"
    }
    if exhausted_trees:
        for row in stages["failure_lineage"]:
            if row.get("mechanism_tree_id") in exhausted_trees:
                row["family_closed"] = True

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
            "ok": True, "passed": False, "reason": "no_admission_survivors",
            "note_zh": "当前没有候选通过三轴准入；这不等于整个机制方向死亡。",
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
    else:
        fc = stages.get("family_closures") or {}
        handoff = {
            "present_to_assembly": False,
            "research_state_counts": state_counts,
            "family_closures": {
                "n_closed": fc.get("n_closed"),
                "closures": fc.get("closures"),
                "branch_reports": (fc.get("branch_reports") or [])[:16],
                "micro_data_available": fc.get("micro_data_available"),
                "rule_zh": fc.get("rule_zh"),
            },
            "mechanism_space_coverage": stages.get("mechanism_space_coverage"),
            "next_actions": [
                "continue_mechanism_branching_via_trees",
                "upgrade_proxies_or_state_filters",
                "acquire_microstructure_feeds_if_data_blocked",
                "do_not_escalate_to_family_exhausted_without_coverage_and_contradiction",
            ],
            "selection_mode": "no_survivor_diagnostic_handoff",
        }

    ledger.append_event({
        "event_type": "research_discovery_summary",
        "present_to_assembly": ok,
        "n_survivors": len(survivors),
        "research_state_counts": stages.get("research_state_counts"),
        "mechanism_space_coverage": stages.get("mechanism_space_coverage"),
        "family_closures": stages.get("family_closures"),
        "near_miss_diagnostics_n": len(near_miss_diagnostics),
        "probe_trials_used": global_trials_used,
        "probe_trial_limit": global_trial_limit,
    }, run_id=run_id)

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
            "当前没有候选同时通过统计、经济幅度与执行可行性三轴，禁止硬凑完整策略。"
            "失败已按证据不足、代理不足、周期错配、执行映射失败等具体状态记录；"
            "单个探针失败未被解释为整个机制族死亡。"
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
            "shadow_feedback", "learning_loop", "research_branch_manager",
        ],
        "roles": list(committee.ROLE_CONTRACTS.keys()),
        "learning_mvp": learn.probe().get("mvp"),
        "at": _now(),
    }
