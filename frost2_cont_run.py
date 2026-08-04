#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜贰续 — CL 15m destruction rescue + new creation under WF≥7."""
from __future__ import print_function

import copy
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, "/root")
import auto_trade_dual_engine_factory as d
import frost2_action_run as f2

OUT = "/root/auto_trade/dual_engine"
# Enforce new gates
f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write(name, obj):
    path = os.path.join(OUT, name)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return path


def mk_cl(rsi=54.0, z=0.1, hold=20, tp=45.0, tag="base", entry_extra=None, exit_inv="prev_high20", ema="ema16"):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z)}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": ema}},
    ]
    if entry_extra:
        entry.extend(entry_extra)
    dsl = {
        "key": "frost2c_cl_15m_exh_%s" % tag,
        "name": "寒霜贰-CL-15m-exhaustion_fade",
        "direction": "short",
        "timeframe": "15m",
        "supported_instruments": ["CL-USDT-SWAP"],
        "max_hold_bars": int(hold),
        "description": "CL 15m exhaustion rescue dest-fix",
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(tp)},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": exit_inv},
             "role": "invalidation"},
        ]},
        "schema": "qiyu_strategy_dsl_v1",
    }
    return {
        "symbol": "CL-USDT-SWAP",
        "timeframe": "15m",
        "direction": "short",
        "logic_class": "exhaustion_fade",
        "thesis": "CL 15m exhaustion fade (寒霜贰续抢救)",
        "title": "寒霜贰-CL-15m-exhaustion_fade",
        "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"),
        "gate_mode": "frost2",
        "source": "frost2_cont_cl_rescue",
    }


DEST_FIX_PROMPT = """你是栖语策略总设计师 GLM-5.2。CL 15m exhaustion_fade 已过 WF≥7/10，但 logic_destruction(±20%参数扰动)失败。
失败细节：seed29 mean_net=-0.070 trades=5；seed47 mean_net=-0.035 trades=29；seed11勉强通过。
当前DSL入场: rsi14>54, z20>0.1, macd_stick<0, close<ema16；出场: rsi14<45 TP, close>prev_high20 invalidation；hold=20。

请给出【外科手术级】修复（10分钟内可落地），只输出JSON：
{
  "diagnosis_zh": "为何±20%扰动后期望翻负",
  "patches": [
    {"target":"entry|exit|max_hold_bars",
     "feature_or_field":"rsi14|z20|macd_stick|ema16|prev_high20|max_hold_bars|...",
     "action":"change_value|change_feature|add_condition|remove_condition|change_op",
     "from":"原值/原条件简述",
     "to":"新值或完整条件对象 {left,op,right}",
     "why":"一句"}
  ],
  "expected_effect":"如何改善destruction",
  "keep_logic_class": true
}
硬约束：仍为 short exhaustion_fade；可用特征仅 rsi14,z20,macd_stick,cci,ema*,close,prev_high20,prev_low20,atr14,h1_ema19,h1_ema53,h1_slope4；
最多改 1～3 处核心条件；不要换标的/周期。只输出JSON。
"""


def apply_patches(book, patches):
    book = copy.deepcopy(book)
    dsl = book["dsl"]
    entry = list((dsl.get("entry") or {}).get("all") or [])
    exit_any = list((dsl.get("exit") or {}).get("any") or [])
    applied = []
    for p in patches or []:
        if not isinstance(p, dict):
            continue
        target = str(p.get("target") or "")
        field = str(p.get("feature_or_field") or "")
        action = str(p.get("action") or "")
        to_val = p.get("to")
        if target == "max_hold_bars" or field == "max_hold_bars":
            try:
                dsl["max_hold_bars"] = int(to_val if not isinstance(to_val, dict) else to_val.get("value") or to_val)
                applied.append(p)
            except Exception:
                pass
            continue
        leaves = entry if target.startswith("entry") else exit_any
        if action == "add_condition" and isinstance(to_val, dict):
            # normalize to dsl leaf
            if "left" in to_val:
                leaf = to_val
            else:
                feat = to_val.get("feature") or field
                right = {}
                if "feature_right" in to_val:
                    right = {"feature": to_val["feature_right"]}
                elif "value" in to_val:
                    right = {"value": float(to_val["value"])}
                leaf = {"left": {"feature": feat}, "op": to_val.get("op") or "gt", "right": right}
                if to_val.get("role"):
                    leaf["role"] = to_val["role"]
            leaves.append(leaf)
            applied.append(p)
            continue
        if action == "remove_condition":
            leaves2 = [x for x in leaves if ((x.get("left") or {}).get("feature") != field)]
            if target.startswith("entry"):
                entry = leaves2
            else:
                exit_any = leaves2
            applied.append(p)
            continue
        # change_value / change_feature / change_op
        for leaf in leaves:
            feat = ((leaf.get("left") or {}).get("feature") or "")
            if feat != field and field not in ("*", feat):
                continue
            if action == "change_feature" and isinstance(to_val, str):
                leaf["left"]["feature"] = to_val
                applied.append(p)
            elif action in ("change_value", "change_op", "change_feature") or True:
                if isinstance(to_val, dict):
                    if "op" in to_val:
                        leaf["op"] = to_val["op"]
                    if "value" in to_val:
                        leaf.setdefault("right", {})["value"] = float(to_val["value"])
                    if "feature_right" in to_val:
                        leaf["right"] = {"feature": to_val["feature_right"]}
                    if "feature" in to_val and action == "change_feature":
                        leaf["left"]["feature"] = to_val["feature"]
                    if "role" in to_val:
                        leaf["role"] = to_val["role"]
                    applied.append(p)
                else:
                    try:
                        leaf.setdefault("right", {})["value"] = float(to_val)
                        applied.append(p)
                    except Exception:
                        if action == "change_op":
                            leaf["op"] = str(to_val)
                            applied.append(p)
            break
        if target.startswith("entry"):
            entry = leaves
        else:
            exit_any = leaves
    dsl["entry"] = {"all": entry}
    dsl["exit"] = {"any": exit_any}
    dsl["key"] = (dsl.get("key") or "frost2c_cl") + "_p%d" % len(applied)
    book["dsl"] = f2.ensure_dsl(dsl, book["symbol"], book["timeframe"])
    book["patches_applied"] = applied
    return book


