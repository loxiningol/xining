# -*- coding: utf-8 -*-
"""Phase-4 multi-environment incubator (post L1–L3).

Hard gates (fail-closed):
  1. Cross-asset blind test on 2–3 non-target correlated symbols
     (default ETH/SOL/BNB USDT-SWAP). Require Expectancy > 0 AND
     Calmar ≥ 0.8 on ≥2 non-target symbols — else reject single-symbol
     overfitting.
  2. Multi-TF / window perturbation: for 15m strategies try 5m & 1h
     frames when available; always also perturb ATR/lookback windows ±20%.
     Hard: Calmar must not cliff-drop >40% vs baseline.

Research-only. Does not auto-mount. Protective 0.9% SL unchanged.
Production mount sizing remains B-grade 30%/20x via CLI --confirm only.
"""
from __future__ import print_function

import copy
import math
import time

from .fitness_engine import (
    _calmar,
    _pnls,
    payoff_stats,
)


STAGE = "phase4_incubator"
INCUBATOR_VERSION = "phase4_incubator_v1"

# Cross-asset defaults (OKX SWAP naming used by local loaders)
DEFAULT_CROSS_SYMBOLS = (
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "BNB-USDT-SWAP",
)
CROSS_CALMAR_MIN = 0.8
CROSS_EXPECTANCY_MIN = 0.0  # classic mean pnl > 0
CROSS_PASS_SYMBOLS_MIN = 2

# Multi-TF / perturbation
CALMAR_CLIFF_DROP_MAX = 0.40  # reject if drop > 40%
WINDOW_PERTURB_FRACTIONS = (-0.20, 0.20)
ALT_TIMEFRAMES_FOR_15M = ("5m", "1h")

PROTECTIVE_SL_PCT = 0.009


def _safe_float(x, default=None):
    try:
        v = float(x)
        if math.isfinite(v):
            return v
    except Exception:
        pass
    return default


def metrics_from_trades(trades):
    """Calmar / classic expectancy / payoff / mean MAE for incubator + Wx."""
    pnls = _pnls(trades)
    n = len(pnls)
    if not n:
        return {
            "trades": 0,
            "calmar": 0.0,
            "classic_expectancy": 0.0,
            "payoff_ratio": 0.0,
            "mean_mae": None,
            "win_rate": 0.0,
        }
    calmar, calmar_meta = _calmar(pnls, trades)
    stats = payoff_stats(pnls)
    maes = []
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        m = _safe_float(t.get("mae_price_pct"), None)
        if m is None:
            m = _safe_float(t.get("mae"), None)
        if m is not None:
            maes.append(abs(m))
    mean_mae = (sum(maes) / float(len(maes))) if maes else None
    return {
        "trades": n,
        "calmar": round(float(calmar), 6),
        "classic_expectancy": round(float(stats["classic_expectancy_mean_pnl"]), 8),
        "payoff_ratio": round(float(stats["payoff_ratio"]), 6),
        "mean_mae": None if mean_mae is None else round(float(mean_mae), 8),
        "win_rate": round(float(stats["win_rate"]), 6),
        "calmar_meta": calmar_meta,
        "max_drawdown": calmar_meta.get("max_drawdown"),
    }


def _symbol_passes(metrics):
    return (
        int(metrics.get("trades") or 0) >= 1
        and float(metrics.get("classic_expectancy") or 0.0) > CROSS_EXPECTANCY_MIN
        and float(metrics.get("calmar") or 0.0) >= CROSS_CALMAR_MIN
    )


def _normalize_symbol(sym):
    text = str(sym or "").strip().upper()
    if not text:
        return text
    if text.endswith("-SWAP"):
        return text
    if text.endswith("USDT") and "-USDT" in text:
        return text + "-SWAP" if not text.endswith("-SWAP") else text
    if text.endswith("-USDT"):
        return text + "-SWAP"
    return text


def default_frame_loader(symbol, timeframe):
    """Best-effort local/prod parquet loader. Returns None if unavailable."""
    try:
        import auto_trade_dual_engine_factory as dual
        return dual._frame(symbol, timeframe)
    except Exception:
        pass
    try:
        import auto_trade_human_confirm_pipeline as pipeline
        return pipeline._frame(symbol, timeframe)
    except Exception:
        return None


