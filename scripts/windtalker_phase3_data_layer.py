# -*- coding: utf-8 -*-
"""WINDTALKER PHASE 3 — research data layer (fail-closed, no lookahead).

Fetches / aligns OI, funding, basis, taker flow, cross-asset bars for probes.
Never writes into production daemon paths. Missing/stale → NaN columns; DSL fails closed.
"""
from __future__ import print_function

import json
import math
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path("/root/auto_trade/windtalker_phase3")
AUTO = Path("/root/auto_trade")
CACHE = ROOT / "data_cache"
SCHEMA_VERSION = "windtalker_phase3_data_v1"

UA = "windtalker-phase3-research/1.0"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _okx(path, timeout=12):
    url = "https://www.okx.com" + path
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _zscore(xs, win=20):
    out = [float("nan")] * len(xs)
    for i in range(len(xs)):
        if i + 1 < win:
            continue
        window = xs[i + 1 - win:i + 1]
        if any(not math.isfinite(v) for v in window):
            continue
        mu = sum(window) / float(win)
        var = sum((v - mu) ** 2 for v in window) / float(win)
        sd = math.sqrt(var) if var > 0 else 0.0
        out[i] = 0.0 if sd == 0 else (xs[i] - mu) / sd
    return out


def _asof_align(bar_ts, series_ts_val, max_lag_ms):
    """Align (ts,val) series onto bar timestamps using as-of past values only."""
    series = sorted((int(t), float(v)) for t, v in series_ts_val if math.isfinite(float(v)))
    out = []
    j = -1
    n = len(series)
    for bt in bar_ts:
        bt = int(bt)
        while j + 1 < n and series[j + 1][0] <= bt:
            j += 1
        if j < 0:
            out.append(float("nan"))
            continue
        ts, val = series[j]
        if bt - ts > max_lag_ms:
            out.append(float("nan"))  # stale → fail-closed
        else:
            out.append(val)
    return out


def load_candle_cache(symbol, timeframe):
    """Load formal candle cache if present; else fetch OKX market candles."""
    base = symbol.split("-")[0].lower()
    tf = timeframe.lower()
    path = AUTO / ("formal_%s_%s_candles_cache.json" % (base, tf))
    if path.exists():
        d = json.loads(path.read_text())
        rows = d.get("candles") or []
        # ensure ascending
        rows = sorted(rows, key=lambda r: int(r["ts"]))
        return {
            "source": "formal_cache",
            "path": str(path),
            "schema_version": SCHEMA_VERSION,
            "bars": rows,
            "fetched_at": d.get("fetched_at"),
        }
    # live fetch research-only
    bar = timeframe
    inst = symbol
    data = []
    after = None
    for _ in range(6):
        q = "/api/v5/market/candles?instId=%s&bar=%s&limit=300" % (inst, bar)
        if after:
            q += "&after=%s" % after
        d = _okx(q)
        chunk = d.get("data") or []
        if not chunk:
            break
        data.extend(chunk)
        after = chunk[-1][0]
        time.sleep(0.15)
    # OKX returns newest first
    rows = []
    for c in reversed(data):
        rows.append({
            "ts": int(c[0]),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "vol": float(c[5]) if len(c) > 5 else float("nan"),
        })
    # dedupe
    seen = set()
    uniq = []
    for r in rows:
        if r["ts"] in seen:
            continue
        seen.add(r["ts"])
        uniq.append(r)
    uniq.sort(key=lambda r: r["ts"])
    return {
        "source": "okx_market_candles",
        "path": None,
        "schema_version": SCHEMA_VERSION,
        "bars": uniq,
        "fetched_at": _now(),
    }


def fetch_funding_history(inst_id, limit_pages=5):
    rows = []
    for i in range(limit_pages):
        q = "/api/v5/public/funding-rate-history?instId=%s&limit=100" % inst_id
        if rows:
            q += "&before=%s" % rows[-1]["ts"]
        d = _okx(q)
        chunk = d.get("data") or []
        if not chunk:
            break
        for r in chunk:
            rows.append({"ts": int(r["fundingTime"]), "rate": float(r["fundingRate"])})
        time.sleep(0.12)
    rows = sorted({r["ts"]: r for r in rows}.values(), key=lambda x: x["ts"])
    return {"ok": bool(rows), "n": len(rows), "rows": rows, "source": "okx_funding_rate_history"}


