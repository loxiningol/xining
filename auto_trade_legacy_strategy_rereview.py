# -*- coding: utf-8 -*-
"""Legacy mounted strategies: extreme purge + retrospective 3AI theoretical review.

One-shot governance for pre-Codex / pre-3AI auto-trade mounts:
  · Extreme live performers → delete (no AI quota)
  · Paused mass_ / E / D probes → delete (no AI quota)
  · Remaining mounted strategies missing ai_theoretical_wr_avg → serial 3AI review
    - pass: write WR + retrospective flags (do NOT change position ratio)
    - fail: full delete via human-confirm pipeline

Skip the already-proper strategy: ada5_z20_t60_prev_h14_0724k.
Default mode is dry-run; pass --apply to write.
"""
from __future__ import print_function

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"
AUDIT_PATH = AUTO_DIR / "legacy_strategy_rereview_audit.jsonl"
STATE_PATH = AUTO_DIR / "legacy_strategy_rereview_latest.json"

SKIP_KEYS = frozenset({"ada5_z20_t60_prev_h14_0724k"})
AI_GAP_SEC = float(os.environ.get("QIYU_LEGACY_REREVIEW_GAP_SEC", "8"))


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


def _append_audit(row):
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    row = dict(row or {})
    row.setdefault("time", _now())
    with AUDIT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _wx(message, kind="legacy_rereview_batch", meta=None):
    try:
        import auto_trade_formal_notify as notify
        return notify.send_message(message, kind=kind, meta=meta or {})
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _display_name(key, fallback=None):
    try:
        import auto_trade_strategy_titles as titles
        return titles.resolve_strategy_name(key, fallback or key)
    except Exception:
        return fallback or key


def _live_stats(strategy_key, limit=40):
    """Closed-trade window for extreme filters."""
    import auto_trade_human_confirm_pipeline as pipeline
    trades = pipeline._closed_trades_for(strategy_key, limit=limit)
    n = len(trades)
    if not n:
        return {
            "n": 0, "wins": 0, "wr_pct": None, "net_pnl": 0.0,
            "stops": 0, "loss_streak": 0, "trades": [],
        }
    wins = sum(1 for t in trades if t.get("profit"))
    stops = sum(1 for t in trades if t.get("stop"))
    net = sum(float(t.get("pnl") or 0.0) for t in trades)
    streak = 0
    for t in trades:  # newest-first from _closed_trades_for
        if t.get("profit"):
            break
        streak += 1
    return {
        "n": n,
        "wins": wins,
        "wr_pct": round(wins / float(n) * 100.0, 2),
        "net_pnl": round(net, 4),
        "stops": stops,
        "loss_streak": streak,
        "trades": trades,
    }


def is_extreme_live(stats):
    """Any extreme rule hit → delete before spending AI quota."""
    n = int(stats.get("n") or 0)
    stops = int(stats.get("stops") or 0)
    wr = stats.get("wr_pct")
    net = float(stats.get("net_pnl") or 0.0)
    streak = int(stats.get("loss_streak") or 0)
    reasons = []
    if n >= 3 and stops >= 2:
        reasons.append("stops_ge2_in_ge3")
    if n >= 5 and wr is not None and wr < 40.0 and net < 0:
        reasons.append("wr_lt40_net_neg_ge5")
    if streak >= 3:
        reasons.append("consecutive_losses_ge3")
    return bool(reasons), reasons


def is_mass_ed_probe(item):
    """Paused mass / E / D probes: batch-delete without AI."""
    row = item.get("row") or {}
    key = str(item.get("strategy_key") or "")
    grade = str(
        row.get("lifecycle_grade") or row.get("max_grade")
        or item.get("lifecycle_grade") or ""
    ).upper()
    is_mass = bool(row.get("mass_engine")) or key.startswith("mass_")
    is_ed = grade in ("E", "D")
    paused = bool(item.get("pause_new_entries") or row.get("pause_new_entries"))
    frozen = bool(row.get("frozen_by_human_pipeline"))
    # Plan: 已暂停 mass_/E/D 探针 — require pause (or freeze flag) + mass/ED
    if not (is_mass or is_ed):
        return False, []
    if not (paused or frozen):
        # still treat mass_* keys as purge targets even if somehow unpaused
        if is_mass:
            return True, ["mass_probe"]
        return False, []
    tags = []
    if is_mass:
        tags.append("mass_probe")
    if is_ed:
        tags.append("grade_%s" % grade)
    return True, tags


def has_theoretical_wr(row):
    try:
        wr = (row or {}).get("ai_theoretical_wr_avg")
        if wr is None:
            return False
        float(wr)
        return True
    except Exception:
        return False


