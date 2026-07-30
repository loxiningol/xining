# -*- coding: utf-8 -*-
"""HumanizerMiddleware — map raw driver enums to natural Chinese for UI.

User-facing lexicon uses 第一/二/三次复核; internal Gate/L codes stay in logic only.
"""
from __future__ import print_function

import re

from . import review_lexicon as lex

HUMAN_TRANSLATION_MAP = {
    # Terminal outcomes
    "LIMIT_REACHED_FAILED": "触达当前机制演进极限（已自动归档）",
    "LIMIT_REACHED": "触达当前机制演进极限（已自动归档）",
    "NO_PROGRESS_REPEATED_REASON": "策略参数陷入局部收敛，连续多轮无性能突破",
    "SUCCESS": "三次复核均已通过，策略待人工确认签发",
    "CONVERGED_NO_IMPROVEMENT": "统计指标已达帕累托前沿，终止微扰",
    "MAX_ITERATIONS": "已用尽本轮演进配额，自动收束",
    "MAX_AI_OPTIMIZE": "AI 优化已达上限（≤3 轮），策略失败归档",
    "AI_LIMIT_REACHED": "智能体判定已无优化空间，归档机制族",
    "AI_ABORT": "触发不可协商风控红线，已主动中止",
    "KB_BLOCKED_EXHAUSTED": "Failure KB 拦截次数耗尽，换族归档",
    "UNKNOWN_STOPPED": "进程已停止（状态未完整落盘）",
    "STOPPED": "已停止",
    "L0_SPARSE_CLEAN_PACK": "第一次复核：清洁包密度过稀（拒绝放宽入场雕花）",
    "RESET_REQUIRED": "第一次复核：契约/忠实度失败，需重译（禁止加法雕花）",
    # Pipeline reasons → 复核 lexicon
    "funnel_l0_cull": "第一次复核未过：开仓密度过稀（拒绝全量回测）",
    "funnel_l0_fail": "第一次复核未过：开仓密度不足 / 逻辑过稀",
    "funnel_l1_cull": "第二次复核未过：样本量或期望不足",
    "funnel_l1_fail": "第二次复核未过：单标的稳定性不足",
    "insufficient_evidence": "统计证据不足（未达到显著性要求）",
    "kb_blocked": "触发 Failure KB 负面指纹拦截",
    "repair_exhausted_or_drift": "第三次复核未过：修补耗尽或逻辑漂移",
    "gate2_3_fail": "第三次复核未过：适应度 / 矩阵门禁",
    "pretest_quality_fail": "第一次复核未过：预检契约失败（SHIT_TRANSLATION）",
    "exception": "执行异常，已安全回滚",
    # Mid-run phases
    "running": "自主演进运行中",
    "seed": "第二次复核 · 种子重试中",
    "l1": "第二次复核进行中",
    "calling_ai": "三方智能体协商补丁中",
    "ai": "三方智能体协商补丁中",
    "patch": "正在应用补丁并校验 DSL",
    "validate": "DSL 语法与忠实度校验中",
    "gate2": "第三次复核 · 适应度计算中",
    "success": "三次复核均已通过，策略待人工确认签发",
    "idle": "卡槽空闲，等待新机制入队",
    "queued": "已入队，等待空闲卡槽",
    "archived": "已归档极限",
    "breakthrough": "演进突破，进入第三次复核",
    # Legacy stage aliases (UI purge of Gate/L codes)
    "gate0": "%s · 机制完整性" % lex.REVIEW_1,
    "gate1": "%s · 代码忠实度" % lex.REVIEW_1,
    "l0": "%s · 开仓密度预检" % lex.REVIEW_1,
    "L0": "%s · 开仓密度预检" % lex.REVIEW_1,
    "L1": "%s · 单标的稳定性" % lex.REVIEW_2,
    "Gate2": "%s · 适应度门禁" % lex.REVIEW_3,
}

# Slot chrome labels
SLOT_STATE_LABELS = {
    "running": "运行中",
    "breakthrough": "演进突破",
    "archived": "已归档极限",
    "idle": "空闲",
    "queued": "排队中",
    "success": "待人工确认",
}


def _norm(code):
    return str(code or "").strip()


def humanize_code(code, fallback=None):
    """Translate a single enum / stop_code / reason to Chinese."""
    raw = _norm(code)
    if not raw:
        return fallback or ""
    if raw in HUMAN_TRANSLATION_MAP:
        return HUMAN_TRANSLATION_MAP[raw]
    up = raw.upper()
    if up in HUMAN_TRANSLATION_MAP:
        return HUMAN_TRANSLATION_MAP[up]
    low = raw.lower()
    if low in HUMAN_TRANSLATION_MAP:
        return HUMAN_TRANSLATION_MAP[low]
    # Prefer review lexicon for known pipeline reasons
    rev = lex.review_label_from_reason(raw)
    if rev and low in lex.STAGE_TO_REVIEW:
        return "%s相关" % rev
    # snake / slash compounds: "LIMIT_REACHED_FAILED / NO_PROGRESS_..."
    if "/" in raw:
        parts = [humanize_code(p.strip()) for p in raw.split("/") if p.strip()]
        parts = [p for p in parts if p]
        if parts:
            return "；".join(parts)
    # already Chinese?
    if re.search(r"[\u4e00-\u9fff]", raw):
        return raw
    # last resort: soft prettify without leaking raw SCREAMING_SNAKE as primary
    pretty = raw.replace("_", " ").strip()
    return fallback or pretty


def humanize_final(final_status=None, stop_code=None, success=False):
    if success or str(final_status or "").upper() == "SUCCESS":
        return HUMAN_TRANSLATION_MAP["SUCCESS"]
    bits = []
    for code in (final_status, stop_code):
        if not code:
            continue
        zh = humanize_code(code)
        if zh and zh not in bits:
            bits.append(zh)
    return "；".join(bits) if bits else "已结束"


def humanize_status_label(phase=None, reason=None, final_status=None, stop_code=None,
                          success=False, seed_idx=None, seed_max=None):
    if success or str(final_status or "").upper() == "SUCCESS":
        return HUMAN_TRANSLATION_MAP["SUCCESS"]
    if final_status or stop_code:
        return humanize_final(final_status, stop_code, success=False)
    label = humanize_code(phase) or humanize_code(reason) or "自主演进运行中"
    if seed_idx and seed_max and not final_status:
        return "%s 种子重试 %s/%s · %s" % (lex.REVIEW_2, seed_idx, seed_max, label)
    return label


def prefer_chinese_text(*candidates, **kwargs):
    """Pick the first candidate that contains CJK; else first non-empty."""
    fallback = kwargs.get("fallback", "机制因果说明待补充")
    max_len = int(kwargs.get("max_len", 160) or 160)
    nonempty = []
    for c in candidates:
        s = str(c or "").strip()
        if not s:
            continue
        nonempty.append(s)
        if re.search(r"[\u4e00-\u9fff]", s):
            cut = re.split(r"[。\n]", s)[0].strip() or s
            return cut[:max_len]
    if nonempty:
        cut = re.split(r"[.\n]", nonempty[0])[0].strip() or nonempty[0]
        return cut[:max_len]
    return str(fallback)[:max_len]


def slot_state_label(state):
    return SLOT_STATE_LABELS.get(str(state or "").lower(), humanize_code(state) or "未知")
