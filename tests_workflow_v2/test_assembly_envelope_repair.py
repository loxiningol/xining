# -*- coding: utf-8 -*-
from __future__ import print_function

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dual_engine_workflow_v2 import creation_blueprint as blueprint
from dual_engine_workflow_v2 import research_discovery as discovery


def _admitted_payload(contract_id, hypothesis_id, mechanism_id, factor, returns):
    hypothesis = {
        "hypothesis_id": hypothesis_id,
        "mechanism_id": mechanism_id,
        "family": "microstructure_exhaustion",
        "statement_zh": "%s exact admitted hypothesis" % hypothesis_id,
        "required_entry_timing": {"mode": "next_bar_open"},
    }
    probe = {
        "event_id": "event_%s" % hypothesis_id,
        "event_kind": "human_contract_exact",
        "event_logic": "ordered_joins",
        "terms": [{"factor": factor, "op": ">=", "value": 1.0, "join": "root"}],
        "factor": factor,
        "side": "high",
        "q": 0.9,
        "trade_direction": "long",
        "horizon_bars": 3,
        "execution_mapping": "next_bar_open",
        "primary_cost_scenario": "base",
        "primary_cost_per_trade": 0.001,
        "statistical_returns_are_post_cost": True,
        "mean_net": sum(returns) / float(len(returns)),
    }
    recipe = discovery.assembly_recipe(
        hypothesis, probe, {"contract_id": contract_id},
    )
    return {
        "recipe_id": recipe["recipe_id"],
        "recipe": recipe,
        "hypothesis": hypothesis,
        "probe": probe,
        "probe_returns": list(returns),
        "multiple_testing_gate": {"passed": True, "dsr": {"passed": True}},
        "judge": {"admit_to_assembly": True},
        "feasibility": {"passed": True},
        "execution": {"passed": True},
        "antifalsify": {"passed": True},
    }


