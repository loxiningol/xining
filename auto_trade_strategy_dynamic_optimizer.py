# -*- coding: utf-8 -*-
"""策略动态优化器 — explore improvement directions for live S/A/B strategies.

Designer 2026-07-24:
  · Suggest only; never auto-apply without Codex confirm/reject
  · Does not change S/A/B/C grade rules or reset trade counters on apply
  · Runs in background; does not block opens
  · AI spend (optional health notes) goes through creation-factory budget
"""
from __future__ import print_function

from datetime import datetime, timedelta
from pathlib import Path
import copy
import hashlib
import json
import os
import tempfile
import uuid

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
DSL_CONFIG_PATH = ROOT / "strategy_configs" / "ai_dsl_strategies.json"
EXP_CONFIG_PATH = ROOT / "strategy_configs" / "experimental_strategies.json"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"

PENDING_PATH = AUTO_DIR / "strategy_optimizer_pending.json"
REJECTED_PATH = AUTO_DIR / "strategy_optimizer_rejected.json"
STATE_PATH = AUTO_DIR / "strategy_optimizer_state.json"
AUDIT_PATH = AUTO_DIR / "strategy_optimizer_audit.jsonl"
LESSONS_PATH = AUTO_DIR / "strategy_optimizer_trade_lessons.json"
HEALTH_PATH = AUTO_DIR / "strategy_optimizer_health.json"

LEVERAGE = 20
STOP_LOSS_PCT = 0.009
MIN_GRADE = ("S", "A", "B")
MIN_SAMPLE = 10
MIN_WR_LIFT_PP = 5.0          # absolute percentage points
MIN_NET_LIFT_REL = 0.10       # relative +10%
PARAM_RANGE = 0.30            # ±30%
PARAM_STEP = 0.05             # 5%
MAX_VARIANTS_PER_STRATEGY = 48
MAX_PROPOSALS_PER_STRATEGY = 2
MAX_PROPOSALS_PER_RUN = 8
REJECT_COOLDOWN_DAYS = 30

GRADE_RANK = {"S": 4, "A": 3, "B": 2, "C": 1}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today():
    return datetime.now().strftime("%Y-%m-%d")


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
    row = dict(row)
    row.setdefault("time", _now())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _wx(text, kind="strategy_optimizer_propose", meta=None):
    try:
        import auto_trade_formal_notify as notify
        return notify.send_message(text, kind=kind, meta=meta or {})
    except Exception as exc:
        _append_audit({"event": "wx_fail", "error": str(exc),
                       "text_head": str(text)[:200]})
        return {"ok": False, "error": str(exc)}


def _fingerprint(change):
    raw = json.dumps(change or {}, ensure_ascii=False, sort_keys=True,
                     default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# ─── targets / definitions ────────────────────────────────────────────

def list_live_targets(min_grades=MIN_GRADE):
    """Assignments with lifecycle grade in S/A/B (designer: B级以上)."""
    controls = _read(CONTROL_PATH, {"assignments": {}})
    out = []
    for aid, row in (controls.get("assignments") or {}).items():
        if row.get("pause_new_entries"):
            continue
        grade = str(row.get("lifecycle_grade") or "").upper()
        if grade not in min_grades:
            continue
        parts = str(aid).split("|", 2)
        if len(parts) != 3:
            continue
        symbol, timeframe, key = parts
        out.append({
            "assignment_id": aid,
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy_key": row.get("strategy_key") or key,
            "strategy_name": row.get("strategy_name") or key,
            "lifecycle_grade": grade,
            "row": row,
        })
    out.sort(key=lambda x: (-GRADE_RANK.get(x["lifecycle_grade"], 0),
                            x["assignment_id"]))
    return out


def load_definition(strategy_key):
    """Load DSL first, then experimental params strategy."""
    dsl_doc = _read(DSL_CONFIG_PATH, {"strategies": []})
    for row in dsl_doc.get("strategies") or []:
        if row.get("key") == strategy_key:
            return {"kind": "dsl", "definition": copy.deepcopy(row)}
    exp_doc = _read(EXP_CONFIG_PATH, {"strategies": []})
    for row in exp_doc.get("strategies") or []:
        if row.get("key") == strategy_key:
            return {"kind": "experimental", "definition": copy.deepcopy(row)}
    return None


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


def _metrics_from_result(result):
    trades = list(result.get("trades") or [])
    pnls = [float(t.get("pnl_ratio") or 0.0) for t in trades]
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    wr = (wins / float(n) * 100.0) if n else 0.0
    mean_net = (sum(pnls) / float(n)) if n else 0.0
    total_ret = result.get("total_return_percent")
    if total_ret is None and pnls:
        eq = 1.0
        for p in pnls:
            eq *= max(1e-12, 1.0 + p)
        total_ret = (eq - 1.0) * 100.0
    return {
        "trades": n,
        "win_rate": round(wr, 4),
        "mean_net": round(mean_net, 8),
        "total_return_pct": round(float(total_ret or 0.0), 6),
        "max_drawdown": round(_max_drawdown(pnls), 6),
    }


def evaluate_definition(kind, definition, symbol, timeframe):
    """Backtest a candidate definition; returns metrics dict or error."""
    try:
        if kind == "dsl":
            import auto_trade_human_confirm_pipeline as pipeline
            import auto_trade_strategy_dsl as dsl_mod
            import auto_trade_strategy_ecosystem as eco
            definition = dsl_mod.validate_strategy(definition)
            frame = pipeline._frame(symbol, timeframe)
            if hasattr(frame, "iloc") and len(frame) > 12000:
                frame = frame.iloc[-12000:]
            fr = eco._friction_scenario(symbol, "observed_base")
            result = dsl_mod.backtest_dsl(
                frame, definition,
                leverage=LEVERAGE,
                stop_loss_pct=float(definition.get("stop_loss_pct")
                                    or STOP_LOSS_PCT),
                fee_rate_per_side=float(fr.get("fee_rate_per_side") or 0.0005),
                slippage_rate_per_side=float(
                    fr.get("slippage_rate_per_side") or 0.0002),
            )
            return {"ok": True, "metrics": _metrics_from_result(result),
                    "result": result}
        if kind == "experimental":
            import auto_trade_strategy_ecosystem as eco
            params = dict(definition.get("params") or {})
            stop = float(definition.get("stop_loss_pct")
                         or (definition.get("params") or {}).get("stop_loss_pct")
                         or STOP_LOSS_PCT)
            result = eco._run_with_config(
                definition.get("key"), symbol, timeframe, params, stop,
                friction_scenario="observed_base")
            # normalize engine trades if needed
            if isinstance(result, dict) and "trades" not in result:
                # some engines nest
                trades = result.get("trade_list") or result.get("closed_trades") or []
                result = dict(result, trades=trades)
            return {"ok": True, "metrics": _metrics_from_result(result or {}),
                    "result": result}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "unknown_kind"}


