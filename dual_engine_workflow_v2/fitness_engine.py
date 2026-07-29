# -*- coding: utf-8 -*-
"""Multi-objective fitness engine for STEP A Gate2/Gate3 / formal PreGate.

Hard gates (all must pass when enforce=True):
  - Calmar >= 1.5  (annualized return / |max DD|)
  - Payoff ratio (avg_win / avg_loss) >= 2.5
  - Expectancy factor: win_rate * payoff >= 1.0  (user formula; classic mean also reported)
  - Worst 5 losses <= 40% of total absolute loss sum
  - MAE demotion: any trade with MAE > 2.0 * avg_win → reject ("dead-hold to BE")
  - Remove-max-win: drop largest win; if Calmar OR Sharpe drops >50% → reject ("lottery overfitting")

Annualization (documented):
  If trade timestamps available → span_days from first entry to last exit (min 1 day).
  Else → assume ~1 trade/day proxy: span_days = max(1, n_trades).
  annualized_return = (1 + total_compound_return) ** (365.25 / span_days) - 1
  Calmar = annualized_return / abs(max_drawdown)   (max_dd as positive fraction of equity)
  Sharpe = mean(pnl) / std(pnl) * sqrt(min(n, 252))  (trade-pnl proxy; same as dual-engine)

Protective 0.9% SL / 20x / 30% size are OUT OF SCOPE here — fitness only.
"""
from __future__ import print_function

import math
from datetime import datetime


# ---- Hard thresholds (Phase 2) ----
CALMAR_MIN = 1.5
PAYOFF_MIN = 2.5
EXPECTANCY_FACTOR_MIN = 1.0  # WR * payoff
WORST5_LOSS_SHARE_MAX = 0.40
MAE_VS_AVG_WIN_MAX = 2.0
REMOVE_MAX_WIN_DROP_MAX = 0.50  # 50% relative drop


def _safe_float(x, default=None):
    try:
        v = float(x)
        if math.isfinite(v):
            return v
    except Exception:
        pass
    return default


