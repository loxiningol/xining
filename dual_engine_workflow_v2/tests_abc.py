# -*- coding: utf-8 -*-
"""Replace logic_destruction ±20% with A necessity / B param stability / C contribution."""
from __future__ import print_function

import copy
import math
import random


def _metrics_bundle(m):
    m = m or {}
    return {
        "net_profit": float(m.get("oos_profit") if m.get("oos_profit") is not None
                            else m.get("mean_net") or 0) * float(m.get("trades") or 0),
        "mean_net": float(m.get("mean_net") or 0),
        "profit_factor": float(m.get("profit_factor") or m.get("pf") or 0),
        "sharpe": float(m.get("sharpe") or 0),
        "max_dd": float(m.get("max_dd") or m.get("max_drawdown") or 0),
        "trades": int(m.get("trades") or 0),
        "oos_profit": float(m.get("oos_profit") or 0),
        "fold_positive": int(m.get("fold_positive") or 0),
        "folds": int(m.get("folds") or 0),
        "win_rate_pct": float(m.get("win_rate_pct") or 0),
    }


def _score(m):
    """Multi-metric score — never single-metric."""
    b = _metrics_bundle(m)
    # higher better; max_dd is negative-ish magnitude
    dd_pen = abs(b["max_dd"])
    return (
        b["mean_net"] * 40.0
        + b["sharpe"] * 12.0
        + b["oos_profit"] * 80.0
        + min(b["profit_factor"], 5.0) * 4.0
        + min(b["trades"], 80) * 0.05
        + b["fold_positive"] * 1.5
        - dd_pen * 15.0
    )


def _walk_numeric_leaves(definition, mutate_fn):
    dsl = copy.deepcopy(definition)

    def walk(node):
        if isinstance(node, dict):
            right = node.get("right")
            if isinstance(right, dict) and "value" in right:
                try:
                    v = float(right["value"])
                    right["value"] = mutate_fn(v, node)
                except Exception:
                    pass
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(dsl.get("entry"))
    walk(dsl.get("exit"))
    return dsl


def _remove_core_leaves(definition, core_features):
    """Delete test: remove leaves whose feature is in core_features."""
    import copy as _c
    dsl = _c.deepcopy(definition)
    cores = set(str(x).lower() for x in (core_features or []))

    def filt(node):
        if isinstance(node, dict):
            if "left" in node and "op" in node:
                left = node.get("left") or {}
                feat = str(left.get("feature") or "").lower()
                if feat in cores or any(c in feat for c in cores):
                    return None
                return node
            out = {}
            for k, v in node.items():
                nv = filt(v)
                if nv is not None:
                    out[k] = nv
            return out
        if isinstance(node, list):
            return [x for x in (filt(i) for i in node) if x is not None]
        return node

    dsl["entry"] = filt(dsl.get("entry"))
    return dsl


def _randomize_thresholds(definition, seed=21):
    rng = random.Random(seed)
    return _walk_numeric_leaves(
        definition, lambda v, node: round(v * rng.uniform(0.5, 1.5) + rng.uniform(-1, 1), 6)
    )


def _time_shift_proxy(definition, seed=33):
    """Proxy for time misalignment: shift windows/thresholds systematically."""
    rng = random.Random(seed)
    shift = rng.choice([-1, 1]) * rng.uniform(0.15, 0.35)
    return _walk_numeric_leaves(definition, lambda v, node: round(v * (1.0 + shift), 6))


def _irrelevant_substitute(definition, seed=55):
    """Replace numeric thresholds with distribution-similar noise values."""
    rng = random.Random(seed)
    vals = []

    def collect(node):
        if isinstance(node, dict):
            right = node.get("right")
            if isinstance(right, dict) and "value" in right:
                try:
                    vals.append(float(right["value"]))
                except Exception:
                    pass
            for v in node.values():
                collect(v)
        elif isinstance(node, list):
            for x in node:
                collect(x)

    collect(definition)
    if not vals:
        return copy.deepcopy(definition)
    mean = sum(vals) / len(vals)
    std = (sum((x - mean) ** 2 for x in vals) / len(vals)) ** 0.5 or abs(mean) * 0.1 or 1.0

    def mut(v, node):
        return round(rng.gauss(mean, std), 6)

    return _walk_numeric_leaves(definition, mut)


