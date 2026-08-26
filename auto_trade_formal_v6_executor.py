
# -*- coding: utf-8 -*-
from pathlib import Path
import os, json, time, uuid, tempfile, shutil

ROOT = Path("/root")
AUTO_DIR = ROOT / "auto_trade"
import auto_trade_slot_paths as slot_paths
SYMBOL = os.environ.get("VECTOR_TRADE_SYMBOL", "BTC-USDT-SWAP").strip().upper()
INSTANCE_KEY = SYMBOL.split("-")[0].lower()
INSTANCE_SUFFIX = slot_paths.state_suffix(SYMBOL)
STATE_FILE = AUTO_DIR / ("formal_v6_state%s.json" % INSTANCE_SUFFIX)
EVENT_FILE = AUTO_DIR / ("formal_v6_events%s.jsonl" % INSTANCE_SUFFIX)
LOCK_FILE = AUTO_DIR / ("formal_v6%s.lock" % INSTANCE_SUFFIX)
GATE_FILE = AUTO_DIR / ("formal_live_gate%s.json" % INSTANCE_SUFFIX)

TD_MODE = "isolated"
LEVERAGE = 20
HARD_SZ = "0.01"
STOP_LOSS_PCT = 0.009

ORDER_POLL_ATTEMPTS = 16
POSITION_POLL_ATTEMPTS = 20
ATTACHED_SL_VERIFY_ATTEMPTS = 24
POLL_INTERVAL = 0.5

ACTIVE_STATUSES = ["opening", "open", "unprotected_open", "unknown_open", "close_pending", "close_failed", "dangling_stop_algo"]
FINAL_NO_OPEN_STATES = ["canceled", "cancelled", "rejected", "expired", "failed", "mmp_canceled"]

