#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Submit htf_ema_align_ltf_cci_vol_pullback into STEP A on production.

Does NOT auto-mount. Does NOT pass --confirm. Does NOT restart formal daemons.
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

PACK_PATH = Path(
    sys.argv[1] if len(sys.argv) > 1 else "/root/strategy_htf_ltf_pullback_v1.json"
)
DIRECTION = (sys.argv[2] if len(sys.argv) > 2 else "long").lower()
SYMBOL = sys.argv[3] if len(sys.argv) > 3 else "BTC-USDT-SWAP"
TIMEFRAME = sys.argv[4] if len(sys.argv) > 4 else "15m"


def main():
    pack = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    spec = pack["mechanism_spec"]
    dsl = pack.get("dsl_long" if DIRECTION == "long" else "dsl_short")
    if not isinstance(dsl, dict):
        raise SystemExit("missing dsl for direction=%s" % DIRECTION)

    import auto_trade_strategy_dsl as dsl_mod
    dsl_mod.validate_strategy(dsl)
    print("dsl_validate_ok", dsl.get("key"), flush=True)

    from dual_engine_workflow_v2.pipeline_step_a import run_creation_pipeline_step_a
    from dual_engine_workflow_v2.step_a_config import STEP_A_CODE_VERSION
    from dual_engine_workflow_v2.failure_kb import path_is_blocked

    fam = spec.get("mechanism_family")
    blocked, why = path_is_blocked("family|%s" % fam, family=fam)
    print("kb_family_check", fam, "blocked", blocked, why, flush=True)
    if blocked:
        raise SystemExit("family KB blocked: %s (%s)" % (fam, why))

    print("STEP_A_CODE_VERSION", STEP_A_CODE_VERSION, flush=True)
    print("submit", SYMBOL, TIMEFRAME, DIRECTION, "family", fam, flush=True)
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
                    "source": "htf_ltf_pullback_v1_handcraft",
                    "user_authorized_core_features": (pack.get("meta") or {}).get(
                        "user_authorized_core_features"
                    )
                    or [
                        "cci",
                        "ema21",
                        "ema53",
                        "ema200",
                        "h1_ema19",
                        "h1_ema53",
                    ],
                    "thesis": (pack.get("meta") or {}).get("thesis"),
                    "kb_avoidance": (pack.get("meta") or {}).get("kb_avoidance"),
                },
                "errors": [],
                "call_id": "htf_ltf_pb_%s_%s" % (DIRECTION, int(time.time())),
                "attempts": 0,
            },
            windtalker_tag="htf_ema_align_ltf_cci_vol_pullback_%s" % DIRECTION,
        )
    except Exception as exc:
        result = {
            "ok": False,
            "reason": "exception",
            "error": str(exc),
            "trace": traceback.format_exc()[-1200:],
        }
    elapsed = round(time.time() - t0, 2)
    out = {"elapsed_sec": elapsed, "result": result}
    out_path = ROOT / "auto_trade" / "dual_engine" / "workflow_v2" / (
        "htf_ltf_pullback_submit_%s_%s.json"
        % (DIRECTION, time.strftime("%Y%m%d_%H%M%S"))
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    summary = {
        "ok": (result or {}).get("ok"),
        "task_id": (result or {}).get("task_id"),
        "stage": (result or {}).get("stage"),
        "reason": (result or {}).get("reason"),
        "pending_key": ((result or {}).get("pending_push") or {}).get("key")
        or ((result or {}).get("human_confirm_state") or {}).get("push", {}).get("key"),
        "pending_ok": ((result or {}).get("pending_push") or {}).get("ok")
        or ((result or {}).get("human_confirm_state") or {}).get("pending_ok"),
        "elapsed_sec": elapsed,
    }
    print("RESULT_PATH", out_path, flush=True)
    print("RESULT_SUMMARY", json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
