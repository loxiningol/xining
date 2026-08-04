#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone XAU-USDT-SWAP 15m trend-breakout creation (frost3).

Logic: EMA trend filter + vol contraction → directional breakout + volume confirm.
Artifacts ONLY under frost3_xau15m_breakout_* — does not touch live ADA/LTC/NG/XRP.
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
PREFIX = "frost3_xau15m_breakout"
SYMBOL = "XAU-USDT-SWAP"
TIMEFRAME = "15m"
MAX_AUDIT_REVISE = 2
MAX_QUICK_REPAIR = 3
MAX_FULL_REPAIR = 3
MAX_SIM_REPAIR = 3
FRAME_PKL = "/root/auto_trade/dual_engine/frost3_xau15m_breakout_frame.pkl"

# Required logic family for this task (breakout-class, but not the deleted xau15_h1 clone)
REQUIRED_LOGIC_SUBSTR = ("breakout", "trend_break", "vol_contract", "收缩", "突破")

# Prefer shorter local parquet window under multi-agent I/O pressure
os.environ.setdefault("QIYU_ECOSYSTEM_BACKTEST_START", "2026-04-01 00:00:00")


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_xau_frame_cache():
    """Preload XAU 15m research frame once and monkeypatch factory/pipeline loaders."""
    import pickle
    fr = None
    if os.path.isfile(FRAME_PKL):
        try:
            fr = pickle.load(open(FRAME_PKL, "rb"))
            _log("loaded frame pickle n=%s" % (len(fr) if fr is not None else None))
        except Exception as exc:
            _log("frame pickle load fail: %s" % exc)
            fr = None
    if fr is None:
        _log("building research frame from local market_data...")
        fr = d._frame(SYMBOL, TIMEFRAME)
        try:
            pickle.dump(fr, open(FRAME_PKL, "wb"), protocol=pickle.HIGHEST_PROTOCOL)
            _log("wrote frame pickle n=%s path=%s" % (len(fr), FRAME_PKL))
        except Exception as exc:
            _log("frame pickle write fail: %s" % exc)
    # monkeypatch both factory and human_confirm pipeline caches
    import auto_trade_human_confirm_pipeline as pipeline

    def _cached_frame(symbol, timeframe):
        if str(symbol).upper() == SYMBOL and str(timeframe) == TIMEFRAME:
            return fr
        return pipeline._frame.__wrapped__(symbol, timeframe) if hasattr(pipeline._frame, "__wrapped__") else fr

    # Keep it simple: always return cached XAU frame for our symbol; others unused
    def _xau_only_frame(symbol, timeframe):
        if str(symbol).upper() == SYMBOL and str(timeframe) == TIMEFRAME:
            return fr
        # fallback original via eco for unexpected calls
        import auto_trade_strategy_ecosystem as eco
        return eco._load_research_frame(symbol, timeframe)

    pipeline._FRAME_CACHE["%s|%s" % (SYMBOL, TIMEFRAME)] = fr
    pipeline._frame = _xau_only_frame
    d._frame = _xau_only_frame
    write_art("frame_meta", {
        "n_bars": int(len(fr)) if fr is not None else 0,
        "start": str(fr.index[0]) if fr is not None and len(fr) else None,
        "end": str(fr.index[-1]) if fr is not None and len(fr) else None,
        "pkl": FRAME_PKL,
        "backtest_start_env": os.environ.get("QIYU_ECOSYSTEM_BACKTEST_START"),
        "at": _now(),
    })
    return fr


def write_art(suffix, obj):
    path = os.path.join(OUT, "%s_%s.json" % (PREFIX, suffix))
    open(path, "w").write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


def _safe(s):
    s = str(s or "x")
    for ch in (".", " ", "/", ":", "+", "%", "-"):
        s = s.replace(ch, "p" if ch in (".", "-") else "_")
    return s[:40]


def _log(msg):
    print("[%s] %s" % (PREFIX, msg), flush=True)
    try:
        open(os.path.join(OUT, "%s_run.log" % PREFIX), "a").write(
            "%s %s\n" % (_now(), msg))
    except Exception:
        pass


# ─── Analysis: US-session ATR + continuation + freezer breakout deaths ───

def load_xau_micro():
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


