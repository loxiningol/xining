# -*- coding: utf-8 -*-
"""Creation-stage return hardness + strategy-degeneration fuses.

Calibrated against live ADA5 seed (2026-07-31 audit):
  - Hard weekly >=8% was revoked: forces overfitting / leverage chasing.
  - Prefer annualized proxy floor + return/MDD + anti-degeneration.
  - If a brief still demands unreachable weekly targets, discovery contract
    should return no credible candidate instead of forcing delivery.
"""
from __future__ import print_function

from datetime import datetime


# --- defaults (overridable via design_doc.constraints) ---
# Weekly hard floor OFF by default (0.0). Brief may still request, but discovery
# treats >=8% weekly as usually infeasible rather than an optimization target.
MIN_WEEKLY_RETURN = 0.0
MIN_RETURN_MDD = 1.0              # total_return / abs(mdd)
MIN_WINDOW_TOTAL_RETURN = 0.01    # absolute window equity return floor
MIN_FACTOR_WEEKLY_LEV = 0.03      # factor LS weekly after leverage + 2x costs
FACTOR_LEV_SCALE = 8.0            # amplify unit-notional factor rets toward "带杠杆"
ROUND_TRIP_COST = 0.001           # 5bp slip + 5bp fee each way ≈ 10bp RT
MIN_EXPOSURE = 0.0                # exposure floor off (ADA5 contradiction)
MIN_AVG_TRADE_BP = 1.0            # average trade net < 1bp → degen
MIN_ANNUAL_ACCEPTABLE = 0.06
EXPECTED_ANNUAL_RANGE = (0.08, 0.20)
MIN_ANNUALIZED_PROXY = 0.06       # total_return * (365/span_days)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def default_return_constraints():
    return {
        "expected_annual_return_range": list(EXPECTED_ANNUAL_RANGE),
        "minimum_acceptable_annual_return": MIN_ANNUAL_ACCEPTABLE,
        "minimum_annualized_return": MIN_ANNUALIZED_PROXY,
        "minimum_weekly_return": MIN_WEEKLY_RETURN,
        "minimum_return_mdd": MIN_RETURN_MDD,
        "minimum_window_total_return": MIN_WINDOW_TOTAL_RETURN,
        "minimum_factor_weekly_lev": MIN_FACTOR_WEEKLY_LEV,
        "factor_lev_scale": FACTOR_LEV_SCALE,
        "minimum_exposure": MIN_EXPOSURE,
        "minimum_avg_trade_bp": MIN_AVG_TRADE_BP,
        "eval_lookback_days": 730,
        "note_zh": (
            "收益硬度（ADA5模板校准）：近2年视界年化代理≥6%、收益/回撤≥1.0；"
            "因子多空周收益(带杠杆)≥3%；退化：单笔<1bp 或 窗内总收益<1% → 换视角。"
            "已撤销：周收益≥8%、暴露≥10%（逼过拟合且与 ADA5顺势回升 矛盾）。"
        ),
    }


def merge_constraints(constraints=None):
    base = default_return_constraints()
    c = dict(constraints or {})
    for k, v in base.items():
        if k not in c or c.get(k) is None:
            c[k] = v
    return c


def span_days_from_ts(first_ts, last_ts):
    if first_ts is None or last_ts is None:
        return None
    a = int(first_ts)
    b = int(last_ts)
    if a > 1e12:
        a //= 1000
    if b > 1e12:
        b //= 1000
    return max(0.0, (b - a) / 86400.0)


def weekly_return_proxy(total_return, span_days):
    """Scale window equity return to a 7-day proxy (not a calendar week slice)."""
    if total_return is None or span_days is None or span_days <= 1e-9:
        return None
    return float(total_return) * (7.0 / float(span_days))


def equity_total_return(returns):
    eq = 1.0
    for r in returns or []:
        if r is None:
            continue
        eq *= (1.0 + float(r))
    return eq - 1.0


def max_drawdown_from_returns(returns):
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in returns or []:
        if r is None:
            continue
        eq *= (1.0 + float(r))
        if eq > peak:
            peak = eq
        dd = eq / peak - 1.0
        if dd < mdd:
            mdd = dd
    return mdd


def factor_ls_weekly_lev(returns, span_days, lev_scale=FACTOR_LEV_SCALE,
                         round_trip_cost=ROUND_TRIP_COST):
    """Long-short weekly return proxy after leverage scale and 2-side costs."""
    rets = [float(x) for x in (returns or []) if x is not None]
    if len(rets) < 5 or not span_days or span_days <= 1e-9:
        return {
            "ok": False,
            "weekly_lev": None,
            "reason": "insufficient",
            "n": len(rets),
        }
    eq = 1.0
    for r in rets:
        net = float(r) - float(round_trip_cost)
        eq *= (1.0 + net * float(lev_scale))
    total = eq - 1.0
    weekly = total * (7.0 / float(span_days))
    return {
        "ok": True,
        "n": len(rets),
        "unit_total_after_cost_lev": total,
        "weekly_lev": weekly,
        "lev_scale": float(lev_scale),
        "round_trip_cost": float(round_trip_cost),
        "span_days": float(span_days),
    }


