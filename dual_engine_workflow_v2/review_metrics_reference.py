# -*- coding: utf-8 -*-
"""Independent review-metrics reference calculator (P0 truth oracle).

MUST NOT import production review / manufacture metric helpers.
Used only to verify production metrics for parity before four-AI review.
"""
from __future__ import print_function

import math
from datetime import datetime, timedelta

LEVERAGE = 20.0
STOP_PRICE_DISTANCE = 0.005
PROFIT_FIRST_TARGET = 0.005555
MIN_AVG_WINNING_LEVERED = 0.1111  # ratio scale (11.11%)
WEEKLY_FREQ_SOFT_MIN = 0.5

# Segment weights (plan §5.2)
WEIGHT_0_12M = 1.00
WEIGHT_12_24M = 0.80
WEIGHT_24_48M = 0.30
WEIGHT_GT_48M = 0.10
HALF_LIFE_DAYS = 365.0
MIN_OLD_WEIGHT = 0.05


def _f(x, default=None):
    try:
        if x is None:
            return default
        out = float(x)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except Exception:
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
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(text[:19].replace("T", " "), fmt if " " in fmt or "T" in text else "%Y-%m-%d")
        except Exception:
            continue
    try:
        # epoch seconds / ms
        num = float(text)
        if num > 1e12:
            num = num / 1000.0
        return datetime.utcfromtimestamp(num)
    except Exception:
        return None


def protective_stop_price(entry_price, side="long"):
    entry = _f(entry_price)
    if entry is None or entry <= 0:
        return None
    side = str(side or "long").lower()
    if side in ("short", "sell", "s"):
        return entry * (1.0 + STOP_PRICE_DISTANCE)
    return entry * (1.0 - STOP_PRICE_DISTANCE)


def trade_returns_from_fill(trade, leverage=LEVERAGE):
    """Compute one trade's price/levered returns from fill fields.

    Cost convention (locked for reference):
      net_price_return = gross_price_return - total_cost_return
      net_leveraged_return = net_price_return * leverage

    Costs are expressed as fractions of notional (price layer), then leveraged.
    If the trade already provides pnl_ratio (levered after fees), that is
    recorded as production_provided but reference still recomputes when prices
    exist.
    """
    t = dict(trade or {})
    side = str(t.get("side") or "long").lower()
    entry = _f(t.get("entry_price"))
    exit_ = _f(t.get("exit_price"))
    qty = _f(t.get("quantity"), 1.0) or 1.0
    entry_fee = _f(t.get("entry_fee"), 0.0) or 0.0
    exit_fee = _f(t.get("exit_fee"), 0.0) or 0.0
    funding = _f(t.get("funding"), 0.0) or 0.0
    slip = _f(t.get("slippage_cost"), 0.0) or 0.0
    lev = _f(t.get("leverage"), leverage) or leverage

    out = {
        "side": side,
        "entry_price": entry,
        "exit_price": exit_,
        "quantity": qty,
        "leverage": lev,
        "stop_price": t.get("stop_price") or protective_stop_price(entry, side),
        "exit_reason": t.get("exit_reason"),
    }
    if entry is None or exit_ is None or entry <= 0:
        # Fall back to provided levered pnl_ratio only as last resort.
        pnl = _f(t.get("pnl_ratio_full_size"))
        if pnl is None:
            pnl = _f(t.get("pnl_ratio"))
        if pnl is None:
            pnl = _f(t.get("net_leveraged_return"))
        out.update({
            "gross_price_return": None,
            "gross_leveraged_return": None,
            "total_cost_return": None,
            "net_price_return": None,
            "net_leveraged_return": pnl,
            "is_profitable": bool(pnl is not None and pnl > 0),
            "source": "provided_pnl_ratio_fallback",
        })
        return out

    if side in ("short", "sell", "s"):
        gross_price = (entry - exit_) / entry
    else:
        gross_price = (exit_ - entry) / entry

    # Fees/funding/slippage as fraction of entry notional.
    notional = abs(entry * qty)
    total_cost_abs = entry_fee + exit_fee + funding + slip
    total_cost_return = (total_cost_abs / notional) if notional > 0 else 0.0
    net_price = gross_price - total_cost_return
    net_lev = net_price * float(lev)
    out.update({
        "gross_price_return": gross_price,
        "gross_leveraged_return": gross_price * float(lev),
        "total_cost_return": total_cost_return,
        "net_price_return": net_price,
        "net_leveraged_return": net_lev,
        "is_profitable": bool(net_lev > 0),
        "cost_convention": (
            "net_price_return = gross_price_return - cost/notional; "
            "net_leveraged_return = net_price_return * leverage"
        ),
        "source": "price_fill_recompute",
    })
    return out


