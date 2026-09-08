# -*- coding: utf-8 -*-
"""诊改搜挂：分阶过门 + 机器结构化批评（Self-Refine 的 Feedback 由机器写）。"""
from __future__ import print_function

import json

try:
    from dual_engine_workflow_v2.thin_timing_atoms import (
        TIMING_ATOM_PAIRS as _TIMING_ATOM_PAIRS,
        TIMING_SKDJ as _TIMING_SKDJ,
        TIMING_PEER as _TIMING_PEER,
    )
    ALLOWED_TIMING = tuple(sorted(set([a for a, _b in _TIMING_ATOM_PAIRS])))
    ALLOWED_TIMING_PAIRS = tuple(_TIMING_ATOM_PAIRS)
except Exception:  # pragma: no cover
    ALLOWED_TIMING = (
        "skdj_k", "skdj_d", "skdj_kd", "skdj_diff",
        "macd_hist", "macd_dif", "macd_dd", "cci", "kdj_k", "kdj_d", "kdj_kd",
    )
    ALLOWED_TIMING_PAIRS = ()
    _TIMING_SKDJ = frozenset(ALLOWED_TIMING[:4])
    _TIMING_PEER = frozenset(ALLOWED_TIMING[4:])
FORBID = (
    "candle_pattern", "donchian", "ema", "change_symbol", "change_family",
    "loosen_timing",
)
# Soft (family/symbol): explore-budget gated in thin loop — not absolute deadlocks.
# Hard: position geometry must not drift during refine.
LOCK_KEYS_SOFT = ("family", "symbol")
# Keep parity with thin_create_policy.LOCK_KEYS_HARD (incl. channel dwin).
LOCK_KEYS_HARD = ("held", "xwin", "dwin", "fast", "slow", "z", "atr", "hold")
LOCK_KEYS = LOCK_KEYS_SOFT + LOCK_KEYS_HARD

# S2 RR geometry bounds (escape-proof). Outside → reject + critique.
RR_XWIN_MIN, RR_XWIN_MAX = 1.0, 10.0
RR_ATR_MIN, RR_ATR_MAX = 0.5, 3.0


