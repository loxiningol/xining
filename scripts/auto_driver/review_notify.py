# -*- coding: utf-8 -*-
"""WxPusher adapters for the 3-review auto_driver pipeline.

IMPORTANT:
  - Human-confirm broadcast ALREADY exists via
    auto_trade_human_confirm_pipeline._wx → auto_trade_formal_notify.send_message
    (kind=strategy_pending_confirm). Reuse that path; do NOT invent a second channel.
  - Strategy-failure broadcast did NOT exist — this module adds it on the SAME
    formal_notify / common.send_wx WxPusher channel (kind=strategy_review_failed).
"""
from __future__ import print_function

import json
import os
import shutil
import time
from pathlib import Path

from . import review_lexicon as lex


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _root(vector_root=None):
    return Path(vector_root or os.environ.get("VECTOR_ROOT") or "/root")


def archive_dir(vector_root=None):
    return _root(vector_root) / "strategies" / "archived"


def _wx(text, kind, meta=None):
    """Send via the verified formal notify channel (same as human confirm)."""
    try:
        import auto_trade_formal_notify as notify
        return notify.send_message(str(text or ""), kind=kind, meta=meta or {})
    except Exception as exc:
        return {"ok": False, "sent": False, "error": str(exc)}


def channel_status():
    """Probe the existing WxPusher channel used by human confirm."""
    try:
        import auto_trade_formal_notify as notify
        ch = notify.get_channel()
        return {
            "ok": bool(ch.get("ok")),
            "ready": bool(ch.get("notification_real_channel_ready") or ch.get("ready")),
            "bind_mode": ch.get("bind_mode"),
            "verified_send_mode": ch.get("verified_send_mode"),
            "error": ch.get("error"),
            "human_confirm_kind": "strategy_pending_confirm",
            "failure_kind": "strategy_review_failed",
            "same_channel_as_human_confirm": True,
        }
    except Exception as exc:
        return {
            "ok": False,
            "ready": False,
            "error": str(exc),
            "same_channel_as_human_confirm": True,
        }


def archive_failed_pack(pack, *, reason=None, review_n=None, stop_code=None,
                        workdir=None, vector_root=None, extra=None):
    """Copy failed strategy pack under /root/strategies/archived/ (never live)."""
    root = _root(vector_root)
    dest_dir = archive_dir(root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    pack = pack or {}
    spec = pack.get("mechanism_spec") or {}
    meta = pack.get("meta") or {}
    fam = (
        spec.get("mechanism_family")
        or meta.get("contract_id")
        or meta.get("title")
        or "unknown_family"
    )
    stamp = time.strftime("%Y%m%d_%H%M%S")
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(fam))[:80]
    base = "%s__%s" % (stamp, safe)
    pack_path = dest_dir / ("%s.json" % base)
    meta_path = dest_dir / ("%s.meta.json" % base)
    payload = dict(pack)
    payload.setdefault("meta", {})
    if isinstance(payload["meta"], dict):
        payload["meta"] = dict(payload["meta"])
        payload["meta"]["archived"] = True
        payload["meta"]["archived_at"] = _now()
        payload["meta"]["archive_reason"] = reason
        payload["meta"]["archive_review_n"] = review_n
        payload["meta"]["archive_stop_code"] = stop_code
    pack_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    record = {
        "schema": "qiyu_strategy_review_archive_v1",
        "archived_at": _now(),
        "pack_path": str(pack_path),
        "family": fam,
        "reason": reason,
        "review_n": review_n,
        "review_label": lex.review_label(review_n),
        "stop_code": stop_code,
        "workdir": str(workdir) if workdir else None,
        "extra": extra or {},
        "production_mounted": False,
        "auto_mount": False,
    }
    meta_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    # Best-effort mirror pack_final from workdir
    if workdir:
        try:
            src = Path(workdir) / "pack_final.json"
            if src.exists():
                shutil.copyfile(str(src), str(dest_dir / ("%s.pack_final.json" % base)))
        except Exception:
            pass
    return record


def _strategy_display_name(pack, cfg=None):
    pack = pack or {}
    cfg = cfg or {}
    meta = pack.get("meta") or {}
    spec = pack.get("mechanism_spec") or {}
    dsl = pack.get("dsl") or pack.get("dsl_long") or pack.get("dsl_short") or {}
    name = (
        meta.get("title_zh")
        or meta.get("title")
        or spec.get("mechanism_name")
        or dsl.get("name")
        or dsl.get("key")
        or spec.get("mechanism_family")
        or "未命名策略"
    )
    try:
        import auto_trade_strategy_titles as titles
        return titles.resolve_strategy_name(dsl.get("key"), name) or name
    except Exception:
        return name


