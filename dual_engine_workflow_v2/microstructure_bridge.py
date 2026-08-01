# -*- coding: utf-8 -*-
"""Honest microstructure bridge for research probes.

Uses forward-sampled book/flow snapshots from microstructure_telemetry.db when
present.  Never invents historical L2/OI/liquidation/funding.  Overlap with
OHLCV candles is partial by design; outside the overlap window factors are None.
"""
from __future__ import print_function

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def db_path():
    return _root() / "auto_trade" / "microstructure_telemetry.db"


def status_path():
    return _root() / "auto_trade" / "microstructure_status.json"


def availability(symbol=None):
    """Report which micro dimensions are actually present (not claimed)."""
    path = db_path()
    out = {
        "ok": True,
        "db_exists": path.exists(),
        "symbol": symbol,
        "n_samples": 0,
        "span_ms": None,
        "available_dimensions": [],
        "unavailable_dimensions": [
            "historical_l2_replay",
            "open_interest",
            "liquidation_flow",
            "historical_funding",
            "cross_exchange_basis",
        ],
        "boundary_zh": (
            "仅有前向分钟级盘口/主动成交子窗采样；不是连续 L2 回放，"
            "也不是 OI/清算/资金费率历史。"
        ),
        "at": _now(),
    }
    if not path.exists():
        return out
    try:
        conn = sqlite3.connect(str(path), timeout=10)
        if symbol:
            row = conn.execute(
                "SELECT COUNT(*), MIN(observed_ms), MAX(observed_ms) "
                "FROM microstructure_samples WHERE symbol=?",
                (str(symbol),),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*), MIN(observed_ms), MAX(observed_ms) "
                "FROM microstructure_samples"
            ).fetchone()
        conn.close()
        n, mn, mx = int(row[0] or 0), row[1], row[2]
        out["n_samples"] = n
        if n > 0 and mn is not None and mx is not None:
            out["span_ms"] = [int(mn), int(mx)]
            out["span_days"] = (float(mx) - float(mn)) / 86400000.0
            out["available_dimensions"] = [
                "level2_order_book_snapshot",
                "trade_side_flow_snapshot",
                "half_spread_rate",
                "depth_imbalance",
                "trade_flow_imbalance",
                "aggression_acceleration",
            ]
    except Exception as exc:
        out["ok"] = False
        out["error"] = str(exc)[:200]
    return out


def _candle_ts_ms(row):
    ts = (row or {}).get("ts")
    if ts is None:
        return None
    try:
        v = float(ts)
    except Exception:
        return None
    # seconds vs ms
    if v < 1e12:
        v *= 1000.0
    return int(v)


