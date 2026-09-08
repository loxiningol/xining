# -*- coding: utf-8 -*-
"""Manor zone collaboration ledger (P0).

Region cards show processes + backend change notes so agents do not edit blind.
Python 3.6 compatible. Does not remelt KEEP4. Does not restart formal.
"""
from __future__ import print_function

import json
import os
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

SCHEMA = "qiyu_manor_zone_ledger_v1"
LEDGER_NAME = "manor_zone_ledger.json"
MAX_ENTRIES = 200
STATUS_CHANGES = 10
STATUS_PROCESSES = 8

ZONE_KINDS = ("process", "change", "deploy", "repair", "experiment")
ZONE_STATUSES = ("running", "done", "aborted", "blocked")

# Target topology. Building ids match manor-layout-20260823exp.js.
ZONES = (
    {
        "id": "live",
        "label_zh": "实盘工作区",
        "short_zh": "实盘区",
        "legacy_ids": ("live", "craft"),
        "modules": (
            {
                "id": "bakery",
                "title_zh": "运行中策略面包房",
                "anchor": "#atmSectionBakery",
                "manor_building": "bakery",
            },
            {
                "id": "positions",
                "title_zh": "仓位工作坊",
                "anchor": "#atmSectionPositions",
                "manor_building": "pos_greenhouse",
            },
        ),
        # near bakery (right mid);仓位在左下可经 chip 下钻
        "marker": {"x": 1480, "y": 200, "cx": 1400, "cy": 400, "scale": 0.82},
    },
    {
        "id": "manufacture",
        "label_zh": "策略制造区",
        "short_zh": "制造区",
        "legacy_ids": ("alchemy", "manufacture"),
        "modules": (
            {
                "id": "greenhouse",
                "title_zh": "发明口温室",
                "anchor": "#mdqModuleExperimental",
                "manor_building": "experimental",
            },
            {
                "id": "invent",
                "title_zh": "创造熔炉（单一系统）",
                "anchor": "#atmSectionCreation",
                "manor_building": "furnace1",
            },
            {
                "id": "furnace_retired",
                "title_zh": "右熔炉 · 已退役",
                "anchor": "#atmSectionCreation",
                "manor_building": "furnace2",
            },
            {
                "id": "windmill",
                "title_zh": "风车确认队列",
                "anchor": "#atmSectionReview",
                "manor_building": "quality",
            },
            # legacy aliases kept for old ledger rows / cards
            {
                "id": "furnace_a",
                "title_zh": "创造熔炉（旧称 hub-a）",
                "anchor": "#atmSectionCreation",
                "manor_building": "furnace1",
                "legacy": True,
            },
            {
                "id": "furnace_b",
                "title_zh": "右熔炉（旧称 hub-b，已退役）",
                "anchor": "#atmSectionCreation",
                "manor_building": "furnace2",
                "legacy": True,
            },
        ),
        "marker": {"x": 335, "y": 120, "cx": 650, "cy": 330, "scale": 0.82},
    },
    {
        "id": "archive",
        "label_zh": "研究档案区",
        "short_zh": "档案区",
        "legacy_ids": ("archive",),
        "modules": (
            {
                "id": "records",
                "title_zh": "档案室",
                "anchor": "#atmSectionArchive",
                "manor_building": "strategies",
            },
        ),
        "marker": {"x": 1388, "y": 980, "cx": 1388, "cy": 880, "scale": 0.85},
    },
    {
        "id": "watch",
        "label_zh": "观风区",
        "short_zh": "观风区",
        "legacy_ids": ("watch",),
        "modules": (
            {
                "id": "hold_tower",
                "title_zh": "观风塔",
                "anchor": "#atmSectionHoldAssist",
                "manor_building": "market",
            },
        ),
        # pinned to 观风塔 building — not owned by 仓位/档案楼栋
        "marker": {"x": 1810, "y": 380, "cx": 1810, "cy": 520, "scale": 0.9},
    },
)


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def _ledger_path():
    override = os.environ.get("MANOR_ZONE_LEDGER_PATH")
    if override:
        return Path(override)
    return _root() / "auto_trade" / LEDGER_NAME


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _atomic_write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _empty_ledger():
    return {
        "schema": SCHEMA,
        "updated_at": _now(),
        "entries": [],
    }