def fetch_oi_history(inst_id, period="5m", limit_pages=5):
    rows = []
    after = None
    for _ in range(limit_pages):
        q = "/api/v5/rubik/stat/contracts/open-interest-history?instId=%s&period=%s" % (
            inst_id, period)
        # endpoint returns latest 100; paginate via end
        d = _okx(q)
        chunk = d.get("data") or []
        if not chunk:
            break
        for r in chunk:
            rows.append({"ts": int(r[0]), "oi": float(r[1])})
        # rubik may not paginate well; break after first if duplicates
        break
    # also merge local cache if present
    cache_p = AUTO / "dual_engine" / "frost3_btc5m_microedge_okx_funding_oi.json"
    if cache_p.exists() and "BTC" in inst_id:
        try:
            cd = json.loads(cache_p.read_text())
            for r in cd.get("oi_history") or []:
                rows.append({"ts": int(r["ts"]), "oi": float(r["oi"])})
        except Exception:
            pass
    rows = sorted({r["ts"]: r for r in rows}.values(), key=lambda x: x["ts"])
    return {"ok": bool(rows), "n": len(rows), "rows": rows,
            "source": "okx_rubik_oi_history+cache"}


def fetch_taker_volume(ccy="BTC", period="5m"):
    q = "/api/v5/rubik/stat/taker-volume?ccy=%s&instType=CONTRACTS&period=%s" % (ccy, period)
    d = _okx(q)
    chunk = d.get("data") or []
    rows = []
    for r in chunk:
        # [ts, sellVol, buyVol] per OKX rubik docs (sell, buy)
        rows.append({
            "ts": int(r[0]),
            "taker_sell": float(r[1]),
            "taker_buy": float(r[2]),
        })
    rows = sorted({r["ts"]: r for r in rows}.values(), key=lambda x: x["ts"])
    return {"ok": bool(rows), "n": len(rows), "rows": rows, "source": "okx_rubik_taker_volume"}


def fetch_basis_series(inst_id, index_id, bar="5m", limit=300):
    mark = _okx("/api/v5/market/mark-price-candles?instId=%s&bar=%s&limit=%s" % (
        inst_id, bar, limit)).get("data") or []
    idx = _okx("/api/v5/market/index-candles?instId=%s&bar=%s&limit=%s" % (
        index_id, bar, limit)).get("data") or []
    m = {int(r[0]): float(r[4]) for r in mark}
    i = {int(r[0]): float(r[4]) for r in idx}
    rows = []
    for ts in sorted(set(m) & set(i)):
        if i[ts] == 0:
            continue
        basis_bps = (m[ts] - i[ts]) / i[ts] * 10000.0
        rows.append({"ts": ts, "basis_bps": basis_bps, "mark": m[ts], "index": i[ts]})
    return {"ok": bool(rows), "n": len(rows), "rows": rows, "source": "okx_mark_vs_index"}


def load_microstructure_flow(symbol, limit=50000):
    """Optional enrichment from local microstructure_telemetry.db (research only)."""
    import sqlite3
    db = AUTO / "microstructure_telemetry.db"
    if not db.exists():
        return {"ok": False, "n": 0, "rows": [], "source": "microstructure_absent"}
    con = sqlite3.connect(str(db))
    cur = con.cursor()
    cur.execute(
        "SELECT observed_ms, buy_notional_usd, sell_notional_usd, trade_flow_imbalance "
        "FROM microstructure_samples WHERE symbol=? ORDER BY observed_ms DESC LIMIT ?",
        (symbol, limit),
    )
    rows = []
    for ms, b, s, imb in cur.fetchall():
        rows.append({
            "ts": int(ms),
            "buy_notional_usd": float(b or 0),
            "sell_notional_usd": float(s or 0),
            "flow_imbalance": float(imb if imb is not None else float("nan")),
        })
    con.close()
    rows.sort(key=lambda r: r["ts"])
    return {"ok": bool(rows), "n": len(rows), "rows": rows, "source": "microstructure_telemetry.db"}


