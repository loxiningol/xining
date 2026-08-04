# -*- coding: utf-8 -*-
"""Account-wide live portfolio limits shared by every symbol/timeframe daemon."""

from __future__ import print_function

from datetime import datetime
from pathlib import Path
import glob
import json
import os
import time


AUTO_DIR = Path(os.environ.get("VECTOR_AUTO_DIR", "/root/auto_trade"))
POLICY_FILE = AUTO_DIR / "portfolio_risk_policy.json"
DEFAULT_POLICY = {
    "schema": "qiyu_portfolio_risk_v2_absolute_equity",
    "enabled": True,
    "max_open_positions": 3,
    "max_positions_per_symbol": 1,
    "max_pending_orders": 0,
    "max_daily_entries": 5,
    "max_daily_realized_loss_ratio": 0.08,
    "max_consecutive_losses": 3,
    "max_total_estimated_risk_ratio": 0.152,
    "position_sizing_basis": "account_total_equity_at_entry",
    "profiles": {
        "1h": {"full_position_ratio": 0.28, "leverage": 20, "stop_loss_pct": 0.009},
        "15m": {"full_position_ratio": 0.20, "leverage": 20, "stop_loss_pct": 0.009},
        "5m": {"full_position_ratio": 0.15, "leverage": 20, "stop_loss_pct": 0.009},
    },
}


def _read(path, default):
    try:
        with open(str(path), "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else default
    except Exception:
        return default


def load_policy():
    policy = dict(DEFAULT_POLICY)
    disk = _read(POLICY_FILE, {})
    for key, value in disk.items():
        policy[key] = value
    profiles = dict(DEFAULT_POLICY["profiles"])
    profiles.update(policy.get("profiles") or {})
    policy["profiles"] = profiles
    return policy


def profile(timeframe):
    key = str(timeframe or "1h").lower()
    return dict(load_policy()["profiles"].get(key) or DEFAULT_POLICY["profiles"]["1h"])


def _today_start_ts():
    # Server is configured in Asia/Shanghai; local midnight is the intended risk day.
    now = datetime.now()
    return time.mktime((now.year, now.month, now.day, 0, 0, 0, 0, 0, -1))


def _state_snapshots():
    rows = []
    for name in glob.glob(str(AUTO_DIR / "formal_v6_state*.json")):
        state = _read(name, {})
        if state:
            rows.append((name, state))
    return rows


def _number(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return float(default)


def local_risk_snapshot():
    start = _today_start_ts()
    active = []
    closed_today = []
    for name, state in _state_snapshots():
        current = state.get("current")
        if isinstance(current, dict) and current.get("status") not in ("closed", "cancelled", "failed"):
            ratio = _number(current.get("full_position_ratio"), 1.0)
            leverage = _number(current.get("leverage"), 20)
            stop = _number(current.get("stop_loss_pct"), 0.009)
            current = dict(current)
            current["estimated_account_risk_ratio"] = ratio * leverage * stop
            current["state_file"] = name
            active.append(current)
        for trade in state.get("history") or []:
            if not isinstance(trade, dict):
                continue
            if _number(trade.get("opened_at_ts"), 0) >= start:
                closed_today.append(trade)
    closed_today.sort(key=lambda x: _number(x.get("closed_at_ts"), 0))
    realized_loss = 0.0
    consecutive_losses = 0
    for trade in closed_today:
        pnl = _number(trade.get("pnl"), 0)
        imr = _number(((trade.get("position_poll") or {}).get("position") or {}).get("imr"), 0)
        ratio = _number(trade.get("full_position_ratio"), 1.0)
        account_return = (pnl / imr * ratio) if imr > 0 else 0.0
        if account_return < 0:
            realized_loss += -account_return
            consecutive_losses += 1
        elif account_return > 0:
            consecutive_losses = 0
    return {
        "active": active,
        "estimated_open_risk_ratio": sum(
            _number(item.get("estimated_account_risk_ratio"), 0) for item in active
        ),
        "daily_entries": len(closed_today) + sum(
            1 for item in active if _number(item.get("opened_at_ts"), 0) >= start
        ),
        "daily_realized_loss_ratio": realized_loss,
        "consecutive_losses": consecutive_losses,
    }


def preflight(symbol, timeframe, full_position_ratio, leverage, stop_loss_pct,
              exchange_positions, pending_orders):
    policy = load_policy()
    if not policy.get("enabled", True):
        return {"ok": False, "blocked": True, "error": "portfolio risk policy disabled"}
    positions = list(exchange_positions or [])
    pending = list(pending_orders or [])
    symbol = str(symbol or "").upper()
    if len(positions) >= int(policy["max_open_positions"]):
        return {"ok": False, "blocked": True, "error": "portfolio max open positions reached"}
    same_symbol = [row for row in positions if str(row.get("instId") or "").upper() == symbol]
    if len(same_symbol) >= int(policy["max_positions_per_symbol"]):
        return {"ok": False, "blocked": True, "error": "symbol already has an active position"}
    if len(pending) > int(policy.get("max_pending_orders", 0)):
        return {"ok": False, "blocked": True, "error": "account has pending swap orders"}
    local = local_risk_snapshot()
    if local["daily_entries"] >= int(policy["max_daily_entries"]):
        return {"ok": False, "blocked": True, "error": "daily entry limit reached", "risk": local}
    if local["daily_realized_loss_ratio"] >= float(policy["max_daily_realized_loss_ratio"]):
        return {"ok": False, "blocked": True, "error": "daily realized loss circuit breaker", "risk": local}
    if local["consecutive_losses"] >= int(policy["max_consecutive_losses"]):
        return {"ok": False, "blocked": True, "error": "consecutive loss circuit breaker", "risk": local}
    candidate_risk = float(full_position_ratio) * float(leverage) * float(stop_loss_pct)
    total = local["estimated_open_risk_ratio"] + candidate_risk
    if total > float(policy["max_total_estimated_risk_ratio"]) + 1e-12:
        return {"ok": False, "blocked": True, "error": "portfolio estimated risk limit exceeded",
                "candidate_risk_ratio": candidate_risk, "combined_risk_ratio": total, "risk": local}
    return {"ok": True, "policy": policy, "risk": local,
            "candidate_risk_ratio": candidate_risk, "combined_risk_ratio": total,
            "timeframe": timeframe, "symbol": symbol}
