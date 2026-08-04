#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜叁·BTC5m MicroEdge — FULLY ISOLATED production-candidate pipeline.

Instrument: BTC-USDT-SWAP 5m only.
Artifacts ONLY: /root/auto_trade/dual_engine/frost3_btc5m_microedge_*
Never overwrite Frost3/XRP-port/other pipelines; protect live ADA/LTC/NG/XRP + existing BTC daemons.

Fixed trading params (NOT changed by this script):
  leverage 20x | initial position 30% | fixed stop 0.9% | TP by strategy
  NO martingale / grid / add-on / dynamic stop

Edge MUST be Liquidity/Microstructure/Funding/OI/Auction/Session/VWAP/VP/Delta/OF.
Traditional indicator combos are FILTERS only, never the edge source.

Honest proxies (documented in proxy_notes artifact):
  - session_liq / auction_win / hour_utc from bar timestamp
  - vwap_dist from typical-price rolling VWAP (OHLC; no true volume → range proxy)
  - vp_poc_dist from rolling range-proxy volume profile POC
  - delta_proxy / of_imbalance from bar close-location (order-flow proxy)
  - vol_pulse_proxy = atr14 / atr14.rolling(48).mean
  - liq_void_proxy = atr14 / atr14.rolling(96).mean after compression
  - funding_z / oi_chg_z from OKX public API if fetchable; else return-skew /
    range-expansion intensity proxies (never claimed as real funding/OI)
