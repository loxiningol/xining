#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Re-run strict four-review for crec CL 15m exhaustion fade.

Uses rejected pending DSL + fresh backtest evidence. On full pass, enqueues
pending human confirm via dual_engine_step_a_rereview (never auto-mount).
Output and logs use 第一次复核…第四次复核 — never R1/R2 shorthand.

  --force-three-ai   Skip 第二次/第三次复核 gate; run 第四次复核（三AI） anyway.
"""
from __future__ import print_function

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

STRATEGY_KEY = os.environ.get(
    "REREVIEW_KEY", "crec_cl_15m_exh_r55_z0p0_h20_t45_seed")
OUT_PATH = Path(
    os.environ.get("REREVIEW_OUT")
    or "/root/auto_trade/dual_engine/crec_cl15m_four_review_rerun.json"
)
DEFAULT_ARTIFACT_PATHS = (
    "/root/auto_trade/creation_recovery/artifacts/feature_tp/feature_tp_survivors.json",
    "/root/auto_trade/dual_engine/creation_recovery_20260802/feature_tp_survivors.json",
    "/root/auto_trade/creation_recovery/artifacts/feature_tp/cl_seed_submit.json",
)


def _pending_row_has_dsl(row):
    dsl = (row or {}).get("dsl") or {}
    return bool(dsl.get("entry_conditions") or dsl.get("exit_conditions"))


def _load_pending_item(key):
    pending_path = ROOT / "auto_trade" / "strategy_pending_human_confirm.json"
    if pending_path.is_file():
        data = json.loads(pending_path.read_text(encoding="utf-8"))
        for row in data.get("items") or []:
            if row.get("key") == key and _pending_row_has_dsl(row):
                return row
    artifact = os.environ.get("REREVIEW_ARTIFACT")
    paths = [artifact] if artifact else list(DEFAULT_ARTIFACT_PATHS)
    for p in paths:
        if not p:
            continue
        ap = Path(p)
        if not ap.is_file():
            continue
        blob = json.loads(ap.read_text(encoding="utf-8"))
        row = None
        if isinstance(blob, dict):
            for r in blob.get("rows") or blob.get("items") or []:
                if isinstance(r, dict) and (
                    r.get("key") == key or r.get("strategy_key") == key
                ):
                    row = r
                    break
        if row:
            dsl = dict(row.get("dsl") or {})
            item = {
                "key": key,
                "name": row.get("name") or dsl.get("name"),
                "symbol": row.get("symbol") or (dsl.get("supported_instruments") or [None])[0],
                "timeframe": row.get("timeframe") or dsl.get("timeframe") or "15m",
                "direction": row.get("direction") or dsl.get("direction"),
                "logic_brief": row.get("logic_brief") or row.get("thesis") or dsl.get("description"),
                "dsl": dsl,
                "_loaded_from": str(ap),
            }
            print("[rereview] loaded survivors row", ap, flush=True)
            return item
        dsl = blob.get("dsl") or blob.get("strategy_dsl") or blob
        if not isinstance(dsl, dict) or not dsl.get("entry_conditions"):
            nested = blob.get("item") or blob.get("candidate") or {}
            dsl = nested.get("dsl") or nested
        item = {
            "key": key,
            "name": blob.get("name") or dsl.get("name") or blob.get("title_zh"),
            "symbol": blob.get("symbol") or (dsl.get("supported_instruments") or [None])[0],
            "timeframe": blob.get("timeframe") or dsl.get("timeframe") or "15m",
            "direction": blob.get("direction") or dsl.get("direction"),
            "logic_brief": blob.get("logic_brief") or blob.get("thesis") or dsl.get("description"),
            "dsl": dsl,
            "_loaded_from": str(ap),
        }
        print("[rereview] loaded artifact", ap, flush=True)
        return item
    raise SystemExit("strategy not found in pending or recovery artifacts: %s" % key)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force-three-ai",
        action="store_true",
        help="跳过第二次/第三次复核门槛，强制执行第四次复核（三AI）",
    )
    args = parser.parse_args()
    force_three_ai = bool(args.force_three_ai or os.environ.get("FORCE_THREE_AI") == "1")

    import auto_trade_dual_engine_factory as dual
    import auto_trade_human_confirm_pipeline as pipeline
    import auto_trade_strategy_dsl as dsl_mod
    import auto_trade_ai_consensus as ai_mod
    from dual_engine_workflow_v2 import review_admission_v2 as adm
    from dual_engine_workflow_v2 import review_lexicon as lex
    from dual_engine_workflow_v2.fitness_engine import evaluate_multi_objective

    def _stage_label(review_row):
        n = int((review_row or {}).get("review_n") or 0)
        return lex.review_label(n) or "复核"

    def _reasons_zh(review_row):
        raw = list((review_row or {}).get("reject_reasons") or [])
        return [lex.scrub(x) for x in raw]

    item = _load_pending_item(STRATEGY_KEY)
    dsl = dict(item.get("dsl") or {})
    symbol = item.get("symbol") or (dsl.get("supported_instruments") or [None])[0]
    timeframe = item.get("timeframe") or dsl.get("timeframe") or "15m"

    validate_error = None
    try:
        dsl_mod.validate_strategy(dsl)
    except Exception as exc:
        validate_error = str(exc)

    print("[rereview] backtest", symbol, timeframe, STRATEGY_KEY, flush=True)
    bt = dual._backtest(dsl, symbol, timeframe, friction_name="observed_base")
    trades = list(bt.get("trades") or [])
    metrics = dual._metrics_from_trades(trades)
    metrics.update({
        "symbol": symbol,
        "timeframe": timeframe,
        "key": STRATEGY_KEY,
        "name": item.get("name") or dsl.get("name"),
        "direction": dsl.get("direction") or item.get("direction"),
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
        dsl, validate_error=validate_error, lookahead_ok=True,
        require_density=False,
    )
    r2 = adm.review2_single_symbol_stability(metrics=metrics, trades=trades)
    r3 = adm.review3_matrix_outlier(gate2_fitness=gate2, soft_pass=False)

    print("[rereview]", _stage_label(r1), r1.get("pass"), _reasons_zh(r1), flush=True)
    print("[rereview]", _stage_label(r2), r2.get("pass"), _reasons_zh(r2), r2.get("metrics"), flush=True)
    print("[rereview]", _stage_label(r3), r3.get("pass"), _reasons_zh(r3), flush=True)

    ai = None
    r4 = None
    push = None
    can_run_three_ai = bool(
        r1.get("pass") and r2.get("pass") and r3.get("pass")
    ) or force_three_ai
    if force_three_ai and not (r2.get("pass") and r3.get("pass")):
        print("[rereview] 操作员强制：跳过第二次/第三次复核，直接进入第四次复核（三AI）",
              flush=True)

    if can_run_three_ai:
        n = int(metrics.get("trades") or len(trades) or 0)
        span_days, span_source = ai_mod.resolve_backtest_observation_span_days(
            dsl,
            {"symbol": symbol, "timeframe": timeframe, "trades": n},
            trades=trades,
        )
        win_only = [float(t.get("pnl_ratio") or 0.0) for t in trades if float(t.get("pnl_ratio") or 0.0) > 0]
        mean_win = (sum(win_only) / float(len(win_only))) if win_only else None
        evidence = {
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy_key": STRATEGY_KEY,
            "trades": n,
            "span_days": span_days,
            "observation_days": span_days,
            "span_source": span_source,
            "weekly_opens_require_2y": True,
            "frequency_method": "backtest_2y_fill_rate_proxy",
            "trades_list": trades,
            "safety_metrics": {
                "trades": n,
                "span_days": span_days,
                "observation_days": span_days,
                "trades_list": trades,
                "mean_net": metrics.get("mean_net"),
                "win_rate": metrics.get("win_rate_pct"),
                "mean_net_win_only_pct": (mean_win * 100.0) if mean_win is not None and abs(mean_win) <= 1.5 else mean_win,
            },
            "gate2_fitness": gate2,
            "admission": {"r1": r1, "r2": r2, "r3": r3},
            "title_zh": item.get("name"),
            "mechanism_family": "exhaustion_fade",
        }
        candidate = dict(dsl)
        candidate["title"] = item.get("name")
        candidate["mechanism_family"] = "exhaustion_fade"
        print("[rereview] running 第四次复核（三AI理论复核）...", flush=True)
        t0 = time.time()
        ai = ai_mod.theoretical_review_all(candidate, evidence) or {}
        print("[rereview] 第四次复核 elapsed", round(time.time() - t0, 1),
              "approved", ai.get("approved"), flush=True)
        r4 = adm.review4_three_ai(ai_review=ai)
        print("[rereview]", _stage_label(r4), r4.get("pass"), _reasons_zh(r4), flush=True)

        if r4.get("pass") and r1.get("pass") and r2.get("pass") and r3.get("pass"):
            ai_review = dict(ai)
            ai_review["four_review_admission"] = {
                "schema": "qiyu_four_review_admission_v2",
                "profile": adm.PROFILE,
                "pass": True,
                "reviews": {"r1": r1, "r2": r2, "r3": r3, "r4": r4},
                "pipeline_handoff": "manual_four_review_rerun",
                "strategy_key": STRATEGY_KEY,
            }
            push = pipeline.ingest_and_screen(
                {
                    "dsl": dsl,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "thesis": item.get("logic_brief") or dsl.get("description"),
                    "direction": dsl.get("direction"),
                },
                source="dual_engine_step_a_rereview",
                ai_review=ai_review,
                require_ai_review=True,
            )
            print("[rereview] pending push", json.dumps(
                {"ok": push.get("ok"), "reason": push.get("reason"),
                 "pushed": push.get("pushed"), "key": push.get("key")},
                ensure_ascii=False), flush=True)
        elif r4.get("pass") and force_three_ai:
            print("[rereview] 第四次复核通过，但第二次/第三次复核未过 — 不写入 pending（需全阶段通过才待签发）",
                  flush=True)
    else:
        r4 = adm.review4_three_ai(ai_review={"approved": False})
        print("[rereview] 未进入第四次复核 — 第一次复核未通过", flush=True)

    stage_summary = [
        {
            "stage": lex.REVIEW_1,
            "scope": lex.REVIEW_1_SCOPE,
            "pass": bool(r1.get("pass")),
            "reject_reasons_zh": _reasons_zh(r1),
        },
        {
            "stage": lex.REVIEW_2,
            "scope": lex.REVIEW_2_SCOPE,
            "pass": bool(r2.get("pass")),
            "reject_reasons_zh": _reasons_zh(r2),
            "metrics": r2.get("metrics"),
        },
        {
            "stage": lex.REVIEW_3,
            "scope": lex.REVIEW_3_SCOPE,
            "pass": bool(r3.get("pass")),
            "reject_reasons_zh": _reasons_zh(r3),
        },
        {
            "stage": lex.REVIEW_4,
            "scope": lex.REVIEW_4_SCOPE,
            "pass": bool((r4 or {}).get("pass")),
            "reject_reasons_zh": _reasons_zh(r4),
        },
    ]

    reached = bool((push or {}).get("ok"))
    out = {
        "schema": "qiyu_four_review_rerun_v1",
        "ok": reached,
        "strategy_key": STRATEGY_KEY,
        "name": item.get("name"),
        "symbol": symbol,
        "timeframe": timeframe,
        "metrics": metrics,
        "gate2_fitness": gate2,
        "reviews": {"r1": r1, "r2": r2, "r3": r3, "r4": r4},
        "stage_summary_zh": stage_summary,
        "ai": ai,
        "force_three_ai": force_three_ai,
        "loaded_from": item.get("_loaded_from"),
        "pending_push": push,
        "reached_human_confirm": reached,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str) + "\n",
                        encoding="utf-8")
    print("[rereview] wrote", OUT_PATH, flush=True)
    if force_three_ai and ai is not None:
        return 0 if ai.get("approved") else 3
    return 0 if reached else 2


if __name__ == "__main__":
    sys.exit(main())
