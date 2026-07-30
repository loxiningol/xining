# -*- coding: utf-8 -*-
"""Publish Auto-Driver live progress for the dashboard (human-readable)."""
from __future__ import print_function

import json
import os
import re
import time
from pathlib import Path


from . import status_humanizer as humanizer


LIVE_STATUS_NAME = "auto_driver_live.json"


def live_status_path(vector_root=None):
    root = Path(vector_root or os.environ.get("VECTOR_ROOT") or "/root")
    return root / "auto_trade" / "dual_engine" / LIVE_STATUS_NAME


def _sym_short(symbol):
    return str(symbol or "").replace("-USDT-SWAP", "").replace("-SWAP", "") or "?"


def humanize_mechanism_title(symbol, timeframe, family=None, mechanism_name=None,
                             mechanism_spec=None):
    """Natural-language strategy title: [SYM TFm] Causal action (Scene)."""
    spec = mechanism_spec or {}
    fam = str(family or spec.get("mechanism_family") or mechanism_name or "").strip()
    base = re.sub(r"_ad\d+$", "", fam)
    sym = _sym_short(symbol)
    tf = str(timeframe or spec.get("suitable_timeframes", ["?"])[0] if isinstance(spec.get("suitable_timeframes"), list) else timeframe or "?")

    # Family → (core action ZH, scene EN short)
    catalog = {
        "session_vol_squeeze_expansion": ("亚盘狭幅压缩突破", "Session Volatility Squeeze"),
        "session_vol_squeeze_expansion_v1": ("亚盘狭幅压缩突破", "Session Volatility Squeeze"),
        "asia_sweep_fade": ("亚盘极值扫单反手", "Asia Sweep Fade"),
        "asia_sweep_fade_v1": ("亚盘极值扫单反手", "Asia Sweep Fade"),
        "asia_range_sweep_fade": ("亚盘极值扫单反手", "Asia Sweep Fade"),
        "session_liq_engulf_displace_matrix": ("会话流动性吞没置换", "Session Liq Engulf"),
        "macro_sfp_displacement": ("宏观流动性扫荡回收", "Macro SFP Displacement"),
        "macro_sfp_unblocked_v1": ("宏观流动性扫荡回收 · 解封", "Macro SFP Unblocked"),
        "macro_sfp_unblocked": ("宏观流动性扫荡回收 · 解封", "Macro SFP Unblocked"),
        "rolling_4h_sweep_5m_v1": ("滚动4小时极值流动性扫荡", "Rolling 4H Sweep Fade"),
        "rolling_4h_sweep_5m": ("滚动4小时极值流动性扫荡", "Rolling 4H Sweep Fade"),
        "compression_release_structural_breakout": ("波动压缩后结构突破", "Compression Release Breakout"),
        "htf_slope_aligned_micro_pullback": ("高周期顺势回踩续涨", "HTF Slope Pullback"),
        "stop_hunt_range_reclaim": ("假突破扫损回收", "Stop Hunt Reclaim"),
        "exhaustion_fade_short": ("动量衰竭反转", "Exhaustion Fade"),
        "session_trend_pullback": ("顺势极值回升", "Trend Pullback"),
    }
    action, scene = catalog.get(base, (None, None))
    if not action:
        # Heuristic from tokens
        tokens = base.lower()
        if "squeeze" in tokens or "compression" in tokens:
            action, scene = "波动压缩突破", "Vol Squeeze Expansion"
        elif "exhaustion" in tokens or "fade" in tokens or "xrpport" in tokens:
            action, scene = "动量衰竭反转", "Exhaustion Fade"
        elif "pullback" in tokens or "trendpb" in tokens or "trend_pb" in tokens:
            action, scene = "顺势极值回升", "Trend Pullback"
        elif "breakout" in tokens:
            action, scene = "结构突破延续", "Structural Breakout"
        elif "sfp" in tokens or "sweep" in tokens:
            action, scene = "流动性扫荡回收", "Liquidity Sweep Reclaim"
        elif "engulf" in tokens or "displace" in tokens:
            action, scene = "会话流动性吞没置换", "Session Liq Engulf"
        elif re.search(r"(^|_)(tp|h|r|c)\d+", tokens) or re.fullmatch(r"[a-z]{2,6}(_[a-z]*\d+)+", tokens):
            # Param-soup keys (sol_tp47_h32_r46_c20) → generic trend-pullback family
            action, scene = "顺势极值回升", "Trend Pullback"
        else:
            pretty = re.sub(r"[_-]+", " ", base).strip() or "未命名机制"
            # still reject pure ascii param soup as display action
            if re.search(r"\b(tp|h|r|c)\d+\b", pretty, re.I):
                action, scene = "机制候选", "Candidate"
            else:
                action, scene = pretty, "Mechanism"
    sym = sym or "?"
    tf = "" if (not tf or str(tf) in ("?", "None", "null")) else str(tf)
    if tf:
        return "[%s %s] %s (%s)" % (sym, tf, action, scene)
    return "[%s] %s (%s)" % (sym, action, scene)


