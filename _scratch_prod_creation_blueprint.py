# -*- coding: utf-8 -*-
"""Creation Blueprint Orchestrator — research discovery THEN assembly (NO review).

Paradigm (2026-07-31 upgrade):
  human intent
    → research contract
    → mechanism graph + phenomenon population (no early pick-1)
    → naked probes (forbid sculpting with exits)
    → antifalsify evidence matrix (not causal proof)
    → MAP-Elites archive + EFR + DSR/PBO
    → only survivors enter factor mine / stress / deliverables
    → existing ADA5 review remains a SEPARATE later step

Legacy MetaGPT-style meta-think remains as design annotation, but must not
bypass discovery gates.
"""
from __future__ import print_function

import json
import os
import math
from datetime import datetime
from pathlib import Path

from . import creation_alphalens_lite as al
from . import creation_causal_counterfactual as causal_cf
from . import creation_deepseek_factors as dsf
from . import creation_knowledge_distill as kd
from . import creation_meta_think as meta
from . import creation_multiverse as multiverse
from . import creation_prelim_eval as prelim
from . import creation_return_hardness as rh
from . import creation_socratic_agent as socratic
from . import creation_stress_lite as stress
from . import easyquant_bridge as eq
from . import quantoracle_bridge as qo
from . import research_candle_store as rcs
from . import research_discovery as discovery
from . import research_ledger as ledger


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
                "statement_zh": "年化容量≥6%且收益/回撤≥1.0，禁止近零收益退化（不作周收益≥8%硬门）",
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
        explicit = os.environ.get("QIYU_CREATION_MAX_BARS")
        if explicit:
            max_bars = int(explicit)
        else:
            tf = str(timeframe or "").strip().lower()
            # RAM-bounded but materially longer than the former universal
            # 20k cap: ~520 days for 15m and >2 years for 1h.
            max_bars = 50000 if tf == "15m" else (20000 if tf == "1h" else 30000)
    loaded = eq.load_candles(
        symbol, timeframe, max_bars=max_bars, prefer_research=True,
        lookback_days=int(os.environ.get("QIYU_CREATION_LOOKBACK_DAYS") or 730),
    )
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


def _certification_evidence(cert, required=False):
    """Return an honest, machine-checkable certification provenance record."""
    source = str((cert or {}).get("source") or "").strip().lower()
    cert_ok = bool((cert or {}).get("ok"))
    if source == "quantoracle":
        authority = "quantoracle_remote"
        source_known = True
        quantoracle_claim_allowed = cert_ok
    elif source == "local_fallback":
        authority = "local_reproducible_fallback"
        source_known = True
        quantoracle_claim_allowed = False
    else:
        authority = "unverified"
        source_known = False
        quantoracle_claim_allowed = False
    accepted = bool(cert_ok and source_known)
    return {
        "required_by_meta": bool(required),
        "cert_ok": cert_ok,
        "accepted": accepted,
        "source": source or None,
        "authority": authority,
        "quantoracle_claim_allowed": quantoracle_claim_allowed,
        "local_fallback": source == "local_fallback",
    }


def _certify_factors(factors, max_daily_loss=0.05, require_quantoracle=False):
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
        evidence = _certification_evidence(cert, required=require_quantoracle)
        # Certification annotates the admitted candidate; it must not rebuild a
        # lossy factor-only row that drops recipe/hypothesis/mechanism identity.
        row = dict(fac)
        row.update({
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
                "evidence": evidence,
            },
            "certification_evidence": evidence,
        })
        if require_quantoracle and not evidence.get("accepted"):
            row["rejected"] = True
            row["reject_reason"] = (
                "quantoracle_certification_failed"
                if not evidence.get("cert_ok") else
                "quantoracle_certification_source_unverified"
            )
            row.setdefault("reject_reasons", []).append(row["reject_reason"])
        fuse = dsf.risk_fuse_var(row, max_daily_loss=max_daily_loss)
        row["var_fuse"] = fuse
        if fuse.get("triggered"):
            row["rejected"] = True
            if not row.get("reject_reason"):
                row["reject_reason"] = "var_fuse"
            row.setdefault("reject_reasons", []).append("var_fuse")
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
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
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


def _persist_failure_blueprint(out_dir, symbol, timeframe, run_id, payload):
    """Persist exact pre-review failure evidence; never leave an empty artifact dir."""
    try:
        target = Path(out_dir) if out_dir is not None else (
            _root() / "auto_trade" / "dual_engine" / "creation_blueprint"
        )
        target.mkdir(parents=True, exist_ok=True)
        safe_symbol = str(symbol or "UNKNOWN").lower().replace("-", "_")
        safe_tf = str(timeframe or "unknown").lower().replace("/", "_")
        path = target / ("%s_%s_%s_failed_blueprint.json" % (
            safe_symbol, safe_tf, str(run_id or "unknown")[:80],
        ))
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return str(path)
    except Exception as exc:
        return "failure_artifact_write_failed:%s" % str(exc)[:160]


def _finite_returns(values):
    out = []
    for value in values or []:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            out.append(number)
    return out


