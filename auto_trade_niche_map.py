# -*- coding: utf-8 -*-
"""Unoccupied niche map — weekly directed exploration brief for hackathon."""
from __future__ import print_function

from datetime import datetime
from pathlib import Path
from collections import Counter
import json
import os
import sqlite3
import tempfile

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
TELEMETRY_DB = AUTO_DIR / "microstructure_telemetry.db"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"
ECO_DB = AUTO_DIR / "strategy_ecosystem.db"
REPORT_PATH = AUTO_DIR / "niche_map_report.json"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def _covered_primitives():
    ctrl = _read(CONTROL_PATH, {})
    covered = set()
    coverage = []
    for aid, row in sorted((ctrl.get("assignments") or {}).items()):
        b = row.get("environment_boundary") or {}
        ids = list(b.get("any_of") or []) + list(b.get("all_of") or [])
        for x in ids:
            covered.add(x)
        if ids or row.get("audit_state") == "conditional_frequency_probe":
            coverage.append({
                "assignment_id": aid,
                "state": row.get("audit_state"),
                "any_of": b.get("any_of") or [],
                "vol_in": b.get("volatility_in") or [],
            })
    return covered, coverage


def _recent_clusters(days=7):
    if not TELEMETRY_DB.exists():
        return []
    import time
    since = int(time.time() * 1000) - int(days * 86400000)
    conn = sqlite3.connect(str(TELEMETRY_DB))
    try:
        rows = conn.execute(
            "SELECT a.cluster_id, a.symbol, c.state, c.label, c.description, "
            "COUNT(*) as hits FROM microstructure_primitive_assignments a "
            "JOIN microstructure_primitive_clusters c ON c.cluster_id=a.cluster_id "
            "WHERE a.end_ms>=? GROUP BY a.cluster_id "
            "ORDER BY hits DESC LIMIT 400",
            (since,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        out.append({
            "cluster_id": r[0], "symbol": r[1], "state": r[2],
            "label": r[3] or "", "description": (r[4] or "")[:160],
            "hits_7d": int(r[5] or 0),
        })
    return out


def _top_death_codes(limit=12):
    if not Path(ECO_DB).exists():
        return []
    conn = sqlite3.connect(str(ECO_DB))
    try:
        rows = conn.execute(
            "SELECT clusters_json FROM prescreen_death_maps "
            "ORDER BY created_at DESC LIMIT 8"
        ).fetchall()
    finally:
        conn.close()
    counts = Counter()
    for (cj,) in rows:
        try:
            clusters = json.loads(cj or "[]")
        except Exception:
            continue
        cells = clusters if isinstance(clusters, list) else list(
            (clusters or {}).values())
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            code = cell.get("death_cause_code") or cell.get("pattern_id")
            if code:
                counts[code] += int(cell.get("occurrences") or 1)
    return [{"death_cause_code": k, "weight": v}
            for k, v in counts.most_common(limit)]


def _logic_drafts(uncovered, death_codes, covered_rows):
    """Reasoned niche drafts — logic only, not DSL."""
    drafts = []
    # Group uncovered by symbol
    by_sym = {}
    for row in uncovered:
        by_sym.setdefault(row["symbol"], []).append(row)

    # 1) High-hit uncovered registered/pending with spread/depth language
    for sym, rows in sorted(by_sym.items()):
        labeled = [r for r in rows if r.get("label") and "未命名" not in r["label"]]
        top = (labeled or rows)[:3]
        if not top:
            continue
        if any("价差" in (r.get("label") or "") or "深度" in (r.get("description") or "")
               for r in top):
            drafts.append({
                "priority": "P1",
                "symbol": sym,
                "niche": "spread_shock_continuation",
                "logic": (
                    "%s 上出现高频未覆盖的价差/深度基元，但现有探针未声明该组合。"
                    "可探索：H1 方向过滤 + 5m/15m 价差骤扩后的顺势第一根实体确认，"
                    "避免纯极值反转（死亡热力含 cost_collapse/stop_cluster）。"
                    % sym
                ),
                "avoid": [c["death_cause_code"] for c in death_codes[:4]],
                "seed_primitives": [r["cluster_id"] for r in top],
            })

    # 2) Symbols with probes but empty any_of / weak coverage
    weak = [c for c in covered_rows if not (c.get("any_of"))]
    for row in weak[:5]:
        drafts.append({
            "priority": "P2",
            "symbol": (row["assignment_id"] or "").split("|")[0],
            "niche": "boundary_hole",
            "logic": (
                "策略 %s 已在跑但环境 any_of 为空或过宽，生态位声明不足。"
                "应补：把该标的近 7 日 top pending/registered 基元收窄进边界，"
                "并设计与边界同构的入场叶，而不是再发明无环境策略。"
                % row["assignment_id"]
            ),
            "avoid": ["negative_net_expectancy"],
            "seed_primitives": [],
        })

    # 3) Cross-asset transfer from successful NG/XAU styles
    if by_sym.get("ADA-USDT-SWAP") or by_sym.get("LTC-USDT-SWAP"):
        drafts.append({
            "priority": "P1",
            "symbol": "ADA-USDT-SWAP / LTC-USDT-SWAP",
            "niche": "transfer_ng_session_exhaustion",
            "logic": (
                "NG session exhaustion reclaim 已被验证可触发；ADA/LTC 5m 有大量 "
                "uncovered unstable 基元。可迁移：会话窗 + z20/RSI 极端 + K 拐头 "
                "的同构，但必须换会话小时并收紧 ATR 带，防止跨品种参数照搬。"
            ),
            "avoid": ["holdout_collapse", "cost_collapse"],
            "seed_primitives": [],
        })

    # 4) Forward week: vol regime shift niche
    drafts.append({
        "priority": "P2",
        "symbol": "BTC-USDT-SWAP",
        "niche": "post_spike_mean_reversion_with_h1_filter",
        "logic": (
            "未来一周若 BTC 波动率分位从高位回落，可能出现“高潮后的有方向回撤”生态位："
            "要求 H1 趋势仍在、15m z20 从 <-2 回到 (-2,-1) 带时的顺势再入，"
            "明确禁止无 invalidation 的 CCI 极值 TP（已在黑客松审计中否决）。"
        ),
        "avoid": ["stop_cluster", "cost_collapse"],
        "seed_primitives": [],
    })

    # 5) Inventory-stress fade only when depth recovers
    drafts.append({
        "priority": "P3",
        "symbol": "XAG-USDT-SWAP / XAU-USDT-SWAP",
        "niche": "depth_recovery_fade",
        "logic": (
            "贵金属深度基元频繁但未占领：先等 top5 深度从崩溃恢复 30% 后再做 "
            "突破失败回撤，而不是在深度崩溃当中追突破（易被做市商收割）。"
        ),
        "avoid": ["cost_collapse"],
        "seed_primitives": [],
    })
    return drafts[:12]


def build_report(days=7):
    covered, coverage = _covered_primitives()
    clusters = _recent_clusters(days=days)
    uncovered = [c for c in clusters if c["cluster_id"] not in covered]
    death = _top_death_codes()
    drafts = _logic_drafts(uncovered, death, coverage)
    report = {
        "ok": True,
        "schema": "qiyu_niche_map_v1",
        "generated_at": _now(),
        "horizon_days": days,
        "summary": {
            "active_probe_or_bound_strategies": len(coverage),
            "covered_primitive_ids": len(covered),
            "clusters_touched": len(clusters),
            "uncovered_clusters": len(uncovered),
            "coverage_ratio": round(
                float(len(covered)) / float(max(len({c['cluster_id'] for c in clusters}), 1)),
                4),
        },
        "occupied_niches": coverage,
        "uncovered_top": uncovered[:40],
        "death_heatmap_prior": death,
        "exploration_briefs": drafts,
        "hackathon_prompt_pool": [
            {
                "title": d["niche"],
                "symbol_hint": d["symbol"],
                "logic": d["logic"],
                "avoid_death_codes": d["avoid"],
                "priority": d["priority"],
            }
            for d in drafts
        ],
        "policy": "weekly refresh; guides hackathon/mutations; not auto-live",
    }
    _atomic(REPORT_PATH, report)
    return report


if __name__ == "__main__":
    print(json.dumps(build_report(), ensure_ascii=False, indent=2)[:4000])
