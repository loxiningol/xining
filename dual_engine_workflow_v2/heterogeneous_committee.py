# -*- coding: utf-8 -*-
"""Heterogeneous cognitive committee — independent submissions, no chat loop.

Roles (forced separation):
  mechanism_scientist  — mechanism graph only; FORBIDDEN to inspect returns
  empirical_scientist  — phenomenon scanner only; FORBIDDEN to write trade rules
  symbolic_searcher    — non-LLM GP expressions; FORBIDDEN to write stories
  antifalsify_auditor  — negative controls / placebo / competitors (stats)
  constructive_redteam — build strongest opposing-family naked probe
  statistician         — DSR/PBO/trial budget (non-LLM judge inputs)
  research_director    — allocates budget only (no strategies)
  judge                — evidence-field scoring only; Kimi optional when enabled

LLM enrichment is optional and MUST NOT replace non-LLM cores.
"""
from __future__ import print_function

import os
from datetime import datetime

from . import antifalsify as af
from . import mechanism_graph as mgraph
from . import multiple_testing as mtest
from . import phenomenon_scanner as phscan
from . import probe_protocol as probes
from . import research_blackboard as board
from . import research_ledger as ledger
from . import symbolic_searcher as sym


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


ROLE_CONTRACTS = {
    "research_director": {
        "model_pref": "glm",
        "may_propose_strategy": False,
        "task_zh": "只分配试验预算与阶段门槛",
    },
    "mechanism_scientist": {
        "model_pref": "glm",
        "may_see_returns": False,
        "task_zh": "从参与者约束推导收益来源",
        "forbids_zh": "禁止先看回测收益",
    },
    "empirical_scientist": {
        "model_pref": "qwen",
        "may_write_rules": False,
        "task_zh": "扫描条件分布异常",
        "forbids_zh": "禁止编写交易规则",
    },
    "symbolic_searcher": {
        "model_pref": "non_llm",
        "task_zh": "符号/遗传搜索表达式",
        "forbids_zh": "禁止写自然语言故事",
    },
    "antifalsify_auditor": {
        "model_pref": "non_llm_stats",
        "task_zh": "负对照/安慰剂/竞争解释",
        "forbids_zh": "禁止帮助优化策略",
    },
    "constructive_redteam": {
        "model_pref": "qwen_or_non_llm",
        "task_zh": "构造最强反方探针并比较",
        "forbids_zh": "禁止给主假设找借口",
    },
    "statistician": {
        "model_pref": "non_llm",
        "task_zh": "多重检验与有效试验次数",
        "forbids_zh": "不应由LLM裁决",
    },
    "judge": {
        "model_pref": "kimi_optional_else_rules",
        "may_propose_strategy": False,
        "task_zh": "只基于证据字段分配资源/放行",
    },
}


OPPOSING_FAMILY = {
    "mean_reversion": "vol_squeeze_break",
    "vol_squeeze_break": "mean_reversion",
    "liquidity_sweep": "vol_squeeze_break",
    "trend_pullback": "mean_reversion",
    "crowding_fade": "vol_squeeze_break",
    "liquidation_bounce": "vol_squeeze_break",
    "data_driven": "mean_reversion",
    "symbolic": "mean_reversion",
}


def research_director_budget(max_trial_budget=200):
    return {
        "role": "research_director",
        "max_trial_budget": int(max_trial_budget),
        "stage_gates": [
            "mechanism_complete",
            "naked_probe",
            "antifalsify_matrix",
            "efr",
            "multiple_testing",
        ],
        "may_propose_strategy": False,
        "at": _now(),
    }


def run_mechanism_scientist(brief, symbol, timeframe, run_id, limit=12):
    """Independent: mechanisms only. Does not receive fwd returns."""
    pack = mgraph.select_for_brief(brief, symbol=symbol, timeframe=timeframe, limit=limit)
    hyps = [mgraph.mechanism_to_hypothesis(m) for m in (pack.get("mechanisms") or [])]
    board.write(run_id, "mechanism_scientist", "hypothesis_population", {
        "n": len(hyps),
        "mechanism_ids": [h.get("mechanism_id") for h in hyps],
        "forbids_zh": ROLE_CONTRACTS["mechanism_scientist"]["forbids_zh"],
        "saw_returns": False,
    })
    for h in hyps:
        ledger.append_event({
            "event_type": "hypothesis_mechanism",
            "hypothesis_id": h.get("hypothesis_id"),
            "role": "mechanism_scientist",
        }, run_id=run_id)
    return {
        "role": "mechanism_scientist",
        "ok": True,
        "hypotheses": hyps,
        "n": len(hyps),
        "saw_returns": False,
        "at": _now(),
    }


