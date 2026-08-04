#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜贰 CL 15m ENTRY reconstruction — Round 1 ONLY."""
from __future__ import print_function

import copy
import json
import os
import re
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
OUT = "/root/auto_trade/dual_engine"
PREFIX = "frost2_cl_entry_r1"


def _write(name, obj):
    path = os.path.join(OUT, name)
    open(path, "w").write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


def original_book(extra_entry=None, tag="base"):
    """Best Quick-pass CL: entry cci>50; ORIGINAL exit (rsi<45 TP, prev_high20 inv, hold=12)."""
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 54.0}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.1}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
        {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 50.0}},
    ]
    if extra_entry:
        entry.extend(extra_entry)
    dsl = {
        "key": "frost2_cl_entry_r1_%s" % tag,
        "name": "寒霜贰-CL-15m-exhaustion_fade",
        "direction": "short",
        "entry": {"all": entry},
        "exit": {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]},
        "max_hold_bars": 12,
    }
    return {
        "symbol": "CL-USDT-SWAP", "timeframe": "15m", "direction": "short",
        "logic_class": "exhaustion_fade",
        "thesis": "entry reconstruction r1",
        "title": "寒霜贰-CL-15m-exhaustion_fade",
        "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"),
        "gate_mode": "frost2", "source": "cl_entry_r1",
    }


def augment_frame(frame):
    df = frame.copy()
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    body = (df["close"] - df["open"]).abs()
    upper_wick = df["high"] - df[["open", "close"]].max(axis=1)
    lower_wick = df[["open", "close"]].min(axis=1) - df["low"]
    df["range"] = rng
    df["body"] = body
    df["upper_wick"] = upper_wick
    df["lower_wick"] = lower_wick
    df["wick_ratio"] = upper_wick / rng
    df["close_loc"] = (df["close"] - df["low"]) / rng  # 1=close at high (buy pressure)
    df["atr_pct"] = df["atr14"] / df["close"]
    df["range_atr"] = rng / df["atr14"].replace(0, np.nan)
    df["dist_ema16_atr"] = (df["ema16"] - df["close"]) / df["atr14"].replace(0, np.nan)
    df["ret1"] = df["close"].pct_change(1)
    df["ret3"] = df["close"].pct_change(3)
    df["accel"] = df["ret1"] - df["close"].pct_change(1).shift(1)
    df["spread_proxy"] = rng / df["close"]  # no L2 depth; bar-range proxy
    # rolling percentiles (lookback 96 bars ~ 1 day of 15m)
    win = 96
    for col in ["atr_pct", "spread_proxy", "range", "upper_wick", "z20", "cci", "rsi14"]:
        df[col + "_pctl"] = df[col].rolling(win, min_periods=20).apply(
            lambda x: float(pd.Series(x).rank(pct=True).iloc[-1]), raw=False)
    # volume not in frame — mark unavailable
    df["volume_available"] = False
    df["depth_available"] = False
    return df


def row_snapshot(df, idx):
    if idx is None or idx < 1 or idx >= len(df):
        return None
    r = df.iloc[idx]
    r1 = df.iloc[idx - 1]
    keys = [
        "open", "high", "low", "close", "rsi14", "z20", "cci", "macd_stick",
        "atr14", "ema8", "ema16", "ema21", "prev_high20", "k", "d", "j",
        "h1_ema19", "h1_ema53", "h1_slope4", "h1_atr14",
        "range", "body", "upper_wick", "lower_wick", "wick_ratio", "close_loc",
        "atr_pct", "range_atr", "dist_ema16_atr", "ret1", "ret3", "accel",
        "spread_proxy",
        "atr_pct_pctl", "spread_proxy_pctl", "range_pctl", "upper_wick_pctl",
        "z20_pctl", "cci_pctl", "rsi14_pctl",
    ]
    snap = {k: (None if pd.isna(r.get(k)) else float(r.get(k))) for k in keys if k in df.columns}
    snap["ts"] = str(df.index[idx])
    snap["idx"] = int(idx)
    snap["pre_bar_close"] = float(r1["close"])
    snap["pre_bar_ret"] = float((r["close"] / r1["close"]) - 1.0) if r1["close"] else None
    # microstructure availability
    snap["micro_note"] = (
        "NO 5-level depth/volume on server frame; proxies: spread_proxy=bar_range/close, "
        "close_loc=(close-low)/(high-low) as imbalance proxy, atr_pct_pctl as vol percentile, "
        "upper_wick_pctl as adverse pressure proxy for shorts")
    snap["registered_primitives"] = {
        "exhaustion_short_shape": bool(
            float(r["rsi14"]) > 54 and float(r["z20"]) > 0.1
            and float(r["macd_stick"]) < 0 and float(r["close"]) < float(r["ema16"])
            and float(r["cci"]) > 50),
        "h1_down": bool(float(r["h1_ema19"]) < float(r["h1_ema53"])),
        "near_prev_high20": bool(
            (float(r["prev_high20"]) - float(r["close"])) / max(float(r["atr14"]), 1e-9) < 1.0),
    }
    return snap


