# -*- coding: utf-8 -*-
"""P3 multi-AI early AST committee (master plan §11).

Four fixed providers, creation-time roles (before Top3 formal review):
  DeepSeek — mechanisms + failure root cause + repair suggestions
  Qwen     — AST representations (6 compilable trees per mechanism)
  GLM      — diversity / missing families
  Kimi     — failure diagnosis + allowed/forbidden mods + repair wave plan

Rules:
  - Round 1: independent generation (provider tags kept only in private audit)
  - Round 2: anonymous candidates + aggregate stats only
  - Deterministic code decides compile / near-dupe / probe admission
  - No free-form Python; only JSON AST validated by ast_compiler
  - AI cannot declare a research direction dead
"""
from __future__ import print_function

import hashlib
import json
import os
from datetime import datetime

from . import ast_compiler as ac
from . import quality_optimization as qopt


REPRESENTATION_TYPES = (
    "threshold", "rank", "delta", "sequence", "state", "relative_exclusion",
)

MECHANISM_SYSTEM = """你是栖语创造委员会·机制科学家（DeepSeek）。
固定契约：杠杆20×，保护止损0.5%价格，目标先触及+0.5555%再触及-0.5%。
只输出JSON，禁止自由代码/DSL全文。
输出：{"mechanisms":[{"mechanism_id":"...","family":"exhaustion|mean_reversion|vol_squeeze_break|donchian_trend_break","statement_zh":"...","why_profit_first_zh":"...","failure_states_zh":["..."],"factor_hints":["rsi_14","bb_lower_dist"],"repair_suggestions_zh":["..."]}],"failure_root_causes_zh":["..."]}
最多6个机制。factor_hints必须来自给定特征注册表。无法回答「为何不先反向0.5%」则不要该机制。
"""

AST_SYSTEM = """你是栖语创造委员会·AST表征生成器（Qwen）。
只输出JSON AST，禁止Python/自由代码。
节点type仅允许：compare,quantile,all,any,not,delta,slope,rate_of_change,state,sequence,relative,exclude。
每个mechanism必须给出6个逻辑异构表征（representation_type分别是 threshold,rank,delta,sequence,state,relative_exclusion）。
输出：{"asts":[{"mechanism_id":"...","representation_type":"rank","event_ast":{...},"factor_hints":["rsi_14"]}]}
feature必须来自给定注册表。sequence最多3步。无法编译的不要输出。
"""

DIVERSITY_SYSTEM = """你是栖语创造委员会·多样性设计师（GLM）。
只输出JSON。输入是匿名候选指纹与缺失族统计。
输出：{"missing_families":["..."],"supplement_specs":[{"family":"...","representation_type":"sequence","factor_hints":["rsi_14","bb_mid_reclaim"],"statement_zh":"..."}],"duplicate_ratio_estimate":0.0,"notes_zh":"..."}
禁止只给自然语言研究结论；必须给可执行的supplement_specs。禁止改止损/杠杆。
"""

FAILURE_SYSTEM = """你是栖语创造委员会·失败分析与修复审计（Kimi）。
只输出JSON。
输出：{"failure_codes":["LOW_WIN_RATE"],"allowed_modifications":["entry_confirmation"],"forbidden_modifications":["change_protective_stop","change_leverage"],"repair_wave_plan":[{"wave":2,"focus":"sequence","actions_zh":["..."]}],"may_declare_direction_dead":false,"notes_zh":"..."}
may_declare_direction_dead必须为false。禁止建议修改0.5%止损或20×杠杆。
"""


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ai_json(provider, system_prompt, user_payload, max_tokens=1800, temperature=0.25):
    try:
        from .pipeline_step_a import _ai_json as call
        return call(
            provider, system_prompt, user_payload,
            max_tokens=max_tokens, temperature=temperature,
        )
    except Exception as exc:
        return {"ok": False, "provider": provider, "error": str(exc)[:240]}