def humanize_causal_blurb(mechanism_spec=None, fallback=None):
    spec = mechanism_spec or {}
    return humanizer.prefer_chinese_text(
        spec.get("counterparty_source"),
        spec.get("market_inefficiency"),
        spec.get("why_edge_exists"),
        spec.get("entry_logic"),
        fallback=fallback or "机制因果说明待补充",
        max_len=160,
    )


def _gate_rows_from_reason(reason, l1=None, g2=None, gates=None, l0=None):
    """Build pipeline checklist for UI (lightweight funnel: G0→G1→L0→L1→G2→4D)."""
    reason = str(reason or "")
    l1 = l1 or {}
    l0 = l0 or {}
    g2 = g2 or {}
    by_id = {}
    for g in gates or []:
        if isinstance(g, dict) and g.get("gate_id"):
            by_id[str(g["gate_id"])] = g

    def _st(passed, running=False, pending=False):
        if passed:
            return "done"
        if running:
            return "running"
        if pending:
            return "pending"
        return "pending"

    g0 = by_id.get("gate0_mechanism_integrity") or {}
    g1 = by_id.get("gate1_fidelity") or by_id.get("gate1_code_fidelity") or {}

    is_l0_fail = reason in ("funnel_l0_fail", "funnel_l0_cull")
    is_l1_fail = reason in ("funnel_l1_fail", "funnel_l1_cull")
    reached_l0 = is_l0_fail or is_l1_fail or reason in (
        "repair_exhausted_or_drift", "gate2_3_fail", "ok", "success",
    ) or bool(l0) or bool(l1)
    # Gate0/1 are upstream of L0; if we have an L0/L1 verdict they already passed
    reached_gates = reached_l0 or reason not in ("", "exception", "kb_blocked")
    l0_pass = bool(l0.get("pass")) or (reached_l0 and not is_l0_fail and (
        is_l1_fail or reason in ("repair_exhausted_or_drift", "gate2_3_fail", "ok", "success")
    ))
    l0_m = l0.get("metrics") or {}
    reached_l1 = is_l1_fail or reason in ("repair_exhausted_or_drift", "gate2_3_fail", "ok", "success") or (
        bool(l1) and not is_l0_fail
    )
    l1_pass = bool(l1.get("pass")) or reason in ("repair_exhausted_or_drift", "gate2_3_fail")
    g2_running = reason in ("repair_exhausted_or_drift", "gate2_3_fail")
    g2_pass = bool(g2.get("pass") or g2.get("gate_pass"))

    rows = [
        {
            "id": "gate0",
            "label": "Gate0 机制完整性",
            "status": _st(g0.get("pass") if g0 else reached_gates),
            "detail": "语义完整 / 因果闭环" if (g0.get("pass") or reached_gates) else (reason or "待执行"),
        },
        {
            "id": "gate1",
            "label": "Gate1 代码忠实度",
            "status": _st(g1.get("pass") if g1 else reached_l0),
            "detail": "DSL 语法通过" if reached_l0 else "待执行",
        },
        {
            "id": "l0",
            "label": "L0 开仓密度预检",
            "status": (
                "done" if l0_pass else (
                    "fail" if is_l0_fail else (
                        "running" if reason in ("seed", "l1") and not reached_l0 else (
                            "pending" if not reached_l0 else "fail"
                        )
                    )
                )
            ),
            "detail": (
                "触发 %s / %s (密度 %s)" % (
                    l0_m.get("triggers") if l0_m.get("triggers") is not None else "—",
                    l0_m.get("evaluated_bars") if l0_m.get("evaluated_bars") is not None else "—",
                    l0_m.get("density") if l0_m.get("density") is not None else "—",
                )
                if reached_l0 else "待执行 · 秒杀过稀逻辑"
            ),
        },
        {
            "id": "l1",
            "label": "L1 微观筛选器",
            "status": (
                "done" if l1_pass else (
                    "fail" if is_l1_fail or (reached_l1 and not l1_pass) else (
                        "pending" if is_l0_fail or not reached_l1 else "pending"
                    )
                )
            ),
            "detail": (
                "样本 n=%s / payoff=%s" % (
                    l1.get("filled_entries") if l1.get("filled_entries") is not None else "—",
                    ("%.2f" % float(l1["payoff_ratio"])) if l1.get("payoff_ratio") is not None else "—",
                )
                if reached_l1 and not is_l0_fail else (
                    "未进入（L0 未过）" if is_l0_fail else "待执行"
                )
            ),
        },
        {
            "id": "gate2",
            "label": "Gate2 / L2 门禁",
            "status": "done" if g2_pass else ("fail" if (g2_running or reason in ("repair_exhausted_or_drift", "gate2_3_fail")) else "pending"),
            "detail": (
                "Calmar=%s, Payoff=%s, w5=%s" % (
                    ("%.2f" % float(g2["calmar"])) if g2.get("calmar") is not None else "—",
                    ("%.2f" % float(g2["payoff_ratio"])) if g2.get("payoff_ratio") is not None else "—",
                    ("%.2f" % float(g2["worst5_loss_share"])) if g2.get("worst5_loss_share") is not None else "—",
                )
                if (g2_running or g2_pass or g2.get("payoff_ratio") is not None) else "待执行"
            ),
        },
        {
            "id": "audit4d",
            "label": "四维攻击复核",
            "status": "done" if g2_pass else "pending",
            "detail": "未进入 (Gate2 未过)" if not g2_pass else "进入四维复核 (因果 / 博弈 / 回测诚信 / 执行摩擦)",
        },
    ]
    return rows