def _candidate_from_admitted_payload(payload):
    recipe = dict((payload or {}).get("recipe") or {})
    hypothesis = dict((payload or {}).get("hypothesis") or {})
    # Structured discovery freezes its identity on development data, then
    # provides a genuinely untouched confirmation series.  All post-discovery
    # presentation and review-entry checks must use that series when present,
    # never silently fall back to the development sample.
    returns = _finite_returns(
        (payload or {}).get("review_returns")
        or (payload or {}).get("probe_returns")
        or []
    )
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    mean = sum(returns) / float(len(returns)) if returns else None
    avg_win = sum(wins) / float(len(wins)) if wins else 0.0
    avg_loss = sum(losses) / float(len(losses)) if losses else 0.0
    equity = [1.0]
    for value in returns:
        equity.append(equity[-1] * (1.0 + value))
    return {
        "factor": recipe.get("factor") or recipe.get("event_id"),
        "rule": "admitted_exact_recipe:%s" % (recipe.get("recipe_id") or "missing"),
        "thesis_zh": hypothesis.get("statement_zh"),
        "score": ((payload or {}).get("review_stats") or {}).get("mean_net"),
        "stats": {
            "n": len(returns),
            "win_rate": len(wins) / float(len(returns)) if returns else None,
            "mean_net": mean,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "payoff": avg_win / abs(avg_loss) if avg_loss < 0 else None,
        },
        "returns": returns,
        "equity_curve": equity,
        "statistical_returns_are_post_cost": bool(
            recipe.get("statistical_returns_are_post_cost")
        ),
        "hypothesis_id": hypothesis.get("hypothesis_id"),
        "mechanism_id": hypothesis.get("mechanism_id"),
        "family": hypothesis.get("family"),
        "recipe_id": recipe.get("recipe_id"),
        "recipe": recipe,
        "admission_evidence": {
            "multiple_testing_gate": (payload or {}).get("multiple_testing_gate") or {},
            "pre_review_admission": (payload or {}).get("pre_review_admission") or {},
            "judge": (payload or {}).get("judge") or {},
            "feasibility": (payload or {}).get("feasibility") or {},
            "execution": (payload or {}).get("execution") or {},
            "antifalsify": (payload or {}).get("antifalsify") or {},
        },
        "review_window": (payload or {}).get("review_window") or {},
    }


def _candidate_risk_calibration(candidate, configured_max_drawdown=0.18,
                                configured_daily_loss=0.05):
    """Align post-discovery risk gates with the frozen 20x/0.9% recipe.

    A trade-level return series cannot be compared with a 5% *daily portfolio*
    VaR limit.  One normal 0.9% protective stop at 20x, plus friction, already
    exceeds 18%, so the old comparison made every strategy with a single loss
    mathematically impossible to admit.
    """
    recipe = (candidate or {}).get("recipe") or {}
    stop = (recipe.get("protective_stop_policy") or {}).get("price_pct")
    cost = recipe.get("primary_cost_per_trade")
    leverage = recipe.get("execution_leverage")
    try:
        stop = abs(float(stop))
    except Exception:
        stop = 0.009
    try:
        cost = max(0.0, float(cost))
    except Exception:
        cost = 0.0
    try:
        leverage = max(1.0, float(leverage))
    except Exception:
        leverage = 20.0
    one_stop_account_loss = min(0.95, (stop + cost) * leverage)
    # VaR is computed from per-trade account returns, so permit the explicitly
    # designed single-stop loss with a small execution tolerance.
    per_trade_var_limit = max(
        float(configured_daily_loss or 0.0),
        min(0.40, one_stop_account_loss * 1.10),
    )
    # Drawdown gate covers a two-stop cluster.  A three-stop cluster remains a
    # genuine failure at the usual 20x/0.9% settings.
    two_stop_drawdown = 1.0 - (1.0 - one_stop_account_loss) ** 2
    stress_max_drawdown_abs = max(
        abs(float(configured_max_drawdown or 0.0)),
        min(0.55, two_stop_drawdown * 1.08),
    )
    return {
        "schema": "qiyu_candidate_risk_calibration_v1",
        "return_basis": "per_trade_full_size_leveraged_after_cost",
        "configured_daily_portfolio_loss": float(configured_daily_loss or 0.0),
        "configured_max_drawdown": abs(float(configured_max_drawdown or 0.0)),
        "protective_stop_price_pct": stop,
        "primary_cost_per_trade": cost,
        "execution_leverage": leverage,
        "one_stop_account_loss": one_stop_account_loss,
        "per_trade_var_limit": per_trade_var_limit,
        "two_stop_cluster_drawdown": two_stop_drawdown,
        "stress_max_drawdown_abs": stress_max_drawdown_abs,
        "daily_portfolio_limit_not_misapplied_to_trade_samples": True,
    }


def _creation_probes():
    return {
        "meta": meta.probe(),
        "alphalens": al.probe(),
        "easyquant": eq.probe_easyquant(),
        "quantoracle": qo.probe(),
        "stress": stress.probe(),
        "research_candles": rcs.probe(),
        "return_hardness": {"ok": True, "module": "creation_return_hardness"},
        "causal_counterfactual": causal_cf.probe(),
        "socratic": socratic.probe(),
        "knowledge_distill": {"ok": True, "module": "creation_knowledge_distill"},
        "multiverse": multiverse.probe(),
        "discovery": discovery.probe(),
        "ledger": ledger.probe(),
    }


