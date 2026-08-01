# -*- coding: utf-8 -*-
"""Multiple-testing judges used before formal strategy review.

The module deliberately keeps return-frequency Sharpe separate from annualized
Sharpe.  DSR operates on the former, because multiplying by ``sqrt(n)`` turns a
Sharpe ratio into a t-statistic and then applying the DSR sampling adjustment
would count sample size twice.

PBO requires either a shared time index or equal-length legacy positionally
aligned series.  It never truncates unequal trade-return lists and pretends
that the Nth trades occurred in the same market period.
"""
from __future__ import print_function

import math
import random
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

try:  # NormalDist is Python 3.8+; production still runs Python 3.6.
    from statistics import NormalDist
except ImportError:  # pragma: no cover - exercised on the production runtime
    NormalDist = None


DSR_PASS_THRESHOLD = 0.95
PBO_PASS_THRESHOLD = 0.40
_MIN_PBO_OBSERVATIONS = 16


_TRIAL_SIMILARITY_WEIGHTS = {
    "family": 0.15,
    "mechanism_id": 0.25,
    "normalized_event": 0.15,
    "factors": 0.20,
    "direction": 0.05,
    "horizon": 0.05,
    "execution_mapping": 0.10,
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _finite(xs):
    out = []
    for x in xs or []:
        if x is None:
            continue
        try:
            v = float(x)
        except (TypeError, ValueError):
            continue
        if math.isnan(v) or math.isinf(v):
            continue
        out.append(v)
    return out


def _normalise_descriptor_value(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.strip().lower().split())
    if isinstance(value, (list, tuple, set)):
        tokens = [_normalise_descriptor_value(item) for item in value]
        return tuple(sorted(set(token for token in tokens if token != "")))
    if isinstance(value, dict):
        return tuple(sorted(
            (str(key), _normalise_descriptor_value(item))
            for key, item in value.items()
            if item is not None
        ))
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return round(value, 12)
    return value


def _normalise_trial_descriptor(descriptor):
    factors = descriptor.get("factors", descriptor.get("factor", descriptor.get("factor_hints")))
    return {
        "family": _normalise_descriptor_value(descriptor.get("family")),
        "mechanism_id": _normalise_descriptor_value(descriptor.get("mechanism_id")),
        "normalized_event": _normalise_descriptor_value(
            descriptor.get("normalized_event", descriptor.get("event", descriptor.get("event_definition")))
        ),
        "factors": _normalise_descriptor_value(factors),
        "direction": _normalise_descriptor_value(
            descriptor.get("direction", descriptor.get("predicted_direction"))
        ),
        "horizon": _normalise_descriptor_value(descriptor.get("horizon")),
        "execution_mapping": _normalise_descriptor_value(descriptor.get("execution_mapping")),
    }


def _descriptor_signature(descriptor):
    payload = json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _factor_similarity(left, right):
    left_set = set(left if isinstance(left, tuple) else ([left] if left != "" else []))
    right_set = set(right if isinstance(right, tuple) else ([right] if right != "" else []))
    if not left_set or not right_set:
        return 0.0
    return float(len(left_set.intersection(right_set))) / float(len(left_set.union(right_set)))


def _descriptor_similarity(left, right):
    similarity = 0.0
    for field, weight in _TRIAL_SIMILARITY_WEIGHTS.items():
        left_value = left.get(field)
        right_value = right.get(field)
        if field == "factors":
            similarity += weight * _factor_similarity(left_value, right_value)
        elif left_value != "" and right_value != "" and left_value == right_value:
            similarity += weight
    return min(1.0, max(0.0, similarity))


def estimate_effective_trials(trial_descriptors, raw_trials=None):
    """Estimate independent trials from auditable structural redundancy.

    Exact structural duplicates count once.  Remaining unique trials receive a
    pairwise similarity proxy based on family, mechanism, event, factors,
    direction, horizon, and execution mapping.  A Kish-style effective count is
    used with a conservative ``sqrt(unique)`` floor.  Missing/unclassified raw
    trials retain full weight, so incomplete descriptors cannot weaken the
    multiple-testing penalty.
    """
    descriptors = list(trial_descriptors or [])
    valid = []
    for row in descriptors:
        if not isinstance(row, dict):
            continue
        normalised = _normalise_trial_descriptor(row)
        # Exact-deduplication is safe only when the descriptor identifies a
        # family and at least one actual mechanism/event/factor dimension.
        if normalised.get("family") and any(
            normalised.get(field) for field in ("mechanism_id", "normalized_event", "factors")
        ):
            valid.append(normalised)
    try:
        supplied_raw = int(raw_trials) if raw_trials is not None else len(descriptors)
    except (TypeError, ValueError):
        supplied_raw = len(descriptors)
    raw = max(0, supplied_raw, len(descriptors))

    clusters = {}
    for descriptor in valid:
        signature = _descriptor_signature(descriptor)
        if signature not in clusters:
            clusters[signature] = {"descriptor": descriptor, "count": 0}
        clusters[signature]["count"] += 1
    unique_rows = [row["descriptor"] for row in clusters.values()]
    unique_count = len(unique_rows)

    similarities = []
    for left_index in range(unique_count):
        for right_index in range(left_index + 1, unique_count):
            similarities.append(_descriptor_similarity(unique_rows[left_index], unique_rows[right_index]))
    denominator = float(unique_count) + 2.0 * sum(similarities)
    kish_effective = (float(unique_count) ** 2 / denominator) if denominator > 0 else 0.0
    conservative_floor = math.sqrt(float(unique_count)) if unique_count else 0.0
    structured_effective = min(
        float(unique_count),
        max(kish_effective, conservative_floor),
    ) if unique_count else 0.0

    # Invalid descriptor rows and raw trials not represented by descriptors are
    # unclassified.  Count them independently rather than assuming correlation.
    unclassified = max(0, raw - len(valid))
    effective = structured_effective + float(unclassified)
    if raw > 0:
        effective = min(float(raw), max(1.0, effective))

    family_counts = {}
    for row in valid:
        family = row.get("family") or "__missing__"
        family_counts[family] = family_counts.get(family, 0) + 1
    cluster_evidence = []
    for signature, cluster in sorted(clusters.items()):
        cluster_evidence.append({
            "signature": signature,
            "count": cluster["count"],
            "family": cluster["descriptor"].get("family") or None,
            "mechanism_id": cluster["descriptor"].get("mechanism_id") or None,
        })

    return {
        "ok": True,
        "method": "structural_similarity_kish_with_sqrt_floor_v1",
        "raw_trials": raw,
        "descriptor_trials": len(descriptors),
        "classified_trials": len(valid),
        "unclassified_trials_full_weight": unclassified,
        "unique_signatures": unique_count,
        "duplicate_trials": max(0, len(valid) - unique_count),
        "effective_structured_trials": structured_effective,
        "effective_trials": effective,
        "kish_effective_before_floor": kish_effective,
        "conservative_sqrt_unique_floor": conservative_floor,
        "pairwise_similarity_mean": (
            sum(similarities) / float(len(similarities)) if similarities else 0.0
        ),
        "pairwise_similarity_max": max(similarities) if similarities else 0.0,
        "family_counts": family_counts,
        "similarity_weights": dict(_TRIAL_SIMILARITY_WEIGHTS),
        "signature_clusters": cluster_evidence,
        "coverage_ratio": (float(len(valid)) / float(raw)) if raw else 1.0,
        "note_zh": "结构完全重复仅计一次；同家族/机制/事件/因子等按可审计相似度降权。缺失描述的试验按独立试验全额计入，禁止用不完整证据降低惩罚。",
        "at": _now(),
    }


def _normalise_timestamp(value):
    """Return a sortable/hashable timestamp without guessing mixed formats."""
    if isinstance(value, datetime):
        if value.utcoffset() is not None:
            return ("epoch", value.timestamp())
        return ("naive_datetime", value.isoformat())
    if isinstance(value, date):
        return ("naive_datetime", datetime.combine(value, datetime.min.time()).isoformat())
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            raise ValueError("non_finite_timestamp")
        return ("number", number)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty_timestamp")
        # Canonicalise ISO timestamps when possible.  Other strings retain their
        # lexical order; callers should use ISO-8601 for chronological ordering.
        try:
            if hasattr(datetime, "fromisoformat"):
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            else:  # Python 3.6 compatibility for ISO-8601 used by PBO clocks.
                iso = text
                tzinfo = None
                if iso.endswith("Z"):
                    iso = iso[:-1]
                    tzinfo = timezone.utc
                else:
                    match = __import__("re").search(r"([+-])(\d{2}):(\d{2})$", iso)
                    if match:
                        sign = 1 if match.group(1) == "+" else -1
                        offset = timedelta(
                            hours=sign * int(match.group(2)),
                            minutes=sign * int(match.group(3)),
                        )
                        tzinfo = timezone(offset)
                        iso = iso[:match.start()]
                parsed = None
                for pattern in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        parsed = datetime.strptime(iso, pattern).replace(tzinfo=tzinfo)
                        break
                    except ValueError:
                        continue
                if parsed is None:
                    raise ValueError("unsupported_iso_timestamp")
        except (TypeError, ValueError):
            return ("string", text)
        if parsed.utcoffset() is not None:
            return ("epoch", parsed.timestamp())
        return ("naive_datetime", parsed.isoformat())
    raise ValueError("unsupported_timestamp_type")


def _parse_return_series(series):
    """Parse numeric, mapping, pair, or record return-series representations."""
    if series is None:
        return {"values": [], "time_map": None, "error": None}

    timestamped = None
    if isinstance(series, dict):
        if "returns" in series or "timestamps" in series:
            returns = series.get("returns")
            timestamps = series.get("timestamps")
            if not isinstance(returns, (list, tuple)) or not isinstance(timestamps, (list, tuple)):
                return {"values": [], "time_map": None, "error": "invalid_timestamped_wrapper"}
            if len(returns) != len(timestamps):
                return {"values": [], "time_map": None, "error": "timestamp_return_length_mismatch"}
            timestamped = list(zip(timestamps, returns))
        else:
            timestamped = list(series.items())
    else:
        try:
            rows = list(series)
        except TypeError:
            return {"values": [], "time_map": None, "error": "series_not_iterable"}
        if rows and all(isinstance(row, dict) for row in rows):
            timestamped = []
            for row in rows:
                ts = row.get("timestamp", row.get("time", row.get("ts")))
                value = row.get("return", row.get("ret", row.get("value", row.get("pnl"))))
                if ts is None or value is None:
                    return {"values": [], "time_map": None, "error": "invalid_timestamped_record"}
                timestamped.append((ts, value))
        elif rows and all(isinstance(row, (list, tuple)) and len(row) == 2 for row in rows):
            timestamped = rows
        else:
            return {"values": _finite(rows), "time_map": None, "error": None}

    time_map = {}
    values = []
    for raw_time, raw_return in timestamped or []:
        try:
            key = _normalise_timestamp(raw_time)
            value = float(raw_return)
        except (TypeError, ValueError):
            return {"values": [], "time_map": None, "error": "invalid_timestamp_or_return"}
        if math.isnan(value) or math.isinf(value):
            return {"values": [], "time_map": None, "error": "non_finite_timestamped_return"}
        if key in time_map:
            return {"values": [], "time_map": None, "error": "duplicate_timestamp"}
        time_map[key] = value
        values.append(value)
    return {"values": values, "time_map": time_map, "error": None}


def _return_values(returns):
    parsed = _parse_return_series(returns)
    if parsed.get("error"):
        return []
    return parsed.get("values") or []


def sharpe_ratio(returns, periods_per_year=None):
    """Return sample Sharpe at the input return frequency.

    The default is ``mean(return) / sample_std(return)``.  Annualization is
    explicit and uses ``sqrt(periods_per_year)``; sample count is never an
    annualization factor.
    """
    rets = _return_values(returns)
    n = len(rets)
    if n < 5:
        return None
    mean = sum(rets) / float(n)
    var = sum((x - mean) ** 2 for x in rets) / float(max(n - 1, 1))
    vol = math.sqrt(var) if var > 0 else 0.0
    if vol <= 1e-12:
        result = 0.0
    else:
        result = mean / vol
    if periods_per_year is not None:
        factor = float(periods_per_year)
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError("periods_per_year must be a finite positive number")
        result *= math.sqrt(factor)
    return result


def annualized_sharpe_ratio(returns, periods_per_year):
    """Explicit annualized Sharpe convenience wrapper."""
    return sharpe_ratio(returns, periods_per_year=periods_per_year)


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))


