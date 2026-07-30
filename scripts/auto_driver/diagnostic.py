# -*- coding: utf-8 -*-
"""AI-readable post-mortem diagnostics for L1 / Gate2 failures."""
from __future__ import print_function

import re

from . import status_humanizer as humanizer


_REJECT_ZH = {
    "sample_filled_entries_lt_5": "样本量饥饿 — 无法满足最小统计显性 (n ≥ 5)",
    "sample_payoff_le_1.2": "盈亏比过低 — payoff 未达 L1 门槛 (≤ 1.2)",
    "severe_mae_gt_2.5x_avg_win": "逆向波动过大 — MAE 超过平均盈利 2.5 倍",
    "expectancy_non_positive": "期望收益非正 — classic expectancy ≤ 0",
}

_CHECK_ZH = {
    "payoff_ge_2_5": "盈亏比未达 Gate2 门槛 (payoff < 2.5)",
    "calmar_ge_1_5": "Calmar 未达 Gate2 门槛 (< 1.5)",
    "worst5_loss_share_le_40pct": "亏损集中度过高 (worst5 > 40%)",
    "remove_max_win_stable": "彩票单依赖 / 去掉最大盈利后崩塌",
    "expectancy_factor_ge_1_0": "因子期望不足 (< 1.0)",
    "sample_size_ge_8": "Gate2 样本量不足 (< 8)",
    "mae_dead_hold_clear": "死扛 / MAE 异常",
}

_TAG_ZH = {
    "pseudo_high_WR_low_payoff": "虚高胜率低盈亏比",
    "lottery_overfitting": "彩票单过拟合",
    "loss_concentration": "亏损集中",
}


def _fmt(x, digits=2, na="暂无"):
    try:
        if x is None:
            return na
        return ("%." + str(digits) + "f") % float(x)
    except Exception:
        return na


def _pct(x, digits=2, na="暂无"):
    try:
        if x is None:
            return na
        v = float(x)
        # already percent-like
        if abs(v) > 1.5:
            return ("%." + str(digits) + "f") % v + "%"
        return ("%." + str(digits) + "f") % (v * 100.0) + "%"
    except Exception:
        return na


def _cond_text(cond):
    if not isinstance(cond, dict):
        return None
    left = cond.get("left") or {}
    right = cond.get("right") or {}
    op = cond.get("op") or "?"
    lf = left.get("feature") or left.get("id") or "?"
    if "value" in right:
        rf = str(right.get("value"))
    else:
        rf = right.get("feature") or right.get("id") or "?"
        if right.get("scale") not in (None, 1, 1.0):
            rf = "%s×%s" % (rf, right.get("scale"))
    return "`%s %s %s`" % (lf, op, rf)


def _entry_conditions(dsl):
    entry = (dsl or {}).get("entry") or {}
    rows = []
    for c in (entry.get("all") or []):
        t = _cond_text(c)
        if t:
            rows.append({"id": c.get("id"), "text": t, "raw": c})
    return rows


def _vol_z_hint(conds):
    for c in conds:
        raw = c.get("raw") or {}
        left = raw.get("left") or {}
        right = raw.get("right") or {}
        feat = str(left.get("feature") or "")
        if "vol_z" in feat or feat == "z20":
            try:
                thr = float(right.get("value"))
            except Exception:
                thr = None
            return feat, thr, c.get("text")
    return None, None, None


def _primary_fail_stage(reason, l1, g2):
    reason = str(reason or "")
    if reason in ("funnel_l0_fail", "funnel_l0_cull"):
        return "l0"
    if reason in ("funnel_l1_fail", "funnel_l1_cull") or (
        l1 and l1.get("pass") is False and reason not in ("repair_exhausted_or_drift", "gate2_3_fail")
    ):
        # if gate2 has real fitness and reason is gate2-ish, prefer gate2
        if reason in ("repair_exhausted_or_drift", "gate2_3_fail"):
            return "gate2"
        if l1.get("filled_entries") is not None or l1.get("reject_reasons"):
            return "l1"
        if reason.startswith("funnel_l1"):
            return "l1"
    if reason in ("repair_exhausted_or_drift", "gate2_3_fail") or (
        g2 and (g2.get("failed_checks") or g2.get("pass") is False)
    ):
        return "gate2"
    if reason in ("kb_blocked",):
        return "kb"
    if reason in ("immutable_spec_error",):
        return "spec"
    if l1 and l1.get("pass") is False:
        return "l1"
    return "unknown"


