# -*- coding: utf-8 -*-
"""Data evidence ladder — claim strength vs available data.

Level 0: OHLCV path / volume / range proxies
Level 1: forward book/flow snapshots (spread, depth, aggression)
Level 2: OI / funding / liquidation
Level 3: historical L2 replay / queue position

Missing Level-2/3 data blocks *strong claims* only.  It must not terminate a
whole strategy family that still has Level-0/1 researchable questions.
"""
from __future__ import print_function

from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


LEVELS = {
    0: {
        "id": "L0_ohlcv",
        "title_zh": "OHLCV 路径/成交量代理",
        "dims": ["ohlcv_swap_candles", "derived_factors", "derived_ohlcv_proxy"],
    },
    1: {
        "id": "L1_forward_micro",
        "title_zh": "前向盘口/主动成交摘要",
        "dims": [
            "level2_order_book_snapshot", "trade_side_flow_snapshot",
            "microstructure_forward_samples", "half_spread_rate",
        ],
    },
    2: {
        "id": "L2_derivatives",
        "title_zh": "OI / funding / 清算",
        "dims": ["open_interest", "liquidation_flow", "historical_funding"],
    },
    3: {
        "id": "L3_l2_replay",
        "title_zh": "历史 L2 回放 / 队列",
        "dims": ["historical_l2_replay", "queue_position", "cancel_flow"],
    },
}


# Strong-claim labels that require a minimum ladder level.
CLAIM_MIN_LEVEL = {
    "liquidation_exhaustion": 2,
    "forced_flow_reset": 2,
    "oi_flush_bounce": 2,
    "funding_crowding": 2,
    "book_vacuum_replenish": 1,
    "adverse_selection_seconds": 1,
    "maker_fill_probability": 1,
    "queue_position_edge": 3,
    "selling_pressure_decay_proxy": 0,
    "price_path_exhaustion": 0,
    "vol_squeeze_expansion": 0,
    "break_acceptance_path": 0,
    "failed_break_reversion": 0,
}


MECHANISM_CLAIMS = {
    "liquidation_flow_exhaustion_direct_002": {
        "strong_claim": "liquidation_exhaustion",
        "proxy_claim": "selling_pressure_decay_proxy",
        "proxy_wording_zh": "与卖压衰减一致（非清算结束证明）",
    },
    "forced_liquidation_bounce_001": {
        "strong_claim": "forced_flow_reset",
        "proxy_claim": "selling_pressure_decay_proxy",
        "proxy_wording_zh": "与极端卖压后路径恢复一致（非强制平仓证明）",
    },
    "panic_exhaustion_recovery_001": {
        "strong_claim": "price_path_exhaustion",
        "proxy_claim": "price_path_exhaustion",
        "proxy_wording_zh": "价格路径衰竭代理",
    },
    "exhaustion_absorption_reclaim_002": {
        "strong_claim": "selling_pressure_decay_proxy",
        "proxy_claim": "selling_pressure_decay_proxy",
        "proxy_wording_zh": "冲击后吸收/收回代理",
    },
    "liquidity_vacuum_recovery_direct_001": {
        "strong_claim": "book_vacuum_replenish",
        "proxy_claim": "price_path_exhaustion",
        "proxy_wording_zh": "无盘口时退化为路径真空代理",
    },
    "correlation_breakdown_001": {
        "strong_claim": "price_path_exhaustion",
        "proxy_claim": "price_path_exhaustion",
        "proxy_wording_zh": "跨资产不确认的 OHLCV 代理（非跨所基差）",
    },
    "squeeze_volatility_release_002": {
        "strong_claim": "vol_squeeze_expansion",
        "proxy_claim": "vol_squeeze_expansion",
        "proxy_wording_zh": "收缩后绝对波动扩张",
    },
    "vol_squeeze_expansion_001": {
        "strong_claim": "vol_squeeze_expansion",
        "proxy_claim": "vol_squeeze_expansion",
        "proxy_wording_zh": "收缩后波动扩张",
    },
    "vol_squeeze_break_001": {
        "strong_claim": "break_acceptance_path",
        "proxy_claim": "break_acceptance_path",
        "proxy_wording_zh": "突破方向路径（非盘口推动证明）",
    },
    "squeeze_break_acceptance_002": {
        "strong_claim": "break_acceptance_path",
        "proxy_claim": "break_acceptance_path",
        "proxy_wording_zh": "突破后接受/延续路径",
    },
    "squeeze_fake_break_reversion_001": {
        "strong_claim": "failed_break_reversion",
        "proxy_claim": "failed_break_reversion",
        "proxy_wording_zh": "假突破回归路径",
    },
    "failed_breakout_trap_001": {
        "strong_claim": "failed_break_reversion",
        "proxy_claim": "failed_break_reversion",
        "proxy_wording_zh": "突破失败陷阱路径",
    },
    "donchian_break_continuation_001": {
        "strong_claim": "break_acceptance_path",
        "proxy_claim": "break_acceptance_path",
        "proxy_wording_zh": "唐奇安通道有效突破后的路径延续（OHLCV）",
    },
    "donchian_false_break_reclaim_001": {
        "strong_claim": "failed_break_reversion",
        "proxy_claim": "failed_break_reversion",
        "proxy_wording_zh": "唐奇安假突破收回路径",
    },
    "donchian_htf_filter_break_001": {
        "strong_claim": "break_acceptance_path",
        "proxy_claim": "break_acceptance_path",
        "proxy_wording_zh": "高周期过滤下的顺势通道突破",
    },
    "asia_range_break_001": {
        "strong_claim": "break_acceptance_path",
        "proxy_claim": "break_acceptance_path",
        "proxy_wording_zh": "会话区间突破延续路径",
    },
}


