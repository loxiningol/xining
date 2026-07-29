# -*- coding: utf-8 -*-
"""4D formal review: DeepSeek causal / Qwen game / backtest integrity / GLM friction.

OpenAI slot maps to GLM in this deployment — dimension 3 uses a local independent
backtest-integrity auditor and records executor explicitly (no forged OpenAI calls).
WR≥75% remains auxiliary only and cannot override fatal issues.
"""
from __future__ import print_function

import json

from .config import AUX_WR_MEAN_GATE, LEVERAGE, STOP_LOSS_PCT, INITIAL_POSITION_PCT


def _issue_bucket(parsed, prefix):
    """Normalize model output into fatal/major/minor/pass."""
    parsed = parsed or {}
    fatal = list(parsed.get("%s_fatal_issue" % prefix) or parsed.get("fatal_issue") or [])
    major = list(parsed.get("%s_major_issue" % prefix) or parsed.get("major_issue") or [])
    minor = list(parsed.get("%s_minor_issue" % prefix) or parsed.get("minor_issue") or [])
    if isinstance(fatal, str):
        fatal = [fatal] if fatal else []
    if isinstance(major, str):
        major = [major] if major else []
    if isinstance(minor, str):
        minor = [minor] if minor else []
    # explicit pass flag
    passed = bool(parsed.get("%s_pass" % prefix) or parsed.get("pass"))
    if fatal:
        passed = False
    elif not major and not fatal and ("%s_pass" % prefix) not in parsed and "pass" not in parsed:
        # if model omitted pass but no issues → soft pass
        passed = True
    return {
        "%s_fatal_issue" % prefix: fatal,
        "%s_major_issue" % prefix: major,
        "%s_minor_issue" % prefix: minor,
        "%s_pass" % prefix: passed,
        "evidence": parsed.get("evidence") or parsed.get("falsifiable_conditions") or parsed.get("repro") or [],
        "raw": parsed,
    }


def local_backtest_integrity_audit(definition, packs, book):
    """Dimension 3 executor: local independent engine (NOT OpenAI — unavailable as separate provider)."""
    fatal, major, minor = [], [], []
    evidence = []
    base = (packs or {}).get("base_metrics") or {}
    # Sample size
    if int(base.get("trades") or 0) < 8:
        major.append("statistical_power_low:trades<%s" % base.get("trades"))
    # Lookahead heuristics: exit before entry keys
    dsl = definition or {}
    blob = json.dumps(dsl, ensure_ascii=False).lower()
    if "future_" in blob or "next_bar" in blob or "lookahead" in blob:
        fatal.append("possible_lookahead_token_in_dsl")
    # Bar-unrealizable: same-bar entry+exit without hold
    if int(dsl.get("max_hold_bars") or 0) == 0:
        major.append("max_hold_bars_zero_same_bar_risk")
    # Fee / slip presence in packs
    fr = (packs or {}).get("extreme_friction") or {}
    if not fr:
        major.append("missing_friction_pack")
    elif fr.get("pass") is False:
        minor.append("friction_pack_failed_soft")
    # Train/test contamination proxy: no oos
    if float(base.get("oos_profit") or 0) == 0 and int(base.get("folds") or 0) < 5:
        major.append("weak_oos_or_folds")
    # Multiple testing: repair rounds high
    if int(book.get("repair_round") or 0) >= 3:
        minor.append("multiple_testing_after_repairs")
    # Timestamp/timezone cannot be proven here
    unresolved = ["timezone_alignment_not_rechecked_in_local_auditor"]
    passed = not fatal and not major
    evidence.append({"executor": "local_backtest_integrity_v1", "openai_used": False,
                     "reason": "openai_provider_slot_maps_to_glm_in_auto_trade_ai_consensus"})
    return {
        "backtest_fatal_issue": fatal,
        "backtest_major_issue": major,
        "backtest_minor_issue": minor,
        "backtest_pass": passed,
        "evidence": evidence,
        "unresolved": unresolved,
        "executor": "local_backtest_integrity_v1",
        "openai_forged": False,
    }


