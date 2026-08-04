# -*- coding: utf-8 -*-
"""创造管道第二步 · 多目标适应度（门槛0–7 中的适应度逻辑）。

与胜率门、去最大盈利、漏斗 L0–L3、寒霜贰筛同属「第二步：统一门槛」。

硬条件（enforce=True 时全部必须通过）：
  - 卡尔玛：大样本 ≥1.5；小样本（成交<15）≥1.0（见质量教义）
  - 盈亏比：大样本 ≥2.5；小样本 ≥1.8
  - 期望因子：胜率 × 盈亏比 ≥ 1.0（始终硬）
  - 最差 5 笔亏损占比（大样本且亏损笔数>5 时）≤ 总亏损绝对和的 40%
  - 最大不利偏移：任一手 MAE > 2.0 × 平均盈利 → 拒（死扛回本）
  - 去最大盈利：去掉最大盈利后，若卡尔玛或夏普相对跌超 50% → 拒（彩票过拟合）

年化口径见 compute 内注释。保护止损/杠杆/仓位不在本模块范围。
"""
from __future__ import print_function

import math
from datetime import datetime

from .creation_quality_doctrine import (
    GATE2_CALMAR_LARGE,
    GATE2_EXPECTANCY_FACTOR_MIN,
    GATE2_PAYOFF_LARGE,
    MIN_TRADES_CREATION,
    gate2_floors,
)


# ---- 默认大样本地板（兼容旧引用；实际判定走 gate2_floors）----
CALMAR_MIN = GATE2_CALMAR_LARGE
PAYOFF_MIN = GATE2_PAYOFF_LARGE
EXPECTANCY_FACTOR_MIN = GATE2_EXPECTANCY_FACTOR_MIN  # 胜率 × 盈亏比
WORST5_LOSS_SHARE_MAX = 0.40
MAE_VS_AVG_WIN_MAX = 2.0
REMOVE_MAX_WIN_DROP_MAX = 0.50  # 相对跌幅 50%


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


def remove_max_win_stress(pnls, trades, calmar_min=None):
    """去最大盈利压力：去掉最大盈利单，量卡尔玛/夏普相对跌幅。

    最大回撤近 0 时卡尔玛数值不稳定；该情形下要求缩样后仍过卡尔玛地板，
    或以夏普跌幅为准。
    """
    if calmar_min is None:
        calmar_min = CALMAR_MIN
    calmar_min = float(calmar_min)
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
        # 卡尔玛相对跌幅不稳定：缩样丢地板或夏普崩 >50% 才判失败
        calmar_fail = red_calmar < calmar_min and base_calmar >= calmar_min
        sharpe_fail = s_drop > REMOVE_MAX_WIN_DROP_MAX
        failed = calmar_fail or sharpe_fail
        reason = "去最大盈利后崩溃" if failed else "ok_near_zero_dd_mode"
    else:
        failed = (c_drop > REMOVE_MAX_WIN_DROP_MAX) or (s_drop > REMOVE_MAX_WIN_DROP_MAX)
        reason = "去最大盈利后崩溃" if failed else "ok"

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
    floors = gate2_floors(stats["n"])
    calmar_min = float(floors["calmar_min"])
    payoff_min = float(floors["payoff_min"])
    calmar, calmar_meta = _calmar(pnls, trades)
    sharpe = _sharpe(pnls)
    w5_share, w5_meta = worst5_loss_share(pnls)
    mae_flags, mae_meta = mae_dead_hold_flags(trades, stats["avg_win"])
    lottery = remove_max_win_stress(pnls, trades, calmar_min=calmar_min)

    calmar_ok = calmar >= calmar_min
    payoff_ok = stats["payoff_ratio"] >= payoff_min
    n_losses = sum(1 for p in pnls if p < 0)
    # 小样本或亏损笔数≤5 时，「最差五笔占比」恒接近 100%，无信息量，跳过硬拦
    worst5_enforce = (not floors.get("small_sample")) and n_losses > 5
    worst5_ok = True if not worst5_enforce else (w5_share <= WORST5_LOSS_SHARE_MAX)
    checks = {
        "成交笔数达标": stats["n"] >= int(MIN_TRADES_CREATION),
        "门槛2_卡尔玛达标": calmar_ok,
        "门槛2_盈亏比达标": payoff_ok,
        "期望因子达标": stats["expectancy_factor_wr_x_payoff"] >= EXPECTANCY_FACTOR_MIN,
        "最差五笔亏损不过度集中": worst5_ok,
        "最大不利偏移可接受": len(mae_flags) == 0,
        "去最大盈利后不崩": bool(lottery.get("pass")),
        # 兼容旧键名
        "sample_size_ge_8": stats["n"] >= int(MIN_TRADES_CREATION),
        "calmar_ge_1_5": calmar_ok,
        "payoff_ge_2_5": payoff_ok,
        "expectancy_factor_ge_1_0": stats["expectancy_factor_wr_x_payoff"] >= EXPECTANCY_FACTOR_MIN,
        "worst5_loss_share_le_40pct": worst5_ok,
        "mae_dead_hold_clear": len(mae_flags) == 0,
        "remove_max_win_stable": bool(lottery.get("pass")),
    }
    if not worst5_enforce:
        w5_meta = dict(w5_meta or {})
        w5_meta["note"] = "小样本或亏损笔数≤5，跳过最差五笔集中度硬拦"
        w5_meta["enforced"] = False
    # 无 MAE 字段时不自动失败（研究夹具）；正式回测若输出 mae 则强制
    has_mae = any(
        isinstance(t, dict) and (
            t.get("mae_price_pct") is not None or t.get("mae") is not None
        )
        for t in trades
    )
    if not has_mae:
        checks["最大不利偏移可接受"] = True
        checks["mae_dead_hold_clear"] = True
        mae_meta["note"] = "无MAE字段，跳过强制"

    return {
        "n": stats["n"],
        "成交笔数": stats["n"],
        "win_rate": stats["win_rate"],
        "胜率": stats["win_rate"],
        "win_rate_pct": stats["win_rate_pct"],
        "payoff_ratio": stats["payoff_ratio"],
        "盈亏比": stats["payoff_ratio"],
        "avg_win": stats["avg_win"],
        "avg_loss": stats["avg_loss"],
        "expectancy_factor": stats["expectancy_factor_wr_x_payoff"],
        "期望因子": stats["expectancy_factor_wr_x_payoff"],
        "classic_expectancy": stats["classic_expectancy_mean_pnl"],
        "calmar": round(calmar, 6),
        "卡尔玛": round(calmar, 6),
        "sharpe": round(sharpe, 6),
        "calmar_meta": calmar_meta,
        "worst5_loss_share": round(w5_share, 6),
        "worst5_meta": w5_meta,
        "mae_flags": mae_flags,
        "mae_meta": mae_meta,
        "remove_max_win": lottery,
        "checks": checks,
        "门槛2": floors,
        "thresholds": {
            "calmar_min": calmar_min,
            "payoff_min": payoff_min,
            "expectancy_factor_min": EXPECTANCY_FACTOR_MIN,
            "worst5_loss_share_max": WORST5_LOSS_SHARE_MAX,
            "mae_vs_avg_win_max": MAE_VS_AVG_WIN_MAX,
            "remove_max_win_drop_max": REMOVE_MAX_WIN_DROP_MAX,
            "label_zh": floors.get("label_zh"),
        },
        "formulas": {
            "expectancy_factor": "胜率 × 盈亏比 ≥ 1.0",
            "classic_expectancy": stats["formula_classic_expectancy"],
            "payoff": "平均盈利 / 平均亏损幅度",
            "calmar": calmar_meta.get("annualization"),
        },
    }


