#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit mounted live strategies: monitoring + open-on-trigger readiness."""
from __future__ import print_function

import glob
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO = ROOT / "auto_trade"
STRATEGY_CFG = ROOT / "strategy_configs"
EXPECTED_10 = [
    ("BTC-USDT-SWAP", "1h", "frost3_btc1h_xrpport_exhaustion_fade_slope", "formal_daemon_config.json"),
    ("ADA-USDT-SWAP", "5m", "codex0725t3_ada5m_trendpb_r42_z2p3_h14", "formal_daemon_config_ada_5m.json"),
    ("ADA-USDT-SWAP", "5m", "ada5m_bopb_asia_o20_r42_z2p3", "formal_daemon_config_ada_5m.json"),
    ("BTC-USDT-SWAP", "5m", "btc5_trend_rebound_ada5_clone_v1", "formal_daemon_config_btc_5m.json"),
    ("ETH-USDT-SWAP", "5m", "eth5_trend_rebound_ada5_clone_v1", "formal_daemon_config_eth_5m.json"),
    ("SOL-USDT-SWAP", "5m", "sol5_trend_rebound_ada5_clone_v1", "formal_daemon_config_sol_5m.json"),
    ("XRP-USDT-SWAP", "5m", "xrp5_trend_rebound_ada5_clone_v1", "formal_daemon_config_xrp_5m.json"),
    ("LTC-USDT-SWAP", "5m", "ltc5_exhaustion_fade_short_ai", "formal_daemon_config_ltc_5m.json"),
    ("NG-USDT-SWAP", "5m", "ng5_exhaustion_fade_short_ai", "formal_daemon_config_ng_5m.json"),
    ("XRP-USDT-SWAP", "15m", "frost_xrp_rescue_h20_t45", "formal_daemon_config_xrp_15m.json"),
]
KLINE_CLOSE_DELAY = {
    "codex0725t3_ada5m_trendpb_r42_z2p3_h14": 300,
    "sol5_trend_rebound_ada5_clone_v1": 300,
    "btc5_trend_rebound_ada5_clone_v1": 300,
    "eth5_trend_rebound_ada5_clone_v1": 300,
    "xrp5_trend_rebound_ada5_clone_v1": 300,
}


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def registry_lookup(key):
    for name in ("experimental_strategies.json", "ai_dsl_strategies.json"):
        data = read_json(STRATEGY_CFG / name, {})
        for item in data.get("strategies") or []:
            if item.get("key") == key:
                return name, item
    return None, {}


def pid_alive(pid_path):
    try:
        pid = int(Path(pid_path).read_text().strip())
        os.kill(pid, 0)
        return True, pid
    except Exception:
        return False, None


def suffix_for(symbol, timeframe):
    asset = symbol.split("-")[0].lower()
    tf = str(timeframe).lower()
    if tf == "1h" and symbol == "BTC-USDT-SWAP":
        return ""
    if tf in ("5m", "15m"):
        return "_%s_%s" % (asset, tf)
    return "_%s" % asset


def main():
    controls = read_json(AUTO / "strategy_runtime_controls.json", {"assignments": {}})
    assignments = controls.get("assignments") or {}
    gate = read_json(AUTO / "live_cognitive_gate_required.json", {})
    enforce_gate = bool(gate.get("enforce"))

    rows = []
    all_ok = True
    for symbol, timeframe, key, cfg_name in EXPECTED_10:
        cfg_path = AUTO / cfg_name
        cfg = read_json(cfg_path, {})
        aid = "%s|%s|%s" % (symbol, timeframe, key)
        row_ctrl = assignments.get(aid) or {}
        keys = list(cfg.get("strategy_keys") or [])
        sk = cfg.get("strategy_key")
        if sk and sk not in keys:
            keys.append(sk)

        issues = []
        checks = {}

        mounted = key in keys
        checks["mounted_in_daemon_config"] = mounted
        if not mounted:
            issues.append("未写入 daemon strategy_keys")

        checks["daemon_config_exists"] = cfg_path.exists()
        if not cfg_path.exists():
            issues.append("daemon 配置文件缺失: %s" % cfg_name)

        checks["daemon_enabled"] = cfg.get("enabled") is not False
        if cfg.get("enabled") is False:
            issues.append("daemon enabled=false")

        checks["allow_auto_open"] = bool(cfg.get("allow_auto_open"))
        if not cfg.get("allow_auto_open"):
            issues.append("allow_auto_open=false")

        checks["formal_authorized"] = bool(cfg.get("formal_auto_trading_authorized"))
        if not cfg.get("formal_auto_trading_authorized"):
            issues.append("formal_auto_trading_authorized=false")

        checks["gate_authorized"] = bool(cfg.get("gate_authorized_auto_trading"))
        if not cfg.get("gate_authorized_auto_trading"):
            issues.append("gate_authorized_auto_trading=false")

        suffix = suffix_for(symbol, timeframe)
        pid_path = AUTO / ("formal_daemon%s.pid" % suffix)
        alive, pid = pid_alive(pid_path)
        checks["daemon_process_alive"] = alive
        checks["daemon_pid"] = pid
        if not alive:
            issues.append("守护进程未运行 (%s)" % pid_path.name)

        pause = bool(row_ctrl.get("pause_new_entries"))
        checks["pause_new_entries"] = pause
        if pause:
            issues.append("runtime_controls 暂停新开仓")

        audit_state = str(row_ctrl.get("audit_state") or "")
        checks["audit_state"] = audit_state or "(none)"
        if enforce_gate and audit_state not in ("passed_all", "conditional_frequency_probe", ""):
            if audit_state in ("eliminated_pending_archive", "read_only_shadow", "lifecycle_shadow", "failed_closed"):
                issues.append("audit_state=%s 禁止新开仓" % audit_state)

        grade = str(row_ctrl.get("lifecycle_grade") or "B").upper()
        if grade in ("DELETED", "SHADOW"):
            issues.append("lifecycle_grade=%s" % grade)
        checks["lifecycle_grade"] = grade
        if grade == "C":
            issues.append("C级禁止新开仓")

        reg_name, reg = registry_lookup(key)
        checks["registry"] = reg_name or "missing"
        if reg_name == "ai_dsl_strategies.json":
            if reg.get("auto_trade_eligible") is not True:
                issues.append("auto_trade_eligible!=true")
            if reg.get("live_enabled") is not True:
                issues.append("live_enabled!=true")
        elif reg_name == "experimental_strategies.json":
            if reg.get("auto_trade_eligible") is False:
                issues.append("auto_trade_eligible=false")
        elif not reg_name:
            issues.append("策略未在 strategy_configs 注册")

        checks["kline_close_delay_sec"] = KLINE_CLOSE_DELAY.get(key, 0)
        can_open = mounted and not issues
        if not can_open:
            all_ok = False

        rows.append({
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy_key": key,
            "daemon_config": cfg_name,
            "can_open_on_trigger": can_open,
            "issues": issues,
            "checks": checks,
        })

    print(json.dumps({
        "ok": all_ok,
        "vector_root": str(ROOT),
        "enforce_cognitive_gate": enforce_gate,
        "expected_count": len(EXPECTED_10),
        "ready_count": sum(1 for r in rows if r["can_open_on_trigger"]),
        "rows": rows,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