def load_ledger():
    path = _ledger_path()
    if not path.exists():
        return _empty_ledger()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_ledger()
    if not isinstance(doc, dict):
        return _empty_ledger()
    entries = doc.get("entries")
    if not isinstance(entries, list):
        entries = []
    return {
        "schema": SCHEMA,
        "updated_at": doc.get("updated_at") or _now(),
        "entries": [row for row in entries if isinstance(row, dict)],
    }


def save_ledger(doc):
    out = {
        "schema": SCHEMA,
        "updated_at": _now(),
        "entries": list((doc or {}).get("entries") or [])[-MAX_ENTRIES:],
    }
    _atomic_write_json(_ledger_path(), out)
    return out


def zone_catalog():
    rows = []
    for zone in ZONES:
        mods = [
            dict(m) for m in (zone.get("modules") or ())
            if not dict(m).get("legacy")
        ]
        rows.append({
            "id": zone["id"],
            "label_zh": zone["label_zh"],
            "short_zh": zone["short_zh"],
            "legacy_ids": list(zone.get("legacy_ids") or ()),
            "modules": mods,
            "marker": dict(zone.get("marker") or {}),
        })
    return {
        "ok": True,
        "schema": SCHEMA,
        "module_zh": "庄园区域协同",
        "zones": rows,
        "note_zh": "点区域开状态卡；楼栋仍下钻业务面板。",
    }


def _normalize_zone_id(zone_id):
    raw = str(zone_id or "").strip().lower()
    if not raw:
        return None
    if raw == "alchemy":
        return "manufacture"
    if raw == "craft":
        return "live"
    for zone in ZONES:
        if zone["id"] == raw:
            return zone["id"]
        if raw in (zone.get("legacy_ids") or ()):
            return zone["id"]
    return None


def _zone_meta(zone_id):
    zid = _normalize_zone_id(zone_id)
    for zone in ZONES:
        if zone["id"] == zid:
            return zone
    return None


def _seed_demo_entries(zone_id):
    """P1 demo processes when ledger empty — manufacture invent trial."""
    if zone_id != "manufacture":
        return []
    return [{
        "id": "demo_single_invent",
        "zone_id": "manufacture",
        "kind": "experiment",
        "status": "running",
        "title_zh": "单一创造系统（演示）",
        "summary_zh": "hub-a/hub-b 隔离已退役；仅 qiyu-kimi-thin-hub + /tmp/kdh_thin",
        "detail_zh": "演示条目。真实变更请用 POST /api/manor/zones/events 或 manor_zone_note 登记。",
        "actor": "cursor",
        "started_at": _now(),
        "updated_at": _now(),
        "ended_at": None,
        "services": ["qiyu-kimi-thin-hub"],
        "git_sha": None,
        "branch": None,
        "pr_url": None,
        "doc_paths": ["docs/PARALLEL_CREATE_HUBS.md"],
        "related_modules": ["invent", "greenhouse"],
        "demo": True,
    }]


