# -*- coding: utf-8 -*-
"""Unit tests for invent hypothesis / step reward / strict bind."""
from __future__ import print_function

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestElementOps(unittest.TestCase):
    def test_diff_location_and_timing(self):
        from dual_engine_workflow_v2.invent_element_ops import (
            diff_elements, infer_intent,
        )
        parent = {
            "family": "xu_long", "symbol": "NVDA-USDT-SWAP", "route": "ema_osc",
            "held": 28, "xwin": 8.0, "atr": 2.0,
            "timing": [
                {"factor": "skdj_diff", "operator": "above", "value": -20, "window": 9},
            ],
        }
        child = dict(parent)
        child["family"] = "ma_long"
        child["ma_kind"] = "sma"
        child["timing"] = [
            {"factor": "skdj_k", "operator": "cross_up", "value": 20, "window": 9},
            {"factor": "macd_hist", "operator": "above", "value": -1},
        ]
        d = diff_elements(parent, child)
        self.assertTrue(d["has_diff"])
        self.assertTrue(d["location_changed"] or d["family_changed"])
        self.assertTrue(d["timing_structure_changed"])
        self.assertEqual(infer_intent(parent, child, "add_location"), "add_location")
        self.assertIn(infer_intent(parent, child), (
            "replace_spine", "add_location", "add_timing",
        ))


class TestStepReward(unittest.TestCase):
    def test_dual_c_and_noop(self):
        from dual_engine_workflow_v2.invent_step_reward import score_step
        parent_r = {
            "family": "xu_long", "symbol": "A", "route": "ema_osc",
            "held": 28, "xwin": 8,
            "timing": [{"factor": "skdj_diff", "operator": "above", "value": 0}],
        }
        child_r = dict(parent_r)
        child_r["family"] = "ma_long"
        child_r["ma_kind"] = "sma"
        child_r["timing"] = [
            {"factor": "skdj_k", "operator": "cross_up", "value": 20},
            {"factor": "volume_ratio", "operator": "above", "value": 1.2},
        ]
        parent_e = {
            "n": 40, "weekly": 0.6, "hitch": 0.2, "E": 0.004,
            "C_week_pct": -0.5, "C_week_oos_pct": -0.4,
            "usable": False, "oos_usable": False,
        }
        child_e = dict(parent_e)
        child_e["C_week_pct"] = -0.1
        child_e["C_week_oos_pct"] = -0.05
        scored = score_step(
            parent_e, child_e, parent_recipe=parent_r, child_recipe=child_r,
            intent="add_location",
        )
        self.assertIn("dual_c_sync_up", scored["tags"])
        self.assertIn("element_effective", scored["tags"])
        self.assertGreater(scored["total"], 0)

        clone = score_step(
            parent_e, parent_e, parent_recipe=parent_r, child_recipe=parent_r,
            intent="noop",
        )
        self.assertIn("noop_or_clone", clone["tags"])
        self.assertLess(clone["total"], 0)

    def test_hit_floor_bonus_does_not_change_threshold(self):
        from dual_engine_workflow_v2.invent_step_reward import score_step
        child_e = {
            "n": 50, "weekly": 0.8, "hitch": 0.1, "E": 0.01,
            "C_week_pct": 0.2, "C_week_oos_pct": 0.1,
            "usable": True, "oos_usable": True, "hit_floor": True,
        }
        scored = score_step(None, child_e, parent_recipe=None, child_recipe={"family": "xu_long"})
        self.assertIn("hit_floor_bonus", scored["tags"])