def build_inventory():
    import auto_trade_system_forecast as forecast
    mounted = forecast.list_auto_trade_strategies()
    rows = []
    for item in mounted:
        key = item.get("strategy_key")
        if not key:
            continue
        row = item.get("row") or {}
        stats = _live_stats(key)
        extreme, extreme_reasons = is_extreme_live(stats)
        mass_ed, mass_tags = is_mass_ed_probe(item)
        skip = key in SKIP_KEYS
        has_wr = has_theoretical_wr(row)
        rows.append({
            "assignment_id": item.get("assignment_id"),
            "strategy_key": key,
            "strategy_name": _display_name(key, item.get("strategy_name")),
            "symbol": item.get("symbol"),
            "timeframe": item.get("timeframe"),
            "lifecycle_grade": item.get("lifecycle_grade"),
            "can_open": bool(item.get("can_open")),
            "pause_new_entries": bool(item.get("pause_new_entries")),
            "has_ai_theoretical_wr": has_wr,
            "ai_theoretical_wr_avg": row.get("ai_theoretical_wr_avg"),
            "skip_proper": skip,
            "live_n": stats["n"],
            "live_wr_pct": stats["wr_pct"],
            "live_net_pnl": stats["net_pnl"],
            "live_stops": stats["stops"],
            "live_loss_streak": stats["loss_streak"],
            "extreme": extreme,
            "extreme_reasons": extreme_reasons,
            "mass_ed_purge": mass_ed,
            "mass_ed_tags": mass_tags,
            "daemon_config": item.get("daemon_config"),
            "max_position_ratio": item.get("max_position_ratio"),
            "row": row,
            "_stats_trades": stats.get("trades") or [],
        })
    return rows


def classify_actions(inventory):
    """Partition into purge / rereview / skip buckets (priority: skip > extreme > mass_ed > rereview)."""
    purge_extreme = []
    purge_mass_ed = []
    rereview = []
    skip = []
    keep_with_wr = []
    for item in inventory:
        key = item.get("strategy_key")
        if item.get("skip_proper"):
            skip.append(item)
            continue
        if item.get("extreme"):
            purge_extreme.append(item)
            continue
        if item.get("mass_ed_purge"):
            purge_mass_ed.append(item)
            continue
        if item.get("has_ai_theoretical_wr"):
            keep_with_wr.append(item)
            continue
        # remaining mounted without WR → rereview (open or paused non-mass)
        rereview.append(item)
    return {
        "purge_extreme": purge_extreme,
        "purge_mass_ed": purge_mass_ed,
        "rereview": rereview,
        "skip_proper": skip,
        "keep_with_wr": keep_with_wr,
    }


def _scrub_daemon_key_on_configs(key, daemon_config_names=None, symbol=None):
    """Remove key from specific daemon config files (not all symbols)."""
    import auto_trade_human_confirm_pipeline as pipeline
    key = str(key or "")
    if not key:
        return 0
    targets = []
    names = [n for n in (daemon_config_names or []) if n]
    if names:
        for name in names:
            path = AUTO_DIR / name
            if path.exists():
                targets.append(path)
    else:
        for path in AUTO_DIR.glob("formal_daemon_config*.json"):
            cfg = pipeline._read(path, {})
            if symbol and cfg.get("symbol") and cfg.get("symbol") != symbol:
                continue
            keys = list(cfg.get("strategy_keys") or [])
            if key in keys or cfg.get("strategy_key") == key:
                targets.append(path)
    n = 0
    seen = set()
    for path in targets:
        if str(path) in seen:
            continue
        seen.add(str(path))
        cfg = pipeline._read(path, {})
        if not isinstance(cfg, dict):
            continue
        keys = list(cfg.get("strategy_keys") or [])
        if key not in keys and cfg.get("strategy_key") != key:
            continue
        keys = [k for k in keys if k != key]
        cfg["strategy_keys"] = keys
        # Keep strategy_key explicit so daemon setdefault cannot revive ema6 default.
        cfg["strategy_key"] = keys[0] if keys else ""
        if not keys:
            cfg["allow_auto_open"] = False
        pipeline._atomic(path, cfg)
        n += 1
    return n


def delete_strategy(aid, row, reason, trades, apply=False,
                    daemon_config=None):
    import auto_trade_human_confirm_pipeline as pipeline
    if not apply:
        return {
            "ok": True, "dry_run": True, "assignment_id": aid,
            "reason": reason, "action": "would_delete",
        }
    out_row = pipeline._delete_strategy(aid, row, reason, trades)
    key = (row or {}).get("strategy_key") or (
        aid.split("|", 2)[-1] if aid and "|" in str(aid) else "")
    symbol = (row or {}).get("symbol") or (
        aid.split("|")[0] if aid and "|" in str(aid) else None)
    names = [daemon_config] if daemon_config else []
    scrubbed = _scrub_daemon_key_on_configs(
        key, daemon_config_names=names, symbol=symbol)
    _append_audit({
        "event": "deleted",
        "assignment_id": aid,
        "reason": reason,
        "strategy_key": key,
        "daemon_configs_scrubbed": scrubbed,
        "daemon_config": daemon_config,
    })
    return {
        "ok": True, "dry_run": False, "assignment_id": aid,
        "reason": reason, "action": "deleted",
        "lifecycle_grade": (out_row or {}).get("lifecycle_grade"),
        "daemon_configs_scrubbed": scrubbed,
    }


