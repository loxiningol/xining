# -*- coding: utf-8 -*-
"""Layer B quality optimization — diagnose, constrain, repair (never relax fixed contract).

Fixed contract (immutable):
  leverage=20, stop=0.5% price, avg winning levered >= 11.11% for formal pass.
Handoff floors remain in manufacture_batch_policy.

This module does NOT change stop/leverage. It only proposes entry/state/sequence
repairs and diversity enforcement for candidates that already materialized.
"""
from __future__ import print_function

from datetime import datetime

# Failure taxonomy (plan §12.1) — complete set for P4
LOW_WIN_RATE = "LOW_WIN_RATE"
LOW_AVG_WINNING_RETURN = "LOW_AVG_WINNING_RETURN"
LOW_PROFIT_FIRST_RATE = "LOW_PROFIT_FIRST_RATE"
HIGH_MAE = "HIGH_MAE"
LOW_FREQUENCY = "LOW_FREQUENCY"
RECENT_2Y_FAILURE = "RECENT_2Y_FAILURE"
REGIME_INSTABILITY = "REGIME_INSTABILITY"
PARAMETER_SPIKE = "PARAMETER_SPIKE"
COST_FAILURE = "COST_FAILURE"
DELAY_FAILURE = "DELAY_FAILURE"
PNL_CONCENTRATION = "PNL_CONCENTRATION"
NEAR_DUPLICATE = "NEAR_DUPLICATE"

ALL_FAILURE_CODES = (
    LOW_WIN_RATE,
    LOW_AVG_WINNING_RETURN,
    LOW_PROFIT_FIRST_RATE,
    HIGH_MAE,
    LOW_FREQUENCY,
    RECENT_2Y_FAILURE,
    REGIME_INSTABILITY,
    PARAMETER_SPIKE,
    COST_FAILURE,
    DELAY_FAILURE,
    PNL_CONCENTRATION,
    NEAR_DUPLICATE,
)

REPRESENTATION_TYPES = (
    "threshold",
    "rank",
    "delta",
    "sequence",
    "state",
    "relative_exclusion",
)

# Allowed modifications by failure code (plan §12.2)
ALLOWED_MODS = {
    LOW_WIN_RATE: (
        "entry_confirmation", "forbidden_state", "event_sequence",
        "relative_btc", "drop_failing_direction",
    ),
    LOW_AVG_WINNING_RETURN: (
        "exit_logic", "extend_profit_capture", "staged_protection",
        "trend_continuation_exit", "max_hold",
    ),
    LOW_PROFIT_FIRST_RATE: (
        "entry_timing", "path_confirmation", "exclude_fail_state", "event_sequence",
    ),
    HIGH_MAE: (
        "entry_timing", "path_confirmation", "exclude_fail_state", "event_sequence",
    ),
    LOW_FREQUENCY: (
        "adjacent_param_plateau", "same_mechanism_low_corr_repr", "expand_state_coverage",
    ),
    RECENT_2Y_FAILURE: (
        "relocate_to_recent_regime", "recent_features", "document_old_mechanism_death",
    ),
    REGIME_INSTABILITY: (
        "regime_filter", "drop_unstable_slice", "recent_features",
    ),
    PARAMETER_SPIKE: (
        "adjacent_param_plateau", "widen_param_tolerance", "same_mechanism_low_corr_repr",
    ),
    COST_FAILURE: (
        "raise_edge_buffer", "reduce_turnover", "path_confirmation",
    ),
    DELAY_FAILURE: (
        "entry_timing", "event_sequence", "path_confirmation",
    ),
    PNL_CONCENTRATION: (
        "expand_state_coverage", "same_mechanism_low_corr_repr", "drop_outlier_cluster",
    ),
    NEAR_DUPLICATE: (
        "same_mechanism_low_corr_repr", "drop_failing_direction",
    ),
}

FORBIDDEN_ALWAYS = (
    "change_protective_stop",
    "change_leverage",
    "lower_avg_winning_1111",
    "bypass_handoff_floor",
)

