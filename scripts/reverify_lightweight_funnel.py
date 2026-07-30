#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lightweight funnel re-verify for True Words campaign packs (no mount)."""
from __future__ import print_function

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.chdir(str(ROOT))


CASES = [
    {
        "name": "macro_sfp_unblocked_v1",
        "pack": ROOT / "strategy_macro_sfp_unblocked_v1.json",
        "symbol": "ETH-USDT-SWAP",
        "timeframe": "15m",
        "direction": "long",
    },
    {
        "name": "asia_sweep_fade_v1",
        "pack": ROOT / "strategy_asia_sweep_fade_v1.json",
        "symbol": "ETH-USDT-SWAP",
        "timeframe": "15m",
        "direction": "long",
    },
    {
        "name": "macro_sfp_displacement",
        "pack": ROOT / "strategy_macro_sfp_displacement_v2_scaleout.json",
        "symbol": "ETH-USDT-SWAP",
        "timeframe": "15m",
        "direction": "long",
    },
    {
        "name": "session_liq_engulf_displace_matrix_ad10",
        "pack": ROOT / "strategy_session_liq_engulf_matrix_ad10_seed.json",
        "symbol": "ETH-USDT-SWAP",
        "timeframe": "15m",
        "direction": "long",
        "alt_packs": [
            ROOT / "strategy_session_liq_engulf_matrix_ad10_eth_seed.json",
            ROOT / "strategy_session_liq_engulf_matrix_v1b.json",
        ],
    },
]


def _load_pack(case):
    paths = [case["pack"]] + list(case.get("alt_packs") or [])
    for p in paths:
        if p and Path(p).exists():
            return json.loads(Path(p).read_text(encoding="utf-8")), str(p)
    return None, None


def _select_dsl(pack, direction):
    if str(direction).lower() == "short":
        return pack.get("dsl_short") or pack.get("dsl")
    return pack.get("dsl_long") or pack.get("dsl")