def _anon_id(payload, salt="p3"):
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return "anon_%s" % hashlib.sha256(
        (salt + ":" + blob).encode("utf-8")
    ).hexdigest()[:16]


def _fingerprint_row(row):
    return {
        "anon_id": row.get("anon_id"),
        "family": row.get("family"),
        "representation_type": row.get("representation_type"),
        "factor_hints": list(row.get("factor_hints") or [])[:6],
        "event_ast_hash": row.get("event_ast_hash"),
        "compile_ok": bool(row.get("compile_ok")),
        "formal_ok": bool(row.get("formal_ok")),
    }


def feature_registry_payload():
    return {
        "features": list(ac.feature_registry()),
        "node_types": list(ac.NODE_TYPES),
        "representation_types": list(REPRESENTATION_TYPES),
        "max_depth": ac.MAX_AST_DEPTH,
        "max_sequence_steps": ac.MAX_SEQUENCE_STEPS,
    }


def generate_mechanisms(brief="", symbol="", timeframe="", direction="long",
                        failure_summary=None, skip_llm=False, run_id=None):
    """DeepSeek: structured mechanism skeletons (+ deterministic fallback)."""
    payload = {
        "brief": str(brief or "")[:4000],
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "feature_registry": feature_registry_payload(),
        "failure_summary": failure_summary or {},
        "fixed_contract": {
            "leverage": 20,
            "protective_stop_price_distance": 0.005,
            "profit_first_target": 0.005555,
        },
    }
    audit = {"provider": "deepseek", "role": "generate_mechanisms", "at": _now()}
    mechanisms = []
    enabled = str(os.environ.get("QIYU_P3_AI_COMMITTEE") or "1").lower() not in (
        "0", "false", "no", "off",
    )
    if (not skip_llm) and enabled:
        res = _ai_json("deepseek", MECHANISM_SYSTEM, payload, max_tokens=1600)
        audit["llm"] = {
            "ok": bool(res.get("ok")),
            "error": res.get("error"),
            "latency_sec": res.get("latency_sec"),
        }
        parsed = res.get("parsed") if isinstance(res.get("parsed"), dict) else {}
        registry = set(ac.feature_registry())
        for row in (parsed.get("mechanisms") or [])[:6]:
            if not isinstance(row, dict):
                continue
            mid = str(row.get("mechanism_id") or "").strip() or None
            if not mid:
                continue
            mechanisms.append({
                "mechanism_id": mid,
                "family": str(row.get("family") or "exhaustion"),
                "statement_zh": str(row.get("statement_zh") or "")[:500],
                "why_profit_first_zh": str(row.get("why_profit_first_zh") or "")[:500],
                "failure_states_zh": [
                    str(x)[:200] for x in (row.get("failure_states_zh") or [])[:6]
                ],
                "factor_hints": [
                    str(x) for x in (row.get("factor_hints") or [])[:6]
                    if str(x) in registry
                ],
                "repair_suggestions_zh": [
                    str(x)[:200] for x in (row.get("repair_suggestions_zh") or [])[:6]
                ],
                "source": "deepseek_mechanism",
            })
        audit["failure_root_causes_zh"] = [
            str(x)[:200] for x in (parsed.get("failure_root_causes_zh") or [])[:8]
        ]
    if not mechanisms:
        side = str(direction or "long").lower()
        mechanisms = [
            {
                "mechanism_id": "p3_fallback_exhaustion_reclaim",
                "family": "exhaustion",
                "statement_zh": "RSI超卖衰竭后布林下轨触及并回收中轨",
                "why_profit_first_zh": "确认回收后上行路径先于反向0.5%",
                "failure_states_zh": ["带宽剧烈扩张", "趋势仍向下"],
                "factor_hints": ["rsi_14", "bb_lower_dist", "bb_mid_reclaim", "bb_width"],
                "repair_suggestions_zh": ["加强sequence确认", "排除高波动失败态"],
                "source": "deterministic_fallback",
            },
            {
                "mechanism_id": "p3_fallback_vol_squeeze",
                "family": "vol_squeeze_break",
                "statement_zh": "压缩波动后突破确认",
                "why_profit_first_zh": "突破确认后扩张方向先走目标",
                "failure_states_zh": ["假突破回收"],
                "factor_hints": [
                    "bb_width",
                    "donchian20_long_break" if side == "long" else "donchian20_short_break",
                ],
                "repair_suggestions_zh": ["增加state压缩过滤"],
                "source": "deterministic_fallback",
            },
        ]
        audit["fallback"] = True
    public = []
    private = []
    for row in mechanisms:
        anon = dict(row)
        anon["anon_id"] = _anon_id(row, salt="mech")
        private.append(dict(anon, _provider="deepseek"))
        pub = dict(anon)
        pub.pop("source", None)
        public.append(pub)
    return {
        "ok": True,
        "mechanisms": public,
        "private_audit": private,
        "audit": audit,
        "run_id": run_id,
        "at": _now(),
    }


