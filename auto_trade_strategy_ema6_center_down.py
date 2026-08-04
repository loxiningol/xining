# -*- coding: utf-8 -*-
"""Live adapter for the authoritative 1h/15m/5m backtest strategies.

No trading rule is duplicated here. Entry and exit are delegated to the
timeframe-specific registry in backtest_engine_v2 with parameters from the
same experimental_strategies.json used by the website backtester.
"""
from pathlib import Path
import json
import os
import tempfile
import time

import pandas as pd

import backtest_engine_v2 as bt

ROOT = Path("/root")
AUTO_DIR = ROOT / "auto_trade"
SYMBOL = os.environ.get("VECTOR_TRADE_SYMBOL", "BTC-USDT-SWAP").strip().upper()
INSTANCE_KEY = SYMBOL.split("-")[0].lower()
TIMEFRAME = os.environ.get("VECTOR_TRADE_TIMEFRAME", "1h").strip().lower()
TIMEFRAME = (
    "5m" if TIMEFRAME in ("5m", "5min", "5minute")
    else ("15m" if TIMEFRAME in ("15m", "15min", "15minute") else "1h")
)
INSTANCE_SUFFIX = (
    "_" + INSTANCE_KEY + "_" + TIMEFRAME
    if TIMEFRAME in ("15m", "5m")
    else ("" if SYMBOL == "BTC-USDT-SWAP" else "_" + INSTANCE_KEY)
)
SIGNAL_FILE = AUTO_DIR / ("formal_ema6_signal%s.json" % INSTANCE_SUFFIX)
CANDLE_CACHE_FILE = AUTO_DIR / (
    "formal_%s_%s_candles_cache.json" % (INSTANCE_KEY, TIMEFRAME)
)
STRATEGY_KEY = "ema7_center_down_short"
STRATEGY_NAME = "ema6居中后再下行"
STRATEGIES = {
    "ema7_center_down_short": {
        "name": "ema6居中后再下行",
        "side": "short",
        "signal_file": AUTO_DIR / ("formal_ema6_signal%s.json" % INSTANCE_SUFFIX),
    },
    "conventional_up_break_long": {
        "name": "常规上升排列突破",
        "side": "long",
        "signal_file": AUTO_DIR / ("formal_conventional_up_break_signal%s.json" % INSTANCE_SUFFIX),
    },
    "early_downtrend_ema6_ema75_short": {
        "name": "EMA19反抽失败·EMA6/EMA75同步破位",
        "side": "short",
        "signal_file": AUTO_DIR / ("formal_early_downtrend_ema6_ema75_signal%s.json" % INSTANCE_SUFFIX),
    },
    "conventional_down_arrangement_bottom_up_long": {
        "name": "常规下跌排列筑底上行",
        "side": "long",
        "signal_file": AUTO_DIR / ("formal_conventional_down_arrangement_bottom_up_signal%s.json" % INSTANCE_SUFFIX),
    },
    "cci_75_100": {
        "name": "EMA7上升趋势回踩续涨",
        "side": "long",
        "signal_file": AUTO_DIR / ("formal_ema7_pullback_continuation_long_signal%s.json" % INSTANCE_SUFFIX),
    },
    "ema53_liquidity_sweep_reclaim_long": {
        "name": "EMA53缓升｜36小时低点扫荡收回（AI创造）",
        "side": "long",
        "signal_file": AUTO_DIR / ("formal_ema53_liquidity_sweep_reclaim_long_signal%s.json" % INSTANCE_SUFFIX),
    },
    "ema8_mainwave_long": {
        "name": "EMA8主升浪",
        "side": "long",
        "signal_file": AUTO_DIR / ("formal_ema8_mainwave_long_signal%s.json" % INSTANCE_SUFFIX),
    },
    "cci_neg60_neg110_short": {
        "name": "CCI负60-负110下行中再下行",
        "side": "short",
        "signal_file": AUTO_DIR / ("formal_cci_neg60_neg110_short_signal%s.json" % INSTANCE_SUFFIX),
    },
    "btc15_dual_cycle_downtrend_reentry_short_ai": {
        "name": "双周期下跌加速再死叉（AI创造）",
        "side": "short",
        "signal_file": AUTO_DIR / (
            "formal_btc15_dual_cycle_downtrend_reentry_short_ai_signal%s.json"
            % INSTANCE_SUFFIX
        ),
    },
    "conventional_up_arrangement_valid_death_cross_short": {
        "name": "常规上升排列有效死叉",
        "side": "short",
        "signal_file": AUTO_DIR / (
            "formal_conventional_up_arrangement_valid_death_cross_short_signal%s.json"
            % INSTANCE_SUFFIX
        ),
    },
    "btc5_exhaustion_reclaim_long_ai": {"name":"BTC 5分钟超跌收回（AI创造）","side":"long","signal_file":AUTO_DIR/("formal_btc5_exhaustion_reclaim_long_ai_signal%s.json"%INSTANCE_SUFFIX)},
    "cl5_exhaustion_fade_short_ai": {"name":"CL 5分钟冲高衰竭回落（AI创造）","side":"short","signal_file":AUTO_DIR/("formal_cl5_exhaustion_fade_short_ai_signal%s.json"%INSTANCE_SUFFIX)},
    "ng5_exhaustion_fade_short_ai": {"name":"NG 5分钟冲高衰竭回落（AI创造）","side":"short","signal_file":AUTO_DIR/("formal_ng5_exhaustion_fade_short_ai_signal%s.json"%INSTANCE_SUFFIX)},
    "ng5_session_exhaustion_reclaim_long_ai": {"name":"NG 5分钟时段超跌收回（AI创造）","side":"long","signal_file":AUTO_DIR/("formal_ng5_session_exhaustion_reclaim_long_ai_signal%s.json"%INSTANCE_SUFFIX)},
    "xag5_session_breakdown_short_ai": {"name":"XAG 5分钟时段顺势破位（AI创造）","side":"short","signal_file":AUTO_DIR/("formal_xag5_session_breakdown_short_ai_signal%s.json"%INSTANCE_SUFFIX)},
    "ltc5_exhaustion_fade_short_ai": {"name":"LTC 5分钟冲高衰竭回落（AI创造）","side":"short","signal_file":AUTO_DIR/("formal_ltc5_exhaustion_fade_short_ai_signal%s.json"%INSTANCE_SUFFIX)},
    "ada5_session_trend_pullback_short_ai": {"name":"ADA 5分钟时段趋势反抽（AI创造）","side":"short","signal_file":AUTO_DIR/("formal_ada5_session_trend_pullback_short_ai_signal%s.json"%INSTANCE_SUFFIX)},
    "xau15_h1_breakout_long_ai": {"name":"XAU 15分钟顺势放量突破（AI创造）","side":"long","signal_file":AUTO_DIR/("formal_xau15_h1_breakout_long_ai_signal%s.json"%INSTANCE_SUFFIX)},
}
for _dsl_key, _dsl_definition in getattr(bt, "DSL_STRATEGY_DEFINITIONS", {}).items():
    if (
        _dsl_definition.get("live_enabled") is True
        and _dsl_definition.get("timeframe") == TIMEFRAME
        and SYMBOL in (_dsl_definition.get("supported_instruments") or [])
    ):
        STRATEGIES[_dsl_key] = {
            "name": _dsl_definition.get("name") or _dsl_key,
            "side": _dsl_definition.get("direction"),
            "signal_file": AUTO_DIR / (
                "formal_dsl_%s_signal%s.json" % (_dsl_key, INSTANCE_SUFFIX)
            ),
        }
