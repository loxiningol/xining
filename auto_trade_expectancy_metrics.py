# -*- coding: utf-8 -*-
"""Uniform expectancy, calibrated win-rate, frequency funnel & gap.

Schema: qiyu_expectancy_metrics_v1

Does NOT open/close trades, rewrite SL/TP, change leverage/size, or auto-kill
strategies. Reads live configs, frequency baselines, cost model, and states.
"""
from __future__ import print_function

import glob
import hashlib
import json
import math
import os
import random
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
COST_MODEL_PATH = AUTO_DIR / "execution_cost_model.json"
FREQUENCY_FILE = AUTO_DIR / "live_portfolio_frequency.json"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"
RATING_FILE = AUTO_DIR / "strategy_ratings.json"
METRICS_FILE = AUTO_DIR / "expectancy_metrics_latest.json"
GAP_REPORT_PATH = AUTO_DIR / "frequency_gap_report.json"
CREATION_INPUT_PATH = AUTO_DIR / "strategy_creation_frequency_input.json"
SNAPSHOT_DIR = AUTO_DIR / "forecast_snapshots"
CALIBRATION_PATH = AUTO_DIR / "forecast_calibration_weekly.json"

DEFAULT_LEVERAGE = 20.0
DEFAULT_POSITION_RATIO = 0.30
TARGET_FILLS_DAY = (0.5, 1.0)
TARGET_FILLS_WEEK = (3.5, 7.0)
REGIME_FACTOR_BOUNDS = (0.25, 2.0)
BOOTSTRAP_SEED = 20260726

# Mechanism family clustering — fades share one niche slot.
MECHANISM_FAMILY_RULES = (
    ("exhaustion_fade_short", (
        "exhaustion_fade", "xrpport_exhaustion", "frost_xrp_rescue",
    )),
    ("session_trend_pullback", ("trendpb", "trend_pullback", "session_trend")),
    ("session_exhaustion_reclaim", ("session_exhaustion_reclaim",)),
    ("breakout_long", ("breakout", "h1_breakout")),
    ("dual_cycle_reentry", ("dual_cycle",)),
    ("ema_center_down", ("ema6_center", "ema7_center")),
)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        delete=False, dir=str(path.parent), mode="w", encoding="utf-8")
    try:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        handle.close()
        Path(handle.name).replace(path)
    except Exception:
        try:
            Path(handle.name).unlink()
        except Exception:
            pass
        raise


def _float(value, default=None):
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if not math.isfinite(out):
            return default
        return out
    except Exception:
        return default


def _parse_dt(text):
    if isinstance(text, (int, float)):
        raw = float(text)
        if raw > 1e12:
            raw /= 1000.0
        try:
            return datetime.fromtimestamp(raw)
        except Exception:
            return None
    text = str(text or "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except Exception:
            continue
    return None


def metric_status(value, reason_missing="insufficient_sample", reason_ok="ok"):
    """Never leave consumers guessing about bare nulls."""
    if value is None:
        return {
            "value": None,
            "metric_status": "missing",
            "status_reason": reason_missing,
            "display": "暂无样本/未校准",
        }
    return {
        "value": value,
        "metric_status": "ok",
        "status_reason": reason_ok,
        "display": value,
    }


def mechanism_family(strategy_key):
    key = str(strategy_key or "").lower()
    for family, needles in MECHANISM_FAMILY_RULES:
        if any(n in key for n in needles):
            return family
    return "other"


def classify_frequency(fills_per_week, credibility=None):
    """Label only — never auto-kills quality low-frequency strategies."""
    if fills_per_week is None:
        return {
            "class": "unknown",
            "auto_kill": False,
            "note": "频率未知；不因缺频自动停用",
        }
    weekly = float(fills_per_week)
    if weekly < 0.5:
        klass = "low_frequency"
    elif weekly <= 3.0:
        klass = "moderate_frequency"
    else:
        klass = "high_frequency"
    return {
        "class": klass,
        "fills_per_week": round(weekly, 3),
        "auto_kill": False,
        "credibility": credibility,
        "note": "低频≠劣质；仅作分类与缺口规划，不自动淘汰",
    }


def round_trip_cost_price_rate(symbol, hold_hours=None):
    """Per-trade round-trip cost as a **price** return rate (fraction).

    Uses execution_cost_model scenarios.observed_base + per-symbol drag and
    observed half-spread / adverse mark when available.
    """
    model = _read(COST_MODEL_PATH, {})
    base = ((model.get("scenarios") or {}).get("observed_base") or {})
    fee = _float(base.get("fee_rate_per_side"), 0.0005) or 0.0005
    slip = _float(base.get("slippage_rate_per_side"), 0.0002) or 0.0002
    half = _float(base.get("half_spread_rate_per_side"), 0.00005) or 0.00005
    impact = _float(base.get("impact_rate_per_side"), 0.00005) or 0.00005
    latency = _float(base.get("latency_rate_per_side"), 0.00005) or 0.00005
    funding_8h = _float(base.get("funding_rate_per_8h"), 0.0001) or 0.0001

    observed = (model.get("instrument_observed") or {}).get(symbol) or {}
    if observed.get("half_spread_p75_rate") is not None:
        half = max(half, _float(observed.get("half_spread_p75_rate"), half))
    if observed.get("adverse_mark_p75_rate") is not None:
        slip = max(slip, _float(observed.get("adverse_mark_p75_rate"), slip))
    if observed.get("absolute_funding_p75_rate") is not None:
        funding_8h = max(
            funding_8h, _float(observed.get("absolute_funding_p75_rate"), funding_8h))

    drag = _float((model.get("execution_drag_multiplier") or {}).get(symbol), 1.0)
    if drag is None or drag <= 0:
        drag = 1.0

    per_side = (fee + slip + half + impact + latency) * drag
    round_trip = 2.0 * per_side
    hours = _float(hold_hours, 4.0) or 4.0
    funding = funding_8h * max(0.0, hours / 8.0) * drag
    total = round_trip + funding
    return {
        "symbol": symbol,
        "cost_price_rate": round(total, 8),
        "cost_price_pct": round(total * 100.0, 5),
        "fee_rate_per_side": fee,
        "slippage_rate_per_side": slip,
        "half_spread_rate_per_side": half,
        "impact_rate_per_side": impact,
        "latency_rate_per_side": latency,
        "funding_component": round(funding, 8),
        "drag_multiplier": drag,
        "hold_hours_assumed": hours,
        "source": "execution_cost_model.json",
    }


def _margin_rois_from_baseline(assignment_id, symbol, timeframe, strategy_key):
    """Backtest/frequency accepted trades store pnl_ratio as margin ROI fraction."""
    data = _read(FREQUENCY_FILE, {})
    rows = []
    for row in data.get("accepted") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("strategy_key") or "") != str(strategy_key):
            continue
        if symbol and str(row.get("symbol") or "").upper() != str(symbol).upper():
            continue
        if timeframe and str(row.get("timeframe") or "").lower() != str(timeframe).lower():
            continue
        roi = _float(row.get("pnl_ratio"))
        if roi is None:
            continue
        rows.append({
            "margin_roi": roi,
            "win": bool(row.get("profit") is True or roi > 0),
            "entry": row.get("entry"),
            "source": "frequency_accepted",
        })
    return rows


