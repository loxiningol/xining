# -*- coding: utf-8 -*-
"""hub-a only wrapper example: Phase B 钝侧（统计 off）.

Copy/merge into VPS `/root/scripts/kimi_thin_recipe_loop_hub_a_strict.py`.
Hub-b Cursor must NOT restart hub-a.

Phase B：与标定侧 hub-b 分默认 — 此处 placebo/LOO/MC=off。
禁止与 hub-b 同日把统计项升 soft/hard。
"""
from __future__ import print_function

import os
import sys

os.chdir("/root")
os.environ.setdefault("VECTOR_ROOT", "/root")
os.environ.setdefault("PYTHONPATH", "/root")

sys.path.insert(0, "/root")
sys.path.insert(0, "/root/scripts")

from dual_engine_workflow_v2.creation_small_n_rigor import (  # noqa: E402
    PROFILE_HUB_A_PHASE_B,
    apply_profile,
    install_into_kdh,
)

print("hub_a_phase_b_profile: %s" % (apply_profile(PROFILE_HUB_A_PHASE_B, force=True),), flush=True)

LANE_ZH = {
    "primary": "1号车道",
    "backup": "2号车道",
    "eq2": "3号车道",
    "cr2": "4号车道",
}

import kimi_dual_http_create_20260906 as kdh  # noqa: E402

print("hub_a_small_n_rigor: %s" % (install_into_kdh(kdh),), flush=True)
print("hub_a_lane_zh: %s" % LANE_ZH, flush=True)

import kimi_thin_recipe_loop as loop

_orig_emit = loop.emit


def emit(obj):
    obj = dict(obj or {})
    lane = obj.get("lane")
    if lane in LANE_ZH:
        obj["lane_zh"] = LANE_ZH[lane]
    if obj.get("phase") == "boot":
        obj["lane_zh_map"] = dict(LANE_ZH)
        lanes = obj.get("lanes") or []
        obj["lanes_zh"] = [LANE_ZH.get(x, x) for x in lanes]
        cfg = getattr(kdh, "_small_n_rigor_config", None)
        obj["small_n_rigor"] = cfg
        obj["small_n_phase"] = (cfg or {}).get("phase")
        obj["small_n_hub_role"] = (cfg or {}).get("hub_role")
    return _orig_emit(obj)


loop.emit = emit
sys.exit(loop.main() or 0)
