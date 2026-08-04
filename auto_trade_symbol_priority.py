# -*- coding: utf-8 -*-
"""Account-wide deterministic signal arbitration for 1H auto trading."""

from pathlib import Path
import json
import os
import re
import tempfile
import time
import uuid


AUTO_DIR = Path(os.environ.get("VECTOR_PRIORITY_AUTO_DIR", "/root/auto_trade"))
PRIORITY_FILE = AUTO_DIR / "symbol_priority.json"
STATE_FILE = AUTO_DIR / "global_signal_priority_state.json"
LOCK_FILE = AUTO_DIR / "global_signal_priority.lock"
DEFAULT_PRIORITY = ["BTC-USDT-SWAP", "CL-USDT-SWAP", "XAU-USDT-SWAP"]
DEFAULT_ARBITRATION_WINDOW_SEC = 15


def _read_json(path, default):
    try:
        text = Path(path).read_text(encoding="utf-8")
        data = json.loads(text)
        return data
    except Exception:
        return default


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        delete=False, dir=str(path.parent), mode="w", encoding="utf-8"
    )
    try:
        json.dump(data, tmp, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.flush()
        tmp.close()
        Path(tmp.name).replace(path)
    except Exception:
        try:
            Path(tmp.name).unlink()
        except Exception:
            pass
        raise


def _acquire_lock(timeout_sec=4.0, stale_sec=15.0):
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        now = time.time()
        token = uuid.uuid4().hex
        payload = {"token": token, "pid": os.getpid(), "ts": now}
        if LOCK_FILE.exists():
            old = _read_json(LOCK_FILE, {})
            stale = now - float(old.get("ts") or 0) > stale_sec
            old_pid = old.get("pid")
            if old_pid:
                try:
                    os.kill(int(old_pid), 0)
                except Exception:
                    stale = True
            if stale:
                try:
                    LOCK_FILE.unlink()
                except Exception:
                    pass
        try:
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, json.dumps(payload).encode("utf-8"))
            finally:
                os.close(fd)
            return payload
        except FileExistsError:
            time.sleep(0.05)
    return None


def _release_lock(lock):
    try:
        if not lock or not LOCK_FILE.exists():
            return
        current = _read_json(LOCK_FILE, {})
        if current.get("token") == lock.get("token"):
            LOCK_FILE.unlink()
    except Exception:
        pass


def _load_priority():
    cfg = _read_json(PRIORITY_FILE, {})
    order = cfg.get("priority") if isinstance(cfg, dict) else None
    if not isinstance(order, list):
        order = list(DEFAULT_PRIORITY)
    normalized = []
    for symbol in order:
        symbol = str(symbol or "").strip().upper()
        if symbol and symbol not in normalized:
            normalized.append(symbol)
    for symbol in DEFAULT_PRIORITY:
        if symbol not in normalized:
            normalized.append(symbol)
    window = int((cfg or {}).get("arbitration_window_sec") or DEFAULT_ARBITRATION_WINDOW_SEC)
    return {"priority": normalized, "arbitration_window_sec": max(10, min(window, 300))}


