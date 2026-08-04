#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import re
import sys

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7


def main():
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 54.0}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.1}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
        {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 50.0}},
    ]
    dsl = f2.ensure_dsl({
        "key": "cci_h12", "name": "CL", "direction": "short",
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
        "max_hold_bars": 12,
    }, "CL-USDT-SWAP", "15m")
    base = d._backtest(dsl, "CL-USDT-SWAP", "15m", "observed_base")
    trades = base.get("trades") or []
    print("n", len(trades), "keys", sorted(trades[0].keys()) if trades else None)
    pnls = []
    slim_trades = []
    for i, t in enumerate(trades):
        pr = float(t.get("pnl_ratio") or 0)
        pnls.append(pr)
        slim = {k: t.get(k) for k in (
            "pnl_ratio", "exit_reason", "hold_bars", "entry_ts", "exit_ts",
            "entry_price", "exit_price", "role", "exit_role", "bars_held", "reason",
            "entry_bar", "exit_bar")}
        slim_trades.append(slim)
        print(i, slim)
    print("pnls", pnls)
    mc = f2.run_monte_carlo_beat_shuffles(trades)
    print("mc", mc)

    feat_cands = []
    for path in [
        "/root/auto_trade_strategy_dsl.py",
        "/root/auto_trade_feature_engine.py",
        "/root/qiyu_features.py",
        "/root/auto_trade_dual_engine_factory.py",
    ]:
        if not os.path.exists(path):
            continue
        txt = open(path).read()
        ms = re.findall(r"[\"']([a-z][a-z0-9_]{2,30})[\"']", txt)
        cand = sorted(set(
            m for m in ms
            if any(x in m for x in (
                "ema", "rsi", "cci", "atr", "z20", "macd", "bb", "vol",
                "prev_", "h1_", "adx", "obv", "stoch", "willr"))
        ))
        print(path, "n", len(cand))
        feat_cands.extend(cand)

    # XRP / ADA live defs
    live_hits = []
    for root, _dirs, files in os.walk("/root/auto_trade"):
        for fn in files:
            low = fn.lower()
            if not fn.endswith(".json"):
                continue
            if any(x in low for x in ("xrp", "ada", "frost", "pending", "assignment")):
                live_hits.append(os.path.join(root, fn))
    print("live_json_n", len(live_hits))
    for p in live_hits[:40]:
        print("hit", p)

    out = {
        "n": len(trades),
        "pnls": pnls,
        "mc": mc,
        "trades": slim_trades,
        "features_sample": sorted(set(feat_cands))[:120],
    }
    open("/root/auto_trade/dual_engine/frost2_cont_cl_autopsy.json", "w").write(
        json.dumps(out, ensure_ascii=False, indent=2, default=str) + "\n")
    print("wrote autopsy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
