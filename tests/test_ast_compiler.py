# -*- coding: utf-8 -*-
"""P2 tests for AST schema + deterministic compiler."""
from __future__ import print_function

import unittest

from dual_engine_workflow_v2 import ast_compiler as ac
from dual_engine_workflow_v2 import candidate_materialization as cm
from dual_engine_workflow_v2 import research_discovery as rd
import auto_trade_strategy_dsl as dsl


def _series(n, start=0.0, step=1.0):
    return [float(start + i * step) for i in range(n)]


def _matrix(n=120):
    rsi = []
    for i in range(n):
        # Oscillate so quantiles and thresholds fire at different bars.
        rsi.append(20.0 + (i % 40))
    return {
        "rsi_14": rsi,
        "bb_lower_dist": [(-0.02 if (i % 17) < 3 else 0.01) for i in range(n)],
        "bb_upper_dist": [(0.02 if (i % 19) < 3 else -0.01) for i in range(n)],
        "bb_width": [0.01 + 0.001 * (i % 30) for i in range(n)],
        "bb_mid_reclaim": [(0.005 if (i % 11) == 0 else -0.002) for i in range(n)],
        "delta_rsi_14": [rsi[i] - rsi[i - 3] if i >= 3 else 0.0 for i in range(n)],
        "close_z_20": [((i % 20) - 10) / 5.0 for i in range(n)],
        "volume_z": [((i % 15) - 7) / 4.0 for i in range(n)],
        "asset_return_3": [0.01 if i % 9 == 0 else -0.002 for i in range(n)],
        "btc_return_3": [-0.01 if i % 9 == 0 else 0.001 for i in range(n)],
    }


class TestValidateAst(unittest.TestCase):
    def test_illegal_type_fails(self):
        pack = ac.validate_ast({"type": "python_eval", "code": "1"})
        self.assertFalse(pack["ok"])
        codes = [e.get("code") for e in pack.get("errors") or []]
        self.assertIn("unsupported_node", codes)

    def test_illegal_op_fails(self):
        pack = ac.validate_ast({
            "type": "compare", "feature": "rsi_14", "op": "xor", "value": 30,
        })
        self.assertFalse(pack["ok"])
        codes = [e.get("code") for e in pack.get("errors") or []]
        self.assertIn("unsupported_operator", codes)

    def test_sequence_too_deep_fails(self):
        steps = [
            {"event": {"type": "compare", "feature": "rsi_14", "op": "lt", "value": 30}},
            {"event": {"type": "compare", "feature": "rsi_14", "op": "gt", "value": 40}, "within_bars": 2},
            {"event": {"type": "compare", "feature": "rsi_14", "op": "gt", "value": 50}, "within_bars": 2},
            {"event": {"type": "compare", "feature": "rsi_14", "op": "gt", "value": 55}, "within_bars": 2},
        ]
        pack = ac.validate_ast({"type": "sequence", "steps": steps})
        self.assertFalse(pack["ok"])
        codes = [e.get("code") for e in pack.get("errors") or []]
        self.assertIn("sequence_too_deep", codes)


class TestCompileRoundtrip(unittest.TestCase):
    def test_compare_all_not_mask_and_dsl(self):
        ast = {
            "type": "all",
            "children": [
                {"type": "compare", "feature": "rsi_14", "op": "lt", "value": 35.0},
                {
                    "type": "not",
                    "child": {
                        "type": "compare", "feature": "bb_width", "op": "gt", "value": 0.05,
                    },
                },
            ],
        }
        matrix = _matrix()
        mask_pack = ac.compile_ast_to_mask(ast, matrix)
        self.assertTrue(mask_pack["ok"], mask_pack)
        self.assertTrue(any(mask_pack["mask"]))
        dsl_pack = ac.compile_ast_to_dsl(ast)
        self.assertTrue(dsl_pack["ok"], dsl_pack)
        self.assertTrue(dsl_pack["formal_ok"], dsl_pack.get("reasons"))
        strategy = {
            "schema": "qiyu_strategy_dsl_v1",
            "key": "ast_roundtrip_cmp",
            "name": "ast_roundtrip",
            "direction": "long",
            "timeframe": "15m",
            "supported_instruments": ["BTC-USDT-SWAP"],
            "max_hold_bars": 16,
            "entry": dsl_pack["entry_tree"],
            "exit": {
                "id": "x_hold",
                "exit_op": "max_hold_only",
            },
            "protective_stop_pct": 0.005,
            "execution_leverage": 20,
            "execution_mapping": "next_bar_open",
        }
        validated = dsl.validate_strategy(strategy)
        self.assertEqual(validated["key"], "ast_roundtrip_cmp")

    def test_quantile_roundtrip(self):
        ast = {
            "type": "all",
            "children": [
                {"type": "quantile", "feature": "rsi_14", "side": "low", "q": 0.80},
                {"type": "quantile", "feature": "bb_lower_dist", "side": "low", "q": 0.80},
            ],
        }
        mask_pack = ac.compile_ast_to_mask(ast, _matrix(300))
        self.assertTrue(mask_pack["ok"], mask_pack)
        dsl_pack = ac.compile_ast_to_dsl(ast)
        self.assertTrue(dsl_pack["ok"])
        self.assertTrue(dsl_pack["formal_ok"], dsl_pack.get("reasons"))
        self.assertIn("all", dsl_pack["entry_tree"])


