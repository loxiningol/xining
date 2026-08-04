# -*- coding: utf-8 -*-
"""Scan market-state regions for path-event mechanism existence."""
from __future__ import print_function

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dual_engine_workflow_v2 import path_outcome as po

from project_prometheus import phase0_contract as C
from project_prometheus import phase0_states as S


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _tf_minutes(tf):
    s = str(tf).lower().strip()
    if s.endswith("m"):
        return int(s[:-1])
    if s.endswith("h"):
        return int(s[:-1]) * 60
    return 5


def _bars_per_week(tf):
    return max(1.0, (7.0 * 24.0 * 60.0) / float(_tf_minutes(tf)))


def evaluate_region(candles, signal_indices, direction, horizon, tf):
    """Path-event stats for one mechanism region."""
    labels = []
    for si in signal_indices:
        lab = po.label_signal(
            candles, si, direction, horizon,
            mapping="next_bar_open",
            target_pct=C.TARGET_PRICE_PCT,
            stop_pct=C.STOP_PRICE_PCT,
        )
        if lab is not None:
            labels.append(lab)
    summ = po.summarize_labels(labels)
    resolved = int(summ.get("n_profit_first") or 0) + int(summ.get("n_loss_first") or 0)
    n = int(summ.get("n") or 0)
    pfr_resolved = None
    if resolved > 0:
        pfr_resolved = float(summ.get("n_profit_first") or 0) / float(resolved)

    mean_win_lev_gross = summ.get("mean_winning_levered")
    mean_win_lev_net = None
    if mean_win_lev_gross is not None:
        mean_win_lev_net = float(mean_win_lev_gross) - float(C.LEVERED_FEE_DRAG)

    # weekly frequency: entries per week over sample span
    weekly = None
    if n >= 2 and signal_indices:
        span_bars = max(signal_indices) - min(signal_indices) + 1
        weeks = float(span_bars) / float(_bars_per_week(tf))
        if weeks > 0:
            weekly = float(len(signal_indices)) / weeks

    # existence checks
    reasons = []
    exists = True
    if resolved < int(C.EXISTENCE_MIN_RESOLVED):
        exists = False
        reasons.append("resolved_lt_%d" % C.EXISTENCE_MIN_RESOLVED)
    if pfr_resolved is None or pfr_resolved < float(C.EXISTENCE_WIN_RATE_MIN):
        exists = False
        reasons.append("profit_first_rate_lt_%.2f" % C.EXISTENCE_WIN_RATE_MIN)
    if mean_win_lev_net is None or mean_win_lev_net < float(C.EXISTENCE_MEAN_WIN_LEVERED_NET_MIN):
        exists = False
        reasons.append("mean_win_levered_net_lt_%.4f" % C.EXISTENCE_MEAN_WIN_LEVERED_NET_MIN)
    if weekly is None or weekly < float(C.EXISTENCE_WEEKLY_FREQ_MIN):
        exists = False
        reasons.append("weekly_lt_%.2f" % C.EXISTENCE_WEEKLY_FREQ_MIN)
    tpr = float(summ.get("temporal_positive_ratio") or 0.0)
    if tpr < float(C.EXISTENCE_MIN_TEMPORAL_POSITIVE):
        exists = False
        reasons.append("temporal_positive_ratio_lt_%.2f" % C.EXISTENCE_MIN_TEMPORAL_POSITIVE)

    return {
        "n_signals": len(signal_indices),
        "n_labeled": n,
        "n_resolved": resolved,
        "profit_first_rate_all": summ.get("profit_first_rate"),
        "profit_first_rate_resolved": pfr_resolved,
        "mean_winning_move_pct": summ.get("mean_winning_move_pct"),
        "mean_winning_levered_gross": mean_win_lev_gross,
        "mean_winning_levered_net": mean_win_lev_net,
        "weekly_event_frequency": weekly,
        "temporal_positive_ratio": tpr,
        "median_mae_pct": summ.get("median_mae_pct"),
        "median_mfe_pct": summ.get("median_mfe_pct"),
        "horizon": horizon,
        "direction": "long" if direction > 0 else "short",
        "existence_pass": bool(exists),
        "fail_reasons": reasons,
        "fee_drag_levered": C.LEVERED_FEE_DRAG,
        "required_mean_win_move_pct": C.REQUIRED_MEAN_WIN_MOVE_PCT,
    }