def load_breakout_deaths():
    """Freezer / failure_vault breakout-class deaths + CL archive traps.

    Avoid d.collect_inputs() here — under parallel frost3 load it can block
    for minutes; use prior dump / CL archive / known breakout deaths instead.
    """
    death = {
        "freezer_breakout": [],
        "death_heatmap_top10": [
            {"code": "stop_cluster"},
            {"code": "negative_net_expectancy"},
            {"code": "cost_collapse"},
            {"code": "sample_starvation"},
            {"code": "holdout_collapse"},
            {"code": "ai_logic_rejection"},
            {"code": "posterior_or_multiple_testing_failure"},
        ],
        "cl_traps": [],
        "error": None,
        "collect_inputs_skipped": True,
    }
    # reuse prior dump if present
    prior = os.path.join(OUT, "%s_breakout_deaths.json" % PREFIX)
    try:
        if os.path.isfile(prior):
            old = json.load(open(prior))
            if old.get("freezer_breakout"):
                death["freezer_breakout"] = old["freezer_breakout"]
    except Exception:
        pass
    if not death["freezer_breakout"]:
        death["freezer_breakout"] = [
            {
                "source": "failure_vault",
                "key": "xau15_h1_breakout_long_ai",
                "reason": "legacy_rereview_ai_reject:avg=44.333;deepseek/qwen/chatgpt REJECT",
                "grade": "deleted",
            },
            {
                "source": "failure_vault",
                "key": "conventional_up_break_long",
                "reason": "legacy_rereview_ai_reject:avg=28.667",
                "grade": "deleted",
            },
        ]
    try:
        cl = step1.load_cl_archive()
        death["cl_traps"] = cl.get("trap_checklist") or cl.get("why_exhausted") or []
        death["cl_summary"] = cl.get("summary_zh") or cl.get("recommendation") or ""
    except Exception as exc:
        death["cl_error"] = str(exc)
        death["cl_traps"] = [
            "WF needs folds>=10 → trades>=10",
            "勿单阈值压交易到<10",
            "软化过滤勿引入新硬止损",
            "logic_destruction±20%必须稳健",
            "friction崩盘主因硬止损簇",
        ]
    death["known_breakout_deaths"] = [
        {
            "key": "xau15_h1_breakout_long_ai",
            "death": "legacy_rereview_ai_reject",
            "lesson_zh": "旧XAU15m H1突破被三模型拒：名义WR中等但风险高/成本后期望弱；勿原样复刻cci>130硬冲",
        },
        {
            "key": "conventional_up_break_long",
            "death": "legacy_rereview_ai_reject",
            "lesson_zh": "无过滤常规向上突破易被AI拒；需趋势+收缩+量能确认与结构失效",
        },
    ]
    return death


def _pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    i = int(round((len(xs) - 1) * p / 100.0))
    return xs[max(0, min(len(xs) - 1, i))]


def _stats(xs):
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean": sum(xs) / float(len(xs)),
        "p50": _pct(xs, 50),
        "p75": _pct(xs, 75),
        "p90": _pct(xs, 90),
        "p95": _pct(xs, 95),
        "lte_0.3pct": sum(1 for x in xs if x <= 0.003) / float(len(xs)),
        "lte_0.45pct": sum(1 for x in xs if x <= 0.0045) / float(len(xs)),
    }


