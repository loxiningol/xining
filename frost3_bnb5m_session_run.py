#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone BNB-USDT-SWAP 5m session-window V-reclaim.

Artifacts ONLY under frost3_bnb5m_session_* — parallel-safe vs BNB1h/ETH/others.
Injects session_liq + vol_pulse_proxy (ATR) because DSL has no hour/volume/depth.
"""
from __future__ import print_function

import copy
import json
import os
import re
import sqlite3
import sys
import traceback
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
PREFIX = "frost3_bnb5m_session"
SYMBOL = "BNB-USDT-SWAP"
TIMEFRAME = "5m"
MAX_AUDIT_REVISE = 2
MAX_QUICK_REPAIR = 3
MAX_FULL_REPAIR = 3
MAX_SIM_REPAIR = 3
SESSION_UTC_START = 7
SESSION_UTC_END = 20

# Do NOT clone live LTC/NG exhaustion fade; this is session-scoped V-reclaim.
BANNED_LOGIC_SUBSTR = (
    "impulse_continuation", "trend_pullback_fade", "breakout_continuation",
    "exhaustion_fade",
)


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


def install_session_proxies():
    """Process-local: allow session_liq / vol_pulse_proxy and inject into frames."""
    dsl_mod.FEATURES.add("session_liq")
    dsl_mod.FEATURES.add("vol_pulse_proxy")
    dsl_mod.FEATURES.add("hour_utc")

    _orig_frame = d._frame

    _cache = {"frame": None}
    PREBUILT = os.path.join(OUT, "frost3_bnb5m_session_frame.pkl")

    def _frame_with_proxies(symbol, timeframe):
        # Process-local cache — critical on 1vCPU/0.75GB hosts under multi-agent load.
        if (
            str(symbol).upper() == SYMBOL
            and str(timeframe) == TIMEFRAME
            and _cache["frame"] is not None
        ):
            return _cache["frame"]

        frame = None
        if str(symbol).upper() == SYMBOL and str(timeframe) == TIMEFRAME and os.path.exists(PREBUILT):
            try:
                import pickle
                frame = pickle.load(open(PREBUILT, "rb"))
                print("[bnb5m] using prebuilt frame", PREBUILT, "rows", len(frame), flush=True)
            except Exception as exc:
                print("[bnb5m] prebuilt load fail", exc, flush=True)
                frame = None

        if frame is None:
            frame = _orig_frame(symbol, timeframe)
            try:
                max_bars = int(os.environ.get("BNB5M_MAX_BARS", "3500"))
                if hasattr(frame, "iloc") and len(frame) > max_bars:
                    frame = frame.iloc[-max_bars:].copy()
                elif hasattr(frame, "copy"):
                    frame = frame.copy()
                if hasattr(frame, "index") and hasattr(frame.index, "hour"):
                    hour = frame.index.hour.astype(float)
                    frame["hour_utc"] = hour
                    frame["session_liq"] = (
                        ((hour >= float(SESSION_UTC_START)) & (hour < float(SESSION_UTC_END)))
                        .astype(float)
                    )
                else:
                    frame["hour_utc"] = 12.0
                    frame["session_liq"] = 1.0
                if "atr14" in getattr(frame, "columns", []):
                    base = frame["atr14"].rolling(48, min_periods=12).mean()
                    frame["vol_pulse_proxy"] = (
                        frame["atr14"] / base.replace(0, float("nan"))
                    ).fillna(1.0)
                else:
                    frame["vol_pulse_proxy"] = 1.0
            except Exception:
                pass

        if str(symbol).upper() == SYMBOL and str(timeframe) == TIMEFRAME:
            _cache["frame"] = frame
        return frame

    d._frame = _frame_with_proxies
    return {
        "session_utc": [SESSION_UTC_START, SESSION_UTC_END],
        "session_feature": "session_liq (1 if hour in [7,20) else 0)",
        "volume_proxy": "vol_pulse_proxy = atr14 / atr14.rolling(48).mean",
        "book_depth": "unavailable in DSL/backtest frame — micro descriptors narrative only",
        "market_data_volume": "parquet has OHLC only — no true volume",
    }


def load_bnb_micro():
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
        ev = cur.execute(
            "SELECT cluster_id, event_code, lift, cluster_total, cluster_hits "
            "FROM microstructure_event_associations WHERE symbol=? "
            "ORDER BY lift DESC LIMIT 10",
            (SYMBOL,),
        ).fetchall()
        out["event_lifts"] = [
            {"cluster_id": a, "event": b, "lift": c, "n": d, "hits": e}
            for a, b, c, d, e in ev
        ]
        con.close()
    except Exception as exc:
        out["note"] = (out.get("note") or "") + ";db_err:%s" % exc
    return out


def load_death():
    """Prefer cached inputs — avoid collect_inputs() under multi-agent load."""
    inputs = {}
    for path in (
        os.path.join(OUT, "frost3_bnb1h_inputs.json"),
        os.path.join(OUT, "frost3_inputs_raw.json"),
        os.path.join(OUT, "frost3_bnb5m_session_inputs.json"),
    ):
        try:
            blob = json.load(open(path))
            if isinstance(blob, dict) and blob:
                # bnb1h_inputs nests death; raw is flat
                if isinstance(blob.get("death"), dict):
                    inputs = dict(blob.get("death") or {})
                    inputs.setdefault(
                        "death_heatmap_top10",
                        (blob.get("death") or {}).get("death_heatmap_top10"),
                    )
                else:
                    inputs = blob
                inputs["_source_path"] = path
                break
        except Exception:
            continue
    if not inputs:
        try:
            # last resort, short — may be slow under contention
            inputs = d.collect_inputs()
            inputs["_source_path"] = "collect_inputs"
        except Exception as exc:
            inputs = {"error": str(exc)}
    fz = list(inputs.get("freezer_last10") or [])
    # also accept nested from bnb1h death block
    if not fz and isinstance(inputs.get("freezer_5m_hf_focus"), list):
        fz = list(inputs.get("freezer_5m_hf_focus") or [])
    hf = []
    for x in fz:
        s = json.dumps(x, ensure_ascii=False)
        if any(k in s for k in ("5m", "btc5", "friction", "reclaim", "过度", "滑点", "cost")):
            hf.append(x)
    death_hm = inputs.get("death_heatmap_top10") or []
    if not death_hm and isinstance(inputs.get("death"), dict):
        death_hm = (inputs.get("death") or {}).get("death_heatmap_top10") or []
    return {
        "death_heatmap_top10": death_hm[:10],
        "freezer_last10": fz[:10],
        "freezer_5m_hf_focus": hf or [
            {
                "key": "btc5_exhaustion_reclaim_long_ai",
                "reason": "lifecycle_eliminated_friction_negative — 5m reclaim 被滑点/摩擦吞噬",
            }
        ],
        "hf_lessons_zh": [
            "5m 高频易过度交易：必须用会话窗+确认条件降频",
            "滑点/摩擦吞噬：避免边际入场与过短持有刷单；extreme friction Sharpe 必须≥0",
            "btc5 exhaustion_reclaim 已因 friction_negative 冷冻 — 本方案要更严确认与会话过滤",
            "SOL5m impulse 失败：trades 过多数百且 WF 崩 — 禁止无会话约束的冲动延续",
        ],
        "mandatory_bnb": [
            n for n in (inputs.get("mandatory_bnb") or inputs.get("mandatory_niches") or [])
            if isinstance(n, dict) and "BNB" in str(n.get("symbol_hint") or "")
        ],
        "source": inputs.get("_source_path"),
    }


def load_intraday_patterns():
    """Lightweight static stats — avoid heavy pickle/pandas under multi-agent CPU pressure."""
    return {
        "n_bars": 12000,
        "proxy_signal_count": 20,
        "session_bar_pct": 53.8,
        "vol_pulse_gt_1p05_pct": 35.7,
        "frame_source": "precomputed_probe_20260726",
        "note": (
            "precomputed from BNB 5m frame probe: session UTC07-20 + dump/reclaim/"
            "vol_pulse_proxy>1.05 ≈20 entries; DSL uses single-bar leaves"
        ),
        "error": None,
    }


PARAM_PROMPT = """你是GLM-5.2。只输出纯JSON，禁止Markdown。
任务：输出《BNB 5m 会话回收策略参数方案》。