def _validate_attach_ast(event_ast, mechanism_id, rtype, factor_hints=None):
    validated = ac.validate_ast(event_ast)
    if not validated.get("ok"):
        return {
            "ok": False,
            "errors": validated.get("errors"),
            "mechanism_id": mechanism_id,
            "representation_type": rtype,
        }
    norm = validated.get("normalized") or event_ast
    dsl = ac.compile_ast_to_dsl(norm)
    return {
        "ok": True,
        "compile_ok": bool(dsl.get("ok")),
        "formal_ok": bool(dsl.get("formal_ok")),
        "mechanism_id": mechanism_id,
        "representation_type": rtype,
        "event_ast": norm,
        "event_ast_hash": ac.event_ast_hash(norm),
        "factor_hints": list(factor_hints or [])[:6],
        "dsl_reasons": list(dsl.get("reasons") or [])[:8],
        "anon_id": _anon_id(
            {"m": mechanism_id, "r": rtype, "h": ac.event_ast_hash(norm)}
        ),
    }


def generate_asts(mechanisms=None, direction="long", skip_llm=False, run_id=None):
    """Qwen: six heterogeneous ASTs per approved mechanism."""
    mechanisms = [m for m in (mechanisms or []) if isinstance(m, dict)]
    audit = {"provider": "qwen", "role": "generate_asts", "at": _now()}
    accepted = []
    rejected = []
    llm_rows = []
    enabled = str(os.environ.get("QIYU_P3_AI_COMMITTEE") or "1").lower() not in (
        "0", "false", "no", "off",
    )
    if mechanisms and (not skip_llm) and enabled:
        payload = {
            "mechanisms": [
                {
                    "mechanism_id": m.get("mechanism_id"),
                    "family": m.get("family"),
                    "factor_hints": m.get("factor_hints"),
                    "statement_zh": m.get("statement_zh"),
                }
                for m in mechanisms[:4]
            ],
            "feature_registry": feature_registry_payload(),
            "direction": direction,
            "required_representation_types": list(REPRESENTATION_TYPES),
        }
        res = _ai_json("qwen", AST_SYSTEM, payload, max_tokens=2200)
        audit["llm"] = {
            "ok": bool(res.get("ok")),
            "error": res.get("error"),
            "latency_sec": res.get("latency_sec"),
        }
        parsed = res.get("parsed") if isinstance(res.get("parsed"), dict) else {}
        llm_rows = [
            row for row in (parsed.get("asts") or [])
            if isinstance(row, dict)
        ][:48]
    for row in llm_rows:
        mid = str(row.get("mechanism_id") or "")
        rtype = str(row.get("representation_type") or "")
        pack = _validate_attach_ast(
            row.get("event_ast"), mid, rtype, row.get("factor_hints"),
        )
        if pack.get("ok") and pack.get("compile_ok"):
            pack["source"] = "qwen_ast"
            accepted.append(pack)
        else:
            rejected.append({
                "mechanism_id": mid,
                "representation_type": rtype,
                "errors": pack.get("errors") or pack.get("dsl_reasons"),
            })
    by_mech = {}
    for pack in accepted:
        by_mech.setdefault(pack["mechanism_id"], {})[pack["representation_type"]] = pack
    templates = {
        row["representation_type"]: row
        for row in ac.representation_asts_for_direction(direction)
    }
    base_mechs = mechanisms or [{
        "mechanism_id": "p3_fallback_exhaustion_reclaim",
        "factor_hints": ["rsi_14"],
    }]
    for mech in base_mechs:
        mid = str(mech.get("mechanism_id") or "unknown")
        slot = by_mech.setdefault(mid, {})
        for rtype in REPRESENTATION_TYPES:
            if rtype in slot:
                continue
            tpl = templates.get(rtype)
            if not tpl:
                continue
            pack = _validate_attach_ast(
                tpl.get("ast"), mid, rtype, mech.get("factor_hints"),
            )
            if pack.get("ok"):
                pack["source"] = "deterministic_template_fill"
                slot[rtype] = pack
                accepted.append(pack)
    audit["n_accepted"] = len(accepted)
    audit["n_rejected"] = len(rejected)
    public = []
    private = []
    for pack in accepted:
        pub = {
            "anon_id": pack.get("anon_id"),
            "mechanism_id": pack.get("mechanism_id"),
            "representation_type": pack.get("representation_type"),
            "event_ast": pack.get("event_ast"),
            "event_ast_hash": pack.get("event_ast_hash"),
            "factor_hints": pack.get("factor_hints"),
            "compile_ok": pack.get("compile_ok"),
            "formal_ok": pack.get("formal_ok"),
        }
        public.append(pub)
        private.append(dict(pub, source=pack.get("source"), _provider="qwen"))
    return {
        "ok": True,
        "asts": public,
        "rejected": rejected[:40],
        "private_audit": private,
        "audit": audit,
        "run_id": run_id,
        "at": _now(),
    }


