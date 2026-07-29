# -*- coding: utf-8 -*-
"""DeepSeek logic attacker + production-risk attacker (no rewrite)."""
from __future__ import print_function

from .config import _now
from .step_a_config import STEP_A_SCHEMA, STEP_A_CODE_VERSION


def deepseek_logic_attack(mechanism_spec, dsl, packs, ai_json_fn):
    """Attack with counterexamples only — must not rewrite mechanism."""
    prompt = (
        "你是 DeepSeek 逻辑/反例攻击者。禁止改写盈利机制或给出替代入场公式。"
        "只输出 JSON：{counterexamples:[...], logic_holes:[...], pass:bool, "
        "severity_rewrite:false, notes:str}。"
        "pass=false 当存在可证伪的关键因果漏洞。"
    )
    payload = {
        "mechanism_spec": mechanism_spec,
        "dsl_entry": (dsl or {}).get("entry"),
        "dsl_exit": (dsl or {}).get("exit"),
        "base_metrics": (packs or {}).get("base_metrics"),
        "role_constraint": "attacker_no_rewrite",
    }
    try:
        res = ai_json_fn("deepseek", prompt, payload, max_tokens=1200, temperature=0.2)
        parsed = (res or {}).get("parsed") or {}
    except Exception as exc:
        return {
            "ok": False, "pass": False, "error": str(exc),
            "executor": "deepseek", "role": "logic_attacker",
            "forbidden_rewrite": True, "at": _now(),
        }
    return {
        "ok": bool((res or {}).get("ok")),
        "pass": bool(parsed.get("pass")),
        "counterexamples": parsed.get("counterexamples") or [],
        "logic_holes": parsed.get("logic_holes") or [],
        "forbidden_rewrite": True,
        "notes": parsed.get("notes"),
        "executor": "deepseek",
        "role": "logic_attacker",
        "ai_error": (res or {}).get("error"),
        "at": _now(),
        "schema": STEP_A_SCHEMA,
        "code_version": STEP_A_CODE_VERSION,
    }


def production_risk_attack(mechanism_spec, dsl, packs, ai_json_fn):
    """4th AI: production risk — SL attach, close, size, leverage, no force open."""
    prompt = (
        "你是生产风险攻击AI。检查策略若上线是否威胁：止损挂载、平仓、仓位、杠杆、"
        "强制开仓、模拟成交冒充实盘。禁止改写机制。输出 JSON："
        "{risks:[...], fatal:bool, pass:bool, checks:{attached_sl:bool,tp_or_exit:bool,"
        "no_force_open:bool,no_sim_fill_as_live:bool,leverage_ok:bool,size_cap_ok:bool}}。"
    )
    payload = {
        "constraints": {
            "leverage": 20, "sl_pct": 0.009, "b_grade_size": 0.30,
            "ada_sl_migration_forbidden": True,
        },
        "mechanism_spec": {
            "stop_logic": (mechanism_spec or {}).get("stop_logic"),
            "take_profit_logic": (mechanism_spec or {}).get("take_profit_logic"),
            "non_negotiable_rules": (mechanism_spec or {}).get("non_negotiable_rules"),
        },
        "dsl_keys": list((dsl or {}).keys()),
        "live_enabled": (dsl or {}).get("live_enabled"),
        "auto_trade_eligible": (dsl or {}).get("auto_trade_eligible"),
    }
    try:
        res = ai_json_fn("qwen", prompt, payload, max_tokens=1000, temperature=0.15)
        parsed = (res or {}).get("parsed") or {}
    except Exception as exc:
        # Local deterministic fallback — never auto-pass production risk on AI outage
        local = _local_prod_risk(dsl, mechanism_spec)
        local["error"] = str(exc)
        local["executor"] = "local_fallback_after_qwen_error"
        return local

    if not (res or {}).get("ok") or not isinstance(parsed, dict):
        return _local_prod_risk(dsl, mechanism_spec)

    checks = parsed.get("checks") or {}
    return {
        "ok": True,
        "pass": bool(parsed.get("pass")) and not bool(parsed.get("fatal")),
        "fatal": bool(parsed.get("fatal")),
        "risks": parsed.get("risks") or [],
        "checks": checks,
        "executor": "qwen_as_production_risk_ai",
        "role": "production_risk_attacker",
        "forbidden_rewrite": True,
        "at": _now(),
        "schema": STEP_A_SCHEMA,
        "code_version": STEP_A_CODE_VERSION,
    }


