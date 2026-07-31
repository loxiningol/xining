# -*- coding: utf-8 -*-
"""Bridge a qualified sole-creation blueprint into the existing four reviews.

This module does not change review standards and never deploys to live trading.
It only removes the old requirement to rerun discovery before review.
"""
from __future__ import print_function

import time

from .process_safe_state import process_lock


def _build_focus(blueprint, job):
    stages = (blueprint or {}).get("stages") or {}
    selected = stages.get("selected") or {}
    return {
        "symbol": job.get("symbol"),
        "timeframe": job.get("timeframe"),
        "direction": job.get("trade_direction"),
        "human_brief": job.get("brief"),
        "order_zh": job.get("brief") or job.get("research_direction"),
        "professional_research": (blueprint or {}).get("glm_research_brief"),
        "creation_blueprint": {
            "ok": (blueprint or {}).get("ok"),
            "run_id": (blueprint or {}).get("run_id"),
            "source": job.get("source"),
            "research_direction": job.get("research_direction"),
            "stages_summary": {
                "meta_family": ((stages.get("meta") or {}).get("design_doc") or {}).get("mechanism_family"),
                "hypothesis_passed": (stages.get("hypothesis") or {}).get("passed"),
                "selected": selected,
                "stress_passed": (stages.get("stress") or {}).get("passed"),
            },
            "fuses": (blueprint or {}).get("fuses"),
            "deliverables": (blueprint or {}).get("deliverables"),
        },
        "quantoracle": {
            "probe": ((blueprint or {}).get("probes") or {}).get("quantoracle"),
            "certified_factors": stages.get("certified_factors"),
        },
    }


def submit_blueprint_to_formal_review(blueprint, job):
    """Run the existing mechanism-spec, implementation and four-review entry."""
    if not (blueprint or {}).get("ok") or not (blueprint or {}).get("present_to_human"):
        return {
            "ok": False,
            "started": False,
            "reason": "creation_candidate_not_qualified",
            "message_zh": "创造门槛未通过，禁止进入正式复核。",
        }
    if not (blueprint or {}).get("glm_research_brief"):
        return {
            "ok": False,
            "started": False,
            "reason": "qualified_blueprint_missing_research_brief",
            "message_zh": "合格蓝图缺少可追溯研究摘要，禁止进入正式复核。",
        }

    # Review is deliberately serialized; creation itself remains two-lane.
    with process_lock("formal_review_submission"):
        from .pipeline_step_a import (
            codex_implement_from_spec,
            glm_require_mechanism_spec,
            run_creation_pipeline_step_a,
        )
        focus = _build_focus(blueprint, job)
        mode_ctx = {
            "schema": "qiyu_parallel_creation_to_review_v1",
            "isolation": "new_mechanism",
            "mode_name": "new_mechanism",
            "source_job_id": job.get("job_id"),
        }
        try:
            from . import failure_kb
            kb_ctx = failure_kb.compact_context_for_glm() if hasattr(
                failure_kb, "compact_context_for_glm"
            ) else {}
        except Exception:
            kb_ctx = {}
        kb_ctx = dict(kb_ctx or {})
        kb_ctx["creation_blueprint_mandatory"] = blueprint.get("glm_research_brief")
        spec_pack = glm_require_mechanism_spec(
            mode_ctx=mode_ctx,
            focus=focus,
            mode_name="new_mechanism",
            kb_ctx=kb_ctx,
            retries=2,
        )
        if not (spec_pack or {}).get("ok"):
            return {
                "ok": False,
                "started": True,
                "reason": "mechanism_spec_failed",
                "errors": (spec_pack or {}).get("errors"),
                "ai_error": (spec_pack or {}).get("ai_error"),
            }

        spec = spec_pack.get("mechanism_spec") or {}
        meta = dict(spec_pack.get("meta") or {})
        meta.setdefault("symbol", job.get("symbol"))
        meta.setdefault("timeframe", job.get("timeframe"))
        meta.setdefault("direction", job.get("trade_direction"))
        meta["source"] = "parallel_sole_creation"
        meta["source_job_id"] = job.get("job_id")
        meta["source_agent"] = job.get("source")
        meta["research_direction"] = job.get("research_direction")
        meta["human_brief"] = job.get("brief")
        pack = {
            "ok": True,
            "schema": "qiyu_step_a_v1",
            "artifact": "strategy_candidate_pack",
            "call_id": spec_pack.get("call_id"),
            "attempts": spec_pack.get("attempts"),
            "meta": meta,
            "mechanism_spec": spec,
            "creation_blueprint": {
                "ok": blueprint.get("ok"),
                "run_id": blueprint.get("run_id"),
                "loops": blueprint.get("loops"),
                "fuses": blueprint.get("fuses"),
                "deliverables": blueprint.get("deliverables"),
                "best_factor": blueprint.get("best_factor"),
            },
            "creation_research": {
                "schema": "qiyu_creation_research_via_parallel_blueprint_v1",
                "glm_research_brief": blueprint.get("glm_research_brief"),
                "research_run_id": blueprint.get("run_id"),
                "source_job_id": job.get("job_id"),
            },
            "errors": [],
        }
        impl = codex_implement_from_spec(pack)
        if not impl or not bool(impl.get("ok", True)) or not impl.get("dsl"):
            return {
                "ok": False,
                "started": True,
                "reason": "strategy_dsl_implementation_failed",
                "implementation": impl,
            }
        pack["dsl"] = impl.get("dsl")
        pack["dsl_long"] = impl.get("dsl_long") or (
            pack["dsl"] if str(meta.get("direction")).lower() == "long" else None
        )
        pack["dsl_short"] = impl.get("dsl_short") or (
            pack["dsl"] if str(meta.get("direction")).lower() == "short" else None
        )
        result = run_creation_pipeline_step_a(
            symbol=job.get("symbol"),
            timeframe=job.get("timeframe"),
            exploration_mode="A",
            allow_horizontal_expand=False,
            prebuilt_spec_pack=pack,
            windtalker_tag="parallel_creation_%s_%s" % (
                job.get("job_id"), int(time.time()),
            ),
        )
        return {
            "ok": bool((result or {}).get("ok")),
            "started": True,
            "reason": (result or {}).get("reason"),
            "task_id": (result or {}).get("task_id"),
            "human_confirm_state": (result or {}).get("human_confirm_state"),
            "admission": (result or {}).get("admission_v2"),
            "live_execution_changed": False,
        }
