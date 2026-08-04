# -*- coding: utf-8 -*-
"""System health for 3AI ops role: rule alerts + optional daily AI digest.

Hooked from human-confirm --tick. Alerts via WxPusher on anomalies.
Daily AI comments are cached for the 23:59 report enhancement.
"""
from __future__ import print_function

import json
import os
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
HEALTH_STATE = AUTO_DIR / "system_health_ai_state.json"
HEALTH_CACHE = AUTO_DIR / "system_health_ai_daily_cache.json"
ALERT_COOLDOWN_SEC = 1800
AI_DAILY_ONCE = True


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today():
    return date.today().isoformat()


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


def _wx(message, kind="system_health_alert", meta=None):
    try:
        import auto_trade_formal_notify as notify
        return notify.send_message(message, kind=kind, meta=meta or {})
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _daemon_counts():
    try:
        out = subprocess.check_output(
            "systemctl list-units --type=service --state=running --no-legend "
            "'qiyu-formal-auto-trade-*' | wc -l",
            shell=True, universal_newlines=True, stderr=subprocess.DEVNULL)
        running = int(str(out).strip() or 0)
    except Exception:
        running = -1
    try:
        out = subprocess.check_output(
            "systemctl list-units --type=service --all --no-legend "
            "'qiyu-formal-auto-trade-*' | wc -l",
            shell=True, universal_newlines=True, stderr=subprocess.DEVNULL)
        total = int(str(out).strip() or 0)
    except Exception:
        total = -1
    return {"running": running, "configured": total}


def _critical_json_ok():
    paths = [
        AUTO_DIR / "strategy_runtime_controls.json",
        AUTO_DIR / "strategy_pending_human_confirm.json",
        AUTO_DIR / "daily_trade_report_state.json",
    ]
    bad = []
    for path in paths:
        if not path.exists():
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            bad.append("%s:%s" % (path.name, exc))
    return bad


