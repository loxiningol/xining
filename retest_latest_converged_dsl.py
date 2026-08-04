# -*- coding: utf-8 -*-
"""Re-evaluate the latest 3/3-converged DSL after deterministic engine fixes."""
from __future__ import print_function

import copy
import argparse
import json

import auto_trade_strategy_ecosystem as ecosystem


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC-USDT-SWAP")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--mutation-limit", type=int, default=8)
    args = parser.parse_args()
    symbol = args.symbol.upper(); timeframe = args.timeframe.lower()
    conn = ecosystem._db()
    try:
        row = conn.execute(
            "SELECT hypothesis_json FROM research_hypotheses "
            "WHERE provider='consensus' AND state='research_converged' "
            "AND hypothesis_json LIKE ? AND hypothesis_json LIKE ? "
            "ORDER BY created_at DESC LIMIT 1"
        , ('%%\"supported_instruments\": [\"%s\"]%%' % symbol,
           '%%\"timeframe\": \"%s\"%%' % timeframe)).fetchone()
    finally:
        conn.close()
    if not row:
        raise SystemExit("no converged DSL found")
    hypothesis = json.loads(row[0])
    definition = hypothesis.get("dsl") or {}
    if (definition.get("timeframe") != timeframe
            or symbol not in (definition.get("supported_instruments") or [])):
        raise SystemExit("latest converged DSL does not match requested mission")
    provider_results = [{
        "provider": "research_convergence_retest",
        "ok": True,
        "hypotheses": [{
            "kind": "dsl",
            "strategy_key": definition.get("key"),
            "dsl": definition,
            "rationale": "复用已完成3/3会审的原始DSL，修正量纲变异和零交易排序后重新确定性回测",
        }],
    }]
    empty = ecosystem._empty_metrics()
    baseline = {"0.009": copy.deepcopy(empty), "0.006": copy.deepcopy(empty)}
    outcome = ecosystem._process_dsl_hypotheses(
        {"strategy_key": "strategy_creation_%s_%s" %
                         (symbol.split("-")[0].lower(), timeframe),
         "symbol": symbol, "timeframe": timeframe},
        provider_results, baseline, parent_version_hash=None,
        promotion_allowed=True, mutation_limit=max(0, min(args.mutation_limit, 32)),
    )
    print(json.dumps({"ok": True, "source_hypothesis_id": hypothesis.get("hypothesis_id"),
                      "outcomes": outcome}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