def _norm_ppf(probability):
    """Inverse standard-normal CDF with a Python 3.6-safe fallback.

    The fallback is Peter J. Acklam's rational approximation.  Its error is
    far below the precision needed by the effective-trial DSR threshold.
    """
    p = float(probability)
    if not 0.0 < p < 1.0:
        raise ValueError("normal probability must be between zero and one")
    if NormalDist is not None:
        return NormalDist().inv_cdf(p)

    a = (
        -3.969683028665376e01, 2.209460984245205e02,
        -2.759285104469687e02, 1.383577518672690e02,
        -3.066479806614716e01, 2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01, 1.615858368580409e02,
        -1.556989798598866e02, 6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03, -3.223964580411365e-01,
        -2.400758277161838e00, -2.549732539343734e00,
        4.374664141464968e00, 2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03, 3.224671290700398e-01,
        2.445134137142996e00, 3.754408661907416e00,
    )
    p_low = 0.02425
    p_high = 1.0 - p_low
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q) + 1.0
        )
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r) + 1.0
        )
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q) + 1.0
    )


def _expected_max_null_sharpe(n_trials, sample_size):
    """Approximate expected maximum *non-annualized* Sharpe under the null."""
    trials = max(1.0, float(n_trials))
    if trials <= 1.0 or sample_size < 2:
        return 0.0
    # Bailey/Lopez de Prado approximation.  Normal quantiles live on the
    # z-statistic scale, so convert them to return-frequency SR scale exactly
    # once using sqrt(n - 1).
    euler_gamma = 0.5772156649015329
    q1 = min(1.0 - 1e-12, max(1e-12, 1.0 - (1.0 / trials)))
    q2 = min(1.0 - 1e-12, max(1e-12, 1.0 - (1.0 / (trials * math.e))))
    expected_max_z = (
        (1.0 - euler_gamma) * _norm_ppf(q1)
        + euler_gamma * _norm_ppf(q2)
    )
    return expected_max_z / math.sqrt(float(sample_size - 1))