def notify_strategy_failure(*, pack=None, cfg=None, reason=None, stop_code=None,
                            review_n=None, core_cause=None, ai_optimize_used=None,
                            archive_record=None, dry_run=False):
    """NEW failure broadcast on the existing WxPusher channel.

    Content: 策略名 + 第几次复核失败 + 核心原因.
    """
    cfg = cfg or {}
    pack = pack or {}
    n = review_n or lex.review_n_from_reason(reason)
    label = lex.review_label(n) or "复核"
    name = _strategy_display_name(pack, cfg)
    symbol = cfg.get("symbol") or (pack.get("meta") or {}).get("symbol") or "—"
    timeframe = cfg.get("timeframe") or (pack.get("meta") or {}).get("timeframe") or "—"
    cause = core_cause or reason or stop_code or "未给出核心原因"
    ai_txt = "—" if ai_optimize_used is None else str(ai_optimize_used)
    arch = ""
    if archive_record:
        arch = "\n归档路径: %s" % (archive_record.get("pack_path") or "—")
    msg = (
        "【策略复核失败·已归档】\n"
        "名称: {name}\n"
        "标的/周期: {symbol} / {timeframe}\n"
        "失败关卡: {label}（第{n}次复核）\n"
        "核心原因: {cause}\n"
        "停止码: {stop}\n"
        "AI优化轮次: {ai}/3\n"
        "production_mounted=False（失败策略永不自动上线）\n"
        "时间: {t}{arch}"
    ).format(
        name=name,
        symbol=symbol,
        timeframe=timeframe,
        label=label,
        n=n if n else "?",
        cause=str(cause)[:280],
        stop=stop_code or "—",
        ai=ai_txt,
        t=_now(),
        arch=arch,
    )
    meta = {
        "strategy_name": name,
        "symbol": symbol,
        "timeframe": timeframe,
        "review_n": n,
        "review_label": label,
        "reason": reason,
        "stop_code": stop_code,
        "core_cause": str(cause)[:280],
        "ai_optimize_used": ai_optimize_used,
        "archive_path": (archive_record or {}).get("pack_path"),
        "production_mounted": False,
        "channel": "auto_trade_formal_notify",
    }
    if dry_run:
        return {"ok": True, "sent": False, "dry_run": True, "message": msg, "meta": meta}
    result = _wx(msg, kind="strategy_review_failed", meta=meta)
    result["message"] = msg
    result["meta"] = meta
    return result


def ensure_human_confirm_on_success(*, result=None, pack=None, cfg=None):
    """Reuse existing human-confirm Wx path when STEP A already queued.

    Prefer pipeline push already done inside run_creation_pipeline_step_a
    (ingest_and_screen → kind=strategy_pending_confirm). If missing, enqueue
    via the same pipeline module — never auto-mount.
    """
    result = result or {}
    pack = pack or {}
    cfg = cfg or {}
    pending = result.get("pending") or {}
    human = result.get("human_confirm_state") or {}
    if pending.get("ok") or human.get("pending_ok") or human.get("awaiting_human"):
        return {
            "ok": True,
            "already_queued": True,
            "key": pending.get("key") or (human.get("push") or {}).get("key"),
            "channel": "strategy_pending_confirm",
            "production_mounted": False,
            "auto_mount": False,
        }
    # Fallback enqueue through the SAME pipeline (same Wx channel).
    try:
        import auto_trade_human_confirm_pipeline as pipe
        dsl = (
            pack.get("dsl")
            or pack.get("dsl_long")
            or pack.get("dsl_short")
            or {}
        )
        dsl = dict(dsl)
        if cfg.get("symbol"):
            dsl["supported_instruments"] = [cfg["symbol"]]
        if cfg.get("timeframe"):
            dsl["timeframe"] = cfg["timeframe"]
        metrics = {
            "symbol": cfg.get("symbol"),
            "timeframe": cfg.get("timeframe"),
            "direction": cfg.get("direction") or dsl.get("direction"),
            "name": _strategy_display_name(pack, cfg),
            "key": dsl.get("key"),
        }
        # Pull incubator metrics from result when present
        gr = result.get("gate_results") or {}
        for k in ("calmar", "payoff", "payoff_ratio", "mean_net", "trades", "win_rate"):
            if result.get(k) is not None:
                metrics[k] = result.get(k)
        if gr.get("calmar") is not None:
            metrics["calmar"] = gr.get("calmar")
        push = pipe.enqueue_for_human(
            {"dsl": dsl, "symbol": cfg.get("symbol"), "timeframe": cfg.get("timeframe")},
            metrics,
            source="auto_driver_review_pipeline",
            ai_review={
                "approved": True,
                "policy": "three_review_pass_await_human",
                "natural_language": (
                    "三次复核均已通过；已接入原人工确认 WxPusher 通道。"
                    "production_mounted=False，需 --confirm 才挂载。"
                ),
                "calmar": metrics.get("calmar"),
                "payoff": metrics.get("payoff") or metrics.get("payoff_ratio"),
            },
        )
        return {
            "ok": bool(push.get("ok")),
            "already_queued": bool(push.get("duplicate")),
            "key": push.get("key"),
            "pushed": push.get("pushed"),
            "channel": "strategy_pending_confirm",
            "production_mounted": False,
            "auto_mount": False,
            "push": push,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "channel": "strategy_pending_confirm",
            "production_mounted": False,
            "auto_mount": False,
        }