def _enrich_metrics(metrics, symbol, timeframe, definition):
    """Normalize metrics fields for codex evidence builder."""
    out = dict(metrics or {})
    out.setdefault("symbol", symbol)
    out.setdefault("timeframe", timeframe)
    out.setdefault("leverage", 20)
    out.setdefault(
        "stop_loss_pct",
        definition.get("stop_loss_pct")
        or (definition.get("params") or {}).get("stop_loss_pct")
        or 0.10,
    )
    if out.get("max_loss_streak") is None:
        # best-effort from trades if present later; leave None otherwise
        pass
    return out


def _candidate_for_ai(kind, definition):
    """Slim candidate object for theoretical_review_all hashing/prompt."""
    if kind == "dsl":
        return definition
    return {
        "key": definition.get("key"),
        "name": definition.get("name"),
        "kind": "experimental",
        "direction": definition.get("direction"),
        "timeframe": definition.get("timeframe")
        or (definition.get("supported_timeframes") or [None])[0],
        "supported_instruments": definition.get("supported_instruments"),
        "description": definition.get("description"),
        "trigger": definition.get("trigger"),
        "exit": definition.get("exit"),
        "params": definition.get("params"),
        "stop_loss_pct": definition.get("stop_loss_pct"),
        "max_hold_bars": (definition.get("params") or {}).get("max_hold_bars"),
    }


def _load_and_screen(item):
    """Load DSL or experimental definition and produce evidence metrics.

    Returns (ok, payload, error). On ok, payload has kind/definition/metrics/meta.
    Infra/load failures return ok=False with error — caller must NOT delete.
    """
    import auto_trade_strategy_dynamic_optimizer as opt
    import auto_trade_human_confirm_pipeline as pipeline
    import auto_trade_codex_strategy_review as codex

    key = item.get("strategy_key")
    symbol = item.get("symbol")
    timeframe = item.get("timeframe")
    loaded = opt.load_definition(key)
    if not loaded or not loaded.get("definition"):
        return False, None, "definition_not_found"
    definition = loaded["definition"]
    kind = loaded.get("kind") or "dsl"
    symbol = symbol or (definition.get("supported_instruments") or [None])[0]
    timeframe = (
        timeframe
        or definition.get("timeframe")
        or (definition.get("supported_timeframes") or [None])[0]
    )
    meta = {
        "symbol": symbol,
        "timeframe": timeframe,
        "thesis": definition.get("description") or definition.get("name")
        or definition.get("trigger"),
        "author": "legacy_retrospective",
        "notes": "retrospective_ai_review_of_mounted_legacy_strategy",
    }

    if kind == "dsl" or definition.get("entry"):
        cand = {
            "dsl": definition,
            "symbol": symbol,
            "timeframe": timeframe,
            "thesis": meta["thesis"],
        }
        ok, metrics, reason = pipeline.safety_screen_candidate(cand)
        if not ok:
            # Genuine safety hard-fail (lookahead/death/validate) → deletable
            return False, {
                "deletable": True,
                "stage": "safety",
                "reason": reason,
                "metrics": metrics,
            }, reason
        metrics = _enrich_metrics(metrics, symbol, timeframe, definition)
        evidence = codex._build_evidence(definition, metrics, meta)
        return True, {
            "kind": "dsl",
            "definition": definition,
            "candidate": definition,
            "metrics": metrics,
            "evidence": evidence,
            "meta": meta,
        }, None

    # Experimental / params strategies: friction backtest via ecosystem engine
    ev = opt.evaluate_definition("experimental", definition, symbol, timeframe)
    if not ev.get("ok"):
        return False, {"deletable": False, "stage": "evaluate"}, (
            "evaluate_fail:%s" % (ev.get("error") or "unknown")
        )
    metrics = _enrich_metrics(ev.get("metrics") or {}, symbol, timeframe, definition)
    # max_loss_streak from result trades if available
    try:
        trades = list((ev.get("result") or {}).get("trades") or [])
        pnls = [float(t.get("pnl_ratio") or 0.0) for t in trades]
        streak = mx = 0
        for p in pnls:
            if p <= 0:
                streak += 1
                mx = max(mx, streak)
            else:
                streak = 0
        metrics["max_loss_streak"] = mx
        metrics["bars_used"] = metrics.get("bars_used")
    except Exception:
        pass
    candidate = _candidate_for_ai("experimental", definition)
    evidence = codex._build_evidence(candidate, metrics, meta)
    evidence["source"] = "legacy_experimental_retrospective"
    evidence["logic_brief"] = {
        "key": definition.get("key"),
        "name": definition.get("name"),
        "direction": definition.get("direction"),
        "timeframe": timeframe,
        "trigger": definition.get("trigger"),
        "exit": definition.get("exit"),
        "params": definition.get("params"),
        "description": definition.get("description"),
    }
    return True, {
        "kind": "experimental",
        "definition": definition,
        "candidate": candidate,
        "metrics": metrics,
        "evidence": evidence,
        "meta": meta,
    }, None


