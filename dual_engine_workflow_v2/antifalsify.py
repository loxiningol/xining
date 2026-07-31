# -*- coding: utf-8 -*-
"""Automatic antifalsification — negative controls, placebos, competing explanations.

Outputs an evidence matrix; NEVER claims causal proof.
"""
from __future__ import print_function

import math
import random
from datetime import datetime

from . import creation_alphalens_lite as al
from . import probe_protocol as pp


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _shift(series, k):
    n = len(series or [])
    if n == 0:
        return []
    k = int(k) % n
    return list(series[-k:] + series[:-k])


def _permute_block(series, block=8, rng=None):
    rng = rng or random.Random(0)
    xs = list(series or [])
    if len(xs) < block * 2:
        ys = list(xs)
        rng.shuffle(ys)
        return ys
    blocks = [xs[i:i + block] for i in range(0, len(xs), block)]
    rng.shuffle(blocks)
    out = []
    for b in blocks:
        out.extend(b)
    return out[: len(xs)]


def _sign_flip(series):
    return [(-float(v) if v is not None else None) for v in (series or [])]


def _effect(factor_values, fwd):
    return al.causal_pre_post(factor_values, fwd)


def _probe_net(factor_values, fwd, side="high"):
    return pp.evaluate_naked_probe(factor_values, fwd, side=side)


def run_antifalsify_battery(factor_values, fwd_returns, side="high", seed=7,
                            competing_factors=None, factor_matrix=None):
    """Build evidence matrix for one signal series."""
    rng = random.Random(int(seed))
    base_probe = _probe_net(factor_values, fwd_returns, side=side)
    base_eff = _effect(factor_values, fwd_returns)
    base_net = float((base_probe or {}).get("mean_net") or 0.0)
    base_t = float((base_eff or {}).get("t_stat") or (base_probe or {}).get("t_stat") or 0.0)

    rows = []

    # Negative controls
    for name, series in (
        ("time_shift_plus_5", _shift(factor_values, 5)),
        ("time_shift_minus_5", _shift(factor_values, -5)),
        ("sign_flip", _sign_flip(factor_values)),
        ("block_permute", _permute_block(factor_values, block=10, rng=rng)),
    ):
        p = _probe_net(series, fwd_returns, side=side)
        e = _effect(series, fwd_returns)
        net = float((p or {}).get("mean_net") or 0.0)
        # original should beat negative control
        support = base_net > net and abs(base_t) >= abs(float((e or {}).get("t_stat") or 0))
        rows.append({
            "test": name,
            "family": "negative_control",
            "support": "support" if support else ("oppose" if net >= base_net and abs(float((e or {}).get("t_stat") or 0)) >= abs(base_t) else "neutral"),
            "mean_net": net,
            "t_stat": (e or {}).get("t_stat"),
        })

    # Placebo: shuffle fwd labels in blocks (keep autocorr-ish)
    fwd_placebo = _permute_block(fwd_returns, block=12, rng=rng)
    p = _probe_net(factor_values, fwd_placebo, side=side)
    e = _effect(factor_values, fwd_placebo)
    rows.append({
        "test": "fwd_block_placebo",
        "family": "placebo",
        "support": (
            "support" if float((p or {}).get("mean_net") or 0) < base_net * 0.5 else "oppose"
        ),
        "mean_net": (p or {}).get("mean_net"),
        "t_stat": (e or {}).get("t_stat"),
    })

    # Competing explanations: if competing factor explains more, mark oppose
    competing_factors = list(competing_factors or [])
    if not competing_factors and factor_matrix:
        for cname in ("atr_pct_14", "range_pct", "abs_ret_1", "ret_12", "volume_z"):
            if cname in factor_matrix:
                competing_factors.append(cname)
    for cname in competing_factors[:4]:
        cseries = (factor_matrix or {}).get(cname) or []
        if not cseries:
            continue
        cp = _probe_net(cseries, fwd_returns, side=side)
        cnet = float((cp or {}).get("mean_net") or 0.0)
        # residual proxy: original net should exceed competitor
        if cnet >= base_net and cnet > 0:
            verdict = "oppose"
        elif base_net > cnet:
            verdict = "support"
        else:
            verdict = "neutral"
        rows.append({
            "test": "compete_%s" % cname,
            "family": "competing_explanation",
            "support": verdict,
            "mean_net": cnet,
            "t_stat": (cp or {}).get("t_stat"),
            "competitor": cname,
        })

    # Evidence matrix summary
    def _bucket(family):
        sub = [r for r in rows if r.get("family") == family]
        return {
            "support": sum(1 for r in sub if r.get("support") == "support"),
            "neutral": sum(1 for r in sub if r.get("support") == "neutral"),
            "oppose": sum(1 for r in sub if r.get("support") == "oppose"),
            "tests": sub,
        }

    matrix = {
        "mechanism_consistency": {
            "support": 1 if base_probe.get("passed") else 0,
            "neutral": 0 if base_probe.get("passed") else 1,
            "oppose": 0 if base_probe.get("passed") else 0,
            "note_zh": "裸探针方向/净优势是否存在（非因果证明）",
        },
        "negative_control": _bucket("negative_control"),
        "placebo": _bucket("placebo"),
        "competing_explanation": _bucket("competing_explanation"),
        "cost_after_edge": {
            "support": 1 if base_net > 0 else 0,
            "neutral": 0,
            "oppose": 0 if base_net > 0 else 1,
            "mean_net": base_net,
        },
    }
    oppose_n = sum(
        int((matrix[k].get("oppose") or 0))
        for k in ("negative_control", "placebo", "competing_explanation", "cost_after_edge")
    )
    support_n = sum(
        int((matrix[k].get("support") or 0))
        for k in ("mechanism_consistency", "negative_control", "placebo",
                  "competing_explanation", "cost_after_edge")
    )
    # Pass if more support than oppose AND cost edge positive — still NOT causal proof
    passed = bool(base_net > 0 and support_n > oppose_n and oppose_n <= 2)
    return {
        "ok": True,
        "schema": "qiyu_antifalsify_evidence_matrix_v1",
        "passed": passed,
        "causal_claim": False,
        "credibility": "elevated_if_pass_not_proven",
        "base_probe": {k: v for k, v in (base_probe or {}).items() if k != "trade_returns"},
        "base_effect": base_eff,
        "evidence_matrix": matrix,
        "rows": rows,
        "support_n": support_n,
        "oppose_n": oppose_n,
        "note_zh": (
            "多种替代解释未能推翻 → 提高可信度；仍不宣称完成因果证明。"
            if passed else
            "反证/安慰剂/竞争解释未通过；不得进入策略组装。"
        ),
        "at": _now(),
    }


def probe():
    return {
        "ok": True,
        "provider": "antifalsify_battery_v1",
        "causal_claim": False,
        "at": _now(),
    }