# ─── variant generation ───────────────────────────────────────────────

def _param_multipliers():
    vals = []
    x = 1.0 - PARAM_RANGE
    while x <= 1.0 + PARAM_RANGE + 1e-9:
        if abs(x - 1.0) > 1e-9:
            vals.append(round(x, 4))
        x = round(x + PARAM_STEP, 4)
    return vals


def generate_param_scan_variants(kind, definition, limit=MAX_VARIANTS_PER_STRATEGY):
    """Scan numeric params / DSL leaf thresholds ±30% @ 5% steps."""
    variants = []
    multis = _param_multipliers()
    if kind == "experimental":
        params = dict(definition.get("params") or {})
        for name, raw in list(params.items()):
            if len(variants) >= limit:
                break
            try:
                base = float(raw)
            except Exception:
                continue
            if abs(base) < 1e-12:
                continue
            # skip capital/risk locks
            if name in ("stop_loss_pct", "leverage", "signal_score"):
                continue
            for m in multis:
                if len(variants) >= limit:
                    break
                changed = copy.deepcopy(definition)
                new_val = round(base * m, 10)
                changed.setdefault("params", {})[name] = new_val
                variants.append({
                    "kind": kind,
                    "definition": changed,
                    "opt_type": "参数调整",
                    "change": {
                        "field": "params.%s" % name,
                        "from": base,
                        "to": new_val,
                        "multiplier": m,
                    },
                    "explanation": "将参数 %s 从 %s 调整为 %s（×%.2f）"
                                   % (name, base, new_val, m),
                })
        return variants

    # DSL: scan numeric leaf values
    try:
        import auto_trade_strategy_dsl as dsl_mod
        base = dsl_mod.validate_strategy(definition)
    except Exception:
        return []
    leaves = []
    try:
        leaves.extend([("entry", p, leaf)
                       for p, leaf in dsl_mod._leaf_paths(base["entry"])])
        leaves.extend([("exit", p, leaf)
                       for p, leaf in dsl_mod._leaf_paths(base["exit"])])
    except Exception:
        return []
    for section, path, leaf in leaves:
        if len(variants) >= limit:
            break
        operand = leaf.get("right")
        if not isinstance(operand, dict) or "value" not in operand:
            continue
        try:
            base_val = float(operand["value"])
        except Exception:
            continue
        if abs(base_val) < 1e-12:
            continue
        feature = str((leaf.get("left") or {}).get("feature") or leaf.get("id") or "")
        for m in multis:
            if len(variants) >= limit:
                break
            changed = copy.deepcopy(base)
            target = dsl_mod._at(changed[section], path)
            new_val = round(base_val * m, 10)
            target["right"]["value"] = new_val
            # keep live key identical — optimizer applies in place
            changed["key"] = base["key"]
            variants.append({
                "kind": kind,
                "definition": changed,
                "opt_type": "参数调整",
                "change": {
                    "field": "%s.%s.value" % (section, feature or leaf.get("id")),
                    "from": base_val,
                    "to": new_val,
                    "multiplier": m,
                    "condition_id": leaf.get("id"),
                },
                "explanation": "将%s条件 %s 阈值从 %s 调整为 %s"
                               % (section, feature, base_val, new_val),
            })
    return variants


def generate_logic_variants(kind, definition, limit=24):
    """Reuse DSL mutate_strategy operators; keep original key on apply."""
    if kind != "dsl":
        return []
    try:
        import auto_trade_strategy_dsl as dsl_mod
        mutants = dsl_mod.mutate_strategy(definition, limit=limit)
    except Exception:
        return []
    out = []
    base_key = definition.get("key")
    for m in mutants:
        origin = m.get("origin") or {}
        mut = origin.get("mutation") or "logic"
        # restore live key for in-place apply
        applied = copy.deepcopy(m)
        applied["key"] = base_key
        out.append({
            "kind": kind,
            "definition": applied,
            "opt_type": "逻辑修改",
            "change": dict(origin),
            "explanation": "逻辑微调算子 %s（%s）"
                           % (mut, json.dumps(origin, ensure_ascii=False)),
        })
    return out


