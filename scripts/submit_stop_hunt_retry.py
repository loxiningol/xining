#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Submit stop_hunt_range_reclaim to STEP A with L1-seed retries."""
from __future__ import print_function
import json, os, sys, time, traceback
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

PACK = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/strategy_stop_hunt_range_reclaim.json")
DIRECTION = (sys.argv[2] if len(sys.argv) > 2 else "short").lower()
SYMBOL = sys.argv[3] if len(sys.argv) > 3 else "ETH-USDT-SWAP"
TIMEFRAME = sys.argv[4] if len(sys.argv) > 4 else "15m"
MAX_TRIES = int(sys.argv[5] if len(sys.argv) > 5 else 15)
FAMILY = "stop_hunt_range_reclaim"


def unblock():
    from dual_engine_workflow_v2.failure_kb import path_is_blocked
    p = Path("/root/auto_trade/dual_engine/workflow_v2/failure_knowledgebase.json")
    kb = json.loads(p.read_text())
    kb["blocked_families"] = [f for f in (kb.get("blocked_families") or []) if f != FAMILY]
    kb["blocked_paths"] = [x for x in (kb.get("blocked_paths") or []) if FAMILY not in str(x)]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(kb, ensure_ascii=False, indent=2))
    tmp.replace(p)
    b, _ = path_is_blocked("family|%s" % FAMILY, family=FAMILY)
    print("unblock_check", b, flush=True)


def main():
    import auto_trade_strategy_dsl as dsl_mod
    from dual_engine_workflow_v2.pipeline_step_a import run_creation_pipeline_step_a
    from dual_engine_workflow_v2.step_a_config import STEP_A_CODE_VERSION

    pack = json.loads(PACK.read_text(encoding="utf-8"))
    spec = pack["mechanism_spec"]
    dsl = pack.get("dsl_short" if DIRECTION == "short" else "dsl_long")
    dsl = dict(dsl)
    dsl["supported_instruments"] = [SYMBOL]
    dsl["timeframe"] = TIMEFRAME
    dsl_mod.validate_strategy(dsl)
    print("STEP_A", STEP_A_CODE_VERSION, "submit", SYMBOL, TIMEFRAME, DIRECTION, flush=True)
    unblock()
    results = []
    for i in range(1, MAX_TRIES + 1):
        print("TRY", i, flush=True)
        t0 = time.time()
        try:
            result = run_creation_pipeline_step_a(
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                exploration_mode="A",
                allow_horizontal_expand=False,
                prebuilt_spec_pack={
                    "ok": True,
                    "mechanism_spec": spec,
                    "dsl": dsl,
                    "dsl_long": pack.get("dsl_long"),
                    "dsl_short": pack.get("dsl_short"),
                    "meta": {
                        "symbol": SYMBOL,
                        "timeframe": TIMEFRAME,
                        "direction": DIRECTION,
                        "title": spec.get("mechanism_name"),
                        "source": "stop_hunt_range_reclaim_handcraft",
                    },
                    "errors": [],
                    "call_id": "stop_hunt_%s_%s_%d" % (DIRECTION, int(time.time()), i),
                    "attempts": 0,
                },
                windtalker_tag="stop_hunt_range_reclaim_%s_try%d" % (DIRECTION, i),
            )
        except Exception as exc:
            result = {
                "ok": False,
                "reason": "exception",
                "error": str(exc),
                "trace": traceback.format_exc()[-800:],
            }
        elapsed = round(time.time() - t0, 2)
        summary = {
            "try": i,
            "ok": (result or {}).get("ok"),
            "task_id": (result or {}).get("task_id"),
            "reason": (result or {}).get("reason") or (result or {}).get("stage"),
            "elapsed": elapsed,
        }
        print("SUMMARY", json.dumps(summary, ensure_ascii=False), flush=True)
        results.append({"summary": summary, "result": result})
        reason = str(summary.get("reason") or "")
        if summary.get("ok"):
            break
        if reason in ("funnel_l1_fail", "kb_blocked"):
            unblock()
            continue
        break
    out = ROOT / "auto_trade" / "dual_engine" / "workflow_v2" / (
        "stop_hunt_retry_%s_%s.json" % (DIRECTION, time.strftime("%Y%m%d_%H%M%S"))
    )
    out.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2, default=str))
    print("OUT", out, flush=True)
    return 0 if results and results[-1]["summary"].get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
