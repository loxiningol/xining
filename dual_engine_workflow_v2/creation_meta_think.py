# -*- coding: utf-8 -*-
"""Stage ① Meta-think — MetaGPT / AutoGen style multi-role design doc.

No MetaGPT/AutoGen install required. Four roles (PM / Architect / Risk / QA)
produce a structured strategy design document. Optional GLM enrichment when
skip_llm=False and AI keys are present.
"""
from __future__ import print_function

import json
import os
import re
from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# Forced divergence preamble — compensates for missing multi-agent debate.
# Must lead every GLM meta-think prompt; also applied in local heuristic path.
GLM_META_DIVERGENCE_INSTRUCTION = (
    "请先列举至少 5 种彼此正交的市场微观结构/参与者约束视角（不要只写3个同质变体），"
    "每条必须写清：收益支付者、被利用约束、可观测代理、预测方向、成立/失效条件、容量。"
    "禁止过早收敛为单一完整策略；禁止一上来直接写进出场规则。"
    "后续由研究发现系统用裸探针与反证实验淘汰，而不是靠文笔选最优故事。"
    "禁止把周收益≥8%当作硬交付目标；若人类要求不可实现，应标明不可行而非硬凑。"
    "年化净收益启发式估计可写 max_annual_net_estimate，但不得替代独立证据。"
)

# Default three orthogonal microstructure lenses (local path when LLM skipped)
_DEFAULT_PERSPECTIVES = (
    {
        "id": "P1_inventory_mean_reversion",
        "lens_zh": "做市商库存/均值回归",
        "thesis_zh": "价格偏离公允后由库存风险驱动回收，信号来自分位极端与回归",
        "family": "mean_reversion",
        "factor_hints": ["close_z_20", "dist_roll_low", "lower_wick_pct"],
    },
    {
        "id": "P2_liquidity_sweep_stop_hunt",
        "lens_zh": "流动性sweep / 止损猎杀",
        "thesis_zh": "假突破扫流动性后反向回收，信号来自滚动高低点外延与影线",
        "family": "liquidity_sweep",
        "factor_hints": ["dist_roll_high", "dist_roll_low", "upper_wick_pct", "lower_wick_pct"],
    },
    {
        "id": "P3_vol_regime_breakout",
        "lens_zh": "波动率状态切换 / 压缩突破",
        "thesis_zh": "低波压缩后的方向选择与扩张跟随，信号来自 range/ATR 状态",
        "family": "vol_squeeze_break",
        "factor_hints": ["range_pct", "atr_pct_14", "ret_12"],
    },
)


def probe():
    out = {
        "ok": True,
        "provider": "metagpt_autogen_lite_v1",
        "backends": {"metagpt": False, "autogen": False},
        "roles": ["product_manager", "architect", "risk_officer", "qa"],
        "notes": [
            "Local multi-role design doc; optional GLM enrichment.",
            "Meta-think always applies divergence: 3 microstructure lenses then deepen one.",
        ],
        "divergence_instruction": GLM_META_DIVERGENCE_INSTRUCTION,
        "probed_at": _now(),
    }
    for name, key in (("metagpt", "metagpt"), ("autogen", "autogen")):
        try:
            __import__(name)
            out["backends"][key] = True
        except Exception:
            pass
    return out


def _parse_constraints(brief):
    from . import creation_return_hardness as rh

    text = str(brief or "")
    out = {
        "max_ann_vol": None,
        "max_drawdown": None,
        "max_daily_loss": 0.05,
        "protective_sl": 0.009,
        "raw_hints": [],
    }
    m = re.search(r"波动[率]?[^0-9]{0,6}(\d+(?:\.\d+)?)\s*%", text)
    if m:
        out["max_ann_vol"] = float(m.group(1)) / 100.0
        out["raw_hints"].append("max_ann_vol_from_brief")
    m = re.search(r"回撤[^0-9]{0,6}(\d+(?:\.\d+)?)\s*%", text)
    if m:
        out["max_drawdown"] = float(m.group(1)) / 100.0
        out["raw_hints"].append("max_dd_from_brief")
    m = re.search(r"单日[^0-9]{0,8}(\d+(?:\.\d+)?)\s*%", text)
    if m:
        out["max_daily_loss"] = float(m.group(1)) / 100.0
    # Return hardness from brief (optional overrides)
    m = re.search(r"1\s*周[^0-9]{0,8}(\d+(?:\.\d+)?)\s*%", text)
    if m:
        out["minimum_weekly_return"] = float(m.group(1)) / 100.0
        out["raw_hints"].append("min_weekly_from_brief")
    m = re.search(r"周收益[^0-9]{0,8}(\d+(?:\.\d+)?)\s*%", text)
    if m and "minimum_weekly_return" not in out:
        out["minimum_weekly_return"] = float(m.group(1)) / 100.0
        out["raw_hints"].append("min_weekly_from_brief")
    if out["max_ann_vol"] is None:
        out["max_ann_vol"] = 0.35
    if out["max_drawdown"] is None:
        out["max_drawdown"] = 0.18
    # Always merge return-hardness floors (Improvement 1)
    out = rh.merge_constraints(out)
    return out


