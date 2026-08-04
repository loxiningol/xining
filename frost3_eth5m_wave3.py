#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETH5m wave3: random/grid search + pipeline WITHOUT sketch rebuild corruption."""
from __future__ import print_function

import copy
import json
import os
import random
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


def eval_dsl(dsl):
    import auto_trade_strategy_dsl as dsl_mod
    definition = dsl_mod.validate_strategy(dsl)
    base = d._backtest(definition, SYMBOL, TF, "observed_base")
    return d._metrics_from_trades(base.get("trades") or []), definition


def score(bm):
    tr = int(bm.get("trades") or 0)
    fp = int(bm.get("fold_positive") or 0)
    folds = int(bm.get("folds") or 0)
    wr = float(bm.get("win_rate_pct") or 0)
    sh = float(bm.get("sharpe") or -9)
    mn = float(bm.get("mean_net") or 0)
    oos = float(bm.get("oos_profit") or 0)
    s = 0.0
    if tr >= 10 and folds >= 10 and fp >= 7:
        s += 1000
    s += fp * 80
    s += sh * 15
    s += wr * 1.5
    s += mn * 200
    s += max(0.0, oos) * 30
    if 12 <= tr <= 60:
        s += 40
    elif tr < 8:
        s -= 200
    elif tr > 150:
        s -= 40
    return s


def build_variants(rng):
    """Generate diverse non-banned ETH5m logics."""
    variants = []

    # Template A: crowded_ribbon_fade short (fade failed r1 long signal)
    for _ in range(40):
        rsi_lo = rng.uniform(50, 58)
        rsi_hi = rsi_lo + rng.uniform(8, 16)
        z_lo = rng.uniform(0.0, 0.5)
        z_hi = z_lo + rng.uniform(0.8, 1.8)
        slope = rng.uniform(0.0002, 0.001)
        hold = int(rng.choice([10, 12, 16, 20, 24]))
        tp = rng.uniform(40, 50)
        entry = [
            leaf("h1_ema19", "gt", "h1_ema53"),
            leaf("h1_slope4", "gt", slope),
            leaf("close", "gt", "ema21"),
            leaf("ema8", "gt", "ema21"),
            leaf("rsi14", "gt", rsi_lo),
            leaf("rsi14", "lt", rsi_hi),
            leaf("macd_stick", "gt", 0.0),
            leaf("z20", "gt", z_lo),
            leaf("z20", "lt", z_hi),
        ]
        exits = [leaf("rsi14", "lt", tp), leaf("close", "gt", "prev_high20")]
        variants.append(("crowded_ribbon_fade", "short", entry, exits, hold))

    # Template B: weak_thrust_reject short (not exhaustion — mid RSI, already below ema)
    for _ in range(30):
        rsi_lo = rng.uniform(46, 54)
        rsi_hi = rsi_lo + rng.uniform(6, 14)
        z_lo = rng.uniform(-0.2, 0.3)
        z_hi = z_lo + rng.uniform(0.6, 1.4)
        hold = int(rng.choice([12, 16, 20, 28]))
        tp = rng.uniform(35, 45)
        entry = [
            leaf("h1_ema19", "lt", "h1_ema53"),
            leaf("close", "lt", "ema21"),
            leaf("rsi14", "gt", rsi_lo),
            leaf("rsi14", "lt", rsi_hi),
            leaf("macd_stick", "lt", 0.0),
            leaf("z20", "gt", z_lo),
            leaf("z20", "lt", z_hi),
        ]
        if rng.random() < 0.5:
            entry.append(leaf("ema8", "lt", "ema21"))
        exits = [leaf("rsi14", "lt", tp), leaf("close", "gt", "ema16")]
        variants.append(("weak_thrust_reject", "short", entry, exits, hold))

    # Template C: z_stretch_fade short (z stretch in bear regime; rsi band avoids classic exhaustion 58+)
    for _ in range(30):
        z_lo = rng.uniform(0.8, 1.5)
        z_hi = z_lo + rng.uniform(0.5, 1.5)
        rsi_lo = rng.uniform(48, 56)  # deliberately not requiring >58 exhaustion
        rsi_hi = min(70.0, rsi_lo + rng.uniform(8, 14))
        hold = int(rng.choice([10, 14, 18, 22]))
        tp = rng.uniform(38, 48)
        entry = [
            leaf("h1_ema19", "lt", "h1_ema53"),
            leaf("z20", "gt", z_lo),
            leaf("z20", "lt", z_hi),
            leaf("rsi14", "gt", rsi_lo),
            leaf("rsi14", "lt", rsi_hi),
            leaf("macd_stick", "lt", 0.0),
            leaf("close", "lt", "ema21"),
        ]
        exits = [leaf("rsi14", "lt", tp), leaf("close", "gt", "prev_high20")]
        variants.append(("z_stretch_fade", "short", entry, exits, hold))

    # Template D: dip_absorb_long (H1 bull, mild dip, NOT reclaim of prev_low)
    for _ in range(30):
        z_lo = rng.uniform(-1.8, -0.8)
        z_hi = z_lo + rng.uniform(0.4, 1.0)
        rsi_lo = rng.uniform(30, 40)
        rsi_hi = rsi_lo + rng.uniform(8, 14)
        hold = int(rng.choice([12, 16, 20, 28]))
        tp = rng.uniform(52, 62)
        entry = [
            leaf("h1_ema19", "gt", "h1_ema53"),
            leaf("h1_slope4", "gt", rng.uniform(0.0002, 0.0008)),
            leaf("z20", "gt", z_lo),
            leaf("z20", "lt", z_hi),
            leaf("rsi14", "gt", rsi_lo),
            leaf("rsi14", "lt", rsi_hi),
            leaf("close", "gt", "ema53"),
            leaf("macd_stick", "gt", rng.choice([-2.0, 0.0])),
        ]
        exits = [leaf("rsi14", "gt", tp), leaf("close", "lt", "ema53")]
        variants.append(("dip_absorb_hold", "long", entry, exits, hold))

    # Template E: sparse slope short stronger
    for _ in range(25):
        slope = -rng.uniform(0.0005, 0.002)
        rsi_hi = rng.uniform(35, 45)
        rsi_lo = rsi_hi - rng.uniform(8, 14)
        hold = int(rng.choice([16, 24, 32]))
        entry = [
            leaf("h1_ema19", "lt", "h1_ema53"),
            leaf("h1_slope4", "lt", slope),
            leaf("close", "lt", "ema8"),
            leaf("rsi14", "lt", rsi_hi),
            leaf("rsi14", "gt", rsi_lo),
            leaf("macd_stick", "lt", 0.0),
            leaf("z20", "lt", -rng.uniform(0.1, 0.6)),
        ]
        exits = [leaf("rsi14", "lt", rng.uniform(20, 28)), leaf("close", "gt", "ema21")]
        variants.append(("sparse_slope_short", "short", entry, exits, hold))

    return variants


