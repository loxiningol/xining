#!/usr/bin/env python3
"""Forensic extract: NO_DIRECTIONAL_EFFECT from research_ledger + probe replay."""
from __future__ import print_function

import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

RUN_ID = os.environ.get("FORENSIC_RUN_ID", "trend1")
BRIEF = os.environ.get("FORENSIC_BRIEF", "均线金叉做多")
SYMBOL = "ETH-USDT-SWAP"
TF = "5m"
DIRECTION = "long"


def _rets_stats(rets):
    if not rets:
        return {"n": 0, "all_nan": False, "all_zero": False}
    vals = list(rets)
    n = len(vals)
    n_nan = sum(1 for x in vals if x is None or (isinstance(x, float) and math.isnan(x)))
    finite = [float(x) for x in vals if x is not None and not (isinstance(x, float) and math.isnan(x))]
    n_zero = sum(1 for x in finite if abs(x) < 1e-15)
    return {
        "n": n,
        "n_nan": n_nan,
        "n_finite": len(finite),
        "n_zero": n_zero,
        "all_nan": n_nan == n,
        "all_zero": bool(finite) and n_zero == len(finite),
        "mean": (sum(finite) / len(finite)) if finite else None,
        "min": min(finite) if finite else None,
        "max": max(finite) if finite else None,
    }


def _load_ledger(run_id):
    path = ROOT / "auto_trade" / "dual_engine" / "research_ledger" / ("%s.jsonl" % run_id)
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    return rows, path


def _ledger_nde(rows):
    probes = [r for r in rows if r.get("event_type") == "probe" and r.get("research_state") == "NO_DIRECTIONAL_EFFECT"]
    summary = next((r for r in rows if r.get("event_type") == "research_discovery_summary"), {})
    lineage = [x for x in (summary.get("failure_lineage") or []) if x.get("research_state") == "NO_DIRECTIONAL_EFFECT"]
    learning = [r for r in rows if r.get("event_type") == "learning_update" and r.get("fail_stage") == "NO_DIRECTIONAL_EFFECT"]
    return probes, lineage, learning, summary


def _replay_probes(target_ids=None, max_hyp=24):
    from dual_engine_workflow_v2 import creation_blueprint as cb
    from dual_engine_workflow_v2 import research_discovery as rd
    from dual_engine_workflow_v2.research_discovery import build_hypothesis_population
    from dual_engine_workflow_v2 import probe_protocol as probes

    m = cb._load_matrix(SYMBOL, TF)
    cc = rd.compile_research_contract(BRIEF, SYMBOL, TF, {}, direction=DIRECTION)
    pop = build_hypothesis_population(
        BRIEF, SYMBOL, TF, m.get("matrix"), m.get("fwd"),
        max_mechanisms=int(os.environ.get("QIYU_MAX_MECHANISMS", "6")),
        run_id="forensic_%s" % RUN_ID,
        candles=m.get("candles"),
        research_contract=cc,
        skip_alpha_discovery=True,
    )
    hyps = list(pop.get("hypotheses") or [])
    if target_ids:
        want = set(target_ids)
        hyps = [h for h in hyps if h.get("hypothesis_id") in want]
    hyps = hyps[:max_hyp]

    replay = []
    for h in hyps:
        pr = probes.probe_hypothesis(
            h, m.get("matrix"), m.get("fwd"), candles=m.get("candles"),
            symbol=SYMBOL, timeframe=TF,
        )
        if pr.get("research_state") != "NO_DIRECTIONAL_EFFECT":
            continue
        best = pr.get("best") or {}
        rets = best.get("trade_returns") or []
        terms = best.get("terms") or []
        factors = [t.get("factor") for t in terms if t.get("factor")]
        if not factors:
            factors = [best.get("factor")] if best.get("factor") else list(h.get("factor_hints") or h.get("observable_proxy") or [])
        replay.append({
            "hypothesis_id": h.get("hypothesis_id"),
            "family": h.get("family"),
            "mechanism_id": h.get("mechanism_id"),
            "factor_hints": list(h.get("factor_hints") or h.get("observable_proxy") or []),
            "injected_factors": factors,
            "terms": terms,
            "best_event": best.get("event_id"),
            "best_factor": best.get("factor"),
            "best_side": best.get("side"),
            "n_raw_triggers": best.get("n_raw_triggers"),
            "n_independent_events": best.get("n_independent_events"),
            "win_rate": best.get("win_rate"),
            "win_rate_pct": best.get("win_rate_pct"),
            "mean_net": best.get("mean_net"),
            "hac_t_stat": best.get("hac_t_stat"),
            "failure_codes": best.get("failure_codes"),
            "research_state": pr.get("research_state"),
            "rets_stats": _rets_stats(rets),
            "rets_head": [round(float(x), 6) if x is not None and not (isinstance(x, float) and math.isnan(x)) else None for x in rets[:8]],
        })
    return replay, len(pop.get("hypotheses") or [])