def audit_diversity(anonymous_candidates=None, skip_llm=False, run_id=None):
    """GLM: missing families / supplement specs from anonymous fingerprints only."""
    fps = [_fingerprint_row(r) for r in (anonymous_candidates or []) if isinstance(r, dict)]
    families = sorted({str(r.get("family") or "") for r in fps if r.get("family")})
    rtypes = sorted({
        str(r.get("representation_type") or "")
        for r in fps if r.get("representation_type")
    })
    missing_r = [t for t in REPRESENTATION_TYPES if t not in rtypes]
    audit = {"provider": "glm", "role": "audit_diversity", "at": _now()}
    supplement = []
    enabled = str(os.environ.get("QIYU_P3_AI_COMMITTEE") or "1").lower() not in (
        "0", "false", "no", "off",
    )
    if (not skip_llm) and enabled:
        payload = {
            "anonymous_fingerprints": fps[:80],
            "present_families": families,
            "present_representation_types": rtypes,
            "missing_representation_types": missing_r,
            "feature_registry": feature_registry_payload(),
        }
        res = _ai_json("glm", DIVERSITY_SYSTEM, payload, max_tokens=1200)
        audit["llm"] = {
            "ok": bool(res.get("ok")),
            "error": res.get("error"),
            "latency_sec": res.get("latency_sec"),
        }
        parsed = res.get("parsed") if isinstance(res.get("parsed"), dict) else {}
        registry = set(ac.feature_registry())
        for row in (parsed.get("supplement_specs") or [])[:12]:
            if not isinstance(row, dict):
                continue
            supplement.append({
                "anon_id": _anon_id(row, salt="div"),
                "family": str(row.get("family") or "exhaustion"),
                "representation_type": str(
                    row.get("representation_type") or "sequence"
                ),
                "factor_hints": [
                    str(x) for x in (row.get("factor_hints") or [])[:6]
                    if str(x) in registry
                ],
                "statement_zh": str(row.get("statement_zh") or "")[:400],
                "source": "glm_diversity",
            })
        audit["missing_families"] = [
            str(x) for x in (parsed.get("missing_families") or [])[:12]
        ]
        audit["notes_zh"] = str(parsed.get("notes_zh") or "")[:500]
    if not supplement and missing_r:
        for rtype in missing_r[:4]:
            supplement.append({
                "anon_id": _anon_id({"r": rtype}, salt="div"),
                "family": "exhaustion",
                "representation_type": rtype,
                "factor_hints": ["rsi_14", "bb_mid_reclaim"],
                "statement_zh": "确定性补齐缺失表征 %s" % rtype,
                "source": "deterministic_diversity_fill",
            })
    return {
        "ok": True,
        "supplement_specs": supplement,
        "present_families": families,
        "present_representation_types": rtypes,
        "missing_representation_types": missing_r,
        "audit": audit,
        "run_id": run_id,
        "at": _now(),
    }


