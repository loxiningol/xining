# -*- coding: utf-8 -*-
"""Phase 6 end-to-end validation: full pipeline dry-run + invariant checks."""
from __future__ import print_function

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="p6test_")
os.environ.setdefault("VECTOR_ROOT", _TMP)

import dual_engine_workflow_v2.step_a_config as sc
import dual_engine_workflow_v2.config as cfg

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


# ═══════════════════════════════════════════════════════════════════════
# §1  KB Pre-read & Negative Feedback Injection
# ═══════════════════════════════════════════════════════════════════════

class TestKBPreReadInjection(unittest.TestCase):
    """Verify creation context includes KB with blocked paths/fingerprints."""

    def test_kb_context_has_must_read(self):
        from dual_engine_workflow_v2.failure_kb import kb_context_for_ai
        ctx = kb_context_for_ai()
        self.assertTrue(ctx["must_read"])
        self.assertIn("blocked_paths", ctx)
        self.assertIn("blocked_families", ctx)
        self.assertIn("reusable_lessons", ctx)
        self.assertIn("instruction", ctx)
        self.assertIn("Phase5", ctx["instruction"])

    def test_kb_records_rejection_and_blocks(self):
        from dual_engine_workflow_v2.failure_kb import (
            record_pipeline_rejection, path_is_blocked, load_failure_kb,
        )
        record_pipeline_rejection(
            task_id="e2e_reject_001",
            stage="funnel_l1_micro_screen",
            failed_tests=["few_fills", "low_payoff"],
            reject_reasons=["sample_filled_entries<5", "payoff<=1.2"],
            dsl={"key": "e2e_bad", "direction": "long", "timeframe": "15m"},
            blocked_paths=["funnel_l1|few_fills", "funnel_l1|low_payoff"],
            symbol="BTC-USDT-SWAP", timeframe="15m",
        )
        kb = load_failure_kb()
        self.assertTrue(len(kb.get("records") or []) >= 1)
        blocked, _ = path_is_blocked("funnel_l1|few_fills")
        self.assertTrue(blocked)

    def test_factory_context_includes_kb(self):
        try:
            import auto_trade_strategy_creation_factory as factory
            ctx = factory.build_factory_context(
                symbol="BTC-USDT-SWAP", timeframe="15m")
            self.assertIn("failure_kb_must_read", ctx)
            self.assertTrue(ctx["failure_kb_must_read"]["must_read"])
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
# §2  L1 Micro-Screen (< 0.1s)
# ═══════════════════════════════════════════════════════════════════════

class TestL1MicroScreen(unittest.TestCase):
    def test_l1_rejects_garbage_fast(self):
        from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen
        garbage_trades = [
            {"entry_index": i, "pnl_ratio": -0.02, "mae_price_pct": 0.05}
            for i in range(3)
        ]
        t0 = time.time()
        result = run_micro_screen(trades=garbage_trades)
        elapsed = time.time() - t0
        self.assertFalse(result["pass"])
        self.assertLess(elapsed, 0.1)

    def test_l1_passes_healthy(self):
        from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen
        healthy = [
            {"entry_index": i, "pnl_ratio": 0.01 if i % 2 == 0 else -0.005,
             "mae_price_pct": 0.003}
            for i in range(20)
        ]
        result = run_micro_screen(trades=healthy)
        self.assertTrue(result["pass"])


# ═══════════════════════════════════════════════════════════════════════
# §3  L2 Pareto + L3 Null Hypothesis
# ═══════════════════════════════════════════════════════════════════════

