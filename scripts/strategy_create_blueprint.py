#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DEPRECATED wrapper — redirects to sole creation entry."""
from __future__ import print_function

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))


def main():
    ap = argparse.ArgumentParser(description="Redirect → sole creation entry")
    ap.add_argument("--symbol", required=True, help="必须显式指定研究标的；ADA 已禁止研究")
    ap.add_argument("--timeframe", default="5m")
    ap.add_argument("--direction", default="long", choices=("long", "short", "both"))
    ap.add_argument("--brief", default="")
    ap.add_argument("--horizon", type=int, default=3)
    ap.add_argument("--skip-llm", action="store_true", default=True)
    ap.add_argument("--with-llm", action="store_true")
    ap.add_argument("--max-loops", type=int, default=5)
    args = ap.parse_args()
    from dual_engine_workflow_v2.creation_sole_entry import create_strategy
    print("REDIRECT_TO_SOLE_ENTRY", flush=True)
    out = create_strategy(
        symbol=args.symbol,
        timeframe=args.timeframe,
        direction=args.direction,
        brief=args.brief,
        skip_llm=(False if args.with_llm else True),
        max_loops=args.max_loops,
    )
    print(json.dumps({
        "ok": out.get("ok"),
        "schema": out.get("schema"),
        "pipeline_gate": out.get("pipeline_gate"),
        "present_to_human": out.get("present_to_human"),
        "handoff_zh": out.get("handoff_zh"),
        "redirected_from": "strategy_create_blueprint.py",
    }, ensure_ascii=False, indent=2, default=str))
    return 0 if (out.get("pipeline_gate") or {}).get("passed") else 2


if __name__ == "__main__":
    sys.exit(main() or 0)
