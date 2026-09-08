# -*- coding: utf-8 -*-
"""Invent seed ↔ geometry gate ↔ lock consistency (deploy / CI gate)."""
from __future__ import print_function

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dual_engine_workflow_v2 import invent_contract_audit as ica
from dual_engine_workflow_v2.timing_stage import rr_geometry_bounds_ok


def test_audit_clean_repo():
    pack = ica.run_audit(include_live_inherit=False)
    assert pack["ok"], json.dumps(pack.get("findings") or pack, ensure_ascii=False)[:800]


def test_literal_out_of_bounds_detected():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bad_seed.py"
        p.write_text('SEED = {"xwin": 12, "atr": 2.0}\n', encoding="utf-8")
        hits = ica.audit_literal_bounds([p])
        assert any(h.get("code") == "literal_out_of_bounds" for h in hits), hits


def test_order_reversed_detected():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "loop.py"
        p.write_text(
            "ok_rr, err_rr = rr_geometry_bounds_ok(recipe)\n"
            "# Force lane seed identity until first lock\n"
            "recipe['xwin'] = seed0['xwin']\n",
            encoding="utf-8",
        )
        hits = ica.audit_loop_order(p)
        assert any(h.get("code") == "seed_force_after_geometry_gate" for h in hits), hits


def test_locked_seed_outside_gate():
    hits = ica.audit_locked_seed_vs_gate({"primary": {"xwin": 12, "atr": 2.18}})
    assert any(h.get("code") == "locked_seed_outside_gate" for h in hits), hits


def test_geometry_bounds_helper():
    ok, _err = rr_geometry_bounds_ok({"xwin": 8.0, "atr": 2.18})
    assert ok
    bad, err = rr_geometry_bounds_ok({"xwin": 12, "atr": 2.18})
    assert not bad
    assert "xwin" in (err or "")


def test_route_reseeds_inside_gate():
    hits = ica.audit_module_seeds()
    assert not hits, hits


def main():
    tests = [
        test_audit_clean_repo,
        test_literal_out_of_bounds_detected,
        test_order_reversed_detected,
        test_locked_seed_outside_gate,
        test_geometry_bounds_helper,
        test_route_reseeds_inside_gate,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as exc:
            failed += 1
            print("FAIL", fn.__name__, exc)
    if failed:
        raise SystemExit(1)
    print("OK invent_contract_audit %d tests" % len(tests))


if __name__ == "__main__":
    main()