class TestHypothesisParse(unittest.TestCase):
    def test_parse_hypothesis_and_legacy(self):
        from dual_engine_workflow_v2.invent_hypothesis_loop import (
            parse_hypothesis, enrich_critique, hypothesis_user_message,
        )

        def extract(obj):
            if isinstance(obj, dict) and isinstance(obj.get("recipe"), dict):
                return obj["recipe"]
            return None

        hyp = parse_hypothesis({
            "hypothesis": {
                "intent": "add_relation",
                "rationale_keys": ["ema_ma"],
                "recipe": {
                    "family": "ma_long", "symbol": "X", "timing": [{"a": 1}],
                },
            }
        }, extract_recipe_fn=extract)
        self.assertTrue(hyp["ok"])
        self.assertEqual(hyp["intent"], "add_relation")

        legacy = parse_hypothesis({
            "recipe": {"family": "xu_long", "symbol": "Y", "timing": [{"a": 1}]},
        }, extract_recipe_fn=extract)
        self.assertTrue(legacy["ok"])
        self.assertTrue(legacy["legacy"])

        crit = enrich_critique({"stage": "S1_n", "hint": "x"}, {
            "total": 2, "parts": {"dual_c_sync_up": 2}, "tags": ["dual_c_sync_up"],
            "delta": {"C_week_pct": 0.1, "C_week_oos_pct": 0.05},
            "next_intents": ["add_location"],
            "diff": {"elements_added": ["family"]},
            "intent": "add_location",
        })
        self.assertEqual(crit["schema"], "hypothesis_refine_v1")
        msg = hypothesis_user_message(crit)
        self.assertIn("hypothesis", msg)


class TestStrictBind(unittest.TestCase):
    def test_strict_bind_skips_failover(self):
        from dual_engine_workflow_v2 import kimi_provider as kp

        os.environ["KDH_INVENT_STRICT_BIND"] = "1"
        self.assertTrue(kp.invent_strict_bind())

        calls = []

        def fake_one(endpoint, body, timeout, retries):
            calls.append(endpoint.get("name"))
            return {
                "ok": False,
                "error": "HTTPError:HTTP Error 429: Too Many Requests",
                "endpoint": endpoint.get("name"),
                "used": ["%s:429" % endpoint.get("name")],
                "http_code": 429,
            }

        old_chain = kp.invent_endpoint_chain
        old_post = kp._kimi_post_one
        try:
            kp.invent_endpoint_chain = lambda: [
                {"name": "primary", "url": "http://p", "key": "k", "model": "m"},
                {"name": "backup", "url": "http://b", "key": "k", "model": "m"},
            ]
            kp._kimi_post_one = fake_one
            posted = kp.kimi_post_named("primary", {"messages": []}, timeout=5)
            self.assertFalse(posted.get("ok"))
            self.assertFalse(posted.get("failover"))
            self.assertTrue(posted.get("strict_bind"))
            self.assertEqual(calls, ["primary"])
            used = " ".join(str(x) for x in (posted.get("used") or []))
            self.assertNotIn("backup", used)
        finally:
            kp.invent_endpoint_chain = old_chain
            kp._kimi_post_one = old_post
            os.environ.pop("KDH_INVENT_STRICT_BIND", None)

    def test_strict_bind_off_allows_failover(self):
        from dual_engine_workflow_v2 import kimi_provider as kp

        os.environ["KDH_INVENT_STRICT_BIND"] = "0"
        self.assertFalse(kp.invent_strict_bind())
        calls = []

        def fake_one(endpoint, body, timeout, retries):
            calls.append(endpoint.get("name"))
            if endpoint.get("name") == "backup":
                return {
                    "ok": True, "raw": {"choices": [{"message": {"content": "{}"}}]},
                    "endpoint": "backup", "used": ["backup"], "http_code": 200,
                }
            return {
                "ok": False,
                "error": "HTTPError:HTTP Error 429: Too Many Requests",
                "endpoint": "primary",
                "used": ["primary:429"],
                "http_code": 429,
            }

        old_chain = kp.invent_endpoint_chain
        old_post = kp._kimi_post_one
        try:
            kp.invent_endpoint_chain = lambda: [
                {"name": "primary", "url": "http://p", "key": "k", "model": "m"},
                {"name": "backup", "url": "http://b", "key": "k", "model": "m"},
            ]
            kp._kimi_post_one = fake_one
            posted = kp.kimi_post_named("primary", {"messages": []}, timeout=5)
            self.assertTrue(posted.get("ok"))
            self.assertTrue(posted.get("failover"))
            self.assertEqual(posted.get("endpoint"), "backup")
            self.assertEqual(calls, ["primary", "backup"])
        finally:
            kp.invent_endpoint_chain = old_chain
            kp._kimi_post_one = old_post
            os.environ.pop("KDH_INVENT_STRICT_BIND", None)


if __name__ == "__main__":
    unittest.main()
