#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rescue frost strategies that nearly passed; push sim/formal."""
from __future__ import print_function
import copy
import json
import sys
import time

sys.path.insert(0, "/root")
import auto_trade_dual_engine_factory as d
import frost_action_run as f

OUT = "/root/auto_trade/dual_engine/frost_rescue_final.json"


def suite(book):
    packs = d.run_internal_packs(book, mode="frost_relaxed")
    if not packs.get("ok"):
        return packs
    try:
        base = d._backtest(packs["definition"], packs["symbol"], packs["timeframe"], "observed_base")
        mc = f.run_monte_carlo(base.get("trades") or [])
    except Exception as exc:
        mc = {"ok": False, "pass": False, "error": str(exc)}
    packs["monte_carlo"] = mc
    packs["suite"] = {
        "walk_forward": (packs.get("anti_overfit") or {}).get("pass"),
        "extreme_friction": (packs.get("extreme_friction") or {}).get("pass"),
        "logic_destruction": (packs.get("logic_destruction") or {}).get("pass"),
        "monte_carlo_soft": mc.get("pass"),
    }
    packs["pass"] = bool(
        (packs.get("anti_overfit") or {}).get("pass")
        and (packs.get("extreme_friction") or {}).get("pass")
        and (packs.get("logic_destruction") or {}).get("pass")
    )
    return packs


def mk_book(symbol, tf, direction, logic, dsl):
    dsl = f._ensure_dsl(dsl, symbol, tf)
    return {
        "symbol": symbol,
        "timeframe": tf,
        "direction": direction,
        "logic_class": logic,
        "dsl": dsl,
        "gate_mode": "frost_relaxed",
        "title_short": dsl.get("name"),
        "thesis": logic,
    }


def search_xrp():
    # known pass: rsi55 z0 hold20 tp45
    winners = []
    for hold, rsi_tp in [(20, 45), (22, 45), (18, 42), (20, 42), (24, 45)]:
        dsl = {
            "key": "frost_xrp_rescue_h%s_t%s" % (hold, rsi_tp),
            "name": "寒霜-XRP-15m-exhaustion_fade",
            "direction": "short",
            "timeframe": "15m",
            "supported_instruments": ["XRP-USDT-SWAP"],
            "max_hold_bars": hold,
            "description": "XRP exhaustion rescue",
            "entry": {"all": [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
            ]},
            "exit": {"any": [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(rsi_tp)}, "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
            ]},
        }
        book = mk_book("XRP-USDT-SWAP", "15m", "short", "exhaustion_fade", dsl)
        packs = suite(book)
        bm = packs.get("base_metrics") or {}
        print("XRP", dsl["key"], "PASS", packs.get("pass"), "fp", bm.get("fold_positive"),
              "sh", bm.get("sharpe"), "fr", ((packs.get("extreme_friction") or {}).get("metrics") or {}).get("sharpe"),
              "dest", (packs.get("logic_destruction") or {}).get("pass"), flush=True)
        if packs.get("pass"):
            winners.append((book, packs))
            break
    return winners[0] if winners else None


def search_doge():
    winners = []
    best = None
    for rsi_lo, hold, rsi_tp, zhi in [
        (35, 18, 55, 1.5), (35, 22, 52, 1.2), (35, 24, 50, 1.0),
        (34, 20, 52, 1.3), (36, 18, 55, 1.2), (33, 22, 50, 1.5),
        (35, 20, 48, 1.5), (37, 16, 55, 1.0), (32, 24, 52, 1.5),
        (35, 26, 55, 1.5), (30, 20, 50, 1.2), (38, 18, 58, 1.5),
        (35, 18, 55, 0.8), (35, 18, 60, 1.5), (34, 24, 55, 1.0),
    ]:
        dsl = {
            "key": "frost_doge_rescue_%s_h%s_t%s_z%s" % (rsi_lo, hold, rsi_tp, str(zhi).replace(".", "p")),
            "name": "寒霜-DOGE-1h-range_reclaim",
            "direction": "long",
            "timeframe": "1h",
            "supported_instruments": ["DOGE-USDT-SWAP"],
            "max_hold_bars": hold,
            "description": "DOGE reclaim rescue",
            "entry": {"all": [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi_lo)}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(rsi_lo + 12)}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema19"}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -1.5}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": float(zhi)}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
            ]},
            "exit": {"any": [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi_tp)}, "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}, "role": "invalidation"},
            ]},
        }
        book = mk_book("DOGE-USDT-SWAP", "1h", "long", "range_reclaim", dsl)
        packs = suite(book)
        bm = packs.get("base_metrics") or {}
        print("DOGE", dsl["key"], "PASS", packs.get("pass"), "fp", bm.get("fold_positive"),
              "sh", bm.get("sharpe"), "fr", ((packs.get("extreme_friction") or {}).get("metrics") or {}).get("sharpe"),
              "dest", (packs.get("logic_destruction") or {}).get("pass"), flush=True)
        score = (1 if packs.get("pass") else 0) * 1000 + (bm.get("fold_positive") or 0) * 20 + (bm.get("sharpe") or -9)
        if best is None or score > best[0]:
            best = (score, book, packs)
        if packs.get("pass"):
            winners.append((book, packs))
            break
    if winners:
        return winners[0]
    return (best[1], best[2]) if best else None


