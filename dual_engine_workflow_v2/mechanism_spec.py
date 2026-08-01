# -*- coding: utf-8 -*-
"""Immutable mechanism_spec.json — GLM proposer output, never overwritten."""
from __future__ import print_function

import copy
import hashlib
import json
import os
from pathlib import Path

from .config import _now, _atomic
from .step_a_config import (
    MECHANISM_SPEC_FIELDS,
    MECHANISM_SPEC_DIR,
    FIELD_ALIASES,
    SPEC_TO_STATEMENT,
    STEP_A_SCHEMA,
    STEP_A_CODE_VERSION,
    ensure_step_a_dirs,
)


class MechanismSpecError(ValueError):
    pass


def _pick_alias(obj, field):
    if not isinstance(obj, dict):
        return None
    for k in FIELD_ALIASES.get(field, [field]):
        if k in obj and obj[k] is not None and str(obj[k]).strip() not in ("", "none", "n/a", "null", "-", "todo"):
            return obj[k]
    return None


def normalize_mechanism_spec(raw, focus=None):
    """Extract/normalize mechanism_spec from GLM JSON with alias recovery."""
    focus = focus or {}
    src = raw if isinstance(raw, dict) else {}
    # nested forms
    for nest in ("mechanism_spec", "mechanism_statement", "hypothesis", "spec"):
        if isinstance(src.get(nest), dict):
            # prefer nested if it has more filled fields
            candidate = src[nest]
            if nest == "mechanism_spec" or sum(1 for f in MECHANISM_SPEC_FIELDS if _pick_alias(candidate, f) is not None) >= 5:
                src = {**src, **candidate}
                break

    cleaned = {}
    errors = []
    for field in MECHANISM_SPEC_FIELDS:
        val = _pick_alias(src, field)
        if field in ("non_negotiable_rules", "tunable_parameters", "forbidden_transformations",
                     "suitable_symbols", "suitable_timeframes"):
            if val is None:
                cleaned[field] = []
                errors.append("missing_or_empty:%s" % field)
            elif isinstance(val, list):
                cleaned[field] = [str(x).strip() for x in val if str(x).strip()]
                if not cleaned[field]:
                    errors.append("missing_or_empty:%s" % field)
            else:
                # allow comma/newline separated string
                parts = [p.strip() for p in str(val).replace("\n", ",").split(",") if p.strip()]
                cleaned[field] = parts
                if not parts:
                    errors.append("missing_or_empty:%s" % field)
        else:
            text = str(val).strip() if val is not None else ""
            if not text or text.lower() in ("none", "n/a", "null", "-", "todo"):
                errors.append("missing_or_empty:%s" % field)
                cleaned[field] = text
            else:
                cleaned[field] = text

    # Fill defaults for symbol/tf lists from focus when empty (still errors if truly empty after)
    if not cleaned.get("suitable_symbols") and focus.get("symbol"):
        cleaned["suitable_symbols"] = [focus["symbol"]]
        if "missing_or_empty:suitable_symbols" in errors:
            errors.remove("missing_or_empty:suitable_symbols")
    if not cleaned.get("suitable_timeframes") and focus.get("timeframe"):
        cleaned["suitable_timeframes"] = [focus["timeframe"]]
        if "missing_or_empty:suitable_timeframes" in errors:
            errors.remove("missing_or_empty:suitable_timeframes")

    # Auto mechanism_id if name present but id missing
    if (not cleaned.get("mechanism_id") or "missing_or_empty:mechanism_id" in errors) and cleaned.get("mechanism_name"):
        base = str(cleaned["mechanism_name"]).lower().replace(" ", "_")[:40]
        cleaned["mechanism_id"] = "mech_%s_%s" % (base, hashlib.sha1(base.encode()).hexdigest()[:6])
        if "missing_or_empty:mechanism_id" in errors:
            errors.remove("missing_or_empty:mechanism_id")

    ok = len(errors) == 0
    return ok, errors, cleaned


def spec_to_mechanism_statement(spec):
    """Bridge STEP A spec → v2 mechanism_statement for fingerprint/fidelity reuse."""
    spec = spec or {}
    stmt = {}
    for stmt_k, spec_k in SPEC_TO_STATEMENT.items():
        val = spec.get(spec_k)
        if stmt_k == "forbidden_substitutions":
            if isinstance(val, list):
                stmt[stmt_k] = list(val)
            else:
                stmt[stmt_k] = [str(val)] if val else []
        else:
            stmt[stmt_k] = str(val or "")
    # persistence_reason distinct if possible
    if spec.get("why_edge_exists"):
        stmt["persistence_reason"] = str(spec.get("why_edge_exists"))
    return stmt


def content_hash(obj):
    blob = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def permanent_spec_path(mechanism_id):
    ensure_step_a_dirs()
    from . import step_a_config as sc
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(mechanism_id or "unknown"))[:80]
    return Path(sc.MECHANISM_SPEC_DIR) / ("%s_mechanism_spec.json" % safe)


