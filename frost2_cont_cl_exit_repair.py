#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CL 15m extreme-friction blotter + exit-only repair (max 2 cycles)."""
from __future__ import print_function

import copy
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

try:
    import auto_trade_ai_ecosystem as eco
except Exception:
    eco = None

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
OUT = "/root/auto_trade/dual_engine"


def cl_book(exit_extra=None, inv="prev_high20", hold=12, tp=45, tag="cci_h12"):
    """Entry fixed: rsi>54, z>0.1, macd<0, close<ema16, cci>50."""
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 54.0}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.1}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
        {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 50.0}},
    ]
    exit_any = [
        {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(tp)}, "role": "take_profit"},
        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": inv}, "role": "invalidation"},
    ]
    if exit_extra:
        exit_any.extend(exit_extra)
    dsl = {
        "key": "frost2c_cl_15m_exit_%s" % tag,
        "name": "寒霜贰-CL-15m-exhaustion_fade",
        "direction": "short",
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "max_hold_bars": int(hold),
    }
    return {
        "symbol": "CL-USDT-SWAP", "timeframe": "15m", "direction": "short",
        "logic_class": "exhaustion_fade",
        "thesis": "exit-only friction repair",
        "title": "寒霜贰-CL-15m-exhaustion_fade",
        "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"),
        "gate_mode": "frost2", "source": "cl_exit_repair",
    }


def friction_params(friction_name, slip_mult=1.0, latency_extra=0.0):
    row = {
        "friction_name": friction_name,
        "slip_mult": slip_mult,
        "latency_extra": latency_extra,
        "engine_stop_loss_pct": d.STOP_LOSS_PCT,
        "engine_leverage": d.LEVERAGE,
        "engine_account_stop_approx": round(d.STOP_LOSS_PCT * d.LEVERAGE, 6),
    }
    if eco is not None:
        try:
            fr = eco._friction_scenario("CL-USDT-SWAP", friction_name)
            slip = float(fr.get("slippage_rate_per_side") or 0.0002) * float(slip_mult)
            lat = float(fr.get("latency_rate_per_side") or 0) + float(latency_extra)
            fee = float(fr.get("fee_rate_per_side") or 0)
            row.update({
                "scenario_raw": fr,
                "fee_rate_per_side": fee,
                "slippage_rate_per_side_effective": slip,
                "latency_rate_per_side_effective": lat,
                "fill_fail_pct_extreme": 0.05 if slip_mult >= 2 else 0.0,
            })
        except Exception as exc:
            row["scenario_error"] = str(exc)
    return row


def blotter(definition, friction_name="observed_base", slip_mult=1.0,
            latency_extra=0.0, fill_fail_pct=0.0):
    bt = d._backtest(
        definition, "CL-USDT-SWAP", "15m", friction_name,
        slip_mult=slip_mult, latency_extra=latency_extra, fill_fail_pct=fill_fail_pct)
    trades = bt.get("trades") or []
    rows = []
    for i, t in enumerate(trades):
        rows.append({
            "i": i,
            "pnl_ratio": t.get("pnl_ratio"),
            "profit": t.get("profit"),
            "exit_type": t.get("exit_type"),
            "stop_loss": t.get("stop_loss"),
            "entry_time": t.get("entry_time"),
            "exit_time": t.get("exit_time"),
            "entry_index": t.get("entry_index"),
            "exit_index": t.get("exit_index"),
            "transaction_cost_ratio": t.get("transaction_cost_ratio"),
            "friction_scenario": t.get("friction_scenario"),
            "entry_conditions": t.get("entry_conditions"),
            "exit_conditions": t.get("exit_conditions"),
            # price fields if present
            "entry_price": t.get("entry_price") or t.get("entry_px") or t.get("open_price"),
            "exit_price": t.get("exit_price") or t.get("exit_px") or t.get("close_price"),
            "stop_trigger_price": t.get("stop_trigger_price") or t.get("stop_price") or t.get("sl_price"),
            "raw_keys": sorted(t.keys()),
        })
    metrics = bt.get("metrics") or {}
    # packs-style metrics sometimes nested differently
    if not metrics and "sharpe" in bt:
        metrics = {k: bt.get(k) for k in (
            "trades", "win_rate_pct", "mean_net", "oos_profit", "sharpe",
            "fold_positive", "folds", "fold_means") if k in bt}
    return {"metrics": metrics or bt, "trades": rows, "n": len(rows)}


