# -*- coding: utf-8 -*-
"""Strict OOS reproduction of Phase0 Region A/B before any strategization."""
from __future__ import print_function

import json
from datetime import datetime
from pathlib import Path

from dual_engine_workflow_v2 import path_outcome as po
from project_prometheus import phase0_states as S
from project_prometheus import conversion_contract as CC


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_ts(ts):
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        try:
            return datetime.utcfromtimestamp(float(ts) / (1000.0 if float(ts) > 1e12 else 1.0))
        except Exception:
            return None
    s = str(ts).replace("T", " ").split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def _bars_per_week(tf="5m"):
    return (7.0 * 24.0 * 60.0) / 5.0


def match_region_indices(states, region, warmup=80, stride=1):
    idxs = []
    n = len(states)
    horizon = int(region["horizon"])
    for i in range(warmup, n - horizon - 2, stride):
        st = states[i]
        if st.get("vol") != region["vol"]:
            continue
        if st.get("trend") != region["trend"]:
            continue
        if st.get("session") != region["session"]:
            continue
        idxs.append(i)
    return idxs


def metrics_for_indices(candles, idxs, direction, horizon, tf="5m"):
    labels = []
    for si in idxs:
        lab = po.label_signal(
            candles, si, direction, horizon,
            mapping="next_bar_open",
            target_pct=CC.TARGET_PRICE_PCT,
            stop_pct=CC.STOP_PRICE_PCT,
        )
        if lab is not None:
            labels.append(lab)
    summ = po.summarize_labels(labels)
    resolved = int(summ.get("n_profit_first") or 0) + int(summ.get("n_loss_first") or 0)
    wr = None
    if resolved > 0:
        wr = float(summ.get("n_profit_first") or 0) / float(resolved)
    mean_gross = summ.get("mean_winning_levered")
    mean_net = (float(mean_gross) - CC.LEVERED_FEE_DRAG) if mean_gross is not None else None
    weekly = None
    if idxs and len(idxs) >= 2:
        span = max(idxs) - min(idxs) + 1
        weeks = float(span) / _bars_per_week(tf)
        if weeks > 0:
            weekly = float(len(idxs)) / weeks
    elif idxs and len(idxs) == 1:
        weekly = float(len(idxs)) / 1.0  # degenerate
    return {
        "win_rate": wr,
        "average_profitable_trade_return_leveraged_net": mean_net,
        "weekly_frequency": weekly,
        "trade_count": resolved,
        "n_signals": len(idxs),
        "n_labeled": int(summ.get("n") or 0),
        "mean_winning_levered_gross": mean_gross,
    }


def gates_ok(m, min_n=CC.MIN_RESOLVED):
    reasons = []
    wr = m.get("win_rate")
    net = m.get("average_profitable_trade_return_leveraged_net")
    weekly = m.get("weekly_frequency")
    n = int(m.get("trade_count") or 0)
    if n < int(min_n):
        reasons.append("trade_count_lt_%d" % min_n)
    if wr is None or wr < CC.WIN_RATE_MIN:
        reasons.append("win_rate_lt_%.2f" % CC.WIN_RATE_MIN)
    if net is None or net < CC.MEAN_WIN_LEVERED_NET_MIN:
        reasons.append("mean_win_net_lt_%.4f" % CC.MEAN_WIN_LEVERED_NET_MIN)
    if weekly is None or weekly < CC.WEEKLY_FREQ_MIN:
        reasons.append("weekly_lt_%.2f" % CC.WEEKLY_FREQ_MIN)
    return len(reasons) == 0, reasons


