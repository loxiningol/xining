# -*- coding: utf-8 -*-
"""诊改搜挂：分阶过门 + 机器结构化批评（Self-Refine 的 Feedback 由机器写）。"""
from __future__ import print_function

import json

ALLOWED_TIMING = (
    "skdj_k", "skdj_d", "skdj_kd", "skdj_diff",
    "macd_hist", "macd_dif", "macd_dd", "cci", "kdj_k", "kdj_d", "kdj_kd",
)
FORBID = ("candle_pattern", "donchian", "ema", "change_symbol", "change_family")
# Soft (family/symbol): explore-budget gated in thin loop — not absolute deadlocks.
# Hard: position geometry must not drift during refine.
LOCK_KEYS_SOFT = ("family", "symbol")
LOCK_KEYS_HARD = ("held", "xwin", "fast", "slow", "z", "atr", "hold")
LOCK_KEYS = LOCK_KEYS_SOFT + LOCK_KEYS_HARD


def classify_stage(row):
    """Return stage id for curriculum."""
    if not isinstance(row, dict):
        return "S1_n"
    if row.get("hit_floor"):
        return "S4_pass"
    n = 0
    try:
        n = int(row.get("n") or 0)
    except Exception:
        n = 0
    hitch = row.get("hitch")
    try:
        hitch = None if hitch is None else float(hitch)
    except Exception:
        hitch = None
    e = row.get("E")
    if e is None:
        e = row.get("E_path")
    try:
        e = None if e is None else float(e)
    except Exception:
        e = None
    weekly = row.get("weekly")
    try:
        weekly = None if weekly is None else float(weekly)
    except Exception:
        weekly = None

    if n < 30:
        return "S1_n"
    if weekly is not None and weekly < 0.50:
        return "S1_n"
    if hitch is not None and hitch >= 0.30:
        return "S2_hitch"
    if e is not None and e < 0.003:
        return "S3_E"
    # C not floor yet
    return "S4_C"


def stage_need(stage):
    if stage == "S1_n":
        return {"n_ge": 30, "weekly_ge": 0.50}
    if stage == "S2_hitch":
        return {"hitch_lt": 0.30}
    if stage == "S3_E":
        return {"E_ge": 0.003}
    if stage == "S4_C":
        return {"C_week_pct": 0.1, "C_week_oos_pct": 0.05, "usable": True}
    return {"hit_floor": True}


def stage_hint(stage, diagnosis=None):
    diagnosis = diagnosis or {}
    dhint = str(diagnosis.get("hint") or "")
    if stage == "S1_n":
        return (
            "未达到机器基础门槛：自然样本不足（证据不足）。"
            "禁止放宽参数凑笔数。"
            "请换不同合法 timing 原子组合继续探索；"
            "同一周期+家族须完成最小探索深度后方可换周期/换族或结案。"
            + ((" " + dhint) if dhint else "")
        )
    if stage == "S2_hitch":
        return "止损先触过高：收紧 SKDJ/MACD，不要加宽 ATR。" + ((" " + dhint) if dhint else "")
    if stage == "S3_E":
        return "E 不足：按诊包改 K/D 相对位置或差值，配 MACD/CCI。" + ((" " + dhint) if dhint else "")
    if stage == "S4_C":
        return "门将过但双 C 未正：只改 timing，保持挂钩/DSR/min_trl。" + ((" " + dhint) if dhint else "")
    return "已达双 C 门。"


def blockers_from_row(row, stage):
    out = []
    if stage == "S1_n":
        out.append("n_low")
    if stage == "S2_hitch":
        out.append("hitch_high")
    if stage == "S3_E":
        out.append("E_thin")
    if stage == "S4_C":
        out.append("C_not_floor")
        for b in list(row.get("blockers") or [])[:4]:
            out.append(str(b))
    return out