def filter_factor_by_ls_weekly(candidate, span_days, constraints=None):
    """Drop factors whose levered LS weekly return is below floor."""
    c = merge_constraints(constraints)
    floor = float(c.get("minimum_factor_weekly_lev") or MIN_FACTOR_WEEKLY_LEV)
    lev = float(c.get("factor_lev_scale") or FACTOR_LEV_SCALE)
    pack = factor_ls_weekly_lev(
        candidate.get("returns") or [],
        span_days,
        lev_scale=lev,
        round_trip_cost=ROUND_TRIP_COST,
    )
    row = dict(candidate)
    row["ls_weekly"] = pack
    if not pack.get("ok"):
        row["ls_weekly_reject"] = True
        row["ls_weekly_reason"] = "ls_weekly_insufficient"
        return False, row
    if float(pack["weekly_lev"]) < floor:
        row["ls_weekly_reject"] = True
        row["ls_weekly_reason"] = "ls_weekly_below_floor"
        return False, row
    row["ls_weekly_reject"] = False
    return True, row


def estimate_exposure(n_trades, n_bars, hold_bars=3):
    if not n_bars or n_bars <= 0:
        return None
    return min(1.0, float(n_trades) * float(hold_bars) / float(n_bars))


def detect_degeneration(
    trade_returns,
    span_days=None,
    n_bars=None,
    hold_bars=3,
    total_return=None,
    constraints=None,
):
    """Return degeneration verdict before presenting a strategy."""
    c = merge_constraints(constraints)
    rets = [float(x) for x in (trade_returns or []) if x is not None]
    reasons = []
    if total_return is None:
        total_return = equity_total_return(rets)
    avg_bp = None
    if rets:
        avg_bp = (sum(rets) / float(len(rets))) * 10000.0
    exposure = estimate_exposure(len(rets), n_bars, hold_bars=hold_bars)

    min_exp = float(c.get("minimum_exposure") or MIN_EXPOSURE)
    min_bp = float(c.get("minimum_avg_trade_bp") or MIN_AVG_TRADE_BP)
    min_tot = float(c.get("minimum_window_total_return") or MIN_WINDOW_TOTAL_RETURN)

    if exposure is not None and exposure < min_exp:
        reasons.append("exposure_below_floor")
    if avg_bp is not None and avg_bp < min_bp:
        reasons.append("avg_trade_below_1bp")
    if total_return is not None and float(total_return) < min_tot:
        reasons.append("window_total_return_below_1pct")

    degenerated = bool(reasons)
    return {
        "ok": True,
        "schema": "qiyu_creation_degeneration_v1",
        "degenerated": degenerated,
        "reasons": reasons,
        "metrics": {
            "n_trades": len(rets),
            "n_bars": n_bars,
            "hold_bars": hold_bars,
            "exposure": exposure,
            "avg_trade_bp": avg_bp,
            "total_return": total_return,
            "span_days": span_days,
        },
        "floors": {
            "minimum_exposure": min_exp,
            "minimum_avg_trade_bp": min_bp,
            "minimum_window_total_return": min_tot,
        },
        "human_banner_zh": (
            None if not degenerated else (
                "【策略退化熔断】命中: %s。禁止输出；回溯元思考换微观结构视角。"
                % ",".join(reasons)
            )
        ),
        "at": _now(),
    }


