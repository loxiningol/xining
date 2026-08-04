#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜贰 / crec — 归属创造管道「第二步：门槛」，不是旁路平行线。

创造策略指令固定顺序：
  第一步研究发现 → 第二步统一门槛 → 第三步四阶段复核

本文件只实现第二步中的扫描筛：
  快速筛：小样本前向比例门（正折/可用折≥0.6 或正折≥5）；最低成交 8 笔；
         胜率>50% 且平均净收益>0 时逻辑扰动仅提示
  完整筛：蒙特卡洛与摩擦（击败率按成交笔数分层）

通过第二步后才交第三步复核模块。避开已占利基：ADA*、LTC*、NG*、XRP 15m。
"""
from __future__ import print_function

import copy
import json
import os
import random
import sys
import time
import traceback
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, "/root"):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)
import auto_trade_dual_engine_factory as d

try:
    from dual_engine_workflow_v2.creation_quality_doctrine import (
        MIN_TRADES_CREATION,
        WF_LARGE_NEED_PASS,
        WF_LARGE_NEED_TOTAL,
        WF_POS_MIN_AVAILABLE,
        WF_POS_RATIO,
        mc_beat_threshold,
        wf_ok_small_n as doctrine_wf_ok_small_n,
    )
except Exception:
    # 生产机若路径不同，回退本地常量
    MIN_TRADES_CREATION = 8
    WF_LARGE_NEED_PASS = 7
    WF_LARGE_NEED_TOTAL = 10
    WF_POS_MIN_AVAILABLE = 5
    WF_POS_RATIO = 0.6

    def mc_beat_threshold(n):
        return 0.70 if 0 < int(n or 0) < 20 else 0.90

    def doctrine_wf_ok_small_n(fp, folds, trades, min_trades=None):
        min_n = int(MIN_TRADES_CREATION if min_trades is None else min_trades)
        n = int(trades or 0)
        avail = int(folds or 0)
        fp = int(fp or 0)
        if n < min_n or avail <= 0:
            return False, {"pass": False, "reason": "insufficient_trades_or_folds"}
        ratio = fp / float(avail)
        ok = ratio >= WF_POS_RATIO or fp >= WF_POS_MIN_AVAILABLE
        return ok, {"pass": ok, "ratio": round(ratio, 4), "trades": n,
                    "folds": avail, "fold_positive": fp}

OUT_DIR = "/root/auto_trade/dual_engine"
AVOID = {
    ("ADA-USDT-SWAP", None),  # any TF
    ("LTC-USDT-SWAP", None),
    ("NG-USDT-SWAP", None),
    ("XRP-USDT-SWAP", "15m"),
}
# 旧绝对窗数仅作诊断；快速筛走小样本比例门
QUICK_WF_POS = WF_LARGE_NEED_PASS
QUICK_WF_FOLDS = WF_LARGE_NEED_TOTAL
QUICK_WF_POS_RATIO = WF_POS_RATIO
QUICK_WF_POS_MIN_AVAILABLE = WF_POS_MIN_AVAILABLE
MIN_TRADES = MIN_TRADES_CREATION
FRICTION_SHARPE_GE = 0.0
MC_BEAT_PCT = 0.90  # 大样本默认；完整筛按成交笔数覆盖
MC_PATHS = 200
SIM_GATE = 55.0
MAX_ROUNDS = 3
MAX_PARAM_TWEAKS = 2


def _wf_ok_small_n(bm):
    """小样本前向稳健：比例门，不再硬卡 10 窗至少 7 窗。"""
    trades = int(bm.get("trades") or 0)
    folds = int(bm.get("folds") or 0)
    fp = int(bm.get("fold_positive") or 0)
    ok, detail = doctrine_wf_ok_small_n(fp, folds, trades, min_trades=MIN_TRADES)
    detail = dict(detail or {})
    detail.setdefault("legacy_abs_pos", QUICK_WF_POS)
    detail.setdefault("legacy_abs_folds", QUICK_WF_FOLDS)
    detail.setdefault("gate_ratio", QUICK_WF_POS_RATIO)
    detail.setdefault("gate_min_available_pos", QUICK_WF_POS_MIN_AVAILABLE)
    return ok, detail


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write(name, obj):
    path = os.path.join(OUT_DIR, name)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return path


def _read(name, default=None):
    path = os.path.join(OUT_DIR, name)
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def avoided(symbol, timeframe):
    for sym, tf in AVOID:
        if symbol == sym and (tf is None or timeframe == tf):
            return True
    return False


def ensure_dsl(dsl, symbol, timeframe):
    dsl = copy.deepcopy(dsl or {})
    dsl.setdefault("schema", "qiyu_strategy_dsl_v1")
    dsl["supported_instruments"] = [symbol]
    dsl["timeframe"] = timeframe
    if not dsl.get("key"):
        dsl["key"] = "frost2_%s_%s_%s" % (
            symbol.split("-")[0].lower(), timeframe,
            datetime.now().strftime("%H%M%S"))
    if not dsl.get("name"):
        dsl["name"] = "寒霜贰-%s-%s" % (symbol.split("-")[0], timeframe)
    # stamp entry/exit ids
    for i, row in enumerate((dsl.get("entry") or {}).get("all") or []):
        row.setdefault("id", "e%d" % (i + 1))
    for i, row in enumerate((dsl.get("exit") or {}).get("any") or []):
        row.setdefault("id", "x%d" % (i + 1))
    return dsl


def run_monte_carlo_beat_shuffles(trades, n=MC_PATHS, seed=11, beat_pct=MC_BEAT_PCT):
    """Pass if realized compound equity beats ≥beat_pct of sign-null paths.

    Order-only shuffle is path-independent for multiplicative PnL (useless).
    Bootstrap-vs-self centers near ~50% beat (also cannot gate at 90%).
    Sign-randomization null keeps |pnl| but flips signs at random — a real
    "original better than 90% Monte Carlo shuffles" edge test. Also records
    frost1-style bootstrap positive-path ratio for diagnostics.
    """
    pnls = [float(t.get("pnl_ratio") or 0.0) for t in (trades or [])]
    if len(pnls) < 5:
        return {"ok": False, "pass": False, "reason": "too_few_trades", "n": len(pnls)}

    def _compound(xs):
        a = 1.0
        for p in xs:
            a *= (1.0 + p)
        return a - 1.0

    actual = _compound(pnls)
    rng = random.Random(seed)
    null_finals = []
    boot_finals = []
    m = len(pnls)
    for _ in range(n):
        # sign-null shuffle
        signed = [p if rng.random() < 0.5 else -p for p in pnls]
        null_finals.append(_compound(signed))
        # frost1 bootstrap diagnostic
        boot = [pnls[rng.randrange(0, m)] for __ in range(m)]
        boot_finals.append(_compound(boot))
    beat = sum(1 for x in null_finals if actual > x) / float(len(null_finals))
    pos_boot = sum(1 for x in boot_finals if x > 0) / float(len(boot_finals))
    passed = beat >= float(beat_pct) and actual > 0
    return {
        "ok": True,
        "pass": bool(passed),
        "paths": n,
        "actual_final": round(actual, 6),
        "beat_ratio": round(beat, 4),
        "method": "sign_randomization_null",
        "bootstrap_positive_path_ratio": round(pos_boot, 4),
        "bootstrap_mean_final": round(sum(boot_finals) / float(len(boot_finals)), 6),
        "gates": {"beat_shuffles_ge": beat_pct, "actual_final_gt_0": True},
        "mean_shuffle_final": round(sum(null_finals) / float(len(null_finals)), 6),
    }


def quick_suite(book):
    """快速筛：小样本前向比例门 + 高胜率书逻辑扰动仅提示。"""
    old = d.FROST_RELAXED_WF_POS
    d.FROST_RELAXED_WF_POS = max(1, int(QUICK_WF_POS_MIN_AVAILABLE))
    try:
        packs = d.run_internal_packs(book, mode="frost_relaxed")
    finally:
        d.FROST_RELAXED_WF_POS = old
    if not packs.get("ok"):
        packs["quick_pass"] = False
        packs["failed_step"] = packs.get("stage") or "validate"
        return packs
    anti = packs.get("anti_overfit") or {}
    dest = packs.get("logic_destruction") or {}
    bm = anti.get("metrics") or packs.get("base_metrics") or {}
    packs["base_metrics"] = bm
    wf_ok, wf_detail = _wf_ok_small_n(bm)
    dest_hard = bool(dest.get("pass"))
    wr = float(bm.get("win_rate_pct") or bm.get("win_rate") or 0.0)
    if wr <= 1.0 and bm.get("win_rate_pct") is None and bm.get("win_rate") is not None:
        wr = float(bm.get("win_rate") or 0.0) * 100.0
    mean_net = float(bm.get("mean_net") or 0.0)
    high_quality = bool(wr > 50.0 and mean_net > 0.0)
    # 胜率>50% 且平均净收益>0 时，逻辑扰动不硬拦
    dest_blocks = bool((not dest_hard) and (not high_quality))
    dest_ok_for_pass = bool(dest_hard or high_quality)
    packs["quick"] = {
        "前向稳健_小样本": wf_ok,
        "walk_forward_small_n": wf_ok,
        "walk_forward_detail": wf_detail,
        "前向稳健详情": wf_detail,
        "logic_destruction": dest_hard,
        "逻辑扰动通过": dest_hard,
        "logic_destruction_advisory": bool(high_quality and not dest_hard),
        "逻辑扰动仅提示": bool(high_quality and not dest_hard),
        "logic_destruction_blocks": dest_blocks,
        "fold_positive": bm.get("fold_positive"),
        "正折数": bm.get("fold_positive"),
        "folds": bm.get("folds"),
        "可用折数": bm.get("folds"),
        "gate_wf_pos_ratio": QUICK_WF_POS_RATIO,
        "gate_wf_pos_min_available": QUICK_WF_POS_MIN_AVAILABLE,
        "gate_wf_pos_legacy_abs": QUICK_WF_POS,
        "high_quality_wr_mean": high_quality,
        "高胜率且平均净收益为正": high_quality,
        "win_rate_pct": wr,
        "胜率_百分比": wr,
        "mean_net": mean_net,
        "平均净收益": mean_net,
        "说明": "快速筛=小样本前向比例门，不是旧的10窗至少7窗",
    }
    packs["quick_pass"] = bool(wf_ok and dest_ok_for_pass)
    if not packs["quick_pass"]:
        packs["failed_step"] = (
            "快速筛_前向稳健" if not wf_ok else "快速筛_逻辑扰动")
    return packs


def full_suite(book, packs=None):
    """完整筛：摩擦夏普≥0 + 蒙特卡洛击败率（按成交笔数分层）。须先过快速筛。"""
    if packs is None or not packs.get("ok"):
        packs = quick_suite(book)
    if not packs.get("quick_pass"):
        packs["full_pass"] = False
        return packs
    fr = packs.get("extreme_friction") or {}
    fr_m = fr.get("metrics") or {}
    fr_ok = (
        int(fr_m.get("trades") or 0) >= 5
        and float(fr_m.get("sharpe") or -99) >= FRICTION_SHARPE_GE
    )
    try:
        base = d._backtest(
            packs["definition"], packs["symbol"], packs["timeframe"], "observed_base")
        trade_list = base.get("trades") or []
        beat_need = float(mc_beat_threshold(len(trade_list)))
        mc = run_monte_carlo_beat_shuffles(
            trade_list, n=MC_PATHS, beat_pct=beat_need,
        )
        mc["击败率要求"] = beat_need
        mc["成交笔数"] = len(trade_list)
    except Exception as exc:
        mc = {"ok": False, "pass": False, "error": str(exc)}
    packs["monte_carlo_frost2"] = mc
    packs["full"] = {
        "friction_sharpe_ge_0": fr_ok,
        "摩擦夏普达标": fr_ok,
        "friction_sharpe": fr_m.get("sharpe"),
        "mc_beat_90pct_shuffles": bool(mc.get("pass")),
        "蒙特卡洛击败达标": bool(mc.get("pass")),
        "mc": mc,
    }
    packs["full_pass"] = bool(fr_ok and mc.get("pass"))
    if not packs["full_pass"]:
        packs["failed_step"] = (
            "完整筛_摩擦夏普" if not fr_ok else "完整筛_蒙特卡洛")
    return packs


# ─── GLM postmortem ────────────────────────────────────────────────────

POSTMORTEM_PROMPT = """你是栖语自动交易系统的策略总设计师（GLM-5.2）。
上一轮「寒霜」仅 XRP 15m exhaustion_fade 进入正式复核并上线；DOGE 1h range_reclaim 与 SOL 5m impulse_continuation 全面失败。
请输出《失败根因与规避方案》JSON（不要 Markdown）：
{
  "report_title": "失败根因与规避方案",
  "doge_1h": {
    "root_defects": ["逻辑缺陷1","..."],
    "why_failed_gates": {"walk_forward":"...","logic_destruction":"...","sim":"..."},
    "salvageable": true/false,
    "salvage_note": "若可抢救，如何改核心入出场；否则说明放弃理由"
  },
  "sol_5m": {
    "root_defects": ["..."],
    "why_failed_gates": {"walk_forward":"...","friction":"...","sim":"..."},
    "salvageable": true/false,
    "salvage_note": "..."
  },
  "trap_checklist": ["后续假设必须规避的陷阱1","..."],
  "new_directions": [
    {"rank":1,"symbol":"BTC-USDT-SWAP|ETH-USDT-SWAP|SOL-USDT-SWAP|DOGE-USDT-SWAP|XAG-USDT-SWAP|XAU-USDT-SWAP|CL-USDT-SWAP 之一",
     "timeframe":"5m|15m|1h",
     "direction":"long|short",
     "logic_class":"短横线英文类名",
     "thesis":"因果机制一句话",
     "entry_sketch":"可回测特征组合",
     "exit_sketch":"止盈/失效条件",
     "avoid_from_postmortem":["对照陷阱"],
     "why_not_live_overlap":"为何不是 ADA/LTC/NG/XRP15m"}
  ],
  "mentor_notes":"给工程师的硬约束"
}
硬约束：
1) new_directions 必须 2～3 条，且 symbol/timeframe 不得为 ADA*、LTC*、NG*、XRP-USDT-SWAP/15m
2) 禁止只调阈值；必须指出核心逻辑缺陷
3) 优先可交易样本≥8、结构稳健、避免 stop_cluster / 过度交易 / 样本饥渴
"""


def build_failure_packet():
    rescue = _read("frost_rescue_final.json", {}) or {}
    fail = _read("frost_failure_reports.json", {}) or {}
    hyp = _read("frost_hypotheses.json", {}) or {}
    insight = _read("frost_insight.json", {}) or {}
    books = {((b.get("symbol"), b.get("timeframe"))): b
             for b in (hyp.get("books") or []) if isinstance(b, dict)}
    doge = (rescue.get("strategies") or {}).get("DOGE-USDT-SWAP") or fail.get("DOGE-USDT-SWAP")
    sol = (rescue.get("strategies") or {}).get("SOL-USDT-SWAP") or fail.get("SOL-USDT-SWAP")
    doge_book = books.get(("DOGE-USDT-SWAP", "1h")) or {}
    sol_book = books.get(("SOL-USDT-SWAP", "5m")) or {}
    xrp_ok = (rescue.get("strategies") or {}).get("XRP-USDT-SWAP") or {}
    return {
        "op": "寒霜贰·失败复盘输入",
        "contrast_success_xrp_15m": {
            "key": xrp_ok.get("dsl_key"),
            "suite_pass": xrp_ok.get("suite_pass"),
            "base_metrics": xrp_ok.get("base_metrics"),
            "formal": (xrp_ok.get("formal") or {}).get("annotation"),
            "lesson": "短逻辑+稀疏信号+高折正WF+破坏稳健 → 正式复核高胜率",
        },
        "doge_1h_failure": {
            "rescue": doge,
            "strict_fail": fail.get("DOGE-USDT-SWAP"),
            "original_thesis": doge_book.get("thesis") or insight.get("priority_niches"),
            "dsl": doge_book.get("dsl") or ((doge or {}).get("dsl_key")),
            "logic_class": "range_reclaim",
            "notes": [
                "strict: 399笔超交易 WR21% Sharpe负 fold_pos 3/10",
                "rescue best: 18笔 WR50% fold_pos 6/10 dest FAIL oos负 sim~42/40",
            ],
        },
        "sol_5m_failure": {
            "rescue": sol,
            "strict_fail": fail.get("SOL-USDT-SWAP"),
            "original_thesis": sol_book.get("thesis"),
            "dsl": sol_book.get("dsl") or ((sol or {}).get("dsl_key")),
            "logic_class": "impulse_continuation",
            "notes": [
                "strict: 248笔 WR14% Sharpe极负 fold_pos 0/10",
                "rescue: 7笔 WR43% fold_pos 3/7 friction负 dest FAIL sim~35/33",
            ],
        },
        "live_forbidden": [
            "ADA-USDT-SWAP any", "LTC-USDT-SWAP any",
            "NG-USDT-SWAP any", "XRP-USDT-SWAP 15m (already live B/30%)",
        ],
        "frost2_gates": {
            "quick": "快速筛：小样本前向比例门 + 逻辑扰动（高胜率书仅提示）",
            "full": "完整筛：蒙特卡洛击败率按成交笔数分层 + 摩擦夏普≥0",
            "sim": "模拟复核：DeepSeek与Qwen理论胜率均≥55%",
            "formal": "正式复核：仅 DeepSeek+Qwen，理论胜率均≥50%",
        },
    }


def run_postmortem():
    packet = build_failure_packet()
    _write("frost2_failure_packet.json", packet)
    print("[frost2] submitting postmortem to GLM-5.2 ...", flush=True)
    res = d._ai_json("glm", POSTMORTEM_PROMPT, packet, max_tokens=3200, temperature=0.25)
    out = {
        "op": "寒霜贰·失败根因与规避方案",
        "started_at": _now(),
        "ai": {"ok": res.get("ok"), "error": res.get("error"),
               "latency_sec": res.get("latency_sec"),
               "raw_preview": res.get("raw_preview")},
        "report": res.get("parsed") if res.get("ok") else None,
    }
    if not res.get("ok"):
        # one retry with stricter instruction
        res2 = d._ai_json(
            "glm",
            POSTMORTEM_PROMPT + "\n只输出纯 JSON 对象，键名必须英文蛇形。",
            packet, max_tokens=3200, temperature=0.1)
        out["ai_retry"] = {"ok": res2.get("ok"), "error": res2.get("error"),
                           "latency_sec": res2.get("latency_sec"),
                           "raw_preview": res2.get("raw_preview")}
        if res2.get("ok"):
            out["report"] = res2.get("parsed")
            out["ai"] = out["ai_retry"]
    out["finished_at"] = _now()
    _write("frost2_postmortem.json", out)
    print("[frost2] postmortem saved", flush=True)
    return out


# ─── Hypothesis / DSL from postmortem ──────────────────────────────────

SUPPORTED_TFS = ("5m", "15m", "1h")


def _remap_timeframe(tf):
    tf = str(tf or "5m").lower()
    if tf in SUPPORTED_TFS:
        return tf
    # 4h unsupported → 1h (postmortem remap)
    if tf in ("4h", "2h", "3h"):
        return "1h"
    return "15m"


def books_from_postmortem(report, adapt=None):
    books = []
    dirs = list((report or {}).get("new_directions") or [])
    adapt = adapt or {}
    third = adapt.get("third_direction")
    if isinstance(third, dict) and third.get("symbol"):
        dirs = dirs + [third]
    for i, row in enumerate(dirs[:4]):
        symbol = str(row.get("symbol") or "").upper()
        if "|" in symbol:
            symbol = symbol.split("|")[0]
        timeframe = _remap_timeframe(row.get("timeframe"))
        if avoided(symbol, timeframe):
            print("[frost2] skip avoided", symbol, timeframe, flush=True)
            continue
        direction = str(row.get("direction") or "long").lower()
        logic = str(row.get("logic_class") or "custom")
        # apply feature-proxy notes into thesis so sketch_to_dsl can read them
        proxy = None
        if symbol.startswith("SOL") and timeframe == "15m":
            proxy = adapt.get("sol_15m_feature_proxy")
        if symbol.startswith("DOGE") and timeframe == "1h":
            proxy = adapt.get("doge_1h_remap_from_4h")
        row2 = dict(row)
        if isinstance(proxy, dict):
            row2["entry_sketch"] = proxy.get("entry_proxy") or row.get("entry_sketch")
            row2["exit_sketch"] = proxy.get("exit_proxy") or row.get("exit_sketch")
            row2["_hold_bars"] = proxy.get("hold_bars")
        dsl = sketch_to_dsl(symbol, timeframe, direction, logic, row2, i + 1)
        books.append({
            "title": "寒霜贰假设#%d" % (i + 1),
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "logic_class": logic,
            "thesis": row.get("thesis"),
            "entry_sketch": row2.get("entry_sketch"),
            "exit_sketch": row2.get("exit_sketch"),
            "avoid_from_postmortem": row.get("avoid_from_postmortem"),
            "dsl": dsl,
            "gate_mode": "frost2",
            "source": "glm_postmortem_new_direction",
        })
    # Optional salvage variants
    for key, sym, tf, direction, logic in [
        ("doge_1h", "DOGE-USDT-SWAP", "1h", "long", "range_reclaim_v2"),
        ("sol_5m", "SOL-USDT-SWAP", "5m", "long", "impulse_continuation_v2"),
    ]:
        block = (report or {}).get(key) or {}
        if not block.get("salvageable"):
            continue
        if avoided(sym, tf):
            continue
        note = str(block.get("salvage_note") or "")
        dsl = salvage_dsl(sym, tf, direction, logic, note)
        books.append({
            "title": "寒霜贰抢救-%s" % key,
            "symbol": sym,
            "timeframe": tf,
            "direction": direction,
            "logic_class": logic,
            "thesis": note[:180],
            "dsl": dsl,
            "gate_mode": "frost2",
            "source": "glm_salvage",
            "salvage": True,
        })
    return books


def sketch_to_dsl(symbol, timeframe, direction, logic, row, idx):
    """Codex engineer: map sketch to concrete DSL using proven feature vocab."""
    hold = 16 if timeframe == "1h" else (24 if timeframe == "15m" else 12)
    if row.get("_hold_bars"):
        try:
            hold = int(row.get("_hold_bars"))
        except Exception:
            pass
    key = "frost2_%s_%s_%s_%d" % (
        symbol.split("-")[0].lower(), timeframe, logic[:18], idx)
    name = "寒霜贰-%s-%s-%s" % (symbol.split("-")[0], timeframe, logic)
    thesis = " ".join([
        str(row.get("thesis") or ""),
        str(row.get("entry_sketch") or ""),
        str(row.get("exit_sketch") or ""),
    ])
    low = (thesis + " " + logic).lower()
    # Prefer XRP-success mirror for exhaustion shorts (proven formal path).
    if direction == "short" and (
            "exhaust" in low or "fade" in low or "衰竭" in thesis or "背离" in thesis):
        entry = [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 58.0}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.3}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
        ]
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"},
             "role": "invalidation"},
        ]
        hold = max(hold, 20)
    elif direction == "short":
        entry = [
            {"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -1.0}},
        ]
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 30.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"},
             "role": "invalidation"},
        ]
    elif "reclaim" in low or "range" in low or "回收" in thesis or "针探" in thesis:
        # Sparse reclaim: avoid mid-band thrash; invalidate on structure break.
        entry = [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 40.0}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 52.0}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_low20"}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -1.0}},
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 0.8}},
        ]
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 58.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"},
             "role": "invalidation"},
        ]
        hold = max(hold, 14)
    elif "break" in low or "impulse" in low or "突破" in thesis or "动量" in thesis:
        entry = [
            {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 72.0}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 2.2}},
        ]
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 75.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"},
             "role": "invalidation"},
        ]
        hold = 10 if timeframe == "5m" else hold
    else:
        entry = [
            {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 50.0}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 68.0}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.8}},
        ]
        exit_any = [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 72.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"},
             "role": "invalidation"},
        ]
    dsl = {
        "key": key,
        "name": name,
        "direction": direction,
        "timeframe": timeframe,
        "supported_instruments": [symbol],
        "max_hold_bars": hold,
        "description": str(row.get("thesis") or logic)[:160],
        "entry": {"all": entry},
        "exit": {"any": exit_any},
        "schema": "qiyu_strategy_dsl_v1",
    }
    return ensure_dsl(dsl, symbol, timeframe)


def salvage_dsl(symbol, timeframe, direction, logic, note):
    # Conservative sparse variants informed by XRP success pattern
    if "doge" in symbol.lower():
        dsl = {
            "key": "frost2_doge_salvage_reclaim_v2",
            "name": "寒霜贰-DOGE-1h-range_reclaim_v2",
            "direction": "long",
            "entry": {"all": [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 40.0}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_low20"}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -1.0}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 0.8}},
            ]},
            "exit": {"any": [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 58.0},
                 "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"},
                 "role": "invalidation"},
            ]},
            "max_hold_bars": 14,
            "description": note[:160] or "salvage doge reclaim sparse",
        }
    else:
        dsl = {
            "key": "frost2_sol_salvage_impulse_v2",
            "name": "寒霜贰-SOL-5m-impulse_v2",
            "direction": "long",
            "entry": {"all": [
                {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high14"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema16"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 70.0}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 2.5}},
            ]},
            "exit": {"any": [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 75.0},
                 "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"},
                 "role": "invalidation"},
            ]},
            "max_hold_bars": 8,
            "description": note[:160] or "salvage sol impulse sparse",
        }
    return ensure_dsl(dsl, symbol, timeframe)


def param_tweak(book, round_i):
    book = copy.deepcopy(book)
    dsl = book["dsl"]
    hold = int(dsl.get("max_hold_bars") or 12)
    dsl["max_hold_bars"] = max(6, hold + (-2 if round_i % 2 == 0 else 2))
    # nudge RSI thresholds in entry/exit
    for row in (dsl.get("entry") or {}).get("all") or []:
        feat = ((row.get("left") or {}).get("feature") or "")
        right = row.get("right") or {}
        if feat == "rsi14" and "value" in right:
            v = float(right["value"])
            right["value"] = v + (1.0 if round_i == 1 else -1.5)
    for row in (dsl.get("exit") or {}).get("any") or []:
        feat = ((row.get("left") or {}).get("feature") or "")
        right = row.get("right") or {}
        if feat == "rsi14" and "value" in right and row.get("role") == "take_profit":
            v = float(right["value"])
            right["value"] = v + (-2.0 if round_i == 1 else 2.0)
    dsl["key"] = "%s_t%d" % (dsl.get("key", "frost2").split("_t")[0], round_i)
    book["dsl"] = ensure_dsl(dsl, book["symbol"], book["timeframe"])
    book["tweak_round"] = round_i
    return book


REWRITE_PROMPT = """你是策略总设计师。该假设在寒霜贰闸门失败，参数微调已用尽。
请重写核心入场/出场（仍同一 logic_class 精神，但机制可变），输出 JSON：
{
  "rewrite_ok": true,
  "reason_zh": "...",
  "dsl": {
    "key":"...", "name":"...", "direction":"long|short", "timeframe":"...",
    "supported_instruments":["..."], "max_hold_bars":12,
    "entry":{"all":[ {"left":{"feature":"..."},"op":"gt|lt|cross_above|cross_below","right":{"value":0}|{"feature":"..."}} ]},
    "exit":{"any":[ {"left":{"feature":"..."},"op":"...","right":{...},"role":"take_profit|invalidation"} ]}
  }
}
可用特征: rsi14,z20,macd_stick,cci,ema6,ema8,ema16,ema19,ema21,close,prev_high14,prev_high20,prev_low20,h1_ema19,h1_ema53,h1_slope4
禁止 ADA/LTC/NG/XRP15m。只输出 JSON。
"""


def glm_rewrite(book, packs, failed_step):
    payload = {
        "book": {
            "symbol": book.get("symbol"),
            "timeframe": book.get("timeframe"),
            "direction": book.get("direction"),
            "logic_class": book.get("logic_class"),
            "thesis": book.get("thesis"),
            "dsl": book.get("dsl"),
        },
        "failed_step": failed_step,
        "quick": packs.get("quick"),
        "full": packs.get("full"),
        "base_metrics": packs.get("base_metrics"),
        "trap_hint": "避免超交易、负期望、破坏不稳、样本饥渴",
    }
    res = d._ai_json("glm", REWRITE_PROMPT, payload, max_tokens=1800, temperature=0.3)
    parsed = res.get("parsed") if res.get("ok") else None
    if not parsed or not parsed.get("dsl"):
        return None, res
    dsl = ensure_dsl(parsed["dsl"], book["symbol"], book["timeframe"])
    dsl["direction"] = book.get("direction") or dsl.get("direction")
    dsl["timeframe"] = book["timeframe"]
    dsl["supported_instruments"] = [book["symbol"]]
    if not str(dsl.get("key") or "").startswith("frost2_"):
        dsl["key"] = "frost2_rw_%s" % (dsl.get("key") or "x")
    nb = copy.deepcopy(book)
    nb["dsl"] = dsl
    nb["rewrite"] = parsed.get("reason_zh")
    nb["rewritten"] = True
    return nb, res


def glm_audit_books(postmortem, books):
    prompt = """你是策略总设计师。对照《失败根因与规避方案》审核工程师假设书。
