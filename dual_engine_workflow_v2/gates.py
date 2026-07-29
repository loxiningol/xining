# -*- coding: utf-8 -*-
"""Gates 0–7 orchestrator for STEP A."""
from __future__ import print_function

import copy

from .config import _now, _atomic
from .step_a_config import (
    GATE_IDS,
    GATES_DIR,
    STEP_A_SCHEMA,
    STEP_A_CODE_VERSION,
    WF_WINDOW_PASS_REQUIREMENT,
    ensure_step_a_dirs,
)


def _gate(gid, name, passed, evidence, blocking=True, notes=None):
    return {
        "gate_id": gid,
        "name": name,
        "pass": bool(passed),
        "blocking": bool(blocking),
        "evidence": evidence or {},
        "notes": notes or [],
        "at": _now(),
    }


def evaluate_gate0(mechanism_spec, normalize_ok):
    spec = mechanism_spec or {}
    checks = {
        "spec_complete": bool(normalize_ok),
        "counterparty_clear": bool(str(spec.get("counterparty_source") or "").strip()),
        "edge_decay_clear": bool(str(spec.get("edge_decay_conditions") or "").strip()),
        "not_indicator_soup": "rsi+macd+ema" not in str(spec.get("entry_logic") or "").lower(),
        "family_present": bool(str(spec.get("mechanism_family") or "").strip()),
    }
    return _gate(
        "gate0_mechanism_integrity", "机制完整性",
        all(checks.values()), checks,
    )


def evaluate_gate1(fidelity_diff, definition, mechanism_spec):
    fd = fidelity_diff or {}
    checks = {
        "fidelity_pass": bool(fd.get("pass")),
        "has_entry": bool((definition or {}).get("entry")),
        "has_exit_or_exit_logic": bool((definition or {}).get("exit") or (mechanism_spec or {}).get("exit_logic")),
        "stop_in_spec": bool(str((mechanism_spec or {}).get("stop_logic") or "").strip()),
        "no_non_negotiable_violations": not bool(fd.get("non_negotiable_violations")),
        "no_future_feature_tokens": "future_" not in str(fd.get("feature_set") or "").lower(),
    }
    return _gate("gate1_code_fidelity", "代码忠实度", all(checks.values()), checks)


def evaluate_gate2(base_metrics, trades):
    """Gate2 base backtest + Phase-2 multi-objective fitness hard gates."""
    from .fitness_engine import evaluate_multi_objective

    m = base_metrics or {}
    n = int(m.get("trades") or len(trades or []) or 0)
    fitness = evaluate_multi_objective(trades, base_metrics=m, enforce=True, min_trades=8)
    fm = fitness.get("metrics") or {}
    evidence = {
        "sample_size": n,
        "win_rate_pct": m.get("win_rate_pct") if m.get("win_rate_pct") is not None
                        else fm.get("win_rate_pct"),
        "profit_factor": m.get("profit_factor") or m.get("pf"),
        "gross_expectancy": m.get("mean_gross") or m.get("mean_net"),
        "cost_after_expectancy": m.get("mean_net"),
        "max_dd": m.get("max_dd") or m.get("max_drawdown") or (fm.get("calmar_meta") or {}).get("max_drawdown"),
        "holding": m.get("avg_hold_bars"),
        "trade_frequency_proxy_trades": n,
        "fitness": {
            "pass": fitness.get("pass"),
            "failed_checks": fitness.get("failed_checks"),
            "verdict_tags": fitness.get("verdict_tags"),
            "calmar": fm.get("calmar"),
            "payoff_ratio": fm.get("payoff_ratio"),
            "expectancy_factor": fm.get("expectancy_factor"),
            "classic_expectancy": fm.get("classic_expectancy"),
            "worst5_loss_share": fm.get("worst5_loss_share"),
            "sharpe": fm.get("sharpe"),
            "remove_max_win": fm.get("remove_max_win"),
            "mae_flags_n": len(fm.get("mae_flags") or []),
            "thresholds": fm.get("thresholds"),
            "formulas": fm.get("formulas"),
            "checks": fitness.get("checks"),
        },
    }
    # Hard: sample + metrics recorded + multi-objective fitness
    passed = bool(fitness.get("pass")) and m.get("mean_net") is not None
    notes = [
        "gate2_phase2_multi_objective_fitness",
        "calmar>=1.5; payoff>=2.5; WR*payoff>=1.0; worst5_loss<=40%; "
        "MAE>2*avg_win demote; remove-max-win Calmar/Sharpe drop>50% reject",
        "protective_0.9pct_SL_unchanged",
    ]
    if fitness.get("verdict_tags"):
        notes.append("tags=" + ",".join(fitness["verdict_tags"]))
    return _gate("gate2_base_backtest", "基础回测", passed, evidence, notes=notes)