BAR = "5m" if TIMEFRAME == "5m" else ("15m" if TIMEFRAME == "15m" else "1H")
MIN_CANDLES = 1400 if TIMEFRAME == "5m" else (1200 if TIMEFRAME == "15m" else 120)
BAR_MILLISECONDS = {"5m": 300000, "15m": 900000, "1h": 3600000}[TIMEFRAME]
_FRAME_CONTEXT_CACHE = {"key": None, "value": None}
_NO_TRIGGER_DIAG_CACHE = {}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path, default):
    """Return decoded JSON, or a caller-owned default for absent/bad cache files."""
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError, TypeError):
        return default


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    f = tempfile.NamedTemporaryFile(delete=False, dir=str(path.parent), mode="w", encoding="utf-8")
    try:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        f.close()
        Path(f.name).replace(path)
    except Exception:
        try:
            Path(f.name).unlink()
        except Exception:
            pass
        raise


def _okx_request(method, path, params=None, body=None, auth=False):
    import auto_trade_okx as okx
    return okx._okx_request(method, path, params=params, body=body, auth=auth)


def _closed_rows(raw):
    rows = raw.get("data") if isinstance(raw, dict) and str(raw.get("code")) == "0" else []
    out = []
    for row in rows or []:
        if not isinstance(row, list) or len(row) < 9 or str(row[8]) != "1":
            continue
        try:
            out.append({"ts": int(row[0]), "open": float(row[1]), "high": float(row[2]),
                        "low": float(row[3]), "close": float(row[4])})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda x: x["ts"])
    dedup = {x["ts"]: x for x in out}
    return [dedup[k] for k in sorted(dedup)]