def generate_death_avoidance_variants(kind, definition, recent_stops, limit=8):
    """If recent stops hit known death codes, try remove/add filter variants."""
    if kind != "dsl" or not recent_stops:
        return []
    codes = []
    try:
        import auto_trade_niche_map as niche
        report = niche.build_report(days=7) if hasattr(niche, "build_report") else {}
        for row in (report.get("death_heatmap_prior")
                    or report.get("top_death_codes") or []):
            if isinstance(row, dict):
                codes.append(str(row.get("code") or row.get("death_cause_code") or ""))
            else:
                codes.append(str(row))
    except Exception:
        pass
    try:
        vault = _read(AUTO_DIR / "strategy_failure_vault.json", {"items": []})
        for item in (vault.get("items") or [])[-50:]:
            code = ((item.get("row") or {}).get("death_cause_code")
                    or item.get("death_cause_code") or "")
            if code:
                codes.append(str(code))
    except Exception:
        pass
    codes = [c for c in codes if c][:20]
    if not codes:
        return []
    # Prefer logic remove_condition variants tagged as death avoidance
    logic = generate_logic_variants(kind, definition, limit=limit * 2)
    out = []
    for item in logic:
        mut = (item.get("change") or {}).get("mutation")
        if mut not in ("remove_condition", "add_condition", "exit_threshold",
                       "remove_exit_condition"):
            continue
        item = dict(item)
        item["opt_type"] = "死因规避"
        item["explanation"] = (
            "近期止损命中死因库线索，尝试 %s 以规避：%s"
            % ((item.get("change") or {}).get("mutation"),
               ",".join(codes[:3]))
        )
        item["death_codes"] = codes[:5]
        out.append(item)
        if len(out) >= limit:
            break
    return out


def generate_cross_learn_variants(kind, definition, symbol, timeframe,
                                  strategy_key, limit=4):
    """Adopt one numeric difference from a better peer on same symbol/tf."""
    peers = [t for t in list_live_targets()
             if t["symbol"] == symbol and t["timeframe"] == timeframe
             and t["strategy_key"] != strategy_key]
    if not peers or kind != "dsl":
        return []
    # Rank peers by recent live win rate
    import auto_trade_human_confirm_pipeline as pipeline
    ranked = []
    for peer in peers:
        trades = pipeline._closed_trades_for(peer["strategy_key"], limit=20)
        if len(trades) < 5:
            continue
        wr = sum(1 for t in trades if t.get("profit")) / float(len(trades)) * 100.0
        ranked.append((wr, peer))
    ranked.sort(key=lambda x: -x[0])
    if not ranked:
        return []
    self_trades = pipeline._closed_trades_for(strategy_key, limit=20)
    self_wr = (sum(1 for t in self_trades if t.get("profit"))
               / float(len(self_trades)) * 100.0) if self_trades else 0.0
    out = []
    for wr, peer in ranked[:3]:
        if wr < self_wr + 5.0:
            continue
        peer_def = load_definition(peer["strategy_key"])
        if not peer_def or peer_def["kind"] != "dsl":
            continue
        # try isolate peer max_hold_bars if different
        try:
            base_hold = int(definition.get("max_hold_bars") or 0)
            peer_hold = int((peer_def["definition"] or {}).get("max_hold_bars") or 0)
        except Exception:
            continue
        if peer_hold and peer_hold != base_hold:
            changed = copy.deepcopy(definition)
            changed["max_hold_bars"] = peer_hold
            out.append({
                "kind": kind,
                "definition": changed,
                "opt_type": "逻辑修改",
                "change": {
                    "field": "max_hold_bars",
                    "from": base_hold,
                    "to": peer_hold,
                    "peer": peer["strategy_key"],
                    "peer_wr": wr,
                },
                "explanation": (
                    "同类交叉学习：同伴 %s 实盘胜率 %.1f%% 优于本策略 %.1f%%，"
                    "试采用其 max_hold_bars=%s"
                    % (peer["strategy_key"], wr, self_wr, peer_hold)),
            })
        if len(out) >= limit:
            break
    return out


# ─── filter / propose ─────────────────────────────────────────────────

def passes_screen(base_m, var_m):
    """Designer gates for pushing a proposal."""
    if int(var_m.get("trades") or 0) < MIN_SAMPLE:
        return False, "n_lt_%s" % MIN_SAMPLE
    wr_lift = float(var_m.get("win_rate") or 0) - float(base_m.get("win_rate") or 0)
    if wr_lift < MIN_WR_LIFT_PP - 1e-9:
        return False, "wr_lift_lt_5pp"
    base_net = float(base_m.get("total_return_pct") or 0)
    var_net = float(var_m.get("total_return_pct") or 0)
    if abs(base_net) < 1e-9:
        if var_net <= 0:
            return False, "base_net_zero_var_non_positive"
        rel = 1.0
    else:
        rel = (var_net - base_net) / abs(base_net)
    if rel < MIN_NET_LIFT_REL - 1e-9:
        return False, "net_lift_lt_10pct"
    if float(var_m.get("max_drawdown") or 0) > float(base_m.get("max_drawdown") or 0) + 1e-9:
        return False, "dd_worse"
    return True, "ok"