def search(n_eval=120, seed=42):
    rng = random.Random(seed)
    variants = build_variants(rng)
    rng.shuffle(variants)
    variants = variants[:n_eval]
    best = None
    rows = []
    for i, (logic, direction, entry, exits, hold) in enumerate(variants):
        exit_any = []
        for j, e in enumerate(exits):
            ee = copy.deepcopy(e)
            ee["role"] = "take_profit" if j == 0 else "invalidation"
            exit_any.append(ee)
        dsl = f2.ensure_dsl({
            "key": "frost3_eth5m_w3_%s_%d" % (logic[:16], i),
            "name": "寒霜叁-ETH-5m-%s" % logic,
            "direction": direction,
            "timeframe": TF,
            "supported_instruments": [SYMBOL],
            "max_hold_bars": hold,
            "description": "wave3 search %s" % logic,
            "entry": {"all": entry},
            "exit": {"any": exit_any},
            "schema": "qiyu_strategy_dsl_v1",
        }, SYMBOL, TF)
        try:
            bm, _ = eval_dsl(dsl)
            sc = score(bm)
            row = {
                "i": i, "logic": logic, "direction": direction, "score": sc,
                "tr": bm.get("trades"), "fp": bm.get("fold_positive"),
                "folds": bm.get("folds"), "wr": bm.get("win_rate_pct"),
                "sharpe": bm.get("sharpe"), "mean_net": bm.get("mean_net"),
                "oos": bm.get("oos_profit"),
                "wf_ok": (
                    int(bm.get("trades") or 0) >= 10
                    and int(bm.get("folds") or 0) >= 10
                    and int(bm.get("fold_positive") or 0) >= 7
                ),
                "dsl": dsl,
            }
            rows.append(row)
            if best is None or row["score"] > best["score"]:
                best = row
                print("[best]", logic, direction, "fp", row["fp"], "tr", row["tr"],
                      "wr", round(float(row["wr"] or 0), 2), "sh", row["sharpe"],
                      "mn", round(float(row["mean_net"] or 0), 4), "sc", round(sc, 1),
                      flush=True)
            elif i % 10 == 0:
                print("[prog]", i, "/", len(variants), "cur_best_fp",
                      best.get("fp"), flush=True)
        except Exception as exc:
            print("[err]", i, logic, exc, flush=True)

    rows.sort(key=lambda r: r["score"], reverse=True)
    out = {
        "at": _now(),
        "n": len(rows),
        "top10": [{k: v for k, v in r.items() if k != "dsl"} for r in rows[:10]],
        "best": {k: v for k, v in rows[0].items() if k != "dsl"} if rows else None,
    }
    if rows:
        out["best_dsl"] = rows[0]["dsl"]
    open(os.path.join(OUT, "%s_wave3_search.json" % PREFIX), "w").write(
        json.dumps(out, ensure_ascii=False, indent=2, default=str) + "\n")
    print("SEARCH_DONE best", out.get("best"), flush=True)
    return rows


