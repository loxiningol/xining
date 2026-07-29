#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Archive session_liq_engulf as limit_reached; validate vol-squeeze pack; smoke features."""
from __future__ import print_function

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path("/root")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def archive_session_liq_kb():
    from dual_engine_workflow_v2 import failure_kb as fkb
    from dual_engine_workflow_v2.config import _now, _atomic
    from dual_engine_workflow_v2 import step_a_config as sc

    kb_path = Path(sc.FAILURE_KB_PATH)
    shutil.copy2(str(kb_path), str(kb_path) + ".bak_before_vol_squeeze_%s" % time.strftime("%Y%m%d_%H%M%S"))
    kb = fkb.load_failure_kb()
    kb.setdefault("blocked_families", [])
    kb.setdefault("blocked_paths", [])
    kb.setdefault("lessons", [])
    kb.setdefault("records", [])
    kb.setdefault("limit_reached_families", [])

    limit_bases = [
        "session_liq_engulf_displace_matrix",
        "session_liq_engulf_matrix",
    ]
    for fam in limit_bases:
        if fam not in kb["limit_reached_families"]:
            kb["limit_reached_families"].append(fam)
        if fam not in kb["blocked_families"]:
            kb["blocked_families"].append(fam)
        path = "family|%s" % fam
        if path not in kb["blocked_paths"]:
            kb["blocked_paths"].append(path)
        path2 = "limit_reached|%s" % fam
        if path2 not in kb["blocked_paths"]:
            kb["blocked_paths"].append(path2)

    # Also pin known adN descendants already seen in KB so AI context stays explicit.
    for fam in list(kb.get("blocked_families") or []):
        if str(fam).startswith("session_liq_engulf"):
            if fam not in kb["limit_reached_families"]:
                kb["limit_reached_families"].append(fam)

    lesson = (
        "LIMIT_REACHED archive 2026-07-30: session_liq_engulf_* (incl. displace_matrix "
        "and all _adN) exhausted on ETH 15m — scale-out collapses payoff; no-scale-out "
        "fails lottery/w5 under vol_z>=1.8 sparsity. FORBIDDEN: further micro-perturbation, "
        "family_adN resume, or Gate2 floor lowering. Pivot to session_vol_squeeze_expansion_v1."
    )
    if lesson not in kb["lessons"]:
        kb["lessons"].append(lesson)

    tid = "archive_session_liq_limit_reached_%s" % time.strftime("%Y%m%d_%H%M%S")
    rec = fkb.build_failure_record(
        task_id=tid,
        mechanism_spec={
            "mechanism_family": "session_liq_engulf_displace_matrix",
            "mechanism_name": "session_liq_engulf_displace_matrix",
            "mechanism_id": "mech_session_liq_engulf_displace_matrix_eth15m_v1b",
            "market_inefficiency": "archived_limit_reached",
            "counterparty_source": "n/a",
            "entry_logic": "archived",
            "non_negotiable_rules": ["limit_reached_no_further_micro_perturbation"],
        },
        stage="auto_driver_limit_archive",
        failed_tests=["limit_reached", "gate2_lottery_w5_theoretical_ceiling"],
        failure_reason=(
            "auto_driver CONVERGED_NO_IMPROVEMENT on session_liq_engulf lineage; "
            "best Gate2 fingerprint pay≈2.482 w5≈0.893 lottery; scale-out destroyed payoff"
        ),
        is_engineering=False,
        is_cost=False,
        is_data=False,
        is_mechanism_absent=True,
        mechanism_drift=False,
        repair_count=0,
        final_verdict="limit_reached",
        reusable_lessons=[lesson],
        blocked_paths=[
            "family|session_liq_engulf_displace_matrix",
            "family|session_liq_engulf_matrix",
            "limit_reached|session_liq_engulf_displace_matrix",
        ],
        counterexamples=[],
        exploration_mode="A",
        symbol="ETH-USDT-SWAP",
        timeframe="15m",
        gate_results={"archived_by": "session_vol_squeeze_pivot"},
    )
    fkb.save_failure_record(rec)

    # Re-load and ensure limit_reached_families persisted (save_failure_record may
    # not know the new key — write explicitly).
    kb2 = fkb.load_failure_kb()
    kb2.setdefault("limit_reached_families", [])
    for fam in limit_bases:
        if fam not in kb2["limit_reached_families"]:
            kb2["limit_reached_families"].append(fam)
        if fam not in kb2["blocked_families"]:
            kb2["blocked_families"].append(fam)
    if lesson not in kb2["lessons"]:
        kb2["lessons"].append(lesson)
    kb2["updated_at"] = _now()
    _atomic(kb_path, kb2)

    blocked, why = fkb.path_is_blocked(
        "family|session_liq_engulf_displace_matrix_ad99",
        family="session_liq_engulf_displace_matrix_ad99",
    )
    print("archive_ok limit_reached_families=", kb2.get("limit_reached_families"))
    print("ad99_blocked", blocked, why)
    assert blocked, "prefix limit_reached block failed"
    return tid


def validate_pack_and_features():
    import auto_trade_strategy_dsl as dsl_mod
    import auto_trade_dual_engine_factory as dual
    import auto_trade_human_confirm_pipeline as pipe

    pack = json.loads(Path("/root/strategy_session_vol_squeeze_v1.json").read_text(encoding="utf-8"))
    dsl = pack.get("dsl_long") or pack.get("dsl")
    dsl_mod.validate_strategy(dsl)
    print("dsl_validate_ok", dsl.get("key"))

    pipe._FRAME_CACHE.clear()
    fr = dual._frame("ETH-USDT-SWAP", "15m")
    need = [
        "hour_utc", "asia_high", "asia_low", "asia_mid", "asia_range",
        "asia_range_atr_ratio", "vol_z20", "atr14",
    ]
    miss = [c for c in need if c not in fr.columns]
    assert not miss, "missing features: %s" % miss
    print(
        "features_ok",
        "hour_sample", float(fr["hour_utc"].iloc[-1]),
        "asia_ratio_nan_pct", float(fr["asia_range_atr_ratio"].isna().mean()),
        "squeeze_bars", int((fr["asia_range_atr_ratio"] < 0.7).sum()),
    )

    # quick full-sample trade count probe (not Gate2)
    defn = dsl_mod.validate_strategy(dsl)
    bt = dsl_mod.backtest_dsl(fr, defn, stop_loss_pct=0.009)
    trades = bt.get("trades") or []
    print("probe_trades", len(trades), "ret", bt.get("total_return_percent"))

    # KB must not block new family
    from dual_engine_workflow_v2.failure_kb import path_is_blocked
    blocked, why = path_is_blocked(
        "family|session_vol_squeeze_expansion_v1",
        family="session_vol_squeeze_expansion_v1",
    )
    print("new_family_blocked", blocked, why)
    assert not blocked, "new family unexpectedly blocked"


def main():
    tid = archive_session_liq_kb()
    validate_pack_and_features()
    print("DEPLOY_PRECHECK_OK", tid)


if __name__ == "__main__":
    main()
