# -*- coding: utf-8 -*-
"""Forward execution-friction telemetry for research backtests.

Read-only: samples top-of-book and account fills, stores no credentials, and
never places/amends/cancels an order. Historical L2 cannot be reconstructed;
the resulting estimates improve only as forward observations accumulate.
"""
from __future__ import print_function

import glob
import hashlib
import json
import math
import os
import sqlite3
import time
from pathlib import Path

import auto_trade_okx as okx


ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
DB_PATH = AUTO_DIR / "execution_cost_telemetry.db"
MODEL_PATH = AUTO_DIR / "execution_cost_model.json"
RETENTION_DAYS = 35
FALLBACK_SYMBOLS = (
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "BNB-USDT-SWAP",
    "XRP-USDT-SWAP", "ADA-USDT-SWAP", "DOGE-USDT-SWAP", "LTC-USDT-SWAP",
    "LINK-USDT-SWAP", "AVAX-USDT-SWAP", "SUI-USDT-SWAP", "DOT-USDT-SWAP",
    "ATOM-USDT-SWAP", "NEAR-USDT-SWAP", "APT-USDT-SWAP", "OP-USDT-SWAP",
    "ARB-USDT-SWAP", "UNI-USDT-SWAP", "AAVE-USDT-SWAP", "BCH-USDT-SWAP",
    "PEPE-USDT-SWAP", "WIF-USDT-SWAP", "TRX-USDT-SWAP",
    "XAU-USDT-SWAP", "XAG-USDT-SWAP", "CL-USDT-SWAP", "NG-USDT-SWAP",
)


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _quantile(values, probability):
    values = sorted(float(value) for value in values if value is not None)
    if not values:
        return None
    position = (len(values)-1)*float(probability)
    low = int(math.floor(position)); high = int(math.ceil(position))
    if low == high:
        return values[low]
    return values[low]+(values[high]-values[low])*(position-low)


def _read_json(path, default):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except Exception:
        return default