def _review_has_infra_failure(review):
    """True if any provider failed due to API/HTTP/rate-limit — not a substantive reject."""
    reviews = review.get("reviews") or []
    if not reviews:
        # fallback: inspect fail_reasons / natural language
        blob = " ".join(str(x) for x in (review.get("fail_reasons") or []))
        blob += " " + str(review.get("natural_language") or "")
        markers = ("429", "Too Many Requests", "HTTP Error", "调用失败",
                   "timeout", "Timed out", "API密钥未配置", "外部AI研究授权")
        return any(m in blob for m in markers)
    for row in reviews:
        if row.get("ok"):
            continue
        reason = str(row.get("reason") or "")
        if row.get("consent_missing") or row.get("credential_missing"):
            return True
        markers = ("429", "Too Many Requests", "HTTP Error", "调用失败",
                   "timeout", "Timed out", "URLError", "Connection")
        if any(m in reason for m in markers):
            return True
        # ok=False with empty decision content often infra
        if not row.get("ok") and float(row.get("theoretical_win_rate_pct") or 0) == 0 \
                and str(row.get("stop_cluster_risk") or "") == "high" \
                and ("失败" in reason or "Error" in reason or "429" in reason):
            return True
    return False


def rereview_one(item, apply=False):
    """Safety/evidence + 3AI theoretical review; write WR or delete on AI reject.

    Infra/load/API failures (incl. ChatGPT 429) do NOT delete — retry later.
    """
    import auto_trade_human_confirm_pipeline as pipeline
    import auto_trade_ai_consensus as ai

    aid = item.get("assignment_id")
    key = item.get("strategy_key")
    row = dict(item.get("row") or {})
    row.setdefault("strategy_key", key)
    row.setdefault("symbol", item.get("symbol"))
    row.setdefault("timeframe", item.get("timeframe"))
    row.setdefault("strategy_name", item.get("strategy_name"))
    trades = item.get("_stats_trades") or []

    ok, payload, err = _load_and_screen(item)
    if not ok:
        deletable = bool((payload or {}).get("deletable"))
        if deletable:
            del_reason = "legacy_rereview_safety_fail:%s" % err
            del_out = delete_strategy(
                aid, row, del_reason, trades, apply=apply,
                daemon_config=item.get("daemon_config"))
            return {
                "ok": False, "strategy_key": key, "assignment_id": aid,
                "stage": (payload or {}).get("stage") or "safety",
                "reason": err, "metrics": (payload or {}).get("metrics"),
                "delete": del_out, "approved": False,
            }
        return {
            "ok": False, "strategy_key": key, "assignment_id": aid,
            "stage": (payload or {}).get("stage") or "load",
            "error": err, "approved": False, "action": "skipped_infra_error",
            "deletable": False,
        }

    candidate = payload["candidate"]
    evidence = payload["evidence"]
    review = ai.theoretical_review_all(candidate, evidence)
    # Attach per-provider rows for infra detection (theoretical_review_all keeps them)
    if "reviews" not in review:
        # reconstruct minimal from maps if needed
        pass

    if _review_has_infra_failure(review):
        _append_audit({
            "event": "rereview_infra_skip",
            "assignment_id": aid,
            "strategy_key": key,
            "fail_reasons": review.get("fail_reasons"),
            "natural_language": review.get("natural_language"),
        })
        return {
            "ok": False, "strategy_key": key, "assignment_id": aid,
            "stage": "ai_infra",
            "error": "provider_infra_failure",
            "fail_reasons": review.get("fail_reasons"),
            "ai_theoretical_wr_avg": review.get("ai_theoretical_wr_avg"),
            "ai_theoretical_wr_by_provider": review.get(
                "ai_theoretical_wr_by_provider"),
            "approved": False, "action": "skipped_infra_error",
            "deletable": False, "dry_run": not apply,
        }

    approved = bool(review.get("approved"))
    out = {
        "ok": approved,
        "strategy_key": key,
        "assignment_id": aid,
        "stage": "ai_theoretical_review",
        "kind": payload.get("kind"),
        "ai_theoretical_wr_avg": review.get("ai_theoretical_wr_avg"),
        "ai_theoretical_wr_by_provider": review.get(
            "ai_theoretical_wr_by_provider"),
        "ai_stop_cluster_risk_by_provider": review.get(
            "ai_stop_cluster_risk_by_provider"),
        "fail_reasons": review.get("fail_reasons"),
        "natural_language": review.get("natural_language"),
        "approved": approved,
        "dry_run": not apply,
        "metrics_summary": {
            "trades": (payload.get("metrics") or {}).get("trades"),
            "win_rate": (payload.get("metrics") or {}).get("win_rate"),
            "mean_net": (payload.get("metrics") or {}).get("mean_net"),
        },
    }

    if approved:
        if apply:
            row["ai_theoretical_wr_avg"] = review.get("ai_theoretical_wr_avg")
            row["ai_theoretical_wr_by_provider"] = review.get(
                "ai_theoretical_wr_by_provider")
            row["ai_stop_cluster_risk_by_provider"] = review.get(
                "ai_stop_cluster_risk_by_provider")
            row["retrospective_ai_reviewed"] = True
            row["retrospective_ai_reviewed_at"] = _now()
            if not row.get("human_confirm_pipeline"):
                row["human_confirm_pipeline"] = True
                row["human_confirm_pipeline_source"] = "legacy_retrospective"
            # Never revive a deleted assignment while backfilling WR metrics.
            if (
                str(row.get("lifecycle_grade") or "").lower() == "deleted"
                or row.get("deleted_at")
                or str(row.get("audit_state") or "") == "eliminated_pending_archive"
            ):
                out["action"] = "skipped_deleted_no_revive"
                out["ai_theoretical_wr_avg"] = review.get("ai_theoretical_wr_avg")
                return out
            pipeline._save_assignment(aid, row)
            _append_audit({
                "event": "rereview_pass",
                "assignment_id": aid,
                "strategy_key": key,
                "ai_theoretical_wr_avg": row.get("ai_theoretical_wr_avg"),
            })
            out["action"] = "wrote_wr"
        else:
            out["action"] = "would_write_wr"
        return out

    del_reason = "legacy_rereview_ai_reject:avg=%s;%s" % (
        review.get("ai_theoretical_wr_avg"),
        ";".join(review.get("fail_reasons") or [])[:180],
    )
    del_out = delete_strategy(
        aid, row, del_reason, trades, apply=apply,
        daemon_config=item.get("daemon_config"))
    out["delete"] = del_out
    out["action"] = "deleted" if apply else "would_delete"
    return out


