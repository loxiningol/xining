# -*- coding: utf-8 -*-
"""系统规划预测模块 — 三AI推演未来开仓频率。

Designer 2026-07-24:
  · 每周一 UTC+8 08:00 定时；网站可手动触发
  · 采集范围：所有 formal daemon 已挂载的自动交易策略（含未评级），
    不以 B/A/S 人工确认名单为唯一来源
  · DeepSeek: 信号触发次数区间
  · Qwen: 适用环境活跃度 高/中/低
  · GLM: 综合日/周开仓次数与主要贡献策略
  · 不计入策略复核配额；kind=system_forecast
"""
from __future__ import print_function

import argparse
import json
import math
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"
DSL_CONFIG_PATH = ROOT / "strategy_configs" / "ai_dsl_strategies.json"
EXP_CONFIG_PATH = ROOT / "strategy_configs" / "experimental_strategies.json"
LATEST_PATH = AUTO_DIR / "system_forecast_latest.json"
HISTORY_PATH = AUTO_DIR / "system_forecast_history.jsonl"
STATE_PATH = AUTO_DIR / "system_forecast_state.json"
HISTORY_KEEP = 52  # ~1 year of weekly runs

PROVIDERS = ("deepseek", "qwen", "glm")

# Fallback titles aligned with monitor dashboard known_names.
_STRATEGY_TITLE_FALLBACK = {
    "ema6_center_down_then_fall": "EMA6居中后再下行",
    "ema7_center_down_short": "EMA6居中后再下行",
    "conventional_up_break_long": "常规上升排列突破",
    "early_downtrend_ema6_ema75_short": "EMA19反抽失败·EMA6/EMA75同步破位",
    "conventional_down_arrangement_bottom_up_long": "常规下跌排列筑底上行",
    "cci_75_100": "EMA7上升趋势回踩续涨",
    "ema53_liquidity_sweep_reclaim_long": "EMA53缓升｜36小时低点扫荡收回（AI创造）",
    "ema8_mainwave_long": "EMA8主升浪",
    "cci_neg60_neg110_short": "CCI负60-负110下行中再下行",
    "btc15_dual_cycle_downtrend_reentry_short_ai": "双周期下跌加速再死叉（AI创造）",
    "conventional_up_arrangement_valid_death_cross_short": "常规上升排列有效死叉",
    "btc5_exhaustion_reclaim_long_ai": "BTC 5分钟超跌收回（AI创造）",
    "cl5_exhaustion_fade_short_ai": "CL 5分钟冲高衰竭回落（AI创造）",
    "ng5_exhaustion_fade_short_ai": "NG 5分钟冲高衰竭回落（AI创造）",
    "ng5_session_exhaustion_reclaim_long_ai": "NG 5分钟时段超跌收回（AI创造）",
    "xag5_session_breakdown_short_ai": "XAG 5分钟时段顺势破位（AI创造）",
    "ltc5_exhaustion_fade_short_ai": "LTC 5分钟冲高衰竭回落（AI创造）",
    "ada5_session_trend_pullback_short_ai": "ADA 5分钟时段趋势反抽（AI创造）",
    "xau15_h1_breakout_long_ai": "XAU 15分钟顺势放量突破（AI创造）",
}

_NAME_CACHE = None


def _strategy_title_map():
    global _NAME_CACHE
    if _NAME_CACHE is not None:
        return _NAME_CACHE
    names = dict(_STRATEGY_TITLE_FALLBACK)
    for path in (DSL_CONFIG_PATH, EXP_CONFIG_PATH):
        doc = _read(path, {"strategies": []})
        for row in (doc.get("strategies") or []):
            if not isinstance(row, dict):
                continue
            key = row.get("key") or row.get("strategy_key")
            if not key:
                continue
            title = (row.get("name") or row.get("strategy_name")
                     or row.get("title") or "").strip()
            if title:
                names[str(key)] = title
    _NAME_CACHE = names
    return names


def strategy_display_name(strategy_key, report=None):
    """Human title for Wx/UI; never return empty."""
    key = str(strategy_key or "").strip()
    if not key:
        return ""
    fallback = None
    if report:
        for row in (report.get("per_strategy") or []):
            if row.get("strategy_key") == key:
                name = str(row.get("strategy_name") or "").strip()
                if name and name != key:
                    fallback = name
                    break
        if fallback is None:
            snap_rows = ((report.get("snapshot") or {}).get("strategies") or [])
            for row in snap_rows:
                if row.get("strategy_key") == key:
                    name = str(row.get("strategy_name") or "").strip()
                    if name and name != key:
                        fallback = name
                        break
    try:
        import auto_trade_strategy_titles as titles
        return titles.short_strategy_title(key, fallback)
    except Exception:
        pass
    title = _strategy_title_map().get(key)
    if title and title != key:
        return title
    return fallback or key


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        delete=False, dir=str(path.parent), mode="w", encoding="utf-8")
    try:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        handle.close()
        Path(handle.name).replace(path)
    except Exception:
        try:
            Path(handle.name).unlink()
        except Exception:
            pass
        raise


def _wx(message, kind="system_forecast", meta=None):
    try:
        import auto_trade_formal_notify as notify
        return notify.send_message(message, kind=kind, meta=meta or {})
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _parse_dt(text):
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(text)[:19], fmt)
        except Exception:
            continue
    return None


def _hold_hours(opened_at, closed_at):
    a = _parse_dt(opened_at)
    b = _parse_dt(closed_at)
    if not a or not b:
        return None
    return max(0.0, (b - a).total_seconds() / 3600.0)


def _logic_brief(definition):
    if not isinstance(definition, dict):
        return ""
    desc = str(definition.get("description") or "").strip()
    if desc:
        return desc[:180]
    direction = definition.get("direction") or "?"
    tf = definition.get("timeframe") or "?"
    hold = definition.get("max_hold_bars")
    return "方向=%s 周期=%s max_hold=%s" % (direction, tf, hold)


def _symbol_market_stats(symbol, timeframe):
    """Light market reference only — never block on research frame loads."""
    out = {
        "symbol": symbol,
        "timeframe": timeframe,
        "ok": False,
        "source": "light_cache",
    }
    breath = _read(AUTO_DIR / "strategy_breath_sensors.json", {})
    if isinstance(breath, dict) and breath:
        out.update({
            "ok": True,
            "volatility_percentile": breath.get("volatility_percentile"),
            "breath_updated_at": breath.get("updated_at") or breath.get("time"),
            "note": "全局波动参考；逐标的精确波动未在预测路径强加载以免阻塞。",
        })
    micro = _read(AUTO_DIR / "microstructure_primitives_status.json", {})
    if isinstance(micro, dict) and micro:
        out["micro_status"] = str(micro.get("status") or micro.get("natural_language") or "")[:120]
        out["ok"] = True
    if not out.get("ok"):
        out["note"] = "无本地波动缓存；预测将主要依赖近7日实际开仓与策略逻辑。"
    return out


def _micro_fitness_hint(symbol, timeframe):
    """Reference-only environment fitness from niche/micro status files."""
    hints = []
    niche = _read(AUTO_DIR / "niche_map_report.json", {})
    if isinstance(niche, dict):
        cells = niche.get("cells") or niche.get("terrain_cells") or []
        for cell in cells[:20]:
            if not isinstance(cell, dict):
                continue
            txt = json.dumps(cell, ensure_ascii=False)
            if symbol.split("-")[0] in txt or (timeframe or "") in txt:
                hints.append({
                    "source": "niche_map",
                    "summary": str(cell.get("observable_summary")
                                   or cell.get("pattern_id")
                                   or cell)[:160],
                })
    micro = _read(AUTO_DIR / "microstructure_primitives_status.json", {})
    if isinstance(micro, dict) and micro:
        hints.append({
            "source": "microstructure_primitives",
            "summary": str(micro.get("natural_language")
                           or micro.get("status")
                           or "status_present")[:160],
            "updated_at": micro.get("updated_at") or micro.get("time"),
        })
    breath = _read(AUTO_DIR / "strategy_breath_sensors.json", {})
    if isinstance(breath, dict) and breath.get("volatility_percentile") is not None:
        hints.append({
            "source": "strategy_breath",
            "volatility_percentile": breath.get("volatility_percentile"),
            "note": "参考用，非开仓条件",
        })
    return hints[:8]


