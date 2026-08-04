#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Final CL Full-gate push: cut -22% stop-cluster via atr/z caps + regime filters."""
from __future__ import print_function

import json
import os
import sys

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
OUT = "/root/auto_trade/dual_engine"

GLM_PROMPT = """你是GLM-5.2策略外科医生。只输出一个JSON对象，不要Markdown、不要思考过程。
CL 15m short exhaustion 已过 WF≥7 与 logic_destruction，Full失败：
friction_sharpe=-0.145(需≥0); MC beat=0.83(需≥0.90); actual_final=-0.0097。
12笔pnl: 两笔约-0.221的失效止损主导亏损。
当前DSL: entry rsi14>54,z20>0.1,macd_stick<0,close<ema16,cci>50;
exit rsi14<45 TP, close>prev_high20 inv; max_hold=12。
请给1-3个外科补丁（改哪个条件 from→to），目标去掉大亏单并让 friction Sharpe≥0。
可用特征: rsi14,z20,macd_stick,cci,ema6/8/16/21,close,prev_high20,prev_low20,atr14,h1_ema19,h1_ema53,h1_slope4,max_hold_bars。
JSON格式:
{"patches":[{"feature_or_field":"...","action":"change_value|add_condition|change_exit","from":"...","to":"...","why":"..."}],"priority_patch_index":0,"note_zh":"..."}
"""


def mk(tag, rsi=54, z_lo=0.1, z_hi=None, hold=12, tp=45, cci=50,
       ema="ema16", inv="prev_high20", atr_max=None, atr_min=None,
       h1_down=False, slope_down=False, macd_floor=None):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z_lo)}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": ema}},
        {"left": {"feature": "cci"}, "op": "gt", "right": {"value": float(cci)}},
    ]
    if z_hi is not None:
        entry.append({"left": {"feature": "z20"}, "op": "lt", "right": {"value": float(z_hi)}})
    if atr_max is not None:
        entry.append({"left": {"feature": "atr14"}, "op": "lt", "right": {"value": float(atr_max)}})
    if atr_min is not None:
        entry.append({"left": {"feature": "atr14"}, "op": "gt", "right": {"value": float(atr_min)}})
    if macd_floor is not None:
        entry.append({"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": float(macd_floor)}})
    if h1_down:
        entry.append({"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}})
    if slope_down:
        entry.append({"left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": 0.0}})
    dsl = {
        "key": "frost2c_cl_fin_%s" % tag,
        "name": "寒霜贰-CL-15m-exhaustion_fade",
        "direction": "short",
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(tp)}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": inv}, "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
    }
    return {
        "symbol": "CL-USDT-SWAP", "timeframe": "15m", "direction": "short",
        "logic_class": "exhaustion_fade",
        "thesis": "cut stop-cluster; pass friction+MC",
        "title": "寒霜贰-CL-15m-exhaustion_fade",
        "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"),
        "gate_mode": "frost2", "source": "cl_final",
    }


def score(s):
    return (
        float(s.get("friction_sh") if s.get("friction_sh") is not None else -99),
        float(s.get("mc_beat") or 0),
        float(s.get("mc_actual") or -99),
        float(s.get("mean") or -99),
    )


def eval_one(book):
    q = f2.quick_suite(book)
    bm = q.get("base_metrics") or {}
    s = {
        "key": book["dsl"]["key"],
        "quick": bool(q.get("quick_pass")),
        "fp": bm.get("fold_positive"), "tr": bm.get("trades"),
        "wr": round(float(bm.get("win_rate_pct") or 0), 1),
        "sh": bm.get("sharpe"), "mean": bm.get("mean_net"),
        "dest": (q.get("logic_destruction") or {}).get("pass"),
    }
    if not q.get("quick_pass"):
        return None, s, q
    full = f2.full_suite(book, q)
    s["full"] = full.get("full_pass")
    s["friction_sh"] = (full.get("full") or {}).get("friction_sharpe")
    mc = (full.get("full") or {}).get("mc") or {}
    s["mc_beat"] = mc.get("beat_ratio")
    s["mc_actual"] = mc.get("actual_final")
    return (full if full.get("full_pass") else None), s, full


