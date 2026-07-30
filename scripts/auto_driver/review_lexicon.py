# -*- coding: utf-8 -*-
"""User-facing 复核 lexicon.

Internal stage codes (Gate0/L0/L1/Gate2/…) stay unchanged for logic.
Only UI / Wx / logs that humans read should use 第一/二/三次复核.
"""
from __future__ import print_function

# Canonical user-facing names
REVIEW_1 = "第一次复核"
REVIEW_2 = "第二次复核"
REVIEW_3 = "第三次复核"

# Internal stage → review ordinal (1/2/3)
STAGE_TO_REVIEW = {
    "pretest": 1,
    "pretest_quality_fail": 1,
    "gate0": 1,
    "gate1": 1,
    "l0": 1,
    "funnel_l0_fail": 1,
    "funnel_l0_cull": 1,
    "l0_sparse_limit": 1,
    "reset_required": 1,
    "review2_evidence_fail": 2,
    "l1": 2,
    "seed": 2,
    "funnel_l1_fail": 2,
    "funnel_l1_cull": 2,
    "gate2": 3,
    "gate2_3_fail": 3,
    "repair_exhausted_or_drift": 3,
    "audit4d": 3,
    "matrix": 3,
    "outlier": 3,
}

REVIEW_LABEL = {
    1: REVIEW_1,
    2: REVIEW_2,
    3: REVIEW_3,
}

# Compact pipe marks for dashboard (replace G0/L0… with R1/R2/R3)
PIPE_ID_MAP = {
    "gate0": "R1a",
    "gate1": "R1b",
    "l0": "R1",
    "l1": "R2",
    "gate2": "R3",
    "audit4d": "R3+",
}

# Pipeline row labels (UI checklist)
PIPE_LABELS = {
    "gate0": "%s · 机制完整性" % REVIEW_1,
    "gate1": "%s · 代码忠实度" % REVIEW_1,
    "l0": "%s · 开仓密度预检" % REVIEW_1,
    "l1": "%s · 单标的稳定性" % REVIEW_2,
    "gate2": "%s · 适应度 / 矩阵" % REVIEW_3,
    "audit4d": "%s · 四维攻击" % REVIEW_3,
}


def review_n_from_reason(reason, stage=None):
    """Return 1/2/3 for a pipeline reason or diagnostic stage."""
    for key in (stage, reason):
        k = str(key or "").strip().lower()
        if k in STAGE_TO_REVIEW:
            return STAGE_TO_REVIEW[k]
    raw = str(reason or stage or "").lower()
    if "pretest" in raw or "l0" in raw or "gate0" in raw or "gate1" in raw:
        return 1
    if "l1" in raw or "seed" in raw:
        return 2
    if "gate2" in raw or "repair" in raw or "4d" in raw or "matrix" in raw:
        return 3
    return None


def review_label(n):
    try:
        n = int(n)
    except Exception:
        return None
    return REVIEW_LABEL.get(n)


def review_label_from_reason(reason, stage=None):
    n = review_n_from_reason(reason, stage=stage)
    return review_label(n) if n else None


def pipe_label(stage_id, fallback=None):
    sid = str(stage_id or "")
    return PIPE_LABELS.get(sid) or fallback or sid


def pipe_short(stage_id):
    sid = str(stage_id or "")
    return PIPE_ID_MAP.get(sid, (sid or "?")[:2].upper())