def deflated_sharpe_ratio(returns, n_trials, skew=None, kurt=None):
    """Return DSR probability on a consistent non-annualized Sharpe scale.

    ``dsr`` is the approximate probability that the observed return-frequency
    Sharpe exceeds the expected maximum null Sharpe after accounting for the
    supplied effective trial count.  The pass threshold remains 0.95.
    """
    rets = _return_values(returns)
    sr = sharpe_ratio(rets)
    if sr is None:
        return {
            "ok": False,
            "error": "insufficient",
            "dsr": None,
            "psr": None,
            "passed": False,
            "threshold": DSR_PASS_THRESHOLD,
        }
    n = len(rets)
    try:
        n_trials = max(1.0, float(n_trials or 1.0))
    except (TypeError, ValueError):
        n_trials = 1.0
    if not math.isfinite(n_trials):
        n_trials = 1.0
    sr0 = _expected_max_null_sharpe(n_trials, n)

    mean = sum(rets) / float(n)
    centered = [x - mean for x in rets]
    m2 = sum(x * x for x in centered) / float(n)
    m3 = sum(x ** 3 for x in centered) / float(n)
    m4 = sum(x ** 4 for x in centered) / float(n)
    if skew is None:
        skew = 0.0 if m2 <= 1e-18 else m3 / (m2 ** 1.5)
    if kurt is None:
        kurt = 3.0 if m2 <= 1e-18 else m4 / (m2 ** 2)

    variance_factor = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * (sr ** 2)
    variance_factor = max(variance_factor, 1e-8)
    se = math.sqrt(variance_factor / max(n - 1, 1))
    z_dsr = (sr - sr0) / se if se > 0 else 0.0
    z_psr = sr / se if se > 0 else 0.0
    dsr = _norm_cdf(z_dsr)
    return {
        "ok": True,
        "sharpe": sr,
        "sharpe_type": "non_annualized_return_frequency",
        "sample_size": n,
        "sr0_null_max": sr0,
        "n_trials_effective": n_trials,
        "skew": skew,
        "kurtosis": kurt,
        "standard_error": se,
        "psr": _norm_cdf(z_psr),
        "dsr": dsr,
        "threshold": DSR_PASS_THRESHOLD,
        "passed": bool(dsr >= DSR_PASS_THRESHOLD),
        "note_zh": "DSR使用输入收益频率的非年化Sharpe；样本量仅在抽样误差中计算一次，门槛保持0.95。",
        "at": _now(),
    }