def apply_glm_patches(base_kwargs, patches):
    kw = dict(base_kwargs)
    extras = []
    for p in patches or []:
        field = str(p.get("feature_or_field") or "")
        to = p.get("to")
        action = str(p.get("action") or "")
        try:
            if field == "rsi14" and action in ("change_value", "add_condition"):
                kw["rsi"] = float(to)
            elif field == "z20" and "lt" in str(to).lower():
                # e.g. to "1.0" meaning upper bound
                kw["z_hi"] = float(str(to).replace("<", "").strip())
            elif field == "z20":
                kw["z_lo"] = float(to)
            elif field == "cci":
                kw["cci"] = float(to)
            elif field == "max_hold_bars":
                kw["hold"] = int(float(to))
            elif field in ("tp", "rsi_exit") or "rsi14" in field and "exit" in action:
                kw["tp"] = float(to)
            elif field == "atr14" and action.startswith("add"):
                # expect to like "<1.2" or dict
                s = str(to)
                if "<" in s:
                    kw["atr_max"] = float(s.split("<")[-1].strip())
                elif ">" in s:
                    kw["atr_min"] = float(s.split(">")[-1].strip())
                else:
                    kw["atr_max"] = float(to)
            elif field in ("h1_ema19", "h1_down"):
                kw["h1_down"] = True
            elif field == "h1_slope4":
                kw["slope_down"] = True
            elif field == "prev_high20" and "ema" in str(to):
                kw["inv"] = str(to).strip()
            elif field == "macd_stick" and action.startswith("add"):
                s = str(to)
                if ">" in s:
                    kw["macd_floor"] = float(s.split(">")[-1].strip())
            else:
                extras.append(p)
        except Exception:
            extras.append(p)
    return kw, extras