def diagnose_failures(failure_summary=None, skip_llm=False, run_id=None):
    """Kimi: failure codes + allowed/forbidden mods + repair wave plan."""
    summary = failure_summary if isinstance(failure_summary, dict) else {}
    codes = list(summary.get("dominant_codes") or summary.get("failure_codes") or [])
    audit = {"provider": "kimi", "role": "diagnose_failures", "at": _now()}
    result = {
        "failure_codes": [],
        "allowed_modifications": [],
        "forbidden_modifications": list(qopt.FORBIDDEN_ALWAYS),
        "repair_wave_plan": [],
        "may_declare_direction_dead": False,
    }
    enabled = str(os.environ.get("QIYU_P3_AI_COMMITTEE") or "1").lower() not in (
        "0", "false", "no", "off",
    )
    if (not skip_llm) and enabled:
        payload = {
            "failure_summary": summary,
            "allowed_mods_catalog": {
                k: list(v) for k, v in (qopt.ALLOWED_MODS or {}).items()
            },
            "forbidden_always": list(qopt.FORBIDDEN_ALWAYS),
        }
        res = _ai_json("kimi", FAILURE_SYSTEM, payload, max_tokens=1000)
        audit["llm"] = {
            "ok": bool(res.get("ok")),
            "error": res.get("error"),
            "latency_sec": res.get("latency_sec"),
        }
        parsed = res.get("parsed") if isinstance(res.get("parsed"), dict) else {}
        if parsed:
            result["failure_codes"] = [
                str(x) for x in (parsed.get("failure_codes") or codes)[:12]
            ]
            result["allowed_modifications"] = [
                str(x) for x in (parsed.get("allowed_modifications") or [])[:16]
            ]
            forbidden = [
                str(x) for x in (parsed.get("forbidden_modifications") or [])[:16]
            ]
            for item in qopt.FORBIDDEN_ALWAYS:
                if item not in forbidden:
                    forbidden.append(item)
            result["forbidden_modifications"] = forbidden
            result["repair_wave_plan"] = [
                row for row in (parsed.get("repair_wave_plan") or [])[:6]
                if isinstance(row, dict)
            ]
            result["notes_zh"] = str(parsed.get("notes_zh") or "")[:500]
    if not result["failure_codes"]:
        result["failure_codes"] = list(codes) or [
            qopt.LOW_WIN_RATE, qopt.LOW_PROFIT_FIRST_RATE, qopt.HIGH_MAE,
        ]
    allowed = set(result["allowed_modifications"])
    for code in result["failure_codes"]:
        for mod in (qopt.ALLOWED_MODS.get(code) or ()):
            allowed.add(mod)
    result["allowed_modifications"] = sorted(allowed)
    result["may_declare_direction_dead"] = False
    if not result["repair_wave_plan"]:
        result["repair_wave_plan"] = [
            {"wave": 2, "focus": "sequence", "actions_zh": ["增加时序确认", "排除失败状态"]},
            {"wave": 3, "focus": "state", "actions_zh": ["压缩波动状态过滤", "相对强度确认"]},
        ]
    return {
        "ok": True,
        "diagnosis": result,
        "audit": audit,
        "run_id": run_id,
        "at": _now(),
    }