def _strategy_7d_activity(strategy_key):
    import auto_trade_human_confirm_pipeline as pipeline
    after = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    closed = pipeline._closed_trades_for(strategy_key, limit=80, after=after)
    # also count opens still open / opened in window from formal states
    opens_7d = 0
    hold_hours = []
    for t in closed:
        opens_7d += 1
        h = _hold_hours(t.get("opened_at"), t.get("closed_at"))
        if h is not None:
            hold_hours.append(h)
    # scan current opens
    for path in list(AUTO_DIR.glob("formal_v6_state*.json")):
        state = _read(path, {})
        cur = state.get("current")
        if not isinstance(cur, dict):
            continue
        if str(cur.get("strategy_key") or "") != str(strategy_key):
            continue
        if cur.get("closed_at"):
            continue
        opened = cur.get("opened_at") or ""
        if opened >= after:
            opens_7d += 1
            # live hold so far
            a = _parse_dt(opened)
            if a:
                hold_hours.append(max(0.0, (datetime.now() - a).total_seconds() / 3600.0))
    avg_hold = (sum(hold_hours) / float(len(hold_hours))) if hold_hours else None
    return {
        "opens_7d": opens_7d,
        "closed_7d": len(closed),
        "avg_hold_hours": None if avg_hold is None else round(avg_hold, 3),
        "sample_holds": [round(x, 2) for x in hold_hours[:8]],
    }


def _daemon_timeframe(path, cfg):
    tf = str((cfg or {}).get("timeframe") or "").strip().lower()
    if tf in ("1m", "5m", "15m", "1h", "4h"):
        return tf
    m = re.search(r"_(\d+m)\.json$", Path(path).name)
    if m:
        return m.group(1)
    return "1h"


def _assignment_row(assignments, symbol, timeframe, key):
    aid = "%s|%s|%s" % (symbol, timeframe, key)
    row = assignments.get(aid)
    if isinstance(row, dict):
        return aid, row
    # fallback: same symbol+key under any timeframe
    for a, r in (assignments or {}).items():
        if not isinstance(r, dict):
            continue
        parts = str(a).split("|", 2)
        if len(parts) == 3 and parts[0] == symbol and parts[2] == key:
            return a, r
    return aid, {}


def list_auto_trade_strategies():
    """All strategies currently mounted in formal auto-trade daemons.

    Source of truth = formal_daemon_config*.json (enabled + strategy_keys),
    enriched with strategy_runtime_controls (grade / pause / ratio).
    """
    controls = _read(CONTROL_PATH, {"assignments": {}})
    assignments = controls.get("assignments") or {}
    out = []
    seen = set()
    for path in sorted(AUTO_DIR.glob("formal_daemon_config*.json")):
        cfg = _read(path, {})
        if not isinstance(cfg, dict):
            continue
        if cfg.get("enabled") is False:
            continue
        symbol = cfg.get("symbol")
        if not symbol:
            continue
        timeframe = _daemon_timeframe(path, cfg)
        keys = []
        for k in (cfg.get("strategy_keys") or []):
            if k and k not in keys:
                keys.append(k)
        sk = cfg.get("strategy_key")
        if sk and sk not in keys:
            keys.append(sk)
        allow_open = bool(cfg.get("allow_auto_open", False))
        for key in keys:
            dedupe = "%s|%s|%s" % (symbol, timeframe, key)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            aid, row = _assignment_row(assignments, symbol, timeframe, key)
            raw_g = str(row.get("lifecycle_grade") or "").strip()
            grade_u = raw_g.upper() if raw_g else ""
            # Deleted / shadow assignments are not live mounted strategies.
            if grade_u in ("DELETED", "SHADOW"):
                continue
            grade = grade_u if grade_u in ("S", "A", "B", "C") else "B"
            # Live mounted strategies default to B; never show 未评级.
            if (not raw_g or raw_g in ("未评级", "待评级")
                    or grade_u in ("NONE", "NULL", "UNRATED", "PENDING")):
                grade = "B"
            pause = bool(row.get("pause_new_entries"))
            can_open = bool(allow_open and not pause)
            out.append({
                "assignment_id": aid,
                "strategy_key": key,
                "strategy_name": row.get("strategy_name") or key,
                "lifecycle_grade": grade,
                "symbol": symbol,
                "timeframe": timeframe,
                "daemon_config": path.name,
                "daemon_allow_auto_open": allow_open,
                "pause_new_entries": pause,
                "can_open": can_open,
                "max_position_ratio": row.get("max_position_ratio"),
                "row": row,
            })
    out.sort(key=lambda x: (
        0 if x.get("can_open") else 1,
        x.get("symbol") or "",
        x.get("timeframe") or "",
        x.get("strategy_key") or "",
    ))
    return out


def collect_forecast_snapshot():
    """Step 1: all auto-trade mounted strategies + 7d activity + market refs."""
    import auto_trade_strategy_dynamic_optimizer as opt
    targets = list_auto_trade_strategies()
    strategies = []
    market_by_symbol = {}
    for t in targets:
        key = t.get("strategy_key")
        symbol = t.get("symbol")
        timeframe = t.get("timeframe")
        loaded = opt.load_definition(key) or {}
        definition = loaded.get("definition") or {}
        activity = _strategy_7d_activity(key)
        mk = "%s|%s" % (symbol, timeframe)
        if mk not in market_by_symbol:
            market_by_symbol[mk] = _symbol_market_stats(symbol, timeframe)
        strategies.append({
            "assignment_id": t.get("assignment_id"),
            "strategy_key": key,
            "strategy_name": t.get("strategy_name") or key,
            "grade": t.get("lifecycle_grade"),
            "symbol": symbol,
            "timeframe": timeframe,
            "can_open": bool(t.get("can_open")),
            "pause_new_entries": bool(t.get("pause_new_entries")),
            "daemon_allow_auto_open": bool(t.get("daemon_allow_auto_open")),
            "max_position_ratio": t.get("max_position_ratio"),
            "ai_theoretical_wr_avg": (t.get("row") or {}).get(
                "ai_theoretical_wr_avg"),
            "daemon_config": t.get("daemon_config"),
            "direction": definition.get("direction"),
            "logic_brief": _logic_brief(definition),
            "max_hold_bars": definition.get("max_hold_bars"),
            "ai_theoretical_wr_avg": (t.get("row") or {}).get("ai_theoretical_wr_avg"),
            "activity_7d": activity,
            "env_fitness_ref": _micro_fitness_hint(symbol, timeframe),
            "market": market_by_symbol[mk],
        })
    can_open_n = sum(1 for s in strategies if s.get("can_open"))
    paused_n = sum(1 for s in strategies if s.get("pause_new_entries"))
    return {
        "generated_at": _now(),
        "horizon": {"days": 7, "label": "未来一周"},
        "active_strategy_count": len(strategies),
        "can_open_strategy_count": can_open_n,
        "paused_strategy_count": paused_n,
        "strategies": strategies,
        "markets": list(market_by_symbol.values()),
        "macro_calendar": {
            "available": False,
            "note": "系统暂无内置宏观事件日历数据源；预测默认假设无极端政策/数据冲击。",
        },
        "notes": [
            "策略来源=formal_daemon_config* 已挂载清单，不是仅 B/A/S。",
            "can_open=false（pause_new_entries）的策略预期开仓应为 0。",
            "环境适配度/微观基元仅作参考，不作为开仓条件。",
            "开仓活动统计来自 formal_v6_state* 近7天成交。",
        ],
    }


