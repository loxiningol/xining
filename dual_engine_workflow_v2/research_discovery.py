# -*- coding: utf-8 -*-
"""Research discovery orchestrator — BEFORE strategy assembly.

Paradigm: searchable mechanism/phenomenon population → cheap probes →
antifalsify evidence → QD archive → only then hand off to factor/assembly.
"""
from __future__ import print_function

import copy
import hashlib
import json
import os
import re
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
from . import microstructure_bridge as micro
from . import data_evidence_ladder as ladder
from . import research_campaign as campaign
from . import research_contract as rcontract
from . import cheap_probe_stream as cheap
from . import symbolic_searcher as sym
from . import recipe_policy


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def assembly_recipe(hypothesis, probe_best, contract):
    """Freeze the exact post-discovery implementation identity."""
    hypothesis = hypothesis or {}
    probe_best = probe_best or {}
    recipe = {
        "schema": "qiyu_admitted_probe_recipe_v1",
        "research_contract_id": (contract or {}).get("contract_id"),
        "research_contract_body_hash": (
            (rcontract.verify_contract_integrity(contract or {})).get(
                "expected_contract_id"
            )
        ),
        "hypothesis_id": hypothesis.get("hypothesis_id"),
        "mechanism_id": hypothesis.get("mechanism_id"),
        "family": hypothesis.get("family"),
        "event_id": probe_best.get("event_id"),
        "event_kind": probe_best.get("event_kind"),
        "event_logic": probe_best.get("event_logic"),
        "terms": copy.deepcopy(probe_best.get("terms") or []),
        "factor": probe_best.get("factor"),
        "side": probe_best.get("side"),
        "quantile": probe_best.get("q"),
        "trade_direction": probe_best.get("trade_direction"),
        "horizon_bars": probe_best.get("horizon_bars"),
        "execution_mapping": probe_best.get("execution_mapping"),
        "required_entry_timing": copy.deepcopy(
            hypothesis.get("required_entry_timing") or {}
        ),
        "primary_cost_scenario": probe_best.get("primary_cost_scenario"),
        "primary_cost_per_trade": probe_best.get("primary_cost_per_trade"),
        "statistical_returns_are_post_cost": bool(
            probe_best.get("statistical_returns_are_post_cost")
        ),
        "exit_policy": copy.deepcopy(probe_best.get("exit_policy") or {}),
        "protective_stop_policy": copy.deepcopy(
            probe_best.get("protective_stop_policy") or {}
        ),
        "protective_stop_evaluated": (
            probe_best.get("protective_stop_evaluated") is True
        ),
        "execution_leverage": probe_best.get("execution_leverage"),
        "statistical_return_basis": probe_best.get("statistical_return_basis"),
    }
    canonical = json.dumps(
        recipe, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    recipe["recipe_id"] = "recipe_%s" % hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()[:24]
    return recipe


def verify_assembly_recipe(recipe):
    row = dict(recipe or {})
    supplied = row.pop("recipe_id", None)
    canonical = json.dumps(
        row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    expected = "recipe_%s" % hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return bool(supplied and supplied == expected), expected


def compile_research_contract(brief, symbol, timeframe, constraints=None,
                              direction="long", design_seed=None,
                              available_data=None, data_version=None,
                              code_version=None):
    """Stage 0: compile intent into an immutable, machine-checkable contract."""
    c = dict(constraints or {})
    text = str(brief or "")
    # Detect impossible hard return demands (article: do not force deliver)
    weekly_demand = c.get("minimum_weekly_return")
    forced_weekly = None
    if weekly_demand is not None and float(weekly_demand) >= 0.08:
        forced_weekly = float(weekly_demand)
    if "周收益" in text and "8%" in text:
        # An explicit revocation always wins.  Discovery must not silently add
        # back a target that the structured meta stage already removed.
        if not any(k in text for k in (
            "禁止", "撤销", "已撤销", "取消", "放弃", "不作", "不作为", "不得",
            "不要硬凑", "无需", "无须", "不需要", "不要求", "不再要求",
            "非硬", "不是硬门槛", "仅作诊断", "只作诊断", "仅供诊断", "仅作观察",
        )):
            forced_weekly = forced_weekly or 0.08
    feasible = True
    warnings = []
    if forced_weekly and forced_weekly >= 0.08:
        feasible = False
        warnings.append(
            "硬性周收益≥8%在永续短周期上通常不具统计/经济可实现性；"
            "系统将拒绝被迫交付，改为返回无可信候选（除非证据极强）。"
        )
    contract = rcontract.compile_contract(
        text, symbol, timeframe, direction=direction, constraints=c,
        design_seed=design_seed,
        available_data=available_data or ["ohlcv_swap_candles", "derived_factors"],
        data_version=data_version,
        code_version=code_version or ledger.git_hash_short(),
    )
    # Compatibility fields used by the evidence ladder and older reports.
    contract.update({
        "symbol": (contract.get("target") or {}).get("symbol"),
        "timeframe": (contract.get("target") or {}).get("timeframe"),
        "direction": (contract.get("target") or {}).get("direction"),
        "brief_preview": text[:2000],
        "available_data": list(((contract.get("data_contract") or {}).get("available") or [])),
        "unavailable_data": [
            "level2_order_book", "open_interest", "liquidation_flow",
            "historical_funding", "cross_exchange_basis",
        ],
        "proxy_policy_zh": ladder.contract_policy_zh(),
        "architecture_zh": (
            "不能安全地一次性并行物化100–500个完整回测；"
            "通过流式廉价探针、摘要落盘、分级筛选与可恢复调度覆盖约100–500个机制格子。"
        ),
        "forbidden_info": ["future_bars", "unrealized_label_leak"],
        "max_complexity": "probe_then_assemble",
        "max_trial_budget": int(c.get("max_trial_budget") or 520),
        "min_evidence": [
            "naked_probe", "antifalsify_matrix", "leakage_audit",
            "causal_boundary", "execution_feasibility", "efr",
        ],
        "constraints": c,
        "forced_weekly_demand": forced_weekly,
        "requirement_feasible": feasible,
        "warnings_zh": warnings,
        "at": _now(),
    })
    if not contract.get("valid"):
        contract["requirement_feasible"] = False
        contract["warnings_zh"] = list(contract.get("warnings_zh") or []) + [
            "研究契约无效：%s" % ",".join(contract.get("validation_errors") or [])
        ]
    return contract


def _design_seed_hypotheses(design_seed, contract):
    """Convert pre-discovery local/GLM designs into testable population rows."""
    design = (design_seed or {}).get("design_doc") if isinstance(design_seed, dict) else None
    design = design or (design_seed if isinstance(design_seed, dict) else {})
    divergence = design.get("divergence") or {}
    local = list(divergence.get("perspectives") or [])
    glm = list(divergence.get("perspectives_glm") or [])
    perspectives = glm or local
    local_by_family = {}
    for p in local:
        local_by_family.setdefault(str(p.get("family") or ""), p)
    rows = []
    for index, source_row in enumerate(perspectives[:12]):
        if not isinstance(source_row, dict):
            continue
        p = dict(source_row)
        fallback = local_by_family.get(str(p.get("family") or ""), {})
        hints = (
            p.get("factor_hints") or p.get("observable_proxy")
            or fallback.get("factor_hints") or fallback.get("observable_proxy") or []
        )
        identity = "%s|%s|%s|%s" % (
            (contract or {}).get("contract_id"), p.get("id") or index,
            p.get("family"), p.get("thesis_zh") or p.get("refined_logic_zh"),
        )
        hid = "H_design_%s" % hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        target = (contract or {}).get("target") or {}
        row = {
            "hypothesis_id": hid,
            "source": "glm_meta_design" if glm else "structured_meta_design",
            "path": "design_to_data",
            "mechanism_id": p.get("mechanism_id") or "design_%s" % (p.get("id") or index),
            "family": p.get("family") or design.get("mechanism_family") or "design_generated",
            "statement_zh": p.get("thesis_zh") or p.get("refined_logic_zh") or design.get("core_logic_zh"),
            "payoff_payer": p.get("who_pays") or p.get("counterparty_zh") or design.get("counterparty_zh"),
            "constraint_used": p.get("constraint") or p.get("constraints") or [],
            "observable_proxy": list(p.get("observable_proxy") or hints),
            "factor_hints": list(hints),
            "predicted_direction": target.get("direction") or design.get("direction"),
            "horizon": p.get("horizon") or target.get("timeframe"),
            "conditional_on": list(p.get("conditional_on") or []),
            "failure_conditions": list(
                p.get("failure_conditions") or p.get("known_failure_modes")
                or design.get("failure_scenarios_zh") or []
            ),
            "capacity_limit": p.get("capacity") or p.get("capacity_limit") or "unknown",
            "alternative_explanations": list(p.get("alternative_explanations") or []),
            "required_data": list(p.get("required_data") or ["derived_ohlcv_proxy"]),
            "simplest_antifalsify": "shuffle_event_time_and_sign_flip_should_kill_edge",
            "completeness": {"ok": bool(hints), "passed": bool(hints),
                             "missing": [] if hints else ["observable_proxy"]},
            # Keep the audit statement literal: deterministic local structure
            # ran before discovery too, but only GLM rows are AI-generated.
            "ai_generated_before_discovery": bool(glm),
            "structured_generated_before_discovery": True,
            "priority_boost": 4.0,
        }
        rows.append(rcontract.apply_to_hypothesis(row, contract))
    return rows


def build_hypothesis_population(brief, symbol, timeframe, factor_matrix, fwd_returns,
                                max_mechanisms=14, max_phenomena=24, run_id=None,
                                budget_plan=None, candles=None, design_seed=None,
                                research_contract=None):
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
    design_hypotheses = _design_seed_hypotheses(design_seed, research_contract)
    # AI/local structured design is a population member, never an untested winner.
    # It is inserted before deterministic discovery so the evidence gates test the
    # requested mechanisms instead of deciding whether AI is allowed to run.
    hyps = list(design_hypotheses) + list(hyps)
    prefer = set(branch_mgr.preferred_mechanism_ids(brief))
    annotated = []
    for h in hyps:
        row = rcontract.apply_to_hypothesis(h, research_contract)
        row = branch_mgr.annotate_hypothesis(row, brief)
        if prefer and str(row.get("mechanism_id") or "") in prefer:
            row["priority_boost"] = float(row.get("priority_boost") or 0) + 2.0
            row["tree_priority"] = True
        row["priority_boost"] = float(row.get("priority_boost") or 0) + float(
            row.get("contract_priority") or 0
        )
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
            "design_scientist": {
                "n": len(design_hypotheses),
                "llm_enriched": any(
                    h.get("source") == "glm_meta_design" for h in design_hypotheses
                ),
                "ran_before_discovery": True,
            },
        },
        "mechanisms": {"n": mech.get("n"), "hypotheses": mech.get("hypotheses")},
        "phenomena": emp.get("phenomena"),
        "symbolic": sym_pack.get("search"),
        "mechanism_trees": [t.get("tree_id") for t in branch_mgr.trees_for_brief(brief)],
        "preferred_mechanism_ids": list(prefer)[:16],
        "hypotheses_raw_n": len(hyps),
        "design_hypotheses_n": len(design_hypotheses),
        "hypotheses": dedup.get("kept") or [],
        "dedupe": {"dropped": dedup.get("dropped"), "n_kept": dedup.get("n_kept")},
        "at": _now(),
    }


