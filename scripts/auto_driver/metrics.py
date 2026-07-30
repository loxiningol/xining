# -*- coding: utf-8 -*-
"""Extract Gate / funnel metrics from STEP A return payloads (read-only)."""
from __future__ import print_function

import copy


def _gates_by_id(result):
    out = {}
    for g in ((result or {}).get("gate_results") or {}).get("gates") or []:
        if isinstance(g, dict) and g.get("gate_id"):
            out[str(g["gate_id"])] = g
    return out


def extract_l1(result):
    ph = ((result or {}).get("phase3_funnel") or {}).get("l1_micro_screen") or {}
    metrics = ph.get("metrics") or {}
    return {
        "pass": bool(ph.get("pass")),
        "reject_reasons": list(ph.get("reject_reasons") or []),
        "filled_entries": metrics.get("filled_entries"),
        "payoff_ratio": metrics.get("payoff_ratio"),
        "win_rate": metrics.get("win_rate"),
        "expectancy_factor": metrics.get("expectancy_factor"),
    }


def extract_gate2_fitness(result):
    g2 = _gates_by_id(result).get("gate2_base_backtest") or {}
    ev = g2.get("evidence") or {}
    fit = ev.get("fitness") or {}
    if not fit and isinstance(ev, dict):
        # some paths flatten fitness fields into evidence
        if "failed_checks" in ev or "payoff_ratio" in ev:
            fit = ev
    return {
        "gate_pass": bool(g2.get("pass")),
        "missing": bool(ev.get("missing")),
        "sample_size": ev.get("sample_size") or fit.get("n") or fit.get("sample_size"),
        "pass": fit.get("pass"),
        "failed_checks": list(fit.get("failed_checks") or []),
        "verdict_tags": list(fit.get("verdict_tags") or []),
        "payoff_ratio": fit.get("payoff_ratio"),
        "calmar": fit.get("calmar"),
        "expectancy_factor": fit.get("expectancy_factor"),
        "worst5_loss_share": fit.get("worst5_loss_share"),
        "sharpe": fit.get("sharpe"),
        "win_rate_pct": ev.get("win_rate_pct") or fit.get("win_rate_pct"),
        "remove_max_win": fit.get("remove_max_win"),
        "checks": fit.get("checks") or {},
        "thresholds": fit.get("thresholds") or {},
    }


def extract_walk_forward(result):
    wf = (result or {}).get("walk_forward")
    if not isinstance(wf, dict):
        l3 = ((result or {}).get("phase3_funnel") or {}).get("l3_null_hypothesis") or {}
        wf = l3.get("walk_forward") if isinstance(l3, dict) else None
    if not isinstance(wf, dict):
        g3 = _gates_by_id(result).get("gate3_walk_forward") or {}
        wf = (g3.get("evidence") or {}) if isinstance(g3, dict) else {}
    return {
        "pass_count": wf.get("pass_count") if isinstance(wf, dict) else None,
        "total": wf.get("total") if isinstance(wf, dict) else None,
        "windows": wf.get("windows") if isinstance(wf, dict) else None,
        "raw": wf if isinstance(wf, dict) else {},
    }


def gate_scorecard(result):
    by_id = _gates_by_id(result)
    rows = []
    for gid, g in by_id.items():
        rows.append({
            "gate_id": gid,
            "pass": bool(g.get("pass")),
            "missing": bool((g.get("evidence") or {}).get("missing")),
        })
    return rows