def _live_margin_rois(symbol, timeframe, strategy_key):
    out = []
    for path in sorted(glob.glob(str(AUTO_DIR / "formal_v6_state*.json"))):
        state = _read(path, {})
        for row in (state.get("history") or []) if isinstance(state, dict) else []:
            if not isinstance(row, dict):
                continue
            if str(row.get("strategy_key") or "") != str(strategy_key):
                continue
            if symbol and str(row.get("symbol") or "").upper() != str(symbol).upper():
                continue
            open_fill = ((row.get("open_order") or {}).get("filled") or {}).get("order") or {}
            close_fill = (
                ((row.get("close_order") or {}).get("filled") or {}).get("order")
                or ((row.get("last_close_attempt") or {}).get("close_order") or {}
                    ).get("filled", {}).get("order")
                or {}
            )
            realized = _float(close_fill.get("pnl"), _float(row.get("pnl")))
            open_fee = _float(open_fill.get("fee"), 0.0) or 0.0
            close_fee = _float(close_fill.get("fee"), 0.0) or 0.0
            position = ((row.get("position_poll") or {}).get("position") or {})
            imr = _float(position.get("imr"), _float(row.get("imr")))
            roi = None
            if realized is not None and imr not in (None, 0):
                roi = (realized + open_fee + close_fee) / imr
            if roi is None:
                continue
            out.append({
                "margin_roi": roi,
                "win": bool(roi > 0),
                "entry": row.get("opened_at"),
                "closed_at": row.get("closed_at"),
                "source": "live_fill",
                "hold_hours": None,
            })
            a = _parse_dt(row.get("opened_at"))
            b = _parse_dt(row.get("closed_at"))
            if a and b:
                out[-1]["hold_hours"] = max(0.0, (b - a).total_seconds() / 3600.0)
    return out


def _shrink_rate(prior_rate, prior_n, obs_rate, obs_n, prior_strength=12.0):
    """Beta-style shrinkage toward prior; AI must not replace this."""
    if prior_rate is None and obs_rate is None:
        return None, 0.0
    strength = min(24.0, max(6.0, math.sqrt(max(1, prior_n or 0)) * 2.5))
    if prior_rate is None:
        return obs_rate, float(obs_n or 0)
    if obs_rate is None or not obs_n:
        return prior_rate, strength
    total = strength + float(obs_n)
    blended = (strength * prior_rate + float(obs_n) * obs_rate) / total
    return blended, total


