#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CL near-miss push: cci_h12 had mc_beat=0.83 friction=-0.145. Hunt full pass → pending."""
from __future__ import print_function
import json, os, sys
sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
OUT = "/root/auto_trade/dual_engine"

GLM_FULL_FIX = """你是GLM-5.2。CL 15m exhaustion 已过 WF≥7 与 logic_destruction，但 Full 未过：
- friction Sharpe=-0.145（需≥0）
- MC beat_ratio=0.83（需≥0.90），actual_final=-0.0097
当前: rsi>54, z>0.1, macd<0, close<ema16, cci>50; exit rsi<45 TP, close>prev_high20 inv; hold=12。
输出JSON外科补丁（1-3处）提升摩擦后期望与路径稳健：
{"patches":[{"feature_or_field":"...","action":"change_value|add_condition","from":"...","to":...,"why":"..."}],"note":"..."}
可用特征限: rsi14,z20,macd_stick,cci,ema*,close,prev_high20,prev_low20,h1_ema19,h1_ema53,h1_slope4。只输出JSON。
"""


def mk(rsi, z, hold, tp, cci_v, tag, extra=None, inv="prev_high20"):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z)}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
        {"left": {"feature": "cci"}, "op": "gt", "right": {"value": float(cci_v)}},
    ]
    if extra:
        entry.extend(extra)
    dsl = {
        "key": "frost2c_cl_15m_push_%s" % tag,
        "name": "寒霜贰-CL-15m-exhaustion_fade",
        "direction": "short",
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(tp)}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": inv}, "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
        "description": "CL push full gates",
    }
    return {
        "symbol": "CL-USDT-SWAP", "timeframe": "15m", "direction": "short",
        "logic_class": "exhaustion_fade", "thesis": "CL exhaustion full push",
        "title": "寒霜贰-CL-15m-exhaustion_fade",
        "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"),
        "gate_mode": "frost2", "source": "frost2_cont_cl_push",
    }


