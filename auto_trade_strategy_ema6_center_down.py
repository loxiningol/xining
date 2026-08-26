# -*- coding: utf-8 -*-
"""Live adapter for the authoritative 1h/15m/5m backtest strategies.

No trading rule is duplicated here. Entry and exit are delegated to the
timeframe-specific registry in backtest_engine_v2 with parameters from the
same experimental_strategies.json used by the website backtester.
"""
from pathlib import Path
import hashlib
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
# Confirm() may mount conditional_exits with close_fraction < 1 only when this
# evaluator reports should_partial_close and the formal executor can reduce.
LIVE_CONDITIONAL_PARTIAL_EXITS = True
import auto_trade_slot_paths as slot_paths
TIMEFRAME = slot_paths.normalize_timeframe(TIMEFRAME)
INSTANCE_SUFFIX = slot_paths.instance_suffix(SYMBOL, TIMEFRAME)
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
DSL_CONFIG_FILE = ROOT / "strategy_configs" / "ai_dsl_strategies.json"
SEMANTIC_CONFIG_FILE = ROOT / "strategy_configs" / "semantic_live_strategies.json"
_STRATEGY_CONFIG_MTIMES = {"dsl": None, "semantic": None}
_SEMANTIC_STRATEGIES = {}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _mtime_ns(path):
    try:
        stat = Path(path).stat()
        return getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1000000000))
    except Exception:
        return None


def _refresh_configured_strategies(force=False):
    """Hot-load approved strategies without waiting for a service restart.

    A strategy file may be updated after the daemon process starts.  The old
    process used a registry frozen at import time and then reported every new
    approved strategy as unsupported.  Validation is completed before the
    last-known-good in-memory registry is changed.
    """
    dsl_mtime = _mtime_ns(DSL_CONFIG_FILE)
    if force or dsl_mtime != _STRATEGY_CONFIG_MTIMES.get("dsl"):
        try:
            bt._register_dsl_strategies()
            for key, definition in getattr(bt, "DSL_STRATEGY_DEFINITIONS", {}).items():
                if (
                    definition.get("live_enabled") is True
                    and definition.get("timeframe") == TIMEFRAME
                    and SYMBOL in (definition.get("supported_instruments") or [])
                ):
                    STRATEGIES[key] = {
                        "name": definition.get("name") or key,
                        "side": definition.get("direction"),
                        "signal_file": AUTO_DIR / (
                            "formal_dsl_%s_signal%s.json" % (key, INSTANCE_SUFFIX)
                        ),
                    }
            _STRATEGY_CONFIG_MTIMES["dsl"] = dsl_mtime
        except Exception:
            pass

    semantic_mtime = _mtime_ns(SEMANTIC_CONFIG_FILE)
    if force or semantic_mtime != _STRATEGY_CONFIG_MTIMES.get("semantic"):
        try:
            from dual_engine_workflow_v2 import ast_compiler
            from dual_engine_workflow_v2.exit_dsl import validate_exit_plan

            payload = _read_json(SEMANTIC_CONFIG_FILE, {"strategies": []})
            desired = {}
            formal_features = ast_compiler.formal_feature_registry()
            for raw in payload.get("strategies") or []:
                if not isinstance(raw, dict):
                    continue
                if raw.get("timeframe") != TIMEFRAME:
                    continue
                if SYMBOL not in (raw.get("supported_instruments") or []):
                    continue
                key = str(raw.get("key") or "")
                direction = str(raw.get("direction") or "").lower()
                stop_pct = float(raw.get("protective_stop_pct"))
                entry_check = ast_compiler.validate_ast(
                    raw.get("entry_ast"), feature_registry_list=formal_features,
                )
                exit_check = validate_exit_plan(
                    raw.get("exit_plan"), feature_registry_list=formal_features,
                )
                if (
                    not key or direction not in ("long", "short")
                    or not (0.0 < stop_pct < 1.0)
                    or not entry_check.get("ok") or not exit_check.get("ok")
                ):
                    raise ValueError("invalid semantic live strategy: %s" % (key or "-"))
                definition = dict(raw)
                definition["entry_ast"] = entry_check.get("normalized")
                definition["exit_plan"] = exit_check.get("normalized")
                desired[key] = definition
            for old_key in list(_SEMANTIC_STRATEGIES):
                if old_key not in desired:
                    STRATEGIES.pop(old_key, None)
            _SEMANTIC_STRATEGIES.clear()
            _SEMANTIC_STRATEGIES.update(desired)
            for key, definition in desired.items():
                STRATEGIES[key] = {
                    "name": definition.get("name") or key,
                    "side": definition.get("direction"),
                    "signal_file": AUTO_DIR / (
                        "formal_semantic_%s_signal%s.json" % (key, INSTANCE_SUFFIX)
                    ),
                }
            _STRATEGY_CONFIG_MTIMES["semantic"] = semantic_mtime
        except Exception:
            # Fail-safe hot reload: malformed new files never erase the last
            # validated in-memory version used to manage an open position.
            pass


