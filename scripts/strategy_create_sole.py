#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SOLE creation CLI — the only supported human/Cursor creation command.

Usage:
  python3 scripts/strategy_create_sole.py \\
    --symbol ADA-USDT-SWAP --timeframe 5m --brief "人类指令"
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
    ap = argparse.ArgumentParser(description="Sole research-discovery creation entry")
    ap.add_argument("--symbol", default="ADA-USDT-SWAP")
    ap.add_argument("--timeframe", default="5m")
    ap.add_argument("--direction", default="long", choices=("long", "short", "both"))
    ap.add_argument("--brief", default="", required=False)
    ap.add_argument("--with-llm", action="store_true")
    ap.add_argument("--with-glm-spec", action="store_true")
    ap.add_argument("--submit-step-a", action="store_true")
    args = ap.parse_args()

    from dual_engine_workflow_v2.creation_sole_entry import create_strategy

    print(
        "SOLE_CREATION_START", args.symbol, args.timeframe,
        "brief_len", len(args.brief or ""), flush=True,
    )
    t0 = time.time()
    out = create_strategy(
        symbol=args.symbol,
        timeframe=args.timeframe,
        direction=args.direction,
        brief=args.brief,
        skip_llm=not args.with_llm,
        with_glm_spec=args.with_glm_spec,
        submit_step_a=args.submit_step_a,
    )
    print(
        "SOLE_CREATION_DONE", round(time.time() - t0, 1),
        "ok", out.get("ok"),
        "present", out.get("present_to_human"),
        "gate", (out.get("pipeline_gate") or {}).get("passed"),
        flush=True,
    )
    print(json.dumps({
        "ok": out.get("ok"),
        "schema": out.get("schema"),
        "pipeline_gate": out.get("pipeline_gate"),
        "present_to_human": out.get("present_to_human"),
        "handoff_zh": out.get("handoff_zh"),
        "receipt_path": out.get("receipt_path"),
        "run_id": ((out.get("blueprint") or {}).get("run_id")),
        "discovery": ((out.get("blueprint") or {}).get("stages") or {}).get("research_discovery"),
    }, ensure_ascii=False, indent=2, default=str))
    return 0 if out.get("ok") or (out.get("pipeline_gate") or {}).get("passed") else 2


if __name__ == "__main__":
    sys.exit(main() or 0)
