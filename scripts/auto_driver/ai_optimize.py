# -*- coding: utf-8 -*-
"""3rd-party AI: prune-audit / RESET only — never additive decorate on shit packs."""
from __future__ import print_function

import copy
import time

from . import charter as _charter

SYSTEM_PROMPT = _charter.prompt_prefix() + """
你是「剪枝审判官」而非参数堆砌器（只输出 JSON，不要 Markdown）。

核心原则（反屎上雕花）：
- 若当前包相对 invariants_contract / mechanism 伪代码已语义偏离 → decision 必须为 RESET。
- 禁止增量式优化（Additive Optimization）：不得加过滤、加分支、加 EMA/CCI/RSI、不得用 swing_extreme 冒充影线止损。
- PRUNE 只允许：删除冗余叶子、把越界参数钳回契约、恢复契约要求的缺失叶子（且该叶子必须已在契约中定义）。
- 若修不好除非发明契约外新逻辑 → RESET（清零重译），绝不是继续雕花。

角色：
- GLM-5.2 = 逻辑翻译官 / 剪枝审判（本提示词）。严禁把臃肿代码当基底去“改好它”。
- Cursor/Codex = 精密打字员：只翻译契约，不发挥。

硬约束：
1. 不得建议固定百分比止盈。只允许契约内的 ATR / prev_mid48 / entry_wick_buffer / partial_tp_atr。
2. 不得削弱 0.9% 保护止损；不得降低 Gate2（payoff≥2.5 / calmar≥1.5 / WF≥7/10）。
3. 不得建议 live mount / human confirm 绕过。
4. rolling_4h_sweep_5m*：必须保留 vol_ma20_ratio>1.15、prev_high48/prev_low48 扫荡收回、
   entry_wick_buffer(0.0008)、prev_mid48 初 TP、atr_trailing≈3.2；严禁 swing_extreme 当影线止损代理。
5. DSL 边界：atr_trailing.n_atr ∈ [2.5,5.0]；entry_wick_buffer.buffer_pct ∈ [0.0003,0.003]。
6. 若 pretest_quality / invariants 已判定 SHIT_TRANSLATION → 只能 RESET，禁止 PRUNE/PATCH。
7. 若连续多轮都在同一错误基底上调 n_atr/max_hold → LIMIT_REACHED（承认起点是屎，停止雕花）。

输出唯一 JSON：
{
  "decision": "RESET" | "PRUNE" | "LIMIT_REACHED" | "ABORT",
  "rationale": "中文：偏离点 / 为何不能雕花",
  "limit_reason": "若 LIMIT_REACHED 说明瓶颈，否则 null",
  "confidence": 0.0到1.0,
  "deviations": ["逐条列出相对契约的偏离"],
  "target_fixes": ["restore_entry_wick_buffer", "restore_prev_mid48_tp"],
  "patches": [
     {"op":"replace_exit","exit":{...}},
     {"op":"noop","note":"RESET — discard pack, retranslate from contract"}
  ],
  "expected_effects": {"semantic_fidelity":"+", "complexity":"-"}
}

兼容：若输出 PATCH，驱动视为 PRUNE 并做减法过滤。
"""