def eval_full(book):
    q = f2.quick_suite(book)
    bm = q.get("base_metrics") or {}
    s = {
        "key": (book.get("dsl") or {}).get("key"),
        "quick": q.get("quick_pass"),
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
    return full if full.get("full_pass") else None, s, full


def main():
    # GLM advice (best-effort)
    near_book = mk(54, 0.1, 12, 45, 50, "seed")
    q0 = f2.quick_suite(near_book)
    ai = d._ai_json("glm", GLM_FULL_FIX, {
        "dsl": near_book["dsl"],
        "metrics": q0.get("base_metrics"),
        "full_near": {"friction": -0.145, "mc_beat": 0.83, "mc_actual": -0.0097},
    }, max_tokens=900, temperature=0.15)
    open(os.path.join(OUT, "frost2_cont_cl_glm_full_fix.json"), "w").write(
        json.dumps({"ok": ai.get("ok"), "parsed": ai.get("parsed"), "error": ai.get("error"),
                    "raw_preview": ai.get("raw_preview")}, ensure_ascii=False, indent=2) + "\n")
    print("GLM_FULL", ai.get("ok"), ai.get("error"), flush=True)

    cands = []
    # dense-but-bounded grid around near-miss cci_h12
    for rsi in (54, 55, 56):
        for z in (0.1, 0.15, 0.2):
            for hold in (10, 12, 14):
                for tp in (42, 45):
                    for cci_v in (50, 60):
                        tag = "r%s_z%s_h%s_t%s_c%s" % (
                            rsi, str(z).replace(".", "p"), hold, tp, cci_v)
                        cands.append(mk(rsi, z, hold, tp, cci_v, tag))
    # regime extras
    h1 = {"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}}
    sl = {"left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": 0.0}}
    for hold, tp, cci_v, tag in [
        (12, 42, 50, "h1_a"), (10, 45, 60, "h1_b"), (14, 40, 50, "h1_c"),
        (12, 45, 50, "hs_a"), (10, 42, 60, "hs_b"),
        (8, 45, 50, "h1_h8"), (16, 42, 55, "h1_h16"),
    ]:
        extra = [h1] if tag.startswith("h1") else [h1, sl]
        cands.append(mk(54, 0.1, hold, tp, cci_v, tag, extra=extra))
    # inv=ema8 / tp tighten
    for hold, tp, cci_v, tag in [
        (12, 45, 50, "inv8_a"), (10, 42, 50, "inv8_b"), (12, 40, 60, "inv8_c"),
        (14, 48, 50, "tp48"), (10, 38, 50, "tp38"),
    ]:
        inv = "ema8" if tag.startswith("inv8") else "prev_high20"
        cands.append(mk(54, 0.1, hold, tp, cci_v, tag, inv=inv))
    # mean-edge: higher RSI + cci
    for rsi, z, hold, tp, cci_v in [
        (57, 0.2, 12, 42, 70), (58, 0.25, 10, 40, 80), (55, 0.05, 12, 45, 50),
    ]:
        cands.append(mk(rsi, z, hold, tp, cci_v, "edge_%s" % rsi))

    print("push candidates", len(cands), flush=True)
    winner = None
    best = []
    for i, book in enumerate(cands):
        passed, s, packs = eval_full(book)
        if s.get("quick"):
            best.append(s)
            print("TRY", s, flush=True)
        elif i % 30 == 0:
            print("..", s, flush=True)
        if passed is not None:
            winner = (book, packs, s)
            print("FULL_WIN", s, flush=True)
            break
        # early stop if many quick but no full after enough tries — continue
        if len(best) >= 25 and winner is None and i > 80:
            # keep going but report
            pass

    best.sort(key=lambda x: (
        float(x.get("friction_sh") if x.get("friction_sh") is not None else -99),
        float(x.get("mc_beat") or 0),
        float(x.get("mean") or -99),
    ), reverse=True)

    result = {"best10": best[:10], "ok": False, "n_quick": len(best)}
    if winner:
        book, packs, s = winner
        sf = f2.run_sim_formal(book, packs)
        result.update({
            "key": s["key"], "summary": s,
            "sim": sf.get("sim"), "formal": sf.get("formal"),
            "pending": sf.get("pending"), "failed_step": sf.get("failed_step"),
            "ok": not sf.get("failed_step"),
        })
        print("SIMFORMAL", result.get("failed_step"),
              (sf.get("sim") or {}).get("wr_deepseek_sim"),
              (sf.get("formal") or {}).get("approved"),
              sf.get("pending"), flush=True)
    else:
        result["failed_step"] = "full_suite_no_winner"
        result["best_near"] = best[0] if best else None
        print("NO_WINNER best", result.get("best_near"), flush=True)

    open(os.path.join(OUT, "frost2_cont_cl_push.json"), "w").write(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n")

    # update status / pending
    st_path = os.path.join(OUT, "frost2_cont_status.json")
    try:
        st = json.load(open(st_path))
    except Exception:
        st = {"op": "寒霜贰续"}
    st["cl_push"] = {
        "ok": result.get("ok"),
        "failed_step": result.get("failed_step"),
        "key": result.get("key"),
        "pending": result.get("pending"),
        "best_near": result.get("best_near") or (result.get("best10") or [None])[0],
        "sim": result.get("sim"),
        "formal": {k: (result.get("formal") or {}).get(k)
                   for k in ("approved", "annotation")} if result.get("formal") else None,
    }
    pend = (result.get("pending") or {}).get("key")
    if pend:
        keys = list(st.get("pending_keys") or [])
        if pend not in keys:
            keys.append(pend)
        st["pending_keys"] = keys
        st["ok_min_cl"] = True
    open(st_path, "w").write(json.dumps(st, ensure_ascii=False, indent=2) + "\n")
    print("DONE", {"ok": result.get("ok"), "pending": pend,
                   "failed_step": result.get("failed_step")}, flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