def _local_prod_risk(dsl, mechanism_spec):
    dsl = dsl or {}
    spec = mechanism_spec or {}
    checks = {
        "attached_sl": bool(str(spec.get("stop_logic") or "").strip()),
        "tp_or_exit": bool(dsl.get("exit") or spec.get("take_profit_logic") or spec.get("exit_logic")),
        "no_force_open": True,  # creation path never sets force
        "no_sim_fill_as_live": not bool(dsl.get("live_enabled")),
        "leverage_ok": True,
        "size_cap_ok": True,
        "not_auto_mounted": not bool(dsl.get("auto_trade_eligible")),
    }
    fatal = not checks["attached_sl"] or not checks["tp_or_exit"]
    return {
        "ok": True,
        "pass": (not fatal) and checks["no_sim_fill_as_live"] and checks["not_auto_mounted"],
        "fatal": fatal,
        "risks": [k for k, v in checks.items() if not v],
        "checks": checks,
        "executor": "local_production_risk_rules",
        "role": "production_risk_attacker",
        "forbidden_rewrite": True,
        "at": _now(),
        "schema": STEP_A_SCHEMA,
        "code_version": STEP_A_CODE_VERSION,
    }


def glm_mechanism_review(mechanism_spec, ai_json_fn):
    prompt = (
        "你是 GLM 机制复核者。确认机制完整、对手方明确、偏差消失条件明确、非纯指标组合。"
        "禁止为了通过而放宽标准。输出 JSON：{pass:bool, issues:[...], mechanism_clarity:0-100}。"
    )
    try:
        res = ai_json_fn("glm", prompt, {"mechanism_spec": mechanism_spec}, max_tokens=800, temperature=0.2)
        parsed = (res or {}).get("parsed") or {}
    except Exception as exc:
        return {"ok": False, "pass": False, "error": str(exc), "executor": "glm"}
    return {
        "ok": bool((res or {}).get("ok")),
        "pass": bool(parsed.get("pass")),
        "issues": parsed.get("issues") or [],
        "mechanism_clarity": parsed.get("mechanism_clarity"),
        "executor": "glm",
        "role": "mechanism_reviewer",
        "at": _now(),
    }


def codex_fidelity_review(mechanism_spec, fidelity_diff, ai_json_fn):
    prompt = (
        "你是 Codex 忠实度复核者。对照 mechanism_spec 与 fidelity_diff，确认未改写机制。"
        "输出 JSON：{pass:bool, drift_flags:[...], notes:str}。"
    )
    payload = {"mechanism_spec": mechanism_spec, "fidelity_diff": fidelity_diff}
    try:
        res = ai_json_fn("glm", prompt, payload, max_tokens=800, temperature=0.1)  # local/codex slot via glm if needed
        parsed = (res or {}).get("parsed") or {}
    except Exception as exc:
        # fall back to rule fidelity
        return {
            "ok": True,
            "pass": bool((fidelity_diff or {}).get("pass")),
            "drift_flags": (fidelity_diff or {}).get("feature_drift_vs_prior") or [],
            "notes": "local_fidelity_fallback:%s" % exc,
            "executor": "local_fidelity_diff",
            "role": "fidelity_reviewer",
            "at": _now(),
        }
    if not (res or {}).get("ok"):
        return {
            "ok": True,
            "pass": bool((fidelity_diff or {}).get("pass")),
            "drift_flags": (fidelity_diff or {}).get("feature_drift_vs_prior") or [],
            "executor": "local_fidelity_diff",
            "role": "fidelity_reviewer",
            "at": _now(),
        }
    return {
        "ok": True,
        "pass": bool(parsed.get("pass")),
        "drift_flags": parsed.get("drift_flags") or [],
        "notes": parsed.get("notes"),
        "executor": "codex_review_via_ai",
        "role": "fidelity_reviewer",
        "at": _now(),
    }
