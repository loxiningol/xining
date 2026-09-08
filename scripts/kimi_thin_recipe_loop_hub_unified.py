# -*- coding: utf-8 -*-
"""Unified thin invent hub (blunt + FREE_CREATE).

Both hub-a and hub-b ExecStart this wrapper; only KDH_THIN_NS differs:
  hub-a: KDH_THIN_NS="" → /tmp/kdh_thin
  hub-b: KDH_THIN_NS=b → /tmp/kdh_thin_b

Lanes: 1→equity/kimi1, 2→crypto/kimi2. No calibrate vs blunt arm split.
"""
from __future__ import print_function

import os
import sys

os.chdir("/root")
os.environ.setdefault("VECTOR_ROOT", "/root")
os.environ.setdefault("PYTHONPATH", "/root")
os.environ.setdefault("KDH_FREE_CREATE", "1")
os.environ.setdefault("KDH_INVENT_STRICT_BIND", "1")

_ROOT = os.environ.get("VECTOR_ROOT") or "/root"
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_scripts = os.path.join(_ROOT, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from dual_engine_workflow_v2.creation_small_n_rigor import (  # noqa: E402
    FROZEN_HUB_B,
    apply_profile,
    frozen_invariants,
    install_into_kdh,
    record_promotion,
)

_NS = str(os.environ.get("KDH_THIN_NS") or "").strip().strip("_") or "a"
_HUB = "b" if _NS not in ("", "a") else "a"

_inv = frozen_invariants()
if not _inv.get("ok"):
    print("hub_unified_frozen_invariants_FAIL: %s" % (_inv.get("errors"),), flush=True)
    if _HUB == "b":
        sys.exit(2)
    print("hub_unified_frozen_invariants_WARN continuing invent", flush=True)

_prof = apply_profile(FROZEN_HUB_B, force=True)
try:
    record_promotion(
        clause="UNIFIED_BLUNT_FREE_CREATE",
        from_tier="phase_e",
        to_tier="unified",
        hub=_HUB,
        reason_zh="取消 a/b 臂差异；双侧 blunt+FREE_CREATE+双车道1/2",
    )
except Exception as exc:
    print("hub_unified_record_promotion_skip: %s" % (str(exc)[:120],), flush=True)

print("hub_unified_profile: %s hub=%s ns=%s" % (_prof, _HUB, _NS), flush=True)
print("hub_unified_frozen_invariants_ok: %s" % (_inv.get("ok"),), flush=True)

import kimi_dual_http_create_20260906 as kdh  # noqa: E402

_rigor = install_into_kdh(kdh)
print("hub_unified_small_n_rigor_installed: %s" % (_rigor,), flush=True)

import kimi_thin_recipe_loop as loop  # noqa: E402

LANE_ZH = {
    "1": "1号车道",
    "2": "2号车道",
    "primary": "1号车道",
    "backup": "2号车道",
}

# Ensure numeric seeds exist even if inherit only has legacy keys.
for num, old in (("1", "primary"), ("2", "backup")):
    if num not in loop.SEEDS and old in loop.SEEDS:
        loop.SEEDS[num] = dict(loop.SEEDS[old])

_CRYPTO = set(("2", "backup", "cr2", "6", "8"))
_EQUITY = set(("1", "primary", "eq2", "5", "7"))
_orig_channel = loop.channel


def channel(lane, state):
    name = str(lane or "").strip()
    lane_map = dict(kdh.LANES or {})
    if name in _CRYPTO:
        lane_map[name] = list(kdh.CRYPTO)
    elif name in _EQUITY:
        lane_map[name] = list(kdh.EQUITY)
    kdh.LANES = lane_map
    return _orig_channel(lane, state)


loop.channel = channel

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
        obj["small_n_hub_role"] = "blunt_unified"
        obj["small_n_frozen"] = True
        obj["small_n_blueprint"] = "unified_blunt_free_create"
        obj["hub"] = _HUB
        obj["ns"] = _NS if _NS != "a" else "a"
        obj["unified_hub"] = True
    return _orig_emit(obj)


loop.emit = emit


def main():
    return loop.main()


if __name__ == "__main__":
    sys.exit(main() or 0)