class TestDeltaSlope(unittest.TestCase):
    def test_delta_bars3_causal(self):
        n = 20
        base = _series(n, 10, 1)
        # Precomputed delta = base[i]-base[i-3]
        delta = [None] * n
        for i in range(n):
            delta[i] = (base[i] - base[i - 3]) if i >= 3 else None
        matrix = {"rsi_14": base, "delta_rsi_14": delta}
        ast = {
            "type": "delta",
            "feature": "rsi_14",
            "bars": 3,
            "op": "gt",
            "value": 2.5,
        }
        pack = ac.compile_ast_to_mask(ast, matrix)
        self.assertTrue(pack["ok"], pack)
        mask = pack["mask"]
        # At i=5: delta=3 > 2.5 → True; uses only past values via precomputed col.
        self.assertTrue(mask[5])
        # At i=2: no history → False / not True
        self.assertFalse(bool(mask[2]))


class TestSequenceCausal(unittest.TestCase):
    def test_a_then_b_within_n_no_future_leak(self):
        # A true only at index 5; B true only at index 8.
        n = 15
        a = [False] * n
        b = [False] * n
        a[5] = True
        b[8] = True
        # Encode via compare on features.
        fa = [1.0 if x else 0.0 for x in a]
        fb = [1.0 if x else 0.0 for x in b]
        matrix = {"rsi_14": fa, "bb_mid_reclaim": fb}
        ast = {
            "type": "sequence",
            "steps": [
                {"event": {"type": "compare", "feature": "rsi_14", "op": "gt", "value": 0.5}},
                {
                    "event": {
                        "type": "compare", "feature": "bb_mid_reclaim", "op": "gt", "value": 0.5,
                    },
                    "within_bars": 4,
                },
            ],
        }
        pack = ac.compile_ast_to_mask(ast, matrix)
        self.assertTrue(pack["ok"], pack)
        mask = pack["mask"]
        # Sequence fires when B is true NOW and A was true in prior within_bars.
        self.assertTrue(mask[8])
        # At the moment A is true, B is false → sequence false (no peeking ahead to B@8).
        self.assertFalse(mask[5])
        # Before A occurred, cannot fire.
        self.assertFalse(mask[4])

    def test_was_true_within_dsl_causal(self):
        import pandas as pd
        frame = pd.DataFrame({
            "rsi14": [10.0, 10.0, 40.0, 10.0, 10.0],
        })
        node = {
            "was_true_within": {
                "bars": 2,
                "expr": {
                    "id": "e1",
                    "left": {"feature": "rsi14"},
                    "op": "gt",
                    "right": {"value": 30},
                },
            }
        }
        # index 3: lookback 1..2 → indices 1,2; index 2 had rsi=40 → True
        ok, _ = dsl.evaluate_expression(frame, 3, node)
        self.assertTrue(ok)
        # index 2: lookback indices 0,1 — both 10 → False (does not use current 40)
        ok2, _ = dsl.evaluate_expression(frame, 2, node)
        self.assertFalse(ok2)