def align_factors_to_candles(symbol, candles, max_rows=20000):
    """Bucket micro samples onto candle bars (last sample in bar wins).

    Returns factor series aligned 1:1 with candles.  Bars without micro data are
    None — probes must treat this as partial coverage, not fabricated history.
    """
    candles = list(candles or [])
    n = len(candles)
    empty = {
        "micro_depth_imbalance": [None] * n,
        "micro_trade_flow_imbalance": [None] * n,
        "micro_half_spread_rate": [None] * n,
        "micro_aggression_acceleration": [None] * n,
        "micro_bid_depth_usd": [None] * n,
        "micro_ask_depth_usd": [None] * n,
    }
    meta = {
        "ok": False,
        "n_candles": n,
        "n_aligned": 0,
        "coverage_ratio": 0.0,
        "available": availability(symbol),
    }
    if n < 8 or not symbol:
        meta["reason"] = "no_candles_or_symbol"
        return {"factors": empty, "meta": meta}
    avail = meta["available"]
    if not avail.get("n_samples"):
        meta["reason"] = "no_micro_samples"
        return {"factors": empty, "meta": meta}

    ts_list = [_candle_ts_ms(c) for c in candles]
    if any(t is None for t in ts_list):
        meta["reason"] = "candle_ts_unusable"
        return {"factors": empty, "meta": meta}
    t0, t1 = min(ts_list), max(ts_list)
    # bar width from median delta
    deltas = sorted(ts_list[i + 1] - ts_list[i] for i in range(n - 1) if ts_list[i + 1] > ts_list[i])
    bar_ms = int(deltas[len(deltas) // 2]) if deltas else 300000
    bar_ms = max(60000, bar_ms)

    try:
        conn = sqlite3.connect(str(db_path()), timeout=15)
        rows = conn.execute(
            "SELECT observed_ms, depth_imbalance, trade_flow_imbalance, "
            "half_spread_rate, aggression_acceleration, bid_depth_usd, ask_depth_usd "
            "FROM microstructure_samples WHERE symbol=? AND observed_ms>=? AND observed_ms<=? "
            "ORDER BY observed_ms ASC LIMIT ?",
            (str(symbol), int(t0 - bar_ms), int(t1 + bar_ms), int(max_rows)),
        ).fetchall()
        conn.close()
    except Exception as exc:
        meta["reason"] = "db_read_failed:%s" % str(exc)[:120]
        return {"factors": empty, "meta": meta}

    # map each sample to the candle whose [ts, ts+bar) contains it
    # candles assumed sorted
    idx = 0
    factors = {k: list(v) for k, v in empty.items()}
    aligned = 0
    for obs_ms, depth_imb, flow_imb, spread, aggr, bid_d, ask_d in rows:
        while idx + 1 < n and ts_list[idx + 1] <= obs_ms:
            idx += 1
        if idx >= n:
            break
        # accept sample if within this bar or slightly after open
        if obs_ms < ts_list[idx] - bar_ms:
            continue
        if idx + 1 < n and obs_ms >= ts_list[idx + 1] + bar_ms:
            continue
        factors["micro_depth_imbalance"][idx] = depth_imb
        factors["micro_trade_flow_imbalance"][idx] = flow_imb
        factors["micro_half_spread_rate"][idx] = spread
        factors["micro_aggression_acceleration"][idx] = aggr
        factors["micro_bid_depth_usd"][idx] = bid_d
        factors["micro_ask_depth_usd"][idx] = ask_d
        aligned += 1

    covered = sum(
        1 for i in range(n) if factors["micro_depth_imbalance"][i] is not None
    )
    meta.update({
        "ok": covered > 0,
        "n_samples_read": len(rows),
        "n_aligned": aligned,
        "n_bars_covered": covered,
        "coverage_ratio": float(covered) / float(n) if n else 0.0,
        "bar_ms": bar_ms,
        "reason": "aligned" if covered else "no_overlap",
        "at": _now(),
    })
    return {"factors": factors, "meta": meta}


def enrich_contract(contract, symbol=None):
    """Mutate/return contract with honest micro availability flags."""
    c = dict(contract or {})
    avail = availability(symbol or c.get("symbol"))
    available = list(c.get("available_data") or [])
    unavailable = list(c.get("unavailable_data") or [])
    if avail.get("n_samples"):
        for dim in (
            "level2_order_book_snapshot",
            "trade_side_flow_snapshot",
            "microstructure_forward_samples",
        ):
            if dim not in available:
                available.append(dim)
        # still not full historical feeds
        for dim in (
            "historical_l2_replay",
            "open_interest",
            "liquidation_flow",
            "historical_funding",
            "cross_exchange_basis",
        ):
            if dim not in unavailable:
                unavailable.append(dim)
    c["available_data"] = available
    c["unavailable_data"] = unavailable
    c["microstructure"] = {
        "n_samples": avail.get("n_samples"),
        "span_days": avail.get("span_days"),
        "available_dimensions": avail.get("available_dimensions"),
        "boundary_zh": avail.get("boundary_zh"),
    }
    return c


def inject_into_factor_matrix(factor_matrix, symbol, candles):
    """Add micro factors when overlap exists; return meta for ledger."""
    matrix = factor_matrix if factor_matrix is not None else {}
    pack = align_factors_to_candles(symbol, candles)
    for name, series in (pack.get("factors") or {}).items():
        if any(v is not None for v in series):
            matrix[name] = series
    return matrix, pack.get("meta") or {}


def probe():
    return {
        "ok": True,
        "provider": "microstructure_bridge_v1",
        "db": str(db_path()),
        "at": _now(),
    }