def _rejected_blocked(fp):
    doc = _read(REJECTED_PATH, {"items": []})
    cutoff = (datetime.now() - timedelta(days=REJECT_COOLDOWN_DAYS)).strftime(
        "%Y-%m-%d %H:%M:%S")
    for item in doc.get("items") or []:
        if item.get("fingerprint") == fp and str(item.get("rejected_at") or "") >= cutoff:
            return True
    return False


def _pending_has(fp):
    doc = _read(PENDING_PATH, {"items": []})
    for item in doc.get("items") or []:
        if item.get("status") == "pending" and item.get("fingerprint") == fp:
            return True
    return False


def _score_variant(base_m, var_m):
    wr_lift = float(var_m["win_rate"]) - float(base_m["win_rate"])
    base_net = float(base_m.get("total_return_pct") or 0)
    var_net = float(var_m.get("total_return_pct") or 0)
    rel = ((var_net - base_net) / abs(base_net)) if abs(base_net) > 1e-9 else var_net
    return wr_lift * 2.0 + rel * 10.0 - float(var_m.get("max_drawdown") or 0) * 5.0


def explore_one(target, trigger="daily", max_proposals=MAX_PROPOSALS_PER_STRATEGY):
    """Explore optimization directions for one live assignment."""
    key = target["strategy_key"]
    symbol = target["symbol"]
    timeframe = target["timeframe"]
    loaded = load_definition(key)
    if not loaded:
        return {"ok": False, "error": "definition_missing", "key": key}
    kind = loaded["kind"]
    definition = loaded["definition"]
    base_eval = evaluate_definition(kind, definition, symbol, timeframe)
    if not base_eval.get("ok"):
        return {"ok": False, "error": "baseline_fail",
                "detail": base_eval.get("error"), "key": key}
    base_m = base_eval["metrics"]
    if int(base_m.get("trades") or 0) < MIN_SAMPLE:
        return {"ok": True, "skipped": "baseline_n_lt_%s" % MIN_SAMPLE,
                "key": key, "baseline": base_m}

    import auto_trade_human_confirm_pipeline as pipeline
    recent_stops = [t for t in pipeline._closed_trades_for(key, limit=15)
                    if t.get("stop")]

    candidates = []
    candidates.extend(generate_param_scan_variants(kind, definition))
    candidates.extend(generate_logic_variants(kind, definition))
    candidates.extend(generate_death_avoidance_variants(
        kind, definition, recent_stops))
    candidates.extend(generate_cross_learn_variants(
        kind, definition, symbol, timeframe, key))

    # Dedup by fingerprint of change
    seen = set()
    uniq = []
    for c in candidates:
        fp = _fingerprint(c.get("change"))
        if fp in seen:
            continue
        seen.add(fp)
        c["fingerprint"] = fp
        uniq.append(c)
    candidates = uniq[:MAX_VARIANTS_PER_STRATEGY]

    scored = []
    for c in candidates:
        ev = evaluate_definition(kind, c["definition"], symbol, timeframe)
        if not ev.get("ok"):
            continue
        var_m = ev["metrics"]
        ok, reason = passes_screen(base_m, var_m)
        if not ok:
            continue
        if not c.get("explanation"):
            continue  # must be explainable
        if _rejected_blocked(c["fingerprint"]) or _pending_has(c["fingerprint"]):
            continue
        scored.append({
            "variant": c,
            "metrics": var_m,
            "score": _score_variant(base_m, var_m),
            "baseline": base_m,
        })
    scored.sort(key=lambda x: -x["score"])

    pushed = []
    for item in scored[:max_proposals]:
        prop = enqueue_proposal(
            target=target,
            kind=kind,
            baseline=base_m,
            variant=item["variant"],
            metrics=item["metrics"],
            trigger=trigger,
        )
        if prop.get("ok") and prop.get("pushed"):
            pushed.append(prop.get("proposal"))
    return {
        "ok": True,
        "key": key,
        "trigger": trigger,
        "n_candidates": len(candidates),
        "n_passed": len(scored),
        "n_pushed": len(pushed),
        "baseline": base_m,
        "proposals": pushed,
    }


