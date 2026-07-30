#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run creation blueprint stages ①–⑤ only (NO review, NO mount).

Usage:
  python3 scripts/strategy_create_blueprint.py \\
    --symbol ADA-USDT-SWAP --timeframe 5m --direction long \\
    --brief "用户口述" --skip-llm
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
    ap = argparse.ArgumentParser(description="Creation blueprint ①–⑤ (pre-review)")
    ap.add_argument("--symbol", default="ADA-USDT-SWAP")
    ap.add_argument("--timeframe", default="5m")
    ap.add_argument("--direction", default="long", choices=("long", "short", "both"))
    ap.add_argument("--brief", default="")
    ap.add_argument("--horizon", type=int, default=3)
    ap.add_argument("--skip-llm", action="store_true", default=True)
    ap.add_argument("--with-llm", action="store_true",
                    help="Enable GLM meta-enrich + DeepSeek factor proposals")
    ap.add_argument("--max-loops", type=int, default=5)
    args = ap.parse_args()
    skip_llm = False if args.with_llm else True

    from dual_engine_workflow_v2.creation_blueprint import run_creation_blueprint

    print("BLUEPRINT_START", args.symbol, args.timeframe, "skip_llm", skip_llm, flush=True)
    t0 = time.time()
    out = run_creation_blueprint(
        symbol=args.symbol,
        timeframe=args.timeframe,
        direction=args.direction,
        brief=args.brief,
        horizon=args.horizon,
        skip_llm=skip_llm,
        max_loops=args.max_loops,
    )
    print("BLUEPRINT_ELAPSED", round(time.time() - t0, 1), flush=True)
    print("ok", out.get("ok"), "loops", out.get("loops"), "fuses", out.get("fuses"), flush=True)
    sel = ((out.get("stages") or {}).get("selected") or {})
    print(
        "selected", sel.get("factor"), sel.get("rule"),
        "qo", ((sel.get("quantoracle") or {}).get("source")),
        flush=True,
    )
    print("hyp", ((out.get("stages") or {}).get("hypothesis") or {}).get("passed"), flush=True)
    print("stress", ((out.get("stages") or {}).get("stress") or {}).get("passed"), flush=True)
    print("deliverables", out.get("deliverables"), flush=True)
    # also echo compact summary path
    summary = {
        "ok": out.get("ok"),
        "symbol": out.get("symbol"),
        "timeframe": out.get("timeframe"),
        "loops": out.get("loops"),
        "fuses": out.get("fuses"),
        "selected": sel,
        "deliverables": out.get("deliverables"),
    }
    print("SUMMARY", json.dumps(summary, ensure_ascii=False, default=str)[:1500], flush=True)
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception as exc:
        print("FATAL", exc)
        raise