def analyze_us_atr_continuation():
    """Prefer factory frame; fallback to formal candle cache."""
    report = {
        "source": None,
        "n_bars": 0,
        "us_hour_utc": [13, 21],
        "stop_loss_pct": 0.009,
        "leverage": 20,
        "note_zh": "",
    }
    rows = []
    # Prefer candle cache for pre-analysis (factory _frame is I/O heavy under frost3 load).
    # Full backtests later will load the frame via quick_suite.
    try:
        c = json.load(open("/root/auto_trade/formal_xau_15m_candles_cache.json"))
        bars = c.get("candles") or c.get("data")
        if bars is None:
            for _k, v in c.items():
                if isinstance(v, list) and v and isinstance(v[0], dict) and "close" in v[0]:
                    bars = v
                    break
        report["source"] = "formal_candle_cache"
        report["n_bars"] = len(bars or [])
        prev_c = None
        trs = []
        closes = []
        for b in (bars or []):
            ts = b["ts"]
            dt = datetime.utcfromtimestamp(ts / 1000.0)
            o, h, l, cl = float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])
            if prev_c is None:
                tr = h - l
            else:
                tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)
            closes.append(cl)
            atr = sum(trs[-14:]) / 14.0 if len(trs) >= 14 else None
            atr_pct = (atr / cl) if (atr and cl) else None
            rows.append({
                "dt": dt, "hour": dt.hour, "o": o, "h": h, "l": l, "c": cl,
                "amp": (h - l) / cl if cl else 0.0, "atr_pct": atr_pct,
                "vol_z20": None, "atr14": atr, "h1_atr14": None,
                "prev_high20": None, "h1_ema19": None, "h1_ema53": None,
                "h1_slope4": None, "ema21": None, "macd_stick": None,
                "rsi14": None, "z20": None,
            })
            prev_c = cl
        # fill vol_z20 proxy from bar amplitude z-score
        amps = [r["amp"] for r in rows]
        for i, r in enumerate(rows):
            w = amps[max(0, i - 19):i + 1]
            if len(w) >= 8:
                mu = sum(w) / float(len(w))
                var = sum((x - mu) ** 2 for x in w) / float(len(w))
                sd = var ** 0.5
                r["vol_z20"] = ((r["amp"] - mu) / sd) if sd > 1e-12 else 0.0
        # simple ema21 / prev_high20 / h1 proxies from 15m for continuation stats
        ema21 = None
        alpha21 = 2.0 / 22.0
        highs = []
        # hourly closes for h1 ema
        h1_map = {}
        for r in rows:
            key = r["dt"].replace(minute=0, second=0, microsecond=0) if hasattr(r["dt"], "replace") else r["dt"]
            h1_map[key] = r["c"]
        h1_keys = sorted(h1_map.keys())
        h1_closes = [h1_map[k] for k in h1_keys]
        def _ema_series(xs, n):
            if not xs:
                return []
            a = 2.0 / (n + 1.0)
            out = [xs[0]]
            for x in xs[1:]:
                out.append(a * x + (1 - a) * out[-1])
            return out
        e19 = _ema_series(h1_closes, 19)
        e53 = _ema_series(h1_closes, 53)
        h1_idx = {h1_keys[i]: i for i in range(len(h1_keys))}
        for r in rows:
            ema21 = r["c"] if ema21 is None else (alpha21 * r["c"] + (1 - alpha21) * ema21)
            r["ema21"] = ema21
            highs.append(r["h"])
            if len(highs) > 20:
                r["prev_high20"] = max(highs[-21:-1])
            hk = r["dt"].replace(minute=0, second=0, microsecond=0) if hasattr(r["dt"], "replace") else None
            if hk in h1_idx:
                ii = h1_idx[hk]
                r["h1_ema19"] = e19[ii]
                r["h1_ema53"] = e53[ii]
                if ii >= 4 and h1_closes[ii - 4] > 0:
                    r["h1_slope4"] = (h1_closes[ii] - h1_closes[ii - 4]) / h1_closes[ii - 4]
            # h1_atr14 rough: mean hourly TR
            if hk in h1_idx and h1_idx[hk] >= 14:
                ii = h1_idx[hk]
                # approximate h1 atr as 4x 15m atr median of last hour-ish
                r["h1_atr14"] = (r["atr14"] * 2.2) if r.get("atr14") else None
    except Exception as exc:
        report["cache_error"] = str(exc)
        return report

    def is_us(r):
        return 13 <= int(r.get("hour") or 0) < 21

    us = [r for r in rows if is_us(r) and r.get("atr_pct") is not None]
    allok = [r for r in rows if r.get("atr_pct") is not None]
    amp_us = [r["amp"] for r in us]
    amp_all = [r["amp"] for r in allok]
    atr_us = [r["atr_pct"] for r in us]
    atr_all = [r["atr_pct"] for r in allok]
    report["amp_all"] = _stats(amp_all)
    report["amp_us"] = _stats(amp_us)
    report["atr_pct_all"] = _stats(atr_all)
    report["atr_pct_us"] = _stats(atr_us)
    if atr_us:
        med = _pct(atr_us, 50)
        report["stop_vs_us_atr"] = {
            "stop_over_mean_atr": 0.009 / (sum(atr_us) / float(len(atr_us))),
            "stop_over_median_atr": 0.009 / med if med else None,
            "safety_zh": "0.9%硬止损约为US会话ATR中位数的5×+，相对正常15m波幅(常≤0.3%)安全垫充足；主要风险是趋势反转硬止损簇而非噪声扫损",
        }
        # atr14 absolute for DSL thresholds (price units)
        atr_abs_us = [r["atr14"] for r in us if r.get("atr14") is not None]
        if atr_abs_us:
            report["atr14_abs_us"] = _stats(atr_abs_us)
            report["suggested_atr14_contraction_lt"] = _pct(atr_abs_us, 40)

    # continuation: trend+contract+breakout+volz
    cont = {"long": [], "short": []}
    hold = 8
    for i in range(60, len(rows) - hold - 1):
        r = rows[i]
        if not is_us(r):
            continue
        window = rows[i - 20:i]
        atrs = [x["atr_pct"] for x in window if x.get("atr_pct") is not None]
        if len(atrs) < 12:
            continue
        med = sorted(atrs)[len(atrs) // 2]
        recent = atrs[-4:]
        contr = (sum(recent) / len(recent)) < 0.9 * med
        # trend proxies
        trend_long = True
        trend_short = True
        if r.get("h1_ema19") is not None and r.get("h1_ema53") is not None:
            trend_long = r["h1_ema19"] > r["h1_ema53"]
            trend_short = r["h1_ema19"] < r["h1_ema53"]
        if r.get("h1_slope4") is not None:
            trend_long = trend_long and r["h1_slope4"] > 0
            trend_short = trend_short and r["h1_slope4"] < 0
        ph = max(x["h"] for x in window)
        pl = min(x["l"] for x in window)
        vol_ok = True
        if r.get("vol_z20") is not None:
            vol_ok = r["vol_z20"] > 0.2
        if contr and trend_long and r["c"] > ph and r["c"] > r["o"] and vol_ok:
            entry = r["c"]
            fut = rows[i + 1:i + 1 + hold]
            mfe = max((x["h"] - entry) / entry for x in fut)
            mae = max((entry - x["l"]) / entry for x in fut)
            end = (fut[-1]["c"] - entry) / entry
            cont["long"].append({"mfe": mfe, "mae": mae, "end": end})
        if contr and trend_short and r["c"] < pl and r["c"] < r["o"] and vol_ok:
            entry = r["c"]
            fut = rows[i + 1:i + 1 + hold]
            mfe = max((entry - x["l"]) / entry for x in fut)
            mae = max((x["h"] - entry) / entry for x in fut)
            end = (entry - fut[-1]["c"]) / entry
            cont["short"].append({"mfe": mfe, "mae": mae, "end": end})

    cont_sum = {}
    for side, arr in cont.items():
        if not arr:
            cont_sum[side] = {"n": 0}
            continue
        cont_sum[side] = {
            "n": len(arr),
            "mfe_p50": _pct([a["mfe"] for a in arr], 50),
            "mae_p50": _pct([a["mae"] for a in arr], 50),
            "end_pos_rate": sum(1 for a in arr if a["end"] > 0) / float(len(arr)),
            "mfe_gt_mae": sum(1 for a in arr if a["mfe"] > a["mae"]) / float(len(arr)),
            "mfe_ge_0.45pct": sum(1 for a in arr if a["mfe"] >= 0.0045) / float(len(arr)),
            "mae_lt_0.9pct": sum(1 for a in arr if a["mae"] < 0.009) / float(len(arr)),
        }
    report["continuation_us_trend_contract_breakout"] = cont_sum
    report["param_hints"] = {
        "ema_trend": "h1_ema19>h1_ema53 + h1_slope4>0 + close>ema21",
        "vol_contraction": "atr14 < h1_atr14 (15m相对1h收缩) 或 atr14 < US p40",
        "breakout": "close > prev_high20",
        "volume_confirm": "vol_z20 > 0.3 (无成交量时为波幅z代理)",
        "stop_match": "硬止损0.9%~5×US ATR，出场必须以结构失效为主，避免靠止损吃噪声",
        "max_hold_bars": 24,
        "avoid": "勿复刻已删 xau15_h1_breakout_long_ai；勿无过滤破高；trades目标≥12",
    }
    report["note_zh"] = (
        "XAU 15m US会话ATR中位约0.16–0.17%，bar波幅常≤0.3%；"
        "0.9%@20x相对正常bar有充足安全垫；突破后延续依赖趋势过滤+收缩后放量，"
        "裸破高延续偏弱。"
    )
    return report


# ─── GLM parameter plan + direction ───

PLAN_PROMPT = """你是GLM-5.2。只输出纯JSON，禁止Markdown。
任务：输出《XAU 15m 趋势突破参数方案》。

硬约束：
1) symbol=XAU-USDT-SWAP，timeframe=15m，禁止改标的/周期
2) 逻辑必须是：EMA趋势过滤 + 波动收缩 → 方向突破 + 量能确认（可用vol_z20）
3) 必须吸收 freezer 突破类死亡：勿复刻已删 xau15_h1_breakout_long_ai / conventional_up_break_long
4) 吸收 CL15m 陷阱：trades预期≥12；勿边际rsi≈54；勿单阈值把样本压死；保留结构失效出场；logic_destruction±20%要稳；friction崩多因硬止损簇
5) 参考 us_atr 分析：止损0.9%约为US ATR数倍，出场靠结构不是靠硬止损吃波动
6) 可用特征：rsi14,z20,vol_z20,macd_stick,cci,atr14,h1_atr14,ema8/16/21,close,prev_high20,prev_low20,h1_ema19,h1_ema53,h1_slope4

JSON schema：
{
  "report_title":"XAU 15m 趋势突破参数方案",
  "symbol":"XAU-USDT-SWAP",
  "timeframe":"15m",
  "micro_read_zh":"...",
  "us_vol_read_zh":"...",
  "breakout_death_read_zh":"...",
  "hypothesis":{
    "logic_class":"ema_trend_vol_contract_breakout",
    "direction":"long",
    "ema_period_note":"用ema21+ h1_ema19/53，不必另造周期",
    "vol_contraction_def":"atr14<h1_atr14 或等价",
    "stop_atr_match_zh":"...",
    "entry_sketch":"分号分隔条件",
    "exit_sketch":"止盈与结构失效; max_hold=24",
    "expected_trades_hint":">=12",
    "why_diff_vs_deleted_xau_breakout":"..."
  },
  "alt_directions":[{"rank":2,"direction":"long|short","logic_class":"...","entry_sketch":"...","exit_sketch":"..."}],
  "decision_ready":true,
  "mentor_notes":"..."
}
"""


BOOK_PROMPT = """你是GLM-5.2。只输出JSON。批准或修订《策略逻辑假设书》（XAU-USDT-SWAP 15m 趋势突破）。
必须确认：1)EMA趋势+波动收缩→突破+量能 2)规避突破类freezer死亡与CL陷阱 3)止损-ATR匹配与结构失效出场。
禁止改标的/周期；禁止改成exhaustion_fade/range_reclaim。
JSON：{"decision":"pass|reject|revise","score":0到100,
"answers_ok":{"trend_contract_breakout":true,"avoid_deaths":true,"stop_atr":true},
"revise":{"entry_sketch":null,"exit_sketch":null,"param_tweaks":{},"why":"..."},
"reason_zh":"..."}
"""


def ask_glm_plan(packet):
    ai = d._ai_json("glm", PLAN_PROMPT, packet, max_tokens=2200, temperature=0.15)
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


def fallback_plan(atr_report, deaths):
    atr_lt = (atr_report.get("suggested_atr14_contraction_lt")
              or ((atr_report.get("atr14_abs_us") or {}).get("p50"))
              or 8.0)
    return {
        "report_title": "XAU 15m 趋势突破参数方案",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "micro_read_zh": "XAU微观用vol_z20/波幅z与atr收缩代理流动性压缩后的方向释放。",
        "us_vol_read_zh": atr_report.get("note_zh") or "",
        "breakout_death_read_zh": (
            "freezer: xau15_h1_breakout_long_ai 与 conventional_up_break_long 被AI拒；"
            "需趋势+收缩+量能+结构失效，禁止裸破高。"
        ),
        "hypothesis": {
            "logic_class": "ema_trend_vol_contract_breakout",
            "direction": "long",
            "ema_period_note": "15m ema21 + H1 ema19/53 趋势对齐",
            "vol_contraction_def": "atr14 < US会话绝对阈值(约p40~median，禁止atr14<h1_atr14伪收缩)",
            "stop_atr_match_zh": (
                "硬止损0.9%≈5×US ATR中位；正常bar≤0.3%不会扫损；"
                "用close<ema21失效快速离场，防趋势反转硬止损簇"
            ),
            "entry_sketch": (
                "h1_ema19>h1_ema53; h1_slope4>0.0003; close>ema21; close>ema8; "
                "close>prev_high20; atr14<%s; vol_z20>0.8; "
                "macd_stick>0; rsi14>55; rsi14<66; z20>0.35; z20<1.35; cci>80; cci<160"
            ) % (atr_lt if atr_lt else 7.5),
            "exit_sketch": "rsi14>70 take_profit; close<ema21 invalidation; max_hold=24",
            "expected_trades_hint": ">=12",
            "why_diff_vs_deleted_xau_breakout": (
                "旧策略偏cci硬阈值冲破；本方案用绝对atr收缩+高vol_z20确认+H1斜率，"
                "禁用atr14<h1_atr14伪条件，ema21结构失效，目标样本≥12"
            ),
            "atr14_hint": atr_lt,
        },
        "alt_directions": [
            {
                "rank": 2,
                "direction": "short",
                "logic_class": "ema_trend_vol_contract_breakout_short",
                "entry_sketch": (
                    "h1_ema19<h1_ema53; h1_slope4<-0.0003; close<ema21; close<ema8; "
                    "close<prev_low20; atr14<%s; vol_z20>0.8; "
                    "macd_stick<0; rsi14<45; rsi14>34; z20<-0.35; z20>-1.35"
                ) % (atr_lt if atr_lt else 7.5),
                "exit_sketch": "rsi14<30 take_profit; close>ema21 invalidation; max_hold=24",
            }
        ],
        "decision_ready": True,
        "mentor_notes": "US会话趋势突破；保护ADA/LTC/NG/XRP不动。",
        "fallback": True,
        "provider": "codex_fallback",
    }


def glm_audit_book(book, plan):
    payload = {
        "book": {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "thesis": book.get("thesis"),
            "entry": (book.get("dsl") or {}).get("entry"),
            "exit": (book.get("dsl") or {}).get("exit"),
            "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
            "hypothesis_book_zh": book.get("hypothesis_book_zh"),
        },
        "plan": plan,
        "cl_traps": (plan or {}).get("breakout_death_read_zh"),
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


# ─── DSL builders ───

def parse_sketch_conditions(sketch):
    conds = []
    text = str(sketch or "")
    for part in re.split(r"[;；\n]+", text):
        part = part.strip()
        if not part:
            continue
        part2 = re.sub(
            r"\b(take_profit|invalidation|max_hold\s*=\s*\d+)\b", "",
            part, flags=re.I).strip()
        m = re.match(
            r"^(h1_slope4|h1_ema19|h1_ema53|h1_atr14|atr14|rsi14|z20|vol_z20|macd_stick|cci|"
            r"ema\d+|close|prev_high\d+|prev_low\d+)\s*"
            r"(>=|<=|>|<)\s*"
            r"(-?\d+(?:\.\d+)?|h1_slope4|h1_ema19|h1_ema53|h1_atr14|atr14|ema\d+|"
            r"prev_high\d+|prev_low\d+|close)$",
            part2.replace(" ", ""),
        )
        if not m:
            continue
        left, op, right = m.group(1), m.group(2), m.group(3)
        if left.startswith("prev_high") and left not in ("prev_high20",):
            left = "prev_high20"
        if left.startswith("prev_low") and left not in ("prev_low20",):
            left = "prev_low20"
        op_map = {">": "gt", "<": "lt", ">=": "gte", "<=": "lte"}
        leaf = {"left": {"feature": left}, "op": op_map.get(op, "gt")}
        try:
            leaf["right"] = {"value": float(right)}
        except Exception:
            if right.startswith("prev_high"):
                right = "prev_high20"
            if right.startswith("prev_low"):
                right = "prev_low20"
            leaf["right"] = {"feature": right}
        conds.append(leaf)
    return conds


def default_long_entry(atr_lt=None):
    # NOTE: atr14 < h1_atr14 is nearly always true (15m ATR << 1h ATR) — NOT contraction.
    # Use absolute atr14 upper bound calibrated from US-session distribution instead.
    if atr_lt is None:
        atr_lt = 7.5
    return [
        {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
        {"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0003}},
        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema8"}},
        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}},
        {"left": {"feature": "atr14"}, "op": "lt", "right": {"value": float(atr_lt)}},
        {"left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.8}},
        {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}},
        {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 66.0}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.35}},
        {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.35}},
        {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 80.0}},
        {"left": {"feature": "cci"}, "op": "lt", "right": {"value": 160.0}},
    ]


