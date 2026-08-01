# -*- coding: utf-8 -*-
"""Heterogeneous cognitive committee — independent submissions, no chat loop.

Roles (forced separation):
  mechanism_scientist  — mechanism graph only; FORBIDDEN to inspect returns
  empirical_scientist  — phenomenon scanner only; FORBIDDEN to write trade rules
  symbolic_searcher    — non-LLM GP expressions; FORBIDDEN to write stories
  antifalsify_auditor  — negative controls / placebo / competitors (stats)
  constructive_redteam — build strongest opposing-family naked probe
  leakage_auditor      — look-ahead / timestamp / label leakage checks
  causal_auditor       — CausalImpact-lite hygiene; FORBIDDEN to claim proof
  execution_engineer   — cost / slippage / capacity feasibility (not strategy invent)
  statistician         — DSR/PBO/trial budget (non-LLM judge inputs)
  research_director    — allocates budget only (no strategies)
  judge                — deterministic evidence gate + Kimi independent veto

LLM enrichment is optional and MUST NOT replace non-LLM cores.
Multi-vendor live LLM roundtable is NOT required; blackboard is structured fields.
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
    "leakage_auditor": {
        "model_pref": "non_llm",
        "task_zh": "检查前视/标签/时间戳泄漏气味",
        "forbids_zh": "禁止优化入场以掩盖泄漏",
    },
    "causal_auditor": {
        "model_pref": "non_llm_stats",
        "task_zh": "复核因果主张边界；只做卫生检查",
        "forbids_zh": "禁止把相关/干预代理说成因果证明",
    },
    "execution_engineer": {
        "model_pref": "non_llm",
        "may_propose_strategy": False,
        "task_zh": "成本/滑点/容量可行性",
        "forbids_zh": "禁止发明新交易逻辑",
    },
    "statistician": {
        "model_pref": "non_llm",
        "task_zh": "多重检验与有效试验次数",
        "forbids_zh": "不应由LLM裁决",
    },
    "judge": {
        "model_pref": "deterministic_rules_then_kimi_independent_veto",
        "may_propose_strategy": False,
        "task_zh": "本地硬门槛通过后，由Kimi独立裁决是否允许进入策略组装",
    },
}


OPPOSING_FAMILY = {
    "mean_reversion": "vol_squeeze_break",
    "vol_squeeze_break": "mean_reversion",
    "liquidity_sweep": "vol_squeeze_break",
    "trend_pullback": "mean_reversion",
    "volume_anomaly_breakout": "mean_reversion",
    "crowding_fade": "vol_squeeze_break",
    "liquidation_bounce": "vol_squeeze_break",
    "data_driven": "mean_reversion",
    "symbolic": "mean_reversion",
}


def research_director_budget(max_trial_budget=None):
    max_trial_budget = int(
        max_trial_budget
        or os.environ.get("QIYU_MAX_TRIAL_BUDGET")
        or 320
    )
    return {
        "role": "research_director",
        "max_trial_budget": int(max_trial_budget),
        "stage_gates": [
            "mechanism_complete",
            "naked_probe",
            "antifalsify_matrix",
            "leakage_audit",
            "causal_boundary",
            "execution_feasibility",
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


def run_empirical_scientist(factor_matrix, fwd_returns, run_id, max_phenomena=24,
                            candles=None, timeframe=None):
    """Independent: phenomena only. No trade rules."""
    scanned = phscan.scan_all(
        factor_matrix, fwd_returns, max_phenomena=max_phenomena,
        candles=candles, timeframe=timeframe,
    )
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
    n_pop = int(os.environ.get("QIYU_SYM_POP") or 60)
    n_gen = int(os.environ.get("QIYU_SYM_GEN") or 6)
    top_k = int(os.environ.get("QIYU_SYM_TOPK") or 16)
    # RAM-safe caps on tiny VPS
    n_pop = max(20, min(n_pop, 100))
    n_gen = max(2, min(n_gen, 8))
    pack = sym.search(
        factor_matrix, fwd_returns, n_pop=n_pop, n_gen=n_gen, top_k=top_k,
    )
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
        "n_pop": n_pop,
        "n_gen": n_gen,
        "top_keys": [h.get("observable_proxy") for h in hyps[:5]],
        "llm": False,
        "backend": "gp_lite_not_pysr",
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


def run_leakage_auditor(factor_values, fwd_returns, side="high", run_id=None,
                        precomputed_event_mask=False, candles=None, symbol=None,
                        timeframe=None, horizon=3, trade_direction="long",
                        execution_mapping="next_bar_open"):
    """Look-ahead / label leakage smell checks. Does not invent strategies."""
    issues = []
    f = list(factor_values or [])
    r = list(fwd_returns or [])
    n = min(len(f), len(r))
    if n < 40:
        out = {
            "role": "leakage_auditor",
            "ok": False,
            "passed": False,
            "error": "insufficient_series",
            "at": _now(),
        }
        if run_id:
            board.write(run_id, "leakage_auditor", "audit", out)
        return out

    aligned_f = f[-n:]
    aligned_r = r[-n:]
    aligned_candles = list(candles or [])[-n:] if candles else None

    def evaluate(series):
        if precomputed_event_mask:
            return probes._evaluate_trial(
                aligned_candles,
                aligned_r,
                {
                    "event_id": "leakage_exact_event",
                    "kind": "human_contract_exact",
                    "terms": [{"factor": "precomputed_contract_event"}],
                    "mask": [bool(value) for value in series],
                },
                int(horizon or 3),
                -1 if str(trade_direction).lower() == "short" else 1,
                execution_mapping or "next_bar_open",
                symbol=symbol,
                timeframe=timeframe,
            )
        direction = -1 if str(trade_direction or "long").lower() == "short" else 1
        return probes.evaluate_naked_probe(
            series,
            aligned_r,
            side=side,
            candles=aligned_candles,
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            execution_mapping=execution_mapping or "next_bar_open",
            horizons=(int(horizon or 3),),
        )

    # Future-value alignment: if using t+2 signal information improves edge,
    # the original feature/event construction may contain look-ahead leakage.
    base = evaluate(aligned_f)
    base_net = float((base or {}).get("mean_net") or 0.0)
    fwd_shift = aligned_f[2:] + [None, None]
    fut = evaluate(fwd_shift)
    fut_net = float((fut or {}).get("mean_net") or 0.0)
    if fut_net > base_net * 1.15 and fut_net > 0:
        issues.append("future_shift_improves_edge")

    # Same-bar perfect alignment with abs return proxy
    same_bar_hits = 0
    checked = 0
    for i in range(n):
        if aligned_f[i] is None or aligned_r[i] is None:
            continue
        checked += 1
        try:
            if abs(float(aligned_f[i])) > 0 and abs(float(aligned_r[i])) > 0:
                # crude: factor equals fwd in magnitude often → smell
                if abs(float(aligned_f[i]) - float(aligned_r[i])) < 1e-12:
                    same_bar_hits += 1
        except Exception:
            pass
    if checked > 50 and same_bar_hits / float(checked) > 0.05:
        issues.append("factor_equals_fwd_too_often")

    passed = len(issues) == 0
    out = {
        "role": "leakage_auditor",
        "ok": True,
        "passed": passed,
        "issues": issues,
        "base_net": base_net,
        "future_shift_net": fut_net,
        "precomputed_event_mask": bool(precomputed_event_mask),
        "event_mask_requantiled": False if precomputed_event_mask else None,
        "forbids_zh": ROLE_CONTRACTS["leakage_auditor"]["forbids_zh"],
        "note_zh": (
            "未发现明显泄漏气味" if passed else
            ("泄漏气味: %s" % ",".join(issues))
        ),
        "at": _now(),
    }
    if run_id:
        board.write(run_id, "leakage_auditor", "audit", out)
        ledger.append_event({
            "event_type": "leakage_audit",
            "passed": passed,
            "issues": issues,
        }, run_id=run_id)
    return out


def run_causal_auditor(antifalsify_pack, causal_claim_flag=False, run_id=None):
    """Boundary check: antifalsify must NOT be marketed as causal proof."""
    pack = antifalsify_pack or {}
    violations = []
    if causal_claim_flag or pack.get("causal_claim") is True:
        violations.append("explicit_causal_claim")
    if not pack.get("ok"):
        violations.append("antifalsify_missing")
    # Hygiene: require evidence matrix present
    if not pack.get("evidence_matrix"):
        violations.append("missing_evidence_matrix")
    passed = len(violations) == 0 and pack.get("causal_claim") is False
    out = {
        "role": "causal_auditor",
        "ok": True,
        "passed": passed,
        "causal_claim_allowed": False,
        "violations": violations,
        "credibility": pack.get("credibility") or "elevated_if_pass_not_proven",
        "forbids_zh": ROLE_CONTRACTS["causal_auditor"]["forbids_zh"],
        "note_zh": (
            "因果边界合规：反证通过≠因果证明" if passed else
            ("因果卫生失败: %s" % ",".join(violations))
        ),
        "dowhy_installed": False,
        "at": _now(),
    }
    if run_id:
        board.write(run_id, "causal_auditor", "boundary", out)
        ledger.append_event({
            "event_type": "causal_boundary",
            "passed": passed,
            "violations": violations,
        }, run_id=run_id)
    return out


def run_execution_engineer(probe_best, efr_pack=None, run_id=None, min_efr=1.5):
    """Cost/capacity feasibility. Does not invent trade logic."""
    best = probe_best or {}
    mean_net = float(best.get("mean_net") or 0.0)
    n_hits = int(
        best.get("n_independent_events")
        or best.get("n_filled_events")
        or best.get("n_hits")
        or 0
    )
    efr = (efr_pack or {}).get("efr") if isinstance(efr_pack, dict) else efr_pack
    efr_val = None
    if isinstance(efr, dict):
        efr_val = efr.get("efr")
    elif efr is not None:
        try:
            efr_val = float(efr)
        except Exception:
            efr_val = None
    issues = []
    if mean_net <= 0:
        issues.append("non_positive_net_after_cost")
    if n_hits < 12:
        issues.append("too_few_hits_for_capacity")
    if efr_val is not None and float(efr_val) < float(min_efr):
        issues.append("efr_below_floor")
    # Fragile edge vs typical swap round-trip
    if 0 < mean_net < 0.0004:
        issues.append("edge_thinner_than_typical_slippage_buffer")
    passed = len(issues) == 0
    out = {
        "role": "execution_engineer",
        "ok": True,
        "passed": passed,
        "issues": issues,
        "mean_net": mean_net,
        "n_hits": n_hits,
        "sample_field": (
            "n_independent_events" if best.get("n_independent_events") is not None
            else "n_filled_events" if best.get("n_filled_events") is not None
            else "n_hits"
        ),
        "efr": efr_val,
        "may_propose_strategy": False,
        "lean_installed": False,
        "note_zh": (
            "执行可行性初检通过（轻量；非 LEAN 撮合仿真）" if passed else
            ("执行可行性未过: %s" % ",".join(issues))
        ),
        "at": _now(),
    }
    if run_id:
        board.write(run_id, "execution_engineer", "feasibility", out)
        ledger.append_event({
            "event_type": "execution_feasibility",
            "passed": passed,
            "issues": issues,
            "mean_net": mean_net,
        }, run_id=run_id)
    return out


def run_constructive_redteam(main_hyp, factor_matrix, fwd_returns, run_id,
                             main_probe_best=None):
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
    main_probe = (
        {"passed": bool((main_probe_best or {}).get("passed")), "best": main_probe_best}
        if main_probe_best is not None
        else probes.probe_hypothesis(main_hyp, factor_matrix, fwd_returns)
    )
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


KIMI_EVIDENCE_JUDGE_PROMPT = """你是策略正式四次复核之前的独立研究裁判。
你不是策略生成者，不得提出新策略、阈值或过滤条件。输入只有脱敏的证据布尔字段
和本地规则得分，不含账户、密钥或下单信息。请独立判断证据是否足以进入策略组装。
本地硬门槛拥有最高优先级，你只能同意或否决，绝不能覆盖本地否决。
严格输出JSON：{"decision":"ADMIT或REJECT","reason":"...",
"unresolved_risks":["..."]}。ADMIT只代表可进入策略组装，不代表通过正式复核或可实盘。
"""


def _kimi_evidence_judgment(evidence_fields, rule_score):
    """Call the configured Kimi K3 endpoint with redacted evidence only."""
    try:
        from auto_trade_pre_review_discovery import _call_structured
        result = _call_structured(
            "kimi", KIMI_EVIDENCE_JUDGE_PROMPT,
            {
                "evidence_fields": dict(evidence_fields or {}),
                "deterministic_rule_score": float(rule_score),
                "contains_market_data": False,
                "contains_account_data": False,
                "authority": "pre-review research ADMIT or REJECT only",
            },
            max_tokens=700, retry=True,
        )
    except Exception as exc:
        return {
            "attempted": True, "enabled": True, "ok": False,
            "decision": "REJECT", "error": str(exc),
            "note_zh": "Kimi独立裁判调用异常，按安全原则拒绝进入策略组装。",
        }
    parsed = result.get("result") or {}
    decision = str(parsed.get("decision") or "").strip().upper()
    if not result.get("ok") or decision not in ("ADMIT", "REJECT"):
        return {
            "attempted": True, "enabled": True, "ok": False,
            "decision": "REJECT", "error": result.get("error"),
            "note_zh": "Kimi未返回有效结构化裁决，按安全原则拒绝进入策略组装。",
        }
    return {
        "attempted": True, "enabled": True, "ok": True,
        "model": result.get("model"), "decision": decision,
        "reason": str(parsed.get("reason") or "")[:1200],
        "unresolved_risks": [
            str(value)[:400] for value in
            (parsed.get("unresolved_risks") or [])[:8]
        ],
        "note_zh": (
            "Kimi独立裁判同意进入策略组装"
            if decision == "ADMIT" else
            "Kimi独立裁判否决进入策略组装"
        ),
    }


def judge_from_evidence(evidence_fields, run_id=None):
    """Deterministic evidence gate followed by an independent Kimi veto gate."""
    required = [
        "naked_probe_passed", "antifalsify_passed", "efr_passed",
        "leakage_passed", "causal_boundary_passed", "execution_passed",
    ]
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
    if evidence_fields.get("leakage_passed"):
        score += 0.8
    if evidence_fields.get("causal_boundary_passed"):
        score += 0.8
    if evidence_fields.get("execution_passed"):
        score += 0.8
    if evidence_fields.get("dsr_passed"):
        score += 1.5
    if evidence_fields.get("bidirectional_hit"):
        score += 1.0
    deterministic_admit = bool(not missing and score >= 6.5)
    # Kimi is called only after all deterministic research gates (including
    # DSR/PBO supplied by the caller) pass.  Availability policy is explicit:
    # optional judges may veto with a valid REJECT, but disabled/provider-error
    # states no longer masquerade as evidence that the candidate failed.
    kimi_enabled = str(os.environ.get("QIYU_KIMI_ENABLED") or "0").strip().lower() in (
        "1", "true", "yes", "on",
    )
    kimi_required = str(os.environ.get("QIYU_KIMI_REQUIRED") or "0").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if kimi_enabled:
        if deterministic_admit:
            kimi_note = _kimi_evidence_judgment(evidence_fields, score)
        else:
            kimi_note = {
                "attempted": False, "enabled": True, "ok": True,
                "decision": "REJECT",
                "note_zh": "本地确定性门槛已否决，不向Kimi发送无资格候选。",
            }
    else:
        kimi_note = {
            "attempted": False,
            "enabled": False,
            "ok": True,
            "decision": "UNAVAILABLE" if kimi_required else "SKIPPED",
            "note_zh": (
                "Kimi为必需裁判但未启用，按配置失败关闭。"
                if kimi_required else
                "Kimi为可选独立否决层且未启用；保留本地确定性裁决并显式标记降级。"
            ),
        }
    if kimi_required:
        kimi_admit = bool(kimi_note.get("ok") and kimi_note.get("decision") == "ADMIT")
    elif kimi_note.get("ok") and kimi_note.get("decision") == "REJECT":
        kimi_admit = False
    else:
        kimi_admit = True
    admit = bool(deterministic_admit and kimi_admit)
    out = {
        "role": "judge",
        "admit_to_assembly": admit,
        "deterministic_admit": deterministic_admit,
        "kimi_admit": kimi_admit,
        "kimi_required": kimi_required,
        "judge_policy": "deterministic_then_required_kimi" if kimi_required else "deterministic_with_optional_kimi_veto",
        "degraded": bool(not kimi_enabled or (kimi_note.get("attempted") and not kimi_note.get("ok"))),
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
