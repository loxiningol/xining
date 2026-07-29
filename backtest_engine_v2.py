# -*- coding: utf-8 -*-
import os
import json
import time
import hashlib
import logging
import math
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np

import strategy_logic as sl
from strategy_logic import *

MARKET_DATA_ROOT = os.environ.get("VECTOR_MARKET_DATA_ROOT", "/root/market_data")
TIMEFRAME_SPECS = {
    "1h": {
        "okx_bar": "1H",
        "label": "1小时",
        "expected_delta": pd.Timedelta(hours=1),
        "gap_tolerance": pd.Timedelta(minutes=65),
        "regime_bars_90d": 2160,
        "regime_min_bars": 336,
        "request_max_pages": 320,
    },
    "4h": {
        "okx_bar": "4H",
        "label": "4小时",
        "expected_delta": pd.Timedelta(hours=4),
        "gap_tolerance": pd.Timedelta(hours=4, minutes=20),
        "regime_bars_90d": 540,
        "regime_min_bars": 84,
        "request_max_pages": 120,
    },
    "15m": {
        "okx_bar": "15m",
        "label": "15分钟",
        "expected_delta": pd.Timedelta(minutes=15),
        "gap_tolerance": pd.Timedelta(minutes=20),
        "regime_bars_90d": 8640,
        "regime_min_bars": 1344,
        "request_max_pages": 1200,
    },
    "5m": {
        "okx_bar": "5m",
        "label": "5分钟",
        "expected_delta": pd.Timedelta(minutes=5),
        "gap_tolerance": pd.Timedelta(minutes=8),
        "regime_bars_90d": 25920,
        "regime_min_bars": 4032,
        "request_max_pages": 1800,
    },
}
CONFIG_PATH = os.environ.get(
    "VECTOR_STRATEGY_CONFIG_PATH",
    "/root/strategy_configs/experimental_strategies.json",
)
DSL_CONFIG_PATH = os.environ.get(
    "VECTOR_DSL_STRATEGY_CONFIG_PATH",
    "/root/strategy_configs/ai_dsl_strategies.json",
)
EXECUTION_COST_MODEL_PATH = os.environ.get(
    "QIYU_EXECUTION_COST_MODEL_PATH",
    "/root/auto_trade/execution_cost_model.json",
)
LOG_DIR = os.environ.get("VECTOR_BACKTEST_LOG_DIR", "/root/logs/backtest")
ENGINE_VERSION = "backtest_engine_v2_stage8_cost_aware_robust_validation"

os.makedirs(LOG_DIR, exist_ok=True)

def init_backtest_logger(strategy_name):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_file = os.path.join(LOG_DIR, f"{ts}_{strategy_name}.log")
    logger = logging.getLogger(f"BT_{ts}")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
        logger.addHandler(fh)
    return logger

def _sha256_file(path):
    p = Path(path)
    if not p.exists():
        return "missing"
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]

def load_strategy_params(strategy_name):
    p = Path(CONFIG_PATH)
    if not p.exists():
        return {}

    data = json.loads(p.read_text(encoding="utf-8"))
    for s in data.get("strategies", []):
        if s.get("key") == strategy_name:
            params = s.get("params", {})
            if not isinstance(params, dict):
                return {}
            out = {}
            for k, v in params.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out[k] = float(v)
            return out
    return {}

def load_strategy_metadata(strategy_name):
    for source in (CONFIG_PATH, DSL_CONFIG_PATH):
        p = Path(source)
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        for strategy in data.get("strategies", []):
            if strategy.get("key") == strategy_name:
                return dict(strategy)
    return {}


def load_execution_friction(inst_id, scenario="observed_base"):
    defaults = {"fee_rate_per_side": 0.0005,
                "slippage_rate_per_side": 0.0002,
                "half_spread_rate_per_side": 0.00005,
                "impact_rate_per_side": 0.00005,
                "latency_rate_per_side": 0.00005,
                "funding_rate_per_8h": 0.0001}
    try:
        payload = json.loads(Path(EXECUTION_COST_MODEL_PATH).read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    scenarios = payload.get("scenarios") or {}
    if scenario == "triple_actual":
        # Retrospective live-strategy audit: every currently modeled friction
        # component is tripled before instrument-specific drag and measured
        # execution floors are applied.  This is intentionally harsher than
        # the ordinary severe scenario and may never be used to make a losing
        # strategy look better.
        source = dict(scenarios.get("observed_base") or defaults)
        row = {key: 3.0*float(source.get(key, defaults[key]))
               for key in defaults}
    else:
        row = dict(scenarios.get(scenario) or defaults)
    multiplier = float((payload.get("execution_drag_multiplier") or {}).get(
        str(inst_id).upper(), 1.5))
    for key in ("slippage_rate_per_side", "half_spread_rate_per_side",
                "impact_rate_per_side", "latency_rate_per_side"):
        row[key] = float(row.get(key, defaults[key]))*multiplier
    row["fee_rate_per_side"] = float(row.get("fee_rate_per_side", .0005))
    row["funding_rate_per_8h"] = float(row.get("funding_rate_per_8h", 0.0))
    observed = ((payload.get("instrument_observed") or {}).get(
        str(inst_id).upper()) or {})
    percentile = {"observed_base": "p75", "stressed": "p90",
                  "severe": "p95", "triple_actual": "p95"}.get(
                      scenario, "p75")
    observed_candidates = {}
    if int(observed.get("book_samples") or 0) >= 24:
        measured = observed.get("half_spread_%s_rate" % percentile)
        try:
            measured = float(measured)
            row["half_spread_rate_per_side"] = max(
                row["half_spread_rate_per_side"], min(.003, measured))
            observed_candidates["half_spread_rate_per_side"] = measured
        except Exception:
            pass
    if int(observed.get("fill_samples") or 0) >= 10:
        measured = observed.get("adverse_mark_%s_rate" % percentile)
        try:
            measured = float(measured)
            row["slippage_rate_per_side"] = max(
                row["slippage_rate_per_side"], min(.003, measured))
            observed_candidates["slippage_rate_per_side"] = measured
        except Exception:
            pass
    if int(observed.get("funding_samples") or 0) >= 10:
        measured = observed.get("absolute_funding_%s_rate" % percentile)
        try:
            measured = float(measured)
            row["funding_rate_per_8h"] = max(
                row["funding_rate_per_8h"], min(.01, measured))
            observed_candidates["funding_rate_per_8h"] = measured
        except Exception:
            pass
    capacity = dict(payload.get("capacity_reference") or {})
    try:
        conservative_depth = min(
            float(observed.get("top5_bid_depth_p25_usd") or 0.0),
            float(observed.get("top5_ask_depth_p25_usd") or 0.0))
        modeled_notional = float(capacity.get(
            "maximum_modeled_notional_usdt") or 0.0)
    except Exception:
        conservative_depth = 0.0; modeled_notional = 0.0
    capacity_calibration = {"active": False}
    if (int(observed.get("depth_samples") or 0) >= 24
            and conservative_depth > 0 and modeled_notional > 0):
        utilization = modeled_notional/conservative_depth
        stress_multiplier = {"observed_base": 1.0, "stressed": 1.5,
                             "severe": 2.25}.get(scenario, 1.0)
        impact_floor = min(.005, row["half_spread_rate_per_side"]*
                           min(8.0, max(.25, math.sqrt(utilization)))*
                           stress_multiplier)
        row["impact_rate_per_side"] = max(
            row["impact_rate_per_side"], impact_floor)
        observed_candidates["capacity_impact_rate_per_side"] = impact_floor
        capacity_calibration = {
            "active": True, "modeled_notional_usdt": modeled_notional,
            "conservative_top5_depth_usd": conservative_depth,
            "depth_utilization": round(utilization, 8),
            "impact_floor_rate_per_side": round(impact_floor, 10),
        }
    row["scenario"] = scenario; row["execution_drag_multiplier"] = multiplier
    row["forward_calibration"] = {
        "observed_candidates": observed_candidates,
        "effective_terms": {
            "half_spread_rate_per_side": row["half_spread_rate_per_side"],
            "slippage_rate_per_side": row["slippage_rate_per_side"],
            "impact_rate_per_side": row["impact_rate_per_side"],
            "funding_rate_per_8h": row["funding_rate_per_8h"],
        },
        "book_samples": int(observed.get("book_samples") or 0),
        "fill_samples": int(observed.get("fill_samples") or 0),
        "depth_samples": int(observed.get("depth_samples") or 0),
        "funding_samples": int(observed.get("funding_samples") or 0),
        "capacity_calibration": capacity_calibration,
        "percentile": percentile,
    }
    return row

def P(params, key, default):
    v = params.get(key, default)
    try:
        return float(v)
    except Exception:
        return default

def _beijing_input_to_utc_naive(value):
    t = pd.Timestamp(value)
    if t.tzinfo is None:
        t = t.tz_localize("Asia/Shanghai")
    else:
        t = t.tz_convert("Asia/Shanghai")
    return t.tz_convert("UTC").tz_localize(None)

def normalize_timeframe(value):
    key = str(value or "1h").strip().lower().replace(" ", "")
    aliases = {
        "1h": "1h", "1hour": "1h", "60m": "1h", "60min": "1h",
        "4h": "4h", "4hour": "4h", "240m": "4h", "240min": "4h",
        "15m": "15m", "15min": "15m", "15minute": "15m",
        "5m": "5m", "5min": "5m", "5minute": "5m",
    }
    if key not in aliases:
        raise ValueError("不支持的回测周期：" + str(value))
    return aliases[key]

def normalize_instrument(value):
    key = str(value or "BTC-USDT-SWAP").strip().upper().replace("_", "-")
    # Compact aliases (BASE / BASEUSDT / BASE-USDT) → BASE-USDT-SWAP
    aliases = {
        "CLUSDT": "CL-USDT-SWAP", "NGUSDT": "NG-USDT-SWAP",
        "XAGUSDT": "XAG-USDT-SWAP", "LTCUSDT": "LTC-USDT-SWAP",
        "ADAUSDT": "ADA-USDT-SWAP", "ETHUSDT": "ETH-USDT-SWAP",
        "SOLUSDT": "SOL-USDT-SWAP", "XRPUSDT": "XRP-USDT-SWAP",
        "DOGEUSDT": "DOGE-USDT-SWAP", "BNBUSDT": "BNB-USDT-SWAP",
        "LINKUSDT": "LINK-USDT-SWAP", "AVAXUSDT": "AVAX-USDT-SWAP",
        "SUIUSDT": "SUI-USDT-SWAP", "DOTUSDT": "DOT-USDT-SWAP",
    }
    if key in aliases:
        return aliases[key]
    if key.endswith("-USDT-SWAP"):
        base = key[: -len("-USDT-SWAP")]
        if base and all(ch.isalnum() for ch in base):
            return key
    if key.endswith("-USDT"):
        base = key[: -len("-USDT")]
        if base and all(ch.isalnum() for ch in base):
            return base + "-USDT-SWAP"
    if key.endswith("USDT") and "-" not in key:
        base = key[: -len("USDT")]
        if base and all(ch.isalnum() for ch in base):
            return base + "-USDT-SWAP"
    if key and all(ch.isalnum() for ch in key) and not key.endswith("SWAP"):
        return key + "-USDT-SWAP"
    raise ValueError("不支持的回测标的：" + key)

def _local_data_directories(inst_id, timeframe):
    directories = [
        os.path.join(MARKET_DATA_ROOT, inst_id, timeframe),
    ]
    if inst_id == "BTC-USDT-SWAP" and timeframe == "1h":
        directories.append(os.path.join(MARKET_DATA_ROOT, "BTC-USDT", "1h"))
    return directories

def _find_local_data_directory(inst_id, timeframe):
    for directory in _local_data_directories(inst_id, timeframe):
        if not os.path.isdir(directory):
            continue
        if any(name.endswith(".parquet") for name in os.listdir(directory)):
            return directory
    return None

def load_parquet_range(data_dir, start, end, timeframe="1h"):
    timeframe = normalize_timeframe(timeframe)
    spec = TIMEFRAME_SPECS[timeframe]
    start_ts = _beijing_input_to_utc_naive(start)
    end_ts = _beijing_input_to_utc_naive(end)

    if not os.path.exists(data_dir):
        raise FileNotFoundError(f"【数据熔断】Parquet目录不存在: {data_dir}")

    files = sorted([os.path.join(data_dir, f) for f in os.listdir(data_dir) if f.endswith(".parquet")])
    if not files:
        raise FileNotFoundError(f"【数据熔断】Parquet目录为空，未发现任何 .parquet 文件: {data_dir}")

    dfs = []
    for f in files:
        df = pd.read_parquet(f)
        if not isinstance(df.index, pd.DatetimeIndex):
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.set_index("timestamp")
            else:
                raise ValueError(f"【字段错误】文件缺少 DatetimeIndex 或 timestamp 字段: {f}")
        dfs.append(df)

    df = pd.concat(dfs).sort_index()
    df = df[~df.index.duplicated(keep="first")]

    need_cols = ["open", "high", "low", "close"]
    miss = [c for c in need_cols if c not in df.columns]
    if miss:
        raise ValueError(f"【字段错误】Parquet缺少字段: {miss}")

    for c in need_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    if df[need_cols].isnull().values.any():
        raise ValueError("【数据错误】OHLC存在 NaN 或非数字值")

    if len(df) > 1:
        gaps = pd.Series(df.index).diff().dropna()
        if gaps.max() > spec["gap_tolerance"]:
            raise ValueError(
                "【时间轴异常】%s数据存在断层，最大断层: %s"
                % (spec["label"], gaps.max())
            )

    # Keep enough warm-up history for long indicators and higher-timeframe
    # context without including warm-up candles in the requested result.
    warm_start = start_ts - pd.Timedelta(days=90)
    df = df.loc[(df.index >= warm_start) & (df.index <= end_ts)]

    if df.empty:
        raise ValueError(f"【时序穿透阻断】指定区间无数据: {start} 至 {end}")

    if len(df) < 80:
        raise ValueError(f"【数据不足】有效K线不足，当前 {len(df)} 根")

    return df

def load_okx_swap_range(inst_id, start, end, timeframe="1h"):
    """Load confirmed candles from OKX when a local archive is unavailable."""
    import auto_trade_okx
    timeframe = normalize_timeframe(timeframe)
    spec = TIMEFRAME_SPECS[timeframe]
    start_ts = _beijing_input_to_utc_naive(start)
    end_ts = _beijing_input_to_utc_naive(end)
    warm_start = start_ts - pd.Timedelta(days=90)
    rows_by_ts = {}
    cursor = None
    for _ in range(spec["request_max_pages"]):
        params = {"instId": inst_id, "bar": spec["okx_bar"], "limit": "300"}
        if cursor is not None:
            params["after"] = str(cursor)
        raw = auto_trade_okx._okx_request(
            "GET", "/api/v5/market/history-candles", params=params, auth=False
        )
        if not isinstance(raw, dict) or str(raw.get("code")) != "0":
            raise RuntimeError("OKX历史K线读取失败: %s" % ((raw or {}).get("msg") if isinstance(raw, dict) else raw))
        batch = raw.get("data") or []
        if not batch:
            break
        oldest = None
        for row in batch:
            if not isinstance(row, list) or len(row) < 9 or str(row[8]) != "1":
                continue
            ts_ms = int(row[0])
            oldest = ts_ms if oldest is None else min(oldest, ts_ms)
            utc_ts = pd.to_datetime(ts_ms, unit="ms", utc=True).tz_localize(None)
            rec = {
                "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]),
            }
            # OKX history-candles: vol / volCcy / volCcyQuote at [5:8]
            if len(row) > 5:
                try:
                    rec["volume"] = float(row[5])
                except Exception:
                    pass
            rows_by_ts[utc_ts] = rec
        if oldest is None:
            break
        oldest_utc = pd.to_datetime(oldest, unit="ms", utc=True).tz_localize(None)
        if oldest_utc <= warm_start or cursor == oldest:
            break
        cursor = oldest
    if not rows_by_ts:
        raise ValueError(
            "【数据不足】OKX未返回%s的确认%s K线"
            % (inst_id, spec["label"])
        )
    df = pd.DataFrame.from_dict(rows_by_ts, orient="index").sort_index()
    df = df.loc[(df.index >= warm_start) & (df.index <= end_ts)]
    if len(df) < 80:
        raise ValueError("【数据不足】%s有效K线不足，当前 %d 根" % (inst_id, len(df)))
    gaps = pd.Series(df.index).diff().dropna()
    if not gaps.empty and gaps.max() > spec["gap_tolerance"]:
        raise ValueError(
            "【时间轴异常】%s %s数据存在断层，最大断层: %s"
            % (inst_id, spec["label"], gaps.max())
        )
    return df

def precompute_indicators(df, timeframe="1h"):
    df = df.copy()
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)

    for span in [6, 7, 8, 16, 17, 19, 21, 23, 32, 38, 53, 75, 95, 200]:
        df[f"ema{span}"] = c.ewm(span=span, adjust=False).mean()

    tp = (h + l + c) / 3
    ma = tp.rolling(62, min_periods=62).mean()
    md = tp.rolling(62, min_periods=62).apply(lambda x: abs(x - x.mean()).mean(), raw=False)
    df["cci"] = (tp - ma) / (0.015 * md)

    low_n = l.rolling(24, min_periods=24).min()
    high_n = h.rolling(24, min_periods=24).max()
    rsv = (c - low_n) / (high_n - low_n) * 100
    rsv = rsv.replace([np.inf, -np.inf], np.nan).fillna(50)

    df["k"] = rsv.ewm(alpha=1/3, adjust=False).mean()
    df["d"] = df["k"].ewm(alpha=1/3, adjust=False).mean()
    df["j"] = 3 * df["k"] - 2 * df["d"]

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    diff = ema12 - ema26
    dea = diff.ewm(span=9, adjust=False).mean()
    df["macd_stick"] = 2 * (diff - dea)

    previous_close = c.shift(1)
    true_range = pd.concat([
        h-l,
        (h-previous_close).abs(),
        (l-previous_close).abs(),
    ],axis=1).max(axis=1)
    df["atr14"] = true_range.rolling(14,min_periods=14).mean()
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    df["rsi14"] = 100 - 100/(1 + gain/loss.replace(0,np.nan))
    mean20 = c.rolling(20,min_periods=20).mean()
    std20 = c.rolling(20,min_periods=20).std()
    df["z20"] = (c-mean20)/std20.replace(0,np.nan)
    df["prev_high20"] = h.shift(1).rolling(20,min_periods=20).max()
    df["prev_low20"] = l.shift(1).rolling(20,min_periods=20).min()

    # vol_z20: true volume z-score when volume present; else bar-range z-score
    # proxy (documented as range_vol_proxy — participation/expansion stand-in).
    if "volume" in df.columns:
        vol = df["volume"].astype(float)
    else:
        vol = (h - l).astype(float)
    vol_mean = vol.rolling(20, min_periods=20).mean()
    vol_std = vol.rolling(20, min_periods=20).std()
    df["vol_z20"] = (vol - vol_mean) / vol_std.replace(0, np.nan)

    if normalize_timeframe(timeframe) in ("15m", "5m"):
        hourly = df[["open","high","low","close"]].resample(
            "1h",label="left",closed="left"
        ).agg({
            "open":"first",
            "high":"max",
            "low":"min",
            "close":"last",
        }).dropna()
        hourly_close = hourly["close"].astype(float)
        hourly_high = hourly["high"].astype(float)
        hourly_low = hourly["low"].astype(float)
        hourly_previous_close = hourly_close.shift(1)
        hourly_true_range = pd.concat([
            hourly_high-hourly_low,
            (hourly_high-hourly_previous_close).abs(),
            (hourly_low-hourly_previous_close).abs(),
        ],axis=1).max(axis=1)
        higher = pd.DataFrame(index=hourly.index)
        higher["h1_ema19"] = hourly_close.ewm(
            span=19,adjust=False
        ).mean()
        higher["h1_ema53"] = hourly_close.ewm(
            span=53,adjust=False
        ).mean()
        higher["h1_atr14"] = hourly_true_range.rolling(
            14,min_periods=14
        ).mean()
        higher["h1_slope4"] = higher["h1_ema19"].pct_change(4)
        # Only expose the previous fully closed hourly candle.
        higher = higher.shift(1).reindex(df.index,method="ffill")
        for column in higher.columns:
            df[column] = higher[column]

    return df

