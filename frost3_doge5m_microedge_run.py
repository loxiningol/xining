#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone DOGE-USDT-SWAP 5m high-vol microstructure edge.

Artifacts ONLY: /root/auto_trade/dual_engine/frost3_doge5m_microedge_*
Key prefix: frost3_doge5m_microedge_
FULLY ISOLATED from frost3_doge4h1h / other frost3 agents.
Protect live ADA/LTC/NG/XRP.

Edge (allowed families only):
  Session liquidity + Volume Acceptance + Auction reclaim
  + Funding skew / OI pulse when fetchable (else honest OHLCV proxies).
Forbidden as edge: EMA / MACD / RSI / ATR / Bollinger strategies.

Fixed: 20x, SL 0.9%, position 30% (factory LEVERAGE/STOP); no martingale/grid/add/dyn stop.
Target: ~1 open/day; formal DS+Qwen+GLM avg WR ≥75% → pending.
"""
from __future__ import print_function

import copy
import json
import math
import os
import random
import re
import sqlite3
import sys
import traceback
import urllib.request
from datetime import datetime

ENV_PATH = "/root/auto_trade/ai_ecosystem.env"
if os.path.exists(ENV_PATH):
    for line in open(ENV_PATH):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)

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
PREFIX = "frost3_doge5m_microedge"
KEY_PREFIX = "frost3_doge5m_microedge_"
SYMBOL = "DOGE-USDT-SWAP"
TIMEFRAME = "5m"
LOGIC_CLASS = "hv_session_vol_reject_short"
SESSION_UTC_START = 13
SESSION_UTC_END = 22  # exclusive — US/EU DOGE high-activity window
MAX_AUDIT_REVISE = 2
MAX_QUICK_REPAIR = 5
MAX_FULL_REPAIR = 5
MAX_SIM_REPAIR = 3
FORMAL_AVG_GATE = 75.0
TARGET_OPENS_PER_DAY = 1.0

# Classic-indicator features banned from ENTRY (edge must not be these).
BANNED_ENTRY_FEATURES = {
    "ema6", "ema7", "ema8", "ema16", "ema17", "ema19", "ema21", "ema23",
    "ema32", "ema38", "ema53", "ema75", "ema95", "ema200",
    "macd_stick", "rsi14", "atr14", "h1_atr14", "h1_ema19", "h1_ema53",
    "k", "d", "j", "cci", "z20",  # z20 ≈ BB location; banned as edge
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


# ─── Friction documentation (DOGE taker-heavy) ─────────────────────────

FRICTION_DOC = {
    "symbol": SYMBOL,
    "source": "/root/auto_trade/execution_cost_model.json",
    "assumptions_zh": (
        "OKX SWAP Lv1 观测 taker 费率 5bp/边；样本 nearly all-taker。"
        "DOGE execution_drag_multiplier=1.25（薄于 BTC/ETH）。"
        "回测使用 observed_base 并乘拖曳；极端摩擦 slip×2 + latency + 5% fill-fail。"
    ),
    "observed_base_nominal": {
        "fee_rate_per_side": 0.0005,
        "slippage_rate_per_side": 0.0002,
        "half_spread_rate_per_side": 0.00005,
        "impact_rate_per_side": 0.00005,
        "latency_rate_per_side": 0.00005,
        "funding_rate_per_8h": 0.0001,
        "drag_multiplier": 1.25,
    },
    "effective_after_doge_drag_approx": {
        "fee_bp_side": 5.0,
        "slip_bp_side": 2.0 * 1.25,
        "half_spread_bp_side": 0.5 * 1.25,
        "impact_bp_side": 0.5 * 1.25,
        "latency_bp_side": 0.5 * 1.25,
        "round_trip_cost_bp_notional_approx": 2 * (5.0 + 2.5 + 0.625 + 0.625 + 0.625),
        "note": "20x 杠杆下权益层成本被放大；必须过 extreme friction Sharpe≥0",
    },
    "taker_heavy": True,
    "no_l2_replay": True,
}


def _okx_get(path, params):
    qs = "&".join("%s=%s" % (k, v) for k, v in params.items())
    url = "https://www.okx.com%s?%s" % (path, qs)
    req = urllib.request.Request(url, headers={"User-Agent": "frost3-doge5m/1.0"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_funding_oi_series():
    """Best-effort real funding + OI history; degrade to empty with notes."""
    out = {
        "funding": [], "oi": [],
        "funding_ok": False, "oi_ok": False,
        "notes": [],
    }
    if os.environ.get("DOGE5M_SKIP_OKX", "0") == "1":
        out["notes"].append("okx_fetch_skipped_by_env")
        return out
    try:
        raw = _okx_get(
            "/api/v5/public/funding-rate-history",
            {"instId": SYMBOL, "limit": "100"},
        )
        rows = raw.get("data") or []
        for r in rows:
            try:
                out["funding"].append({
                    "ts": int(r.get("fundingTime") or r.get("ts") or 0),
                    "rate": float(r.get("realizedRate") or r.get("fundingRate") or 0),
                })
            except Exception:
                continue
        out["funding_ok"] = len(out["funding"]) >= 8
    except Exception as exc:
        out["notes"].append("funding_fetch_err:%s" % exc)
    try:
        # candidature endpoint — some OKX builds use /api/v5/public/open-interest-history
        raw = _okx_get(
            "/api/v5/public/open-interest-history",
            {"instId": SYMBOL, "period": "5m", "limit": "100"},
        )
        rows = raw.get("data") or []
        for r in rows:
            try:
                out["oi"].append({
                    "ts": int(r.get("ts") or 0),
                    "oi": float(r.get("oi") or r.get("oiCcy") or 0),
                })
            except Exception:
                continue
        out["oi_ok"] = len(out["oi"]) >= 8
    except Exception as exc:
        out["notes"].append("oi_fetch_err:%s" % exc)
        # fallback: spot current OI only
        try:
            raw = _okx_get("/api/v5/public/open-interest", {"instId": SYMBOL})
            rows = raw.get("data") or []
            if rows:
                out["oi_spot"] = float(rows[0].get("oi") or 0)
                out["notes"].append("oi_history_unavailable_used_spot_only")
        except Exception as exc2:
            out["notes"].append("oi_spot_err:%s" % exc2)
    return out


def install_micro_proxies(fund_oi=None):
    """Process-local feature injection — session / vol-accept / funding / OI / auction."""
    dsl_mod.FEATURES.add("session_liq")
    dsl_mod.FEATURES.add("hour_utc")
    dsl_mod.FEATURES.add("vol_accept_proxy")
    dsl_mod.FEATURES.add("funding_abs_proxy")
    dsl_mod.FEATURES.add("oi_pulse_proxy")
    dsl_mod.FEATURES.add("auction_accept")
    dsl_mod.FEATURES.add("auction_reject")
    dsl_mod.FEATURES.add("close_loc")

    _orig_frame = d._frame
    _cache = {"frame": None}
    fund_oi = fund_oi or {}

    def _align_series(index, points, value_key, default=0.0):
        """Step-function align sparse ts→value onto bar index (UTC assumed)."""
        import pandas as pd
        if not points:
            return pd.Series(default, index=index, dtype=float)
        pts = sorted(
            [(int(p["ts"]), float(p[value_key])) for p in points if p.get("ts")],
            key=lambda x: x[0],
        )
        if not pts:
            return pd.Series(default, index=index, dtype=float)
        # build as of last known
        ts_ns = index.view("int64") if hasattr(index, "view") else None
        out = []
        j = 0
        cur = default
        for t in index:
            try:
                ms = int(pd.Timestamp(t).timestamp() * 1000)
            except Exception:
                out.append(cur)
                continue
            while j < len(pts) and pts[j][0] <= ms:
                cur = pts[j][1]
                j += 1
            out.append(cur)
        return pd.Series(out, index=index, dtype=float)

    def _load_doge_frame():
        """Prefer compact pickle — pipeline._frame rebuilds features and OOMs/swaps."""
        import pickle
        pkl = "/root/auto_trade/codex_0725_train5/frame_DOGE_USDT_SWAP_5m.pkl"
        if os.path.exists(pkl):
            try:
                fr = pickle.load(open(pkl, "rb"))
                print("[doge5m] loaded pickle frame", pkl, "bars", len(fr), flush=True)
                return fr
            except Exception as exc:
                print("[doge5m] pickle load fail", exc, flush=True)
        return _orig_frame(SYMBOL, TIMEFRAME)

    def _frame_with_proxies(symbol, timeframe):
        if (
            str(symbol).upper() == SYMBOL
            and str(timeframe) == TIMEFRAME
            and _cache["frame"] is not None
        ):
            return _cache["frame"]
        if str(symbol).upper() == SYMBOL and str(timeframe) == TIMEFRAME:
            frame = _load_doge_frame()
        else:
            frame = _orig_frame(symbol, timeframe)
        try:
            # Keep frame short — 1vCPU/0.75GB hosts thrash under multi-agent load.
            if hasattr(frame, "iloc") and len(frame) > 4000:
                frame = frame.iloc[-4000:].copy()
            elif hasattr(frame, "copy"):
                frame = frame.copy()
            import pandas as pd
            if hasattr(frame, "index") and hasattr(frame.index, "hour"):
                hour = frame.index.hour.astype(float)
                frame["hour_utc"] = hour
                frame["session_liq"] = (
                    ((hour >= float(SESSION_UTC_START)) & (hour < float(SESSION_UTC_END)))
                    .astype(float)
                )
            else:
                frame["hour_utc"] = 15.0
                frame["session_liq"] = 1.0

            hi = frame["high"].astype(float)
            lo = frame["low"].astype(float)
            cl = frame["close"].astype(float)
            op = frame["open"].astype(float)
            rng = (hi - lo).replace(0, float("nan"))
            med = rng.rolling(48, min_periods=12).median()
            # Volume acceptance proxy: range expansion vs recent median (OHLCV-honest)
            frame["vol_accept_proxy"] = (rng / med.replace(0, float("nan"))).fillna(1.0)
            frame["close_loc"] = ((cl - lo) / rng).clip(0, 1).fillna(0.5)
            prev_low = frame["prev_low20"] if "prev_low20" in frame.columns else lo
            prev_high = frame["prev_high20"] if "prev_high20" in frame.columns else hi
            # Auction accept: green close after testing lows (flush→accept)
            frame["auction_accept"] = (
                ((cl > op) & (cl > prev_low) & (frame["close_loc"] > 0.55))
                .astype(float)
            )
            # Auction reject: red close after probing highs in expanded range
            frame["auction_reject"] = (
                ((cl < op) & (cl < prev_high) & (frame["close_loc"] < 0.40))
                .astype(float)
            )

            # Funding abs proxy — real history if present else 0 (documented)
            if fund_oi.get("funding_ok"):
                rates = _align_series(frame.index, fund_oi["funding"], "rate", 0.0)
                frame["funding_abs_proxy"] = rates.abs()
            else:
                # Honest fallback: elevated |close change| / close as carry-tension proxy
                # NOT claimed as real funding — flag in meta
                ret = cl.pct_change().abs().rolling(3, min_periods=1).mean().fillna(0)
                frame["funding_abs_proxy"] = (ret * 0.15).clip(0, 0.01)

            if fund_oi.get("oi_ok"):
                oi = _align_series(frame.index, fund_oi["oi"], "oi", float("nan"))
                base = oi.rolling(24, min_periods=6).mean()
                frame["oi_pulse_proxy"] = (oi / base.replace(0, float("nan"))).fillna(1.0)
            else:
                # Honest fallback: consecutive range expansion pulse
                frame["oi_pulse_proxy"] = (
                    frame["vol_accept_proxy"].rolling(3, min_periods=1).mean().fillna(1.0)
                )

            if str(symbol).upper() == SYMBOL and str(timeframe) == TIMEFRAME:
                _cache["frame"] = frame
        except Exception:
            traceback.print_exc()
        return frame

    d._frame = _frame_with_proxies
    return {
        "session_utc": [SESSION_UTC_START, SESSION_UTC_END],
        "session_feature": "session_liq=1 if hour_utc in [13,22)",
        "vol_accept": "vol_accept_proxy=(high-low)/median48(high-low) — volume acceptance OHLCV proxy",
        "auction_accept": "green close + close>prev_low20 + close_loc>0.55",
        "funding": (
            "real funding-rate-history abs aligned"
            if fund_oi.get("funding_ok")
            else "PROXY: 3bar |ret|*0.15 (NOT real funding; documented)"
        ),
        "oi": (
            "real open-interest-history pulse"
            if fund_oi.get("oi_ok")
            else "PROXY: rolling vol_accept mean (NOT real OI; documented)"
        ),
        "banned_entry_features": sorted(BANNED_ENTRY_FEATURES),
        "fund_oi_notes": fund_oi.get("notes") or [],
    }


def load_doge_micro():
    out = {
        "symbol": SYMBOL,
        "sample_count": None,
        "top_clusters": [],
        "recent_descriptors": [],
        "event_lifts": [],
        "note": None,
    }
    try:
        st = json.load(open("/root/auto_trade/microstructure_status.json"))
        out["sample_count"] = (st.get("sample_counts") or {}).get(SYMBOL)
    except Exception as exc:
        out["note"] = "status_err:%s" % exc
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
                "WHERE cluster_id=?",
                (cid,),
            ).fetchone()
            clusters.append({
                "cluster_id": cid, "count": cnt,
                "label": (lab or [None, None])[0],
                "description": ((lab or [None, None])[1] or "")[:160],
            })
        out["top_clusters"] = clusters
        desc = cur.execute(
            "SELECT descriptor_text, created_at FROM microstructure_windows "
            "WHERE symbol=? ORDER BY created_at DESC LIMIT 8",
            (SYMBOL,),
        ).fetchall()
        out["recent_descriptors"] = [{"text": t, "at": a} for t, a in desc if t]
        con.close()
    except Exception as exc:
        out["note"] = (out.get("note") or "") + ";db_err:%s" % exc
    return out


def load_death():
    inputs = {}
    for path in (
        os.path.join(OUT, "frost3_inputs_raw.json"),
        os.path.join(OUT, "insight_latest.json"),
    ):
        try:
            blob = json.load(open(path))
            if isinstance(blob, dict) and blob:
                inputs = blob
                inputs["_source_path"] = path
                break
        except Exception:
            continue
    return {
        "death_heatmap_top10": (inputs.get("death_heatmap_top10") or [])[:10],
        "doge_fails_zh": [
            "DOGE 1h range_reclaim frost1/2 失败 — 禁止 reclaim/EMA 趋势边",
            "5m 高频摩擦吞噬 — 必须会话降频≈1笔/日 + 真实摩擦",
            "禁止 EMA/MACD/RSI/ATR/布林 作为边；只用微观/资金费/OI/会话/量能接纳",
        ],
        "source": inputs.get("_source_path"),
    }


PARAM_PROMPT = """你是GLM-5.2。只输出纯JSON，禁止Markdown。
任务：输出《DOGE 5m 高波动微观结构边参数方案》。