def find_idx_by_time(df, entry_time):
    """Match trade entry_time to frame index."""
    if entry_time is None:
        return None
    ts = pd.Timestamp(entry_time)
    if ts in df.index:
        return int(df.index.get_loc(ts))
    # nearest
    try:
        pos = df.index.get_indexer([ts], method="nearest")[0]
        return int(pos)
    except Exception:
        return None


def build_package():
    book = original_book(tag="snapshot_base")
    frame = augment_frame(d._frame("CL-USDT-SWAP", "15m"))
    bt = d._backtest(book["dsl"], "CL-USDT-SWAP", "15m", "observed_base")
    trades = bt.get("trades") or []

    losers, winners = [], []
    for t in trades:
        pnl = float(t.get("pnl_ratio") or 0)
        et = t.get("entry_time")
        idx = find_idx_by_time(frame, et)
        # prefer trade entry_index if consistent
        ti = t.get("entry_index")
        if ti is not None and 0 <= int(ti) < len(frame):
            # verify time proximity
            if abs(int(ti) - (idx or int(ti))) <= 5:
                idx = int(ti)
        snap = row_snapshot(frame, idx)
        row = {
            "entry_time": et,
            "exit_time": t.get("exit_time"),
            "pnl_ratio": pnl,
            "exit_type": t.get("exit_type"),
            "stop_loss": t.get("stop_loss"),
            "entry_index": t.get("entry_index"),
            "exit_index": t.get("exit_index"),
            "entry_price_close": (snap or {}).get("close"),
            "snapshot": snap,
            "entry_conditions": t.get("entry_conditions"),
        }
        if t.get("stop_loss") or pnl < -0.15:
            losers.append(row)
        elif pnl > 0:
            winners.append(row)

    winners_sorted = sorted(winners, key=lambda x: -float(x.get("pnl_ratio") or 0))
    winners_top = winners_sorted[: max(5, min(8, len(winners_sorted)))]

    # quantitative contrast
    def avg_field(rows, field):
        vals = []
        for r in rows:
            s = r.get("snapshot") or {}
            v = s.get(field)
            if v is not None and not (isinstance(v, float) and np.isnan(v)):
                vals.append(float(v))
        if not vals:
            return None
        return {"n": len(vals), "mean": round(sum(vals) / len(vals), 6),
                "min": round(min(vals), 6), "max": round(max(vals), 6)}

    contrast_fields = [
        "rsi14", "z20", "cci", "atr_pct", "atr_pct_pctl", "spread_proxy",
        "spread_proxy_pctl", "upper_wick", "upper_wick_pctl", "wick_ratio",
        "close_loc", "range_atr", "dist_ema16_atr", "ret1", "ret3", "accel",
        "macd_stick",
    ]
    contrast = {}
    for f in contrast_fields:
        contrast[f] = {"losers": avg_field(losers, f), "winners": avg_field(winners_top, f)}

    pkg = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "round": 1,
        "depth_available": False,
        "volume_available": False,
        "proxy_note": "Using OHLC/feature proxies; no 5-level depth on server",
        "current_entry_dsl": book["dsl"]["entry"],
        "current_exit_dsl": book["dsl"]["exit"],
        "max_hold_bars": book["dsl"]["max_hold_bars"],
        "losers": losers,
        "winners_sample": winners_top,
        "all_winner_entry_times": [w.get("entry_time") for w in winners],
        "n_trades": len(trades),
        "n_winners": len(winners),
        "n_losers_hard": len(losers),
        "contrast": contrast,
        "baseline_pnls": [float(t.get("pnl_ratio") or 0) for t in trades],
    }
    _write("%s_package.json" % PREFIX, pkg)
    return pkg, book, frame, trades, winners, losers


