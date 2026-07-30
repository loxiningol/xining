#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GLM-first strategy creation collaboration (optional EasyQuant modeling envelope).

Usage (on VECTOR_ROOT / prod):
  python3 scripts/strategy_create_collab.py \\
    --symbol ETH-USDT-SWAP --timeframe 5m --direction long \\
    --brief "用户口述的机制偏好"

Does NOT mount. Writes a collab pack under auto_trade/dual_engine/collab_packs/.
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
    ap = argparse.ArgumentParser(description="GLM+EasyQuant strategy creation collab")
    ap.add_argument("--symbol", default="ETH-USDT-SWAP")
    ap.add_argument("--timeframe", default="5m")
    ap.add_argument("--direction", default="long", choices=("long", "short", "both"))
    ap.add_argument("--brief", default="", help="Human order / mechanism preference")
    ap.add_argument("--mode", default="new_mechanism",
                    choices=("new_mechanism", "known_mechanism_deep_dig",
                             "combination_mechanism", "failure_reverse_research"))
    ap.add_argument("--implement", action="store_true",
                    help="Also run codex_implement_from_spec after GLM spec")
    ap.add_argument("--submit-step-a", action="store_true",
                    help="Submit to STEP A without prebuilt bypass of GLM (uses live spec)")
    args = ap.parse_args()

    from dual_engine_workflow_v2.easyquant_bridge import probe_easyquant, modeling_envelope
    from dual_engine_workflow_v2.pipeline_step_a import (
        glm_require_mechanism_spec, codex_implement_from_spec,
    )

    focus = {
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "direction": args.direction,
        "human_brief": args.brief,
    }
    eq = probe_easyquant()
    envelope = modeling_envelope(brief=args.brief, focus=focus, probe=eq)
    print("EASYQUANT", json.dumps(eq, ensure_ascii=False))

    # glm_require_mechanism_spec expects exploration isolation dict, not Mode A/B/C.
    mode_ctx = {"schema": "qiyu_create_collab_v1", "isolation": args.mode, "mode_name": args.mode}
    try:
        from dual_engine_workflow_v2 import failure_kb
        kb_ctx = failure_kb.compact_context_for_glm() if hasattr(failure_kb, "compact_context_for_glm") else {}
        if not kb_ctx and hasattr(failure_kb, "load_kb"):
            kb_ctx = {"note": "kb_present_compact_unavailable"}
    except Exception:
        kb_ctx = {}

    # Inject EasyQuant / human brief into focus so GLM sees it
    focus["easyquant_modeling"] = envelope
    focus["order_zh"] = args.brief or "（无额外口述，按空白利基自主提出新机制）"

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
            "easyquant": eq,
            "spec_pack": spec_pack,
        }
    else:
        spec = spec_pack.get("mechanism_spec") or {}
        meta = dict(spec_pack.get("meta") or {})
        meta.setdefault("symbol", args.symbol)
        meta.setdefault("timeframe", args.timeframe)
        meta.setdefault("direction", args.direction if args.direction != "both" else meta.get("direction") or "long")
        meta["source"] = "glm_live_collab"
        meta["easyquant_probe"] = eq
        meta["human_brief"] = args.brief
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
            "easyquant_modeling": envelope,
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
            # Pass GLM-produced pack as prebuilt ONLY after live GLM design —
            # this preserves the spec GLM just wrote (does not skip design).
            result = run_creation_pipeline_step_a(
                symbol=args.symbol,
                timeframe=args.timeframe,
                exploration_mode="A",
                allow_horizontal_expand=False,
                prebuilt_spec_pack=pack,
                windtalker_tag="glm_collab_%s" % int(time.time()),
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
    out_path = out_dir / ("collab_%s_%s_%s.json" % (args.symbol.split("-")[0].lower(), args.timeframe, stamp))
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("WROTE", out_path)
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    # build_mode_context may not exist on older trees — soft import inside main
    try:
        sys.exit(main() or 0)
    except Exception as exc:
        print("FATAL", exc)
        raise
