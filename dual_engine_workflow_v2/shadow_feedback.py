# -*- coding: utf-8 -*-
"""Shadow / Canary feedback + four-gap diagnosis (lite).

Fast feedback may update execution scorecards only.
Core mechanism beliefs require medium/slow timescale + independent events.

Champion–Challenger compare rules are recorded; auto-replace is forbidden here.
"""
from __future__ import print_function

import json
import os
from datetime import datetime
from pathlib import Path

from . import generator_scorecard as scorecard
from . import research_ledger as ledger


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def feedback_dir():
    d = _root() / "auto_trade" / "dual_engine" / "shadow_feedback"
    d.mkdir(parents=True, exist_ok=True)
    return d


def compute_gaps(probe_expected=None, oos_result=None, shadow=None, live=None, small=None, target=None):
    """Four gaps bridging research → live without collapsing to PnL."""
    def _f(x, default=None):
        if x is None:
            return default
        try:
            return float(x)
        except Exception:
            return default

    research_gap = None
    if probe_expected is not None and oos_result is not None:
        research_gap = _f(probe_expected) - _f(oos_result)

    simulation_gap = None
    if oos_result is not None and shadow is not None:
        simulation_gap = _f(oos_result) - _f(shadow)

    execution_gap = None
    if shadow is not None and live is not None:
        execution_gap = _f(shadow) - _f(live)

    scaling_gap = None
    if small is not None and target is not None:
        scaling_gap = _f(small) - _f(target)

    routing = {
        "research_gap": ["mechanism", "proxy", "statistical_search"],
        "simulation_gap": ["backtester", "market_generator", "fill_assumptions"],
        "execution_gap": ["order_model", "latency", "impact"],
        "scaling_gap": ["capacity_model", "participation", "sizing"],
    }
    return {
        "ok": True,
        "schema": "qiyu_feedback_gaps_v1",
        "research_gap": research_gap,
        "simulation_gap": simulation_gap,
        "execution_gap": execution_gap,
        "scaling_gap": scaling_gap,
        "routing": routing,
        "at": _now(),
    }


def ingest_shadow_status(run_id=None):
    """Best-effort read of legacy shadow status without mutating live strategies."""
    path = _root() / "auto_trade" / "legacy_shadow_status.json"
    if not path.exists():
        return {
            "ok": True,
            "available": False,
            "note_zh": "无影子状态文件；Shadow层待有候选时记录理论成交。",
            "at": _now(),
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc), "at": _now()}
    out = {
        "ok": True,
        "available": True,
        "n_rows": len(data) if isinstance(data, list) else 1,
        "sample": data[:3] if isinstance(data, list) else data,
        "timescale": "fast",
        "may_update_mechanism_core": False,
        "at": _now(),
    }
    # Fast feedback: only bump execution_engineer cost bias proxy if failures present
    if isinstance(data, dict) and str(data.get("state") or "").startswith("shadow_failed"):
        scorecard.record_outcome(
            "execution_engineer",
            {
                "generator": "execution_engineer",
                "fail_stage": "execution",
                "family": None,
                "responsibility": {"execution_failure": 0.6},
                "reward_vector": {"execution_accuracy": 0.0, "overfitting_risk": 0.2},
            },
            contract_compare=None,
            novelty=0.0,
        )
    if run_id:
        ledger.append_event({
            "event_type": "shadow_feedback_ingest",
            "available": True,
            "may_update_mechanism_core": False,
        }, run_id=run_id)
    # persist snapshot
    snap = feedback_dir() / ("shadow_ingest_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    snap.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return out


def register_champion_challenger_rules(champion_id, challenger_id, rules=None):
    """Record compare rules; does NOT auto-replace champion."""
    rules = rules or {
        "require_risk_adjusted_net_edge": True,
        "require_multi_state_incremental_value": True,
        "forbid_replace_on_short_window_pnl": True,
        "min_independent_events": 20,
    }
    row = {
        "schema": "qiyu_champion_challenger_v1",
        "champion_id": champion_id,
        "challenger_id": challenger_id,
        "rules": rules,
        "auto_replace": False,
        "at": _now(),
        "note_zh": "仅登记比较规则；禁止因短期收益自动替换 Champion。",
    }
    path = feedback_dir() / ("cc_%s_%s.json" % (
        str(champion_id or "na")[:40], str(challenger_id or "na")[:40]
    ))
    path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    return row


def probe():
    return {
        "ok": True,
        "provider": "shadow_feedback_lite_v1",
        "layers": ["shadow", "canary", "champion_challenger"],
        "auto_replace": False,
        "at": _now(),
    }