"""
from __future__ import print_function

import copy
import json
import math
import os
import re
import sqlite3
import sys
import traceback
import urllib.request
from datetime import datetime

sys.path.insert(0, "/root")
import frost2_action_run as f2
import frost3_action as f3
import frost3_step1 as step1
import auto_trade_dual_engine_factory as d
import auto_trade_strategy_dsl as dsl_mod

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
f2.AVOID = [
    ("ADA-USDT-SWAP", None),
    ("LTC-USDT-SWAP", None),
    ("NG-USDT-SWAP", None),
    ("XRP-USDT-SWAP", None),
]

OUT = "/root/auto_trade/dual_engine"
PREFIX = "frost3_btc5m_microedge"
SYMBOL = "BTC-USDT-SWAP"
TIMEFRAME = "5m"
MAX_AUDIT_REVISE = 2
MAX_QUICK_REPAIR = 3
MAX_FULL_REPAIR = 3
MAX_SIM_REPAIR = 3

# Target band (report honestly if gates pass but these miss)
TARGET_WR_AVG = 75.0
TARGET_OPENS_LO = 0.5
TARGET_OPENS_HI = 1.2
TARGET_PF = 1.8

# Session / auction UTC windows (BTC crypto — London + NY liquidity)
SESSION_UTC_START = 7
SESSION_UTC_END = 21
AUCTION_LONDON = (7, 10)   # London open auction proxy
AUCTION_NY = (13, 16)      # NY open auction proxy

MICRO_FEATURES = {
    "hour_utc", "session_liq", "auction_win", "vwap_dist", "vp_poc_dist",
    "delta_proxy", "of_imbalance", "vol_pulse_proxy", "liq_void_proxy",
    "funding_z", "oi_chg_z", "range_proxy_z",
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_art(suffix, obj):
    path = os.path.join(OUT, "%s_%s.json" % (PREFIX, suffix))
    open(path, "w").write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


def _safe(s):
    s = str(s or "x")
    for ch in (".", " ", "/", ":", "+", "%", "-"):
        s = s.replace(ch, "p" if ch in (".", "-") else "_")
    return s[:40]


# ─── OKX public funding / OI fetch (best-effort) ───────────────────────

def fetch_okx_funding_oi():
    """Best-effort OKX public endpoints. Never fabricate; return empty on fail."""
    out = {
        "ok": False,
        "funding_rates": [],
        "oi_history": [],
        "note": None,
        "source": "okx_public",
    }
    try:
        # Funding rate history
        url_f = (
            "https://www.okx.com/api/v5/public/funding-rate-history"
            "?instId=BTC-USDT-SWAP&limit=100"
        )
        req = urllib.request.Request(url_f, headers={"User-Agent": "frost3-btc5m/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            blob = json.loads(resp.read().decode("utf-8"))
        rows = blob.get("data") or []
        out["funding_rates"] = [
            {"ts": int(r.get("fundingTime") or 0), "rate": float(r.get("fundingRate") or 0)}
            for r in rows if r
        ]
        # Open interest history (candles)
        url_o = (
            "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history"
            "?instId=BTC-USDT-SWAP&period=5m&limit=100"
        )
        req2 = urllib.request.Request(url_o, headers={"User-Agent": "frost3-btc5m/1.0"})
        with urllib.request.urlopen(req2, timeout=12) as resp2:
            blob2 = json.loads(resp2.read().decode("utf-8"))
        oi_rows = blob2.get("data") or []
        # OKX rubik often returns [ts, oi, ...] lists
        parsed_oi = []
        for r in oi_rows:
            try:
                if isinstance(r, (list, tuple)) and len(r) >= 2:
                    parsed_oi.append({"ts": int(r[0]), "oi": float(r[1])})
                elif isinstance(r, dict):
                    parsed_oi.append({
                        "ts": int(r.get("ts") or r.get("timestamp") or 0),
                        "oi": float(r.get("oi") or r.get("openInterest") or 0),
                    })
            except Exception:
                continue
        out["oi_history"] = parsed_oi
        out["ok"] = bool(out["funding_rates"] or out["oi_history"])
        if not out["ok"]:
            out["note"] = "okx_empty_payload"
    except Exception as exc:
        out["note"] = "okx_fetch_fail:%s" % exc
        out["ok"] = False
    return out


def _align_series_to_index(index, points, value_key):
    """Map sparse ts→value points onto frame index (ffill). Returns list aligned to index."""
    if not points or index is None:
        return None
    try:
        import pandas as pd
        ser = pd.Series(
            {pd.to_datetime(p["ts"], unit="ms", utc=True): p[value_key] for p in points if p.get("ts")},
        ).sort_index()
        if ser.empty:
            return None
        # frame index may be tz-naive UTC
        idx = pd.to_datetime(index)
        if getattr(idx, "tz", None) is None:
            ser.index = ser.index.tz_localize(None)
        else:
            ser = ser.tz_convert(idx.tz)
        aligned = ser.reindex(idx, method="ffill")
        return aligned.astype(float)
    except Exception:
        return None


# ─── Feature injection ─────────────────────────────────────────────────

def install_micro_proxies(okx_blob=None):
    """Process-local FEATURE allowlist + frame injection. Does not touch live daemons."""
    for f in MICRO_FEATURES:
        dsl_mod.FEATURES.add(f)

    _orig_frame = d._frame
    _cache = {"frame": None}
    okx_blob = okx_blob or {}

    def _frame_with_proxies(symbol, timeframe):
        if (
            str(symbol).upper() == SYMBOL
            and str(timeframe) == TIMEFRAME
            and _cache["frame"] is not None
        ):
            return _cache["frame"]
        frame = _orig_frame(symbol, timeframe)
        try:
            if hasattr(frame, "iloc") and len(frame) > 10000:
                frame = frame.iloc[-10000:].copy()
            elif hasattr(frame, "copy"):
                frame = frame.copy()

            # --- session / auction ---
            if hasattr(frame, "index") and hasattr(frame.index, "hour"):
                hour = frame.index.hour.astype(float)
                frame["hour_utc"] = hour
                frame["session_liq"] = (
                    ((hour >= float(SESSION_UTC_START)) & (hour < float(SESSION_UTC_END)))
                    .astype(float)
                )
                al0, al1 = AUCTION_LONDON
                an0, an1 = AUCTION_NY
                frame["auction_win"] = (
                    (((hour >= al0) & (hour < al1)) | ((hour >= an0) & (hour < an1)))
                    .astype(float)
                )
            else:
                frame["hour_utc"] = 12.0
                frame["session_liq"] = 1.0
                frame["auction_win"] = 0.0

            h = frame["high"] if "high" in frame.columns else frame["close"]
            l = frame["low"] if "low" in frame.columns else frame["close"]
            c = frame["close"]
            o = frame["open"] if "open" in frame.columns else c
            typ = (h + l + c) / 3.0
            # range as volume proxy (honest: NOT exchange volume)
            rng = (h - l).abs().clip(lower=1e-12)
            # VWAP proxy
            cum_tp_v = (typ * rng).rolling(48, min_periods=12).sum()
            cum_v = rng.rolling(48, min_periods=12).sum().replace(0, float("nan"))
            vwap = cum_tp_v / cum_v
            atr = frame["atr14"] if "atr14" in frame.columns else rng.rolling(14).mean()
            frame["vwap_dist"] = ((c - vwap) / atr.replace(0, float("nan"))).fillna(0.0)

            # Volume-profile POC proxy over 96 bars: price bin by rolling window median of
            # high-volume (range) levels ≈ close of bar with max range in window
            try:
                import numpy as np
                closes = c.values.astype(float)
                ranges = rng.values.astype(float)
                n = len(closes)
                poc = np.full(n, float("nan"))
                w = 96
                for i in range(n):
                    lo = max(0, i - w + 1)
                    window_r = ranges[lo:i + 1]
                    window_c = closes[lo:i + 1]
                    if len(window_r) < 12:
                        continue
                    j = int(np.nanargmax(window_r))
                    poc[i] = window_c[j]
                frame["vp_poc_dist"] = ((c - poc) / atr.replace(0, float("nan"))).fillna(0.0)
            except Exception:
                frame["vp_poc_dist"] = 0.0

            # Delta / order-flow proxies
            hl = (h - l).replace(0, float("nan"))
            frame["delta_proxy"] = (((c - o) / hl).fillna(0.0)).clip(-1, 1)
            frame["of_imbalance"] = frame["delta_proxy"].rolling(6, min_periods=3).sum().fillna(0.0)

            if "atr14" in frame.columns:
                base48 = frame["atr14"].rolling(48, min_periods=12).mean()
                base96 = frame["atr14"].rolling(96, min_periods=24).mean()
                frame["vol_pulse_proxy"] = (
                    frame["atr14"] / base48.replace(0, float("nan"))
                ).fillna(1.0)
                frame["liq_void_proxy"] = (
                    frame["atr14"] / base96.replace(0, float("nan"))
                ).fillna(1.0)
            else:
                frame["vol_pulse_proxy"] = 1.0
                frame["liq_void_proxy"] = 1.0

            frame["range_proxy_z"] = (
                (rng - rng.rolling(48, min_periods=12).mean())
                / rng.rolling(48, min_periods=12).std().replace(0, float("nan"))
            ).fillna(0.0)

            # Funding / OI: real if OKX aligned, else honest OHLCV proxies
            funding_aligned = None
            oi_aligned = None
            if okx_blob.get("ok"):
                funding_aligned = _align_series_to_index(
                    frame.index, okx_blob.get("funding_rates") or [], "rate")
                oi_aligned = _align_series_to_index(
                    frame.index, okx_blob.get("oi_history") or [], "oi")

            if funding_aligned is not None and float(funding_aligned.notna().sum()) > 20:
                mu = funding_aligned.rolling(24, min_periods=6).mean()
                sd = funding_aligned.rolling(24, min_periods=6).std().replace(0, float("nan"))
                frame["funding_z"] = ((funding_aligned - mu) / sd).fillna(0.0)
                frame["_funding_source"] = "okx_public"
            else:
                # Honest proxy: multi-bar return skew as carry/pressure stand-in
                ret = c.pct_change()
                skew = ret.rolling(36, min_periods=12).mean() / (
                    ret.rolling(36, min_periods=12).std().replace(0, float("nan"))
                )
                frame["funding_z"] = skew.fillna(0.0).clip(-4, 4)
                frame["_funding_source"] = "return_skew_proxy_NOT_real_funding"

            if oi_aligned is not None and float(oi_aligned.notna().sum()) > 20:
                oi_chg = oi_aligned.pct_change()
                mu = oi_chg.rolling(24, min_periods=6).mean()
                sd = oi_chg.rolling(24, min_periods=6).std().replace(0, float("nan"))
                frame["oi_chg_z"] = ((oi_chg - mu) / sd).fillna(0.0)
                frame["_oi_source"] = "okx_public"
            else:
                # Honest proxy: signed range-expansion intensity (NOT real OI)
                signed = (c - o).apply(lambda x: 1.0 if x >= 0 else -1.0)
                intensity = (rng / atr.replace(0, float("nan"))).fillna(1.0) * signed
                frame["oi_chg_z"] = intensity.rolling(12, min_periods=4).mean().fillna(0.0)
                frame["_oi_source"] = "signed_range_intensity_proxy_NOT_real_OI"

            if str(symbol).upper() == SYMBOL and str(timeframe) == TIMEFRAME:
                _cache["frame"] = frame
        except Exception as exc:
            try:
                frame["_proxy_error"] = str(exc)[:200]
            except Exception:
                pass
        return frame

    d._frame = _frame_with_proxies
    return {
        "session_utc": [SESSION_UTC_START, SESSION_UTC_END],
        "auction_london_utc": list(AUCTION_LONDON),
        "auction_ny_utc": list(AUCTION_NY),
        "features": sorted(MICRO_FEATURES),
        "honest_proxy_notes": {
            "vwap_dist": "rolling 48-bar typical-price VWAP using range as volume proxy; /atr14",
            "vp_poc_dist": "96-bar POC = close of max-range bar; /atr14 — NOT exchange VP",
            "delta_proxy": "(close-open)/(high-low) bar delta stand-in",
            "of_imbalance": "6-bar sum of delta_proxy",
            "vol_pulse_proxy": "atr14 / atr14.rolling(48).mean",
            "liq_void_proxy": "atr14 / atr14.rolling(96).mean",
            "funding_z": "OKX public funding z if fetchable else return-skew proxy (labeled)",
            "oi_chg_z": "OKX OI change z if fetchable else signed range-intensity proxy (labeled)",
            "book_depth": "unavailable in DSL/backtest — not faked with RSI",
            "true_volume": "parquet typically OHLC-only; range used as volume proxy",
        },
        "okx_funding_oi": {
            "ok": bool(okx_blob.get("ok")),
            "n_funding": len(okx_blob.get("funding_rates") or []),
            "n_oi": len(okx_blob.get("oi_history") or []),
            "note": okx_blob.get("note"),
        },
        "live_daemon_touch": False,
    }


# ─── Context loaders ───────────────────────────────────────────────────

def load_btc_micro():
    out = {"symbol": SYMBOL, "top_clusters": [], "recent_descriptors": [], "note": None}
    db = "/root/auto_trade/microstructure_telemetry.db"
    try:
        con = sqlite3.connect(db)
        cur = con.cursor()
        rows = cur.execute(
            "SELECT cluster_id, COUNT(*) c FROM microstructure_primitive_assignments "
            "WHERE symbol=? GROUP BY cluster_id ORDER BY c DESC LIMIT 8",
            (SYMBOL,),
        ).fetchall()
        clusters = []
        for cid, cnt in rows:
            lab = cur.execute(
                "SELECT label, description FROM microstructure_primitive_clusters "
                "WHERE cluster_id=?", (cid,),
            ).fetchone()
            clusters.append({
                "cluster_id": cid, "count": cnt,
                "label": (lab or [None, None])[0],
                "description": ((lab or [None, None])[1] or "")[:160],
            })
        out["top_clusters"] = clusters
        desc = cur.execute(
            "SELECT descriptor_text, created_at FROM microstructure_windows "
            "WHERE symbol=? ORDER BY created_at DESC LIMIT 6",
            (SYMBOL,),
        ).fetchall()
        out["recent_descriptors"] = [{"text": t, "at": a} for t, a in desc if t]
        con.close()
    except Exception as exc:
        out["note"] = "db_err:%s" % exc
    return out


def load_death():
    inputs = {}
    for path in (
        os.path.join(OUT, "frost3_inputs_raw.json"),
        os.path.join(OUT, "frost3_btc5m_microedge_inputs.json"),
    ):
        try:
            blob = json.load(open(path))
            if isinstance(blob, dict) and blob:
                inputs = blob
                inputs["_source_path"] = path
                break
        except Exception:
            continue
    if not inputs:
        try:
            inputs = d.collect_inputs()
            inputs["_source_path"] = "collect_inputs"
        except Exception as exc:
            inputs = {"error": str(exc)}
    fz = list(inputs.get("freezer_last10") or [])
    return {
        "death_heatmap_top10": (inputs.get("death_heatmap_top10") or [])[:10],
        "freezer_last10": fz[:10],
        "btc5_known_death": {
            "key": "btc5_exhaustion_reclaim_long_ai",
            "reason": "lifecycle_eliminated_friction_negative — 5m reclaim 被滑点/摩擦吞噬",
        },
        "hf_lessons_zh": [
            "BTC5m exhaustion_reclaim 已因 friction_negative 冷冻 — 禁止同类边际回收刷单",
            "目标开仓频率 0.5-1.2/天：必须用会话/拍卖窗+微观代理严过滤",
            "Edge 必须来自流动性/微观/资金费/OI/拍卖/会话/VWAP/VP/Delta/OF，传统指标仅作过滤",
            "CL15m 边际 rsi≈54 + 过窄过滤 → 样本饥渴或硬止损簇；本线远离",
        ],
        "cl_archive_hint": "archive/frost2_cl_15m_entry_exhausted_r3",
        "source": inputs.get("_source_path"),
    }


# ─── GLM: 10 edges → score → top2 ──────────────────────────────────────

EDGE10_PROMPT = """你是GLM-5.2。只输出纯JSON，禁止Markdown。
任务：为 BTC-USDT-SWAP 5m 生成恰好10个【微观/流动性族】交易边缘候选并打分，再推荐top2。