def evaluate_return_hardness(
    trade_returns=None,
    total_return=None,
    max_drawdown=None,
    span_days=None,
    first_ts=None,
    last_ts=None,
    n_bars=None,
    hold_bars=3,
    constraints=None,
):
    """Combined return-hardness + degeneration gate for creation output."""
    c = merge_constraints(constraints)
    rets = [float(x) for x in (trade_returns or []) if x is not None]
    if span_days is None:
        span_days = span_days_from_ts(first_ts, last_ts)
    if total_return is None:
        total_return = equity_total_return(rets)
    if max_drawdown is None:
        max_drawdown = max_drawdown_from_returns(rets)

    weekly = weekly_return_proxy(total_return, span_days)
    annualized = None
    if total_return is not None and span_days and float(span_days) > 1e-9:
        annualized = float(total_return) * (365.0 / float(span_days))
    mdd = float(max_drawdown) if max_drawdown is not None else None
    ret_mdd = None
    if total_return is not None and mdd is not None and abs(mdd) > 1e-12:
        ret_mdd = float(total_return) / abs(mdd)
    elif total_return is not None and mdd is not None and abs(mdd) <= 1e-12:
        ret_mdd = 0.0 if abs(float(total_return)) < 1e-6 else 999.0

    reasons = []
    min_w = float(c["minimum_weekly_return"]) if c.get("minimum_weekly_return") is not None else float(MIN_WEEKLY_RETURN)
    min_rm = float(c.get("minimum_return_mdd") or MIN_RETURN_MDD)
    min_ann = float(
        c.get("minimum_annualized_return")
        or c.get("minimum_acceptable_annual_return")
        or MIN_ANNUALIZED_PROXY
    )

    # Weekly floor only enforced when explicitly > 0 (default off).
    if min_w > 0:
        if weekly is None:
            reasons.append("weekly_return_missing")
        elif weekly < min_w:
            reasons.append("weekly_return_below_floor")

    if annualized is None:
        reasons.append("annualized_return_missing")
    elif annualized < min_ann:
        reasons.append("annualized_return_below_floor")

    if ret_mdd is None:
        reasons.append("return_mdd_missing")
    elif ret_mdd < min_rm:
        reasons.append("return_mdd_below_floor")

    degen = detect_degeneration(
        rets,
        span_days=span_days,
        n_bars=n_bars,
        hold_bars=hold_bars,
        total_return=total_return,
        constraints=c,
    )
    if degen.get("degenerated"):
        reasons.extend(degen.get("reasons") or [])

    seen = set()
    uniq = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq.append(r)

    passed = len(uniq) == 0
    return {
        "ok": True,
        "schema": "qiyu_creation_return_hardness_v1",
        "passed": passed,
        "present_to_human": passed,
        "reject_reasons": uniq,
        "metrics": {
            "total_return": total_return,
            "weekly_return_proxy": weekly,
            "annualized_return_proxy": annualized,
            "max_drawdown": mdd,
            "return_mdd": ret_mdd,
            "span_days": span_days,
            "n_trades": len(rets),
            "n_bars": n_bars,
        },
        "floors": {
            "minimum_weekly_return": min_w,
            "minimum_annualized_return": min_ann,
            "minimum_return_mdd": min_rm,
            "minimum_acceptable_annual_return": c.get("minimum_acceptable_annual_return"),
            "expected_annual_return_range": c.get("expected_annual_return_range"),
        },
        "degeneration": degen,
        "return_scope_zh": (
            "annualized_return_proxy = 窗内权益总收益 × (365 / span_days)；"
            "weekly_return_proxy 仅作诊断，默认不再作硬门槛。"
        ),
        "human_banner_zh": (
            None if passed else (
                "【收益硬度未通过·禁止展示】原因: %s。"
                "年化代理=%s（门槛≥%.0f%%），收益/回撤=%s（门槛≥%.1f）。"
                "若需求本身不可实现，应返回无可信候选，而非逼过拟合。"
                % (
                    ",".join(uniq),
                    ("%.2f%%" % (100 * annualized) if annualized is not None else "缺失"),
                    100 * min_ann,
                    ("%.2f" % ret_mdd if ret_mdd is not None else "缺失"),
                    min_rm,
                )
            )
        ),
        "at": _now(),
    }


def perspective_return_capacity(lens_id, family=None):
    """Heuristic max annual net capacity for divergence lenses."""
    fam = (family or "").lower()
    lid = (lens_id or "").lower()
    if any(k in lid or k in fam for k in (
        "inventory", "mean_reversion", "pairs", "cointegration", "做市", "库存",
    )):
        return 0.05
    if any(k in lid or k in fam for k in ("liquidity", "sweep", "sfp")):
        return 0.10
    if any(k in lid or k in fam for k in (
        "breakout", "trend", "momentum", "vol_squeeze", "mom_vol", "dual_ma", "donchian",
    )):
        return 0.18
    return 0.10


def annotate_perspectives_with_capacity(perspectives, min_annual=MIN_ANNUAL_ACCEPTABLE):
    out = []
    for p in perspectives or []:
        row = dict(p)
        cap = row.get("max_annual_net_estimate")
        if cap is None:
            cap = perspective_return_capacity(row.get("id"), row.get("family"))
        row["max_annual_net_estimate"] = float(cap)
        row["return_capacity_ok"] = float(cap) >= float(min_annual)
        out.append(row)
    return out


def select_perspective_by_return_capacity(perspectives, min_annual=MIN_ANNUAL_ACCEPTABLE,
                                          preferred_id=None):
    annotated = annotate_perspectives_with_capacity(perspectives, min_annual=min_annual)
    ok = [p for p in annotated if p.get("return_capacity_ok")]
    pool = ok or annotated
    if preferred_id:
        for p in pool:
            if p.get("id") == preferred_id and p.get("return_capacity_ok"):
                return p, annotated
    chosen = sorted(
        pool, key=lambda x: float(x.get("max_annual_net_estimate") or 0), reverse=True
    )[0]
    return chosen, annotated