def build_failure_context(result, pack, iteration, symbol, timeframe, direction,
                          optimize_goals=None, notes=None):
    """Structured context for 3rd-party AI optimizer prompts."""
    reason = (result or {}).get("reason") or (result or {}).get("stage")
    l1 = extract_l1(result)
    g2 = extract_gate2_fitness(result)
    wf = extract_walk_forward(result)
    spec = (pack or {}).get("mechanism_spec") or {}
    dsl = _active_dsl(pack, direction)
    gaps = metric_gaps(g2, l1, reason)
    ctx = {
        "iteration": iteration,
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "pipeline_reason": reason,
        "task_id": (result or {}).get("task_id"),
        "ok": bool((result or {}).get("ok")),
        "l1": l1,
        "gate2_fitness": g2,
        "walk_forward": {k: wf.get(k) for k in ("pass_count", "total")},
        "gates": gate_scorecard(result),
        "metric_gaps": gaps,
        "composite_score": composite_score(g2, l1, reason),
        "mechanism_family": spec.get("mechanism_family"),
        "mechanism_name": spec.get("mechanism_name"),
        "non_negotiable_rules": list(spec.get("non_negotiable_rules") or [])[:20],
        "forbidden_transformations": list(spec.get("forbidden_transformations") or [])[:20],
        "optimize_goals": list(optimize_goals or []),
        "notes": list(notes or [])[:12],
        "dsl_summary": summarize_dsl(dsl),
        "dsl": copy.deepcopy(dsl) if isinstance(dsl, dict) else {},
        "mechanism_spec": copy.deepcopy(spec) if isinstance(spec, dict) else {},
        "pretest_quality": copy.deepcopy((result or {}).get("pretest_quality") or {}),
    }
    try:
        from dual_engine_workflow_v2 import invariants_contract as inv
        contract, src = inv.resolve_contract_for_pack(pack)
        ctx["invariants_contract"] = contract
        ctx["invariants_contract_source"] = src
        if not ctx.get("pretest_quality"):
            from dual_engine_workflow_v2.pretest_quality import run_pretest_quality
            ctx["pretest_quality"] = run_pretest_quality(pack, direction=direction)
    except Exception as exc:
        ctx["invariants_contract_error"] = str(exc)
    try:
        from . import diagnostic as diagnostic_mod
        ctx["diagnostic"] = diagnostic_mod.build_diagnostic(
            ctx=ctx, result=result, pack=pack,
            symbol=symbol, timeframe=timeframe, direction=direction,
        )
    except Exception as exc:
        ctx["diagnostic"] = {"ok": False, "error": str(exc)}
    return ctx


def _active_dsl(pack, direction):
    if not isinstance(pack, dict):
        return {}
    if str(direction).lower() == "short":
        return pack.get("dsl_short") or pack.get("dsl") or {}
    return pack.get("dsl_long") or pack.get("dsl") or {}


def summarize_dsl(dsl):
    if not isinstance(dsl, dict):
        return {}
    entry = dsl.get("entry") or {}
    exit_ = dsl.get("exit") or {}
    return {
        "key": dsl.get("key"),
        "direction": dsl.get("direction"),
        "timeframe": dsl.get("timeframe"),
        "max_hold_bars": dsl.get("max_hold_bars"),
        "entry_all_n": len(entry.get("all") or []) if isinstance(entry, dict) else None,
        "entry_any_n": len(entry.get("any") or []) if isinstance(entry, dict) else None,
        "exit_any_n": len(exit_.get("any") or []) if isinstance(exit_, dict) else None,
        "exit_ops": [
            (x.get("exit_op") or x.get("id") or x.get("role"))
            for x in (exit_.get("any") or [])
            if isinstance(x, dict)
        ][:12],
    }