def main():
    # trade detail dump
    base_book = mk("base")
    base_bt = d._backtest(base_book["dsl"], "CL-USDT-SWAP", "15m", "observed_base")
    trades = base_bt.get("trades") or []
    detail = []
    for t in trades:
        detail.append({
            "pnl": t.get("pnl_ratio"),
            "exit_type": t.get("exit_type"),
            "entry_time": t.get("entry_time"),
            "exit_time": t.get("exit_time"),
            "entry_index": t.get("entry_index"),
            "exit_index": t.get("exit_index"),
            "stop_loss": t.get("stop_loss"),
            "cost": t.get("transaction_cost_ratio"),
        })
    open(os.path.join(OUT, "frost2_cont_cl_trade_detail.json"), "w").write(
        json.dumps(detail, ensure_ascii=False, indent=2) + "\n")
    print("trade_detail", len(detail), flush=True)
    for row in detail:
        print("T", row, flush=True)

    # GLM surgical (strict JSON)
    ai = d._ai_json("glm", GLM_PROMPT, {
        "dsl": base_book["dsl"],
        "trade_pnls": [r["pnl"] for r in detail],
        "trade_exit_types": [r["exit_type"] for r in detail],
        "need": {"friction_sharpe_ge": 0, "mc_beat_ge": 0.9},
    }, max_tokens=700, temperature=0.1)
    open(os.path.join(OUT, "frost2_cont_cl_glm_final.json"), "w").write(
        json.dumps({"ok": ai.get("ok"), "parsed": ai.get("parsed"),
                    "error": ai.get("error"),
                    "raw_preview": (ai.get("raw_preview") or "")[:1500]},
                   ensure_ascii=False, indent=2) + "\n")
    print("GLM", ai.get("ok"), ai.get("error"), ai.get("parsed"), flush=True)

    cands = []
    # core: z upper bound to avoid runaway trends mistaken as exhaustion
    for z_hi in (0.6, 0.8, 1.0, 1.2, 1.5):
        for hold in (8, 10, 12, 16):
            for tp in (40, 42, 45):
                cands.append(mk("zhi%s_h%s_t%s" % (str(z_hi).replace(".", "p"), hold, tp),
                                z_hi=z_hi, hold=hold, tp=tp))
    # atr cap (CL atr14 scale unknown — try several)
    for atr_max in (0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 5.0):
        for hold in (10, 12):
            cands.append(mk("atr%s_h%s" % (str(atr_max).replace(".", "p"), hold),
                            atr_max=atr_max, hold=hold, z_hi=1.0))
    # atr floor (need some vol to mean-revert)
    for atr_min in (0.05, 0.1, 0.2, 0.3):
        cands.append(mk("atrmin%s" % str(atr_min).replace(".", "p"),
                        atr_min=atr_min, z_hi=1.0, hold=12))
    # regime
    for hold, tp, z_hi in [(10, 42, 0.8), (12, 45, 1.0), (8, 40, 0.6), (14, 45, 1.2)]:
        cands.append(mk("h1_h%s_t%s" % (hold, tp), hold=hold, tp=tp, z_hi=z_hi, h1_down=True))
        cands.append(mk("hs_h%s_t%s" % (hold, tp), hold=hold, tp=tp, z_hi=z_hi,
                        h1_down=True, slope_down=True))
    # macd floor (avoid already crashing)
    for floor in (-0.5, -1.0, -2.0, -5.0):
        cands.append(mk("macdf%s" % str(floor).replace("-", "m").replace(".", "p"),
                        macd_floor=floor, z_hi=1.0, hold=12))
    # stricter entry quality
    for rsi, cci, z_lo, z_hi, hold, tp in [
        (56, 60, 0.15, 0.9, 10, 42),
        (58, 70, 0.2, 1.0, 12, 40),
        (55, 55, 0.1, 0.7, 8, 45),
        (57, 80, 0.2, 1.2, 12, 42),
        (60, 50, 0.3, 1.0, 10, 40),
    ]:
        cands.append(mk("q_r%s_c%s" % (rsi, cci), rsi=rsi, cci=cci, z_lo=z_lo,
                        z_hi=z_hi, hold=hold, tp=tp))
    # XRP-like hold=20
    for z_hi in (0.8, 1.0, None):
        cands.append(mk("h20_zhi%s" % (z_hi if z_hi else "none"),
                        hold=20, tp=45, z_hi=z_hi))
    # GLM patches applied on base
    parsed = ai.get("parsed") or {}
    if isinstance(parsed, dict) and parsed.get("patches"):
        kw, _ = apply_glm_patches({
            "rsi": 54, "z_lo": 0.1, "hold": 12, "tp": 45, "cci": 50,
        }, parsed.get("patches"))
        cands.insert(0, mk("glm_p0", **kw))
        # also glm + z_hi
        kw2 = dict(kw)
        kw2.setdefault("z_hi", 1.0)
        cands.insert(0, mk("glm_p0_zhi", **kw2))

    print("final_cands", len(cands), flush=True)
    winner = None
    best = []
    for i, book in enumerate(cands):
        passed, s, packs = eval_one(book)
        if s.get("quick"):
            best.append(s)
            print("TRY", s, flush=True)
        elif i % 25 == 0:
            print("..", s.get("key"), s.get("fp"), s.get("dest"), flush=True)
        if passed is not None:
            winner = (book, packs, s)
            print("FULL_WIN", s, flush=True)
            break

    best.sort(key=score, reverse=True)
    result = {
        "ok": False, "n_quick": len(best), "best12": best[:12],
        "glm": {"ok": ai.get("ok"), "parsed": ai.get("parsed")},
    }
    if winner:
        book, packs, s = winner
        sf = f2.run_sim_formal(book, packs)
        result.update({
            "ok": not sf.get("failed_step"),
            "key": s["key"], "summary": s,
            "sim": sf.get("sim"), "formal": sf.get("formal"),
            "pending": sf.get("pending"),
            "failed_step": sf.get("failed_step"),
        })
        print("SF", result.get("failed_step"), result.get("pending"), flush=True)
    else:
        result["failed_step"] = "full_suite_no_winner"
        result["best_near"] = best[0] if best else None
        print("NO_WIN", result.get("best_near"), flush=True)

    open(os.path.join(OUT, "frost2_cont_cl_final.json"), "w").write(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n")

    st_path = os.path.join(OUT, "frost2_cont_status.json")
    try:
        st = json.load(open(st_path))
    except Exception:
        st = {"op": "寒霜贰续"}
    st["cl_final"] = {
        "ok": result.get("ok"),
        "failed_step": result.get("failed_step"),
        "key": result.get("key"),
        "pending": result.get("pending"),
        "best_near": result.get("best_near") or (result.get("best12") or [None])[0],
        "abandoned": (not result.get("ok")),
    }
    if (result.get("pending") or {}).get("ok"):
        keys = list(st.get("pending_keys") or [])
        k = result["pending"]["key"]
        if k not in keys:
            keys.append(k)
        st["pending_keys"] = keys
        st["ok_min_cl"] = True
        st["cl_final"]["abandoned"] = False
    elif not result.get("ok"):
        st["cl_abandoned"] = True
        st["cl_abandon_reason"] = result.get("failed_step")
        st["cl_best_near"] = result.get("best_near") or (result.get("best12") or [None])[0]
    open(st_path, "w").write(json.dumps(st, ensure_ascii=False, indent=2) + "\n")
    print("DONE", {"ok": result.get("ok"), "failed": result.get("failed_step"),
                   "pending": (result.get("pending") or {}).get("key")}, flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
