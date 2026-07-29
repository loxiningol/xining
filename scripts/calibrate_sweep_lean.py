#!/usr/bin/env python3
import gc, json, sys
sys.path.insert(0, "/root")
import auto_trade_strategy_dsl as d
import auto_trade_dual_engine_factory as dual
from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen

def make(direction, style, volz, trail, hold, tf, atr_min=None):
    key = ("sl_%s_%s_%s_v%s_t%s" % (direction[0], style[0], tf, volz, trail)).replace(".", "")[:80]
    if direction == "short":
        if style == "samebar":
            leaves = [
                {"id": "e_sweep", "left": {"feature": "high"}, "op": "gt", "right": {"feature": "prev_high20"}},
                {"id": "e_reclaim", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_high20"}},
            ]
        else:
            leaves = [
                {"id": "e_sweep_prev", "left": {"feature": "high", "offset": 1}, "op": "gt",
                 "right": {"feature": "prev_high20", "offset": 1}},
                {"id": "e_reclaim", "left": {"feature": "close"}, "op": "cross_below",
                 "right": {"feature": "prev_high20"}},
            ]
        leaves += [
            {"id": "e_vol", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": volz}},
            {"id": "e_rej", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}},
        ]
    else:
        if style == "samebar":
            leaves = [
                {"id": "e_sweep", "left": {"feature": "low"}, "op": "lt", "right": {"feature": "prev_low20"}},
                {"id": "e_reclaim", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_low20"}},
            ]
        else:
            leaves = [
                {"id": "e_sweep_prev", "left": {"feature": "low", "offset": 1}, "op": "lt",
                 "right": {"feature": "prev_low20", "offset": 1}},
                {"id": "e_reclaim", "left": {"feature": "close"}, "op": "cross_above",
                 "right": {"feature": "prev_low20"}},
            ]
        leaves += [
            {"id": "e_vol", "left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": volz}},
            {"id": "e_rej", "left": {"feature": "close"}, "op": "gt", "right": {"feature": "open"}},
        ]
    if atr_min is not None:
        leaves.append({"id": "e_atr_min", "left": {"feature": "atr14"}, "op": "gt",
                       "right": {"feature": "close", "scale": atr_min}})
    return {
        "schema": "qiyu_strategy_dsl_v1", "key": key, "name": "stop hunt range reclaim",
        "direction": direction, "timeframe": tf, "supported_instruments": ["BTC-USDT-SWAP"],
        "max_hold_bars": hold,
        "entry": {"all": leaves},
        "exit": {"any": [
            {"id": "x_atr", "exit_op": "atr_trailing", "n_atr": trail, "atr_period": 14, "role": "take_profit"},
            {"id": "x_sw", "exit_op": "swing_extreme", "lookback": 10, "role": "invalidation"},
        ]},
        "description": "vol confirmed liquidity sweep then range reclaim; atr trail; no fixed pct tp; no rsi/cci",
    }

def eval_one(fr, dsl, do_l1=False):
    defn = d.validate_strategy(dsl)
    bt = d.backtest_dsl(fr, defn, stop_loss_pct=0.009)
    pnls = [float(t.get("pnl_ratio") or 0) for t in (bt.get("trades") or [])]
    n = len(pnls)
    if n < 10:
        return None
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p <= 0]
    if not wins or not losses:
        return None
    out = {
        "n": n,
        "mean": round(sum(pnls) / n, 4),
        "pay": round((sum(wins) / len(wins)) / (sum(losses) / len(losses)), 3),
        "wr": round(len(wins) / float(n), 3),
    }
    if do_l1:
        ok = 0
        for i in range(10):
            r = run_micro_screen(
                definition=defn, frame=fr,
                backtest_fn=lambda frm, dd, defn=defn: d.backtest_dsl(frm, defn, stop_loss_pct=0.009),
                seed=8000 + i * 6151,
            )
            if r.get("pass"):
                ok += 1
        out["l1_10"] = ok
    return out

def main():
    variants = []
    for direction in ("short", "long"):
        for style in ("nextbar", "samebar"):
            for volz, trail, atr_min in (
                (1.2, 3.5, None),
                (1.2, 4.0, None),
                (1.6, 4.0, None),
                (1.2, 4.0, 0.0015),
            ):
                variants.append((direction, style, volz, trail, atr_min))
    rows = []
    chosen_pack = None
    for tf in ("15m", "5m"):
        hold = 18 if tf == "15m" else 30
        for sym in ("SOL-USDT-SWAP", "XRP-USDT-SWAP", "ETH-USDT-SWAP", "ADA-USDT-SWAP", "DOGE-USDT-SWAP", "BTC-USDT-SWAP"):
            try:
                fr = dual._frame(sym, tf)
            except Exception as e:
                print("NOFRAME", sym, tf, e, flush=True)
                continue
            print("FRAME", sym, tf, len(fr), flush=True)
            for direction, style, volz, trail, atr_min in variants:
                dsl = make(direction, style, volz, trail, hold, tf, atr_min)
                dsl["supported_instruments"] = [sym]
                try:
                    m = eval_one(fr, dsl, do_l1=False)
                except Exception:
                    continue
                if not m:
                    continue
                row = dict(m)
                row.update({
                    "symbol": sym, "tf": tf, "dir": direction, "style": style,
                    "volz": volz, "trail": trail, "atr_min": atr_min, "hold": hold,
                })
                rows.append(row)
                if row["mean"] > 0 and row["pay"] >= 1.8 and row["n"] >= 15:
                    print("HIT", row, flush=True)
            del fr
            gc.collect()
    rows.sort(key=lambda r: (r["mean"], r["pay"], r["n"]), reverse=True)
    print("TOP15", flush=True)
    for r in rows[:15]:
        print(r, flush=True)
    pos = [r for r in rows if r["mean"] > 0]
    print("POS_N", len(pos), flush=True)
    cands = [r for r in pos if r["pay"] >= 1.5] or pos or rows[:3]
    best = None
    for r in cands[:5]:
        fr = dual._frame(r["symbol"], r["tf"])
        dsl = make(r["dir"], r["style"], r["volz"], r["trail"], r["hold"], r["tf"], r["atr_min"])
        dsl["supported_instruments"] = [r["symbol"]]
        m = eval_one(fr, dsl, do_l1=True)
        if not m:
            continue
        r2 = dict(r)
        r2["l1_10"] = m.get("l1_10", 0)
        print("L1CHECK", r2, flush=True)
        if best is None or (r2.get("l1_10", 0), r2["mean"], r2["pay"]) > (best.get("l1_10", 0), best["mean"], best["pay"]):
            best = r2
            chosen_pack = {"row": r2, "dsl": dsl}
        del fr
        gc.collect()
    out = {"top": rows[:25], "positive": pos[:25], "chosen": chosen_pack, "pos_n": len(pos)}
    __import__("pathlib").Path("/root/auto_trade/dual_engine/workflow_v2/sweep_reclaim_lean.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2)
    )
    print("DONE", "chosen", best, flush=True)

if __name__ == "__main__":
    main()
