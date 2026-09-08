# -*- coding: utf-8 -*-
"""Single invent hub (no hub-a / hub-b split).

One queue /tmp/kdh_thin, one log, lanes 1→kimi1 2→kimi2.
Legacy KDH_THIN_NS=b is ignored: invent is one system.
"""
from __future__ import print_function

import os
import sys

os.chdir("/root")
os.environ.setdefault("VECTOR_ROOT", "/root")
os.environ.setdefault("PYTHONPATH", "/root")
os.environ.setdefault("KDH_FREE_CREATE", "1")
os.environ.setdefault("KDH_INVENT_STRICT_BIND", "1")
# Force single invent namespace (legacy parallel hubs retired).
os.environ["KDH_THIN_NS"] = ""
os.environ.pop("KDH_THIN_ROOT", None)

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

_inv = frozen_invariants()
if not _inv.get("ok"):
    print("invent_hub_frozen_invariants_WARN: %s" % (_inv.get("errors"),), flush=True)

_prof = apply_profile(FROZEN_HUB_B, force=True)
try:
    record_promotion(
        clause="SINGLE_INVENT_SYSTEM",
        from_tier="dual_hub",
        to_tier="single",
        hub="invent",
        reason_zh="取消 hub-a/hub-b 隔离；单一创造系统",
    )
except Exception as exc:
    print("invent_hub_record_promotion_skip: %s" % (str(exc)[:120],), flush=True)

print("invent_hub_profile: %s" % (_prof,), flush=True)
print("invent_hub_frozen_invariants_ok: %s" % (_inv.get("ok"),), flush=True)

import kimi_dual_http_create_20260906 as kdh  # noqa: E402

_rigor = install_into_kdh(kdh)
print("invent_hub_small_n_rigor_installed: %s" % (_rigor,), flush=True)

import kimi_thin_recipe_loop as loop  # noqa: E402

LANE_ZH = {
    "1": "1号车道",
    "2": "2号车道",
    "primary": "1号车道",
    "backup": "2号车道",
}

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
        obj["small_n_hub_role"] = "single_invent"
        obj["small_n_frozen"] = True
        obj["small_n_blueprint"] = "single_invent_system"
        obj["hub"] = "invent"
        obj["ns"] = "invent"
        obj["unified_hub"] = True
        obj["single_invent"] = True
        obj["parallel_hubs"] = False
    return _orig_emit(obj)


loop.emit = emit


def main():
    return loop.main()


if __name__ == "__main__":
    sys.exit(main() or 0)