# P4 thresholds (diagnostic; never relax handoff/formal floors)
PLATEAU_MIN_SCORE = 0.55
PNL_TOP_SHARE_MAX = 0.40
REGIME_PASS_RATE_MIN = 0.50
COST_STRESS_EDGE_MIN = 0.0
DELAY_STRESS_EDGE_MIN = 0.0


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _f(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def classify_package_failures(metrics, handoff_gate=None):
    """Return ordered failure codes for one manufactured package."""
    m = metrics or {}
    codes = []
    wr = _f(m.get("win_rate"))
    if wr is None and m.get("win_rate_pct") is not None:
        wr = _f(m.get("win_rate_pct"))
        if wr is not None and wr > 1.5:
            wr = wr / 100.0
    mean_win = _f(m.get("mean_win_only_pct"))
    if mean_win is not None and 0 < abs(mean_win) < 0.5:
        mean_win = mean_win * 100.0
    pfr = _f(m.get("profit_first_rate"))
    mae = _f(m.get("median_mae_pct") if m.get("median_mae_pct") is not None else m.get("median_mae"))
    weekly = _f(m.get("weekly_opens"))

    if wr is not None and wr < 0.45:
        codes.append(LOW_WIN_RATE)
    if mean_win is not None and mean_win < 9.0:
        codes.append(LOW_AVG_WINNING_RETURN)
    if pfr is not None and pfr < 0.55:
        codes.append(LOW_PROFIT_FIRST_RATE)
    if mae is not None and abs(mae) > 0.0035:
        codes.append(HIGH_MAE)
    if weekly is not None and weekly < 0.5:
        codes.append(LOW_FREQUENCY)
    if m.get("recent_2y_requirement_passed") is False:
        codes.append(RECENT_2Y_FAILURE)

    # P4 extended diagnostics (optional fields — only fire when scored).
    regime_rate = _f(m.get("regime_slice_pass_rate"))
    if regime_rate is not None and regime_rate < REGIME_PASS_RATE_MIN:
        codes.append(REGIME_INSTABILITY)
    elif m.get("regime_stable") is False:
        codes.append(REGIME_INSTABILITY)

    plateau = _f(m.get("parameter_plateau_score"))
    if plateau is not None and plateau < PLATEAU_MIN_SCORE:
        codes.append(PARAMETER_SPIKE)
    elif m.get("parameter_spike") is True:
        codes.append(PARAMETER_SPIKE)

    if m.get("cost_stress_passed") is False:
        codes.append(COST_FAILURE)
    else:
        cost_edge = _f(m.get("cost_stress_mean_net"))
        if cost_edge is not None and cost_edge < COST_STRESS_EDGE_MIN:
            codes.append(COST_FAILURE)

    if m.get("delay_stress_passed") is False:
        codes.append(DELAY_FAILURE)
    else:
        delay_edge = _f(m.get("delay_stress_mean_net"))
        if delay_edge is not None and delay_edge < DELAY_STRESS_EDGE_MIN:
            codes.append(DELAY_FAILURE)

    top_share = _f(m.get("top_trade_pnl_share"))
    if top_share is not None and top_share > PNL_TOP_SHARE_MAX:
        codes.append(PNL_CONCENTRATION)
    elif m.get("pnl_concentrated") is True:
        codes.append(PNL_CONCENTRATION)

    gate = handoff_gate or {}
    for reason in gate.get("reasons") or []:
        r = str(reason)
        if "near_duplicate" in r and NEAR_DUPLICATE not in codes:
            codes.append(NEAR_DUPLICATE)
        if "recent_2y" in r and RECENT_2Y_FAILURE not in codes:
            codes.append(RECENT_2Y_FAILURE)
        if "cost" in r and COST_FAILURE not in codes:
            codes.append(COST_FAILURE)
        if "delay" in r and DELAY_FAILURE not in codes:
            codes.append(DELAY_FAILURE)

    # Deduplicate while preserving order.
    seen = set()
    ordered = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            ordered.append(code)
    codes = ordered

    return {
        "codes": codes,
        "primary": codes[0] if codes else None,
        "allowed_modifications": sorted(set(
            mod
            for code in codes
            for mod in (ALLOWED_MODS.get(code) or ())
        )),
        "forbidden": list(FORBIDDEN_ALWAYS),
        "observed": {
            "win_rate": wr,
            "mean_win_only_pct": mean_win,
            "profit_first_rate": pfr,
            "median_mae_pct": mae,
            "weekly_opens": weekly,
            "parameter_plateau_score": plateau,
            "top_trade_pnl_share": top_share,
            "regime_slice_pass_rate": regime_rate,
            "recent_2y_requirement_passed": m.get("recent_2y_requirement_passed"),
            "cost_stress_passed": m.get("cost_stress_passed"),
            "delay_stress_passed": m.get("delay_stress_passed"),
        },
    }


def classify_batch(packages):
    """Aggregate failure distribution across manufactured packages."""
    dist = {}
    rows = []
    for pack in packages or []:
        diag = classify_package_failures(
            pack.get("select_metrics") or {},
            pack.get("handoff_gate"),
        )
        rows.append({
            "recipe_id": pack.get("recipe_id"),
            "rank": pack.get("rank"),
            "diagnosis": diag,
        })
        for code in diag.get("codes") or []:
            dist[code] = int(dist.get(code) or 0) + 1
    dominant = sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))
    dominant_codes = [code for code, _ in dominant]
    allowed = {}
    for code in dominant_codes:
        allowed[code] = list(ALLOWED_MODS.get(code) or ())
    return {
        "n_packages": len(packages or []),
        "failure_distribution": dist,
        "dominant_codes": dominant_codes,
        "allowed_modifications": allowed,
        "forbidden_always": list(FORBIDDEN_ALWAYS),
        "rows": rows[:40],
        "at": _now(),
    }