def evaluate_gate3(walk_forward, trades=None, base_metrics=None, null_hypothesis=None):
    """walk_forward: {windows:[{id,pass,metrics,fail_reason}], pass_count, total}

    Phase-2: when trades (or walk_forward['oos_trades']) provided, also require
    multi-objective fitness on that sample (does not lower window pass counts).

    Phase-3 (additive): optional null_hypothesis dict — if provided and
    pass=False, Gate3 fails closed with evidence logged. Signature remains
    backward compatible (null_hypothesis defaults to None).
    """
    from .fitness_engine import evaluate_multi_objective

    wf = walk_forward or {}
    windows = list(wf.get("windows") or [])
    need_pass, need_total = WF_WINDOW_PASS_REQUIREMENT
    total = int(wf.get("total") or len(windows) or 0)
    pass_count = int(wf.get("pass_count") or sum(1 for w in windows if w.get("pass")))
    # Must show per-window detail — averages alone fail
    has_detail = all(("id" in w or "window" in w) and ("metrics" in w or "pass" in w) for w in windows) if windows else False
    window_ok = total >= need_total and pass_count >= need_pass and has_detail

    oos_trades = trades if trades is not None else wf.get("oos_trades") or wf.get("trades")
    fitness_block = None
    fitness_ok = True
    if oos_trades is not None:
        fitness_block = evaluate_multi_objective(
            oos_trades, base_metrics=base_metrics or wf.get("aggregate_metrics"),
            enforce=True, min_trades=8,
        )
        fitness_ok = bool(fitness_block.get("pass"))

    nh = null_hypothesis
    nh_ok = True
    if nh is not None:
        nh_ok = bool(nh.get("pass"))

    passed = window_ok and fitness_ok and nh_ok
    evidence = {
        "pass_count": pass_count,
        "total": total,
        "requirement": "%s/%s" % (need_pass, need_total),
        "windows": windows,
        "average_only_forbidden": True,
        "has_per_window_detail": has_detail,
        "failed_window_explanations": [
            {"id": w.get("id") or w.get("window"), "reason": w.get("fail_reason") or w.get("reason")}
            for w in windows if not w.get("pass")
        ],
        "fitness": None if fitness_block is None else {
            "pass": fitness_block.get("pass"),
            "failed_checks": fitness_block.get("failed_checks"),
            "verdict_tags": fitness_block.get("verdict_tags"),
            "checks": fitness_block.get("checks"),
            "calmar": (fitness_block.get("metrics") or {}).get("calmar"),
            "payoff_ratio": (fitness_block.get("metrics") or {}).get("payoff_ratio"),
            "expectancy_factor": (fitness_block.get("metrics") or {}).get("expectancy_factor"),
        },
        "fitness_enforced_when_trades_provided": oos_trades is not None,
        "phase3_null_hypothesis": None if nh is None else {
            "pass": nh.get("pass"),
            "reject_reasons": nh.get("reject_reasons"),
            "stage": nh.get("stage"),
        },
        "window_pass_rule_phase3": "Calmar>=1.0 AND classic_expectancy>0 (set by WF builder)",
    }
    notes = []
    if oos_trades is None:
        notes.append("fitness_deferred_no_oos_trades_in_gate3_payload")
    elif not fitness_ok:
        notes.append("fitness_failed_on_oos_trades")
    if nh is not None and not nh_ok:
        notes.append("phase3_null_hypothesis_fail_closed")
    return _gate("gate3_walk_forward", "Walk-forward", passed, evidence, notes=notes)


def evaluate_gate4(split_summary):
    s = split_summary or {}
    evidence = {
        "n_tests": s.get("n_tests"),
        "pass": s.get("pass"),
        "fail": s.get("fail"),
        "inconclusive": s.get("inconclusive"),
        "hard_fail_ids": s.get("gate4_hard_fail_ids") or [],
        "results": s.get("results"),
    }
    # Must have executed 20 tests; block on mechanism_failure
    executed = int(s.get("n_tests") or 0) >= 20
    blocked = bool(s.get("gate4_block"))
    passed = executed and not blocked
    return _gate("gate4_split_destruction", "逻辑破坏拆分测试", passed, evidence,
                 notes=["INCONCLUSIVE allowed; mechanism_failure FAIL blocks"])