def run_necessity_tests(definition, base_metrics, backtest_fn, core_features=None,
                        allow_direction_flip=False):
    """A. Mechanism necessity — base must beat ablation variants on multi-metrics."""
    base_s = _score(base_metrics)
    base_b = _metrics_bundle(base_metrics)
    variants = []
    cores = core_features or _infer_core_features(definition)

    plans = [
        ("delete", lambda: _remove_core_leaves(definition, cores)),
        ("randomize", lambda: _randomize_thresholds(definition, 21)),
        ("time_shift", lambda: _time_shift_proxy(definition, 33)),
        ("irrelevant_substitute", lambda: _irrelevant_substitute(definition, 55)),
    ]
    if allow_direction_flip:
        def _flip():
            d = copy.deepcopy(definition)
            d["direction"] = "short" if d.get("direction") == "long" else "long"
            return d
        plans.append(("direction_flip", _flip))

    for name, builder in plans:
        try:
            alt = builder()
            alt = backtest_fn(alt)  # returns metrics dict or {"metrics":...}
            metrics = alt.get("metrics") if isinstance(alt, dict) and "metrics" in alt else alt
            s = _score(metrics)
            variants.append({
                "name": name,
                "ok": True,
                "score": round(s, 4),
                "metrics": _metrics_bundle(metrics),
                "base_advantage": round(base_s - s, 4),
            })
        except Exception as exc:
            variants.append({"name": name, "ok": False, "error": str(exc),
                             "score": None, "base_advantage": None})

    # Pass criteria: base significantly better than delete/random/shift/substitute
    advantages = [v.get("base_advantage") for v in variants
                  if v.get("ok") and v.get("base_advantage") is not None]
    # Pseudo-explanation if delete ~ base
    delete_row = next((v for v in variants if v.get("name") == "delete"), None)
    random_row = next((v for v in variants if v.get("name") == "randomize"), None)
    shift_row = next((v for v in variants if v.get("name") == "time_shift"), None)

    reasons = []
    passed = True
    if delete_row and delete_row.get("ok"):
        if abs(delete_row.get("base_advantage") or 0) < 0.5 and base_b["trades"] >= 5:
            passed = False
            reasons.append("pseudo_mechanism:delete_nearly_equal")
        elif (delete_row.get("base_advantage") or 0) < 0:
            passed = False
            reasons.append("delete_outperforms_base")
    if random_row and random_row.get("ok"):
        if abs(random_row.get("base_advantage") or 0) < 0.35:
            passed = False
            reasons.append("no_information_value:random_similar")
    if shift_row and shift_row.get("ok"):
        if (shift_row.get("base_advantage") or 0) < -0.5:
            passed = False
            reasons.append("time_shift_better_possible_leakage")
    if advantages and sum(1 for a in advantages if a > 0.3) < max(2, len(advantages) // 2):
        passed = False
        reasons.append("base_not_dominating_majority_variants")

    return {
        "pass": bool(passed),
        "base_score": round(base_s, 4),
        "base_metrics": base_b,
        "core_features": cores,
        "variants": variants,
        "reasons": reasons,
        "test": "mechanism_necessity",
    }


def _infer_core_features(definition):
    feats = []
    from .mechanism import extract_dsl_conditions
    for row in extract_dsl_conditions(definition):
        if row.get("phase") == "entry":
            feats.append(str(row.get("feature") or ""))
    return feats[:3] or ["entry"]


def _collect_param_paths(definition):
    paths = []

    def walk(node, path):
        if isinstance(node, dict):
            right = node.get("right")
            if isinstance(right, dict) and "value" in right:
                try:
                    float(right["value"])
                    paths.append(path + ["right", "value"])
                except Exception:
                    pass
            for k, v in node.items():
                walk(v, path + [k])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + [i])

    walk(definition.get("entry"), ["entry"])
    walk(definition.get("exit"), ["exit"])
    return paths


def _set_path(obj, path, value):
    cur = obj
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = value


def _get_path(obj, path):
    cur = obj
    for p in path:
        cur = cur[p]
    return cur