def representation_type_for_skeleton(skeleton):
    """Map skeleton fields → one of the six fixed representation types."""
    sk = skeleton or {}
    if sk.get("representation_type") in REPRESENTATION_TYPES:
        return sk.get("representation_type")
    wave = str(sk.get("wave") or "")
    triggers = " ".join(str(x) for x in (sk.get("trigger") or []))
    conf = " ".join(str(x) for x in (sk.get("confirmation") or []))
    excl = " ".join(str(x) for x in (sk.get("exclusion") or []))
    blob = (triggers + " " + conf + " " + excl).lower()
    if "delta" in blob or "slope" in blob or "velocity" in blob:
        return "delta"
    if "within" in blob or "then" in blob or "sequence" in wave:
        return "sequence"
    if excl.strip() and ("btc" in blob or "relative" in blob or "not" in blob or "exclude" in blob):
        return "relative_exclusion"
    if "state" in wave or "squeeze" in blob or "trend_bias" in blob or "volatility" in blob:
        return "state"
    if "percentile" in blob or "rank" in blob or "q0" in blob:
        return "rank"
    return "threshold"


def diversity_audit(hypotheses):
    """Fail if pool collapses to one family / one representation type."""
    rows = list(hypotheses or [])
    n = len(rows)
    if n <= 0:
        return {
            "ok": False,
            "reasons": ["empty_population"],
            "unique_strategy_families": 0,
            "largest_family_share": 1.0,
            "representation_counts": {},
            "near_duplicate_share": 1.0,
        }
    families = {}
    repr_counts = {}
    fingerprints = []
    for h in rows:
        fam = str(h.get("family") or h.get("mechanism_id") or "unknown")
        families[fam] = int(families.get(fam) or 0) + 1
        sk = h.get("materialization_skeleton") or {}
        rtype = h.get("representation_type") or representation_type_for_skeleton(sk)
        repr_counts[rtype] = int(repr_counts.get(rtype) or 0) + 1
        hints = tuple(sorted(str(x) for x in (h.get("factor_hints") or [])[:6]))
        fingerprints.append((fam, hints, rtype))
    largest = max(families.values()) if families else 0
    largest_share = float(largest) / float(n)
    # Near-duplicate: same family + same first-3 hints
    seen = {}
    dup = 0
    for fam, hints, rtype in fingerprints:
        key = (fam, hints[:3])
        if key in seen:
            dup += 1
        seen[key] = True
    dup_share = float(dup) / float(n)
    missing_repr = [r for r in REPRESENTATION_TYPES if not repr_counts.get(r)]
    reasons = []
    if len(families) < 3:
        reasons.append("unique_families_below_3")
    if largest_share > 0.35 + 1e-12:
        reasons.append("largest_family_share_above_0.35")
    if dup_share > 0.25 + 1e-12:
        reasons.append("near_duplicate_share_above_0.25")
    if len(repr_counts) < 4:
        reasons.append("representation_types_below_4")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "unique_strategy_families": len(families),
        "family_counts": families,
        "largest_family_share": largest_share,
        "representation_counts": repr_counts,
        "missing_representation_types": missing_repr,
        "near_duplicate_share": dup_share,
        "n": n,
    }


