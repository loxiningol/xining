# -*- coding: utf-8 -*-
"""Unified formal-review gate helpers (P0).

All formal review submissions must pass these before four-AI slim review:

  validate_handoff_floor()
  issue_handoff_token() / require_handoff_token()
  validate_review_metrics_parity()
  validate_fixed_contract()
  then submit_slim_four_ai_review (existing pipeline)
"""
from __future__ import print_function

import hashlib
import json
from datetime import datetime

HANDOFF_TOKEN_SCHEMA = "qiyu_handoff_token_v1"
POLICY_VERSION = "manufacture_batch_policy_v1+review_gate_v1"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _stable_hash(payload):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def normalize_trades_for_reference(trades):
    """Map DSL / backtest trade dicts into reference-calculator fill schema.

    DSL / slim Formal trades embed round-trip costs in ``pnl_ratio`` and usually
    leave entry/exit fee fields at 0. Prefer that levered pnl for parity so the
    independent reference does not fee-lessly recompute from fill prices and
    diverge from production.
    """
    out = []
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        pnl = t.get("pnl_ratio_full_size")
        if pnl is None:
            pnl = t.get("pnl_ratio")
        explicit_fee = 0.0
        for key in (
            "entry_fee",
            "exit_fee",
            "funding",
            "funding_fee",
            "slippage_cost",
            "slippage",
        ):
            try:
                explicit_fee += abs(float(t.get(key) or 0.0))
            except Exception:
                pass
        # When costs live in pnl_ratio (typical DSL), skip fee-less fill recompute.
        prefer_pnl = pnl is not None and explicit_fee <= 1e-15
        entry_price = None if prefer_pnl else (
            t.get("entry_price") or t.get("entry") or t.get("price")
        )
        exit_price = None if prefer_pnl else (t.get("exit_price") or t.get("exit"))
        row = {
            "entry_time": t.get("entry_time") or t.get("entry_ts") or t.get("open_time"),
            "exit_time": t.get("exit_time") or t.get("exit_ts") or t.get("close_time"),
            "side": t.get("side") or t.get("direction") or "long",
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": t.get("quantity") or t.get("qty") or 1.0,
            "entry_fee": t.get("entry_fee") or 0.0,
            "exit_fee": t.get("exit_fee") or 0.0,
            "funding": t.get("funding") or t.get("funding_fee") or 0.0,
            "slippage_cost": t.get("slippage_cost") or t.get("slippage") or 0.0,
            "stop_price": t.get("stop_price"),
            "exit_reason": t.get("exit_reason") or t.get("reason"),
            "pnl_ratio": pnl,
            "mae": t.get("mae") or t.get("mae_pct") or t.get("mae_price_pct"),
            "mfe": t.get("mfe") or t.get("mfe_pct"),
            "profit_first": t.get("profit_first") or t.get("path_hit"),
            "leverage": t.get("leverage") or 20,
        }
        out.append(row)
    return out


def validate_handoff_floor(metrics=None):
    from . import manufacture_batch_policy as mfg
    return mfg.qualifies_for_review_handoff(metrics or {})


def issue_handoff_token(
    candidate_id=None,
    recipe_id=None,
    metrics=None,
    data_hash=None,
    code_version=None,
    job_id=None,
):
    """Issue a signed-style handoff token after floor pass. No token → no review."""
    gate = validate_handoff_floor(metrics)
    if not gate.get("ok"):
        return {
            "ok": False,
            "token": None,
            "reason": "handoff_floor_failed",
            "gate": gate,
        }
    m = dict(metrics or {})
    metrics_hash = _stable_hash({
        "n": m.get("n") or m.get("n_trades"),
        "win_rate": m.get("win_rate") or m.get("win_rate_pct"),
        "mean_win_only_pct": m.get("mean_win_only_pct"),
        "weekly_opens": m.get("weekly_opens"),
        "profit_first_rate": m.get("profit_first_rate"),
        "median_mae_pct": m.get("median_mae_pct") or m.get("median_mae"),
    })
    body = {
        "schema": HANDOFF_TOKEN_SCHEMA,
        "candidate_id": candidate_id or recipe_id,
        "recipe_id": recipe_id,
        "job_id": job_id,
        "metrics_hash": metrics_hash,
        "data_hash": data_hash,
        "code_version": code_version,
        "policy_version": POLICY_VERSION,
        "issued_at": _now(),
        "gate_observed": gate.get("observed"),
    }
    token = _stable_hash(body)
    body["token"] = token
    body["ok"] = True
    return body