def weekly_entry_frequency(n_entries, observation_days):
    """WeeklyFrequency = N_entries / (ObservationDays / 7).

    ObservationDays MUST be the evaluated data span, not active-trade span.
    """
    n = int(n_entries or 0)
    days = _f(observation_days)
    if days is None or days <= 0:
        return {
            "weekly_entry_frequency": None,
            "n_entries": n,
            "observation_days": days,
            "formula": "N_entries / (ObservationDays / 7)",
            "error": "invalid_observation_days",
        }
    freq = n / (float(days) / 7.0)
    return {
        "weekly_entry_frequency": freq,
        "n_entries": n,
        "observation_days": float(days),
        "observation_weeks": float(days) / 7.0,
        "formula": "N_entries / (ObservationDays / 7)",
    }


def average_profitable_trade_return(net_leveraged_returns):
    """AvgWinningReturn_20x = mean(net_leveraged_return | return > 0).

    Losing trades never enter the denominator.
    Zero is NOT profitable.
    """
    vals = []
    for x in net_leveraged_returns or []:
        v = _f(x)
        if v is not None:
            vals.append(v)
    wins = [v for v in vals if v > 0]
    if not wins:
        return {
            "n_trades": len(vals),
            "n_winning": 0,
            "average_profitable_trade_return": None,
            "average_profitable_trade_return_pct": None,
            "passes_1111": False,
        }
    mean_ratio = sum(wins) / float(len(wins))
    return {
        "n_trades": len(vals),
        "n_winning": len(wins),
        "average_profitable_trade_return": mean_ratio,
        "average_profitable_trade_return_pct": mean_ratio * 100.0,
        "passes_1111": bool(mean_ratio >= MIN_AVG_WINNING_LEVERED),
        "formula": (
            "mean(NetReturn_i * leverage for i in winning); "
            "losing trades excluded from denominator"
        ),
    }


def age_weight(age_days, half_life_days=HALF_LIFE_DAYS, minimum=MIN_OLD_WEIGHT):
    days = _f(age_days, 0.0) or 0.0
    hl = max(1.0, float(half_life_days or HALF_LIFE_DAYS))
    w = math.pow(2.0, -float(days) / hl)
    return max(float(minimum), w)


def segment_weight(age_days):
    days = _f(age_days, 0.0) or 0.0
    if days <= 365:
        return WEIGHT_0_12M
    if days <= 730:
        return WEIGHT_12_24M
    if days <= 1460:
        return WEIGHT_24_48M
    return WEIGHT_GT_48M