def quality_repair_event_defs(failure_codes=None):
    """Stricter confirmation + exclusion event definitions for Layer B repair.

    These are probe event specs (feature, side, q) — stop/leverage untouched.
    """
    codes = set(failure_codes or [])
    defs = []
    # Path / MAE / WR repairs: demand reclaim + velocity decay + width guard
    if codes & {LOW_PROFIT_FIRST_RATE, HIGH_MAE, LOW_WIN_RATE} or not codes:
        defs.extend([
            (
                "qopt_bb_rsi_confirm_exclude",
                (
                    ("rsi_14", "low", 0.75),
                    ("bb_lower_dist", "low", 0.75),
                    ("bb_mid_reclaim", "high", 0.70),
                    ("downside_velocity_decay", "high", 0.70),
                    ("bb_width", "low", 0.65),
                ),
                "sequence",
            ),
            (
                "qopt_reclaim_state_filter",
                (
                    ("rsi_14", "low", 0.70),
                    ("bullish_reclaim", "high", 0.75),
                    ("reclaim_strength", "high", 0.70),
                    ("squeeze_persistence", "high", 0.60),
                    ("volatility_acceleration", "low", 0.70),
                ),
                "state",
            ),
            (
                "qopt_delta_turn_confirm",
                (
                    ("rsi_14", "low", 0.75),
                    ("delta_rsi_14", "high", 0.70),
                    ("close_location", "high", 0.65),
                    ("trend_bias_50_200", "high", 0.55),
                ),
                "delta",
            ),
            (
                "qopt_relative_exclusion",
                (
                    ("bb_lower_dist", "low", 0.70),
                    ("bb_mid_reclaim", "high", 0.70),
                    ("expansion_score", "low", 0.70),
                    ("volume_z", "high", 0.55),
                ),
                "relative_exclusion",
            ),
        ])
    if LOW_AVG_WINNING_RETURN in codes:
        defs.append((
            "qopt_strong_reclaim_only",
            (
                ("rsi_14", "low", 0.80),
                ("bb_mid_reclaim", "high", 0.80),
                ("reclaim_strength", "high", 0.80),
                ("downside_velocity_decay", "high", 0.75),
            ),
            "sequence",
        ))
    return defs


def build_quality_repair_hypotheses(direction="long", failure_codes=None, limit=16):
    """Forced hypotheses that encode quality repairs as factor_hints + AST."""
    out = []
    try:
        from . import ast_compiler as ac
        templates = {
            row["representation_type"]: row
            for row in ac.representation_asts_for_direction(direction)
        }
    except Exception:
        templates = {}
    for index, (event_id, terms, rtype) in enumerate(
        quality_repair_event_defs(failure_codes)[: max(1, int(limit))]
    ):
        hints = [name for name, _, _ in terms]
        picked = templates.get(rtype)
        # Prefer sequence/state/exclude ASTs for path repairs.
        if picked is None and templates:
            picked = (
                templates.get("sequence")
                or templates.get("state")
                or templates.get("relative_exclusion")
                or list(templates.values())[0]
            )
        row = {
            "hypothesis_id": "H_qopt_%s_%d" % (event_id, index + 1),
            "mechanism_id": "quality_repair_%s" % event_id,
            "family": "mean_reversion",
            "statement_zh": (
                "质量修复表征[%s]：%s（仅改入场确认/排除/时序，不改止损与杠杆）"
                % (rtype, event_id)
            ),
            "factor_hints": hints,
            "observable_proxy": hints,
            "predicted_direction": str(direction or "long").lower(),
            "source": "quality_optimization_repair",
            "path": "quality_repair",
            "priority_boost": 15.0,
            "representation_type": rtype,
            "quality_repair_event": event_id,
            "quality_repair_terms": [
                {"feature": a, "side": b, "q": c} for a, b, c in terms
            ],
            "may_research": True,
            "contract_relevant": True,
            "forbidden_modifications": list(FORBIDDEN_ALWAYS),
        }
        if picked:
            row["event_ast"] = picked.get("ast")
            row["event_ast_hash"] = picked.get("event_ast_hash")
            row["relative_skipped"] = picked.get("relative_skipped")
            row["representation_type"] = picked.get("representation_type") or rtype
        out.append(row)
    return out