def available_level(available_data=None, micro_meta=None):
    """Highest ladder level currently usable."""
    avail = set(available_data or [])
    micro_meta = micro_meta or {}
    level = 0
    if avail & set(LEVELS[1]["dims"]) or float(micro_meta.get("coverage_ratio") or 0) >= 0.15:
        level = 1
    if avail & set(LEVELS[2]["dims"]):
        level = 2
    if avail & set(LEVELS[3]["dims"]):
        level = 3
    return level


def claim_status(claim_key, available_data=None, micro_meta=None):
    need = int(CLAIM_MIN_LEVEL.get(claim_key, 0))
    have = available_level(available_data, micro_meta)
    if have >= need:
        return {
            "claim": claim_key,
            "status": "researchable",
            "need_level": need,
            "have_level": have,
            "block_code": None,
        }
    block = {
        1: "DATA_BLOCKED_MICRO_SNAPSHOT",
        2: "DATA_BLOCKED_DERIVATIVES",
        3: "DATA_BLOCKED_L2_REPLAY",
    }.get(need, "DATA_INADEQUATE")
    if claim_key in ("liquidation_exhaustion", "forced_flow_reset", "oi_flush_bounce"):
        block = "DATA_BLOCKED_LIQUIDATION"
    return {
        "claim": claim_key,
        "status": "strong_claim_blocked",
        "need_level": need,
        "have_level": have,
        "block_code": block,
    }


def evaluate_mechanism_claim(mechanism_id, available_data=None, micro_meta=None):
    """Return strong/proxy researchability for one mechanism leaf."""
    mid = str(mechanism_id or "")
    pack = MECHANISM_CLAIMS.get(mid) or {
        "strong_claim": "price_path_exhaustion",
        "proxy_claim": "price_path_exhaustion",
        "proxy_wording_zh": "默认 OHLCV 代理声明",
    }
    strong = claim_status(pack["strong_claim"], available_data, micro_meta)
    proxy = claim_status(pack["proxy_claim"], available_data, micro_meta)
    # Research continues on proxy when strong claim is blocked.
    if strong["status"] == "researchable":
        mode = "strong_claim"
        active = strong
    elif proxy["status"] == "researchable":
        mode = "proxy_claim"
        active = proxy
    else:
        mode = "fully_blocked"
        active = strong
    return {
        "mechanism_id": mid,
        "mode": mode,
        "active_claim": active.get("claim"),
        "block_code": None if mode != "fully_blocked" else active.get("block_code"),
        "strong": strong,
        "proxy": proxy,
        "wording_zh": pack.get("proxy_wording_zh") if mode == "proxy_claim" else None,
        "may_research": mode != "fully_blocked",
        "family_termination_forbidden": True,
        "at": _now(),
    }


def annotate_hypothesis(hypothesis, available_data=None, micro_meta=None):
    h = dict(hypothesis or {})
    ev = evaluate_mechanism_claim(h.get("mechanism_id"), available_data, micro_meta)
    h["evidence_level"] = available_level(available_data, micro_meta)
    h["claim_mode"] = ev.get("mode")
    h["active_claim"] = ev.get("active_claim")
    h["claim_block_code"] = ev.get("block_code")
    h["claim_wording_zh"] = ev.get("wording_zh")
    h["may_research"] = bool(ev.get("may_research"))
    if ev.get("mode") == "proxy_claim":
        h["required_data"] = ["derived_ohlcv_proxy"]
        h["strong_claim_blocked"] = True
    return h


def branch_research_policy(branch, available_data=None, micro_meta=None):
    """Per-branch: strong blocked ≠ family/branch research stopped."""
    branch = branch or {}
    mids = list(branch.get("mechanism_ids") or [])
    rows = [evaluate_mechanism_claim(m, available_data, micro_meta) for m in mids]
    researchable = [r for r in rows if r.get("may_research")]
    strong_blocked = [r for r in rows if (r.get("strong") or {}).get("status") == "strong_claim_blocked"]
    if researchable:
        status = "proxy_open" if strong_blocked else "open"
    elif any((r.get("strong") or {}).get("status") == "strong_claim_blocked" for r in rows):
        status = "strong_claim_blocked_proxy_absent"
    else:
        status = "untested"
    return {
        "status": status,
        "researchable_n": len(researchable),
        "strong_blocked_n": len(strong_blocked),
        "note_zh": (
            "强声明因数据不足被阻断，但代理/路径问题仍可研究；禁止终止整个家族。"
            if strong_blocked and researchable else
            "强声明与代理均不可研究。" if status == "strong_claim_blocked_proxy_absent" else
            "分支可在当前证据等级研究。"
        ),
        "mechanisms": rows,
    }


def contract_policy_zh():
    return (
        "证据降级阶梯：缺清算/OI/历史L2 时仅阻断对应强声明（DATA_BLOCKED_*），"
        "OHLCV 代理与前向微观校准必须继续；不得把强声明阻断写成整个策略家族死亡。"
        "当前架构不能安全地一次性并行物化 100–500 个完整回测，"
        "但可通过流式廉价探针覆盖约 100–500 个机制格子摘要。"
    )


def probe():
    return {
        "ok": True,
        "provider": "data_evidence_ladder_v1",
        "levels": LEVELS,
        "claims": sorted(CLAIM_MIN_LEVEL.keys()),
        "at": _now(),
    }
