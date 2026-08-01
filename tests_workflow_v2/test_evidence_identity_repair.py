# -*- coding: utf-8 -*-
from __future__ import print_function

import math
import os
import sys
import tempfile
import unittest
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dual_engine_workflow_v2 import antifalsify
from dual_engine_workflow_v2 import creation_blueprint
from dual_engine_workflow_v2 import creation_stress_lite
from dual_engine_workflow_v2 import heterogeneous_committee
from dual_engine_workflow_v2 import research_discovery


def _candles(n):
    return [
        {
            "ts": 1800000000000 + index * 300000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
        }
        for index in range(n)
    ]


def _admitted_payload(contract_id):
    hypothesis = {
        "hypothesis_id": "hyp_evidence_identity",
        "mechanism_id": "mech_evidence_identity",
        "family": "microstructure_exhaustion",
    }
    probe = {
        "event_id": "event_evidence_identity",
        "event_kind": "literal",
        "terms": [{"factor": "ret_3", "side": "high", "q": 0.8}],
        "factor": "ret_3",
        "side": "high",
        "q": 0.8,
        "trade_direction": "long",
        "horizon_bars": 3,
        "execution_mapping": "next_bar_open",
        "primary_cost_scenario": "taker_taker",
        "primary_cost_per_trade": 0.001,
        "statistical_returns_are_post_cost": True,
        "mean_net": 0.01,
    }
    recipe = research_discovery.assembly_recipe(
        hypothesis, probe, {"contract_id": contract_id},
    )
    return {
        "recipe_id": recipe["recipe_id"],
        "recipe": recipe,
        "hypothesis": hypothesis,
        "probe": probe,
        "probe_returns": [0.01] * 20,
        "multiple_testing_gate": {"passed": True},
        "judge": {"admit_to_assembly": True},
        "feasibility": {"passed": True},
        "execution": {"passed": True},
        "antifalsify": {"passed": True},
    }


