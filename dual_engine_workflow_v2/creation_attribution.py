# -*- coding: utf-8 -*-
"""Cross-symbol common-factor attribution for creation cases.

A cluster is a structure fingerprint + fail-rule bucket. Research direction,
symbol, and long/short are never cluster keys and must not become bans.
"""
from __future__ import print_function

from collections import Counter, defaultdict

from .creation_case_store import OOS_RULES, feature_fingerprint
from .quality_gate import IS_NUMERIC_DIMENSIONS, STRUCTURE_CHECKS


MIN_SYMBOLS = 3
MIN_JOBS = 5
MIN_SUCCESS_JOBS = 2

EXIT_GEOM = frozenset({
    "holding_bars_below_threshold",
    "tp_trigger_rate_below_threshold",
    "payoff_ratio_below_threshold",
})
FREQ = frozenset({
    "weekly_opens_below_threshold",
    "trade_count_below_threshold",
})
EXPECT = frozenset({
    "E_raw_below_threshold",
    "t_statistic_below_threshold",
    "in_sample_sharpe_below_threshold",
    "win_rate_below_threshold",
})
STRUCTURE = frozenset({
    "structure_cannot_read_trend",
    "no_trend_location_skill",
    "win_rate_payoff_combo_failed",
})
DRAWDOWN = frozenset({"max_drawdown_above_threshold"})

BUCKET_LABELS = {
    "exit_geometry": "退出几何（持仓/止盈触发/盈亏比）",
    "frequency": "开仓频率或样本笔数",
    "expectancy": "期望/夏普/胜率/t",
    "structure": "入场结构识字",
    "drawdown": "回撤",
    "mixed": "多项数字缺口并存",
}

RULE_LABELS = {}
for rule_id, _metric, label_zh in IS_NUMERIC_DIMENSIONS:
    RULE_LABELS[rule_id] = label_zh
for rule_id, _metric, label_zh in STRUCTURE_CHECKS:
    RULE_LABELS[rule_id] = label_zh


def _is_dict(row):
    return row if isinstance(row, dict) else {}


def is_rules(failed_rules):
    return [
        str(item) for item in (failed_rules or [])
        if str(item) and str(item) not in OOS_RULES
    ]


def dominant_bucket(failed_rules):
    rules = set(is_rules(failed_rules))
    if not rules:
        return "mixed"
    scores = (
        ("structure", len(rules & STRUCTURE)),
        ("exit_geometry", len(rules & EXIT_GEOM)),
        ("frequency", len(rules & FREQ)),
        ("expectancy", len(rules & EXPECT)),
        ("drawdown", len(rules & DRAWDOWN)),
    )
    scores = sorted(scores, key=lambda item: (-item[1], item[0]))
    if scores[0][1] <= 0:
        return "mixed"
    if scores[1][1] == scores[0][1] and scores[0][1] > 0:
        return "mixed"
    return scores[0][0]


def cluster_id_for(case):
    case = _is_dict(case)
    family = str(case.get("structure_family") or "unknown")
    fp = str(case.get("feature_fingerprint") or feature_fingerprint(case.get("features")))
    bucket = dominant_bucket(case.get("failed_rules"))
    return "fam:%s|fp:%s|bucket:%s" % (family, fp, bucket)


def route_failure(case):
    """entry_structure / exit_structure / parameter / mechanism. Advisory only."""
    case = _is_dict(case)
    traj = _is_dict(case.get("retry_trajectory"))
    last = set(is_rules(traj.get("last_rules") or case.get("failed_rules")))
    if traj.get("same_rules_across_retries") and last:
        return "mechanism"
    if last & STRUCTURE and not (last - STRUCTURE - EXIT_GEOM):
        if last & STRUCTURE:
            return "entry_structure"
    if last and last <= EXIT_GEOM:
        return "exit_structure"
    if last & EXIT_GEOM and not (last & STRUCTURE):
        return "exit_structure"
    if traj.get("rules_rotating"):
        return "parameter"
    if last & STRUCTURE:
        return "entry_structure"
    return "parameter"


def _quality_failure(case):
    outcome = str((_is_dict(case)).get("outcome") or "")
    return outcome in (
        "candidate_quality_failure", "CANDIDATE_QUALITY_FAILURE",
    )


def _ready(case):
    outcome = str((_is_dict(case)).get("outcome") or "")
    return outcome in ("candidate_ready", "review_submitted")