def build_critique(recipe, machine, identity, adjusts, diagnosis=None, explore_state=None):
    """Machine critic payload for Kimi refine (JSON-only instruction)."""
    stage = classify_stage(machine or {})
    diagnosis = diagnosis or (machine or {}).get("diagnosis") or {}
    critique = {
        "schema": "diagnose_refine_v1",
        "stage": stage,
        "identity": identity,
        "adjusts": int(adjusts or 0),
        "lock": list(LOCK_KEYS),
        "lock_hard": list(LOCK_KEYS_HARD),
        "lock_soft": list(LOCK_KEYS_SOFT),
        "edit_budget": 1,
        "allowed_factors": list(ALLOWED_TIMING),
        "forbid": list(FORBID),
        "blockers": blockers_from_row(machine or {}, stage),
        "diagnosis": {
            "hint": diagnosis.get("hint"),
            "is_w22": ((diagnosis.get("windows") or {}).get("IS") or {}).get("w22"),
            "oos_w22": ((diagnosis.get("windows") or {}).get("OOS") or {}).get("w22"),
        },
        "machine": {
            "phase": (machine or {}).get("phase"),
            "n": (machine or {}).get("n"),
            "n_oos": (machine or {}).get("n_oos"),
            "hitch": (machine or {}).get("hitch"),
            "E": (machine or {}).get("E") or (machine or {}).get("E_path"),
            "C_week_pct": (machine or {}).get("C_week_pct"),
            "C_week_oos_pct": (machine or {}).get("C_week_oos_pct"),
            "hit_floor": (machine or {}).get("hit_floor"),
        },
        "need_next": stage_need(stage),
        "hint": stage_hint(stage, diagnosis),
        "recipe": recipe or {},
    }
    # §4.3: same op-group streak does not count toward explore depth
    hist = list((machine or {}).get("timing_history") or [])
    if not hist and explore_state is not None:
        hist = list((explore_state or {}).get("timing_history") or [])
    cur = list((recipe or {}).get("timing") or [])
    if cur:
        try:
            from dual_engine_workflow_v2 import thin_timing_atoms as _tta
            counts, warn = _tta.diversity_counts(hist, cur)
            critique["diversity_counts_toward_depth"] = bool(counts)
            if warn:
                critique["diversity_warn_zh"] = warn
                critique["hint"] = (critique.get("hint") or "") + " " + warn
                # Do not allow loosen_timing when diversity gate fails
                forbid = list(critique.get("forbid") or [])
                if "loosen_timing" not in forbid:
                    forbid.append("loosen_timing")
                critique["forbid"] = forbid
        except Exception:
            pass
    # Explore-budget action hints (§1.3 / §2.1)
    try:
        from dual_engine_workflow_v2 import thin_create_policy as _tcp
        expl = (machine or {}).get("explore") or {}
        if not expl and explore_state is not None:
            expl = _tcp.explore_actions_for_critique(explore_state, recipe, stage)
        if expl:
            critique["explore"] = expl
            acts = list(expl.get("actions_allowed") or [])
            if acts:
                critique["actions_allowed"] = acts
            extra_forbid = list(expl.get("forbid_extra") or [])
            if extra_forbid:
                forbid = list(critique.get("forbid") or [])
                for item in extra_forbid:
                    if item not in forbid:
                        forbid.append(item)
                critique["forbid"] = forbid
            if stage == "S1_n" and not expl.get("depth_ok"):
                eh = (
                    "探索深度未满：evals=%s/%s，timing指纹=%s/%s。"
                    % (
                        expl.get("evals"), expl.get("min_evals"),
                        expl.get("timing_fps_n"), expl.get("min_timing_fps"),
                    )
                )
                critique["hint"] = ((critique.get("hint") or "") + " " + eh).strip()
    except Exception:
        pass
    return critique


def critique_user_message(critique):
    """Prompt body: no prose required from model; JSON recipe only."""
    body = json.dumps(critique, ensure_ascii=False, default=str)
    if len(body) > 1400:
        body = body[:1400]
    return (
        "机器批评如下。禁止文字。锁死 identity 对应标的与位置几何。"
        "edit_budget=1：本轮只改 timing 里 1 个因子（或只改其 value/window）。"
        "禁止 candle_pattern/donchian/换标的。只输出 {\"recipe\":{...}}。\n"
        + body
    )


def timing_leaf_variants(base_timing, diagnosis=None, limit=32):
    """Fixed neighborhood micro-search candidates (no Optuna dependency)."""
    diagnosis = diagnosis or {}
    hint = str(diagnosis.get("hint") or "")
    variants = []
    peer = {"factor": "macd_hist", "operator": "above", "value": 0}

    def add(leaves):
        if len(variants) >= limit:
            return
        variants.append(list(leaves))

    # baseline-ish
    for w in (9, 22):
        for k in (45, 50, 55, 60):
            add([
                {"factor": "skdj_k", "operator": "above", "value": k, "window": w},
                dict(peer),
            ])
        add([
            {"factor": "skdj_kd", "operator": "above", "value": 0, "window": w},
            dict(peer),
        ])
        add([
            {"factor": "skdj_kd", "operator": "cross_up", "value": 0, "window": w},
            dict(peer),
        ])
        for d in (-4, -2, 0, 2):
            add([
                {"factor": "skdj_diff", "operator": "above", "value": d, "window": w},
                dict(peer),
                {"factor": "skdj_k", "operator": "below", "value": 90, "window": w},
            ])
        add([
            {"factor": "skdj_k", "operator": "above", "value": 50, "window": w},
            {"factor": "cci", "operator": "above", "value": -100, "window": 20},
            dict(peer),
        ])
    if "金叉" in hint or "oos_K" in hint:
        # prioritize cross_up front
        variants = sorted(
            variants,
            key=lambda t: (0 if any(x.get("operator") == "cross_up" for x in t) else 1),
        )
    # keep base first if provided
    if base_timing:
        variants = [list(base_timing)] + [v for v in variants if v != list(base_timing)]
    return variants[:limit]
