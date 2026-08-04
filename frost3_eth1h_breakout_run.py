#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone ETH-USDT-SWAP 1h trend-breakout strategy creation.

Artifacts ONLY under frost3_eth1h_breakout_* — parallel-safe vs ETH 5m / BNB / others.
Logic family (fixed): EMA trend filter + vol-contraction proxy → directional breakout
+ volume/participation proxy. Customize params for ETH 1h; do not invent a new family.
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

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
f2.AVOID = [
    ("ADA-USDT-SWAP", None),
    ("LTC-USDT-SWAP", None),
    ("NG-USDT-SWAP", None),
    ("XRP-USDT-SWAP", None),
]

OUT = "/root/auto_trade/dual_engine"
PREFIX = "frost3_eth1h_breakout"
SYMBOL = "ETH-USDT-SWAP"
TIMEFRAME = "1h"
LOGIC_FAMILY = "trend_breakout"
MAX_AUDIT_REVISE = 2
MAX_QUICK_REPAIR = 3
MAX_FULL_REPAIR = 3
MAX_SIM_REPAIR = 3

# Live ADA/LTC/NG/XRP occupy fade/reclaim — we stay trend_breakout only.
# Do NOT mutate into these families during repair.
BANNED_LOGIC_SUBSTR = (
    "exhaust", "fade", "reclaim", "range_reclaim", "impulse_continuation",
    "trend_pullback_fade", "衰竭", "回收", "回撤做空",
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


def load_eth_micro():
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
                "SELECT label, description FROM microstructure_primitive_clusters WHERE cluster_id=?",
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


def load_breakout_freezer():
    """Breakout-class freezer / failure lessons (negative examples)."""
    lessons = []
    try:
        import auto_trade_strategy_creation_factory as cf
        for x in cf.freezer_lessons(limit=80):
            if not isinstance(x, dict):
                continue
            blob = json.dumps(x, ensure_ascii=False).lower()
            key = str(x.get("key") or "")
            if any(t in blob for t in (
                "breakout", "break_out", "up_break", "down_break", "breakdown",
                "突破", "假突", "conventional_up_break", "xau15_h1_breakout",
            )):
                lessons.append({
                    "key": key,
                    "reason": str(x.get("reason") or "")[:280],
                    "grade": x.get("grade"),
                    "source": x.get("source"),
                })
    except Exception as exc:
        lessons.append({"error": str(exc)})
    # curated death causes (review-stable)
    curated = [
        {
            "key": "conventional_up_break_long",
            "death": "ai_logic_rejection_low_wr",
            "avoid_zh": "复杂KD/CCI路径突破易被正式复核判低胜率/高风险；ETH1h用更短可解释链路",
        },
        {
            "key": "xau15_h1_breakout_long_ai",
            "death": "ai_reject_risk_high_despite_mid_wr",
            "avoid_zh": "有趋势+破高但缺收缩过滤/失效边界时，复核仍可因风险拒；必须带收缩代理+结构失效",
        },
        {
            "key": "btc15_h1_downtrend_z20_breakdown_short",
            "death": "grade_D_mass_purge",
            "avoid_zh": "纯z20破位无EMA趋势对齐易沦为噪声空；保留ema19/53趋势过滤",
        },
        {
            "code": "stop_cluster",
            "avoid_zh": "假突破追高触发硬止损簇→friction崩；用z20上界+ema失效早退，勿无边界硬扛",
        },
        {
            "code": "sample_starvation",
            "avoid_zh": "突破过滤过窄→trades<10→WF folds不足（CL同构陷阱）；cci阈值保持偏软",
        },
        {
            "code": "cost_collapse",
            "avoid_zh": "高频假突破吃摩擦；1h+prev_high20降低刷单，max_hold适中",
        },
    ]
    return {
        "freezer_breakout_hits": lessons[:12],
        "curated_death_avoids": curated,
        "volume_feature_note_zh": (
            "DSL无volume/vol_ratio/body_atr；成交量确认用代理："
            "cci动量扩张 + macd_stick同向 + z20中等扩张带。"
            "波动收缩用 z20 上界抑制已极端延展（收缩后释放窗口），"
            "非BB squeeze序列（单bar DSL不可表达先验收缩序列）。"
        ),
    }


def load_death():
    try:
        inputs = d.collect_inputs()
    except Exception as exc:
        return {"error": str(exc), "death_heatmap_top10": [], "freezer_last10": []}
    return {
        "death_heatmap_top10": (inputs.get("death_heatmap_top10") or [])[:10],
        "freezer_last10": (inputs.get("freezer_last10") or [])[:10],
        "mandatory_eth": [
            n for n in (inputs.get("mandatory_niches") or [])
            if isinstance(n, dict) and "ETH" in str(n.get("symbol_hint") or "")
        ],
        "coverage_eth": [
            c for c in (inputs.get("coverage_map") or [])
            if isinstance(c, dict) and "ETH" in str(c.get("symbol") or "")
        ],
    }


def load_eth1h_vol_snapshot():
    """Light ETH 1h micro/vol snapshot for GLM param customization."""
    snap = {"symbol": SYMBOL, "timeframe": TIMEFRAME, "ok": False}
    try:
        df = d._frame(SYMBOL, TIMEFRAME)
        atr_pct = df["atr14"] / df["close"]
        snap.update({
            "ok": True,
            "bars": int(len(df)),
            "atr_pct_q30": float(atr_pct.quantile(0.3)),
            "atr_pct_q50": float(atr_pct.quantile(0.5)),
            "atr_pct_q70": float(atr_pct.quantile(0.7)),
            "z20_q20": float(df["z20"].quantile(0.2)),
            "z20_q50": float(df["z20"].quantile(0.5)),
            "z20_q80": float(df["z20"].quantile(0.8)),
            "rsi_q50": float(df["rsi14"].quantile(0.5)),
            "cci_q80": float(df["cci"].quantile(0.8)),
            "break_high20_rate": float((df["close"] > df["prev_high20"]).mean()),
            "trend_up_rate": float((df["ema19"] > df["ema53"]).mean()),
            "note_zh": (
                "1h帧无h1_ema19/53（用原生ema19/53）；无volume列；"
                "atr_pct中位约%.3f%%；破prev_high20稀有≈%.2f%%"
                % (100.0 * float(atr_pct.quantile(0.5)),
                   100.0 * float((df["close"] > df["prev_high20"]).mean()))
            ),
        })
    except Exception as exc:
        snap["error"] = str(exc)
    return snap


PARAM_PROMPT = """你是GLM-5.2。只输出纯JSON，禁止Markdown。
任务：输出《ETH 1h 趋势突破参数方案》。

硬约束：
1) symbol固定 ETH-USDT-SWAP，timeframe固定 1h，logic_family固定 trend_breakout
2) 逻辑骨架不可改：EMA趋势过滤 + 波动收缩代理 + 方向突破 + 量能/参与度代理
3) 禁止改成 exhaustion_fade / range_reclaim / impulse_continuation 等其他家族
4) 阅读 cl_archive：勿边际rsi≈54；勿过窄过滤致trades<10；需结构失效出场；friction崩主因硬止损簇
5) 阅读 breakout_freezer：避开 conventional_up_break 复杂路径、无收缩追破、无结构失效
6) DSL无volume：量能用 cci+macd_stick+z20 代理（见 volume_feature_note）
7) 1h特征用 ema19/ema53（不要用缺失的h1_ema*）、prev_high20/prev_low20、h1_slope4、rsi14、z20、cci、macd_stick、atr14
8) 至少给出3个同家族参数变体（long为主，可含1个short镜像）；推荐1个；目标trades≥12

JSON schema：
{
  "report_title":"ETH 1h 趋势突破参数方案",
  "symbol":"ETH-USDT-SWAP",
  "timeframe":"1h",
  "logic_family":"trend_breakout",
  "cl_lessons_zh":"...",
  "freezer_breakout_lessons_zh":"...",
  "micro_vol_read_zh":"...",
  "volume_proxy_note_zh":"...",
  "hypothesis":{
    "ema_period":"ema19>ema53",
    "vol_contraction_def":"z20上界抑制极端延展=收缩后释放窗口代理",
    "volume_threshold":"cci与macd代理阈值及理由",
    "rationale_zh":"..."
  },
  "variants":[
    {
      "rank":1,
      "direction":"long",
      "logic_class":"trend_breakout",
      "ema_period":"19/53",
      "vol_contraction":"z20<1.4且z20>0.2",
      "volume_proxy":"cci>70; macd_stick>0",
      "entry_sketch":"分号分隔条件",
      "exit_sketch":"止盈与结构失效; max_hold=N",
      "why_avoids_freezer":"...",
      "why_avoids_cl_traps":"...",
      "diff_vs_live_ada_ltc_ng_xrp":"趋势突破而非衰竭/回收",
      "avoid_death":["stop_cluster","sample_starvation"],
      "expected_trades_hint":">=12"
    }
  ],
  "recommended_rank":1,
  "mentor_notes":"..."
}
"""


def ask_glm_params(packet):
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
    return parsed, ai


def fallback_param_plan(packet):
    """Deterministic ETH 1h trend_breakout param variants (review-stable family)."""
    vol = packet.get("eth1h_vol") or {}
    return {
        "report_title": "ETH 1h 趋势突破参数方案",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_family": LOGIC_FAMILY,
        "cl_lessons_zh": (
            "CL15m：边际超买+中等cci→硬止损≈-22%拖垮friction；"
            "再抬cci阈值拦亏损把trades压到<10→WF不足；软化引入新硬止损且dest失败。"
        ),
        "freezer_breakout_lessons_zh": (
            "conventional_up_break/xau15_h1_breakout：复杂或无收缩追破易被AI拒；"
            "breakdown纯z20噪声；须EMA趋势+收缩代理+结构失效，保持可解释短链路。"
        ),
        "micro_vol_read_zh": (
            "ETH微观多样本含 flow/spread/aggression 波动；1h atr_pct中位≈%.3f%%；"
            "破prev_high20稀有，过滤宜偏软保样本；量能不可直取，用cci+macd+z20代理。"
            % (100.0 * float(vol.get("atr_pct_q50") or 0.009),)
        ),
        "volume_proxy_note_zh": (packet.get("breakout_freezer") or {}).get(
            "volume_feature_note_zh"),
        "hypothesis": {
            "ema_period": "ema19 > ema53（沿用成熟突破框架19/53，非发明新周期族）",
            "vol_contraction_def": (
                "单bar无法表达先验BB收缩序列；用 z20∈(0.2,1.4] 作为"
                "『收缩后方向释放窗口』：有扩张确认但未极端延展（避免追炸）"
            ),
            "volume_threshold": (
                "cci>70 + macd_stick>0 作为参与度/量能代理；阈值偏软避免CL式样本饥荒"
            ),
            "rationale_zh": (
                "ETH1h波动高于山寨刷单盘，破高稀疏；以趋势对齐+中等z扩张+软cci确认，"
                "出场用ema21结构失效，降低假突破硬止损簇。"
            ),
        },
        "variants": [
            {
                "rank": 1,
                "direction": "long",
                "logic_class": "trend_breakout",
                "ema_period": "19/53",
                "vol_contraction": "z20>0.2; z20<1.4",
                "volume_proxy": "cci>70; macd_stick>0",
                "thesis": "ETH1h多头趋势下，波动自收缩窗口向上突破prev_high20，量能代理确认",
                "entry_sketch": (
                    "ema19>ema53; h1_slope4>0; close>prev_high20; close>ema21; "
                    "rsi14>55; rsi14<72; macd_stick>0; z20>0.2; z20<1.4; cci>70"
                ),
                "exit_sketch": "rsi14>75 take_profit; close<ema21 invalidation; max_hold=14",
                "why_avoids_freezer": "短链路非conventional复杂KD；带z上界防追延展；有结构失效",
                "why_avoids_cl_traps": "rsi带宽远离54；cci软阈值；目标≥12笔；结构失效非硬扛",
                "diff_vs_live_ada_ltc_ng_xrp": "趋势突破，非ADA/LTC/NG/XRP衰竭fade或回收reclaim",
                "avoid_death": ["stop_cluster", "sample_starvation", "ai_logic_rejection"],
                "expected_trades_hint": ">=12",
            },
            {
                "rank": 2,
                "direction": "long",
                "logic_class": "trend_breakout",
                "ema_period": "19/53",
                "vol_contraction": "z20>0.15; z20<1.6",
                "volume_proxy": "cci>55; macd_stick>0",
                "thesis": "同家族放宽量能/z带以提高折数，仍要求破高+趋势",
                "entry_sketch": (
                    "ema19>ema53; h1_slope4>0; close>prev_high20; close>ema21; "
                    "rsi14>52; rsi14<74; macd_stick>0; z20>0.15; z20<1.6; cci>55"
                ),
                "exit_sketch": "rsi14>73 take_profit; close<ema21 invalidation; max_hold=16",
                "why_avoids_freezer": "仍非无过滤追破；放宽为样本而非取消趋势",
                "why_avoids_cl_traps": "优先保证trades/folds；失效仍用ema21",
                "diff_vs_live_ada_ltc_ng_xrp": "突破多头，非衰竭空/回收多",
                "avoid_death": ["sample_starvation", "holdout_collapse"],
                "expected_trades_hint": ">=12",
            },
            {
                "rank": 3,
                "direction": "short",
                "logic_class": "trend_breakout",
                "ema_period": "19/53",
                "vol_contraction": "z20<-0.2; z20>-1.4",
                "volume_proxy": "cci<-70; macd_stick<0",
                "thesis": "空头趋势下破prev_low20的镜像趋势突破",
                "entry_sketch": (
                    "ema19<ema53; h1_slope4<0; close<prev_low20; close<ema21; "
                    "rsi14<45; rsi14>28; macd_stick<0; z20<-0.2; z20>-1.4; cci<-70"
                ),
                "exit_sketch": "rsi14<25 take_profit; close>ema21 invalidation; max_hold=14",
                "why_avoids_freezer": "趋势对齐的breakdown，非纯z噪声空",
                "why_avoids_cl_traps": "非边际超买衰竭；结构失效清晰",
                "diff_vs_live_ada_ltc_ng_xrp": "顺势破位突破空，非exhaustion_fade",
                "avoid_death": ["stop_cluster", "negative_net_expectancy"],
                "expected_trades_hint": ">=12",
            },
        ],
        "recommended_rank": 1,
        "mentor_notes": "家族锁死trend_breakout；仅调参。并行不影响ETH5m。",
        "fallback": True,
        "provider": "codex_fallback",
    }


def logic_banned(logic):
    low = str(logic or "").lower()
    for s in BANNED_LOGIC_SUBSTR:
        if s.lower() in low:
            return True
    # must remain breakout-family
    return False


def sanitize_variants(report):
    dirs = []
    for row in (report.get("variants") or report.get("directions") or []):
        if not isinstance(row, dict):
            continue
        row = dict(row)
        row["symbol"] = SYMBOL
        row["timeframe"] = TIMEFRAME
        row["logic_class"] = LOGIC_FAMILY
        if logic_banned(row.get("thesis")) or logic_banned(row.get("entry_sketch")):
            # allow word "突破" in thesis; only ban fade/reclaim families
            pass
        if any(x in str(row.get("logic_class") or "").lower() for x in (
            "exhaust", "reclaim", "fade",
        )):
            continue
        dirs.append(row)
    return dirs


def parse_sketch_conditions(sketch):
    conds = []
    text = str(sketch or "")
    # Normalize Chinese joiners so "z20>0.2且z20<1.4" splits cleanly
    text = text.replace("并且", ";").replace("且", ";")
    for part in re.split(r"[;；\n]+", text):
        part = part.strip()
        if not part:
            continue
        part2 = re.sub(
            r"\b(take_profit|invalidation|max_hold\s*=\s*\d+)\b", "", part, flags=re.I
        ).strip()
        m = re.match(
            r"^(h1_slope4|h1_ema19|h1_ema53|rsi14|z20|macd_stick|cci|atr14|"
            r"ema\d+|close|prev_high\d+|prev_low\d+)\s*"
            r"(>=|<=|>|<)\s*"
            r"(-?\d+(?:\.\d+)?|h1_slope4|h1_ema19|h1_ema53|ema\d+|prev_high\d+|"
            r"prev_low\d+|close)$",
            part2.replace(" ", ""),
        )
        if not m:
            continue
        left, op, right = m.group(1), m.group(2), m.group(3)
        # 1h frame: remap missing h1_ema* → native ema*
        if left == "h1_ema19":
            left = "ema19"
        if left == "h1_ema53":
            left = "ema53"
        op_map = {">": "gt", "<": "lt", ">=": "gte", "<=": "lte"}
        leaf = {"left": {"feature": left}, "op": op_map.get(op, "gt")}
        try:
            leaf["right"] = {"value": float(right)}
        except Exception:
            rfeat = right
            if rfeat == "h1_ema19":
                rfeat = "ema19"
            if rfeat == "h1_ema53":
                rfeat = "ema53"
            leaf["right"] = {"feature": rfeat}
        conds.append(leaf)
    return conds


def build_eth1h_breakout_dsl(row, idx):
    direction = str(row.get("direction") or "long").lower()
    if direction not in ("long", "short"):
        direction = "long"
    entry = parse_sketch_conditions(row.get("entry_sketch"))
    hold = 14
    mhold = re.search(r"max_hold\s*=\s*(\d+)", str(row.get("exit_sketch") or ""), re.I)
    if mhold:
        hold = max(10, min(24, int(mhold.group(1))))

    if len(entry) < 5:
        if direction == "short":
            entry = [
                {"left": {"feature": "ema19"}, "op": "lt", "right": {"feature": "ema53"}},
                {"left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": 0.0}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 28.0}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": -0.2}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -1.4}},
                {"left": {"feature": "cci"}, "op": "lt", "right": {"value": -70.0}},
            ]
        else:
            entry = [
                {"left": {"feature": "ema19"}, "op": "gt", "right": {"feature": "ema53"}},
                {"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 72.0}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.2}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.4}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 70.0}},
            ]
            direction = "long"

    if direction == "long":
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 75.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"},
             "role": "invalidation"},
        ]
    else:
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 25.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"},
             "role": "invalidation"},
        ]
    sketch_exit = parse_sketch_conditions(row.get("exit_sketch"))
    for leaf in sketch_exit:
        feat = (leaf.get("left") or {}).get("feature")
        if feat == "rsi14":
            leaf = dict(leaf)
            leaf["role"] = "take_profit"
            exit_any[0] = leaf

    # Ensure vol-contraction z20 band present (core of family); GLM sketches may omit
    feats = set((c.get("left") or {}).get("feature") for c in entry)
    if direction == "long" and "z20" not in feats:
        entry.extend([
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.2}},
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.4}},
        ])
    if direction == "short" and "z20" not in feats:
        entry.extend([
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": -0.2}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -1.4}},
        ])
    # Drop tautology atr14>0 (not informative; can confuse audits)
    entry = [
        c for c in entry
        if not (
            ((c.get("left") or {}).get("feature") == "atr14")
            and (c.get("op") in ("gt", "gte"))
            and isinstance(c.get("right"), dict)
            and float((c.get("right") or {}).get("value") or -1) == 0.0
        )
    ]

    dsl = {
        "key": "frost3_eth1h_breakout_%s_%d" % (_safe(direction)[:8], idx),
        "name": "寒霜叁-ETH-1h-trend_breakout",
        "direction": direction,
        "timeframe": TIMEFRAME,
        "supported_instruments": [SYMBOL],
        "max_hold_bars": hold,
        "description": (
            "ETH1h trend_breakout ema19/53 + z20 release window + "
            "prev_high/low break + cci/macd volume proxy"
        )[:160],
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "schema": "qiyu_strategy_dsl_v1",
    }
    return sanitize_dsl(f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME))


