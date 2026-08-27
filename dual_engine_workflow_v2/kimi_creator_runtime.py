# -*- coding: utf-8 -*-
"""Kimi-led collaborative creator runtime.

Kimi owns every final strategy decision. Research collaborators may analyse
read-only evidence and propose falsifiable options. Local code exposes facts,
validates syntax, executes the submitted identity and applies the deterministic
10-metric IS quality gate plus structure and blind OOS. It never ranks, selects
or submits strategy logic.
"""
from __future__ import print_function

import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request

from .creation_timeframe_policy import public_policy, timeframe_role
from datetime import datetime
from pathlib import Path

from .kimi_provider import kimi_transport_backoff_seconds as _transport_backoff_seconds
from .blind_oos_policy import (
    holdout_rejected_sandbox_evidence,
    oos_lineage_root_id,
    should_restart_mechanism_after_oos,
)
from .creator_resource_budget import (
    MAX_MECHANISM_GENERATIONS,
    MAX_OOS_IDENTITY_TESTS,
    job_time_budget_seconds as _job_time_budget_seconds,
    kimi_progress_detail as _kimi_progress_detail,
)
from .process_safe_state import atomic_write_json


SCHEMA = "qiyu_kimi_collaborative_creator_runtime_v5"
GUARANTEED_OPTIMIZATION_RETRIES = 3
MAX_CONDITIONAL_EXTRA_RETRIES = 2
# Backward-compatible public name: this is the guaranteed budget, not a hard
# ceiling.  Evidence-qualified strategies may receive up to two extra retries.
MAX_OPTIMIZATION_RETRIES = GUARANTEED_OPTIMIZATION_RETRIES
MAX_QUALITY_EVALUATIONS_PER_STRATEGY = (
    1 + GUARANTEED_OPTIMIZATION_RETRIES + MAX_CONDITIONAL_EXTRA_RETRIES
)
# Provider transport/capacity failures are not strategy-quality attempts.  Give
# a temporarily saturated endpoint enough bounded recovery chances without
# consuming the creator's formal optimisation allowance.
MAX_TECHNICAL_REPAIRS_PER_ATTEMPT = 4
# Historical modes remain executable for old recipes outside this creator.
# New Kimi candidates use the continuous exit_plan contract exclusively.
ALLOWED_TAKE_PROFIT_MODES = ()

RETRYABLE_TRANSPORT_MARKERS = (
    "429", "timeout", "timed out", "temporarily", "urlerror",
    "connection reset", "service unavailable", "502", "503", "504",
    "500", "internal server", "bad_response_body",
    "401", "unauthorized", "403", "kimi_empty_content",
    "kimi_json_parse_failed", "bad gateway", "gateway time",
    "tpm", "too many", "rate limit", "rate_limit",
    "请求过于频繁", "并发已达上限",
)


def _transport_retryable(error_text):
    text = str(error_text or "").lower()
    # Gateway wraps both weekly quota and TPM 429 as 额度/并发已达上限.
    # Rate-limit / TPM must retry and let kimi_post_json fail over first.
    if any(marker in text for marker in (
        "429", "too many", "rate limit", "rate_limit", "tpm",
        "请求过于频繁", "并发已达上限",
    )):
        return True
    # Remaining account/entitlement failures are not transient transport faults.
    if any(marker in text for marker in (
        "weekly usage limit reached", "insufficient balance",
        "creditsError".lower(), "committing fraud", "has been blocked",
        "account has found to be",
    )):
        return False
    return any(marker in text for marker in RETRYABLE_TRANSPORT_MARKERS)


def _message_text(message):
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item or ""))
        content = "".join(parts)
    content = str(content or "").strip()
    if content:
        return content
    for key in ("reasoning_content", "reasoning", "output_text"):
        value = str(message.get(key) or "").strip()
        if value:
            return value
    return ""


def _parse_model_json(content):
    text = str(content or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3].rstrip()
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    parsed = json.loads(text)
    return parsed


def _looks_like_strategy_object(row):
    if not isinstance(row, dict):
        return False
    return bool(
        row.get("entry_ast")
        or row.get("hypothesis_id")
        or row.get("protective_stop_pct")
        or row.get("exit_plan")
        or row.get("candidate_id")
    )


def _unwrap_provider_object(parsed):
    if isinstance(parsed, list):
        return parsed
    if not isinstance(parsed, dict):
        return parsed
    inner = parsed.get("candidate")
    if (
        isinstance(inner, dict)
        and _looks_like_strategy_object(inner)
        and parsed.get("status") not in ("candidate", "blocked", "hypotheses")
    ):
        return inner
    return parsed


def _extract_sandbox_hypotheses(raw):
    meta = {"top_keys": [], "source": None, "got_status": None}
    if isinstance(raw, list):
        rows = [row for row in raw if isinstance(row, dict)]
        meta["source"] = "root_list"
        return rows, meta
    if not isinstance(raw, dict):
        meta["got_type"] = type(raw).__name__
        return [], meta
    meta["top_keys"] = sorted(raw.keys())
    meta["got_status"] = raw.get("status")
    for key in ("hypotheses", "hypothesis", "directions", "candidates"):
        value = raw.get(key)
        if isinstance(value, list) and value:
            meta["source"] = key
            return [row for row in value if isinstance(row, dict)], meta
        if isinstance(value, dict) and _looks_like_strategy_object(value):
            meta["source"] = key + "_object"
            return [value], meta
    nested = raw.get("candidate")
    if isinstance(nested, (dict, list)):
        inner, inner_meta = _extract_sandbox_hypotheses(nested)
        if inner:
            inner_meta["source"] = "candidate." + str(inner_meta.get("source") or "nested")
            inner_meta["top_keys"] = meta["top_keys"]
            return inner, inner_meta
    if _looks_like_strategy_object(raw):
        row = dict(raw)
        if not str(row.get("hypothesis_id") or "").strip():
            row["hypothesis_id"] = str(
                row.get("candidate_id") or row.get("title_zh") or "sandbox_single"
            )
        meta["source"] = "single_object_promoted"
        return [row], meta
    return [], meta


def _compact_received_shape(raw, meta=None):
    payload = dict(meta or {})
    if isinstance(raw, dict):
        payload["top_keys"] = payload.get("top_keys") or sorted(raw.keys())
        payload["got_status"] = raw.get("status")
    elif isinstance(raw, list):
        payload["got_type"] = "list"
        payload["list_len"] = len(raw)
    else:
        payload["got_type"] = type(raw).__name__
    try:
        payload["raw_prefix"] = json.dumps(raw, ensure_ascii=False, default=str)[:800]
    except Exception:
        payload["raw_prefix"] = str(raw)[:800]
    return payload


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def _report_progress(stage, detail, percent, extras=None):
    """Best-effort queue progress; never affects creation or attempt counts."""
    path = str(os.environ.get("QIYU_JOB_PROGRESS_PATH") or "").strip()
    if not path:
        return
    try:
        from .parallel_creation import update_job_progress
        update_job_progress(path, stage, detail=detail, percent=percent, extras=extras)
    except Exception:
        return


def _load_ai_env():
    path = _root() / "auto_trade" / "ai_ecosystem.env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and not str(os.environ.get(key) or "").strip():
            os.environ[key] = value


def _finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _protective_stop_pct(value):
    """Normalize one explicit continuous price-stop fraction.

    There is deliberately no platform default and no discrete menu.  The only
    limits are technical price validity: finite, strictly positive and below a
    100% adverse price move.
    """
    value = _finite(value)
    if value is None or value <= 0.0 or value >= 1.0:
        return None
    return float(value)


def _factor_summary(values, maximum_quantile_sample=30000):
    finite = [float(v) for v in (values or []) if _finite(v) is not None]
    n_total = len(values or [])
    if not finite:
        return {
            "available": False, "n": 0, "missing": n_total,
            "missing_rate": 1.0 if n_total else None,
        }
    n = len(finite)
    mean = sum(finite) / float(n)
    variance = sum((v - mean) ** 2 for v in finite) / float(max(1, n - 1))
    step = max(1, int(math.ceil(n / float(maximum_quantile_sample))))
    sample = sorted(finite[::step])

    def quantile(q):
        if not sample:
            return None
        idx = int(round((len(sample) - 1) * float(q)))
        return sample[max(0, min(len(sample) - 1, idx))]

    return {
        "available": True,
        "n": n,
        "missing": max(0, n_total - n),
        "missing_rate": (max(0, n_total - n) / float(n_total)) if n_total else 0.0,
        "min": min(finite), "max": max(finite),
        "mean": mean, "std": math.sqrt(max(variance, 0.0)),
        "quantiles": {
            "p05": quantile(0.05), "p10": quantile(0.10),
            "p25": quantile(0.25), "p50": quantile(0.50),
            "p75": quantile(0.75), "p90": quantile(0.90),
            "p95": quantile(0.95),
        },
        "quantile_sample_n": len(sample),
    }


def _ts(value):
    try:
        raw = float(value)
        if raw > 10 ** 12:
            raw /= 1000.0
        return datetime.fromtimestamp(raw).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _data_fingerprint(candles, symbol, timeframe):
    if not candles:
        return None
    payload = {
        "symbol": symbol, "timeframe": timeframe, "n": len(candles),
        "start": candles[0].get("ts"), "end": candles[-1].get("ts"),
        "first_close": candles[0].get("close"), "last_close": candles[-1].get("close"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]


def _data_span_days(candles):
    rows = list(candles or [])
    if len(rows) < 2:
        return 0.0
    values = []
    for row in (rows[0], rows[-1]):
        raw = (row or {}).get("ts")
        if raw is None:
            raw = (row or {}).get("timestamp")
        try:
            raw = float(raw)
            if raw > 1e12:
                raw /= 1000.0
            values.append(raw)
        except Exception:
            pass
    if len(values) == 2 and values[1] > values[0]:
        return (values[1] - values[0]) / 86400.0
    return 0.0


def _creator_fail_lessons():
    try:
        from .quality_author import recent_fail_lessons
        rows = recent_fail_lessons(limit=12)
    except Exception:
        rows = []
    return {
        "note_zh": (
            "这是质检失败记忆，不是绿红截断栏。"
            "不要复制超卖/抄底/反转/rsi-z 振荡器。赔率型不是最大化目标。"
        ),
        "recent_failures": rows,
    }


def _creator_learning_slice():
    empty_library = {
        "not_a_gate": True,
        "not_a_research_direction_ban": True,
        "clusters": [],
        "note_zh": "课包为空时只使用静态趋势识字。不能放行。",
    }
    empty_order = {
        "not_a_gate": True,
        "quality_gate_unchanged": True,
        "items": [],
        "note_zh": "检查顺序为空。10项门槛不变。",
    }
    try:
        from .creation_learning_pack import public_creator_library
        lib = public_creator_library() or {}
    except Exception:
        lib = {}
    library = lib.get("case_library") if isinstance(lib.get("case_library"), dict) else empty_library
    order = lib.get("advisory_check_order") if isinstance(lib.get("advisory_check_order"), dict) else empty_order
    library.setdefault("not_a_gate", True)
    order.setdefault("not_a_gate", True)
    return {
        "case_library": library,
        "advisory_check_order": order,
        "success_few_shot": list(lib.get("success_few_shot") or []),
        "live_family_paths": lib.get("live_family_paths") or {
            "occupancy_geo_excluded": True, "by_family": {}, "not_a_gate": True,
        },
    }


def _creator_case_library():
    return _creator_learning_slice().get("case_library")


def _creator_check_order():
    return _creator_learning_slice().get("advisory_check_order")


def build_creator_tool_bundle(symbol, timeframe, live_parent=None):
    """Build the authoritative read-only context Kimi receives before attempt 1."""
    from . import ast_compiler
    from . import easyquant_bridge as eq
    from .cognitive_designer import _load_candles_bundle
    from .quality_gate import quality_gate_10_thresholds, quality_gate_oos_thresholds
    from .exit_dsl import public_exit_grammar
    from .entry_structure_literacy import public_creator_brief as trend_literacy_brief

    # A creation instruction owns one immutable factor-matrix cache.  Clear
    # stale matrices from older jobs, then let the distribution tool and all
    # three candidate replays share the matrix built below.
    eq.clear_factor_matrix_cache()
    loaded = _load_candles_bundle(symbol, timeframe, mode="FULL")
    if not loaded.get("ok"):
        return {"ok": False, "error": loaded.get("error") or "data_load_failed", "detail": loaded}
    full_candles = list(loaded.get("candles") or [])
    if not full_candles:
        return {"ok": False, "error": "candles_empty", "detail": loaded}
    # The last chronological 30% is never exposed to Kimi, GLM, feasibility
    # probes, counterfactuals or retries.  It is read once only after the exact
    # candidate identity passes all IS checks.
    split_index = int(len(full_candles) * 0.70)
    split_index = max(240, min(len(full_candles) - 64, split_index))
    if split_index <= 0 or split_index >= len(full_candles):
        return {
            "ok": False, "error": "blind_oos_split_not_possible",
            "detail": {"n_bars": len(full_candles), "required_minimum": 304},
        }
    candles = full_candles[:split_index]
    matrix = eq._build_factor_matrix(candles, symbol=symbol, timeframe=timeframe)
    from .higher_timeframe_context import build as build_higher_timeframe_context
    higher_timeframe = build_higher_timeframe_context(candles, timeframe)
    higher_matrix = dict(higher_timeframe.get("matrix") or {})
    matrix.update(higher_matrix)
    formal = ast_compiler.formal_feature_registry()
    from . import recipe_policy
    semantics = dict(getattr(recipe_policy, "GENERIC_FACTOR_SEMANTICS", {}) or {})
    distributions = {}
    for name in formal:
        distributions[name] = _factor_summary(matrix.get(name) or [])
        distributions[name]["semantics"] = semantics.get(name)
    learning = _creator_learning_slice()
    bundle = {
        "ok": True,
        "schema": "qiyu_kimi_creator_tool_bundle_v2_blind_oos",
        "immutable_creator_role": "Kimi K3 alone authors and decides the final strategy logic",
        "research_collaboration_role": (
            "configured AIs may analyse read-only evidence and propose options; "
            "they cannot vote, gate, submit or deploy"
        ),
        "tools_are_read_only": True,
        "timeframe_creation_policy": public_policy(),
        "selected_timeframe_role": timeframe_role(timeframe),
        "data_scope": {
            "symbol": symbol, "timeframe": timeframe, "n_bars": len(candles),
            "start": _ts(candles[0].get("ts")), "end": _ts(candles[-1].get("ts")),
            "span_days": _data_span_days(candles),
            "data_fingerprint": _data_fingerprint(candles, symbol, timeframe),
            "volume_is_proxy": bool(loaded.get("volume_is_proxy")),
            "known_absences": [
                "historical L2/order book", "order-flow imbalance",
                "open interest", "funding history", "liquidations", "basis",
            ],
            "blind_oos_protocol": {
                "development_fraction": 0.70,
                "reserved_oos_fraction": 0.30,
                "reserved_oos_values_visible_to_creator": False,
                "candidate_frozen_before_oos": True,
                "oos_read_maximum": 1,
                "oos_failure_feedback_to_creator": False,
                "oos_read_is_per_frozen_identity": True,
            },
        },
        "formal_factor_manifest": distributions,
        "formal_factor_names": formal,
        "higher_timeframe_environment_evidence": {
            "base_timeframe": higher_timeframe.get("base_timeframe"),
            "higher_timeframe_minutes": higher_timeframe.get("higher_timeframe_minutes"),
            "causal_alignment": higher_timeframe.get("causal_alignment"),
            "descriptive_only_not_a_formal_entry_factor": True,
            "factor_summaries": {
                name: _factor_summary(values)
                for name, values in sorted(higher_matrix.items())
            },
        },
        "research_only_factors_hidden_from_creator": True,
        "trend_literacy": trend_literacy_brief(),
        "quality_fail_lessons": _creator_fail_lessons(),
        "case_library": learning.get("case_library"),
        "advisory_check_order": learning.get("advisory_check_order"),
        "success_few_shot": learning.get("success_few_shot"),
        "live_family_paths": learning.get("live_family_paths"),
        "entry_ast_grammar": {
            "boolean": ["all", "any", "not"],
            "leaves": ["compare", "quantile", "delta", "slope", "rate_of_change"],
            "compare_ops": [
                "lt", "lte", "gt", "gte", "eq", "between",
                "cross_above", "cross_below",
            ],
            "quantile": {
                "fields": ["type=quantile", "feature", "side=high|low", "q", "window", "min_history"],
                "q_rule": "0.5 < q < 1.0",
                "default_window": 240,
                "default_min_history": 80,
            },
            "max_depth": 6,
            "important": "Only features in formal_factor_names are executable.",
            "exact_examples": {
                "single_compare": {
                    "type": "compare", "feature": "donchian20_long_break",
                    "op": "gt", "value": 0.0,
                },
                "all_conditions": {
                    "type": "all",
                    "children": [
                        {"type": "compare", "feature": "donchian20_long_break", "op": "gt", "value": 0.0},
                        {"type": "compare", "feature": "trend_bias_50_200", "op": "gt", "value": 0.0},
                    ],
                },
                "relative_features": {
                    "type": "compare", "feature": "ema_12", "op": "cross_above",
                    "right_feature": "ema_26",
                },
                "rolling_quantile": {
                    "type": "quantile", "feature": "volume_z", "side": "high",
                    "q": 0.8, "window": 240, "min_history": 80,
                },
                "delta_or_slope": {
                    "type": "slope", "feature": "trend_bias_50_200", "bars": 3,
                    "op": "gt", "value": 0.0,
                },
            },
            "non_empty_rule": "all/any must contain at least one complete child object; entry_ast itself must be an object, never text or null",
        },
        "exit_grammar": {
            "protective_stop_price_pct": {
                "required_on_every_candidate": True,
                "continuous_numeric_value": True,
                "unit": "unleveraged adverse price fraction; multiply by 100 for percent",
                "technical_validity_only": "finite and 0 < protective_stop_pct < 1",
                "platform_default": None,
                "preset_menu": None,
                "creator_rule": (
                    "Kimi must author the value from the strategy mechanism, observed "
                    "volatility and read-only evidence; local code never chooses it"
                ),
            },
            "continuous_composable_exit_plan": public_exit_grammar(),
            "legacy_take_profit_menu_available_to_new_creator": False,
            "indicator_conditions": (
                "embed a formal market AST inside exit_plan.conditional_exits[].when"
            ),
            "entry_execution": "signal on closed bar; enter at next bar open",
        },
        "formal_retry_policy": {
            "base_evaluation": 1,
            "guaranteed_optimization_retries": GUARANTEED_OPTIMIZATION_RETRIES,
            "conditional_extra_retries_maximum": MAX_CONDITIONAL_EXTRA_RETRIES,
            "absolute_quality_evaluation_maximum": MAX_QUALITY_EVALUATIONS_PER_STRATEGY,
            "conditional_extension_is_machine_evidence_budget_only": True,
            "quality_gate_unchanged": True,
        },
        "return_and_cost_semantics": {
            "backtest_trade_return": "unleveraged price return before friction",
            "quality_E_raw": "mean(trade_return - 2*BASE_FRICTION)",
            "BASE_FRICTION_per_side": 0.0008,
            "stress_E": "mean(trade_return - 2*stress_friction_rate)",
            "stress_friction_rate_per_side": 0.0016,
            "no_probe_metric_is_used_for_final_decision": True,
        },
        "quality_gate": {
            "authority": "single quality_gate.check_full: IS10 + structure + blind OOS Sharpe",
            "is_numeric_dimensions": 10,
            "is_thresholds": quality_gate_10_thresholds(),
            "thresholds": quality_gate_10_thresholds(),
            "is_stressed_expectancy_diagnostic_only": True,
            "structural_check": "R*win_rate > 1-win_rate",
            "trend_literacy_veto": (
                "oscillator mean-reversion that cannot read trend direction "
                "or magnitude is rejected before IS numbers; numeric thresholds unchanged"
            ),
            "oos_numeric_dimensions": 1,
            "oos_diagnostics_not_used_for_pass_fail": [
                "sharpe_retention_ratio", "stressed_expectancy",
            ],
            "oos_thresholds": quality_gate_oos_thresholds(),
            "no_other_review_or_ai_judge": True,
            "quality_gate_unchanged_numeric_thresholds": True,
        },
        "_candles": candles,
        "_factor_matrix": matrix,
        "_blind_oos_candles": full_candles,
        "_blind_oos_start_index": split_index,
        "_full_data_fingerprint": _data_fingerprint(full_candles, symbol, timeframe),
        "_blind_oos_split_id": "chronological_70_30_%s" % split_index,
    }
    if live_parent:
        bundle["live_parent"] = live_parent
    return bundle


def _public_tool_bundle(bundle):
    return {key: value for key, value in (bundle or {}).items() if not key.startswith("_")}


def _kimi_call(system_prompt, payload, max_tokens=12000, temperature=0.55):
    _load_ai_env()
    from .kimi_provider import kimi_post_json
    model = str(os.environ.get("QIYU_KIMI_MODEL") or "kimi-k3").strip()
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "response_format": {"type": "json_object"},
    }
    exclude = []
    last = {
        "ok": False,
        "error": "kimi_provider_failed",
    }
    for _attempt in range(2):
        posted = kimi_post_json(body, timeout=540, exclude=exclude)
        last = {
            "ok": False,
            "error": posted.get("error") or "kimi_provider_failed",
            "kimi_endpoint": posted.get("endpoint"),
            "kimi_endpoints_used": posted.get("used"),
        }
        if not posted.get("ok"):
            return last
        raw = posted.get("raw") or {}
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        content = _message_text(message)
        if not content:
            last = {
                "ok": False, "error": "kimi_empty_content",
                "request_id": raw.get("id"), "usage": raw.get("usage"),
                "kimi_endpoint": posted.get("endpoint"),
                "kimi_endpoints_used": posted.get("used"),
            }
        else:
            try:
                parsed = _unwrap_provider_object(_parse_model_json(content))
                return {
                    "ok": True, "candidate": parsed,
                    "request_id": raw.get("id"), "model": model, "usage": raw.get("usage"),
                    "kimi_endpoint": posted.get("endpoint"),
                    "kimi_endpoints_used": posted.get("used"),
                }
            except Exception:
                last = {
                    "ok": False, "error": "kimi_json_parse_failed",
                    "raw_content": content[:4000], "request_id": raw.get("id"),
                    "kimi_endpoint": posted.get("endpoint"),
                    "kimi_endpoints_used": posted.get("used"),
                }
        used_name = posted.get("endpoint")
        if used_name and used_name not in exclude:
            exclude.append(used_name)
            continue
        break
    return last

