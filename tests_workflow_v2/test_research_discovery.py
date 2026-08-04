# -*- coding: utf-8 -*-
"""Tests for research discovery modules (real math, not name-only)."""
from __future__ import print_function

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ["VECTOR_ROOT"] = os.path.join(ROOT, "strategies", "_scratch")

from dual_engine_workflow_v2 import antifalsify
from dual_engine_workflow_v2 import edge_friction as efr
from dual_engine_workflow_v2 import map_elites_archive as qd
from dual_engine_workflow_v2 import mechanism_graph as mg
from dual_engine_workflow_v2 import multiple_testing as mt
from dual_engine_workflow_v2 import phenomenon_scanner as ph
from dual_engine_workflow_v2 import probe_protocol as pp
from dual_engine_workflow_v2 import research_discovery as rd
from dual_engine_workflow_v2 import research_ledger as ledger


class TestResearchDiscovery(unittest.TestCase):
    def test_mechanism_completeness_gate(self):
        bad = {"mechanism_id": "x"}
        self.assertFalse(mg.completeness_check(bad)["passed"])
        good = mg.load_graph()[0]
        self.assertTrue(mg.completeness_check(good)["passed"])

    def test_volume_anomaly_direction_has_exact_formal_factors(self):
        selected = mg.select_for_brief(
            "成交量异动 + 放量突破，确认方向延续而非反转",
            symbol="BTC-USDT-SWAP", timeframe="5m", limit=12,
        )
        rows = [
            row for row in selected["mechanisms"]
            if row.get("family") == "volume_anomaly_breakout"
        ]
        self.assertTrue(rows)
        self.assertEqual(
            ["volume_z", "close_z_20"], rows[0]["factor_hints"],
        )
        self.assertEqual(
            ["volume_z", "close_z_20"],
            rows[0]["required_factor_intersection"],
        )
        self.assertEqual(
            {"volume_z": "high", "close_z_20": "trade_direction"},
            rows[0]["factor_side_constraints"],
        )

    def test_volume_anomaly_probe_never_uses_low_volume_or_wrong_price_side(self):
        matrix = {
            "volume_z": [float(i % 20) for i in range(400)],
            "close_z_20": [float((i % 31) - 15) for i in range(400)],
        }
        base = {
            "factor_hints": ["volume_z", "close_z_20"],
            "required_factor_intersection": ["volume_z", "close_z_20"],
            "factor_side_constraints": {
                "volume_z": "high", "close_z_20": "trade_direction",
            },
            "trade_direction_locked": True,
        }
        long_specs, _ = rd.probes._candidate_events(
            dict(base, predicted_direction="long"), matrix, max_specs=18,
        )
        short_specs, _ = rd.probes._candidate_events(
            dict(base, predicted_direction="short"), matrix, max_specs=18,
        )
        self.assertEqual(4, len(long_specs))  # INTERSECTION_QUANTILES only
        self.assertEqual(4, len(short_specs))
        for spec in long_specs:
            self.assertEqual("mechanism_intersection", spec["kind"])
            self.assertEqual(
                [("volume_z", "high"), ("close_z_20", "high")],
                [(term["factor"], term["side"]) for term in spec["terms"]],
            )
        for spec in short_specs:
            self.assertEqual(
                [("volume_z", "high"), ("close_z_20", "low")],
                [(term["factor"], term["side"]) for term in spec["terms"]],
            )

    def test_enforced_family_owns_probe_budget_before_controls(self):
        # Population focus is enforced in build_hypothesis_population; the old
        # _schedule_probe_population helper was removed.  Assert brief/tree lock.
        pack = rd.build_hypothesis_population(
            brief="成交量异动突破",
            symbol="BTC-USDT-SWAP",
            timeframe="15m",
            factor_matrix={
                "volume_z": [0.0] * 50,
                "close_z_20": [0.0] * 50,
            },
            fwd_returns=[0.0] * 50,
            max_mechanisms=4,
            max_phenomena=4,
            research_contract={"family_hints": ["volume_anomaly_breakout"]},
        )
        self.assertTrue(pack.get("human_direction_focus_enforced"))
        families = set(
            str(row.get("family") or "") for row in (pack.get("hypotheses") or [])
        )
        # Unrelated Donchian-only leaves should not dominate a volume brief.
        self.assertNotIn("donchian_trend_break", families)

    def test_phenomenon_scan_finds_shift(self):
        n = 200
        factor = [float(i) for i in range(n)]
        fwd = [0.01 if i > 160 else -0.002 for i in range(n)]
        out = ph.scan_conditional_shifts({"f": factor}, fwd, min_abs_t=1.5)
        self.assertTrue(out["ok"])
        self.assertGreater(out["n"], 0)

    def test_naked_probe_hard_rule(self):
        n = 150
        factor = [float(i % 20) for i in range(n)]
        fwd = [0.01 if factor[i] >= 16 else -0.001 for i in range(n)]
        out = pp.evaluate_naked_probe(factor, fwd, side="high")
        self.assertTrue(out["ok"])
        self.assertIn("hard_rule_zh", out)

    def test_efr_rejects_tiny_edge(self):
        out = efr.edge_to_friction(0.0001, min_efr=1.5)
        self.assertFalse(out["passed"])
        out2 = efr.edge_to_friction(0.01, min_efr=1.5)
        self.assertTrue(out2["passed"])

    def test_dsr_runs(self):
        rets = [0.01] * 40
        out = mt.deflated_sharpe_ratio(rets, n_trials=50)
        self.assertTrue(out["ok"])
        self.assertIn("dsr", out)

    def test_map_elites_cells(self):
        archive = {}
        h = {
            "hypothesis_id": "H1",
            "family": "mean_reversion",
            "horizon": "5m-30m",
            "predicted_direction": "reversal",
            "factor_hints": ["close_z_20"],
        }
        archive, row, _ = qd.upsert(
            archive, h,
            probe_best={"passed": True, "mean_net": 0.002, "t_stat": 2.0, "n_hits": 20},
            antifalsify={"passed": True, "support_n": 3, "oppose_n": 0},
            efr={"efr": 2.0},
            n_bars=500,
        )
        self.assertEqual(len(archive), 1)
        self.assertIn("cell", row["descriptor"])

    def test_ledger_append_and_budget(self):
        rid = ledger.new_run_id("test")
        ledger.append_event({"event_type": "hypothesis", "x": 1}, run_id=rid)
        ledger.append_event({"event_type": "probe", "x": 2}, run_id=rid)
        b = ledger.effective_trial_budget(run_id=rid)
        self.assertGreaterEqual(b["effective_trials"], 2.0)

    def test_discovery_orchestrator_smoke(self):
        n = 180
        matrix = {
            "close_z_20": [float((i % 25) - 12) for i in range(n)],
            "range_pct": [0.01 + 0.001 * (i % 7) for i in range(n)],
            "atr_pct_14": [0.012 + 0.001 * (i % 5) for i in range(n)],
            "ret_12": [0.001 * ((i % 9) - 4) for i in range(n)],
            "volume_z": [float((i % 11) - 5) for i in range(n)],
            "abs_ret_1": [0.002 * (i % 4) for i in range(n)],
        }
        # plant a workable high-z edge
        fwd = []
        for i in range(n):
            fwd.append(0.008 if matrix["close_z_20"][i] >= 10 else -0.001)
        out = rd.run_discovery(
            "ADA-USDT-SWAP", "5m", brief="库存回归",
            factor_matrix=matrix, fwd_returns=fwd,
            max_hypotheses_probe=8, min_efr=1.0,
        )
        self.assertIn("schema", out)
        self.assertIn("stages", out)
        self.assertIn("trial_budget", out["stages"])


if __name__ == "__main__":
    unittest.main()