def _main_cause_l0(l0):
    rejects = list((l0 or {}).get("reject_reasons") or [])
    m = (l0 or {}).get("metrics") or {}
    trig = m.get("triggers")
    ev = m.get("evaluated_bars")
    dens = m.get("density")
    return {
        "code": "REJECT_TOO_RARE" if "REJECT_TOO_RARE" in rejects else (rejects[0] if rejects else "l0_fail"),
        "title_zh": "开仓密度过稀 (L0 Density Cull)",
        "detail_zh": "历史触发 %s/%s（密度 %s）；拒绝进入矩阵回测以节省算力"
        % (
            trig if trig is not None else "—",
            ev if ev is not None else "—",
            dens if dens is not None else "—",
        ),
    }


def _main_cause_l1(l1):
    rejects = list(l1.get("reject_reasons") or [])
    n = l1.get("filled_entries")
    try:
        n_f = float(n) if n is not None else None
    except Exception:
        n_f = None
    if "sample_filled_entries_lt_5" in rejects or (n_f is not None and n_f < 5):
        return {
            "code": "sample_starvation",
            "title_zh": "样本量饥饿 (Sample Starvation)",
            "detail_zh": "无法满足最小统计显性 (n ≥ 5)",
        }
    if "sample_payoff_le_1.2" in rejects:
        return {
            "code": "weak_payoff",
            "title_zh": "盈亏比过低 (Weak Payoff)",
            "detail_zh": "L1 payoff 未越过 1.2 门槛，边不够厚",
        }
    if "severe_mae_gt_2.5x_avg_win" in rejects:
        return {
            "code": "severe_mae",
            "title_zh": "逆向波动过大 (Severe MAE)",
            "detail_zh": "持仓期间不利波动远超平均盈利",
        }
    if rejects:
        r0 = rejects[0]
        return {
            "code": r0,
            "title_zh": _REJECT_ZH.get(r0, humanizer.humanize_code(r0, fallback=r0)),
            "detail_zh": "；".join(_REJECT_ZH.get(x, x) for x in rejects[:3]),
        }
    return {
        "code": "l1_fail",
        "title_zh": "L1 微观筛选未过",
        "detail_zh": "样本或收益期望未达标",
    }


def _main_cause_gate2(g2):
    failed = list(g2.get("failed_checks") or [])
    tags = list(g2.get("verdict_tags") or [])
    parts = [_CHECK_ZH.get(x, x) for x in failed[:4]]
    tag_zh = [_TAG_ZH.get(t, t) for t in tags[:3]]
    title = "Gate2 适应度门禁未过"
    if "remove_max_win_stable" in failed or "lottery_overfitting" in tags:
        title = "彩票单 / 过拟合风险 (Lottery Overfit)"
    elif "worst5_loss_share_le_40pct" in failed:
        title = "亏损集中度失控 (Loss Concentration)"
    elif "payoff_ge_2_5" in failed:
        title = "盈亏比未达正式门槛 (Payoff < 2.5)"
    elif "calmar_ge_1_5" in failed:
        title = "回撤收益比不足 (Calmar < 1.5)"
    return {
        "code": ",".join(failed[:3]) or "gate2_fail",
        "title_zh": title,
        "detail_zh": "；".join(parts + tag_zh) or "正式适应度检查未通过",
    }