def _progress_pct(iteration, max_iter, phase_hint=None):
    max_iter = max(int(max_iter or 1), 1)
    iteration = max(int(iteration or 1), 1)
    base = (iteration - 1) / float(max_iter)
    # within-round weight
    phase = str(phase_hint or "")
    bonus = 0.15
    if phase in ("calling_ai", "ai"):
        bonus = 0.55
    elif phase in ("patch", "validate"):
        bonus = 0.75
    elif phase in ("l1", "seed"):
        bonus = 0.35
    elif phase in ("gate2", "repair_exhausted_or_drift"):
        bonus = 0.65
    elif phase in ("done", "success"):
        return 100.0
    pct = (base + bonus / float(max_iter)) * 100.0
    return round(max(1.0, min(99.0, pct)), 1)


def _eta_sec(elapsed, pct):
    try:
        elapsed = float(elapsed or 0)
        pct = float(pct or 0)
        if pct <= 1e-6 or elapsed <= 0:
            return None
        total = elapsed / (pct / 100.0)
        return max(0, int(round(total - elapsed)))
    except Exception:
        return None


def _select_dsl_for_diag(pack, direction):
    pack = pack or {}
    if str(direction or "long").lower() == "short":
        return pack.get("dsl_short") or pack.get("dsl") or {}
    return pack.get("dsl_long") or pack.get("dsl") or {}