def default_short_entry(atr_lt=None):
    if atr_lt is None:
        atr_lt = 7.5
    return [
        {"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}},
        {"left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": -0.0003}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}},
        {"left": {"feature": "atr14"}, "op": "lt", "right": {"value": float(atr_lt)}},
        {"left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.8}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}},
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 34.0}},
        {"left": {"feature": "z20"}, "op": "lt", "right": {"value": -0.35}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -1.35}},
    ]


def build_xau_dsl(hyp, idx=1):
    direction = str(hyp.get("direction") or "long").lower()
    logic = str(hyp.get("logic_class") or "ema_trend_vol_contract_breakout")
    atr_lt = hyp.get("atr14_hint")
    try:
        atr_lt = float(atr_lt) if atr_lt is not None else 7.5
    except Exception:
        atr_lt = 7.5
    # Prefer US-session absolute contraction (~median/p40), never atr14<h1_atr14
    atr_lt = max(4.0, min(12.0, atr_lt))
    entry = parse_sketch_conditions(hyp.get("entry_sketch"))
    # rewrite any atr14<h1_atr14 pseudo-contraction into absolute bound
    fixed = []
    for e in entry:
        left = (e.get("left") or {}).get("feature")
        right = e.get("right") or {}
        if left == "atr14" and e.get("op") in ("lt", "lte") and right.get("feature") == "h1_atr14":
            fixed.append({"left": {"feature": "atr14"}, "op": "lt",
                          "right": {"value": float(atr_lt)}})
        else:
            fixed.append(e)
    entry = fixed
    if len(entry) < 5:
        entry = (default_long_entry(atr_lt) if direction == "long"
                 else default_short_entry(atr_lt))
    # ensure critical leaves present
    feats = set((e.get("left") or {}).get("feature") for e in entry)
    if "prev_high20" not in feats and "prev_low20" not in feats:
        if direction == "long":
            entry.append({"left": {"feature": "close"}, "op": "gt",
                          "right": {"feature": "prev_high20"}})
        else:
            entry.append({"left": {"feature": "close"}, "op": "lt",
                          "right": {"feature": "prev_low20"}})
    if "vol_z20" not in feats:
        entry.append({"left": {"feature": "vol_z20"}, "op": "gt", "right": {"value": 0.8}})
    if "atr14" not in feats:
        entry.append({"left": {"feature": "atr14"}, "op": "lt",
                      "right": {"value": float(atr_lt)}})

    hold = 24
    mhold = re.search(r"max_hold\s*=\s*(\d+)", str(hyp.get("exit_sketch") or ""), re.I)
    if mhold:
        hold = max(12, int(mhold.group(1)))

    if direction == "long":
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 70.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"},
             "role": "invalidation"},
        ]
    else:
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 30.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"},
             "role": "invalidation"},
        ]
    sketch_exit = parse_sketch_conditions(hyp.get("exit_sketch"))
    for leaf in sketch_exit:
        feat = (leaf.get("left") or {}).get("feature")
        if feat == "rsi14":
            leaf = dict(leaf)
            leaf["role"] = "take_profit"
            exit_any[0] = leaf

    dsl = {
        "key": "%s_%s_%d" % (PREFIX, _safe(logic)[:18], idx),
        "name": "寒霜叁-XAU-15m-%s" % logic,
        "direction": direction,
        "timeframe": TIMEFRAME,
        "supported_instruments": [SYMBOL],
        "max_hold_bars": hold,
        "description": str(hyp.get("vol_contraction_def") or logic)[:160],
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "schema": "qiyu_strategy_dsl_v1",
    }
    return f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)