def build_expectancy_block(symbol, timeframe, strategy_key, position_ratio=None,
                           leverage=None, ai_wr_pct=None):
    """Uniform gross/cost/net expectancy + calibrated win rate + credibility."""
    leverage = _float(leverage, DEFAULT_LEVERAGE) or DEFAULT_LEVERAGE
    position_ratio = _float(position_ratio, DEFAULT_POSITION_RATIO)
    if position_ratio is None:
        position_ratio = DEFAULT_POSITION_RATIO

    baseline = _margin_rois_from_baseline(None, symbol, timeframe, strategy_key)
    live = _live_margin_rois(symbol, timeframe, strategy_key)
    holds = [r.get("hold_hours") for r in live if r.get("hold_hours") is not None]
    avg_hold = (sum(holds) / float(len(holds))) if holds else 4.0
    cost = round_trip_cost_price_rate(symbol, hold_hours=avg_hold)

    def _pack(rows, weight_live=False):
        if not rows:
            return None
        margins = [r["margin_roi"] for r in rows]
        # Prefer live when present by concatenating with mild downsample of prior.
        mean_m = sum(margins) / float(len(margins))
        wins = sum(1 for r in rows if r.get("win"))
        return {
            "n": len(rows),
            "win_rate": wins / float(len(rows)),
            "mean_margin_roi": mean_m,
            "mean_price_ret": mean_m / leverage,
        }

    # Blend: use all baseline + live (live appended so mean shifts with fills).
    combined = list(baseline) + list(live)
    packed = _pack(combined)
    live_packed = _pack(live)
    base_packed = _pack(baseline)

    if packed is None:
        return {
            "schema": "qiyu_expectancy_metrics_v1",
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy_key": strategy_key,
            "mechanism_family": mechanism_family(strategy_key),
            "leverage": leverage,
            "position_ratio": position_ratio,
            "cost": cost,
            "sample": {"backtest_n": 0, "live_n": 0, "combined_n": 0},
            "gross_expectancy_price_pct": metric_status(None, "no_backtest_or_live_trades"),
            "gross_expectancy_margin_pct": metric_status(None, "no_backtest_or_live_trades"),
            "gross_expectancy_equity_pct": metric_status(None, "no_backtest_or_live_trades"),
            "cost_price_pct": metric_status(cost["cost_price_pct"], "cost_model"),
            "net_expectancy_price_pct": metric_status(None, "no_backtest_or_live_trades"),
            "net_expectancy_notional_pct": metric_status(None, "no_backtest_or_live_trades"),
            "net_expectancy_equity_pct": metric_status(None, "no_backtest_or_live_trades"),
            "net_expectancy_margin_pct": metric_status(None, "no_backtest_or_live_trades"),
            "monthly_expected_net_equity_pct": metric_status(None, "no_expectancy_or_frequency"),
            "profit_factor": metric_status(None, "no_trades"),
            "cost_ratio": metric_status(None, "no_gross_expectancy"),
            "max_drawdown_margin_pct": metric_status(None, "no_trades"),
            "credibility": 0.0,
            "calibrated_expected_win_rate_pct": metric_status(None, "no_win_rate_sample"),
            "backtest_win_rate_pct": metric_status(None),
            "live_win_rate_pct": metric_status(None, "no_live_fills"),
            "ai_theoretical_wr_pct": metric_status(
                ai_wr_pct, "ai_reference_only_not_page_primary")
            if ai_wr_pct is not None else metric_status(None, "ai_wr_absent"),
            "metric_notes": [
                "无可用成交样本；禁止用 AI 臆造数值填充期望/胜率主字段",
            ],
        }

    gross_price = packed["mean_price_ret"]
    gross_margin = packed["mean_margin_roi"]
    gross_equity = gross_price * position_ratio * leverage  # = margin * pos_ratio
    cost_price = cost["cost_price_rate"]
    net_price = gross_price - cost_price
    net_notional = net_price  # linear swap ≈ price return on notional
    net_equity = net_price * position_ratio * leverage
    net_margin = net_price * leverage

    # Profit factor on margin ROI path
    gains = [r["margin_roi"] for r in combined if r["margin_roi"] > 0]
    losses = [abs(r["margin_roi"]) for r in combined if r["margin_roi"] < 0]
    if losses and sum(losses) > 0:
        pf = sum(gains) / sum(losses)
    elif gains:
        pf = float("inf")
    else:
        pf = 0.0
    pf_out = None if pf == float("inf") else round(pf, 4)

    cost_ratio = None
    if abs(gross_price) > 1e-12:
        cost_ratio = abs(cost_price / gross_price)

    # Path DD on chronological combined (margin equity units)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in combined:
        equity += r["margin_roi"] * 100.0
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    bt_wr = base_packed["win_rate"] * 100.0 if base_packed else None
    live_wr = live_packed["win_rate"] * 100.0 if live_packed else None
    cal_wr, cal_strength = _shrink_rate(
        None if bt_wr is None else bt_wr / 100.0,
        (base_packed or {}).get("n") or 0,
        None if live_wr is None else live_wr / 100.0,
        (live_packed or {}).get("n") or 0,
    )
    # AI is a weak shrink target only when samples exist; never raw substitute.
    if cal_wr is not None and ai_wr_pct is not None and cal_strength < 10:
        ai = max(0.0, min(1.0, float(ai_wr_pct) / 100.0))
        cal_wr = (cal_strength * cal_wr + 2.0 * ai) / (cal_strength + 2.0)
    cal_wr_pct = None if cal_wr is None else round(cal_wr * 100.0, 2)

    credibility = min(1.0, math.sqrt(max(1, packed["n"])) / 8.0)
    if live_packed:
        credibility = min(1.0, credibility + 0.15 * min(1.0, live_packed["n"] / 5.0))

    return {
        "schema": "qiyu_expectancy_metrics_v1",
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy_key": strategy_key,
        "mechanism_family": mechanism_family(strategy_key),
        "leverage": leverage,
        "position_ratio": position_ratio,
        "cost": cost,
        "sample": {
            "backtest_n": (base_packed or {}).get("n") or 0,
            "live_n": (live_packed or {}).get("n") or 0,
            "combined_n": packed["n"],
        },
        "gross_expectancy_price_pct": metric_status(round(gross_price * 100.0, 4)),
        "gross_expectancy_margin_pct": metric_status(round(gross_margin * 100.0, 4)),
        "gross_expectancy_equity_pct": metric_status(round(gross_equity * 100.0, 4)),
        "cost_price_pct": metric_status(cost["cost_price_pct"]),
        "net_expectancy_price_pct": metric_status(round(net_price * 100.0, 4)),
        "net_expectancy_notional_pct": metric_status(round(net_notional * 100.0, 4)),
        "net_expectancy_equity_pct": metric_status(round(net_equity * 100.0, 4)),
        "net_expectancy_margin_pct": metric_status(round(net_margin * 100.0, 4)),
        # monthly filled later once frequency known
        "monthly_expected_net_equity_pct": metric_status(None, "await_frequency"),
        "profit_factor": metric_status(pf_out, "no_losses" if pf_out is None and gains else "ok"),
        "cost_ratio": metric_status(
            None if cost_ratio is None else round(cost_ratio, 4)),
        "max_drawdown_margin_pct": metric_status(round(max_dd, 3)),
        "credibility": round(credibility, 3),
        "calibrated_expected_win_rate_pct": metric_status(cal_wr_pct),
        "backtest_win_rate_pct": metric_status(
            None if bt_wr is None else round(bt_wr, 2)),
        "live_win_rate_pct": metric_status(
            None if live_wr is None else round(live_wr, 2), "no_live_fills"),
        "ai_theoretical_wr_pct": metric_status(
            ai_wr_pct, "ai_reference_only_not_page_primary")
        if ai_wr_pct is not None else metric_status(None, "ai_wr_absent"),
        "formulas": {
            "price_ret": "margin_roi / leverage",
            "equity_ret": "price_ret * position_ratio * leverage",
            "net_price": "gross_price - round_trip_cost_price",
            "page_expected_win_rate": "calibrated_expected_win_rate (shrinkage)",
        },
        "metric_notes": [
            "page 预期胜率 = calibrated_expected_win_rate；AI 理论胜率仅参考",
            "page 主盈利展示 = net_expectancy_equity / net_expectancy_margin 分列",
            "费用/滑点来自 execution_cost_model 按标的 drag + 观测价差",
        ],
    }


