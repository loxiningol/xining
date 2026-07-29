# -*- coding: utf-8 -*-
"""Split scores — AI WR never equals live readiness."""
from __future__ import print_function

from .config import _now
from .step_a_config import STEP_A_SCHEMA, STEP_A_CODE_VERSION


def build_split_scores(*, ai_logic_wr=None, ai_n=None,
                       backtest_wr=None, backtest_n=None,
                       walk_forward_wr=None, walk_forward_n=None,
                       oos_wr=None, oos_n=None,
                       sim_exec_wr=None, sim_exec_n=None,
                       live_wr=None, live_n=None,
                       calibrated_wr=None,
                       cost_expectancy=None,
                       mechanism_credibility=None,
                       execution_credibility=None,
                       data_credibility=None,
                       overall_credibility=None):
    def pack(wr, n, label):
        return {
            "label": label,
            "win_rate_pct": wr,
            "sample_size": n,
            "insufficient_sample": (n is None) or (int(n) < 20 if n is not None else True),
        }

    scores = {
        "schema": STEP_A_SCHEMA,
        "artifact": "split_scores",
        "code_version": STEP_A_CODE_VERSION,
        "created_at": _now(),
        "ai_logic_wr": pack(ai_logic_wr, ai_n, "AI逻辑评估胜率"),
        "backtest_wr": pack(backtest_wr, backtest_n, "回测胜率"),
        "walk_forward_wr": pack(walk_forward_wr, walk_forward_n, "Walk-forward胜率"),
        "oos_wr": pack(oos_wr, oos_n, "样本外胜率"),
        "sim_exec_wr": pack(sim_exec_wr, sim_exec_n, "模拟执行胜率"),
        "live_wr": pack(live_wr, live_n if live_n is not None else 0, "实盘胜率"),
        "calibrated_expected_wr": {"label": "校准预期胜率", "win_rate_pct": calibrated_wr, "sample_size": None},
        "cost_after_expectancy": {"label": "成本后单笔期望", "value": cost_expectancy, "sample_size": backtest_n},
        "mechanism_credibility": mechanism_credibility,
        "execution_credibility": execution_credibility,
        "data_credibility": data_credibility,
        "overall_credibility": overall_credibility,
        "policy": {
            "ai_wr_ge_75_not_live_ready": True,
            "live_ready_requires_gate7_human_confirm": True,
            "scores_must_show_sample_size": True,
        },
    }
    # Explicit non-equivalence
    ai = scores["ai_logic_wr"].get("win_rate_pct")
    scores["live_ready"] = False
    scores["live_ready_reason"] = "AI≥75% ≠ live-ready; Gate7 human confirm required"
    if ai is not None and float(ai) >= 75:
        scores["warning"] = "ai_wr_ge_75_but_not_live_ready"
    return scores
