# -*- coding: utf-8 -*-
"""Phase-3 Level-1 micro-screen — cull garbage before full-history backtest.

Timing: after strategy compile / DSL validate, before full BT.
Runs on 10–20% random contiguous market slices (in-memory).
Target wall time < 0.1s when possible (actual recorded in result).

Hard reject if:
  - sample filled entries < 5
  - sample payoff ≤ 1.2
  - severe MAE: any trade MAE > 2.5 × avg_win (price space)
"""
from __future__ import print_function

import random
import time

from .fitness_engine import _pnls, _safe_float, payoff_stats


STAGE = "funnel_l1_micro_screen"
SAMPLE_FRACTION_DEFAULT = 0.15
SAMPLE_FRACTION_MIN = 0.10
SAMPLE_FRACTION_MAX = 0.20
MIN_FILLED_ENTRIES = 5
PAYOFF_FLOOR = 1.2  # hard reject if payoff ≤ 1.2
MAE_VS_AVG_WIN_MAX = 2.5
N_SLICES_DEFAULT = 2


def _clamp_fraction(fraction):
    try:
        f = float(fraction)
    except Exception:
        f = SAMPLE_FRACTION_DEFAULT
    return max(SAMPLE_FRACTION_MIN, min(SAMPLE_FRACTION_MAX, f))


