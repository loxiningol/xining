# -*- coding: utf-8 -*-
"""MultiSlotEvolutionManager — RAM-adaptive concurrent evolution slots."""
from __future__ import print_function

import json
import os
import time
from pathlib import Path

from . import live_status
from . import status_humanizer as humanizer


SLOTS_FILE = "auto_driver_slots.json"


def slots_path(vector_root=None):
    root = Path(vector_root or os.environ.get("VECTOR_ROOT") or "/root")
    return root / "auto_trade" / "dual_engine" / SLOTS_FILE


def read_meminfo_mb():
    """Return (total_mb, available_mb). Best-effort."""
    total = avail = None
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) // 1024
            elif line.startswith("MemAvailable:"):
                avail = int(line.split()[1]) // 1024
    except Exception:
        pass
    if total is None:
        try:
            import psutil  # optional
            vm = psutil.virtual_memory()
            total = int(vm.total // (1024 * 1024))
            avail = int(vm.available // (1024 * 1024))
        except Exception:
            total, avail = 1024, 512
    if avail is None:
        avail = max(0, int(total or 0) // 4)
    return int(total or 0), int(avail or 0)


def recommend_slot_count(total_mb=None, available_mb=None):
    """Blueprint: >8GB→5, 4–8GB→3, else 1 (protect 764MB hosts)."""
    if total_mb is None or available_mb is None:
        total_mb, available_mb = read_meminfo_mb()
    # Prefer available headroom; fall back to total.
    if available_mb >= 8192 or total_mb >= 16384:
        return 5
    if available_mb >= 4096 or total_mb >= 8192:
        return 3
    if total_mb >= 4096 and available_mb >= 2048:
        return 3
    return 1


def _compact_pipe(pipeline):
    marks = []
    id_map = {
        "gate0": "G0", "gate1": "G1", "l1": "L1", "gate2": "G2", "audit4d": "4D",
    }
    for row in pipeline or []:
        short = id_map.get(row.get("id"), (row.get("id") or "?")[:2].upper())
        st = row.get("status")
        if st == "done":
            m = "✓"
        elif st == "running":
            m = "⏳"
        elif st == "fail":
            m = "✗"
        else:
            m = "·"
        marks.append("%s:%s" % (short, m))
    return marks


def _infer_slot_state(payload):
    if not payload:
        return "idle"
    if payload.get("success") or (payload.get("final") or {}).get("success"):
        return "success"
    final = (payload.get("final") or {}).get("status") or payload.get("final_status")
    if final:
        if str(final).upper() == "SUCCESS":
            return "success"
        return "archived"
    if payload.get("running"):
        # breakthrough-ish if gate2 done
        for row in payload.get("pipeline") or []:
            if row.get("id") == "gate2" and row.get("status") == "done":
                return "breakthrough"
            if row.get("id") == "audit4d" and row.get("status") in ("running", "done"):
                return "breakthrough"
        return "running"
    return "idle"


def _slot_from_live_payload(payload, slot_id):
    payload = payload or {}
    strat = payload.get("strategy") or {}
    prog = payload.get("progress") or {}
    final = payload.get("final") or {}
    state = _infer_slot_state(payload)
    status = payload.get("status_label") or ""
    # ensure humanized
    if final.get("status") or final.get("stop_code"):
        status = humanizer.humanize_final(
            final.get("status"), final.get("stop_code"), success=bool(final.get("success")),
        )
    elif status and re_is_raw(status):
        status = humanizer.humanize_status_label(
            phase=payload.get("phase"),
            reason=(payload.get("metrics") or {}).get("pipeline_reason"),
            final_status=final.get("status"),
            stop_code=final.get("stop_code"),
            success=bool(final.get("success")),
        )
    pct = float(prog.get("percent") or 0)
    eta = prog.get("eta_sec")
    elapsed = prog.get("elapsed_sec")
    return {
        "slot_id": slot_id,
        "state": state,
        "state_label": humanizer.slot_state_label(state),
        "running": bool(payload.get("running")),
        "title_zh": strat.get("title_zh") or "—",
        "family": strat.get("family") or strat.get("family_base") or "—",
        "causal_blurb": strat.get("causal_blurb") or "",
        "status_label": status,
        "percent": pct,
        "round_label": prog.get("round_label") or "",
        "iteration": prog.get("iteration"),
        "max_iterations": prog.get("max_iterations"),
        "eta_sec": eta,
        "elapsed_sec": elapsed,
        "pipeline": payload.get("pipeline") or [],
        "pipeline_compact": _compact_pipe(payload.get("pipeline") or []),
        "final_zh": humanizer.humanize_final(
            final.get("status"), final.get("stop_code"), success=bool(final.get("success")),
        ) if (final.get("status") or final.get("stop_code")) else None,
        "updated_at": payload.get("updated_at"),
        "workdir": final.get("workdir") or (payload.get("final") or {}).get("workdir"),
        "source": payload.get("source"),
    }


def re_is_raw(text):
    s = str(text or "")
    if not s:
        return False
    if re_search_cjk(s):
        # still raw if SCREAMING enums leak in
        return bool(__import__("re").search(r"\b[A-Z]{3,}(?:_[A-Z0-9]+)+\b", s))
    return bool(__import__("re").search(r"_|LIMIT|FAILED|PROGRESS|funnel_|gate2_", s))


def re_search_cjk(s):
    return bool(__import__("re").search(r"[\u4e00-\u9fff]", s))


def _recent_run_payloads(vector_root, limit=8):
    root = Path(vector_root or os.environ.get("VECTOR_ROOT") or "/root")
    runs_dir = root / "auto_trade" / "dual_engine" / "workflow_v2" / "auto_driver_runs"
    if not runs_dir.exists():
        return []
    runs = sorted([p for p in runs_dir.iterdir() if p.is_dir()],
                  key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for run in runs[:limit]:
        live = run / live_status.LIVE_STATUS_NAME
        if live.exists():
            try:
                data = json.loads(live.read_text(encoding="utf-8"))
                data["source"] = "run:%s" % run.name
                data.setdefault("final", {})
                if data.get("final") is None:
                    data["final"] = {}
                data["final"]["workdir"] = str(run)
                out.append(data)
                continue
            except Exception:
                pass
        try:
            cfg = {}
            pack = {}
            if (run / "config.used.json").exists():
                cfg = json.loads((run / "config.used.json").read_text(encoding="utf-8"))
            pack_path = run / "pack_final.json"
            if not pack_path.exists():
                pack_path = run / "pack_initial.json"
            if pack_path.exists():
                pack = json.loads(pack_path.read_text(encoding="utf-8"))
            state = {"workdir": str(run), "iterations": [], "n_iterations": 0}
            mtime = run.stat().st_mtime
            if (run / "driver_state.json").exists():
                ds = json.loads((run / "driver_state.json").read_text(encoding="utf-8"))
                state.update({k: ds.get(k) for k in (
                    "final_status", "stop_code", "success", "elapsed_sec",
                    "iterations", "n_iterations",
                ) if k in ds})
            iters = sorted(run.glob("iter_*"))
            result = None
            if iters and not state.get("iterations"):
                state["n_iterations"] = len(iters)
                fc = iters[-1] / "failure_context.json"
                if fc.exists():
                    ctx = json.loads(fc.read_text(encoding="utf-8"))
                    state["iterations"] = [{
                        "iteration": ctx.get("iteration") or len(iters),
                        "pipeline_reason": ctx.get("pipeline_reason"),
                        "l1": ctx.get("l1"),
                        "gate2": ctx.get("gate2_fitness"),
                        "task_id": ctx.get("task_id"),
                    }]
                    result = {"reason": ctx.get("pipeline_reason"), "task_id": ctx.get("task_id")}
            if not state.get("final_status"):
                state["final_status"] = "UNKNOWN_STOPPED"
            phase = str(state.get("stop_code") or state.get("final_status") or "stopped").lower()
            cfg = dict(cfg or {})
            cfg.setdefault("symbol", "?")
            cfg.setdefault("timeframe", "?")
            cfg.setdefault("direction", "long")
            cfg.setdefault("max_iterations", 4)
            cfg["vector_root"] = str(root)
            cfg["workdir"] = str(run)
            pub = live_status.publish_live_status(
                cfg, state, pack, phase=phase, result=result, write_global=False,
            )
            pub["source"] = "synth:%s" % run.name
            pub["running"] = False
            pub["_mtime"] = mtime
            out.append(pub)
        except Exception:
            continue
    return out


def build_slots_board(vector_root=None, display_history=5):
    """Assemble multi-slot board for dashboard (capacity + active + recent)."""
    root = Path(vector_root or os.environ.get("VECTOR_ROOT") or "/root")
    total_mb, avail_mb = read_meminfo_mb()
    capacity = recommend_slot_count(total_mb, avail_mb)

    current = live_status.read_live_status(str(root))
    recent = _recent_run_payloads(str(root), limit=display_history + 3)

    seen = set()
    ordered = []
    for payload in [current] + recent:
        if not isinstance(payload, dict):
            continue
        strat = payload.get("strategy") or {}
        title = str(strat.get("title_zh") or "").strip()
        wd = ((payload.get("final") or {}).get("workdir")
              or payload.get("source")
              or strat.get("family")
              or title
              or "")
        key = str(wd)
        if not key or key in seen:
            continue
        if title in ("", "—", "-") and not payload.get("running"):
            continue
        seen.add(key)
        ordered.append(payload)

    def _sort_key(p):
        running = 0 if p.get("running") else 1
        wd = (p.get("final") or {}).get("workdir")
        mt = p.get("_mtime") or 0
        try:
            if wd and Path(wd).exists():
                mt = Path(wd).stat().st_mtime
        except Exception:
            pass
        return (running, -float(mt or 0))

    ordered.sort(key=_sort_key)

    display_n = max(capacity, min(display_history, 5))
    slots = []
    for i, payload in enumerate(ordered[:display_n], start=1):
        slots.append(_slot_from_live_payload(payload, i))

    while len(slots) < capacity:
        slots.append({
            "slot_id": len(slots) + 1,
            "state": "idle",
            "state_label": humanizer.slot_state_label("idle"),
            "running": False,
            "title_zh": "等待新机制入队",
            "family": "—",
            "causal_blurb": "",
            "status_label": humanizer.HUMAN_TRANSLATION_MAP["idle"],
            "percent": 0,
            "round_label": "",
            "pipeline": [],
            "pipeline_compact": [],
            "final_zh": None,
            "updated_at": None,
        })

    active = sum(1 for s in slots if s.get("running") or s.get("state") in ("running", "breakthrough"))
    board = {
        "schema": "qiyu_auto_driver_slots_v1",
        "ok": True,
        "engine": {
            "name": "真挚之语 (True Words) 并发演进阵列",
            "version": "v2.5",
            "roles": "GLM-5.2 总设计师 · Codex 总工程师 · 四维正式复核",
        },
        "ram": {
            "total_mb": total_mb,
            "available_mb": avail_mb,
            "total_gb": round(total_mb / 1024.0, 2),
            "available_gb": round(avail_mb / 1024.0, 2),
        },
        "capacity": capacity,
        "active_slots": active,
        "slots": slots,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note_zh": (
            "本机可用内存约 %dMB / 总量 %dMB，并发卡槽容量 %d。"
            "容量自适应：>8GB→5，4–8GB→3，低配主机→1，防止 OOM。"
            % (avail_mb, total_mb, capacity)
        ),
        "human_confirm_required": True,
        "auto_mount": False,
        "charter_version": "true_words_v2.5",
    }
    path = slots_path(str(root))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(board, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception:
        pass
    return board


def read_slots_board(vector_root=None):
    path = slots_path(vector_root)
    # Always rebuild so RAM + latest runs stay fresh
    return build_slots_board(vector_root)