def extract_blotter():
    book = cl_book(tag="blotter_base")
    dsl = book["dsl"]
    normal_params = friction_params("observed_base", 1.0, 0.0)
    extreme_params = friction_params("observed_base", 2.0, 0.00005)
    extreme_params["fill_fail_pct"] = 0.05
    extreme_params["note"] = (
        "factory run_internal_packs extreme: slip_mult=2.0, latency_extra=0.00005, fill_fail_pct=0.05")

    base = blotter(dsl, "observed_base", 1.0, 0.0, 0.0)
    extreme = blotter(dsl, "observed_base", 2.0, 0.00005, 0.05)

    # also try named extreme scenario if exists
    alt = None
    try:
        alt = blotter(dsl, "extreme", 1.0, 0.0, 0.0)
    except Exception as exc:
        alt = {"error": str(exc)}

    stops_base = [t for t in base["trades"] if t.get("stop_loss") or (
        float(t.get("pnl_ratio") or 0) < -0.15)]
    stops_ext = [t for t in extreme["trades"] if t.get("stop_loss") or (
        float(t.get("pnl_ratio") or 0) < -0.15)]

    # pair by entry_time
    paired = []
    by_et = {t.get("entry_time"): t for t in base["trades"]}
    for et in stops_ext:
        paired.append({
            "entry_time": et.get("entry_time"),
            "extreme": et,
            "normal": by_et.get(et.get("entry_time")),
        })

    packet = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dsl": dsl,
        "normal_friction_params": normal_params,
        "extreme_friction_params": extreme_params,
        "base_metrics": base.get("metrics"),
        "extreme_metrics": extreme.get("metrics"),
        "base_trades": base["trades"],
        "extreme_trades": extreme["trades"],
        "hard_stops_extreme": stops_ext,
        "hard_stops_normal_paired": paired,
        "alt_extreme_named": alt if isinstance(alt, dict) and alt.get("error") else {
            "metrics": (alt or {}).get("metrics"), "n": (alt or {}).get("n")},
    }
    path = os.path.join(OUT, "frost2_cont_cl_exit_blotter.json")
    open(path, "w").write(json.dumps(packet, ensure_ascii=False, indent=2, default=str) + "\n")
    print("BLOTTER_WRITTEN", path, "base_n", base["n"], "ext_n", extreme["n"],
          "hard_ext", len(stops_ext), flush=True)
    for p in paired:
        n = p.get("normal") or {}
        e = p.get("extreme") or {}
        print("PAIR", p.get("entry_time"),
              "normal_pnl", n.get("pnl_ratio"), "ext_pnl", e.get("pnl_ratio"),
              "exit", e.get("exit_type"), "entry_px", e.get("entry_price"),
              "exit_px", e.get("exit_price"), flush=True)
    return packet, book


GLM_PROMPT = """你是GLM-5.2交易退出机制外科医生。只输出一个JSON对象，禁止Markdown与思考过程。
背景：CL-USDT-SWAP 15m short exhaustion。入场已过 WF≥7 与 logic_destruction，禁止改入场。
引擎固定止损 stop_loss_pct=0.9% × leverage=20 ≈ 账户约18%（加费后两笔约-22%）。
DSL出场现为：rsi14<45 take_profit；close>prev_high20 invalidation；max_hold=12。
极端摩擦：slip_mult=2、latency_extra=0.00005、fill_fail_pct=0.05。

请基于提供的两笔硬止损 blotter 回答：
1) 正常摩擦下这两笔大约亏多少（用 normal_pnl）？
2) 固定%硬止损在极端滑点叠加下是否必然放大爆仓式亏损？
3) 给出**唯一一条**可执行的出场/止损机制修改（只改EXIT，不改ENTRY）。优先：更紧的技术失效、动态追踪、或用已有特征做高摩擦回避式提前平仓。
可用出场特征：rsi14, close, ema6/8/16/21, prev_high20, prev_low20, z20, macd_stick, cci, atr14, max_hold_bars。

JSON格式：
{
  "normal_loss_estimate_zh":"...",
  "extreme_blowup_inevitable":true,
  "why_zh":"...",
  "exit_patch":{
    "action":"add_exit_condition|change_invalidation|change_max_hold|change_tp",
    "feature_or_field":"...",
    "from":"...",
    "to":"...",
    "dsl_exit_any_item":{"left":{"feature":"..."},"op":"...","right":{"feature_or_value":"..."},"role":"invalidation"},
    "why":"..."
  },
  "note_zh":"..."
}
"""