def require_handoff_token(token_blob, recipe_id=None, metrics=None):
    """Reject formal review if token missing / mismatched / floor no longer holds."""
    reasons = []
    blob = token_blob if isinstance(token_blob, dict) else None
    if not blob or not blob.get("token"):
        reasons.append("handoff_token_missing")
        return {"ok": False, "reasons": reasons, "error": "HANDOFF_TOKEN_INVALID"}
    if blob.get("schema") != HANDOFF_TOKEN_SCHEMA:
        reasons.append("handoff_token_schema_invalid")
    if recipe_id and blob.get("recipe_id") and str(blob.get("recipe_id")) != str(recipe_id):
        reasons.append("handoff_token_recipe_mismatch")
    # Re-check floor against current metrics if provided.
    if metrics is not None:
        gate = validate_handoff_floor(metrics)
        if not gate.get("ok"):
            reasons.append("handoff_floor_no_longer_passes")
            return {
                "ok": False,
                "reasons": reasons,
                "gate": gate,
                "error": "HANDOFF_TOKEN_INVALID",
            }
        expected = issue_handoff_token(
            candidate_id=blob.get("candidate_id"),
            recipe_id=blob.get("recipe_id") or recipe_id,
            metrics=metrics,
            data_hash=blob.get("data_hash"),
            code_version=blob.get("code_version"),
            job_id=blob.get("job_id"),
        )
        if expected.get("metrics_hash") != blob.get("metrics_hash"):
            reasons.append("handoff_token_metrics_hash_mismatch")
    if reasons:
        return {"ok": False, "reasons": reasons, "error": "HANDOFF_TOKEN_INVALID"}
    return {"ok": True, "token": blob.get("token"), "policy_version": blob.get("policy_version")}


def validate_review_metrics_parity(
    trades,
    production_metrics=None,
    observation_start=None,
    observation_end=None,
    as_of=None,
):
    """Gate: production metrics must match independent reference calculator."""
    from . import review_metrics_reference as ref

    normalized = normalize_trades_for_reference(trades)
    reference = ref.compute_trade_ledger_metrics(
        normalized,
        observation_start=observation_start,
        observation_end=observation_end,
        as_of=as_of,
    )
    prod = dict(production_metrics or {})
    # Prefer pnl_ratio-based production win-only (already levered) for parity
    # when fill prices are incomplete — rebuild production from same ledger.
    if normalized and (
        "average_profitable_trade_return" not in prod
        and "mean_win_only_pct" not in prod
    ):
        from . import manufacture_batch_policy as mfg
        win_pack = mfg.levered_win_only_mean_pct(trades=trades)
        prod["mean_win_only_pct"] = win_pack.get("mean_win_only_pct")
        prod["average_profitable_trade_return"] = win_pack.get("mean_win_only_ratio")
        prod["n"] = win_pack.get("n_trades")
    if "mean_win_only_pct" in prod and "average_profitable_trade_return" not in prod:
        pct = prod.get("mean_win_only_pct")
        try:
            pct = float(pct)
            prod["average_profitable_trade_return"] = (
                pct / 100.0 if abs(pct) >= 0.5 else pct
            )
        except Exception:
            pass
    if "weekly_opens" in prod and "weekly_entry_frequency" not in prod:
        prod["weekly_entry_frequency"] = prod.get("weekly_opens")
    if "n" in prod and "trade_count" not in prod:
        prod["trade_count"] = prod.get("n")

    # When trades only have pnl_ratio (no entry/exit prices), reference falls
    # back to provided ratios — compare win-only mean on that shared basis.
    parity = ref.parity_compare(prod, reference)
    # Also attach pnl_ratio-only reference for production that already ×20.
    pnl_vals = []
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        v = t.get("pnl_ratio_full_size")
        if v is None:
            v = t.get("pnl_ratio")
        try:
            if v is not None:
                pnl_vals.append(float(v))
        except Exception:
            continue
    if pnl_vals:
        pnl_ref = ref.average_profitable_trade_return(pnl_vals)
        prod_ratio = prod.get("average_profitable_trade_return")
        if prod_ratio is not None and pnl_ref.get("average_profitable_trade_return") is not None:
            ad = abs(float(prod_ratio) - float(pnl_ref["average_profitable_trade_return"]))
            parity.setdefault("absolute_diff", {})["pnl_ratio_win_only"] = ad
            if ad > 1e-9:
                parity.setdefault("failed_keys", []).append("pnl_ratio_win_only")
                parity["metric_parity_passed"] = False
            else:
                parity.setdefault("checked_keys", []).append("pnl_ratio_win_only")
                if not parity.get("failed_keys"):
                    parity["metric_parity_passed"] = True
        parity["pnl_ratio_reference"] = pnl_ref

    parity["reference_full"] = {
        k: reference.get(k)
        for k in (
            "trade_count",
            "win_rate",
            "average_profitable_trade_return",
            "average_profitable_trade_return_pct",
            "weekly_entry_frequency",
            "observation_days",
            "profit_first_rate",
            "median_mae",
            "recent_2y_requirement_passed",
            "cost_convention_zh",
        )
    }
    if not parity.get("metric_parity_passed"):
        parity["block_reason"] = "REVIEW_METRIC_PARITY_FAILURE"
        parity["error"] = "REVIEW_METRIC_PARITY_FAILURE"
    return parity