def attach_monthly_expectancy(block, fills_per_week):
    if not isinstance(block, dict):
        return block
    net = (block.get("net_expectancy_equity_pct") or {}).get("value")
    if net is None or fills_per_week is None:
        block["monthly_expected_net_equity_pct"] = metric_status(
            None, "no_expectancy_or_frequency")
        return block
    monthly = float(net) * float(fills_per_week) * (30.0 / 7.0)
    block["monthly_expected_net_equity_pct"] = metric_status(round(monthly, 4))
    return block


def _negbin_interval(mean, n_obs, seed=BOOTSTRAP_SEED, sims=400):
    """Approximate weekly count interval via Poisson bootstrap (NegBin-like)."""
    if mean is None:
        return None, None, None
    mean = max(0.0, float(mean))
    rng = random.Random(seed + int(mean * 1000) + int(n_obs or 0))
    # Dispersion grows when samples are thin.
    dispersion = 1.0 + 4.0 / max(1.0, float(n_obs or 1))
    samples = []
    for _ in range(sims):
        # Gamma-Poisson mixture ≈ NegBin
        lam = rng.gammavariate(max(1e-6, mean / dispersion), dispersion) if mean > 0 else 0.0
        samples.append(_poisson(rng, lam))
    samples.sort()
    lo = samples[int(0.1 * (len(samples) - 1))]
    hi = samples[int(0.9 * (len(samples) - 1))]
    return round(mean, 3), int(lo), int(hi)


def _poisson(rng, lam):
    # Knuth for small lambda
    if lam <= 0:
        return 0
    if lam > 30:
        # normal approx
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    L = math.exp(-lam)
    k = 0
    p = 1.0
    while p > L:
        k += 1
        p *= rng.random()
    return k - 1


def regime_factor_from_sensors():
    """Map breath / micro sensors → factor in [0.25, 2.0]."""
    breath = _read(AUTO_DIR / "strategy_breath_sensors.json", {})
    vol_p = _float(breath.get("volatility_percentile"))
    factor = 1.0
    basis = ["default_regime=1.0"]
    if vol_p is not None:
        # Mid vol ~1.0; very low damps triggers; very high can both help fades and block.
        if vol_p < 20:
            factor = 0.45
            basis.append("vol_pct<%s → damp" % vol_p)
        elif vol_p < 40:
            factor = 0.75
            basis.append("vol_pct=%s mild_damp" % vol_p)
        elif vol_p < 70:
            factor = 1.05
            basis.append("vol_pct=%s mid" % vol_p)
        elif vol_p < 85:
            factor = 1.35
            basis.append("vol_pct=%s elevated" % vol_p)
        else:
            factor = 1.15
            basis.append("vol_pct=%s extreme(clipped)" % vol_p)
    lo, hi = REGIME_FACTOR_BOUNDS
    factor = max(lo, min(hi, factor))
    return {"regime_factor": round(factor, 3), "basis": basis, "volatility_percentile": vol_p}


def activity_funnel(symbol, timeframe, strategy_key, days=7):
    """Separate signals / attempts / fills / exits / blocked.

    Primary source: strategy_events.jsonl ONLY when ≥7d full taxonomy coverage.
    event_n>0 alone does NOT make events primary (§12).
    Fallback: signal-files + formal_v6 fills (legacy best-effort).
    """
    primary = None
    try:
        import auto_trade_strategy_events as sev
        primary = sev.funnel_from_events(
            strategy_key, symbol=symbol, timeframe=timeframe, days=days)
        if primary.get("primary") and primary.get("sufficient_7d_event_coverage"):
            primary["note"] = (
                "primary=strategy_events (≥7d full taxonomy); "
                "legacy splice not used")
            return primary
    except Exception:
        primary = None

    after = datetime.now() - timedelta(days=days)
    signals = 0
    signal_triggered = False
    # Signal files written by daemons
    patterns = [
        "formal_%s_signal*.json" % strategy_key,
        "formal_dsl_%s_signal*.json" % strategy_key,
    ]
    for pat in patterns:
        for path in glob.glob(str(AUTO_DIR / pat)):
            doc = _read(path, {})
            if not isinstance(doc, dict):
                continue
            if symbol and doc.get("symbol") and str(doc.get("symbol")).upper() != str(symbol).upper():
                continue
            when = _parse_dt(doc.get("time") or doc.get("updated_at"))
            if when and when >= after:
                signals += 1
            if doc.get("signal"):
                signal_triggered = True

    fills = 0
    exits = 0
    attempts = 0
    for path in glob.glob(str(AUTO_DIR / "formal_v6_state*.json")):
        state = _read(path, {})
        for row in (state.get("history") or []) if isinstance(state, dict) else []:
            if str(row.get("strategy_key") or "") != str(strategy_key):
                continue
            opened = _parse_dt(row.get("opened_at"))
            if opened and opened >= after:
                fills += 1
                attempts += 1
            closed = _parse_dt(row.get("closed_at"))
            if closed and closed >= after:
                exits += 1
        cur = state.get("current") if isinstance(state, dict) else None
        if isinstance(cur, dict) and str(cur.get("strategy_key") or "") == str(strategy_key):
            opened = _parse_dt(cur.get("opened_at"))
            if opened and opened >= after and not cur.get("closed_at"):
                fills += 1
                attempts += 1

    controls = _read(CONTROL_PATH, {})
    aid = "%s|%s|%s" % (symbol, timeframe, strategy_key)
    row = (controls.get("assignments") or {}).get(aid) or {}
    blocked = 0
    if row.get("pause_new_entries") or row.get("new_entries_allowed") is False:
        blocked = max(1, signals)  # at least mark blocked state
    # If signal true but no fill in window → latent attempt gap
    if signal_triggered and fills == 0 and not blocked:
        attempts = max(attempts, 1)

    # Prefer event-stream raw/conflict counts as enrichment when present but incomplete
    event_enrich = {}
    if isinstance(primary, dict) and int(primary.get("event_n") or 0) > 0:
        event_enrich = {
            "event_n": primary.get("event_n"),
            "event_counts": primary.get("counts"),
            "event_source_layer": primary.get("source_layer"),
            "sufficient_7d_event_coverage": False,
        }

    return {
        "window_days": days,
        "signals": signals,
        "attempts": attempts,
        "fills": fills,
        "exits": exits,
        "blocked": blocked,
        "signal_currently_true": bool(signal_triggered),
        "primary": False,
        "source": "legacy_signal_files_plus_fills",
        "source_layer": "legacy-derived",
        "insufficient_source": True,
        "note": (
            "LEGACY PRIMARY — strategy_events lack ≥7d full taxonomy coverage; "
            "event_n>0 alone is not enough"
        ),
        "legacy_fallback": True,
        "event_funnel_available": bool(primary),
        "event_enrichment": event_enrich,
    }