def load_closed_candles(limit=None):
    limit = int(limit or (1400 if TIMEFRAME == "5m" else (1200 if TIMEFRAME == "15m" else 300)))
    cached = _read_json(CANDLE_CACHE_FILE, {})
    cached_rows = cached.get("candles") if isinstance(cached, dict) else None
    cache_age = time.time() - float((cached or {}).get("fetched_at_ts") or 0)
    now_ms = int(time.time() * 1000)
    # A candle timestamp is its open time. At any instant the most recent
    # confirmed candle should therefore be the previous timeframe bucket.
    expected_latest_ts = (now_ms // BAR_MILLISECONDS) * BAR_MILLISECONDS - BAR_MILLISECONDS
    cached_latest_ts = int(cached_rows[-1]["ts"]) if isinstance(cached_rows, list) and cached_rows else 0
    if (
        isinstance(cached_rows, list)
        and len(cached_rows) >= MIN_CANDLES
        and cached_latest_ts >= expected_latest_ts
    ):
        out = dict(cached)
        out.update({"ok": True, "source": "fresh_local_candle_cache",
                    "fresh": True, "latest_candle_ts": cached_latest_ts,
                    "expected_latest_candle_ts": expected_latest_ts})
        out["cache_age_sec"] = round(cache_age, 3)
        return out
    rows_by_ts = {}
    after = None
    # The newest OKX page normally includes one still-forming candle, which is
    # deliberately discarded. Fetch one spare page so ``limit`` confirmed
    # candles remain available after that exclusion.
    # Once a warm cache exists, only the newest page is required. This cuts a
    # 1400-candle 5m refresh from six requests to one and avoids OKX 429 bursts.
    warm_cache = isinstance(cached_rows, list) and len(cached_rows) >= MIN_CANDLES
    if warm_cache:
        for row in cached_rows:
            try:
                rows_by_ts[int(row["ts"])] = row
            except (KeyError, TypeError, ValueError):
                continue
    page_count = 1 if warm_cache else max(1, (limit + 299) // 300 + 1)
    for _ in range(page_count):
        params = {
            "instId": SYMBOL,
            "bar": BAR,
            "limit": str(min(300, limit)),
        }
        if after is not None:
            params["after"] = str(after)
        try:
            endpoint = (
                "/api/v5/market/candles"
                if warm_cache and after is None
                else "/api/v5/market/history-candles"
            )
            raw = _okx_request("GET", endpoint, params=params, auth=False)
        except Exception as exc:
            if isinstance(cached_rows, list) and len(cached_rows) >= MIN_CANDLES:
                out = dict(cached)
                out.update({"ok":True,"source":"stale_local_candle_cache_fallback",
                            "fresh":cached_latest_ts >= expected_latest_ts,
                            "latest_candle_ts":cached_latest_ts,
                            "expected_latest_candle_ts":expected_latest_ts,
                            "cache_fallback_error":str(exc),"cache_age_sec":round(cache_age,3)})
                return out
            raise
        page = _closed_rows(raw)
        if not page:
            break
        for row in page:
            rows_by_ts[row["ts"]] = row
        oldest = min(row["ts"] for row in page)
        if after is not None and oldest >= after:
            break
        after = oldest
        if len(rows_by_ts) >= limit:
            break
        time.sleep(0.2)
    rows = [rows_by_ts[ts] for ts in sorted(rows_by_ts)][-limit:]
    if len(rows) < MIN_CANDLES:
        return {
            "ok": False,
            "error": "insufficient confirmed %s candles" % BAR,
            "count": len(rows),
        }
    payload = {"ok": True, "symbol": SYMBOL, "bar": BAR, "confirmed_only": True,
               "source": ("okx_recent_candles_incremental" if warm_cache
                          else "okx_history_candles"), "fetched_at": _now(),
               "fetched_at_ts": time.time(), "candles": rows,
               "latest_candle_ts": int(rows[-1]["ts"]),
               "expected_latest_candle_ts": expected_latest_ts,
               "fresh": int(rows[-1]["ts"]) >= expected_latest_ts}
    _atomic_write(CANDLE_CACHE_FILE, payload)
    return payload


def _frame(candles):
    df = pd.DataFrame(candles)
    df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
    return bt.precompute_indicators(
        df[["open", "high", "low", "close"]], timeframe=TIMEFRAME
    )


def _frame_context(candles):
    """Compute indicators once per closed candle and share them across strategies."""
    if not candles:
        raise ValueError("empty closed candle set")
    key = (len(candles), int(candles[0]["ts"]), int(candles[-1]["ts"]))
    if _FRAME_CONTEXT_CACHE.get("key") == key:
        return _FRAME_CONTEXT_CACHE["value"]
    df = _frame(candles)
    if len(df) < MIN_CANDLES:
        raise ValueError("need at least %d closed candles" % MIN_CANDLES)
    df = df.replace([float("inf"), float("-inf")], float("nan")).ffill().bfill()
    value = {
        "df": df,
        "open": df["open"].astype(float).tolist(),
        "high": df["high"].astype(float).tolist(),
        "low": df["low"].astype(float).tolist(),
        "close": df["close"].astype(float).tolist(),
        "kwargs": bt._build_kwargs(df),
    }
    _FRAME_CONTEXT_CACHE.update({"key": key, "value": value})
    return value


def _evaluate(candles, current=None, strategy_key=STRATEGY_KEY):
    context = _frame_context(candles)
    df = context["df"]
    o, h, l, c = context["open"], context["high"], context["low"], context["close"]
    idx = len(df) - 1
    kwargs = context["kwargs"]
    params = bt.load_strategy_params(strategy_key)
    entry_f, exit_f, direction = bt.STRATEGIES_BY_TIMEFRAME[TIMEFRAME][strategy_key]
    met, info = entry_f(o, c, h, l, idx, params, **kwargs)
    exit_met, exit_info = False, {}
    if current:
        entry_data = dict(current.get("entry_data") or {})
        if (
            strategy_key
            == "conventional_up_arrangement_valid_death_cross_short"
            and current.get("entry_price") not in (None, "")
        ):
            entry_data["price"] = float(current.get("entry_price"))
        res = exit_f(c, h, l, idx, entry_data, params, **kwargs)
        if isinstance(res, tuple):
            exit_met, exit_info = bool(res[0]), res[1] or {}
        else:
            exit_met = bool(res)
    ts = int(df.index[-1].timestamp() * 1000)
    return {"entry": bool(met), "entry_info": info or {}, "exit": exit_met,
            "exit_info": exit_info, "direction": direction, "candle_ts": ts,
            "candle_id": "%s:%s:%d" % (SYMBOL, BAR, ts), "close": c[-1],
            "params": params}


def _latest_indicator_snapshot(candles):
    context = _frame_context(candles)
    row = context["df"].iloc[-1]
    fields = (
        "open", "high", "low", "close", "ema6", "ema17", "ema19",
        "ema53", "ema75", "ema95", "k", "d", "j", "cci",
        "macd_stick", "atr14", "rsi14", "z20", "h1_slope4",
    )
    snapshot = {}
    for field in fields:
        if field not in row:
            continue
        try:
            value = float(row[field])
            if value == value and abs(value) != float("inf"):
                snapshot[field] = round(value, 10)
        except Exception:
            pass
    return snapshot


def _diagnose_not_triggered(strategy_key, candles):
    """Find one-parameter near misses without duplicating strategy logic.

    Legacy Python strategies do not expose each boolean leaf.  This bounded
    counterfactual test still records which declared threshold could turn the
    exact authoritative predicate true.  DSL strategies expose exact leaf
    failures separately.
    """
    context = _frame_context(candles)
    candle_ts = int(context["df"].index[-1].timestamp() * 1000)
    cache_key = "%s:%s" % (strategy_key, candle_ts)
    if cache_key in _NO_TRIGGER_DIAG_CACHE:
        return _NO_TRIGGER_DIAG_CACHE[cache_key]
    params = bt.load_strategy_params(strategy_key)
    metadata = bt.load_strategy_metadata(strategy_key)
    param_meta = metadata.get("param_meta") or {}
    entry_f = bt.STRATEGIES_BY_TIMEFRAME[TIMEFRAME][strategy_key][0]
    o, c = context["open"], context["close"]
    h, l = context["high"], context["low"]
    idx, kwargs = len(context["df"])-1, context["kwargs"]
    blockers = []
    checked = 0
    for name in sorted(param_meta):
        if name not in params or name in ("stop_loss_pct", "leverage"):
            continue
        spec = param_meta.get(name) or {}
        try:
            old = float(params[name]); step = float(spec.get("step"))
            low = float(spec.get("min")); high = float(spec.get("max"))
        except Exception:
            continue
        candidates = [old-step, old+step, old-3*step, old+3*step, low, high]
        seen = set()
        for value in candidates:
            value = max(low, min(high, value))
            token = round(value, 10)
            if token in seen or abs(value-old) < 1e-12:
                continue
            seen.add(token); changed = dict(params); changed[name] = value
            checked += 1
            try:
                met, _ = entry_f(o, c, h, l, idx, changed, **kwargs)
            except Exception:
                met = False
            if met:
                blockers.append({
                    "parameter": name,
                    "label": spec.get("label") or name,
                    "current_value": old,
                    "counterfactual_value": value,
                    "interpretation": "仅改变这一阈值即可使当前闭合K线触发",
                })
                break
    diagnosis = {
        "reason_code": (
            "single_parameter_near_miss" if blockers
            else "multiple_conditions_or_fixed_shape_failed"
        ),
        "single_parameter_blockers": blockers,
        "counterfactuals_checked": checked,
        "indicator_snapshot": _latest_indicator_snapshot(candles),
    }
    _NO_TRIGGER_DIAG_CACHE[cache_key] = diagnosis
    if len(_NO_TRIGGER_DIAG_CACHE) > 64:
        for old_key in list(_NO_TRIGGER_DIAG_CACHE)[:-32]:
            _NO_TRIGGER_DIAG_CACHE.pop(old_key, None)
    return diagnosis


def compute_signal_for(strategy_key, candles=None):
    meta = STRATEGIES.get(strategy_key)
    if not meta:
        return {"ok": False, "signal": "not_ready", "strategy_key": strategy_key,
                "reason": "unsupported live strategy", "time": _now()}
    loaded = {"ok": True, "candles": candles, "source": "provided"} if candles is not None else load_closed_candles()
    if not loaded.get("ok"):
        result = {"ok": False, "signal": "not_ready", "strategy_key": strategy_key,
                  "reason": loaded.get("error"), "data": loaded, "time": _now()}
    else:
        ev = _evaluate(loaded["candles"], strategy_key=strategy_key)
        entry_info = dict(ev["entry_info"] or {})
        entry_info["entry_signal_ts"] = ev["candle_ts"]
        strategy_metadata = bt.load_strategy_metadata(strategy_key)
        entry_info["strategy_version"] = strategy_metadata.get("version")
        entry_info["approved_version_hash"] = (
            strategy_metadata.get("approved_version_hash")
            or strategy_metadata.get("last_ai_consensus_hash")
        )
        entry_info["strategy_params"] = dict(ev.get("params") or {})
        diagnosis = None if ev["entry"] else _diagnose_not_triggered(
            strategy_key, loaded["candles"]
        )
        result = {"ok": True, "strategy_key": strategy_key, "strategy_name": meta["name"],
                  "symbol": SYMBOL, "timeframe": BAR, "uses_authoritative_backtest_logic": True,
                  "confirmed_candles_only": True, "signal": meta["side"] if ev["entry"] else "none",
                  "side": meta["side"] if ev["entry"] else None, "signal_candle_ts": ev["candle_ts"],
                  "signal_candle_id": "%s:%s:%s:%d" % (strategy_key, SYMBOL, BAR, ev["candle_ts"]), "entry_info": entry_info,
                  "params": ev["params"], "source": loaded.get("source"),
                  "not_triggered_diagnosis": diagnosis, "time": _now()}
    _atomic_write(meta["signal_file"], result)
    return result

def compute_signal(candles=None):
    return compute_signal_for(STRATEGY_KEY, candles=candles)

def should_close_position_for(strategy_key, current=None, signal=None, candles=None):
    if not isinstance(current, dict):
        return {"ok": True, "should_close": False, "reason": "no current position"}
    loaded = {"ok": True, "candles": candles, "source": "provided"} if candles is not None else load_closed_candles()
    if not loaded.get("ok"):
        return {"ok": False, "should_close": False, "reason": loaded.get("error")}
    ev = _evaluate(loaded["candles"], current=current, strategy_key=strategy_key)
    timed_exit = False
    live_price_exit = False
    live_price_exit_info = {}
    fixed_intraday_keys = {
        "btc5_exhaustion_reclaim_long_ai",
        "cl5_exhaustion_fade_short_ai",
        "ng5_exhaustion_fade_short_ai",
        "ng5_session_exhaustion_reclaim_long_ai",
        "xag5_session_breakdown_short_ai",
        "ltc5_exhaustion_fade_short_ai",
        "ada5_session_trend_pullback_short_ai",
        "xau15_h1_breakout_long_ai",
    }
    if strategy_key in fixed_intraday_keys:
        try:
            entry_price = float(current.get("entry_price") or (current.get("entry_data") or {}).get("price"))
            params = ev.get("params") or {}
            target_ratio = float(params.get("take_profit_price_ratio",0.006))
            side = (STRATEGIES.get(strategy_key) or {}).get("side")
            target_price = entry_price*(1.0+target_ratio if side == "long" else 1.0-target_ratio)
            raw_ticker = _okx_request("GET","/api/v5/market/ticker",params={"instId":SYMBOL},auth=False)
            rows = raw_ticker.get("data") if isinstance(raw_ticker,dict) and str(raw_ticker.get("code")) == "0" else []
            live_price = float((rows or [{}])[0].get("last"))
            live_price_exit = live_price >= target_price if side == "long" else live_price <= target_price
            if live_price_exit:
                live_price_exit_info = {"exit_type":"实时价格止盈","entry_price":entry_price,"target_price":target_price,"live_price":live_price}
            opened_ts = float(current.get("opened_at_ts") or 0)
            bar_seconds = 300 if TIMEFRAME == "5m" else 900
            timed_exit = bool(opened_ts and time.time() >= opened_ts + int(params.get("max_hold_bars",72))*bar_seconds)
        except Exception:
            live_price_exit = False
    if strategy_key == "conventional_up_arrangement_valid_death_cross_short":
        try:
            entry_price = float(
                current.get("entry_price")
                or (current.get("entry_data") or {}).get("price")
            )
            target_ratio = float(
                (ev.get("params") or {}).get(
                    "take_profit_price_ratio", 0.013
                )
            )
            target_price = entry_price*(1.0-target_ratio)
            raw_ticker = _okx_request(
                "GET", "/api/v5/market/ticker",
                params={"instId": SYMBOL}, auth=False
            )
            ticker_rows = (
                raw_ticker.get("data")
                if isinstance(raw_ticker, dict)
                and str(raw_ticker.get("code")) == "0"
                else []
            )
            live_price = float((ticker_rows or [{}])[0].get("last"))
            live_price_exit = bool(live_price <= target_price)
            if live_price_exit:
                live_price_exit_info = {
                    "exit_type": "实时价格止盈1.3%",
                    "entry_price": entry_price,
                    "target_price": target_price,
                    "live_price": live_price,
                }
        except Exception:
            live_price_exit = False
    if strategy_key in (
        "conventional_up_break_long",
        "ema53_liquidity_sweep_reclaim_long",
        "btc15_dual_cycle_downtrend_reentry_short_ai",
        "conventional_up_arrangement_valid_death_cross_short",
    ):
        entry_signal_ts = (
            int(float(current.get("opened_at_ts"))*1000)
            if (
                strategy_key
                == "conventional_up_arrangement_valid_death_cross_short"
                and current.get("opened_at_ts")
            )
            else ((current.get("entry_data") or {}).get("entry_signal_ts"))
        )
        try:
            if strategy_key == "btc15_dual_cycle_downtrend_reentry_short_ai":
                max_hold_seconds = (
                    int((ev.get("params") or {}).get("max_hold_bars", 48))
                    * 15 * 60
                )
            elif (
                strategy_key
                == "conventional_up_arrangement_valid_death_cross_short"
            ):
                max_hold_seconds = (
                    int(
                        (ev.get("params") or {}).get(
                            "max_hold_hours_without_exit", 12
                        )
                    )
                    * 60 * 60
                )
            else:
                max_hold_hours = (
                    9
                    if strategy_key == "conventional_up_break_long"
                    else int((ev.get("params") or {}).get("max_hold_hours", 18))
                )
                max_hold_seconds = max_hold_hours * 60 * 60
            timer_now_ts = (
                int(time.time()*1000)
                if (
                    strategy_key
                    == "conventional_up_arrangement_valid_death_cross_short"
                )
                else ev["candle_ts"]
            )
            timed_exit = bool(
                entry_signal_ts
                and timer_now_ts
                >= int(entry_signal_ts) + max_hold_seconds * 1000
            )
        except Exception:
            timed_exit = False
    authoritative_exit = bool(ev["exit"])
    k_indicator_take_profit_exit = bool(
        strategy_key == "ng5_exhaustion_fade_short_ai"
        and ev["exit"]
        and str((ev.get("exit_info") or {}).get("exit_type")) == "K指标止盈"
    )
    if strategy_key in fixed_intraday_keys:
        authoritative_exit = False
    if strategy_key == "conventional_up_arrangement_valid_death_cross_short":
        # A completed candle's historical low is stale for live execution.
        # This fixed-price strategy exits on the current OKX ticker instead.
        authoritative_exit = False
    should_close = bool(
        authoritative_exit or timed_exit or live_price_exit
        or k_indicator_take_profit_exit
    )
    exit_info = (
        dict(live_price_exit_info)
        if live_price_exit
        else dict(ev["exit_info"] or {})
    )
    if (
        timed_exit and not live_price_exit and not authoritative_exit
        and not k_indicator_take_profit_exit
    ):
        exit_info = {"exit_type": "定时强制平仓"}
    return {"ok": True, "should_close": should_close,
            "reason": (
                "live_price_take_profit"
                if live_price_exit
                else (
                    "k_indicator_take_profit"
                    if k_indicator_take_profit_exit
                    else (
                        "timed_forced_exit"
                        if timed_exit
                        else (
                            "authoritative_strategy_exit"
                            if authoritative_exit
                            else "exit_not_met"
                        )
                    )
                )
            ),
            "exit_info": exit_info, "signal_candle_ts": ev["candle_ts"],
            "signal_candle_id": ev["candle_id"]}

def should_close_position(current=None, signal=None):
    return should_close_position_for(STRATEGY_KEY, current=current, signal=signal)


def get_status():
    return {"ok": True, "strategy_key": STRATEGY_KEY, "strategy_name": STRATEGY_NAME,
            "authoritative_registry_key": STRATEGY_KEY, "uses_authoritative_backtest_logic": True,
            "confirmed_candles_only": True, "signal": compute_signal(), "time": _now()}