def inject_missing_representations(hypotheses, direction="long", limit=8):
    """Append repair hypotheses only for missing representation types."""
    rows = [dict(h) for h in (hypotheses or []) if isinstance(h, dict)]
    audit = diversity_audit(rows)
    missing = list(audit.get("missing_representation_types") or [])
    if not missing and audit.get("ok"):
        return {
            "hypotheses": rows,
            "injected_n": 0,
            "diversity_audit": audit,
            "missing_before": [],
        }
    # Prefer quality-repair defs covering missing types; fall back to any repair.
    candidates = build_quality_repair_hypotheses(
        direction=direction,
        failure_codes=[LOW_PROFIT_FIRST_RATE, HIGH_MAE, LOW_WIN_RATE],
        limit=24,
    )
    injected = []
    have = set(
        h.get("representation_type") or representation_type_for_skeleton(
            h.get("materialization_skeleton") or {}
        )
        for h in rows
    )
    for hyp in candidates:
        if len(injected) >= int(limit):
            break
        rtype = hyp.get("representation_type")
        if rtype in have and rtype not in missing:
            continue
        if rtype in have and rtype in missing:
            # already covered after prior inject — skip duplicates
            continue
        if missing and rtype not in missing and rtype in have:
            continue
        if rtype in have:
            continue
        hyp = dict(hyp)
        hyp["source"] = "diversity_inject"
        hyp["hypothesis_id"] = "H_div_%s_%d" % (rtype, len(injected) + 1)
        hyp["diversity_inject"] = True
        rows.append(hyp)
        injected.append(rtype)
        have.add(rtype)
        if rtype in missing:
            missing = [m for m in missing if m != rtype]
    return {
        "hypotheses": rows,
        "injected_n": len(injected),
        "injected_types": injected,
        "diversity_audit_before": audit,
        "diversity_audit_after": diversity_audit(rows),
        "missing_before": list(audit.get("missing_representation_types") or []),
    }


def recent_2y_gate_from_returns(returns_with_ts, as_of=None, min_avg=0.1111):
    """Hard gate: recent 2y avg winning levered must pass independently.

    Also surfaces weighted metrics so old data cannot dominate the decision.
    """
    from . import review_metrics_reference as ref

    as_of = as_of or datetime.utcnow()
    trades = []
    for row in returns_with_ts or []:
        if isinstance(row, dict):
            trades.append(row)
        else:
            continue
    metrics = ref.compute_trade_ledger_metrics(trades, as_of=as_of)
    recent = metrics.get("recent_2y_metrics") or {}
    avg = recent.get("average_profitable_trade_return")
    weighted = metrics.get("weighted_average_profitable_trade_return")
    passed = bool(avg is not None and float(avg) >= float(min_avg))
    # Weighted view must not reverse a recent-2y fail into a pass.
    if not passed:
        decision = "reject_recent_2y_failure"
    else:
        decision = "pass"
    return {
        "ok": passed,
        "recent_2y_requirement_passed": passed,
        "average_profitable_trade_return": avg,
        "weighted_average_profitable_trade_return": weighted,
        "full_history_avg": metrics.get("average_profitable_trade_return"),
        "recent_2y_metrics": recent,
        "weighted_metrics": {
            "average_profitable_trade_return": weighted,
            "note_zh": "最近2年权重大于旧数据；加权均值仅展示，不得覆盖 recent_2y 硬门槛",
        },
        "decision": decision,
        "note_zh": "旧数据不得替最近2年通过",
    }