def _backtest_metrics(definition, frame, backtest_fn, stop_loss_pct=PROTECTIVE_SL_PCT):
    if frame is None or backtest_fn is None:
        return None, []
    try:
        result = backtest_fn(frame, definition) or {}
    except TypeError:
        # allow (frame, definition, stop_loss_pct=...)
        try:
            result = backtest_fn(frame, definition, stop_loss_pct=stop_loss_pct) or {}
        except Exception:
            return None, []
    except Exception:
        return None, []
    trades = list(result.get("trades") or [])
    return metrics_from_trades(trades), trades


def _scale_window_fields(node, fraction):
    """Recursively scale lookback / atr_period / period-like ints by (1+fraction)."""
    if not isinstance(node, dict):
        if isinstance(node, list):
            return [_scale_window_fields(x, fraction) for x in node]
        return node
    out = {}
    scale_keys = {
        "lookback", "atr_period", "period", "n", "window",
        "ema_period", "rsi_period", "cci_period", "max_hold_bars",
    }
    for k, v in node.items():
        if k in scale_keys and isinstance(v, (int, float)) and not isinstance(v, bool):
            scaled = int(max(1, round(float(v) * (1.0 + float(fraction)))))
            out[k] = scaled
        elif isinstance(v, (dict, list)):
            out[k] = _scale_window_fields(v, fraction)
        else:
            out[k] = v
    return out


def perturb_definition_windows(definition, fraction):
    """Return a deep-copied DSL with ATR/lookback windows scaled by ±fraction."""
    defn = copy.deepcopy(definition or {})
    for section in ("entry", "exit"):
        if section in defn:
            defn[section] = _scale_window_fields(defn[section], fraction)
    if "max_hold_bars" in defn and isinstance(defn["max_hold_bars"], (int, float)):
        defn["max_hold_bars"] = int(
            max(1, round(float(defn["max_hold_bars"]) * (1.0 + float(fraction))))
        )
    # Keep identity keys stable for research BT (validators may require key)
    return defn


def run_cross_asset_blind_test(
    definition,
    target_symbol,
    timeframe,
    backtest_fn,
    frame_loader=None,
    frame_map=None,
    cross_symbols=None,
    stop_loss_pct=PROTECTIVE_SL_PCT,
):
    """Blind-test definition on non-target symbols.

    frame_map: optional {symbol: frame} for unit tests / offline fixtures.
    frame_loader(symbol, timeframe) used when symbol not in frame_map.
    """
    loader = frame_loader or default_frame_loader
    frame_map = frame_map or {}
    target = _normalize_symbol(target_symbol)
    symbols = list(cross_symbols or DEFAULT_CROSS_SYMBOLS)
    # Never count the target itself toward the ≥2 requirement
    symbols = [_normalize_symbol(s) for s in symbols if _normalize_symbol(s) != target]

    results = []
    pass_count = 0
    tested = 0
    for sym in symbols:
        frame = frame_map.get(sym)
        if frame is None:
            try:
                frame = loader(sym, timeframe)
            except Exception:
                frame = None
        if frame is None:
            results.append({
                "symbol": sym,
                "available": False,
                "pass": False,
                "skip_reason": "frame_unavailable",
            })
            continue
        metrics, trades = _backtest_metrics(
            definition, frame, backtest_fn, stop_loss_pct=stop_loss_pct,
        )
        if metrics is None:
            results.append({
                "symbol": sym,
                "available": True,
                "pass": False,
                "skip_reason": "backtest_error",
            })
            continue
        tested += 1
        ok = _symbol_passes(metrics)
        if ok:
            pass_count += 1
        results.append({
            "symbol": sym,
            "available": True,
            "pass": ok,
            "metrics": metrics,
            "n_trades": len(trades),
        })

    # Cross-asset score: fraction of tested symbols that pass hard rule
    score = (pass_count / float(tested)) if tested else 0.0
    passed = pass_count >= CROSS_PASS_SYMBOLS_MIN
    reject_reasons = []
    if tested < CROSS_PASS_SYMBOLS_MIN:
        reject_reasons.append(
            "cross_asset_insufficient_frames:%d_need_%d" % (tested, CROSS_PASS_SYMBOLS_MIN)
        )
    if pass_count < CROSS_PASS_SYMBOLS_MIN:
        reject_reasons.append(
            "cross_asset_single_symbol_overfit:%d_of_%d_pass"
            % (pass_count, max(tested, len(symbols)))
        )
    return {
        "stage": "cross_asset_blind_test",
        "pass": passed,
        "pass_count": pass_count,
        "tested": tested,
        "required_pass": CROSS_PASS_SYMBOLS_MIN,
        "cross_asset_score": round(score, 4),
        "calmar_min": CROSS_CALMAR_MIN,
        "expectancy_min": CROSS_EXPECTANCY_MIN,
        "symbols": results,
        "reject_reasons": [] if passed else reject_reasons,
        "fail_closed": True,
    }