def append_event(
    zone_id,
    kind="change",
    status="done",
    title_zh="",
    summary_zh="",
    detail_zh="",
    actor="cursor",
    services=None,
    git_sha=None,
    branch=None,
    pr_url=None,
    doc_paths=None,
    related_modules=None,
    event_id=None,
    started_at=None,
    ended_at=None,
):
    zid = _normalize_zone_id(zone_id)
    if not zid:
        return {"ok": False, "error": "unknown_zone", "zone_id": zone_id}
    kind = str(kind or "change").strip().lower()
    if kind not in ZONE_KINDS:
        return {"ok": False, "error": "bad_kind", "kind": kind}
    status = str(status or "done").strip().lower()
    if status not in ZONE_STATUSES:
        return {"ok": False, "error": "bad_status", "status": status}
    title = str(title_zh or "").strip() or str(summary_zh or "").strip() or "未命名事项"
    summary = str(summary_zh or "").strip() or title
    now = _now()
    row = {
        "id": str(event_id or ("mz_" + uuid.uuid4().hex[:12])),
        "zone_id": zid,
        "kind": kind,
        "status": status,
        "title_zh": title[:120],
        "summary_zh": summary[:240],
        "detail_zh": str(detail_zh or "")[:4000],
        "actor": str(actor or "cursor")[:64],
        "started_at": started_at or now,
        "updated_at": now,
        "ended_at": ended_at if status != "running" else None,
        "services": [str(x) for x in (services or []) if str(x).strip()][:20],
        "git_sha": (str(git_sha).strip()[:40] if git_sha else None),
        "branch": (str(branch).strip()[:120] if branch else None),
        "pr_url": (str(pr_url).strip()[:300] if pr_url else None),
        "doc_paths": [str(x) for x in (doc_paths or []) if str(x).strip()][:12],
        "related_modules": [
            str(x) for x in (related_modules or []) if str(x).strip()
        ][:20],
        "demo": False,
    }
    if status != "running" and not row["ended_at"]:
        row["ended_at"] = now
    doc = load_ledger()
    entries = list(doc.get("entries") or [])
    # upsert by id
    replaced = False
    for i, old in enumerate(entries):
        if str(old.get("id") or "") == row["id"]:
            merged = dict(old)
            merged.update(row)
            merged["started_at"] = old.get("started_at") or row["started_at"]
            entries[i] = merged
            row = merged
            replaced = True
            break
    if not replaced:
        entries.append(row)
    doc["entries"] = entries
    save_ledger(doc)
    return {"ok": True, "entry": row, "replaced": replaced}


def _entries_for_zone(entries, zone_id):
    zid = _normalize_zone_id(zone_id)
    out = []
    for row in entries or []:
        if _normalize_zone_id(row.get("zone_id")) == zid:
            out.append(row)
    return out


def _sort_recent(rows):
    def key(row):
        return str(row.get("updated_at") or row.get("started_at") or "")
    return sorted(rows, key=key, reverse=True)


def _auto_process_row(
    event_id, zone_id, title_zh, summary_zh, actor="system",
    services=None, related_modules=None, detail_zh="",
):
    now = _now()
    return {
        "id": event_id,
        "zone_id": zone_id,
        "kind": "process",
        "status": "running",
        "title_zh": str(title_zh or "")[:120],
        "summary_zh": str(summary_zh or title_zh or "")[:240],
        "detail_zh": str(detail_zh or "")[:4000],
        "actor": str(actor or "system")[:64],
        "started_at": now,
        "updated_at": now,
        "ended_at": None,
        "services": [str(x) for x in (services or []) if str(x).strip()][:20],
        "git_sha": None,
        "branch": None,
        "pr_url": None,
        "doc_paths": [],
        "related_modules": [
            str(x) for x in (related_modules or []) if str(x).strip()
        ][:20],
        "demo": False,
        "source": "auto",
    }


_AUTO_CACHE_TTL_SEC = 2.0
_auto_cache = {}  # zone_id -> (at, rows)


def clear_auto_cache():
    _auto_cache.clear()


def _cached_auto(zone_id, builder):
    now = time.time()
    hit = _auto_cache.get(zone_id)
    if hit and (now - float(hit[0])) < _AUTO_CACHE_TTL_SEC:
        return list(hit[1])
    rows = builder()
    _auto_cache[zone_id] = (now, list(rows or []))
    return list(rows or [])


