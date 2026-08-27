# -*- coding: utf-8 -*-
"""Versioned advisory learning pack for the sole Kimi creator.

Phase-0 contract (frozen):
- Write: parallel_creation finish → quality_inspector.ingest_from_creation_job
  → creation_case_store.record_creation_outcome → refresh_pack.
- Read: public_creator_library() → Kimi tool_bundle only (advisory).
- Cluster key: structure_family + feature_fingerprint + fail_bucket.
  Never cluster on research_direction / symbol / long|short.
- Promote: ≥3 symbols AND ≥5 jobs; human veto hides cluster, keeps cases.
- quality_gate thresholds unchanged; pack is not_a_gate / cannot mount.
- Path lexicon: 止盈 / 止损 / 定时. Occupancy weekly-geo is never a reward.
- Rollback: QIYU_CREATION_LEARNING=0 disables write + public injection.

Cannot pass quality_gate, cannot mount, cannot rewrite PRODUCTION_ERROR_CASES.
Human vetoes drop a cluster from the public library without deleting cases.
"""
from __future__ import print_function

import json
import os
from datetime import datetime

from .creation_attribution import (
    MIN_JOBS,
    MIN_SYMBOLS,
    advisory_check_order,
    build_clusters,
    feature_usage,
    success_few_shot,
)
from .creation_case_store import load_cases, store_dir
from .creation_live_lessons import apply_slow_mechanism_beliefs, summarize_live_family_paths
from .process_safe_state import atomic_write_json, process_lock


SCHEMA = "qiyu_creation_learning_pack_v1"
VETO_SCHEMA = "qiyu_creation_learning_vetoes_v1"


def learning_enabled():
    """QIYU_CREATION_LEARNING=0 turns off write/read injection (phase-1 rollback)."""
    raw = str(os.environ.get("QIYU_CREATION_LEARNING") or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def pack_path():
    return store_dir() / "learning_pack.json"


def vetoes_path():
    return store_dir() / "human_vetoes.json"


def report_path():
    return store_dir() / "cluster_report.md"


def empty_pack():
    return {
        "schema": SCHEMA,
        "version": 0,
        "built_at": None,
        "min_support": {"jobs": MIN_JOBS, "symbols": MIN_SYMBOLS},
        "clusters": [],
        "success_few_shot": [],
        "live_family_paths": {
            "occupancy_geo_excluded": True,
            "by_family": {},
            "note_zh": "尚无实盘路径课。",
        },
        "check_order": advisory_check_order([]),
        "feature_usage": {"fail_top": [], "ready_top": []},
        "human_vetoes": [],
        "not_a_gate": True,
        "quality_gate_unchanged": True,
        "production_error_cases_not_auto_updated": True,
        "note_zh": "课包为空时回退静态趋势识字 doctrine。不能放行或挂载。",
    }


def load_vetoes():
    path = vetoes_path()
    if not path.is_file():
        return {"schema": VETO_SCHEMA, "cluster_ids": []}
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"schema": VETO_SCHEMA, "cluster_ids": []}
    if not isinstance(row, dict):
        return {"schema": VETO_SCHEMA, "cluster_ids": []}
    ids = [
        str(item).strip() for item in (row.get("cluster_ids") or [])
        if str(item).strip()
    ]
    return {"schema": VETO_SCHEMA, "cluster_ids": ids}


def set_human_veto(cluster_id, veto=True):
    cid = str(cluster_id or "").strip()
    if not cid:
        return load_vetoes()
    with process_lock("creation_learning_vetoes"):
        data = load_vetoes()
        ids = list(data.get("cluster_ids") or [])
        if veto and cid not in ids:
            ids.append(cid)
        if not veto:
            ids = [item for item in ids if item != cid]
        data = {"schema": VETO_SCHEMA, "cluster_ids": ids, "updated_at": _now()}
        atomic_write_json(vetoes_path(), data)
        return data


def load_pack():
    path = pack_path()
    if not path.is_file():
        return empty_pack()
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return empty_pack()
    if not isinstance(row, dict) or row.get("schema") != SCHEMA:
        return empty_pack()
    row.setdefault("not_a_gate", True)
    row.setdefault("quality_gate_unchanged", True)
    row.setdefault("production_error_cases_not_auto_updated", True)
    return row


