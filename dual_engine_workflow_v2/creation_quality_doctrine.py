# -*- coding: utf-8 -*-
"""策略创造质量教义 — 全链路唯一真相源。

================================================================
创造策略指令之后，系统只按下面三步顺序叙述与执行（禁止并行分叉说法）：

  第一步：研究发现
      委员会 + 探针 + 多重检验

  第二步：门槛（全部关卡统一放在本步）
      · 胜率严格大于 50%
      · 去最大盈利后不崩
      · 原有漏斗 L0–L3
      · 门槛 0 至 7 的逻辑
      · 寒霜贰 / crec 扫描：
          快速筛 = 小样本前向比例门
          完整筛 = 蒙特卡洛与摩擦

  第三步：四阶段复核（与复核模块对接）
      只有过了第二步门槛的策略，才交给四阶段复核

================================================================

本模块存放第二步共用的数值与判定函数；第一步/第三步各有专属模块。
"""
from __future__ import print_function


# ---- 创造管道固定顺序（给人看的唯一叙述）----
CREATION_PIPELINE_ORDER_ZH = (
    "创造策略指令",
    "第一步：研究发现（委员会 + 探针 + 多重检验）",
    "第二步：门槛（胜率大于50% + 去最大盈利后不崩 + 漏斗L0-L3 + 门槛0至7"
    " + 寒霜贰/crec 快速筛与完整筛，全部统一在本步）",
    "第三步：四阶段复核（与复核模块对接）",
)

# ---- 第二步：胜率硬底 ----
# 严格大于：胜率==50% 不得声称创造成功
MIN_WIN_RATE_EXCLUSIVE = 0.50
MIN_WIN_RATE_PCT_FLOOR = 50.0  # 复核侧百分比口径的下限（≥50%）

# ---- 第二步：成交笔数（创造侧全链路对齐）----
MIN_TRADES_CREATION = 8
MIN_INDEPENDENT_EVENTS = 8

# ---- 第二步：小样本前向稳健（快速筛 / 门槛3 共用）----
SMALL_N_TRADE_BOUNDARY = 20  # 成交笔数低于此 → 小样本前向
WF_POS_RATIO = 0.6           # 正折 / 可用折
WF_POS_MIN_AVAILABLE = 5     # 或至少这么多正折
WF_LARGE_NEED_PASS = 7       # 大样本：至少通过窗数
WF_LARGE_NEED_TOTAL = 10     # 大样本：总窗数

# ---- 第二步：适应度地板（原门槛2 逻辑，按样本量）----
GATE2_SMALL_N_BOUNDARY = 15
GATE2_CALMAR_LARGE = 1.5
GATE2_PAYOFF_LARGE = 2.5
GATE2_CALMAR_SMALL = 1.0
GATE2_PAYOFF_SMALL = 1.8
GATE2_EXPECTANCY_FACTOR_MIN = 1.0  # 胜率 × 盈亏比；始终硬

# ---- 第二步：蒙特卡洛击败率（按样本量；完整筛 / 门槛5 共用）----
MC_BEAT_SMALL = 0.70
MC_BEAT_LARGE = 0.90
MC_SMALL_N_BOUNDARY = 20


def pipeline_order_zh():
    """返回创造管道固定三步顺序（含入口标题）。"""
    return list(CREATION_PIPELINE_ORDER_ZH)


def win_rate_above_floor(win_rate, exclusive_floor=None):
    """第二步：胜率是否严格大于硬底。win_rate 为 0~1 比例。"""
    floor = float(MIN_WIN_RATE_EXCLUSIVE if exclusive_floor is None else exclusive_floor)
    try:
        wr = float(win_rate)
    except Exception:
        return False
    return wr > floor


