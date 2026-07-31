# -*- coding: utf-8 -*-
"""Independent DeepSeek/Qwen/GLM review bound to one candidate hash.

This module has no exchange credentials and cannot execute trades.  Missing,
failed or malformed reviews are rejections; there is deliberately no fallback.
"""
from __future__ import print_function

import ast
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib import request as urllib_request
from urllib import error as urllib_error


PROVIDERS = ("deepseek", "qwen", "glm")
# Prepared but inactive unless QIYU_<NAME>_ENABLED=1. Never joined into
# PROVIDERS consensus / review voting while standby.
STANDBY_PROVIDERS = ("kimi",)
CONSENT_SCOPES = ("redacted_market_research_data",
                  "redacted_trade_research_data")


def external_research_consent_status(provider=None, purpose=None):
    """Return a public, secret-free view of the explicit external-AI consent.

    Every network entry point calls this function. Missing, malformed or
    insufficient consent therefore fails closed even when API keys exist.
    """
    root = os.environ.get("VECTOR_ROOT", "/root")
    path = os.environ.get(
        "QIYU_AI_RESEARCH_CONSENT_FILE",
        os.path.join(root, "auto_trade", "ai_research_consent.json"),
    )
    result = {"granted": False, "provider_allowed": False,
              "purpose_allowed": False, "path": path}
    try:
        with open(path, "r") as handle:
            record = json.load(handle)
        providers = set(str(value).lower() for value in
                        (record.get("providers") or []))
        scopes = set(str(value) for value in (record.get("scopes") or []))
        required = set(CONSENT_SCOPES)
        if purpose:
            required.add(str(purpose))
        result.update({
            "granted": bool(record.get("granted")) and required.issubset(scopes),
            "provider_allowed": provider is None or str(provider).lower() in providers,
            "purpose_allowed": purpose is None or str(purpose) in scopes,
            "granted_at": record.get("granted_at"),
            "providers": sorted(providers),
            "scopes": sorted(scopes),
        })
        result["allowed"] = bool(result["granted"] and
                                 result["provider_allowed"] and
                                 result["purpose_allowed"])
    except Exception as exc:
        result.update({"allowed": False,
                       "error": "consent record unavailable or invalid: %s" % exc})
    return result


def _load_root_only_env():
    """Load the service env for CLI/UI status without exposing values."""
    root = os.environ.get("VECTOR_ROOT", "/root")
    path = os.path.join(root, "auto_trade", "ai_ecosystem.env")
    try:
        with open(path, "r") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip()
                if value[:1] == value[-1:] and value[:1] in ("'", '"'):
                    value = value[1:-1]
                # Prefer real keys: do not let empty shell/env stubs block
                # /root/auto_trade/ai_ecosystem.env values.
                if key.startswith("QIYU_"):
                    existing = str(os.environ.get(key) or "").strip()
                    if not existing:
                        os.environ[key] = value
    except Exception:
        pass


_load_root_only_env()


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def candidate_hash(candidate):
    return hashlib.sha256(canonical_json(candidate).encode("utf-8")).hexdigest()


def _normalize_provider_name(name):
    """Map legacy chatgpt/openai slot to glm (Zhipu GLM-5.2)."""
    n = str(name or "").strip().lower().replace("-", "").replace("_", "")
    if n in ("chatgpt", "openai", "gpt", "glm"):
        return "glm"
    if n in ("kimi", "kimik3", "moonshot", "moonshotai"):
        return "kimi"
    # restore common dashed forms for unknown names
    raw = str(name or "").strip().lower()
    return raw


def _provider_enabled(name):
    """Active consensus providers are always on; standby need explicit enable."""
    name = _normalize_provider_name(name)
    if name in PROVIDERS:
        return True
    if name in STANDBY_PROVIDERS:
        flag = str(os.environ.get("QIYU_%s_ENABLED" % name.upper(), "0")).strip().lower()
        return flag in ("1", "true", "yes", "on")
    return False


def _provider_config(name):
    name = _normalize_provider_name(name)
    prefix = "QIYU_%s_" % name.upper()
    defaults = {
        "deepseek": ("https://api.deepseek.com/chat/completions", "deepseek-v4-pro"),
        "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "qwen3.7-plus"),
        "glm": (
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            "glm-5.2",
        ),
        # Moonshot Kimi K3 — standby only until QIYU_KIMI_ENABLED=1
        "kimi": (
            "https://api.moonshot.ai/v1/chat/completions",
            "kimi-k3",
        ),
    }
    if name not in defaults:
        raise KeyError("unknown AI provider: %s" % name)
    url, model = defaults[name]
    return {
        "name": name,
        "api_key": os.environ.get(prefix + "API_KEY", "").strip(),
        "url": os.environ.get(prefix + "URL", url).strip(),
        "model": os.environ.get(prefix + "MODEL", model).strip(),
        "timeout": int(os.environ.get(prefix + "TIMEOUT_SEC", "90")),
        "enabled": _provider_enabled(name),
        "standby": name in STANDBY_PROVIDERS,
    }


def credentials_status():
    # Keep active providers at top-level for existing status UIs.
    rows = {}
    for name in PROVIDERS:
        cfg = _provider_config(name)
        rows[name] = {
            "configured": bool(cfg["api_key"]), "url": cfg["url"],
            "model": cfg["model"],
            "enabled": True,
            "standby": False,
        }
    standby = {}
    for name in STANDBY_PROVIDERS:
        cfg = _provider_config(name)
        standby[name] = {
            "configured": bool(cfg["api_key"]), "url": cfg["url"],
            "model": cfg["model"],
            "enabled": bool(cfg.get("enabled")),
            "standby": True,
            "note_zh": "已预置，默认不参与复核/创造；QIYU_KIMI_ENABLED=1 后才可调用",
        }
    rows["standby"] = standby
    return rows


SYSTEM_PROMPT = """你是量化交易策略的独立风险复核员。你不会看到其他AI的结论。
只根据候选规则、基线、0.9%主止损回测、0.6%辅助回测、分段与风险指标判断。
不要因高胜率忽视样本、频率、连续止损、手续费和过拟合。不得修改候选内容。
仅输出一个JSON对象：
{"candidate_hash":"原样复述", "decision":"APPROVE或REJECT", "confidence":0到1,
 "risk_flags":["..."], "reason":"中文简述"}
若哈希、证据或信息不完整必须REJECT。"""


