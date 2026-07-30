# -*- coding: utf-8 -*-
"""Generate delivery_report.md after the auto-driver loop ends."""
from __future__ import print_function

import json
from datetime import datetime


def _fmt(x, nd=4):
    if x is None:
        return "-"
    if isinstance(x, float):
        return ("%." + str(nd) + "f") % x
    return str(x)


def _md_escape(s):
    return str(s or "").replace("|", "\\|").replace("\n", " ")


def write_delivery_report(path, state, cfg, initial_pack, final_pack):
    lines = []
    status = state.get("final_status") or "UNKNOWN"
    lines.append("# Auto-Driver Delivery Report")
    lines.append("")
    lines.append("Generated at: `%s`" % datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))
    lines.append("")
    lines.append("## 1. 终态总结")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append("| Final status | **%s** |" % status)
    lines.append("| Stop code | `%s` |" % (state.get("stop_code") or "-"))
    lines.append("| Iterations | %s |" % state.get("n_iterations"))
    lines.append("| Elapsed sec | %s |" % _fmt(state.get("elapsed_sec"), 1))
    lines.append("| Symbol / TF / Dir | `%s` / `%s` / `%s` |" % (
        cfg.get("symbol"), cfg.get("timeframe"), cfg.get("direction")))
    lines.append("| Initial family | `%s` |" % (
        ((initial_pack or {}).get("mechanism_spec") or {}).get("mechanism_family") or "-"))
    lines.append("| Final family | `%s` |" % (
        ((final_pack or {}).get("mechanism_spec") or {}).get("mechanism_family") or "-"))
    lines.append("| Workdir | `%s` |" % (state.get("workdir") or "-"))
    lines.append("")
    if state.get("stop_detail"):
        lines.append("Stop detail:")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(state.get("stop_detail"), ensure_ascii=False, indent=2, default=str)[:4000])
        lines.append("```")
        lines.append("")

    lines.append("## 2. 迭代轨迹表")
    lines.append("")
    lines.append("| Iter | Reason | Score | Gap | Pay | Calmar | W5 | 第二次复核 fills | AI decision | Patches |")
    lines.append("|---:|---|---:|---:|---:|---:|---:|---:|---|---|")
    for it in (state.get("iterations") or []):
        g2 = it.get("gate2") or {}
        l1 = it.get("l1") or {}
        patches = it.get("applied_patches") or []
        patch_s = ",".join([str(p.get("op")) for p in patches[:6]]) or "-"
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            it.get("iteration"),
            _md_escape(it.get("pipeline_reason")),
            _fmt(it.get("composite_score")),
            _fmt(it.get("total_gap")),
            _fmt(g2.get("payoff_ratio")),
            _fmt(g2.get("calmar")),
            _fmt(g2.get("worst5_loss_share")),
            _fmt(l1.get("filled_entries"), 0),
            _md_escape(it.get("ai_decision") or "-"),
            _md_escape(patch_s),
        ))
    lines.append("")

    lines.append("### 每轮摘要")
    lines.append("")
    for it in (state.get("iterations") or []):
        lines.append("#### Iteration %s" % it.get("iteration"))
        lines.append("")
        lines.append("- task_id: `%s`" % (it.get("task_id") or "-"))
        lines.append("- pipeline_reason: `%s`" % (it.get("pipeline_reason") or "-"))
        lines.append("- failed_checks: `%s`" % (it.get("failed_checks") or []))
        lines.append("- l1_reject: `%s`" % (it.get("l1_reject") or []))
        lines.append("- AI: `%s` — %s" % (
            it.get("ai_decision") or "-",
            _md_escape((it.get("ai_rationale") or "")[:400]),
        ))
        if it.get("limit_reason"):
            lines.append("- limit_reason: %s" % _md_escape(it.get("limit_reason")))
        lines.append("")

    if status not in ("SUCCESS",):
        lines.append("## 3. 极限分析（未过关）")
        lines.append("")
        lines.append(state.get("limit_analysis") or _default_limit_analysis(state))
        lines.append("")
    else:
        lines.append("## 3. 过关说明")
        lines.append("")
        lines.append("策略已通过当前驱动所托管的 STEP A 管道（`result.ok=true`）。")
        lines.append("请人工审查 `pending` / 人工确认签发流程；**本驱动不会自动 mount / confirm。**")
        lines.append("")

    lines.append("## 4. Key Diff / 变更说明")
    lines.append("")
    lines.append(_diff_summary(initial_pack, final_pack, state))
    lines.append("")

    lines.append("## 5. 待审查建议")
    lines.append("")
    for tip in (state.get("review_suggestions") or _default_suggestions(status, state)):
        lines.append("- %s" % tip)
    lines.append("")

    lines.append("## Appendix: config snapshot")
    lines.append("")
    lines.append("```json")
    safe_cfg = {k: v for k, v in (cfg or {}).items() if "key" not in str(k).lower()}
    lines.append(json.dumps(safe_cfg, ensure_ascii=False, indent=2, default=str)[:3000])
    lines.append("```")
    lines.append("")

    text = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _default_limit_analysis(state):
    parts = []
    parts.append("驱动在终止条件触发后停止，策略仍未 `ok=true`。")
    code = state.get("stop_code")
    if code == "AI_LIMIT_REACHED":
        parts.append("3 方 AI 多数/指定判定：在当前数据、硬规则与 non-negotiables 下已无进一步优化空间。")
        if state.get("ai_limit_reason"):
            parts.append("AI limit_reason: %s" % state.get("ai_limit_reason"))
    elif code == "CONVERGED_NO_IMPROVEMENT":
        parts.append("连续多轮 composite_score / gap 改善低于阈值，判定收敛到局部平台。")
    elif code == "MAX_ITERATIONS":
        parts.append("达到 max_iterations 安全上限。")
    elif code == "NO_PROGRESS_REPEATED_REASON":
        parts.append("连续多轮相同 pipeline_reason 且分数无抬升，补丁无效或同因反复。")
    # bottleneck from last fitness
    iters = state.get("iterations") or []
    if iters:
        last = iters[-1]
        fails = last.get("failed_checks") or []
        if fails:
            parts.append("最后一轮 failed_checks: %s" % fails)
        gaps = last.get("metric_gaps") or {}
        if gaps:
            ranked = sorted(gaps.items(), key=lambda kv: -float(kv[1] or 0))[:5]
            parts.append("主要指标缺口: %s" % ", ".join(["%s=%.4f" % (k, float(v)) for k, v in ranked]))
    return "\n\n".join(parts)