ALLOWED_DSL_TOP = {
    "schema", "key", "name", "direction", "timeframe",
    "supported_instruments", "entry", "exit", "max_hold_bars",
    "description", "origin", "version", "live_enabled",
    "approved_version_hash", "auto_trade_eligible",
}


def sanitize_dsl(dsl):
    """Strip unknown top-level fields; keep key DSL-safe."""
    dsl = copy.deepcopy(dsl or {})
    for k in list(dsl.keys()):
        if k not in ALLOWED_DSL_TOP:
            dsl.pop(k, None)
    key = str(dsl.get("key") or "frost3_eth1h_breakout")
    key = re.sub(r"[^0-9A-Za-z_]", "_", key)[:100]
    if not key:
        key = "frost3_eth1h_breakout"
    dsl["key"] = key
    # re-stamp ids cleanly
    for i, row in enumerate((dsl.get("entry") or {}).get("all") or []):
        row["id"] = "e%d" % (i + 1)
        for bad in list(row.keys()):
            if bad not in ("id", "left", "op", "right", "lower", "upper", "role"):
                row.pop(bad, None)
    for i, row in enumerate((dsl.get("exit") or {}).get("any") or []):
        row["id"] = "x%d" % (i + 1)
        for bad in list(row.keys()):
            if bad not in ("id", "left", "op", "right", "lower", "upper", "role"):
                row.pop(bad, None)
    return dsl