def metric_gaps(g2, l1, reason):
    """Positive gap = shortfall vs hard floors (0 means cleared / N/A)."""
    gaps = {}
    reason = str(reason or "")
    if reason in ("funnel_l1_fail",) or (l1 and not l1.get("pass")):
        fills = l1.get("filled_entries")
        try:
            fills_n = float(fills) if fills is not None else 0.0
        except Exception:
            fills_n = 0.0
        gaps["l1_filled_entries"] = max(0.0, 5.0 - fills_n)
        pay = l1.get("payoff_ratio")
        try:
            pay_f = float(pay) if pay is not None else 0.0
        except Exception:
            pay_f = 0.0
        if fills_n >= 1:
            gaps["l1_payoff"] = max(0.0, 1.2 - pay_f)
    thr = (g2 or {}).get("thresholds") or {}
    payoff_min = float(thr.get("payoff_min") or 2.5)
    calmar_min = float(thr.get("calmar_min") or 1.5)
    w5_max = float(thr.get("worst5_loss_share_max") or 0.40)
    exp_min = float(thr.get("expectancy_factor_min") or 1.0)

    def _f(x, default=None):
        try:
            if x is None:
                return default
            return float(x)
        except Exception:
            return default

    if not (g2 or {}).get("missing"):
        pay = _f(g2.get("payoff_ratio"))
        if pay is not None:
            gaps["payoff_ratio"] = max(0.0, payoff_min - pay)
        cal = _f(g2.get("calmar"))
        if cal is not None:
            gaps["calmar"] = max(0.0, calmar_min - cal)
        w5 = _f(g2.get("worst5_loss_share"))
        if w5 is not None:
            gaps["worst5_loss_share"] = max(0.0, w5 - w5_max)
        exp = _f(g2.get("expectancy_factor"))
        if exp is not None:
            gaps["expectancy_factor"] = max(0.0, exp_min - exp)
        if "remove_max_win_stable" in (g2.get("failed_checks") or []):
            gaps["lottery"] = 1.0
        else:
            gaps["lottery"] = 0.0
    return gaps


def composite_score(g2, l1, reason):
    """Higher is better. Used for convergence detection."""
    score = 0.0
    reason = str(reason or "")
    g2_has_signal = bool(
        (g2 or {}).get("payoff_ratio") is not None
        or (g2 or {}).get("failed_checks")
        or (g2 or {}).get("gate_pass")
    )
    # Prefer Gate2 scoring when fitness evidence exists (L1 may be omitted in
    # repair_exhausted payloads, which previously collapsed score to 0).
    l1_only = (
        reason in ("funnel_l1_fail",)
        or (
            l1
            and l1.get("pass") is False
            and not g2_has_signal
            and reason not in ("repair_exhausted_or_drift", "gate2_3_fail", "gate2_fail")
        )
    )
    if l1_only:
        fills = l1.get("filled_entries") or 0
        try:
            score += min(float(fills) / 5.0, 1.0) * 2.0
        except Exception:
            pass
        pay = l1.get("payoff_ratio")
        try:
            if pay is not None:
                score += min(float(pay) / 1.2, 1.5)
        except Exception:
            pass
        return round(score, 6)

    if (g2 or {}).get("missing") and not g2_has_signal:
        return round(score, 6)

    def _f(x):
        try:
            return float(x)
        except Exception:
            return None

    pay = _f(g2.get("payoff_ratio"))
    if pay is not None:
        score += min(pay / 2.5, 1.5) * 2.0
    cal = _f(g2.get("calmar"))
    if cal is not None:
        score += min(max(cal, 0.0) / 1.5, 1.5) * 1.5
    w5 = _f(g2.get("worst5_loss_share"))
    if w5 is not None:
        # lower w5 better; 0.4 floor → 1.0 contribution
        score += max(0.0, min(1.0, (0.4 / max(w5, 1e-6)))) * 2.0
    exp = _f(g2.get("expectancy_factor"))
    if exp is not None:
        score += min(max(exp, 0.0) / 1.0, 1.5)
    if "remove_max_win_stable" not in (g2.get("failed_checks") or []):
        score += 1.0
    if g2.get("pass") or (g2.get("gate_pass") and not g2.get("failed_checks")):
        score += 3.0
    return round(score, 6)


def total_gap(gaps):
    if not isinstance(gaps, dict):
        return 0.0
    s = 0.0
    for v in gaps.values():
        try:
            s += float(v)
        except Exception:
            continue
    return round(s, 6)