DEEPSEEK_PROMPT = """你是栖语系统的「信号频率推演官」(DeepSeek角色)。
只对 snapshot.openable_strategies 做未来7天开仓次数区间估计。
paused_strategies 已全部暂停，不要逐条输出（系统会自动记为0）。
只输出紧凑JSON（勿输出markdown）：
{"by_strategy":[{"strategy_key":"...","symbol":"...","timeframe":"...",
 "weekly_opens_low":0,"weekly_opens_high":0,
 "daily_opens_low":0,"daily_opens_high":0,"basis":"一句中文"}],
 "notes":["..."]}
必须覆盖 openable_strategies 每一条。次数非负。"""


QWEN_PROMPT = """你是栖语系统的「环境活跃度评估官」(Qwen角色)。
只对 snapshot.openable_strategies 评估未来一周适用环境活跃度 high|mid|low。
paused_strategies 无需逐条输出。
只输出紧凑JSON（勿输出markdown）：
{"by_strategy":[{"strategy_key":"...","symbol":"...","timeframe":"...",
 "activity":"high或mid或low","env_fit_prob":0到1,"basis":"一句中文"}],
 "market_regime":"一句中文","notes":["..."]}
必须覆盖 openable_strategies 每一条。"""


CHATGPT_PROMPT = """你是栖语系统的「综合规划预测官」(GLM角色)。
综合 DeepSeek/Qwen 对可开仓策略的估计，输出系统级开仓节奏预测。
暂停策略预期开仓=0，不要逐条展开。
只输出紧凑JSON（勿输出markdown）：
{"daily_opens_expected":数字,"weekly_opens_expected":数字,
 "daily_opens_range":[低,高],"weekly_opens_range":[低,高],
 "top_contributors":[{"strategy_key":"...","weekly_opens_expected":数字,"share_pct":0到100}],
 "by_strategy":[{"strategy_key":"...","symbol":"...","timeframe":"...",
  "weekly_opens_low":0,"weekly_opens_high":0,
  "confidence":"high或mid或low","note":"一句"}],
 "key_assumptions":["..."],
 "risk_warnings":["..."],
 "summary_zh":"中文总述：先写可开仓策略数，再写预计本周总开仓与主要贡献叙事（勿写暂停策略数；级数统计由系统填）"}
by_strategy 只需覆盖 openable_strategies。"""


def _ai_payload_from_snapshot(snapshot):
    openable = []
    paused = []
    for s in (snapshot.get("strategies") or []):
        row = {
            "strategy_key": s.get("strategy_key"),
            "grade": s.get("grade"),
            "symbol": s.get("symbol"),
            "timeframe": s.get("timeframe"),
            "direction": s.get("direction"),
            "logic_brief": (s.get("logic_brief") or "")[:100],
            "opens_7d": (s.get("activity_7d") or {}).get("opens_7d"),
            "avg_hold_hours": (s.get("activity_7d") or {}).get("avg_hold_hours"),
            "max_position_ratio": s.get("max_position_ratio"),
        }
        if s.get("can_open"):
            openable.append(row)
        else:
            paused.append({
                "strategy_key": s.get("strategy_key"),
                "symbol": s.get("symbol"),
                "timeframe": s.get("timeframe"),
                "grade": s.get("grade"),
            })
    return {
        "generated_at": snapshot.get("generated_at"),
        "horizon": snapshot.get("horizon"),
        "active_strategy_count": snapshot.get("active_strategy_count"),
        "can_open_strategy_count": snapshot.get("can_open_strategy_count"),
        "paused_strategy_count": snapshot.get("paused_strategy_count"),
        "openable_strategies": openable,
        "paused_strategies": paused,
        "paused_note": "以上暂停策略预期开仓均为0，无需逐条预测",
        "notes": snapshot.get("notes"),
        "macro_calendar": snapshot.get("macro_calendar"),
    }


def local_baseline_forecast(snapshot):
    """Deterministic fallback from 7d actual opens for can_open strategies."""
    by_strategy = []
    weekly_sum = 0.0
    weekly_low = 0.0
    weekly_high = 0.0
    for s in (snapshot.get("strategies") or []):
        key = s.get("strategy_key")
        opens = _num((s.get("activity_7d") or {}).get("opens_7d"), 0) or 0
        if not s.get("can_open"):
            by_strategy.append({
                "strategy_key": key,
                "symbol": s.get("symbol"),
                "timeframe": s.get("timeframe"),
                "weekly_opens_low": 0,
                "weekly_opens_high": 0,
                "daily_opens_low": 0,
                "daily_opens_high": 0,
                "activity": "low",
                "confidence": "high",
                "basis": "暂停新开仓",
            })
            continue
        # project next week near recent 7d, with mild band
        low = max(0.0, opens * 0.5)
        high = max(opens * 1.5, opens + 1.0) if opens > 0 else 1.0
        mid = (low + high) / 2.0
        weekly_sum += mid
        weekly_low += low
        weekly_high += high
        activity = "high" if opens >= 3 else ("mid" if opens >= 1 else "low")
        by_strategy.append({
            "strategy_key": key,
            "symbol": s.get("symbol"),
            "timeframe": s.get("timeframe"),
            "weekly_opens_low": round(low, 2),
            "weekly_opens_high": round(high, 2),
            "daily_opens_low": round(low / 7.0, 3),
            "daily_opens_high": round(high / 7.0, 3),
            "activity": activity,
            "confidence": "mid",
            "basis": "近7日实际开仓=%s 的本地基线外推" % opens,
            "weekly_opens_expected": round(mid, 2),
        })
    openable = [x for x in by_strategy if (x.get("weekly_opens_high") or 0) > 0
                or any(s.get("strategy_key") == x.get("strategy_key") and s.get("can_open")
                       for s in (snapshot.get("strategies") or []))]
    tops = sorted(
        [x for x in by_strategy if any(
            s.get("strategy_key") == x.get("strategy_key") and s.get("can_open")
            for s in (snapshot.get("strategies") or []))],
        key=lambda x: -(x.get("weekly_opens_expected") or 0)
    )[:5]
    total = weekly_sum or 0.0
    for t in tops:
        t["share_pct"] = round(
            100.0 * (t.get("weekly_opens_expected") or 0) / total, 1) if total else 0
    return {
        "daily_opens_expected": round(weekly_sum / 7.0, 2),
        "weekly_opens_expected": round(weekly_sum, 2),
        "daily_opens_range": [round(weekly_low / 7.0, 2), round(weekly_high / 7.0, 2)],
        "weekly_opens_range": [round(weekly_low, 2), round(weekly_high, 2)],
        "top_contributors": [
            {
                "strategy_key": t.get("strategy_key"),
                "weekly_opens_expected": t.get("weekly_opens_expected"),
                "share_pct": t.get("share_pct"),
            } for t in tops
        ],
        "by_strategy": by_strategy,
        "key_assumptions": [
            "以近7日实际开仓为基线外推",
            "暂停策略预期开仓为0",
            "波动率与策略集合大致维持",
        ],
        "risk_warnings": [
            "基线未建模极端行情",
            "暂停策略若恢复会抬升开仓频率",
        ],
        "summary_zh": (
            "预计本周总开仓约%s次，按近7日可开仓策略外推。"
            % (round(weekly_sum, 2),)
        ),
        "source": "local_baseline",
    }


