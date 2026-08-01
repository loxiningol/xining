# -*- coding: utf-8 -*-
from __future__ import print_function

import copy
import unittest

from dual_engine_workflow_v2 import pipeline_step_a
from dual_engine_workflow_v2 import research_contract


class FrequencyContractGateRepairTests(unittest.TestCase):
    def _contract(self):
        return research_contract.compile_contract(
            "所有周期；必须平均周交易量大于0.75",
            "BTC-USDT-SWAP", "5m", direction="long",
        )

    def test_weekly_frequency_is_immutable_and_recompile_stable(self):
        contract = self._contract()
        self.assertTrue(contract["valid"])
        self.assertEqual(
            {
                "metric": "theoretical_weekly_opens",
                "operator": ">",
                "threshold": 0.75,
                "scope": "pre_review_submission",
                "source": "human_instruction",
            },
            contract["performance_contract"],
        )
        recompiled = research_contract.compile_contract(
            contract["brief"], "BTC-USDT-SWAP", "5m", direction="long",
            constraints={"research_contract": contract},
            data_version="runtime-data-version",
            code_version="runtime-code-version",
        )
        self.assertEqual(contract["contract_id"], recompiled["contract_id"])
        self.assertTrue(research_contract.verify_contract_integrity(recompiled)["ok"])

        tampered = copy.deepcopy(contract)
        tampered["performance_contract"]["threshold"] = 0.50
        audit = research_contract.verify_contract_integrity(tampered)
        self.assertFalse(audit["ok"])
        self.assertIn("research_contract_body_hash_mismatch", audit["reasons"])

        weekly_opens_wording = research_contract.compile_contract(
            "必须平均周开仓大于0.75", "BTC-USDT-SWAP", "5m", direction="long",
        )
        self.assertTrue(weekly_opens_wording["valid"])
        self.assertEqual(
            0.75, weekly_opens_wording["performance_contract"]["threshold"],
        )

    def test_strict_gate_blocks_equal_or_lower_before_review(self):
        contract = self._contract()
        for observed in (None, 0.70, 0.75):
            theo, verified = pipeline_step_a._apply_contract_frequency_gate(
                {
                    "approved": True,
                    "ai_theoretical_weekly_opens_avg": observed,
                },
                {"ok": True, "reasons": []},
                contract,
            )
            self.assertFalse(theo["approved"])
            self.assertFalse(verified["ok"])
            self.assertFalse(verified["research_contract_frequency_gate"]["pass"])

    def test_strict_gate_allows_only_value_above_threshold(self):
        contract = self._contract()
        theo, verified = pipeline_step_a._apply_contract_frequency_gate(
            {
                "approved": True,
                "ai_theoretical_weekly_opens_avg": 0.750001,
            },
            {"ok": True, "reasons": []},
            contract,
        )
        self.assertTrue(theo["approved"])
        self.assertTrue(verified["ok"])
        self.assertTrue(verified["research_contract_frequency_gate"]["pass"])


if __name__ == "__main__":
    unittest.main()
