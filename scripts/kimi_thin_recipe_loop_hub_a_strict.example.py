# -*- coding: utf-8 -*-
"""hub-a only wrapper example: 1–4号 + Phase A small-n rigor.

Copy/merge into VPS `/root/scripts/kimi_thin_recipe_loop_hub_a_strict.py`.
Hub-b Cursor must NOT restart hub-a; hub-a Cursor applies this.
"""
from __future__ import print_function

import os
import sys

os.chdir("/root")
os.environ.setdefault("VECTOR_ROOT", "/root")
os.environ.setdefault("PYTHONPATH", "/root")
# Phase A suggested defaults (same soft/observe as hub-b Day-0; do not raise to hard together)
os.environ.setdefault("CREATE_ANTI_EVASION", "1")
os.environ.setdefault("CREATE_N_DISCOUNT", "1")
os.environ.setdefault("CREATE_TIMING_BUDGET", "soft")
os.environ.setdefault("CREATE_PLACEBO", "observe")
os.environ.setdefault("CREATE_LOO", "observe")
os.environ.setdefault("CREATE_MC_SUBSET", "observe")
os.environ.setdefault("CREATE_NOISE_STRESS", "off")

sys.path.insert(0, "/root")
sys.path.insert(0, "/root/scripts")

LANE_ZH = {
    "primary": "1号车道",
    "backup": "2号车道",
    "eq2": "3号车道",
    "cr2": "4号车道",
}

import kimi_dual_http_create_20260906 as kdh
from dual_engine_workflow_v2.creation_small_n_rigor import install_into_kdh

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
        obj["small_n_rigor"] = getattr(kdh, "_small_n_rigor_config", None)
    return _orig_emit(obj)


loop.emit = emit
sys.exit(loop.main() or 0)