def _pbo_error(error, **extra):
    result = {
        "ok": False,
        "error": error,
        "pbo": None,
        "passed": False,
        "threshold": PBO_PASS_THRESHOLD,
        "at": _now(),
    }
    result.update(extra)
    return result


def _aligned_candidates(returns_matrix):
    parsed = [_parse_return_series(series) for series in (returns_matrix or [])]
    if len(parsed) < 2:
        return None, _pbo_error("need_>=2_candidates", blocking=False)
    errors = [row.get("error") for row in parsed if row.get("error")]
    if errors:
        return None, _pbo_error("invalid_candidate_series", details=errors, blocking=True)

    has_time = [row.get("time_map") is not None for row in parsed]
    if any(has_time) and not all(has_time):
        return None, _pbo_error(
            "mixed_timestamped_and_legacy_series",
            blocking=True,
            alignment_mode="rejected",
        )

    if all(has_time):
        common = set(parsed[0]["time_map"])
        for row in parsed[1:]:
            common.intersection_update(row["time_map"])
        timestamp_kinds = {key[0] for key in common}
        if len(timestamp_kinds) > 1:
            return None, _pbo_error(
                "mixed_timestamp_kinds",
                blocking=True,
                timestamp_kinds=sorted(timestamp_kinds),
                alignment_mode="rejected",
            )
        ordered = sorted(common)
        if len(ordered) < _MIN_PBO_OBSERVATIONS:
            return None, _pbo_error(
                "insufficient_common_timestamps",
                blocking=True,
                n_common_observations=len(ordered),
                alignment_mode="common_time_index",
            )
        aligned = [[row["time_map"][timestamp] for timestamp in ordered] for row in parsed]
        meta = {
            "alignment_mode": "common_time_index",
            "n_common_observations": len(ordered),
            "dropped_observations_by_candidate": [len(row["values"]) - len(ordered) for row in parsed],
        }
        return aligned, meta

    lengths = [len(row.get("values") or []) for row in parsed]
    if any(length < _MIN_PBO_OBSERVATIONS for length in lengths):
        return None, _pbo_error(
            "candidate_too_short",
            blocking=True,
            candidate_lengths=lengths,
            min_observations=_MIN_PBO_OBSERVATIONS,
        )
    if len(set(lengths)) != 1:
        return None, _pbo_error(
            "unaligned_legacy_lengths",
            blocking=True,
            candidate_lengths=lengths,
            alignment_mode="rejected_no_time_index",
            note_zh="无时间索引且长度不等，禁止按第N笔交易截断对齐。请传共同时间戳。",
        )
    return [row["values"] for row in parsed], {
        "alignment_mode": "positional_legacy_equal_length",
        "n_common_observations": lengths[0],
        "alignment_assumption": "caller_guarantees_same_observation_clock",
        "note_zh": "兼容旧输入：仅在等长时按位置对齐；调用方必须保证各位置属于同一市场时点。",
    }