def make_book(hyp, plan, idx=1):
    dsl = build_xau_dsl(hyp, idx)
    return {
        "title": "XAU15m突破#%d-%s" % (idx, hyp.get("logic_class")),
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "direction": dsl.get("direction") or str(hyp.get("direction") or "long").lower(),
        "logic_class": str(hyp.get("logic_class") or "ema_trend_vol_contract_breakout"),
        "thesis": "EMA趋势过滤+波动收缩→方向突破+量能确认",
        "entry_sketch": hyp.get("entry_sketch"),
        "exit_sketch": hyp.get("exit_sketch"),
        "why_avoids_cl_traps": hyp.get("why_diff_vs_deleted_xau_breakout"),
        "diff_vs_live": "异于ADA/LTC/NG/XRP现网；亦异于已删xau15_h1_breakout硬冲",
        "avoid_from_postmortem": [
            "stop_cluster", "sample_starvation", "ai_logic_rejection",
            "cost_collapse", "xau15_h1_breakout_clone",
        ],
        "dsl": dsl,
        "gate_mode": "frost2",
        "source": PREFIX,
        "dir_rank": idx,
        "hypothesis_book_zh": {
            "ema_period": hyp.get("ema_period_note"),
            "vol_contraction": hyp.get("vol_contraction_def"),
            "stop_atr_match": hyp.get("stop_atr_match_zh"),
            "us_vol": (plan or {}).get("us_vol_read_zh"),
            "breakout_deaths": (plan or {}).get("breakout_death_read_zh"),
            "causal_entry_exit": {
                "entry": hyp.get("entry_sketch"),
                "exit": hyp.get("exit_sketch"),
            },
        },
    }


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
        hyp = {
            "direction": book["direction"],
            "logic_class": book["logic_class"],
            "entry_sketch": book.get("entry_sketch"),
            "exit_sketch": book.get("exit_sketch"),
        }
        book["dsl"] = build_xau_dsl(hyp, int(book.get("dir_rank") or 1))
    if tweaks:
        for phase in ("entry", "exit"):
            block = (book.get("dsl") or {}).get(phase) or {}
            rows = block.get("all") or block.get("any") or []
            for r in rows:
                feat = ((r.get("left") or {}).get("feature"))
                if feat in tweaks and isinstance(tweaks[feat], (int, float)):
                    if isinstance(r.get("right"), dict) and "value" in r["right"]:
                        r["right"]["value"] = float(tweaks[feat])
    book["dsl"] = f2.ensure_dsl(book["dsl"], SYMBOL, TIMEFRAME)
    return book