def ask_glm(packet, cycle):
    hard = []
    for p in packet.get("hard_stops_normal_paired") or []:
        hard.append({
            "entry_time": p.get("entry_time"),
            "normal": {k: (p.get("normal") or {}).get(k) for k in (
                "pnl_ratio", "exit_type", "exit_time", "entry_price", "exit_price",
                "transaction_cost_ratio", "stop_loss")},
            "extreme": {k: (p.get("extreme") or {}).get(k) for k in (
                "pnl_ratio", "exit_type", "exit_time", "entry_price", "exit_price",
                "transaction_cost_ratio", "stop_loss")},
        })
    user = {
        "cycle": cycle,
        "normal_params": packet.get("normal_friction_params"),
        "extreme_params": packet.get("extreme_friction_params"),
        "base_sharpe": (packet.get("base_metrics") or {}).get("sharpe") if isinstance(
            packet.get("base_metrics"), dict) else None,
        "extreme_sharpe_hint": -0.146,
        "hard_stop_pairs": hard,
        "current_exit": (packet.get("dsl") or {}).get("exit"),
        "max_hold_bars": (packet.get("dsl") or {}).get("max_hold_bars"),
    }
    ai = d._ai_json("glm", GLM_PROMPT, user, max_tokens=900, temperature=0.1)
    path = os.path.join(OUT, "frost2_cont_cl_exit_glm_c%d.json" % cycle)
    open(path, "w").write(json.dumps({
        "ok": ai.get("ok"), "parsed": ai.get("parsed"), "error": ai.get("error"),
        "raw_preview": (ai.get("raw_preview") or ai.get("content") or "")[:2000],
    }, ensure_ascii=False, indent=2) + "\n")
    print("GLM_C%s" % cycle, ai.get("ok"), ai.get("error"), ai.get("parsed"), flush=True)
    return ai


def apply_exit_patch(book, parsed, cycle):
    book = copy.deepcopy(book)
    dsl = book["dsl"]
    patch = (parsed or {}).get("exit_patch") or {}
    action = str(patch.get("action") or "")
    item = patch.get("dsl_exit_any_item")
    field = str(patch.get("feature_or_field") or "")
    to = patch.get("to")

    # normalize item
    if isinstance(item, dict) and item.get("left"):
        row = copy.deepcopy(item)
        # fix right if feature_or_value shorthand
        right = row.get("right") or {}
        if "feature_or_value" in right:
            v = right.pop("feature_or_value")
            if isinstance(v, (int, float)) or (isinstance(v, str) and v.replace(".", "", 1).isdigit()):
                right["value"] = float(v)
            else:
                right["feature"] = str(v)
            row["right"] = right
        row.setdefault("role", "invalidation")
        dsl.setdefault("exit", {}).setdefault("any", []).append(row)
    elif action == "change_invalidation" and to:
        # replace prev_high20 with to feature
        for row in (dsl.get("exit") or {}).get("any") or []:
            if row.get("role") == "invalidation":
                if isinstance(to, str) and not to.replace(".", "", 1).replace("-", "", 1).isdigit():
                    row["right"] = {"feature": to}
                else:
                    # value threshold vs entry not supported; use feature ema8 default
                    row["right"] = {"feature": str(to)}
    elif action == "change_max_hold" or field == "max_hold_bars":
        dsl["max_hold_bars"] = int(float(to))
    elif action == "change_tp" or "rsi" in field and "tp" in action:
        for row in (dsl.get("exit") or {}).get("any") or []:
            if row.get("role") == "take_profit":
                row["right"] = {"value": float(to)}
    else:
        # fallback surgical exits that address hard-stop before 0.9% move
        # tighter invalidation: close > ema8 (fires sooner than prev_high20 for shorts)
        dsl["exit"]["any"].append({
            "left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema8"},
            "role": "invalidation", "id": "x_early_inv",
        })
        patch = {"action": "fallback_add_ema8_invalidation", "why": "glm_parse_or_empty"}

    dsl["key"] = "frost2c_cl_15m_exit_c%d" % cycle
    book["dsl"] = f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m")
    book["_applied_patch"] = patch
    return book