class EvidenceIdentityTests(unittest.TestCase):
    def test_generic_antifalsify_keeps_short_horizon_and_execution_identity(self):
        calls = []

        def fake_probe(*args, **kwargs):
            calls.append(kwargs)
            return {
                "ok": True,
                "passed": True,
                "mean_net": 0.002,
                "t_stat": 2.0,
                "hac_t_stat": 2.0,
                "trade_returns": [0.002] * 12,
            }

        n = 100
        with mock.patch.object(
            antifalsify.pp, "evaluate_naked_probe", side_effect=fake_probe,
        ), mock.patch.object(
            antifalsify, "_effect", return_value={"t_stat": 2.0},
        ):
            antifalsify.run_antifalsify_battery(
                [float(index % 11) for index in range(n)],
                [0.001] * n,
                side="low",
                factor_matrix={"atr_pct_14": [0.2] * n},
                candles=_candles(n),
                symbol="NG-USDT-SWAP",
                timeframe="5m",
                horizon=7,
                trade_direction="short",
                execution_mapping="pullback_limit",
            )

        self.assertGreaterEqual(len(calls), 12)
        for call in calls:
            self.assertTrue(call.get("candles"))
            self.assertEqual("NG-USDT-SWAP", call.get("symbol"))
            self.assertEqual("5m", call.get("timeframe"))
            self.assertEqual(-1, call.get("direction"))
            self.assertEqual((7,), call.get("horizons"))
            self.assertEqual("pullback_limit", call.get("execution_mapping"))

    def test_exact_event_competitor_keeps_admitted_identity(self):
        competitor_calls = []

        def fake_trial(candles, fwd, event, horizon, direction, mapping,
                       symbol=None, timeframe=None):
            return {
                "ok": True,
                "passed": True,
                "mean_net": 0.003,
                "hac_t_stat": 2.0,
                "n_independent_events": 12,
                "trade_returns": [0.003] * 12,
            }

        def fake_probe(*args, **kwargs):
            competitor_calls.append(kwargs)
            return {"mean_net": 0.001, "hac_t_stat": 1.0, "passed": False}

        n = 80
        mask = [(index % 5) == 0 for index in range(n)]
        with mock.patch.object(
            antifalsify.pp, "_evaluate_trial", side_effect=fake_trial,
        ), mock.patch.object(
            antifalsify.pp, "evaluate_naked_probe", side_effect=fake_probe,
        ):
            antifalsify.run_antifalsify_battery(
                mask,
                [0.0] * n,
                precomputed_event_mask=True,
                factor_matrix={"range_pct": [0.1] * n},
                candles=_candles(n),
                symbol="NG-USDT-SWAP",
                timeframe="5m",
                horizon=9,
                trade_direction="short",
                execution_mapping="pullback_limit",
            )

        self.assertEqual(1, len(competitor_calls))
        call = competitor_calls[0]
        self.assertEqual(-1, call["direction"])
        self.assertEqual((9,), call["horizons"])
        self.assertEqual("pullback_limit", call["execution_mapping"])
        self.assertEqual("NG-USDT-SWAP", call["symbol"])
        self.assertEqual("5m", call["timeframe"])
        self.assertTrue(call["candles"])

    def test_generic_leakage_audit_keeps_candidate_identity(self):
        calls = []

        def fake_probe(*args, **kwargs):
            calls.append(kwargs)
            return {"mean_net": 0.001, "passed": True}

        n = 70
        with mock.patch.object(
            heterogeneous_committee.probes,
            "evaluate_naked_probe",
            side_effect=fake_probe,
        ):
            result = heterogeneous_committee.run_leakage_auditor(
                [float(index % 7) for index in range(n)],
                [0.001] * n,
                side="low",
                candles=_candles(n),
                symbol="NG-USDT-SWAP",
                timeframe="5m",
                horizon=11,
                trade_direction="short",
                execution_mapping="pullback_limit",
            )

        self.assertTrue(result["passed"])
        self.assertEqual(2, len(calls))
        for call in calls:
            self.assertTrue(call["candles"])
            self.assertEqual("NG-USDT-SWAP", call["symbol"])
            self.assertEqual("5m", call["timeframe"])
            self.assertEqual(-1, call["direction"])
            self.assertEqual((11,), call["horizons"])
            self.assertEqual("pullback_limit", call["execution_mapping"])

    def test_leakage_audit_slices_candles_to_same_tail_as_factor_and_label(self):
        calls = []

        def fake_probe(*args, **kwargs):
            calls.append(kwargs)
            return {"mean_net": 0.001, "passed": True}

        candles = _candles(100)
        with mock.patch.object(
            heterogeneous_committee.probes,
            "evaluate_naked_probe",
            side_effect=fake_probe,
        ):
            heterogeneous_committee.run_leakage_auditor(
                [float(index) for index in range(60)],
                [0.001] * 60,
                candles=candles,
                symbol="ADA-USDT-SWAP",
                timeframe="5m",
                horizon=3,
                trade_direction="long",
                execution_mapping="next_bar_open",
            )
        self.assertEqual(2, len(calls))
        self.assertEqual(60, len(calls[0]["candles"]))
        self.assertEqual(candles[-60]["ts"], calls[0]["candles"][0]["ts"])


