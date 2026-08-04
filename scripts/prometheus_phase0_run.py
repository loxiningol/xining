#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run Prometheus Phase 0 mechanism-existence scan.

Does not touch Route-0 Discovery/Builder/Repair/Evolution.
Does not create strategies or call Formal.
"""
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
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--symbols",
        default="BTC-USDT-SWAP,ETH-USDT-SWAP,SOL-USDT-SWAP",
    )
    ap.add_argument("--timeframes", default="5m,15m")
    ap.add_argument("--max-bars", type=int, default=250000)
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--max-cells-per-spec", type=int, default=27)
    ap.add_argument(
        "--out-dir",
        default="auto_trade/dual_engine/alpha_discovery/project_prometheus/phase0",
    )
    args = ap.parse_args()

    from dual_engine_workflow_v2 import easyquant_bridge as eq
    from project_prometheus import phase0_existence as pe
    from project_prometheus import phase0_contract as C

    symbols = [s.strip() for s in str(args.symbols).split(",") if s.strip()]
    timeframes = [t.strip() for t in str(args.timeframes).split(",") if t.strip()]

    all_rows = []
    load_meta = []
    for sym in symbols:
        for tf in timeframes:
            loaded = eq.load_candles(
                sym, tf, max_bars=int(args.max_bars), prefer_research=True
            )
            candles = loaded.get("candles") if isinstance(loaded, dict) else loaded
            meta = {
                "symbol": sym,
                "timeframe": tf,
                "n": len(candles) if candles else 0,
            }
            load_meta.append(meta)
            if not candles or len(candles) < 500:
                meta["skipped"] = "insufficient_bars"
                continue
            rows = pe.scan_symbol(
                candles,
                symbol=sym,
                timeframe=tf,
                stride=int(args.stride),
                max_cells_per_spec=int(args.max_cells_per_spec),
            )
            all_rows.extend(rows)
            meta["n_regions"] = len(rows)

    verdict = pe.existence_verdict(all_rows)
    pack = {
        "at": pe._now(),
        "project": "Prometheus",
        "phase": 0,
        "purpose": "profit_mechanism_existence",
        "not_strategy_creation": True,
        "route0_untouched": True,
        "contract": C.CONTRACT_NOTE,
        "existence_floors": {
            "win_rate_min": C.EXISTENCE_WIN_RATE_MIN,
            "mean_win_levered_net_min": C.EXISTENCE_MEAN_WIN_LEVERED_NET_MIN,
            "weekly_min": C.EXISTENCE_WEEKLY_FREQ_MIN,
            "min_resolved": C.EXISTENCE_MIN_RESOLVED,
            "min_temporal_positive": C.EXISTENCE_MIN_TEMPORAL_POSITIVE,
            "fee_drag_levered": C.LEVERED_FEE_DRAG,
        },
        "symbols": symbols,
        "timeframes": timeframes,
        "load_meta": load_meta,
        "n_rows": len(all_rows),
        "verdict_pack": verdict,
        "rows": all_rows,
    }
    out_dir = pe.save_pack(pack, args.out_dir)

    # local mirror
    local = Path("alpha_discovery/project_prometheus/phase0")
    if Path("alpha_discovery").exists():
        local.mkdir(parents=True, exist_ok=True)
        for name in (
            "PHASE0_EXISTENCE.json",
            "PHASE0_EXISTENCE_REPORT.md",
            "Prometheus_Phase0_收益机制存在性报告.md",
        ):
            src = Path(args.out_dir) / name
            if src.exists() and src.resolve() != (local / name).resolve():
                (local / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    summary = {
        "ok": True,
        "verdict": verdict.get("verdict"),
        "n_passing_regions": verdict.get("n_passing_regions"),
        "n_regions_tested": verdict.get("n_regions_tested"),
        "statement_zh": verdict.get("statement_zh"),
        "out_dir": str(out_dir),
        "target_levered_net_if_exit_at_target": C.TARGET_LEVERED_NET,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