def local_fallback_patches(cycle):
    """Deterministic exit-only patches if GLM fails — tighter stop before engine 0.9%."""
    if cycle == 1:
        # early technical invalidation + slightly shorter hold
        return {
            "exit_patch": {
                "action": "add_exit_condition",
                "feature_or_field": "ema8",
                "from": "prev_high20_only",
                "to": "also close>ema8 invalidation",
                "dsl_exit_any_item": {
                    "left": {"feature": "close"}, "op": "gt",
                    "right": {"feature": "ema8"}, "role": "invalidation",
                },
                "why": "ema8 tighter than prev_high20; exit before 0.9% hard stop",
            },
            "also_max_hold": 8,
        }
    # cycle 2: even tighter — ema6 invalidation + z20 reclaim
    return {
        "exit_patch": {
            "action": "add_exit_condition",
            "feature_or_field": "ema6+z20",
            "from": "prev_high20",
            "to": "close>ema6 OR z20<0",
            "dsl_exit_any_item": {
                "left": {"feature": "close"}, "op": "gt",
                "right": {"feature": "ema6"}, "role": "invalidation",
            },
            "extra_items": [{
                "left": {"feature": "z20"}, "op": "lt",
                "right": {"value": 0.0}, "role": "invalidation",
            }],
            "why": "cut adverse excursion before engine hard stop",
        },
        "also_max_hold": 6,
        "replace_inv": "ema8",
    }


def apply_local(book, parsed, cycle):
    book = apply_exit_patch(book, parsed, cycle)
    # extras from local fallback
    extras = (parsed.get("exit_patch") or {}).get("extra_items") or []
    for item in extras:
        book["dsl"]["exit"]["any"].append(copy.deepcopy(item))
    if parsed.get("also_max_hold"):
        book["dsl"]["max_hold_bars"] = int(parsed["also_max_hold"])
    if parsed.get("replace_inv"):
        for row in book["dsl"]["exit"]["any"]:
            right = row.get("right") or {}
            if row.get("role") == "invalidation" and right.get("feature") == "prev_high20":
                right["feature"] = parsed["replace_inv"]
    book["dsl"] = f2.ensure_dsl(book["dsl"], "CL-USDT-SWAP", "15m")
    return book


def evaluate(book):
    q = f2.quick_suite(book)
    bm = q.get("base_metrics") or {}
    out = {
        "key": book["dsl"]["key"],
        "quick": bool(q.get("quick_pass")),
        "fp": bm.get("fold_positive"),
        "tr": bm.get("trades"),
        "wr": bm.get("win_rate_pct"),
        "sh": bm.get("sharpe"),
        "dest": (q.get("logic_destruction") or {}).get("pass"),
        "failed_step": q.get("failed_step"),
        "patch": book.get("_applied_patch"),
    }
    fr = q.get("extreme_friction") or {}
    fr_m = fr.get("metrics") or {}
    out["friction_sharpe"] = fr_m.get("sharpe")
    out["friction_pass"] = (
        int(fr_m.get("trades") or 0) >= 5
        and float(fr_m.get("sharpe") or -99) >= 0.0
    )
    if not q.get("quick_pass"):
        return out, q
    full = f2.full_suite(book, q)
    out["full"] = full.get("full_pass")
    out["friction_sharpe"] = (full.get("full") or {}).get("friction_sharpe")
    out["friction_pass"] = bool((full.get("full") or {}).get("friction_sharpe_ge_0"))
    mc = (full.get("full") or {}).get("mc") or {}
    out["mc_beat"] = mc.get("beat_ratio")
    out["mc_actual"] = mc.get("actual_final")
    out["mc_pass"] = bool(mc.get("pass"))
    out["failed_step"] = full.get("failed_step")
    out["_full_packs"] = full
    return out, full


