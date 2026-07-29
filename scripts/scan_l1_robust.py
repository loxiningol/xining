#!/usr/bin/env python3
import copy, json, sys, gc
sys.path.insert(0,"/root")
import auto_trade_strategy_dsl as d
import auto_trade_dual_engine_factory as dual
from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen

base=json.load(open("/root/strategy_atr_squeeze_structural_breakout.json"))["dsl_long"]
fr=dual._frame("BTC-USDT-SWAP","15m")

def make(volz, trail, lag=5, hold=24):
    x=copy.deepcopy(base)
    x["key"]=("r_%s_%s_%s"%(str(volz).replace(".",""), str(trail).replace(".",""), lag))[:90]
    x["max_hold_bars"]=hold
    for leaf in x["entry"]["all"]:
        if leaf["id"]=="e_atr_decl":
            leaf["right"]={"feature":"atr14","offset":lag}
        if leaf["id"]=="e_vol_confirm":
            leaf["right"]={"value":volz}
    for leaf in x["exit"]["any"]:
        if leaf.get("exit_op")=="atr_trailing":
            leaf["n_atr"]=trail
    x["exit"]["any"]=[e for e in x["exit"]["any"] if e.get("exit_op")!="swing_extreme"]
    return x

cands=[]
for volz in (0.8,1.0,1.2):
  for trail in (2.5,3.0,3.5):
    for lag in (5,8):
      cands.append((volz,trail,lag))
print("cands", len(cands), flush=True)
rows=[]
for volz,trail,lag in cands:
    dsl=make(volz,trail,lag)
    defn=d.validate_strategy(dsl)
    bt=d.backtest_dsl(fr, defn, stop_loss_pct=0.009)
    trades=bt.get("trades") or []
    pnls=[float(t.get("pnl_ratio") or 0) for t in trades]
    wins=[p for p in pnls if p>0]; losses=[abs(p) for p in pnls if p<=0]
    pay=(sum(wins)/len(wins))/(sum(losses)/len(losses)) if wins and losses else 0
    ok=0
    for i in range(20):
        s=2000+i*7919
        r=run_micro_screen(definition=defn, frame=fr,
            backtest_fn=lambda frm,dd,defn=defn: d.backtest_dsl(frm,defn,stop_loss_pct=0.009), seed=s)
        if r.get("pass"): ok+=1
    row={"volz":volz,"trail":trail,"lag":lag,"n":len(pnls),"pay":round(pay,3),"l1_20":ok}
    print(row, flush=True)
    rows.append(row)
    del bt,trades,pnls,wins,losses,defn
    gc.collect()
rows.sort(key=lambda r:(r["l1_20"], r["pay"], r["n"]), reverse=True)
print("BEST", rows[:8], flush=True)
json.dump({"rows":rows,"best":rows[:8]}, open("/root/auto_trade/dual_engine/workflow_v2/atr_squeeze_robust.json","w"), indent=2)
print("DONE", flush=True)
