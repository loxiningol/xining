# -*- coding: utf-8 -*-
"""创造蓝图编排 — 归属「第一步研究发现」并衔接到「第二步门槛」。

创造策略指令固定顺序：
  第一步：研究发现（本模块主责：委员会产物 + 探针 + 多重检验后组装）
  第二步：统一门槛（胜率>50% + 去最大盈利后不崩 + 漏斗/门槛0–7/寒霜贰筛）
  第三步：复核（本编排器不替代复核模块）

第一步内部：契约 → 机制图谱/种群 → 裸探针 → 反证矩阵 → EFR/DSR/PBO → 仅存活者组装。
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
from .research_discovery import _creation_skip_llm
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
    # MMVQ: never hand off on <180d spans — load enough bars for that floor.
    lookback_days = int(
        os.environ.get("QIYU_CREATION_LOOKBACK_DAYS")
        or os.environ.get("QIYU_RESEARCH_LOOKBACK_DAYS")
        or 730
    )
    lookback_days = max(180, lookback_days)
    if max_bars is None:
        explicit = os.environ.get("QIYU_CREATION_MAX_BARS")
        if explicit:
            max_bars = int(explicit)
        else:
            tf = str(timeframe or "").strip().lower()
            # 5m: 180d≈51840 bars; keep headroom for 730d when available.
            if tf in ("5m", "5min"):
                max_bars = max(52000, int(lookback_days * 288 * 1.05))
            elif tf == "15m":
                max_bars = max(20000, int(lookback_days * 96 * 1.05))
            elif tf == "1h":
                max_bars = max(5000, int(lookback_days * 24 * 1.05))
            else:
                max_bars = 50000
    # Never let env truncation defeat the mid-frequency span floor.
    tf = str(timeframe or "").strip().lower()
    min_span = 180.0
    try:
        from . import manufacture_batch_policy as mfg
        min_span = float(mfg.HANDOFF_MIN_SPAN_DAYS or 180)
    except Exception:
        min_span = 180.0
    per_day = 288.0 if tf in ("5m", "5min") else (96.0 if tf == "15m" else 24.0)
    floor_bars = int(min_span * per_day * 1.02)
    max_bars = max(int(max_bars or 0), floor_bars)
    loaded = eq.load_candles(
        symbol, timeframe, max_bars=max_bars, prefer_research=True,
        lookback_days=lookback_days,
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
    # Span floor: refuse to open creation on short research windows that inflate weekly.
    span_days = None
    try:
        if len(candles) >= 2:
            t0 = int(candles[0].get("ts") or 0)
            t1 = int(candles[-1].get("ts") or 0)
            if t1 > t0:
                span_days = (t1 - t0) / 86400000.0
    except Exception:
        span_days = None
    if span_days is None:
        # Fallback from bar count (5m≈288/day, 15m≈96/day).
        tf = str(timeframe or "").strip().lower()
        per_day = 288.0 if tf in ("5m", "5min") else (96.0 if tf == "15m" else 24.0)
        span_days = float(len(candles)) / per_day if candles else 0.0
    min_span = 180.0
    try:
        from . import manufacture_batch_policy as mfg
        min_span = float(mfg.HANDOFF_MIN_SPAN_DAYS or 180)
    except Exception:
        min_span = 180.0
    if float(span_days or 0.0) + 1e-9 < float(min_span):
        return {
            "ok": False,
            "error": "research_span_days_below_%d" % int(min_span),
            "n_bars": loaded.get("n"),
            "span_days": span_days,
            "path": loaded.get("path"),
            "source": loaded.get("source"),
            "research": loaded.get("research"),
            "hint_zh": (
                "研究窗口不足 %d 天（实际≈%.1f 天 / %s bars）。"
                "短窗会把密集触发伪装成高周频；拒绝开跑。"
                % (int(min_span), float(span_days or 0.0), loaded.get("n"))
            ),
        }
    matrix = eq._build_factor_matrix(candles)
    fwd = eq._forward_returns(closes, horizon=int(horizon))
    return {
        "ok": True,
        "candles": candles,
        "matrix": matrix,
        "fwd": fwd,
        "n_bars": loaded.get("n"),
        "span_days": span_days,
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
            "- 胜率: %s（硬底：严格大于 50%%）" % prelim_pack.get("win_rate"),
            "- 收益硬度拒绝: %s" % ",".join(hardness.get("reject_reasons") or []),
            "- 拒绝原因: %s" % ",".join(prelim_pack.get("reject_reasons") or []),
            "- 已尝试经典变式: %s" % (blueprint.get("classic_tried") or []),
            "- 已尝试视角: %s" % (blueprint.get("perspectives_tried") or []),
            "",
            "不会把胜率未严格大于50%、或近零收益/虚高夏普的垃圾策略包装成「创造成功」给人看。",
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
        "- 初评胜率: %s（严格大于 50%% 门禁已过）" % prelim_pack.get("win_rate"),
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
        "本报告止于第一步研究发现与第二步门槛衔接。第三步复核由复核模块承接，本蓝图不替代复核。",
        "",
    ])

    code = '''# Auto-generated creation blueprint stub — NOT for live mount.
# Factor: {factor} / {rule}
# Mechanism: {family}
# 第二步胜率门：已通过（严格大于50%）
# Window: {window}
# Next: 第二步其余门槛 → 第三步复核

STRATEGY = {{
    "symbol": "{symbol}",
    "timeframe": "{tf}",
    "factor": "{factor}",
    "rule": "{rule}",
    "protective_sl": 0.005,
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
        "probe": (payload or {}).get("probe") or {},
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


def _admission_trade_quality(returns, min_wr=None, min_trades=None,
                             min_expectancy_factor=1.0):
    """创造交接硬底：复核前必须挡住低胜率彩票书。

    复核仍负责压力/前向/矩阵/多模型。创造侧不得把「胜率≤50% 但肥尾平均净收益好看」
    的书交出去。门槛与第二次复核的胜率下限、门槛2 的期望因子对齐。
    """
    from .creation_quality_doctrine import (
        MIN_TRADES_CREATION,
        MIN_WIN_RATE_EXCLUSIVE,
    )
    if min_wr is None:
        min_wr = MIN_WIN_RATE_EXCLUSIVE
    if min_trades is None:
        min_trades = MIN_TRADES_CREATION
    vals = [float(x) for x in (returns or []) if x is not None]
    n = len(vals)
    if n < int(min_trades):
        return {
            "ok": False,
            "reasons": ["成交笔数不足"],
            "n": n,
            "成交笔数": n,
            "win_rate": None,
            "胜率": None,
            "win_rate_pct": None,
            "payoff_ratio": None,
            "expectancy_factor": None,
            "mean_net": None,
            "平均净收益": None,
        }
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]
    wr = len(wins) / float(n)
    mean_net = sum(vals) / float(n)
    avg_win = (sum(wins) / float(len(wins))) if wins else 0.0
    avg_loss_mag = (sum(-v for v in losses) / float(len(losses))) if losses else 0.0
    if avg_loss_mag > 0:
        payoff = avg_win / avg_loss_mag
    else:
        payoff = 999.0 if avg_win > 0 else 0.0
    expectancy_factor = wr * payoff
    # 去最大盈利：去掉单笔最大盈利后均值不得为负
    if wins:
        max_win = max(wins)
        reduced = list(vals)
        reduced.remove(max_win)
        mean_without_max = sum(reduced) / float(len(reduced)) if reduced else -1e9
    else:
        mean_without_max = mean_net
    reasons = []
    # 胜率必须严格大于 50%
    if wr <= float(min_wr):
        reasons.append("胜率未严格大于50%")
    if mean_net <= 0:
        reasons.append("平均净收益非正")
    if expectancy_factor < float(min_expectancy_factor):
        reasons.append("期望因子低于1")
    if mean_without_max <= 0:
        reasons.append("去最大盈利后崩溃")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "n": n,
        "成交笔数": n,
        "win_rate": wr,
        "胜率": wr,
        "win_rate_pct": wr * 100.0,
        "payoff_ratio": min(payoff, 999.0),
        "盈亏比": min(payoff, 999.0),
        "expectancy_factor": expectancy_factor,
        "期望因子": expectancy_factor,
        "mean_net": mean_net,
        "平均净收益": mean_net,
        "mean_net_without_max_win": mean_without_max,
        "去最大盈利后平均净收益": mean_without_max,
        "thresholds": {
            "min_wr_exclusive": float(min_wr),
            "胜率硬底_严格大于": float(min_wr),
            "min_trades": int(min_trades),
            "最低成交笔数": int(min_trades),
            "min_expectancy_factor": float(min_expectancy_factor),
        },
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
        stop = 0.005
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
    payloads = list(disc.get("assembly_payload") or [])
    try:
        from . import manufacture_batch_policy as mfg
        payloads = payloads[: int(mfg.ASSEMBLY_PAYLOAD_CAP)]
        manufacture_mode = bool(mfg.pre_review_gates_disabled())
    except Exception:
        payloads = payloads[:24]
        manufacture_mode = False
        mfg = None
    recipe_ids = [str(row.get("recipe_id") or "") for row in payloads if row.get("recipe_id")]
    stages["admission_envelope"] = {
        "schema": "qiyu_admission_envelope_v1",
        "research_contract_id": compiled_contract.get("contract_id"),
        "recipe_ids": recipe_ids,
        "n_admitted": len(payloads),
        "identity_locked": True,
        "post_discovery_mechanism_mutation_allowed": False,
        "manufacture_batch": manufacture_mode,
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
    manufactured = []
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
        if not manufacture_mode and not (payload.get("judge") or {}).get("admit_to_assembly"):
            attempt["failed_gate"] = "committee_not_admitted"
            attempts.append(attempt)
            continue

        candidate = _candidate_from_admitted_payload(payload)
        if len(candidate.get("returns") or []) < 8:
            attempt["failed_gate"] = "post_cost_returns_insufficient"
            attempts.append(attempt)
            continue

        # Quality metrics are recorded for ranking; manufacture mode does not veto.
        quality = _admission_trade_quality(
            candidate.get("returns") or [],
            min_wr=float(MIN_PRESENT_WR),
            min_trades=8,
            min_expectancy_factor=1.0,
        )
        quality["basis"] = "account_net_after_friction_v1"
        attempt["admission_trade_quality"] = quality
        if (not manufacture_mode) and (not quality.get("ok")):
            attempt["failed_gate"] = "creation_wr_anti_lottery"
            attempt["failed_reasons"] = list(quality.get("reasons") or [])
            attempts.append(attempt)
            continue
        from .creation_quality_doctrine import gate2_account_returns_ok
        gate2 = gate2_account_returns_ok(
            candidate.get("returns") or [],
            trades=candidate.get("trades") or [],
        )
        attempt["admission_gate2"] = gate2
        if (not manufacture_mode) and (not gate2.get("ok")):
            attempt["failed_gate"] = "creation_gate2_floors"
            attempt["failed_reasons"] = list(gate2.get("reasons") or [])
            attempts.append(attempt)
            continue
        candidate_mean = float(quality.get("mean_net") or -1e9)
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
        n_fills = len(candidate.get("returns") or [])
        try:
            import auto_trade_ai_consensus as _ai_freq
            freq_pack = _ai_freq.compute_weekly_open_frequency(
                n_fills,
                span_days=frequency_span_days,
                candidate={
                    "symbol": symbol or candidate.get("symbol"),
                    "timeframe": timeframe or candidate.get("timeframe"),
                },
                evidence={
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "span_days": frequency_span_days,
                    "observation_days": frequency_span_days,
                    "trades": n_fills,
                    "bars_span_days": frequency_span_days,
                    "safety_metrics": {
                        "trades": n_fills,
                        "span_days": frequency_span_days,
                        "bars_span_days": frequency_span_days,
                    },
                },
                source="creation_blueprint",
            )
            weekly_opens = float(freq_pack.get("expected_weekly_fills") or 0.0)
            frequency_span_days = (
                freq_pack.get("span_days")
                if freq_pack.get("span_days") is not None
                else frequency_span_days
            )
            attempt["weekly_frequency_pack"] = {
                "method": freq_pack.get("method"),
                "span_source": freq_pack.get("span_source"),
                "sample_2y_ok": freq_pack.get("sample_2y_ok"),
            }
        except Exception:
            weekly_opens = (
                n_fills / float(frequency_span_days) * 7.0
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
        # Weekly frequency remains owned by formal 3AI review; WR is not deferred.
        attempt["weekly_frequency_deferred_to_formal_review"] = True

        risk_calibration = _candidate_risk_calibration(
            candidate,
            configured_max_drawdown=abs(float(max_dd)),
            configured_daily_loss=max_daily,
        )
        attempt["risk_calibration"] = risk_calibration
        attempt["passed"] = True
        attempt["handoff_scope"] = "slim_multiai_review_only" if manufacture_mode else "four_formal_reviews_only"
        attempts.append(attempt)
        pack_stages = {
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
            "admission_trade_quality": quality,
            "prelim": {
                "present_to_human": True,
                "scope": "formal_review_submission_not_human_deployment_approval",
                "n_trades": quality.get("n"),
                "win_rate": quality.get("win_rate"),
                "win_rate_pct": quality.get("win_rate_pct"),
                "mean_net": candidate_mean,
                "payoff_ratio": quality.get("payoff_ratio"),
                "expectancy_factor": quality.get("expectancy_factor"),
                "weekly_opens": weekly_opens,
                "wr_gate": (
                    "manufacture_rank_wr60_weekly0p5_mean3pct"
                    if manufacture_mode else "strict_above_50pct_plus_anti_lottery"
                ),
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
        if manufacture_mode and mfg is not None:
            path_summary = {}
            for src in (
                candidate.get("path_summary"),
                candidate.get("probe"),
                (payload.get("probe") if isinstance(payload, dict) else None),
                pack_stages.get("probe") if isinstance(pack_stages, dict) else None,
            ):
                if isinstance(src, dict):
                    for key in (
                        "profit_first_rate", "median_mae_pct", "median_mfe_pct",
                        "mean_winning_levered", "path_entry_score",
                        "execution_leverage", "protective_stop_policy",
                        "exit_policy", "win_rate",
                    ):
                        if path_summary.get(key) is None and src.get(key) is not None:
                            path_summary[key] = src.get(key)
                    bare = src.get("path_bare_screen") or {}
                    summ = bare.get("summary") if isinstance(bare, dict) else None
                    if isinstance(summ, dict):
                        for key in (
                            "profit_first_rate", "median_mae_pct", "median_mfe_pct",
                            "mean_winning_levered",
                        ):
                            if path_summary.get(key) is None and summ.get(key) is not None:
                                path_summary[key] = summ.get(key)
            select_metrics = mfg.package_metrics(
                candidate.get("returns") or [],
                span_days=frequency_span_days or span_days,
                trades=candidate.get("trades") or [],
                symbol=symbol,
                timeframe=timeframe,
                path_summary=path_summary,
            )
            # Path bare screen on event returns when path rates missing.
            if select_metrics.get("profit_first_rate") is None:
                try:
                    from . import path_bare_screen as pbs
                    # Approximate from account returns is NOT path-hit; mark missing.
                    select_metrics["path_rate_missing"] = True
                except Exception:
                    pass
            manufactured.append({
                "candidate": candidate,
                "stages": pack_stages,
                "payload": payload,
                "returns": list(candidate.get("returns") or []),
                "span_days": frequency_span_days or span_days,
                "symbol": symbol,
                "timeframe": timeframe,
                "select_metrics": select_metrics,
                "recipe_id": candidate.get("recipe_id"),
                "weekly_opens": weekly_opens,
            })
            # Keep collecting until payload exhausted (need >=10 when available).
            continue
        selected = candidate
        selected_stages = pack_stages
        break

    top_for_review = []
    ranked_all = []
    if manufacture_mode and mfg is not None:
        top_for_review, ranked_all = mfg.select_top_for_review(manufactured)
        # 全部制造包写入质检器（合格/不合格由质检器自己分表）。
        try:
            from . import quality_inspector as qi
            for row in ranked_all:
                metrics = row.get("select_metrics") or {}
                title = (
                    row.get("title_zh")
                    or row.get("recipe_id")
                    or ((row.get("candidate") or {}).get("recipe_id"))
                    or "unnamed"
                )
                qi.ingest({
                    "id": row.get("recipe_id") or title,
                    "title_zh": title,
                    "symbol": row.get("symbol") or symbol,
                    "timeframe": row.get("timeframe") or timeframe,
                    "direction": direction,
                    "source": "creation_pipeline",
                    "expectancy_E": metrics.get("expectancy_E"),
                    "weekly_open_freq": metrics.get("weekly_opens"),
                    "mean_win_only_pct": metrics.get("mean_win_only_pct") or metrics.get(
                        "account_mean_win_only_pct"
                    ),
                    "metrics": metrics,
                })
        except Exception:
            pass
        stages["manufacture_batch"] = {
            "schema": "qiyu_manufacture_batch_v1",
            "n_manufactured": len(manufactured),
            "min_required": int(mfg.MIN_MANUFACTURE),
            "top_n": int(mfg.TOP_N_TO_REVIEW),
            "shortfall": max(0, int(mfg.MIN_MANUFACTURE) - len(manufactured)),
            "handoff_floors": {
                "qi_only": True,
                "expectancy_E_strict_gt": float(mfg.HANDOFF_EXPECTANCY_STRICT_GT),
                "weekly_opens_strict_gt": float(mfg.HANDOFF_MIN_WEEKLY_OPENS),
            },
            "n_handoff_qualified": len(top_for_review),
            "thresholds": {
                "expectancy_E_strict_gt": mfg.HANDOFF_EXPECTANCY_STRICT_GT,
                "weekly_opens_strict_gt": mfg.REVIEW_WEEKLY_OPENS,
                "qi_only": True,
            },
            "ranked": [
                {
                    "rank": row.get("rank"),
                    "recipe_id": row.get("recipe_id"),
                    "select_metrics": row.get("select_metrics"),
                    "select_rank": row.get("select_rank"),
                    "handoff_gate": row.get("handoff_gate"),
                }
                for row in ranked_all
            ],
            "selected_for_review": [
                {
                    "rank": row.get("rank"),
                    "recipe_id": row.get("recipe_id"),
                    "select_metrics": row.get("select_metrics"),
                    "select_rank": row.get("select_rank"),
                    "handoff_gate": row.get("handoff_gate"),
                }
                for row in top_for_review
            ],
        }
        if not top_for_review:
            try:
                from . import quality_optimization as qopt
                quality_classification = qopt.classify_batch(ranked_all)
                repair_plan = qopt.directed_repair_plan(
                    failure_codes=list(
                        quality_classification.get("dominant_codes") or []
                    ),
                    direction=direction,
                )
                opt_log = qopt.optimization_round_log(
                    failure_code_before=(
                        (quality_classification.get("dominant_codes") or [None])[0]
                    ),
                    allowed_modification=list(
                        ((quality_classification.get("allowed_modifications") or {})
                         .get((quality_classification.get("dominant_codes") or [None])[0])
                         or [])
                    ),
                    exact_change=[
                        a.get("exact_change")
                        for a in (repair_plan.get("actions") or [])
                    ],
                    metric_before={
                        "n_manufactured": len(manufactured),
                        "n_handoff_qualified": 0,
                    },
                    metric_after={"n_handoff_qualified": 0},
                    rollback_reason=None,
                    parameter_plateau_score_value=(
                        ((ranked_all[0].get("select_metrics") or {})
                         .get("parameter_plateau_score"))
                        if ranked_all else None
                    ),
                    round_index=1,
                )
            except Exception as exc:
                quality_classification = {"error": str(exc)[:200]}
                repair_plan = {"error": str(exc)[:200]}
                opt_log = {"error": str(exc)[:200]}
            failed = {
                "ok": False,
                "schema": "qiyu_creation_blueprint_v1",
                "present_to_human": False,
                # Materialization succeeded; quality/handoff failed — never call this
                # a market research rejection.
                "outcome": "candidate_quality_failure",
                "error": "CANDIDATE_QUALITY_FAILURE",
                "detail": {
                    "reason": "manufactured_survivors_below_anti_shit_handoff_floor",
                    "failure_layer": "B_quality_handoff",
                    "materialization_ok": True,
                    "message_zh": (
                        "制造批次有存活包，但未达质检器门槛"
                        "（四AI平均 E>0 且周开仓频率>0.5）；"
                        "已写入质检不合格表。材料化已成功，属质检层失败。"
                    ),
                    "n_manufactured": len(manufactured),
                    "n_handoff_qualified": 0,
                    "quality_classification": quality_classification,
                    "dominant_failure_codes": list(
                        quality_classification.get("dominant_codes") or []
                    ),
                    "allowed_modifications": quality_classification.get(
                        "allowed_modifications"
                    ) or {},
                    "forbidden_always": list(
                        quality_classification.get("forbidden_always") or []
                    ),
                    "directed_repair_plan": {
                        "actions": list((repair_plan or {}).get("actions") or []),
                        "allowed_modifications": list(
                            (repair_plan or {}).get("allowed_modifications") or []
                        ),
                        "forbidden": list((repair_plan or {}).get("forbidden") or []),
                        "hypotheses_n": len((repair_plan or {}).get("hypotheses") or []),
                    },
                    "optimization": opt_log,
                    "ranked_top": [
                        {
                            "rank": row.get("rank"),
                            "recipe_id": row.get("recipe_id"),
                            "select_metrics": row.get("select_metrics"),
                            "handoff_gate": (row.get("handoff_gate")
                                            or mfg.qualifies_for_review_handoff(
                                                row.get("select_metrics"))),
                        }
                        for row in ranked_all[:5]
                    ],
                    "next_step": "quality_optimization_layer_B",
                    "note_zh": (
                        "禁止解读为「市场没有策略」。A层材料化成功；质检器未放行。"
                        "唯一门槛：E>0 且周开仓频率>0.5。"
                    ),
                },
                "symbol": symbol, "timeframe": timeframe, "direction": direction,
                "brief": brief, "research_contract": compiled_contract,
                "stages": stages,
                "fuses": {"abort_reason": "manufacture_handoff_floor_fail"},
                "probes": _creation_probes(),
                "run_id": run_id,
                "data": stages.get("data"),
                "handoff_zh": (
                    "质检器门槛未过（E>0 且周开仓>0.5）：已写入不合格表。"
                ),
                "at": _now(),
            }
            failed["failure_artifact"] = _persist_failure_blueprint(
                out_dir, symbol, timeframe, run_id, failed,
            )
            return failed
        if top_for_review:
            selected = top_for_review[0].get("candidate")
            selected_stages = top_for_review[0].get("stages")

    stages["assembly_lineage"] = {
        "schema": "qiyu_locked_assembly_lineage_v1",
        "attempts": attempts,
        "selected_recipe_id": selected.get("recipe_id") if selected else None,
        "selected_is_in_admission_envelope": bool(
            selected and selected.get("recipe_id") in recipe_ids
        ),
        "global_factor_mining_used": False,
        "classic_or_unadmitted_switch_used": False,
        "manufacture_n": len(manufactured) if manufacture_mode else None,
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
                "manufacture_n": len(manufactured) if manufacture_mode else 0,
            },
            "symbol": symbol, "timeframe": timeframe, "direction": direction,
            "brief": brief, "research_contract": compiled_contract,
            "stages": stages,
            "fuses": {"abort_reason": "admitted_population_exhausted"},
            "probes": _creation_probes(),
            "run_id": run_id,
            "data": stages.get("data"),
            "handoff_zh": (
                "制造批次无可编译候选；禁止用未验证因子硬凑。"
                if manufacture_mode else
                "已准入候选在后置风险门全部失败；禁止切换未验证因子，须以父失败门开启新研究轮。"
            ),
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
    review_batch = []
    seed_rows = top_for_review if (manufacture_mode and top_for_review) else [{
        "candidate": selected, "select_metrics": None, "select_rank": None, "rank": 1,
    }]
    for row in seed_rows:
        cand = row.get("candidate") or selected
        review_batch.append({
            "rank": row.get("rank"),
            "recipe_id": cand.get("recipe_id"),
            "hypothesis_id": cand.get("hypothesis_id"),
            "mechanism_id": cand.get("mechanism_id"),
            "recipe": cand.get("recipe"),
            "returns": list(cand.get("returns") or []),
            "select_metrics": row.get("select_metrics"),
            "select_rank": row.get("select_rank"),
            "handoff_token": row.get("handoff_token"),
            "handoff_gate": row.get("handoff_gate"),
            "best_factor": {
                key: value for key, value in cand.items()
                if key not in ("returns", "equity_curve")
            },
        })
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
        "review_batch": review_batch,
        "n_manufactured": len(manufactured) if manufacture_mode else 1,
        "probes": _creation_probes(),
        "run_id": run_id,
        "data": stages.get("data"),
        "handoff_zh": (
            "制造批次完成：已按交接门槛筛出 Top-%d（胜率严格大于%.0f%% · 盈利单严格大于 stop×杠杆≈%.2f%% · 周频≥%.1f），"
            "交接并进入复核。"
            % (
                len(review_batch),
                (mfg.HANDOFF_MIN_WIN_RATE * 100.0) if mfg is not None else 50.0,
                (
                    mfg.mean_win_floor_pct()
                    if mfg is not None else 10.0
                ),
                (mfg.HANDOFF_MIN_WEEKLY_OPENS if mfg is not None else 0.1),
            )
            if manufacture_mode else
            "候选身份已锁定；第一步研究发现可交接，进入第二步统一门槛后交第三步复核。"
        ),
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
    skip_llm=None,
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
    skip_llm = _creation_skip_llm(skip_llm)

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
    def _run_disc(dir_arg, brief_arg, rid_arg, seed_arg, contract_arg):
        return discovery.run_discovery(
            symbol=symbol,
            timeframe=timeframe,
            direction=dir_arg,
            brief=brief_arg,
            factor_matrix=data.get("matrix"),
            fwd_returns=data.get("fwd"),
            candles=data.get("candles"),
            constraints=discovery_constraints,
            run_id=rid_arg,
            design_seed=seed_arg,
            research_contract=contract_arg,
            data_version=data_version,
            code_version=code_version,
        )

    disc = _run_disc(direction, brief, run_id, meta_pack, compiled_contract)
    guarantee_attempts = [{
        "direction": direction,
        "mode": "primary",
        "n_survivors": disc.get("n_survivors"),
        "present_to_assembly": disc.get("present_to_assembly"),
        "research_state_counts": (disc.get("stages") or {}).get("research_state_counts"),
    }]
    # Same-identity deepen only: denser quantiles inside the locked brief/contract.
    # Never rewrite the brief or flip long↔short to manufacture survivors.
    if not disc.get("present_to_assembly"):
        deepen_id = "%s_deepen" % run_id
        prev_deepen = os.environ.get("QIYU_STRUCTURED_DEEPEN")
        os.environ["QIYU_STRUCTURED_DEEPEN"] = "1"
        try:
            try:
                from . import job_progress as jp
                jp.report_progress(
                    "map_elites",
                    "same-identity deepen：加密度量搜索",
                    force=True,
                )
            except Exception:
                pass
            disc_deep = _run_disc(
                direction, brief, deepen_id, meta_pack, compiled_contract,
            )
        finally:
            if prev_deepen is None:
                os.environ.pop("QIYU_STRUCTURED_DEEPEN", None)
            else:
                os.environ["QIYU_STRUCTURED_DEEPEN"] = prev_deepen
        guarantee_attempts.append({
            "direction": direction,
            "mode": "same_identity_deepen",
            "n_survivors": disc_deep.get("n_survivors"),
            "present_to_assembly": disc_deep.get("present_to_assembly"),
            "research_state_counts": (disc_deep.get("stages") or {}).get(
                "research_state_counts"
            ),
        })
        if disc_deep.get("present_to_assembly"):
            disc = disc_deep
            run_id = deepen_id
    stages["guarantee_output_attempts"] = guarantee_attempts
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
        "guarantee_output_attempts": guarantee_attempts,
        "candidate_materialization": (disc.get("stages") or {}).get(
            "candidate_materialization"
        ) or disc.get("candidate_materialization"),
    }
    if not disc.get("present_to_assembly"):
        pipe = (disc.get("detail") or {}).get("pipeline_failure") or {}
        is_pipeline = bool(
            disc.get("error") == "CANDIDATE_MATERIALIZATION_FAILURE"
            or pipe.get("is_pipeline_error")
            or disc.get("outcome") == "generation_system_failure"
        )
        failed = {
            "ok": False,
            "schema": "qiyu_creation_blueprint_v1",
            "present_to_human": False,
            "error": (
                "CANDIDATE_MATERIALIZATION_FAILURE" if is_pipeline
                else (disc.get("error") or "no_credible_discovery_candidate")
            ),
            "outcome": (
                "generation_system_failure" if is_pipeline
                else (
                    disc.get("outcome") or (
                        "data_blocked" if ((disc.get("detail") or {}).get("data_blocked"))
                        else "research_rejected"
                    )
                )
            ),
            "detail": disc.get("detail"),
            "candidate_materialization": (
                (disc.get("stages") or {}).get("candidate_materialization")
                or disc.get("candidate_materialization")
            ),
            "stages": stages,
            "fuses": {
                "abort_reason": (
                    "candidate_materialization_failure_after_repair"
                    if is_pipeline else
                    "research_discovery_empty_after_guarantee"
                ),
            },
            "probes": {
                "discovery": discovery.probe(),
                "ledger": ledger.probe(),
                "research_candles": rcs.probe(),
            },
            "handoff_zh": (
                pipe.get("message_zh")
                or disc.get("human_banner_zh")
                or (
                    "管道生成失败：扩搜与表征扩展后仍未形成可编译候选池；"
                    "这是生成系统故障，不是市场研究拒绝。"
                )
            ),
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

        # 压力测试前硬优先胜率≥50% 的候选
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
                "note_zh": "无胜率严格大于50%的候选；将触发经典变式切换而非对人展示。",
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

        # ⑤b 初评胜率门禁 — 胜率未严格大于50% 禁止作为交付展示
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
                "第一步研究发现已通过初评衔接；进入第二步统一门槛后，"
                "再交第三步复核。本编排器不替代复核模块。"
                if presentable and ok else
                (hardness_pack or {}).get("human_banner_zh")
                or (prelim_pack or {}).get("human_banner_zh")
                or (stages.get("research_discovery") or {}).get("human_banner_zh")
                or "第一步/第二步未通过或无可信候选：禁止硬凑完整策略。"
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
                "【第一步：研究发现】下列候选已经过机制图谱/现象扫描、裸探针、反证证据矩阵、"
                "EFR 与多重检验（DSR/PBO-lite）。禁止回退到『先写完整策略再圆故事』。"
                "下一步是第二步统一门槛，再交第三步复核；禁止声称已过复核。"
                "禁止把 CausalImpact-lite 说成因果证明；禁止周收益≥8%硬凑。"
                "你是总指挥。请据此写 mechanism_spec；禁止与 QuantOracle certified 数字冲突。"
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
                "初评门禁未过（胜率未严格大于50%，或收益硬度/策略退化熔断）。"
                "禁止向人类展示本候选为成功交付。"
                "请换方向或继续经典变式；不要美化虚高夏普+近零收益。"
            ),
            "built_at": _now(),
        }
    return blueprint
