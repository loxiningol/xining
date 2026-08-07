# -*- coding: utf-8 -*-
"""Manufacture-batch policy → 质检器唯一门槛。

User lock (2026-08-05 QI-only):
- 取消摩擦检验 / AF / 泄漏 / MMVQ 等一切管道硬杀
- 唯一硬门槛（与 quality_inspector 一致）：四AI平均 E>0 且周开仓频率>0.5
- W/R/n/span 等仅作诊断与排序，不再拦截交接
"""
from __future__ import print_function

import math

# ---- Batch manufacture ----
MIN_MANUFACTURE = 10
TOP_N_TO_REVIEW = 3
ASSEMBLY_PAYLOAD_CAP = 24

# ---- Sole review thresholds (QI-only) ----
REVIEW_WR = 0.50  # report / ranking advisory only
REVIEW_WR_PCT = 50.0
REVIEW_WEEKLY_OPENS = 0.5
REVIEW_MEAN_WIN_ONLY_PCT = 10.0  # legacy alias / ranking
REVIEW_MEAN_TRADE_RETURN = REVIEW_MEAN_WIN_ONLY_PCT / 100.0
REVIEW_MEAN_TRADE_PCT = REVIEW_MEAN_WIN_ONLY_PCT

PROTECTIVE_STOP_PRICE_PCT = 0.005
EXECUTION_LEVERAGE = 20
LEVERAGE_MIN = 20
LEVERAGE_MAX = 50

# Pre-QI diagnostic gates must not hard-kill (摩擦/AF/泄漏/因果等).
PRE_REVIEW_HARD_GATES_DISABLED = True

HANDOFF_MIN_WIN_ONLY_PCT = 10.0      # ranking advisory only
HANDOFF_MIN_WIN_RATE = 0.35          # creation READY / ranking advisory
HANDOFF_WIN_RATE_STRICT_GT = True
HANDOFF_MIN_WEEKLY_OPENS = REVIEW_WEEKLY_OPENS  # QI: weekly > 0.5
HANDOFF_MIN_TRADES = 1               # no n-floor kill at QI
HANDOFF_MIN_SPAN_DAYS = 180          # short windows → weekly untrusted / reject load
HANDOFF_MIN_PROFIT_FIRST_RATE = 0.0  # ranking artifact only
HANDOFF_MAX_MEDIAN_MAE = 1.0
HANDOFF_EXPECTANCY_STRICT_GT = 0.0   # QI: E > 0
HANDOFF_MIN_PAYOFF_R = 0.0           # ranking advisory only
HANDOFF_DEDUPE_METRIC_TOL = {
    "win_rate_pct": 0.05,
    "mean_win_only_pct": 0.05,
    "weekly_opens": 0.05,
    "profit_first_rate": 0.02,
    "expectancy_E": 0.05,
}


def _safe_float(value, default=None):
    try:
        if value is None:
            return default
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
        return default


def mean_win_floor_pct(metrics=None):
    """Legacy helper: stop_pct * leverage * 100. Not a hard gate anymore."""
    m = metrics or {}
    stop = _safe_float(m.get("protective_stop_price_pct"))
    if stop is None:
        stop = _safe_float(m.get("stop_pct"))
    if stop is None:
        stop = PROTECTIVE_STOP_PRICE_PCT
    lev = _safe_float(m.get("execution_leverage"))
    if lev is None:
        lev = _safe_float(m.get("leverage"))
    if lev is None:
        lev = EXECUTION_LEVERAGE
    lev = max(float(LEVERAGE_MIN), min(float(LEVERAGE_MAX), float(lev)))
    return float(stop) * float(lev) * 100.0


def expectancy_E(win_rate, payoff_R):
    """E = W×R − (1−W)."""
    w = _safe_float(win_rate)
    r = _safe_float(payoff_R)
    if w is None or r is None:
        return None
    if w < 0:
        return None
    if w > 1.0 and w <= 100.0:
        w = w / 100.0
    if w > 1.0 or r <= 0:
        return None
    return float(w) * float(r) - (1.0 - float(w))