def _ensure_symbol_locked(symbol, cfg):
    order = list(cfg["priority"])
    if symbol not in order:
        order.append(symbol)
        cfg["priority"] = order
        _atomic_write(PRIORITY_FILE, {
            "priority": order,
            "arbitration_window_sec": cfg["arbitration_window_sec"],
            "unknown_symbol_policy": "append_to_end",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    return order


def _group_id(candle_id):
    text = str(candle_id or "")
    match = re.search(r"(\d{10,13})(?!.*\d)", text)
    if match:
        raw = int(match.group(1))
        open_ms = raw * 1000 if raw < 100000000000 else raw
        tf_match = re.search(r":(5m|15m|1h):", text, re.IGNORECASE)
        tf_ms = {"5m":300000, "15m":900000, "1h":3600000}.get(
            (tf_match.group(1).lower() if tf_match else "1h"), 3600000
        )
        return "CLOSE:" + str(open_ms + tf_ms)
    return "CLOSE:" + text


def _cleanup(state, now):
    groups = state.setdefault("groups", {})
    for key in list(groups):
        updated = float((groups.get(key) or {}).get("updated_ts") or 0)
        if updated and now - updated > 172800:
            groups.pop(key, None)


def register_and_decide(symbol, candle_id, strategy_key, side, now=None,
                        timeframe="1h", score=None, strategy_grade=None,
                        expected_win_rate=None, expected_return=None):
    """Register a signal and return pending/winner/suppressed deterministically."""
    now = float(now if now is not None else time.time())
    symbol = str(symbol or "").strip().upper()
    lock = _acquire_lock()
    if not lock:
        return {"ok": False, "status": "error", "error": "priority coordinator lock busy"}
    try:
        cfg = _load_priority()
        order = _ensure_symbol_locked(symbol, cfg)
        rank = {item: index + 1 for index, item in enumerate(order)}
        state = _read_json(STATE_FILE, {"schema": "global_signal_priority_v1", "groups": {}})
        if not isinstance(state, dict):
            state = {"schema": "global_signal_priority_v1", "groups": {}}
        _cleanup(state, now)
        gid = _group_id(candle_id)
        groups = state.setdefault("groups", {})
        group = groups.setdefault(gid, {
            "group_id": gid,
            "first_seen_ts": now,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "intents": {},
            "decision": None,
        })
        group["updated_ts"] = now
        grade = str(strategy_grade or "B").upper()
        grade_rank = {"S": 4, "A": 3, "B": 2, "C": 1}.get(grade, 2)
        new_intent = {
            "symbol": symbol,
            "priority": rank.get(symbol, len(order) + 1),
            "strategy_key": str(strategy_key or ""),
            "side": str(side or ""),
            "timeframe": str(timeframe or "1h"),
            "signal_score": float(score or 0),
            "strategy_grade": grade,
            "grade_rank": grade_rank,
            "expected_win_rate": float(expected_win_rate or 0),
            "expected_return": float(expected_return or 0),
            "candle_id": str(candle_id or ""),
            "registered_ts": now,
        }
        intents = group.setdefault("intents", {})
        existing = intents.get(symbol)
        # Multiple timeframes of the same symbol share one account intent.
        # Keep the objectively stronger grade/expectancy instead of whichever
        # daemon happened to write last.
        new_quality = (
            int(new_intent["grade_rank"]), float(new_intent["expected_return"]),
            float(new_intent["expected_win_rate"]), float(new_intent["signal_score"]),
        )
        old_quality = (
            int((existing or {}).get("grade_rank") or 0),
            float((existing or {}).get("expected_return") or -999),
            float((existing or {}).get("expected_win_rate") or 0),
            float((existing or {}).get("signal_score") or 0),
        )
        if not isinstance(existing, dict) or new_quality > old_quality:
            intents[symbol] = new_intent
        decision = group.get("decision")
        if isinstance(decision, dict):
            status = "winner" if decision.get("winner_symbol") == symbol else "suppressed"
            _atomic_write(STATE_FILE, state)
            return {
                "ok": True, "status": status, "group_id": gid,
                "winner_symbol": decision.get("winner_symbol"),
                "winner_priority": decision.get("winner_priority"),
                "current_priority": rank.get(symbol),
                "decision_locked": True,
            }
        age = now - float(group.get("first_seen_ts") or now)
        if age < cfg["arbitration_window_sec"]:
            _atomic_write(STATE_FILE, state)
            return {
                "ok": True, "status": "pending", "group_id": gid,
                "current_priority": rank.get(symbol),
                "wait_remaining_sec": round(cfg["arbitration_window_sec"] - age, 3),
                "registered_symbols": sorted(group["intents"], key=lambda item: rank.get(item, 999999)),
            }
        winner = min(group["intents"].values(), key=lambda item: (
            -int(item.get("grade_rank") or 0),
            int(item.get("priority") or 999999),
            -float(item.get("expected_return") or -999),
            -float(item.get("expected_win_rate") or 0),
            item.get("registered_ts") or now,
        ))
        decision = {
            "winner_symbol": winner["symbol"],
            "winner_priority": winner["priority"],
            "winner_strategy_key": winner.get("strategy_key"),
            "winner_strategy_grade": winner.get("strategy_grade"),
            "winner_expected_win_rate": winner.get("expected_win_rate"),
            "winner_expected_return": winner.get("expected_return"),
            "decided_ts": now,
            "decided_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "execution": None,
        }
        group["decision"] = decision
        _atomic_write(STATE_FILE, state)
        return {
            "ok": True,
            "status": "winner" if winner["symbol"] == symbol else "suppressed",
            "group_id": gid,
            "winner_symbol": winner["symbol"],
            "winner_priority": winner["priority"],
            "current_priority": rank.get(symbol),
            "registered_symbols": sorted(group["intents"], key=lambda item: rank.get(item, 999999)),
            "decision_locked": True,
        }
    finally:
        _release_lock(lock)


def mark_execution(candle_id, symbol, opened, detail=None):
    now = time.time()
    lock = _acquire_lock()
    if not lock:
        return {"ok": False, "error": "priority coordinator lock busy"}
    try:
        state = _read_json(STATE_FILE, {"schema": "global_signal_priority_v1", "groups": {}})
        group = (state.get("groups") or {}).get(_group_id(candle_id))
        if not isinstance(group, dict) or not isinstance(group.get("decision"), dict):
            return {"ok": False, "error": "priority decision not found"}
        decision = group["decision"]
        if decision.get("winner_symbol") != str(symbol or "").upper():
            return {"ok": False, "error": "execution symbol is not priority winner"}
        decision["execution"] = {
            "opened": bool(opened),
            "ts": now,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "detail": detail or {},
        }
        group["updated_ts"] = now
        _atomic_write(STATE_FILE, state)
        return {"ok": True, "winner_symbol": decision.get("winner_symbol"), "opened": bool(opened)}
    finally:
        _release_lock(lock)


def status():
    cfg = _load_priority()
    state = _read_json(STATE_FILE, {"schema": "global_signal_priority_v1", "groups": {}})
    return {
        "ok": True,
        "priority": cfg["priority"],
        "arbitration_window_sec": cfg["arbitration_window_sec"],
        "unknown_symbol_policy": "append_to_end",
        "recent_groups": list((state.get("groups") or {}).values())[-12:],
    }
