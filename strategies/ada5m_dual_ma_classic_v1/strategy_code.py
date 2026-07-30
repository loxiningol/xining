# -*- coding: utf-8 -*-
"""ADA 5m — Classic dual-MA trend variant (WR>=50% prelim gate).

Entry: EMA(8)/EMA(34) cross + ATR expansion filter.
Exit: hard SL 0.9%, TP 2R, time-stop 12 bars.
Costs: slip 5bp + taker 5bp. Leverage 20x, margin <=5%.
Live orders dry-run until human --confirm.
"""
from __future__ import print_function

import json
import math
import os
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


def label_window(candles):
    if not candles:
        return {}
    first_ts = int(candles[0]["ts"])
    last_ts = int(candles[-1]["ts"])
    a = first_ts // 1000 if first_ts > 1e12 else first_ts
    b = last_ts // 1000 if last_ts > 1e12 else last_ts
    span = max(0.0, (b - a) / 86400.0)
    first_iso = datetime.utcfromtimestamp(a).strftime("%Y-%m-%d %H:%M:%S UTC")
    last_iso = datetime.utcfromtimestamp(b).strftime("%Y-%m-%d %H:%M:%S UTC")
    return {
        "first_ts": first_ts,
        "last_ts": last_ts,
        "first_iso": first_iso,
        "last_iso": last_iso,
        "span_days": span,
        "n_bars": len(candles),
        "timeframe": "5m",
        "label_zh": "回测窗口：%s → %s（共 %.2f 天，%s 根 5m）"
        % (first_iso, last_iso, span, len(candles)),
        "return_scope_zh": (
            "下列「总收益」仅为该日历窗口的累计权益变化，不是单日收益，"
            "不是自然周收益标签，也不是年化收益。"
        ),
    }


class DualMaClassicStrategy(object):
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

    def on_bar(self, i, candles, atrs, fast, slow):
        p = self.p
        c = candles[i]
        if atrs[i] is None or fast[i] is None or slow[i] is None:
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
                })
                self.position = None
                self.cooldown_until = i + int(p["frequency"]["cooldown_bars_after_exit"])
            return

        if self.day_counts[day] >= max_day:
            return
        if i < 1 or fast[i - 1] is None or slow[i - 1] is None:
            return
        atr_lb = int(p["entry"].get("atr_lookback") or 5)
        atr_prev = atrs[i - atr_lb] if i >= atr_lb else None
        expanding = True
        if p["entry"].get("require_atr_expand", True):
            if atr_prev is None:
                return
            expanding = atrs[i] > atr_prev * float(p["entry"].get("atr_expand_mult") or 1.05)
        if not expanding:
            return

        side = None
        if fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]:
            side = "long"
        elif fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]:
            side = "short"
        if side is None:
            return

        margin, notional = self.size_notional(atrs[i])
        close = float(c["close"])
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
    atrs = atr_pct(candles, int(params["position_sizing"]["atr_period"]))
    fast = ema(closes, int(params["entry"]["fast_ema"]))
    slow = ema(closes, int(params["entry"]["slow_ema"]))
    strat = DualMaClassicStrategy(params)
    equity_curve = [strat.equity]
    for i in range(len(candles)):
        strat.on_bar(i, candles, atrs, fast, slow)
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
    window = label_window(candles)
    sharpe = None
    if len(rets) >= 5 and window.get("span_days"):
        m = sum(rets) / float(len(rets))
        var = sum((r - m) ** 2 for r in rets) / float(max(len(rets) - 1, 1))
        sd = var ** 0.5
        if sd > 1e-12:
            trades_per_day = len(rets) / max(window["span_days"], 1e-9)
            sharpe = (m / sd) * math.sqrt(max(trades_per_day, 0.1) * 365.0)
    payoff = None
    if wins and losses:
        payoff = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
    wr = (len(wins) / float(len(rets))) if rets else None
    avg_per_day = (
        sum(strat.day_counts.values()) / float(len(strat.day_counts))
        if strat.day_counts else 0.0
    )
    presentable = bool(wr is not None and wr >= float(params["targets"].get("min_win_rate", 0.5))
                       and len(trades) >= 10)
    return {
        "ok": presentable,
        "present_to_human": presentable,
        "strategy_id": params.get("strategy_id"),
        "symbol": params.get("symbol"),
        "logic": params.get("logic"),
        "n_bars": len(candles),
        "n_trades": len(trades),
        "total_return": total_ret,
        "final_equity": strat.equity,
        "sharpe_ann_proxy": sharpe,
        "sharpe_note_zh": "短窗夏普代理极易虚高，仅供参考，不得单独当作达标证据。",
        "max_drawdown": mdd,
        "win_rate": wr,
        "payoff": payoff,
        "avg_trades_per_day": avg_per_day,
        "day_trade_counts": strat.day_counts,
        "targets": params.get("targets"),
        "classic_variant": params.get("classic_variant"),
        "rejected_prior": params.get("rejected_prior"),
        "costs": {
            "slippage_pct": params.get("slippage_pct"),
            "taker_fee_pct": params.get("taker_fee_pct"),
        },
        "trades_tail": trades[-15:],
        "data_window": window,
        "min_win_rate_gate": float(params["targets"].get("min_win_rate", 0.5)),
    }


if __name__ == "__main__":
    print(json.dumps(run_backtest(), ensure_ascii=False, indent=2, default=str))
