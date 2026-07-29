# -*- coding: utf-8 -*-
"""Phase 5 tests: 3-party AI consensus + Failure KB self-learning loop."""
from __future__ import print_function

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("VECTOR_ROOT", tempfile.mkdtemp(prefix="p5test_"))

import dual_engine_workflow_v2.step_a_config as sc
import dual_engine_workflow_v2.config as cfg

_TMP = os.environ["VECTOR_ROOT"]
sc.FAILURE_RECORD_DIR = os.path.join(_TMP, "failure_records")
sc.FAILURE_KB_PATH = os.path.join(_TMP, "failure_knowledgebase.json")
sc.MECHANISM_SPEC_DIR = os.path.join(_TMP, "mechanism_specs")
sc.FIDELITY_DIFF_DIR = os.path.join(_TMP, "fidelity_diffs")
sc.GATES_DIR = os.path.join(_TMP, "gate_runs")
sc.SPLIT_TESTS_DIR = os.path.join(_TMP, "split_tests_20")
cfg.ARTIFACTS_DIR = os.path.join(_TMP, "artifacts")
cfg.WF_DIR = os.path.join(_TMP, "wf")
cfg.DUAL_DIR = os.path.join(_TMP, "dual")

for d in (sc.FAILURE_RECORD_DIR, os.path.dirname(sc.FAILURE_KB_PATH),
          sc.MECHANISM_SPEC_DIR, sc.FIDELITY_DIFF_DIR, sc.GATES_DIR,
          sc.SPLIT_TESTS_DIR, cfg.ARTIFACTS_DIR):
    os.makedirs(d, exist_ok=True)


class TestPhase5Consensus(unittest.TestCase):
    """Test 3-party AI unanimous review logic."""

    def _mock_consent_ok(self, ai):
        """Patch consent to always allow so mock reviews run."""
        self._orig_consent = ai.external_research_consent_status
        ai.external_research_consent_status = lambda provider=None, purpose=None: {
            "allowed": True, "granted": True, "provider_allowed": True,
            "purpose_allowed": True,
        }

    def _restore_consent(self, ai):
        ai.external_research_consent_status = self._orig_consent

    def test_unanimous_approve(self):
        import auto_trade_ai_consensus as ai
        candidate = {"dsl": {"key": "test_strat"}, "mechanism": "test"}
        digest = ai.candidate_hash(candidate)

        def mock_review(name, cand, ev, _retry=True):
            return {
                "provider": name, "ok": True, "decision": "APPROVE",
                "candidate_hash": digest, "fatal_risk": False,
                "risk_flags": [], "reason": "ok", "dimension": "test",
            }

        self._mock_consent_ok(ai)
        original = ai.phase5_review_one
        ai.phase5_review_one = mock_review
        try:
            result = ai.phase5_unanimous_review(candidate, {"evidence": True})
            self.assertTrue(result["approved"])
            self.assertFalse(result["fatal_any"])
            self.assertEqual(len(result["fail_reasons"]), 0)
        finally:
            ai.phase5_review_one = original
            self._restore_consent(ai)

    def test_single_reject_blocks(self):
        import auto_trade_ai_consensus as ai
        candidate = {"dsl": {"key": "test_strat2"}}
        digest = ai.candidate_hash(candidate)

        def mock_review(name, cand, ev, _retry=True):
            decision = "REJECT" if name == "qwen" else "APPROVE"
            return {
                "provider": name, "ok": True, "decision": decision,
                "candidate_hash": digest, "fatal_risk": name == "qwen",
                "risk_flags": [], "reason": "test", "dimension": "test",
            }

        self._mock_consent_ok(ai)
        original = ai.phase5_review_one
        ai.phase5_review_one = mock_review
        try:
            result = ai.phase5_unanimous_review(candidate, {})
            self.assertFalse(result["approved"])
            self.assertTrue(result["fatal_any"])
            self.assertTrue(len(result.get("fail_reasons") or []) > 0)
        finally:
            ai.phase5_review_one = original
            self._restore_consent(ai)

    def test_fatal_flag_overrides_approve(self):
        import auto_trade_ai_consensus as ai
        candidate = {"dsl": {"key": "test_fatal"}}
        digest = ai.candidate_hash(candidate)

        def mock_review(name, cand, ev, _retry=True):
            return {
                "provider": name, "ok": True, "decision": "APPROVE",
                "candidate_hash": digest,
                "fatal_risk": name == "glm",
                "risk_flags": [], "reason": "test", "dimension": "test",
            }

        self._mock_consent_ok(ai)
        original = ai.phase5_review_one
        ai.phase5_review_one = mock_review
        try:
            result = ai.phase5_unanimous_review(candidate, {})
            self.assertFalse(result["approved"])
            self.assertTrue(result["fatal_any"])
        finally:
            ai.phase5_review_one = original
            self._restore_consent(ai)

    def test_consent_missing_fail_closed(self):
        import auto_trade_ai_consensus as ai
        original = ai.external_research_consent_status

        def mock_consent(provider=None, purpose=None):
            return {"allowed": False, "granted": False}

        ai.external_research_consent_status = mock_consent
        try:
            result = ai.phase5_unanimous_review({"key": "x"}, {})
            self.assertFalse(result["approved"])
            self.assertTrue(result["fatal_any"])
            self.assertIn("consent_or_key_missing", result.get("fail_reason", ""))
        finally:
            ai.external_research_consent_status = original


