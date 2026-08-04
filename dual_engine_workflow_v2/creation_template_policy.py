# -*- coding: utf-8 -*-
"""Strategy-independent creation and admission policy.

ADA5 is a calculation/regression fixture only.  It never lowers a threshold,
forces a pass, or grants a strategy-specific soft gate.
"""
from __future__ import print_function

GOLDEN_KEY = "codex0725t3_ada5m_trendpb_r42_z2p3_h14"
GOLDEN_TITLE = "ADA5顺势回升"
EVAL_LOOKBACK_DAYS = 730
FORWARD_HORIZON_DAYS = 730
DEFAULT_STRATEGY_VALIDITY_DAYS = 730

POLICY_ZH = (
    "创造、门禁、复核和部署采用统一策略无关规则；ADA5顺势回升仅用于"
    "验证频率算法与回归兼容，不享有任何软放行。"
)


def policy_snapshot():
    return {
        "schema": "qiyu_template_policy_v2",
        "calculation_fixture_key": GOLDEN_KEY,
        "calculation_fixture_title": GOLDEN_TITLE,
        "eval_lookback_days": EVAL_LOOKBACK_DAYS,
        "forward_horizon_days": FORWARD_HORIZON_DAYS,
        "default_strategy_validity_days": DEFAULT_STRATEGY_VALIDITY_DAYS,
        "policy_zh": POLICY_ZH,
        "yields_to_golden": False,
        "strategy_specific_bypass": False,
        "fixture_role": "calculation_and_regression_only",
    }