def compute_trade_ledger_metrics(
    trades,
    observation_start=None,
    observation_end=None,
    as_of=None,
    leverage=LEVERAGE,
):
    """Full reference metrics from a trade ledger."""
    as_of_dt = _parse_ts(as_of) or datetime.utcnow()
    rows = []
    for raw in trades or []:
        if not isinstance(raw, dict):
            continue
        computed = trade_returns_from_fill(raw, leverage=leverage)
        entry_ts = _parse_ts(raw.get("entry_time") or raw.get("entry_ts"))
        age_days = None
        if entry_ts is not None:
            age_days = max(0.0, (as_of_dt - entry_ts).total_seconds() / 86400.0)
        computed["entry_time"] = raw.get("entry_time") or raw.get("entry_ts")
        computed["exit_time"] = raw.get("exit_time") or raw.get("exit_ts")
        computed["age_days"] = age_days
        computed["segment_weight"] = segment_weight(age_days) if age_days is not None else None
        computed["decay_weight"] = (
            age_weight(age_days) if age_days is not None else None
        )
        # Optional path fields
        computed["mae"] = _f(raw.get("mae") or raw.get("mae_pct") or raw.get("median_mae_pct"))
        computed["mfe"] = _f(raw.get("mfe") or raw.get("mfe_pct"))
        computed["profit_first"] = raw.get("profit_first")
        if computed.get("profit_first") is None and computed.get("mfe") is not None:
            # Approximate: hit +0.5555% before -0.5% if mfe>=target and path flag absent
            computed["profit_first"] = bool(
                computed["mfe"] >= PROFIT_FIRST_TARGET
            )
        rows.append(computed)

    nets = [r.get("net_leveraged_return") for r in rows]
    nets_ok = [v for v in (_f(x) for x in nets) if v is not None]
    wins = [v for v in nets_ok if v > 0]
    losses = [v for v in nets_ok if v <= 0]
    win_rate = (len(wins) / float(len(nets_ok))) if nets_ok else None
    avg_win = average_profitable_trade_return(nets_ok)

    # Observation span: prefer explicit window, else fail closed (do not use
    # first-trade→last-trade active span as a silent substitute).
    obs_start = _parse_ts(observation_start)
    obs_end = _parse_ts(observation_end)
    observation_days = None
    if obs_start and obs_end and obs_end >= obs_start:
        observation_days = (obs_end - obs_start).total_seconds() / 86400.0
    weekly = weekly_entry_frequency(len(rows), observation_days)

    pfr_flags = [bool(r.get("profit_first")) for r in rows if r.get("profit_first") is not None]
    profit_first_rate = (
        (sum(1 for x in pfr_flags if x) / float(len(pfr_flags))) if pfr_flags else None
    )
    maes = [abs(v) for v in (_f(r.get("mae")) for r in rows) if v is not None]
    median_mae = None
    if maes:
        maes_sorted = sorted(maes)
        mid = len(maes_sorted) // 2
        if len(maes_sorted) % 2:
            median_mae = maes_sorted[mid]
        else:
            median_mae = 0.5 * (maes_sorted[mid - 1] + maes_sorted[mid])

    # Recent 2y vs older split
    recent = [r for r in rows if r.get("age_days") is not None and r["age_days"] <= 730]
    older = [r for r in rows if r.get("age_days") is not None and r["age_days"] > 730]
    recent_nets = [_f(r.get("net_leveraged_return")) for r in recent]
    recent_nets = [v for v in recent_nets if v is not None]
    older_nets = [_f(r.get("net_leveraged_return")) for r in older]
    older_nets = [v for v in older_nets if v is not None]
    recent_2y = average_profitable_trade_return(recent_nets)
    older_metrics = average_profitable_trade_return(older_nets)

    # Weighted average of winning returns using segment weights
    weighted_wins = []
    weighted_w = []
    for r in rows:
        v = _f(r.get("net_leveraged_return"))
        w = _f(r.get("segment_weight"))
        if v is not None and v > 0 and w is not None:
            weighted_wins.append(v * w)
            weighted_w.append(w)
    weighted_avg = None
    if weighted_w:
        weighted_avg = sum(weighted_wins) / float(sum(weighted_w))

    recent_pass = bool(
        recent_2y.get("average_profitable_trade_return") is not None
        and recent_2y["average_profitable_trade_return"] >= MIN_AVG_WINNING_LEVERED
    )
    # Recent-2y domination rule: older cannot rescue recent failure.
    recent_dominated_decision = "pass" if recent_pass else "reject_recent_2y_failure"

    return {
        "trade_count": len(rows),
        "win_rate": win_rate,
        "n_winning": len(wins),
        "n_losing": len(losses),
        "average_profitable_trade_return": avg_win.get("average_profitable_trade_return"),
        "average_profitable_trade_return_pct": avg_win.get(
            "average_profitable_trade_return_pct"
        ),
        "passes_1111": avg_win.get("passes_1111"),
        "weekly_entry_frequency": weekly.get("weekly_entry_frequency"),
        "observation_days": observation_days,
        "weekly_pack": weekly,
        "profit_first_rate": profit_first_rate,
        "median_mae": median_mae,
        "leverage": leverage,
        "stop_distance": STOP_PRICE_DISTANCE,
        "recent_2y_metrics": recent_2y,
        "older_metrics": older_metrics,
        "weighted_average_profitable_trade_return": weighted_avg,
        "recent_2y_requirement_passed": recent_pass,
        "recent_dominated_decision": recent_dominated_decision,
        "score_blend_note": "0.70*Score_recent2y + 0.30*Score_older (informational); hard gate = recent2y",
        "trades": rows,
        "cost_convention_zh": (
            "费用在标的收益层扣减后乘杠杆："
            "net_leveraged = (gross_price - cost/notional) * leverage"
        ),
    }