def _role_pm(brief, symbol, timeframe, direction, constraints):
    return {
        "role": "product_manager",
        "goal_zh": "把人类意图翻译成可验证的策略产品需求（必须赚钱，不只避险）",
        "user_intent": brief or "（未口述：在空白利基上找可复现边缘）",
        "target": {"symbol": symbol, "timeframe": timeframe, "direction": direction},
        "success_metrics": {
            "max_ann_vol": constraints["max_ann_vol"],
            "max_drawdown": constraints["max_drawdown"],
            "protective_sl": constraints["protective_sl"],
            "max_daily_loss_var": constraints["max_daily_loss"],
            "expected_annual_return_range": constraints.get("expected_annual_return_range"),
            "minimum_acceptable_annual_return": constraints.get("minimum_acceptable_annual_return"),
            "minimum_weekly_return": constraints.get("minimum_weekly_return"),
            "minimum_return_mdd": constraints.get("minimum_return_mdd"),
        },
        "non_goals": [
            "跳过假设验证直接挖因子",
            "用 LLM 口算夏普/Kelly",
            "绕过后续 ADA5 四复核",
            "用极低仓位刷低回撤/虚高夏普而收益归零",
        ],
    }


def _diverge_then_select(brief, symbol, timeframe):
    """Always list 3 distinct microstructure lenses, then pick one to deepen.

    Prompt-technique substitute for missing multi-agent debate.
    If the brief explicitly lists A/B/C creation menus, those become the
    three perspectives (preferred over generic defaults).
    """
    text = (brief or "")
    text_l = text.lower()

    # Explicit A/B/C menu from human creation orders
    has_abc = (
        ("动量突破" in text or "多时间框架" in text or "A." in text or "A、" in text or "逻辑方向" in text)
        and ("压缩" in text or "均值回归" in text or "B." in text or "B、" in text)
        and ("配对" in text or "协整" in text or "C." in text or "C、" in text)
    )
    if has_abc:
        perspectives = [
            {
                "id": "A_mtf_momentum_breakout",
                "lens_zh": "多时间框架动量突破（15m确认 + 1h定方向）",
                "thesis_zh": "高周期定方向，低周期动量/突破确认后顺势切入",
                "family": "trend_pullback",
                "factor_hints": ["ret_12", "ret_3", "dist_roll_high", "atr_pct_14"],
            },
            {
                "id": "B_vol_squeeze_mean_reversion",
                "lens_zh": "波动率压缩-扩张均值回归",
                "thesis_zh": "低波压缩后的扩张边缘或分位回归，ATR/range 状态切换",
                "family": "vol_squeeze_break",
                "factor_hints": ["range_pct", "atr_pct_14", "close_z_20"],
            },
            {
                "id": "C_pairs_cointegration",
                "lens_zh": "多标的配对（协整 + 价差回归）",
                "thesis_zh": "协整对价差偏离阈值后回归，需双标的与配对检验",
                "family": "pairs_cointegration",
                "factor_hints": ["close_z_20", "ret_12"],
            },
        ]
        # Prefer A when MTF keywords / BTC-like; B when squeeze; C when pairs
        selected_idx = 0
        if any(k in text for k in ("配对", "协整", "价差")) and "优先" not in text:
            selected_idx = 2
        if any(k in text for k in ("压缩-扩张", "压缩", "均值回归")) and "动量突破" not in text:
            selected_idx = 1
        if any(k in text for k in ("多时间框架", "动量突破", "15min", "15m", "1h定方向")):
            selected_idx = 0
        # If brief says 优先考虑以下逻辑之一 and lists A first with MTF available intent
        if "优先考虑以下逻辑之一" in text or "自主选择最优" in text:
            # Optimal default for single liquid MTF symbol: A
            selected_idx = 0
            if "配对" in text and "必须配对" in text:
                selected_idx = 2
        from . import creation_return_hardness as rh
        preferred = perspectives[selected_idx]["id"]
        chosen, perspectives = rh.select_perspective_by_return_capacity(
            perspectives, preferred_id=preferred,
        )
        return {
            "divergence_instruction": GLM_META_DIVERGENCE_INSTRUCTION,
            "perspectives": perspectives,
            "population_first": True,
            "early_pick_one": False,
            "seed_priority_id": chosen["id"],
            "selected_id": chosen["id"],  # legacy alias; discovery ignores as pick-1
            "selected_lens_zh": chosen["lens_zh"],
            "selection_reason_zh": (
                "人类指令含 A/B/C 菜单；仅作种子优先级（非整条链路三选一），"
                "研究发现以种群并行探针为准；优先 {id}（容量≈{cap:.0f}%）"
            ).format(
                id=chosen["id"],
                cap=100 * float(chosen.get("max_annual_net_estimate") or 0),
            ),
            "chosen": chosen,
        }

    # Explicit dual-direction recreation menu (mom×vol vs pairs)
    has_d12 = (
        ("波动率" in text or "布林" in text or "压缩" in text or "动量" in text)
        and ("价差" in text or "配对" in text or "统计套利" in text or "Z-score" in text or "Z分数" in text or "跨品种" in text)
    )
    if has_d12 and not has_abc:
        perspectives = [
            {
                "id": "D1_momentum_vol_dual_filter",
                "lens_zh": "动量与波动率双重过滤（有效趋势启动）",
                "thesis_zh": "波动率压缩后扩张且突破关键阻力时确认趋势；高波分位放弃追单",
                "family": "mom_vol_filter",
                "factor_hints": ["range_pct", "atr_pct_14", "ret_12", "dist_roll_high"],
            },
            {
                "id": "D2_pairs_mean_reversion",
                "lens_zh": "跨品种价差均值回归（统计套利）",
                "thesis_zh": "相关合约价差/比价 Z-score 偏离后多低估空高估，待回归",
                "family": "pairs_cointegration",
                "factor_hints": ["close_z_20", "ret_12"],
            },
            {
                "id": "D3_contrast_liquidity",
                "lens_zh": "对照：流动性sweep假突破",
                "thesis_zh": "作为发散第三视角，防止只在趋势/套利两极摇摆",
                "family": "liquidity_sweep",
                "factor_hints": ["dist_roll_low", "upper_wick_pct", "lower_wick_pct"],
            },
        ]
        selected_idx = 0  # default D1 seed priority; discovery probes population
        if "配对" in text and "放弃动量" in text:
            selected_idx = 1
        from . import creation_return_hardness as rh
        preferred = perspectives[selected_idx]["id"]
        chosen, perspectives = rh.select_perspective_by_return_capacity(
            perspectives, preferred_id=preferred,
        )
        return {
            "divergence_instruction": GLM_META_DIVERGENCE_INSTRUCTION,
            "perspectives": perspectives,
            "population_first": True,
            "early_pick_one": False,
            "seed_priority_id": chosen["id"],
            "selected_id": chosen["id"],
            "selected_lens_zh": chosen["lens_zh"],
            "selection_reason_zh": (
                "人类换方向菜单 D1/D2；仅种子优先级，研究发现种群并行；优先 {id}（容量≈{cap:.0f}%）"
            ).format(
                id=chosen["id"],
                cap=100 * float(chosen.get("max_annual_net_estimate") or 0),
            ),
            "chosen": chosen,
        }

    perspectives = [dict(p) for p in _DEFAULT_PERSPECTIVES]

    # Optional swap when brief clearly asks trend
    if any(k in text_l for k in ("突破", "breakout", "趋势", "momentum", "顺势", "pullback", "回撤切入")):
        perspectives[0] = {
            "id": "P1_trend_pullback",
            "lens_zh": "趋势回撤 / 惯性延续",
            "thesis_zh": "高周期方向确认后的低周期回撤切入，信号来自动量与回撤深度",
            "family": "trend_pullback",
            "factor_hints": ["ret_12", "ret_3", "close_z_20", "dist_roll_low"],
        }

    selected_idx = 0
    # Avoid treating 止损/硬性止损 as liquidity-sweep intent
    sweep_hit = any(k in text for k in ("扫荡", "liquidity", "流动性猎杀", "sfp", "止损猎杀", "假突破"))
    if sweep_hit:
        selected_idx = 1
    elif any(k in text for k in ("压缩", "squeeze", "波动扩张", "低波")):
        selected_idx = 2
    elif any(k in text_l for k in ("突破", "breakout", "趋势", "momentum", "顺势", "pullback")):
        selected_idx = 0

    from . import creation_return_hardness as rh
    preferred = perspectives[selected_idx]["id"]
    chosen, perspectives = rh.select_perspective_by_return_capacity(
        perspectives, preferred_id=preferred,
    )
    return {
        "divergence_instruction": GLM_META_DIVERGENCE_INSTRUCTION,
        "perspectives": perspectives,
        "population_first": True,
        "early_pick_one": False,
        "seed_priority_id": chosen["id"],
        "selected_id": chosen["id"],
        "selected_lens_zh": chosen["lens_zh"],
        "selection_reason_zh": (
            "关键词仅设定种子优先级；研究发现以机制/现象/符号种群并行探针，"
            "不作早期三选一；优先 {id}（容量≈{cap:.0f}%）"
        ).format(
            id=chosen["id"],
            cap=100 * float(chosen.get("max_annual_net_estimate") or 0),
        ),
        "chosen": chosen,
    }