def run_three_ai_forecast(snapshot):
    """Step 2: DeepSeek + Qwen in parallel, then GLM synthesis."""
    slim = _ai_payload_from_snapshot(snapshot)
    baseline = local_baseline_forecast(snapshot)

    pool = ThreadPoolExecutor(max_workers=2)
    try:
        fut_ds = pool.submit(_ai_call, "deepseek", DEEPSEEK_PROMPT,
                             {"role": "signal_frequency", "snapshot": slim},
                             2200)
        fut_qw = pool.submit(_ai_call, "qwen", QWEN_PROMPT,
                             {"role": "env_activity", "snapshot": slim},
                             1800)
        deepseek = fut_ds.result()
        qwen = fut_qw.result()
    finally:
        pool.shutdown(wait=True)

    chatgpt = _ai_call(
        "glm", CHATGPT_PROMPT,
        {
            "role": "synthesis",
            "snapshot": {
                "generated_at": slim.get("generated_at"),
                "active_strategy_count": slim.get("active_strategy_count"),
                "can_open_strategy_count": slim.get("can_open_strategy_count"),
                "paused_strategy_count": slim.get("paused_strategy_count"),
                "openable_strategies": slim.get("openable_strategies"),
                "paused_strategies_count": len(slim.get("paused_strategies") or []),
            },
            "deepseek": deepseek.get("parsed") if deepseek.get("ok") else {
                "error": deepseek.get("error"), "fallback_hint": "use_baseline"},
            "qwen": qwen.get("parsed") if qwen.get("ok") else {
                "error": qwen.get("error"), "fallback_hint": "use_baseline"},
            "local_baseline": {
                "daily_opens_expected": baseline.get("daily_opens_expected"),
                "weekly_opens_expected": baseline.get("weekly_opens_expected"),
                "weekly_opens_range": baseline.get("weekly_opens_range"),
                "top_contributors": baseline.get("top_contributors"),
            },
        },
        max_tokens=2200,
    )
    return {
        "deepseek": deepseek,
        "qwen": qwen,
        "glm": chatgpt,
        "local_baseline": baseline,
        "all_ok": bool(deepseek.get("ok") and qwen.get("ok") and chatgpt.get("ok")),
    }


