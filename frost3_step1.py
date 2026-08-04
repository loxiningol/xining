#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜叁 — Step1 GLM diagnosis → 《新策略方向推荐报告》."""
from __future__ import print_function

import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, "/root")
import auto_trade_dual_engine_factory as d

OUT = "/root/auto_trade/dual_engine"
PREFIX = "frost3"
AVOID_SYMBOLS = {
    "ADA-USDT-SWAP", "LTC-USDT-SWAP", "NG-USDT-SWAP", "XRP-USDT-SWAP",
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write(name, obj):
    path = os.path.join(OUT, name)
    open(path, "w").write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


def load_cl_archive():
    arch = os.path.join(OUT, "archive", "frost2_cl_15m_entry_exhausted_r3")
    hist = {}
    try:
        hist = json.load(open(os.path.join(arch, "HISTORY.json")))
    except Exception as exc:
        hist = {"error": str(exc)}
    notes = {
        "archive_path": arch,
        "outcome": hist.get("outcome"),
        "why_exhausted": hist.get("why_exhausted") or [],
        "hard_stop_autopsy": hist.get("hard_stop_autopsy"),
        "rounds": [
            {
                "round": r.get("round"),
                "filter": r.get("filter"),
                "summary_zh": r.get("summary_zh"),
                "gates": r.get("gates"),
                "winner_retention_pct": r.get("winner_retention_pct"),
            }
            for r in (hist.get("rounds") or [])
        ],
        "trap_checklist": [
            "勿用过窄入场过滤导致 trades<10 → WF folds<10（cci阈值陷阱）",
            "勿用边际 exhaustion 入场（rsi刚过线+中等cci）→ 引擎硬止损≈-22%",
            "软化参数换交易数时，必须同时挡住新硬止损，且 logic_destruction±20% 不翻车",
            "禁止只靠 cci 单阈值：拦亏损与保留盈利/折数不可兼得",
            "禁止再推 CL-USDT-SWAP 15m exhaustion_fade 同构方案",
            "extreme friction Sharpe 崩盘主因是硬止损簇，不是滑点叠加",
            "dest 失败常见于扰动后赢家变硬止损或重新放进亏损簇",
        ],
    }
    return notes


def load_coverage():
    """Active formal coverage from ratings + runtime assignments."""
    cov = []
    try:
        r = json.load(open("/root/auto_trade/strategy_ratings.json"))
        rbi = r.get("ratings_by_id") or {}
        for aid, v in rbi.items():
            if not isinstance(v, dict):
                continue
            cov.append({
                "assignment_id": aid,
                "key": v.get("strategy_key"),
                "name": v.get("strategy_name"),
                "symbol": v.get("symbol"),
                "timeframe": v.get("timeframe"),
                "grade": v.get("grade"),
                "auto_open": v.get("auto_open"),
                "expected_wr": v.get("expected_win_rate_pct"),
                "logic_hint": v.get("strategy_name") or v.get("strategy_key"),
            })
    except Exception as exc:
        cov.append({"error_ratings": str(exc)})
    # runtime assignments: protect live open
    try:
        rc = json.load(open("/root/auto_trade/strategy_runtime_controls.json"))
        assigns = rc.get("assignments") or {}
        for niche_key, meta in assigns.items():
            if not isinstance(meta, dict):
                continue
            parts = str(niche_key).split("|")
            if meta.get("pause_new_entries"):
                continue
            if str(meta.get("audit_state") or "") in (
                "eliminated_pending_archive", "failed_closed", "deleted", "archived",
            ):
                continue
            # only surface if can open or rated
            cov.append({
                "assignment_id": niche_key,
                "key": parts[2] if len(parts) > 2 else niche_key,
                "symbol": parts[0] if parts else meta.get("symbol"),
                "timeframe": parts[1] if len(parts) > 1 else meta.get("timeframe"),
                "grade": meta.get("lifecycle_grade") or meta.get("grade"),
                "audit_state": meta.get("audit_state"),
                "can_open": bool(meta.get("new_entries_allowed", True)) and not bool(
                    meta.get("pause_new_entries")),
                "source": "runtime_assignments",
            })
    except Exception as exc:
        cov.append({"error_runtime": str(exc)})
    # dedupe by symbol|tf|key
    seen = set()
    out = []
    for row in cov:
        k = "%s|%s|%s" % (row.get("symbol"), row.get("timeframe"), row.get("key"))
        if k in seen:
            continue
        seen.add(k)
        out.append(row)
    return out


def load_micro_death(inputs):
    micro = inputs.get("micro_72h") or {}
    # enrich from insight
    try:
        insight = json.load(open(os.path.join(OUT, "insight_latest.json")))
    except Exception:
        insight = {}
    death = inputs.get("death_heatmap_top10") or []
    return {
        "micro_72h": micro,
        "insight_summary": insight.get("summary_zh"),
        "insight_micro_read": insight.get("micro_read"),
        "insight_niches": insight.get("priority_niches") or [],
        "death_heatmap_top10": death,
        "failure_common_causes": insight.get("failure_common_causes") or [
            x.get("code") if isinstance(x, dict) else x for x in death
        ],
        "freezer_last10": (inputs.get("freezer_last10") or [])[:10],
        "mandatory_niches": [
            n for n in (inputs.get("mandatory_niches") or [])
            if str((n.get("symbol_hint") if isinstance(n, dict) else "") or "")
            not in AVOID_SYMBOLS
        ],
        "factory_niche_summary": (inputs.get("factory_context") or {}).get("niche_summary"),
    }


GLM_PROMPT = """你是GLM-5.2。只输出JSON，禁止Markdown、省略号占位符、注释。
任务：输出《新策略方向推荐报告》，供寒霜叁行动采用。

硬约束：
1) 禁止任何 ADA/LTC/NG/XRP 标的（任意周期）
2) 必须阅读并规避 CL 15m exhaustion 归档陷阱（见 cl_archive）
3) 至少给出 3 个互斥方向；每个含 symbol/timeframe/direction/logic_class/thesis/entry_sketch/exit_sketch/diff_vs_live/why_avoids_cl_traps/avoid_death
4) 逻辑不要复刻 CL：rsi刚过线+cci中等+过窄过滤→硬止损/折数不足
5) 优先 trades 预期≥12、结构失效出场、避免边际 exhaustion
6) timeframe 仅 5m/15m/1h

JSON schema：
{
  "report_title":"新策略方向推荐报告",
  "cl_root_cause_summary_zh":"...",
  "trap_checklist":["..."],
  "coverage_read_zh":"...",
  "micro_death_read_zh":"...",
  "directions":[
    {
      "rank":1,
      "symbol":"SOL-USDT-SWAP",
      "timeframe":"5m",
      "direction":"long",
      "logic_class":"impulse_continuation",
      "thesis":"...",
      "entry_sketch":"...",
      "exit_sketch":"...",
      "diff_vs_live":"...",
      "why_avoids_cl_traps":"...",
      "avoid_death":["stop_cluster"],
      "expected_trades_hint":">=12"
    }
  ],
  "mentor_notes":"..."
}
必须恰好或至少 3 条 directions，且 symbol 不在禁用来源。
"""


def ask_glm(packet):
    ai = d._ai_json("glm", GLM_PROMPT, packet, max_tokens=2200, temperature=0.15)
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


def codex_fallback(packet):
    """Deterministic ≥3 directions if GLM fails — avoids banned symbols + CL traps."""
    return {
        "report_title": "新策略方向推荐报告",
        "cl_root_cause_summary_zh": (
            "CL15m exhaustion 根因是边际入场触发引擎硬止损（≈-22%）导致 friction 崩盘；"
            "随后用 cci 抬阈值拦亏损又把交易数压到<10，WF folds 不足；再软化参数引入新硬止损且 dest 失败。"
        ),
        "trap_checklist": packet["cl_archive"]["trap_checklist"],
        "coverage_read_zh": (
            "现网重点：LTC5m/NG5m exhaustion_fade 已占用；ADA5m/XRP15m 需保护避开。"
            "寒霜叁改做 SOL/BNB/DOGE/ETH/LINK 等未保护标的，逻辑避开同构衰竭边际入场。"
        ),
        "micro_death_read_zh": (
            "死亡热力主因 stop_cluster / negative_net_expectancy / cost_collapse / sample_starvation；"
            "新方向需控制触发密度、结构失效出场、保证样本≥12。"
        ),
        "directions": [
            {
                "rank": 1,
                "symbol": "SOL-USDT-SWAP",
                "timeframe": "5m",
                "direction": "long",
                "logic_class": "impulse_continuation",
                "thesis": "H1多头过滤下，5m突破后回踩ema21不破的动量延续，拒绝中性RSI夹心",
                "entry_sketch": "h1_ema19>h1_ema53; close>ema21; close>ema8; rsi14>55; macd_stick>0; z20>0.2",
                "exit_sketch": "rsi14<48 take_profit; close<ema21 invalidation; max_hold=36",
                "diff_vs_live": "现网无SOL5m动量延续多头；不同于LTC/NG衰竭空",
                "why_avoids_cl_traps": "非exhaustion；入场远离边际超买；要求趋势对齐+动量同向，追求足够交易数而非cci收窄",
                "avoid_death": ["stop_cluster", "sample_starvation"],
                "expected_trades_hint": ">=12",
            },
            {
                "rank": 2,
                "symbol": "BNB-USDT-SWAP",
                "timeframe": "15m",
                "direction": "short",
                "logic_class": "trend_pullback_fade",
                "thesis": "H1空头中，15m反弹至ema16附近且动量转弱后做空，用结构高点失效而非硬扛",
                "entry_sketch": "h1_ema19<h1_ema53; close<ema16; rsi14>52; rsi14<68; macd_stick<0; z20>0.15; cci>80",
                "exit_sketch": "rsi14<42 take_profit; close>prev_high20 invalidation; max_hold=20",
                "diff_vs_live": "BNB15m未覆盖；非CL同构；cci阈值更宽并保留趋势过滤",
                "why_avoids_cl_traps": "强制H1趋势；rsi带宽避免刚过线；持有期与失效边界明确；不靠单一cci>90收窄到<10笔",
                "avoid_death": ["stop_cluster", "cost_collapse", "holdout_collapse"],
                "expected_trades_hint": ">=12",
            },
            {
                "rank": 3,
                "symbol": "DOGE-USDT-SWAP",
                "timeframe": "15m",
                "direction": "long",
                "logic_class": "range_reclaim",
                "thesis": "15m区间下沿回收：收盘站回ema21且macd转正，结构破位立即失效，控制频率",
                "entry_sketch": "close>ema21; close>prev_low20; rsi14>42; rsi14<58; macd_stick>0; z20>-0.5; z20<1.0",
                "exit_sketch": "rsi14>65 take_profit; close<prev_low20 invalidation; max_hold=24",
                "diff_vs_live": "不同于DOGE旧1h刷单回收；用15m+结构失效，避开保护标的",
                "why_avoids_cl_traps": "非商品CL；非边际超买衰竭；出场用结构低点而非无边界中轨；目标样本充足",
                "avoid_death": ["sample_starvation", "negative_net_expectancy"],
                "expected_trades_hint": ">=12",
            },
        ],
        "mentor_notes": "三方向逻辑互斥；禁止回退到ADA/LTC/NG/XRP；CL15m exhaustion 永不再做同构。",
        "fallback": True,
        "provider": "codex_fallback",
    }


def sanitize_directions(report):
    dirs = []
    for row in (report.get("directions") or []):
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").upper().split("|")[0]
        if sym in AVOID_SYMBOLS or sym.split("-")[0] in ("ADA", "LTC", "NG", "XRP"):
            continue
        if str(row.get("timeframe")) not in ("5m", "15m", "1h"):
            continue
        # ban CL 15m exhaustion clone
        logic = str(row.get("logic_class") or "").lower()
        if sym.startswith("CL") and "exhaust" in logic and str(row.get("timeframe")) == "15m":
            continue
        row = dict(row)
        row["symbol"] = sym
        dirs.append(row)
    report = dict(report)
    report["directions"] = dirs[:5]
    return report


def main():
    print("[frost3] Step1 start", _now(), flush=True)
    inputs = d.collect_inputs()
    # rewrite focus candidates without banned symbols
    inputs["focus_candidates"] = [
        c for c in (inputs.get("focus_candidates") or [])
        if str(c.get("symbol")) not in AVOID_SYMBOLS
    ]
    cl = load_cl_archive()
    cov = load_coverage()
    micro = load_micro_death(inputs)
    packet = {
        "op": "寒霜叁",
        "at": _now(),
        "gates": {
            "wf": ">=7/10",
            "logic_destruction": True,
            "mc_beat": ">=0.90 sign_randomization_null",
            "friction_sharpe": ">=0",
            "sim": "DS&Qwen >=55%",
        },
        "cl_archive": cl,
        "active_coverage": cov,
        "micro_death": micro,
        "banned_symbols": sorted(AVOID_SYMBOLS),
    }
    _write("%s_step1_packet.json" % PREFIX, packet)

    parsed, ai = ask_glm(packet)
    _write("%s_step1_glm.json" % PREFIX, {
        "ok": ai.get("ok"), "error": ai.get("error"),
        "parsed": parsed, "raw_preview": str(ai.get("raw_preview") or "")[:3000],
    })
    used_fallback = False
    if not parsed or not (parsed.get("directions") or []):
        report = codex_fallback(packet)
        used_fallback = True
    else:
        report = sanitize_directions(parsed)
        if len(report.get("directions") or []) < 3:
            fb = codex_fallback(packet)
            # merge unique
            have = set(
                (d.get("symbol"), d.get("timeframe"), d.get("logic_class"))
                for d in report.get("directions") or []
            )
            for row in fb["directions"]:
                k = (row.get("symbol"), row.get("timeframe"), row.get("logic_class"))
                if k not in have:
                    report.setdefault("directions", []).append(row)
                    have.add(k)
            report["directions"] = report["directions"][:5]
            if len(report["directions"]) < 3:
                report = fb
                used_fallback = True
            report["cl_root_cause_summary_zh"] = report.get("cl_root_cause_summary_zh") or fb[
                "cl_root_cause_summary_zh"]
            report["trap_checklist"] = report.get("trap_checklist") or fb["trap_checklist"]

    report["generated_at"] = _now()
    report["glm_ok"] = bool(ai.get("ok") and not used_fallback)
    report["fallback"] = used_fallback
    report["banned_symbols"] = sorted(AVOID_SYMBOLS)
    path = _write("%s_direction_report.json" % PREFIX, report)
    # also human-readable mirror
    _write("%s_新策略方向推荐报告.json" % PREFIX, report)
    print("[frost3] Step1 report", path, "dirs", len(report.get("directions") or []),
          "fallback", used_fallback, flush=True)
    for i, row in enumerate(report.get("directions") or [], 1):
        print(" DIR%d" % i, row.get("symbol"), row.get("timeframe"),
              row.get("direction"), row.get("logic_class"), flush=True)
    return report


if __name__ == "__main__":
    main()