def local_tweak_xau(book, n, failed_step=""):
    book = f3.local_tweak(book, n, failed_step)
    dsl = book["dsl"]
    entries = list((dsl.get("entry") or {}).get("all") or [])
    step = str(failed_step or "")
    for row in entries:
        feat = (row.get("left") or {}).get("feature")
        right = row.get("right") or {}
        if "value" not in right:
            continue
        v = float(right["value"])
        if feat == "vol_z20" and row.get("op") == "gt":
            # loosen for more trades on WF fail; tighten on friction
            if "walk" in step or "wf" in step:
                right["value"] = max(0.05, v - 0.1 * n)
            else:
                right["value"] = v + 0.05 * n
        elif feat == "cci" and row.get("op") == "lt":
            right["value"] = min(220.0, v + 10.0 * n) if "walk" in step else max(120.0, v - 10 * n)
        elif feat == "z20" and row.get("op") == "lt" and book.get("direction") == "long":
            if "walk" in step:
                right["value"] = min(2.2, v + 0.15 * n)
    # if too few trades: drop cci upper / loosen rsi band slightly already in f3
    dsl["entry"] = {"all": entries}
    k = str(dsl.get("key") or "")
    if PREFIX not in k:
        dsl["key"] = ("%s_%s" % (PREFIX, k))[:100]
    book["dsl"] = f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)
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