def build_clusters(cases, min_symbols=MIN_SYMBOLS, min_jobs=MIN_JOBS):
    bags = defaultdict(list)
    for case in cases or []:
        if not isinstance(case, dict):
            continue
        if not _quality_failure(case):
            continue
        bags[cluster_id_for(case)].append(case)

    clusters = []
    for cid, rows in sorted(bags.items()):
        symbols = sorted({
            str(row.get("symbol") or "").strip()
            for row in rows if str(row.get("symbol") or "").strip()
        })
        jobs = sorted({
            str(row.get("job_id") or "").strip()
            for row in rows if str(row.get("job_id") or "").strip()
        })
        rule_counter = Counter()
        for row in rows:
            for rule in is_rules(row.get("failed_rules")):
                rule_counter[rule] += 1
        top_rules = [item[0] for item in rule_counter.most_common(6)]
        sample = rows[0]
        family = sample.get("structure_family") or "unknown"
        fp = sample.get("feature_fingerprint") or "none"
        bucket = dominant_bucket(sample.get("failed_rules"))
        routes = Counter(route_failure(row) for row in rows)
        promoted = len(symbols) >= int(min_symbols) and len(jobs) >= int(min_jobs)
        lesson_zh = (
            "共同因子：结构家族 %s，入场指纹 %s，最常钉死 %s（%s）。"
            "这不是研究方向禁令，也不是某个标的或做多做空方向的禁令。"
            "只有多标的重复出现的结构/数字因子才作为创造提示。"
        ) % (
            family,
            fp,
            "、".join(RULE_LABELS.get(rule, rule) for rule in top_rules[:3]) or "未命名缺口",
            BUCKET_LABELS.get(bucket, bucket),
        )
        clusters.append({
            "cluster_id": cid,
            "promoted": bool(promoted),
            "n_jobs": len(jobs),
            "n_symbols": len(symbols),
            "min_support": {"jobs": int(min_jobs), "symbols": int(min_symbols)},
            "support_ok": bool(promoted),
            "structure_family": family,
            "feature_fingerprint": fp,
            "fail_bucket": bucket,
            "fail_bucket_zh": BUCKET_LABELS.get(bucket, bucket),
            "top_failed_rules": top_rules,
            "route_mode": (routes.most_common(1)[0][0] if routes else "parameter"),
            "lesson_zh": lesson_zh,
            "not_a_gate": True,
            "not_a_research_direction_ban": True,
            "symbols_sample": symbols[:8],
        })
    clusters.sort(key=lambda row: (-int(row.get("promoted") or 0), -int(row.get("n_jobs") or 0)))
    return clusters


def success_few_shot(cases, min_jobs=MIN_SUCCESS_JOBS):
    bags = defaultdict(list)
    for case in cases or []:
        if not isinstance(case, dict) or not _ready(case):
            continue
        family = str(case.get("structure_family") or "unknown")
        fp = str(case.get("feature_fingerprint") or "none")
        bags["%s|%s" % (family, fp)].append(case)
    shots = []
    for key, rows in sorted(bags.items()):
        jobs = {str(row.get("job_id") or "") for row in rows if row.get("job_id")}
        if len(jobs) < int(min_jobs):
            continue
        family, fp = key.split("|", 1)
        shots.append({
            "structure_family": family,
            "feature_fingerprint": fp,
            "n_jobs": len(jobs),
            "n_symbols": len({str(row.get("symbol") or "") for row in rows if row.get("symbol")}),
            "note_zh": (
                "过门共同结构：家族 %s，指纹 %s。只作形状提示，禁止抄参数阈值。"
            ) % (family, fp),
            "not_a_gate": True,
        })
    return shots[:12]


def feature_usage(cases):
    fail = Counter()
    ready = Counter()
    for case in cases or []:
        if not isinstance(case, dict):
            continue
        feats = [str(item) for item in (case.get("features") or []) if item]
        bucket = fail if _quality_failure(case) else (ready if _ready(case) else None)
        if bucket is None:
            continue
        for feat in feats:
            bucket[feat] += 1
    return {
        "fail_top": [{"feature": name, "n": n} for name, n in fail.most_common(12)],
        "ready_top": [{"feature": name, "n": n} for name, n in ready.most_common(12)],
        "note_zh": "formal feature 在失败/过门案例中的出现次数。不是研究方向黑名单。",
    }


def advisory_check_order(clusters):
    """Priority of IS/structure rules for Kimi. Cannot pass or fail a candidate."""
    counter = Counter()
    for cluster in clusters or []:
        if not cluster.get("promoted"):
            continue
        weight = int(cluster.get("n_jobs") or 1)
        for rule in cluster.get("top_failed_rules") or []:
            if rule in OOS_RULES:
                continue
            counter[rule] += weight
    items = []
    for rule, n in counter.most_common(8):
        bucket = dominant_bucket([rule])
        hint = "优先改退出几何/持仓，而不是再堆入场过滤"
        if bucket == "structure":
            hint = "优先补能读趋势方向与幅度的入场，而不是调数字门槛"
        elif bucket == "frequency":
            hint = "优先放宽过严入场或换机制，而不是缩短周期刷频率"
        elif bucket == "expectancy":
            hint = "优先检查机制是否真有顺势幅度，而不是刷盈亏比"
        items.append({
            "rule": rule,
            "label_zh": RULE_LABELS.get(rule, rule),
            "weight": n,
            "hint_zh": hint,
        })
    return {
        "not_a_gate": True,
        "quality_gate_unchanged": True,
        "items": items,
        "note_zh": "检查顺序只改变创造时先修什么。10项数字与趋势识字门槛不变，本表不能放行。",
    }
