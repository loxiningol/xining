#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Multi-symbol Donchian20 + HTF trend filter screen (no auto-mount)."""
from __future__ import print_function
import json, pickle, time
from pathlib import Path
import auto_trade_strategy_dsl as dsl_mod

FRAMES = [
  ("BTC-USDT-SWAP", "5m", [
    "/root/auto_trade/codex_0725_train5/frame_BTC_USDT_SWAP_5m.pkl",
    "/root/auto_trade/dual_engine/frame_BTC_USDT_SWAP_5m.pkl",
  ]),
  ("BTC-USDT-SWAP", "15m", [
    "/root/auto_trade/codex_0725_train5/frame_BTC_USDT_SWAP_15m.pkl",
    "/root/auto_trade/dual_engine/frost3_btc15m_frame.pkl",
  ]),
  ("ETH-USDT-SWAP", "15m", ["/root/auto_trade/dual_engine/frost3_eth15m_fade_frame.pkl"]),
  ("ETH-USDT-SWAP", "5m", ["/root/auto_trade/codex_0725_train5/frame_ETH_USDT_SWAP_5m.pkl"]),
  ("SOL-USDT-SWAP", "5m", ["/root/auto_trade/codex_0725_train5/frame_SOL_USDT_SWAP_5m.pkl"]),
  ("SOL-USDT-SWAP", "15m", ["/root/auto_trade/dual_engine/frost3_sol15m_frame.pkl"]),
  ("BNB-USDT-SWAP", "15m", ["/root/auto_trade/codex_0725_train5/frame_BNB_USDT_SWAP_15m.pkl"]),
  ("BNB-USDT-SWAP", "5m", ["/root/auto_trade/dual_engine/frost3_bnb5m_session_frame.pkl"]),
  ("LTC-USDT-SWAP", "5m", ["/root/auto_trade/codex_0725_train5/frame_LTC_USDT_SWAP_5m.pkl"]),
  ("XAU-USDT-SWAP", "15m", ["/root/auto_trade/dual_engine/frost3_xau15m_breakout_frame.pkl"]),
  ("XAG-USDT-SWAP", "5m", ["/root/auto_trade/codex_0725_train5/frame_XAG_USDT_SWAP_5m.pkl"]),
  ("CL-USDT-SWAP", "5m", ["/root/auto_trade/codex_0725_train5/frame_CL_USDT_SWAP_5m.pkl"]),
]

def load_frame(paths):
  for p in paths:
    fp = Path(p)
    if fp.exists():
      obj = pickle.load(open(str(fp), "rb"))
      if hasattr(obj, "columns") and len(obj) > 500:
        return obj, str(fp)
  return None, None

def mk_long(key, name, tf, slope_min, volz_min, z_max, hold, use_struct, use_body):
  entry = [
    {"id":"bo","left":{"feature":"close"},"op":"cross_above","right":{"feature":"prev_high20"}},
    {"id":"h1","left":{"feature":"h1_ema19"},"op":"gt","right":{"feature":"h1_ema53"}},
    {"id":"slope","left":{"feature":"h1_slope4"},"op":"gt","right":{"value": float(slope_min)}},
  ]
  if use_struct:
    entry += [
      {"id":"s1","left":{"feature":"close"},"op":"gt","right":{"feature":"close","offset":1}},
      {"id":"s2","left":{"feature":"close","offset":1},"op":"gt","right":{"feature":"close","offset":2}},
    ]
  if volz_min is not None:
    entry.append({"id":"vz","left":{"feature":"vol_z20"},"op":"gt","right":{"value": float(volz_min)}})
  if z_max is not None:
    entry.append({"id":"z","left":{"feature":"z20"},"op":"lt","right":{"value": float(z_max)}})
  if use_body:
    entry.append({"id":"body","left":{"feature":"close"},"op":"gt","right":{"feature":"open"}})
  entry.append({"id":"px","left":{"feature":"close"},"op":"gt","right":{"feature":"ema21"}})
  return {
    "schema":"qiyu_strategy_dsl_v1",
    "key": key,
    "name": name,
    "direction":"long",
    "timeframe": tf,
    "entry":{"all": entry},
    "exit":{"any":[
      {"id":"tp","left":{"feature":"rsi14"},"op":"gt","right":{"value":60.0},"role":"take_profit"},
      {"id":"inv","left":{"feature":"close"},"op":"lt","right":{"feature":"prev_low20"},"role":"invalidation"},
    ]},
    "max_hold_bars": int(hold),
    "auto_trade_eligible": False,
    "live_enabled": False,
  }