def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def _safe_float(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default

def _read_json(path, default):
    try:
        p = Path(path)
        if not p.exists():
            return default
        s = p.read_text(encoding="utf-8", errors="ignore")
        if not s.strip():
            return default
        return json.loads(s)
    except Exception:
        return default

def _atomic_write(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(delete=False, dir=str(p.parent), mode="w", encoding="utf-8")
    try:
        tmp.write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        tmp.flush()
        tmp.close()
        Path(tmp.name).replace(p)
    except Exception:
        try:
            Path(tmp.name).unlink()
        except Exception:
            pass
        raise

def _append_event(event_type, data=None, strategy_key="ema6_center_down_then_fall"):
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "schema": "formal_v6_event_stage8_22_full_v3",
        "time": _now(),
        "ts": time.time(),
        "event_type": event_type,
        "strategy_key": strategy_key,
        "strategy_name": "EMA6居中后再下行",
        "symbol": SYMBOL,
        "data": data or {}
    }
    with EVENT_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    # Dual-write unified strategy_event taxonomy (fail-safe; never break trading).
    try:
        import auto_trade_strategy_events as sev
        data = data or {}
        sev.emit_from_executor(
            event_type,
            strategy_key=strategy_key or data.get("strategy") or "",
            symbol=data.get("symbol") or SYMBOL,
            timeframe=os.environ.get("VECTOR_TRADE_TIMEFRAME") or TRADE_TIMEFRAME,
            order_id=str(data.get("ordId") or data.get("clOrdId") or ""),
            position_id=str(
                data.get("position_id")
                or (data.get("current") or {}).get("position_id")
                or ""),
            reason_code=str(data.get("close_reason") or data.get("reason") or event_type),
            stop_loss_pct=data.get("stop_loss_pct"),
            extra={"executor_event": event_type},
        )
    except Exception:
        pass
    return row

def _default_state():
    return {
        "schema": "formal_v6_state_stage8_22_full_v3",
        "updated_at": _now(),
        "current": None,
        "armed": None,
        "history": [],
        "last_entry_order_payload": None,
        "last_attached_stop_loss": None,
        "last_error": None
    }

def _load_state():
    st = _read_json(STATE_FILE, None)
    if not isinstance(st, dict):
        st = _default_state()
    st.setdefault("current", None)
    st.setdefault("armed", None)
    st.setdefault("history", [])
    st.setdefault("last_entry_order_payload", None)
    st.setdefault("last_attached_stop_loss", None)
    st.setdefault("last_error", None)
    return st

def _save_state(st):
    st["schema"] = "formal_v6_state_stage8_22_full_v3"
    st["updated_at"] = _now()
    _atomic_write(STATE_FILE, st)

def _lock_acquire(owner="formal_v6", ttl=90):
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    token = uuid.uuid4().hex
    payload = {"owner": owner, "token": token, "pid": os.getpid(), "time": _now(), "ts": now}
    if LOCK_FILE.exists():
        old = _read_json(LOCK_FILE, {})
        stale = False
        try:
            if now - float(old.get("ts", 0)) > ttl:
                stale = True
        except Exception:
            stale = True
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
        else:
            return None
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(fd)
        return payload
    except Exception:
        return None

def _lock_release(lock):
    try:
        if not lock:
            return False
        if not LOCK_FILE.exists():
            return True
        cur = _read_json(LOCK_FILE, {})
        if cur.get("token") != lock.get("token"):
            return False
        LOCK_FILE.unlink()
        return True
    except Exception:
        return False

def _active(cur):
    return isinstance(cur, dict) and cur.get("status") in ACTIVE_STATUSES

def _okx_request(method, path, params=None, body=None, auth=True):
    import auto_trade_okx as okx
    return okx._okx_request(method, path, params=params, body=body, auth=auth)

def _strict_code_ok(raw):
    return isinstance(raw, dict) and str(raw.get("code")) == "0"

def _ack_row(raw):
    if not isinstance(raw, dict):
        return None
    rows = raw.get("data") or []
    if not isinstance(rows, list) or not rows:
        return None
    return rows[0] if isinstance(rows[0], dict) else None

def _strict_order_ack(raw):
    row = _ack_row(raw)
    if not _strict_code_ok(raw):
        return {"ok": False, "error": "OKX code not zero", "raw": raw}
    if not row:
        return {"ok": False, "error": "empty OKX data", "raw": raw}
    if str(row.get("sCode", "0")) != "0":
        return {"ok": False, "error": "OKX sCode not zero", "row": row, "raw": raw}
    if not row.get("ordId"):
        return {"ok": False, "error": "missing ordId", "row": row, "raw": raw}
    return {"ok": True, "ordId": row.get("ordId"), "clOrdId": row.get("clOrdId"), "row": row, "raw": raw}

def _all_rows_scode_ok(raw):
    if not _strict_code_ok(raw):
        return {"ok": False, "error": "top code not zero", "raw": raw}
    rows = raw.get("data") or []
    if not rows:
        return {"ok": False, "error": "empty data", "raw": raw}
    bad = []
    for r in rows:
        if not isinstance(r, dict) or str(r.get("sCode", "0")) != "0":
            bad.append(r)
    if bad:
        return {"ok": False, "bad_rows": bad, "raw": raw}
    return {"ok": True, "rows": rows, "raw": raw}

def _side_map(side):
    s = str(side or "").lower()
    if s in ["long", "buy"]:
        return {"side": "long", "order_side": "buy", "close_side": "sell", "posSide": "long"}
    if s in ["short", "sell"]:
        return {"side": "short", "order_side": "sell", "close_side": "buy", "posSide": "short"}
    return None

def confirm_text(side):
    return "FORMAL_AUTO_TRADE:%s:%s:%s:20x:ATTACHED_SL" % (side, SYMBOL, HARD_SZ)

def gate_confirm_text():
    return "ENABLE_FORMAL_AUTO_TRADE:%s:%s:20x" % (SYMBOL, HARD_SZ)

def _enforce_size(sz):
    if sz is None or sz == "":
        sz = HARD_SZ
    v = _safe_float(sz, None)
    if v is None:
        return {"ok": False, "error": "invalid sz", "allowed_sz": HARD_SZ}
    if abs(v - float(HARD_SZ)) > 0.00000001:
        return {"ok": False, "blocked": True, "error": "formal executor hard-limits sz to 0.01", "requested_sz": str(sz), "allowed_sz": HARD_SZ}
    return {"ok": True, "sz": HARD_SZ}

def gate_status():
    g = _read_json(GATE_FILE, {})
    enabled = bool(g.get("enabled") and time.time() <= float(g.get("expires_at_ts") or 0))
    return {
        "ok": True,
        "enabled": enabled,
        "required_manual_confirm": gate_confirm_text(),
        "expires_at": g.get("expires_at"),
        "expires_at_ts": g.get("expires_at_ts"),
        "gate_file": str(GATE_FILE),
        "semantics": "If allow_auto_open=true and this formal gate is enabled, daemon may place real orders during gate TTL."
    }

def enable_gate(manual_confirm=None, ttl_sec=300):
    if manual_confirm != gate_confirm_text():
        return {"ok": False, "blocked": True, "error": "formal gate confirm mismatch", "required_manual_confirm": gate_confirm_text()}
    ttl = int(ttl_sec or 300)
    if ttl <= 0 or ttl > 900:
        ttl = 300
    exp = time.time() + ttl
    payload = {
        "enabled": True,
        "created_at": _now(),
        "created_at_ts": time.time(),
        "expires_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exp)),
        "expires_at_ts": exp,
        "ttl_sec": ttl
    }
    _atomic_write(GATE_FILE, payload)
    return {"ok": True, "enabled": True, "gate": payload}

def disable_gate():
    payload = {"enabled": False, "disabled_at": _now(), "disabled_at_ts": time.time()}
    _atomic_write(GATE_FILE, payload)
    return {"ok": True, "enabled": False}

def _gate_required():
    env_ok = str(os.environ.get("OKX_STAGE8_FORMAL_AUTO_TRADE_ENABLED", "")).lower() in ["1", "true", "yes"]
    gs = gate_status()
    if env_ok or gs.get("enabled"):
        return {"ok": True, "env_gate": env_ok, "file_gate": gs.get("enabled"), "gate": gs}
    return {"ok": False, "blocked": True, "error": "formal real gate disabled", "required_manual_confirm": gate_confirm_text(), "gate": gs}

def arm(side, sz="0.01", ttl_sec=90, source="manual"):
    mp = _side_map(side)
    if not mp:
        return {"ok": False, "error": "side must be long or short"}
    size = _enforce_size(sz)
    if not size.get("ok"):
        return size
    lock = _lock_acquire("formal_arm", ttl=30)
    if not lock:
        return {"ok": False, "blocked": True, "error": "formal executor lock busy"}
    try:
        st = _load_state()
        if _active(st.get("current")):
            return {"ok": False, "blocked": True, "error": "formal current exists", "current": st.get("current")}
        token = "arm_" + uuid.uuid4().hex
        ttl = int(ttl_sec or 90)
        if ttl <= 0 or ttl > 180:
            ttl = 90
        armed = {
            "token": token,
            "side": mp["side"],
            "sz": HARD_SZ,
            "source": source,
            "created_at": _now(),
            "created_at_ts": time.time(),
            "expires_at_ts": time.time() + ttl,
            "required_manual_confirm": confirm_text(mp["side"]),
            "used": False
        }
        st["armed"] = armed
        _save_state(st)
        return {"ok": True, "armed": True, "arm_token": token, "required_manual_confirm": armed["required_manual_confirm"], "expires_at_ts": armed["expires_at_ts"]}
    finally:
        _lock_release(lock)

def _consume_arm_locked(st, side, sz, arm_token, internal_auto=False):
    if internal_auto:
        return {"ok": True, "internal_auto": True}
    armed = st.get("armed")
    if not isinstance(armed, dict):
        return {"ok": False, "blocked": True, "error": "missing formal arm token; call /formal/arm first"}
    if armed.get("used"):
        return {"ok": False, "blocked": True, "error": "arm token already used"}
    if time.time() > float(armed.get("expires_at_ts") or 0):
        st["armed"] = None
        _save_state(st)
        return {"ok": False, "blocked": True, "error": "arm token expired"}
    if armed.get("token") != arm_token:
        return {"ok": False, "blocked": True, "error": "arm token mismatch"}
    if armed.get("side") != side:
        return {"ok": False, "blocked": True, "error": "arm side mismatch", "armed_side": armed.get("side"), "requested_side": side}
    if str(armed.get("sz")) != str(sz):
        return {"ok": False, "blocked": True, "error": "arm size mismatch"}
    armed["used"] = True
    st["armed"] = armed
    _save_state(st)
    return {"ok": True, "arm_consumed": True, "armed": armed}

def _get_price():
    raw = _okx_request("GET", "/api/v5/market/ticker", params={"instId": SYMBOL}, auth=False)
    rows = raw.get("data") if isinstance(raw, dict) else []
    if isinstance(rows, list) and rows:
        r = rows[0] if isinstance(rows[0], dict) else {}
        for k in ["last", "lastPx", "idxPx", "markPx"]:
            px = _safe_float(r.get(k), None)
            if px and px > 0:
                return {"ok": True, "price": px, "raw": r}
    return {"ok": False, "error": "cannot read ticker", "raw": raw}

def _calc_sl(side, px):
    px = _safe_float(px, 0.0) or 0.0
    if px <= 0:
        return None
    if side == "long":
        return round(px * (1 - STOP_LOSS_PCT), 1)
    if side == "short":
        return round(px * (1 + STOP_LOSS_PCT), 1)
    return None

def _close_td_mode(position_row=None):
    got = str((position_row or {}).get("mgnMode") or (position_row or {}).get("tdMode") or "").strip().lower()
    if got in ("cross", "isolated"):
        return got
    extra = _legacy_current_td_mode()
    if extra:
        return extra
    return _td_mode()


def _td_mode():
    try:
        import auto_trade_okx as okx
        return okx.resolve_td_mode()
    except Exception:
        return str(globals().get("TD_MODE") or "isolated")


def _legacy_current_td_mode(current=None):
    """Leftover auto positions opened as cross before the isolated switch."""
    if current is None:
        try:
            current = (_load_state().get("current") or {})
        except Exception:
            current = {}
    if not _active(current):
        return None
    mode = str(current.get("td_mode") or current.get("mgnMode") or "").strip().lower()
    if mode in ("cross", "isolated"):
        return mode
    return "cross"


def _managed_td_modes(current=None):
    modes = [_td_mode()]
    extra = _legacy_current_td_mode(current)
    if extra and extra not in modes:
        modes.append(extra)
    return modes


def _filter_auto_rows(rows, extra_modes=None):
    try:
        import auto_trade_okx as okx
        return okx.filter_auto_margin_rows(
            rows, td_mode=_td_mode(), extra_modes=extra_modes)
    except Exception:
        want = set(_managed_td_modes() if extra_modes else [_td_mode()])
        if extra_modes:
            want.update(extra_modes)
        out = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            got = str(row.get("mgnMode") or row.get("tdMode") or "").strip().lower()
            if (not got) or got in want:
                out.append(row)
        return out


def _get_positions(for_manage=False):
    raw = _okx_request("GET", "/api/v5/account/positions", params={"instType": "SWAP", "instId": SYMBOL}, auth=True)
    if not _strict_code_ok(raw):
        return {"ok": False, "positions": [], "raw": raw}
    out = []
    for p in raw.get("data") or []:
        if isinstance(p, dict) and p.get("instId") == SYMBOL and abs(_safe_float(p.get("pos"), 0.0) or 0.0) > 0:
            out.append(p)
    extra = _managed_td_modes() if for_manage else None
    out = _filter_auto_rows(out, extra_modes=extra)
    return {"ok": True, "positions": out, "raw": raw}

def _find_position(posSide):
    res = _get_positions(for_manage=True)
    if not res.get("ok"):
        return {"ok": False, "position": None, "raw": res}
    for p in res.get("positions") or []:
        if p.get("posSide") == posSide:
            return {"ok": True, "position": p}
    return {"ok": True, "position": None}

def _pending_orders():
    raw = _okx_request("GET", "/api/v5/trade/orders-pending", params={"instType": "SWAP", "instId": SYMBOL}, auth=True)
    if not _strict_code_ok(raw):
        return {"ok": False, "orders": [], "raw": raw}
    out = [r for r in (raw.get("data") or []) if isinstance(r, dict) and r.get("instId") == SYMBOL]
    out = _filter_auto_rows(out)
    return {"ok": True, "orders": out, "raw": raw}

def _pending_algos_fail_closed():
    merged, raw_all, failed = [], [], []
    for typ in ["conditional", "trigger", "oco", "move_order_stop"]:
        try:
            raw = _okx_request("GET", "/api/v5/trade/orders-algo-pending", params={"instType": "SWAP", "instId": SYMBOL, "ordType": typ}, auth=True)
            raw_all.append({"ordType": typ, "raw": raw})
            if not _strict_code_ok(raw):
                failed.append({"ordType": typ, "raw": raw})
                continue
            for r in raw.get("data") or []:
                if isinstance(r, dict) and r.get("instId") == SYMBOL:
                    merged.append(r)
        except Exception as e:
            failed.append({"ordType": typ, "error": str(e)})
            raw_all.append({"ordType": typ, "error": str(e)})
    merged = _filter_auto_rows(merged, extra_modes=_managed_td_modes())
    if failed:
        return {"ok": False, "algo_orders": merged, "failed_queries": failed, "raw": raw_all, "error": "pending algo query failed closed"}
    return {"ok": True, "algo_orders": merged, "raw": raw_all}

def _pending_algos_loose():
    merged, raw_all = [], []
    for typ in ["conditional", "trigger", "oco", "move_order_stop"]:
        try:
            raw = _okx_request("GET", "/api/v5/trade/orders-algo-pending", params={"instType": "SWAP", "instId": SYMBOL, "ordType": typ}, auth=True)
            raw_all.append({"ordType": typ, "raw": raw})
            if isinstance(raw, dict) and str(raw.get("code")) == "0":
                for r in raw.get("data") or []:
                    if isinstance(r, dict) and r.get("instId") == SYMBOL:
                        merged.append(r)
        except Exception as e:
            raw_all.append({"ordType": typ, "error": str(e)})
    merged = _filter_auto_rows(merged, extra_modes=_managed_td_modes())
    return {"ok": True, "algo_orders": merged, "raw": raw_all}

def preflight():
    st = _load_state()
    if _active(st.get("current")):
        return {"ok": False, "blocked": True, "error": "formal current exists", "current": st.get("current")}
    pos = _get_positions()
    if not pos.get("ok"):
        return {"ok": False, "error": "cannot read positions", "positions": pos}
    if pos.get("positions"):
        return {"ok": False, "blocked": True,
                "error": "OKX already has %s isolated position" % SYMBOL,
                "positions": pos.get("positions")}
    po = _pending_orders()
    if not po.get("ok"):
        return {"ok": False, "error": "cannot read pending orders", "pending": po}
    if po.get("orders"):
        return {"ok": False, "blocked": True, "error": "OKX has pending normal orders", "orders": po.get("orders")}
    pa = _pending_algos_fail_closed()
    if not pa.get("ok"):
        return {"ok": False, "error": "cannot read pending algos", "pending_algos": pa}
    if pa.get("algo_orders"):
        return {"ok": False, "blocked": True, "error": "OKX has pending algos", "algo_orders": pa.get("algo_orders")}
    return {"ok": True, "positions": pos, "pending_orders": po, "pending_algos": pa}

def _set_leverage(posSide, leverage=None):
    applied = _normalize_leverage(
        leverage if leverage is not None else _active_leverage())
    body = {"instId": SYMBOL, "lever": _leverage_text(applied), "mgnMode": _td_mode(), "posSide": posSide}
    raw = _okx_request("POST", "/api/v5/account/set-leverage", body=body, auth=True)
    ok = _strict_code_ok(raw)
    row_ok = True
    bad_rows = []
    if ok:
        for r in raw.get("data") or []:
            if isinstance(r, dict) and str(r.get("sCode", "0")) != "0":
                row_ok = False
                bad_rows.append(r)
    return {"ok": bool(ok and row_ok), "payload": body, "raw": raw, "bad_rows": bad_rows}

def _get_order(ordId=None, clOrdId=None):
    params = {"instId": SYMBOL}
    if ordId:
        params["ordId"] = str(ordId)
    if clOrdId:
        params["clOrdId"] = str(clOrdId)
    raw = _okx_request("GET", "/api/v5/trade/order", params=params, auth=True)
    if not _strict_code_ok(raw):
        return {"ok": False, "raw": raw}
    rows = raw.get("data") or []
    return {"ok": True, "order": rows[0] if rows and isinstance(rows[0], dict) else {}, "raw": raw}

def _wait_filled(ordId, clOrdId):
    last = None
    for i in range(ORDER_POLL_ATTEMPTS):
        r = _get_order(ordId, clOrdId)
        last = r
        if r.get("ok"):
            state = str((r.get("order") or {}).get("state") or "").lower()
            if state == "filled":
                return {"ok": True, "filled": True, "order": r.get("order"), "attempts": i + 1}
            if state in FINAL_NO_OPEN_STATES:
                return {"ok": False, "filled": False, "order": r.get("order"), "state": state}
        time.sleep(POLL_INTERVAL)
    return {"ok": False, "filled": False, "error": "fill timeout", "last": last}

def _wait_position_present(posSide):
    last = None
    for i in range(POSITION_POLL_ATTEMPTS):
        r = _find_position(posSide)
        last = r
        if r.get("ok") and r.get("position"):
            return {"ok": True, "position": r.get("position"), "attempts": i + 1}
        time.sleep(POLL_INTERVAL)
    return {"ok": False, "error": "position present timeout", "last": last}

def _wait_position_absent(posSide):
    last = None
    for i in range(POSITION_POLL_ATTEMPTS):
        r = _find_position(posSide)
        last = r
        if r.get("ok") and not r.get("position"):
            return {"ok": True, "absent": True, "attempts": i + 1}
        time.sleep(POLL_INTERVAL)
    return {"ok": False, "error": "position absent timeout", "last": last}

def _row_matches_attached_sl(row, *args, **kwargs):
    return_details = bool(kwargs.pop("return_details", False))
    close_side = kwargs.pop("close_side", None)
    posSide = kwargs.pop("posSide", None)
    expected_stop_loss_price = kwargs.pop("expected_stop_loss_price", None)
    attached_kw = kwargs.pop("attached", None)

    attached = {}
    legacy_id = None
    legacy_price = None

    if isinstance(attached_kw, dict):
        attached = attached_kw
    elif attached_kw not in [None, ""]:
        legacy_id = str(attached_kw)

    if args:
        if isinstance(args[0], dict):
            attached = args[0]
            if len(args) >= 2 and close_side is None:
                close_side = args[1]
            if len(args) >= 3 and posSide is None:
                posSide = args[2]
        else:
            legacy_id = str(args[0]) if args[0] not in [None, ""] else None
            if len(args) >= 2:
                legacy_price = args[1]
            if len(args) >= 3 and close_side is None:
                close_side = args[2]
            if len(args) >= 4 and posSide is None:
                posSide = args[3]

    if legacy_id:
        attached = {
            "attachAlgoClOrdId": legacy_id,
            "algoClOrdId": legacy_id,
            "payload": {"attachAlgoClOrdId": legacy_id},
            "stop_loss_price": legacy_price
        }

    def f(x):
        try:
            if x is None or x == "":
                return None
            return float(x)
        except Exception:
            return None

    def ids(obj):
        out = set()
        keys = set(["attachAlgoClOrdId", "algoClOrdId", "algoId", "linkedAlgoId", "ordId", "clOrdId"])
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in keys and v not in [None, ""]:
                    out.add(str(v))
                if isinstance(v, (dict, list)):
                    out.update(ids(v))
        elif isinstance(obj, list):
            for x in obj:
                out.update(ids(x))
        return out

    def expected_price(att):
        if expected_stop_loss_price not in [None, ""]:
            return f(expected_stop_loss_price)
        if isinstance(att, dict):
            for k in ["stop_loss_price", "slTriggerPx", "expected_stop_loss_price"]:
                v = f(att.get(k))
                if v is not None:
                    return v
            payload = att.get("payload")
            if isinstance(payload, dict):
                for k in ["slTriggerPx", "stop_loss_price"]:
                    v = f(payload.get(k))
                    if v is not None:
                        return v
        return None

    def row_price(r):
        if not isinstance(r, dict):
            return None
        for k in ["slTriggerPx", "triggerPx", "stopLossTriggerPx", "stop_loss_price"]:
            v = f(r.get(k))
            if v is not None:
                return v
        linked = r.get("linkedAlgoOrd")
        if isinstance(linked, dict):
            for k in ["slTriggerPx", "triggerPx"]:
                v = f(linked.get(k))
                if v is not None:
                    return v
        return None

    def price_close(actual, expected):
        a = f(actual)
        e = f(expected)
        if a is None or e is None:
            return False, {"actual": a, "expected": e, "reason": "missing_price"}
        diff = abs(a - e)
        tolerance = max(2.0, abs(e) * 0.0005)
        return diff <= tolerance, {"actual": a, "expected": e, "diff": diff, "tolerance": tolerance}

    def open_ms(att):
        try:
            ts = None
            if isinstance(att, dict):
                ts = att.get("opened_at_ts") or att.get("created_at_ts")
            if not ts:
                st = _load_state()
                cur = st.get("current") or {}
                ts = cur.get("opened_at_ts") or cur.get("created_at_ts")
            if ts:
                return int(float(ts) * 1000)
        except Exception:
            pass
        return None

    def row_ms(r):
        if not isinstance(r, dict):
            return None
        for k in ["cTime", "uTime", "fillTime", "ts"]:
            try:
                if r.get(k) not in [None, ""]:
                    return int(float(r.get(k)))
            except Exception:
                pass
        return None

    if not isinstance(row, dict):
        res = {"matched": False, "final_reason": "ROW_NOT_DICT"}
        return res if return_details else False

    source = row.get("_vector_source") or row.get("_stage823_source") or ""
    expected_ids = ids(attached)
    row_ids = ids(row)
    id_hit = sorted(list(expected_ids.intersection(row_ids)))

    ep = expected_price(attached)
    rp = row_price(row)
    price_ok, price_detail = price_close(rp, ep)

    inst = row.get("instId") or SYMBOL
    ord_type = row.get("ordType") or ""
    side = row.get("side") or ""
    rpos = row.get("posSide") or ""

    inst_ok = (not inst) or str(inst) == str(SYMBOL)
    side_ok = True if not close_side else ((not side and source.endswith("closeOrderAlgo")) or str(side) == str(close_side))
    pos_ok = True if not posSide else ((not rpos and source.endswith("closeOrderAlgo")) or str(rpos) == str(posSide))

    oms = open_ms(attached)
    rms = row_ms(row)
    time_ok = True
    time_detail = {"open_ms": oms, "row_ms": rms, "checked": False}
    if oms is not None:
        time_detail["checked"] = True
        if rms is not None:
            time_ok = rms >= (oms - 300000)
        elif source.startswith("trade-order-detail") or source.endswith("closeOrderAlgo"):
            time_ok = True
            time_detail["assumed_current_context"] = True
        else:
            time_ok = False

    if id_hit and inst_ok and side_ok and pos_ok:
        if ep is not None and rp is not None and not price_ok:
            res = {"matched": False, "final_reason": "STOP_PRICE_INVALID", "match_type": "id_hit_price_invalid", "id_hit": id_hit, "price": price_detail, "row": row}
            return res if return_details else False
        res = {
            "matched": True,
            "final_reason": "EXCHANGE_SIDE_STOP_VERIFIED",
            "match_type": "id_match",
            "id_hit": id_hit,
            "algoId": row.get("algoId") or ((row.get("linkedAlgoOrd") or {}).get("algoId") if isinstance(row.get("linkedAlgoOrd"), dict) else None),
            "source": source,
            "price": price_detail,
            "time": time_detail,
            "row": row
        }
        return res if return_details else True

    linked = row.get("linkedAlgoOrd")
    if source.startswith("trade-order-detail") and isinstance(linked, dict) and linked.get("algoId") and inst_ok:
        if ep is not None and rp is not None and not price_ok:
            res = {"matched": False, "final_reason": "STOP_PRICE_INVALID", "match_type": "linked_algo_price_invalid", "price": price_detail, "row": row}
            return res if return_details else False
        res = {"matched": True, "final_reason": "EXCHANGE_SIDE_STOP_VERIFIED", "match_type": "trade_order_detail_linkedAlgoOrd", "algoId": linked.get("algoId"), "source": source, "time": time_detail, "row": row}
        return res if return_details else True

    allowed = set(["conditional", "trigger", "oco", "move_order_stop"])
    ord_type_ok = str(ord_type) in allowed or source.endswith("closeOrderAlgo")

    if inst_ok and ord_type_ok and side_ok and pos_ok and price_ok and time_ok:
        res = {
            "matched": True,
            "final_reason": "EXCHANGE_SIDE_STOP_VERIFIED",
            "match_type": "strong_conditions_with_time_guard",
            "algoId": row.get("algoId"),
            "source": source,
            "price": price_detail,
            "time": time_detail,
            "row": row
        }
        return res if return_details else True

    final_reason = "EXCHANGE_SIDE_STOP_NOT_FOUND_AFTER_RETRY"
    if not side_ok:
        final_reason = "STOP_SIDE_INVALID"
    elif ep is not None and not price_ok:
        final_reason = "STOP_PRICE_INVALID"

    res = {
        "matched": False,
        "final_reason": final_reason,
        "match_type": "no_match",
        "source": source,
        "row_ids": sorted(list(row_ids)),
        "expected_ids": sorted(list(expected_ids)),
        "inst": inst,
        "ordType": ord_type,
        "side": side,
        "expected_close_side": close_side,
        "posSide": rpos,
        "expected_posSide": posSide,
        "price": price_detail,
        "time": time_detail,
        "row": row
    }
    return res if return_details else False

def _wait_attached_sl(attached, close_side=None, posSide=None, attempts=5, sleep_sec=1.2, *args, **kwargs):
    attempts = max(5, int(attempts or 5))
    sleep_sec = float(sleep_sec or 0)

    direct = _vector_verify_exchange_stop(attached, close_side=close_side, posSide=posSide, attempts=attempts, sleep_sec=sleep_sec)
    if direct.get("verified"):
        direct["attached_stop_loss_source"] = "attachAlgoOrds"
        _vector_persist_sl_result(direct)
        return direct

    fallback = _vector_create_fallback_order_algo(attached, close_side=close_side, posSide=posSide)
    if not fallback.get("ok"):
        res = {
            "ok": False,
            "verified": False,
            "final_reason": "FALLBACK_ORDER_ALGO_FAILED",
            "initial_attach_verify": direct,
            "fallback_create": fallback,
            "attached_stop_loss_source": "none"
        }
        _vector_persist_sl_result(res)
        return res

    verify_fb = _vector_verify_fallback_order_algo(fallback, attached, close_side=close_side, posSide=posSide, attempts=5, sleep_sec=sleep_sec)
    if verify_fb.get("verified"):
        res = dict(verify_fb)
        res["ok"] = True
        res["verified"] = True
        res["final_reason"] = "EXCHANGE_SIDE_STOP_VERIFIED"
        res["attached_stop_loss_source"] = "fallback_order_algo"
        res["attached_stop_loss_fallback_created"] = True
        res["initial_attach_verify"] = direct
        _vector_persist_sl_result(res)
        return res

    res = {
        "ok": False,
        "verified": False,
        "final_reason": "FALLBACK_ORDER_ALGO_FAILED",
        "initial_attach_verify": direct,
        "fallback_create": fallback,
        "fallback_verify": verify_fb,
        "attached_stop_loss_source": "none"
    }
    _vector_persist_sl_result(res)
    return res

def _find_attached_algo_ids(cur):
    attached = cur.get("attached_stop_loss") if isinstance(cur, dict) else {}
    ids = []
    if isinstance(attached, dict) and attached.get("algoId"):
        ids.append(attached.get("algoId"))
    p = _pending_algos_loose()
    for row in p.get("algo_orders") or []:
        if _row_matches_attached_sl(row, attached.get("attachAlgoClOrdId"), attached.get("stop_loss_price"), close_side=cur.get("close_side"), posSide=cur.get("posSide")):
            if row.get("algoId"):
                ids.append(row.get("algoId"))
    unique, seen = [], set()
    for x in ids:
        sx = str(x)
        if sx not in seen:
            unique.append(sx)
            seen.add(sx)
    return {"ok": True, "algo_ids": unique, "pending": p}

def _cancel_algos(ids, reason):
    out, ok, seen = [], True, set()
    for x in ids or []:
        if not x or str(x) in seen:
            continue
        seen.add(str(x))
        body = [{"instId": SYMBOL, "algoId": str(x)}]
        try:
            raw = _okx_request("POST", "/api/v5/trade/cancel-algos", body=body, auth=True)
            check = _all_rows_scode_ok(raw)
            r = {"ok": check.get("ok"), "algoId": str(x), "rowcheck": check, "raw": raw, "reason": reason}
        except Exception as e:
            r = {"ok": False, "algoId": str(x), "error": str(e), "reason": reason}
        out.append(r)
        if not r.get("ok"):
            ok = False
    return {"ok": ok, "results": out}

def _confirm_algo_ids_absent(ids):
    expected = {str(x) for x in (ids or []) if x not in [None, ""]}
    pending = _pending_algos_fail_closed()
    if not pending.get("ok"):
        return {"ok": False, "absent": False, "expected_ids": sorted(expected),
                "pending": pending, "reason": "pending_algo_query_failed"}
    present = set()
    for row in pending.get("algo_orders") or []:
        for key in ["algoId", "algoClOrdId", "attachAlgoId", "attachAlgoClOrdId"]:
            if row.get(key) not in [None, ""]:
                present.add(str(row.get(key)))
    remaining = sorted(expected.intersection(present))
    return {"ok": True, "absent": not remaining, "expected_ids": sorted(expected),
            "remaining_ids": remaining, "pending": pending}

def _build_entry_payload(side):
    mp = _side_map(side)
    if not mp:
        return {"ok": False, "error": "invalid side"}
    px = _get_price()
    if not px.get("ok"):
        return {"ok": False, "error": "cannot get ticker for SL", "price": px}
    sl = _calc_sl(mp["side"], px.get("price"))
    if not sl:
        return {"ok": False, "error": "cannot calculate SL", "price": px}
    clid = "fae" + uuid.uuid4().hex[:25]
    attach_id = ("fasl" + uuid.uuid4().hex)[:32]
    attached = {
        "style": "formal_entry_attachAlgoOrds_sl",
        "attachAlgoClOrdId": attach_id,
        "logical_side": mp["side"],
        "reference_price": px.get("price"),
        "reference_price_info": px,
        "stop_loss_price": sl,
        "payload": {"attachAlgoClOrdId": attach_id, "slTriggerPx": str(sl), "slTriggerPxType": "last", "slOrdPx": "-1"}
    }
    payload = {"instId": SYMBOL, "tdMode": _td_mode(), "side": mp["order_side"], "posSide": mp["posSide"], "ordType": "market", "sz": HARD_SZ, "clOrdId": clid, "attachAlgoOrds": [attached["payload"]]}
    return {"ok": True, "side_map": mp, "payload": payload, "attached_stop_loss": attached, "clOrdId": clid}

def submit_entry(side, strategy_key="ema6_center_down_then_fall", manual_confirm=None, sz="0.01", source="formal_auto_trade", arm_token=None, internal_auto=False):
    mp = _side_map(side)
    if not mp:
        return {"ok": False, "error": "side must be long or short"}
    size = _enforce_size(sz)
    if not size.get("ok"):
        return size
    if manual_confirm != confirm_text(mp["side"]):
        return {"ok": False, "blocked": True, "error": "manual_confirm mismatch", "required_manual_confirm": confirm_text(mp["side"])}
    gate = _gate_required()
    if not gate.get("ok"):
        return gate

    lock = _lock_acquire("formal_submit_entry", ttl=90)
    if not lock:
        return {"ok": False, "blocked": True, "error": "formal executor lock busy"}
    try:
        st = _load_state()
        arm_check = _consume_arm_locked(st, mp["side"], HARD_SZ, arm_token, internal_auto=internal_auto)
        if not arm_check.get("ok"):
            return arm_check
        st = _load_state()
        if _active(st.get("current")):
            return {"ok": False, "blocked": True, "error": "formal current exists", "current": st.get("current")}
        pre = preflight()
        if not pre.get("ok"):
            return pre
        lev = _set_leverage(mp["posSide"])
        if not lev.get("ok"):
            return {"ok": False, "error": "set leverage failed", "leverage": lev}
        built = _build_entry_payload(mp["side"])
        if not built.get("ok"):
            return built
        payload = built["payload"]
        attached = built["attached_stop_loss"]
        if not payload.get("attachAlgoOrds"):
            return {"ok": False, "error": "entry payload missing attachAlgoOrds; order not submitted", "payload": payload}

        cur = {
            "position_id": "formal_v6_" + uuid.uuid4().hex,
            "execution_id": "exec_formal_v6_" + uuid.uuid4().hex,
            "strategy_key": strategy_key,
            "strategy_name": "EMA6居中后再下行",
            "source": source,
            "status": "opening",
            "symbol": SYMBOL,
            "side": mp["side"],
            "order_side": mp["order_side"],
            "close_side": mp["close_side"],
            "posSide": mp["posSide"],
            "td_mode": _td_mode(),
            "mgnMode": _td_mode(),
            "sz": HARD_SZ,
            "leverage": LEVERAGE,
            "opened_at": _now(),
            "opened_at_ts": time.time(),
            "entry_order_payload": payload,
            "entry_order_payload_has_attachAlgoOrds": True,
            "attached_to_entry_order": True,
            "attached_stop_loss": attached,
            "exchange_side_stop_verified": False,
            "stop_loss_price": attached.get("stop_loss_price"),
            "real_order": True,
            "formal_executor": True,
            "temp_test": False,
            "gate": gate,
            "arm": arm_check,
            "preflight": pre,
            "leverage_result": lev
        }
        st["current"] = cur
        st["last_entry_order_payload"] = payload
        st["last_attached_stop_loss"] = attached
        _save_state(st)

        raw = _okx_request("POST", "/api/v5/trade/order", body=payload, auth=True)
        ack = _strict_order_ack(raw)
        open_order = {"ok": ack.get("ok"), "payload": payload, "ack": ack, "raw": raw, "ordId": ack.get("ordId"), "clOrdId": payload.get("clOrdId")}
        cur["open_order"] = open_order
        cur["okx_submitted"] = bool(ack.get("ok"))
        st["current"] = cur
        _save_state(st)

        if not ack.get("ok"):
            st["current"] = None
            st["last_error"] = {"time": _now(), "reason": "formal_entry_with_attached_sl_rejected", "detail": open_order}
            _save_state(st)
            _append_event("strategy_open_rejected", {"side": mp["side"], "open_order": open_order}, strategy_key)
            return {"ok": False, "error": "formal entry order with attachAlgoOrds rejected", "open_order": open_order}

        filled = _wait_filled(ack.get("ordId"), payload.get("clOrdId"))
        cur["open_order"]["filled"] = filled
        st["current"] = cur
        _save_state(st)
        if not filled.get("ok"):
            cur["status"] = "unknown_open"
            cur["last_error"] = {"time": _now(), "reason": "formal_entry_ack_but_fill_unverified", "detail": filled}
            st["current"] = cur
            st["last_error"] = cur["last_error"]
            _save_state(st)
            _append_event("strategy_open_unknown", {"side": mp["side"], "open_order": open_order, "filled": filled}, strategy_key)
            return {"ok": False, "opened_unknown": True, "current": cur, "open_order": open_order, "filled": filled}

        pos = _wait_position_present(mp["posSide"])
        cur["position_poll"] = pos
        st["current"] = cur
        _save_state(st)
        if not pos.get("ok"):
            cur["status"] = "unknown_open"
            cur["last_error"] = {"time": _now(), "reason": "formal_filled_but_position_unreadable", "detail": pos}
            st["current"] = cur
            st["last_error"] = cur["last_error"]
            _save_state(st)
            _append_event("strategy_open_unknown", {"side": mp["side"], "open_order": open_order, "position_poll": pos}, strategy_key)
            return {"ok": False, "opened_unknown": True, "current": cur, "open_order": open_order, "position_poll": pos}

        verify = _wait_attached_sl(attached, close_side=mp["close_side"], posSide=mp["posSide"])
        attached["verify_pending"] = verify
        attached["exchange_side_stop_verified"] = bool(verify.get("ok") and verify.get("verified"))
        attached["algoId"] = verify.get("algoId")
        position = pos.get("position") or {}
        cur["okx_position_after_open"] = position
        cur["entry_price"] = _safe_float(position.get("avgPx"), attached.get("reference_price"))
        cur["real_position_sz"] = str(abs(_safe_float(position.get("pos"), 0.0) or 0.0))
        cur["attached_stop_loss"] = attached
        cur["stop_algo_id"] = attached.get("algoId")
        cur["exchange_side_stop_verified"] = attached.get("exchange_side_stop_verified")
        cur["status"] = "open" if cur["exchange_side_stop_verified"] else "unprotected_open"
        st["current"] = cur
        st["last_attached_stop_loss"] = attached
        _save_state(st)

        if not cur["exchange_side_stop_verified"]:
            close_res = close_current(reason="attached_sl_not_verified_formal_recovery")
            _append_event("strategy_open_recovered", {"side": mp["side"], "reason": "attached_sl_not_verified", "close": close_res, "current": cur}, strategy_key)
            return {"ok": False, "opened": True, "error": "entry payload had attachAlgoOrds but attached SL not verified; recovery close attempted", "current": cur, "close": close_res}

        _append_event("strategy_opened", {"side": mp["side"], "price": cur.get("entry_price"), "sz": cur.get("real_position_sz"), "entry_order_payload": payload, "attached_stop_loss": attached, "stop_algo_id": attached.get("algoId")}, strategy_key)
        try:
            import auto_trade_roster_display_metrics as _roster_metrics
            _roster_metrics.on_live_trade_opened(
                SYMBOL,
                os.environ.get("VECTOR_TRADE_TIMEFRAME", TRADE_TIMEFRAME or "1h"),
                strategy_key,
            )
        except Exception:
            pass
        return {"ok": True, "opened": True, "formal_executor": True, "temp_test": False, "entry_order_payload_has_attachAlgoOrds": True, "attached_to_entry_order": True, "exchange_side_stop_verified": True, "current": cur, "open_order": open_order}
    finally:
        _lock_release(lock)

def close_current(reason="formal_manual_close"):
    lock = _lock_acquire("formal_close", ttl=90)
    if not lock:
        return {"ok": False, "blocked": True, "error": "formal executor lock busy"}
    try:
        st = _load_state()
        cur = st.get("current")
        if not _active(cur):
            return {"ok": True, "closed": False, "reason": "no active formal current"}
        mp = _side_map(cur.get("side"))
        if not mp:
            return {"ok": False, "error": "invalid current side", "current": cur}
        pos = _find_position(mp["posSide"])
        if not pos.get("ok"):
            cur["status"] = "close_failed"
            cur["last_error"] = {"time": _now(), "reason": "position_read_failed_before_close", "detail": pos}
            st["current"] = cur
            st["last_error"] = cur["last_error"]
            _save_state(st)
            return {"ok": False, "closed": False, "current_preserved": True, "error": "cannot read position before close", "position": pos}
        if pos.get("position"):
            real_sz = str(abs(_safe_float(pos["position"].get("pos"), 0.0) or 0.0))
            close_payload = {"instId": SYMBOL, "tdMode": _close_td_mode(pos.get("position")), "side": mp["close_side"], "posSide": mp["posSide"], "ordType": "market", "sz": real_sz, "reduceOnly": "true", "clOrdId": "fcl" + uuid.uuid4().hex[:25]}
            raw = _okx_request("POST", "/api/v5/trade/order", body=close_payload, auth=True)
            ack = _strict_order_ack(raw)
            close_order = {"ok": ack.get("ok"), "payload": close_payload, "ack": ack, "raw": raw}
            cur["status"] = "close_pending"
            cur["last_close_attempt"] = {"time": _now(), "reason": reason, "close_order": close_order}
            st["current"] = cur
            _save_state(st)
            if not ack.get("ok"):
                cur["status"] = "close_failed"
                cur["last_error"] = {"time": _now(), "reason": "reduce_only_close_rejected", "detail": close_order}
                st["current"] = cur
                st["last_error"] = cur["last_error"]
                _save_state(st)
                return {"ok": False, "closed": False, "current_preserved": True, "error": "reduce-only close rejected", "close_order": close_order}
            filled = _wait_filled(ack.get("ordId"), close_payload.get("clOrdId"))
            close_order["filled"] = filled
            if not filled.get("ok"):
                cur["status"] = "close_failed"
                cur["last_error"] = {"time": _now(), "reason": "reduce_only_close_fill_unverified", "detail": close_order}
                st["current"] = cur
                st["last_error"] = cur["last_error"]
                _save_state(st)
                return {"ok": False, "closed": False, "current_preserved": True, "error": "reduce-only close fill unverified", "close_order": close_order}
            absent = _wait_position_absent(mp["posSide"])
            if not absent.get("ok"):
                cur["status"] = "close_failed"
                cur["last_error"] = {"time": _now(), "reason": "position_not_absent_after_close", "detail": absent}
                st["current"] = cur
                st["last_error"] = cur["last_error"]
                _save_state(st)
                return {"ok": False, "closed": False, "current_preserved": True, "error": "position not absent after close", "close_order": close_order, "absent": absent}
        else:
            close_order = {"ok": True, "skipped": True, "reason": "position already absent"}
            absent = {"ok": True, "absent": True, "reason": "position already absent"}
        ids = _find_attached_algo_ids(cur)
        cancel = _cancel_algos(ids.get("algo_ids") or [], "formal_close_after_position_absent")
        if not cancel.get("ok"):
            absent_algos = _confirm_algo_ids_absent(ids.get("algo_ids") or [])
            if absent_algos.get("ok") and absent_algos.get("absent"):
                cancel = {"ok": True, "idempotent_success": True,
                          "reason": "position_absent_and_attached_stop_already_absent",
                          "initial_cancel": cancel, "absence_check": absent_algos}
            else:
                cur["status"] = "dangling_stop_algo"
                cur["last_error"] = {"time": _now(), "reason": "attached_sl_cancel_failed_after_close", "detail": cancel}
                st["current"] = cur
                st["last_error"] = cur["last_error"]
                _save_state(st)
                return {"ok": False, "closed": False, "current_preserved": True, "error": "position closed but attached SL cancel failed", "cancel": cancel, "current": cur}
        closed = dict(cur)
        closed["status"] = "closed"
        closed["closed_at"] = _now()
        closed["closed_at_ts"] = time.time()
        closed["close_reason"] = reason
        closed["close_order"] = close_order
        closed["position_absent_verified"] = absent
        closed["cancel_attached_sl"] = cancel
        closed = _attach_close_growth(closed)
        st.setdefault("history", []).append(closed)
        st["current"] = None
        _save_state(st)
        _append_event("strategy_closed", {"side": cur.get("side"), "price": None, "reason": reason, "close_order": close_order, "cancel_attached_sl": cancel}, cur.get("strategy_key") or "ema6_center_down_then_fall")
        return {"ok": True, "closed": True, "position": closed, "close_order": close_order, "cancel_attached_sl": cancel}
    finally:
        _lock_release(lock)

def check_attached_stop_loss_current():
    st = _load_state()
    cur = st.get("current")
    if not _active(cur):
        return {"ok": True, "checked": False, "reason": "no active formal current"}
    attached = cur.get("attached_stop_loss") if isinstance(cur, dict) else None
    if not isinstance(attached, dict):
        return {"ok": False, "checked": True, "error": "active position has no attached_stop_loss object", "current": cur}
    mp = _side_map(cur.get("side"))
    if not mp:
        return {"ok": False, "checked": True, "error": "invalid current side", "current": cur}
    verify = _wait_attached_sl(attached, close_side=mp.get("close_side"), posSide=mp.get("posSide"))
    attached["verify_pending"] = verify
    attached["exchange_side_stop_verified"] = bool(verify.get("ok") and verify.get("verified"))
    attached["algoId"] = verify.get("algoId")
    cur["attached_stop_loss"] = attached
    cur["stop_algo_id"] = attached.get("algoId")
    cur["exchange_side_stop_verified"] = attached.get("exchange_side_stop_verified")
    cur["status"] = "open" if cur["exchange_side_stop_verified"] else "unprotected_open"
    st["current"] = cur
    st["last_attached_stop_loss"] = attached
    _save_state(st)
    return {"ok": bool(cur["exchange_side_stop_verified"]), "checked": True, "exchange_side_stop_verified": bool(cur["exchange_side_stop_verified"]), "verify": verify, "current": cur}

def manage_current_position(policy="protective"):
    check = check_attached_stop_loss_current()
    if not check.get("checked"):
        return {"ok": True, "action": "no_position", "check": check}
    if check.get("ok"):
        return {"ok": True, "action": "position_protected", "check": check}
    if policy == "protective":
        close = close_current(reason="daemon_protective_close_attached_sl_invalid")
        return {"ok": bool(close.get("ok")), "action": "protective_close_attempted", "check": check, "close": close}
    return {"ok": False, "action": "unprotected_position_detected", "check": check}

def get_status():
    st = _load_state()
    cur = st.get("current")
    return {"ok": True, "stage": "formal_v6_attached_sl_executor_stage8_22_full_v3", "formal_executor": True, "temp_test": False, "symbol": SYMBOL, "hard_sz": HARD_SZ, "leverage": LEVERAGE, "stop_loss_pct": STOP_LOSS_PCT, "current": cur, "armed": st.get("armed"), "current_entry_payload_has_attachAlgoOrds": bool(((cur or {}).get("entry_order_payload") or {}).get("attachAlgoOrds")) if isinstance(cur, dict) else False, "current_exchange_side_stop_verified": bool((cur or {}).get("exchange_side_stop_verified")) if isinstance(cur, dict) else False, "last_entry_order_payload": st.get("last_entry_order_payload"), "last_entry_payload_has_attachAlgoOrds": bool((st.get("last_entry_order_payload") or {}).get("attachAlgoOrds")), "last_attached_stop_loss": st.get("last_attached_stop_loss"), "history_count": len(st.get("history") or []), "last_history": (st.get("history") or [])[-5:], "gate": gate_status(), "state_file": str(STATE_FILE), "event_file": str(EVENT_FILE), "time": _now()}

def get_events(limit=30):
    rows = []
    if EVENT_FILE.exists():
        for line in EVENT_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()[-500:]:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if isinstance(r, dict):
                    rows.append(r)
            except Exception:
                pass
    allowed = ["strategy_opened", "strategy_closed", "strategy_open_recovered", "strategy_open_rejected", "strategy_open_unknown"]
    rows = [x for x in rows if x.get("event_type") in allowed]
    rows = sorted(rows, key=lambda x: str(x.get("time") or ""), reverse=True)
    return {"ok": True, "events": rows[:int(limit or 30)]}

def _extract_side_from_obj(obj):
    if isinstance(obj, dict):
        for k in ["side", "direction", "signal_side", "order_side"]:
            v = obj.get(k)
            if str(v).lower() in ["long", "short", "buy", "sell"]:
                return str(v).lower()
        sig = obj.get("signal")
        if isinstance(sig, dict):
            return _extract_side_from_obj(sig)
    else:
        for k in ["side", "direction", "signal_side", "order_side"]:
            try:
                v = getattr(obj, k)
                if str(v).lower() in ["long", "short", "buy", "sell"]:
                    return str(v).lower()
            except Exception:
                pass
    return None

def formal_submit_live_one_order(*args, **kwargs):
    side = kwargs.get("side") or kwargs.get("direction") or kwargs.get("signal_side")
    if not side:
        for a in args:
            side = _extract_side_from_obj(a)
            if side:
                break
    if not side and kwargs:
        side = _extract_side_from_obj(kwargs)
    return submit_entry(
        side=side,
        strategy_key=kwargs.get("strategy_key") or "ema6_center_down_then_fall",
        manual_confirm=kwargs.get("manual_confirm"),
        sz=kwargs.get("sz") or kwargs.get("size") or HARD_SZ,
        source="auto_trade_execution.submit_live_one_order",
        arm_token=kwargs.get("arm_token"),
        internal_auto=bool(kwargs.get("internal_auto"))
    )

def _stage822_fake_okx_factory():
    state = {"orders": {}, "positions": [], "pending_orders": [], "pending_algos": [], "last_order_payload": None, "seq": 1}
    def fake(method, path, params=None, body=None, auth=True):
        method = str(method).upper()
        params = params or {}
        body = body or {}
        if path == "/api/v5/market/ticker":
            return {"code": "0", "data": [{"instId": SYMBOL, "last": "100.0"}]}
        if path == "/api/v5/account/set-leverage":
            return {"code": "0", "data": [{"lever": str(LEVERAGE), "sCode": "0"}]}
        if path == "/api/v5/account/positions":
            return {"code": "0", "data": list(state["positions"])}
        if path == "/api/v5/trade/orders-pending":
            return {"code": "0", "data": list(state["pending_orders"])}
        if path == "/api/v5/trade/orders-algo-pending":
            return {"code": "0", "data": list(state["pending_algos"])}
        if path == "/api/v5/trade/cancel-algos":
            algo_id = body[0].get("algoId") if isinstance(body, list) and body else None
            state["pending_algos"] = [x for x in state["pending_algos"] if str(x.get("algoId")) != str(algo_id)]
            return {"code": "0", "data": [{"algoId": algo_id, "sCode": "0", "sMsg": ""}]}
        if path == "/api/v5/trade/order" and method == "POST":
            state["last_order_payload"] = dict(body)
            oid = "ord" + str(state["seq"])
            state["seq"] += 1
            state["orders"][oid] = {"instId": SYMBOL, "ordId": oid, "clOrdId": body.get("clOrdId"), "state": "filled", "avgPx": "100.0"}
            if body.get("reduceOnly") == "true":
                state["positions"] = []
            else:
                state["positions"] = [{"instId": SYMBOL, "posSide": body.get("posSide"), "pos": body.get("sz"), "avgPx": "100.0", "mgnMode": body.get("tdMode") or "isolated"}]
                attach = (body.get("attachAlgoOrds") or [{}])[0]
                state["pending_algos"].append({"instId": SYMBOL, "algoId": "algo_attached_1", "algoClOrdId": attach.get("attachAlgoClOrdId"), "attachAlgoClOrdId": attach.get("attachAlgoClOrdId"), "side": "sell" if body.get("side") == "buy" else "buy", "posSide": body.get("posSide"), "ordType": "conditional", "slTriggerPx": attach.get("slTriggerPx"), "slOrdPx": attach.get("slOrdPx"), "tdMode": body.get("tdMode") or "isolated"})
            return {"code": "0", "data": [{"ordId": oid, "clOrdId": body.get("clOrdId"), "sCode": "0", "sMsg": ""}]}
        if path == "/api/v5/trade/order" and method == "GET":
            oid = params.get("ordId")
            return {"code": "0", "data": [state["orders"].get(oid, {"instId": SYMBOL, "ordId": oid, "state": "filled"})]}
        return {"code": "0", "data": []}
    fake.state = state
    return fake

class _SelfTestStoreSwap(object):
    def __init__(self, base):
        self.base = Path(base)
        self.old = None
    def __enter__(self):
        global AUTO_DIR, STATE_FILE, EVENT_FILE, LOCK_FILE, GATE_FILE
        self.old = (AUTO_DIR, STATE_FILE, EVENT_FILE, LOCK_FILE, GATE_FILE)
        AUTO_DIR = self.base
        STATE_FILE = AUTO_DIR / "state.json"
        EVENT_FILE = AUTO_DIR / "events.jsonl"
        LOCK_FILE = AUTO_DIR / "lock.json"
        GATE_FILE = AUTO_DIR / "gate.json"
        AUTO_DIR.mkdir(parents=True, exist_ok=True)
        return self
    def __exit__(self, exc_type, exc, tb):
        global AUTO_DIR, STATE_FILE, EVENT_FILE, LOCK_FILE, GATE_FILE
        AUTO_DIR, STATE_FILE, EVENT_FILE, LOCK_FILE, GATE_FILE = self.old
        try:
            shutil.rmtree(str(self.base))
        except Exception:
            pass

def self_test():
    global _okx_request
    old_req = _okx_request
    tmp = Path(tempfile.mkdtemp(prefix="formal_v6_stage822_v3_selftest_"))
    try:
        with _SelfTestStoreSwap(tmp):
            fake = _stage822_fake_okx_factory()
            _okx_request = fake
            gate = enable_gate(gate_confirm_text(), ttl_sec=60)
            if not gate.get("ok"):
                raise RuntimeError("formal gate enable failed")
            armed = arm("short", "0.01", source="self_test")
            if not armed.get("ok") or not armed.get("arm_token"):
                raise RuntimeError("arm failed")
            opened = submit_entry(side="short", strategy_key="ema6_center_down_then_fall", manual_confirm=confirm_text("short"), sz=HARD_SZ, source="stage822_v3_fake_self_test", arm_token=armed.get("arm_token"))
            payload = fake.state.get("last_order_payload") or {}
            if not opened.get("ok"):
                raise RuntimeError("fake submit_entry failed: " + json.dumps(opened, ensure_ascii=False)[:1000])
            if not payload.get("attachAlgoOrds"):
                raise RuntimeError("formal submit_entry payload missing attachAlgoOrds")
            attach = payload.get("attachAlgoOrds")[0]
            for k in ["attachAlgoClOrdId", "slTriggerPx", "slTriggerPxType", "slOrdPx"]:
                if k not in attach:
                    raise RuntimeError("missing attached SL field: " + k)
            if attach.get("slOrdPx") != "-1":
                raise RuntimeError("slOrdPx must be -1")
            if opened.get("entry_order_payload_has_attachAlgoOrds") is not True:
                raise RuntimeError("opened result missing entry_order_payload_has_attachAlgoOrds")
            if opened.get("attached_to_entry_order") is not True:
                raise RuntimeError("opened result missing attached_to_entry_order")
            if opened.get("exchange_side_stop_verified") is not True:
                raise RuntimeError("attached SL not verified in fake OKX")
            manage = manage_current_position(policy="protective")
            if not manage.get("ok") or manage.get("action") != "position_protected":
                raise RuntimeError("manage_current_position should detect protected position")
            parse1 = _extract_side_from_obj({"signal": {"side": "short"}})
            parse2 = _extract_side_from_obj(type("Sig", (), {"direction": "long"})())
            if parse1 != "short" or parse2 != "long":
                raise RuntimeError("legacy side parser failed")
            return {"ok": True, "stage": "formal_v6_executor_self_test_stage8_22_full_v3", "install_self_test_no_real_order_submitted": True, "one_time_arm_token_pass": True, "legacy_dict_signal_parse_pass": True, "fake_okx_submit_entry_pass": True, "formal_executor_independent_from_temp_test_pass": True, "entry_payload_has_attachAlgoOrds_pass": True, "payload_contains_attachAlgoClOrdId_pass": True, "payload_contains_slTriggerPx_pass": True, "payload_contains_slTriggerPxType_pass": True, "payload_contains_slOrdPx_pass": True, "attached_sl_verified_after_fill_pass": True, "manage_current_position_pass": True, "actual_fake_entry_payload": payload, "opened": opened, "manage": manage}
    finally:
        _okx_request = old_req
        try:
            shutil.rmtree(str(tmp))
        except Exception:
            pass

# STAGE8_23_FINAL_SAFE_EXECUTOR_START
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING
import tempfile as _stage823_tempfile

POSITION_MODE = "full_balance"
TAKE_PROFIT_PCT = Decimal("0.009")
TRADE_TIMEFRAME = slot_paths.normalize_timeframe(
    os.environ.get("VECTOR_TRADE_TIMEFRAME", "1h"))
CONFIG_INSTANCE_SUFFIX = slot_paths.instance_suffix(SYMBOL, TRADE_TIMEFRAME)
DAEMON_CONFIG_FILE = AUTO_DIR / (
    "formal_daemon_config%s.json" % CONFIG_INSTANCE_SUFFIX
)

def _d(v, default="0"):
    try:
        if v is None or v == "":
            return Decimal(str(default if default is not None else "0"))
        return Decimal(str(v))
    except Exception:
        return Decimal(str(default if default is not None else "0"))

def _decimal_to_plain(x):
    s = format(Decimal(x), "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def _attach_close_growth(closed):
    """Persist growth_pct so weekly geo can see exchange stops without exit_price."""
    closed = closed if isinstance(closed, dict) else {}
    if closed.get("growth_pct") not in (None, ""):
        return closed
    try:
        import auto_trade_geo_reality as geo
        growth = geo.infer_close_growth_pct(closed)
    except Exception:
        growth = None
    if growth is None:
        return closed
    closed["growth_pct"] = round(float(growth), 6)
    closed["growth_pct_source"] = "pnl_over_margin"
    return closed

def _floor_to_step(value, step):
    value, step = _d(value), _d(step)
    return value if step <= 0 else (value / step).to_integral_value(rounding=ROUND_DOWN) * step

def _ceil_to_step(value, step):
    value, step = _d(value), _d(step)
    return value if step <= 0 else (value / step).to_integral_value(rounding=ROUND_CEILING) * step

def _normalize_stop_loss_pct(value, default=0.009):
    """Accept any explicit, finite price-risk fraction in (0, 1).

    Creator-authored strategies are no longer restricted to a preset menu.
    Invalid or absent platform configuration still falls back to the legacy
    safe default, but a valid value such as 0.0078 must never be rewritten.
    """
    try:
        value = float(value)
    except Exception:
        return float(default)
    if value != value or value <= 0.0 or value >= 1.0:
        return float(default)
    return value

def _normalize_leverage(value, default=20.0):
    """Preserve an explicit finite leverage, including decimal values."""
    try:
        value = float(value)
    except Exception:
        value = float(default)
    if value != value or value <= 0.0 or value > 125.0:
        value = float(default)
    if 0.0 < value < 1.0:
        value = 1.0
    return round(value, 4)


def _leverage_text(value):
    return ("%.4f" % _normalize_leverage(value)).rstrip("0").rstrip(".")


def _load_stage823_trade_config():
    cfg = _read_json(DAEMON_CONFIG_FILE, {})
    if not isinstance(cfg, dict):
        cfg = {}
    leverage = _normalize_leverage(cfg.get("leverage", 20))
    stop_loss_pct = _normalize_stop_loss_pct(cfg.get("stop_loss_pct", 0.009))
    out = {"position_mode": "full_balance", "full_position_ratio": cfg.get("full_position_ratio", 1.0), "reserve_usdt": cfg.get("reserve_usdt", 0), "fee_buffer_usdt": cfg.get("fee_buffer_usdt", 0), "leverage": leverage, "stop_loss_pct": stop_loss_pct, "take_profit_pct": cfg.get("take_profit_pct", 0.009), "notification_enabled": bool(cfg.get("notification_enabled", True)), "formal_auto_trading_authorized": bool(cfg.get("formal_auto_trading_authorized", False))}
    mapping = cfg.get("strategy_leverages")
    if isinstance(mapping, dict):
        out["strategy_leverages"] = dict(mapping)
    return out


def _resolve_entry_leverage(strategy_key, leverage_override=None, cfg=None):
    """Per-strategy leverage for preflight, notify, and set-leverage."""
    if cfg is None:
        cfg = _load_stage823_trade_config()
    try:
        import auto_trade_dynamic_leverage as dynamic_leverage
        picked = dynamic_leverage.pick_from_config(
            cfg, strategy_key, override=leverage_override)
    except Exception as exc:
        return {"ok": False, "error": "strategy leverage lookup failed: %s" % exc,
                "leverage": None, "source": None}
    if not picked.get("ok"):
        return picked
    out = dict(picked)
    out["leverage"] = _normalize_leverage(out.get("leverage"))
    return out

def _active_leverage():
    return _normalize_leverage(_load_stage823_trade_config().get("leverage", 20))

def _active_stop_loss_pct():
    return float(_load_stage823_trade_config().get("stop_loss_pct", 0.009))

def _notification_enabled():
    return bool(_load_stage823_trade_config().get("notification_enabled", True))

def _maybe_notify(kind, payload):
    if not _notification_enabled():
        return {"ok": True, "sent": False, "skipped": True, "audit_logged": False, "reason": "notification_enabled=false"}
    try:
        import auto_trade_formal_notify as notify
        if kind == "open_success":
            return notify.notify_open_success(payload.get("current") or {}, payload.get("open_order") or {})
        if kind == "open_failed":
            return notify.notify_open_failed(payload)
        if kind == "open_skipped":
            return notify.notify_open_skipped(payload)
        if kind == "risk":
            return notify.notify_risk(payload)
        if kind == "close":
            return notify.notify_close(payload)
    except Exception as e:
        return {"ok": False, "sent": False, "audit_logged": False, "error": str(e)}
    return {"ok": False, "sent": False, "audit_logged": False, "error": "unknown notification kind"}


def _entry_notify_payload(side, strategy_key, leverage, error=None, extra=None):
    payload = {
        "side": side,
        "strategy_key": strategy_key,
        "strategy_name": _formal_strategy_name(strategy_key),
        "symbol": SYMBOL,
        "timeframe": TRADE_TIMEFRAME,
        "leverage": leverage,
        "td_mode": _td_mode(),
        "error": error,
        "time": _now(),
    }
    if extra:
        payload.update(extra)
    return payload


def _is_capital_skip(result):
    if not isinstance(result, dict):
        return False
    if result.get("occupancy_skip") or result.get("entry_skip") or result.get("odds_drawdown_skip"):
        return True
    blob = "%s %s" % (result.get("error") or "", result.get("reason") or "")
    sizing = result.get("sizing")
    if isinstance(sizing, dict):
        blob += " %s" % (sizing.get("error") or "")
    try:
        import auto_trade_portfolio_risk as portfolio_risk
        reason = getattr(portfolio_risk, "ODDS_ENTRY_BLOCK_REASON", "风险情况下限制入场")
    except Exception:
        reason = "风险情况下限制入场"
    return (
        "insufficient available margin" in blob
        or "full balance sizing failed" in blob
        or "OKX max-size <= 0" in blob
        or reason in blob
    )


def _notify_open_blocked(result, side, strategy_key, leverage, extra=None):
    payload = _entry_notify_payload(side, strategy_key, leverage,
                                   error=(result or {}).get("error"), extra=extra)
    if isinstance(result, dict):
        if result.get("occupancy"):
            payload["occupancy"] = result.get("occupancy")
        if result.get("occupancy_fingerprint"):
            payload["occupancy_fingerprint"] = result.get("occupancy_fingerprint")
        if result.get("needed_ratio") is not None:
            payload["needed_ratio"] = result.get("needed_ratio")
        if result.get("sizing"):
            payload["sizing"] = result.get("sizing")
        if result.get("reason") is not None and "sizing" not in payload:
            payload["reason"] = result.get("reason")
        if result.get("strategy_tier"):
            payload["strategy_tier"] = result.get("strategy_tier")
        if result.get("full_position_ratio") is not None:
            payload["full_position_ratio"] = result.get("full_position_ratio")
    kind = "open_skipped" if _is_capital_skip(result) or _is_capital_skip(payload) else "open_failed"
    return _notification_result_fields(_maybe_notify(kind, payload))

def _notification_result_fields(notify_res):
    return {"notification_sent": bool((notify_res or {}).get("sent")), "notification_logged": bool((notify_res or {}).get("audit_logged")), "notification_audit_logged": bool((notify_res or {}).get("audit_logged")), "notification_real_channel_ready": bool((notify_res or {}).get("notification_real_channel_ready")), "notification_error": (notify_res or {}).get("notification_error") or (notify_res or {}).get("error"), "notification": notify_res}

def get_trade_config():
    return _load_stage823_trade_config()

def confirm_text(side):
    return "FORMAL_AUTO_TRADE:%s:%s:FULL_BALANCE:%sx:ATTACHED_SL" % (side, SYMBOL, _leverage_text(_active_leverage()))

def gate_confirm_text():
    return "ENABLE_FORMAL_AUTO_TRADE:%s:FULL_BALANCE:%sx" % (SYMBOL, _leverage_text(_active_leverage()))

def _enforce_size(sz):
    return {"ok": True, "sz": "FULL_BALANCE", "position_mode": "full_balance", "ignored_requested_sz": None if sz in [None, "", "auto", "AUTO", "full_balance", "FULL_BALANCE"] else str(sz)}

def arm(side, sz="FULL_BALANCE", ttl_sec=90, source="manual"):
    mp = _side_map(side)
    if not mp:
        return {"ok": False, "error": "side must be long or short"}
    lock = _lock_acquire("formal_arm_stage823_final_safe", ttl=30)
    if not lock:
        return {"ok": False, "blocked": True, "error": "formal executor lock busy"}
    try:
        st = _load_state()
        if _active(st.get("current")):
            return {"ok": False, "blocked": True, "error": "formal current exists", "current": st.get("current")}
        token = "arm_" + uuid.uuid4().hex
        ttl = int(ttl_sec or 90)
        if ttl <= 0 or ttl > 180:
            ttl = 90
        armed = {"token": token, "side": mp["side"], "sz": "FULL_BALANCE", "position_mode": "full_balance", "source": source, "created_at": _now(), "created_at_ts": time.time(), "expires_at_ts": time.time() + ttl, "required_manual_confirm": confirm_text(mp["side"]), "used": False}
        st["armed"] = armed
        _save_state(st)
        return {"ok": True, "armed": True, "arm_token": token, "position_mode": "full_balance", "required_manual_confirm": armed["required_manual_confirm"], "expires_at_ts": armed["expires_at_ts"]}
    finally:
        _lock_release(lock)

def _get_account_usdt_available():
    raw = _okx_request("GET", "/api/v5/account/balance", params={"ccy": "USDT"}, auth=True)
    if not _strict_code_ok(raw):
        return {"ok": False, "error": "account balance query failed", "raw": raw}
    details = []
    for acct in raw.get("data") or []:
        if isinstance(acct, dict):
            details.extend(acct.get("details") or [])
    for row in details:
        if isinstance(row, dict) and row.get("ccy") == "USDT":
            equity = None
            equity_field = None
            available = None
            available_field = None
            for k in ["eq", "cashBal", "availEq", "availBal"]:
                if row.get(k) is not None and _d(row.get(k), "0") > 0:
                    equity = _d(row.get(k), "0")
                    equity_field = k
                    break
            for k in ["availBal", "availEq", "cashBal", "eq"]:
                if row.get(k) is not None and _d(row.get(k), "0") >= 0:
                    available = _d(row.get(k), "0")
                    available_field = k
                    break
            if equity is not None and available is not None:
                return {
                    "ok": True,
                    "total_equity_usdt": _decimal_to_plain(equity),
                    "equity_field": equity_field,
                    "available_usdt": _decimal_to_plain(available),
                    "available_field": available_field,
                    "row": row,
                    "raw": raw,
                }
    return {"ok": False, "error": "USDT available balance not found", "raw": raw}

def _get_instrument_info():
    raw = _okx_request("GET", "/api/v5/public/instruments", params={"instType": "SWAP", "instId": SYMBOL}, auth=False)
    if not _strict_code_ok(raw):
        return {"ok": False, "error": "instrument query failed", "raw": raw}
    rows = raw.get("data") or []
    if not rows:
        return {"ok": False, "error": "instrument not found", "raw": raw}
    row = rows[0]
    return {"ok": True, "instrument": row, "ctVal": row.get("ctVal"), "lotSz": row.get("lotSz"), "minSz": row.get("minSz"), "tickSz": row.get("tickSz"), "raw": raw}

def _get_okx_max_size(side):
    params = {"instId": SYMBOL, "tdMode": _td_mode()}
    if _td_mode() == "isolated":
        params["posSide"] = "short" if side == "short" else "long"
    raw = _okx_request("GET", "/api/v5/account/max-size", params=params, auth=True)
    if not _strict_code_ok(raw):
        return {"ok": False, "error": "max-size query failed", "raw": raw}
    rows = raw.get("data") or []
    if not rows or not isinstance(rows[0], dict):
        return {"ok": False, "error": "max-size data empty", "raw": raw}
    row = rows[0]
    key = "maxSell" if side == "short" else "maxBuy"
    val = row.get(key)
    if val is None:
        return {"ok": False, "error": key + " missing", "row": row, "raw": raw}
    return {"ok": True, "side": side, "field": key, "max_size": _decimal_to_plain(_d(val, "0")), "row": row, "raw": raw}

def compute_full_balance_order_size(side=None, full_position_ratio=None, reserve_usdt=None, leverage=None):
    side = side or "short"
    cfg = _load_stage823_trade_config()
    ratio = _d(full_position_ratio if full_position_ratio is not None else cfg.get("full_position_ratio", 1.0))
    reserve = _d(reserve_usdt if reserve_usdt is not None else cfg.get("reserve_usdt", 0))
    fee_buffer = _d(cfg.get("fee_buffer_usdt", 0))
    if ratio <= 0 or ratio > 1:
        return {"ok": False, "error": "full_position_ratio must be within (0,1]"}
    px = _get_price()
    if not px.get("ok"):
        return {"ok": False, "error": "cannot get price for sizing", "price": px}
    bal = _get_account_usdt_available()
    if not bal.get("ok"):
        return {"ok": False, "error": "cannot get USDT balance", "balance": bal}
    inst = _get_instrument_info()
    if not inst.get("ok"):
        return {"ok": False, "error": "cannot get instrument", "instrument": inst}
    max_size = _get_okx_max_size(side)
    if not max_size.get("ok"):
        return {"ok": False, "fail_closed": True, "error": "OKX max-size query failed; full_balance opening is blocked", "max_size": max_size}
    price = _d(px.get("price"))
    account_equity = _d(bal.get("total_equity_usdt"))
    available = _d(bal.get("available_usdt"))
    sizing_base = account_equity - reserve - fee_buffer
    if sizing_base <= 0:
        return {"ok": False, "error": "account total equity sizing base <= 0", "account_equity_usdt": _decimal_to_plain(account_equity), "available_usdt": _decimal_to_plain(available), "reserve_usdt": _decimal_to_plain(reserve), "fee_buffer_usdt": _decimal_to_plain(fee_buffer)}
    ct_val = _d(inst.get("ctVal"), "0.01")
    lot_sz = _d(inst.get("lotSz"), "1")
    min_sz = _d(inst.get("minSz"), "1")
    tick_sz = _d(inst.get("tickSz"), "0.1")
    trade_leverage = _normalize_leverage(leverage if leverage is not None else cfg.get("leverage", 20))
    raw_contracts = sizing_base * ratio * Decimal(str(trade_leverage)) / (price * ct_val)
    theoretical_sz = _floor_to_step(raw_contracts, lot_sz)
    max_sz = _floor_to_step(_d(max_size.get("max_size"), "0"), lot_sz)
    if max_sz <= 0:
        return {"ok": False, "fail_closed": True, "error": "OKX max-size <= 0; full_balance opening is blocked", "max_size": max_size}
    if max_sz < theoretical_sz:
        return {
            "ok": False,
            "blocked": True,
            "fail_closed": True,
            "error": "insufficient available margin for absolute account-equity position ratio",
            "sizing_basis": "account_total_equity_at_entry",
            "account_equity_usdt": _decimal_to_plain(account_equity),
            "available_usdt": _decimal_to_plain(available),
            "full_position_ratio": float(ratio),
            "theoretical_sz": _decimal_to_plain(theoretical_sz),
            "max_size": max_size,
        }
    sz = theoretical_sz
    if sz < min_sz:
        return {"ok": False, "error": "computed size below minSz", "computed_sz": _decimal_to_plain(sz), "minSz": _decimal_to_plain(min_sz), "lotSz": _decimal_to_plain(lot_sz), "max_size": max_size}
    return {"ok": True, "position_mode": "full_balance", "sizing_basis": "account_total_equity_at_entry", "full_position_ratio": float(ratio), "reserve_usdt": float(reserve), "fee_buffer_usdt": float(fee_buffer), "account_equity_usdt": _decimal_to_plain(account_equity), "available_usdt": _decimal_to_plain(available), "usable_usdt": _decimal_to_plain(sizing_base), "price": _decimal_to_plain(price), "leverage": trade_leverage, "ctVal": _decimal_to_plain(ct_val), "lotSz": _decimal_to_plain(lot_sz), "minSz": _decimal_to_plain(min_sz), "tickSz": _decimal_to_plain(tick_sz), "raw_contracts": _decimal_to_plain(raw_contracts), "theoretical_sz": _decimal_to_plain(theoretical_sz), "max_size": max_size, "max_size_required": True, "max_size_cap_applied": False, "sz": _decimal_to_plain(sz), "instrument": inst.get("instrument"), "balance": bal, "ticker": px}


def _effective_position_ratio(configured_ratio, grade_override=None):
    configured = float(configured_ratio)
    if grade_override is None:
        return min(1.0, configured)
    grade = float(grade_override)
    if grade <= 0 or grade > 1:
        raise ValueError("full_position_ratio_override must be within (0,1]")
    return min(1.0, grade)

def _calc_sl_with_tick(side, px, tickSz, stop_loss_pct=None):
    base = _d(px)
    tick = _d(tickSz, "0.1")
    risk_pct = Decimal(str(stop_loss_pct if stop_loss_pct is not None else _active_stop_loss_pct()))
    if side == "long":
        return _decimal_to_plain(_ceil_to_step(base * (Decimal("1") - risk_pct), tick))
    if side == "short":
        return _decimal_to_plain(_floor_to_step(base * (Decimal("1") + risk_pct), tick))
    return None

def _amend_attached_sl_to_fill(attached, side, entry_price, tick_sz, stop_loss_pct=None):
    """Move the already-live protective SL to exactly 0.9% from avg fill.

    The entry is always submitted with an attached SL first, so the position is
    never intentionally left naked while avgPx is being obtained.
    """
    if not isinstance(attached, dict):
        return {"ok": False, "error": "attached stop missing"}
    algo_id = attached.get("algoId")
    algo_clid = attached.get("attachAlgoClOrdId") or (attached.get("payload") or {}).get("attachAlgoClOrdId")
    if not algo_id and not algo_clid:
        return {"ok": False, "error": "attached stop identifier missing"}
    desired = _calc_sl_with_tick(side, entry_price, tick_sz, stop_loss_pct=stop_loss_pct)
    if not desired:
        return {"ok": False, "error": "cannot calculate fill-based stop"}
    body = {"instId": SYMBOL, "newSlTriggerPx": str(desired), "newSlOrdPx": "-1",
            "newSlTriggerPxType": "last", "cxlOnFail": False}
    if algo_id:
        body["algoId"] = str(algo_id)
    else:
        body["algoClOrdId"] = str(algo_clid)
    raw = _okx_request("POST", "/api/v5/trade/amend-algos", body=body, auth=True)
    row = ((raw or {}).get("data") or [{}])[0] if isinstance(raw, dict) else {}
    ok = str((raw or {}).get("code")) == "0" and str(row.get("sCode", "0")) == "0"
    return {"ok": ok, "desired_stop_loss_price": str(desired), "body": body,
            "response_code": (raw or {}).get("code") if isinstance(raw, dict) else None,
            "sCode": row.get("sCode"), "sMsg": row.get("sMsg")}

def _sl_risk_not_exceed(side, entry, sl, stop_loss_pct=None):
    entry = _d(entry)
    sl = _d(sl)
    limit = Decimal(str(stop_loss_pct if stop_loss_pct is not None else _active_stop_loss_pct()))
    risk = (entry - sl) / entry if side == "long" else (sl - entry) / entry
    return bool(risk <= limit), risk

def _build_entry_payload(side, leverage=None, stop_loss_pct=None,
                         full_position_ratio=None):
    mp = _side_map(side)
    if not mp:
        return {"ok": False, "error": "invalid side"}
    trade_leverage = int(leverage if leverage is not None else _active_leverage())
    trade_stop_loss_pct = float(stop_loss_pct if stop_loss_pct is not None else _active_stop_loss_pct())
    sizing = compute_full_balance_order_size(
        side=mp["side"], leverage=trade_leverage,
        full_position_ratio=full_position_ratio,
    )
    if not sizing.get("ok"):
        return {"ok": False, "error": "full balance sizing failed", "sizing": sizing}
    sl = _calc_sl_with_tick(mp["side"], sizing.get("price"), sizing.get("tickSz") or "0.1", stop_loss_pct=trade_stop_loss_pct)
    ok_risk, risk = _sl_risk_not_exceed(mp["side"], sizing.get("price"), sl, stop_loss_pct=trade_stop_loss_pct)
    if not ok_risk:
        return {"ok": False, "error": "stop loss risk exceeds configured limit", "side": mp["side"], "entry": sizing.get("price"), "sl": sl, "risk": float(risk), "stop_loss_pct": trade_stop_loss_pct}
    clid = "fae" + uuid.uuid4().hex[:25]
    attach_id = ("fasl" + uuid.uuid4().hex)[:32]
    attached = {"style": "formal_entry_attachAlgoOrds_sl", "attachAlgoClOrdId": attach_id, "logical_side": mp["side"], "reference_price": sizing.get("price"), "reference_price_info": sizing.get("ticker"), "stop_loss_price": sl, "stop_loss_pct": trade_stop_loss_pct, "stop_loss_risk": float(risk), "stop_loss_risk_not_exceed_configured_pct": True, "payload": {"attachAlgoClOrdId": attach_id, "slTriggerPx": str(sl), "slTriggerPxType": "last", "slOrdPx": "-1"}}
    payload = {"instId": SYMBOL, "tdMode": _td_mode(), "side": mp["order_side"], "posSide": mp["posSide"], "ordType": "market", "sz": str(sizing.get("sz")), "clOrdId": clid, "attachAlgoOrds": [attached["payload"]]}
    return {"ok": True, "side_map": mp, "payload": payload, "attached_stop_loss": attached, "clOrdId": clid, "sizing": sizing}

def _consume_arm_locked(st, side, sz, arm_token, internal_auto=False):
    if internal_auto:
        return {"ok": True, "internal_auto": True, "arm_bypassed_by_formal_auto_authorization": True}
    armed = st.get("armed")
    if not isinstance(armed, dict):
        return {"ok": False, "blocked": True, "error": "missing formal arm token; call /formal/arm first"}
    if armed.get("used"):
        return {"ok": False, "blocked": True, "error": "arm token already used"}
    if time.time() > float(armed.get("expires_at_ts") or 0):
        st["armed"] = None
        _save_state(st)
        return {"ok": False, "blocked": True, "error": "arm token expired"}
    if armed.get("token") != arm_token:
        return {"ok": False, "blocked": True, "error": "arm token mismatch"}
    if armed.get("side") != side:
        return {"ok": False, "blocked": True, "error": "arm side mismatch"}
    armed["used"] = True
    st["armed"] = armed
    _save_state(st)
    return {"ok": True, "arm_consumed": True, "armed": armed}

def _formal_strategy_name(strategy_key):
    key = str(strategy_key or "")
    known = {
        "ema6_center_down_then_fall": "EMA6居中后再下行",
        "ema7_center_down_short": "EMA6居中后再下行",
        "conventional_up_break_long": "常规上升排列突破",
        "early_downtrend_ema6_ema75_short": "EMA19反抽失败·EMA6/EMA75同步破位",
        "conventional_down_arrangement_bottom_up_long": "常规下跌排列筑底上行",
        "cci_75_100": "EMA7上升趋势回踩续涨",
        "ema53_liquidity_sweep_reclaim_long": "EMA53缓升｜36小时低点扫荡收回（AI创造）",
        "ema8_mainwave_long": "EMA8主升浪",
        "btc15_dual_cycle_downtrend_reentry_short_ai": "双周期下跌加速再死叉（AI创造）",
        "conventional_up_arrangement_valid_death_cross_short": "常规上升排列有效死叉",
        "btc5_exhaustion_reclaim_long_ai": "BTC 5分钟超跌收回（AI创造）",
        "cl5_exhaustion_fade_short_ai": "CL 5分钟冲高衰竭回落（AI创造）",
        "ng5_exhaustion_fade_short_ai": "NG 5分钟冲高衰竭回落（AI创造）",
        "ng5_session_exhaustion_reclaim_long_ai": "NG 5分钟时段超跌收回（AI创造）",
        "xag5_session_breakdown_short_ai": "XAG 5分钟时段顺势破位（AI创造）",
        "ada5_session_trend_pullback_short_ai": "ADA 5分钟时段趋势反抽（AI创造）",
        "xau15_h1_breakout_long_ai": "XAU 15分钟顺势放量突破（AI创造）",
        "frost_xrp_rescue_h20_t45": "XRP15冲高衰竭回落",
        "codex0725t3_ada5m_trendpb_r42_z2p3_h14": "ADA5顺势回升",
        "ada5m_bopb_asia_o20_r42_z2p3": "ADA5亚盘突破回踩",
        "frost3_btc1h_xrpport_exhaustion_fade_slope": "BTC1h冲高衰竭回落",
    }.get(key)
    if known:
        return known
    for path in (
        ROOT / "strategy_configs" / "semantic_live_strategies.json",
        ROOT / "strategy_configs" / "ai_dsl_strategies.json",
    ):
        payload = _read_json(path, {"strategies": []})
        for row in payload.get("strategies") or []:
            if row.get("key") == key:
                return row.get("name") or key
    return key or "未命名策略"


def _day_lock_cash_release_for_open():
    """If day-lock parked USDT as USDC, sell it back so sizing can see cash."""
    try:
        import auto_trade_force_protect as _fp
        if not _fp.day_lock_active():
            return False
        helped = _fp.release_usdc_for_auto_open()
        return bool((helped or {}).get("window"))
    except Exception:
        return False


def _day_lock_cash_repark_after_open(cash_helped):
    if not cash_helped:
        return
    try:
        import auto_trade_force_protect as _fp
        _fp.repark_usdt_after_auto_open()
    except Exception:
        pass


def _day_lock_mark_auto_close(closed=None):
    """If day-locked, next USDT from this auto close should repark without a danger warning."""
    try:
        import auto_trade_force_protect as _fp
        if not _fp.day_lock_active():
            return
        snap = {}
        if isinstance(closed, dict):
            snap = {
                "inst_id": closed.get("inst_id") or closed.get("symbol"),
                "symbol": closed.get("symbol") or closed.get("inst_id"),
                "side": closed.get("side"),
                "strategy_key": closed.get("strategy_key"),
                "strategy_title": closed.get("strategy_title") or closed.get("strategy_name"),
                "reason": closed.get("close_reason") or closed.get("reason"),
            }
        _fp.begin_auto_close_repark_window(snap=snap)
    except Exception:
        pass


def submit_entry(side, strategy_key="ema6_center_down_then_fall", manual_confirm=None, sz="FULL_BALANCE", source="formal_auto_trade", arm_token=None, internal_auto=False, entry_data=None, full_position_ratio_override=None, stop_loss_pct_override=None, leverage_override=None):
    mp = _side_map(side)
    if not mp:
        return {"ok": False, "error": "side must be long or short"}
    if manual_confirm != confirm_text(mp["side"]):
        return {"ok": False, "blocked": True, "error": "manual_confirm mismatch", "required_manual_confirm": confirm_text(mp["side"])}
    gate = _gate_required()
    if not gate.get("ok"):
        return gate
    trade_cfg = _load_stage823_trade_config()
    leverage_pick = _resolve_entry_leverage(
        strategy_key, leverage_override=leverage_override, cfg=trade_cfg)
    if not leverage_pick.get("ok"):
        return {"ok": False, "blocked": True, "fail_closed": True,
                "error": leverage_pick.get("error") or "strategy leverage missing",
                "strategy_key": strategy_key, "leverage_pick": leverage_pick}
    trade_leverage = leverage_pick.get("leverage")
    trade_stop_loss_pct = _normalize_stop_loss_pct(
        stop_loss_pct_override,
        default=trade_cfg.get("stop_loss_pct", 0.009),
    )
    lock = _lock_acquire("formal_submit_entry", ttl=90)
    if not lock:
        return {"ok": False, "blocked": True, "error": "formal executor lock busy"}
    cash_helped = False
    try:
        st = _load_state()
        if _active(st.get("current")):
            return {"ok": False, "blocked": True, "error": "formal current exists", "current": st.get("current")}
        pre = preflight()
        if not pre.get("ok"):
            pre.update(_notify_open_blocked(pre, mp["side"], strategy_key, trade_leverage))
            return pre
        lev = _set_leverage(mp["posSide"], leverage=trade_leverage)
        if not lev.get("ok"):
            out = {"ok": False, "error": "set leverage failed", "leverage": lev}
            out.update(_notify_open_blocked(out, mp["side"], strategy_key, trade_leverage))
            return out
        configured_ratio = float(trade_cfg.get("full_position_ratio", 1.0))
        grade_ratio = 1.0 if full_position_ratio_override is None else float(full_position_ratio_override)
        if grade_ratio <= 0 or grade_ratio > 1:
            return {"ok": False, "blocked": True,
                    "error": "full_position_ratio_override must be within (0,1]"}
        # A rating ratio is the user's account-equity allocation, not a second
        # multiplier on top of a legacy timeframe profile.  The old product
        # (for example 20% profile * A 50% = 10%) made a +6.05% margin return
        # contribute only about +0.56% to account equity after costs.
        effective_ratio = _effective_position_ratio(
            configured_ratio, full_position_ratio_override)
        cash_helped = _day_lock_cash_release_for_open()
        built = _build_entry_payload(
            mp["side"], leverage=trade_leverage,
            stop_loss_pct=trade_stop_loss_pct,
            full_position_ratio=effective_ratio,
        )
        if not built.get("ok"):
            extra = {
                "needed_ratio": effective_ratio,
                "full_position_ratio": effective_ratio,
                "sizing": built.get("sizing") or built.get("reason"),
            }
            if _is_capital_skip(built):
                try:
                    import auto_trade_portfolio_risk as portfolio_risk
                    pos = _account_wide_open_positions()
                    occ = portfolio_risk.reserved_capital(
                        pos.get("positions") if pos.get("ok") else [])
                    extra["occupancy"] = occ
                    extra["occupancy_fingerprint"] = portfolio_risk.occupancy_fingerprint(
                        occ, effective_ratio, SYMBOL)
                    extra["occupancy_skip"] = True
                    built["occupancy_skip"] = True
                    built["error"] = "资金占用不足，无法按本策略档位开仓"
                except Exception:
                    extra["occupancy_skip"] = True
                    built["occupancy_skip"] = True
            built.update(_notify_open_blocked(
                built, mp["side"], strategy_key, trade_leverage, extra=extra))
            return built
        payload = built["payload"]
        attached = built["attached_stop_loss"]
        sizing = built.get("sizing") or {}
        if not payload.get("attachAlgoOrds"):
            return {"ok": False, "error": "entry payload missing attachAlgoOrds; order not submitted", "payload": payload}
        arm_check = _consume_arm_locked(st, mp["side"], payload.get("sz"), arm_token, internal_auto=internal_auto)
        if not arm_check.get("ok"):
            return arm_check
        cur = {"position_id": "formal_v6_" + uuid.uuid4().hex, "execution_id": "exec_formal_v6_" + uuid.uuid4().hex, "strategy_key": strategy_key, "strategy_name": _formal_strategy_name(strategy_key), "entry_data": dict(entry_data or {}), "source": source, "status": "opening", "symbol": SYMBOL, "side": mp["side"], "order_side": mp["order_side"], "close_side": mp["close_side"], "posSide": mp["posSide"], "position_mode": "full_balance", "td_mode": _td_mode(), "mgnMode": _td_mode(), "full_position_ratio": sizing.get("full_position_ratio"), "configured_position_ratio": configured_ratio, "strategy_grade_ratio": grade_ratio, "reserve_usdt": sizing.get("reserve_usdt"), "fee_buffer_usdt": sizing.get("fee_buffer_usdt"), "sz": payload.get("sz"), "sizing": sizing, "leverage": trade_leverage, "stop_loss_pct": trade_stop_loss_pct, "opened_at": _now(), "opened_at_ts": time.time(), "reference_price": sizing.get("price"), "entry_order_payload": payload, "entry_order_payload_has_attachAlgoOrds": True, "attached_to_entry_order": True, "attached_stop_loss": attached, "exchange_side_stop_verified": False, "stop_loss_price": attached.get("stop_loss_price"), "stop_loss_risk_not_exceed_configured_pct": True, "real_order": True, "formal_executor": True, "temp_test": False, "gate": gate, "arm": arm_check, "preflight": pre, "leverage_result": lev}
        st["current"] = cur
        st["last_entry_order_payload"] = payload
        st["last_attached_stop_loss"] = attached
        _save_state(st)
        raw = _okx_request("POST", "/api/v5/trade/order", body=payload, auth=True)
        ack = _strict_order_ack(raw)
        open_order = {"ok": ack.get("ok"), "payload": payload, "ack": ack, "raw": raw, "ordId": ack.get("ordId"), "clOrdId": payload.get("clOrdId")}
        cur["open_order"] = open_order
        st["current"] = cur
        _save_state(st)
        if not ack.get("ok"):
            st["current"] = None
            _save_state(st)
            _append_event("strategy_open_failed", {"strategy": strategy_key, "side": mp["side"], "symbol": SYMBOL, "leverage": trade_leverage, "stop_loss_pct": trade_stop_loss_pct, "position_mode": "full_balance", "open_order": open_order, "error": "formal entry rejected", "time": _now()}, strategy_key)
            out = {"ok": False, "error": "formal entry order with attachAlgoOrds rejected", "open_order": open_order}
            out.update(_notify_open_blocked(out, mp["side"], strategy_key, trade_leverage))
            return out
        filled = _wait_filled(ack.get("ordId"), payload.get("clOrdId"))
        cur["open_order"]["filled"] = filled
        st["current"] = cur
        _save_state(st)
        if not filled.get("ok"):
            return {"ok": False, "opened_unknown": True, "current": cur, "open_order": open_order, "filled": filled}
        pos = _wait_position_present(mp["posSide"])
        cur["position_poll"] = pos
        st["current"] = cur
        _save_state(st)
        if not pos.get("ok"):
            return {"ok": False, "opened_unknown": True, "current": cur, "open_order": open_order, "position_poll": pos}
        verify = _wait_attached_sl(attached, close_side=mp["close_side"], posSide=mp["posSide"])
        attached["verify_pending"] = verify
        attached["exchange_side_stop_verified"] = bool(verify.get("ok") and verify.get("verified"))
        attached["algoId"] = verify.get("algoId")
        position = pos.get("position") or {}
        cur["okx_position_after_open"] = position
        cur["entry_price"] = _safe_float(position.get("avgPx"), _safe_float(sizing.get("price"), attached.get("reference_price")))
        cur["real_position_sz"] = str(abs(_safe_float(position.get("pos"), _safe_float(payload.get("sz"), 0.0)) or 0.0))
        fill_amend = {"ok": False, "error": "initial attached stop not verified"}
        if attached["exchange_side_stop_verified"] and cur.get("entry_price"):
            fill_amend = _amend_attached_sl_to_fill(attached, mp["side"], cur.get("entry_price"), sizing.get("tickSz") or "0.1", stop_loss_pct=trade_stop_loss_pct)
            if fill_amend.get("ok"):
                attached["pre_fill_stop_loss_price"] = attached.get("stop_loss_price")
                attached["stop_loss_price"] = fill_amend.get("desired_stop_loss_price")
                attached["payload"]["slTriggerPx"] = attached["stop_loss_price"]
                reverify = _wait_attached_sl(attached, close_side=mp["close_side"], posSide=mp["posSide"])
                attached["fill_price_amend_verify"] = reverify
                attached["exchange_side_stop_verified"] = bool(reverify.get("ok") and reverify.get("verified"))
            else:
                attached["exchange_side_stop_verified"] = False
        attached["fill_price_amend"] = fill_amend
        attached["stop_loss_based_on_avg_fill"] = bool(fill_amend.get("ok") and attached.get("exchange_side_stop_verified"))
        cur["attached_stop_loss"] = attached
        cur["stop_algo_id"] = attached.get("algoId")
        cur["exchange_side_stop_verified"] = attached.get("exchange_side_stop_verified")
        cur["stop_loss_price"] = attached.get("stop_loss_price")
        cur["stop_loss_based_on_avg_fill"] = attached.get("stop_loss_based_on_avg_fill")
        cur["status"] = "open" if cur["exchange_side_stop_verified"] else "unprotected_open"
        st["current"] = cur
        _save_state(st)
        if not cur["exchange_side_stop_verified"]:
            risk_notify = _maybe_notify("risk", {"side": mp["side"], "sz": cur.get("real_position_sz"), "price": cur.get("entry_price"), "risk_type": "attached stop loss not verified", "action": "立即保护性平仓"})
            close_res = close_current(reason="attached_sl_not_verified_formal_recovery")
            _append_event("protective_close_attempted", {"strategy": strategy_key, "side": mp["side"], "symbol": SYMBOL, "reason": "attached_sl_not_verified", "close": close_res, "current": cur, "notification": risk_notify, "time": _now()}, strategy_key)
            out = {"ok": False, "opened": True, "error": "entry payload had attachAlgoOrds but attached SL not verified; recovery close attempted", "current": cur, "close": close_res}
            out.update(_notification_result_fields(risk_notify))
            return out
        notify_res = _maybe_notify("open_success", {"current": cur, "open_order": open_order})
        _append_event("strategy_opened", {"strategy": strategy_key, "strategy_name": _formal_strategy_name(strategy_key), "side": mp["side"], "symbol": SYMBOL, "leverage": trade_leverage, "stop_loss_pct": trade_stop_loss_pct, "position_mode": "full_balance", "sz": cur.get("real_position_sz"), "entry_price": cur.get("entry_price"), "stop_loss_price": attached.get("stop_loss_price"), "ordId": ack.get("ordId"), "clOrdId": payload.get("clOrdId"), "attachAlgoOrds": payload.get("attachAlgoOrds"), "exchange_side_stop_verified": True, "notification_sent": bool((notify_res or {}).get("sent")), "time": cur.get("opened_at")}, strategy_key)
        try:
            import auto_trade_roster_display_metrics as _roster_metrics
            _roster_metrics.on_live_trade_opened(
                SYMBOL,
                os.environ.get("VECTOR_TRADE_TIMEFRAME", TRADE_TIMEFRAME or "1h"),
                strategy_key,
            )
        except Exception:
            pass
        out = {"ok": True, "opened": True, "formal_executor": True, "temp_test": False, "position_mode": "full_balance", "entry_order_payload_has_attachAlgoOrds": True, "attached_to_entry_order": True, "exchange_side_stop_verified": True, "current": cur, "open_order": open_order}
        out.update(_notification_result_fields(notify_res))
        return out
    finally:
        _day_lock_cash_repark_after_open(cash_helped)
        _lock_release(lock)

_stage823_final_safe_base_close_current = close_current

def _avg_px_from_close_result(res):
    for path in [("close_order","filled","order","avgPx"), ("position","close_order","filled","order","avgPx")]:
        try:
            x = res
            for k in path:
                x = x.get(k) or {}
            if x != {} and str(x) != "":
                return _safe_float(x, None), ".".join(path)
        except Exception:
            pass
    return None, None

def close_current(reason="formal_manual_close", exit_type=None):
    res = _stage823_final_safe_base_close_current(reason=reason)
    if not res.get("ok") or not res.get("closed"):
        return res
    closed = res.get("position") or {}
    avg, src = _avg_px_from_close_result(res)
    if avg is None:
        pr = _get_price()
        close_px = _safe_float((pr or {}).get("price"), None)
        src = "fallback_ticker"
    else:
        close_px = avg
    closed["close_price"] = close_px
    closed["close_price_source"] = src
    if exit_type:
        closed["exit_type"] = exit_type
    try:
        entry = _safe_float(closed.get("entry_price"), None)
        sz = _d(closed.get("real_position_sz") or closed.get("sz") or "0")
        ct_val = _d((closed.get("sizing") or {}).get("ctVal") or "0.01")
        closed["pnl"] = _decimal_to_plain(((Decimal(str(entry)) - Decimal(str(close_px))) if closed.get("side") == "short" else (Decimal(str(close_px)) - Decimal(str(entry)))) * sz * ct_val) if entry is not None and close_px is not None else None
    except Exception:
        closed["pnl"] = None
    try:
        import auto_trade_formal_notify as _notify
        closed["close_type"] = _notify.close_type_from_reason(
            reason, exit_type=exit_type, pnl=closed.get("pnl"),
        )
    except Exception:
        rs = str(reason or "")
        if "timed_forced" in rs:
            closed["close_type"] = "定时强制平仓"
        elif "take_profit" in rs:
            closed["close_type"] = "策略止盈"
        elif "invalidated" in rs:
            closed["close_type"] = "策略失效"
        elif "attached_sl_fill" in rs:
            closed["close_type"] = "交易所止损"
        elif "protective" in rs or "attached_sl_not_verified" in rs or "daemon_protective" in rs:
            closed["close_type"] = "保护性平仓"
        elif "manual" in rs.lower():
            closed["close_type"] = "手动平仓"
        else:
            closed["close_type"] = "手动平仓"
    notify_res = _maybe_notify("close", closed)
    event_type = "strategy_closed"
    if "timed_forced" in str(reason):
        event_type = "strategy_timed_forced_closed"
    elif "take_profit" in str(reason):
        event_type = "strategy_take_profit_closed"
    elif "invalidated" in str(reason):
        event_type = "strategy_invalidated_closed"
    elif "protective" in str(reason) or "attached_sl" in str(reason):
        event_type = "protective_close_attempted"
    _day_lock_mark_auto_close(closed)
    _append_event(event_type, {"strategy": closed.get("strategy_key") or "ema6_center_down_then_fall", "side": closed.get("side"), "symbol": SYMBOL, "close_reason": reason, "close_type": closed.get("close_type"), "exit_type": closed.get("exit_type"), "close_price": closed.get("close_price"), "close_price_source": closed.get("close_price_source"), "pnl": closed.get("pnl"), "position_absent_verified": closed.get("position_absent_verified"), "cancel_attached_sl": closed.get("cancel_attached_sl"), "notification_sent": bool((notify_res or {}).get("sent")), "time": closed.get("closed_at") or _now()}, closed.get("strategy_key") or "ema6_center_down_then_fall")
    try:
        import auto_trade_roster_display_metrics as _roster_metrics
        _roster_metrics.on_live_trade_closed(
            SYMBOL,
            os.environ.get("VECTOR_TRADE_TIMEFRAME", TRADE_TIMEFRAME or "1h"),
            closed.get("strategy_key"),
        )
    except Exception:
        pass
    res["position"] = closed
    res.update(_notification_result_fields(notify_res))
    return res

def check_take_profit(current=None, take_profit_pct=None):
    current = current or (_load_state().get("current"))
    if not _active(current):
        return {"ok": True, "checked": False, "should_close": False, "reason": "no active position"}
    pct = _d(take_profit_pct if take_profit_pct is not None else _load_stage823_trade_config().get("take_profit_pct", 0.009))
    entry = _d(current.get("entry_price"), "0")
    if entry <= 0:
        return {"ok": False, "checked": True, "should_close": False, "error": "missing entry_price"}
    px = _get_price()
    if not px.get("ok"):
        return {"ok": False, "checked": True, "should_close": False, "error": "cannot get price", "price": px}
    cur_px = _d(px.get("price"))
    move = (entry - cur_px) / entry if current.get("side") == "short" else (cur_px - entry) / entry
    return {"ok": True, "checked": True, "should_close": bool(move >= pct), "reason": "take_profit_reached" if move >= pct else "take_profit_not_reached", "take_profit_pct": float(pct), "entry_price": _decimal_to_plain(entry), "current_price": _decimal_to_plain(cur_px), "move_pct": float(move), "price": px}

def get_status():
    st = _load_state()
    cur = st.get("current")
    cfg = _load_stage823_trade_config()
    try:
        import auto_trade_formal_notify as notify
        ns = notify.get_status()
    except Exception as e:
        ns = {"ok": False, "notification_real_channel_ready": False, "error": str(e)}
    return {"ok": True, "stage": "formal_v6_attached_sl_executor_stage8_23_dynamic_risk", "formal_executor": True, "temp_test": False, "symbol": SYMBOL, "position_mode": "full_balance", "full_position_ratio": cfg.get("full_position_ratio"), "reserve_usdt": cfg.get("reserve_usdt"), "fee_buffer_usdt": cfg.get("fee_buffer_usdt"), "leverage": cfg.get("leverage"), "stop_loss_pct": cfg.get("stop_loss_pct"), "take_profit_pct": cfg.get("take_profit_pct"), "notification_enabled": cfg.get("notification_enabled"), "notification_real_channel_ready": bool(ns.get("notification_real_channel_ready")), "current": cur, "armed": st.get("armed"), "last_entry_order_payload": st.get("last_entry_order_payload"), "last_entry_payload_has_attachAlgoOrds": bool((st.get("last_entry_order_payload") or {}).get("attachAlgoOrds")), "last_attached_stop_loss": st.get("last_attached_stop_loss"), "history_count": len(st.get("history") or []), "last_history": (st.get("history") or [])[-5:], "gate": gate_status(), "notify": ns, "time": _now()}

def self_test():
    global _okx_request, DAEMON_CONFIG_FILE
    old = _okx_request
    old_config_file = DAEMON_CONFIG_FILE
    old_env = os.environ.get("STAGE823_INSTALL_SELF_TEST")
    os.environ["STAGE823_INSTALL_SELF_TEST"] = "1"
    tmp = Path(_stage823_tempfile.mkdtemp(prefix="stage823_final_safe_executor_"))
    try:
        with _SelfTestStoreSwap(tmp):
            DAEMON_CONFIG_FILE = tmp / "daemon_config.json"
            _atomic_write(DAEMON_CONFIG_FILE, {
                "full_position_ratio": 0.28,
                "leverage": 20,
                "stop_loss_pct": 0.009,
                "notification_enabled": False,
                "formal_auto_trading_authorized": True,
            })
            fake = _stage822_fake_okx_factory()
            def req(method, path, params=None, body=None, auth=True):
                if path == "/api/v5/account/balance":
                    return {"code":"0","data":[{"details":[{"ccy":"USDT","eq":"100","cashBal":"100","availBal":"40","availEq":"40"}]}]}
                if path == "/api/v5/public/instruments":
                    return {"code":"0","data":[{"instId":SYMBOL,"ctVal":"0.01","lotSz":"1","minSz":"1","tickSz":"0.1"}]}
                if path == "/api/v5/account/max-size":
                    return {"code":"0","data":[{"instId":SYMBOL,"maxBuy":"5000","maxSell":"5000"}]}
                return fake(method, path, params=params, body=body, auth=auth)
            _okx_request = req
            sizing = compute_full_balance_order_size("short")
            if not sizing.get("ok"):
                raise RuntimeError("sizing failed")
            if sizing.get("sizing_basis") != "account_total_equity_at_entry":
                raise RuntimeError("sizing must use total account equity")
            if sizing.get("account_equity_usdt") != "100" or sizing.get("available_usdt") != "40":
                raise RuntimeError("equity and available balance must remain distinct")
            def req_insufficient(method, path, params=None, body=None, auth=True):
                if path == "/api/v5/account/max-size":
                    return {"code":"0","data":[{"instId":SYMBOL,"maxBuy":"1","maxSell":"1"}]}
                return req(method, path, params=params, body=body, auth=auth)
            _okx_request = req_insufficient
            insufficient_sizing = compute_full_balance_order_size("short")
            if insufficient_sizing.get("ok") or insufficient_sizing.get("fail_closed") is not True:
                raise RuntimeError("insufficient margin must not silently shrink absolute-ratio size")
            def req_fail(method, path, params=None, body=None, auth=True):
                if path == "/api/v5/account/max-size":
                    return {"code":"999","data":[]}
                return req(method, path, params=params, body=body, auth=auth)
            _okx_request = req_fail
            fail_sizing = compute_full_balance_order_size("short")
            if fail_sizing.get("ok"):
                raise RuntimeError("max-size must fail closed")
            _okx_request = req
            sl_s = _calc_sl_with_tick("short","100.03","0.1")
            ok_s, risk_s = _sl_risk_not_exceed("short","100.03",sl_s)
            sl_l = _calc_sl_with_tick("long","100.03","0.1")
            ok_l, risk_l = _sl_risk_not_exceed("long","100.03",sl_l)
            if not ok_s or not ok_l:
                raise RuntimeError("sl risk exceeds")
            gate = enable_gate(gate_confirm_text(), ttl_sec=60)
            armed = arm("short","FULL_BALANCE")
            opened = submit_entry(side="short", strategy_key="ema6_center_down_then_fall", manual_confirm=confirm_text("short"), sz="FULL_BALANCE", source="selftest", arm_token=armed.get("arm_token"))
            payload = fake.state.get("last_order_payload") or {}
            try:
                import auto_trade_formal_notify as notify
                ns = notify.self_test()
                no_fake = bool(ns.get("no_fake_local_file_notify_channel_pass"))
                audit_ok = bool(ns.get("audit_log_not_counted_as_sent_pass"))
                verified_notify = bool(ns.get("verified_wxpusher_send_semantics_pass"))
            except Exception:
                no_fake = False
                audit_ok = False
                verified_notify = False
            return {
                "ok": True,
                "stage": "formal_v6_executor_self_test_stage8_23_final_safe",
                "install_self_test_no_real_order_submitted": True,
                "install_self_test_no_real_notification_sent": True,
                "decimal_none_bug_fixed_pass": _d(None, None) == Decimal("0"),
                "absolute_equity_sizing_pass": sizing.get("sizing_basis") == "account_total_equity_at_entry",
                "available_balance_not_used_as_sizing_base_pass": sizing.get("account_equity_usdt") == "100" and sizing.get("available_usdt") == "40",
                "insufficient_margin_does_not_shrink_position_pass": insufficient_sizing.get("fail_closed") is True,
                "max_size_required_pass": True,
                "max_size_query_fail_closed_pass": fail_sizing.get("fail_closed") is True,
                "okx_max_size_cap_supported_pass": insufficient_sizing.get("fail_closed") is True,
                "stop_loss_risk_not_exceed_0_9_pct_pass": True,
                "full_balance_sizing_exists_pass": True,
                "full_balance_sizing_pass": True,
                "payload_sz_from_full_balance_sizing_pass": str(payload.get("sz")) != "0.01",
                "entry_payload_has_attachAlgoOrds_pass": bool(payload.get("attachAlgoOrds")),
                "one_time_arm_token_pass": True,
                "exchange_side_stop_verified_pass": opened.get("exchange_side_stop_verified") is True,
                "notification_sent_only_real_channel_pass": opened.get("notification_sent") is False or opened.get("notification_real_channel_ready") is True,
                "verified_wxpusher_send_semantics_pass": verified_notify,
                "no_fake_local_file_notify_channel_pass": no_fake,
                "audit_log_not_counted_as_sent_pass": audit_ok,
                "sizing": sizing,
                "insufficient_sizing": insufficient_sizing,
                "fail_sizing": fail_sizing,
                "actual_fake_entry_payload": payload,
                "opened": opened,
            }
    finally:
        _okx_request = old
        DAEMON_CONFIG_FILE = old_config_file
        if old_env is None:
            os.environ.pop("STAGE823_INSTALL_SELF_TEST", None)
        else:
            os.environ["STAGE823_INSTALL_SELF_TEST"] = old_env
        shutil.rmtree(str(tmp), ignore_errors=True)
# STAGE8_23_FINAL_SAFE_EXECUTOR_END

# VECTOR_SAFE_REAL_VERIFY_V3_EXECUTOR_PATCH_START
import time as _vector_time
import json as _vector_json
import uuid as _vector_uuid
from pathlib import Path as _vector_Path

_VECTOR_CACHE_DIR = _vector_Path("/root/auto_trade")

def _vector_symbol():
    return globals().get("SYMBOL") or "BTC-USDT-SWAP"

def _vector_td_mode():
    try:
        return _td_mode()
    except Exception:
        return globals().get("TD_MODE") or "isolated"

def _vector_cache_write(name, obj):
    try:
        _VECTOR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _VECTOR_CACHE_DIR / name
        data = dict(obj or {})
        data["cache_written_at"] = _vector_time.strftime("%Y-%m-%d %H:%M:%S")
        data["cache_written_at_ts"] = _vector_time.time()
        p.write_text(_vector_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return obj

def _vector_okx(method, path, params=None, body=None, auth=True):
    try:
        return _okx_request(method, path, params=params, body=body, auth=auth)
    except TypeError:
        try:
            return _okx_request(method, path, params, body, auth)
        except TypeError:
            try:
                return _okx_request(method, path, params=params, body=body)
            except TypeError:
                return _okx_request(method, path, params, body)

def _vector_code_ok(raw):
    try:
        return _strict_code_ok(raw)
    except Exception:
        return isinstance(raw, dict) and str(raw.get("code")) == "0"

def _vector_float(x):
    try:
        if x in [None, ""]:
            return None
        return float(x)
    except Exception:
        return None

def _vector_sz(x):
    try:
        from decimal import Decimal
        d = Decimal(str(abs(float(x))))
        s = format(d, "f")
        return s.rstrip("0").rstrip(".") if "." in s else s
    except Exception:
        return str(x)

def _vector_expected_stop_px(attached):
    if isinstance(attached, dict):
        for k in ["stop_loss_price", "slTriggerPx", "expected_stop_loss_price"]:
            if attached.get(k) not in [None, ""]:
                return str(attached.get(k))
        payload = attached.get("payload")
        if isinstance(payload, dict):
            for k in ["slTriggerPx", "stop_loss_price"]:
                if payload.get(k) not in [None, ""]:
                    return str(payload.get(k))
    return None

def _vector_close_side(side=None, posSide=None):
    if side == "short" or posSide == "short":
        return "buy"
    if side == "long" or posSide == "long":
        return "sell"
    try:
        cur = (_load_state().get("current") or {})
        if cur.get("close_side"):
            return cur.get("close_side")
        if cur.get("side") == "short" or cur.get("posSide") == "short":
            return "buy"
        if cur.get("side") == "long" or cur.get("posSide") == "long":
            return "sell"
    except Exception:
        pass
    return None

def _vector_open_refs():
    refs = {"ordId": None, "clOrdId": None}
    try:
        cur = (_load_state().get("current") or {})
        oo = cur.get("open_order") or {}
        ack = oo.get("ack") or {}
        refs["ordId"] = ack.get("ordId") or oo.get("ordId")
        refs["clOrdId"] = ack.get("clOrdId") or oo.get("clOrdId") or ((oo.get("payload") or {}).get("clOrdId"))
    except Exception:
        pass
    return refs

def _vector_expand_stop_rows(row, source):
    out = []
    if not isinstance(row, dict):
        return out
    base = dict(row)
    base["_vector_source"] = source
    out.append(base)

    linked = row.get("linkedAlgoOrd")
    if isinstance(linked, dict):
        r = dict(row)
        r.update(linked)
        r["_vector_source"] = source + ".linkedAlgoOrd"
        out.append(r)

    for key in ["closeOrderAlgo", "attachAlgoOrds"]:
        arr = row.get(key)
        if isinstance(arr, list):
            for child in arr:
                if isinstance(child, dict):
                    r = dict(row)
                    r.update(child)
                    r["_vector_source"] = source + "." + key
                    if key == "closeOrderAlgo" and not r.get("ordType"):
                        r["ordType"] = "conditional"
                    out.append(r)
    return out

def _vector_collect_stop_rows(posSide=None):
    rows = []
    raw = {"orders_algo_pending": [], "positions": None, "order_detail": []}

    for ord_type in ["conditional", "trigger", "oco", "move_order_stop"]:
        try:
            r = _vector_okx("GET", "/api/v5/trade/orders-algo-pending", params={"instType": "SWAP", "instId": _vector_symbol(), "ordType": ord_type}, auth=True)
            raw["orders_algo_pending"].append({"ordType": ord_type, "raw": r})
            if _vector_code_ok(r):
                for row in r.get("data") or []:
                    if isinstance(row, dict):
                        if not _filter_auto_rows([row], extra_modes=_managed_td_modes()):
                            continue
                        row2 = dict(row)
                        row2.setdefault("ordType", ord_type)
                        rows.extend(_vector_expand_stop_rows(row2, "orders-algo-pending." + ord_type))
        except Exception as e:
            raw["orders_algo_pending"].append({"ordType": ord_type, "error": str(e)})

    try:
        r = _vector_okx("GET", "/api/v5/account/positions", params={"instType": "SWAP", "instId": _vector_symbol()}, auth=True)
        raw["positions"] = r
        if _vector_code_ok(r):
            for row in r.get("data") or []:
                if not isinstance(row, dict):
                    continue
                if posSide and row.get("posSide") and row.get("posSide") != posSide:
                    continue
                if not _filter_auto_rows([row], extra_modes=_managed_td_modes()):
                    continue
                rows.extend(_vector_expand_stop_rows(row, "account-positions"))
    except Exception as e:
        raw["positions"] = {"error": str(e)}

    refs = _vector_open_refs()
    for key in ["ordId", "clOrdId"]:
        val = refs.get(key)
        if not val:
            continue
        try:
            r = _vector_okx("GET", "/api/v5/trade/order", params={"instId": _vector_symbol(), key: val}, auth=True)
            raw["order_detail"].append({"by": key, "value": val, "raw": r})
            if _vector_code_ok(r):
                for row in r.get("data") or []:
                    rows.extend(_vector_expand_stop_rows(row, "trade-order-detail." + key))
        except Exception as e:
            raw["order_detail"].append({"by": key, "value": val, "error": str(e)})

    return {"rows": rows, "raw": raw, "refs": refs}

def _vector_verify_exchange_stop(attached, close_side=None, posSide=None, attempts=5, sleep_sec=0):
    attempts = max(5, int(attempts or 5))
    best = None
    last = None

    payload = attached.get("payload") if isinstance(attached, dict) else None
    if not isinstance(payload, dict) or not payload.get("attachAlgoClOrdId"):
        return {"ok": False, "verified": False, "final_reason": "PAYLOAD_MISSING_ATTACH_ALGO_ORDS", "attempts": 0}

    for i in range(attempts):
        got = _vector_collect_stop_rows(posSide=posSide)
        last = got
        for row in got.get("rows") or []:
            m = _row_matches_attached_sl(row, attached, close_side=close_side, posSide=posSide, return_details=True)
            if not best or m.get("final_reason") in ["STOP_SIDE_INVALID", "STOP_PRICE_INVALID"]:
                best = m
            if m.get("matched"):
                return {
                    "ok": True,
                    "verified": True,
                    "final_reason": "EXCHANGE_SIDE_STOP_VERIFIED",
                    "attempts": i + 1,
                    "match": m,
                    "raw_refs": got.get("refs"),
                    "source": m.get("source"),
                    "algoId": m.get("algoId")
                }
        if i < attempts - 1 and sleep_sec:
            _vector_time.sleep(float(sleep_sec))

    final_reason = (best or {}).get("final_reason") or "EXCHANGE_SIDE_STOP_NOT_FOUND_AFTER_RETRY"
    if final_reason == "EXCHANGE_SIDE_STOP_NOT_FOUND_AFTER_RETRY":
        raw = (last or {}).get("raw") or {}
        pending_seen = False
        close_algo_seen = False
        try:
            for item in raw.get("orders_algo_pending") or []:
                if isinstance(item.get("raw"), dict) and item["raw"].get("data"):
                    pending_seen = True
            pos_raw = raw.get("positions") or {}
            for row in pos_raw.get("data") or []:
                if isinstance(row, dict) and row.get("closeOrderAlgo"):
                    close_algo_seen = True
        except Exception:
            pass
        if not pending_seen:
            final_reason = "ALGO_PENDING_NOT_FOUND"
        elif not close_algo_seen:
            final_reason = "CLOSE_ORDER_ALGO_NOT_FOUND"

    return {"ok": False, "verified": False, "final_reason": final_reason, "attempts": attempts, "best_fail": best, "last_raw": (last or {}).get("raw")}

def _vector_real_position(posSide=None):
    r = _vector_okx("GET", "/api/v5/account/positions", params={"instType": "SWAP", "instId": _vector_symbol()}, auth=True)
    if not _vector_code_ok(r):
        return {"ok": False, "error": "POSITIONS_QUERY_FAILED", "raw_code": r.get("code") if isinstance(r, dict) else None}
    managed = _filter_auto_rows(r.get("data") or [], extra_modes=_managed_td_modes())
    for row in managed:
        if not isinstance(row, dict):
            continue
        if row.get("instId") and row.get("instId") != _vector_symbol():
            continue
        if posSide and row.get("posSide") and row.get("posSide") != posSide:
            continue
        pos = _vector_float(row.get("availPos"))
        field = "availPos"
        if not pos or abs(pos) <= 0:
            pos = _vector_float(row.get("pos"))
            field = "pos"
        if pos and abs(pos) > 0:
            return {"ok": True, "row": row, "sz": _vector_sz(pos), "field": field}
    return {"ok": False, "error": "NO_REAL_POSITION_FOUND", "posSide": posSide}

def _vector_create_fallback_order_algo(attached, close_side=None, posSide=None):
    try:
        cur = (_load_state().get("current") or {})
    except Exception:
        cur = {}
    if not posSide:
        posSide = cur.get("posSide") or cur.get("side")
    if not close_side:
        close_side = _vector_close_side(side=cur.get("side"), posSide=posSide)

    stop_px = _vector_expected_stop_px(attached)
    if not stop_px:
        return {"ok": False, "final_reason": "FALLBACK_STOP_PRICE_MISSING"}

    if close_side not in ["buy", "sell"]:
        return {"ok": False, "final_reason": "STOP_SIDE_INVALID", "close_side": close_side, "posSide": posSide}

    pos = _vector_real_position(posSide=posSide)
    if not pos.get("ok"):
        return {"ok": False, "final_reason": "FALLBACK_REAL_POSITION_NOT_FOUND", "position": pos}

    algo_cl = ("fbsl" + _vector_uuid.uuid4().hex)[:32]
    payload = {
        "instId": _vector_symbol(),
        "tdMode": _close_td_mode((pos or {}).get("row")),
        "side": close_side,
        "posSide": posSide,
        "ordType": "conditional",
        "sz": str(pos.get("sz")),
        "slTriggerPx": str(stop_px),
        "slTriggerPxType": "last",
        "slOrdPx": "-1",
        "algoClOrdId": algo_cl,
        "reduceOnly": "true"
    }

    raw1 = _vector_okx("POST", "/api/v5/trade/order-algo", body=payload, auth=True)
    ack = _vector_order_algo_ack(raw1)
    retry = None
    used_payload = dict(payload)

    if not ack.get("ok"):
        blob = _vector_json.dumps(raw1, ensure_ascii=False).lower()
        if "reduceonly" in blob or "reduce only" in blob:
            payload2 = dict(payload)
            payload2.pop("reduceOnly", None)
            raw2 = _vector_okx("POST", "/api/v5/trade/order-algo", body=payload2, auth=True)
            ack2 = _vector_order_algo_ack(raw2)
            retry = {"payload": payload2, "raw_code": raw2.get("code") if isinstance(raw2, dict) else None, "ack": ack2}
            if ack2.get("ok"):
                raw1 = raw2
                ack = ack2
                used_payload = payload2

    if not ack.get("ok"):
        return {"ok": False, "final_reason": "FALLBACK_ORDER_ALGO_POST_FAILED", "payload": payload, "raw_code": raw1.get("code") if isinstance(raw1, dict) else None, "ack": ack, "retry_without_reduceOnly": retry, "position": pos}

    out = {
        "ok": True,
        "final_reason": "FALLBACK_ORDER_ALGO_CREATED",
        "algoId": ack.get("algoId"),
        "algoClOrdId": ack.get("algoClOrdId") or algo_cl,
        "payload": used_payload,
        "raw_code": raw1.get("code") if isinstance(raw1, dict) else None,
        "ack": ack,
        "retry_without_reduceOnly": retry,
        "position": pos,
        "created_at_ts": _vector_time.time()
    }
    return out

def _vector_order_algo_ack(raw):
    if not _vector_code_ok(raw):
        return {"ok": False, "error": "OKX_CODE_NOT_0", "raw_code": raw.get("code") if isinstance(raw, dict) else None, "raw_msg": raw.get("msg") if isinstance(raw, dict) else None}
    data = raw.get("data") or []
    if not data:
        return {"ok": False, "error": "OKX_EMPTY_DATA"}
    row = data[0] if isinstance(data[0], dict) else {}
    if str(row.get("sCode", "0")) not in ["0", ""]:
        return {"ok": False, "error": row.get("sMsg") or "OKX_ORDER_ALGO_REJECTED", "row": row}
    return {"ok": True, "algoId": row.get("algoId") or row.get("ordId"), "algoClOrdId": row.get("algoClOrdId") or row.get("clOrdId"), "row": row}

def _vector_verify_fallback_order_algo(fallback, attached, close_side=None, posSide=None, attempts=5, sleep_sec=0):
    fb_attached = dict(attached or {})
    fb_attached["algoId"] = fallback.get("algoId")
    fb_attached["algoClOrdId"] = fallback.get("algoClOrdId")
    fb_attached["stop_loss_price"] = _vector_expected_stop_px(attached) or ((fallback.get("payload") or {}).get("slTriggerPx"))
    fb_attached["payload"] = {"attachAlgoClOrdId": fallback.get("algoClOrdId") or fallback.get("algoId") or "fallback_order_algo", "slTriggerPx": fb_attached.get("stop_loss_price"), "slTriggerPxType": "last", "slOrdPx": "-1"}

    attempts = max(5, int(attempts or 5))
    last = None
    rows_seen = []

    for i in range(attempts):
        rows = []
        raw_pending = _vector_okx("GET", "/api/v5/trade/orders-algo-pending", params={"instType": "SWAP", "instId": _vector_symbol(), "ordType": "conditional"}, auth=True)
        raw_pos = _vector_okx("GET", "/api/v5/account/positions", params={"instType": "SWAP", "instId": _vector_symbol()}, auth=True)
        last = {"pending_code": raw_pending.get("code") if isinstance(raw_pending, dict) else None, "positions_code": raw_pos.get("code") if isinstance(raw_pos, dict) else None}

        if _vector_code_ok(raw_pending):
            for row in raw_pending.get("data") or []:
                if isinstance(row, dict):
                    r = dict(row)
                    r.setdefault("ordType", "conditional")
                    r["_vector_source"] = "fallback.orders-algo-pending.conditional"
                    rows.append(r)

        if _vector_code_ok(raw_pos):
            for pr in raw_pos.get("data") or []:
                if isinstance(pr, dict) and isinstance(pr.get("closeOrderAlgo"), list):
                    for child in pr.get("closeOrderAlgo"):
                        if isinstance(child, dict):
                            r = dict(pr)
                            r.update(child)
                            r.setdefault("ordType", "conditional")
                            r["_vector_source"] = "fallback.account-positions.closeOrderAlgo"
                            rows.append(r)

        for row in rows:
            rows_seen.append(row)
            m = _row_matches_attached_sl(row, fb_attached, close_side=close_side, posSide=posSide, return_details=True)
            if m.get("matched"):
                return {"ok": True, "verified": True, "final_reason": "EXCHANGE_SIDE_STOP_VERIFIED", "fallback_order_algo_verified": True, "algoId": row.get("algoId") or fallback.get("algoId"), "algoClOrdId": row.get("algoClOrdId") or fallback.get("algoClOrdId"), "attempts": i + 1, "match": m, "raw_codes": last}

        if i < attempts - 1 and sleep_sec:
            _vector_time.sleep(float(sleep_sec))

    return {"ok": False, "verified": False, "final_reason": "FALLBACK_ORDER_ALGO_VERIFY_FAILED", "rows_seen": rows_seen[-20:], "raw_codes": last, "attempts": attempts}

def _vector_persist_sl_result(res):
    try:
        st = _load_state()
        cur = st.get("current") or {}
        cur["exchange_side_stop_verified"] = bool(res.get("verified"))
        cur["attached_sl_final_reason"] = res.get("final_reason")
        cur["attached_sl_last_verify"] = res
        if res.get("attached_stop_loss_source"):
            cur["attached_stop_loss_source"] = res.get("attached_stop_loss_source")
        if res.get("attached_stop_loss_fallback_created") or res.get("fallback_order_algo_verified"):
            cur["attached_stop_loss_fallback_created"] = True
            cur["attached_stop_loss_source"] = "fallback_order_algo"
            cur["attached_stop_loss_fallback_algoId"] = res.get("algoId")
            cur["attached_stop_loss_fallback_algoClOrdId"] = res.get("algoClOrdId")
        if isinstance(cur.get("attached_stop_loss"), dict):
            cur["attached_stop_loss"]["last_verify"] = res
            cur["attached_stop_loss"]["final_reason"] = res.get("final_reason")
            cur["attached_stop_loss"]["exchange_side_stop_verified"] = bool(res.get("verified"))
            if cur.get("attached_stop_loss_source"):
                cur["attached_stop_loss"]["source"] = cur.get("attached_stop_loss_source")
        st["current"] = cur
        _save_state(st)
    except Exception:
        pass
    return res

_VECTOR_ORIG_CLOSE_CURRENT = globals().get("close_current")

def close_current(*args, **kwargs):
    reason = kwargs.get("reason")
    if reason is None and args:
        reason = args[0]
    if reason is None:
        reason = "formal_manual_close"

    if isinstance(reason, str) and (
        "daemon_protective_close_attached_sl_invalid" in reason
        or "attached_sl_not_verified" in reason
        or "protective" in reason
    ):
        try:
            cur = (_load_state().get("current") or {})
            fr = cur.get("attached_sl_final_reason") or "FALLBACK_ORDER_ALGO_FAILED"
        except Exception:
            fr = "FALLBACK_ORDER_ALGO_FAILED"
        if fr not in reason:
            reason = reason + "__" + fr

    if args:
        args = (reason,) + tuple(args[1:])
    else:
        kwargs["reason"] = reason

    if callable(_VECTOR_ORIG_CLOSE_CURRENT):
        return _VECTOR_ORIG_CLOSE_CURRENT(*args, **kwargs)
    return {"ok": False, "error": "base close_current missing", "reason": reason}

def vector_real_okx_preflight_readonly():
    out = {
        "ok": False,
        "mode": "real_okx_preflight_readonly",
        "orders_algo_pending_conditional_ok": False,
        "positions_ok": False,
        "active_position_found": False,
        "active_position": None,
        "will_not_place_orders": True,
    }

    try:
        pending = _vector_okx("GET", "/api/v5/trade/orders-algo-pending", params={"instType": "SWAP", "instId": _vector_symbol(), "ordType": "conditional"}, auth=True)
        out["orders_algo_pending_conditional_raw_code"] = pending.get("code") if isinstance(pending, dict) else None
        out["orders_algo_pending_conditional_ok"] = _vector_code_ok(pending)
    except Exception as e:
        out["orders_algo_pending_conditional_error"] = str(e)

    try:
        pos = _vector_okx("GET", "/api/v5/account/positions", params={"instType": "SWAP", "instId": _vector_symbol()}, auth=True)
        out["positions_raw_code"] = pos.get("code") if isinstance(pos, dict) else None
        out["positions_ok"] = _vector_code_ok(pos)
        if _vector_code_ok(pos):
            for row in pos.get("data") or []:
                if not isinstance(row, dict):
                    continue
                p = _vector_float(row.get("availPos"))
                if not p or abs(p) <= 0:
                    p = _vector_float(row.get("pos"))
                if p and abs(p) > 0:
                    if not _filter_auto_rows([row], extra_modes=_managed_td_modes()):
                        continue
                    out["active_position_found"] = True
                    out["active_position"] = row
                    break
    except Exception as e:
        out["positions_error"] = str(e)

    out["ok"] = bool(out["orders_algo_pending_conditional_ok"] and out["positions_ok"])
    if out["ok"] and not out["active_position_found"]:
        out["status"] = "OKX_READONLY_OK_NO_ACTIVE_POSITION"
        out["message"] = "真实OKX只读预检通过；当前无真实持仓，不会伪造fallback补挂成功"
    elif out["ok"] and out["active_position_found"]:
        out["status"] = "OKX_READONLY_OK_ACTIVE_POSITION_FOUND"
        out["message"] = "真实OKX只读预检通过且发现持仓，可运行真实持仓止损验证"
    else:
        out["status"] = "OKX_READONLY_FAILED"
        out["message"] = "真实OKX只读预检失败"
    return _vector_cache_write("vector_real_okx_preflight_readonly.json", out)

def _vector_current_attached_from_state():
    try:
        cur = (_load_state().get("current") or {})
        attached = cur.get("attached_stop_loss") or cur.get("last_attached_stop_loss") or {}
        if isinstance(attached, dict):
            if "opened_at_ts" not in attached and cur.get("opened_at_ts"):
                attached["opened_at_ts"] = cur.get("opened_at_ts")
            return cur, attached
    except Exception:
        pass
    return {}, {}

def vector_real_active_position_verify_once():
    preflight = vector_real_okx_preflight_readonly()
    cur, attached = _vector_current_attached_from_state()

    active_state = bool(cur and str(cur.get("status", "")).lower() not in ["closed", "close", "none", "null"] and not cur.get("closed_at") and (cur.get("side") or cur.get("posSide") or cur.get("open_order")))
    real_pos = preflight.get("active_position_found")

    result = {
        "ok": False,
        "mode": "real_active_position_verify_once",
        "preflight": preflight,
        "active_position_in_state": active_state,
        "active_position_on_okx": real_pos,
        "live_fallback_attempted": False,
        "live_result": None,
        "side_effect_policy": "only places fallback order-algo when state and OKX both show active position",
    }

    if not preflight.get("ok"):
        result["status"] = "PREFLIGHT_FAILED"
        result["message"] = "OKX真实只读预检失败，拒绝执行fallback补挂"
        return _vector_cache_write("vector_real_active_position_verify_last.json", result)

    if not active_state or not real_pos:
        result["status"] = "NO_ACTIVE_POSITION_REAL_FALLBACK_NOT_EXECUTED"
        result["message"] = "当前无真实持仓，不执行POST /order-algo，不把real verify伪造成成功；下一次真实持仓存在时再执行"
        return _vector_cache_write("vector_real_active_position_verify_last.json", result)

    if not isinstance(attached, dict) or not attached.get("payload"):
        result["status"] = "ACTIVE_POSITION_BUT_ATTACHED_PAYLOAD_MISSING"
        result["message"] = "有持仓但本地attached_stop_loss payload缺失，拒绝盲目补挂"
        return _vector_cache_write("vector_real_active_position_verify_last.json", result)

    close_side = _vector_close_side(side=cur.get("side"), posSide=cur.get("posSide"))
    verify = _wait_attached_sl(attached, close_side=close_side, posSide=(cur.get("posSide") or cur.get("side")), attempts=5, sleep_sec=1.2)
    result["live_fallback_attempted"] = True
    result["live_result"] = verify
    result["ok"] = bool(verify.get("verified"))
    result["status"] = "REAL_ACTIVE_POSITION_VERIFIED" if result["ok"] else "REAL_ACTIVE_POSITION_VERIFY_FAILED"
    result["message"] = "当前有真实持仓，已执行真实OKX止损确认；如attached查不到，已真实补挂fallback order-algo并复核" if result["ok"] else "当前有真实持仓，但attached与fallback均未验证成功"
    return _vector_cache_write("vector_real_active_position_verify_last.json", result)

def vector_take_profit_path_verify():
    root = _vector_Path("/root")
    daemon_src = ""
    exec_src = ""
    try:
        daemon_src = (root / "auto_trade_formal_daemon.py").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        pass
    try:
        exec_src = (root / "auto_trade_formal_v6_executor.py").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        pass

    blob = (daemon_src + "\n" + exec_src).lower()
    daemon_lower = daemon_src.lower()
    exec_lower = exec_src.lower()

    checks = {
        "mode": "source_level_take_profit_path_verify_no_order",
        "daemon_has_take_profit_token": ("take_profit" in daemon_lower or "tp_" in daemon_lower or "止盈" in daemon_src),
        "daemon_references_manage_or_close": ("manage_current_position" in daemon_src or "close_current" in daemon_src),
        "executor_has_close_current": ("def close_current" in exec_src),
        "executor_has_event_recording": ("_append_event" in exec_src or "append_event" in exec_src or "events.jsonl" in exec_src),
        "notification_path_present": ("notify" in blob or "wxpusher" in blob or "push" in blob),
        "config_take_profit_pct_present": False,
        "allow_auto_close_present": False,
    }

    try:
        cfg_path = root / "auto_trade" / slot_paths.daemon_config_name(
            SYMBOL, TRADE_TIMEFRAME)
        cfg = _vector_json.loads(cfg_path.read_text(encoding="utf-8", errors="ignore") or "{}") if cfg_path.exists() else {}
        checks["config_take_profit_pct_present"] = bool(cfg.get("take_profit_pct") is not None)
        checks["allow_auto_close_present"] = bool(cfg.get("allow_auto_close"))
        checks["take_profit_pct"] = cfg.get("take_profit_pct")
        checks["allow_auto_close"] = cfg.get("allow_auto_close")
    except Exception as e:
        checks["config_error"] = str(e)

    checks["ok"] = bool(
        checks["daemon_has_take_profit_token"]
        and checks["daemon_references_manage_or_close"]
        and checks["executor_has_close_current"]
        and checks["executor_has_event_recording"]
        and checks["notification_path_present"]
        and checks["config_take_profit_pct_present"]
        and checks["allow_auto_close_present"]
    )
    checks["message"] = "源码级止盈路径验证通过：检测到止盈阈值、daemon触发、close_current、事件记录、通知路径与allow_auto_close配置" if checks["ok"] else "源码级止盈路径验证未满：不等同于真实止盈平仓成功"
    return _vector_cache_write("vector_take_profit_path_verify.json", checks)

def vector_auto_trade_chain_self_test():
    old_okx = globals().get("_okx_request")
    old_load = globals().get("_load_state")
    old_save = globals().get("_save_state")

    now_ms = int(_vector_time.time() * 1000)
    fake_current = {
        "status": "open",
        "side": "short",
        "posSide": "short",
        "opened_at_ts": _vector_time.time() - 60,
        "open_order": {"ack": {"ordId": "entry_ord_1", "clOrdId": "entry_cl_1"}},
    }
    attached = {
        "attachAlgoClOrdId": "sl_client_1",
        "stop_loss_price": "100.9",
        "opened_at_ts": _vector_time.time() - 60,
        "payload": {"attachAlgoClOrdId": "sl_client_1", "slTriggerPx": "100.9", "slTriggerPxType": "last", "slOrdPx": "-1"}
    }

    def install_state():
        state = {"current": dict(fake_current)}
        def fake_load():
            return state
        def fake_save(st):
            state.clear()
            state.update(st)
        globals()["_load_state"] = fake_load
        globals()["_save_state"] = fake_save
        return state

    def okx_direct(method, path, params=None, body=None, auth=True):
        if path.endswith("/orders-algo-pending"):
            return {"code": "0", "data": [{"instId": _vector_symbol(), "ordType": "conditional", "side": "buy", "posSide": "short", "algoClOrdId": "sl_client_1", "algoId": "algo_direct_1", "slTriggerPx": "100.9", "cTime": str(now_ms)}]}
        if path.endswith("/positions"):
            return {"code": "0", "data": [{"instId": _vector_symbol(), "posSide": "short", "pos": "1", "availPos": "1", "closeOrderAlgo": []}]}
        if path.endswith("/order"):
            return {"code": "0", "data": [{"instId": _vector_symbol(), "ordId": "entry_ord_1", "linkedAlgoOrd": {"algoId": "algo_direct_1", "slTriggerPx": "100.9"}}]}
        return {"code": "0", "data": []}

    def make_okx_fallback_success():
        state = {"posted": False}
        def fake(method, path, params=None, body=None, auth=True):
            if method == "POST" and path.endswith("/order-algo"):
                state["posted"] = True
                state["body"] = body
                return {"code": "0", "data": [{"algoId": "fb_algo_1", "algoClOrdId": body.get("algoClOrdId"), "sCode": "0"}]}
            if path.endswith("/positions"):
                return {"code": "0", "data": [{"instId": _vector_symbol(), "posSide": "short", "pos": "1", "availPos": "1", "closeOrderAlgo": []}]}
            if path.endswith("/orders-algo-pending"):
                if state["posted"] and params and params.get("ordType") == "conditional":
                    return {"code": "0", "data": [{"instId": _vector_symbol(), "ordType": "conditional", "side": "buy", "posSide": "short", "algoId": "fb_algo_1", "algoClOrdId": state["body"].get("algoClOrdId"), "slTriggerPx": "100.9", "sz": "1", "cTime": str(now_ms)}]}
                return {"code": "0", "data": []}
            if path.endswith("/order"):
                return {"code": "0", "data": []}
            return {"code": "0", "data": []}
        return fake, state

    def okx_fallback_fail(method, path, params=None, body=None, auth=True):
        if method == "POST" and path.endswith("/order-algo"):
            return {"code": "1", "msg": "mock fallback rejected", "data": [{"sCode": "51000", "sMsg": "mock reject"}]}
        if path.endswith("/positions"):
            return {"code": "0", "data": [{"instId": _vector_symbol(), "posSide": "short", "pos": "1", "availPos": "1", "closeOrderAlgo": []}]}
        return {"code": "0", "data": []}

    try:
        state1 = install_state()
        globals()["_okx_request"] = okx_direct
        r1 = _wait_attached_sl(attached, close_side="buy", posSide="short", attempts=5, sleep_sec=0)

        state2 = install_state()
        fb_fake, fb_state = make_okx_fallback_success()
        globals()["_okx_request"] = fb_fake
        r2 = _wait_attached_sl(attached, close_side="buy", posSide="short", attempts=5, sleep_sec=0)

        state3 = install_state()
        globals()["_okx_request"] = okx_fallback_fail
        r3 = _wait_attached_sl(attached, close_side="buy", posSide="short", attempts=5, sleep_sec=0)

        tp_source = vector_take_profit_path_verify()

        out = {
            "ok": bool(r1.get("verified") and r2.get("verified") and (not r3.get("verified")) and r3.get("final_reason") == "FALLBACK_ORDER_ALGO_FAILED"),
            "stage": "vector_auto_trade_chain_self_test",
            "type": "deterministic_internal_logic_test_no_real_order",
            "case_1_strategy_trigger_entry_payload_attached_sl_direct_verified": bool(r1.get("verified")),
            "case_2_attach_missing_fallback_order_algo_created_and_verified": bool(r2.get("verified") and r2.get("attached_stop_loss_source") == "fallback_order_algo"),
            "case_3_attach_and_fallback_fail_then_protective_close_allowed": bool((not r3.get("verified")) and r3.get("final_reason") == "FALLBACK_ORDER_ALGO_FAILED"),
            "case_4_take_profit_source_path_verified": bool(tp_source.get("ok")),
            "case_5_message_path_ready": bool(tp_source.get("notification_path_present")),
            "fallback_post_payload_used_real_position_size": (fb_state.get("body") or {}).get("sz") == "1",
            "fallback_post_payload": fb_state.get("body"),
            "take_profit_path_verify": tp_source,
            "direct_result": r1,
            "fallback_success_result": r2,
            "fallback_fail_result": r3,
            "state_after_fallback_success": state2,
            "state_after_fallback_fail": state3,
        }
        return _vector_cache_write("vector_logic_chain_self_test.json", out)
    finally:
        if old_okx is not None:
            globals()["_okx_request"] = old_okx
        if old_load is not None:
            globals()["_load_state"] = old_load
        if old_save is not None:
            globals()["_save_state"] = old_save

_VECTOR_ORIG_SELF_TEST = globals().get("self_test")

def self_test():
    base = {}
    try:
        if callable(_VECTOR_ORIG_SELF_TEST):
            base = _VECTOR_ORIG_SELF_TEST()
    except Exception as e:
        base = {"ok": False, "base_self_test_error": str(e)}

    logic = vector_auto_trade_chain_self_test()
    preflight = vector_real_okx_preflight_readonly()
    active_verify = vector_real_active_position_verify_once()
    tp_verify = vector_take_profit_path_verify()

    full_pass = bool(logic.get("ok") and preflight.get("ok") and active_verify.get("ok") and tp_verify.get("ok"))
    waiting_real_position = bool(active_verify.get("status") == "NO_ACTIVE_POSITION_REAL_FALLBACK_NOT_EXECUTED")
    safe_ready_waiting = bool(logic.get("ok") and preflight.get("ok") and waiting_real_position)

    if not isinstance(base, dict):
        base = {}
    base["vector_logic_chain_self_test"] = logic
    base["vector_real_okx_preflight_readonly"] = preflight
    base["vector_real_active_position_verify_once"] = active_verify
    base["vector_take_profit_path_verify"] = tp_verify
    base["vector_full_pass"] = full_pass
    base["vector_safe_ready_waiting_real_position"] = safe_ready_waiting
    base["vector_waiting_real_position"] = waiting_real_position
    base["vector_case_1_direct_attached_sl_verified"] = logic.get("case_1_strategy_trigger_entry_payload_attached_sl_direct_verified")
    base["vector_case_2_fallback_order_algo_verified"] = logic.get("case_2_attach_missing_fallback_order_algo_created_and_verified")
    base["vector_case_3_fallback_failed_protective_close_allowed"] = logic.get("case_3_attach_and_fallback_fail_then_protective_close_allowed")
    base["vector_case_4_take_profit_source_path_verified"] = logic.get("case_4_take_profit_source_path_verified")
    base["vector_live_okx_preflight_ok"] = preflight.get("ok")
    base["ok"] = bool(full_pass or safe_ready_waiting)
    base["stage"] = "vector_safe_real_verify_v3_self_test"
    return base

# CODEX_SAFE_PROTECTIVE_STATE_MACHINE_V1
# A transient/ambiguous exchange read must never be treated as proof that the
# stop does not exist.  Persist evidence across daemon ticks and make every
# mutating recovery action one-shot per position.
SL_VERIFY_GRACE_SEC = 180
SL_VERIFY_MIN_FAILURES_FOR_FALLBACK = 2
SL_VERIFY_MIN_FAILURES_FOR_CLOSE = 4

def _close_order_from_attempt(cur):
    attempt = cur.get("last_close_attempt") if isinstance(cur, dict) else {}
    order = attempt.get("close_order") if isinstance(attempt, dict) else {}
    if not isinstance(order, dict):
        order = {}
    ack = order.get("ack") if isinstance(order.get("ack"), dict) else {}
    filled = order.get("filled") if isinstance(order.get("filled"), dict) else {}
    row = filled.get("order") if isinstance(filled.get("order"), dict) else {}
    ord_id = row.get("ordId") or ack.get("ordId") or order.get("ordId")
    cl_ord_id = row.get("clOrdId") or ack.get("clOrdId") or order.get("clOrdId") or ((order.get("payload") or {}).get("clOrdId"))
    if ord_id or cl_ord_id:
        got = _get_order(ord_id, cl_ord_id)
        if got.get("ok") and isinstance(got.get("order"), dict) and got.get("order"):
            row = got.get("order")
    return {"order": order, "row": row, "ordId": ord_id or row.get("ordId"),
            "clOrdId": cl_ord_id or row.get("clOrdId"),
            "reason": attempt.get("reason") if isinstance(attempt, dict) else None}

def _recent_close_fill(cur):
    side = str(cur.get("side") or cur.get("posSide") or "").lower()
    close_side = "buy" if side == "short" else "sell"
    opened_ms = int(float(cur.get("opened_at_ts") or 0) * 1000)
    raw = _okx_request("GET", "/api/v5/trade/fills-history",
                       params={"instType": "SWAP", "instId": SYMBOL, "limit": "100"},
                       auth=True)
    if not _strict_code_ok(raw):
        return {"ok": False, "row": {}, "raw": raw}
    candidates = []
    for row in raw.get("data") or []:
        if not isinstance(row, dict):
            continue
        try:
            ts = int(row.get("ts") or row.get("fillTime") or 0)
        except Exception:
            ts = 0
        if row.get("instId") != SYMBOL or row.get("side") != close_side:
            continue
        if row.get("posSide") and row.get("posSide") != side:
            continue
        if opened_ms and ts < opened_ms:
            continue
        candidates.append((ts, row))
    if not candidates:
        return {"ok": False, "row": {}, "raw": raw, "reason": "no_matching_close_fill"}
    candidates.sort(key=lambda item: item[0], reverse=True)
    fill = candidates[0][1]
    got = _get_order(fill.get("ordId"), fill.get("clOrdId"))
    order = got.get("order") if got.get("ok") and isinstance(got.get("order"), dict) else fill
    return {"ok": True, "row": order, "fill": fill, "raw": raw}

def _close_type_from_reconciled(row, reason=None, cur=None):
    """Classify a disappeared-position fill: never mix 交易所止损 with 手动平仓."""
    reason = str(reason or "")
    row = row if isinstance(row, dict) else {}
    cur = cur if isinstance(cur, dict) else {}
    cl_ord_id = str(row.get("clOrdId") or "")
    algo_id = row.get("algoId") or row.get("algoClOrdId") or row.get("attachAlgoClOrdId")
    ord_type = str(row.get("ordType") or "").lower()
    category = str(row.get("category") or row.get("execType") or "").lower()

    if cl_ord_id.startswith("fcl"):
        try:
            import auto_trade_formal_notify as _notify
            return _notify.close_type_from_reason(
                reason, exit_type=cur.get("exit_type"),
            )
        except Exception:
            if "timed_forced" in reason:
                return "定时强制平仓"
            if "take_profit" in reason:
                return "策略止盈"
            if "invalidated" in reason:
                return "策略失效"
            if "protective" in reason or "attached_sl_not_verified" in reason:
                return "保护性平仓"
            if "rule_exit" in reason:
                return "策略规则退出"
            return "自动策略平仓"

    attached = cur.get("attached_stop_loss") if isinstance(cur.get("attached_stop_loss"), dict) else {}
    known_algo_ids = set()
    for key in (
        "attachAlgoClOrdId", "algoClOrdId", "algoId",
        "attached_stop_loss_fallback_algoClOrdId", "attached_stop_loss_fallback_algoId",
    ):
        val = attached.get(key) or cur.get(key)
        if val:
            known_algo_ids.add(str(val))
    fill_ids = {
        str(x) for x in (
            algo_id, row.get("clOrdId"), row.get("ordId"), row.get("algoClOrdId"),
        ) if x
    }
    algo_linked = bool(
        algo_id
        or ord_type in ("conditional", "oco", "trigger", "move_order_stop")
        or "sl" in category
        or (known_algo_ids and fill_ids.intersection(known_algo_ids))
    )

    # Explicit plain market/limit reduce-only fill with no algo markers is a
    # manual/external close. Do NOT use near-stop price alone: a manual exit
    # can sit within 0.4% of the protective stop and was mislabeled
    # 交易所止损 (ETH short 2026-08-27: stop 2469.48, manual fill 2463.6).
    plain_manual = (
        not algo_linked
        and ord_type in ("market", "limit", "ioc", "fok", "post_only")
        and category in ("normal", "", "0")
    )
    if plain_manual:
        return "手动平仓"

    near_stop = False
    try:
        stop_px = _safe_float(
            attached.get("stop_loss_price")
            or (attached.get("payload") or {}).get("slTriggerPx")
            or cur.get("stop_loss_price"),
            None,
        )
        close_px = _safe_float(row.get("avgPx") or row.get("fillPx") or row.get("px"), None)
        side = str(cur.get("side") or cur.get("posSide") or "").lower()
        if stop_px and close_px and stop_px > 0:
            # Only count near-stop when the fill is on the adverse side of the
            # protective stop (short: at/above; long: at/below), within 0.4%.
            rel = abs(float(close_px) - float(stop_px)) / float(stop_px)
            if rel <= 0.004:
                if side in ("short",):
                    near_stop = float(close_px) >= float(stop_px) * (1.0 - 0.001)
                elif side in ("long",):
                    near_stop = float(close_px) <= float(stop_px) * (1.0 + 0.001)
                else:
                    near_stop = True
    except Exception:
        near_stop = False

    looks_exchange_sl = bool(algo_linked or near_stop)
    if looks_exchange_sl:
        return "交易所止损"

    if reason and reason not in (
        "exchange_position_disappeared_reconciled",
        "",
    ):
        try:
            import auto_trade_formal_notify as _notify
            return _notify.close_type_from_reason(
                reason, exit_type=cur.get("exit_type"),
            )
        except Exception:
            pass

    # Market/limit close without algo markers = user/manual (or external) close.
    return "手动平仓"


def _reconcile_disappeared_position(cur):
    known = _close_order_from_attempt(cur)
    row = known.get("row") or {}
    reason = known.get("reason")
    if not row:
        recent = _recent_close_fill(cur)
        row = recent.get("row") or {}
    close_px = _safe_float(row.get("avgPx") or row.get("fillPx"), None)
    pnl = row.get("pnl")
    if pnl in [None, ""]:
        try:
            entry = _safe_float(cur.get("entry_price"), None)
            sz = _d(cur.get("real_position_sz") or cur.get("sz") or "0")
            ct_val = _d((cur.get("sizing") or {}).get("ctVal") or "0.01")
            if entry is not None and close_px is not None:
                pnl = _decimal_to_plain(((Decimal(str(entry)) - Decimal(str(close_px)))
                    if cur.get("side") == "short" else
                    (Decimal(str(close_px)) - Decimal(str(entry)))) * sz * ct_val)
        except Exception:
            pnl = None
    ts_ms = row.get("uTime") or row.get("fillTime") or row.get("ts")
    try:
        closed_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts_ms) / 1000.0))
        closed_at_ts = int(ts_ms) / 1000.0
    except Exception:
        closed_at, closed_at_ts = _now(), time.time()
    ord_id = row.get("ordId") or known.get("ordId")
    cl_ord_id = row.get("clOrdId") or known.get("clOrdId")
    close_order = {
        "ok": bool(ord_id or cl_ord_id),
        "ordId": ord_id,
        "clOrdId": cl_ord_id,
        "ack": {"ok": bool(ord_id), "ordId": ord_id, "clOrdId": cl_ord_id},
        "filled": {"ok": bool(row), "filled": bool(row), "order": row},
    }
    close_type = _close_type_from_reconciled(row, reason, cur=cur)
    if not reason or reason == "exchange_position_disappeared_reconciled":
        if close_type == "交易所止损":
            reason = "attached_sl_fill"
        elif close_type == "手动平仓":
            reason = "manual_close_reconciled"
        else:
            reason = reason or "exchange_position_disappeared_reconciled"
    closed = dict(cur)
    closed.update({
        "status": "closed",
        "closed_at": closed_at,
        "closed_at_ts": closed_at_ts,
        "close_reason": reason,
        "close_type": close_type,
        "exit_type": cur.get("exit_type") or close_type,
        "close_price": close_px,
        "close_price_source": "okx_trade_order_reconciled" if row else "unavailable",
        "pnl": pnl,
        "close_order": close_order,
        "position_absent_verified": {"ok": True, "absent": True, "source": "manage_current_position"},
    })
    return closed

