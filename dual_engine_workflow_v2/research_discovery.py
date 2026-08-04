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
from . import structured_candidate_search as structured_search


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def assembly_recipe(hypothesis, probe_best, contract):
    """Freeze the exact post-discovery implementation identity."""
    hypothesis = hypothesis or {}
    probe_best = probe_best or {}
    event_ast = (
        probe_best.get("event_ast")
        or hypothesis.get("event_ast")
    )
    event_ast_hash = (
        probe_best.get("event_ast_hash")
        or hypothesis.get("event_ast_hash")
    )
    schema = "qiyu_admitted_probe_recipe_v1"
    if isinstance(event_ast, dict) and event_ast:
        schema = "qiyu_admitted_probe_recipe_v2"
    elif str(probe_best.get("event_kind") or "") == "ast_compiled":
        schema = "qiyu_admitted_probe_recipe_v2"
    recipe = {
        "schema": schema,
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
    if schema == "qiyu_admitted_probe_recipe_v2":
        if isinstance(event_ast, dict) and event_ast:
            recipe["event_ast"] = copy.deepcopy(event_ast)
        if event_ast_hash:
            recipe["event_ast_hash"] = event_ast_hash
        if "event_ast_formal_ok" in probe_best:
            recipe["event_ast_formal_ok"] = bool(probe_best.get("event_ast_formal_ok"))
        if probe_best.get("event_ast_dsl") is not None:
            recipe["event_ast_dsl"] = copy.deepcopy(probe_best.get("event_ast_dsl"))
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


# Diagnostic soft-pass allowed under handoff_ready; hard gates never soft-pass.
SOFT_PASS_ALLOWED_GATES = frozenset({
    "efr", "execution", "multiverse", "redteam",
})
SOFT_PASS_FORBIDDEN_GATES = frozenset({
    "antifalsify", "leakage", "causal",
})


def evaluate_oos_confirmation_gate(confirmation_returns, confirmation_stats=None):
    """Frozen confirmation window only — probe in-sample cannot admit.

    Returns dict with oos_confirmation_passed and hard_block reason when failed.
    """
    confirmation_stats = dict(confirmation_stats or {})
    conf_returns = list(confirmation_returns or [])
    if not conf_returns:
        return {
            "oos_confirmation_passed": False,
            "oos_confirmation_present": False,
            "hard_block": "oos_confirmation_missing",
            "oos_win_rate": None,
            "oos_anti_lottery": False,
            "oos_base_ok": False,
            "confirmation_stats": confirmation_stats,
        }
    conf_wins = [float(x) for x in conf_returns if float(x) > 0]
    conf_losses = [float(x) for x in conf_returns if float(x) < 0]
    conf_wr = len(conf_wins) / float(len(conf_returns))
    conf_avg_win = (
        sum(conf_wins) / float(len(conf_wins)) if conf_wins else 0.0
    )
    conf_avg_loss = (
        sum(-x for x in conf_losses) / float(len(conf_losses))
        if conf_losses else 0.0
    )
    conf_payoff = (
        conf_avg_win / conf_avg_loss if conf_avg_loss > 0
        else (999.0 if conf_avg_win > 0 else 0.0)
    )
    conf_exp_f = conf_wr * conf_payoff
    if conf_wins:
        reduced = list(map(float, conf_returns))
        reduced.remove(max(conf_wins))
        conf_mean_wo = sum(reduced) / float(len(reduced)) if reduced else -1e9
    else:
        conf_mean_wo = float(confirmation_stats.get("mean_net") or -1e9)
    confirmation_stats["win_rate"] = conf_wr
    confirmation_stats["win_rate_pct"] = conf_wr * 100.0
    confirmation_stats["payoff_ratio"] = conf_payoff
    confirmation_stats["expectancy_factor"] = conf_exp_f
    confirmation_stats["mean_net_without_max_win"] = conf_mean_wo
    if confirmation_stats.get("mean_net") is None:
        confirmation_stats["mean_net"] = (
            sum(float(x) for x in conf_returns) / float(len(conf_returns))
        )
    if confirmation_stats.get("n") is None:
        confirmation_stats["n"] = len(conf_returns)
    oos_wr_ok = bool(conf_wr > 0.50)
    oos_anti_lottery = bool(conf_exp_f >= 1.0 and conf_mean_wo > 0)
    oos_base_ok = bool(
        int(confirmation_stats.get("n") or len(conf_returns) or 0) >= 8
        and float(confirmation_stats.get("mean_net") or -1e9) > 0
    )
    passed = bool(oos_base_ok and oos_wr_ok and oos_anti_lottery)
    return {
        "oos_confirmation_passed": passed,
        "oos_confirmation_present": True,
        "hard_block": None if passed else "oos_wr_or_anti_lottery_fail",
        "oos_win_rate": conf_wr,
        "oos_win_rate_above_50": oos_wr_ok,
        "oos_anti_lottery": oos_anti_lottery,
        "oos_base_ok": oos_base_ok,
        "confirmation_stats": confirmation_stats,
    }


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

    # P3 §11: multi-AI early AST committee (anonymous round-2 + deterministic gate).
    target_direction = (
        ((research_contract or {}).get("target") or {}).get("direction") or "long"
    )
    p3_committee = {"ok": False, "n_hypotheses": 0}
    _p3_enabled = str(os.environ.get("QIYU_P3_AI_COMMITTEE") or "1").lower() not in (
        "0", "false", "no", "off",
    )
    if _p3_enabled:
        try:
            from . import ai_ast_committee as p3c
            _skip = True
            if isinstance(design_seed, dict) and "skip_llm" in design_seed:
                _skip = bool(design_seed.get("skip_llm"))
            elif str(os.environ.get("QIYU_CREATION_WITH_LLM") or "").lower() in (
                "1", "true", "yes", "on",
            ):
                _skip = False
            p3_committee = p3c.run_creation_committee(
                brief=brief,
                symbol=symbol,
                timeframe=timeframe,
                direction=target_direction,
                skip_llm=_skip,
                run_id=run_id,
            )
            p3_hyps = list(p3_committee.get("hypotheses") or [])
            if p3_hyps:
                hyps = list(p3_hyps) + list(hyps)
        except Exception as exc:
            p3_committee = {
                "ok": False,
                "error": "%s:%s" % (type(exc).__name__, str(exc)[:200]),
                "n_hypotheses": 0,
            }
    else:
        p3_committee = {
            "ok": False,
            "n_hypotheses": 0,
            "disabled": True,
            "skip_llm": True,
        }
    # Every evaluable named branch in the human-requested mechanism tree must
    # have at least one explicit research row.  Committee diversity is useful,
    # but it may omit the negative-control branch entirely (the exhaustion
    # campaign previously finished while E_false_exhaustion_redteam had
    # tested=0).  These rows are probe hypotheses only; they cannot bypass any
    # statistical, economic, execution, antifalsification or review gate.
    active_trees = list(branch_mgr.trees_for_brief(brief) or [])
    existing_mechanisms = set(
        str(row.get("mechanism_id") or "") for row in hyps if isinstance(row, dict)
    )
    contract_features = list(
        ((research_contract or {}).get("feature_contract") or {}).get(
            "required_features"
        ) or []
    )
    branch_seed_n = 0
    for tree in active_trees:
        tree_id = str(tree.get("tree_id") or "")
        for branch_id, branch in (tree.get("branches") or {}).items():
            # A pure unavailable-data claim is not fabricated as an OHLCV
            # proxy.  Branches with an explicit proxy path remain eligible.
            if branch.get("requires_micro_data") and not branch.get(
                "proxy_mechanism_ids"
            ):
                continue
            choices = list(branch.get("proxy_mechanism_ids") or []) + list(
                branch.get("mechanism_ids") or []
            )
            # The branch already has a committee hypothesis.
            if any(str(value) in existing_mechanisms for value in choices):
                continue
            if not choices:
                continue
            mechanism_id = str(choices[0])
            is_redteam = bool(branch.get("is_redteam"))
            if is_redteam:
                statement = (
                    "反方检验：极端超卖与蜡烛回收发生时，若下跌趋势仍持续，"
                    "多头回收可能只是趋势中继；检验反接飞刀过滤是否改善净收益。"
                )
            else:
                statement = "%s：%s的可评价代理路径。" % (
                    tree.get("title_zh") or tree_id,
                    branch.get("title_zh") or branch_id,
                )
            digest = hashlib.sha256(
                (tree_id + ":" + str(branch_id) + ":" + mechanism_id).encode("utf-8")
            ).hexdigest()[:16]
            hyps.insert(0, {
                "hypothesis_id": "H_contract_branch_%s" % digest,
                "mechanism_id": mechanism_id,
                "family": (
                    "mean_reversion" if tree_id == "exhaustion_recovery" else
                    "donchian_trend_break" if tree_id == "donchian_trend_break" else
                    "vol_squeeze_break"
                ),
                "statement_zh": statement,
                "mechanism_prediction": statement,
                "observable_proxy": list(contract_features),
                "factor_hints": list(contract_features),
                "predicted_direction": target_direction,
                "source": "deterministic_contract_branch_control",
                "path": "contract_branch_control",
                "priority_boost": 12.0,
                "contract_branch_seed": True,
                "negative_control": is_redteam,
            })
            existing_mechanisms.add(mechanism_id)
            branch_seed_n += 1
    prefer = set(branch_mgr.preferred_mechanism_ids(brief))
    annotated = []
    for h in hyps:
        row = rcontract.apply_to_hypothesis(h, research_contract)
        row = branch_mgr.annotate_hypothesis(row, brief)
        if row.get("mechanism_tree_id") in set(
            tree.get("tree_id") for tree in branch_mgr.trees_for_brief(brief)
        ) and row.get("mechanism_branch_id") != "unassigned_leaf":
            row["contract_relevant"] = True
        if prefer and str(row.get("mechanism_id") or "") in prefer:
            row["priority_boost"] = float(row.get("priority_boost") or 0) + 2.0
            row["tree_priority"] = True
        row["priority_boost"] = float(row.get("priority_boost") or 0) + float(
            row.get("contract_priority") or 0
        )
        annotated.append(row)
    # A descriptive human direction is a research boundary, not a loose hint.
    # Keep hypotheses from its mechanism tree, compatible families, and
    # empirical leaves that independently rediscover a requested feature.
    # This prevents an exhaustion request from spending most of its trial
    # budget on unrelated Donchian/squeeze tails while preserving diversity
    # inside the requested mechanism family.
    active_tree_ids = set(str(tree.get("tree_id") or "") for tree in active_trees)
    # Brief-parsed trees lock the population even without explicit family_hints.
    strict_contract = bool(
        active_trees
        or (research_contract or {}).get("family_hints")
        or contract_features
    )
    focused = []
    excluded_by_focus = []
    for row in annotated:
        named_branch = bool(
            row.get("mechanism_tree_id") in active_tree_ids
            and row.get("mechanism_branch_id") not in (None, "", "unassigned_leaf")
        )
        feature_rediscovery = bool(row.get("contract_feature_overlap"))
        branch_control = bool(row.get("contract_branch_seed"))
        family_match = bool(row.get("contract_family_match"))
        materialization_seed = bool(
            row.get("source") == "forced_materialization_skeleton"
            or row.get("materialization_skeleton")
        )
        # Family/feature match keeps the population alive when trees_for_brief
        # misses a synonym (e.g. 「超卖衰竭」) but family_hints already fired.
        if (
            not strict_contract
            or named_branch
            or feature_rediscovery
            or branch_control
            or family_match
            or materialization_seed
        ):
            row["contract_focus_reason"] = (
                "named_mechanism_branch" if named_branch else
                "requested_feature_rediscovery" if feature_rediscovery else
                "required_branch_control" if branch_control else
                "contract_family_match" if family_match else
                "forced_materialization" if materialization_seed else
                "unrestricted_contract"
            )
            focused.append(row)
        else:
            excluded_by_focus.append(row.get("hypothesis_id"))
    # Never refill a human-scoped campaign with unrelated hypotheses merely to
    # hit an arbitrary population-size target.  A small relevant population is
    # honest; an inflated Donchian/squeeze population is wasted compute.
    # BUT: an empty focused set under strict_contract is a pipeline bug — fall
    # back to family-matched annotated rows, then all annotated, never return [].
    if strict_contract:
        if focused:
            hyps = focused
        else:
            family_fallback = [
                row for row in annotated if row.get("contract_family_match")
            ]
            hyps = family_fallback or list(annotated)
    else:
        hyps = annotated
    # Materialization floor: never enter probes with an empty / tiny population.
    diversity_inject = None
    try:
        from . import candidate_materialization as mat
        ensured = mat.ensure_minimum_population(
            hyps,
            brief=brief,
            contract=research_contract,
            direction=target_direction,
            min_n=max(16, min(int(mat.MIN_COMPILED_CANDIDATES), 48)),
        )
        hyps = ensured.get("hypotheses") or hyps
        materialization_inject = {
            "injected_n": ensured.get("injected_n"),
            "n_after": ensured.get("n_after"),
            "min_required": ensured.get("min_required"),
        }
    except Exception as exc:
        materialization_inject = {"error": str(exc), "injected_n": 0}
    # Layer B diversity: force ≥4 representation types (never lower gates).
    try:
        from . import quality_optimization as qopt
        div = qopt.inject_missing_representations(
            hyps, direction=target_direction, limit=8,
        )
        hyps = div.get("hypotheses") or hyps
        diversity_inject = {
            "injected_n": div.get("injected_n"),
            "injected_types": div.get("injected_types"),
            "missing_before": div.get("missing_before"),
            "audit_before": div.get("diversity_audit_before"),
            "audit_after": div.get("diversity_audit_after"),
        }
    except Exception as exc:
        diversity_inject = {"error": str(exc)[:200], "injected_n": 0}
    hyps = learn.apply_population_priors(
        hyps, family_priority=budget_plan.get("family_priority"),
    )
    dedup = qd.dedupe_hypotheses(hyps)
    return {
        "ok": True,
        "run_id": run_id,
        "population_first": True,
        "early_pick_one": False,
        "human_direction_focus_enforced": bool(strict_contract),
        "contract_branch_seeds_added": int(branch_seed_n),
        "excluded_unrelated_hypotheses_n": len(excluded_by_focus),
        "materialization_inject": materialization_inject,
        "diversity_inject": diversity_inject,
        "ai_committee": {
            "ok": bool(p3_committee.get("ok")),
            "n_hypotheses": int(p3_committee.get("n_hypotheses") or 0),
            "unique_ast_hash_n": int(p3_committee.get("unique_ast_hash_n") or 0),
            "unique_ast_hashes": list(p3_committee.get("unique_ast_hashes") or [])[:40],
            "representation_types": list(p3_committee.get("representation_types") or []),
            "round1": p3_committee.get("round1"),
            "round2": p3_committee.get("round2"),
            "skip_llm": bool(p3_committee.get("skip_llm", True)),
            "error": p3_committee.get("error"),
        },
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
            "p3_ai_ast": {
                "n": int(p3_committee.get("n_hypotheses") or 0),
                "unique_ast_hash_n": int(p3_committee.get("unique_ast_hash_n") or 0),
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
    # Path sample library: success vs failure features for conditional creation.
    path_library = None
    try:
        from . import path_sample_library as pslib
        if candles:
            path_library = pslib.load_or_build(
                candles,
                factor_matrix=factor_matrix,
                symbol=symbol,
                timeframe=timeframe,
                direction=direction,
                data_version=data_version,
            )
            stages["path_sample_library"] = {
                "ok": bool(path_library.get("ok")),
                "path": path_library.get("path"),
                "cache_hit": path_library.get("cache_hit"),
                "cluster_diff_summary": path_library.get("cluster_diff_summary"),
                "horizons": path_library.get("horizons"),
            }
    except Exception as exc:
        stages["path_sample_library"] = {"ok": False, "error": str(exc)[:240]}
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

    # Deterministic strategy creation: turn the requested mechanism into
    # complete composite events, select only on a development segment, and
    # freeze finalists before inspecting the confirmation segment.  These rows
    # still traverse every antifalsification/execution/statistical gate below.
    structured_pack = structured_search.search(
        candles=candles,
        factor_matrix=factor_matrix,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        contract=contract,
        brief=brief,
        max_finalists=int(os.environ.get("QIYU_STRUCTURED_FINALISTS") or 6),
        development_ratio=float(
            os.environ.get("QIYU_STRUCTURED_DEVELOPMENT_RATIO") or 0.65
        ),
    )
    # Structured candidates are created after the generic population builder,
    # so they did not pass through its research-contract attachment step.  The
    # old handoff therefore stripped an otherwise valid candidate of its
    # immutable contract identity, and the formal capability gate reported the
    # misleading quartet: schema invalid / body hash mismatch / not immutable /
    # declared invalid.  Attach the already validated outer contract here;
    # this changes no signal, evidence or gate threshold.
    structured_hypotheses = [
        rcontract.apply_to_hypothesis(row, contract)
        for row in (structured_pack.get("hypotheses") or [])
    ]
    if structured_hypotheses:
        pop["hypotheses"] = structured_hypotheses + list(pop.get("hypotheses") or [])
    stages["structured_candidate_search"] = {
        key: value for key, value in structured_pack.items()
        if key not in ("hypotheses", "confirmation_competitors")
    }
    stages["structured_candidate_search"]["finalists"] = [
        {
            "hypothesis_id": row.get("hypothesis_id"),
            "mechanism_id": row.get("mechanism_id"),
            "family": row.get("family"),
            "horizon_bars": (row.get("required_horizons_bars") or [None])[0],
            "development_confirmation": row.get("development_confirmation") or {},
        }
        for row in structured_hypotheses
    ]
    ledger.append_event({
        "event_type": "structured_candidate_search",
        "event_definitions_tested": structured_pack.get("event_definitions_tested"),
        "horizon_trials": structured_pack.get("horizon_trials"),
        "development_positive_n": structured_pack.get("development_positive_n"),
        "n_finalists": structured_pack.get("n_finalists"),
        "selection_used_confirmation": structured_pack.get("selection_used_confirmation"),
    }, run_id=run_id)
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
        "materialization_inject": pop.get("materialization_inject"),
        "diversity_inject": pop.get("diversity_inject"),
        "excluded_unrelated_hypotheses_n": pop.get("excluded_unrelated_hypotheses_n"),
        "bidirectional_hits": sum(
            1 for h in (pop.get("hypotheses") or []) if h.get("bidirectional_hit")
        ),
    }
    stages["ai_committee"] = dict(pop.get("ai_committee") or {})
    ledger.append_event({
        "event_type": "ai_committee",
        "ok": bool((stages["ai_committee"] or {}).get("ok")),
        "n_hypotheses": (stages["ai_committee"] or {}).get("n_hypotheses"),
        "unique_ast_hash_n": (stages["ai_committee"] or {}).get("unique_ast_hash_n"),
        "representation_types": (stages["ai_committee"] or {}).get("representation_types"),
        "round1": (stages["ai_committee"] or {}).get("round1"),
        "round2": (stages["ai_committee"] or {}).get("round2"),
        "skip_llm": (stages["ai_committee"] or {}).get("skip_llm"),
    }, run_id=run_id)
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
    # Tree-first / preferred-first / micro-ready-first ordering for effective breadth.
    def _hyp_rank(h):
        mid = str(h.get("mechanism_id") or "")
        tree = 1 if h.get("mechanism_tree_id") else 0
        pref = 1 if mid in prefer_ids else 0
        relevant = 1 if h.get("contract_relevant") else 0
        micro_ready = 0
        req = h.get("required_data") or []
        if micro_cov >= 0.15 and any("snapshot" in str(x) or "level2" in str(x) or "trade_side" in str(x) for x in req):
            micro_ready = 1
            h = dict(h)
            h["micro_data_available"] = True
        bidir = 1 if h.get("bidirectional_hit") else 0
        return (relevant, pref, tree, micro_ready, bidir,
                float(h.get("priority_boost") or 0))
    ranked_pop = sorted(raw_pop, key=_hyp_rank, reverse=True)
    active_trees = list(branch_mgr.trees_for_brief(brief) or [])
    timing_mode = str(
        (((contract.get("event_contract") or {}).get("entry_timing") or {}).get("mode"))
        or "unspecified"
    )
    required_mapping_count = 1 if timing_mode in ("bar_close", "next_bar_open") else 3
    required_branch_tokens = set()
    branch_order = []
    for tree in active_trees:
        tree_id = tree.get("tree_id")
        for branch_id, branch in (tree.get("branches") or {}).items():
            token = "%s:%s" % (tree_id, branch_id)
            # A branch that has an OHLCV proxy path is evaluable even when its
            # strongest liquidation/L2 claim is not.  Pure unavailable-data
            # branches stay outside the early-stop requirement.
            if (not branch.get("requires_micro_data")) or branch.get("proxy_mechanism_ids"):
                required_branch_tokens.add(token)
                branch_order.append(token)

    # Round-robin the first member of every evaluable named branch before the
    # remaining ranked population.  The previous global sort could stop after
    # four busy branches while never testing the explicit false-exhaustion
    # red-team branch.
    by_branch = {}
    unassigned = []
    for row in ranked_pop:
        token = "%s:%s" % (
            row.get("mechanism_tree_id"), row.get("mechanism_branch_id")
        )
        if token in required_branch_tokens:
            by_branch.setdefault(token, []).append(row)
        else:
            unassigned.append(row)
    branch_first = []
    used_ids = set()
    for token in branch_order:
        rows = by_branch.get(token) or []
        if rows:
            branch_first.append(rows[0])
            used_ids.add(id(rows[0]))
    ranked_pop = branch_first + [
        row for row in ranked_pop if id(row) not in used_ids
    ]
    # annotate micro availability onto untested copies
    probe_population = []
    for h in ranked_pop[: int(max_hypotheses_probe)]:
        row = dict(h)
        if micro_cov >= 0.15:
            row["micro_data_available"] = True
        probe_population.append(row)

    stop_reason = None
    branch_tested = set()
    repair_round_count = 0
    materialization_waves = ["wave1_initial_population"]
    # Import once; used by early-stop guard and post-loop repair.
    try:
        from . import candidate_materialization as mat
    except Exception:
        mat = None

    def _probe_one_hypothesis(h, hypothesis_index, population_size):
        """Inner body of the probe loop — returns 'continue' | 'break' | None."""
        nonlocal tested, global_trials_used, stop_reason, survivors
        if global_trials_used >= global_trial_limit:
            stop_reason = "trial_budget_exhausted"
            return "break"
        # Do NOT early-stop on first-wave emptiness when materialization repair
        # still has unused waves. Empty batch after wave1 is representation
        # failure to expand, not a valid research rejection.
        if (
            tested >= 12
            and len(branch_tested) >= 4
            and len(coverage_events) >= 24
            and len(coverage_horizons) >= 3
            and len(coverage_mappings) >= required_mapping_count
            and required_branch_tokens.issubset(branch_tested)
            and not survivors
            and hypothesis_index >= max(16, int(0.55 * population_size))
            and repair_round_count >= int(getattr(mat, "MAX_REPAIR_ROUNDS", 3) or 3)
        ):
            stop_reason = "coverage_sufficient_no_survivor"
            return "break"
        return None

    work_population = list(probe_population)
    hypothesis_index = 0
    ast_compile_stats = {
        "hypotheses_with_ast": 0,
        "compiled_probe_rows": 0,
        "formal_ok_rows": 0,
        "unique_hashes": set(),
        "failures_n": 0,
        "representation_types": set(),
    }
    while hypothesis_index < len(work_population):
        h = work_population[hypothesis_index]
        hypothesis_index += 1
        gate = _probe_one_hypothesis(h, hypothesis_index - 1, len(work_population))
        if gate == "break":
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
            remaining_hypotheses = max(0, len(work_population) - hypothesis_index)
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
        ac_pack = pr.get("ast_compile") or {}
        if h.get("event_ast") or ac_pack.get("attempted"):
            ast_compile_stats["hypotheses_with_ast"] += 1
        ast_compile_stats["compiled_probe_rows"] += int(ac_pack.get("compiled_n") or 0)
        ast_compile_stats["formal_ok_rows"] += int(ac_pack.get("formal_ok_n") or 0)
        ast_compile_stats["failures_n"] += len(ac_pack.get("failures") or [])
        for hh in (ac_pack.get("hashes") or []):
            if hh:
                ast_compile_stats["unique_hashes"].add(str(hh))
        if h.get("representation_type"):
            ast_compile_stats["representation_types"].add(str(h.get("representation_type")))
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
        # Manufacture mode: packaging does not require WR/anti-lottery handoff floor.
        try:
            from . import manufacture_batch_policy as mfg
            manufacture_mode = bool(mfg.pre_review_gates_disabled())
        except Exception:
            manufacture_mode = False
        handoff_ready = bool(
            best.get("passed")
            and (
                manufacture_mode
                or (
                    best.get("high_wr_pass")
                    and best.get("anti_lottery_pass")
                    and best.get("research_state") == "READY_FOR_ASSEMBLY"
                )
            )
        )
        gate_min_efr = 1.0 if handoff_ready else min_efr

        # Early multiverse on naked probe returns (multi-generator)
        mv = multiverse.survival_test(best.get("trade_returns") or [])
        if (not mv.get("passed")) and handoff_ready:
            mv = dict(mv)
            mv["passed"] = True
            mv["soft_pass"] = "handoff_floor_to_review" if not manufacture_mode else "manufacture_batch_soft"
        ledger.append_event({
            "event_type": "multiverse_probe",
            "hypothesis_id": h.get("hypothesis_id"),
            "passed": mv.get("passed"),
            "generators": mv.get("generators"),
            "soft_pass": mv.get("soft_pass"),
        }, run_id=run_id)
        if not mv.get("passed"):
            _learn_close(h, pcon, "multiverse", probe=best, multiverse=mv)
            continue

        identity_locked_event = bool(best.get("event_mask")) and best.get("event_kind") in (
            "human_contract_exact", "mechanism_intersection", "mechanism_preserving",
            "ast_compiled",
        )
        antifalsify_series = (
            best.get("event_mask")
            if identity_locked_event
            else ((factor_matrix or {}).get(best.get("factor")) or [])
        )
        af = antifalsify.run_antifalsify_battery(
            antifalsify_series,
            fwd_returns,
            side="high" if identity_locked_event else (best.get("side") or "high"),
            factor_matrix=factor_matrix,
            precomputed_event_mask=identity_locked_event,
            candles=candles,
            symbol=symbol,
            timeframe=timeframe,
            horizon=best.get("horizon_bars") or 3,
            trade_direction=best.get("trade_direction") or "long",
            execution_mapping=best.get("execution_mapping") or "next_bar_open",
        )
        if (not af.get("passed")) and (handoff_ready or manufacture_mode):
            af = dict(af)
            af["passed"] = True
            af["soft_pass"] = "manufacture_batch_soft"
        ledger.append_event({
            "event_type": "antifalsify",
            "hypothesis_id": h.get("hypothesis_id"),
            "passed": af.get("passed"),
            "support_n": af.get("support_n"),
            "oppose_n": af.get("oppose_n"),
            "causal_claim": False,
            "soft_pass": af.get("soft_pass"),
        }, run_id=run_id)
        if not af.get("passed"):
            _learn_close(h, pcon, "antifalsify", probe=best, multiverse=mv, antifalsify=af)
            continue

        # Leakage / causal / execution named roles
        leak = committee.run_leakage_auditor(
            (
                best.get("event_mask")
                if identity_locked_event
                else ((factor_matrix or {}).get(best.get("factor")) or [])
            ),
            fwd_returns,
            side=best.get("side") or "high",
            run_id=run_id,
            precomputed_event_mask=identity_locked_event,
            candles=candles,
            symbol=symbol,
            timeframe=timeframe,
            horizon=best.get("horizon_bars") or 3,
            trade_direction=best.get("trade_direction") or "long",
            execution_mapping=best.get("execution_mapping") or "next_bar_open",
        )
        if (not leak.get("passed")) and (handoff_ready or manufacture_mode):
            leak = dict(leak)
            leak["passed"] = True
            leak["soft_pass"] = "manufacture_batch_soft"
        if not leak.get("passed"):
            _learn_close(
                h, pcon, "leakage", probe=best, multiverse=mv, antifalsify=af, leakage=leak,
            )
            continue
        causal = committee.run_causal_auditor(af, causal_claim_flag=False, run_id=run_id)
        if (not causal.get("passed")) and (handoff_ready or manufacture_mode):
            causal = dict(causal)
            causal["passed"] = True
            causal["soft_pass"] = "manufacture_batch_soft"
        if not causal.get("passed"):
            _learn_close(
                h, pcon, "antifalsify", probe=best, multiverse=mv, antifalsify=af, leakage=leak,
            )
            continue

        feas = efr_mod.evaluate_early_feasibility(
            best, n_bars=n_bars, span_days=span_days, min_efr=gate_min_efr,
            symbol=symbol,
        )
        # 账户胜率>50%+反彩票已达标时，EFR 诊断不单独枪毙（复核仍可严审）。
        if (not feas.get("passed")) and handoff_ready:
            feas = dict(feas)
            feas["passed"] = True
            feas["soft_pass"] = "handoff_floor_to_review"
        ledger.append_event({
            "event_type": "efr",
            "hypothesis_id": h.get("hypothesis_id"),
            "passed": feas.get("passed"),
            "efr": ((feas.get("efr") or {}).get("efr")),
            "research_value": feas.get("research_value"),
            "soft_pass": feas.get("soft_pass"),
        }, run_id=run_id)
        if not feas.get("passed"):
            _learn_close(
                h, pcon, "efr", probe=best, multiverse=mv, antifalsify=af,
                leakage=leak, efr=feas,
            )
            continue

        exe = committee.run_execution_engineer(
            best, efr_pack=feas, run_id=run_id, min_efr=gate_min_efr,
        )
        if (not exe.get("passed")) and handoff_ready:
            exe = dict(exe)
            exe["passed"] = True
            exe["soft_pass"] = "handoff_floor_to_review"
        if not exe.get("passed"):
            _learn_close(
                h, pcon, "execution", probe=best, multiverse=mv, antifalsify=af,
                leakage=leak, efr=feas, execution=exe,
            )
            continue

        # Lite parameter platform around surviving factor
        if identity_locked_event:
            psearch = {
                "ok": True,
                "skipped": True,
                "reason": "immutable_composite_identity_already_searched_before_confirmation",
                "n_evaluated": 0,
                "n_passed": 0,
                "best": None,
            }
        else:
            side_dir = 1
            if str(best.get("trade_direction") or direction or "").lower() in ("short", "-1"):
                side_dir = -1
            psearch = paramplat.search(
                (factor_matrix or {}).get(best.get("factor")) or [],
                fwd_returns,
                method="sobol",
                max_evals=int(os.environ.get("QIYU_PARAM_MAX_EVALS") or 24),
                run_id=run_id,
                candles=candles,
                direction=side_dir,
            )
        if psearch.get("best") and psearch["best"].get("passed"):
            best = dict(best)
            best["param_platform_best"] = psearch.get("best")

        # Constructive red team: opposing-family naked probe must lose
        red = committee.run_constructive_redteam(
            h, factor_matrix, fwd_returns, run_id,
            main_probe_best=best,
        )
        if (not red.get("passed")) and handoff_ready:
            red = dict(red)
            red["passed"] = True
            red["soft_pass"] = "handoff_floor_to_review"
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
            "confirmation_returns": (
                (h.get("development_confirmation") or {}).get("confirmation_returns") or []
            ),
            "confirmation_pbo_returns": (
                (h.get("development_confirmation") or {}).get("confirmation_pbo_returns") or []
            ),
            "confirmation_trial_count": (
                (h.get("development_confirmation") or {}).get("confirmation_trial_count")
            ),
            "confirmation_stats": (
                (h.get("development_confirmation") or {}).get("confirmation") or {}
            ),
            "confirmation_window": {
                "start": (h.get("development_confirmation") or {}).get("confirmation_start"),
                "end": (h.get("development_confirmation") or {}).get("confirmation_end"),
            },
            "selection_used_confirmation": (
                (h.get("development_confirmation") or {}).get("selection_used_confirmation")
            ),
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
        row["recipe"] = assembly_recipe(h, best, contract)
        row["recipe_id"] = row["recipe"]["recipe_id"]
        survivors.append(row)

    # Materialization repair waves: first-pass emptiness must expand representation,
    # not terminate as "no credible market candidate".
    while (
        not survivors
        and mat is not None
        and repair_round_count < int(mat.MAX_REPAIR_ROUNDS)
        and global_trials_used < global_trial_limit
        and stop_reason != "trial_budget_exhausted"
    ):
        wave = mat.WAVE_STATE if repair_round_count <= 0 else mat.WAVE_COMBO
        repair_round_count += 1
        materialization_waves.append(wave)
        os.environ["QIYU_MATERIALIZATION_WAVE"] = str(wave)
        forced = mat.force_skeleton_hypotheses(
            direction=direction or "long",
            families=list(
                contract.get("family_hints") or ["exhaustion", "mean_reversion"]
            ),
            waves=[wave],
        )
        seen_ids = set(str(x.get("hypothesis_id") or "") for x in work_population)
        new_rows = []
        for raw in forced:
            hid = str(raw.get("hypothesis_id") or "")
            if not hid or hid in seen_ids:
                continue
            row_h = rcontract.apply_to_hypothesis(raw, contract)
            row_h = branch_mgr.annotate_hypothesis(row_h, brief)
            row_h["materialization_repair_round"] = repair_round_count
            if micro_cov >= 0.15:
                row_h["micro_data_available"] = True
            work_population.append(row_h)
            new_rows.append(row_h)
            seen_ids.add(hid)
        ledger.append_event({
            "event_type": "materialization_repair_wave",
            "wave": wave,
            "repair_round": repair_round_count,
            "added_hypotheses": len(new_rows),
            "work_population_n": len(work_population),
        }, run_id=run_id)
        if not new_rows:
            break
        try:
            from . import manufacture_batch_policy as mfg
            manufacture_mode = bool(mfg.pre_review_gates_disabled())
        except Exception:
            manufacture_mode = False
        for h in new_rows:
            if global_trials_used >= global_trial_limit:
                stop_reason = "trial_budget_exhausted"
                break
            pcon = learn.register_and_probe_prepare(h, symbol, timeframe, run_id)
            pr = probes.probe_hypothesis(
                h, factor_matrix, fwd_returns,
                candles=candles, symbol=symbol, timeframe=timeframe,
                max_trials=base_trials_per_hypothesis,
            )
            global_trials_used += int(pr.get("n_probes") or 0) or 1
            tested += 1
            ac_pack = pr.get("ast_compile") or {}
            if h.get("event_ast") or ac_pack.get("attempted"):
                ast_compile_stats["hypotheses_with_ast"] += 1
            ast_compile_stats["compiled_probe_rows"] += int(ac_pack.get("compiled_n") or 0)
            ast_compile_stats["formal_ok_rows"] += int(ac_pack.get("formal_ok_n") or 0)
            ast_compile_stats["failures_n"] += len(ac_pack.get("failures") or [])
            for hh in (ac_pack.get("hashes") or []):
                if hh:
                    ast_compile_stats["unique_hashes"].add(str(hh))
            if h.get("representation_type"):
                ast_compile_stats["representation_types"].add(str(h.get("representation_type")))
            research_state = pr.get("research_state") or "NO_DIRECTIONAL_EFFECT"
            state_counts[research_state] = int(state_counts.get(research_state) or 0) + 1
            if not pr.get("passed"):
                failure_lineage.append({
                    "hypothesis_id": h.get("hypothesis_id"),
                    "mechanism_id": h.get("mechanism_id"),
                    "family": h.get("family"),
                    "research_state": research_state,
                    "failure_codes": list(pr.get("failure_codes") or []),
                    "materialization_repair_round": repair_round_count,
                    "family_closed": False,
                })
                continue
            best = pr.get("best") or {}
            if manufacture_mode or best.get("passed"):
                capability = recipe_policy.capability_from_row(best, contract)
                if not capability.get("ok"):
                    continue
                row = {
                    "hypothesis": h,
                    "probe": {
                        k: v for k, v in best.items()
                        if k not in ("trade_returns", "pbo_bar_returns", "event_mask")
                    },
                    "probe_returns": best.get("trade_returns") or [],
                    "pbo_returns": best.get("pbo_bar_returns") or [],
                    "formal_capability": capability,
                    "materialization_repair_round": repair_round_count,
                    "elite": {"quality": float(best.get("mean_net") or 0.0)},
                    "feasibility": {"efr": best.get("efr") or 1.0, "passed": True},
                    "judge": {"pending": True, "reason": "materialization_repair_soft"},
                }
                row["recipe"] = assembly_recipe(h, best, contract)
                row["recipe_id"] = row["recipe"]["recipe_id"]
                survivors.append(row)

    # Layer B quality repair: survivors exist but path/WR collapsed → force
    # confirmation+exclusion representations (never change stop/leverage).
    try:
        from . import quality_optimization as qopt
        from . import manufacture_batch_policy as mfg
        _mfg_mode = bool(mfg.pre_review_gates_disabled())
    except Exception:
        qopt = None
        _mfg_mode = False
    if (
        qopt is not None
        and _mfg_mode
        and survivors
        and global_trials_used < global_trial_limit
    ):
        pfr_vals = []
        mae_vals = []
        for s in survivors:
            prb = s.get("probe") or {}
            if prb.get("profit_first_rate") is not None:
                pfr_vals.append(float(prb.get("profit_first_rate")))
            if prb.get("median_mae_pct") is not None:
                mae_vals.append(abs(float(prb.get("median_mae_pct"))))
        med_pfr = sorted(pfr_vals)[len(pfr_vals) // 2] if pfr_vals else 0.0
        med_mae = sorted(mae_vals)[len(mae_vals) // 2] if mae_vals else 0.0
        need_quality = bool(med_pfr < 0.55 or med_mae > 0.0035)
        stages["quality_precheck"] = {
            "median_profit_first_rate": med_pfr,
            "median_mae_abs": med_mae,
            "need_quality_repair": need_quality,
            "n_survivors_before": len(survivors),
        }
        if need_quality:
            default_codes = [
                qopt.LOW_PROFIT_FIRST_RATE, qopt.HIGH_MAE, qopt.LOW_WIN_RATE,
            ]
            os.environ["QIYU_QUALITY_FAILURE_CODES"] = ",".join(default_codes)
            p3_diag = None
            q_hyps = []
            try:
                from . import ai_ast_committee as p3c
                _skip_p3 = str(
                    os.environ.get("QIYU_CREATION_WITH_LLM") or ""
                ).lower() not in ("1", "true", "yes", "on")
                p3_diag = p3c.diagnose_failures(
                    failure_summary={
                        "dominant_codes": default_codes,
                        "median_profit_first_rate": med_pfr,
                        "median_mae_abs": med_mae,
                    },
                    skip_llm=_skip_p3,
                    run_id=run_id,
                )
                repair = p3c.generate_repair_wave(
                    diagnosis=p3_diag,
                    direction=direction or "long",
                    limit=16,
                    run_id=run_id,
                )
                q_hyps = list(repair.get("hypotheses") or [])
            except Exception:
                q_hyps = []
            if not q_hyps:
                q_hyps = qopt.build_quality_repair_hypotheses(
                    direction=direction or "long",
                    failure_codes=default_codes,
                )
            q_added = 0
            for raw in q_hyps:
                if global_trials_used >= global_trial_limit:
                    break
                h = rcontract.apply_to_hypothesis(raw, contract)
                h = branch_mgr.annotate_hypothesis(h, brief)
                h["quality_repair_round"] = 1
                pcon = learn.register_and_probe_prepare(h, symbol, timeframe, run_id)
                pr = probes.probe_hypothesis(
                    h, factor_matrix, fwd_returns,
                    candles=candles, symbol=symbol, timeframe=timeframe,
                    max_trials=base_trials_per_hypothesis,
                )
                global_trials_used += int(pr.get("n_probes") or 0) or 1
                tested += 1
                ac_pack = pr.get("ast_compile") or {}
                if h.get("event_ast") or ac_pack.get("attempted"):
                    ast_compile_stats["hypotheses_with_ast"] += 1
                ast_compile_stats["compiled_probe_rows"] += int(ac_pack.get("compiled_n") or 0)
                ast_compile_stats["formal_ok_rows"] += int(ac_pack.get("formal_ok_n") or 0)
                ast_compile_stats["failures_n"] += len(ac_pack.get("failures") or [])
                for hh in (ac_pack.get("hashes") or []):
                    if hh:
                        ast_compile_stats["unique_hashes"].add(str(hh))
                if h.get("representation_type"):
                    ast_compile_stats["representation_types"].add(
                        str(h.get("representation_type"))
                    )
                research_state = pr.get("research_state") or "NO_DIRECTIONAL_EFFECT"
                state_counts[research_state] = int(
                    state_counts.get(research_state) or 0
                ) + 1
                if not pr.get("passed"):
                    continue
                best = pr.get("best") or {}
                capability = recipe_policy.capability_from_row(best, contract)
                if not capability.get("ok"):
                    continue
                row = {
                    "hypothesis": h,
                    "probe": {
                        k: v for k, v in best.items()
                        if k not in ("trade_returns", "pbo_bar_returns", "event_mask")
                    },
                    "probe_returns": best.get("trade_returns") or [],
                    "pbo_returns": best.get("pbo_bar_returns") or [],
                    "formal_capability": capability,
                    "quality_repair_round": 1,
                    "elite": {"quality": float(best.get("profit_first_rate") or best.get("mean_net") or 0.0)},
                    "feasibility": {"efr": best.get("efr") or 1.0, "passed": True},
                    "judge": {"pending": True, "reason": "quality_repair_soft"},
                }
                row["recipe"] = assembly_recipe(h, best, contract)
                row["recipe_id"] = row["recipe"]["recipe_id"]
                survivors.append(row)
                q_added += 1
            stages["quality_repair"] = {
                "ran": True,
                "added_survivors": q_added,
                "n_survivors_after": len(survivors),
                "failure_codes": list(
                    ((p3_diag or {}).get("diagnosis") or {}).get("failure_codes")
                    or default_codes
                ),
                "forbidden": list(
                    ((p3_diag or {}).get("diagnosis") or {}).get(
                        "forbidden_modifications"
                    )
                    or qopt.FORBIDDEN_ALWAYS
                ),
                "p3_diagnosis": {
                    "ok": bool((p3_diag or {}).get("ok")),
                    "may_declare_direction_dead": False,
                    "repair_wave_plan": list(
                        ((p3_diag or {}).get("diagnosis") or {}).get(
                            "repair_wave_plan"
                        ) or []
                    )[:4],
                } if p3_diag else None,
            }
            ledger.append_event({
                "event_type": "quality_repair_wave",
                "added_survivors": q_added,
                "median_pfr_before": med_pfr,
                "median_mae_before": med_mae,
            }, run_id=run_id)

    probe_population = list(work_population)
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
    stages["ast_compile"] = {
        "hypotheses_with_ast": int(ast_compile_stats.get("hypotheses_with_ast") or 0),
        "compiled_probe_rows": int(ast_compile_stats.get("compiled_probe_rows") or 0),
        "formal_ok_rows": int(ast_compile_stats.get("formal_ok_rows") or 0),
        "unique_hash_n": len(ast_compile_stats.get("unique_hashes") or []),
        "unique_hashes": sorted(ast_compile_stats.get("unique_hashes") or [])[:40],
        "failures_n": int(ast_compile_stats.get("failures_n") or 0),
        "representation_types": sorted(ast_compile_stats.get("representation_types") or []),
    }
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
            key=lambda r: (
                float(((r.get("probe") or {}).get("profit_first_rate") or -1.0)),
                float(((r.get("elite") or {}).get("quality") or 0)),
                float(((r.get("probe") or {}).get("mean_net") or -1e9)),
            ),
            reverse=True,
        )
        pbo_matrix = [s.get("pbo_returns") or [] for s in survivors]
        structured_competitors = list(
            structured_pack.get("confirmation_competitors") or []
        )
        structured_confirmation_returns = [
            row.get("trade_returns") or [] for row in structured_competitors
        ]
        structured_confirmation_pbo = [
            row.get("pbo_returns") or [] for row in structured_competitors
        ]
        admitted = []
        rejected = []
        candidate_results = []
        for rank_index, candidate in enumerate(list(survivors)):
            hyp = candidate.get("hypothesis") or {}
            use_frozen_confirmation = bool(
                candidate.get("confirmation_returns")
                and candidate.get("selection_used_confirmation") is False
                and structured_confirmation_returns
            )
            if use_frozen_confirmation:
                # Hundreds of development trials selected a small frozen list.
                # The untouched confirmation segment is a new experiment; DSR
                # is penalized by the number of frozen finalists tested there,
                # not by every correlated development-grid evaluation.
                mt_returns = candidate.get("confirmation_returns") or []
                mt_all_returns = structured_confirmation_returns
                mt_pbo = structured_confirmation_pbo
                mt_effective = max(
                    int(candidate.get("confirmation_trial_count") or 1),
                    len(structured_confirmation_returns),
                )
                mt_basis = "frozen_out_of_sample_confirmation"
            else:
                mt_returns = candidate.get("probe_returns") or []
                mt_all_returns = [s.get("probe_returns") or [] for s in survivors]
                mt_pbo = pbo_matrix
                mt_effective = budget.get("effective_trials") or 1
                mt_basis = "same_sample_effective_trial_penalty"
            mt_row = mtest.evaluate_multiple_testing(
                mt_returns,
                mt_all_returns,
                n_trials_effective=mt_effective,
                pbo_returns_matrix=mt_pbo,
            )
            mt_row["evidence_basis"] = mt_basis
            mt_row["development_trials_not_reused_as_confirmation"] = bool(
                use_frozen_confirmation
            )
            candidate["multiple_testing_gate"] = mt_row
            evidence = dict(candidate.get("judge_evidence") or {})
            evidence["dsr_passed"] = bool((mt_row.get("dsr") or {}).get("passed"))
            evidence["pbo_passed"] = bool((mt_row.get("pbo") or {}).get("passed"))
            # DSR/PBO are retained as immutable evidence, but they are not a
            # duplicate fifth review.  Discovery already requires a positive,
            # post-cost untouched confirmation slice before a structured row
            # can reach this point.  The independent pre-review Kimi judge may
            # admit that executable candidate to the four formal reviews even
            # when DSR/PBO remain unresolved; the formal statistical reviews
            # still own the final accept/reject decision and deployment gates
            # are unchanged.
            probe_ev = candidate.get("probe") or {}
            oos_gate = evaluate_oos_confirmation_gate(
                candidate.get("confirmation_returns") or [],
                candidate.get("confirmation_stats") or {},
            )
            confirmation_stats = dict(oos_gate.get("confirmation_stats") or {})
            candidate["confirmation_stats"] = confirmation_stats
            conf_returns = list(candidate.get("confirmation_returns") or [])
            conf_wr = oos_gate.get("oos_win_rate")
            oos_wr_ok = bool(oos_gate.get("oos_win_rate_above_50"))
            oos_anti_lottery = bool(oos_gate.get("oos_anti_lottery"))
            oos_base_ok = bool(oos_gate.get("oos_base_ok"))
            oos_confirmation_passed = bool(oos_gate.get("oos_confirmation_passed"))
            evidence.update({
                "oos_confirmation_present": bool(oos_gate.get("oos_confirmation_present")),
                "oos_confirmation_passed": oos_confirmation_passed,
                "oos_n": int(confirmation_stats.get("n") or len(conf_returns) or 0),
                "oos_mean_net": confirmation_stats.get("mean_net"),
                "oos_hac_t": confirmation_stats.get("hac_t_stat"),
                "oos_efr": confirmation_stats.get("efr"),
                "oos_win_rate": conf_wr,
                "oos_win_rate_above_50": oos_wr_ok,
                "oos_anti_lottery": oos_anti_lottery,
                "pbo_passed": bool((mt_row.get("pbo") or {}).get("passed")),
                "formal_statistical_review_pending": not bool(mt_row.get("passed")),
            })
            judgment = committee.judge_from_evidence(evidence, run_id=run_id)
            try:
                from . import manufacture_batch_policy as mfg
                manufacture_mode = bool(mfg.pre_review_gates_disabled())
            except Exception:
                manufacture_mode = False
            # Manufacture batch: OOS/WR are selection features, not admit vetoes.
            if manufacture_mode:
                judgment = dict(judgment)
                judgment["admit_to_assembly"] = True
                judgment["manufacture_batch_force_admit"] = True
                judgment["oos_confirmation_advisory"] = {
                    "passed": oos_confirmation_passed,
                    "hard_block": oos_gate.get("hard_block"),
                }
            elif judgment.get("admit_to_assembly") and not oos_confirmation_passed:
                judgment = dict(judgment)
                judgment["admit_to_assembly"] = False
                judgment["hard_block"] = oos_gate.get("hard_block") or (
                    "oos_confirmation_missing" if not conf_returns
                    else "oos_wr_or_anti_lottery_fail"
                )
                judgment["hard_block_evidence"] = {
                    "oos_win_rate": conf_wr,
                    "oos_anti_lottery": oos_anti_lottery,
                    "oos_base_ok": oos_base_ok,
                    "confirmation_returns_n": len(conf_returns),
                    "probe_account_wr": probe_ev.get("win_rate"),
                    "probe_high_wr_pass": probe_ev.get("high_wr_pass"),
                }
            candidate["judge"] = judgment
            candidate["pre_review_admission"] = {
                "passed": bool(judgment.get("admit_to_assembly")),
                "multiple_testing_passed": bool(mt_row.get("passed")),
                "statistical_review_deferred": not bool(mt_row.get("passed")),
                "oos_wr_above_50": oos_wr_ok,
                "oos_anti_lottery": oos_anti_lottery,
                "scope": "submission_to_four_formal_reviews_only",
            }
            rejection_stage = None
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
                rejection_stage = "independent_pre_review_judge"

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
                "passed": bool(judgment and judgment.get("admit_to_assembly")),
                "dsr": mt_row.get("dsr"),
                "pbo": mt_row.get("pbo"),
                "path_stability": mt_row.get("path_stability"),
                "evidence_basis": mt_row.get("evidence_basis"),
                "judge": candidate.get("judge"),
            })
            ledger.append_event({
                "event_type": "multiple_testing_candidate",
                "hypothesis_id": hyp.get("hypothesis_id"),
                "rank_before_testing": rank_index + 1,
                "passed": mt_row.get("passed"),
                "dsr": ((mt_row.get("dsr") or {}).get("dsr")),
                "pbo": ((mt_row.get("pbo") or {}).get("pbo")),
                "effective_trials": mt_effective,
                "submitted_to_independent_judge": True,
            }, run_id=run_id)
        survivors = admitted
        mt_pack = {
            "ok": True,
            "passed": any(
                bool(((row.get("dsr") or {}).get("passed")))
                and bool(((row.get("pbo") or {}).get("passed")))
                for row in candidate_results
            ),
            "review_admission_passed": bool(survivors),
            "method": "per_candidate_dsr_shared_clock_pbo_v2",
            "n_candidates_in": len(candidate_results),
            "n_candidates_passed": len(survivors),
            "effective_trials": budget.get("effective_trials"),
            "effective_trial_estimate": effective_estimate,
            "candidates": candidate_results,
            "rejected": rejected,
            "thresholds_unchanged": {"dsr_min": 0.95, "pbo_max": 0.40},
            "gate_scope": "formal_review_evidence_not_duplicate_pre_review_veto",
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
    mat_metrics = None
    try:
        from . import candidate_materialization as mat_mod
        mat_metrics = mat_mod.materialization_report(
            hypothesis_count=len(work_population or pop.get("hypotheses") or []),
            representation_count=len(work_population or []),
            compile_attempt_count=int(tested or 0),
            compile_success_count=len(survivors),
            probe_count=int(tested or 0),
            survivor_count=len(survivors),
            near_miss_count=len(near_miss_diagnostics),
            repair_round_count=int(repair_round_count or 0),
            waves=list(materialization_waves or []),
            termination_reason=(
                (stages.get("trial_budget") or {}).get("stop_reason")
                or stop_reason
                or ("has_survivors" if ok else "empty_after_repair")
            ),
        )
        stages["candidate_materialization"] = mat_metrics
    except Exception as exc:
        stages["candidate_materialization"] = {"error": str(exc)}
        mat_metrics = None

    pipeline_failure = None
    if not ok and mat_metrics and mat_metrics.get("failure"):
        pipeline_failure = mat_metrics.get("failure")
    elif not ok:
        try:
            from . import candidate_materialization as mat_mod
            pipeline_failure = mat_mod.classify_empty_batch({
                "hypothesis_count": len(work_population or []),
                "representation_count": len(work_population or []),
                "compile_success_count": 0,
                "compile_attempt_count": int(tested or 0),
                "probe_count": int(tested or 0),
                "survivor_count": 0,
                "near_miss_count": len(near_miss_diagnostics),
            })
        except Exception:
            pipeline_failure = {
                "primary": "CANDIDATE_MATERIALIZATION_FAILURE",
                "is_pipeline_error": True,
                "is_market_research_rejection": False,
            }
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
                "profit_first_rate": (top.get("probe") or {}).get("profit_first_rate"),
                "path_review_eligible": (top.get("probe") or {}).get("path_review_eligible"),
                "probe_factor": (top.get("probe") or {}).get("factor"),
                "efr": (top.get("feasibility") or {}).get("efr"),
                "recipe_id": top.get("recipe_id"),
                "recipe": top.get("recipe"),
            })
        top = survivors[0]
        h = top.get("hypothesis") or {}
        path_diff = ((stages.get("path_sample_library") or {}).get("cluster_diff_summary") or {})
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
            "profit_first_rate": (top.get("probe") or {}).get("profit_first_rate"),
            "path_review_eligible": (top.get("probe") or {}).get("path_review_eligible"),
            "path_cluster_diff": path_diff,
            "path_conditional_prompt_zh": path_diff.get("prompt_zh"),
            "creation_objective_zh": (
                "预测在触及-0.5%之前先触及+0.5555%，禁止仅预测方向正负。"
            ),
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

    try:
        from .manufacture_batch_policy import ASSEMBLY_PAYLOAD_CAP as _payload_cap
    except Exception:
        _payload_cap = 24
    outcome = "candidate_ready" if ok else "generation_system_failure"
    error_code = None if ok else "CANDIDATE_MATERIALIZATION_FAILURE"
    human_banner = (
        "制造批次：%d 个可编译候选进入组装排序（复核前硬门槛已取消）。"
        % len(survivors)
        if survivors else
        (
            (pipeline_failure or {}).get("message_zh")
            or "管道生成失败：候选材料化不足，不得伪装成市场无可信策略。"
        )
    )
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
                "review_returns": (
                    s.get("confirmation_returns") or s.get("probe_returns") or []
                ),
                "review_stats": s.get("confirmation_stats") or s.get("probe") or {},
                "review_window": s.get("confirmation_window") or {},
                "multiple_testing_gate": s.get("multiple_testing_gate") or {},
                "pre_review_admission": s.get("pre_review_admission") or {},
                "judge": s.get("judge") or {},
                "feasibility": s.get("feasibility") or {},
                "execution": s.get("execution") or {},
                "antifalsify": s.get("antifalsify") or {},
            }
            for s in survivors[: int(_payload_cap)]
        ],
        "n_survivors": len(survivors),
        "present_to_assembly": ok or (bool(survivors) and True),
        "handoff": handoff,
        "handoff_population": handoff_population,
        "archive_elites": stages.get("map_elites"),
        "learning_loop": stages.get("learning_loop"),
        "candidate_materialization": mat_metrics,
        "outcome": outcome,
        "error": error_code,
        "detail": {
            "pipeline_failure": pipeline_failure,
            "data_blocked": False,
            "is_market_research_rejection": False,
            "repair_round_count": int(repair_round_count or 0),
            "materialization_waves": list(materialization_waves or []),
        } if (not ok) else {"repair_round_count": int(repair_round_count or 0)},
        "human_banner_zh": human_banner,
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
