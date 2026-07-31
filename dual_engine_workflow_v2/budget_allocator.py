# -*- coding: utf-8 -*-
"""Research budget allocator — contextual multi-arm bandit lite.

Allocates trial budget across generators / families / search modes.
Reward is a constrained blend of info gain proxies — NOT raw PnL.
No end-to-end RL over strategy generation.
"""
from __future__ import print_function

import json
import math
import os
import random
import threading
from datetime import datetime
from pathlib import Path

from . import generator_scorecard as scorecard
from . import mechanism_beliefs as beliefs


_LOCK = threading.Lock()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def state_path():
    d = _root() / "auto_trade" / "dual_engine" / "budget_allocator"
    d.mkdir(parents=True, exist_ok=True)
    return d / "allocator_state.json"


DEFAULT_ARMS = (
    "mechanism_scientist",
    "empirical_scientist",
    "symbolic_searcher",
    "family:mean_reversion",
    "family:vol_squeeze_break",
    "family:liquidity_sweep",
    "family:trend_pullback",
    "family:crowding_fade",
    "family:liquidation_bounce",
)


def load_state():
    path = state_path()
    if not path.exists():
        return {
            "schema": "qiyu_budget_allocator_v1",
            "arms": {a: {"pulls": 0, "reward_sum": 0.0} for a in DEFAULT_ARMS},
            "updated_at": _now(),
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "schema": "qiyu_budget_allocator_v1",
            "arms": {a: {"pulls": 0, "reward_sum": 0.0} for a in DEFAULT_ARMS},
            "updated_at": _now(),
        }


def save_state(state):
    state = dict(state or {})
    state["updated_at"] = _now()
    with _LOCK:
        state_path().write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return state


def _arm_reward_from_scorecard(name, summary):
    """Constrained reward — never PnL alone."""
    s = summary or {}
    probe_s = float(s.get("probe_survival_rate") or 0)
    asm = float(s.get("assembly_rate") or 0)
    dir_acc = float(s.get("direction_accuracy") or 0.5)
    cal_err = float(s.get("calibration_error") or 0.5)
    novelty = float(s.get("novelty_score") or 0.5)
    cost_bias = float(s.get("cost_underestimation_bias") or 0)
    # weights fixed by research contract (not free LLM)
    r = (
        0.25 * probe_s
        + 0.20 * asm
        + 0.20 * dir_acc
        + 0.15 * novelty
        + 0.10 * max(0.0, 1.0 - cal_err)
        - 0.20 * cost_bias
        - 0.10 * 0.3  # baseline research cost
    )
    return max(-0.5, min(1.0, r))


def observe_attribution(attribution, contract_compare=None):
    """Online update after one outcome (medium timescale)."""
    state = load_state()
    arms = state.setdefault("arms", {})
    gen = (attribution or {}).get("generator") or "unknown"
    fam = (attribution or {}).get("family")
    rv = (attribution or {}).get("reward_vector") or {}

    # vector → scalar only at allocation layer
    r = (
        0.25 * float(rv.get("prediction_accuracy") or 0)
        + 0.20 * float(rv.get("mechanism_consistency") or 0)
        + 0.15 * float(rv.get("oos_reproducibility") or 0)
        + 0.15 * float(rv.get("net_edge") or 0)
        + 0.10 * float(rv.get("novelty") or 0.5)
        - 0.15 * float(rv.get("overfitting_risk") or 0.3)
        - 0.10 * float(rv.get("research_cost") or 0.3)
    )
    # execution-dominated failure: do not punish mechanism family hard
    resp = (attribution or {}).get("responsibility") or {}
    if float(resp.get("execution_failure") or 0) >= 0.35:
        if fam:
            arm = "family:%s" % fam
            slot = arms.setdefault(arm, {"pulls": 0, "reward_sum": 0.0})
            slot["pulls"] += 1
            slot["reward_sum"] += 0.05  # weak hold
        exe_arm = arms.setdefault("execution_engineer", {"pulls": 0, "reward_sum": 0.0})
        exe_arm["pulls"] += 1
        exe_arm["reward_sum"] += -0.25
    else:
        for arm_name in (gen, ("family:%s" % fam) if fam else None):
            if not arm_name:
                continue
            slot = arms.setdefault(arm_name, {"pulls": 0, "reward_sum": 0.0})
            slot["pulls"] += 1
            slot["reward_sum"] += float(r)

    save_state(state)
    return {"ok": True, "reward_scalar": r, "at": _now()}