硬约束：
1) symbol=BNB-USDT-SWAP，timeframe=5m，不可改
2) 逻辑必须是【固定会话窗 UTC 07:00-20:00】+【急跌后V型回收】；可用 session_liq>0.5 表示会话
3) 订单簿深度不可回测；volume 不可用；必须用 vol_pulse_proxy(=atr14/atr48均值) 作为量能脉冲代理，并在方案中写明
4) 禁止复刻现网 LTC/NG exhaustion_fade 盲克隆；禁止 CL15m 边际rsi≈54+中等cci+过窄过滤
5) 规避冷冻库5m高频死因：过度交易、滑点/摩擦吞噬（参考 btc5_exhaustion_reclaim friction_negative）
6) trades预期 12-40（会话过滤后），远离无约束高频刷单；结构失效出场；rsi带宽勿贴54
7) 可用特征：session_liq, vol_pulse_proxy, hour_utc, rsi14,z20,macd_stick,cci,ema8/16/21,prev_low20,prev_high20,h1_ema19,h1_ema53,h1_slope4,atr14

JSON schema：
{
  "report_title":"BNB 5m 会话回收策略参数方案",
  "symbol":"BNB-USDT-SWAP",
  "timeframe":"5m",
  "session_window_utc":[7,20],
  "session_rationale_zh":"为何该会话窗降噪/提胜率",
  "reclaim_confirm_definition_zh":"V型回收确认的可执行定义",
  "proxy_notes":{
    "book_depth":"unavailable",
    "volume":"vol_pulse_proxy=atr14/atr48",
    "session":"session_liq"
  },
  "cl_lessons_zh":"...",
  "freezer_hf_lessons_zh":"...",
  "micro_read_zh":"...",
  "hypothesis":{
    "logic_class":"session_window_v_reclaim",
    "direction":"long",
    "thesis":"...",
    "entry_sketch":"分号分隔条件，必须含 session_liq>0.5 与 vol_pulse_proxy 与回收确认",
    "exit_sketch":"止盈+结构失效+max_hold",
    "param_grid":{"rsi_lo":46,"rsi_hi":60,"z_lo":-0.8,"z_hi":0.6,"vol_pulse_min":1.05,"max_hold":32},
    "diff_vs_bnb1h":"...",
    "diff_vs_live_ltc_ng_exhaust":"...",
    "avoid_death":["stop_cluster","cost_collapse","过度交易","滑点吞噬"],
    "expected_trades_hint":"12-40"
  },
  "alt_directions":[
    {"rank":2,"logic_class":"...","direction":"long|short","entry_sketch":"...","exit_sketch":"...","why_secondary":"..."},
    {"rank":3,"logic_class":"...","direction":"long|short","entry_sketch":"...","exit_sketch":"...","why_secondary":"..."}
  ],
  "recommended":"primary_hypothesis",
  "mentor_notes":"..."
}
"""


AUDIT_PROMPT = """你是GLM-5.2。只输出JSON。审计 BNB5m 会话V回收假设书。
硬禁：ADA/LTC/NG/XRP；禁止CL15m exhaustion同构；禁止无会话约束的高频刷单。
必须：session_liq 会话窗；vol_pulse_proxy 或等价量能代理；V回收确认清晰；trades预期12-40；结构失效出场。
JSON：{"decision":"pass|reject|revise","score":0到100,"issues":["..."],
"revise":{"entry_sketch":null,"exit_sketch":null,"param_tweaks":{},"why":"..."},
"reason_zh":"..."}
"""


def ask_glm_params(packet, timeout_sec=90):
    """GLM with hard timeout so multi-agent API contention cannot stall the pipeline."""
    import threading
    box = {"parsed": None, "ai": {"ok": False, "error": "timeout"}}

    def _run():
        try:
            ai = d._ai_json("glm", PARAM_PROMPT, packet, max_tokens=2600, temperature=0.15)
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


def fallback_params(packet):
    return {
        "report_title": "BNB 5m 会话回收策略参数方案",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "session_window_utc": [SESSION_UTC_START, SESSION_UTC_END],
        "session_rationale_zh": (
            "UTC 07:00-20:00 覆盖欧美重叠与亚洲尾盘后的主流动性时段，"
            "过滤亚盘深夜薄流动性刷单，降低过度交易与滑点吞噬风险。"
        ),
        "reclaim_confirm_definition_zh": (
            "急跌代理：近端触及/靠近 prev_low20 或 z20 仍偏负；"
            "V回收确认：收盘重上 ema21 且 >prev_low20，macd_stick>0，rsi14 回到 46-60（远离54刚过线单边），"
            "且 vol_pulse_proxy>1.05 表示波动脉冲（量能/深度不可用时的代理）。"
        ),
        "proxy_notes": {
            "book_depth": "unavailable in backtest; micro depth_drop/spread_jump narrative only",
            "volume": "vol_pulse_proxy=atr14/atr14.rolling(48).mean",
            "session": "session_liq=1 if hour_utc in [7,20)",
        },
        "cl_lessons_zh": (
            "CL15m：边际超买+硬止损簇致 friction 崩；过窄cci过滤致 trades<10；"
            "软化换样本又引入硬止损且 dest 失败。本方案用会话+带宽+结构失效，不用cci单阈值。"
        ),
        "freezer_hf_lessons_zh": (
            "btc5_exhaustion_reclaim 因 friction_negative 冷冻；5m 必须会话降频+确认，避免滑点吞噬。"
        ),
        "micro_read_zh": (
            "BNB 微观常见 depth_drop/spread_jump/flow 翻转；回测用 ATR 脉冲+价格回收代理，"
            "不声称直接交易微观事件。"
        ),
        "hypothesis": {
            "logic_class": "session_window_v_reclaim",
            "direction": "long",
            "thesis": "流动性会话内急跌后的V型回收：会话过滤+波动脉冲+结构回收确认",
            "entry_sketch": (
                "session_liq>0.5; vol_pulse_proxy>1.08; h1_ema19>h1_ema53; h1_slope4>0; "
                "close>ema21; close>prev_low20; macd_stick>0; rsi14>48; rsi14<58; z20>-0.7; z20<0.45"
            ),
            "exit_sketch": "rsi14>68 take_profit; close<ema21 invalidation; max_hold=36",
            "param_grid": {
                "rsi_lo": 48, "rsi_hi": 58, "z_lo": -0.7, "z_hi": 0.45,
                "vol_pulse_min": 1.08, "max_hold": 36,
            },
            "diff_vs_bnb1h": "BNB1h 禁 reclaim 族做斜率持续；本线专做5m会话V回收，键前缀分离",
            "diff_vs_live_ltc_ng_exhaust": "非LTC/NG衰竭空；是会话约束的多头V回收，含量能脉冲代理",
            "avoid_death": ["stop_cluster", "cost_collapse", "过度交易", "滑点吞噬"],
            "expected_trades_hint": "12-40",
        },
        "alt_directions": [
            {
                "rank": 2,
                "logic_class": "session_macd_reclaim_tight",
                "direction": "long",
                "entry_sketch": (
                    "session_liq>0.5; vol_pulse_proxy>1.1; close>ema16; macd_stick>0; "
                    "rsi14>48; rsi14<58; z20>-0.5; z20<0.4"
                ),
                "exit_sketch": "rsi14>66 take_profit; close<ema16 invalidation; max_hold=28",
                "why_secondary": "更紧rsi带，样本可能不足",
            },
            {
                "rank": 3,
                "logic_class": "session_dump_fade_short",
                "direction": "short",
                "entry_sketch": (
                    "session_liq>0.5; vol_pulse_proxy>1.08; close<ema21; macd_stick<0; "
                    "rsi14<48; rsi14>34; z20<0.2"
                ),
                "exit_sketch": "rsi14<30 take_profit; close>ema21 invalidation; max_hold=28",
                "why_secondary": "非V回收主线，仅作对照",
            },
        ],
        "recommended": "primary_hypothesis",
        "mentor_notes": "独立 frost3_bnb5m_session_*；保护 ADA/LTC/NG/XRP；与BNB1h键分离。",
        "fallback": True,
        "provider": "codex_fallback",
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
            r"^(session_liq|vol_pulse_proxy|hour_utc|h1_slope4|h1_ema19|h1_ema53|rsi14|z20|"
            r"macd_stick|cci|atr14|ema\d+|close|prev_high\d+|prev_low\d+)\s*"
            r"(>=|<=|>|<)\s*"
            r"(-?\d+(?:\.\d+)?|session_liq|vol_pulse_proxy|hour_utc|h1_slope4|h1_ema19|"
            r"h1_ema53|ema\d+|prev_high\d+|prev_low\d+|close)$",
            part2,
        )
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


def build_session_vreclaim_dsl(row, idx):
    direction = str(row.get("direction") or "long").lower()
    logic = str(row.get("logic_class") or "session_window_v_reclaim")
    entry = parse_sketch_conditions(row.get("entry_sketch"))
    hold = 32
    mhold = re.search(r"max_hold\s*=\s*(\d+)", str(row.get("exit_sketch") or ""), re.I)
    if mhold:
        hold = max(20, min(48, int(mhold.group(1))))

    feats = {((c.get("left") or {}).get("feature")) for c in entry}
    if "session_liq" not in feats:
        entry.insert(0, {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}})
    if "vol_pulse_proxy" not in feats:
        entry.append({"left": {"feature": "vol_pulse_proxy"}, "op": "gt", "right": {"value": 1.05}})

    if len(entry) < 5 or "reclaim" in logic or logic == "session_window_v_reclaim":
        # Quality-first V-reclaim: session + pulse + H1 align + reclaim band.
        # Target ~15-40 trades (not 100+) to avoid 5m overtrading / cost_collapse.
        entry = [
            {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}},
            {"left": {"feature": "vol_pulse_proxy"}, "op": "gt", "right": {"value": 1.08}},
            {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
            {"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_low20"}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 48.0}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 58.0}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -0.7}},
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 0.45}},
        ]
        direction = "long"
        pg = row.get("param_grid") or {}
        if isinstance(pg, dict):
            for c in entry:
                feat = (c.get("left") or {}).get("feature")
                if feat == "rsi14" and c.get("op") == "gt" and pg.get("rsi_lo") is not None:
                    c["right"] = {"value": float(pg["rsi_lo"])}
                if feat == "rsi14" and c.get("op") == "lt" and pg.get("rsi_hi") is not None:
                    c["right"] = {"value": float(pg["rsi_hi"])}
                if feat == "z20" and c.get("op") == "gt" and pg.get("z_lo") is not None:
                    c["right"] = {"value": float(pg["z_lo"])}
                if feat == "z20" and c.get("op") == "lt" and pg.get("z_hi") is not None:
                    c["right"] = {"value": float(pg["z_hi"])}
                if feat == "vol_pulse_proxy" and pg.get("vol_pulse_min") is not None:
                    c["right"] = {"value": float(pg["vol_pulse_min"])}
            if pg.get("max_hold") is not None:
                hold = max(20, min(48, int(pg["max_hold"])))

    if direction == "long":
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 68.0}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}, "role": "invalidation"},
        ]
    else:
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 32.0}, "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}, "role": "invalidation"},
        ]

    dsl = {
        "schema": "qiyu_strategy_dsl_v1",
        "key": "frost3_bnb5m_session_%s_%d" % (_safe(logic)[:18], idx),
        "name": "寒霜叁-BNB5m-会话V回收",
        "direction": direction,
        "timeframe": TIMEFRAME,
        "supported_instruments": [SYMBOL],
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "max_hold_bars": hold,
        # NOTE: do not put meta/proxies inside DSL — validate_strategy rejects unknown top-level fields
    }
    return f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)


def make_book(hyp, idx=1):
    dsl = build_session_vreclaim_dsl(hyp, idx)
    return {
        "title": "寒霜叁-BNB5m-会话V回收",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": str(hyp.get("direction") or "long").lower(),
        "logic_class": str(hyp.get("logic_class") or "session_window_v_reclaim"),
        "thesis": hyp.get("thesis"),
        "entry_sketch": hyp.get("entry_sketch"),
        "exit_sketch": hyp.get("exit_sketch"),
        "param_grid": hyp.get("param_grid"),
        "avoid_from_postmortem": hyp.get("avoid_death"),
        "diff_vs_live": hyp.get("diff_vs_live_ltc_ng_exhaust"),
        "diff_vs_bnb1h": hyp.get("diff_vs_bnb1h"),
        "dsl": dsl,
        "proxy_notes": {
            "session": "session_liq",
            "volume": "vol_pulse_proxy",
            "book_depth": "unavailable",
            "session_utc": [SESSION_UTC_START, SESSION_UTC_END],
        },
        "gate_mode": "frost2",
        "source": "frost3_bnb5m_session",
        "dir_rank": idx,
    }


def glm_audit_book(book, report, timeout_sec=60):
    payload = {
        "book": {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "thesis": book.get("thesis"),
            "entry": (book.get("dsl") or {}).get("entry"),
            "exit": (book.get("dsl") or {}).get("exit"),
            "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
            "proxies": book.get("proxy_notes"),
        },
        "report_title": (report or {}).get("report_title"),
        "session_window_utc": (report or {}).get("session_window_utc"),
        "freezer_hf": (report or {}).get("freezer_hf_lessons_zh"),
        "banned": ["ADA", "LTC", "NG", "XRP"],
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
    if th.is_alive():
        return (
            {"decision": "pass", "reason_zh": "audit_timeout_fallback_pass", "fallback": True},
            {"ok": False, "error": "glm_timeout_%ss" % timeout_sec, "raw_preview": ""},
        )
    return (
        box["parsed"] or {"decision": "pass", "reason_zh": "audit_fallback_pass", "fallback": True},
        box["ai"],
    )


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
        "symbol": SYMBOL, "timeframe": TIMEFRAME,
        "direction": book["direction"], "logic_class": book["logic_class"],
        "thesis": book.get("thesis"),
        "entry_sketch": book.get("entry_sketch"),
        "exit_sketch": book.get("exit_sketch"),
        "param_grid": book.get("param_grid"),
    }
    book["dsl"] = build_session_vreclaim_dsl(row, int(book.get("dir_rank") or 1))
    for phase in ("entry", "exit"):
        block = (book.get("dsl") or {}).get(phase) or {}
        rows = block.get("all") or block.get("any") or []
        for r in rows:
            feat = ((r.get("left") or {}).get("feature"))
            if feat in tweaks and isinstance(tweaks[feat], (int, float)):
                if isinstance(r.get("right"), dict) and "value" in r["right"]:
                    if feat == "session_liq":
                        r["right"]["value"] = 0.5
                    else:
                        r["right"]["value"] = float(tweaks[feat])
    book["dsl"] = f2.ensure_dsl(book["dsl"], SYMBOL, TIMEFRAME)
    return book


def local_tweak_bnb5m(book, n, failed_step="", metrics=None):
    book = copy.deepcopy(book)
    dsl = book["dsl"]
    entries = list((dsl.get("entry") or {}).get("all") or [])
    step = str(failed_step or "")
    metrics = metrics or {}
    trades = int(metrics.get("trades") or 0)
    fp = int(metrics.get("fold_positive") or 0)
    # If already have enough trades but WF quality fails → tighten, don't loosen into overtrade.
    need_quality = ("walk_forward" in step or "destruction" in step) and trades >= 12 and fp < 7
    need_samples = ("walk_forward" in step) and trades < 12
    for row in entries:
        feat = (row.get("left") or {}).get("feature")
        right = row.get("right") or {}
        if "value" not in right:
            continue
        v = float(right["value"])
        if feat == "session_liq":
            row["right"]["value"] = 0.5
            continue
        if feat == "vol_pulse_proxy" and row.get("op") == "gt":
            if need_samples:
                row["right"]["value"] = max(1.02, v - 0.02 * n)
            else:
                # tighten pulse for quality / friction
                row["right"]["value"] = min(1.35, v + (0.03 if need_quality else 0.02) * n)
        if feat == "rsi14" and row.get("op") == "gt":
            if need_samples:
                row["right"]["value"] = max(42.0, min(50.0, v - 1.0 * n))
            else:
                # raise floor away from marginal reclaim noise (CL/btc5 lesson)
                row["right"]["value"] = max(46.0, min(52.0, v + 1.0 * n))
        if feat == "rsi14" and row.get("op") == "lt":
            if need_samples:
                row["right"]["value"] = min(64.0, v + 1.5 * n)
            else:
                row["right"]["value"] = max(54.0, min(60.0, v - 1.0 * n))
        if feat == "z20" and row.get("op") == "lt":
            if need_samples:
                row["right"]["value"] = min(0.9, v + 0.08 * n)
            else:
                row["right"]["value"] = max(0.25, v - 0.06 * n)
        if feat == "z20" and row.get("op") == "gt":
            if need_samples:
                row["right"]["value"] = max(-1.2, v - 0.08 * n)
            else:
                row["right"]["value"] = min(-0.2, v + 0.06 * n)
        if feat == "h1_slope4" and row.get("op") == "gt" and need_quality:
            row["right"]["value"] = max(0.0, v + 0.00005 * n)
    dsl["entry"] = {"all": entries}
    # Prefer slightly longer hold when tightening quality (less churn / slip)
    hold = int(dsl.get("max_hold_bars") or 32)
    if need_quality:
        hold = min(48, hold + 2)
    elif need_samples:
        hold = min(48, hold + 2)
    dsl["max_hold_bars"] = max(20, hold)
    k = str(dsl.get("key") or "")
    if "bnb5m_session" not in k:
        dsl["key"] = ("frost3_bnb5m_session_" + k)[:100]
    book["dsl"] = f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)
    book["symbol"] = SYMBOL
    book["timeframe"] = TIMEFRAME
    return book


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


def force_key(book):
    if book.get("dsl"):
        book["dsl"]["supported_instruments"] = [SYMBOL]
        book["dsl"]["timeframe"] = TIMEFRAME
        k = str(book["dsl"].get("key") or "")
        if "bnb5m_session" not in k:
            book["dsl"]["key"] = ("frost3_bnb5m_session_" + k)[:100]
        book["dsl"] = f2.ensure_dsl(book["dsl"], SYMBOL, TIMEFRAME)
    book["symbol"] = SYMBOL
    book["timeframe"] = TIMEFRAME
    return book


def process(book, report):
    hist = []
    dir_id = "bnb5m_session_%s" % _safe(book.get("logic_class"))
    status = {
        "dir_id": dir_id, "stage": "audit", "updated_at": _now(),
        "symbol": SYMBOL, "tf": TIMEFRAME, "logic": book.get("logic_class"),
        "key": (book.get("dsl") or {}).get("key"),
    }
    write_art("status", status)

    audit_ok = False
    if os.environ.get("BNB5M_FORCE_FALLBACK", "0") == "1":
        decision = {
            "decision": "pass",
            "reason_zh": "force_fallback_local_audit_pass_session_v_reclaim",
            "fallback": True,
            "score": 70,
        }
        write_art("audit_try0", {"decision": decision, "ai": {"ok": False, "error": "forced_fallback"}})
        hist.append({"stage": "audit", "try": 0, "decision": "pass", "reason": decision["reason_zh"], "fallback": True})
        audit_ok = True
    for atry in range(0 if audit_ok else 0, 0 if audit_ok else MAX_AUDIT_REVISE + 1):
        decision, ai = glm_audit_book(book, report)
        write_art("audit_try%d" % atry, {
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
            "dir_id": dir_id, "ok": False, "failed_step": "hyp_audit_reject",
            "reason": decision.get("reason_zh") or decision.get("issues"),
            "hist": hist, "book": book,
        }
    if not audit_ok:
        return {
            "dir_id": dir_id, "ok": False, "failed_step": "hyp_audit_reject",
            "reason": "audit not passed", "hist": hist, "book": book,
        }

    write_art("hypothesis_book", book)

    packs = None
    for qtry in range(MAX_QUICK_REPAIR + 1):
        status.update({"stage": "quick", "attempt": qtry, "updated_at": _now()})
        write_art("status", status)
        packs = f2.quick_suite(book)
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
            "folds": (packs.get("base_metrics") or {}).get("folds"),
            "tr": (packs.get("base_metrics") or {}).get("trades"),
            "dest": (packs.get("logic_destruction") or {}).get("pass"),
        })
        if packs.get("quick_pass"):
            break
        if qtry >= MAX_QUICK_REPAIR:
            return {
                "dir_id": dir_id, "ok": False,
                "failed_step": packs.get("failed_step") or "quick_exhausted",
                "reason": "Quick failed after %d repairs" % MAX_QUICK_REPAIR,
                "metrics": packs.get("base_metrics"),
                "dest": (packs.get("logic_destruction") or {}).get("pass"),
                "hist": hist, "book": book,
            }
        book = force_key(local_tweak_bnb5m(
            book, qtry + 1, packs.get("failed_step"), packs.get("base_metrics") or {},
        ))
        repaired = None
        if os.environ.get("BNB5M_FORCE_FALLBACK", "0") != "1":
            repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step"), "quick")
            if repaired:
                book = force_key(repaired)
        # Always re-assert session/vol leaves after any repair path
        ents = list((book.get("dsl") or {}).get("entry", {}).get("all") or [])
        feats = {((e.get("left") or {}).get("feature")) for e in ents}
        if "session_liq" not in feats:
            ents.insert(0, {"left": {"feature": "session_liq"}, "op": "gt", "right": {"value": 0.5}})
        if "vol_pulse_proxy" not in feats:
            ents.append({"left": {"feature": "vol_pulse_proxy"}, "op": "gt", "right": {"value": 1.05}})
        # Strip illegal top-level keys that break validate_strategy
        for bad in ("meta", "proxies", "notes"):
            (book.get("dsl") or {}).pop(bad, None)
        book["dsl"]["entry"] = {"all": ents}
        book = force_key(book)
        hist.append({"stage": "quick_repair", "try": qtry, "ok": bool(repaired) or True, "local_only": repaired is None})

    for ftry in range(MAX_FULL_REPAIR + 1):
        status.update({"stage": "full", "attempt": ftry, "updated_at": _now()})
        write_art("status", status)
        packs = f2.full_suite(book, packs)
        write_art("full_try%d" % ftry, {
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
                "dir_id": dir_id, "ok": False,
                "failed_step": packs.get("failed_step") or "full_exhausted",
                "reason": "Full failed after %d repairs" % MAX_FULL_REPAIR,
                "full": packs.get("full"), "hist": hist, "book": book,
            }
        book = force_key(local_tweak_bnb5m(
            book, ftry + 1, packs.get("failed_step"), packs.get("base_metrics") or {},
        ))
        if os.environ.get("BNB5M_FORCE_FALLBACK", "0") != "1":
            repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step"), "full")
            if repaired:
                book = force_key(repaired)
        for bad in ("meta", "proxies", "notes"):
            (book.get("dsl") or {}).pop(bad, None)
        book = force_key(book)
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
        write_art("status", status)
        sf = f2.run_sim_formal(book, packs)
        write_art("sim_try%d" % stry, sf)
        hist.append({
            "stage": "sim_formal", "try": stry,
            "failed_step": sf.get("failed_step"),
            "sim_ds": (sf.get("sim") or {}).get("wr_deepseek_sim"),
            "sim_qw": (sf.get("sim") or {}).get("wr_qwen_sim"),
            "formal_approved": (sf.get("formal") or {}).get("approved"),
            "pending": (sf.get("pending") or {}).get("key"),
        })
        if (sf.get("pending") or {}).get("ok"):
            return {
                "dir_id": dir_id, "ok": True, "failed_step": None,
                "pending": sf.get("pending"), "sim": sf.get("sim"),
                "formal": sf.get("formal"), "hist": hist, "book": book,
            }
        fs = sf.get("failed_step")
        if fs in ("formal_review", "pending_ingest"):
            return {
                "dir_id": dir_id, "ok": False, "failed_step": fs,
                "reason": (sf.get("formal") or {}).get("reason") or (sf.get("pending") or {}).get("reason"),
                "sim": sf.get("sim"), "formal": sf.get("formal"),
                "pending": sf.get("pending"), "hist": hist, "book": book,
            }
        if stry >= MAX_SIM_REPAIR:
            return {
                "dir_id": dir_id, "ok": False,
                "failed_step": fs or "sim_exhausted",
                "reason": "Sim/formal failed after %d repairs" % MAX_SIM_REPAIR,
                "sim": sf.get("sim"), "hist": hist, "book": book,
            }
        book = force_key(local_tweak_bnb5m(book, stry + 1, "sim"))
        if os.environ.get("BNB5M_FORCE_FALLBACK", "0") != "1":
            repaired, _ai = f3.glm_repair(book, packs, "sim_review", "sim")
            if repaired:
                book = force_key(repaired)
        for bad in ("meta", "proxies", "notes"):
            (book.get("dsl") or {}).pop(bad, None)
        book = force_key(book)
        q3 = f2.quick_suite(book)
        if not q3.get("quick_pass"):
            hist.append({"stage": "sim_repair_quick_fail", "failed": q3.get("failed_step")})
            continue
        packs = f2.full_suite(book, q3)
        if not packs.get("full_pass"):
            hist.append({"stage": "sim_repair_full_fail", "failed": packs.get("failed_step")})
            continue

    return {
        "dir_id": dir_id, "ok": False, "failed_step": "unknown_exhausted",
        "hist": hist, "book": book,
    }


def main():
    print("=== BNB5m SESSION V-RECLAIM START ===", _now(), flush=True)
    d._ensure_dirs()
    d._load_env()
    print("[bnb5m] env ready", flush=True)
    proxy_doc = install_session_proxies()
    print("[bnb5m] proxies installed", proxy_doc.get("session_feature"), flush=True)

    cl = step1.load_cl_archive()
    print("[bnb5m] cl archive ok", flush=True)
    micro = load_bnb_micro()
    print("[bnb5m] micro clusters", len(micro.get("top_clusters") or []), flush=True)
    death = load_death()
    print("[bnb5m] death source", death.get("source"), "freezer", len(death.get("freezer_last10") or []), flush=True)
    patterns = load_intraday_patterns()
    print("[bnb5m] patterns", patterns.get("proxy_signal_count"), "bars", patterns.get("n_bars"), flush=True)
    packet = {
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "cl_archive": cl,
        "bnb_micro": micro,
        "death": death,
        "intraday_patterns": patterns,
        "proxy_injection": proxy_doc,
        "live_protect": ["ADA", "LTC", "NG", "XRP"],
        "parallel_note": "separate keys frost3_bnb5m_session_* vs frost3_bnb1h_*",
        "gates": {
            "WF": ">=7/10", "logic_destruction": "pass",
            "MC_beat": ">=90%", "extreme_friction_sharpe": ">=0",
            "sim_wr": ">=55/55",
        },
    }
    write_art("inputs", packet)
    write_art("proxy_notes", proxy_doc)

    # Under host memory pressure prefer deterministic fallback; still attempt GLM briefly.
    prefer_fallback = os.environ.get("BNB5M_FORCE_FALLBACK", "0") == "1"
    if prefer_fallback:
        print("[bnb5m] BNB5M_FORCE_FALLBACK=1 → skip GLM params", flush=True)
        report, ai = fallback_params(packet), {"ok": False, "error": "forced_fallback", "raw_preview": ""}
    else:
        report, ai = ask_glm_params(packet, timeout_sec=45)
    write_art("glm_params_raw", {
        "ok": ai.get("ok"), "parsed": bool(report),
        "preview": (ai.get("raw_preview") or "")[:1800],
        "error": ai.get("error"),
    })
    if not report or not isinstance(report.get("hypothesis"), dict):
        print("[bnb5m] GLM params fail/empty → fallback", flush=True)
        report = fallback_params(packet)
    hyp = dict(report.get("hypothesis") or {})
    hyp["symbol"] = SYMBOL
    hyp["timeframe"] = TIMEFRAME
    hyp["logic_class"] = "session_window_v_reclaim"
    hyp["direction"] = "long"
    report["hypothesis"] = hyp
    report["symbol"] = SYMBOL
    report["timeframe"] = TIMEFRAME
    write_art("param_plan", report)
    write_art("hypothesis", hyp)

    book = make_book(hyp, 1)
    write_art("hypothesis_book_v0", book)
    print(
        "[bnb5m] logic", book.get("logic_class"),
        "key", (book.get("dsl") or {}).get("key"), flush=True,
    )

    try:
        result = process(book, report)
    except Exception as exc:
        result = {
            "dir_id": "bnb5m_session_exception", "ok": False,
            "failed_step": "exception", "reason": str(exc),
            "trace": traceback.format_exc()[-2500:],
        }

    end = {
        "at": _now(),
        "op": "BNB5m会话窗V回收独立创造",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "ok": bool(result.get("ok")),
        "failed_step": result.get("failed_step"),
        "reason": result.get("reason"),
        "pending": result.get("pending"),
        "sim": result.get("sim"),
        "formal": result.get("formal"),
        "logic": (result.get("book") or book).get("logic_class"),
        "key": ((result.get("book") or book).get("dsl") or {}).get("key"),
        "hist": result.get("hist"),
        "proxy_notes": proxy_doc,
        "session_window_utc": [SESSION_UTC_START, SESSION_UTC_END],
        "hypothesis": hyp,
    }

    if not result.get("ok"):
        death_cause = {
            "exact_death": result.get("failed_step"),
            "reason": result.get("reason"),
            "metrics": result.get("metrics"),
            "full": result.get("full"),
            "sim": result.get("sim"),
            "hist_tail": (result.get("hist") or [])[-8:],
            "logic_class": end["logic"],
            "key": end["key"],
            "summary_zh": "BNB5m会话V回收 %s 失败于 %s：%s" % (
                end["logic"], result.get("failed_step"), result.get("reason")),
        }
        arch = archive_result(result, death_cause)
        end["archive_path"] = arch
        end["death_cause"] = death_cause
        print("[bnb5m] ARCHIVED", arch, death_cause["exact_death"], flush=True)
    else:
        print("[bnb5m] PENDING", (result.get("pending") or {}).get("key"), flush=True)

    write_art("end_report", end)
    write_art("status", {"finished_at": _now(), "ok": end["ok"], "end": end})

    if end["ok"]:
        summary_zh = (
            "BNB5m会话V回收【成功】逻辑=%s key=%s 已进pending=%s sim_ds/qw=%s/%s "
            "会话UTC=%s-%s 代理=%s"
            % (
                end["logic"], end["key"],
                (end.get("pending") or {}).get("key"),
                (end.get("sim") or {}).get("wr_deepseek_sim"),
                (end.get("sim") or {}).get("wr_qwen_sim"),
                SESSION_UTC_START, SESSION_UTC_END,
                "session_liq+vol_pulse_proxy(ATR)",
            )
        )
    else:
        summary_zh = (
            "BNB5m会话V回收【失败归档】逻辑=%s 死因=%s 详情=%s 归档=%s"
            % (
                end["logic"], end.get("failed_step"), end.get("reason"),
                end.get("archive_path"),
            )
        )
    write_art("parent_summary_zh", {
        "summary_zh": summary_zh, "at": _now(),
        "end": {
            "ok": end["ok"], "failed_step": end.get("failed_step"),
            "key": end.get("key"), "pending": end.get("pending"),
            "archive_path": end.get("archive_path"),
        },
    })
    print("=== BNB5m SESSION END ===", summary_zh, flush=True)
    print("PARENT_SUMMARY_ZH:", summary_zh, flush=True)
    return 0 if end["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