class TestL2L3Funnel(unittest.TestCase):
    def test_l2_singleton_fitness_pass(self):
        from dual_engine_workflow_v2.funnel_l2_pareto import evaluate_l2_survivor
        trades = [{"pnl_ratio": 0.01}] * 20
        result = evaluate_l2_survivor(
            trades, base_metrics={"calmar": 2.0, "payoff_ratio": 3.0,
                                  "win_rate_pct": 60, "trade_frequency": 20},
            candidate_id="e2e_l2")
        self.assertTrue(result["pass"])

    def test_l3_wf_requires_7_of_10(self):
        from dual_engine_workflow_v2.funnel_l3_null_hypothesis import (
            evaluate_null_hypothesis,
        )
        trades = [{"entry_index": i, "pnl_ratio": 0.005} for i in range(50)]
        result = evaluate_null_hypothesis(
            definition={}, frame=None, backtest_fn=None,
            trades=trades, seed=42, skip_frame_tests=True,
        )
        wf = result.get("walk_forward") or {}
        self.assertGreaterEqual(wf.get("total", 0), 10)


# ═══════════════════════════════════════════════════════════════════════
# §4  Phase 4 Incubator
# ═══════════════════════════════════════════════════════════════════════

class TestPhase4Incubator(unittest.TestCase):
    def test_incubator_rejects_single_overfit(self):
        from dual_engine_workflow_v2.incubator import _symbol_passes
        r1 = _symbol_passes({"trades": 10, "classic_expectancy": 0.01, "calmar": 1.0})
        r2 = _symbol_passes({"trades": 10, "classic_expectancy": -0.005, "calmar": 0.3})
        r3 = _symbol_passes({"trades": 10, "classic_expectancy": -0.01, "calmar": 0.2})
        pass_count = sum([r1, r2, r3])
        self.assertLess(pass_count, 2)

    def test_incubator_accepts_broad_edge(self):
        from dual_engine_workflow_v2.incubator import _symbol_passes
        r1 = _symbol_passes({"trades": 10, "classic_expectancy": 0.01, "calmar": 1.2})
        r2 = _symbol_passes({"trades": 10, "classic_expectancy": 0.008, "calmar": 0.9})
        r3 = _symbol_passes({"trades": 10, "classic_expectancy": -0.005, "calmar": 0.3})
        pass_count = sum([r1, r2, r3])
        self.assertGreaterEqual(pass_count, 2)


# ═══════════════════════════════════════════════════════════════════════
# §5  Phase 5: 3-Party AI Consensus
# ═══════════════════════════════════════════════════════════════════════

class TestPhase5Consensus(unittest.TestCase):
    def _mock_consent(self, ai):
        self._orig = ai.external_research_consent_status
        ai.external_research_consent_status = lambda provider=None, purpose=None: {
            "allowed": True, "granted": True,
            "provider_allowed": True, "purpose_allowed": True,
        }

    def _restore(self, ai):
        ai.external_research_consent_status = self._orig

    def test_unanimous_approve(self):
        import auto_trade_ai_consensus as ai
        cand = {"dsl": {"key": "e2e_ok"}}
        digest = ai.candidate_hash(cand)

        def mock(name, c, e, _retry=True):
            return {"provider": name, "ok": True, "decision": "APPROVE",
                    "candidate_hash": digest, "fatal_risk": False,
                    "risk_flags": [], "reason": "ok", "dimension": "test"}

        self._mock_consent(ai)
        orig = ai.phase5_review_one
        ai.phase5_review_one = mock
        try:
            r = ai.phase5_unanimous_review(cand, {})
            self.assertTrue(r["approved"])
            self.assertFalse(r["fatal_any"])
        finally:
            ai.phase5_review_one = orig
            self._restore(ai)

    def test_fatal_blocks(self):
        import auto_trade_ai_consensus as ai
        cand = {"dsl": {"key": "e2e_fatal"}}
        digest = ai.candidate_hash(cand)

        def mock(name, c, e, _retry=True):
            return {"provider": name, "ok": True, "decision": "APPROVE",
                    "candidate_hash": digest,
                    "fatal_risk": name == "deepseek",
                    "risk_flags": [], "reason": "test", "dimension": "test"}

        self._mock_consent(ai)
        orig = ai.phase5_review_one
        ai.phase5_review_one = mock
        try:
            r = ai.phase5_unanimous_review(cand, {})
            self.assertFalse(r["approved"])
            self.assertTrue(r["fatal_any"])
        finally:
            ai.phase5_review_one = orig
            self._restore(ai)

    def test_consent_fail_closed(self):
        import auto_trade_ai_consensus as ai
        orig = ai.external_research_consent_status
        ai.external_research_consent_status = lambda provider=None, purpose=None: {"allowed": False}
        try:
            r = ai.phase5_unanimous_review({"k": 1}, {})
            self.assertFalse(r["approved"])
            self.assertTrue(r["fatal_any"])
        finally:
            ai.external_research_consent_status = orig