def parity_compare(production_metrics, reference_metrics, tol=1e-9):
    """Compare production vs reference; fail closed on material diffs."""
    prod = production_metrics or {}
    ref = reference_metrics or {}
    keys = (
        ("win_rate", "win_rate"),
        ("average_profitable_trade_return", "average_profitable_trade_return"),
        ("mean_win_only_ratio", "average_profitable_trade_return"),
        ("weekly_entry_frequency", "weekly_entry_frequency"),
        ("weekly_opens", "weekly_entry_frequency"),
        ("profit_first_rate", "profit_first_rate"),
        ("median_mae", "median_mae"),
        ("median_mae_pct", "median_mae"),
        ("trade_count", "trade_count"),
        ("n", "trade_count"),
    )
    absolute_diff = {}
    relative_diff = {}
    checked = []
    failed = []
    for prod_key, ref_key in keys:
        if prod_key not in prod and ref_key not in ref:
            continue
        pv = _f(prod.get(prod_key))
        rv = _f(ref.get(ref_key))
        # Normalize pct-point mistakes on win-only mean
        if prod_key in ("mean_win_only_pct",) and pv is not None and abs(pv) >= 0.5:
            pv = pv / 100.0
        if pv is None and rv is None:
            continue
        if prod_key not in prod:
            continue
        checked.append(prod_key)
        if pv is None or rv is None:
            failed.append(prod_key)
            absolute_diff[prod_key] = None
            relative_diff[prod_key] = None
            continue
        ad = abs(pv - rv)
        absolute_diff[prod_key] = ad
        relative_diff[prod_key] = (ad / abs(rv)) if rv != 0 else ad
        count_keys = ("trade_count", "n")
        limit = 0.0 if prod_key in count_keys else float(tol)
        if ad > limit + 1e-15:
            failed.append(prod_key)
    passed = not failed and bool(checked)
    return {
        "production_metrics": prod,
        "reference_metrics": {
            k: ref.get(k)
            for k in (
                "win_rate",
                "average_profitable_trade_return",
                "weekly_entry_frequency",
                "profit_first_rate",
                "median_mae",
                "trade_count",
                "recent_2y_requirement_passed",
            )
        },
        "absolute_diff": absolute_diff,
        "relative_diff": relative_diff,
        "checked_keys": checked,
        "failed_keys": failed,
        "metric_parity_passed": bool(passed),
        "error": None if passed else "REVIEW_METRIC_PARITY_FAILURE",
    }


def probe():
    demo = average_profitable_trade_return([0.12, 0.14, -0.10, -0.10])
    wk = weekly_entry_frequency(20, 140)
    return {
        "ok": True,
        "leverage": LEVERAGE,
        "stop": STOP_PRICE_DISTANCE,
        "demo_avg_win": demo.get("average_profitable_trade_return"),
        "demo_weekly": wk.get("weekly_entry_frequency"),
    }