def _parse_ts(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text[:19] if "T" in text or " " in text else text[:10],
                                     fmt if "%z" not in fmt else "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _pnls(trades):
    out = []
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        p = _safe_float(t.get("pnl_ratio"), None)
        if p is None:
            p = _safe_float(t.get("pnl"), None)
        if p is None:
            continue
        out.append(p)
    return out


def _equity_curve(pnls):
    equity = 1.0
    curve = [1.0]
    for p in pnls:
        equity *= max(0.0, 1.0 + float(p))
        curve.append(equity)
    return curve


def _max_drawdown(curve):
    """Max drawdown as positive fraction of peak equity (0..1+)."""
    peak = curve[0] if curve else 1.0
    max_dd = 0.0
    for x in curve:
        if x > peak:
            peak = x
        if peak > 0:
            dd = (peak - x) / peak
            if dd > max_dd:
                max_dd = dd
    return float(max_dd)


def _span_days(trades, n_pnls):
    starts = []
    ends = []
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        s = _parse_ts(t.get("entry_time") or t.get("entry_ts"))
        e = _parse_ts(t.get("exit_time") or t.get("exit_ts"))
        if s:
            starts.append(s)
        if e:
            ends.append(e)
    if starts and ends:
        span = (max(ends) - min(starts)).total_seconds() / 86400.0
        return max(1.0, float(span))
    return max(1.0, float(n_pnls or 1))


def _sharpe(pnls):
    n = len(pnls)
    if n < 3:
        return 0.0
    mean = sum(pnls) / float(n)
    var = sum((p - mean) ** 2 for p in pnls) / float(n - 1)
    std = math.sqrt(max(0.0, var))
    if std <= 0:
        return 0.0
    return (mean / std) * math.sqrt(min(n, 252))


def _calmar(pnls, trades):
    if not pnls:
        return 0.0, {
            "annualized_return": 0.0,
            "max_drawdown": 0.0,
            "span_days": 0.0,
            "total_compound_return": 0.0,
        }
    curve = _equity_curve(pnls)
    total_ret = curve[-1] - 1.0
    max_dd = _max_drawdown(curve)
    span = _span_days(trades, len(pnls))
    # Compound annualization from sample span
    try:
        ann = (curve[-1] ** (365.25 / span)) - 1.0 if curve[-1] > 0 else -1.0
    except Exception:
        ann = total_ret * (365.25 / span)
    if max_dd <= 1e-12:
        calmar = 999.0 if ann > 0 else 0.0
    else:
        calmar = ann / max_dd
    meta = {
        "annualized_return": round(float(ann), 8),
        "max_drawdown": round(float(max_dd), 8),
        "span_days": round(float(span), 4),
        "total_compound_return": round(float(total_ret), 8),
        "annualization": (
            "Calmar = ((1+R)^(365.25/span_days)-1) / max_DD; "
            "span from trade timestamps else n_trades-as-days proxy"
        ),
    }
    return float(calmar), meta


def payoff_stats(pnls):
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(pnls)
    wr = (len(wins) / float(n)) if n else 0.0
    avg_win = (sum(wins) / float(len(wins))) if wins else 0.0
    avg_loss_mag = (sum(-p for p in losses) / float(len(losses))) if losses else 0.0
    if avg_loss_mag > 0:
        payoff = avg_win / avg_loss_mag
    else:
        payoff = 999.0 if avg_win > 0 else 0.0
    # User formula
    expectancy_factor = wr * payoff
    # Classic expectancy (mean net per trade)
    classic_expectancy = (sum(pnls) / float(n)) if n else 0.0
    return {
        "n": n,
        "win_rate": wr,
        "win_rate_pct": wr * 100.0,
        "avg_win": avg_win,
        "avg_loss": avg_loss_mag,
        "payoff_ratio": min(payoff, 999.0),
        "expectancy_factor_wr_x_payoff": expectancy_factor,
        "classic_expectancy_mean_pnl": classic_expectancy,
        "formula_expectancy_factor": "win_rate * (avg_win/avg_loss) >= 1.0",
        "formula_classic_expectancy": "mean(pnl_ratio)",
    }


def worst5_loss_share(pnls):
    losses = sorted((-p for p in pnls if p < 0), reverse=True)
    total = sum(losses)
    if total <= 0:
        return 0.0, {"worst5_sum": 0.0, "total_loss_sum": 0.0}
    worst5 = sum(losses[:5])
    return worst5 / total, {"worst5_sum": worst5, "total_loss_sum": total}


def mae_dead_hold_flags(trades, avg_win):
    """Flag trades where MAE (price adverse excursion) > 2.0 * avg_win.

    MAE is expected as fraction of entry (price space), comparable to raw
    unlevered move. avg_win is leveraged pnl_ratio by default — we compare
    MAE to avg_win / leverage if leverage present on trade, else treat both
    as same unit when mae_pct is stored as leveraged-equivalent.
    Prefer trade['mae_price_pct'] (unlevered) vs avg_win_price.
    """
    flags = []
    if avg_win <= 0:
        return flags, {"skipped": "avg_win_non_positive"}
    for i, t in enumerate(trades or []):
        if not isinstance(t, dict):
            continue
        mae = _safe_float(t.get("mae_price_pct"), None)
        if mae is None:
            mae = _safe_float(t.get("mae"), None)
        if mae is None:
            continue
        lev = _safe_float(t.get("leverage"), 1.0) or 1.0
        # Convert leveraged avg_win to price-space if mae is price pct
        avg_win_price = abs(avg_win) / lev if lev > 1 else abs(avg_win)
        # If mae looks leveraged (>0.05 typical for price), still use raw compare
        # when strategy tagged mae_is_leveraged
        if t.get("mae_is_leveraged"):
            threshold = MAE_VS_AVG_WIN_MAX * abs(avg_win)
            compare_mae = abs(mae)
        else:
            threshold = MAE_VS_AVG_WIN_MAX * avg_win_price
            compare_mae = abs(mae)
        if compare_mae > threshold + 1e-15:
            flags.append({
                "trade_index": i,
                "mae": compare_mae,
                "threshold": threshold,
                "reason": "dead_hold_to_BE",
            })
    return flags, {"avg_win_ref": avg_win, "n_flagged": len(flags)}


def remove_max_win_stress(pnls, trades):
    """Drop largest winning trade; measure Calmar/Sharpe relative drop.

    When max DD is ~0, Calmar becomes numerically huge and relative drops are
    unstable. In that regime we require the reduced sample to still clear the
    Calmar floor (or treat Calmar-drop as non-binding) and rely on Sharpe drop.
    """
    if not pnls:
        return {
            "pass": False,
            "reason": "no_trades",
            "calmar_drop": None,
            "sharpe_drop": None,
        }
    wins_idx = [i for i, p in enumerate(pnls) if p > 0]
    if not wins_idx:
        return {
            "pass": True,
            "reason": "no_winning_trades",
            "calmar_drop": 0.0,
            "sharpe_drop": 0.0,
        }
    max_i = max(wins_idx, key=lambda i: pnls[i])
    base_calmar, base_meta = _calmar(pnls, trades)
    base_sharpe = _sharpe(pnls)
    reduced_pnls = [p for i, p in enumerate(pnls) if i != max_i]
    reduced_trades = [t for i, t in enumerate(trades or []) if i != max_i]
    if len(reduced_trades) != len(reduced_pnls):
        reduced_trades = [{"pnl_ratio": p} for p in reduced_pnls]
    red_calmar, red_meta = _calmar(reduced_pnls, reduced_trades)
    red_sharpe = _sharpe(reduced_pnls)

    def _drop(base, red):
        if abs(base) <= 1e-12:
            return 0.0 if red >= base else 1.0
        return max(0.0, (base - red) / abs(base))

    # Cap astronomical Calmars (near-zero DD) for relative-drop math
    CALMAR_CAP = 50.0
    base_c_eff = min(base_calmar, CALMAR_CAP) if base_calmar > 0 else base_calmar
    red_c_eff = min(red_calmar, CALMAR_CAP) if red_calmar > 0 else red_calmar
    c_drop = _drop(base_c_eff, red_c_eff)
    s_drop = _drop(base_sharpe, red_sharpe)

    near_zero_dd = float(base_meta.get("max_drawdown") or 0) < 1e-4
    if near_zero_dd:
        # Relative Calmar is unstable; fail only if reduced sample loses Calmar floor
        # or Sharpe collapses >50%.
        calmar_fail = red_calmar < CALMAR_MIN and base_calmar >= CALMAR_MIN
        sharpe_fail = s_drop > REMOVE_MAX_WIN_DROP_MAX
        failed = calmar_fail or sharpe_fail
        reason = "lottery_overfitting" if failed else "ok_near_zero_dd_mode"
    else:
        failed = (c_drop > REMOVE_MAX_WIN_DROP_MAX) or (s_drop > REMOVE_MAX_WIN_DROP_MAX)
        reason = "lottery_overfitting" if failed else "ok"

    return {
        "pass": not failed,
        "reason": reason,
        "calmar_base": round(base_calmar, 6),
        "calmar_without_max_win": round(red_calmar, 6),
        "calmar_drop": round(c_drop, 6),
        "sharpe_base": round(base_sharpe, 6),
        "sharpe_without_max_win": round(red_sharpe, 6),
        "sharpe_drop": round(s_drop, 6),
        "dropped_pnl": pnls[max_i],
        "threshold_drop": REMOVE_MAX_WIN_DROP_MAX,
        "near_zero_dd_mode": near_zero_dd,
        "max_drawdown_base": base_meta.get("max_drawdown"),
        "max_drawdown_reduced": red_meta.get("max_drawdown"),
    }


def compute_fitness_metrics(trades, base_metrics=None):
    """Compute full multi-objective metrics + check breakdown."""
    trades = list(trades or [])
    pnls = _pnls(trades)
    # Allow pnl list-only via base_metrics override
    if not pnls and base_metrics and isinstance(base_metrics.get("pnls"), list):
        pnls = [float(x) for x in base_metrics["pnls"]]
        trades = [{"pnl_ratio": p} for p in pnls]

    stats = payoff_stats(pnls)
    calmar, calmar_meta = _calmar(pnls, trades)
    sharpe = _sharpe(pnls)
    w5_share, w5_meta = worst5_loss_share(pnls)
    mae_flags, mae_meta = mae_dead_hold_flags(trades, stats["avg_win"])
    lottery = remove_max_win_stress(pnls, trades)

    checks = {
        "sample_size_ge_8": stats["n"] >= 8,
        "calmar_ge_1_5": calmar >= CALMAR_MIN,
        "payoff_ge_2_5": stats["payoff_ratio"] >= PAYOFF_MIN,
        "expectancy_factor_ge_1_0": stats["expectancy_factor_wr_x_payoff"] >= EXPECTANCY_FACTOR_MIN,
        "worst5_loss_share_le_40pct": w5_share <= WORST5_LOSS_SHARE_MAX,
        "mae_dead_hold_clear": len(mae_flags) == 0,
        "remove_max_win_stable": bool(lottery.get("pass")),
    }
    # If no MAE recorded on any trade, do not auto-fail MAE (research fixtures
    # without path data). Formal backtests that emit mae_price_pct are enforced.
    has_mae = any(
        isinstance(t, dict) and (
            t.get("mae_price_pct") is not None or t.get("mae") is not None
        )
        for t in trades
    )
    if not has_mae:
        checks["mae_dead_hold_clear"] = True
        mae_meta["note"] = "no_mae_fields_skip_enforcement"

    return {
        "n": stats["n"],
        "win_rate": stats["win_rate"],
        "win_rate_pct": stats["win_rate_pct"],
        "payoff_ratio": stats["payoff_ratio"],
        "avg_win": stats["avg_win"],
        "avg_loss": stats["avg_loss"],
        "expectancy_factor": stats["expectancy_factor_wr_x_payoff"],
        "classic_expectancy": stats["classic_expectancy_mean_pnl"],
        "calmar": round(calmar, 6),
        "sharpe": round(sharpe, 6),
        "calmar_meta": calmar_meta,
        "worst5_loss_share": round(w5_share, 6),
        "worst5_meta": w5_meta,
        "mae_flags": mae_flags,
        "mae_meta": mae_meta,
        "remove_max_win": lottery,
        "checks": checks,
        "thresholds": {
            "calmar_min": CALMAR_MIN,
            "payoff_min": PAYOFF_MIN,
            "expectancy_factor_min": EXPECTANCY_FACTOR_MIN,
            "worst5_loss_share_max": WORST5_LOSS_SHARE_MAX,
            "mae_vs_avg_win_max": MAE_VS_AVG_WIN_MAX,
            "remove_max_win_drop_max": REMOVE_MAX_WIN_DROP_MAX,
        },
        "formulas": {
            "expectancy_factor": stats["formula_expectancy_factor"],
            "classic_expectancy": stats["formula_classic_expectancy"],
            "payoff": "avg_win / avg_loss_magnitude",
            "calmar": calmar_meta.get("annualization"),
        },
    }


def evaluate_multi_objective(trades, base_metrics=None, enforce=True, min_trades=8):
    """Return {pass, metrics, failed_checks, verdict_tags}.

    enforce=True applies all hard gates (Gate2 / formal fitness path).
    """
    metrics = compute_fitness_metrics(trades, base_metrics=base_metrics)
    checks = dict(metrics["checks"])
    if min_trades and metrics["n"] < int(min_trades):
        checks["sample_size_ge_8"] = False

    failed = [k for k, v in checks.items() if not v]
    tags = []
    if not checks.get("payoff_ge_2_5") or not checks.get("expectancy_factor_ge_1_0"):
        tags.append("pseudo_high_WR_low_payoff")
    if not checks.get("mae_dead_hold_clear"):
        tags.append("dead_hold_to_BE")
    if not checks.get("remove_max_win_stable"):
        tags.append("lottery_overfitting")
    if not checks.get("calmar_ge_1_5"):
        tags.append("calmar_below_floor")
    if not checks.get("worst5_loss_share_le_40pct"):
        tags.append("loss_concentration")

    passed = (len(failed) == 0) if enforce else True
    return {
        "pass": bool(passed),
        "enforce": bool(enforce),
        "metrics": metrics,
        "failed_checks": failed,
        "verdict_tags": tags,
        "checks": checks,
    }


def evaluate_pregate_fitness(trades, base_metrics=None):
    """PreGate / Windtalker formal fitness hook — same hard gates as Gate2."""
    return evaluate_multi_objective(
        trades, base_metrics=base_metrics, enforce=True, min_trades=8,
    )