def payoff_ratio(mean_win, mean_loss):
    """R = average win / |average loss|."""
    mw = _safe_float(mean_win)
    ml = _safe_float(mean_loss)
    if mw is None or ml is None or mw <= 0:
        return None
    abs_loss = abs(ml)
    if abs_loss <= 1e-15:
        return None
    return float(mw) / float(abs_loss)


def resolve_expectancy_inputs(metrics=None, wr_avg_pct=None, mean_win_avg_pct=None):
    """Build W/R/E from **account** metrics (+ optional AI averages for W / mean_win).

    Handoff/review MUST NOT use stop×leverage as mean_loss. Missing observed
    mean_loss → E unavailable → failed.
    """
    m = dict(metrics or {})
    # Account W only — never path profit-first overwrite.
    w = _safe_float(m.get("account_win_rate"))
    if w is None:
        wp = _safe_float(m.get("account_win_rate_pct"))
        if wp is not None:
            w = wp / 100.0
    # Fallback only when account fields absent AND basis is still account.
    if w is None and str(m.get("win_rate_basis") or "") in ("", "account_pnl_ratio", "None"):
        w = _safe_float(m.get("win_rate"))
        if w is None:
            wp = _safe_float(m.get("win_rate_pct"))
            if wp is not None:
                w = wp / 100.0
    if wr_avg_pct is not None:
        aw = _safe_float(wr_avg_pct)
        if aw is not None:
            w = aw / 100.0 if aw > 1.0 else aw

    mean_win = _safe_float(m.get("account_mean_win_only_pct"))
    if mean_win is None:
        mean_win = _safe_float(m.get("mean_win_only_pct"))
    if mean_win is not None and 0 < abs(mean_win) < 0.5:
        mean_win = mean_win * 100.0
    if mean_win_avg_pct is not None:
        awm = _safe_float(mean_win_avg_pct)
        if awm is not None:
            if 0 < abs(awm) < 0.5:
                awm = awm * 100.0
            mean_win = awm

    mean_loss = _safe_float(m.get("account_mean_loss_only_pct"))
    if mean_loss is None:
        mean_loss = _safe_float(m.get("mean_loss_only_pct"))
    if mean_loss is not None and 0 < abs(mean_loss) < 0.5:
        mean_loss = mean_loss * 100.0
    if mean_loss is None or abs(mean_loss) <= 1e-15:
        # No stop×L fallback — E unavailable (质检器也会判不合格).
        return {
            "W": w,
            "R": None,
            "E": None,
            "mean_win_pct": mean_win,
            "mean_loss_pct": None,
            "loss_basis": "missing_observed_mean_loss",
            "passed": False,
        }

    r = payoff_ratio(mean_win, mean_loss)
    e = expectancy_E(w, r)
    return {
        "W": w,
        "R": r,
        "E": e,
        "mean_win_pct": mean_win,
        "mean_loss_pct": abs(mean_loss),
        "loss_basis": "mean_loss_only_pct",
        "passed": (e is not None and e > HANDOFF_EXPECTANCY_STRICT_GT + 1e-15),
    }


def pre_review_gates_disabled():
    return bool(PRE_REVIEW_HARD_GATES_DISABLED)


def slim_multiai_review_only():
    """复核路径：跳过 L0–L7 / Gate0–6 / Phase5，只走精简多模型均值。"""
    return True