def save_immutable_mechanism_spec(spec, task_id=None, meta=None):
    """Write mechanism_spec.json once. Refuse overwrite of existing content_hash."""
    ensure_step_a_dirs()
    if not isinstance(spec, dict) or not spec.get("mechanism_id"):
        raise MechanismSpecError("mechanism_id required")
    path = permanent_spec_path(spec["mechanism_id"])
    payload = {
        "schema": STEP_A_SCHEMA,
        "artifact": "mechanism_spec",
        "immutable": True,
        "code_version": STEP_A_CODE_VERSION,
        "task_id": task_id,
        "created_at": _now(),
        "content_hash": content_hash(spec),
        "mechanism_spec": copy.deepcopy(spec),
        "meta": meta or {},
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        if existing.get("content_hash") and existing.get("content_hash") != payload["content_hash"]:
            raise MechanismSpecError(
                "refuse_overwrite_immutable_spec:%s existing_hash=%s new_hash=%s"
                % (path.name, existing.get("content_hash"), payload["content_hash"])
            )
        # same content — keep original created_at
        payload["created_at"] = existing.get("created_at") or payload["created_at"]
        payload["immutable_note"] = "idempotent_rewrite_same_hash"
    _atomic(path, payload)
    # also write sidecar pointer for task
    if task_id:
        from . import step_a_config as sc
        ptr = Path(sc.MECHANISM_SPEC_DIR) / ("%s_pointer.json" % task_id)
        _atomic(ptr, {"task_id": task_id, "path": str(path), "mechanism_id": spec["mechanism_id"],
                      "content_hash": payload["content_hash"], "at": _now()})
    return str(path), payload


def load_mechanism_spec(mechanism_id):
    path = permanent_spec_path(mechanism_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def glm_spec_prompt_template():
    """Few-shot schema to reduce representation_failure."""
    example = {
        "mechanism_spec": {
            "mechanism_id": "mech_liquidity_vacuum_reclaim_sol5m",
            "mechanism_name": "liquidity_vacuum_reclaim",
            "mechanism_family": "liquidity_vacuum_reclaim",
            "market_inefficiency": "forced liquidations create temporary price vacuum then mean reclaim",
            "counterparty_source": "overleveraged late longs being stopped out into thin book",
            "why_edge_exists": "stop cascades are mechanical and not instantly inventory-rebalanced",
            "edge_decay_conditions": "when depth recovers and cascade intensity drops below threshold",
            "required_market_regime": "elevated realized vol with declining book depth",
            "entry_logic": "enter short after cascade impulse + reclaim stall below cascade mid",
            "exit_logic": "3.0x ATR trailing OR swing extreme lookback 20",
            "stop_logic": "fixed 0.9% protective SL plus swing extreme lookback 20 invalidation",
            "take_profit_logic": "3.0x ATR trailing",
            "invalidation_logic": "swing extreme lookback 20; no cascade signature before entry",
            "non_negotiable_rules": [
                "must observe forced-flow signature",
                "no RSI/MACD substitution of cascade detector",
                "fixed SL 0.9%",
            ],
            "tunable_parameters": ["cascade_lookback_bars", "depth_z_threshold", "max_hold_bars"],
            "forbidden_transformations": [
                "replace cascade detector with RSI",
                "convert to trend-follow breakout",
                "remove forced-flow condition",
            ],
            "expected_trade_frequency_class": "0.5_to_1_per_day",
            "expected_holding_period": "3_to_18_bars_5m",
            "suitable_symbols": ["SOL-USDT-SWAP"],
            "suitable_timeframes": ["5m"],
        },
        "title": "liquidity_vacuum_reclaim_sol5m",
        "thesis": "capture post-cascade reclaim asymmetry",
        "direction": "short",
        "suggested_core_features": ["volume_z", "atr_pct", "range_compression"],
        "holding_horizon": "3_to_18_bars",
    }
    return (
        "你是策略机制提出者 GLM。只提出盈利机制，不写可运行交易代码。\n"
        "必须输出 JSON，顶层含 mechanism_spec 对象，字段一字不差全部填写（禁止空字符串/none/todo）：\n"
        + ", ".join(MECHANISM_SPEC_FIELDS)
        + "\n\n字段语义：\n"
        "- market_inefficiency: 可观测市场偏差\n"
        "- counterparty_source: 谁在亏/被迫行为者\n"
        "- why_edge_exists: 为何不会立刻被套利抹平\n"
        "- edge_decay_conditions / invalidation_logic: 偏差消失条件\n"
        "- entry/exit/stop/take_profit_logic: 与机制因果相连\n"
        "- 可执行退出只允许明确枚举：N.x ATR trailing（N=2.5..5.0）、"
        "swing extreme lookback N（N=5..60）、fixed_pct_tp P%（P>=2.0）、"
        "partial N.x ATR ratio R%（N=1.5..4.0，R=10..90）。"
        "所有参数必须显式填写；至少填写一种；禁止只写‘恢复正常/回到公允价’等不可编译叙事。\n"
        "- non_negotiable_rules: 不可修改项\n"
        "- tunable_parameters: 允许调节参数名列表\n"
        "- forbidden_transformations: 禁止Codex改写的变换\n"
        "- mechanism_family 不得使用 exhaustion_fade_short 克隆名，除非 Mode=known_mechanism_deep_dig\n"
        "\n示例结构（内容请按当前 focus 重写，勿原样照抄）：\n"
        + json.dumps(example, ensure_ascii=False, indent=2)
        + "\n仅输出JSON。"
    )
