# -*- coding: utf-8 -*-
"""Weekly adaptive-engine observation rollup (read-only metrics).

Does not change gates.  Reads existing audit/state files and writes
auto_trade/adaptive_week_observe.json for the 'run one trading week' review.
"""
from __future__ import print_function

from datetime import datetime, timedelta
from pathlib import Path
import json
import os
import tempfile

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO = ROOT / "auto_trade"
OUT = AUTO / "adaptive_week_observe.json"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


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


def _tail_jsonl(path, max_lines=5000):
    p = Path(path)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _since_days(days=7):
    return datetime.now() - timedelta(days=int(days))


def _parse_time(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(s)[:19], fmt)
        except Exception:
            continue
    return None


def breath_metrics(days=7):
    rows = _tail_jsonl(AUTO / "strategy_breath_audit.jsonl")
    cutoff = _since_days(days)
    changes = []
    for row in rows:
        t = _parse_time(row.get("time") or (row.get("state") or {}).get("updated_at"))
        if t and t < cutoff:
            continue
        st = row.get("state") or row
        if st.get("changed"):
            outs = st.get("outputs") or {}
            changes.append({
                "time": row.get("time"),
                "soft_window_hours": outs.get("soft_window_hours"),
                "missing_leaf_tolerance": outs.get("missing_leaf_tolerance"),
                "overlay_dd_retract": outs.get("overlay_dd_retract"),
                "smoothed_score": st.get("smoothed_score"),
            })
    state = _read(AUTO / "strategy_breath_state.json", {})
    return {
        "adjustments": len(changes),
        "recent_changes": changes[-20:],
        "current": state.get("outputs"),
        "smoothed_score": state.get("smoothed_score"),
    }


def corridor_metrics():
    q = _read(AUTO / "strategy_gene_corridor_queue.json", {})
    items = q.get("items") or []
    counts = {}
    latencies = []
    for item in items:
        st = str(item.get("stage") or "?")
        counts[st] = counts.get(st, 0) + 1
        hist = item.get("history") or []
        if len(hist) >= 2 and item.get("grade"):
            t0 = _parse_time(hist[0].get("at"))
            t1 = _parse_time(hist[-1].get("at"))
            if t0 and t1:
                latencies.append((t1 - t0).total_seconds() / 3600.0)
    avg_h = round(sum(latencies) / float(len(latencies)), 2) if latencies else None
    return {
        "candidates": len(items),
        "stage_counts": counts,
        "graded": sum(1 for i in items if i.get("grade")),
        "avg_hours_draft_to_latest_stage": avg_h,
        "probe_or_shadow": sum(
            1 for i in items
            if i.get("stage") in ("probe_armed", "shadow_observe")),
    }


def niche_metrics():
    report = _read(AUTO / "niche_map_report.json", {})
    return report.get("summary") or {}


def arbitration_metrics(days=7):
    rows = _tail_jsonl(AUTO / "ai_arbitration_audit.jsonl")
    cutoff = _since_days(days)
    modes = {}
    degraded = 0
    holds = 0
    for row in rows:
        t = _parse_time(row.get("time"))
        if t and t < cutoff:
            continue
        r = row.get("result") or row
        mode = str(r.get("mode") or "?")
        modes[mode] = modes.get(mode, 0) + 1
        if mode == "degraded_pair":
            degraded += 1
        if mode in ("human_hold", "failed") or r.get("notify_human"):
            holds += 1
    scores = _read(AUTO / "ai_provider_availability.json", {})
    return {
        "mode_counts": modes,
        "degraded_pair": degraded,
        "human_holds": holds,
        "provider_scores": scores.get("scores") or {},
    }


def cost_metrics():
    survival = _read(AUTO / "capital_survival_report.json", {})
    budgets = survival.get("budgets") or {}
    profiles = survival.get("profiles") or {}
    return {
        "target_point": survival.get("target_point"),
        "daily_ai_budget_usd": budgets.get("daily_ai_budget_usd"),
        "thrifty_cost_usd": (profiles.get("thrifty") or {}).get("cost", {}).get("total_usd"),
        "current_cost_usd": (profiles.get("current") or {}).get("cost", {}).get("total_usd"),
        "note": "actual token $ not metered yet — compare call-count budgets when ledger grows",
    }


def open_frequency_proxy(days=7):
    """Count verified open notifies in formal_notification_audit.log."""
    path = AUTO / "formal_notification_audit.log"
    if not path.exists():
        return {"opens_sent_verified": 0}
    cutoff = _since_days(days)
    n = 0
    samples = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]:
        if "strategy_opened_sent_verified" not in line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        # message embeds open time; use file mtime proxy via meta if present
        msg = str(row.get("message") or "")
        n += 1
        samples.append(msg[:120])
    return {
        "opens_sent_verified_tail": n,
        "samples": samples[-10:],
        "window_note": "tail scan of audit log; pair with OKX fills for truth",
    }


def build(days=7):
    report = {
        "ok": True,
        "schema": "qiyu_adaptive_week_observe_v1",
        "generated_at": _now(),
        "window_days": days,
        "goal": {
            "hold_hours_target": 24,
            "opens_per_day_target": 3,
            "gaps_acknowledged": [
                "frequency_still_market_dependent",
                "niche_coverage_low",
                "hackathon_tp_manual_fix",
                "small_equity_thin_absolute_pnl",
            ],
        },
        "breath": breath_metrics(days),
        "gene_corridor": corridor_metrics(),
        "niche": niche_metrics(),
        "ai_arbitration": arbitration_metrics(days),
        "cost": cost_metrics(),
        "opens": open_frequency_proxy(days),
        "policy": "observe only — no new adaptive modules this week",
    }
    _atomic(OUT, report)
    return report


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
