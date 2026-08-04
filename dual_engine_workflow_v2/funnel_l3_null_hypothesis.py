# -*- coding: utf-8 -*-
"""创造管道第二步 · 漏斗 L3 — 空假设反过拟合 + 前向稳健。

与胜率门、去最大盈利、门槛0–7、寒霜贰筛同属「第二步：统一门槛」。

检验：
  1) 打乱收益：若打乱序列上夏普仍 ≥0.5 → 拒（巧合拟合）
  2) 镜像市场：价格关于首收盘镜像后仍有伪优势 → 拒
  3) 前向稳健：
     - 大样本（成交≥20）：10 窗至少 7 窗，单窗卡尔玛≥1.0 且经典期望>0
     - 小样本（成交<20）：可用折上比例门（≥0.6 或正折≥5），单窗只需经典期望>0

镜像拒则：成交≥5 且夏普≥0.5 且经典期望>0。
"""
from __future__ import print_function

import math
import random

from .creation_quality_doctrine import (
    SMALL_N_TRADE_BOUNDARY,
    WF_LARGE_NEED_PASS,
    WF_LARGE_NEED_TOTAL,
    wf_pass_for_sample,
)
from .fitness_engine import (
    _calmar,
    _pnls,
    _sharpe,
    payoff_stats,
)
from .step_a_config import WF_WINDOW_PASS_REQUIREMENT


STAGE = "funnel_l3_null_hypothesis"
PERM_SHARPE_REJECT_GE = 0.5
INVERT_SHARPE_REJECT_GE = 0.5
INVERT_MIN_TRADES = 5
WF_CALMAR_MIN = 1.0
WF_FOLDS_DEFAULT = 10


def _col_list(frame, name):
    """Extract column as float list — works with pandas or MiniFrame."""
    if frame is None or name not in getattr(frame, "columns", []):
        return None
    col = frame[name]
    if hasattr(col, "tolist"):
        return [float(x) for x in col.tolist()]
    if hasattr(col, "iloc"):
        return [float(col.iloc[i]) for i in range(len(col))]
    return [float(x) for x in list(col)]


def _set_col(frame, name, values):
    """Assign column values; prefer pandas path, else MiniFrame._data."""
    try:
        frame[name] = values
        return frame
    except Exception:
        pass
    if hasattr(frame, "_data"):
        frame._data[name] = list(values)
    return frame


def permute_returns_frame(frame, seed=42):
    """Shuffle close log-returns; rebuild OHLC preserving bar ranges.

    Fail-closed: if frame lacks close, returns None.
    Works with pandas DataFrame or list-backed MiniFrame (no pandas required).
    """
    close = _col_list(frame, "close")
    if close is None or len(close) < 3:
        return None
    out = frame.copy()
    if any(c <= 0 for c in close):
        floor = min(close)
        if floor <= 0:
            close = [c - floor + 1.0 for c in close]
    log_rets = []
    for i in range(1, len(close)):
        log_rets.append(math.log(close[i] / close[i - 1]) if close[i - 1] > 0 else 0.0)
    rng = random.Random(seed)
    rng.shuffle(log_rets)
    new_close = [close[0]]
    for r in log_rets:
        new_close.append(new_close[-1] * math.exp(r))
    opens = _col_list(out, "open")
    highs = _col_list(out, "high")
    lows = _col_list(out, "low")
    new_open, new_high, new_low = [], [], []
    for i, nc in enumerate(new_close):
        oc = close[i] if close[i] else nc
        scale = nc / oc if oc else 1.0
        if opens is not None:
            oo = float(opens[i])
            new_open.append(oo * scale)
        if highs is not None and lows is not None:
            hh = float(highs[i])
            ll = float(lows[i])
            nh, nl = hh * scale, ll * scale
            if nh < nl:
                nh, nl = nl, nh
            nl = min(nl, nc)
            nh = max(nh, nc)
            if new_open:
                nl = min(nl, new_open[-1])
                nh = max(nh, new_open[-1])
            new_high.append(nh)
            new_low.append(nl)
    _set_col(out, "close", new_close)
    if new_open:
        _set_col(out, "open", new_open)
    if new_high:
        _set_col(out, "high", new_high)
        _set_col(out, "low", new_low)
    return out