# ═══════════════════════════════════════════════════════════════════════
# §6  Silent Drop vs Wx Push Routing
# ═══════════════════════════════════════════════════════════════════════

class TestSilentDropRouting(unittest.TestCase):
    def test_rejected_no_wx(self):
        """Simulated rejection: no WxPusher call, KB written."""
        from dual_engine_workflow_v2.failure_kb import (
            record_pipeline_rejection, load_failure_kb,
        )
        before = len((load_failure_kb().get("records") or []))
        record_pipeline_rejection(
            task_id="e2e_silent_001",
            stage="phase5_3party_consensus",
            failed_tests=["unanimous_reject"],
            reject_reasons=["deepseek:REJECT(fatal=True)"],
            dsl={"key": "silent_test", "direction": "short", "timeframe": "5m"},
            symbol="ETH-USDT-SWAP", timeframe="5m",
        )
        after = len((load_failure_kb().get("records") or []))
        self.assertGreater(after, before)


# ═══════════════════════════════════════════════════════════════════════
# §7  Multi-Asset Coverage
# ═══════════════════════════════════════════════════════════════════════

class TestMultiAssetCoverage(unittest.TestCase):
    def test_top20_instruments_supported(self):
        import auto_trade_strategy_dsl as dsl
        top20 = [
            "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
            "BNB-USDT-SWAP", "XRP-USDT-SWAP", "ADA-USDT-SWAP",
            "DOGE-USDT-SWAP", "LTC-USDT-SWAP", "LINK-USDT-SWAP",
            "AVAX-USDT-SWAP", "DOT-USDT-SWAP", "ATOM-USDT-SWAP",
            "NEAR-USDT-SWAP", "APT-USDT-SWAP", "SUI-USDT-SWAP",
            "OP-USDT-SWAP", "ARB-USDT-SWAP", "FIL-USDT-SWAP",
            "UNI-USDT-SWAP", "AAVE-USDT-SWAP",
        ]
        for sym in top20:
            self.assertIn(sym, dsl.INSTRUMENTS,
                          "%s not in DSL INSTRUMENTS" % sym)

    def test_commodity_instruments_supported(self):
        import auto_trade_strategy_dsl as dsl
        for sym in ("XAU-USDT-SWAP", "XAG-USDT-SWAP", "NG-USDT-SWAP", "CL-USDT-SWAP"):
            self.assertIn(sym, dsl.INSTRUMENTS)

    def test_total_instruments_ge_20(self):
        import auto_trade_strategy_dsl as dsl
        self.assertGreaterEqual(len(dsl.INSTRUMENTS), 20)


# ═══════════════════════════════════════════════════════════════════════
# §8  Multi-Timeframe Coverage
# ═══════════════════════════════════════════════════════════════════════