def _provider_call_with_bounded_transport_retries(
        provider_call, system_prompt, payload, max_tokens, temperature,
        maximum_retries=2):
    """Retry transport/capacity failures without spending a formal evaluation."""
    history = []
    response = None
    for repair_number in range(int(maximum_retries) + 1):
        response = provider_call(
            system_prompt, payload,
            max_tokens=max_tokens, temperature=temperature,
        )
        error_text = str((response or {}).get("error") or "")
        retryable = _transport_retryable(error_text)
        delay = (
            _transport_backoff_seconds(error_text, repair_number)
            if not (response or {}).get("ok") and retryable
            and repair_number < int(maximum_retries) else None
        )
        history.append({
            "repair_number": repair_number,
            "ok": bool((response or {}).get("ok")),
            "request_id": (response or {}).get("request_id"),
            "error": (response or {}).get("error"),
            "retryable_transport_failure": bool(retryable),
            "bounded_backoff_seconds": delay,
            "counted_as_formal_submission": False,
        })
        if (response or {}).get("ok") or not retryable or delay is None:
            break
        time.sleep(delay)
    return response or {"ok": False, "error": "empty_provider_response"}, history


def _creator_system_prompt():
    return (
        "你是Kimi K3，也是本系统最终策略身份、因果机制与AST的唯一作者和决定者。"
        "其他独立研究合作者会基于同一份只读证据提出可证伪选项，但它们没有投票、质检、提交或部署权；"
        "你必须逐项独立取舍，可以采纳或拒绝。所有本地模块只提供真实数据、因子分布、可执行语法、"
        "逐笔诊断、反事实回放和IS 10项质检结果；它们不会替你选择、改写或提交策略。"
        "你必须独立提出一个机制清楚、可证伪、严格可执行的策略实例。不要引用未提供的数据，"
        "不要声称未经回测的胜率、盈亏比、收益或交易次数。基础草案必须先完成因果链自检："
        "避免依赖极少数巨额盈利覆盖大量失败的彩票式结构，避免堆叠高度相关或互斥条件，"
        "并根据已提供的因子分布、摩擦成本和目标周期波动设计有机会覆盖成本的退出路径。"
        "每个候选都必须由你明确给出protective_stop_pct连续数值；系统没有默认止损、没有档位菜单，"
        "也不会替你选择。该值是未加杠杆的逆向价格比例，以小数数值明确表达。"
        "止损值必须来自机制、波动分布与反事实证据，而不是随意复制某个历史最优小数。"
        "你还必须完整编写exit_plan：止盈百分比或ATR倍数、追踪、保本、分段退出、持仓时间、"
        "浮盈与指标条件都可使用任意连续数值并自由组合；本地确定性执行器只执行，不替你挑值。"
        "首次正式质检前的收益可行性报告会给出最低毛收益、ATR可达性和止损×止盈盈亏平衡面。"
        "若报告显示当前收益几何需要不现实的胜率，你应先重构退出或机制，不能浪费正式次数。"
        "这只是创造先验，不是额外质检门。你和研究合作者只能看到按时间顺序切分后的前70%开发样本；"
        "后30%是候选身份冻结后只读一次的盲测，OOS失败不会把数据反馈给你重试。"
        "每个正式策略身份先输出基础策略；如果IS质检失败，后续保证三次调用必须优化同一个策略；"
        "只有机器证据达到公开的接近门槛条件时，系统才可能再提供最多两次同策略优化。"
        "该身份用尽额度仍未通过时，系统会携带完整失败证据返回假设沙箱，由你选择机制不同的新身份；"
        "每个任务最多四个顺序研究身份。身份内部candidate_id必须保持不变，不得中途换机制。"
        "优化时可以依据实际质检值调整条件、阈值和正式退出方式，但必须保留同一策略身份与核心因果机制。"
        "每次优化必须先写明一个主要、可证伪的优化假设，并围绕它进行最小而连贯的修改；"
        "不要同时盲改多个互不相关的条件。优化不是单指标爬坡：必须读取全指标约束账本，"
        "修复失败缺口的同时保护已经通过指标的安全边际；若必须牺牲某项，须明确写出可证伪的权衡。"
        "反事实回放只是诊断事实，不是系统替你选出的策略。"
        "高滑点压测期望只作为风险诊断展示，不能批准、否决、排序策略或减少重试额度。"
        "任何外部评价若声称夏普虚假、持亏行为或tick覆盖不足，必须先绑定逐笔与合约事实；"
        "缺少必要字段时只能标记为待验证假设，不能直接写进机制结论。"
        "逐笔MFE/MAE及4/8/12/16/24根固定持有窗口只用于判断信号是否存在足够价格空间；"
        "它们不是止盈止损推荐。若短窗口物理空间不足，应由你自主选择更长持有、波动环境过滤、"
        "不同入场机制或其他可执行重构，不能由程序强制ATR倍数。"
        "收益几何复算单会明确区分毛逐笔收益、基础摩擦后的E_raw与压力成本诊断；"
        "你不得用降低非负成本或扩大仓位来声称提升未加杠杆单笔E_raw。"
        "若胜率与盈亏比保持不变时所需平均盈亏幅度明显高于当前实际值，这只说明当前收益结构不足，"
        "不等于程序强制你扩大止损、止盈或持仓；你仍须结合真实MFE/MAE、环境切片和退出路径自行重构。"
        "优化假设必须以当前质检器的实时阈值为目标，不能把低于已知门槛的预期数值写成成功目标。"
        "优化重试还必须填写optimization_target_contract：目标E_raw不得低于实时E门槛，"
        "目标毛收益不得低于E门槛加双边基础摩擦，并列明需要保护的已通过指标。"
        "程序只校验这条成本算术是否自洽，不判断策略、不给参数建议，也不消耗正式质检次数。"
        "收益集中、删去最佳一笔和多指标退化信息都只是诊断，不是新的质检门。"
        "单个反事实数值更好不等于稳健最优；不得机械复制灵敏度探针产生的精确小数。"
        "优先使用具有机制含义的整洁阈值、正式分位数表达或多个相邻证据共同支持的区间，"
        "并明确说明为什么不是对单一样本点拟合。"
        "不得以任何研究合作者同意、支持、评分较高或多数AI一致作为采纳理由；只能引用可复算的事实路径。"
        "周期由创造调度策略在调用前锁定：15分钟、1小时为主，4小时、5分钟为辅。你不得为了达到周频率"
        "而请求缩短周期；周开仓频率只能由当前锁定周期上的真实数据和回测产生。"
        "趋势识字：你必须生产能判断入场点、回撤、趋势方向和趋势幅度的策略。"
        "禁止创造结构上像原油超卖反转那种读不出方向/幅度的赔率型振荡器："
        "不得只用 rsi、z、随机指标、CCI 当方向；不得写超卖/抄底/二次探底/均值回归/反转标题；"
        "不得把 Donchian 写成几乎恒真的假过滤；不得逆着中期趋势做修复单。"
        "允许唐奇安/趋势偏置与方向同向、压缩后扩张、顺势衰竭（振荡器只作时机）、高ADX骑轨。"
        "止盈必须对应你判断的幅度，不得设到价格到不了的位置去刷盈亏比。"
        "case_library 与 advisory_check_order 是跨标的共同因子课，不是研究方向禁令，也不是第二套质检。"
        "某次回踩做空因持仓过短失败，不等于以后不能做空；只有多标的重复出现的结构指纹才值得避开。"
        "live_family_paths.entry_situations 是同一策略的入场点课：哪类入场反复止损、哪类反复止盈。"
        "必须反复≥3次才采信，并阅读归因对照；不得把单笔或研究方向本身写成禁令。"
        "不得把 research_direction 或做多/做空本身当成黑名单。课包不能放行，也不能改 10 项门槛。"
        "若 tool_bundle.live_parent 存在，这是运行策略二次回炉：必须产出新 strategy_key / 新 identity；"
        "根据反复≥3次的入场点课改入场情境过滤，禁止抄止损/止盈精确小数、禁止对着盲 OOS 改参、"
        "禁止用占用周几何当理由。核心机制家族可保留，禁止退化成无趋势振荡器。"
        "输出单个合法JSON对象。status应为candidate；只有缺少不可替代工具时才可为blocked并明确列出缺口。"
    )


def _sandbox_system_prompt():
    return (
        _creator_system_prompt().rsplit("输出单个合法JSON对象。", 1)[0]
        + "当前是正式候选之前的假设沙箱，不是正式提交。"
        "必须输出单个合法JSON对象：status必须是hypotheses，"
        "hypotheses必须是3至6个机制不同的对象数组。"
        "禁止输出status=candidate，禁止只交一个正式策略身份。"
    )


def _candidate_shape():
    return {
        "status": "candidate|blocked",
        "candidate_id": "unique id",
        "title_zh": "正常中文策略标题",
        "symbol": "must equal requested symbol",
        "timeframe": "must equal requested timeframe",
        "direction": "long|short",
        "mechanism_zh": "因果机制",
        "entry_ast": {
            "type": "all",
            "children": [
                {"type": "compare", "feature": "donchian20_long_break", "op": "gt", "value": 0.0},
                {"type": "compare", "feature": "trend_bias_50_200", "op": "gt", "value": 0.0},
                {"type": "compare", "feature": "adx_14", "op": "gt", "value": 25.0},
            ],
        },
        "exit_ast": None,
        "protective_stop_pct": "<required creator-authored continuous numeric fraction>",
        "exit_plan": {
            "primary_take_profit": {"unit": "percent|atr", "value": "continuous positive"},
            "trailing": None,
            "breakeven": None,
            "partial_exits": [],
            "conditional_exits": [],
            "max_holding_bars": 24,
        },
        "falsification_zh": "什么结果证明该想法不成立",
        "tool_limitations_zh": [],
        "missing_materials_zh": [],
        "collaboration_decision_zh": "如何独立处理研究合作者的意见",
        "adopted_collaboration_points": [],
        "rejected_collaboration_points": [],
        "optimization_hypothesis_zh": "优化重试时填写本轮唯一主假设；基础创造可为空",
        "optimization_target_contract": {
            "target_E_raw_at_least": "优化重试必须填写，且不低于实时E门槛",
            "target_gross_mean_at_least": "优化重试必须填写，且不低于E门槛+双边基础摩擦",
            "passing_metrics_to_preserve": ["从最新约束账本抄录仍需保护的指标名"],
            "expected_direction_by_metric": {"E_raw": "increase"},
            "falsifiable_tradeoff_zh": "如预计牺牲已通过指标，写明幅度与证伪条件；否则写无",
        },
        "collaboration_evidence_refs": ["引用的原始事实或反事实evidence_id；基础创造可为空"],
        "causal_chain_self_check_zh": "为什么不是彩票式逻辑、条件为何不应互斥、退出为何可能覆盖摩擦",
        "trend_literacy_self_check_zh": "趋势往哪边、幅度如何判断、止盈为何不是过高刷盈亏比、为何不是无趋势振荡器",
        "claimed_performance": None,
        "contract_note": (
            "entry_ast must be a non-empty JSON object copied from the formal grammar shape, "
            "with only formally listed features; after initial creation candidate_id is immutable "
            "through all guaranteed and evidence-qualified optimization retries; "
            "protective_stop_pct and every exit_plan numeric value have no default or preset menu"
        ),
    }


def _hypothesis_sandbox_shape():
    return {
        "status": "hypotheses",
        "hypotheses": [{
            "hypothesis_id": "unique within this sandbox; not a candidate_id",
            "title_zh": "机制方向标题",
            "direction": "long|short",
            "mechanism_zh": "可证伪机制链",
            "entry_ast": {"type": "all", "children": []},
            "protective_stop_pct": "creator-authored continuous fraction",
            "exit_plan": {
                "primary_take_profit": {
                    "unit": "percent|atr", "value": "positive continuous number",
                },
                "trailing": None,
                "breakeven": None,
                "partial_exits": [],
                "conditional_exits": [{
                    "role": "take_profit|invalidation",
                    "when": {
                        "type": "market_ast",
                        "ast": {
                            "type": "compare",
                            "feature": "one formal factor",
                            "op": "lt|lte|gt|gte",
                            "value": "continuous number",
                        },
                    },
                    "close_fraction": "number in (0,1]",
                }],
                "max_holding_bars": "integer 4..288",
            },
            "falsification_zh": "哪些观测会否定这个机制",
            "distinct_from_other_hypotheses_zh": "与其他方向的机制区别",
        }],
        "rule": "propose 3-6 genuinely different mechanisms; no candidate identity yet",
    }