def glm_execution_friction_audit(book, packs, ai_json_fn=None):
    """Dimension 4: GLM execution friction under 20x / 0.9% SL / 30% position."""
    fr = (packs or {}).get("extreme_friction") or {}
    fr_m = fr.get("metrics") or {}
    payload = {
        "constraints": {
            "leverage": LEVERAGE,
            "stop_loss_pct": STOP_LOSS_PCT,
            "initial_position_pct": INITIAL_POSITION_PCT,
            "no_martingale": True,
            "no_grid": True,
            "no_add_on": True,
        },
        "friction_metrics": fr_m,
        "base_metrics": (packs or {}).get("base_metrics"),
        "symbol": book.get("symbol"),
        "thesis": book.get("thesis"),
        "mechanism_statement": book.get("mechanism_statement"),
    }
    prompt = (
        "你是GLM-5.2执行摩擦审计官。审查Maker/Taker、滑点、点差、延迟、限流、部分成交、"
        "止损偏差、盘口深度、资金费率、标记价偏差，以及20x+0.9%SL+30%仓位生存性。"
        "仅输出JSON：{\"execution_fatal_issue\":[],\"execution_major_issue\":[],"
        "\"execution_minor_issue\":[],\"execution_pass\":true|false,"
        "\"net_metrics_after_friction\":{},\"evidence\":[]}"
    )
    parsed = {}
    if ai_json_fn:
        try:
            res = ai_json_fn("glm", prompt, payload, max_tokens=1200, temperature=0.1)
            parsed = (res or {}).get("parsed") or {}
        except Exception as exc:
            parsed = {"execution_fatal_issue": [], "execution_major_issue": ["glm_friction_call_failed:%s" % exc],
                      "execution_pass": False}
    # Machine overlay: friction pack fail → at least major
    out = _issue_bucket(parsed, "execution")
    if fr and fr.get("pass") is False:
        out["execution_major_issue"] = list(out.get("execution_major_issue") or []) + [
            "machine_extreme_friction_failed"
        ]
        out["execution_pass"] = False if out.get("execution_fatal_issue") else out.get("execution_pass")
        if out["execution_major_issue"]:
            out["execution_pass"] = False
    # Survival under hard SL
    if float(fr_m.get("sharpe") or -1) < 0 and int(fr_m.get("trades") or 0) >= 5:
        out["execution_major_issue"] = list(out.get("execution_major_issue") or []) + [
            "friction_sharpe_negative_under_stress"
        ]
        out["execution_pass"] = False
    out["net_metrics_after_friction"] = parsed.get("net_metrics_after_friction") or fr_m
    out["constraints_checked"] = payload["constraints"]
    return out


def deepseek_causal_attack(book, packs, definition, ai_json_fn):
    prompt = (
        "你是DeepSeek因果机制攻击官。审查被迫参与者、偏差方向性、相关vs因果、对手方非理性假设、"
        "删除机制叙述后是否只剩普通技术形态。禁止只给胜率。"
        "仅输出JSON：{\"causal_fatal_issue\":[],\"causal_major_issue\":[],\"causal_minor_issue\":[],"
        "\"causal_pass\":true|false,\"falsifiable_conditions\":[],\"evidence\":[]}"
    )
    payload = {
        "mechanism_statement": book.get("mechanism_statement"),
        "fingerprint": book.get("mechanism_fingerprint"),
        "dsl": {"entry": (definition or {}).get("entry"), "exit": (definition or {}).get("exit")},
        "base_metrics": (packs or {}).get("base_metrics"),
        "necessity": (packs or {}).get("abc", {}).get("necessity") if packs else None,
    }
    parsed = {}
    try:
        res = ai_json_fn("deepseek", prompt, payload, max_tokens=1400, temperature=0.15)
        parsed = (res or {}).get("parsed") or {}
        if not (res or {}).get("ok"):
            parsed.setdefault("causal_major_issue", [])
            parsed["causal_major_issue"] = list(parsed.get("causal_major_issue") or []) + [
                "deepseek_call_failed:%s" % ((res or {}).get("error") or "unknown")
            ]
            parsed["causal_pass"] = False
    except Exception as exc:
        parsed = {"causal_major_issue": ["deepseek_exception:%s" % exc], "causal_pass": False}
    return _issue_bucket(parsed, "causal")