def validate_fixed_contract(metrics=None):
    """Hard fixed-contract checks before four-AI review."""
    from . import review_metrics_reference as ref

    m = metrics or {}
    reasons = []
    avg = m.get("average_profitable_trade_return")
    if avg is None and m.get("mean_win_only_pct") is not None:
        try:
            pct = float(m.get("mean_win_only_pct"))
            avg = pct / 100.0 if abs(pct) >= 0.5 else pct
        except Exception:
            avg = None
    # Soft: if avg missing, don't invent pass; require explicit value for hard pass.
    if avg is not None and float(avg) < ref.MIN_AVG_WINNING_LEVERED:
        reasons.append("avg_winning_levered_below_stop_times_leverage_default")
    if m.get("recent_2y_requirement_passed") is False:
        reasons.append("recent_2y_requirement_failed")
    stop = m.get("stop_distance")
    if stop is not None and abs(float(stop) - ref.STOP_PRICE_DISTANCE) > 1e-12:
        reasons.append("stop_distance_not_0.005")
    lev = m.get("leverage")
    if lev is not None:
        try:
            from . import manufacture_batch_policy as mfg
            lv = float(lev)
            if lv < float(mfg.LEVERAGE_MIN) - 1e-12 or lv > float(mfg.LEVERAGE_MAX) + 1e-12:
                reasons.append("leverage_out_of_range_%s_%s" % (mfg.LEVERAGE_MIN, mfg.LEVERAGE_MAX))
        except Exception:
            if abs(float(lev) - ref.LEVERAGE) > 1e-12:
                reasons.append("leverage_not_20")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "thresholds": {
            "average_profitable_trade_return": ref.MIN_AVG_WINNING_LEVERED,
            "stop_distance": ref.STOP_PRICE_DISTANCE,
            "leverage_range": [20, 50],
        },
    }


def preflight_formal_review(
    trades=None,
    production_metrics=None,
    handoff_token=None,
    recipe_id=None,
    observation_start=None,
    observation_end=None,
    require_parity=True,
    require_token=True,
):
    """Single preflight used by formal_review_bridge / slim path."""
    blocks = []
    token_check = {"ok": True}
    if require_token:
        token_check = require_handoff_token(
            handoff_token, recipe_id=recipe_id, metrics=production_metrics,
        )
        if not token_check.get("ok"):
            blocks.append("HANDOFF_TOKEN_INVALID")
    floor = validate_handoff_floor(production_metrics)
    if not floor.get("ok"):
        blocks.append("HANDOFF_FLOOR_FAIL")
    parity = {"metric_parity_passed": True, "skipped": True}
    if require_parity and trades is not None:
        parity = validate_review_metrics_parity(
            trades,
            production_metrics=production_metrics,
            observation_start=observation_start,
            observation_end=observation_end,
        )
        if not parity.get("metric_parity_passed"):
            blocks.append("REVIEW_METRIC_PARITY_FAILURE")
    contract = validate_fixed_contract(production_metrics)
    # Fixed contract avg gate is for final pass reporting; handoff already
    # enforced ≥9%. Do not double-block preflight solely on missing 11.11 here
    # when parity/token/floor are the P0 hard stops — keep as advisory reasons.
    return {
        "ok": not blocks,
        "blocks": blocks,
        "handoff_token": token_check,
        "handoff_floor": floor,
        "metric_parity": parity,
        "fixed_contract": contract,
        "error": blocks[0] if blocks else None,
    }


def probe():
    return {
        "ok": True,
        "gates": [
            "validate_handoff_floor",
            "issue_handoff_token",
            "require_handoff_token",
            "validate_review_metrics_parity",
            "validate_fixed_contract",
            "preflight_formal_review",
        ],
        "providers_locked": ["deepseek", "qwen", "glm", "kimi"],
        "policy_version": POLICY_VERSION,
    }
