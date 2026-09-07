# -*- coding: utf-8 -*-
"""Hub-a thin invent wrapper: Phase E frozen calibrate side (蓝图 a=标定).

Merge into VPS ExecStart drop-in. hub-b Cursor must not restart hub-a unless asked.
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
    FROZEN_HUB_A,
    apply_profile,
    frozen_invariants,
    install_into_kdh,
    record_promotion,
)

_inv = frozen_invariants()
if not _inv.get("ok"):
    print("hub_a_frozen_invariants_FAIL: %s" % (_inv.get("errors"),), flush=True)
    sys.exit(2)

print("hub_a_phase_e_profile: %s" % (apply_profile(FROZEN_HUB_A, force=True),), flush=True)
print("hub_a_frozen_invariants_ok: %s" % (_inv.get("ok"),), flush=True)
record_promotion(
    clause="PHASE_E_FREEZE_ALIGN",
    from_tier="blueprint",
    to_tier="E",
    hub="a",
    reason_zh="蓝图对齐：a=标定冻结 timing hard + 统计 observe",
)

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
        obj["small_n_frozen"] = True
        obj["small_n_blueprint"] = "a_calibrate_b_blunt"
    return _orig_emit(obj)


loop.emit = emit
sys.exit(loop.main() or 0)