def main():
    packet, base_book = extract_blotter()
    cycles = []
    winner = None

    for cycle in (1, 2):
        print("==== CYCLE", cycle, "====", flush=True)
        ai = ask_glm(packet, cycle)
        parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
        if not parsed or not (parsed.get("exit_patch")):
            parsed = local_fallback_patches(cycle)
            print("USING_LOCAL_FALLBACK", parsed, flush=True)
        else:
            # merge hold hint if glm only gave exit item
            fb = local_fallback_patches(cycle)
            if "also_max_hold" not in parsed and cycle == 1:
                # keep glm pure unless empty
                pass

        book = apply_local(copy.deepcopy(base_book), parsed, cycle)
        # ensure entry unchanged
        ent = (book["dsl"].get("entry") or {}).get("all") or []
        assert len(ent) >= 5, "entry corrupted"

        s, packs = evaluate(book)
        # extreme blotter after patch
        ext = blotter(book["dsl"], "observed_base", 2.0, 0.00005, 0.05)
        hard = [t for t in ext["trades"] if t.get("stop_loss") or float(t.get("pnl_ratio") or 0) < -0.15]
        s["extreme_hard_stops_n"] = len(hard)
        s["extreme_pnls"] = [t.get("pnl_ratio") for t in ext["trades"]]
        print("EVAL", {k: s.get(k) for k in s if k != "_full_packs"}, flush=True)

        cycle_row = {
            "cycle": cycle,
            "glm": {"ok": ai.get("ok"), "parsed": ai.get("parsed"), "error": ai.get("error")},
            "applied": book.get("_applied_patch"),
            "dsl_exit": book["dsl"].get("exit"),
            "max_hold_bars": book["dsl"].get("max_hold_bars"),
            "result": {k: s.get(k) for k in s if k != "_full_packs"},
        }
        cycles.append(cycle_row)
        open(os.path.join(OUT, "frost2_cont_cl_exit_cycle%d.json" % cycle), "w").write(
            json.dumps(cycle_row, ensure_ascii=False, indent=2, default=str) + "\n")

        if s.get("quick") and s.get("friction_pass") and s.get("mc_pass"):
            winner = (book, packs, s)
            break
        # if quick broken by exit, try next cycle with different exit
        if not s.get("quick"):
            print("QUICK_BROKEN_BY_EXIT continue", flush=True)
            continue
        if not s.get("friction_pass"):
            print("FRICTION_STILL_FAIL", s.get("friction_sharpe"), flush=True)
            # feed updated blotter into next glm
            packet["extreme_trades"] = ext["trades"]
            packet["hard_stops_extreme"] = hard
            continue

    final = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ok": False,
        "entered_review_queue": False,
        "friction_sharpe_after": None,
        "cycles": cycles,
        "pending": None,
    }

    if winner:
        book, packs, s = winner
        final["friction_sharpe_after"] = s.get("friction_sharpe")
        final["mc_beat"] = s.get("mc_beat")
        final["summary"] = {k: s.get(k) for k in s if k != "_full_packs"}
        sf = f2.run_sim_formal(book, packs)
        final["sim"] = sf.get("sim")
        final["formal"] = sf.get("formal")
        final["pending"] = sf.get("pending")
        final["failed_step"] = sf.get("failed_step")
        final["ok"] = not sf.get("failed_step")
        final["entered_review_queue"] = bool((sf.get("pending") or {}).get("ok"))
        print("SF", final.get("failed_step"), final.get("pending"), flush=True)
    else:
        # best friction among cycles
        best_fr = None
        for c in cycles:
            fr = (c.get("result") or {}).get("friction_sharpe")
            if fr is None:
                continue
            if best_fr is None or float(fr) > float(best_fr):
                best_fr = fr
                final["best_cycle"] = c
        final["friction_sharpe_after"] = best_fr
        final["failed_step"] = "exit_repair_exhausted"
        final["entered_review_queue"] = False

    open(os.path.join(OUT, "frost2_cont_cl_exit_repair.json"), "w").write(
        json.dumps(final, ensure_ascii=False, indent=2, default=str) + "\n")

    st_path = os.path.join(OUT, "frost2_cont_status.json")
    try:
        st = json.load(open(st_path))
    except Exception:
        st = {"op": "寒霜贰续"}
    st["cl_exit_repair"] = {
        "friction_sharpe_after": final.get("friction_sharpe_after"),
        "entered_review_queue": final.get("entered_review_queue"),
        "ok": final.get("ok"),
        "failed_step": final.get("failed_step"),
        "pending": final.get("pending"),
        "cycles_n": len(cycles),
    }
    if final.get("entered_review_queue"):
        keys = list(st.get("pending_keys") or [])
        k = (final.get("pending") or {}).get("key")
        if k and k not in keys:
            keys.append(k)
        st["pending_keys"] = keys
        st["ok_min_cl"] = True
        st["cl_abandoned"] = False
    open(st_path, "w").write(json.dumps(st, ensure_ascii=False, indent=2) + "\n")

    print("FINAL_LINE",
          "摩擦夏普修复到=%s" % final.get("friction_sharpe_after"),
          "是否进入复核队列=%s" % final.get("entered_review_queue"),
          flush=True)
    return 0 if final.get("entered_review_queue") else 1


if __name__ == "__main__":
    sys.exit(main())