def qwen_game_theory_attack(book, packs, definition, ai_json_fn):
    prompt = (
        "你是Qwen博弈稳定性攻击官。审查对手适应、拥挤失效、状态切换反向、策略互抢、容量、公开化后是否成立。"
        "禁止只给胜率。仅输出JSON：{\"game_theory_fatal_issue\":[],\"game_theory_major_issue\":[],"
        "\"game_theory_minor_issue\":[],\"game_theory_pass\":true|false,"
        "\"fragile_scenarios\":[],\"failure_paths\":[],\"evidence\":[]}"
    )
    payload = {
        "mechanism_statement": book.get("mechanism_statement"),
        "symbol": book.get("symbol"),
        "timeframe": book.get("timeframe"),
        "base_metrics": (packs or {}).get("base_metrics"),
        "coverage_risk": book.get("coverage_risk"),
    }
    parsed = {}
    try:
        res = ai_json_fn("qwen", prompt, payload, max_tokens=1400, temperature=0.15)
        parsed = (res or {}).get("parsed") or {}
        if not (res or {}).get("ok"):
            parsed.setdefault("game_theory_major_issue", [])
            parsed["game_theory_major_issue"] = list(parsed.get("game_theory_major_issue") or []) + [
                "qwen_call_failed:%s" % ((res or {}).get("error") or "unknown")
            ]
            parsed["game_theory_pass"] = False
    except Exception as exc:
        parsed = {"game_theory_major_issue": ["qwen_exception:%s" % exc], "game_theory_pass": False}
    out = _issue_bucket(parsed, "game_theory")
    out["fragile_scenarios"] = (parsed or {}).get("fragile_scenarios") or []
    out["failure_paths"] = (parsed or {}).get("failure_paths") or []
    return out


def auxiliary_wr_check(ai_review_or_sim):
    """Optional WR≥75% mean — auxiliary only."""
    try:
        mean = float(ai_review_or_sim.get("ai_theoretical_wr_avg")
                     or ai_review_or_sim.get("wr_mean") or 0)
    except Exception:
        mean = 0.0
    return {
        "auxiliary_only": True,
        "gate": AUX_WR_MEAN_GATE,
        "mean": mean,
        "pass": mean >= AUX_WR_MEAN_GATE,
        "cannot_override_fatal": True,
    }


def run_multidimensional_review(book, packs, definition, ai_json_fn, aux_wr=None):
    causal = deepseek_causal_attack(book, packs, definition, ai_json_fn)
    game = qwen_game_theory_attack(book, packs, definition, ai_json_fn)
    backtest = local_backtest_integrity_audit(definition, packs, book)
    execution = glm_execution_friction_audit(book, packs, ai_json_fn=ai_json_fn)

    fatal_blocks = []
    if not causal.get("causal_pass") and causal.get("causal_fatal_issue"):
        fatal_blocks.append("causal")
    if not game.get("game_theory_pass") and game.get("game_theory_fatal_issue"):
        fatal_blocks.append("game_theory")
    if not backtest.get("backtest_pass") and backtest.get("backtest_fatal_issue"):
        fatal_blocks.append("backtest")
    if not execution.get("execution_pass") and execution.get("execution_fatal_issue"):
        fatal_blocks.append("execution")

    # Also treat missing pass with fatal list
    for name, row, key in (
        ("causal", causal, "causal_fatal_issue"),
        ("game_theory", game, "game_theory_fatal_issue"),
        ("backtest", backtest, "backtest_fatal_issue"),
        ("execution", execution, "execution_fatal_issue"),
    ):
        if row.get(key):
            if name not in fatal_blocks:
                fatal_blocks.append(name)

    major_pending = []
    for name, row, key, pkey in (
        ("causal", causal, "causal_major_issue", "causal_pass"),
        ("game_theory", game, "game_theory_major_issue", "game_theory_pass"),
        ("backtest", backtest, "backtest_major_issue", "backtest_pass"),
        ("execution", execution, "execution_major_issue", "execution_pass"),
    ):
        if row.get(key) and not row.get(pkey):
            major_pending.append(name)

    dims_pass = (
        bool(causal.get("causal_pass"))
        and bool(game.get("game_theory_pass"))
        and bool(backtest.get("backtest_pass"))
        and bool(execution.get("execution_pass"))
    )
    aux = auxiliary_wr_check(aux_wr or {})
    # Approved only if all 4 pass AND no fatals; aux WR does not override fatals
    approved = dims_pass and not fatal_blocks
    if fatal_blocks:
        approved = False

    return {
        "schema": "qiyu_multidimensional_review_v1",
        "approved": approved,
        "dims_pass": dims_pass,
        "fatal_blocks": fatal_blocks,
        "major_pending": major_pending,
        "causal": causal,
        "game_theory": game,
        "backtest_integrity": backtest,
        "execution_friction": execution,
        "auxiliary_wr": aux,
        "policy": "fatal_any_dimension_blocks; aux_wr_cannot_override; no_model_may_override_another_fatal",
    }
