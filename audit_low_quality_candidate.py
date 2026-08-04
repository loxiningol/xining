# -*- coding: utf-8 -*-
"""Read-only regression audit for the rejected 25.40% BTC15 candidate."""
from __future__ import print_function

import copy
import json
import sqlite3

import auto_trade_strategy_dsl as dsl
import auto_trade_strategy_ecosystem as ecosystem


DB = "/root/auto_trade/strategy_ecosystem.db"
CANDIDATE_HASH = "1f5bfd27a4c7975784b9f12d6b0cf43dde80d1acf71d6f23cd814650cd83a738"


def add_correct_exit_roles(definition):
    value = copy.deepcopy(definition)
    for _path, leaf in dsl._leaf_paths(value["exit"]):
        if (leaf.get("left") or {}).get("feature") == "close" and str(
                (leaf.get("right") or {}).get("feature") or "").startswith("ema"):
            leaf["role"] = "invalidation"
        else:
            leaf["role"] = "take_profit"
    return value


def main():
    conn = sqlite3.connect(DB)
    candidate_row = conn.execute(
        "SELECT candidate_json,evidence_json FROM candidates WHERE candidate_hash=?",
        (CANDIDATE_HASH,),
    ).fetchone()
    proposal_row = conn.execute(
        "SELECT hypothesis_json FROM research_hypotheses "
        "WHERE provider='qwen' AND strategy_key='btc15_h1_uptrend_cci_pullback_long_v1' "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    candidate = json.loads(candidate_row[0])
    evidence = json.loads(candidate_row[1])
    proposal = json.loads(proposal_row[0])["dsl"]
    frame = ecosystem._load_research_frame("BTC-USDT-SWAP", "15m")
    stored_definition = candidate["dsl"]
    typed_stored = add_correct_exit_roles(stored_definition)
    typed_proposal = add_correct_exit_roles(proposal)
    print(json.dumps({
        "candidate_hash": CANDIDATE_HASH,
        "reported_backtest": (((evidence.get("runs") or {}).get("candidate") or {})
                              .get("0.009") or {}),
        "reviewed_original_executable_hash": dsl.executable_hash(proposal),
        "actually_tested_executable_hash": candidate.get("executable_hash"),
        "same_executable": dsl.executable_hash(proposal) == candidate.get("executable_hash"),
        "legacy_semantic_audit": ecosystem._dsl_semantic_audit(stored_definition),
        "typed_stored_entry_edge_screen": ecosystem._dsl_entry_edge_screen(
            typed_stored, frame),
        "typed_reviewed_original_entry_edge_screen": ecosystem._dsl_entry_edge_screen(
            typed_proposal, frame),
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