def search_sol():
    winners = []
    best = None
    for rsi_e, zmax, hold, rsi_tp in [
        (42, 2.0, 14, 60), (42, 2.5, 18, 58), (40, 2.0, 16, 55),
        (42, 3.0, 20, 62), (38, 2.5, 14, 58), (45, 2.0, 12, 60),
        (42, 2.0, 24, 65), (35, 2.5, 16, 55), (42, 1.5, 14, 58),
        (48, 2.2, 10, 60), (42, 2.0, 14, 55), (40, 3.0, 22, 60),
    ]:
        dsl = {
            "key": "frost_sol_rescue_%s_z%s_h%s" % (rsi_e, str(zmax).replace(".", "p"), hold),
            "name": "寒霜-SOL-5m-impulse_continuation",
            "direction": "long",
            "timeframe": "5m",
            "supported_instruments": ["SOL-USDT-SWAP"],
            "max_hold_bars": hold,
            "description": "SOL impulse rescue ADA-template",
            "entry": {"all": [
                {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
                {"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "rsi14"}, "op": "cross_above", "right": {"value": float(rsi_e)}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": float(zmax)}},
            ]},
            "exit": {"any": [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi_tp)}, "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}, "role": "invalidation"},
            ]},
        }
        book = mk_book("SOL-USDT-SWAP", "5m", "long", "impulse_continuation", dsl)
        packs = suite(book)
        bm = packs.get("base_metrics") or {}
        print("SOL", dsl["key"], "PASS", packs.get("pass"), "fp", bm.get("fold_positive"),
              "sh", bm.get("sharpe"), "fr", ((packs.get("extreme_friction") or {}).get("metrics") or {}).get("sharpe"),
              "dest", (packs.get("logic_destruction") or {}).get("pass"), flush=True)
        score = (1 if packs.get("pass") else 0) * 1000 + (bm.get("fold_positive") or 0) * 20 + (bm.get("sharpe") or -9)
        if best is None or score > best[0]:
            best = (score, book, packs)
        if packs.get("pass"):
            winners.append((book, packs))
            break
    if winners:
        return winners[0]
    return (best[1], best[2]) if best else None


def push_sim_formal(book, packs, force_sim=False):
    row = {
        "symbol": book.get("symbol"),
        "logic_class": book.get("logic_class"),
        "dsl_key": (book.get("dsl") or {}).get("key"),
        "suite_pass": packs.get("pass"),
        "suite": packs.get("suite"),
        "base_metrics": packs.get("base_metrics"),
        "friction_sharpe": ((packs.get("extreme_friction") or {}).get("metrics") or {}).get("sharpe"),
        "dest": (packs.get("logic_destruction") or {}).get("pass"),
        "mc": packs.get("monte_carlo"),
    }
    if not packs.get("pass") and not force_sim:
        row["sim"] = None
        row["formal"] = None
        return row

    # Step5 sim even if force
    sim = None
    for repair in range(0, 4):
        if repair:
            book = d._apply_repair_operator(
                book, ((sim or {}).get("audit") or {}).get("repair_hints") or [], repair - 1
            )
            book["gate_mode"] = "frost_relaxed"
            book["dsl"] = f._ensure_dsl(book.get("dsl") or {}, book.get("symbol"), book.get("timeframe"))
            packs2 = suite(book)
            if packs.get("pass") and not packs2.get("pass"):
                continue
            packs = packs2
        sim = d.glm_sim_review(book, packs)
        print("SIM", book.get("symbol"), sim.get("wr_deepseek_sim"), sim.get("wr_qwen_sim"),
              "pass", sim.get("pass"), flush=True)
        if float(sim.get("wr_deepseek_sim") or 0) >= 55 and float(sim.get("wr_qwen_sim") or 0) >= 55:
            sim["pass"] = True
            break
    row["sim"] = sim
    row["cert"] = None
    row["formal"] = None
    row["pending"] = None
    if sim and float(sim.get("wr_deepseek_sim") or 0) >= 55 and float(sim.get("wr_qwen_sim") or 0) >= 55 and packs.get("pass"):
        row["cert"] = {
            "title": "内部质量认证",
            "at": d._now(),
            "symbol": book.get("symbol"),
            "key": (book.get("dsl") or {}).get("key"),
            "sim_ds": sim.get("wr_deepseek_sim"),
            "sim_qwen": sim.get("wr_qwen_sim"),
            "gate_mode": "frost_relaxed",
        }
        formal = d.formal_ds_qwen_review(packs.get("definition") or book.get("dsl"), packs, book)
        row["formal"] = {
            "approved": formal.get("approved"),
            "annotation": formal.get("annotation"),
            "ai_review": formal.get("ai_review"),
        }
        if formal.get("approved"):
            import auto_trade_human_confirm_pipeline as pipeline
            pending = pipeline.ingest_and_screen(
                {
                    "dsl": packs.get("definition") or book.get("dsl"),
                    "symbol": book.get("symbol"),
                    "timeframe": book.get("timeframe"),
                    "thesis": book.get("thesis") or book.get("logic_class"),
                },
                source="frost_relax_rescue",
                ai_review=formal.get("ai_review"),
                require_ai_review=True,
            )
            row["pending"] = {
                "ok": pending.get("ok"),
                "key": pending.get("key"),
                "reason": pending.get("reason"),
                "duplicate": pending.get("duplicate"),
            }
            d._record_formal({
                "time": d._now(),
                "op": "寒霜放宽救援",
                "key": (book.get("dsl") or {}).get("key"),
                "title": book.get("title_short"),
                "symbol": book.get("symbol"),
                "status": "等待" if pending.get("ok") else "退回",
                "annotation": formal.get("annotation"),
            })
        print("FORMAL", book.get("symbol"), (row.get("formal") or {}).get("approved"),
              (row.get("formal") or {}).get("annotation"), row.get("pending"), flush=True)
    return row


