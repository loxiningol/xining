# -*- coding: utf-8 -*-
"""Codex-authored strategy submit → safety check → 3AI theoretical review.

Isolated from the retired AI creation factory. Strategies are developed by
Codex under human instruction, then submitted here for 3AI review before
human confirm.
"""
from __future__ import print_function

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
AUDIT_PATH = AUTO_DIR / "codex_strategy_review_audit.jsonl"
STATE_PATH = AUTO_DIR / "codex_strategy_review_state.json"


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


def _existing_strategy_catalog():
    """Live DSL store + pending queue + active assignments for near-dup checks."""
    catalog = []
    dsl_path = ROOT / "strategy_configs" / "ai_dsl_strategies.json"
    try:
        doc = json.loads(dsl_path.read_text(encoding="utf-8"))
        for row in (doc.get("strategies") or []):
            if isinstance(row, dict) and row.get("key"):
                catalog.append(row)
    except Exception:
        pass
    pending_path = AUTO_DIR / "strategy_pending_human_confirm.json"
    try:
        pend = json.loads(pending_path.read_text(encoding="utf-8"))
        for row in (pend.get("items") or []):
            if not isinstance(row, dict):
                continue
            dsl = row.get("dsl")
            if isinstance(dsl, dict) and dsl.get("key"):
                catalog.append(dsl)
    except Exception:
        pass
    return catalog


