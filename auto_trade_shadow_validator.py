# -*- coding: utf-8 -*-
"""DELETED: shadow validator (designer 2026-07-24).

Live path is now: creation factory → machine screen → human confirm → B live.
"""
from __future__ import print_function
import json

DELETED = True


def run_once(*args, **kwargs):
    return {"ok": True, "deleted": True,
            "reason": "shadow_validator_deleted_use_human_confirm_pipeline"}


def _loss_streak(values):
    streak = best = 0
    for value in values or []:
        if float(value) <= 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def _micro_snapshot(*args, **kwargs):
    return {"deleted": True}


if __name__ == "__main__":
    print(json.dumps(run_once(), ensure_ascii=False, indent=2))