def run_empirical_scientist(factor_matrix, fwd_returns, run_id, max_phenomena=24):
    """Independent: phenomena only. No trade rules."""
    scanned = phscan.scan_all(factor_matrix, fwd_returns, max_phenomena=max_phenomena)
    hyps = [
        phscan.phenomenon_to_hypothesis(ph, rank=i)
        for i, ph in enumerate(scanned.get("phenomena") or [])
    ]
    board.write(run_id, "empirical_scientist", "phenomenon_population", {
        "n": len(hyps),
        "phenomenon_ids": [h.get("hypothesis_id") for h in hyps],
        "forbids_zh": ROLE_CONTRACTS["empirical_scientist"]["forbids_zh"],
        "wrote_trade_rules": False,
    })
    for h in hyps:
        ledger.append_event({
            "event_type": "hypothesis_empirical",
            "hypothesis_id": h.get("hypothesis_id"),
            "role": "empirical_scientist",
        }, run_id=run_id)
    return {
        "role": "empirical_scientist",
        "ok": True,
        "hypotheses": hyps,
        "phenomena": scanned,
        "n": len(hyps),
        "wrote_trade_rules": False,
        "at": _now(),
    }


def run_symbolic_searcher(factor_matrix, fwd_returns, run_id):
    pack = sym.search(factor_matrix, fwd_returns)
    hyps = sym.expressions_to_hypotheses(pack, factor_matrix=factor_matrix)
    # inject synthetic series into caller's matrix for later probes
    for h in hyps:
        syn = h.get("synthetic_factor") or {}
        name = syn.get("name")
        series = syn.get("series")
        if name and series is not None:
            factor_matrix[name] = series
    board.write(run_id, "symbolic_searcher", "expression_population", {
        "n": len(hyps),
        "n_evaluated": pack.get("n_evaluated"),
        "top_keys": [h.get("observable_proxy") for h in hyps[:5]],
        "llm": False,
    })
    for h in hyps:
        ledger.append_event({
            "event_type": "hypothesis_symbolic",
            "hypothesis_id": h.get("hypothesis_id"),
            "role": "symbolic_searcher",
            "ic": h.get("symbolic_ic"),
        }, run_id=run_id)
    return {
        "role": "symbolic_searcher",
        "ok": bool(pack.get("ok")),
        "hypotheses": hyps,
        "search": {k: v for k, v in pack.items() if k != "expressions"},
        "n": len(hyps),
        "llm": False,
        "at": _now(),
    }


def run_constructive_redteam(main_hyp, factor_matrix, fwd_returns, run_id):
    """Build opposing-family naked probe; compare mean_net vs main."""
    fam = (main_hyp or {}).get("family") or "data_driven"
    opp_fam = OPPOSING_FAMILY.get(fam, "mean_reversion")
    # pick opposing mechanism seed factors
    seeds = mgraph.load_graph()
    opp = None
    for m in seeds:
        if m.get("family") == opp_fam:
            opp = mgraph.mechanism_to_hypothesis(m)
            break
    if opp is None:
        opp = {
            "hypothesis_id": "H_redteam_opp",
            "family": opp_fam,
            "factor_hints": ["close_z_20", "ret_12", "range_pct"],
            "source": "constructive_redteam",
        }
    opp["source"] = "constructive_redteam"
    opp_probe = probes.probe_hypothesis(opp, factor_matrix, fwd_returns)
    main_probe = probes.probe_hypothesis(main_hyp, factor_matrix, fwd_returns)
    main_net = float(((main_probe.get("best") or {}).get("mean_net") or -1e9))
    opp_net = float(((opp_probe.get("best") or {}).get("mean_net") or -1e9))
    main_beats = bool(main_probe.get("passed") and main_net > opp_net)
    board.write(run_id, "constructive_redteam", "opponent_probe", {
        "main_hypothesis_id": (main_hyp or {}).get("hypothesis_id"),
        "opp_family": opp_fam,
        "main_net": main_net,
        "opp_net": opp_net,
        "main_beats_opponent": main_beats,
    })
    ledger.append_event({
        "event_type": "redteam_constructive",
        "main": (main_hyp or {}).get("hypothesis_id"),
        "opp_family": opp_fam,
        "main_beats_opponent": main_beats,
    }, run_id=run_id)
    return {
        "role": "constructive_redteam",
        "ok": True,
        "passed": main_beats,
        "main_net": main_net,
        "opp_net": opp_net,
        "opp_family": opp_fam,
        "opp_probe_passed": opp_probe.get("passed"),
        "note_zh": (
            "主假设裸优势结构性高于反方" if main_beats else
            "反方探针不弱于主假设：不得仅因语言自洽放行"
        ),
        "at": _now(),
    }


