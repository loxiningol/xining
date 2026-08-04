#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clean single-process hunt: diagnose + ADA trendpb ports + SOL/BTC exhaustion."""
from __future__ import print_function

import json
import os
import sys
import traceback

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
OUT = "/root/auto_trade/dual_engine"
LOCK = "/tmp/frost2_cont_clean.lock"


def acquire():
    if os.path.exists(LOCK):
        try:
            pid = int(open(LOCK).read().strip() or "0")
            os.kill(pid, 0)
            print("LOCKED by", pid, flush=True)
            return False
        except Exception:
            pass
    open(LOCK, "w").write(str(os.getpid()))
    return True


def release():
    try:
        if os.path.exists(LOCK) and open(LOCK).read().strip() == str(os.getpid()):
            os.remove(LOCK)
    except Exception:
        pass


def ensure(dsl, sym, tf):
    return f2.ensure_dsl(dsl, sym, tf)


def book_of(sym, tf, direction, logic, title, dsl, thesis=""):
    return {
        "symbol": sym, "timeframe": tf, "direction": direction,
        "logic_class": logic, "thesis": thesis or title, "title": title,
        "dsl": ensure(dsl, sym, tf), "gate_mode": "frost2", "source": "clean",
    }


def exh(sym, tf, tag, rsi=55, z=0.0, hold=20, tp=45, cci=None):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z)}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
    ]
    if cci is not None:
        entry.append({"left": {"feature": "cci"}, "op": "gt", "right": {"value": float(cci)}})
    dsl = {
        "key": "frost2c_%s_%s_%s" % (sym.split("-")[0].lower(), tf, tag),
        "name": "寒霜贰续-%s-%s-exhaustion" % (sym.split("-")[0], tf),
        "direction": "short",
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(tp)}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
        "max_hold_bars": int(hold),
    }
    return book_of(sym, tf, "short", "exhaustion_fade", dsl["name"], dsl)


def trendpb(sym, tf, tag, rsi_lo=42, z=2.0, hold=14, tp_rsi=60, direction="short"):
    # ADA live-style session trend pullback short
    if direction == "short":
        entry = [
            {"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema8"}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(rsi_lo)}},
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": float(-abs(z))}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        ]
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(tp_rsi)}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}, "role": "invalidation"},
        ]
    else:
        entry = [
            {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi_lo)}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(abs(z))}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
        ]
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(100 - tp_rsi)}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}, "role": "invalidation"},
        ]
    dsl = {
        "key": "frost2c_%s_%s_tpb_%s" % (sym.split("-")[0].lower(), tf, tag),
        "name": "寒霜贰续-%s-%s-trend_pullback" % (sym.split("-")[0], tf),
        "direction": direction,
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "max_hold_bars": int(hold),
    }
    return book_of(sym, tf, direction, "trend_pullback", dsl["name"], dsl)


def eval_one(book):
    try:
        q = f2.quick_suite(book)
    except Exception as exc:
        return None, {"key": book["dsl"]["key"], "error": str(exc), "quick": False}, None
    bm = q.get("base_metrics") or {}
    s = {
        "key": book["dsl"]["key"], "symbol": book["symbol"], "tf": book["timeframe"],
        "logic": book["logic_class"],
        "quick": bool(q.get("quick_pass")),
        "ok_packs": bool(q.get("ok")),
        "fp": bm.get("fold_positive"), "tr": bm.get("trades"),
        "wr": round(float(bm.get("win_rate_pct") or 0), 1) if bm.get("win_rate_pct") is not None else None,
        "sh": bm.get("sharpe"), "mean": bm.get("mean_net"),
        "dest": (q.get("logic_destruction") or {}).get("pass"),
        "failed_step": q.get("failed_step"),
    }
    if not q.get("quick_pass"):
        return None, s, q
    full = f2.full_suite(book, q)
    s["full"] = full.get("full_pass")
    s["friction_sh"] = (full.get("full") or {}).get("friction_sharpe")
    mc = (full.get("full") or {}).get("mc") or {}
    s["mc_beat"] = mc.get("beat_ratio")
    s["mc_actual"] = mc.get("actual_final")
    s["failed_step"] = full.get("failed_step")
    return (full if full.get("full_pass") else None), s, full