def restore_false_experimental_deletes(apply=False):
    """Undo deletes caused by experimental_non_dsl load bug; purge vault entries."""
    import auto_trade_human_confirm_pipeline as pipeline

    marker = "legacy_rereview_fail:experimental_non_dsl"
    # Prefer fields from the inventory snapshot taken before/during false delete
    snap = {}
    try:
        latest = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        for item in latest.get("inventory") or []:
            aid = item.get("assignment_id")
            if aid:
                snap[aid] = item
    except Exception:
        pass

    controls = pipeline._read(pipeline.CONTROL_PATH, {"assignments": {}})
    assignments = controls.get("assignments") or {}
    restored = []
    for aid, row in list(assignments.items()):
        reason = str(row.get("delete_reason") or "")
        if marker not in reason:
            continue
        prev = snap.get(aid) or {}
        new_row = dict(row)
        for k in ("deleted_at", "delete_reason"):
            new_row.pop(k, None)
        grade = prev.get("lifecycle_grade") or "B"
        if str(grade).strip() in ("未评级", "待评级", "") or str(grade).upper() in (
                "NONE", "NULL", "UNRATED", "PENDING"):
            grade = "B"
        # Do not coerce a snap graded DELETED into live B via this helper.
        new_row["lifecycle_grade"] = grade
        if prev.get("max_position_ratio") is not None:
            new_row["max_position_ratio"] = prev.get("max_position_ratio")
        pause = bool(prev.get("pause_new_entries"))
        if prev.get("can_open") is True:
            pause = False
        new_row["pause_new_entries"] = pause
        new_row["new_entries_allowed"] = not pause
        new_row["audit_state"] = "conditional_frequency_probe"
        new_row["restored_from_false_delete"] = True
        new_row["restored_at"] = _now()
        restored.append({
            "assignment_id": aid,
            "strategy_key": new_row.get("strategy_key") or aid.split("|", 2)[-1],
            "pause_new_entries": pause,
            "max_position_ratio": new_row.get("max_position_ratio"),
        })
        if apply:
            assignments[aid] = new_row

    # Purge matching failure-vault items so never_auto_revive does not stick
    vault = pipeline._read(pipeline.FAILURE_VAULT, {"items": []})
    items = list(vault.get("items") or [])
    kept = []
    removed_vault = 0
    for item in items:
        if marker in str(item.get("reason") or ""):
            removed_vault += 1
            continue
        kept.append(item)

    if apply:
        controls["assignments"] = assignments
        controls["updated_at"] = _now()
        controls["updated_by"] = "legacy_restore_false_experimental_deletes"
        pipeline._atomic(pipeline.CONTROL_PATH, controls)
        vault = {
            "schema": "qiyu_strategy_failure_vault_v1",
            "updated_at": _now(),
            "items": kept[-5000:],
        }
        pipeline._atomic(pipeline.FAILURE_VAULT, vault)
        _append_audit({
            "event": "restore_false_experimental_deletes",
            "restored_n": len(restored),
            "removed_vault": removed_vault,
            "keys": [r["strategy_key"] for r in restored],
        })

    return {
        "ok": True,
        "apply": apply,
        "restored_n": len(restored),
        "removed_vault": removed_vault,
        "restored": restored,
    }


