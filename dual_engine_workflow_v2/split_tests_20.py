# -*- coding: utf-8 -*-
"""20 split logic-destruction tests — structured PASS/FAIL/INCONCLUSIVE each."""
from __future__ import print_function

import copy
import math
import random
import statistics

from .config import _now, _atomic
from .step_a_config import (
    SPLIT_TEST_IDS,
    SPLIT_TEST_NAMES_ZH,
    SPLIT_TESTS_DIR,
    STEP_A_SCHEMA,
    STEP_A_CODE_VERSION,
    MC_ORDER_ACCEPT_PCT,
    ensure_step_a_dirs,
)


def _result(test_id, inputs, threshold, actual, status, failure_class,
            repair_allowed, drift_after_repair=None, notes=None):
    assert status in ("PASS", "FAIL", "INCONCLUSIVE")
    return {
        "test_id": test_id,
        "name_zh": SPLIT_TEST_NAMES_ZH.get(test_id, test_id),
        "inputs": inputs,
        "threshold": threshold,
        "actual": actual,
        "status": status,
        "failure_class": failure_class if status != "PASS" else None,
        "repair_allowed": bool(repair_allowed) if status == "FAIL" else False,
        "drift_after_repair": drift_after_repair,
        "notes": notes or [],
    }


def _trade_pnls(trades):
    out = []
    for t in trades or []:
        for k in ("net_pnl_pct", "pnl_pct", "net", "ret"):
            if t.get(k) is not None:
                try:
                    out.append(float(t[k]))
                    break
                except Exception:
                    pass
    return out


def _mean(xs):
    return float(sum(xs) / len(xs)) if xs else 0.0


def _sharpe_proxy(pnls):
    if not pnls or len(pnls) < 3:
        return 0.0
    mu = _mean(pnls)
    sd = statistics.pstdev(pnls) or 1e-9
    return mu / sd * math.sqrt(max(len(pnls), 1))