def _atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(str(path)+".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                                    sort_keys=True), encoding="utf-8")
    temporary.replace(path)
    os.chmod(str(path), 0o600)


def _symbols():
    values = set()
    for path in glob.glob(str(AUTO_DIR / "formal_daemon_config*.json")):
        row = _read_json(path, {})
        symbol = str(row.get("symbol") or "").upper()
        if symbol.endswith("-SWAP"):
            values.add(symbol)
    return sorted(values or FALLBACK_SYMBOLS)


def _connect():
    connection = sqlite3.connect(str(DB_PATH), timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("""CREATE TABLE IF NOT EXISTS book_samples(
        sample_key TEXT PRIMARY KEY, symbol TEXT NOT NULL, observed_ms INTEGER NOT NULL,
        bid REAL NOT NULL, ask REAL NOT NULL, half_spread_rate REAL NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS book_depth_samples(
        sample_key TEXT PRIMARY KEY, symbol TEXT NOT NULL, observed_ms INTEGER NOT NULL,
        bid_depth_usd REAL NOT NULL, ask_depth_usd REAL NOT NULL,
        levels INTEGER NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS fill_samples(
        trade_key TEXT PRIMARY KEY, symbol TEXT NOT NULL, order_id TEXT,
        observed_ms INTEGER NOT NULL, side TEXT, exec_type TEXT,
        fill_price REAL, mark_price REAL, fill_size REAL,
        adverse_mark_rate REAL, fee_amount REAL)""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_books_symbol_time ON book_samples(symbol,observed_ms)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_depth_symbol_time ON book_depth_samples(symbol,observed_ms)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_fills_symbol_time ON fill_samples(symbol,observed_ms)")
    try:
        os.chmod(str(DB_PATH), 0o600)
    except Exception:
        pass
    return connection


def _contracts_to_usd(levels, ct_val, ct_val_ccy, ct_type=None):
    total = 0.0
    for level in levels or []:
        price = _number(level[0] if len(level) > 0 else None)
        contracts = _number(level[1] if len(level) > 1 else None)
        if not price or not contracts or contracts < 0:
            continue
        # Linear swaps normally express ctVal in the base commodity.  A few
        # quote-denominated contracts express it directly in USD/stablecoin.
        if str(ct_type or "").lower() == "inverse":
            total += contracts*ct_val
        elif str(ct_type or "").lower() == "linear":
            total += contracts*ct_val*price
        elif ct_val_ccy in ("USD", "USDT", "USDC"):
            total += contracts*ct_val
        else:
            total += contracts*ct_val*price
    return total


def _sample_books(connection, symbols):
    inserted = 0; depth_inserted = 0
    for symbol in symbols:
        try:
            instrument_response = okx._okx_request(
                "GET", "/api/v5/public/instruments",
                params={"instType": "SWAP", "instId": symbol}, auth=False)
            instrument = (instrument_response.get("data") or [])[0]
            ct_val = _number(instrument.get("ctVal"))
            ct_val_ccy = str(instrument.get("ctValCcy") or "").upper()
            ct_type = str(instrument.get("ctType") or "").lower()
            if not ct_val or ct_val <= 0:
                continue
            response = okx._okx_request(
                "GET", "/api/v5/market/books",
                params={"instId": symbol, "sz": "5"}, auth=False)
            data = (response.get("data") or [])[0]
            bid = _number((data.get("bids") or [[None]])[0][0])
            ask = _number((data.get("asks") or [[None]])[0][0])
            observed_ms = int(data.get("ts") or time.time()*1000)
            if not bid or not ask or ask < bid:
                continue
            midpoint = (bid+ask)/2.0
            half_spread = (ask-bid)/(2.0*midpoint) if midpoint > 0 else None
            if half_spread is None or half_spread < 0:
                continue
            key = hashlib.sha256((symbol+"|"+str(observed_ms)).encode("utf-8")).hexdigest()
            cursor = connection.execute(
                "INSERT OR IGNORE INTO book_samples VALUES(?,?,?,?,?,?)",
                (key, symbol, observed_ms, bid, ask, half_spread))
            inserted += max(0, int(cursor.rowcount or 0))
            bids = data.get("bids") or []; asks = data.get("asks") or []
            bid_depth = _contracts_to_usd(bids, ct_val, ct_val_ccy, ct_type)
            ask_depth = _contracts_to_usd(asks, ct_val, ct_val_ccy, ct_type)
            if bid_depth > 0 and ask_depth > 0:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO book_depth_samples VALUES(?,?,?,?,?,?)",
                    (key, symbol, observed_ms, bid_depth, ask_depth,
                     min(len(bids), len(asks))))
                depth_inserted += max(0, int(cursor.rowcount or 0))
        except Exception:
            # One unavailable contract must not prevent the others calibrating.
            continue
    return inserted, depth_inserted


def _funding_summaries(symbols):
    output = {}
    for symbol in symbols:
        try:
            response = okx._okx_request(
                "GET", "/api/v5/public/funding-rate-history",
                params={"instId": symbol, "limit": "100"}, auth=False)
            rates = [abs(value) for value in (
                _number(row.get("realizedRate") or row.get("fundingRate"))
                for row in response.get("data") or []) if value is not None]
            output[symbol] = {
                "funding_samples": len(rates),
                "absolute_funding_p75_rate": _quantile(rates, .75),
                "absolute_funding_p90_rate": _quantile(rates, .90),
                "absolute_funding_p95_rate": _quantile(rates, .95),
            }
        except Exception:
            output[symbol] = {"funding_samples": 0}
    return output


def _capacity_reference():
    try:
        response = okx._okx_request(
            "GET", "/api/v5/account/balance", params={"ccy": "USDT"},
            auth=True)
        account = (response.get("data") or [])[0]
        equity = _number(account.get("totalEq"))
        if not equity:
            for detail in account.get("details") or []:
                if str(detail.get("ccy") or "").upper() == "USDT":
                    equity = _number(detail.get("eq") or detail.get("cashBal"))
                    break
        if not equity or equity <= 0:
            return {"available": False}
        leverage = 20.0; maximum_grade_allocation = .70
        return {
            "available": True,
            "account_equity_usdt": round(equity, 8),
            "reference_leverage": leverage,
            "maximum_strategy_allocation": maximum_grade_allocation,
            "maximum_modeled_notional_usdt": round(
                equity*leverage*maximum_grade_allocation, 8),
            "note": "research-only capacity reference; never sizes or places orders",
        }
    except Exception:
        return {"available": False}


def _sample_fills(connection, allowed_symbols):
    response = okx._okx_request(
        "GET", "/api/v5/trade/fills-history",
        params={"instType": "SWAP", "limit": "100"}, auth=True)
    inserted = 0
    for fill in response.get("data") or []:
        symbol = str(fill.get("instId") or "").upper()
        if symbol not in allowed_symbols:
            continue
        fill_price = _number(fill.get("fillPx")); mark_price = _number(fill.get("fillMarkPx"))
        side = str(fill.get("side") or "").lower()
        adverse = None
        if fill_price and mark_price and fill_price > 0 and mark_price > 0:
            adverse = ((fill_price/mark_price-1.0) if side == "buy" else
                       (mark_price/fill_price-1.0))
        observed_ms = int(fill.get("fillTime") or fill.get("ts") or time.time()*1000)
        trade_key = str(fill.get("tradeId") or "")
        if not trade_key:
            trade_key = hashlib.sha256(json.dumps(
                [symbol, fill.get("ordId"), observed_ms, fill_price,
                 fill.get("fillSz")], separators=(",", ":")).encode("utf-8")).hexdigest()
        cursor = connection.execute(
            "INSERT OR IGNORE INTO fill_samples VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (trade_key, symbol, str(fill.get("ordId") or ""), observed_ms,
             side, str(fill.get("execType") or ""), fill_price, mark_price,
             _number(fill.get("fillSz")), adverse, _number(fill.get("fee"))))
        inserted += max(0, int(cursor.rowcount or 0))
    return inserted


def _summaries(connection, symbols):
    output = {}
    for symbol in symbols:
        spreads = [row[0] for row in connection.execute(
            "SELECT half_spread_rate FROM book_samples WHERE symbol=? ORDER BY observed_ms",
            (symbol,)).fetchall()]
        depths = connection.execute(
            "SELECT bid_depth_usd,ask_depth_usd FROM book_depth_samples "
            "WHERE symbol=? ORDER BY observed_ms", (symbol,)).fetchall()
        bid_depths = [row[0] for row in depths]
        ask_depths = [row[1] for row in depths]
        fills = connection.execute(
            "SELECT order_id,adverse_mark_rate,exec_type FROM fill_samples "
            "WHERE symbol=? ORDER BY observed_ms", (symbol,)).fetchall()
        adverse = [max(0.0, float(row[1])) for row in fills if row[1] is not None]
        order_counts = {}
        for order_id, _value, _exec_type in fills:
            if order_id:
                order_counts[order_id] = order_counts.get(order_id, 0)+1
        output[symbol] = {
            "book_samples": len(spreads),
            "half_spread_p50_rate": _quantile(spreads, .50),
            "half_spread_p75_rate": _quantile(spreads, .75),
            "half_spread_p90_rate": _quantile(spreads, .90),
            "half_spread_p95_rate": _quantile(spreads, .95),
            "depth_samples": len(depths),
            "top5_bid_depth_p25_usd": _quantile(bid_depths, .25),
            "top5_bid_depth_p50_usd": _quantile(bid_depths, .50),
            "top5_ask_depth_p25_usd": _quantile(ask_depths, .25),
            "top5_ask_depth_p50_usd": _quantile(ask_depths, .50),
            "fill_samples": len(fills),
            "unique_orders": len(order_counts),
            "multi_fill_orders": sum(count > 1 for count in order_counts.values()),
            "taker_fills": sum(str(row[2]).upper() == "T" for row in fills),
            "adverse_mark_p50_rate": _quantile(adverse, .50),
            "adverse_mark_p75_rate": _quantile(adverse, .75),
            "adverse_mark_p90_rate": _quantile(adverse, .90),
            "adverse_mark_p95_rate": _quantile(adverse, .95),
            "observed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    return output


def main():
    symbols = _symbols(); connection = _connect()
    try:
        book_inserted, depth_inserted = _sample_books(connection, symbols)
        fill_inserted = _sample_fills(connection, set(symbols))
        cutoff = int((time.time()-RETENTION_DAYS*86400)*1000)
        connection.execute("DELETE FROM book_samples WHERE observed_ms<?", (cutoff,))
        connection.execute("DELETE FROM book_depth_samples WHERE observed_ms<?", (cutoff,))
        connection.execute("DELETE FROM fill_samples WHERE observed_ms<?", (cutoff,))
        connection.commit()
        observed = _summaries(connection, symbols)
    finally:
        connection.close()
    model = _read_json(MODEL_PATH, {})
    try:
        fee = okx._okx_request("GET", "/api/v5/account/trade-fee",
                               params={"instType": "SWAP"}, auth=True)
        fee_row = (fee.get("data") or [])[0]
        taker = abs(float(fee_row.get("taker")))
        if 0 < taker < .01:
            model.setdefault("calibration", {})["observed_taker_fee_per_side"] = taker
            for scenario in (model.get("scenarios") or {}).values():
                scenario["fee_rate_per_side"] = taker
    except Exception:
        pass
    funding = _funding_summaries(symbols)
    for symbol in symbols:
        observed.setdefault(symbol, {}).update(funding.get(symbol) or {})
    model["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    model["instrument_observed"] = observed
    model["capacity_reference"] = _capacity_reference()
    model.setdefault("calibration", {})["forward_telemetry"] = {
        "retention_days": RETENTION_DAYS,
        "book_samples_inserted": book_inserted,
        "depth_samples_inserted": depth_inserted,
        "fill_samples_inserted": fill_inserted,
        "note": "fill-vs-mark is a conservative execution proxy, not pure slippage; historical L2 remains unavailable",
    }
    _atomic_json(MODEL_PATH, model)
    print(json.dumps({"ok": True, "symbols": symbols,
                      "book_samples_inserted": book_inserted,
                      "depth_samples_inserted": depth_inserted,
                      "fill_samples_inserted": fill_inserted,
                      "instrument_observed": observed},
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