def main():
    rows, ledger_path = _load_ledger(RUN_ID)
    probes, lineage, learning, summary = _ledger_nde(rows)

    ledger_report = {
        "run_id": RUN_ID,
        "brief": BRIEF,
        "ledger_path": str(ledger_path),
        "nde_probe_events": len(probes),
        "nde_failure_lineage": len(lineage),
        "nde_learning_updates": len(learning),
        "state_counts": summary.get("research_state_counts"),
        "判定位置": {
            "primary": "dual_engine_workflow_v2/probe_protocol.py evaluate_trial()",
            "manufacture_branch_L1481-1489": "net_mean<=0 → NO_DIRECTIONAL_EFFECT; net_mean>0 but WR<=35% → NEAR_MISS",
            "default_branch_L1511-1512": "statistical/economic/execution all fail → NO_DIRECTIONAL_EFFECT",
            "cheap_probe_L157-158": "cheap_probe_stream: mean<=0 or t<1.0 → NO_DIRECTIONAL_EFFECT",
            "ledger_write": "research_discovery.py L1769-1791 append_event event_type=probe",
        },
    }

    # Ledger-only aggregates (no replay)
    indep = [x.get("independent_events") for x in lineage if x.get("independent_events") is not None]
    means = [x.get("mean_net") for x in lineage if x.get("mean_net") is not None]
    ledger_report["lineage_stats"] = {
        "independent_events_min": min(indep) if indep else None,
        "independent_events_max": max(indep) if indep else None,
        "independent_events_zero": sum(1 for x in indep if int(x) == 0),
        "mean_net_positive": sum(1 for x in means if float(x) > 0),
        "mean_net_negative": sum(1 for x in means if float(x) <= 0),
        "mean_net_nan": sum(1 for x in lineage if x.get("mean_net") is None),
        "failure_codes": dict(Counter(c for x in lineage for c in (x.get("failure_codes") or []))),
        "best_events": dict(Counter(x.get("best_event") for x in lineage if x.get("best_event"))),
        "families": dict(Counter(x.get("family") for x in lineage if x.get("family"))),
        "learning_attribution": dict(Counter(x.get("primary_attribution") for x in learning)),
    }

    ledger_report["lineage_samples"] = lineage[:8]
    ledger_report["probe_samples"] = probes[:8]

    # Replay full population probe pass (deterministic enough for forensic)
    replay, pop_n = _replay_probes(target_ids=None, max_hyp=64)
    ledger_report["population_n"] = pop_n
    ledger_report["replay_n"] = len(replay)
    ledger_report["replay"] = replay

    wr_vals = [r.get("win_rate") for r in replay if r.get("win_rate") is not None]
    ledger_report["replay_wr_stats"] = {
        "n_with_wr": len(wr_vals),
        "wr_min": min(wr_vals) if wr_vals else None,
        "wr_max": max(wr_vals) if wr_vals else None,
        "wr_gt_35pct": sum(1 for w in wr_vals if float(w) > 0.35),
        "wr_eq_0": sum(1 for w in wr_vals if float(w) == 0),
        "n_zero_triggers": sum(1 for r in replay if int(r.get("n_raw_triggers") or 0) == 0),
        "n_zero_independent": sum(1 for r in replay if int(r.get("n_independent_events") or 0) == 0),
        "rets_all_nan": sum(1 for r in replay if (r.get("rets_stats") or {}).get("all_nan")),
        "rets_all_zero": sum(1 for r in replay if (r.get("rets_stats") or {}).get("all_zero")),
    }

    if replay:
        ledger_report["conclusion_zh"] = (
            "信号已生成（n_independent_events>0），rets 非全 NaN/全零；"
            "NO_DIRECTIONAL_EFFECT 主因是 mean_net<=0 或统计轴未过（非胜率计算故障）。"
            if ledger_report["replay_wr_stats"]["n_zero_independent"] == 0
            and ledger_report["replay_wr_stats"]["rets_all_nan"] == 0
            else "部分假设无有效信号或 rets 异常，需分案查看 replay 明细。"
        )
    else:
        ledger_report["conclusion_zh"] = "重放未命中 NDE 假设（种群随机性）；以 ledger failure_lineage 为准。"

    out = ROOT / "alpha_discovery" / ("NDE_FORENSIC_%s.json" % RUN_ID)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ledger_report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "out": str(out),
        "nde_n": len(probes),
        "replay_n": len(replay),
        "conclusion": ledger_report.get("conclusion_zh"),
        "lineage_stats": ledger_report.get("lineage_stats"),
        "replay_wr_stats": ledger_report.get("replay_wr_stats"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