def _call_provider(provider, system_prompt, user_payload, max_tokens=2800, temperature=0.2):
    import auto_trade_dual_engine_factory as dual

    dual._load_env()
    return dual._ai_json(
        provider,
        system_prompt,
        user_payload,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def propose_from_provider(provider, failure_context, history_tail=None, max_tokens=2800):
    pretest = (failure_context or {}).get("pretest_quality") or {}
    shit = str(pretest.get("quality") or "") == "SHIT_TRANSLATION"
    user_payload = {
        "role": "prune_auditor",
        "provider_slot": provider,
        "failure_context": failure_context,
        "recent_iterations": history_tail or [],
        "optimize_goals": (failure_context or {}).get("optimize_goals") or [],
        "invariants_contract": (failure_context or {}).get("invariants_contract"),
        "pretest_quality": pretest,
        "instructions": {
            "mode": "RESET_ONLY" if shit else "PRUNE_OR_RESET",
            "ban_additive_optimization": True,
            "must_respect_non_negotiables": True,
            "if_semantic_drift": "RESET",
            "if_no_room_left": "LIMIT_REACHED",
            "never_lower_gate2_floors": True,
            "never_fake_wick_with_swing_extreme": True,
        },
    }
    t0 = time.time()
    raw = _call_provider(provider, SYSTEM_PROMPT, user_payload, max_tokens=max_tokens)
    elapsed = round(time.time() - t0, 2)
    parsed = (raw or {}).get("parsed") if isinstance(raw, dict) else None
    ok = bool(raw and raw.get("ok") and isinstance(parsed, dict))
    if ok and isinstance(parsed, dict):
        if str(parsed.get("decision") or "").upper() == "PATCH":
            parsed["decision"] = "PRUNE"
        if shit:
            parsed["decision"] = "RESET"
            parsed["rationale"] = (
                ((parsed.get("rationale") or "") + "｜pretest=SHIT_TRANSLATION → 强制 RESET")
                .strip("｜")
            )
    return {
        "provider": provider,
        "ok": ok,
        "elapsed_sec": elapsed,
        "error": None if ok else (raw or {}).get("error") or (raw or {}).get("reason") or "ai_failed",
        "consent_missing": bool((raw or {}).get("consent_missing")),
        "credential_missing": bool((raw or {}).get("credential_missing")),
        "proposal": parsed if isinstance(parsed, dict) else {},
        "raw_keys": list((raw or {}).keys()) if isinstance(raw, dict) else [],
    }


def propose_from_all(providers, failure_context, history_tail=None, max_tokens=2800):
    rows = []
    for p in providers:
        try:
            rows.append(propose_from_provider(
                p, failure_context, history_tail=history_tail, max_tokens=max_tokens,
            ))
        except Exception as exc:
            rows.append({
                "provider": p,
                "ok": False,
                "elapsed_sec": 0,
                "error": str(exc),
                "proposal": {},
            })
    return rows


def merge_proposals(provider_rows, prefer_provider="glm"):
    """Merge prune/reset proposals. RESET beats PRUNE; never decorate shit."""
    ok_rows = [r for r in (provider_rows or []) if r.get("ok") and isinstance(r.get("proposal"), dict)]
    decisions = []
    for r in ok_rows:
        d = str((r.get("proposal") or {}).get("decision") or "").upper()
        if d == "PATCH":
            d = "PRUNE"
        decisions.append(d)

    reset_n = sum(1 for d in decisions if d == "RESET")
    limit_n = sum(1 for d in decisions if d == "LIMIT_REACHED")
    prune_rows = [
        r for r in ok_rows
        if str((r.get("proposal") or {}).get("decision") or "").upper() in ("PRUNE", "PATCH")
    ]

    if reset_n >= 1:
        r0 = next(
            r for r in ok_rows
            if str((r.get("proposal") or {}).get("decision") or "").upper() == "RESET"
        )
        prop = r0.get("proposal") or {}
        return {
            "decision": "RESET",
            "rationale": prop.get("rationale") or "semantic_drift_reset",
            "limit_reason": prop.get("limit_reason"),
            "deviations": prop.get("deviations") or [],
            "patches": [],
            "chosen_provider": r0.get("provider"),
            "provider_rows": provider_rows,
        }

    if limit_n >= 2:
        reasons = []
        for r in ok_rows:
            prop = r.get("proposal") or {}
            if str(prop.get("decision") or "").upper() == "LIMIT_REACHED":
                reasons.append("%s: %s" % (r.get("provider"), prop.get("limit_reason") or prop.get("rationale")))
        return {
            "decision": "LIMIT_REACHED",
            "rationale": "majority_limit",
            "limit_reason": " | ".join(reasons)[:800],
            "patches": [],
            "source_providers": [r.get("provider") for r in ok_rows],
            "provider_rows": provider_rows,
        }

    chosen = None
    for r in prune_rows:
        if r.get("provider") == prefer_provider:
            chosen = r
            break
    if chosen is None and prune_rows:
        chosen = prune_rows[0]

    if chosen is not None:
        prop = chosen.get("proposal") or {}
        return {
            "decision": "PRUNE",
            "rationale": prop.get("rationale"),
            "limit_reason": None,
            "confidence": prop.get("confidence"),
            "target_fixes": prop.get("target_fixes") or [],
            "patches": list(prop.get("patches") or []),
            "expected_effects": prop.get("expected_effects") or {},
            "chosen_provider": chosen.get("provider"),
            "source_providers": [r.get("provider") for r in prune_rows],
            "provider_rows": provider_rows,
        }

    if limit_n >= 1:
        r0 = next(
            r for r in ok_rows
            if str((r.get("proposal") or {}).get("decision") or "").upper() == "LIMIT_REACHED"
        )
        prop = r0.get("proposal") or {}
        return {
            "decision": "LIMIT_REACHED",
            "rationale": prop.get("rationale"),
            "limit_reason": prop.get("limit_reason") or prop.get("rationale"),
            "patches": [],
            "chosen_provider": r0.get("provider"),
            "provider_rows": provider_rows,
        }

    errs = [("%s:%s" % (r.get("provider"), r.get("error"))) for r in (provider_rows or [])]
    return {
        "decision": "ABORT",
        "rationale": "no_usable_ai_proposal",
        "limit_reason": None,
        "patches": [],
        "errors": errs,
        "provider_rows": provider_rows,
    }


def history_tail_from_iterations(iterations, n=3):
    """Compact prior iteration summaries for the prompt."""
    out = []
    for it in (iterations or [])[-n:]:
        out.append({
            "iteration": it.get("iteration"),
            "pipeline_reason": it.get("pipeline_reason"),
            "composite_score": it.get("composite_score"),
            "total_gap": it.get("total_gap"),
            "failed_checks": it.get("failed_checks"),
            "l1_reject": it.get("l1_reject"),
            "applied_patches": it.get("applied_patches"),
            "ai_decision": it.get("ai_decision"),
            "ai_rationale": (it.get("ai_rationale") or "")[:240],
        })
    return out