def _role_architect(brief, symbol, timeframe, direction, divergence=None):
    divergence = divergence or _diverge_then_select(brief, symbol, timeframe)
    chosen = divergence["chosen"]
    family = chosen["family"]
    logic = chosen["thesis_zh"]
    hints = list(chosen.get("factor_hints") or [])
    return {
        "role": "architect",
        "mechanism_family": family,
        "core_logic_zh": logic,
        "entry_sketch_zh": "分位信号触发 + 方向过滤 + 保护性止损 %.1f%%" % 0.9,
        "exit_sketch_zh": "目标位 / 时间止盈 / 结构破坏离场",
        "divergence": {
            "instruction": GLM_META_DIVERGENCE_INSTRUCTION,
            "perspectives": divergence["perspectives"],
            "selected_id": divergence["selected_id"],
            "selected_lens_zh": divergence["selected_lens_zh"],
            "selection_reason_zh": divergence["selection_reason_zh"],
        },
        "core_hypotheses": [
            {
                "id": "H1",
                "statement_zh": "在「%s」视角下，目标品种该时框存在可复现边缘" % chosen["lens_zh"],
                "testable_factor_hints": hints or ["close_z_20", "ret_12", "range_pct"],
            },
            {
                "id": "H2",
                "statement_zh": "极端分位后的前瞻收益与对照区间存在显著均值差（非伪相关）",
                "testable_factor_hints": hints[:3] or ["close_z_20", "lower_wick_pct"],
            },
        ],
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
    }