def qualifies_for_review_handoff(metrics):
    """质检器唯一门槛：E>0 且周开仓频率>0.5（与 quality_inspector 一致）。"""
    m = metrics or {}
    reasons = []
    n = int(_safe_float(m.get("n") or m.get("n_trades"), 0) or 0)
    weekly = _safe_float(m.get("weekly_opens"))
    if weekly is None:
        weekly = _safe_float(m.get("weekly_open_freq"))
    span = _safe_float(m.get("span_days") or m.get("observation_days"))
    exp = resolve_expectancy_inputs(m)
    e = _safe_float(exp.get("E"))
    if e is None:
        e = _safe_float(m.get("expectancy_E"))

    if e is None:
        reasons.append("expectancy_E_unavailable")
    elif e <= HANDOFF_EXPECTANCY_STRICT_GT + 1e-15:
        reasons.append("expectancy_E_not_gt_0(E=%s)" % round(float(e), 6))
    if weekly is None:
        reasons.append("weekly_open_freq_unavailable")
    elif m.get("weekly_opens_trusted") is False:
        reasons.append("weekly_open_freq_untrusted_short_span")
    elif weekly <= HANDOFF_MIN_WEEKLY_OPENS + 1e-15:
        reasons.append("weekly_open_freq_not_gt_0.5(weekly=%s)" % weekly)
    if span is not None and span < HANDOFF_MIN_SPAN_DAYS - 1e-9:
        reasons.append("span_days_below_%d" % int(HANDOFF_MIN_SPAN_DAYS))

    seen = set()
    uniq = []
    for rsn in reasons:
        if rsn not in seen:
            seen.add(rsn)
            uniq.append(rsn)
    reasons = uniq
    return {
        "ok": not reasons,
        "reasons": reasons,
        "thresholds": {
            "weekly_opens_strict_gt": HANDOFF_MIN_WEEKLY_OPENS,
            "expectancy_E_strict_gt": HANDOFF_EXPECTANCY_STRICT_GT,
            "expectancy_formula": "E = W*R - (1-W)",
            "R_formula": "mean_win / |mean_loss|",
            "W_basis": "account_win_rate_only",
            "loss_fallback": "forbidden",
            "qi_only": True,
            "mmvq": False,
            "leverage_range": [LEVERAGE_MIN, LEVERAGE_MAX],
        },
        "observed": {
            "n": n,
            "span_days": span,
            "weekly_opens": weekly,
            "W": exp.get("W"),
            "R": exp.get("R"),
            "E": e,
            "mean_win_pct": exp.get("mean_win_pct"),
            "mean_loss_pct": exp.get("mean_loss_pct"),
            "loss_basis": exp.get("loss_basis"),
            "recent_2y_requirement_passed": m.get("recent_2y_requirement_passed"),
        },
        "expectancy": exp,
    }


def _near_duplicate(a_metrics, b_metrics):
    a = a_metrics or {}
    b = b_metrics or {}
    for key, tol in HANDOFF_DEDUPE_METRIC_TOL.items():
        av = _safe_float(a.get(key))
        bv = _safe_float(b.get(key))
        if av is None or bv is None:
            return False
        if abs(av - bv) > float(tol) + 1e-15:
            return False
    return True