def main():
    if not acquire():
        return 2
    try:
        diag = {}
        # 1) XRP known-good
        xrp = exh("XRP-USDT-SWAP", "15m", "diag_xrp", rsi=55, z=0.0, hold=20, tp=45)
        _, sx, _ = eval_one(xrp)
        diag["xrp15"] = sx
        print("DIAG_XRP", sx, flush=True)

        # 2) CL best near
        cl = exh("CL-USDT-SWAP", "15m", "diag_cl", rsi=54, z=0.1, hold=12, tp=45, cci=50)
        _, sc, _ = eval_one(cl)
        diag["cl15"] = sc
        print("DIAG_CL", sc, flush=True)

        # 3) ADA live-ish (for gate calibration; will not push ADA)
        ada = trendpb("ADA-USDT-SWAP", "5m", "diag_ada", rsi_lo=42, z=2.0, hold=14)
        _, sa, _ = eval_one(ada)
        diag["ada5"] = sa
        print("DIAG_ADA", sa, flush=True)

        open(os.path.join(OUT, "frost2_cont_clean_diag.json"), "w").write(
            json.dumps(diag, ensure_ascii=False, indent=2) + "\n")

        cands = []
        # SOL/BTC/ETH/DOGE trendpb ports (avoid ADA/LTC/NG/XRP15)
        for sym, tf in [
            ("SOL-USDT-SWAP", "5m"), ("SOL-USDT-SWAP", "15m"),
            ("BTC-USDT-SWAP", "5m"), ("BTC-USDT-SWAP", "15m"),
            ("ETH-USDT-SWAP", "5m"), ("ETH-USDT-SWAP", "15m"),
            ("DOGE-USDT-SWAP", "5m"), ("DOGE-USDT-SWAP", "15m"), ("DOGE-USDT-SWAP", "1h"),
            ("XRP-USDT-SWAP", "5m"),
        ]:
            for rsi_lo, z, hold in [
                (42, 2.0, 14), (40, 2.2, 14), (45, 1.8, 12),
                (38, 2.3, 16), (42, 2.5, 10), (48, 1.5, 14),
            ]:
                cands.append(trendpb(sym, tf, "r%s_z%s_h%s" % (
                    rsi_lo, str(z).replace(".", "p"), hold),
                    rsi_lo=rsi_lo, z=z, hold=hold))

        # SOL/BTC 15m exhaustion (GLM dirs) — few variants
        for sym in ("SOL-USDT-SWAP", "BTC-USDT-SWAP", "ETH-USDT-SWAP"):
            for rsi, z, hold, cci in [
                (55, 0.0, 20, None), (54, 0.1, 12, 50), (58, 0.2, 16, None),
                (56, 0.0, 24, 40), (60, 0.3, 12, 60),
            ]:
                cands.append(exh(sym, "15m", "ex_r%s_h%s" % (rsi, hold),
                                 rsi=rsi, z=z, hold=hold, cci=cci))

        # DOGE reclaim
        for rsi, hold, tp in [(38, 12, 58), (40, 10, 55), (35, 14, 60)]:
            entry = [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": float(rsi)}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema8"}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
            ]
            dsl = {
                "key": "frost2c_doge_1h_rec_%s" % rsi,
                "name": "寒霜贰续-DOGE-1h-reclaim",
                "direction": "long",
                "entry": {"all": entry},
                "exit": {"any": [
                    {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(tp)}, "role": "take_profit"},
                    {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}, "role": "invalidation"},
                ]},
                "max_hold_bars": int(hold),
            }
            cands.append(book_of("DOGE-USDT-SWAP", "1h", "long", "range_reclaim",
                                 dsl["name"], dsl))

        print("clean_cands", len(cands), flush=True)
        winners, near, failed = [], [], []
        for i, book in enumerate(cands):
            # never push ADA
            if book["symbol"] == "ADA-USDT-SWAP":
                continue
            if book["symbol"] == "XRP-USDT-SWAP" and book["timeframe"] == "15m":
                continue
            passed, s, packs = eval_one(book)
            if s.get("error"):
                print("ERR", s, flush=True)
                failed.append(s)
                continue
            if s.get("quick") and s.get("full"):
                print("FULL", s, flush=True)
                sf = f2.run_sim_formal(book, packs)
                row = {
                    "summary": s, "sim": sf.get("sim"), "formal": sf.get("formal"),
                    "pending": sf.get("pending"), "failed_step": sf.get("failed_step"),
                    "ok": not sf.get("failed_step"),
                }
                winners.append(row)
                print("SF", row.get("failed_step"), (row.get("pending") or {}).get("key"), flush=True)
                if sum(1 for w in winners if w.get("ok")) >= 2:
                    break
            elif s.get("quick"):
                near.append(s)
                print("NEAR", s, flush=True)
            else:
                failed.append(s)
                if i % 10 == 0:
                    print("..", s.get("key"), s.get("fp"), s.get("dest"), s.get("failed_step"), flush=True)

        near.sort(key=lambda x: (
            float(x.get("friction_sh") if x.get("friction_sh") is not None else -99),
            float(x.get("mc_beat") or 0),
            float(x.get("mean") or -99),
        ), reverse=True)

        pending_keys = [w["pending"]["key"] for w in winners if (w.get("pending") or {}).get("ok")]
        out = {
            "ok": len(pending_keys) > 0,
            "diag": diag,
            "pending_keys": pending_keys,
            "winners": winners,
            "near": near[:15],
            "n_failed": len(failed),
            "n_cands": len(cands),
        }
        open(os.path.join(OUT, "frost2_cont_clean.json"), "w").write(
            json.dumps(out, ensure_ascii=False, indent=2) + "\n")

        st_path = os.path.join(OUT, "frost2_cont_status.json")
        try:
            st = json.load(open(st_path))
        except Exception:
            st = {"op": "寒霜贰续"}
        st["gates"] = {"wf": ">=7/10", "dest": True, "mc90": True, "friction_ge0": True}
        st["cl_abandoned"] = True
        st["cl_abandon_reason"] = "full_friction_mc_unrecoverable; two hard-stop -22% trades"
        st["cl_best_near"] = {
            "key": "frost2c_cl_15m_cci_h12",
            "quick": True, "fp": 7, "dest": True,
            "friction_sh": -0.145, "mc_beat": 0.83, "failed_step": "full_friction_sharpe",
        }
        st["clean"] = {
            "ok": out["ok"], "pending_keys": pending_keys,
            "near": near[:5], "diag": diag,
        }
        keys = list(st.get("pending_keys") or [])
        for k in pending_keys:
            if k not in keys:
                keys.append(k)
        st["pending_keys"] = keys
        open(st_path, "w").write(json.dumps(st, ensure_ascii=False, indent=2) + "\n")
        print("DONE", {"ok": out["ok"], "pending": pending_keys,
                       "near0": near[0] if near else None}, flush=True)
        return 0 if out["ok"] else 1
    except Exception:
        traceback.print_exc()
        return 3
    finally:
        release()


if __name__ == "__main__":
    sys.exit(main())