def _sandbox_hypothesis_candidate(raw, symbol, timeframe):
    """Use the production syntax validator without creating a formal identity."""
    row = dict(raw or {})
    hypothesis_id = str(row.get("hypothesis_id") or "").strip()
    if not hypothesis_id:
        return {"ok": False, "errors": ["hypothesis_id_required"]}
    candidate = {
        "status": "candidate",
        "candidate_id": "sandbox:%s" % hypothesis_id,
        "title_zh": row.get("title_zh") or hypothesis_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": row.get("direction"),
        "mechanism_zh": row.get("mechanism_zh"),
        "entry_ast": row.get("entry_ast"),
        "exit_ast": None,
        "protective_stop_pct": row.get("protective_stop_pct"),
        "exit_plan": row.get("exit_plan"),
        "falsification_zh": row.get("falsification_zh"),
        "tool_limitations_zh": [], "missing_materials_zh": [],
        "collaboration_decision_zh": "sandbox syntax probe only",
        "adopted_collaboration_points": [], "rejected_collaboration_points": [],
        "optimization_hypothesis_zh": "",
        "collaboration_evidence_refs": [],
        "causal_chain_self_check_zh": row.get("mechanism_zh") or "sandbox mechanism",
        "trend_literacy_self_check_zh": row.get("trend_literacy_self_check_zh") or row.get("mechanism_zh") or "",
        "claimed_performance": None,
    }
    validated = _validate_candidate(
        candidate, symbol, timeframe, direction=None,
        is_optimization_retry=False,
    )
    if not validated.get("ok"):
        return {"ok": False, "errors": validated.get("errors") or []}
    return {
        "ok": True,
        "hypothesis_id": hypothesis_id,
        "hypothesis": row,
        "executable_candidate": validated.get("candidate"),
    }


def _compact_sandbox_probe(evaluation):
    if not isinstance(evaluation, dict) or not evaluation.get("ok"):
        return {"ok": False, "error": (evaluation or {}).get("error") or "probe_failed"}
    backtest = evaluation.get("backtest") or {}
    metrics = (evaluation.get("quality_gate") or {}).get("metrics") or {}
    from .optimization_evidence import (
        exit_and_excursion_audit,
        friction_sensitivity_diagnostic,
        holding_structure_diagnostics,
        quality_constraint_ledger,
        return_geometry_reconciliation,
        trade_concentration_diagnostics,
    )
    return {
        "ok": True,
        "same_engine_exact_probe_not_formal_submission": True,
        "trade_count": backtest.get("trade_count"),
        "weekly_opens": backtest.get("weekly_opens"),
        "gross_mean_before_quality_cost": backtest.get("mean_net"),
        "win_rate": backtest.get("win_rate"),
        "payoff_ratio": backtest.get("payoff_ratio"),
        "exit_reason_dist": backtest.get("exit_reason_dist"),
        "E_raw_for_information_only": metrics.get("E_raw"),
        "stressed_expectancy_for_information_only": metrics.get("stressed_expectancy"),
        "t_statistic_for_information_only": metrics.get("t_statistic"),
        "all_10_is_metrics_for_information_only": {
            key: value for key, value in metrics.items()
            if key != "stressed_expectancy"
        },
        "stressed_expectancy_diagnostic_only": metrics.get("stressed_expectancy"),
        "quality_constraint_ledger_for_information_only": (
            quality_constraint_ledger(evaluation)
        ),
        "holding_structure_diagnostics_for_information_only": (
            holding_structure_diagnostics(backtest)
        ),
        "exit_and_excursion_audit_for_information_only": (
            exit_and_excursion_audit(backtest)
        ),
        "return_geometry_reconciliation_for_information_only": (
            return_geometry_reconciliation(backtest, evaluation)
        ),
        "friction_sensitivity_diagnostic_only": (
            friction_sensitivity_diagnostic(backtest)
        ),
        "trade_concentration_diagnostics_for_information_only": (
            trade_concentration_diagnostics(backtest)
        ),
        "failed_evidence_codes_not_a_sandbox_gate": list(
            (evaluation.get("quality_gate") or {}).get("reasons") or []
        ),
    }


def _run_hypothesis_sandbox(provider_call, collaboration, instruction, public_tools,
                            candles, volume_is_proxy=False):
    """Kimi hypotheses → independent GLM falsifiers → unranked exact probes → Kimi choice."""
    proposal_payload = {
        "mode": "pre_candidate_hypothesis_sandbox",
        "instruction": instruction,
        "read_only_tool_bundle": public_tools,
        "required_output_shape": _hypothesis_sandbox_shape(),
        "rules_zh": (
            "提出3至6个机制真正不同的方向。此阶段不得创建candidate_id，不得宣称表现，"
            "不得挑一个正式提交；每个方向必须具备可执行AST、连续退出计划和明确反证。"
            "至少两个方向必须能判断趋势方向与幅度：唐奇安/趋势偏置同向、压缩扩张或高ADX骑轨。"
            "禁止把3到6个方向都写成超卖/抄底/反转/rsi-z振荡器。"
        ),
    }
    proposal_history = []
    proposal = None
    hypotheses, validated, executable = [], [], []
    received_shape = None
    for repair_number in range(MAX_TECHNICAL_REPAIRS_PER_ATTEMPT + 1):
        proposal, transport_history = _provider_call_with_bounded_transport_retries(
            provider_call, _sandbox_system_prompt(), proposal_payload,
            max_tokens=18000, temperature=0.72 if repair_number == 0 else 0.40,
            maximum_retries=MAX_TECHNICAL_REPAIRS_PER_ATTEMPT,
        )
        proposal_history.append({
            "repair_number": repair_number, "ok": proposal.get("ok"),
            "request_id": proposal.get("request_id"), "error": proposal.get("error"),
            "bounded_backoff_seconds": None,
            "transport_history": transport_history,
        })
        if not proposal.get("ok"):
            continue
        raw = proposal.get("candidate")
        hypotheses, received_shape = _extract_sandbox_hypotheses(raw)
        validated = [
            _sandbox_hypothesis_candidate(row, instruction["requested_symbol"],
                                          instruction["requested_timeframe"])
            for row in hypotheses[:6]
        ]
        executable = [row for row in validated if row.get("ok")]
        if len(executable) >= 2:
            break
        contract_errors = [
            {"hypothesis_index": index, "errors": row.get("errors") or []}
            for index, row in enumerate(validated) if not row.get("ok")
        ]
        if not hypotheses:
            contract_errors.append({
                "hypothesis_index": None,
                "errors": ["hypotheses_array_required_len_3_to_6"],
                "received_shape": _compact_received_shape(raw, received_shape),
            })
        proposal_payload = dict(proposal_payload)
        proposal_payload["technical_contract_errors"] = contract_errors
        proposal_payload["received_shape"] = _compact_received_shape(raw, received_shape)
        proposal_payload["instruction_to_creator"] = (
            "技术契约不足，不计正式创造次数。禁止输出status=candidate。"
            "必须输出{status:'hypotheses', hypotheses:[3至6个方向]}。"
            "至少两个必须能由给定正式因子和连续退出DSL完整执行。conditional_exits中的"
            "市场条件必须严格写为when={type:'market_ast', ast:{完整正式AST}}，不能把"
            "feature/op/value直接放在when下，也不能省略ast。AST若使用between，必须按正式"
            "语法同时提供lower与upper；不确定时使用普通lt/lte/gt/gte compare。"
        )
    if len(executable) < 1:
        return {
            "ok": False, "error": "fewer_than_two_executable_sandbox_hypotheses",
            "proposal_history": proposal_history, "validation": validated,
            "received_shape": received_shape,
        }

    falsification = None
    if collaboration is not None and hasattr(collaboration, "falsify_hypotheses"):
        try:
            falsification = collaboration.falsify_hypotheses(
                instruction,
                [row.get("hypothesis") for row in executable],
                public_tools,
            )
        except Exception as exc:
            falsification = {
                "advisory_only": True, "not_a_gate_or_vote": True,
                "error": "%s:%s" % (type(exc).__name__, str(exc)[:240]),
            }

    probe_rows = []
    for row in executable:
        evaluation = _run_candidate(
            row.get("executable_candidate"), candles,
            volume_is_proxy=bool(volume_is_proxy),
        )
        probe_rows.append({
            "hypothesis_id": row.get("hypothesis_id"),
            "hypothesis_exactly_as_authored": row.get("hypothesis"),
            "probe": _compact_sandbox_probe(evaluation),
        })

    selection_payload = {
        "mode": "select_one_formal_candidate_after_hypothesis_sandbox",
        "instruction": instruction,
        "read_only_tool_bundle": public_tools,
        "kimi_hypotheses_in_original_order": [
            row.get("hypothesis") for row in executable
        ],
        "independent_glm_falsification_not_a_vote": falsification,
        "same_engine_probe_rows_unranked": probe_rows,
        "required_output_shape": _candidate_shape(),
        "selection_rule_zh": (
            "现在由你独立选择一个机制并形成唯一正式候选身份。程序没有排序或推荐。"
            "你可以依据反证与探针重写被选机制的入场和退出，但不得声称探针即正式通过。"
            "必须同时阅读每个方向的10项约束账本，不得仅因某一个收益指标较高就选择一个在胜率、"
            "持仓、止盈率或样本结构上明显崩塌的方向；若选择存在权衡的方向，必须给出可证伪理由。"
            "不得选择读不出趋势方向或幅度的振荡器赔率型，即使探针期望较高。"
        ),
    }
    # Selection is a provider call after an already expensive hypothesis and
    # falsification round.  A transient capacity response here must not erase
    # all deterministic probes or be misreported as a creative failure.  Use
    # the same bounded transport retry contract as the formal creator calls;
    # these retries never consume a quality evaluation.
    selection, selection_history = _provider_call_with_bounded_transport_retries(
        provider_call,
        _creator_system_prompt(),
        selection_payload,
        max_tokens=18000,
        temperature=0.48,
        maximum_retries=MAX_TECHNICAL_REPAIRS_PER_ATTEMPT,
    )
    return {
        "ok": bool(selection.get("ok")),
        "proposal_request": {
            "request_id": proposal.get("request_id"), "model": proposal.get("model"),
            "usage": proposal.get("usage"),
        },
        "proposal_history": proposal_history,
        "raw_hypothesis_count": len(hypotheses),
        "executable_hypothesis_count": len(executable),
        "validation": validated,
        "falsification": falsification,
        "probe_rows_unranked": probe_rows,
        "selection_request": {
            "request_id": selection.get("request_id"), "model": selection.get("model"),
            "usage": selection.get("usage"), "error": selection.get("error"),
        },
        "selection_transport_history": selection_history,
        "selected_candidate_raw": selection.get("candidate"),
        "automatic_ranking_or_selection": False,
        "formal_candidate_identity_created_only_by_kimi_selection": True,
    }


def _ast_features(node):
    found = set()
    if isinstance(node, dict):
        for key in ("feature", "right_feature"):
            value = str(node.get(key) or "").strip()
            if value:
                found.add(value)
        for value in node.values():
            found.update(_ast_features(value))
    elif isinstance(node, (list, tuple)):
        for value in node:
            found.update(_ast_features(value))
    return found


def _validate_candidate(raw, symbol, timeframe, direction=None,
                        expected_candidate_id=None,
                        expected_base_features=None,
                        is_optimization_retry=None):
    from . import ast_compiler

    row = dict(raw or {})
    if is_optimization_retry is None:
        is_optimization_retry = expected_candidate_id is not None
    errors = []
    if row.get("status") == "blocked":
        return {"ok": False, "blocked": True, "errors": ["creator_reported_blocked"], "candidate": row}
    if row.get("status") != "candidate":
        errors.append("status_must_be_candidate")
    if not str(row.get("collaboration_decision_zh") or "").strip():
        errors.append("collaboration_decision_zh_required")
    if not str(row.get("causal_chain_self_check_zh") or "").strip():
        errors.append("causal_chain_self_check_zh_required")
    if row.get("claimed_performance") not in (None, "", {}, []):
        errors.append("unbacktested_performance_claim_forbidden")
    candidate_id = str(row.get("candidate_id") or "").strip()
    if not candidate_id:
        errors.append("candidate_id_required")
    if expected_candidate_id is not None and candidate_id != str(expected_candidate_id):
        errors.append("candidate_id_must_remain:%s" % expected_candidate_id)
    if is_optimization_retry and not str(
            row.get("optimization_hypothesis_zh") or "").strip():
        errors.append("optimization_hypothesis_zh_required_on_retry")
    if is_optimization_retry and not isinstance(
            row.get("collaboration_evidence_refs"), list):
        errors.append("collaboration_evidence_refs_list_required_on_retry")
    if is_optimization_retry:
        from .quality_gate import BASE_FRICTION, SINGLE_STRATEGY_GATE_RULES
        target_contract = row.get("optimization_target_contract")
        if not isinstance(target_contract, dict):
            errors.append("optimization_target_contract_required_on_retry")
        else:
            target_e = _finite(target_contract.get("target_E_raw_at_least"))
            target_gross = _finite(
                target_contract.get("target_gross_mean_at_least")
            )
            minimum_e = float(
                SINGLE_STRATEGY_GATE_RULES["min_raw_expectancy"]
            )
            minimum_gross = minimum_e + 2.0 * float(BASE_FRICTION)
            if target_e is None or target_e < minimum_e:
                errors.append(
                    "optimization_target_E_below_live_gate:need_%s" % minimum_e
                )
            if target_gross is None or target_gross < minimum_gross:
                errors.append(
                    "optimization_target_gross_below_live_cost_geometry:need_%s" %
                    minimum_gross
                )
            if not isinstance(
                    target_contract.get("passing_metrics_to_preserve"), list):
                errors.append(
                    "optimization_passing_metrics_to_preserve_list_required"
                )
            if not isinstance(
                    target_contract.get("expected_direction_by_metric"), dict):
                errors.append(
                    "optimization_expected_direction_by_metric_required"
                )
    if str(row.get("symbol") or "").upper() != str(symbol or "").upper():
        errors.append("symbol_mismatch")
    if str(row.get("timeframe") or "").lower() != str(timeframe or "").lower():
        errors.append("timeframe_mismatch")
    candidate_direction = str(row.get("direction") or "").lower()
    if candidate_direction not in ("long", "short"):
        errors.append("direction_invalid")
    if direction and candidate_direction != str(direction).strip().lower():
        errors.append("direction_mismatch")
    stop_pct = _protective_stop_pct(row.get("protective_stop_pct"))
    if stop_pct is None:
        errors.append(
            "protective_stop_pct_required_as_finite_fraction_between_0_and_1"
        )
    else:
        row["protective_stop_pct"] = stop_pct
    formal = ast_compiler.formal_feature_registry()
    from .exit_dsl import validate_exit_plan
    exit_plan_check = validate_exit_plan(
        row.get("exit_plan"), feature_registry_list=formal,
    )
    if not exit_plan_check.get("ok"):
        errors.extend(
            "exit_plan_invalid:%s" % item
            for item in (exit_plan_check.get("errors") or [])
        )
    else:
        row["exit_plan"] = exit_plan_check.get("normalized")
        row["max_holding_bars"] = row["exit_plan"].get("max_holding_bars")
    entry = ast_compiler.validate_ast(
        row.get("entry_ast") or {}, feature_registry_list=formal,
    )
    if not entry.get("ok"):
        errors.append("entry_ast_invalid:%s" % json.dumps(entry.get("errors") or [], ensure_ascii=False))
    else:
        compiled = ast_compiler.compile_ast_to_dsl(entry.get("normalized"))
        if not compiled.get("formal_ok"):
            errors.append("entry_ast_not_formal:%s" % json.dumps(compiled.get("reasons") or [], ensure_ascii=False))
        else:
            row["entry_ast"] = entry.get("normalized")
    from .entry_structure_literacy import assess as assess_entry
    literacy = assess_entry(
        row.get("entry_ast"),
        direction=candidate_direction,
        title_zh=row.get("title_zh") or row.get("mechanism_zh"),
    )
    row["structure_literacy"] = {
        "ok": literacy.get("ok"),
        "family": literacy.get("family"),
        "reasons": literacy.get("reasons") or [],
    }
    if not literacy.get("ok"):
        errors.append(literacy.get("creator_error") or "structure_cannot_read_trend")
    sandbox_id = str(row.get("candidate_id") or "").startswith("sandbox:")
    if not sandbox_id and not str(row.get("trend_literacy_self_check_zh") or "").strip():
        errors.append("trend_literacy_self_check_zh_required")
    if expected_base_features:
        base_features = set(expected_base_features)
        current_features = _ast_features(row.get("entry_ast") or {})
        overlap = base_features & current_features
        minimum_overlap = max(1, int(math.ceil(min(
            len(base_features), len(current_features),
        ) * 0.5)))
        if len(overlap) < minimum_overlap:
            errors.append(
                "same_strategy_feature_continuity_failed:need_%s_have_%s" %
                (minimum_overlap, len(overlap))
            )
    if row.get("exit_ast") not in (None, {}, []):
        errors.append("top_level_exit_ast_forbidden_use_exit_plan_conditional_exits")
    row["exit_ast"] = None
    return {"ok": not errors, "blocked": False, "errors": errors, "candidate": row}


def _candidate_to_ir(candidate):
    from .strategy_ir import StrategyIR
    max_holding_bars = (
        candidate.get("max_holding_bars")
        if candidate.get("max_holding_bars") is not None
        else ((candidate.get("exit_plan") or {}).get("max_holding_bars"))
    )

    return StrategyIR(
        symbol=candidate.get("symbol"),
        timeframe=candidate.get("timeframe"),
        direction=candidate.get("direction"),
        entry_atoms=[], exit_atoms=[],
        entry_ast=candidate.get("entry_ast"),
        exit_ast=candidate.get("exit_ast"),
        stop_contract={
            "mode": "fixed_pct",
            "fixed": float(candidate.get("protective_stop_pct")),
            "exit_plan": candidate.get("exit_plan"),
        },
        holding_policy={"max_bars": int(max_holding_bars)},
        text_summary=(candidate.get("title_zh") or candidate.get("mechanism_zh") or "")[:2000],
        branch_type="kimi_sole_creator",
    )