def scan_symbol(
    candles,
    symbol,
    timeframe,
    directions=(1, -1),
    horizons=None,
    stride=None,
    max_cells_per_spec=None,
):
    horizons = horizons or C.HORIZONS
    stride = int(stride if stride is not None else C.STRIDE_DEFAULT)
    states = S.build_state_series(candles)
    n = len(candles)
    warmup = 80
    results = []

    for spec in S.MECHANISM_SPECS:
        keys = list(spec.get("keys") or [])
        # group signal indices by cell
        buckets = defaultdict(list)
        for i in range(warmup, n - max(horizons) - 2, stride):
            st = states[i]
            if keys:
                if any(st.get(k) in (None, "UNK") for k in keys):
                    continue
                cid = S.cell_id(st, keys)
            else:
                cid = "ALL"
            buckets[cid].append(i)

        # optionally cap cells by sample size rank
        cells = list(buckets.items())
        cells.sort(key=lambda kv: -len(kv[1]))
        if max_cells_per_spec is not None:
            cells = cells[: int(max_cells_per_spec)]

        for cid, idxs in cells:
            for direction in directions:
                for horizon in horizons:
                    stats = evaluate_region(
                        candles, idxs, direction, int(horizon), timeframe
                    )
                    results.append({
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "mechanism_id": spec["mechanism_id"],
                        "payoff_source": spec["payoff_source"],
                        "description": spec["description"],
                        "cell": cid,
                        "state_keys": keys,
                        **stats,
                    })
    return results


def existence_verdict(rows):
    passed = [r for r in rows if r.get("existence_pass")]
    # structural note: target-exit net < 11.11% by fee construction
    structural = {
        "target_levered_gross": C.TARGET_LEVERED_GROSS,
        "target_levered_net_if_exit_at_exact_target": C.TARGET_LEVERED_NET,
        "fee_drag_levered": C.LEVERED_FEE_DRAG,
        "net_1111_requires_mean_win_move_pct_ge": C.REQUIRED_MEAN_WIN_MOVE_PCT,
        "note": (
            "Exiting exactly at +0.5555pct yields levered gross 11.11pct but net "
            "~{:.2f}pct after documented fee/slip drag; existence of mean_win_net>=11.11pct "
            "requires winning paths with mean MFE beyond the minimum target."
        ).format(C.TARGET_LEVERED_NET * 100.0),
    }
    if passed:
        return {
            "verdict": "MECHANISM_EXISTENCE_EVIDENCE_FOUND",
            "n_passing_regions": len(passed),
            "n_regions_tested": len(rows),
            "passing": passed,
            "structural_fee_note": structural,
            "statement_zh": (
                "在已扫描的市场状态区域中，发现 %d 个区域满足 Phase0 存在性门槛；"
                "机制存在性得到证据支持（仍非 Formal PASS / 非策略创造）。"
                % len(passed)
            ),
        }
    # best near-misses for reproducibility
    ranked = sorted(
        rows,
        key=lambda r: (
            float(r.get("profit_first_rate_resolved") or 0.0),
            float(r.get("mean_winning_levered_net") or -9),
            float(r.get("weekly_event_frequency") or 0.0),
            int(r.get("n_resolved") or 0),
        ),
        reverse=True,
    )
    return {
        "verdict": "NO_CONTRACT_SATISFYING_MECHANISM_FOUND",
        "n_passing_regions": 0,
        "n_regions_tested": len(rows),
        "passing": [],
        "top_near_misses": ranked[:20],
        "structural_fee_note": structural,
        "statement_zh": (
            "当前市场未发现满足合同的收益机制"
            "（路径先触及+0.5555%再触及-0.5%；胜率≥70%；"
            "盈利单杠杆净收益均值≥11.11%；周频≥0.1；可重复样本）。"
            "不得据此继续投入策略创造 / 旧路线优化。"
        ),
    }


