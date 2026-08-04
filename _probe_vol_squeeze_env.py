#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe prod env for session_vol_squeeze pack design."""
from __future__ import print_function

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/root")


def main():
    kb_path = Path("/root/auto_trade/dual_engine/workflow_v2/failure_knowledgebase.json")
    kb = json.loads(kb_path.read_text(encoding="utf-8"))
    print("KB blocked_families:")
    for f in kb.get("blocked_families") or []:
        print(" ", f if isinstance(f, str) else json.dumps(f, ensure_ascii=False)[:200])
    print("verdicts", Counter(r.get("verdict") for r in (kb.get("records") or []) if isinstance(r, dict)).most_common(15))
    for r in kb.get("records") or []:
        if isinstance(r, dict) and "session_liq" in str(r.get("family") or ""):
            print("sample_session_liq_record", json.dumps(r, ensure_ascii=False)[:400])
            break

    text = Path("/root/auto_trade_strategy_dsl.py").read_text(encoding="utf-8")
    m = re.search(r"FEATURES\s*=\s*\{([^}]+)\}", text)
    if m:
        feats = sorted(re.findall(r"[\"']([a-zA-Z0-9_]+)[\"']", m.group(1)))
        print("FEATURES", feats)

    import auto_trade_dual_engine_factory as dual

    frm = dual._frame("ETH-USDT-SWAP", "15m")
    print("nrows", len(frm))
    print("cols", list(frm.columns))
    interesting = [
        c
        for c in frm.columns
        if any(x in str(c).lower() for x in ("hour", "session", "asia", "range", "atr", "vol", "squeeze", "bb", "utc", "tod"))
    ]
    print("interesting", interesting)

    pack = json.loads(Path("/root/strategy_session_liq_engulf_matrix_ad1_seed.json").read_text(encoding="utf-8"))
    print("pack_top", sorted(pack.keys()))
    print("mech_keys", sorted((pack.get("mechanism_spec") or {}).keys()))
    print("dsl_exit", (pack.get("dsl_long") or pack.get("dsl") or {}).get("exit"))
    print("suitable_n", len((pack.get("mechanism_spec") or {}).get("suitable_symbols") or []))


if __name__ == "__main__":
    main()
