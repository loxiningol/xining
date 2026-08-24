#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild /root/auto_trade/vector_status_cache.json from current live roster."""
from __future__ import print_function

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main():
    import web_server as ws
    import auto_trade_roster_display_metrics as rdm

    payload = ws._vector_status_payload()
    rdm.save_vector_status_cache(payload)
    zones = payload.get("asset_zones") or []
    count = sum(
        len((z.get("records") or {}).get("strategy_stats") or [])
        for z in zones
    )
    print(json.dumps({
        "ok": True,
        "zones": len(zones),
        "strategy_count": count,
        "time": payload.get("time"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
