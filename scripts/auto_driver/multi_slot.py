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
DISPLAY_ARCHIVE_FILE = "auto_driver_slots_display_archive.json"
# Completed (100% / archived / success) cards kept on the module.
# When cumulative completed ≥ 3, oldest are auto-archived off-display.
MAX_COMPLETED_VISIBLE = 2


def slots_path(vector_root=None):
    root = Path(vector_root or os.environ.get("VECTOR_ROOT") or "/root")
    return root / "auto_trade" / "dual_engine" / SLOTS_FILE


def display_archive_path(vector_root=None):
    root = Path(vector_root or os.environ.get("VECTOR_ROOT") or "/root")
    return root / "auto_trade" / "dual_engine" / DISPLAY_ARCHIVE_FILE


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
    from . import review_lexicon as lex
    marks = []
    for row in pipeline or []:
        short = lex.pipe_short(row.get("id"))
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


_TERMINAL_DIAG_STAGES = (
    "l0", "l1", "gate2", "kb", "spec",
    "r1", "r2", "r3", "r4",
)
_LEGACY_PIPE_IDS = ("gate0", "gate1", "l0", "l1", "gate2", "audit4d", "four_d")
_NEW_PIPE_IDS = ("r1", "r2", "r3", "r4", "human")


def _infer_slot_state(payload):
    if not payload:
        return "idle"
    # Prefer explicit terminal diagnostics / final over stale running flags
    reason = str(
        (payload.get("metrics") or {}).get("pipeline_reason")
        or payload.get("phase")
        or ""
    )
    diag = payload.get("diagnostic") or {}
    status_label = str(payload.get("status_label") or diag.get("terminal_zh") or "")
    if "已归档" in status_label or diag.get("stage") in _TERMINAL_DIAG_STAGES:
        if payload.get("success") or (payload.get("final") or {}).get("success"):
            return "success"
        # Even if a stale running=True leaked, terminal cull wins for display
        if not payload.get("running") or "已归档" in status_label or diag.get("stage") in _TERMINAL_DIAG_STAGES:
            if reason or diag.get("stage") or "已归档" in status_label:
                return "archived"
    if diag.get("stage") in _TERMINAL_DIAG_STAGES and not payload.get("running"):
        if payload.get("success") or (payload.get("final") or {}).get("success"):
            return "success"
        return "archived"
    if reason in ("funnel_l0_fail", "funnel_l0_cull", "funnel_l1_fail", "funnel_l1_cull",
                  "repair_exhausted_or_drift", "gate2_3_fail", "kb_blocked", "immutable_spec_error",
                  "review2_evidence_fail", "review3_fail", "review4_ai_fail",
                  "ai_theoretical_review_required"):
        return "archived"
    if payload.get("success") or (payload.get("final") or {}).get("success"):
        return "success"
    final = (payload.get("final") or {}).get("status") or payload.get("final_status")
    if final:
        if str(final).upper() == "SUCCESS":
            return "success"
        return "archived"
    if payload.get("running"):
        # breakthrough-ish once R3 soft-pass / R4 in flight
        for row in payload.get("pipeline") or []:
            rid = row.get("id")
            if rid in ("r3", "gate2") and row.get("status") == "done":
                return "breakthrough"
            if rid in ("r4", "human", "audit4d") and row.get("status") in ("running", "done"):
                return "breakthrough"
        return "running"
    # Terminal fail marks on pipeline ⇒ archived even without final_status
    for row in payload.get("pipeline") or []:
        if (row or {}).get("id") in ("r1", "r2", "r3", "r4", "l0", "l1", "gate2") and (row or {}).get("status") == "fail":
            return "archived"
    return "idle"


