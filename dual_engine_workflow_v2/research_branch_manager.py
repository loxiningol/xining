# -*- coding: utf-8 -*-
"""Research branch manager — mechanism trees + coverage-aware family closure.

A leaf probe never closes a family.  Only aggregate coverage of available
(OHLCV-proxy) branches may emit MECHANISM_CONTRADICTED / FAMILY_EXHAUSTED, and
never when required high-information data is still missing.
"""
from __future__ import print_function

from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# Exhaustion recovery tree (report §七)
EXHAUSTION_TREE = {
    "tree_id": "exhaustion_recovery",
    "title_zh": "衰竭回收机制树",
    "branches": {
        "A_forced_liquidation": {
            "title_zh": "强制平仓衰竭",
            "mechanism_ids": [
                "panic_exhaustion_recovery_001",
                "liquidation_flow_exhaustion_direct_002",
                "forced_liquidation_bounce_001",
            ],
            # Strong liquidation claim needs L2 derivatives; proxy path cells remain L0.
            "requires_micro_data": True,
            "requires_data": ["liquidation_flow", "open_interest"],
            "proxy_mechanism_ids": [
                "panic_exhaustion_recovery_001",
                "exhaustion_absorption_reclaim_002",
            ],
            "strong_claim": "liquidation_exhaustion",
        },
        "B_liquidity_vacuum": {
            "title_zh": "流动性真空后恢复",
            "mechanism_ids": ["liquidity_vacuum_recovery_direct_001"],
            "requires_micro_data": True,
            "requires_data": ["level2_order_book_snapshot", "trade_side_flow_snapshot"],
        },
        "C_absorption_reclaim": {
            "title_zh": "信息冲击后价格接受失败 / 吸收收回",
            "mechanism_ids": ["exhaustion_absorption_reclaim_002"],
            "requires_micro_data": False,
        },
        "D_cross_market": {
            "title_zh": "跨市场非同步冲击",
            "mechanism_ids": ["correlation_breakdown_001"],
            "requires_micro_data": True,
            "requires_data": ["cross_exchange_basis"],
        },
        "E_false_exhaustion_redteam": {
            "title_zh": "假衰竭反方",
            "mechanism_ids": ["trend_pullback_continuation_001"],
            "requires_micro_data": False,
            "is_redteam": True,
        },
    },
}

# Vol squeeze → expansion tree (report §七)
SQUEEZE_TREE = {
    "tree_id": "vol_squeeze_expansion",
    "title_zh": "波动率收缩扩张机制树",
    "branches": {
        "T1_volatility_only": {
            "title_zh": "只预测未来实现波动",
            "mechanism_ids": ["squeeze_volatility_release_002", "vol_squeeze_expansion_001"],
            "requires_micro_data": False,
            "task": "volatility_only",
        },
        "T2_direction": {
            "title_zh": "预测突破方向",
            "mechanism_ids": ["vol_squeeze_break_001", "squeeze_break_acceptance_002"],
            "requires_micro_data": False,
            "task": "direction",
        },
        "T3_continuation_or_fade": {
            "title_zh": "预测突破后延续或回归",
            "mechanism_ids": ["squeeze_fake_break_reversion_001", "failed_breakout_trap_001"],
            "requires_micro_data": False,
            "task": "continuation_or_fade",
        },
    },
}

TREES = (EXHAUSTION_TREE, SQUEEZE_TREE)

# Fine-grained failure codes (report §漏洞六)
FAILURE_CODE_ZH = {
    "gross_edge_absent": "毛优势不存在",
    "gross_edge_unstable": "毛优势不稳定",
    "horizon_mismatch": "持有期错配",
    "event_dilution": "事件定义过宽导致稀释",
    "signal_redundancy": "连续信号重复计数风险已抑制",
    "execution_taker_only": "仅 taker–taker 映射不成立",
    "execution_mapping_failure": "执行映射无法覆盖成本",
    "spread_dominated": "点差/摩擦情景吞噬优势",
    "adverse_selection": "入场后短期逆向选择（MAE 主导）",
    "state_conditional_only": "仅特定波动状态有效",
    "direction_unresolved": "能预测波动扩张但不能预测方向",
    "proxy_failure": "代理变量未测到目标机制",
    "data_insufficient": "数据维度不足",
    "sample_insufficient": "独立事件不足",
    "mechanism_contradicted": "机制预测被充分覆盖后反证",
    "family_exhausted": "机制空间已充分覆盖且整体无效",
    "near_miss": "接近门槛需诊断升级",
}


