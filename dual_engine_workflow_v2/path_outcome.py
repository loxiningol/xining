# -*- coding: utf-8 -*-
"""Path-hit labels: profit_first / MFE / MAE before fixed stop.

Creation target is NOT future_return > 0. It is:

  P( touch +TARGET before touch -STOP | entry conditions )

Long:  target = entry * (1 + 0.005555), stop = entry * (1 - 0.005)
Short: target = entry * (1 - 0.005555), stop = entry * (1 + 0.005)

Same entry semantics as probe_protocol: next-bar open (signal_i + 1).

对外中文路径结果：止盈 / 止损 / 定时（见 path_lexicon；禁止先盈/先亏/到期）。
"""
from __future__ import print_function

import math

from .path_lexicon import first_touch_zh as _first_touch_zh

TARGET_PRICE_PCT = 0.005555
STOP_PRICE_PCT = 0.005
HORIZONS = (6, 12, 24, 48, 96)
LEVERAGE = 20
MIN_LEVERED_WIN_RETURN = TARGET_PRICE_PCT * LEVERAGE  # 0.1111
MIN_LEVERED_WIN_MEDIAN = 0.10


def _finite(x):
    try:
        v = float(x)
    except Exception:
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _median(xs):
    vals = sorted(v for v in xs if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return 0.5 * (vals[mid - 1] + vals[mid])


def _mean(xs):
    vals = [v for v in xs if v is not None]
    if not vals:
        return None
    return sum(vals) / float(len(vals))


def entry_index_from_signal(signal_i, mapping="next_bar_open"):
    if mapping == "delayed_confirmation":
        return int(signal_i) + 2
    return int(signal_i) + 1


def label_path(candles, entry_i, direction, horizon,
               target_pct=None, stop_pct=None):
    """Label one entry path.

    profit_first:
      1  = target touched before stop
      0  = stop touched before target
     -1  = neither within horizon
    """
    rows = candles or []
    entry_i = int(entry_i)
    horizon = int(horizon)
    direction = 1 if float(direction) > 0 else -1
    target_pct = float(target_pct if target_pct is not None else TARGET_PRICE_PCT)
    stop_pct = float(stop_pct if stop_pct is not None else STOP_PRICE_PCT)
    if entry_i < 0 or entry_i >= len(rows):
        return None
    end_i = entry_i + horizon - 1
    if end_i >= len(rows):
        return None
    entry = _finite(rows[entry_i].get("open"))
    if not entry or entry <= 0:
        return None
    if direction > 0:
        target_price = entry * (1.0 + target_pct)
        stop_price = entry * (1.0 - stop_pct)
    else:
        target_price = entry * (1.0 - target_pct)
        stop_price = entry * (1.0 + stop_pct)

    bars_to_target = None
    bars_to_stop = None
    mfe_pct = 0.0
    mae_pct = 0.0
    # MAE while path still unresolved toward target; MFE until stop.
    for offset, index in enumerate(range(entry_i, end_i + 1)):
        high = _finite(rows[index].get("high"))
        low = _finite(rows[index].get("low"))
        if high is None or low is None:
            continue
        if direction > 0:
            fav = high / entry - 1.0
            adv = low / entry - 1.0
            hit_target = high >= target_price
            hit_stop = low <= stop_price
        else:
            fav = 1.0 - low / entry
            adv = 1.0 - high / entry
            hit_target = low <= target_price
            hit_stop = high >= stop_price
        if fav > mfe_pct:
            mfe_pct = fav
        if adv < mae_pct:
            mae_pct = adv

        # Same-bar ambiguity: conservative — if both touch, count as stop-first
        # (path does not clear the protective barrier cleanly).
        if hit_target and hit_stop:
            bars_to_stop = offset + 1
            bars_to_target = offset + 1
            ft = "stop_same_bar_as_target"
            return {
                "ok": True,
                "profit_first": 0,
                "first_touch": ft,
                "first_touch_zh": _first_touch_zh(ft, 0),
                "mfe_pct": float(mfe_pct),
                "mae_pct": float(mae_pct),
                "bars_to_target": bars_to_target,
                "bars_to_stop": bars_to_stop,
                "entry_index": entry_i,
                "entry_price": entry,
                "direction": direction,
                "horizon": horizon,
                "target_price_pct": target_pct,
                "stop_price_pct": stop_pct,
                "levered_mfe": float(mfe_pct) * LEVERAGE,
                "levered_target_return": target_pct * LEVERAGE,
            }
        if hit_stop and bars_to_stop is None:
            bars_to_stop = offset + 1
            ft = "stop"
            return {
                "ok": True,
                "profit_first": 0,
                "first_touch": ft,
                "first_touch_zh": _first_touch_zh(ft, 0),
                "mfe_pct": float(mfe_pct),
                "mae_pct": float(mae_pct),
                "bars_to_target": None,
                "bars_to_stop": bars_to_stop,
                "entry_index": entry_i,
                "entry_price": entry,
                "direction": direction,
                "horizon": horizon,
                "target_price_pct": target_pct,
                "stop_price_pct": stop_pct,
                "levered_mfe": float(mfe_pct) * LEVERAGE,
                "levered_target_return": target_pct * LEVERAGE,
            }
        if hit_target and bars_to_target is None:
            bars_to_target = offset + 1
            ft = "target"
            return {
                "ok": True,
                "profit_first": 1,
                "first_touch": ft,
                "first_touch_zh": _first_touch_zh(ft, 1),
                "mfe_pct": float(mfe_pct),
                "mae_pct": float(mae_pct),
                "bars_to_target": bars_to_target,
                "bars_to_stop": None,
                "entry_index": entry_i,
                "entry_price": entry,
                "direction": direction,
                "horizon": horizon,
                "target_price_pct": target_pct,
                "stop_price_pct": stop_pct,
                "levered_mfe": float(mfe_pct) * LEVERAGE,
                "levered_target_return": target_pct * LEVERAGE,
            }

    ft = "unresolved"
    return {
        "ok": True,
        "profit_first": -1,
        "first_touch": ft,
        "first_touch_zh": _first_touch_zh(ft, -1),
        "mfe_pct": float(mfe_pct),
        "mae_pct": float(mae_pct),
        "bars_to_target": None,
        "bars_to_stop": None,
        "entry_index": entry_i,
        "entry_price": entry,
        "direction": direction,
        "horizon": horizon,
        "target_price_pct": target_pct,
        "stop_price_pct": stop_pct,
        "levered_mfe": float(mfe_pct) * LEVERAGE,
        "levered_target_return": target_pct * LEVERAGE,
    }


def label_signal(candles, signal_i, direction, horizon, mapping="next_bar_open",
                 target_pct=None, stop_pct=None):
    entry_i = entry_index_from_signal(signal_i, mapping=mapping)
    row = label_path(
        candles, entry_i, direction, horizon,
        target_pct=target_pct, stop_pct=stop_pct,
    )
    if row is None:
        return None
    row["signal_index"] = int(signal_i)
    row["mapping"] = mapping
    return row


def _temporal_positive_ratio(labels, n_slices=5):
    """Fraction of time slices with profit_first_rate >= 0.5 among resolved."""
    rows = [r for r in (labels or []) if r]
    if len(rows) < n_slices:
        # Too few for split — treat as single slice.
        pf = [r for r in rows if int(r.get("profit_first") or -99) == 1]
        stop = [r for r in rows if int(r.get("profit_first") or -99) == 0]
        resolved = len(pf) + len(stop)
        if resolved <= 0:
            return 0.0
        return 1.0 if (len(pf) / float(resolved)) >= 0.5 else 0.0
    size = max(1, len(rows) // int(n_slices))
    ok = 0
    total = 0
    for i in range(int(n_slices)):
        chunk = rows[i * size:(i + 1) * size] if i < n_slices - 1 else rows[i * size:]
        if not chunk:
            continue
        pf = sum(1 for r in chunk if int(r.get("profit_first") or -99) == 1)
        stop = sum(1 for r in chunk if int(r.get("profit_first") or -99) == 0)
        resolved = pf + stop
        if resolved <= 0:
            continue
        total += 1
        if (pf / float(resolved)) >= 0.5:
            ok += 1
    if total <= 0:
        return 0.0
    return ok / float(total)


def summarize_labels(labels):
    """Aggregate path labels for a candidate event set."""
    rows = [r for r in (labels or []) if r and r.get("ok")]
    n = len(rows)
    if n <= 0:
        return {
            "n": 0,
            "profit_first_rate": None,
            "loss_first_rate": None,
            "unresolved_rate": None,
            "median_mfe_pct": None,
            "median_mae_pct": None,
            "mean_mfe_pct": None,
            "mean_mae_pct": None,
            "mean_winning_move_pct": None,
            "median_winning_move_pct": None,
            "mean_winning_levered": None,
            "median_winning_levered": None,
            "avg_bars_to_target": None,
            "avg_bars_to_stop": None,
            "temporal_positive_ratio": 0.0,
            "levered_target_return": MIN_LEVERED_WIN_RETURN,
        }
    pf = [r for r in rows if int(r.get("profit_first")) == 1]
    lf = [r for r in rows if int(r.get("profit_first")) == 0]
    ur = [r for r in rows if int(r.get("profit_first")) == -1]
    winning_moves = [float(r.get("mfe_pct") or 0.0) for r in pf]
    # Floor winning move at target when profit_first (path cleared target).
    winning_moves = [max(v, TARGET_PRICE_PCT) for v in winning_moves]
    mae_vals = [float(r.get("mae_pct")) for r in rows if r.get("mae_pct") is not None]
    mfe_vals = [float(r.get("mfe_pct")) for r in rows if r.get("mfe_pct") is not None]
    btt = [float(r["bars_to_target"]) for r in pf if r.get("bars_to_target") is not None]
    bts = [float(r["bars_to_stop"]) for r in lf if r.get("bars_to_stop") is not None]
    mean_win = _mean(winning_moves)
    med_win = _median(winning_moves)
    return {
        "n": n,
        "n_profit_first": len(pf),
        "n_loss_first": len(lf),
        "n_unresolved": len(ur),
        "profit_first_rate": len(pf) / float(n),
        "loss_first_rate": len(lf) / float(n),
        "unresolved_rate": len(ur) / float(n),
        "median_mfe_pct": _median(mfe_vals),
        "median_mae_pct": _median(mae_vals),
        "mean_mfe_pct": _mean(mfe_vals),
        "mean_mae_pct": _mean(mae_vals),
        "mean_winning_move_pct": mean_win,
        "median_winning_move_pct": med_win,
        "mean_winning_levered": (mean_win * LEVERAGE) if mean_win is not None else None,
        "median_winning_levered": (med_win * LEVERAGE) if med_win is not None else None,
        "avg_bars_to_target": _mean(btt),
        "avg_bars_to_stop": _mean(bts),
        "temporal_positive_ratio": _temporal_positive_ratio(rows),
        "levered_target_return": MIN_LEVERED_WIN_RETURN,
    }


def summarize_signals(candles, signal_indices, direction, horizon,
                      mapping="next_bar_open", target_pct=None, stop_pct=None):
    labels = []
    for signal_i in signal_indices or []:
        row = label_signal(
            candles, signal_i, direction, horizon,
            mapping=mapping, target_pct=target_pct, stop_pct=stop_pct,
        )
        if row is not None:
            labels.append(row)
    summary = summarize_labels(labels)
    summary["labels"] = labels
    summary["direction"] = 1 if float(direction) > 0 else -1
    summary["horizon"] = int(horizon)
    summary["mapping"] = mapping
    return summary


def probe():
    # Synthetic: long, entry 100, next bars go up first then down.
    up_first = [
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
        {"open": 100.0, "high": 100.70, "low": 99.95, "close": 100.60},  # target ~100.5555
        {"open": 100.60, "high": 100.80, "low": 99.40, "close": 99.50},
    ]
    down_first = [
        {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
        {"open": 100.0, "high": 100.20, "low": 99.40, "close": 99.50},  # stop 99.5
        {"open": 99.50, "high": 101.00, "low": 99.40, "close": 100.80},
    ]
    a = label_path(up_first, 1, 1, 2)
    b = label_path(down_first, 1, 1, 2)
    return {
        "ok": True,
        "target_price_pct": TARGET_PRICE_PCT,
        "stop_price_pct": STOP_PRICE_PCT,
        "leverage": LEVERAGE,
        "min_levered_win": MIN_LEVERED_WIN_RETURN,
        "up_first_profit_first": (a or {}).get("profit_first"),
        "down_first_profit_first": (b or {}).get("profit_first"),
    }