def _role_risk(constraints):
    return {
        "role": "risk_officer",
        "hard_bounds": {
            "protective_sl_pct": constraints["protective_sl"] * 100.0,
            "max_ann_vol": constraints["max_ann_vol"],
            "max_drawdown": constraints["max_drawdown"],
            "max_daily_var": constraints["max_daily_loss"],
            "require_quantoracle": True,
            "minimum_weekly_return": constraints.get("minimum_weekly_return"),
            "minimum_return_mdd": constraints.get("minimum_return_mdd"),
            "minimum_acceptable_annual_return": constraints.get(
                "minimum_acceptable_annual_return"
            ),
            "expected_annual_return_range": constraints.get("expected_annual_return_range"),
        },
        "failure_scenarios_zh": [
            "政策/指数跳空导致止损缺口扩大",
            "流动性枯竭使滑点吞没理论边缘",
            "波动率状态切换使均值回归失效",
            "单边趋势日反复触发回归信号",
            "过度避险把仓位压没导致收益归零（策略退化）",
        ],
        "kill_switches_zh": [
            "QuantOracle VaR 超阈值立即否决",
            "Alphalens 换手过高/IC 衰减过快丢弃因子",
            "压力测试回撤超界后迭代不超过 5 次",
            "周收益代理<8% 或 收益/回撤<1.0 → 收益硬度熔断",
            "暴露<10% / 单笔<1bp / 窗内总收益<1% → 策略退化熔断并换视角",
            "因子多空周收益(带杠杆)<3% → 丢弃（即使 IC 显著）",
        ],
    }