def levered_win_only_mean_pct(returns=None, trades=None):
    """Canonical win-only mean in percentage points (levered equity after fees).

    Source of truth for trades: ``pnl_ratio`` / ``pnl_ratio_full_size`` from
    backtest_engine_v2 / DSL backtest — already × leverage and after costs.
    Never multiply by leverage again. Never treat a ratio as percentage points.

    Unit contract:
      pnl_ratio = 0.1111  →  11.11 percentage points
      mean_net_win_only_pct MUST be on the percentage-point scale (≥1 for
      typical levered winners; if a caller handed ratio-scale by mistake,
      values stay < 0.5 and we *100 once).
    """
    vals = []
    if returns is not None:
        for x in returns or []:
            v = _safe_float(x)
            if v is not None:
                vals.append(v)
    elif trades is not None:
        for t in trades or []:
            if not isinstance(t, dict):
                continue
            v = _safe_float(t.get("pnl_ratio_full_size"))
            if v is None:
                v = _safe_float(t.get("pnl_ratio"))
            if v is not None:
                vals.append(v)
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v <= 0]
    mean_loss_ratio = None
    mean_loss_pct = None
    if losses:
        mean_loss_ratio = sum(losses) / float(len(losses))
        if abs(mean_loss_ratio) >= 0.5:
            mean_loss_pct = mean_loss_ratio
            mean_loss_ratio = mean_loss_ratio / 100.0
        else:
            mean_loss_pct = mean_loss_ratio * 100.0
    if not wins:
        return {
            "n_trades": len(vals),
            "n_wins": 0,
            "n_losses": len(losses),
            "mean_win_only_ratio": None,
            "mean_win_only_pct": None,
            "mean_loss_only_ratio": mean_loss_ratio,
            "mean_loss_only_pct": mean_loss_pct,
            "unit": "percentage_points_levered_after_fees",
            "leverage_applied_in_pnl_ratio": True,
            "do_not_multiply_leverage_again": True,
        }
    mean_ratio = sum(wins) / float(len(wins))
    # Guard: if somehow handed percentage points already (e.g. 11.11), keep.
    if abs(mean_ratio) >= 0.5:
        mean_pct = mean_ratio
        mean_ratio = mean_ratio / 100.0
    else:
        mean_pct = mean_ratio * 100.0
    return {
        "n_trades": len(vals),
        "n_wins": len(wins),
        "n_losses": len(losses),
        "mean_win_only_ratio": mean_ratio,
        "mean_win_only_pct": mean_pct,
        "mean_loss_only_ratio": mean_loss_ratio,
        "mean_loss_only_pct": mean_loss_pct,
        "unit": "percentage_points_levered_after_fees",
        "leverage_applied_in_pnl_ratio": True,
        "do_not_multiply_leverage_again": True,
        "example_zh": "pnl_ratio=0.1111 → 11.11个百分点（已含20x，禁止再×杠杆）",
    }


