#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETH5m wave2: fast-screen alternate logics, then full pipeline on best."""
from __future__ import print_function

import copy
import json
import os
import sys
import traceback
from datetime import datetime

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d
import frost3_eth5m_pipeline as pipe

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7

OUT = "/root/auto_trade/dual_engine"
PREFIX = "frost3_eth5m"
SYMBOL = "ETH-USDT-SWAP"
TF = "5m"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def leaf(feat, op, right):
    if isinstance(right, str):
        return {"left": {"feature": feat}, "op": op, "right": {"feature": right}}
    return {"left": {"feature": feat}, "op": op, "right": {"value": float(right)}}


def make_dsl(name, direction, entry, exits, hold):
    exit_any = []
    for i, e in enumerate(exits):
        ee = copy.deepcopy(e)
        ee["role"] = "take_profit" if i == 0 else "invalidation"
        exit_any.append(ee)
    return f2.ensure_dsl({
        "key": "frost3_eth5m_%s_%s" % (name[:24], direction),
        "name": "寒霜叁-ETH-5m-%s" % name,
        "direction": direction,
        "timeframe": TF,
        "supported_instruments": [SYMBOL],
        "max_hold_bars": hold,
        "description": name,
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "schema": "qiyu_strategy_dsl_v1",
    }, SYMBOL, TF)


def fast_metrics(dsl):
    import auto_trade_strategy_dsl as dsl_mod
    definition = dsl_mod.validate_strategy(dsl)
    base = d._backtest(definition, SYMBOL, TF, "observed_base")
    return d._metrics_from_trades(base.get("trades") or [])


def candidates():
    return [
        ("h1_slope_down_persistence", "short", [
            leaf("h1_ema19", "lt", "h1_ema53"), leaf("h1_slope4", "lt", -0.0004),
            leaf("close", "lt", "ema21"), leaf("ema8", "lt", "ema21"),
            leaf("rsi14", "lt", 48), leaf("rsi14", "gt", 34),
            leaf("macd_stick", "lt", 0), leaf("z20", "lt", -0.05), leaf("z20", "gt", -1.4),
        ], [leaf("rsi14", "lt", 30), leaf("close", "gt", "ema21")], 36),
        ("bear_ribbon_drift", "short", [
            leaf("h1_ema19", "lt", "h1_ema53"), leaf("h1_slope4", "lt", -0.0003),
            leaf("close", "lt", "ema16"), leaf("ema8", "lt", "ema21"),
            leaf("rsi14", "lt", 45), leaf("rsi14", "gt", 30),
            leaf("macd_stick", "lt", 0), leaf("z20", "lt", 0.2), leaf("z20", "gt", -1.6),
        ], [leaf("rsi14", "lt", 28), leaf("close", "gt", "ema21")], 36),
        ("macd_stick_persistence", "long", [
            leaf("h1_ema19", "gt", "h1_ema53"), leaf("h1_slope4", "gt", 0.0008),
            leaf("close", "gt", "ema16"), leaf("macd_stick", "gt", 0),
            leaf("rsi14", "gt", 55), leaf("rsi14", "lt", 68),
            leaf("z20", "gt", 0.2), leaf("z20", "lt", 1.5),
        ], [leaf("rsi14", "gt", 72), leaf("close", "lt", "ema21")], 32),
        ("atr_quiet_ribbon_drift", "long", [
            leaf("h1_ema19", "gt", "h1_ema53"), leaf("close", "gt", "ema21"),
            leaf("close", "gt", "ema8"), leaf("ema21", "gt", "ema53"),
            leaf("rsi14", "gt", 53), leaf("rsi14", "lt", 64),
            leaf("macd_stick", "gt", 0), leaf("z20", "gt", -0.2), leaf("z20", "lt", 1.2),
        ], [leaf("rsi14", "gt", 68), leaf("close", "lt", "ema16")], 40),
        ("trend_aligned_hold", "long", [
            leaf("h1_ema19", "gt", "h1_ema53"), leaf("h1_slope4", "gt", 0.0005),
            leaf("close", "gt", "ema21"), leaf("close", "gt", "ema53"),
            leaf("rsi14", "gt", 48), leaf("rsi14", "lt", 62),
            leaf("macd_stick", "gt", 0), leaf("cci", "gt", -50), leaf("cci", "lt", 120),
        ], [leaf("rsi14", "gt", 70), leaf("close", "lt", "ema21")], 36),
        # Sparse short: stronger filters (learn from r1 over-loose long)
        ("sparse_bear_drift", "short", [
            leaf("h1_ema19", "lt", "h1_ema53"), leaf("h1_slope4", "lt", -0.0006),
            leaf("close", "lt", "ema21"), leaf("close", "lt", "ema8"),
            leaf("rsi14", "lt", 42), leaf("rsi14", "gt", 28),
            leaf("macd_stick", "lt", 0), leaf("z20", "lt", -0.2), leaf("z20", "gt", -1.8),
            leaf("cci", "lt", -40),
        ], [leaf("rsi14", "lt", 25), leaf("close", "gt", "ema16")], 28),
        # z-mean revert long: deep negative z in H1 bull — NOT reclaim/exhaustion
        ("zscore_mean_revert_long", "long", [
            leaf("h1_ema19", "gt", "h1_ema53"), leaf("z20", "lt", -1.2),
            leaf("z20", "gt", -2.5), leaf("rsi14", "lt", 40), leaf("rsi14", "gt", 25),
            leaf("macd_stick", "gt", -5.0), leaf("close", "gt", "ema53"),
        ], [leaf("rsi14", "gt", 55), leaf("close", "lt", "prev_low20")], 24),
        # z-mean revert short
        ("zscore_mean_revert_short", "short", [
            leaf("h1_ema19", "lt", "h1_ema53"), leaf("z20", "gt", 1.2),
            leaf("z20", "lt", 2.5), leaf("rsi14", "gt", 60), leaf("rsi14", "lt", 75),
            leaf("macd_stick", "lt", 5.0), leaf("close", "lt", "ema53"),
        ], [leaf("rsi14", "lt", 45), leaf("close", "gt", "prev_high20")], 24),
    ]