def run_multi_tf_perturbation(
    definition,
    target_symbol,
    timeframe,
    baseline_metrics,
    backtest_fn,
    baseline_frame=None,
    frame_loader=None,
    frame_map=None,
    stop_loss_pct=PROTECTIVE_SL_PCT,
):
    """Multi-TF + window ±20% perturbation; reject Calmar cliff-drop >40%."""
    loader = frame_loader or default_frame_loader
    frame_map = dict(frame_map or {})
    baseline_calmar = float((baseline_metrics or {}).get("calmar") or 0.0)
    floor = baseline_calmar * (1.0 - CALMAR_CLIFF_DROP_MAX) if baseline_calmar > 0 else 0.0

    probes = []
    cliff_hits = []

    # ---- Alternate timeframes (esp. 15m → 5m & 1h) ----
    tf = str(timeframe or "").lower()
    alt_tfs = list(ALT_TIMEFRAMES_FOR_15M) if tf == "15m" else []
    # Always try sibling TFs lightly when not 15m (optional evidence, still hard if cliff)
    if tf == "5m":
        alt_tfs = ["15m", "1h"]
    elif tf == "1h":
        alt_tfs = ["15m", "5m"]

    for alt_tf in alt_tfs:
        key = "%s|%s" % (_normalize_symbol(target_symbol), alt_tf)
        frame = frame_map.get(key) or frame_map.get(alt_tf)
        if frame is None:
            try:
                frame = loader(target_symbol, alt_tf)
            except Exception:
                frame = None
        if frame is None:
            probes.append({
                "kind": "alt_timeframe",
                "timeframe": alt_tf,
                "available": False,
                "skip_reason": "frame_unavailable",
            })
            continue
        metrics, _trades = _backtest_metrics(
            definition, frame, backtest_fn, stop_loss_pct=stop_loss_pct,
        )
        if metrics is None:
            probes.append({
                "kind": "alt_timeframe",
                "timeframe": alt_tf,
                "available": True,
                "pass": False,
                "skip_reason": "backtest_error",
            })
            continue
        cal = float(metrics.get("calmar") or 0.0)
        drop = None
        if baseline_calmar > 1e-12:
            drop = max(0.0, (baseline_calmar - cal) / baseline_calmar)
        cliff = bool(baseline_calmar > 0 and cal < floor - 1e-15)
        if cliff:
            cliff_hits.append("alt_tf_%s_calmar_cliff" % alt_tf)
        probes.append({
            "kind": "alt_timeframe",
            "timeframe": alt_tf,
            "available": True,
            "pass": not cliff,
            "metrics": metrics,
            "calmar_drop": None if drop is None else round(drop, 6),
            "cliff": cliff,
        })

    # ---- ATR / lookback window ±20% on baseline frame ----
    base_frame = baseline_frame
    if base_frame is None:
        base_key = "%s|%s" % (_normalize_symbol(target_symbol), timeframe)
        base_frame = frame_map.get(base_key) or frame_map.get(timeframe)
        if base_frame is None:
            try:
                base_frame = loader(target_symbol, timeframe)
            except Exception:
                base_frame = None

    for frac in WINDOW_PERTURB_FRACTIONS:
        label = "window_%+d_pct" % int(round(frac * 100))
        if base_frame is None:
            probes.append({
                "kind": "window_perturb",
                "fraction": frac,
                "label": label,
                "available": False,
                "skip_reason": "baseline_frame_unavailable",
            })
            continue
        pert = perturb_definition_windows(definition, frac)
        metrics, _trades = _backtest_metrics(
            pert, base_frame, backtest_fn, stop_loss_pct=stop_loss_pct,
        )
        if metrics is None:
            probes.append({
                "kind": "window_perturb",
                "fraction": frac,
                "label": label,
                "available": True,
                "pass": False,
                "skip_reason": "backtest_error",
            })
            continue
        cal = float(metrics.get("calmar") or 0.0)
        drop = None
        if baseline_calmar > 1e-12:
            drop = max(0.0, (baseline_calmar - cal) / baseline_calmar)
        cliff = bool(baseline_calmar > 0 and cal < floor - 1e-15)
        if cliff:
            cliff_hits.append("%s_calmar_cliff" % label)
        probes.append({
            "kind": "window_perturb",
            "fraction": frac,
            "label": label,
            "available": True,
            "pass": not cliff,
            "metrics": metrics,
            "calmar_drop": None if drop is None else round(drop, 6),
            "cliff": cliff,
        })

    # Hard fail if any available probe cliff-drops; if NO probes available, fail-closed
    available = [p for p in probes if p.get("available")]
    reject_reasons = list(cliff_hits)
    if not available:
        reject_reasons.append("multi_tf_no_perturbation_frames")
    passed = (len(available) > 0) and (len(cliff_hits) == 0)
    return {
        "stage": "multi_tf_perturbation",
        "pass": passed,
        "baseline_calmar": baseline_calmar,
        "calmar_floor": round(floor, 6),
        "cliff_drop_max": CALMAR_CLIFF_DROP_MAX,
        "probes": probes,
        "reject_reasons": [] if passed else reject_reasons,
        "fail_closed": True,
    }


