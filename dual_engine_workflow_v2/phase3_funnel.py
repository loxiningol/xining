# -*- coding: utf-8 -*-
"""Phase-3 three-level funnel orchestrator.

L1 micro-screen → L2 full-BT fitness + Pareto → L3 null hypothesis + WF.

Fail-closed: any level reject stops promotion; rejection stage + reasons logged.
Does not alter Gate0–7 public signatures; callers attach results additively.
Protective 0.9% SL unchanged; no ADA migrate; no auto-mount.
"""
from __future__ import print_function

import time

from .funnel_l1_micro_screen import run_micro_screen, STAGE as L1_STAGE
from .funnel_l2_pareto import evaluate_l2_survivor, pareto_front, STAGE as L2_STAGE
from .funnel_l3_null_hypothesis import (
    evaluate_null_hypothesis,
    walk_forward_windows,
    STAGE as L3_STAGE,
)


FUNNEL_VERSION = "phase3_funnel_v1"


def run_phase3_funnel(
    definition=None,
    frame=None,
    trades=None,
    base_metrics=None,
    backtest_fn=None,
    full_backtest_fn=None,
    candidate_id=None,
    seed=42,
    skip_l1=False,
    skip_l3_frame_tests=False,
    l1_trades=None,
):
    """Execute L1→L2→L3 for a single candidate (or precomputed trades).

    full_backtest_fn() → {trades, metrics} used when trades not supplied.
    backtest_fn(frame, definition) used by L1 sample BT and L3 frame tests.
    """
    t0 = time.perf_counter()
    log = {
        "funnel_version": FUNNEL_VERSION,
        "levels": {},
        "rejected_at": None,
        "reject_reasons": [],
        "pass": False,
        "fail_closed": True,
        "protective_sl_pct": 0.009,
        "ada_migrate": False,
        "auto_mount": False,
    }

    # ---- Level 1 ----
    if skip_l1:
        l1 = {
            "pass": True, "stage": L1_STAGE, "skipped": True,
            "reject_reasons": [], "wall_time_ms": 0.0,
        }
    else:
        l1 = run_micro_screen(
            definition=definition,
            frame=frame,
            backtest_fn=backtest_fn,
            trades=l1_trades if l1_trades is not None else (
                None if (frame is not None or backtest_fn is not None) else trades
            ),
            seed=seed,
        )
    log["levels"]["l1_micro_screen"] = l1
    if not l1.get("pass"):
        log["rejected_at"] = L1_STAGE
        log["reject_reasons"] = list(l1.get("reject_reasons") or ["l1_fail"])
        log["wall_time_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
        return log

    # ---- Level 2 (full BT + fitness + Pareto singleton) ----
    used_trades = trades
    used_metrics = base_metrics
    if used_trades is None and full_backtest_fn is not None:
        try:
            full = full_backtest_fn() or {}
            used_trades = list(full.get("trades") or [])
            used_metrics = full.get("metrics") or full.get("base_metrics") or {}
        except Exception as exc:
            log["levels"]["l2_full_bt_pareto"] = {
                "pass": False,
                "stage": L2_STAGE,
                "reject_reasons": ["full_backtest_error:%s" % exc],
                "fail_closed": True,
            }
            log["rejected_at"] = L2_STAGE
            log["reject_reasons"] = ["full_backtest_error:%s" % exc]
            log["wall_time_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
            return log
    if used_trades is None:
        used_trades = []

    l2 = evaluate_l2_survivor(
        used_trades, base_metrics=used_metrics, candidate_id=candidate_id,
    )
    log["levels"]["l2_full_bt_pareto"] = l2
    log["trades"] = used_trades
    log["base_metrics"] = used_metrics
    if not l2.get("pass"):
        log["rejected_at"] = L2_STAGE
        log["reject_reasons"] = list(l2.get("reject_reasons") or ["l2_fail"])
        log["wall_time_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
        return log

    # ---- Level 3 ----
    l3 = evaluate_null_hypothesis(
        definition=definition,
        frame=frame,
        backtest_fn=backtest_fn,
        trades=used_trades,
        seed=seed,
        skip_frame_tests=skip_l3_frame_tests or frame is None or backtest_fn is None,
    )
    log["levels"]["l3_null_hypothesis"] = l3
    log["walk_forward"] = l3.get("walk_forward")
    if not l3.get("pass"):
        log["rejected_at"] = L3_STAGE
        log["reject_reasons"] = list(l3.get("reject_reasons") or ["l3_fail"])
        log["wall_time_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
        return log

    log["pass"] = True
    log["rejected_at"] = None
    log["reject_reasons"] = []
    log["wall_time_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
    return log


def apply_pareto_to_batch(candidates):
    """Batch helper for creation-factory screening (additive)."""
    return pareto_front(candidates, require_fitness_pass=True)


def phase3_wf_from_trades(trades, folds=10):
    """Public helper — Gate3-aligned WF with Calmar≥1.0 + positive expectancy."""
    return walk_forward_windows(trades, folds=folds)
