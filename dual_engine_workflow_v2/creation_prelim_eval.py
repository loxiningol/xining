# -*- coding: utf-8 -*-
"""创造初评门禁（对人展示前）。

硬规则：初评胜率未严格大于 50% 时，禁止把策略展示为交付物。
系统必须换方向或套用经典策略变式。

同时强制标注回测日历窗口，避免「总收益」口径歧义。
"""
from __future__ import print_function

from datetime import datetime

from .creation_quality_doctrine import (
    MIN_TRADES_CREATION,
    MIN_WIN_RATE_EXCLUSIVE,
)


MIN_WIN_RATE = MIN_WIN_RATE_EXCLUSIVE  # 胜率硬底（严格大于）


def _ts_to_iso(ts):
    if ts is None:
        return None
    t = int(ts)
    if t > 1e12:
        t //= 1000
    return datetime.utcfromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S UTC")


def label_window(first_ts, last_ts, n_bars=None, timeframe=None):
    """Human-readable window — required on every prelim / backtest summary."""
    first_iso = _ts_to_iso(first_ts)
    last_iso = _ts_to_iso(last_ts)
    span_days = None
    if first_ts is not None and last_ts is not None:
        a = int(first_ts)
        b = int(last_ts)
        if a > 1e12:
            a //= 1000
        if b > 1e12:
            b //= 1000
        span_days = max(0.0, (b - a) / 86400.0)
    return {
        "first_ts": first_ts,
        "last_ts": last_ts,
        "first_iso": first_iso,
        "last_iso": last_iso,
        "span_days": span_days,
        "n_bars": n_bars,
        "timeframe": timeframe,
        "label_zh": (
            "回测窗口：%s → %s（共 %.2f 天%s）"
            % (
                first_iso or "?",
                last_iso or "?",
                span_days if span_days is not None else -1,
                ("，%s 根 %s" % (n_bars, timeframe)) if n_bars and timeframe else "",
            )
        ),
        "return_scope_zh": (
            "下列「总收益」仅为该日历窗口的累计权益变化，不是单日收益，也不是年化收益。"
        ),
    }


# Classic templates to try when current direction fails WR gate
CLASSIC_VARIANTS = (
    {
        "id": "classic_donchian_breakout",
        "family": "trend_pullback",
        "lens_zh": "经典 Donchian 通道突破变式",
        "thesis_zh": "价格突破 N 期高低点确认趋势；EMA 过滤逆势单",
        "factor_hints": ["dist_roll_high", "dist_roll_low", "ret_12", "atr_pct_14"],
    },
    {
        "id": "classic_dual_ma_trend",
        "family": "trend_pullback",
        "lens_zh": "经典双均线趋势变式",
        "thesis_zh": "快慢均线排列 + 波动扩张确认，避免无波动横盘刷单",
        "factor_hints": ["close_z_20", "ret_12", "atr_pct_14", "range_pct"],
    },
    {
        "id": "classic_bollinger_mean_reversion",
        "family": "mean_reversion",
        "lens_zh": "经典布林带均值回归变式",
        "thesis_zh": "价格触及带宽外延后回归中轨；高波环境收紧或不做",
        "factor_hints": ["close_z_20", "range_pct", "lower_wick_pct", "upper_wick_pct"],
    },
    {
        "id": "classic_rsi_reversion",
        "family": "mean_reversion",
        "lens_zh": "经典 RSI 极值回归变式",
        "thesis_zh": "短动量过热/过冷后的回归；配合 EMA 大方向过滤",
        "factor_hints": ["ret_3", "ret_12", "close_z_20", "abs_ret_1"],
    },
    {
        "id": "classic_vol_squeeze_breakout",
        "family": "vol_squeeze_break",
        "lens_zh": "经典波动压缩突破变式",
        "thesis_zh": "低波收口后的方向选择；高波分位禁止追单",
        "factor_hints": ["range_pct", "atr_pct_14", "ret_12", "dist_roll_high"],
    },
)


def prelim_eval(stats, first_ts=None, last_ts=None, n_bars=None, timeframe=None,
                min_win_rate=MIN_WIN_RATE, min_trades=MIN_TRADES_CREATION):
    """创造输出是否可对人展示。

    stats 期望字段：胜率 win_rate、成交笔数 n_trades|n、总收益、最大回撤等。
    胜率硬底为严格大于 min_win_rate（默认 50%）。
    """
    stats = stats or {}
    wr = stats.get("win_rate")
    n = stats.get("n_trades")
    if n is None:
        n = stats.get("n")
    try:
        wr_f = float(wr) if wr is not None else None
    except Exception:
        wr_f = None
    try:
        n_i = int(n) if n is not None else 0
    except Exception:
        n_i = 0

    window = label_window(first_ts, last_ts, n_bars=n_bars, timeframe=timeframe)
    reasons = []
    presentable = True
    if wr_f is None:
        presentable = False
        reasons.append("win_rate_missing")
    elif wr_f <= float(min_win_rate):
        presentable = False
        reasons.append("win_rate_not_above_50pct")
    if n_i < int(min_trades):
        presentable = False
        reasons.append("insufficient_trades")

    verdict = {
        "ok": True,
        "schema": "qiyu_creation_prelim_eval_v1",
        "present_to_human": presentable,
        "min_win_rate": float(min_win_rate),
        "min_win_rate_rule_zh": "胜率必须严格大于 50%",
        "win_rate": wr_f,
        "胜率": wr_f,
        "n_trades": n_i,
        "成交笔数": n_i,
        "total_return": stats.get("total_return"),
        "max_drawdown": stats.get("max_drawdown"),
        "sharpe_ann_proxy": stats.get("sharpe_ann_proxy") or stats.get("sharpe"),
        "window": window,
        "reject_reasons": reasons,
        "human_banner_zh": (
            None if presentable else (
                "【初评未通过·禁止展示为交付】胜率 %s 未严格大于 50%%（或成交笔数不足）。"
                "系统将自动换方向 / 套用经典策略变式，不把垃圾包推给人看。"
                % ("%.1f%%" % (100 * wr_f) if wr_f is not None else "缺失")
            )
        ),
        "metrics_note_zh": window["return_scope_zh"] + " " + window["label_zh"],
    }
    return verdict


def next_classic_variant(tried_ids=None):
    tried = set(tried_ids or [])
    for v in CLASSIC_VARIANTS:
        if v["id"] not in tried:
            return dict(v)
    return None


def apply_variant_to_design(design_doc, variant):
    """Mutate design_doc toward a classic variant (in-place copy returned)."""
    design = dict(design_doc or {})
    if not variant:
        return design
    design["mechanism_family"] = variant.get("family") or design.get("mechanism_family")
    design["core_logic_zh"] = variant.get("thesis_zh") or design.get("core_logic_zh")
    design["hypotheses"] = [
        {
            "id": "H1_classic",
            "statement_zh": variant.get("thesis_zh"),
            "testable_factor_hints": list(variant.get("factor_hints") or []),
        },
        {
            "id": "H2_wr_gate",
            "statement_zh": "初评胜率必须严格大于 50%，否则不得对人展示",
            "testable_factor_hints": list(variant.get("factor_hints") or [])[:3],
        },
    ]
    design["classic_variant"] = {
        "id": variant.get("id"),
        "lens_zh": variant.get("lens_zh"),
    }
    div = dict(design.get("divergence") or {})
    div["selected_id"] = variant.get("id")
    div["selected_lens_zh"] = variant.get("lens_zh")
    div["selection_reason_zh"] = "初评胜率门禁触发：切换经典策略变式"
    design["divergence"] = div
    return design