def _schedule_probe_population(hypotheses, max_hypotheses_probe, micro_cov,
                               prefer_ids, contract):
    """Put immutable-contract hypotheses ahead of discovery controls.

    A family-enforced ResearchContract is a resource-allocation constraint, not
    merely a final survivor filter.  If off-family controls consume the early
    probe budget, the requested hypothesis can reach a prediction contract but
    never receive an empirical probe.  Keep a small control tail for
    antifalsification, while guaranteeing that every in-budget contract-family
    hypothesis is scheduled first.
    """
    prefer_ids = set(prefer_ids or [])

    def _rank(h):
        mid = str(h.get("mechanism_id") or "")
        tree = 1 if h.get("mechanism_tree_id") else 0
        pref = 1 if mid in prefer_ids else 0
        micro_ready = 0
        req = h.get("required_data") or []
        if micro_cov >= 0.15 and any(
            "snapshot" in str(x) or "level2" in str(x) or "trade_side" in str(x)
            for x in req
        ):
            micro_ready = 1
        bidir = 1 if h.get("bidirectional_hit") else 0
        return (
            1 if h.get("contract_family_match") else 0,
            1 if h.get("ai_generated_before_discovery") else 0,
            pref, tree, micro_ready, bidir,
            float(h.get("priority_boost") or 0),
        )

    raw = list(hypotheses or [])
    limit = max(1, int(max_hypotheses_probe))
    if contract.get("family_hints_enforced"):
        in_family = sorted(
            [h for h in raw if h.get("contract_family_match")],
            key=_rank, reverse=True,
        )
        controls = sorted(
            [h for h in raw if not h.get("contract_family_match")],
            key=_rank, reverse=True,
        )
        # Requested-family rows own the budget.  Controls can use only unused
        # capacity and remain a bounded antifalsification tail.
        selected = in_family[:limit]
        unused = max(0, limit - len(selected))
        control_limit = min(unused, 4)
        selected.extend(controls[:control_limit])
    else:
        selected = sorted(raw, key=_rank, reverse=True)[:limit]

    scheduled = []
    for h in selected:
        row = dict(h)
        if micro_cov >= 0.15:
            row["micro_data_available"] = True
        scheduled.append(row)
    return scheduled