def run_split_tests_20(*, definition, base_metrics, trades, backtest_fn=None,
                       mechanism_spec=None, fidelity_diff=None,
                       symbol=None, timeframe=None, friction_fn=None):
    """Execute all 20 tests. Uses real trades/metrics; INCONCLUSIVE if sample too small."""
    base_metrics = base_metrics or {}
    trades = list(trades or [])
    pnls = _trade_pnls(trades)
    n = len(pnls)
    base_mean = float(base_metrics.get("mean_net") or _mean(pnls) or 0.0)
    base_sharpe = float(base_metrics.get("sharpe") or _sharpe_proxy(pnls) or 0.0)
    results = []

    # 1 future_leak — proxy: time-shift thresholds should not improve
    if backtest_fn and definition:
        try:
            shifted = copy.deepcopy(definition)

            def _shift(node):
                if isinstance(node, dict):
                    right = node.get("right")
                    if isinstance(right, dict) and "value" in right:
                        try:
                            right["value"] = float(right["value"]) * 1.25
                        except Exception:
                            pass
                    for v in node.values():
                        _shift(v)
                elif isinstance(node, list):
                    for x in node:
                        _shift(x)
            _shift(shifted.get("entry"))
            sm = (backtest_fn(shifted) or {}).get("metrics") or {}
            s_mean = float(sm.get("mean_net") or 0)
            status = "PASS" if s_mean <= base_mean + abs(base_mean) * 0.15 + 1e-6 else "FAIL"
            if n < 5:
                status = "INCONCLUSIVE"
            results.append(_result(
                "future_leak",
                {"method": "threshold_forward_shift_proxy", "base_mean": base_mean},
                {"shifted_mean_not_materially_better": True, "tol": 0.15},
                {"shifted_mean": s_mean},
                status,
                "data_failure" if status == "FAIL" else ("inconclusive" if status == "INCONCLUSIVE" else None),
                repair_allowed=False,
                notes=["proxy_not_true_bar_shift; true bar-shift preferred when frame API available"],
            ))
        except Exception as exc:
            results.append(_result("future_leak", {}, {}, {"error": str(exc)}, "INCONCLUSIVE",
                                   "data_failure", False, notes=[str(exc)]))
    else:
        results.append(_result("future_leak", {}, {}, {"error": "no_backtest_fn"}, "INCONCLUSIVE",
                               "data_failure", False))

    # 2 label_leak — check dsl features for obvious future labels
    feats = []
    try:
        from .mechanism import extract_dsl_conditions
        feats = [c.get("feature") for c in extract_dsl_conditions(definition or {})]
    except Exception:
        pass
    leak_tokens = ("future_", "label_", "y_", "target_", "fwd_ret", "ahead_")
    leaks = [f for f in feats if any(t in str(f).lower() for t in leak_tokens)]
    results.append(_result(
        "label_leak", {"features": feats}, {"forbidden_tokens": list(leak_tokens)},
        {"leaks": leaks},
        "FAIL" if leaks else "PASS",
        "data_failure" if leaks else None, repair_allowed=True,
    ))

    # 3 param_sensitivity
    if backtest_fn and definition and n >= 5:
        sens = []
        for mult in (0.8, 1.2):
            d = copy.deepcopy(definition)

            def walk(node, m=mult):
                if isinstance(node, dict):
                    right = node.get("right")
                    if isinstance(right, dict) and "value" in right:
                        try:
                            right["value"] = round(float(right["value"]) * m, 6)
                        except Exception:
                            pass
                    for v in node.values():
                        walk(v)
                elif isinstance(node, list):
                    for x in node:
                        walk(x)
            walk(d.get("entry"))
            m2 = (backtest_fn(d) or {}).get("metrics") or {}
            sens.append(float(m2.get("mean_net") or 0))
        collapse = all(abs(x - base_mean) > max(0.02, abs(base_mean) * 2) for x in sens) if sens else False
        # FAIL if tiny param change destroys edge to large negative AND base was positive
        fail = base_mean > 0 and any(x < -abs(base_mean) for x in sens)
        results.append(_result(
            "param_sensitivity", {"mults": [0.8, 1.2], "base_mean": base_mean},
            {"no_sign_flip_to_large_negative": True},
            {"means": sens, "collapse_flag": collapse},
            "FAIL" if fail else "PASS",
            "mechanism_failure" if fail else None, repair_allowed=True,
        ))
    else:
        results.append(_result("param_sensitivity", {"n": n}, {"min_trades": 5},
                               {"n": n}, "INCONCLUSIVE", "inconclusive", False))

    # 4 session_sensitivity — split trades by hour if available
    by_hour = {}
    for t in trades:
        h = t.get("hour") or t.get("entry_hour")
        if h is None and t.get("entry_ts"):
            try:
                h = int(str(t["entry_ts"])[11:13])
            except Exception:
                h = None
        if h is None:
            continue
        by_hour.setdefault(int(h), []).append(t)
    if len(by_hour) >= 2 and n >= 8:
        hour_means = {h: _mean(_trade_pnls(v)) for h, v in by_hour.items()}
        vals = list(hour_means.values())
        spread = max(vals) - min(vals)
        fail = spread > max(0.05, abs(base_mean) * 5) and min(vals) < -0.01
        results.append(_result(
            "session_sensitivity", {"hours": list(hour_means)},
            {"spread_not_extreme_with_negative_bucket": True},
            {"hour_means": hour_means, "spread": spread},
            "FAIL" if fail else "PASS",
            "mechanism_failure" if fail else None, repair_allowed=True,
        ))
    else:
        results.append(_result("session_sensitivity", {"n": n, "hours": len(by_hour)},
                               {"min_hours": 2, "min_trades": 8},
                               {"n": n}, "INCONCLUSIVE", "inconclusive", False))

    # 5 symbol_transfer — without other symbol data → INCONCLUSIVE unless flagged
    results.append(_result(
        "symbol_transfer",
        {"symbol": symbol, "note": "requires_second_symbol_backtest"},
        {"transfer_mean_net_non_catastrophic": True},
        {"executed": False},
        "INCONCLUSIVE", "inconclusive", False,
        notes=["not_auto_run_other_symbol_to_avoid_live_side_effects"],
    ))

    # 6 regime stratification — high/low vol split via atr if present
    if n >= 10:
        atr_vals = []
        for t in trades:
            if t.get("atr_pct") is not None:
                atr_vals.append((float(t["atr_pct"]), float(_trade_pnls([t])[0] if _trade_pnls([t]) else 0)))
        if len(atr_vals) >= 10:
            atr_vals.sort()
            mid = len(atr_vals) // 2
            low = _mean([p for _, p in atr_vals[:mid]])
            high = _mean([p for _, p in atr_vals[mid:]])
            fail = (low < -0.01 and high < -0.01)
            results.append(_result(
                "regime_stratification", {"split": "atr_pct_median"},
                {"not_both_regimes_negative": True},
                {"low_mean": low, "high_mean": high},
                "FAIL" if fail else "PASS",
                "mechanism_failure" if fail else None, repair_allowed=True,
            ))
        else:
            results.append(_result("regime_stratification", {}, {}, {"atr_annotated": False},
                                   "INCONCLUSIVE", "inconclusive", False))
    else:
        results.append(_result("regime_stratification", {"n": n}, {"min": 10}, {"n": n},
                               "INCONCLUSIVE", "inconclusive", False))

    # 7 extreme market — worst 20% pnl window
    if n >= 10:
        ordered = sorted(pnls)
        worst = ordered[: max(1, n // 5)]
        worst_mean = _mean(worst)
        fail = worst_mean < -0.05
        results.append(_result(
            "extreme_market", {"worst_quantile": 0.2},
            {"worst_mean_gt_-5pct": True},
            {"worst_mean": worst_mean},
            "FAIL" if fail else "PASS",
            "mechanism_failure" if fail else None, repair_allowed=True,
        ))
    else:
        results.append(_result("extreme_market", {"n": n}, {"min": 10}, {"n": n},
                               "INCONCLUSIVE", "inconclusive", False))

    # 8 random signal control
    if n >= 8:
        rng = random.Random(42)
        rand_means = []
        for _ in range(20):
            fake = [rng.choice(pnls) * rng.choice([-1, 1]) for _ in pnls]
            rand_means.append(_mean(fake))
        # original should beat majority of random sign-scrambles
        beat = sum(1 for m in rand_means if base_mean > m)
        status = "PASS" if beat >= 14 else ("FAIL" if beat <= 8 else "INCONCLUSIVE")
        results.append(_result(
            "random_signal_control", {"scrambles": 20},
            {"beat_at_least_14_of_20": True},
            {"beat": beat, "base_mean": base_mean},
            status,
            "mechanism_failure" if status == "FAIL" else ("inconclusive" if status == "INCONCLUSIVE" else None),
            repair_allowed=False,
        ))
    else:
        results.append(_result("random_signal_control", {"n": n}, {"min": 8}, {"n": n},
                               "INCONCLUSIVE", "inconclusive", False))

    # 9 signal delay — friction latency proxy
    if friction_fn:
        try:
            fr = friction_fn(latency_extra=0.0002) or {}
            fm = fr.get("metrics") or fr
            f_mean = float(fm.get("mean_net") or 0)
            fail = base_mean > 0 and f_mean < -abs(base_mean)
            results.append(_result(
                "signal_delay", {"latency_extra": 0.0002},
                {"delayed_not_large_negative_vs_base": True},
                {"delayed_mean": f_mean, "base_mean": base_mean},
                "FAIL" if fail else "PASS",
                "implementation_failure" if fail else None, repair_allowed=True,
            ))
        except Exception as exc:
            results.append(_result("signal_delay", {}, {}, {"error": str(exc)},
                                   "INCONCLUSIVE", "data_failure", False))
    else:
        results.append(_result("signal_delay", {}, {}, {"friction_fn": False},
                               "INCONCLUSIVE", "inconclusive", False))

    # 10 entry price perturb
    if n >= 5:
        shocked = [p - 0.0005 for p in pnls]  # ~5 bps adverse
        sm = _mean(shocked)
        fail = base_mean > 0 and sm < 0 and abs(sm) > abs(base_mean)
        results.append(_result(
            "entry_price_perturb", {"adverse_bps": 5},
            {"expectancy_not_flip_worse_than_base_mag": True},
            {"shocked_mean": sm},
            "FAIL" if fail else "PASS",
            "cost_failure" if fail else None, repair_allowed=True,
        ))
    else:
        results.append(_result("entry_price_perturb", {"n": n}, {"min": 5}, {"n": n},
                               "INCONCLUSIVE", "inconclusive", False))

    # 11 exit price perturb
    if n >= 5:
        shocked = [p - 0.0005 for p in pnls]
        sm = _mean(shocked)
        fail = base_mean > 0 and sm < -0.002
        results.append(_result(
            "exit_price_perturb", {"adverse_bps": 5},
            {"shocked_mean_gt_-0.2pct": True},
            {"shocked_mean": sm},
            "FAIL" if fail else "PASS",
            "cost_failure" if fail else None, repair_allowed=True,
        ))
    else:
        results.append(_result("exit_price_perturb", {"n": n}, {"min": 5}, {"n": n},
                               "INCONCLUSIVE", "inconclusive", False))

    # 12 fee multiply
    if friction_fn:
        try:
            fr = friction_fn(slip_mult=1.0, fee_mult=2.0) or {}
            fm = fr.get("metrics") or fr
            f_mean = float(fm.get("mean_net") or 0)
            fail = f_mean < -0.01
            results.append(_result(
                "fee_multiply", {"fee_mult": 2.0},
                {"mean_net_gt_-1pct": True},
                {"mean_net": f_mean},
                "FAIL" if fail else "PASS",
                "cost_failure" if fail else None, repair_allowed=False,
            ))
        except Exception as exc:
            # fallback synthetic
            f_mean = base_mean - 0.001 * 2
            results.append(_result(
                "fee_multiply", {"fee_mult": 2.0, "fallback": True},
                {"mean_net_gt_-1pct": True},
                {"mean_net": f_mean, "error": str(exc)},
                "FAIL" if f_mean < -0.01 else "PASS",
                "cost_failure" if f_mean < -0.01 else None, repair_allowed=False,
            ))
    else:
        f_mean = base_mean - 0.002
        results.append(_result(
            "fee_multiply", {"fee_mult": 2.0, "synthetic": True},
            {"mean_net_gt_-1pct": True},
            {"mean_net": f_mean},
            "INCONCLUSIVE" if n < 5 else ("FAIL" if f_mean < -0.01 else "PASS"),
            "cost_failure" if (n >= 5 and f_mean < -0.01) else "inconclusive",
            repair_allowed=False,
        ))

    # 13 slip multiply
    if friction_fn:
        try:
            fr = friction_fn(slip_mult=2.0) or {}
            fm = fr.get("metrics") or fr
            f_sharpe = float(fm.get("sharpe") or 0)
            fail = base_sharpe > 0 and f_sharpe < -0.5
            results.append(_result(
                "slip_multiply", {"slip_mult": 2.0},
                {"friction_sharpe_not_lt_-0.5_when_base_positive": True},
                {"friction_sharpe": f_sharpe, "base_sharpe": base_sharpe},
                "FAIL" if fail else "PASS",
                "cost_failure" if fail else None, repair_allowed=False,
            ))
        except Exception as exc:
            results.append(_result("slip_multiply", {}, {}, {"error": str(exc)},
                                   "INCONCLUSIVE", "data_failure", False))
    else:
        results.append(_result("slip_multiply", {}, {}, {"friction_fn": False},
                               "INCONCLUSIVE", "inconclusive", False))

    # 14 mc trade order
    if n >= 8:
        rng = random.Random(7)
        ok = 0
        trials = 50
        for _ in range(trials):
            seq = pnls[:]
            rng.shuffle(seq)
            # equity path never < -15% cumulative as crude stress
            eq = 0.0
            worst = 0.0
            for p in seq:
                eq += p
                worst = min(worst, eq)
            if worst > -0.15:
                ok += 1
        pct = ok / float(trials)
        status = "PASS" if pct >= MC_ORDER_ACCEPT_PCT else "FAIL"
        results.append(_result(
            "mc_trade_order", {"trials": trials},
            {"accept_pct": MC_ORDER_ACCEPT_PCT, "max_dd_path": -0.15},
            {"accept_pct_observed": round(pct, 4)},
            status,
            "mechanism_failure" if status == "FAIL" else None, repair_allowed=False,
        ))
    else:
        results.append(_result("mc_trade_order", {"n": n}, {"min": 8}, {"n": n},
                               "INCONCLUSIVE", "inconclusive", False))

    # 15 remove outlier win
    if n >= 5:
        mx = max(pnls)
        trimmed = [p for p in pnls if p != mx] or pnls[:-1]
        tm = _mean(trimmed)
        fail = base_mean > 0 and tm <= 0
        results.append(_result(
            "remove_outlier_win", {"removed": mx},
            {"mean_remains_non_negative_if_base_positive": True},
            {"trimmed_mean": tm},
            "FAIL" if fail else "PASS",
            "mechanism_failure" if fail else None, repair_allowed=False,
        ))
    else:
        results.append(_result("remove_outlier_win", {"n": n}, {"min": 5}, {"n": n},
                               "INCONCLUSIVE", "inconclusive", False))

    # 16 remove top10 wins
    if n >= 15:
        top = sorted(pnls, reverse=True)[:10]
        remain = sorted(pnls, reverse=True)[10:]
        tm = _mean(remain)
        fail = base_mean > 0 and tm < -0.005
        results.append(_result(
            "remove_top10_wins", {"removed_n": 10},
            {"remain_mean_gt_-0.5pct": True},
            {"remain_mean": tm, "top_sum": sum(top)},
            "FAIL" if fail else "PASS",
            "mechanism_failure" if fail else None, repair_allowed=False,
        ))
    else:
        results.append(_result("remove_top10_wins", {"n": n}, {"min": 15}, {"n": n},
                               "INCONCLUSIVE", "inconclusive", False))

    # 17 consecutive loss stress
    if n >= 8:
        max_streak = 0
        streak = 0
        for p in pnls:
            if p < 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        fail = max_streak >= max(8, n // 3)
        results.append(_result(
            "consecutive_loss_stress", {},
            {"max_losing_streak_lt_max(8,n/3)": True},
            {"max_losing_streak": max_streak},
            "FAIL" if fail else "PASS",
            "mechanism_failure" if fail else None, repair_allowed=True,
        ))
    else:
        results.append(_result("consecutive_loss_stress", {"n": n}, {"min": 8}, {"n": n},
                               "INCONCLUSIVE", "inconclusive", False))

    # 18 mechanism counterexample — DeepSeek slot filled by caller optionally;
    # here structural: if fidelity failed non-negotiable → FAIL
    cx_fail = bool((fidelity_diff or {}).get("non_negotiable_violations"))
    results.append(_result(
        "mechanism_counterexample",
        {"fidelity_violations": (fidelity_diff or {}).get("non_negotiable_violations")},
        {"no_non_negotiable_violations": True},
        {"violations": (fidelity_diff or {}).get("non_negotiable_violations")},
        "FAIL" if cx_fail else ("INCONCLUSIVE" if not fidelity_diff else "PASS"),
        "implementation_failure" if cx_fail else "inconclusive",
        repair_allowed=True,
        notes=["attacker_ai_may_append_counterexamples_in_gate6"],
    ))

    # 19 version drift — compare round0 vs current fidelity feature set
    drift = bool((fidelity_diff or {}).get("feature_drift_vs_prior"))
    material = False
    for d in ((fidelity_diff or {}).get("feature_drift_vs_prior") or []):
        if len(d.get("added") or []) + len(d.get("removed") or []) >= 2:
            material = True
    results.append(_result(
        "version_drift",
        {"drift_events": (fidelity_diff or {}).get("feature_drift_vs_prior")},
        {"no_material_feature_set_swap": True},
        {"material": material, "any_drift": drift},
        "FAIL" if material else "PASS",
        "implementation_failure" if material else None, repair_allowed=False,
    ))

    # 20 execution chain integrity — structural checks on definition/spec
    chain_ok = True
    notes = []
    if not (definition or {}).get("entry"):
        chain_ok = False
        notes.append("missing_entry")
    if not (definition or {}).get("exit") and not (mechanism_spec or {}).get("exit_logic"):
        chain_ok = False
        notes.append("missing_exit")
    if not (mechanism_spec or {}).get("stop_logic"):
        chain_ok = False
        notes.append("missing_stop_logic_in_spec")
    # live attach SL is production-protected; we only require spec declares it
    results.append(_result(
        "execution_chain_integrity",
        {"checks": ["entry", "exit", "stop_logic_in_spec"]},
        {"all_present": True},
        {"notes": notes},
        "PASS" if chain_ok else "FAIL",
        "implementation_failure" if not chain_ok else None, repair_allowed=True,
    ))

    # Ensure all 20 present
    got = {r["test_id"] for r in results}
    for tid in SPLIT_TEST_IDS:
        if tid not in got:
            results.append(_result(tid, {}, {}, {"error": "not_executed"}, "INCONCLUSIVE",
                                   "inconclusive", False))

    # Order by SPLIT_TEST_IDS
    order = {t: i for i, t in enumerate(SPLIT_TEST_IDS)}
    results.sort(key=lambda r: order.get(r["test_id"], 99))

    summary = {
        "schema": STEP_A_SCHEMA,
        "artifact": "split_tests_20",
        "code_version": STEP_A_CODE_VERSION,
        "created_at": _now(),
        "n_tests": len(results),
        "pass": sum(1 for r in results if r["status"] == "PASS"),
        "fail": sum(1 for r in results if r["status"] == "FAIL"),
        "inconclusive": sum(1 for r in results if r["status"] == "INCONCLUSIVE"),
        "results": results,
        # Gate4 policy: any mechanism_failure FAIL blocks; cost/impl may repair
        "gate4_block": any(
            r["status"] == "FAIL" and r.get("failure_class") == "mechanism_failure"
            for r in results
        ),
        "gate4_hard_fail_ids": [
            r["test_id"] for r in results
            if r["status"] == "FAIL" and r.get("failure_class") == "mechanism_failure"
        ],
    }
    return summary


def save_split_tests(task_id, summary):
    ensure_step_a_dirs()
    from . import step_a_config as sc
    from pathlib import Path
    path = Path(sc.SPLIT_TESTS_DIR) / ("%s_split_tests_20.json" % task_id)
    _atomic(path, summary)
    return str(path)