def trees_for_brief(brief=""):
    text = str(brief or "").lower()
    out = []
    if any(k in text for k in ("衰竭", "回收", "exhaust", "panic", "清算", "超跌")):
        out.append(EXHAUSTION_TREE)
    if any(k in text for k in ("收缩", "扩张", "squeeze", "压缩", "波动率", "突破")):
        out.append(SQUEEZE_TREE)
    return out


def mechanism_branch_map():
    """mechanism_id → (tree_id, branch_id)."""
    out = {}
    for tree in TREES:
        for bid, branch in (tree.get("branches") or {}).items():
            for mid in branch.get("mechanism_ids") or []:
                out[mid] = (tree["tree_id"], bid)
    return out


def annotate_hypothesis(hypothesis, brief=""):
    """Attach tree/branch metadata when known."""
    h = dict(hypothesis or {})
    mid = h.get("mechanism_id")
    mapping = mechanism_branch_map()
    if mid in mapping:
        tree_id, branch_id = mapping[mid]
        h["mechanism_tree_id"] = tree_id
        h["mechanism_branch_id"] = branch_id
        for tree in TREES:
            if tree["tree_id"] == tree_id:
                br = (tree.get("branches") or {}).get(branch_id) or {}
                h["mechanism_branch_zh"] = br.get("title_zh")
                h["requires_micro_data"] = bool(br.get("requires_micro_data"))
                h["branch_task"] = br.get("task")
                break
    elif trees_for_brief(brief):
        # unmarked hypothesis under an active tree brief — still track as free leaf
        h.setdefault("mechanism_tree_id", trees_for_brief(brief)[0]["tree_id"])
        h.setdefault("mechanism_branch_id", "unassigned_leaf")
    return h


def preferred_mechanism_ids(brief="", max_n=16):
    ids = []
    for tree in trees_for_brief(brief):
        for branch in (tree.get("branches") or {}).values():
            for mid in branch.get("mechanism_ids") or []:
                if mid not in ids:
                    ids.append(mid)
    return ids[: int(max_n)]