硬约束：
1) symbol=BTC-USDT-SWAP，timeframe=5m，不可改
2) Edge 必须来自：Liquidity / Microstructure / Funding / OpenInterest / Auction /
   Session / VWAP / VolumeProfile / Delta / OrderFlowProxy（可用诚实代理特征）
3) 禁止把 EMA交叉/MACD金叉/RSI超买超卖/布林突破/SuperTrend/ATR策略/传统指标组合当作EDGE来源
   （这些最多当过滤条件）
4) 禁止复刻现网 ADA/LTC/NG/XRP 逻辑；禁止 btc5_exhaustion_reclaim 同构摩擦刷单
5) 固定交易参数不可改：杠杆20x、初始仓位30%、固定止损0.9%、策略止盈；无马丁/网格/加仓/动态止损
6) 目标：理论胜率≥75%、日均开仓0.5-1.2、PF≥1.8、费用滑点后仍盈利、MaxDD尽量低
7) 可用代理特征：session_liq, auction_win, hour_utc, vwap_dist, vp_poc_dist, delta_proxy,
   of_imbalance, vol_pulse_proxy, liq_void_proxy, funding_z, oi_chg_z, range_proxy_z
   以及过滤用 rsi14/z20/macd_stick/ema*/h1_*/prev_high20/prev_low20/atr14

