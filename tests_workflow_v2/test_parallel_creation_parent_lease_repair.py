# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dual_engine_workflow_v2 import parallel_creation as queue
from dual_engine_workflow_v2.process_safe_state import atomic_write_json


class TempQueueCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qiyu_parent_lease_")
        self.env = mock.patch.dict(os.environ, {"VECTOR_ROOT": self.temp.name}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def contract(self, threshold, compiled_at="2026-08-01 00:00:00"):
        return {
            "schema": "qiyu_research_contract_v2",
            "target": {
                "symbol": "ADA-USDT-SWAP",
                "timeframe": "5m",
                "direction": "long",
            },
            "event_contract": {
                "entry_conditions": [{
                    "feature": "J_5m",
                    "operator": ">=",
                    "value": threshold,
                }],
            },
            "compiled_at": compiled_at,
        }

    def write_parent(self, job_id="parent-1", state="failed", gate="dsr"):
        row = {
            "schema": queue.SCHEMA,
            "job_id": job_id,
            "symbol": "ADA-USDT-SWAP",
            "timeframe": "5m",
            "trade_direction": "long",
            "brief": "mutation baseline",
            "research_direction": "mutation baseline",
            "research_contract": self.contract(90, compiled_at="2026-07-31 23:59:00"),
            "outcome": "research_rejected",
            "status_code": "research_rejected",
            "result": {
                "outcome": "research_rejected",
                "failure_evidence": {
                    "failure_lineage": [
                        {"hypothesis_id": "h1", "failed_gate": gate},
                    ],
                    "multiple_testing": {gate: {"passed": False}},
                },
            },
        }
        atomic_write_json(queue._job_path(state, job_id), row)
        return row

    def mutation(self, parent_id="parent-1", gate="dsr"):
        return {
            "parent_id": parent_id,
            "failed_gate": gate,
            "allowed_mutations": ["entry_threshold"],
            "structural_delta": {
                "entry_threshold": {"before": 90, "after": 89},
            },
        }

    def submit(self, mutation_contract=None, suffix="x", **overrides):
        kwargs = {
            "source": "human",
            "research_direction": "mutation baseline",
            "symbol": "ADA-USDT-SWAP",
            "timeframe": "5m",
            "direction": "long",
            "brief": "mutation baseline",
            "research_contract": self.contract(89),
            "mutation_contract": mutation_contract,
            "wake_workers": False,
            "data_version": "data-v1",
            "code_version": "code-v1",
        }
        kwargs.update(overrides)
        return queue.submit_job(**kwargs)


class TestMutationParentValidation(TempQueueCase):
    def test_parentless_request_keeps_first_round_behavior(self):
        result = self.submit(
            mutation_contract={"allowed_mutations": "legacy-parentless-shape"},
            suffix="parentless",
        )
        self.assertTrue(result["accepted"])
        self.assertIsNone(result["parent_evidence_hash"])

    def test_bidirectional_request_must_be_split_before_queueing(self):
        with self.assertRaisesRegex(ValueError, "split into two jobs"):
            queue.submit_job(
                "human", "both directions", direction="both",
                wake_workers=False, data_version="d", code_version="c",
            )
        self.assertEqual(list((queue.base_dir() / "pending").glob("*.json")), [])

    def test_declared_parent_must_exist_in_terminal_queue(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.submit(self.mutation(parent_id="missing-parent"), suffix="missing")

        # A pending receipt is deliberately not a valid mutation parent.
        atomic_write_json(queue._job_path("pending", "pending-parent"), {
            "job_id": "pending-parent",
            "result": {"failure_evidence": {"failed_gate": "dsr"}},
        })
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.submit(self.mutation(parent_id="pending-parent"), suffix="pending")

    def test_failed_gate_must_be_exact_parent_evidence(self):
        row = self.write_parent(gate="not_dsr_related")
        row["result"]["failure_evidence"] = {"note": "not_dsr_related"}
        atomic_write_json(queue._job_path("failed", "parent-1"), row)
        with self.assertRaisesRegex(ValueError, "absent from parent evidence"):
            self.submit(self.mutation(gate="dsr"), suffix="wrong-gate")

    def test_metric_presence_does_not_prove_that_gate_failed(self):
        row = self.write_parent(gate="judge")
        row["result"]["failure_evidence"]["multiple_testing"] = {
            "dsr": {"ok": True, "passed": True, "dsr": 0.99},
        }
        atomic_write_json(queue._job_path("failed", "parent-1"), row)
        with self.assertRaisesRegex(ValueError, "absent from parent evidence"):
            self.submit(self.mutation(gate="dsr"), suffix="metric-only")

    def test_mutation_shapes_are_fail_closed(self):
        self.write_parent()
        invalid_rows = (
            dict(self.mutation(), failed_gate=""),
            dict(self.mutation(), allowed_mutations=[]),
            dict(self.mutation(), allowed_mutations="entry_threshold"),
            dict(self.mutation(), allowed_mutations=["x", "x"]),
            dict(self.mutation(), structural_delta=[]),
            dict(self.mutation(), structural_delta={}),
            dict(self.mutation(), structural_delta={"x": float("nan")}),
            dict(self.mutation(), structural_delta={"unapproved_field": {"after": 1}}),
        )
        for index, contract in enumerate(invalid_rows):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    self.submit(contract, suffix="invalid-%s" % index)

    def test_valid_parent_binds_child_to_evidence_hash(self):
        parent = self.write_parent(state="completed")
        result = self.submit(self.mutation(), suffix="valid")
        self.assertTrue(result["accepted"])
        expected = queue._canonical_json_hash(
            queue._parent_evidence_payload(parent, "completed")
        )
        self.assertEqual(result["parent_evidence_hash"], expected)
        child = queue._read(queue._job_path("pending", result["job_id"]))
        self.assertEqual(child["parent_evidence_hash"], expected)
        validation = child["mutation_parent_validation"]
        self.assertTrue(validation["verified"])
        self.assertEqual(validation["parent_job_id"], "parent-1")
        self.assertEqual(validation["parent_terminal_state"], "completed")
        self.assertEqual(validation["failed_gate"], "dsr")
        self.assertEqual(
            validation["actual_identity_changes"],
            ["research_contract.event_contract.entry_conditions.0.value"],
        )

    def test_every_actual_parent_child_identity_change_must_be_declared(self):
        self.write_parent()
        cases = (
            {"symbol": "BTC-USDT-SWAP"},
            {"timeframe": "15m"},
            {"direction": "short"},
            {"brief": "changed brief"},
            {"research_direction": "changed research direction"},
            {"research_contract": self.contract(88)},
        )
        for index, overrides in enumerate(cases):
            with self.subTest(index=index, overrides=overrides):
                with self.assertRaisesRegex(
                    ValueError, "undeclared or structurally mismatched",
                ):
                    self.submit(self.mutation(), **overrides)

    def test_declared_top_level_change_requires_exact_before_and_after(self):
        self.write_parent()
        contract = {
            "parent_id": "parent-1",
            "failed_gate": "dsr",
            "allowed_mutations": ["brief"],
            "structural_delta": {
                "brief": {"before": "mutation baseline", "after": "new brief"},
            },
        }
        accepted = self.submit(
            contract,
            brief="new brief",
            research_contract=self.contract(90, compiled_at="later-runtime-only"),
        )
        self.assertTrue(accepted["accepted"])

        contract["structural_delta"]["brief"]["after"] = "a lie"
        with self.assertRaisesRegex(ValueError, "structurally mismatched"):
            self.submit(
                contract,
                brief="new brief",
                research_contract=self.contract(90),
                force=True,
            )

    def test_noop_declared_mutation_is_rejected(self):
        self.write_parent()
        with self.assertRaisesRegex(ValueError, "does not match any actual"):
            self.submit(
                self.mutation(),
                research_contract=self.contract(90, compiled_at="runtime-only-change"),
            )


class TestWorkerLeaseRecovery(TempQueueCase):
    def claim(self, suffix="lease"):
        submitted = self.submit(suffix=suffix)
        job, path, lock = queue.claim_next(0)
        self.addCleanup(queue._release_file_lock, lock)
        self.assertEqual(job["job_id"], submitted["job_id"])
        return job, Path(path)

    def make_old(self, job, path, seconds=100):
        old = time.time() - seconds
        job["started_ts"] = old
        job["heartbeat_ts"] = old
        job["progress"] = dict(job.get("progress") or {}, updated_ts=old)
        atomic_write_json(path, job)
        os.utime(str(path), (old, old))
        return old

    def test_claim_records_pid_host_and_progress_heartbeat(self):
        job, path = self.claim("claim-fields")
        self.assertEqual(job["worker_pid"], os.getpid())
        self.assertEqual(job["worker_host"], socket.gethostname())
        self.assertIn("worker_pid_start_token", job)
        self.assertEqual(job["worker_pid_start_token"], queue._pid_start_token(os.getpid()))
        self.assertEqual(job["heartbeat_ts"], job["progress"]["updated_ts"])
        old_heartbeat = job["heartbeat_ts"]
        queue.update_job_progress(path, "probe", detail="lease heartbeat")
        refreshed = queue._read(path)
        self.assertGreaterEqual(refreshed["heartbeat_ts"], old_heartbeat)
        self.assertEqual(refreshed["heartbeat_ts"], refreshed["progress"]["updated_ts"])

    def test_live_local_pid_is_never_recovered_even_when_timestamp_is_old(self):
        job, path = self.claim("live-pid")
        self.make_old(job, path)
        with mock.patch.object(queue, "_pid_is_alive", return_value=True) as alive:
            recovered = queue.recover_stale_jobs(max_age_seconds=1)
        self.assertEqual(recovered, [])
        alive.assert_called_once_with(os.getpid())
        self.assertTrue(path.exists())

    def test_reused_live_pid_does_not_own_old_lease(self):
        job, path = self.claim("reused-pid")
        job["worker_pid_start_token"] = "linux-proc:old-boot:10"
        self.make_old(job, path)
        with mock.patch.object(queue, "_pid_is_alive", return_value=True), mock.patch.object(
            queue, "_pid_start_token", return_value="linux-proc:new-boot:99",
        ):
            recovered = queue.recover_stale_jobs(max_age_seconds=1)
        self.assertEqual(recovered, [job["job_id"]])

    def test_live_pid_falls_back_safely_when_proc_token_is_unavailable(self):
        job, path = self.claim("no-proc")
        self.make_old(job, path)
        with mock.patch.object(queue, "_pid_is_alive", return_value=True), mock.patch.object(
            queue, "_pid_start_token", return_value=None,
        ):
            recovered = queue.recover_stale_jobs(max_age_seconds=1)
        self.assertEqual(recovered, [])
        self.assertTrue(path.exists())

    def test_recent_heartbeat_protects_dead_worker_job(self):
        job, path = self.claim("recent-heartbeat")
        old = time.time() - 100
        job["started_ts"] = old
        job["heartbeat_ts"] = time.time()
        job["progress"] = dict(job.get("progress") or {}, updated_ts=old)
        atomic_write_json(path, job)
        with mock.patch.object(queue, "_pid_is_alive", return_value=False) as alive:
            recovered = queue.recover_stale_jobs(max_age_seconds=10)
        self.assertEqual(recovered, [])
        alive.assert_not_called()
        self.assertTrue(path.exists())

    def test_dead_stale_job_is_requeued_and_lease_fields_are_cleared(self):
        job, path = self.claim("dead-pid")
        self.make_old(job, path)
        with mock.patch.object(queue, "_pid_is_alive", return_value=False):
            recovered = queue.recover_stale_jobs(max_age_seconds=1)
        self.assertEqual(recovered, [job["job_id"]])
        self.assertFalse(path.exists())
        queued = queue._read(queue._job_path("pending", job["job_id"]))
        self.assertNotIn("worker_pid", queued)
        self.assertNotIn("worker_host", queued)
        self.assertNotIn("heartbeat_ts", queued)
        self.assertEqual(queued["recovery_count"], 1)

    def test_terminal_receipt_reconciles_stale_running_without_requeue(self):
        job, path = self.claim("terminal-crash-window")
        self.make_old(job, path)
        terminal = dict(job)
        terminal.update({
            "status": "本轮结束",
            "outcome": "research_rejected",
            "status_code": "research_rejected",
            "finished_ts": time.time() - 50,
        })
        terminal_path = queue._job_path("completed", job["job_id"])
        atomic_write_json(terminal_path, terminal)

        with mock.patch.object(queue, "_pid_is_alive", return_value=False) as alive:
            recovered = queue.recover_stale_jobs(max_age_seconds=1)

        self.assertEqual(recovered, [])
        alive.assert_not_called()
        self.assertFalse(path.exists())
        self.assertTrue(terminal_path.exists())
        self.assertFalse(queue._job_path("pending", job["job_id"]).exists())

    def test_failed_terminal_also_reconciles_without_requeue(self):
        job, path = self.claim("failed-terminal-crash-window")
        self.make_old(job, path)
        terminal = dict(job)
        terminal.update({
            "status": "研究执行未完成",
            "outcome": "technical_failed",
            "status_code": "technical_failed",
            "finished_ts": time.time() - 50,
        })
        terminal_path = queue._job_path("failed", job["job_id"])
        atomic_write_json(terminal_path, terminal)

        with mock.patch.object(queue, "_pid_is_alive", return_value=False) as alive:
            recovered = queue.recover_stale_jobs(max_age_seconds=1)

        self.assertEqual(recovered, [])
        alive.assert_not_called()
        self.assertFalse(path.exists())
        self.assertTrue(terminal_path.exists())
        self.assertFalse(queue._job_path("pending", job["job_id"]).exists())

    def test_live_pid_on_another_host_does_not_block_recovery(self):
        job, path = self.claim("remote-pid")
        job["worker_host"] = "another-host"
        self.make_old(job, path)
        with mock.patch.object(queue, "_pid_is_alive", return_value=True) as alive:
            recovered = queue.recover_stale_jobs(max_age_seconds=1)
        self.assertEqual(recovered, [job["job_id"]])
        alive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
