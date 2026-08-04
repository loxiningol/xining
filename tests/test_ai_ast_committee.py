# -*- coding: utf-8 -*-
"""P3 tests for multi-AI early AST committee (skip_llm / deterministic path)."""
from __future__ import print_function

import unittest

from dual_engine_workflow_v2 import ai_ast_committee as p3
from dual_engine_workflow_v2 import ast_compiler as ac
from dual_engine_workflow_v2 import quality_optimization as qopt


class TestCommitteeApis(unittest.TestCase):
    def test_generate_mechanisms_fallback(self):
        pack = p3.generate_mechanisms(
            brief="RSI+BB exhaustion",
            symbol="BTC-USDT-SWAP",
            timeframe="15m",
            direction="long",
            skip_llm=True,
        )
        self.assertTrue(pack["ok"])
        self.assertGreaterEqual(len(pack.get("mechanisms") or []), 2)
        for row in pack["mechanisms"]:
            self.assertIn("anon_id", row)
            self.assertTrue(row.get("mechanism_id"))
            self.assertNotIn("_provider", row)

    def test_generate_asts_six_per_mechanism(self):
        mech = p3.generate_mechanisms(skip_llm=True)
        pack = p3.generate_asts(
            mechanisms=mech.get("mechanisms"),
            direction="long",
            skip_llm=True,
        )
        self.assertTrue(pack["ok"])
        by_mech = {}
        for row in pack.get("asts") or []:
            self.assertTrue(row.get("compile_ok"))
            self.assertTrue(row.get("event_ast"))
            self.assertTrue(row.get("event_ast_hash"))
            mid = row["mechanism_id"]
            by_mech.setdefault(mid, set()).add(row["representation_type"])
            validated = ac.validate_ast(row["event_ast"])
            self.assertTrue(validated["ok"], validated)
        for mid, types in by_mech.items():
            self.assertEqual(
                set(types), set(p3.REPRESENTATION_TYPES),
                "mech %s missing types: %s" % (mid, set(p3.REPRESENTATION_TYPES) - types),
            )

    def test_audit_diversity_structured(self):
        anon = [
            {
                "anon_id": "a1",
                "family": "exhaustion",
                "representation_type": "threshold",
                "factor_hints": ["rsi_14"],
                "event_ast_hash": "h1",
                "compile_ok": True,
                "formal_ok": True,
            }
        ]
        pack = p3.audit_diversity(anonymous_candidates=anon, skip_llm=True)
        self.assertTrue(pack["ok"])
        self.assertIn("sequence", pack.get("missing_representation_types") or [])
        specs = pack.get("supplement_specs") or []
        self.assertTrue(specs)
        for row in specs:
            self.assertIn("representation_type", row)
            self.assertIn("factor_hints", row)

    def test_diagnose_never_declares_dead(self):
        pack = p3.diagnose_failures(
            failure_summary={"dominant_codes": [qopt.LOW_WIN_RATE]},
            skip_llm=True,
        )
        self.assertTrue(pack["ok"])
        diag = pack["diagnosis"]
        self.assertFalse(diag.get("may_declare_direction_dead"))
        for item in qopt.FORBIDDEN_ALWAYS:
            self.assertIn(item, diag.get("forbidden_modifications") or [])
        self.assertTrue(diag.get("repair_wave_plan"))

    def test_repair_wave_uses_quality_path(self):
        diag = p3.diagnose_failures(skip_llm=True)
        repair = p3.generate_repair_wave(diagnosis=diag, direction="long", limit=8)
        self.assertTrue(repair["ok"])
        self.assertGreaterEqual(repair.get("n") or 0, 1)
        for row in repair.get("hypotheses") or []:
            self.assertEqual(row.get("source"), "p3_repair_wave")
            mods = row.get("forbidden_modifications") or []
            self.assertTrue(any("stop" in str(x).lower() or "leverage" in str(x).lower()
                                or x in qopt.FORBIDDEN_ALWAYS for x in mods)
                            or set(qopt.FORBIDDEN_ALWAYS).issubset(set(mods)))


class TestCreationCommittee(unittest.TestCase):
    def test_run_creation_committee_skip_llm(self):
        pack = p3.run_creation_committee(
            brief="structured RSI+BB exhaustion long",
            symbol="BTC-USDT-SWAP",
            timeframe="15m",
            direction="long",
            skip_llm=True,
            run_id="test_p3",
        )
        self.assertTrue(pack["ok"])
        self.assertGreaterEqual(int(pack.get("n_hypotheses") or 0), 6)
        self.assertGreaterEqual(int(pack.get("unique_ast_hash_n") or 0), 4)
        rtypes = set(pack.get("representation_types") or [])
        self.assertGreaterEqual(len(rtypes), 4)
        for h in pack.get("hypotheses") or []:
            if h.get("event_ast"):
                self.assertTrue(h.get("event_ast_hash"))
                self.assertIn(h.get("source"), (
                    "p3_ai_ast_committee", "p3_repair_wave",
                ))
        # Round-2 fingerprints must be anonymous (no provider tags).
        for fp in pack.get("anonymous_fingerprints") or []:
            self.assertNotIn("_provider", fp)
            self.assertNotIn("source", fp)
            self.assertIn("anon_id", fp)

    def test_free_code_ast_rejected(self):
        pack = p3._validate_attach_ast(
            {"type": "python_eval", "code": "print(1)"},
            "bad",
            "threshold",
        )
        self.assertFalse(pack.get("ok"))


if __name__ == "__main__":
    unittest.main()