def time_splits(candles):
    """Index ranges: train 50% / val 15% / test 15% / rolling_oos last 20% chunks / recent_2y."""
    n = len(candles)
    # prefer calendar recent_2y if span allows
    times = [_parse_ts(c.get("ts")) for c in candles]
    valid_t = [t for t in times if t is not None]
    recent_2y = None
    if valid_t:
        t_end = valid_t[-1]
        # 730 calendar days
        from datetime import timedelta
        cut = t_end - timedelta(days=730)
        idxs = [i for i, t in enumerate(times) if t is not None and t >= cut]
        recent_2y = idxs
        span_days = (t_end - valid_t[0]).total_seconds() / 86400.0
    else:
        span_days = None

    i1 = int(n * 0.50)
    i2 = int(n * 0.65)
    i3 = int(n * 0.80)
    splits = {
        "train": list(range(0, i1)),
        "validation": list(range(i1, i2)),
        "test": list(range(i2, i3)),
        "holdout_tail": list(range(i3, n)),
    }
    # rolling OOS: 4 contiguous folds on last 50%
    rolling = {}
    start = int(n * 0.50)
    fold = max(1, (n - start) // 4)
    for k in range(4):
        a = start + k * fold
        b = start + (k + 1) * fold if k < 3 else n
        rolling["rolling_oos_%d" % (k + 1)] = list(range(a, b))
    return splits, rolling, recent_2y, span_days


def reproduce_region(candles, region, stride=1):
    states = S.build_state_series(candles)
    all_idx = match_region_indices(states, region, stride=stride)
    splits, rolling, recent_2y_idxs, span_days = time_splits(candles)
    direction = int(region["direction_sign"])
    horizon = int(region["horizon"])

    def subset_metrics(allowed_set, min_n=20):
        idxs = [i for i in all_idx if i in allowed_set]
        m = metrics_for_indices(candles, idxs, direction, horizon, region["timeframe"])
        ok, reasons = gates_ok(m, min_n=min_n)
        m["gates_ok"] = ok
        m["fail_reasons"] = reasons
        return m

    allowed = {name: set(ix) for name, ix in splits.items()}
    report = {
        "region_id": region["region_id"],
        "spec": {k: region[k] for k in (
            "symbol", "timeframe", "direction", "vol", "trend", "session", "horizon"
        )},
        "phase0_observed": region.get("phase0_observed"),
        "n_candles": len(candles),
        "data_span_days": span_days,
        "n_region_signals_full": len(all_idx),
        "full_sample": subset_metrics(set(range(len(candles))), min_n=40),
        "train": subset_metrics(allowed["train"], min_n=20),
        "validation": subset_metrics(allowed["validation"], min_n=15),
        "test": subset_metrics(allowed["test"], min_n=15),
        "holdout_tail": subset_metrics(allowed["holdout_tail"], min_n=15),
        "rolling_oos": {},
        "recent_2y": None,
    }
    for name, ix in rolling.items():
        report["rolling_oos"][name] = subset_metrics(set(ix), min_n=10)

    if recent_2y_idxs is not None:
        r2 = subset_metrics(set(recent_2y_idxs), min_n=40)
        r2["n_bars_in_window"] = len(recent_2y_idxs)
        r2["window_days_available"] = span_days
        r2["requires_730d"] = True
        if span_days is not None and span_days < 600:
            r2["data_coverage_insufficient"] = True
            r2["fail_reasons"] = list(r2.get("fail_reasons") or []) + [
                "recent_2y_data_span_lt_600d(got=%.1f)" % span_days
            ]
            r2["gates_ok"] = False
        report["recent_2y"] = r2

    # Strict OOS verdict: validation AND test AND majority of rolling folds must pass
    # (full_sample alone is NOT enough — that was Phase0 selection)
    oos_parts = [
        ("validation", report["validation"]),
        ("test", report["test"]),
        ("holdout_tail", report["holdout_tail"]),
    ]
    rolling_ok = sum(1 for m in report["rolling_oos"].values() if m.get("gates_ok"))
    rolling_n = len(report["rolling_oos"])
    strict_ok = (
        report["validation"].get("gates_ok")
        and report["test"].get("gates_ok")
        and report["holdout_tail"].get("gates_ok")
        and rolling_ok >= max(1, (rolling_n + 1) // 2)
    )
    # recent_2y required for Formal path; mark separately
    recent_ok = bool((report.get("recent_2y") or {}).get("gates_ok"))

    if not strict_ok:
        status = "PHASE0_FALSE_POSITIVE"
    elif not recent_ok:
        status = "OOS_NUMERIC_OK_BUT_RECENT2Y_FAIL"
    else:
        status = "OOS_REPRODUCED"

    report["status"] = status
    report["strict_oos_pass"] = bool(strict_ok)
    report["recent_2y_pass"] = bool(recent_ok)
    report["may_strategize"] = status == "OOS_REPRODUCED"
    report["rolling_oos_pass_count"] = "%d/%d" % (rolling_ok, rolling_n)
    return report


def run_all(candles_by_key, stride=1):
    out = {"at": _now(), "contract": {
        "win_rate_minimum": CC.WIN_RATE_MIN,
        "mean_win_net_minimum": CC.MEAN_WIN_LEVERED_NET_MIN,
        "weekly_minimum": CC.WEEKLY_FREQ_MIN,
        "stop": CC.STOP_PRICE_PCT,
        "leverage": CC.LEVERAGE,
    }, "regions": {}}
    for rid, region in CC.REGIONS.items():
        key = "%s|%s" % (region["symbol"], region["timeframe"])
        candles = candles_by_key.get(key)
        if not candles:
            out["regions"][rid] = {"status": "NO_DATA", "may_strategize": False}
            continue
        out["regions"][rid] = reproduce_region(candles, region, stride=stride)
    return out


def render_md(pack):
    lines = []
    lines.append("# Phase0 Region → Strategy：样本外复现报告")
    lines.append("")
    lines.append("- at: %s" % pack.get("at"))
    lines.append("- WR floor: **%.2f** · mean_win_net: **%.4f** · weekly: **%.2f**" % (
        CC.WIN_RATE_MIN, CC.MEAN_WIN_LEVERED_NET_MIN, CC.WEEKLY_FREQ_MIN,
    ))
    lines.append("")
    for rid, r in (pack.get("regions") or {}).items():
        lines.append("## Region %s — **%s**" % (rid, r.get("status")))
        lines.append("")
        lines.append("- may_strategize: `%s`" % r.get("may_strategize"))
        lines.append("- strict_oos_pass: `%s` · recent_2y_pass: `%s` · rolling: %s" % (
            r.get("strict_oos_pass"), r.get("recent_2y_pass"), r.get("rolling_oos_pass_count"),
        ))
        lines.append("- data_span_days: %s · n_region_signals_full: %s" % (
            r.get("data_span_days"), r.get("n_region_signals_full"),
        ))
        lines.append("")
        lines.append("| split | wr | mean_win_net | weekly | n | gates_ok | fails |")
        lines.append("|---|---|---|---|---|---|---|")
        for name in ("full_sample", "train", "validation", "test", "holdout_tail", "recent_2y"):
            m = r.get(name) or {}
            if not m:
                continue
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                name,
                None if m.get("win_rate") is None else round(m["win_rate"], 3),
                None if m.get("average_profitable_trade_return_leveraged_net") is None
                else round(m["average_profitable_trade_return_leveraged_net"], 4),
                None if m.get("weekly_frequency") is None else round(m["weekly_frequency"], 2),
                m.get("trade_count"),
                m.get("gates_ok"),
                ",".join(m.get("fail_reasons") or [])[:60],
            ))
        for name, m in (r.get("rolling_oos") or {}).items():
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                name,
                None if m.get("win_rate") is None else round(m["win_rate"], 3),
                None if m.get("average_profitable_trade_return_leveraged_net") is None
                else round(m["average_profitable_trade_return_leveraged_net"], 4),
                None if m.get("weekly_frequency") is None else round(m["weekly_frequency"], 2),
                m.get("trade_count"),
                m.get("gates_ok"),
                ",".join(m.get("fail_reasons") or [])[:60],
            ))
        lines.append("")
    return "\n".join(lines)
