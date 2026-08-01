# -*- coding: utf-8 -*-
"""Fail-closed instrument policy for strategy research and creation only."""
from __future__ import print_function

import os


# Human instruction 2026-08-01: ADA must not enter any new research mission.
# This does not disable or mutate already-approved live ADA strategies.
FORBIDDEN_RESEARCH_SYMBOLS = frozenset(("ADA-USDT-SWAP",))


def forbidden_symbols():
    """Return the immutable base ban plus optional stricter environment bans."""
    rows = set(FORBIDDEN_RESEARCH_SYMBOLS)
    extra = str(os.environ.get("QIYU_RESEARCH_FORBIDDEN_SYMBOLS") or "")
    rows.update(
        item.strip().upper() for item in extra.replace(";", ",").split(",")
        if item.strip()
    )
    return frozenset(rows)


def require_allowed(symbol):
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        raise ValueError("research symbol is required; implicit default instruments are forbidden")
    if normalized in forbidden_symbols():
        raise ValueError("research_symbol_forbidden:%s" % normalized)
    return normalized


def policy_probe():
    return {
        "ok": True,
        "scope": "new_strategy_research_only",
        "forbidden_symbols": sorted(forbidden_symbols()),
        "changes_existing_live_execution": False,
    }