def parameter_plateau_score(param_surface, metric_key="score", relative_tol=0.15):
    """Score how flat a local parameter surface is (1=plateau, 0=spike).

    param_surface: list of dicts with numeric params + a metric.
    Neighbors within relative_tol of the best point should keep similar metrics.
    """
    rows = [r for r in (param_surface or []) if isinstance(r, dict)]
    if len(rows) < 2:
        return {
            "ok": False,
            "parameter_plateau_score": None,
            "parameter_spike": None,
            "n": len(rows),
            "reason": "insufficient_surface",
        }
    scored = []
    for row in rows:
        metric = _f(row.get(metric_key))
        if metric is None:
            metric = _f(row.get("mean_win_only_pct"))
        if metric is None:
            metric = _f(row.get("profit_first_rate"))
        if metric is None:
            continue
        params = row.get("params") if isinstance(row.get("params"), dict) else {
            k: row.get(k) for k in row.keys()
            if k not in (metric_key, "mean_win_only_pct", "profit_first_rate", "params", "label")
            and _f(row.get(k)) is not None
        }
        scored.append({"params": params or {}, "metric": float(metric)})
    if len(scored) < 2:
        return {
            "ok": False,
            "parameter_plateau_score": None,
            "parameter_spike": None,
            "n": len(scored),
            "reason": "insufficient_numeric_metrics",
        }
    best = max(scored, key=lambda r: r["metric"])
    best_m = best["metric"]
    if abs(best_m) < 1e-12:
        best_m = 1e-12
    neighbor_ok = 0
    neighbor_n = 0
    for row in scored:
        if row is best:
            continue
        # Treat every other point as a candidate neighbor in discrete grid.
        neighbor_n += 1
        rel = abs(row["metric"] - best["metric"]) / abs(best_m)
        if rel <= float(relative_tol):
            neighbor_ok += 1
    score = float(neighbor_ok) / float(neighbor_n) if neighbor_n else 0.0
    spike = bool(score < PLATEAU_MIN_SCORE)
    return {
        "ok": not spike,
        "parameter_plateau_score": round(score, 6),
        "parameter_spike": spike,
        "best_metric": best["metric"],
        "neighbor_ok": neighbor_ok,
        "neighbor_n": neighbor_n,
        "n": len(scored),
        "relative_tol": relative_tol,
    }


def expand_adjacent_param_plateau(base_params=None, axes=None, steps=None, limit=12):
    """Generate adjacent stable parameter points for LOW_FREQUENCY / PARAMETER_SPIKE.

    Does not change stop/leverage. Only nudges entry/confirmation thresholds.
    """
    base = dict(base_params or {})
    if not base:
        base = {
            "rsi_threshold": 35.0,
            "bb_reclaim_q": 0.70,
            "width_cap_q": 0.65,
            "confirm_bars": 2,
        }
    axes = list(axes or ("rsi_threshold", "bb_reclaim_q", "width_cap_q", "confirm_bars"))
    steps = dict(steps or {
        "rsi_threshold": (-3.0, -1.5, 1.5, 3.0),
        "bb_reclaim_q": (-0.05, -0.02, 0.02, 0.05),
        "width_cap_q": (-0.05, -0.02, 0.02, 0.05),
        "confirm_bars": (-1, 1),
    })
    out = []
    for axis in axes:
        if axis not in base:
            continue
        base_v = _f(base.get(axis))
        if base_v is None:
            continue
        for delta in steps.get(axis) or ():
            row = dict(base)
            new_v = base_v + float(delta)
            if axis == "confirm_bars":
                new_v = max(1, int(round(new_v)))
            elif "q" in axis:
                new_v = max(0.05, min(0.95, new_v))
            row[axis] = new_v
            row["plateau_axis"] = axis
            row["plateau_delta"] = delta
            out.append(row)
            if len(out) >= int(limit):
                return out
    return out


