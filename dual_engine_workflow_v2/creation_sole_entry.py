# -*- coding: utf-8 -*-
"""SOLE strategy-creation entry — the only allowed path before review.

All human / Cursor / Codex / Web / timer creation MUST call:
  dual_engine_workflow_v2.creation_sole_entry.create_strategy(...)

Any other creation pipeline is refused or redirected here.
Review (STEP A / ADA5) remains separate and unchanged.
"""
from __future__ import print_function

import json
import os
from datetime import datetime
from pathlib import Path

from .process_safe_state import atomic_write_json, process_lock, unique_id


SOLE_SCHEMA = "qiyu_sole_creation_entry_v1"
BLOCKED_REASON = (
    "策略创造途径已统一为蓝图研究发现管道"
    "（creation_sole_entry → research_discovery → assembly）。"
    "禁止 ecosystem/legacy dual-engine/mass/factory 旁路创造。"
)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def is_sole_creation_enforced():
    """Default ON. Set QIYU_SOLE_CREATION=0 only for emergency rollback."""
    return str(os.environ.get("QIYU_SOLE_CREATION") or "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def refuse_side_path(path_name, detail=None):
    return {
        "ok": False,
        "schema": SOLE_SCHEMA,
        "blocked": True,
        "path": path_name,
        "error": "sole_creation_enforced",
        "message_zh": BLOCKED_REASON,
        "detail": detail,
        "required_entry": "dual_engine_workflow_v2.creation_sole_entry.create_strategy",
        "at": _now(),
    }


def verify_blueprint_stages(blueprint):
    """Hard checklist backed only by evidence present in this blueprint.

    A declared module is not proof that a stage ran.  Every check below is
    therefore fail-closed: missing fields never default to ``True``.
    """
    stages = (blueprint or {}).get("stages") or {}
    disc = stages.get("research_discovery") or {}
    probes = (blueprint or {}).get("probes") or {}
    discovery_probe = probes.get("discovery") or {}
    declared_modules = set(discovery_probe.get("modules") or [])
    declared_roles = set(discovery_probe.get("roles") or [])
    checks = []

    def add(name, ok, note=""):
        checks.append({"check": name, "ok": bool(ok), "note": note})

    add("has_research_discovery", isinstance(disc, dict) and bool(disc), "必须先跑研究发现")
    add(
        "research_discovery_completed",
        isinstance(disc.get("ok"), bool)
        and bool(disc.get("run_id"))
        and isinstance(disc.get("n_survivors"), int)
        and not isinstance(disc.get("n_survivors"), bool)
        and disc.get("n_survivors") >= 0,
        "必须有运行标识、明确结果和候选计数",
    )
    pop = disc.get("population") or {}
    add("committee_present", isinstance(pop.get("committee"), dict) and bool(pop.get("committee")), "异构委员会提交")
    committee = pop.get("committee") or {}
    add("mechanism_scientist", isinstance(committee.get("mechanism_scientist"), dict), "机制研究者")
    add("empirical_scientist", isinstance(committee.get("empirical_scientist"), dict), "数据研究者")
    add("symbolic_searcher", isinstance(committee.get("symbolic_searcher"), dict), "非LLM符号搜索")
    add("population_first", pop.get("population_first") is True, "种群优先而非早期三选一")
    add("trial_budget", isinstance(disc.get("trial_budget"), dict) and bool(disc.get("trial_budget")), "实验注册表/试验预算")
    add("no_early_pick_one", pop.get("early_pick_one") is False, "种群而非三选一（由 discovery 强制）")
    add("discovery_probe", discovery_probe.get("ok") is True, "研究发现模块必须自检成功")
    add(
        "parameter_platform_module",
        "parameter_platform" in declared_modules,
        "轻量参数平台必须由 discovery 探针声明",
    )
    add(
        "leakage_causal_execution_roles",
        {"leakage_auditor", "causal_auditor", "execution_engineer"}.issubset(declared_roles),
        "泄漏/因果边界/执行角色必须由 discovery 探针声明",
    )
    add(
        "learning_loop_module",
        "learning_loop" in declared_modules,
        "学习闭环必须由 discovery 探针声明",
    )
    add(
        "prediction_contract_module",
        "prediction_contract" in declared_modules,
        "事前预测契约必须由 discovery 探针声明",
    )
    candidate_claimed = bool(
        (blueprint or {}).get("ok") is True
        and (blueprint or {}).get("present_to_human") is True
    )
    if candidate_claimed:
        contract = (
            (blueprint or {}).get("research_contract")
            or stages.get("research_contract")
            or disc.get("contract")
            or {}
        )
        multiple_testing = disc.get("multiple_testing") or {}
        envelope = stages.get("admission_envelope") or {}
        lineage = stages.get("assembly_lineage") or {}
        selected_recipe_id = lineage.get("selected_recipe_id")
        add("candidate_contract_valid", contract.get("valid") is True, "候选必须绑定有效研究契约")
        add(
            "candidate_discovery_admitted",
            disc.get("ok") is True and int(disc.get("n_survivors") or 0) > 0,
            "候选必须来自 discovery 幸存者",
        )
        add(
            "candidate_multiple_testing_passed",
            multiple_testing.get("passed") is True,
            "至少一个候选必须通过逐候选DSR/PBO",
        )
        add(
            "candidate_recipe_lineage_locked",
            bool(
                selected_recipe_id
                and selected_recipe_id in (envelope.get("recipe_ids") or [])
                and lineage.get("selected_is_in_admission_envelope") is True
                and lineage.get("global_factor_mining_used") is False
                and lineage.get("classic_or_unadmitted_switch_used") is False
            ),
            "最终recipe必须属于准入信封且未发生后置换机制",
        )
    failed = [c for c in checks if not c["ok"]]
    return {
        "ok": len(failed) == 0,
        "passed": len(failed) == 0,
        "checks": checks,
        "failed": failed,
        "note_zh": (
            "蓝图多阶段硬门控通过" if not failed else
            ("蓝图阶段缺失: %s" % ",".join(c["check"] for c in failed))
        ),
    }


def _creation_outcome(blueprint, gate):
    """Classify the research result independently from process completion."""
    blueprint = blueprint if isinstance(blueprint, dict) else {}
    error = str(blueprint.get("error") or "").strip().lower()
    detail = blueprint.get("detail") or {}
    detail_error = str(detail.get("error") or "").strip().lower() if isinstance(detail, dict) else ""
    technical_failed = bool(
        blueprint.get("outcome") == "technical_failed"
        or error in ("creation_pipeline_exception", "technical_failed")
    )
    technical_completed = bool(blueprint) and not technical_failed
    data_markers = (
        "candle", "data_blocked", "data_missing", "no_data", "empty_data", "manifest_missing",
        "store_unavailable", "research_candles", "bad_candle_shape",
    )
    data_blocked = technical_completed and (
        blueprint.get("outcome") == "data_blocked"
        or any(marker in (error + " " + detail_error) for marker in data_markers)
    )
    candidate_ready = bool(
        technical_completed
        and blueprint.get("ok") is True
        and blueprint.get("present_to_human") is True
        and (gate or {}).get("passed") is True
    )
    if technical_failed:
        outcome = "technical_failed"
    elif data_blocked:
        outcome = "data_blocked"
    elif candidate_ready:
        outcome = "candidate_ready"
    elif technical_completed and (
        blueprint.get("ok") is False
        or blueprint.get("present_to_human") is False
        or bool(error)
        or (gate or {}).get("passed") is False
    ):
        outcome = "research_rejected"
    else:
        outcome = "technical_completed" if technical_completed else "technical_failed"
    return outcome, {
        "technical_completed": technical_completed,
        "technical_failed": outcome == "technical_failed",
        "research_rejected": outcome == "research_rejected",
        "data_blocked": outcome == "data_blocked",
        "candidate_ready": outcome == "candidate_ready",
        "review_submitted": False,
    }


def _create_strategy_unlocked(
    symbol="ADA-USDT-SWAP",
    timeframe="5m",
    direction="long",
    brief="",
    skip_llm=True,
    max_loops=5,
    with_glm_spec=False,
    submit_step_a=False,
    mission_id=None,
    source="direct",
    research_direction="",
    out_dir=None,
    research_contract=None,
    mutation_contract=None,
    data_version=None,
    code_version=None,
):
    """Canonical creation. Always research-discovery blueprint first."""
    from . import creation_blueprint as blueprint_module

    brief = str(brief or "").strip() or (
        "人类下达创造指令：在 %s %s 上寻找可证伪收益机制（研究发现优先，禁止先写完整策略）"
        % (symbol, timeframe)
    )
    mission_id = str(mission_id or unique_id("creation"))
    blueprint_kwargs = dict(
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        brief=brief,
        skip_llm=skip_llm,
        max_loops=max_loops,
        out_dir=out_dir,
        run_id=mission_id,
    )
    # Optional contracts are forwarded only when supplied so deployments can
    # roll this entry point out before the blueprint signature is upgraded.
    if research_contract is not None:
        blueprint_kwargs["research_contract"] = research_contract
    if mutation_contract is not None:
        blueprint_kwargs["mutation_contract"] = mutation_contract
    if data_version is not None:
        blueprint_kwargs["data_version"] = data_version
    if code_version is not None:
        blueprint_kwargs["code_version"] = code_version
    try:
        blueprint = blueprint_module.run_creation_blueprint(**blueprint_kwargs)
    except Exception as exc:
        # A model/schema/assembly exception is a technical failure, not a
        # research rejection.  Persist bounded evidence so a later round can
        # diagnose the exact failing component instead of leaving no artifact.
        effective_contract = research_contract
        try:
            contract_constraints = blueprint_module.rh.default_return_constraints()
            if research_contract:
                contract_constraints["research_contract"] = research_contract
            if mutation_contract:
                contract_constraints["mutation_contract"] = mutation_contract
            effective_contract = blueprint_module.discovery.compile_research_contract(
                brief, symbol, timeframe, contract_constraints,
                direction=direction, design_seed=None,
                data_version=data_version, code_version=code_version,
            )
        except Exception:
            pass
        blueprint = {
            "ok": False,
            "schema": "qiyu_creation_blueprint_v1",
            "present_to_human": False,
            "outcome": "technical_failed",
            "error": "creation_pipeline_exception",
            "detail": {
                "exception_type": type(exc).__name__,
                "message": str(exc)[:500],
                "stage_hint": "contract_data_meta_discovery_or_assembly",
            },
            "research_contract": effective_contract,
            "stages": ({"research_contract": effective_contract}
                       if isinstance(effective_contract, dict) else {}),
            "run_id": mission_id,
            "at": _now(),
        }
        blueprint["failure_artifact"] = blueprint_module._persist_failure_blueprint(
            out_dir, symbol, timeframe, mission_id, blueprint,
        )
    stages = (blueprint or {}).get("stages") or {}
    gate = verify_blueprint_stages(blueprint)
    if not stages.get("research_discovery"):
        gate = {
            "ok": False,
            "passed": False,
            "failed": [{"check": "research_discovery_missing"}],
            "checks": [],
            "note_zh": "创造管道未进入研究发现阶段",
        }

    outcome, outcome_status = _creation_outcome(blueprint, gate)
    effective_research_contract = (
        (blueprint or {}).get("research_contract")
        or (stages.get("research_contract") if isinstance(stages, dict) else None)
        or ((stages.get("research_discovery") or {}).get("contract") if isinstance(stages, dict) else None)
        or research_contract
    )
    out = {
        # Compatibility field, now intentionally means a reviewable candidate
        # exists.  Process completion is exposed separately below.
        "ok": outcome == "candidate_ready",
        "technical_completed": bool(outcome_status.get("technical_completed")),
        "outcome": outcome,
        "status_code": outcome,
        "outcome_status": outcome_status,
        "schema": SOLE_SCHEMA,
        "sole_entry": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "brief": brief[:500],
        "mission_id": mission_id,
        "source": str(source or "direct"),
        "research_direction": str(research_direction or brief)[:500],
        "research_contract": effective_research_contract,
        "mutation_contract": mutation_contract,
        "data_version": data_version,
        "code_version": code_version,
        "blueprint": blueprint,
        "pipeline_gate": gate,
        "present_to_human": bool(blueprint.get("present_to_human")),
        "handoff_zh": blueprint.get("handoff_zh") or gate.get("note_zh"),
        "at": _now(),
    }
    if with_glm_spec or submit_step_a:
        out["post_note_zh"] = (
            "GLM spec / STEP A 请使用 scripts/strategy_create_collab.py "
            "在 sole 管道 present_to_human=True 后继续；本入口只保证创造管道唯一。"
        )
        out["with_glm_spec_requested"] = bool(with_glm_spec)
        out["submit_step_a_requested"] = bool(submit_step_a)

    try:
        d = _root() / "auto_trade" / "dual_engine" / "sole_creation_runs"
        d.mkdir(parents=True, exist_ok=True)
        path = d / ("%s.json" % mission_id)
        slim = {
            "ok": out.get("ok"),
            "technical_completed": out.get("technical_completed"),
            "outcome": out.get("outcome"),
            "status_code": out.get("status_code"),
            "outcome_status": out.get("outcome_status"),
            "schema": out.get("schema"),
            "symbol": symbol,
            "timeframe": timeframe,
            "pipeline_gate": gate,
            "blueprint_ok": blueprint.get("ok"),
            "present_to_human": blueprint.get("present_to_human"),
            "run_id": blueprint.get("run_id"),
            "mission_id": mission_id,
            "source": str(source or "direct"),
            "research_direction": str(research_direction or brief)[:500],
            "research_contract": effective_research_contract,
            "mutation_contract": mutation_contract,
            "data_version": data_version,
            "code_version": code_version,
            "handoff_zh": out.get("handoff_zh"),
            "blueprint_error": blueprint.get("error"),
            "blueprint_detail": blueprint.get("detail"),
            "fuses": blueprint.get("fuses"),
            "at": out.get("at"),
        }
        atomic_write_json(path, slim)
        out["receipt_path"] = str(path)
    except Exception:
        pass
    return out


def create_strategy(
    symbol="ADA-USDT-SWAP",
    timeframe="5m",
    direction="long",
    brief="",
    skip_llm=True,
    max_loops=5,
    with_glm_spec=False,
    submit_step_a=False,
    mission_id=None,
    source="direct",
    research_direction="",
    out_dir=None,
    research_contract=None,
    mutation_contract=None,
    data_version=None,
    code_version=None,
):
    """Canonical executor with a global maximum of two simultaneous missions."""
    kwargs = {
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "brief": brief,
        "skip_llm": skip_llm,
        "max_loops": max_loops,
        "with_glm_spec": with_glm_spec,
        "submit_step_a": submit_step_a,
        "mission_id": mission_id,
        "source": source,
        "research_direction": research_direction,
        "out_dir": out_dir,
        "research_contract": research_contract,
        "mutation_contract": mutation_contract,
        "data_version": data_version,
        "code_version": code_version,
    }
    # Queue workers already hold one of these locks for their whole lifetime.
    if os.environ.get("QIYU_CREATION_SLOT_HELD") in ("0", "1"):
        return _create_strategy_unlocked(**kwargs)
    for slot in (0, 1):
        with process_lock("creation_capacity_%s" % slot, blocking=False) as lock_handle:
            if lock_handle is None:
                continue
            out = _create_strategy_unlocked(**kwargs)
            out["capacity_slot"] = slot
            return out
    return {
        "ok": False,
        "technical_completed": False,
        "outcome": "technical_failed",
        "status_code": "technical_failed",
        "outcome_status": {
            "technical_completed": False,
            "research_rejected": False,
            "data_blocked": False,
            "candidate_ready": False,
            "review_submitted": False,
        },
        "schema": SOLE_SCHEMA,
        "error": "parallel_creation_capacity_full",
        "message_zh": "两个策略研究槽均在运行；请通过唯一任务入口排队，禁止启动第三条旁路。",
        "required_entry": "dual_engine_workflow_v2.parallel_creation.submit_job",
        "at": _now(),
    }


def probe():
    return {
        "ok": True,
        "provider": "creation_sole_entry_v1",
        "enforced": is_sole_creation_enforced(),
        "blocked_paths": [
            "auto_trade_strategy_ecosystem.create_strategy_once",
            "auto_trade_dual_engine_factory.start_creation_task",
            "auto_trade_dual_engine_factory.run_creation_pipeline",
            "auto_trade_strategy_creation_factory.run_daily_collaborative_round",
            "auto_trade_mass_engine",
        ],
        "required": "parallel_creation.submit_job(source,research_direction,...)",
        "maximum_parallel_missions": 2,
        "parallel_isolation": [
            "mission_id", "research_ledger", "research_blackboard",
            "artifact_directory", "formal_review_handoff",
        ],
        "at": _now(),
    }