def evaluate_gate5(mc_summary, friction):
    mc = mc_summary or {}
    fr = friction or {}
    accept = float(mc.get("accept_pct_observed") or mc.get("accept_pct") or 0)
    fr_sharpe = float((fr.get("metrics") or fr).get("sharpe") or fr.get("sharpe") or 0)
    fr_mean = float((fr.get("metrics") or fr).get("mean_net") or fr.get("mean_net") or 0)
    checks = {
        "mc_accept_ge_90": accept >= 0.90,
        "friction_sharpe_not_unacceptable": fr_sharpe > -0.5,
        "friction_mean_not_clearly_negative": fr_mean > -0.01,
    }
    return _gate("gate5_mc_friction", "蒙特卡洛与摩擦压力", all(checks.values()), {
        "mc": mc, "friction": {"sharpe": fr_sharpe, "mean_net": fr_mean}, "checks": checks,
    })


def evaluate_gate6(reviews):
    """reviews: dict of separate AI reviews — never average into one score."""
    r = reviews or {}
    parts = {
        "glm_mechanism": r.get("glm_mechanism") or {},
        "codex_fidelity": r.get("codex_fidelity") or {},
        "deepseek_logic": r.get("deepseek_logic") or {},
        "production_risk": r.get("production_risk") or {},
    }
    checks = {k: bool(v.get("pass")) for k, v in parts.items()}
    # Must be separate; averaging forbidden
    evidence = {
        "separate_reviews": parts,
        "averaged_score_forbidden": True,
        "checks": checks,
    }
    return _gate("gate6_multi_ai_review", "多AI复核", all(checks.values()), evidence)


def evaluate_gate7(human_confirm_state):
    """Only human confirm allows candidate/auto_open/real size."""
    h = human_confirm_state or {}
    confirmed = bool(h.get("human_confirmed"))
    evidence = {
        "pending_ok": h.get("pending_ok"),
        "human_confirmed": confirmed,
        "auto_open_mounted": bool(h.get("auto_open_mounted")),
        "real_size_granted": bool(h.get("real_size_granted")),
        "policy": "Gate7 required before candidate pool / auto_open / real size",
    }
    # Passing Gate7 means: pushed to pending AND awaiting/received human confirm.
    # Auto-mount without confirm is FAIL.
    if h.get("auto_open_mounted") and not confirmed:
        return _gate("gate7_human_confirm", "人工确认", False, evidence,
                     notes=["illegal_auto_mount_without_human_confirm"])
    passed = bool(h.get("pending_ok")) and (
        confirmed or h.get("awaiting_human") is True
    )
    # Strict: "pass" for pipeline continuation to pending is awaiting_human;
    # production mount requires confirmed.
    return _gate(
        "gate7_human_confirm", "人工确认",
        passed,
        evidence,
        notes=["production_mount_requires_human_confirmed=true"],
    )


def assemble_gate_results(gates_list, task_id=None):
    by_id = {g["gate_id"]: g for g in gates_list}
    ordered = []
    for gid in GATE_IDS:
        ordered.append(by_id.get(gid) or _gate(gid, gid, False, {"missing": True}))
    # sequential: first failure stops "all_pass"
    all_pass = True
    first_fail = None
    for g in ordered:
        if not g.get("pass"):
            all_pass = False
            first_fail = g["gate_id"]
            break
    payload = {
        "schema": STEP_A_SCHEMA,
        "artifact": "gate_results",
        "code_version": STEP_A_CODE_VERSION,
        "task_id": task_id,
        "created_at": _now(),
        "gates": ordered,
        "all_gates_pass": all_pass,
        "first_fail_gate": first_fail,
        "passed_count": sum(1 for g in ordered if g.get("pass")),
        "total_gates": len(ordered),
        "production_mounted": False,  # never true without gate7 human_confirmed
    }
    # production mount flag
    g7 = by_id.get("gate7_human_confirm") or {}
    ev = g7.get("evidence") or {}
    payload["production_mounted"] = bool(ev.get("human_confirmed") and ev.get("auto_open_mounted"))
    return payload


def save_gate_results(task_id, payload):
    ensure_step_a_dirs()
    from . import step_a_config as sc
    from pathlib import Path
    path = Path(sc.GATES_DIR) / ("%s_gate_results.json" % task_id)
    _atomic(path, payload)
    return str(path)