def _wx(message, kind="codex_strategy_review", meta=None):
    try:
        import auto_trade_formal_notify as notify
        return notify.send_message(message, kind=kind, meta=meta or {})
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _load_payload(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    if isinstance(data.get("dsl"), dict):
        dsl = data["dsl"]
        meta = {k: v for k, v in data.items() if k != "dsl"}
    else:
        dsl = data
        meta = {}
    return dsl, meta


def _build_evidence(definition, metrics, meta):
    """Evidence for 3AI: must reflect the candidate symbol's real-cost screen."""
    symbol = (metrics.get("symbol")
              or meta.get("symbol")
              or (definition.get("supported_instruments") or [None])[0])
    timeframe = (metrics.get("timeframe")
                 or meta.get("timeframe")
                 or definition.get("timeframe"))
    return {
        "source": "codex_manual",
        "accounting_note": (
            "下列 safety_metrics 已按【该策略标的+周期】在 observed_base 全摩擦"
            "（fee/slippage/spread/impact/latency/funding）+ 固定杠杆/硬止损口径回测；"
            "窗口=最近≤12000根。empirical_win_rate 是该标的真实成本样本胜率，"
            "理论胜率估计应以它为锚，不得无根据地大幅下调。"
        ),
        "thesis": meta.get("thesis") or definition.get("description"),
        "instrument": {
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": definition.get("direction"),
            "bars_used": metrics.get("bars_used"),
            "leverage": metrics.get("leverage"),
            "stop_loss_pct": metrics.get("stop_loss_pct"),
            "friction_rates": metrics.get("friction_rates"),
        },
        "safety_metrics": {
            "symbol": symbol,
            "timeframe": timeframe,
            "trades": metrics.get("trades"),
            "mean_net": metrics.get("mean_net"),
            "win_rate": metrics.get("win_rate"),
            "empirical_win_rate": metrics.get("win_rate"),
            "max_drawdown": metrics.get("max_drawdown"),
            "max_loss_streak": metrics.get("max_loss_streak"),
            "total_return_pct": metrics.get("total_return_pct"),
            "fold_means": metrics.get("fold_means"),
            "fold_meta": metrics.get("fold_meta"),
            "fold_ok_advisory": metrics.get("fold_ok_advisory"),
            "advisory_only": True,
            "statistical_gates_retired": True,
        },
        "logic_brief": {
            "key": definition.get("key"),
            "name": definition.get("name"),
            "direction": definition.get("direction"),
            "timeframe": definition.get("timeframe"),
            "supported_instruments": definition.get("supported_instruments"),
            "entry": definition.get("entry"),
            "exit": definition.get("exit"),
            "max_hold_bars": definition.get("max_hold_bars"),
            "description": definition.get("description"),
        },
        "notes": meta.get("notes"),
        "codex_author": meta.get("author") or "codex",
    }


def submit_codex_strategy(dsl, meta=None, dry_run=False):
    """Safety → 3AI theoretical review → human-confirm queue."""
    import auto_trade_strategy_dsl as dsl_mod
    import auto_trade_human_confirm_pipeline as pipeline
    import auto_trade_ai_consensus as ai

    meta = dict(meta or {})
    try:
        definition = dsl_mod.validate_strategy(dsl)
    except Exception as exc:
        out = {"ok": False, "stage": "validate", "error": str(exc)}
        _append_audit({"event": "submit_fail", "out": out})
        return out

    cand = {
        "dsl": definition,
        "symbol": meta.get("symbol") or (definition.get("supported_instruments") or [None])[0],
        "timeframe": meta.get("timeframe") or definition.get("timeframe"),
        "thesis": meta.get("thesis"),
    }
    ok, metrics, reason = pipeline.safety_screen_candidate(cand)
    if not ok:
        out = {"ok": False, "stage": "safety", "reason": reason, "metrics": metrics,
               "key": definition.get("key")}
        _append_audit({"event": "submit_fail", "out": out})
        if not dry_run:
            shown = definition.get("name") or definition.get("key")
            try:
                import auto_trade_strategy_titles as titles
                shown = titles.resolve_strategy_name(
                    definition.get("key"), definition.get("name"))
            except Exception:
                pass
            _wx(
                "【Codex策略安全校验失败】\n策略: %s\n原因: %s\n时间: %s"
                % (shown, reason, _now()),
                kind="codex_strategy_safety_fail",
                meta={"key": definition.get("key"), "reason": reason},
            )
        return out

    # Hard gate: reject near-duplicate logic (param-only tweaks ≠ creation)
    try:
        catalog = _existing_strategy_catalog()
        dup = dsl_mod.find_near_duplicate(definition, catalog)
    except Exception as exc:
        dup = None
        _append_audit({"event": "near_dup_check_error", "error": str(exc),
                       "key": definition.get("key")})
    if dup:
        out = {
            "ok": False,
            "stage": "near_duplicate",
            "key": definition.get("key"),
            "reason": "near_duplicate_logic:%s" % (dup.get("key") or "?"),
            "duplicate_of": dup,
            "metrics": metrics,
        }
        _append_audit({"event": "submit_fail", "out": out})
        if not dry_run:
            shown = definition.get("name") or definition.get("key")
            other = dup.get("name") or dup.get("key")
            try:
                import auto_trade_strategy_titles as titles
                shown = titles.resolve_strategy_name(
                    definition.get("key"), definition.get("name"))
                other = titles.resolve_strategy_name(
                    dup.get("key"), dup.get("name"))
            except Exception:
                pass
            _wx(
                "【Codex拒绝·近邻重复逻辑】\n"
                "候选: %s\n"
                "近似已有: %s\n"
                "规则: 同结构仅改阈值不算策略创造\n"
                "时间: %s" % (shown, other, _now()),
                kind="codex_strategy_near_dup",
                meta={"key": definition.get("key"),
                      "duplicate_of": dup.get("key")},
            )
        return out

    evidence = _build_evidence(definition, metrics, meta)
    review = ai.theoretical_review_all(definition, evidence)
    out = {
        "ok": bool(review.get("approved")),
        "stage": "ai_theoretical_review",
        "key": definition.get("key"),
        "safety_reason": reason,
        "metrics": metrics,
        "ai_review": {
            "approved": review.get("approved"),
            "ai_theoretical_wr_avg": review.get("ai_theoretical_wr_avg"),
            "ai_theoretical_wr_by_provider": review.get(
                "ai_theoretical_wr_by_provider"),
            "ai_theoretical_mean_net_avg": review.get(
                "ai_theoretical_mean_net_avg"),
            "ai_theoretical_mean_net_by_provider": review.get(
                "ai_theoretical_mean_net_by_provider"),
            "ai_stop_cluster_risk_by_provider": review.get(
                "ai_stop_cluster_risk_by_provider"),
            "fail_reasons": review.get("fail_reasons"),
            "natural_language": review.get("natural_language"),
            "reviews": [
                {"provider": r.get("provider"), "decision": r.get("decision"),
                 "theoretical_win_rate_pct": r.get("theoretical_win_rate_pct"),
                 "theoretical_mean_net_pct": r.get("theoretical_mean_net_pct"),
                 "stop_cluster_risk": r.get("stop_cluster_risk"),
                 "stop_cluster_prob": r.get("stop_cluster_prob"),
                 "reason": r.get("reason"), "ok": r.get("ok")}
                for r in (review.get("reviews") or [])
            ],
        },
        "dry_run": bool(dry_run),
        "time": _now(),
    }

    if dry_run:
        _append_audit({"event": "submit_dry_run", "out": out})
        _atomic(STATE_PATH, out)
        return out

    if not review.get("approved"):
        _append_audit({"event": "ai_reject", "out": out})
        shown = definition.get("name") or definition.get("key")
        try:
            import auto_trade_strategy_titles as titles
            shown = titles.resolve_strategy_name(
                definition.get("key"), definition.get("name"))
        except Exception:
            pass
        _wx(
            "【三AI理论复核未通过】\n"
            "策略: %s\n"
            "均值理论胜率: %s\n"
            "均值理论盈利单盈利率: %s\n"
            "原因: %s\n时间: %s"
            % (shown,
               review.get("ai_theoretical_wr_avg"),
               review.get("ai_theoretical_mean_net_avg"),
               review.get("natural_language") or "; ".join(
                   review.get("fail_reasons") or []),
               _now()),
            kind="codex_strategy_ai_reject",
            meta={"key": definition.get("key"),
                  "avg": review.get("ai_theoretical_wr_avg"),
                  "mean_net_avg": review.get("ai_theoretical_mean_net_avg")},
        )
        _atomic(STATE_PATH, out)
        return out

    queued = pipeline.ingest_and_screen(
        cand, source="codex_manual", ai_review=review, require_ai_review=True)
    out["queued"] = queued
    out["pushed"] = bool(queued.get("pushed") or queued.get("duplicate"))
    _append_audit({"event": "ai_pass_queued", "out": out})
    _atomic(STATE_PATH, out)
    return out


def review_pending_item(key):
    """Re-run 3AI review on an awaiting_confirm item (debug)."""
    import auto_trade_human_confirm_pipeline as pipeline
    import auto_trade_ai_consensus as ai

    pending = pipeline.load_pending()
    item = None
    for row in pending.get("items") or []:
        if row.get("key") == key and row.get("status") == "awaiting_confirm":
            item = row
            break
    if not item:
        return {"ok": False, "error": "not_awaiting_confirm", "key": key}
    dsl = item.get("dsl") or {}
    evidence = _build_evidence(dsl, item.get("metrics") or {}, {
        "thesis": item.get("logic_brief"), "author": "codex_rereview"})
    review = ai.theoretical_review_all(dsl, evidence)
    item["ai_theoretical_wr_by_provider"] = review.get(
        "ai_theoretical_wr_by_provider")
    item["ai_theoretical_wr_avg"] = review.get("ai_theoretical_wr_avg")
    item["ai_stop_cluster_risk_by_provider"] = review.get(
        "ai_stop_cluster_risk_by_provider")
    item["ai_review_natural_language"] = review.get("natural_language")
    item["ai_rereview_at"] = _now()
    if not review.get("approved"):
        item["status"] = "ai_rereview_rejected"
    pipeline.save_pending(pending)
    return {"ok": True, "key": key, "approved": review.get("approved"),
            "ai_theoretical_wr_avg": review.get("ai_theoretical_wr_avg"),
            "natural_language": review.get("natural_language")}


def main():
    parser = argparse.ArgumentParser(
        description="Codex策略提交 → 三AI理论复核 → 人工确认")
    parser.add_argument("--submit", type=str, default="",
                        help="JSON path: DSL or {dsl,thesis,symbol,timeframe}")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--review-pending", type=str, default="",
                        help="Re-review awaiting key")
    args = parser.parse_args()
    if args.submit:
        dsl, meta = _load_payload(args.submit)
        print(json.dumps(
            submit_codex_strategy(dsl, meta=meta, dry_run=args.dry_run),
            ensure_ascii=False, indent=2, default=str))
    elif args.review_pending:
        print(json.dumps(review_pending_item(args.review_pending),
                         ensure_ascii=False, indent=2, default=str))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