def _diff_summary(initial_pack, final_pack, state):
    lines = []
    ispec = (initial_pack or {}).get("mechanism_spec") or {}
    fspec = (final_pack or {}).get("mechanism_spec") or {}
    if ispec.get("mechanism_family") != fspec.get("mechanism_family"):
        lines.append("- mechanism_family: `%s` → `%s`" % (
            ispec.get("mechanism_family"), fspec.get("mechanism_family")))
    # collect applied patches across iters
    all_ops = []
    for it in (state.get("iterations") or []):
        for p in (it.get("applied_patches") or []):
            all_ops.append(p)
    if not all_ops:
        lines.append("- 无成功应用的 AI 补丁（可能仅第二次复核 seed 重试或 AI 不可用）。")
    else:
        lines.append("- 累计应用补丁 %d 条：" % len(all_ops))
        for p in all_ops[:40]:
            if p.get("op") == "set":
                lines.append("  - set `%s` = `%s`" % (p.get("path"), p.get("value")))
            else:
                lines.append("  - %s %s" % (p.get("op"), json.dumps(
                    {k: v for k, v in p.items() if k != "op"}, ensure_ascii=False, default=str)[:160]))
    # dsl max_hold / exit trail quick compare
    def _dsl(pack):
        return (pack or {}).get("dsl_long") or (pack or {}).get("dsl") or {}
    idsl, fdsl = _dsl(initial_pack), _dsl(final_pack)
    if idsl.get("max_hold_bars") != fdsl.get("max_hold_bars"):
        lines.append("- max_hold_bars: %s → %s" % (idsl.get("max_hold_bars"), fdsl.get("max_hold_bars")))
    return "\n".join(lines) if lines else "- （无显著字段级差异记录）"


def _default_suggestions(status, state):
    tips = []
    if status == "SUCCESS":
        tips.append("审查 pending human confirm 材料；仅在人工确认后 mount。")
        tips.append("核对 Walk-Forward / 抗风险拆分证据是否与第三次复核一致，防止样本运气。")
        tips.append("将成功 pack 归档到 strategy 版本库，并记录 auto-driver 迭代轨迹。")
    else:
        tips.append("阅读第 3 节极限分析：若瓶颈是第三次复核 worst5/lottery 与 vol 选择性冲突，需换机制族而非继续调参。")
        tips.append("检查 workdir 中各 iter 的 pack 快照与 gate JSON，确认 AI 补丁是否被 DSL validate 拒绝。")
        tips.append("若 AI_ABORT：检查 `/root/auto_trade/ai_ecosystem.env` 密钥与 `ai_research_consent.json`。")
        tips.append("不要手动解锁 KB family 后无差异重提；需有可区分的机制/样本策略。")
        tips.append("可将 delivery_report 与最终 pack 交给主 AI 做架构级 redesign，而不是继续同一入口的微扰。")
    tips.append("本驱动为外包 Wrapper：未修改 dual_engine_workflow_v2 管道核心逻辑。")
    return tips
