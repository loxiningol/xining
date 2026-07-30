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


def probe():
    out = {
        "ok": True,
        "provider": "metagpt_autogen_lite_v1",
        "backends": {"metagpt": False, "autogen": False},
        "roles": ["product_manager", "architect", "risk_officer", "qa"],
        "notes": ["Local multi-role design doc; optional GLM enrichment."],
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
    if out["max_ann_vol"] is None:
        out["max_ann_vol"] = 0.35
    if out["max_drawdown"] is None:
        out["max_drawdown"] = 0.18
    return out


def _role_pm(brief, symbol, timeframe, direction, constraints):
    return {
        "role": "product_manager",
        "goal_zh": "把人类意图翻译成可验证的策略产品需求",
        "user_intent": brief or "（未口述：在空白利基上找可复现边缘）",
        "target": {"symbol": symbol, "timeframe": timeframe, "direction": direction},
        "success_metrics": {
            "max_ann_vol": constraints["max_ann_vol"],
            "max_drawdown": constraints["max_drawdown"],
            "protective_sl": constraints["protective_sl"],
            "max_daily_loss_var": constraints["max_daily_loss"],
        },
        "non_goals": [
            "跳过假设验证直接挖因子",
            "用 LLM 口算夏普/Kelly",
            "绕过后续 ADA5 四复核",
        ],
    }


def _role_architect(brief, symbol, timeframe, direction):
    # Heuristic mechanism family from brief keywords
    text = (brief or "").lower()
    family = "mean_reversion"
    logic = "价格相对均线或分位极端后的均值回收"
    if any(k in text for k in ("突破", "breakout", "趋势", "momentum", "顺势")):
        family = "trend_pullback"
        logic = "高周期方向确认后的低周期回撤切入"
    elif any(k in text for k in ("压缩", "squeeze", "波动扩张", "波动率")):
        family = "vol_squeeze_break"
        logic = "低波压缩后的方向选择与扩张跟随"
    elif any(k in text for k in ("扫荡", "liquidity", "流动性", "sfp")):
        family = "liquidity_sweep"
        logic = "假突破扫流动性后的反向回收"
    return {
        "role": "architect",
        "mechanism_family": family,
        "core_logic_zh": logic,
        "entry_sketch_zh": "分位信号触发 + 方向过滤 + 保护性止损 %.1f%%" % 0.9,
        "exit_sketch_zh": "目标位 / 时间止盈 / 结构破坏离场",
        "core_hypotheses": [
            {
                "id": "H1",
                "statement_zh": "目标品种在该时框存在可复现的分位回归或动量延续边缘",
                "testable_factor_hints": ["close_z_20", "ret_12", "dist_roll_low", "range_pct"],
            },
            {
                "id": "H2",
                "statement_zh": "极端分位后的前瞻收益与对照区间存在显著均值差（非伪相关）",
                "testable_factor_hints": ["close_z_20", "lower_wick_pct", "upper_wick_pct"],
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
        },
        "failure_scenarios_zh": [
            "政策/指数跳空导致止损缺口扩大",
            "流动性枯竭使滑点吞没理论边缘",
            "波动率状态切换使均值回归失效",
            "单边趋势日反复触发回归信号",
        ],
        "kill_switches_zh": [
            "QuantOracle VaR 超阈值立即否决",
            "Alphalens 换手过高/IC 衰减过快丢弃因子",
            "压力测试回撤超界后迭代不超过 5 次",
        ],
    }


def _role_qa():
    return {
        "role": "qa",
        "acceptance_checklist": [
            "设计文档含假设与失效条件",
            "假设经 Alphalens/Causal 风格验证",
            "因子经 EasyQuant 挖掘 + QuantOracle 认证",
            "二次 Alphalens 筛选通过",
            "Backtrader 极端场景 + 红队攻击通过",
            "交付 strategy_code / params / risk_report 草稿",
            "不触及、不改写现有 ADA5 复核代码路径",
        ],
        "forbidden": [
            "宣称已过复核",
            "自动挂载实盘",
            "用通用模型估算风险数字",
        ],
    }


def _optional_glm_enrich(design_doc, skip_llm=True):
    if skip_llm:
        return {"enriched": False, "reason": "skip_llm"}
    try:
        # Prefer existing pipeline helper without importing heavy review paths
        from dual_engine_workflow_v2.pipeline_step_a import _ai_json
        prompt = (
            "你是策略总指挥。根据下列多角色设计草稿，输出 JSON："
            "{\"refined_logic_zh\":\"...\",\"refined_hypotheses\":[...],"
            "\"counterparty_zh\":\"...\",\"invalidation_zh\":\"...\"}。"
            "禁止编造夏普/胜率数字。"
        )
        res = _ai_json(
            "glm",
            prompt,
            {"design_doc": design_doc},
            max_tokens=900,
            temperature=0.2,
        )
        parsed = (res or {}).get("parsed") or (res or {}).get("json") or {}
        if not isinstance(parsed, dict):
            parsed = {}
        return {"enriched": bool(parsed), "glm": parsed, "raw_ok": bool(res)}
    except Exception as exc:
        return {"enriched": False, "error": str(exc)[:200]}


def run_meta_think(brief, symbol, timeframe, direction="long", skip_llm=True):
    constraints = _parse_constraints(brief)
    roles = [
        _role_pm(brief, symbol, timeframe, direction, constraints),
        _role_architect(brief, symbol, timeframe, direction),
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
        "mechanism_family": architect["mechanism_family"],
        "core_logic_zh": architect["core_logic_zh"],
        "hypotheses": architect["core_hypotheses"],
        "constraints": constraints,
        "failure_scenarios_zh": roles[2]["failure_scenarios_zh"],
        "risk_bounds": roles[2]["hard_bounds"],
        "roles": roles,
        "built_at": _now(),
        "tooling": probe(),
    }
    enrich = _optional_glm_enrich(design, skip_llm=skip_llm)
    design["glm_enrichment"] = enrich
    if enrich.get("enriched") and isinstance(enrich.get("glm"), dict):
        g = enrich["glm"]
        if g.get("refined_logic_zh"):
            design["core_logic_zh"] = g["refined_logic_zh"]
        if g.get("refined_hypotheses"):
            design["hypotheses"] = g["refined_hypotheses"]
        design["counterparty_zh"] = g.get("counterparty_zh")
        design["invalidation_zh"] = g.get("invalidation_zh")
    return {
        "ok": True,
        "schema": "qiyu_creation_stage1_meta_v1",
        "design_doc": design,
        "at": _now(),
    }
