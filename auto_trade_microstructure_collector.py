# -*- coding: utf-8 -*-
"""Low-memory forward microstructure sampler for active-hunt research.

Public/read-only OKX endpoints only.  One bounded process samples five book
levels plus the latest public trades, stores minute-level derived features,
then exits.  It never places, amends, cancels or sizes an order.
"""
from __future__ import print_function

import glob
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import time
from pathlib import Path

import auto_trade_okx as okx


ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
DB_PATH = AUTO_DIR / "microstructure_telemetry.db"
STATUS_PATH = AUTO_DIR / "microstructure_status.json"
RESEARCH_TARGETS_PATH = AUTO_DIR / "strategy_creation_targets.json"
RETENTION_DAYS = 35
MAX_RESEARCH_EXTRA_PER_RUN = 2
FALLBACK_SYMBOLS = (
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "BNB-USDT-SWAP",
    "XRP-USDT-SWAP", "ADA-USDT-SWAP", "DOGE-USDT-SWAP", "LTC-USDT-SWAP",
    "LINK-USDT-SWAP", "AVAX-USDT-SWAP", "SUI-USDT-SWAP", "DOT-USDT-SWAP",
    "ATOM-USDT-SWAP", "NEAR-USDT-SWAP", "APT-USDT-SWAP",
    "XAU-USDT-SWAP", "XAG-USDT-SWAP", "CL-USDT-SWAP", "NG-USDT-SWAP",
)


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _read(path, default):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except Exception:
        return default


def _atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        delete=False, dir=str(path.parent), mode="w", encoding="utf-8")
    try:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush(); handle.close(); Path(handle.name).replace(path)
        os.chmod(str(path), 0o600)
    except Exception:
        try: Path(handle.name).unlink()
        except Exception: pass
        raise


