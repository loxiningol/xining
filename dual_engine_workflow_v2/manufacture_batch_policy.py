# -*- coding: utf-8 -*-
"""Manufacture-batch policy: quantity first, then select Top-N for slim multi-AI review.

User lock (2026-08-03 evening):
- Cancel ALL pre-review hard gates.
- Manufacture ≥ MIN_MANUFACTURE packages; hand Top-N to review.
- Formal review sole gate = multi-AI AVERAGE of:
    weekly_opens >= 0.5
    mean_win_only_pct (levered, fees included, winning trades ONLY) >= 11.11
- Providers: deepseek + qwen + glm + kimi
- Protective stop: 0.5% price (unlevered)
"""
from __future__ import print_function

import math

# ---- Batch manufacture ----
MIN_MANUFACTURE = 10
TOP_N_TO_REVIEW = 3
ASSEMBLY_PAYLOAD_CAP = 24

# ---- Sole review thresholds (multi-AI average) ----
# Win-rate is advisory only for ranking; NOT a sole-review hard average gate.
REVIEW_WR = 0.60
REVIEW_WR_PCT = 60.0
REVIEW_WEEKLY_OPENS = 0.5
# Levered equity return on winning trades only, percentage points.
# Example: pnl_ratio=+0.1111 → 11.11 percentage points after 20x + fees.
REVIEW_MEAN_WIN_ONLY_PCT = 11.11
REVIEW_MEAN_TRADE_RETURN = REVIEW_MEAN_WIN_ONLY_PCT / 100.0
REVIEW_MEAN_TRADE_PCT = REVIEW_MEAN_WIN_ONLY_PCT  # alias for older callers

# Price stop (unlevered). 0.5% of price, NOT 0.5% of equity.
PROTECTIVE_STOP_PRICE_PCT = 0.005
EXECUTION_LEVERAGE = 20

PRE_REVIEW_HARD_GATES_DISABLED = True

# Handoff floors: manufacture may collect junk survivors, but Top-N must NOT be
# submitted to slim multi-AI if account evidence already proves they are shit.
# These are NOT the old Gate0–7 vetoes; they only block wasting review on clones
# that already fail the sole review axes on the backtest ledger.
HANDOFF_MIN_WIN_ONLY_PCT = 9.0          # account win-only (levered); review needs 11.11
HANDOFF_MIN_WIN_RATE = 0.45             # 胜率 floor for handoff (advisory in review)
HANDOFF_MIN_WEEKLY_OPENS = REVIEW_WEEKLY_OPENS
HANDOFF_MIN_TRADES = 12
HANDOFF_MIN_PROFIT_FIRST_RATE = 0.55
HANDOFF_MAX_MEDIAN_MAE = 0.0035
HANDOFF_DEDUPE_METRIC_TOL = {
    "win_rate_pct": 0.05,
    "mean_win_only_pct": 0.05,
    "weekly_opens": 0.05,
    "profit_first_rate": 0.02,
}


def pre_review_gates_disabled():
    return bool(PRE_REVIEW_HARD_GATES_DISABLED)


def slim_multiai_review_only():
    """Formal path: skip L0–L7 / Gate0–6 / Phase5; only multi-AI average."""
    return True