def run_purge(apply=False, inventory=None):
    inventory = inventory if inventory is not None else build_inventory()
    buckets = classify_actions(inventory)
    results = {"extreme": [], "mass_ed": []}
    for item in buckets["purge_extreme"]:
        aid = item.get("assignment_id")
        row = dict(item.get("row") or {})
        row.setdefault("strategy_key", item.get("strategy_key"))
        row.setdefault("symbol", item.get("symbol"))
        row.setdefault("timeframe", item.get("timeframe"))
        reason = "legacy_extreme_purge:%s" % ",".join(
            item.get("extreme_reasons") or ["extreme"])
        results["extreme"].append(delete_strategy(
            aid, row, reason, item.get("_stats_trades") or [], apply=apply,
            daemon_config=item.get("daemon_config")))
    for item in buckets["purge_mass_ed"]:
        aid = item.get("assignment_id")
        row = dict(item.get("row") or {})
        row.setdefault("strategy_key", item.get("strategy_key"))
        row.setdefault("symbol", item.get("symbol"))
        row.setdefault("timeframe", item.get("timeframe"))
        reason = "legacy_mass_ed_purge:%s" % ",".join(
            item.get("mass_ed_tags") or ["mass_ed"])
        results["mass_ed"].append(delete_strategy(
            aid, row, reason, item.get("_stats_trades") or [], apply=apply,
            daemon_config=item.get("daemon_config")))
    return {
        "ok": True,
        "apply": apply,
        "time": _now(),
        "counts": {
            "extreme": len(results["extreme"]),
            "mass_ed": len(results["mass_ed"]),
        },
        "results": results,
        "buckets_preview": {
            "extreme_keys": [x.get("strategy_key") for x in buckets["purge_extreme"]],
            "mass_ed_keys": [x.get("strategy_key") for x in buckets["purge_mass_ed"]],
        },
    }


def run_rereview(apply=False, inventory=None, gap_sec=None):
    inventory = inventory if inventory is not None else build_inventory()
    buckets = classify_actions(inventory)
    gap = AI_GAP_SEC if gap_sec is None else float(gap_sec)
    results = []
    targets = buckets["rereview"]
    for i, item in enumerate(targets):
        print("[%s/%s] rereview %s ..." % (
            i + 1, len(targets), item.get("strategy_key")), flush=True)
        try:
            one = rereview_one(item, apply=apply)
        except Exception as exc:
            one = {
                "ok": False, "strategy_key": item.get("strategy_key"),
                "assignment_id": item.get("assignment_id"),
                "stage": "exception", "error": str(exc),
            }
            _append_audit({"event": "rereview_exception", "out": one})
        results.append(one)
        if i + 1 < len(targets) and gap > 0:
            time.sleep(gap)
    passed = [r for r in results if r.get("approved")]
    deleted = [r for r in results
               if (not r.get("approved")
                   and r.get("action") in ("deleted", "would_delete"))]
    infra = [r for r in results
             if r.get("action") == "skipped_infra_error" or (
                 not r.get("approved") and r.get("deletable") is False
                 and r.get("stage") in ("load", "evaluate"))]
    return {
        "ok": True,
        "apply": apply,
        "time": _now(),
        "counts": {
            "targets": len(targets),
            "passed": len(passed),
            "failed": len(deleted),
            "infra_skipped": len(infra),
        },
        "passed_keys": [r.get("strategy_key") for r in passed],
        "failed_keys": [r.get("strategy_key") for r in deleted],
        "infra_skipped_keys": [r.get("strategy_key") for r in infra],
        "results": results,
    }


