# -*- coding: utf-8 -*-
"""Pretest quality pipeline — contract → asserts → prune/RESET gate.

Runs BEFORE L0 / L1 / Gate2. If translation drifted, return SHIT_TRANSLATION
and refuse additive optimize. Never decorate garbage.
"""
from __future__ import print_function

from . import invariants_contract as inv
from . import sanity_assert as sanity


def run_pretest_quality(pack, direction=None, search_roots=None):
    """Full pretest. pass=False ⇒ do not enter funnel testing."""
    direction = direction or ((pack.get("meta") or {}).get("direction") or "long")
    contract, src = inv.resolve_contract_for_pack(pack, search_roots=search_roots)
    contract_report = inv.check_invariants(pack, contract=contract, direction=direction)
    if contract is None:
        return {
            "pass": False,
            "stage": "pretest_quality",
            "rejected_at": "invariants_contract",
            "quality": "SHIT_TRANSLATION",
            "verdict": "RESET_REQUIRED",
            "reason": "no_invariants_contract",
            "invariants": contract_report,
            "sanity": None,
            "message_zh": "无契约：禁止落码/优化。先写不变量契约与断言。",
        }

    if not contract_report.get("pass"):
        return {
            "pass": False,
            "stage": "pretest_quality",
            "rejected_at": "invariants_contract",
            "quality": "SHIT_TRANSLATION",
            "verdict": "RESET_REQUIRED",
            "reason": "invariants_violated",
            "invariants": contract_report,
            "sanity": None,
            "message_zh": "契约违背：翻译已偏离，禁止优化，必须清零重译。",
            "violations": list(contract_report.get("violations") or []),
        }

    sanity_report = sanity.run_sanity_asserts(
        pack, direction=direction, contract=contract,
    )
    if not sanity_report.get("pass"):
        return {
            "pass": False,
            "stage": "pretest_quality",
            "rejected_at": "sanity_assert",
            "quality": "SHIT_TRANSLATION",
            "verdict": "RESET_REQUIRED",
            "reason": "sanity_assert_failed",
            "invariants": contract_report,
            "sanity": sanity_report,
            "message_zh": "语义断言失败：语法对、语义崩。禁止屎上雕花。",
            "failures": list(sanity_report.get("failures") or []),
        }

    return {
        "pass": True,
        "stage": "pretest_quality",
        "rejected_at": None,
        "quality": "ok",
        "verdict": "READY_FOR_FUNNEL",
        "reason": None,
        "invariants": contract_report,
        "sanity": sanity_report,
        "message_zh": "契约+断言通过，允许进入 L0/L1。",
        "contract_source": src,
    }