def metrics(res):
  trades = res.get("trades") or []
  pnls = [float(t.get("pnl_ratio") or 0.0) for t in trades]
  n = len(pnls)
  if not n:
    return {"trades":0}
  mean = sum(pnls)/float(n)
  wins = [p for p in pnls if p>0]
  losses = [p for p in pnls if p<=0]
  wr = len(wins)/float(n)
  mean_win = (sum(wins)/len(wins)) if wins else 0.0
  mean_loss = (sum(losses)/len(losses)) if losses else 0.0
  payoff = (mean_win/abs(mean_loss)) if mean_loss < 0 else 999.0
  # equity path
  eq=1.0; peak=1.0; mdd=0.0
  for p in pnls:
    eq *= (1.0+p)
    if eq>peak: peak=eq
    dd=(peak-eq)/peak if peak>0 else 0
    if dd>mdd: mdd=dd
  total = eq-1.0
  return {
    "trades": n, "wr": wr, "mean_net": mean, "mean_win": mean_win,
    "payoff": payoff, "total": total, "mdd": mdd,
    "ret_mdd": (total/mdd) if mdd>1e-9 else None,
  }

def main():
  rows=[]
  variants=[]
  for slope in (0.0, 0.0005, 0.0015):
    for volz in (None, 1.0, 1.6):
      for zmax in (None, 2.3):
        for hold in (14, 24, 36):
          for struct in (False, True):
            for body in (False, True):
              # prune explosion: skip some combos
              if struct and body and volz is None and zmax is None:
                continue
              variants.append((slope, volz, zmax, hold, struct, body))
  # de-dup / cap
  seen=set(); cleaned=[]
  for v in variants:
    if v in seen: continue
    seen.add(v); cleaned.append(v)
  # prioritize lean variants first
  cleaned = cleaned[:80]
  print("N_VARIANTS", len(cleaned), flush=True)
  for sym, tf, paths in FRAMES:
    fr, src = load_frame(paths)
    if fr is None:
      print("NO_FRAME", sym, tf, flush=True); continue
    cols=set(fr.columns)
    need=["prev_high20","prev_low20","h1_ema19","h1_ema53","h1_slope4","ema21","rsi14","close","open"]
    if any(c not in cols for c in need):
      print("SKIP_COLS", sym, tf, flush=True); continue
    has_volz = "vol_z20" in cols
    has_z = "z20" in cols
    print("FRAME", sym, tf, "n", len(fr), "src", src, flush=True)
    for slope, volz, zmax, hold, struct, body in cleaned:
      if volz is not None and not has_volz: continue
      if zmax is not None and not has_z: continue
      key="donch_scr_%s_%s_s%s_vz%s_z%s_h%s_st%s_b%s" % (
        sym.split("-")[0].lower(), tf, slope, volz, zmax, hold, int(struct), int(body))
      dsl=mk_long(key, "唐奇安趋势突破筛", tf, slope, volz, zmax, hold, struct, body)
      dsl["supported_instruments"]=[sym]
      try:
        dsl_mod.validate_strategy(dsl)
        res=dsl_mod.backtest_dsl(fr, dsl, stop_loss_pct=0.009)
      except Exception as exc:
        continue
      m=metrics(res)
      if m.get("trades",0) < 8: continue
      if m.get("wr",0) < 0.50: continue
      row={
        "sym":sym,"tf":tf,"slope":slope,"volz":volz,"zmax":zmax,
        "hold":hold,"struct":struct,"body":body,
        **m, "src":src, "key":key,
      }
      rows.append(row)
      print("HIT", json.dumps(row, ensure_ascii=False, default=str)[:400], flush=True)
  rows.sort(key=lambda r: (r.get("wr",0), r.get("mean_net",0), r.get("trades",0)), reverse=True)
  out=Path("/root/auto_trade/dual_engine/donchian_multi_screen_v1.json")
  out.write_text(json.dumps({"ok":True,"n":len(rows),"top":rows[:40],"all":rows}, ensure_ascii=False, indent=2), encoding="utf-8")
  print("WROTE", out, "hits", len(rows), flush=True)
  for r in rows[:12]:
    print("TOP", r["sym"], r["tf"], "wr", round(r["wr"],3), "n", r["trades"], "mean", round(r["mean_net"],4), "total", round(r["total"],4), "struct", r["struct"], "volz", r["volz"], "slope", r["slope"], "hold", r["hold"], flush=True)

if __name__=="__main__":
  main()