def enqueue_proposal(target, kind, baseline, variant, metrics, trigger):
    prop_id = "opt_%s_%s" % (
        (target["strategy_key"] or "x")[:24],
        uuid.uuid4().hex[:8])
    proposal = {
        "id": prop_id,
        "status": "pending",
        "created_at": _now(),
        "trigger": trigger,
        "assignment_id": target["assignment_id"],
        "strategy_key": target["strategy_key"],
        "strategy_name": target.get("strategy_name") or target["strategy_key"],
        "symbol": target["symbol"],
        "timeframe": target["timeframe"],
        "lifecycle_grade": target.get("lifecycle_grade"),
        "kind": kind,
        "opt_type": variant.get("opt_type") or "参数调整",
        "change": variant.get("change") or {},
        "explanation": variant.get("explanation") or "",
        "fingerprint": variant.get("fingerprint") or _fingerprint(variant.get("change")),
        "baseline": baseline,
        "candidate_metrics": metrics,
        "definition": variant.get("definition"),
        "delta": {
            "win_rate_pp": round(
                float(metrics["win_rate"]) - float(baseline["win_rate"]), 3),
            "total_return_rel": (
                round((float(metrics["total_return_pct"])
                       - float(baseline["total_return_pct"]))
                      / abs(float(baseline["total_return_pct"])), 4)
                if abs(float(baseline.get("total_return_pct") or 0)) > 1e-9
                else None),
            "max_drawdown_base": baseline.get("max_drawdown"),
            "max_drawdown_new": metrics.get("max_drawdown"),
            "trades_base": baseline.get("trades"),
            "trades_new": metrics.get("trades"),
        },
    }
    doc = _read(PENDING_PATH, {"items": []})
    items = list(doc.get("items") or [])
    items.append(proposal)
    # keep last 80
    doc["items"] = items[-80:]
    doc["updated_at"] = _now()
    _atomic(PENDING_PATH, doc)

    msg = (
        "【优化建议·待确认】\n"
        "策略: %s\n"
        "等级: %s · %s / %s\n"
        "类型: %s\n"
        "改动: %s\n"
        "回测对比:\n"
        "  胜率: %.1f%% → %.1f%%（%+.1fpp）\n"
        "  净收益%%: %.2f → %.2f\n"
        "  最大回撤: %.2f%% → %.2f%%\n"
        "  样本: %s → %s 笔\n"
        "确认: python3 auto_trade_strategy_dynamic_optimizer.py --confirm %s\n"
        "拒绝: python3 auto_trade_strategy_dynamic_optimizer.py --reject %s\n"
        "时间: %s"
        % (
            proposal["strategy_name"],
            proposal.get("lifecycle_grade") or "?",
            proposal["symbol"], proposal["timeframe"],
            proposal["opt_type"],
            proposal["explanation"][:240],
            float(baseline["win_rate"]), float(metrics["win_rate"]),
            float(proposal["delta"]["win_rate_pp"]),
            float(baseline["total_return_pct"]), float(metrics["total_return_pct"]),
            float(baseline["max_drawdown"]) * 100.0,
            float(metrics["max_drawdown"]) * 100.0,
            baseline.get("trades"), metrics.get("trades"),
            prop_id, prop_id, _now(),
        )
    )
    wx = _wx(msg, kind="strategy_optimizer_propose", meta={
        "proposal_id": prop_id,
        "strategy_key": proposal["strategy_key"],
        "opt_type": proposal["opt_type"],
    })
    _append_audit({"event": "propose", "proposal_id": prop_id,
                   "key": proposal["strategy_key"], "trigger": trigger,
                   "wx": bool(wx.get("ok"))})
    return {"ok": True, "pushed": True, "proposal": proposal, "wx": wx}


# ─── confirm / reject / apply ─────────────────────────────────────────

def _find_pending(proposal_id):
    doc = _read(PENDING_PATH, {"items": []})
    for i, item in enumerate(doc.get("items") or []):
        if item.get("id") == proposal_id:
            return doc, i, item
    return doc, -1, None


def apply_proposal(proposal):
    """Write definition in place; keep strategy key; do not reset trade counters."""
    kind = proposal.get("kind")
    key = proposal.get("strategy_key")
    definition = proposal.get("definition")
    if not definition or not key:
        return {"ok": False, "error": "missing_definition"}
    backup_dir = AUTO_DIR / "backups" / "optimizer_applies"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if kind == "dsl":
        import auto_trade_strategy_dsl as dsl_mod
        definition = dsl_mod.validate_strategy(definition)
        definition["key"] = key
        data = _read(DSL_CONFIG_PATH, {"strategies": []})
        _atomic(backup_dir / ("%s_%s_dsl.json" % (stamp, key[:40])), data)
        rows = list(data.get("strategies") or [])
        found = False
        for i, row in enumerate(rows):
            if row.get("key") == key:
                # preserve live flags
                live = bool(row.get("live_enabled", True))
                eligible = bool(row.get("auto_trade_eligible", True))
                new_row = {k: v for k, v in definition.items()
                           if k not in ("fingerprint",)}
                new_row["key"] = key
                new_row["live_enabled"] = live
                new_row["auto_trade_eligible"] = eligible
                new_row["optimizer_applied_at"] = _now()
                new_row["optimizer_proposal_id"] = proposal.get("id")
                rows[i] = new_row
                found = True
                break
        if not found:
            return {"ok": False, "error": "dsl_key_missing"}
        data["strategies"] = rows
        data["updated_at"] = _now()
        _atomic(DSL_CONFIG_PATH, data)
    elif kind == "experimental":
        data = _read(EXP_CONFIG_PATH, {"strategies": []})
        _atomic(backup_dir / ("%s_%s_exp.json" % (stamp, key[:40])), data)
        rows = list(data.get("strategies") or [])
        found = False
        for i, row in enumerate(rows):
            if row.get("key") == key:
                new_row = dict(row)
                new_row["params"] = dict(
                    (definition.get("params") or row.get("params") or {}))
                if definition.get("max_hold_bars") is not None:
                    new_row["max_hold_bars"] = definition.get("max_hold_bars")
                new_row["optimizer_applied_at"] = _now()
                new_row["optimizer_proposal_id"] = proposal.get("id")
                new_row["modified_at_beijing"] = _now()
                new_row.pop("probe_param_overlay", None)
                rows[i] = new_row
                found = True
                break
        if not found:
            return {"ok": False, "error": "exp_key_missing"}
        data["strategies"] = rows
        data["updated_at"] = _now()
        _atomic(EXP_CONFIG_PATH, data)
    else:
        return {"ok": False, "error": "unknown_kind"}

    # Annotate assignment without clearing trade windows / counters
    aid = proposal.get("assignment_id")
    if aid:
        controls = _read(CONTROL_PATH, {"assignments": {}})
        row = dict((controls.get("assignments") or {}).get(aid) or {})
        row["optimizer_last_apply_at"] = _now()
        row["optimizer_last_proposal_id"] = proposal.get("id")
        row["optimizer_last_change"] = proposal.get("change")
        # CRITICAL: do not touch promote/stop windows or lifecycle_grade
        controls.setdefault("assignments", {})[aid] = row
        controls["updated_at"] = _now()
        _atomic(CONTROL_PATH, controls)

    # Restart daemon so new DSL/params load
    try:
        import auto_trade_human_confirm_pipeline as pipeline
        unit_map = {
            ("BTC-USDT-SWAP", "15m"): "qiyu-formal-auto-trade-btc-15m.service",
            ("BTC-USDT-SWAP", "5m"): "qiyu-formal-auto-trade-btc-5m.service",
            ("NG-USDT-SWAP", "5m"): "qiyu-formal-auto-trade-ng-5m.service",
            ("ADA-USDT-SWAP", "5m"): "qiyu-formal-auto-trade-ada-5m.service",
            ("XAG-USDT-SWAP", "5m"): "qiyu-formal-auto-trade-xag-5m.service",
            ("XAU-USDT-SWAP", "15m"): "qiyu-formal-auto-trade-xau-15m.service",
            ("CL-USDT-SWAP", "5m"): "qiyu-formal-auto-trade-cl-5m.service",
            ("LTC-USDT-SWAP", "5m"): "qiyu-formal-auto-trade-ltc-5m.service",
        }
        unit = unit_map.get((proposal.get("symbol"), proposal.get("timeframe")))
        if unit:
            import subprocess
            subprocess.call(["systemctl", "try-restart", unit])
    except Exception:
        pass
    return {"ok": True, "key": key, "kind": kind}