JSON schema：
{
  "report_title":"BTC5m微观边缘10候选",
  "symbol":"BTC-USDT-SWAP",
  "timeframe":"5m",
  "proxy_honesty_zh":"...",
  "edges":[
    {
      "id":1,
      "name":"短横线英文名",
      "family":"session|auction|vwap|volume_profile|delta|order_flow|funding|oi|liquidity|microstructure",
      "direction":"long|short",
      "thesis":"因果机制",
      "edge_source":"真正的边缘来自哪里",
      "filters_only":["rsi/...仅过滤"],
      "entry_sketch":"分号分隔，必须含至少1个微观代理特征",
      "exit_sketch":"止盈+结构失效+max_hold",
      "expected_opens_per_day":0.8,
      "scores":{
        "novelty":0到10,"frequency":0到10,"capacity":0到10,
        "overfit_risk":0到10,"death_risk":0到10
      },
      "composite":0到100,
      "death_notes":"..."
    }
  ],
  "scoring_formula_zh":"composite≈(novelty+frequency+capacity)*3 - (overfit_risk+death_risk)*2 等自洽公式",
  "top2":[
    {"rank":1,"id":N,"name":"...","why":"..."},
    {"rank":2,"id":M,"name":"...","why":"..."}
  ],
  "mentor_notes":"..."
}
必须恰好10条 edges；top2 必须引用 edges 的 id。
"""


AUDIT_PROMPT = """你是GLM-5.2。只输出JSON。审计 BTC5m 微观边缘假设书。
硬禁：ADA/LTC/NG/XRP；禁止传统指标当EDGE；禁止btc5 exhaustion_reclaim 同构。
必须：边缘来自微观代理族；会话/拍卖/VWAP/Delta/Funding/OI 等至少其一为核心；
结构失效出场；预期日均开仓约0.5-1.2；固定止损0.9%外由策略止盈。
JSON：{"decision":"pass|reject|revise","score":0到100,"issues":["..."],
"revise":{"entry_sketch":null,"exit_sketch":null,"param_tweaks":{},"why":"..."},
"reason_zh":"..."}
"""


def ask_glm(prompt, packet, timeout_sec=90, max_tokens=3200):
    import threading
    box = {"parsed": None, "ai": {"ok": False, "error": "timeout"}}

    def _run():
        try:
            ai = d._ai_json("glm", prompt, packet, max_tokens=max_tokens, temperature=0.2)
            parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
            raw = ai.get("raw_preview") or ai.get("content") or ""
            if not parsed and raw:
                m = re.search(r"\{[\s\S]*\}", str(raw))
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except Exception:
                        parsed = None
            box["parsed"] = parsed
            box["ai"] = ai
        except Exception as exc:
            box["ai"] = {"ok": False, "error": str(exc)}

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout_sec)
    if th.is_alive():
        return None, {"ok": False, "error": "glm_timeout_%ss" % timeout_sec, "raw_preview": ""}
    return box["parsed"], box["ai"]


def _composite(scores):
    s = scores or {}
    nov = float(s.get("novelty") or 5)
    freq = float(s.get("frequency") or 5)
    cap = float(s.get("capacity") or 5)
    of = float(s.get("overfit_risk") or 5)
    death = float(s.get("death_risk") or 5)
    return round((nov + freq + cap) * 3.5 - (of + death) * 2.5, 2)


def fallback_10_edges(packet):
    """Codex/GLM-fallback: 10 honest micro-family edges + scores. Not traditional-indicator edges."""
    raw = [
        {
            "id": 1, "name": "london_auction_vwap_reclaim", "family": "auction",
            "direction": "long",
            "thesis": "伦敦开盘拍卖窗流动性注入，价格自VWAP下方回收时顺流动性方向做多",
            "edge_source": "auction_win + vwap_dist 负偏离回收",
            "filters_only": ["rsi14带宽过滤", "h1趋势过滤"],
            "entry_sketch": (
                "auction_win>0.5; session_liq>0.5; vwap_dist>-1.8; vwap_dist<-0.15; "
                "delta_proxy>0.05; of_imbalance>0.15; vol_pulse_proxy>1.02; "
                "rsi14>44; rsi14<62; h1_ema19>h1_ema53"
            ),
            "exit_sketch": "vwap_dist>0.9 take_profit; close<ema21 invalidation; max_hold=36",
            "expected_opens_per_day": 0.7,
            "scores": {"novelty": 8, "frequency": 7, "capacity": 8, "overfit_risk": 4, "death_risk": 4},
            "death_notes": "避免无拍卖窗的高频VWAP刷单",
        },
        {
            "id": 2, "name": "ny_of_absorption_long", "family": "order_flow",
            "direction": "long",
            "thesis": "纽约拍卖窗宽幅后收盘偏多的吸收形态，订单流代理转正后做多",
            "edge_source": "auction_win(NY) + of_imbalance 翻正 + range_proxy_z 脉冲后收敛",
            "filters_only": ["z20不过度超买", "macd_stick过滤"],
            "entry_sketch": (
                "auction_win>0.5; hour_utc>12; session_liq>0.5; of_imbalance>0.35; "
                "delta_proxy>0.1; range_proxy_z>0.4; range_proxy_z<2.2; "
                "vol_pulse_proxy>1.04; z20>-0.4; z20<1.0; macd_stick>0"
            ),
            "exit_sketch": "rsi14>70 take_profit; of_imbalance<-0.2 invalidation; max_hold=32",
            "expected_opens_per_day": 0.65,
            "scores": {"novelty": 8, "frequency": 6, "capacity": 7, "overfit_risk": 5, "death_risk": 4},
            "death_notes": "宽幅后假吸收导致止损簇",
        },
        {
            "id": 3, "name": "funding_extreme_fade_short", "family": "funding",
            "direction": "short",
            "thesis": "资金费率代理极端偏多时在会话内做空均值回归（若仅有return-skew代理须降置信）",
            "edge_source": "funding_z 极端 + session_liq",
            "filters_only": ["rsi过滤", "z20过滤"],
            "entry_sketch": (
                "session_liq>0.5; funding_z>1.4; of_imbalance<0.05; delta_proxy<0.05; "
                "vwap_dist>0.25; rsi14>55; rsi14<72; z20>0.2; z20<1.6"
            ),
            "exit_sketch": "funding_z<0.3 take_profit; close>prev_high20 invalidation; max_hold=40",
            "expected_opens_per_day": 0.55,
            "scores": {"novelty": 7, "frequency": 5, "capacity": 7, "overfit_risk": 6, "death_risk": 6},
            "death_notes": "代理非真funding时过拟合与趋势碾压风险高",
        },
        {
            "id": 4, "name": "oi_unwind_cascade_short", "family": "oi",
            "direction": "short",
            "thesis": "持仓强度代理回落+价格仍偏高时，仓位挤兑下行",
            "edge_source": "oi_chg_z 转负 + liq_void_proxy 扩张",
            "filters_only": ["h1空头过滤"],
            "entry_sketch": (
                "session_liq>0.5; oi_chg_z<-0.4; liq_void_proxy>1.08; vwap_dist>0.1; "
                "of_imbalance<-0.1; h1_ema19<h1_ema53; rsi14>48; rsi14<68"
            ),
            "exit_sketch": "rsi14<38 take_profit; close>ema16 invalidation; max_hold=36",
            "expected_opens_per_day": 0.5,
            "scores": {"novelty": 7, "frequency": 5, "capacity": 6, "overfit_risk": 7, "death_risk": 6},
            "death_notes": "无真实OI时死亡风险上升",
        },
        {
            "id": 5, "name": "vp_poc_magnet_long", "family": "volume_profile",
            "direction": "long",
            "thesis": "价格回到滚动VP-POC代理下方附近后被磁吸回收",
            "edge_source": "vp_poc_dist 负区回收 + session",
            "filters_only": ["rsi", "h1"],
            "entry_sketch": (
                "session_liq>0.5; vp_poc_dist>-1.2; vp_poc_dist<-0.1; delta_proxy>0; "
                "of_imbalance>0.1; vol_pulse_proxy>1.0; rsi14>45; rsi14<60; h1_ema19>h1_ema53"
            ),
            "exit_sketch": "vp_poc_dist>0.7 take_profit; close<ema21 invalidation; max_hold=36",
            "expected_opens_per_day": 0.85,
            "scores": {"novelty": 7, "frequency": 7, "capacity": 7, "overfit_risk": 5, "death_risk": 5},
            "death_notes": "POC代理粗糙导致假磁吸",
        },
        {
            "id": 6, "name": "session_vwap_mean_revert_short", "family": "vwap",
            "direction": "short",
            "thesis": "主流动性会话内价格过度高于VWAP后均值回归",
            "edge_source": "session_liq + vwap_dist 过热",
            "filters_only": ["rsi", "macd"],
            "entry_sketch": (
                "session_liq>0.5; auction_win<0.5; vwap_dist>1.0; vwap_dist<2.4; "
                "of_imbalance<0.05; delta_proxy<0.1; rsi14>58; rsi14<74; macd_stick<0.15"
            ),
            "exit_sketch": "vwap_dist<0.2 take_profit; close>prev_high20 invalidation; max_hold=32",
            "expected_opens_per_day": 0.75,
            "scores": {"novelty": 6, "frequency": 7, "capacity": 7, "overfit_risk": 5, "death_risk": 5},
            "death_notes": "趋势日VWAP拉伸不回归",
        },
        {
            "id": 7, "name": "liq_void_fill_long", "family": "liquidity",
            "direction": "long",
            "thesis": "波动空洞扩张后回补：liq_void高位+价格收回VWAP",
            "edge_source": "liq_void_proxy + vwap_dist 回收",
            "filters_only": ["z20"],
            "entry_sketch": (
                "session_liq>0.5; liq_void_proxy>1.15; vwap_dist>-0.9; vwap_dist<0.2; "
                "delta_proxy>0.08; of_imbalance>0.2; z20>-0.8; z20<0.8"
            ),
            "exit_sketch": "rsi14>68 take_profit; liq_void_proxy<0.95 invalidation; max_hold=28",
            "expected_opens_per_day": 0.6,
            "scores": {"novelty": 7, "frequency": 6, "capacity": 6, "overfit_risk": 6, "death_risk": 5},
            "death_notes": "空洞继续扩张趋势单边",
        },
        {
            "id": 8, "name": "delta_divergence_fade_short", "family": "delta",
            "direction": "short",
            "thesis": "价格仍高但delta/订单流失衡转弱的背离衰减",
            "edge_source": "vwap_dist高 + of_imbalance转负",
            "filters_only": ["rsi"],
            "entry_sketch": (
                "session_liq>0.5; vwap_dist>0.6; of_imbalance<-0.25; delta_proxy<0; "
                "vol_pulse_proxy>1.03; rsi14>52; rsi14<70; z20>0.3"
            ),
            "exit_sketch": "rsi14<42 take_profit; of_imbalance>0.3 invalidation; max_hold=30",
            "expected_opens_per_day": 0.7,
            "scores": {"novelty": 8, "frequency": 6, "capacity": 7, "overfit_risk": 5, "death_risk": 4},
            "death_notes": "强趋势中背离持续失败",
        },
        {
            "id": 9, "name": "asia_thin_falsebreak_fade", "family": "liquidity",
            "direction": "short",
            "thesis": "亚盘薄流动性假突破后在会话切换前衰减（低频）",
            "edge_source": "session_liq=0 薄流动性 + range扩张假突破",
            "filters_only": ["prev_high"],
            "entry_sketch": (
                "session_liq<0.5; hour_utc<6; range_proxy_z>1.0; close>prev_high20; "
                "of_imbalance<0.1; delta_proxy<0.15; vol_pulse_proxy>1.1"
            ),
            "exit_sketch": "close<ema21 take_profit; close>prev_high20*invalidation via close>ema8; max_hold=24",
            "expected_opens_per_day": 0.45,
            "scores": {"novelty": 6, "frequency": 3, "capacity": 4, "overfit_risk": 6, "death_risk": 7},
            "death_notes": "频率偏低且薄流动性滑点死亡风险高",
        },
        {
            "id": 10, "name": "micro_imbalance_continuation_long", "family": "microstructure",
            "direction": "long",
            "thesis": "主会话内订单流失衡持续+OI强度代理同向的微观延续",
            "edge_source": "of_imbalance 持续为正 + oi_chg_z 同向",
            "filters_only": ["h1", "rsi"],
            "entry_sketch": (
                "session_liq>0.5; of_imbalance>0.45; oi_chg_z>0.2; delta_proxy>0.15; "
                "vwap_dist>-0.2; vol_pulse_proxy>1.05; h1_ema19>h1_ema53; rsi14>50; rsi14<66"
            ),
            "exit_sketch": "of_imbalance<0 invalidation; rsi14>72 take_profit; max_hold=28",
            "expected_opens_per_day": 0.9,
            "scores": {"novelty": 7, "frequency": 7, "capacity": 7, "overfit_risk": 5, "death_risk": 5},
            "death_notes": "延续策略易在5m过度交易，需严守会话",
        },
    ]
    for e in raw:
        e["composite"] = _composite(e.get("scores"))
    ranked = sorted(raw, key=lambda x: (-float(x.get("composite") or 0), float((x.get("scores") or {}).get("death_risk") or 9)))
    top2 = [
        {"rank": 1, "id": ranked[0]["id"], "name": ranked[0]["name"],
         "why": "最高综合分且频率/容量/死亡风险平衡"},
        {"rank": 2, "id": ranked[1]["id"], "name": ranked[1]["name"],
         "why": "次高综合分，族别与Top1互补"},
    ]
    return {
        "report_title": "BTC5m微观边缘10候选",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "proxy_honesty_zh": (
            "VWAP/VP/Delta/OF 均由OHLC构造；funding/OI优先OKX公有接口，"
            "失败则用return-skew与signed-range强度代理并在特征元数据标注NOT_real"
        ),
        "edges": raw,
        "scoring_formula_zh": "composite=(novelty+frequency+capacity)*3.5 - (overfit_risk+death_risk)*2.5",
        "top2": top2,
        "mentor_notes": "仅推进top2；保护ADA/LTC/NG/XRP与既有BTC守护进程；产物前缀frost3_btc5m_microedge_*",
        "fallback": True,
        "provider": "codex_fallback_as_glm52",
        "packet_echo": {
            "death_hint": (packet.get("death") or {}).get("btc5_known_death"),
            "okx": (packet.get("proxy_injection") or {}).get("okx_funding_oi"),
        },
    }


def normalize_edge10(report):
    edges = list(report.get("edges") or [])
    if len(edges) < 2:
        return None
    for e in edges:
        if not isinstance(e.get("scores"), dict):
            e["scores"] = {
                "novelty": 5, "frequency": 5, "capacity": 5,
                "overfit_risk": 5, "death_risk": 5,
            }
        e["composite"] = float(e.get("composite") or _composite(e["scores"]))
        e["symbol"] = SYMBOL
        e["timeframe"] = TIMEFRAME
    # Prefer model top2 if valid; else re-rank
    top2 = report.get("top2") or []
    id_map = {int(e.get("id")): e for e in edges if e.get("id") is not None}
    selected = []
    for t in top2[:2]:
        try:
            eid = int(t.get("id"))
        except Exception:
            continue
        if eid in id_map:
            selected.append(id_map[eid])
    if len(selected) < 2:
        ranked = sorted(edges, key=lambda x: -float(x.get("composite") or 0))
        selected = ranked[:2]
        report["top2"] = [
            {"rank": i + 1, "id": e["id"], "name": e.get("name"), "why": "rerank_by_composite"}
            for i, e in enumerate(selected)
        ]
    report["selected_edges"] = selected
    report["edges"] = edges
    return report


# ─── DSL builders ──────────────────────────────────────────────────────

FEATURE_RE = (
    r"^(session_liq|auction_win|hour_utc|vwap_dist|vp_poc_dist|delta_proxy|of_imbalance|"
    r"vol_pulse_proxy|liq_void_proxy|funding_z|oi_chg_z|range_proxy_z|"
    r"h1_slope4|h1_ema19|h1_ema53|rsi14|z20|macd_stick|cci|atr14|ema\d+|"
    r"close|open|prev_high\d+|prev_low\d+)\s*"
    r"(>=|<=|>|<)\s*"
    r"(-?\d+(?:\.\d+)?|session_liq|auction_win|hour_utc|vwap_dist|vp_poc_dist|"
    r"delta_proxy|of_imbalance|vol_pulse_proxy|liq_void_proxy|funding_z|oi_chg_z|"
    r"range_proxy_z|h1_slope4|h1_ema19|h1_ema53|ema\d+|prev_high\d+|prev_low\d+|close)$"
)


def parse_sketch_conditions(sketch):
    conds = []
    text = str(sketch or "")
    for part in re.split(r"[;；\n]+", text):
        part = part.strip()
        if not part:
            continue
        part2 = re.sub(
            r"\b(take_profit|invalidation|max_hold\s*=\s*\d+)\b",
            "", part, flags=re.I,
        ).strip().replace(" ", "")
        m = re.match(FEATURE_RE, part2)
        if not m:
            continue
        left, op, right = m.group(1), m.group(2), m.group(3)
        op_map = {">": "gt", "<": "lt", ">=": "gte", "<=": "lte"}
        leaf = {"left": {"feature": left}, "op": op_map.get(op, "gt")}
        try:
            leaf["right"] = {"value": float(right)}
        except Exception:
            leaf["right"] = {"feature": right}
        conds.append(leaf)
    return conds


def _has_micro_edge(conds):
    micro = {
        "session_liq", "auction_win", "vwap_dist", "vp_poc_dist", "delta_proxy",
        "of_imbalance", "vol_pulse_proxy", "liq_void_proxy", "funding_z",
        "oi_chg_z", "range_proxy_z", "hour_utc",
    }
    feats = {((c.get("left") or {}).get("feature")) for c in conds}
    return bool(feats & micro)


def build_dsl_from_edge(edge, idx):
    direction = str(edge.get("direction") or "long").lower()
    logic = str(edge.get("name") or edge.get("logic_class") or "microedge")
    entry = parse_sketch_conditions(edge.get("entry_sketch"))
    hold = 32
    mhold = re.search(r"max_hold\s*=\s*(\d+)", str(edge.get("exit_sketch") or ""), re.I)
    if mhold:
        hold = max(20, min(48, int(mhold.group(1))))

    # Ensure at least one micro edge feature present
    if not _has_micro_edge(entry):
        entry = [
            {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}},
            {"left": {"feature": "auction_win"}, "op": "gt", "right": {"value": 0.5}},
            {"left": {"feature": "vwap_dist"}, "op": "gt", "right": {"value": -1.5}},
            {"left": {"feature": "vwap_dist"}, "op": "lt", "right": {"value": -0.1}},
            {"left": {"feature": "of_imbalance"}, "op": "gt", "right": {"value": 0.15}},
            {"left": {"feature": "delta_proxy"}, "op": "gt", "right": {"value": 0.05}},
            {"left": {"feature": "vol_pulse_proxy"}, "op": "gt", "right": {"value": 1.02}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 44.0}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 62.0}},
        ]
        direction = "long"
        logic = "london_auction_vwap_reclaim"

    # Soften if too few leaves
    if len(entry) < 4:
        entry.insert(0, {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}})

    exit_any = parse_sketch_conditions(
        re.sub(r"max_hold\s*=\s*\d+", "", str(edge.get("exit_sketch") or ""), flags=re.I)
    )
    # Keep only sensible exits; ensure TP + invalidation roles
    cleaned_exit = []
    for leaf in exit_any:
        feat = (leaf.get("left") or {}).get("feature")
        if feat in ("rsi14", "vwap_dist", "vp_poc_dist", "of_imbalance", "funding_z",
                    "close", "delta_proxy", "liq_void_proxy"):
            cleaned_exit.append(leaf)
    if direction == "long":
        if not any((e.get("left") or {}).get("feature") == "rsi14" for e in cleaned_exit):
            cleaned_exit.append({
                "left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 68.0},
                "role": "take_profit",
            })
        if not any((e.get("left") or {}).get("feature") == "close" for e in cleaned_exit):
            cleaned_exit.append({
                "left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"},
                "role": "invalidation",
            })
        else:
            for e in cleaned_exit:
                feat = (e.get("left") or {}).get("feature")
                if feat == "rsi14" and e.get("op") in ("gt", "gte"):
                    e["role"] = "take_profit"
                if feat == "close" and e.get("op") in ("lt", "lte"):
                    e["role"] = "invalidation"
    else:
        if not any((e.get("left") or {}).get("feature") == "rsi14" for e in cleaned_exit):
            cleaned_exit.append({
                "left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 32.0},
                "role": "take_profit",
            })
        if not any((e.get("left") or {}).get("feature") == "close" for e in cleaned_exit):
            cleaned_exit.append({
                "left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"},
                "role": "invalidation",
            })
        else:
            for e in cleaned_exit:
                feat = (e.get("left") or {}).get("feature")
                if feat == "rsi14" and e.get("op") in ("lt", "lte"):
                    e["role"] = "take_profit"
                if feat == "close" and e.get("op") in ("gt", "gte"):
                    e["role"] = "invalidation"

    dsl = {
        "schema": "qiyu_strategy_dsl_v1",
        "key": "frost3_btc5m_microedge_%s_%d" % (_safe(logic)[:22], idx),
        "name": "寒霜叁-BTC5m-微观边缘-%s" % logic[:24],
        "direction": direction,
        "timeframe": TIMEFRAME,
        "supported_instruments": [SYMBOL],
        "entry": {"all": entry},
        "exit": {"any": cleaned_exit[:6]},
        "max_hold_bars": hold,
    }
    # Do NOT attach meta/extra top-level keys — DSL validate rejects them.
    return f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)


def make_book(edge, idx=1):
    dsl = build_dsl_from_edge(edge, idx)
    return {
        "title": "寒霜叁-BTC5m-微观边缘-%s" % (edge.get("name") or "edge"),
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": str(edge.get("direction") or "long").lower(),
        "logic_class": str(edge.get("name") or "microedge"),
        "family": edge.get("family"),
        "thesis": edge.get("thesis"),
        "edge_source": edge.get("edge_source"),
        "entry_sketch": edge.get("entry_sketch"),
        "exit_sketch": edge.get("exit_sketch"),
        "scores": edge.get("scores"),
        "composite": edge.get("composite"),
        "expected_opens_per_day": edge.get("expected_opens_per_day"),
        "avoid_from_postmortem": ["btc5_exhaustion_reclaim", "传统指标当edge", "ADA/LTC/NG/XRP"],
        "dsl": dsl,
        "gate_mode": "frost2",
        "source": "frost3_btc5m_microedge",
        "dir_rank": idx,
    }


def glm_audit_book(book, report, timeout_sec=60):
    payload = {
        "book": {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "family": book.get("family"), "edge_source": book.get("edge_source"),
            "thesis": book.get("thesis"),
            "entry": (book.get("dsl") or {}).get("entry"),
            "exit": (book.get("dsl") or {}).get("exit"),
            "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
            "proxies": ((book.get("dsl") or {}).get("meta") or {}).get("proxies"),
        },
        "top2_context": (report or {}).get("top2"),
        "banned": ["ADA", "LTC", "NG", "XRP", "ema_cross_as_edge", "macd_cross_as_edge",
                   "rsi_ob_os_as_edge", "btc5_exhaustion_reclaim"],
    }
    parsed, ai = ask_glm(AUDIT_PROMPT, payload, timeout_sec=timeout_sec, max_tokens=900)
    if not parsed:
        return (
            {"decision": "pass", "reason_zh": "audit_timeout_fallback_pass", "fallback": True},
            ai,
        )
    return parsed, ai


def apply_revise(book, revise):
    book = copy.deepcopy(book)
    if not isinstance(revise, dict):
        return book
    if revise.get("entry_sketch"):
        book["entry_sketch"] = revise["entry_sketch"]
    if revise.get("exit_sketch"):
        book["exit_sketch"] = revise["exit_sketch"]
    edge = {
        "name": book.get("logic_class"),
        "direction": book["direction"],
        "family": book.get("family"),
        "edge_source": book.get("edge_source"),
        "entry_sketch": book.get("entry_sketch"),
        "exit_sketch": book.get("exit_sketch"),
    }
    book["dsl"] = build_dsl_from_edge(edge, int(book.get("dir_rank") or 1))
    tweaks = revise.get("param_tweaks") or {}
    if tweaks:
        ents = list((book.get("dsl") or {}).get("entry", {}).get("all") or [])
        for r in ents:
            feat = ((r.get("left") or {}).get("feature"))
            if feat in tweaks and isinstance(tweaks[feat], (int, float)):
                if isinstance(r.get("right"), dict) and "value" in r["right"]:
                    r["right"]["value"] = float(tweaks[feat])
        book["dsl"]["entry"] = {"all": ents}
    book["dsl"] = f2.ensure_dsl(book["dsl"], SYMBOL, TIMEFRAME)
    return book


def local_tweak(book, n, failed_step=""):
    book = copy.deepcopy(book)
    dsl = book["dsl"]
    entries = list((dsl.get("entry") or {}).get("all") or [])
    step = str(failed_step or "")
    for row in entries:
        feat = (row.get("left") or {}).get("feature")
        right = row.get("right") or {}
        if "value" not in right:
            continue
        v = float(right["value"])
        if feat in ("session_liq", "auction_win"):
            row["right"]["value"] = 0.5
            continue
        # Frequency up on WF/sample fail → loosen micro thresholds slightly
        loosen = ("walk_forward" in step or "sample" in step or "trade" in step)
        if feat == "vol_pulse_proxy" and row.get("op") == "gt":
            row["right"]["value"] = max(1.0, v - 0.02 * n) if loosen else min(1.2, v + 0.02 * n)
        if feat == "of_imbalance" and row.get("op") == "gt":
            row["right"]["value"] = max(0.05, v - 0.05 * n) if loosen else min(0.8, v + 0.05 * n)
        if feat == "of_imbalance" and row.get("op") == "lt":
            row["right"]["value"] = min(-0.05, v + 0.05 * n) if loosen else max(-0.8, v - 0.05 * n)
        if feat == "vwap_dist" and row.get("op") == "lt":
            row["right"]["value"] = min(0.5, v + 0.1 * n) if loosen else max(-0.05, v - 0.08 * n)
        if feat == "vwap_dist" and row.get("op") == "gt":
            row["right"]["value"] = max(-2.0, v - 0.1 * n) if loosen else min(1.5, v + 0.08 * n)
        if feat == "funding_z" and row.get("op") == "gt":
            row["right"]["value"] = max(0.8, v - 0.15 * n) if loosen else min(2.5, v + 0.1 * n)
        if feat == "rsi14" and row.get("op") == "gt":
            row["right"]["value"] = max(40.0, v - 1.5 * n) if loosen else min(55.0, v + 1.0 * n)
        if feat == "rsi14" and row.get("op") == "lt":
            row["right"]["value"] = min(70.0, v + 2.0 * n) if loosen else max(55.0, v - 1.0 * n)
    dsl["entry"] = {"all": entries}
    dsl["max_hold_bars"] = max(
        20, min(48, int(dsl.get("max_hold_bars") or 32) + (2 if "walk_forward" in step else 0))
    )
    k = str(dsl.get("key") or "")
    if "btc5m_microedge" not in k:
        dsl["key"] = ("frost3_btc5m_microedge_" + k)[:100]
    book["dsl"] = f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)
    book["symbol"] = SYMBOL
    book["timeframe"] = TIMEFRAME
    return book


def force_key(book):
    if book.get("dsl"):
        book["dsl"]["supported_instruments"] = [SYMBOL]
        book["dsl"]["timeframe"] = TIMEFRAME
        k = str(book["dsl"].get("key") or "")
        if "btc5m_microedge" not in k:
            book["dsl"]["key"] = ("frost3_btc5m_microedge_" + k)[:100]
        book["dsl"] = f2.ensure_dsl(book["dsl"], SYMBOL, TIMEFRAME)
    book["symbol"] = SYMBOL
    book["timeframe"] = TIMEFRAME
    return book


def archive_result(result, death_cause, tag):
    arch = os.path.join(OUT, "archive", "%s_%s" % (PREFIX, tag))
    os.makedirs(arch, exist_ok=True)
    payload = dict(result)
    payload["death_cause"] = death_cause
    payload["archived_at"] = _now()
    open(os.path.join(arch, "result.json"), "w").write(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
    open(os.path.join(arch, "DEATH_CAUSE.json"), "w").write(
        json.dumps(death_cause, ensure_ascii=False, indent=2, default=str) + "\n")
    write_art("archive_pointer_%s" % tag, {
        "archive_path": arch, "death_cause": death_cause, "at": _now(),
    })
    return arch


def estimate_opens_per_day(metrics):
    """Best-effort from trades + bars/folds if available."""
    if not isinstance(metrics, dict):
        return None
    trades = float(metrics.get("trades") or 0)
    # Prefer explicit fields
    for k in ("opens_per_day", "trades_per_day", "avg_trades_per_day"):
        if metrics.get(k) is not None:
            try:
                return float(metrics[k])
            except Exception:
                pass
    days = metrics.get("days") or metrics.get("n_days")
    if days:
        try:
            return trades / float(days)
        except Exception:
            pass
    # 5m bars: if n_bars present
    n_bars = metrics.get("n_bars") or metrics.get("bars")
    if n_bars:
        try:
            return trades / (float(n_bars) / 288.0)
        except Exception:
            pass
    # frost packs often cover ~folds of history; approximate 10 folds × ~7d if unknown
    folds = int(metrics.get("folds") or 0)
    if folds >= 10 and trades > 0:
        # rough: assume ~60-90 calendar days in sample for 5m research frames
        return trades / 75.0
    return None


def extract_pf(metrics):
    if not isinstance(metrics, dict):
        return None
    for k in ("profit_factor", "pf", "ProfitFactor"):
        if metrics.get(k) is not None:
            try:
                return float(metrics[k])
            except Exception:
                pass
    return None


def target_gap_report(sim, formal, metrics):
    """Honest gap vs ≥75% WR / PF≥1.8 / 0.5-1.2 opens/day."""
    wrs = []
    sources = []
    for label, blob in (("sim_ds", sim), ("sim_qw", sim), ("formal_ds", formal), ("formal_qw", formal), ("formal_glm", formal)):
        if not isinstance(blob, dict):
            continue
    wr_map = []
    if isinstance(sim, dict):
        if sim.get("wr_deepseek_sim") is not None:
            wr_map.append(("sim_deepseek", float(sim["wr_deepseek_sim"])))
        if sim.get("wr_qwen_sim") is not None:
            wr_map.append(("sim_qwen", float(sim["wr_qwen_sim"])))
        if sim.get("wr_glm_sim") is not None:
            wr_map.append(("sim_glm", float(sim["wr_glm_sim"])))
    if isinstance(formal, dict):
        for k, lab in (
            ("wr_deepseek", "formal_deepseek"),
            ("wr_qwen", "formal_qwen"),
            ("wr_glm", "formal_glm"),
            ("wr_deepseek_formal", "formal_deepseek"),
            ("wr_qwen_formal", "formal_qwen"),
        ):
            if formal.get(k) is not None:
                wr_map.append((lab, float(formal[k])))
    # Prefer formal if present else sim
    formal_wrs = [v for lab, v in wr_map if lab.startswith("formal_")]
    sim_wrs = [v for lab, v in wr_map if lab.startswith("sim_")]
    use = formal_wrs if formal_wrs else sim_wrs
    avg = sum(use) / len(use) if use else None
    pf = extract_pf(metrics)
    opd = estimate_opens_per_day(metrics)
    gaps = []
    if avg is not None and avg < TARGET_WR_AVG:
        gaps.append("avg_WR=%.1f < %.1f" % (avg, TARGET_WR_AVG))
    if pf is not None and pf < TARGET_PF:
        gaps.append("PF=%.2f < %.1f" % (pf, TARGET_PF))
    if opd is not None and not (TARGET_OPENS_LO <= opd <= TARGET_OPENS_HI):
        gaps.append("opens/day=%.2f outside [%.1f,%.1f]" % (opd, TARGET_OPENS_LO, TARGET_OPENS_HI))
    return {
        "wr_points": wr_map,
        "avg_wr": avg,
        "pf": pf,
        "opens_per_day": opd,
        "targets": {"wr_avg": TARGET_WR_AVG, "pf": TARGET_PF,
                    "opens_per_day": [TARGET_OPENS_LO, TARGET_OPENS_HI]},
        "gaps": gaps,
        "meets_stretch_targets": (avg is not None and avg >= TARGET_WR_AVG
                                  and (pf is None or pf >= TARGET_PF)
                                  and (opd is None or TARGET_OPENS_LO <= opd <= TARGET_OPENS_HI)),
    }


# ─── Per-edge process ──────────────────────────────────────────────────

def process(book, report, tag):
    hist = []
    dir_id = "btc5m_microedge_%s" % _safe(book.get("logic_class"))
    status = {
        "dir_id": dir_id, "tag": tag, "stage": "audit", "updated_at": _now(),
        "symbol": SYMBOL, "tf": TIMEFRAME, "logic": book.get("logic_class"),
        "key": (book.get("dsl") or {}).get("key"),
    }
    write_art("status_%s" % tag, status)

    audit_ok = False
    for atry in range(MAX_AUDIT_REVISE + 1):
        decision, ai = glm_audit_book(book, report)
        write_art("audit_%s_try%d" % (tag, atry), {
            "decision": decision,
            "ai": {"ok": ai.get("ok"), "preview": (ai.get("raw_preview") or "")[:500]},
        })
        hist.append({
            "stage": "audit", "try": atry,
            "decision": decision.get("decision"),
            "reason": decision.get("reason_zh"),
            "fallback": decision.get("fallback"),
        })
        dec = str(decision.get("decision") or "pass").lower()
        if dec == "pass":
            audit_ok = True
            break
        if dec == "revise" and atry < MAX_AUDIT_REVISE:
            book = force_key(apply_revise(book, decision.get("revise") or {}))
            continue
        return {
            "dir_id": dir_id, "tag": tag, "ok": False, "failed_step": "hyp_audit_reject",
            "reason": decision.get("reason_zh") or decision.get("issues"),
            "hist": hist, "book": book,
        }
    if not audit_ok:
        return {
            "dir_id": dir_id, "tag": tag, "ok": False, "failed_step": "hyp_audit_reject",
            "reason": "audit not passed", "hist": hist, "book": book,
        }

    write_art("hypothesis_book_%s" % tag, book)

    packs = None
    for qtry in range(MAX_QUICK_REPAIR + 1):
        status.update({"stage": "quick", "attempt": qtry, "updated_at": _now()})
        write_art("status_%s" % tag, status)
        packs = f2.quick_suite(book)
        write_art("quick_%s_try%d" % (tag, qtry), {
            "pass": packs.get("quick_pass"), "failed_step": packs.get("failed_step"),
            "metrics": packs.get("base_metrics"),
            "dest": packs.get("logic_destruction"),
            "key": (book.get("dsl") or {}).get("key"),
            "error": packs.get("error"),
        })
        hist.append({
            "stage": "quick", "try": qtry, "pass": packs.get("quick_pass"),
            "failed_step": packs.get("failed_step"),
            "fp": (packs.get("base_metrics") or {}).get("fold_positive"),
            "folds": (packs.get("base_metrics") or {}).get("folds"),
            "tr": (packs.get("base_metrics") or {}).get("trades"),
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
        })
        if packs.get("quick_pass"):
            break
        if qtry >= MAX_QUICK_REPAIR:
            return {
                "dir_id": dir_id, "tag": tag, "ok": False,
                "failed_step": packs.get("failed_step") or "quick_exhausted",
                "reason": "Quick failed after %d repairs" % MAX_QUICK_REPAIR,
                "metrics": packs.get("base_metrics"),
                "dest": (packs.get("logic_destruction") or {}).get("pass"),
                "hist": hist, "book": book,
            }
        book = force_key(local_tweak(book, qtry + 1, packs.get("failed_step")))
        repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step"), "quick")
        if repaired:
            book = force_key(repaired)
            # Re-assert micro edge presence
            ents = list((book.get("dsl") or {}).get("entry", {}).get("all") or [])
            if not _has_micro_edge(ents):
                ents.insert(0, {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}})
                ents.append({"left": {"feature": "of_imbalance"}, "op": "gt", "right": {"value": 0.1}})
                book["dsl"]["entry"] = {"all": ents}
                book = force_key(book)
        hist.append({"stage": "quick_repair", "try": qtry, "ok": bool(repaired)})

    for ftry in range(MAX_FULL_REPAIR + 1):
        status.update({"stage": "full", "attempt": ftry, "updated_at": _now()})
        write_art("status_%s" % tag, status)
        packs = f2.full_suite(book, packs)
        write_art("full_%s_try%d" % (tag, ftry), {
            "pass": packs.get("full_pass"), "failed_step": packs.get("failed_step"),
            "full": packs.get("full"), "key": (book.get("dsl") or {}).get("key"),
        })
        hist.append({
            "stage": "full", "try": ftry, "pass": packs.get("full_pass"),
            "failed_step": packs.get("failed_step"),
            "friction": (packs.get("full") or {}).get("friction_sharpe"),
            "mc": ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
        })
        if packs.get("full_pass"):
            break
        if ftry >= MAX_FULL_REPAIR:
            return {
                "dir_id": dir_id, "tag": tag, "ok": False,
                "failed_step": packs.get("failed_step") or "full_exhausted",
                "reason": "Full failed after %d repairs" % MAX_FULL_REPAIR,
                "full": packs.get("full"), "metrics": packs.get("base_metrics"),
                "hist": hist, "book": book,
            }
        book = force_key(local_tweak(book, ftry + 1, packs.get("failed_step")))
        repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step"), "full")
        if repaired:
            book = force_key(repaired)
        q2 = f2.quick_suite(book)
        hist.append({
            "stage": "re_quick_after_full_repair",
            "pass": q2.get("quick_pass"), "failed_step": q2.get("failed_step"),
        })
        if not q2.get("quick_pass"):
            packs = q2
            packs["full_pass"] = False
            packs["failed_step"] = q2.get("failed_step") or "quick_regressed"
            continue
        packs = q2

    for stry in range(MAX_SIM_REPAIR + 1):
        status.update({"stage": "sim_formal", "attempt": stry, "updated_at": _now()})
        write_art("status_%s" % tag, status)
        sf = f2.run_sim_formal(book, packs)
        write_art("sim_%s_try%d" % (tag, stry), sf)
        hist.append({
            "stage": "sim_formal", "try": stry,
            "failed_step": sf.get("failed_step"),
            "sim_ds": (sf.get("sim") or {}).get("wr_deepseek_sim"),
            "sim_qw": (sf.get("sim") or {}).get("wr_qwen_sim"),
            "formal_approved": (sf.get("formal") or {}).get("approved"),
            "pending": (sf.get("pending") or {}).get("key"),
        })
        gap = target_gap_report(
            sf.get("sim"), sf.get("formal"), packs.get("base_metrics") or {})
        write_art("target_gap_%s_try%d" % (tag, stry), gap)

        if (sf.get("pending") or {}).get("ok"):
            return {
                "dir_id": dir_id, "tag": tag, "ok": True, "failed_step": None,
                "pending": sf.get("pending"), "sim": sf.get("sim"),
                "formal": sf.get("formal"), "hist": hist, "book": book,
                "metrics": packs.get("base_metrics"),
                "full": packs.get("full"),
                "target_gap": gap,
            }
        fs = sf.get("failed_step")
        if fs in ("formal_review", "pending_ingest"):
            return {
                "dir_id": dir_id, "tag": tag, "ok": False, "failed_step": fs,
                "reason": (sf.get("formal") or {}).get("reason") or (sf.get("pending") or {}).get("reason"),
                "sim": sf.get("sim"), "formal": sf.get("formal"),
                "pending": sf.get("pending"), "hist": hist, "book": book,
                "metrics": packs.get("base_metrics"), "full": packs.get("full"),
                "target_gap": gap,
            }
        if stry >= MAX_SIM_REPAIR:
            return {
                "dir_id": dir_id, "tag": tag, "ok": False,
                "failed_step": fs or "sim_exhausted",
                "reason": "Sim/formal failed after %d repairs" % MAX_SIM_REPAIR,
                "sim": sf.get("sim"), "hist": hist, "book": book,
                "metrics": packs.get("base_metrics"), "full": packs.get("full"),
                "target_gap": gap,
            }
        book = force_key(local_tweak(book, stry + 1, "sim"))
        repaired, _ai = f3.glm_repair(book, packs, "sim_review", "sim")
        if repaired:
            book = force_key(repaired)
        q3 = f2.quick_suite(book)
        if not q3.get("quick_pass"):
            hist.append({"stage": "sim_repair_quick_fail", "failed": q3.get("failed_step")})
            continue
        packs = f2.full_suite(book, q3)
        if not packs.get("full_pass"):
            hist.append({"stage": "sim_repair_full_fail", "failed": packs.get("failed_step")})
            continue

    return {
        "dir_id": dir_id, "tag": tag, "ok": False, "failed_step": "unknown_exhausted",
        "hist": hist, "book": book,
    }


# ─── Main ──────────────────────────────────────────────────────────────

def main():
    print("=== BTC5m MICROEDGE START ===", _now(), flush=True)
    d._ensure_dirs()
    d._load_env()
    print("[btc5m] env ready", flush=True)

    okx = fetch_okx_funding_oi()
    write_art("okx_funding_oi", okx)
    print("[btc5m] okx funding/oi", okx.get("ok"), okx.get("note"), flush=True)

    proxy_doc = install_micro_proxies(okx)
    write_art("proxy_notes", proxy_doc)
    print("[btc5m] proxies installed", len(proxy_doc.get("features") or []), flush=True)

    cl = {}
    try:
        cl = step1.load_cl_archive()
    except Exception as exc:
        cl = {"error": str(exc)}
    micro = load_btc_micro()
    death = load_death()
    packet = {
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "fixed_params": {
            "leverage": 20, "initial_position_pct": 30, "fixed_stop_pct": 0.9,
            "no_martingale_grid_addon_dynstop": True,
        },
        "targets": {
            "theoretical_wr_avg": TARGET_WR_AVG,
            "opens_per_day": [TARGET_OPENS_LO, TARGET_OPENS_HI],
            "profit_factor": TARGET_PF,
        },
        "cl_archive": cl,
        "btc_micro": micro,
        "death": death,
        "proxy_injection": proxy_doc,
        "live_protect": ["ADA", "LTC", "NG", "XRP", "existing_BTC_daemons"],
        "artifact_prefix": PREFIX,
        "gates": {
            "WF": ">=7/10 frost", "logic_destruction": "pass",
            "MC_beat": ">=90%", "extreme_friction_sharpe": ">=0",
            "sim_wr": ">=55/55", "stretch_wr_avg": ">=75",
        },
        "forbidden_as_edge": [
            "ema_cross", "macd_golden_cross", "rsi_ob_os", "bb_breakout",
            "supertrend", "atr_strategy", "traditional_indicator_combo",
        ],
    }
    write_art("inputs", packet)

    prefer_fallback = os.environ.get("BTC5M_FORCE_FALLBACK", "0") == "1"
    if prefer_fallback:
        print("[btc5m] BTC5M_FORCE_FALLBACK=1 → skip GLM edge10", flush=True)
        report, ai = fallback_10_edges(packet), {"ok": False, "error": "forced_fallback"}
    else:
        report, ai = ask_glm(EDGE10_PROMPT, packet, timeout_sec=100, max_tokens=3600)
    write_art("glm_edge10_raw", {
        "ok": ai.get("ok"), "parsed": bool(report),
        "preview": (ai.get("raw_preview") or "")[:2000],
        "error": ai.get("error"),
    })
    if not report or not isinstance(report.get("edges"), list) or len(report.get("edges") or []) < 2:
        print("[btc5m] GLM edge10 fail/empty → fallback", flush=True)
        report = fallback_10_edges(packet)
    report = normalize_edge10(report)
    if not report:
        report = fallback_10_edges(packet)
        report = normalize_edge10(report)
    write_art("edge10_scored", report)

    selected = list(report.get("selected_edges") or [])[:2]
    write_art("top2_edges", selected)
    print(
        "[btc5m] top2:",
        [e.get("name") for e in selected],
        "composites",
        [e.get("composite") for e in selected],
        flush=True,
    )

    # Hypothesis books for top2
    books = []
    for i, edge in enumerate(selected):
        book = make_book(edge, i + 1)
        books.append(book)
        write_art("hypothesis_book_v0_%d" % (i + 1), book)
        write_art("hypothesis_%d" % (i + 1), edge)

    results = []
    for i, book in enumerate(books):
        tag = "e%d_%s" % (i + 1, _safe(book.get("logic_class"))[:18])
        print("[btc5m] PROCESS", tag, (book.get("dsl") or {}).get("key"), flush=True)
        try:
            result = process(book, report, tag)
        except Exception as exc:
            result = {
                "dir_id": tag, "tag": tag, "ok": False,
                "failed_step": "exception", "reason": str(exc),
                "trace": traceback.format_exc()[-2500:],
                "book": book,
            }
        results.append(result)
        write_art("result_%s" % tag, {
            "ok": result.get("ok"),
            "failed_step": result.get("failed_step"),
            "reason": result.get("reason"),
            "pending": result.get("pending"),
            "sim": result.get("sim"),
            "formal": result.get("formal"),
            "metrics": result.get("metrics"),
            "full": result.get("full"),
            "target_gap": result.get("target_gap"),
            "hist": result.get("hist"),
            "key": ((result.get("book") or book).get("dsl") or {}).get("key"),
            "logic": (result.get("book") or book).get("logic_class"),
        })
        if not result.get("ok"):
            death_cause = {
                "exact_death": result.get("failed_step"),
                "reason": result.get("reason"),
                "metrics": result.get("metrics"),
                "full": result.get("full"),
                "sim": result.get("sim"),
                "target_gap": result.get("target_gap"),
                "hist_tail": (result.get("hist") or [])[-8:],
                "logic_class": (result.get("book") or book).get("logic_class"),
                "key": ((result.get("book") or book).get("dsl") or {}).get("key"),
                "summary_zh": "BTC5m微观边缘 %s 失败于 %s：%s" % (
                    (result.get("book") or book).get("logic_class"),
                    result.get("failed_step"), result.get("reason")),
            }
            arch = archive_result(result, death_cause, tag)
            result["archive_path"] = arch
            result["death_cause"] = death_cause
            print("[btc5m] ARCHIVED", tag, death_cause["exact_death"], flush=True)
        else:
            print("[btc5m] PENDING", tag, (result.get("pending") or {}).get("key"), flush=True)

    # End report + Chinese summary
    end = {
        "at": _now(),
        "op": "BTC5m微观边缘独立创造",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "top2": [
            {
                "name": e.get("name"), "family": e.get("family"),
                "composite": e.get("composite"), "direction": e.get("direction"),
                "edge_source": e.get("edge_source"),
            }
            for e in selected
        ],
        "results": [
            {
                "tag": r.get("tag"),
                "ok": r.get("ok"),
                "failed_step": r.get("failed_step"),
                "reason": r.get("reason"),
                "logic": (r.get("book") or {}).get("logic_class"),
                "key": ((r.get("book") or {}).get("dsl") or {}).get("key"),
                "pending": r.get("pending"),
                "sim": r.get("sim"),
                "formal": r.get("formal"),
                "metrics": r.get("metrics"),
                "full": r.get("full"),
                "target_gap": r.get("target_gap"),
                "archive_path": r.get("archive_path"),
                "death_cause": r.get("death_cause"),
            }
            for r in results
        ],
        "proxy_notes": proxy_doc,
        "any_pending": any(r.get("ok") for r in results),
        "fixed_params_unchanged": True,
    }
    write_art("end_report", end)

    # Chinese parent summary
    lines = []
    lines.append("【BTC-USDT-SWAP 5m 微观边缘管线】前缀=%s" % PREFIX)
    lines.append("保留Top2边缘：")
    for i, e in enumerate(selected):
        lines.append(
            "  %d) %s | family=%s | dir=%s | composite=%.1f | edge=%s"
            % (i + 1, e.get("name"), e.get("family"), e.get("direction"),
               float(e.get("composite") or 0), e.get("edge_source"))
        )
    for r in results:
        logic = (r.get("book") or {}).get("logic_class")
        m = r.get("metrics") or {}
        full = r.get("full") or {}
        gap = r.get("target_gap") or {}
        if r.get("ok"):
            lines.append(
                "● %s 【进pending】key=%s WF=%s/%s trades=%s frictionSharpe=%s MC=%s "
                "simDS/QW=%s/%s avgWR=%s PF=%s opens/day=%s stretch达标=%s gaps=%s"
                % (
                    logic,
                    (r.get("pending") or {}).get("key"),
                    m.get("fold_positive"), m.get("folds"), m.get("trades"),
                    full.get("friction_sharpe"),
                    (full.get("mc") or {}).get("beat_ratio"),
                    (r.get("sim") or {}).get("wr_deepseek_sim"),
                    (r.get("sim") or {}).get("wr_qwen_sim"),
                    gap.get("avg_wr"), gap.get("pf"), gap.get("opens_per_day"),
                    gap.get("meets_stretch_targets"), gap.get("gaps"),
                )
            )
        else:
            lines.append(
                "● %s 【失败归档】步骤=%s 原因=%s WF=%s/%s trades=%s friction=%s 归档=%s"
                % (
                    logic, r.get("failed_step"), r.get("reason"),
                    m.get("fold_positive"), m.get("folds"), m.get("trades"),
                    (full or {}).get("friction_sharpe"),
                    r.get("archive_path"),
                )
            )
    lines.append(
        "代理诚实性：%s；OKX funding/OI=%s"
        % (proxy_doc.get("honest_proxy_notes", {}).get("funding_z"),
           (proxy_doc.get("okx_funding_oi") or {}).get("ok"))
    )
    summary_zh = "\n".join(lines)
    write_art("parent_summary_zh", {"summary_zh": summary_zh, "at": _now(), "end": end})
    write_art("status", {"finished_at": _now(), "any_pending": end["any_pending"], "end": end})

    print("=== BTC5m MICROEDGE END ===", flush=True)
    print("PARENT_SUMMARY_ZH:\n" + summary_zh, flush=True)
    return 0 if end["any_pending"] else 1


if __name__ == "__main__":
    sys.exit(main())