def package_metrics(returns, span_days=None, trades=None, symbol=None, timeframe=None,
                    path_summary=None):
    """Account metrics for ranking / review evidence."""
    vals = [_safe_float(x) for x in (returns or [])]
    vals = [v for v in vals if v is not None]
    if not vals and trades:
        for t in trades or []:
            if not isinstance(t, dict):
                continue
            v = _safe_float(t.get("pnl_ratio_full_size"))
            if v is None:
                v = _safe_float(t.get("pnl_ratio"))
            if v is not None:
                vals.append(v)
    n = len(vals)
    win_pack = levered_win_only_mean_pct(returns=vals)
    path_summary = dict(path_summary or {})
    if n <= 0:
        return {
            "n": 0,
            "win_rate": None,
            "win_rate_pct": None,
            "mean_trade_return": None,
            "mean_trade_pct": None,
            "mean_win_only_pct": None,
            "weekly_opens": None,
            "span_days": span_days,
            "win_only_audit": win_pack,
            "profit_first_rate": path_summary.get("profit_first_rate"),
            "median_mae_pct": path_summary.get("median_mae_pct"),
            "median_mfe_pct": path_summary.get("median_mfe_pct"),
            "mean_winning_levered": path_summary.get("mean_winning_levered"),
            "win_rate_basis": None,
            "mean_win_basis": None,
        }
    wins = [v for v in vals if v > 0]
    wr = len(wins) / float(n)
    mean_net = sum(vals) / float(n)
    weekly = None
    weekly_trusted = False
    try:
        import auto_trade_ai_consensus as ai
        pack = ai.compute_weekly_open_frequency(
            n,
            span_days=span_days,
            candidate={"symbol": symbol, "timeframe": timeframe},
            evidence={
                "span_days": span_days,
                "observation_days": span_days,
                "trades": n,
                "bars_span_days": span_days,
            },
            source="manufacture_batch_policy",
        )
        if pack.get("span_days") is not None:
            span_days = pack.get("span_days")
        span_f = _safe_float(span_days)
        if span_f is not None and span_f >= HANDOFF_MIN_SPAN_DAYS - 1e-9:
            weekly = _safe_float(pack.get("expected_weekly_fills"))
            weekly_trusted = True
        else:
            # Keep diagnostic density but mark untrusted for handoff.
            weekly = _safe_float(pack.get("expected_weekly_fills"))
            weekly_trusted = False
    except Exception:
        span_f = _safe_float(span_days)
        if span_f and span_f > 0:
            weekly = n * 7.0 / float(span_f)
            weekly_trusted = bool(span_f >= HANDOFF_MIN_SPAN_DAYS - 1e-9)
    out = {
        "n": n,
        "win_rate": wr,
        "win_rate_pct": wr * 100.0,
        "mean_trade_return": mean_net,
        "mean_trade_pct": mean_net * 100.0,
        "mean_win_only_pct": win_pack.get("mean_win_only_pct"),
        "weekly_opens": weekly,
        "weekly_opens_trusted": weekly_trusted,
        "span_days": span_days,
        "n_trades": n,
        "win_only_audit": win_pack,
        "profit_first_rate": path_summary.get("profit_first_rate"),
        "median_mae_pct": path_summary.get("median_mae_pct"),
        "median_mfe_pct": path_summary.get("median_mfe_pct"),
        "mean_winning_levered": path_summary.get("mean_winning_levered"),
        "path_entry_score": path_summary.get("path_entry_score"),
        # Account ledger is the only handoff identity for W / mean_win / mean_loss.
        "win_rate_basis": "account_pnl_ratio",
        "mean_win_basis": "account_pnl_ratio_win_only",
        "account_win_rate": wr,
        "account_win_rate_pct": wr * 100.0,
        "account_mean_win_only_pct": win_pack.get("mean_win_only_pct"),
        "mean_loss_only_pct": win_pack.get("mean_loss_only_pct"),
        "account_mean_loss_only_pct": win_pack.get("mean_loss_only_pct"),
    }
    # Path / barrier diagnostics — never overwrite account win_rate used for E.
    pfr = _safe_float(path_summary.get("profit_first_rate"))
    exit_pol = path_summary.get("exit_policy") or {}
    barrier = (
        str((exit_pol or {}).get("mode") or "") == "intrabar_fixed_pct_target_v1"
        or path_summary.get("path_identity") == "rhyme_barrier_resolved"
        or path_summary.get("win_rate_basis") == "path_profit_first_resolved"
    )
    probe_wr = _safe_float(path_summary.get("win_rate"))
    if barrier and probe_wr is not None:
        out["path_win_rate"] = probe_wr
        out["path_win_rate_pct"] = probe_wr * 100.0
        out["path_win_rate_basis"] = "path_profit_first_resolved"
        if pfr is None:
            out["profit_first_rate"] = probe_wr
    elif barrier and pfr is not None:
        out["path_win_rate"] = pfr
        out["path_win_rate_pct"] = pfr * 100.0
        out["path_win_rate_basis"] = "path_profit_first_resolved"
    mw_lev = _safe_float(path_summary.get("mean_winning_levered"))
    if barrier and mw_lev is not None and mw_lev > 0:
        out["path_mean_win_only_pct"] = mw_lev * 100.0 if mw_lev < 1.5 else mw_lev
    exp = resolve_expectancy_inputs(out)
    out["expectancy_E"] = exp.get("E")
    out["expectancy_W"] = exp.get("W")
    out["expectancy_R"] = exp.get("R")
    out["expectancy"] = exp
    if path_summary.get("execution_leverage") is not None:
        out["execution_leverage"] = path_summary.get("execution_leverage")
    stop_pol = path_summary.get("protective_stop_policy") or {}
    if isinstance(stop_pol, dict) and stop_pol.get("price_pct") is not None:
        out["protective_stop_price_pct"] = stop_pol.get("price_pct")
    # Optional recent-2y gate when timestamped trades are available.
    if trades:
        try:
            from . import quality_optimization as qopt
            gate = qopt.recent_2y_gate_from_returns(trades)
            out["recent_2y_requirement_passed"] = gate.get("recent_2y_requirement_passed")
            out["recent_2y_gate"] = {
                "ok": gate.get("ok"),
                "average_profitable_trade_return": gate.get(
                    "average_profitable_trade_return"
                ),
                "weighted_average_profitable_trade_return": gate.get(
                    "weighted_average_profitable_trade_return"
                ),
                "weighted_metrics": gate.get("weighted_metrics"),
                "decision": gate.get("decision"),
            }
            out["weighted_average_profitable_trade_return"] = gate.get(
                "weighted_average_profitable_trade_return"
            )
        except Exception as exc:
            out["recent_2y_gate_error"] = str(exc)[:160]
    elif path_summary.get("recent_2y_requirement_passed") is not None:
        out["recent_2y_requirement_passed"] = path_summary.get(
            "recent_2y_requirement_passed"
        )
    # P4 quality enrichment: concentration / optional stress / plateau surface.
    try:
        from . import quality_optimization as qopt
        out = qopt.enrich_quality_metrics(
            out,
            returns=returns,
            param_surface=path_summary.get("param_surface"),
            cost_stress=path_summary.get("cost_stress"),
            delay_stress=path_summary.get("delay_stress"),
            regime_slices=path_summary.get("regime_slices"),
        )
    except Exception as exc:
        out["quality_enrich_error"] = str(exc)[:160]
    return out