def evaluate_family_closures(failure_lineage, coverage, contract=None, brief=""):
    """Aggregate-only family closure. Never closes on missing micro-data branches."""
    lineage = list(failure_lineage or [])
    cov = coverage or {}
    contract = contract or {}
    missing_high = list(cov.get("missing_high_information_data") or [])
    available = set(contract.get("available_data") or ["ohlcv", "derived_ohlcv_proxy"])
    book_ok = bool(available & {
        "level2_order_book", "level2_order_book_snapshot",
        "trade_side_flow", "trade_side_flow_snapshot",
        "microstructure_forward_samples",
    })
    derivatives_ok = bool(available & {
        "open_interest", "liquidation_flow", "historical_funding",
    })
    # Full micro_ok only when derivatives feeds exist; book snapshots alone are partial.
    micro_ok = derivatives_ok and book_ok and not missing_high
    score = float(cov.get("score") or 0.0)
    closures = []
    branch_reports = []

    def _branch_data_ready(branch):
        req = list(branch.get("requires_data") or [])
        if not req:
            return not bool(branch.get("requires_micro_data")) or book_ok
        for item in req:
            if item in ("liquidation_flow", "open_interest", "historical_funding"):
                if item not in available:
                    return False
            elif item in (
                "level2_order_book", "level2_order_book_snapshot",
                "trade_side_flow", "trade_side_flow_snapshot",
            ):
                if not book_ok:
                    return False
        return True

    active_trees = trees_for_brief(brief) or list(TREES)
    for tree in active_trees:
        tree_id = tree["tree_id"]
        branches = tree.get("branches") or {}
        branch_states = {}
        for bid, branch in branches.items():
            mids = set(branch.get("mechanism_ids") or [])
            rows = [r for r in lineage if r.get("mechanism_id") in mids]
            states = [r.get("research_state") for r in rows if r.get("research_state")]
            tested = len(states)
            contradicted = sum(
                1 for s in states if s in (
                    "NO_DIRECTIONAL_EFFECT", "MECHANISM_CONTRADICTED",
                )
            )
            proxy_or_data = sum(
                1 for s in states if s in ("PROXY_INADEQUATE", "DATA_INADEQUATE")
            )
            near = sum(
                1 for s in states if s in (
                    "NEAR_MISS_DIAGNOSTIC", "DIRECTIONAL_BUT_SMALL",
                    "VOLATILITY_EFFECT_ONLY", "EXECUTION_MAPPING_FAILURE",
                    "STATE_CONDITIONAL", "HORIZON_MISMATCH",
                )
            )
            data_ready = _branch_data_ready(branch)
            # Evidence ladder: strong-claim block ≠ stop all research on the branch.
            try:
                from . import data_evidence_ladder as _ladder
                pol = _ladder.branch_research_policy(branch, available, None)
            except Exception:
                pol = {"status": "open" if data_ready else "data_blocked"}
            ladder_status = pol.get("status")
            if tested == 0 and ladder_status == "proxy_open":
                status = "proxy_open_untested"
            elif tested == 0:
                status = "untested"
            elif ladder_status == "proxy_open" and not data_ready:
                # Strong claim blocked, but proxy leaves were tested / open.
                if near > 0:
                    status = "proxy_open_near_miss"
                elif contradicted >= max(1, int(0.7 * max(tested, 1))):
                    status = "proxy_branch_contradicted"
                else:
                    status = "proxy_open"
            elif not data_ready and ladder_status != "proxy_open":
                status = "data_blocked"
            elif proxy_or_data and not any(
                s not in ("PROXY_INADEQUATE", "DATA_INADEQUATE", "SAMPLE_INADEQUATE")
                for s in states
            ):
                status = "proxy_or_data_blocked"
            elif near > 0:
                status = "open_near_miss"
            elif tested >= 2 and contradicted >= max(2, int(0.7 * tested)):
                status = "branch_contradicted"
            else:
                status = "open"
            branch_states[bid] = {
                "status": status,
                "tested": tested,
                "contradicted": contradicted,
                "near_miss_like": near,
                "proxy_or_data": proxy_or_data,
                "requires_micro_data": bool(branch.get("requires_micro_data")),
                "data_ready": data_ready,
                "ladder_status": ladder_status,
                "title_zh": branch.get("title_zh"),
                "note_zh": pol.get("note_zh"),
            }
            branch_reports.append({
                "tree_id": tree_id, "branch_id": bid, **branch_states[bid],
            })

        evaluable = [
            b for b, st in branch_states.items()
            if st["status"] not in ("untested", "data_blocked", "proxy_or_data_blocked")
        ]
        contradicted_branches = [
            b for b in evaluable if branch_states[b]["status"] == "branch_contradicted"
        ]
        openish = [
            b for b in evaluable
            if branch_states[b]["status"] in ("open", "open_near_miss")
        ]
        # FAMILY_EXHAUSTED only when every evaluable non-micro branch contradicted,
        # coverage score high, and no near-miss left — never when micro branches blocked.
        micro_blocked = any(
            st["status"] == "data_blocked" for st in branch_states.values()
        )
        can_exhaust = (
            score >= 0.72
            and evaluable
            and not openish
            and len(contradicted_branches) >= max(2, len(evaluable))
            and not micro_blocked
            and bool(cov.get("family_exhaustion_allowed"))
        )
        if can_exhaust:
            closures.append({
                "tree_id": tree_id,
                "research_state": "FAMILY_EXHAUSTED",
                "research_state_zh": "机制族在充分覆盖后耗尽",
                "failure_codes": ["family_exhausted"],
                "branches": branch_states,
                "at": _now(),
            })
        elif (
            score >= 0.55
            and contradicted_branches
            and not openish
            and not micro_blocked
        ):
            closures.append({
                "tree_id": tree_id,
                "research_state": "MECHANISM_CONTRADICTED",
                "research_state_zh": "机制在充分覆盖后被重复证伪",
                "failure_codes": ["mechanism_contradicted"],
                "branches": branch_states,
                "note_zh": "可评价分支已被重复证伪；微数据分支仍可能未测。",
                "at": _now(),
            })

    return {
        "ok": True,
        "n_closed": len([c for c in closures if c.get("research_state") == "FAMILY_EXHAUSTED"]),
        "closures": closures,
        "branch_reports": branch_reports,
        "micro_data_available": micro_ok,
        "rule_zh": (
            "只有充分机制空间覆盖且可评价分支被独立证据重复证伪，才可关闭机制族；"
            "缺清算/OI/历史L2 时仅阻断强声明，不得终止仍可做 OHLCV/前向微观代理研究的家族；"
            "禁止 FAMILY_EXHAUSTED。"
        ),
        "at": _now(),
    }


def probe():
    return {
        "ok": True,
        "provider": "research_branch_manager_v1",
        "trees": [t["tree_id"] for t in TREES],
        "failure_codes": FAILURE_CODE_ZH,
        "at": _now(),
    }