def _run_candidate(candidate, candles, volume_is_proxy=False):
    from .quality_gate import build_numeric_report_10, check_is
    from .semantic_backtest import run_semantic_backtest
    from .strategy_ir import compute_execution_hash, compute_recipe_hash

    ir = _candidate_to_ir(candidate)
    backtest = run_semantic_backtest(
        ir, candles,
        symbol=ir.symbol, timeframe=ir.timeframe, direction=ir.direction,
        volume_is_proxy=bool(volume_is_proxy),
    )
    if not isinstance(backtest, dict) or not backtest.get("ok"):
        return {
            "ok": False, "technical_error": True,
            "error": (backtest or {}).get("error") or "backtest_failed",
        }
    gate = check_is(backtest, timeframe=ir.timeframe)
    report = build_numeric_report_10(gate)
    return {
        "ok": True, "technical_error": False,
        "ir": ir.to_dict(), "recipe_hash": compute_recipe_hash(ir),
        "execution_hash": compute_execution_hash(ir),
        "backtest": backtest, "quality_gate": gate,
        "quality_report": report,
    }


def _run_candidate_with_technical_retries(candidate, candles,
                                          volume_is_proxy=False,
                                          maximum_retries=2):
    """Retry executor faults without spending a Kimi creative attempt."""
    events = []
    for retry_number in range(max(0, int(maximum_retries)) + 1):
        result = _run_candidate(
            candidate, candles, volume_is_proxy=volume_is_proxy,
        )
        if result.get("ok"):
            return result, events
        events.append({
            "retry_number": retry_number,
            "stage": "backtest",
            "error": result.get("error") or "backtest_failed",
            "at": _now(),
        })
    return result, events


def _run_blind_oos_once(candidate, is_evaluation, full_candles,
                        oos_start_index, instruction_id, dataset_version,
                        split_id, development_candidate_count,
                        volume_is_proxy=False, test_mode=False,
                        lineage_root_id=None, oos_identity_look_index=1,
                        oos_identity_looks_planned=None):
    """Freeze one candidate and consume the chronological OOS exactly once."""
    from .quality_gate import (
        build_numeric_report_oos, check_full, check_is, check_oos,
    )
    from .semantic_backtest import run_semantic_backtest
    from .strategy_ir import compute_execution_hash, compute_recipe_hash

    ir = _candidate_to_ir(candidate)
    recipe_hash = compute_recipe_hash(ir)
    lineage_id = lineage_root_id or instruction_id
    look_index = max(1, int(oos_identity_look_index or 1))
    looks_planned = max(
        look_index,
        int(
            MAX_OOS_IDENTITY_TESTS
            if oos_identity_looks_planned is None
            else oos_identity_looks_planned
        ),
    )
    reserved = None
    if not test_mode:
        try:
            from .oos_registry import reserve_oos
            reserved = reserve_oos(
                lineage_id, dataset_version, instruction_id,
            )
        except Exception as exc:
            return {
                "ok": False, "technical_error": True,
                "error": "blind_oos_reservation_failed:%s:%s" % (
                    type(exc).__name__, str(exc)[:240],
                ),
            }
    try:
        oos_backtest = run_semantic_backtest(
            ir, full_candles,
            symbol=ir.symbol, timeframe=ir.timeframe, direction=ir.direction,
            volume_is_proxy=bool(volume_is_proxy),
            entry_start_index=int(oos_start_index), entry_end_index=len(full_candles),
        )
    except Exception as exc:
        oos_backtest = {
            "ok": False,
            "error": "oos_backtest_exception:%s:%s" % (
                type(exc).__name__, str(exc)[:240],
            ),
        }
    finally:
        if not test_mode and reserved is not None:
            from .oos_registry import mark_oos_consumed_on_read
            mark_oos_consumed_on_read(
                lineage_id, dataset_version, instruction_id,
                recipe_hash=recipe_hash,
            )
    if not isinstance(oos_backtest, dict) or not oos_backtest.get("ok"):
        return {
            "ok": False, "technical_error": True,
            "error": (oos_backtest or {}).get("error") or "oos_backtest_failed",
        }
    is_backtest = dict((is_evaluation or {}).get("backtest") or {})
    if not is_backtest:
        return {"ok": False, "technical_error": True, "error": "is_backtest_missing"}

    frozen = {
        "candidate_id": candidate.get("candidate_id"),
        "recipe_hash": recipe_hash,
        "execution_hash": compute_execution_hash(ir),
        "oos_start_index": int(oos_start_index),
        "oos_end_index_exclusive": len(full_candles),
        "dataset_version": dataset_version,
        "split_id": split_id,
        "lineage_root_id": lineage_id,
        "read_count": 1,
        "oos_identity_look_index": look_index,
        "oos_identity_looks_planned": looks_planned,
        "multiple_testing_same_holdout": look_index > 1,
        "feedback_to_creator_forbidden": True,
        "candidate_mutation_after_read_forbidden": True,
        "at": _now(),
    }

    # A zero-trade OOS is a deterministic quality failure.  A formal receipt
    # cannot exist without a ledger, so record the hard failure directly and
    # never treat it as an executor fault or allow a retry.
    if int(oos_backtest.get("trade_count") or 0) <= 0:
        is_gate = check_is(is_backtest, timeframe=ir.timeframe)
        oos_gate = check_oos(oos_backtest, is_backtest, timeframe=ir.timeframe)
        full_gate = check_full(
            is_backtest, timeframe=ir.timeframe, oos_backtest=oos_backtest,
            test_mode=True,
            entry_ast=candidate.get("entry_ast"),
            direction=candidate.get("direction"),
            title_zh=candidate.get("title_zh"),
        )
        full_gate["failed_rules"] = list(dict.fromkeys(
            list(full_gate.get("failed_rules") or []) + ["oos_zero_trades"]
        ))
        full_gate["ok"] = False
        return {
            "ok": True, "technical_error": False, "quality_pass": False,
            "backtest": oos_backtest, "quality_gate": full_gate,
            "is_gate": is_gate, "oos_gate": oos_gate,
            "quality_report_oos": build_numeric_report_oos(oos_gate),
            "frozen_candidate_receipt": frozen,
            "formal_receipt_unavailable_reason": "oos_zero_trades",
        }

    from .formal_evaluator import run_formal_quality_gate
    formal = run_formal_quality_gate(
        is_backtest, oos_backtest, ir.timeframe, recipe_hash,
        dataset_version=dataset_version, split_id=split_id,
        mission_id=instruction_id, lineage_root_id=lineage_id,
        candidate_count_development=int(development_candidate_count or 0),
        candidate_count_selection=look_index,
        test_mode=bool(test_mode), oos_reserved=False,
        code_version=oos_backtest.get("engine_code_digest"),
        entry_ast=candidate.get("entry_ast"),
        direction=candidate.get("direction"),
        title_zh=candidate.get("title_zh"),
    )
    gate = formal.get("quality_gate") or {}
    oos_gate = gate.get("oos_gate") or {}
    return {
        "ok": True, "technical_error": False,
        "quality_pass": bool(formal.get("ok")),
        "backtest": oos_backtest, "quality_gate": gate,
        "is_gate": gate.get("is_gate") or {}, "oos_gate": oos_gate,
        "quality_report_oos": build_numeric_report_oos(oos_gate),
        "frozen_candidate_receipt": frozen,
        "formal_quality_receipts": {
            "is_receipt": formal.get("is_receipt"),
            "oos_receipt": formal.get("oos_receipt"),
            "receipt_validation": formal.get("receipt_validation"),
        },
    }


def _compact_counterfactual_replays(value):
    """Keep every unranked probe while removing bulky per-trade payloads."""
    if not isinstance(value, dict):
        return value
    compact = {
        key: value.get(key) for key in (
            "executed", "budget", "selection_or_ranking_performed",
            "diagnostic_dimensions",
        )
    }
    compact["rows"] = []
    for row in value.get("rows") or []:
        evaluation = row.get("evaluation") or {}
        compact["rows"].append({
            "evidence_id": row.get("evidence_id"),
            "kind": row.get("kind"),
            "change": row.get("change"),
            "not_a_submitted_candidate": row.get("not_a_submitted_candidate"),
            "evaluation": {
                "ok": evaluation.get("ok"),
                "quality_pass": evaluation.get("quality_pass"),
                "failed_rules": evaluation.get("failed_rules"),
                "metrics": evaluation.get("metrics"),
                "gross_mean_before_quality_cost": evaluation.get(
                    "gross_mean_before_quality_cost"
                ),
                "exit_reason_dist": evaluation.get("exit_reason_dist"),
            },
        })
    return compact


def _compact_optimization_evidence(value):
    """Bound creator context without discarding decision-bearing evidence.

    Full receipts retain all trades and diagnostics.  Kimi receives the named
    evidence tables, but never the repeated raw backtest/trade payloads that
    previously made a late retry exceed hundreds of thousands of tokens.
    """
    if not isinstance(value, dict):
        return value
    keep = (
        "schema", "authority", "quality_gate_unchanged",
        "metric_delta_from_previous_submission", "retry_tradeoff_diagnostics",
        "submitted_candidate_diff", "entry_condition_signal_funnel",
        "trade_return_distribution", "return_geometry_reconciliation",
        "friction_sensitivity_diagnostic_only",
        "bootstrap_uncertainty_diagnostic_only",
        "trade_concentration_diagnostics", "chronological_trade_stability",
        "exit_path_breakdown", "exit_and_excursion_audit",
        "holding_structure_diagnostics", "quality_constraint_ledger",
        "used_factor_correlations", "factor_outcome_contrast",
        "stratified_best_worst_trade_samples",
        "market_environment_stratification", "explicit_limitations",
        "return_structure_feasibility",
        "collaborator_requested_structural_probe_results",
    )
    result = {key: value.get(key) for key in keep if key in value}
    if "counterfactual_replays" in value:
        result["counterfactual_replays"] = _compact_counterfactual_replays(
            value.get("counterfactual_replays")
        )
    result["full_evidence_retained_in_receipt"] = True
    return result


def _feedback(attempt_row):
    result = attempt_row.get("evaluation") or {}
    backtest = result.get("backtest") or {}
    return {
        "evaluation_number": attempt_row.get("evaluation_number") or attempt_row.get("attempt_number"),
        "optimization_retry_number": attempt_row.get("optimization_retry_number"),
        "candidate_identity": attempt_row.get("candidate"),
        "quality_report": result.get("quality_report"),
        "backtest_summary": {
            "trade_count": backtest.get("trade_count"),
            "weekly_opens": backtest.get("weekly_opens"),
            "gross_mean_before_quality_cost": backtest.get("mean_net"),
            "win_rate": backtest.get("win_rate"),
            "payoff_ratio": backtest.get("payoff_ratio"),
            "exit_reason_dist": backtest.get("exit_reason_dist"),
            "span_days": backtest.get("span_days"),
        },
        "read_only_optimization_evidence": _compact_optimization_evidence(
            attempt_row.get("optimization_evidence")
        ),
        "return_structure_feasibility": (
            (((attempt_row.get("non_gating_preflight") or {}).get("final_raw_facts") or {}).get(
                "return_structure_feasibility"
            ))
        ),
        "post_failure_research_collaboration": attempt_row.get("post_failure_collaboration"),
        "failure_routing": attempt_row.get("failure_routing"),
        "local_strategy_advice": None,
        "instruction": (
            "请依据事实优化同一策略；必须保持candidate_id与核心因果机制，不得换成另一个策略。"
            "若failure_routing为exit_structure_problem，可完整重构连续退出计划；若为parameter_problem，"
            "围绕同一可证伪假设做局部参数研究；若为entry_structure_problem，应保留同一因果机制，"
            "但可依据结构反事实重构入场条件组合；若为mechanism_problem，剩余保证次数只用于直接证伪"
            "或最后的同机制结构修复，额度用尽后返回假设沙箱，不得继续微调续命。"
            "必须逐项读取quality_constraint_ledger："
            "优先修复失败指标，同时保护passing_metrics_to_preserve的安全边际；不得再用牺牲胜率、"
            "止盈率或持仓结构的方式单独追逐期望。若确有权衡，必须写入optimization_hypothesis_zh。"
            "holding_structure_diagnostics用于定位过早退出的具体路径。"
            "本地工具不会推荐因子、阈值或方向。"
        ),
    }


def _collaboration_probe_requests(memo):
    requests = []
    for row in (memo or {}).get("collaborators") or []:
        if not row.get("ok"):
            continue
        requests.extend(list((row.get("memo") or {}).get(
            "requested_structural_probes"
        ) or []))
    return requests[:8]


def _kimi_structural_probe_plan(provider_call, instruction, candidate,
                                quality_report, optimization_evidence,
                                collaboration_memo, public_tools):
    """Let Kimi ask the tool for evidence before spending the next submission."""
    payload = {
        "mode": "request_structural_counterfactuals_before_formal_retry",
        "instruction": instruction,
        "current_candidate": candidate,
        "quality_report_10": quality_report,
        "existing_unranked_evidence": optimization_evidence,
        "independent_collaboration_memo": collaboration_memo,
        "formal_factor_names": (public_tools or {}).get("formal_factor_names"),
        "entry_ast_grammar": (public_tools or {}).get("entry_ast_grammar"),
        "exit_grammar": (public_tools or {}).get("exit_grammar"),
        "required_output_shape": {
            "requests": [{
                "probe_id": "unique id",
                "hypothesis_zh": "new falsifiable structural hypothesis",
                "entry_ast": "complete AST or null to keep current",
                "direction": "long|short|null",
                "protective_stop_pct": "continuous number or null",
                "exit_plan": "complete exit plan or null",
                "falsification_zh": "what probe result refutes it",
            }],
        },
        "rule_zh": (
            "这是工具请求，不是正式候选。可请求条件增加、替换、运算符反转、all/any、多空镜像、"
            "入场与退出联合变化。最多8项，不得声称优劣；程序将按原顺序回放并返回全部结果。"
        ),
    }
    response = provider_call(
        _creator_system_prompt(), payload, max_tokens=12000, temperature=0.36,
    )
    return {
        "ok": bool(response.get("ok")),
        "request_id": response.get("request_id"),
        "model": response.get("model"), "usage": response.get("usage"),
        "error": response.get("error"),
        "requests": list(((response.get("candidate") or {}).get("requests") or []))[:8],
    }


def _attempt_metric(attempt, name):
    gate = ((attempt or {}).get("evaluation") or {}).get("quality_gate") or {}
    return _finite((gate.get("metrics") or {}).get(name))


def _conditional_extension_decision(attempts):
    from .quality_gate import BASE_FRICTION, SINGLE_STRATEGY_GATE_RULES
    raw_gate = float(SINGLE_STRATEGY_GATE_RULES["min_raw_expectancy"])
    t_gate = float(SINGLE_STRATEGY_GATE_RULES["min_t_statistic"])
    gross_gate = raw_gate + 2.0 * float(BASE_FRICTION)
    """Decide only whether another formal AI submission deserves budget.

    This is not a quality gate and cannot approve a strategy.  It prevents a
    far-from-frontier candidate from mining the same development sample while
    allowing an objectively near-frontier, improving candidate at most two
    additional submissions.
    """
    rows = list(attempts or [])
    latest_generation = max(
        [int(row.get("mechanism_generation") or 1) for row in rows] or [1]
    )
    rows = [
        row for row in rows
        if int(row.get("mechanism_generation") or 1) == latest_generation
    ]
    latest = rows[-1] if rows else {}
    previous = rows[-2] if len(rows) >= 2 else {}
    latest_eval = latest.get("evaluation") or {}
    latest_bt = latest_eval.get("backtest") or {}
    metrics = ((latest_eval.get("quality_gate") or {}).get("metrics") or {})
    checks = {
        "E_raw_at_least_70pct_of_gate": (
            (_finite(metrics.get("E_raw")) or -1e9) >= 0.70 * raw_gate
        ),
        "gross_mean_at_least_70pct_of_required_geometry": (
            (_finite(latest_bt.get("mean_net")) or -1e9) >= 0.70 * gross_gate
        ),
        "t_statistic_near_gate": (
            (_finite(metrics.get("t_statistic")) or -1e9) >= 0.85 * t_gate
        ),
        "trade_count_at_least_80pct_of_gate_for_retry_budget_only": (
            (_finite(metrics.get("n")) or -1e9)
            >= 0.80 * float(SINGLE_STRATEGY_GATE_RULES["min_total_trades"])
        ),
        "weekly_frequency_already_valid": (
            (_finite(metrics.get("weekly_opens")) or -1e9) >= 0.5
        ),
    }
    segments = (((latest.get("optimization_evidence") or {}).get(
        "chronological_trade_stability") or {}).get("segments") or [])
    segment_means = [
        _finite(row.get("mean_return")) for row in segments
        if _finite(row.get("mean_return")) is not None
    ]
    checks["chronological_evidence_available"] = len(segment_means) >= 4
    checks["at_least_three_positive_time_segments"] = (
        sum(1 for value in segment_means if value > 0.0) >= 3
    )
    refs = (latest.get("candidate") or {}).get("collaboration_evidence_refs")
    checks["creator_cited_evidence"] = bool(isinstance(refs, list) and refs)
    routing = latest.get("failure_routing") or {}
    checks["not_a_mechanism_failure"] = routing.get("category") != "mechanism_problem"
    checks["new_falsifiable_structural_hypothesis"] = bool(
        routing.get("new_falsifiable_structural_hypothesis_present")
    )

    improvements = []
    for name in ("E_raw", "t_statistic"):
        before = _attempt_metric(previous, name)
        after = _attempt_metric(latest, name)
        if before is not None and after is not None and after > before:
            improvements.append(name)
    checks["at_least_one_core_metric_improved"] = len(improvements) >= 1
    allowed = bool(checks) and all(checks.values())
    return {
        "schema": "qiyu_conditional_retry_budget_v1",
        "authority": "resource_budget_only_not_quality_gate",
        "allowed": allowed,
        "checks": checks,
        "improved_core_metrics": improvements,
        "quality_gate_unchanged": True,
        "automatic_strategy_selection": False,
    }