def build_frequency_plateau_hypotheses(direction="long", base_params=None, limit=8):
    """LOW_FREQUENCY repair: adjacent param plateau → probeable hypotheses."""
    neighbors = expand_adjacent_param_plateau(base_params=base_params, limit=limit)
    out = []
    for index, params in enumerate(neighbors):
        hints = ["rsi_14", "bb_mid_reclaim", "bb_width"]
        out.append({
            "hypothesis_id": "H_qopt_plateau_%d" % (index + 1),
            "mechanism_id": "quality_plateau_%s" % (params.get("plateau_axis") or "axis"),
            "family": "mean_reversion",
            "statement_zh": (
                "频率修复·邻域参数平台：%s=%s（不改止损/杠杆）"
                % (params.get("plateau_axis"), params.get(params.get("plateau_axis")))
            ),
            "factor_hints": hints,
            "observable_proxy": hints,
            "predicted_direction": str(direction or "long").lower(),
            "source": "quality_param_plateau",
            "path": "quality_repair",
            "priority_boost": 12.0,
            "representation_type": "threshold",
            "param_plateau": params,
            "forbidden_modifications": list(FORBIDDEN_ALWAYS),
            "may_research": True,
            "contract_relevant": True,
        })
    return out


def pnl_concentration_from_returns(returns):
    """Share of absolute PnL captured by the single largest trade."""
    vals = []
    for x in returns or []:
        v = _f(x)
        if v is not None:
            vals.append(v)
    if not vals:
        return {"top_trade_pnl_share": None, "pnl_concentrated": None, "n": 0}
    abs_vals = [abs(v) for v in vals]
    total = sum(abs_vals)
    if total <= 1e-12:
        return {"top_trade_pnl_share": 0.0, "pnl_concentrated": False, "n": len(vals)}
    share = max(abs_vals) / float(total)
    return {
        "top_trade_pnl_share": round(share, 6),
        "pnl_concentrated": bool(share > PNL_TOP_SHARE_MAX),
        "n": len(vals),
    }


def enrich_quality_metrics(metrics, returns=None, param_surface=None,
                           cost_stress=None, delay_stress=None, regime_slices=None):
    """Attach P4 diagnostic fields onto package metrics (non-destructive)."""
    out = dict(metrics or {})
    if returns is not None and out.get("top_trade_pnl_share") is None:
        conc = pnl_concentration_from_returns(returns)
        out.update(conc)
    if param_surface is not None and out.get("parameter_plateau_score") is None:
        plate = parameter_plateau_score(param_surface)
        out["parameter_plateau_score"] = plate.get("parameter_plateau_score")
        out["parameter_spike"] = plate.get("parameter_spike")
        out["parameter_plateau"] = plate
    if isinstance(cost_stress, dict):
        if "passed" in cost_stress:
            out["cost_stress_passed"] = bool(cost_stress.get("passed"))
        if cost_stress.get("mean_net") is not None:
            out["cost_stress_mean_net"] = _f(cost_stress.get("mean_net"))
    if isinstance(delay_stress, dict):
        if "passed" in delay_stress:
            out["delay_stress_passed"] = bool(delay_stress.get("passed"))
        if delay_stress.get("mean_net") is not None:
            out["delay_stress_mean_net"] = _f(delay_stress.get("mean_net"))
    if regime_slices is not None and out.get("regime_slice_pass_rate") is None:
        rows = list(regime_slices or [])
        if rows:
            passed_n = sum(1 for r in rows if (r is True) or (
                isinstance(r, dict) and r.get("passed")
            ))
            rate = float(passed_n) / float(len(rows))
            out["regime_slice_pass_rate"] = round(rate, 6)
            out["regime_stable"] = bool(rate >= REGIME_PASS_RATE_MIN)
    return out