def verify_one(case):
    from dual_engine_workflow_v2.failure_kb import path_is_blocked
    from dual_engine_workflow_v2.funnel_l0_density import run_l0_density
    from dual_engine_workflow_v2.funnel_l1_micro_screen import run_micro_screen
    from dual_engine_workflow_v2.incubation_soft_gate import soft_progress, should_skip_full_matrix
    from auto_driver import memory_guard
    import auto_trade_strategy_dsl as dsl_mod
    import auto_trade_dual_engine_factory as dual
    import auto_trade_human_confirm_pipeline as pipe

    pack, pack_path = _load_pack(case)
    out = {
        "name": case["name"],
        "pack_path": pack_path,
        "ok_pack": bool(pack),
    }
    if not pack:
        out["verdict"] = "MISSING_PACK"
        out["detail"] = "strategy pack file not found"
        return out

    spec = pack.get("mechanism_spec") or {}
    fam = spec.get("mechanism_family") or case["name"]
    blocked, br = path_is_blocked("family|%s" % fam, family=fam)
    out["family"] = fam
    out["kb_blocked"] = bool(blocked)
    out["kb_reason"] = br
    if blocked:
        out["verdict"] = "KB_BLOCKED"
        out["detail"] = "Failure KB exact/path block — do not micro-perturb; start a new family"
        return out

    symbol = case["symbol"]
    tf = case["timeframe"]
    direction = case["direction"]
    dsl = dict(_select_dsl(pack, direction) or {})
    dsl["supported_instruments"] = [symbol]
    dsl["timeframe"] = tf
    t0 = time.time()
    try:
        definition = dsl_mod.validate_strategy(dsl)
    except Exception as exc:
        out["verdict"] = "DSL_INVALID"
        out["detail"] = str(exc)
        out["elapsed_sec"] = round(time.time() - t0, 2)
        return out

    budget = memory_guard.matrix_budget()
    out["ram_budget"] = budget
    memory_guard.ensure_headroom(min_avail_mb=120, label="verify_%s" % case["name"])
    try:
        frame = dual._frame(symbol, tf)
    except Exception as exc:
        out["verdict"] = "FRAME_FAIL"
        out["detail"] = str(exc)
        return out

    l0 = run_l0_density(definition=definition, frame=frame)
    out["l0"] = {
        "pass": l0.get("pass"),
        "triggers": (l0.get("metrics") or {}).get("triggers"),
        "evaluated": (l0.get("metrics") or {}).get("evaluated_bars"),
        "density": (l0.get("metrics") or {}).get("density"),
        "wall_ms": l0.get("wall_time_ms"),
        "reasons": l0.get("reject_reasons"),
    }
    if not l0.get("pass"):
        out["verdict"] = "L0_REJECT_TOO_RARE"
        out["detail"] = "density cull — relax non-causal filters before matrix"
        out["elapsed_sec"] = round(time.time() - t0, 2)
        memory_guard.force_release("post_%s" % case["name"])
        return out

    # Anchor L1 (primary only on this host under low RAM)
    def _bt(frm, defn):
        return dsl_mod.backtest_dsl(frm, defn, stop_loss_pct=0.009)

    l1 = run_micro_screen(
        definition=definition, frame=frame, backtest_fn=_bt, seed=42,
    )
    m = l1.get("metrics") or {}
    out["l1"] = {
        "pass": l1.get("pass"),
        "filled_entries": m.get("filled_entries"),
        "payoff_ratio": m.get("payoff_ratio"),
        "reasons": l1.get("reject_reasons"),
        "wall_ms": l1.get("wall_time_ms"),
    }
    if not l1.get("pass"):
        out["verdict"] = "L1_FAIL"
        out["detail"] = ",".join(l1.get("reject_reasons") or ["l1_fail"])
        out["elapsed_sec"] = round(time.time() - t0, 2)
        memory_guard.force_release("post_%s" % case["name"])
        return out

    # Primary full BT soft metrics (no full 38 matrix on low RAM)
    try:
        bt = dual._backtest(definition, symbol, tf, "observed_base")
        trades = list((bt or {}).get("trades") or [])
        metrics = dual._metrics_from_trades(trades)
    except Exception as exc:
        out["verdict"] = "PRIMARY_BT_FAIL"
        out["detail"] = str(exc)
        out["elapsed_sec"] = round(time.time() - t0, 2)
        return out

    soft = soft_progress(metrics)
    skip = should_skip_full_matrix(metrics)
    out["primary"] = {
        "n_trades": len(trades),
        "payoff_ratio": metrics.get("payoff_ratio"),
        "calmar": metrics.get("calmar"),
        "win_rate_pct": metrics.get("win_rate_pct") or metrics.get("win_rate"),
        "soft_budding": soft.get("budding"),
        "skip_full_matrix": skip,
        "soft": soft,
    }
    # Formal Gate2 floors check (report only)
    pay = metrics.get("payoff_ratio")
    cal = metrics.get("calmar")
    try:
        formal_ok = (
            pay is not None and float(pay) >= 2.5
            and cal is not None and float(cal) >= 1.5
            and len(trades) >= 8
        )
    except Exception:
        formal_ok = False
    out["formal_gate2_primary"] = formal_ok
    if formal_ok:
        out["verdict"] = "PRIMARY_FORMAL_OK_PENDING_MATRIX"
        out["detail"] = "primary clears hard floors; matrix width still RAM-gated"
    elif soft.get("budding"):
        out["verdict"] = "SOFT_BUDDING_NOT_FORMAL"
        out["detail"] = "positive soft bud; needs exit/scale-out evolution toward Gate2"
    else:
        out["verdict"] = "PRIMARY_WEAK"
        out["detail"] = "passed L0/L1 but primary fitness below soft budding"
    out["elapsed_sec"] = round(time.time() - t0, 2)
    memory_guard.force_release("post_%s" % case["name"])
    try:
        pipe.trim_frame_cache(2)
    except Exception:
        pass
    return out


def main():
    rows = []
    for case in CASES:
        print("=== verify", case["name"], flush=True)
        try:
            row = verify_one(case)
        except Exception as exc:
            row = {"name": case["name"], "verdict": "EXCEPTION", "detail": str(exc)}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False, indent=2, default=str), flush=True)
        time.sleep(1)
    out_path = ROOT / "auto_trade" / "dual_engine" / "lightweight_funnel_reverify.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "schema": "qiyu_lightweight_funnel_reverify_v1",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "results": rows,
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("WROTE", out_path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
