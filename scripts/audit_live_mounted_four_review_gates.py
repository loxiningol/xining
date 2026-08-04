#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit every live auto-trading strategy against strict four-review hard gates.

Enumerates all enabled + allow_auto_open daemon mounts, backtests each strategy,
runs 第一次复核…第四次复核（三AI） with production thresholds. None skipped.
"""
from __future__ import print_function

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

OUT_PATH = Path(
    os.environ.get("LIVE_FOUR_REVIEW_AUDIT_OUT")
    or "/root/auto_trade/dual_engine/live_mounted_four_review_audit.json"
)
PROGRESS_PATH = Path(
    os.environ.get("LIVE_FOUR_REVIEW_AUDIT_PROGRESS")
    or "/root/auto_trade/dual_engine/live_mounted_four_review_audit_progress.json"
)


def _write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _stage_label(review_row, lex):
    n = int((review_row or {}).get("review_n") or 0)
    return lex.review_label(n) or "复核"


def _reasons_zh(review_row, lex):
    raw = list((review_row or {}).get("reject_reasons") or [])
    return [lex.scrub(x) for x in raw]


def _first_fail_stage(r1, r2, r3, r4, lex):
    for row in (r1, r2, r3, r4):
        if row and not row.get("pass"):
            return _stage_label(row, lex)
    return None


def audit_one(assignment, *, force_three_ai=True, run_three_ai=True):
    import auto_trade_dual_engine_factory as dual
    import auto_trade_strategy_dsl as dsl_mod
    import auto_trade_strategy_dynamic_optimizer as opt
    import auto_trade_ai_consensus as ai_mod
    from dual_engine_workflow_v2 import review_admission_v2 as adm
    from dual_engine_workflow_v2 import review_lexicon as lex
    from dual_engine_workflow_v2.fitness_engine import evaluate_multi_objective
    from freeze_live_strategy_entries import discover_live_assignments

    key = assignment["strategy_key"]
    symbol = assignment["symbol"]
    timeframe = assignment["timeframe"]
    loaded = opt.load_definition(key)
    if not loaded or not loaded.get("definition"):
        return {
            "assignment_id": assignment["assignment_id"],
            "strategy_key": key,
            "symbol": symbol,
            "timeframe": timeframe,
            "ok": False,
            "error": "definition_missing",
            "all_pass": False,
        }

    dsl = dict(loaded["definition"])
    validate_error = None
    try:
        dsl_mod.validate_strategy(dsl)
    except Exception as exc:
        validate_error = str(exc)

    bt = dual._backtest(dsl, symbol, timeframe, friction_name="observed_base")
    trades = list(bt.get("trades") or [])
    metrics = dual._metrics_from_trades(trades)
    metrics.update({
        "symbol": symbol,
        "timeframe": timeframe,
        "key": key,
        "name": dsl.get("name"),
        "direction": dsl.get("direction"),
    })

    fit = evaluate_multi_objective(trades, base_metrics=metrics, enforce=True)
    fm = fit.get("metrics") or {}
    gate2 = {
        "pass": bool(fit.get("pass")),
        "failed_checks": list(fit.get("failed_checks") or []),
        "sample_size": fm.get("n") or metrics.get("trades"),
        "win_rate_pct": fm.get("win_rate_pct") or metrics.get("win_rate_pct"),
        "mean_net": fm.get("mean_net") or metrics.get("mean_net"),
        "payoff_ratio": fm.get("payoff_ratio"),
        "calmar": fm.get("calmar"),
        "expectancy_factor": fm.get("expectancy_factor"),
        "worst5_loss_share": fm.get("worst5_loss_share"),
    }

    r1 = adm.review1_syntax_assert_density(
        dsl, validate_error=validate_error, lookahead_ok=True, require_density=False,
    )
    r2 = adm.review2_single_symbol_stability(metrics=metrics, trades=trades)
    r3 = adm.review3_matrix_outlier(gate2_fitness=gate2, soft_pass=False)

    ai = None
    r4 = None
    can_three_ai = bool(
        r1.get("pass") and r2.get("pass") and r3.get("pass")
    ) or force_three_ai

    # Always compute honest weekly density (even if 3AI skipped).
    n = int(metrics.get("trades") or len(trades) or 0)
    bars_used = metrics.get("bars_used") or metrics.get("n_bars")
    freq_evidence = {
        "symbol": symbol,
        "timeframe": timeframe,
        "trades": n,
        "trades_list": trades,
        "bars_used": bars_used,
        "safety_metrics": {
            "trades": n,
            "trades_list": trades,
            "bars_used": bars_used,
        },
    }
    honest_freq = ai_mod.compute_weekly_open_frequency(
        n,
        candidate=dsl,
        evidence=freq_evidence,
        trades=trades,
        source="audit_live_mounted",
    )
    span_days = honest_freq.get("span_days")
    span_source = honest_freq.get("span_source")

    if run_three_ai and can_three_ai:
        win_only = [
            float(t.get("pnl_ratio") or 0.0) for t in trades
            if float(t.get("pnl_ratio") or 0.0) > 0
        ]
        mean_win = (sum(win_only) / float(len(win_only))) if win_only else None
        evidence = {
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy_key": key,
            "trades": n,
            "span_days": span_days,
            "observation_days": span_days,
            "span_source": span_source,
            "bars_used": bars_used,
            "weekly_opens_require_2y": True,
            "trades_list": trades,
            "safety_metrics": {
                "trades": n,
                "span_days": span_days,
                "observation_days": span_days,
                "bars_used": bars_used,
                "trades_list": trades,
                "mean_net": metrics.get("mean_net"),
                "win_rate": metrics.get("win_rate_pct"),
                "mean_net_win_only_pct": (
                    (mean_win * 100.0) if mean_win is not None and abs(mean_win) <= 1.5
                    else mean_win
                ),
            },
            "gate2_fitness": gate2,
            "admission": {"r1": r1, "r2": r2, "r3": r3},
            "title_zh": dsl.get("name") or key,
        }
        candidate = dict(dsl)
        candidate["title"] = dsl.get("name") or key
        ai = ai_mod.theoretical_review_all(candidate, evidence) or {}
        r4 = adm.review4_three_ai(ai_review=ai)
    elif run_three_ai:
        r4 = adm.review4_three_ai(ai_review={"approved": False})
    else:
        r4 = {"review_n": 4, "pass": None, "skipped": True}

    stage_summary = [
        {
            "stage": lex.REVIEW_1,
            "pass": bool(r1.get("pass")),
            "reject_reasons_zh": _reasons_zh(r1, lex),
        },
        {
            "stage": lex.REVIEW_2,
            "pass": bool(r2.get("pass")),
            "reject_reasons_zh": _reasons_zh(r2, lex),
            "metrics": r2.get("metrics"),
        },
        {
            "stage": lex.REVIEW_3,
            "pass": bool(r3.get("pass")),
            "reject_reasons_zh": _reasons_zh(r3, lex),
            "failed_checks": gate2.get("failed_checks"),
        },
        {
            "stage": lex.REVIEW_4,
            "pass": bool((r4 or {}).get("pass")) if r4 else None,
            "reject_reasons_zh": _reasons_zh(r4, lex) if r4 else [],
            "ai_wr_avg": (ai or {}).get("ai_theoretical_wr_avg"),
            "ai_weekly_avg": (ai or {}).get("ai_theoretical_weekly_opens_avg"),
            "ai_by_provider": (ai or {}).get("ai_theoretical_wr_by_provider"),
        },
    ]

    all_pass = bool(
        r1.get("pass") and r2.get("pass") and r3.get("pass")
        and (r4 or {}).get("pass")
    )
    return {
        "assignment_id": assignment["assignment_id"],
        "strategy_key": key,
        "strategy_name": dsl.get("name") or key,
        "symbol": symbol,
        "timeframe": timeframe,
        "daemon_config": assignment.get("config_file"),
        "kind": loaded.get("kind"),
        "metrics": metrics,
        "gate2_fitness": gate2,
        "reviews": {"r1": r1, "r2": r2, "r3": r3, "r4": r4},
        "stage_summary_zh": stage_summary,
        "first_fail_stage": _first_fail_stage(r1, r2, r3, r4, lex),
        "all_pass": all_pass,
        "ai": ai,
        "honest_weekly": {
            "expected_weekly_fills": honest_freq.get("expected_weekly_fills"),
            "span_days": honest_freq.get("span_days"),
            "span_source": honest_freq.get("span_source"),
            "sample_2y_ok": honest_freq.get("sample_2y_ok"),
            "method": honest_freq.get("method"),
            "n_trades": honest_freq.get("n_trades"),
        },
        "thresholds": {
            "min_trades": adm.MIN_TRADES,
            "min_win_rate_pct": adm.MIN_WIN_RATE_PCT,
            "r3_soft_pass": adm.R3_SOFT_PASS_ON_LEGACY_FAIL,
            "min_theoretical_wr": ai_mod.MIN_THEORETICAL_WR,
            "min_theoretical_weekly_opens": ai_mod.MIN_THEORETICAL_WEEKLY_OPENS,
        },
        "audited_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-three-ai",
        action="store_true",
        help="Skip 第四次复核（三AI）— default runs 3AI on every mount",
    )
    parser.add_argument(
        "--require-prior-pass-for-three-ai",
        action="store_true",
        help="Only run 三AI when 第一至第三次复核 all pass (not recommended for full audit)",
    )
    args = parser.parse_args()

    from freeze_live_strategy_entries import discover_live_assignments
    from dual_engine_workflow_v2 import review_lexicon as lex

    assignments = discover_live_assignments()
    if not assignments:
        print("[audit] no live assignments found", flush=True)
        return 1

    force_three_ai = not args.require_prior_pass_for_three_ai
    run_three_ai = not args.no_three_ai

    print("[audit] live mounts:", len(assignments), flush=True)
    rows = []
    passed = []
    failed = []
    errors = []

    for index, assignment in enumerate(assignments, start=1):
        aid = assignment["assignment_id"]
        print("[audit] (%d/%d) %s" % (index, len(assignments), aid), flush=True)
        t0 = time.time()
        try:
            row = audit_one(
                assignment,
                force_three_ai=force_three_ai,
                run_three_ai=run_three_ai,
            )
        except Exception as exc:
            row = {
                "assignment_id": aid,
                "strategy_key": assignment.get("strategy_key"),
                "symbol": assignment.get("symbol"),
                "timeframe": assignment.get("timeframe"),
                "ok": False,
                "all_pass": False,
                "error": str(exc),
                "traceback": traceback.format_exc()[-1200:],
            }
        row["elapsed_sec"] = round(time.time() - t0, 1)
        rows.append(row)
        if row.get("error"):
            errors.append(row)
        elif row.get("all_pass"):
            passed.append(row)
        else:
            failed.append(row)
        print(
            "[audit]",
            aid,
            "all_pass" if row.get("all_pass") else "FAIL",
            row.get("first_fail_stage") or row.get("error"),
            "elapsed",
            row.get("elapsed_sec"),
            flush=True,
        )
        _write_json(PROGRESS_PATH, {
            "schema": "live_mounted_four_review_audit_progress_v1",
            "done": index,
            "total": len(assignments),
            "last_assignment_id": aid,
            "passed_count": len(passed),
            "failed_count": len(failed),
            "error_count": len(errors),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

    summary = {
        "total": len(assignments),
        "all_pass_count": len(passed),
        "fail_count": len(failed),
        "error_count": len(errors),
        "passed_keys": [r.get("strategy_key") for r in passed],
        "failed_by_stage": {},
    }
    for row in failed:
        stage = row.get("first_fail_stage") or "未知"
        summary["failed_by_stage"].setdefault(stage, []).append(
            row.get("strategy_key"))

    out = {
        "schema": "live_mounted_four_review_audit_v1",
        "ok": len(errors) == 0,
        "summary": summary,
        "thresholds_note_zh": (
            "硬门槛：第一次复核语法/断言；第二次复核 trades≥%s WR≥50%% mean_net>0；"
            "第三次复核 Gate2/worst5；第四次复核三AI WR≥65%% 周开仓≥0.5 三票齐投"
        ),
        "assignments": rows,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_json(OUT_PATH, out)
    print("[audit] wrote", OUT_PATH, flush=True)
    print("[audit] PASS", len(passed), "FAIL", len(failed), "ERR", len(errors), flush=True)
    for row in failed:
        print(
            " FAIL",
            row.get("strategy_key"),
            "→",
            row.get("first_fail_stage"),
            row.get("stage_summary_zh", [{}])[-2 if run_three_ai else -1],
            flush=True,
        )
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
