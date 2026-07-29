#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-click autonomous Gate-retry driver (wrapper around STEP A; no core edits).

Examples:
  python3 scripts/run_auto_driver.py --config scripts/auto_driver_config.example.json
  python3 scripts/run_auto_driver.py --pack strategy_xxx.json --symbol ETH-USDT-SWAP \\
      --timeframe 15m --direction long --max-iterations 10
  python3 scripts/run_auto_driver.py --pack strategy_xxx.json --dry-run --dry-run-skip-ai
"""
from __future__ import print_function

import argparse
import json
import os
import sys
from pathlib import Path


def _resolve_path(p, search_roots):
    path = Path(p)
    if path.is_absolute() and path.exists():
        return path
    for root in search_roots:
        cand = Path(root) / p
        if cand.exists():
            return cand
    return path


def main(argv=None):
    scripts_dir = Path(__file__).resolve().parent
    workspace = scripts_dir.parent
    default_root = "/root" if Path("/root/dual_engine_workflow_v2").exists() else str(workspace)
    vector_root = Path(os.environ.get("VECTOR_ROOT") or default_root)

    # Prefer the auto_driver package shipped next to this script. VECTOR_ROOT
    # (/root) often also has a stale /root/auto_driver/ copy that would otherwise
    # shadow scripts/auto_driver after insert(0, vector_root).
    sys.path.insert(0, str(vector_root))
    sys.path.insert(0, str(workspace))
    sys.path.insert(0, str(scripts_dir))

    from auto_driver.driver import run_driver

    ap = argparse.ArgumentParser(description="Autonomous STEP A Gate-retry driver")
    ap.add_argument("--config", default=None, help="JSON config path")
    ap.add_argument("--pack", default=None, help="Initial strategy candidate pack JSON")
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--timeframe", default=None)
    ap.add_argument("--direction", default=None, choices=["long", "short"])
    ap.add_argument("--max-iterations", type=int, default=None)
    ap.add_argument("--l1-seed-retries", type=int, default=None)
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--providers", default=None, help="Comma-separated: deepseek,qwen,glm")
    ap.add_argument("--prefer-provider", default=None)
    ap.add_argument("--convergence-eps", type=float, default=None)
    ap.add_argument("--convergence-patience", type=int, default=None)
    ap.add_argument("--report-copy-to", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dry-run-skip-ai", action="store_true")
    ap.add_argument("--skip-pipeline", action="store_true")
    ap.add_argument("--vector-root", default=None)
    args = ap.parse_args(argv)

    search_roots = [Path.cwd(), workspace, vector_root, scripts_dir]
    cfg = {}
    if args.config:
        cfg_path = _resolve_path(args.config, search_roots)
        cfg = json.loads(Path(cfg_path).read_text(encoding="utf-8"))

    def override(key, value):
        if value is not None:
            cfg[key] = value

    override("vector_root", args.vector_root)
    override("pack_path", args.pack)
    override("symbol", args.symbol)
    override("timeframe", args.timeframe)
    override("direction", args.direction)
    override("max_iterations", args.max_iterations)
    override("l1_seed_retries", args.l1_seed_retries)
    override("workdir", args.workdir)
    override("tag", args.tag)
    override("prefer_provider", args.prefer_provider)
    override("convergence_eps", args.convergence_eps)
    override("convergence_patience", args.convergence_patience)
    override("report_copy_to", args.report_copy_to)
    if args.providers:
        cfg["ai_providers"] = [p.strip() for p in args.providers.split(",") if p.strip()]
    if args.dry_run:
        cfg["dry_run"] = True
    if args.dry_run_skip_ai:
        cfg["dry_run_skip_ai"] = True
    if args.skip_pipeline:
        cfg["skip_pipeline"] = True

    # defaults
    cfg.setdefault("vector_root", str(vector_root))
    cfg.setdefault("symbol", "ETH-USDT-SWAP")
    cfg.setdefault("timeframe", "15m")
    cfg.setdefault("direction", "long")
    cfg.setdefault("max_iterations", 10)
    cfg.setdefault("l1_seed_retries", 6)
    cfg.setdefault("tag", "auto_driver")
    cfg.setdefault("prefer_provider", "glm")
    cfg.setdefault("convergence_eps", 0.02)
    cfg.setdefault("convergence_patience", 3)
    cfg.setdefault("stagnant_reason_patience", 4)
    cfg.setdefault("max_kb_family_bumps", 3)
    cfg.setdefault("auto_bump_family_after_gate2", True)
    cfg.setdefault("ai_providers", ["deepseek", "qwen", "glm"])
    cfg.setdefault("report_copy_to", "delivery_report.md")
    cfg.setdefault("report_name", "delivery_report.md")

    if not cfg.get("pack_path"):
        ap.error("--pack or config.pack_path is required")

    # resolve pack path early for clearer errors
    pack_resolved = _resolve_path(cfg["pack_path"], search_roots + [Path(cfg["vector_root"])])
    if pack_resolved.exists():
        cfg["pack_path"] = str(pack_resolved)

    os.environ["VECTOR_ROOT"] = str(cfg["vector_root"])
    result = run_driver(cfg)
    print(json.dumps({
        "ok": result.get("ok"),
        "final_status": result.get("final_status"),
        "stop_code": result.get("stop_code"),
        "workdir": result.get("workdir"),
        "report_path": result.get("report_path"),
    }, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