def _fmt_inventory_table(inventory):
    lines = []
    header = (
        "%-4s %-28s %-6s %-4s %-5s %-5s %6s %7s %8s %5s %4s %-8s %s"
        % ("#", "key", "sym", "tf", "open", "hasWR", "n", "wr%", "net",
           "stop", "ls", "action", "name")
    )
    lines.append(header)
    lines.append("-" * len(header))
    buckets = classify_actions(inventory)
    extreme_keys = {x["strategy_key"] for x in buckets["purge_extreme"]}
    mass_keys = {x["strategy_key"] for x in buckets["purge_mass_ed"]}
    rr_keys = {x["strategy_key"] for x in buckets["rereview"]}
    for i, item in enumerate(inventory, 1):
        key = item["strategy_key"]
        if item.get("skip_proper"):
            action = "SKIP"
        elif key in extreme_keys:
            action = "EXTREME"
        elif key in mass_keys:
            action = "MASS/ED"
        elif key in rr_keys:
            action = "REREVIEW"
        elif item.get("has_ai_theoretical_wr"):
            action = "KEEP_WR"
        else:
            action = "?"
        sym = str(item.get("symbol") or "").replace("-USDT-SWAP", "")[:6]
        lines.append(
            "%-4s %-28s %-6s %-4s %-5s %-5s %6s %7s %8s %5s %4s %-8s %s"
            % (
                i,
                (key or "")[:28],
                sym,
                str(item.get("timeframe") or "")[:4],
                "Y" if item.get("can_open") else "N",
                "Y" if item.get("has_ai_theoretical_wr") else "N",
                item.get("live_n"),
                "" if item.get("live_wr_pct") is None else "%.1f" % item["live_wr_pct"],
                "%.2f" % float(item.get("live_net_pnl") or 0),
                item.get("live_stops"),
                item.get("live_loss_streak"),
                action,
                (item.get("strategy_name") or "")[:24],
            )
        )
    summary = (
        "\nSUMMARY mounted=%s extreme=%s mass_ed=%s rereview=%s "
        "skip_proper=%s keep_wr=%s"
        % (
            len(inventory),
            len(buckets["purge_extreme"]),
            len(buckets["purge_mass_ed"]),
            len(buckets["rereview"]),
            len(buckets["skip_proper"]),
            len(buckets["keep_with_wr"]),
        )
    )
    lines.append(summary)
    return "\n".join(lines)


def _strip_heavy(inventory):
    out = []
    for item in inventory:
        row = dict(item)
        row.pop("_stats_trades", None)
        row.pop("row", None)
        out.append(row)
    return out