def qualifies_for_review_handoff(metrics):
    """Refuse to hand lottery/shit packages to slim multi-AI review."""
    m = metrics or {}
    reasons = []
    n = int(_safe_float(m.get("n") or m.get("n_trades"), 0) or 0)
    wr = _safe_float(m.get("win_rate"))
    wr_pct = _safe_float(m.get("win_rate_pct"))
    if wr is None and wr_pct is not None:
        wr = wr_pct / 100.0
    weekly = _safe_float(m.get("weekly_opens"))
    mean_win = _safe_float(m.get("mean_win_only_pct"))
    if mean_win is not None and 0 < abs(mean_win) < 0.5:
        mean_win = mean_win * 100.0
    pfr = _safe_float(m.get("profit_first_rate"))
    med_mae = _safe_float(m.get("median_mae_pct"))
    if n < HANDOFF_MIN_TRADES:
        reasons.append("handoff_n_trades_below_%d" % HANDOFF_MIN_TRADES)
    if wr is None or wr < HANDOFF_MIN_WIN_RATE - 1e-12:
        reasons.append("handoff_win_rate_below_%.0f_pct" % (HANDOFF_MIN_WIN_RATE * 100.0))
    if weekly is None or weekly < HANDOFF_MIN_WEEKLY_OPENS - 1e-9:
        reasons.append("handoff_weekly_below_%.1f" % HANDOFF_MIN_WEEKLY_OPENS)
    if mean_win is None or mean_win < HANDOFF_MIN_WIN_ONLY_PCT - 1e-9:
        reasons.append("handoff_mean_win_only_below_%.2f_pct" % HANDOFF_MIN_WIN_ONLY_PCT)
    if pfr is None or pfr < HANDOFF_MIN_PROFIT_FIRST_RATE - 1e-12:
        reasons.append("handoff_profit_first_rate_below_%.2f" % HANDOFF_MIN_PROFIT_FIRST_RATE)
    if med_mae is not None and abs(med_mae) > HANDOFF_MAX_MEDIAN_MAE + 1e-12:
        reasons.append("handoff_median_mae_above_%.4f" % HANDOFF_MAX_MEDIAN_MAE)
    # Explicit recent-2y fail is a hard handoff veto (missing = not scored yet).
    if m.get("recent_2y_requirement_passed") is False:
        reasons.append("handoff_recent_2y_failed")
    # P4 execution / stability vetoes when scored (never bypass floor by omission).
    if m.get("cost_stress_passed") is False:
        reasons.append("handoff_cost_stress_failed")
    if m.get("delay_stress_passed") is False:
        reasons.append("handoff_delay_stress_failed")
    if m.get("pnl_concentrated") is True:
        reasons.append("handoff_pnl_concentration")
    top_share = _safe_float(m.get("top_trade_pnl_share"))
    if top_share is not None and top_share > 0.40 + 1e-12:
        reasons.append("handoff_top_trade_pnl_share_above_0.40")
    if m.get("parameter_spike") is True:
        reasons.append("handoff_parameter_spike")
    plateau = _safe_float(m.get("parameter_plateau_score"))
    if plateau is not None and plateau < 0.55 - 1e-12:
        reasons.append("handoff_parameter_plateau_below_0.55")
    if m.get("regime_stable") is False:
        reasons.append("handoff_regime_unstable")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "thresholds": {
            "n_trades": HANDOFF_MIN_TRADES,
            "win_rate": HANDOFF_MIN_WIN_RATE,
            "weekly_opens": HANDOFF_MIN_WEEKLY_OPENS,
            "mean_win_only_pct": HANDOFF_MIN_WIN_ONLY_PCT,
            "profit_first_rate": HANDOFF_MIN_PROFIT_FIRST_RATE,
            "median_mae_max": HANDOFF_MAX_MEDIAN_MAE,
            "recent_2y_required_when_present": True,
            "top_trade_pnl_share_max": 0.40,
            "parameter_plateau_min": 0.55,
            "cost_delay_required_when_present": True,
        },
        "observed": {
            "n": n,
            "win_rate": wr,
            "weekly_opens": weekly,
            "mean_win_only_pct": mean_win,
            "profit_first_rate": pfr,
            "median_mae_pct": med_mae,
            "recent_2y_requirement_passed": m.get("recent_2y_requirement_passed"),
            "cost_stress_passed": m.get("cost_stress_passed"),
            "delay_stress_passed": m.get("delay_stress_passed"),
            "top_trade_pnl_share": top_share,
            "parameter_plateau_score": plateau,
            "regime_stable": m.get("regime_stable"),
        },
    }