def publish_live_status(cfg, state, pack, *, phase="running", result=None,
                        seed_idx=None, seed_max=None, message=None,
                        write_global=True):
    """Write dashboard-facing live status JSON (atomic)."""
    path = live_status_path(cfg.get("vector_root"))
    if write_global:
        path.parent.mkdir(parents=True, exist_ok=True)
    spec = (pack or {}).get("mechanism_spec") or {}
    fam = spec.get("mechanism_family")
    symbol = cfg.get("symbol")
    timeframe = cfg.get("timeframe")
    title = humanize_mechanism_title(symbol, timeframe, fam, spec.get("mechanism_name"), spec)
    # Prefer Chinese display title from pack meta if present
    meta_title = ((pack or {}).get("meta") or {}).get("title_zh") or ((pack or {}).get("meta") or {}).get("title")
    if meta_title and re.search(r"[\u4e00-\u9fff]", str(meta_title)):
        # keep symbol/tf prefix style when meta is campaign title
        if str(meta_title).startswith("["):
            title = str(meta_title)
        else:
            title = humanize_mechanism_title(symbol, timeframe, fam, meta_title, spec)
    causal = humanize_causal_blurb(spec)

    iters = list((state or {}).get("iterations") or [])
    last = iters[-1] if iters else {}
    reason = None
    if isinstance(result, dict):
        reason = result.get("reason") or result.get("stage")
    reason = reason or last.get("pipeline_reason") or phase

    l1 = last.get("l1") or {}
    l0 = last.get("l0") or {}
    g2 = last.get("gate2") or {}
    if isinstance(result, dict):
        try:
            from . import metrics as metrics_mod
            ctx = metrics_mod.build_failure_context(
                result, pack, state.get("n_iterations") or len(iters) or 1,
                symbol, timeframe, cfg.get("direction") or "long",
            )
            l1 = ctx.get("l1") or l1
            g2 = ctx.get("gate2_fitness") or g2
            gates = ctx.get("gates") or []
            ph = (result.get("phase3_funnel") or {})
            if ph.get("l0_density"):
                l0 = ph.get("l0_density") or l0
        except Exception:
            gates = []
            try:
                ph = (result.get("phase3_funnel") or {})
                if ph.get("l0_density"):
                    l0 = ph.get("l0_density") or l0
            except Exception:
                pass
    else:
        gates = []

    max_iter = int(cfg.get("max_iterations") or 10)
    cur_iter = int(state.get("n_iterations") or len(iters) or 1)
    elapsed = float(state.get("elapsed_sec") or 0)
    if not elapsed and state.get("_t0"):
        elapsed = time.time() - float(state["_t0"])
    pct = _progress_pct(cur_iter, max_iter, phase_hint=reason or phase)
    if state.get("success") or state.get("final_status") == "SUCCESS":
        pct = 100.0
        phase = "success"
    elif state.get("final_status"):
        phase = str(state.get("stop_code") or state.get("final_status") or "stopped").lower()
        pct = 100.0

    status_label = humanizer.humanize_status_label(
        phase=phase,
        reason=reason,
        final_status=state.get("final_status"),
        stop_code=state.get("stop_code"),
        success=bool(state.get("success")),
        seed_idx=seed_idx,
        seed_max=seed_max,
    )
    # Build / refresh diagnostic post-mortem
    diagnostic = last.get("diagnostic") if isinstance(last, dict) else None
    try:
        from . import diagnostic as diagnostic_mod
        ctx_like = {
            "pipeline_reason": reason,
            "l0": l0,
            "l1": l1,
            "gate2_fitness": g2,
            "mechanism_family": fam,
            "dsl": _select_dsl_for_diag(pack, cfg.get("direction")),
            "mechanism_spec": spec,
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": cfg.get("direction"),
        }
        diagnostic = diagnostic_mod.build_diagnostic(
            ctx=ctx_like, result=result if isinstance(result, dict) else None,
            pack=pack, title_zh=title,
            symbol=symbol, timeframe=timeframe, direction=cfg.get("direction"),
        )
        if state.get("final_status") and diagnostic.get("terminal_zh"):
            # Prefer cull-stage terminal label over generic stop codes
            if diagnostic.get("stage") in ("l0", "l1", "gate2", "kb", "spec"):
                status_label = diagnostic.get("terminal_zh")
    except Exception as exc:
        diagnostic = diagnostic or {"ok": False, "error": str(exc)}

    message_zh = None
    if message:
        message_zh = humanizer.humanize_code(message, fallback=str(message))

    eta = _eta_sec(elapsed, pct)
    final_zh = None
    if state.get("final_status") or state.get("stop_code"):
        final_zh = humanizer.humanize_final(
            state.get("final_status"), state.get("stop_code"),
            success=bool(state.get("success")),
        )
        if diagnostic and diagnostic.get("terminal_zh") and diagnostic.get("stage") in ("l1", "gate2", "kb", "spec"):
            final_zh = "%s；%s" % (diagnostic.get("terminal_zh"), final_zh)
    payload = {
        "schema": "qiyu_auto_driver_live_v1",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "running": not bool(state.get("final_status")),
        "phase": phase,
        "phase_zh": humanizer.humanize_code(phase),
        "status_label": status_label,
        "message": message_zh or message,
        "engine": {
            "name": "真挚之语 (True Words) 自主量化演进引擎",
            "version": "v2.5",
            "roles": "GLM-5.2 总设计师 · Codex 总工程师 · 四维正式复核",
        },
        "strategy": {
            "title_zh": title,
            "family": fam,
            "family_base": re.sub(r"_ad\d+$", "", str(fam or "")),
            "symbol": symbol,
            "symbol_short": _sym_short(symbol),
            "timeframe": timeframe,
            "direction": cfg.get("direction"),
            "causal_blurb": causal,
            "mechanism_name": spec.get("mechanism_name"),
        },
        "progress": {
            "percent": pct,
            "iteration": cur_iter,
            "max_iterations": max_iter,
            "round_label": "Round %d/%d" % (cur_iter, max_iter),
            "elapsed_sec": int(round(elapsed)),
            "eta_sec": eta,
            "seed_idx": seed_idx,
            "seed_max": seed_max,
        },
        "pipeline": _gate_rows_from_reason(reason, l1=l1, g2=g2, gates=gates, l0=l0),
        "diagnostic": diagnostic,
        "metrics": {
            "composite_score": last.get("composite_score"),
            "total_gap": last.get("total_gap"),
            "l1_filled_entries": l1.get("filled_entries"),
            "l1_payoff": l1.get("payoff_ratio"),
            "gate2_payoff": g2.get("payoff_ratio"),
            "gate2_calmar": g2.get("calmar"),
            "gate2_w5": g2.get("worst5_loss_share"),
            "failed_checks": last.get("failed_checks") or g2.get("failed_checks"),
            "task_id": last.get("task_id") or (result or {}).get("task_id") if isinstance(result, dict) else last.get("task_id"),
            "pipeline_reason": reason,
            "pipeline_reason_zh": humanizer.humanize_code(reason),
            "ai_decision": last.get("ai_decision"),
        },
        "final": {
            "status": state.get("final_status"),
            "stop_code": state.get("stop_code"),
            "status_zh": final_zh,
            "success": bool(state.get("success")),
            "workdir": state.get("workdir") or cfg.get("workdir"),
            "report_path": None,
        },
        "charter_version": "true_words_v2.5",
        "human_confirm_required": True,
        "auto_mount": False,
    }
    if write_global:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        os.replace(str(tmp), str(path))
    # also mirror into workdir if known
    wd = state.get("workdir") or cfg.get("workdir")
    if wd:
        try:
            wdp = Path(wd) / LIVE_STATUS_NAME
            wdp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass
    # refresh multi-slot board (best-effort)
    if write_global:
        try:
            from . import multi_slot
            multi_slot.build_slots_board(cfg.get("vector_root"))
        except Exception:
            pass
    return payload


