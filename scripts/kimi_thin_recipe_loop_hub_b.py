# -*- coding: utf-8 -*-
"""Hub-b thin invent: lanes 5–8 + Phase B/C small-n rigor (标定侧).

Do not run on hub-a.
Phase B: split from hub-a（a 钝 / b 标定）.
Phase C: first clause only — timing_budget soft→hard；placebo/LOO/MC 仍 observe.
"""
from __future__ import print_function

import os
import sys

os.environ.setdefault("KDH_THIN_NS", "b")

_ROOT = os.environ.get("VECTOR_ROOT") or "/root"
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_scripts = os.path.join(_ROOT, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

from dual_engine_workflow_v2.creation_small_n_rigor import (  # noqa: E402
    PROFILE_HUB_B_PHASE_C,
    apply_profile,
    install_into_kdh,
    record_promotion,
)

_prof = apply_profile(PROFILE_HUB_B_PHASE_C, force=True)
_promo = record_promotion(
    clause="CREATE_TIMING_BUDGET",
    from_tier="soft",
    to_tier="hard",
    hub="b",
    reason_zh="Phase C 首条：削弱堆叶凑过；统计项仍 observe，未满窗不升",
    asks=None,
)
print("hub_b_phase_bc_profile: %s" % (_prof,), flush=True)
print("hub_b_phase_c_promotion: %s" % (_promo,), flush=True)

import kimi_dual_http_create_20260906 as kdh  # noqa: E402

_rigor = install_into_kdh(kdh)
print("hub_b_small_n_rigor_installed: %s" % (_rigor,), flush=True)

import kimi_thin_recipe_loop as loop  # noqa: E402

_ALIAS = {
    "5": "primary",
    "6": "backup",
    "7": "eq2",
    "8": "cr2",
}

for num, old in _ALIAS.items():
    if num not in loop.SEEDS and old in loop.SEEDS:
        loop.SEEDS[num] = dict(loop.SEEDS[old])

_CRYPTO_LANES = set(("6", "8"))
_EQUITY_LANES = set(("5", "7"))
_orig_channel = loop.channel


def channel(lane, state):
    name = str(lane or "").strip()
    lane_map = dict(kdh.LANES or {})
    if name in _CRYPTO_LANES:
        lane_map[name] = list(kdh.CRYPTO)
    elif name in _EQUITY_LANES:
        lane_map[name] = list(kdh.EQUITY)
    kdh.LANES = lane_map
    return _orig_channel(lane, state)


loop.channel = channel

_orig_emit = loop.emit


def emit(obj):
    obj = dict(obj or {})
    if obj.get("phase") == "boot":
        obj["lane_numbers"] = {
            "5": "5号车道",
            "6": "6号车道",
            "7": "7号车道",
            "8": "8号车道",
        }
        obj["lane_alias"] = dict(_ALIAS)
        cfg = getattr(kdh, "_small_n_rigor_config", None)
        obj["small_n_rigor"] = cfg
        obj["small_n_phase"] = (cfg or {}).get("phase")
        obj["small_n_hub_role"] = (cfg or {}).get("hub_role")
    return _orig_emit(obj)


loop.emit = emit


def main():
    return loop.main()


if __name__ == "__main__":
    sys.exit(main() or 0)