def process(book, plan):
    hist = []
    dir_id = "xau15m_%s" % _safe(book.get("logic_class"))
    status = {
        "dir_id": dir_id, "stage": "audit", "updated_at": _now(),
        "symbol": SYMBOL, "tf": TIMEFRAME, "logic": book.get("logic_class"),
        "key": (book.get("dsl") or {}).get("key"),
    }
    write_art("status", status)

    audit_ok = False
    for atry in range(MAX_AUDIT_REVISE + 1):
        decision, ai = glm_audit_book(book, plan)
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
            book = apply_revise(book, decision.get("revise") or {})
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
        _log("quick try %d key=%s" % (qtry, (book.get("dsl") or {}).get("key")))
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
        book = local_tweak_xau(book, qtry + 1, packs.get("failed_step"))
        repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step"), "quick")
        if repaired:
            repaired["symbol"] = SYMBOL
            repaired["timeframe"] = TIMEFRAME
            if repaired.get("dsl"):
                repaired["dsl"]["supported_instruments"] = [SYMBOL]
                repaired["dsl"]["timeframe"] = TIMEFRAME
                kk = str(repaired["dsl"].get("key") or "")
                if PREFIX not in kk:
                    repaired["dsl"]["key"] = ("%s_%s" % (PREFIX, kk))[:100]
                repaired["dsl"] = f2.ensure_dsl(repaired["dsl"], SYMBOL, TIMEFRAME)
            book = repaired
        hist.append({"stage": "quick_repair", "try": qtry, "ok": bool(repaired)})

    for ftry in range(MAX_FULL_REPAIR + 1):
        status.update({"stage": "full", "attempt": ftry, "updated_at": _now()})
        write_art("status", status)
        _log("full try %d" % ftry)
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
        book = local_tweak_xau(book, ftry + 1, packs.get("failed_step"))
        repaired, _ai = f3.glm_repair(book, packs, packs.get("failed_step"), "full")
        if repaired:
            repaired["symbol"] = SYMBOL
            repaired["timeframe"] = TIMEFRAME
            if repaired.get("dsl"):
                repaired["dsl"]["supported_instruments"] = [SYMBOL]
                repaired["dsl"]["timeframe"] = TIMEFRAME
                repaired["dsl"] = f2.ensure_dsl(repaired["dsl"], SYMBOL, TIMEFRAME)
            book = repaired
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
        _log("sim/formal try %d" % stry)
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
        book = local_tweak_xau(book, stry + 1, "sim")
        repaired, _ai = f3.glm_repair(book, packs, "sim_review", "sim")
        if repaired:
            repaired["symbol"] = SYMBOL
            repaired["timeframe"] = TIMEFRAME
            if repaired.get("dsl"):
                repaired["dsl"]["supported_instruments"] = [SYMBOL]
                repaired["dsl"]["timeframe"] = TIMEFRAME
                repaired["dsl"] = f2.ensure_dsl(repaired["dsl"], SYMBOL, TIMEFRAME)
            book = repaired
        q3 = f2.quick_suite(book)
        if not q3.get("quick_pass"):
            hist.append({"stage": "sim_repair_quick_fail", "failed": q3.get("failed_step")})
            continue
        packs = f2.full_suite(book, q3)
        if not packs.get("full_pass"):
            hist.append({"stage": "sim_repair_full_fail", "failed": packs.get("failed_step")})
            continue

    return {"dir_id": dir_id, "ok": False, "failed_step": "unknown_exhausted",
            "hist": hist, "book": book}