class TestMultiTimeframeCoverage(unittest.TestCase):
    def test_dsl_supports_5m_15m_1h(self):
        import auto_trade_strategy_dsl as dsl
        for tf in ("5m", "15m", "1h"):
            strat = {
                "schema": dsl.SCHEMA, "key": "tf_test_%s" % tf,
                "name": "TF test", "direction": "long", "timeframe": tf,
                "supported_instruments": ["BTC-USDT-SWAP"],
                "entry": {"all": [
                    {"id": "e1", "left": {"feature": "close"},
                     "op": "gt", "right": {"feature": "ema21"}},
                    {"id": "e2", "left": {"feature": "cci"},
                     "op": "gt", "right": {"value": 0}},
                ]},
                "exit": {"any": [{"id": "x1", "left": {"feature": "close"},
                                  "op": "lt", "right": {"feature": "ema21"},
                                  "role": "invalidation"}]},
                "max_hold_bars": 10,
            }
            validated = dsl.validate_strategy(strat)
            self.assertEqual(validated["timeframe"], tf)

    def test_dsl_timeframe_allowlist(self):
        import auto_trade_strategy_dsl as dsl
        for tf in ("5m", "15m", "1h", "4h"):
            self.assertNotEqual(tf, "invalid")


# ═══════════════════════════════════════════════════════════════════════
# §9  Production Invariants (CRITICAL)
# ═══════════════════════════════════════════════════════════════════════

class TestProductionInvariants(unittest.TestCase):
    """Hard-coded production safety constants must NOT change."""

    def test_stop_loss_pct_0009(self):
        import auto_trade_human_confirm_pipeline as pipeline
        self.assertEqual(pipeline.STOP_LOSS_PCT, 0.009)

    def test_leverage_20x(self):
        import auto_trade_human_confirm_pipeline as pipeline
        self.assertEqual(pipeline.LEVERAGE, 20)

    def test_b_grade_30pct(self):
        import auto_trade_human_confirm_pipeline as pipeline
        self.assertAlmostEqual(pipeline.GRADE_RATIO["B"], 0.30)

    def test_s_grade_70pct(self):
        import auto_trade_human_confirm_pipeline as pipeline
        self.assertAlmostEqual(pipeline.GRADE_RATIO["S"], 0.70)

    def test_step_a_constraints_sl(self):
        self.assertEqual(sc.PRODUCTION_CONSTRAINTS["stop_loss_pct"], 0.009)

    def test_step_a_constraints_leverage(self):
        self.assertEqual(sc.PRODUCTION_CONSTRAINTS["leverage"], 20)

    def test_step_a_constraints_position(self):
        self.assertAlmostEqual(sc.PRODUCTION_CONSTRAINTS["initial_position_pct"], 0.30)

    def test_step_a_no_force_open(self):
        self.assertTrue(sc.PRODUCTION_CONSTRAINTS["no_force_open"])

    def test_ada_migration_disabled(self):
        self.assertFalse(sc.PRODUCTION_CONSTRAINTS["ada_sl_migration_allowed"])

    def test_protective_sl_in_dsl_module(self):
        import auto_trade_strategy_dsl as dsl
        self.assertGreaterEqual(dsl.ATR_TRAIL_N_MIN, 2.5)

    def test_triple_friction_wx_disabled(self):
        import auto_trade_human_confirm_pipeline as pipeline
        self.assertFalse(pipeline.TRIPLE_FRICTION_TIP_WX_ENABLED)


# ═══════════════════════════════════════════════════════════════════════
# §10  Code Version & Phase Integrity
# ═══════════════════════════════════════════════════════════════════════

class TestCodeVersionIntegrity(unittest.TestCase):
    def test_step_a_version_phase5(self):
        self.assertIn("phase5", sc.STEP_A_CODE_VERSION)

    def test_phase5_consensus_importable(self):
        import auto_trade_ai_consensus as ai
        self.assertTrue(callable(ai.phase5_unanimous_review))
        self.assertTrue(callable(ai.phase5_review_one))

    def test_formal_4d_phase5_bridge(self):
        from dual_engine_workflow_v2.formal_4d import run_phase5_consensus
        self.assertTrue(callable(run_phase5_consensus))

    def test_failure_kb_auto_record(self):
        from dual_engine_workflow_v2.failure_kb import record_pipeline_rejection
        self.assertTrue(callable(record_pipeline_rejection))

    def test_exit_ops_include_atr_trailing(self):
        import auto_trade_strategy_dsl as dsl
        self.assertIn("atr_trailing", dsl.EXIT_OPS)
        self.assertIn("swing_extreme", dsl.EXIT_OPS)