def _slot_from_live_payload(payload, slot_id):
    payload = payload or {}
    strat = payload.get("strategy") or {}
    prog = payload.get("progress") or {}
    final = payload.get("final") or {}
    diag = payload.get("diagnostic") or {}
    status = payload.get("status_label") or ""
    # Rebuild diagnostic if missing but we have metrics / can load failure_context
    if not diag.get("ok"):
        diag = _ensure_diagnostic(payload)
    # Always prefer R1–R4 rows; rebuild whenever payload still carries legacy G0/L0/G2 ids.
    from . import review_lexicon as lex
    pipeline = payload.get("pipeline") or []
    has_new = any((r or {}).get("id") in _NEW_PIPE_IDS for r in pipeline)
    has_legacy = any((r or {}).get("id") in _LEGACY_PIPE_IDS for r in pipeline)
    if (not has_new) or has_legacy or not pipeline:
        try:
            reason = (
                (payload.get("metrics") or {}).get("pipeline_reason")
                or payload.get("phase")
                or (diag.get("pipeline_reason") if isinstance(diag, dict) else None)
            )
            l0 = {}
            g2 = {}
            l1 = {}
            stage = (diag.get("stage") if isinstance(diag, dict) else None) or ""
            fm = (diag.get("fatal_metrics") or {}) if isinstance(diag, dict) else {}
            if stage in ("l0", "r1"):
                l0 = {
                    "pass": False,
                    "metrics": {
                        "triggers": fm.get("n"),
                        "evaluated_bars": fm.get("evaluated_bars"),
                        "density": fm.get("density"),
                    },
                }
            if stage in ("l1", "r2"):
                l1 = {
                    "pass": False,
                    "filled_entries": fm.get("n"),
                    "payoff_ratio": fm.get("payoff"),
                    "expectancy_factor": fm.get("expectancy"),
                }
            if stage in ("gate2", "r3"):
                g2 = {
                    "pass": False,
                    "sample_size": fm.get("n"),
                    "payoff_ratio": fm.get("payoff"),
                    "expectancy_factor": fm.get("expectancy"),
                    "calmar": fm.get("calmar"),
                    "worst5_loss_share": fm.get("w5"),
                    "failed_checks": (diag.get("reject_lines") or []) if isinstance(diag, dict) else [],
                }
                # Under admission_v2 soft-pass, archived-at-gate2 still maps to R3 fail for
                # *legacy hard* archives; fresh runs pass R3 via admission.review3.
                if reason in ("repair_exhausted_or_drift", "gate2_3_fail", "review3_fail"):
                    l1 = {"pass": True, "filled_entries": fm.get("n"), "payoff_ratio": fm.get("payoff")}
                    l0 = {"pass": True, "metrics": {}}
            admission = payload.get("admission_v2") or (payload.get("final") or {}).get("admission_v2") or {}
            pipeline = live_status._gate_rows_from_reason(
                reason, l1=l1, g2=g2, l0=l0, admission=admission,
            )
        except Exception:
            pass
    # Attach rebuilt pipeline for state inference
    payload = dict(payload)
    payload["pipeline"] = pipeline
    if diag:
        # Scrub any legacy Gate/L codes that survived old diagnostic snapshots.
        diag = dict(diag)
        for k in ("terminal_zh", "culled_at", "main_cause_line", "fatal_line", "ai_prompt", "pipeline_reason_zh"):
            if diag.get(k):
                diag[k] = lex.scrub(diag[k])
        mc = diag.get("main_cause")
        if isinstance(mc, dict) and mc.get("title_zh"):
            mc = dict(mc)
            mc["title_zh"] = lex.scrub(mc["title_zh"])
            if mc.get("detail_zh"):
                mc["detail_zh"] = lex.scrub(mc["detail_zh"])
            diag["main_cause"] = mc
        payload["diagnostic"] = diag
    state = _infer_slot_state(payload)
    # ensure humanized status
    if diag.get("terminal_zh") and state in ("archived", "success"):
        status = diag.get("terminal_zh")
    elif final.get("status") or final.get("stop_code"):
        status = humanizer.humanize_final(
            final.get("status"), final.get("stop_code"), success=bool(final.get("success")),
        )
        if diag.get("terminal_zh"):
            status = diag.get("terminal_zh")
    elif status and re_is_raw(status):
        status = humanizer.humanize_status_label(
            phase=payload.get("phase"),
            reason=(payload.get("metrics") or {}).get("pipeline_reason"),
            final_status=final.get("status"),
            stop_code=final.get("stop_code"),
            success=bool(final.get("success")),
        )
    if diag.get("terminal_zh") and state in ("archived", "success"):
        status = diag.get("terminal_zh")
    status = lex.scrub(status or "")
    pct = float(prog.get("percent") or 0)
    eta = prog.get("eta_sec")
    elapsed = prog.get("elapsed_sec")
    return {
        "slot_id": slot_id,
        "state": state,
        "state_label": humanizer.slot_state_label(state),
        "running": bool(payload.get("running")) and state == "running",
        "title_zh": strat.get("title_zh") or "—",
        "family": strat.get("family") or strat.get("family_base") or "—",
        "causal_blurb": strat.get("causal_blurb") or "",
        "status_label": status,
        "percent": 100.0 if state == "archived" else pct,
        "round_label": prog.get("round_label") or "",
        "iteration": prog.get("iteration"),
        "max_iterations": prog.get("max_iterations"),
        "eta_sec": eta,
        "elapsed_sec": elapsed,
        "pipeline": pipeline,
        "pipeline_compact": _compact_pipe(pipeline),
        "diagnostic": diag,
        "final_zh": (
            diag.get("terminal_zh")
            or (
                humanizer.humanize_final(
                    final.get("status"), final.get("stop_code"), success=bool(final.get("success")),
                ) if (final.get("status") or final.get("stop_code")) else None
            )
        ),
        "updated_at": payload.get("updated_at"),
        "workdir": final.get("workdir") or (payload.get("final") or {}).get("workdir"),
        "source": payload.get("source"),
    }


