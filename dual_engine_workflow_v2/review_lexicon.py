# -*- coding: utf-8 -*-
"""Canonical 复核 lexicon — titles MUST match backend admission checks.

【第一次复核】基础语法、逻辑断言、开仓密度预检
【第二次复核】单标的历史回测与样本收益稳定性
【第三次复核】多标的矩阵验证与抗风险离群测试
【第四次复核】三AI理论复核
【人工确认签发】Wx pending → 人类 --confirm（挂载门，不是“第N次复核”可省略编号）

复核次数不限死为 3；上列为当前流水线真实阶段。内部代码键可保留，
对外 / AI / Wx / UI 必须经 scrub()。
"""
from __future__ import print_function

import re

REVIEW_1 = "第一次复核"
REVIEW_2 = "第二次复核"
REVIEW_3 = "第三次复核"
REVIEW_4 = "第四次复核"

REVIEW_1_SCOPE = "基础语法、逻辑断言、开仓密度预检"
REVIEW_2_SCOPE = "单标的历史回测与样本收益稳定性"
REVIEW_3_SCOPE = "多标的矩阵验证与抗风险离群测试"
REVIEW_4_SCOPE = "三AI理论复核"

REVIEW_1_FULL = "【%s】（%s）" % (REVIEW_1, REVIEW_1_SCOPE)
REVIEW_2_FULL = "【%s】（%s）" % (REVIEW_2, REVIEW_2_SCOPE)
REVIEW_3_FULL = "【%s】（%s）" % (REVIEW_3, REVIEW_3_SCOPE)
REVIEW_4_FULL = "【%s】（%s）" % (REVIEW_4, REVIEW_4_SCOPE)

HUMAN_CONFIRM_GATE = "人工确认签发"
HUMAN_CONFIRM_NOTE = "四次复核通过后进入 WxPusher 人工确认；永不自动上线"

STAGE_TO_REVIEW = {
    "pretest": 1,
    "pretest_quality_fail": 1,
    "gate0": 1,
    "gate0_mechanism_integrity": 1,
    "gate1": 1,
    "gate1_fidelity": 1,
    "gate1_code_fidelity": 1,
    "gate1_fail": 1,
    "l0": 1,
    "funnel_l0_fail": 1,
    "funnel_l0_cull": 1,
    "funnel_l0_density": 1,
    "l0_sparse_limit": 1,
    "reset_required": 1,
    "validate_fail": 1,
    "review1_fail": 1,
    "review2_evidence_fail": 2,
    "l1": 2,
    "seed": 2,
    "funnel_l1_fail": 2,
    "funnel_l1_cull": 2,
    "funnel_l1_micro_screen": 2,
    "review2_fail": 2,
    "gate2": 3,
    "gate2_3_fail": 3,
    "gate2_base_backtest": 3,
    "gate3": 3,
    "gate3_walk_forward": 3,
    "repair_exhausted_or_drift": 3,
    "funnel_l2_full_bt_pareto": 3,
    "funnel_l3_null_hypothesis": 3,
    "audit4d": 3,
    "matrix": 3,
    "outlier": 3,
    "phase4_incubator": 3,
    "review3_fail": 3,
    "gate6": 4,
    "gate6_multi_ai": 4,
    "gate6_fail": 4,
    "phase5_consensus_fail": 4,
    "ai_theoretical_review_required": 4,
    "review4_fail": 4,
    "review4_ai_fail": 4,
    "review4_fail": 4,
    "三AI": 4,
}

REVIEW_LABEL = {1: REVIEW_1, 2: REVIEW_2, 3: REVIEW_3, 4: REVIEW_4}
REVIEW_SCOPE = {1: REVIEW_1_SCOPE, 2: REVIEW_2_SCOPE, 3: REVIEW_3_SCOPE, 4: REVIEW_4_SCOPE}
REVIEW_FULL = {1: REVIEW_1_FULL, 2: REVIEW_2_FULL, 3: REVIEW_3_FULL, 4: REVIEW_4_FULL}

# Pipeline checklist ids → compact marks + labels (must match backend meaning)
PIPE_ID_MAP = {
    "gate0": "R1a",
    "gate1": "R1b",
    "l0": "R1c",
    "l1": "R2",
    "gate2": "R3",
    "audit4d": "R3b",
    "ai3": "R4",
    "human": "HC",
}

PIPE_LABELS = {
    "gate0": "%s · 基础语法 / 机制完整性" % REVIEW_1,
    "gate1": "%s · 逻辑断言 / 代码忠实度" % REVIEW_1,
    "l0": "%s · 开仓密度预检" % REVIEW_1,
    "l1": "%s · 单标的回测稳定性" % REVIEW_2,
    "gate2": "%s · 矩阵验证" % REVIEW_3,
    "audit4d": "%s · 抗风险离群" % REVIEW_3,
    "ai3": "%s · 三AI理论复核" % REVIEW_4,
    "human": HUMAN_CONFIRM_GATE,
}