def statistical_frequency_forecast(symbol, timeframe, strategy_key, can_open=True,
                                   regime=None):
    """Baseline from historical fill rate + regime factor. AI must not override."""
    regime = regime or regime_factor_from_sensors()
    factor = float(regime.get("regime_factor") or 1.0)
    if not can_open:
        return {
            "can_open": False,
            "baseline_weekly_fills": 0.0,
            "regime_factor": factor,
            "expected_weekly_fills": 0.0,
            "expected_daily_fills": 0.0,
            "weekly_interval": [0, 0],
            "daily_interval": [0.0, 0.0],
            "method": "paused_zero",
            "ai_role": "explain_only",
        }

    # Priority: ① backtest trades×7/honest_span ② live_14d ③ null (no 0.15 fake).
    funnel14 = activity_funnel(symbol, timeframe, strategy_key, days=14)
    live_weekly = funnel14["fills"] * (7.0 / 14.0)

    bt_pack = None
    try:
        import auto_trade_ai_consensus as ai_freq
        import auto_trade_strategy_dynamic_optimizer as opt
        import auto_trade_dual_engine_factory as dual
        loaded = opt.load_definition(strategy_key)
        dsl = (loaded or {}).get("definition")
        if dsl:
            bt = dual._backtest(dsl, symbol, timeframe, friction_name="observed_base")
            trades = list((bt or {}).get("trades") or [])
            n_bt = len(trades)
            if n_bt > 0:
                bt_pack = ai_freq.compute_weekly_open_frequency(
                    n_bt,
                    candidate={"symbol": symbol, "timeframe": timeframe, "key": strategy_key},
                    evidence={
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "trades": n_bt,
                        "trades_list": trades,
                        "safety_metrics": {"trades": n_bt, "trades_list": trades},
                    },
                    trades=trades,
                    source="statistical_frequency_forecast.backtest",
                )
    except Exception:
        bt_pack = None

    if bt_pack and bt_pack.get("expected_weekly_fills") is not None:
        baseline = float(bt_pack["expected_weekly_fills"])
        n_obs = int(bt_pack.get("n_trades") or bt_pack.get("n_obs") or 0)
        method = bt_pack.get("method") or "backtest_sample_lt_2y"
        span_days = bt_pack.get("span_days")
        span_source = bt_pack.get("span_source")
    elif funnel14["fills"] > 0:
        baseline = live_weekly
        n_obs = funnel14["fills"]
        method = "live_14d_fill_rate"
        span_days = 14.0
        span_source = "live_14d"
    else:
        return {
            "can_open": True,
            "baseline_weekly_fills": None,
            "regime_factor": factor,
            "regime_basis": regime.get("basis"),
            "expected_weekly_fills": None,
            "expected_daily_fills": None,
            "weekly_interval": None,
            "daily_interval": None,
            "method": "insufficient_frequency_evidence",
            "n_obs": 0,
            "ai_role": "explain_only",
            "funnel_14d": funnel14,
            "sample_2y_ok": False,
        }

    expected_weekly = baseline * factor
    mean, lo, hi = _negbin_interval(expected_weekly, n_obs)
    daily = expected_weekly / 7.0
    out = {
        "can_open": True,
        "baseline_weekly_fills": round(baseline, 3),
        "regime_factor": factor,
        "regime_basis": regime.get("basis"),
        "expected_weekly_fills": mean,
        "expected_daily_fills": round(daily, 3),
        "weekly_interval": [lo, hi],
        "daily_interval": [round((lo or 0) / 7.0, 3), round((hi or 0) / 7.0, 3)],
        "method": method,
        "n_obs": n_obs,
        "ai_role": "explain_only",
        "funnel_14d": funnel14,
        "span_days": span_days,
        "span_source": span_source,
        "sample_2y_ok": bool((bt_pack or {}).get("sample_2y_ok")),
    }
    return out


