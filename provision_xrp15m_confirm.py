#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provision XRP 15m formal daemon + map, then confirm frost strategy."""
from __future__ import print_function
import json
import subprocess
from pathlib import Path

ROOT = Path("/root")
AUTO = ROOT / "auto_trade"
PIPELINES = [
    ROOT / "auto_trade_human_confirm_pipeline.py",
    AUTO / "auto_trade_human_confirm_pipeline.py",
]
KEY = "frost_xrp_rescue_h20_t45"


def patch_daemon_map(path):
    if not path.exists():
        print("skip missing", path)
        return
    text = path.read_text(encoding="utf-8")
    if "formal_daemon_config_xrp_15m.json" in text:
        print("already mapped", path)
        return
    needle = '("CL-USDT-SWAP", "5m"): "formal_daemon_config_cl_5m.json",\n    }'
    insert = (
        '("CL-USDT-SWAP", "5m"): "formal_daemon_config_cl_5m.json",\n'
        '        ("LTC-USDT-SWAP", "5m"): "formal_daemon_config_ltc_5m.json",\n'
        '        ("XRP-USDT-SWAP", "15m"): "formal_daemon_config_xrp_15m.json",\n'
        "    }"
    )
    if needle not in text:
        # tolerate alternate spacing
        needle2 = '("CL-USDT-SWAP", "5m"): "formal_daemon_config_cl_5m.json",\n    }\n'
        if needle2 in text:
            text = text.replace(needle2, insert + "\n", 1)
            path.write_text(text, encoding="utf-8")
            print("patched alt", path)
            return
        i = text.find("formal_daemon_config_cl_5m")
        print("needle missing in", path, "ctx=", repr(text[i:i + 160]))
        return
    path.write_text(text.replace(needle, insert, 1), encoding="utf-8")
    print("patched", path)


def main():
    for p in PIPELINES:
        patch_daemon_map(p)

    cfg = {
        "allow_auto_close": True,
        "allow_auto_open": True,
        "assignment_status": "active",
        "bar": "15m",
        "cooldown_sec_after_open": 300,
        "enabled": True,
        "fee_buffer_usdt": 0,
        "formal_auto_trading_authorized": True,
        "full_position_ratio": 0.3,
        "gate_authorized_auto_trading": True,
        "last_open_ts": 0,
        "last_signal_candle_id": None,
        "last_signal_candle_ids": {},
        "leverage": 20,
        "monitoring_enabled": True,
        "notification_enabled": True,
        "note": "XRP 15m 寒霜 exhaustion_fade：人工确认后B档30%上线",
        "position_mode": "full_balance",
        "protective_close_if_attached_sl_invalid": True,
        "reserve_usdt": 0,
        "schema_version": "intraday_live_v2_frequency",
        "stop_loss_pct": 0.009,
        "strategy_key": "",
        "strategy_keys": [],
        "symbol": "XRP-USDT-SWAP",
        "sz": "0.01",
        "take_profit_mode": "authoritative_strategy",
        "take_profit_on_signal_invalidated": False,
        "take_profit_pct": 0.009,
        "tick_interval_sec": 30,
        "timeframe": "15m",
    }
    (AUTO / "formal_daemon_config_xrp_15m.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("wrote config")

    unit = """[Unit]
Description=Qiyu Formal Auto Trade Daemon (XRP 15m Live)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root
Environment=PYTHONPATH=/root
Environment=VECTOR_TRADE_SYMBOL=XRP-USDT-SWAP
Environment=VECTOR_TRADE_TIMEFRAME=15m
ExecStart=/usr/bin/python3 -c "import auto_trade_formal_daemon as d; d.run_forever()"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
    Path("/etc/systemd/system/qiyu-formal-auto-trade-xrp-15m.service").write_text(
        unit, encoding="utf-8"
    )
    print("wrote unit")

    for cmd in [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "--now", "qiyu-formal-auto-trade-xrp-15m.service"],
    ]:
        print("run", cmd)
        subprocess.check_call(cmd)

    # confirm
    print("confirming", KEY)
    out = subprocess.check_output(
        ["python3", "/root/auto_trade_human_confirm_pipeline.py", "--confirm", KEY],
        cwd="/root",
    )
    print(out.decode("utf-8", errors="ignore"))


if __name__ == "__main__":
    main()
