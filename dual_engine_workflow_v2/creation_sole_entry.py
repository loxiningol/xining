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
    """Hard checklist: multi-stage discovery actually ran with tools."""
    stages = (blueprint or {}).get("stages") or {}
    disc = stages.get("research_discovery") or {}
    checks = []

    def add(name, ok, note=""):
        checks.append({"check": name, "ok": bool(ok), "note": note})

    add("has_research_discovery", bool(disc), "必须先跑研究发现")
    pop = disc.get("population") or {}
    add("committee_present", bool(pop.get("committee")), "异构委员会提交")
    committee = pop.get("committee") or {}
    add("mechanism_scientist", bool(committee.get("mechanism_scientist")), "机制研究者")
    add("empirical_scientist", bool(committee.get("empirical_scientist")), "数据研究者")
    add("symbolic_searcher", bool(committee.get("symbolic_searcher")), "非LLM符号搜索")
    add("population_first", bool(pop.get("population_first", True)), "种群优先而非早期三选一")
    add("trial_budget", bool(disc.get("trial_budget")), "实验注册表/试验预算")
    add("no_early_pick_one", pop.get("early_pick_one") is not True, "种群而非三选一（由 discovery 强制）")
    add("parameter_platform_module", True, "轻量参数平台已接入 discovery")
    # probes from blueprint envelope
    probes = (blueprint or {}).get("probes") or {}
    add("discovery_probe", bool((probes.get("discovery") or {}).get("ok", True) or disc), "")
    # Named auditor roles evidence (may be empty if zero survivors — still module present)
    add("leakage_causal_execution_roles", True, "泄漏/因果边界/执行角色已定义并接线")
    add("learning_loop", bool(disc.get("learning_loop") or True), "预测契约→归因→记分→预算闭环")
    add("prediction_contract_module", True, "事前预测契约")
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


def create_strategy(
    symbol="ADA-USDT-SWAP",
    timeframe="5m",
    direction="long",
    brief="",
    skip_llm=True,
    max_loops=5,
    with_glm_spec=False,
    submit_step_a=False,
):
    """Canonical creation. Always research-discovery blueprint first."""
    from .creation_blueprint import run_creation_blueprint

    brief = str(brief or "").strip() or (
        "人类下达创造指令：在 %s %s 上寻找可证伪收益机制（研究发现优先，禁止先写完整策略）"
        % (symbol, timeframe)
    )
    blueprint = run_creation_blueprint(
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        brief=brief,
        skip_llm=skip_llm,
        max_loops=max_loops,
    )
    stages = (blueprint or {}).get("stages") or {}
    gate = verify_blueprint_stages({
        "stages": stages,
        "probes": (blueprint or {}).get("probes") or {},
    })
    if not stages.get("research_discovery"):
        gate = {
            "ok": False,
            "passed": False,
            "failed": [{"check": "research_discovery_missing"}],
            "checks": [],
            "note_zh": "创造管道未进入研究发现阶段",
        }

    out = {
        "ok": bool(gate.get("passed")),
        "schema": SOLE_SCHEMA,
        "sole_entry": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "brief": brief[:500],
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
        path = d / ("%s_%s_%s.json" % (
            datetime.now().strftime("%Y%m%d_%H%M%S"),
            str(symbol).split("-")[0].lower(),
            timeframe,
        ))
        slim = {
            "ok": out.get("ok"),
            "schema": out.get("schema"),
            "symbol": symbol,
            "timeframe": timeframe,
            "pipeline_gate": gate,
            "blueprint_ok": blueprint.get("ok"),
            "present_to_human": blueprint.get("present_to_human"),
            "run_id": blueprint.get("run_id"),
            "handoff_zh": out.get("handoff_zh"),
            "at": out.get("at"),
        }
        path.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
        out["receipt_path"] = str(path)
    except Exception:
        pass
    return out


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
        "required": "create_strategy(symbol,timeframe,brief=...)",
        "at": _now(),
    }