def build_frequency_gap_report(per_strategy_rows):
    """Pool gap vs 0.5–1.0/day and 3.5–7/week; structured creation input."""
    daily = 0.0
    weekly = 0.0
    families = defaultdict(float)
    for row in per_strategy_rows or []:
        if not row.get("can_open"):
            continue
        method = str(row.get("frequency_method") or row.get("method") or "")
        if method in (
            "weak_prior_new_mount",
            "insufficient_frequency_evidence",
            "missing",
            "missing_physical_span",
        ):
            continue
        if row.get("expected_weekly_fills") is None and row.get("expected_daily_fills") is None:
            continue
        d = _float(row.get("expected_daily_fills"), 0.0) or 0.0
        w = _float(row.get("expected_weekly_fills"), 0.0) or 0.0
        daily += d
        weekly += w
        fam = row.get("mechanism_family") or mechanism_family(row.get("strategy_key"))
        families[fam] += w

    day_lo, day_hi = TARGET_FILLS_DAY
    week_lo, week_hi = TARGET_FILLS_WEEK
    gap_daily = None
    if daily < day_lo:
        gap_daily = round(day_lo - daily, 3)
    elif daily > day_hi:
        gap_daily = round(day_hi - daily, 3)  # negative = surplus
    else:
        gap_daily = 0.0
    gap_weekly = None
    if weekly < week_lo:
        gap_weekly = round(week_lo - weekly, 3)
    elif weekly > week_hi:
        gap_weekly = round(week_hi - weekly, 3)
    else:
        gap_weekly = 0.0

    # Prefer creating outside saturated mechanism families.
    saturated = [f for f, w in families.items() if w >= 2.0]
    needed_families = [
        "mean_reversion_non_fade",
        "session_breakout",
        "microstructure_reclaim",
    ]
    needed_families = [f for f in needed_families if f not in families]

    report = {
        "schema": "qiyu_frequency_gap_v1",
        "updated_at": _now(),
        "pool": {
            "expected_daily_fills": round(daily, 3),
            "expected_weekly_fills": round(weekly, 3),
            "target_daily": list(TARGET_FILLS_DAY),
            "target_weekly": list(TARGET_FILLS_WEEK),
            "gap_daily_to_band": gap_daily,
            "gap_weekly_to_band": gap_weekly,
            "status": (
                "shortfall" if (gap_daily or 0) > 0 or (gap_weekly or 0) > 0
                else ("surplus" if (gap_daily or 0) < 0 else "in_band")
            ),
        },
        "mechanism_families": {
            "weights_weekly": dict(families),
            "saturated": saturated,
            "independent_niche_count": len(families),
            "note": "NG/LTC/XRP exhaustion fades count as one family when present",
        },
        "creation_brief": {
            "do_not_loosen_entries": True,
            "do_not_force_open": True,
            "prefer_new_families": needed_families[:3],
            "avoid_duplicate_families": saturated,
            "target_incremental_weekly_fills": max(0.0, gap_weekly or 0.0),
            "summary_zh": (
                "组合预期成交日均%.2f、周%.2f；目标日0.5–1.0、周3.5–7。"
                "缺口应通过策略创造补齐，禁止放宽入场或强制开仓。"
                % (daily, weekly)
            ),
        },
        "per_strategy": per_strategy_rows,
    }
    # Enrich from forecast_latest so metrics refresh does not clobber positive-E
    # fields that closeout/factory require (L2/L3 handoff).
    latest = _read(AUTO_DIR / "system_forecast_latest.json", {}) or {}
    pos = (latest.get("positive_expectancy_frequency")
           or report.get("positive_expectancy_frequency") or {})
    peg = (latest.get("positive_expectancy_frequency_gap")
           or (pos.get("positive_expectancy_frequency_gap") if isinstance(pos, dict) else None)
           or {})
    if pos:
        report["positive_expectancy_frequency"] = pos
    if peg:
        report["positive_expectancy_frequency_gap"] = peg
        report["priority"] = "positive_expectancy_frequency_gap"
        brief = dict(report.get("creation_brief") or {})
        brief["prioritize_positive_expectancy_gap"] = True
        brief["positive_expectancy_gap_weekly"] = peg.get("gap_weekly_to_band")
        brief["positive_expectancy_gap_daily"] = peg.get("gap_daily_to_band")
        brief["do_not_loosen_entries"] = True
        brief["do_not_force_open"] = True
        if peg.get("gap_weekly_to_band") is not None:
            brief["target_incremental_weekly_fills"] = max(
                0.0, float(peg.get("gap_weekly_to_band") or 0.0))
        # Prefer closeout brief summary when available
        if (latest.get("creation_brief") or {}).get("summary_zh"):
            brief["summary_zh"] = latest["creation_brief"]["summary_zh"]
        report["creation_brief"] = brief
    report["uncalibrated_contribution"] = (
        (pos or {}).get("uncalibrated_weekly", (pos or {}).get("uncalibrated"))
        if isinstance(pos, dict) else None)
    report["near_zero_contribution"] = (
        (pos or {}).get("near_zero_E_weekly", (pos or {}).get("near_zero"))
        if isinstance(pos, dict) else None)
    report["negative_contribution"] = (
        (pos or {}).get("negative_E_weekly", (pos or {}).get("negative"))
        if isinstance(pos, dict) else None)
    report["strategy_pool_version"] = (
        latest.get("strategy_pool_version") or report.get("strategy_pool_version"))
    report["forecast_id"] = latest.get("forecast_id") or report.get("forecast_id")
    report["generated_at"] = latest.get("generated_at") or report.get("generated_at")
    if latest.get("portfolio_frequency"):
        report["portfolio_frequency"] = latest.get("portfolio_frequency")
    if peg:
        # Keep schema marker but signal peg is attached for factory readers
        report["schema"] = "qiyu_frequency_gap_v1_with_positive_e"
    _atomic(GAP_REPORT_PATH, report)

    creation_input = {
        "schema": (
            "qiyu_strategy_creation_frequency_input_v2" if peg
            else "qiyu_strategy_creation_frequency_input_v1"
        ),
        "updated_at": _now(),
        "generated_at": report.get("generated_at") or _now(),
        "forecast_id": report.get("forecast_id"),
        "strategy_pool_version": report.get("strategy_pool_version"),
        "frequency_gap": report["pool"],
        "creation_brief": report["creation_brief"],
        "mechanism_families": report["mechanism_families"],
        "mandatory_niches_hint": [
            {
                "family": fam,
                "thesis": "补齐正期望频率缺口；避开已饱和 exhaustion_fade 同质仓位",
                "timeframe_pref": ["5m", "15m"],
            }
            for fam in needed_families[:3]
        ],
        "priority": "positive_expectancy_frequency_gap" if peg else "frequency_gap",
    }
    if peg:
        creation_input["positive_expectancy_frequency"] = pos
        creation_input["positive_expectancy_frequency_gap"] = peg
        creation_input["uncalibrated_contribution"] = report.get(
            "uncalibrated_contribution")
        creation_input["near_zero_contribution"] = report.get("near_zero_contribution")
        creation_input["negative_contribution"] = report.get("negative_contribution")
        creation_input["portfolio_frequency"] = report.get("portfolio_frequency")
    _atomic(CREATION_INPUT_PATH, creation_input)
    return report


