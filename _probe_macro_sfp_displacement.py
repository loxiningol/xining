#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quick L1/payoff probe for macro_sfp_displacement variants (research only)."""
from __future__ import print_function
import copy
import json
import sys
from pathlib import Path

ROOT = Path("/root")
sys.path.insert(0, str(ROOT))

import auto_trade_strategy_dsl as d
import auto_trade_dual_engine_factory as dual
from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen


def _pay(pnls):
    if not pnls:
        return 0.0, 0.0, 0.0
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p <= 0]
    mean = sum(pnls) / float(len(pnls))
    pay = (sum(wins) / len(wins)) / (sum(losses) / len(losses)) if wins and losses else 0.0
    wr = len(wins) / float(len(pnls))
    return mean, pay, wr


def _mk(direction, symbol, volz, trail, hold, use_h4=False, loose_engulf=False):
    pack = json.loads(Path("/root/strategy_macro_sfp_displacement_v1.json").read_text())
    dsl = copy.deepcopy(pack["dsl_short" if direction == "short" else "dsl_long"])
    dsl["supported_instruments"] = [symbol]
    dsl["key"] = "macro_sfp_%s_%s_v%.0f_t%.0f%s%s" % (
        direction, symbol.split("-")[0].lower(), volz * 10, trail * 10,
        "_h4" if use_h4 else "", "_loose" if loose_engulf else "",
    )[:90]
    dsl["timeframe"] = "15m"
    dsl["max_hold_bars"] = hold
    # patch vol / trail
    def walk(node):
        if isinstance(node, dict):
            if node.get("id") == "e_vol_displace":
                node["right"] = {"value": float(volz)}
            if node.get("exit_op") == "atr_trailing":
                node["n_atr"] = float(trail)
            if use_h4:
                # swap pdh/pdl anchors to h4 extremes
                for side in ("left", "right"):
                    op = node.get(side)
                    if isinstance(op, dict) and op.get("feature") in ("pdh", "pdl"):
                        feat = op["feature"]
                        op["feature"] = "h4_high24" if feat == "pdh" else "h4_low24"
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)
    walk(dsl)
    if loose_engulf:
        # drop strict engulf pair; keep directional body only
        entry = dsl.get("entry") or {}
        kids = list(entry.get("all") or [])
        keep = []
        for c in kids:
            cid = ""
            if isinstance(c, dict):
                cid = str(c.get("id") or "")
                if "any" in c:
                    keep.append(c)
                    continue
            if cid.startswith("e_engulf_"):
                continue
            keep.append(c)
        dsl["entry"]["all"] = keep
    return d.validate_strategy(dsl)


def evaluate(defn, frame, l1_n=12):
    bt = d.backtest_dsl(frame, defn, stop_loss_pct=0.009)
    pnls = [float(t.get("pnl_ratio") or 0) for t in (bt.get("trades") or [])]
    mean, pay, wr = _pay(pnls)
    ok = 0
    for i in range(l1_n):
        r = run_micro_screen(
            definition=defn, frame=frame,
            backtest_fn=lambda frm, dd, _d=defn: d.backtest_dsl(frm, _d, stop_loss_pct=0.009),
            seed=9100 + i * 7919,
        )
        if r.get("pass"):
            ok += 1
    return {
        "n": len(pnls), "mean": round(mean, 5), "pay": round(pay, 3),
        "wr": round(wr, 3), "l1_%d" % l1_n: ok,
    }


def main():
    # clear frame cache so new pdh columns appear
    import auto_trade_human_confirm_pipeline as pipe
    pipe._FRAME_CACHE.clear()

    variants = []
    for sym in ("BTC-USDT-SWAP", "ETH-USDT-SWAP"):
        fr = dual._frame(sym, "15m")
        assert "pdh" in fr.columns and "pdl" in fr.columns, "missing pdh/pdl on %s" % sym
        print("FRAME", sym, "cols_ok", "pdh" in fr.columns, "h4", "h4_high24" in fr.columns,
              "pdh_nan", float(fr["pdh"].isna().mean()), flush=True)
        for direction in ("short", "long"):
            for volz in (1.8, 1.5):
                for trail in (5.0, 4.0):
                    for use_h4 in (False, True):
                        for loose in (False, True):
                            defn = _mk(direction, sym, volz, trail, 48, use_h4=use_h4, loose_engulf=loose)
                            m = evaluate(defn, fr, l1_n=10)
                            row = {
                                "sym": sym, "dir": direction, "volz": volz, "trail": trail,
                                "h4": use_h4, "loose": loose, **m,
                            }
                            variants.append(row)
                            print("ROW", json.dumps(row, ensure_ascii=False), flush=True)

    ranked = sorted(variants, key=lambda r: (-(r.get("l1_10") or 0), -(r.get("pay") or 0), -(r.get("n") or 0)))
    print("TOP", json.dumps(ranked[:12], ensure_ascii=False, indent=2), flush=True)
    Path("/root/macro_sfp_probe_results.json").write_text(
        json.dumps({"ranked": ranked}, ensure_ascii=False, indent=2)
    )
    print("OUT /root/macro_sfp_probe_results.json", flush=True)


if __name__ == "__main__":
    main()