class TestLockedAdmissionEnvelope(unittest.TestCase):
    def test_failed_admitted_recipe_only_falls_through_to_admitted_peer(self):
        contract_id = "rc_locked_test"
        contract = {
            "schema": "qiyu_research_contract_v2",
            "valid": True,
            "contract_id": contract_id,
        }
        first = _admitted_payload(
            contract_id, "hyp_first", "mech_first", "first_admitted_factor",
            [0.011] * 10,
        )
        second = _admitted_payload(
            contract_id, "hyp_second", "mech_second", "second_admitted_factor",
            [0.022] * 10,
        )

        # This is deliberately much higher-scoring than either admitted row,
        # but it is outside assembly_payload and therefore outside the immutable
        # admission envelope.  It models the old classic/global-miner escape path.
        unadmitted_classic = {
            "recipe_id": "recipe_unadmitted_classic",
            "hypothesis_id": "hyp_unadmitted_classic",
            "factor": "classic_global_factor",
            "score": 999999.0,
            "returns": [0.99] * 50,
        }
        disc = {
            "assembly_payload": [first, second],
            "handoff": {"best_factor": unadmitted_classic},
            "legacy_global_candidates": [unadmitted_classic],
        }
        stages = {
            "data": {"n_bars": 100, "source": "unit_test"},
            "meta": {
                "design_doc": {
                    "mechanism_family": "pre_discovery_design",
                    "hypotheses": [{"hypothesis_id": "pre_discovery_seed"}],
                    "constraints": {},
                }
            },
        }
        data = {
            "n_bars": 100,
            "candles": [
                {"ts": 1767225600000},
                {"ts": 1769904000000},
            ],
        }

        def certify(candidates, max_daily_loss=None):
            row = dict(candidates[0])
            row["quantoracle"] = {
                "source": "unit_test",
                "certified": {"sharpe_ratio": 2.0},
            }
            return [row], [row]

        stress_results = [
            {"passed": False, "human_banner_zh": "first recipe stress failure"},
            {
                "passed": True,
                "backtrader": {"full_max_drawdown": -0.04},
                "human_banner_zh": "passed",
            },
        ]
        prelim_pass = {
            "present_to_human": True,
            "win_rate": 1.0,
            "window": {"label_zh": "unit test", "return_scope_zh": "post-cost"},
            "reject_reasons": [],
        }
        hardness_pass = {
            "passed": True,
            "metrics": {"weekly_return_proxy": 0.05, "return_mdd": -0.04},
            "reject_reasons": [],
            "degeneration": {"detected": False},
        }

        with tempfile.TemporaryDirectory(prefix="qiyu_assembly_envelope_") as tmp:
            with mock.patch.object(blueprint, "_certify_factors", side_effect=certify), \
                    mock.patch.object(
                        blueprint.rh, "factor_ls_weekly_lev",
                        return_value={"ok": True, "weekly_lev": 0.10},
                    ), \
                    mock.patch.object(
                        blueprint.stress, "run_stress", side_effect=stress_results,
                    ) as stress_mock, \
                    mock.patch.object(
                        blueprint.prelim, "prelim_eval", return_value=prelim_pass,
                    ), \
                    mock.patch.object(
                        blueprint.rh, "evaluate_return_hardness",
                        return_value=hardness_pass,
                    ), \
                    mock.patch.object(
                        blueprint, "_creation_probes", return_value={"unit": {"ok": True}},
                    ), \
                    mock.patch.object(
                        blueprint, "_mine_with_specs",
                        side_effect=AssertionError("global miner must not run after admission"),
                    ) as mine_mock, \
                    mock.patch.object(
                        blueprint, "_switch_direction",
                        side_effect=AssertionError("classic/perspective switch must not run"),
                    ) as switch_mock:
                result = blueprint._assemble_admitted_population(
                    symbol="ADA-USDT-SWAP",
                    timeframe="5m",
                    direction="long",
                    brief="locked assembly test",
                    data=data,
                    stages=stages,
                    disc=disc,
                    compiled_contract=contract,
                    run_id="assembly_envelope_test",
                    out_dir=tmp,
                )

            self.assertTrue(result["ok"])
            self.assertEqual(stress_mock.call_count, 2)
            mine_mock.assert_not_called()
            switch_mock.assert_not_called()

            attempts = result["stages"]["assembly_lineage"]["attempts"]
            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[0]["recipe_id"], first["recipe_id"])
            self.assertEqual(attempts[0]["failed_gate"], "stress")
            self.assertFalse(attempts[0]["passed"])
            self.assertEqual(attempts[1]["recipe_id"], second["recipe_id"])
            self.assertTrue(attempts[1]["passed"])

            envelope = result["stages"]["admission_envelope"]
            lineage = result["stages"]["assembly_lineage"]
            self.assertEqual(envelope["recipe_ids"], [first["recipe_id"], second["recipe_id"]])
            self.assertEqual(lineage["selected_recipe_id"], second["recipe_id"])
            self.assertTrue(lineage["selected_is_in_admission_envelope"])
            self.assertFalse(lineage["global_factor_mining_used"])
            self.assertFalse(lineage["classic_or_unadmitted_switch_used"])
            self.assertNotEqual(lineage["selected_recipe_id"], unadmitted_classic["recipe_id"])

            self.assertEqual(result["best_factor"]["recipe_id"], second["recipe_id"])
            self.assertEqual(result["best_factor"]["hypothesis_id"], "hyp_second")
            self.assertEqual(result["stages"]["selected"]["recipe_id"], second["recipe_id"])
            self.assertEqual(result["stages"]["selected"]["hypothesis_id"], "hyp_second")
            self.assertEqual(result["glm_research_brief"]["selected_recipe_id"], second["recipe_id"])

            persisted = json.loads(
                Path(result["deliverables"]["blueprint_json"]).read_text(encoding="utf-8")
            )
            self.assertEqual(persisted["best_factor"]["recipe_id"], second["recipe_id"])
            self.assertEqual(persisted["best_factor"]["hypothesis_id"], "hyp_second")
            self.assertEqual(
                persisted["stages"]["assembly_lineage"]["selected_recipe_id"],
                second["recipe_id"],
            )
            serialized = json.dumps(persisted, ensure_ascii=False)
            self.assertNotIn(unadmitted_classic["recipe_id"], serialized)
            self.assertNotIn(unadmitted_classic["hypothesis_id"], serialized)


if __name__ == "__main__":
    unittest.main()