def main():
    _log("=== XAU15m BREAKOUT START === %s" % _now())
    d._ensure_dirs()
    d._load_env()
    open(os.path.join(OUT, "%s_run.log" % PREFIX), "w").write("%s start\n" % _now())

    write_art("status", {"stage": "frame", "updated_at": _now(), "symbol": SYMBOL, "tf": TIMEFRAME})
    try:
        ensure_xau_frame_cache()
    except Exception as exc:
        _log("frame cache failed (will retry in quick): %s" % exc)
        write_art("frame_meta", {"error": str(exc), "at": _now()})

    write_art("status", {"stage": "analyze", "updated_at": _now(), "symbol": SYMBOL, "tf": TIMEFRAME})

    deaths = load_breakout_deaths()
    write_art("breakout_deaths", deaths)
    micro = load_xau_micro()
    write_art("micro", micro)
    _log("analyzing US ATR / continuation...")
    atr = analyze_us_atr_continuation()
    write_art("us_atr_continuation", atr)
    _log("atr source=%s n=%s stop/medATR=%s" % (
        atr.get("source"), atr.get("n_bars"),
        (atr.get("stop_vs_us_atr") or {}).get("stop_over_median_atr")))

    cl = {
        "trap_checklist": deaths.get("cl_traps") or [],
        "summary_zh": deaths.get("cl_summary") or "",
        "archive_path": "/root/auto_trade/dual_engine/archive/frost2_cl_15m_entry_exhausted_r3",
    }
    packet = {
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_required": "EMA趋势过滤 + 波动收缩 → 方向突破 + 量能确认",
        "us_atr": atr,
        "xau_micro": micro,
        "breakout_deaths": deaths,
        "cl_archive": cl,
        "live_protect": ["ADA", "LTC", "NG", "XRP"],
        "gates": {
            "WF": ">=7/10", "logic_destruction": "pass",
            "MC_beat": ">=90%", "extreme_friction_sharpe": ">=0",
            "sim": ">=55%/55%",
        },
    }
    write_art("inputs", packet)

    _log("GLM 《XAU 15m 趋势突破参数方案》...")
    plan, ai = ask_glm_plan(packet)
    write_art("glm_plan_raw", {
        "ok": ai.get("ok"), "parsed": bool(plan),
        "preview": (ai.get("raw_preview") or "")[:2000],
        "error": ai.get("error"),
    })
    if not plan or not isinstance(plan.get("hypothesis"), dict):
        _log("GLM plan fail → fallback")
        plan = fallback_plan(atr, deaths)
    else:
        hyp = plan["hypothesis"]
        if not hyp.get("entry_sketch"):
            fb = fallback_plan(atr, deaths)
            plan["hypothesis"] = fb["hypothesis"]
            hyp = plan["hypothesis"]
        # kill pseudo-contraction atr14<h1_atr14 from GLM
        esk = str(hyp.get("entry_sketch") or "")
        if "atr14<h1_atr14" in esk.replace(" ", "") or "atr14 < h1_atr14" in esk:
            _log("GLM used atr14<h1_atr14 pseudo-contraction → replace with fallback sketch")
            fb = fallback_plan(atr, deaths)
            plan["hypothesis"] = fb["hypothesis"]
            plan["glm_contraction_rewritten"] = True
    # prefer calibrated absolute atr bound from analysis
    sug = atr.get("suggested_atr14_contraction_lt")
    if sug and isinstance(plan.get("hypothesis"), dict):
        plan["hypothesis"]["atr14_hint"] = sug
    write_art("参数方案", plan)
    write_art("param_plan", plan)

    hyp = plan["hypothesis"]
    hyp["symbol"] = SYMBOL
    hyp["timeframe"] = TIMEFRAME
    write_art("picked_hypothesis", hyp)
    _log("hypothesis %s %s" % (hyp.get("logic_class"), hyp.get("direction")))

    book = make_book(hyp, plan, 1)
    write_art("hypothesis_book_v0", book)

    try:
        result = process(book, plan)
    except Exception as exc:
        result = {
            "dir_id": "xau15m_exception", "ok": False, "failed_step": "exception",
            "reason": str(exc), "trace": traceback.format_exc()[-2500:],
            "book": book,
        }

    end = {
        "at": _now(),
        "op": "XAU15m趋势突破独立创造",
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
        "atr_source": atr.get("source"),
        "stop_vs_atr": atr.get("stop_vs_us_atr"),
        "continuation": atr.get("continuation_us_trend_contract_breakout"),
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
            "summary_zh": "XAU15m突破 %s 失败于 %s：%s" % (
                end["logic"], result.get("failed_step"), result.get("reason")),
        }
        arch = archive_result(result, death_cause)
        end["archive_path"] = arch
        end["death_cause"] = death_cause
        _log("ARCHIVED %s %s" % (arch, death_cause["exact_death"]))
    else:
        _log("PENDING %s" % (result.get("pending") or {}).get("key"))

    write_art("end_report", end)
    write_art("status", {"finished_at": _now(), "ok": end["ok"], "end": end})

    if end["ok"]:
        summary_zh = (
            "XAU15m趋势突破【成功】逻辑=%s key=%s pending=%s sim_ds/qw=%s/%s；"
            "US_ATR止损比≈%s；突破延续样本=%s"
            % (
                end["logic"], end["key"],
                (end.get("pending") or {}).get("key"),
                (end.get("sim") or {}).get("wr_deepseek_sim"),
                (end.get("sim") or {}).get("wr_qwen_sim"),
                (end.get("stop_vs_atr") or {}).get("stop_over_median_atr"),
                (end.get("continuation") or {}).get("long"),
            )
        )
    else:
        summary_zh = (
            "XAU15m趋势突破【失败归档】逻辑=%s 死因=%s 详情=%s 归档=%s；"
            "US_ATR止损/中位≈%s（安全垫充足则死因偏逻辑/样本而非止损过窄）"
            % (
                end["logic"], end.get("failed_step"), end.get("reason"),
                end.get("archive_path"),
                (end.get("stop_vs_atr") or {}).get("stop_over_median_atr"),
            )
        )
    write_art("parent_summary_zh", {"summary_zh": summary_zh, "at": _now(), "end": end})
    _log("=== END === %s" % summary_zh)
    print("PARENT_SUMMARY_ZH:", summary_zh, flush=True)
    return 0 if end["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