def _ai_call(provider, system_prompt, user_obj, max_tokens=1200):
    import auto_trade_ai_consensus as ai
    from urllib import request as urllib_request
    cfg = ai._provider_config(provider)
    consent = ai.external_research_consent_status(provider, "final_review")
    if not consent.get("allowed"):
        return {"provider": provider, "ok": False, "error": "consent_missing"}
    if not cfg.get("api_key"):
        return {"provider": provider, "ok": False, "error": "api_key_missing"}

    def _once(tokens):
        body = {
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": ai.canonical_json(user_obj)},
            ],
        }
        if provider == "deepseek":
            body["response_format"] = {"type": "json_object"}
        if provider == "glm":
            body["max_completion_tokens"] = max(tokens, 1600)
        else:
            body["temperature"] = 0.2
            body["max_tokens"] = tokens
        started = time.time()
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(
            cfg["url"], data=encoded,
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        timeout = max(int(cfg.get("timeout") or 90), 120)
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        content = (((raw.get("choices") or [{}])[0].get("message") or {})
                   .get("content"))
        if not str(content or "").strip() and provider == "deepseek":
            content = (((raw.get("choices") or [{}])[0].get("message") or {})
                       .get("reasoning_content") or content)
        parsed = ai._parse_content_json(content)
        return {
            "provider": provider, "ok": True, "parsed": parsed,
            "latency_sec": round(time.time() - started, 3),
        }

    try:
        return _once(max_tokens)
    except Exception as exc:
        # one compact retry for truncated JSON
        try:
            user2 = dict(user_obj)
            if isinstance(user2.get("snapshot"), dict):
                snap = dict(user2["snapshot"])
                # shrink logic briefs further on retry
                rows = []
                for row in (snap.get("openable_strategies") or []):
                    rows.append({
                        "strategy_key": row.get("strategy_key"),
                        "symbol": row.get("symbol"),
                        "timeframe": row.get("timeframe"),
                        "opens_7d": row.get("opens_7d"),
                        "grade": row.get("grade"),
                    })
                snap["openable_strategies"] = rows
                user2["snapshot"] = snap
            return _once(max(1200, int(max_tokens * 0.7)))
        except Exception as exc2:
            return {
                "provider": provider, "ok": False,
                "error": "%s | retry: %s" % (exc, exc2),
                "latency_sec": None,
            }


def _num(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def build_report(snapshot, ai_bundle):
    """Step 3: fixed-format forecast report."""
    ds = (ai_bundle.get("deepseek") or {}).get("parsed") or {}
    qw = (ai_bundle.get("qwen") or {}).get("parsed") or {}
    cg = (ai_bundle.get("glm") or {}).get("parsed") or {}
    baseline = ai_bundle.get("local_baseline") or {}

    daily = _num(cg.get("daily_opens_expected"))
    weekly = _num(cg.get("weekly_opens_expected"))
    daily_range = cg.get("daily_opens_range") or []
    weekly_range = cg.get("weekly_opens_range") or []
    used_baseline = False
    if daily is None and weekly is None:
        daily = _num(baseline.get("daily_opens_expected"))
        weekly = _num(baseline.get("weekly_opens_expected"))
        daily_range = baseline.get("daily_opens_range") or daily_range
        weekly_range = baseline.get("weekly_opens_range") or weekly_range
        used_baseline = True
        if not cg:
            cg = dict(baseline)
    if daily is None and weekly is not None:
        daily = weekly / 7.0
    if weekly is None and daily is not None:
        weekly = daily * 7.0

    # merge per-strategy
    by_key = {}
    for row in (baseline.get("by_strategy") or []):
        if isinstance(row, dict) and row.get("strategy_key"):
            by_key[row["strategy_key"]] = {
                "strategy_key": row["strategy_key"],
                "symbol": row.get("symbol"),
                "timeframe": row.get("timeframe"),
                "weekly_opens_low": _num(row.get("weekly_opens_low"), 0),
                "weekly_opens_high": _num(row.get("weekly_opens_high"), 0),
                "activity": row.get("activity"),
                "confidence": row.get("confidence"),
                "note": row.get("basis"),
                "from_baseline": True,
            }
    for row in (ds.get("by_strategy") or []):
        if isinstance(row, dict) and row.get("strategy_key"):
            item = by_key.setdefault(row["strategy_key"], {
                "strategy_key": row["strategy_key"]})
            item["weekly_opens_low"] = _num(row.get("weekly_opens_low"),
                                            item.get("weekly_opens_low") or 0)
            item["weekly_opens_high"] = _num(row.get("weekly_opens_high"),
                                             item.get("weekly_opens_high") or 0)
            item["deepseek_basis"] = row.get("basis")
            item["symbol"] = row.get("symbol") or item.get("symbol")
            item["timeframe"] = row.get("timeframe") or item.get("timeframe")
            item["from_baseline"] = False
    for row in (qw.get("by_strategy") or []):
        if not isinstance(row, dict) or not row.get("strategy_key"):
            continue
        item = by_key.setdefault(row["strategy_key"], {
            "strategy_key": row["strategy_key"]})
        item["activity"] = row.get("activity")
        item["env_fit_prob"] = _num(row.get("env_fit_prob"))
        item["qwen_basis"] = row.get("basis")
        item["symbol"] = row.get("symbol") or item.get("symbol")
        item["timeframe"] = row.get("timeframe") or item.get("timeframe")
    for row in (cg.get("by_strategy") or []):
        if not isinstance(row, dict) or not row.get("strategy_key"):
            continue
        item = by_key.setdefault(row["strategy_key"], {
            "strategy_key": row["strategy_key"]})
        if row.get("weekly_opens_low") is not None:
            item["weekly_opens_low"] = _num(row.get("weekly_opens_low"),
                                            item.get("weekly_opens_low"))
        if row.get("weekly_opens_high") is not None:
            item["weekly_opens_high"] = _num(row.get("weekly_opens_high"),
                                             item.get("weekly_opens_high"))
        item["confidence"] = row.get("confidence") or item.get("confidence") or "mid"
        item["note"] = row.get("note") or item.get("note")
        item["symbol"] = row.get("symbol") or item.get("symbol")
        item["timeframe"] = row.get("timeframe") or item.get("timeframe")
        item["from_baseline"] = False

    # attach names/grades from snapshot
    meta = {}
    for s in (snapshot.get("strategies") or []):
        meta[s["strategy_key"]] = s
        meta["%s|%s|%s" % (s.get("symbol"), s.get("timeframe"),
                           s.get("strategy_key"))] = s
    per_strategy = []
    for key, item in by_key.items():
        m = meta.get(key) or {}
        exact = meta.get("%s|%s|%s" % (item.get("symbol") or m.get("symbol"),
                                       item.get("timeframe") or m.get("timeframe"),
                                       key))
        if exact:
            m = exact
        item["strategy_name"] = m.get("strategy_name") or key
        item["grade"] = m.get("grade")
        item["symbol"] = item.get("symbol") or m.get("symbol")
        item["timeframe"] = item.get("timeframe") or m.get("timeframe")
        item["can_open"] = m.get("can_open")
        item["pause_new_entries"] = m.get("pause_new_entries")
        item["max_position_ratio"] = (
            item.get("max_position_ratio")
            if item.get("max_position_ratio") is not None
            else m.get("max_position_ratio"))
        item["ai_theoretical_wr_avg"] = (
            item.get("ai_theoretical_wr_avg")
            if item.get("ai_theoretical_wr_avg") is not None
            else m.get("ai_theoretical_wr_avg"))
        try:
            import auto_trade_strategy_titles as titles
            item["strategy_title"] = titles.short_strategy_title(
                key, item.get("strategy_name"))
            item["strategy_name"] = item["strategy_title"]
        except Exception:
            item["strategy_title"] = item.get("strategy_name") or key
        item["opens_7d_actual"] = (m.get("activity_7d") or {}).get("opens_7d")
        if m.get("can_open") is False:
            item["weekly_opens_low"] = 0
            item["weekly_opens_high"] = 0
            item["activity"] = item.get("activity") or "low"
            item["confidence"] = item.get("confidence") or "high"
            item["note"] = (item.get("note") or "已暂停新开仓，预期开仓=0")
        if not item.get("confidence"):
            act = str(item.get("activity") or "").lower()
            item["confidence"] = {"high": "high", "mid": "mid", "low": "low"}.get(
                act, "mid")
        per_strategy.append(item)
    have = set(x.get("strategy_key") for x in per_strategy)
    for s in (snapshot.get("strategies") or []):
        key = s.get("strategy_key")
        if key in have:
            continue
        per_strategy.append({
            "strategy_key": key,
            "strategy_name": s.get("strategy_name") or key,
            "grade": s.get("grade"),
            "symbol": s.get("symbol"),
            "timeframe": s.get("timeframe"),
            "can_open": s.get("can_open"),
            "pause_new_entries": s.get("pause_new_entries"),
            "max_position_ratio": s.get("max_position_ratio"),
            "ai_theoretical_wr_avg": s.get("ai_theoretical_wr_avg"),
            "weekly_opens_low": 0,
            "weekly_opens_high": 0 if not s.get("can_open") else None,
            "confidence": "low",
            "activity": "low",
            "opens_7d_actual": (s.get("activity_7d") or {}).get("opens_7d"),
            "note": "AI未单独返回，已按快照补齐",
        })
        have.add(key)
    per_strategy.sort(key=lambda x: (
        0 if x.get("can_open") else 1,
        -(x.get("weekly_opens_high") or 0),
        x.get("strategy_key") or "",
    ))

    assumptions = list(cg.get("key_assumptions") or [])
    if not assumptions:
        assumptions = list(baseline.get("key_assumptions") or []) or [
            "波动率大致维持当前水平",
            "无极端宏观/政策冲击（系统暂无宏观日历硬数据）",
            "现有自动交易挂载策略集合不发生大规模增减",
        ]
    risks = list(cg.get("risk_warnings") or [])
    if not risks:
        risks = list(baseline.get("risk_warnings") or []) or [
            "重大数据发布或突发波动可能显著改变触发频率",
            "策略降级/暂停会降低周开仓次数",
            "市场结构突变导致适用环境消失",
        ]
    summary = cg.get("summary_zh") or baseline.get("summary_zh") or ""

    report = {
        "schema": "qiyu_system_forecast_v1",
        "generated_at": _now(),
        "snapshot_at": snapshot.get("generated_at"),
        "active_strategy_count": snapshot.get("active_strategy_count"),
        "can_open_strategy_count": snapshot.get("can_open_strategy_count"),
        "paused_strategy_count": snapshot.get("paused_strategy_count"),
        "overall": {
            "daily_opens_expected": None if daily is None else round(daily, 2),
            "weekly_opens_expected": None if weekly is None else round(weekly, 2),
            "daily_opens_range": daily_range,
            "weekly_opens_range": weekly_range,
            "summary_zh": summary,
            "used_local_baseline": used_baseline,
        },
        "top_contributors": cg.get("top_contributors") or baseline.get("top_contributors") or [],
        "per_strategy": per_strategy,
        "key_assumptions": assumptions,
        "risk_warnings": risks,
        "ai_status": {
            "deepseek_ok": bool((ai_bundle.get("deepseek") or {}).get("ok")),
            "qwen_ok": bool((ai_bundle.get("qwen") or {}).get("ok")),
            "chatgpt_ok": bool((ai_bundle.get("glm") or {}).get("ok")),
            "all_ok": bool(ai_bundle.get("all_ok")),
            "used_local_baseline": used_baseline,
            "errors": {
                p: (ai_bundle.get(p) or {}).get("error")
                for p in PROVIDERS
                if not (ai_bundle.get(p) or {}).get("ok")
            },
        },
        "raw_ai": {
            "deepseek": ds,
            "qwen": qw,
            "glm": cg if not used_baseline else {"note": "chatgpt_failed_used_baseline"},
            "local_baseline": {
                "daily_opens_expected": baseline.get("daily_opens_expected"),
                "weekly_opens_expected": baseline.get("weekly_opens_expected"),
            },
        },
    }
    # Normalize digest: grade counts replace pause wording; keep weekly narrative.
    report["overall"]["summary_zh"] = compose_forecast_summary_zh(
        report, raw_summary=summary)
    report["overall"]["weekly_contributors_zh"] = format_weekly_contributors_line(
        report)
    report["openable_grade_counts"] = _openable_grade_counts(report)
    report = apply_statistical_frequency_overlay(report, snapshot, ai_bundle)
    # Recompose summary after statistical numbers overwrite AI counts.
    report["overall"]["summary_zh"] = compose_forecast_summary_zh(
        report, raw_summary=(report.get("overall") or {}).get("ai_narrative_zh")
        or summary)
    report["overall"]["weekly_contributors_zh"] = format_weekly_contributors_line(
        report)
    report["openable_grade_counts"] = _openable_grade_counts(report)
    return report


def apply_statistical_frequency_overlay(report, snapshot, ai_bundle=None):
    """Numbers = statistical baseline × regime; AI text is explain-only."""
    try:
        import auto_trade_expectancy_metrics as exp
    except Exception as exc:
        report.setdefault("overall", {})["statistical_overlay_error"] = str(exc)
        return report

    regime = exp.regime_factor_from_sensors()
    ai_bundle = ai_bundle or {}
    ai_notes = {}
    for provider in ("deepseek", "qwen", "glm"):
        parsed = ((ai_bundle.get(provider) or {}).get("parsed") or {})
        ai_notes[provider] = {
            "ok": bool((ai_bundle.get(provider) or {}).get("ok")),
            "summary": parsed.get("summary_zh") or parsed.get("market_regime")
            or parsed.get("notes"),
        }

    # Preserve AI narrative separately so it never silently becomes the count.
    overall = report.setdefault("overall", {})
    overall["ai_narrative_zh"] = overall.get("summary_zh")
    overall["frequency_method"] = "statistical_baseline_plus_regime"
    overall["ai_role"] = "explain_only"
    overall["regime_factor"] = regime.get("regime_factor")
    overall["regime_basis"] = regime.get("basis")

    weekly_sum = 0.0
    weekly_lo = 0.0
    weekly_hi = 0.0
    per = []
    meta = {}
    for s in (snapshot.get("strategies") or []):
        meta[s.get("strategy_key")] = s

    existing = {row.get("strategy_key"): row for row in (report.get("per_strategy") or [])
                if isinstance(row, dict)}

    for s in (snapshot.get("strategies") or []):
        key = s.get("strategy_key")
        item = dict(existing.get(key) or {})
        freq = exp.statistical_frequency_forecast(
            s.get("symbol"), s.get("timeframe"), key,
            can_open=bool(s.get("can_open")), regime=regime)
        funnel = exp.activity_funnel(
            s.get("symbol"), s.get("timeframe"), key, days=7)
        block = exp.build_expectancy_block(
            s.get("symbol"), s.get("timeframe"), key,
            position_ratio=_num(s.get("max_position_ratio"), 0.30) or 0.30,
            leverage=20.0,
            ai_wr_pct=_num(s.get("ai_theoretical_wr_avg")),
        )
        exp.attach_monthly_expectancy(block, freq.get("expected_weekly_fills"))
        freq_class = exp.classify_frequency(
            freq.get("expected_weekly_fills"), block.get("credibility"))

        freq_method = str(freq.get("method") or "")
        # Do not sum weak_prior / insufficient evidence into fillable weekly opens.
        countable = freq_method not in (
            "weak_prior_new_mount",
            "insufficient_frequency_evidence",
            "missing",
            "missing_physical_span",
            "missing_2y_sample",
        ) and freq.get("expected_weekly_fills") is not None
        w = (_num(freq.get("expected_weekly_fills"), 0.0) or 0.0) if countable else 0.0
        if countable:
            weekly_sum += w
        interval = freq.get("weekly_interval") or [0, 0]
        lo = max(0, int(interval[0] or 0)) if countable else 0
        hi = max(lo, int(interval[1] or 0)) if countable else 0
        if countable:
            weekly_lo += lo
            weekly_hi += hi

        item.update({
            "strategy_key": key,
            "strategy_name": item.get("strategy_name") or s.get("strategy_name") or key,
            "grade": s.get("grade"),
            "symbol": s.get("symbol"),
            "timeframe": s.get("timeframe"),
            "can_open": s.get("can_open"),
            "pause_new_entries": s.get("pause_new_entries"),
            "max_position_ratio": s.get("max_position_ratio"),
            "ai_theoretical_wr_avg": s.get("ai_theoretical_wr_avg"),
            "mechanism_family": block.get("mechanism_family"),
            "weekly_opens_low": lo if countable else None,
            "weekly_opens_high": hi if countable else None,
            "weekly_opens_expected": w if countable else None,
            "expected_weekly_fills": w if countable else None,
            "expected_daily_fills": freq.get("expected_daily_fills") if countable else None,
            "daily_opens_expected": freq.get("expected_daily_fills") if countable else None,
            "daily_opens_range": (
                [round(lo / 7.0, 3), round(hi / 7.0, 3)] if countable else None
            ),
            "opens_7d_actual": funnel.get("fills"),
            "funnel_7d": funnel,
            "frequency_class": freq_class,
            "frequency_method": freq_method,
            "frequency_countable": countable,
            "span_days": freq.get("span_days"),
            "span_source": freq.get("span_source"),
            "sample_2y_ok": freq.get("sample_2y_ok"),
            "regime_factor": freq.get("regime_factor"),
            "baseline_weekly_fills": freq.get("baseline_weekly_fills"),
            "calibrated_expected_win_rate_pct": (
                (block.get("calibrated_expected_win_rate_pct") or {}).get("value")),
            "net_expectancy_equity_pct": (
                (block.get("net_expectancy_equity_pct") or {}).get("value")),
            "net_expectancy_margin_pct": (
                (block.get("net_expectancy_margin_pct") or {}).get("value")),
            "metric_status": {
                "win_rate": (block.get("calibrated_expected_win_rate_pct") or {}).get(
                    "metric_status"),
                "expectancy": (block.get("net_expectancy_equity_pct") or {}).get(
                    "metric_status"),
            },
            "confidence": (
                "high" if (block.get("credibility") or 0) >= 0.7
                else ("mid" if (block.get("credibility") or 0) >= 0.35 else "low")),
            "activity": (
                "high" if w >= 3 else ("mid" if w >= 1 else "low")),
            "ai_explain": {
                "deepseek": item.get("deepseek_basis"),
                "qwen": item.get("qwen_basis"),
                "glm": item.get("note"),
                "role": "explain_only_not_numeric_source",
            },
            "from_baseline": True,
            "note": (
                "统计基线×regime(%.2f)；AI仅解释。漏斗 信号/尝试/成交/平仓/阻断=%s/%s/%s/%s/%s"
                % (freq.get("regime_factor") or 1.0,
                   funnel.get("signals"), funnel.get("attempts"),
                   funnel.get("fills"), funnel.get("exits"),
                   funnel.get("blocked"))
            ),
        })
        try:
            import auto_trade_strategy_titles as titles
            item["strategy_title"] = titles.short_strategy_title(
                key, item.get("strategy_name"))
            item["strategy_name"] = item["strategy_title"]
        except Exception:
            item["strategy_title"] = item.get("strategy_name") or key
        per.append(item)

    per.sort(key=lambda x: (
        0 if x.get("can_open") else 1,
        -(x.get("weekly_opens_expected") or 0),
        x.get("strategy_key") or "",
    ))
    tops = [x for x in per if x.get("can_open")][:5]
    total = weekly_sum or 0.0
    top_contributors = []
    for t in tops:
        share = round(100.0 * (t.get("weekly_opens_expected") or 0) / total, 1) if total else 0
        top_contributors.append({
            "strategy_key": t.get("strategy_key"),
            "weekly_opens_expected": t.get("weekly_opens_expected"),
            "share_pct": share,
            "mechanism_family": t.get("mechanism_family"),
        })
        t["share_pct"] = share
        t["forecast_contribution"] = share

    overall["daily_opens_expected"] = round(weekly_sum / 7.0, 2)
    overall["weekly_opens_expected"] = round(weekly_sum, 2)
    overall["daily_opens_range"] = [
        round(weekly_lo / 7.0, 2), round(weekly_hi / 7.0, 2)]
    overall["weekly_opens_range"] = [int(weekly_lo), int(weekly_hi)]
    overall["used_local_baseline"] = True
    overall["used_statistical_baseline"] = True
    overall["ai_explain_bundle"] = ai_notes

    report["per_strategy"] = per
    report["top_contributors"] = top_contributors
    report["schema"] = "qiyu_system_forecast_v2_statistical"
    try:
        gap = exp.build_frequency_gap_report(per)
        report["frequency_gap"] = gap.get("pool")
        report["mechanism_families"] = gap.get("mechanism_families")
        report["creation_brief"] = gap.get("creation_brief")
    except Exception as exc:
        report["frequency_gap_error"] = str(exc)
    return report


def format_report_text(report, full=True):
    overall = report.get("overall") or {}
    gap = report.get("frequency_gap") or {}
    lines = [
        "【系统规划预测】",
        "生成时间: %s" % report.get("generated_at"),
        "自动交易挂载策略: %s（可开仓 %s / 暂停 %s）"
        % (report.get("active_strategy_count"),
           report.get("can_open_strategy_count"),
           report.get("paused_strategy_count")),
        "频率方法: 统计基线×regime（AI仅解释，不作为次数来源）",
        "",
        "1) 总体预测",
        "  预计日均成交约 %s 次，周总成交约 %s 次。"
        % (overall.get("daily_opens_expected"), overall.get("weekly_opens_expected")),
    ]
    if overall.get("daily_opens_range") or overall.get("weekly_opens_range"):
        lines.append("  日区间 %s · 周区间 %s"
                     % (overall.get("daily_opens_range"),
                        overall.get("weekly_opens_range")))
    if gap:
        lines.append(
            "  组合缺口: 日目标0.5–1.0(现%s, gap=%s) · 周目标3.5–7(现%s, gap=%s) · %s"
            % (gap.get("expected_daily_fills"), gap.get("gap_daily_to_band"),
               gap.get("expected_weekly_fills"), gap.get("gap_weekly_to_band"),
               gap.get("status")))
    if overall.get("summary_zh"):
        lines.append("  %s" % overall.get("summary_zh"))
    lines.extend(["", "2) 分策略预测（可开仓优先）"])
    try:
        from auto_trade_strategy_titles import strategy_display_name as _title
    except Exception:
        def _title(k, *a, **k2):
            return k
    for row in (report.get("per_strategy") or [])[:40]:
        flag = "可开" if row.get("can_open") else "暂停"
        funnel = row.get("funnel_7d") or {}
        try:
            import auto_trade_strategy_titles as titles
            card = titles.format_live_strategy_card(
                row.get("strategy_key"),
                row.get("strategy_name"),
                grade=row.get("grade"),
                max_position_ratio=row.get("max_position_ratio"),
                ai_theoretical_wr_avg=row.get("ai_theoretical_wr_avg"),
            )
            indented = "\n".join("    " + ln for ln in card.split("\n"))
            lines.append(
                "  · [%s]\n%s\n    周成交 %s-%s(期望%s) · 家族=%s · 漏斗 S/A/F/E/B=%s/%s/%s/%s/%s"
                % (flag, indented,
                   row.get("weekly_opens_low"), row.get("weekly_opens_high"),
                   row.get("weekly_opens_expected"),
                   row.get("mechanism_family") or "-",
                   funnel.get("signals"), funnel.get("attempts"),
                   funnel.get("fills"), funnel.get("exits"),
                   funnel.get("blocked"))
            )
        except Exception:
            shown = _title(row.get("strategy_key")) or row.get("strategy_key")
            lines.append(
                "  · [%s] %s [%s/%s %s] 周成交 %s-%s"
                % (flag, shown,
                   row.get("symbol"), row.get("timeframe"), row.get("grade"),
                   row.get("weekly_opens_low"), row.get("weekly_opens_high"))
            )
    lines.extend(["", "3) 关键假设"])
    for a in (report.get("key_assumptions") or [])[:8]:
        lines.append("  · %s" % a)
    lines.extend(["", "4) 风险提示"])
    for r in (report.get("risk_warnings") or [])[:8]:
        lines.append("  · %s" % r)
    if not full:
        lines.append("")
        lines.append("完整报告请打开网站 /forecast 查看。")
    ai = report.get("ai_status") or {}
    lines.append("")
    lines.append("三AI状态(解释-only): DS=%s QW=%s CG=%s"
                 % (ai.get("deepseek_ok"), ai.get("qwen_ok"), ai.get("chatgpt_ok")))
    return "\n".join(lines)


def _openable_grade_counts(report):
    """Count S/A/B/C among currently openable strategies; skip deleted/other."""
    counts = {"S": 0, "A": 0, "B": 0, "C": 0}
    for row in (report.get("per_strategy") or []):
        if not row.get("can_open"):
            continue
        g = str(row.get("grade") or "").strip().upper()
        if g in counts:
            counts[g] += 1
    return counts


def _grade_count_phrase(counts):
    return "%s个s级策略 %s个a级策略 %s个b级策略 %s个c级策略" % (
        counts.get("S", 0),
        counts.get("A", 0),
        counts.get("B", 0),
        counts.get("C", 0),
    )


def _strategy_display_name(key):
    try:
        from auto_trade_strategy_titles import strategy_display_name
        return strategy_display_name(key)
    except Exception:
        return key


def format_weekly_contributors_line(report):
    tops = report.get("top_contributors") or []
    top_txt = ", ".join(
        "%s(≈%s)" % (
            _strategy_display_name(t.get("strategy_key")),
            t.get("weekly_opens_expected"),
        )
        for t in tops[:3]
    ) or "无"
    return "每周主要贡献: %s" % top_txt


_SUMMARY_PREFIX_RE = re.compile(
    r"^当前共有\d+个可开仓策略[，,]"
    r"(?:"
    r"\d+个暂停策略[。.\s]*|"
    r"(?:\d+个[sSaAbBcC]级策略\s*)+"
    r")?"
)


def _extract_forecast_narrative(summary_zh):
    """Keep the『预计本周…』narrative; drop old pause / grade prefixes."""
    raw = (summary_zh or "").strip()
    if not raw:
        return ""
    raw = _SUMMARY_PREFIX_RE.sub("", raw).strip()
    raw = re.sub(r"^本地基线[：:].*?[，,]\s*", "", raw).strip()
    # Prefer from 预计本周 if present mid-string after leftover junk
    m = re.search(r"预计本周.*", raw)
    if m:
        return m.group(0).strip()
    return raw


def _qualitative_tail_from_narrative(raw):
    """Keep AI qualitative prose; drop stale numeric『预计本周…』clauses."""
    text = (raw or "").strip()
    if not text:
        return ""
    text = _SUMMARY_PREFIX_RE.sub("", text).strip()
    text = re.sub(r"^本地基线[：:].*?[，,]\s*", "", text).strip()
    text = re.sub(
        r"^当前共有\d+个可开仓策略[，,].*?c级策略\s*",
        "", text, flags=re.I).strip()
    text = re.sub(
        r"预计本周总开仓约[\d.]+\s*次（日均约[\d.]+\s*次）[，,]?\s*",
        "", text)
    text = re.sub(
        r"预计本周总开仓约[\d.]+\s*次[，,。.\s]*",
        "", text)
    return text.strip()


def compose_forecast_summary_zh(report, raw_summary=None):
    """Body sentence: openable count + s/a/b/c grades + weekly narrative."""
    overall = report.get("overall") or {}
    n = report.get("can_open_strategy_count")
    if n is None:
        n = sum(1 for r in (report.get("per_strategy") or []) if r.get("can_open"))
    grades = _grade_count_phrase(_openable_grade_counts(report))
    src = raw_summary if raw_summary is not None else overall.get("summary_zh")
    weekly = overall.get("weekly_opens_expected")
    daily = overall.get("daily_opens_expected")
    if daily is None and weekly is not None:
        try:
            daily = float(weekly) / 7.0
        except Exception:
            daily = None
    tail = _qualitative_tail_from_narrative(src)
    if weekly is not None:
        wtxt = round(float(weekly), 1)
        if daily is not None:
            count = "预计本周总开仓约%s次（日均约%s次）" % (
                wtxt, round(float(daily), 1))
        else:
            count = "预计本周总开仓约%s次。" % wtxt
    else:
        count = "预计本周总开仓待估。"
    narrative = "%s，%s" % (count, tail) if tail else count
    if narrative.startswith("预计"):
        return "当前共有%s个可开仓策略，%s %s" % (n, grades, narrative)
    return "当前共有%s个可开仓策略，%s。%s" % (n, grades, narrative)


def format_wx_summary(report, batch_time=None):
    """Wx summary: statistical counts + top contributors; AI prose explain-only."""
    overall = report.get("overall") or {}
    body = overall.get("summary_zh") or compose_forecast_summary_zh(report)
    stamp = batch_time or report.get("generated_at") or _now()
    lines = [
        "【系统交易频率预测·摘要】",
        "挂载 %s｜可开 %s｜暂停 %s"
        % (report.get("active_strategy_count"),
           report.get("can_open_strategy_count"),
           report.get("paused_strategy_count")),
        "预计日均成交 %s｜周总 %s"
        % (overall.get("daily_opens_expected"),
           overall.get("weekly_opens_expected")),
        format_weekly_contributors_line(report),
        body,
        "完整报告: http://64.176.47.192:8080/forecast",
        "时间: %s" % stamp,
    ]
    return "\n".join(lines)


def _append_history(report):
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    slim = {
        "generated_at": report.get("generated_at"),
        "overall": report.get("overall"),
        "active_strategy_count": report.get("active_strategy_count"),
        "per_strategy": report.get("per_strategy"),
        "key_assumptions": report.get("key_assumptions"),
        "risk_warnings": report.get("risk_warnings"),
        "ai_status": report.get("ai_status"),
        "top_contributors": report.get("top_contributors"),
    }
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(slim, ensure_ascii=False) + "\n")
    # trim
    try:
        lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
        if len(lines) > HISTORY_KEEP:
            HISTORY_PATH.write_text(
                "\n".join(lines[-HISTORY_KEEP:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def load_latest():
    try:
        import auto_trade_forecast_closeout as closeout
        return closeout.load_latest_for_ui()
    except Exception:
        return _read(LATEST_PATH, {})


def load_history(limit=20):
    rows = []
    if not HISTORY_PATH.exists():
        return rows
    try:
        lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:
        return rows
    for line in lines[::-1]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
        if len(rows) >= limit:
            break
    return rows


def run_forecast(push_wx=True, batch_time=None):
    """Full pipeline: snapshot → 3AI(explain) → statistical numbers → store → Wx."""
    snapshot = collect_forecast_snapshot()
    ai_bundle = run_three_ai_forecast(snapshot)
    report = build_report(snapshot, ai_bundle)
    try:
        import auto_trade_forecast_closeout as closeout
        report = closeout.apply_closeout_layers(report)
        report = closeout.clear_stale_on_report(report)
    except Exception as exc:
        report["closeout_layer_error"] = str(exc)
    try:
        import auto_trade_expectancy_metrics as exp
        snap_path = exp.persist_forecast_snapshot(report, forever=True)
        report["snapshot_persist_path"] = snap_path
        calib = exp.update_weekly_calibration(report)
        try:
            import auto_trade_forecast_closeout as closeout
            calib = closeout.enrich_calibration(calib)
        except Exception:
            pass
        report["calibration"] = {
            "updated_at": calib.get("updated_at"),
            "rows": (calib.get("rows") or [])[-12:],
            "period_metrics": calib.get("period_metrics"),
            "calibration_stage": calib.get("calibration_stage"),
        }
        if calib.get("calibration_stage"):
            report["calibration_stage"] = calib.get("calibration_stage")
            if (calib.get("calibration_stage") or {}).get("ui_must_show_calibration_banner"):
                report["calibration_banner_zh"] = (
                    calib["calibration_stage"].get("banner_zh"))
        exp.compute_all_live_metrics()
        # Metrics path rewrites gap/creation files — restamp positive-E v2 handoff.
        try:
            import auto_trade_forecast_closeout as closeout
            closeout.persist_strategy_creation_frequency_input(report)
        except Exception:
            pass
    except Exception as exc:
        report["persist_error"] = str(exc)
        try:
            import auto_trade_forecast_closeout as closeout
            closeout.persist_strategy_creation_frequency_input(report)
        except Exception:
            pass
    report["snapshot"] = {
        "active_strategy_count": snapshot.get("active_strategy_count"),
        "can_open_strategy_count": snapshot.get("can_open_strategy_count"),
        "paused_strategy_count": snapshot.get("paused_strategy_count"),
        "strategies": [
            {
                "strategy_key": s.get("strategy_key"),
                "grade": s.get("grade"),
                "symbol": s.get("symbol"),
                "timeframe": s.get("timeframe"),
                "can_open": s.get("can_open"),
                "pause_new_entries": s.get("pause_new_entries"),
                "logic_brief": s.get("logic_brief"),
                "activity_7d": s.get("activity_7d"),
                "ai_theoretical_wr_avg": s.get("ai_theoretical_wr_avg"),
            }
            for s in (snapshot.get("strategies") or [])
        ],
        "macro_calendar": snapshot.get("macro_calendar"),
        "markets": snapshot.get("markets"),
    }
    _atomic(LATEST_PATH, report)
    _append_history(report)
    if batch_time:
        report["generated_at"] = batch_time
        report.setdefault("overall", {})["batch_time"] = batch_time
    _atomic(STATE_PATH, {
        "last_run_at": _now(),
        "last_all_ok": bool((report.get("ai_status") or {}).get("all_ok")),
        "last_weekly_opens_expected": (report.get("overall") or {}).get(
            "weekly_opens_expected"),
    })
    wx_result = None
    if push_wx:
        overall = report.get("overall") or {}
        if overall.get("weekly_opens_expected") is None and overall.get("daily_opens_expected") is None:
            wx_result = {"ok": False, "sent": False, "error": "skip_wx_empty_forecast"}
        else:
            wx_result = _wx(format_wx_summary(report, batch_time=batch_time),
                            kind="system_forecast",
                            meta={
                                "generated_at": report.get("generated_at"),
                                "weekly": overall.get("weekly_opens_expected"),
                                "mounted": report.get("active_strategy_count"),
                                "can_open": report.get("can_open_strategy_count"),
                            })
    return {
        "ok": True,
        "report": report,
        "wx": wx_result,
        "text": format_report_text(report, full=True),
    }


def main():
    parser = argparse.ArgumentParser(description="系统规划预测")
    parser.add_argument("--run", action="store_true", help="执行完整预测并推送")
    parser.add_argument("--no-wx", action="store_true", help="不推送微信")
    parser.add_argument("--snapshot", action="store_true", help="仅输出快照")
    parser.add_argument("--latest", action="store_true", help="打印最新报告")
    parser.add_argument("--history", type=int, default=0, help="打印最近N条历史")
    args = parser.parse_args()
    if args.snapshot:
        print(json.dumps(collect_forecast_snapshot(), ensure_ascii=False,
                         indent=2, default=str))
        return 0
    if args.latest:
        print(json.dumps(load_latest(), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.history:
        print(json.dumps(load_history(args.history), ensure_ascii=False,
                         indent=2, default=str))
        return 0
    if args.run:
        out = run_forecast(push_wx=not args.no_wx)
        report = out.get("report") or {}
        overall = report.get("overall") or {}
        usable = overall.get("weekly_opens_expected") is not None
        print(json.dumps({
            "ok": out.get("ok"),
            "generated_at": report.get("generated_at"),
            "overall": overall,
            "active_strategy_count": report.get("active_strategy_count"),
            "can_open_strategy_count": report.get("can_open_strategy_count"),
            "paused_strategy_count": report.get("paused_strategy_count"),
            "ai_status": report.get("ai_status"),
            "wx": out.get("wx"),
            "text": out.get("text"),
        }, ensure_ascii=False, indent=2, default=str))
        if usable and (report.get("ai_status") or {}).get("all_ok"):
            return 0
        if usable:
            return 0  # baseline-filled still usable
        return 2
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