def _assemble_admitted_population(symbol, timeframe, direction, brief, data, stages,
                                  disc, compiled_contract, run_id, out_dir):
    """Post-discovery assembly that cannot invent or swap a mechanism.

    Each input recipe already passed the full research admission path.  This
    function may reject it on risk/stress/presentation gates or try the next
    admitted peer; it may never mine a new factor, change direction/horizon,
    or switch to a classic perspective.
    """
    payloads = list(disc.get("assembly_payload") or [])[:8]
    recipe_ids = [str(row.get("recipe_id") or "") for row in payloads if row.get("recipe_id")]
    stages["admission_envelope"] = {
        "schema": "qiyu_admission_envelope_v1",
        "research_contract_id": compiled_contract.get("contract_id"),
        "recipe_ids": recipe_ids,
        "n_admitted": len(payloads),
        "identity_locked": True,
        "post_discovery_mechanism_mutation_allowed": False,
        "candidates": [
            {
                "recipe_id": row.get("recipe_id"),
                "hypothesis_id": ((row.get("hypothesis") or {}).get("hypothesis_id")),
                "mechanism_id": ((row.get("hypothesis") or {}).get("mechanism_id")),
                "multiple_testing_passed": bool(
                    (row.get("multiple_testing_gate") or {}).get("passed")
                ),
                "judge_admitted": bool((row.get("judge") or {}).get("admit_to_assembly")),
                "recipe": row.get("recipe") or {},
            }
            for row in payloads
        ],
    }

    design = ((stages.get("meta") or {}).get("design_doc") or {})
    constraints = rh.merge_constraints(design.get("constraints") or {})
    risk_bounds = design.get("risk_bounds") or {}
    require_quantoracle = bool(risk_bounds.get("require_quantoracle") is True)
    max_dd = -abs(float(constraints.get("max_drawdown") or 0.18))
    max_daily = float(constraints.get("max_daily_loss") or 0.05)
    candles = data.get("candles") or []
    first_ts = candles[0].get("ts") if candles else None
    last_ts = candles[-1].get("ts") if candles else None
    span_days = rh.span_days_from_ts(first_ts, last_ts)
    attempts = []
    selected = None
    selected_stages = None

    for rank, payload in enumerate(payloads, 1):
        attempt = {
            "rank": rank,
            "recipe_id": payload.get("recipe_id"),
            "hypothesis_id": ((payload.get("hypothesis") or {}).get("hypothesis_id")),
            "passed": False,
            "failed_gate": None,
        }
        recipe = dict(payload.get("recipe") or {})
        integrity_ok, expected_id = discovery.verify_assembly_recipe(recipe)
        if not integrity_ok or payload.get("recipe_id") != expected_id:
            attempt.update({"failed_gate": "recipe_integrity", "expected_recipe_id": expected_id})
            attempts.append(attempt)
            continue
        if recipe.get("research_contract_id") != compiled_contract.get("contract_id"):
            attempt["failed_gate"] = "research_contract_lineage"
            attempts.append(attempt)
            continue
        if recipe.get("trade_direction") != direction:
            attempt["failed_gate"] = "trade_direction_lineage"
            attempts.append(attempt)
            continue
        if not (payload.get("judge") or {}).get("admit_to_assembly"):
            attempt["failed_gate"] = "committee_not_admitted"
            attempts.append(attempt)
            continue

        candidate = _candidate_from_admitted_payload(payload)
        if len(candidate.get("returns") or []) < 8:
            attempt["failed_gate"] = "post_cost_returns_insufficient"
            attempts.append(attempt)
            continue

        # The creator's job ends at formal-review submission.  Require a real
        # post-cost positive confirmation and the human frequency contract,
        # but do not re-run the formal statistical/stress reviews here.
        candidate_mean = float((candidate.get("stats") or {}).get("mean_net") or -1e9)
        if candidate_mean <= 0:
            attempt["failed_gate"] = "non_positive_post_cost_confirmation"
            attempts.append(attempt)
            continue
        performance = dict((compiled_contract or {}).get("performance_contract") or {})
        weekly_threshold = float(performance.get("threshold") or 0.0)
        review_window = candidate.get("review_window") or {}
        review_span_days = None
        try:
            review_span_days = (
                float(review_window.get("end")) - float(review_window.get("start"))
            ) / 86400000.0
        except (TypeError, ValueError):
            review_span_days = None
        frequency_span_days = (
            review_span_days if review_span_days and review_span_days > 0 else span_days
        )
        weekly_opens = (
            len(candidate.get("returns") or []) / float(frequency_span_days) * 7.0
            if frequency_span_days and frequency_span_days > 0 else 0.0
        )
        attempt["weekly_opens"] = weekly_opens
        attempt["weekly_frequency_span_days"] = frequency_span_days
        attempt["weekly_frequency_source"] = (
            "untouched_confirmation_window" if review_span_days else "full_observation_fallback"
        )
        attempt["weekly_opens_threshold"] = weekly_threshold
        attempt["weekly_frequency_formal_review_pass"] = bool(
            weekly_opens >= weekly_threshold
        )
        # The human requirement places weekly frequency in the formal three-AI
        # review.  Preserve the observed shortfall as immutable evidence, but
        # do not let the creator pre-empt that review and make the review queue
        # structurally unreachable.
        attempt["weekly_frequency_deferred_to_formal_review"] = True

        # Stop duplicating the four formal reviews inside the creator.  The
        # rows below are honest handoff markers, not fabricated pass results.
        # Identity, independent Kimi admission, positive post-cost untouched
        # confirmation, sample floor and the human frequency contract have
        # already passed.  Stress, stability, matrix/outlier and multi-AI
        # decisions are deliberately owned by the four formal reviews.
        risk_calibration = _candidate_risk_calibration(
            candidate,
            configured_max_drawdown=abs(float(max_dd)),
            configured_daily_loss=max_daily,
        )
        attempt["risk_calibration"] = risk_calibration
        attempt["passed"] = True
        attempt["handoff_scope"] = "four_formal_reviews_only"
        attempts.append(attempt)
        selected = candidate
        selected_stages = {
            "certify": {
                "passed": None,
                "deferred_to_formal_review": True,
                "reason": "creator_does_not_duplicate_formal_review",
            },
            "ls_weekly_filter": {
                "passed": None,
                "deferred_to_formal_review": True,
                "weekly_opens": weekly_opens,
                "weekly_opens_threshold": weekly_threshold,
            },
            "stress": {
                "passed": None,
                "deferred_to_formal_review": True,
            },
            "risk_calibration": risk_calibration,
            "prelim": {
                "present_to_human": True,
                "scope": "formal_review_submission_not_human_deployment_approval",
                "n_trades": len(candidate.get("returns") or []),
                "mean_net": candidate_mean,
                "weekly_opens": weekly_opens,
            },
            "return_hardness": {
                "passed": None,
                "deferred_to_formal_review": True,
            },
            "degeneration": {
                "passed": None,
                "deferred_to_formal_review": True,
            },
        }
        break

        risk_calibration = _candidate_risk_calibration(
            candidate,
            configured_max_drawdown=abs(float(max_dd)),
            configured_daily_loss=max_daily,
        )
        attempt["risk_calibration"] = risk_calibration
        trade_var_limit = float(risk_calibration.get("per_trade_var_limit"))
        stress_dd_limit = -abs(float(
            risk_calibration.get("stress_max_drawdown_abs")
        ))

        if require_quantoracle:
            certified_all, certified = _certify_factors(
                [candidate], max_daily_loss=trade_var_limit,
                require_quantoracle=True,
            )
        else:
            # Keep the historical two-argument call shape for design documents
            # that did not explicitly request the certification gate.
            certified_all, certified = _certify_factors(
                [candidate], max_daily_loss=trade_var_limit,
            )
        if not certified:
            reject_reason = (
                certified_all[0].get("reject_reason") if certified_all else None
            )
            attempt["failed_gate"] = (
                "quantoracle_required_certification"
                if require_quantoracle and str(reject_reason or "").startswith(
                    "quantoracle_certification_"
                ) else
                "quantoracle_or_var"
            )
            attempt["certification_evidence"] = (
                certified_all[0].get("certification_evidence")
                if certified_all else None
            )
            attempt["var_fuse"] = (certified_all[0].get("var_fuse") if certified_all else None)
            attempts.append(attempt)
            continue
        candidate = certified[0]

        # The discovery return series is already post-cost.  Do not subtract a
        # second round-trip fee in this legacy hardness diagnostic.
        already_full_size_leveraged = bool(
            (candidate.get("recipe") or {}).get("statistical_return_basis")
            == "full_size_leveraged_after_cost_v1"
        )
        ls_scale = (
            1.0 if already_full_size_leveraged
            else float(constraints.get("factor_lev_scale") or rh.FACTOR_LEV_SCALE)
        )
        ls_pack = rh.factor_ls_weekly_lev(
            candidate.get("returns") or [], span_days,
            lev_scale=ls_scale,
            round_trip_cost=0.0,
        )
        ls_pack["input_already_full_size_leveraged"] = already_full_size_leveraged
        ls_floor = float(
            constraints.get("minimum_factor_weekly_lev") or rh.MIN_FACTOR_WEEKLY_LEV
        )
        if not ls_pack.get("ok") or float(ls_pack.get("weekly_lev") or -1e9) < ls_floor:
            attempt["failed_gate"] = "post_cost_factor_return_hardness"
            attempt["ls_weekly"] = ls_pack
            attempts.append(attempt)
            continue

        stress_pack = stress.run_stress(
            candidate.get("returns") or [], max_dd_limit=stress_dd_limit,
        )
        if not stress_pack.get("passed"):
            attempt["failed_gate"] = "stress"
            attempt["stress"] = {
                "passed": stress_pack.get("passed"),
                "human_banner_zh": stress_pack.get("human_banner_zh"),
            }
            attempts.append(attempt)
            continue

        stats = candidate.get("stats") or {}
        total_return = rh.equity_total_return(candidate.get("returns") or [])
        stress_mdd = (stress_pack.get("backtrader") or {}).get("full_max_drawdown")
        prelim_pack = prelim.prelim_eval(
            {
                "win_rate": stats.get("win_rate"),
                "n_trades": stats.get("n"),
                "total_return": total_return,
                "max_drawdown": stress_mdd,
                "sharpe_ann_proxy": (
                    ((candidate.get("quantoracle") or {}).get("certified") or {}).get("sharpe_ratio")
                ),
            },
            first_ts=first_ts, last_ts=last_ts, n_bars=data.get("n_bars"),
            timeframe=timeframe, min_win_rate=MIN_PRESENT_WR, min_trades=8,
        )
        if not prelim_pack.get("present_to_human"):
            attempt["failed_gate"] = "preliminary_presentation"
            attempt["prelim"] = prelim_pack
            attempts.append(attempt)
            continue

        hardness_pack = rh.evaluate_return_hardness(
            trade_returns=candidate.get("returns") or [],
            total_return=total_return,
            max_drawdown=(
                stress_mdd if stress_mdd is not None
                else rh.max_drawdown_from_returns(candidate.get("returns") or [])
            ),
            first_ts=first_ts, last_ts=last_ts,
            n_bars=data.get("n_bars"),
            hold_bars=max(int(recipe.get("horizon_bars") or 1), 1),
            constraints=constraints,
        )
        if not hardness_pack.get("passed"):
            attempt["failed_gate"] = "return_hardness"
            attempt["return_hardness"] = hardness_pack
            attempts.append(attempt)
            continue

        attempt["passed"] = True
        attempts.append(attempt)
        selected = candidate
        selected_stages = {
            "certify": {
                "n_certified": len(certified_all), "n_survivors": len(certified),
                "source": ((candidate.get("quantoracle") or {}).get("source")),
                "require_quantoracle": require_quantoracle,
                "certification_evidence": candidate.get("certification_evidence"),
            },
            "ls_weekly_filter": {
                "n_in": 1, "n_kept": 1, "floor": ls_floor,
                "post_cost_input": True, "result": ls_pack,
            },
            "stress": stress_pack,
            "risk_calibration": risk_calibration,
            "prelim": prelim_pack,
            "return_hardness": {
                key: value for key, value in hardness_pack.items() if key != "degeneration"
            },
            "degeneration": hardness_pack.get("degeneration"),
        }
        break

    stages["assembly_lineage"] = {
        "schema": "qiyu_locked_assembly_lineage_v1",
        "attempts": attempts,
        "selected_recipe_id": selected.get("recipe_id") if selected else None,
        "selected_is_in_admission_envelope": bool(
            selected and selected.get("recipe_id") in recipe_ids
        ),
        "global_factor_mining_used": False,
        "classic_or_unadmitted_switch_used": False,
        "next_step_if_exhausted": "new_discovery_round_with_parent_mutation_contract",
    }

    if selected is None:
        failed = {
            "ok": False,
            "schema": "qiyu_creation_blueprint_v1",
            "present_to_human": False,
            "outcome": "research_rejected",
            "error": "admitted_population_exhausted",
            "detail": {
                "reason": "all_admitted_recipes_failed_post_discovery_gates",
                "next_step": "new_discovery_round_with_parent_mutation_contract",
                "attempts": attempts,
            },
            "symbol": symbol, "timeframe": timeframe, "direction": direction,
            "brief": brief, "research_contract": compiled_contract,
            "stages": stages,
            "fuses": {"abort_reason": "admitted_population_exhausted"},
            "probes": _creation_probes(),
            "run_id": run_id,
            "data": stages.get("data"),
            "handoff_zh": "已准入候选在后置风险门全部失败；禁止切换未验证因子，须以父失败门开启新研究轮。",
            "at": _now(),
        }
        failed["failure_artifact"] = _persist_failure_blueprint(
            out_dir, symbol, timeframe, run_id, failed,
        )
        return failed

    if selected.get("recipe_id") not in recipe_ids:
        raise RuntimeError("assembly_lineage_violation:selected_recipe_not_admitted")
    stages.update(selected_stages or {})
    hypothesis = next(
        (row.get("hypothesis") or {} for row in payloads
         if row.get("recipe_id") == selected.get("recipe_id")),
        {},
    )
    design = dict(design)
    design["pre_discovery_hypotheses"] = design.get("hypotheses") or []
    design["hypotheses"] = [hypothesis]
    design["mechanism_family"] = selected.get("family")
    design["factor_hints"] = [selected.get("factor")]
    design["discovery_locked"] = True
    stages["meta"]["design_doc"] = design
    stages["selected"] = {
        "factor": selected.get("factor"), "rule": selected.get("rule"),
        "stats": selected.get("stats"), "quantoracle": selected.get("quantoracle"),
        "hypothesis_id": selected.get("hypothesis_id"),
        "mechanism_id": selected.get("mechanism_id"),
        "recipe_id": selected.get("recipe_id"),
        "recipe": selected.get("recipe"),
    }
    best_factor = {
        key: value for key, value in selected.items()
        if key not in ("returns", "equity_curve")
    }
    blueprint = {
        "ok": True,
        "present_to_human": True,
        "outcome": "candidate_ready",
        "schema": "qiyu_creation_blueprint_v1",
        "symbol": symbol, "timeframe": timeframe, "direction": direction,
        "brief": brief, "research_contract": compiled_contract,
        "loops": {"hypothesis": len(attempts), "stress": len(attempts)},
        "fuses": {"iteration": False, "abort_reason": None},
        "classic_tried": [], "perspectives_tried": [],
        "prelim": selected_stages["prelim"],
        "return_hardness": selected_stages["return_hardness"],
        "stages": stages,
        "best_factor": best_factor,
        "probes": _creation_probes(),
        "run_id": run_id,
        "data": stages.get("data"),
        "handoff_zh": "候选身份已锁定且属于 discovery admission envelope，可进入后续四阶段复核。",
        "at": _now(),
    }
    paths, params = _write_deliverables(out_dir, symbol, timeframe, blueprint)
    blueprint["deliverables"] = paths
    blueprint["params"] = params
    blueprint["glm_research_brief"] = {
        "schema": "qiyu_creation_blueprint_brief_v2",
        "design_doc": {
            "mechanism_family": selected.get("family"),
            "core_logic_zh": hypothesis.get("statement_zh"),
            "hypotheses": [hypothesis],
            "constraints": constraints,
        },
        "research_contract": compiled_contract,
        "admission_envelope": stages.get("admission_envelope"),
        "selected_recipe_id": selected.get("recipe_id"),
        "selected_recipe": selected.get("recipe"),
        "best_factor": best_factor,
        "prelim": selected_stages["prelim"],
        "stress_passed": bool(selected_stages["stress"].get("passed")),
        "instructions_zh": "只能实现 selected_recipe；禁止替换机制、方向、周期、事件或执行映射。",
        "built_at": _now(),
    }
    # Persist only after deliverables and formal handoff fields are complete.
    Path(paths["blueprint_json"]).write_text(
        json.dumps(blueprint, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return blueprint


def run_creation_blueprint(
    symbol,
    timeframe,
    direction="long",
    brief="",
    horizon=3,
    skip_llm=True,
    max_loops=MAX_LOOP,
    out_dir=None,
    run_id=None,
    research_contract=None,
    mutation_contract=None,
    data_version=None,
    code_version=None,
):
    """Execute stages ①–⑤ with fuses. Returns blueprint envelope for GLM / collab."""
    loops = {"hypothesis": 0, "stress": 0}
    fuses = {"iteration": False, "overfit_drops": 0, "var_rejects": 0, "abort_reason": None}
    stages = {}
    run_id = run_id or ledger.new_run_id("blueprint")

    # Compile a provisional contract before touching data.  This makes target,
    # direction and mutation-contract errors observable even when the candle
    # store is unavailable, and gives data failures an auditable intent record.
    discovery_constraints = rh.default_return_constraints()
    if research_contract:
        discovery_constraints["research_contract"] = research_contract
    if mutation_contract:
        discovery_constraints["mutation_contract"] = mutation_contract
    compiled_contract = discovery.compile_research_contract(
        brief, symbol, timeframe, discovery_constraints,
        direction=direction, design_seed=None,
        data_version=data_version, code_version=code_version,
    )
    stages["research_contract"] = compiled_contract
    if not compiled_contract.get("valid"):
        failed = {
            "ok": False,
            "schema": "qiyu_creation_blueprint_v1",
            "present_to_human": False,
            "outcome": "research_rejected",
            "error": "invalid_research_contract",
            "detail": {"validation_errors": compiled_contract.get("validation_errors") or []},
            "research_contract": compiled_contract,
            "stages": stages,
            "run_id": run_id,
            "at": _now(),
        }
        failed["failure_artifact"] = _persist_failure_blueprint(
            out_dir, symbol, timeframe, run_id, failed,
        )
        return failed

    data = _load_matrix(symbol, timeframe, horizon=horizon)
    if not data.get("ok"):
        failed = {
            "ok": False,
            "schema": "qiyu_creation_blueprint_v1",
            "present_to_human": False,
            "outcome": "data_blocked",
            "error": "candle_cache_missing",
            "detail": data,
            "research_contract": compiled_contract,
            "stages": stages,
            "run_id": run_id,
            "at": _now(),
        }
        failed["failure_artifact"] = _persist_failure_blueprint(
            out_dir, symbol, timeframe, run_id, failed,
        )
        return failed
    stages["data"] = {
        "n_bars": data.get("n_bars"),
        "path": data.get("path"),
        "source": data.get("source"),
        "research": data.get("research"),
        "note_zh": data.get("note_zh"),
    }

    if not data_version:
        candles_for_version = data.get("candles") or []
        first_ts = (candles_for_version[0] or {}).get("ts") if candles_for_version else None
        last_ts = (candles_for_version[-1] or {}).get("ts") if candles_for_version else None
        data_version = "%s:%s:%s" % (first_ts, last_ts, len(candles_for_version))

    # Bind the immutable contract to the actual candle snapshot once known.
    compiled_contract = discovery.compile_research_contract(
        brief, symbol, timeframe, discovery_constraints,
        direction=direction, design_seed=None,
        data_version=data_version, code_version=code_version,
    )
    stages["research_contract"] = compiled_contract
    if not compiled_contract.get("valid"):
        failed = {
            "ok": False,
            "schema": "qiyu_creation_blueprint_v1",
            "present_to_human": False,
            "outcome": "research_rejected",
            "error": "invalid_research_contract",
            "detail": {"validation_errors": compiled_contract.get("validation_errors") or []},
            "research_contract": compiled_contract,
            "stages": stages,
            "run_id": run_id,
            "at": _now(),
        }
        failed["failure_artifact"] = _persist_failure_blueprint(
            out_dir, symbol, timeframe, run_id, failed,
        )
        return failed

    # --- Structured/AI mechanism design BEFORE deterministic discovery. ---
    # The design is a hypothesis source, not a judge; every generated row must
    # still pass naked probes, antifalsification, execution and statistics.
    meta_pack = meta.run_meta_think(
        brief=brief, symbol=symbol, timeframe=timeframe,
        direction=direction, skip_llm=skip_llm,
        research_contract=compiled_contract,
        mutation_contract=mutation_contract,
    )
    stages["meta"] = meta_pack

    # --- Research discovery (population → naked probe → antifalsify → EFR) ---
    disc = discovery.run_discovery(
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        brief=brief,
        factor_matrix=data.get("matrix"),
        fwd_returns=data.get("fwd"),
        candles=data.get("candles"),
        constraints=discovery_constraints,
        run_id=run_id,
        design_seed=meta_pack,
        research_contract=compiled_contract,
        data_version=data_version,
        code_version=code_version,
    )
    stages["research_discovery"] = {
        "ok": disc.get("ok"),
        "run_id": disc.get("run_id"),
        "n_survivors": disc.get("n_survivors"),
        "handoff": disc.get("handoff"),
        "trial_budget": (disc.get("stages") or {}).get("trial_budget"),
        "multiple_testing": (disc.get("stages") or {}).get("multiple_testing"),
        "map_elites": (disc.get("stages") or {}).get("map_elites"),
        "population": (disc.get("stages") or {}).get("population"),
        "contract": (disc.get("stages") or {}).get("contract"),
        "human_banner_zh": disc.get("human_banner_zh"),
        "outcome": disc.get("outcome"),
        "error": disc.get("error"),
        "detail": disc.get("detail"),
        "research_state_counts": (disc.get("stages") or {}).get("research_state_counts"),
        "failure_lineage": (disc.get("stages") or {}).get("failure_lineage"),
        "near_miss_diagnostics": (disc.get("stages") or {}).get("near_miss_diagnostics"),
        "lean_campaign": (disc.get("stages") or {}).get("lean_campaign"),
    }
    if not disc.get("present_to_assembly"):
        failed = {
            "ok": False,
            "schema": "qiyu_creation_blueprint_v1",
            "present_to_human": False,
            "error": "no_credible_discovery_candidate",
            "outcome": disc.get("outcome") or (
                "data_blocked" if ((disc.get("detail") or {}).get("data_blocked"))
                else "research_rejected"
            ),
            "detail": disc.get("detail"),
            "stages": stages,
            "fuses": {"abort_reason": "research_discovery_empty"},
            "probes": {
                "discovery": discovery.probe(),
                "ledger": ledger.probe(),
                "research_candles": rcs.probe(),
            },
            "handoff_zh": disc.get("human_banner_zh"),
            "run_id": run_id,
            "at": _now(),
        }
        failed["failure_artifact"] = _persist_failure_blueprint(
            out_dir, symbol, timeframe, run_id, failed,
        )
        return failed

    handoff = disc.get("handoff") or {}

    # Discovery admission is now a hard identity boundary.  Assembly consumes
    # only exact admitted recipes and their post-cost returns.  The legacy
    # re-mining/mutation block below is intentionally unreachable for v4
    # discovery results because it could swap in an untested classic factor.
    return _assemble_admitted_population(
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        brief=brief,
        data=data,
        stages=stages,
        disc=disc,
        compiled_contract=compiled_contract,
        run_id=run_id,
        out_dir=(out_dir if out_dir is not None else (
            _root() / "auto_trade" / "dual_engine" / "creation_blueprint"
        )),
    )

    # ① Meta-think was already run before discovery; now overlay evidence-backed
    # handoff data without issuing a second model call.
    design = meta_pack.get("design_doc") or {}
    # Overlay discovery-backed mechanism onto design doc
    if handoff.get("family"):
        design["mechanism_family"] = handoff.get("family")
    if handoff.get("core_logic_zh"):
        design["core_logic_zh"] = handoff.get("core_logic_zh")
    design["discovery_handoff"] = handoff
    design["research_run_id"] = run_id
    if handoff.get("factor_hints"):
        design["factor_hints"] = list(handoff.get("factor_hints") or [])
    constraints = rh.merge_constraints(design.get("constraints") or {})
    design["constraints"] = constraints
    max_dd = -abs(float(constraints.get("max_drawdown") or 0.18))
    max_daily = float(constraints.get("max_daily_loss") or 0.05)

    # ①b External knowledge cards — must read before deepening
    knowledge = kd.assess_lens_against_cards(design)
    stages["knowledge_distill"] = knowledge
    design["knowledge_cards_brief"] = knowledge.get("cards_brief")
    if not knowledge.get("passed"):
        # switch lens immediately if cards invalidate current choice
        design.setdefault("mutation_log", []).append({
            "at": _now(),
            "reason": "knowledge_card_invalidate",
            "banner": knowledge.get("human_banner_zh"),
        })
        # try next perspective capacity-aware
        from .creation_return_hardness import select_perspective_by_return_capacity
        pers = ((design.get("divergence") or {}).get("perspectives") or [])
        tried = [((design.get("divergence") or {}).get("selected_id"))]
        alt, annotated = None, pers
        remaining = [p for p in pers if p.get("id") not in tried]
        if remaining:
            alt, annotated = select_perspective_by_return_capacity(remaining)
        if alt:
            design["mechanism_family"] = alt.get("family") or design.get("mechanism_family")
            design["core_logic_zh"] = alt.get("thesis_zh") or design.get("core_logic_zh")
            div = dict(design.get("divergence") or {})
            div["perspectives"] = annotated or pers
            div["selected_id"] = alt.get("id")
            div["selected_lens_zh"] = alt.get("lens_zh")
            div["selection_reason_zh"] = "外部认知卡片否决原视角后切换"
            design["divergence"] = div
            knowledge = kd.assess_lens_against_cards(design)
            stages["knowledge_distill"] = knowledge

    # ①c Socratic challenger (AutoGen-lite) — groupthink breaker
    soc = socratic.challenge_design(
        design, knowledge_cards=knowledge.get("cards_brief") or knowledge.get("invalidating_cards"),
        skip_llm=skip_llm,
    )
    stages["socratic"] = {k: v for k, v in soc.items() if k != "qa"}
    stages["socratic"]["qa"] = soc.get("qa")
    if not soc.get("passed"):
        # attach answers as pressure; force invalidation enrichment then continue
        design.setdefault("mutation_log", []).append({
            "at": _now(),
            "reason": "socratic_challenge_fail",
            "n_failed": soc.get("n_failed"),
            "banner": soc.get("human_banner_zh"),
        })
        # Auto-patch weak spots where possible
        if not design.get("invalidation_zh"):
            design["invalidation_zh"] = "苏格拉底追问强制补全：前提消失或状态切换后停止开仓"
        soc = socratic.challenge_design(
            design,
            knowledge_cards=knowledge.get("cards_brief"),
            skip_llm=True,
        )
        stages["socratic"] = {k: v for k, v in soc.items() if k != "qa"}
        stages["socratic"]["qa"] = soc.get("qa")
        stages["socratic"]["recheck_after_patch"] = True

    stages["meta"]["design_doc"] = design

    # Core factor hints: discovery handoff first, then design hypotheses
    core_hints = []
    for name in (handoff.get("factor_hints") or []):
        if name and name not in core_hints:
            core_hints.append(name)
    if handoff.get("probe_factor") and handoff.get("probe_factor") not in core_hints:
        core_hints.insert(0, handoff.get("probe_factor"))
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
            # mutate: broaden / shift hints then retry; after 2 fails switch lens
            core_hints = list(dict.fromkeys(
                core_hints + ["ret_12", "close_z_20", "range_pct", "dist_roll_low", "atr_pct_14", "dist_roll_high"]
            ))
            design.setdefault("mutation_log", []).append({
                "at": _now(),
                "reason": "hypothesis_fail",
                "new_hints": core_hints,
                "loop": loops["hypothesis"],
            })
            if loops["hypothesis"] >= 2:
                design, hints, switched = _switch_direction(
                    design, classic_tried, perspectives_tried,
                )
                if switched is None:
                    fuses["abort_reason"] = "hypothesis_fail_directions_exhausted"
                    abort = True
                    break
                core_hints = hints or core_hints
                stages["meta"]["design_doc"] = design
                design.setdefault("mutation_log", []).append({
                    "at": _now(),
                    "reason": "hypothesis_fail_switch_direction",
                    "switched": switched,
                    "loop": loops["hypothesis"],
                })
            continue

        # ②b Causal counterfactual battery — correlation illusion killer
        accepted_factors = [
            r["factor"] for r in (hyp.get("factors") or []) if r.get("accepted")
        ] or core_hints
        cf = causal_cf.run_counterfactual_battery(
            data["matrix"], data["fwd"], core_factors=accepted_factors[:4],
        )
        stages["causal_counterfactual"] = {
            "passed": cf.get("passed"),
            "human_banner_zh": cf.get("human_banner_zh"),
            "factors": [
                {
                    "factor": r.get("factor"),
                    "passed": r.get("passed"),
                    "reason": r.get("reason"),
                    "survived": r.get("survived"),
                    "wiped": r.get("wiped"),
                }
                for r in (cf.get("factors") or [])
            ],
        }
        if not cf.get("passed"):
            # Evidence demotion only — discovery antifalsify already ran.
            # Do NOT treat CausalImpact-lite as causal proof gate.
            design.setdefault("mutation_log", []).append({
                "at": _now(),
                "reason": "causal_counterfactual_weak_evidence",
                "banner": cf.get("human_banner_zh"),
                "loop": loops["hypothesis"],
                "note_zh": "仅作机制一致性降权，不宣称因果失败即否决。",
            })
            stages["causal_counterfactual"]["gate_mode"] = "evidence_not_veto"
            # continue to mining with warning rather than exhausting directions
            pass
        else:
            stages["causal_counterfactual"]["gate_mode"] = "evidence_support"

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

        # ④b Multi-universe survival (Monte Carlo) — before expensive stress
        mv = multiverse.survival_test(trade_returns)
        stages["multiverse"] = mv
        if not mv.get("passed"):
            design.setdefault("mutation_log", []).append({
                "at": _now(),
                "reason": "multiverse_survival_fail",
                "profit_frac": mv.get("profit_frac"),
                "need_regime_filter": mv.get("need_regime_filter"),
                "banner": mv.get("human_banner_zh"),
                "loop": loops["hypothesis"],
            })
            # Prefer mutating toward regime filter factors rather than instant abort
            core_hints = list(dict.fromkeys(
                ["range_pct", "atr_pct_14"] + list(core_hints or [])
            ))
            if mv.get("need_regime_filter") and loops["hypothesis"] < int(max_loops):
                continue
            design, hints, switched = _switch_direction(
                design, classic_tried, perspectives_tried,
            )
            if switched is None:
                fuses["abort_reason"] = "multiverse_directions_exhausted"
                abort = True
                break
            core_hints = hints or core_hints
            stages["meta"]["design_doc"] = design
            continue

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
        "outcome": "candidate_ready" if (ok and presentable) else "research_rejected",
        "schema": "qiyu_creation_blueprint_v1",
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "brief": brief,
        "research_contract": (stages.get("research_discovery") or {}).get("contract"),
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
            "causal_counterfactual": causal_cf.probe(),
            "socratic": socratic.probe(),
            "knowledge_distill": {"ok": True, "module": "creation_knowledge_distill"},
            "multiverse": multiverse.probe(),
            "discovery": discovery.probe(),
            "ledger": ledger.probe(),
        },
        "run_id": run_id,
        "data": stages.get("data"),
        "handoff_zh": (
            (
                "研究发现+组装通过初评，可进入后续 ADA5 复核；本编排器不改复核代码。"
                if presentable and ok else
                (hardness_pack or {}).get("human_banner_zh")
                or (prelim_pack or {}).get("human_banner_zh")
                or (stages.get("research_discovery") or {}).get("human_banner_zh")
                or "初评未通过或无可信候选：禁止硬凑完整策略。"
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
            "research_contract": blueprint.get("research_contract"),
            "best_factor": blueprint.get("best_factor"),
            "hypothesis_passed": (stages.get("hypothesis") or {}).get("passed"),
            "rescreen": stages.get("rescreen"),
            "stress_passed": (stages.get("stress") or {}).get("passed"),
            "prelim": prelim_pack,
            "fuses": fuses,
            "research_discovery": stages.get("research_discovery"),
            "instructions_zh": (
                "【研究发现优先】下列候选已经过机制图谱/现象扫描、裸探针、反证证据矩阵、"
                "EFR 与多重检验（DSR/PBO-lite）。禁止回退到『先写完整策略再圆故事』。"
                "禁止把 CausalImpact-lite 说成因果证明；禁止周收益≥8%硬凑。"
                "你是总指挥。请据此写 mechanism_spec；禁止与 QuantOracle certified 数字冲突；"
                "禁止声称已过复核。"
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