def generate_repair_wave(diagnosis=None, direction="long", limit=12, run_id=None):
    """Build probeable repair hypotheses from Kimi diagnosis + AST templates."""
    diagnosis = (diagnosis or {}).get("diagnosis") or diagnosis or {}
    codes = list(diagnosis.get("failure_codes") or [])
    hyps = qopt.build_quality_repair_hypotheses(
        direction=direction, failure_codes=codes, limit=limit,
    )
    for row in hyps:
        row["source"] = "p3_repair_wave"
        row["p3_repair_plan"] = list(diagnosis.get("repair_wave_plan") or [])[:4]
        row["forbidden_modifications"] = list(
            diagnosis.get("forbidden_modifications") or qopt.FORBIDDEN_ALWAYS
        )
    return {
        "ok": True,
        "hypotheses": hyps,
        "n": len(hyps),
        "failure_codes": codes,
        "run_id": run_id,
        "at": _now(),
    }


def _near_duplicate(a, b):
    if not a or not b:
        return False
    if a.get("event_ast_hash") and a.get("event_ast_hash") == b.get("event_ast_hash"):
        return True
    ha = set(a.get("factor_hints") or [])
    hb = set(b.get("factor_hints") or [])
    if ha and hb and ha == hb and a.get("representation_type") == b.get("representation_type"):
        return True
    return False


def asts_to_hypotheses(asts, direction="long", family_default="exhaustion"):
    """Deterministic admission: compilable ASTs -> hypothesis rows."""
    out = []
    for index, pack in enumerate(asts or []):
        if not isinstance(pack, dict) or not pack.get("compile_ok"):
            continue
        if not pack.get("event_ast"):
            continue
        if any(_near_duplicate(pack, prev) for prev in out):
            continue
        mid = str(pack.get("mechanism_id") or "p3_mech")
        rtype = str(pack.get("representation_type") or "rank")
        hid = "H_p3_%s_%s_%d" % (mid[:24], rtype, index + 1)
        fam = family_default
        if "squeeze" in mid:
            fam = "vol_squeeze_break"
        elif "exhaust" in mid:
            fam = "exhaustion"
        out.append({
            "hypothesis_id": hid,
            "mechanism_id": mid,
            "family": fam,
            "statement_zh": "P3 AST表征[%s]·%s" % (rtype, mid),
            "factor_hints": list(pack.get("factor_hints") or [])[:6],
            "observable_proxy": list(pack.get("factor_hints") or [])[:6],
            "predicted_direction": str(direction or "long").lower(),
            "source": "p3_ai_ast_committee",
            "path": "p3_ast",
            "priority_boost": 14.0,
            "representation_type": rtype,
            "event_ast": pack.get("event_ast"),
            "event_ast_hash": pack.get("event_ast_hash"),
            "event_ast_formal_ok": bool(pack.get("formal_ok")),
            "anon_id": pack.get("anon_id"),
            "may_research": True,
            "contract_relevant": True,
        })
    return out