def _average_rank_percentile(scores, index):
    """Tie-aware ascending percentile; 0.5 is the exact median rank."""
    value = scores[index]
    lower = sum(1 for score in scores if score < value)
    equal = sum(1 for score in scores if score == value)
    return (float(lower) + 0.5 * float(equal)) / float(len(scores))


def pbo_cscv(returns_matrix, n_partitions=8, seed=11):
    """Estimate PBO using contiguous blocks on a genuinely common clock.

    Timestamped candidates are aligned by intersection of timestamps.  Legacy
    numeric lists are accepted only when all lengths are equal, making the
    positional-clock assumption explicit in the result.
    """
    cands, alignment = _aligned_candidates(returns_matrix)
    if cands is None:
        return alignment
    if all(series == cands[0] for series in cands[1:]):
        return _pbo_error(
            "need_>=2_distinct_candidates",
            blocking=False,
            alignment_mode=alignment.get("alignment_mode"),
            n_common_observations=alignment.get("n_common_observations"),
        )

    n = len(cands[0])
    n_partitions = max(4, min(int(n_partitions), 12, n))
    if n_partitions % 2 == 1:
        n_partitions -= 1
    if n_partitions < 4:
        return _pbo_error("insufficient_partitions", blocking=True)

    rng = random.Random(int(seed))
    part = max(1, n // n_partitions)
    idxs = list(range(n_partitions))
    from itertools import combinations
    half = n_partitions // 2
    combos = list(combinations(idxs, half))
    if len(combos) > 40:
        rng.shuffle(combos)
        combos = combos[:40]

    overfit_weight = 0.0
    tie_combos = 0
    total = 0
    for train_parts in combos:
        train_set = set(train_parts)
        is_scores = []
        oos_scores = []
        for series in cands:
            is_rets = []
            oos_rets = []
            for partition in range(n_partitions):
                start = partition * part
                stop = n if partition == n_partitions - 1 else min(n, (partition + 1) * part)
                chunk = series[start:stop]
                if partition in train_set:
                    is_rets.extend(chunk)
                else:
                    oos_rets.extend(chunk)
            is_score = sharpe_ratio(is_rets)
            oos_score = sharpe_ratio(oos_rets)
            # Preserve a valid zero Sharpe.  Only an unavailable score receives
            # the explicit floor; ``score or -999`` would corrupt zero.
            is_scores.append(is_score if is_score is not None else -999.0)
            oos_scores.append(oos_score if oos_score is not None else -999.0)

        best_score = max(is_scores)
        winners = [i for i, score in enumerate(is_scores) if score == best_score]
        if len(winners) > 1:
            tie_combos += 1
        # Average over all tied IS winners, avoiding candidate-order bias.
        combo_tokens = [
            1.0 if _average_rank_percentile(oos_scores, index) < 0.5 else 0.0
            for index in winners
        ]
        overfit_weight += sum(combo_tokens) / float(len(combo_tokens))
        total += 1

    pbo = float(overfit_weight) / float(max(total, 1))
    result = {
        "ok": True,
        "pbo": pbo,
        "n_candidates": len(cands),
        "n_combos": total,
        "n_partitions": n_partitions,
        "n_is_tie_combos": tie_combos,
        "threshold": PBO_PASS_THRESHOLD,
        "passed": bool(pbo <= PBO_PASS_THRESHOLD),
        "note_zh": "PBO按共同时间块比较；样本内并列最优及样本外并列名次均采用平均处理。",
        "at": _now(),
    }
    result.update(alignment)
    return result


def purged_walk_slices(n, n_folds=5, embargo=3):
    """Yield chronological test paths plus purged/embargoed reference indices."""
    n = int(n)
    n_folds = max(2, int(n_folds))
    embargo = max(0, int(embargo))
    fold = max(1, n // n_folds)
    slices = []
    for i in range(n_folds):
        test_a = i * fold
        test_b = n if i == n_folds - 1 else min(n, (i + 1) * fold)
        test_idx = list(range(test_a, test_b))
        purge_a = max(0, test_a - embargo)
        purge_b = min(n, test_b + embargo)
        train_idx = [j for j in range(n) if j < purge_a or j >= purge_b]
        if len(train_idx) >= 10 and len(test_idx) >= 5:
            slices.append({"train": train_idx, "test": test_idx, "fold": i})
    return slices


def path_stability_sharpes(returns, n_folds=5, embargo=3):
    """Describe return-path stability; this is intentionally not full CPCV.

    There is no model selection/retraining callback here, so test-block scores
    are diagnostics rather than independently selected out-of-sample estimates.
    """
    rets = _return_values(returns)
    slices = purged_walk_slices(len(rets), n_folds=n_folds, embargo=embargo)
    path_scores = []
    for sl in slices:
        test = [rets[i] for i in sl["test"]]
        score = sharpe_ratio(test)
        if score is not None:
            path_scores.append(score)
    if not path_scores:
        return {
            "ok": False,
            "error": "no_folds",
            "path_sharpes": [],
            "oos_sharpes": [],
            "method": "walk_forward_path_stability",
            "is_full_cpcv": False,
            "selection_retrained_each_fold": False,
        }
    ordered = sorted(path_scores)
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0
    return {
        "ok": True,
        "method": "walk_forward_path_stability",
        "is_full_cpcv": False,
        "selection_retrained_each_fold": False,
        "path_sharpes": path_scores,
        "median_path_sharpe": median,
        # Compatibility fields; explicitly labelled below and not advertised as
        # proof of out-of-sample selection performance.
        "oos_sharpes": path_scores,
        "median_oos_sharpe": median,
        "n_folds": len(path_scores),
        "embargo": embargo,
        "note_zh": "这是收益路径稳定性诊断；未在每折重新训练及选模，因此不是完整CPCV或独立OOS证明。",
        "at": _now(),
    }


def cpcv_oos_sharpes(returns, n_folds=5, embargo=3):
    """Backward-compatible alias for :func:`path_stability_sharpes`."""
    result = path_stability_sharpes(returns, n_folds=n_folds, embargo=embargo)
    result["compatibility_alias"] = "cpcv_oos_sharpes"
    return result


def _same_series(left, right):
    if left is right:
        return True
    try:
        equal = left == right
        return equal if isinstance(equal, bool) else False
    except Exception:
        return False


def evaluate_multiple_testing(best_returns, candidate_returns_list, n_trials_effective,
                              pbo_returns_matrix=None):
    dsr = deflated_sharpe_ratio(best_returns, n_trials=n_trials_effective)
    matrix = list(
        pbo_returns_matrix if pbo_returns_matrix is not None
        else (candidate_returns_list or [])
    )
    if pbo_returns_matrix is None and not any(
        _same_series(best_returns, candidate) for candidate in matrix
    ):
        matrix = [best_returns] + matrix
    pbo = pbo_cscv(matrix)
    stability = path_stability_sharpes(best_returns)

    if pbo.get("ok"):
        passed = bool(dsr.get("ok") and dsr.get("passed") and pbo.get("passed"))
        decision_basis = "dsr_and_pbo"
    elif pbo.get("blocking"):
        # Multiple candidates were supplied but could not be validly aligned.
        # Fail closed instead of silently falling back to a weaker gate.
        passed = False
        decision_basis = "blocked_invalid_pbo_alignment"
    else:
        # With only one distinct candidate PBO selection-overfit is undefined;
        # preserve the historical DSR + positive path-median fallback.
        median = stability.get("median_path_sharpe")
        passed = bool(dsr.get("passed") and median is not None and median > 0)
        decision_basis = "single_candidate_dsr_and_path_stability"

    return {
        "ok": True,
        "passed": passed,
        "decision_basis": decision_basis,
        "dsr": dsr,
        "pbo": pbo,
        "path_stability": stability,
        "cpcv": dict(stability, compatibility_alias="cpcv"),
        "n_trials_effective": n_trials_effective,
        "pbo_input": "common_bar_clock" if pbo_returns_matrix is not None else "candidate_returns_list",
        "thresholds_unchanged": {
            "dsr_min": DSR_PASS_THRESHOLD,
            "pbo_max": PBO_PASS_THRESHOLD,
        },
        "note_zh": "统计裁判使用DSR与按共同时间对齐的PBO；路径稳定性不冒充完整CPCV。无效对齐时失败关闭。",
        "at": _now(),
    }


def probe():
    return {
        "ok": True,
        "provider": "multiple_testing_v2",
        "methods": ["DSR_non_annualized", "PBO_CSCV_time_aligned", "path_stability"],
        "compatibility_aliases": ["cpcv_oos_sharpes", "result.cpcv"],
        "thresholds": {"dsr_min": DSR_PASS_THRESHOLD, "pbo_max": PBO_PASS_THRESHOLD},
        "at": _now(),
    }
