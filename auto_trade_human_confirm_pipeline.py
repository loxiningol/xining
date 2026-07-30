# -*- coding: utf-8 -*-
"""Human-confirm strategy pipeline (designer rule, 2026-07-24).

Create → machine screen → WxPusher push → human confirm → B(30%) live
→ auto B/C stop-loss monitor (Wx notify on open/downgrade/delete).

Grades (equity ratio, leverage fixed 20x):
  S=70%  A=50%  B=30%  C=15%

Never auto-live without human confirm. E/D mass probes are frozen.
"""
from __future__ import print_function

from datetime import datetime
from pathlib import Path
import json
import os
import tempfile

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
PENDING_PATH = AUTO_DIR / "strategy_pending_human_confirm.json"
FAILURE_VAULT = AUTO_DIR / "strategy_failure_vault.json"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"
DSL_CONFIG_PATH = ROOT / "strategy_configs" / "ai_dsl_strategies.json"
AUDIT_PATH = AUTO_DIR / "human_confirm_pipeline_audit.jsonl"
STATE_PATH = AUTO_DIR / "human_confirm_pipeline_state.json"
MASS_FREEZE_FLAG = AUTO_DIR / "mass_engine_frozen.json"

GRADE_RATIO = {"S": 0.70, "A": 0.50, "B": 0.30, "C": 0.15}
LEVERAGE = 20
STOP_LOSS_PCT = 0.009
MAX_DD = 0.40
MIN_TRADES_SCREEN = 10  # designer 2026-07-24: sample ≥10
MIN_WIN_RATE_SCREEN = 50.0  # designer: wr ≥50% under real cost
N_FOLDS = 5
# Triple-friction is NOT a screen gate; used only as post-live risk tip.
TRIPLE_FRICTION_SCENARIO = "severe"
# HARD KILL-SWITCH: never Wx-push triple-friction tips (designer 2026-07-25).
# Callers cannot override this; _wx also refuses the tip kind/title.
TRIPLE_FRICTION_TIP_WX_ENABLED = False
TRIPLE_FRICTION_TIP_WX_KIND = "strategy_triple_friction_tip"
TRIPLE_FRICTION_TIP_WX_MARKER = "三倍摩擦风险提示"


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


