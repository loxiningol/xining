#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Professional strategy creation collab (pre-review).

Flywheel (NO review code touched):
  human brief
    → ① MetaGPT/AutoGen-style meta-think
    → ② Alphalens/CausalImpact-style hypothesis validation
    → ③ EasyQuant + DeepSeek mine → QuantOracle certify
    → ④ Alphalens rescreen
    → ⑤ Backtrader extreme + AutoGen red-team
    → GLM mechanism_spec (anchored to blueprint)
    → optional Codex / STEP A (existing ADA5 review is separate)

Usage:
  python3 scripts/strategy_create_collab.py \\
    --symbol ETH-USDT-SWAP --timeframe 5m --direction long \\
    --brief "用户口述"

Does NOT mount. Never skips ADA5 four-review when --submit-step-a.
"""
from __future__ import print_function

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))


def main():
    ap = argparse.ArgumentParser(
        description="Creation blueprint ①–⑤ + GLM (pre-review)"
    )
    ap.add_argument("--symbol", default="ETH-USDT-SWAP")
    ap.add_argument("--timeframe", default="5m")
    ap.add_argument("--direction", default="long", choices=("long", "short", "both"))
    ap.add_argument("--brief", default="", help="Human order / mechanism preference")
    ap.add_argument("--mode", default="new_mechanism",
                    choices=("new_mechanism", "known_mechanism_deep_dig",
                             "combination_mechanism", "failure_reverse_research"))
    ap.add_argument("--horizon", type=int, default=3, help="Forward bars for factor labels")
    ap.add_argument("--skip-research", action="store_true",
                    help="Debug only: skip blueprint stages ①–⑤")
    ap.add_argument("--skip-llm-research", action="store_true", default=True,
                    help="Blueprint stages without GLM/DeepSeek LLM calls (default)")
    ap.add_argument("--with-llm-research", action="store_true",
                    help="Enable GLM meta-enrich + DeepSeek factor proposals in blueprint")
    ap.add_argument("--implement", action="store_true",
                    help="Also run codex_implement_from_spec after GLM spec")
    ap.add_argument("--submit-step-a", action="store_true",
                    help="Submit to STEP A (ADA5 admission) after GLM design")
    args = ap.parse_args()

    from dual_engine_workflow_v2.pipeline_step_a import (
        glm_require_mechanism_spec, codex_implement_from_spec,
    )

    focus = {
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "direction": args.direction,
        "human_brief": args.brief,
    }

    research = None
    blueprint = None
    if not args.skip_research:
        from dual_engine_workflow_v2.creation_sole_entry import create_strategy as _sole_create
        skip_llm = False if args.with_llm_research else True
        print("SOLE_ENTRY_START", args.symbol, args.timeframe, "skip_llm", skip_llm, flush=True)
        t_res = time.time()
        _sole = _sole_create(
            symbol=args.symbol,
            timeframe=args.timeframe,
            direction=args.direction,
            brief=args.brief,
            skip_llm=skip_llm,
        )
        blueprint = (_sole or {}).get("blueprint") or {}
        print(
            "SOLE_ENTRY_DONE", round(time.time() - t_res, 1),
            "ok", (_sole or {}).get("ok"),
            "gate", ((_sole or {}).get("pipeline_gate") or {}).get("passed"),
            "present", (blueprint or {}).get("present_to_human"),
            "loops", (blueprint or {}).get("loops"),
            "fuses", (blueprint or {}).get("fuses"),
            flush=True,
        )
        if not ((_sole or {}).get("pipeline_gate") or {}).get("passed"):
            print("BLOCKED_PIPELINE_GATE", (_sole or {}).get("handoff_zh"), flush=True)
            return 4
        # keep variable name blueprint for rest of collab flow
        _ = None  # sole path replaces direct run_creation_blueprint
        if False:
            blueprint = None
        prelim = (blueprint or {}).get("prelim") or {}
        if prelim:
            print("PRELIM", prelim.get("human_banner_zh") or "ok",
                  "wr", prelim.get("win_rate"),
                  "window", ((prelim.get("window") or {}).get("label_zh")),
                  flush=True)
        hardness = (blueprint or {}).get("return_hardness") or {}
        if hardness:
            print(
                "RETURN_HARDNESS",
                "passed", hardness.get("passed"),
                "weekly", ((hardness.get("metrics") or {}).get("weekly_return_proxy")),
                "ret_mdd", ((hardness.get("metrics") or {}).get("return_mdd")),
                "reasons", hardness.get("reject_reasons"),
                flush=True,
            )
        if not (blueprint or {}).get("present_to_human"):
            print("BLOCKED_NOT_PRESENTABLE_CREATION_GATE", flush=True)
            out = {
                "ok": False,
                "present_to_human": False,
                "stage": "creation_gate",
                "blueprint": {
                    "ok": False,
                    "present_to_human": False,
                    "prelim": prelim,
                    "return_hardness": hardness,
                    "classic_tried": (blueprint or {}).get("classic_tried"),
                    "perspectives_tried": (blueprint or {}).get("perspectives_tried"),
                    "fuses": (blueprint or {}).get("fuses"),
                    "deliverables": (blueprint or {}).get("deliverables"),
                    "handoff_zh": (blueprint or {}).get("handoff_zh"),
                },
                "errors": [
                    "win_rate_or_return_hardness_or_degeneration_failed"
                ],
            }
            out_dir = ROOT / "auto_trade" / "dual_engine" / "collab_packs"
            out_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            out_path = out_dir / (
                "REJECTED_collab_%s_%s_%s.json"
                % (args.symbol.split("-")[0].lower(), args.timeframe, stamp)
            )
            out_path.write_text(
                json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            print("WROTE", out_path)
            return 3

        sel = ((blueprint or {}).get("stages") or {}).get("selected") or {}
        print(
            "SELECTED", sel.get("factor"), sel.get("rule"),
            "qo", ((sel.get("quantoracle") or {}).get("source")),
            "stress", ((blueprint or {}).get("stages") or {}).get("stress", {}).get("passed"),
            flush=True,
        )
        # Compatibility alias for older collab consumers
        research = {
            "ok": (blueprint or {}).get("ok"),
            "schema": "qiyu_creation_research_stage_v1_via_blueprint",
            "glm_research_brief": (blueprint or {}).get("glm_research_brief"),
            "certified_factors": ((blueprint or {}).get("stages") or {}).get("certified_factors"),
            "factor_mine": ((blueprint or {}).get("stages") or {}).get("mine"),
            "quantoracle_probe": ((blueprint or {}).get("probes") or {}).get("quantoracle"),
            "easyquant_envelope": None,
            "blueprint": {
                "ok": (blueprint or {}).get("ok"),
                "loops": (blueprint or {}).get("loops"),
                "fuses": (blueprint or {}).get("fuses"),
                "deliverables": (blueprint or {}).get("deliverables"),
            },
        }
        focus["professional_research"] = (blueprint or {}).get("glm_research_brief")
        focus["creation_blueprint"] = {
            "ok": (blueprint or {}).get("ok"),
            "stages_summary": {
                "meta_family": (
                    ((blueprint or {}).get("stages") or {}).get("meta", {})
                    .get("design_doc", {}) or {}
                ).get("mechanism_family"),
                "hypothesis_passed": (
                    ((blueprint or {}).get("stages") or {}).get("hypothesis") or {}
                ).get("passed"),
                "selected": sel,
                "stress_passed": (
                    ((blueprint or {}).get("stages") or {}).get("stress") or {}
                ).get("passed"),
            },
            "fuses": (blueprint or {}).get("fuses"),
            "deliverables": (blueprint or {}).get("deliverables"),
        }
        focus["quantoracle"] = {
            "probe": ((blueprint or {}).get("probes") or {}).get("quantoracle"),
            "certified_factors": ((blueprint or {}).get("stages") or {}).get("certified_factors"),
        }
    else:
        from dual_engine_workflow_v2.easyquant_bridge import modeling_envelope
        focus["easyquant_modeling"] = modeling_envelope(brief=args.brief, focus=focus)

    mode_ctx = {"schema": "qiyu_create_collab_v3_blueprint", "isolation": args.mode, "mode_name": args.mode}
    try:
        from dual_engine_workflow_v2 import failure_kb
        kb_ctx = failure_kb.compact_context_for_glm() if hasattr(failure_kb, "compact_context_for_glm") else {}
        if not kb_ctx and hasattr(failure_kb, "load_kb"):
            kb_ctx = {"note": "kb_present_compact_unavailable"}
    except Exception:
        kb_ctx = {}

    if blueprint and blueprint.get("glm_research_brief"):
        kb_ctx = dict(kb_ctx or {})
        kb_ctx["creation_blueprint_mandatory"] = blueprint["glm_research_brief"]
    elif research and research.get("glm_research_brief"):
        kb_ctx = dict(kb_ctx or {})
        kb_ctx["creation_research_mandatory"] = research["glm_research_brief"]

    focus["order_zh"] = args.brief or "（无额外口述：按蓝图已验证因子提出新机制）"

    print("GLM_SPEC_START", args.symbol, args.timeframe, args.direction, flush=True)
    t0 = time.time()
    spec_pack = glm_require_mechanism_spec(
        mode_ctx=mode_ctx, focus=focus, mode_name=args.mode, kb_ctx=kb_ctx, retries=2,
    )
    print("GLM_SPEC_ELAPSED", round(time.time() - t0, 1), "ok", (spec_pack or {}).get("ok"), flush=True)
    if not (spec_pack or {}).get("ok"):
        print("GLM_SPEC_FAIL", (spec_pack or {}).get("errors"), (spec_pack or {}).get("ai_error"))
        out = {
            "ok": False,
            "stage": "glm_mechanism_spec",
            "blueprint": blueprint,
            "research": research,
            "spec_pack": spec_pack,
        }
    else:
        spec = spec_pack.get("mechanism_spec") or {}
        meta = dict(spec_pack.get("meta") or {})
        meta.setdefault("symbol", args.symbol)
        meta.setdefault("timeframe", args.timeframe)
        meta.setdefault(
            "direction",
            args.direction if args.direction != "both" else meta.get("direction") or "long",
        )
        meta["source"] = "glm_live_collab_creation_blueprint_v1"
        meta["human_brief"] = args.brief
        meta["creation_stack"] = [
            "meta_think_metagpt_autogen",
            "hypothesis_alphalens_causal",
            "easyquant_deepseek_mine",
            "quantoracle_certify",
            "alphalens_rescreen",
            "backtrader_autogen_stress",
            "glm_mechanism_spec",
            "ada5_four_review_required_later",
        ]
        title = meta.get("title") or spec.get("mechanism_name") or "glm_collab"
        print("MECHANISM", spec.get("mechanism_family"), title)
        print("INEFFICIENCY", (spec.get("market_inefficiency") or "")[:160])
        print("COUNTERPARTY", (spec.get("counterparty_source") or "")[:160])

        pack = {
            "ok": True,
            "schema": "qiyu_step_a_v1",
            "artifact": "strategy_candidate_pack",
            "call_id": spec_pack.get("call_id"),
            "attempts": spec_pack.get("attempts"),
            "meta": meta,
            "mechanism_spec": spec,
            "creation_blueprint": {
                "ok": (blueprint or {}).get("ok") if blueprint else None,
                "loops": (blueprint or {}).get("loops") if blueprint else None,
                "fuses": (blueprint or {}).get("fuses") if blueprint else None,
                "deliverables": (blueprint or {}).get("deliverables") if blueprint else None,
                "best_factor": (blueprint or {}).get("best_factor") if blueprint else None,
            },
            "creation_research": research,
            "errors": [],
        }

        if args.implement or args.submit_step_a:
            impl = codex_implement_from_spec(pack)
            pack["dsl"] = (impl or {}).get("dsl")
            pack["dsl_long"] = (impl or {}).get("dsl_long") or (
                pack["dsl"] if str(meta.get("direction")).lower() == "long" else None
            )
            pack["dsl_short"] = (impl or {}).get("dsl_short") or (
                pack["dsl"] if str(meta.get("direction")).lower() == "short" else None
            )
            pack["implement"] = {
                "ok": bool((impl or {}).get("ok", True) if impl else False),
                "keys": list((impl or {}).keys())[:20],
            }
            print("IMPLEMENT", pack["implement"])

        if args.submit_step_a:
            from dual_engine_workflow_v2.pipeline_step_a import run_creation_pipeline_step_a
            result = run_creation_pipeline_step_a(
                symbol=args.symbol,
                timeframe=args.timeframe,
                exploration_mode="A",
                allow_horizontal_expand=False,
                prebuilt_spec_pack=pack,
                windtalker_tag="blueprint_collab_%s" % int(time.time()),
            )
            pack["step_a_result"] = {
                "ok": (result or {}).get("ok"),
                "reason": (result or {}).get("reason"),
                "task_id": (result or {}).get("task_id"),
                "human_confirm_state": (result or {}).get("human_confirm_state"),
                "admission_v2": (result or {}).get("admission_v2"),
            }
            print("STEP_A", pack["step_a_result"])

        out = pack

    out_dir = ROOT / "auto_trade" / "dual_engine" / "collab_packs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / (
        "collab_%s_%s_%s.json" % (args.symbol.split("-")[0].lower(), args.timeframe, stamp)
    )
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("WROTE", out_path)
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception as exc:
        print("FATAL", exc)
        raise