def invert_price_frame(frame):
    """Reflect OHLC about the first close; swap high/low if needed."""
    close = _col_list(frame, "close")
    if close is None or len(close) < 2:
        return None
    out = frame.copy()
    pivot = float(close[0])
    for col in ("open", "high", "low", "close"):
        vals = _col_list(out, col)
        if vals is None:
            continue
        _set_col(out, col, [(2.0 * pivot) - float(v) for v in vals])
    highs = _col_list(out, "high")
    lows = _col_list(out, "low")
    if highs is not None and lows is not None:
        hi = [max(h, l) for h, l in zip(highs, lows)]
        lo = [min(h, l) for h, l in zip(highs, lows)]
        _set_col(out, "high", hi)
        _set_col(out, "low", lo)
        lows = lo
    closes = _col_list(out, "close")
    min_px = min(closes)
    if lows is not None:
        min_px = min(min_px, min(lows))
    if min_px <= 0:
        shift = abs(min_px) + 1.0
        for col in ("open", "high", "low", "close"):
            vals = _col_list(out, col)
            if vals is None:
                continue
            _set_col(out, col, [float(v) + shift for v in vals])
    return out


def _bt_trades(backtest_fn, frame, definition):
    if backtest_fn is None or frame is None:
        return [], "no_backtest_fn_or_frame"
    try:
        result = backtest_fn(frame, definition)
        return list((result or {}).get("trades") or []), None
    except Exception as exc:
        return [], str(exc)


def run_permuted_returns_test(definition, frame, backtest_fn, seed=42,
                              sharpe_reject_ge=PERM_SHARPE_REJECT_GE):
    """Reject if Sharpe on permuted prices ≥ threshold (coincidence fit)."""
    perm = permute_returns_frame(frame, seed=seed)
    if perm is None:
        return {
            "pass": False,
            "test": "permuted_returns",
            "reject_reasons": ["permute_frame_unavailable"],
            "fail_closed": True,
        }
    trades, err = _bt_trades(backtest_fn, perm, definition)
    if err:
        return {
            "pass": False,
            "test": "permuted_returns",
            "reject_reasons": ["permuted_backtest_error:%s" % err],
            "fail_closed": True,
        }
    pnls = _pnls(trades)
    sharpe = _sharpe(pnls)
    reject = sharpe >= float(sharpe_reject_ge)
    return {
        "pass": not reject,
        "test": "permuted_returns",
        "reject_reasons": ["permuted_sharpe_ge_%s" % sharpe_reject_ge] if reject else [],
        "metrics": {
            "n_trades": len(pnls),
            "sharpe": round(float(sharpe), 6),
            "mean_pnl": (sum(pnls) / float(len(pnls))) if pnls else 0.0,
        },
        "threshold_sharpe_reject_ge": float(sharpe_reject_ge),
        "seed": seed,
        "rule": "if Sharpe(permuted_returns_BT) >= 0.5 → reject coincidence fit",
        "fail_closed": True,
    }


