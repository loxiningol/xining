# -*- coding: utf-8 -*-
from __future__ import print_function

import tempfile
import unittest
from unittest import mock

from dual_engine_workflow_v2 import creation_sole_entry
from dual_engine_workflow_v2 import parallel_creation
from dual_engine_workflow_v2 import research_symbol_policy


class ResearchSymbolBanRepairTests(unittest.TestCase):
    def test_ada_is_forbidden_without_touching_live_execution(self):
        probe = research_symbol_policy.policy_probe()
        self.assertIn("ADA-USDT-SWAP", probe["forbidden_symbols"])
        self.assertFalse(probe["changes_existing_live_execution"])

    def test_queue_rejects_ada_before_writing_a_job(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(
            "os.environ", {"VECTOR_ROOT": root}, clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "research_symbol_forbidden"):
                parallel_creation.submit_job(
                    "human", "trend breakout", symbol="ADA-USDT-SWAP",
                    wake_workers=False,
                )
            self.assertEqual([], list(parallel_creation.base_dir().glob("pending/*.json")))

    def test_queue_requires_explicit_symbol_and_accepts_non_ada(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(
            "os.environ", {"VECTOR_ROOT": root}, clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "symbol is required"):
                parallel_creation.submit_job(
                    "human", "trend breakout", wake_workers=False,
                )
            accepted = parallel_creation.submit_job(
                "human", "trend breakout", symbol="BTC-USDT-SWAP",
                wake_workers=False, data_version="d1", code_version="c1",
            )
            self.assertTrue(accepted["accepted"])

    def test_direct_sole_entry_rejects_ada_before_blueprint(self):
        with mock.patch(
            "dual_engine_workflow_v2.creation_blueprint.run_creation_blueprint",
            side_effect=AssertionError("blueprint must not run"),
        ):
            with self.assertRaisesRegex(ValueError, "research_symbol_forbidden"):
                creation_sole_entry.create_strategy(symbol="ADA-USDT-SWAP")


if __name__ == "__main__":
    unittest.main()
