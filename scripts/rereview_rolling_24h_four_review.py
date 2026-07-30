#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-shot four-review of rolling_24h_sweep_clean_v1 under admission_v2.

Uses the last Gate2-reaching auto_driver evidence (n/payoff/calmar/WR).
Does NOT mount and does NOT --confirm. Optionally runs live 三AI if R1–R3 pass.
"""
from __future__ import print_function

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

PACK = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/strategy_rolling_24h_sweep_clean_v1.json")
RUN = Path(
    sys.argv[2]
    if len(sys.argv) > 2
    else "/root/auto_trade/dual_engine/workflow_v2/auto_driver_runs/20260730_132856"
)
FORCE_AI = "--force-ai" in sys.argv
OUT = Path(
    os.environ.get("REREVIEW_OUT")
    or "/root/auto_trade/dual_engine/rolling_24h_four_review_rereview.json"
)


def _load_evidence(run):
    fc = None
    for p in sorted(run.glob("iter_*/failure_context.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        g2 = d.get("gate2_fitness") or {}
        if g2.get("sample_size") or g2.get("payoff_ratio") is not None:
            fc = d
            break
    if fc is None:
        raise SystemExit("no gate2 evidence in %s" % run)
    g2 = dict(fc.get("gate2_fitness") or {})
    # Prefer matrix/primary fitness as stability sample (legacy L1 fields often empty
    # once the run entered Gate2 repair).
    metrics = {
        "trades": int(g2.get("sample_size") or 0),
        "win_rate_pct": g2.get("win_rate_pct"),
        "mean_net": g2.get("mean_net"),
        "payoff_ratio": g2.get("payoff_ratio"),
        "calmar": g2.get("calmar"),
        "expectancy_factor": g2.get("expectancy_factor"),
        "worst5_loss_share": g2.get("worst5_loss_share"),
    }
    # Derive a conservative mean_net proxy when missing: expectancy_factor * unit risk
    # is not dollar-mean; if calmar < 0 treat mean_net as non-positive.
    if metrics["mean_net"] is None:
        try:
            cal = float(g2.get("calmar"))
            # Negative Calmar ⇒ equity path destroyed ⇒ mean_net not positive.
            metrics["mean_net"] = -0.01 if cal < 0 else None
        except Exception:
            metrics["mean_net"] = None
    l0 = {}
    # Recover L0 from any seed that recorded density
    for p in sorted(run.glob("iter_*/*seed*_result.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        r = d.get("result") or d
        l0 = ((r.get("phase3_funnel") or {}).get("l0_density") or {})
        if l0:
            break
    return fc, g2, metrics, l0


def main():
    from dual_engine_workflow_v2 import review_admission_v2 as adm
    from dual_engine_workflow_v2 import review_lexicon as lex
    import auto_trade_strategy_dsl as dsl_mod

    pack = json.loads(PACK.read_text(encoding="utf-8"))
    spec = pack.get("mechanism_spec") or {}
    direction = str((pack.get("meta") or {}).get("direction") or "long").lower()
    dsl = pack.get("dsl_short" if direction == "short" else "dsl_long") or pack.get("dsl") or {}
    title = (
        (pack.get("meta") or {}).get("title_zh")
        or spec.get("display_title_zh")
        or spec.get("mechanism_family")
    )
    print("PACK", PACK)
    print("TITLE", title)
    print("FAMILY", spec.get("mechanism_family"))
    print("EVIDENCE_RUN", RUN)

    validate_error = None
    try:
        dsl_mod.validate_strategy(dict(dsl))
    except Exception as exc:
        validate_error = str(exc)

    fc, g2, metrics, l0 = _load_evidence(RUN)
    print("METRICS", json.dumps(metrics, ensure_ascii=False))
    print("G2_FAILED", g2.get("failed_checks"))

    r1 = adm.review1_syntax_assert_density(
        definition=dsl, validate_error=validate_error, l0=l0, require_density=False,
    )
    r2 = adm.review2_single_symbol_stability(metrics=metrics, trades=[])
    r3 = adm.review3_matrix_outlier(gate2_fitness=g2, soft_pass=True)

    print("R1", r1.get("pass"), r1.get("reject_reasons"), "soft_density", (r1.get("density_detail") or {}).get("soft_passed"))
    print("R2", r2.get("pass"), r2.get("reject_reasons"), r2.get("metrics"))
    print("R3", r3.get("pass"), "soft", r3.get("soft_passed"), "hard", r3.get("hard_pass"), r3.get("failed_checks"))

    ai = None
    r4 = None
    hc = None
    reached_human = False
    if r1.get("pass") and r2.get("pass") and r3.get("pass") or FORCE_AI:
        print("RUNNING_3AI", flush=True)
        import auto_trade_ai_consensus as ai_mod
        # Build minimal safety evidence for voters
        evidence = {
            "backtest": {
                "trades": metrics.get("trades"),
                "win_rate_pct": metrics.get("win_rate_pct"),
                "payoff_ratio": metrics.get("payoff_ratio"),
                "calmar": metrics.get("calmar"),
                "expectancy_factor": metrics.get("expectancy_factor"),
                "mean_net": metrics.get("mean_net"),
                "worst5_loss_share": metrics.get("worst5_loss_share"),
            },
            "gate2_fitness": g2,
            "admission": {"r1": r1, "r2": r2, "r3": r3},
            "title_zh": title,
            "mechanism_family": spec.get("mechanism_family"),
        }
        candidate = dict(dsl)
        candidate["key"] = dsl.get("key") or "rolling_24h_sweep_clean_v1_eth_long"
        candidate["title"] = title
        candidate["mechanism_family"] = spec.get("mechanism_family")
        t0 = time.time()
        ai = ai_mod.theoretical_review_all(candidate, evidence)
        print("3AI_ELAPSED", round(time.time() - t0, 1), "approved", (ai or {}).get("approved"))
        print("3AI_WR_AVG", (ai or {}).get("ai_theoretical_wr_avg"),
              "WIN_MEAN_AVG", (ai or {}).get("ai_theoretical_mean_net_avg"))
        r4 = adm.review4_three_ai(ai_review=ai)
        print("R4", r4.get("pass"), r4.get("reject_reasons"))
        if r1.get("pass") and r2.get("pass") and r3.get("pass") and r4.get("pass"):
            hc = adm.human_confirm_gate(pending_ok=True, human_confirmed=False)
            reached_human = True
            print("HUMAN_CONFIRM_READY", True, "(never auto-mount)")
        else:
            print("HUMAN_CONFIRM_READY", False)
    else:
        r4 = adm.review4_three_ai(ai_review={"approved": False})
        print("SKIP_3AI (hard fail before R4)")
        print("HUMAN_CONFIRM_READY", False)

    pipe = [
        ("一", "第一次复核", "✓" if r1.get("pass") else "✗"),
        ("二", "第二次复核", "✓" if r2.get("pass") else "✗"),
        ("三", "第三次复核", "✓" if r3.get("pass") else "✗"),
        ("四", "第四次复核", "✓" if (r4 or {}).get("pass") else ("·" if r4 is None else "✗")),
        ("签", "人工确认签发", "⏳" if reached_human else "·"),
    ]
    compact = ["%s:%s" % (m, s) for m, _, s in pipe]
    print("PIPE", " · ".join(compact))

    out = {
        "schema": "qiyu_four_review_rereview_v1",
        "ok": bool(reached_human),
        "title_zh": title,
        "family": spec.get("mechanism_family"),
        "evidence_run": str(RUN),
        "metrics": metrics,
        "gate2_failed_checks": g2.get("failed_checks"),
        "reviews": {"r1": r1, "r2": r2, "r3": r3, "r4": r4, "human": hc},
        "ai": ai,
        "pipeline_compact": compact,
        "reached_human_confirm": reached_human,
        "note_zh": (
            "四复核通过，已可进入 Wx 人工确认签发（永不自动上线）。"
            if reached_human
            else "未过关：%s"
            % (
                "第二次复核未过（胜率/样本收益稳定性）"
                if not r2.get("pass")
                else (
                    "第一次复核未过"
                    if not r1.get("pass")
                    else (
                        "第四次复核未过（三AI）"
                        if r4 and not r4.get("pass")
                        else "未达人工确认"
                    )
                )
            )
        ),
        "lexicon": lex.pipeline_stage_list(),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("WROTE", OUT)
    return 0 if True else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