def run_inverted_market_test(definition, frame, backtest_fn,
                             sharpe_reject_ge=INVERT_SHARPE_REJECT_GE,
                             min_trades=INVERT_MIN_TRADES):
    """Reject spurious signal that survives price inversion."""
    inv = invert_price_frame(frame)
    if inv is None:
        return {
            "pass": False,
            "test": "inverted_market",
            "reject_reasons": ["invert_frame_unavailable"],
            "fail_closed": True,
        }
    trades, err = _bt_trades(backtest_fn, inv, definition)
    if err:
        return {
            "pass": False,
            "test": "inverted_market",
            "reject_reasons": ["inverted_backtest_error:%s" % err],
            "fail_closed": True,
        }
    pnls = _pnls(trades)
    stats = payoff_stats(pnls)
    sharpe = _sharpe(pnls)
    n = len(pnls)
    expect = float(stats["classic_expectancy_mean_pnl"])
    # Exact reject rule (see module docstring)
    reject = (
        n >= int(min_trades)
        and sharpe >= float(sharpe_reject_ge)
        and expect > 0.0
    )
    return {
        "pass": not reject,
        "test": "inverted_market",
        "reject_reasons": ["inverted_spurious_edge"] if reject else [],
        "metrics": {
            "n_trades": n,
            "sharpe": round(float(sharpe), 6),
            "classic_expectancy": expect,
            "payoff_ratio": stats["payoff_ratio"],
            "calmar": round(_calmar(pnls, trades)[0], 6),
        },
        "threshold": {
            "min_trades": int(min_trades),
            "sharpe_ge": float(sharpe_reject_ge),
            "expectancy_gt": 0.0,
        },
        "rule": (
            "REJECT if inverted BT has n_trades>=5 AND sharpe>=0.5 "
            "AND classic_expectancy>0 (spurious / non-directional edge)"
        ),
        "fail_closed": True,
    }


def walk_forward_windows(trades, folds=WF_FOLDS_DEFAULT,
                         calmar_min=WF_CALMAR_MIN):
    """构建前向窗口；按成交笔数选择小样本比例门或大样本绝对窗数门。"""
    trades = list(trades or [])
    pnls = _pnls(trades)
    n = len(pnls)
    need_pass, need_total = WF_WINDOW_PASS_REQUIREMENT
    small_n = n > 0 and n < int(SMALL_N_TRADE_BOUNDARY)
    # 小样本：折数不超过成交笔数，避免大量空窗
    if small_n:
        folds = max(2, min(int(folds or need_total), n))
    else:
        folds = int(folds or need_total)
    windows = []
    if n <= 0:
        for i in range(folds):
            windows.append({
                "id": "window_%s" % i,
                "pass": False,
                "metrics": {"trades": 0, "calmar": 0.0, "classic_expectancy": 0.0},
                "fail_reason": "成交不足以填充窗口",
            })
    else:
        for i in range(folds):
            lo = int(math.floor(n * i / float(folds)))
            hi = int(math.floor(n * (i + 1) / float(folds)))
            if i == folds - 1:
                hi = n
            part_pnls = pnls[lo:hi]
            part_trades = trades[lo:hi] if len(trades) == n else [
                {"pnl_ratio": p} for p in part_pnls
            ]
            if not part_pnls:
                windows.append({
                    "id": "window_%s" % i,
                    "pass": False,
                    "metrics": {"trades": 0, "calmar": 0.0, "classic_expectancy": 0.0},
                    "fail_reason": "空窗口",
                })
                continue
            calmar, calmar_meta = _calmar(part_pnls, part_trades)
            expect = sum(part_pnls) / float(len(part_pnls))
            if small_n:
                # 小样本：单窗只需经典期望>0，不强制每窗卡尔玛≥1.0
                passed = expect > 0.0
                fail_reason = None if passed else "经典期望非正"
            else:
                passed = (calmar >= float(calmar_min)) and (expect > 0.0)
                fail_reason = None
                if not passed:
                    if expect <= 0:
                        fail_reason = "经典期望非正"
                    elif calmar < float(calmar_min):
                        fail_reason = "卡尔玛低于%.1f" % float(calmar_min)
                    else:
                        fail_reason = "窗口未通过"
            windows.append({
                "id": "window_%s" % i,
                "pass": bool(passed),
                "metrics": {
                    "trades": len(part_pnls),
                    "成交笔数": len(part_pnls),
                    "calmar": round(float(calmar), 6),
                    "卡尔玛": round(float(calmar), 6),
                    "classic_expectancy": round(float(expect), 8),
                    "mean_net": round(float(expect), 8),
                    "平均净收益": round(float(expect), 8),
                    "calmar_meta": calmar_meta,
                },
                "fail_reason": fail_reason,
            })
    # 可用折 = 非空窗（小样本）；大样本仍看全部窗
    nonempty = [w for w in windows if int((w.get("metrics") or {}).get("trades") or 0) > 0]
    available = len(nonempty) if small_n else len(windows)
    pass_count = sum(1 for w in (nonempty if small_n else windows) if w.get("pass"))
    total = len(windows)
    ok, detail = wf_pass_for_sample(
        n, pass_count, total if not small_n else max(available, 1),
        available_folds=available if small_n else total,
    )
    return {
        "windows": windows,
        "pass_count": pass_count,
        "total": total,
        "available_folds": available,
        "requirement": (
            "小样本比例门" if small_n else "%s/%s" % (need_pass, need_total)
        ),
        "pass": bool(ok),
        "calmar_min": float(calmar_min) if not small_n else None,
        "small_sample": bool(small_n),
        "gate_mode": detail.get("gate_mode"),
        "forward_detail": detail,
        "门槛名": "门槛3",
        "rule": (
            "小样本：单窗经典期望>0；整体走前向比例门"
            if small_n else
            "大样本：单窗卡尔玛≥1.0 且经典期望>0；需至少 %d/%d"
            % (WF_LARGE_NEED_PASS, WF_LARGE_NEED_TOTAL)
        ),
        "label_zh": detail.get("label_zh") or detail.get("label_zh_mode"),
        "trades": trades,
        "oos_trades": trades,
        "成交笔数": n,
    }


