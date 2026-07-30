# -*- coding: utf-8 -*-
"""ADA 5m — Momentum × Volatility dual filter (Direction 1).

Signal = (BB bandwidth compressed then expanding) AND (break N-bar high/low)
         AND (EMA144 trend align) AND (bandwidth NOT in high-vol abandon zone).

Hard rules: 20x, SL 0.9%, margin ≤5% equity, ATR sizing, 2–8/day throttle,
slip 5bp + taker 5bp. No look-ahead. Live orders dry-run until human confirm.
"""
from __future__ import print_function

import json
import math
import os
import urllib.request
from datetime import datetime


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def load_params(path=None):
    here = os.path.dirname(os.path.abspath(__file__))
    path = path or os.path.join(here, "params.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ema(series, period):
    out = [None] * len(series)
    if period <= 1:
        return out
    k = 2.0 / (period + 1.0)
    s = None
    for i, v in enumerate(series):
        if v is None:
            continue
        if s is None:
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


def bb_width(closes, period=20, k=2.0):
    """Relative Bollinger bandwidth (causal)."""
    out = [None] * len(closes)
    for i in range(len(closes)):
        if i < period - 1:
            continue
        window = closes[i - period + 1: i + 1]
        m = sum(window) / float(period)
        var = sum((x - m) ** 2 for x in window) / float(period)
        sd = var ** 0.5
        if m <= 1e-12:
            continue
        out[i] = (2.0 * k * sd) / m
    return out


class OkxPublic(object):
    BASE = "https://www.okx.com"

    def __init__(self, timeout=20):
        self.timeout = timeout

    def candles(self, inst_id, bar="5m", limit=100):
        q = "instId=%s&bar=%s&limit=%s" % (inst_id, bar, limit)
        req = urllib.request.Request(
            self.BASE + "/api/v5/market/candles?" + q,
            headers={"User-Agent": "qiyu-ada5m-momvol/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rows = []
        for r in reversed(data.get("data") or []):
            rows.append({
                "ts": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
            })
        return rows


class OkxPrivate(object):
    def place_order(self, **kwargs):
        return {
            "ok": False,
            "dry_run": True,
            "note": "Live path dry-run until human --confirm mount.",
            "intent": kwargs,
            "at": _now(),
        }


class MomVolFilterStrategy(object):
    def __init__(self, params):
        self.p = params
        self.position = None
        self.equity = float(params.get("backtest", {}).get("initial_equity") or 10000.0)
        self.trades = []
        self.day_counts = {}
        self.cooldown_until = -1

    def _day_key(self, ts_ms):
        t = int(ts_ms)
        if t > 1e12:
            t //= 1000
        return datetime.utcfromtimestamp(t).strftime("%Y-%m-%d")

    def size_notional(self, atr_p):
        ps = self.p["position_sizing"]
        sl = float(self.p["protective_sl_pct"])
        risk_cash = self.equity * float(ps.get("risk_fraction_of_equity") or 0.0035)
        stop_pct = max(sl, float(atr_p or sl))
        notional = risk_cash / stop_pct
        lev = float(self.p.get("leverage") or 20)
        margin = notional / lev
        max_m = self.equity * float(ps.get("max_equity_fraction") or 0.05)
        min_m = self.equity * float(ps.get("min_equity_fraction") or 0.01)
        margin = max(min_m, min(max_m, margin))
        return margin, margin * lev

    def vol_state(self, bw, i):
        vf = self.p["vol_filter"]
        lb = int(vf.get("lookback_for_quantile") or 80)
        if i < lb or bw[i] is None or bw[i - 1] is None or bw[i - 2] is None:
            return None
        hist = [v for v in bw[i - lb: i] if v is not None]
        if len(hist) < lb // 2:
            return None
        hs = sorted(hist)
        q_c = float(vf.get("compress_quantile") or 0.25)
        q_h = float(vf.get("abandon_high_vol_quantile") or 0.80)
        thr = hs[int(len(hs) * q_c)]
        hi = hs[min(len(hs) - 1, int(len(hs) * q_h))]
        was_compressed = min(bw[i - 1], bw[i - 2]) <= thr
        expanding = bw[i] >= bw[i - 1] * float(vf.get("expand_mult") or 1.05)
        high_vol = bw[i] >= hi
        return {
            "ok_entry": bool(was_compressed and expanding and not high_vol),
            "was_compressed": was_compressed,
            "expanding": expanding,
            "high_vol": high_vol,
            "bw": bw[i],
            "thr": thr,
            "hi": hi,
        }

    def on_bar(self, i, candles, atrs, bws, emas, highs, lows, closes):
        p = self.p
        c = candles[i]
        if atrs[i] is None:
            return
        if i < self.cooldown_until:
            return
        day = self._day_key(c["ts"])
        self.day_counts.setdefault(day, 0)
        max_day = int(p["frequency"]["max_trades_per_day"])
        slip = float(p["slippage_pct"])
        fee = float(p["taker_fee_pct"])
        sl_pct = float(p["protective_sl_pct"])
        tp_r = float(p["exit"]["take_profit_r"])

        if self.position is not None:
            pos = self.position
            px = float(c["close"])
            hi = float(c["high"])
            lo = float(c["low"])
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
                    ret = (exit_px / pos["entry_px"] - 1.0) - fee
                else:
                    ret = (pos["entry_px"] / exit_px - 1.0) - fee
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
                    "vol_meta": pos.get("vol_meta"),
                })
                self.position = None
                self.cooldown_until = i + int(p["frequency"]["cooldown_bars_after_exit"])
            return

        if self.day_counts[day] >= max_day:
            return
        vs = self.vol_state(bws, i)
        if not vs or not vs["ok_entry"]:
            return
        lookback = int(p["entry"]["breakout_lookback"])
        if i < lookback:
            return
        hh = max(highs[i - lookback: i])
        ll = min(lows[i - lookback: i])
        close = closes[i]
        e = emas[i]
        require_ema = bool(p["entry"].get("require_ema_align", True))
        if require_ema and e is None:
            return
        side = None
        if close > hh and ((not require_ema) or close > e):
            side = "long"
        elif close < ll and ((not require_ema) or close < e):
            side = "short"
        if side is None:
            return

        margin, notional = self.size_notional(atrs[i])
        if side == "long":
            entry_px = close * (1.0 + slip)
            sl = entry_px * (1.0 - sl_pct)
            tp = entry_px * (1.0 + sl_pct * tp_r)
        else:
            entry_px = close * (1.0 - slip)
            sl = entry_px * (1.0 + sl_pct)
            tp = entry_px * (1.0 - sl_pct * tp_r)
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
            "vol_meta": {k: vs[k] for k in ("bw", "thr", "hi", "was_compressed", "expanding")},
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
    path = os.path.join(dir_path, params["backtest"]["candle_5m"])
    candles = load_candle_file(path)
    closes = [float(x["close"]) for x in candles]
    highs = [float(x["high"]) for x in candles]
    lows = [float(x["low"]) for x in candles]
    vf = params["vol_filter"]
    atrs = atr_pct(candles, int(params["position_sizing"]["atr_period"]))
    bws = bb_width(closes, int(vf.get("bb_period") or 20), float(vf.get("bb_k") or 2.0))
    emas = ema(closes, int(params["trend_filter"]["ema_period"]))
    strat = MomVolFilterStrategy(params)
    equity_curve = [strat.equity]
    for i in range(len(candles)):
        strat.on_bar(i, candles, atrs, bws, emas, highs, lows, closes)
        equity_curve.append(strat.equity)
    return summarize(strat, equity_curve, candles, params)


def summarize(strat, equity_curve, candles, params):
    trades = strat.trades
    rets = [t["ret"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    init = float(params["backtest"]["initial_equity"])
    total_ret = strat.equity / init - 1.0
    peak = equity_curve[0]
    mdd = 0.0
    for x in equity_curve:
        if x > peak:
            peak = x
        dd = x / peak - 1.0
        if dd < mdd:
            mdd = dd
    sharpe = None
    if len(rets) >= 5:
        m = sum(rets) / float(len(rets))
        var = sum((r - m) ** 2 for r in rets) / float(max(len(rets) - 1, 1))
        sd = var ** 0.5
        if sd > 1e-12 and candles:
            t0 = int(candles[0]["ts"])
            t1 = int(candles[-1]["ts"])
            if t0 > 1e12:
                t0 //= 1000
                t1 //= 1000
            days = max(1.0, (t1 - t0) / 86400.0)
            trades_per_day = len(rets) / days
            sharpe = (m / sd) * math.sqrt(max(trades_per_day, 0.1) * 365.0)
    payoff = None
    if wins and losses:
        payoff = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
    avg_per_day = (
        sum(strat.day_counts.values()) / float(len(strat.day_counts))
        if strat.day_counts else 0.0
    )
    return {
        "ok": True,
        "strategy_id": params.get("strategy_id"),
        "symbol": params.get("symbol"),
        "logic": params.get("logic"),
        "n_bars": len(candles),
        "n_trades": len(trades),
        "total_return": total_ret,
        "final_equity": strat.equity,
        "sharpe_ann_proxy": sharpe,
        "max_drawdown": mdd,
        "win_rate": (len(wins) / float(len(rets))) if rets else None,
        "payoff": payoff,
        "avg_trades_per_day": avg_per_day,
        "day_trade_counts": strat.day_counts,
        "targets": params.get("targets"),
        "rejected_logic": params.get("rejected_logic"),
        "costs": {
            "slippage_pct": params.get("slippage_pct"),
            "taker_fee_pct": params.get("taker_fee_pct"),
        },
        "trades_tail": trades[-15:],
        "data_window": {
            "first_ts": candles[0]["ts"] if candles else None,
            "last_ts": candles[-1]["ts"] if candles else None,
            "note_zh": "受 formal_* 缓存长度限制；非完整 2024YTD。",
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
    print(json.dumps(run_backtest(), ensure_ascii=False, indent=2, default=str))
