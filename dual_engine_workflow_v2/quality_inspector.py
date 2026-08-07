# -*- coding: utf-8 -*-
"""质检器 — 双管道汇流后的唯一简化复核。

门槛（四 AI 平均数）：
  - E > 0
  - 周开仓频率 > 0.5
通过 → 质检合格表；否则 → 质检不合格表。
不再展示四阶段复核卡片。
"""
from __future__ import print_function

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "auto_trade" / "quality_inspector" / "board.json"

GATE_E_STRICT_GT = 0.0
GATE_WEEKLY_STRICT_GT = 0.5


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _f(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _default_store():
    return {
        "ok": True,
        "updated_at": _now(),
        "schema": "qiyu_quality_inspector_v1",
        "gate_zh": "四AI平均数：E>0 且 周开仓频率>0.5",
        "gate": {
            "expectancy_E_strict_gt": GATE_E_STRICT_GT,
            "weekly_open_freq_strict_gt": GATE_WEEKLY_STRICT_GT,
            "source": "four_ai_average",
        },
        "items": [],
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
    data.setdefault("items", [])
    data.setdefault("gate_zh", "四AI平均数：E>0 且 周开仓频率>0.5")
    data.setdefault("schema", "qiyu_quality_inspector_v1")
    return data


def save_store(data):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    data = dict(data or {})
    data["ok"] = True
    data["updated_at"] = _now()
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def evaluate_gate(metrics=None):
    """Return pass/fail for simplified 质检器 gate."""
    m = dict(metrics or {})
    e = _f(m.get("expectancy_E"))
    if e is None:
        e = _f((m.get("expectancy") or {}).get("E") if isinstance(m.get("expectancy"), dict) else None)
    weekly = _f(m.get("weekly_open_freq"))
    if weekly is None:
        weekly = _f(m.get("weekly_opens"))
    mean_win = _f(m.get("mean_win_only_pct"))
    reasons = []
    if e is None:
        reasons.append("expectancy_E_unavailable")
    elif e <= GATE_E_STRICT_GT + 1e-15:
        reasons.append("expectancy_E_not_gt_0(E=%s)" % e)
    if weekly is None:
        reasons.append("weekly_open_freq_unavailable")
    elif weekly <= GATE_WEEKLY_STRICT_GT + 1e-15:
        reasons.append("weekly_open_freq_not_gt_0.5(weekly=%s)" % weekly)
    return {
        "passed": not reasons,
        "reasons": reasons,
        "expectancy_E": e,
        "weekly_open_freq": weekly,
        "mean_win_only_pct": mean_win,
        "gate": {
            "expectancy_E_strict_gt": GATE_E_STRICT_GT,
            "weekly_open_freq_strict_gt": GATE_WEEKLY_STRICT_GT,
        },
    }


def _row_id(row):
    rid = str(row.get("id") or "").strip()
    if rid:
        return rid
    title = str(row.get("title_zh") or row.get("name") or row.get("strategy_name") or "").strip()
    if title:
        return "qi_%s" % (abs(hash(title)) % (10 ** 10))
    return ""


def _public_row(row, verdict=None):
    metrics = row.get("metrics") or {}
    if not isinstance(metrics, dict):
        metrics = {}
    verdict = verdict or evaluate_gate({
        "expectancy_E": row.get("expectancy_E", metrics.get("expectancy_E")),
        "weekly_open_freq": row.get("weekly_open_freq", metrics.get("weekly_open_freq") or metrics.get("weekly_opens")),
        "mean_win_only_pct": row.get("mean_win_only_pct", metrics.get("mean_win_only_pct")),
        "expectancy": metrics.get("expectancy"),
    })
    title = (
        row.get("title_zh") or row.get("name") or row.get("strategy_name")
        or row.get("id") or "—"
    )
    return {
        "id": row.get("id"),
        "title_zh": title,
        "strategy_name": title,
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "direction": row.get("direction"),
        "pipeline": row.get("pipeline"),
        "pipeline_label": row.get("pipeline_label"),
        "source": row.get("source"),
        "expectancy_E": verdict.get("expectancy_E"),
        "weekly_open_freq": verdict.get("weekly_open_freq"),
        "mean_win_only_pct": verdict.get("mean_win_only_pct"),
        "passed": bool(verdict.get("passed")),
        "gate_reasons": list(verdict.get("reasons") or []),
        "updated_at": row.get("updated_at"),
    }


def ingest(row):
    """Upsert one candidate into the 质检器 board and re-evaluate the gate."""
    row = dict(row or {})
    rid = _row_id(row)
    if not rid:
        return {"ok": False, "error": "missing_id_or_title"}
    row["id"] = rid
    metrics = dict(row.get("metrics") or {})
    for key_src, key_dst in (
        ("expectancy_E", "expectancy_E"),
        ("weekly_open_freq", "weekly_open_freq"),
        ("weekly_opens", "weekly_open_freq"),
        ("mean_win_only_pct", "mean_win_only_pct"),
    ):
        if row.get(key_src) is not None:
            metrics[key_dst] = row.get(key_src)
        elif metrics.get(key_src) is not None and key_dst not in metrics:
            metrics[key_dst] = metrics.get(key_src)
    verdict = evaluate_gate(metrics)
    row["metrics"] = metrics
    row["passed"] = bool(verdict.get("passed"))
    row["gate_reasons"] = list(verdict.get("reasons") or [])
    row["expectancy_E"] = verdict.get("expectancy_E")
    row["weekly_open_freq"] = verdict.get("weekly_open_freq")
    row["mean_win_only_pct"] = verdict.get("mean_win_only_pct")
    row["updated_at"] = _now()
    row.setdefault("title_zh", row.get("name") or row.get("strategy_name") or rid)

    data = load_store()
    items = list(data.get("items") or [])
    replaced = False
    for i, old in enumerate(items):
        if not isinstance(old, dict):
            continue
        if str(old.get("id") or "") == rid or (
            row.get("title_zh") and str(old.get("title_zh") or "") == str(row.get("title_zh"))
        ):
            merged = dict(old)
            merged.update(row)
            items[i] = merged
            replaced = True
            break
    if not replaced:
        items.append(row)
    data["items"] = items
    save_store(data)
    return {
        "ok": True,
        "id": rid,
        "passed": row["passed"],
        "replaced": replaced,
        "gate_reasons": row["gate_reasons"],
        "count": len(items),
    }


def _seed_from_pending_optimize():
    """Pull existing pending-optimize rows so the board is not empty after deploy."""
    try:
        from . import pending_optimize as po
        board = po.status() or {}
        for row in list(board.get("strategies") or []):
            if not isinstance(row, dict):
                continue
            ingest({
                "id": row.get("id"),
                "title_zh": row.get("title_zh"),
                "symbol": row.get("symbol"),
                "timeframe": row.get("timeframe"),
                "direction": row.get("direction"),
                "source": "pending_optimize_seed",
                "expectancy_E": row.get("expectancy_E"),
                "weekly_open_freq": row.get("weekly_open_freq"),
                "mean_win_only_pct": row.get("mean_win_only_pct"),
            })
    except Exception:
        pass


def status():
    """Website board: 质检器 + 合格/不合格两表。"""
    data = load_store()
    if not list(data.get("items") or []):
        _seed_from_pending_optimize()
        data = load_store()

    passed = []
    failed = []
    for row in list(data.get("items") or []):
        if not isinstance(row, dict):
            continue
        if row.get("active") is False:
            continue
        pub = _public_row(row)
        if pub.get("passed"):
            passed.append(pub)
        else:
            failed.append(pub)

    # Prefer fresher rows first.
    passed.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    failed.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)

    return {
        "ok": True,
        "schema": "qiyu_quality_inspector_board_v1",
        "updated_at": data.get("updated_at") or _now(),
        "module_zh": "质检器",
        "gate_zh": data.get("gate_zh") or "四AI平均数：E>0 且 周开仓频率>0.5",
        "gate": data.get("gate") or {
            "expectancy_E_strict_gt": GATE_E_STRICT_GT,
            "weekly_open_freq_strict_gt": GATE_WEEKLY_STRICT_GT,
            "source": "four_ai_average",
        },
        "note_zh": (
            "双管道产出汇入质检器；仅看四AI平均 E>0 与周开仓频率>0.5。"
            "左侧为质检合格，右侧为质检不合格。禁止自动挂载。"
        ),
        "passed": passed,
        "failed": failed,
        "passed_count": len(passed),
        "failed_count": len(failed),
        "count": len(passed) + len(failed),
        # Backward-compatible aliases for old review API consumers.
        "stages": [],
        "handoffs_from_pipelines": [],
        "human_confirm_gate": "质检合格后进入策略待优化",
        "automatic_live_deployment": False,
    }