def _role_qa():
    return {
        "role": "qa",
        "acceptance_checklist": [
            "设计文档含假设与失效条件",
            "设计文档含 expected_annual_return_range 与 minimum_acceptable_annual_return",
            "假设经 Alphalens/Causal 风格验证",
            "因子经 EasyQuant 挖掘 + QuantOracle 认证",
            "因子多空周收益(带杠杆)≥3%",
            "二次 Alphalens 筛选通过",
            "Backtrader 极端场景 + 红队攻击通过",
            "收益硬度：周收益代理≥8%、收益/回撤≥1.0",
            "无策略退化（暴露/单笔/窗内收益）",
            "初评胜率≥50%",
            "交付 strategy_code / params / risk_report 草稿",
            "不触及、不改写现有 ADA5 复核代码路径",
        ],
        "forbidden": [
            "宣称已过复核",
            "自动挂载实盘",
            "用通用模型估算风险数字",
            "用虚高夏普掩盖近零绝对收益",
        ],
    }


def glm_meta_think_system_prompt():
    """Canonical GLM meta-think prompt — divergence instruction MUST be first."""
    return (
        GLM_META_DIVERGENCE_INSTRUCTION
        + "\n\n"
        "你是策略总指挥（元思考）。完成三视角列举与择一深入后，再根据多角色设计草稿输出 JSON：\n"
        "{\n"
        "  \"perspectives\":[\n"
        "    {\"id\":\"P1\",\"lens_zh\":\"...\",\"thesis_zh\":\"...\",\"family\":\"...\","
        "\"max_annual_net_estimate\":0.12},\n"
        "    {\"id\":\"P2\",\"lens_zh\":\"...\",\"thesis_zh\":\"...\",\"family\":\"...\","
        "\"max_annual_net_estimate\":0.08},\n"
        "    {\"id\":\"P3\",\"lens_zh\":\"...\",\"thesis_zh\":\"...\",\"family\":\"...\","
        "\"max_annual_net_estimate\":0.15}\n"
        "  ],\n"
        "  \"selected_id\":\"P?\",\n"
        "  \"selection_reason_zh\":\"...\",\n"
        "  \"expected_annual_return_range\":[0.08,0.20],\n"
        "  \"minimum_acceptable_annual_return\":0.06,\n"
        "  \"refined_logic_zh\":\"...\",\n"
        "  \"refined_hypotheses\":[...],\n"
        "  \"counterparty_zh\":\"...\",\n"
        "  \"invalidation_zh\":\"...\"\n"
        "}\n"
        "三种视角必须来自不同微观结构机制（库存回归 / 流动性sweep / 波动状态切换 / 趋势回撤等），"
        "不得彼此只改参数。每个视角必须给出 max_annual_net_estimate。"
        "禁止编造夏普/胜率数字。禁止声称已过复核。"
        "禁止设计靠极低仓位刷低回撤、总收益近零的策略。"
    )


def _optional_glm_enrich(design_doc, skip_llm=True):
    if skip_llm:
        return {
            "enriched": False,
            "reason": "skip_llm",
            "divergence_instruction": GLM_META_DIVERGENCE_INSTRUCTION,
        }
    try:
        # Prefer existing pipeline helper without importing heavy review paths
        from dual_engine_workflow_v2.pipeline_step_a import _ai_json
        prompt = glm_meta_think_system_prompt()
        res = _ai_json(
            "glm",
            prompt,
            {
                "human_brief": (design_doc or {}).get("human_brief"),
                "design_doc": design_doc,
                "mandatory_divergence": GLM_META_DIVERGENCE_INSTRUCTION,
            },
            max_tokens=1200,
            temperature=0.35,
        )
        parsed = (res or {}).get("parsed") or (res or {}).get("json") or {}
        if not isinstance(parsed, dict):
            parsed = {}
        return {
            "enriched": bool(parsed),
            "glm": parsed,
            "raw_ok": bool(res),
            "divergence_instruction": GLM_META_DIVERGENCE_INSTRUCTION,
        }
    except Exception as exc:
        return {
            "enriched": False,
            "error": str(exc)[:200],
            "divergence_instruction": GLM_META_DIVERGENCE_INSTRUCTION,
        }


