#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Retry STEP A submit for HTF/LTF pullback until L1 clears (seed-sensitive).

No auto-mount. No --confirm. No formal daemon restart.
"""
from __future__ import print_function

import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

PACK = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/strategy_htf_ltf_pullback_v2.json")
DIRECTION = (sys.argv[2] if len(sys.argv) > 2 else "long").lower()
SYMBOL = sys.argv[3] if len(sys.argv) > 3 else "BTC-USDT-SWAP"
TIMEFRAME = sys.argv[4] if len(sys.argv) > 4 else "15m"
MAX_TRIES = int(sys.argv[5] if len(sys.argv) > 5 else 12)


def main():
    import auto_trade_strategy_dsl as dsl_mod
    from dual_engine_workflow_v2.pipeline_step_a import run_creation_pipeline_step_a
    from dual_engine_workflow_v2.step_a_config import STEP_A_CODE_VERSION
    from dual_engine_workflow_v2.failure_kb import path_is_blocked

    pack = json.loads(PACK.read_text(encoding="utf-8"))
    spec = pack["mechanism_spec"]
    dsl = pack["dsl_long" if DIRECTION == "long" else "dsl_short"]
    dsl_mod.validate_strategy(dsl)
    fam = spec.get("mechanism_family")
    blocked, why = path_is_blocked("family|%s" % fam, family=fam)
    print("kb_family_check", fam, "blocked", blocked, why, flush=True)
    if blocked:
        raise SystemExit("family KB blocked")
    print("STEP_A", STEP_A_CODE_VERSION, "max_tries", MAX_TRIES, flush=True)

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
                        "source": "htf_ltf_pullback_v2_retry",
                        "user_authorized_core_features": (pack.get("meta") or {}).get(
                            "user_authorized_core_features"
                        ),
                        "thesis": (pack.get("meta") or {}).get("thesis"),
                    },
                    "errors": [],
                    "call_id": "htf_ltf_pb_v2_%s_%s_%d" % (DIRECTION, int(time.time()), i),
                    "attempts": 0,
                },
                windtalker_tag="htf_ema_align_ltf_cci_vol_pullback_v2_%s_try%d" % (DIRECTION, i),
            )
        except Exception as exc:
            result = {
                "ok": False,
                "reason": "exception",
                "error": str(exc),
                "trace": traceback.format_exc()[-800:],
            }
        elapsed = round(time.time() - t0, 2)
        pending = (result or {}).get("pending_push") or {}
        hc = (result or {}).get("human_confirm_state") or {}
        summary = {
            "try": i,
            "ok": (result or {}).get("ok"),
            "task_id": (result or {}).get("task_id"),
            "reason": (result or {}).get("reason") or (result or {}).get("stage"),
            "stage": (result or {}).get("stage"),
            "pending_key": pending.get("key") or (hc.get("push") or {}).get("key"),
            "pending_ok": pending.get("ok") if "ok" in pending else hc.get("pending_ok"),
            "elapsed": elapsed,
        }
        print("SUMMARY", json.dumps(summary, ensure_ascii=False), flush=True)
        results.append({"summary": summary, "result": result})
        reason = str(summary.get("reason") or "")
        if summary.get("ok"):
            break
        if reason in ("funnel_l1_fail",):
            continue
        # hard stop on other gate fails / kb / duplicate / gate1
        break

    out = ROOT / "auto_trade" / "dual_engine" / "workflow_v2" / (
        "htf_ltf_pullback_retry_%s_%s.json" % (DIRECTION, time.strftime("%Y%m%d_%H%M%S"))
    )
    out.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2, default=str))
    print("OUT", out, flush=True)
    last = results[-1]["summary"] if results else {}
    print("RESULT_SUMMARY", json.dumps(last, ensure_ascii=False), flush=True)
    return 0 if last.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
