# -*- coding: utf-8 -*-
"""Creation Blueprint Orchestrator — stages ①–⑤ only (NO review).

Flywheel:
  human intent
    → ① MetaGPT/AutoGen-style meta-think → design doc
    → ② Alphalens/CausalImpact-style hypothesis validation
    → ③ EasyQuant + DeepSeek mine → QuantOracle certify
    → ④ Alphalens rescreen (IC/IR/turnover)
    → ⑤ Backtrader extreme + AutoGen red-team stress
    → deliver pack (then existing ADA5 review is a SEPARATE later step)

Hard fuses:
  1) max 5 retries on stage② / stage⑤ failure loops
  2) overfit fuse: fast IC decay / high turnover → drop factor
  3) VaR fuse: exceed human daily-loss bound → reject before stress

Does NOT import or modify review_admission_v2 / four-review gates.
"""
from __future__ import print_function

import json
import os
from datetime import datetime
from pathlib import Path

from . import creation_alphalens_lite as al
from . import creation_deepseek_factors as dsf
from . import creation_meta_think as meta
from . import creation_stress_lite as stress
from . import easyquant_bridge as eq
from . import quantoracle_bridge as qo


MAX_LOOP = 5


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def _load_matrix(symbol, timeframe, horizon=3, max_bars=1200):
    loaded = eq.load_candles(symbol, timeframe, max_bars=max_bars)
    if not loaded.get("ok"):
        return {"ok": False, "error": loaded.get("error"), "path": loaded.get("path")}
    candles = loaded["candles"]
    closes = [r["close"] for r in candles]
    matrix = eq._build_factor_matrix(candles)
    fwd = eq._forward_returns(closes, horizon=int(horizon))
    return {
        "ok": True,
        "candles": candles,
        "matrix": matrix,
        "fwd": fwd,
        "n_bars": loaded.get("n"),
        "path": loaded.get("path"),
    }


def _mine_with_specs(symbol, timeframe, specs, horizon=3, top_k=6):
    """Run EasyQuant miner then keep rows matching requested specs preference."""
    mine = eq.mine_factors(
        symbol=symbol,
        timeframe=timeframe,
        horizon=horizon,
        top_k=max(int(top_k), 8),
    )
    if not mine.get("ok"):
        return mine
    wanted = set((s.get("factor"), s.get("rule")) for s in (specs or []))
    preferred = []
    rest = []
    for fac in mine.get("factors") or []:
        key = (fac.get("factor"), fac.get("rule"))
        if key in wanted:
            preferred.append(fac)
        else:
            rest.append(fac)
    # also pull from all_scores path: re-mine is already top by score; merge
    ordered = preferred + rest
    mine["factors"] = ordered[: int(top_k)]
    mine["spec_request"] = {
        "n_specs": len(specs or []),
        "matched": len(preferred),
        "deepseek_hint": any((s or {}).get("source") == "deepseek" for s in (specs or [])),
    }
    return mine


def _certify_factors(factors, max_daily_loss=0.05):
    certified = []
    for fac in factors or []:
        st = fac.get("stats") or {}
        cert = qo.certify_factor_signal(
            returns=fac.get("returns") or [],
            win_rate=st.get("win_rate"),
            avg_win=st.get("avg_win"),
            avg_loss=st.get("avg_loss"),
            equity_curve=fac.get("equity_curve"),
        )
        row = {
            "factor": fac.get("factor"),
            "rule": fac.get("rule"),
            "thesis_zh": fac.get("thesis_zh"),
            "score": fac.get("score"),
            "stats": st,
            "returns": fac.get("returns") or [],
            "quantoracle": {
                "ok": cert.get("ok"),
                "source": cert.get("source"),
                "certified": cert.get("certified"),
                "error": cert.get("error"),
                "note_zh": cert.get("note_zh"),
            },
        }
        fuse = dsf.risk_fuse_var(row, max_daily_loss=max_daily_loss)
        row["var_fuse"] = fuse
        if fuse.get("triggered"):
            row["rejected"] = True
            row["reject_reason"] = "var_fuse"
        certified.append(row)
    survivors = [r for r in certified if not r.get("rejected")]
    return certified, survivors