GLM_PROMPT = """你是GLM-5.2入场过滤器设计师。只输出一个JSON对象，禁止Markdown、禁止省略号占位。
任务：CL-USDT-SWAP 15m short exhaustion。两笔约-22%硬止损来自入场质量，禁止改出场。
服务器无五档深度/成交量；已给 OHLC 代理特征与 losers/winners 对比。

必须给出：
1) losers vs winners 可量化微观差异（具体数字）
2) **唯一一条**可写入 DSL 的入场预过滤（只用可用特征：rsi14,z20,cci,macd_stick,atr14,close,ema*,prev_high20,k,d,j,h1_*）
3) 该过滤必须在回放中拦截两笔亏损入场，并保留原盈利单≥70%

JSON严格格式（数值必须是数字，不要字符串公式以外的空话）：
{
  "loser_vs_winner_diffs":[{"feature":"rsi14","loser_mean":0,"winner_mean":0,"diff":0,"note":"..."}],
  "entry_filter":{
    "feature":"rsi14",
    "op":"gt",
    "value":56.0,
    "why":"...",
    "expected_block_losers":2,
    "expected_keep_winner_pct":70
  },
  "dsl_entry_extra":{"left":{"feature":"rsi14"},"op":"gt","right":{"value":56.0}},
  "note_zh":"..."
}
"""


def ask_glm(pkg):
    # compact payload for GLM
    user = {
        "losers_compact": [{
            "entry_time": x.get("entry_time"),
            "entry_price": x.get("entry_price_close"),
            "pnl": x.get("pnl_ratio"),
            "snap": {k: (x.get("snapshot") or {}).get(k) for k in (
                "rsi14", "z20", "cci", "macd_stick", "atr_pct", "atr_pct_pctl",
                "spread_proxy_pctl", "upper_wick_pctl", "wick_ratio", "close_loc",
                "range_atr", "dist_ema16_atr", "ret1", "accel", "k", "j")}
        } for x in pkg.get("losers") or []],
        "winners_compact": [{
            "entry_time": x.get("entry_time"),
            "entry_price": x.get("entry_price_close"),
            "pnl": x.get("pnl_ratio"),
            "snap": {k: (x.get("snapshot") or {}).get(k) for k in (
                "rsi14", "z20", "cci", "macd_stick", "atr_pct", "atr_pct_pctl",
                "spread_proxy_pctl", "upper_wick_pctl", "wick_ratio", "close_loc",
                "range_atr", "dist_ema16_atr", "ret1", "accel", "k", "j")}
        } for x in pkg.get("winners_sample") or []],
        "contrast": pkg.get("contrast"),
        "current_entry": pkg.get("current_entry_dsl"),
        "constraints": {
            "block_both_losers": True,
            "keep_winners_ge_pct": 70,
            "features_allowed": [
                "rsi14", "z20", "cci", "macd_stick", "atr14", "close",
                "ema6", "ema8", "ema16", "ema21", "prev_high20", "k", "d", "j",
                "h1_ema19", "h1_ema53", "h1_slope4", "h1_atr14"],
            "no_depth_no_volume": True,
        },
    }
    ai = d._ai_json("glm", GLM_PROMPT, user, max_tokens=1200, temperature=0.1)
    parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
    raw = ai.get("raw_preview") or ai.get("content") or ""
    if not parsed and raw:
        m = re.search(r"\{[\s\S]*\}", str(raw))
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
    # reject placeholder
    if parsed:
        ef = parsed.get("entry_filter") or {}
        if str(ef.get("why", "")).strip() in ("...", "") and str(
                (parsed.get("note_zh") or "")).strip() in ("...", ""):
            # still usable if value numeric
            if not isinstance(ef.get("value"), (int, float)):
                parsed = None
    _write("%s_glm.json" % PREFIX, {
        "ok": ai.get("ok"), "parsed": parsed, "error": ai.get("error"),
        "raw_preview": str(raw)[:2500],
    })
    return parsed, ai