def save_pack(pack, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "PHASE0_EXISTENCE.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    md = render_report(pack)
    (out_dir / "PHASE0_EXISTENCE_REPORT.md").write_text(md, encoding="utf-8")
    (out_dir / "Prometheus_Phase0_收益机制存在性报告.md").write_text(md, encoding="utf-8")
    return out_dir


def render_report(pack):
    v = pack.get("verdict_pack") or {}
    lines = []
    lines.append("# Project Prometheus Phase 0 — 收益机制存在性研究报告")
    lines.append("")
    lines.append("- at: %s" % pack.get("at"))
    lines.append("- object: **Profit Mechanism**（非 Strategy）")
    lines.append("- path event: 先触及 **+0.5555%** 再触及 **-0.5%**")
    lines.append("- Route-0 / Discovery / Builder / Repair / Evolution: **未使用、未扩展**")
    lines.append("")
    lines.append("## 唯一验收回答")
    lines.append("")
    lines.append("### 是否证明存在（或不存在）符合合同约束的收益机制？")
    lines.append("")
    lines.append("**%s**" % v.get("verdict"))
    lines.append("")
    lines.append(v.get("statement_zh") or "")
    lines.append("")
    lines.append("### 是否形成可独立重复验证的机制证据？")
    lines.append("")
    lines.append(
        "YES — 全部区域统计写入 `PHASE0_EXISTENCE.json`；"
        "门槛与费用模型固定；可用同数据复算。"
        if pack.get("rows")
        else "NO"
    )
    lines.append("")
    lines.append("### 是否避免在未证明机制存在前继续策略创造研发？")
    lines.append("")
    lines.append("**YES** — Phase 0 不创造策略；Route-0 保持冻结/淘汰。")
    lines.append("")
    lines.append("## 存在性门槛")
    lines.append("")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append("| leverage | %s |" % C.LEVERAGE)
    lines.append("| stop | %.4f |" % C.STOP_PRICE_PCT)
    lines.append("| target path | %.6f |" % C.TARGET_PRICE_PCT)
    lines.append("| win_rate (profit_first resolved) | ≥ %.2f |" % C.EXISTENCE_WIN_RATE_MIN)
    lines.append("| mean_win levered NET | ≥ %.4f |" % C.EXISTENCE_MEAN_WIN_LEVERED_NET_MIN)
    lines.append("| weekly frequency | ≥ %.2f |" % C.EXISTENCE_WEEKLY_FREQ_MIN)
    lines.append("| min resolved | ≥ %s |" % C.EXISTENCE_MIN_RESOLVED)
    lines.append("| fee drag levered | %.4f |" % C.LEVERED_FEE_DRAG)
    lines.append("")
    sn = v.get("structural_fee_note") or {}
    lines.append("## 结构费用注释（可复现）")
    lines.append("")
    lines.append("- target levered gross: **%.4f**" % float(sn.get("target_levered_gross") or 0))
    lines.append(
        "- target levered net if exit at exact target: **%.4f**"
        % float(sn.get("target_levered_net_if_exit_at_exact_target") or 0)
    )
    lines.append(
        "- net≥11.11%% requires mean winning move ≥ **%.6f**"
        % float(sn.get("net_1111_requires_mean_win_move_pct_ge") or 0)
    )
    lines.append("- %s" % (sn.get("note") or ""))
    lines.append("")
    lines.append("## 扫描摘要")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    lines.append("| symbols | %s |" % pack.get("symbols"))
    lines.append("| n_regions_tested | %s |" % v.get("n_regions_tested"))
    lines.append("| n_passing_regions | %s |" % v.get("n_passing_regions"))
    lines.append("")
    if v.get("passing"):
        lines.append("## Passing regions")
        lines.append("")
        for r in v.get("passing")[:30]:
            lines.append(
                "- `%s` %s %s %s cell=`%s` horizon=%s pfr=%.3f mean_net=%.4f weekly=%.3f n=%s"
                % (
                    r.get("mechanism_id"),
                    r.get("symbol"),
                    r.get("timeframe"),
                    r.get("direction"),
                    r.get("cell"),
                    r.get("horizon"),
                    float(r.get("profit_first_rate_resolved") or 0),
                    float(r.get("mean_winning_levered_net") or 0),
                    float(r.get("weekly_event_frequency") or 0),
                    r.get("n_resolved"),
                )
            )
    else:
        lines.append("## Top near-misses（非存在证明）")
        lines.append("")
        lines.append("| mechanism | symbol | tf | dir | cell | H | pfr | mean_net | weekly | n | fails |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in (v.get("top_near_misses") or [])[:15]:
            lines.append(
                "| %s | %s | %s | %s | %s | %s | %.3f | %.4f | %.3f | %s | %s |"
                % (
                    r.get("mechanism_id"),
                    r.get("symbol"),
                    r.get("timeframe"),
                    r.get("direction"),
                    str(r.get("cell"))[:40],
                    r.get("horizon"),
                    float(r.get("profit_first_rate_resolved") or 0),
                    float(r.get("mean_winning_levered_net") or 0),
                    float(r.get("weekly_event_frequency") or 0),
                    r.get("n_resolved"),
                    ",".join((r.get("fail_reasons") or [])[:2]),
                )
            )
    lines.append("")
    lines.append("## 禁止事项遵守")
    lines.append("")
    lines.append("- 未新增 Prompt / Discovery / Builder / Repair / Evolution / Route-0 模块")
    lines.append("- 未创造策略、未提交 Formal")
    lines.append("- 研究对象是收益机制存在性，不是策略表达")
    lines.append("")
    return "\n".join(lines)