def sample_frame_slices(frame, fraction=SAMPLE_FRACTION_DEFAULT, seed=None,
                        n_slices=N_SLICES_DEFAULT, min_bars=400):
    """Return concatenated random contiguous slices totaling ~fraction of bars.

    Preserves bar continuity inside each slice so indicators/backtest remain valid.
    """
    frac = _clamp_fraction(fraction)
    n = len(frame) if frame is not None else 0
    if n <= 0:
        return None, {"error": "empty_frame", "n_bars": 0}
    rng = random.Random(seed)
    target = max(int(min_bars), int(round(n * frac)))
    target = min(target, n)
    slice_len = max(int(min_bars), target // max(1, int(n_slices)))
    slice_len = min(slice_len, n)
    parts = []
    starts = []
    for _ in range(max(1, int(n_slices))):
        if n - slice_len <= 0:
            start = 0
        else:
            start = rng.randint(0, n - slice_len)
        starts.append(start)
        parts.append(frame.iloc[start:start + slice_len])
    # Prefer first slice alone when already meeting target (faster)
    if len(parts) == 1 or len(parts[0]) >= target:
        sampled = parts[0]
    else:
        try:
            import pandas as pd
            sampled = pd.concat(parts).sort_index()
            sampled = sampled[~sampled.index.duplicated(keep="first")]
        except Exception:
            # Fall back to first slice only (still a valid contiguous sample)
            sampled = parts[0]
        if len(sampled) > target * 1.25:
            sampled = sampled.iloc[: max(target, int(min_bars))]
    meta = {
        "n_bars_full": n,
        "n_bars_sample": len(sampled),
        "fraction_requested": frac,
        "fraction_actual": round(len(sampled) / float(n), 4) if n else 0.0,
        "slice_starts": starts,
        "n_slices": len(parts),
        "seed": seed,
    }
    return sampled, meta


def evaluate_micro_screen_trades(trades, min_filled=MIN_FILLED_ENTRIES,
                                 payoff_floor=PAYOFF_FLOOR,
                                 mae_vs_avg_win_max=MAE_VS_AVG_WIN_MAX):
    """Hard-reject rules on a trade list (no frame required)."""
    trades = list(trades or [])
    pnls = _pnls(trades)
    n = len(pnls)
    stats = payoff_stats(pnls)
    payoff = float(stats["payoff_ratio"])
    avg_win = float(stats["avg_win"])

    mae_flags = []
    has_mae = False
    for i, t in enumerate(trades):
        if not isinstance(t, dict):
            continue
        mae = _safe_float(t.get("mae_price_pct"), None)
        if mae is None:
            mae = _safe_float(t.get("mae"), None)
        if mae is None:
            continue
        has_mae = True
        lev = _safe_float(t.get("leverage"), 1.0) or 1.0
        avg_win_price = abs(avg_win) / lev if lev > 1 else abs(avg_win)
        if avg_win_price <= 0:
            continue
        if abs(mae) > mae_vs_avg_win_max * avg_win_price + 1e-15:
            mae_flags.append({
                "trade_index": i,
                "mae": abs(mae),
                "threshold": mae_vs_avg_win_max * avg_win_price,
            })

    reasons = []
    if n < int(min_filled):
        reasons.append("sample_filled_entries_lt_%s" % min_filled)
    if n >= 1 and payoff <= float(payoff_floor):
        reasons.append("sample_payoff_le_%s" % payoff_floor)
    if has_mae and mae_flags:
        reasons.append("severe_mae_gt_%sx_avg_win" % mae_vs_avg_win_max)

    passed = len(reasons) == 0
    return {
        "pass": passed,
        "stage": STAGE,
        "reject_reasons": reasons,
        "metrics": {
            "filled_entries": n,
            "payoff_ratio": payoff,
            "win_rate": stats["win_rate"],
            "avg_win": avg_win,
            "avg_loss": stats["avg_loss"],
            "expectancy_factor": stats["expectancy_factor_wr_x_payoff"],
            "classic_expectancy": stats["classic_expectancy_mean_pnl"],
            "mae_flags_n": len(mae_flags),
            "mae_enforced": has_mae,
        },
        "thresholds": {
            "min_filled_entries": int(min_filled),
            "payoff_floor_reject_le": float(payoff_floor),
            "mae_vs_avg_win_max": float(mae_vs_avg_win_max),
        },
        "mae_flags": mae_flags[:10],
    }


def run_micro_screen(definition=None, frame=None, backtest_fn=None,
                     trades=None, fraction=SAMPLE_FRACTION_DEFAULT,
                     seed=None, n_slices=N_SLICES_DEFAULT,
                     min_bars=400, stop_loss_pct=0.009):
    """Run L1 micro-screen.

    Modes:
      1) trades provided → evaluate in-memory (fastest; used by unit tests)
      2) backtest_fn(sampled_frame, definition) provided → slice + BT
      3) frame + definition → default dsl.backtest_dsl on sample
    """
    t0 = time.perf_counter()
    sample_meta = None
    used_trades = trades
    bt_error = None

    if used_trades is None:
        if frame is None and backtest_fn is None:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return {
                "pass": False,
                "stage": STAGE,
                "reject_reasons": ["no_frame_or_trades"],
                "metrics": {},
                "sample_meta": None,
                "wall_time_ms": round(elapsed_ms, 3),
                "fail_closed": True,
            }
        sampled = frame
        if frame is not None and backtest_fn is None:
            sampled, sample_meta = sample_frame_slices(
                frame, fraction=fraction, seed=seed,
                n_slices=n_slices, min_bars=min_bars,
            )
        elif frame is not None and backtest_fn is not None:
            sampled, sample_meta = sample_frame_slices(
                frame, fraction=fraction, seed=seed,
                n_slices=n_slices, min_bars=min_bars,
            )
        try:
            if backtest_fn is not None:
                result = backtest_fn(sampled if sampled is not None else frame, definition)
            else:
                import auto_trade_strategy_dsl as dsl_mod
                result = dsl_mod.backtest_dsl(
                    sampled, definition, stop_loss_pct=float(stop_loss_pct),
                )
            used_trades = list((result or {}).get("trades") or [])
        except Exception as exc:
            bt_error = str(exc)
            used_trades = []

    verdict = evaluate_micro_screen_trades(used_trades)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if bt_error:
        verdict["pass"] = False
        verdict["reject_reasons"] = list(verdict.get("reject_reasons") or []) + [
            "micro_backtest_error:%s" % bt_error
        ]
    verdict["sample_meta"] = sample_meta
    verdict["wall_time_ms"] = round(elapsed_ms, 3)
    verdict["target_wall_time_ms"] = 100.0
    verdict["under_target"] = elapsed_ms < 100.0
    verdict["fail_closed"] = True
    verdict["n_trades_sample"] = len(used_trades or [])
    verdict["trades_sample"] = list(used_trades or [])
    return verdict


def run_micro_screen_matrix(definition=None, frames_by_symbol=None, backtest_fn=None,
                            fraction=SAMPLE_FRACTION_DEFAULT, seed=None,
                            n_slices=N_SLICES_DEFAULT, min_bars=400,
                            stop_loss_pct=0.009):
    """L1 across a multi-symbol matrix: aggregate sample fills, do not lower floors.

    frames_by_symbol: ordered dict/list of (symbol, frame) pairs.
    Each symbol is micro-screened independently; trades are pooled for the
    sample_filled_entries / payoff / MAE hard rules (thresholds unchanged).
    """
    t0 = time.perf_counter()
    frames_by_symbol = frames_by_symbol or []
    if isinstance(frames_by_symbol, dict):
        frames_by_symbol = list(frames_by_symbol.items())

    pooled = []
    per_symbol = []
    base_seed = int(seed) if seed is not None else 42
    for i, item in enumerate(frames_by_symbol):
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            sym, frame = item[0], item[1]
        else:
            continue
        if frame is None:
            per_symbol.append({
                "symbol": sym, "pass": False, "filled_entries": 0,
                "reject_reasons": ["no_frame"],
            })
            continue
        one = run_micro_screen(
            definition=definition,
            frame=frame,
            backtest_fn=backtest_fn,
            fraction=fraction,
            seed=base_seed + i * 9973,
            n_slices=n_slices,
            min_bars=min_bars,
            stop_loss_pct=stop_loss_pct,
        )
        trades = list(one.get("trades_sample") or [])
        for t in trades:
            if isinstance(t, dict):
                tt = dict(t)
                tt.setdefault("matrix_symbol", sym)
                pooled.append(tt)
            else:
                pooled.append(t)
        m = (one.get("metrics") or {})
        per_symbol.append({
            "symbol": sym,
            "pass": bool(one.get("pass")),
            "filled_entries": int(m.get("filled_entries") or len(trades)),
            "payoff_ratio": m.get("payoff_ratio"),
            "reject_reasons": list(one.get("reject_reasons") or []),
            "wall_time_ms": one.get("wall_time_ms"),
            "sample_meta": one.get("sample_meta"),
        })

    verdict = evaluate_micro_screen_trades(pooled)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if not frames_by_symbol:
        verdict["pass"] = False
        verdict["reject_reasons"] = list(verdict.get("reject_reasons") or []) + [
            "empty_matrix_frames"
        ]
    verdict["sample_meta"] = {
        "mode": "multi_symbol_matrix",
        "n_symbols_requested": len(frames_by_symbol),
        "n_symbols_with_frame": sum(
            1 for r in per_symbol if "no_frame" not in (r.get("reject_reasons") or [])
        ),
        "per_symbol": per_symbol,
        "seed": base_seed,
    }
    verdict["wall_time_ms"] = round(elapsed_ms, 3)
    verdict["target_wall_time_ms"] = 100.0
    verdict["under_target"] = elapsed_ms < 100.0
    verdict["fail_closed"] = True
    verdict["n_trades_sample"] = len(pooled)
    verdict["trades_sample"] = pooled
    verdict["matrix_enabled"] = True
    return verdict