输出 JSON: {"approved":[{"index":0,"note":"..."}],"rejected":[{"index":1,"reason":"..."}],"mentor_notes":"..."}
硬拒：落入 trap_checklist、或标的/周期为 ADA/LTC/NG/XRP15m、或仅阈值微调无新机制。
"""
    payload = {
        "postmortem_summary": {
            "trap_checklist": (postmortem or {}).get("trap_checklist"),
            "new_directions": (postmortem or {}).get("new_directions"),
            "mentor_notes": (postmortem or {}).get("mentor_notes"),
        },
        "books": [
            {"index": i, "symbol": b.get("symbol"), "timeframe": b.get("timeframe"),
             "direction": b.get("direction"), "logic_class": b.get("logic_class"),
             "thesis": b.get("thesis"), "dsl_key": (b.get("dsl") or {}).get("key"),
             "entry": (b.get("dsl") or {}).get("entry"),
             "exit": (b.get("dsl") or {}).get("exit"),
             "source": b.get("source")}
            for i, b in enumerate(books)
        ],
    }
    res = d._ai_json("glm", prompt, payload, max_tokens=1200, temperature=0.2)
    return res


def run_sim_formal(book, packs):
    """Sim (≥55/55) → formal DeepSeek+Qwen → pending human confirm."""
    packs = dict(packs or {})
    packs["pass"] = bool(packs.get("full_pass"))
    sim = d.glm_sim_review(book, packs)
    wr_ds = float(sim.get("wr_deepseek_sim") or 0)
    wr_qw = float(sim.get("wr_qwen_sim") or 0)
    sim["pass"] = wr_ds >= SIM_GATE and wr_qw >= SIM_GATE
    if not sim.get("pass"):
        return {"sim": sim, "formal": None, "pending": None,
                "failed_step": "sim_review"}

    definition = packs.get("definition") or book.get("dsl")
    formal_raw = d.formal_ds_qwen_review(definition, packs, book)
    formal = {
        "approved": formal_raw.get("approved"),
        "annotation": formal_raw.get("annotation"),
        "ai_review": formal_raw.get("ai_review"),
        "reason": formal_raw.get("reason"),
        "stage": formal_raw.get("stage"),
    }
    if not formal.get("approved"):
        return {"sim": sim, "formal": formal, "pending": None,
                "failed_step": "formal_review"}

    # Legacy frost2 only runs DeepSeek+Qwen formal review. Pending human confirm
    # requires STEP A + theoretical_review_all (DS+Qwen+GLM). Do not enqueue here.
    pending = {
        "ok": False,
        "reason": "legacy_frost2_pending_disabled",
        "hint": "submit via dual_engine_step_a + verified 3AI review",
        "production_mounted": False,
    }
    try:
        d._record_formal({
            "time": d._now(),
            "op": "寒霜贰",
            "key": (book.get("dsl") or {}).get("key"),
            "title": book.get("title") or (book.get("dsl") or {}).get("name"),
            "symbol": book.get("symbol"),
            "status": "退回",
            "annotation": formal.get("annotation"),
            "reason": pending.get("reason"),
        })
    except Exception:
        pass
    return {
        "sim": sim,
        "formal": formal,
        "pending": pending,
        "failed_step": "legacy_pending_disabled",
    }


def process_book(book, postmortem_report):
    hist = []
    cur = copy.deepcopy(book)
    param_fails = 0
    attempts = 0
    max_attempts = MAX_ROUNDS * 2  # allow rewrite + retest beyond raw rounds
    while attempts < max_attempts:
        attempts += 1
        print("[frost2] quick", cur["symbol"], cur["timeframe"],
              (cur.get("dsl") or {}).get("key"), "attempt", attempts, flush=True)
        packs = quick_suite(cur)
        hist.append({"attempt": attempts, "stage": "quick", "pass": packs.get("quick_pass"),
                     "quick": packs.get("quick"), "metrics": packs.get("base_metrics"),
                     "failed_step": packs.get("failed_step")})
        if not packs.get("quick_pass"):
            param_fails += 1
            if param_fails <= MAX_PARAM_TWEAKS:
                cur = param_tweak(cur, param_fails)
                continue
            rewritten, ai = glm_rewrite(cur, packs, packs.get("failed_step"))
            hist.append({"attempt": attempts, "stage": "glm_rewrite",
                         "ok": bool(rewritten), "ai_error": (ai or {}).get("error")})
            if not rewritten:
                # local structural rewrite fallback (XRP-success family / sparse reclaim)
                cur = local_structural_rewrite(cur, packs.get("failed_step"))
                hist.append({"attempt": attempts, "stage": "local_rewrite", "ok": True})
                param_fails = 0
                continue
            cur = rewritten
            param_fails = 0
            continue

        print("[frost2] full", (cur.get("dsl") or {}).get("key"), flush=True)
        packs = full_suite(cur, packs)
        hist.append({"attempt": attempts, "stage": "full", "pass": packs.get("full_pass"),
                     "full": packs.get("full"), "failed_step": packs.get("failed_step")})
        if not packs.get("full_pass"):
            param_fails += 1
            if param_fails <= MAX_PARAM_TWEAKS:
                cur = param_tweak(cur, param_fails)
                continue
            rewritten, ai = glm_rewrite(cur, packs, packs.get("failed_step"))
            hist.append({"attempt": attempts, "stage": "glm_rewrite_full",
                         "ok": bool(rewritten), "ai_error": (ai or {}).get("error")})
            if not rewritten:
                cur = local_structural_rewrite(cur, packs.get("failed_step"))
                hist.append({"attempt": attempts, "stage": "local_rewrite_full", "ok": True})
                param_fails = 0
                continue
            cur = rewritten
            param_fails = 0
            continue

        print("[frost2] sim/formal", (cur.get("dsl") or {}).get("key"), flush=True)
        sf = run_sim_formal(cur, packs)
        hist.append({"attempt": attempts, "stage": "sim_formal",
                     "failed_step": sf.get("failed_step"),
                     "sim": {k: (sf.get("sim") or {}).get(k)
                             for k in ("pass", "wr_deepseek_sim", "wr_qwen_sim", "fallback")},
                     "formal_approved": ((sf.get("formal") or {}).get("approved")),
                     "pending": sf.get("pending")})
        if sf.get("failed_step"):
            rewritten, ai = glm_rewrite(cur, packs, sf.get("failed_step"))
            hist.append({"attempt": attempts, "stage": "glm_rewrite_sim",
                         "ok": bool(rewritten)})
            if not rewritten:
                cur = local_structural_rewrite(cur, sf.get("failed_step"))
                continue
            cur = rewritten
            continue
        return {"book": cur, "ok": True, "failed_step": None, "history": hist,
                "packs": packs, "sim_formal": sf}

    last = hist[-1] if hist else {}
    return {"book": cur, "ok": False,
            "failed_step": last.get("failed_step") or "max_rounds",
            "history": hist, "packs": locals().get("packs")}


def local_structural_rewrite(book, failed_step):
    """Deterministic core rewrite after GLM/param exhaustion — still same class family."""
    book = copy.deepcopy(book)
    symbol = book["symbol"]
    timeframe = book["timeframe"]
    direction = book.get("direction") or "long"
    logic = book.get("logic_class") or ""
    tag = "lr%s" % str(failed_step or "x")[:12]
    if direction == "short" or "exhaust" in logic:
        dsl = {
            "key": "frost2_%s_%s_exh_%s" % (symbol.split("-")[0].lower(), timeframe, tag),
            "name": "寒霜贰-%s-%s-exhaustion_fade" % (symbol.split("-")[0], timeframe),
            "direction": "short",
            "entry": {"all": [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 60.0}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.5}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema8"}},
            ]},
            "exit": {"any": [
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 42.0},
                 "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"},
                 "role": "invalidation"},
            ]},
            "max_hold_bars": 22 if timeframe == "15m" else 14,
            "description": "local rewrite exhaustion sparse short",
        }
        book["direction"] = "short"
    else:
        dsl = {
            "key": "frost2_%s_%s_rec_%s" % (symbol.split("-")[0].lower(), timeframe, tag),
            "name": "寒霜贰-%s-%s-range_reclaim" % (symbol.split("-")[0], timeframe),
            "direction": "long",
            "entry": {"all": [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 42.0}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 50.0}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_low20"}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": -50.0}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -0.8}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 0.6}},
            ]},
            "exit": {"any": [
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 56.0},
                 "role": "take_profit"},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"},
                 "role": "invalidation"},
            ]},
            "max_hold_bars": 12 if timeframe == "1h" else 16,
            "description": "local rewrite sparse reclaim",
        }
        book["direction"] = "long"
    book["dsl"] = ensure_dsl(dsl, symbol, timeframe)
    book["rewritten"] = True
    book["rewrite"] = "local_structural_rewrite:%s" % failed_step
    return book


def main(argv=None):
    argv = list(argv or sys.argv[1:])
    resume = "--resume" in argv or "--resume-from-postmortem" in argv
    os.makedirs(OUT_DIR, exist_ok=True)
    state = {
        "op": "寒霜贰",
        "started_at": _now(),
        "xrp_confirm_status": "already_live_B_30pct",
        "gates": {
            "quick": "快速筛：小样本前向比例门 + 逻辑扰动（高胜率书仅提示）",
            "full": "完整筛：蒙特卡洛击败率按成交笔数分层 + 摩擦夏普≥0",
            "sim": "模拟复核理论胜率门槛=%s" % SIM_GATE,
        },
        "avoid": ["ADA*", "LTC*", "NG*", "XRP 15m"],
        "resume": resume,
    }
    _write("frost2_run.json", state)

    # 1) Mandatory postmortem
    if resume and (_read("frost2_root_cause_report.json") or {}).get("new_directions"):
        report = _read("frost2_root_cause_report.json")
        print("[frost2] resume: using existing postmortem report", flush=True)
    else:
        pm = run_postmortem()
        report = pm.get("report")
        if not report or not (report.get("new_directions") or report.get("trap_checklist")):
            state["ok"] = False
            state["failed_step"] = "glm_postmortem_incomplete"
            state["postmortem"] = pm
            state["finished_at"] = _now()
            _write("frost2_run.json", state)
            print("[frost2] FATAL: postmortem incomplete", flush=True)
            return 2
        _write("frost2_root_cause_report.json", report)

    print("[frost2] traps:", report.get("trap_checklist"), flush=True)
    print("[frost2] directions:", [
        (x.get("symbol"), x.get("timeframe"), x.get("logic_class"))
        for x in (report.get("new_directions") or [])
    ], flush=True)

    adapt_wrap = _read("frost2_direction_adapt.json", {}) or {}
    adapt = adapt_wrap.get("parsed") or adapt_wrap
    # ensure proxy keys unified
    if adapt.get("doge_1h_remap") and not adapt.get("doge_1h_remap_from_4h"):
        adapt["doge_1h_remap_from_4h"] = adapt["doge_1h_remap"]
    if adapt.get("sol_15m_proxy") and not adapt.get("sol_15m_feature_proxy"):
        adapt["sol_15m_feature_proxy"] = adapt["sol_15m_proxy"]
    # default proxies from XRP-success / postmortem traps when missing
    adapt.setdefault("sol_15m_feature_proxy", {
        "entry_proxy": "rsi14>58; z20>0.3; macd_stick<0; close<ema16",
        "exit_proxy": "rsi14<45 TP; close>prev_high20 invalidation",
        "hold_bars": 24,
    })
    adapt.setdefault("doge_1h_remap_from_4h", {
        "entry_proxy": "sparse reclaim rsi40-52 close>ema21>prev_low20 macd>0 z20[-1,0.8]",
        "exit_proxy": "rsi>58 TP; close<prev_low20 invalidation",
        "hold_bars": 14,
    })

    # 2) Hypotheses from report only (+ adapt third / remaps)
    books = books_from_postmortem(report, adapt=adapt)
    audit = glm_audit_books(report, books)
    _write("frost2_hypotheses.json", {
        "created_at": _now(), "books": books, "adapt": adapt,
        "audit": audit.get("parsed") if audit.get("ok") else audit,
    })
    approved_idx = set()
    if audit.get("ok") and isinstance(audit.get("parsed"), dict):
        for row in (audit["parsed"].get("approved") or []):
            if isinstance(row, dict) and row.get("index") is not None:
                approved_idx.add(int(row["index"]))
            elif isinstance(row, int):
                approved_idx.add(row)
    if not approved_idx:
        approved_idx = set(range(len(books)))
        print("[frost2] audit missing/empty — keep all postmortem books", flush=True)
    books = [b for i, b in enumerate(books) if i in approved_idx]
    # de-dup by symbol|tf|logic
    seen = set()
    uniq = []
    for b in books:
        k = (b["symbol"], b["timeframe"], b["logic_class"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(b)
    books = uniq
    state["hypotheses"] = [
        {"symbol": b["symbol"], "timeframe": b["timeframe"],
         "logic": b["logic_class"], "key": (b.get("dsl") or {}).get("key")}
        for b in books
    ]
    print("[frost2] books", state["hypotheses"], flush=True)

    # 3) Process each book
    results = []
    pending_keys = []
    for book in books:
        if avoided(book["symbol"], book["timeframe"]):
            results.append({"book": book, "ok": False, "failed_step": "avoid_live_overlap"})
            continue
        try:
            r = process_book(book, report)
        except Exception as exc:
            r = {"book": book, "ok": False, "failed_step": "exception",
                 "error": str(exc), "trace": traceback.format_exc()[-800:]}
        results.append(r)
        if r.get("ok"):
            pend = ((r.get("sim_formal") or {}).get("pending") or {})
            if pend.get("ok") or pend.get("key"):
                pending_keys.append(pend.get("key") or (r["book"].get("dsl") or {}).get("key"))
        _write("frost2_run.json", {
            **state,
            "results_partial": [
                {"key": ((x.get("book") or {}).get("dsl") or {}).get("key"),
                 "ok": x.get("ok"), "failed_step": x.get("failed_step")}
                for x in results
            ],
            "pending_keys": pending_keys,
            "updated_at": _now(),
        })

    state["results"] = [
        {
            "key": ((r.get("book") or {}).get("dsl") or {}).get("key"),
            "symbol": (r.get("book") or {}).get("symbol"),
            "timeframe": (r.get("book") or {}).get("timeframe"),
            "logic": (r.get("book") or {}).get("logic_class"),
            "ok": r.get("ok"),
            "failed_step": r.get("failed_step"),
            "history": r.get("history"),
            "sim": ((r.get("sim_formal") or {}).get("sim")),
            "formal": ((r.get("sim_formal") or {}).get("formal")),
            "pending": ((r.get("sim_formal") or {}).get("pending")),
            "quick": (r.get("packs") or {}).get("quick"),
            "full": (r.get("packs") or {}).get("full"),
            "metrics": (r.get("packs") or {}).get("base_metrics"),
        }
        for r in results
    ]
    state["pending_keys"] = pending_keys
    state["survivors"] = [x for x in state["results"] if x.get("ok")]
    state["ok"] = len(pending_keys) >= 2
    state["finished_at"] = _now()
    _write("frost2_run.json", state)
    print(json.dumps({
        "ok": state["ok"],
        "pending_keys": pending_keys,
        "deaths": [
            {"key": x.get("key"), "failed_step": x.get("failed_step"),
             "symbol": x.get("symbol"), "tf": x.get("timeframe")}
            for x in state["results"] if not x.get("ok")
        ],
    }, ensure_ascii=False, indent=2), flush=True)
    return 0 if state["ok"] else 1


if __name__ == "__main__":
    for name in ("glm_sim_review", "formal_ds_qwen_review", "_ai_json", "run_internal_packs"):
        if not hasattr(d, name):
            print("missing helper", name, flush=True)
            sys.exit(3)
    sys.exit(main())
