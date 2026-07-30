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
  4) return-hardness fuse: weekly proxy <8% or return/MDD <1.0 → switch lens
  5) degeneration fuse: low exposure / tiny avg trade / window total <1%
  6) factor LS weekly (levered) <3% → drop even if IC looks fine

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
from . import creation_prelim_eval as prelim
from . import creation_return_hardness as rh
from . import creation_stress_lite as stress
from . import easyquant_bridge as eq
from . import quantoracle_bridge as qo
from . import research_candle_store as rcs


MAX_LOOP = 5
MIN_PRESENT_WR = prelim.MIN_WIN_RATE


def _switch_direction(design, classic_tried, perspectives_tried):
    """On return-hardness / degeneration fail: try unused lens, else classic variant."""
    div = dict(design.get("divergence") or {})
    pers = list(div.get("perspectives") or [])
    for p in pers:
        pid = p.get("id")
        if not pid or pid in perspectives_tried:
            continue
        if p.get("return_capacity_ok") is False:
            continue
        perspectives_tried.append(pid)
        design = dict(design)
        design["mechanism_family"] = p.get("family") or design.get("mechanism_family")
        design["core_logic_zh"] = p.get("thesis_zh") or design.get("core_logic_zh")
        design["hypotheses"] = [
            {
                "id": "H1_switched_lens",
                "statement_zh": p.get("thesis_zh"),
                "testable_factor_hints": list(p.get("factor_hints") or []),
            },
            {
                "id": "H2_return_hardness",
                "statement_zh": "周收益代理≥8%且收益/回撤≥1.0，禁止近零收益退化",
                "testable_factor_hints": list(p.get("factor_hints") or [])[:3],
            },
        ]
        div["selected_id"] = pid
        div["selected_lens_zh"] = p.get("lens_zh")
        div["selection_reason_zh"] = "收益硬度/退化熔断后切换未用微观结构视角"
        design["divergence"] = div
        return design, list(p.get("factor_hints") or []), {
            "mode": "perspective",
            "id": pid,
            "lens_zh": p.get("lens_zh"),
        }

    variant = prelim.next_classic_variant(classic_tried)
    if variant is None:
        return design, None, None
    classic_tried.append(variant["id"])
    design = prelim.apply_variant_to_design(design, variant)
    return design, list(variant.get("factor_hints") or []), {
        "mode": "classic",
        "id": variant.get("id"),
        "lens_zh": variant.get("lens_zh"),
    }


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def _load_matrix(symbol, timeframe, horizon=3, max_bars=None):
    # Prefer long research history when R2/local store is populated.
    if max_bars is None:
        max_bars = int(os.environ.get("QIYU_CREATION_MAX_BARS") or 20000)
    loaded = eq.load_candles(symbol, timeframe, max_bars=max_bars, prefer_research=True)
    if not loaded.get("ok"):
        return {
            "ok": False,
            "error": loaded.get("error"),
            "path": loaded.get("path"),
            "research": loaded.get("research"),
            "hint_zh": loaded.get("hint_zh"),
        }
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
        "source": loaded.get("source"),
        "research": loaded.get("research"),
        "note_zh": loaded.get("note_zh"),
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

    presentable = bool((blueprint.get("prelim") or {}).get("present_to_human"))
    hardness_ok = (blueprint.get("return_hardness") or {}).get("passed")
    if hardness_ok is False:
        presentable = False
    design = ((blueprint.get("stages") or {}).get("meta") or {}).get("design_doc") or {}
    best = blueprint.get("best_factor") or {}
    stress_pack = ((blueprint.get("stages") or {}).get("stress") or {})
    prelim_pack = blueprint.get("prelim") or {}
    window = (prelim_pack.get("window") or {})
    hardness = blueprint.get("return_hardness") or {}

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
        "present_to_human": presentable,
        "prelim_win_rate": prelim_pack.get("win_rate"),
        "return_hardness": {
            "passed": hardness.get("passed"),
            "metrics": hardness.get("metrics"),
            "reject_reasons": hardness.get("reject_reasons"),
        },
        "window": window,
    }

    if not presentable:
        risk_lines = [
            "# 【初评未通过·禁止展示为交付】",
            "",
            (hardness.get("human_banner_zh")
             or prelim_pack.get("human_banner_zh")
             or "胜率/收益硬度门禁未过。"),
            "",
            "- %s" % (window.get("label_zh") or ""),
            "- %s" % (window.get("return_scope_zh") or ""),
            "- 胜率: %s（门槛 ≥50%%）" % prelim_pack.get("win_rate"),
            "- 收益硬度拒绝: %s" % ",".join(hardness.get("reject_reasons") or []),
            "- 拒绝原因: %s" % ",".join(prelim_pack.get("reject_reasons") or []),
            "- 已尝试经典变式: %s" % (blueprint.get("classic_tried") or []),
            "- 已尝试视角: %s" % (blueprint.get("perspectives_tried") or []),
            "",
            "不会把胜率<50% 或 近零收益/虚高夏普 的垃圾策略包装成「创造成功」给人看。",
            "请换方向、拉长样本，或继续自动经典变式迭代。",
            "",
        ]
        code = (
            "# REJECTED — win_rate gate failed. Not a deliverable.\n"
            "# present_to_human=False\n"
            "STRATEGY = %r\n"
        ) % params
        paths = {
            "strategy_code": str(out_dir / ("%s_REJECTED_strategy_code.py" % base)),
            "params": str(out_dir / ("%s_REJECTED_params.json" % base)),
            "risk_report": str(out_dir / ("%s_REJECTED_risk_report.md" % base)),
            "blueprint_json": str(out_dir / ("%s_REJECTED_blueprint.json" % base)),
        }
        Path(paths["strategy_code"]).write_text(code, encoding="utf-8")
        Path(paths["params"]).write_text(
            json.dumps(params, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        Path(paths["risk_report"]).write_text("\n".join(risk_lines), encoding="utf-8")
        return paths, params

    risk_lines = [
        "# 策略创造风险报告（蓝图 ①–⑤，不含复核）",
        "",
        "- 标的: %s %s" % (symbol, timeframe),
        "- 机制族: %s" % design.get("mechanism_family"),
        "- 核心逻辑: %s" % design.get("core_logic_zh"),
        "- 最优因子: %s / %s" % (best.get("factor"), best.get("rule")),
        "- QuantOracle source: %s" % ((best.get("quantoracle") or {}).get("source")),
        "- 压力测试通过: %s" % stress_pack.get("passed"),
        "- 初评胜率: %s（≥50%% 门禁已过）" % prelim_pack.get("win_rate"),
        "- %s" % (window.get("label_zh") or ""),
        "- %s" % (window.get("return_scope_zh") or ""),
        "- 收益硬度: %s" % json.dumps(
            {
                "passed": (blueprint.get("return_hardness") or {}).get("passed"),
                "weekly_proxy": ((blueprint.get("return_hardness") or {}).get("metrics") or {}).get(
                    "weekly_return_proxy"
                ),
                "return_mdd": ((blueprint.get("return_hardness") or {}).get("metrics") or {}).get(
                    "return_mdd"
                ),
            },
            ensure_ascii=False,
        ),
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

    code = '''# Auto-generated creation blueprint stub — NOT for live mount.
# Factor: {factor} / {rule}
# Mechanism: {family}
# Prelim WR gate: PASSED (>=50%)
# Window: {window}
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
        window=(window.get("label_zh") or "").replace('"', "'"),
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
    stages["data"] = {
        "n_bars": data.get("n_bars"),
        "path": data.get("path"),
        "source": data.get("source"),
        "research": data.get("research"),
        "note_zh": data.get("note_zh"),
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
    classic_tried = []
    perspectives_tried = []
    prelim_pack = None
    hardness_pack = None
    presentable = False

    # seed perspectives_tried with currently selected lens
    sel0 = ((design.get("divergence") or {}).get("selected_id"))
    if sel0:
        perspectives_tried.append(sel0)

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

        # ③b Factor LS weekly-return filter (levered, cost-aware) — Improvement 2
        candles = data.get("candles") or []
        span = rh.span_days_from_ts(
            candles[0]["ts"] if candles else None,
            candles[-1]["ts"] if candles else None,
        )
        ls_kept = []
        ls_dropped = []
        for row in survivors:
            ok_ls, packed = rh.filter_factor_by_ls_weekly(
                row, span, constraints=constraints,
            )
            if ok_ls:
                ls_kept.append(packed)
            else:
                ls_dropped.append(packed)
        stages["ls_weekly_filter"] = {
            "n_in": len(survivors),
            "n_kept": len(ls_kept),
            "n_dropped": len(ls_dropped),
            "floor": (constraints or {}).get("minimum_factor_weekly_lev"),
            "dropped_sample": [
                {
                    "factor": d.get("factor"),
                    "reason": d.get("ls_weekly_reason"),
                    "weekly_lev": ((d.get("ls_weekly") or {}).get("weekly_lev")),
                }
                for d in ls_dropped[:8]
            ],
        }
        if ls_kept:
            survivors = ls_kept
        else:
            # all factors washed to near-zero absolute return — switch lens
            design.setdefault("mutation_log", []).append({
                "at": _now(),
                "reason": "all_factors_ls_weekly_below_floor",
                "loop": loops["hypothesis"],
                "n_dropped": len(ls_dropped),
            })
            design, hints, switched = _switch_direction(
                design, classic_tried, perspectives_tried,
            )
            if switched is None:
                fuses["abort_reason"] = "ls_weekly_floor_and_directions_exhausted"
                abort = True
                break
            core_hints = hints or core_hints
            stages["meta"]["design_doc"] = design
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

        # Hard prefer WR>=50% candidates before stress
        wr_ok = [
            r for r in kept
            if float((r.get("stats") or {}).get("win_rate") or 0) >= float(MIN_PRESENT_WR)
        ]
        if wr_ok:
            kept = wr_ok
            stages["rescreen"]["wr50_filter"] = {"kept": len(kept), "applied": True}
        else:
            stages["rescreen"]["wr50_filter"] = {
                "kept": 0, "applied": True,
                "note_zh": "无胜率≥50%候选；将触发经典变式切换而非对人展示。",
            }

        if not kept:
            # switch classic / perspective immediately
            design, hints, switched = _switch_direction(
                design, classic_tried, perspectives_tried,
            )
            if switched is None:
                fuses["abort_reason"] = "no_wr50_candidate_and_directions_exhausted"
                abort = True
                break
            core_hints = hints or core_hints
            design.setdefault("mutation_log", []).append({
                "at": _now(),
                "reason": "wr50_gate_switch_direction",
                "switched": switched,
                "loop": loops["hypothesis"],
            })
            stages["meta"]["design_doc"] = design
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
        if not st.get("passed"):
            design.setdefault("mutation_log", []).append({
                "at": _now(),
                "reason": "stress_fail",
                "loop": loops["stress"],
                "stress_passed": False,
            })
            if best.get("factor") in core_hints:
                core_hints = [x for x in core_hints if x != best.get("factor")] + ["range_pct", "atr_pct_14"]
            continue

        # ⑤b Preliminary WR gate — NEVER present WR<50% as delivery
        st_stats = best.get("stats") or {}
        # trade-level WR from returns if available
        if trade_returns:
            tw = sum(1 for r in trade_returns if r > 0) / float(len(trade_returns))
        else:
            tw = st_stats.get("win_rate")
        candles = data.get("candles") or []
        first_ts = candles[0]["ts"] if candles else None
        last_ts = candles[-1]["ts"] if candles else None
        prelim_pack = prelim.prelim_eval(
            {
                "win_rate": tw if tw is not None else st_stats.get("win_rate"),
                "n_trades": len(trade_returns) or st_stats.get("n"),
                "total_return": None,
                "max_drawdown": (st.get("backtrader") or {}).get("full_max_drawdown"),
                "sharpe_ann_proxy": (
                    ((best.get("quantoracle") or {}).get("certified") or {}).get("sharpe_ratio")
                ),
            },
            first_ts=first_ts,
            last_ts=last_ts,
            n_bars=data.get("n_bars"),
            timeframe=timeframe,
            min_win_rate=MIN_PRESENT_WR,
            min_trades=8,
        )
        stages["prelim"] = prelim_pack
        if not prelim_pack.get("present_to_human"):
            # WR gate failed after stress — switch direction
            design.setdefault("mutation_log", []).append({
                "at": _now(),
                "reason": "prelim_wr_gate_fail",
                "win_rate": prelim_pack.get("win_rate"),
                "banner": prelim_pack.get("human_banner_zh"),
            })
            design, hints, switched = _switch_direction(
                design, classic_tried, perspectives_tried,
            )
            if switched is None:
                fuses["abort_reason"] = "prelim_wr_below_50_directions_exhausted"
                abort = True
                break
            core_hints = hints or core_hints
            stages["meta"]["design_doc"] = design
            best = None
            presentable = False
            continue

        # ⑤c Return hardness + degeneration (Improvement 3/5) — after WR pass
        total_ret = rh.equity_total_return(trade_returns)
        mdd_bt = (st.get("backtrader") or {}).get("full_max_drawdown")
        hardness_pack = rh.evaluate_return_hardness(
            trade_returns=trade_returns,
            total_return=total_ret,
            max_drawdown=mdd_bt if mdd_bt is not None else rh.max_drawdown_from_returns(trade_returns),
            first_ts=first_ts,
            last_ts=last_ts,
            n_bars=data.get("n_bars"),
            hold_bars=max(int(horizon), 3),
            constraints=constraints,
        )
        stages["return_hardness"] = {
            k: v for k, v in hardness_pack.items() if k != "degeneration"
        }
        stages["degeneration"] = hardness_pack.get("degeneration")
        if hardness_pack.get("passed"):
            presentable = True
            break

        design.setdefault("mutation_log", []).append({
            "at": _now(),
            "reason": "return_hardness_or_degeneration_fail",
            "reject_reasons": hardness_pack.get("reject_reasons"),
            "metrics": hardness_pack.get("metrics"),
            "banner": hardness_pack.get("human_banner_zh"),
        })
        design, hints, switched = _switch_direction(
            design, classic_tried, perspectives_tried,
        )
        if switched is None:
            fuses["abort_reason"] = "return_hardness_directions_exhausted"
            abort = True
            break
        core_hints = hints or core_hints
        stages["meta"]["design_doc"] = design
        best = None
        presentable = False
        continue

    ok = (
        (not abort)
        and best is not None
        and bool((stages.get("stress") or {}).get("passed"))
        and presentable
        and bool((hardness_pack or {}).get("passed"))
    )
    if abort and not fuses.get("abort_reason"):
        fuses["abort_reason"] = "aborted"
    if best is not None and not presentable and not fuses.get("abort_reason"):
        if hardness_pack and not hardness_pack.get("passed"):
            fuses["abort_reason"] = "return_hardness_not_presentable"
        else:
            fuses["abort_reason"] = "prelim_wr_below_50_not_presentable"

    # ensure prelim exists even on abort
    if prelim_pack is None and best is not None:
        st_stats = best.get("stats") or {}
        candles = data.get("candles") or []
        prelim_pack = prelim.prelim_eval(
            {
                "win_rate": st_stats.get("win_rate"),
                "n_trades": st_stats.get("n"),
            },
            first_ts=candles[0]["ts"] if candles else None,
            last_ts=candles[-1]["ts"] if candles else None,
            n_bars=data.get("n_bars"),
            timeframe=timeframe,
        )
        presentable = False
        stages["prelim"] = prelim_pack

    # deliverables
    if out_dir is None:
        out_dir = _root() / "auto_trade" / "dual_engine" / "creation_blueprint"
    # presentable requires BOTH wr gate and return hardness
    if presentable and hardness_pack and not hardness_pack.get("passed"):
        presentable = False
    blueprint = {
        "ok": ok,
        "present_to_human": presentable,
        "schema": "qiyu_creation_blueprint_v1",
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "brief": brief,
        "loops": loops,
        "fuses": fuses,
        "classic_tried": classic_tried,
        "perspectives_tried": perspectives_tried,
        "prelim": prelim_pack,
        "return_hardness": hardness_pack,
        "stages": stages,
        "best_factor": (
            {k: v for k, v in (best or {}).items() if k != "returns"}
            if best and presentable else None
        ),
        "probes": {
            "meta": meta.probe(),
            "alphalens": al.probe(),
            "easyquant": eq.probe_easyquant(),
            "quantoracle": qo.probe(),
            "stress": stress.probe(),
            "research_candles": rcs.probe(),
            "return_hardness": {"ok": True, "module": "creation_return_hardness"},
        },
        "data": stages.get("data"),
        "handoff_zh": (
            (
                "创造蓝图通过初评（胜率≥50% + 收益硬度）并可进入后续 ADA5 复核；本编排器不改复核代码。"
                if presentable and ok else
                (hardness_pack or {}).get("human_banner_zh")
                or (prelim_pack or {}).get("human_banner_zh")
                or "初评未通过或熔断：禁止把胜率<50%或近零收益策略当交付展示。"
            )
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

    # GLM-facing brief for mechanism_spec (creation only) — only if presentable
    if presentable and ok:
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
            "prelim": prelim_pack,
            "fuses": fuses,
            "instructions_zh": (
                "【发散强制】请先列举 3 种完全不同的市场微观结构视角，"
                "并估算每种可承载的最大年化净收益，再择一深入——"
                "禁止一上来直接写策略。"
                "你是总指挥。下列结果来自创造蓝图且已过胜率≥50%与收益硬度门禁。"
                "请据此写 mechanism_spec；禁止与 QuantOracle certified 数字冲突；"
                "禁止声称已过复核；禁止设计近零收益守财奴策略。"
            ),
            "built_at": _now(),
        }
    else:
        blueprint["glm_research_brief"] = {
            "schema": "qiyu_creation_blueprint_brief_v1",
            "blocked": True,
            "present_to_human": False,
            "prelim": prelim_pack,
            "return_hardness": hardness_pack,
            "classic_tried": classic_tried,
            "perspectives_tried": perspectives_tried,
            "instructions_zh": (
                "初评门禁未过（胜率<50% 或 收益硬度/策略退化熔断）。"
                "禁止向人类展示本候选为成功交付。"
                "请换方向或继续经典变式；不要美化虚高夏普+近零收益。"
            ),
            "built_at": _now(),
        }
    return blueprint