def rr_geometry_bounds_ok(recipe):
    """Return (ok, error_zh). None values skip that field."""
    if not isinstance(recipe, dict):
        return True, None
    try:
        if recipe.get("xwin") is not None:
            x = float(recipe.get("xwin"))
            if x < RR_XWIN_MIN or x > RR_XWIN_MAX:
                return False, (
                    "参数超出合理区间：xwin须在%.1f-%.1f，当前=%s。"
                    "请回到合理区间。禁止文字。"
                    % (RR_XWIN_MIN, RR_XWIN_MAX, recipe.get("xwin"))
                )
    except Exception:
        return False, "xwin非法。禁止文字。"
    try:
        if recipe.get("atr") is not None:
            a = float(recipe.get("atr"))
            if a < RR_ATR_MIN or a > RR_ATR_MAX:
                return False, (
                    "参数超出合理区间：atr须在%.1f-%.1f，当前=%s。"
                    "请回到合理区间。禁止文字。"
                    % (RR_ATR_MIN, RR_ATR_MAX, recipe.get("atr"))
                )
    except Exception:
        return False, "atr非法。禁止文字。"
    return True, None


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
        return (
            "止损先触过高：盈亏结构差。"
            "本轮允许改几何：xwin至少放大至1.5倍，或atr收紧至0.7倍（禁止加宽atr）；"
            "同时必须改 timing 的 factor|operator 结构，禁止只拧阈值。"
            "连续两轮仍 hitch≥30% 将由机器换路线。"
            + ((" " + dhint) if dhint else "")
        )
    if stage == "S3_E":
        return (
            "E 不足：优先换 timing 结构（cross/between/相对位置），"
            "禁止阈值微调；可配 MACD/CCI。"
            + ((" " + dhint) if dhint else "")
        )
    if stage == "S4_C":
        return "门将过但双 C 未正：只改 timing 结构，保持挂钩/DSR/min_trl。" + ((" " + dhint) if dhint else "")
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
    import os as _os
    free = str(_os.environ.get("KDH_FREE_CREATE") or "1").strip().lower() not in (
        "0", "false", "no", "off",
    )
    stage = classify_stage(machine or {})
    diagnosis = diagnosis or (machine or {}).get("diagnosis") or {}
    critique = {
        "schema": "diagnose_refine_v1",
        "stage": stage,
        "identity": identity,
        "adjusts": int(adjusts or 0),
        "lock": [] if free else list(LOCK_KEYS),
        "lock_hard": [] if free else list(LOCK_KEYS_HARD),
        "lock_soft": [] if free else list(LOCK_KEYS_SOFT),
        "edit_budget": 4 if free else 1,
        "free_create": free,
        "allowed_factors": list(ALLOWED_TIMING),
        "allowed_timing_pairs": [
            "%s %s" % (a, b) for a, b in (ALLOWED_TIMING_PAIRS or ())
        ],
        "forbid": list(FORBID),
        "actions_allowed": (
            [
                "refine_timing_diverse", "refine_geometry_rr",
                "switch_family", "switch_tf", "switch_route",
            ]
            if free
            else (
                ["refine_timing_diverse"]
                if stage == "S1_n"
                else ["refine_timing"]
            )
        ),
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
        "hint": (
            "自由创造：可改完整 recipe（标的/家族/周期/几何/timing）。禁止复读同一壳子。"
            if free
            else stage_hint(stage, diagnosis)
        ),
        "recipe": recipe or {},
    }
    # S2: temporarily unlock RR geometry (xwin/atr); force structure edit not value nudge.
    if (not free) and stage == "S2_hitch":
        critique["lock_hard"] = [
            k for k in list(LOCK_KEYS_HARD) if k not in ("xwin", "atr", "dwin")
        ]
        critique["actions_allowed"] = ["refine_geometry_rr", "refine_timing_structure"]
        critique["edit_budget"] = 2
        critique["forbid"] = list(critique.get("forbid") or []) + ["value_only_tweak"]
        critique["rr_hint"] = {
            "xwin_scale_ge": 1.5,
            "atr_scale_le": 0.7,
            "forbid_widen_atr": True,
            "xwin_min": RR_XWIN_MIN,
            "xwin_max": RR_XWIN_MAX,
            "atr_min": RR_ATR_MIN,
            "atr_max": RR_ATR_MAX,
        }
    if free:
        critique["rr_hint"] = {
            "xwin_min": RR_XWIN_MIN,
            "xwin_max": RR_XWIN_MAX,
            "atr_min": RR_ATR_MIN,
            "atr_max": RR_ATR_MAX,
            "note_zh": "物理区间约束，不是身份焊死",
        }
    # OOS_SPARSE: prefer switch_tf (new contract resets geometry). Do NOT ask to edit hold.
    try:
        n_oos = int((machine or {}).get("n_oos") or 0)
    except Exception:
        n_oos = 0
    if n_oos > 0 and n_oos < 25 and stage in ("S3_E", "S4_C", "S4_pass"):
        exec_tf = (
            (recipe or {}).get("exec_tf")
            or (recipe or {}).get("timeframe")
            or "1h"
        )
        critique["oos_phase"] = "OOS_SPARSE"
        if free:
            critique["hint"] = (
                "统计窗口内交易机会偏少（仅%d笔）。可换周期/换几何/换标的开新合同；"
                "禁止为凑笔数放宽 timing。"
                % n_oos
            ).strip()
        else:
            critique["hint"] = (
                "统计窗口内交易机会偏少（仅%d笔）。几何锁限制修改 hold。"
                "解决路径：① 接受 switch_tf，从 %s 切到更密周期（如15m）；"
                "新合同会重置几何，须按K线密度重算 hold"
                "（同墙钟持有在更密周期对应更多根，例1h hold=20≈15m hold=80，不是÷4成5）。"
                "② 等待研究窗扩深生效。禁止为凑笔数放宽 timing，禁止本轮改 hold。"
                % (n_oos, exec_tf)
            ).strip()
        acts = list(critique.get("actions_allowed") or [])
        if "switch_tf" not in acts:
            acts.append("switch_tf")
        critique["actions_allowed"] = acts
        if not free:
            critique["forbid"] = list(critique.get("forbid") or []) + [
                "change_hold", "loosen_timing",
            ]
    # Negative-sample summaries (C<-1%): ONLY S2/S3 — never pollute S1_n
    neg = []
    if stage in ("S2_hitch", "S3_E") and isinstance(explore_state, dict):
        neg = list(explore_state.get("neg_summaries") or [])[:5]
    if neg:
        critique["neg_summaries"] = neg
        critique["hint"] = (
            (critique.get("hint") or "")
            + " 避开近期不良实践："
            + "；".join(
                "%s" % (row.get("summary") or row)
                for row in neg[:5]
            )
        ).strip()
    # P0-Q dimension redundancy
    try:
        from dual_engine_workflow_v2 import creation_dimension_policy as _cdp
        dim_state = explore_state if isinstance(explore_state, dict) else {}
        _ok, dim_code, dim_payload = _cdp.apply_dimension_policy(
            dim_state, (recipe or {}).get("timing"),
        )
        critique["dimension"] = dim_payload
        if dim_code == "dimension_redundant_soft":
            critique["hint"] = (
                (critique.get("hint") or "") + " " + (_cdp.SOFT_WARN)
            ).strip()
            critique["dimension_soft"] = True
        if dim_code == "dimension_redundant_hard":
            critique["actions_allowed"] = ["close", "switch_route"]
            critique["forbid"] = list(critique.get("forbid") or []) + [
                "refine_timing", "refine_timing_diverse", "loosen_timing",
            ]
            critique["reject"] = "dimension_redundant_hard"
            critique["hint"] = (
                "维度冗余硬否决：同维 D2 族过多且连续无视软警告。"
                "须换维（量能/波动）或换路线，禁止继续堆振荡器。"
            )
    except Exception:
        pass
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
            if acts and critique.get("reject") != "dimension_redundant_hard":
                # Preserve S2 RR actions if explore only returned refine_timing.
                if stage == "S2_hitch":
                    for a in ("refine_geometry_rr", "refine_timing_structure"):
                        if a not in acts:
                            acts.append(a)
                    acts = [a for a in acts if a != "refine_timing"] or acts
                critique["actions_allowed"] = acts
                if stage == "S2_hitch":
                    critique["lock_hard"] = [
                        k for k in list(LOCK_KEYS_HARD)
                        if k not in ("xwin", "atr", "dwin")
                    ]
                    critique["edit_budget"] = max(int(critique.get("edit_budget") or 1), 2)
            extra_forbid = list(expl.get("forbid_extra") or [])
            if extra_forbid:
                forbid = list(critique.get("forbid") or [])
                for item in extra_forbid:
                    if item not in forbid:
                        forbid.append(item)
                critique["forbid"] = forbid
            if stage == "S1_n" and not expl.get("depth_ok"):
                eh = (
                    "探索深度未满：evals=%s/%s，结构指纹=%s/%s。"
                    % (
                        expl.get("evals"), expl.get("min_evals"),
                        expl.get("timing_fps_n"), expl.get("min_timing_fps"),
                    )
                )
                critique["hint"] = ((critique.get("hint") or "") + " " + eh).strip()
    except Exception:
        pass
    # Contract fields echo
    if isinstance(recipe, dict):
        critique["contract"] = {
            "route": recipe.get("route"),
            "exec_tf": recipe.get("exec_tf") or recipe.get("timeframe"),
            "filter_tfs": recipe.get("filter_tfs") or [],
        }
    return critique


def critique_user_message(critique):
    """Prompt body: no prose required from model; JSON recipe only."""
    body = json.dumps(critique, ensure_ascii=False, default=str)
    if len(body) > 1400:
        body = body[:1400]
    stage = str((critique or {}).get("stage") or "")
    if stage == "S2_hitch":
        head = (
            "机器批评如下。禁止文字。S2盈亏比优先：允许改 xwin/atr；"
            "必须改 timing 的 factor|operator（禁止只改 value）。"
            "合同保留 route/exec_tf/filter_tfs。只输出 {\"recipe\":{...}}。\n"
        )
    else:
        head = (
            "机器批评如下。禁止文字。锁死 identity 对应标的与位置几何。"
            "edit_budget=1：本轮改 timing 结构（换 factor|operator），禁止只拧阈值。"
            "合同字段须保留 route / exec_tf(=timeframe) / filter_tfs。"
            "禁止 candle_pattern/donchian/换标的。只输出 {\"recipe\":{...}}。\n"
        )
    return head + body


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
