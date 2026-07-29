#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Submit atr_squeeze_structural_breakout into STEP A on production host.

Does NOT auto-mount. Does NOT restart formal daemons.
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

PACK_PATH = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/strategy_atr_squeeze_structural_breakout.json")
DIRECTION = (sys.argv[2] if len(sys.argv) > 2 else "long").lower()
SYMBOL = sys.argv[3] if len(sys.argv) > 3 else "BTC-USDT-SWAP"
TIMEFRAME = sys.argv[4] if len(sys.argv) > 4 else "15m"


def main():
    pack = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    spec = pack["mechanism_spec"]
    dsl = pack.get("dsl_long" if DIRECTION == "long" else "dsl_short")
    if not isinstance(dsl, dict):
        raise SystemExit("missing dsl for direction=%s" % DIRECTION)

    # Local validate before pipeline
    import auto_trade_strategy_dsl as dsl_mod
    dsl_mod.validate_strategy(dsl)
    print("dsl_validate_ok", dsl.get("key"))

    from dual_engine_workflow_v2.pipeline_step_a import run_creation_pipeline_step_a
    from dual_engine_workflow_v2.step_a_config import STEP_A_CODE_VERSION

    print("STEP_A_CODE_VERSION", STEP_A_CODE_VERSION)
    print("submit", SYMBOL, TIMEFRAME, DIRECTION, "family", spec.get("mechanism_family"))
    t0 = time.time()
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
                "source": "atr_squeeze_structural_breakout_handcraft",
            },
            "errors": [],
            "call_id": "atr_squeeze_%s_%s" % (DIRECTION, int(time.time())),
            "attempts": 0,
        },
        windtalker_tag="atr_squeeze_structural_breakout_%s" % DIRECTION,
    )
    elapsed = round(time.time() - t0, 2)
    out = {
        "elapsed_sec": elapsed,
        "result": result,
    }
    out_path = ROOT / "auto_trade" / "dual_engine" / "workflow_v2" / (
        "atr_squeeze_submit_%s_%s.json" % (DIRECTION, time.strftime("%Y%m%d_%H%M%S"))
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("RESULT_PATH", out_path)
    print("RESULT_SUMMARY", json.dumps({
        "ok": (result or {}).get("ok"),
        "task_id": (result or {}).get("task_id"),
        "reason": (result or {}).get("reason") or (result or {}).get("stage"),
        "elapsed_sec": elapsed,
    }, ensure_ascii=False))
    return 0 if (result or {}).get("ok") else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