def make_book(row, idx):
    dsl = build_eth1h_breakout_dsl(row, idx)
    return {
        "title": "ETH1h-breakout#%d" % idx,
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": dsl.get("direction") or str(row.get("direction") or "long").lower(),
        "logic_class": LOGIC_FAMILY,
        "thesis": row.get("thesis"),
        "entry_sketch": row.get("entry_sketch"),
        "exit_sketch": row.get("exit_sketch"),
        "hypothesis": {
            "ema_period": row.get("ema_period") or "19/53",
            "vol_contraction": row.get("vol_contraction"),
            "volume_proxy": row.get("volume_proxy"),
            "rationale": row.get("why_avoids_cl_traps"),
        },
        "why_avoids_cl_traps": row.get("why_avoids_cl_traps"),
        "why_avoids_freezer": row.get("why_avoids_freezer"),
        "diff_vs_live": row.get("diff_vs_live_ada_ltc_ng_xrp") or row.get("diff_vs_live"),
        "avoid_from_postmortem": row.get("avoid_death"),
        "dsl": dsl,
        "gate_mode": "frost2",
        "source": "frost3_eth1h_breakout",
        "dir_rank": idx,
        "hypothesis_book_zh": {
            "ema_period": row.get("ema_period") or "19/53",
            "vol_contraction_def": row.get("vol_contraction"),
            "volume_threshold": row.get("volume_proxy"),
            "causal_entry_exit": {
                "entry": row.get("entry_sketch"),
                "exit": row.get("exit_sketch"),
                "thesis": row.get("thesis"),
            },
            "diff_vs_live": row.get("diff_vs_live_ada_ltc_ng_xrp"),
        },
    }


