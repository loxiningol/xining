# -*- coding: utf-8 -*-
"""HumanizerMiddleware — map raw driver enums to 三复核 Chinese for UI/AI."""
from __future__ import print_function

import re

from . import review_lexicon as lex

HUMAN_TRANSLATION_MAP = {
    # Terminal outcomes
    "LIMIT_REACHED_FAILED": "触达当前机制演进极限（已自动归档）",
    "LIMIT_REACHED": "触达当前机制演进极限（已自动归档）",
    "NO_PROGRESS_REPEATED_REASON": "策略参数陷入局部收敛，连续多轮无性能突破",
    "SUCCESS": "四复核（含三AI）均已通过，策略待%s" % lex.HUMAN_CONFIRM_GATE,
    "CONVERGED_NO_IMPROVEMENT": "统计指标已达帕累托前沿，终止微扰",
    "MAX_ITERATIONS": "已用尽本轮演进配额，自动收束",
    "MAX_AI_OPTIMIZE": "AI 优化已达上限（≤3 轮），策略失败归档",
    "AI_LIMIT_REACHED": "智能体判定已无优化空间，归档机制族",
    "AI_ABORT": "触发不可协商风控红线，已主动中止",
    "KB_BLOCKED_EXHAUSTED": "Failure KB 拦截次数耗尽，换族归档",
    "UNKNOWN_STOPPED": "进程已停止（状态未完整落盘）",
    "STOPPED": "已停止",
    "L0_SPARSE_CLEAN_PACK": "%s：清洁包密度过稀（拒绝放宽入场雕花）" % lex.REVIEW_1,
    "RESET_REQUIRED": "%s：契约/逻辑断言失败，需重译（禁止加法雕花）" % lex.REVIEW_1,
    # Pipeline reasons
    "funnel_l0_cull": "%s未过：开仓密度过稀（拒绝全量回测）" % lex.REVIEW_1,
    "funnel_l0_fail": "%s未过：开仓密度不足 / 逻辑过稀" % lex.REVIEW_1,
    "funnel_l1_cull": "%s未过：样本量或收益期望不足" % lex.REVIEW_2,
    "funnel_l1_fail": "%s未过：单标的历史回测稳定性不足" % lex.REVIEW_2,
    "insufficient_evidence": "统计证据不足（未达到显著性要求）",
    "kb_blocked": "触发 Failure KB 负面指纹拦截",
    "repair_exhausted_or_drift": "%s未过：修补耗尽或逻辑漂移" % lex.REVIEW_3,
    "gate2_3_fail": "%s未过：矩阵验证 / 抗离群" % lex.REVIEW_3,
    "review2_evidence_fail": "%s未过：样本收益稳定性不足" % lex.REVIEW_2,
    "pretest_quality_fail": "%s未过：逻辑断言 / 契约失败" % lex.REVIEW_1,
    "exception": "执行异常，已安全回滚",
    # Mid-run phases
    "running": "自主演进运行中",
    "seed": "%s · 种子重试中" % lex.REVIEW_2,
    "l1": "%s进行中" % lex.REVIEW_2,
    "calling_ai": "三方智能体协商补丁中",
    "ai": "三方智能体协商补丁中",
    "patch": "正在应用补丁并校验 DSL",
    "validate": "%s · 语法与忠实度校验中" % lex.REVIEW_1,
    "gate2": "%s进行中" % lex.REVIEW_3,
    "success": "四复核（含三AI）均已通过，策略待%s" % lex.HUMAN_CONFIRM_GATE,
    "idle": "卡槽空闲，等待新机制入队",
    "queued": "已入队，等待空闲卡槽",
    "archived": "已归档极限",
    "breakthrough": "演进突破，进入%s" % lex.REVIEW_3,
    "gate0": lex.pipe_label("gate0"),
    "gate1": lex.pipe_label("gate1"),
    "l0": lex.pipe_label("l0"),
}

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
    """Translate a single enum / stop_code / reason to Chinese (无 Gate/L 泄漏)."""
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
    rev = lex.review_label_from_reason(raw)
    if rev and low in lex.STAGE_TO_REVIEW:
        scope = lex.review_scope(lex.review_n_from_reason(raw)) or ""
        return "%s（%s）" % (rev, scope) if scope else rev
    if "/" in raw:
        parts = [humanize_code(p.strip()) for p in raw.split("/") if p.strip()]
        parts = [p for p in parts if p]
        if parts:
            return "；".join(parts)
    if re.search(r"[\u4e00-\u9fff]", raw):
        return lex.scrub(raw)
    # scrub legacy tokens before soft prettify
    scrubbed = lex.scrub(raw)
    if scrubbed != raw:
        return scrubbed
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
    return lex.scrub(label)


def prefer_chinese_text(*candidates, **kwargs):
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
            return lex.scrub(cut)[:max_len]
    if nonempty:
        cut = re.split(r"[.\n]", nonempty[0])[0].strip() or nonempty[0]
        return lex.scrub(cut)[:max_len]
    return str(fallback)[:max_len]


def slot_state_label(state):
    return SLOT_STATE_LABELS.get(str(state or "").lower(), humanize_code(state) or "未知")
