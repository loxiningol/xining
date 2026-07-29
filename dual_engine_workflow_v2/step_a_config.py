# -*- coding: utf-8 -*-
"""STEP A constants — strategy production & training refactor (not STEP B)."""
from __future__ import print_function

from pathlib import Path

from .config import ROOT, AUTO_DIR, DUAL_DIR, WF_DIR, ARTIFACTS_DIR, _now, _atomic

STEP_A_CODE_VERSION = "step_a_strategy_creation_20260729_phase3_funnel"
STEP_A_SCHEMA = "qiyu_step_a_v1"

MECHANISM_SPEC_DIR = WF_DIR / "mechanism_specs"
FIDELITY_DIFF_DIR = WF_DIR / "fidelity_diffs"
FAILURE_RECORD_DIR = WF_DIR / "failure_records"
FAILURE_KB_PATH = WF_DIR / "failure_knowledgebase.json"
GATES_DIR = WF_DIR / "gate_runs"
SPLIT_TESTS_DIR = WF_DIR / "split_tests_20"

# STEP A mechanism_spec required fields (§二)
MECHANISM_SPEC_FIELDS = (
    "mechanism_id",
    "mechanism_name",
    "mechanism_family",
    "market_inefficiency",
    "counterparty_source",
    "why_edge_exists",
    "edge_decay_conditions",
    "required_market_regime",
    "entry_logic",
    "exit_logic",
    "stop_logic",
    "take_profit_logic",
    "invalidation_logic",
    "non_negotiable_rules",
    "tunable_parameters",
    "forbidden_transformations",
    "expected_trade_frequency_class",
    "expected_holding_period",
    "suitable_symbols",
    "suitable_timeframes",
)

# Map STEP A spec → legacy v2 mechanism_statement fields
SPEC_TO_STATEMENT = {
    "forced_actor": "counterparty_source",
    "observable_behavior": "market_inefficiency",
    "predictable_distortion": "why_edge_exists",
    "persistence_reason": "why_edge_exists",
    "counterparty": "counterparty_source",
    "activation_regime": "required_market_regime",
    "invalidation_regime": "edge_decay_conditions",
    "causal_entry": "entry_logic",
    "causal_exit": "exit_logic",
    "forbidden_substitutions": "forbidden_transformations",
}

# Alias recovery for GLM representation_failure
FIELD_ALIASES = {
    "mechanism_id": ["mechanism_id", "id", "mech_id"],
    "mechanism_name": ["mechanism_name", "name", "title"],
    "mechanism_family": ["mechanism_family", "family", "family_name"],
    "market_inefficiency": ["market_inefficiency", "inefficiency", "observable_behavior", "distortion"],
    "counterparty_source": ["counterparty_source", "counterparty", "forced_actor", "loser"],
    "why_edge_exists": ["why_edge_exists", "persistence_reason", "predictable_distortion", "edge_reason"],
    "edge_decay_conditions": ["edge_decay_conditions", "invalidation_regime", "decay", "invalidation_logic"],
    "required_market_regime": ["required_market_regime", "activation_regime", "regime"],
    "entry_logic": ["entry_logic", "causal_entry", "entry"],
    "exit_logic": ["exit_logic", "causal_exit", "exit"],
    "stop_logic": ["stop_logic", "stop", "sl_logic"],
    "take_profit_logic": ["take_profit_logic", "tp_logic", "take_profit"],
    "invalidation_logic": ["invalidation_logic", "invalidation_regime", "kill_switch"],
    "non_negotiable_rules": ["non_negotiable_rules", "immutable", "locked_rules"],
    "tunable_parameters": ["tunable_parameters", "allowed_params", "tunables"],
    "forbidden_transformations": ["forbidden_transformations", "forbidden_substitutions", "forbidden"],
    "expected_trade_frequency_class": ["expected_trade_frequency_class", "frequency_class", "target_frequency"],
    "expected_holding_period": ["expected_holding_period", "holding_horizon", "hold_period"],
    "suitable_symbols": ["suitable_symbols", "symbols", "symbol"],
    "suitable_timeframes": ["suitable_timeframes", "timeframes", "timeframe"],
}

# STEP A fingerprint fields (§四)
STEP_A_FINGERPRINT_FIELDS = (
    "directionality",
    "regime_dependency",
    "signal_origin",
    "entry_trigger_type",
    "exit_trigger_type",
    "mean_reversion_vs_trend",
    "volatility_dependency",
    "liquidity_dependency",
    "holding_period",
    "symbol_dependency",
    "timeframe_dependency",
    "counterparty_type",
)