BOOK_PROMPT = """你是GLM-5.2。只输出JSON。批准或修订《ETH 1h 趋势突破假设书》。
必须确认：1)EMA周期 2)波动收缩定义 3)量能阈值+理由 均清楚。
禁止改标的/周期；禁止改成exhaustion/reclaim家族；必须保持trend_breakout骨架。
对照CL：trades预期≥12；避免rsi刚过54；保留结构失效。
JSON：{"decision":"pass|reject|revise","score":0到100,
"answers_ok":{"ema_period":true,"vol_contraction":true,"volume_threshold":true},
"revise":{"entry_sketch":null,"exit_sketch":null,"param_tweaks":{},"why":"..."},
"reason_zh":"..."}
"""


def glm_audit_book(book, report):
    payload = {
        "book": {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "thesis": book.get("thesis"),
            "hypothesis": book.get("hypothesis"),
            "hypothesis_book_zh": book.get("hypothesis_book_zh"),
            "entry": (book.get("dsl") or {}).get("entry"),
            "exit": (book.get("dsl") or {}).get("exit"),
            "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
            "volume_proxy_note": (report or {}).get("volume_proxy_note_zh"),
        },
        "cl_traps": (report or {}).get("cl_lessons_zh"),
        "freezer": (report or {}).get("freezer_breakout_lessons_zh"),
        "banned_logic": list(BANNED_LOGIC_SUBSTR),
    }
    ai = d._ai_json("glm", BOOK_PROMPT, payload, max_tokens=1000, temperature=0.1)
    parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
    raw = ai.get("raw_preview") or ""
    if not parsed and raw:
        m = re.search(r"\{[\s\S]*\}", str(raw))
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
    return parsed or {"decision": "pass", "reason_zh": "audit_fallback_pass", "fallback": True}, ai


