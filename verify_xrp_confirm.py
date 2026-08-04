#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import print_function
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

KEY = "frost_xrp_rescue_h20_t45"
AID = "XRP-USDT-SWAP|15m|frost_xrp_rescue_h20_t45"


def main():
    # find dsl
    hits = subprocess.check_output(
        ["bash", "-lc", "rg -l frost_xrp_rescue_h20_t45 /root/auto_trade /root 2>/dev/null | head -n 30"]
    ).decode()
    print("HITS\n", hits)

    # upsert location
    src = Path("/root/auto_trade_human_confirm_pipeline.py").read_text(encoding="utf-8")
    i = src.find("def _upsert_dsl")
    print(src[i:i + 900])

    # auth API probe
    import base64
    auth = base64.b64encode(b"quant:btc2026").decode()
    headers = {"Authorization": "Basic " + auth}
    for ep in [
        "/api/strategy_runtime",
        "/api/runtime/assignments",
        "/api/auto_trade/assignments",
        "/api/formal/daemon_status",
        "/api/trading/strategies",
        "/api/live_strategies",
        "/api/strategy/list",
        "/api/dashboard",
    ]:
        try:
            req = urllib.request.Request("http://127.0.0.1:8080" + ep, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode("utf-8", "ignore")
            print(ep, "->", resp.status, "xrp" if "frost_xrp" in body or "XRP-USDT" in body else body[:100])
        except Exception as exc:
            print(ep, "ERR", type(exc).__name__, exc)

    # daemon
    print("daemon_active", subprocess.check_output(["systemctl", "is-active", "qiyu-formal-auto-trade-xrp-15m.service"]).decode().strip())
    log = Path("/root/logs/backtest/formal_daemon_xrp_15m.log")
    print("log_exists", log.exists(), "size", log.stat().st_size if log.exists() else 0)
    if log.exists():
        print("LOG_TAIL\n", log.read_text(encoding="utf-8", errors="ignore")[-1500:])

    cfg = json.loads(Path("/root/auto_trade/formal_daemon_config_xrp_15m.json").read_text(encoding="utf-8"))
    ctrl = json.loads(Path("/root/auto_trade/strategy_runtime_controls.json").read_text(encoding="utf-8"))
    row = (ctrl.get("assignments") or {}).get(AID) or {}
    pend = json.loads(Path("/root/auto_trade/strategy_pending_human_confirm.json").read_text(encoding="utf-8"))
    item = next((x for x in (pend.get("items") or []) if x.get("key") == KEY), {})
    print("SUMMARY", json.dumps({
        "title": item.get("name"),
        "status": item.get("status"),
        "grade": row.get("lifecycle_grade"),
        "size": row.get("max_position_ratio"),
        "wr": item.get("ai_theoretical_wr_avg"),
        "daemon_keys": cfg.get("strategy_keys"),
        "allow_auto_open": cfg.get("allow_auto_open"),
        "pause_new_entries": row.get("pause_new_entries"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