def _mechanism_branch_coverage_ledger(attempts, diagnostics):
    """Describe formal coverage of Kimi-authored branches without ranking them."""
    generations = {}
    for row in diagnostics or []:
        sandbox = row.get("hypothesis_sandbox") or {}
        if not sandbox:
            continue
        generation = int(row.get("mechanism_generation") or 1)
        target = generations.setdefault(generation, {
            "mechanism_generation": generation,
            "sandbox_hypotheses_in_original_order": [],
            "formal_candidate_ids": [],
            "formal_evaluation_count": 0,
        })
        for probe_row in sandbox.get("probe_rows_unranked") or []:
            probe = probe_row.get("probe") or {}
            target["sandbox_hypotheses_in_original_order"].append({
                "hypothesis_id": probe_row.get("hypothesis_id"),
                "initial_probe_E_raw": probe.get("E_raw_for_information_only"),
                "trade_count": probe.get("trade_count"),
                "weekly_opens": probe.get("weekly_opens"),
                "probe_is_not_formal_submission": True,
            })
    for row in attempts or []:
        generation = int(row.get("mechanism_generation") or 1)
        target = generations.setdefault(generation, {
            "mechanism_generation": generation,
            "sandbox_hypotheses_in_original_order": [],
            "formal_candidate_ids": [],
            "formal_evaluation_count": 0,
        })
        candidate_id = (row.get("candidate") or {}).get("candidate_id")
        if candidate_id and candidate_id not in target["formal_candidate_ids"]:
            target["formal_candidate_ids"].append(candidate_id)
        target["formal_evaluation_count"] += 1
        target["latest_failure_routing"] = row.get("failure_routing")
    return {
        "schema": "qiyu_mechanism_branch_coverage_ledger_v1",
        "authority": "descriptive_creator_evidence_only",
        "can_rank_select_approve_or_reject": False,
        "branches": [generations[key] for key in sorted(generations)],
    }


def _make_handoff(instruction_id, brief, candidate, evaluation, notification=None,
                  human_submission=None):
    creator_provider = str(candidate.get("_creator_provider") or "kimi").lower()
    creator_label = str(candidate.get("_creator_label") or "Kimi K3")
    creator_path = "%s_sole_creator" % creator_provider
    gate = evaluation.get("quality_gate") or {}
    report = evaluation.get("quality_report") or {}
    oos_report = evaluation.get("quality_report_oos") or {}
    full_gate = gate if isinstance(gate.get("is_gate"), dict) else None
    is_gate = (
        (full_gate or {}).get("is_gate")
        or evaluation.get("is_quality_gate")
        or gate
    )
    ir = evaluation.get("ir") or {}
    recipe_hash = evaluation.get("recipe_hash")
    metrics = dict(is_gate.get("metrics") or {})
    metrics.update({
        "expectancy_E": metrics.get("E_raw"),
        "E_basis": "backtest_account_only",
        "symbol": candidate.get("symbol"),
        "timeframe": candidate.get("timeframe"),
        "direction": candidate.get("direction"),
        "protective_stop_pct": candidate.get("protective_stop_pct"),
        "key": "%s_%s" % (creator_provider, str(recipe_hash or instruction_id)[:24]),
        "name": candidate.get("title_zh"),
    })
    admission = {
        "schema": "qiyu_quality_gate_admission_v1",
        "ok": bool(gate.get("ok")), "pass": bool(gate.get("ok")),
        "status": "PASS" if gate.get("ok") else "FAIL",
        "quality_gate": full_gate or {
            "ok": bool(gate.get("ok")), "is_gate": gate,
            "oos_gate": {"ok": False, "reasons": ["oos_not_reached"]},
            "failed_rules": list(gate.get("reasons") or []),
            "criteria": (
                "IS 10项 + 胜率盈亏比结构 + 一次性盲OOS夏普/2项数据硬规则；"
                "夏普保留率与OOS高滑点期望仅风险诊断"
            ),
        },
        "metrics": metrics,
        "failed_rules": list(
            (full_gate or {}).get("failed_rules") or gate.get("reasons") or []
        ),
        "recipe_identity_hash": recipe_hash,
        "authority": "single_quality_gate_check_full_is10_structure_oos3",
    }
    dsl = {
        "key": metrics.get("key"), "name": candidate.get("title_zh"),
        "symbol": candidate.get("symbol"), "timeframe": candidate.get("timeframe"),
        "direction": candidate.get("direction"), "entry_ast": candidate.get("entry_ast"),
        "exit_ast": candidate.get("exit_ast"),
        "protective_stop_pct": candidate.get("protective_stop_pct"),
        "exit_plan": candidate.get("exit_plan"),
        "max_holding_bars": candidate.get("max_holding_bars"),
        "creation_path": creator_path,
    }
    return {
        "schema": "qiyu_kimi_quality_handoff_v1",
        "ok": bool(gate.get("ok")), "present_to_human": bool(gate.get("ok")),
        "quality_status": "通过" if gate.get("ok") else "未通过",
        "run_id": instruction_id, "creator": creator_provider,
        "creator_label": creator_label,
        "creator_role": "sole_strategy_creator",
        "brief": brief, "recipe": ir, "recipe_identity_hash": recipe_hash,
        "dsl": dsl, "metrics": metrics, "quality_gate": admission.get("quality_gate"),
        "quality_gate_admission": admission, "quality_report_10": report,
        "quality_report_oos": oos_report,
        "blind_oos_receipt": ((evaluation.get("blind_oos") or {}).get(
            "frozen_candidate_receipt"
        )),
        "notification": notification, "human_confirm_submission": human_submission,
        "automatic_live_deployment": False, "at": _now(),
    }


def _enqueue_human(handoff, dry_run=False):
    if dry_run:
        return {"ok": True, "dry_run": True, "production_mounted": False}
    try:
        import auto_trade_human_confirm_pipeline as hcp
        payload = dict(
            cand={"dsl": handoff.get("dsl"), "recipe": handoff.get("recipe")},
            metrics=handoff.get("metrics") or {},
            source="cognitive_%s" % (handoff.get("creator") or "kimi"),
            ai_review={
                "schema": "qiyu_independent_sole_creator_v1",
                "creator": handoff.get("creator") or "kimi",
                "creator_label": handoff.get("creator_label") or "Kimi K3",
                "advisory_only": False,
                "quality_gate_only": True,
            },
            quality_gate_admission=handoff.get("quality_gate_admission"),
            recipe_identity_hash=handoff.get("recipe_identity_hash"),
        )
        try:
            return hcp.enqueue_for_human(send_notification=False, **payload)
        except TypeError as exc:
            if "send_notification" not in str(exc):
                raise
            return hcp.enqueue_for_human(**payload)
    except Exception as exc:
        return {"ok": False, "error": "%s:%s" % (type(exc).__name__, str(exc)[:240])}