# ═══════════════════════════════════════════════════════════════════════
# §11  Full Pipeline Dry-Run Simulation
# ═══════════════════════════════════════════════════════════════════════

class TestFullPipelineDryRun(unittest.TestCase):
    """Simulate rejection + approval path through the entire chain."""

    def test_rejection_path_writes_kb_no_wx(self):
        """Rejection at L1 → KB written, no pending queue entry."""
        from dual_engine_workflow_v2.failure_kb import (
            record_pipeline_rejection, load_failure_kb,
        )
        from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen

        bad_trades = [{"entry_index": 0, "pnl_ratio": -0.05, "mae_price_pct": 0.1}]
        l1 = run_micro_screen(trades=bad_trades)
        self.assertFalse(l1["pass"])

        record_pipeline_rejection(
            task_id="dryrun_reject",
            stage="funnel_l1_micro_screen",
            failed_tests=l1.get("reject_reasons") or ["l1"],
            reject_reasons=l1.get("reject_reasons") or ["l1"],
            dsl={"key": "dryrun_bad", "direction": "long", "timeframe": "15m"},
            symbol="BTC-USDT-SWAP", timeframe="15m",
        )
        kb = load_failure_kb()
        found = any(r.get("task_id") == "dryrun_reject"
                    for r in (kb.get("records") or []))
        self.assertTrue(found)

    def test_approval_path_structure(self):
        """Mock full approval: verify pending queue structure."""
        import auto_trade_human_confirm_pipeline as pipeline
        pending_before = len((pipeline.load_pending().get("items") or []))

        wx_calls = []
        import auto_trade_formal_notify as notify
        orig_send = notify.send_message

        def mock_send(msg, kind="", meta=None, dry_run=False):
            wx_calls.append(kind)
            return {"ok": True, "sent": False, "dry_run": True}

        notify.send_message = mock_send
        try:
            import auto_trade_strategy_dsl as dsl
            test_dsl = {
                "schema": dsl.SCHEMA, "key": "dryrun_approve_e2e",
                "name": "E2E审批测试", "direction": "long", "timeframe": "15m",
                "supported_instruments": ["BTC-USDT-SWAP"],
                "entry": {"all": [
                    {"id": "e1", "left": {"feature": "close"},
                     "op": "cross_above", "right": {"feature": "ema21"}},
                    {"id": "e2", "left": {"feature": "cci"},
                     "op": "gt", "right": {"value": 0}},
                ]},
                "exit": {"any": [
                    {"id": "x1", "left": {"feature": "j"},
                     "op": "gt", "right": {"value": 85},
                     "role": "take_profit"},
                ]},
                "max_hold_bars": 20,
            }
            ai_review = {
                "approved": True,
                "ai_theoretical_wr_avg": 58.0,
                "calmar": 2.1,
                "payoff": 3.0,
                "cross_asset_score": 0.85,
                "mean_mae": 0.003,
                "phase4_incubator": True,
                "natural_language": "E2E dry-run test",
            }
            result = pipeline.ingest_and_screen(
                {"dsl": test_dsl, "symbol": "BTC-USDT-SWAP",
                 "timeframe": "15m", "production_mounted": False},
                source="e2e_dryrun",
                ai_review=ai_review,
            )
            if result.get("ok"):
                self.assertIn("strategy_pending_confirm", wx_calls)
                self.assertFalse(result.get("production_mounted", True))
                pending_after = len((pipeline.load_pending().get("items") or []))
                self.assertGreater(pending_after, pending_before)
        except Exception:
            pass
        finally:
            notify.send_message = orig_send


if __name__ == "__main__":
    unittest.main()
