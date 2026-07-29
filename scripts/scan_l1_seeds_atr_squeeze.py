#!/usr/bin/env python3
import json
import sys

sys.path.insert(0, "/root")
import auto_trade_strategy_dsl as d
import auto_trade_dual_engine_factory as dual
from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen

pack = json.load(open("/root/strategy_atr_squeeze_structural_breakout.json"))
defn = d.validate_strategy(pack["dsl_long"])
fr = dual._frame("BTC-USDT-SWAP", "15m")
tid = "wsa_20260729_204421_a119"
seed = hash(tid) % (2 ** 31)
print("task_seed", seed, flush=True)
l1 = run_micro_screen(
    definition=defn,
    frame=fr,
    backtest_fn=lambda frm, dd: d.backtest_dsl(frm, defn, stop_loss_pct=0.009),
    seed=seed,
)
print("task_l1", l1.get("pass"), l1.get("reject_reasons"), l1.get("metrics"), flush=True)
ok = 0
fail_reasons = {}
for i in range(30):
    s = 1000 + i * 9973
    r = run_micro_screen(
        definition=defn,
        frame=fr,
        backtest_fn=lambda frm, dd: d.backtest_dsl(frm, defn, stop_loss_pct=0.009),
        seed=s,
    )
    if r.get("pass"):
        ok += 1
    else:
        why = (r.get("reject_reasons") or ["?"])[0]
        fail_reasons[why] = fail_reasons.get(why, 0) + 1
print("pass_rate", ok, "/30", "fails", fail_reasons, flush=True)
