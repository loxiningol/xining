# -*- coding: utf-8 -*-
from __future__ import print_function

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dual_engine_workflow_v2 import creation_sole_entry as sole
from dual_engine_workflow_v2 import creation_blueprint
from dual_engine_workflow_v2 import parallel_creation as queue
from dual_engine_workflow_v2 import creation_sole_entry
from dual_engine_workflow_v2 import formal_review_bridge
from dual_engine_workflow_v2.process_safe_state import atomic_write_json


MODULES = ["parameter_platform", "learning_loop", "prediction_contract"]
ROLES = ["leakage_auditor", "causal_auditor", "execution_engineer"]


def valid_blueprint(ok=False, present=False):
    blueprint = {
        "ok": bool(ok),
        "present_to_human": bool(present),
        "schema": "qiyu_creation_blueprint_v1",
        "run_id": "run_test",
        "research_contract": {
            "schema": "qiyu_research_contract_v2",
            "valid": True,
            "contract_id": "rc_test",
        },
        "stages": {
            "research_discovery": {
                "ok": bool(ok),
                "run_id": "run_test",
                "n_survivors": 1 if ok else 0,
                "population": {
                    "committee": {
                        "mechanism_scientist": {},
                        "empirical_scientist": {},
                        "symbolic_searcher": {},
                    },
                    "population_first": True,
                    "early_pick_one": False,
                },
                "trial_budget": {"effective_trials": 7},
                "contract": {
                    "schema": "qiyu_research_contract_v2",
                    "valid": True,
                    "contract_id": "rc_test",
                },
                "multiple_testing": {"passed": bool(ok)},
            },
            "admission_envelope": {
                "research_contract_id": "rc_test",
                "recipe_ids": ["recipe_test"] if ok else [],
            },
            "assembly_lineage": {
                "selected_recipe_id": "recipe_test" if ok else None,
                "selected_hypothesis_id": "hyp_test" if ok else None,
                "selected_is_in_admission_envelope": bool(ok),
                "global_factor_mining_used": False,
                "classic_or_unadmitted_switch_used": False,
            },
        },
        "probes": {
            "discovery": {"ok": True, "modules": MODULES, "roles": ROLES},
        },
    }
    if ok:
        blueprint["best_factor"] = {
            "recipe_id": "recipe_test",
            "hypothesis_id": "hyp_test",
            "contract_id": "rc_test",
        }
    return blueprint


class TempRootCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qiyu_status_queue_test_")
        self.env = mock.patch.dict(os.environ, {"VECTOR_ROOT": self.temp.name}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()


class TestFailClosedStageGate(unittest.TestCase):
    def test_missing_fields_never_default_true(self):
        gate = sole.verify_blueprint_stages({
            "stages": {"research_discovery": {"population": {}}},
            "probes": {"discovery": {}},
        })
        self.assertFalse(gate["passed"])
        failed = {row["check"] for row in gate["failed"]}
        self.assertIn("research_discovery_completed", failed)
        self.assertIn("population_first", failed)
        self.assertIn("no_early_pick_one", failed)
        self.assertIn("discovery_probe", failed)
        self.assertIn("parameter_platform_module", failed)
        self.assertIn("learning_loop_module", failed)

    def test_complete_evidence_passes_even_when_research_rejects(self):
        blueprint = valid_blueprint(ok=False, present=False)
        gate = sole.verify_blueprint_stages(blueprint)
        self.assertTrue(gate["passed"])


class TestCreationOutcome(TempRootCase):
    def test_rejected_research_is_not_ok_but_is_technically_complete(self):
        captured = {}

        def fake_blueprint(**kwargs):
            captured.update(kwargs)
            return valid_blueprint(ok=False, present=False)

        research_contract = {"symbol": "LTC-USDT-SWAP", "required": ["event_a"]}
        mutation_contract = {"parent_id": "p1", "failed_gate": "dsr"}
        with mock.patch.object(
            creation_blueprint,
            "run_creation_blueprint", side_effect=fake_blueprint,
        ):
            result = sole._create_strategy_unlocked(
                symbol="LTC-USDT-SWAP",
                mission_id="outcome_reject",
                research_contract=research_contract,
                mutation_contract=mutation_contract,
                data_version="data-v1",
                code_version="code-v1",
            )
        self.assertFalse(result["ok"])
        self.assertTrue(result["technical_completed"])
        self.assertEqual(result["outcome"], "research_rejected")
        self.assertEqual(result["status_code"], "research_rejected")
        self.assertTrue(result["outcome_status"]["research_rejected"])
        self.assertEqual(captured["research_contract"], research_contract)
        self.assertEqual(captured["mutation_contract"], mutation_contract)
        self.assertEqual(captured["data_version"], "data-v1")
        self.assertEqual(captured["code_version"], "code-v1")

    def test_data_failure_has_a_distinct_outcome_and_receipt_evidence(self):
        blueprint = {
            "ok": False,
            "schema": "qiyu_creation_blueprint_v1",
            "error": "candle_cache_missing",
            "detail": {"error": "manifest_missing", "path": "/missing/candles.json"},
        }
        with mock.patch.object(
            creation_blueprint,
            "run_creation_blueprint", return_value=blueprint,
        ):
            result = sole._create_strategy_unlocked(
                symbol="LTC-USDT-SWAP", mission_id="outcome_data",
            )
        self.assertFalse(result["ok"])
        self.assertTrue(result["technical_completed"])
        self.assertEqual(result["outcome"], "data_blocked")
        receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
        self.assertEqual(receipt["blueprint_error"], "candle_cache_missing")
        self.assertEqual(receipt["blueprint_detail"]["error"], "manifest_missing")

    def test_only_reviewable_candidate_sets_ok(self):
        blueprint = valid_blueprint(ok=True, present=True)
        with mock.patch.object(
            creation_blueprint,
            "run_creation_blueprint", return_value=blueprint,
        ):
            result = sole._create_strategy_unlocked(
                symbol="LTC-USDT-SWAP", mission_id="outcome_ready",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["outcome"], "candidate_ready")

    def test_pipeline_exception_is_a_persisted_technical_failure(self):
        with mock.patch.object(
            creation_blueprint,
            "run_creation_blueprint",
            side_effect=RuntimeError("schema exploded"),
        ):
            result = sole._create_strategy_unlocked(
                symbol="LTC-USDT-SWAP", mission_id="outcome_exception",
            )
        self.assertFalse(result["ok"])
        self.assertFalse(result["technical_completed"])
        self.assertEqual(result["outcome"], "technical_failed")
        self.assertTrue(result["outcome_status"]["technical_failed"])
        artifact = result["blueprint"].get("failure_artifact")
        self.assertTrue(artifact)
        self.assertTrue(Path(artifact).exists())
        self.assertEqual(
            result["blueprint"]["detail"]["exception_type"], "RuntimeError",
        )


class TestFormalSubmissionStatus(TempRootCase):
    def _run_qualified(self, formal_result):
        submitted = queue.submit_job(
            "human", "formal status", brief="formal status",
            symbol="LTC-USDT-SWAP",
            wake_workers=False, data_version="d", code_version="c",
        )
        job, running_path, lock = queue.claim_next(0)
        result = {
            "ok": True,
            "technical_completed": True,
            "outcome": "candidate_ready",
            "present_to_human": True,
            "blueprint": {
                "ok": True, "present_to_human": True,
                "run_id": "formal_status", "stages": {},
            },
        }
        try:
            with mock.patch.object(
                creation_sole_entry, "create_strategy", return_value=result,
            ), mock.patch.object(
                formal_review_bridge, "submit_blueprint_to_formal_review",
                return_value=formal_result,
            ):
                return queue.execute_claimed(job, running_path, 0)
        finally:
            queue._release_file_lock(lock)

    def test_attempt_without_submission_remains_candidate_ready(self):
        finished = self._run_qualified({
            "ok": False, "started": False, "attempted": True,
            "submission_created": False, "review_submitted": False,
            "task_id": "failed_internal_task", "reason": "compiler_failed",
        })
        self.assertEqual("candidate_ready", finished["outcome"])
        self.assertTrue(finished["outcome_status"]["candidate_ready"])
        self.assertFalse(finished["outcome_status"]["review_submitted"])
        self.assertFalse(finished["formal_review_started"])
        self.assertTrue(finished["formal_review_attempted"])

    def test_only_accepted_task_receipt_sets_review_submitted(self):
        finished = self._run_qualified({
            "ok": True, "started": True, "attempted": True,
            "submission_created": True, "review_submitted": True,
            "task_id": "accepted_formal_task",
        })
        self.assertEqual("review_submitted", finished["outcome"])
        self.assertTrue(finished["outcome_status"]["review_submitted"])
        self.assertTrue(finished["formal_submission_created"])


class TestCompactFailureEvidence(unittest.TestCase):
    def test_keeps_gate_statistics_judge_and_near_misses_with_bounds(self):
        huge = "x" * 10000
        result = {
            "ok": False,
            "technical_completed": True,
            "outcome": "research_rejected",
            "present_to_human": False,
            "pipeline_gate": {"passed": True, "checks": [{"check": "x", "ok": True}]},
            "blueprint": {
                "ok": False,
                "error": "no_credible_discovery_candidate",
                "detail": {"message": huge},
                "fuses": {"abort_reason": "research_discovery_empty"},
                "run_id": "r1",
                "stages": {
                    "research_discovery": {
                        "n_survivors": 0,
                        "trial_budget": {"effective_trials": 17},
                        "multiple_testing": {
                            "passed": False,
                            "dsr": {"dsr": 0.12, "passed": False},
                            "pbo": {"pbo": 0.75, "passed": False},
                        },
                        "near_miss_diagnostics": [
                            {"hypothesis_id": "h1", "failed_gate": "dsr", "note": huge},
                        ],
                        "failure_lineage": [{"hypothesis_id": "h1", "fail_stage": "multiple_testing"}],
                        "judge": {"kimi": {"enabled": True, "decision": "ADMIT"}},
                    },
                },
            },
        }
        compact = queue._compact_result(result)
        evidence = compact["failure_evidence"]
        self.assertEqual(evidence["multiple_testing"]["dsr"]["dsr"], 0.12)
        self.assertEqual(evidence["multiple_testing"]["pbo"]["pbo"], 0.75)
        self.assertEqual(evidence["near_miss_diagnostics"][0]["hypothesis_id"], "h1")
        named = evidence["named_statistical_and_judge_evidence"]
        self.assertTrue(any("kimi" in path for path in named))
        self.assertLess(len(json.dumps(compact, ensure_ascii=False)), 50000)


class TestJobFingerprintAndCooldown(TempRootCase):
    def test_full_identity_changes_for_each_material_input(self):
        base = dict(
            text="same direction", symbol="LTC-USDT-SWAP", timeframe="5m",
            direction="long", brief="brief", data_version="d1", code_version="c1",
            research_contract={"event": "a"}, mutation_contract={"parent_id": "p1"},
            skip_llm=True, max_loops=5,
        )
        key = queue._direction_key(**base)
        for field, value in (
            ("symbol", "BTC-USDT-SWAP"), ("timeframe", "15m"),
            ("direction", "short"), ("brief", "changed"),
            ("data_version", "d2"), ("code_version", "c2"),
            ("research_contract", {"event": "b"}),
            ("mutation_contract", {"parent_id": "p2"}),
            ("skip_llm", False), ("max_loops", 6),
        ):
            changed = dict(base)
            changed[field] = value
            self.assertNotEqual(key, queue._direction_key(**changed), field)

    def test_contract_runtime_metadata_cannot_evade_semantic_deduplication(self):
        base = dict(
            text="same direction", symbol="LTC-USDT-SWAP", timeframe="5m",
            direction="long", brief="brief", data_version="d1", code_version="c1",
            research_contract={
                "target": {"symbol": "LTC-USDT-SWAP", "timeframe": "5m"},
                "event_contract": {
                    "entry_conditions": [{"feature": "J_5m", "value": 89}],
                    "verified_at": "2026-08-01 01:00:00",
                },
                "compiled_at": "2026-08-01 01:00:00",
            },
            skip_llm=True, max_loops=5,
        )
        first = queue._direction_key(**base)
        recompiled = dict(base)
        recompiled["research_contract"] = {
            "target": {"symbol": "LTC-USDT-SWAP", "timeframe": "5m"},
            "event_contract": {
                "entry_conditions": [{"feature": "J_5m", "value": 89}],
                "verified_at": "2026-08-01 02:00:00",
            },
            "compiled_at": "2026-08-01 02:00:00",
        }
        self.assertEqual(first, queue._direction_key(**recompiled))

        changed_rule = dict(recompiled)
        changed_rule["research_contract"] = dict(recompiled["research_contract"])
        changed_rule["research_contract"]["event_contract"] = {
            "entry_conditions": [{"feature": "J_5m", "value": 88}],
            "verified_at": "2026-08-01 02:00:00",
        }
        self.assertNotEqual(first, queue._direction_key(**changed_rule))

        submit = dict(
            source="human", research_direction="same direction",
            symbol="LTC-USDT-SWAP", timeframe="5m", direction="long", brief="brief",
            wake_workers=False, data_version="d1", code_version="c1",
            cooldown_seconds=3600,
        )
        accepted = queue.submit_job(
            research_contract=base["research_contract"], **submit
        )
        duplicate = queue.submit_job(
            research_contract=recompiled["research_contract"], **submit
        )
        self.assertTrue(accepted["accepted"])
        self.assertFalse(duplicate["accepted"])
        self.assertEqual(duplicate["duplicate_state"], "pending")

    def test_active_and_completed_duplicates_do_not_enqueue_again(self):
        kwargs = dict(
            source="human", research_direction="same", symbol="LTC-USDT-SWAP",
            timeframe="5m", direction="long", brief="exact brief",
            wake_workers=False, data_version="data-v1", code_version="code-v1",
            cooldown_seconds=3600,
        )
        first = queue.submit_job(**kwargs)
        second = queue.submit_job(**kwargs)
        self.assertTrue(first["accepted"])
        self.assertFalse(second["accepted"])
        self.assertEqual(second["duplicate_state"], "pending")
        self.assertEqual(len(list((queue.base_dir() / "pending").glob("*.json"))), 1)

        row = queue._read(queue._job_path("pending", first["job_id"]))
        row["finished_ts"] = time.time()
        atomic_write_json(queue._job_path("completed", first["job_id"]), row)
        queue._job_path("pending", first["job_id"]).unlink()
        third = queue.submit_job(**kwargs)
        self.assertFalse(third["accepted"])
        self.assertEqual(third["duplicate_state"], "completed")
        self.assertGreater(third["cooldown_remaining_seconds"], 0)

        changed_data = dict(kwargs)
        changed_data["data_version"] = "data-v2"
        fourth = queue.submit_job(**changed_data)
        self.assertTrue(fourth["accepted"])

        forced = queue.submit_job(force=True, **kwargs)
        self.assertTrue(forced["accepted"])

    def test_worker_persists_research_rejected_not_false_success(self):
        submitted = queue.submit_job(
            "human", "worker status", brief="worker status", wake_workers=False,
            symbol="LTC-USDT-SWAP",
            data_version="d", code_version="c",
        )
        job, running_path, lock = queue.claim_next(0)
        self.assertEqual(job["job_id"], submitted["job_id"])
        try:
            with mock.patch.dict(os.environ, {
                "QIYU_PARALLEL_CREATION_SELFTEST": "1",
                "QIYU_PARALLEL_SELFTEST_DELAY": "0",
            }, clear=False):
                finished = queue.execute_claimed(job, running_path, 0)
        finally:
            queue._release_file_lock(lock)
        self.assertTrue(finished["technical_completed"])
        self.assertEqual(finished["outcome"], "research_rejected")
        self.assertEqual(finished["status_code"], "research_rejected")
        self.assertTrue(finished["outcome_status"]["research_rejected"])
        self.assertFalse(finished["formal_review_handoff_ready"])
        persisted = queue._read(queue._job_path("completed", job["job_id"]))
        self.assertEqual(persisted["outcome"], "research_rejected")


if __name__ == "__main__":
    unittest.main()
