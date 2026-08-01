# -*- coding: utf-8 -*-
"""Immutable, machine-readable contract for pre-review strategy research.

The contract is deliberately conservative: the original instruction is always
kept, explicit structured fields win over text heuristics, and an unsupported
required data source blocks research instead of being silently replaced.
"""
from __future__ import print_function

import hashlib
import json
import math
import re
from datetime import datetime


SCHEMA = "qiyu_research_contract_v2"
VALID_DIRECTIONS = ("long", "short")
DEFAULT_HORIZONS_BARS = (1, 3, 6, 12)
PRODUCTION_PROTECTIVE_STOP_PCT = 0.009
PRODUCTION_EXECUTION_LEVERAGE = 20


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [x for x in value if x not in (None, "")]
    return [value]


def _uniq(values):
    out, seen = [], set()
    for value in values or []:
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(value)
    return out


def normalize_timeframe(value):
    text = str(value or "").strip().lower().replace("分钟", "m").replace("小时", "h")
    text = text.replace("mins", "m").replace("min", "m").replace("hours", "h").replace("hour", "h")
    text = re.sub(r"\s+", "", text)
    return text


def normalize_direction(value):
    text = str(value or "").strip().lower()
    aliases = {
        "多": "long", "做多": "long", "多头": "long", "buy": "long",
        "空": "short", "做空": "short", "空头": "short", "sell": "short",
        "双向": "both", "多空": "both", "long_short": "both",
    }
    return aliases.get(text, text)


def _operator(raw):
    text = str(raw or "").strip().lower()
    mapping = {
        "大于等于": ">=", "不小于": ">=", "至少": ">=", "gte": ">=", "=>": ">=",
        "小于等于": "<=", "不大于": "<=", "至多": "<=", "lte": "<=", "=<": "<=",
        "大于": ">", "超过": ">", "gt": ">",
        "小于": "<", "低于": "<", "lt": "<",
        "等于": "==", "为": "==", "=": "==",
    }
    return mapping.get(text, text)


def _indicator_feature(name):
    raw = str(name or "").strip()
    lower = raw.lower()
    aliases = {
        "j": "kdj_j", "j值": "kdj_j", "kdj_j": "kdj_j",
        "k": "kdj_k", "k值": "kdj_k", "kdj_k": "kdj_k",
        "d": "kdj_d", "d值": "kdj_d", "kdj_d": "kdj_d",
        "rsi": "rsi_14", "rsi14": "rsi_14", "rsi_14": "rsi_14",
        "atr": "atr_pct_14", "atr14": "atr_pct_14", "atr_14": "atr_pct_14",
    }
    return aliases.get(lower, lower)