def apply_revise(book, revise):
    book = copy.deepcopy(book)
    if not isinstance(revise, dict):
        return book
    if revise.get("entry_sketch"):
        book["entry_sketch"] = revise["entry_sketch"]
    if revise.get("exit_sketch"):
        book["exit_sketch"] = revise["exit_sketch"]
    tweaks = revise.get("param_tweaks") or {}
    if revise.get("entry_sketch") or revise.get("exit_sketch"):
        row = {
            "symbol": SYMBOL, "timeframe": TIMEFRAME,
            "direction": book["direction"], "logic_class": LOGIC_FAMILY,
            "thesis": book.get("thesis"),
            "entry_sketch": book.get("entry_sketch"),
            "exit_sketch": book.get("exit_sketch"),
            "ema_period": (book.get("hypothesis") or {}).get("ema_period"),
            "vol_contraction": (book.get("hypothesis") or {}).get("vol_contraction"),
            "volume_proxy": (book.get("hypothesis") or {}).get("volume_proxy"),
        }
        book["dsl"] = build_eth1h_breakout_dsl(row, int(book.get("dir_rank") or 1))
    if tweaks:
        for phase in ("entry", "exit"):
            block = (book.get("dsl") or {}).get(phase) or {}
            rows = block.get("all") or block.get("any") or []
            for r in rows:
                feat = ((r.get("left") or {}).get("feature"))
                if feat in tweaks and isinstance(tweaks[feat], (int, float)):
                    if isinstance(r.get("right"), dict) and "value" in r["right"]:
                        r["right"]["value"] = float(tweaks[feat])
    book["logic_class"] = LOGIC_FAMILY
    book["dsl"] = sanitize_dsl(f2.ensure_dsl(book["dsl"], SYMBOL, TIMEFRAME))
    return book