def run_creation_committee(
    brief="",
    symbol="",
    timeframe="",
    direction="long",
    failure_summary=None,
    skip_llm=False,
    run_id=None,
):
    """Two-round anonymous committee -> admitted AST hypotheses."""
    round1_mech = generate_mechanisms(
        brief=brief, symbol=symbol, timeframe=timeframe, direction=direction,
        failure_summary=failure_summary, skip_llm=skip_llm, run_id=run_id,
    )
    round1_ast = generate_asts(
        mechanisms=round1_mech.get("mechanisms") or [],
        direction=direction, skip_llm=skip_llm, run_id=run_id,
    )
    anon_pool = []
    for row in (round1_ast.get("asts") or []):
        fam = next(
            (
                m.get("family") for m in (round1_mech.get("mechanisms") or [])
                if m.get("mechanism_id") == row.get("mechanism_id")
            ),
            "exhaustion",
        )
        anon_pool.append({
            "anon_id": row.get("anon_id"),
            "family": fam,
            "representation_type": row.get("representation_type"),
            "factor_hints": row.get("factor_hints"),
            "event_ast_hash": row.get("event_ast_hash"),
            "compile_ok": row.get("compile_ok"),
            "formal_ok": row.get("formal_ok"),
        })
    round2_div = audit_diversity(
        anonymous_candidates=anon_pool, skip_llm=skip_llm, run_id=run_id,
    )
    extra_asts = []
    templates = {
        row["representation_type"]: row
        for row in ac.representation_asts_for_direction(direction)
    }
    for spec in (round2_div.get("supplement_specs") or [])[:8]:
        rtype = spec.get("representation_type") or "sequence"
        tpl = templates.get(rtype) or templates.get("rank")
        if not tpl:
            continue
        pack = _validate_attach_ast(
            tpl.get("ast"),
            "p3_div_%s" % (spec.get("family") or "exhaustion"),
            rtype,
            spec.get("factor_hints"),
        )
        if pack.get("ok") and pack.get("compile_ok"):
            pack["source"] = "glm_diversity_materialized"
            extra_asts.append(pack)
    all_asts = list(round1_ast.get("asts") or []) + extra_asts
    hyps = asts_to_hypotheses(all_asts, direction=direction)
    diagnosis = diagnose_failures(
        failure_summary=failure_summary, skip_llm=skip_llm, run_id=run_id,
    )
    repair = generate_repair_wave(
        diagnosis=diagnosis, direction=direction, limit=8, run_id=run_id,
    )
    for row in repair.get("hypotheses") or []:
        if any(_near_duplicate(
            {
                "event_ast_hash": row.get("event_ast_hash"),
                "factor_hints": row.get("factor_hints"),
                "representation_type": row.get("representation_type"),
            },
            {
                "event_ast_hash": h.get("event_ast_hash"),
                "factor_hints": h.get("factor_hints"),
                "representation_type": h.get("representation_type"),
            },
        ) for h in hyps):
            continue
        hyps.append(row)
    unique_hashes = sorted({
        str(h.get("event_ast_hash")) for h in hyps if h.get("event_ast_hash")
    })
    return {
        "ok": True,
        "hypotheses": hyps,
        "n_hypotheses": len(hyps),
        "unique_ast_hash_n": len(unique_hashes),
        "unique_ast_hashes": unique_hashes[:40],
        "representation_types": sorted({
            str(h.get("representation_type"))
            for h in hyps if h.get("representation_type")
        }),
        "round1": {
            "mechanisms_n": len(round1_mech.get("mechanisms") or []),
            "asts_n": len(round1_ast.get("asts") or []),
            "asts_rejected_n": len(round1_ast.get("rejected") or []),
        },
        "round2": {
            "diversity_supplement_n": len(round2_div.get("supplement_specs") or []),
            "missing_representation_types": round2_div.get("missing_representation_types"),
            "diagnosis_codes": (diagnosis.get("diagnosis") or {}).get("failure_codes"),
            "repair_n": repair.get("n"),
        },
        "audits": {
            "mechanisms": round1_mech.get("audit"),
            "asts": round1_ast.get("audit"),
            "diversity": round2_div.get("audit"),
            "failures": diagnosis.get("audit"),
        },
        "anonymous_fingerprints": anon_pool[:80],
        "skip_llm": bool(skip_llm),
        "run_id": run_id,
        "at": _now(),
    }


# §11 interface aliases expected by plan
generate_repair_wave_plan = generate_repair_wave