def _pick_trade_returns(survivors):
    if not survivors:
        return [], None
    # prefer positive mean_net + quantoracle sharpe
    def key(r):
        sharpe = ((r.get("quantoracle") or {}).get("certified") or {}).get("sharpe_ratio") or -999
        mean = (r.get("stats") or {}).get("mean_net") or -999
        return (float(sharpe), float(mean))

    best = sorted(survivors, key=key, reverse=True)[0]
    return best.get("returns") or [], best


def _write_deliverables(out_dir, symbol, timeframe, blueprint):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = "%s_%s_%s" % (symbol.split("-")[0].lower(), timeframe, stamp)

    design = ((blueprint.get("stages") or {}).get("meta") or {}).get("design_doc") or {}
    best = blueprint.get("best_factor") or {}
    stress_pack = ((blueprint.get("stages") or {}).get("stress") or {})

    params = {
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": blueprint.get("direction"),
        "mechanism_family": design.get("mechanism_family"),
        "factor": best.get("factor"),
        "rule": best.get("rule"),
        "constraints": design.get("constraints"),
        "quantoracle": (best.get("quantoracle") or {}).get("certified"),
        "blueprint_schema": "qiyu_creation_blueprint_v1",
    }
    risk_lines = [
        "# 策略创造风险报告（蓝图 ①–⑤，不含复核）",
        "",
        "- 标的: %s %s" % (symbol, timeframe),
        "- 机制族: %s" % design.get("mechanism_family"),
        "- 核心逻辑: %s" % design.get("core_logic_zh"),
        "- 最优因子: %s / %s" % (best.get("factor"), best.get("rule")),
        "- QuantOracle source: %s" % ((best.get("quantoracle") or {}).get("source")),
        "- 压力测试通过: %s" % stress_pack.get("passed"),
        "- 熔断触发: %s" % json.dumps(blueprint.get("fuses") or {}, ensure_ascii=False),
        "",
        "## 失效场景",
    ]
    for s in (design.get("failure_scenarios_zh") or []):
        risk_lines.append("- %s" % s)
    risk_lines.extend([
        "",
        "## 说明",
        "本报告止于创造蓝图交付。ADA5 四复核为后续独立环节，本蓝图不改动复核代码。",
        "",
    ])

    # strategy_code.py is a research stub anchored to certified factor — not a live mount
    code = '''# Auto-generated creation blueprint stub — NOT for live mount.
# Factor: {factor} / {rule}
# Mechanism: {family}
# Next: human/GLM mechanism_spec → existing ADA5 review (unchanged).

STRATEGY = {{
    "symbol": "{symbol}",
    "timeframe": "{tf}",
    "factor": "{factor}",
    "rule": "{rule}",
    "protective_sl": 0.009,
    "source": "creation_blueprint_v1",
}}

def describe():
    return STRATEGY
'''.format(
        factor=best.get("factor") or "none",
        rule=best.get("rule") or "none",
        family=design.get("mechanism_family") or "unknown",
        symbol=symbol,
        tf=timeframe,
    )

    paths = {
        "strategy_code": str(out_dir / ("%s_strategy_code.py" % base)),
        "params": str(out_dir / ("%s_params.json" % base)),
        "risk_report": str(out_dir / ("%s_risk_report.md" % base)),
        "blueprint_json": str(out_dir / ("%s_blueprint.json" % base)),
    }
    Path(paths["strategy_code"]).write_text(code, encoding="utf-8")
    Path(paths["params"]).write_text(
        json.dumps(params, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    Path(paths["risk_report"]).write_text("\n".join(risk_lines), encoding="utf-8")
    # blueprint json written by caller (may be large); path reserved
    return paths, params


def run_creation_blueprint(
    symbol,
    timeframe,
    direction="long",
    brief="",
    horizon=3,
    skip_llm=True,
    max_loops=MAX_LOOP,
    out_dir=None,
):
    """Execute stages ①–⑤ with fuses. Returns blueprint envelope for GLM / collab."""
    loops = {"hypothesis": 0, "stress": 0}
    fuses = {"iteration": False, "overfit_drops": 0, "var_rejects": 0, "abort_reason": None}
    stages = {}
    data = _load_matrix(symbol, timeframe, horizon=horizon)
    if not data.get("ok"):
        return {
            "ok": False,
            "schema": "qiyu_creation_blueprint_v1",
            "error": "candle_cache_missing",
            "detail": data,
            "at": _now(),
        }

    # ① Meta-think
    meta_pack = meta.run_meta_think(
        brief=brief, symbol=symbol, timeframe=timeframe,
        direction=direction, skip_llm=skip_llm,
    )
    stages["meta"] = meta_pack
    design = meta_pack.get("design_doc") or {}
    constraints = design.get("constraints") or {}
    max_dd = -abs(float(constraints.get("max_drawdown") or 0.18))
    max_daily = float(constraints.get("max_daily_loss") or 0.05)

    # Core factor hints from design
    core_hints = []
    for h in design.get("hypotheses") or []:
        for name in h.get("testable_factor_hints") or []:
            if name not in core_hints:
                core_hints.append(name)

    survivors = []
    best = None
    trade_returns = []
    abort = False

    while True:
        # ② Hypothesis validation
        loops["hypothesis"] += 1
        if loops["hypothesis"] > int(max_loops):
            fuses["iteration"] = True
            fuses["abort_reason"] = "hypothesis_loop_exceeded"
            abort = True
            break

        hyp = al.validate_hypothesis(
            data["matrix"], data["fwd"], core_factors=core_hints or None,
        )
        stages["hypothesis"] = hyp
        if not hyp.get("passed"):
            # mutate: broaden / shift hints then retry
            core_hints = list(dict.fromkeys(
                core_hints + ["ret_12", "close_z_20", "range_pct", "dist_roll_low", "atr_pct_14"]
            ))
            design.setdefault("mutation_log", []).append({
                "at": _now(),
                "reason": "hypothesis_fail",
                "new_hints": core_hints,
                "loop": loops["hypothesis"],
            })
            continue

        # ③ Mine + QuantOracle
        specs_pack = dsf.propose_factor_specs(design, skip_llm=skip_llm, max_specs=12)
        stages["factor_propose"] = {
            "ok": specs_pack.get("ok"),
            "n": len(specs_pack.get("specs") or []),
            "deepseek": specs_pack.get("deepseek"),
        }
        mine = _mine_with_specs(
            symbol, timeframe, specs_pack.get("specs") or [],
            horizon=horizon, top_k=6,
        )
        stages["mine"] = {
            "ok": mine.get("ok"),
            "n_bars": mine.get("n_bars"),
            "candle_path": mine.get("candle_path"),
            "all_scores": mine.get("all_scores"),
            "spec_request": mine.get("spec_request"),
            "note_zh": mine.get("note_zh"),
        }
        if not mine.get("ok"):
            fuses["abort_reason"] = "mine_failed"
            abort = True
            break

        certified_all, survivors = _certify_factors(
            mine.get("factors") or [], max_daily_loss=max_daily,
        )
        fuses["var_rejects"] += sum(1 for r in certified_all if r.get("rejected"))
        stages["certify"] = {
            "n_certified": len(certified_all),
            "n_survivors": len(survivors),
            "sources": list(set(
                (r.get("quantoracle") or {}).get("source") for r in certified_all
            )),
        }
        # strip heavy returns from stage dump later; keep for screening/stress
        stages["certified_factors"] = [
            {k: v for k, v in r.items() if k != "returns"} for r in certified_all
        ]

        if not survivors:
            design.setdefault("mutation_log", []).append({
                "at": _now(), "reason": "all_var_or_cert_rejected",
                "loop": loops["hypothesis"],
            })
            continue

        # ④ Alphalens rescreen
        rescreen = al.rescreen_candidates(survivors, data["matrix"], data["fwd"])
        fuses["overfit_drops"] += len([
            d for d in (rescreen.get("dropped") or []) if d.get("overfit_fuse")
        ])
        stages["rescreen"] = {
            "n_kept": rescreen.get("n_kept"),
            "n_dropped": rescreen.get("n_dropped"),
            "dropped_reasons": [
                {"factor": d.get("factor"), "reasons": d.get("drop_reasons")}
                for d in (rescreen.get("dropped") or [])[:8]
            ],
        }
        kept = rescreen.get("kept") or []
        # If IC rescreen too strict on short windows, fall back to QuantOracle survivors
        # with positive mean_net (still drop overfit_fuse)
        if not kept:
            kept = [
                r for r in survivors
                if (r.get("stats") or {}).get("mean_net", 0) > 0
                and not (
                    ((r.get("alphalens_rescreen") or {}).get("reasons") or [])
                    and "turnover_too_high" in ((r.get("alphalens_rescreen") or {}).get("reasons") or [])
                )
            ]
            # attach empty rescreen note
            for r in kept:
                r.setdefault("alphalens_rescreen", {"passed": False, "fallback": "mean_net_positive"})
            stages["rescreen"]["fallback"] = "positive_mean_net_survivors"
            stages["rescreen"]["n_kept"] = len(kept)

        if not kept:
            design.setdefault("mutation_log", []).append({
                "at": _now(), "reason": "rescreen_empty", "loop": loops["hypothesis"],
            })
            continue

        trade_returns, best = _pick_trade_returns(kept)
        # strip returns from best for output later
        stages["selected"] = {
            "factor": best.get("factor"),
            "rule": best.get("rule"),
            "stats": best.get("stats"),
            "quantoracle": best.get("quantoracle"),
        }

        # ⑤ Stress
        loops["stress"] += 1
        if loops["stress"] > int(max_loops):
            fuses["iteration"] = True
            fuses["abort_reason"] = "stress_loop_exceeded"
            abort = True
            break

        st = stress.run_stress(trade_returns, max_dd_limit=max_dd)
        stages["stress"] = st
        if st.get("passed"):
            break

        # mutate risk / factor preference and retry from hypothesis broadening
        design.setdefault("mutation_log", []).append({
            "at": _now(),
            "reason": "stress_fail",
            "loop": loops["stress"],
            "stress_passed": False,
        })
        # tighten: prefer lower-turnover factors next — drop current best from hints cycle
        if best.get("factor") in core_hints:
            core_hints = [x for x in core_hints if x != best.get("factor")] + ["range_pct", "atr_pct_14"]
        # continue outer while → re-validate / re-mine
        continue

    ok = (not abort) and best is not None and bool((stages.get("stress") or {}).get("passed"))
    if abort and not fuses.get("abort_reason"):
        fuses["abort_reason"] = "aborted"

    # deliverables
    if out_dir is None:
        out_dir = _root() / "auto_trade" / "dual_engine" / "creation_blueprint"
    blueprint = {
        "ok": ok,
        "schema": "qiyu_creation_blueprint_v1",
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "brief": brief,
        "loops": loops,
        "fuses": fuses,
        "stages": stages,
        "best_factor": (
            {k: v for k, v in (best or {}).items() if k != "returns"} if best else None
        ),
        "probes": {
            "meta": meta.probe(),
            "alphalens": al.probe(),
            "easyquant": eq.probe_easyquant(),
            "quantoracle": qo.probe(),
            "stress": stress.probe(),
        },
        "handoff_zh": (
            "创造蓝图 ①–⑤ 完成（或熔断）。下一步才是现有 ADA5 四复核；"
            "本编排器不调用、不修改复核代码。"
        ),
        "at": _now(),
    }

    paths, params = _write_deliverables(out_dir, symbol, timeframe, blueprint)
    # write compact blueprint json (drop huge series already stripped)
    Path(paths["blueprint_json"]).write_text(
        json.dumps(blueprint, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    blueprint["deliverables"] = paths
    blueprint["params"] = params

    # GLM-facing brief for mechanism_spec (creation only)
    blueprint["glm_research_brief"] = {
        "schema": "qiyu_creation_blueprint_brief_v1",
        "design_doc": {
            "mechanism_family": design.get("mechanism_family"),
            "core_logic_zh": design.get("core_logic_zh"),
            "hypotheses": design.get("hypotheses"),
            "constraints": design.get("constraints"),
            "failure_scenarios_zh": design.get("failure_scenarios_zh"),
        },
        "best_factor": blueprint.get("best_factor"),
        "hypothesis_passed": (stages.get("hypothesis") or {}).get("passed"),
        "rescreen": stages.get("rescreen"),
        "stress_passed": (stages.get("stress") or {}).get("passed"),
        "fuses": fuses,
        "instructions_zh": (
            "你是总指挥。下列结果来自创造蓝图 ①元思考→②假设验证→③挖掘+QuantOracle→"
            "④Alphalens筛选→⑤压力/红队。请据此写 mechanism_spec；"
            "禁止与 QuantOracle certified 数字冲突；禁止声称已过复核。"
        ),
        "built_at": _now(),
    }
    return blueprint
