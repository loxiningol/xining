# -*- coding: utf-8 -*-
"""Publish Auto-Driver live progress for the dashboard (human-readable)."""
from __future__ import print_function

import json
import os
import re
import time
from pathlib import Path


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
    for key in ("why_edge_exists", "market_inefficiency", "entry_logic", "counterparty_source"):
        val = str(spec.get(key) or "").strip()
        if val:
            # keep first sentence-ish
            cut = re.split(r"[。\n]", val)[0].strip()
            if cut:
                return cut[:180]
    return str(fallback or "机制因果说明待补充")[:180]


def _gate_rows_from_reason(reason, l1=None, g2=None, gates=None):
    """Build pipeline checklist for UI."""
    reason = str(reason or "")
    l1 = l1 or {}
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
    # Infer from pipeline reason when gate payloads sparse
    reached_l1 = reason in ("funnel_l1_fail", "repair_exhausted_or_drift", "gate2_3_fail", "ok", "success") or bool(l1)
    l1_pass = bool(l1.get("pass")) or reason in ("repair_exhausted_or_drift", "gate2_3_fail")
    g2_running = reason in ("repair_exhausted_or_drift", "gate2_3_fail")
    g2_pass = bool(g2.get("pass") or g2.get("gate_pass"))

    rows = [
        {
            "id": "gate0",
            "label": "Gate0 机制完整性",
            "status": _st(g0.get("pass") if g0 else reached_l1 or reason not in ("", "exception", "kb_blocked")),
            "detail": "语义完整 / 因果闭环" if (g0.get("pass") or reached_l1) else (reason or "待执行"),
        },
        {
            "id": "gate1",
            "label": "Gate1 代码忠实度",
            "status": _st(g1.get("pass") if g1 else reached_l1),
            "detail": "DSL 语法通过" if reached_l1 else "待执行",
        },
        {
            "id": "l1",
            "label": "L1 微观筛选器",
            "status": "done" if l1_pass else ("running" if reason == "funnel_l1_fail" else ("pending" if not reached_l1 else "fail")),
            "detail": (
                "样本 n=%s / payoff=%s" % (
                    l1.get("filled_entries") if l1.get("filled_entries") is not None else "—",
                    ("%.2f" % float(l1["payoff_ratio"])) if l1.get("payoff_ratio") is not None else "—",
                )
                if reached_l1 else "待执行"
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


def publish_live_status(cfg, state, pack, *, phase="running", result=None,
                        seed_idx=None, seed_max=None, message=None):
    """Write dashboard-facing live status JSON (atomic)."""
    path = live_status_path(cfg.get("vector_root"))
    path.parent.mkdir(parents=True, exist_ok=True)
    spec = (pack or {}).get("mechanism_spec") or {}
    fam = spec.get("mechanism_family")
    symbol = cfg.get("symbol")
    timeframe = cfg.get("timeframe")
    title = humanize_mechanism_title(symbol, timeframe, fam, spec.get("mechanism_name"), spec)
    causal = humanize_causal_blurb(spec)

    iters = list((state or {}).get("iterations") or [])
    last = iters[-1] if iters else {}
    reason = None
    if isinstance(result, dict):
        reason = result.get("reason") or result.get("stage")
    reason = reason or last.get("pipeline_reason") or phase

    l1 = last.get("l1") or {}
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
        except Exception:
            gates = []
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

    status_label_map = {
        "running": "自主演进运行中",
        "seed": "L1 种子重试中",
        "l1": "L1 微观筛选中",
        "calling_ai": "三方 AI 出补丁中 (DeepSeek / Qwen / GLM)",
        "ai": "三方 AI 出补丁中 (DeepSeek / Qwen / GLM)",
        "patch": "应用补丁并 DSL 校验",
        "repair_exhausted_or_drift": "正在进行 Gate2 适应度门禁",
        "funnel_l1_fail": "L1 未过 · 准备结构补丁",
        "success": "已通过流水线 · 等待人类确认",
        "limit_reached_failed": "已触达极限并归档 (LIMIT_REACHED)",
        "converged_no_improvement": "收敛无改善 · 已归档",
        "no_progress_repeated_reason": "无进展重复失败 · 已归档 LIMIT_REACHED",
        "max_iterations": "已达最大轮次 · 归档",
        "ai_limit_reached": "AI 判定无优化空间 · 归档",
        "stopped": "已停止",
    }
    phase_l = str(phase).lower()
    reason_l = str(reason).lower()
    if state.get("final_status"):
        # Prefer terminal labels over last mid-pipeline reason
        status_label = (
            status_label_map.get(phase_l)
            or status_label_map.get(str(state.get("stop_code") or "").lower())
            or status_label_map.get(str(state.get("final_status") or "").lower())
            or ("已通过流水线 · 等待人类确认" if state.get("success") else "已结束并归档")
        )
        pct = 100.0
    else:
        status_label = status_label_map.get(phase_l) or status_label_map.get(reason_l) or "自主演进运行中"
        if seed_idx and seed_max:
            status_label = "L1 种子重试 %s/%s · %s" % (seed_idx, seed_max, status_label)

    eta = _eta_sec(elapsed, pct)
    payload = {
        "schema": "qiyu_auto_driver_live_v1",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "running": not bool(state.get("final_status")),
        "phase": phase,
        "status_label": status_label,
        "message": message,
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
        "pipeline": _gate_rows_from_reason(reason, l1=l1, g2=g2, gates=gates),
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
            "ai_decision": last.get("ai_decision"),
        },
        "final": {
            "status": state.get("final_status"),
            "stop_code": state.get("stop_code"),
            "success": bool(state.get("success")),
            "workdir": state.get("workdir") or cfg.get("workdir"),
            "report_path": None,
        },
        "charter_version": "true_words_v2.5",
        "human_confirm_required": True,
        "auto_mount": False,
    }
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
