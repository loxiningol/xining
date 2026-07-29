#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit checks for auto_driver (no network / no STEP A)."""
from __future__ import print_function

import json
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from auto_driver import metrics, patch_apply, limits, report
from auto_driver.driver import run_driver


def test_patch_and_metrics():
    pack = {
        "mechanism_spec": {"mechanism_family": "demo_fam", "mechanism_id": "mech_demo"},
        "dsl": {
            "key": "k", "direction": "long", "timeframe": "15m",
            "supported_instruments": ["ETH-USDT-SWAP"],
            "entry": {"all": [{"id": "a", "left": {"feature": "close"}, "op": "gt", "right": {"value": 1}}]},
            "exit": {"any": [{"id": "x", "exit_op": "atr_trailing", "n_atr": 5.0, "atr_period": 14}]},
            "max_hold_bars": 48,
        },
    }
    pack["dsl_long"] = json.loads(json.dumps(pack["dsl"]))
    new, applied, errors = patch_apply.apply_patches(pack, [
        {"op": "set", "path": "dsl.exit.any[0].n_atr", "value": 4.0},
        {"op": "set_max_hold_bars", "value": 36},
        {"op": "rename_family", "mechanism_family": "demo_fam_v2"},
    ], direction="long")
    assert not errors, errors
    assert new["dsl"]["exit"]["any"][0]["n_atr"] == 4.0
    assert new["dsl_long"]["max_hold_bars"] == 36
    assert new["mechanism_spec"]["mechanism_family"] == "demo_fam_v2"
    assert applied

    fake = {
        "ok": False,
        "reason": "repair_exhausted_or_drift",
        "gate_results": {"gates": [{
            "gate_id": "gate2_base_backtest",
            "pass": False,
            "evidence": {
                "sample_size": 13,
                "fitness": {
                    "pass": False,
                    "failed_checks": ["payoff_ge_2_5", "worst5_loss_share_le_40pct"],
                    "payoff_ratio": 2.48,
                    "calmar": 26.0,
                    "worst5_loss_share": 0.89,
                    "expectancy_factor": 1.1,
                    "thresholds": {"payoff_min": 2.5, "calmar_min": 1.5, "worst5_loss_share_max": 0.4, "expectancy_factor_min": 1.0},
                },
            },
        }]},
        "phase3_funnel": {"l1_micro_screen": {"pass": True, "reject_reasons": [], "metrics": {"filled_entries": 8}}},
    }
    ctx = metrics.build_failure_context(fake, new, 1, "ETH-USDT-SWAP", "15m", "long")
    assert ctx["metric_gaps"]["payoff_ratio"] > 0
    assert ctx["composite_score"] > 0

    st = {"iterations": [
        {"composite_score": 1.0, "total_gap": 2.0, "pipeline_reason": "x"},
        {"composite_score": 1.01, "total_gap": 1.99, "pipeline_reason": "x"},
        {"composite_score": 1.015, "total_gap": 1.98, "pipeline_reason": "x"},
        {"composite_score": 1.016, "total_gap": 1.97, "pipeline_reason": "x"},
    ], "success": False}
    stop, code, _ = limits.detect_limits(st, {"max_iterations": 10, "convergence_eps": 0.02, "convergence_patience": 3})
    assert stop and code == "CONVERGED_NO_IMPROVEMENT", (stop, code)
    print("test_patch_and_metrics OK")


def test_dry_run_driver():
    pack = {
        "ok": True,
        "mechanism_spec": {
            "mechanism_id": "mech_demo",
            "mechanism_name": "demo",
            "mechanism_family": "demo_fam",
            "market_inefficiency": "x",
            "counterparty_source": "y",
            "why_edge_exists": "z",
            "edge_decay_conditions": "d",
            "required_market_regime": "r",
            "entry_logic": "e",
            "exit_logic": "x",
            "stop_logic": "s",
            "take_profit_logic": "t",
            "invalidation_logic": "i",
            "non_negotiable_rules": ["no_fixed_pct_take_profit"],
            "tunable_parameters": ["vol_z20_min"],
            "forbidden_transformations": [],
            "expected_trade_frequency_class": "selective",
            "expected_holding_period": "1d",
            "suitable_symbols": ["ETH-USDT-SWAP"],
            "suitable_timeframes": ["15m"],
        },
        "dsl": {
            "schema": "qiyu_strategy_dsl_v1",
            "key": "k", "name": "n", "direction": "long", "timeframe": "15m",
            "supported_instruments": ["ETH-USDT-SWAP"],
            "entry": {"all": [{"id": "a", "left": {"feature": "close"}, "op": "gt", "right": {"value": 0}}]},
            "exit": {"any": [{"id": "x", "exit_op": "atr_trailing", "n_atr": 5.0, "atr_period": 14, "role": "take_profit"}]},
            "max_hold_bars": 48,
        },
    }
    pack["dsl_long"] = json.loads(json.dumps(pack["dsl"]))
    with tempfile.TemporaryDirectory() as td:
        pack_path = Path(td) / "pack.json"
        pack_path.write_text(json.dumps(pack), encoding="utf-8")
        cfg = {
            "pack_path": str(pack_path),
            "symbol": "ETH-USDT-SWAP",
            "timeframe": "15m",
            "direction": "long",
            "workdir": str(Path(td) / "run"),
            "vector_root": str(Path(td)),
            "dry_run": True,
            "dry_run_skip_ai": True,
            "max_iterations": 2,
            "report_copy_to": str(Path(td) / "delivery_report.md"),
            "l1_seed_retries": 1,
        }
        out = run_driver(cfg)
        assert out["final_status"] == "LIMIT_REACHED_FAILED"
        assert Path(out["report_path"]).exists()
        assert Path(cfg["report_copy_to"]).exists()
        text = Path(out["report_path"]).read_text(encoding="utf-8")
        assert "终态总结" in text
        assert "迭代轨迹表" in text
        print("test_dry_run_driver OK", out["stop_code"])


if __name__ == "__main__":
    test_patch_and_metrics()
    test_dry_run_driver()
    print("ALL_OK")
