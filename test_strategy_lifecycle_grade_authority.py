# -*- coding: utf-8 -*-
"""Hourly lifecycle scoring must not overwrite outcome-managed grades."""
from __future__ import print_function

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class LifecycleGradeAuthorityTest(unittest.TestCase):
    def test_newer_grade_transition_wins_over_stale_hourly_snapshot(self):
        import auto_trade_strategy_lifecycle as lifecycle
        aid = "NG-USDT-SWAP|5m|managed"
        latest = {"assignments": {aid: {
            "grade_managed_by": "trade_outcome_state_machine",
            "lifecycle_grade": "C", "max_position_ratio": 0.10,
            "grade_window": "C_next3", "grade_changed_at": "new",
            "rotation_score": 1.0,
        }}}
        stale = {aid: {
            "grade_managed_by": "trade_outcome_state_machine",
            "lifecycle_grade": "B", "max_position_ratio": 0.30,
            "grade_window": "B_promote", "grade_changed_at": "old",
            "rotation_score": 88.0, "rotation_updated_at": "hourly",
        }}
        merged = lifecycle._merge_outcome_managed_rows(latest, stale)[aid]
        self.assertEqual(merged["lifecycle_grade"], "C")
        self.assertEqual(merged["max_position_ratio"], 0.10)
        self.assertEqual(merged["grade_changed_at"], "new")
        self.assertEqual(merged["rotation_score"], 88.0)

    def test_hourly_score_is_advisory_for_outcome_managed_strategy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            auto = root / "auto_trade"
            auto.mkdir(parents=True)
            aid = "NG-USDT-SWAP|5m|managed_b"
            (auto / "strategy_runtime_controls.json").write_text(
                json.dumps({"assignments": {aid: {
                    "symbol": "NG-USDT-SWAP", "timeframe": "5m",
                    "strategy_key": "managed_b",
                    "audit_state": "conditional_frequency_probe",
                    "pause_new_entries": False, "new_entries_allowed": True,
                    "lifecycle_grade": "B", "max_grade": "B",
                    "max_position_ratio": 0.30,
                    "grade_managed_by": "trade_outcome_state_machine",
                }}}), encoding="utf-8")
            (auto / "live_portfolio_frequency.json").write_text(
                json.dumps({"assignments": [{
                    "symbol": "NG-USDT-SWAP", "timeframe": "5m",
                    "strategy_key": "managed_b", "trades": 20,
                    "win_rate": 10.0, "return_pct": -50.0,
                }]}), encoding="utf-8")
            (auto / "live_strategy_retrospective_audit.json").write_text(
                json.dumps({"results": []}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"VECTOR_ROOT": str(root)}, clear=False):
                import auto_trade_strategy_lifecycle as lifecycle
                importlib.reload(lifecycle)
                with mock.patch.object(
                        lifecycle, "_triple_cost_mean_negative",
                        return_value=(False, "")), mock.patch.object(
                            lifecycle, "_stop_loss_frequency_elevated",
                            return_value=(False, "")), mock.patch.object(
                                lifecycle, "_score_declined_three_hours",
                                return_value=True), mock.patch.object(
                                    lifecycle, "_dual_ai_logic_failure",
                                    return_value=(False, "")), mock.patch.object(
                                        lifecycle, "_death_precursor",
                                        return_value=(False, "")), mock.patch.object(
                                            lifecycle, "_try_wake_dormant",
                                            return_value=[]), mock.patch.object(
                                                lifecycle, "promote_shadow_to_c_probes",
                                                return_value=[]):
                    out = lifecycle.run_once(skip_ai=True)
            row = json.loads((auto / "strategy_runtime_controls.json").read_text(
                encoding="utf-8"))["assignments"][aid]
            self.assertEqual(row["lifecycle_grade"], "B")
            self.assertEqual(row["max_position_ratio"], 0.30)
            self.assertEqual(out["downgraded"], [])
            self.assertEqual(out["eliminated"], [])


if __name__ == "__main__":
    unittest.main()