def _append_audit(row):
    path = Path(AUDIT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _wx(text, kind="human_confirm_pipeline", meta=None):
    # Permanent block: triple-friction tip must never reach WxPusher.
    kind_s = str(kind or "")
    text_s = str(text or "")
    if (not TRIPLE_FRICTION_TIP_WX_ENABLED) and (
            kind_s == TRIPLE_FRICTION_TIP_WX_KIND
            or TRIPLE_FRICTION_TIP_WX_MARKER in text_s
            or "三倍摩擦压力提示" in text_s):
        _append_audit({
            "time": _now(),
            "event": "wx_blocked_triple_friction_tip",
            "kind": kind_s,
            "text_head": text_s[:200],
        })
        return {"ok": True, "sent": False, "blocked": True,
                "reason": "triple_friction_tip_wx_hard_disabled"}
    try:
        import auto_trade_formal_notify as notify
        return notify.send_message(text, kind=kind, meta=meta or {})
    except Exception as exc:
        _append_audit({"time": _now(), "event": "wx_fail", "error": str(exc),
                       "text_head": str(text)[:200]})
        return {"ok": False, "error": str(exc)}


# ─── Freeze mass / E-D probes ─────────────────────────────────────────

def freeze_mass_and_ed_probes():
    """Stop mass engine + pause every mass/E/D probe assignment."""
    flag = {
        "frozen": True,
        "frozen_at": _now(),
        "reason": "designer_rule_human_confirm_b_pipeline",
        "policy": "no_mass_no_ED_auto_live",
    }
    _atomic(MASS_FREEZE_FLAG, flag)

    # Disable timer if present
    try:
        import subprocess
        subprocess.call(["systemctl", "stop", "qiyu-mass-engine.timer"])
        subprocess.call(["systemctl", "disable", "qiyu-mass-engine.timer"])
        for u in ("qiyu-mass-probation2", "qiyu-mass-probation-rescreen",
                  "qiyu-mass-bootstrap4", "qiyu-mass-engine"):
            subprocess.call(["systemctl", "stop", u])
    except Exception:
        pass

    controls = _read(CONTROL_PATH, {"assignments": {}})
    paused = []
    for aid, row in list((controls.get("assignments") or {}).items()):
        row = dict(row)
        is_mass = bool(row.get("mass_engine"))
        grade = str(row.get("lifecycle_grade") or row.get("max_grade") or "").upper()
        if is_mass or grade in ("E", "D"):
            row["pause_new_entries"] = True
            row["new_entries_allowed"] = False
            row["frozen_by_human_pipeline"] = True
            row["frozen_at"] = _now()
            if str(row.get("audit_state") or "") == "conditional_frequency_probe":
                row["audit_state"] = "failed_closed"
            controls["assignments"][aid] = row
            paused.append(aid)
            # disable DSL live flag
            key = aid.split("|", 2)[-1] if "|" in aid else ""
            if key:
                _set_dsl_live(key, False)
    controls["updated_at"] = _now()
    controls["updated_by"] = "freeze_mass_and_ed_probes"
    _atomic(CONTROL_PATH, controls)
    _append_audit({"time": _now(), "event": "freeze_mass_ed", "paused": paused})
    _wx(
        "【系统冻结通知】\n"
        "工业化量产引擎与全部 E/D 级探针已冻结停用。\n"
        "新流程：初筛→推送→人工确认→B级(30%%)上线。\n"
        "已暂停赋值: %s 个\n时间: %s" % (len(paused), _now()),
        kind="pipeline_freeze",
        meta={"paused_n": len(paused)},
    )
    return {"ok": True, "paused": paused, "n": len(paused)}


def mass_is_frozen():
    return bool((_read(MASS_FREEZE_FLAG, {}) or {}).get("frozen"))


# ─── DSL / daemon helpers ─────────────────────────────────────────────

def _set_dsl_live(key, live):
    data = _read(DSL_CONFIG_PATH, {"strategies": []})
    rows = data.get("strategies") or []
    ALLOWED = {
        "schema", "key", "name", "direction", "timeframe",
        "supported_instruments", "entry", "exit", "max_hold_bars",
        "description", "origin", "version", "live_enabled",
        "approved_version_hash", "auto_trade_eligible",
    }
    found = False
    for i, row in enumerate(rows):
        if row.get("key") == key:
            slim = {k: v for k, v in row.items() if k in ALLOWED}
            slim["live_enabled"] = bool(live)
            slim["auto_trade_eligible"] = bool(live)
            rows[i] = slim
            found = True
            break
    if found:
        data["strategies"] = rows
        data["updated_at"] = _now()
        _atomic(DSL_CONFIG_PATH, data)
    return found


def _upsert_dsl(definition, live=False):
    import auto_trade_strategy_dsl as dsl_mod
    definition = dsl_mod.validate_strategy(definition)
    data = _read(DSL_CONFIG_PATH, {"schema": "qiyu_ai_dsl_strategies_v1",
                                   "strategies": []})
    ALLOWED = {
        "schema", "key", "name", "direction", "timeframe",
        "supported_instruments", "entry", "exit", "max_hold_bars",
        "description", "origin", "version", "live_enabled",
        "approved_version_hash", "auto_trade_eligible",
    }
    row = {k: v for k, v in definition.items() if k in ALLOWED}
    row["live_enabled"] = bool(live)
    row["auto_trade_eligible"] = bool(live)
    key = row.get("key")
    rows = list(data.get("strategies") or [])
    found = False
    for i, existing in enumerate(rows):
        if existing.get("key") == key:
            rows[i] = row
            found = True
            break
    if not found:
        rows.append(row)
    data["strategies"] = rows
    data["updated_at"] = _now()
    _atomic(DSL_CONFIG_PATH, data)
    return row


def _daemon_config_name(symbol, timeframe):
    mapping = {
        ("BTC-USDT-SWAP", "15m"): "formal_daemon_config_btc_15m.json",
        ("BTC-USDT-SWAP", "5m"): "formal_daemon_config_btc_5m.json",
        ("NG-USDT-SWAP", "5m"): "formal_daemon_config_ng_5m.json",
        ("ADA-USDT-SWAP", "5m"): "formal_daemon_config_ada_5m.json",
        ("XAG-USDT-SWAP", "5m"): "formal_daemon_config_xag_5m.json",
        ("XAU-USDT-SWAP", "15m"): "formal_daemon_config_xau_15m.json",
        ("CL-USDT-SWAP", "5m"): "formal_daemon_config_cl_5m.json",
    }
    return mapping.get((symbol, timeframe))


def _ensure_daemon_key(symbol, timeframe, key):
    name = _daemon_config_name(symbol, timeframe)
    if not name:
        return False
    path = AUTO_DIR / name
    if not path.exists():
        return False
    cfg = _read(path, {})
    keys = list(cfg.get("strategy_keys") or [])
    if key not in keys:
        keys.append(key)
        cfg["strategy_keys"] = keys
    cfg["enabled"] = True
    cfg["allow_auto_open"] = True
    cfg["formal_auto_trading_authorized"] = True
    cfg["gate_authorized_auto_trading"] = True
    _atomic(path, cfg)
    try:
        import auto_trade_forecast_closeout as closeout
        closeout.notify_pool_change(
            "strategy_mounted",
            detail={"symbol": symbol, "timeframe": timeframe, "strategy_key": key},
            auto_refresh=True,
        )
    except Exception:
        pass
    return True


def _remove_daemon_key(symbol, timeframe, key):
    name = _daemon_config_name(symbol, timeframe)
    if not name:
        return
    path = AUTO_DIR / name
    if not path.exists():
        return
    cfg = _read(path, {})
    keys = [k for k in (cfg.get("strategy_keys") or []) if k != key]
    cfg["strategy_keys"] = keys
    _atomic(path, cfg)
    try:
        import auto_trade_forecast_closeout as closeout
        closeout.notify_pool_change(
            "strategy_unmounted",
            detail={"symbol": symbol, "timeframe": timeframe, "strategy_key": key},
            auto_refresh=True,
        )
    except Exception:
        pass


# ─── Screening ────────────────────────────────────────────────────────

_FRAME_CACHE = {}
_FRAME_CACHE_MAX = 6  # low-RAM: never retain full 38-symbol frames


def trim_frame_cache(max_entries=None):
    """Evict oldest frame-cache entries to bound RAM."""
    max_entries = int(max_entries if max_entries is not None else _FRAME_CACHE_MAX)
    if max_entries < 1:
        _FRAME_CACHE.clear()
        return 0
    while len(_FRAME_CACHE) > max_entries:
        try:
            _FRAME_CACHE.pop(next(iter(_FRAME_CACHE)))
        except Exception:
            _FRAME_CACHE.clear()
            break
    return len(_FRAME_CACHE)


def _frame(symbol, timeframe):
    key = "%s|%s" % (symbol, timeframe)
    if key in _FRAME_CACHE:
        return _FRAME_CACHE[key]
    try:
        import backtest_engine_v2 as bt
        if not hasattr(bt, "normalize_instrument"):
            def _norm(value):
                text = str(value or "").strip().upper()
                if text.endswith("-SWAP"):
                    return text
                if text.endswith("USDT"):
                    return text + "-SWAP"
                return text
            bt.normalize_instrument = _norm
    except Exception:
        pass
    import auto_trade_strategy_ecosystem as eco
    frame = eco._load_research_frame(symbol, timeframe)
    # Truncate BEFORE caching — critical for 764MB hosts / 38-symbol matrix.
    try:
        if hasattr(frame, "iloc") and len(frame) > 12000:
            frame = frame.iloc[-12000:].copy()
    except Exception:
        pass
    trim_frame_cache(_FRAME_CACHE_MAX - 1)
    _FRAME_CACHE[key] = frame
    return frame


def _max_drawdown(pnls):
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for p in pnls:
        equity *= max(1e-12, 1.0 + float(p))
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    return max_dd


def _five_fold_pass(trades, min_positive=4):
    """5-segment blind test: require at least min_positive folds with mean>0.

    Designer rule 2026-07-24: 5过4 (was previously all-5-positive).
    """
    if not trades:
        return False, [], {}
    ordered = sorted(trades, key=lambda t: int(t.get("entry_index") or 0))
    if len(ordered) < N_FOLDS:
        return False, [], {"n": len(ordered)}
    folds = [[] for _ in range(N_FOLDS)]
    for i, t in enumerate(ordered):
        folds[i % N_FOLDS].append(float(t.get("pnl_ratio") or 0.0))
    means = []
    for fold in folds:
        if not fold:
            return False, means, {"empty_fold": True}
        means.append(sum(fold) / float(len(fold)))
    positive = sum(1 for m in means if m > 0)
    ok = positive >= int(min_positive)
    return ok, means, {
        "fold_sizes": [len(f) for f in folds],
        "positive_folds": positive,
        "required_positive": int(min_positive),
        "rule": "five_fold_pass_4_of_5",
    }


def _five_fold_all_positive(trades):
    """Backward-compatible alias → 5过4."""
    return _five_fold_pass(trades, min_positive=4)


def _death_hard_fail(dsl):
    try:
        text = json.dumps(dsl, ensure_ascii=False)
        # Prefer niche death heatmap / none_of ids when available
        try:
            import auto_trade_niche_map as niche
            report = niche.build_report(days=7) if hasattr(niche, "build_report") else {}
            codes = []
            for row in (report.get("death_heatmap_prior")
                        or report.get("top_death_codes") or []):
                if isinstance(row, dict):
                    codes.append(str(row.get("code") or row.get("death_cause_code") or ""))
                else:
                    codes.append(str(row))
            for pid in codes:
                if pid and pid in text:
                    return "death_heatmap:%s" % pid
        except Exception:
            pass
        # Failure vault never-revive keys
        try:
            vault = _read(FAILURE_VAULT, {"items": []})
            for item in (vault.get("items") or [])[-200:]:
                key = ((item.get("row") or {}).get("strategy_key")
                       or item.get("assignment_id") or "")
                if key and key in text and key == (dsl.get("key") or ""):
                    return "failure_vault_never_revive:%s" % key
        except Exception:
            pass
    except Exception:
        text = json.dumps(dsl, ensure_ascii=False).lower()
        if str(dsl.get("direction") or "").lower() == "short":
            if "cci" in text and "take_profit" in text and "-100" in text:
                return "tp_momentum_contradiction_cci"
    return None


def _no_lookahead(dsl):
    """Reject obvious future-leak operators/features if any sneak in."""
    text = json.dumps(dsl, ensure_ascii=False).lower()
    banned = ("future_", "lead(", "shift(-", "t+1", "next_bar", "lookahead")
    for b in banned:
        if b in text:
            return False, b
    return True, None


def safety_screen_candidate(cand):
    """Safety-only hard gates (designer 2026-07-24 role pivot).

    Pass criteria for machine: validate DSL, no lookahead, no death hard conflict.
    Backtest metrics are attached as evidence only — never used to reject.
    Statistical gates (n/WR/5fold/mean) retired; 3AI theoretical review is the gate.
    """
    import auto_trade_strategy_dsl as dsl_mod
    import auto_trade_strategy_ecosystem as eco
    dsl = cand.get("dsl") or cand
    try:
        definition = dsl_mod.validate_strategy(dsl)
    except Exception as exc:
        return False, {}, "validate_fail:%s" % exc

    ok_ll, leak = _no_lookahead(definition)
    if not ok_ll:
        return False, {}, "lookahead:%s" % leak

    death = _death_hard_fail(definition)
    if death:
        return False, {}, "death:%s" % death

    symbol = (definition.get("supported_instruments")
              or [cand.get("symbol") or "BTC-USDT-SWAP"])[0]
    timeframe = definition.get("timeframe") or cand.get("timeframe") or "15m"
    metrics = {
        "trades": 0,
        "mean_net": None,
        "win_rate": None,
        "max_drawdown": None,
        "fold_means": [],
        "fold_meta": {},
        "total_return_pct": None,
        "friction": "observed_base_real_1x_evidence_only",
        "triple_friction_in_screen": False,
        "statistical_gates_retired": True,
        "safety_only": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "key": definition.get("key"),
        "name": definition.get("name"),
        "direction": definition.get("direction"),
        "evidence_backtest": None,
    }
    try:
        frame = _frame(symbol, timeframe)
        if hasattr(frame, "iloc") and len(frame) > 12000:
            frame = frame.iloc[-12000:]
        fr = eco._friction_scenario(symbol, "observed_base")
        result = dsl_mod.backtest_dsl(
            frame, definition,
            leverage=LEVERAGE,
            stop_loss_pct=STOP_LOSS_PCT,
            fee_rate_per_side=float(fr.get("fee_rate_per_side") or 0.0005),
            slippage_rate_per_side=float(fr.get("slippage_rate_per_side") or 0.0002),
            half_spread_rate_per_side=float(fr.get("half_spread_rate_per_side") or 0),
            impact_rate_per_side=float(fr.get("impact_rate_per_side") or 0),
            latency_rate_per_side=float(fr.get("latency_rate_per_side") or 0),
            funding_rate_per_8h=float(fr.get("funding_rate_per_8h") or 0),
            friction_scenario="observed_base",
        )
        trades = result.get("trades") or []
        pnls = [float(t.get("pnl_ratio") or 0.0) for t in trades]
        n = len(pnls)
        mean = (sum(pnls) / float(n)) if n else None
        wins = sum(1 for p in pnls if p > 0)
        wr = (wins / float(n) * 100.0) if n else None
        max_dd = _max_drawdown(pnls) if pnls else None
        fold_ok, fold_means, fold_meta = _five_fold_pass(trades, min_positive=4)
        streak = mx_streak = 0
        for p in pnls:
            if p <= 0:
                streak += 1
                mx_streak = max(mx_streak, streak)
            else:
                streak = 0
        metrics.update({
            "trades": n,
            "mean_net": mean,
            "win_rate": wr,
            "max_drawdown": max_dd,
            "fold_means": fold_means,
            "fold_meta": fold_meta,
            "fold_ok_advisory": fold_ok,
            "total_return_pct": result.get("total_return_percent"),
            "max_loss_streak": mx_streak,
            "bars_used": int(len(frame)),
            "leverage": LEVERAGE,
            "stop_loss_pct": STOP_LOSS_PCT,
            "friction_rates": {
                "fee_rate_per_side": float(fr.get("fee_rate_per_side") or 0.0005),
                "slippage_rate_per_side": float(
                    fr.get("slippage_rate_per_side") or 0.0002),
                "half_spread_rate_per_side": float(
                    fr.get("half_spread_rate_per_side") or 0),
                "impact_rate_per_side": float(fr.get("impact_rate_per_side") or 0),
                "latency_rate_per_side": float(
                    fr.get("latency_rate_per_side") or 0),
                "funding_rate_per_8h": float(fr.get("funding_rate_per_8h") or 0),
                "scenario": "observed_base",
            },
            "evidence_backtest": {
                "trades": n, "mean_net": mean, "win_rate": wr,
                "max_drawdown": max_dd, "max_loss_streak": mx_streak,
                "symbol": symbol, "timeframe": timeframe,
                "advisory_only": True,
            },
        })
    except Exception as exc:
        metrics["evidence_backtest_error"] = str(exc)
    return True, metrics, "safety_pass"


def screen_candidate(cand):
    """Compatibility wrapper — statistical gates retired; safety only."""
    return safety_screen_candidate(cand)


def triple_friction_risk_tip(definition, symbol=None, timeframe=None):
    """Post-live pressure tip only — never used as eliminate/screen gate."""
    import auto_trade_strategy_dsl as dsl_mod
    import auto_trade_strategy_ecosystem as eco
    try:
        definition = dsl_mod.validate_strategy(definition)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    symbol = symbol or (definition.get("supported_instruments") or ["BTC-USDT-SWAP"])[0]
    timeframe = timeframe or definition.get("timeframe") or "15m"
    try:
        frame = _frame(symbol, timeframe)
        if hasattr(frame, "iloc") and len(frame) > 12000:
            frame = frame.iloc[-12000:]
        fr = eco._friction_scenario(symbol, TRIPLE_FRICTION_SCENARIO)
        result = dsl_mod.backtest_dsl(
            frame, definition,
            leverage=LEVERAGE,
            stop_loss_pct=STOP_LOSS_PCT,
            fee_rate_per_side=float(fr.get("fee_rate_per_side") or 0.0005),
            slippage_rate_per_side=float(fr.get("slippage_rate_per_side") or 0.0002),
            half_spread_rate_per_side=float(fr.get("half_spread_rate_per_side") or 0),
            impact_rate_per_side=float(fr.get("impact_rate_per_side") or 0),
            latency_rate_per_side=float(fr.get("latency_rate_per_side") or 0),
            funding_rate_per_8h=float(fr.get("funding_rate_per_8h") or 0),
            friction_scenario=TRIPLE_FRICTION_SCENARIO,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    trades = result.get("trades") or []
    pnls = [float(t.get("pnl_ratio") or 0.0) for t in trades]
    n = len(pnls)
    mean = (sum(pnls) / float(n)) if n else 0.0
    tip = {
        "ok": True,
        "scenario": TRIPLE_FRICTION_SCENARIO,
        "trades": n,
        "mean_net": mean,
        "negative_expectation": bool(mean <= 0),
        "not_elimination": True,
        "natural_language": (
            "三倍摩擦压力提示（非淘汰）：均值净收益 %.6f，样本 %d。"
            "仅作风险提示，不作为删除/降级依据。" % (mean, n)
        ),
    }
    return tip


# ─── Pending queue + push ─────────────────────────────────────────────

def load_pending():
    data = _read(PENDING_PATH, {"items": []})
    if not isinstance(data.get("items"), list):
        data["items"] = []
    return data


def save_pending(data):
    data["updated_at"] = _now()
    data["schema"] = "qiyu_pending_human_confirm_v1"
    _atomic(PENDING_PATH, data)


def enqueue_for_human(cand, metrics, source="unknown", ai_review=None):
    """After safety+3AI pass: queue + WxPusher notify. Never live.

    Phase-4 Wx card includes Calmar, payoff, cross-asset score, mean MAE when
    present. production_mounted stays False until CLI --confirm (B 30%/20x).
    """
    dsl = cand.get("dsl") or cand
    key = (dsl.get("key") or metrics.get("key")
           or cand.get("strategy_key") or "unknown")
    ai_review = ai_review or {}
    wr_map = (ai_review.get("ai_theoretical_wr_by_provider")
              or metrics.get("ai_theoretical_wr_by_provider") or {})
    wr_avg = ai_review.get("ai_theoretical_wr_avg")
    if wr_avg is None:
        wr_avg = metrics.get("ai_theoretical_wr_avg")
    mean_net_map = (ai_review.get("ai_theoretical_mean_net_by_provider")
                    or metrics.get("ai_theoretical_mean_net_by_provider") or {})
    mean_net_avg = ai_review.get("ai_theoretical_mean_net_avg")
    if mean_net_avg is None:
        mean_net_avg = metrics.get("ai_theoretical_mean_net_avg")
    risk_map = (ai_review.get("ai_stop_cluster_risk_by_provider")
                or metrics.get("ai_stop_cluster_risk_by_provider") or {})
    # Phase-4 incubator card fields
    calmar = ai_review.get("calmar")
    if calmar is None:
        calmar = metrics.get("calmar")
    payoff = ai_review.get("payoff")
    if payoff is None:
        payoff = metrics.get("payoff") or metrics.get("payoff_ratio")
    cross_score = ai_review.get("cross_asset_score")
    if cross_score is None:
        cross_score = metrics.get("cross_asset_score")
    mean_mae = ai_review.get("mean_mae")
    if mean_mae is None:
        mean_mae = metrics.get("mean_mae")
    pending = load_pending()
    for row in pending["items"]:
        if row.get("key") == key and row.get("status") == "awaiting_confirm":
            return {"ok": True, "duplicate": True, "key": key}
    item = {
        "key": key,
        "status": "awaiting_confirm",
        "created_at": _now(),
        "source": source,
        "symbol": metrics.get("symbol") or cand.get("symbol"),
        "timeframe": metrics.get("timeframe") or cand.get("timeframe"),
        "direction": metrics.get("direction") or dsl.get("direction"),
        "name": metrics.get("name") or dsl.get("name") or key,
        "dsl": dsl,
        "metrics": metrics,
        "logic_brief": _logic_brief(dsl),
        "ai_theoretical_wr_by_provider": wr_map,
        "ai_theoretical_wr_avg": wr_avg,
        "ai_theoretical_mean_net_by_provider": mean_net_map,
        "ai_theoretical_mean_net_avg": mean_net_avg,
        "ai_stop_cluster_risk_by_provider": risk_map,
        "ai_review_natural_language": ai_review.get("natural_language"),
        "calmar": calmar,
        "payoff": payoff,
        "cross_asset_score": cross_score,
        "mean_mae": mean_mae,
        "phase4_incubator": bool(ai_review.get("phase4_incubator")
                                 or cand.get("phase4_incubator")),
        "production_mounted": False,
    }
    pending["items"].append(item)
    save_pending(pending)
    avg_txt = ("%.1f%%" % float(wr_avg)) if wr_avg is not None else "-"
    mean_net_txt = (
        ("%.3f%%" % float(mean_net_avg)) if mean_net_avg is not None else "-"
    )
    calmar_txt = ("%.2f" % float(calmar)) if calmar is not None else "-"
    payoff_txt = ("%.2f" % float(payoff)) if payoff is not None else "-"
    cross_txt = ("%.2f" % float(cross_score)) if cross_score is not None else "-"
    mae_txt = ("%.4f" % float(mean_mae)) if mean_mae is not None else "-"
    msg = (
        "【四复核通过·待人工确认签发】\n"
        "名称: {name}\n"
        "标的/周期: {symbol} / {timeframe}\n"
        "方向: {direction}\n"
        "逻辑: {logic}\n"
        "Calmar: {calmar} · Payoff: {payoff}\n"
        "Cross-asset score: {cross} · Mean MAE: {mae}\n"
        "三AI理论胜率均值: {avg}\n"
        "三AI理论单笔盈利率均值: {mean_net}\n"
        "分项胜率: DS {ds} / Qwen {qw} / GLM {gpt}\n"
        "分项单笔盈利率: DS {ds_mn} / Qwen {qw_mn} / GLM {gpt_mn}\n"
        "回测证据(仅参考): 净均值 {mean} · 样本 {n} · 机器胜率 {wr}\n"
        "production_mounted=False（需人工 --confirm 才上 B级30%/20x/SL0.9%）\n"
        "确认指令: python3 auto_trade_human_confirm_pipeline.py --confirm {key}\n"
        "拒绝指令: python3 auto_trade_human_confirm_pipeline.py --reject {key}\n"
        "时间: {t}"
    ).format(
        name=item["name"], key=key,
        symbol=item["symbol"], timeframe=item["timeframe"],
        direction=item["direction"], logic=item["logic_brief"],
        calmar=calmar_txt, payoff=payoff_txt, cross=cross_txt, mae=mae_txt,
        avg=avg_txt, mean_net=mean_net_txt,
        ds=wr_map.get("deepseek"), qw=wr_map.get("qwen"),
        gpt=wr_map.get("glm", wr_map.get("chatgpt")),
        ds_mn=mean_net_map.get("deepseek"), qw_mn=mean_net_map.get("qwen"),
        gpt_mn=mean_net_map.get("glm", mean_net_map.get("chatgpt")),
        mean=metrics.get("mean_net"),
        n=int(metrics.get("trades") or 0),
        wr=metrics.get("win_rate"),
        t=_now(),
    )
    _wx(msg, kind="strategy_pending_confirm", meta={"key": key,
                                                    "ai_theoretical_wr_avg": wr_avg,
                                                    "ai_theoretical_mean_net_avg": mean_net_avg,
                                                    "calmar": calmar,
                                                    "payoff": payoff,
                                                    "cross_asset_score": cross_score,
                                                    "mean_mae": mean_mae,
                                                    "production_mounted": False})
    _append_audit({"time": _now(), "event": "pending_confirm", "key": key,
                   "source": source, "metrics": metrics,
                   "ai_theoretical_wr_avg": wr_avg,
                   "calmar": calmar, "payoff": payoff,
                   "cross_asset_score": cross_score, "mean_mae": mean_mae,
                   "production_mounted": False})
    return {"ok": True, "key": key, "pushed": True,
            "ai_theoretical_wr_avg": wr_avg,
            "production_mounted": False}


def _logic_brief(dsl):
    entry = (dsl.get("entry") or {})
    leaves = entry.get("all") or entry.get("any") or []
    bits = []
    for leaf in leaves[:6]:
        if not isinstance(leaf, dict):
            continue
        feat = ((leaf.get("left") or {}).get("feature") or "?")
        op = leaf.get("op") or "?"
        right = leaf.get("right") or {}
        if "value" in right:
            bits.append("%s %s %s" % (feat, op, right.get("value")))
        elif "feature" in right:
            bits.append("%s %s %s" % (feat, op, right.get("feature")))
        else:
            bits.append("%s %s" % (feat, op))
    return "; ".join(bits) if bits else (dsl.get("description") or "DSL策略")[:180]


def ingest_and_screen(cand, source="creator", ai_review=None, require_ai_review=True):
    """Safety screen; optional 3AI review already done by Codex submit path.

    Legacy callers without ai_review are rejected when require_ai_review=True
    (factory creation is disabled; only Codex+3AI path should enqueue).
    Never auto-mount; production_mounted stays False until CLI --confirm.
    """
    ok, metrics, reason = safety_screen_candidate(cand)
    if not ok:
        _append_audit({"time": _now(), "event": "screen_fail",
                       "reason": reason, "source": source,
                       "key": (cand.get("dsl") or cand).get("key")})
        return {"ok": False, "reason": reason, "metrics": metrics,
                "production_mounted": False}
    if require_ai_review and not (ai_review and ai_review.get("approved")):
        return {
            "ok": False,
            "reason": "ai_theoretical_review_required",
            "metrics": metrics,
            "production_mounted": False,
            "hint": "use auto_trade_codex_strategy_review.py --submit",
        }
    if ai_review:
        metrics = dict(metrics)
        metrics["ai_theoretical_wr_by_provider"] = ai_review.get(
            "ai_theoretical_wr_by_provider")
        metrics["ai_theoretical_wr_avg"] = ai_review.get("ai_theoretical_wr_avg")
        metrics["ai_theoretical_mean_net_by_provider"] = ai_review.get(
            "ai_theoretical_mean_net_by_provider")
        metrics["ai_theoretical_mean_net_avg"] = ai_review.get(
            "ai_theoretical_mean_net_avg")
        metrics["ai_stop_cluster_risk_by_provider"] = ai_review.get(
            "ai_stop_cluster_risk_by_provider")
        for k in ("calmar", "payoff", "cross_asset_score", "mean_mae"):
            if ai_review.get(k) is not None:
                metrics[k] = ai_review.get(k)
        if ai_review.get("payoff") is None and ai_review.get("payoff_ratio") is not None:
            metrics["payoff"] = ai_review.get("payoff_ratio")
    # Carry incubator payload fields onto metrics when present on cand
    incub = cand.get("phase4_incubator") or {}
    if isinstance(incub, dict):
        if metrics.get("cross_asset_score") is None:
            metrics["cross_asset_score"] = incub.get("cross_asset_score")
        if metrics.get("mean_mae") is None:
            metrics["mean_mae"] = incub.get("mean_mae")
        if metrics.get("calmar") is None:
            metrics["calmar"] = incub.get("calmar")
        if metrics.get("payoff") is None:
            metrics["payoff"] = incub.get("payoff_ratio")
    return enqueue_for_human(cand, metrics, source=source, ai_review=ai_review)


# ─── Human confirm → B live ───────────────────────────────────────────

def confirm(key, confirmed_by="codex_human"):
    """Human confirm: write B-grade live assignment (30%)."""
    pending = load_pending()
    item = None
    for row in pending["items"]:
        if row.get("key") == key and row.get("status") == "awaiting_confirm":
            item = row
            break
    if not item:
        return {"ok": False, "error": "not_awaiting_confirm", "key": key}

    dsl = item.get("dsl") or {}
    import auto_trade_strategy_dsl as dsl_mod
    definition = dsl_mod.validate_strategy(dsl)
    symbol = item.get("symbol") or (definition.get("supported_instruments") or [None])[0]
    timeframe = item.get("timeframe") or definition.get("timeframe")
    key = definition["key"]
    _upsert_dsl(definition, live=True)
    if not _ensure_daemon_key(symbol, timeframe, key):
        return {"ok": False, "error": "no_daemon_config", "symbol": symbol,
                "timeframe": timeframe}

    aid = "%s|%s|%s" % (symbol, timeframe, key)
    controls = _read(CONTROL_PATH, {"assignments": {}})
    assignments = controls.setdefault("assignments", {})
    row = dict(assignments.get(aid) or {})
    row.update({
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy_key": key,
        "audit_state": "conditional_frequency_probe",
        "lifecycle_grade": "B",
        "max_grade": "B",
        "max_position_ratio": GRADE_RATIO["B"],
        "pause_new_entries": False,
        "new_entries_allowed": True,
        "human_confirmed": True,
        "human_confirmed_at": _now(),
        "human_confirmed_by": confirmed_by,
        "human_confirm_pipeline": True,
        "grade_window": "B_first3",
        "grade_window_closed": [],
        "automatic_live_restoration": False,
        "stop_loss_pct": STOP_LOSS_PCT,
        "leverage": LEVERAGE,
        "mass_engine": False,
        "ai_theoretical_wr_by_provider": item.get("ai_theoretical_wr_by_provider"),
        "ai_theoretical_wr_avg": item.get("ai_theoretical_wr_avg"),
        "ai_theoretical_mean_net_by_provider": item.get(
            "ai_theoretical_mean_net_by_provider"),
        "ai_theoretical_mean_net_avg": item.get("ai_theoretical_mean_net_avg"),
        "ai_stop_cluster_risk_by_provider": item.get(
            "ai_stop_cluster_risk_by_provider"),
        "environment_boundary": row.get("environment_boundary") or {
            "schema": "qiyu_env_boundary_v1",
            "any_of": [],
            "all_of": [],
            "none_of": [],
            "volatility_in": ["low", "mid", "high"],
            "final_approved": True,
            "source": "human_confirm_b",
        },
    })
    if isinstance(row.get("environment_boundary"), dict):
        row["environment_boundary"]["final_approved"] = True
    assignments[aid] = row
    controls["updated_at"] = _now()
    controls["updated_by"] = "human_confirm_pipeline.confirm"
    _atomic(CONTROL_PATH, controls)
    try:
        import auto_trade_forecast_closeout as closeout
        closeout.notify_pool_change(
            "human_confirm_mount",
            detail={"assignment_id": aid, "strategy_key": key},
            auto_refresh=True,
        )
    except Exception:
        pass

    item["status"] = "confirmed_live_b"
    item["confirmed_at"] = _now()
    item["assignment_id"] = aid
    save_pending(pending)

    name = item.get("name") or key
    try:
        import auto_trade_strategy_titles as titles
        name = titles.resolve_strategy_name(key, name)
    except Exception:
        pass
    _wx(
        "【B级策略已上线】\n"
        "名称: %s\n"
        "标的/周期: %s / %s\n"
        "仓位: B级 30%%（杠杆20x，止损0.9%%）\n"
        "监控: 首3单若2单止损则降C；度过后4单3盈则升A；"
        "A级4单3盈且均盈利率>5%%则升S\n"
        "时间: %s" % (name, symbol, timeframe, _now()),
        kind="strategy_b_online",
        meta={"key": key, "assignment_id": aid, "grade": "B"},
    )
    _append_audit({"time": _now(), "event": "confirmed_b_online",
                   "key": key, "assignment_id": aid})
    try:
        import subprocess
        unit_map = {
            ("BTC-USDT-SWAP", "15m"): "qiyu-formal-auto-trade-btc-15m.service",
            ("BTC-USDT-SWAP", "5m"): "qiyu-formal-auto-trade-btc-5m.service",
            ("NG-USDT-SWAP", "5m"): "qiyu-formal-auto-trade-ng-5m.service",
            ("ADA-USDT-SWAP", "5m"): "qiyu-formal-auto-trade-ada-5m.service",
            ("XAG-USDT-SWAP", "5m"): "qiyu-formal-auto-trade-xag-5m.service",
            ("XAU-USDT-SWAP", "15m"): "qiyu-formal-auto-trade-xau-15m.service",
            ("CL-USDT-SWAP", "5m"): "qiyu-formal-auto-trade-cl-5m.service",
        }
        unit = unit_map.get((symbol, timeframe))
        if unit:
            subprocess.call(["systemctl", "try-restart", unit])
    except Exception:
        pass
    return {"ok": True, "key": key, "assignment_id": aid, "grade": "B",
            "max_position_ratio": GRADE_RATIO["B"]}


def reject(key, reason="human_reject"):
    pending = load_pending()
    found = False
    for row in pending["items"]:
        if row.get("key") == key and row.get("status") == "awaiting_confirm":
            row["status"] = "rejected"
            row["rejected_at"] = _now()
            row["reject_reason"] = reason
            found = True
    save_pending(pending)
    if found:
        try:
            import auto_trade_strategy_titles as titles
            shown = titles.resolve_strategy_name(key, key)
        except Exception:
            shown = key
        _wx("【策略已拒绝】\n名称: %s\n原因: %s\n时间: %s" % (shown, reason, _now()),
            kind="strategy_rejected", meta={"key": key})
    return {"ok": found, "key": key}


# ─── Live S/A/B/C monitor ─────────────────────────────────────────────

def _is_stop_trade(row):
    if bool(row.get("stop_loss") or row.get("stopped")):
        return True
    ctype = str(row.get("close_type") or row.get("exit_type")
                or row.get("close_reason") or "")
    low = ctype.lower()
    if "止损" in ctype or "stop" in low or "sl_" in low or "attached_sl" in low:
        return True
    if "交易所止损" in ctype:
        return True
    return False


def _nested(obj, *keys):
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _account_return_pct(row):
    """Total-equity return % for one closed trade (designer rule)."""
    try:
        existing = row.get("account_return_pct")
        if existing is not None:
            return float(existing)
    except Exception:
        pass
    equity = None
    for path in (
        ("sizing", "account_equity_usdt"),
        ("sizing", "account_equity"),
        ("entry_data", "account_equity_usdt"),
    ):
        val = _nested(row, *path)
        try:
            if val is not None:
                equity = float(val)
                if equity:
                    break
        except Exception:
            continue
    pnl = row.get("pnl")
    if pnl is None:
        pnl = row.get("net_pnl")
    try:
        pnl = float(pnl)
    except Exception:
        return None
    if equity in (None, 0):
        return None
    return (pnl / equity) * 100.0


def _closed_trades_for(strategy_key, limit=40, after=None):
    """Prefer formal daemon history; include stop/profit/equity-return."""
    out = []
    for path in list(AUTO_DIR.glob("formal_v6_state*.json")):
        state = _read(path, {})
        for row in list(state.get("history") or [])[::-1]:
            if not isinstance(row, dict):
                continue
            if str(row.get("strategy_key") or "") != str(strategy_key):
                continue
            if not row.get("closed_at"):
                continue
            if after and str(row.get("closed_at")) < str(after):
                continue
            try:
                pnl = float(row.get("pnl"))
            except Exception:
                pnl = 0.0
            acct = _account_return_pct(row)
            profit = (acct > 0) if acct is not None else (pnl > 0)
            out.append({
                "pnl": pnl,
                "account_return_pct": acct,
                "profit": profit,
                "stop": _is_stop_trade(row),
                "closed_at": row.get("closed_at"),
                "opened_at": row.get("opened_at"),
                "close_type": (row.get("close_type") or row.get("close_reason")
                               or ""),
            })
            if len(out) >= limit:
                return out
    return out


def _trade_token(t):
    return "%s|%s" % (t.get("closed_at"), t.get("pnl"))


def _next_window(trades, consumed, size):
    seen = set(consumed or [])
    window = []
    for t in trades:
        token = _trade_token(t)
        if token in seen:
            continue
        window.append(t)
        if len(window) >= size:
            break
    if len(window) < size:
        return None
    return window


def _save_assignment(aid, row):
    controls = _read(CONTROL_PATH, {"assignments": {}})
    controls.setdefault("assignments", {})[aid] = row
    controls["updated_at"] = _now()
    _atomic(CONTROL_PATH, controls)
    return row


def _set_grade(row, grade, window_name):
    grade = str(grade).upper()
    row = dict(row)
    row["lifecycle_grade"] = grade
    row["max_grade"] = grade
    row["max_position_ratio"] = GRADE_RATIO.get(grade, 0.15)
    row["grade_window"] = window_name
    row["grade_changed_at"] = _now()
    row["promote_window_closed"] = []
    row["stop_window_closed"] = []
    # Keep historical grade_window_closed for audit but reset active windows
    row["grade_window_closed"] = []
    return row


def _archive_failure(aid, row, reason, trades):
    vault = _read(FAILURE_VAULT, {"items": []})
    items = list(vault.get("items") or [])
    items.append({
        "archived_at": _now(),
        "assignment_id": aid,
        "reason": reason,
        "row": row,
        "trades": trades,
        "never_auto_revive": True,
    })
    vault = {
        "schema": "qiyu_strategy_failure_vault_v1",
        "updated_at": _now(),
        "items": items[-5000:],
    }
    _atomic(FAILURE_VAULT, vault)


def _delete_strategy(aid, row, reason, trades):
    symbol = row.get("symbol") or aid.split("|")[0]
    timeframe = row.get("timeframe") or aid.split("|")[1]
    key = row.get("strategy_key") or aid.split("|", 2)[-1]
    name = row.get("strategy_name") or key
    row = dict(row)
    row["pause_new_entries"] = True
    row["new_entries_allowed"] = False
    row["audit_state"] = "eliminated_pending_archive"
    row["lifecycle_grade"] = "deleted"
    row["max_position_ratio"] = 0.0
    row["deleted_at"] = _now()
    row["delete_reason"] = reason
    _set_dsl_live(key, False)
    _remove_daemon_key(symbol, timeframe, key)
    _archive_failure(aid, row, reason, trades)
    _save_assignment(aid, row)
    try:
        import auto_trade_strategy_titles as titles
        name = titles.resolve_strategy_name(key, name)
    except Exception:
        pass
    _wx(
        "【%s 已删除】\n"
        "原因: %s\n"
        "标的/周期: %s / %s\n"
        "已写入失败案例库，永不自动复活。\n"
        "时间: %s" % (name, reason, symbol, timeframe, _now()),
        kind="strategy_deleted",
        meta={"key": key, "reason": reason},
    )
    _append_audit({"time": _now(), "event": "strategy_deleted",
                   "assignment_id": aid, "reason": reason})
    return row


def _promote(aid, row, to_grade, reason):
    key = row.get("strategy_key") or aid.split("|", 2)[-1]
    name = row.get("strategy_name") or key
    from_g = str(row.get("lifecycle_grade") or "?")
    window = {"A": "A_run", "S": "S_run", "B": "B_promote"}.get(to_grade, "A_run")
    row = _set_grade(row, to_grade, window)
    row["promoted_at"] = _now()
    row["promote_reason"] = reason
    _save_assignment(aid, row)
    ratio = int(GRADE_RATIO[to_grade] * 100)
    try:
        import auto_trade_strategy_titles as titles
        name = titles.resolve_strategy_name(key, name)
    except Exception:
        pass
    _wx(
        "【%s 晋升至%s级，%s】\n"
        "仓位: %s级 %s%%（杠杆20x）\n"
        "时间: %s" % (
            name, to_grade, reason, to_grade, ratio, _now()),
        kind="strategy_promote_%s" % to_grade.lower(),
        meta={"key": key, "grade": to_grade, "from": from_g, "reason": reason},
    )
    _append_audit({"time": _now(), "event": "promote",
                   "assignment_id": aid, "from": from_g, "to": to_grade,
                   "reason": reason})
    return row


def _demote(aid, row, to_grade, reason, window_trades=None):
    key = row.get("strategy_key") or aid.split("|", 2)[-1]
    name = row.get("strategy_name") or key
    from_g = str(row.get("lifecycle_grade") or "?")
    if to_grade == "C":
        window = "C_next3"
    elif to_grade == "B":
        # Demotion into B skips first-3 probation; enter promote lane.
        window = "B_promote"
    elif to_grade == "A":
        window = "A_run"
    else:
        window = "B_promote"
    row = _set_grade(row, to_grade, window)
    row["downgraded_at"] = _now()
    row["downgrade_reason"] = reason
    if to_grade == "C":
        row["downgraded_to_c_at"] = _now()
    if window_trades is not None:
        row["last_demote_window"] = window_trades
    _save_assignment(aid, row)
    ratio = int(GRADE_RATIO.get(to_grade, 0) * 100)
    try:
        import auto_trade_strategy_titles as titles
        name = titles.resolve_strategy_name(key, name)
    except Exception:
        pass
    _wx(
        "【%s 降级至%s级】\n"
        "原因: %s\n"
        "仓位: %s级 %s%%\n"
        "时间: %s" % (name, to_grade, reason, to_grade, ratio, _now()),
        kind="strategy_downgrade_%s" % to_grade.lower(),
        meta={"key": key, "grade": to_grade, "from": from_g},
    )
    _append_audit({"time": _now(), "event": "downgrade",
                   "assignment_id": aid, "from": from_g, "to": to_grade,
                   "reason": reason})
    # Dynamic optimizer event hook (B→C / S→A / A→B) — suggest only
    try:
        import auto_trade_strategy_dynamic_optimizer as optimizer
        event = "demote_%s_to_%s" % (from_g, to_grade)
        optimizer.on_lifecycle_event(aid, event, row)
    except Exception as exc:
        _append_audit({"time": _now(), "event": "optimizer_hook_fail",
                       "assignment_id": aid, "error": str(exc)})
    return row


def monitor_live_grades():
    """S/A/B/C promote + demote rules (designer 2026-07-24)."""
    lock_path = AUTO_DIR / "human_confirm_monitor.lock"
    lock_fd = None
    try:
        import fcntl
        lock_fd = open(str(lock_path), "a+")
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        if lock_fd is not None:
            try:
                lock_fd.close()
            except Exception:
                pass
            return {"ok": True, "skipped": "lock_held", "time": _now(),
                    "actions": []}

    try:
        return _monitor_live_grades_unlocked()
    finally:
        if lock_fd is not None:
            try:
                import fcntl
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass


def _monitor_live_grades_unlocked():
    controls = _read(CONTROL_PATH, {"assignments": {}})
    actions = []
    for aid, row in list((controls.get("assignments") or {}).items()):
        if not row.get("human_confirm_pipeline") and not row.get("human_confirmed"):
            continue
        if row.get("pause_new_entries") and str(row.get("audit_state") or "") in (
                "eliminated_pending_archive", "failed_closed"):
            continue
        grade = str(row.get("lifecycle_grade") or "").upper()
        if grade not in ("S", "A", "B", "C"):
            continue
        key = row.get("strategy_key") or aid.split("|", 2)[-1]
        after = (row.get("grade_changed_at")
                 or row.get("downgraded_to_c_at")
                 or row.get("promoted_at")
                 or row.get("human_confirmed_at"))
        trades = _closed_trades_for(key, limit=40, after=after)
        window_name = str(row.get("grade_window") or "")
        if not window_name:
            window_name = {
                "S": "S_run", "A": "A_run", "B": "B_first3", "C": "C_next3",
            }.get(grade, "B_first3")
            row = dict(row)
            row["grade_window"] = window_name

        # Legacy alias after first-3 survival
        if grade == "B" and window_name in ("B_survived", "manual"):
            window_name = "B_promote"
            row = dict(row)
            row["grade_window"] = "B_promote"

        acted = False

        # ── demotion / elimination (stop windows of 3) ──
        if grade == "B" and window_name == "B_first3":
            window = _next_window(trades, row.get("stop_window_closed")
                                  or row.get("grade_window_closed"), 3)
            if window:
                stops = sum(1 for t in window if t.get("stop"))
                tokens = [_trade_token(t) for t in window]
                if stops >= 2:
                    row = _demote(aid, row, "C", "首3单2止损", window)
                    actions.append({"action": "downgrade_c", "aid": aid,
                                    "stops": stops})
                    acted = True
                else:
                    row = dict(row)
                    consumed = (
                        list(row.get("stop_window_closed") or []) + tokens)[-40:]
                    row["stop_window_closed"] = consumed
                    row["grade_window_closed"] = consumed
                    # First-3 probation trades do not count toward promote-4.
                    row["promote_window_closed"] = (
                        list(row.get("promote_window_closed") or [])
                        + tokens)[-40:]
                    row["grade_window"] = "B_promote"
                    row["b_first3_survived_at"] = _now()
                    _save_assignment(aid, row)
                    actions.append({"action": "b_first3_ok", "aid": aid,
                                    "stops": stops})
                    window_name = "B_promote"
                    # fall through to promote eval with refreshed row

        elif grade == "C":
            window = _next_window(trades, row.get("stop_window_closed")
                                  or row.get("grade_window_closed"), 3)
            if window:
                stops = sum(1 for t in window if t.get("stop"))
                if stops >= 2:
                    row = _delete_strategy(aid, row, "C级3单2止损", window)
                    actions.append({"action": "delete", "aid": aid,
                                    "stops": stops})
                    acted = True
                else:
                    tokens = [_trade_token(t) for t in window]
                    row = dict(row)
                    row["stop_window_closed"] = (
                        list(row.get("stop_window_closed") or []) + tokens)[-40:]
                    row["grade_window_closed"] = row["stop_window_closed"]
                    _save_assignment(aid, row)
                    actions.append({"action": "c_window_ok", "aid": aid,
                                    "stops": stops})

        elif grade in ("S", "A"):
            window = _next_window(trades, row.get("stop_window_closed"), 3)
            if window:
                stops = sum(1 for t in window if t.get("stop"))
                tokens = [_trade_token(t) for t in window]
                if stops >= 2:
                    to_g = "A" if grade == "S" else "B"
                    reason = "3单2止损"
                    row = _demote(aid, row, to_g, reason, window)
                    actions.append({"action": "downgrade_%s" % to_g.lower(),
                                    "aid": aid, "stops": stops})
                    acted = True
                else:
                    row = dict(row)
                    row["stop_window_closed"] = (
                        list(row.get("stop_window_closed") or []) + tokens)[-40:]
                    _save_assignment(aid, row)
                    actions.append({"action": "stop_window_ok", "aid": aid,
                                    "grade": grade, "stops": stops})

        if acted:
            controls = _read(CONTROL_PATH, {"assignments": {}})
            continue

        # ── promotion (windows of 4) ──
        grade = str(row.get("lifecycle_grade") or "").upper()
        window_name = str(row.get("grade_window") or "")
        if grade == "B" and window_name == "B_promote":
            window = _next_window(trades, row.get("promote_window_closed"), 4)
            if window:
                wins = sum(1 for t in window if t.get("profit"))
                tokens = [_trade_token(t) for t in window]
                if wins >= 3:
                    row = _promote(aid, row, "A", "4单3盈")
                    actions.append({"action": "promote_a", "aid": aid,
                                    "wins": wins})
                else:
                    row = dict(row)
                    row["promote_window_closed"] = (
                        list(row.get("promote_window_closed") or [])
                        + tokens)[-40:]
                    _save_assignment(aid, row)
                    actions.append({"action": "promote_window_fail",
                                    "aid": aid, "wins": wins})

        elif grade == "A" and window_name in ("A_run", "A_promote", ""):
            window = _next_window(trades, row.get("promote_window_closed"), 4)
            if window:
                wins = sum(1 for t in window if t.get("profit"))
                rets = [t.get("account_return_pct") for t in window
                        if t.get("account_return_pct") is not None]
                mean_ret = (sum(rets) / float(len(rets))) if rets else None
                tokens = [_trade_token(t) for t in window]
                if wins >= 3 and mean_ret is not None and mean_ret > 5.0:
                    row = _promote(
                        aid, row, "S",
                        "4单3盈，均盈利率%.2f%%" % mean_ret)
                    actions.append({"action": "promote_s", "aid": aid,
                                    "wins": wins, "mean_ret": mean_ret})
                else:
                    row = dict(row)
                    row["promote_window_closed"] = (
                        list(row.get("promote_window_closed") or [])
                        + tokens)[-40:]
                    _save_assignment(aid, row)
                    actions.append({
                        "action": "promote_window_fail",
                        "aid": aid, "wins": wins, "mean_ret": mean_ret,
                    })

    # Triple-friction tips are computed silently for audit only.
    # Wx permanently hard-disabled (designer 2026-07-25); arg ignored.
    tip_actions = _maybe_emit_triple_friction_tips(controls, limit=2)
    if tip_actions:
        actions.extend(tip_actions)

    state = {"ok": True, "time": _now(), "actions": actions}
    _atomic(STATE_PATH, state)
    return state


def _maybe_emit_triple_friction_tips(controls, limit=2, send_wx=None):
    """Periodic pressure tip for live S/A/B; audit-only forever.

    Wx is HARD-DISABLED. The send_wx argument is ignored and cannot
    re-enable 【三倍摩擦风险提示·非淘汰】 (designer 2026-07-25).
    """
    from datetime import timedelta
    # Kill-switch: callers cannot override (even send_wx=True).
    send_wx = False
    if TRIPLE_FRICTION_TIP_WX_ENABLED:
        send_wx = False  # still forced off; constant is the only gate
    actions = []
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for aid, row in list((controls.get("assignments") or {}).items()):
        if n >= limit:
            break
        if not (row.get("human_confirm_pipeline") or row.get("human_confirmed")):
            continue
        grade = str(row.get("lifecycle_grade") or "").upper()
        if grade not in ("S", "A", "B"):
            continue
        last = str(row.get("triple_friction_tip_at") or "")
        if last and last >= cutoff:
            continue
        key = row.get("strategy_key") or aid.split("|", 2)[-1]
        # load dsl
        dsl_doc = _read(DSL_CONFIG_PATH, {"strategies": []})
        definition = None
        for s in dsl_doc.get("strategies") or []:
            if s.get("key") == key:
                definition = s
                break
        if not definition:
            continue
        parts = str(aid).split("|", 2)
        symbol = parts[0] if len(parts) == 3 else row.get("symbol")
        timeframe = parts[1] if len(parts) == 3 else row.get("timeframe")
        tip = triple_friction_risk_tip(definition, symbol, timeframe)
        row = dict(row)
        row["triple_friction_tip_at"] = _now()
        row["triple_friction_tip"] = tip
        _save_assignment(aid, row)
        if tip.get("negative_expectation"):
            actions.append({
                "action": "triple_friction_tip_recorded",
                "aid": aid,
                "mean_net": tip.get("mean_net"),
                "wx_sent": False,
                "wx_hard_disabled": True,
            })
            # Intentionally no _wx() call site. Do not restore.
        n += 1
    return actions


def notify_open_if_pipeline(strategy_key, payload=None):
    """Call from formal notify path for confirmed pipeline strategies."""
    controls = _read(CONTROL_PATH, {"assignments": {}})
    for aid, row in (controls.get("assignments") or {}).items():
        if (row.get("strategy_key") == strategy_key
                or aid.endswith("|" + str(strategy_key))):
            if row.get("human_confirm_pipeline") or row.get("human_confirmed"):
                grade = row.get("lifecycle_grade") or "?"
                try:
                    import auto_trade_strategy_titles as titles
                    shown = titles.resolve_strategy_name(
                        strategy_key, row.get("strategy_name"))
                except Exception:
                    shown = strategy_key
                _wx(
                    "【开仓通报·%s级】\n"
                    "策略: %s\n"
                    "仓位上限: %.0f%%\n"
                    "时间: %s" % (
                        grade, shown,
                        float(row.get("max_position_ratio") or 0) * 100,
                        _now()),
                    kind="strategy_opened_graded",
                    meta={"key": strategy_key, "grade": grade},
                )
                return True
    return False


# ─── Corridor / creator ingest ────────────────────────────────────────

def ingest_corridor_item(item, source="corridor"):
    """Corridor exit: screen → pending human (never auto E/D live)."""
    draft = item.get("draft") or {}
    cand = {
        "dsl": draft.get("dsl") or draft,
        "symbol": draft.get("symbol") or item.get("symbol"),
        "timeframe": draft.get("timeframe") or item.get("timeframe"),
    }
    return ingest_and_screen(cand, source=source)


def run_screen_tick(limit=20):
    """Monitor live grades + lightweight system health (3AI ops role)."""
    if not mass_is_frozen():
        freeze_mass_and_ed_probes()
    mon = monitor_live_grades()
    health = {}
    try:
        import auto_trade_system_health_ai as health_mod
        health = health_mod.run_health_tick(force_ai=False)
    except Exception as exc:
        health = {"ok": False, "error": str(exc)}
    out = {
        "ok": True,
        "time": _now(),
        "screened": [],
        "monitor": mon,
        "health": {
            "ok": health.get("ok"),
            "alerted": health.get("alerted"),
            "issues": health.get("issues"),
            "ai_ran": health.get("ai_ran"),
        },
        "note": "ai_creation_disabled_codex_submit_only",
        "pending_n": len((load_pending().get("items") or [])),
        "limit": int(limit),
    }
    _atomic(STATE_PATH, out)
    return out


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze-mass", action="store_true")
    parser.add_argument("--confirm", type=str, default="")
    parser.add_argument("--reject", type=str, default="")
    parser.add_argument("--monitor", action="store_true")
    parser.add_argument("--tick", action="store_true")
    parser.add_argument("--list-pending", action="store_true")
    args = parser.parse_args()
    if args.freeze_mass:
        print(json.dumps(freeze_mass_and_ed_probes(), ensure_ascii=False, indent=2))
    elif args.confirm:
        print(json.dumps(confirm(args.confirm), ensure_ascii=False, indent=2))
    elif args.reject:
        print(json.dumps(reject(args.reject), ensure_ascii=False, indent=2))
    elif args.monitor:
        print(json.dumps(monitor_live_grades(), ensure_ascii=False, indent=2))
    elif args.list_pending:
        print(json.dumps(load_pending(), ensure_ascii=False, indent=2))
    elif args.tick:
        print(json.dumps(run_screen_tick(), ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(run_screen_tick(), ensure_ascii=False, indent=2, default=str))