def confirm(proposal_id, confirmed_by="codex_human"):
    doc, idx, item = _find_pending(proposal_id)
    if item is None:
        return {"ok": False, "error": "proposal_not_found", "id": proposal_id}
    if item.get("status") != "pending":
        return {"ok": False, "error": "not_pending", "status": item.get("status")}
    applied = apply_proposal(item)
    if not applied.get("ok"):
        return applied
    item = dict(item)
    item["status"] = "applied"
    item["confirmed_at"] = _now()
    item["confirmed_by"] = confirmed_by
    item.pop("definition", None)  # shrink stored payload after apply
    doc["items"][idx] = item
    doc["updated_at"] = _now()
    _atomic(PENDING_PATH, doc)
    _wx(
        "【优化已执行】\n"
        "策略: %s\n"
        "类型: %s\n"
        "改动: %s\n"
        "交易计数: 不清零（升降级窗口保留）\n"
        "提案: %s\n"
        "时间: %s"
        % (item.get("strategy_name"), item.get("opt_type"),
           item.get("explanation", "")[:200], proposal_id, _now()),
        kind="strategy_optimizer_applied",
        meta={"proposal_id": proposal_id, "key": item.get("strategy_key")},
    )
    _append_audit({"event": "confirm_apply", "proposal_id": proposal_id,
                   "by": confirmed_by})
    return {"ok": True, "applied": applied, "proposal_id": proposal_id}


def reject(proposal_id, reason="human_reject"):
    doc, idx, item = _find_pending(proposal_id)
    if item is None:
        return {"ok": False, "error": "proposal_not_found", "id": proposal_id}
    item = dict(item)
    item["status"] = "rejected"
    item["rejected_at"] = _now()
    item["reject_reason"] = reason
    item.pop("definition", None)
    doc["items"][idx] = item
    doc["updated_at"] = _now()
    _atomic(PENDING_PATH, doc)

    rej = _read(REJECTED_PATH, {"items": []})
    rej_items = list(rej.get("items") or [])
    rej_items.append({
        "proposal_id": proposal_id,
        "fingerprint": item.get("fingerprint"),
        "strategy_key": item.get("strategy_key"),
        "rejected_at": _now(),
        "reason": reason,
        "change": item.get("change"),
        "cooldown_days": REJECT_COOLDOWN_DAYS,
    })
    rej["items"] = rej_items[-200:]
    rej["updated_at"] = _now()
    _atomic(REJECTED_PATH, rej)

    _wx(
        "【优化已拒绝】\n"
        "策略: %s\n"
        "提案: %s\n"
        "原因: %s\n"
        "30天内同指纹不再推送\n"
        "时间: %s"
        % (item.get("strategy_name"), proposal_id, reason, _now()),
        kind="strategy_optimizer_rejected",
        meta={"proposal_id": proposal_id},
    )
    _append_audit({"event": "reject", "proposal_id": proposal_id,
                   "reason": reason})
    return {"ok": True, "proposal_id": proposal_id, "rejected": True}


# ─── daily / event / learning ─────────────────────────────────────────