def run_meta_think(brief, symbol, timeframe, direction="long", skip_llm=True):
    constraints = _parse_constraints(brief)
    divergence = _diverge_then_select(brief, symbol, timeframe)
    roles = [
        _role_pm(brief, symbol, timeframe, direction, constraints),
        _role_architect(brief, symbol, timeframe, direction, divergence=divergence),
        _role_risk(constraints),
        _role_qa(),
    ]
    architect = roles[1]
    design = {
        "schema": "qiyu_strategy_design_doc_v1",
        "title_zh": "%s %s %s 策略设计文档" % (
            symbol.split("-")[0], timeframe, architect["mechanism_family"]
        ),
        "human_brief": brief,
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "divergence": {
            "instruction": GLM_META_DIVERGENCE_INSTRUCTION,
            "perspectives": divergence["perspectives"],
            "selected_id": divergence["selected_id"],
            "selected_lens_zh": divergence["selected_lens_zh"],
            "selection_reason_zh": divergence["selection_reason_zh"],
        },
        "mechanism_family": architect["mechanism_family"],
        "core_logic_zh": architect["core_logic_zh"],
        "hypotheses": architect["core_hypotheses"],
        "constraints": constraints,
        "expected_annual_return_range": constraints.get("expected_annual_return_range"),
        "minimum_acceptable_annual_return": constraints.get(
            "minimum_acceptable_annual_return"
        ),
        "failure_scenarios_zh": roles[2]["failure_scenarios_zh"],
        "invalidation_zh": (
            "；".join(roles[2]["failure_scenarios_zh"][:2])
            if roles[2].get("failure_scenarios_zh") else
            "信号在波动状态切换或流动性枯竭后失效"
        ),
        "risk_bounds": roles[2]["hard_bounds"],
        "roles": roles,
        "built_at": _now(),
        "tooling": probe(),
    }
    enrich = _optional_glm_enrich(design, skip_llm=skip_llm)
    design["glm_enrichment"] = enrich
    if enrich.get("enriched") and isinstance(enrich.get("glm"), dict):
        g = enrich["glm"]
        # Prefer GLM's own three-lens divergence when present
        if isinstance(g.get("perspectives"), list) and len(g["perspectives"]) >= 3:
            design["divergence"]["perspectives_glm"] = g["perspectives"][:3]
            if g.get("selected_id"):
                design["divergence"]["selected_id"] = g["selected_id"]
            if g.get("selection_reason_zh"):
                design["divergence"]["selection_reason_zh"] = g["selection_reason_zh"]
        if g.get("refined_logic_zh"):
            design["core_logic_zh"] = g["refined_logic_zh"]
        if g.get("refined_hypotheses"):
            design["hypotheses"] = g["refined_hypotheses"]
        if g.get("expected_annual_return_range"):
            design["expected_annual_return_range"] = g["expected_annual_return_range"]
            design["constraints"]["expected_annual_return_range"] = g[
                "expected_annual_return_range"
            ]
        if g.get("minimum_acceptable_annual_return") is not None:
            design["minimum_acceptable_annual_return"] = g[
                "minimum_acceptable_annual_return"
            ]
            design["constraints"]["minimum_acceptable_annual_return"] = g[
                "minimum_acceptable_annual_return"
            ]
        design["counterparty_zh"] = g.get("counterparty_zh")
        design["invalidation_zh"] = g.get("invalidation_zh")
    return {
        "ok": True,
        "schema": "qiyu_creation_stage1_meta_v1",
        "design_doc": design,
        "divergence_instruction": GLM_META_DIVERGENCE_INSTRUCTION,
        "at": _now(),
    }