def evaluate_null_hypothesis(definition=None, frame=None, backtest_fn=None,
                             trades=None, seed=42, folds=WF_FOLDS_DEFAULT,
                             skip_frame_tests=False):
    """Run L3 suite. Frame tests optional when only trades available (WF only)."""
    results = {}
    reasons = []

    # 1+2 require frame + backtest_fn unless skipped (unit fixtures)
    if skip_frame_tests or frame is None or backtest_fn is None:
        results["permuted_returns"] = {
            "pass": True if skip_frame_tests else False,
            "test": "permuted_returns",
            "skipped": True,
            "reject_reasons": [] if skip_frame_tests else ["frame_or_backtest_fn_missing"],
            "fail_closed": True,
        }
        results["inverted_market"] = {
            "pass": True if skip_frame_tests else False,
            "test": "inverted_market",
            "skipped": True,
            "reject_reasons": [] if skip_frame_tests else ["frame_or_backtest_fn_missing"],
            "fail_closed": True,
        }
        if not skip_frame_tests:
            reasons.extend(["permuted_returns_unavailable", "inverted_market_unavailable"])
    else:
        results["permuted_returns"] = run_permuted_returns_test(
            definition, frame, backtest_fn, seed=seed,
        )
        results["inverted_market"] = run_inverted_market_test(
            definition, frame, backtest_fn,
        )
        if not results["permuted_returns"].get("pass"):
            reasons.extend(results["permuted_returns"].get("reject_reasons") or ["perm_fail"])
        if not results["inverted_market"].get("pass"):
            reasons.extend(results["inverted_market"].get("reject_reasons") or ["invert_fail"])

    wf = walk_forward_windows(trades or [], folds=folds)
    results["walk_forward"] = wf
    if not wf.get("pass"):
        reasons.append(
            "walk_forward_%s_of_%s" % (wf.get("pass_count"), wf.get("total"))
        )

    passed = (
        bool(results["permuted_returns"].get("pass"))
        and bool(results["inverted_market"].get("pass"))
        and bool(wf.get("pass"))
    )
    return {
        "pass": passed,
        "stage": STAGE,
        "reject_reasons": reasons,
        "tests": results,
        "walk_forward": wf,
        "fail_closed": True,
    }