def run_incubator(
    definition=None,
    target_symbol=None,
    timeframe=None,
    baseline_trades=None,
    baseline_metrics=None,
    baseline_frame=None,
    backtest_fn=None,
    frame_loader=None,
    frame_map=None,
    cross_symbols=None,
    stop_loss_pct=PROTECTIVE_SL_PCT,
    skip_cross_asset=False,
    skip_multi_tf=False,
):
    """Run Phase-4 incubator after L1–L3 survivors.

    Returns fail-closed log with cross_asset_score and mean_mae for Wx cards.
    """
    t0 = time.perf_counter()
    base_m = dict(baseline_metrics or {})
    if not base_m and baseline_trades is not None:
        base_m = metrics_from_trades(baseline_trades)
    elif baseline_trades is not None and "mean_mae" not in base_m:
        extra = metrics_from_trades(baseline_trades)
        for k in ("mean_mae", "calmar", "classic_expectancy", "payoff_ratio"):
            if base_m.get(k) is None:
                base_m[k] = extra.get(k)

    log = {
        "incubator_version": INCUBATOR_VERSION,
        "stage": STAGE,
        "pass": False,
        "rejected_at": None,
        "reject_reasons": [],
        "fail_closed": True,
        "protective_sl_pct": float(stop_loss_pct),
        "ada_migrate": False,
        "auto_mount": False,
        "production_mounted": False,
        "baseline_metrics": {
            "calmar": base_m.get("calmar"),
            "payoff_ratio": base_m.get("payoff_ratio") or base_m.get("payoff"),
            "classic_expectancy": base_m.get("classic_expectancy") or base_m.get("mean_net"),
            "mean_mae": base_m.get("mean_mae"),
            "trades": base_m.get("trades"),
        },
        "tests": {},
    }

    if not skip_cross_asset:
        cross = run_cross_asset_blind_test(
            definition=definition,
            target_symbol=target_symbol,
            timeframe=timeframe,
            backtest_fn=backtest_fn,
            frame_loader=frame_loader,
            frame_map=frame_map,
            cross_symbols=cross_symbols,
            stop_loss_pct=stop_loss_pct,
        )
        log["tests"]["cross_asset"] = cross
        log["cross_asset_score"] = cross.get("cross_asset_score")
        if not cross.get("pass"):
            log["rejected_at"] = "cross_asset_blind_test"
            log["reject_reasons"] = list(cross.get("reject_reasons") or ["cross_asset_fail"])
            log["wall_time_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
            return log
    else:
        log["tests"]["cross_asset"] = {"pass": True, "skipped": True}
        log["cross_asset_score"] = None

    if not skip_multi_tf:
        multi = run_multi_tf_perturbation(
            definition=definition,
            target_symbol=target_symbol,
            timeframe=timeframe,
            baseline_metrics=base_m,
            backtest_fn=backtest_fn,
            baseline_frame=baseline_frame,
            frame_loader=frame_loader,
            frame_map=frame_map,
            stop_loss_pct=stop_loss_pct,
        )
        log["tests"]["multi_tf_perturbation"] = multi
        if not multi.get("pass"):
            log["rejected_at"] = "multi_tf_perturbation"
            log["reject_reasons"] = list(multi.get("reject_reasons") or ["multi_tf_fail"])
            log["wall_time_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
            return log
    else:
        log["tests"]["multi_tf_perturbation"] = {"pass": True, "skipped": True}

    log["pass"] = True
    log["mean_mae"] = base_m.get("mean_mae")
    log["calmar"] = base_m.get("calmar")
    log["payoff_ratio"] = base_m.get("payoff_ratio") or base_m.get("payoff")
    log["wall_time_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
    return log
