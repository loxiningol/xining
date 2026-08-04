# -*- coding: utf-8 -*-
"""Hourly maintenance tick (post creation-factory consolidation).

Breath controller, gene corridor, and environment admission are retired.
Niche map refresh is owned by the creation factory (weekly soft guidance).
"""
from __future__ import print_function

import json
import sys


def main():
    import auto_trade_capital_survival as survival
    import auto_trade_adaptive_week_observe as observe
    out = {
        "breath": {"ok": True, "disabled": True,
                   "reason": "breath_controller_removed"},
        "corridor": {"ok": True, "deleted": True,
                     "reason": "gene_corridor_deleted_use_creation_factory"},
        "survival_target": survival.build_report().get("target_point"),
        "week_observe": observe.build(days=7).get("goal"),
    }
    # Creation factory: soft niche refresh + ensure freeze + human pipeline
    try:
        import auto_trade_strategy_creation_factory as factory
        niche = factory.niche_guidance(force=False)
        out["niche"] = {
            "week": (factory._read(factory.NICHE_CACHE, {}) or {}).get("week"),
            "summary": (niche or {}).get("summary"),
        }
    except Exception as exc:
        out["factory_niche_error"] = str(exc)
    try:
        import auto_trade_human_confirm_pipeline as pipeline
        if not pipeline.mass_is_frozen():
            out["freeze"] = pipeline.freeze_mass_and_ed_probes()
        out["human_pipeline"] = pipeline.run_screen_tick(limit=12)
    except Exception as exc:
        out["human_pipeline_error"] = str(exc)
    observe.build(days=7)
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