def screen():
    rows = []
    for name, direction, entry, exits, hold in candidates():
        dsl = make_dsl(name, direction, entry, exits, hold)
        try:
            bm = fast_metrics(dsl)
            row = {
                "name": name, "direction": direction,
                "tr": bm.get("trades"), "fp": bm.get("fold_positive"),
                "folds": bm.get("folds"), "wr": bm.get("win_rate_pct"),
                "sharpe": bm.get("sharpe"), "mean_net": bm.get("mean_net"),
                "oos": bm.get("oos_profit"),
                "wf_ok": (
                    int(bm.get("trades") or 0) >= 8
                    and int(bm.get("folds") or 0) >= 10
                    and int(bm.get("fold_positive") or 0) >= 7
                ),
                "dsl": dsl,
            }
            print("[screen]", name, direction, "tr", row["tr"], "fp", row["fp"],
                  "wr", round(float(row["wr"] or 0), 2), "sharpe", row["sharpe"],
                  "wf", row["wf_ok"], flush=True)
        except Exception as exc:
            row = {"name": name, "direction": direction, "error": str(exc)}
            print("[screen-err]", name, exc, flush=True)
        rows.append(row)

    def score(r):
        if r.get("error"):
            return -1e9
        s = 0.0
        if r.get("wf_ok"):
            s += 500
        s += 40 * int(r.get("fp") or 0)
        s += float(r.get("sharpe") or -9) * 10
        s += float(r.get("wr") or 0)
        if float(r.get("mean_net") or 0) > 0:
            s += 30
        # prefer 12-80 trades
        tr = int(r.get("tr") or 0)
        if 12 <= tr <= 80:
            s += 25
        elif tr > 120:
            s -= 20
        return s

    rows.sort(key=score, reverse=True)
    out = {
        "at": _now(),
        "probes": [{k: v for k, v in r.items() if k != "dsl"} for r in rows],
        "best": {k: v for k, v in rows[0].items() if k != "dsl"} if rows else None,
    }
    if rows and not rows[0].get("error"):
        out["best_dsl"] = rows[0]["dsl"]
        out["best_logic"] = rows[0]["name"]
        out["best_direction"] = rows[0]["direction"]
    open(os.path.join(OUT, "%s_wave2_probe.json" % PREFIX), "w").write(
        json.dumps(out, ensure_ascii=False, indent=2, default=str) + "\n")
    return rows