def notify_batch_summary(purge_out=None, rereview_out=None, apply=False):
    parts = ["【遗留策略治理汇总】", "模式: %s" % ("实写" if apply else "dry-run")]
    if purge_out:
        c = purge_out.get("counts") or {}
        parts.append("极差删除: %s" % c.get("extreme", 0))
        parts.append("mass/E/D删除: %s" % c.get("mass_ed", 0))
        ex = (purge_out.get("buckets_preview") or {}).get("extreme_keys") or []
        md = (purge_out.get("buckets_preview") or {}).get("mass_ed_keys") or []
        if ex:
            parts.append("极差: %s" % ", ".join(ex[:12]))
        if md:
            parts.append("mass/ED: %s" % ", ".join(md[:12]))
    if rereview_out:
        c = rereview_out.get("counts") or {}
        parts.append(
            "三AI复核: 目标%s / 通过%s / 失败%s"
            % (c.get("targets", 0), c.get("passed", 0), c.get("failed", 0))
        )
        pk = rereview_out.get("passed_keys") or []
        fk = rereview_out.get("failed_keys") or []
        if pk:
            parts.append("通过: %s" % ", ".join(pk[:15]))
        if fk:
            parts.append("失败删除: %s" % ", ".join(fk[:15]))
    parts.append("时间: %s" % _now())
    msg = "\n".join(parts)
    return _wx(msg, kind="legacy_rereview_batch", meta={
        "apply": apply,
        "purge": (purge_out or {}).get("counts"),
        "rereview": (rereview_out or {}).get("counts"),
    })


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Legacy strategy extreme purge + retrospective 3AI review")
    parser.add_argument("--inventory", action="store_true",
                        help="Print mounted inventory table + JSON summary")
    parser.add_argument("--purge-extreme", action="store_true",
                        help="Delete extreme + paused mass/E/D probes")
    parser.add_argument("--rereview-missing-wr", action="store_true",
                        help="Serial 3AI review for remaining missing-WR mounts")
    parser.add_argument("--restore-false-deletes", action="store_true",
                        help="Undo experimental_non_dsl false deletes + vault purge")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write/delete (default dry-run)")
    parser.add_argument("--gap-sec", type=float, default=None,
                        help="Seconds between 3AI reviews (default %s)" % AI_GAP_SEC)
    parser.add_argument("--json-out", default="",
                        help="Optional path to write full JSON result")
    parser.add_argument("--no-wx", action="store_true",
                        help="Skip batch Wx summary")
    args = parser.parse_args(argv)

    if not (args.inventory or args.purge_extreme or args.rereview_missing_wr
            or args.restore_false_deletes):
        parser.error(
            "specify --inventory and/or --purge-extreme and/or "
            "--rereview-missing-wr and/or --restore-false-deletes")

    apply = bool(args.apply)
    payload = {"time": _now(), "apply": apply}

    if args.restore_false_deletes:
        restore_out = restore_false_experimental_deletes(apply=apply)
        payload["restore"] = restore_out
        print(json.dumps(restore_out, ensure_ascii=False, indent=2))
        if apply and not args.no_wx:
            _wx(
                "【遗留策略误删恢复】\n"
                "原因: experimental_non_dsl 加载缺陷导致未跑三AI即删\n"
                "已恢复赋值: %s\n"
                "已清理失败库条目: %s\n"
                "时间: %s"
                % (restore_out.get("restored_n"),
                   restore_out.get("removed_vault"), _now()),
                kind="legacy_rereview_batch",
                meta={"restore_n": restore_out.get("restored_n")},
            )

    inventory = build_inventory()
    buckets = classify_actions(inventory)
    payload.update({
        "mounted": len(inventory),
        "summary": {
            "extreme": len(buckets["purge_extreme"]),
            "mass_ed": len(buckets["purge_mass_ed"]),
            "rereview": len(buckets["rereview"]),
            "skip_proper": len(buckets["skip_proper"]),
            "keep_with_wr": len(buckets["keep_with_wr"]),
        },
        "inventory": _strip_heavy(inventory),
        "bucket_keys": {
            "extreme": [x["strategy_key"] for x in buckets["purge_extreme"]],
            "mass_ed": [x["strategy_key"] for x in buckets["purge_mass_ed"]],
            "rereview": [x["strategy_key"] for x in buckets["rereview"]],
            "skip_proper": [x["strategy_key"] for x in buckets["skip_proper"]],
            "keep_with_wr": [x["strategy_key"] for x in buckets["keep_with_wr"]],
        },
    })

    if args.inventory:
        print(_fmt_inventory_table(inventory))
        print(json.dumps({
            "summary": payload["summary"],
            "bucket_keys": payload["bucket_keys"],
        }, ensure_ascii=False, indent=2))

    purge_out = None
    rereview_out = None
    if args.purge_extreme:
        # Rebuild after inventory print; purge uses pre-rereview inventory
        purge_out = run_purge(apply=apply, inventory=inventory)
        payload["purge"] = {
            "counts": purge_out.get("counts"),
            "buckets_preview": purge_out.get("buckets_preview"),
            "results": purge_out.get("results"),
        }
        print(json.dumps({
            "purge": payload["purge"]["counts"],
            "keys": purge_out.get("buckets_preview"),
            "apply": apply,
        }, ensure_ascii=False, indent=2))

    if args.rereview_missing_wr:
        # After purge (if applied), rebuild so deleted keys are not reviewed
        if apply and args.purge_extreme:
            inventory2 = build_inventory()
        else:
            # dry-run purge does not remove; filter extreme/mass out of rereview
            inventory2 = [
                x for x in inventory
                if x.get("strategy_key") not in set(
                    payload["bucket_keys"]["extreme"]
                    + payload["bucket_keys"]["mass_ed"]
                )
            ]
        rereview_out = run_rereview(
            apply=apply, inventory=inventory2, gap_sec=args.gap_sec)
        payload["rereview"] = {
            "counts": rereview_out.get("counts"),
            "passed_keys": rereview_out.get("passed_keys"),
            "failed_keys": rereview_out.get("failed_keys"),
            "results": rereview_out.get("results"),
        }
        print(json.dumps({
            "rereview": payload["rereview"]["counts"],
            "passed": rereview_out.get("passed_keys"),
            "failed": rereview_out.get("failed_keys"),
            "apply": apply,
        }, ensure_ascii=False, indent=2))

    if (args.purge_extreme or args.rereview_missing_wr) and not args.no_wx:
        wx = notify_batch_summary(purge_out, rereview_out, apply=apply)
        payload["wx"] = wx
        print("WX", json.dumps(wx, ensure_ascii=False))

    _atomic(STATE_PATH, payload)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