def score_package(metrics):
    """Higher is better. Soft score for manufacture ranking (not a hard gate)."""
    m = metrics or {}
    wr = _safe_float(m.get("account_win_rate"))
    if wr is None:
        wr = _safe_float(m.get("win_rate"), 0.0) or 0.0
    weekly = _safe_float(m.get("weekly_opens"), 0.0) or 0.0
    mean_win = _safe_float(m.get("account_mean_win_only_pct"))
    if mean_win is None:
        mean_win = _safe_float(m.get("mean_win_only_pct"), -1.0)
    if mean_win is None:
        mean_win = (_safe_float(m.get("mean_trade_pct"), -1.0) or -1.0)
    e = _safe_float(m.get("expectancy_E"))
    if e is None:
        e = _safe_float((m.get("expectancy") or {}).get("E"), -1.0) or -1.0
    wr_score = max(0.0, min(2.0, wr / max(HANDOFF_MIN_WIN_RATE, 1e-9)))
    weekly_score = max(0.0, min(2.0, weekly / REVIEW_WEEKLY_OPENS))
    mean_score = max(
        0.0,
        min(2.0, (mean_win / REVIEW_MEAN_WIN_ONLY_PCT) if REVIEW_MEAN_WIN_ONLY_PCT else 0.0),
    )
    e_score = max(0.0, min(2.0, e / max(HANDOFF_EXPECTANCY_STRICT_GT, 1e-9)))
    hit = (
        int(weekly >= REVIEW_WEEKLY_OPENS)
        + int(mean_win >= REVIEW_MEAN_WIN_ONLY_PCT)
        + int(wr > HANDOFF_MIN_WIN_RATE)
        + int(e > HANDOFF_EXPECTANCY_STRICT_GT)
    )
    return {
        "score": round(
            0.35 * e_score + 0.25 * mean_score + 0.20 * weekly_score
            + 0.20 * wr_score,
            6,
        ),
        "hits": hit,
        "wr_ok": wr > HANDOFF_MIN_WIN_RATE,
        "weekly_ok": weekly >= REVIEW_WEEKLY_OPENS,
        "mean_ok": mean_win >= REVIEW_MEAN_WIN_ONLY_PCT,
        "e_ok": e > HANDOFF_EXPECTANCY_STRICT_GT,
        "axes": {
            "account_win_rate": wr,
            "weekly_opens": weekly,
            "mean_win_only_pct": mean_win,
            "expectancy_E": e,
        },
    }