def review_one(name, candidate, evidence, _retry=True):
    name = _normalize_provider_name(name)
    cfg = _provider_config(name)
    digest = candidate_hash(candidate)
    if not cfg.get("enabled"):
        return {"provider": name, "ok": False, "decision": "REJECT",
                "candidate_hash": digest,
                "reason": "待机模型未启用（QIYU_%s_ENABLED≠1）" % name.upper(),
                "standby_disabled": True}
    consent = external_research_consent_status(name, "final_review")
    if not consent.get("allowed"):
        return {"provider": name, "ok": False, "decision": "REJECT",
                "candidate_hash": digest,
                "reason": "外部AI研究授权缺失或范围不足",
                "consent_missing": True}
    if not cfg["api_key"]:
        return {"provider": name, "ok": False, "decision": "REJECT",
                "candidate_hash": digest, "reason": "API密钥未配置",
                "credential_missing": True}
    user_payload = {"candidate_hash": digest, "candidate": candidate,
                    "deterministic_evidence": evidence}
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": canonical_json(user_payload)},
        ],
    }
    if name == "deepseek":
        body["response_format"] = {"type": "json_object"}
    body["temperature"] = 0
    body["max_tokens"] = 800
    started = time.time()
    try:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        http_request = urllib_request.Request(
            cfg["url"], data=encoded,
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST",
        )
        response = urllib_request.urlopen(http_request, timeout=cfg["timeout"])
        raw = json.loads(response.read().decode("utf-8"))
        text = (((raw.get("choices") or [{}])[0].get("message") or {})
                .get("content") or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json.loads(text)
        exact_hash = str(parsed.get("candidate_hash") or "") == digest
        decision = str(parsed.get("decision") or "").upper()
        ok = bool(exact_hash and decision in ("APPROVE", "REJECT"))
        return {
            "provider": name, "model": cfg["model"], "ok": ok,
            "candidate_hash": digest, "decision": decision if ok else "REJECT",
            "confidence": parsed.get("confidence"),
            "risk_flags": parsed.get("risk_flags") or [],
            "reason": parsed.get("reason") or ("返回格式或哈希不正确" if not ok else ""),
            "latency_sec": round(time.time() - started, 3),
        }
    except urllib_error.HTTPError as exc:
        if _retry:
            return review_one(name, candidate, evidence, _retry=False)
        reason = "HTTP %s" % exc.code
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error") or {}
            reason = "%s: %s" % (detail.get("code") or detail.get("type") or reason,
                                  detail.get("message") or reason)
        except Exception:
            pass
        return {"provider": name, "model": cfg["model"], "ok": False,
                "candidate_hash": digest, "decision": "REJECT",
                "reason": "复核调用失败: %s" % reason,
                "latency_sec": round(time.time() - started, 3)}
    except Exception as exc:
        if _retry:
            return review_one(name, candidate, evidence, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "candidate_hash": digest, "decision": "REJECT",
                "reason": "复核调用失败: %s" % exc,
                "latency_sec": round(time.time() - started, 3)}


def unanimous_review(candidate, evidence, use_arbitration=True):
    digest = candidate_hash(candidate)
    # Providers run concurrently but independently: none receives another
    # provider's answer.  Optional arbitration adds retry/failover/degraded pair.
    if use_arbitration:
        try:
            import auto_trade_ai_arbitration as arb

            def _call(provider):
                return review_one(provider, candidate, evidence)

            verdict = arb.arbitrate_reviews(_call)
            reviews = verdict.get("reviews") or []
            decision = str(verdict.get("effective_decision") or "HOLD").upper()
            approved = decision == "APPROVE"
            return {
                "ok": True,
                "candidate_hash": digest,
                "approved": approved,
                "unanimous": verdict.get("mode") == "unanimous" and approved,
                "mode": verdict.get("mode"),
                "grade_penalty_steps": int(verdict.get("grade_penalty_steps") or 0),
                "natural_language": verdict.get("natural_language"),
                "reviews": reviews,
                "notify_human": bool(verdict.get("notify_human")),
            }
        except Exception:
            pass
    pool = ThreadPoolExecutor(max_workers=len(PROVIDERS))
    try:
        futures = [pool.submit(review_one, name, candidate, evidence)
                   for name in PROVIDERS]
        reviews = [future.result() for future in futures]
    finally:
        pool.shutdown(wait=True)
    approved = all(
        row.get("ok") and row.get("decision") == "APPROVE"
        and row.get("candidate_hash") == digest for row in reviews
    )
    return {"candidate_hash": digest, "approved": approved,
            "policy": "deepseek_and_qwen_and_glm_unanimous_exact_hash",
            "reviews": reviews}


# ─── Phase 5: 3-party dimensional unanimous review ───────────────────

PHASE5_REVIEW_DIMENSIONS = {
    "deepseek": "causal_logic",
    "qwen": "market_game_friction",
    "glm": "production_risk",
}

PHASE5_SYSTEM_PROMPT = """你是栖语量化策略的独立三维复核员。你不会看到其他AI的结论。
你的复核维度是：{dimension_label}。
候选规则由 candidate_hash 唯一标识。
{dimension_instruction}
仅输出一个JSON对象：
{{"candidate_hash":"原样复述", "decision":"APPROVE或REJECT", "confidence":0到1,
 "fatal_risk":false, "risk_flags":["..."], "reason":"中文简述"}}
若哈希、证据或信息不完整必须REJECT且fatal_risk=true。"""

PHASE5_DIM_INSTRUCTIONS = {
    "causal_logic": "审查因果链是否自洽、机制是否可伪证、盈利来源是否独立于过拟合。命中不可观测因果或未来函数必须 fatal_risk=true。",
    "market_game_friction": "审查博弈对手适应、拥挤、摩擦成本覆盖、容量风险。压力成本不能覆盖必须 fatal_risk=true。",
    "production_risk": "审查实盘执行链、延迟、滑点、部分成交、止损偏差、20x杠杆存活性。不可执行必须 fatal_risk=true。",
}


def phase5_review_one(name, candidate, evidence, _retry=True):
    """Phase 5: per-provider dimensional review with Fatal flag."""
    name = _normalize_provider_name(name)
    cfg = _provider_config(name)
    digest = candidate_hash(candidate)
    dim = PHASE5_REVIEW_DIMENSIONS.get(name, "production_risk")
    consent = external_research_consent_status(name, "final_review")
    if not consent.get("allowed"):
        return {"provider": name, "ok": False, "decision": "REJECT",
                "candidate_hash": digest, "fatal_risk": True,
                "reason": "外部AI研究授权缺失或范围不足",
                "consent_missing": True, "dimension": dim}
    if not cfg["api_key"]:
        return {"provider": name, "ok": False, "decision": "REJECT",
                "candidate_hash": digest, "fatal_risk": True,
                "reason": "API密钥未配置",
                "credential_missing": True, "dimension": dim}
    prompt = PHASE5_SYSTEM_PROMPT.format(
        dimension_label=dim,
        dimension_instruction=PHASE5_DIM_INSTRUCTIONS.get(dim, ""),
    )
    user_payload = {"candidate_hash": digest, "candidate": candidate,
                    "deterministic_evidence": evidence}
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": canonical_json(user_payload)},
        ],
    }
    if name == "deepseek":
        body["response_format"] = {"type": "json_object"}
    body["temperature"] = 0
    body["max_tokens"] = 900
    started = time.time()
    try:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        http_request = urllib_request.Request(
            cfg["url"], data=encoded,
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST",
        )
        response = urllib_request.urlopen(http_request, timeout=cfg["timeout"])
        raw = json.loads(response.read().decode("utf-8"))
        text = (((raw.get("choices") or [{}])[0].get("message") or {})
                .get("content") or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json.loads(text)
        exact_hash = str(parsed.get("candidate_hash") or "") == digest
        decision = str(parsed.get("decision") or "").upper()
        fatal = bool(parsed.get("fatal_risk"))
        ok = bool(exact_hash and decision in ("APPROVE", "REJECT"))
        if fatal:
            decision = "REJECT"
        return {
            "provider": name, "model": cfg["model"], "ok": ok,
            "candidate_hash": digest,
            "decision": decision if ok else "REJECT",
            "confidence": parsed.get("confidence"),
            "fatal_risk": fatal or (not ok),
            "risk_flags": parsed.get("risk_flags") or [],
            "reason": parsed.get("reason") or ("返回格式或哈希不正确" if not ok else ""),
            "dimension": dim,
            "latency_sec": round(time.time() - started, 3),
        }
    except Exception as exc:
        if _retry:
            return phase5_review_one(name, candidate, evidence, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "candidate_hash": digest, "decision": "REJECT",
                "fatal_risk": True,
                "reason": "复核调用失败: %s" % exc,
                "dimension": dim,
                "latency_sec": round(time.time() - started, 3)}


def phase5_unanimous_review(candidate, evidence):
    """Phase 5: 3-party unanimous dimensional review.

    All 3 providers must APPROVE with exact candidate_hash match.
    Any Fatal flag from any provider → immediate REJECT.
    Missing consent/key → fail-closed REJECT.
    """
    digest = candidate_hash(candidate)
    consent_ok = True
    for name in PROVIDERS:
        cs = external_research_consent_status(name, "final_review")
        if not cs.get("allowed"):
            consent_ok = False
    if not consent_ok:
        return {
            "candidate_hash": digest,
            "approved": False,
            "policy": "phase5_3party_unanimous_dimensional_fail_closed",
            "reviews": [],
            "fail_reason": "consent_or_key_missing",
            "fatal_any": True,
        }

    pool = ThreadPoolExecutor(max_workers=len(PROVIDERS))
    try:
        futures = [pool.submit(phase5_review_one, name, candidate, evidence)
                   for name in PROVIDERS]
        reviews = [future.result() for future in futures]
    finally:
        pool.shutdown(wait=True)

    fatal_any = any(r.get("fatal_risk") for r in reviews)
    approved = (
        not fatal_any
        and all(
            r.get("ok") and r.get("decision") == "APPROVE"
            and r.get("candidate_hash") == digest
            for r in reviews
        )
    )
    fail_reasons = []
    for r in reviews:
        if r.get("decision") != "APPROVE" or r.get("fatal_risk"):
            fail_reasons.append("%s:%s(fatal=%s)" % (
                r.get("provider"), r.get("decision"), r.get("fatal_risk")))

    return {
        "candidate_hash": digest,
        "approved": approved,
        "policy": "phase5_3party_unanimous_dimensional_fail_closed",
        "reviews": reviews,
        "fatal_any": fatal_any,
        "fail_reasons": fail_reasons,
    }


# ─── Theoretical strategy review (Codex-authored; 3AI gate) ───────────

THEORETICAL_STRATEGY_REVIEW_PROMPT = """你是栖语策略的独立「理论复核官」。你不会看到其他AI的结论。
任务：评估 Codex 提交的策略在真实成本下是否具备稳健正期望，重点看：
  A) 理论胜率 theoretical_win_rate_pct
  B) 理论「盈利单」单笔盈利率 theoretical_mean_net_pct
     （只统计盈利成交的平均净收益率；单位百分比点，例如 7.5 表示盈利单平均约 +7.5%）
  C) 止损簇风险
规则：
1) 不得修改策略；不得因「已经写好」而放宽标准。
2) deterministic_evidence.safety_metrics 已按【该策略标的+周期】observed_base全摩擦回测。
   请优先使用：
   - empirical_win_rate / win_rate
   - mean_net_win_only_pct（盈利单平均净收益率，百分比点）
   - mean_net_win_only（盈利单平均，小数口径，如 0.075 = 7.5%）
   注意：mean_net（含亏损全体均值）仅作参考，禁止再当作 B 项锚点。
   你输出的 theoretical_mean_net_pct 必须是「盈利单」百分比点，不要输出 0.075。
3) theoretical_win_rate_pct 必须以 empirical_win_rate 为锚做有限折价（通常0-8个百分点），
   仅当逻辑自相矛盾、明显过拟合或命中已知死因时才可大幅下调。
4) theoretical_mean_net_pct 必须以 mean_net_win_only_pct 为锚做有限折价
   （通常相对折价约 5%-20%，或最多下调 0.5~1.5 个百分点）；样本很小（trades<30）时更保守。
5) stop_cluster_risk 优先参考 max_loss_streak 与 fold_meta；streak≤2且五折多数为正时，
   不应默认 high。给 stop_cluster_prob（0-1）。
6) 硬门槛（必须同时满足才可 APPROVE）：
   - 理论胜率 ≥ 65
   - 理论盈利单盈利率 ≥ 5（百分比点）
   - 止损簇风险低（low，或概率≤0.30）
   - 每周理论开仓 theoretical_weekly_opens ≥ 0.5
     统计锚点必须来自近2年（约730天，span≥600天）回测成交密度
     （trades×7/span_days，method=backtest_2y_fill_rate_proxy）；
     必须以该锚点做有限折价（通常相对折价约 5%-20%，或最多下调 0.3~0.5 次/周）。
     禁止上调超过统计锚点；不得用短窗 live_14d 替代近2年锚点。
7) 证据不足、逻辑自相矛盾、明显依赖未来信息或命中已知死因 → REJECT。
仅输出一个JSON对象：
{"candidate_hash":"原样复述","decision":"APPROVE或REJECT",
 "theoretical_win_rate_pct":0到100,
 "theoretical_mean_net_pct":数字（盈利单平均净盈利率，百分比点）,
 "theoretical_weekly_opens":数字（周理论开仓，次/周）,
 "stop_cluster_risk":"low或medium或high",
 "stop_cluster_prob":0到1,"confidence":0到1,"reason":"中文简述",
 "failure_modes":["..."]}
"""

MIN_THEORETICAL_WR = 65.0
MIN_THEORETICAL_WIN_MEAN_NET_PCT = 5.0
# 生产口径（R4）：近2年回测成交密度作统计锚点 + 3AI 有限折价。
MIN_THEORETICAL_WEEKLY_OPENS = 0.5
WEEKLY_OPENS_LOOKBACK_DAYS = 730
WEEKLY_OPENS_MIN_SPAN_DAYS = 600  # near-2y sample floor for R4
WEEKLY_OPENS_STAT_METHOD = "backtest_2y_fill_rate_proxy"
WEEKLY_OPENS_MAX_REL_DISCOUNT = 0.20
WEEKLY_OPENS_MAX_ABS_DISCOUNT = 0.50
MAX_STOP_CLUSTER_PROB = 0.30


def _negative_binomial_weekly_interval_80(observed_fills, observed_days=14.0):
    """Gamma-Poisson posterior-predictive 80% interval for next-week fills.

    With 2 fills in 14 days this intentionally yields [0, 3], matching the
    production ADA5 frequency card.  This is a count uncertainty interval,
    never an AI estimate.
    """
    import math

    n = max(0, int(observed_fills or 0))
    days = _sf_num(observed_days, 14.0) or 14.0
    exposure_weeks = max(float(days) / 7.0, 1e-9)
    # For n=0 use a weak Jeffreys shape only to describe uncertainty.  The
    # point estimate remains the observed rate (zero), so this cannot pass.
    shape = float(n) if n > 0 else 0.5
    p = exposure_weeks / (exposure_weeks + 1.0)

    def _pmf(k):
        return math.exp(
            math.lgamma(k + shape) - math.lgamma(shape) - math.lgamma(k + 1)
            + shape * math.log(p) + k * math.log(1.0 - p)
        )

    quantiles = []
    cumulative = 0.0
    targets = (0.10, 0.90)
    target_i = 0
    for k in range(0, 1000):
        cumulative += _pmf(k)
        while target_i < len(targets) and cumulative >= targets[target_i] - 1e-15:
            quantiles.append(k)
            target_i += 1
        if target_i >= len(targets):
            break
    if len(quantiles) != 2:
        return [0, max(0, int(round(n / exposure_weeks * 3.0)))]
    return [int(quantiles[0]), int(quantiles[1])]


def _weekly_frequency_pack(expected, *, method, source, baseline=None,
                           regime_factor=None, n_obs=None, observed_days=None,
                           weekly_interval=None, funnel_14d=None):
    expected = _sf_num(expected)
    daily = None if expected is None else float(expected) / 7.0
    if weekly_interval is None and n_obs is not None:
        weekly_interval = _negative_binomial_weekly_interval_80(
            n_obs, observed_days or 14.0)
    confidence = "low"
    if n_obs is not None and int(n_obs) >= 20:
        confidence = "high"
    elif n_obs is not None and int(n_obs) >= 8:
        confidence = "mid"
    return {
        "expected_weekly_fills": expected,
        "expected_daily_fills": None if daily is None else round(daily, 3),
        "baseline_weekly_fills": baseline,
        "regime_factor": regime_factor,
        "weekly_interval": weekly_interval,
        "interval_level": 0.80 if weekly_interval is not None else None,
        "method": method,
        "calculation": "hybrid:statistical_anchor;three_ai_limited_discount",
        "statistical_baseline_locked": True,
        "ai_role": "limited_discount",
        "ai_may_override": False,
        "ai_may_discount": True,
        "max_rel_discount": WEEKLY_OPENS_MAX_REL_DISCOUNT,
        "max_abs_discount": WEEKLY_OPENS_MAX_ABS_DISCOUNT,
        "source": source,
        "n_obs": n_obs,
        "observed_days": observed_days,
        "confidence": confidence,
        "funnel_14d": funnel_14d,
    }


def resolve_statistical_weekly_opens(candidate=None, evidence=None):
    """Weekly opens statistical anchor (AI 可在此锚点上有限折价，不可上调覆盖).

    R4 / theoretical priority (近2年样本):
      1) evidence 显式近2年统计字段（method含2y 或 span≥600）
      2) 回测成交密度代理 trades/span_days×7（span≥600 → backtest_2y_fill_rate_proxy）
      3) 仅当 weekly_opens_require_2y=False 时才回退 live_14d
      4) 缺失/短窗 → expected=None 或 sample_insufficient（门禁失败）
    """
    evidence = dict(evidence or {})
    candidate = dict(candidate or {})
    require_2y = bool(evidence.get("weekly_opens_require_2y", True))

    def _attach_span(pack, span, n_trades=None):
        if span is not None:
            pack["span_days"] = float(span)
            pack["observed_days"] = float(span)
            pack["lookback_days_target"] = WEEKLY_OPENS_LOOKBACK_DAYS
        if n_trades is not None:
            pack["n_trades"] = int(n_trades)
        pack["sample_2y_ok"] = weekly_opens_2y_sample_ok(pack)
        return pack

    # 1) Explicit fields — accept only if 2y-contract satisfied when required.
    for key in (
        "statistical_weekly_opens_expected",
        "weekly_opens_expected",
        "expected_weekly_fills",
        "baseline_weekly_fills",
    ):
        v = _sf_num(evidence.get(key))
        if v is None:
            continue
        method = (evidence.get("frequency_method")
                  or evidence.get("weekly_opens_method")
                  or "evidence_explicit")
        span = _sf_num(
            evidence.get("span_days")
            or evidence.get("frequency_observed_days")
            or evidence.get("observation_days")
        )
        pack = _weekly_frequency_pack(
            float(v),
            method=method,
            source="evidence.%s" % key,
            baseline=evidence.get("baseline_weekly_fills"),
            regime_factor=evidence.get("regime_factor"),
            n_obs=evidence.get("frequency_n_obs") or evidence.get("n_obs"),
            observed_days=span,
            weekly_interval=evidence.get("weekly_interval"),
        )
        pack = _attach_span(pack, span, evidence.get("trades"))
        if (not require_2y) or weekly_opens_2y_sample_ok(pack):
            if weekly_opens_2y_sample_ok(pack) and "2y" not in str(method):
                pack["method"] = WEEKLY_OPENS_STAT_METHOD
            return pack
        # fall through — explicit short-window value cannot satisfy R4

    freq_block = evidence.get("frequency") or evidence.get("statistical_frequency") or {}
    if isinstance(freq_block, dict):
        v = _sf_num(freq_block.get("expected_weekly_fills"))
        if v is not None:
            span = _sf_num(
                freq_block.get("span_days")
                or freq_block.get("observed_days")
            )
            pack = _weekly_frequency_pack(
                float(v),
                method=freq_block.get("method") or WEEKLY_OPENS_STAT_METHOD,
                source="evidence.frequency",
                baseline=freq_block.get("baseline_weekly_fills"),
                regime_factor=freq_block.get("regime_factor"),
                n_obs=freq_block.get("n_obs"),
                observed_days=span,
                weekly_interval=freq_block.get("weekly_interval"),
                funnel_14d=freq_block.get("funnel_14d"),
            )
            pack = _attach_span(pack, span, freq_block.get("n_trades"))
            if (not require_2y) or weekly_opens_2y_sample_ok(pack):
                return pack

    # 2) Near-2y backtest density proxy (R4 primary)
    sm = evidence.get("safety_metrics") or {}
    n = int(sm.get("trades") or sm.get("total_trades") or evidence.get("trades") or 0)
    span = _sf_num(
        sm.get("span_days")
        or evidence.get("span_days")
        or sm.get("observation_days")
        or evidence.get("observation_days")
    )
    if n > 0 and span is not None and span > 1e-9:
        weekly = float(n) * (7.0 / float(span))
        method = (
            WEEKLY_OPENS_STAT_METHOD
            if float(span) + 1e-9 >= float(WEEKLY_OPENS_MIN_SPAN_DAYS)
            else "backtest_fill_rate_proxy_short_window"
        )
        pack = _weekly_frequency_pack(
            weekly,
            method=method,
            source="evidence.safety_metrics",
            baseline=weekly,
            regime_factor=1.0,
            n_obs=n,
            observed_days=float(span),
        )
        pack = _attach_span(pack, span, n)
        if (not require_2y) or weekly_opens_2y_sample_ok(pack):
            return pack
        # Keep the short-window pack for diagnostics; gate will fail sample_2y.
        pack["expected_weekly_fills"] = None
        pack["method"] = "backtest_sample_lt_2y"
        pack["short_window_weekly_fills"] = weekly
        return pack

    if require_2y:
        return _attach_span(
            _weekly_frequency_pack(
                None, method="missing_2y_sample", source="unresolved_2y"),
            span,
            n if n else None,
        )

    # 3) Optional legacy live_14d fallback (only when 2y not required)
    live_14d_fills = _sf_num(evidence.get("live_14d_fills"))
    if live_14d_fills is not None:
        days = _sf_num(evidence.get("live_observation_days"), 14.0) or 14.0
        regime_factor = _sf_num(evidence.get("regime_factor"), 1.0) or 1.0
        baseline = float(live_14d_fills) * 7.0 / float(days)
        return _weekly_frequency_pack(
            baseline * regime_factor,
            method="live_14d_fill_rate",
            source="evidence.live_14d_fills",
            baseline=baseline,
            regime_factor=regime_factor,
            n_obs=int(live_14d_fills),
            observed_days=days,
        )

    symbol = candidate.get("symbol") or evidence.get("symbol")
    instruments = candidate.get("supported_instruments") or evidence.get(
        "supported_instruments") or []
    if not symbol and isinstance(instruments, list) and instruments:
        symbol = instruments[0]
    timeframe = candidate.get("timeframe") or evidence.get("timeframe")
    key = candidate.get("key") or evidence.get("strategy_key")

    if symbol and timeframe and key:
        try:
            import auto_trade_expectancy_metrics as exp
            live_pack = exp.statistical_frequency_forecast(
                symbol, timeframe, key, can_open=True,
            )
            return _weekly_frequency_pack(
                float(live_pack.get("expected_weekly_fills") or 0.0),
                method=live_pack.get("method") or "live_14d_fill_rate",
                source="statistical_frequency_forecast",
                baseline=live_pack.get("baseline_weekly_fills"),
                regime_factor=live_pack.get("regime_factor"),
                n_obs=live_pack.get("n_obs"),
                observed_days=14,
                weekly_interval=live_pack.get("weekly_interval"),
                funnel_14d=live_pack.get("funnel_14d"),
            )
        except Exception:
            pass

    return _weekly_frequency_pack(
        None, method="missing", source="unresolved")


def _sf_num(x, default=None):
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default


def weekly_opens_2y_sample_ok(pack):
    """True when the statistical weekly-open anchor is a near-2y sample."""
    pack = pack or {}
    method = str(pack.get("method") or "")
    bad_methods = (
        "missing",
        "missing_2y_sample",
        "backtest_sample_lt_2y",
        "backtest_fill_rate_proxy_short_window",
        "live_14d_fill_rate",
        "weak_prior_new_mount",
        "unresolved",
        "unresolved_2y",
    )
    if method in bad_methods or "short" in method.lower() or "lt_2y" in method.lower():
        return False
    span = _sf_num(pack.get("span_days"))
    if span is None:
        span = _sf_num(pack.get("observed_days"))
    if span is not None and float(span) + 1e-9 >= float(WEEKLY_OPENS_MIN_SPAN_DAYS):
        return True
    if method == WEEKLY_OPENS_STAT_METHOD:
        return True
    return False


def _normalize_mean_net_pct(raw, empirical_mean_net=None):
    """Normalize AI win-only mean-net into percentage points.

    Accepts either percentage points (7.5) or decimal ratio (0.075).
    """
    v = _sf_num(raw)
    if v is None:
        return None
    emp = _sf_num(empirical_mean_net)
    # Heuristic: AI sometimes echoes decimal ratio (0.07x)
    if abs(v) < 0.5:
        if emp is None or abs(emp) < 0.5:
            return round(v * 100.0, 6)
    return round(v, 6)


def _normalize_theoretical_weekly_opens(raw, statistical_anchor=None):
    """Normalize AI weekly opens: anchor-discount only, never inflate above anchor.

    Soft guidance band (rel≤20% / abs≤0.5) is prompt-level; code hard-blocks
    inflation above the statistical anchor. Deeper discount is allowed when the
    model judges the signal sparse/overfit — the ≥0.5 gate then fail-closes.
    """
    v = _sf_num(raw)
    if v is None:
        return None
    v = max(0.0, float(v))
    anchor = _sf_num(statistical_anchor)
    if anchor is not None:
        v = min(v, max(0.0, float(anchor)))
    return round(v, 6)


def _empirical_win_mean_net_pct(evidence):
    """Prefer win-only empirics from safety_metrics."""
    sm = ((evidence or {}).get("safety_metrics") or {})
    for key in ("mean_net_win_only_pct", "win_mean_net_pct",
                "avg_win_pnl_pct"):
        v = _sf_num(sm.get(key))
        if v is not None:
            return v
    for key in ("mean_net_win_only", "win_mean_net", "avg_win_pnl_ratio"):
        v = _sf_num(sm.get(key))
        if v is not None:
            return (v * 100.0) if abs(v) < 0.5 else v
    # Do NOT fall back to all-trade mean_net — that mixes losses.
    return None


def theoretical_review_one(name, candidate, evidence, _retry=True):
    """Per-provider theoretical WR + win-only mean-net + weekly + stop-cluster."""
    name = _normalize_provider_name(name)
    cfg = _provider_config(name)
    digest = candidate_hash(candidate)
    evidence = dict(evidence or {})
    evidence.setdefault("weekly_opens_require_2y", True)
    emp_win_mean_pct = _empirical_win_mean_net_pct(evidence)
    weekly_pack = resolve_statistical_weekly_opens(candidate, evidence)
    stat_weekly = _sf_num(weekly_pack.get("expected_weekly_fills"))
    consent = external_research_consent_status(name, "final_review")
    if not consent.get("allowed"):
        return {"provider": name, "ok": False, "decision": "REJECT",
                "candidate_hash": digest,
                "theoretical_win_rate_pct": 0.0,
                "theoretical_mean_net_pct": None,
                "theoretical_weekly_opens": None,
                "stop_cluster_risk": "high", "stop_cluster_prob": 1.0,
                "reason": "外部AI研究授权缺失或范围不足",
                "consent_missing": True}
    if not cfg["api_key"]:
        return {"provider": name, "ok": False, "decision": "REJECT",
                "candidate_hash": digest,
                "theoretical_win_rate_pct": 0.0,
                "theoretical_mean_net_pct": None,
                "theoretical_weekly_opens": None,
                "stop_cluster_risk": "high", "stop_cluster_prob": 1.0,
                "reason": "API密钥未配置", "credential_missing": True}
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": THEORETICAL_STRATEGY_REVIEW_PROMPT},
            {"role": "user", "content": canonical_json({
                "candidate_hash": digest,
                "candidate": candidate,
                "deterministic_evidence": evidence,
                "gates": {
                    "min_theoretical_win_rate_pct": MIN_THEORETICAL_WR,
                    "min_theoretical_win_mean_net_pct": MIN_THEORETICAL_WIN_MEAN_NET_PCT,
                    "min_theoretical_weekly_opens": MIN_THEORETICAL_WEEKLY_OPENS,
                    "weekly_opens_method": WEEKLY_OPENS_STAT_METHOD,
                    "weekly_opens_ai_may_override": False,
                    "weekly_opens_ai_may_discount": True,
                    "weekly_opens_max_rel_discount": WEEKLY_OPENS_MAX_REL_DISCOUNT,
                    "weekly_opens_max_abs_discount": WEEKLY_OPENS_MAX_ABS_DISCOUNT,
                    "max_stop_cluster_prob": MAX_STOP_CLUSTER_PROB,
                    "mean_net_scope": "winning_trades_only",
                },
                "unit_hint": {
                    "theoretical_win_rate_pct": "0-100 percentage points",
                    "theoretical_mean_net_pct": (
                        "winning-trades-only percentage points; "
                        "empirical mean_net_win_only_pct≈%s"
                        % (("%.4f" % emp_win_mean_pct)
                           if emp_win_mean_pct is not None else "unknown")
                    ),
                    "theoretical_weekly_opens": (
                        "次/周；近2年回测锚点≈%s（span_days≈%s，method=%s）；"
                        "有限折价后门槛≥%.2f；禁止上调超过锚点；禁止用短窗替代"
                        % (
                            ("%.4f" % stat_weekly)
                            if stat_weekly is not None else "missing",
                            weekly_pack.get("span_days")
                            or weekly_pack.get("observed_days")
                            or "missing",
                            weekly_pack.get("method") or "missing",
                            MIN_THEORETICAL_WEEKLY_OPENS,
                        )
                    ),
                },
                "statistical_weekly_opens": weekly_pack,
            })},
        ],
    }
    if name == "deepseek":
        body["response_format"] = {"type": "json_object"}
    body["temperature"] = 0.2
    body["max_tokens"] = 1600
    started = time.time()
    try:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(
            cfg["url"], data=encoded,
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        with urllib_request.urlopen(req, timeout=max(int(cfg.get("timeout") or 90), 120)) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        content = (((raw.get("choices") or [{}])[0].get("message") or {})
                   .get("content"))
        if not str(content or "").strip() and name == "deepseek":
            content = (((raw.get("choices") or [{}])[0].get("message") or {})
                       .get("reasoning_content") or content)
        if not str(content or "").strip():
            # GLM / reasoning models may park JSON in reasoning_content.
            content = (((raw.get("choices") or [{}])[0].get("message") or {})
                       .get("reasoning_content") or content)
        parsed = _parse_content_json(content)
        decision = str(parsed.get("decision") or "REJECT").upper()
        if decision not in ("APPROVE", "REJECT"):
            decision = "REJECT"
        wr = _sf_num(parsed.get("theoretical_win_rate_pct"), 0.0) or 0.0
        mean_net_pct = _normalize_mean_net_pct(
            parsed.get("theoretical_mean_net_pct",
                       parsed.get("theoretical_win_mean_net_pct",
                                  parsed.get("theoretical_per_trade_profit_pct"))),
            empirical_mean_net=(
                (emp_win_mean_pct / 100.0) if emp_win_mean_pct is not None else None
            ),
        )
        weekly_opens = _normalize_theoretical_weekly_opens(
            parsed.get("theoretical_weekly_opens",
                       parsed.get("weekly_opens_expected",
                                  parsed.get("theoretical_weekly_opens_expected"))),
            statistical_anchor=stat_weekly,
        )
        risk = str(parsed.get("stop_cluster_risk") or "high").lower()
        if risk not in ("low", "medium", "high"):
            risk = "high"
        scp = _sf_num(parsed.get("stop_cluster_prob"), 1.0)
        if scp is None:
            scp = 1.0
        scp = max(0.0, min(1.0, scp))
        hash_ok = str(parsed.get("candidate_hash") or "") in ("", digest)
        return {
            "provider": name, "model": cfg["model"], "ok": True,
            "candidate_hash": digest,
            "decision": decision if hash_ok else "REJECT",
            "theoretical_win_rate_pct": wr,
            "theoretical_mean_net_pct": mean_net_pct,
            "theoretical_weekly_opens": weekly_opens,
            "statistical_weekly_opens_anchor": stat_weekly,
            "mean_net_scope": "winning_trades_only",
            "stop_cluster_risk": risk,
            "stop_cluster_prob": scp,
            "confidence": parsed.get("confidence"),
            "reason": parsed.get("reason") or "",
            "failure_modes": parsed.get("failure_modes") or [],
            "latency_sec": round(time.time() - started, 3),
            "hash_mismatch": not hash_ok,
        }
    except Exception as exc:
        if _retry:
            return theoretical_review_one(name, candidate, evidence, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "candidate_hash": digest, "decision": "REJECT",
                "theoretical_win_rate_pct": 0.0,
                "theoretical_mean_net_pct": None,
                "theoretical_weekly_opens": None,
                "stop_cluster_risk": "high", "stop_cluster_prob": 1.0,
                "reason": "复核调用失败: %s" % exc,
                "latency_sec": round(time.time() - started, 3)}


def _provider_theoretical_pass(row):
    if not row or not row.get("ok"):
        return False
    if str(row.get("decision") or "").upper() != "APPROVE":
        return False
    wr = _sf_num(row.get("theoretical_win_rate_pct"), 0.0) or 0.0
    if wr < MIN_THEORETICAL_WR - 1e-9:
        return False
    mean_net_pct = _sf_num(row.get("theoretical_mean_net_pct"))
    if mean_net_pct is None or mean_net_pct < MIN_THEORETICAL_WIN_MEAN_NET_PCT - 1e-9:
        return False
    weekly = _sf_num(row.get("theoretical_weekly_opens"))
    if weekly is None or weekly < MIN_THEORETICAL_WEEKLY_OPENS - 1e-9:
        return False
    risk = str(row.get("stop_cluster_risk") or "").lower()
    scp = _sf_num(row.get("stop_cluster_prob"), 1.0)
    if scp is None:
        scp = 1.0
    if risk == "low" or scp <= MAX_STOP_CLUSTER_PROB + 1e-12:
        return True
    return False


def _provider_infra_unavailable(row):
    """True when a provider could not cast a real vote (rate-limit/HTTP/key/consent)."""
    if not row:
        return True
    if row.get("consent_missing") or row.get("credential_missing"):
        return True
    if row.get("ok"):
        return False
    reason = str(row.get("reason") or "")
    markers = (
        "429", "Too Many Requests", "HTTP Error", "调用失败",
        "timeout", "Timed out", "URLError", "Connection",
        "API密钥未配置", "外部AI研究授权",
    )
    return any(m in reason for m in markers)


def theoretical_review_all(candidate, evidence):
    """Concurrent theoretical review.

    Production rule: DeepSeek, Qwen and GLM must all cast a real passing vote.
    Infrastructure failures are retryable failures, never permission to reduce
    the review to two providers.
    R4 weekly gate: near-2y backtest anchor + AI limited discount ≥ 0.5/week.
    """
    digest = candidate_hash(candidate)
    evidence = dict(evidence or {})
    evidence.setdefault("weekly_opens_require_2y", True)
    pool = ThreadPoolExecutor(max_workers=len(PROVIDERS))
    try:
        futures = [pool.submit(theoretical_review_one, name, candidate, evidence)
                   for name in PROVIDERS]
        reviews = [future.result() for future in futures]
    finally:
        pool.shutdown(wait=True)
    by_provider = {}
    wr_map = {}
    mean_net_map = {}
    weekly_map = {}
    risk_map = {}
    for row in reviews:
        name = row.get("provider")
        by_provider[name] = row
        wr_map[name] = float(row.get("theoretical_win_rate_pct") or 0.0)
        mean_net_map[name] = row.get("theoretical_mean_net_pct")
        weekly_map[name] = row.get("theoretical_weekly_opens")
        risk_map[name] = {
            "risk": row.get("stop_cluster_risk"),
            "prob": row.get("stop_cluster_prob"),
        }

    skipped = []
    voters = []
    for p in PROVIDERS:
        row = by_provider.get(p) or {}
        if _provider_infra_unavailable(row):
            skipped.append(p)
        else:
            voters.append(p)

    min_voters = len(PROVIDERS)
    wrs_vote = [wr_map[p] for p in voters] if voters else []
    avg = (sum(wrs_vote) / float(len(wrs_vote))) if wrs_vote else 0.0
    mean_vals = []
    for p in voters:
        v = _sf_num(mean_net_map.get(p))
        if v is not None:
            mean_vals.append(v)
    mean_net_avg = (
        round(sum(mean_vals) / float(len(mean_vals)), 6) if mean_vals else None
    )
    weekly_vals = []
    for p in voters:
        v = _sf_num(weekly_map.get(p))
        if v is not None:
            weekly_vals.append(v)
    weekly_avg = (
        round(sum(weekly_vals) / float(len(weekly_vals)), 6)
        if weekly_vals else None
    )
    approved = (
        len(voters) >= min_voters
        and all(_provider_theoretical_pass(by_provider.get(p)) for p in voters)
    )
    fail_reasons = []
    if len(voters) < min_voters:
        fail_reasons.append(
            "healthy_voters_lt_%s(have=%s,skipped=%s)"
            % (min_voters, ",".join(voters) or "-", ",".join(skipped) or "-"))
    for p in voters:
        row = by_provider.get(p) or {}
        if not _provider_theoretical_pass(row):
            fail_reasons.append(
                "%s:%s(wr=%.1f,mean_net=%.3f,weekly=%.3f,risk=%s,p=%.2f)" % (
                    p, row.get("decision") or "FAIL",
                    float(row.get("theoretical_win_rate_pct") or 0),
                    float(row.get("theoretical_mean_net_pct") or 0),
                    float(row.get("theoretical_weekly_opens") or 0),
                    row.get("stop_cluster_risk") or "?",
                    float(row.get("stop_cluster_prob") or 1)))
    for p in skipped:
        row = by_provider.get(p) or {}
        fail_reasons.append("%s:SKIP_INFRA(%s)" % (
            p, str(row.get("reason") or "unavailable")[:80]))

    # Weekly opens: near-2y statistical anchor + AI limited discount (gate on AI avg)
    evidence = dict(evidence or {})
    evidence.setdefault("weekly_opens_require_2y", True)
    weekly_pack = resolve_statistical_weekly_opens(candidate, evidence)
    stat_weekly = _sf_num(weekly_pack.get("expected_weekly_fills"))
    sample_2y_ok = weekly_opens_2y_sample_ok(weekly_pack)
    weekly_ok = (
        weekly_avg is not None
        and float(weekly_avg) >= float(MIN_THEORETICAL_WEEKLY_OPENS) - 1e-9
        and stat_weekly is not None
        and sample_2y_ok
    )
    if not sample_2y_ok:
        approved = False
        fail_reasons.append(
            "weekly_2y_sample_required(got_span=%s,method=%s,min_span=%s)"
            % (
                weekly_pack.get("span_days")
                or weekly_pack.get("observed_days")
                or "missing",
                weekly_pack.get("method") or "missing",
                WEEKLY_OPENS_MIN_SPAN_DAYS,
            )
        )
    elif not weekly_ok:
        approved = False
        fail_reasons.append(
            "weekly_opens_lt_%s(got=%s,anchor=%s,method=%s)"
            % (
                MIN_THEORETICAL_WEEKLY_OPENS,
                ("%.4f" % weekly_avg) if weekly_avg is not None else "missing",
                ("%.4f" % stat_weekly) if stat_weekly is not None else "missing",
                weekly_pack.get("method") or "missing",
            )
        )

    policy = (
        "theoretical_wr_ge_%s_win_mean_net_ge_%s_weekly_opens_ge_%s_stop_cluster_low_healthy_providers_min_%s"
        % (
            int(MIN_THEORETICAL_WR),
            int(MIN_THEORETICAL_WIN_MEAN_NET_PCT),
            MIN_THEORETICAL_WEEKLY_OPENS,
            min_voters,
        )
    )
    nl_extra = (
        ("；已忽略不可用: " + ",".join(skipped)) if skipped else ""
    )
    mean_txt = (
        ("，均值盈利单盈利率 %.3f%%" % mean_net_avg)
        if mean_net_avg is not None else "，盈利单盈利率未齐"
    )
    weekly_txt = (
        ("，三AI周开仓 %.3f（锚点 %.3f·%s）"
         % (weekly_avg, stat_weekly, weekly_pack.get("method")))
        if weekly_avg is not None and stat_weekly is not None
        else "，三AI周开仓缺失"
    )
    return {
        "schema": "qiyu_three_ai_theoretical_review_v2",
        "ok": True,
        "candidate_hash": digest,
        "approved": approved,
        "policy": policy,
        "gates": {
            "min_theoretical_win_rate_pct": MIN_THEORETICAL_WR,
            "min_theoretical_win_mean_net_pct": MIN_THEORETICAL_WIN_MEAN_NET_PCT,
            "min_theoretical_weekly_opens": MIN_THEORETICAL_WEEKLY_OPENS,
            "weekly_opens_method": WEEKLY_OPENS_STAT_METHOD,
            "weekly_opens_lookback_days": WEEKLY_OPENS_LOOKBACK_DAYS,
            "weekly_opens_min_span_days": WEEKLY_OPENS_MIN_SPAN_DAYS,
            "weekly_opens_ai_may_override": False,
            "weekly_opens_ai_may_discount": True,
            "weekly_opens_require_2y": True,
            "mean_net_scope": "winning_trades_only",
        },
        "ai_theoretical_wr_by_provider": wr_map,
        "ai_theoretical_wr_avg": round(avg, 3),
        "ai_theoretical_mean_net_by_provider": mean_net_map,
        "ai_theoretical_mean_net_avg": mean_net_avg,
        "ai_theoretical_weekly_opens_by_provider": weekly_map,
        "ai_theoretical_weekly_opens_avg": weekly_avg,
        "mean_net_scope": "winning_trades_only",
        "statistical_weekly_opens": weekly_pack,
        "statistical_weekly_opens_expected": stat_weekly,
        "weekly_opens_2y_sample_ok": sample_2y_ok,
        "weekly_opens_gate_ok": weekly_ok,
        "ai_stop_cluster_risk_by_provider": risk_map,
        "reviews": reviews,
        "fail_reasons": fail_reasons,
        "skipped_infra_providers": skipped,
        "voting_providers": voters,
        "natural_language": (
            "三AI理论复核%s：有效投票%s家均值胜率 %.1f%%%s%s%s；%s"
            % ("通过" if approved else "未通过",
               len(voters), avg, mean_txt, weekly_txt, nl_extra,
               "；".join(fail_reasons) if fail_reasons else "有效投票方均达标")
        ),
    }


def validate_theoretical_review_result(review):
    """Validate the complete review before any human-confirm queue write."""
    row = dict(review or {})
    reasons = []
    if row.get("schema") != "qiyu_three_ai_theoretical_review_v2":
        reasons.append("review_schema_missing_or_legacy")
    by_provider = {}
    for item in row.get("reviews") or []:
        if isinstance(item, dict) and item.get("provider"):
            by_provider[_normalize_provider_name(item.get("provider"))] = item
    required = {_normalize_provider_name(p) for p in PROVIDERS}
    actual = set(by_provider)
    if actual != required:
        reasons.append(
            "three_ai_votes_incomplete(required=%s,actual=%s)"
            % (",".join(sorted(required)), ",".join(sorted(actual)) or "-")
        )
    for provider in sorted(required):
        if not _provider_theoretical_pass(by_provider.get(provider) or {}):
            reasons.append("provider_not_pass:%s" % provider)
    weekly_pack = row.get("statistical_weekly_opens") or {}
    stat_weekly = _sf_num(weekly_pack.get("expected_weekly_fills"))
    if stat_weekly is None:
        stat_weekly = _sf_num(row.get("statistical_weekly_opens_expected"))
    theo_weekly = _sf_num(row.get("ai_theoretical_weekly_opens_avg"))
    if theo_weekly is None:
        # Fallback: mean provider theoretical weekly from reviews
        vals = []
        for item in row.get("reviews") or []:
            if not isinstance(item, dict):
                continue
            v = _sf_num(item.get("theoretical_weekly_opens"))
            if v is not None:
                vals.append(v)
        if vals:
            theo_weekly = sum(vals) / float(len(vals))
    if theo_weekly is None or theo_weekly < MIN_THEORETICAL_WEEKLY_OPENS - 1e-9:
        reasons.append(
            "weekly_opens_lt_%s(got=%s)"
            % (MIN_THEORETICAL_WEEKLY_OPENS,
               "missing" if theo_weekly is None else "%.6f" % theo_weekly)
        )
    if stat_weekly is None:
        reasons.append("weekly_statistical_anchor_missing")
    if not weekly_opens_2y_sample_ok(weekly_pack):
        reasons.append(
            "weekly_2y_sample_required(got_span=%s,method=%s)"
            % (
                weekly_pack.get("span_days")
                or weekly_pack.get("observed_days")
                or "missing",
                weekly_pack.get("method") or "missing",
            )
        )
    if weekly_pack.get("ai_may_override") is not False:
        reasons.append("weekly_override_forbidden_flag_missing")
    if weekly_pack.get("ai_may_discount") is not True:
        reasons.append("weekly_discount_contract_missing")
    if not row.get("approved"):
        reasons.append("aggregate_not_approved")
    return {
        "ok": not reasons,
        "approved": not reasons,
        "reasons": reasons,
        "required_providers": sorted(required),
        "actual_providers": sorted(actual),
        "statistical_weekly_opens_expected": stat_weekly,
        "ai_theoretical_weekly_opens_avg": theo_weekly,
        "weekly_opens_2y_sample_ok": weekly_opens_2y_sample_ok(weekly_pack),
        "min_weekly_opens": MIN_THEORETICAL_WEEKLY_OPENS,
    }


LIVE_RETROSPECTIVE_AUDIT_PROMPT = """你是现存实盘策略的独立追溯审计员。你不会看到另外两家AI的结论。
输入包含策略精确逻辑归档、真实仓位参数、净成本回测、三倍实际摩擦回测、极端行情统计和死亡知识。禁止修改策略、禁止因其已经实盘而降低标准。
你必须分别回答：成本后理论机制是否可能为正期望；有哪些未经证实的假设；是否命中已提供的死亡知识；并执行三轮反事实攻击：
1 inventory_toxic_flow：库存厌恶与毒性订单流导致价差、深度恶化；
2 crowding_adaptation：学习、拥挤、抢跑和参数适应；
3 regime_execution_shift：市场环境切换、延迟和执行失配。
只有证据完整、三倍成本净均值为正、没有未解决硬伤且三轮均SURVIVE时才可PASS。历史微观回放缺失必须标记UNCERTAIN或FAIL，不能想象数据。
仅输出一个JSON对象：
{"strategy_hash":"原样复述","verdict":"PASS或FAIL或UNCERTAIN","confidence":0到1,
"cost_expectancy":"POSITIVE或NEGATIVE或UNPROVEN","unproven_assumptions":["..."],
"death_matches":[{"rule_id":"...","reason":"..."}],
"attacks":[{"attack_id":"inventory_toxic_flow","verdict":"SURVIVE或FAIL","reason":"..."},{"attack_id":"crowding_adaptation","verdict":"SURVIVE或FAIL","reason":"..."},{"attack_id":"regime_execution_shift","verdict":"SURVIVE或FAIL","reason":"..."}],
"reason":"中文结论"}。"""


def retrospective_live_review_one(name, manifest, evidence, _retry=True):
    cfg = _provider_config(name)
    digest = candidate_hash(manifest)
    consent = external_research_consent_status(name, "final_review")
    if not consent.get("allowed") or not cfg["api_key"]:
        return {"provider": name, "ok": False, "strategy_hash": digest,
                "verdict": "FAIL", "reason": "外部AI授权或密钥不可用"}
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": LIVE_RETROSPECTIVE_AUDIT_PROMPT},
            {"role": "user", "content": canonical_json({
                "strategy_hash": digest, "strategy_manifest": manifest,
                "deterministic_evidence": evidence})},
        ],
    }
    if name == "deepseek":
        body["response_format"] = {"type": "json_object"}
    body["temperature"] = 0
    body["max_tokens"] = 1800
    started = time.time()
    try:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib_request.Request(
            cfg["url"], data=encoded,
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        response = urllib_request.urlopen(request, timeout=cfg["timeout"])
        raw = json.loads(response.read().decode("utf-8"))
        content = (((raw.get("choices") or [{}])[0].get("message") or {})
                   .get("content") or "").strip()
        parsed = _parse_content_json(content)
        verdict = str(parsed.get("verdict") or "").upper()
        attacks = parsed.get("attacks") or []
        attack_map = {str(row.get("attack_id")): str(row.get("verdict") or "").upper()
                      for row in attacks if isinstance(row, dict)}
        required = {"inventory_toxic_flow", "crowding_adaptation",
                    "regime_execution_shift"}
        exact = str(parsed.get("strategy_hash") or "") == digest
        complete = set(attack_map) == required and all(
            value in ("SURVIVE", "FAIL") for value in attack_map.values())
        ok = bool(exact and complete and verdict in ("PASS", "FAIL", "UNCERTAIN"))
        return {
            "provider": name, "model": cfg["model"], "ok": ok,
            "strategy_hash": digest,
            "verdict": verdict if ok else "FAIL",
            "confidence": parsed.get("confidence"),
            "cost_expectancy": parsed.get("cost_expectancy"),
            "unproven_assumptions": parsed.get("unproven_assumptions") or [],
            "death_matches": parsed.get("death_matches") or [],
            "attacks": attacks if complete else [],
            "reason": parsed.get("reason") or (
                "返回格式、哈希或三轮攻击不完整" if not ok else ""),
            "latency_sec": round(time.time()-started, 3),
        }
    except Exception as exc:
        if _retry:
            return retrospective_live_review_one(
                name, manifest, evidence, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "strategy_hash": digest, "verdict": "FAIL",
                "reason": "追溯审计调用失败: %s" % exc,
                "latency_sec": round(time.time()-started, 3)}


def retrospective_live_unanimous_review(manifest, evidence):
    digest = candidate_hash(manifest)
    pool = ThreadPoolExecutor(max_workers=len(PROVIDERS))
    try:
        reviews = [future.result() for future in [
            pool.submit(retrospective_live_review_one, provider, manifest, evidence)
            for provider in PROVIDERS]]
    finally:
        pool.shutdown(wait=True)
    passed = all(
        row.get("ok") and row.get("strategy_hash") == digest
        and row.get("verdict") == "PASS"
        and all(str(attack.get("verdict") or "").upper() == "SURVIVE"
                for attack in row.get("attacks") or [])
        for row in reviews)
    return {"strategy_hash": digest, "passed": passed,
            "policy": "three_ai_independent_unanimous_live_retrospective_audit",
            "reviews": reviews}


RESEARCH_ROLES = {
    "deepseek": "侧重市场形态、环境切换、成功失败案例的结构性差异，提出可证伪假设。",
    "qwen": "侧重条件级反事实、频率与胜率权衡、参数敏感性，避免过拟合与重复失败。",
    "glm": "侧重生命周期、实盘偏差、风险收益与可执行性，提出稳健且可审计假设。",
}

ACTIVE_HUNT_ROLES = {
    "deepseek": "市场机制猎手：判断形态在何种可观测环境下才可能覆盖摩擦成本。",
    "qwen": "博弈与反事实猎手：寻找最小必要条件，并优先发现会让假设立即死亡的反例。",
    "glm": "统计实验猎手：控制样本选择偏差、盈亏平衡约束和每次探针的信息增益。",
}

ACTIVE_HUNT_PROMPT = """你是主动狩猎式正向样本系统中的独立生存可行性审查员，看不到其他AI答案。
输入只包含真实事件图谱候选和系统可计算的受控过滤器。你不得创造新指标、逐笔订单流、200毫秒OFI、宏观日历或历史L2数据；未列入available_filter_catalog的条件一律不可使用。
若prior_probe_history已有相同pattern_id与过滤组合的失败探针，除非提出不同的必要过滤组合并说明新信息来源，否则应REJECT，禁止重复消耗算力。
先阅读failed_branch_clusters中的共同死因。mutation_operator_ids必须针对其中一个有数据计数的死因，reason中写明死因代码；不能以故事代替失败统计。
再阅读prescreen_death_map。若同一pattern_id存在重复历史死因，HUNT必须用mutation_operator_catalog中明确覆盖该死因的算子，并在death_avoidance_explanation中说明算子如何规避已测死因；没有对应算子时REJECT。不得把AI原文否决理由冒充确定性统计。
slow_microstructure_primitives仅是无结果标签的描述词典，不是盈利证据，也不能写入当前历史OHLCV探针条件。候选必须依赖在5秒、15秒、60秒采样和分钟快照下仍可观察的慢微观结构，不得依赖亚5秒速度、排队位置或撤单流。
unlabeled_micro_observation_context包含尚未注册的稳定聚类和前向共现统计。聚类本身不得命名、不得视为交易特征、不得用于历史样本过滤。只有associations中明确标记eligible=true的前向统计共现（死因-微观共现，或与价差跳变/深度下降的无价格结果标签微观事件共现），才允许在micro_background_plan.avoid_cluster_ids中列出；该计划仅供未来影子观察，不参与本次历史探针通过。没有合格共现时必须返回空列表。
operator_lattice_screen是同一标的、同一周期先通过完整成本筛选后，系统在开发段对已注册算子组合进行的三倍摩擦确定性搜索。只有eligible_for_ai_review=true的组合可以被优先采用，且required_filter_ids必须逐字使用其filter_ids；它仍不是正例、不是探针通过，也未使用封存30%数据。你必须独立攻击其样本选择、留出稳定性和成本覆盖，不得因该字段存在而降低任何门槛。若状态为dormant或eligible_count为0，不得把它描述为支持证据。
distilled_death_rule_navigation是跨目标失败知识的描述性规则树，只能用于安排搜索顺序和提出可证伪问题。automatic_pruning_allowed=false的规则绝不能用于拒绝本目标候选；必须等待本标的、本周期的两次独立确定性失败、可编译过滤器影响审计以及Qwen/OpenAI双批准。跨目标共现不是本目标证据。
registered_micro_navigation_policy规定注册基元只能形成未来影子分层问题。即使基元已经获得描述性名称，也不得称其为理想或危险环境，不得写入历史过滤器、DSL或实盘触发；只有未来同期统计形成合格关联后才可作为观察性警告。
在其他证据质量相当时，优先HUNT“认知空白”：即该pattern_id尚无prescreen_death_map死因，且没有合格的前向死亡-微观共现警告。这只是搜索顺序，不得提高可行性分数或降低任何准入门槛。候选仍必须由现有DSL特征、受控过滤器和可证伪机制完整描述；未标注聚类不得被当作“可解释基元”。
你必须对probe_catalog中的每个pattern_id分别作出HUNT或REJECT，不得遗漏或新增pattern_id。
HUNT不表示盈利或正例，只表示值得进行一次三倍摩擦成本的最小探针。
hard_veto只能用于未来数据泄漏、输入不可观测、成本算术不可能、机制与方向矛盾或样本明显不足；不得因个人偏好设置硬否决。
每个HUNT必须选择1至3个required_filter_ids，全部来自available_filter_catalog，并给出三轮适应性攻击：①库存厌恶/毒性流环境下的流动性撤退、价差与深度恶化；②参与者学习、拥挤、抢跑与参数适应；③市场环境切换、延迟与执行失配。三轮均需给出SURVIVE或FAIL。这里的对手盘只是基于公开机制常识的反事实逻辑攻击，绝不冒充历史实证；攻击结论不得直接写入实证死亡地图，只有之后真实确定性探针或前向观察复现时才可形成经验死亡证据。REJECT的adaptation_rounds可以为空数组，以节省输出，但pattern_id仍绝不能遗漏。
你还可以为HUNT选择0至2个mutation_operator_ids，但只能来自mutation_operator_catalog。这些是对失败模式的受控微小变异，不是新策略；不得自行发明算子。
counterfactual_blueprint只是未标注的理论灯塔，不得称为成功案例、训练正例或宣称胜率。
严格输出JSON：
{"reviews":[{"pattern_id":"原样", "decision":"HUNT或REJECT", "viability_score":0到100,
"required_filter_ids":["..."], "mutation_operator_ids":["..."], "hard_veto":false, "fatal_flaws":["..."],
"break_even":{"min_win_rate_pct":数字,"min_payoff_ratio":数字},
"adaptation_rounds":[{"round":1,"attack":"...","verdict":"SURVIVE或FAIL","reason":"..."},{"round":2,"attack":"...","verdict":"SURVIVE或FAIL","reason":"..."},{"round":3,"attack":"...","verdict":"SURVIVE或FAIL","reason":"..."}],
"counterfactual_blueprint":"只描述可测量特征组合，不声称历史存在", "death_avoidance_explanation":"针对已测死因的说明；无历史则写无", "micro_background_plan":{"avoid_cluster_ids":["仅限合格前向共现ID"],"observation_only":true,"reason":"..."}, "reason":"..."}]}
"""

RESEARCH_PROMPT = """你是栖语量化系统的独立研究AI。你看不到另外两位AI的输出。
根据给定的真实案例摘要、市场环境、条件归因、失败记忆和当前策略元数据，提出0至3个可回测假设。
不要批准策略，不要写Python，不要触碰止损、杠杆、仓位或组合风险参数。
参数假设格式：{"kind":"parameter","strategy_key":"...","parameter":"已存在参数",
"direction":-1或1,"step_count":1到3,"rationale":"...","expected_effect":"..."}
参数假设只能使用输入中tunable_parameters明确列出的参数；列表为空时不得提出任何参数假设。
逻辑假设只能使用qiyu_strategy_dsl_v1，特征限于OHLC、EMA6/7/8/16/17/19/21/23/32/38/53/75/95/200、K/D/J/CCI/MACD_STICK/ATR14/RSI14/Z20及H1字段；
运算符限lt/lte/gt/gte/eq/between/cross_above/cross_below；入口至少两个条件，布尔节点限all/any/not。
布尔节点必须只含一个键，例如{"all":[...]}，不得在all/any/not同层添加id、name、description或其他字段；只有叶子条件可以包含id。
逻辑假设格式：{"kind":"dsl","strategy_key":"必须与dsl.key相同的新键","rationale":"...","source_pattern_ids":["仅填写证据中真实存在的pattern_id"],"causal_chain":{"market_state":"...","mechanism":"...","observable_transition":"...","why_cost_survives":"...","failure_condition":"..."},"falsification_tests":["..."],"dsl":{完整DSL对象}}
完整DSL对象必须包含schema/key/name/direction/timeframe/supported_instruments/max_hold_bars/entry/exit；schema必须等于qiyu_strategy_dsl_v1。禁止只返回entry/exit片段。
causal_chain五个字段和至少两条falsification_tests缺一不可；因果链只能引用输入中真实存在的数据。系统当前没有历史逐笔订单簿、成交量或做市商撤单数据，因此不得声称“订单簿失衡、流动性真空、做市商被迫撤单”等无法由DSL观测的机制。why_cost_survives必须引用deterministic_event_atlas中的基础/压力成本证据。
exit中的每一个叶子条件必须写明role：真正获利目标写"take_profit"，趋势/形态失效退出写"invalidation"。不得把多头跌破均线或空头突破均线包装成止盈；不得把失效退出描述为“给予盈利空间”。
当strategy_metadata.creation_mode为true时，这是从零创造新策略的任务：不得提出parameter假设；每个DSL必须使用明确中文name和description，优先补足现有组合缺少的方向或市场环境，并服务于提高有效交易频率。不得仅改名复制existing_strategies_to_avoid_duplicates中的逻辑，也不得声称未经回测的胜率。
若context含deterministic_event_atlas，必须先阅读其中目标标的真实数据产生的固定前向窗口证据。新逻辑应以atlas中的qualified_patterns为主要事件骨架，并在rationale中引用pattern_id、样本数、全样本与留出段扣费后胜率/均值；不得只凭指标名称另编故事。若qualified_patterns为空，可以返回空数组；只有在top_observed中存在明确可组合的互补证据时才可提出待预筛实验，并须直说尚未达到可信门槛。
若context含active_hunt且其中存在suspected_positive_probe，可以基于其pattern_id与agreed_filter_ids提出一个待严格验证DSL；此时source_pattern_ids必须逐字填写实际采用的疑似正例pattern_id。必须明确它只是在三倍成本窄窗口中存活的“疑似正例”，并非已验证正例。counterfactual_blueprint不得作为盈利证据，similarity结果不得替代exact窗口，且不得把系统尚未积累的微观结构字段写入DSL。
创造策略的entry必须至少包含一个一次性事件触发（优先cross_above/cross_below，或本根与前一根offset条件共同描述真实转折）；不得只用会连续多根成立的均线排列、阈值和价格状态，否则会在持仓释放后重复入场并形成手续费与止损簇。逻辑叙事不等于统计优势；不得写“有望达到目标胜率”等无实证断言。
所有数值阈值必须与context.feature_value_reference中的真实量纲和分位数一致；h1_slope4是小数收益率（0.01代表1%），不得写成1、5、20等百分数式阈值。若failure_memory含确定性回测失败DSL及指标，必须针对其零信号、低频、低胜率、负期望、留出集坍塌或止损簇原因作实质修正，不得只改名称。
创造模式每位研究者最多只提交1个自己认为最值得回测的完整DSL；没有可靠方案时返回空数组。优先保证结构完整，不要用冗长文字解释。
若context包含collaborative_revision_task，则不要另起无关方案；必须依据base_dsl和匿名review_feedback修订为一个新的完整DSL，保留合理核心并实质解决反对意见。
若context包含backtest_revision_task，则这是第2或第3轮实证纠错：必须以base_dsl为起点，逐项读取0.9%主止损、0.6%辅止损、留出集、市场环境分解、最大连亏、全样本feature_outcome_comparison、exit_type_performance和典型胜负案例；只能提交一个实质修订后的完整DSL。优先采用全样本胜负分位确有分离度的特征，若胜负分布高度重叠则不得把该特征包装成新过滤器；必须说明修改针对哪项失败证据，禁止只改名、凭感觉重复原逻辑或声称保证胜率。若结构性错误无法修复，返回空数组。
若context同时包含multi_ai_failure_analysis，你是本轮唯一的“策略架构师”：必须综合三份匿名诊断中的共同证据与分歧，只能沿用上一轮确定性冠军base_dsl，不得以较差候选为父版本。若诊断多数建议ABANDON，或无法提出同时保护样本量、胜率和期望的可证伪修改，返回空数组。rationale必须明确列出相对父版本保留、删除、增加的条件及预期改善指标。
严格输出JSON：{"hypotheses":[...]}; 没有可靠假设时输出空数组。不要重复失败记忆中的方案。"""


def _parse_content_json(text):
    text = str(text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    candidates = [text]
    # Providers may put a private reasoning block before the requested JSON,
    # or wrap the final object in a fenced block that is not at byte zero.
    # Extract complete fenced/balanced objects rather than slicing from the
    # first brace in the reasoning to the last brace in the answer.
    candidates.extend(match.group(1).strip() for match in re.finditer(
        r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE))
    balanced = []
    depth = 0; start_at = None; in_string = False; escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start_at = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start_at is not None:
                balanced.append(text[start_at:index+1])
                start_at = None
    candidates.extend(reversed(balanced))
    start = text.find("{"); end = text.rfind("}")
    if start >= 0 and end > start and text[start:end+1] != text:
        candidates.append(text[start:end+1])
    last_error = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception as exc:
            last_error = exc
        # Some OpenAI-compatible providers occasionally return a Python-style
        # literal (single quotes/trailing commas) despite an explicit JSON-only
        # prompt.  literal_eval accepts data literals only; it cannot execute
        # functions, imports, attribute access or arbitrary code.  Round-trip
        # through JSON to reject non-JSON objects before they reach the engine.
        try:
            value = ast.literal_eval(candidate)
            return json.loads(json.dumps(value, ensure_ascii=False))
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ValueError("empty structured AI response")


def research_one(name, context, strategy_metadata, _retry=True):
    cfg = _provider_config(name)
    consent = external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed"):
        return {"provider": name, "ok": False, "hypotheses": [],
                "error": "外部AI研究授权缺失或范围不足",
                "consent_missing": True}
    if not cfg["api_key"]:
        return {"provider": name, "ok": False, "hypotheses": [],
                "error": "API密钥未配置"}
    tunable = sorted((strategy_metadata.get("param_meta") or {}).keys())
    assignment = (context.get("assignment") or {}) if isinstance(context, dict) else {}
    dsl_skeleton = {
        "schema": "qiyu_strategy_dsl_v1", "key": "new_unique_key",
        "name": "中文策略名", "direction": "long或short",
        "timeframe": assignment.get("timeframe"),
        "supported_instruments": [assignment.get("symbol")],
        "max_hold_bars": 8,
        "entry": {"all": [
            {"id": "condition_1", "left": {"feature": "close"},
             "op": "gt", "right": {"feature": "ema6"}},
            {"id": "condition_2", "left": {"feature": "cci"},
             "op": "gt", "right": {"value": 20}},
        ]},
        "exit": {"any": [
            {"id": "exit_1", "left": {"feature": "j"},
             "op": "gt", "right": {"value": 90},
             "role": "take_profit"},
        ]},
    }
    payload = {"role_focus": RESEARCH_ROLES[name], "context": context,
               "strategy_metadata": strategy_metadata,
               "tunable_parameters": tunable,
               "required_dsl_skeleton_example": dsl_skeleton}
    body = {"model": cfg["model"], "messages": [
        {"role": "system", "content": RESEARCH_PROMPT + "\n" + RESEARCH_ROLES[name]},
        {"role": "user", "content": canonical_json(payload)},
    ]}
    if name == "deepseek":
        body["response_format"] = {"type": "json_object"}
    body["temperature"] = 0.2
    body["max_tokens"] = 5000
    started = time.time()
    try:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(cfg["url"], data=encoded,
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        response = urllib_request.urlopen(req, timeout=max(cfg["timeout"], 180))
        raw = json.loads(response.read().decode("utf-8"))
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        content = message.get("content") or message.get("reasoning_content") or ""
        parsed = _parse_content_json(content)
        hypotheses = parsed.get("hypotheses") if isinstance(parsed, dict) else []
        if not isinstance(hypotheses, list):
            raise ValueError("hypotheses must be list")
        return {"provider": name, "model": cfg["model"], "ok": True,
                "hypotheses": hypotheses[:3],
                "latency_sec": round(time.time()-started, 3)}
    except Exception as exc:
        if _retry:
            return research_one(name, context, strategy_metadata, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "hypotheses": [], "error": str(exc),
                "latency_sec": round(time.time()-started, 3)}


def independent_research(context, strategy_metadata):
    pool = ThreadPoolExecutor(max_workers=len(PROVIDERS))
    try:
        futures = [pool.submit(research_one, name, context, strategy_metadata)
                   for name in PROVIDERS]
        results = [future.result() for future in futures]
    finally:
        pool.shutdown(wait=True)
    return {"ok": any(row.get("ok") for row in results),
            "policy": "three_independent_researchers_no_cross_answer_visibility",
            "results": results,
            "hypothesis_count": sum(len(row.get("hypotheses") or []) for row in results)}


def active_hunt_one(name, hunt_context, _retry=True):
    """Review a finite probe catalog; cannot invent executable strategy code."""
    cfg = _provider_config(name)
    consent = external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed"):
        return {"provider": name, "ok": False, "reviews": [],
                "error": "外部AI研究授权缺失或范围不足"}
    if not cfg["api_key"]:
        return {"provider": name, "ok": False, "reviews": [],
                "error": "API密钥未配置"}
    pattern_ids = set(str(row.get("pattern_id")) for row in
                      hunt_context.get("probe_catalog") or [])
    filter_ids = set(str(row.get("filter_id")) for row in
                     hunt_context.get("available_filter_catalog") or [])
    operator_ids = set(str(row.get("operator_id")) for row in
                       hunt_context.get("mutation_operator_catalog") or [])
    micro_context = hunt_context.get("unlabeled_micro_observation_context") or {}
    cluster_ids = set(str(row.get("cluster_id")) for row in
                      micro_context.get("clusters") or [])
    eligible_cluster_ids = set(str(row.get("cluster_id")) for row in
                               micro_context.get("associations") or []
                               if row.get("eligible"))
    payload = {"role_focus": ACTIVE_HUNT_ROLES[name],
               "hunt_context": hunt_context}
    body = {"model": cfg["model"], "messages": [
        {"role": "system", "content": ACTIVE_HUNT_PROMPT + "\n" +
         ACTIVE_HUNT_ROLES[name]},
        {"role": "user", "content": canonical_json(payload)},
    ]}
    if name == "deepseek":
        body["response_format"] = {"type": "json_object"}
        # This stage is a finite catalog classifier, not strategy synthesis.
        # V4-Pro defaults to thinking mode, which can consume the output
        # budget before the mandatory complete JSON panel is emitted.  Keep
        # deep thinking for hypothesis generation/final audits; use the
        # model's non-thinking structured mode for this bounded prefilter.
        body["thinking"] = {"type": "disabled"}
    body["temperature"] = 0
    body["max_tokens"] = 5000
    started = time.time()
    try:
        req = urllib_request.Request(
            cfg["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        response = urllib_request.urlopen(req, timeout=max(cfg["timeout"], 180))
        raw = json.loads(response.read().decode("utf-8"))
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        content = message.get("content") or message.get("reasoning_content") or ""
        parsed = _parse_content_json(content)
        source = parsed.get("reviews") if isinstance(parsed, dict) else None
        if not isinstance(source, list):
            raise ValueError("reviews must be list")
        normalized = []
        for item in source:
            if not isinstance(item, dict) or str(item.get("pattern_id")) not in pattern_ids:
                continue
            decision = str(item.get("decision") or "").upper()
            filters = [str(value) for value in item.get("required_filter_ids") or []
                       if str(value) in filter_ids]
            operators = [str(value) for value in item.get("mutation_operator_ids") or []
                         if str(value) in operator_ids]
            micro_plan = item.get("micro_background_plan") or {}
            avoid_clusters = [str(value) for value in
                              micro_plan.get("avoid_cluster_ids") or []
                              if str(value) in cluster_ids
                              and str(value) in eligible_cluster_ids]
            rounds = item.get("adaptation_rounds") or []
            rounds_ok = (decision == "REJECT" and not rounds) or (
                len(rounds) == 3 and
                set(int(row.get("round") or 0) for row in rounds) == {1, 2, 3}
                and all(str(row.get("verdict") or "").upper()
                        in ("SURVIVE", "FAIL") for row in rounds))
            score = float(item.get("viability_score") or 0.0)
            if (decision not in ("HUNT", "REJECT") or not 0 <= score <= 100
                    or not rounds_ok or (decision == "HUNT" and not 1 <= len(filters) <= 3)):
                continue
            normalized.append({
                "pattern_id": str(item["pattern_id"]), "decision": decision,
                "viability_score": score, "required_filter_ids": filters[:3],
                "mutation_operator_ids": operators[:2],
                "hard_veto": bool(item.get("hard_veto")),
                "fatal_flaws": [str(value)[:300] for value in
                                (item.get("fatal_flaws") or [])[:5]],
                "break_even": item.get("break_even") or {},
                "adaptation_rounds": rounds,
                "counterfactual_blueprint": str(
                    item.get("counterfactual_blueprint") or "")[:1200],
                "death_avoidance_explanation": str(
                    item.get("death_avoidance_explanation") or "")[:800],
                "micro_background_plan": {
                    "avoid_cluster_ids": avoid_clusters[:3],
                    "observation_only": True,
                    "reason": str(micro_plan.get("reason") or "")[:700]},
                "reason": str(item.get("reason") or "")[:1200],
            })
        unique = {row["pattern_id"]: row for row in normalized}
        if set(unique) != pattern_ids:
            raise ValueError("provider omitted or duplicated probe_catalog patterns")
        return {"provider": name, "model": cfg["model"], "ok": True,
                "reviews": [unique[key] for key in sorted(unique)],
                "latency_sec": round(time.time()-started, 3)}
    except Exception as exc:
        if _retry:
            return active_hunt_one(name, hunt_context, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "reviews": [], "error": str(exc),
                "latency_sec": round(time.time()-started, 3)}


def active_hunt_priors(hunt_context):
    pool = ThreadPoolExecutor(max_workers=len(PROVIDERS))
    try:
        futures = [pool.submit(active_hunt_one, name, hunt_context)
                   for name in PROVIDERS]
        results = [future.result() for future in futures]
    finally:
        pool.shutdown(wait=True)
    return {"ok": all(row.get("ok") for row in results),
            "policy": "three_independent_controlled_survival_necessity_reviews",
            "results": results}


PRESCREEN_DEATH_PROMPT = """你是初筛死亡地图整理员，不生成策略。
输入同时含确定性回测死因与AI原文否决。必须保持stage边界：不得把AI推理写成统计事实，也不得从OHLCV推断历史L2、排队、撤单流或200毫秒OFI。
只能使用allowed_death_codes。任务是把重复死因压缩成可检索的地形描述，并指出下一次受控变异必须回答的可证伪问题；不得提出盈利、胜率或入场结论。
严格输出JSON：{"terrain_cells":[{"pattern_id":"原样","death_cause_code":"受控代码","evidence_stage":"deterministic_atlas或three_ai_prescreen或mixed","observable_summary":"...","required_countermeasure_question":"...","confidence":"measured或reasoned"}],"global_notes":["..."]}。
"""


def prescreen_death_analysis(context, _retry=True):
    """DeepSeek semantic compression of recorded deaths; never a label source."""
    name = "deepseek"; cfg = _provider_config(name)
    consent = external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed") or not cfg["api_key"]:
        return {"provider": name, "ok": False,
                "error": "外部AI授权或密钥缺失"}
    body = {"model": cfg["model"], "messages": [
        {"role": "system", "content": PRESCREEN_DEATH_PROMPT},
        {"role": "user", "content": canonical_json(context)},
    ], "response_format": {"type": "json_object"}, "max_tokens": 8000,
       "thinking": {"type": "enabled" if _retry else "disabled"}}
    started = time.time()
    try:
        req = urllib_request.Request(
            cfg["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        raw = json.loads(urllib_request.urlopen(
            req, timeout=max(cfg["timeout"], 180)).read().decode("utf-8"))
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        parsed = _parse_content_json(message.get("content") or
                                     message.get("reasoning_content") or "")
        allowed = set(str(value) for value in context.get("allowed_death_codes") or [])
        pattern_ids = set(str(row.get("pattern_id")) for row in
                          context.get("clusters") or [])
        cells = []
        for row in parsed.get("terrain_cells") or []:
            if (not isinstance(row, dict)
                    or str(row.get("pattern_id")) not in pattern_ids
                    or str(row.get("death_cause_code")) not in allowed):
                continue
            stage = str(row.get("evidence_stage") or "")
            confidence = str(row.get("confidence") or "")
            if stage not in ("deterministic_atlas", "three_ai_prescreen", "mixed"):
                continue
            if confidence not in ("measured", "reasoned"):
                continue
            cells.append({"pattern_id": str(row["pattern_id"]),
                          "death_cause_code": str(row["death_cause_code"]),
                          "evidence_stage": stage, "confidence": confidence,
                          "observable_summary": str(row.get(
                              "observable_summary") or "")[:700],
                          "required_countermeasure_question": str(row.get(
                              "required_countermeasure_question") or "")[:700]})
        return {"provider": name, "model": cfg["model"], "ok": True,
                "terrain_cells": cells,
                "global_notes": [str(value)[:500] for value in
                                 (parsed.get("global_notes") or [])[:8]],
                "policy": "semantic_map_only_no_profit_label",
                "latency_sec": round(time.time()-started, 3)}
    except Exception as exc:
        if _retry:
            return prescreen_death_analysis(context, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "error": str(exc), "latency_sec": round(time.time()-started, 3)}


MICRO_PRIMITIVE_DISCOVERY_PROMPT = """你是慢微观结构描述词典的概念整理员。
输入是无涨跌、无盈亏标签的聚类中心与代表窗口。你只能给重复的可观测状态命名，不得预测价格、生成策略、提出入场，或使用做市商意图、吸筹、护盘、撤单、排队等输入无法证明的词。
所有描述必须在分钟盘口快照及5秒/15秒/60秒最近成交子窗口下可复核。mechanism_hypothesis只能写“待验证描述性假设”，不得写成事实。
严格输出JSON：{"primitives":[{"cluster_id":"原样","label":"简短中文中性名称","description":"只写可观测变化","mechanism_hypothesis":"待验证描述性假设或空","forbidden_prediction":false}]}。
"""

MICRO_PRIMITIVE_AUDIT_PROMPT = """你是慢微观结构词典审计员，不能改写概念。
逐项检查：是否完全可由给定采样字段观察；是否偷偷包含涨跌、盈利、胜率、买卖入场或不可见参与者意图；是否误称连续逐笔、历史L2、队列或撤单数据；是否把聚类当成Alpha。
严格输出JSON：{"audits":[{"cluster_id":"原样","decision":"APPROVE或REJECT","flags":["..."],"reason":"..."}]}。任一越界必须REJECT。
"""


def _micro_primitive_propose(context, _retry=True):
    name = "deepseek"; cfg = _provider_config(name)
    consent = external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed") or not cfg["api_key"]:
        return {"provider": name, "ok": False, "primitives": [],
                "error": "外部AI授权或密钥缺失"}
    cluster_ids = set(str(row.get("cluster_id")) for row in
                      context.get("clusters") or [])
    body = {"model": cfg["model"], "messages": [
        {"role": "system", "content": MICRO_PRIMITIVE_DISCOVERY_PROMPT},
        {"role": "user", "content": canonical_json(context)},
    ], "response_format": {"type": "json_object"}, "max_tokens": 7000,
       "thinking": {"type": "enabled" if _retry else "disabled"}}
    started = time.time()
    try:
        req = urllib_request.Request(
            cfg["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        raw = json.loads(urllib_request.urlopen(
            req, timeout=max(cfg["timeout"], 180)).read().decode("utf-8"))
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        parsed = _parse_content_json(message.get("content") or
                                     message.get("reasoning_content") or "")
        rows = []
        for item in parsed.get("primitives") or []:
            cluster_id = str(item.get("cluster_id") or "")
            if cluster_id not in cluster_ids or bool(item.get("forbidden_prediction")):
                continue
            rows.append({"cluster_id": cluster_id,
                         "label": str(item.get("label") or "")[:80],
                         "description": str(item.get("description") or "")[:800],
                         "mechanism_hypothesis": str(item.get(
                             "mechanism_hypothesis") or "")[:500],
                         "forbidden_prediction": False})
        unique = {row["cluster_id"]: row for row in rows}
        if set(unique) != cluster_ids:
            raise ValueError("proposal omitted primitive clusters")
        return {"provider": name, "model": cfg["model"], "ok": True,
                "primitives": [unique[key] for key in sorted(unique)],
                "latency_sec": round(time.time()-started, 3)}
    except Exception as exc:
        if _retry:
            return _micro_primitive_propose(context, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "primitives": [], "error": str(exc),
                "latency_sec": round(time.time()-started, 3)}


def _micro_primitive_audit_one(name, context, _retry=True):
    cfg = _provider_config(name)
    consent = external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed") or not cfg["api_key"]:
        return {"provider": name, "ok": False, "audits": [],
                "error": "外部AI授权或密钥缺失"}
    cluster_ids = set(str(row.get("cluster_id")) for row in
                      context.get("proposed_primitives") or [])
    body = {"model": cfg["model"], "messages": [
        {"role": "system", "content": MICRO_PRIMITIVE_AUDIT_PROMPT},
        {"role": "user", "content": canonical_json(context)},
    ]}
    body.update({"temperature": 0, "max_tokens": 3500})
    started = time.time()
    try:
        req = urllib_request.Request(
            cfg["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        raw = json.loads(urllib_request.urlopen(
            req, timeout=max(cfg["timeout"], 180)).read().decode("utf-8"))
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        parsed = _parse_content_json(message.get("content") or
                                     message.get("reasoning_content") or "")
        audits = []
        for item in parsed.get("audits") or []:
            cluster_id = str(item.get("cluster_id") or "")
            decision = str(item.get("decision") or "").upper()
            if cluster_id not in cluster_ids or decision not in ("APPROVE", "REJECT"):
                continue
            audits.append({"cluster_id": cluster_id, "decision": decision,
                           "flags": [str(value)[:250] for value in
                                     (item.get("flags") or [])[:8]],
                           "reason": str(item.get("reason") or "")[:700]})
        unique = {row["cluster_id"]: row for row in audits}
        if set(unique) != cluster_ids:
            raise ValueError("auditor omitted primitive clusters")
        return {"provider": name, "model": cfg["model"], "ok": True,
                "audits": [unique[key] for key in sorted(unique)],
                "latency_sec": round(time.time()-started, 3)}
    except Exception as exc:
        if _retry:
            return _micro_primitive_audit_one(name, context, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "audits": [], "error": str(exc),
                "latency_sec": round(time.time()-started, 3)}


def review_micro_primitive_catalog(context):
    proposal = _micro_primitive_propose(context)
    if not proposal.get("ok"):
        return {"ok": False, "proposal": proposal, "audits": [],
                "registered": [], "policy": "fail_closed_no_descriptive_registration"}
    audit_context = dict(context)
    audit_context["proposed_primitives"] = proposal.get("primitives") or []
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        audits = [future.result() for future in [
            pool.submit(_micro_primitive_audit_one, "qwen", audit_context),
            pool.submit(_micro_primitive_audit_one, "glm", audit_context)]]
    finally:
        pool.shutdown(wait=True)
    by_provider = {row.get("provider"): {
        item.get("cluster_id"): item for item in row.get("audits") or []}
        for row in audits if row.get("ok")}
    registered = []
    for primitive in proposal.get("primitives") or []:
        cluster_id = primitive["cluster_id"]
        decisions = [rows.get(cluster_id, {}).get("decision")
                     for rows in by_provider.values()]
        if len(by_provider) == 2 and decisions == ["APPROVE", "APPROVE"]:
            registered.append(primitive)
    return {"ok": bool(proposal.get("ok") and len(by_provider) == 2),
            "proposal": proposal, "audits": audits,
            "registered": registered,
            "policy": "deepseek_names_qwen_glm_double_audit_no_outcome_labels"}


DEATH_MICRO_COOCCURRENCE_PROMPT = """你是死亡模式与未标注慢微观聚类的共现审计员，不生成策略。
只复述输入中的前向同期计数、背景频率、共现频率、lift和置信区间。不得进行因果推断，不得给聚类命名，不得声称它导致亏损，不得提出入场、盈利、胜率或自动剪枝规则。
若样本不足或eligible=false，必须写insufficient，不得补故事。严格输出JSON：
{"observations":[{"cell_id":"原样","state":"descriptive_cooccurrence或insufficient","summary":"包含输入具体计数与比例","causal_claim":false,"allowed_use":"prospective_shadow_warning_only"}],"global_boundary":"..."}。
"""


def death_micro_cooccurrence_analysis(context, _retry=True):
    name = "deepseek"; cfg = _provider_config(name)
    consent = external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed") or not cfg["api_key"]:
        return {"provider": name, "ok": False, "observations": [],
                "error": "外部AI授权或密钥缺失"}
    cell_ids = set(str(row.get("cell_id")) for row in context.get("cells") or [])
    body = {"model": cfg["model"], "messages": [
        {"role": "system", "content": DEATH_MICRO_COOCCURRENCE_PROMPT},
        {"role": "user", "content": canonical_json(context)},
    ], "response_format": {"type": "json_object"}, "max_tokens": 6000,
       "thinking": {"type": "enabled" if _retry else "disabled"}}
    started = time.time()
    try:
        req = urllib_request.Request(
            cfg["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        raw = json.loads(urllib_request.urlopen(
            req, timeout=max(cfg["timeout"], 180)).read().decode("utf-8"))
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        parsed = _parse_content_json(message.get("content") or
                                     message.get("reasoning_content") or "")
        rows = []
        for item in parsed.get("observations") or []:
            cell_id = str(item.get("cell_id") or "")
            state = str(item.get("state") or "")
            if cell_id not in cell_ids or state not in (
                    "descriptive_cooccurrence", "insufficient"):
                continue
            rows.append({"cell_id": cell_id, "state": state,
                         "summary": str(item.get("summary") or "")[:900],
                         "causal_claim": False,
                         "allowed_use": "prospective_shadow_warning_only"})
        return {"provider": name, "model": cfg["model"], "ok": True,
                "observations": rows,
                "global_boundary": str(parsed.get("global_boundary") or "")[:800],
                "policy": "descriptive_cooccurrence_no_causal_pruning",
                "latency_sec": round(time.time()-started, 3)}
    except Exception as exc:
        if _retry:
            return death_micro_cooccurrence_analysis(context, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "observations": [], "error": str(exc),
                "latency_sec": round(time.time()-started, 3)}


SYSTEM_SOLVABILITY_PROMPT = """你是量化研究系统的独立可解性审计员，看不到其他AI答案。
你只审查规则之间是否逻辑互斥、是否仍允许一个理论证据向量依次通过。不得评价市场是否一定提供该样本，不得提出放松杠杆、止损、成本或验证门槛，不得生成交易策略。
输入中的deterministic_witness是结构性可满足见证，不是盈利证据；若你认为它无效，必须指出具体违反哪条现行规则。死亡规则只作用于已有pattern_id时，不得错误外推为封锁全部未来模式。
严格输出JSON：{"verdict":"FEASIBLE或EMPTY或UNCERTAIN","contradictions":[{"rule_a":"...","rule_b":"...","proof":"..."}],"witness_assessment":"...","unblocked_prototype_shape":"只描述证据结构，不写交易条件","confidence":0到100}。
"""


def _system_solvability_one(name, context, _retry=True):
    cfg = _provider_config(name)
    consent = external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed") or not cfg["api_key"]:
        return {"provider": name, "ok": False, "verdict": "UNCERTAIN",
                "error": "外部AI授权或密钥缺失"}
    body = {"model": cfg["model"], "messages": [
        {"role": "system", "content": SYSTEM_SOLVABILITY_PROMPT},
        {"role": "user", "content": canonical_json(context)},
    ]}
    if name == "deepseek":
        body.update({"response_format": {"type": "json_object"},
                     "max_tokens": 9000,
                     "thinking": {"type": "enabled" if _retry else "disabled"}})
    else:
        body.update({"temperature": 0, "max_tokens": 4500})
    started = time.time()
    try:
        req = urllib_request.Request(
            cfg["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        raw = json.loads(urllib_request.urlopen(
            req, timeout=max(cfg["timeout"], 180)).read().decode("utf-8"))
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        parsed = _parse_content_json(message.get("content") or
                                     message.get("reasoning_content") or "")
        verdict = str(parsed.get("verdict") or "").upper()
        if verdict not in ("FEASIBLE", "EMPTY", "UNCERTAIN"):
            raise ValueError("invalid solvability verdict")
        return {"provider": name, "model": cfg["model"], "ok": True,
                "verdict": verdict,
                "contradictions": [{"rule_a": str(row.get("rule_a") or "")[:300],
                                    "rule_b": str(row.get("rule_b") or "")[:300],
                                    "proof": str(row.get("proof") or "")[:900]}
                                   for row in (parsed.get("contradictions") or [])[:8]],
                "witness_assessment": str(parsed.get(
                    "witness_assessment") or "")[:1200],
                "unblocked_prototype_shape": str(parsed.get(
                    "unblocked_prototype_shape") or "")[:900],
                "confidence": max(0.0, min(100.0, float(
                    parsed.get("confidence") or 0.0))),
                "latency_sec": round(time.time()-started, 3)}
    except Exception as exc:
        if _retry:
            return _system_solvability_one(name, context, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "verdict": "UNCERTAIN", "error": str(exc),
                "latency_sec": round(time.time()-started, 3)}


def system_solvability_reviews(context):
    pool = ThreadPoolExecutor(max_workers=3)
    try:
        results = [future.result() for future in [
            pool.submit(_system_solvability_one, name, context)
            for name in PROVIDERS]]
    finally:
        pool.shutdown(wait=True)
    complete = len([row for row in results if row.get("ok")]) == 3
    verdicts = [row.get("verdict") for row in results if row.get("ok")]
    return {"ok": complete, "results": results,
            "unanimous_feasible": complete and set(verdicts) == {"FEASIBLE"},
            "unanimous_empty": complete and set(verdicts) == {"EMPTY"},
            "policy": "three_independent_no_rule_relaxation_solvability_audit"}


PROBE_AUTOPSY_PROMPT = """你是失败探针解剖员。只分析一个已完成的三倍全摩擦探针，不生成策略或新指标。
必须用输入中的具体样本数、净均值、留出段和概率定位唯一主死因；只能从allowed_filter_ids中选择一条未来可验证的REQUIRE剪枝规则。
若没有足够数据支持规则，proposed_rule必须为null。不得引用历史不存在的逐笔盘口、排队、撤单或毫秒OFI。
严格输出JSON：
{"death_cause_code":"cost_collapse|holdout_collapse|sample_starvation|regime_instability|tail_risk|capacity_shortfall|no_actionable_cause",
"evidence_refs":["..."],"microstructure_boundary":"...",
"proposed_rule":{"filter_id":"受控ID","action":"REQUIRE","rationale":"...","expected_prune_pct":0到100}或null}
"""


PRUNING_RULE_AUDIT_PROMPT = """你是失败学习剪枝规则的独立审计员。你不能修改或创建规则。
检查规则是否由给定探针数据支持、是否可观测、是否避免未来函数、是否过宽、是否会把样本全部剪掉、是否重复既有失败规则。
严格输出JSON：{"decision":"APPROVE或REJECT","hard_flags":["..."],"reason":"..."}。
任何证据不足、不可观测或预计剪除超过80%的规则必须REJECT。
"""


def probe_failure_autopsy(context, _retry=True):
    """DeepSeek deep diagnosis; retry uses structured non-thinking mode."""
    name = "deepseek"; cfg = _provider_config(name)
    consent = external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed") or not cfg["api_key"]:
        return {"provider": name, "ok": False,
                "error": "外部AI授权或密钥缺失"}
    body = {"model": cfg["model"], "messages": [
        {"role": "system", "content": PROBE_AUTOPSY_PROMPT},
        {"role": "user", "content": canonical_json(context)},
    ], "response_format": {"type": "json_object"},
       "max_tokens": 12000,
       "thinking": {"type": "enabled" if _retry else "disabled"}}
    started = time.time()
    try:
        req = urllib_request.Request(
            cfg["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        raw = json.loads(urllib_request.urlopen(
            req, timeout=max(cfg["timeout"], 180)).read().decode("utf-8"))
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        parsed = _parse_content_json(message.get("content") or "")
        code = str(parsed.get("death_cause_code") or "")
        allowed_codes = {"cost_collapse", "holdout_collapse", "sample_starvation",
                         "regime_instability", "tail_risk", "capacity_shortfall",
                         "no_actionable_cause"}
        if code not in allowed_codes:
            raise ValueError("invalid death_cause_code")
        rule = parsed.get("proposed_rule")
        allowed_filters = set(str(value) for value in
                              context.get("allowed_filter_ids") or [])
        if rule is not None:
            if (not isinstance(rule, dict)
                    or str(rule.get("filter_id")) not in allowed_filters
                    or str(rule.get("action")) != "REQUIRE"):
                raise ValueError("autopsy proposed rule outside controlled catalog")
            rule = {"filter_id": str(rule["filter_id"]), "action": "REQUIRE",
                    "rationale": str(rule.get("rationale") or "")[:600],
                    "expected_prune_pct": float(rule.get("expected_prune_pct") or 0.0)}
        return {"provider": name, "model": cfg["model"], "ok": True,
                "autopsy": {"death_cause_code": code,
                             "evidence_refs": [str(v)[:300] for v in
                                               (parsed.get("evidence_refs") or [])[:6]],
                             "microstructure_boundary": str(
                                 parsed.get("microstructure_boundary") or "")[:500],
                             "proposed_rule": rule},
                "latency_sec": round(time.time()-started, 3)}
    except Exception as exc:
        if _retry:
            return probe_failure_autopsy(context, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "error": str(exc), "latency_sec": round(time.time()-started, 3)}


def _audit_pruning_rule_one(name, context, _retry=True):
    cfg = _provider_config(name)
    consent = external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed") or not cfg["api_key"]:
        return {"provider": name, "ok": False, "decision": "REJECT",
                "error": "外部AI授权或密钥缺失"}
    body = {"model": cfg["model"], "messages": [
        {"role": "system", "content": PRUNING_RULE_AUDIT_PROMPT},
        {"role": "user", "content": canonical_json(context)},
    ]}
    body.update({"temperature": 0, "max_tokens": 1800})
    started = time.time()
    try:
        req = urllib_request.Request(
            cfg["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        raw = json.loads(urllib_request.urlopen(
            req, timeout=max(cfg["timeout"], 180)).read().decode("utf-8"))
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        parsed = _parse_content_json(message.get("content") or
                                     message.get("reasoning_content") or "")
        decision = str(parsed.get("decision") or "").upper()
        if decision not in ("APPROVE", "REJECT"):
            raise ValueError("invalid pruning audit decision")
        return {"provider": name, "model": cfg["model"], "ok": True,
                "decision": decision,
                "hard_flags": [str(v)[:300] for v in
                               (parsed.get("hard_flags") or [])[:6]],
                "reason": str(parsed.get("reason") or "")[:800],
                "latency_sec": round(time.time()-started, 3)}
    except Exception as exc:
        if _retry:
            return _audit_pruning_rule_one(name, context, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "decision": "REJECT", "error": str(exc),
                "latency_sec": round(time.time()-started, 3)}


def audit_pruning_rule(context):
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        futures = [pool.submit(_audit_pruning_rule_one, name, context)
                   for name in ("qwen", "glm")]
        results = [future.result() for future in futures]
    finally:
        pool.shutdown(wait=True)
    approved = all(row.get("ok") and row.get("decision") == "APPROVE"
                   for row in results)
    return {"approved": approved,
            "policy": "deepseek_proposes_qwen_and_glm_both_audit",
            "results": results}


DEATH_DISTILLATION_PROMPT = """你是失败知识蒸馏器，不生成策略、入场条件或盈利结论。
输入是已经去重、带stage的失败证据。deterministic_atlas是测量结果；three_ai_prescreen只是推理意见，二者不得混写。不得从OHLCV推断历史盘口、价差收窄、深度、撤单、队列或200毫秒OFI；registered_micro_vocabulary也不能作为历史证据。
请提炼不超过10条层级根规则。规则只能用于搜索导航或进入同标的同周期隔离验证，绝不能直接跨标的自动剪枝。只有future_data、unobservable_input、cost_arithmetic_impossible三类结构性硬伤可以标为structural_candidate，且仍需Codex复核；负期望、止损簇、留出崩塌、样本不足只能标为navigation_only或target_local_quarantine。
每条规则必须引用至少2个输入中真实存在的evidence_id，并提供可反例化条件。严格输出JSON：
{"root_rules":[{"rule_key":"R1","title":"...","category":"structural_hard_veto|empirical_cost_failure|empirical_holdout_failure|sample_failure|ai_reasoned_only","scope_recommendation":"navigation_only|target_local_quarantine|structural_candidate","applicable_death_codes":["..."],"source_evidence_ids":["..."],"logic_statement":"...","observable_boundary":"...","counterexample_test":"...","children":[{"name":"...","condition":"..."}]}],"global_boundary":"..."}
"""

DEATH_DISTILLATION_AUDIT_PROMPT = """你是死亡知识蒸馏结果的独立审计员，不能修改或补写规则。
逐条检查：证据ID是否存在；是否混淆确定性测量与AI意见；是否捏造历史L2/订单流；是否把跨标的经验变成自动剪枝；是否不可证伪；是否把微观描述基元当成盈利或方向证据。任一问题必须REJECT。
严格输出JSON：{"audits":[{"rule_key":"原样","decision":"APPROVE或REJECT","flags":["..."],"reason":"..."}]}
"""


def _death_distillation_propose(context, _retry=True):
    name = "deepseek"; cfg = _provider_config(name)
    consent = external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed") or not cfg["api_key"]:
        return {"provider": name, "ok": False,
                "error": "外部AI授权或密钥缺失", "root_rules": []}
    body = {"model": cfg["model"], "messages": [
        {"role": "system", "content": DEATH_DISTILLATION_PROMPT},
        {"role": "user", "content": canonical_json(context)},
    ], "response_format": {"type": "json_object"}, "max_tokens": 12000,
       "thinking": {"type": "enabled" if _retry else "disabled"}}
    started = time.time()
    try:
        req = urllib_request.Request(
            cfg["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        raw = json.loads(urllib_request.urlopen(
            req, timeout=max(cfg["timeout"], 180)).read().decode("utf-8"))
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        parsed = _parse_content_json(message.get("content") or
                                     message.get("reasoning_content") or "")
        evidence_ids = set(str(row.get("evidence_id")) for row in
                           context.get("evidence") or [])
        allowed_codes = set(str(value) for value in
                            context.get("allowed_death_codes") or [])
        categories = {"structural_hard_veto", "empirical_cost_failure",
                      "empirical_holdout_failure", "sample_failure",
                      "ai_reasoned_only"}
        scopes = {"navigation_only", "target_local_quarantine",
                  "structural_candidate"}
        rules = []; seen = set()
        for item in (parsed.get("root_rules") or [])[:10]:
            key = str(item.get("rule_key") or "")
            sources = [str(value) for value in
                       (item.get("source_evidence_ids") or [])]
            codes = [str(value) for value in
                     (item.get("applicable_death_codes") or [])]
            if (not key or key in seen or len(set(sources)) < 2
                    or not set(sources).issubset(evidence_ids)
                    or not set(codes).issubset(allowed_codes)
                    or str(item.get("category")) not in categories
                    or str(item.get("scope_recommendation")) not in scopes):
                continue
            seen.add(key)
            rules.append({
                "rule_key": key, "title": str(item.get("title") or "")[:200],
                "category": str(item["category"]),
                "scope_recommendation": str(item["scope_recommendation"]),
                "applicable_death_codes": sorted(set(codes)),
                "source_evidence_ids": sorted(set(sources)),
                "logic_statement": str(item.get("logic_statement") or "")[:1000],
                "observable_boundary": str(item.get("observable_boundary") or "")[:700],
                "counterexample_test": str(item.get("counterexample_test") or "")[:700],
                "children": [{"name": str(row.get("name") or "")[:160],
                              "condition": str(row.get("condition") or "")[:500]}
                             for row in (item.get("children") or [])[:8]
                             if isinstance(row, dict)],
            })
        return {"provider": name, "model": cfg["model"], "ok": True,
                "root_rules": rules,
                "global_boundary": str(parsed.get("global_boundary") or "")[:1000],
                "latency_sec": round(time.time()-started, 3)}
    except Exception as exc:
        if _retry:
            return _death_distillation_propose(context, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "root_rules": [], "error": str(exc),
                "latency_sec": round(time.time()-started, 3)}


def _death_distillation_audit_one(name, context, _retry=True):
    cfg = _provider_config(name)
    consent = external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed") or not cfg["api_key"]:
        return {"provider": name, "ok": False, "audits": [],
                "error": "外部AI授权或密钥缺失"}
    body = {"model": cfg["model"], "messages": [
        {"role": "system", "content": DEATH_DISTILLATION_AUDIT_PROMPT},
        {"role": "user", "content": canonical_json(context)},
    ]}
    body.update({"temperature": 0, "max_tokens": 4000})
    started = time.time()
    try:
        req = urllib_request.Request(
            cfg["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        raw = json.loads(urllib_request.urlopen(
            req, timeout=max(cfg["timeout"], 180)).read().decode("utf-8"))
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        parsed = _parse_content_json(message.get("content") or
                                     message.get("reasoning_content") or "")
        rule_keys = set(str(row.get("rule_key")) for row in
                        context.get("proposed_rules") or [])
        audits = {}
        for item in parsed.get("audits") or []:
            key = str(item.get("rule_key") or "")
            decision = str(item.get("decision") or "").upper()
            if key in rule_keys and decision in ("APPROVE", "REJECT"):
                audits[key] = {"rule_key": key, "decision": decision,
                               "flags": [str(value)[:300] for value in
                                         (item.get("flags") or [])[:8]],
                               "reason": str(item.get("reason") or "")[:800]}
        if set(audits) != rule_keys:
            raise ValueError("auditor omitted distilled rules")
        return {"provider": name, "model": cfg["model"], "ok": True,
                "audits": [audits[key] for key in sorted(audits)],
                "latency_sec": round(time.time()-started, 3)}
    except Exception as exc:
        if _retry:
            return _death_distillation_audit_one(name, context, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "audits": [], "error": str(exc),
                "latency_sec": round(time.time()-started, 3)}


def distill_death_rule_tree(context):
    proposal = _death_distillation_propose(context)
    if not proposal.get("ok") or not proposal.get("root_rules"):
        return {"ok": False, "proposal": proposal, "audits": [],
                "policy": "fail_closed_no_distilled_rule"}
    audit_context = dict(context)
    audit_context["proposed_rules"] = proposal["root_rules"]
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        audits = [future.result() for future in [
            pool.submit(_death_distillation_audit_one, "qwen", audit_context),
            pool.submit(_death_distillation_audit_one, "glm", audit_context)]]
    finally:
        pool.shutdown(wait=True)
    return {"ok": bool(all(row.get("ok") for row in audits)),
            "proposal": proposal, "audits": audits,
            "policy": "deepseek_distills_qwen_glm_both_audit_fail_closed"}


FAILURE_ANALYSIS_PROMPT = """你是栖语量化系统的失败归因研究员，不负责生成策略代码或DSL。
你必须只依据输入中的父版本DSL、0.9%/0.6%回测、留出段、样本量、频率、最大连亏、回撤、胜负特征分布、退出类型和典型案例诊断失败。
禁止用“可能”“建议优化”等空话；每项结论必须引用输入中的具体指标或条件ID。不要声称保证胜率。
当样本不足、胜负分布重叠或结构无可救药时应明确ABANDON，不能为了继续轮次而强行修改。
严格输出JSON：
{"diagnosis":{"verdict":"REVISE或ABANDON","root_causes":["..."],"evidence_refs":["..."],"keep_condition_ids":["..."],"remove_condition_ids":["..."],"proposed_changes":["..."],"frequency_bottleneck":"...","exit_diagnosis":"...","anti_overfit_checks":["..."]}}
数组最多各6项，每项不超过120个汉字。"""


def analyze_failure_one(name, context, strategy_metadata, _retry=True):
    """Return evidence-only diagnosis; this endpoint cannot propose executable DSL."""
    cfg = _provider_config(name)
    consent = external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed"):
        return {"provider": name, "ok": False, "error": "外部AI研究授权缺失或范围不足"}
    if not cfg["api_key"]:
        return {"provider": name, "ok": False, "error": "API密钥未配置"}
    payload = {"role_focus": RESEARCH_ROLES[name], "context": context,
               "strategy_metadata": strategy_metadata}
    body = {"model": cfg["model"], "messages": [
        {"role": "system", "content": FAILURE_ANALYSIS_PROMPT + "\n" + RESEARCH_ROLES[name]},
        {"role": "user", "content": canonical_json(payload)},
    ]}
    if name == "deepseek":
        body["response_format"] = {"type": "json_object"}
    body["temperature"] = 0
    body["max_tokens"] = 4500
    started = time.time()
    try:
        req = urllib_request.Request(
            cfg["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        response = urllib_request.urlopen(req, timeout=max(cfg["timeout"], 180))
        raw = json.loads(response.read().decode("utf-8"))
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        parsed = _parse_content_json(
            message.get("content") or message.get("reasoning_content") or "")
        diagnosis = parsed.get("diagnosis") if isinstance(parsed, dict) else None
        if not isinstance(diagnosis, dict):
            raise ValueError("diagnosis must be an object")
        verdict = str(diagnosis.get("verdict") or "").upper()
        if verdict not in ("REVISE", "ABANDON"):
            raise ValueError("diagnosis verdict must be REVISE or ABANDON")
        normalized = {"verdict": verdict}
        for key in ("root_causes", "evidence_refs", "keep_condition_ids",
                    "remove_condition_ids", "proposed_changes", "anti_overfit_checks"):
            values = diagnosis.get(key) or []
            if not isinstance(values, list):
                raise ValueError("diagnosis.%s must be an array" % key)
            normalized[key] = [str(value)[:240] for value in values[:6]]
        normalized["frequency_bottleneck"] = str(diagnosis.get("frequency_bottleneck") or "")[:300]
        normalized["exit_diagnosis"] = str(diagnosis.get("exit_diagnosis") or "")[:300]
        return {"provider": name, "model": cfg["model"], "ok": True,
                "diagnosis": normalized,
                "latency_sec": round(time.time()-started, 3)}
    except Exception as exc:
        if _retry:
            return analyze_failure_one(name, context, strategy_metadata, _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "error": str(exc), "latency_sec": round(time.time()-started, 3)}


def collaborative_failure_analysis(context, strategy_metadata):
    """Parallel causal diagnosis followed later by one architect and 3/3 review."""
    pool = ThreadPoolExecutor(max_workers=len(PROVIDERS))
    try:
        futures = [pool.submit(analyze_failure_one, name, context, strategy_metadata)
                   for name in PROVIDERS]
        results = [future.result() for future in futures]
    finally:
        pool.shutdown(wait=True)
    abandon = sum(1 for row in results
                  if row.get("ok") and (row.get("diagnosis") or {}).get("verdict") == "ABANDON")
    return {"ok": all(row.get("ok") for row in results),
            "policy": "three_parallel_evidence_diagnoses_then_one_rotating_architect_then_anonymous_3_of_3_review",
            "abandon_votes": abandon, "results": results}


ADVERSARIAL_REVIEW_ROLES = {
    "deepseek": "市场微观结构批判者：专门寻找叙事与可观测K线证据之间的断裂、环境错配和伪因果。",
    "qwen": "统计证伪者：专门寻找小样本、选择偏差、留出段坍塌、重复试验和过拟合。",
    "glm": "执行成本狙击手：专门检查手续费、价差、滑点、冲击、延迟、资金费、止损簇和不可成交假设。",
}


HYPOTHESIS_REVIEW_PROMPT = """你是栖语量化系统的对抗式研究会审员。
候选假设来自匿名研究者；你看不到其他会审员的结论。你不能修改、合并或新建假设。
你只能根据案例、市场环境、条件归因、失败记忆与当前策略，判断每个假设是否值得进入确定性回测。
特别检查：重复失败、样本频率损失、逻辑过拟合、市场环境错配、止损簇风险，entry是否含一次性事件触发，以及数值阈值是否符合feature_value_reference中的真实量纲和分位数。
每个逻辑假设都必须含deterministic_entry_edge_screen和semantic_audit。只有两者passed均为true才可SUPPORT；理由必须引用事件样本量、至少一个固定前向周期的全样本/留出段扣费后胜率与均值，不能只说“逻辑自洽”“值得回测”。三票通过仅表示允许进入完整回测，不代表策略有效或胜率达标。
你必须主动攻击候选而非帮助其过关。任一未来函数/不可观测因果、压力成本不能覆盖、因果链与DSL不一致，都是单项致命否决；不需要凑满五条。survival_score低于65必须REJECT。
严格输出JSON：
{"batch_hash":"原样复述","votes":[{"hypothesis_id":"原样复述","decision":"SUPPORT或REJECT","survival_score":0到100,"causal_verdict":"PASS或FAIL","cost_coverage_verdict":"PASS或FAIL","leakage_verdict":"PASS或FAIL","fatal_flaws":["..."],"reason":"中文简述","risk_flags":["..."]}]}
必须为每个hypothesis_id投票；批次哈希或格式错误时整批视为否决。"""
HYPOTHESIS_REVIEW_PROMPT += "\nreason限制在120个汉字以内，fatal_flaws最多5项，risk_flags最多3项，优先保证每个ID都有完整投票。"


def review_hypotheses_one(name, batch_hash, catalog, context, _retry=True):
    cfg = _provider_config(name)
    consent = external_research_consent_status(name, "cross_review")
    if not consent.get("allowed"):
        return {"provider": name, "ok": False, "batch_hash": batch_hash,
                "votes": [], "error": "外部AI研究授权缺失或范围不足",
                "consent_missing": True}
    if not cfg["api_key"]:
        return {"provider": name, "ok": False, "batch_hash": batch_hash,
                "votes": [], "error": "API密钥未配置"}
    review_role = ADVERSARIAL_REVIEW_ROLES[name]
    payload = {"batch_hash": batch_hash, "review_role": review_role,
               "hypotheses": catalog,
               "research_context": context}
    body = {"model": cfg["model"], "messages": [
        {"role": "system", "content": HYPOTHESIS_REVIEW_PROMPT + "\n你的专职角色：" + review_role},
        {"role": "user", "content": canonical_json(payload)},
    ]}
    if name == "deepseek":
        body["response_format"] = {"type": "json_object"}
    body["temperature"] = 0
    # DeepSeek's reasoning field can consume a substantial part of the
    # completion budget before emitting the short JSON vote block.
    body["max_tokens"] = 6000 if name == "deepseek" else 3500
    started = time.time()
    try:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(
            cfg["url"], data=encoded,
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST",
        )
        response = urllib_request.urlopen(req, timeout=max(cfg["timeout"], 150))
        raw = json.loads(response.read().decode("utf-8"))
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        content = message.get("content") or message.get("reasoning_content") or ""
        parsed = _parse_content_json(content)
        expected_ids = set(row.get("hypothesis_id") for row in catalog)
        votes = parsed.get("votes") if isinstance(parsed, dict) else None
        if str(parsed.get("batch_hash") or "") != batch_hash or not isinstance(votes, list):
            raise ValueError("batch hash or votes format mismatch")
        normalized = []
        seen = set()
        for vote in votes:
            hid = str(vote.get("hypothesis_id") or "")
            decision = str(vote.get("decision") or "").upper()
            score = float(vote.get("survival_score"))
            causal_verdict = str(vote.get("causal_verdict") or "").upper()
            cost_verdict = str(vote.get("cost_coverage_verdict") or "").upper()
            leakage_verdict = str(vote.get("leakage_verdict") or "").upper()
            fatal_flaws = [str(value)[:300] for value in
                           (vote.get("fatal_flaws") or [])][:5]
            if hid not in expected_ids or hid in seen or decision not in ("SUPPORT", "REJECT"):
                raise ValueError("invalid hypothesis vote")
            if (not 0 <= score <= 100 or causal_verdict not in ("PASS", "FAIL")
                    or cost_verdict not in ("PASS", "FAIL")
                    or leakage_verdict not in ("PASS", "FAIL")):
                raise ValueError("invalid adversarial vote fields")
            hard_fail = (score < 65 or causal_verdict == "FAIL"
                         or cost_verdict == "FAIL" or leakage_verdict == "FAIL"
                         or bool(fatal_flaws))
            if hard_fail:
                decision = "REJECT"
            seen.add(hid)
            normalized.append({"hypothesis_id": hid, "decision": decision,
                               "survival_score": score,
                               "causal_verdict": causal_verdict,
                               "cost_coverage_verdict": cost_verdict,
                               "leakage_verdict": leakage_verdict,
                               "fatal_flaws": fatal_flaws,
                               "reason": vote.get("reason") or "",
                               "risk_flags": vote.get("risk_flags") or [],
                               "review_role": review_role})
        if seen != expected_ids:
            raise ValueError("not every hypothesis received a vote")
        return {"provider": name, "model": cfg["model"], "ok": True,
                "batch_hash": batch_hash, "votes": normalized,
                "latency_sec": round(time.time()-started, 3)}
    except Exception as exc:
        if _retry:
            return review_hypotheses_one(name, batch_hash, catalog, context,
                                         _retry=False)
        return {"provider": name, "model": cfg["model"], "ok": False,
                "batch_hash": batch_hash, "votes": [], "error": str(exc),
                "latency_sec": round(time.time()-started, 3)}


def hypothesis_convergence(catalog, context):
    """Anonymous cross-review between ideation and any deterministic testing."""
    public_catalog = [
        {key: value for key, value in row.items()
         if key not in ("source_providers", "rationales", "votes")}
        for row in catalog
    ]
    batch_hash = hashlib.sha256(canonical_json(public_catalog).encode("utf-8")).hexdigest()
    if not catalog:
        return {"ok": True, "batch_hash": batch_hash, "reviews": [],
                "admitted": [], "rejected": [],
                "policy": "no_hypotheses"}
    pool = ThreadPoolExecutor(max_workers=len(PROVIDERS))
    try:
        futures = [pool.submit(review_hypotheses_one, name, batch_hash,
                               public_catalog, context) for name in PROVIDERS]
        reviews = [future.result() for future in futures]
    finally:
        pool.shutdown(wait=True)
    admitted = []; rejected = []
    for hypothesis in catalog:
        hid = hypothesis["hypothesis_id"]
        support = 0; provider_votes = []
        for review in reviews:
            vote = next((row for row in review.get("votes") or []
                         if row.get("hypothesis_id") == hid), None)
            decision = vote.get("decision") if review.get("ok") and vote else "REJECT"
            provider_votes.append({"provider": review.get("provider"),
                                   "decision": decision,
                                   "reason": (vote or {}).get("reason") or review.get("error"),
                                   "survival_score": (vote or {}).get("survival_score"),
                                   "causal_verdict": (vote or {}).get("causal_verdict"),
                                   "cost_coverage_verdict": (vote or {}).get("cost_coverage_verdict"),
                                   "leakage_verdict": (vote or {}).get("leakage_verdict"),
                                   "fatal_flaws": (vote or {}).get("fatal_flaws") or [],
                                   "review_role": (vote or {}).get("review_role")})
            support += 1 if decision == "SUPPORT" else 0
        independent_support = len(set(hypothesis.get("source_providers") or []))
        # Parameter direction must have arisen independently from at least two
        # researchers and survive a 2/3 cross-review. A new logic DSL may have
        # one origin, but all three anonymous reviewers must endorse it before
        # it is even allowed to consume backtest capacity.
        required = 3 if hypothesis.get("kind") == "dsl" else 2
        source_ok = independent_support >= 2 if hypothesis.get("kind") == "parameter" else independent_support >= 1
        row = dict(hypothesis)
        row.update({"support_count": support, "required_support": required,
                    "independent_source_count": independent_support,
                    "votes": provider_votes})
        if source_ok and support >= required:
            admitted.append(row)
        else:
            rejected.append(row)
    return {"ok": all(row.get("ok") for row in reviews),
            "batch_hash": batch_hash, "reviews": reviews,
            "admitted": admitted, "rejected": rejected,
            "policy": "adversarial_role_review;score_below_65_or_any_hard_fail_veto;parameter_2_of_3;dsl_exact_3_of_3"}


ENV_BOUNDARY_PROPOSE_PROMPT = (
    "你是栖语策略适用微观环境边界的起草者（DeepSeek）。"
    "只能使用上下文中的 registered_primitives_* 的 cluster_id。"
    "返回 JSON：boundary={any_of:[], all_of:[], none_of:[], volatility_in:[], "
    "volatility_not_in:[], natural_language:str, uses_outcome_labels:false}。"
    "含义：当前注册基元满足 all_of（近期窗口同时出现）、落在 any_of、且不触及 none_of 时策略才可开仓。"
    "禁止使用盈亏/胜率等结果标签，禁止循环论证，禁止编造未给出的 cluster_id。"
)

ENV_BOUNDARY_AUDIT_PROMPT = (
    "你是栖语环境边界审计者。只批准不循环论证、不结果标签、不引用未注册基元、"
    "且不会把边界收得只拟合极少样本的声明。返回 JSON："
    "{decision:APPROVE|REJECT, reason:str, flags:[]}。"
)


def propose_environment_boundary(context, _retry=True):
    name = "deepseek"
    cfg = _provider_config(name)
    consent = external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed") or not cfg["api_key"]:
        return {"provider": name, "ok": False, "error": "外部AI授权或密钥缺失"}
    allowed = set()
    for key in ("registered_primitives_symbol", "registered_primitives_global_top"):
        for row in context.get(key) or []:
            allowed.add(str(row.get("cluster_id") or ""))
    body = {"model": cfg["model"], "messages": [
        {"role": "system", "content": ENV_BOUNDARY_PROPOSE_PROMPT},
        {"role": "user", "content": canonical_json(context)},
    ], "response_format": {"type": "json_object"}, "max_tokens": 2500,
       "thinking": {"type": "enabled" if _retry else "disabled"}}
    started = time.time()
    try:
        req = urllib_request.Request(
            cfg["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        raw = json.loads(urllib_request.urlopen(
            req, timeout=max(cfg["timeout"], 180)).read().decode("utf-8"))
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        parsed = _parse_content_json(message.get("content") or
                                     message.get("reasoning_content") or "")
        boundary = parsed.get("boundary") or parsed
        for key in ("any_of", "all_of", "none_of"):
            vals = []
            for item in boundary.get(key) or []:
                cid = str(item)
                if cid in allowed:
                    vals.append(cid)
            boundary[key] = vals
        boundary["uses_outcome_labels"] = False
        if not (boundary.get("any_of") or boundary.get("all_of") or
                boundary.get("none_of")):
            raise ValueError("empty environment boundary")
        return {"provider": name, "model": cfg["model"], "ok": True,
                "boundary": boundary,
                "latency_sec": round(time.time() - started, 3)}
    except Exception as exc:
        if _retry:
            return propose_environment_boundary(context, _retry=False)
        return {"provider": name, "model": cfg.get("model"), "ok": False,
                "error": str(exc), "latency_sec": round(time.time() - started, 3)}


def audit_environment_boundary(name, context, boundary, _retry=True):
    cfg = _provider_config(name)
    consent = external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed") or not cfg["api_key"]:
        return {"provider": name, "ok": False, "decision": "REJECT",
                "error": "外部AI授权或密钥缺失"}
    payload = {"context": context, "boundary": boundary}
    body = {"model": cfg["model"], "messages": [
        {"role": "system", "content": ENV_BOUNDARY_AUDIT_PROMPT},
        {"role": "user", "content": canonical_json(payload)},
    ]}
    body.update({"temperature": 0, "max_tokens": 1200})
    started = time.time()
    try:
        req = urllib_request.Request(
            cfg["url"], data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        raw = json.loads(urllib_request.urlopen(
            req, timeout=max(cfg["timeout"], 180)).read().decode("utf-8"))
        message = ((raw.get("choices") or [{}])[0].get("message") or {})
        parsed = _parse_content_json(message.get("content") or
                                     message.get("reasoning_content") or "")
        decision = str(parsed.get("decision") or "").upper()
        if decision not in ("APPROVE", "REJECT"):
            raise ValueError("invalid env boundary audit decision")
        return {"provider": name, "model": cfg["model"], "ok": True,
                "decision": decision,
                "reason": str(parsed.get("reason") or "")[:700],
                "flags": [str(x)[:200] for x in (parsed.get("flags") or [])[:8]],
                "latency_sec": round(time.time() - started, 3)}
    except Exception as exc:
        if _retry:
            return audit_environment_boundary(name, context, boundary, _retry=False)
        return {"provider": name, "model": cfg.get("model"), "ok": False,
                "decision": "REJECT", "error": str(exc),
                "latency_sec": round(time.time() - started, 3)}


def finalize_environment_boundary(name, context, boundary, prior_audit, _retry=True):
    """GLM final review; must APPROVE after Qwen audit."""
    result = audit_environment_boundary(name, {
        "prior_audit": prior_audit,
        "role": "final_review",
        **(context or {}),
    }, boundary, _retry=_retry)
    result["role"] = "final_review"
    return result