class KimiCreatorRuntime(object):
    def __init__(self, provider_call=None, collaboration=None,
                 creator_provider="kimi", creator_label="Kimi K3",
                 suppress_failure_notification=True):
        self.creator_provider = str(creator_provider or "kimi").strip().lower()
        self.creator_label = str(creator_label or self.creator_provider).strip()
        self.creator_path = "%s_sole_creator" % self.creator_provider
        self.suppress_failure_notification = bool(
            suppress_failure_notification)
        base_provider_call = provider_call or _kimi_call

        def attributed_provider_call(system_prompt, payload, **kwargs):
            # Prompts and public evidence historically named Kimi explicitly.
            # Replace only creator-role wording at the outbound boundary so a
            # fallback model never receives a false identity or is asked to
            # defer to the unavailable model.
            def relabel(value):
                if isinstance(value, dict):
                    return {key: relabel(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [relabel(item) for item in value]
                if isinstance(value, tuple):
                    return tuple(relabel(item) for item in value)
                if isinstance(value, str):
                    if self.creator_provider == "kimi":
                        return value
                    return value.replace("Kimi K3", self.creator_label).replace(
                        "Kimi", self.creator_label)
                return value
            return base_provider_call(
                relabel(system_prompt), relabel(payload), **kwargs
            )

        self.provider_call = attributed_provider_call
        # Unit/self tests often inject a fake Kimi provider. They must opt in to
        # an injected collaborator rather than accidentally calling production AIs.
        if collaboration is None and provider_call is None:
            from .creation_collaboration import CreationResearchCollaboration
            collaboration = CreationResearchCollaboration()
        self.collaboration = collaboration

    def run(self, symbol, timeframe, direction, brief, instruction_id,
            max_optimization_retries=3, dry_run_notifications=False,
            test_mode=False, max_conditional_extra_retries=2,
            max_mechanism_generations=None, live_parent=None):
        max_optimization_retries = max(
            0,
            min(
                int(max_optimization_retries if max_optimization_retries is not None else 3),
                GUARANTEED_OPTIMIZATION_RETRIES,
            ),
        )
        max_conditional_extra_retries = max(
            0,
            min(
                int(max_conditional_extra_retries or 0),
                MAX_CONDITIONAL_EXTRA_RETRIES,
            ),
        )
        guaranteed_evaluations = 1 + max_optimization_retries
        absolute_evaluation_limit = (
            guaranteed_evaluations + max_conditional_extra_retries
        )
        current_max_evaluations = guaranteed_evaluations
        tool_bundle = build_creator_tool_bundle(symbol, timeframe, live_parent=live_parent)
        if not tool_bundle.get("ok"):
            return self._technical_failure(
                instruction_id, symbol, timeframe, direction, brief,
                tool_bundle.get("error") or "tool_bundle_failed", tool_bundle,
            )
        candles = tool_bundle.pop("_candles")
        factor_matrix = tool_bundle.pop("_factor_matrix", {})
        blind_oos_candles = tool_bundle.pop("_blind_oos_candles", [])
        blind_oos_start_index = tool_bundle.pop("_blind_oos_start_index", None)
        full_data_fingerprint = tool_bundle.pop("_full_data_fingerprint", None)
        blind_oos_split_id = tool_bundle.pop("_blind_oos_split_id", None)
        public_tools = _public_tool_bundle(tool_bundle)
        attempts = []
        technical_events = []
        last_candidate = None
        last_evaluation = None
        creator_diagnostics = []
        strategy_identity = None
        collaboration_history = []
        initial_collaboration = None
        sandbox_instruction = {
            "instruction_id": instruction_id,
            "brief": brief,
            "requested_symbol": symbol,
            "requested_timeframe": timeframe,
            "direction_preference": direction,
            "live_parent": live_parent,
        }
        hypothesis_sandbox = _run_hypothesis_sandbox(
            self.provider_call, self.collaboration, sandbox_instruction,
            public_tools, candles,
            volume_is_proxy=bool(
                (public_tools.get("data_scope") or {}).get("volume_is_proxy")
            ),
        )
        if not hypothesis_sandbox.get("ok"):
            return self._technical_failure(
                instruction_id, symbol, timeframe, direction, brief,
                "hypothesis_sandbox_failed", hypothesis_sandbox,
                attempts=attempts, tool_bundle=public_tools,
            )
        sandbox_selected_candidate = hypothesis_sandbox.get("selected_candidate_raw")
        initial_collaboration = hypothesis_sandbox.get("falsification")
        if initial_collaboration:
            collaboration_history.append(initial_collaboration)
        creator_diagnostics.append({
            "stage": "pre_candidate_hypothesis_sandbox",
            "hypothesis_sandbox": hypothesis_sandbox,
        })
        _report_progress(
            "creator_tools_ready",
            "假设沙箱已完成：%s提出机制、独立合作者反证、同源探针回放、当前创造者独立选定正式候选" % self.creator_label,
            25,
        )

        evidence_evaluation_cache = {}
        mechanism_generation = 1
        configured_mechanism_generations = (
            max_mechanism_generations
            if max_mechanism_generations is not None
            else os.environ.get("QIYU_MAX_CREATOR_MECHANISM_BRANCHES")
            or MAX_MECHANISM_GENERATIONS
        )
        maximum_mechanism_generations = (
            1 if test_mode else max(1, min(MAX_MECHANISM_GENERATIONS, int(
                configured_mechanism_generations
            )))
        )
        strategy_start_evaluation_number = 1
        job_started = time.time()
        job_time_budget = None if test_mode else _job_time_budget_seconds()

        def evidence_evaluator(variant):
            cache_key = hashlib.sha256(json.dumps(
                variant, sort_keys=True, ensure_ascii=False,
            ).encode("utf-8")).hexdigest()
            if cache_key not in evidence_evaluation_cache:
                evidence_evaluation_cache[cache_key] = _run_candidate(
                    variant, candles,
                    volume_is_proxy=bool(
                        (public_tools.get("data_scope") or {}).get("volume_is_proxy")
                    ),
                )
            return evidence_evaluation_cache[cache_key]

        oos_identity_tests = 0

        def _launch_new_mechanism(
                evidence, diagnostics_stage, progress_stage, progress_zh,
                extras=None):
            nonlocal mechanism_generation, strategy_start_evaluation_number
            nonlocal current_max_evaluations, absolute_evaluation_limit
            nonlocal sandbox_selected_candidate, initial_collaboration
            nonlocal collaboration_history, strategy_identity
            nonlocal last_candidate, last_evaluation, evaluation_number
            nonlocal hypothesis_sandbox
            _report_progress(
                progress_stage, progress_zh,
                min(88, 45 + evaluation_number * 8),
                extras=extras or {},
            )
            restart_instruction = dict(sandbox_instruction)
            restart_instruction["prior_mechanism_failure_evidence"] = evidence
            next_sandbox = _run_hypothesis_sandbox(
                self.provider_call, self.collaboration, restart_instruction,
                public_tools, candles,
                volume_is_proxy=bool(
                    (public_tools.get("data_scope") or {}).get("volume_is_proxy")
                ),
            )
            if not next_sandbox.get("ok"):
                return self._technical_failure(
                    instruction_id, symbol, timeframe, direction, brief,
                    "mechanism_restart_sandbox_failed", next_sandbox,
                    attempts=attempts, tool_bundle=public_tools,
                )
            mechanism_generation += 1
            strategy_start_evaluation_number = evaluation_number + 1
            current_max_evaluations = (
                strategy_start_evaluation_number + guaranteed_evaluations - 1
            )
            absolute_evaluation_limit = (
                current_max_evaluations + max_conditional_extra_retries
            )
            sandbox_selected_candidate = next_sandbox.get("selected_candidate_raw")
            hypothesis_sandbox = next_sandbox
            initial_collaboration = next_sandbox.get("falsification")
            collaboration_history = (
                [initial_collaboration] if initial_collaboration else []
            )
            creator_diagnostics.append({
                "stage": diagnostics_stage,
                "mechanism_generation": mechanism_generation,
                "hypothesis_sandbox": next_sandbox,
                "oos_identity_tests": oos_identity_tests,
            })
            strategy_identity = None
            last_candidate = None
            last_evaluation = None
            evaluation_number += 1
            return None

        evaluation_number = 1
        while evaluation_number <= current_max_evaluations:
            if (
                    job_time_budget is not None
                    and (time.time() - job_started) >= job_time_budget
            ):
                elapsed = time.time() - job_started
                technical_events.append({
                    "stage": "job_time_budget",
                    "error": "job_time_budget_exhausted",
                    "max_seconds": job_time_budget,
                    "elapsed_seconds": round(elapsed, 1),
                    "evaluation_number": evaluation_number,
                    "mechanism_generation": mechanism_generation,
                    "at": _now(),
                })
                _report_progress(
                    "kimi_creating",
                    "本任务已达资源时限（%.0f分钟），释放管道给排队任务" % (
                        job_time_budget / 60.0,
                    ),
                    min(92, 38 + evaluation_number * 4),
                    extras={
                        "stop_reason": "job_time_budget_exhausted",
                        "evaluation_number": evaluation_number,
                        "strategy_count": mechanism_generation,
                    },
                )
                if last_candidate is not None:
                    return self._quality_failure(
                        instruction_id, brief, last_candidate, last_evaluation,
                        evaluation_number, attempts, public_tools,
                        technical_events, creator_diagnostics,
                        max_optimization_retries=max(0, len(attempts) - 1),
                        dry_run=dry_run_notifications or test_mode,
                    )
                return self._technical_failure(
                    instruction_id, symbol, timeframe, direction, brief,
                    "job_time_budget_exhausted", technical_events,
                    attempts=attempts, tool_bundle=public_tools,
                )
            optimization_retry_number = (
                evaluation_number - strategy_start_evaluation_number
            )
            strategy_retry_cap = max(
                0, current_max_evaluations - strategy_start_evaluation_number,
            )
            progress_detail = _kimi_progress_detail(
                self.creator_label,
                optimization_retry_number,
                strategy_retry_cap,
                mechanism_generation,
                maximum_mechanism_generations,
            )
            _report_progress(
                "kimi_creating",
                progress_detail,
                25 + optimization_retry_number * 18,
                extras={
                    "evaluation_number": evaluation_number,
                    "optimization_retry_number": optimization_retry_number,
                    "guaranteed_optimization_retries": max_optimization_retries,
                    "current_strategy_retry_cap": strategy_retry_cap,
                    "current_maximum_optimization_retries": strategy_retry_cap,
                    "absolute_maximum_optimization_retries": (
                        guaranteed_evaluations + max_conditional_extra_retries - 1
                    ),
                    "conditional_extra_retries_available": max_conditional_extra_retries,
                    "current_quality_evaluation_limit": current_max_evaluations,
                    "absolute_quality_evaluation_limit": absolute_evaluation_limit,
                    "strategy_count": mechanism_generation,
                    "maximum_strategy_count": maximum_mechanism_generations,
                },
            )
            # A retry needs its own mechanism lineage, not every earlier failed
            # strategy.  Old behaviour replayed all prior branches and grew a
            # late Kimi request beyond 600k tokens, causing 429/504 failures.
            current_branch_attempts = [
                row for row in attempts
                if int(row.get("mechanism_generation") or 1)
                == int(mechanism_generation)
            ]
            prior_feedback = [
                _feedback(row) for row in current_branch_attempts[-2:]
            ]
            lifecycle = {
                "mode": (
                    "create_one_base_strategy" if optimization_retry_number == 0
                    else "optimize_same_strategy"
                ),
                "strategy_count": mechanism_generation,
                "maximum_strategy_count": maximum_mechanism_generations,
                "mechanism_generation": mechanism_generation,
                "evaluation_number": evaluation_number,
                "optimization_retry_number": optimization_retry_number,
                "guaranteed_optimization_retries": max_optimization_retries,
                "current_maximum_optimization_retries": strategy_retry_cap,
                "absolute_maximum_optimization_retries": (
                    guaranteed_evaluations + max_conditional_extra_retries - 1
                ),
                "same_candidate_id_required": optimization_retry_number > 0,
                "base_strategy_identity": strategy_identity,
                "latest_candidate_exactly_as_submitted": last_candidate,
                "rule_zh": (
                    "初次创造一个策略；失败后保证优化同一策略三次。只有客观证据达到"
                    "接近门槛条件，系统才逐次追加、最多两次；禁止用新策略替换原策略消耗额度。"
                ),
            }
            payload = {
                "instruction": {
                    "instruction_id": instruction_id,
                    "brief": brief,
                    "requested_symbol": symbol,
                    "requested_timeframe": timeframe,
                    "timeframe_role": timeframe_role(timeframe),
                    "timeframe_policy": public_policy(),
                    "direction_preference": direction,
                    "evaluation_number": evaluation_number,
                    "optimization_retry_number": optimization_retry_number,
                    "guaranteed_optimization_retries": max_optimization_retries,
                    "current_maximum_optimization_retries": strategy_retry_cap,
                    "current_maximum_quality_evaluations": current_max_evaluations,
                    "absolute_maximum_quality_evaluations": absolute_evaluation_limit,
                    "mechanism_generation": mechanism_generation,
                },
                "strategy_lifecycle": lifecycle,
                "read_only_tool_bundle": public_tools,
                "previous_attempt_feedback": prior_feedback,
                "research_collaboration": (
                    initial_collaboration if optimization_retry_number == 0
                    else (collaboration_history[-1] if collaboration_history else None)
                ),
                "collaboration_rule_zh": (
                    "研究合作者只有建议权。你必须独立核对其证据路径，记录采纳与拒绝，"
                    "最终候选只能由你输出；协作者不可替代10项质检。"
                ),
                "required_output_shape": _candidate_shape(),
            }
            if optimization_retry_number > 0 and attempts:
                from .optimization_evidence import execute_requested_structural_probes
                prior = attempts[-1]
                probe_plan = _kimi_structural_probe_plan(
                    self.provider_call,
                    payload.get("instruction"),
                    prior.get("candidate"),
                    ((prior.get("evaluation") or {}).get("quality_report")),
                    _compact_optimization_evidence(
                        prior.get("optimization_evidence")
                    ),
                    prior.get("post_failure_collaboration"),
                    public_tools,
                )
                probe_results = execute_requested_structural_probes(
                    prior.get("candidate"), probe_plan.get("requests"),
                    evidence_evaluator, maximum_requests=8,
                )
                prior["creator_requested_structural_probe_plan"] = probe_plan
                prior["creator_requested_structural_probe_results"] = probe_results
                payload["creator_requested_structural_probe_results_unranked"] = probe_results
                payload["probe_use_rule_zh"] = (
                    "这些是你在正式重试前请求的同源事实。它们不排序、不自动改写策略；"
                    "请只在形成新的可证伪结构假设时使用。"
                )
            candidate = None
            request_meta = None
            transport_history = []
            for repair_number in range(MAX_TECHNICAL_REPAIRS_PER_ATTEMPT + 1):
                if optimization_retry_number == 0 and repair_number == 0:
                    response = {
                        "ok": True,
                        "candidate": sandbox_selected_candidate,
                        "request_id": (
                            (hypothesis_sandbox.get("selection_request") or {}).get(
                                "request_id"
                            )
                        ),
                        "model": (
                            (hypothesis_sandbox.get("selection_request") or {}).get("model")
                        ),
                        "usage": (
                            (hypothesis_sandbox.get("selection_request") or {}).get("usage")
                        ),
                    }
                else:
                    response, transport_history = _provider_call_with_bounded_transport_retries(
                        self.provider_call,
                        _creator_system_prompt(), payload,
                        max_tokens=14000,
                        temperature=0.62 if optimization_retry_number == 0 else 0.42,
                        maximum_retries=MAX_TECHNICAL_REPAIRS_PER_ATTEMPT,
                    )
                if response.get("ok"):
                    response = dict(response)
                    response["candidate"] = _unwrap_provider_object(
                        response.get("candidate")
                    )
                request_meta = {
                    "request_id": response.get("request_id"),
                    "model": response.get("model"), "usage": response.get("usage"),
                    "repair_number": repair_number,
                    "transport_history": transport_history if optimization_retry_number != 0 or repair_number != 0 else [],
                }
                if not response.get("ok"):
                    error_text = str(response.get("error") or "")
                    retryable_transport_failure = _transport_retryable(error_text)
                    backoff_seconds = None
                    if (
                        retryable_transport_failure
                        and repair_number < MAX_TECHNICAL_REPAIRS_PER_ATTEMPT
                    ):
                        # Provider capacity errors are technical, not creative
                        # quality attempts.  Back off finitely; never poll until
                        # a desired strategy answer appears.
                        backoff_seconds = _transport_backoff_seconds(
                            error_text, repair_number
                        )
                    technical_events.append({
                        "evaluation_number": evaluation_number,
                        "optimization_retry_number": optimization_retry_number,
                        "repair_number": repair_number,
                        "stage": "%s_provider" % self.creator_provider,
                        "provider": self.creator_provider,
                        "error": response.get("error"),
                        "retryable_transport_failure": retryable_transport_failure,
                        "bounded_backoff_seconds": backoff_seconds,
                        "at": _now(),
                    })
                    if repair_number >= MAX_TECHNICAL_REPAIRS_PER_ATTEMPT:
                        return self._technical_failure(
                            instruction_id, symbol, timeframe, direction, brief,
                            "%s_provider_failed" % self.creator_provider,
                            technical_events,
                            attempts=attempts, tool_bundle=public_tools,
                        )
                    if backoff_seconds:
                        time.sleep(backoff_seconds)
                    continue
                validation = _validate_candidate(
                    response.get("candidate"), symbol, timeframe,
                    direction=direction,
                    is_optimization_retry=optimization_retry_number > 0,
                    expected_candidate_id=(
                        (strategy_identity or {}).get("candidate_id")
                        if optimization_retry_number > 0 else None
                    ),
                    expected_base_features=(
                        (strategy_identity or {}).get("base_entry_features")
                        if optimization_retry_number > 0 else None
                    ),
                )
                if validation.get("blocked"):
                    blocked = validation.get("candidate") or {}
                    # Tool bundle is already in this request.  A blocked status
                    # here is a contract mistake, not a missing-data halt.
                    technical_events.append({
                        "evaluation_number": evaluation_number,
                        "optimization_retry_number": optimization_retry_number,
                        "repair_number": repair_number,
                        "stage": "false_blocked_with_tools_already_provided",
                        "errors": ["status_blocked_forbidden_when_tool_bundle_present"],
                        "blocked_reason": blocked.get("reason") or blocked.get("reason_zh"),
                        "at": _now(),
                    })
                    if repair_number >= MAX_TECHNICAL_REPAIRS_PER_ATTEMPT:
                        return self._technical_failure(
                            instruction_id, symbol, timeframe, direction, brief,
                            "candidate_contract_unrepairable", technical_events,
                            attempts=attempts, tool_bundle=public_tools,
                        )
                    payload = dict(payload)
                    payload["technical_contract_errors"] = [
                        "status_blocked_forbidden_when_tool_bundle_present"
                    ]
                    payload["rejected_candidate_exactly_as_received"] = blocked
                    payload["instruction_to_creator"] = (
                        "read_only_tool_bundle 已在本请求中提供正式因子分布、语法和数据范围。"
                        "禁止输出 status=blocked。必须输出完整 status=candidate 对象。"
                    )
                    continue
                if validation.get("ok"):
                    candidate = validation.get("candidate")
                    candidate["_creator_provider"] = self.creator_provider
                    candidate["_creator_label"] = self.creator_label
                    creator_diagnostics.append({
                        "evaluation_number": evaluation_number,
                        "optimization_retry_number": optimization_retry_number,
                        "tool_limitations_zh": candidate.get("tool_limitations_zh") or [],
                        "missing_materials_zh": candidate.get("missing_materials_zh") or [],
                    })
                    break
                technical_events.append({
                    "evaluation_number": evaluation_number,
                    "optimization_retry_number": optimization_retry_number,
                    "repair_number": repair_number,
                    "stage": "candidate_contract", "errors": validation.get("errors") or [],
                    "at": _now(),
                })
                if repair_number >= MAX_TECHNICAL_REPAIRS_PER_ATTEMPT:
                    return self._technical_failure(
                        instruction_id, symbol, timeframe, direction, brief,
                        "candidate_contract_unrepairable", technical_events,
                        attempts=attempts, tool_bundle=public_tools,
                    )
                payload = dict(payload)
                payload["technical_contract_errors"] = validation.get("errors") or []
                payload["rejected_candidate_exactly_as_received"] = response.get("candidate")
                payload["received_shape"] = _compact_received_shape(response.get("candidate"))
                payload["instruction_to_creator"] = (
                    "这是语法/能力契约错误，不计创造次数。上方已原样附上你刚才提交的对象。"
                    "禁止输出hypotheses数组或choice对象；必须输出单个status=candidate的完整策略。"
                    "若这是优化重试，candidate_id必须与base_strategy_identity完全一致；"
                    "entry_ast必须是非空对象，all/any必须含完整children。"
                    "若错误含 structure_cannot_read_trend：必须换能判断趋势方向与幅度的机制，"
                    "禁止继续用 rsi/z 超卖、二次探底、反转或无趋势布林轨；本地程序不会替你改写。"
                )

            preflight_record = None
            if optimization_retry_number == 0:
                from .optimization_evidence import build_non_gating_preflight
                from .creator_feasibility import build as build_return_feasibility

                draft_candidate = candidate
                raw_preflight = build_non_gating_preflight(
                    draft_candidate,
                    factor_matrix,
                    public_tools.get("formal_factor_manifest") or {},
                    draft_candidate.get("protective_stop_pct"),
                )
                draft_probe = _run_candidate(
                    draft_candidate, candles,
                    volume_is_proxy=bool(
                        (public_tools.get("data_scope") or {}).get("volume_is_proxy")
                    ),
                )
                raw_preflight["return_structure_feasibility"] = build_return_feasibility(
                    draft_candidate,
                    public_tools.get("formal_factor_manifest") or {},
                    probe_evaluation=draft_probe,
                )
                from .optimization_evidence import (
                    exit_and_excursion_audit,
                    friction_sensitivity_diagnostic,
                    holding_structure_diagnostics,
                    market_regime_slices,
                    quality_constraint_ledger,
                    return_geometry_reconciliation,
                    trade_concentration_diagnostics,
                )
                raw_preflight["quality_constraint_ledger"] = (
                    quality_constraint_ledger(draft_probe)
                )
                raw_preflight["holding_structure_diagnostics"] = (
                    holding_structure_diagnostics(
                        (draft_probe or {}).get("backtest") or {}
                    )
                )
                raw_preflight["exit_and_excursion_audit"] = (
                    exit_and_excursion_audit((draft_probe or {}).get("backtest") or {})
                )
                raw_preflight["return_geometry_reconciliation"] = (
                    return_geometry_reconciliation(
                        (draft_probe or {}).get("backtest") or {}, draft_probe,
                    )
                )
                raw_preflight["friction_sensitivity_diagnostic_only"] = (
                    friction_sensitivity_diagnostic(
                        (draft_probe or {}).get("backtest") or {}
                    )
                )
                raw_preflight["market_environment_stratification"] = (
                    market_regime_slices(
                        (draft_probe or {}).get("backtest") or {}, factor_matrix,
                    )
                )
                raw_preflight["trade_concentration_diagnostics"] = (
                    trade_concentration_diagnostics(
                        (draft_probe or {}).get("backtest") or {}
                    )
                )
                preflight_collaboration = None
                if self.collaboration is not None and hasattr(
                        self.collaboration, "review_preflight"):
                    try:
                        preflight_collaboration = self.collaboration.review_preflight(
                            {
                                "instruction_id": instruction_id,
                                "brief": brief,
                                "requested_symbol": symbol,
                                "requested_timeframe": timeframe,
                                "direction_preference": direction,
                            },
                            draft_candidate,
                            raw_preflight,
                        )
                        collaboration_history.append(preflight_collaboration)
                    except Exception as exc:
                        preflight_collaboration = {
                            "advisory_only": True,
                            "not_a_gate_or_vote": True,
                            "available_count": 0,
                            "error": "%s:%s" % (type(exc).__name__, str(exc)[:240]),
                        }
                        technical_events.append({
                            "evaluation_number": evaluation_number,
                            "stage": "research_collaboration_preflight",
                            "non_blocking": True,
                            "error": preflight_collaboration.get("error"),
                            "at": _now(),
                        })
                preflight_payload = dict(payload)
                preflight_payload.update({
                    "mode": "non_gating_draft_preflight_refinement",
                    "draft_candidate_exactly_as_authored": draft_candidate,
                    "raw_preflight_facts": raw_preflight,
                    "preflight_research_collaboration": preflight_collaboration,
                    "instruction_to_final_author_zh": (
                        "这是正式第一次10项质检前的一次非阻断事实预览，不是额外门禁。"
                        "请独立核对条件联合命中数、逐条件漏斗、最低毛收益、止损×止盈平衡面、"
                        "ATR可达性与同源精确探针。若当前退出几何需要不现实胜率，应由你主动重构；"
                        "你可以修改或原样保留草案。不得声称胜率或收益，candidate_id必须不变，"
                        "最终完整候选仍只能由你输出。"
                    ),
                })
                preflight_response, _preflight_transport = (
                    _provider_call_with_bounded_transport_retries(
                        self.provider_call,
                        _creator_system_prompt(), preflight_payload,
                        max_tokens=14000, temperature=0.38,
                        maximum_retries=MAX_TECHNICAL_REPAIRS_PER_ATTEMPT,
                    )
                )
                preflight_meta = {
                    "request_id": preflight_response.get("request_id"),
                    "model": preflight_response.get("model"),
                    "usage": preflight_response.get("usage"),
                    "non_gating": True,
                }
                if preflight_response.get("ok"):
                    refined = _validate_candidate(
                        _unwrap_provider_object(preflight_response.get("candidate")), symbol, timeframe,
                        direction=direction,
                        expected_candidate_id=draft_candidate.get("candidate_id"),
                        expected_base_features=None,
                        is_optimization_retry=False,
                    )
                    if refined.get("ok"):
                        candidate = refined.get("candidate")
                    else:
                        technical_events.append({
                            "evaluation_number": evaluation_number,
                            "stage": "non_gating_preflight_refinement_contract",
                            "non_blocking": True,
                            "errors": refined.get("errors") or [],
                            "at": _now(),
                        })
                else:
                    technical_events.append({
                        "evaluation_number": evaluation_number,
                        "stage": "non_gating_preflight_refinement_provider",
                        "non_blocking": True,
                        "error": preflight_response.get("error"),
                        "at": _now(),
                    })
                final_preflight = build_non_gating_preflight(
                    candidate,
                    factor_matrix,
                    public_tools.get("formal_factor_manifest") or {},
                    candidate.get("protective_stop_pct"),
                )
                final_probe = _run_candidate(
                    candidate, candles,
                    volume_is_proxy=bool(
                        (public_tools.get("data_scope") or {}).get("volume_is_proxy")
                    ),
                )
                final_preflight["return_structure_feasibility"] = build_return_feasibility(
                    candidate,
                    public_tools.get("formal_factor_manifest") or {},
                    probe_evaluation=final_probe,
                )
                final_preflight["quality_constraint_ledger"] = (
                    quality_constraint_ledger(final_probe)
                )
                final_preflight["holding_structure_diagnostics"] = (
                    holding_structure_diagnostics(
                        (final_probe or {}).get("backtest") or {}
                    )
                )
                final_preflight["exit_and_excursion_audit"] = (
                    exit_and_excursion_audit((final_probe or {}).get("backtest") or {})
                )
                final_preflight["return_geometry_reconciliation"] = (
                    return_geometry_reconciliation(
                        (final_probe or {}).get("backtest") or {}, final_probe,
                    )
                )
                final_preflight["friction_sensitivity_diagnostic_only"] = (
                    friction_sensitivity_diagnostic(
                        (final_probe or {}).get("backtest") or {}
                    )
                )
                final_preflight["market_environment_stratification"] = (
                    market_regime_slices(
                        (final_probe or {}).get("backtest") or {}, factor_matrix,
                    )
                )
                final_preflight["trade_concentration_diagnostics"] = (
                    trade_concentration_diagnostics(
                        (final_probe or {}).get("backtest") or {}
                    )
                )
                # The refinement itself is another Kimi-authored version.  The
                # engine must not silently count it as the first formal attempt
                # before its measured consequences are shown to the author.
                # Present both known versions without ranking; Kimi chooses.
                refined_candidate = candidate
                refined_preflight = final_preflight
                comparison_payload = {
                    "mode": "author_choose_preflight_version_after_exact_comparison",
                    "instruction": {
                        "instruction_id": instruction_id,
                        "brief": brief,
                        "requested_symbol": symbol,
                        "requested_timeframe": timeframe,
                    },
                    "draft_version": {
                        "candidate": draft_candidate,
                        "quality_constraint_ledger": raw_preflight.get(
                            "quality_constraint_ledger"
                        ),
                        "holding_structure_diagnostics": raw_preflight.get(
                            "holding_structure_diagnostics"
                        ),
                        "exit_and_excursion_audit": raw_preflight.get(
                            "exit_and_excursion_audit"
                        ),
                        "return_geometry_reconciliation": raw_preflight.get(
                            "return_geometry_reconciliation"
                        ),
                        "friction_sensitivity_diagnostic_only": raw_preflight.get(
                            "friction_sensitivity_diagnostic_only"
                        ),
                        "market_environment_stratification": raw_preflight.get(
                            "market_environment_stratification"
                        ),
                        "trade_concentration_diagnostics": raw_preflight.get(
                            "trade_concentration_diagnostics"
                        ),
                        "return_structure_feasibility": raw_preflight.get(
                            "return_structure_feasibility"
                        ),
                    },
                    "refined_version": {
                        "candidate": refined_candidate,
                        "quality_constraint_ledger": refined_preflight.get(
                            "quality_constraint_ledger"
                        ),
                        "holding_structure_diagnostics": refined_preflight.get(
                            "holding_structure_diagnostics"
                        ),
                        "exit_and_excursion_audit": refined_preflight.get(
                            "exit_and_excursion_audit"
                        ),
                        "return_geometry_reconciliation": refined_preflight.get(
                            "return_geometry_reconciliation"
                        ),
                        "friction_sensitivity_diagnostic_only": refined_preflight.get(
                            "friction_sensitivity_diagnostic_only"
                        ),
                        "market_environment_stratification": refined_preflight.get(
                            "market_environment_stratification"
                        ),
                        "trade_concentration_diagnostics": refined_preflight.get(
                            "trade_concentration_diagnostics"
                        ),
                        "return_structure_feasibility": refined_preflight.get(
                            "return_structure_feasibility"
                        ),
                    },
                    "required_output_shape": {
                        "choice": "draft|refined",
                        "reason_zh": "基于全部10项缺口与安全边际的可证伪理由",
                    },
                    "rule_zh": (
                        "两版都由你创建，程序不排序、不推荐、不自动回退。请亲自决定哪一版进入"
                        "第一次正式质检。不得只看E_raw；必须同时保护已通过指标并解释权衡。"
                    ),
                }
                comparison_response, _comparison_transport = (
                    _provider_call_with_bounded_transport_retries(
                        self.provider_call,
                        _creator_system_prompt(), comparison_payload,
                        max_tokens=3000, temperature=0.16,
                        maximum_retries=MAX_TECHNICAL_REPAIRS_PER_ATTEMPT,
                    )
                )
                comparison_answer = (
                    comparison_response.get("candidate") or {}
                    if comparison_response.get("ok") else {}
                )
                author_choice = str(
                    comparison_answer.get("choice") or ""
                ).strip().lower()
                if author_choice == "draft":
                    candidate = draft_candidate
                    final_preflight = raw_preflight
                    final_probe = draft_probe
                elif author_choice == "refined":
                    candidate = refined_candidate
                else:
                    # Preserve the last explicitly authored complete candidate;
                    # malformed comparison metadata does not spend a formal
                    # attempt and never lets local code choose performance.
                    author_choice = "refined_default_on_invalid_comparison_metadata"
                    technical_events.append({
                        "evaluation_number": evaluation_number,
                        "stage": "non_gating_preflight_author_choice_metadata",
                        "non_blocking": True,
                        "error": comparison_response.get("error") or
                                 "choice_must_be_draft_or_refined",
                        "at": _now(),
                    })
                comparison_meta = {
                    "request_id": comparison_response.get("request_id"),
                    "model": comparison_response.get("model"),
                    "usage": comparison_response.get("usage"),
                    "ok": bool(comparison_response.get("ok")),
                    "author_choice": author_choice,
                    "author_reason_zh": comparison_answer.get("reason_zh"),
                    "program_ranking_or_selection": False,
                    "counted_as_formal_submission": False,
                }
                preflight_record = {
                    "non_gating": True,
                    "draft_candidate": draft_candidate,
                    "draft_raw_facts": raw_preflight,
                    "research_collaboration": preflight_collaboration,
                    "refined_candidate": refined_candidate,
                    "refined_raw_facts": refined_preflight,
                    "final_candidate": candidate,
                    "final_raw_facts": final_preflight,
                    "creator_request": preflight_meta,
                    "author_version_choice": comparison_meta,
                    "system_selected_or_blocked": False,
                    "same_engine_probe_counted_as_formal_submission": False,
                }
                request_meta["preflight"] = preflight_meta
                request_meta["preflight_author_version_choice"] = comparison_meta

            if optimization_retry_number > 0:
                # Each retry receives exactly one development-only exact
                # preview and Kimi receives exactly one chance to revise it.
                # There is no loop, no ranking, no automatic quality decision,
                # and no OOS access.  This prevents a zero-trade or otherwise
                # unintended edit from consuming a formal evaluation blindly.
                from .optimization_evidence import (
                    exit_and_excursion_audit,
                    build_non_gating_preflight,
                    friction_sensitivity_diagnostic,
                    holding_structure_diagnostics,
                    market_regime_slices,
                    quality_constraint_ledger,
                    return_geometry_reconciliation,
                    trade_concentration_diagnostics,
                )
                from .creator_feasibility import build as build_return_feasibility

                retry_draft_candidate = candidate
                retry_draft_probe = _run_candidate(
                    retry_draft_candidate, candles,
                    volume_is_proxy=bool(
                        (public_tools.get("data_scope") or {}).get("volume_is_proxy")
                    ),
                )
                retry_facts = build_non_gating_preflight(
                    retry_draft_candidate,
                    factor_matrix,
                    public_tools.get("formal_factor_manifest") or {},
                    retry_draft_candidate.get("protective_stop_pct"),
                )
                retry_facts["return_structure_feasibility"] = (
                    build_return_feasibility(
                        retry_draft_candidate,
                        public_tools.get("formal_factor_manifest") or {},
                        probe_evaluation=retry_draft_probe,
                    )
                )
                retry_facts["quality_constraint_ledger"] = (
                    quality_constraint_ledger(retry_draft_probe)
                )
                retry_facts["holding_structure_diagnostics"] = (
                    holding_structure_diagnostics(
                        (retry_draft_probe or {}).get("backtest") or {}
                    )
                )
                retry_facts["exit_and_excursion_audit"] = (
                    exit_and_excursion_audit(
                        (retry_draft_probe or {}).get("backtest") or {}
                    )
                )
                retry_facts["return_geometry_reconciliation"] = (
                    return_geometry_reconciliation(
                        (retry_draft_probe or {}).get("backtest") or {},
                        retry_draft_probe,
                    )
                )
                retry_facts["friction_sensitivity_diagnostic_only"] = (
                    friction_sensitivity_diagnostic(
                        (retry_draft_probe or {}).get("backtest") or {}
                    )
                )
                retry_facts["market_environment_stratification"] = (
                    market_regime_slices(
                        (retry_draft_probe or {}).get("backtest") or {},
                        factor_matrix,
                    )
                )
                retry_facts["trade_concentration_diagnostics"] = (
                    trade_concentration_diagnostics(
                        (retry_draft_probe or {}).get("backtest") or {}
                    )
                )
                retry_preflight_payload = {
                    "mode": "one_bounded_retry_preflight_before_formal_count",
                    "instruction": payload.get("instruction"),
                    "strategy_lifecycle": lifecycle,
                    "retry_candidate_exactly_as_authored": retry_draft_candidate,
                    "one_same_engine_development_preview": retry_facts,
                    "previous_attempt_feedback": prior_feedback[-1:] if prior_feedback else [],
                    "research_collaboration": payload.get("research_collaboration"),
                    "required_output_shape": _candidate_shape(),
                    "rule_zh": (
                        "这是本次优化唯一一次、仅开发集的正式计数前预览，不是质检或投票。"
                        "请核对是否出现0成交、成本算术错误或意外破坏已通过指标；你可原样提交或"
                        "仅修订一次完整候选。程序不会排名、拒绝、回退或替你选参数；你的这次输出"
                        "将直接进入本轮唯一正式质检。本轮不得再次预览，OOS始终不可见。"
                    ),
                }
                retry_preflight_response, retry_transport_history = (
                    _provider_call_with_bounded_transport_retries(
                        self.provider_call,
                        _creator_system_prompt(), retry_preflight_payload,
                        max_tokens=14000, temperature=0.30,
                        maximum_retries=MAX_TECHNICAL_REPAIRS_PER_ATTEMPT,
                    )
                )
                retry_preflight_meta = {
                    "request_id": retry_preflight_response.get("request_id"),
                    "model": retry_preflight_response.get("model"),
                    "usage": retry_preflight_response.get("usage"),
                    "ok": bool(retry_preflight_response.get("ok")),
                    "error": retry_preflight_response.get("error"),
                    "preview_count": 1,
                    "revision_opportunities": 1,
                    "counted_as_formal_submission": False,
                    "program_ranking_selection_or_rejection": False,
                    "oos_exposed": False,
                    "bounded_transport_history": retry_transport_history,
                }
                if retry_preflight_response.get("ok"):
                    retry_revision = _validate_candidate(
                        retry_preflight_response.get("candidate"), symbol, timeframe,
                        direction=direction,
                        expected_candidate_id=(strategy_identity or {}).get("candidate_id"),
                        expected_base_features=(strategy_identity or {}).get(
                            "base_entry_features"
                        ),
                        is_optimization_retry=True,
                    )
                    if retry_revision.get("ok"):
                        candidate = retry_revision.get("candidate")
                        retry_preflight_meta["kimi_revision_technically_valid"] = True
                    else:
                        retry_preflight_meta["kimi_revision_technically_valid"] = False
                        retry_preflight_meta["technical_contract_errors"] = (
                            retry_revision.get("errors") or []
                        )
                        technical_events.append({
                            "evaluation_number": evaluation_number,
                            "optimization_retry_number": optimization_retry_number,
                            "stage": "bounded_retry_preflight_revision_contract",
                            "non_blocking": True,
                            "errors": retry_revision.get("errors") or [],
                            "fallback": "use_last_technically_valid_kimi_authored_retry",
                            "at": _now(),
                        })
                else:
                    technical_events.append({
                        "evaluation_number": evaluation_number,
                        "optimization_retry_number": optimization_retry_number,
                        "stage": "bounded_retry_preflight_provider",
                        "non_blocking": True,
                        "error": retry_preflight_response.get("error"),
                        "formal_quality_evaluation_consumed": False,
                        "bounded_transport_history": retry_transport_history,
                        "fallback": (
                            "continue_with_retry_candidate_already_authored_by_kimi"
                        ),
                        "at": _now(),
                    })
                    retry_preflight_meta["fallback"] = (
                        "continue_with_retry_candidate_already_authored_by_kimi"
                    )
                    retry_preflight_meta["reason_zh"] = (
                        "附加预览调用发生传输或容量错误；该预览不是门禁。"
                        "保留Kimi在本轮已独立写出的完整优化候选并继续一次正式质检。"
                    )
                preflight_record = {
                    "non_gating": True,
                    "phase": "optimization_retry",
                    "draft_candidate": retry_draft_candidate,
                    "draft_raw_facts": retry_facts,
                    "final_candidate": candidate,
                    "creator_request": retry_preflight_meta,
                    "exact_preview_count": 1,
                    "author_revision_opportunities": 1,
                    "system_selected_or_blocked": False,
                    "same_engine_probe_counted_as_formal_submission": False,
                    "oos_exposed": False,
                }
                request_meta["bounded_retry_preflight"] = retry_preflight_meta

            # Keep authorship explicit after any contract/preflight
            # normalization, which intentionally drops unknown provider fields.
            candidate["_creator_provider"] = self.creator_provider
            candidate["_creator_label"] = self.creator_label
            evaluation, backtest_retry_events = _run_candidate_with_technical_retries(
                candidate, candles,
                volume_is_proxy=bool((public_tools.get("data_scope") or {}).get("volume_is_proxy")),
            )
            for event in backtest_retry_events:
                event["evaluation_number"] = evaluation_number
                event["optimization_retry_number"] = optimization_retry_number
                technical_events.append(event)
            if not evaluation.get("ok"):
                return self._technical_failure(
                    instruction_id, symbol, timeframe, direction, brief,
                    "backtest_technical_failure", technical_events,
                    attempts=attempts, tool_bundle=public_tools,
                )
            attempt_row = {
                "attempt_number": evaluation_number,
                "evaluation_number": evaluation_number,
                "optimization_retry_number": optimization_retry_number,
                "mechanism_generation": mechanism_generation,
                "phase": (
                    "initial_creation" if optimization_retry_number == 0
                    else "same_strategy_optimization"
                ),
                "candidate": candidate,
                "creator_request": request_meta,
                "non_gating_preflight": preflight_record,
                "evaluation": evaluation,
                "is_quality_pass": bool((evaluation.get("quality_gate") or {}).get("ok")),
                "quality_pass": bool((evaluation.get("quality_gate") or {}).get("ok")),
                "at": _now(),
            }
            oos_terminal_failure = False
            if attempt_row["is_quality_pass"]:
                _report_progress(
                    "blind_oos_quality_inspection",
                    "IS 10项已通过；候选身份已冻结，正在一次性读取后30%盲测",
                    93,
                    extras={
                        "evaluation_number": evaluation_number,
                        "candidate_id": candidate.get("candidate_id"),
                        "oos_read_maximum": 1,
                        "oos_feedback_to_creator": False,
                        "oos_identity_look_index": oos_identity_tests + 1,
                        "mechanism_generation": mechanism_generation,
                    },
                )
                blind_oos = _run_blind_oos_once(
                    candidate, evaluation, blind_oos_candles,
                    blind_oos_start_index, instruction_id,
                    dataset_version=full_data_fingerprint,
                    split_id=blind_oos_split_id,
                    development_candidate_count=evaluation_number,
                    volume_is_proxy=bool(
                        (public_tools.get("data_scope") or {}).get("volume_is_proxy")
                    ),
                    test_mode=bool(test_mode),
                    lineage_root_id=oos_lineage_root_id(
                        instruction_id, mechanism_generation,
                    ),
                    oos_identity_look_index=oos_identity_tests + 1,
                    oos_identity_looks_planned=MAX_OOS_IDENTITY_TESTS,
                )
                if not blind_oos.get("ok"):
                    technical_events.append({
                        "evaluation_number": evaluation_number,
                        "stage": "blind_oos_once",
                        "error": blind_oos.get("error") or "blind_oos_failed",
                        "at": _now(),
                    })
                    return self._technical_failure(
                        instruction_id, symbol, timeframe, direction, brief,
                        "blind_oos_technical_failure", technical_events,
                        attempts=attempts, tool_bundle=public_tools,
                    )
                evaluation = dict(evaluation)
                evaluation["is_quality_gate"] = evaluation.get("quality_gate")
                evaluation["quality_gate"] = blind_oos.get("quality_gate") or {}
                evaluation["quality_report_oos"] = blind_oos.get("quality_report_oos")
                evaluation["blind_oos"] = blind_oos
                attempt_row["evaluation"] = evaluation
                attempt_row["blind_oos"] = {
                    "quality_pass": bool(blind_oos.get("quality_pass")),
                    "quality_report_oos": blind_oos.get("quality_report_oos"),
                    "frozen_candidate_receipt": blind_oos.get("frozen_candidate_receipt"),
                    "formal_quality_receipts": blind_oos.get("formal_quality_receipts"),
                    "feedback_to_creator": False,
                    "read_count": 1,
                }
                attempt_row["quality_pass"] = bool(blind_oos.get("quality_pass"))
                attempt_row["oos_identity_look_index"] = oos_identity_tests + 1
                oos_terminal_failure = not attempt_row["quality_pass"]
                if oos_terminal_failure:
                    oos_identity_tests += 1

            if not attempt_row["quality_pass"] and not oos_terminal_failure:
                from .optimization_evidence import build_optimization_evidence

                attempt_row["optimization_evidence"] = build_optimization_evidence(
                    candidate,
                    evaluation,
                    previous_attempt=attempts[-1] if attempts else None,
                    factor_matrix=factor_matrix,
                    factor_manifest=public_tools.get("formal_factor_manifest") or {},
                    evaluator=evidence_evaluator,
                    exit_modes=ALLOWED_TAKE_PROFIT_MODES,
                    maximum_replays=64,
                )
                from .creator_feasibility import build as build_return_feasibility
                attempt_row["optimization_evidence"][
                    "return_structure_feasibility"
                ] = build_return_feasibility(
                    candidate,
                    public_tools.get("formal_factor_manifest") or {},
                    probe_evaluation=evaluation,
                )
                from .failure_routing import classify as classify_failure_routing
                attempt_row["failure_routing"] = classify_failure_routing(
                    candidate,
                    evaluation,
                    attempt_row.get("optimization_evidence"),
                    previous_attempt=attempts[-1] if attempts else None,
                )
                if (
                    evaluation_number >= current_max_evaluations
                    and current_max_evaluations < absolute_evaluation_limit
                ):
                    budget = _conditional_extension_decision(
                        attempts + [attempt_row]
                    )
                    attempt_row["conditional_retry_budget"] = budget
                    if budget.get("allowed"):
                        current_max_evaluations += 1
                        attempt_row["conditional_retry_budget"][
                            "extended_to_quality_evaluation"
                        ] = current_max_evaluations
                if (
                    self.collaboration is not None
                    and evaluation_number < current_max_evaluations
                ):
                    try:
                        memo = self.collaboration.after_failure(
                            {
                                "instruction_id": instruction_id,
                                "brief": brief,
                                "requested_symbol": symbol,
                                "requested_timeframe": timeframe,
                                "direction_preference": direction,
                                "evaluation_number": evaluation_number,
                                "next_optimization_retry_number": optimization_retry_number + 1,
                            },
                            candidate,
                            evaluation.get("quality_report"),
                            attempt_row.get("optimization_evidence"),
                            previous_memos=collaboration_history,
                        )
                        attempt_row["post_failure_collaboration"] = memo
                        collaboration_history.append(memo)
                        from .optimization_evidence import execute_requested_structural_probes
                        collaborator_probe_results = execute_requested_structural_probes(
                            candidate,
                            _collaboration_probe_requests(memo),
                            evidence_evaluator,
                            maximum_requests=8,
                        )
                        attempt_row["collaborator_requested_structural_probe_results"] = (
                            collaborator_probe_results
                        )
                        attempt_row["optimization_evidence"][
                            "collaborator_requested_structural_probe_results"
                        ] = collaborator_probe_results
                    except Exception as exc:
                        memo = {
                            "advisory_only": True,
                            "not_a_gate_or_vote": True,
                            "available_count": 0,
                            "error": "%s:%s" % (type(exc).__name__, str(exc)[:240]),
                        }
                        attempt_row["post_failure_collaboration"] = memo
                        collaboration_history.append(memo)
                        technical_events.append({
                            "evaluation_number": evaluation_number,
                            "optimization_retry_number": optimization_retry_number,
                            "stage": "research_collaboration_after_failure",
                            "non_blocking": True,
                            "error": memo.get("error"),
                            "at": _now(),
                        })
            attempts.append(attempt_row)
            if strategy_identity is None:
                strategy_identity = {
                    "candidate_id": candidate.get("candidate_id"),
                    "initial_title_zh": candidate.get("title_zh"),
                    "initial_mechanism_zh": candidate.get("mechanism_zh"),
                    "symbol": candidate.get("symbol"),
                    "timeframe": candidate.get("timeframe"),
                    "direction": candidate.get("direction"),
                    "base_entry_features": sorted(_ast_features(
                        candidate.get("entry_ast") or {},
                    )),
                }
            last_candidate = candidate
            last_evaluation = evaluation
            self._save_progress(
                instruction_id, symbol, timeframe, direction, brief,
                public_tools, attempts, technical_events, creator_diagnostics,
                status="quality_pass" if attempt_row["quality_pass"] else "quality_fail",
            )
            if oos_terminal_failure:
                oos_restart_allowed = should_restart_mechanism_after_oos(
                    mechanism_generation,
                    maximum_mechanism_generations,
                    oos_identity_tests,
                )
                if oos_restart_allowed:
                    restarted = _launch_new_mechanism(
                        holdout_rejected_sandbox_evidence(candidate),
                        "holdout_rejected_returned_to_hypothesis_sandbox",
                        "blind_oos_rejected_new_mechanism",
                        (
                            "该身份未通过一次性盲OOS；不向创造者泄露样本外数字。"
                            "换机制、新身份，再给一次独立盲测（同一持有期计多次假设）"
                        ),
                        extras={
                            "evaluation_number": evaluation_number,
                            "candidate_id": candidate.get("candidate_id"),
                            "finished_mechanism_generation": mechanism_generation,
                            "oos_identity_tests": oos_identity_tests,
                            "oos_identity_tests_maximum": MAX_OOS_IDENTITY_TESTS,
                            "oos_feedback_to_creator": False,
                        },
                    )
                    if restarted is not None:
                        return restarted
                    continue
                _report_progress(
                    "blind_oos_rejected",
                    "候选未通过一次性OOS盲测；独立身份盲测额度已用尽。不向创造者泄露OOS数据、不允许据此改参重试",
                    96,
                    extras={
                        "evaluation_number": evaluation_number,
                        "candidate_id": candidate.get("candidate_id"),
                        "oos_identity_tests": oos_identity_tests,
                        "oos_failed_rules": list(
                            (((evaluation.get("quality_gate") or {}).get("oos_gate") or {}).get(
                                "reasons"
                            ) or [])
                        ),
                    },
                )
                return self._quality_failure(
                    instruction_id, brief, candidate, evaluation,
                    evaluation_number, attempts, public_tools,
                    technical_events, creator_diagnostics,
                    max_optimization_retries=current_max_evaluations - 1,
                    dry_run=dry_run_notifications or test_mode,
                )
            if (
                    not attempt_row["quality_pass"]
                    and evaluation_number >= current_max_evaluations
                    and optimization_retry_number >= GUARANTEED_OPTIMIZATION_RETRIES
                    and mechanism_generation < maximum_mechanism_generations):
                restarted = _launch_new_mechanism(
                    {
                        "candidate": candidate,
                        "quality_report_10": evaluation.get("quality_report"),
                        "failure_routing": attempt_row.get("failure_routing"),
                        "optimization_evidence": attempt_row.get("optimization_evidence"),
                        "rule_zh": (
                            "旧策略身份已获得初次质检和三次以上优化，不得只换一组参数重新包装；"
                            "提出机制不同的新方向，并明确规避已证实的失败结构。程序不排序，仍由你选择。"
                        ),
                    },
                    "mechanism_failure_returned_to_hypothesis_sandbox",
                    "mechanism_return_to_sandbox",
                    "当前策略已获得初次质检和至少三次完整优化仍未通过；保留失败证据并返回假设沙箱，由当前创造者选择新机制",
                    extras={
                        "finished_mechanism_generation": mechanism_generation,
                        "failure_routing": attempt_row.get("failure_routing"),
                        "formal_identity_received_full_retry_budget": True,
                    },
                )
                if restarted is not None:
                    return restarted
                continue
            _report_progress(
                "quality_inspection",
                "%s已完成10项数据质检：%s" % (
                    (
                        "基础策略" if optimization_retry_number == 0
                        else "同一策略第%s/%s次优化" % (
                            optimization_retry_number, strategy_retry_cap,
                        )
                    ),
                    "通过" if attempt_row["quality_pass"] else "未通过，返回事实供当前创造者继续优化同一策略",
                ),
                min(92, 38 + optimization_retry_number * 18),
                extras={
                    "evaluation_number": evaluation_number,
                    "optimization_retry_number": optimization_retry_number,
                    "quality_pass": attempt_row["quality_pass"],
                    "current_quality_evaluation_limit": current_max_evaluations,
                    "conditional_extension": attempt_row.get("conditional_retry_budget"),
                },
            )
            if attempt_row["quality_pass"]:
                return self._success(
                    instruction_id, brief, candidate, evaluation, evaluation_number,
                    attempts, public_tools, technical_events, creator_diagnostics,
                    max_optimization_retries=current_max_evaluations - 1,
                    dry_run=dry_run_notifications or test_mode,
                )
            evaluation_number += 1

        return self._quality_failure(
            instruction_id, brief, last_candidate, last_evaluation,
            len(attempts), attempts, public_tools, technical_events,
            creator_diagnostics,
            max_optimization_retries=max(0, len(attempts) - 1),
            dry_run=dry_run_notifications or test_mode,
        )

    def _success(self, instruction_id, brief, candidate, evaluation, attempt_number,
                 attempts, tool_bundle, technical_events, diagnostics,
                 max_optimization_retries=3, dry_run=False):
        from .kimi_creation_notify import notify_quality_outcome

        notification = notify_quality_outcome(
            "success", instruction_id, attempt_number, candidate,
            evaluation.get("quality_report"),
            oos_report=evaluation.get("quality_report_oos"),
            optimization_retry_number=max(0, attempt_number - 1),
            maximum_optimization_retries=max_optimization_retries,
            dry_run=dry_run,
        )
        handoff = _make_handoff(
            instruction_id, brief, candidate, evaluation,
            notification=notification, human_submission=None,
        )
        human = _enqueue_human(handoff, dry_run=dry_run)
        handoff["human_confirm_submission"] = human
        out = self._result(
            instruction_id, candidate.get("symbol"), candidate.get("timeframe"),
            candidate.get("direction"), brief, attempts, tool_bundle,
            technical_events, diagnostics, "candidate_ready", True,
            handoff=handoff, notification=notification, human_submission=human,
        )
        self._write_final(instruction_id, out)
        return out

    def _quality_failure(self, instruction_id, brief, candidate, evaluation,
                         attempt_number, attempts, tool_bundle, technical_events,
                         diagnostics, max_optimization_retries=3, dry_run=False):
        from .kimi_creation_notify import notify_quality_outcome

        notification = notify_quality_outcome(
            "failure", instruction_id, attempt_number, candidate,
            evaluation.get("quality_report"),
            oos_report=evaluation.get("quality_report_oos"),
            optimization_retry_number=max(0, attempt_number - 1),
            maximum_optimization_retries=max_optimization_retries,
            dry_run=(dry_run or self.suppress_failure_notification),
        )
        handoff = _make_handoff(
            instruction_id, brief, candidate, evaluation,
            notification=notification, human_submission=None,
        )
        handoff["ok"] = False
        handoff["present_to_human"] = False
        handoff["quality_status"] = "未通过"
        out = self._result(
            instruction_id, candidate.get("symbol"), candidate.get("timeframe"),
            candidate.get("direction"), brief, attempts, tool_bundle,
            technical_events, diagnostics, "candidate_quality_failure", True,
            handoff=handoff, notification=notification,
        )
        self._write_final(instruction_id, out)
        return out

    def _technical_failure(self, instruction_id, symbol, timeframe, direction,
                           brief, reason, detail, attempts=None, tool_bundle=None):
        handoff = {
            "schema": "qiyu_kimi_quality_handoff_v1", "ok": False,
            "present_to_human": False, "quality_status": "技术链路未完成",
            "run_id": instruction_id, "creator": self.creator_provider,
            "creator_label": self.creator_label,
            "error": reason, "detail": detail,
            "automatic_live_deployment": False, "at": _now(),
        }
        out = self._result(
            instruction_id, symbol, timeframe, direction, brief, attempts or [],
            tool_bundle or {}, detail if isinstance(detail, list) else [], [],
            "technical_failed", False, handoff=handoff,
        )
        self._write_final(instruction_id, out)
        return out

    def _result(self, instruction_id, symbol, timeframe, direction, brief,
                attempts, tool_bundle, technical_events, diagnostics, outcome,
                technical_completed, handoff=None, notification=None,
                human_submission=None):
        ready = outcome == "candidate_ready"
        strategy_count = max(
            [int(row.get("mechanism_generation") or 1) for row in attempts] or [1]
        )
        optimization_retry_count = sum(
            1 for row in attempts if int(row.get("optimization_retry_number") or 0) > 0
        )
        last_gate = (
            (((attempts or [])[-1].get("evaluation") or {}).get("quality_gate") or {})
            if attempts else {}
        )
        last_is_passed = bool(
            (last_gate.get("is_gate") or {}).get("all_passed")
        )
        last_oos_reached = bool(last_gate.get("oos_gate"))
        if ready:
            pipeline_note_zh = "统一质检器通过：10项IS、结构校验与一次性盲OOS均通过"
        elif technical_completed and last_is_passed and last_oos_reached:
            pipeline_note_zh = (
                "10项IS与结构校验已通过；一次性盲OOS未通过。"
                "同一身份不得据此改参重试；任务内独立身份盲测额度已用尽"
            )
        elif technical_completed:
            pipeline_note_zh = (
                "同一策略已用尽本次证据允许的优化额度，仍未通过10项IS与结构校验"
            )
        else:
            pipeline_note_zh = "技术链路未完成"
        status = {
            "technical_completed": bool(technical_completed),
            "technical_failed": outcome == "technical_failed",
            "research_rejected": False,
            "candidate_quality_failure": outcome == "candidate_quality_failure",
            "data_blocked": False,
            "candidate_ready": ready,
            "human_confirmation_queued": bool((human_submission or {}).get("ok")),
        }
        return {
            "ok": ready, "technical_completed": bool(technical_completed),
            "outcome": outcome, "status_code": outcome, "outcome_status": status,
            "schema": SCHEMA, "creation_path": self.creator_path,
            "creator": self.creator_provider,
            "creator_label": self.creator_label,
            "local_tools_can_modify_strategy": False,
            "research_collaboration": {
                "enabled": self.collaboration is not None,
                "advisory_only": True,
                "not_a_gate_or_vote": True,
                "named_creator_is_final_author": True,
                "configured_collaborators": list(
                    getattr(self.collaboration, "collaborators", ()) or ()
                ) if self.collaboration is not None else [],
            },
            "creation_quality_telemetry": {
                "schema": "qiyu_creation_quality_trajectory_v1",
                "used_non_gating_base_preflight": bool(
                    attempts and attempts[0].get("non_gating_preflight")
                ),
                "metric_trajectory_unranked": [{
                    "evaluation_number": row.get("evaluation_number"),
                    "quality_pass": bool(row.get("quality_pass")),
                    "metrics": dict(
                        (((row.get("evaluation") or {}).get("quality_gate") or {}).get(
                            "metrics") or {})
                    ),
                    "failed_rules": list(
                        (((row.get("evaluation") or {}).get("quality_gate") or {}).get(
                            "reasons") or ((row.get("evaluation") or {}).get(
                                "quality_gate") or {}).get("failed_rules") or [])
                    ),
                } for row in attempts],
                "automatic_best_attempt_selection": False,
                "purpose": "measure future creation quality and pass-rate changes without influencing a candidate",
            },
            "instruction_id": instruction_id, "mission_id": instruction_id,
            "symbol": symbol, "timeframe": timeframe, "direction": direction,
            "brief": brief,
            "strategy_count": strategy_count,
            "attempt_count": len(attempts),
            "evaluation_count": len(attempts),
            "optimization_retry_count": optimization_retry_count,
            "guaranteed_optimization_retries": GUARANTEED_OPTIMIZATION_RETRIES,
            "maximum_conditional_extra_retries": MAX_CONDITIONAL_EXTRA_RETRIES,
            "maximum_optimization_retries": (
                GUARANTEED_OPTIMIZATION_RETRIES + MAX_CONDITIONAL_EXTRA_RETRIES
            ),
            "maximum_quality_evaluations_per_strategy": MAX_QUALITY_EVALUATIONS_PER_STRATEGY,
            "attempts": attempts, "tool_bundle": tool_bundle,
            "technical_events": technical_events,
            "creator_diagnostics": diagnostics,
            "mechanism_branch_coverage_ledger": (
                _mechanism_branch_coverage_ledger(attempts, diagnostics)
            ),
            "handoff": handoff or {}, "blueprint": handoff or {},
            "cognitive": {
                "creator": self.creator_provider,
                "creator_label": self.creator_label,
                "strategy_count": strategy_count,
                "lifecycle": (
                    "每个正式策略身份均获得三次保证优化；样本内用尽则携带失败证据返回假设沙箱。"
                    "某身份一次性盲OOS未通过时，不泄露样本外数字；若仍有机制额度，换新身份再盲测一次，"
                    "同一持有期计多次假设。禁止对着同一身份用样本外明细改参。"
                ),
                "attempts": attempts,
            },
            "pipeline_gate": {
                "ok": ready, "passed": ready,
                "note_zh": pipeline_note_zh,
                "single_inspector": True,
                "is_numeric_dimensions": 10,
                "blind_oos_once": True,
                "blind_oos_per_frozen_identity": True,
                "oos_identity_tests": sum(
                    1 for row in attempts if (row.get("blind_oos") or {}).get(
                        "frozen_candidate_receipt"
                    )
                ),
                "oos_identity_tests_maximum": MAX_OOS_IDENTITY_TESTS,
            },
            "present_to_human": bool((handoff or {}).get("present_to_human")),
            "quality_status": (handoff or {}).get("quality_status"),
            "quality_gate": (handoff or {}).get("quality_gate"),
            "notification": notification,
            "human_confirm_submission": human_submission,
            "automatic_live_deployment": False, "at": _now(),
        }

    def _save_progress(self, instruction_id, symbol, timeframe, direction, brief,
                       tool_bundle, attempts, technical_events, diagnostics, status):
        strategy_count = max(
            [int(row.get("mechanism_generation") or 1) for row in attempts] or [1]
        )
        optimization_retry_count = sum(
            1 for row in attempts if int(row.get("optimization_retry_number") or 0) > 0
        )
        out = {
            "schema": SCHEMA, "instruction_id": instruction_id,
            "creator": self.creator_provider,
            "creator_label": self.creator_label, "status": status,
            "symbol": symbol, "timeframe": timeframe, "direction": direction,
            "brief": brief,
            "strategy_count": strategy_count,
            "attempt_count": len(attempts),
            "evaluation_count": len(attempts),
            "optimization_retry_count": optimization_retry_count,
            "guaranteed_optimization_retries": GUARANTEED_OPTIMIZATION_RETRIES,
            "maximum_conditional_extra_retries": MAX_CONDITIONAL_EXTRA_RETRIES,
            "maximum_optimization_retries": (
                GUARANTEED_OPTIMIZATION_RETRIES + MAX_CONDITIONAL_EXTRA_RETRIES
            ),
            "attempts": attempts,
            "tool_bundle": tool_bundle, "technical_events": technical_events,
            "creator_diagnostics": diagnostics,
            "mechanism_branch_coverage_ledger": (
                _mechanism_branch_coverage_ledger(attempts, diagnostics)
            ),
            "updated_at": _now(),
        }
        path = _root() / "auto_trade" / "kimi_creation_runs" / ("%s.json" % instruction_id)
        atomic_write_json(path, out)

    def _write_final(self, instruction_id, result):
        path = _root() / "auto_trade" / "kimi_creation_runs" / ("%s.json" % instruction_id)
        atomic_write_json(path, result)
