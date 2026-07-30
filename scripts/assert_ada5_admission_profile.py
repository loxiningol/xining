#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assert production STEP A uses ADA5顺势回升 (ada_t3_calibrated_v1) admission."""
from __future__ import print_function

import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))


def main():
    from dual_engine_workflow_v2 import pipeline_step_a as p
    from dual_engine_workflow_v2 import review_admission_v2 as a
    import auto_trade_ai_consensus as ai

    profile = p._admission_profile()
    enabled = p._admission_v2_enabled()
    ok = (
        profile == "ada_t3_calibrated_v1"
        and enabled
        and a.PROFILE == "ada_t3_calibrated_v1"
        and a.GOLDEN_KEY == "codex0725t3_ada5m_trendpb_r42_z2p3_h14"
        and float(ai.MIN_THEORETICAL_WR) >= 65.0
        and float(ai.MIN_THEORETICAL_WIN_MEAN_NET_PCT) >= 5.0
    )
    print("profile", profile)
    print("v2_enabled", enabled)
    print("golden", a.GOLDEN_KEY, a.GOLDEN_TITLE)
    print("R2", a.MIN_TRADES, a.MIN_WIN_RATE_PCT, a.REQUIRE_POSITIVE_MEAN_NET)
    print("R3_soft", a.R3_SOFT_PASS_ON_LEGACY_FAIL)
    print("R4", ai.MIN_THEORETICAL_WR, ai.MIN_THEORETICAL_WIN_MEAN_NET_PCT)
    print("OK" if ok else "FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