def judge_from_evidence(evidence_fields, run_id=None):
    """Rule judge on evidence fields only. Optional Kimi comment if enabled."""
    required = ["naked_probe_passed", "antifalsify_passed", "efr_passed"]
    missing = [k for k in required if not evidence_fields.get(k)]
    score = 0.0
    if evidence_fields.get("naked_probe_passed"):
        score += 2.0
    if evidence_fields.get("antifalsify_passed"):
        score += 2.0
    if evidence_fields.get("efr_passed"):
        score += 1.5
    if evidence_fields.get("redteam_passed"):
        score += 1.0
    if evidence_fields.get("dsr_passed"):
        score += 1.5
    if evidence_fields.get("bidirectional_hit"):
        score += 1.0
    admit = bool(not missing and score >= 5.5)
    kimi_note = None
    # Kimi judge slot — only if explicitly enabled; never invent strategies
    if str(os.environ.get("QIYU_KIMI_ENABLED") or "0").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        kimi_note = {
            "attempted": True,
            "enabled": True,
            "role": "judge_only",
            "note_zh": "Kimi 仅可就证据字段发表资源分配意见；本次默认仍以规则裁判为准（防相关偏差）。",
        }
    else:
        kimi_note = {
            "attempted": False,
            "enabled": False,
            "note_zh": "Kimi Judge 未启用（QIYU_KIMI_ENABLED=0）；使用非LLM规则裁判。",
        }
    out = {
        "role": "judge",
        "admit_to_assembly": admit,
        "score": score,
        "missing": missing,
        "evidence_fields": evidence_fields,
        "kimi": kimi_note,
        "may_propose_strategy": False,
        "at": _now(),
    }
    if run_id:
        board.write(run_id, "judge", "admission", out)
    return out


def merge_independent_hypotheses(mech_pack, emp_pack, sym_pack):
    """Merge without early pick-1; mark bidirectional intersections."""
    hyps = []
    hyps.extend(mech_pack.get("hypotheses") or [])
    hyps.extend(emp_pack.get("hypotheses") or [])
    hyps.extend(sym_pack.get("hypotheses") or [])
    mech_factors = set()
    for h in mech_pack.get("hypotheses") or []:
        for f in (h.get("factor_hints") or h.get("observable_proxy") or []):
            mech_factors.add(f)
    for h in hyps:
        hints = h.get("factor_hints") or h.get("observable_proxy") or []
        if h.get("path") == "data_to_theory" and any(f in mech_factors for f in hints):
            h["bidirectional_hit"] = True
            h["priority_boost"] = 2.0
        else:
            h.setdefault("bidirectional_hit", False)
            h.setdefault("priority_boost", 0.0)
    # symbolic with high |IC| boost slightly but still need mechanism link later
    for h in hyps:
        if h.get("source") == "symbolic_searcher":
            h["priority_boost"] = float(h.get("priority_boost") or 0) + min(
                1.5, abs(float(h.get("symbolic_ic") or 0)) * 10.0
            )
    hyps.sort(
        key=lambda h: (
            1 if h.get("bidirectional_hit") else 0,
            float(h.get("priority_boost") or 0),
            abs(float(((h.get("phenomenon") or {}).get("t_stat") or 0))),
            abs(float(h.get("symbolic_ic") or 0)),
        ),
        reverse=True,
    )
    return hyps


def probe():
    return {
        "ok": True,
        "provider": "heterogeneous_committee_v1",
        "roles": list(ROLE_CONTRACTS.keys()),
        "contracts": ROLE_CONTRACTS,
        "at": _now(),
    }
