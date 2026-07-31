# -*- coding: utf-8 -*-
"""Non-LLM symbolic expression searcher (genetic-programming lite).

Forbidden: writing natural-language strategy stories.
Allowed: evolve simple arithmetic factor expressions on the feature matrix.
"""
from __future__ import print_function

import math
import random
from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


OPS = ("add", "sub", "mul", "abs_sub", "rank_proxy")


def _align(a, b):
    n = min(len(a or []), len(b or []))
    return list(a)[-n:], list(b)[-n:]


def _eval_expr(expr, matrix):
    """expr: {op, left, right} where left/right are factor names or nested."""
    op = expr.get("op")
    left = expr.get("left")
    right = expr.get("right")
    if isinstance(left, dict):
        ls = _eval_expr(left, matrix)
    else:
        ls = list((matrix or {}).get(left) or [])
    if isinstance(right, dict):
        rs = _eval_expr(right, matrix)
    else:
        rs = list((matrix or {}).get(right) or [])
    ls, rs = _align(ls, rs)
    out = []
    for i in range(len(ls)):
        a = ls[i]
        b = rs[i]
        if a is None or b is None:
            out.append(None)
            continue
        try:
            fa, fb = float(a), float(b)
        except Exception:
            out.append(None)
            continue
        if op == "add":
            out.append(fa + fb)
        elif op == "sub":
            out.append(fa - fb)
        elif op == "mul":
            out.append(fa * fb)
        elif op == "abs_sub":
            out.append(abs(fa - fb))
        elif op == "rank_proxy":
            out.append(fa - fb)
        else:
            out.append(fa - fb)
    return out


def _corr(xs, ys):
    pairs = [
        (float(a), float(b))
        for a, b in zip(xs, ys)
        if a is not None and b is not None
    ]
    n = len(pairs)
    if n < 30:
        return None
    mx = sum(a for a, _ in pairs) / float(n)
    my = sum(b for _, b in pairs) / float(n)
    num = sum((a - mx) * (b - my) for a, b in pairs)
    dx = math.sqrt(sum((a - mx) ** 2 for a, _ in pairs))
    dy = math.sqrt(sum((b - my) ** 2 for _, b in pairs))
    if dx <= 1e-12 or dy <= 1e-12:
        return 0.0
    return num / (dx * dy)


def _random_expr(factors, rng, depth=0):
    if depth >= 2 or (depth > 0 and rng.random() < 0.55):
        return rng.choice(factors)
    return {
        "op": rng.choice(OPS),
        "left": _random_expr(factors, rng, depth + 1),
        "right": _random_expr(factors, rng, depth + 1),
    }


def _expr_key(expr):
    if not isinstance(expr, dict):
        return str(expr)
    return "%s(%s,%s)" % (expr.get("op"), _expr_key(expr.get("left")), _expr_key(expr.get("right")))


def search(factor_matrix, fwd_returns, n_pop=60, n_gen=6, seed=13, top_k=16):
    """Evolve expressions maximizing |IC| with fwd returns. No LLM / no PySR."""
    factors = [k for k, v in (factor_matrix or {}).items() if v and len(v) >= 50]
    if len(factors) < 2:
        return {
            "ok": False,
            "error": "insufficient_factors",
            "expressions": [],
            "role": "symbolic_searcher",
            "forbids_zh": "禁止写自然语言故事",
            "at": _now(),
        }
    rng = random.Random(int(seed))
    fwd = list(fwd_returns or [])
    pop = [_random_expr(factors, rng) for _ in range(int(n_pop))]
    scored = []
    seen = set()
    for gen in range(int(n_gen)):
        gen_rows = []
        for expr in pop:
            key = _expr_key(expr)
            if key in seen:
                continue
            seen.add(key)
            series = _eval_expr(expr, factor_matrix)
            ic = _corr(series, fwd[-len(series):] if series else [])
            if ic is None:
                continue
            row = {
                "expression": expr,
                "expression_key": key,
                "ic": ic,
                "abs_ic": abs(ic),
                "series_tail_n": len(series),
            }
            gen_rows.append(row)
            scored.append(row)
        gen_rows.sort(key=lambda r: r["abs_ic"], reverse=True)
        elites = [r["expression"] for r in gen_rows[: max(4, int(n_pop) // 5)]]
        # mutate / crossover
        pop = list(elites)
        while len(pop) < int(n_pop):
            if elites and rng.random() < 0.5:
                base = rng.choice(elites)
                child = {
                    "op": rng.choice(OPS),
                    "left": base if rng.random() < 0.5 else rng.choice(factors),
                    "right": rng.choice(factors),
                }
                pop.append(child)
            else:
                pop.append(_random_expr(factors, rng))
    scored.sort(key=lambda r: r["abs_ic"], reverse=True)
    # de-dupe keys
    uniq = []
    keys = set()
    for r in scored:
        if r["expression_key"] in keys:
            continue
        keys.add(r["expression_key"])
        uniq.append(r)
        if len(uniq) >= int(top_k):
            break
    return {
        "ok": True,
        "role": "symbolic_searcher",
        "backend": "gp_lite_v1",
        "n_evaluated": len(seen),
        "expressions": uniq,
        "forbids_zh": "禁止写自然语言故事；只提交表达式与IC",
        "at": _now(),
    }


def expressions_to_hypotheses(search_pack, factor_matrix=None):
    hyps = []
    matrix = factor_matrix or {}
    for i, row in enumerate((search_pack or {}).get("expressions") or []):
        key = row.get("expression_key") or ("sym_%d" % i)
        # materialize series into synthetic factor name for naked probes
        series = None
        try:
            series = _eval_expr(row.get("expression") or {}, matrix)
        except Exception:
            series = None
        syn_name = "sym__%d" % i
        leaves = []

        def _leaves(node, acc):
            if isinstance(node, dict):
                _leaves(node.get("left"), acc)
                _leaves(node.get("right"), acc)
            elif node:
                acc.append(str(node))

        _leaves(row.get("expression"), leaves)
        hyps.append({
            "hypothesis_id": "H_sym_%d" % i,
            "source": "symbolic_searcher",
            "path": "algorithmic_search",
            "mechanism_id": None,
            "family": "symbolic",
            "payoff_payer": "unknown_pending_mechanism_link",
            "constraint_used": ["expression_ic"],
            "observable_proxy": [key] + leaves[:4],
            "predicted_direction": (
                "positive_ic" if float(row.get("ic") or 0) > 0 else "negative_ic"
            ),
            "horizon": "label_horizon",
            "conditional_on": ["symbolic_expression"],
            "failure_conditions": ["ic_collapses_oos", "expression_is_classic_factor_alias"],
            "capacity_limit": "unknown",
            "alternative_explanations": ["multiple_testing", "classic_factor_in_disguise"],
            "factor_hints": [syn_name] + leaves[:2],
            "synthetic_factor": {"name": syn_name, "series": series},
            "symbolic_expression": row.get("expression"),
            "symbolic_ic": row.get("ic"),
            "simplest_antifalsify": "block_permute_labels",
            "statement_zh": "符号搜索表达式 %s |IC|=%.4f" % (
                key, float(row.get("abs_ic") or 0),
            ),
        })
    return hyps


def probe():
    return {
        "ok": True,
        "provider": "symbolic_searcher_gp_lite_v2",
        "llm": False,
        "pysr": False,
        "note_zh": "GP-lite；PySR/RL 因内存与依赖未装。",
        "at": _now(),
    }
