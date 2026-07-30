#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI: run pretest_quality on a strategy pack.

Usage:
  python3 scripts/pretest_quality.py --strategy strategy_ny_open_liq_fade_clean_v1.json
  python3 /root/scripts/pretest_quality.py --strategy /root/strategy_ny_open_liq_fade_clean_v1.json
"""
from __future__ import print_function

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pretest quality gate (anti-shit-decorate)")
    ap.add_argument("--strategy", required=True, help="Path to strategy pack JSON")
    ap.add_argument("--direction", default=None, choices=["long", "short"])
    args = ap.parse_args(argv)

    scripts_dir = Path(__file__).resolve().parent
    workspace = scripts_dir.parent
    vector_root = Path(os.environ.get("VECTOR_ROOT") or (
        "/root" if Path("/root/dual_engine_workflow_v2").exists() else str(workspace)
    ))
    sys.path.insert(0, str(vector_root))
    sys.path.insert(0, str(workspace))
    sys.path.insert(0, str(scripts_dir))

    from dual_engine_workflow_v2.pretest_quality import run_pretest_quality
    import auto_trade_strategy_dsl as dsl_mod

    path = Path(args.strategy)
    if not path.is_absolute():
        for root in (Path.cwd(), workspace, vector_root, scripts_dir):
            cand = root / args.strategy
            if cand.exists():
                path = cand
                break
    if not path.exists():
        print(json.dumps({"pass": False, "error": "pack_not_found", "path": str(path)}, ensure_ascii=False))
        return 2

    pack = json.loads(path.read_text(encoding="utf-8"))
    direction = args.direction or (pack.get("meta") or {}).get("direction") or "long"
    dsl = pack.get("dsl_%s" % direction) or pack.get("dsl")
    try:
        dsl_mod.validate_strategy(dsl)
    except Exception as exc:
        print(json.dumps({
            "pass": False,
            "quality": "SHIT_TRANSLATION",
            "verdict": "RESET_REQUIRED",
            "reason": "dsl_invalid:%s" % exc,
        }, ensure_ascii=False, indent=2))
        return 1

    report = run_pretest_quality(pack, direction=direction, search_roots=[
        workspace, vector_root, Path.cwd(),
    ])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    sys.exit(main())
