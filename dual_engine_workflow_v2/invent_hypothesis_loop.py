# -*- coding: utf-8 -*-
"""Hypothesis invent loop helpers: parse → lineage → ledger → critique enrich."""
from __future__ import print_function

import json
import os
import time
from pathlib import Path

from dual_engine_workflow_v2.invent_element_ops import (
    ADD_INTENTS,
    infer_intent,
    next_intents_for,
    recipe_structure_fp,
)
from dual_engine_workflow_v2.invent_step_reward import score_step
from dual_engine_workflow_v2.timing_stage import build_critique, critique_user_message

INTENTS = tuple(sorted(ADD_INTENTS | frozenset(("noop", "refine_timing"))))


def _ledger_path(ns=""):
    ns = str(ns or "").strip().strip("_") or "a"
    override = str(os.environ.get("KDH_INVENT_STEP_LEDGER") or "").strip()
    if override:
        return Path(override)
    return Path(
        "/root/auto_trade/dual_engine/sole_creation_runs/invent_step_ledger_%s.jsonl"
        % ns
    )


def parse_hypothesis(obj, extract_recipe_fn=None):
    """Accept {hypothesis:{...}} or legacy {recipe:{...}} / bare recipe."""
    intent = None
    rationale = []
    recipe = None
    if not isinstance(obj, dict):
        return {
            "ok": False, "error": "not_object",
            "intent": None, "recipe": None, "rationale_keys": [],
        }
    hyp = obj.get("hypothesis")
    if isinstance(hyp, dict):
        intent = str(hyp.get("intent") or "").strip() or None
        rationale = list(hyp.get("rationale_keys") or [])[:8]
        recipe = hyp.get("recipe")
        if recipe is None and extract_recipe_fn:
            recipe = extract_recipe_fn(hyp)
    if recipe is None and extract_recipe_fn:
        recipe = extract_recipe_fn(obj)
    elif recipe is None and isinstance(obj.get("recipe"), dict):
        recipe = obj.get("recipe")
    if not isinstance(recipe, dict):
        return {
            "ok": False, "error": "missing_recipe",
            "intent": intent, "recipe": None, "rationale_keys": rationale,
        }
    return {
        "ok": True,
        "intent": intent,
        "recipe": recipe,
        "rationale_keys": rationale,
        "legacy": not isinstance(hyp, dict),
    }


def make_step_id(lane, rnd, ident):
    return "%s:%s:%s" % (lane, rnd, (ident or "anon")[:80])


def append_ledger(row, ns=""):
    path = _ledger_path(ns)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return str(path)
    except Exception as exc:
        return "ledger_fail:%s" % str(exc)[:80]


def record_step(lane, rnd, ns, parent_id, step_id, parent_recipe, child_recipe,
                parent_eval, child_eval, declared_intent=None,
                rationale_keys=None):
    """Score + persist one invent step; return score dict + lineage fields."""
    intent = infer_intent(parent_recipe, child_recipe, declared_intent)
    scored = score_step(
        parent_eval, child_eval,
        parent_recipe=parent_recipe,
        child_recipe=child_recipe,
        intent=intent,
    )
    next_intents = next_intents_for(
        scored.get("stage"), scored.get("diff"), scored.get("parts"),
    )
    row = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ts": time.time(),
        "lane": lane,
        "round": rnd,
        "ns": ns or "a",
        "parent_id": parent_id or "",
        "step_id": step_id,
        "intent": intent,
        "declared_intent": declared_intent,
        "rationale_keys": list(rationale_keys or [])[:8],
        "structure_fp": recipe_structure_fp(child_recipe),
        "parent_fp": recipe_structure_fp(parent_recipe) if parent_recipe else "",
        "reward_total": scored.get("total"),
        "reward_parts": scored.get("parts"),
        "reward_tags": scored.get("tags"),
        "delta": scored.get("delta"),
        "stage": scored.get("stage"),
        "parent_stage": scored.get("parent_stage"),
        "diff_categories": (scored.get("diff") or {}).get("categories"),
        "elements_added": (scored.get("diff") or {}).get("elements_added"),
        "C_week_pct": (child_eval or {}).get("C_week_pct"),
        "C_week_oos_pct": (child_eval or {}).get("C_week_oos_pct"),
        "n": (child_eval or {}).get("n"),
        "hit_floor": bool((child_eval or {}).get("hit_floor")),
        "next_intents": next_intents,
    }
    path = append_ledger(row, ns=ns)
    scored["next_intents"] = next_intents
    scored["ledger_path"] = path
    scored["step_id"] = step_id
    scored["parent_id"] = parent_id or ""
    scored["intent"] = intent
    return scored


def enrich_critique(critique, scored, free_create=True):
    """Attach step reward / delta / next intents onto machine critique."""
    critique = dict(critique or {})
    scored = scored or {}
    critique["schema"] = "hypothesis_refine_v1"
    critique["step_reward"] = {
        "total": scored.get("total"),
        "parts": scored.get("parts"),
        "tags": scored.get("tags"),
    }
    critique["delta_C"] = (scored.get("delta") or {})
    critique["intent"] = scored.get("intent")
    critique["next_intents"] = list(scored.get("next_intents") or [])
    critique["elements_added"] = (scored.get("diff") or {}).get("elements_added")
    critique["parent_id"] = scored.get("parent_id")
    critique["step_id"] = scored.get("step_id")
    if free_create:
        acts = list(critique.get("actions_allowed") or [])
        for intent in ("add_location", "add_relation", "add_timing",
                       "adjust_geometry", "replace_spine"):
            if intent not in acts:
                acts.append(intent)
        critique["actions_allowed"] = acts
        hint = str(critique.get("hint") or "")
        extra = (
            "下一步优先 intent=%s；须相对父配方有结构 diff；"
            "鼓励加 location/relation（价格相对MA/EMA），禁止只拧 timing 空转。"
            % ",".join(critique["next_intents"][:4])
        )
        critique["hint"] = (hint + " " + extra).strip()
    return critique


def hypothesis_user_message(critique):
    """Critique prompt asking for hypothesis package (JSON only)."""
    body = json.dumps(critique, ensure_ascii=False, default=str)
    if len(body) > 1600:
        body = body[:1600]
    head = (
        "机器批评如下。禁止文字。只输出 "
        "{\"hypothesis\":{\"intent\":\"add_location|add_relation|add_timing|"
        "adjust_geometry|replace_spine\",\"rationale_keys\":[...],"
        "\"recipe\":{...}}}。"
        "须相对父配方叠加或调整要素（location/relation 优先）；"
        "禁止无结构 diff 的复读壳子。合同保留 route/exec_tf/filter_tfs。\n"
    )
    return head + body


def critique_after_eval(recipe, pair, ident, adjusts, diagnosis=None,
                        explore_state=None, scored=None, free_create=True):
    """Build enriched critique + user message for next invent turn."""
    base = build_critique(
        recipe, pair, ident, adjusts,
        diagnosis=diagnosis, explore_state=explore_state,
    )
    if scored:
        base = enrich_critique(base, scored, free_create=free_create)
        return base, hypothesis_user_message(base)
    # first step / no parent: still ask hypothesis shape when free
    if free_create:
        base["schema"] = "hypothesis_refine_v1"
        base["next_intents"] = list(next_intents_for(
            base.get("stage"), None, None,
        ))
        return base, hypothesis_user_message(base)
    return base, critique_user_message(base)