def _atr14(highs, lows, closes):
    out = [float("nan")] * len(closes)
    trs = [float("nan")] * len(closes)
    for i in range(len(closes)):
        if i == 0:
            trs[i] = highs[i] - lows[i]
        else:
            trs[i] = max(highs[i] - lows[i],
                         abs(highs[i] - closes[i - 1]),
                         abs(lows[i] - closes[i - 1]))
    for i in range(len(closes)):
        if i + 1 < 14:
            continue
        out[i] = sum(trs[i + 1 - 14:i + 1]) / 14.0 / max(closes[i], 1e-12)
    return out


def _vol_z(vols, win=20):
    return _zscore(vols, win=win)


def _prev_ext(arr, win=20, mode="high"):
    out = [float("nan")] * len(arr)
    for i in range(len(arr)):
        if i < win:
            continue
        window = arr[i - win:i]  # exclusive of current → no lookahead
        out[i] = max(window) if mode == "high" else min(window)
    return out


def build_research_frame(symbol, timeframe, lead_symbol=None, period=None):
    """Build aligned bar frame with research features. Fail-closed NaNs when missing."""
    CACHE.mkdir(parents=True, exist_ok=True)
    period = period or timeframe
    ccy = symbol.split("-")[0]
    index_id = "%s-USDT" % ccy

    candles = load_candle_cache(symbol, timeframe)
    bars = candles["bars"]
    if len(bars) < 80:
        # try fetch
        candles = load_candle_cache(symbol, timeframe)  # already tries fetch if missing
        bars = candles["bars"]

    meta = {
        "schema_version": SCHEMA_VERSION,
        "symbol": symbol,
        "timeframe": timeframe,
        "built_at": _now(),
        "sources": {},
        "no_lookahead": True,
        "fail_closed": True,
        "stale_policy_ms": {
            "funding": 8 * 3600 * 1000 + 600000,
            "oi": 30 * 60 * 1000 if timeframe == "5m" else 2 * 3600 * 1000,
            "taker": 30 * 60 * 1000 if timeframe == "5m" else 2 * 3600 * 1000,
            "basis": 30 * 60 * 1000 if timeframe == "5m" else 2 * 3600 * 1000,
        },
    }

    ts = [int(b["ts"]) for b in bars]
    o = [float(b["open"]) for b in bars]
    h = [float(b["high"]) for b in bars]
    l = [float(b["low"]) for b in bars]
    c = [float(b["close"]) for b in bars]
    v = [float(b.get("vol") or float("nan")) for b in bars]

    frame = {
        "ts": ts, "open": o, "high": h, "low": l, "close": c,
        "atr14": _atr14(h, l, c),
        "vol_z20": _vol_z([0.0 if not math.isfinite(x) else x for x in v]),
        "prev_high20": _prev_ext(h, 20, "high"),
        "prev_low20": _prev_ext(l, 20, "low"),
    }

    # Funding
    try:
        fund = fetch_funding_history(symbol)
        meta["sources"]["funding"] = {k: fund[k] for k in ("ok", "n", "source")}
        rates = _asof_align(ts, [(r["ts"], r["rate"]) for r in fund["rows"]],
                           meta["stale_policy_ms"]["funding"])
        frame["funding_rate"] = rates
        frame["funding_z20"] = _zscore([0.0 if not math.isfinite(x) else x for x in rates])
    except Exception as e:
        meta["sources"]["funding"] = {"ok": False, "error": str(e)}
        frame["funding_rate"] = [float("nan")] * len(ts)
        frame["funding_z20"] = [float("nan")] * len(ts)

    # OI
    try:
        oi = fetch_oi_history(symbol, period=period if period in ("5m", "1H", "1D") else "5m")
        meta["sources"]["oi"] = {k: oi[k] for k in ("ok", "n", "source")}
        oi_vals = _asof_align(ts, [(r["ts"], r["oi"]) for r in oi["rows"]],
                              meta["stale_policy_ms"]["oi"])
        frame["oi"] = oi_vals
        frame["oi_z20"] = _zscore([0.0 if not math.isfinite(x) else x for x in oi_vals])
        deltas = [float("nan")] * len(oi_vals)
        for i in range(1, len(oi_vals)):
            if math.isfinite(oi_vals[i]) and math.isfinite(oi_vals[i - 1]) and oi_vals[i - 1] != 0:
                deltas[i] = (oi_vals[i] - oi_vals[i - 1]) / abs(oi_vals[i - 1])
        frame["oi_delta_pct"] = deltas
        # crowding: high oi_z with price stretch
        crowd = [float("nan")] * len(ts)
        for i in range(len(ts)):
            if math.isfinite(frame["oi_z20"][i]) and math.isfinite(frame["atr14"][i]):
                crowd[i] = frame["oi_z20"][i] * (1.0 if c[i] >= o[i] else -1.0)
        frame["oi_crowding"] = crowd
    except Exception as e:
        meta["sources"]["oi"] = {"ok": False, "error": str(e)}
        for k in ("oi", "oi_z20", "oi_delta_pct", "oi_crowding"):
            frame[k] = [float("nan")] * len(ts)

    # Basis
    try:
        basis = fetch_basis_series(symbol, index_id, bar=timeframe, limit=300)
        meta["sources"]["basis"] = {k: basis[k] for k in ("ok", "n", "source")}
        bps = _asof_align(ts, [(r["ts"], r["basis_bps"]) for r in basis["rows"]],
                          meta["stale_policy_ms"]["basis"])
        frame["basis_bps"] = bps
        frame["basis_z20"] = _zscore([0.0 if not math.isfinite(x) else x for x in bps])
    except Exception as e:
        meta["sources"]["basis"] = {"ok": False, "error": str(e)}
        frame["basis_bps"] = [float("nan")] * len(ts)
        frame["basis_z20"] = [float("nan")] * len(ts)

    # Taker
    try:
        taker = fetch_taker_volume(ccy=ccy, period="5m" if timeframe == "5m" else "5m")
        meta["sources"]["taker"] = {k: taker[k] for k in ("ok", "n", "source")}
        buy = _asof_align(ts, [(r["ts"], r["taker_buy"]) for r in taker["rows"]],
                          meta["stale_policy_ms"]["taker"])
        sell = _asof_align(ts, [(r["ts"], r["taker_sell"]) for r in taker["rows"]],
                           meta["stale_policy_ms"]["taker"])
        imb = []
        for b, s in zip(buy, sell):
            if not (math.isfinite(b) and math.isfinite(s)) or (b + s) == 0:
                imb.append(float("nan"))
            else:
                imb.append((b - s) / (b + s))
        frame["taker_buy"] = buy
        frame["taker_sell"] = sell
        frame["taker_imbalance"] = imb
        frame["taker_imbalance_z20"] = _zscore(
            [0.0 if not math.isfinite(x) else x for x in imb])
    except Exception as e:
        meta["sources"]["taker"] = {"ok": False, "error": str(e)}
        for k in ("taker_buy", "taker_sell", "taker_imbalance", "taker_imbalance_z20"):
            frame[k] = [float("nan")] * len(ts)

    # Microstructure flow enrichment (optional)
    try:
        micro = load_microstructure_flow(symbol)
        meta["sources"]["microstructure_flow"] = {k: micro[k] for k in ("ok", "n", "source")}
        if micro["ok"]:
            flow = _asof_align(
                ts, [(r["ts"], r["flow_imbalance"]) for r in micro["rows"]
                     if math.isfinite(r["flow_imbalance"])],
                15 * 60 * 1000)
            frame["flow_imbalance"] = flow
        else:
            frame["flow_imbalance"] = [float("nan")] * len(ts)
    except Exception as e:
        meta["sources"]["microstructure_flow"] = {"ok": False, "error": str(e)}
        frame["flow_imbalance"] = [float("nan")] * len(ts)

    # Cross-asset lead-lag
    lead_symbol = lead_symbol or ("ETH-USDT-SWAP" if symbol.startswith("BTC") else "BTC-USDT-SWAP")
    try:
        lead = load_candle_cache(lead_symbol, timeframe)
        lead_bars = {int(b["ts"]): float(b["close"]) for b in lead["bars"]}
        lead_close = [lead_bars.get(t, float("nan")) for t in ts]
        lead_ret1 = [float("nan")] * len(ts)
        lead_ret3 = [float("nan")] * len(ts)
        lag_ret1 = [float("nan")] * len(ts)
        for i in range(1, len(ts)):
            if math.isfinite(c[i]) and math.isfinite(c[i - 1]) and c[i - 1] != 0:
                lag_ret1[i] = (c[i] - c[i - 1]) / c[i - 1]
            if math.isfinite(lead_close[i]) and math.isfinite(lead_close[i - 1]) and lead_close[i - 1] != 0:
                lead_ret1[i] = (lead_close[i] - lead_close[i - 1]) / lead_close[i - 1]
            if i >= 3 and math.isfinite(lead_close[i]) and math.isfinite(lead_close[i - 3]) and lead_close[i - 3] != 0:
                lead_ret3[i] = (lead_close[i] - lead_close[i - 3]) / lead_close[i - 3]
        # rolling corr of lead_ret1 vs lag_ret1 (past window only)
        corr = [float("nan")] * len(ts)
        sync = [float("nan")] * len(ts)
        win = 20
        for i in range(win, len(ts)):
            xs = lead_ret1[i - win + 1:i + 1]
            ys = lag_ret1[i - win + 1:i + 1]
            pairs = [(x, y) for x, y in zip(xs, ys) if math.isfinite(x) and math.isfinite(y)]
            if len(pairs) < win // 2:
                continue
            mx = sum(p[0] for p in pairs) / len(pairs)
            my = sum(p[1] for p in pairs) / len(pairs)
            num = sum((p[0] - mx) * (p[1] - my) for p in pairs)
            dx = math.sqrt(sum((p[0] - mx) ** 2 for p in pairs))
            dy = math.sqrt(sum((p[1] - my) ** 2 for p in pairs))
            if dx > 0 and dy > 0:
                corr[i] = num / (dx * dy)
            # sync score: lead move then same-sign lag delayed — causal lag use lead at i-1
            if i >= 2 and math.isfinite(lead_ret1[i - 1]) and math.isfinite(lag_ret1[i]):
                sync[i] = lead_ret1[i - 1] * lag_ret1[i]
        frame["lead_ret1"] = lead_ret1
        frame["lead_ret3"] = lead_ret3
        frame["lag_ret1"] = lag_ret1
        frame["lead_lag_corr20"] = corr
        frame["cross_sync_score"] = sync
        meta["sources"]["cross_asset"] = {
            "ok": True, "lead_symbol": lead_symbol,
            "lead_bars": len(lead["bars"]), "source": lead.get("source"),
        }
    except Exception as e:
        meta["sources"]["cross_asset"] = {"ok": False, "error": str(e)}
        for k in ("lead_ret1", "lead_ret3", "lag_ret1", "lead_lag_corr20", "cross_sync_score"):
            frame[k] = [float("nan")] * len(ts)

    # Event/state placeholders (probes may overwrite)
    frame["event_code"] = [0.0] * len(ts)
    frame["state_id"] = [0.0] * len(ts)
    frame["seq_age"] = [0.0] * len(ts)
    frame["regime_ok"] = [1.0] * len(ts)

    meta["n_bars"] = len(ts)
    meta["candle_source"] = candles.get("source")
    # coverage
    def cov(col):
        xs = frame[col]
        return sum(1 for x in xs if math.isfinite(x)) / float(len(xs) or 1)

    meta["coverage"] = {k: round(cov(k), 4) for k in (
        "funding_rate", "oi", "basis_bps", "taker_imbalance", "cross_sync_score",
        "flow_imbalance")}
    # persist
    out_path = CACHE / ("%s_%s_research_frame.json" % (
        symbol.replace("-", "_"), timeframe))
    payload = {"meta": meta, "frame": frame}
    out_path.write_text(json.dumps(payload, ensure_ascii=False))
    meta["cache_path"] = str(out_path)
    return meta, frame


