# -*- coding: utf-8 -*-
"""3rd-party AI optimizer: DeepSeek / Qwen / GLM propose patches for Gate failures."""
from __future__ import print_function

import copy
import json
import time


SYSTEM_PROMPT = """你是量化策略无人值守优化器（只输出 JSON，不要 Markdown）。
目标：在不违反 non_negotiable_rules / forbidden_transformations 的前提下，
针对当前 STEP A Gate / L1 失败，提出可执行的策略包补丁，使下一轮更接近过关。

硬约束：
1. 不得建议固定百分比止盈（如 1%/2% TP）。
2. 不得建议削弱 0.9% 保护止损。
3. 不得把 EMA/CCI/RSI 变成核心边（除非 mechanism 已授权且非 forbidden）。
4. 不得建议 live mount / human confirm 绕过。
5. 若判定在当前数据与规则下已无优化空间，decision 必须为 LIMIT_REACHED。

输出唯一 JSON 对象：
{
  "decision": "PATCH" | "LIMIT_REACHED" | "ABORT",
  "rationale": "中文简述失败根因与修改意图",
  "limit_reason": "若 LIMIT_REACHED 则说明理论上限/瓶颈，否则 null",
  "confidence": 0.0到1.0,
  "target_fixes": ["payoff_ge_2_5", "worst5_loss_share_le_40pct", "..."],
  "patches": [
     {"op":"set","path":"dsl.exit.any[0].n_atr","value":4.5},
     {"op":"replace_entry","entry":{...}},
     {"op":"replace_exit","exit":{...}},
     {"op":"merge_dsl","dsl":{"max_hold_bars":36}},
     {"op":"merge_mechanism_spec","mechanism_spec":{"entry_logic":"..."}},
     {"op":"rename_family","mechanism_family":"new_family_name"},
     {"op":"noop","note":"..."}
  ],
  "expected_effects": {"payoff_ratio":"+", "worst5_loss_share":"-", "filled_entries":"+"}
}

path 以策略包根为起点；活跃方向的 DSL 也可用简写 path：dsl.* （驱动会同步 dsl_long/dsl_short）。
patches 应尽量少而可验证；优先调参（vol_z 阈值、ATR trail、swing lookback、max_hold、entry 条件阈值），
其次才替换 entry/exit 子树。不要输出无法解析的伪代码。"""


def _call_provider(provider, system_prompt, user_payload, max_tokens=2800, temperature=0.2):
    """Use existing dual-engine AI JSON helper (OpenAI-compatible providers)."""
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
    user_payload = {
        "role": "gate_failure_optimizer",
        "provider_slot": provider,
        "failure_context": failure_context,
        "recent_iterations": history_tail or [],
        "instructions": {
            "prefer_minimal_patches": True,
            "must_respect_non_negotiables": True,
            "if_no_room_left": "LIMIT_REACHED",
        },
    }
    t0 = time.time()
    raw = _call_provider(provider, SYSTEM_PROMPT, user_payload, max_tokens=max_tokens)
    elapsed = round(time.time() - t0, 2)
    parsed = (raw or {}).get("parsed") if isinstance(raw, dict) else None
    ok = bool(raw and raw.get("ok") and isinstance(parsed, dict))
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
            rows.append(propose_from_provider(p, failure_context, history_tail, max_tokens=max_tokens))
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
    """Merge 3-party proposals into one actionable decision.

    Rules:
    - If >=2 providers say LIMIT_REACHED → LIMIT_REACHED
    - Else prefer prefer_provider PATCH if ok; else first ok PATCH
    - If no usable PATCH → ABORT (AI unavailable) or LIMIT if any LIMIT
    """
    ok_rows = [r for r in (provider_rows or []) if r.get("ok") and isinstance(r.get("proposal"), dict)]
    decisions = []
    for r in ok_rows:
        d = str((r.get("proposal") or {}).get("decision") or "").upper()
        decisions.append(d)

    limit_n = sum(1 for d in decisions if d == "LIMIT_REACHED")
    patch_rows = [r for r in ok_rows if str((r.get("proposal") or {}).get("decision") or "").upper() == "PATCH"]

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

    # prefer provider
    chosen = None
    for r in patch_rows:
        if r.get("provider") == prefer_provider:
            chosen = r
            break
    if chosen is None and patch_rows:
        chosen = patch_rows[0]

    if chosen is not None:
        prop = chosen.get("proposal") or {}
        return {
            "decision": "PATCH",
            "rationale": prop.get("rationale"),
            "limit_reason": None,
            "confidence": prop.get("confidence"),
            "target_fixes": prop.get("target_fixes") or [],
            "patches": list(prop.get("patches") or []),
            "expected_effects": prop.get("expected_effects") or {},
            "chosen_provider": chosen.get("provider"),
            "source_providers": [r.get("provider") for r in patch_rows],
            "provider_rows": provider_rows,
        }

    if limit_n >= 1:
        r0 = next(r for r in ok_rows if str((r.get("proposal") or {}).get("decision") or "").upper() == "LIMIT_REACHED")
        prop = r0.get("proposal") or {}
        return {
            "decision": "LIMIT_REACHED",
            "rationale": prop.get("rationale"),
            "limit_reason": prop.get("limit_reason") or prop.get("rationale"),
            "patches": [],
            "chosen_provider": r0.get("provider"),
            "provider_rows": provider_rows,
        }

    # no usable AI
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