_SCRUB_RULES = [
    (re.compile(r"funnel_l0_cull", re.I), "%s未过：开仓密度过稀" % REVIEW_1),
    (re.compile(r"funnel_l0_fail", re.I), "%s未过：开仓密度不足" % REVIEW_1),
    (re.compile(r"funnel_l1_cull", re.I), "%s未过：样本收益不稳定" % REVIEW_2),
    (re.compile(r"funnel_l1_fail", re.I), "%s未过：单标的稳定性不足" % REVIEW_2),
    (re.compile(r"gate2_3_fail", re.I), "%s未过：矩阵 / 抗离群" % REVIEW_3),
    (re.compile(r"pretest_quality_fail", re.I), "%s未过：逻辑断言 / 契约失败" % REVIEW_1),
    (re.compile(r"review2_evidence_fail", re.I), "%s未过：样本收益稳定性不足" % REVIEW_2),
    (re.compile(r"review4_ai_fail", re.I), "%s未过：三AI理论复核" % REVIEW_4),
    (re.compile(r"ai_theoretical_review_required", re.I), "%s未过：三AI理论复核缺失" % REVIEW_4),
    (re.compile(r"Gate\s*0|gate0", re.I), REVIEW_1),
    (re.compile(r"Gate\s*1|gate1", re.I), REVIEW_1),
    (re.compile(r"Gate\s*2|gate2", re.I), REVIEW_3),
    (re.compile(r"Gate\s*3|gate3", re.I), REVIEW_3),
    (re.compile(r"Gate\s*4|gate4", re.I), "%s·抗风险拆分" % REVIEW_3),
    (re.compile(r"Gate\s*5|gate5", re.I), "%s·摩擦稳健" % REVIEW_3),
    (re.compile(r"Gate\s*6|gate6", re.I), REVIEW_4),
    (re.compile(r"Gate\s*7|gate7", re.I), HUMAN_CONFIRM_GATE),
    (re.compile(r"\bL0\b"), REVIEW_1),
    (re.compile(r"\bL1\b"), REVIEW_2),
    (re.compile(r"\bL2\b"), REVIEW_3),
    (re.compile(r"\bL3\b"), REVIEW_3),
    (re.compile(r"G0→G1→L0|G0→G1→L0密度→L1|轻量漏斗"), "四复核流水线"),
]


def review_n_from_reason(reason, stage=None):
    for key in (stage, reason):
        k = str(key or "").strip().lower()
        if k in STAGE_TO_REVIEW:
            return STAGE_TO_REVIEW[k]
    raw = str(reason or stage or "").lower()
    if any(x in raw for x in ("pretest", "l0", "gate0", "gate1", "validate", "fidelity", "density")):
        return 1
    if any(x in raw for x in ("l1", "seed", "evidence", "micro_screen")):
        return 2
    if any(x in raw for x in ("gate2", "gate3", "repair", "4d", "matrix", "outlier", "l2", "l3", "incubator")):
        return 3
    if any(x in raw for x in ("gate6", "ai_theoretical", "三ai", "3ai", "multi_ai", "phase5")):
        return 4
    return None


def review_label(n):
    try:
        n = int(n)
    except Exception:
        return None
    return REVIEW_LABEL.get(n)


def review_scope(n):
    try:
        n = int(n)
    except Exception:
        return None
    return REVIEW_SCOPE.get(n)


def review_full(n):
    try:
        n = int(n)
    except Exception:
        return None
    return REVIEW_FULL.get(n)


def review_label_from_reason(reason, stage=None):
    n = review_n_from_reason(reason, stage=stage)
    return review_label(n) if n else None


def pipe_label(stage_id, fallback=None):
    sid = str(stage_id or "")
    return PIPE_LABELS.get(sid) or fallback or sid


def pipe_short(stage_id):
    sid = str(stage_id or "")
    return PIPE_ID_MAP.get(sid, "R?")


def scrub(text):
    s = str(text if text is not None else "")
    if not s:
        return s
    for pat, repl in _SCRUB_RULES:
        s = pat.sub(repl, s)
    return s


def stage_title_zh(stage_id):
    n = review_n_from_reason(stage_id)
    if n:
        return "%s（%s）" % (REVIEW_LABEL[n], REVIEW_SCOPE[n])
    return scrub(stage_id)


def pipeline_stage_list():
    """Ordered user-facing stages for dashboards."""
    return [
        REVIEW_1_FULL,
        REVIEW_2_FULL,
        REVIEW_3_FULL,
        REVIEW_4_FULL,
        HUMAN_CONFIRM_GATE,
    ]