def _auto_manufacture_processes():
    """Thin-hub 八车道 → 制造区进行中（对外只用创造结果术语）。"""
    def build():
        rows = []
        try:
            from dual_engine_workflow_v2 import thin_hub_lane_board as thb
            board = thb.status(slim=True)
        except Exception as exc:
            return [_auto_process_row(
                "auto_thin_hub_unavailable",
                "manufacture",
                "创造熔炉状态不可用",
                str(exc)[:200],
                actor="system",
                services=["qiyu-kimi-thin-hub"],
            )]
        if not board.get("ok"):
            return [_auto_process_row(
                "auto_thin_hub_error",
                "manufacture",
                "创造熔炉读取失败",
                str(board.get("error") or "thin_hub_lane_board not ok")[:200],
                actor="system",
            )]
        working = []
        for pipe in board.get("pipelines") or []:
            if not pipe.get("enabled"):
                continue
            if not pipe.get("working"):
                continue
            progress = str(pipe.get("public_progress") or "研究中")
            # Only surface in-flight research as processes
            if progress != "研究中":
                continue
            lane_no = pipe.get("lane_no") or pipe.get("pipeline")
            symbol = pipe.get("symbol") or "—"
            hub = pipe.get("hub") or "invent"
            working.append((lane_no, symbol, hub, pipe.get("last_at")))
        if not working:
            return []
        labels = ["%s号·%s" % (n, s) for n, s, _h, _t in working[:8]]
        detail_lines = [
            "%s号车道 %s · %s · %s" % (n, s, h, (t or ""))
            for n, s, h, t in working
        ]
        rows.append(_auto_process_row(
            "auto_thin_hub_research",
            "manufacture",
            "创造熔炉研究中 · %d 车道" % len(working),
            "、".join(labels),
            actor="thin-hub",
            services=["qiyu-kimi-thin-hub"],
            related_modules=["invent"],
            detail_zh="\n".join(detail_lines),
        ))
        return rows
    return _cached_auto("manufacture", build)


def _auto_watch_processes():
    """观风塔：有持仓研判则记为进行中。"""
    def build():
        try:
            import auto_trade_hold_tribunal as ht
            board = ht.trigger_board(persist=False)
        except Exception:
            return []
        if not board or not board.get("ok"):
            return []
        positions = board.get("positions") or []
        if not positions:
            return []
        alert_n = int(board.get("alert_count") or 0)
        labels = []
        for item in positions[:8]:
            sym = item.get("symbol_label") or item.get("inst_id") or "?"
            st = item.get("status") or ""
            labels.append("%s(%s)" % (sym, st))
        title = "观风塔监测中 · %d 仓" % len(positions)
        if alert_n:
            title = "观风塔告警 · %d 仓 / %d 警" % (len(positions), alert_n)
        return [_auto_process_row(
            "auto_hold_tower",
            "watch",
            title,
            "、".join(labels) if labels else "持仓研判中",
            actor="hold_tribunal",
            services=["qiyu-hold-assist"],
            related_modules=["hold_tower"],
            detail_zh="schema=%s" % (board.get("schema") or ""),
        )]
    return _cached_auto("watch", build)


def _auto_live_processes():
    """实盘：真挚之语等待器处于武装/持仓时记进程。"""
    def build():
        try:
            from dual_engine_workflow_v2 import sincere_wait as sw
            snap = sw.snapshot()
        except Exception:
            return []
        phase = str((snap or {}).get("phase") or "")
        if phase not in ("armed", "opening", "open", "closing"):
            return []
        phase_zh = (snap or {}).get("phase_zh") or phase
        card = None
        try:
            from dual_engine_workflow_v2 import sincere_speech as ss
            card = ss._deployed_card()
        except Exception:
            card = None
        symbol = (card or {}).get("symbol") or ""
        direction = (card or {}).get("direction") or ""
        exit_zh = (card or {}).get("exit_zh") or ""
        summary = "%s %s" % (symbol, direction)
        if exit_zh:
            summary = "%s · 离场 %s" % (summary.strip(), exit_zh)
        return [_auto_process_row(
            "auto_sincere_speech",
            "live",
            "真挚之语 · %s" % phase_zh,
            summary.strip() or phase_zh,
            actor="sincere_speech",
            services=["qiyu-sincere-speech"],
            related_modules=["positions"],
            detail_zh=str((card or {}).get("exit_text") or "")[:800],
        )]
    return _cached_auto("live", build)