def rank_packages(packages):
    ranked = []
    for row in packages or []:
        item = dict(row)
        metrics = item.get("select_metrics") or package_metrics(
            item.get("returns") or item.get("review_returns") or [],
            span_days=item.get("span_days"),
            trades=item.get("trades"),
            symbol=item.get("symbol"),
            timeframe=item.get("timeframe"),
        )
        item["select_metrics"] = metrics
        item["select_rank"] = score_package(metrics)
        ranked.append(item)
    ranked.sort(
        key=lambda r: (
            int((r.get("select_rank") or {}).get("hits") or 0),
            float((r.get("select_rank") or {}).get("score") or 0.0),
            float(((r.get("select_metrics") or {}).get("mean_win_only_pct") or -1e9)),
        ),
        reverse=True,
    )
    for index, row in enumerate(ranked):
        row["rank"] = index + 1
    return ranked


def select_top_for_review(packages, top_n=None):
    """Rank all packages, then keep only handoff-qualified unique Top-N.

    Returns (qualified_top, ranked_all). Empty qualified_top means manufacture
    produced survivors but none clear the anti-shit handoff floor — caller must
    reject honestly instead of submitting 胜率~31% / 盈利单~6% clones.
    """
    top_n = int(top_n if top_n is not None else TOP_N_TO_REVIEW)
    ranked = rank_packages(packages)
    for row in ranked:
        row["handoff_gate"] = qualifies_for_review_handoff(row.get("select_metrics") or {})
    qualified = []
    for row in ranked:
        gate = row.get("handoff_gate") or {}
        if not gate.get("ok"):
            continue
        # Drop near-identical clones (same account fingerprint).
        if any(_near_duplicate(row.get("select_metrics"), prev.get("select_metrics"))
               for prev in qualified):
            row["handoff_gate"] = {
                "ok": False,
                "reasons": ["handoff_near_duplicate_of_higher_rank"],
                "thresholds": gate.get("thresholds"),
                "observed": gate.get("observed"),
            }
            continue
        qualified.append(row)
        if len(qualified) >= top_n:
            break
    # Attach irreversible handoff tokens — formal review must present these.
    try:
        from . import review_gate as rgate
        for row in qualified:
            token = rgate.issue_handoff_token(
                candidate_id=row.get("recipe_id") or row.get("hypothesis_id"),
                recipe_id=row.get("recipe_id"),
                metrics=row.get("select_metrics") or {},
                job_id=row.get("job_id"),
            )
            row["handoff_token"] = token if token.get("ok") else None
            if not token.get("ok"):
                row["handoff_gate"] = {
                    "ok": False,
                    "reasons": list((row.get("handoff_gate") or {}).get("reasons") or [])
                    + ["handoff_token_issue_failed"],
                }
        qualified = [r for r in qualified if (r.get("handoff_token") or {}).get("ok")]
    except Exception as exc:
        for row in qualified:
            row["handoff_token_error"] = str(exc)[:200]
            row["handoff_token"] = None
        qualified = []
    # P4 anti-bypass: never pad Top-N with unqualified packages.
    assert all((r.get("handoff_gate") or {}).get("ok") for r in qualified), (
        "handoff_bypass: unqualified package in select_top_for_review"
    )
    assert all((r.get("handoff_token") or {}).get("ok") for r in qualified), (
        "handoff_bypass: missing handoff token in select_top_for_review"
    )
    assert len(qualified) <= top_n
    return qualified[:top_n], ranked


def handoff_bypass_audit(qualified_top, ranked_all=None):
    """Evidence helper: prove Top-N was filtered before review, not padded."""
    qualified_top = list(qualified_top or [])
    ranked_all = list(ranked_all or [])
    reasons = []
    for row in qualified_top:
        if not (row.get("handoff_gate") or {}).get("ok"):
            reasons.append("qualified_row_gate_not_ok")
        if not (row.get("handoff_token") or {}).get("ok"):
            reasons.append("qualified_row_missing_token")
    # If ranked had any gate-fail above a qualified row, that is fine; padding
    # would mean len(qualified)==top_n while including fails — already blocked.
    n_gate_fail = sum(
        1 for r in ranked_all
        if not (r.get("handoff_gate") or {}).get("ok")
    )
    return {
        "ok": not reasons,
        "reasons": reasons,
        "top3_actual_count": len(qualified_top),
        "ranked_n": len(ranked_all),
        "handoff_fail_count": n_gate_fail,
        "padded_with_junk": False,
        "policy": "filter_then_topn_never_pad",
    }


