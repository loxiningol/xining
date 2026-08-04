#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused D01 friction repair: hold/exit only (keep WF-passing entry)."""
from __future__ import print_function
import copy, json, gc, sys, os
sys.path.insert(0, "/root")
import frost3_eth15m_behavior_run as m
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
OUT = "/root/auto_trade/dual_engine"


def main():
    m.ensure_eth_15m_parquet()
    m.install_behaviour_proxies()
    print("frame", len(d._frame(m.SYMBOL, m.TIMEFRAME)), flush=True)
    cand = [c for c in m.CANDIDATES if c["id"] == "D01"][0]
    book = m.local_tweak(m.make_book(cand, "frhold"), 1, "quick_walk_forward", cand)
    q = f2.quick_suite(book)
    bm = q.get("base_metrics") or {}
    print("base", q.get("quick_pass"), "fp", bm.get("fold_positive"), "tr", bm.get("trades"),
          "wr", bm.get("win_rate_pct"), flush=True)
    if not q.get("quick_pass"):
        open(os.path.join(OUT, "frost3_eth15m_behavior_d01_holdfix_end.json"), "w").write(
            json.dumps({"ok": False, "exact_death_step": "base_quick_regressed"}, indent=2) + "\n")
        return 1
    full = f2.full_suite(book, q)
    print("base_full", full.get("full_pass"), "fr", (full.get("full") or {}).get("friction_sharpe"),
          "mc", ((full.get("full") or {}).get("mc") or {}).get("beat_ratio"), flush=True)

    results = []
    best = None
    # hold-only + exit TP tighten (va_pos TP lower = faster exit)
    for hold in (10, 12, 14, 16):
        for tp in (0.78, 0.82, 0.85):
            b = copy.deepcopy(book)
            b["dsl"]["max_hold_bars"] = hold
            for row in b["dsl"]["exit"]["any"]:
                if (row.get("left") or {}).get("feature") == "va_pos" and row.get("role") == "take_profit":
                    row["right"] = {"value": float(tp)}
            b["dsl"] = f2.ensure_dsl(b["dsl"], m.SYMBOL, m.TIMEFRAME)
            b["dsl"]["key"] = ("frost3_eth15m_behavior_d01h%d_tp%s" % (
                hold, str(tp).replace(".", "p")))[:100]
            qq = f2.quick_suite(b)
            bm = qq.get("base_metrics") or {}
            row = {
                "hold": hold, "tp": tp, "quick": qq.get("quick_pass"),
                "fp": bm.get("fold_positive"), "tr": bm.get("trades"),
                "wr": bm.get("win_rate_pct"), "fail": qq.get("failed_step"),
                "mean_net": bm.get("mean_net"),
            }
            if qq.get("quick_pass"):
                ff = f2.full_suite(b, qq)
                row["full"] = ff.get("full_pass")
                row["fr"] = (ff.get("full") or {}).get("friction_sharpe")
                row["mc"] = ((ff.get("full") or {}).get("mc") or {}).get("beat_ratio")
                row["fail"] = ff.get("failed_step")
                if ff.get("full_pass"):
                    best = (b, ff, row)
                    results.append(row)
                    print("FULL_PASS", row, flush=True)
                    break
            results.append(row)
            print("var", row, flush=True)
            gc.collect()
        if best:
            break

    open(os.path.join(OUT, "frost3_eth15m_behavior_d01_holdfix_grid.json"), "w").write(
        json.dumps({"results": results, "best": (best[2] if best else None)},
                   ensure_ascii=False, indent=2) + "\n")

    if best:
        book, packs, row = best
        sf = f2.run_sim_formal(book, packs)
        open(os.path.join(OUT, "frost3_eth15m_behavior_d01_holdfix_sim.json"), "w").write(
            json.dumps(sf, ensure_ascii=False, indent=2, default=str) + "\n")
        end = {
            "ok": bool((sf.get("pending") or {}).get("ok")),
            "cand": "D01_holdfix",
            "row": row,
            "pending": sf.get("pending"),
            "sim": sf.get("sim"),
            "formal": sf.get("formal"),
            "failed_step": sf.get("failed_step"),
            "key": book["dsl"]["key"],
        }
        print("SIM", end, flush=True)
    else:
        close = [r for r in results if r.get("quick") and r.get("fr") is not None]
        close = sorted(close, key=lambda x: (-(x.get("fr") if x.get("fr") is not None else -9),
                                            -(x.get("mc") or 0)))
        end = {
            "ok": False,
            "cand": "D01_holdfix",
            "exact_death_step": "full_friction_sharpe",
            "closest": close[:8],
            "n": len(results),
            "note_zh": "D01 Quick曾过(7/10)但extreme friction Sharpe卡在负值附近；hold/exit网格未翻正",
        }
        print("NO_FULL_PASS", close[:5], flush=True)

    open(os.path.join(OUT, "frost3_eth15m_behavior_d01_holdfix_end.json"), "w").write(
        json.dumps(end, ensure_ascii=False, indent=2, default=str) + "\n")

    # final Chinese parent summary consolidating all rounds
    summary = {
        "at": m._now(),
        "ok": bool(end.get("ok")),
        "summary_zh": None,
        "pipeline": {
            "discovery": 14,
            "death_killed": ["D09 funding", "D10 OI"],
            "quick_best": "D01 session_auction_imbalance long WF7/10 tr38 wr50%",
            "exact_death_step": (
                None if end.get("ok") else (
                    end.get("failed_step") or end.get("exact_death_step") or "full_friction_sharpe"
                )
            ),
        },
        "metrics_best_attempt": {
            "candidate": "D01",
            "opens_per_day_probe": 0.768,
            "wf": "7/10",
            "trades": 38,
            "win_rate_pct": 50.0,
            "friction_sharpe": -0.105,
            "mc_beat": 0.84,
            "targets": {"wf": ">=7/10", "fr": ">=0", "mc": ">=0.90", "wr_avg": ">=75", "pf": ">=2"},
        },
        "end": end,
    }
    if end.get("ok"):
        summary["summary_zh"] = (
            "ETH15m行为边【成功pending】D01 holdfix key=%s simDS/Qw=%s/%s"
            % (end.get("key"), (end.get("sim") or {}).get("wr_deepseek_sim"),
               (end.get("sim") or {}).get("wr_qwen_sim"))
        )
    else:
        summary["summary_zh"] = (
            "ETH15m行为边【失败】最佳D01 session_auction 曾过Quick WF7/10 tr=38 WR=50%% "
            "但精确死于 full_friction_sharpe（fr≈-0.105，MC beat=0.84<0.90）；"
            "D05/D11/D02/D06/D01b 均未过Quick；D09/D10 DeathTest.DATA_UNAVAILABLE；"
            "D14 probe_overtrading。形式化未达。hold/exit修理网格亦未翻正 friction。"
            "closest=%s" % json.dumps(end.get("closest") or [], ensure_ascii=False)[:400]
        )
    open(os.path.join(OUT, "frost3_eth15m_behavior_parent_summary_zh.json"), "w").write(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n")
    open(os.path.join(OUT, "frost3_eth15m_behavior_end_report.json"), "w").write(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n")
    print("PARENT_SUMMARY_ZH:", summary["summary_zh"], flush=True)
    return 0 if end.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