def read_live_status(vector_root=None):
    path = live_status_path(vector_root)
    if not path.exists():
        # fallback: synthesize from newest auto_driver_runs
        return _synthesize_from_runs(vector_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc), "running": False}
    # stale guard: if marked running but process gone & file old > 30min
    try:
        age = time.time() - path.stat().st_mtime
        if data.get("running") and age > 1800:
            data["running"] = False
            data["status_label"] = (data.get("status_label") or "") + " · 状态可能已过期"
            data["stale"] = True
    except Exception:
        pass
    data["ok"] = True
    data["source"] = str(path)
    return data


def _synthesize_from_runs(vector_root=None):
    root = Path(vector_root or os.environ.get("VECTOR_ROOT") or "/root")
    runs_dir = root / "auto_trade" / "dual_engine" / "workflow_v2" / "auto_driver_runs"
    if not runs_dir.exists():
        return {
            "ok": True,
            "running": False,
            "status_label": "当前无 Auto-Driver 任务",
            "engine": {
                "name": "真挚之语 (True Words) 自主量化演进引擎",
                "version": "v2.5",
            },
            "strategy": {"title_zh": "—"},
            "progress": {"percent": 0, "iteration": 0, "max_iterations": 0},
            "pipeline": [],
            "source": "empty",
        }
    runs = sorted([p for p in runs_dir.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True)
    if not runs:
        return {"ok": True, "running": False, "status_label": "当前无 Auto-Driver 任务", "pipeline": [], "source": "empty"}
    latest = runs[0]
    cfg = {}
    pack = {}
    try:
        cfg = json.loads((latest / "config.used.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        pack = json.loads((latest / "pack_initial.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    state = {"workdir": str(latest), "iterations": [], "n_iterations": 0}
    # recover last failure context
    iters = sorted(latest.glob("iter_*"))
    result = None
    if iters:
        state["n_iterations"] = len(iters)
        fc = iters[-1] / "failure_context.json"
        if fc.exists():
            try:
                ctx = json.loads(fc.read_text(encoding="utf-8"))
                state["iterations"] = [{
                    "iteration": ctx.get("iteration") or len(iters),
                    "pipeline_reason": ctx.get("pipeline_reason"),
                    "composite_score": ctx.get("composite_score"),
                    "total_gap": None,
                    "l1": ctx.get("l1"),
                    "gate2": ctx.get("gate2_fitness"),
                    "failed_checks": (ctx.get("gate2_fitness") or {}).get("failed_checks"),
                    "task_id": ctx.get("task_id"),
                }]
                result = {"reason": ctx.get("pipeline_reason"), "task_id": ctx.get("task_id")}
            except Exception:
                pass
    # running if process alive
    running = _auto_driver_process_alive()
    if (latest / "driver_state.json").exists():
        try:
            ds = json.loads((latest / "driver_state.json").read_text(encoding="utf-8"))
            state.update({k: ds.get(k) for k in ("final_status", "stop_code", "success", "elapsed_sec", "iterations") if k in ds})
            if ds.get("final_status"):
                running = False
        except Exception:
            pass
    state["_t0"] = latest.stat().st_mtime
    if not running and not state.get("final_status"):
        # finished without state file
        state["final_status"] = "UNKNOWN_STOPPED"
    pub = publish_live_status(cfg or {"symbol": "?", "timeframe": "?", "direction": "long", "max_iterations": 10},
                             state, pack, phase="running" if running else "stopped", result=result)
    pub["source"] = "synthesized:%s" % latest.name
    pub["running"] = running
    return pub


def _auto_driver_process_alive():
    try:
        import subprocess
        out = subprocess.check_output(
            ["pgrep", "-af", "run_auto_driver"], stderr=subprocess.DEVNULL,
        ).decode("utf-8", "ignore")
        for line in out.splitlines():
            if "run_auto_driver" in line and "pgrep" not in line:
                return True
        return False
    except Exception:
        return False
