#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reproduce Phase0 Region A/B under strict time splits. No strategization yet."""
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
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument(
        "--out-dir",
        default="auto_trade/dual_engine/alpha_discovery/project_prometheus/region_conversion",
    )
    args = ap.parse_args()

    from dual_engine_workflow_v2 import easyquant_bridge as eq
    from project_prometheus import region_oos_reproduce as rr
    from project_prometheus import conversion_contract as CC

    candles_by_key = {}
    for region in CC.REGIONS.values():
        key = "%s|%s" % (region["symbol"], region["timeframe"])
        if key in candles_by_key:
            continue
        loaded = eq.load_candles(
            region["symbol"], region["timeframe"],
            max_bars=250000, prefer_research=True,
        )
        candles = loaded.get("candles") if isinstance(loaded, dict) else loaded
        candles_by_key[key] = candles or []

    pack = rr.run_all(candles_by_key, stride=int(args.stride))
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "REGION_OOS_REPRODUCE.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    md = rr.render_md(pack)
    (out / "REGION_OOS_REPRODUCE.md").write_text(md, encoding="utf-8")

    local = Path("alpha_discovery/project_prometheus/region_conversion")
    if Path("alpha_discovery").exists():
        local.mkdir(parents=True, exist_ok=True)
        for name in ("REGION_OOS_REPRODUCE.json", "REGION_OOS_REPRODUCE.md"):
            src = out / name
            if src.exists() and src.resolve() != (local / name).resolve():
                (local / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    summary = {
        "ok": True,
        "regions": {
            rid: {
                "status": r.get("status"),
                "may_strategize": r.get("may_strategize"),
                "strict_oos_pass": r.get("strict_oos_pass"),
                "recent_2y_pass": r.get("recent_2y_pass"),
                "full_wr": (r.get("full_sample") or {}).get("win_rate"),
                "val_wr": (r.get("validation") or {}).get("win_rate"),
                "test_wr": (r.get("test") or {}).get("win_rate"),
                "holdout_wr": (r.get("holdout_tail") or {}).get("win_rate"),
            }
            for rid, r in (pack.get("regions") or {}).items()
        },
        "out_dir": str(out),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
