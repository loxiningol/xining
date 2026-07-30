# -*- coding: utf-8 -*-
"""HumanizerMiddleware — map raw driver enums to natural Chinese for UI."""
from __future__ import print_function

import re

HUMAN_TRANSLATION_MAP = {
    # Terminal outcomes
    "LIMIT_REACHED_FAILED": "触达当前机制演进极限（已自动归档）",
    "LIMIT_REACHED": "触达当前机制演进极限（已自动归档）",
    "NO_PROGRESS_REPEATED_REASON": "策略参数陷入局部收敛，连续多轮无性能突破",
    "SUCCESS": "完美穿透全关卡，策略待人工签发",
    "CONVERGED_NO_IMPROVEMENT": "统计指标已达帕累托前沿，终止微扰",
    "MAX_ITERATIONS": "已用尽本轮演进配额，自动收束",
    "AI_LIMIT_REACHED": "智能体判定已无优化空间，归档机制族",
    "AI_ABORT": "触发不可协商风控红线，已主动中止",
    "KB_BLOCKED_EXHAUSTED": "Failure KB 拦截次数耗尽，换族归档",
    "UNKNOWN_STOPPED": "进程已停止（状态未完整落盘）",
    "STOPPED": "已停止",
    # Pipeline reasons
    "funnel_l1_cull": "微观筛选过滤（样本量或期望不足）",
    "funnel_l1_fail": "微观筛选未过（样本或收益期望不足）",
    "insufficient_evidence": "统计证据不足（未达到显著性要求）",
    "kb_blocked": "触发 Failure KB 负面指纹拦截",
    "repair_exhausted_or_drift": "修补次数耗尽或出现逻辑漂移",
    "gate2_3_fail": "Gate2 适应度门禁未过",
    "exception": "执行异常，已安全回滚",
    # Mid-run phases
    "running": "自主演进运行中",
    "seed": "L1 种子重试中",
    "l1": "L1 微观筛选中",
    "calling_ai": "三方智能体协商补丁中",
    "ai": "三方智能体协商补丁中",
    "patch": "正在应用补丁并校验 DSL",
    "validate": "DSL 语法与忠实度校验中",
    "gate2": "正在计算 Gate2 平稳度",
    "success": "完美穿透全关卡，策略待人工签发",
    "idle": "卡槽空闲，等待新机制入队",
    "queued": "已入队，等待空闲卡槽",
    "archived": "已归档极限",
    "breakthrough": "演进突破，进入四维复核",
}

# Slot chrome labels
SLOT_STATE_LABELS = {
    "running": "运行中",
    "breakthrough": "演进突破",
    "archived": "已归档极限",
    "idle": "空闲",
    "queued": "排队中",
    "success": "待人工签发",
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
        return "L1 种子重试 %s/%s · %s" % (seed_idx, seed_max, label)
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
