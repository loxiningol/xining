# -*- coding: utf-8 -*-
"""Portfolio validation for the final frequency-expansion candidate set."""
from __future__ import print_function

from datetime import timedelta
import json
from pathlib import Path

import pandas as pd

import evaluate_live_portfolio_frequency as freq
import optimize_intraday_rules as opt


ROOT = Path("/root")
START = pd.Timestamp("2026-03-15 00:00:00")
END = pd.Timestamp("2026-07-20 23:59:59")
CANDIDATES = [
    {"key":"xag5_session_breakdown_short_ai", "symbol":"XAG-USDT-SWAP",
     "family":"breakout", "side":"short",
     "params":{"atr_max":.004,"atr_min":.0008,"body":.6,"cci":50,"slope":.0005},
     "hours":(0,8), "tp":.006,"sl":.009,"hold":72},
    {"key":"ltc5_exhaustion_fade_short_ai", "symbol":"LTC-USDT-SWAP",
     "family":"exhaustion_reversal", "side":"short",
     "params":{"atr_max":.008,"atr_min":.0003,"rsi":25,"slope":0.0,
               "slope_limit":.006,"z":1.25},
     "hours":None, "tp":.009,"sl":.009,"hold":72},
    {"key":"ada5_session_trend_pullback_short_ai", "symbol":"ADA-USDT-SWAP",
     "family":"trend_pullback", "side":"short",
     "params":{"atr_max":.015,"atr_min":.0003,"osc":45,"slope":.0005,
               "touch":0.0},
     "hours":(12,20), "tp":.009,"sl":.006,"hold":48},
]


def candidate_rows(candidate):
    symbol = candidate["symbol"]
    filename = symbol.replace("-", "_") + "_5m_OKX_LOCAL.parquet"
    path = ROOT / "market_data" / symbol / "5m" / filename
    frame = opt.indicators(pd.read_parquet(str(path)).sort_index())
    mask = opt.signal_mask(frame, candidate["family"], candidate["side"],
                           candidate["params"])
    if candidate["hours"]:
        start, end = candidate["hours"]
        hour = frame.index.hour.to_numpy()
        mask &= (hour >= start) & (hour < end)
    trades = opt.simulate(frame, mask, candidate["side"], START, END,
                          candidate["tp"], candidate["sl"], candidate["hold"])
    return [{"symbol":symbol,"timeframe":"5m","strategy_key":candidate["key"],
             "entry":row[0].to_pydatetime(),"exit":row[1].to_pydatetime(),
             "profit":row[2]>0,"pnl_ratio":float(row[2]*20),
             "risk_ratio":.15*20*candidate["sl"]} for row in trades]


def main():
    data = json.load(open(str(ROOT/"auto_trade/live_portfolio_frequency.json"),
                          "r",encoding="utf-8"))
    current = []
    for row in data.get("accepted",[]):
        item = dict(row)
        item["entry"],item["exit"] = freq.parse_time(item["entry"]),freq.parse_time(item["exit"])
        current.append(item)
    reports, extra = [], []
    for candidate in CANDIDATES:
        rows = candidate_rows(candidate)
        reports.append({"key":candidate["key"],"trades":len(rows),
                        "wins":sum(1 for r in rows if r["profit"]),
                        "win_rate":sum(1 for r in rows if r["profit"])/float(len(rows))*100 if rows else 0})
        extra.extend(rows)
    policy = json.load(open(str(ROOT/"auto_trade/portfolio_risk_policy.json"),"r",encoding="utf-8"))
    combined,rejected = freq.simulate(current+extra,policy)
    start,end = START.to_pydatetime(),END.to_pydatetime()
    periods = {}
    for name,days in (("full",127),("last_90d",89),("last_60d",59),("last_30d",29)):
        a = start if name=="full" else max(start,end-timedelta(days=days))
        periods[name]=freq.period_metrics(combined,a,end)
    result={"candidates":reports,"current":len(current),"combined":len(combined),
            "rejected":dict(rejected),"periods":periods}
    (ROOT/"auto_trade/frequency_target_portfolio.json").write_text(
        json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,default=str))


if __name__ == "__main__":
    main()
