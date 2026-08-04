# -*- coding: utf-8 -*-
"""Hourly strategy rotation entrypoint.

Runs lifecycle metabolism then ecosystem self-evolution (niche competition,
micro-mutation queue, portfolio metrics, transfer proposals).  Vault revive
AI review is deferred to the dedicated evolution timer to keep the hourly
path bounded.  Does not grant passed_all and never touches stop-loss gates.
"""
from __future__ import print_function

import json

import auto_trade_strategy_lifecycle as lifecycle
import auto_trade_strategy_evolution as evolution


def run_once():
    life = lifecycle.run_once(preemptive=False)
    evo = evolution.run_once(
        skip_ai=True,  # hourly path: no revive AI spend; mutations/portfolio/niche only
        include_revive=False,
        include_mutations=True,
    )
    return {
        "ok": bool(life.get("ok")) and bool(evo.get("ok")),
        "lifecycle": life,
        "evolution": evo,
        "natural_language": (
            "%s | %s"
            % (life.get("natural_language") or "",
               evo.get("natural_language") or "")
        ).strip(" |"),
    }


if __name__ == "__main__":
    print(json.dumps(run_once(), ensure_ascii=False, indent=2, sort_keys=True))