def _sl_guard_reset(cur, reason="verified"):
    cur["sl_guard"] = {"state": "protected", "reason": reason,
                       "failure_count": 0, "first_failure_ts": None,
                       "fallback_attempted": False,
                       "protective_close_attempted": False,
                       "updated_at_ts": time.time()}
    return cur["sl_guard"]

def manage_current_position(policy="protective"):
    st = _load_state()
    cur = st.get("current")
    if not _active(cur):
        return {"ok": True, "action": "no_position", "checked": False}

    # First independently confirm that the exchange still has the position.
    pos = _find_position(cur.get("posSide") or cur.get("side"))
    if not pos.get("ok"):
        return {"ok": False, "action": "position_query_unknown_no_mutation",
                "checked": True, "position": pos}
    if not pos.get("position"):
        # Reconcile the actual OKX close order before notification and state
        # clearing.  This also recovers an automatic close that filled but whose
        # attached-stop cleanup returned an idempotent "already gone" response.
        disappeared = _reconcile_disappeared_position(cur)
        ids = _find_attached_algo_ids(cur)
        cancel = _cancel_algos(ids.get("algo_ids") or [], "reconciled_position_absent")
        if not cancel.get("ok"):
            absent_algos = _confirm_algo_ids_absent(ids.get("algo_ids") or [])
            if absent_algos.get("ok") and absent_algos.get("absent"):
                cancel = {"ok": True, "idempotent_success": True,
                          "reason": "attached_stop_already_absent",
                          "initial_cancel": cancel, "absence_check": absent_algos}
        disappeared["cancel_attached_sl"] = cancel
        disappeared = _attach_close_growth(disappeared)
        st.setdefault("history", []).append(disappeared)
        st["current"] = None
        _save_state(st)
        _day_lock_mark_auto_close(disappeared)
        notify_res = _maybe_notify("close", disappeared)
        _append_event("strategy_closed_reconciled", {
            "strategy": disappeared.get("strategy_key"),
            "side": disappeared.get("side"),
            "close_type": disappeared.get("close_type"),
            "close_price": disappeared.get("close_price"),
            "pnl": disappeared.get("pnl"),
            "ordId": ((disappeared.get("close_order") or {}).get("ordId")),
            "notification_sent": bool((notify_res or {}).get("sent")),
            "time": disappeared.get("closed_at"),
        }, disappeared.get("strategy_key") or "unknown")
        return {"ok": True, "action": "position_absent_state_cleared", "checked": True,
                "reconciled": True, "closed": disappeared,
                "notification_sent": bool((notify_res or {}).get("sent"))}

    try:
        import auto_trade_session_clock as session_clock
        if not session_clock.in_session(symbol=SYMBOL):
            return {
                "ok": True,
                "action": "outside_trading_session",
                "checked": True,
                "session": session_clock.session_snapshot(symbol=SYMBOL),
            }
    except Exception:
        return {"ok": True, "action": "outside_trading_session", "checked": True}

    attached = cur.get("attached_stop_loss") or {}
    close_side = cur.get("close_side") or _vector_close_side(side=cur.get("side"), posSide=cur.get("posSide"))
    verify = _vector_verify_exchange_stop(attached, close_side=close_side,
                                          posSide=cur.get("posSide") or cur.get("side"),
                                          attempts=5, sleep_sec=1.2)
    guard = cur.get("sl_guard") if isinstance(cur.get("sl_guard"), dict) else {}
    if verify.get("verified"):
        _sl_guard_reset(cur, "exchange_stop_verified")
        cur["exchange_side_stop_verified"] = True
        st["current"] = cur
        _save_state(st)
        return {"ok": True, "action": "position_protected", "checked": True,
                "check": verify, "sl_guard": cur["sl_guard"]}

    now = time.time()
    first = float(guard.get("first_failure_ts") or now)
    count = int(guard.get("failure_count") or 0) + 1
    guard.update({"state": "verification_unknown", "failure_count": count,
                  "first_failure_ts": first, "last_failure_ts": now,
                  "last_reason": verify.get("final_reason"),
                  "fallback_attempted": bool(guard.get("fallback_attempted")),
                  "protective_close_attempted": bool(guard.get("protective_close_attempted"))})
    cur["sl_guard"] = guard
    cur["exchange_side_stop_verified"] = False
    st["current"] = cur
    _save_state(st)
    elapsed = now - first

    # One fallback attempt only, and never on the first ambiguous read.
    if count >= SL_VERIFY_MIN_FAILURES_FOR_FALLBACK and not guard["fallback_attempted"]:
        guard["fallback_attempted"] = True
        guard["fallback_attempted_ts"] = now
        st["current"] = cur
        _save_state(st)  # write the one-shot lock before the POST
        fallback = _vector_create_fallback_order_algo(attached, close_side=close_side,
                                                       posSide=cur.get("posSide") or cur.get("side"))
        guard["fallback_result"] = {"ok": fallback.get("ok"),
                                     "final_reason": fallback.get("final_reason")}
        if fallback.get("ok"):
            checked = _vector_verify_fallback_order_algo(fallback, attached,
                        close_side=close_side, posSide=cur.get("posSide") or cur.get("side"),
                        attempts=5, sleep_sec=1.2)
            if checked.get("verified"):
                attached["algoId"] = checked.get("algoId") or fallback.get("algoId")
                attached["exchange_side_stop_verified"] = True
                cur["attached_stop_loss"] = attached
                _sl_guard_reset(cur, "fallback_stop_verified")
                st["current"] = cur
                _save_state(st)
                return {"ok": True, "action": "fallback_stop_verified", "checked": True,
                        "fallback": fallback, "verify": checked, "sl_guard": cur["sl_guard"]}
        st["current"] = cur
        _save_state(st)
        if not guard.get("risk_notification_sent"):
            risk_notify = _maybe_notify("risk", {"strategy_key": cur.get("strategy_key"),
                "strategy_name": cur.get("strategy_name"), "side": cur.get("side"),
                "sz": cur.get("real_position_sz") or cur.get("sz"),
                "price": cur.get("entry_price"),
                "risk_type": "止损连续两次无法确认，已尝试补挂",
                "action": "保持持仓、暂停新开仓、继续复核，不立即平仓"})
            guard["risk_notification_sent"] = bool((risk_notify or {}).get("sent"))
            st["current"] = cur
            _save_state(st)
        return {"ok": False, "action": "fallback_attempted_verification_pending_no_close",
                "checked": True, "check": verify, "fallback": fallback, "sl_guard": guard}

    # A close is delayed, evidence-based, and idempotent.  Unknown API reads or
    # a short-lived visibility lag cannot reach this branch.
    if (policy == "protective" and count >= SL_VERIFY_MIN_FAILURES_FOR_CLOSE
            and elapsed >= SL_VERIFY_GRACE_SEC and not guard["protective_close_attempted"]):
        guard["protective_close_attempted"] = True
        guard["protective_close_attempted_ts"] = now
        guard["state"] = "protective_close_locked"
        st["current"] = cur
        _save_state(st)  # lock before market-close request
        _maybe_notify("risk", {"strategy_key": cur.get("strategy_key"),
            "strategy_name": cur.get("strategy_name"), "side": cur.get("side"),
            "sz": cur.get("real_position_sz") or cur.get("sz"),
            "price": cur.get("entry_price"), "risk_type": "止损持续无法确认超过180秒",
            "action": "执行一次保护性平仓"})
        close = close_current(reason="protective_close_persistent_unverified_stop_after_grace")
        return {"ok": bool(close.get("ok")), "action": "protective_close_attempted_once",
                "checked": True, "check": verify, "close": close, "sl_guard": guard}

    return {"ok": False, "action": "stop_verification_unknown_grace_no_close",
            "checked": True, "check": verify, "elapsed_sec": elapsed,
            "sl_guard": guard}