def codex_fallback_filter(pkg):
    """Derive one quantifiable filter from contrast: block both losers, keep ≥70% winners."""
    losers = pkg.get("losers") or []
    winners = pkg.get("winners_sample") or []
    all_win_times = pkg.get("all_winner_entry_times") or [
        w.get("entry_time") for w in winners]

    # candidates that can express in DSL FEATURES
    # From prior autopsy: losers rsi~54.3-54.4 — raise rsi floor
    # Also check atr_pct_pctl, upper_wick_pctl, close_loc, z20
    candidates = []

    def snaps(rows):
        return [r.get("snapshot") or {} for r in rows]

    L = snaps(losers)
    # use ALL winners from package via rebuilding — winners_sample only top;
    # retention evaluated later on full list
    W = snaps(pkg.get("winners_sample") or [])

    # rsi14 > X: X must be > max(loser rsi) and keep most winners
    loser_rsi = [float(s["rsi14"]) for s in L if s.get("rsi14") is not None]
    if loser_rsi:
        thr = round(max(loser_rsi) + 0.05, 2)  # just above both losers
        candidates.append({
            "feature": "rsi14", "op": "gt", "value": thr,
            "why": "losers entered at marginal rsi~54.3; require rsi>%.2f" % thr,
            "dsl_entry_extra": {
                "left": {"feature": "rsi14"}, "op": "gt",
                "right": {"value": float(thr)},
            },
        })
        # also try 55.0 / 56.0
        for v in (55.0, 56.0, 57.0):
            if v > max(loser_rsi):
                candidates.append({
                    "feature": "rsi14", "op": "gt", "value": v,
                    "why": "stronger exhaustion rsi>%.1f blocks marginal losers" % v,
                    "dsl_entry_extra": {
                        "left": {"feature": "rsi14"}, "op": "gt",
                        "right": {"value": float(v)},
                    },
                })

    # z20 > X above max loser z
    loser_z = [float(s["z20"]) for s in L if s.get("z20") is not None]
    if loser_z:
        thr = round(max(loser_z) + 0.01, 3)
        candidates.append({
            "feature": "z20", "op": "gt", "value": thr,
            "why": "losers had weak z20; require z20>%.3f" % thr,
            "dsl_entry_extra": {
                "left": {"feature": "z20"}, "op": "gt",
                "right": {"value": float(thr)},
            },
        })

    # cci > X
    loser_cci = [float(s["cci"]) for s in L if s.get("cci") is not None]
    if loser_cci:
        # both losers had cci 80-90; winners may be higher — use max loser + epsilon won't work if winners lower
        # instead require cci > 100 to demand stronger extension IF losers are below
        for v in (100.0, 120.0, 150.0):
            if all(c < v for c in loser_cci):
                candidates.append({
                    "feature": "cci", "op": "gt", "value": v,
                    "why": "require stronger CCI extension >%.0f (losers were below)" % v,
                    "dsl_entry_extra": {
                        "left": {"feature": "cci"}, "op": "gt",
                        "right": {"value": float(v)},
                    },
                })

    # atr14 upper bound: if losers have higher atr_pct
    # atr14 < value — need absolute level from loser snaps
    loser_atr = [float(s["atr14"]) for s in L if s.get("atr14") is not None]
    win_atr = [float(s["atr14"]) for s in W if s.get("atr14") is not None]
    if loser_atr and win_atr:
        # if losers atr higher, cap atr14 < midpoint
        if np.mean(loser_atr) > np.mean(win_atr):
            thr = round(float(min(loser_atr)) - 1e-6, 5)
            # only if some winners below
            candidates.append({
                "feature": "atr14", "op": "lt", "value": thr,
                "why": "block high-ATR regime where 0.9%% stop hits; atr14<%.5f" % thr,
                "dsl_entry_extra": {
                    "left": {"feature": "atr14"}, "op": "lt",
                    "right": {"value": float(thr)},
                },
            })

    # k < 80 / j < 90 — if losers overextended in stoch
    return candidates