def _bottlenecks(stage, l1, g2, conds, family, l0=None):
    notes = []
    feat, thr, text = _vol_z_hint(conds)
    n = l1.get("filled_entries")
    try:
        n_f = float(n) if n is not None else None
    except Exception:
        n_f = None
    l0 = l0 or {}
    l0m = l0.get("metrics") or {}

    if stage == "l0":
        notes.append(
            "L0 触发 %s / %s（密度 %s），低于秒杀阈值；勿上矩阵。"
            % (l0m.get("triggers"), l0m.get("evaluated_bars"), l0m.get("density"))
        )
        if thr is not None:
            notes.append(
                "优先下调非因果量能门槛（当前 %s），保留扫荡/收回核心。"
                % (text or ("vol_z ≥ %s" % thr))
            )
        if len(conds) >= 4:
            notes.append(
                "入场 ALL 共 %d 条，合取过严是密度归零主因；先砍到 3 条因果核心。"
                % len(conds)
            )
    if stage == "l1" and n_f is not None and n_f < 5:
        if thr is not None and thr >= 1.5:
            notes.append(
                "条件 %s 阈值偏高（≥ %s），与其他入场条件叠加后极易样本饥饿。"
                % (text or feat, _fmt(thr, 1))
            )
        if len(conds) >= 5:
            notes.append(
                "入场 ALL 条件多达 %d 条，合取过滤过严，建议先保留因果核心 3–4 条再扩。"
                % len(conds)
            )
        # highlight stacked asia / sweep pairs
        feats = " ".join((c.get("text") or "") for c in conds)
        if "vol_z" in feats and ("asia_high" in feats or "asia_low" in feats or "pdl" in feats):
            notes.append(
                "量能门槛与极值/扫荡条件叠加，常见会滤掉绝大多数潜在触点；优先下调 vol_z 或放宽时窗。"
            )
    if stage == "l1" and "sample_payoff_le_1.2" in (l1.get("reject_reasons") or []):
        notes.append("即便有触点，盈亏比仍偏弱：检查出场是否过早止盈 / 止损是否被噪声打掉。")
    if stage == "gate2":
        failed = g2.get("failed_checks") or []
        if "remove_max_win_stable" in failed:
            notes.append("去掉单笔最大盈利后曲线崩塌 → 边依赖离群赢家，需 scale-out 或拉长样本矩阵。")
        if "worst5_loss_share_le_40pct" in failed:
            notes.append("Worst5 亏损占比过高 → 多笔近似等额小亏堆积；考虑部分止盈或收紧无效化。")
        if "payoff_ge_2_5" in failed:
            notes.append("Payoff 卡在正式门槛之下 → 优先改 exit（ATR trail / partial_tp），勿为抬样本乱砍 entry。")
        pay = g2.get("payoff_ratio")
        try:
            if pay is not None and float(pay) < 1.3:
                notes.append("Payoff 显著偏低，机制可能仍在吃假突破；考虑反手 / 收回确认，而非顺势追突破。")
        except Exception:
            pass
    if not notes and conds:
        notes.append("核心入场条件：" + " + ".join(c.get("text") for c in conds[:5] if c.get("text")))
    return notes[:5]


def _ai_prompt(stage, title_zh, family, l1, g2, cause, bottlenecks, symbol, timeframe, l0=None):
    fam = family or "unknown_family"
    l0 = l0 or {}
    l0m = l0.get("metrics") or {}
    if stage == "l0":
        tip = (
            "策略 %s（%s）在 L0 开仓密度预检被秒杀：触发 %s/%s（密度 %s）。"
            % (
                fam, title_zh,
                l0m.get("triggers"), l0m.get("evaluated_bars"), l0m.get("density"),
            )
        )
        if bottlenecks:
            tip += " " + bottlenecks[0]
        tip += (
            " 下一轮：保留扫荡+收回因果核心，优先下调 vol_z / 减少 AND 条数以抬密度；"
            "通过 L0 后再谈锚定 L1 / 矩阵。正式 Gate2 门槛（payoff≥2.5 / calmar≥1.5）不降；禁止自动上实盘。"
        )
        return tip
    if stage == "l1":
        n = l1.get("filled_entries")
        pay = l1.get("payoff_ratio")
        rej = ",".join(l1.get("reject_reasons") or []) or cause.get("code")
        tip = (
            "策略 %s（%s）在 L1 因「%s」淘汰。关键数据：n=%s，payoff=%s，reject=%s。"
            % (fam, title_zh, cause.get("title_zh"), n, _fmt(pay), rej)
        )
        if bottlenecks:
            tip += " 条件堵塞：" + bottlenecks[0]
        tip += (
            " 下一轮请在不降低 Gate2 正式门槛（payoff≥2.5 / calmar≥1.5）的前提下，"
            "优先放松非因果核心过滤或改时框/矩阵扩样本；禁止自动上实盘。"
        )
        return tip
    if stage == "gate2":
        tip = (
            "策略 %s（%s）过了 L1 但倒在 Gate2：%s。关键数据：payoff=%s，calmar=%s，w5=%s，failed=%s。"
            % (
                fam, title_zh, cause.get("title_zh"),
                _fmt(g2.get("payoff_ratio")), _fmt(g2.get("calmar")),
                _fmt(g2.get("worst5_loss_share")),
                ",".join(g2.get("failed_checks") or []),
            )
        )
        if bottlenecks:
            tip += " 诊断：" + bottlenecks[0]
        tip += (
            " 下一轮禁止降低 Gate2 门槛；优先改 exit / scale-out / 多标的矩阵平滑，"
            "Gate2-after-L1 时不要砍 entry 导致样本回落。"
        )
        return tip
    if stage in ("kb", "spec"):
        return (
            "策略 %s（%s / %s %s）演进失败：%s。请基于 Failure KB 教训另起机制族，勿微扰已归档族。"
            % (fam, title_zh, symbol or "?", timeframe or "?", cause.get("title_zh") or "未知")
        )
    return (
        "策略 %s（%s / %s %s）演进失败：%s。先补齐 L0/L1/Gate2 证据快照再决定是微扰还是换族。"
        % (fam, title_zh, symbol or "?", timeframe or "?", cause.get("title_zh") or "未知")
    )