def auto_processes(zone_id):
    zid = _normalize_zone_id(zone_id)
    if zid == "manufacture":
        return _auto_manufacture_processes()
    if zid == "watch":
        return _auto_watch_processes()
    if zid == "live":
        return _auto_live_processes()
    return []


def zone_status(zone_id, include_demo=True, include_auto=True):
    meta = _zone_meta(zone_id)
    if not meta:
        return {"ok": False, "error": "unknown_zone", "zone_id": zone_id}
    zid = meta["id"]
    doc = load_ledger()
    rows = _entries_for_zone(doc.get("entries") or [], zid)
    processes = [r for r in rows if str(r.get("status") or "") == "running"]
    changes = [r for r in rows if str(r.get("status") or "") != "running"]
    auto_rows = auto_processes(zid) if include_auto else []
    # Prefer auto + ledger; de-dupe by id
    by_id = {}
    for row in auto_rows + processes:
        rid = str(row.get("id") or "")
        if not rid:
            continue
        if rid not in by_id:
            by_id[rid] = row
    processes = list(by_id.values())
    if include_demo and not processes and not rows and not auto_rows:
        processes = _seed_demo_entries(zid)
    processes = _sort_recent(processes)[:STATUS_PROCESSES]
    changes = _sort_recent(changes)[:STATUS_CHANGES]
    health = "ok"
    if any(str(r.get("status")) == "blocked" for r in processes):
        health = "error"
    elif processes:
        health = "active"
    elif any(str(r.get("kind")) == "repair" for r in changes[:3]):
        health = "warning"
    return {
        "ok": True,
        "schema": SCHEMA,
        "zone": {
            "id": zid,
            "label_zh": meta["label_zh"],
            "short_zh": meta["short_zh"],
            "modules": [
                dict(m) for m in (meta.get("modules") or ())
                if not dict(m).get("legacy")
            ],
            "marker": dict(meta.get("marker") or {}),
        },
        "health": health,
        "processes": processes,
        "changes": changes,
        "auto_process_count": len(auto_rows),
        "ledger_updated_at": doc.get("updated_at"),
        "updated_at": _now(),
    }


def zones_overview(include_demo=True):
    """Slim badge board for map markers. One ledger read; auto sniff is 2s-cached."""
    cat = zone_catalog()
    doc = load_ledger()
    # Warm auto caches once so manufacture/watch/live don't each re-sniff.
    auto_processes("manufacture")
    auto_processes("watch")
    auto_processes("live")
    out = []
    for zone in cat["zones"]:
        zid = zone["id"]
        rows = _entries_for_zone(doc.get("entries") or [], zid)
        processes = [r for r in rows if str(r.get("status") or "") == "running"]
        changes = [r for r in rows if str(r.get("status") or "") != "running"]
        auto_rows = auto_processes(zid)
        by_id = {}
        for row in auto_rows + processes:
            rid = str(row.get("id") or "")
            if rid and rid not in by_id:
                by_id[rid] = row
        processes = list(by_id.values())
        if include_demo and not processes and not rows and not auto_rows:
            processes = _seed_demo_entries(zid)
        processes = _sort_recent(processes)[:STATUS_PROCESSES]
        changes = _sort_recent(changes)[:STATUS_CHANGES]
        health = "ok"
        if any(str(r.get("status")) == "blocked" for r in processes):
            health = "error"
        elif processes:
            health = "active"
        elif any(str(r.get("kind")) == "repair" for r in changes[:3]):
            health = "warning"
        out.append({
            "id": zone["id"],
            "label_zh": zone["label_zh"],
            "short_zh": zone["short_zh"],
            "modules": zone["modules"],
            "marker": zone["marker"],
            "health": health,
            "process_count": len(processes),
            "change_count": len(changes),
            "top_process_zh": (
                ((processes or [{}])[0] or {}).get("title_zh")
                if processes else None
            ),
        })
    return {
        "ok": True,
        "schema": SCHEMA,
        "module_zh": "庄园区域协同",
        "zones": out,
        "updated_at": _now(),
    }