def run_parameter_stability(definition, backtest_fn, step=0.10, span=0.50):
    """B. Staged param stability — 1D scan then 2D on top sensitive; plateau required."""
    paths = _collect_param_paths(definition)
    if not paths:
        return {"pass": False, "reason": "no_numeric_params", "test": "parameter_stability"}

    base = backtest_fn(definition)
    base_m = base.get("metrics") if isinstance(base, dict) and "metrics" in base else base
    base_s = _score(base_m)

    # Stage 1: 1D scans
    sensitivities = []
    one_d = []
    for path in paths[:8]:
        try:
            base_v = float(_get_path(definition, path))
        except Exception:
            continue
        series = []
        factors = []
        f = -span
        while f <= span + 1e-9:
            factors.append(round(f, 4))
            f += step
        for fac in factors:
            d = copy.deepcopy(definition)
            new_v = base_v * (1.0 + fac)
            if base_v == 0:
                new_v = fac
            # legality: avoid nonsense signs flip for bounded ratios if abs huge
            _set_path(d, path, round(new_v, 6))
            try:
                r = backtest_fn(d)
                m = r.get("metrics") if isinstance(r, dict) and "metrics" in r else r
                s = _score(m)
                series.append({"factor": fac, "value": round(new_v, 6), "score": round(s, 4),
                               "mean_net": _metrics_bundle(m)["mean_net"],
                               "trades": _metrics_bundle(m)["trades"]})
            except Exception as exc:
                series.append({"factor": fac, "error": str(exc), "score": None})
        valid = [x for x in series if x.get("score") is not None]
        if not valid:
            continue
        scores = [x["score"] for x in valid]
        sens = (max(scores) - min(scores)) if scores else 0
        # plateau: adjacent positive expectation
        pos_adj = 0
        for i in range(len(valid) - 1):
            if valid[i].get("mean_net", -1) > 0 and valid[i + 1].get("mean_net", -1) > 0:
                pos_adj += 1
        peak_only = False
        if valid:
            best = max(valid, key=lambda x: x["score"])
            neighbors = [x for x in valid if abs(x["factor"] - best["factor"]) <= step + 1e-9
                         and x is not best]
            if neighbors and all((best["score"] - n["score"]) > 1.5 for n in neighbors):
                peak_only = True
        one_d.append({
            "path": "/".join(str(p) for p in path),
            "base_value": base_v,
            "series": series,
            "sensitivity": round(sens, 4),
            "positive_adjacent_pairs": pos_adj,
            "isolated_peak": peak_only,
        })
        sensitivities.append((sens, path, peak_only, pos_adj))

    sensitivities.sort(key=lambda x: -x[0])
    # Stage 2: 2D on top 2–3
    two_d = []
    top = [s[1] for s in sensitivities[:3]]
    if len(top) >= 2:
        p1, p2 = top[0], top[1]
        grid = []
        try:
            v1 = float(_get_path(definition, p1))
            v2 = float(_get_path(definition, p2))
        except Exception:
            v1 = v2 = None
        if v1 is not None:
            for f1 in (-0.2, 0.0, 0.2):
                for f2 in (-0.2, 0.0, 0.2):
                    d = copy.deepcopy(definition)
                    _set_path(d, p1, round(v1 * (1 + f1), 6))
                    _set_path(d, p2, round(v2 * (1 + f2), 6))
                    try:
                        r = backtest_fn(d)
                        m = r.get("metrics") if isinstance(r, dict) and "metrics" in r else r
                        grid.append({
                            "f1": f1, "f2": f2,
                            "score": round(_score(m), 4),
                            "mean_net": _metrics_bundle(m)["mean_net"],
                            "trades": _metrics_bundle(m)["trades"],
                        })
                    except Exception as exc:
                        grid.append({"f1": f1, "f2": f2, "error": str(exc)})
            pos_cells = sum(1 for g in grid if g.get("mean_net", -1) > 0)
            two_d.append({
                "paths": ["/".join(map(str, p1)), "/".join(map(str, p2))],
                "grid": grid,
                "positive_cells": pos_cells,
                "stable_region": pos_cells >= 4,
            })

    isolated = any(x.get("isolated_peak") for x in one_d)
    weak_plateau = all((x.get("positive_adjacent_pairs") or 0) < 2 for x in one_d) if one_d else True
    stable_2d = any(t.get("stable_region") for t in two_d) if two_d else (not isolated and not weak_plateau)

    high_overfit = bool(isolated or (weak_plateau and not stable_2d))
    passed = (not high_overfit) and (not weak_plateau or stable_2d)

    return {
        "pass": bool(passed),
        "high_overfit_risk": high_overfit,
        "isolated_peak": isolated,
        "base_score": round(base_s, 4),
        "stage1_one_d": one_d,
        "stage2_two_d": two_d,
        "stage3_plateau": {
            "stable_2d": stable_2d,
            "weak_plateau": weak_plateau,
        },
        "conditional_keep_requires_glm": high_overfit,
        "test": "parameter_stability",
    }


