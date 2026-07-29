#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Submit session_liq_engulf_displace_matrix to STEP A with L1-seed retries. No --confirm / no mount."""
from __future__ import print_function
import json, os, sys, time, traceback
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

PACK = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/strategy_session_liq_engulf_matrix_v1.json")
DIRECTION = (sys.argv[2] if len(sys.argv) > 2 else "long").lower()
SYMBOL = sys.argv[3] if len(sys.argv) > 3 else "ETH-USDT-SWAP"
TIMEFRAME = sys.argv[4] if len(sys.argv) > 4 else "15m"
MAX_TRIES = int(sys.argv[5] if len(sys.argv) > 5 else 10)
TAG = sys.argv[6] if len(sys.argv) > 6 else "session_liq_engulf_matrix_v1"


def main():
    import auto_trade_strategy_dsl as dsl_mod
    import auto_trade_human_confirm_pipeline as pipe
    from dual_engine_workflow_v2.pipeline_step_a import run_creation_pipeline_step_a
    from dual_engine_workflow_v2.step_a_config import STEP_A_CODE_VERSION

    pipe._FRAME_CACHE.clear()
    pack = json.loads(PACK.read_text(encoding="utf-8"))
    spec = pack["mechanism_spec"]
    dsl = pack.get("dsl_short" if DIRECTION == "short" else "dsl_long") or pack.get("dsl")
    dsl = dict(dsl)
    dsl["supported_instruments"] = [SYMBOL]
    dsl["timeframe"] = TIMEFRAME
    dsl_mod.validate_strategy(dsl)
    print(
        "STEP_A", STEP_A_CODE_VERSION, "submit", SYMBOL, TIMEFRAME, DIRECTION,
        "family", spec.get("mechanism_family"), flush=True,
    )

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
                        "source": "session_liq_engulf_displace_matrix_handcraft",
                    },
                    "errors": [],
                    "call_id": "sess_liq_%s_%s_%d" % (DIRECTION, int(time.time()), i),
                    "attempts": 0,
                },
                windtalker_tag="%s_%s_try%d" % (TAG, DIRECTION, i),
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
            "human_confirm_state": (result or {}).get("human_confirm_state"),
            "wx": (result or {}).get("wx") or (result or {}).get("wxpusher"),
            "production_mounted": (result or {}).get("production_mounted"),
        }
        for k in ("gate_results", "phase3_funnel", "pending_path"):
            if (result or {}).get(k) is not None:
                summary[k] = result.get(k)
        print("SUMMARY", json.dumps(summary, ensure_ascii=False, default=str)[:2500], flush=True)
        results.append({"summary": summary, "result": result})
        reason = str(summary.get("reason") or "")
        if summary.get("ok"):
            break
        if reason in ("funnel_l1_fail",):
            continue
        break

    out = ROOT / "auto_trade" / "dual_engine" / "workflow_v2" / (
        "session_liq_submit_%s_%s.json" % (DIRECTION, time.strftime("%Y%m%d_%H%M%S"))
    )
    out.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2, default=str))
    print("OUT", out, flush=True)
    last = results[-1]["summary"] if results else {}
    return 0 if last.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