def passes_extra(snap, extra):
    if not snap or not extra:
        return False
    feat = ((extra.get("left") or {}).get("feature"))
    op = extra.get("op")
    right = extra.get("right") or {}
    if "value" in right:
        rv = float(right["value"])
        lv = snap.get(feat)
        if lv is None:
            return False
        lv = float(lv)
        if op == "gt":
            return lv > rv
        if op == "gte":
            return lv >= rv
        if op == "lt":
            return lv < rv
        if op == "lte":
            return lv <= rv
    elif "feature" in right:
        rv = snap.get(right["feature"])
        lv = snap.get(feat)
        if lv is None or rv is None:
            return False
        lv, rv = float(lv), float(rv)
        if op == "gt":
            return lv > rv
        if op == "lt":
            return lv < rv
    return False


def evaluate_filter_on_snapshots(extra, losers, winners_all_snaps):
    blocked = sum(1 for L in losers if not passes_extra(L.get("snapshot") or {}, extra))
    # "blocked" = filter rejects entry = NOT passes_extra
    blocked = sum(1 for L in losers if not passes_extra(L.get("snapshot") or {}, extra))
    kept = sum(1 for W in winners_all_snaps if passes_extra(W.get("snapshot") or {}, extra))
    total_w = len(winners_all_snaps) or 1
    return {
        "losers_blocked": blocked,
        "losers_total": len(losers),
        "winners_kept": kept,
        "winners_total": len(winners_all_snaps),
        "winner_retention_pct": round(100.0 * kept / float(total_w), 2),
    }


def pick_best_candidate(candidates, losers, winners_all):
    scored = []
    for c in candidates:
        extra = c.get("dsl_entry_extra")
        ev = evaluate_filter_on_snapshots(extra, losers, winners_all)
        score = (
            1000 * int(ev["losers_blocked"] == 2)
            + 100 * int(ev["losers_blocked"] >= 1)
            + ev["winner_retention_pct"]
        )
        scored.append({**c, "replay": ev, "score": score})
    scored.sort(key=lambda x: -x["score"])
    # prefer block 2 losers AND retention >= 70
    for s in scored:
        if s["replay"]["losers_blocked"] >= 2 and s["replay"]["winner_retention_pct"] >= 70:
            return s, scored
    # else best score
    return (scored[0] if scored else None), scored


def run_gates(book):
    q = f2.quick_suite(book)
    bm = q.get("base_metrics") or {}
    out = {
        "quick": bool(q.get("quick_pass")),
        "fp": bm.get("fold_positive"),
        "folds": bm.get("folds"),
        "tr": bm.get("trades"),
        "wr": bm.get("win_rate_pct"),
        "sh": bm.get("sharpe"),
        "dest": (q.get("logic_destruction") or {}).get("pass"),
        "failed_step": q.get("failed_step"),
    }
    fr_m = ((q.get("extreme_friction") or {}).get("metrics") or {})
    out["friction_sharpe"] = fr_m.get("sharpe")
    if not q.get("quick_pass"):
        out["full"] = False
        return out, q
    full = f2.full_suite(book, q)
    out["full"] = bool(full.get("full_pass"))
    out["friction_sharpe"] = (full.get("full") or {}).get("friction_sharpe")
    out["friction_pass"] = bool((full.get("full") or {}).get("friction_sharpe_ge_0"))
    mc = (full.get("full") or {}).get("mc") or {}
    out["mc_beat"] = mc.get("beat_ratio")
    out["mc_actual"] = mc.get("actual_final")
    out["mc_pass"] = bool(mc.get("pass"))
    out["failed_step"] = full.get("failed_step")
    out["_packs"] = full
    return out, full


def verify_replay(book, frame, loser_times, winner_times):
    """Re-backtest and count how many loser entry_times still appear / winners retained."""
    bt = d._backtest(book["dsl"], "CL-USDT-SWAP", "15m", "observed_base")
    trades = bt.get("trades") or []
    times = set(t.get("entry_time") for t in trades)
    losers_still = [t for t in loser_times if t in times]
    winners_kept = [t for t in winner_times if t in times]
    return {
        "losers_intercepted": len(loser_times) - len(losers_still),
        "losers_still_present": losers_still,
        "losers_total": len(loser_times),
        "winners_kept": len(winners_kept),
        "winners_total": len(winner_times),
        "winner_retention_pct": round(
            100.0 * len(winners_kept) / float(len(winner_times) or 1), 2),
        "new_trade_n": len(trades),
        "new_pnls": [float(t.get("pnl_ratio") or 0) for t in trades],
        "new_hard_stops": sum(1 for t in trades if t.get("stop_loss") or float(t.get("pnl_ratio") or 0) < -0.15),
    }