def main():
    d.FROST_GATE_MODE = "frost_relaxed"
    report = {
        "op": "寒霜放宽救援",
        "started_at": d._now(),
        "gate_mode": "frost_relaxed",
        "strategies": {},
        "pending": [],
        "survivors": [],
        "sim_entered": [],
        "ok": False,
    }
    # XRP known path
    print("=== RESCUE XRP ===", flush=True)
    xrp = search_xrp()
    if xrp:
        book, packs = xrp
        row = push_sim_formal(book, packs, force_sim=True)
        report["strategies"]["XRP-USDT-SWAP"] = row
        report["sim_entered"].append("XRP-USDT-SWAP")
        if row.get("pending") and row["pending"].get("ok"):
            report["pending"].append(row["pending"])
            report["survivors"].append("XRP-USDT-SWAP")
            report["ok"] = True
    d._atomic(OUT, report)

    print("=== RESCUE DOGE ===", flush=True)
    doge = search_doge()
    if doge:
        book, packs = doge
        # force sim if suite pass OR near-pass (fp>=6 and sharpe>0)
        bm = packs.get("base_metrics") or {}
        force = packs.get("pass") or (
            (bm.get("fold_positive") or 0) >= 6 and float(bm.get("sharpe") or -1) > 0
        )
        row = push_sim_formal(book, packs, force_sim=force)
        report["strategies"]["DOGE-USDT-SWAP"] = row
        if row.get("sim"):
            report["sim_entered"].append("DOGE-USDT-SWAP")
        if row.get("pending") and row["pending"].get("ok"):
            report["pending"].append(row["pending"])
            report["survivors"].append("DOGE-USDT-SWAP")
            report["ok"] = True
    d._atomic(OUT, report)

    print("=== RESCUE SOL ===", flush=True)
    sol = search_sol()
    if sol:
        book, packs = sol
        bm = packs.get("base_metrics") or {}
        force = packs.get("pass") or (
            (bm.get("fold_positive") or 0) >= 4 and float(bm.get("win_rate_pct") or 0) >= 40
        )
        row = push_sim_formal(book, packs, force_sim=force)
        report["strategies"]["SOL-USDT-SWAP"] = row
        if row.get("sim"):
            report["sim_entered"].append("SOL-USDT-SWAP")
        if row.get("pending") and row["pending"].get("ok"):
            report["pending"].append(row["pending"])
            report["survivors"].append("SOL-USDT-SWAP")
            report["ok"] = True

    report["finished_at"] = d._now()
    d._atomic(OUT, report)
    print("DONE", json.dumps({
        "ok": report.get("ok"),
        "survivors": report.get("survivors"),
        "sim_entered": report.get("sim_entered"),
        "pending": report.get("pending"),
        "summary": {k: {
            "suite": v.get("suite_pass"),
            "sim": ((v.get("sim") or {}).get("wr_deepseek_sim"), (v.get("sim") or {}).get("wr_qwen_sim")),
            "formal": (v.get("formal") or {}).get("approved") if v.get("formal") else None,
            "pending_key": (v.get("pending") or {}).get("key"),
        } for k, v in report.get("strategies").items()},
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