def _semantic_entry_evaluation(definition, candles):
    from dual_engine_workflow_v2 import ast_compiler
    from dual_engine_workflow_v2.easyquant_bridge import _build_factor_matrix

    matrix = _build_factor_matrix(candles, symbol=SYMBOL, timeframe=TIMEFRAME)
    compiled = ast_compiler.compile_ast_to_mask(
        definition.get("entry_ast"), matrix,
        feature_registry_list=ast_compiler.formal_feature_registry(),
    )
    mask = list(compiled.get("mask") or [])
    if not compiled.get("ok") or len(mask) != len(candles):
        missing = []
        stack = [definition.get("entry_ast")]
        seen = set()
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            feat = node.get("feature")
            if feat and feat not in seen:
                seen.add(feat)
                series = list(matrix.get(feat) or [])
                if len(series) != len(candles):
                    missing.append("%s(len=%d)" % (feat, len(series)))
            for child in (node.get("children") or []):
                stack.append(child)
            if isinstance(node.get("child"), dict):
                stack.append(node.get("child"))
            for step in (node.get("steps") or []):
                if isinstance(step, dict):
                    stack.append(step.get("event"))
        detail = compiled.get("error") or (compiled.get("compile_report") or {}).get("errors")
        raise ValueError(
            "semantic entry compile failed: mask_len=%d candles=%d missing=%s detail=%s"
            % (len(mask), len(candles), missing or None, detail)
        )
    atr_values = list(matrix.get("atr_pct_14") or [])
    atr_pct = atr_values[-1] if atr_values else None
    candle_ts = int(candles[-1]["ts"])
    identity = hashlib.sha256(json.dumps(
        {
            "entry_ast": definition.get("entry_ast"),
            "exit_plan": definition.get("exit_plan"),
            "protective_stop_pct": definition.get("protective_stop_pct"),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {
        "entry": bool(mask[-1]),
        "candle_ts": candle_ts,
        "candle_id": "%s:%s:%s:%d" % (
            definition.get("key"), SYMBOL, BAR, candle_ts,
        ),
        "entry_info": {
            "price": float(candles[-1]["close"]),
            "entry_signal_ts": candle_ts,
            "entry_atr_pct": float(atr_pct) if atr_pct is not None else None,
            "protective_stop_pct": float(definition.get("protective_stop_pct")),
            "semantic_strategy_hash": identity,
            "exit_plan": definition.get("exit_plan"),
            "execution_contract": "closed_signal_bar_then_market_entry",
            "atr_reference_contract": "last_fully_closed_signal_bar",
        },
    }


def _semantic_trigger_unit(trigger):
    """Map creator DSL units onto the live evaluator.

    `exit_dsl.TRIGGER_UNITS` is `percent|atr`.  Older live code only accepted
    `pct`, so a valid `percent` plan never produced a take-profit distance.
    """
    unit = str((trigger or {}).get("unit") or "").strip().lower()
    if unit in ("percent", "pct", "%", "percentage"):
        return "percent"
    if unit == "atr":
        return "atr"
    return ""


def _semantic_trigger_distance(trigger, entry_price, atr_value):
    trigger = trigger or {}
    value = float(trigger.get("value") or 0.0)
    unit = _semantic_trigger_unit(trigger)
    if unit == "atr":
        return value * atr_value
    if unit == "percent":
        return value * entry_price
    return None


def _exit_plan_needs_atr(plan):
    """True when any authored trigger is ATR-denominated."""
    triggers = []
    plan = plan or {}
    triggers.append(plan.get("primary_take_profit"))
    trailing = plan.get("trailing") or {}
    if isinstance(trailing, dict):
        triggers.extend([trailing.get("activation"), trailing.get("distance")])
    breakeven = plan.get("breakeven") or {}
    if isinstance(breakeven, dict):
        triggers.append(breakeven.get("activation"))
    for row in list(plan.get("partial_exits") or []):
        triggers.append((row or {}).get("trigger"))
    return any(_semantic_trigger_unit(item) == "atr" for item in triggers)


def _atr_pct_from_candles(candles, period=14):
    rows = [
        row for row in (candles or [])
        if row.get("high") is not None and row.get("low") is not None
        and row.get("close") is not None
    ]
    if len(rows) < period + 1:
        return None
    trs = []
    start = len(rows) - period
    for j in range(start, len(rows)):
        high = float(rows[j]["high"])
        low = float(rows[j]["low"])
        prev_close = float(rows[j - 1]["close"])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    close = float(rows[-1]["close"])
    if close <= 0.0:
        return None
    return (sum(trs) / float(period)) / close


def _with_live_price_bar(candles, live_price, bar_seconds, now_ts=None):
    """Overlay the in-progress bar so conditions can fire before the close."""
    rows = [dict(row) for row in (candles or [])]
    try:
        live_price = float(live_price)
    except (TypeError, ValueError):
        return rows
    if live_price <= 0.0 or bar_seconds <= 0:
        return rows
    now_ms = int((time.time() if now_ts is None else float(now_ts)) * 1000.0)
    bar_ms = int(float(bar_seconds) * 1000.0)
    period_start = (now_ms // bar_ms) * bar_ms
    if rows and int(rows[-1].get("ts") or 0) >= period_start:
        last = dict(rows[-1])
        last["close"] = live_price
        last["high"] = max(float(last.get("high") or live_price), live_price)
        last["low"] = min(float(last.get("low") or live_price), live_price)
        rows[-1] = last
        return rows
    open_px = float(rows[-1]["close"]) if rows else live_price
    rows.append({
        "ts": period_start,
        "open": open_px,
        "high": max(open_px, live_price),
        "low": min(open_px, live_price),
        "close": live_price,
    })
    return rows


def _conditional_exit_ast(when):
    if isinstance(when, dict) and when.get("type") == "market_ast":
        return when.get("ast")
    return when


def _compile_conditional_mask(when, candles, symbol, timeframe):
    from dual_engine_workflow_v2 import ast_compiler
    from dual_engine_workflow_v2.easyquant_bridge import _build_factor_matrix
    matrix = _build_factor_matrix(candles, symbol=symbol, timeframe=timeframe)
    compiled = ast_compiler.compile_ast_to_mask(
        _conditional_exit_ast(when), matrix,
        feature_registry_list=ast_compiler.formal_feature_registry(),
    )
    mask = list(compiled.get("mask") or [])
    return bool(compiled.get("ok") and mask and mask[-1])


def _strip_unclosed_bars(candles, bar_seconds, now_ts=None):
    """Drop the in-progress timeframe bucket even if a caller passed it in."""
    try:
        bar_ms = int(float(bar_seconds) * 1000.0)
    except (TypeError, ValueError):
        return list(candles or [])
    if bar_ms <= 0:
        return list(candles or [])
    now_ms = int((time.time() if now_ts is None else float(now_ts)) * 1000.0)
    period_start = (now_ms // bar_ms) * bar_ms
    out = []
    for bar in candles or []:
        try:
            ts = int((bar or {}).get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if 0 < ts < period_start:
            out.append(bar)
    return out


def _has_closed_bar_after_signal(closed_candles, signal_ts):
    """True once at least one completed bar exists after the entry signal bar.

    Invalidation AST must not see the in-progress live bar. Without this gate a
    1h short can scratch on the same forming hour that just filled.
    """
    try:
        gate = int(signal_ts or 0)
    except (TypeError, ValueError):
        gate = 0
    if gate <= 0:
        return False
    for bar in closed_candles or []:
        try:
            ts = int((bar or {}).get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts > gate:
            return True
    return False


def _clamp_trail_stop(stop_price, entry_price, direction):
    """Once trailing is armed, never give back past entry."""
    if direction == "long":
        return max(float(stop_price), float(entry_price))
    return min(float(stop_price), float(entry_price))


def _filled_original_fraction(receipts):
    """Sum filled reductions as fractions of the original filled size."""
    total = 0.0
    for rec in (receipts or {}).values():
        if not isinstance(rec, dict):
            continue
        if rec.get("status") != "filled":
            continue
        try:
            value = float(rec.get("fraction_of_original") or 0.0)
        except (TypeError, ValueError):
            continue
        if value > 0.0:
            total += value
    if total < 0.0:
        return 0.0
    if total > 1.0:
        return 1.0
    return total


def _conditional_close_fraction(row):
    """Backtest contract: fraction of the *remaining* position, default all."""
    try:
        value = float((row or {}).get("close_fraction"))
    except (TypeError, ValueError):
        return 1.0
    if value <= 0.0 or value > 1.0:
        return 1.0
    return value


def _semantic_live_exit(definition, current, candles):
    """Evaluate the continuous creator exit against actual filled entry data."""
    entry_data = dict(current.get("entry_data") or {})
    entry_price = float(current.get("entry_price") or entry_data.get("price") or 0.0)
    if entry_price <= 0.0:
        return {"ok": False, "should_close": False, "reason": "entry_price_missing"}
    plan = definition.get("exit_plan") or {}
    atr_pct = float(entry_data.get("entry_atr_pct") or 0.0)
    if atr_pct <= 0.0:
        recovered = _atr_pct_from_candles(candles)
        atr_pct = float(recovered or 0.0)
    if _exit_plan_needs_atr(plan) and atr_pct <= 0.0:
        return {"ok": False, "should_close": False, "reason": "entry_atr_pct_missing"}
    direction = str(definition.get("direction") or "long").lower()
    sign = 1.0 if direction == "long" else -1.0
    atr_value = entry_price * atr_pct
    signal_ts = int(float(entry_data.get("entry_signal_ts") or 0))
    insts = definition.get("supported_instruments") or []
    inst_id = str(insts[0] if insts else SYMBOL)
    bar_seconds = {"5m": 300, "15m": 900, "1h": 3600}.get(TIMEFRAME, 3600)
    raw = _okx_request(
        "GET", "/api/v5/market/ticker", params={"instId": inst_id}, auth=False,
    )
    ticker_rows = raw.get("data") if isinstance(raw, dict) and str(raw.get("code")) == "0" else []
    live_price = float((ticker_rows or [{}])[0].get("last"))
    eval_candles = _with_live_price_bar(candles, live_price, bar_seconds)
    active_rows = [row for row in eval_candles if int(row.get("ts") or 0) > signal_ts]
    highs = [float(row["high"]) for row in active_rows]
    lows = [float(row["low"]) for row in active_rows]
    best_price = (
        max([entry_price, live_price] + highs) if direction == "long"
        else min([entry_price, live_price] + lows)
    )
    favorable = sign * (best_price - entry_price) / entry_price

    # Partial exits are expressed as fractions of the original filled size in
    # the creator/backtest contract.  The executor owns the durable receipt;
    # this evaluator only reports the next unfired target.  A prepared or
    # uncertain receipt is deliberately reported again so the executor can
    # reconcile the same clOrdId instead of submitting a second reduction.
    partial_receipts = dict(current.get("partial_exit_receipts") or {})
    for index, row in enumerate(list(plan.get("partial_exits") or [])):
        event_id = "semantic_partial_exit_%d" % index
        receipt = dict(partial_receipts.get(event_id) or {})
        if receipt.get("status") == "filled":
            continue
        distance = _semantic_trigger_distance(
            row.get("trigger"), entry_price, atr_value,
        )
        target = entry_price + sign * distance if distance is not None else None
        target_hit = target is not None and (
            live_price >= target if direction == "long" else live_price <= target
        )
        historical_hit = target is not None and any(
            (float(bar["high"]) >= target if direction == "long"
             else float(bar["low"]) <= target)
            for bar in active_rows
        )
        if target_hit or historical_hit or receipt:
            return {
                "ok": True,
                "should_close": False,
                "should_partial_close": True,
                "reason": "semantic_partial_exit",
                "partial_event_id": event_id,
                "fraction_of_original": float(row.get("fraction")),
                "exit_info": {
                    "exit_type": "分批止盈",
                    "partial_exit_index": index,
                    "target_price": target,
                    "live_price": live_price,
                    "historical_hit": historical_hit,
                    "receipt_status": receipt.get("status"),
                },
            }

    primary = plan.get("primary_take_profit")
    if primary:
        distance = _semantic_trigger_distance(primary, entry_price, atr_value)
        target = entry_price + sign * distance if distance is not None else None
        target_hit = target is not None and (
            live_price >= target if direction == "long" else live_price <= target
        )
        historical_hit = target is not None and any(
            (float(row["high"]) >= target if direction == "long" else float(row["low"]) <= target)
            for row in active_rows
        )
        if target_hit or historical_hit:
            return {
                "ok": True, "should_close": True,
                "reason": "semantic_primary_take_profit",
                "exit_info": {"exit_type": "连续目标止盈", "target_price": target,
                              "live_price": live_price, "historical_hit": historical_hit},
            }

    eval_bar_ts = int((eval_candles[-1] or {}).get("ts") or 0) if eval_candles else 0
    closed_candles = _strip_unclosed_bars(list(candles or []), bar_seconds)
    invalidation_ready = _has_closed_bar_after_signal(closed_candles, signal_ts)
    for index, row in enumerate(list(plan.get("conditional_exits") or [])):
        role = str(row.get("role") or "invalidation")
        if role == "take_profit":
            candles_for_mask = eval_candles
            intrabar = True
        else:
            if not invalidation_ready:
                continue
            candles_for_mask = closed_candles
            intrabar = False
        try:
            condition_met = _compile_conditional_mask(
                row.get("when"), candles_for_mask, inst_id, TIMEFRAME,
            )
        except Exception:
            condition_met = False
        if not condition_met:
            continue
        exit_type = "条件止盈" if role == "take_profit" else "条件失效平仓"
        close_fraction = _conditional_close_fraction(row)
        condition_id = str(
            row.get("condition_id") or ("condition_%02d" % (index + 1))
        )
        info = {
            "exit_type": exit_type,
            "condition_id": condition_id,
            "live_price": live_price,
            "intrabar": intrabar,
            "close_fraction": close_fraction,
        }
        if close_fraction >= 1.0 - 1e-12:
            return {
                "ok": True, "should_close": True,
                "reason": "semantic_conditional_exit",
                "exit_info": info,
            }
        remaining = 1.0 - _filled_original_fraction(partial_receipts)
        live_frac = remaining * close_fraction
        event_id = "semantic_conditional_partial_%s_%s" % (
            condition_id, eval_bar_ts,
        )
        receipt = dict(partial_receipts.get(event_id) or {})
        if receipt.get("status") == "filled":
            continue
        if live_frac <= 1e-12:
            continue
        info["remaining_original_fraction"] = remaining
        info["receipt_status"] = receipt.get("status")
        return {
            "ok": True,
            "should_close": False,
            "should_partial_close": True,
            "reason": "semantic_conditional_partial_exit",
            "partial_event_id": event_id,
            "fraction_of_original": live_frac,
            "exit_info": info,
        }

    dynamic_stops = []
    breakeven = plan.get("breakeven")
    if breakeven:
        activation = _semantic_trigger_distance(
            breakeven.get("activation"), entry_price, atr_value,
        )
        if activation is not None and favorable + 1e-12 >= activation / entry_price:
            dynamic_stops.append((
                entry_price * (1.0 + sign * float(breakeven.get("offset_pct") or 0.0)),
                "保本退出",
            ))
    trailing = plan.get("trailing")
    if trailing:
        activation = _semantic_trigger_distance(
            trailing.get("activation"), entry_price, atr_value,
        )
        distance = _semantic_trigger_distance(
            trailing.get("distance"), entry_price, atr_value,
        )
        if (
            activation is not None and distance is not None
            and favorable + 1e-12 >= activation / entry_price
        ):
            raw_stop = best_price - sign * distance
            dynamic_stops.append((
                _clamp_trail_stop(raw_stop, entry_price, direction),
                "动态追踪退出",
            ))
    elif plan.get("primary_take_profit"):
        primary_distance = _semantic_trigger_distance(
            plan.get("primary_take_profit"), entry_price, atr_value,
        )
        if primary_distance:
            activation = 0.5 * primary_distance
            distance = 0.25 * primary_distance
            if favorable + 1e-12 >= activation / entry_price:
                raw_stop = best_price - sign * distance
                dynamic_stops.append((
                    _clamp_trail_stop(raw_stop, entry_price, direction),
                    "派生追踪退出",
                ))
    for stop_price, exit_type in dynamic_stops:
        hit = live_price <= stop_price if direction == "long" else live_price >= stop_price
        if hit:
            return {
                "ok": True, "should_close": True, "reason": "semantic_dynamic_exit",
                "exit_info": {"exit_type": exit_type, "stop_price": stop_price,
                              "live_price": live_price, "best_price": best_price},
            }

    max_bars = int(plan.get("max_holding_bars") or 0)
    bar_seconds = {"5m": 300, "15m": 900, "1h": 3600}.get(TIMEFRAME, 3600)
    opened_at = float(current.get("opened_at_ts") or 0.0)
    if (
        max_bars > 0 and signal_ts
        and definition.get("timed_exit_anchor") == "closed_signal_bar_boundary"
    ):
        signal_seconds = (
            float(signal_ts) / 1000.0 if float(signal_ts) > 100000000000.0
            else float(signal_ts)
        )
        scheduled_ts = signal_seconds + (max_bars + 1) * bar_seconds
        if time.time() >= scheduled_ts:
            return {
                "ok": True, "should_close": True,
                "reason": "semantic_second_candle_open_exit",
                "exit_info": {
                    "exit_type": "第二根K线开盘平仓",
                    "max_holding_bars": max_bars,
                    "scheduled_ts": scheduled_ts,
                },
            }
    if max_bars > 0 and opened_at and time.time() >= opened_at + max_bars * bar_seconds:
        return {
            "ok": True, "should_close": True, "reason": "semantic_timed_exit",
            "exit_info": {"exit_type": "定时强制平仓", "max_holding_bars": max_bars},
        }
    return {
        "ok": True, "should_close": False, "reason": "semantic_exit_not_met",
        "exit_info": {"live_price": live_price, "best_price": best_price,
                      "favorable_excursion": favorable},
    }


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
    frame = bt.precompute_indicators(
        df[["open", "high", "low", "close"]], timeframe=TIMEFRAME
    )
    # The research/backtest path defines h1_slope4 on a native 1h frame as
    # the four-bar percentage change of EMA19.  The generic indicator builder
    # only adds its resampled form for 5m/15m frames, so reproduce the exact
    # research definition here instead of making every native-1h DSL strategy
    # fail permanently with a missing feature.
    if TIMEFRAME == "1h" and "h1_slope4" not in frame.columns:
        frame["h1_slope4"] = (
            frame["ema19"].astype(float)
            / frame["ema19"].astype(float).shift(4) - 1.0
        )
    return frame


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
    _refresh_configured_strategies()
    meta = STRATEGIES.get(strategy_key)
    if not meta:
        return {"ok": False, "signal": "not_ready", "strategy_key": strategy_key,
                "reason": "unsupported live strategy", "time": _now()}
    loaded = {"ok": True, "candles": candles, "source": "provided"} if candles is not None else load_closed_candles()
    if not loaded.get("ok"):
        result = {"ok": False, "signal": "not_ready", "strategy_key": strategy_key,
                  "reason": loaded.get("error"), "data": loaded, "time": _now()}
    elif strategy_key in _SEMANTIC_STRATEGIES:
        try:
            ev = _semantic_entry_evaluation(
                _SEMANTIC_STRATEGIES[strategy_key], loaded["candles"],
            )
            result = {
                "ok": True,
                "strategy_key": strategy_key,
                "strategy_name": meta["name"],
                "symbol": SYMBOL,
                "timeframe": BAR,
                "uses_authoritative_semantic_creator_logic": True,
                "confirmed_candles_only": True,
                "signal": meta["side"] if ev["entry"] else "none",
                "side": meta["side"] if ev["entry"] else None,
                "signal_candle_ts": ev["candle_ts"],
                "signal_candle_id": ev["candle_id"],
                "entry_info": ev["entry_info"],
                "source": loaded.get("source"),
                "time": _now(),
            }
        except Exception as exc:
            result = {
                "ok": False, "signal": "not_ready", "strategy_key": strategy_key,
                "reason": "semantic live evaluation failed: %s" % str(exc)[:240],
                "time": _now(),
            }
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
    _refresh_configured_strategies()
    loaded = {"ok": True, "candles": candles, "source": "provided"} if candles is not None else load_closed_candles()
    if not loaded.get("ok"):
        return {"ok": False, "should_close": False, "reason": loaded.get("error")}
    if strategy_key in _SEMANTIC_STRATEGIES:
        return _semantic_live_exit(
            _SEMANTIC_STRATEGIES[strategy_key], current, loaded["candles"],
        )
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