def evaluate_multi_objective(trades, base_metrics=None, enforce=True, min_trades=None):
    """返回 {pass, metrics, failed_checks, verdict_tags}。

    enforce=True 时套用门槛2 全部硬条件。
    """
    if min_trades is None:
        min_trades = MIN_TRADES_CREATION
    metrics = compute_fitness_metrics(trades, base_metrics=base_metrics)
    checks = dict(metrics["checks"])
    if min_trades and metrics["n"] < int(min_trades):
        checks["成交笔数达标"] = False
        checks["sample_size_ge_8"] = False

    # 失败原因优先用中文键
    zh_keys = (
        "成交笔数达标", "门槛2_卡尔玛达标", "门槛2_盈亏比达标", "期望因子达标",
        "最差五笔亏损不过度集中", "最大不利偏移可接受", "去最大盈利后不崩",
    )
    failed = [k for k in zh_keys if not checks.get(k)]
    tags = []
    if not checks.get("门槛2_盈亏比达标") or not checks.get("期望因子达标"):
        tags.append("高胜率低盈亏比伪优势")
    if not checks.get("最大不利偏移可接受"):
        tags.append("死扛回本")
    if not checks.get("去最大盈利后不崩"):
        tags.append("彩票过拟合")
    if not checks.get("门槛2_卡尔玛达标"):
        tags.append("卡尔玛低于门槛2地板")
    if not checks.get("最差五笔亏损不过度集中"):
        tags.append("亏损过度集中")

    passed = (len(failed) == 0) if enforce else True
    return {
        "pass": bool(passed),
        "enforce": bool(enforce),
        "metrics": metrics,
        "failed_checks": failed,
        "verdict_tags": tags,
        "checks": checks,
        "门槛名": "门槛2",
    }


def evaluate_pregate_fitness(trades, base_metrics=None):
    """正式预检适应度钩子 — 与门槛2 同硬条件。"""
    return evaluate_multi_objective(
        trades, base_metrics=base_metrics, enforce=True,
        min_trades=MIN_TRADES_CREATION,
    )