class StressAndCertificationTests(unittest.TestCase):
    def test_adverse_slippage_subtracts_three_bp_from_every_trade(self):
        returns = [0.01, -0.02, 0.0, -0.005, 0.003] * 4
        result = creation_stress_lite.red_team_attack(returns)
        attack = result["attacks"][0]
        expected_equity = 1.0
        for value in returns:
            expected_equity *= 1.0 + value - 0.0003
        self.assertEqual(-0.0003, attack["per_trade_return_delta"])
        self.assertEqual(len(returns), attack["n_shocked_trades"])
        self.assertTrue(math.isclose(
            expected_equity - 1.0,
            attack["total_return"],
            rel_tol=1e-12,
            abs_tol=1e-12,
        ))

    def test_required_quantoracle_failure_is_eliminated(self):
        candidate = {"factor": "ret_3", "returns": [0.001] * 20, "stats": {}}
        cert = {
            "ok": False,
            "source": "quantoracle",
            "error": "provider_unavailable",
            "certified": {},
        }
        with mock.patch.object(
            creation_blueprint.qo, "certify_factor_signal", return_value=cert,
        ):
            rows, survivors = creation_blueprint._certify_factors(
                [candidate], require_quantoracle=True,
            )
        self.assertFalse(survivors)
        self.assertTrue(rows[0]["rejected"])
        self.assertEqual("quantoracle_certification_failed", rows[0]["reject_reason"])
        self.assertFalse(rows[0]["certification_evidence"]["accepted"])

    def test_qualified_local_fallback_has_explicit_non_quantoracle_source(self):
        candidate = {
            "recipe_id": "recipe_preserved",
            "hypothesis_id": "hyp_preserved",
            "factor": "ret_3",
            "returns": [0.001] * 20,
            "stats": {},
        }
        cert = {
            "ok": True,
            "source": "local_fallback",
            "certified": {"sharpe_ratio": 1.2},
        }
        with mock.patch.object(
            creation_blueprint.qo, "certify_factor_signal", return_value=cert,
        ):
            rows, survivors = creation_blueprint._certify_factors(
                [candidate], require_quantoracle=True,
            )
        self.assertEqual(1, len(survivors))
        evidence = rows[0]["certification_evidence"]
        self.assertEqual("local_fallback", evidence["source"])
        self.assertEqual("local_reproducible_fallback", evidence["authority"])
        self.assertTrue(evidence["local_fallback"])
        self.assertFalse(evidence["quantoracle_claim_allowed"])
        self.assertEqual("recipe_preserved", rows[0]["recipe_id"])
        self.assertEqual("hyp_preserved", rows[0]["hypothesis_id"])

    def test_meta_required_certification_failure_stops_assembly_candidate(self):
        contract_id = "rc_quantoracle_required"
        payload = _admitted_payload(contract_id)
        stages = {
            "data": {"n_bars": 100},
            "meta": {
                "design_doc": {
                    "constraints": {},
                    "risk_bounds": {"require_quantoracle": True},
                    "hypotheses": [],
                },
            },
        }
        observed = []

        def reject_cert(candidates, max_daily_loss=0.05,
                        require_quantoracle=False):
            observed.append(require_quantoracle)
            row = dict(candidates[0])
            row.update({
                "rejected": True,
                "reject_reason": "quantoracle_certification_failed",
                "certification_evidence": {
                    "required_by_meta": True,
                    "cert_ok": False,
                    "accepted": False,
                    "source": "quantoracle",
                },
                "var_fuse": {"triggered": False},
            })
            return [row], []

        with tempfile.TemporaryDirectory(prefix="qiyu_qo_gate_") as tmp:
            with mock.patch.object(
                creation_blueprint, "_certify_factors", side_effect=reject_cert,
            ), mock.patch.object(
                creation_blueprint, "_creation_probes", return_value={},
            ):
                result = creation_blueprint._assemble_admitted_population(
                    symbol="ADA-USDT-SWAP",
                    timeframe="5m",
                    direction="long",
                    brief="require QuantOracle",
                    data={"n_bars": 100, "candles": _candles(100)},
                    stages=stages,
                    disc={"assembly_payload": [payload]},
                    compiled_contract={
                        "contract_id": contract_id,
                        "valid": True,
                    },
                    run_id="qo_required_test",
                    out_dir=tmp,
                )

        self.assertFalse(result["ok"])
        self.assertEqual([True], observed)
        attempt = result["stages"]["assembly_lineage"]["attempts"][0]
        self.assertEqual("quantoracle_required_certification", attempt["failed_gate"])
        self.assertFalse(attempt["certification_evidence"]["accepted"])


if __name__ == "__main__":
    unittest.main()