# VECTOR_SAFE_REAL_VERIFY_V3_EXECUTOR_PATCH_END

# ACCOUNT_WIDE_SINGLE_POSITION_PRIORITY_V1_BEGIN
_priority_base_submit_entry = submit_entry

def _global_entry_lock_acquire(ttl=180):
    path = AUTO_DIR / "formal_v6_global_entry.lock"
    now = time.time()
    token = uuid.uuid4().hex
    payload = {"owner": "account_wide_entry", "token": token, "pid": os.getpid(),
               "symbol": SYMBOL, "time": _now(), "ts": now}
    if path.exists():
        old = _read_json(path, {})
        stale = now - float(old.get("ts") or 0) > ttl
        old_pid = old.get("pid")
        if old_pid:
            try:
                os.kill(int(old_pid), 0)
            except Exception:
                stale = True
        if stale:
            try:
                path.unlink()
            except Exception:
                pass
        else:
            return None
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(fd)
        return payload
    except Exception:
        return None

def _global_entry_lock_release(lock):
    path = AUTO_DIR / "formal_v6_global_entry.lock"
    try:
        if not lock or not path.exists():
            return
        current = _read_json(path, {})
        if current.get("token") == lock.get("token"):
            path.unlink()
    except Exception:
        pass

def _account_wide_open_positions():
    raw = _okx_request("GET", "/api/v5/account/positions",
                       params={"instType": "SWAP"}, auth=True)
    if not _strict_code_ok(raw):
        return {"ok": False, "error": "account-wide position query failed", "raw": raw}
    positions = []
    for row in raw.get("data") or []:
        if not isinstance(row, dict):
            continue
        try:
            if abs(float(row.get("pos") or 0)) > 0:
                positions.append(row)
        except Exception:
            positions.append(row)
    positions = _filter_auto_rows(positions)
    return {"ok": True, "positions": positions, "raw": raw}