def _symbol_plan(now=None):
    core = set()
    for path in glob.glob(str(AUTO_DIR / "formal_daemon_config*.json")):
        symbol = str(_read(path, {}).get("symbol") or "").upper()
        if symbol.endswith("-SWAP"):
            core.add(symbol)
    if not core:
        core.update(FALLBACK_SYMBOLS)
    research = set()
    target_config = _read(RESEARCH_TARGETS_PATH, {})
    for symbol in (target_config.get("matrix") or {}).get("symbols") or []:
        symbol = str(symbol or "").upper()
        if symbol.endswith("-SWAP"):
            research.add(symbol)
    for row in target_config.get("targets") or []:
        symbol = str(row.get("symbol") or "").upper()
        if row.get("enabled", True) and symbol.endswith("-SWAP"):
            research.add(symbol)
    extras = sorted(research-core)
    selected_extras = []
    if extras:
        minute = int((time.time() if now is None else float(now))//60)
        start = (minute*MAX_RESEARCH_EXTRA_PER_RUN) % len(extras)
        selected_extras = [extras[(start+offset) % len(extras)]
                           for offset in range(min(MAX_RESEARCH_EXTRA_PER_RUN,
                                                   len(extras)))]
    return {"core_symbols": sorted(core), "research_symbols": sorted(research),
            "rotating_research_extras": selected_extras,
            "deferred_research_extras": [value for value in extras
                                         if value not in selected_extras],
            "sampled_symbols": sorted(core | set(selected_extras)),
            "policy": "all_live_core_plus_two_research_only_symbols_per_minute"}


def _symbols():
    return _symbol_plan()["sampled_symbols"]


def _connect():
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS microstructure_samples(
      sample_key TEXT PRIMARY KEY, symbol TEXT NOT NULL,
      observed_ms INTEGER NOT NULL, bid REAL NOT NULL, ask REAL NOT NULL,
      half_spread_rate REAL NOT NULL, bid_depth_usd REAL NOT NULL,
      ask_depth_usd REAL NOT NULL, depth_imbalance REAL NOT NULL,
      trade_count INTEGER NOT NULL, buy_notional_usd REAL NOT NULL,
      sell_notional_usd REAL NOT NULL, trade_flow_imbalance REAL NOT NULL,
      aggression_acceleration REAL NOT NULL, trade_window_ms INTEGER NOT NULL,
      created_at TEXT NOT NULL, subwindows_json TEXT NOT NULL DEFAULT '{}')""")
    columns = set(row[1] for row in conn.execute(
        "PRAGMA table_info(microstructure_samples)").fetchall())
    if "subwindows_json" not in columns:
        conn.execute("ALTER TABLE microstructure_samples ADD COLUMN "
                     "subwindows_json TEXT NOT NULL DEFAULT '{}'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_micro_symbol_time "
                 "ON microstructure_samples(symbol,observed_ms)")
    try: os.chmod(str(DB_PATH), 0o600)
    except Exception: pass
    return conn


def _notional(price, size, ct_val, ct_type, ct_val_ccy):
    if str(ct_type).lower() == "inverse":
        return size*ct_val
    if str(ct_type).lower() == "linear":
        return size*ct_val*price
    return size*ct_val if ct_val_ccy in ("USD", "USDT", "USDC") else size*ct_val*price


def _depth(levels, ct_val, ct_type, ct_val_ccy):
    total = 0.0
    for level in levels or []:
        price = _number(level[0] if len(level) > 0 else None)
        size = _number(level[1] if len(level) > 1 else None)
        if price and size and size > 0:
            total += _notional(price, size, ct_val, ct_type, ct_val_ccy)
    return total


def _trade_subwindows(trades, observed_ms):
    result = {}
    for window_ms in (5000, 15000, 60000):
        rows = [value for timestamp, value in trades
                if timestamp >= observed_ms-window_ms]
        buy = sum(max(0.0, value) for value in rows)
        sell = sum(max(0.0, -value) for value in rows)
        total = buy+sell
        result[str(window_ms)] = {
            "trade_count": len(rows), "buy_notional_usd": round(buy, 6),
            "sell_notional_usd": round(sell, 6),
            "trade_flow_imbalance": round((buy-sell)/total, 8) if total else 0.0,
        }
    return result


def _sample_symbol(symbol):
    instrument_response = okx._okx_request(
        "GET", "/api/v5/public/instruments",
        params={"instType": "SWAP", "instId": symbol}, auth=False)
    instrument = (instrument_response.get("data") or [])[0]
    ct_val = _number(instrument.get("ctVal"))
    if not ct_val or ct_val <= 0:
        raise ValueError("invalid ctVal")
    ct_type = str(instrument.get("ctType") or "").lower()
    ct_val_ccy = str(instrument.get("ctValCcy") or "").upper()
    book_response = okx._okx_request(
        "GET", "/api/v5/market/books",
        params={"instId": symbol, "sz": "5"}, auth=False)
    book = (book_response.get("data") or [])[0]
    bids = book.get("bids") or []; asks = book.get("asks") or []
    bid = _number((bids or [[None]])[0][0]); ask = _number((asks or [[None]])[0][0])
    if not bid or not ask or ask < bid:
        raise ValueError("invalid top of book")
    observed_ms = int(book.get("ts") or time.time()*1000)
    midpoint = (bid+ask)/2.0
    half_spread = (ask-bid)/(2.0*midpoint)
    bid_depth = _depth(bids, ct_val, ct_type, ct_val_ccy)
    ask_depth = _depth(asks, ct_val, ct_type, ct_val_ccy)
    depth_total = bid_depth+ask_depth
    depth_imbalance = ((bid_depth-ask_depth)/depth_total if depth_total > 0 else 0.0)

    trade_response = okx._okx_request(
        "GET", "/api/v5/market/trades",
        params={"instId": symbol, "limit": "100"}, auth=False)
    trades = []
    for row in trade_response.get("data") or []:
        price = _number(row.get("px")); size = _number(row.get("sz"))
        timestamp = int(row.get("ts") or 0)
        if not price or not size or timestamp <= 0:
            continue
        value = _notional(price, size, ct_val, ct_type, ct_val_ccy)
        sign = 1.0 if str(row.get("side") or "").lower() == "buy" else -1.0
        trades.append((timestamp, sign*value))
    trades.sort()
    buy = sum(max(0.0, value) for _timestamp, value in trades)
    sell = sum(max(0.0, -value) for _timestamp, value in trades)
    trade_total = buy+sell
    flow = (buy-sell)/trade_total if trade_total > 0 else 0.0
    window_ms = trades[-1][0]-trades[0][0] if len(trades) > 1 else 0
    acceleration = 0.0
    if len(trades) >= 4 and window_ms > 0:
        midpoint_time = trades[0][0]+window_ms/2.0
        older = [value for timestamp, value in trades if timestamp < midpoint_time]
        newer = [value for timestamp, value in trades if timestamp >= midpoint_time]
        older_scale = sum(abs(value) for value in older)
        newer_scale = sum(abs(value) for value in newer)
        older_flow = sum(older)/older_scale if older_scale > 0 else 0.0
        newer_flow = sum(newer)/newer_scale if newer_scale > 0 else 0.0
        acceleration = newer_flow-older_flow
    return {
        "symbol": symbol, "observed_ms": observed_ms,
        "bid": bid, "ask": ask, "half_spread_rate": half_spread,
        "bid_depth_usd": bid_depth, "ask_depth_usd": ask_depth,
        "depth_imbalance": depth_imbalance, "trade_count": len(trades),
        "buy_notional_usd": buy, "sell_notional_usd": sell,
        "trade_flow_imbalance": flow,
        "aggression_acceleration": acceleration,
        "trade_window_ms": window_ms,
        "trade_subwindows": _trade_subwindows(trades, observed_ms),
    }


def main():
    conn = _connect(); inserted = 0; failures = []
    plan = _symbol_plan()
    try:
        for symbol in plan["sampled_symbols"]:
            try:
                row = _sample_symbol(symbol)
                minute = int(row["observed_ms"]//60000)
                key = hashlib.sha256((symbol+"|"+str(minute)).encode("utf-8")).hexdigest()
                cursor = conn.execute(
                    "INSERT OR REPLACE INTO microstructure_samples VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, row["symbol"], row["observed_ms"], row["bid"], row["ask"],
                     row["half_spread_rate"], row["bid_depth_usd"],
                     row["ask_depth_usd"], row["depth_imbalance"], row["trade_count"],
                     row["buy_notional_usd"], row["sell_notional_usd"],
                     row["trade_flow_imbalance"], row["aggression_acceleration"],
                     row["trade_window_ms"], time.strftime("%Y-%m-%d %H:%M:%S"),
                     json.dumps(row["trade_subwindows"], sort_keys=True)))
                inserted += max(0, int(cursor.rowcount or 0))
            except Exception as exc:
                failures.append({"symbol": symbol, "error": str(exc)[:300]})
        cutoff = int((time.time()-RETENTION_DAYS*86400)*1000)
        conn.execute("DELETE FROM microstructure_samples WHERE observed_ms<?", (cutoff,))
        conn.commit()
        counts = {row[0]: row[1] for row in conn.execute(
            "SELECT symbol,COUNT(*) FROM microstructure_samples GROUP BY symbol")}
    finally:
        conn.close()
    status = {
        "ok": not failures, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "inserted": inserted, "failures": failures, "sample_counts": counts,
        "sampling_plan": plan,
        "resolution": "one bounded snapshot per minute plus 5s/15s/60s public-trade subwindows from the latest 100 trades",
        "retention_days": RETENTION_DAYS,
        "data_boundary": "forward-only sampled trade subwindows; not continuous 5s replay, 200ms OFI, queue position, cancel flow, or historical L2 replay",
        "research_only": True,
    }
    _atomic(STATUS_PATH, status)
    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    if failures and inserted == 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
