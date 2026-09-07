# -*- coding: utf-8 -*-
"""Hub-b thin invent: lanes numbered 5–8 + Phase A small-n rigor install.

Do not run on hub-a.
"""
from __future__ import print_function

import os
import sys

os.environ.setdefault("KDH_THIN_NS", "b")

# Phase A Day-0 defaults for hub-b (stats observe only — no hard one-shot)
os.environ.setdefault("CREATE_ANTI_EVASION", "1")
os.environ.setdefault("CREATE_N_DISCOUNT", "1")
os.environ.setdefault("CREATE_TIMING_BUDGET", "soft")
os.environ.setdefault("CREATE_PLACEBO", "observe")
os.environ.setdefault("CREATE_LOO", "observe")
os.environ.setdefault("CREATE_MC_SUBSET", "observe")
os.environ.setdefault("CREATE_NOISE_STRESS", "off")

_ROOT = os.environ.get("VECTOR_ROOT") or "/root"
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_scripts = os.path.join(_ROOT, "scripts")
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

import kimi_dual_http_create_20260906 as kdh  # noqa: E402
from dual_engine_workflow_v2.creation_small_n_rigor import install_into_kdh  # noqa: E402

_rigor = install_into_kdh(kdh)
print("hub_b_small_n_rigor_installed: %s" % (_rigor,), flush=True)

import kimi_thin_recipe_loop as loop  # noqa: E402

# Classic role → hub-b public lane number
_ALIAS = {
    "5": "primary",  # kimi primary / equity
    "6": "backup",   # kimi backup / crypto
    "7": "eq2",      # was qwen; mouth may share primary while qwen off
    "8": "cr2",      # deepseek / crypto
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
        obj["small_n_rigor"] = getattr(kdh, "_small_n_rigor_config", None)
    return _orig_emit(obj)


loop.emit = emit


def main():
    return loop.main()


if __name__ == "__main__":
    sys.exit(main() or 0)
