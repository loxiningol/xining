# -*- coding: utf-8 -*-
"""Stage ③ helpers — DeepSeek factor-expression proposals for EasyQuant mine.

DeepSeek proposes additional factor names / rules; EasyQuant mines them on
OKX candles; QuantOracle certifies. Never invents Sharpe numbers.
"""
from __future__ import print_function

from datetime import datetime

# Map DeepSeek free-text ideas onto local EasyQuant matrix keys + rules
_KNOWN_SPECS = {
    "close_z_20": [("long_low", "均值回归：过深偏离均线后回升"),
                   ("short_high", "均值回归：过热偏离后回落")],
    "ret_12": [("long_high", "短动量延续"), ("short_high", "短动量衰竭反手")],
    "dist_roll_low": [("long_low", "滚动低点外延后收回")],
    "dist_roll_high": [("short_high", "滚动高点外延后收回")],
    "lower_wick_pct": [("long_high", "长下影线后多头回收")],
    "upper_wick_pct": [("short_high", "长上影线后空头回收")],
    "range_pct": [("long_low", "压缩后方向选择")],
    "atr_pct_14": [("long_high", "波动扩张环境")],
    "ret_3": [("long_high", "超短动量"), ("short_high", "超短反转")],
    "ret_1": [("short_high", "单 bar 过热反手")],
    "abs_ret_1": [("long_high", "冲击后波动交易")],
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def propose_factor_specs(design_doc, skip_llm=True, max_specs=12):
    """Return list of {factor, rule, thesis_zh, source} for mining."""
    hyps = (design_doc or {}).get("hypotheses") or []
    hints = []
    for h in hyps:
        for name in (h.get("testable_factor_hints") or []):
            if name not in hints:
                hints.append(name)
    if not hints:
        hints = ["close_z_20", "ret_12", "range_pct", "dist_roll_low"]

    specs = []
    for name in hints:
        for rule, thesis in _KNOWN_SPECS.get(name, [("long_low", "假设衍生")]):
            specs.append({
                "factor": name,
                "rule": rule,
                "thesis_zh": thesis,
                "source": "hypothesis_hint",
            })

    deepseek = {"used": False, "ok": False}
    if not skip_llm:
        deepseek = _deepseek_expand(design_doc)
        for item in deepseek.get("proposals") or []:
            name = str(item.get("factor") or "").strip()
            rule = str(item.get("rule") or "long_low").strip()
            if name in _KNOWN_SPECS:
                specs.append({
                    "factor": name,
                    "rule": rule if rule in ("long_high", "long_low", "short_high", "short_low") else "long_low",
                    "thesis_zh": item.get("thesis_zh") or "DeepSeek 提议",
                    "source": "deepseek",
                })

    # de-dupe
    seen = set()
    uniq = []
    for s in specs:
        key = (s["factor"], s["rule"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(s)
    return {
        "ok": True,
        "specs": uniq[: int(max_specs)],
        "deepseek": deepseek,
        "at": _now(),
    }


def _deepseek_expand(design_doc):
    try:
        from dual_engine_workflow_v2.pipeline_step_a import _ai_json
        allowed = sorted(_KNOWN_SPECS.keys())
        prompt = (
            "你是因子研究员。仅从 allowed_factors 中挑选，输出 JSON："
            "{\"proposals\":[{\"factor\":\"...\",\"rule\":\"long_low|long_high|short_high|short_low\","
            "\"thesis_zh\":\"...\"}]}。"
            "最多 8 条。禁止编造夏普/胜率。禁止发明 allowed 以外的因子名。"
        )
        res = _ai_json(
            "deepseek",
            prompt,
            {
                "design_doc_summary": {
                    "mechanism_family": (design_doc or {}).get("mechanism_family"),
                    "core_logic_zh": (design_doc or {}).get("core_logic_zh"),
                    "hypotheses": (design_doc or {}).get("hypotheses"),
                },
                "allowed_factors": allowed,
            },
            max_tokens=700,
            temperature=0.3,
        )
        parsed = (res or {}).get("parsed") or (res or {}).get("json") or {}
        props = parsed.get("proposals") if isinstance(parsed, dict) else []
        return {
            "used": True,
            "ok": bool(props),
            "proposals": props or [],
            "error": None if props else ((res or {}).get("error") or "empty"),
        }
    except Exception as exc:
        return {"used": True, "ok": False, "proposals": [], "error": str(exc)[:200]}


def var_from_returns(returns, alpha=0.05):
    """Historical VaR (positive number = loss magnitude)."""
    rets = sorted(float(x) for x in (returns or []) if x is not None)
    if len(rets) < 20:
        return None
    idx = max(0, int(len(rets) * float(alpha)) - 1)
    # left tail
    q = rets[idx]
    return abs(min(0.0, q))


def risk_fuse_var(certified_row, max_daily_loss=0.05):
    """Fuse ③: QuantOracle/local VaR beyond human bound → reject."""
    cert = ((certified_row or {}).get("quantoracle") or {}).get("certified") or {}
    rets = (certified_row or {}).get("returns") or []
    # Prefer portfolio vol as proxy if no VaR field; compute hist VaR locally
    var = cert.get("var") or cert.get("historical_var")
    if var is None:
        var = var_from_returns(rets, alpha=0.05)
    if var is None:
        return {"triggered": False, "var": None, "limit": max_daily_loss}
    triggered = float(var) > float(max_daily_loss)
    return {
        "triggered": triggered,
        "var": float(var),
        "limit": float(max_daily_loss),
        "reason": "var_exceeds_limit" if triggered else None,
    }