def _near_duplicate(a_metrics, b_metrics):
    a = a_metrics or {}
    b = b_metrics or {}
    for key, tol in HANDOFF_DEDUPE_METRIC_TOL.items():
        av = _safe_float(a.get(key))
        bv = _safe_float(b.get(key))
        if av is None or bv is None:
            return False
        if abs(av - bv) > tol:
            return False
    return True


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
    if not wins:
        return {
            "n_trades": len(vals),
            "n_wins": 0,
            "mean_win_only_ratio": None,
            "mean_win_only_pct": None,
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
        "mean_win_only_ratio": mean_ratio,
        "mean_win_only_pct": mean_pct,
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
        }
    wins = [v for v in vals if v > 0]
    wr = len(wins) / float(n)
    mean_net = sum(vals) / float(n)
    weekly = None
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
        weekly = _safe_float(pack.get("expected_weekly_fills"))
        if pack.get("span_days") is not None:
            span_days = pack.get("span_days")
    except Exception:
        if span_days and float(span_days) > 0:
            weekly = n * 7.0 / float(span_days)
    out = {
        "n": n,
        "win_rate": wr,
        "win_rate_pct": wr * 100.0,
        "mean_trade_return": mean_net,
        "mean_trade_pct": mean_net * 100.0,
        "mean_win_only_pct": win_pack.get("mean_win_only_pct"),
        "weekly_opens": weekly,
        "span_days": span_days,
        "n_trades": n,
        "win_only_audit": win_pack,
        "profit_first_rate": path_summary.get("profit_first_rate"),
        "median_mae_pct": path_summary.get("median_mae_pct"),
        "median_mfe_pct": path_summary.get("median_mfe_pct"),
        "mean_winning_levered": path_summary.get("mean_winning_levered"),
        "path_entry_score": path_summary.get("path_entry_score"),
    }
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
    wr = _safe_float(m.get("win_rate"), 0.0) or 0.0
    weekly = _safe_float(m.get("weekly_opens"), 0.0) or 0.0
    mean_win = _safe_float(m.get("mean_win_only_pct"), -1.0)
    if mean_win is None:
        mean_win = (_safe_float(m.get("mean_trade_pct"), -1.0) or -1.0)
    pfr = _safe_float(m.get("profit_first_rate"), 0.0) or 0.0
    wr_score = max(0.0, min(2.0, wr / REVIEW_WR))
    weekly_score = max(0.0, min(2.0, weekly / REVIEW_WEEKLY_OPENS))
    mean_score = max(
        0.0,
        min(2.0, (mean_win / REVIEW_MEAN_WIN_ONLY_PCT) if REVIEW_MEAN_WIN_ONLY_PCT else 0.0),
    )
    path_score = max(0.0, min(2.0, pfr / HANDOFF_MIN_PROFIT_FIRST_RATE))
    hit = (
        int(weekly >= REVIEW_WEEKLY_OPENS)
        + int(mean_win >= REVIEW_MEAN_WIN_ONLY_PCT)
        + int(pfr >= HANDOFF_MIN_PROFIT_FIRST_RATE)
    )
    return {
        "score": round(
            0.30 * path_score + 0.30 * mean_score + 0.20 * weekly_score
            + 0.10 * wr_score + 0.10 * hit,
            6,
        ),
        "hits": hit,
        "wr_ok": wr >= REVIEW_WR,
        "weekly_ok": weekly >= REVIEW_WEEKLY_OPENS,
        "mean_ok": mean_win >= REVIEW_MEAN_WIN_ONLY_PCT,
        "path_ok": pfr >= HANDOFF_MIN_PROFIT_FIRST_RATE,
        "axes": {
            "win_rate": wr,
            "weekly_opens": weekly,
            "mean_win_only_pct": mean_win,
            "profit_first_rate": pfr,
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


def multiai_average_pass(wr_avg_pct, weekly_avg, mean_net_avg_pct):
    """Sole formal review gate: AI averages of win-only mean + weekly.

    ``wr_avg_pct`` retained for API compatibility / report; not a hard fail.
    ``mean_net_avg_pct`` MUST be win-only levered percentage points.
    """
    weekly = _safe_float(weekly_avg)
    mean_pct = _safe_float(mean_net_avg_pct)
    # Auto-correct ratio-scale mistakes (0.1111 → 11.11) once.
    if mean_pct is not None and 0 < abs(mean_pct) < 0.5:
        mean_pct = mean_pct * 100.0
    reasons = []
    if weekly is None or weekly < REVIEW_WEEKLY_OPENS - 1e-9:
        reasons.append("weekly_avg_below_%.1f" % REVIEW_WEEKLY_OPENS)
    if mean_pct is None or mean_pct < REVIEW_MEAN_WIN_ONLY_PCT - 1e-9:
        reasons.append("mean_win_only_avg_below_%.2f_pct" % REVIEW_MEAN_WIN_ONLY_PCT)
    return {
        "passed": not reasons,
        "reasons": reasons,
        "thresholds": {
            "weekly_opens": REVIEW_WEEKLY_OPENS,
            "mean_win_only_pct": REVIEW_MEAN_WIN_ONLY_PCT,
            "win_rate_pct_advisory": REVIEW_WR_PCT,
        },
        "observed": {
            "win_rate_pct": _safe_float(wr_avg_pct),
            "weekly_opens": weekly,
            "mean_win_only_pct": mean_pct,
        },
        "mean_net_scope": "winning_trades_only_levered_after_fees",
        "unit": "percentage_points",
    }


def probe():
    # Sanity: 0.1111 ratio → 11.11 pct; must pass threshold equality.
    audit = levered_win_only_mean_pct(returns=[0.1111, -0.05, 0.20])
    return {
        "ok": True,
        "pre_review_hard_gates_disabled": PRE_REVIEW_HARD_GATES_DISABLED,
        "min_manufacture": MIN_MANUFACTURE,
        "top_n_to_review": TOP_N_TO_REVIEW,
        "slim_multiai_review_only": True,
        "protective_stop_price_pct": PROTECTIVE_STOP_PRICE_PCT,
        "review_thresholds": {
            "weekly_opens": REVIEW_WEEKLY_OPENS,
            "mean_win_only_pct": REVIEW_MEAN_WIN_ONLY_PCT,
        },
        "handoff_floors": {
            "win_rate": HANDOFF_MIN_WIN_RATE,
            "weekly_opens": HANDOFF_MIN_WEEKLY_OPENS,
            "mean_win_only_pct": HANDOFF_MIN_WIN_ONLY_PCT,
            "n_trades": HANDOFF_MIN_TRADES,
            "profit_first_rate": HANDOFF_MIN_PROFIT_FIRST_RATE,
            "median_mae_max": HANDOFF_MAX_MEDIAN_MAE,
        },
        "win_only_unit_audit": audit,
    }