def _write_cluster_report(pack):
    lines = [
        "# 创造学习簇报表（只读，非质检）",
        "",
        "- 版本：%s" % pack.get("version"),
        "- 生成：%s" % pack.get("built_at"),
        "- 最小支持度：%s 标的 / %s 任务" % (
            (pack.get("min_support") or {}).get("symbols"),
            (pack.get("min_support") or {}).get("jobs"),
        ),
        "- 本报表不能放行、不能挂载、不能改 10 项门槛，也不能自动改 PRODUCTION_ERROR_CASES。",
        "- 归因对象是共同因子，不是研究方向。",
        "",
        "## 已升格簇",
        "",
    ]
    promoted = [row for row in (pack.get("clusters") or []) if row.get("promoted")]
    if not promoted:
        lines.append("（尚无达到最小支持度的共同因子簇）")
        lines.append("")
    for row in promoted:
        lines.append("### `%s`" % row.get("cluster_id"))
        lines.append("")
        lines.append("- 任务数 / 标的数：%s / %s" % (row.get("n_jobs"), row.get("n_symbols")))
        lines.append("- 结构家族：%s" % row.get("structure_family"))
        lines.append("- 入场指纹：%s" % row.get("feature_fingerprint"))
        lines.append("- 数字桶：%s" % row.get("fail_bucket_zh"))
        lines.append("- %s" % row.get("lesson_zh"))
        lines.append("")
    vetoed = pack.get("human_vetoes") or []
    lines.append("## 人否决")
    lines.append("")
    if vetoed:
        for cid in vetoed:
            lines.append("- `%s`" % cid)
        lines.append("")
    else:
        lines.append("（无）")
        lines.append("")
    live = pack.get("live_family_paths") or {}
    lines.append("## 实盘路径（止盈 / 止损 / 定时）")
    lines.append("")
    lines.append("- 占用周几何已排除：%s" % live.get("occupancy_geo_excluded"))
    by_family = live.get("by_family") or {}
    if not by_family:
        lines.append("- 尚无达到家族最低平仓数的路径课")
        lines.append("")
    else:
        for family, bag in sorted(by_family.items()):
            lines.append(
                "- %s：止盈 %s，止损 %s，定时 %s，n=%s"
                % (
                    family,
                    bag.get("止盈"), bag.get("止损"), bag.get("定时"), bag.get("n"),
                )
            )
        lines.append("")
    entry_sit = live.get("entry_situations") or {}
    lines.append("## 同一策略的入场点（反复≥3次才归因）")
    lines.append("")
    attrs = list(entry_sit.get("attributions") or [])
    if not attrs:
        lines.append("- 尚无同一策略、同一入场情境反复≥3次的对照课")
        lines.append("")
    else:
        for row in attrs[:12]:
            lines.append("- %s" % row.get("lesson_zh"))
        lines.append("")
    common = list(entry_sit.get("common_factors") or [])
    if common:
        lines.append("### 跨策略共同入场因子")
        lines.append("")
        for row in common[:8]:
            lines.append("- %s" % row.get("lesson_zh"))
        lines.append("")
    lines.append("## 过门 few-shot")
    lines.append("")
    shots = pack.get("success_few_shot") or []
    if not shots:
        lines.append("（尚无）")
        lines.append("")
    else:
        for shot in shots:
            lines.append("- %s" % shot.get("note_zh"))
        lines.append("")
    text = "\n".join(lines)
    path = report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".%s.tmp" % path.name)
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))
    return path


def refresh_pack(cases=None, live_paths=None, apply_beliefs=True):
    cases = list(cases if cases is not None else load_cases())
    vetoes = load_vetoes()
    veto_ids = set(vetoes.get("cluster_ids") or [])
    clusters = build_clusters(cases)
    visible = []
    for row in clusters:
        if row.get("cluster_id") in veto_ids:
            continue
        visible.append(row)
    promoted = [row for row in visible if row.get("promoted")]
    if live_paths is None:
        try:
            live_paths = summarize_live_family_paths()
        except Exception:
            live_paths = {
                "occupancy_geo_excluded": True,
                "by_family": {},
                "note_zh": "实盘路径课读取失败，已跳过。",
            }
    if apply_beliefs:
        try:
            apply_slow_mechanism_beliefs(live_paths)
        except Exception:
            pass
    prev = load_pack()
    version = int(prev.get("version") or 0) + 1
    pack = {
        "schema": SCHEMA,
        "version": version,
        "built_at": _now(),
        "min_support": {"jobs": MIN_JOBS, "symbols": MIN_SYMBOLS},
        "clusters": visible,
        "promoted_n": len(promoted),
        "success_few_shot": success_few_shot(cases),
        "live_family_paths": live_paths,
        "check_order": advisory_check_order(visible),
        "feature_usage": feature_usage(cases),
        "human_vetoes": sorted(veto_ids),
        "not_a_gate": True,
        "quality_gate_unchanged": True,
        "production_error_cases_not_auto_updated": True,
        "occupancy_geo_excluded": True,
        "note_zh": (
            "跨标的共同因子课包。禁止把簇写成研究方向或做多做空禁令。"
            "quality_gate 仍是唯一机器否决权。"
        ),
    }
    with process_lock("creation_learning_pack"):
        atomic_write_json(pack_path(), pack)
        try:
            _write_cluster_report(pack)
        except Exception:
            pass
    # Phase 5 feature; default OFF so phase-1 pack refresh cannot enqueue remelts.
    remelt_flag = str(os.environ.get("QIYU_LIVE_REMELT_ENQUEUE") or "0").strip().lower()
    if remelt_flag in ("1", "true", "on", "yes"):
        try:
            from .live_remelt import enqueue_from_pack
            pack["remelt_enqueue"] = enqueue_from_pack(pack)
        except Exception as exc:
            pack["remelt_enqueue"] = {"ok": False, "error": str(exc)[:180]}
    else:
        pack["remelt_enqueue"] = {"ok": True, "skipped": "QIYU_LIVE_REMELT_ENQUEUE=0"}
    return pack