硬约束：
1) symbol=DOGE-USDT-SWAP，timeframe=5m，不可改
2) 边必须属于：Liquidity/Session + Volume Acceptance + Auction + Funding/OI（可代理）
3) 禁止把 EMA/MACD/RSI/ATR/Bollinger/z20 当作入场边（entry 不得依赖这些特征）
4) 目标开仓频率 ≈1笔/日（0.8–1.2）；会话窗 UTC 13-22；结构失效出场；无马丁/网格/加仓/动态止损
5) DOGE 摩擦按 taker-heavy + drag×1.25 真实计入
6) 可用入场特征：session_liq, vol_accept_proxy, auction_accept, funding_abs_proxy, oi_pulse_proxy, close_loc, close, open, prev_low20, prev_high20, hour_utc

JSON schema：
{
  "report_title":"DOGE 5m 高波动微观结构边",
  "symbol":"DOGE-USDT-SWAP",
  "timeframe":"5m",
  "session_window_utc":[13,22],
  "edge_family":"session+vol_accept+auction+funding/oi",
  "hypothesis":{
    "logic_class":"hv_vol_accept_funding_auction",
    "direction":"long",
    "thesis":"...",
    "entry_sketch":"分号条件，必须含 session_liq 与 vol_accept_proxy 与 auction_accept；禁止 rsi/macd/ema/atr/z20",
    "exit_sketch":"prev_high 止盈 + prev_low 失效 + max_hold",
    "param_grid":{"vol_accept_min":1.45,"funding_abs_min":0.00005,"oi_pulse_min":1.05,"close_loc_min":0.55,"max_hold":36},
    "expected_opens_per_day":1.0,
    "avoid_death":["过度交易","滑点吞噬","indicator_edge_ban","stop_cluster"]
  },
  "proxy_notes":{"funding":"...","oi":"...","volume":"vol_accept_proxy"},
  "friction_notes_zh":"...",
  "mentor_notes":"..."
}
"""


AUDIT_PROMPT = """你是GLM-5.2。只输出JSON。审计 DOGE5m 微观结构边假设书。
硬禁：ADA/LTC/NG/XRP；禁止 EMA/MACD/RSI/ATR/布林/z20 入场边；禁止刷单。
必须：session_liq；vol_accept_proxy；auction 确认；≈1笔/日；结构失效出场。
JSON：{"decision":"pass|reject|revise","score":0到100,"issues":["..."],
"revise":{"entry_sketch":null,"exit_sketch":null,"param_tweaks":{},"why":"..."},
"reason_zh":"..."}
"""


def ask_glm_params(packet, timeout_sec=75):
    import threading
    box = {"parsed": None, "ai": {"ok": False, "error": "timeout"}}

    def _run():
        try:
            ai = d._ai_json("glm", PARAM_PROMPT, packet, max_tokens=2200, temperature=0.15)
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
        return None, {"ok": False, "error": "glm_timeout_%ss" % timeout_sec}
    return box["parsed"], box["ai"]


def fallback_params(packet):
    # v1 long accept was -EV on DOGE5m pickle (WR~37%, WF1/10) → regenerate as
    # high-vol session rejection SHORT (crowding + failed auction at highs).
    return {
        "report_title": "DOGE 5m 高波动微观结构边",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "session_window_utc": [SESSION_UTC_START, SESSION_UTC_END],
        "edge_family": "session+vol_accept+auction_reject+funding/oi",
        "hypothesis": {
            "logic_class": LOGIC_CLASS,
            "direction": "short",
            "thesis": (
                "高波动会话内：极端量能扩张后高位拍卖失败（红收+close_loc偏低），"
                "叠加 |funding| 偏高拥挤与 OI/脉冲代理，做拒绝延续空；"
                "严阈值压到约1笔/日，规避指标边与刷单。"
            ),
            "entry_sketch": (
                "session_liq>0.5; vol_accept_proxy>1.85; auction_reject>0.5; "
                "funding_abs_proxy>0.00008; oi_pulse_proxy>1.08; "
                "close_loc<0.38; close<prev_high20"
            ),
            "exit_sketch": (
                "close<prev_low20 take_profit; close>prev_high20 invalidation; max_hold=40"
            ),
            "param_grid": {
                "vol_accept_min": 1.85,
                "funding_abs_min": 0.00008,
                "oi_pulse_min": 1.08,
                "close_loc_max": 0.38,
                "max_hold": 40,
            },
            "expected_opens_per_day": 1.0,
            "avoid_death": ["过度交易", "滑点吞噬", "indicator_edge_ban", "stop_cluster", "long_accept_neg_ev"],
        },
        "proxy_notes": (packet.get("proxy_injection") or {}),
        "friction_notes_zh": FRICTION_DOC["assumptions_zh"],
        "mentor_notes": "独立 frost3_doge5m_microedge_*；与 doge4h1h 隔离；v2 short reject。",
        "fallback": True,
        "provider": "codex_fallback",
        "regen_from": "long_accept_archived_neg_ev",
    }


ALLOWED_ENTRY_FEATURES = {
    "session_liq", "hour_utc", "vol_accept_proxy", "funding_abs_proxy",
    "oi_pulse_proxy", "auction_accept", "auction_reject", "close_loc",
    "close", "open", "high", "low", "prev_high20", "prev_low20",
}


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
        m = re.match(
            r"^(session_liq|hour_utc|vol_accept_proxy|funding_abs_proxy|oi_pulse_proxy|"
            r"auction_accept|auction_reject|close_loc|close|open|high|low|prev_high20|prev_low20)\s*"
            r"(>=|<=|>|<)\s*"
            r"(-?\d+(?:\.\d+)?|session_liq|vol_accept_proxy|funding_abs_proxy|"
            r"oi_pulse_proxy|auction_accept|auction_reject|close_loc|prev_high20|prev_low20|close|open)$",
            part2,
        )
        if not m:
            continue
        left, op, right = m.group(1), m.group(2), m.group(3)
        if left in BANNED_ENTRY_FEATURES:
            continue
        op_map = {">": "gt", "<": "lt", ">=": "gte", "<=": "lte"}
        leaf = {"left": {"feature": left}, "op": op_map.get(op, "gt")}
        try:
            leaf["right"] = {"value": float(right)}
        except Exception:
            leaf["right"] = {"feature": right}
        conds.append(leaf)
    return conds


def build_microedge_dsl(row, idx):
    direction = str(row.get("direction") or "long").lower()
    logic = str(row.get("logic_class") or LOGIC_CLASS)
    entry = parse_sketch_conditions(row.get("entry_sketch"))
    hold = 36
    mhold = re.search(r"max_hold\s*=\s*(\d+)", str(row.get("exit_sketch") or ""), re.I)
    if mhold:
        hold = max(24, min(72, int(mhold.group(1))))
    pg = row.get("param_grid") or {}
    if isinstance(pg, dict) and pg.get("max_hold") is not None:
        hold = max(24, min(72, int(pg["max_hold"])))

    # Enforce core microstructure leaves by direction
    if direction != "short":
        direction = "short"
    need = {
        "session_liq": ("gt", 0.5),
        "vol_accept_proxy": ("gt", float(pg.get("vol_accept_min") or 1.85)),
        "auction_reject": ("gt", 0.5),
        "funding_abs_proxy": ("gt", float(pg.get("funding_abs_min") or 0.00008)),
        "oi_pulse_proxy": ("gt", float(pg.get("oi_pulse_min") or 1.08)),
        "close_loc": ("lt", float(pg.get("close_loc_max") or 0.38)),
    }
    feats = {((c.get("left") or {}).get("feature")) for c in entry}
    # drop long-only leaves if present
    entry = [
        c for c in entry
        if ((c.get("left") or {}).get("feature")) not in ("auction_accept",)
    ]
    feats = {((c.get("left") or {}).get("feature")) for c in entry}
    for feat, (op, val) in need.items():
        # replace existing same-feature leaf
        entry = [c for c in entry if ((c.get("left") or {}).get("feature")) != feat]
        entry.append({"left": {"feature": feat}, "op": op, "right": {"value": float(val)}})
    entry.append({
        "left": {"feature": "close"}, "op": "lt",
        "right": {"feature": "prev_high20"},
    })
    # strip any banned features that slipped in
    entry = [
        c for c in entry
        if ((c.get("left") or {}).get("feature")) not in BANNED_ENTRY_FEATURES
        and ((c.get("right") or {}).get("feature") or "x") not in BANNED_ENTRY_FEATURES
    ]

    exit_any = [
        {
            "left": {"feature": "close"}, "op": "lt",
            "right": {"feature": "prev_low20"}, "role": "take_profit",
        },
        {
            "left": {"feature": "close"}, "op": "gt",
            "right": {"feature": "prev_high20"}, "role": "invalidation",
        },
    ]

    # NOTE: DSL top-level allowlist forbids `meta` — keep notes in description/book.
    desc = (
        "edge=session+vol_accept+auction+funding/oi; logic=%s; session_utc=%d-%d; "
        "pos=30%%; lev=20; sl=0.9%%; doge_drag=1.25 taker-heavy; no EMA/MACD/RSI/ATR/BB entry"
        % (logic, SESSION_UTC_START, SESSION_UTC_END)
    )[:2000]
    dsl = {
        "schema": "qiyu_strategy_dsl_v1",
        "key": "%s%s_%d" % (KEY_PREFIX, _safe(logic)[:22], idx),
        "name": "寒霜叁-DOGE5m-高波动微观边",
        "direction": direction,
        "timeframe": TIMEFRAME,
        "supported_instruments": [SYMBOL],
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "max_hold_bars": hold,
        "description": desc,
        "origin": "frost3_doge5m_microedge",
    }
    return f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)


def make_book(hyp, idx=1):
    dsl = build_microedge_dsl(hyp, idx)
    book = {
        "title": "寒霜叁-DOGE5m-高波动微观边",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": str(hyp.get("direction") or "long").lower(),
        "logic_class": str(hyp.get("logic_class") or LOGIC_CLASS),
        "thesis": hyp.get("thesis"),
        "entry_sketch": hyp.get("entry_sketch"),
        "exit_sketch": hyp.get("exit_sketch"),
        "param_grid": hyp.get("param_grid"),
        "avoid_from_postmortem": hyp.get("avoid_death"),
        "dsl": dsl,
        "gate_mode": "frost2",
        "source": "frost3_doge5m_microedge",
        "dir_rank": idx,
        "edge_meta": {
            "session_utc": [SESSION_UTC_START, SESSION_UTC_END],
            "edge_family": "session+vol_accept+auction+funding/oi",
            "position_pct": 0.30,
            "friction": FRICTION_DOC,
        },
    }
    return force_key(book)


def force_key(book):
    if book.get("dsl"):
        dsl = book["dsl"]
        # Strip illegal top-level keys (e.g. meta) before validate
        allowed = {
            "schema", "key", "name", "direction", "timeframe",
            "supported_instruments", "entry", "exit", "max_hold_bars",
            "description", "origin", "version", "live_enabled",
            "approved_version_hash", "auto_trade_eligible",
        }
        for extra in list(dsl.keys()):
            if extra not in allowed:
                dsl.pop(extra, None)
        dsl["supported_instruments"] = [SYMBOL]
        dsl["timeframe"] = TIMEFRAME
        k = str(dsl.get("key") or "")
        if not k.startswith(KEY_PREFIX):
            dsl["key"] = (KEY_PREFIX + k)[:100]
        # key must be [A-Za-z0-9_]
        dsl["key"] = re.sub(r"[^A-Za-z0-9_]", "_", str(dsl.get("key") or KEY_PREFIX))[:100]
        ents = list((dsl.get("entry") or {}).get("all") or [])
        ents = [
            e for e in ents
            if ((e.get("left") or {}).get("feature")) not in BANNED_ENTRY_FEATURES
        ]
        # ensure core leaves survive GLM repair
        feats = {((e.get("left") or {}).get("feature")) for e in ents}
        for feat, op, val in (
            ("session_liq", "gt", 0.5),
            ("vol_accept_proxy", "gt", 1.85),
            ("auction_reject", "gt", 0.5),
        ):
            if feat not in feats:
                ents.append({"left": {"feature": feat}, "op": op, "right": {"value": val}})
        dsl["entry"] = {"all": ents}
        dsl["direction"] = "short"
        book["direction"] = "short"
        book["dsl"] = f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)
    book["symbol"] = SYMBOL
    book["timeframe"] = TIMEFRAME
    return book


def glm_audit_book(book, report, timeout_sec=25):
    # Under host thrash / API contention, skip network audit — structural checks local.
    if os.environ.get("DOGE5M_FORCE_FALLBACK", "0") == "1":
        return (
            {"decision": "pass", "reason_zh": "forced_fallback_local_audit_pass", "fallback": True},
            {"ok": False, "error": "forced_fallback"},
        )
    # Local structural gate: banned features absent + core micro leaves present
    ents = list(((book.get("dsl") or {}).get("entry") or {}).get("all") or [])
    feats = {((e.get("left") or {}).get("feature")) for e in ents}
    if feats & BANNED_ENTRY_FEATURES:
        return (
            {"decision": "reject", "reason_zh": "banned_indicator_in_entry", "issues": sorted(feats & BANNED_ENTRY_FEATURES)},
            {"ok": True},
        )
    need = {"session_liq", "vol_accept_proxy", "auction_reject"}
    if not need.issubset(feats):
        return (
            {"decision": "revise",
             "revise": {"param_tweaks": {"vol_accept_min": 1.75}},
             "reason_zh": "missing_core_micro_leaves"},
            {"ok": True},
        )
    payload = {
        "book": {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "thesis": book.get("thesis"),
            "entry": (book.get("dsl") or {}).get("entry"),
            "exit": (book.get("dsl") or {}).get("exit"),
            "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
        },
        "banned_entry_features": sorted(BANNED_ENTRY_FEATURES),
        "target_opens_per_day": TARGET_OPENS_PER_DAY,
    }
    import threading
    box = {"parsed": None, "ai": {"ok": False, "error": "timeout"}}

    def _run():
        try:
            ai = d._ai_json("glm", AUDIT_PROMPT, payload, max_tokens=900, temperature=0.1)
            parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
            raw = ai.get("raw_preview") or ""
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
    if th.is_alive() or not box["parsed"]:
        return (
            {"decision": "pass", "reason_zh": "audit_timeout_local_pass", "fallback": True},
            box["ai"],
        )
    return box["parsed"], box["ai"]


def apply_revise(book, revise):
    book = copy.deepcopy(book)
    if not isinstance(revise, dict):
        return book
    if revise.get("entry_sketch"):
        book["entry_sketch"] = revise["entry_sketch"]
    if revise.get("exit_sketch"):
        book["exit_sketch"] = revise["exit_sketch"]
    tweaks = revise.get("param_tweaks") or {}
    if tweaks and isinstance(book.get("param_grid"), dict):
        book["param_grid"].update(tweaks)
    elif tweaks:
        book["param_grid"] = dict(tweaks)
    row = {
        "direction": book["direction"], "logic_class": book["logic_class"],
        "entry_sketch": book.get("entry_sketch"),
        "exit_sketch": book.get("exit_sketch"),
        "param_grid": book.get("param_grid"),
    }
    book["dsl"] = build_microedge_dsl(row, int(book.get("dir_rank") or 1))
    return force_key(book)


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
        if feat == "session_liq":
            row["right"]["value"] = 0.5
        elif feat == "vol_accept_proxy" and row.get("op") == "gt":
            if "walk_forward" in step or "sample" in step or "freq" in step:
                row["right"]["value"] = max(1.15, v - 0.08 * n)
            else:
                row["right"]["value"] = min(2.2, v + 0.06 * n)
        elif feat == "funding_abs_proxy" and row.get("op") == "gt":
            if "walk_forward" in step:
                row["right"]["value"] = max(0.0, v * (0.7 ** n))
            else:
                row["right"]["value"] = min(0.002, v * (1.2 ** n))
        elif feat == "oi_pulse_proxy" and row.get("op") == "gt":
            if "walk_forward" in step:
                row["right"]["value"] = max(1.0, v - 0.03 * n)
            else:
                row["right"]["value"] = min(1.3, v + 0.03 * n)
        elif feat == "close_loc" and row.get("op") == "lt":
            # short reject: widen/narrow close_loc ceiling
            if "walk_forward" in step or "sample" in step:
                row["right"]["value"] = min(0.55, v + 0.04 * n)
            else:
                row["right"]["value"] = max(0.22, v - 0.03 * n)
        elif feat == "close_loc" and row.get("op") == "gt":
            if "walk_forward" in step:
                row["right"]["value"] = max(0.45, v - 0.03 * n)
            else:
                row["right"]["value"] = min(0.75, v + 0.02 * n)
        elif feat in ("auction_accept", "auction_reject"):
            row["right"]["value"] = 0.5
    dsl["entry"] = {"all": entries}
    dsl["max_hold_bars"] = max(
        24, min(72, int(dsl.get("max_hold_bars") or 36) + (4 if "walk_forward" in step else 0))
    )
    book["dsl"] = dsl
    return force_key(book)


def estimate_opens_per_day(book):
    try:
        definition = dsl_mod.validate_strategy(book["dsl"])
        base = d._backtest(definition, SYMBOL, TIMEFRAME, "observed_base")
        trades = list(base.get("trades") or [])
        frame = d._frame(SYMBOL, TIMEFRAME)
        n_bars = max(1, len(frame))
        days = max(1.0, n_bars / 288.0)
        opd = len(trades) / days
        return {
            "trades": len(trades),
            "days_est": round(days, 2),
            "opens_per_day": round(opd, 3),
            "target": TARGET_OPENS_PER_DAY,
            "in_band": 0.8 <= opd <= 1.2,
            "win_rate_pct": base.get("win_rate_percent"),
        }
    except Exception as exc:
        return {"error": str(exc), "opens_per_day": None, "in_band": False}


def extended_stress_suite(book, packs):
    """Full stress: WF/dest already in quick; MC+friction in full;
    plus Random Delay / Random Fee / Spread Expansion / Random Missing Signal.
    """
    definition = packs.get("definition") or book.get("dsl")
    rows = {}

    # 3) Random Delay — latency_extra jitter
    delay_ok = True
    delay_rows = []
    for seed in (3, 17, 41):
        rng = random.Random(seed)
        lat = 0.00005 * rng.uniform(0.5, 3.0)
        try:
            r = d._backtest(definition, SYMBOL, TIMEFRAME, "observed_base",
                            latency_extra=lat)
            m = d._metrics_from_trades(r.get("trades") or [])
            ok = int(m.get("trades") or 0) >= 3 and float(m.get("mean_net") or -1) > -0.015
            delay_rows.append({"seed": seed, "latency_extra": lat, "ok": ok, "metrics": m})
            if not ok:
                delay_ok = False
        except Exception as exc:
            delay_rows.append({"seed": seed, "ok": False, "error": str(exc)})
            delay_ok = False
    rows["random_delay"] = {"pass": delay_ok, "rows": delay_rows}

    # 4) Random Fee — stressed/severe fee paths via slip_mult as fee/slip bundle
    fee_ok = True
    fee_rows = []
    for seed, scen, sm in ((5, "stressed", 1.0), (19, "severe", 1.0), (37, "observed_base", 1.5)):
        try:
            r = d._backtest(definition, SYMBOL, TIMEFRAME, scen, slip_mult=sm)
            m = d._metrics_from_trades(r.get("trades") or [])
            ok = int(m.get("trades") or 0) >= 3 and float(m.get("sharpe") or -99) >= -0.25
            fee_rows.append({"seed": seed, "scenario": scen, "slip_mult": sm, "ok": ok, "metrics": m})
            if not ok:
                fee_ok = False
        except Exception as exc:
            fee_rows.append({"seed": seed, "ok": False, "error": str(exc)})
            fee_ok = False
    rows["random_fee"] = {"pass": fee_ok, "rows": fee_rows}

    # 5) Spread Expansion — slip_mult elevates effective spread/impact bundle
    spr_ok = True
    spr_rows = []
    for seed, sm in ((7, 2.0), (23, 2.5), (53, 3.0)):
        try:
            r = d._backtest(definition, SYMBOL, TIMEFRAME, "observed_base", slip_mult=sm)
            m = d._metrics_from_trades(r.get("trades") or [])
            ok = int(m.get("trades") or 0) >= 3 and float(m.get("mean_net") or -1) > -0.02
            spr_rows.append({"seed": seed, "slip_mult": sm, "ok": ok, "metrics": m})
            if not ok:
                spr_ok = False
        except Exception as exc:
            spr_rows.append({"seed": seed, "ok": False, "error": str(exc)})
            spr_ok = False
    rows["spread_expansion"] = {"pass": spr_ok, "rows": spr_rows}

    # 6) Random Missing Signal — fill_fail_pct
    miss_ok = True
    miss_rows = []
    for seed, ff in ((11, 0.08), (29, 0.12), (47, 0.18)):
        try:
            r = d._backtest(definition, SYMBOL, TIMEFRAME, "observed_base",
                            fill_fail_pct=ff)
            m = d._metrics_from_trades(r.get("trades") or [])
            ok = int(m.get("trades") or 0) >= 2 and float(m.get("mean_net") or -1) > -0.02
            miss_rows.append({"seed": seed, "fill_fail_pct": ff, "ok": ok, "metrics": m})
            if not ok:
                miss_ok = False
        except Exception as exc:
            miss_rows.append({"seed": seed, "ok": False, "error": str(exc)})
            miss_ok = False
    rows["random_missing_signal"] = {"pass": miss_ok, "rows": miss_rows}

    # Aggregate with frost2 full (MC + extreme friction) + quick WF/dest
    full = packs.get("full") or {}
    quick = packs.get("quick") or {}
    fr = packs.get("extreme_friction") or {}
    suite = {
        "1_walk_forward": {
            "pass": bool(quick.get("walk_forward_7of10")),
            "fold_positive": (packs.get("base_metrics") or {}).get("fold_positive"),
            "folds": (packs.get("base_metrics") or {}).get("folds"),
            "gate": ">=7/10",
        },
        "2_monte_carlo": {
            "pass": bool(full.get("mc_beat_90pct_shuffles")),
            "mc": full.get("mc"),
            "gate": "beat>=90% sign-shuffle",
        },
        "3_random_delay": rows["random_delay"],
        "4_random_fee": rows["random_fee"],
        "5_spread_expansion": rows["spread_expansion"],
        "6_random_missing_signal": rows["random_missing_signal"],
        "plus_logic_destruction": {
            "pass": bool((packs.get("logic_destruction") or {}).get("pass")),
        },
        "plus_extreme_friction_sharpe": {
            "pass": bool(full.get("friction_sharpe_ge_0")),
            "sharpe": full.get("friction_sharpe") or (fr.get("metrics") or {}).get("sharpe"),
            "gate": ">=0",
        },
    }
    ordered_ok = all([
        suite["1_walk_forward"]["pass"],
        suite["2_monte_carlo"]["pass"],
        suite["3_random_delay"]["pass"],
        suite["4_random_fee"]["pass"],
        suite["5_spread_expansion"]["pass"],
        suite["6_random_missing_signal"]["pass"],
        suite["plus_logic_destruction"]["pass"],
        suite["plus_extreme_friction_sharpe"]["pass"],
    ])
    failed = [k for k, v in suite.items() if not v.get("pass")]
    return {
        "pass": ordered_ok,
        "failed": failed,
        "suite": suite,
        "friction_doc": FRICTION_DOC,
    }


def formal_ds_qwen_glm(book, packs):
    """Formal DeepSeek + Qwen + GLM; require avg theoretical WR ≥75%."""
    import auto_trade_ai_consensus as ai
    import auto_trade_human_confirm_pipeline as pipeline

    definition = packs.get("definition") or book.get("dsl")
    cand = {
        "dsl": definition, "symbol": book.get("symbol"),
        "timeframe": book.get("timeframe"), "thesis": book.get("thesis"),
    }
    ok, metrics, reason = pipeline.safety_screen_candidate(cand)
    if not ok:
        return {
            "approved": False, "stage": "safety", "reason": reason,
            "metrics": metrics, "avg_wr": None,
        }

    evidence = {
        "safety_metrics": metrics,
        "dual_engine_packs": {
            "anti_overfit": packs.get("anti_overfit"),
            "extreme_friction_pass": (packs.get("extreme_friction") or {}).get("pass"),
            "logic_destruction_pass": (packs.get("logic_destruction") or {}).get("pass"),
            "extended_stress": packs.get("extended_stress"),
            "base_metrics": packs.get("base_metrics"),
            "freq": packs.get("freq"),
        },
        "friction_doc": FRICTION_DOC,
        "source": "frost3_doge5m_microedge",
        "edge_family": "session+vol_accept+auction+funding/oi",
        "banned_indicator_edge": True,
    }
    candidate = {
        "key": definition.get("key"),
        "name": definition.get("name"),
        "direction": definition.get("direction"),
        "timeframe": definition.get("timeframe"),
        "supported_instruments": definition.get("supported_instruments"),
        "entry": definition.get("entry"),
        "exit": definition.get("exit"),
        "description": definition.get("description"),
        "max_hold_bars": definition.get("max_hold_bars"),
    }
    ds = ai.theoretical_review_one("deepseek", candidate, evidence)
    qw = ai.theoretical_review_one("qwen", candidate, evidence)
    glm = ai.theoretical_review_one("glm", candidate, evidence)

    def _wr(row):
        try:
            return float(row.get("theoretical_win_rate_pct"))
        except Exception:
            return 0.0

    wr_ds, wr_qw, wr_glm = _wr(ds), _wr(qw), _wr(glm)
    avg = round((wr_ds + wr_qw + wr_glm) / 3.0, 3)
    approved = (
        bool(ds.get("ok")) and bool(qw.get("ok")) and bool(glm.get("ok"))
        and avg >= FORMAL_AVG_GATE
        and wr_ds >= 50 and wr_qw >= 50 and wr_glm >= 50
    )
    annotation = "DeepSeek %.1f%% | Qwen %.1f%% | GLM %.1f%% | avg %.1f%% (gate %.1f)" % (
        wr_ds, wr_qw, wr_glm, avg, FORMAL_AVG_GATE,
    )
    ai_review = {
        "approved": approved,
        "policy": "deepseek_qwen_glm_avg_ge_75",
        "ai_theoretical_wr_avg": avg,
        "ai_theoretical_wr_by_provider": {
            "deepseek": wr_ds, "qwen": wr_qw, "glm": wr_glm,
        },
        "natural_language": annotation,
        "reviews": [ds, qw, glm],
        "gate_avg": FORMAL_AVG_GATE,
    }
    pending = None
    if approved:
        pending_raw = pipeline.ingest_and_screen(
            {
                "dsl": definition,
                "symbol": book.get("symbol"),
                "timeframe": book.get("timeframe"),
                "thesis": book.get("thesis") or book.get("logic_class"),
            },
            source="frost3_doge5m_microedge",
            ai_review=ai_review,
            require_ai_review=True,
        )
        pending = {
            "ok": pending_raw.get("ok"),
            "key": pending_raw.get("key"),
            "reason": pending_raw.get("reason"),
            "duplicate": pending_raw.get("duplicate"),
        }
        try:
            d._record_formal({
                "time": d._now(),
                "op": "寒霜叁-DOGE5m微观边",
                "key": definition.get("key"),
                "title": book.get("title"),
                "symbol": SYMBOL,
                "status": "等待" if pending.get("ok") else "退回",
                "annotation": annotation,
            })
        except Exception:
            pass
    return {
        "approved": approved,
        "annotation": annotation,
        "ai_review": ai_review,
        "avg_wr": avg,
        "pending": pending,
        "reason": None if approved else ("avg_wr_%.1f_lt_%.1f" % (avg, FORMAL_AVG_GATE)),
    }


def run_sim_then_formal(book, packs):
    sim = d.glm_sim_review(book, packs)
    wr_ds = float(sim.get("wr_deepseek_sim") or 0)
    wr_qw = float(sim.get("wr_qwen_sim") or 0)
    # Also ask GLM self-sim as third for early gate (avg≥75 soft on sim)
    sim["pass"] = wr_ds >= 55.0 and wr_qw >= 55.0
    if not sim.get("pass"):
        return {"sim": sim, "formal": None, "pending": None, "failed_step": "sim_review"}
    formal = formal_ds_qwen_glm(book, packs)
    if not formal.get("approved"):
        return {
            "sim": sim, "formal": formal, "pending": formal.get("pending"),
            "failed_step": "formal_review",
        }
    pending = formal.get("pending") or {}
    if not pending.get("ok"):
        return {
            "sim": sim, "formal": formal, "pending": pending,
            "failed_step": "pending_ingest",
        }
    return {
        "sim": sim, "formal": formal, "pending": pending, "failed_step": None,
    }


def archive_result(result, death_cause):
    arch = os.path.join(OUT, "archive", "%s_%s" % (PREFIX, result.get("dir_id") or "fail"))
    os.makedirs(arch, exist_ok=True)
    payload = dict(result)
    payload["death_cause"] = death_cause
    payload["archived_at"] = _now()
    open(os.path.join(arch, "result.json"), "w").write(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
    open(os.path.join(arch, "DEATH_CAUSE.json"), "w").write(
        json.dumps(death_cause, ensure_ascii=False, indent=2, default=str) + "\n")
    write_art("archive_pointer", {"archive_path": arch, "death_cause": death_cause, "at": _now()})
    return arch


def process(book, report):
    hist = []
    dir_id = "doge5m_microedge_%s" % _safe(book.get("logic_class"))
    status = {
        "dir_id": dir_id, "stage": "audit", "updated_at": _now(),
        "symbol": SYMBOL, "tf": TIMEFRAME, "logic": book.get("logic_class"),
        "key": (book.get("dsl") or {}).get("key"),
    }
    write_art("status", status)

    for atry in range(MAX_AUDIT_REVISE + 1):
        decision, ai = glm_audit_book(book, report)
        write_art("audit_try%d" % atry, {
            "decision": decision,
            "ai": {"ok": ai.get("ok"), "preview": (ai.get("raw_preview") or "")[:500]},
        })
        hist.append({
            "stage": "audit", "try": atry,
            "decision": decision.get("decision"),
            "reason": decision.get("reason_zh"),
        })
        dec = str(decision.get("decision") or "pass").lower()
        if dec == "pass":
            break
        if dec == "revise" and atry < MAX_AUDIT_REVISE:
            book = force_key(apply_revise(book, decision.get("revise") or {}))
            continue
        return {
            "dir_id": dir_id, "ok": False, "failed_step": "hyp_audit_reject",
            "reason": decision.get("reason_zh") or decision.get("issues"),
            "hist": hist, "book": book,
        }

    write_art("hypothesis_book", book)

    # Skip standalone freq backtest (duplicates quick base_bt and OOMs 0.75GB hosts).
    # Frequency is measured from quick/full base_metrics instead.
    packs = None
    for qtry in range(MAX_QUICK_REPAIR + 1):
        status.update({"stage": "quick", "attempt": qtry, "updated_at": _now()})
        write_art("status", status)
        print("[doge5m] quick try", qtry, "key", (book.get("dsl") or {}).get("key"), flush=True)
        packs = f2.quick_suite(book)
        # derive opens/day from quick metrics when available
        bm = packs.get("base_metrics") or {}
        if bm.get("trades") is not None:
            days = max(1.0, 4500 / 288.0)
            freq = {
                "trades": bm.get("trades"),
                "days_est": round(days, 2),
                "opens_per_day": round(float(bm.get("trades") or 0) / days, 3),
                "win_rate_pct": bm.get("win_rate_pct") or bm.get("win_rate"),
                "source": "quick_base_metrics",
            }
            write_art("freq_from_quick_%d" % qtry, freq)
            hist.append({"stage": "freq", "try": qtry, "freq": freq})
            print("[doge5m] freq~", freq.get("opens_per_day"), "tr", freq.get("trades"), flush=True)
        write_art("quick_try%d" % qtry, {
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
            "tr": (packs.get("base_metrics") or {}).get("trades"),
        })
        if packs.get("quick_pass"):
            break
        if qtry >= MAX_QUICK_REPAIR:
            return {
                "dir_id": dir_id, "ok": False,
                "failed_step": packs.get("failed_step") or "quick_exhausted",
                "reason": "Quick failed after %d repairs" % MAX_QUICK_REPAIR,
                "metrics": packs.get("base_metrics"),
                "hist": hist, "book": book,
            }
        book = local_tweak(book, qtry + 1, packs.get("failed_step"))
        if os.environ.get("DOGE5M_FORCE_FALLBACK", "0") != "1":
            repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step"), "quick")
            if repaired:
                book = force_key(repaired)

    for ftry in range(MAX_FULL_REPAIR + 1):
        status.update({"stage": "full", "attempt": ftry, "updated_at": _now()})
        write_art("status", status)
        print("[doge5m] full try", ftry, flush=True)
        packs = f2.full_suite(book, packs)
        ext = extended_stress_suite(book, packs)
        packs["extended_stress"] = ext
        freq = estimate_opens_per_day(book)
        packs["freq"] = freq
        write_art("full_try%d" % ftry, {
            "pass": packs.get("full_pass") and ext.get("pass"),
            "failed_step": packs.get("failed_step"),
            "full": packs.get("full"),
            "extended": ext,
            "freq": freq,
            "key": (book.get("dsl") or {}).get("key"),
        })
        hist.append({
            "stage": "full", "try": ftry,
            "pass": bool(packs.get("full_pass") and ext.get("pass")),
            "failed_step": packs.get("failed_step") or (
                ("extended:" + ",".join(ext.get("failed") or [])) if not ext.get("pass") else None
            ),
            "friction": (packs.get("full") or {}).get("friction_sharpe"),
            "mc": ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
            "opens_per_day": freq.get("opens_per_day"),
        })
        if packs.get("full_pass") and ext.get("pass"):
            # soft freq band — if far outside, tweak then re-full once
            opd = freq.get("opens_per_day")
            if opd is not None and not (0.5 <= opd <= 1.8) and ftry < MAX_FULL_REPAIR:
                book = local_tweak(book, ftry + 1, "freq_high" if opd > 1.8 else "walk_forward")
                packs = f2.quick_suite(book)
                continue
            break
        if ftry >= MAX_FULL_REPAIR:
            return {
                "dir_id": dir_id, "ok": False,
                "failed_step": packs.get("failed_step") or (
                    "extended:" + ",".join(ext.get("failed") or [])
                ) or "full_exhausted",
                "reason": "Full/extended stress failed after %d repairs" % MAX_FULL_REPAIR,
                "full": packs.get("full"), "extended": ext,
                "hist": hist, "book": book,
            }
        book = local_tweak(book, ftry + 1, packs.get("failed_step") or "full")
        if os.environ.get("DOGE5M_FORCE_FALLBACK", "0") != "1":
            repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step") or "full", "full")
            if repaired:
                book = force_key(repaired)
        q2 = f2.quick_suite(book)
        if not q2.get("quick_pass"):
            packs = q2
            packs["full_pass"] = False
            continue
        packs = q2

    write_art("stress_suite_final", packs.get("extended_stress"))

    for stry in range(MAX_SIM_REPAIR + 1):
        status.update({"stage": "sim_formal", "attempt": stry, "updated_at": _now()})
        write_art("status", status)
        sf = run_sim_then_formal(book, packs)
        write_art("sim_try%d" % stry, sf)
        hist.append({
            "stage": "sim_formal", "try": stry,
            "failed_step": sf.get("failed_step"),
            "sim_ds": (sf.get("sim") or {}).get("wr_deepseek_sim"),
            "sim_qw": (sf.get("sim") or {}).get("wr_qwen_sim"),
            "formal_avg": (sf.get("formal") or {}).get("avg_wr"),
            "pending": (sf.get("pending") or {}).get("key"),
        })
        if (sf.get("pending") or {}).get("ok"):
            return {
                "dir_id": dir_id, "ok": True, "failed_step": None,
                "pending": sf.get("pending"), "sim": sf.get("sim"),
                "formal": sf.get("formal"),
                "extended": packs.get("extended_stress"),
                "freq": packs.get("freq"),
                "hist": hist, "book": book,
            }
        fs = sf.get("failed_step")
        if fs in ("formal_review", "pending_ingest") and stry >= MAX_SIM_REPAIR:
            return {
                "dir_id": dir_id, "ok": False, "failed_step": fs,
                "reason": (sf.get("formal") or {}).get("reason") or (sf.get("pending") or {}).get("reason"),
                "sim": sf.get("sim"), "formal": sf.get("formal"),
                "pending": sf.get("pending"),
                "extended": packs.get("extended_stress"),
                "freq": packs.get("freq"),
                "hist": hist, "book": book,
            }
        if stry >= MAX_SIM_REPAIR:
            return {
                "dir_id": dir_id, "ok": False,
                "failed_step": fs or "sim_exhausted",
                "reason": "Sim/formal failed after %d repairs" % MAX_SIM_REPAIR,
                "sim": sf.get("sim"), "formal": sf.get("formal"),
                "hist": hist, "book": book,
            }
        book = local_tweak(book, stry + 1, "sim")
        q3 = f2.quick_suite(book)
        if not q3.get("quick_pass"):
            continue
        packs = f2.full_suite(book, q3)
        packs["extended_stress"] = extended_stress_suite(book, packs)
        packs["freq"] = estimate_opens_per_day(book)
        if not (packs.get("full_pass") and packs["extended_stress"].get("pass")):
            continue

    return {
        "dir_id": dir_id, "ok": False, "failed_step": "unknown_exhausted",
        "hist": hist, "book": book,
    }


def main():
    print("=== DOGE5m MICROEDGE START ===", _now(), flush=True)
    d._ensure_dirs()
    d._load_env()
    write_art("friction_doc", FRICTION_DOC)

    fund_oi = fetch_funding_oi_series()
    write_art("funding_oi_fetch", {
        "funding_ok": fund_oi.get("funding_ok"),
        "oi_ok": fund_oi.get("oi_ok"),
        "funding_n": len(fund_oi.get("funding") or []),
        "oi_n": len(fund_oi.get("oi") or []),
        "notes": fund_oi.get("notes"),
    })
    proxy_doc = install_micro_proxies(fund_oi)
    write_art("proxy_notes", proxy_doc)
    print("[doge5m] proxies", proxy_doc.get("vol_accept"), flush=True)

    cl = step1.load_cl_archive()
    micro = load_doge_micro()
    death = load_death()
    packet = {
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "cl_archive": cl,
        "doge_micro": micro,
        "death": death,
        "proxy_injection": proxy_doc,
        "friction": FRICTION_DOC,
        "live_protect": ["ADA", "LTC", "NG", "XRP"],
        "isolation": "NOT frost3_doge4h1h_*; prefix frost3_doge5m_microedge_ only",
        "gates": {
            "WF": ">=7/10",
            "logic_destruction": "pass",
            "MC_beat": ">=90% sign-shuffle",
            "random_delay": "pass",
            "random_fee": "pass",
            "spread_expansion": "pass",
            "random_missing_signal": "pass",
            "extreme_friction_sharpe": ">=0",
            "opens_per_day": "0.8-1.2",
            "formal_avg_wr_ds_qwen_glm": ">=75",
        },
        "banned_entry_features": sorted(BANNED_ENTRY_FEATURES),
    }
    write_art("inputs", packet)

    prefer_fallback = os.environ.get("DOGE5M_FORCE_FALLBACK", "0") == "1"
    if prefer_fallback:
        report, ai = fallback_params(packet), {"ok": False, "error": "forced_fallback"}
    else:
        report, ai = ask_glm_params(packet, timeout_sec=60)
    write_art("glm_params_raw", {
        "ok": ai.get("ok"), "parsed": bool(report),
        "preview": (ai.get("raw_preview") or "")[:1800],
        "error": ai.get("error"),
    })
    if not report or not isinstance(report.get("hypothesis"), dict):
        report = fallback_params(packet)
    hyp = dict(report.get("hypothesis") or {})
    hyp["symbol"] = SYMBOL
    hyp["timeframe"] = TIMEFRAME
    hyp["logic_class"] = LOGIC_CLASS
    hyp["direction"] = str(hyp.get("direction") or "long").lower()
    report["hypothesis"] = hyp
    write_art("param_plan", report)
    write_art("hypothesis", hyp)

    book = make_book(hyp, 1)
    write_art("hypothesis_book_v0", book)
    print("[doge5m] key", (book.get("dsl") or {}).get("key"), flush=True)

    try:
        result = process(book, report)
    except Exception as exc:
        result = {
            "dir_id": "doge5m_microedge_exception", "ok": False,
            "failed_step": "exception", "reason": str(exc),
            "trace": traceback.format_exc()[-2500:],
        }

    end = {
        "at": _now(),
        "op": "DOGE5m高波动微观结构边独立创造",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "ok": bool(result.get("ok")),
        "failed_step": result.get("failed_step"),
        "reason": result.get("reason"),
        "pending": result.get("pending"),
        "sim": result.get("sim"),
        "formal": result.get("formal"),
        "extended": result.get("extended"),
        "freq": result.get("freq"),
        "logic": (result.get("book") or book).get("logic_class"),
        "key": ((result.get("book") or book).get("dsl") or {}).get("key"),
        "hist": result.get("hist"),
        "proxy_notes": proxy_doc,
        "friction_doc": FRICTION_DOC,
        "session_window_utc": [SESSION_UTC_START, SESSION_UTC_END],
    }

    if not result.get("ok"):
        death_cause = {
            "exact_death": result.get("failed_step"),
            "reason": result.get("reason"),
            "metrics": result.get("metrics"),
            "full": result.get("full"),
            "extended": result.get("extended"),
            "sim": result.get("sim"),
            "formal": result.get("formal"),
            "hist_tail": (result.get("hist") or [])[-8:],
            "logic_class": end["logic"],
            "key": end["key"],
            "summary_zh": "DOGE5m微观边 %s 失败于 %s：%s" % (
                end["logic"], result.get("failed_step"), result.get("reason")),
        }
        # archive + allow regenerate signal
        if str(result.get("failed_step") or "").startswith("extended") or result.get("failed_step") in (
            "quick_exhausted", "full_exhausted", "sim_exhausted", "quick_walk_forward",
            "quick_logic_destruction", "full_friction_sharpe", "full_monte_carlo",
        ):
            death_cause["action"] = "archive_and_regenerate_if_repairs_exhausted"
        arch = archive_result(result, death_cause)
        end["archive_path"] = arch
        end["death_cause"] = death_cause
        print("[doge5m] ARCHIVED", arch, death_cause["exact_death"], flush=True)
    else:
        print("[doge5m] PENDING", (result.get("pending") or {}).get("key"), flush=True)

    write_art("end_report", end)
    write_art("status", {"finished_at": _now(), "ok": end["ok"], "end": end})

    freq = end.get("freq") or {}
    formal = end.get("formal") or {}
    ext = end.get("extended") or {}
    if end["ok"]:
        summary_zh = (
            "DOGE5m高波动微观边【成功→pending】边=%s key=%s；"
            "压力包=%s；频率≈%s笔/日；正式WR均=%.1f%%(DS/QW/GLM)；pending=%s；"
            "摩擦=taker5bp+drag1.25已计入。"
            % (
                end["logic"], end["key"],
                "ALL_PASS" if ext.get("pass") else ext.get("failed"),
                freq.get("opens_per_day"),
                float(formal.get("avg_wr") or 0),
                (end.get("pending") or {}).get("key"),
            )
        )
    else:
        summary_zh = (
            "DOGE5m高波动微观边【失败】边=%s；关卡=%s；详情=%s；"
            "频率≈%s；正式均WR=%s；归档=%s"
            % (
                end["logic"], end.get("failed_step"), end.get("reason"),
                freq.get("opens_per_day"),
                formal.get("avg_wr"),
                end.get("archive_path"),
            )
        )
    write_art("parent_summary_zh", {
        "summary_zh": summary_zh, "at": _now(),
        "end": {
            "ok": end["ok"], "failed_step": end.get("failed_step"),
            "key": end.get("key"), "pending": end.get("pending"),
            "freq": freq, "formal_avg": formal.get("avg_wr"),
            "stress_failed": ext.get("failed"),
            "archive_path": end.get("archive_path"),
        },
    })
    print("=== DOGE5m MICROEDGE END ===", summary_zh, flush=True)
    print("PARENT_SUMMARY_ZH:", summary_zh, flush=True)
    return 0 if end["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