def run_daily_explore(force=False, limit=None):
    """Daily pass over all B+ live strategies (factory / timer)."""
    targets = list_live_targets()
    if limit:
        targets = targets[:int(limit)]
    results = []
    pushed = 0
    for target in targets:
        if pushed >= MAX_PROPOSALS_PER_RUN:
            break
        try:
            out = explore_one(
                target, trigger="daily",
                max_proposals=min(
                    MAX_PROPOSALS_PER_STRATEGY,
                    MAX_PROPOSALS_PER_RUN - pushed))
        except Exception as exc:
            out = {"ok": False, "error": str(exc),
                   "key": target.get("strategy_key")}
        results.append(out)
        pushed += int(out.get("n_pushed") or 0)
    state = {
        "ok": True,
        "mode": "daily_explore",
        "time": _now(),
        "n_targets": len(targets),
        "n_pushed": pushed,
        "results": [
            {k: v for k, v in r.items() if k != "proposals"}
            for r in results
        ],
    }
    _atomic(STATE_PATH, state)
    _append_audit({"event": "daily_explore", "n_targets": len(targets),
                   "n_pushed": pushed})
    # Daily exploration remains active, but its aggregate summary is deliberately
    # audit-only.  Individual actionable proposals already have their own
    # WxPusher notification, so the daily summary was duplicate noise.
    # Weekly health is sent only via scripts/monday_morning_wx_bundle.py (Mon 08:00).
    return state


def on_lifecycle_event(assignment_id, event_type, row=None):
    """Event hook: demotion or consecutive stops → immediate explore."""
    controls = _read(CONTROL_PATH, {"assignments": {}})
    row = row or (controls.get("assignments") or {}).get(assignment_id) or {}
    parts = str(assignment_id).split("|", 2)
    if len(parts) != 3:
        return {"ok": False, "error": "bad_assignment_id"}
    symbol, timeframe, key = parts
    grade = str(row.get("lifecycle_grade") or "").upper()
    # Still explore after B→C (grade may now be C)
    target = {
        "assignment_id": assignment_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy_key": row.get("strategy_key") or key,
        "strategy_name": row.get("strategy_name") or key,
        "lifecycle_grade": grade or "C",
        "row": row,
    }
    out = explore_one(target, trigger="event:%s" % event_type,
                      max_proposals=MAX_PROPOSALS_PER_STRATEGY)
    _append_audit({"event": "lifecycle_hook", "assignment_id": assignment_id,
                   "event_type": event_type, "n_pushed": out.get("n_pushed")})
    return out


