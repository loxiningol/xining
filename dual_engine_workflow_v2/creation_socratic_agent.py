# -*- coding: utf-8 -*-
"""AutoGen-style Socratic challenger for creation design docs.

Does not participate in design — only attacks every logic link with
why / what-if / edge-already-arbitraged questions. Local heuristic judge
always runs; optional GLM enrichment when skip_llm=False.
"""
from __future__ import print_function

from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def probe():
    backends = {"autogen": False}
    try:
        __import__("autogen")
        backends["autogen"] = True
    except Exception:
        pass
    return {
        "ok": True,
        "provider": "socratic_challenger_lite_v1",
        "backends": backends,
        "role": "questioner_only_not_designer",
        "at": _now(),
    }


_QUESTION_TEMPLATES = (
    {
        "id": "Q_why_mechanism",
        "template_zh": "为什么「{lens}」在 {symbol} {tf} 上应产生可交易边缘，而不是噪声？",
        "severity": "mechanism",
    },
    {
        "id": "Q_premise_gone",
        "template_zh": "若前提「{thesis}」不成立（或只在 20% 时段成立），策略会怎样失效？失效条件写清楚了吗？",
        "severity": "premise",
    },
    {
        "id": "Q_arb_eaten",
        "template_zh": "该收益来源是否已被做市商/高频/资金费率套利吃光？剩余边缘扣除双边成本后还够硬吗？",
        "severity": "competition",
    },
    {
        "id": "Q_regime_shift",
        "template_zh": "波动状态切换或流动性枯竭时，{family} 类逻辑会不会系统性翻车？有无环境过滤？",
        "severity": "regime",
    },
    {
        "id": "Q_groupthink",
        "template_zh": "PM/架构/风控/QA 是否在互相打圆场？有没有独立证据否定这个假设？",
        "severity": "groupthink",
    },
)


def _answer_heuristic(qid, design, cards=None):
    """Produce a local answer + pass/fail without LLM."""
    family = (design.get("mechanism_family") or "").lower()
    logic = design.get("core_logic_zh") or ""
    constraints = design.get("constraints") or {}
    cards = cards or []
    invalidation = design.get("invalidation_zh") or ""
    failure = design.get("failure_scenarios_zh") or []

    if qid == "Q_why_mechanism":
        ok = bool(logic) and len(logic) >= 12 and bool(family)
        return ok, (
            "机制族=%s；核心逻辑已陈述。" % family if ok else
            "机制/逻辑陈述不足，疑似口号。"
        )
    if qid == "Q_premise_gone":
        ok = bool(invalidation) or bool(failure)
        return ok, (
            "已给出失效条件/失败场景。" if ok else
            "缺少明确失效条件，前提消失后无法判定。"
        )
    if qid == "Q_arb_eaten":
        # require return hardness floors present as cost-awareness proxy
        ok = constraints.get("minimum_weekly_return") is not None and (
            float(constraints.get("minimum_factor_weekly_lev") or 0) >= 0.03
        )
        return ok, (
            "设计约束含周收益/因子多空收益硬度，默认计入成本竞争。" if ok else
            "未体现成本后剩余边缘约束，可能被套利吃光。"
        )
    if qid == "Q_regime_shift":
        ok = any(
            k in (logic + family + str(failure))
            for k in ("波动", "压缩", "regime", "过滤", "高波", "状态")
        ) or any("波动" in str(x) or "流动性" in str(x) for x in failure)
        return ok, (
            "逻辑或失败场景覆盖波动/流动性状态切换。" if ok else
            "未见环境过滤/状态切换应对，易在 regime 翻转时翻车。"
        )
    if qid == "Q_groupthink":
        # pass if knowledge cards consulted or divergence has >=3 lenses
        div = design.get("divergence") or {}
        n_pers = len(div.get("perspectives") or [])
        ok = n_pers >= 3 or bool(cards)
        return ok, (
            "已强制三视角发散%s。" % ("并对照外部认知卡片" if cards else "")
            if ok else
            "视角不足，疑似群体思维。"
        )
    return False, "未知质询"


def challenge_design(design_doc, knowledge_cards=None, skip_llm=True):
    """Run Socratic battery; fail if any critical question unanswered."""
    design = design_doc or {}
    cards = knowledge_cards or []
    lens = ((design.get("divergence") or {}).get("selected_lens_zh")
            or design.get("mechanism_family") or "未命名视角")
    thesis = design.get("core_logic_zh") or ""
    family = design.get("mechanism_family") or ""
    symbol = design.get("symbol") or "?"
    tf = design.get("timeframe") or "?"

    qa = []
    n_fail = 0
    for t in _QUESTION_TEMPLATES:
        q = t["template_zh"].format(
            lens=lens, thesis=thesis[:80], family=family,
            symbol=symbol, tf=tf,
        )
        ok, ans = _answer_heuristic(t["id"], design, cards=cards)
        if not ok:
            n_fail += 1
        qa.append({
            "id": t["id"],
            "severity": t["severity"],
            "question_zh": q,
            "answer_zh": ans,
            "passed": ok,
        })

    glm_pack = {"enriched": False, "reason": "skip_llm" if skip_llm else "unused"}
    # Optional GLM deepening kept best-effort; local judge is authoritative on VPS
    if not skip_llm:
        try:
            from dual_engine_workflow_v2.pipeline_step_a import _ai_json
            res = _ai_json(
                "glm",
                "你是苏格拉底式质疑者，不参与设计，只追问。对每个问题给出是否过关(pass)与一句话反驳/补强。输出 JSON："
                "{\"answers\":[{\"id\":\"...\",\"pass\":true,\"answer_zh\":\"...\"}]}",
                {"questions": qa, "design_doc": {
                    "mechanism_family": family,
                    "core_logic_zh": thesis,
                    "invalidation_zh": design.get("invalidation_zh"),
                    "failure_scenarios_zh": design.get("failure_scenarios_zh"),
                }},
                max_tokens=900,
                temperature=0.2,
            )
            parsed = (res or {}).get("parsed") or {}
            glm_pack = {"enriched": bool(parsed), "glm": parsed}
            for a in (parsed.get("answers") or []):
                for row in qa:
                    if row["id"] == a.get("id") and a.get("pass") is False:
                        if row["passed"]:
                            row["passed"] = False
                            row["answer_zh"] = (row["answer_zh"] + " | GLM否决: "
                                                + str(a.get("answer_zh") or ""))[:240]
                            n_fail += 1
        except Exception as exc:
            glm_pack = {"enriched": False, "error": str(exc)[:160]}

    passed = n_fail == 0
    return {
        "ok": True,
        "schema": "qiyu_socratic_challenger_v1",
        "passed": passed,
        "n_questions": len(qa),
        "n_failed": n_fail,
        "qa": qa,
        "glm": glm_pack,
        "probe": probe(),
        "at": _now(),
        "human_banner_zh": (
            None if passed else
            "【苏格拉底质询未通过】有 %d 个逻辑连接点无法合理解释，打回元思考。"
            % n_fail
        ),
    }