DUPLICATE_SIMILARITY_THRESHOLD = 0.72
EXHAUSTION_FADE_FAMILY = "exhaustion_fade_short"

# 20 split destruction tests (§五)
SPLIT_TEST_IDS = (
    "future_leak",
    "label_leak",
    "param_sensitivity",
    "session_sensitivity",
    "symbol_transfer",
    "regime_stratification",
    "extreme_market",
    "random_signal_control",
    "signal_delay",
    "entry_price_perturb",
    "exit_price_perturb",
    "fee_multiply",
    "slip_multiply",
    "mc_trade_order",
    "remove_outlier_win",
    "remove_top10_wins",
    "consecutive_loss_stress",
    "mechanism_counterexample",
    "version_drift",
    "execution_chain_integrity",
)

SPLIT_TEST_NAMES_ZH = {
    "future_leak": "未来数据污染测试",
    "label_leak": "标签泄漏测试",
    "param_sensitivity": "参数敏感性测试",
    "session_sensitivity": "时段敏感性测试",
    "symbol_transfer": "标的迁移测试",
    "regime_stratification": "市场状态分层测试",
    "extreme_market": "极端行情测试",
    "random_signal_control": "随机信号对照测试",
    "signal_delay": "信号延迟测试",
    "entry_price_perturb": "入场价格扰动测试",
    "exit_price_perturb": "出场价格扰动测试",
    "fee_multiply": "手续费倍增测试",
    "slip_multiply": "滑点倍增测试",
    "mc_trade_order": "蒙特卡洛交易顺序测试",
    "remove_outlier_win": "单次异常盈利移除测试",
    "remove_top10_wins": "前十大盈利交易移除测试",
    "consecutive_loss_stress": "连续亏损压力测试",
    "mechanism_counterexample": "机制反例测试",
    "version_drift": "策略版本漂移测试",
    "execution_chain_integrity": "执行链完整性测试",
}

FAILURE_CLASSES = (
    "mechanism_failure",
    "implementation_failure",
    "data_failure",
    "cost_failure",
    "inconclusive",
)

GATE_IDS = (
    "gate0_mechanism_integrity",
    "gate1_code_fidelity",
    "gate2_base_backtest",
    "gate3_walk_forward",
    "gate4_split_destruction",
    "gate5_mc_friction",
    "gate6_multi_ai_review",
    "gate7_human_confirm",
)

# Exploration modes STEP A naming (also accept A/B/C/D)
STEP_A_MODES = {
    "1": "known_mechanism_deep_dig",
    "2": "new_mechanism",
    "3": "combination_mechanism",
    "4": "failure_reverse_research",
    "A": "new_mechanism",          # Mode A ≈ new mechanism + isolation
    "B": "known_mechanism_deep_dig",
    "C": "combination_mechanism",
    "D": "failure_reverse_research",
}

REPAIR_ROUND_TYPES = (
    "engineering_only",
    "tunable_params_only",
    "mechanism_viability_verdict",
)

WF_WINDOW_PASS_REQUIREMENT = (7, 10)  # ≥7/10
MC_ORDER_ACCEPT_PCT = 0.90

PRODUCTION_CONSTRAINTS = {
    "leverage": 20,
    "stop_loss_pct": 0.009,
    "initial_position_pct": 0.30,
    "ada_sl_migration_allowed": False,
    "no_force_open": True,
    "no_simulated_fills_as_production": True,
    "ai_wr_not_live_ready": True,
}


def ensure_step_a_dirs():
    # Resolve paths from live module globals so tests can redirect without /root.
    import dual_engine_workflow_v2.step_a_config as self_mod
    for name in (
        "MECHANISM_SPEC_DIR",
        "FIDELITY_DIFF_DIR",
        "FAILURE_RECORD_DIR",
        "GATES_DIR",
        "SPLIT_TESTS_DIR",
    ):
        p = getattr(self_mod, name)
        Path(p).mkdir(parents=True, exist_ok=True)
    try:
        Path(getattr(self_mod, "ARTIFACTS_DIR", ARTIFACTS_DIR)).mkdir(parents=True, exist_ok=True)
    except OSError:
        # Local macOS sandbox may not allow /root; artifacts optional for unit tests.
        pass