def capability_inventory():
    """Build data capability matrix connecting research classes."""
    classes = {
        "OI": {
            "endpoints": [
                "/api/v5/rubik/stat/contracts/open-interest-history",
                "/api/v5/public/open-interest",
            ],
            "local_cache": ["dual_engine/frost3_btc5m_microedge_okx_funding_oi.json"],
            "dsl_features": ["oi", "oi_z20", "oi_delta_pct", "oi_crowding"],
            "in_production_dsl": False,
            "fabricated": False,
        },
        "Funding_Basis": {
            "endpoints": [
                "/api/v5/public/funding-rate-history",
                "/api/v5/market/mark-price-candles",
                "/api/v5/market/index-candles",
            ],
            "dsl_features": ["funding_rate", "funding_z20", "basis_bps", "basis_z20"],
            "in_production_dsl": False,
            "funding_in_production_friction_only": True,
            "fabricated": False,
        },
        "Taker_flow": {
            "endpoints": ["/api/v5/rubik/stat/taker-volume"],
            "local": ["microstructure_telemetry.db buy/sell notional + imbalance"],
            "dsl_features": [
                "taker_buy", "taker_sell", "taker_imbalance",
                "taker_imbalance_z20", "flow_imbalance",
            ],
            "in_production_dsl": False,
            "fabricated": False,
        },
        "Cross_asset_sync": {
            "endpoints": ["/api/v5/market/candles multi-symbol"],
            "local_caches": "formal_*_candles_cache.json",
            "dsl_features": [
                "lead_ret1", "lead_ret3", "lag_ret1",
                "lead_lag_corr20", "cross_sync_score",
            ],
            "in_production_dsl": False,
            "fabricated": False,
        },
    }
    # probe connectivity live
    connected = []
    evidence = {}
    try:
        f = fetch_funding_history("BTC-USDT-SWAP", limit_pages=1)
        evidence["Funding_Basis"] = {"ok": f["ok"], "n": f["n"]}
        if f["ok"]:
            connected.append("Funding_Basis")
    except Exception as e:
        evidence["Funding_Basis"] = {"ok": False, "error": str(e)}
    try:
        oi = fetch_oi_history("BTC-USDT-SWAP")
        evidence["OI"] = {"ok": oi["ok"], "n": oi["n"]}
        if oi["ok"]:
            connected.append("OI")
    except Exception as e:
        evidence["OI"] = {"ok": False, "error": str(e)}
    try:
        tk = fetch_taker_volume("BTC")
        evidence["Taker_flow"] = {"ok": tk["ok"], "n": tk["n"]}
        if tk["ok"]:
            connected.append("Taker_flow")
    except Exception as e:
        evidence["Taker_flow"] = {"ok": False, "error": str(e)}
    try:
        c1 = load_candle_cache("BTC-USDT-SWAP", "5m")
        c2 = load_candle_cache("ETH-USDT-SWAP", "5m")
        ok = len(c1["bars"]) > 50 and len(c2["bars"]) > 50
        evidence["Cross_asset_sync"] = {
            "ok": ok, "btc_bars": len(c1["bars"]), "eth_bars": len(c2["bars"]),
        }
        if ok:
            connected.append("Cross_asset_sync")
    except Exception as e:
        evidence["Cross_asset_sync"] = {"ok": False, "error": str(e)}

    # liquidation / L2
    unavailable = {
        "liquidation_feed": {
            "available": False,
            "policy": "do_not_fabricate",
            "note": "No continuous liquidation replay; cascade ideas must declare proxy or eliminate",
        },
        "historical_L2_orderbook": {
            "available": False,
            "policy": "do_not_fabricate",
            "note": "microstructure is forward-sampled windows, not L2 replay",
        },
    }

    return {
        "schema": "WINDTALKER_PHASE3_DATA_CAPABILITY_MATRIX",
        "generated_at": _now(),
        "schema_versioning": SCHEMA_VERSION,
        "replay_policy": "asof_align_past_only_no_lookahead",
        "missing_stale_policy": "NaN → research DSL fail-closed (no signal)",
        "classes": classes,
        "live_connectivity_evidence": evidence,
        "connected_classes": connected,
        "connected_count": len(connected),
        "meets_ge3_of_4": len(connected) >= 3,
        "unavailable_do_not_fabricate": unavailable,
        "production_isolation": {
            "writes_to_production_dsl_features": False,
            "writes_to_formal_daemon": False,
            "new_data_fail_closed_for_probes": True,
        },
    }