def gate2_floors(n_trades):
    """第二步：适应度地板（原门槛2）— 按成交笔数返回卡尔玛 / 盈亏比。

    期望因子、去最大盈利、最大不利偏移不在此放松。
    """
    n = int(n_trades or 0)
    small = n < int(GATE2_SMALL_N_BOUNDARY)
    return {
        "n": n,
        "small_sample": small,
        "calmar_min": float(GATE2_CALMAR_SMALL if small else GATE2_CALMAR_LARGE),
        "payoff_min": float(GATE2_PAYOFF_SMALL if small else GATE2_PAYOFF_LARGE),
        "expectancy_factor_min": float(GATE2_EXPECTANCY_FACTOR_MIN),
        "所属步骤": "第二步：门槛",
        "label_zh": (
            "第二步·适应度小样本地板（卡尔玛≥%.1f，盈亏比≥%.1f）" % (
                GATE2_CALMAR_SMALL, GATE2_PAYOFF_SMALL)
            if small else
            "第二步·适应度标准地板（卡尔玛≥%.1f，盈亏比≥%.1f）" % (
                GATE2_CALMAR_LARGE, GATE2_PAYOFF_LARGE)
        ),
    }


def gate2_account_returns_ok(returns, trades=None):
    """Cheap assembly floor: apply gate2_floors to an account return vector."""
    from . import fitness_engine as fe

    vals = [float(x) for x in (returns or []) if x is not None]
    floors = gate2_floors(len(vals))
    if len(vals) < int(MIN_TRADES_CREATION):
        return {
            "ok": False,
            "reasons": ["成交笔数不足"],
            "floors": floors,
            "calmar": None,
            "payoff_ratio": None,
            "expectancy_factor": None,
        }
    calmar, _meta = fe._calmar(vals, trades or [])
    pay = fe.payoff_stats(vals)
    payoff = float(pay.get("payoff_ratio") or 0.0)
    exp_f = float(pay.get("expectancy_factor_wr_x_payoff") or 0.0)
    reasons = []
    if calmar < float(floors["calmar_min"]):
        reasons.append("卡尔玛低于地板")
    if payoff < float(floors["payoff_min"]):
        reasons.append("盈亏比低于地板")
    if exp_f < float(floors["expectancy_factor_min"]):
        reasons.append("期望因子低于地板")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "floors": floors,
        "calmar": calmar,
        "payoff_ratio": payoff,
        "expectancy_factor": exp_f,
        "n": len(vals),
    }


def mc_beat_threshold(n_trades):
    """第二步：蒙特卡洛击败率下限（完整筛 / 摩擦稳健共用）。"""
    n = int(n_trades or 0)
    if n > 0 and n < int(MC_SMALL_N_BOUNDARY):
        return float(MC_BEAT_SMALL)
    return float(MC_BEAT_LARGE)


def wf_ok_small_n(fold_positive, folds, trades, min_trades=None):
    """第二步：小样本前向稳健（快速筛与前向窗口判定共用）。

    通过条件：成交笔数达标，且
      （正折/可用折 ≥ 0.6）或（正折数 ≥ 5）。
    """
    min_n = int(MIN_TRADES_CREATION if min_trades is None else min_trades)
    try:
        n = int(trades or 0)
        avail = int(folds or 0)
        fp = int(fold_positive or 0)
    except Exception:
        return False, {
            "pass": False,
            "reason": "invalid_inputs",
            "所属步骤": "第二步：门槛",
            "label_zh": "前向稳健：输入无效",
        }
    if n < min_n or avail <= 0:
        return False, {
            "pass": False,
            "trades": n,
            "folds": avail,
            "fold_positive": fp,
            "ratio": None,
            "reason": "insufficient_trades_or_folds",
            "所属步骤": "第二步：门槛",
            "label_zh": "前向稳健未过：成交笔数或可用折不足",
            "min_trades": min_n,
        }
    ratio = fp / float(avail)
    ok = bool(
        ratio >= float(WF_POS_RATIO)
        or fp >= int(WF_POS_MIN_AVAILABLE)
    )
    return ok, {
        "pass": ok,
        "trades": n,
        "folds": avail,
        "fold_positive": fp,
        "ratio": round(ratio, 4),
        "gate_ratio": float(WF_POS_RATIO),
        "gate_min_available_pos": int(WF_POS_MIN_AVAILABLE),
        "legacy_abs_pos": int(WF_LARGE_NEED_PASS),
        "legacy_abs_folds": int(WF_LARGE_NEED_TOTAL),
        "reason": None if ok else "small_n_forward_ratio_fail",
        "所属步骤": "第二步：门槛",
        "label_zh": (
            "前向稳健通过（小样本比例门：正折/可用折=%.2f）" % ratio
            if ok else
            "前向稳健未过（小样本比例门：正折/可用折=%.2f，需≥%.2f 或正折≥%d）"
            % (ratio, float(WF_POS_RATIO), int(WF_POS_MIN_AVAILABLE))
        ),
    }


