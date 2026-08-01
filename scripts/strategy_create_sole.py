#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Submit a human/Cursor/Codex direction to the sole parallel creation queue."""
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
    ap = argparse.ArgumentParser(description="提交策略研究方向到唯一创造入口（管道1/管道2）")
    ap.add_argument("--source", default="human", choices=(
        "cursor", "codex", "human", "web", "system_timer", "direct",
    ))
    ap.add_argument("--research-direction", required=True)
    ap.add_argument("--symbol", default="ADA-USDT-SWAP")
    ap.add_argument("--timeframe", default="5m")
    ap.add_argument(
        "--direction", default="long", choices=("long", "short"),
        help="双向研究必须拆成独立 long/short 任务，避免编译与统计身份混用",
    )
    ap.add_argument("--brief", default="")
    ap.add_argument("--brief-file", default="")
    ap.add_argument("--with-llm", action="store_true")
    ap.add_argument("--max-loops", type=int, default=5)
    ap.add_argument("--research-contract-file", default="")
    ap.add_argument("--mutation-contract-file", default="")
    ap.add_argument("--data-version", default="")
    ap.add_argument("--code-version", default="")
    ap.add_argument("--cooldown-seconds", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--pipeline", default="",
        help="指定管道：1 / 2 / 管道1 / 管道2；省略则自动分配",
    )
    args = ap.parse_args()
    brief = args.brief
    if args.brief_file:
        brief = Path(args.brief_file).read_text(encoding="utf-8")
    research_contract = (
        json.loads(Path(args.research_contract_file).read_text(encoding="utf-8"))
        if args.research_contract_file else None
    )
    mutation_contract = (
        json.loads(Path(args.mutation_contract_file).read_text(encoding="utf-8"))
        if args.mutation_contract_file else None
    )
    from dual_engine_workflow_v2.parallel_creation import submit_job
    out = submit_job(
        source=args.source,
        research_direction=args.research_direction,
        symbol=args.symbol,
        timeframe=args.timeframe,
        direction=args.direction,
        brief=brief,
        skip_llm=not args.with_llm,
        max_loops=args.max_loops,
        pipeline=(args.pipeline or None),
        research_contract=research_contract,
        mutation_contract=mutation_contract,
        data_version=(args.data_version or None),
        code_version=(args.code_version or None),
        cooldown_seconds=args.cooldown_seconds,
        force=args.force,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main() or 0)