def note_closed_trade(closed):
    """Per-close learning: profitable → env snapshot; stop → fragile mark."""
    closed = dict(closed or {})
    key = str(closed.get("strategy_key") or "")
    if not key:
        return {"ok": False, "error": "no_key"}
    is_stop = False
    try:
        import auto_trade_human_confirm_pipeline as pipeline
        is_stop = pipeline._is_stop_trade(closed)
    except Exception:
        reason = str(closed.get("close_type") or closed.get("close_reason") or "").lower()
        is_stop = "stop" in reason
    try:
        pnl = float(closed.get("pnl") or 0)
    except Exception:
        pnl = 0.0
    profitable = pnl > 0 and not is_stop
    doc = _read(LESSONS_PATH, {"by_strategy": {}})
    by = doc.setdefault("by_strategy", {})
    row = by.setdefault(key, {"wins": [], "stops": [], "updated_at": None})
    snap = {
        "closed_at": closed.get("closed_at") or _now(),
        "pnl": pnl,
        "side": closed.get("side") or closed.get("posSide"),
        "symbol": closed.get("symbol") or closed.get("instId"),
        "close_type": closed.get("close_type") or closed.get("close_reason"),
        "entry_price": closed.get("entry_price"),
        "close_price": closed.get("close_price"),
    }
    if profitable:
        row["wins"] = (list(row.get("wins") or []) + [snap])[-30:]
    if is_stop:
        # death match
        death_hit = None
        try:
            import auto_trade_niche_map as niche
            report = niche.build_report(days=7) if hasattr(niche, "build_report") else {}
            codes = []
            for item in (report.get("death_heatmap_prior")
                         or report.get("top_death_codes") or []):
                if isinstance(item, dict):
                    codes.append(str(item.get("code") or ""))
                else:
                    codes.append(str(item))
            if codes:
                death_hit = codes[0]
                snap["death_code_hint"] = death_hit
        except Exception:
            pass
        row["stops"] = (list(row.get("stops") or []) + [snap])[-30:]
        if death_hit:
            fragile = list(row.get("fragile_points") or [])
            fragile.append({"at": _now(), "death_code": death_hit})
            row["fragile_points"] = fragile[-20:]
    row["updated_at"] = _now()
    by[key] = row
    doc["by_strategy"] = by
    doc["updated_at"] = _now()
    _atomic(LESSONS_PATH, doc)

    # consecutive 2-stop event: only when this close is a stop and the
    # previous recorded close lesson was also a stop (true streak), with cooldown.
    if is_stop:
        streak = list(row.get("stops") or [])[-2:]
        last_hook = str(row.get("last_consec_stop_hook_at") or "")
        cooldown_ok = (not last_hook) or last_hook < (
            datetime.now() - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
        if len(streak) >= 2 and cooldown_ok:
            controls = _read(CONTROL_PATH, {"assignments": {}})
            for aid, arow in (controls.get("assignments") or {}).items():
                if (arow.get("strategy_key") == key or aid.endswith("|" + key)):
                    grade = str(arow.get("lifecycle_grade") or "").upper()
                    if grade in MIN_GRADE or grade == "C":
                        row["last_consec_stop_hook_at"] = _now()
                        by[key] = row
                        doc["by_strategy"] = by
                        _atomic(LESSONS_PATH, doc)
                        try:
                            on_lifecycle_event(aid, "consecutive_stops_2", arow)
                        except Exception as exc:
                            _append_audit({"event": "consec_stop_hook_fail",
                                           "error": str(exc), "key": key})
                    break
    return {"ok": True, "key": key, "profitable": profitable, "stop": is_stop}


def weekly_health_diagnosis(batch_time=None, push_wx=True):
    """Weekly health report per live strategy + optional AI tips via factory budget."""
    lessons = _read(LESSONS_PATH, {"by_strategy": {}})
    targets = list_live_targets()
    reports = []
    for t in targets:
        key = t["strategy_key"]
        row = (lessons.get("by_strategy") or {}).get(key) or {}
        wins = list(row.get("wins") or [])
        stops = list(row.get("stops") or [])
        fragile = list(row.get("fragile_points") or [])
        suggestions = []
        if stops and len(stops) >= 2:
            suggestions.append(
                "近期止损偏多（%d），建议检查入场过滤或缩短持仓"
                % len(stops[-10:]))
        if fragile:
            codes = sorted({f.get("death_code") for f in fragile if f.get("death_code")})
            if codes:
                suggestions.append("脆弱点命中死因: %s" % ",".join(codes[:3]))
        if wins and len(wins) >= 3:
            suggestions.append(
                "盈利单样本 %d，可保留当前环境偏好，避免过度放宽参数"
                % len(wins[-10:]))
        # Optional AI tip via factory budget (1 call max if remaining)
        ai_tip = None
        try:
            import auto_trade_strategy_creation_factory as factory
            ok, st, _ = factory.budget_allow(n_calls=1, manual=False)
            if ok and suggestions:
                # Deterministic tip first; AI optional and soft-fail
                ai_tip = "；".join(suggestions[:2])
                factory.budget_consume(n_calls=1, note="optimizer_health_tip")
        except Exception:
            ai_tip = "；".join(suggestions[:2]) if suggestions else None
        reports.append({
            "strategy_key": key,
            "strategy_name": t.get("strategy_name"),
            "grade": t.get("lifecycle_grade"),
            "wins_n": len(wins),
            "stops_n": len(stops),
            "fragile": fragile[-5:],
            "suggestions": suggestions[:2],
            "ai_tip": ai_tip,
        })
    doc = {
        "ok": True,
        "created_at": _now(),
        "reports": reports,
    }
    _atomic(HEALTH_PATH, doc)
    # Push compact Wx
    stamp = batch_time or _now()
    lines = ["【策略健康诊断·周报】"]
    for r in reports[:8]:
        tip = (r.get("suggestions") or [r.get("ai_tip") or "暂无建议"])[0]
        lines.append("- %s(%s): 盈%d/止%d · %s"
                     % (r.get("strategy_name") or r["strategy_key"],
                        r.get("grade"), r.get("wins_n"), r.get("stops_n"), tip))
    if len(reports) > 8:
        lines.append("…共 %d 个策略" % len(reports))
    lines.append("时间: %s" % stamp)
    if push_wx:
        _wx("\n".join(lines), kind="strategy_optimizer_health",
            meta={"n": len(reports), "batch_time": stamp})
    _append_audit({"event": "weekly_health", "n": len(reports)})
    return doc


def status():
    pending = _read(PENDING_PATH, {"items": []})
    items = [x for x in (pending.get("items") or []) if x.get("status") == "pending"]
    return {
        "ok": True,
        "time": _now(),
        "pending_n": len(items),
        "pending": [
            {"id": x.get("id"), "key": x.get("strategy_key"),
             "opt_type": x.get("opt_type"), "created_at": x.get("created_at")}
            for x in items
        ],
        "state": _read(STATE_PATH, {}),
        "health_at": (_read(HEALTH_PATH, {}) or {}).get("created_at"),
    }


def list_pending():
    doc = _read(PENDING_PATH, {"items": []})
    return {
        "ok": True,
        "items": [x for x in (doc.get("items") or [])
                  if x.get("status") == "pending"],
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Qiyu 策略动态优化器")
    parser.add_argument("--daily", action="store_true",
                        help="每日优化探索（B级以上）")
    parser.add_argument("--force-daily", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--event", type=str, default="",
                        help="assignment_id for event explore")
    parser.add_argument("--event-type", type=str, default="manual")
    parser.add_argument("--confirm", type=str, default="")
    parser.add_argument("--reject", type=str, default="")
    parser.add_argument("--list-pending", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    if args.status:
        print(json.dumps(status(), ensure_ascii=False, indent=2, default=str))
    elif args.list_pending:
        print(json.dumps(list_pending(), ensure_ascii=False, indent=2, default=str))
    elif args.confirm:
        print(json.dumps(confirm(args.confirm), ensure_ascii=False, indent=2,
                         default=str))
    elif args.reject:
        print(json.dumps(reject(args.reject), ensure_ascii=False, indent=2,
                         default=str))
    elif args.health:
        print(json.dumps(weekly_health_diagnosis(), ensure_ascii=False,
                         indent=2, default=str))
    elif args.event:
        print(json.dumps(on_lifecycle_event(args.event, args.event_type),
                         ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(
            run_daily_explore(force=args.force_daily,
                              limit=(args.limit or None)),
            ensure_ascii=False, indent=2, default=str))