def process_fixed_book(book):
    """Run gates with NO sketch rewrite (avoid from_dsl corruption)."""
    hist = []
    report = {"trap_checklist": (pipe.load_eth_micro_packet().get("cl_archive") or {}).get("trap_checklist")}
    pipe._write("%s_book.json" % PREFIX, book)
    pipe._write("%s_hypothesis_book.json" % PREFIX, {
        "title": "策略逻辑假设书",
        "symbol": SYMBOL,
        "timeframe": TF,
        "direction": book["direction"],
        "logic_class": book["logic_class"],
        "micro_behavior": book.get("micro_behavior"),
        "causal_entry": "screened DSL entry confluence (wave3 search)",
        "causal_exit": "rsi TP + structural invalidation",
        "how_avoids_cl_defects": book.get("avoid_from_postmortem"),
        "wave": 3,
        "note": "skip sketch rebuild",
    })

    packs = None
    for qtry in range(pipe.MAX_QUICK_REPAIR + 1):
        pipe._upd(stage="quick", attempt=qtry, wave=3)
        packs = f2.quick_suite(book)
        pipe._write("%s_quick_t%d.json" % (PREFIX, qtry), packs)
        hist.append({
            "stage": "quick", "try": qtry, "pass": packs.get("quick_pass"),
            "failed_step": packs.get("failed_step"),
            "fp": (packs.get("base_metrics") or {}).get("fold_positive"),
            "folds": (packs.get("base_metrics") or {}).get("folds"),
            "tr": (packs.get("base_metrics") or {}).get("trades"),
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
        })
        print("[quick]", qtry, packs.get("quick_pass"), packs.get("failed_step"),
              hist[-1], flush=True)
        if packs.get("quick_pass"):
            break
        if qtry >= pipe.MAX_QUICK_REPAIR:
            return {
                "ok": False,
                "failed_step": packs.get("failed_step") or "quick_exhausted",
                "reason": "Quick failed after %d repairs" % pipe.MAX_QUICK_REPAIR,
                "metrics": packs.get("base_metrics"),
                "hist": hist, "book": book,
                "death_cause_zh": "Quick耗尽：%s；fp=%s/%s trades=%s dest=%s" % (
                    packs.get("failed_step"),
                    (packs.get("base_metrics") or {}).get("fold_positive"),
                    (packs.get("base_metrics") or {}).get("folds"),
                    (packs.get("base_metrics") or {}).get("trades"),
                    (packs.get("logic_destruction") or {}).get("pass"),
                ),
            }
        book = pipe.local_tweak(book, qtry + 1, packs.get("failed_step"))
        repaired, _ai = pipe.glm_repair(book, packs, packs.get("failed_step"), "quick")
        if repaired:
            # keep instrument lock; do not accept symbol/tf changes
            repaired["symbol"] = SYMBOL
            repaired["timeframe"] = TF
            repaired["logic_class"] = book["logic_class"]
            repaired["dsl"]["supported_instruments"] = [SYMBOL]
            repaired["dsl"]["timeframe"] = TF
            book = repaired
        hist.append({"stage": "quick_repair", "try": qtry, "ok": bool(repaired)})
        pipe._write("%s_book.json" % PREFIX, book)

    for ftry in range(pipe.MAX_FULL_REPAIR + 1):
        pipe._upd(stage="full", attempt=ftry, wave=3)
        packs = f2.full_suite(book, packs)
        pipe._write("%s_full_t%d.json" % (PREFIX, ftry), packs)
        hist.append({
            "stage": "full", "try": ftry, "pass": packs.get("full_pass"),
            "failed_step": packs.get("failed_step"),
            "friction": (packs.get("full") or {}).get("friction_sharpe"),
            "mc": ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
        })
        print("[full]", ftry, packs.get("full_pass"), packs.get("failed_step"), hist[-1], flush=True)
        if packs.get("full_pass"):
            break
        if ftry >= pipe.MAX_FULL_REPAIR:
            return {
                "ok": False,
                "failed_step": packs.get("failed_step") or "full_exhausted",
                "reason": "Full failed after %d repairs" % pipe.MAX_FULL_REPAIR,
                "full": packs.get("full"), "hist": hist, "book": book,
                "death_cause_zh": "Full耗尽：%s；friction=%s mc=%s" % (
                    packs.get("failed_step"),
                    (packs.get("full") or {}).get("friction_sharpe"),
                    ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
                ),
            }
        book = pipe.local_tweak(book, ftry + 1, packs.get("failed_step"))
        repaired, _ai = pipe.glm_repair(book, packs, packs.get("failed_step"), "full")
        if repaired:
            repaired["symbol"] = SYMBOL
            repaired["timeframe"] = TF
            book = repaired
        q2 = f2.quick_suite(book)
        hist.append({"stage": "re_quick_after_full_repair", "pass": q2.get("quick_pass"),
                     "failed_step": q2.get("failed_step")})
        if not q2.get("quick_pass"):
            packs = q2
            packs["full_pass"] = False
            packs["failed_step"] = q2.get("failed_step") or "quick_regressed"
            continue
        packs = q2

    for stry in range(pipe.MAX_SIM_REPAIR + 1):
        pipe._upd(stage="sim_formal", attempt=stry, wave=3)
        sf = f2.run_sim_formal(book, packs)
        pipe._write("%s_sim_formal_t%d.json" % (PREFIX, stry), sf)
        hist.append({
            "stage": "sim_formal", "try": stry,
            "failed_step": sf.get("failed_step"),
            "sim_ds": (sf.get("sim") or {}).get("wr_deepseek_sim"),
            "sim_qw": (sf.get("sim") or {}).get("wr_qwen_sim"),
            "formal_approved": (sf.get("formal") or {}).get("approved"),
            "pending": (sf.get("pending") or {}).get("key"),
        })
        print("[sim]", stry, hist[-1], flush=True)
        if (sf.get("pending") or {}).get("ok"):
            return {
                "ok": True, "pending": sf.get("pending"), "sim": sf.get("sim"),
                "formal": sf.get("formal"), "hist": hist, "book": book,
            }
        fs = sf.get("failed_step")
        if fs in ("formal_review", "pending_ingest"):
            return {
                "ok": False, "failed_step": fs,
                "reason": (sf.get("formal") or {}).get("reason") or (
                    sf.get("pending") or {}).get("reason"),
                "sim": sf.get("sim"), "formal": sf.get("formal"),
                "hist": hist, "book": book,
                "death_cause_zh": "正式复核/入库失败：" + str(fs),
            }
        if stry >= pipe.MAX_SIM_REPAIR:
            return {
                "ok": False, "failed_step": fs or "sim_exhausted",
                "reason": "Sim/formal failed after %d repairs" % pipe.MAX_SIM_REPAIR,
                "sim": sf.get("sim"), "hist": hist, "book": book,
                "death_cause_zh": "Sim耗尽：DS=%s Qwen=%s step=%s" % (
                    (sf.get("sim") or {}).get("wr_deepseek_sim"),
                    (sf.get("sim") or {}).get("wr_qwen_sim"), fs,
                ),
            }
        book = pipe.local_tweak(book, stry + 1, "sim")
        repaired, _ai = pipe.glm_repair(book, packs, "sim_review", "sim")
        if repaired:
            book = repaired
        q3 = f2.quick_suite(book)
        if not q3.get("quick_pass"):
            hist.append({"stage": "sim_repair_quick_fail", "failed": q3.get("failed_step")})
            continue
        packs = f2.full_suite(book, q3)
        if not packs.get("full_pass"):
            hist.append({"stage": "sim_repair_full_fail", "failed": packs.get("failed_step")})
            continue

    return {"ok": False, "failed_step": "unknown_exhausted", "hist": hist, "book": book}