def stamp_book_keys(book, tag=""):
    if not book.get("dsl"):
        return book
    book["symbol"] = SYMBOL
    book["timeframe"] = TIMEFRAME
    book["logic_class"] = LOGIC_FAMILY
    book["dsl"]["supported_instruments"] = [SYMBOL]
    book["dsl"]["timeframe"] = TIMEFRAME
    k = str(book["dsl"].get("key") or "")
    if "eth1h_breakout" not in k:
        book["dsl"]["key"] = ("frost3_eth1h_breakout_" + _safe(k) + tag)[:100]
    elif tag and not k.endswith(tag):
        book["dsl"]["key"] = (k + tag)[:100]
    book["dsl"] = sanitize_dsl(f2.ensure_dsl(book["dsl"], SYMBOL, TIMEFRAME))
    return book


def archive_result(result, death_cause):
    arch = os.path.join(OUT, "archive", "frost3_eth1h_breakout_%s" % (
        result.get("dir_id") or "fail"))
    os.makedirs(arch, exist_ok=True)
    payload = dict(result)
    payload["death_cause"] = death_cause
    payload["archived_at"] = _now()
    open(os.path.join(arch, "result.json"), "w").write(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
    open(os.path.join(arch, "DEATH_CAUSE.json"), "w").write(
        json.dumps(death_cause, ensure_ascii=False, indent=2, default=str) + "\n")
    write_art("archive_pointer", {
        "archive_path": arch, "death_cause": death_cause, "at": _now(),
    })
    return arch


def process(book, report):
    hist = []
    dir_id = "eth1h_breakout_%s" % _safe(book.get("direction"))
    status = {
        "dir_id": dir_id, "stage": "audit", "updated_at": _now(),
        "symbol": SYMBOL, "tf": TIMEFRAME, "logic": LOGIC_FAMILY,
        "key": (book.get("dsl") or {}).get("key"),
    }
    write_art("status", status)

    audit_ok = False
    for atry in range(MAX_AUDIT_REVISE + 1):
        decision, ai = glm_audit_book(book, report)
        write_art("audit_try%d" % atry, {
            "decision": decision,
            "ai": {"ok": ai.get("ok"), "preview": (ai.get("raw_preview") or "")[:500]},
        })
        hist.append({
            "stage": "audit", "try": atry, "decision": decision.get("decision"),
            "reason": decision.get("reason_zh"), "fallback": decision.get("fallback"),
        })
        dec = str(decision.get("decision") or "pass").lower()
        if dec == "pass":
            audit_ok = True
            break
        if dec == "revise" and atry < MAX_AUDIT_REVISE:
            book = stamp_book_keys(apply_revise(book, decision.get("revise") or {}))
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
        book = stamp_book_keys(f3.local_tweak(book, qtry + 1, packs.get("failed_step")), "_t")
        repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step"), "quick")
        if repaired:
            repaired["logic_class"] = LOGIC_FAMILY
            book = stamp_book_keys(repaired, "_r")
        hist.append({"stage": "quick_repair", "try": qtry, "ok": bool(repaired)})

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
        book = stamp_book_keys(f3.local_tweak(book, ftry + 1, packs.get("failed_step")), "_ft")
        repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step"), "full")
        if repaired:
            repaired["logic_class"] = LOGIC_FAMILY
            book = stamp_book_keys(repaired, "_fr")
        q2 = f2.quick_suite(book)
        hist.append({
            "stage": "re_quick_after_full_repair", "pass": q2.get("quick_pass"),
            "failed_step": q2.get("failed_step"),
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
                "reason": (sf.get("formal") or {}).get("reason") or (
                    sf.get("pending") or {}).get("reason"),
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
        book = stamp_book_keys(f3.local_tweak(book, stry + 1, "sim"), "_st")
        repaired, _ai = f3.glm_repair(book, packs, "sim_review", "sim")
        if repaired:
            repaired["logic_class"] = LOGIC_FAMILY
            book = stamp_book_keys(repaired, "_sr")
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
    print("=== ETH1h trend_breakout START ===", _now(), flush=True)
    d._ensure_dirs()
    d._load_env()

    # r2+: --resume-fallback skips heavy frame/GLM; uses canonical DSL after r1 validate death
    resume_fallback = "--resume-fallback" in sys.argv
    write_art("run_mode", {
        "at": _now(), "resume_fallback": resume_fallback, "argv": list(sys.argv),
    })

    if resume_fallback:
        print("[eth1h_bo] RESUME-FALLBACK mode (skip GLM/frame)", flush=True)
        freezer_bo = {
            "volume_feature_note_zh": (
                "DSL无volume；量能代理 cci+macd_stick+z20；"
                "收缩代理 z20 release window"
            ),
        }
        # reuse prior inputs if present
        try:
            packet = json.load(open(os.path.join(
                OUT, "%s_inputs.json" % PREFIX)))
        except Exception:
            packet = {
                "eth1h_vol": {"atr_pct_q50": 0.0089},
                "breakout_freezer": freezer_bo,
            }
        report = fallback_param_plan(packet)
        variants = sanitize_variants(report)
        report["variants"] = variants
        write_art("param_plan", report)
        write_art("ETH1h趋势突破参数方案", report)
        pick = variants[0]
        write_art("picked_variant", pick)
        print("[eth1h_bo] picked fallback rank1", pick.get("direction"), flush=True)
        hyp = {
            "at": _now(),
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "logic_family": LOGIC_FAMILY,
            "ema_period": pick.get("ema_period"),
            "vol_contraction_definition": pick.get("vol_contraction"),
            "volume_threshold_and_rationale": pick.get("volume_proxy"),
            "full_hypothesis": report.get("hypothesis"),
            "picked": pick,
            "volume_dsl_limitation_zh": freezer_bo.get("volume_feature_note_zh"),
            "resume_fallback": True,
            "r1_death_note": "r1 validate failed: DSL unknown top-level field meta",
        }
        write_art("hypothesis", hyp)
        book = make_book(pick, 2)
        write_art("hypothesis_book_v0", book)
    else:
        cl = step1.load_cl_archive()
        micro = load_eth_micro()
        death = load_death()
        freezer_bo = load_breakout_freezer()
        vol = load_eth1h_vol_snapshot()
        packet = {
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "logic_family": LOGIC_FAMILY,
            "cl_archive": cl,
            "eth_micro": micro,
            "eth1h_vol": vol,
            "death": death,
            "breakout_freezer": freezer_bo,
            "live_protect": ["ADA", "LTC", "NG", "XRP"],
            "gates": {
                "wf": ">=7/10",
                "logic_destruction": True,
                "mc_beat": ">=0.90",
                "friction_sharpe": ">=0",
                "sim": "DS&Qwen>=55%",
            },
            "parallel_note": "独立于 frost3_eth5m_*；产物前缀 frost3_eth1h_breakout_*",
        }
        write_art("inputs", packet)

        report, ai = ask_glm_params(packet)
        write_art("glm_param_plan_raw", {
            "ok": ai.get("ok"), "parsed": bool(report),
            "preview": (ai.get("raw_preview") or "")[:2000],
            "error": ai.get("error"),
        })
        if not report or not sanitize_variants(report):
            print("[eth1h_bo] GLM param plan fail/empty → fallback", flush=True)
            report = fallback_param_plan(packet)
        variants = sanitize_variants(report)
        if not variants:
            report = fallback_param_plan(packet)
            variants = sanitize_variants(report)
        report["variants"] = variants
        if not report.get("hypothesis"):
            report["hypothesis"] = fallback_param_plan(packet)["hypothesis"]
        write_art("param_plan", report)
        write_art("ETH1h趋势突破参数方案", report)

        rec = int(report.get("recommended_rank") or 1)
        pick = None
        for row in variants:
            if int(row.get("rank") or 0) == rec:
                pick = row
                break
        if pick is None:
            pick = variants[0]
        write_art("picked_variant", pick)
        print("[eth1h_bo] picked rank", pick.get("rank"), pick.get("direction"),
              pick.get("ema_period"), flush=True)

        hyp = {
            "at": _now(),
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "logic_family": LOGIC_FAMILY,
            "ema_period": pick.get("ema_period") or (report.get("hypothesis") or {}).get("ema_period"),
            "vol_contraction_definition": pick.get("vol_contraction") or (
                report.get("hypothesis") or {}).get("vol_contraction_def"),
            "volume_threshold_and_rationale": pick.get("volume_proxy") or (
                report.get("hypothesis") or {}).get("volume_threshold"),
            "full_hypothesis": report.get("hypothesis"),
            "picked": pick,
            "volume_dsl_limitation_zh": freezer_bo.get("volume_feature_note_zh"),
        }
        write_art("hypothesis", hyp)

        book = make_book(pick, 1)
        write_art("hypothesis_book_v0", book)

    try:
        result = process(book, report)
    except Exception as exc:
        result = {
            "dir_id": "eth1h_breakout_exception", "ok": False,
            "failed_step": "exception", "reason": str(exc),
            "trace": traceback.format_exc()[-2500:],
        }

    end = {
        "at": _now(),
        "op": "ETH1h趋势突破独立创造",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_family": LOGIC_FAMILY,
        "ok": bool(result.get("ok")),
        "failed_step": result.get("failed_step"),
        "reason": result.get("reason"),
        "pending": result.get("pending"),
        "sim": result.get("sim"),
        "formal": result.get("formal"),
        "logic": LOGIC_FAMILY,
        "direction": (result.get("book") or book).get("direction"),
        "key": ((result.get("book") or book).get("dsl") or {}).get("key"),
        "hist": result.get("hist"),
        "picked": pick,
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
            "logic_class": LOGIC_FAMILY,
            "key": end["key"],
            "summary_zh": "ETH1h trend_breakout 失败于 %s：%s" % (
                result.get("failed_step"), result.get("reason")),
        }
        arch = archive_result(result, death_cause)
        end["archive_path"] = arch
        end["death_cause"] = death_cause
        print("[eth1h_bo] ARCHIVED", arch, death_cause["exact_death"], flush=True)
    else:
        print("[eth1h_bo] PENDING", (result.get("pending") or {}).get("key"), flush=True)

    write_art("end_report", end)
    write_art("status", {"finished_at": _now(), "ok": end["ok"], "end": end})

    if end["ok"]:
        summary_zh = (
            "ETH1h趋势突破【成功】dir=%s key=%s pending=%s sim_ds/qw=%s/%s "
            "EMA=%s 收缩=%s 量能代理=%s"
            % (
                end.get("direction"), end["key"],
                (end.get("pending") or {}).get("key"),
                (end.get("sim") or {}).get("wr_deepseek_sim"),
                (end.get("sim") or {}).get("wr_qwen_sim"),
                hyp.get("ema_period"),
                hyp.get("vol_contraction_definition"),
                hyp.get("volume_threshold_and_rationale"),
            )
        )
    else:
        summary_zh = (
            "ETH1h趋势突破【失败归档】dir=%s 死因=%s 详情=%s 归档=%s "
            "假设:EMA=%s 收缩=%s 量能=%s"
            % (
                end.get("direction"), end.get("failed_step"), end.get("reason"),
                end.get("archive_path"),
                hyp.get("ema_period"),
                hyp.get("vol_contraction_definition"),
                hyp.get("volume_threshold_and_rationale"),
            )
        )
    write_art("parent_summary_zh", {"summary_zh": summary_zh, "at": _now(), "end": {
        "ok": end["ok"], "failed_step": end.get("failed_step"),
        "pending": end.get("pending"), "key": end.get("key"),
        "archive_path": end.get("archive_path"),
    }})
    print("=== ETH1h trend_breakout END ===", summary_zh, flush=True)
    print("PARENT_SUMMARY_ZH:", summary_zh, flush=True)
    return 0 if end["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