def run_discovery(
    symbol,
    timeframe,
    direction="long",
    brief="",
    factor_matrix=None,
    fwd_returns=None,
    candles=None,
    constraints=None,
    run_id=None,
    max_hypotheses_probe=None,
    min_efr=1.5,
    design_seed=None,
    research_contract=None,
    data_version=None,
    code_version=None,
):
    """Full discovery stages 0–early robustness. Returns survivors for assembly."""
    run_id = run_id or ledger.new_run_id("discover")
    max_hypotheses_probe = int(
        max_hypotheses_probe
        or (constraints or {}).get("max_hypotheses_probe")
        or os.environ.get("QIYU_MAX_HYP_PROBE")
        or 72
    )
    # Tiny-VPS cap: more breadth, but a strict global trial budget below.
    max_hypotheses_probe = max(16, min(int(max_hypotheses_probe), 96))
    stages = {}
    contract_constraints = dict(constraints or {})
    if research_contract:
        contract_constraints["research_contract"] = research_contract
    contract = compile_research_contract(
        brief, symbol, timeframe, contract_constraints,
        direction=direction, design_seed=design_seed,
        available_data=contract_constraints.get("available_data"),
        data_version=data_version, code_version=code_version,
    )
    contract = micro.enrich_contract(contract, symbol=symbol)
    stages["contract"] = contract
    contract_integrity = rcontract.verify_contract_integrity(contract)
    stages["contract_integrity"] = contract_integrity
    # Inject forward micro snapshots into factor matrix when overlap exists.
    micro_meta = {}
    if factor_matrix is not None and candles:
        factor_matrix, micro_meta = micro.inject_into_factor_matrix(
            factor_matrix, symbol, candles,
        )
    stages["microstructure"] = micro_meta
    required_features = list(
        ((contract.get("feature_contract") or {}).get("required_features") or [])
    )
    factor_matrix, derivation_meta = rcontract.derive_contract_features(
        factor_matrix, candles, timeframe, required_features,
    )
    stages["contract_feature_derivation"] = derivation_meta
    missing_features = [
        name for name in required_features if name not in (factor_matrix or {})
    ]
    runtime_contract_errors = []
    if missing_features and (
        (contract.get("event_contract") or {}).get("require_exact_event_fidelity")
    ):
        runtime_contract_errors.extend(
            "required_feature_missing:%s" % name for name in missing_features
        )
    if not contract_integrity.get("ok"):
        runtime_contract_errors.extend(contract_integrity.get("reasons") or [])
    ledger.append_event({
        "event_type": "contract",
        "contract_id": contract.get("contract_id"),
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "feasible": contract.get("requirement_feasible"),
        "valid": contract.get("valid"),
        "validation_errors": contract.get("validation_errors"),
        "warnings": contract.get("warnings_zh"),
        "experiment_id": ledger.experiment_id(run_id),
        "git_hash": ledger.git_hash_short(),
    }, run_id=run_id)

    if not contract.get("valid") or runtime_contract_errors:
        errors = list(contract.get("validation_errors") or []) + runtime_contract_errors
        data_blocked = any(
            str(x).startswith(("required_data_missing:", "required_feature_missing:"))
            for x in errors
        )
        return {
            "ok": False,
            "schema": "qiyu_research_discovery_v4",
            "run_id": run_id,
            "experiment_id": ledger.experiment_id(run_id),
            "git_hash": ledger.git_hash_short(),
            "error": "research_contract_invalid",
            "detail": {"validation_errors": errors, "data_blocked": data_blocked},
            "outcome": "data_blocked" if data_blocked else "research_rejected",
            "stages": stages,
            "survivors": [],
            "n_survivors": 0,
            "handoff": {
                "present_to_assembly": False,
                "contract_id": contract.get("contract_id"),
                "validation_errors": errors,
            },
            "handoff_population": [],
            "present_to_assembly": False,
            "human_banner_zh": "研究契约未通过，禁止用代理变量静默替代：%s" % ",".join(errors),
            "at": _now(),
        }

    if contract.get("requirement_feasible") is False and str(
        (constraints or {}).get("allow_unreachable_targets") or ""
    ).lower() not in ("1", "true", "yes"):
        stages["contract_flag"] = "unreachable_target_blocked"
        ledger.append_event({
            "event_type": "research_requirement_rejected",
            "contract_id": contract.get("contract_id"),
            "reason": "unreachable_hard_target",
            "warnings": contract.get("warnings_zh"),
        }, run_id=run_id)
        return {
            "ok": False,
            "schema": "qiyu_research_discovery_v4",
            "run_id": run_id,
            "experiment_id": ledger.experiment_id(run_id),
            "git_hash": ledger.git_hash_short(),
            "error": "unreachable_hard_requirement",
            "detail": {
                "data_blocked": False,
                "forced_weekly_demand": contract.get("forced_weekly_demand"),
                "warnings_zh": contract.get("warnings_zh"),
            },
            "outcome": "research_rejected",
            "stages": stages,
            "survivors": [],
            "n_survivors": 0,
            "handoff": {
                "present_to_assembly": False,
                "contract_id": contract.get("contract_id"),
                "reason": "unreachable_hard_target",
            },
            "handoff_population": [],
            "present_to_assembly": False,
            "human_banner_zh": (
                "硬性收益要求不可作为强制交付门槛；本轮已停止，禁止通过过拟合或杠杆硬凑。"
            ),
            "at": _now(),
        }

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
    max_hypotheses_probe = max(16, min(int(max_hypotheses_probe), 96))

    pop = build_hypothesis_population(
        brief, symbol, timeframe, factor_matrix, fwd_returns,
        max_mechanisms=int((constraints or {}).get("max_mechanisms") or 20),
        max_phenomena=int((constraints or {}).get("max_phenomena") or 36),
        run_id=run_id,
        budget_plan=budget_plan,
        candles=candles,
        design_seed=design_seed,
        research_contract=contract,
    )
    stages["population"] = {
        "n_hypotheses": len(pop.get("hypotheses") or []),
        "n_mechanisms": (pop.get("mechanisms") or {}).get("n"),
        "n_phenomena": (pop.get("phenomena") or {}).get("n"),
        "n_symbolic": ((pop.get("committee") or {}).get("symbolic_searcher") or {}).get("n"),
        "n_design": ((pop.get("committee") or {}).get("design_scientist") or {}).get("n"),
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

    # Lean campaign: map + streaming cheap probes (summary-only) before heavy probes.
    # This separates candidate count from resident memory.
    lean = campaign.run_lean_campaign(
        brief=brief,
        symbol=symbol,
        timeframe=timeframe,
        factor_matrix=factor_matrix,
        candles=candles,
        fwd_returns=fwd_returns,
        available_data=contract.get("available_data"),
        micro_meta=stages.get("microstructure") or {},
        campaign_id="lean_%s" % run_id,
        max_cells=int((constraints or {}).get("max_mechanism_cells") or 96),
        max_cheap_probes=int((constraints or {}).get("max_cheap_probes") or 160),
        max_diagnostic=int((constraints or {}).get("max_diagnostic") or 8),
        max_exec_tier=int((constraints or {}).get("max_exec_tier") or 4),
        batch_size=6,
    )
    stages["lean_campaign"] = {
        "campaign_id": lean.get("campaign_id"),
        "evidence_level": lean.get("evidence_level"),
        "skipped": bool(lean.get("skipped")),
        "skip_reason": lean.get("skip_reason"),
        "n_cells": ((lean.get("stages") or {}).get("map") or {}).get("n_cells"),
        "cheap": {
            "n_streamed": ((lean.get("stages") or {}).get("cheap_stream") or {}).get("n_streamed_this_call"),
            "state_counts": ((lean.get("stages") or {}).get("cheap_stream") or {}).get("state_counts"),
            "summaries_path": ((lean.get("stages") or {}).get("cheap_stream") or {}).get("summaries_path"),
            "architecture_zh": ((lean.get("stages") or {}).get("cheap_stream") or {}).get("architecture_zh"),
        },
        "diagnostic_n": ((lean.get("stages") or {}).get("diagnostic") or {}).get("n"),
        "execution_shortlist": [
            {
                "cell_id": row.get("cell_id"),
                "hypothesis_id": ((row.get("hypothesis") or {}).get("hypothesis_id")),
                "diag_state": row.get("diag_state"),
                "factor": ((row.get("probe_best") or {}).get("factor")),
                "best_net": row.get("best_net"),
            }
            for row in (
                ((lean.get("stages") or {}).get("execution_tier_shortlist") or [])
            )
        ],
        "branch_policies": lean.get("branch_policies"),
        "limits_zh": lean.get("limits_zh"),
        "policy_zh": lean.get("policy_zh"),
    }
    ledger.append_event({
        "event_type": "lean_campaign",
        "campaign_id": lean.get("campaign_id"),
        "n_cells": stages["lean_campaign"]["n_cells"],
        "cheap_n": stages["lean_campaign"]["cheap"].get("n_streamed"),
        "cheap_states": stages["lean_campaign"]["cheap"].get("state_counts"),
        "evidence_level": lean.get("evidence_level"),
    }, run_id=run_id)
    promote_mids = set()
    for row in (((lean.get("stages") or {}).get("cheap_stream") or {}).get("promote") or []):
        if row.get("mechanism_id"):
            promote_mids.add(str(row.get("mechanism_id")))
        promote_mids.add(str(row.get("cell_id") or ""))
    # Annotate population with evidence-ladder claim mode (proxy vs strong block).
    annotated_pop = []
    for h in (pop.get("hypotheses") or []):
        row = ladder.annotate_hypothesis(
            h, contract.get("available_data"), stages.get("microstructure") or {},
        )
        if str(row.get("mechanism_id") or "") in promote_mids:
            row["priority_boost"] = float(row.get("priority_boost") or 0) + 3.0
            row["lean_promote"] = True
        annotated_pop.append(row)
    # The execution-tier shortlist is an evidence-backed source of hypotheses,
    # not telemetry.  Promote it into the same full gate path; do not bypass any
    # antifalsification, execution, judge, or multiple-testing check.
    seen_hypotheses = set(str(h.get("hypothesis_id") or "") for h in annotated_pop)
    for shortlist_row in (
        ((lean.get("stages") or {}).get("execution_tier_shortlist") or [])
    ):
        hyp = shortlist_row.get("hypothesis") or {}
        hid = str(hyp.get("hypothesis_id") or "")
        if not hid or hid in seen_hypotheses:
            continue
        row = rcontract.apply_to_hypothesis(hyp, contract)
        row = branch_mgr.annotate_hypothesis(row, brief)
        row["lean_execution_shortlist"] = True
        row["priority_boost"] = float(row.get("priority_boost") or 0) + 4.0
        row["lean_probe_evidence"] = shortlist_row.get("probe_best") or {}
        annotated_pop.append(row)
        seen_hypotheses.add(hid)
    annotated_pop.sort(
        key=lambda x: (
            1 if x.get("contract_family_match") else 0,
            1 if x.get("ai_generated_before_discovery") else 0,
            1 if x.get("lean_execution_shortlist") else 0,
            1 if x.get("lean_promote") else 0,
            1 if x.get("may_research") is not False else 0,
            float(x.get("priority_boost") or 0),
        ),
        reverse=True,
    )
    pop["hypotheses"] = annotated_pop
    stages["population"]["lean_promote_n"] = sum(1 for h in annotated_pop if h.get("lean_promote"))
    stages["population"]["execution_shortlist_promoted_n"] = sum(
        1 for h in annotated_pop if h.get("lean_execution_shortlist")
    )
    stages["population"]["claim_proxy_n"] = sum(
        1 for h in annotated_pop if h.get("claim_mode") == "proxy_claim"
    )

    cheap_trial_descriptors = []
    try:
        for cheap_row in cheap.load_summaries(lean.get("campaign_id"), limit=2000):
            cell_id = str(cheap_row.get("cell_id") or "")
            cheap_trial_descriptors.append({
                "family": cheap_row.get("family") or cheap_row.get("tree_id") or "cheap_probe",
                "mechanism_id": cheap_row.get("mechanism_id") or cell_id.split("__fb", 1)[0],
                "normalized_event": re.sub(r"__fb\d+$", "", cell_id),
                "factors": [cheap_row.get("factor")] if cheap_row.get("factor") else [],
                "direction": cheap_row.get("side"),
                "horizon": cheap_row.get("horizon_bars"),
                "execution_mapping": "cheap_probe",
            })
    except Exception:
        cheap_trial_descriptors = []

    archive = {}
    survivors = []
    trial_descriptors = []
    learning_updates = []
    near_miss_diagnostics = []
    state_counts = {}
    failure_lineage = []
    coverage_events = set()
    coverage_horizons = set()
    coverage_mappings = set()
    coverage_directions = set()
    global_trial_limit = int(contract.get("max_trial_budget") or 520)
    global_trials_used = 0
    base_trials_per_hypothesis = max(
        6, min(14, int(global_trial_limit / float(max(max_hypotheses_probe, 1))))
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
            "failure_codes": list((kw.get("probe") or {}).get("failure_codes") or []),
        })
        if stage in (
            "incomplete_mechanism", "multiverse", "antifalsify", "leakage",
            "efr", "execution", "redteam", "multiple_testing", "judge",
        ):
            failure_lineage.append({
                "hypothesis_id": (hyp or {}).get("hypothesis_id"),
                "mechanism_id": (hyp or {}).get("mechanism_id"),
                "family": (hyp or {}).get("family"),
                "mechanism_tree_id": (hyp or {}).get("mechanism_tree_id"),
                "mechanism_branch_id": (hyp or {}).get("mechanism_branch_id"),
                "fail_stage": stage,
                "research_state": stage,
                "failure_codes": list((kw.get("probe") or {}).get("failure_codes") or []),
                "probe_event": (kw.get("probe") or {}).get("event_id"),
                "probe_horizon": (kw.get("probe") or {}).get("horizon_bars"),
                "execution_mapping": (kw.get("probe") or {}).get("execution_mapping"),
                "formal_capability_reasons": list(
                    (((kw.get("probe") or {}).get("formal_capability") or {}).get(
                        "reasons"
                    ) or [])
                ),
                "multiverse_passed": (kw.get("multiverse") or {}).get("passed"),
                "antifalsify_passed": (kw.get("antifalsify") or {}).get("passed"),
                "leakage_passed": (kw.get("leakage") or {}).get("passed"),
                "efr_passed": (kw.get("efr") or {}).get("passed"),
                "execution_passed": (kw.get("execution") or {}).get("passed"),
                "redteam_passed": (kw.get("redteam") or {}).get("passed"),
                "family_closed": False,
            })
        return upd

    tested = 0
    prefer_ids = set(pop.get("preferred_mechanism_ids") or branch_mgr.preferred_mechanism_ids(brief))
    micro_cov = float((stages.get("microstructure") or {}).get("coverage_ratio") or 0.0)
    raw_pop = list(pop.get("hypotheses") or [])
    probe_population = _schedule_probe_population(
        raw_pop, max_hypotheses_probe, micro_cov, prefer_ids, contract,
    )
    stages["population"]["probe_scheduled_n"] = len(probe_population)
    stages["population"]["probe_contract_family_n"] = sum(
        1 for h in probe_population if h.get("contract_family_match")
    )
    stages["population"]["probe_control_n"] = sum(
        1 for h in probe_population if not h.get("contract_family_match")
    )
    stages["population"]["contract_family_scheduled_first"] = bool(
        not contract.get("family_hints_enforced")
        or not probe_population
        or probe_population[0].get("contract_family_match")
    )

    stop_reason = None
    branch_tested = set()
    for hypothesis_index, h in enumerate(probe_population):
        if global_trials_used >= global_trial_limit:
            stop_reason = "trial_budget_exhausted"
            break
        # Coverage-driven stop: preferred tree branches evaluated + high space coverage
        # and no survivor — do not burn remaining budget on redundant symbolic tails.
        if (
            tested >= 12
            and len(branch_tested) >= 4
            and len(coverage_events) >= 24
            and len(coverage_horizons) >= 3
            and len(coverage_mappings) >= 3
            and not survivors
            and hypothesis_index >= max(16, int(0.55 * len(probe_population)))
        ):
            stop_reason = "coverage_sufficient_no_survivor"
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
                    "formal_capability_reasons": list(
                        pr.get("formal_capability_reasons") or []
                    ),
                })
        for trial in (pr.get("probes") or []):
            event_id = str(trial.get("event_id") or "")
            normalized_event = re.sub(r"_q\d+(?:\.\d+)?", "_q*", event_id)
            trial_descriptors.append({
                "family": h.get("family") or h.get("mechanism_tree_id") or "unclassified",
                "mechanism_id": h.get("mechanism_id") or h.get("hypothesis_id"),
                "normalized_event": normalized_event,
                "factors": [
                    term.get("factor") for term in (trial.get("terms") or [])
                    if term.get("factor")
                ] or ([trial.get("factor")] if trial.get("factor") else []),
                "direction": trial.get("trade_direction"),
                "horizon": trial.get("horizon_bars"),
                "execution_mapping": trial.get("execution_mapping"),
            })
        tested += 1
        if h.get("mechanism_branch_id"):
            branch_tested.add("%s:%s" % (h.get("mechanism_tree_id"), h.get("mechanism_branch_id")))
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
            "formal_capability_reasons": list(
                pr.get("formal_capability_reasons") or []
            ),
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
                "formal_capability_reasons": list(
                    pr.get("formal_capability_reasons") or []
                ),
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

        antifalsify_series = (
            best.get("event_mask")
            if best.get("event_kind") == "human_contract_exact"
            else ((factor_matrix or {}).get(best.get("factor")) or [])
        )
        af = antifalsify.run_antifalsify_battery(
            antifalsify_series,
            fwd_returns,
            side="high" if best.get("event_kind") == "human_contract_exact" else (best.get("side") or "high"),
            factor_matrix=factor_matrix,
            precomputed_event_mask=best.get("event_kind") == "human_contract_exact",
            candles=candles,
            symbol=symbol,
            timeframe=timeframe,
            horizon=best.get("horizon_bars") or 3,
            trade_direction=best.get("trade_direction") or "long",
            execution_mapping=best.get("execution_mapping") or "next_bar_open",
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
            (
                best.get("event_mask")
                if best.get("event_kind") == "human_contract_exact"
                else ((factor_matrix or {}).get(best.get("factor")) or [])
            ),
            fwd_returns,
            side=best.get("side") or "high",
            run_id=run_id,
            precomputed_event_mask=best.get("event_kind") == "human_contract_exact",
            candles=candles,
            symbol=symbol,
            timeframe=timeframe,
            horizon=best.get("horizon_bars") or 3,
            trade_direction=best.get("trade_direction") or "long",
            execution_mapping=best.get("execution_mapping") or "next_bar_open",
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
        if best.get("event_kind") == "human_contract_exact":
            psearch = {
                "ok": True,
                "skipped": True,
                "reason": "immutable_exact_event_has_no_quantile_parameter_search",
                "n_evaluated": 0,
                "n_passed": 0,
                "best": None,
            }
        else:
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
            main_probe_best=best,
        )
        if not red.get("passed"):
            _learn_close(
                h, pcon, "redteam", probe=best, multiverse=mv, antifalsify=af,
                leakage=leak, efr=feas, execution=exe, redteam=red,
            )
            continue

        # Freeze deterministic evidence now, but do not call the independent
        # judge until this candidate has its own DSR/PBO result.  The previous
        # order sent ``dsr_passed=False`` before DSR had even run.
        judge_evidence = {
            "naked_probe_passed": True,
            "antifalsify_passed": bool(af.get("passed")),
            "efr_passed": bool(feas.get("passed")),
            "redteam_passed": bool(red.get("passed")),
            "leakage_passed": bool(leak.get("passed")),
            "causal_boundary_passed": bool(causal.get("passed")),
            "execution_passed": bool(exe.get("passed")),
            "bidirectional_hit": bool(h.get("bidirectional_hit")),
        }

        archive, elite_row, _replaced = qd.upsert(
            archive, h, probe_best=best, antifalsify=af,
            efr=feas.get("efr"), n_bars=n_bars,
        )
        row = {
            "hypothesis": h,
            "prediction_contract_hash": pcon.get("contract_hash"),
            "probe": {
                k: v for k, v in best.items()
                if k not in ("trade_returns", "pbo_bar_returns", "event_mask")
            },
            "probe_returns": best.get("trade_returns") or [],
            "pbo_returns": best.get("pbo_bar_returns") or [],
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
            "judge": {"pending": True, "reason": "awaiting_candidate_multiple_testing"},
            "judge_evidence": judge_evidence,
            "elite": elite_row,
        }
        capability = recipe_policy.capability_from_row(best, contract)
        row["formal_capability"] = capability
        if not capability.get("ok"):
            # Defense in depth.  probe_hypothesis already excludes this row,
            # but assembly must independently refuse identity drift.
            continue
        if contract.get("family_hints_enforced") and not h.get("contract_family_match"):
            # Opposing families remain useful antifalsification controls, but
            # they cannot become a strategy under an explicit human family.
            continue
        row["recipe"] = assembly_recipe(h, best, contract)
        row["recipe_id"] = row["recipe"]["recipe_id"]
        survivors.append(row)

    budget = ledger.effective_trial_budget(run_id=run_id)
    ledger_effective = float(budget.get("effective_trials") or 0.0)
    all_trial_descriptors = list(cheap_trial_descriptors) + list(trial_descriptors)
    raw_selection_trials = int(len(cheap_trial_descriptors) + global_trials_used)
    effective_estimate = mtest.estimate_effective_trials(
        all_trial_descriptors, raw_trials=raw_selection_trials,
    )
    budget["ledger_effective_trials_diagnostic_only"] = ledger_effective
    budget["effective_trials"] = float(effective_estimate.get("effective_trials") or 1.0)
    budget["effective_trial_estimate"] = effective_estimate
    budget["raw_selection_trials"] = raw_selection_trials
    budget["cheap_trials_used"] = len(cheap_trial_descriptors)
    budget["probe_trials_used"] = global_trials_used
    budget["probe_trial_limit"] = global_trial_limit
    budget["base_trials_per_hypothesis"] = base_trials_per_hypothesis
    budget["near_miss_extra_trials"] = sum(
        int(x.get("extra_trials") or 0) for x in near_miss_diagnostics
    )
    budget["stop_reason"] = stop_reason or (
        "completed_population" if tested >= len(probe_population) else "unknown"
    )
    budget["branch_tested_n"] = len(branch_tested)
    budget["coverage_stop_enabled"] = True
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
        "family_exhaustion_allowed": bool(
            micro_cov >= 0.35
            and not (contract.get("unavailable_data") or [])
        ),
        "micro_coverage_ratio": micro_cov,
        "reason_zh": (
            "缺清算/OI/历史L2 时仅阻断对应强声明，OHLCV代理与前向微观校准继续；"
            "禁止把强声明阻断写成家族死亡。当前实现用流式廉价探针覆盖机制格子，"
            "而非一次性并行物化全部完整回测。"
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

    # Every provisional survivor receives its own DSR/path test.  PBO compares
    # the same candidate set on the shared candle clock.  One candidate failing
    # can no longer delete untested peers.
    mt_pack = None
    if survivors:
        survivors.sort(
            key=lambda r: float(((r.get("elite") or {}).get("quality") or 0)),
            reverse=True,
        )
        pbo_matrix = [s.get("pbo_returns") or [] for s in survivors]
        admitted = []
        rejected = []
        candidate_results = []
        for rank_index, candidate in enumerate(list(survivors)):
            hyp = candidate.get("hypothesis") or {}
            mt_row = mtest.evaluate_multiple_testing(
                candidate.get("probe_returns") or [],
                [s.get("probe_returns") or [] for s in survivors],
                n_trials_effective=budget.get("effective_trials") or 1,
                pbo_returns_matrix=pbo_matrix,
            )
            candidate["multiple_testing_gate"] = mt_row
            evidence = dict(candidate.get("judge_evidence") or {})
            evidence["dsr_passed"] = bool((mt_row.get("dsr") or {}).get("passed"))
            evidence["pbo_passed"] = bool((mt_row.get("pbo") or {}).get("passed"))
            judgment = None
            rejection_stage = None
            if mt_row.get("passed"):
                judgment = committee.judge_from_evidence(evidence, run_id=run_id)
                candidate["judge"] = judgment
                if judgment.get("admit_to_assembly"):
                    admitted.append(candidate)
                    learn_row = _learn_close(
                        hyp,
                        {
                            "contract_hash": candidate.get("prediction_contract_hash"),
                            "hypothesis_id": hyp.get("hypothesis_id"),
                            "generator": hyp.get("source"),
                            "expected_direction": (contract.get("target") or {}).get("direction"),
                            "confidence": 0.5,
                        },
                        "survived",
                        probe=candidate.get("probe"),
                        multiverse=candidate.get("multiverse"),
                        antifalsify=candidate.get("antifalsify"),
                        leakage=candidate.get("leakage"),
                        efr=candidate.get("feasibility"),
                        execution=candidate.get("execution"),
                        redteam=candidate.get("redteam"),
                    )
                    candidate["learning"] = {
                        "primary": ((learn_row.get("attribution") or {}).get("primary")),
                        "outcome_id": ((learn_row.get("attribution") or {}).get("outcome_id")),
                    }
                else:
                    rejection_stage = "judge"
            else:
                rejection_stage = "multiple_testing"

            if rejection_stage:
                rejected.append({
                    "hypothesis_id": hyp.get("hypothesis_id"),
                    "rank_before_testing": rank_index + 1,
                    "stage": rejection_stage,
                    "dsr": ((mt_row.get("dsr") or {}).get("dsr")),
                    "pbo": ((mt_row.get("pbo") or {}).get("pbo")),
                    "judge_reason": (((judgment or {}).get("kimi") or {}).get("reason")),
                })
                _learn_close(
                    hyp,
                    {
                        "contract_hash": candidate.get("prediction_contract_hash"),
                        "hypothesis_id": hyp.get("hypothesis_id"),
                        "generator": hyp.get("source"),
                        "expected_direction": (contract.get("target") or {}).get("direction"),
                        "confidence": 0.5,
                    },
                    rejection_stage,
                    probe=candidate.get("probe"),
                    multiverse=candidate.get("multiverse"),
                    antifalsify=candidate.get("antifalsify"),
                )

            candidate_results.append({
                "hypothesis_id": hyp.get("hypothesis_id"),
                "rank_before_testing": rank_index + 1,
                "passed": bool(mt_row.get("passed") and judgment and judgment.get("admit_to_assembly")),
                "dsr": mt_row.get("dsr"),
                "pbo": mt_row.get("pbo"),
                "path_stability": mt_row.get("path_stability"),
                "judge": candidate.get("judge"),
            })
            ledger.append_event({
                "event_type": "multiple_testing_candidate",
                "hypothesis_id": hyp.get("hypothesis_id"),
                "rank_before_testing": rank_index + 1,
                "passed": mt_row.get("passed"),
                "dsr": ((mt_row.get("dsr") or {}).get("dsr")),
                "pbo": ((mt_row.get("pbo") or {}).get("pbo")),
                "effective_trials": budget.get("effective_trials"),
            }, run_id=run_id)
        survivors = admitted
        mt_pack = {
            "ok": True,
            "passed": bool(survivors),
            "method": "per_candidate_dsr_shared_clock_pbo_v2",
            "n_candidates_in": len(candidate_results),
            "n_candidates_passed": len(survivors),
            "effective_trials": budget.get("effective_trials"),
            "effective_trial_estimate": effective_estimate,
            "candidates": candidate_results,
            "rejected": rejected,
            "thresholds_unchanged": {"dsr_min": 0.95, "pbo_max": 0.40},
        }
        stages["multiple_testing"] = mt_pack
    else:
        stages["multiple_testing"] = {
            "ok": True, "passed": False, "reason": "no_admission_survivors",
            "note_zh": "当前没有候选通过三轴准入；这不等于整个机制方向死亡。",
        }
    # Include post-probe/statistical/committee failures added after the initial
    # family-closure snapshot.  Without this refresh failure blueprints hid the
    # actual gate that eliminated otherwise READY probes.
    stages["failure_lineage"] = failure_lineage[:120]

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
                "recipe_id": top.get("recipe_id"),
                "recipe": top.get("recipe"),
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
            "recipe_id": top.get("recipe_id"),
            "recipe": top.get("recipe"),
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
        "failure_lineage": (stages.get("failure_lineage") or [])[:40],
        "microstructure": stages.get("microstructure"),
        "near_miss_diagnostics_n": len(near_miss_diagnostics),
        "probe_trials_used": global_trials_used,
        "probe_trial_limit": global_trial_limit,
        "stop_reason": (stages.get("trial_budget") or {}).get("stop_reason"),
    }, run_id=run_id)

    return {
        "ok": ok,
        "schema": "qiyu_research_discovery_v4",
        "run_id": run_id,
        "experiment_id": ledger.experiment_id(run_id),
        "git_hash": ledger.git_hash_short(),
        "stages": stages,
        "survivors": [
            {
                k: v for k, v in s.items()
                if k not in ("probe_returns", "pbo_returns")
            }
            for s in survivors
        ],
        # Private-to-creation handoff: exact admitted recipes plus the net
        # return series needed by post-discovery risk/stress gates.  The
        # blueprint persists a bounded envelope and never writes these series.
        "assembly_payload": [
            {
                "hypothesis": s.get("hypothesis") or {},
                "recipe_id": s.get("recipe_id"),
                "recipe": s.get("recipe") or {},
                "probe": s.get("probe") or {},
                "probe_returns": s.get("probe_returns") or [],
                "multiple_testing_gate": s.get("multiple_testing_gate") or {},
                "judge": s.get("judge") or {},
                "feasibility": s.get("feasibility") or {},
                "execution": s.get("execution") or {},
                "antifalsify": s.get("antifalsify") or {},
            }
            for s in survivors[:8]
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
        "provider": "research_discovery_v4",
        "modules": [
            "research_ledger", "research_blackboard", "mechanism_graph",
            "phenomenon_scanner", "symbolic_searcher", "heterogeneous_committee",
            "probe_protocol", "antifalsify", "map_elites_archive",
            "multiple_testing", "edge_friction", "creation_multiverse",
            "parameter_platform", "prediction_contract", "outcome_attribution",
            "generator_scorecard", "mechanism_beliefs", "budget_allocator",
            "shadow_feedback", "learning_loop", "research_branch_manager", "microstructure_bridge", "data_evidence_ladder", "cheap_probe_stream", "research_campaign", "research_contract_v2",
        ],
        "roles": list(committee.ROLE_CONTRACTS.keys()),
        "learning_mvp": learn.probe().get("mvp"),
        "at": _now(),
    }