class TestExcludeRelative(unittest.TestCase):
    def test_exclude_equals_not(self):
        ast_ex = {
            "type": "exclude",
            "condition": {"type": "compare", "feature": "rsi_14", "op": "gt", "value": 50},
        }
        ast_not = {
            "type": "not",
            "child": {"type": "compare", "feature": "rsi_14", "op": "gt", "value": 50},
        }
        matrix = _matrix(40)
        m1 = ac.compile_ast_to_mask(ast_ex, matrix)["mask"]
        m2 = ac.compile_ast_to_mask(ast_not, matrix)["mask"]
        self.assertEqual(m1, m2)

    def test_relative_missing_feature_code(self):
        ast = {
            "type": "relative",
            "left_feature": "asset_return_3",
            "op": "gt",
            "right_feature": "btc_return_3",
            "offset": 0.0,
        }
        pack = ac.compile_ast_to_mask(ast, {"rsi_14": [1, 2, 3]})
        self.assertFalse(pack["ok"])
        report = pack.get("compile_report") or {}
        # Either validate missing_feature or relative_feature_missing at compile.
        blob = str(report)
        self.assertTrue(
            "missing_feature" in blob or "relative_feature_missing" in blob,
            report,
        )


class TestMaterializationFingerprints(unittest.TestCase):
    def test_force_skeleton_diverse_asts(self):
        rows = cm.force_skeleton_hypotheses(direction="long", families=["exhaustion"])
        hashes = []
        for row in rows:
            if row.get("event_ast"):
                hashes.append(ac.ast_fingerprint(row["event_ast"]))
        unique = sorted(set(hashes))
        self.assertGreaterEqual(len(unique), 4, unique)
        types = sorted({
            row.get("representation_type") for row in rows if row.get("event_ast")
        })
        self.assertGreaterEqual(len(types), 4, types)


class TestFormalDsl(unittest.TestCase):
    def test_rank_ast_formal_ok_validates(self):
        templates = ac.representation_asts_for_direction("long")
        rank = next(t for t in templates if t["representation_type"] == "rank")
        dsl_pack = ac.compile_ast_to_dsl(rank["ast"])
        self.assertTrue(dsl_pack["ok"], dsl_pack)
        self.assertTrue(dsl_pack["formal_ok"], dsl_pack.get("reasons"))
        entry = dsl_pack["entry_tree"]
        # Walk entry tree shape.
        self.assertIn("all", entry)
        # Depth-check via was_true_within-free validate of a stub strategy if possible.
        try:
            dsl._walk(entry, 0)  # type: ignore[attr-defined]
        except AttributeError:
            pass
        except Exception as exc:
            self.fail("entry tree walk failed: %s" % exc)

    def test_assembly_recipe_v2_carries_ast(self):
        hyp = {
            "hypothesis_id": "h_ast",
            "mechanism_id": "m1",
            "family": "exhaustion",
            "event_ast": {
                "type": "compare", "feature": "rsi_14", "op": "lt", "value": 30,
            },
            "event_ast_hash": "ast_deadbeef",
        }
        best = {
            "event_id": "ast_1",
            "event_kind": "ast_compiled",
            "event_logic": "ast",
            "terms": [{"factor": "rsi_14", "node_type": "compare"}],
            "trade_direction": "long",
            "horizon_bars": 16,
            "execution_mapping": "next_bar_open",
            "primary_cost_scenario": "base",
            "primary_cost_per_trade": 0.001,
            "statistical_returns_are_post_cost": True,
            "exit_policy": {"mode": "fixed_horizon_close_v1", "allow_early_take_profit": False},
            "protective_stop_policy": {"mode": "intrabar_fixed_pct_v1", "price_pct": 0.005},
            "protective_stop_evaluated": True,
            "execution_leverage": 20,
            "statistical_return_basis": "full_size_leveraged_after_cost_v1",
            "event_ast": hyp["event_ast"],
            "event_ast_hash": hyp["event_ast_hash"],
            "event_ast_formal_ok": True,
        }
        recipe = rd.assembly_recipe(hyp, best, {"contract_id": "c1"})
        self.assertEqual(recipe["schema"], "qiyu_admitted_probe_recipe_v2")
        self.assertIn("event_ast", recipe)
        self.assertEqual(recipe["event_ast_hash"], "ast_deadbeef")
        ok, expected = rd.verify_assembly_recipe(recipe)
        self.assertTrue(ok, expected)


if __name__ == "__main__":
    unittest.main()