def run_best(rows):
    best = None
    for r in rows:
        if not r.get("error") and r.get("dsl"):
            best = r
            break
    if not best:
        raise RuntimeError("no probe candidate")

    # Prefer first wf_ok if any
    for r in rows:
        if r.get("wf_ok") and r.get("dsl"):
            best = r
            break

    chosen = {
        "rank": 1,
        "symbol": SYMBOL,
        "timeframe": TF,
        "direction": best["direction"],
        "logic_class": best["name"],
        "thesis": "wave2 after r1 h1_slope_persistence long death; screened by base WF",
        "micro_behavior": "selected by fast screen fp/sharpe/wr",
        "entry_sketch": "from_dsl",
        "exit_sketch": "from_dsl",
        "how_avoids_cl_defects": "non-exhaustion; trades target>=12; structural exit",
        "diff_vs_live_ada_ltc_ng_xrp": "not exhaustion/reclaim/breakout/trend_pullback",
        "avoid_death": ["stop_cluster", "sample_starvation"],
        "expected_trades_hint": ">=12",
        "screen": {k: best.get(k) for k in ("tr", "fp", "wr", "sharpe", "wf_ok")},
    }
    pipe._write("%s_chosen.json" % PREFIX, chosen)
    pipe._write("%s_wave2_chosen.json" % PREFIX, chosen)

    book = {
        "title": "寒霜叁ETH5m-wave2-%s" % best["name"],
        "symbol": SYMBOL,
        "timeframe": TF,
        "direction": best["direction"],
        "logic_class": best["name"],
        "thesis": chosen["thesis"],
        "micro_behavior": chosen["micro_behavior"],
        "entry_sketch": chosen["entry_sketch"],
        "exit_sketch": chosen["exit_sketch"],
        "avoid_from_postmortem": chosen["how_avoids_cl_defects"],
        "diff_vs_live": chosen["diff_vs_live_ada_ltc_ng_xrp"],
        "dsl": best["dsl"],
        "gate_mode": "frost2",
        "source": "frost3_eth5m_wave2",
        "dir_rank": 2,
    }
    pipe._write("%s_book_init.json" % PREFIX, book)
    pipe._write("%s_hypothesis_book.json" % PREFIX, {
        "title": "策略逻辑假设书",
        "symbol": SYMBOL,
        "timeframe": TF,
        "direction": best["direction"],
        "logic_class": best["name"],
        "micro_behavior": chosen["micro_behavior"],
        "causal_entry": "DSL entry all-conditions confluence",
        "causal_exit": "TP rsi / structural invalidation / max_hold",
        "how_avoids_cl_defects": chosen["how_avoids_cl_defects"],
        "wave": 2,
        "screen": chosen["screen"],
    })

    report = {
        "trap_checklist": (pipe.load_eth_micro_packet().get("cl_archive") or {}).get("trap_checklist"),
        "wave": 2,
    }
    print("[wave2] PROCESS", best["name"], best["direction"], flush=True)
    result = pipe.process(book, report)

    end = {
        "at": _now(),
        "op": "寒霜叁ETH5m-wave2",
        "symbol": SYMBOL,
        "timeframe": TF,
        "logic_class": (result.get("book") or book).get("logic_class"),
        "direction": (result.get("book") or book).get("direction"),
        "ok": bool(result.get("ok")),
        "failed_step": result.get("failed_step"),
        "reason": result.get("reason"),
        "death_cause_zh": result.get("death_cause_zh"),
        "pending_key": ((result.get("pending") or {}).get("key")),
        "sim": {
            "ds": (result.get("sim") or {}).get("wr_deepseek_sim"),
            "qw": (result.get("sim") or {}).get("wr_qwen_sim"),
        } if result.get("sim") else None,
        "formal": result.get("formal"),
        "metrics": result.get("metrics"),
        "full": result.get("full"),
        "hist": result.get("hist"),
        "book_key": ((result.get("book") or book).get("dsl") or {}).get("key"),
        "screen_best": chosen["screen"],
        "r1_archive": "/root/auto_trade/dual_engine/archive/frost3_eth5m_h1_slope_persistence_r1",
    }
    if not result.get("ok"):
        arch = pipe.archive_fail(result, result.get("book") or book)
        end["archive_path"] = arch
        print("[wave2] ARCHIVED", result.get("failed_step"), arch, flush=True)
    else:
        print("[wave2] PENDING", end.get("pending_key"), flush=True)

    pipe._write("%s_end_report.json" % PREFIX, end)
    if end["ok"]:
        summary = (
            "成功：ETH5m %s/%s 已进pending key=%s；Sim DS=%s Qwen=%s"
            % (end.get("logic_class"), end.get("direction"), end.get("pending_key"),
               (end.get("sim") or {}).get("ds"), (end.get("sim") or {}).get("qw"))
        )
    else:
        summary = (
            "失败：ETH5m wave2 停在 %s；死因=%s；r1已归档斜率多头；归档=%s"
            % (end.get("failed_step"), end.get("death_cause_zh"), end.get("archive_path"))
        )
    pipe._write("%s_parent_summary.json" % PREFIX, {"summary_zh": summary, "end": end})
    print("=== ETH5m PARENT ===", summary, flush=True)
    return 0 if end["ok"] else 1


def main():
    print("[wave2] START", _now(), flush=True)
    # kill leftover heavy probe if any
    rows = screen()
    return run_best(rows)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