def public_creator_library():
    """Read-only slice for Kimi tool_bundle. Empty pack must not break create."""
    if not learning_enabled():
        row = empty_pack()
        row["disabled"] = True
        row["note_zh"] = "QIYU_CREATION_LEARNING=0：课包注入已关闭，回退静态趋势识字。"
        return {
            "ok": True,
            "not_a_gate": True,
            "quality_gate_unchanged": True,
            "disabled": True,
            "schema": SCHEMA,
            "version": 0,
            "case_library": {
                "not_a_gate": True,
                "not_a_research_direction_ban": True,
                "clusters": [],
                "note_zh": row["note_zh"],
            },
            "advisory_check_order": advisory_check_order([]),
            "success_few_shot": [],
            "live_family_paths": {
                "occupancy_geo_excluded": True,
                "by_family": {},
                "not_a_gate": True,
            },
            "feature_usage": {"fail_top": [], "ready_top": []},
        }
    try:
        pack = load_pack()
    except Exception:
        pack = empty_pack()
    if not isinstance(pack, dict) or pack.get("schema") != SCHEMA:
        pack = empty_pack()
    promoted = [row for row in (pack.get("clusters") or []) if row.get("promoted")]
    live = pack.get("live_family_paths") or {}
    entry_sit = live.get("entry_situations") if isinstance(live.get("entry_situations"), dict) else {}
    live_public = {
        "occupancy_geo_excluded": True,
        "by_family": live.get("by_family") or {},
        "live_warnings": list(live.get("live_warnings") or [])[:8],
        "entry_situations": {
            "not_a_gate": True,
            "not_a_research_direction_ban": True,
            "min_repeats": entry_sit.get("min_repeats") or 3,
            "attributions": list(entry_sit.get("attributions") or [])[:8],
            "situations": list(entry_sit.get("situations") or [])[:12],
            "common_factors": list(entry_sit.get("common_factors") or [])[:8],
            "note_zh": entry_sit.get("note_zh") or (
                "同一策略、同一类入场点反复≥3次才对照止盈/止损归因。不是研究方向禁令。"
            ),
        },
        "note_zh": live.get("note_zh"),
        "not_a_gate": True,
    }
    return {
        "ok": True,
        "not_a_gate": True,
        "quality_gate_unchanged": True,
        "schema": SCHEMA,
        "version": pack.get("version") or 0,
        "case_library": {
            "not_a_gate": True,
            "not_a_research_direction_ban": True,
            "min_support": pack.get("min_support"),
            "clusters": [
                {
                    "cluster_id": row.get("cluster_id"),
                    "structure_family": row.get("structure_family"),
                    "feature_fingerprint": row.get("feature_fingerprint"),
                    "fail_bucket_zh": row.get("fail_bucket_zh"),
                    "top_failed_rules": list(row.get("top_failed_rules") or [])[:6],
                    "lesson_zh": row.get("lesson_zh"),
                    "n_jobs": row.get("n_jobs"),
                    "n_symbols": row.get("n_symbols"),
                    "route_mode": row.get("route_mode"),
                    "not_a_gate": True,
                }
                for row in promoted[:12]
            ],
            "note_zh": (
                "这是跨标的共同因子课，不是研究方向禁令。"
                "某次回踩做空因持仓过短失败，不等于以后不能做空；"
                "只有多标的重复出现的结构指纹才值得避开。"
            ),
        },
        "advisory_check_order": pack.get("check_order") or advisory_check_order([]),
        "success_few_shot": list(pack.get("success_few_shot") or [])[:8],
        "live_family_paths": live_public,
        "feature_usage": pack.get("feature_usage") or {},
    }


if __name__ == "__main__":
    pack = refresh_pack()
    print(json.dumps({
        "ok": True,
        "version": pack.get("version"),
        "promoted_n": pack.get("promoted_n"),
        "path": str(pack_path()),
        "report": str(report_path()),
    }, ensure_ascii=False))