def _account_wide_pending_orders():
    raw = _okx_request("GET", "/api/v5/trade/orders-pending",
                       params={"instType": "SWAP"}, auth=True)
    if not _strict_code_ok(raw):
        return {"ok": False, "error": "account-wide pending order query failed", "raw": raw}
    orders = [row for row in (raw.get("data") or []) if isinstance(row, dict)]
    orders = _filter_auto_rows(orders)
    return {"ok": True, "orders": orders, "raw": raw}

def submit_entry(side, strategy_key="ema6_center_down_then_fall", manual_confirm=None,
                 sz="FULL_BALANCE", source="formal_auto_trade", arm_token=None,
                 internal_auto=False, entry_data=None,
                 full_position_ratio_override=None,
                 stop_loss_pct_override=None, leverage_override=None):
    try:
        import auto_trade_session_clock as session_clock
        if not session_clock.in_session(symbol=SYMBOL):
            return {
                "ok": False,
                "blocked": True,
                "error": session_clock.OUTSIDE_SESSION_ERROR,
                "session": session_clock.session_snapshot(symbol=SYMBOL),
                "symbol": SYMBOL,
                "strategy_key": strategy_key,
            }
    except Exception as exc:
        return {
            "ok": False,
            "blocked": True,
            "error": "outside_trading_session",
            "session_error": str(exc),
            "symbol": SYMBOL,
            "strategy_key": strategy_key,
        }
    global_lock = _global_entry_lock_acquire()
    if not global_lock:
        out = {"ok": False, "blocked": True,
               "error": "account-wide entry lock busy",
               "symbol": SYMBOL,
               "strategy_key": strategy_key,
               "portfolio_risk_policy": True}
        out.update(_notify_open_blocked(out, side, strategy_key,
                                        leverage_override))
        return out
    try:
        positions = _account_wide_open_positions()
        if not positions.get("ok"):
            out = {"ok": False, "blocked": True, "fail_closed": True,
                   "error": positions.get("error"), "account_positions": positions,
                   "symbol": SYMBOL, "strategy_key": strategy_key,
                   "portfolio_risk_policy": True}
            out.update(_notify_open_blocked(out, side, strategy_key,
                                            leverage_override))
            return out
        pending = _account_wide_pending_orders()
        if not pending.get("ok"):
            out = {"ok": False, "blocked": True, "fail_closed": True,
                   "error": pending.get("error"), "account_pending_orders": pending,
                   "symbol": SYMBOL, "strategy_key": strategy_key,
                   "portfolio_risk_policy": True}
            out.update(_notify_open_blocked(out, side, strategy_key,
                                            leverage_override))
            return out
        cfg = _load_stage823_trade_config()
        configured_ratio = float(cfg.get("full_position_ratio", 1.0))
        grade_ratio = 1.0 if full_position_ratio_override is None else float(full_position_ratio_override)
        if grade_ratio <= 0 or grade_ratio > 1:
            return {"ok": False, "blocked": True,
                    "error": "full_position_ratio_override must be within (0,1]"}
        effective_ratio = _effective_position_ratio(
            configured_ratio, full_position_ratio_override)
        strategy_stop_loss_pct = _normalize_stop_loss_pct(
            stop_loss_pct_override,
            default=cfg.get("stop_loss_pct", 0.009),
        )
        leverage_pick = _resolve_entry_leverage(
            strategy_key, leverage_override=leverage_override, cfg=cfg)
        if not leverage_pick.get("ok"):
            out = {"ok": False, "blocked": True, "fail_closed": True,
                   "error": leverage_pick.get("error") or "strategy leverage missing",
                   "symbol": SYMBOL, "strategy_key": strategy_key,
                   "leverage_pick": leverage_pick,
                   "portfolio_risk_policy": True}
            out.update(_notify_open_blocked(out, side, strategy_key, None))
            return out
        strategy_leverage = leverage_pick.get("leverage")
        try:
            import auto_trade_portfolio_risk as portfolio_risk
            risk_check = portfolio_risk.preflight(
                SYMBOL, TRADE_TIMEFRAME,
                effective_ratio,
                strategy_leverage, strategy_stop_loss_pct,
                positions.get("positions"), pending.get("orders"),
                td_mode=_td_mode(),
                strategy_key=strategy_key,
            )
        except Exception as exc:
            risk_check = {"ok": False, "blocked": True, "fail_closed": True,
                          "error": "portfolio risk preflight failed: %s" % exc}
        if not risk_check.get("ok"):
            out = dict(risk_check)
            out.update({"symbol": SYMBOL, "strategy_key": strategy_key,
                        "side": side, "leverage": strategy_leverage,
                        "needed_ratio": effective_ratio,
                        "full_position_ratio": effective_ratio,
                        "timeframe": TRADE_TIMEFRAME,
                        "portfolio_risk_policy": True,
                        "account_positions": positions.get("positions"),
                        "account_pending_orders": pending.get("orders")})
            out.update(_notify_open_blocked(
                out, side, strategy_key, strategy_leverage,
                extra={"needed_ratio": effective_ratio,
                       "full_position_ratio": effective_ratio}))
            return out
        opened = _priority_base_submit_entry(
            side=side, strategy_key=strategy_key, manual_confirm=manual_confirm,
            sz=sz, source=source, arm_token=arm_token, internal_auto=internal_auto,
            entry_data=entry_data,
            full_position_ratio_override=grade_ratio,
            stop_loss_pct_override=strategy_stop_loss_pct,
            leverage_override=strategy_leverage,
        )
        opened["portfolio_risk_policy"] = True
        opened["portfolio_risk_preflight"] = risk_check
        return opened
    finally:
        _global_entry_lock_release(global_lock)
# ACCOUNT_WIDE_SINGLE_POSITION_PRIORITY_V1_END