def run_contribution_tests(definition, backtest_fn, core_features=None):
    """C. Ablate non-core conditions; classify necessary/enhance/redundant/negative."""
    from .mechanism import extract_dsl_conditions
    cores = set(str(x).lower() for x in (core_features or _infer_core_features(definition)))
    base = backtest_fn(definition)
    base_m = base.get("metrics") if isinstance(base, dict) and "metrics" in base else base
    base_s = _score(base_m)
    base_tr = _metrics_bundle(base_m)["trades"]
    base_wr = _metrics_bundle(base_m)["win_rate_pct"]

    rows = []
    for cond in extract_dsl_conditions(definition):
        feat = str(cond.get("feature") or "")
        if cond.get("phase") != "entry":
            continue
        if feat.lower() in cores or any(c in feat.lower() for c in cores):
            rows.append({
                "condition_id": cond["condition_id"],
                "feature": feat,
                "classification": "necessary_core",
                "delta_score": None,
                "action": "retain",
            })
            continue
        # remove this leaf
        ablated = _remove_core_leaves(definition, [feat])
        try:
            r = backtest_fn(ablated)
            m = r.get("metrics") if isinstance(r, dict) and "metrics" in r else r
            s = _score(m)
            tr = _metrics_bundle(m)["trades"]
            wr = _metrics_bundle(m)["win_rate_pct"]
            delta = base_s - s
            if delta > 0.8:
                klass = "effective_enhancer"
                action = "retain"
            elif delta < -0.5:
                klass = "negative_contributor"
                action = "remove"
            elif abs(delta) <= 0.8 and tr > base_tr * 1.2 and wr <= base_wr + 1:
                klass = "frequency_only_no_quality"
                action = "remove"
            else:
                klass = "redundant"
                action = "remove"
            rows.append({
                "condition_id": cond["condition_id"],
                "feature": feat,
                "classification": klass,
                "delta_score": round(delta, 4),
                "ablated_metrics": _metrics_bundle(m),
                "action": action,
            })
        except Exception as exc:
            rows.append({
                "condition_id": cond["condition_id"],
                "feature": feat,
                "classification": "error",
                "error": str(exc),
                "action": "revise",
            })

    must_remove = [r for r in rows if r.get("action") == "remove"]
    return {
        "pass": True,  # informational gate: pipeline must apply removals
        "rows": rows,
        "must_remove": must_remove,
        "filter_stacking_risk": sum(
            1 for r in rows if r.get("classification") == "frequency_only_no_quality"
        ) >= 2,
        "test": "condition_contribution",
    }


def run_abc_battery(definition, base_metrics, backtest_fn, core_features=None,
                    allow_direction_flip=False):
    nec = run_necessity_tests(
        definition, base_metrics, backtest_fn,
        core_features=core_features,
        allow_direction_flip=allow_direction_flip,
    )
    stab = run_parameter_stability(definition, backtest_fn)
    contrib = run_contribution_tests(definition, backtest_fn, core_features=core_features)
    overall = bool(nec.get("pass")) and bool(stab.get("pass")) and not contrib.get("filter_stacking_risk")
    return {
        "pass": overall,
        "necessity": nec,
        "parameter_stability": stab,
        "contribution": contrib,
        "replaced_logic_destruction": True,
    }