def main():
    print("=== CL ENTRY R1 START ===", flush=True)
    pkg, base_book, frame, trades, winners, losers = build_package()
    print("PACKAGE losers", len(losers), "winners", len(winners),
          "contrast_rsi", (pkg.get("contrast") or {}).get("rsi14"), flush=True)

    parsed, ai = ask_glm(pkg)
    print("GLM", ai.get("ok"), ai.get("error"),
          (parsed or {}).get("entry_filter") if parsed else None, flush=True)

    candidates = codex_fallback_filter(pkg)
    glm_cand = None
    if parsed and (parsed.get("dsl_entry_extra") or parsed.get("entry_filter")):
        ef = parsed.get("entry_filter") or {}
        extra = parsed.get("dsl_entry_extra")
        if not extra and ef:
            extra = {
                "left": {"feature": ef.get("feature")},
                "op": ef.get("op"),
                "right": {"value": float(ef.get("value"))},
            }
        # validate structure
        try:
            feat = (extra.get("left") or {}).get("feature")
            val = (extra.get("right") or {}).get("value")
            if feat and extra.get("op") and val is not None:
                glm_cand = {
                    "feature": feat,
                    "op": extra.get("op"),
                    "value": float(val),
                    "why": ef.get("why") or parsed.get("note_zh") or "glm",
                    "dsl_entry_extra": extra,
                    "source": "glm",
                }
                candidates.insert(0, glm_cand)
        except Exception as exc:
            print("glm_cand_err", exc, flush=True)

    pick, scored = pick_best_candidate(candidates, losers, winners)
    _write("%s_candidates.json" % PREFIX, {"picked": pick, "scored": scored[:12]})
    if not pick:
        report = {
            "round": 1,
            "ok": False,
            "entered_review": False,
            "error": "no_viable_entry_filter",
            "glm_filter": None,
        }
        _write("%s_interim.json" % PREFIX, report)
        print("INTERIM", report, flush=True)
        return 1

    extra = pick["dsl_entry_extra"]
    # IMPORTANT: if filter is rsi14>X and base already has rsi14>54, REPLACE the rsi condition
    # rather than duplicating — cleaner entry
    book = original_book(tag="filt")
    entry_all = (book["dsl"].get("entry") or {}).get("all") or []
    feat = (extra.get("left") or {}).get("feature")
    op = extra.get("op")
    replaced = False
    if feat == "rsi14" and op in ("gt", "gte"):
        for row in entry_all:
            if (row.get("left") or {}).get("feature") == "rsi14" and row.get("op") in ("gt", "gte"):
                row["right"] = {"value": float((extra.get("right") or {}).get("value"))}
                replaced = True
                break
    if feat == "z20" and op in ("gt", "gte"):
        for row in entry_all:
            if (row.get("left") or {}).get("feature") == "z20" and row.get("op") in ("gt", "gte"):
                row["right"] = {"value": float((extra.get("right") or {}).get("value"))}
                replaced = True
                break
    if feat == "cci" and op in ("gt", "gte"):
        for row in entry_all:
            if (row.get("left") or {}).get("feature") == "cci" and row.get("op") in ("gt", "gte"):
                row["right"] = {"value": float((extra.get("right") or {}).get("value"))}
                replaced = True
                break
    if not replaced:
        entry_all.append(copy.deepcopy(extra))
    book["dsl"]["entry"]["all"] = entry_all
    # restore original exit explicitly
    book["dsl"]["exit"] = {
        "any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
        ]
    }
    book["dsl"]["max_hold_bars"] = 12
    book["dsl"]["key"] = "frost2_cl_entry_r1_v1"
    book["dsl"] = f2.ensure_dsl(book["dsl"], "CL-USDT-SWAP", "15m")

    filter_desc = {
        "feature": pick.get("feature"),
        "op": pick.get("op"),
        "value": pick.get("value"),
        "why": pick.get("why"),
        "source": pick.get("source", "codex_fallback"),
        "dsl_entry_extra": extra,
        "replaced_existing": replaced,
        "exact_condition": "%s %s %s" % (pick.get("feature"), pick.get("op"), pick.get("value")),
    }
    print("FILTER", filter_desc, flush=True)

    loser_times = [L.get("entry_time") for L in losers]
    winner_times = [W.get("entry_time") for W in winners]
    replay = verify_replay(book, frame, loser_times, winner_times)
    print("REPLAY", replay, flush=True)

    gates, packs = run_gates(book)
    print("GATES", {k: gates.get(k) for k in gates if k != "_packs"}, flush=True)

    interim = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "round": 1,
        "stop_after_round": True,
        "glm_filter_added": filter_desc,
        "两笔亏损是否被拦截": {
            "intercepted_count": replay.get("losers_intercepted"),
            "total": replay.get("losers_total"),
            "still_present": replay.get("losers_still_present"),
            "blocked_both": replay.get("losers_intercepted") == 2,
        },
        "盈利单保留比例": replay.get("winner_retention_pct"),
        "replay": replay,
        "gates": {
            "WF": "%s/10 (need>=7) pass=%s" % (gates.get("fp"), gates.get("quick") and gates.get("dest")),
            "fold_positive": gates.get("fp"),
            "dest": gates.get("dest"),
            "quick_pass": gates.get("quick"),
            "MC_beat": gates.get("mc_beat"),
            "MC_pass": gates.get("mc_pass"),
            "friction_sharpe": gates.get("friction_sharpe"),
            "friction_pass": gates.get("friction_pass"),
            "full_pass": gates.get("full"),
            "failed_step": gates.get("failed_step"),
        },
        "dsl_entry_after": book["dsl"].get("entry"),
        "dsl_exit_restored": book["dsl"].get("exit"),
        "entered_review": False,
        "pending": None,
        "glm_raw_ok": ai.get("ok"),
        "snapshot_contrast": pkg.get("contrast"),
    }

    if gates.get("full"):
        sf = f2.run_sim_formal(book, packs)
        interim["sim"] = sf.get("sim")
        interim["formal"] = sf.get("formal")
        interim["pending"] = sf.get("pending")
        interim["sf_failed"] = sf.get("failed_step")
        interim["entered_review"] = bool((sf.get("pending") or {}).get("ok"))
        if interim["entered_review"]:
            st_path = os.path.join(OUT, "frost2_cont_status.json")
            try:
                st = json.load(open(st_path))
            except Exception:
                st = {}
            keys = list(st.get("pending_keys") or [])
            k = (sf.get("pending") or {}).get("key")
            if k and k not in keys:
                keys.append(k)
            st["pending_keys"] = keys
            st["ok_min_cl"] = True
            st["cl_entry_r1"] = interim
            open(st_path, "w").write(json.dumps(st, ensure_ascii=False, indent=2) + "\n")
        print("SF", interim.get("sf_failed"), interim.get("pending"), flush=True)

    _write("%s_interim.json" % PREFIX, interim)
    _write("%s_book.json" % PREFIX, {"book": book, "filter": filter_desc})

    # status stub
    st_path = os.path.join(OUT, "frost2_cont_status.json")
    try:
        st = json.load(open(st_path))
    except Exception:
        st = {}
    st["cl_entry_r1"] = {
        "filter": filter_desc.get("exact_condition"),
        "losers_intercepted": replay.get("losers_intercepted"),
        "winner_retention_pct": replay.get("winner_retention_pct"),
        "gates": interim["gates"],
        "entered_review": interim["entered_review"],
    }
    open(st_path, "w").write(json.dumps(st, ensure_ascii=False, indent=2) + "\n")

    print("=== ROUND1 INTERIM ===", flush=True)
    print(json.dumps({
        "GLM_filter": filter_desc.get("exact_condition"),
        "两笔亏损拦截": "%s/2" % replay.get("losers_intercepted"),
        "盈利单保留": "%s%%" % replay.get("winner_retention_pct"),
        "WF": gates.get("fp"),
        "dest": gates.get("dest"),
        "MC": gates.get("mc_beat"),
        "friction": gates.get("friction_sharpe"),
        "进入复核": interim["entered_review"],
    }, ensure_ascii=False, indent=2), flush=True)
    print("STOP after Round 1", flush=True)
    return 0 if interim.get("entered_review") else 1


if __name__ == "__main__":
    sys.exit(main())