def build_diagnostic(ctx=None, result=None, pack=None, title_zh=None,
                     symbol=None, timeframe=None, direction=None):
    """Return a UI/AI-readable diagnostic dict (never raises)."""
    try:
        ctx = ctx or {}
        pack = pack or {}
        spec = (pack.get("mechanism_spec") if isinstance(pack, dict) else None) or ctx.get("mechanism_spec") or {}
        dsl = ctx.get("dsl") or {}
        if not dsl and isinstance(pack, dict):
            if str(direction or ctx.get("direction") or "long").lower() == "short":
                dsl = pack.get("dsl_short") or pack.get("dsl") or {}
            else:
                dsl = pack.get("dsl_long") or pack.get("dsl") or {}

        reason = ctx.get("pipeline_reason") or (result or {}).get("reason")
        l1 = dict(ctx.get("l1") or {})
        l0 = dict(ctx.get("l0") or {})
        g2 = dict(ctx.get("gate2_fitness") or ctx.get("gate2") or {})
        if result and not l1.get("reject_reasons") and not l1.get("filled_entries"):
            try:
                from . import metrics as metrics_mod
                l1 = metrics_mod.extract_l1(result) or l1
                if not g2.get("failed_checks") and g2.get("payoff_ratio") is None:
                    g2 = metrics_mod.extract_gate2_fitness(result) or g2
                # pull L0 from phase3 funnel if present
                if not l0:
                    ph = (result.get("phase3_funnel") or {})
                    l0 = dict(ph.get("l0_density") or {})
            except Exception:
                pass

        family = ctx.get("mechanism_family") or spec.get("mechanism_family")
        title = title_zh or spec.get("display_title_zh") or family or "未命名策略"
        symbol = symbol or ctx.get("symbol")
        timeframe = timeframe or ctx.get("timeframe")
        stage = _primary_fail_stage(reason, l1, g2)
        conds = _entry_conditions(dsl)

        if stage == "l0":
            cause = _main_cause_l0(l0)
            culled_at = "L0 开仓密度预检"
        elif stage == "l1":
            cause = _main_cause_l1(l1)
            culled_at = "L1 微观筛选器"
        elif stage == "gate2":
            cause = _main_cause_gate2(g2)
            culled_at = "Gate2 / L2 门禁"
        elif stage == "kb":
            cause = {"code": "kb_blocked", "title_zh": "Failure KB 负面指纹拦截", "detail_zh": "该机制路径已被归档封锁"}
            culled_at = "Failure KB"
        elif stage == "spec":
            cause = {"code": "immutable_spec", "title_zh": "不可协商规格冲突", "detail_zh": "补丁触犯 non_negotiable / DSL 硬边界"}
            culled_at = "规格门禁"
        else:
            cause = {"code": str(reason or "unknown"), "title_zh": humanizer.humanize_code(reason) or "状态未完整落盘", "detail_zh": "缺少足够的 L0/L1/Gate2 证据快照"}
            culled_at = "未知阶段"

        bottlenecks = _bottlenecks(stage, l1, g2, conds, family, l0=l0)
        l0m = (l0 or {}).get("metrics") or {}
        if stage == "l0":
            fatal = {
                "n": l0m.get("triggers"),
                "payoff": None,
                "expectancy": None,
                "calmar": None,
                "w5": None,
                "win_rate": None,
                "evaluated_bars": l0m.get("evaluated_bars"),
                "density": l0m.get("density"),
            }
            fatal_line = (
                "L0 触发次数 = %s / 评估K线 = %s | 密度 = %s | 阈值 = ≥%s"
                % (
                    fatal["n"] if fatal["n"] is not None else "—",
                    fatal.get("evaluated_bars") if fatal.get("evaluated_bars") is not None else "—",
                    fatal.get("density") if fatal.get("density") is not None else "—",
                    l0m.get("min_triggers") if l0m.get("min_triggers") is not None else 30,
                )
            )
        else:
            fatal = {
                "n": l1.get("filled_entries") if stage == "l1" else g2.get("sample_size"),
                "payoff": l1.get("payoff_ratio") if stage == "l1" else g2.get("payoff_ratio"),
                "expectancy": l1.get("expectancy_factor") if stage == "l1" else g2.get("expectancy_factor"),
                "calmar": g2.get("calmar"),
                "w5": g2.get("worst5_loss_share"),
                "win_rate": l1.get("win_rate") if stage == "l1" else g2.get("win_rate_pct"),
            }
            fatal_line = (
                "实际回测样本 n = %s 次 | 盈亏比 payoff = %s | 期望因子 = %s | Calmar = %s | worst5 = %s"
                % (
                    fatal["n"] if fatal["n"] is not None else "—",
                    _fmt(fatal["payoff"]),
                    _fmt(fatal["expectancy"]),
                    _fmt(fatal["calmar"]) if stage == "gate2" else "暂无",
                    _fmt(fatal["w5"]) if stage == "gate2" else "暂无",
                )
            )
        reject_lines = []
        for r in (l0.get("reject_reasons") or [])[:5]:
            reject_lines.append(r)
        for r in (l1.get("reject_reasons") or [])[:5]:
            reject_lines.append(_REJECT_ZH.get(r, r))
        for c in (g2.get("failed_checks") or [])[:5]:
            reject_lines.append(_CHECK_ZH.get(c, c))

        ai_prompt = _ai_prompt(
            stage, title, family, l1, g2, cause, bottlenecks, symbol, timeframe, l0=l0,
        )
        terminal_zh = "已归档（淘汰于 %s）" % culled_at
        if stage == "unknown" and not (
            l0m.get("triggers") is not None
            or l1.get("filled_entries") is not None
            or g2.get("failed_checks")
        ):
            terminal_zh = "进程已停止（诊断证据不足，建议查看 delivery_report）"

        markdown = "\n".join([
            "### 归因诊断与经验沉淀",
            "- 策略：%s" % title,
            "- 家族：%s" % (family or "—"),
            "- 淘汰主因：%s — %s" % (cause.get("title_zh"), cause.get("detail_zh")),
            "- 致命数据：%s" % fatal_line,
            "- 条件堵塞：",
        ] + [("  - %s" % b) for b in (bottlenecks or ["暂无"])] + [
            "- AI 进化指导：",
            '  "%s"' % ai_prompt,
        ])

        return {
            "schema": "qiyu_auto_driver_diagnostic_v1",
            "ok": True,
            "stage": stage,
            "culled_at": culled_at,
            "terminal_zh": terminal_zh,
            "main_cause": cause,
            "main_cause_line": "%s — %s" % (cause.get("title_zh"), cause.get("detail_zh")),
            "fatal_metrics": fatal,
            "fatal_line": fatal_line,
            "reject_lines": reject_lines,
            "bottlenecks": bottlenecks,
            "entry_conditions": [c.get("text") for c in conds],
            "ai_prompt": ai_prompt,
            "markdown": markdown,
            "pipeline_reason": reason,
            "pipeline_reason_zh": humanizer.humanize_code(reason),
        }
    except Exception as exc:
        return {
            "schema": "qiyu_auto_driver_diagnostic_v1",
            "ok": False,
            "error": str(exc),
            "terminal_zh": "诊断生成失败",
            "main_cause_line": str(exc),
            "ai_prompt": "",
            "bottlenecks": [],
            "fatal_line": "",
        }
