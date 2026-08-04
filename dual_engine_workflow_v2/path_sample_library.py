# -*- coding: utf-8 -*-
"""Build success / failure / stalled path libraries for conditional AI creation.

Do not invent microstructure fields. Snapshot only factors present in the
research factor matrix; mark missing families as unavailable.
"""
from __future__ import print_function

import json
import math
import os
from datetime import datetime
from pathlib import Path

from . import path_outcome as po


SNAPSHOT_FEATURES = (
    "ret_1", "ret_3", "ret_12",
    "dist_roll_high", "dist_roll_low",
    "range_pct", "upper_wick_pct", "lower_wick_pct", "close_location",
    "atr_pct_14", "close_z_20", "squeeze_score", "expansion_score",
    "squeeze_persistence", "volatility_acceleration",
    "volume_z", "signed_volume_pressure",
    "trend_efficiency_12", "trend_bias_50_200",
    "rsi_14", "bb_lower_dist", "bb_upper_dist", "bb_mid_reclaim", "bb_width",
    "reclaim_strength", "bullish_reclaim", "exhaustion_score",
    "downside_velocity_decay", "upside_velocity_decay",
)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _finite(x):
    try:
        v = float(x)
    except Exception:
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _safe_symbol_key(symbol, timeframe, direction):
    sym = str(symbol or "UNK").replace("-", "_").replace("/", "_").lower()
    tf = str(timeframe or "na").lower()
    side = "long" if str(direction).lower() in ("long", "1", "+1") else "short"
    return "%s_%s_%s" % (sym, tf, side)


def default_library_dir():
    root = Path(os.environ.get("VECTOR_ROOT") or "/root")
    return root / "auto_trade" / "dual_engine" / "path_libraries"


def _snapshot_at(factor_matrix, index):
    snap = {}
    missing = []
    for name in SNAPSHOT_FEATURES:
        series = (factor_matrix or {}).get(name)
        if not series or index >= len(series):
            missing.append(name)
            continue
        val = _finite(series[index])
        if val is None:
            missing.append(name)
        else:
            snap[name] = val
    return snap, missing


