#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refresh microstructure truth cards (manual / cron).

Default: rewrite seed cards. Pass --merge-json FILE to upsert RAG summaries.
"""
from __future__ import print_function

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))
os.environ.setdefault("VECTOR_ROOT", str(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge-json", default="", help="JSON list/object of extra cards")
    ap.add_argument("--reset-seed", action="store_true")
    args = ap.parse_args()
    from dual_engine_workflow_v2 import creation_knowledge_distill as kd
    if args.reset_seed:
        path = kd.cards_dir() / "microstructure_truth_cards.json"
        if path.is_file():
            path.unlink()
    extra = []
    if args.merge_json:
        raw = json.loads(Path(args.merge_json).read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "cards" in raw:
            extra = raw["cards"]
        elif isinstance(raw, list):
            extra = raw
        elif isinstance(raw, dict):
            extra = [raw]
    out = kd.distill_refresh_stub(extra_cards=extra)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