_COND_RE = re.compile(
    r"(?:[（(]?\s*(?P<tf>\d+\s*(?:m|min|分钟|h|hour|小时))\s*[）)]?\s*(?:的|上|周期)?\s*)?"
    r"(?P<indicator>kdj[_\- ]?[jkd]|rsi\s*\d*|atr\s*\d*|[jkdJDK](?:值)?)\s*"
    r"(?P<op>>=|<=|=>|=<|>|<|==|=|大于等于|小于等于|不小于|不大于|至少|至多|大于|小于|超过|低于|等于|为)\s*"
    r"(?P<value>-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def _parse_conditions(text, default_timeframe):
    out = []
    raw_text = str(text or "")
    lower = raw_text.lower()
    # One sentence can contain both entry and exit rules, for example
    # ``开仓K<=20，止盈J>=89``.  Attribute each condition to its
    # nearest role label instead of classifying the whole sentence as an exit.
    separators = "\n。；;!?！？"
    exit_words = ("止盈", "止损", "平仓", "退出", "离场", "take profit", "exit")
    entry_words = ("开仓", "入场", "进场", "建仓", "entry", "open position")

    def nearest_role(sentence_start, sentence_end, match_start, match_end):
        candidates = []
        sentence = lower[sentence_start:sentence_end]
        for role, words in (("entry", entry_words), ("exit", exit_words)):
            for word in words:
                begin = 0
                while True:
                    pos = sentence.find(word, begin)
                    if pos < 0:
                        break
                    absolute_start = sentence_start + pos
                    absolute_end = absolute_start + len(word)
                    if absolute_end <= match_start:
                        distance = match_start - absolute_end
                        side_rank = 0
                    elif absolute_start >= match_end:
                        distance = absolute_start - match_end
                        side_rank = 1
                    else:
                        distance = 0
                        side_rank = 0
                    candidates.append((distance, side_rank, absolute_start, role))
                    begin = pos + max(1, len(word))
        if not candidates:
            return "entry"
        candidates.sort(key=lambda row: (row[0], row[1], -row[2]))
        return candidates[0][3]

    for match in _COND_RE.finditer(raw_text):
        clause_start = 0
        for token in separators:
            clause_start = max(clause_start, lower.rfind(token, 0, match.start()) + 1)
        clause_end = len(lower)
        for token in separators:
            found = lower.find(token, match.end())
            if found >= 0:
                clause_end = min(clause_end, found)
        local_start = clause_start
        for token in ("，", ","):
            local_start = max(local_start, lower.rfind(token, clause_start, match.start()) + 1)
        prefix = lower[max(local_start, match.start() - 12):match.start()]
        join = "or" if any(x in prefix for x in ("或", "或者", " or ")) else "and"
        role = nearest_role(clause_start, clause_end, match.start(), match.end())
        condition_tf = normalize_timeframe(match.group("tf") or default_timeframe)
        indicator = _indicator_feature(match.group("indicator"))
        out.append({
            "indicator": indicator,
            "feature": indicator if condition_tf == normalize_timeframe(default_timeframe) else "%s__%s" % (condition_tf, indicator),
            "operator": _operator(match.group("op")),
            "value": float(match.group("value")),
            "timeframe": condition_tf,
            "join": join,
            "role": role,
            "source_text": match.group(0).strip(),
        })
    return _uniq(out)


def _strong_clauses(text):
    parts = re.split(r"[\n。；;]+", str(text or ""))
    strong = (
        "必须", "要求", "仅当", "不得", "禁止", "不能替", "不可替", "保持", "规则",
        "条件", "若", "即", "等到", "收盘", "走完", "触发", "开仓", "平仓",
        "持有", "固定",
    )
    rows = [p.strip() for p in parts if p.strip() and any(k in p for k in strong)]
    return _uniq(rows)[:40]


def _is_performance_target_clause(text):
    """Separate research targets from executable event requirements."""
    blob = str(text or "").lower()
    return any(token in blob for token in (
        "周收益", "月收益", "年化", "夏普", "sharpe", "dsr", "pbo",
        "最大回撤", "胜率", "收益率目标", "收益目标", "交易频率",
    )) and not any(token in blob for token in (
        "开仓", "入场", "平仓", "止盈", "止损", "k线", "指标",
    ))


def _additional_timeframes(text, primary):
    rows = re.findall(r"(?<!\d)(\d+\s*(?:m|min|分钟|h|hour|小时))(?!\w)", str(text or ""), re.I)
    norm = [normalize_timeframe(x) for x in rows]
    return [x for x in _uniq(norm) if x and x != normalize_timeframe(primary)]


def _family_hints(text):
    blob = str(text or "").lower()
    mapping = (
        (("唐奇安", "donchian", "通道突破"), "donchian_trend_break"),
        (("趋势", "顺势", "momentum", "pullback", "回撤"), "trend_pullback"),
        (("压缩", "squeeze", "波动扩张"), "vol_squeeze_break"),
        (("均值回归", "mean reversion", "反转"), "mean_reversion"),
        (("流动性", "sweep", "假突破", "止损猎杀"), "liquidity_sweep"),
        (("衰竭", "exhaust", "恐慌", "超卖"), "exhaustion"),
        (("配对", "协整", "cross-asset", "跨品种", "lead-lag"), "pairs_cointegration"),
    )
    return _uniq([family for keys, family in mapping if any(k in blob for k in keys)])


def _canonical_hash(payload):
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _parse_holding_contract(text, supplied, constraints):
    raw = supplied.get("holding_contract") or constraints.get("holding_contract") or {}
    raw = dict(raw) if isinstance(raw, dict) else {}
    exact = raw.get("exact_horizon_bars")
    allowed = _list(raw.get("allowed_horizons_bars"))
    text_match = re.search(
        r"(?:固定持有|持有|第)\s*(\d+)\s*(?:根(?:k线|线)?|bars?|根后)",
        str(text or ""), re.IGNORECASE,
    )
    if exact is None and text_match:
        exact = int(text_match.group(1))
    if exact is not None:
        try:
            exact = int(exact)
        except Exception:
            exact = None
        allowed = [exact] if exact is not None else allowed
    if not allowed:
        allowed = list(DEFAULT_HORIZONS_BARS)
    normalized = []
    for value in allowed:
        try:
            value = int(value)
        except Exception:
            continue
        if value not in normalized:
            normalized.append(value)
    allow_early = bool(raw.get("allow_early_take_profit", False))
    if any(token in str(text or "") for token in (
        "不得提前止盈", "禁止提前止盈", "仅到期平仓", "固定持有到期",
    )):
        allow_early = False
    stop_in = raw.get("protective_stop_policy") or {}
    stop_in = dict(stop_in) if isinstance(stop_in, dict) else {}
    try:
        stop_pct = float(
            stop_in.get("price_pct", raw.get(
                "protective_stop_pct", PRODUCTION_PROTECTIVE_STOP_PCT,
            ))
        )
    except Exception:
        stop_pct = None
    return {
        "mode": "exact_horizon" if exact is not None else "allowed_horizons",
        "allowed_horizons_bars": sorted(normalized),
        "exact_horizon_bars": exact,
        "exit_policy": {
            "mode": "fixed_horizon_close_v1",
            "exit_bar": "entry_plus_horizon_minus_1",
            "price": "bar_close",
            "allow_early_take_profit": allow_early,
        },
        "protective_stop_policy": {
            "mode": "intrabar_fixed_pct_v1",
            "price_pct": stop_pct,
            "applies_from": "entry_bar",
            "precedence": "protective_stop_before_time_exit",
        },
        "execution_leverage": PRODUCTION_EXECUTION_LEVERAGE,
        "statistical_return_basis": "full_size_leveraged_after_cost_v1",
        "source": "structured_or_human_exact" if (raw or text_match) else "platform_default",
    }


def stable_contract_payload(contract):
    """Return exactly the immutable body used to derive ``contract_id``."""
    row = contract if isinstance(contract, dict) else {}
    return {
        "schema": row.get("schema"),
        "target": row.get("target") or {},
        "event_contract": row.get("event_contract") or {},
        "holding_contract": row.get("holding_contract") or {},
        "feature_contract": row.get("feature_contract") or {},
        "data_contract": row.get("data_contract") or {},
        "mutation_contract": row.get("mutation_contract") or {},
        "family_hints": row.get("family_hints") or [],
        "brief": row.get("brief") or "",
    }


def verify_contract_integrity(contract):
    row = contract if isinstance(contract, dict) else {}
    expected = "rc_%s" % _canonical_hash(stable_contract_payload(row))
    reasons = []
    if row.get("schema") != SCHEMA:
        reasons.append("research_contract_schema_invalid")
    if row.get("contract_id") != expected:
        reasons.append("research_contract_body_hash_mismatch")
    if row.get("immutable") is not True:
        reasons.append("research_contract_not_immutable")
    if row.get("valid") is not True or row.get("validation_errors"):
        reasons.append("research_contract_declared_invalid")
    return {
        "ok": not reasons,
        "contract_id": row.get("contract_id"),
        "expected_contract_id": expected,
        "reasons": reasons,
    }


def compile_contract(brief, symbol, timeframe, direction="long", constraints=None,
                     design_seed=None, available_data=None, data_version=None,
                     code_version=None):
    constraints = dict(constraints or {})
    supplied = constraints.get("research_contract") or constraints.get("creation_contract") or {}
    supplied = dict(supplied) if isinstance(supplied, dict) else {}
    text = str(brief or "").strip()
    symbol = str(symbol or "").strip().upper()
    timeframe = normalize_timeframe(timeframe)
    direction = normalize_direction(direction)

    target_in = supplied.get("target") or {}
    event_in = supplied.get("event_contract") or {}
    feature_in = supplied.get("feature_contract") or {}
    data_in = supplied.get("data_contract") or {}
    mutation_in = supplied.get("mutation_contract") or constraints.get("mutation_contract") or {}

    target = {
        "symbol": str(target_in.get("symbol") or symbol).upper(),
        "timeframe": normalize_timeframe(target_in.get("timeframe") or timeframe),
        "direction": normalize_direction(target_in.get("direction") or direction),
        "additional_timeframes": _uniq(
            _list(target_in.get("additional_timeframes")) + _additional_timeframes(text, timeframe)
        ),
    }

    parsed_conditions = _parse_conditions(text, timeframe)
    brief_strong_clauses = _strong_clauses(text)
    strong_clauses = _uniq(
        _list(event_in.get("required_clauses"))
        + _list(constraints.get("required_clauses"))
        + [row for row in brief_strong_clauses
           if not _is_performance_target_clause(row)]
    )
    diagnostic_clauses = _uniq(
        _list(event_in.get("diagnostic_clauses"))
        + _list(constraints.get("diagnostic_clauses"))
        + [row for row in brief_strong_clauses
           if _is_performance_target_clause(row)]
    )
    required_conditions = _uniq(
        _list(event_in.get("required_conditions"))
        + _list(constraints.get("required_conditions"))
        + parsed_conditions
    )
    timing_mode = event_in.get("entry_timing") or constraints.get("entry_timing") or {}
    if not isinstance(timing_mode, dict):
        timing_mode = {"mode": str(timing_mode)}
    if any(k in text for k in ("K线走完", "k线走完", "K线收盘", "k线收盘", "收盘再开仓", "收盘在开仓")):
        # A close-confirmed signal is only causally fillable at the following
        # bar open: a 22:50 candle therefore enters at the 22:55 boundary.
        timing_mode = {
            "mode": "next_bar_open",
            "signal_evaluation": "closed_bar",
            "timeframe": timeframe,
        }
    # Do not invent execution timing when the human did not specify it.  An
    # explicit bar-close/next-open requirement is immutable and must be checked
    # by the compiler/backtester; unspecified timing stays a research choice.
    timing_mode.setdefault("mode", "unspecified")
    timing_mode.setdefault("timeframe", timeframe)
    holding_contract = _parse_holding_contract(text, supplied, constraints)

    required_features = _uniq(
        _list(feature_in.get("required_features"))
        + _list(constraints.get("required_features"))
        + [row.get("feature") or row.get("indicator") for row in required_conditions if isinstance(row, dict)]
    )
    explicit_allowed = (
        bool(feature_in.get("allowed_features_enforced"))
        if "allowed_features_enforced" in feature_in else
        ("allowed_features" in feature_in or "allowed_features" in constraints)
    )
    allowed_features = _uniq(
        _list(feature_in.get("allowed_features"))
        + _list(constraints.get("allowed_features"))
    ) if explicit_allowed else []
    cross_assets = _uniq(
        _list(feature_in.get("cross_asset_symbols"))
        + _list(constraints.get("cross_asset_symbols"))
    )
    cross_terms_present = any(
        k in text.lower() for k in ("跨品种", "cross-asset", "lead-lag", "配对", "协整")
    )
    optional_menu = cross_terms_present and any(
        k in text for k in ("可选", "备选", "之一", "自主选择", "A/B/C", "a/b/c")
    )
    mandatory_cross = cross_terms_present and any(
        k in text for k in ("必须配对", "必须跨品种", "仅做配对", "仅限跨品种", "要求配对")
    )
    cross_asset_required = bool(cross_assets or mandatory_cross or (
        cross_terms_present and not optional_menu
    ))
    forbidden_substitutions = _uniq(
        _list(feature_in.get("forbidden_substitutions"))
        + _list(constraints.get("forbidden_substitutions"))
        + (["cross_asset_to_same_symbol_proxy"] if cross_asset_required else [])
        + (["exact_event_to_generic_quantile"] if required_conditions else [])
    )

    available = _uniq(
        _list(available_data)
        + _list(data_in.get("available"))
        + _list(constraints.get("available_data"))
    ) or ["ohlcv_swap_candles", "derived_factors"]
    required_data = _uniq(
        _list(data_in.get("required"))
        + _list(constraints.get("required_data"))
        + (["cross_asset_candles"] if cross_asset_required else [])
    )
    missing_data = [name for name in required_data if name not in set(available)]

    errors = []
    if not target["symbol"]:
        errors.append("target_symbol_missing")
    if not target["timeframe"]:
        errors.append("target_timeframe_missing")
    if target["direction"] == "both":
        errors.append("bidirectional_contract_must_be_split_into_long_and_short_jobs")
    elif target["direction"] not in VALID_DIRECTIONS:
        errors.append("invalid_trade_direction:%s" % target["direction"])
    if target["symbol"] != symbol:
        errors.append("target_symbol_conflict:%s!=%s" % (target["symbol"], symbol))
    if target["timeframe"] != timeframe:
        errors.append("target_timeframe_conflict:%s!=%s" % (target["timeframe"], timeframe))
    if target["direction"] != direction:
        errors.append("target_direction_conflict:%s!=%s" % (target["direction"], direction))
    if missing_data and not bool(data_in.get("allow_missing") or constraints.get("allow_missing_required_data")):
        errors.extend("required_data_missing:%s" % name for name in missing_data)
    if explicit_allowed:
        errors.extend(
            "required_feature_outside_allowlist:%s" % name
            for name in required_features if name not in set(allowed_features)
        )
    allowed_horizons = holding_contract.get("allowed_horizons_bars") or []
    if not allowed_horizons or any(
        int(value) < 1 or int(value) > 240 for value in allowed_horizons
    ):
        errors.append("holding_horizon_outside_1_240")
    if (
        (holding_contract.get("exit_policy") or {}).get("allow_early_take_profit")
        is True
    ):
        errors.append("early_take_profit_research_policy_unsupported")
    stop_pct = ((holding_contract.get("protective_stop_policy") or {}).get("price_pct"))
    if stop_pct is None or abs(float(stop_pct) - PRODUCTION_PROTECTIVE_STOP_PCT) > 1e-12:
        errors.append("protective_stop_policy_must_equal_0p9pct")

    family_hints = _uniq(_list(supplied.get("family_hints")) + _family_hints(text))
    session_window = (
        event_in.get("session_window") or event_in.get("time_window")
        or constraints.get("session_window") or constraints.get("time_window")
    )
    clause_representations = []
    for index, clause in enumerate(strong_clauses, 1):
        text_clause = str(clause or "").strip()
        represented_by = []
        for condition in parsed_conditions:
            source = str((condition or {}).get("source_text") or "").strip()
            if source and source.lower() in text_clause.lower():
                represented_by.append("condition:%s" % (
                    (condition or {}).get("feature") or (condition or {}).get("indicator")
                ))
        if any(token in text_clause for token in (
            "K线走完", "k线走完", "K线收盘", "k线收盘", "收盘再开仓", "收盘在开仓",
        )) and timing_mode.get("mode") == "next_bar_open":
            represented_by.append("entry_timing:next_bar_open")
        if any(token in text_clause for token in ("持有", "根后", "固定")) and (
            holding_contract.get("exact_horizon_bars") is not None
        ):
            represented_by.append("holding_contract:exact_horizon")
        if any(token in text_clause.lower() for token in (
            "session", "utc", "时段", "交易时段",
        )) and session_window:
            represented_by.append("session_window:structured")
        if any(token in text_clause for token in ("不得", "禁止", "不可替", "不能替")) and (
            forbidden_substitutions
        ):
            represented_by.append("feature_contract:forbidden_substitutions")
        status = "machine_exact" if represented_by else "unsupported"
        clause_id = "clause_%s" % _canonical_hash({
            "index": index, "text": text_clause,
        })[:16]
        clause_representations.append({
            "clause_id": clause_id,
            "text": text_clause,
            "representation_status": status,
            "represented_by": represented_by,
        })
        if status == "unsupported":
            errors.append("required_clause_unrepresentable:%s" % clause_id)

    event_contract = {
        "exact_event_text": event_in.get("exact_event_text") or text,
        "required_clauses": strong_clauses,
        "diagnostic_clauses": diagnostic_clauses,
        "clause_representations": clause_representations,
        "required_conditions": required_conditions,
        "entry_conditions": [
            row for row in required_conditions
            if not isinstance(row, dict) or row.get("role", "entry") == "entry"
        ],
        "exit_conditions": [
            row for row in required_conditions
            if isinstance(row, dict) and row.get("role") == "exit"
        ],
        # Keep a structured session window in the immutable identity.  The
        # current probe evaluator does not yet apply it, so the formal recipe
        # compiler can now see it and fail closed instead of silently adding a
        # filter that was never part of the admitted event sample.
        "session_window": session_window,
        "entry_timing": timing_mode,
        "require_exact_event_fidelity": bool(
            event_in.get("require_exact_event_fidelity", bool(required_conditions))
        ),
    }
    feature_contract = {
        "required_features": required_features,
        "allowed_features": allowed_features,
        "allowed_features_enforced": bool(explicit_allowed),
        "forbidden_substitutions": forbidden_substitutions,
        "cross_asset_symbols": cross_assets,
    }
    mutation_contract = {
        "parent_id": mutation_in.get("parent_id"),
        "failed_gate": mutation_in.get("failed_gate"),
        "allowed_mutations": _list(mutation_in.get("allowed_mutations")),
        "structural_delta": mutation_in.get("structural_delta"),
        "data_version": mutation_in.get("data_version") or data_version,
        "code_version": mutation_in.get("code_version") or code_version,
    }
    if mutation_contract.get("parent_id"):
        if not mutation_contract.get("failed_gate"):
            errors.append("mutation_failed_gate_missing")
        if not mutation_contract.get("allowed_mutations"):
            errors.append("mutation_allowlist_missing")
        if not mutation_contract.get("structural_delta"):
            errors.append("mutation_structural_delta_missing")

    stable = {
        "schema": SCHEMA,
        "target": target,
        "event_contract": event_contract,
        "holding_contract": holding_contract,
        "feature_contract": feature_contract,
        "data_contract": {"required": required_data, "available": available, "missing": missing_data},
        "mutation_contract": mutation_contract,
        "family_hints": family_hints,
        "brief": text,
    }
    contract_id = "rc_%s" % _canonical_hash(stable_contract_payload(stable))
    supplied_contract_id = str(supplied.get("contract_id") or "").strip()
    if supplied_contract_id and supplied_contract_id != contract_id:
        errors.append("research_contract_integrity_mismatch:%s!=%s" % (
            supplied_contract_id, contract_id,
        ))
    stable.update({
        "contract_id": contract_id,
        "immutable": True,
        "valid": not errors,
        "validation_errors": errors,
        "design_seed_present": bool(design_seed),
        "compiled_at": _now(),
    })
    return stable


def apply_to_hypothesis(hypothesis, contract):
    """Attach immutable intent and enforce the explicitly requested trade side."""
    row = dict(hypothesis or {})
    contract = contract or {}
    target = contract.get("target") or {}
    direction = target.get("direction")
    if direction in ("long", "short"):
        row["predicted_direction"] = direction
        row["trade_direction_locked"] = True
    row["research_contract_id"] = contract.get("contract_id")
    # The probe capability boundary re-verifies the immutable body, not only
    # its identifier.  Keeping the contract beside the hypothesis prevents a
    # stale ID from blessing changed clauses downstream.
    row["research_contract"] = contract
    row["required_event_conditions"] = list(
        ((contract.get("event_contract") or {}).get("entry_conditions") or [])
    )
    row["required_exit_conditions"] = list(
        ((contract.get("event_contract") or {}).get("exit_conditions") or [])
    )
    row["required_entry_timing"] = (
        (contract.get("event_contract") or {}).get("entry_timing") or {}
    )
    row["required_session_window"] = (
        (contract.get("event_contract") or {}).get("session_window")
    )
    row["holding_contract"] = dict(contract.get("holding_contract") or {})
    row["required_horizons_bars"] = list(
        ((contract.get("holding_contract") or {}).get("allowed_horizons_bars") or [])
    )
    row["research_contract_integrity"] = verify_contract_integrity(contract)
    row["forbidden_substitutions"] = list(
        ((contract.get("feature_contract") or {}).get("forbidden_substitutions") or [])
    )
    hints = list(row.get("factor_hints") or row.get("observable_proxy") or [])
    for feature in ((contract.get("feature_contract") or {}).get("required_features") or []):
        if feature not in hints:
            hints.insert(0, feature)
    row["factor_hints"] = _uniq(hints)
    families = set(contract.get("family_hints") or [])
    row["contract_family_match"] = not families or str(row.get("family") or "") in families
    row["contract_priority"] = 5.0 if row["contract_family_match"] else 0.0
    return row


def derive_contract_features(factor_matrix, candles, primary_timeframe, required_features):
    """Derive exact OHLCV indicators that the contract explicitly requests.

    This is a real indicator implementation, not a proxy substitution.  Only
    primary-timeframe KDJ is currently supported.  Higher-timeframe requests
    remain missing and therefore fail closed until a properly closed/resampled
    source is available.
    """
    matrix = dict(factor_matrix or {})
    required = set(str(x) for x in (required_features or []) if x)
    missing_before = sorted(name for name in required if name not in matrix)
    wanted_kdj = bool(required.intersection(("kdj_k", "kdj_d", "kdj_j")))
    derived = []
    errors = []
    rows = list(candles or [])
    if wanted_kdj and rows:
        # The formal backtest/live frame uses a 24-bar RSV followed by
        # alpha=1/3 K and D smoothing, with 50 during warm-up.  Research must
        # use that exact implementation; the former textbook 9-bar KDJ made a
        # human K/J contract describe one event in discovery and another in
        # formal review.
        k_values = [50.0] * len(rows)
        d_values = [50.0] * len(rows)
        j_values = [50.0] * len(rows)
        k_prev, d_prev = 50.0, 50.0
        for index in range(len(rows)):
            if index < 23:
                continue
            window = rows[index - 23:index + 1]
            try:
                highs = [float(row.get("high")) for row in window]
                lows = [float(row.get("low")) for row in window]
                close = float(rows[index].get("close"))
            except (TypeError, ValueError):
                continue
            if not all(math.isfinite(x) for x in highs + lows + [close]):
                continue
            highest, lowest = max(highs), min(lows)
            rsv = 50.0 if highest <= lowest else 100.0 * (close - lowest) / (highest - lowest)
            k_prev = (2.0 / 3.0) * k_prev + (1.0 / 3.0) * rsv
            d_prev = (2.0 / 3.0) * d_prev + (1.0 / 3.0) * k_prev
            k_values[index] = k_prev
            d_values[index] = d_prev
            j_values[index] = 3.0 * k_prev - 2.0 * d_prev
        for name, values in (
            ("kdj_k", k_values), ("kdj_d", d_values), ("kdj_j", j_values),
        ):
            if name in required and name not in matrix:
                matrix[name] = values
                derived.append(name)
    elif wanted_kdj:
        errors.append("candles_missing_for_primary_kdj")

    unsupported_higher_tf = sorted(
        name for name in required
        if "__" in name and name not in matrix
    )
    if unsupported_higher_tf:
        errors.extend("higher_timeframe_feature_not_derived:%s" % name for name in unsupported_higher_tf)
    missing_after = sorted(name for name in required if name not in matrix)
    return matrix, {
        "ok": not missing_after,
        "primary_timeframe": normalize_timeframe(primary_timeframe),
        "required": sorted(required),
        "missing_before": missing_before,
        "derived_exact": derived,
        "indicator_parameters": {
            "kdj": {"rsv_lookback": 24, "k_alpha": 1.0 / 3.0,
                    "d_alpha": 1.0 / 3.0, "warmup_value": 50.0},
        } if wanted_kdj else {},
        "missing_after": missing_after,
        "errors": errors,
        "proxy_substitution_used": False,
        "at": _now(),
    }


def contract_probe():
    return {
        "ok": True,
        "schema": SCHEMA,
        "valid_directions": list(VALID_DIRECTIONS),
        "fail_closed_on_missing_required_data": True,
        "at": _now(),
    }