def _quantile(xs, q):
    vals = sorted(v for v in xs if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = max(0.0, min(1.0, float(q))) * (len(vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    w = pos - lo
    return vals[lo] * (1.0 - w) + vals[hi] * w


def _feature_diff(success_snaps, failure_snaps, top_k=12):
    """Rank features by |success_p50 - failure_p50| / pooled scale."""
    keys = set()
    for row in success_snaps:
        keys.update(row.keys())
    for row in failure_snaps:
        keys.update(row.keys())
    diffs = []
    for key in sorted(keys):
        s_vals = [r.get(key) for r in success_snaps if r.get(key) is not None]
        f_vals = [r.get(key) for r in failure_snaps if r.get(key) is not None]
        if len(s_vals) < 20 or len(f_vals) < 20:
            continue
        s50 = _quantile(s_vals, 0.5)
        f50 = _quantile(f_vals, 0.5)
        s75 = _quantile(s_vals, 0.75)
        s25 = _quantile(s_vals, 0.25)
        f75 = _quantile(f_vals, 0.75)
        f25 = _quantile(f_vals, 0.25)
        scale = abs(s50) + abs(f50) + 1e-9
        gap = abs(s50 - f50) / scale
        diffs.append({
            "feature": key,
            "success_p25": s25,
            "success_p50": s50,
            "success_p75": s75,
            "failure_p25": f25,
            "failure_p50": f50,
            "failure_p75": f75,
            "rel_gap": round(gap, 6),
            "direction": "higher_in_success" if s50 > f50 else "lower_in_success",
        })
    diffs.sort(key=lambda r: float(r.get("rel_gap") or 0.0), reverse=True)
    return diffs[: int(top_k)]


def build_path_library(
    candles,
    factor_matrix=None,
    symbol=None,
    timeframe=None,
    direction="long",
    horizons=None,
    data_version=None,
    max_samples_per_bucket=4000,
    stride=1,
):
    """Scan every eligible bar for path outcomes under each horizon."""
    horizons = tuple(horizons or po.HORIZONS)
    direction_sign = 1 if str(direction).lower() in ("long", "1", "+1") else -1
    side = "long" if direction_sign > 0 else "short"
    rows = candles or []
    n = len(rows)
    stride = max(1, int(stride))
    buckets = {}
    missing_families = {
        "microstructure": "unavailable",
        "derivatives": "unavailable",
        "cross_asset": "unavailable",
    }
    for horizon in horizons:
        success, fail_stop, stalled = [], [], []
        # Entry at bar i open; need signal context at i-1 when available.
        for entry_i in range(1, n - int(horizon), stride):
            label = po.label_path(rows, entry_i, direction_sign, horizon)
            if label is None:
                continue
            snap_i = entry_i - 1  # pre-entry closed bar snapshot
            snap, miss = _snapshot_at(factor_matrix, snap_i)
            sample = {
                "entry_index": entry_i,
                "signal_index": snap_i,
                "horizon": int(horizon),
                "direction": side,
                "profit_first": label.get("profit_first"),
                "first_touch": label.get("first_touch"),
                "mfe_pct": label.get("mfe_pct"),
                "mae_pct": label.get("mae_pct"),
                "bars_to_target": label.get("bars_to_target"),
                "bars_to_stop": label.get("bars_to_stop"),
                "snapshot": snap,
                "snapshot_missing": miss[:12],
            }
            pf = int(label.get("profit_first"))
            if pf == 1:
                if len(success) < int(max_samples_per_bucket):
                    success.append(sample)
            elif pf == 0:
                if len(fail_stop) < int(max_samples_per_bucket):
                    fail_stop.append(sample)
            else:
                if len(stalled) < int(max_samples_per_bucket):
                    stalled.append(sample)
        success_snaps = [s.get("snapshot") or {} for s in success]
        fail_snaps = [s.get("snapshot") or {} for s in fail_stop]
        diffs = _feature_diff(success_snaps, fail_snaps)
        n_scanned = max(0, (n - int(horizon) - 1) // stride)
        rate = (len(success) / float(n_scanned)) if n_scanned else None
        buckets[str(horizon)] = {
            "horizon": int(horizon),
            "n_scanned": n_scanned,
            "n_success": len(success),
            "n_fail_stop": len(fail_stop),
            "n_stalled": len(stalled),
            "base_profit_first_rate": rate,
            "success_samples": success[:800],  # keep payload bounded
            "fail_stop_samples": fail_stop[:400],
            "stalled_samples": stalled[:200],
            "feature_diffs": diffs,
        }

    # Pick best horizon by success rate with enough samples.
    ranked = sorted(
        buckets.values(),
        key=lambda b: (
            1 if int(b.get("n_success") or 0) >= 80 else 0,
            float(b.get("base_profit_first_rate") or 0.0),
            int(b.get("n_success") or 0),
        ),
        reverse=True,
    )
    best = ranked[0] if ranked else None
    cluster_diff_summary = {
        "schema": "qiyu_path_cluster_diff_v1",
        "best_horizon": (best or {}).get("horizon"),
        "base_profit_first_rate": (best or {}).get("base_profit_first_rate"),
        "n_success": (best or {}).get("n_success"),
        "n_fail_stop": (best or {}).get("n_fail_stop"),
        "top_feature_diffs": (best or {}).get("feature_diffs") or [],
        "prompt_zh": _prompt_block(best, symbol, timeframe, side),
        "unavailable_families": missing_families,
    }
    return {
        "ok": True,
        "schema": "qiyu_path_sample_library_v1",
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": side,
        "data_version": data_version,
        "target_price_pct": po.TARGET_PRICE_PCT,
        "stop_price_pct": po.STOP_PRICE_PCT,
        "leverage": po.LEVERAGE,
        "horizons": list(horizons),
        "buckets": {k: {
            "horizon": v["horizon"],
            "n_scanned": v["n_scanned"],
            "n_success": v["n_success"],
            "n_fail_stop": v["n_fail_stop"],
            "n_stalled": v["n_stalled"],
            "base_profit_first_rate": v["base_profit_first_rate"],
            "feature_diffs": v["feature_diffs"],
            # Keep compact samples for downstream conditional generation.
            "success_samples": v["success_samples"][:120],
            "fail_stop_samples": v["fail_stop_samples"][:60],
        } for k, v in buckets.items()},
        "cluster_diff_summary": cluster_diff_summary,
        "built_at": _now(),
    }


def _prompt_block(best, symbol, timeframe, side):
    if not best:
        return (
            "路径样本库为空。禁止自由编造策略；须先确认行情数据与因子矩阵可用。"
        )
    rate = best.get("base_profit_first_rate")
    rate_pct = ("%.1f%%" % (100.0 * rate)) if rate is not None else "—"
    lines = [
        "在 %s %s %s 数据中，持有期=%s 根时，无条件基线先触及 +0.5555%% 再触 -0.5%% 的概率约为 %s"
        "（成功=%s / 先止损=%s）。"
        % (
            symbol or "标的",
            timeframe or "周期",
            side,
            best.get("horizon"),
            rate_pct,
            best.get("n_success"),
            best.get("n_fail_stop"),
        ),
        "成功相对失败的特征差异（仅可用因子）：",
    ]
    for row in (best.get("feature_diffs") or [])[:8]:
        lines.append(
            "- %s: 成功P50=%.6g 失败P50=%.6g (%s, rel_gap=%.3f)"
            % (
                row.get("feature"),
                float(row.get("success_p50") or 0.0),
                float(row.get("failure_p50") or 0.0),
                row.get("direction"),
                float(row.get("rel_gap") or 0.0),
            )
        )
    lines.append(
        "请生成不超过4个条件的入场表达式，只能使用上列有证据的特征。"
        "禁止增加无数据证据的指标。必须说明为何入场后不应先反向0.5%。"
        "止损固定0.5%价格、杠杆固定20×，不得修改。"
    )
    return "\n".join(lines)


def persist_library(library, out_dir=None):
    library = library or {}
    out_dir = Path(out_dir or default_library_dir())
    out_dir.mkdir(parents=True, exist_ok=True)
    key = _safe_symbol_key(
        library.get("symbol"), library.get("timeframe"), library.get("direction"),
    )
    path = out_dir / ("%s.json" % key)
    path.write_text(json.dumps(library, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(path)


def load_or_build(
    candles,
    factor_matrix=None,
    symbol=None,
    timeframe=None,
    direction="long",
    data_version=None,
    force_rebuild=False,
    out_dir=None,
    stride=None,
):
    out_dir = Path(out_dir or default_library_dir())
    key = _safe_symbol_key(symbol, timeframe, direction)
    path = out_dir / ("%s.json" % key)
    if path.exists() and not force_rebuild:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if (
                data_version
                and cached.get("data_version")
                and str(cached.get("data_version")) != str(data_version)
            ):
                pass
            else:
                cached["cache_hit"] = True
                cached["path"] = str(path)
                return cached
        except Exception:
            pass
    # Stride keeps VPS RAM/CPU safe on long histories.
    if stride is None:
        n = len(candles or [])
        stride = 3 if n > 20000 else (2 if n > 8000 else 1)
    library = build_path_library(
        candles,
        factor_matrix=factor_matrix,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        data_version=data_version,
        stride=stride,
    )
    library["path"] = persist_library(library, out_dir=out_dir)
    library["cache_hit"] = False
    return library


def probe():
    candles = []
    price = 100.0
    for i in range(300):
        # Mild upward drift with noise → some profit-first longs.
        o = price
        h = price * (1.0 + 0.004 + (0.003 if i % 7 == 0 else 0.0))
        l = price * (1.0 - 0.002 - (0.004 if i % 11 == 0 else 0.0))
        c = price * (1.0 + 0.001)
        candles.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000})
        price = c
    fm = {
        "ret_1": [0.0] + [0.001] * 299,
        "rsi_14": [50.0] * 300,
        "atr_pct_14": [0.004] * 300,
    }
    lib = build_path_library(
        candles, factor_matrix=fm, symbol="TEST", timeframe="15m",
        direction="long", horizons=(6, 12), stride=1,
    )
    return {
        "ok": bool(lib.get("ok")),
        "n_horizons": len(lib.get("buckets") or {}),
        "has_prompt": bool((lib.get("cluster_diff_summary") or {}).get("prompt_zh")),
    }