def _ensure_diagnostic(payload):
    """Best-effort rebuild diagnostic from payload metrics or run artifacts."""
    try:
        from . import diagnostic as diagnostic_mod
        existing = payload.get("diagnostic") or {}
        if existing.get("ok") and existing.get("ai_prompt") and existing.get("fatal_metrics", {}).get("n") is not None:
            return existing
        strat = payload.get("strategy") or {}
        metrics = payload.get("metrics") or {}
        wd = (payload.get("final") or {}).get("workdir")
        ctx = {
            "pipeline_reason": metrics.get("pipeline_reason") or payload.get("phase"),
            "l1": {
                "pass": False,
                "filled_entries": metrics.get("l1_filled_entries"),
                "payoff_ratio": metrics.get("l1_payoff"),
                "expectancy_factor": None,
                "reject_reasons": list(metrics.get("l1_reject") or []),
            },
            "gate2_fitness": {
                "payoff_ratio": metrics.get("gate2_payoff"),
                "calmar": metrics.get("gate2_calmar"),
                "worst5_loss_share": metrics.get("gate2_w5"),
                "failed_checks": list(metrics.get("failed_checks") or []),
                "sample_size": None,
                "verdict_tags": [],
            },
            "mechanism_family": strat.get("family"),
            "symbol": strat.get("symbol"),
            "timeframe": strat.get("timeframe"),
        }
        pack = {}
        if wd:
            from pathlib import Path
            import json
            run = Path(wd)
            best_ctx = None
            best_score = -1
            for fc in run.glob("iter_*/failure_context.json"):
                try:
                    cand = json.loads(fc.read_text(encoding="utf-8"))
                except Exception:
                    continue
                score = 0
                l1 = cand.get("l1") or {}
                g2 = cand.get("gate2_fitness") or {}
                if l1.get("filled_entries") is not None:
                    score += 3
                if l1.get("reject_reasons"):
                    score += 2
                if g2.get("failed_checks"):
                    score += 3
                if g2.get("payoff_ratio") is not None:
                    score += 2
                if cand.get("pipeline_reason"):
                    score += 1
                if score > best_score:
                    best_score = score
                    best_ctx = cand
            if best_ctx:
                ctx = best_ctx
            # Enrich empty L1 from seed result files
            l1 = ctx.get("l1") or {}
            if l1.get("filled_entries") is None or not l1.get("reject_reasons"):
                seeds = sorted(run.glob("iter_*/*seed*_result.json")) + sorted(run.glob("iter_*_seed_*_result.json"))
                for sp in reversed(seeds[-12:]):
                    try:
                        sd = json.loads(sp.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    res = sd.get("result") or sd
                    ph = ((res.get("phase3_funnel") or {}).get("l1_micro_screen") or {})
                    if not ph:
                        continue
                    m = ph.get("metrics") or {}
                    if m.get("filled_entries") is None and not ph.get("reject_reasons"):
                        continue
                    ctx["l1"] = {
                        "pass": bool(ph.get("pass")),
                        "reject_reasons": list(ph.get("reject_reasons") or []),
                        "filled_entries": m.get("filled_entries"),
                        "payoff_ratio": m.get("payoff_ratio"),
                        "win_rate": m.get("win_rate"),
                        "expectancy_factor": m.get("expectancy_factor"),
                    }
                    if not ctx.get("pipeline_reason") or ctx.get("pipeline_reason") in ("UNKNOWN_STOPPED", "unknown_stopped", "stopped"):
                        ctx["pipeline_reason"] = res.get("reason") or "funnel_l1_fail"
                    break
            for name in ("pack_final.json", "pack_initial.json"):
                p = run / name
                if p.exists():
                    try:
                        pack = json.loads(p.read_text(encoding="utf-8"))
                        break
                    except Exception:
                        pass
        return diagnostic_mod.build_diagnostic(
            ctx=ctx, pack=pack, title_zh=strat.get("title_zh"),
            symbol=strat.get("symbol"), timeframe=strat.get("timeframe"),
            direction=strat.get("direction"),
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "terminal_zh": "诊断不可用", "ai_prompt": ""}


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


def _payload_mtime(payload):
    """Best-effort mtime for ordering (newer = larger)."""
    payload = payload or {}
    mt = payload.get("_mtime") or 0
    wd = (payload.get("final") or {}).get("workdir")
    try:
        if wd and Path(wd).exists():
            mt = max(float(mt or 0), float(Path(wd).stat().st_mtime))
    except Exception:
        pass
    if not mt:
        try:
            # ISO / common timestamps on live payload
            ts = payload.get("updated_at") or ""
            if ts:
                # tolerate "YYYY-mm-dd HH:MM:SS"
                import datetime as _dt
                for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                    try:
                        mt = _dt.datetime.strptime(str(ts)[:19], fmt).timestamp()
                        break
                    except Exception:
                        continue
        except Exception:
            pass
    return float(mt or 0)


def _is_completed_100(payload):
    """True when campaign finished (archived/success ≈100%) and should count toward prune."""
    if not isinstance(payload, dict):
        return False
    if payload.get("running") and _driver_process_alive():
        # only true-live running escapes completed classification
        src = str(payload.get("source") or "")
        if src in ("", "live") or src.startswith("live"):
            return False
    state = _infer_slot_state(payload)
    if state in ("archived", "success"):
        return True
    diag = payload.get("diagnostic") or {}
    if diag.get("stage") in ("l0", "l1", "gate2", "kb", "spec"):
        return True
    status = str(payload.get("status_label") or diag.get("terminal_zh") or "")
    if "已归档" in status:
        return True
    reason = str(
        (payload.get("metrics") or {}).get("pipeline_reason")
        or payload.get("phase")
        or diag.get("pipeline_reason")
        or ""
    )
    if reason in (
        "funnel_l0_fail", "funnel_l0_cull", "funnel_l1_fail", "funnel_l1_cull",
        "repair_exhausted_or_drift", "gate2_3_fail", "kb_blocked", "immutable_spec_error",
    ):
        return True
    for row in payload.get("pipeline") or []:
        if (row or {}).get("id") in ("l0", "l1", "gate2") and (row or {}).get("status") == "fail":
            return True
    prog = payload.get("progress") or {}
    try:
        pct = float(prog.get("percent") or 0)
    except Exception:
        pct = 0.0
    final = payload.get("final") or {}
    if pct >= 99.5:
        return True
    if final.get("status") or final.get("stop_code") or payload.get("success") or final.get("success"):
        return True
    return False


def _append_display_archive(vector_root, hidden_payloads, reason="completed_overflow_gt_2"):
    """Persist off-display completed slots (oldest pruned when ≥3 completed)."""
    if not hidden_payloads:
        return
    path = display_archive_path(vector_root)
    try:
        existing = []
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                existing = list(data.get("items") or [])
            except Exception:
                existing = []
        seen = {str(it.get("key") or "") for it in existing}
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        for payload in hidden_payloads:
            strat = payload.get("strategy") or {}
            wd = ((payload.get("final") or {}).get("workdir")
                  or payload.get("source")
                  or strat.get("family")
                  or "")
            key = str(wd)
            if not key or key in seen:
                continue
            seen.add(key)
            existing.append({
                "key": key,
                "archived_at": now,
                "reason": reason,
                "title_zh": strat.get("title_zh"),
                "family": strat.get("family") or strat.get("family_base"),
                "final_status": ((payload.get("final") or {}).get("status")
                                 or payload.get("final_status")),
                "stop_code": ((payload.get("final") or {}).get("stop_code")
                              or payload.get("stop_code")),
                "mtime": _payload_mtime(payload),
                "source": payload.get("source"),
            })
        # Cap archive file growth
        existing = existing[-200:]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({
                "schema": "qiyu_auto_driver_slots_display_archive_v1",
                "policy": (
                    "模块内已完成(100%%)卡槽最多保留 %d 个；"
                    "累计≥3 时自动归档时间最旧的，不再于真挚之语模块展示。"
                    % MAX_COMPLETED_VISIBLE
                ),
                "max_completed_visible": MAX_COMPLETED_VISIBLE,
                "updated_at": now,
                "items": existing,
            }, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(path))
    except Exception:
        pass


def prune_completed_for_display(ordered_payloads, vector_root=None,
                                max_completed=None):
    """Keep running/active + newest ≤max_completed finished cards; archive rest.

    Policy: when completed(100%) count ≥ 3, auto-archive oldest off the module.
    """
    max_completed = int(max_completed if max_completed is not None else MAX_COMPLETED_VISIBLE)
    active = []
    completed = []
    for payload in ordered_payloads or []:
        if _is_completed_100(payload):
            completed.append(payload)
        else:
            active.append(payload)
    completed.sort(key=_payload_mtime, reverse=True)  # newest first
    keep = completed[:max_completed]
    hidden = completed[max_completed:]
    if hidden:
        _append_display_archive(vector_root, hidden)
    # Reassemble: running first, then newest completed
    merged = list(active) + list(keep)

    def _sort_key(p):
        running = 0 if p.get("running") else 1
        return (running, -_payload_mtime(p))

    merged.sort(key=_sort_key)
    return merged, hidden


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


def _driver_process_alive():
    """True if auto_driver python process appears running on this host."""
    try:
        import subprocess
        out = subprocess.check_output(
            ["pgrep", "-f", "run_auto_driver.py"], stderr=subprocess.DEVNULL,
        ).decode("utf-8", "ignore").strip()
        return bool(out)
    except Exception:
        return False


def _normalize_payload_running(payload, is_global_live=False):
    """Historical run snapshots must not stay stuck as running=True."""
    payload = dict(payload or {})
    src = str(payload.get("source") or "")
    if is_global_live:
        if payload.get("running") and not _driver_process_alive():
            payload["running"] = False
            # keep status for diagnostics; mark stopped
            if not (payload.get("final") or {}).get("status"):
                payload.setdefault("final", {})
                if isinstance(payload["final"], dict) and not payload["final"].get("status"):
                    payload["final"]["status"] = (
                        (payload.get("metrics") or {}).get("pipeline_reason")
                        or payload.get("phase")
                        or "UNKNOWN_STOPPED"
                    )
        return payload
    # run:/synth: history — never "running"
    payload["running"] = False
    reason = (
        (payload.get("metrics") or {}).get("pipeline_reason")
        or payload.get("phase")
        or ""
    )
    if reason and not (payload.get("final") or {}).get("status"):
        payload.setdefault("final", {})
        if isinstance(payload["final"], dict):
            payload["final"].setdefault("status", str(reason))
    return payload


def build_slots_board(vector_root=None, display_history=5):
    """Assemble multi-slot board for dashboard (capacity + active + recent)."""
    root = Path(vector_root or os.environ.get("VECTOR_ROOT") or "/root")
    total_mb, avail_mb = read_meminfo_mb()
    capacity = recommend_slot_count(total_mb, avail_mb)

    current = live_status.read_live_status(str(root))
    if isinstance(current, dict):
        current = _normalize_payload_running(current, is_global_live=True)
        current["source"] = current.get("source") or "live"
    # Pull enough history to prune completed correctly (keep newest 2 of ≥3).
    recent = _recent_run_payloads(str(root), limit=max(display_history + 6, 12))
    recent = [_normalize_payload_running(p, is_global_live=False) for p in recent]
    # Enrich sparse historical snapshots so prune/state see real cull stage
    enriched = []
    for p in [current] + recent:
        if not isinstance(p, dict):
            continue
        pp = dict(p)
        diag = pp.get("diagnostic") or {}
        if not diag.get("ok") or not diag.get("stage"):
            try:
                pp["diagnostic"] = _ensure_diagnostic(pp)
            except Exception:
                pass
        enriched.append(pp)

    seen = set()
    seen_family_running = set()
    ordered = []
    for payload in enriched:
        if not isinstance(payload, dict):
            continue
        strat = payload.get("strategy") or {}
        title = str(strat.get("title_zh") or "").strip()
        fam = str(strat.get("family") or strat.get("family_base") or "").strip()
        wd = ((payload.get("final") or {}).get("workdir")
              or payload.get("source")
              or fam
              or title
              or "")
        key = str(wd)
        if not key or key in seen:
            continue
        if title in ("", "—", "-") and not payload.get("running"):
            continue
        # One running card per family
        if payload.get("running") and fam:
            if fam in seen_family_running:
                continue
            seen_family_running.add(fam)
        seen.add(key)
        ordered.append(payload)

    ordered.sort(key=lambda p: (0 if p.get("running") else 1, -_payload_mtime(p)))
    # Drop ghost idle/unknown cards when same family already has a terminal archived/success
    terminal_fams = set()
    for p in ordered:
        if _is_completed_100(p):
            fam = str(((p.get("strategy") or {}).get("family") or "")).strip()
            if fam:
                terminal_fams.add(fam)
    filtered = []
    for p in ordered:
        fam = str(((p.get("strategy") or {}).get("family") or "")).strip()
        st = _infer_slot_state(p)
        if fam and fam in terminal_fams and st in ("idle",) and not p.get("running"):
            continue
        filtered.append(p)
    ordered = filtered
    ordered, hidden_completed = prune_completed_for_display(
        ordered, vector_root=str(root), max_completed=MAX_COMPLETED_VISIBLE,
    )

    # Show full pruned set: all non-completed + newest ≤MAX_COMPLETED_VISIBLE finished.
    slots = []
    for i, payload in enumerate(ordered, start=1):
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
    completed_shown = sum(1 for s in slots if s.get("state") in ("archived", "success"))
    board = {
        "schema": "qiyu_auto_driver_slots_v1",
        "ok": True,
        "engine": {
            "name": "真挚之语 (True Words) 并发演进阵列",
            "version": "v2.5",
            "roles": "GLM-5.2 总设计师 · Codex 总工程师 · 四复核（含三AI）+ 人工确认",
        },
        "ram": {
            "total_mb": total_mb,
            "available_mb": avail_mb,
            "total_gb": round(total_mb / 1024.0, 2),
            "available_gb": round(avail_mb / 1024.0, 2),
        },
        "capacity": capacity,
        "active_slots": active,
        "completed_visible": completed_shown,
        "completed_max_visible": MAX_COMPLETED_VISIBLE,
        "completed_hidden": len(hidden_completed or []),
        "slots": slots,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "note_zh": (
            "创立后四复核："
            "【第一次复核】（基础语法、逻辑断言、开仓密度预检）→"
            "【第二次复核】（单标的历史回测与样本收益稳定性）→"
            "【第三次复核】（多标的矩阵验证与抗风险离群测试）→"
            "【第四次复核】（三AI理论复核）；"
            "通过后进入人工确认签发（Wx），永不自动上线。"
            "本机可用内存约 %dMB / 总量 %dMB，并发卡槽容量 %d。"
            "已完成(100%%)卡槽最多展示 %d 个；累计≥3 时自动归档最旧记录，不再显示。"
            "失败则 AI 优化≤3 轮后 Wx 失败播报归档。"
            % (avail_mb, total_mb, capacity, MAX_COMPLETED_VISIBLE)
        ),
        "funnel": {
            "name": "four_review_v2",
            "stages": [
                "第一次复核（基础语法、逻辑断言、开仓密度预检）",
                "第二次复核（单标的历史回测与样本收益稳定性）",
                "第三次复核（多标的矩阵验证与抗风险离群测试）",
                "第四次复核（三AI理论复核）",
                "人工确认签发",
            ],
            "stages_compact": ["R1", "R2", "R3", "R4", "HC"],
            "blocking_profile": "ada_t3_calibrated_v1",
            "legacy_codes_forbidden_in_ui": True,
            "max_ai_optimize": 3,
            "wx_human_confirm_kind": "strategy_pending_confirm",
            "wx_failure_kind": "strategy_review_failed",
            "golden_sample": "codex0725t3_ada5m_trendpb_r42_z2p3_h14",
            "post_pass_gate": "人工确认签发",
        },
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