def main():
    print("[wave3] START", _now(), flush=True)
    rows = search(n_eval=100, seed=20260726)
    if not rows:
        raise RuntimeError("no search rows")

    # Prefer wf_ok; else best score with fp>=5 and mean_net>0; else top score
    pick = None
    for r in rows:
        if r.get("wf_ok"):
            pick = r
            break
    if pick is None:
        for r in rows:
            if int(r.get("fp") or 0) >= 5 and float(r.get("mean_net") or 0) > 0:
                pick = r
                break
    if pick is None:
        pick = rows[0]

    print("[wave3] PICK", pick.get("logic"), pick.get("direction"),
          {k: pick.get(k) for k in ("tr", "fp", "wr", "sharpe", "mean_net", "wf_ok")},
          flush=True)

    # If still hopeless (fp<4 or mean_net<<0), archive search failure and stop
    if int(pick.get("fp") or 0) < 4 and float(pick.get("mean_net") or 0) <= 0:
        end = {
            "at": _now(),
            "op": "寒霜叁ETH5m-wave3",
            "ok": False,
            "failed_step": "search_no_viable",
            "death_cause_zh": (
                "wave3搜索100组后无可行ETH5m方向：最优%s/%s fp=%s wr=%.2f sharpe=%s mean_net=%s；"
                "r1斜率多头与wave2均已在quick_walk_forward死亡。"
                % (pick.get("logic"), pick.get("direction"), pick.get("fp"),
                   float(pick.get("wr") or 0), pick.get("sharpe"), pick.get("mean_net"))
            ),
            "search_best": {k: pick.get(k) for k in pick if k != "dsl"},
            "r1_archive": "/root/auto_trade/dual_engine/archive/frost3_eth5m_h1_slope_persistence_r1",
            "r2_archive": "/root/auto_trade/dual_engine/archive/frost3_eth5m_zscore_mean_revert_long",
        }
        arch = os.path.join(OUT, "archive", "frost3_eth5m_wave3_search_exhausted")
        os.makedirs(arch, exist_ok=True)
        open(os.path.join(arch, "DEATH.json"), "w").write(
            json.dumps(end, ensure_ascii=False, indent=2, default=str) + "\n")
        open(os.path.join(arch, "README.json"), "w").write(json.dumps({
            "title": "ETH5m wave3 search exhausted",
            "failed_step": "search_no_viable",
        }, ensure_ascii=False, indent=2) + "\n")
        end["archive_path"] = arch
        summary = (
            "失败：ETH5m 停在 search_no_viable；死因=%s；归档=%s"
            % (end["death_cause_zh"], arch)
        )
        pipe._write("%s_end_report.json" % PREFIX, end)
        pipe._write("%s_parent_summary.json" % PREFIX, {"summary_zh": summary, "end": end})
        print("=== ETH5m PARENT ===", summary, flush=True)
        return 1

    book = {
        "title": "寒霜叁ETH5m-wave3-%s" % pick["logic"],
        "symbol": SYMBOL,
        "timeframe": TF,
        "direction": pick["direction"],
        "logic_class": pick["logic"],
        "thesis": "wave3 screened DSL; logic=%s" % pick["logic"],
        "micro_behavior": "parameter search survivor",
        "entry_sketch": None,  # critical: do not rebuild from sketch
        "exit_sketch": None,
        "avoid_from_postmortem": "non-exhaustion/reclaim/breakout; CL-safe trade count",
        "diff_vs_live": "orthogonal to ADA/LTC/NG/XRP live families",
        "dsl": pick["dsl"],
        "gate_mode": "frost2",
        "source": "frost3_eth5m_wave3",
        "dir_rank": 3,
    }
    pipe._write("%s_chosen.json" % PREFIX, {
        "logic_class": pick["logic"], "direction": pick["direction"],
        "screen": {k: pick.get(k) for k in ("tr", "fp", "wr", "sharpe", "mean_net", "wf_ok", "score")},
        "wave": 3,
    })

    result = process_fixed_book(book)
    end = {
        "at": _now(),
        "op": "寒霜叁ETH5m-wave3",
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
        "search_best": {k: pick.get(k) for k in ("logic", "direction", "tr", "fp", "wr", "sharpe", "mean_net", "wf_ok")},
    }
    if not result.get("ok"):
        arch = pipe.archive_fail(result, result.get("book") or book)
        end["archive_path"] = arch
        print("[wave3] ARCHIVED", result.get("failed_step"), arch, flush=True)
        summary = (
            "失败：ETH5m wave3 停在 %s；死因=%s；归档=%s"
            % (end.get("failed_step"), end.get("death_cause_zh"), end.get("archive_path"))
        )
    else:
        print("[wave3] PENDING", end.get("pending_key"), flush=True)
        summary = (
            "成功：ETH5m %s/%s 已进pending key=%s；Sim DS=%s Qwen=%s"
            % (end.get("logic_class"), end.get("direction"), end.get("pending_key"),
               (end.get("sim") or {}).get("ds"), (end.get("sim") or {}).get("qw"))
        )
    pipe._write("%s_end_report.json" % PREFIX, end)
    pipe._write("%s_parent_summary.json" % PREFIX, {"summary_zh": summary, "end": end})
    print("=== ETH5m PARENT ===", summary, flush=True)
    return 0 if end.get("ok") else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