def multiai_average_pass(wr_avg_pct, weekly_avg, mean_net_avg_pct, metrics=None):
    """四AI平均质检：仅 E>0 且周开仓频率>0.5。"""
    weekly = _safe_float(weekly_avg)
    mean_pct = _safe_float(mean_net_avg_pct)
    if mean_pct is not None and 0 < abs(mean_pct) < 0.5:
        mean_pct = mean_pct * 100.0
    m = dict(metrics or {})
    exp = resolve_expectancy_inputs(
        m, wr_avg_pct=wr_avg_pct, mean_win_avg_pct=mean_pct,
    )
    reasons = []
    span = _safe_float(m.get("span_days") or m.get("observation_days"))
    e = _safe_float(exp.get("E"))
    if e is None:
        e = _safe_float(m.get("expectancy_E"))
    if weekly is None:
        reasons.append("weekly_open_freq_unavailable")
    elif weekly <= REVIEW_WEEKLY_OPENS + 1e-15:
        reasons.append("weekly_open_freq_not_gt_0.5(weekly=%s)" % weekly)
    if e is None:
        reasons.append("expectancy_E_unavailable")
    elif e <= HANDOFF_EXPECTANCY_STRICT_GT + 1e-15:
        reasons.append("expectancy_E_not_gt_0(E=%s)" % round(float(e), 6))
    return {
        "passed": not reasons,
        "reasons": reasons,
        "thresholds": {
            "weekly_opens_strict_gt": REVIEW_WEEKLY_OPENS,
            "expectancy_E_strict_gt": HANDOFF_EXPECTANCY_STRICT_GT,
            "expectancy_formula": "E = W*R - (1-W)",
            "loss_fallback": "forbidden",
            "qi_only": True,
        },
        "observed": {
            "win_rate_pct": _safe_float(wr_avg_pct),
            "weekly_opens": weekly,
            "mean_win_only_pct": mean_pct,
            "span_days": span,
            "W": exp.get("W"),
            "R": exp.get("R"),
            "E": e,
            "mean_loss_pct": exp.get("mean_loss_pct"),
            "loss_basis": exp.get("loss_basis"),
        },
        "expectancy": exp,
        "mean_net_scope": "winning_trades_only_levered_after_fees",
        "unit": "percentage_points",
    }


def probe():
    # Sanity: 0.1111 ratio → 11.11 pct; must pass threshold equality.
    audit = levered_win_only_mean_pct(returns=[0.1111, -0.05, 0.20])
    return {
        "ok": True,
        "pre_review_hard_gates_disabled": PRE_REVIEW_HARD_GATES_DISABLED,
        "qi_only": True,
        "mmvq": False,
        "min_manufacture": MIN_MANUFACTURE,
        "top_n_to_review": TOP_N_TO_REVIEW,
        "slim_multiai_review_only": True,
        "protective_stop_price_pct": PROTECTIVE_STOP_PRICE_PCT,
        "review_thresholds": {
            "weekly_opens_strict_gt": REVIEW_WEEKLY_OPENS,
            "expectancy_E_strict_gt": HANDOFF_EXPECTANCY_STRICT_GT,
        },
        "handoff_floors": {
            "weekly_opens_strict_gt": HANDOFF_MIN_WEEKLY_OPENS,
            "expectancy_E_strict_gt": HANDOFF_EXPECTANCY_STRICT_GT,
            "formula": "E=W*R-(1-W)",
            "qi_only": True,
            "loss_fallback": "forbidden",
        },
        "win_only_unit_audit": audit,
    }