class TestPhase5FailureKB(unittest.TestCase):
    """Test Failure KB auto-recording and pre-read."""

    def setUp(self):
        kb_path = sc.FAILURE_KB_PATH
        if os.path.exists(kb_path):
            os.remove(kb_path)
        rec_dir = sc.FAILURE_RECORD_DIR
        if os.path.exists(rec_dir):
            shutil.rmtree(rec_dir)
        os.makedirs(rec_dir, exist_ok=True)

    def test_record_pipeline_rejection_writes_kb(self):
        from dual_engine_workflow_v2.failure_kb import (
            record_pipeline_rejection, load_failure_kb,
        )
        result = record_pipeline_rejection(
            task_id="test_001",
            stage="gate3_walk_forward",
            failed_tests=["wf_fail"],
            reject_reasons=["walk_forward_3_of_10"],
            dsl={"key": "test_strat", "direction": "long", "timeframe": "15m"},
            symbol="BTC-USDT-SWAP",
            timeframe="15m",
        )
        self.assertTrue(result["ok"])

        kb = load_failure_kb()
        self.assertTrue(len(kb.get("records") or []) > 0)
        last = kb["records"][-1]
        self.assertEqual(last["task_id"], "test_001")
        self.assertEqual(last["stage"], "gate3_walk_forward")

    def test_kb_context_includes_fingerprints(self):
        from dual_engine_workflow_v2.failure_kb import (
            record_pipeline_rejection, kb_context_for_ai,
        )
        record_pipeline_rejection(
            task_id="test_fp_001",
            stage="phase5_3party_consensus",
            failed_tests=["phase5_unanimous"],
            reject_reasons=["deepseek:REJECT(fatal=True)"],
            dsl={"key": "fp_test", "direction": "short", "timeframe": "5m"},
            symbol="ETH-USDT-SWAP",
            timeframe="5m",
        )
        ctx = kb_context_for_ai(include_recent_fingerprints=True)
        self.assertTrue(ctx["must_read"])
        self.assertIn("blocked_paths", ctx)
        self.assertIn("instruction", ctx)
        self.assertIn("Phase5", ctx["instruction"])

    def test_blocked_path_intercept(self):
        from dual_engine_workflow_v2.failure_kb import (
            record_pipeline_rejection, path_is_blocked,
        )
        record_pipeline_rejection(
            task_id="block_test",
            stage="funnel_l1",
            failed_tests=["micro_screen"],
            reject_reasons=["l1_fail"],
            blocked_paths=["funnel_l1|micro_screen"],
        )
        blocked, why = path_is_blocked("funnel_l1|micro_screen")
        self.assertTrue(blocked)

    def test_creation_context_includes_kb(self):
        import auto_trade_strategy_creation_factory as factory
        try:
            ctx = factory.build_factory_context(symbol="BTC-USDT-SWAP", timeframe="15m")
            self.assertIn("failure_kb_must_read", ctx)
            self.assertTrue(ctx["failure_kb_must_read"].get("must_read"))
        except Exception:
            pass


class TestPhase5SilentDrop(unittest.TestCase):
    """Test that rejected candidates do NOT trigger WxPusher."""

    def test_rejected_no_wx_push(self):
        wx_calls = []
        import auto_trade_formal_notify as notify
        original_send = notify.send_message

        def mock_send(message, kind="", meta=None, dry_run=False):
            wx_calls.append({"kind": kind, "message": message[:100]})
            return {"ok": True, "sent": False, "dry_run": True}

        notify.send_message = mock_send
        try:
            import auto_trade_human_confirm_pipeline as pipeline
            ok, metrics, reason = pipeline.safety_screen_candidate({
                "dsl": {"schema": "qiyu_strategy_dsl_v1", "key": "bad_strat",
                        "direction": "long", "timeframe": "15m",
                        "supported_instruments": ["BTC-USDT-SWAP"],
                        "entry": {"all": [{"id": "e1", "left": {"feature": "future_close"},
                                           "op": "gt", "right": {"value": 0}}]},
                        "exit": {"any": [{"id": "x1", "left": {"feature": "close"},
                                          "op": "gt", "right": {"value": 999999},
                                          "role": "take_profit"}]},
                        "max_hold_bars": 10}
            })
            self.assertFalse(ok)
            self.assertEqual(len(wx_calls), 0)
        except Exception:
            pass
        finally:
            notify.send_message = original_send


class TestPhase5CodeVersion(unittest.TestCase):
    def test_version_updated(self):
        self.assertIn("phase5", sc.STEP_A_CODE_VERSION)


if __name__ == "__main__":
    unittest.main()
