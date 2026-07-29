#!/usr/bin/env python3
import json, sys
sys.path.insert(0, "/root")
import auto_trade_strategy_dsl as d
import auto_trade_dual_engine_factory as dual
from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen

dsl = {
    "schema": "qiyu_strategy_dsl_v1",
    "key": "stop_hunt_reclaim_short_eth15m_v1",
    "name": "stop hunt reclaim short",
    "direction": "short",
    "timeframe": "15m",
    "supported_instruments": ["ETH-USDT-SWAP"],
    "max_hold_bars": 18,
    "entry": {"all": [
        {"id": "e_sweep_prev", "left": {"feature": "high", "offset": 1}, "op": "gt",
         "right": {"feature": "prev_high20", "offset": 1}},
        {"id": "e_reclaim", "left": {"feature": "close"}, "op": "cross_below",
         "right": {"feature": "prev_high20"}},
        {"id": "e_vol", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 1.6}},
        {"id": "e_rej", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}},
    ]},
    "exit": {"any": [
        {"id": "x_atr", "exit_op": "atr_trailing", "n_atr": 4.0, "atr_period": 14, "role": "take_profit"},
        {"id": "x_sw", "exit_op": "swing_extreme", "lookback": 10, "role": "invalidation"},
    ]},
    "description": "nextbar sweep reclaim short eth15m",
}

defn = d.validate_strategy(dsl)
fr = dual._frame("ETH-USDT-SWAP", "15m")
bt = d.backtest_dsl(fr, defn, stop_loss_pct=0.009)
pnls = [float(t.get("pnl_ratio") or 0) for t in (bt.get("trades") or [])]
wins = [p for p in pnls if p > 0]
losses = [abs(p) for p in pnls if p <= 0]
print("ETH", "n", len(pnls), "mean", round(sum(pnls) / len(pnls), 4),
      "pay", round((sum(wins) / len(wins)) / (sum(losses) / len(losses)), 3),
      "wr", round(len(wins) / len(pnls), 3), flush=True)
ok = 0
for i in range(20):
    r = run_micro_screen(
        definition=defn, frame=fr,
        backtest_fn=lambda frm, dd: d.backtest_dsl(frm, defn, stop_loss_pct=0.009),
        seed=9000 + i * 7919,
    )
    if r.get("pass"):
        ok += 1
print("l1_20", ok, flush=True)
for sym in ("SOL-USDT-SWAP", "XRP-USDT-SWAP", "BTC-USDT-SWAP", "ADA-USDT-SWAP"):
    fr2 = dual._frame(sym, "15m")
    dsl2 = json.loads(json.dumps(dsl))
    dsl2["supported_instruments"] = [sym]
    dsl2["key"] = (dsl["key"] + "_" + sym.split("-")[0].lower())[:90]
    defn2 = d.validate_strategy(dsl2)
    bt2 = d.backtest_dsl(fr2, defn2, stop_loss_pct=0.009)
    pn = [float(t.get("pnl_ratio") or 0) for t in (bt2.get("trades") or [])]
    if len(pn) < 8:
        print(sym, "n", len(pn), flush=True)
        continue
    w = [p for p in pn if p > 0]
    l = [abs(p) for p in pn if p <= 0]
    mean = sum(pn) / len(pn)
    pay = (sum(w) / len(w)) / (sum(l) / len(l)) if w and l else 0
    print(sym, "n", len(pn), "mean", round(mean, 4), "pay", round(pay, 3),
          "wr", round(len(w) / len(pn), 3), flush=True)
print("CHECK_DONE", flush=True)
