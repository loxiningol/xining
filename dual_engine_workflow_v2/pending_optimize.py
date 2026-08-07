# -*- coding: utf-8 -*-
"""策略待优化板块：人工/机器优化中的策略登记与展示。"""
from __future__ import print_function

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "auto_trade" / "strategy_pending_optimize" / "strategies.json"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_store():
    return {
        "ok": True,
        "updated_at": _now(),
        "note_zh": "策略待优化展示区：账户胜率 / 盈利单平均 / 周频 / 期望值E。",
        "strategies": [],
    }


def load_store():
    if not STORE.exists():
        data = _default_store()
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        data = _default_store()
    if not isinstance(data, dict):
        data = _default_store()
    data.setdefault("ok", True)
    data.setdefault("strategies", [])
    data.setdefault("note_zh", "策略待优化展示区：账户胜率 / 盈利单平均 / 周频 / 期望值E。")
    return data


def save_store(data):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    data = dict(data or {})
    data["ok"] = True
    data["updated_at"] = _now()
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def upsert_strategy(row):
    """Insert or update one pending-optimize strategy by id/title."""
    data = load_store()
    items = list(data.get("strategies") or [])
    row = dict(row or {})
    rid = str(row.get("id") or "").strip()
    title = str(row.get("title_zh") or row.get("name") or "").strip()
    if not rid and title:
        rid = "auto_%s" % abs(hash(title)) % (10 ** 10)
        row["id"] = rid
    if not rid:
        return {"ok": False, "error": "missing_id_or_title"}
    row["updated_at"] = _now()
    row.setdefault("active", True)
    row.setdefault("optimize_mode", "human")
    row.setdefault("optimize_mode_zh", "人工优化")
    replaced = False
    for i, old in enumerate(items):
        if not isinstance(old, dict):
            continue
        if str(old.get("id") or "") == rid or (
            title and str(old.get("title_zh") or "") == title
        ):
            merged = dict(old)
            merged.update(row)
            items[i] = merged
            replaced = True
            break
    if not replaced:
        items.append(row)
    data["strategies"] = items
    save_store(data)
    # Mirror into 质检器 so dual-pipe UI pass/fail boards stay current.
    try:
        from . import quality_inspector as qi
        metrics = row.get("metrics_2y") or {}
        qi.ingest({
            "id": rid,
            "title_zh": row.get("title_zh") or row.get("name") or rid,
            "symbol": row.get("symbol"),
            "timeframe": row.get("timeframe"),
            "direction": row.get("direction"),
            "source": row.get("source") or "pending_optimize",
            "expectancy_E": metrics.get("expectancy_E"),
            "weekly_open_freq": metrics.get("weekly_open_freq"),
            "mean_win_only_pct": metrics.get("mean_win_only_pct"),
        })
    except Exception:
        pass
    return {"ok": True, "id": rid, "replaced": replaced, "count": len(items)}


def status():
    """Website board payload."""
    data = load_store()
    rows = []
    for row in list(data.get("strategies") or []):
        if not isinstance(row, dict):
            continue
        if row.get("active") is False:
            continue
        metrics = row.get("metrics_2y") or {}
        rows.append({
            "id": row.get("id"),
            "title_zh": row.get("title_zh") or row.get("name") or row.get("id"),
            "symbol": row.get("symbol"),
            "timeframe": row.get("timeframe"),
            "direction": row.get("direction"),
            "direction_zh": row.get("direction_zh") or (
                "开多" if str(row.get("direction") or "").lower() == "long" else (
                    "开空" if str(row.get("direction") or "").lower() == "short" else ""
                )
            ),
            "optimize_mode": row.get("optimize_mode") or "human",
            "optimize_mode_zh": row.get("optimize_mode_zh") or "人工优化",
            "account_win_rate_pct": metrics.get("account_win_rate_pct"),
            "mean_win_only_pct": metrics.get("mean_win_only_pct"),
            "weekly_open_freq": metrics.get("weekly_open_freq"),
            "expectancy_E": metrics.get("expectancy_E"),
            "sample_trades": metrics.get("n_trades"),
            "span_days": metrics.get("span_days"),
            "updated_at": row.get("updated_at") or data.get("updated_at"),
        })
    return {
        "ok": True,
        "updated_at": data.get("updated_at") or _now(),
        "note_zh": data.get("note_zh"),
        "count": len(rows),
        "strategies": rows,
    }