def collect_health_snapshot():
    """Deterministic health facts for rules + AI/daily report."""
    daemons = _daemon_counts()
    pending = _read(AUTO_DIR / "strategy_pending_human_confirm.json", {"items": []})
    awaiting = [i for i in (pending.get("items") or [])
                if i.get("status") == "awaiting_confirm"]
    controls = _read(AUTO_DIR / "strategy_runtime_controls.json", {"assignments": {}})
    assignments = controls.get("assignments") or {}
    live = []
    paused = 0
    for aid, row in assignments.items():
        if row.get("pause_new_entries"):
            paused += 1
    # Prefer currently daemon-mounted strategies as the live roster source.
    mounted = []
    try:
        import auto_trade_system_forecast as forecast
        mounted = forecast.list_auto_trade_strategies() or []
    except Exception:
        mounted = []
    if mounted:
        for t in mounted:
            row = t.get("row") or {}
            grade_raw = str(t.get("lifecycle_grade") or "").lower()
            audit = str(row.get("audit_state") or "")
            if grade_raw in ("deleted", "replaced", "archived"):
                continue
            if audit in ("eliminated_pending_archive", "replaced_by_near_duplicate"):
                continue
            key = t.get("strategy_key")
            try:
                import auto_trade_strategy_titles as titles
                shown = titles.short_strategy_title(
                    key, t.get("strategy_name") or row.get("strategy_name"))
            except Exception:
                shown = key
            grade = t.get("lifecycle_grade") or row.get("max_grade")
            if audit == "read_only_shadow" or str(grade).lower() == "shadow":
                grade = "SHADOW"
            live.append({
                "aid": t.get("assignment_id"),
                "strategy_key": key,
                "grade": grade,
                "max_position_ratio": t.get("max_position_ratio"),
                "ai_theoretical_wr_avg": row.get("ai_theoretical_wr_avg"),
                "name": shown,
                "new_entries_allowed": bool(row.get("new_entries_allowed")),
                "pause_new_entries": bool(t.get("pause_new_entries")),
                "audit_state": audit,
                "can_open": bool(t.get("can_open")),
            })
    else:
        for aid, row in assignments.items():
            if not (row.get("human_confirmed") or row.get("human_confirm_pipeline")):
                continue
            grade_raw = str(row.get("lifecycle_grade") or "").lower()
            audit = str(row.get("audit_state") or "")
            if grade_raw in ("deleted", "replaced", "archived"):
                continue
            if audit in ("eliminated_pending_archive", "replaced_by_near_duplicate"):
                continue
            key = row.get("strategy_key") or str(aid).split("|")[-1]
            try:
                import auto_trade_strategy_titles as titles
                shown = titles.short_strategy_title(
                    key, row.get("strategy_name"))
            except Exception:
                shown = key
            pause = bool(row.get("pause_new_entries"))
            allowed = bool(row.get("new_entries_allowed"))
            live.append({
                "aid": aid,
                "strategy_key": key,
                "grade": row.get("lifecycle_grade") or row.get("max_grade"),
                "max_position_ratio": row.get("max_position_ratio"),
                "ai_theoretical_wr_avg": row.get("ai_theoretical_wr_avg"),
                "name": shown,
                "new_entries_allowed": allowed,
                "pause_new_entries": pause,
                "audit_state": row.get("audit_state"),
                "can_open": bool(allowed and not pause),
            })
    day = None
    try:
        import auto_trade_daily_report as daily
        day = daily.daily_records(limit=1)[0]
    except Exception as exc:
        day = {"error": str(exc)}
    bad_json = _critical_json_ok()
    factory = _read(AUTO_DIR / "strategy_creation_factory_state.json", {})
    factory_disabled = bool(
        factory.get("disabled")
        or factory.get("reason") == "disabled_ai_creation"
    )
    live_openable = sum(
        1 for x in live
        if x.get("can_open") and not x.get("pause_new_entries")
    )
    live_paused_mounted = sum(1 for x in live if x.get("pause_new_entries"))
    daemons_healthy = (
        int(daemons.get("running") or 0) > 0
        and int(daemons.get("running") or 0)
        >= max(1, int(daemons.get("configured") or 0) // 2)
    )
    open_count = (day or {}).get("open_count")
    close_count = (day or {}).get("close_count")
    try:
        open_n = int(open_count or 0)
        close_n = int(close_count or 0)
    except Exception:
        open_n, close_n = 0, 0
    if daemons_healthy:
        if open_n == 0 and close_n == 0:
            daily_activity_note = "今日尚未开平仓（不等于交易停运）"
        else:
            daily_activity_note = "今日已有开/平仓活动"
    else:
        daily_activity_note = "守护进程异常，交易活动需人工核验"
    issues = []
    if daemons.get("running") == 0 and daemons.get("configured", 0) > 0:
        issues.append("formal_daemons_all_down")
    if daemons.get("running", 0) >= 0 and daemons.get("configured", 0) > 0:
        if daemons["running"] < max(1, daemons["configured"] // 2):
            issues.append("formal_daemons_partial_down")
    if bad_json:
        issues.append("critical_json_corrupt")
    if isinstance(day, dict) and day.get("error"):
        issues.append("daily_ledger_error")
    # consecutive-stop hint from pending optimizer / monitor state
    hc_state = _read(AUTO_DIR / "human_confirm_pipeline_state.json", {})
    mon = hc_state.get("monitor") or {}
    if int(mon.get("demotions") or 0) >= 2:
        issues.append("multiple_demotions_recent_tick")
    # Explicit two-track ops context so AIs do not conflate factory vs live trade.
    ops_context = {
        "creation_factory": {
            "label": "策略创造工厂（仅负责造新策略，不是实盘交易）",
            "disabled": factory_disabled,
            "reason": factory.get("reason") or (
                "disabled_ai_creation" if factory_disabled else None),
            "meaning_if_disabled": (
                "仅表示不再用三AI自动创造新策略；正式自动交易可照常运行"
            ),
        },
        "formal_auto_trade": {
            "label": "正式自动交易 / Formal守护进程（实盘开平仓）",
            "daemons": daemons,
            "daemons_healthy": daemons_healthy,
            "live_mounted_strategies": len(live),
            "live_openable_strategies": live_openable,
            "live_mounted_paused": live_paused_mounted,
            "meaning_if_healthy_and_openable": (
                "实盘自动交易在运行；可开仓策略可按信号开仓"
            ),
        },
        "assignment_bookkeeping": {
            "paused_assignments_raw": paused,
            "note": (
                "paused_assignments 多为历史删除/影子赋值簿记，"
                "运维短评请优先看 live_openable_strategies / live_roster，"
                "勿把 raw paused 数解读为全体实盘暂停"
            ),
        },
        "daily_fills": {
            "open_count": open_count,
            "close_count": close_count,
            "note": daily_activity_note,
        },
    }
    return {
        "time": _now(),
        "day": _today(),
        "daemons": daemons,
        "daemons_healthy": daemons_healthy,
        "pending_awaiting": len(awaiting),
        "live_strategies": len(live),
        "live_openable_strategies": live_openable,
        "live_mounted_paused": live_paused_mounted,
        "paused_assignments": paused,
        "paused_assignments_note": (
            "簿记字段，含历史删除/影子行；运维请优先看 live_openable_strategies"
        ),
        "live_ai_wr": [
            {"name": x.get("name"), "grade": x.get("grade"),
             "strategy_key": x.get("strategy_key"),
             "max_position_ratio": x.get("max_position_ratio"),
             "ai_theoretical_wr_avg": x.get("ai_theoretical_wr_avg"),
             "new_entries_allowed": x.get("new_entries_allowed"),
             "pause_new_entries": x.get("pause_new_entries"),
             "can_open": x.get("can_open"),
             "audit_state": x.get("audit_state")}
            for x in live if x.get("ai_theoretical_wr_avg") is not None
        ],
        "live_roster": live,
        "daily": {
            "open_count": open_count,
            "close_count": close_count,
            "net_pnl_usdt": (day or {}).get("net_pnl_usdt"),
            "wins": (day or {}).get("wins"),
            "losses": (day or {}).get("losses"),
            "error": (day or {}).get("error"),
            "activity_note": daily_activity_note,
        },
        "bad_json": bad_json,
        "factory_disabled": factory_disabled,
        "creation_factory_disabled": factory_disabled,
        "ops_context": ops_context,
        "issues": issues,
        "severity": bool(issues),
    }


HEALTH_AI_PROMPT = """你是栖语自动交易系统的运维健康官。根据输入快照给一句中文健康裁决。
只输出JSON：{"verdict":"OK或WARN或CRITICAL","summary":"一句话中文","focus":["..."]}
不要编造未提供的数据。

【两条轨道，严禁混谈】
1) 策略创造工厂（creation_factory / factory_disabled）：只表示是否还用三AI自动造新策略。
   工厂禁用 ≠ 实盘交易停运。若要提工厂，必须写清「创造工厂已停用/禁用」，禁止说成「交易已停/全部任务暂停/待命全体」。
2) 正式自动交易（formal_auto_trade / daemons / live_roster）：才是实盘开平仓状态。

【裁决硬规则】
- 若 daemons_healthy=true（或 daemons.running 接近 configured）且 live_openable_strategies>0（或 live_roster 中有 can_open=true）：
  必须判定实盘自动交易在运行；禁止写「交易暂停/停止/待命全体/全部任务暂停/工厂已禁用导致无交易」。
  可写「创造工厂已停用」仅当明确指造策略工厂，且不得暗示实盘停运。
- paused_assignments 是历史簿记（常含已删/影子赋值），运维短评优先引用 live_openable_strategies / live_roster，不要用 raw paused 数暗示全体暂停。
- 今日 open_count/close_count=0 且守护正常时：只能写「今日尚未开平仓」，禁止写「无交易活动/无交易/交易停摆」。
- summary 应优先概括：守护进程、可开仓实盘策略数/名称要点、今日开平是否发生；工厂状态仅作次要澄清。"""


def _ai_health_one(provider, snapshot):
    import auto_trade_ai_consensus as ai
    cfg = ai._provider_config(provider)
    consent = ai.external_research_consent_status(provider, "final_review")
    if not consent.get("allowed") or not cfg.get("api_key"):
        return {"provider": provider, "ok": False, "verdict": "WARN",
                "summary": "AI不可用"}
    # Feed a compact ops-facing view so models do not overweight raw paused counts.
    ai_view = {
        "time": snapshot.get("time"),
        "day": snapshot.get("day"),
        "ops_context": snapshot.get("ops_context"),
        "daemons": snapshot.get("daemons"),
        "daemons_healthy": snapshot.get("daemons_healthy"),
        "live_openable_strategies": snapshot.get("live_openable_strategies"),
        "live_strategies": snapshot.get("live_strategies"),
        "live_mounted_paused": snapshot.get("live_mounted_paused"),
        "pending_awaiting": snapshot.get("pending_awaiting"),
        "paused_assignments_raw": snapshot.get("paused_assignments"),
        "paused_assignments_note": snapshot.get("paused_assignments_note"),
        "creation_factory_disabled": snapshot.get("creation_factory_disabled"),
        "factory_disabled": snapshot.get("factory_disabled"),
        "daily": snapshot.get("daily"),
        "live_roster": [
            {
                "name": x.get("name"),
                "grade": x.get("grade"),
                "can_open": x.get("can_open"),
                "pause_new_entries": x.get("pause_new_entries"),
                "new_entries_allowed": x.get("new_entries_allowed"),
                "audit_state": x.get("audit_state"),
            }
            for x in (snapshot.get("live_roster") or [])
        ],
        "issues": snapshot.get("issues"),
        "severity": snapshot.get("severity"),
    }
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": HEALTH_AI_PROMPT},
            {"role": "user", "content": ai.canonical_json({
                "snapshot": ai_view})},
        ],
    }
    if provider == "deepseek":
        body["response_format"] = {"type": "json_object"}
    if provider == "glm":
        body["max_completion_tokens"] = 400
    else:
        body["temperature"] = 0.1
        body["max_tokens"] = 300
    try:
        from urllib import request as urllib_request
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(
            cfg["url"], data=encoded,
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        with urllib_request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        content = (((raw.get("choices") or [{}])[0].get("message") or {})
                   .get("content"))
        parsed = ai._parse_content_json(content)
        return {
            "provider": provider, "ok": True,
            "verdict": str(parsed.get("verdict") or "WARN").upper(),
            "summary": parsed.get("summary") or "",
            "focus": parsed.get("focus") or [],
        }
    except Exception as exc:
        return {"provider": provider, "ok": False, "verdict": "WARN",
                "summary": "调用失败:%s" % exc}


def run_ai_health_digest(snapshot):
    pool = ThreadPoolExecutor(max_workers=3)
    try:
        futs = [pool.submit(_ai_health_one, p, snapshot)
                for p in ("deepseek", "qwen", "glm")]
        rows = [f.result() for f in futs]
    finally:
        pool.shutdown(wait=True)
    return {
        "day": _today(),
        "time": _now(),
        "reviews": rows,
        "natural_language": " / ".join(
            "%s:%s" % (r.get("provider"), r.get("summary") or r.get("verdict"))
            for r in rows
        ),
    }


def load_daily_health_cache():
    cache = _read(HEALTH_CACHE, {})
    if cache.get("day") == _today():
        return cache
    return {}


def run_health_tick(force_ai=False):
    """Rule alerts every tick; AI digest at most once/day (or on severity)."""
    snap = collect_health_snapshot()
    state = _read(HEALTH_STATE, {})
    now_ts = time.time()
    last_alert = float(state.get("last_alert_ts") or 0)
    alerted = False
    if snap.get("severity") and (now_ts - last_alert) >= ALERT_COOLDOWN_SEC:
        msg = (
            "【系统健康告警】\n"
            "问题: %s\n"
            "Formal守护进程: 运行 %s / 配置 %s\n"
            "待确认策略: %s · 在线确认策略: %s\n"
            "今日开/平: %s / %s · 净盈亏: %s\n"
            "损坏JSON: %s\n"
            "时间: %s"
            % (",".join(snap.get("issues") or []) or "-",
               (snap.get("daemons") or {}).get("running"),
               (snap.get("daemons") or {}).get("configured"),
               snap.get("pending_awaiting"), snap.get("live_strategies"),
               (snap.get("daily") or {}).get("open_count"),
               (snap.get("daily") or {}).get("close_count"),
               (snap.get("daily") or {}).get("net_pnl_usdt"),
               "; ".join(snap.get("bad_json") or []) or "无",
               _now())
        )
        _wx(msg, kind="system_health_alert", meta={"issues": snap.get("issues")})
        state["last_alert_ts"] = now_ts
        state["last_alert_at"] = _now()
        state["last_issues"] = snap.get("issues")
        alerted = True

    ai_ran = False
    cache = load_daily_health_cache()
    need_ai = force_ai or (
        AI_DAILY_ONCE and cache.get("day") != _today()
    ) or (snap.get("severity") and cache.get("day") != _today())
    # Only burn AI once per day unless forced; severity without cache still once.
    if need_ai and (force_ai or cache.get("day") != _today()):
        try:
            digest = run_ai_health_digest(snap)
            cache = {
                "day": _today(),
                "time": _now(),
                "snapshot_summary": {
                    "daemons": snap.get("daemons"),
                    "daemons_healthy": snap.get("daemons_healthy"),
                    "issues": snap.get("issues"),
                    "pending_awaiting": snap.get("pending_awaiting"),
                    "live_strategies": snap.get("live_strategies"),
                    "live_openable_strategies": snap.get(
                        "live_openable_strategies"),
                    "creation_factory_disabled": snap.get(
                        "creation_factory_disabled"),
                    "daily": snap.get("daily"),
                    "ops_context": snap.get("ops_context"),
                    "live_ai_wr": snap.get("live_ai_wr"),
                },
                "ai_digest": digest,
                "natural_language": digest.get("natural_language"),
            }
            _atomic(HEALTH_CACHE, cache)
            ai_ran = True
            # Wx only when AI says CRITICAL or any review WARN with severity
            verdicts = [str(r.get("verdict") or "").upper()
                        for r in (digest.get("reviews") or [])]
            if "CRITICAL" in verdicts or (
                    snap.get("severity") and "WARN" in verdicts):
                _wx(
                    "【三AI运维短评】\n%s\n时间: %s"
                    % (digest.get("natural_language"), _now()),
                    kind="system_health_ai_digest",
                    meta={"verdicts": verdicts},
                )
        except Exception as exc:
            cache = dict(cache or {})
            cache.update({
                "day": _today(), "time": _now(),
                "error": str(exc),
                "natural_language": "三AI巡检跳过: %s" % exc,
            })
            _atomic(HEALTH_CACHE, cache)

    state["updated_at"] = _now()
    state["last_snapshot"] = {
        "issues": snap.get("issues"),
        "daemons": snap.get("daemons"),
        "pending_awaiting": snap.get("pending_awaiting"),
    }
    _atomic(HEALTH_STATE, state)
    return {
        "ok": True,
        "alerted": alerted,
        "ai_ran": ai_ran,
        "issues": snap.get("issues"),
        "snapshot": snap,
        "daily_cache": load_daily_health_cache(),
    }


def format_daily_report_section():
    """Text block appended to 23:59 daily report."""
    snap = collect_health_snapshot()
    cache = load_daily_health_cache()
    lines = [
        "",
        "--- 系统健康与三AI运维 ---",
        "Formal守护: 运行 %s / 配置 %s" % (
            (snap.get("daemons") or {}).get("running"),
            (snap.get("daemons") or {}).get("configured")),
        "在线挂载策略: %s · 可开仓: %s · 待人工确认: %s" % (
            snap.get("live_strategies"),
            snap.get("live_openable_strategies"),
            snap.get("pending_awaiting")),
        "创造工厂: %s（≠实盘停运）· 赋值簿记暂停数: %s（历史/影子，勿当实盘暂停）" % (
            "已停用" if snap.get("creation_factory_disabled") else "启用中",
            snap.get("paused_assignments")),
        "今日开/平(同步): %s / %s · 净盈亏 %s · %s" % (
            (snap.get("daily") or {}).get("open_count"),
            (snap.get("daily") or {}).get("close_count"),
            (snap.get("daily") or {}).get("net_pnl_usdt"),
            (snap.get("daily") or {}).get("activity_note") or ""),
    ]
    if snap.get("issues"):
        lines.append("规则告警: %s" % ",".join(snap["issues"]))
    else:
        lines.append("规则告警: 无")
    if cache.get("natural_language"):
        lines.append("三AI运维短评: %s" % cache.get("natural_language"))
    else:
        lines.append("三AI运维短评: 三AI巡检跳过（本日尚未生成）")
    wr_rows = snap.get("live_ai_wr") or []
    roster = snap.get("live_roster") or wr_rows
    if roster:
        lines.append("在线策略:")
        try:
            import auto_trade_strategy_titles as titles
            # Prefer active/openable first, then shadow.
            ordered = sorted(
                roster,
                key=lambda r: (
                    0 if r.get("new_entries_allowed") else 1,
                    str(r.get("name") or ""),
                ),
            )
            cards = []
            for row in ordered[:20]:
                cards.append(titles.format_live_strategy_card(
                    row.get("strategy_key") or row.get("name"),
                    row.get("name"),
                    grade=row.get("grade"),
                    max_position_ratio=row.get("max_position_ratio"),
                    ai_theoretical_wr_avg=row.get("ai_theoretical_wr_avg"),
                ))
            lines.append("\n\n".join(cards))
        except Exception:
            lines.append("在线策略三AI理论胜率均值:")
            for row in wr_rows[:12]:
                lines.append("· %s [%s] %.1f%%" % (
                    row.get("name") or "-", row.get("grade") or "-",
                    float(row.get("ai_theoretical_wr_avg") or 0)))
    else:
        lines.append("在线策略: 暂无记录")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--tick", action="store_true")
    parser.add_argument("--force-ai", action="store_true")
    parser.add_argument("--section", action="store_true")
    args = parser.parse_args()
    if args.section:
        print(format_daily_report_section())
    else:
        print(json.dumps(run_health_tick(force_ai=args.force_ai),
                         ensure_ascii=False, indent=2, default=str))
