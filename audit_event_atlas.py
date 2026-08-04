# -*- coding: utf-8 -*-
"""Read-only smoke audit for deterministic pre-ideation event mining."""
from __future__ import print_function

import json
import sys

import auto_trade_strategy_ecosystem as ecosystem


def main():
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "NG-USDT-SWAP").upper()
    timeframe = (sys.argv[2] if len(sys.argv) > 2 else "15m").lower()
    assignment = {"symbol": symbol, "timeframe": timeframe}
    frame = ecosystem._load_research_frame(symbol, timeframe)
    atlas = ecosystem._deterministic_event_atlas(frame, assignment)
    print(json.dumps({
        "instrument": atlas.get("instrument"),
        "timeframe": atlas.get("timeframe"),
        "tested_patterns": atlas.get("tested_patterns"),
        "qualified_count": atlas.get("qualified_count"),
        "qualified_patterns": (atlas.get("qualified_patterns") or [])[:5],
        "top_observed": (atlas.get("top_observed") or [])[:3],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
