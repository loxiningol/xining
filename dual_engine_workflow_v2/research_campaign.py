# -*- coding: utf-8 -*-
"""Lean research campaign — map → cheap stream → diagnostic → exec tier.

Separates:
  - candidate count vs resident memory
  - phenomenon research vs full backtest
  - mechanism evidence vs data ladder level
  - historical discovery vs forward micro calibration
"""
from __future__ import print_function

import json
import os
from datetime import datetime
from pathlib import Path

from . import cheap_probe_stream as cheap
from . import data_evidence_ladder as ladder
from . import probe_protocol as probes
from . import research_branch_manager as branch_mgr
from .process_safe_state import atomic_write_json


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def new_campaign_id(prefix="campaign"):
    return "%s_%s" % (prefix, datetime.now().strftime("%Y%m%d_%H%M%S"))


def campaign_dir(campaign_id):
    d = _root() / "auto_trade" / "dual_engine" / "research_campaigns" / str(campaign_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_lean_campaign(
    brief,
    symbol,
    timeframe,
    factor_matrix,
    candles,
    fwd_returns=None,
    available_data=None,
    micro_meta=None,
    campaign_id=None,
    max_cells=96,
    max_cheap_probes=160,
    max_diagnostic=10,
    max_exec_tier=5,
    batch_size=6,
    progress_cb=None,
):
    """Execute lean stages. Full strategy assembly is NOT done here."""
    campaign_id = campaign_id or new_campaign_id("lean")
    available_data = list(available_data or ["ohlcv_swap_candles", "derived_factors"])
    micro_meta = micro_meta or {}
    have_level = ladder.available_level(available_data, micro_meta)

    # Stage 1: mechanism map (structured, almost free)
    cells = cheap.build_mechanism_cells(brief, max_cells=max_cells)
    # annotate claim modes
    for cell in cells:
        if cell.get("mechanism_id"):
            ev = ladder.evaluate_mechanism_claim(
                cell.get("mechanism_id"), available_data, micro_meta,
            )
            cell["claim_mode"] = ev.get("mode")
            cell["may_research"] = ev.get("may_research")
            cell["claim_wording_zh"] = ev.get("wording_zh")
        else:
            cell["may_research"] = True
            cell["claim_mode"] = "proxy_claim"

    map_path = campaign_dir(campaign_id) / "mechanism_map.json"
    atomic_write_json(map_path, {
        "campaign_id": campaign_id,
        "n_cells": len(cells),
        "evidence_level": have_level,
        "cells": cells,
        "policy_zh": ladder.contract_policy_zh(),
        "at": _now(),
    })

    # Branch policies with degradation (not binary family death)
    branch_policies = {}
    for tree in branch_mgr.trees_for_brief(brief) or list(branch_mgr.TREES):
        for bid, br in (tree.get("branches") or {}).items():
            branch_policies["%s:%s" % (tree["tree_id"], bid)] = ladder.branch_research_policy(
                br, available_data, micro_meta,
            )

    # The public discovery API historically allowed callers to provide an
    # already aligned forward-return vector without candle rows.  The cheap
    # campaign needs multiple real horizons and therefore cannot honestly
    # reconstruct those horizons from one vector.  Skip only this optional
    # tier, with an explicit audit record; the full probe path below still
    # consumes the caller's fwd_returns.
    if not candles:
        result = {
            "ok": True,
            "schema": "qiyu_lean_research_campaign_v1",
            "campaign_id": campaign_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "evidence_level": have_level,
            "evidence_level_zh": ladder.LEVELS.get(have_level, {}).get("title_zh"),
            "skipped": True,
            "skip_reason": "candles_required_for_multi_horizon_cheap_probe",
            "fwd_returns_preserved_for_full_probe": fwd_returns is not None,
            "stages": {
                "map": {
                    "n_cells": len(cells),
                    "path": str(map_path),
                    "researchable_cells": sum(
                        1 for c in cells if c.get("may_research") is not False
                    ),
                },
                "cheap_stream": {
                    "ok": True,
                    "skipped": True,
                    "skip_reason": "candles_required_for_multi_horizon_cheap_probe",
                    "n_streamed_this_call": 0,
                    "state_counts": {},
                    "promote": [],
                    "architecture_zh": "无K线时不伪造多周期前瞻收益；完整探针继续使用调用方对齐收益。",
                },
                "diagnostic": {"n": 0, "rows": []},
                "execution_tier_shortlist": [],
            },
            "branch_policies": branch_policies,
            "limits_zh": {
                "cannot": ["无K线时重建多周期前瞻收益"],
                "can": ["保留机制地图", "由完整探针消费调用方对齐收益"],
            },
            "policy_zh": ladder.contract_policy_zh(),
            "at": _now(),
        }
        atomic_write_json(campaign_dir(campaign_id) / "campaign_result.json", result)
        return result

    # Stage 2: streaming cheap probes
    stream = cheap.stream_cheap_probes(
        [c for c in cells if c.get("may_research") is not False],
        factor_matrix,
        candles,
        campaign_id=campaign_id,
        timeframe=timeframe,
        batch_size=batch_size,
        max_probes=max_cheap_probes,
        available_data=available_data,
        micro_meta=micro_meta,
        progress_cb=progress_cb,
    )

    # Stage 3: near-miss diagnostic — promote top cheap survivors into probe_protocol
    promote = list(stream.get("promote") or [])[: int(max_diagnostic)]
    diagnostic_rows = []
    for row in promote:
        hyp = {
            "hypothesis_id": "H_diag_%s" % (row.get("cell_id") or row.get("probe_id")),
            "mechanism_id": row.get("mechanism_id"),
            "family": row.get("family"),
            "path": "theory_to_data",
            "factor_hints": [row.get("factor")] if row.get("factor") else [],
            "observable_proxy": [row.get("factor")] if row.get("factor") else [],
            "predicted_direction": row.get("active_claim") or row.get("cell_id"),
            "horizon": "%sbars" % (row.get("horizon_bars") or 3),
            "required_data": ["derived_ohlcv_proxy"],
            "completeness": {"passed": True},
            "micro_data_available": have_level >= 1,
            "claim_mode": row.get("claim_mode"),
            "claim_wording_zh": row.get("claim_wording_zh"),
        }
        hyp = ladder.annotate_hypothesis(hyp, available_data, micro_meta)
        hyp = branch_mgr.annotate_hypothesis(hyp, brief)
        pr = probes.probe_hypothesis(
            hyp, factor_matrix, fwd_returns, candles=candles,
            symbol=symbol, timeframe=timeframe, max_trials=8,
        )
        diagnostic_rows.append({
            "cell_id": row.get("cell_id"),
            "probe_id": row.get("probe_id"),
            "cheap_state": row.get("failure_state"),
            "diag_state": pr.get("research_state"),
            "failure_codes": pr.get("failure_codes"),
            "n_probes": pr.get("n_probes"),
            "best_net": ((pr.get("best") or {}).get("mean_net")),
            "independent_events": ((pr.get("best") or {}).get("n_independent_events")),
            "claim_mode": hyp.get("claim_mode"),
            # Keep the structured candidate so the execution shortlist can be
            # promoted into the full discovery population.  Heavy returns stay
            # out of this summary; the full probe is re-run under all gates.
            "hypothesis": hyp,
            "probe_best": {
                k: v for k, v in ((pr.get("best") or {}).items())
                if k != "trade_returns"
            },
        })

    # Stage 4: execution tier — only CHEAP_PASS / EXECUTION_MAPPING near-misses
    exec_candidates = [
        r for r in diagnostic_rows
        if r.get("diag_state") in (
            "EXECUTION_MAPPING_FAILURE", "DIRECTIONAL_BUT_SMALL",
            "NEAR_MISS_DIAGNOSTIC", "READY_FOR_ASSEMBLY", "STATE_CONDITIONAL",
        )
    ][: int(max_exec_tier)]

    result = {
        "ok": True,
        "schema": "qiyu_lean_research_campaign_v1",
        "campaign_id": campaign_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "evidence_level": have_level,
        "evidence_level_zh": ladder.LEVELS.get(have_level, {}).get("title_zh"),
        "stages": {
            "map": {
                "n_cells": len(cells),
                "path": str(map_path),
                "researchable_cells": sum(1 for c in cells if c.get("may_research") is not False),
            },
            "cheap_stream": stream,
            "diagnostic": {
                "n": len(diagnostic_rows),
                "rows": diagnostic_rows,
            },
            "execution_tier_shortlist": exec_candidates,
        },
        "branch_policies": branch_policies,
        "limits_zh": {
            "cannot": [
                "大规模并行完整回测物化",
                "完整历史 L2 级验证",
                "对清算/OI 机制作强因果声明（无数据时）",
                "数百候选同时精细策略回测",
            ],
            "can": [
                "20+ 结构化现象/机制格子",
                "100+ 流式廉价探针摘要",
                "机制空间覆盖与近缘诊断",
                "OHLCV 代理机制 + 前向盘口校准",
                "断点续跑 / 不影响实盘的低优先级调度",
            ],
        },
        "policy_zh": ladder.contract_policy_zh(),
        "at": _now(),
    }
    atomic_write_json(campaign_dir(campaign_id) / "campaign_result.json", result)
    return result


def probe():
    return {
        "ok": True,
        "provider": "research_campaign_lean_v1",
        "stages": ["map", "cheap_stream", "diagnostic", "execution_tier"],
        "at": _now(),
    }