def local_dest_repairs(book, cycle):
    """Deterministic surgical variants if GLM weak/empty."""
    variants = []
    # 1) raise RSI entry threshold (sparser, less fragile)
    variants.append(mk_cl(rsi=56, z=0.1, hold=20, tp=45, tag="c%d_rsi56" % cycle))
    # 2) add h1 downtrend filter (stabilize regime)
    variants.append(mk_cl(
        rsi=54, z=0.1, hold=20, tp=45, tag="c%d_h1down" % cycle,
        entry_extra=[{"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}}]))
    # 3) soften invalidation to ema8 (less catastrophic on perturb)
    variants.append(mk_cl(rsi=54, z=0.15, hold=18, tp=42, tag="c%d_inv_ema8" % cycle, exit_inv="ema8"))
    # 4) tighten z + shorter hold
    variants.append(mk_cl(rsi=55, z=0.3, hold=16, tp=45, tag="c%d_z03" % cycle))
    # 5) add cci filter
    variants.append(mk_cl(
        rsi=54, z=0.1, hold=20, tp=45, tag="c%d_cci" % cycle,
        entry_extra=[{"left": {"feature": "cci"}, "op": "gt", "right": {"value": 50.0}}]))
    # 6) close < ema8 instead of ema16
    variants.append(mk_cl(rsi=54, z=0.1, hold=20, tp=45, tag="c%d_ema8" % cycle, ema="ema8"))
    return variants


def glm_dest_fix(book, packs, cycle):
    payload = {
        "cycle": cycle,
        "dsl": book.get("dsl"),
        "base_metrics": packs.get("base_metrics"),
        "logic_destruction": packs.get("logic_destruction"),
        "quick": packs.get("quick"),
    }
    res = d._ai_json("glm", DEST_FIX_PROMPT, payload, max_tokens=1200, temperature=0.2)
    return res


def push_full_sim_formal(book, packs):
    packs = f2.full_suite(book, packs)
    row = {
        "key": (book.get("dsl") or {}).get("key"),
        "symbol": book.get("symbol"),
        "timeframe": book.get("timeframe"),
        "quick_pass": True,
        "full_pass": packs.get("full_pass"),
        "full": packs.get("full"),
        "metrics": packs.get("base_metrics"),
        "failed_step": packs.get("failed_step"),
    }
    if not packs.get("full_pass"):
        return row
    sf = f2.run_sim_formal(book, packs)
    row["sim"] = sf.get("sim")
    row["formal"] = sf.get("formal")
    row["pending"] = sf.get("pending")
    row["failed_step"] = sf.get("failed_step")
    row["ok"] = not sf.get("failed_step")
    return row


def rescue_cl():
    print("[cont] Task1 CL 15m destruction rescue, WF>=7", flush=True)
    book = mk_cl(tag="base")
    hist = []
    for cycle in range(1, 4):
        print("[cont] CL cycle", cycle, (book.get("dsl") or {}).get("key"), flush=True)
        packs = f2.quick_suite(book)
        bm = packs.get("base_metrics") or {}
        dest = packs.get("logic_destruction") or {}
        hist.append({
            "cycle": cycle,
            "key": (book.get("dsl") or {}).get("key"),
            "quick_pass": packs.get("quick_pass"),
            "fp": bm.get("fold_positive"),
            "trades": bm.get("trades"),
            "wr": bm.get("win_rate_pct"),
            "sharpe": bm.get("sharpe"),
            "dest": dest.get("pass"),
            "dest_rows": dest.get("rows"),
            "failed_step": packs.get("failed_step"),
        })
        write("frost2_cont_cl_cycle%d.json" % cycle, hist[-1])
        print("[cont] fp", bm.get("fold_positive"), "dest", dest.get("pass"),
              "quick", packs.get("quick_pass"), flush=True)

        if packs.get("quick_pass"):
            print("[cont] quick PASS — full/sim/formal", flush=True)
            result = push_full_sim_formal(book, packs)
            result["history"] = hist
            result["rescue_cycles"] = cycle
            write("frost2_cont_cl_rescue.json", result)
            return result

        # Need WF still ok; if WF failed under some patch, try other variants
        wf_ok = bool((packs.get("quick") or {}).get("walk_forward_8of10") or
                     (int(bm.get("fold_positive") or 0) >= 7 and int(bm.get("folds") or 0) >= 10))
        # ask GLM
        ai = glm_dest_fix(book, packs, cycle)
        write("frost2_cont_cl_glm_fix_c%d.json" % cycle, {
            "ok": ai.get("ok"), "error": ai.get("error"),
            "latency_sec": ai.get("latency_sec"),
            "parsed": ai.get("parsed"),
            "raw_preview": ai.get("raw_preview"),
        })
        candidates = []
        parsed = ai.get("parsed") if ai.get("ok") else None
        if parsed and parsed.get("patches"):
            try:
                candidates.append(apply_patches(book, parsed.get("patches")))
            except Exception as exc:
                print("[cont] apply_patches err", exc, flush=True)
        candidates.extend(local_dest_repairs(book, cycle))

        # pick first that recovers dest while keeping wf>=7
        picked = None
        for cand in candidates:
            p2 = f2.quick_suite(cand)
            bm2 = p2.get("base_metrics") or {}
            print("[cont] try", (cand.get("dsl") or {}).get("key"),
                  "fp", bm2.get("fold_positive"),
                  "dest", (p2.get("logic_destruction") or {}).get("pass"),
                  "quick", p2.get("quick_pass"), flush=True)
            if p2.get("quick_pass"):
                picked = (cand, p2)
                break
            # keep improving dest even if not full quick yet
            if ((p2.get("logic_destruction") or {}).get("pass")
                    and int(bm2.get("fold_positive") or 0) >= 7
                    and int(bm2.get("folds") or 0) >= 10):
                # should have been quick_pass — recheck
                picked = (cand, p2)
                break
        if not picked:
            # choose best by (dest, fp, sharpe)
            scored = []
            for cand in candidates:
                p2 = f2.quick_suite(cand)
                bm2 = p2.get("base_metrics") or {}
                scored.append((
                    1 if (p2.get("logic_destruction") or {}).get("pass") else 0,
                    int(bm2.get("fold_positive") or 0),
                    float(bm2.get("sharpe") or -99),
                    cand, p2,
                ))
            scored.sort(reverse=True)
            if scored:
                book = scored[0][3]
            continue
        book, packs = picked[0], picked[1]
        if packs.get("quick_pass"):
            result = push_full_sim_formal(book, packs)
            result["history"] = hist
            result["rescue_cycles"] = cycle
            write("frost2_cont_cl_rescue.json", result)
            return result

    # abandon
    out = {
        "ok": False,
        "failed_step": "logic_destruction_max_cycles",
        "history": hist,
        "key": (book.get("dsl") or {}).get("key"),
        "symbol": "CL-USDT-SWAP",
        "timeframe": "15m",
    }
    write("frost2_cont_cl_rescue.json", out)
    return out


def books_from_postmortem():
    report = {}
    for name in ("frost2_root_cause_report.json", "frost2_postmortem.json"):
        path = os.path.join(OUT, name)
        if os.path.exists(path):
            data = json.load(open(path))
            report = data.get("report") or data
            if report.get("new_directions") or report.get("trap_checklist"):
                break
    adapt = {}
    ap = os.path.join(OUT, "frost2_direction_adapt.json")
    if os.path.exists(ap):
        adapt = (json.load(open(ap)).get("parsed") or {})
    books = f2.books_from_postmortem(report, adapt=adapt)
    # ensure gate 7 path
    for b in books:
        b["gate_mode"] = "frost2"
    return report, books


def task2_new(skip_keys=None):
    print("[cont] Task2 new creation under WF>=7", flush=True)
    report, books = books_from_postmortem()
    write("frost2_cont_task2_books.json", {
        "at": now(),
        "n": len(books),
        "books": [{"symbol": b.get("symbol"), "tf": b.get("timeframe"),
                   "logic": b.get("logic_class"), "key": (b.get("dsl") or {}).get("key")}
                  for b in books],
        "traps": (report or {}).get("trap_checklist"),
    })
    # GLM audit soft
    audit = f2.glm_audit_books(report, books)
    write("frost2_cont_task2_audit.json", {
        "ok": audit.get("ok"), "parsed": audit.get("parsed"), "error": audit.get("error")
    })
    results = []
    pending = []
    for book in books:
        if f2.avoided(book["symbol"], book["timeframe"]):
            results.append({"key": (book.get("dsl") or {}).get("key"),
                            "ok": False, "failed_step": "avoid_live_overlap",
                            "symbol": book["symbol"], "tf": book["timeframe"]})
            continue
        # process with updated gate
        try:
            r = f2.process_book(book, report)
        except Exception as exc:
            r = {"book": book, "ok": False, "failed_step": "exception", "error": str(exc)}
        item = {
            "key": ((r.get("book") or {}).get("dsl") or {}).get("key"),
            "symbol": (r.get("book") or {}).get("symbol"),
            "timeframe": (r.get("book") or {}).get("timeframe"),
            "logic": (r.get("book") or {}).get("logic_class"),
            "ok": r.get("ok"),
            "failed_step": r.get("failed_step"),
            "quick": (r.get("packs") or {}).get("quick"),
            "full": (r.get("packs") or {}).get("full"),
            "metrics": (r.get("packs") or {}).get("base_metrics"),
            "sim": ((r.get("sim_formal") or {}).get("sim")),
            "formal": ((r.get("sim_formal") or {}).get("formal")),
            "pending": ((r.get("sim_formal") or {}).get("pending")),
            "history": r.get("history"),
        }
        results.append(item)
        if (item.get("pending") or {}).get("ok"):
            pending.append(item["pending"].get("key"))
        write("frost2_cont_task2_partial.json", {"results": results, "pending": pending, "at": now()})
        if len(pending) >= 2:
            break
    out = {"results": results, "pending_keys": pending, "ok": len(pending) >= 1}
    write("frost2_cont_task2.json", out)
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    # persist gate note
    write("frost2_cont_gates.json", {
        "at": now(),
        "quick_wf_pos": 7,
        "logic_destruction": True,
        "mc_beat_shuffles": 0.90,
        "friction_sharpe_ge": 0.0,
        "note": "寒霜贰续 user mandate",
    })
    status = {
        "op": "寒霜贰续",
        "started_at": now(),
        "gates": {"wf": ">=7/10", "dest": True, "mc90": True, "friction_ge0": True},
    }
    write("frost2_cont_status.json", status)

    cl = rescue_cl()
    status["cl_rescue"] = {
        "ok": cl.get("ok"),
        "failed_step": cl.get("failed_step"),
        "key": cl.get("key"),
        "pending": cl.get("pending"),
        "sim": cl.get("sim"),
        "formal": {k: (cl.get("formal") or {}).get(k)
                   for k in ("approved", "annotation")} if cl.get("formal") else None,
        "full": cl.get("full"),
        "metrics": cl.get("metrics"),
        "rescue_cycles": cl.get("rescue_cycles"),
    }
    write("frost2_cont_status.json", status)

    t2 = task2_new()
    status["task2"] = {
        "pending_keys": t2.get("pending_keys"),
        "results": [
            {"key": r.get("key"), "symbol": r.get("symbol"), "tf": r.get("timeframe"),
             "ok": r.get("ok"), "failed_step": r.get("failed_step"),
             "pending": (r.get("pending") or {}).get("key") if r.get("pending") else None}
            for r in (t2.get("results") or [])
        ],
    }
    pending = []
    if (cl.get("pending") or {}).get("ok"):
        pending.append(cl["pending"].get("key"))
    pending.extend(t2.get("pending_keys") or [])
    status["pending_keys"] = pending
    status["ok_min_cl"] = bool((cl.get("pending") or {}).get("ok"))
    status["ok_expected_cl_plus_one"] = status["ok_min_cl"] and len(pending) >= 2
    status["finished_at"] = now()
    write("frost2_cont_status.json", status)
    # merge into frost2_run
    try:
        run = json.load(open(os.path.join(OUT, "frost2_run.json")))
    except Exception:
        run = {"op": "寒霜贰"}
    run["cont"] = status
    run["pending_keys"] = pending
    run["phase"] = "frost2_cont"
    run["finished_at"] = now()
    write("frost2_run.json", run)
    print(json.dumps({
        "cl_ok": status["ok_min_cl"],
        "pending_keys": pending,
        "cl_failed_step": cl.get("failed_step"),
        "task2": status["task2"]["results"],
    }, ensure_ascii=False, indent=2), flush=True)
    return 0 if status["ok_min_cl"] else 1


if __name__ == "__main__":
    sys.exit(main())