def _build_kwargs_base_for_e19(df):
    kwargs = {f"e{n}_arr": df[f"ema{n}"].values for n in [7, 8, 16, 17, 21, 23, 32, 38, 53]}
    kwargs.update({
        "cci_arr": df["cci"].values,
        "k_arr": df["k"].values,
        "d_arr": df["d"].values,
        "j_arr": df["j"].values,
        "stick_arr": df["macd_stick"].values,
    })
    return kwargs


def _build_kwargs(df):
    kw = _build_kwargs_base_for_e19(df)
    if kw is None:
        kw = {}

    if "close" in df.columns:
        close_series = df["close"].astype(float)
        for span in [7, 10, 15, 16, 17, 19, 21, 22, 23, 32, 37, 50, 53, 75, 95]:
            col = "ema" + str(span)
            if col in df.columns:
                arr = df[col].astype(float).values.tolist()
            else:
                arr = close_series.ewm(span=span, adjust=False).mean().astype(float).values.tolist()
            kw[col + "_arr"] = arr
            kw["e" + str(span) + "_arr"] = arr

    for col in [
        "k","d","j","cci","macd_stick","open","high","low","close",
        "atr14","h1_ema19","h1_ema53","h1_atr14",
        "rsi14","z20","prev_high20","prev_low20","h1_slope4",
    ]:
        if col in df.columns:
            try:
                kw[col + "_arr"] = df[col].astype(float).values.tolist()
            except Exception:
                pass

    if "ema6" in df.columns:
        kw["e6_arr"] = df["ema6"].astype(float).values.tolist()

    kw["timestamp_arr"] = df.index.tolist()
    # Read-only reference used solely by the validated data-only strategy DSL.
    # Existing Python strategies ignore this keyword through **kw.
    kw["_dsl_frame"] = df

    return kw


def _market_regime_labels(df, timeframe="1h"):
    timeframe = normalize_timeframe(timeframe)
    spec = TIMEFRAME_SPECS[timeframe]
    regime_bars = int(spec["regime_bars_90d"])
    minimum_bars = int(spec["regime_min_bars"])
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs(),
    ], axis=1).max(axis=1)
    atr_pct = true_range.rolling(14, min_periods=14).mean() / close
    volatility_reference = atr_pct.rolling(
        regime_bars, min_periods=minimum_bars
    ).median()
    return_90d = close / close.shift(regime_bars) - 1.0
    labels = []
    for index in range(len(df)):
        trend_value = return_90d.iloc[index]
        volatility_value = atr_pct.iloc[index]
        reference_value = volatility_reference.iloc[index]
        if pd.isna(trend_value):
            labels.append("unknown")
            continue
        if trend_value >= 0.15:
            trend = "bull"
        elif trend_value <= -0.15:
            trend = "bear"
        else:
            trend = "range"
        volatility = (
            "high_vol"
            if not pd.isna(reference_value) and volatility_value > reference_value
            else "normal_vol"
        )
        labels.append("%s_%s" % (trend, volatility))
    return labels
def _display_time(ts):
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M")

