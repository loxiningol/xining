# -*- coding: utf-8 -*-
"""Generate STEP_B_stop_loss_matrix.json on production (report only; no SL mutate)."""
from __future__ import print_function
import glob
import json
from datetime import datetime
from pathlib import Path

AUTO = Path("/root/auto_trade")
DOCS = Path("/root/docs")

LIVE = [
    ("ADA-USDT-SWAP", "5m", "codex0725t3_ada5m_trendpb_r42_z2p3_h14",
     "formal_daemon_config_ada_5m.json", "qiyu-formal-auto-trade-ada-5m"),
    ("LTC-USDT-SWAP", "5m", "ltc5_exhaustion_fade_short_ai",
     "formal_daemon_config_ltc_5m.json", "qiyu-formal-auto-trade-ltc-5m"),
    ("NG-USDT-SWAP", "5m", "ng5_exhaustion_fade_short_ai",
     "formal_daemon_config_ng_5m.json", "qiyu-formal-auto-trade-ng-5m"),
    ("XRP-USDT-SWAP", "15m", "frost_xrp_rescue_h20_t45",
     "formal_daemon_config_xrp_15m.json", "qiyu-formal-auto-trade-xrp-15m"),
    ("BTC-USDT-SWAP", "1h", "frost3_btc1h_xrpport_exhaustion_fade_slope",
     "formal_daemon_config.json", "qiyu-formal-auto-trade"),
]


def last_order_sl(symbol, key):
    out = {"order_sl": None, "exchange_distance": None, "opened_at": None}
    for path in glob.glob(str(AUTO / "formal_v6_state*.json")):
        st = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = list(st.get("history") or [])
        cur = st.get("current")
        if isinstance(cur, dict):
            rows.append(cur)
        for row in reversed(rows):
            if not isinstance(row, dict):
                continue
            if str(row.get("strategy_key")) != key:
                continue
            if row.get("symbol") and str(row.get("symbol")).upper() != symbol.upper():
                continue
            out["order_sl"] = row.get("stop_loss_pct")
            out["opened_at"] = row.get("opened_at")
            entry = row.get("entry_price")
            slpx = row.get("stop_loss_price")
            try:
                if entry and slpx:
                    out["exchange_distance"] = abs(float(slpx) - float(entry)) / float(entry)
            except Exception:
                pass
            return out
    return out


def main():
    ctrl = json.loads((AUTO / "strategy_runtime_controls.json").read_text(encoding="utf-8"))
    asn = ctrl.get("assignments") or {}
    strategies = []
    for sym, tf, key, cfgname, unit in LIVE:
        cfg = json.loads((AUTO / cfgname).read_text(encoding="utf-8"))
        aid = "%s|%s|%s" % (sym, tf, key)
        row = asn.get(aid) or {}
        ordinfo = last_order_sl(sym, key)
        daemon_sl = cfg.get("stop_loss_pct")
        runtime_sl = row.get("stop_loss_pct")
        strategies.append({
            "strategy_key": key,
            "symbol": sym,
            "timeframe": tf,
            "daemon_config_file": cfgname,
            "systemd_unit": unit,
            "daemon_stop_loss_pct": daemon_sl,
            "runtime_controls_stop_loss_pct": runtime_sl,
            "dsl_stop_loss_pct": None,
            "last_order_stop_loss_pct": ordinfo.get("order_sl"),
            "last_order_exchange_distance": ordinfo.get("exchange_distance"),
            "last_order_opened_at": ordinfo.get("opened_at"),
            "frontend_default_stop_loss_pct": 0.009,
            "order_creation_reads_stop_loss_pct": daemon_sl,
            "order_creation_source": (
                "formal_daemon_config via executor._load_stage823_trade_config"
                " / _active_stop_loss_pct"
            ),
            "matches_0_9pct": abs(float(daemon_sl or 0) - 0.009) < 1e-9,
            "inconsistency": (
                runtime_sl is not None and daemon_sl is not None
                and abs(float(runtime_sl) - float(daemon_sl)) > 1e-9
            ),
        })

    ada = [s for s in strategies if "ada" in s["strategy_key"]][0]
    ada_deep = {
        "priority_order": [
            "1. executor._active_stop_loss_pct() <- _load_stage823_trade_config()",
            "2. DAEMON_CONFIG_FILE from VECTOR_TRADE_SYMBOL/TIMEFRAME",
            "3. runtime assignments.stop_loss_pct NOT used for attach SL",
            "4. frontend 0.009 is display/default, not ADA order truth",
        ],
        "which_value_order_creation_reads": ada["order_creation_reads_stop_loss_pct"],
        "next_order_will_be": 0.006,
        "next_order_will_NOT_be": 0.009,
        "frontend_shows": 0.009,
        "display_vs_reality_risk": (
            "Frontend/runtime show 0.9% while ADA daemon config remains 0.6%. "
            "Next ADA order attaches SL at 0.6%. DO NOT migrate without human confirmation."
        ),
        "H1_auto_SL_attach_chain": {
            "result": "PASS",
            "evidence": "attachAlgoOrds path intact; LTC live used 0.009; STEP B did not change SL code path.",
        },
        "H2_all_strategies_actually_0_9pct": {
            "result": "FAIL",
            "evidence": "ADA formal_daemon_config_ada_5m.json stop_loss_pct=0.006",
        },
        "safe_migrate_script": "/root/docs/step_b_ada_sl_migrate_0p6_to_0p9.sh",
        "safe_rollback_script": "/root/docs/step_b_ada_sl_rollback_0p9_to_0p6.sh",
        "ada_only_restart_steps": [
            "Confirm ADA flat / cooldown clear",
            "Human confirms migrate",
            "Run migrate script (ADA config only)",
            "systemctl restart qiyu-formal-auto-trade-ada-5m",
            "Verify daemon+executor effective SL=0.009",
            "On next open verify exchange distance ~0.9%",
        ],
        "next_order_verification_plan": [
            "Read ADA formal_v6 state stop_loss_pct / stop_loss_price",
            "Require |sl-entry|/entry ~0.009",
            "Require attachAlgoOrds + exchange_side_stop_verified",
            "Else run rollback + ADA-only restart",
        ],
        "DO_NOT_AUTO_MIGRATE": True,
    }
    matrix = {
        "schema": "qiyu_step_b_stop_loss_matrix_v1",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "leverage_policy": 20,
        "grade_B_position_ratio": 0.30,
        "strategies": strategies,
        "ada_deep_dive": ada_deep,
        "H1": ada_deep["H1_auto_SL_attach_chain"],
        "H2": ada_deep["H2_all_strategies_actually_0_9pct"],
    }
    DOCS.mkdir(parents=True, exist_ok=True)
    text = json.dumps(matrix, ensure_ascii=False, indent=2)
    (DOCS / "STEP_B_stop_loss_matrix.json").write_text(text, encoding="utf-8")
    (AUTO / "STEP_B_stop_loss_matrix.json").write_text(text, encoding="utf-8")
    print("wrote", DOCS / "STEP_B_stop_loss_matrix.json")
    print("ADA order_creation_reads", ada["order_creation_reads_stop_loss_pct"],
          "H1", ada_deep["H1_auto_SL_attach_chain"]["result"],
          "H2", ada_deep["H2_all_strategies_actually_0_9pct"]["result"])


if __name__ == "__main__":
    main()