def wf_pass_for_sample(n_trades, pass_count, total_windows, available_folds=None):
    """第二步：按样本量选择前向通过规则。"""
    n = int(n_trades or 0)
    fp = int(pass_count or 0)
    total = int(total_windows or 0)
    avail = int(available_folds if available_folds is not None else total)
    if n < int(SMALL_N_TRADE_BOUNDARY):
        ok, detail = wf_ok_small_n(fp, avail, n)
        detail = dict(detail)
        detail["gate_mode"] = "small_n_ratio"
        detail["label_zh_mode"] = "第二步·小样本前向比例门"
        return ok, detail
    ok = bool(total >= int(WF_LARGE_NEED_TOTAL) and fp >= int(WF_LARGE_NEED_PASS))
    return ok, {
        "pass": ok,
        "trades": n,
        "folds": total,
        "fold_positive": fp,
        "gate_mode": "large_n_absolute",
        "requirement": "%s/%s" % (WF_LARGE_NEED_PASS, WF_LARGE_NEED_TOTAL),
        "所属步骤": "第二步：门槛",
        "label_zh": (
            "前向稳健通过（标准：%d/%d）" % (fp, total)
            if ok else
            "前向稳健未过（标准需至少 %d/%d，当前 %d/%d）"
            % (WF_LARGE_NEED_PASS, WF_LARGE_NEED_TOTAL, fp, total)
        ),
        "label_zh_mode": "第二步·大样本绝对窗数门",
    }


def doctrine_summary_zh():
    """给人看的教义摘要：先顺序，再硬底。"""
    return {
        "创造管道顺序": list(CREATION_PIPELINE_ORDER_ZH),
        "第一步": "研究发现：委员会 + 探针 + 多重检验",
        "第二步": (
            "门槛统一："
            "胜率严格大于50% + 去最大盈利后不崩 + 漏斗L0-L3 + 门槛0至7"
            " + 寒霜贰/crec（快速筛=小样本前向比例门；完整筛=蒙特卡洛与摩擦）"
        ),
        "第三步": "过了门槛的策略交予复核模块（四阶段复核）",
        "胜率硬底": "第二步内：创造交接要求胜率严格大于 50%，禁止仅靠平均净收益过关",
        "成交笔数": "第二步内：创造侧最低 %d 笔" % MIN_TRADES_CREATION,
        "前向稳健": (
            "第二步内：成交<%d 用比例门（≥%.0f%% 或正折≥%d）；"
            "成交≥%d 仍要求 %d/%d"
            % (
                SMALL_N_TRADE_BOUNDARY, WF_POS_RATIO * 100, WF_POS_MIN_AVAILABLE,
                SMALL_N_TRADE_BOUNDARY, WF_LARGE_NEED_PASS, WF_LARGE_NEED_TOTAL,
            )
        ),
        "适应度地板": (
            "第二步内：成交<%d 卡尔玛≥%.1f、盈亏比≥%.1f；"
            "成交≥%d 卡尔玛≥%.1f、盈亏比≥%.1f；期望因子与去最大盈利始终硬"
            % (
                GATE2_SMALL_N_BOUNDARY, GATE2_CALMAR_SMALL, GATE2_PAYOFF_SMALL,
                GATE2_SMALL_N_BOUNDARY, GATE2_CALMAR_LARGE, GATE2_PAYOFF_LARGE,
            )
        ),
        "蒙特卡洛": (
            "第二步内：成交<%d 击败率≥%.0f%%；成交≥%d 击败率≥%.0f%%"
            % (
                MC_SMALL_N_BOUNDARY, MC_BEAT_SMALL * 100,
                MC_SMALL_N_BOUNDARY, MC_BEAT_LARGE * 100,
            )
        ),
        "复核之后": "四阶段复核 + 人工确认；生产理论胜率≥65% 不在创造三步里降低",
    }