def _entry_epoch(row):
    """Best-effort unix time from ledger row timestamps."""
    for key in ("updated_at", "started_at", "ended_at"):
        raw = str((row or {}).get(key) or "").strip()
        if not raw:
            continue
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return time.mktime(datetime.strptime(raw[:19], fmt).timetuple())
            except Exception:
                continue
    return 0.0


def conflict_check(zone_id, actor="", intent="edit", window_sec=900):
    """P5: before editing a zone, detect overlapping recent ledger work.

    Blockers: another actor's running entry, or recent non-self change/deploy
    within window_sec. Same-actor running/recent notes are warnings only.
    Auto processes (thin-hub / hold) are informational warnings, not hard blocks.
    """
    meta = _zone_meta(zone_id)
    if not meta:
        return {
            "ok": False,
            "conflict": True,
            "zone_id": None,
            "blockers": [{"code": "unknown_zone", "message_zh": "未知区域"}],
            "warnings": [],
            "recent": [],
        }
    zid = meta["id"]
    actor_s = str(actor or "").strip()
    intent_s = str(intent or "edit").strip() or "edit"
    try:
        win = max(60, int(window_sec or 900))
    except (TypeError, ValueError):
        win = 900
    cutoff = time.time() - win
    doc = load_ledger()
    recent = []
    for row in _entries_for_zone(doc.get("entries") or [], zid):
        if row.get("demo"):
            continue
        ts = _entry_epoch(row)
        status = str(row.get("status") or "").lower()
        # always keep open/running; otherwise require within window
        if status != "running" and ts and ts < cutoff:
            continue
        if status != "running" and not ts:
            continue
        recent.append(dict(row))
    recent.sort(key=_entry_epoch, reverse=True)

    blockers = []
    warnings = []
    for row in recent:
        other = str(row.get("actor") or "").strip()
        title = str(row.get("title_zh") or row.get("summary_zh") or "").strip()
        kind = str(row.get("kind") or "change")
        status = str(row.get("status") or "").lower()
        item = {
            "actor": other or "unknown",
            "title_zh": title or "(无标题)",
            "kind": kind,
            "status": status,
            "updated_at": row.get("updated_at") or row.get("started_at"),
            "id": row.get("id"),
        }
        same = bool(other and actor_s and other == actor_s)
        if status == "running":
            if same:
                warnings.append(item)
            else:
                blockers.append(item)
            continue
        # recent sealed notes: warn; hard-block only when intent is exclusive write
        # and another actor just touched the zone (deploy/repair within window).
        if same:
            warnings.append(item)
        elif intent_s in ("exclusive", "takeover") or (
            intent_s in ("deploy", "repair") and kind in ("deploy", "repair", "experiment")
        ):
            blockers.append(item)
        else:
            warnings.append(item)

    for auto in auto_processes(zid) or []:
        warnings.append({
            "actor": str(auto.get("actor") or "system"),
            "title_zh": str(auto.get("title_zh") or "自动进程"),
            "kind": "auto",
            "status": "running",
            "updated_at": auto.get("updated_at"),
            "id": auto.get("id"),
            "source": "auto",
        })

    conflict = bool(blockers)
    return {
        "ok": not conflict,
        "conflict": conflict,
        "zone_id": zid,
        "label_zh": meta.get("label_zh"),
        "intent": intent_s,
        "actor": actor_s or None,
        "window_sec": win,
        "blockers": blockers,
        "warnings": warnings,
        "recent": recent[:20],
        "message_zh": (
            "区域有他人近期改动，请先读状态卡再动手"
            if conflict
            else ("同区有进行中或近期记录" if warnings else "无冲突")
        ),
        "updated_at": _now(),
    }