def _native(obj):
    if isinstance(obj, dict):
        return {k: _native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_native(x) for x in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj

def _state(idx, o, c, h, l, kwargs):
    return sl._get_state(idx, o, c, h, l, kwargs)

def entry_cci_75_100_long_p(o,c,h,l,idx,params,**kw):
    if idx < 60 or c[idx] <= o[idx]:
        return False, {}
    s = _state(idx,o,c,h,l,kw)
    e38_arr = kw.get("e38_arr")
    e38_m24 = e38_arr[idx-24] if e38_arr is not None else sl.ema(c[:idx-23],38)
    ema38_growth_24h_pct = (s["e38"]/e38_m24-1.0)*100.0
    if not (s["e7"] > s["e23"] > s["e38"]):
        return False, {}
    if not (l[idx] <= s["e7"] and c[idx] >= s["e7"]):
        return False, {}
    if not (l[idx] > s["e38"] and l[idx-1] > s["e38_m1"]):
        return False, {}
    if not (s["k"] > s["d"] and s["k"]-s["d"] < P(params,"kd_diff_max_exclusive",5)):
        return False, {}
    if not (P(params,"cci_entry_min",20) <= s["cci"] <= P(params,"cci_entry_max",100)):
        return False, {}
    if not (s["stick"] > 0 and s["stick"] > s["stick_m1"]):
        return False, {}
    if not (ema38_growth_24h_pct >= P(params,"ema38_growth_24h_min_pct",0.2)-1e-12):
        return False, {}
    return True, {
        "price": c[idx],
        "entry_cci": float(s["cci"]),
        "entry_k": float(s["k"]),
        "ema38_growth_24h_pct": float(ema38_growth_24h_pct),
    }

def exit_cci_75_100_long_p(c,h,l,idx,entry,params,**kw):
    try:
        entry_price = float((entry or {}).get("price"))
    except (TypeError, ValueError):
        return False, {}
    take_profit_price = entry_price*(1.0+P(params,"price_take_profit_pct",0.006))
    if h[idx] >= take_profit_price:
        return True, {"price":take_profit_price, "exit_type":"价格止盈"}
    s = _state(idx,[],c,h,l,kw)
    if (
        s["cci"] >= P(params,"cci_exit_greater_equal",160)
        or s["k"] >= P(params,"k_exit_greater_equal",80)
    ):
        return True, {"price":c[idx], "exit_type":"指标止盈"}
    return False, {}

def entry_ema7_break_long_p(o,c,h,l,idx,params,**kw):
    if idx < 60: return False, {}
    s = _state(idx,o,c,h,l,kw)
    return ((s["e32"] < s["e23"] < s["e8"] < s["e7"]) and (o[idx] < s["e7"] and c[idx] > s["e7"]) and P(params,"cci_entry_min",40) <= s["cci"] <= P(params,"cci_entry_max",110), {"price": c[idx]}) if ((s["e32"] < s["e23"] < s["e8"] < s["e7"]) and (o[idx] < s["e7"] and c[idx] > s["e7"]) and P(params,"cci_entry_min",40) <= s["cci"] <= P(params,"cci_entry_max",110)) else (False, {})

def exit_ema7_break_long_p(c,h,l,idx,entry,params,**kw):
    s = _state(idx,[],c,h,l,kw)
    return s["k"] >= P(params,"k_exit_greater_equal",86.5) or s["cci"] > P(params,"cci_exit_greater_than",135)

def entry_ema7_break_short_p(o,c,h,l,idx,params,**kw):
    if idx < 60: return False, {}
    s = _state(idx,o,c,h,l,kw)
    met = (s["e7"] < s["e8"] < s["e23"] < s["e32"]) and (o[idx] > s["e7"] and c[idx] < s["e7"]) and P(params,"cci_entry_min",-110) <= s["cci"] <= P(params,"cci_entry_max",-40)
    return (True, {"price": c[idx]}) if met else (False, {})

def exit_ema7_break_short_p(c,h,l,idx,entry,params,**kw):
    s = _state(idx,[],c,h,l,kw)
    return s["k"] <= P(params,"k_exit_less_equal",13.5) or s["cci"] < P(params,"cci_exit_less_than",-135)

def entry_ema8_mainwave_long_p(o,c,h,l,idx,params,**kw):
    if idx < 60: return False, {}
    s = _state(idx,o,c,h,l,kw)
    met = (s["e32"] > s["e7"] > s["e8"]) and (P(params,"k_entry_min",45) <= s["k"] <= P(params,"k_entry_max",70) and s["d"] < P(params,"d_entry_less_than",70) and s["j"] < P(params,"j_entry_less_than",85)) and s["stick"] > 0 and (s["cci_m1"] <= P(params,"cci_cross_level",-60) and s["cci"] > P(params,"cci_cross_level",-60))
    return (True, {"price": c[idx]}) if met else (False, {})

def exit_ema8_mainwave_long_p(c,h,l,idx,entry,params,**kw):
    s = _state(idx,[],c,h,l,kw)
    return (s["k"] > 78 and s["j"] > 78) or s["j"] > P(params,"j_exit_greater_than",105)

def entry_ema8_mainwave_short_p(o,c,h,l,idx,params,**kw):
    if idx < 60: return False, {}
    s = _state(idx,o,c,h,l,kw)
    met = (s["e32"] < s["e7"] < s["e8"]) and (P(params,"k_entry_min",30) <= s["k"] <= P(params,"k_entry_max",55) and s["d"] > P(params,"d_entry_greater_than",30) and s["j"] > P(params,"j_entry_greater_than",15)) and s["stick"] < 0 and (s["cci_m1"] >= P(params,"cci_cross_level",60) and s["cci"] < P(params,"cci_cross_level",60))
    return (True, {"price": c[idx]}) if met else (False, {})

def exit_ema8_mainwave_short_p(c,h,l,idx,entry,params,**kw):
    s = _state(idx,[],c,h,l,kw)
    return s["k"] < P(params,"k_exit_less_than",22) and s["j"] < P(params,"j_exit_less_than",22)

def entry_ema7_cross_long_p(o,c,h,l,idx,params,**kw):
    if idx < 60: return False, {}
    s = _state(idx,o,c,h,l,kw)
    met = (s["e7"] > s["e23"] > s["e32"]) and (min(s["e8"], s["e32"]) <= c[idx] <= max(s["e8"], s["e32"])) and (c[idx] > o[idx] and o[idx] < s["e7"] and c[idx] > s["e7"]) and (P(params,"j_entry_min",21) <= s["j"] <= P(params,"j_entry_max",50) and P(params,"cci_entry_min",-110) <= s["cci"] <= P(params,"cci_entry_max",-50))
    return (True, {"price": c[idx]}) if met else (False, {})

def exit_ema7_cross_long_p(c,h,l,idx,entry,params,**kw):
    return _state(idx,[],c,h,l,kw)["j"] > P(params,"j_exit_greater_than",50)

def entry_cci_110_135_long_p(o,c,h,l,idx,params,**kw):
    if idx < 60 or c[idx] <= o[idx]: return False, {}
    s = _state(idx,o,c,h,l,kw)
    met = (l[idx] <= s["e7"] <= h[idx]) and (s["e7"] > s["e23"] > s["e38"]) and (s["k"] > s["d"] and s["stick"] > 0) and (P(params,"cci_entry_min",110) <= s["cci"] <= P(params,"cci_entry_max",135)) and sl._no_ema_cross_10(idx,kw,c)
    return (True, {"price": c[idx]}) if met else (False, {})

def exit_cci_110_135_long_p(c,h,l,idx,entry,params,**kw):
    return _state(idx,[],c,h,l,kw)["cci"] > P(params,"cci_exit_greater_than",145)

def entry_j_cross_79_short_p(o,c,h,l,idx,params,**kw):
    if idx < 60 or c[idx] >= o[idx]: return False, {}
    s = _state(idx,o,c,h,l,kw)
    level = P(params,"j_cross_level",79)
    met = (l[idx] <= s["e7"] <= h[idx]) and (s["e7"] > s["e23"] > s["e38"]) and (s["k"] > s["d"] and s["k"] < P(params,"k_entry_less_than",85)) and (s["j_m1"] >= level and s["j"] < level) and sl._no_ema_cross_10(idx,kw,c)
    return (True, {"price": c[idx]}) if met else (False, {})

def exit_j_cross_79_short_p(c,h,l,idx,entry,params,**kw):
    return _state(idx,[],c,h,l,kw)["j"] <= P(params,"j_exit_less_equal",56)

def entry_cci75_110_long_p(o,c,h,l,idx,params,**kw):
    if idx < 60 or c[idx] <= o[idx]: return False, {}
    s = _state(idx,o,c,h,l,kw)
    met = (l[idx] <= s["e7"] <= h[idx]) and (P(params,"cci_entry_min",-110) <= s["cci"] <= P(params,"cci_entry_max",-75)) and (P(params,"k_entry_min",30) < s["k"] < P(params,"k_entry_max",45) and s["k"] > s["d"]) and (s["stick"] > 0 and s["stick"] > s["stick_m1"]) and (s["e7"] < s["e23"] < s["e38"]) and sl._no_ema_cross_10(idx,kw,c)
    return (True, {"price": c[idx]}) if met else (False, {})

def exit_cci75_110_long_p(c,h,l,idx,entry,params,**kw):
    s = _state(idx,[],c,h,l,kw)
    return s["k"] > P(params,"k_exit_greater_than",47) or s["j"] > P(params,"j_exit_greater_than",53)

def entry_j_cross_7_8_long_p(o,c,h,l,idx,params,**kw):
    if idx < 60: return False, {}
    s = _state(idx,o,c,h,l,kw)
    level = P(params,"j_cross_level",7.8)
    met = (P(params,"cci_entry_min",-250) <= s["cci"] <= P(params,"cci_entry_max",-100)) and (s["j_m1"] < level and s["j"] >= level) and (s["d"] < P(params,"d_entry_less_than",20) and s["k"] < s["d"]) and (s["e7"] < s["e23"] < s["e38"]) and sl._no_ema_cross_10(idx,kw,c)
    return (True, {"price": c[idx]}) if met else (False, {})

def exit_j_cross_7_8_long_p(c,h,l,idx,entry,params,**kw):
    return _state(idx,[],c,h,l,kw)["j"] >= P(params,"j_exit_greater_equal",25)

def entry_cci_less_neg170_long_p(o,c,h,l,idx,params,**kw):
    if idx < 60 or c[idx] <= o[idx]: return False, {}
    s = _state(idx,o,c,h,l,kw)
    level = P(params,"k_cross_level",15)
    met = (s["cci"] < P(params,"cci_entry_less_than",-170)) and (s["k_m1"] < level and s["k"] >= level) and (s["k"] < s["d"] and c[idx] < o[idx-1]) and sl._no_ema_cross_10(idx,kw,c)
    return (True, {"price": c[idx]}) if met else (False, {})

def exit_cci_less_neg170_long_p(c,h,l,idx,entry,params,**kw):
    return _state(idx,[],c,h,l,kw)["j"] >= P(params,"j_exit_greater_equal",50)

def entry_cci_neg60_neg110_short_p(o,c,h,l,idx,params,**kw):
    if idx < 60 or c[idx] >= o[idx]: return False, {}
    s = _state(idx,o,c,h,l,kw)
    met = (l[idx] <= s["e7"] <= h[idx]) and (s["e7"] < s["e23"] < s["e38"]) and (s["d"] < P(params,"d_entry_less_than",40) and s["k"] <= s["d"] and s["d"] - s["k"] <= P(params,"d_minus_k_max",2)) and (P(params,"cci_entry_min",-110) <= s["cci"] <= P(params,"cci_entry_max",-60)) and sl._no_ema_cross_10(idx,kw,c)
    return (True, {"price": c[idx]}) if met else (False, {})

def exit_cci_neg60_neg110_short_p(c,h,l,idx,entry,params,**kw):
    return _state(idx,[],c,h,l,kw)["j"] <= P(params,"j_exit_less_equal",18)

def entry_cci_neg110_neg180_long_p(o,c,h,l,idx,params,**kw):
    if idx < 60: return False, {}
    s = _state(idx,o,c,h,l,kw)
    met = (c[idx] <= s["e7"]) and (s["k"] <= s["d"] and s["k"] < P(params,"k_entry_less_than",30) and s["d"] < P(params,"d_entry_less_than",30) and s["j"] < P(params,"j_entry_less_than",30)) and (P(params,"cci_entry_min",-180) <= s["cci"] <= P(params,"cci_entry_max",-110)) and (s["cci"] > s["cci_m1"] and s["k"] > s["k_m1"] and s["d"] > s["d_m1"] and s["j"] > s["j_m1"]) and (s["e7"] < s["e23"] < s["e38"]) and sl._no_ema_cross_10(idx,kw,c)
    return (True, {"price": c[idx]}) if met else (False, {})

def exit_cci_neg110_neg180_long_p(c,h,l,idx,entry,params,**kw):
    return _state(idx,[],c,h,l,kw)["j"] >= P(params,"j_exit_greater_equal",48)

def entry_ema7_ema23_down_short_p(o,c,h,l,idx,params,**kw):
    if idx < 60 or c[idx] >= o[idx]: return False, {}
    s = _state(idx,o,c,h,l,kw)
    met = (s["e7"] < s["e53"] and s["e23"] < s["e53"]) and (l[idx] <= s["e7"] <= h[idx] and l[idx] <= s["e23"] <= h[idx]) and (c[idx] < s["e7"]) and (P(params,"k_entry_min",50) <= s["k"] < P(params,"k_entry_max",60) and s["j"] > P(params,"j_entry_greater_than",37) and s["k"] > P(params,"k_entry_greater_than",37)) and (P(params,"kd_diff_min",3) < s["k"] - s["d"] < P(params,"kd_diff_max",8)) and (h[idx] < s["e53"] and h[idx-1] < s["e53_m1"])
    return (True, {"price": c[idx]}) if met else (False, {})

def exit_ema7_ema23_down_short_p(c,h,l,idx,entry,params,**kw):
    s = _state(idx,[],c,h,l,kw)
    return s["k"] <= P(params,"k_exit_less_equal",35) and s["j"] <= P(params,"j_exit_less_equal",35)

def entry_ema7_center_down_short_p(o,c,h,l,idx,params,**kw):
    if idx < 60 or c[idx] >= o[idx]: return False, {}
    s = _state(idx,o,c,h,l,kw)

    cci_arr_max = kw.get("cci_arr")
    prev_cci_max_lookback = int(P(params,"lookback_prev_cci_max_lt_bars",5))
    prev_cci_max_lt_level = P(params,"prev_cci_max_less_than",3.5)
    prev_cci_vals_for_max = []
    for kk in range(idx - prev_cci_max_lookback, idx):
        if kk < 0:
            return False, {}
        cur_cci_for_max = cci_arr_max[kk] if cci_arr_max is not None else _state(kk,o,c,h,l,kw)["cci"]
        prev_cci_vals_for_max.append(cur_cci_for_max)
    if not prev_cci_vals_for_max:
        return False, {}
    if not (max(prev_cci_vals_for_max) < prev_cci_max_lt_level):
        return False, {}


    j_arr_trigger = kw.get("j_arr")
    trigger_j_lookback = int(P(params,"lookback_trigger_j_lt_bars",5))
    trigger_j_lt_level = P(params,"trigger_j_less_than",106.5)
    for jj in range(idx - trigger_j_lookback, idx + 1):
        if jj < 0:
            return False, {}
        cur_j_trigger = j_arr_trigger[jj] if j_arr_trigger is not None else _state(jj,o,c,h,l,kw)["j"]
        if not (cur_j_trigger < trigger_j_lt_level):
            return False, {}


    signal_d_less_than = P(params,"signal_d_less_than",76)
    if not (s["d"] < signal_d_less_than): return False, {}

    cci_arr_prev = kw.get("cci_arr")
    prev_cci_lookback = int(P(params,"lookback_prev_cci_gt_bars",5))
    prev_cci_gt_threshold = P(params,"prev_cci_greater_than",-50)
    prev_cci_ok = False
    for j in range(idx - prev_cci_lookback, idx):
        if j < 0:
            return False, {}
        cur_cci_prev = cci_arr_prev[j] if cci_arr_prev is not None else _state(j,o,c,h,l,kw)["cci"]
        if cur_cci_prev > prev_cci_gt_threshold:
            prev_cci_ok = True
            break
    if not prev_cci_ok: return False, {}



    k_arr, d_arr, j_arr = kw.get("k_arr"), kw.get("d_arr"), kw.get("j_arr")
    if idx <= 0:
        return False, {}
    prev_k_value = k_arr[idx - 1] if k_arr is not None else _state(idx-1,o,c,h,l,kw)["k"]
    signal_k_drop_min = P(params,"signal_k_drop_min",1.5)
    if not ((prev_k_value - s["k"]) >= signal_k_drop_min): return False, {}

    if not (s["cci"] < P(params,"signal_cci_less_than",-15)): return False, {}
    if not (P(params,"kd_diff_min",-3) <= (s["k"] - s["d"]) <= P(params,"kd_diff_max",6)): return False, {}
    if not (s["e6"] < s["e53"]): return False, {}

    e95_arr = kw.get("e95_arr")
    cur_e95 = e95_arr[idx] if e95_arr is not None else sl.ema(c[:idx+1],95)
    if not (h[idx] < cur_e95): return False, {}

    prev_lookback = int(P(params,"lookback_prev_k_ge_d_bars",5))
    for i in range(idx - prev_lookback, idx):
        if i < 0:
            return False, {}
        if k_arr is not None and d_arr is not None:
            cur_k, cur_d = k_arr[i], d_arr[i]
        else:
            ps = _state(i,o,c,h,l,kw)
            cur_k, cur_d = ps["k"], ps["d"]
        if cur_k < cur_d:
            return False, {}

    cond_met = False
    e6_arr, e19_arr, e53_arr = kw.get("e6_arr"), kw.get("e19_arr"), kw.get("e53_arr")
    lookback = int(P(params,"lookback_ema_order_bars",5))
    for i in range(idx - lookback, idx + 1):
        if i < 0: continue
        cur_e6 = e6_arr[i] if e6_arr is not None else sl.ema(c[:i+1],6)
        cur_e19 = e19_arr[i] if e19_arr is not None else sl.ema(c[:i+1],19)
        cur_e53 = e53_arr[i] if e53_arr is not None else sl.ema(c[:i+1],53)
        if cur_e53 > cur_e6 > cur_e19:
            cond_met = True
            break
    if not cond_met: return False, {}

    green_lookback = int(P(params,"lookback_green_bars",5))
    required = int(P(params,"required_green_close_count",3))
    qualified_green = []
    for i in range(idx - green_lookback, idx):
        if i < 0:
            continue
        cur_e19 = e19_arr[i] if e19_arr is not None else sl.ema(c[:i+1],19)
        if c[i] > o[i] and c[i] > cur_e19 and c[idx] < c[i]:
            qualified_green.append(i)
    if len(qualified_green) < required: return False, {}

    return True, {"price": c[idx], "tp_class": 2, "entry_j": s["j"], "qualified_green": qualified_green}
def exit_ema7_center_down_short_p(c,h,l,idx,entry,params,**kw):
    s = _state(idx,c,c,h,l,kw)
    j_exit_level = P(params,"class2_j_exit_less_than",36.5)
    d_exit_level = P(params,"exit_d_less_than",62)
    cci_exit_level = P(params,"exit_cci_less_than",-89)
    if s["j"] < j_exit_level and s["d"] < d_exit_level and s["cci"] < cci_exit_level:
        return True, {"price": c[idx], "exit_type": "\u6b62\u76c8\uff08j\u5c0f\u4e8e36.5\u4e14d\u5c0f\u4e8e62\u4e14cci\u5c0f\u4e8e\u8d1f89\uff09"}
    return False, {}

def entry_ema7_center_up_long_p(o,c,h,l,idx,params,**kw):
    if idx < 95 or c[idx] <= o[idx]: return False, {}
    s = _state(idx,o,c,h,l,kw)
    k_arr, d_arr = kw.get("k_arr"), kw.get("d_arr")
    cci_arr, j_arr = kw.get("cci_arr"), kw.get("j_arr")
    e6_arr, e19_arr, e53_arr = kw.get("e6_arr"), kw.get("e19_arr"), kw.get("e53_arr")
    e95_arr = kw.get("e95_arr")

    lookback = int(P(params,"lookback_bars",5))
    prev_idx = range(idx-lookback,idx)
    if idx-lookback < 0: return False, {}

    # Previous five CCI values: at least one below 50, while their minimum is
    # strictly above -3.5.
    prev_cci = [cci_arr[i] if cci_arr is not None else _state(i,o,c,h,l,kw)["cci"] for i in prev_idx]
    if not any(x < P(params,"prev_cci_less_than",50) for x in prev_cci): return False, {}
    if not (min(prev_cci) > P(params,"prev_cci_min_greater_than",-3.5)): return False, {}

    # Trigger candle and previous five J values are all above -6.5.
    for i in range(idx-lookback,idx+1):
        cur_j = j_arr[i] if j_arr is not None else _state(i,o,c,h,l,kw)["j"]
        if not (cur_j > P(params,"trigger_j_greater_than",-6.5)): return False, {}

    if not (s["d"] > P(params,"signal_d_greater_than",24)): return False, {}
    prev_k = k_arr[idx-1] if k_arr is not None else _state(idx-1,o,c,h,l,kw)["k"]
    if not ((s["k"]-prev_k) >= P(params,"signal_k_rise_min",1.5)): return False, {}
    if not (s["cci"] > P(params,"signal_cci_greater_than",15)): return False, {}
    if not (P(params,"dk_diff_min",-3) <= (s["d"]-s["k"]) <= P(params,"dk_diff_max",6)): return False, {}

    # Every previous candle must have D >= K.
    for i in range(idx-lookback,idx):
        cur_k = k_arr[i] if k_arr is not None else _state(i,o,c,h,l,kw)["k"]
        cur_d = d_arr[i] if d_arr is not None else _state(i,o,c,h,l,kw)["d"]
        if cur_d < cur_k: return False, {}

    if not (s["e6"] > s["e53"]): return False, {}
    cur_e95 = e95_arr[idx] if e95_arr is not None else sl.ema(c[:idx+1],95)
    if not (l[idx] > cur_e95): return False, {}

    # Current or previous five candles contain EMA53 < EMA6 < EMA19.
    order_met = False
    for i in range(idx-lookback,idx+1):
        e6 = e6_arr[i] if e6_arr is not None else sl.ema(c[:i+1],6)
        e19 = e19_arr[i] if e19_arr is not None else sl.ema(c[:i+1],19)
        e53 = e53_arr[i] if e53_arr is not None else sl.ema(c[:i+1],53)
        if e53 < e6 < e19:
            order_met = True
            break
    if not order_met: return False, {}

    # Signal close is above at least three prior bearish closes, each below its
    # own EMA19.
    qualified_red = []
    for i in range(idx-lookback,idx):
        e19 = e19_arr[i] if e19_arr is not None else sl.ema(c[:i+1],19)
        if c[i] < o[i] and c[i] < e19 and c[idx] > c[i]:
            qualified_red.append(i)
    if len(qualified_red) < int(P(params,"required_bearish_close_count",3)): return False, {}

    return True, {"price":c[idx],"entry_j":s["j"],"qualified_bearish":qualified_red}

def exit_ema7_center_up_long_p(c,h,l,idx,entry,params,**kw):
    s = _state(idx,c,c,h,l,kw)
    if (s["j"] > P(params,"exit_j_greater_than",53.5)
            and s["d"] > P(params,"exit_d_greater_than",38)
            and s["cci"] > P(params,"exit_cci_greater_than",89)):
        return True, {"price":c[idx],"exit_type":"止盈"}
    return False, {}

def entry_conventional_up_break_long_p(o,c,h,l,idx,params,**kw):
    if idx < 75: return False, {}
    s = _state(idx,o,c,h,l,kw)
    k_arr, d_arr, j_arr = kw.get("k_arr"), kw.get("d_arr"), kw.get("j_arr")
    cci_arr = kw.get("cci_arr")
    stick_arr = kw.get("stick_arr")
    e6_arr, e19_arr = kw.get("e6_arr"), kw.get("e19_arr")
    e17_arr = kw.get("e17_arr")
    e53_arr, e75_arr = kw.get("e53_arr"), kw.get("e75_arr")

    k_now = k_arr[idx] if k_arr is not None else s["k"]
    d_now = d_arr[idx] if d_arr is not None else s["d"]
    k_prev = k_arr[idx-1] if k_arr is not None else _state(idx-1,o,c,h,l,kw)["k"]
    if not ((k_now-k_prev) > 0): return False, {}

    cci_now = cci_arr[idx] if cci_arr is not None else s["cci"]

    stick_now = stick_arr[idx] if stick_arr is not None else s["stick"]
    if not (stick_now >= P(params,"signal_stick_min",0)): return False, {}

    prior_cci_lookback = int(P(params,"prior_cci_non_decreasing_lookback",5))
    if idx-prior_cci_lookback < 0: return False, {}
    prior_cci = [
        cci_arr[i] if cci_arr is not None else _state(i,o,c,h,l,kw)["cci"]
        for i in range(idx-prior_cci_lookback,idx)
    ]
    strictly_decreasing = all(
        prior_cci[i] < prior_cci[i-1] for i in range(1,len(prior_cci))
    )
    if strictly_decreasing: return False, {}

    prior_cci_above_lookback = int(P(params,"prior_cci_above_lookback",8))
    if idx-prior_cci_above_lookback < 0: return False, {}
    for i in range(idx-prior_cci_above_lookback,idx):
        cur_cci = cci_arr[i] if cci_arr is not None else _state(i,o,c,h,l,kw)["cci"]
        if not (cur_cci > P(params,"prior_cci_strictly_greater_than",50)):
            return False, {}

    kd_compare_start = int(P(params,"kd_compare_start_lookback",8))
    kd_compare_end = int(P(params,"kd_compare_end_lookback",10))
    if kd_compare_start < 1 or kd_compare_end < kd_compare_start or idx-kd_compare_end < 0:
        return False, {}
    for lookback in range(kd_compare_start,kd_compare_end+1):
        compare_idx = idx-lookback
        compare_k = k_arr[compare_idx] if k_arr is not None else _state(compare_idx,o,c,h,l,kw)["k"]
        compare_d = d_arr[compare_idx] if d_arr is not None else _state(compare_idx,o,c,h,l,kw)["d"]
        if not (k_now > compare_k and d_now > compare_d):
            return False, {}

    e6 = e6_arr[idx] if e6_arr is not None else sl.ema(c[:idx+1],6)
    e19 = e19_arr[idx] if e19_arr is not None else sl.ema(c[:idx+1],19)
    e53 = e53_arr[idx] if e53_arr is not None else sl.ema(c[:idx+1],53)
    e75 = e75_arr[idx] if e75_arr is not None else sl.ema(c[:idx+1],75)
    if not (e6 > e19 > e53 > e75): return False, {}

    # The signal candle must trade at or below EMA6 and close at or above it.
    if not (l[idx] <= e6 and c[idx] >= e6): return False, {}

    d_lookback = int(P(params,"lookback_prev_d_bars",3))
    if idx-d_lookback < 0: return False, {}
    for i in range(idx-d_lookback,idx):
        cur_d = d_arr[i] if d_arr is not None else _state(i,o,c,h,l,kw)["d"]
        if cur_d < P(params,"prev_d_min",50): return False, {}

    prior_kd_lookback = int(P(params,"prior_kd_min_lookback",2))
    if idx-prior_kd_lookback < 0: return False, {}
    prior_kd_diffs = []
    for i in range(idx-prior_kd_lookback,idx):
        cur_k = k_arr[i] if k_arr is not None else _state(i,o,c,h,l,kw)["k"]
        cur_d = d_arr[i] if d_arr is not None else _state(i,o,c,h,l,kw)["d"]
        prior_kd_diffs.append(cur_k-cur_d)
    if not (min(prior_kd_diffs) < P(params,"prior_kd_min_less_than",1.1)):
        return False, {}

    ema53_lookback = int(P(params,"ema53_low_lookback",3))
    if idx-ema53_lookback < 0: return False, {}

    # The signal candle and each of its previous three candles must remain
    # completely above their corresponding EMA53 values.
    for i in range(idx-ema53_lookback,idx+1):
        cur_e53 = e53_arr[i] if e53_arr is not None else sl.ema(c[:i+1],53)
        if not (l[i] > cur_e53): return False, {}

    # At least one of the previous two candles must both have a J value that
    # fell by the threshold and a low above that candle's EMA17.
    prior_kdj_lookback = int(P(params,"prior_kdj_lookback",2))
    if idx-prior_kdj_lookback-1 < 0: return False, {}
    prior_j_decrease_found = False
    for i in range(idx-prior_kdj_lookback,idx):
        cur_j = j_arr[i] if j_arr is not None else _state(i,o,c,h,l,kw)["j"]
        prev_j = j_arr[i-1] if j_arr is not None else _state(i-1,o,c,h,l,kw)["j"]
        cur_e17 = e17_arr[i] if e17_arr is not None else sl.ema(c[:i+1],17)
        if (prev_j-cur_j >= P(params,"prior_j_net_decrease_min",2)
                and l[i] > cur_e17):
            prior_j_decrease_found = True
            break
    if not prior_j_decrease_found: return False, {}

    j_now = j_arr[idx] if j_arr is not None else s["j"]
    j_prev = j_arr[idx-1] if j_arr is not None else _state(idx-1,o,c,h,l,kw)["j"]
    if not ((j_now-j_prev) <= P(params,"j_net_increase_max",15)):
        return False, {}

    return True, {
        "price":c[idx],
        "entry_j":j_now,
        "entry_cci":float(cci_now),
        "entry_signal_idx":int(idx),
    }

def exit_conventional_up_break_long_p(c,h,l,idx,entry,params,**kw):
    cci_arr = kw.get("cci_arr")
    d_arr = kw.get("d_arr")
    current_cci = cci_arr[idx] if cci_arr is not None else _state(idx,c,c,h,l,kw)["cci"]
    current_d = d_arr[idx] if d_arr is not None else _state(idx,c,c,h,l,kw)["d"]
    entry_cci = float(entry.get("entry_cci")) if isinstance(entry,dict) and entry.get("entry_cci") is not None else None
    cci_exit = entry_cci is not None and current_cci >= entry_cci + P(params,"exit_cci_increase",50)
    d_exit = current_d >= P(params,"exit_d_greater_equal",89.7)
    if cci_exit or d_exit:
        return True, {"price":c[idx],"exit_type":"止盈"}
    entry_signal_idx = entry.get("entry_signal_idx") if isinstance(entry,dict) else None
    max_hold_hours = int(P(params,"max_hold_hours_without_exit",8))
    if entry_signal_idx is not None and idx-int(entry_signal_idx) >= max_hold_hours+1:
        return True, {"price":c[idx],"exit_type":"定时强制平仓"}
    return False, {}

def entry_conventional_down_break_short_p(o,c,h,l,idx,params,**kw):
    if idx < 75: return False, {}
    s = _state(idx,o,c,h,l,kw)
    k_arr, d_arr, j_arr = kw.get("k_arr"), kw.get("d_arr"), kw.get("j_arr")
    cci_arr = kw.get("cci_arr")
    stick_arr = kw.get("stick_arr")
    e6_arr, e19_arr = kw.get("e6_arr"), kw.get("e19_arr")
    e17_arr = kw.get("e17_arr")
    e53_arr, e75_arr = kw.get("e53_arr"), kw.get("e75_arr")

    k_now = k_arr[idx] if k_arr is not None else s["k"]
    d_now = d_arr[idx] if d_arr is not None else s["d"]
    k_prev = k_arr[idx-1] if k_arr is not None else _state(idx-1,o,c,h,l,kw)["k"]
    if not ((k_now-k_prev) < 0): return False, {}

    cci_now = cci_arr[idx] if cci_arr is not None else s["cci"]

    stick_now = stick_arr[idx] if stick_arr is not None else s["stick"]
    if not (stick_now <= P(params,"signal_stick_max",0)): return False, {}

    prior_cci_lookback = int(P(params,"prior_cci_non_increasing_lookback",5))
    if idx-prior_cci_lookback < 0: return False, {}
    prior_cci = [
        cci_arr[i] if cci_arr is not None else _state(i,o,c,h,l,kw)["cci"]
        for i in range(idx-prior_cci_lookback,idx)
    ]
    strictly_increasing = all(
        prior_cci[i] > prior_cci[i-1] for i in range(1,len(prior_cci))
    )
    if strictly_increasing: return False, {}

    prior_cci_below_lookback = int(P(params,"prior_cci_below_lookback",8))
    if idx-prior_cci_below_lookback < 0: return False, {}
    for i in range(idx-prior_cci_below_lookback,idx):
        cur_cci = cci_arr[i] if cci_arr is not None else _state(i,o,c,h,l,kw)["cci"]
        if not (cur_cci < P(params,"prior_cci_strictly_less_than",-50)):
            return False, {}

    kd_compare_start = int(P(params,"kd_compare_start_lookback",8))
    kd_compare_end = int(P(params,"kd_compare_end_lookback",10))
    if kd_compare_start < 1 or kd_compare_end < kd_compare_start or idx-kd_compare_end < 0:
        return False, {}
    for lookback in range(kd_compare_start,kd_compare_end+1):
        compare_idx = idx-lookback
        compare_k = k_arr[compare_idx] if k_arr is not None else _state(compare_idx,o,c,h,l,kw)["k"]
        compare_d = d_arr[compare_idx] if d_arr is not None else _state(compare_idx,o,c,h,l,kw)["d"]
        if not (k_now < compare_k and d_now < compare_d):
            return False, {}

    e6 = e6_arr[idx] if e6_arr is not None else sl.ema(c[:idx+1],6)
    e19 = e19_arr[idx] if e19_arr is not None else sl.ema(c[:idx+1],19)
    e53 = e53_arr[idx] if e53_arr is not None else sl.ema(c[:idx+1],53)
    e75 = e75_arr[idx] if e75_arr is not None else sl.ema(c[:idx+1],75)
    if not (e6 < e19 < e53 < e75): return False, {}

    # The signal candle must trade strictly above EMA6 and close at or below it.
    if not (h[idx] > e6 and c[idx] <= e6): return False, {}

    d_lookback = int(P(params,"lookback_prev_d_bars",3))
    if idx-d_lookback < 0: return False, {}
    for i in range(idx-d_lookback,idx):
        cur_d = d_arr[i] if d_arr is not None else _state(i,o,c,h,l,kw)["d"]
        if cur_d > P(params,"prev_d_max",-50): return False, {}

    prior_dk_lookback = int(P(params,"prior_dk_min_lookback",2))
    if idx-prior_dk_lookback < 0: return False, {}
    prior_dk_diffs = []
    for i in range(idx-prior_dk_lookback,idx):
        cur_k = k_arr[i] if k_arr is not None else _state(i,o,c,h,l,kw)["k"]
        cur_d = d_arr[i] if d_arr is not None else _state(i,o,c,h,l,kw)["d"]
        prior_dk_diffs.append(cur_d-cur_k)
    if not (min(prior_dk_diffs) < P(params,"prior_dk_min_less_than",1.1)):
        return False, {}

    ema53_lookback = int(P(params,"ema53_high_lookback",3))
    if idx-ema53_lookback < 0: return False, {}
    for i in range(idx-ema53_lookback,idx+1):
        cur_e53 = e53_arr[i] if e53_arr is not None else sl.ema(c[:i+1],53)
        if not (h[i] < cur_e53): return False, {}

    prior_kdj_lookback = int(P(params,"prior_kdj_lookback",2))
    if idx-prior_kdj_lookback-1 < 0: return False, {}
    prior_j_increase_found = False
    for i in range(idx-prior_kdj_lookback,idx):
        cur_j = j_arr[i] if j_arr is not None else _state(i,o,c,h,l,kw)["j"]
        prev_j = j_arr[i-1] if j_arr is not None else _state(i-1,o,c,h,l,kw)["j"]
        cur_e17 = e17_arr[i] if e17_arr is not None else sl.ema(c[:i+1],17)
        if (cur_j-prev_j >= P(params,"prior_j_net_increase_min",2)
                and h[i] < cur_e17):
            prior_j_increase_found = True
            break
    if not prior_j_increase_found: return False, {}

    j_now = j_arr[idx] if j_arr is not None else s["j"]
    j_prev = j_arr[idx-1] if j_arr is not None else _state(idx-1,o,c,h,l,kw)["j"]
    if not ((j_prev-j_now) <= P(params,"j_net_decrease_max",15)):
        return False, {}

    return True, {
        "price":c[idx],
        "entry_j":j_now,
        "entry_cci":float(cci_now),
        "entry_signal_idx":int(idx),
    }

def exit_conventional_down_break_short_p(c,h,l,idx,entry,params,**kw):
    cci_arr = kw.get("cci_arr")
    d_arr = kw.get("d_arr")
    current_cci = cci_arr[idx] if cci_arr is not None else _state(idx,c,c,h,l,kw)["cci"]
    current_d = d_arr[idx] if d_arr is not None else _state(idx,c,c,h,l,kw)["d"]
    entry_cci = float(entry.get("entry_cci")) if isinstance(entry,dict) and entry.get("entry_cci") is not None else None
    cci_exit = entry_cci is not None and current_cci <= entry_cci - P(params,"exit_cci_decrease",50)
    d_exit = current_d <= P(params,"exit_d_less_equal",11.3)
    if cci_exit or d_exit:
        return True, {"price":c[idx],"exit_type":"止盈"}
    entry_signal_idx = entry.get("entry_signal_idx") if isinstance(entry,dict) else None
    max_hold_hours = int(P(params,"max_hold_hours_without_exit",8))
    if entry_signal_idx is not None and idx-int(entry_signal_idx) >= max_hold_hours+1:
        return True, {"price":c[idx],"exit_type":"定时强制平仓"}
    return False, {}

def entry_early_downtrend_ema6_ema75_short_p(o,c,h,l,idx,params,**kw):
    if idx < 75: return False, {}
    s = _state(idx,o,c,h,l,kw)
    e6_arr, e19_arr = kw.get("e6_arr"), kw.get("e19_arr")
    e53_arr, e75_arr = kw.get("e53_arr"), kw.get("e75_arr")
    k_arr, d_arr = kw.get("k_arr"), kw.get("d_arr")
    cci_arr = kw.get("cci_arr")

    e6 = e6_arr[idx] if e6_arr is not None else sl.ema(c[:idx+1],6)
    e19 = e19_arr[idx] if e19_arr is not None else sl.ema(c[:idx+1],19)
    e53 = e53_arr[idx] if e53_arr is not None else sl.ema(c[:idx+1],53)
    e75 = e75_arr[idx] if e75_arr is not None else sl.ema(c[:idx+1],75)
    k_now = k_arr[idx] if k_arr is not None else s["k"]
    d_now = d_arr[idx] if d_arr is not None else s["d"]
    cci_now = cci_arr[idx] if cci_arr is not None else s["cci"]

    if not (c[idx] < o[idx]): return False, {}
    if not (o[idx] > e6 and o[idx] > e75): return False, {}
    if not (c[idx] < e6 and c[idx] < e75): return False, {}
    if not (c[idx] < o[idx-1]): return False, {}
    if not (e6 < e19 and e6 < e53): return False, {}
    if not (e75 < e19 and e75 < e53): return False, {}
    if not (e6 >= e75): return False, {}
    if not (k_now < d_now): return False, {}

    ema19_slope_lookback = int(P(params,"ema19_slope_lookback",6))
    if ema19_slope_lookback < 1 or idx-ema19_slope_lookback < 0:
        return False, {}
    e19_previous = (
        e19_arr[idx-ema19_slope_lookback]
        if e19_arr is not None
        else sl.ema(c[:idx-ema19_slope_lookback+1],19)
    )
    ema19_growth_pct = (e19/e19_previous-1.0)*100.0
    if not (
        ema19_growth_pct
        <= P(params,"ema19_growth_max_pct",0.0)+1e-12
    ):
        return False, {}

    retest_lookback = int(P(params,"ema19_retest_lookback",8))
    if retest_lookback < 1 or idx-retest_lookback < 0:
        return False, {}
    retested_ema19 = False
    for i in range(idx-retest_lookback,idx):
        e19_i = (
            e19_arr[i]
            if e19_arr is not None
            else sl.ema(c[:i+1],19)
        )
        if h[i] >= e19_i:
            retested_ema19 = True
            break
    if not retested_ema19:
        return False, {}

    atr_period = int(P(params,"entry_atr_period",14))
    if atr_period < 2 or idx-atr_period < 0:
        return False, {}
    true_ranges = []
    for i in range(idx-atr_period+1,idx+1):
        true_ranges.append(max(
            h[i]-l[i],
            abs(h[i]-c[i-1]),
            abs(l[i]-c[i-1]),
        ))
    atr_value = sum(true_ranges)/float(len(true_ranges))
    body_atr_ratio = (o[idx]-c[idx])/max(atr_value,1e-12)
    if not (
        body_atr_ratio+1e-12
        >= P(params,"entry_body_atr_min",0.7)
    ):
        return False, {}

    return True, {
        "price":c[idx],
        "entry_cci":float(cci_now),
        "ema19_growth_pct":float(ema19_growth_pct),
        "body_atr_ratio":float(body_atr_ratio),
        "ema19_retest_lookback":int(retest_lookback),
    }

def exit_early_downtrend_ema6_ema75_short_p(c,h,l,idx,entry,params,**kw):
    cci_arr, j_arr = kw.get("cci_arr"), kw.get("j_arr")
    current_cci = cci_arr[idx] if cci_arr is not None else _state(idx,c,c,h,l,kw)["cci"]
    current_j = j_arr[idx] if j_arr is not None else _state(idx,c,c,h,l,kw)["j"]
    entry_cci = None
    if isinstance(entry,dict) and entry.get("entry_cci") is not None:
        try:
            entry_cci = float(entry.get("entry_cci"))
        except Exception:
            entry_cci = None
    cci_exit = (
        entry_cci is not None
        and current_cci <= entry_cci-P(params,"exit_cci_drop",100)
    )
    j_exit = current_j <= P(params,"exit_j_less_equal",-10)
    if cci_exit or j_exit:
        return True, {"price":c[idx], "exit_type":"止盈"}
    return False, {}

def entry_ema53_liquidity_sweep_reclaim_long_p(o,c,h,l,idx,params,**kw):
    sweep_lookback = int(P(params,"sweep_lookback",36))
    ema53_growth_lookback = int(P(params,"ema53_growth_lookback",24))
    atr_period = int(P(params,"entry_atr_period",14))
    minimum_idx = max(sweep_lookback,ema53_growth_lookback,atr_period)+1
    if idx < minimum_idx:
        return False, {}

    e53_arr = kw.get("e53_arr")
    k_arr, d_arr = kw.get("k_arr"), kw.get("d_arr")
    cci_arr = kw.get("cci_arr")
    e53_now = e53_arr[idx] if e53_arr is not None else sl.ema(c[:idx+1],53)
    e53_previous = (
        e53_arr[idx-ema53_growth_lookback]
        if e53_arr is not None
        else sl.ema(c[:idx-ema53_growth_lookback+1],53)
    )
    k_now = k_arr[idx] if k_arr is not None else _state(idx,o,c,h,l,kw)["k"]
    k_previous = k_arr[idx-1] if k_arr is not None else _state(idx-1,o,c,h,l,kw)["k"]
    d_now = d_arr[idx] if d_arr is not None else _state(idx,o,c,h,l,kw)["d"]
    cci_now = cci_arr[idx] if cci_arr is not None else _state(idx,o,c,h,l,kw)["cci"]

    prior_low = min(l[idx-sweep_lookback:idx])
    if not (l[idx] < prior_low and c[idx] > prior_low):
        return False, {}

    ema53_growth_pct = (e53_now/e53_previous-1.0)*100.0
    if not (
        ema53_growth_pct+1e-12
        >= P(params,"ema53_growth_min_pct",-0.05)
    ):
        return False, {}

    true_ranges = []
    for i in range(idx-atr_period+1,idx+1):
        true_ranges.append(max(
            h[i]-l[i],
            abs(h[i]-c[i-1]),
            abs(l[i]-c[i-1]),
        ))
    atr_value = sum(true_ranges)/float(len(true_ranges))
    lower_wick = min(o[idx],c[idx])-l[idx]
    lower_wick_atr_ratio = lower_wick/max(atr_value,1e-12)
    if not (
        lower_wick_atr_ratio+1e-12
        >= P(params,"lower_wick_atr_min",0.7)
    ):
        return False, {}
    if not (d_now <= P(params,"entry_d_less_equal",35)):
        return False, {}
    if not (
        k_now-k_previous+1e-12
        >= P(params,"entry_k_net_increase_min",0)
    ):
        return False, {}

    return True, {
        "price":c[idx],
        "entry_cci":float(cci_now),
        "entry_signal_idx":int(idx),
        "swept_reference_low":float(prior_low),
        "ema53_growth_pct":float(ema53_growth_pct),
        "lower_wick_atr_ratio":float(lower_wick_atr_ratio),
    }

def exit_ema53_liquidity_sweep_reclaim_long_p(c,h,l,idx,entry,params,**kw):
    cci_arr = kw.get("cci_arr")
    current_cci = cci_arr[idx] if cci_arr is not None else _state(idx,c,c,h,l,kw)["cci"]
    try:
        entry_cci = float((entry or {}).get("entry_cci"))
    except (TypeError,ValueError):
        entry_cci = None
    if (
        entry_cci is not None
        and current_cci >= entry_cci+P(params,"exit_cci_increase",80)
    ):
        return True, {"price":c[idx], "exit_type":"止盈"}
    entry_signal_idx = (entry or {}).get("entry_signal_idx") if isinstance(entry,dict) else None
    max_hold_hours = int(P(params,"max_hold_hours",12))
    if (
        entry_signal_idx is not None
        and idx-int(entry_signal_idx) >= max_hold_hours
    ):
        return True, {"price":c[idx], "exit_type":"定时强制平仓"}
    return False, {}

def entry_conventional_down_arrangement_bottom_up_long_p(o,c,h,l,idx,params,**kw):
    if idx < 75:
        return False, {}
    e6_arr, e17_arr = kw.get("e6_arr"), kw.get("e17_arr")
    e53_arr, e75_arr = kw.get("e53_arr"), kw.get("e75_arr")
    k_arr, d_arr, j_arr = kw.get("k_arr"), kw.get("d_arr"), kw.get("j_arr")
    cci_arr = kw.get("cci_arr")

    e6 = e6_arr[idx] if e6_arr is not None else sl.ema(c[:idx+1],6)
    e17 = e17_arr[idx] if e17_arr is not None else sl.ema(c[:idx+1],17)
    e53 = e53_arr[idx] if e53_arr is not None else sl.ema(c[:idx+1],53)
    e75 = e75_arr[idx] if e75_arr is not None else sl.ema(c[:idx+1],75)
    if k_arr is not None and d_arr is not None:
        k_now, d_now = k_arr[idx], d_arr[idx]
        k_prev, d_prev = k_arr[idx-1], d_arr[idx-1]
        k_m8, d_m8 = k_arr[idx-8], d_arr[idx-8]
    else:
        k_now, d_now, _ = sl.kdj(h[:idx+1],l[:idx+1],c[:idx+1])
        k_prev, d_prev, _ = sl.kdj(h[:idx],l[:idx],c[:idx])
        k_m8, d_m8, _ = sl.kdj(h[:idx-7],l[:idx-7],c[:idx-7])
    if j_arr is not None:
        j_now, j_prev = j_arr[idx], j_arr[idx-1]
    else:
        j_now = sl.kdj(h[:idx+1],l[:idx+1],c[:idx+1])[2]
        j_prev = sl.kdj(h[:idx],l[:idx],c[:idx])[2]
    if cci_arr is not None:
        cci_now, cci_m8 = cci_arr[idx], cci_arr[idx-8]
    else:
        cci_now = _state(idx,o,c,h,l,kw)["cci"]
        cci_m8 = _state(idx-8,o,c,h,l,kw)["cci"]

    if not (e75 > e53 > e17 > e6): return False, {}
    if not (c[idx] > o[idx]): return False, {}
    if not (k_now > d_now): return False, {}
    kd_abs_max = P(params,"entry_kd_abs_difference_max_exclusive",4)
    if not (abs(k_now-d_now) < kd_abs_max): return False, {}
    if not (abs(k_m8-d_m8) < kd_abs_max): return False, {}
    j_cross_level = P(params,"entry_j_cross_level_exclusive",30)
    j_cross_up = j_now > j_cross_level and j_prev < j_cross_level
    kd_cross_up = k_now > d_now and k_prev < d_prev
    if not (j_cross_up or kd_cross_up): return False, {}
    if not (c[idx] > o[idx-8]): return False, {}
    if not (cci_now > cci_m8): return False, {}
    return True, {
        "price": c[idx],
        "entry_k": float(k_now),
        "reference_candle_idx": idx-8,
        "reference_candle_open": o[idx-8],
        "cci_increase_vs_8": float(cci_now-cci_m8),
        "entry_cross_path": "j_cross_30" if j_cross_up else "kd_cross_up",
    }

def exit_conventional_down_arrangement_bottom_up_long_p(c,h,l,idx,entry,params,**kw):
    k_arr, j_arr = kw.get("k_arr"), kw.get("j_arr")
    k_now = k_arr[idx] if k_arr is not None else sl.kdj(h[:idx+1],l[:idx+1],c[:idx+1])[0]
    j_now = j_arr[idx] if j_arr is not None else sl.kdj(h[:idx+1],l[:idx+1],c[:idx+1])[2]
    try:
        entry_k = float((entry or {}).get("entry_k"))
    except (TypeError, ValueError):
        return False, {}
    if entry_k < P(params,"entry_k_branch_threshold",50):
        should_exit = k_now >= P(params,"exit_k_for_low_entry_k_greater_equal",60)
    else:
        should_exit = j_now > P(params,"exit_j_for_high_entry_k_greater_than",90)
    if should_exit:
        return True, {"price":c[idx], "exit_type":"止盈"}
    return False, {}

def entry_conventional_up_arrangement_top_down_short_p(o,c,h,l,idx,params,**kw):
    if idx < 75:
        return False, {}
    e6_arr, e17_arr = kw.get("e6_arr"), kw.get("e17_arr")
    e53_arr, e75_arr = kw.get("e53_arr"), kw.get("e75_arr")
    k_arr, d_arr, j_arr = kw.get("k_arr"), kw.get("d_arr"), kw.get("j_arr")
    cci_arr = kw.get("cci_arr")

    e6 = e6_arr[idx] if e6_arr is not None else sl.ema(c[:idx+1],6)
    e17 = e17_arr[idx] if e17_arr is not None else sl.ema(c[:idx+1],17)
    e53 = e53_arr[idx] if e53_arr is not None else sl.ema(c[:idx+1],53)
    e75 = e75_arr[idx] if e75_arr is not None else sl.ema(c[:idx+1],75)
    e53_m24 = e53_arr[idx-24] if e53_arr is not None else sl.ema(c[:idx-23],53)
    if k_arr is not None and d_arr is not None:
        k_now, d_now = k_arr[idx], d_arr[idx]
        k_prev, d_prev = k_arr[idx-1], d_arr[idx-1]
        k_m8, d_m8 = k_arr[idx-8], d_arr[idx-8]
        prior_3_kd = [(k_arr[idx-offset], d_arr[idx-offset]) for offset in range(1,4)]
    else:
        k_now, d_now, _ = sl.kdj(h[:idx+1],l[:idx+1],c[:idx+1])
        k_prev, d_prev, _ = sl.kdj(h[:idx],l[:idx],c[:idx])
        k_m8, d_m8, _ = sl.kdj(h[:idx-7],l[:idx-7],c[:idx-7])
        prior_3_kd = [
            sl.kdj(h[:idx-offset+1],l[:idx-offset+1],c[:idx-offset+1])[:2]
            for offset in range(1,4)
        ]
    if j_arr is not None:
        j_now, j_prev = j_arr[idx], j_arr[idx-1]
    else:
        j_now = sl.kdj(h[:idx+1],l[:idx+1],c[:idx+1])[2]
        j_prev = sl.kdj(h[:idx],l[:idx],c[:idx])[2]
    if cci_arr is not None:
        cci_now, cci_m8 = cci_arr[idx], cci_arr[idx-8]
    else:
        cci_now = _state(idx,o,c,h,l,kw)["cci"]
        cci_m8 = _state(idx-8,o,c,h,l,kw)["cci"]
    if not (e75 < e53 < e17 < e6): return False, {}
    ema53_growth_24h_pct = (e53/e53_m24-1.0)*100.0
    ema53_growth_limit = P(params,"entry_ema53_growth_24h_max_pct",1.0)
    if not (ema53_growth_24h_pct <= ema53_growth_limit+1e-12): return False, {}
    if not (c[idx] < o[idx]): return False, {}
    if not (k_now < d_now): return False, {}
    current_kd_abs_max = P(params,"entry_current_kd_abs_difference_max_exclusive",4)
    reference_kd_abs_max = P(params,"entry_reference_kd_abs_difference_max_exclusive",5)
    if not (abs(k_now-d_now) < current_kd_abs_max): return False, {}
    if not (abs(k_m8-d_m8) < reference_kd_abs_max): return False, {}
    j_cross_level = P(params,"entry_j_cross_level_exclusive",65)
    j_cross_down = j_now < j_cross_level and j_prev > j_cross_level
    prior_d_max = P(params,"entry_kd_cross_prior_d_less_than",80)
    prior_3_confirmed = all(k_value > d_value and d_value < prior_d_max for k_value,d_value in prior_3_kd)
    kd_cross_down = k_now < d_now and k_prev > d_prev and prior_3_confirmed
    if not (j_cross_down or kd_cross_down): return False, {}
    if not (cci_now < cci_m8): return False, {}
    return True, {
        "price": c[idx],
        "entry_k": float(k_now),
        "reference_candle_idx": idx-8,
        "reference_candle_open": o[idx-8],
        "cci_decrease_vs_8": float(cci_m8-cci_now),
        "ema53_growth_24h_pct": float(ema53_growth_24h_pct),
        "entry_cross_path": "j_cross_65" if j_cross_down else "kd_cross_down",
    }

def exit_conventional_up_arrangement_top_down_short_p(c,h,l,idx,entry,params,**kw):
    k_arr, j_arr = kw.get("k_arr"), kw.get("j_arr")
    k_now = k_arr[idx] if k_arr is not None else sl.kdj(h[:idx+1],l[:idx+1],c[:idx+1])[0]
    j_now = j_arr[idx] if j_arr is not None else sl.kdj(h[:idx+1],l[:idx+1],c[:idx+1])[2]
    try:
        entry_k = float((entry or {}).get("entry_k"))
    except (TypeError, ValueError):
        return False, {}
    if entry_k > P(params,"entry_k_branch_threshold",50):
        should_exit = k_now <= P(params,"exit_k_for_high_entry_k_less_equal",45)
    else:
        should_exit = j_now < P(params,"exit_j_for_low_entry_k_less_than",10)
    if should_exit:
        return True, {"price":c[idx], "exit_type":"止盈"}
    return False, {}


def entry_general_up_arrangement_effective_cross_down_short_p(
    o,c,h,l,idx,params,**kw
):
    if idx < 75:
        return False, {}

    e17_arr = kw.get("e17_arr")
    e19_arr = kw.get("e19_arr")
    e53_arr = kw.get("e53_arr")
    e75_arr = kw.get("e75_arr")
    e6_arr = kw.get("e6_arr")
    e95_arr = kw.get("e95_arr")
    k_arr = kw.get("k_arr")
    d_arr = kw.get("d_arr")
    j_arr = kw.get("j_arr")
    cci_arr = kw.get("cci_arr")
    required = [
        e6_arr,e17_arr,e19_arr,e53_arr,e75_arr,e95_arr,
        k_arr,d_arr,j_arr,cci_arr,
    ]
    if any(arr is None for arr in required):
        return False, {}

    ema17 = float(e17_arr[idx])
    ema19 = float(e19_arr[idx])
    ema53 = float(e53_arr[idx])
    ema75 = float(e75_arr[idx])
    k_now = float(k_arr[idx])
    d_now = float(d_arr[idx])
    cci_now = float(cci_arr[idx])
    kd_abs_difference = abs(k_now-d_now)
    kd_abs_max = P(
        params,
        "entry_kd_abs_difference_max_exclusive",
        6,
    )
    kd_abs_min = P(
        params,
        "entry_kd_abs_difference_min_inclusive",
        0.3,
    )

    if not (ema17 > ema53 > ema75):
        return False, {}
    if not (
        float(o[idx]) > ema17
        and float(o[idx]) > ema19
        and float(c[idx]) < ema17
        and float(c[idx]) < ema19
        and float(c[idx]) > ema53
        and float(l[idx]) > ema53
        and cci_now > P(params,"entry_cci_greater_than",60)
    ):
        return False, {}
    if not (
        kd_abs_difference >= kd_abs_min-1e-12
        and kd_abs_difference < kd_abs_max
    ):
        return False, {}

    kdj_upper_lookback = int(P(params,"entry_kdj_upper_lookback_bars",2))
    kdj_upper_limit = P(params,"entry_kdj_upper_limit_exclusive",86)
    if kdj_upper_lookback < 0 or idx-kdj_upper_lookback < 0:
        return False, {}
    if not all(
        float(k_arr[i]) < kdj_upper_limit
        and float(d_arr[i]) < kdj_upper_limit
        and float(j_arr[i]) < kdj_upper_limit
        for i in range(idx-kdj_upper_lookback,idx+1)
    ):
        return False, {}

    prior_bearish_ema6_lookback = int(
        P(params,"prior_bearish_close_above_ema6_lookback",2)
    )
    if (
        prior_bearish_ema6_lookback < 1
        or idx-prior_bearish_ema6_lookback < 0
    ):
        return False, {}
    if any(
        float(c[i]) < float(o[i])
        and not (float(c[i]) > float(e6_arr[i]))
        for i in range(idx-prior_bearish_ema6_lookback,idx)
    ):
        return False, {}

    prior_kd_lookback = int(P(params,"prior_k_above_d_lookback",3))
    prior_kd_abs_min = P(
        params,"prior_k_above_d_abs_difference_min_inclusive",0.5
    )
    if prior_kd_lookback < 1 or idx-prior_kd_lookback < 0:
        return False, {}
    prior_kd_confirmed = any(
        float(k_arr[i]) > float(d_arr[i])
        and abs(float(k_arr[i])-float(d_arr[i]))
        >= prior_kd_abs_min-1e-12
        for i in range(idx-prior_kd_lookback,idx)
    )
    if not prior_kd_confirmed:
        return False, {}

    ema_cross_free_lookback = int(
        P(params,"prior_ema_cross_free_lookback",8)
    )
    if ema_cross_free_lookback < 1 or idx-ema_cross_free_lookback-1 < 0:
        return False, {}

    def crossed_on_bar(first,second,bar_idx):
        previous_difference = float(first[bar_idx-1])-float(second[bar_idx-1])
        current_difference = float(first[bar_idx])-float(second[bar_idx])
        return (
            abs(previous_difference) <= 1e-12
            or abs(current_difference) <= 1e-12
            or previous_difference*current_difference < 0
        )

    core_other_emas = [e6_arr,e17_arr,e19_arr,e95_arr]
    crossing_pairs = [(e53_arr,e75_arr)]
    crossing_pairs.extend((e53_arr,other) for other in core_other_emas)
    crossing_pairs.extend((e75_arr,other) for other in core_other_emas)
    for bar_idx in range(idx-ema_cross_free_lookback,idx):
        if any(
            crossed_on_bar(first,second,bar_idx)
            for first,second in crossing_pairs
        ):
            return False, {}

    return True, {
        "price":float(c[idx]),
        "tp_class":2,
        "entry_k":k_now,
        "entry_d":d_now,
        "entry_cci":cci_now,
        "kd_abs_difference":kd_abs_difference,
        "entry_kdj_upper_lookback":kdj_upper_lookback,
        "entry_kdj_upper_limit":kdj_upper_limit,
        "prior_bearish_close_above_ema6_lookback":
            prior_bearish_ema6_lookback,
        "prior_k_above_d_lookback":prior_kd_lookback,
        "prior_k_above_d_abs_difference_min":prior_kd_abs_min,
        "prior_ema_cross_free_lookback":ema_cross_free_lookback,
        "crossed_ema17_and_ema19_down":True,
    }


def exit_general_up_arrangement_effective_cross_down_short_p(
    c,h,l,idx,entry,params,**kw
):
    cci_arr = kw.get("cci_arr")
    j_arr = kw.get("j_arr")
    if cci_arr is None or j_arr is None:
        return False, {}
    cci_now = float(cci_arr[idx])
    j_now = float(j_arr[idx])
    cci_exit = cci_now < P(params,"exit_cci_less_than",15)
    j_exit = j_now < P(params,"exit_j_less_than",21)
    if cci_exit or j_exit:
        return True, {
            "price":float(c[idx]),
            "exit_type":"止盈（CCI小于15或J小于21）",
            "exit_cci":cci_now,
            "exit_j":j_now,
            "exit_condition":"cci" if cci_exit else "j",
        }
    return False, {}


def entry_conventional_up_arrangement_valid_death_cross_short_p(
    o,c,h,l,idx,params,**kw
):
    if idx < 75:
        return False, {}

    k_arr = kw.get("k_arr")
    d_arr = kw.get("d_arr")
    j_arr = kw.get("j_arr")
    cci_arr = kw.get("cci_arr")
    e6_arr = kw.get("e6_arr")
    e17_arr = kw.get("e17_arr")
    e53_arr = kw.get("e53_arr")
    e75_arr = kw.get("e75_arr")
    atr_arr = kw.get("atr14_arr")
    required = [
        k_arr,d_arr,j_arr,cci_arr,e6_arr,e17_arr,e53_arr,e75_arr,
        atr_arr,
    ]
    if any(arr is None for arr in required):
        return False, {}

    k_now = float(k_arr[idx])
    d_now = float(d_arr[idx])
    j_now = float(j_arr[idx])
    cci_now = float(cci_arr[idx])
    atr_now = float(atr_arr[idx])
    if not np.isfinite(atr_now) or atr_now <= 0:
        return False, {}
    k_limit = P(params,"entry_k_less_than",94)
    d_limit = P(params,"entry_d_less_than",94)
    j_limit = P(params,"entry_j_less_than",94)
    if not all(
        float(k_arr[i]) < k_limit
        and float(d_arr[i]) < d_limit
        and float(j_arr[i]) < j_limit
        for i in range(idx-1,idx+1)
    ):
        return False, {}
    second_prior_k_minus_d = float(k_arr[idx-2])-float(d_arr[idx-2])
    if not (
        second_prior_k_minus_d
        < P(params,"second_prior_k_minus_d_less_than",7)
    ):
        return False, {}
    j_net_drop = float(j_arr[idx-1])-j_now
    k_net_drop = float(k_arr[idx-1])-k_now
    cci_net_drop = float(cci_arr[idx-1])-cci_now
    if not (
        k_now < d_now
        and cci_now > P(params,"entry_cci_greater_than",110)
        and j_net_drop >= P(params,"entry_j_net_drop_min",5)
        and k_net_drop >= P(params,"entry_k_net_drop_min",1)
        and cci_net_drop >= P(params,"entry_cci_net_drop_min",0)
        and float(c[idx]) < float(o[idx])
        and float(c[idx]) < float(o[idx-1])
    ):
        return False, {}

    prior_k_above_d_bars = int(P(params,"prior_k_above_d_bars",1))
    if prior_k_above_d_bars < 1 or idx-prior_k_above_d_bars < 0:
        return False, {}
    if not all(
        float(k_arr[i]) > float(d_arr[i])
        for i in range(idx-prior_k_above_d_bars,idx)
    ):
        return False, {}

    ema6 = float(e6_arr[idx])
    ema17 = float(e17_arr[idx])
    ema53 = float(e53_arr[idx])
    ema75 = float(e75_arr[idx])
    close_minus_ema6_atr = (float(c[idx])-ema6)/atr_now
    ema6_slope_atr = (ema6-float(e6_arr[idx-3]))/atr_now
    ema6_ema17_spread_atr = (ema6-ema17)/atr_now
    if not (
        float(c[idx]) > ema17
        and close_minus_ema6_atr
        <= P(params,"entry_close_minus_ema6_atr_max",0)
        and ema6_slope_atr
        <= P(params,"entry_ema6_slope_3h_atr_max",0.5)
        and ema6_ema17_spread_atr
        <= P(params,"entry_ema6_ema17_spread_atr_max",2)
        and ema6 > ema17 > ema53 > ema75
    ):
        return False, {}

    return True, {
        "price":float(c[idx]),
        "tp_class":2,
        "entry_k":k_now,
        "entry_d":d_now,
        "entry_j":j_now,
        "entry_cci":cci_now,
        "entry_signal_idx":int(idx),
        "prior_k_above_d_bars":prior_k_above_d_bars,
        "signal_and_previous_kdj_upper_limits":{
            "k":float(k_limit),
            "d":float(d_limit),
            "j":float(j_limit),
        },
        "second_prior_k_minus_d":second_prior_k_minus_d,
        "j_net_drop":j_net_drop,
        "k_net_drop":k_net_drop,
        "cci_net_drop":cci_net_drop,
        "close_minus_ema6_atr":close_minus_ema6_atr,
        "ema6_slope_3h_atr":ema6_slope_atr,
        "ema6_ema17_spread_atr":ema6_ema17_spread_atr,
        "bearish_candle":True,
        "close_below_previous_open":True,
    }


def exit_conventional_up_arrangement_valid_death_cross_short_p(
    c,h,l,idx,entry,params,**kw
):
    try:
        entry_price = float((entry or {}).get("price"))
    except Exception:
        return False, {}
    target_ratio = P(params,"take_profit_price_ratio",0.013)
    target_price = entry_price*(1.0-target_ratio)
    if float(l[idx]) <= target_price:
        return True, {
            "price":float(target_price),
            "exit_type":"价格止盈1.3%",
        }

    try:
        entry_signal_idx = int((entry or {}).get("entry_signal_idx"))
    except Exception:
        entry_signal_idx = idx
    max_hold_hours = int(P(params,"max_hold_hours_without_exit",12))
    if max_hold_hours > 0 and idx-entry_signal_idx >= max_hold_hours:
        return True, {
            "price":float(c[idx]),
            "exit_type":"定时强制平仓",
        }
    return False, {}


def entry_btc15_dual_cycle_downtrend_reentry_short_p(
    o,c,h,l,idx,params,**kw
):
    lookback_return = int(P(params,"return_lookback_bars",64))
    high_lookback = int(P(params,"prior_high_lookback_bars",96))
    slope_lookback = int(P(params,"ema53_slope_lookback_bars",96))
    minimum_idx = max(
        lookback_return,high_lookback,slope_lookback,14
    )+1
    if idx < minimum_idx:
        return False, {}

    k_arr = kw.get("k_arr")
    d_arr = kw.get("d_arr")
    e53_arr = kw.get("e53_arr")
    atr_arr = kw.get("atr14_arr")
    h1_e19_arr = kw.get("h1_ema19_arr")
    h1_e53_arr = kw.get("h1_ema53_arr")
    h1_atr_arr = kw.get("h1_atr14_arr")
    required = (
        k_arr,d_arr,e53_arr,atr_arr,
        h1_e19_arr,h1_e53_arr,h1_atr_arr,
    )
    if any(value is None for value in required):
        return False, {}

    values = (
        k_arr[idx],d_arr[idx],k_arr[idx-1],d_arr[idx-1],
        e53_arr[idx],e53_arr[idx-slope_lookback],atr_arr[idx],
        h1_e19_arr[idx],h1_e53_arr[idx],h1_atr_arr[idx],
    )
    if not all(np.isfinite(float(value)) for value in values):
        return False, {}

    return_pct = (
        float(c[idx])/float(c[idx-lookback_return])-1.0
    )*100.0
    if not (
        return_pct
        <= P(params,"return_max_pct",-3.5)+1e-12
    ):
        return False, {}

    prior_high = max(float(value) for value in h[idx-high_lookback:idx])
    high_distance_atr = (
        float(c[idx])-prior_high
    )/max(float(atr_arr[idx]),1e-12)
    if not (
        high_distance_atr
        <= P(params,"prior_high_distance_atr_max",-14.0)+1e-12
    ):
        return False, {}

    ema53_slope_pct = (
        float(e53_arr[idx])/float(e53_arr[idx-slope_lookback])-1.0
    )*100.0
    if not (
        ema53_slope_pct
        <= P(params,"ema53_slope_max_pct",-3.0)+1e-12
    ):
        return False, {}

    h1_ema_gap_atr = (
        float(h1_e19_arr[idx])-float(h1_e53_arr[idx])
    )/max(float(h1_atr_arr[idx]),1e-12)
    if not (
        h1_ema_gap_atr
        <= P(params,"h1_ema19_ema53_gap_atr_max",-1.25)+1e-12
    ):
        return False, {}

    if not (
        float(k_arr[idx]) < float(d_arr[idx])
        and float(k_arr[idx-1]) >= float(d_arr[idx-1])
    ):
        return False, {}

    return True, {
        "price":float(c[idx]),
        "entry_signal_idx":int(idx),
        "return_pct":float(return_pct),
        "prior_high_distance_atr":float(high_distance_atr),
        "ema53_slope_pct":float(ema53_slope_pct),
        "h1_ema19_ema53_gap_atr":float(h1_ema_gap_atr),
    }


def exit_btc15_dual_cycle_downtrend_reentry_short_p(
    c,h,l,idx,entry,params,**kw
):
    try:
        entry_price = float((entry or {}).get("price"))
        entry_signal_idx = int((entry or {}).get("entry_signal_idx"))
    except (TypeError,ValueError):
        return False, {}
    target_ratio = P(params,"take_profit_price_ratio",0.012)
    target_price = entry_price*(1.0-target_ratio)
    if float(l[idx]) <= target_price:
        return True, {
            "price":float(target_price),
            "exit_type":"价格止盈",
        }
    max_hold_bars = int(P(params,"max_hold_bars",48))
    if idx-entry_signal_idx >= max_hold_bars:
        return True, {
            "price":float(c[idx]),
            "exit_type":"定时强制平仓",
        }
    return False, {}


def _entry_intraday_exhaustion(o,c,h,l,idx,params,side,**kw):
    if idx < 80:
        return False, {}
    rsi = kw.get("rsi14_arr")
    z20 = kw.get("z20_arr")
    k = kw.get("k_arr")
    atr = kw.get("atr14_arr")
    slope = kw.get("h1_slope4_arr")
    if any(x is None for x in (rsi,z20,k,atr,slope)):
        return False, {}
    atr_pct = float(atr[idx])/max(float(c[idx]),1e-12)
    atr_min = P(params,"atr_pct_min",0.0005)
    atr_max = P(params,"atr_pct_max",0.008)
    z_threshold = P(params,"z20_abs_min",2.0)
    rsi_threshold = P(params,"rsi_extreme",35)
    slope_limit = P(params,"h1_slope_abs_limit",0.001)
    if not (atr_min <= atr_pct <= atr_max):
        return False, {}
    if side == "long":
        met = (
            float(z20[idx]) <= -z_threshold
            and float(rsi[idx]) <= rsi_threshold
            and float(c[idx]) > float(o[idx])
            and float(k[idx]) > float(k[idx-1])
            and float(slope[idx]) >= -slope_limit
        )
    else:
        met = (
            float(z20[idx]) >= z_threshold
            and float(rsi[idx]) >= 100.0-rsi_threshold
            and float(c[idx]) < float(o[idx])
            and float(k[idx]) < float(k[idx-1])
            and float(slope[idx]) <= slope_limit
        )
    if not met:
        return False, {}
    return True, {
        "price":float(c[idx]), "entry_signal_idx":int(idx),
        "z20":float(z20[idx]), "rsi14":float(rsi[idx]),
        "atr_pct":float(atr_pct),
        "signal_score":P(params,"signal_score",210),
    }


def _exit_intraday_fixed(c,h,l,idx,entry,params,side):
    try:
        entry_price = float((entry or {}).get("price"))
        entry_idx = int((entry or {}).get("entry_signal_idx"))
    except (TypeError,ValueError):
        return False, {}
    target = P(params,"take_profit_price_ratio",0.006)
    target_price = entry_price*(1.0+target if side == "long" else 1.0-target)
    hit = float(h[idx]) >= target_price if side == "long" else float(l[idx]) <= target_price
    if hit:
        return True, {"price":float(target_price),"exit_type":"价格止盈"}
    if idx-entry_idx >= int(P(params,"max_hold_bars",72)):
        return True, {"price":float(c[idx]),"exit_type":"定时强制平仓"}
    return False, {}


def entry_btc5_exhaustion_reclaim_long_ai_p(o,c,h,l,idx,params,**kw):
    return _entry_intraday_exhaustion(o,c,h,l,idx,params,"long",**kw)


def exit_btc5_exhaustion_reclaim_long_ai_p(c,h,l,idx,entry,params,**kw):
    return _exit_intraday_fixed(c,h,l,idx,entry,params,"long")


def entry_cl5_exhaustion_fade_short_ai_p(o,c,h,l,idx,params,**kw):
    return _entry_intraday_exhaustion(o,c,h,l,idx,params,"short",**kw)


def exit_cl5_exhaustion_fade_short_ai_p(c,h,l,idx,entry,params,**kw):
    return _exit_intraday_fixed(c,h,l,idx,entry,params,"short")


def entry_ng5_exhaustion_fade_short_ai_p(o,c,h,l,idx,params,**kw):
    return _entry_intraday_exhaustion(o,c,h,l,idx,params,"short",**kw)


def exit_ng5_exhaustion_fade_short_ai_p(c,h,l,idx,entry,params,**kw):
    return _exit_intraday_fixed(c,h,l,idx,entry,params,"short")


def entry_ng5_session_exhaustion_reclaim_long_ai_p(o,c,h,l,idx,params,**kw):
    timestamps = kw.get("timestamp_arr")
    if timestamps is None or idx >= len(timestamps):
        return False, {}
    hour = int(timestamps[idx].hour)
    start = int(P(params,"utc_hour_start",8))
    end = int(P(params,"utc_hour_end",16))
    if not (start <= hour < end):
        return False, {}
    return _entry_intraday_exhaustion(o,c,h,l,idx,params,"long",**kw)


def exit_ng5_session_exhaustion_reclaim_long_ai_p(c,h,l,idx,entry,params,**kw):
    return _exit_intraday_fixed(c,h,l,idx,entry,params,"long")


def entry_xag5_session_breakdown_short_ai_p(o,c,h,l,idx,params,**kw):
    if idx < 80:
        return False, {}
    timestamps = kw.get("timestamp_arr")
    required = [kw.get(x) for x in (
        "h1_ema19_arr", "h1_ema53_arr", "h1_slope4_arr",
        "prev_low20_arr", "atr14_arr", "cci_arr",
    )]
    if timestamps is None or idx >= len(timestamps) or any(x is None for x in required):
        return False, {}
    h1e19,h1e53,slope,prev_low,atr,cci = required
    hour = int(timestamps[idx].hour)
    hour_start = int(P(params,"utc_hour_start",0))
    hour_end = int(P(params,"utc_hour_end",8))
    if not (hour_start <= hour < hour_end):
        return False, {}
    atr_value = float(atr[idx])
    atr_pct = atr_value/max(float(c[idx]),1e-12)
    body_atr = abs(float(c[idx])-float(o[idx]))/max(atr_value,1e-12)
    met = (
        float(h1e19[idx]) < float(h1e53[idx])
        and float(slope[idx]) < -P(params,"h1_slope_min",0.0005)
        and float(c[idx]) < float(prev_low[idx])
        and float(c[idx]) < float(o[idx])
        and body_atr >= P(params,"body_atr_min",0.6)
        and float(cci[idx]) <= -P(params,"cci_abs_min",50)
        and atr_pct >= P(params,"atr_pct_min",0.0008)
        and atr_pct <= P(params,"atr_pct_max",0.004)
    )
    if not met:
        return False, {}
    return True, {
        "price":float(c[idx]), "entry_signal_idx":int(idx),
        "h1_slope4":float(slope[idx]), "body_atr":float(body_atr),
        "cci62":float(cci[idx]), "atr_pct":float(atr_pct),
        "signal_score":P(params,"signal_score",235),
    }


def exit_xag5_session_breakdown_short_ai_p(c,h,l,idx,entry,params,**kw):
    return _exit_intraday_fixed(c,h,l,idx,entry,params,"short")


def entry_ltc5_exhaustion_fade_short_ai_p(o,c,h,l,idx,params,**kw):
    return _entry_intraday_exhaustion(o,c,h,l,idx,params,"short",**kw)


def exit_ltc5_exhaustion_fade_short_ai_p(c,h,l,idx,entry,params,**kw):
    return _exit_intraday_fixed(c,h,l,idx,entry,params,"short")


def entry_ada5_session_trend_pullback_short_ai_p(o,c,h,l,idx,params,**kw):
    if idx < 80:
        return False, {}
    timestamps = kw.get("timestamp_arr")
    required = [kw.get(x) for x in (
        "h1_ema19_arr", "h1_ema53_arr", "h1_slope4_arr",
        "e6_arr", "ema19_arr", "ema53_arr", "k_arr", "d_arr",
        "atr14_arr",
    )]
    if timestamps is None or idx >= len(timestamps) or any(x is None for x in required):
        return False, {}
    h1e19,h1e53,slope,e6,e19,e53,k,d,atr = required
    hour = int(timestamps[idx].hour)
    hour_start = int(P(params,"utc_hour_start",12))
    hour_end = int(P(params,"utc_hour_end",20))
    if not (hour_start <= hour < hour_end):
        return False, {}
    atr_pct = float(atr[idx])/max(float(c[idx]),1e-12)
    touch = P(params,"ema19_touch_tolerance",0.0)
    met = (
        float(h1e19[idx]) < float(h1e53[idx])
        and float(slope[idx]) < -P(params,"h1_slope_min",0.0005)
        and float(e19[idx]) < float(e53[idx])
        and float(h[idx]) >= float(e19[idx])*(1.0-touch)
        and float(c[idx]) <= float(e6[idx])
        and float(c[idx]) < float(o[idx])
        and float(k[idx]) < float(d[idx])
        and float(k[idx-1]) >= float(d[idx-1])
        and float(k[idx]) >= P(params,"k_min",55)
        and atr_pct >= P(params,"atr_pct_min",0.0003)
        and atr_pct <= P(params,"atr_pct_max",0.015)
    )
    if not met:
        return False, {}
    return True, {"price":float(c[idx]),"entry_signal_idx":int(idx),
                  "h1_slope4":float(slope[idx]),"k":float(k[idx]),
                  "d":float(d[idx]),"atr_pct":float(atr_pct),
                  "signal_score":P(params,"signal_score",205)}


def exit_ada5_session_trend_pullback_short_ai_p(c,h,l,idx,entry,params,**kw):
    return _exit_intraday_fixed(c,h,l,idx,entry,params,"short")


def entry_xau15_h1_breakout_long_ai_p(o,c,h,l,idx,params,**kw):
    if idx < 80:
        return False, {}
    need = [kw.get(x) for x in (
        "h1_ema19_arr","h1_ema53_arr","h1_slope4_arr","prev_high20_arr",
        "atr14_arr","cci_arr",
    )]
    if any(x is None for x in need):
        return False, {}
    h1e19,h1e53,slope,prev_high,atr,cci = need
    atr_pct = float(atr[idx])/max(float(c[idx]),1e-12)
    body_atr = abs(float(c[idx])-float(o[idx]))/max(float(atr[idx]),1e-12)
    met = (
        float(h1e19[idx]) > float(h1e53[idx])
        and float(slope[idx]) > P(params,"h1_slope_min",0.001)
        and float(c[idx]) > float(prev_high[idx])
        and float(c[idx]) > float(o[idx])
        and body_atr >= P(params,"body_atr_min",0.9)
        and float(cci[idx]) >= P(params,"cci_min",130)
        and atr_pct >= P(params,"atr_pct_min",0.0018)
        and atr_pct <= P(params,"atr_pct_max",0.008)
    )
    if not met:
        return False, {}
    return True, {"price":float(c[idx]),"entry_signal_idx":int(idx),
                  "signal_score":P(params,"signal_score",250)}


def exit_xau15_h1_breakout_long_ai_p(c,h,l,idx,entry,params,**kw):
    return _exit_intraday_fixed(c,h,l,idx,entry,params,"long")


STRATEGIES_1H = {
    "cci_75_100": (entry_cci_75_100_long_p, exit_cci_75_100_long_p, "long"),
    "ema7_break_long": (entry_ema7_break_long_p, exit_ema7_break_long_p, "long"),
    "ema7_break_short": (entry_ema7_break_short_p, exit_ema7_break_short_p, "short"),
    "ema8_mainwave_long": (entry_ema8_mainwave_long_p, exit_ema8_mainwave_long_p, "long"),
    "ema8_mainwave_short": (entry_ema8_mainwave_short_p, exit_ema8_mainwave_short_p, "short"),
    "ema7_cross_long": (entry_ema7_cross_long_p, exit_ema7_cross_long_p, "long"),
    "cci_110_135_long": (entry_cci_110_135_long_p, exit_cci_110_135_long_p, "long"),
    "j_cross_79_short": (entry_j_cross_79_short_p, exit_j_cross_79_short_p, "short"),
    "cci75_110_long": (entry_cci75_110_long_p, exit_cci75_110_long_p, "long"),
    "j_cross_7_8_long": (entry_j_cross_7_8_long_p, exit_j_cross_7_8_long_p, "long"),
    "cci_less_neg170_long": (entry_cci_less_neg170_long_p, exit_cci_less_neg170_long_p, "long"),
    "cci_neg60_neg110_short": (entry_cci_neg60_neg110_short_p, exit_cci_neg60_neg110_short_p, "short"),
    "cci_neg110_neg180_long": (entry_cci_neg110_neg180_long_p, exit_cci_neg110_neg180_long_p, "long"),
    "ema7_ema23_down_short": (entry_ema7_ema23_down_short_p, exit_ema7_ema23_down_short_p, "short"),
    "ema7_center_down_short": (entry_ema7_center_down_short_p, exit_ema7_center_down_short_p, "short"),
    "ema7_center_up_long": (entry_ema7_center_up_long_p, exit_ema7_center_up_long_p, "long"),
    "conventional_up_break_long": (entry_conventional_up_break_long_p, exit_conventional_up_break_long_p, "long"),
    "conventional_down_break_short": (entry_conventional_down_break_short_p, exit_conventional_down_break_short_p, "short"),
    "early_downtrend_ema6_ema75_short": (entry_early_downtrend_ema6_ema75_short_p, exit_early_downtrend_ema6_ema75_short_p, "short"),
    "ema53_liquidity_sweep_reclaim_long": (entry_ema53_liquidity_sweep_reclaim_long_p, exit_ema53_liquidity_sweep_reclaim_long_p, "long"),
    "conventional_down_arrangement_bottom_up_long": (entry_conventional_down_arrangement_bottom_up_long_p, exit_conventional_down_arrangement_bottom_up_long_p, "long"),
    "conventional_up_arrangement_top_down_short": (entry_conventional_up_arrangement_top_down_short_p, exit_conventional_up_arrangement_top_down_short_p, "short"),
    "general_up_arrangement_effective_cross_down_short": (entry_general_up_arrangement_effective_cross_down_short_p, exit_general_up_arrangement_effective_cross_down_short_p, "short"),
    "conventional_up_arrangement_valid_death_cross_short": (entry_conventional_up_arrangement_valid_death_cross_short_p, exit_conventional_up_arrangement_valid_death_cross_short_p, "short"),
}
STRATEGIES_15M = dict(STRATEGIES_1H)
STRATEGIES_15M.pop(
    "conventional_up_arrangement_valid_death_cross_short", None
)
STRATEGIES_15M.pop(
    "general_up_arrangement_effective_cross_down_short", None
)
STRATEGIES_15M[
    "btc15_dual_cycle_downtrend_reentry_short_ai"
] = (
    entry_btc15_dual_cycle_downtrend_reentry_short_p,
    exit_btc15_dual_cycle_downtrend_reentry_short_p,
    "short",
)
STRATEGIES_15M["xau15_h1_breakout_long_ai"] = (
    entry_xau15_h1_breakout_long_ai_p,
    exit_xau15_h1_breakout_long_ai_p,
    "long",
)
STRATEGIES_5M = {
    "btc5_exhaustion_reclaim_long_ai": (
        entry_btc5_exhaustion_reclaim_long_ai_p,
        exit_btc5_exhaustion_reclaim_long_ai_p,"long",
    ),
    "cl5_exhaustion_fade_short_ai": (
        entry_cl5_exhaustion_fade_short_ai_p,
        exit_cl5_exhaustion_fade_short_ai_p,"short",
    ),
    "ng5_exhaustion_fade_short_ai": (
        entry_ng5_exhaustion_fade_short_ai_p,
        exit_ng5_exhaustion_fade_short_ai_p,"short",
    ),
    "ng5_session_exhaustion_reclaim_long_ai": (
        entry_ng5_session_exhaustion_reclaim_long_ai_p,
        exit_ng5_session_exhaustion_reclaim_long_ai_p,"long",
    ),
    "xag5_session_breakdown_short_ai": (
        entry_xag5_session_breakdown_short_ai_p,
        exit_xag5_session_breakdown_short_ai_p,"short",
    ),
    "ltc5_exhaustion_fade_short_ai": (
        entry_ltc5_exhaustion_fade_short_ai_p,
        exit_ltc5_exhaustion_fade_short_ai_p,"short",
    ),
    "ada5_session_trend_pullback_short_ai": (
        entry_ada5_session_trend_pullback_short_ai_p,
        exit_ada5_session_trend_pullback_short_ai_p,"short",
    ),
}
STRATEGIES_BY_TIMEFRAME = {
    "1h": STRATEGIES_1H,
    "4h": {},
    "15m": STRATEGIES_15M,
    "5m": STRATEGIES_5M,
}

DSL_STRATEGY_DEFINITIONS = {}


def _dsl_entry_factory(definition):
    def entry(o, c, h, l, idx, params, **kw):
        import auto_trade_strategy_dsl as dsl
        met, details = dsl.evaluate_expression(
            kw.get("_dsl_frame"), idx, definition["entry"], explain=True
        )
        return met, {"price": c[idx], "condition_checks": details,
                     "dsl_hash": dsl.dsl_hash(definition),
                     "_dsl_entry_index": idx}
    return entry


def _dsl_exit_factory(definition):
    def exit_f(c, h, l, idx, entry, params, **kw):
        import auto_trade_strategy_dsl as dsl
        frame = kw.get("_dsl_frame")
        entry_idx = int(entry.get("_dsl_entry_index", idx))
        entry_price = float(entry.get("price", c[idx]))
        # Maintain peaks / MAE across bars via entry dict (mutable)
        peak_high = float(entry.get("peak_high") or max(entry_price, float(h[idx])))
        peak_low = float(entry.get("peak_low") or min(entry_price, float(l[idx])))
        peak_high = max(peak_high, float(h[idx]))
        peak_low = min(peak_low, float(l[idx]))
        entry["peak_high"] = peak_high
        entry["peak_low"] = peak_low
        direction = definition.get("direction") or "long"
        if direction == "long":
            adverse = max(0.0, (entry_price - float(l[idx])) / max(entry_price, 1e-12))
        else:
            adverse = max(0.0, (float(h[idx]) - entry_price) / max(entry_price, 1e-12))
        entry["mae_price_pct"] = max(float(entry.get("mae_price_pct") or 0.0), adverse)
        position = {
            "index": entry_idx,
            "price": entry_price,
            "peak_high": peak_high,
            "peak_low": peak_low,
            "mae_price_pct": entry["mae_price_pct"],
        }
        met, details = dsl.evaluate_expression(
            frame, idx, definition["exit"], explain=True,
            position=position, direction=direction,
        )
        timed = idx - entry_idx >= int(definition["max_hold_bars"])
        structured_px = None
        exit_ops = set()
        for row in (details or []):
            if row.get("passed") and row.get("exit_op"):
                exit_ops.add(row.get("exit_op"))
                if row.get("exit_price") is not None:
                    structured_px = float(row["exit_price"])
        if "atr_trailing" in exit_ops:
            exit_type = "ATR动态追踪退出"
        elif "swing_extreme" in exit_ops:
            exit_type = "Swing极值退出"
        elif timed:
            exit_type = "定时强制平仓"
        else:
            exit_type = "DSL策略止盈"
        px = structured_px if structured_px is not None else float(c[idx])
        return met or timed, {
            "price": px,
            "exit_type": exit_type,
            "condition_checks": details,
            "mae_price_pct": float(entry.get("mae_price_pct") or 0.0),
            "dsl_hash": dsl.dsl_hash(definition),
        }
    return exit_f


def _register_dsl_strategies():
    path = Path(DSL_CONFIG_PATH)
    if not path.exists():
        return
    try:
        import auto_trade_strategy_dsl as dsl
        payload = json.loads(path.read_text(encoding="utf-8"))
        for raw in payload.get("strategies", []):
            definition = dsl.validate_strategy(raw)
            timeframe = definition["timeframe"]
            key = definition["key"]
            STRATEGIES_BY_TIMEFRAME[timeframe][key] = (
                _dsl_entry_factory(definition),
                _dsl_exit_factory(definition),
                definition["direction"],
            )
            DSL_STRATEGY_DEFINITIONS[key] = definition
    except Exception as exc:
        logging.getLogger(__name__).error("DSL strategy registry load failed: %s", exc)


_register_dsl_strategies()

def run_backtest(
    strategy_name,
    instId="BTC-USDT",
    start_time=None,
    end_time=None,
    leverage=20,
    stop_loss_pct=0.009,
    cb=None,
    timeframe="1h",
    friction_scenario="observed_base",
    account_position_ratio=1.0,
    # Phase-4 research dynamic R (does NOT alter live B-grade 30%/20x mount).
    dynamic_risk_sizing=False,
    risk_pct=0.012,
    atr_target_multiplier=1.0,
    atr_size_period=14,
    dd_throttle_risk_pct=0.005,
    dd_throttle_of_max_dd=0.50,
):
    """Run engine backtest.

    Protective stop_loss_pct default 0.9%. When dynamic_risk_sizing=True,
    research/incubator evaluation uses ATR-based R; production CLI --confirm
    mount remains B/30%/20x until separately approved (do not silently change).
    """
    logger = init_backtest_logger(strategy_name)
    start_perf = time.time()

    try:
        timeframe = normalize_timeframe(timeframe)
    except Exception as exc:
        return {"error": str(exc)}
    strategy_registry = STRATEGIES_BY_TIMEFRAME[timeframe]
    if strategy_name not in strategy_registry:
        return {"error": "未知策略"}

    _input_stop_loss_pct = stop_loss_pct
    params = load_strategy_params(strategy_name)
    if _input_stop_loss_pct is None:
        stop_loss_pct = P(params, "stop_loss_pct", 0.009)
    else:
        try:
            _sl_text = str(_input_stop_loss_pct).strip()
            if "%" in _sl_text:
                stop_loss_pct = float(_sl_text.replace("%", "").strip()) / 100.0
            else:
                stop_loss_pct = float(_sl_text)
                if stop_loss_pct >= 0.1:
                    stop_loss_pct = stop_loss_pct / 100.0
        except Exception:
            stop_loss_pct = P(params, "stop_loss_pct", 0.009)

    try:
        normalized_inst = normalize_instrument(instId)
        friction = load_execution_friction(normalized_inst, friction_scenario)
        fee_rate_per_side = friction["fee_rate_per_side"]
        slippage_rate_per_side = friction["slippage_rate_per_side"]
        strategy_metadata = load_strategy_metadata(strategy_name)
        supported_instruments = strategy_metadata.get(
            "supported_instruments"
        )
        if (
            isinstance(supported_instruments,list)
            and supported_instruments
            and normalized_inst not in supported_instruments
        ):
            return {"error":"该策略不适用于当前回测标的"}
        local_directory = _find_local_data_directory(
            normalized_inst, timeframe
        )
        if local_directory:
            df = load_parquet_range(
                local_directory, start_time, end_time, timeframe=timeframe
            )
        else:
            df = load_okx_swap_range(
                normalized_inst, start_time, end_time, timeframe=timeframe
            )
        df = precompute_indicators(df,timeframe=timeframe)

        start_ts = _beijing_input_to_utc_naive(start_time)
        start_idx = max(62, df.index.searchsorted(start_ts))
        total_bars = max(0, len(df) - start_idx)

        if total_bars <= 0:
            return {"error": "指定区间内无可回测K线"}

        data_hash = hashlib.sha256(df[["open","high","low","close"]].astype(float).values.tobytes()).hexdigest()[:16]
        logic_hash = _sha256_file("/root/strategy_logic.py")
        config_hash = _sha256_file(CONFIG_PATH)

        entry_f, exit_f, direction = strategy_registry[strategy_name]

        df = df.replace([np.inf, -np.inf], np.nan)
        if df[["open","high","low","close","cci","k","d","j","macd_stick"]].isnull().values.any():
            df = df.ffill().bfill()

        o = df["open"].astype(float).values.tolist()
        h = df["high"].astype(float).values.tolist()
        l = df["low"].astype(float).values.tolist()
        c = df["close"].astype(float).values.tolist()
        ts = df.index.tolist()
        kwargs = _build_kwargs(df)
        market_regimes = _market_regime_labels(df, timeframe=timeframe)
        round_trip_cost_ratio = (
            2.0 * sum(float(friction.get(key) or 0.0) for key in (
                "fee_rate_per_side", "slippage_rate_per_side",
                "half_spread_rate_per_side", "impact_rate_per_side",
                "latency_rate_per_side")) * int(leverage)
        )

        trades = []
        account_position_ratio = max(0.0, min(1.0, float(account_position_ratio)))
        margin_cap = 1.0
        account_cap = 1.0
        peak_equity = 1.0
        max_dd_so_far = 0.0
        pos = None
        entry_p = 0.0
        entry_idx = 0
        entry_data = {}
        entry_size_frac = account_position_ratio
        entry_sizing = None

        # Optional Phase-4 ATR sizing helpers (research only)
        _dsl_size = None
        if dynamic_risk_sizing:
            try:
                import auto_trade_strategy_dsl as _dsl_mod
                _dsl_size = _dsl_mod
            except Exception:
                _dsl_size = None

        telemetry = {
            "bars_scanned": 0,
            "entry_conditions_met": 0,
            "actual_opens": 0,
            "actual_closes": 0,
            "params_loaded": len(params),
        }

        for i in range(start_idx, len(c)):
            telemetry["bars_scanned"] += 1
            current = i - start_idx + 1
            if cb and (current % 5 == 0 or current == total_bars):
                cb(current, total_bars)

            timeout_seconds = 900 if timeframe == "5m" else (480 if timeframe == "15m" else 180)
            if time.time() - start_perf > timeout_seconds:
                raise TimeoutError("回测超时熔断")

            if pos is None:
                met, info = entry_f(o, c, h, l, i, params, **kwargs)
                entry_candle_filter = int(
                    P(params, "entry_requires_directional_candle",
                      0 if strategy_name in DSL_STRATEGY_DEFINITIONS else 1)
                )
                if entry_candle_filter == 0:
                    entry_candle_ok = True
                else:
                    entry_candle_ok = (
                        (direction == "long" and c[i] > o[i])
                        or (direction == "short" and c[i] < o[i])
                    )
                if met and entry_candle_ok:
                    telemetry["entry_conditions_met"] += 1
                    pos = direction
                    entry_p = float(info.get("price", c[i]))
                    entry_idx = i
                    entry_data = info
                    entry_size_frac = account_position_ratio
                    entry_sizing = None
                    if dynamic_risk_sizing and _dsl_size is not None:
                        try:
                            # ATR from recent bars (price units)
                            period = int(atr_size_period or 14)
                            trs = []
                            for j in range(max(1, i - period + 1), i + 1):
                                tr = max(
                                    h[j] - l[j],
                                    abs(h[j] - c[j - 1]),
                                    abs(l[j] - c[j - 1]),
                                )
                                trs.append(tr)
                            atr_v = (sum(trs) / float(len(trs))) if trs else entry_p * 0.01
                            r_eff = _dsl_size.effective_risk_pct(
                                risk_pct, account_cap, peak_equity, max_dd_so_far,
                                dd_throttle_risk_pct=dd_throttle_risk_pct,
                                dd_throttle_of_max_dd=dd_throttle_of_max_dd,
                            )
                            entry_sizing = _dsl_size.atr_position_size(
                                account_cap, r_eff, atr_v,
                                target_multiplier=atr_target_multiplier,
                                entry_price=entry_p,
                            )
                            entry_size_frac = float(entry_sizing.get("account_fraction") or 0.0)
                            entry_sizing["risk_pct_used"] = r_eff
                        except Exception:
                            entry_size_frac = account_position_ratio
                            entry_sizing = {"error": "atr_size_fallback"}
                    telemetry["actual_opens"] += 1
            else:
                sl_price = entry_p * (1 - stop_loss_pct) if direction == "long" else entry_p * (1 + stop_loss_pct)
                is_sl = (l[i] <= sl_price) if direction == "long" else (h[i] >= sl_price)
                exit_res = exit_f(c, h, l, i, entry_data, params, **kwargs)
                if isinstance(exit_res, tuple):
                    is_exit, exit_info = exit_res
                    exit_info = exit_info or {}
                else:
                    is_exit, exit_info = bool(exit_res), {}

                if is_sl or is_exit:
                    exit_p = sl_price if is_sl else float(exit_info.get("price", c[i]))
                    tp_class = int(entry_data.get("tp_class", 2)) if isinstance(entry_data, dict) else 2
                    if strategy_name == "ema7_center_down_short":
                        exit_type = "\u6b62\u635f" if is_sl else exit_info.get("exit_type", "\u6b62\u76c8\uff08j\u5c0f\u4e8e36.5\u4e14d\u5c0f\u4e8e62\u4e14cci\u5c0f\u4e8e\u8d1f89\uff09")
                    else:
                        exit_type = ("%d\u7c7b\u6b62\u635f" % tp_class) if is_sl else exit_info.get("exit_type", ("%d\u7c7b\u6b62\u76c8" % tp_class))
                    raw_pnl = (exit_p - entry_p) / entry_p if direction == "long" else (entry_p - exit_p) / entry_p
                    gross_lev_pnl = raw_pnl * leverage
                    hours_per_bar = {"5m": 1.0/12.0, "15m": .25,
                                     "1h": 1.0}.get(timeframe, 1.0)
                    funding_cost_ratio = ((i-entry_idx)*hours_per_bar/8.0)*float(
                        friction.get("funding_rate_per_8h") or 0.0)*int(leverage)
                    transaction_cost_ratio = round_trip_cost_ratio+funding_cost_ratio
                    net_lev_pnl = gross_lev_pnl - transaction_cost_ratio
                    margin_cap *= max(0.0, 1 + net_lev_pnl)
                    size_frac = float(entry_size_frac if dynamic_risk_sizing else account_position_ratio)
                    account_cap *= max(0.0, 1 + net_lev_pnl * size_frac)
                    if account_cap > peak_equity:
                        peak_equity = account_cap
                    if peak_equity > 0:
                        dd_now = (peak_equity - account_cap) / peak_equity
                        if dd_now > max_dd_so_far:
                            max_dd_so_far = dd_now

                    _trade = {
                        "instrument": normalized_inst,
                        "entry_time": _display_time(ts[entry_idx]),
                        "exit_time": _display_time(ts[i]),
                        "entry_price": float(entry_p),
                        "exit_price": float(exit_p),
                        "gross_pnl_ratio": float(gross_lev_pnl),
                        "transaction_cost_ratio": float(transaction_cost_ratio),
                        "friction_scenario": friction_scenario,
                        "pnl_ratio": float(net_lev_pnl * size_frac) if dynamic_risk_sizing else float(net_lev_pnl),
                        "pnl_ratio_full_size": float(net_lev_pnl),
                        "profit": bool((net_lev_pnl * size_frac) > 0) if dynamic_risk_sizing else bool(net_lev_pnl > 0),
                        "stop_loss": bool(is_sl),
                        "exit_type": exit_type,
                        "tp_class": tp_class,
                        "market_regime": market_regimes[entry_idx],
                    }
                    if isinstance(entry_data, dict) and entry_data.get("mae_price_pct") is not None:
                        _trade["mae_price_pct"] = float(entry_data.get("mae_price_pct") or 0.0)
                    if isinstance(exit_info, dict) and exit_info.get("mae_price_pct") is not None:
                        _trade["mae_price_pct"] = float(exit_info.get("mae_price_pct") or 0.0)
                    _bt_ret_pct = round(float(_trade["pnl_ratio"]) * 100.0, 2)
                    _trade['leverage'] = int(leverage)
                    _trade['stop_loss_ratio'] = float(stop_loss_pct)
                    _trade['stop_loss_pct'] = float(stop_loss_pct)
                    _trade['pnl_pct'] = _bt_ret_pct
                    _trade['return_pct'] = _bt_ret_pct
                    _trade['profit_pct'] = _bt_ret_pct
                    _trade['yield_pct'] = _bt_ret_pct
                    _trade['roi'] = _bt_ret_pct
                    _trade['roi_pct'] = _bt_ret_pct
                    _trade['leveraged_return_pct'] = _bt_ret_pct
                    _trade['full_margin_return_pct'] = round(net_lev_pnl * 100.0, 2)
                    _trade['account_position_ratio'] = size_frac
                    _trade['account_return_pct'] = round(net_lev_pnl * size_frac * 100.0, 2)
                    _trade['dynamic_risk_sizing'] = bool(dynamic_risk_sizing)
                    if entry_sizing is not None:
                        _trade['sizing'] = entry_sizing
                    trades.append(_trade)
                    pos = None
                    telemetry["actual_closes"] += 1

        return _native({
            "instrument": normalized_inst,
            "timeframe": timeframe,
            "timeframe_label": TIMEFRAME_SPECS[timeframe]["label"],
            "total_trades": len(trades),
            "total_return_percent": (account_cap - 1) * 100,
            "full_margin_total_return_percent": (margin_cap - 1) * 100,
            "win_rate_percent": (sum(1 for t in trades if t["profit"]) / len(trades) * 100) if trades else 0.0,
            "trades": trades,
            "telemetry": telemetry,
            "consistency_audit": {
                "data_version": data_hash,
                "strategy_logic_hash": logic_hash,
                "strategy_config_hash": config_hash,
                "engine_version": ENGINE_VERSION,
                "timeframe": timeframe,
                "timeframe_label": TIMEFRAME_SPECS[timeframe]["label"],
                "okx_bar": TIMEFRAME_SPECS[timeframe]["okx_bar"],
                "strategy_parameter_unit": "bars",
                "live_auto_trade_authorized_for_timeframe": timeframe == "1h",
                "params_used": params,
                "stop_loss_pct": stop_loss_pct,
                "fee_rate_per_side": fee_rate_per_side,
                "slippage_rate_per_side": slippage_rate_per_side,
                "friction_scenario": friction_scenario,
                "friction_model": friction,
                "returns_are_net_of_costs": True,
                "account_position_ratio": account_position_ratio,
            }
        })
    except Exception as e:
        logger.error(f"回测失败: {e}", exc_info=True)
        return {"error": str(e)}