def allocate(total_budget=320, context=None, epsilon=0.15):
    """Return next-round budget shares + concrete discovery knobs."""
    context = context or {}
    state = load_state()
    arms = state.get("arms") or {}
    summaries = scorecard.all_summaries()

    # Merge UCB-ish scores
    scored = []
    total_pulls = sum(int((arms.get(a) or {}).get("pulls") or 0) for a in arms) + 1
    names = set(DEFAULT_ARMS) | set(arms.keys()) | set(summaries.keys())
    for name in names:
        if name.startswith("O_") or name == "unknown":
            continue
        slot = arms.get(name) or {"pulls": 0, "reward_sum": 0.0}
        pulls = max(1, int(slot.get("pulls") or 0))
        avg = float(slot.get("reward_sum") or 0) / float(pulls)
        # blend with scorecard
        sc = _arm_reward_from_scorecard(name, summaries.get(name))
        blend = 0.55 * avg + 0.45 * sc
        # mechanism posterior for family arms
        if name.startswith("family:"):
            fam = name.split(":", 1)[1]
            # soft prior from any mechanism belief with that family — skip heavy scan
            blend += 0.05
        ucb = blend + math.sqrt(2.0 * math.log(float(total_pulls)) / float(pulls))
        scored.append({"arm": name, "score": ucb, "avg": avg, "sc": sc, "pulls": pulls})

    scored.sort(key=lambda r: r["score"], reverse=True)
    rng = random.Random(int(context.get("seed") or 17))
    if scored and rng.random() < float(epsilon):
        # explore: shuffle top half lightly
        top = scored[: max(3, len(scored) // 2)]
        rng.shuffle(top)
        scored = top + scored[len(top):]

    # Map to discovery knobs
    mech_weight = 1.0
    emp_weight = 1.0
    sym_weight = 1.0
    family_priority = []
    for row in scored[:12]:
        arm = row["arm"]
        if arm == "mechanism_scientist":
            mech_weight = 1.0 + max(-0.4, min(0.8, row["score"]))
        elif arm == "empirical_scientist":
            emp_weight = 1.0 + max(-0.4, min(0.8, row["score"]))
        elif arm == "symbolic_searcher":
            sym_weight = 1.0 + max(-0.4, min(0.8, row["score"]))
        elif arm.startswith("family:"):
            family_priority.append(arm.split(":", 1)[1])

    base_mech = int(context.get("base_max_mechanisms") or 14)
    base_ph = int(context.get("base_max_phenomena") or 24)
    base_probe = int(context.get("base_max_hyp_probe") or 28)

    # Dampen early UCB explosion on tiny VPS / few pulls
    def _damp(w):
        return max(0.75, min(1.25, float(w)))

    mech_weight = _damp(mech_weight)
    emp_weight = _damp(emp_weight)
    sym_weight = _damp(sym_weight)

    alloc = {
        "ok": True,
        "schema": "qiyu_budget_allocation_v1",
        "total_budget": int(total_budget),
        "epsilon": float(epsilon),
        "top_arms": scored[:8],
        "weights": {
            "mechanism_scientist": mech_weight,
            "empirical_scientist": emp_weight,
            "symbolic_searcher": sym_weight,
        },
        "family_priority": family_priority[:6],
        "knobs": {
            "max_mechanisms": max(6, min(16, int(round(base_mech * mech_weight)))),
            "max_phenomena": max(8, min(28, int(round(base_ph * emp_weight)))),
            "max_hypotheses_probe": max(8, min(32, int(round(base_probe * (
                0.4 * mech_weight + 0.3 * emp_weight + 0.3 * sym_weight
            ))))),
            "sym_pop_boost": max(0.8, min(1.25, sym_weight)),
        },
        "reward_definition_zh": (
            "信息增益代理+校准+复制+新颖 − 成本低估 − 过拟合风险；禁用裸PnL单分。"
        ),
        "at": _now(),
    }
    # persist last allocation
    state["last_allocation"] = {
        "knobs": alloc["knobs"],
        "family_priority": alloc["family_priority"],
        "at": _now(),
    }
    save_state(state)
    return alloc


def probe():
    return {
        "ok": True,
        "provider": "budget_allocator_bandit_lite_v1",
        "path": str(state_path()),
        "no_end_to_end_rl": True,
        "at": _now(),
    }
