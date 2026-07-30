# -*- coding: utf-8 -*-
"""BTC 15m MTF momentum breakout — OKX swap strategy (logic A).

Hard rules:
  - leverage 20x (notional sizing helper)
  - protective SL 0.9% from entry
  - max 5% equity margin per trade
  - ATR dynamic sizing + EMA144 (1h) trend filter
  - target 2–8 trades/day (cooldown enforced)
  - costs: slippage 5bp + OKX taker 5bp (each side)

No look-ahead: signals use only bars with ts <= current bar.
Live path uses okx REST helpers below (ccxt optional).
"""
from __future__ import print_function

import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import datetime


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def load_params(path=None):
    here = os.path.dirname(os.path.abspath(__file__))
    path = path or os.path.join(here, "params.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------- indicators (causal only) ----------

def ema(series, period):
    out = [None] * len(series)
    if period <= 1 or len(series) == 0:
        return out
    k = 2.0 / (period + 1.0)
    s = None
    for i, v in enumerate(series):
        if v is None:
            continue
        if s is None:
            # seed when enough points
            window = [x for x in series[max(0, i - period + 1): i + 1] if x is not None]
            if len(window) < period:
                continue
            s = sum(window) / float(period)
            out[i] = s
            continue
        s = v * k + s * (1.0 - k)
        out[i] = s
    return out


def atr_pct(candles, period=14):
    """ATR as fraction of close (Wilder-ish SMA of TR)."""
    n = len(candles)
    tr = [None] * n
    for i in range(n):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        c = float(candles[i]["close"])
        if i == 0:
            tr[i] = (h - l) / c if c else None
        else:
            pc = float(candles[i - 1]["close"])
            tr[i] = max(h - l, abs(h - pc), abs(l - pc)) / c if c else None
    out = [None] * n
    for i in range(n):
        if i < period - 1:
            continue
        window = tr[i - period + 1: i + 1]
        if any(x is None for x in window):
            continue
        out[i] = sum(window) / float(period)
    return out


def highest(series, lookback, i):
    """Max of series[i-lookback:i] — excludes current bar (no look-ahead)."""
    if i < lookback:
        return None
    window = series[i - lookback: i]
    if any(x is None for x in window):
        return None
    return max(window)


def lowest(series, lookback, i):
    if i < lookback:
        return None
    window = series[i - lookback: i]
    if any(x is None for x in window):
        return None
    return min(window)


# ---------- OKX REST (optional live) ----------

class OkxPublic:
    BASE = "https://www.okx.com"

    def __init__(self, timeout=20):
        self.timeout = timeout

    def _get(self, path, params=None):
        q = ""
        if params:
            q = "?" + "&".join("%s=%s" % (k, params[k]) for k in params)
        url = self.BASE + path + q
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "qiyu-btc15m-mtf/1.0",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def candles(self, inst_id, bar="15m", limit=100):
        data = self._get(
            "/api/v5/market/candles",
            {"instId": inst_id, "bar": bar, "limit": str(limit)},
        )
        rows = []
        for r in reversed(data.get("data") or []):
            # OKX: ts,o,h,l,c,vol,volCcy,...
            rows.append({
                "ts": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
            })
        return rows


class OkxPrivate:
    """Minimal signed place/cancel — requires env keys; unused in backtest."""

    def __init__(self, api_key=None, api_secret=None, passphrase=None):
        self.api_key = api_key or os.environ.get("OKX_API_KEY")
        self.api_secret = api_secret or os.environ.get("OKX_API_SECRET")
        self.passphrase = passphrase or os.environ.get("OKX_API_PASSPHRASE")

    def ready(self):
        return bool(self.api_key and self.api_secret and self.passphrase)

    def place_order(self, inst_id, side, sz, px=None, td_mode="cross", reduce_only=False):
        """Stub for live wiring — returns intent payload (no auto-mount)."""
        return {
            "ok": False,
            "dry_run": True,
            "note": "Live order path intentionally dry-run until human --confirm mount.",
            "intent": {
                "instId": inst_id,
                "side": side,
                "sz": sz,
                "px": px,
                "tdMode": td_mode,
                "reduceOnly": reduce_only,
            },
            "at": _now(),
        }


# ---------- strategy core ----------

class MtfMomentumStrategy(object):
    def __init__(self, params):
        self.p = params
        self.position = None  # dict or None
        self.equity = float(params.get("backtest", {}).get("initial_equity") or 10000.0)
        self.trades = []
        self.day_counts = {}
        self.cooldown_until = -1

    def _day_key(self, ts_ms):
        t = int(ts_ms)
        if t > 1e12:
            t //= 1000
        return datetime.utcfromtimestamp(t).strftime("%Y-%m-%d")

    def _align_htf_ema(self, candles_15m, candles_1h, ema_period):
        """Map each 15m bar to last CLOSED 1h EMA (no look-ahead)."""
        closes_1h = [float(c["close"]) for c in candles_1h]
        ema_1h = ema(closes_1h, ema_period)
        # For slope: ema[i] vs ema[i-1] on 1h
        htf = []
        j = 0
        for c in candles_15m:
            ts = int(c["ts"])
            # advance j while next 1h candle has closed (1h.ts + 1h <= 15m.ts)
            while j + 1 < len(candles_1h):
                ts1 = int(candles_1h[j + 1]["ts"])
                # 1h bar at ts1 is open at ts1; only use bars fully closed before current 15m
                if ts1 + 3600 * 1000 <= ts:
                    j += 1
                else:
                    break
            # use j only if that 1h bar is closed
            ts_j = int(candles_1h[j]["ts"]) if candles_1h else 0
            usable = candles_1h and (ts_j + 3600 * 1000 <= ts) and ema_1h[j] is not None
            if not usable:
                htf.append({"ema": None, "slope_up": None, "price": None})
                continue
            slope_up = None
            if j > 0 and ema_1h[j - 1] is not None:
                slope_up = ema_1h[j] > ema_1h[j - 1]
            htf.append({
                "ema": ema_1h[j],
                "slope_up": slope_up,
                "price": float(candles_1h[j]["close"]),
            })
        return htf

    def trend_ok(self, htf_row, side):
        if not htf_row or htf_row.get("ema") is None:
            return False
        px = htf_row["price"]
        e = htf_row["ema"]
        slope = htf_row.get("slope_up")
        if side == "long":
            if px <= e:
                return False
            return True if slope is None else bool(slope)
        if px >= e:
            return False
        return True if slope is None else (not bool(slope))

    def size_margin(self, equity, atr_p, entry_px):
        """Return margin (quote) to allocate; notional = margin * leverage."""
        ps = self.p["position_sizing"]
        sl = float(self.p["protective_sl_pct"])
        risk_frac = float(ps.get("risk_fraction_of_equity") or 0.004)
        # risk_cash = equity * risk_frac; stop distance ≈ max(sl, atr)
        stop_pct = max(sl, float(atr_p or sl))
        if stop_pct <= 0:
            stop_pct = sl
        # position notional such that stop_pct * notional ≈ risk_cash
        risk_cash = equity * risk_frac
        notional = risk_cash / stop_pct
        leverage = float(self.p.get("leverage") or 20)
        margin = notional / leverage
        max_m = equity * float(ps.get("max_equity_fraction") or 0.05)
        min_m = equity * float(ps.get("min_equity_fraction") or 0.01)
        margin = max(min_m, min(max_m, margin))
        return margin, margin * leverage

    def on_bar(self, i, candles_15m, atrs, htf_rows, highs, lows, closes):
        p = self.p
        c = candles_15m[i]
        if i <= 0 or atrs[i] is None:
            return
        if i < self.cooldown_until:
            return
        day = self._day_key(c["ts"])
        self.day_counts.setdefault(day, 0)
        max_day = int(p["frequency"]["max_trades_per_day"])

        # manage open position
        if self.position is not None:
            pos = self.position
            px = float(c["close"])
            hi = float(c["high"])
            lo = float(c["low"])
            slip = float(p["slippage_pct"])
            fee = float(p["taker_fee_pct"])
            exit_px = None
            reason = None
            if pos["side"] == "long":
                if lo <= pos["sl"]:
                    exit_px = pos["sl"] * (1.0 - slip)
                    reason = "hard_sl"
                elif hi >= pos["tp"]:
                    exit_px = pos["tp"] * (1.0 - slip)
                    reason = "take_profit"
                elif i - pos["entry_i"] >= int(p["exit"]["time_stop_bars"]):
                    exit_px = px * (1.0 - slip)
                    reason = "time_stop"
            else:
                if hi >= pos["sl"]:
                    exit_px = pos["sl"] * (1.0 + slip)
                    reason = "hard_sl"
                elif lo <= pos["tp"]:
                    exit_px = pos["tp"] * (1.0 + slip)
                    reason = "take_profit"
                elif i - pos["entry_i"] >= int(p["exit"]["time_stop_bars"]):
                    exit_px = px * (1.0 + slip)
                    reason = "time_stop"
            if exit_px is not None:
                if pos["side"] == "long":
                    ret = (exit_px / pos["entry_px"] - 1.0) - fee  # entry fee already applied
                else:
                    ret = (pos["entry_px"] / exit_px - 1.0) - fee
                # PnL on notional; equity changes by margin * leverage * ret = notional * ret
                pnl = pos["notional"] * ret
                self.equity += pnl
                self.trades.append({
                    "side": pos["side"],
                    "entry_ts": pos["entry_ts"],
                    "exit_ts": c["ts"],
                    "entry_px": pos["entry_px"],
                    "exit_px": exit_px,
                    "ret": ret,
                    "pnl": pnl,
                    "reason": reason,
                    "equity_after": self.equity,
                })
                self.position = None
                self.cooldown_until = i + int(p["frequency"]["cooldown_bars_after_exit"])
            return

        # entries — only flat
        if self.day_counts[day] >= max_day:
            return
        lookback = int(p["entry"]["breakout_lookback"])
        pb_lb = int(p["entry"].get("pullback_lookback") or 8)
        atr_p = atrs[i]
        if atr_p is None:
            return
        if atr_p < float(p["entry"]["min_atr_pct"]) or atr_p > float(p["entry"]["max_atr_pct"]):
            return
        hh = highest(highs, lookback, i)
        ll = lowest(lows, lookback, i)
        pb_low = lowest(lows, pb_lb, i)
        pb_high = highest(highs, pb_lb, i)
        if hh is None or ll is None or pb_low is None or pb_high is None:
            return
        close = closes[i]
        mom_n = int(p["entry"]["momentum_bars"])
        if i < mom_n:
            return
        mom_up = close > closes[i - mom_n]
        mom_dn = close < closes[i - mom_n]
        htf = htf_rows[i]
        slip = float(p["slippage_pct"])
        fee = float(p["taker_fee_pct"])
        sl_pct = float(p["protective_sl_pct"])
        tp_r = float(p["exit"]["take_profit_r"])

        # A: 1h trend filter + 15m (breakout OR pullback-resume)
        side = None
        long_break = close > hh and mom_up
        short_break = close < ll and mom_dn
        # pullback resume: dipped toward recent low then closes back up while trend long
        long_pb = (
            self.trend_ok(htf, "long")
            and closes[i - 1] <= pb_low * 1.001
            and mom_up
            and close > closes[i - 1]
        )
        short_pb = (
            self.trend_ok(htf, "short")
            and closes[i - 1] >= pb_high * 0.999
            and mom_dn
            and close < closes[i - 1]
        )
        if (long_break or long_pb) and self.trend_ok(htf, "long"):
            side = "long"
        elif (short_break or short_pb) and self.trend_ok(htf, "short"):
            side = "short"
        if side is None:
            return

        margin, notional = self.size_margin(self.equity, atr_p, close)
        if side == "long":
            entry_px = close * (1.0 + slip)
            # fee on entry deducted via reducing effective size in ret accounting at exit (+fee once more)
            sl = entry_px * (1.0 - sl_pct)
            tp = entry_px * (1.0 + sl_pct * tp_r)
        else:
            entry_px = close * (1.0 - slip)
            sl = entry_px * (1.0 + sl_pct)
            tp = entry_px * (1.0 - sl_pct * tp_r)

        # pay entry fee immediately
        self.equity -= notional * fee
        self.position = {
            "side": side,
            "entry_i": i,
            "entry_ts": c["ts"],
            "entry_px": entry_px,
            "sl": sl,
            "tp": tp,
            "margin": margin,
            "notional": notional,
        }
        self.day_counts[day] = self.day_counts.get(day, 0) + 1


def load_candle_file(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    candles = raw.get("candles") if isinstance(raw, dict) else raw
    out = []
    for c in candles:
        out.append({
            "ts": int(c["ts"]),
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
        })
    return out


def run_backtest(params=None, dir_path=None):
    params = params or load_params()
    dir_path = dir_path or os.path.dirname(os.path.abspath(__file__))
    path_15 = os.path.join(dir_path, params["backtest"]["candle_15m"])
    path_1h = os.path.join(dir_path, params["backtest"]["candle_1h"])
    c15 = load_candle_file(path_15)
    c1h = load_candle_file(path_1h)
    strat = MtfMomentumStrategy(params)
    atrs = atr_pct(c15, int(params["position_sizing"]["atr_period"]))
    htf = strat._align_htf_ema(c15, c1h, int(params["trend_filter"]["ema_period"]))
    highs = [float(x["high"]) for x in c15]
    lows = [float(x["low"]) for x in c15]
    closes = [float(x["close"]) for x in c15]
    equity_curve = [strat.equity]
    for i in range(len(c15)):
        strat.on_bar(i, c15, atrs, htf, highs, lows, closes)
        equity_curve.append(strat.equity)
    return summarize(strat, equity_curve, c15, params)


def summarize(strat, equity_curve, candles, params):
    trades = strat.trades
    rets = [t["ret"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    init = float(params["backtest"]["initial_equity"])
    total_ret = strat.equity / init - 1.0
    # max DD
    peak = equity_curve[0]
    mdd = 0.0
    for x in equity_curve:
        if x > peak:
            peak = x
        dd = x / peak - 1.0
        if dd < mdd:
            mdd = dd
    # sharpe on trade returns (period) then crude ann if enough days
    sharpe = None
    if len(rets) >= 5:
        m = sum(rets) / float(len(rets))
        var = sum((r - m) ** 2 for r in rets) / float(max(len(rets) - 1, 1))
        sd = var ** 0.5
        if sd > 1e-12:
            # trades/day estimate
            if candles:
                t0 = int(candles[0]["ts"]); t1 = int(candles[-1]["ts"])
                if t0 > 1e12:
                    t0 //= 1000; t1 //= 1000
                days = max(1.0, (t1 - t0) / 86400.0)
            else:
                days = 1.0
            trades_per_day = len(rets) / days
            # ann factor ~ sqrt(trades_per_year)
            sharpe = (m / sd) * math.sqrt(max(trades_per_day, 0.1) * 365.0)
    payoff = None
    if wins and losses:
        payoff = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
    # frequency
    day_counts = strat.day_counts
    avg_per_day = (sum(day_counts.values()) / float(len(day_counts))) if day_counts else 0.0
    return {
        "ok": True,
        "strategy_id": params.get("strategy_id"),
        "symbol": params.get("symbol"),
        "n_bars": len(candles),
        "n_trades": len(trades),
        "total_return": total_ret,
        "final_equity": strat.equity,
        "sharpe_ann_proxy": sharpe,
        "max_drawdown": mdd,
        "win_rate": (len(wins) / float(len(rets))) if rets else None,
        "payoff": payoff,
        "avg_trades_per_day": avg_per_day,
        "day_trade_counts": day_counts,
        "targets": params.get("targets"),
        "costs": {
            "slippage_pct": params.get("slippage_pct"),
            "taker_fee_pct": params.get("taker_fee_pct"),
        },
        "hard_sl_pct": params.get("protective_sl_pct"),
        "leverage": params.get("leverage"),
        "trades_tail": trades[-12:],
        "data_window": {
            "first_ts": candles[0]["ts"] if candles else None,
            "last_ts": candles[-1]["ts"] if candles else None,
            "note_zh": "受本地 formal_* 缓存长度限制；非完整 2024YTD。",
        },
    }


def describe():
    p = load_params()
    return {
        "title": p.get("title_zh"),
        "logic": p.get("logic"),
        "symbol": p.get("symbol"),
        "leverage": p.get("leverage"),
        "sl": p.get("protective_sl_pct"),
    }


if __name__ == "__main__":
    rep = run_backtest()
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