def optimization_round_log(
    failure_code_before=None,
    allowed_modification=None,
    exact_change=None,
    metric_before=None,
    metric_after=None,
    rollback_reason=None,
    parameter_plateau_score_value=None,
    round_index=1,
):
    """Structured optimization log row (plan §14.4)."""
    before = metric_before if isinstance(metric_before, dict) else {}
    after = metric_after if isinstance(metric_after, dict) else {}
    return {
        "optimization_round": int(round_index),
        "failure_code_before": failure_code_before,
        "allowed_modification": list(allowed_modification or []),
        "exact_change": exact_change,
        "metric_before": before,
        "metric_after": after,
        "rollback_reason": rollback_reason,
        "parameter_plateau_score": parameter_plateau_score_value,
        "forbidden": list(FORBIDDEN_ALWAYS),
        "at": _now(),
    }


def directed_repair_plan(failure_codes=None, direction="long", base_params=None):
    """Map dominant codes → concrete repair actions (no free-form 'keep optimizing')."""
    codes = list(failure_codes or [])
    plan = {
        "failure_codes": codes,
        "allowed_modifications": sorted(set(
            mod for code in codes for mod in (ALLOWED_MODS.get(code) or ())
        )),
        "forbidden": list(FORBIDDEN_ALWAYS),
        "actions": [],
        "hypotheses": [],
    }
    if not codes:
        return plan
    # Path / WR / MAE → entry confirmation ASTs
    if set(codes) & {LOW_WIN_RATE, LOW_PROFIT_FIRST_RATE, HIGH_MAE}:
        hyps = build_quality_repair_hypotheses(
            direction=direction,
            failure_codes=[c for c in codes if c in (
                LOW_WIN_RATE, LOW_PROFIT_FIRST_RATE, HIGH_MAE, LOW_AVG_WINNING_RETURN,
            )],
            limit=12,
        )
        plan["actions"].append({
            "code": "path_entry_repair",
            "exact_change": "inject_confirmation_exclusion_sequence_asts",
            "n_hypotheses": len(hyps),
        })
        plan["hypotheses"].extend(hyps)
    # Frequency / spike → adjacent plateau
    if set(codes) & {LOW_FREQUENCY, PARAMETER_SPIKE}:
        plate_hyps = build_frequency_plateau_hypotheses(
            direction=direction, base_params=base_params, limit=8,
        )
        plan["actions"].append({
            "code": "param_plateau_expand",
            "exact_change": "adjacent_param_neighbors",
            "n_hypotheses": len(plate_hyps),
        })
        plan["hypotheses"].extend(plate_hyps)
    # Recent 2y fail → cannot pass; only relocate research
    if RECENT_2Y_FAILURE in codes:
        plan["actions"].append({
            "code": RECENT_2Y_FAILURE,
            "exact_change": "reject_candidate_relocate_research",
            "may_pass_handoff": False,
        })
    # Cost / delay → tighten entry timing only (never lower floors)
    if COST_FAILURE in codes or DELAY_FAILURE in codes:
        plan["actions"].append({
            "code": "execution_stress_repair",
            "exact_change": "raise_entry_confirmation_reduce_turnover",
            "may_lower_handoff": False,
        })
    return plan


def probe():
    diag = classify_package_failures({
        "win_rate": 0.33,
        "mean_win_only_pct": 12.0,
        "profit_first_rate": 0.30,
        "median_mae_pct": -0.004,
        "weekly_opens": 8.0,
        "parameter_plateau_score": 0.2,
        "cost_stress_passed": False,
        "delay_stress_passed": False,
        "top_trade_pnl_share": 0.55,
        "regime_slice_pass_rate": 0.3,
    })
    plate = parameter_plateau_score([
        {"params": {"rsi_threshold": 35}, "score": 1.0},
        {"params": {"rsi_threshold": 32}, "score": 0.2},
        {"params": {"rsi_threshold": 38}, "score": 0.25},
    ])
    return {
        "ok": True,
        "sample_primary": diag.get("primary"),
        "sample_codes": diag.get("codes"),
        "all_failure_codes_n": len(ALL_FAILURE_CODES),
        "repair_defs_n": len(quality_repair_event_defs(diag.get("codes"))),
        "representation_types": list(REPRESENTATION_TYPES),
        "parameter_plateau_score": plate.get("parameter_plateau_score"),
        "directed_plan_actions_n": len(
            directed_repair_plan(diag.get("codes")).get("actions") or []
        ),
    }