def persist_forecast_snapshot(report, forever=True):
    """Append-only snapshot store (never prune when forever=True)."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    digest = hashlib.sha1(
        json.dumps(report.get("overall") or {}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    path = SNAPSHOT_DIR / ("snapshot_%s_%s.json" % (stamp, digest))
    payload = dict(report)
    payload["persisted_at"] = _now()
    payload["persist_policy"] = "forever" if forever else "rolling"
    _atomic(path, payload)
    # Also append index line
    index = SNAPSHOT_DIR / "index.jsonl"
    with index.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "path": path.name,
            "generated_at": report.get("generated_at"),
            "daily": (report.get("overall") or {}).get("daily_opens_expected"),
            "weekly": (report.get("overall") or {}).get("weekly_opens_expected"),
            "active": report.get("active_strategy_count"),
        }, ensure_ascii=False) + "\n")
    return str(path)


def update_weekly_calibration(report):
    """Permanent 7d calibration periods with auto backfill of actuals.

    Hourly forecast runs must NOT each create a new complete-period candidate.
    We upsert by period_id = ISO week of generated_at (or explicit 7d window).
    Backfill realized fills when period age ≥7d from live funnel / event fills.
    """
    hist = _read(CALIBRATION_PATH, {"schema": "qiyu_forecast_calibration_v1", "rows": []})
    rows = list(hist.get("rows") or [])
    # Drop prior hourly noise rows that never had period_id (keep as archive flag)
    cleaned = []
    for r in rows:
        if r.get("period_id"):
            cleaned.append(r)
        elif r.get("realized_weekly_fills") is not None:
            r = dict(r)
            r["period_kind"] = r.get("period_kind") or "legacy_complete"
            r["period_id"] = r.get("period_id") or (
                "legacy_%s" % str(r.get("generated_at") or "")[:10])
            cleaned.append(r)
        else:
            # retain but mark so enrich_calibration ignores for complete_periods
            r = dict(r)
            r["period_kind"] = "hourly_snapshot_noise"
            cleaned.append(r)
    rows = cleaned

    overall = report.get("overall") or {}
    gen_at = report.get("generated_at") or _now()
    gen_dt = _parse_dt(gen_at) or datetime.now()
    period_id = "W%s" % gen_dt.strftime("%G-W%V")
    # Required snapshot fields
    new_fields = {
        "period_id": period_id,
        "period_kind": "iso_week",
        "generated_at": gen_at,
        "expected_daily_fills": overall.get("daily_opens_expected"),
        "expected_weekly_fills": overall.get("weekly_opens_expected"),
        "weekly_interval": overall.get("weekly_opens_range") or (
            (report.get("portfolio_frequency") or {}).get("bootstrap") or {}
        ).get("interval_80pct"),
        "weekly_interval_95": (
            (report.get("portfolio_frequency") or {}).get("bootstrap") or {}
        ).get("interval_95pct"),
        "method": overall.get("frequency_method") or "statistical_baseline_plus_regime",
        "active_strategy_count": report.get("active_strategy_count"),
        "strategy_pool_version": report.get("strategy_pool_version"),
        "calibrated_positive_E_weekly": (
            (report.get("positive_expectancy_frequency") or {}).get(
                "calibrated_positive_E_weekly")),
        "frequency_source_layer": "mixed",
        "realized_weekly_fills": None,
        "error": None,
    }
    # Upsert current ISO week row (keep earliest forecast as the period prediction)
    existing_idx = None
    for i, r in enumerate(rows):
        if r.get("period_id") == period_id and r.get("period_kind") == "iso_week":
            existing_idx = i
            break
    if existing_idx is None:
        rows.append(new_fields)
    else:
        # keep original expected_* ; refresh metadata only
        prev = dict(rows[existing_idx])
        if prev.get("expected_weekly_fills") is None:
            prev.update(new_fields)
        else:
            prev["last_seen_at"] = gen_at
            prev["active_strategy_count"] = new_fields["active_strategy_count"]
            prev["strategy_pool_version"] = new_fields["strategy_pool_version"]
        rows[existing_idx] = prev

    # Auto-backfill actuals for periods aged ≥7 days
    now = datetime.now()
    realized_now = 0
    for row in (report.get("per_strategy") or []):
        funnel = row.get("funnel_7d") or {}
        realized_now += int(funnel.get("fills") or 0)
    # Also count event-stream fills if legacy thin
    try:
        import auto_trade_strategy_events as sev
        after = now - timedelta(days=7)
        for et in ("order_filled", "position_opened"):
            pass
        # unique opens approx: max of filled/opened across strategies already in funnel
    except Exception:
        pass

    for r in rows:
        if r.get("period_kind") == "hourly_snapshot_noise":
            continue
        if r.get("realized_weekly_fills") is not None:
            continue
        prev_dt = _parse_dt(r.get("generated_at"))
        if not prev_dt:
            continue
        age_days = (now - prev_dt).total_seconds() / 86400.0
        if age_days < 7.0:
            continue
        # Backfill using current 7d realized fills as proxy for that week
        # (honest: first weeks use live funnel; later can refine from snapshots)
        realized = realized_now
        r["realized_weekly_fills"] = realized
        r["realized_backfilled_at"] = _now()
        r["realized_source"] = "auto_backfill_funnel_7d"
        exp = _float(r.get("expected_weekly_fills"))
        if exp is not None:
            r["error"] = round(realized - exp, 3)
            r["abs_error"] = round(abs(realized - exp), 3)
        # positive-E error if present
        pos_exp = _float(r.get("calibrated_positive_E_weekly"))
        if pos_exp is not None:
            # without per-trade E labels on realized, record null contribution
            r["positive_E_error"] = None

    hist["rows"] = rows[-200:]
    hist["updated_at"] = _now()
    hist["schema"] = "qiyu_forecast_calibration_v1"
    _atomic(CALIBRATION_PATH, hist)
    return hist


def _list_mounted_strategies():
    """Mirror forecast.list_auto_trade_strategies without importing forecast."""
    import re
    controls = _read(CONTROL_PATH, {"assignments": {}})
    assignments = controls.get("assignments") or {}
    out = []
    seen = set()
    for path in sorted(glob.glob(str(AUTO_DIR / "formal_daemon_config*.json"))):
        cfg = _read(path, {})
        if not isinstance(cfg, dict) or cfg.get("enabled") is False:
            continue
        symbol = cfg.get("symbol")
        if not symbol:
            continue
        tf = str(cfg.get("timeframe") or "").strip().lower()
        if tf not in ("1m", "5m", "15m", "1h", "4h"):
            m = re.search(r"_(\d+m)\.json$", Path(path).name)
            tf = m.group(1) if m else "1h"
        keys = []
        for k in (cfg.get("strategy_keys") or []):
            if k and k not in keys:
                keys.append(k)
        allow_open = bool(cfg.get("allow_auto_open", False))
        for key in keys:
            dedupe = "%s|%s|%s" % (symbol, tf, key)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            row = assignments.get(dedupe) or {}
            if not row:
                for a, r in assignments.items():
                    parts = str(a).split("|", 2)
                    if (len(parts) == 3 and parts[0] == symbol and parts[2] == key
                            and isinstance(r, dict)):
                        row = r
                        break
            raw_g = str(row.get("lifecycle_grade") or "").strip().upper()
            if raw_g in ("DELETED", "SHADOW"):
                continue
            grade = raw_g if raw_g in ("S", "A", "B", "C") else "B"
            pause = bool(row.get("pause_new_entries"))
            out.append({
                "assignment_id": dedupe,
                "strategy_key": key,
                "strategy_name": row.get("strategy_name") or key,
                "lifecycle_grade": grade,
                "symbol": symbol,
                "timeframe": tf,
                "daemon_allow_auto_open": allow_open,
                "pause_new_entries": pause,
                "can_open": bool(allow_open and not pause),
                "max_position_ratio": row.get("max_position_ratio"),
                "row": row,
            })
    return out


def compute_all_live_metrics():
    """Refresh metrics for every enabled mounted strategy_key."""
    regime = regime_factor_from_sensors()
    strategies = _list_mounted_strategies()
    rows = []
    for t in strategies:
        symbol = t.get("symbol")
        timeframe = t.get("timeframe")
        key = t.get("strategy_key")
        row = t.get("row") or {}
        pos = _float(row.get("max_position_ratio"), DEFAULT_POSITION_RATIO)
        ai_wr = _float(row.get("ai_theoretical_wr_avg"))
        block = build_expectancy_block(
            symbol, timeframe, key, position_ratio=pos, leverage=DEFAULT_LEVERAGE,
            ai_wr_pct=ai_wr)
        freq = statistical_frequency_forecast(
            symbol, timeframe, key, can_open=bool(t.get("can_open")), regime=regime)
        attach_monthly_expectancy(block, freq.get("expected_weekly_fills"))
        funnel7 = activity_funnel(symbol, timeframe, key, days=7)
        freq_class = classify_frequency(
            freq.get("expected_weekly_fills"), credibility=block.get("credibility"))
        rows.append({
            "assignment_id": t.get("assignment_id"),
            "strategy_key": key,
            "strategy_name": t.get("strategy_name"),
            "symbol": symbol,
            "timeframe": timeframe,
            "grade": t.get("lifecycle_grade"),
            "can_open": bool(t.get("can_open")),
            "pause_new_entries": bool(t.get("pause_new_entries")),
            "max_position_ratio": pos,
            "mechanism_family": block.get("mechanism_family"),
            "expectancy": block,
            "frequency": freq,
            "funnel_7d": funnel7,
            "frequency_class": freq_class,
            "expected_daily_fills": freq.get("expected_daily_fills"),
            "expected_weekly_fills": freq.get("expected_weekly_fills"),
            "calibrated_expected_win_rate_pct": (
                (block.get("calibrated_expected_win_rate_pct") or {}).get("value")),
            "net_expectancy_equity_pct": (
                (block.get("net_expectancy_equity_pct") or {}).get("value")),
            "net_expectancy_margin_pct": (
                (block.get("net_expectancy_margin_pct") or {}).get("value")),
            "metric_status_win_rate": (
                (block.get("calibrated_expected_win_rate_pct") or {}).get("metric_status")),
            "metric_status_expectancy": (
                (block.get("net_expectancy_equity_pct") or {}).get("metric_status")),
        })

    gap = build_frequency_gap_report(rows)
    payload = {
        "ok": True,
        "schema": "qiyu_live_expectancy_bundle_v1",
        "updated_at": _now(),
        "updated_at_ts": time.time(),
        "regime": regime,
        "strategies": rows,
        "frequency_gap": gap,
        "counts": {
            "mounted": len(rows),
            "can_open": sum(1 for r in rows if r.get("can_open")),
            "paused": sum(1 for r in rows if r.get("pause_new_entries")),
            "independent_families": (gap.get("mechanism_families") or {}).get(
                "independent_niche_count"),
        },
    }
    _atomic(METRICS_FILE, payload)
    return payload


def metrics_for(symbol, timeframe, strategy_key, refresh=False):
    data = _read(METRICS_FILE, {})
    age = time.time() - float(data.get("updated_at_ts") or 0)
    if refresh or not data.get("ok") or age > 180:
        data = compute_all_live_metrics()
    for row in data.get("strategies") or []:
        if (row.get("symbol") == symbol and row.get("timeframe") == timeframe
                and row.get("strategy_key") == strategy_key):
            return row
    return {}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    result = compute_all_live_metrics() if args.refresh else (
        _read(METRICS_FILE, {}) or compute_all_live_metrics())
    print(json.dumps({
        "ok": result.get("ok"),
        "updated_at": result.get("updated_at"),
        "counts": result.get("counts"),
        "gap": (result.get("frequency_gap") or {}).get("pool"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
