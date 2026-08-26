# -*- coding: utf-8 -*-
"""Forced protection net: close irrational live positions immediately.

Manual single position occupying more than 75% of account equity, or any
position with leverage above 30x, is closed unconditionally, counted as a
danger signal, and a Wx alert is sent. Occupancy above 50% and at most 75%
only sends a reminder: no close, not a danger signal. Manual opens are
capped at 2 per Beijing day. Day lock parks available USDT into zero-fee
USDC spot instead of a funding-account transfer.
"""
from __future__ import print_function

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
AUTO_DIR = ROOT / "auto_trade"
STATE_PATH = AUTO_DIR / "force_protect_state.json"
EVENT_PATH = AUTO_DIR / "force_protect_events.jsonl"

HALF_RATIO = 0.50
WARN_RATIO = 0.50
FORCE_CLOSE_RATIO = 0.75
MAX_LEVERAGE = 30.0
POLL_SEC = 3.0
NOTIFY_COOLDOWN_SEC = 600.0
INBOUND_NOTIFY_COOLDOWN_SEC = 120.0
DANGER_LIMIT = 3
MANUAL_OPEN_LIMIT = 2
HITCH_EXTRA_LIMIT = 3
MANUAL_NOTIFY_RETRY_BASE_SEC = 10.0
MANUAL_NOTIFY_RETRY_MAX_SEC = 300.0
MIN_TRANSFER_USDT = 0.01
RESTORE_RETRY_SEC = 60.0
INBOUND_DUST_USDT = 1.0
CASH_WINDOW_TTL_SEC = 90.0
CASH_WINDOW_FILE = "auto_open_cash_window.json"
AUTO_CLOSE_REPARK_WINDOW_FILE = "auto_close_repark_window.json"
AUTO_CLOSE_REPARK_TTL_SEC = 120.0
ACCT_FUNDING = "6"
ACCT_TRADING = "18"
LOCK_SPOT_INST = "USDC-USDT"
LOCK_SPOT_CCY = "USDT"
# cash = non-margin spot. cross without ccy fails in Futures mode
# ("Parameter ccy can not be empty") and must not abort the cash fallback.
LOCK_SPOT_TD_MODES = ("cash", "cross")

INTERVENE_LINE = "监测到危险操作 系统已强制介入"
SIZE_WARN_LINE = "监测到手动开仓占用偏高"
MANUAL_DETECT_LINE = "监测到手动开仓"
HITCH_STATUS_LINE = "顺风车开仓提醒"
HITCH_FOLLOW_CLOSE_LINE = "顺风车跟随平仓"
UNLOCK_LINE = "日锁定已自动解除"
SESSION_CLOSE_LINE = "收盘时间到 无条件平仓"
SESSION_OPEN_LINE = "交易日开始 策略信号已恢复"
AUTO_CLOSE_REPARK_LINE = "由于账户处在锁定状态 自动平仓后资金已转为usdc"


def _plain_card(lines):
    return "\n".join(str(x) for x in (lines or []) if x)


def _sweet_note(title, banner, quote, facts=None):
    """Manor-housekeeper body. Old title lines are not emitted; WxPusher summary stays elsewhere."""
    try:
        from auto_trade_formal_notify import manor_card
        return manor_card(banner, quote, facts)
    except Exception:
        parts = [str(banner or "").strip(), "“%s”" % str(quote or "").strip()]
        extra = [x for x in (facts or []) if x]
        if extra:
            parts.append("")
            parts.extend(extra)
        return "\n".join(parts)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_float(v, default=None):
    try:
        if v in (None, ""):
            return default
        out = float(v)
        if out != out:
            return default
        return out
    except Exception:
        return default


def _read_json(path, default=None):
    if default is None:
        default = {}
    try:
        p = Path(path)
        if not p.is_file():
            return default
        return json.loads(p.read_text(encoding="utf-8") or "{}")
    except Exception:
        return default


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _append_event(row):
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    with open(str(EVENT_PATH), "a") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def beijing_date(now_ts=None):
    ts = time.time() if now_ts is None else float(now_ts)
    return datetime.utcfromtimestamp(ts + 8 * 3600).strftime("%Y-%m-%d")


def _amt_str(v):
    s = "%.8f" % float(v)
    s = s.rstrip("0").rstrip(".")
    if not s:
        s = "0"
    return s


def _spot_sz(v, digits=2):
    val = _safe_float(v, 0.0) or 0.0
    if val <= 0:
        return "0"
    step = 10 ** int(digits)
    floored = float(int(val * step)) / float(step)
    fmt = "%." + str(int(digits)) + "f"
    return fmt % floored


def trading_ccy(ccy="USDT"):
    import auto_trade_okx as okx
    ccy = str(ccy or "USDT").upper()
    raw = okx.get_okx_account_balance(ccy)
    if not isinstance(raw, dict) or str(raw.get("code")) != "0":
        return {"ok": False, "error": "okx_balance_failed", "ccy": ccy, "raw": raw}
    rows = raw.get("data") or []
    if not rows or not isinstance(rows[0], dict):
        return {"ok": False, "error": "okx_balance_empty", "ccy": ccy, "raw": raw}
    account = rows[0]
    eq = _safe_float(account.get("totalEq"))
    eq_field = "totalEq"
    available = None
    avail_field = None
    for detail in account.get("details") or []:
        if str(detail.get("ccy") or "").upper() != ccy:
            continue
        if eq in (None, 0):
            eq = _safe_float(detail.get("eq") or detail.get("cashBal"))
            eq_field = "details.eq"
        for k in ("availEq", "availBal", "eq"):
            val = _safe_float(detail.get(k))
            if val is not None:
                available = val
                avail_field = k
                break
        break
    if eq in (None, 0) and available in (None, 0):
        if ccy != "USDT":
            return {
                "ok": True,
                "ccy": ccy,
                "available": 0.0,
                "field": eq_field,
                "avail_field": avail_field,
                "raw": raw,
            }
        return {"ok": False, "error": "usdt_equity_missing", "ccy": ccy, "raw": raw}
    avail = available if available is not None else eq
    out = {
        "ok": True,
        "ccy": ccy,
        "available": avail if avail is not None else 0.0,
        "field": eq_field,
        "avail_field": avail_field,
        "raw": raw,
    }
    if ccy == "USDT":
        out["equity_usdt"] = eq if eq is not None else avail
        out["available_usdt"] = avail if avail is not None else eq
    return out


def trading_usdt():
    return trading_ccy("USDT")


def _live_usdc_available():
    """Available USDC in the trading account, or None if the snapshot failed."""
    try:
        snap = trading_ccy("USDC")
    except Exception:
        return None
    if not isinstance(snap, dict) or not snap.get("ok"):
        return None
    return _safe_float(snap.get("available"), 0.0) or 0.0


def account_equity_usdt():
    return trading_usdt()


def _spot_order_result(raw, td_mode, side, sz, tgt_ccy):
    ok = isinstance(raw, dict) and str(raw.get("code")) == "0"
    row = {}
    if isinstance(raw, dict):
        data = raw.get("data") or []
        if data and isinstance(data[0], dict):
            row = data[0]
            if str(row.get("sCode") or "0") not in ("0", ""):
                ok = False
    fill = _safe_float(row.get("accFillSz") or row.get("fillSz"))
    return {
        "ok": ok,
        "amt": _safe_float(sz, 0.0) or 0.0,
        "side": side,
        "sz": sz,
        "tgt_ccy": tgt_ccy,
        "td_mode": td_mode,
        "inst_id": LOCK_SPOT_INST,
        "ord_id": row.get("ordId") if isinstance(row, dict) else None,
        "fill_sz": fill,
        "usdc": fill if side == "buy" else fill,
        "error": None if ok else (
            (row.get("sMsg") if isinstance(row, dict) else None)
            or ((raw or {}).get("msg") if isinstance(raw, dict) else None)
            or "spot_order_failed"
        ),
        "raw": raw,
    }


def _spot_order_retryable(error):
    msg = str(error or "").lower()
    return any(tok in msg for tok in (
        "tdmode", "td_mode", "trade mode",
        "parameter ccy", "ccy can not be empty", "ccy cannot be empty",
    ))


def place_usdc_spot(side, sz, tgt_ccy):
    import auto_trade_okx as okx
    sz = str(sz or "").strip()
    if not sz or sz in ("0", "0.0", "0.00"):
        return {"ok": True, "skipped": True, "reason": "below_min", "amt": 0, "side": side}
    last = None
    for td_mode in LOCK_SPOT_TD_MODES:
        body = {
            "instId": LOCK_SPOT_INST,
            "tdMode": td_mode,
            "ccy": LOCK_SPOT_CCY,
            "side": side,
            "ordType": "market",
            "sz": sz,
            "tgtCcy": tgt_ccy,
            "clOrdId": ("pus" + uuid.uuid4().hex)[:32],
        }
        try:
            raw = okx._okx_request(
                "POST", "/api/v5/trade/order", body=body, auth=True, timeout=15,
            )
        except Exception as exc:
            last = {
                "ok": False,
                "amt": _safe_float(sz, 0.0) or 0.0,
                "side": side,
                "td_mode": td_mode,
                "error": str(exc),
            }
            if _spot_order_retryable(exc):
                continue
            return last
        result = _spot_order_result(raw, td_mode, side, sz, tgt_ccy)
        if result.get("ok"):
            return result
        last = result
        if _spot_order_retryable(result.get("error")):
            continue
        return result
    return last or {"ok": False, "error": "spot_order_failed", "side": side, "sz": sz}


def park_usdt_as_usdc(amt):
    amt = _safe_float(amt, 0.0) or 0.0
    if amt < MIN_TRANSFER_USDT:
        return {"ok": True, "skipped": True, "reason": "below_min", "amt": amt}
    result = place_usdc_spot("buy", _spot_sz(amt), "quote_ccy")
    if result.get("ok") and not result.get("skipped") and not result.get("usdc"):
        snap = trading_ccy("USDC")
        result["usdc"] = _safe_float(snap.get("available"), 0.0) or 0.0
    return result


def unpark_usdc_to_usdt(amt):
    amt = _safe_float(amt, 0.0) or 0.0
    live = _live_usdc_available()
    if live is not None and live >= MIN_TRANSFER_USDT:
        amt = min(amt, live)
    if amt < MIN_TRANSFER_USDT:
        return {"ok": True, "skipped": True, "reason": "below_min", "amt": amt, "live_usdc": live}
    result = place_usdc_spot("sell", _spot_sz(amt), "base_ccy")
    if live is not None:
        result["live_usdc"] = live
    return result


def lock_convert(amt, from_acct, to_acct):
    """Park USDT as USDC (lock) or sell USDC back to USDT (unlock)."""
    if str(from_acct) == ACCT_TRADING:
        return park_usdt_as_usdc(amt)
    return unpark_usdc_to_usdt(amt)


def _cash_window_path():
    return AUTO_DIR / CASH_WINDOW_FILE


def _auto_close_repark_path():
    return AUTO_DIR / AUTO_CLOSE_REPARK_WINDOW_FILE


def day_lock_active(now_ts=None):
    state = _load_state(now_ts)
    return bool(state.get("lock_to_funding"))


def cash_window_active(now_ts=None):
    now_ts = time.time() if now_ts is None else float(now_ts)
    raw = _read_json(_cash_window_path(), {})
    until = _safe_float((raw or {}).get("until_ts"), 0.0) or 0.0
    return until > now_ts


def begin_auto_open_cash_window(ttl_sec=None, now_ts=None):
    now_ts = time.time() if now_ts is None else float(now_ts)
    ttl = CASH_WINDOW_TTL_SEC if ttl_sec is None else float(ttl_sec)
    row = {
        "until_ts": now_ts + max(1.0, ttl),
        "started_at": _now(),
        "started_ts": now_ts,
        "ttl_sec": ttl,
    }
    _write_json(_cash_window_path(), row)
    return row


def end_auto_open_cash_window():
    path = _cash_window_path()
    try:
        if path.is_file():
            path.unlink()
    except Exception:
        pass
    return {"ok": True}


def auto_close_repark_window_active(now_ts=None):
    now_ts = time.time() if now_ts is None else float(now_ts)
    raw = _read_json(_auto_close_repark_path(), {})
    until = _safe_float((raw or {}).get("until_ts"), 0.0) or 0.0
    return until > now_ts


def auto_close_repark_window_snap(now_ts=None):
    if not auto_close_repark_window_active(now_ts=now_ts):
        return {}
    raw = _read_json(_auto_close_repark_path(), {})
    return raw if isinstance(raw, dict) else {}


def begin_auto_close_repark_window(ttl_sec=None, now_ts=None, snap=None):
    """Mark the next inbound USDT as auto-close proceeds while day-locked."""
    now_ts = time.time() if now_ts is None else float(now_ts)
    ttl = AUTO_CLOSE_REPARK_TTL_SEC if ttl_sec is None else float(ttl_sec)
    row = {
        "until_ts": now_ts + max(1.0, ttl),
        "started_at": _now(),
        "started_ts": now_ts,
        "ttl_sec": ttl,
    }
    extra = snap if isinstance(snap, dict) else {}
    for key in ("inst_id", "symbol", "side", "strategy_key", "strategy_title", "reason"):
        if extra.get(key):
            row[key] = extra.get(key)
    _write_json(_auto_close_repark_path(), row)
    return row


def end_auto_close_repark_window():
    path = _auto_close_repark_path()
    try:
        if path.is_file():
            path.unlink()
    except Exception:
        pass
    return {"ok": True}


def _wait_usdt_available(min_amt=None, timeout=5.0):
    need = MIN_TRANSFER_USDT if min_amt is None else float(min_amt)
    deadline = time.time() + max(0.5, float(timeout))
    last = 0.0
    while time.time() < deadline:
        snap = trading_usdt()
        last = _safe_float((snap or {}).get("available_usdt"), 0.0) or 0.0
        if last >= need:
            return last
        time.sleep(0.25)
    return last


def release_usdc_for_auto_open(now_ts=None):
    """Sell parked USDC to USDT so an auto entry can size and fill."""
    now_ts = time.time() if now_ts is None else float(now_ts)
    if not day_lock_active(now_ts):
        return {"ok": True, "skipped": True, "reason": "not_locked", "window": None}
    window = begin_auto_open_cash_window(now_ts=now_ts)
    snap = trading_ccy("USDC")
    avail = _safe_float((snap or {}).get("available"), 0.0) or 0.0
    sold = unpark_usdc_to_usdt(avail)
    waited = 0.0
    if sold.get("ok") and not sold.get("skipped"):
        waited = _wait_usdt_available()
    row = {
        "ok": bool(sold.get("ok")),
        "window": window,
        "usdc": avail,
        "unpark": sold,
        "available_usdt": waited,
    }
    _append_event({"time": _now(), "kind": "auto_open_cash_release",
                   "usdc": avail, "ok": row["ok"]})
    return row


def repark_usdt_after_auto_open():
    """Park leftover available USDT back to USDC after an auto open attempt."""
    try:
        snap = trading_usdt()
        avail = _safe_float((snap or {}).get("available_usdt"), 0.0) or 0.0
        parked = park_usdt_as_usdc(avail)
    except Exception as exc:
        parked = {"ok": False, "error": str(exc)}
        avail = 0.0
    end_auto_open_cash_window()
    _append_event({"time": _now(), "kind": "auto_open_cash_repark",
                   "available_usdt": avail, "ok": bool(parked.get("ok"))})
    return {"ok": bool(parked.get("ok")), "park": parked, "available_usdt": avail}


def funding_to_trading_bills(lock_at_ts, raw_bills=None):
    """Trading-account bills that are transfers in from the funding account."""
    if raw_bills is None:
        import auto_trade_okx as okx
        raw_bills = okx._okx_request(
            "GET", "/api/v5/account/bills",
            params={"ccy": "USDT", "limit": "50"},
            auth=True, timeout=15,
        )
    if not isinstance(raw_bills, dict) or str(raw_bills.get("code")) != "0":
        return []
    lock_ms = int(float(lock_at_ts or 0) * 1000)
    hits = []
    for row in raw_bills.get("data") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("from") or "") != ACCT_FUNDING:
            continue
        if str(row.get("to") or "") != ACCT_TRADING:
            continue
        ts = 0
        try:
            ts = int(row.get("ts") or 0)
        except Exception:
            ts = 0
        if lock_ms and ts and ts < lock_ms:
            continue
        hits.append(row)
    return hits


def position_value_usdt(pos):
    """Occupied capital of one position (仓位总价值)."""
    margin = _safe_float(pos.get("margin_usdt"))
    if margin not in (None, 0):
        return margin
    notional = _safe_float(pos.get("notional_usd"))
    lev = _safe_float(pos.get("leverage"))
    if notional not in (None, 0) and lev not in (None, 0):
        return notional / lev
    return None


def occupancy_ratio(pos, equity_usdt):
    value = position_value_usdt(pos)
    equity = _safe_float(equity_usdt)
    if value is None or equity in (None, 0):
        return None
    return value / equity


def is_manual_position(pos):
    if pos.get("manual") is True:
        return True
    source = str(pos.get("source") or "").lower()
    if "formal_auto" in source:
        return False
    # Do not substring-match "manual": a makeup source like
    # manual_makeup_occupancy_skip would be flattened by day-lock.
    if source == "manual" or source.startswith("manual_okx") or source.startswith("manual_web"):
        return True
    if not pos.get("strategy_key"):
        return True
    return False


def manual_bare_id(pos):
    """Stable exchange/local id for one instrument slot (may be reused after close)."""
    pid = str((pos or {}).get("pos_id") or "").strip()
    if pid:
        return pid
    return str(
        (pos or {}).get("position_id")
        or (pos or {}).get("inst_id")
        or ""
    ).strip()


def manual_open_stamp(pos):
    """Open-time marker. OKX reuses posId on reopen, but cTime/opened_at changes."""
    return str(
        (pos or {}).get("c_time")
        or (pos or {}).get("opened_at")
        or (pos or {}).get("ctime")
        or ""
    ).strip()


def manual_fingerprint(pos, state=None):
    """Unique id for one manual open event (not just one symbol/side).

    Prefer posId|open_stamp. When stamp is missing and *state* is provided,
    use the per-day reopen generation so close+reopen still counts.
    """
    bare = manual_bare_id(pos)
    if not bare:
        return ""
    stamp = manual_open_stamp(pos)
    if stamp:
        return "%s|%s" % (bare, stamp)
    if state is not None:
        gens = state.get("manual_bare_generations") or {}
        gen = int(gens.get(bare) or 0)
        if gen > 0:
            return "%s#%s" % (bare, gen)
    return bare


def size_warn_fingerprint(pos, state=None):
    """Stable id for one manual open. Survives occupancy oscillating in 50–75%."""
    return str(manual_fingerprint(pos, state=state) or "").strip()


def prune_size_warn_notified(state, positions):
    live = set()
    for pos in positions or []:
        if not is_manual_position(pos):
            continue
        fp = size_warn_fingerprint(pos)
        if fp:
            live.add(fp)
    kept = [x for x in (state.get("size_warn_notified_ids") or []) if x in live]
    state["size_warn_notified_ids"] = kept
    return set(kept)


def seed_size_warn_from_legacy_cooldown(state, positions):
    """One-shot: a 10-min cooldown key already means this open was warned."""
    last = state.get("last_notify_ts") or {}
    if not last:
        return False
    warned = list(state.get("size_warn_notified_ids") or [])
    known = set(warned)
    changed = False
    for pos in positions or []:
        if not is_manual_position(pos):
            continue
        fp = size_warn_fingerprint(pos)
        if not fp or fp in known:
            continue
        pid = str(pos.get("position_id") or "")
        inst = str(pos.get("inst_id") or "")
        side = str(pos.get("side") or "")
        mgn = str(pos.get("mgn_mode") or "")
        keys = []
        if pid:
            keys.append("%s|manual_size_warn" % pid)
        if inst and side:
            keys.append("%s|%s|manual_size_warn" % (inst, side))
            if mgn:
                keys.append("%s|%s|%s|manual_size_warn" % (inst, side, mgn))
        if any(k in last for k in keys):
            warned.append(fp)
            known.add(fp)
            changed = True
    if changed:
        state["size_warn_notified_ids"] = warned
    return changed


def remaining_manual_opens(count, limit=MANUAL_OPEN_LIMIT):
    used = int(count or 0)
    cap = int(limit)
    if used < 0:
        used = 0
    left = cap - used
    if left < 0:
        left = 0
    return left


def _inst_side(pos):
    inst = str((pos or {}).get("inst_id") or "").upper()
    side = str((pos or {}).get("side") or "").strip().lower()
    if side in ("buy", "做多"):
        side = "long"
    elif side in ("sell", "做空"):
        side = "short"
    return inst, side


def _is_live_holding(pos):
    """True only when the OKX row still has size (持仓中)."""
    sz = _safe_float((pos or {}).get("contracts"), None)
    if sz is None:
        sz = _safe_float((pos or {}).get("pos"), None)
    if sz is None:
        return True
    return abs(sz) > 0


def auto_strategy_hitch_class(strategy_key):
    """方向型 / 赔率型 / None. Same classifier as open/close Wx advice."""
    try:
        import auto_trade_formal_notify as notify
        return notify.strategy_hitch_class(strategy_key)
    except Exception:
        return None


def auto_position_allows_hitch(pos):
    """Only a live 方向型 auto holding can sponsor a 顺风车."""
    if is_manual_position(pos) or not _is_live_holding(pos):
        return False
    try:
        import auto_trade_formal_notify as notify
        klass = notify.HITCH_CLASS_DIRECTION
    except Exception:
        klass = "方向型"
    return auto_strategy_hitch_class(pos.get("strategy_key")) == klass


def auto_side_keys(positions):
    """(inst, side) pairs of live auto holdings that may sponsor a 顺风车.

    赔率型 / 未分类自动仓即使同标的同方向也不进入此集合。
    """
    keys = set()
    for pos in positions or []:
        if not auto_position_allows_hitch(pos):
            continue
        inst, side = _inst_side(pos)
        if inst and side in ("long", "short"):
            keys.add((inst, side))
    return keys


def live_auto_side_keys(positions):
    """Any live auto holding, regardless of 方向型 / 赔率型.

    Follow-close must not fire just because a sponsor was reclassified.
    """
    keys = set()
    for pos in positions or []:
        if is_manual_position(pos) or not _is_live_holding(pos):
            continue
        inst, side = _inst_side(pos)
        if inst and side in ("long", "short"):
            keys.add((inst, side))
    return keys


def is_hitchhiker(pos, positions=None, auto_keys=None):
    """顺风车: 手动仓与当前仍持仓的方向型自动仓同标的、同方向。

    自动仓已平、无持仓、或该自动策略为赔率型/未分类，一律不算顺风车，
    按普通手动开仓计额度。
    """
    if not is_manual_position(pos):
        return False
    if auto_keys is None:
        auto_keys = auto_side_keys(positions)
    inst, side = _inst_side(pos)
    return (inst, side) in auto_keys


def _hitch_binding_key(pos):
    inst, side = _inst_side(pos)
    if not inst or side not in ("long", "short"):
        return ""
    return "%s|%s" % (inst, side)


def _sponsor_keys_for(positions, inst, side):
    keys = []
    for pos in positions or []:
        if not auto_position_allows_hitch(pos):
            continue
        inst2, side2 = _inst_side(pos)
        if inst2 == inst and side2 == side:
            sk = str(pos.get("strategy_key") or "").strip()
            if sk and sk not in keys:
                keys.append(sk)
    return keys


def sync_hitch_bindings(state, positions):
    """Remember live 顺风车 so they can follow the auto close.

    Hitch is only *recognized* while a 方向型 auto is open. Bindings
    persist the pairing. Follow-close waits until that auto holding is
    actually gone — reclassifying it to 赔率型 must not orphan the hitch.
    """
    bindings = dict(state.get("hitch_bindings") or {})
    auto_keys = auto_side_keys(positions)
    live_auto_pairs = live_auto_side_keys(positions)
    live_manual = {}
    for pos in positions or []:
        if not is_manual_position(pos) or not _is_live_holding(pos):
            continue
        fp = manual_fingerprint(pos, state=state)
        if fp:
            live_manual[fp] = pos
        if is_hitchhiker(pos, auto_keys=auto_keys):
            inst, side = _inst_side(pos)
            bindings[fp] = {
                "inst_id": inst,
                "side": side,
                "mgn_mode": pos.get("mgn_mode"),
                "sponsor_keys": _sponsor_keys_for(positions, inst, side),
                "pair": _hitch_binding_key(pos),
            }
    for fp in list(bindings.keys()):
        if fp not in live_manual:
            bindings.pop(fp, None)
    orphans = []
    for fp, bind in list(bindings.items()):
        pair = (bind.get("inst_id"), bind.get("side"))
        if pair in live_auto_pairs:
            continue
        pos = live_manual.get(fp)
        if pos is None:
            continue
        orphans.append({"fingerprint": fp, "binding": bind, "position": pos})
    state["hitch_bindings"] = bindings
    return orphans


def build_hitch_follow_close_message(pos, closed):
    symbol = _symbol_zh(pos)
    direction = _dir_zh(pos)
    facts = [
        "跟随标的 %s %s" % (symbol, direction),
        "跟随的方向型自动仓已离场（止盈 / 止损 / 失效均会带走顺风车）",
        "顺风车手动仓已一并平仓" if closed else "顺风车手动仓平仓未确认，将继续尝试",
    ]
    quote = (
        "叮铃铃，顺风车到站啦。你贴着的那趟方向型自动仓已经下车，小管家把同向手动仓也一起收好了哦~"
        if closed else
        "叮铃铃，顺风车到站啦。跟随的自动仓已经离场，小管家正在帮你收同向手动仓。"
    )
    return _sweet_note(
        HITCH_FOLLOW_CLOSE_LINE,
        "🚲 顺风车到站：",
        quote,
        facts,
    )


def _hitch_manual_still_open(pos, bind):
    """Re-read OKX after a claimed close. True = hitch is still holding."""
    inst = str((pos or {}).get("inst_id") or (bind or {}).get("inst_id") or "").upper()
    side = str((pos or {}).get("side") or (bind or {}).get("side") or "").lower()
    if side in ("buy", "做多"):
        side = "long"
    elif side in ("sell", "做空"):
        side = "short"
    mgn = str((pos or {}).get("mgn_mode") or (bind or {}).get("mgn_mode") or "").lower()
    try:
        import auto_trade_live_positions as live_pos
        listed = live_pos._list_live_positions_uncached()
    except Exception:
        return True
    for row in (listed or {}).get("positions") or []:
        if not is_manual_position(row) or not _is_live_holding(row):
            continue
        inst2, side2 = _inst_side(row)
        if inst2 != inst or side2 != side:
            continue
        mgn2 = str(row.get("mgn_mode") or "").lower()
        if mgn in ("isolated", "cross") and mgn2 and mgn2 != mgn:
            continue
        return True
    return False


def close_orphaned_hitchhikers(state, orphans, close_fn, notify_fn, now_ts=None, still_open_fn=None):
    now_ts = time.time() if now_ts is None else now_ts
    rows = []
    bindings = dict(state.get("hitch_bindings") or {})
    for item in orphans or []:
        pos = item.get("position") or {}
        fp = item.get("fingerprint")
        bind = item.get("binding") or {}
        closed_res = close_fn(
            pos.get("inst_id") or bind.get("inst_id"),
            pos.get("side") or bind.get("side"),
            reason="force_protect:hitch_follow_close",
            mgn_mode=pos.get("mgn_mode") or bind.get("mgn_mode"),
        ) or {}
        did_close = bool(closed_res.get("ok") and closed_res.get("closed"))
        already = bool(
            closed_res.get("ok")
            and closed_res.get("reason") == "position_already_absent"
        )
        still_seen = _is_live_holding(pos)
        gone = False
        if did_close:
            gone = True
            if still_open_fn is not None:
                try:
                    gone = not bool(still_open_fn(pos, bind))
                except Exception:
                    gone = False
        elif already and not still_seen:
            gone = True
        message = build_hitch_follow_close_message(pos, gone)
        notify_result = None
        notified = False
        suffix = "done" if gone else "pending"
        action_key = "hitch_follow_close|%s|%s" % (fp, suffix)
        if gone or _should_notify(state, action_key, now_ts, cooldown=60):
            notify_result = notify_fn(message, {
                "kind": "force_protect_hitch_follow_close",
                "inst_id": pos.get("inst_id"),
                "side": pos.get("side"),
                "closed": gone,
                "sponsor_keys": bind.get("sponsor_keys") or [],
            })
            notified = True
            state.setdefault("last_notify_ts", {})[action_key] = now_ts
        if gone:
            bindings.pop(fp, None)
        row = {
            "time": _now(),
            "kind": "hitch_follow_close",
            "fingerprint": fp,
            "inst_id": pos.get("inst_id"),
            "side": pos.get("side"),
            "closed": gone,
            "close": closed_res,
            "notified": notified,
            "notify": notify_result,
            "message": message,
            "danger": False,
        }
        rows.append(row)
        _append_event(row)
    state["hitch_bindings"] = bindings
    return rows


def _symbol_zh(pos):
    return pos.get("symbol_label") or pos.get("inst_id") or "-"


def _dir_zh(pos):
    return pos.get("direction_zh") or pos.get("side") or "-"


def format_position_estimate(pos, equity_usdt):
    value = position_value_usdt(pos)
    ratio = occupancy_ratio(pos, equity_usdt)
    lev = _safe_float(pos.get("leverage"))
    parts = []
    if value is not None:
        parts.append("%.2f USDT" % float(value))
    else:
        parts.append("暂无法估算")
    extra = []
    if ratio is not None:
        extra.append("占用 %.1f%%" % (ratio * 100.0))
    if lev is not None:
        extra.append("杠杆 %sx" % _fmt_lev(lev))
    if extra:
        parts.append("（%s）" % "，".join(extra))
    return "".join(parts)


def build_manual_detect_message(
    pos, equity_usdt, count, remaining,
    hitch=False, extra_count=0, extra_remaining=None, used_extra=False,
    status_only=False,
):
    symbol = _symbol_zh(pos)
    direction = _dir_zh(pos)
    estimate = format_position_estimate(pos, equity_usdt)
    facts = [
        "开仓标的 %s %s" % (symbol, direction),
        "仓位估算 %s" % estimate,
        "当天剩余开仓次数 %s（今日已开 %s/%s）" % (
            int(remaining), int(count), MANUAL_OPEN_LIMIT,
        ),
    ]
    extra_left = extra_remaining
    if extra_left is None:
        extra_left = remaining_manual_opens(extra_count, HITCH_EXTRA_LIMIT)
    if hitch:
        facts.append("识别为顺风车（方向型自动仓，同标的同方向）")
        if used_extra:
            facts.append("常规开仓次数已用完，计入顺风车额外次数")
        facts.append("顺风车额外剩余开仓次数 %s（额外已开 %s/%s）" % (
            int(extra_left), int(extra_count or 0), HITCH_EXTRA_LIMIT,
        ))
        banner = "🚲 顺风车小铃铛："
        quote = (
            "叮铃铃！检测到主人贴上了同方向的顺风车 %s %s（%s）。小管家记下啦，温柔一点哦~"
            % (symbol, direction, estimate)
        )
        title = HITCH_STATUS_LINE if status_only else MANUAL_DETECT_LINE
        return _sweet_note(title, banner, quote, facts)
    if int(remaining) <= 0:
        banner = "🍡小管家轻轻提醒："
        quote = (
            "主人手动%s %s（%s）。今日 %s 次手动额度已经用完（已开 %s/%s），小管家记下这一笔，今天常规手动先到这儿，祝主人开仓顺利！"
            % (direction, symbol, estimate, MANUAL_OPEN_LIMIT, int(count), MANUAL_OPEN_LIMIT)
        )
    else:
        banner = "🍡小管家轻轻提醒："
        quote = (
            "主人手动%s %s（%s）。今天还剩 %s 次手动敲门机会（已开 %s/%s），小管家先记在小本本上，祝主人开仓顺利！"
            % (direction, symbol, estimate, int(remaining), int(count), MANUAL_OPEN_LIMIT)
        )
    title = HITCH_STATUS_LINE if status_only else MANUAL_DETECT_LINE
    return _sweet_note(title, banner, quote, None)


def occupancy_band(ratio, warn_ratio=WARN_RATIO, force_ratio=FORCE_CLOSE_RATIO):
    """ok / warn / force for a manual occupancy ratio. None if ratio missing."""
    if ratio is None:
        return None
    try:
        value = float(ratio)
    except Exception:
        return None
    if value > float(force_ratio):
        return "force"
    if value > float(warn_ratio):
        return "warn"
    return "ok"


def violation_reasons(pos, equity_usdt, half_ratio=WARN_RATIO, max_leverage=MAX_LEVERAGE,
                      force_ratio=FORCE_CLOSE_RATIO):
    """Return force-close reason codes. Empty list means do not close."""
    reasons = []
    lev = _safe_float(pos.get("leverage"))
    if lev is not None and lev > max_leverage:
        reasons.append("leverage_gt_30x")
    if is_manual_position(pos):
        ratio = occupancy_ratio(pos, equity_usdt)
        band = occupancy_band(ratio, warn_ratio=half_ratio, force_ratio=force_ratio)
        if band == "force":
            reasons.append("manual_over_force_close")
    return reasons


def advisory_reasons(pos, equity_usdt, warn_ratio=WARN_RATIO,
                     force_ratio=FORCE_CLOSE_RATIO):
    """Remind-only codes. Never close and never count as a danger signal."""
    if not is_manual_position(pos):
        return []
    ratio = occupancy_ratio(pos, equity_usdt)
    if occupancy_band(ratio, warn_ratio=warn_ratio, force_ratio=force_ratio) == "warn":
        return ["manual_size_warn"]
    return []


def _reason_zh(code):
    return {
        "leverage_gt_30x": "杠杆大于30x",
        "manual_over_force_close": "手动单笔占用超过75%",
        "manual_over_half": "手动单笔占用超过75%",
        "manual_size_warn": "手动单笔占用超过半仓",
        "manual_open_over_limit": "今日手动开仓超过%s次" % MANUAL_OPEN_LIMIT,
        "manual_open_outside_session": "交易时段外手动开仓",
        "session_close": "收盘时间到 无条件平仓",
    }.get(code, str(code))


def build_intervene_message(pos, equity_usdt, reasons, closed, remaining=None, count=None):
    ratio = occupancy_ratio(pos, equity_usdt)
    lev = _safe_float(pos.get("leverage"))
    value = position_value_usdt(pos)
    symbol = _symbol_zh(pos)
    direction = _dir_zh(pos)
    estimate = format_position_estimate(pos, equity_usdt)
    facts = []
    title = pos.get("strategy_title") or ("手动开仓" if is_manual_position(pos) else "持仓")
    facts.append("%s %s %s" % (
        title,
        pos.get("symbol_label") or pos.get("inst_id") or "",
        pos.get("direction_zh") or pos.get("side") or "",
    ))
    if "manual_open_over_limit" in (reasons or []):
        facts.append("开仓标的 %s %s" % (symbol, direction))
        facts.append("仓位估算 %s" % estimate)
        used = MANUAL_OPEN_LIMIT if count is None else int(count)
        left = remaining_manual_opens(used) if remaining is None else int(remaining)
        facts.append("当天剩余开仓次数 %s（今日已开 %s/%s）" % (
            left, used if used <= MANUAL_OPEN_LIMIT else used, MANUAL_OPEN_LIMIT,
        ))
    detail = []
    if ratio is not None:
        detail.append("占用 %.1f%%（强制平仓上限 75%%）" % (ratio * 100.0))
    if value is not None and equity_usdt:
        detail.append("仓位 %.2f / 账户 %.2f USDT" % (value, float(equity_usdt)))
    if lev is not None:
        detail.append("杠杆 %sx" % _fmt_lev(lev))
    if detail and list(reasons or []) != ["manual_open_over_limit"]:
        facts.append("；".join(detail))
    facts.append("原因：%s" % "、".join(_reason_zh(c) for c in reasons))
    facts.append("已无条件强制平仓" if closed else "强制平仓未确认成功，将继续重试")
    if "manual_open_over_limit" in (reasons or []):
        quote = (
            "急急急！检测到主人手滑%s %s 啦（仓位 %s）！今天的手动敲门次数已经用光，小鸡毛立刻伸出小爪子帮你把单子平掉啦！今天不能再乱动手了哦，小管家已经把门挂上钥匙锁好，请主人把小手掌收好！"
            % (direction, symbol, estimate)
        )
    elif "leverage_gt_30x" in (reasons or []):
        quote = (
            "杠杆晃得太厉害啦！%s %s 超过安全摇篮，小鸡毛已经把这只小仓位抱回围栏里。"
            % (symbol, direction)
        )
    else:
        quote = (
            "手边仓位有点晃（%s %s），小鸡毛已经把单子抱走啦！请主人先去抱抱小白歇一会儿。"
            % (symbol, direction)
        )
    return _sweet_note(INTERVENE_LINE, "🚫 小鸡毛的急刹车：", quote, facts)


def build_size_warn_message(pos, equity_usdt):
    ratio = occupancy_ratio(pos, equity_usdt)
    value = position_value_usdt(pos)
    symbol = _symbol_zh(pos)
    direction = _dir_zh(pos)
    facts = ["开仓标的 %s %s" % (symbol, direction)]
    pct_text = ""
    if ratio is not None:
        pct_text = "%.1f%%" % (ratio * 100.0)
        facts.append("占用 %.1f%%（50%%–75%% 仅提醒，超过 75%% 才强制平仓）" % (ratio * 100.0))
    bag_text = ""
    if value is not None and equity_usdt:
        bag_text = "%.2f / 账户 %.2f USDT" % (value, float(equity_usdt))
        facts.append("仓位 %.2f / 账户 %.2f USDT" % (value, float(equity_usdt)))
    facts.append("原因：%s" % _reason_zh("manual_size_warn"))
    facts.append("本次仅提醒，未干预交易，不计入危险信号")
    quote = (
        "敲敲桌面！检测到主人手动%s %s 占用了 %s 的小口袋（%s）。虽然超了半仓，但小管家这次只给你塞一张小纸条提醒，暂时不把小盘子打翻。下次不可以再堆这么满啦，抱抱你！"
        % (direction, symbol, pct_text or "偏高", bag_text or "小口袋有点鼓")
    )
    return _sweet_note(SIZE_WARN_LINE, "⚠️ 盘子装得太满啦：", quote, facts)


def _fmt_lev(lev):
    if abs(lev - int(lev)) < 1e-9:
        return str(int(lev))
    return ("%.2f" % lev).rstrip("0").rstrip(".")


def _notify(message, meta):
    import auto_trade_formal_notify as notify
    kind = str((meta or {}).get("kind") or "")
    if not kind.startswith("force_protect_"):
        kind = "force_protect_intervene"
    return notify.send_message(message, kind=kind, meta=meta)


def _load_state(now_ts=None):
    raw = _read_json(STATE_PATH, {})
    if not isinstance(raw, dict):
        raw = {}
    today = beijing_date(now_ts)
    rollover = None
    prev_date = raw.get("beijing_date")
    if prev_date and prev_date != today:
        rollover = {
            "from_date": prev_date,
            "was_locked": bool(raw.get("lock_to_funding")),
            "evacuated_amt": _safe_float(raw.get("evacuated_amt"), 0.0) or 0.0,
            "evacuated_usdc": _safe_float(raw.get("evacuated_usdc"), 0.0) or 0.0,
            "pending_restore_amt": _safe_float(raw.get("pending_restore_amt"), 0.0) or 0.0,
            "pending_restore_usdc": _safe_float(raw.get("pending_restore_usdc"), 0.0) or 0.0,
        }
    state = normalize_day_state(raw, now_ts=now_ts)
    if rollover and (
        rollover.get("was_locked")
        or (rollover.get("pending_restore_usdc") or 0) > 0
        or (rollover.get("evacuated_usdc") or 0) > 0
        or (rollover.get("pending_restore_amt") or 0) > 0
        or (rollover.get("evacuated_amt") or 0) > 0
    ):
        pending = (
            (rollover.get("pending_restore_usdc") or 0.0)
            or (rollover.get("evacuated_usdc") or 0.0)
            or (rollover.get("pending_restore_amt") or 0.0)
            or (rollover.get("evacuated_amt") or 0.0)
        )
        state["pending_restore_amt"] = pending
        state["pending_restore_usdc"] = pending
        state["unlock_from_date"] = rollover.get("from_date") or ""
        state["unlock_notify_pending"] = True
    return state


def normalize_day_state(state, now_ts=None):
    today = beijing_date(now_ts)
    if not isinstance(state, dict):
        state = {}
    if state.get("beijing_date") != today:
        return {
            "beijing_date": today,
            "danger_count": 0,
            "lock_to_funding": False,
            "lock_evacuated": False,
            "lock_at": "",
            "lock_at_ts": 0,
            "evacuated_amt": 0,
            "evacuated_usdc": 0,
            "pending_restore_amt": 0,
            "pending_restore_usdc": 0,
            "unlock_from_date": "",
            "unlock_notify_pending": False,
            "manual_open_count": 0,
            "manual_open_ids": [],
            "hitch_extra_count": 0,
            "hitch_extra_ids": [],
            "manual_denied_ids": [],
            "manual_active_bare_ids": [],
            "manual_closed_bare_ids": [],
            "manual_bare_generations": {},
            "hitch_notified_ids": [],
            # Delivery is independent from the Beijing-day quota.  Preserve
            # failed notifications across midnight until WxPusher confirms
            # acceptance; the original count/date stay frozen in each row.
            "pending_manual_notifications": dict(
                state.get("pending_manual_notifications") or {}),
            "hitch_bindings": dict(state.get("hitch_bindings") or {}),
            "size_warn_notified_ids": list(state.get("size_warn_notified_ids") or []),
            "session_flatten_week": state.get("session_flatten_week") or "",
            "session_leftover_fps": list(state.get("session_leftover_fps") or []),
            "session_close_notified_week": state.get("session_close_notified_week") or "",
            "session_open_notified_week": state.get("session_open_notified_week") or "",
            "last_notify_ts": {},
            "updated_at": _now(),
        }
    state.setdefault("danger_count", 0)
    state.setdefault("lock_to_funding", False)
    state.setdefault("lock_evacuated", False)
    state.setdefault("evacuated_amt", 0)
    state.setdefault("evacuated_usdc", 0)
    state.setdefault("pending_restore_amt", 0)
    state.setdefault("pending_restore_usdc", 0)
    state.setdefault("unlock_from_date", "")
    state.setdefault("unlock_notify_pending", False)
    state.setdefault("manual_open_count", 0)
    state.setdefault("manual_open_ids", [])
    state.setdefault("hitch_extra_count", 0)
    state.setdefault("hitch_extra_ids", [])
    state.setdefault("manual_denied_ids", [])
    state.setdefault("manual_active_bare_ids", [])
    state.setdefault("manual_closed_bare_ids", [])
    state.setdefault("manual_bare_generations", {})
    state.setdefault("hitch_notified_ids", [])
    state.setdefault("pending_manual_notifications", {})
    state.setdefault("hitch_bindings", {})
    state.setdefault("size_warn_notified_ids", [])
    state.setdefault("session_flatten_week", "")
    state.setdefault("session_leftover_fps", [])
    state.setdefault("session_close_notified_week", "")
    state.setdefault("session_open_notified_week", "")
    state.setdefault("last_notify_ts", {})
    return state


def _persist_state(state):
    state = dict(state or {})
    state["updated_at"] = _now()
    _write_json(STATE_PATH, {
        "beijing_date": state.get("beijing_date"),
        "danger_count": int(state.get("danger_count") or 0),
        "lock_to_funding": bool(state.get("lock_to_funding")),
        "lock_evacuated": bool(state.get("lock_evacuated")),
        "lock_at": state.get("lock_at") or "",
        "lock_at_ts": state.get("lock_at_ts") or 0,
        "evacuated_amt": _safe_float(state.get("evacuated_amt"), 0.0) or 0.0,
        "evacuated_usdc": _safe_float(state.get("evacuated_usdc"), 0.0) or 0.0,
        "pending_restore_amt": _safe_float(state.get("pending_restore_amt"), 0.0) or 0.0,
        "pending_restore_usdc": _safe_float(state.get("pending_restore_usdc"), 0.0) or 0.0,
        "unlock_from_date": state.get("unlock_from_date") or "",
        "unlock_notify_pending": bool(state.get("unlock_notify_pending")),
        "manual_open_count": int(state.get("manual_open_count") or 0),
        "manual_open_ids": list(state.get("manual_open_ids") or []),
        "hitch_extra_count": int(state.get("hitch_extra_count") or 0),
        "hitch_extra_ids": list(state.get("hitch_extra_ids") or []),
        "manual_denied_ids": list(state.get("manual_denied_ids") or []),
        "manual_active_bare_ids": list(state.get("manual_active_bare_ids") or []),
        "manual_closed_bare_ids": list(state.get("manual_closed_bare_ids") or []),
        "manual_bare_generations": dict(state.get("manual_bare_generations") or {}),
        "hitch_notified_ids": list(state.get("hitch_notified_ids") or []),
        "pending_manual_notifications": dict(
            state.get("pending_manual_notifications") or {}),
        "hitch_bindings": dict(state.get("hitch_bindings") or {}),
        "size_warn_notified_ids": list(state.get("size_warn_notified_ids") or []),
        "session_flatten_week": state.get("session_flatten_week") or "",
        "session_leftover_fps": list(state.get("session_leftover_fps") or []),
        "session_close_notified_week": state.get("session_close_notified_week") or "",
        "session_open_notified_week": state.get("session_open_notified_week") or "",
        "last_notify_ts": state.get("last_notify_ts") or {},
        "updated_at": state.get("updated_at"),
    })
    return state


def _should_notify(state, action_key, now_ts, cooldown=None):
    last = _safe_float((state.get("last_notify_ts") or {}).get(action_key), 0.0) or 0.0
    wait = NOTIFY_COOLDOWN_SEC if cooldown is None else float(cooldown)
    return (now_ts - last) >= wait


def build_lock_message(danger_count, transferred, amt, transfer_error=None):
    lines = [
        "今日风险行为已达%s次：本日账上锁" % int(danger_count or DANGER_LIMIT),
        "已全仓买入 USDC 现货" if transferred else "正在全仓买入 USDC 现货",
    ]
    if amt:
        lines.append("拦截金额 %.2f USDT" % float(amt))
    if transfer_error and not transferred:
        lines.append("买入尚未成功：%s" % str(transfer_error)[:180])
    return _plain_card(lines)


def build_inbound_block_message(amt, transferred):
    lines = [
        "本日账户仍在上锁",
        "已再次全仓买入 USDC 现货" if transferred else "正在全仓买入 USDC 现货",
    ]
    if amt:
        lines.append("拦截金额 %.2f USDT" % float(amt))
    return _plain_card(lines)


def build_auto_close_repark_message(amt, transferred, snap=None):
    snap = snap or {}
    lines = [AUTO_CLOSE_REPARK_LINE]
    if amt:
        if transferred:
            lines.append("已转回 USDC 金额 %.2f USDT" % float(amt))
        else:
            lines.append("待转入金额 %.2f USDT" % float(amt))
    title = snap.get("strategy_title") or snap.get("strategy_key")
    inst = snap.get("inst_id") or snap.get("symbol")
    if title or inst:
        lines.append("来源 %s %s" % (title or "自动策略", inst or ""))
    return _plain_card(lines)


def build_unlock_message(from_date, transferred, amt, transfer_error=None):
    lines = [
        "昨日锁定已到期（%s）" % (from_date or "前一日"),
    ]
    if amt and amt >= MIN_TRANSFER_USDT:
        if transferred:
            lines.append("已将锁定的 USDC 现货卖回 USDT")
        else:
            lines.append("正在将锁定的 USDC 现货卖回 USDT")
        lines.append("卖出数量 %.2f USDC" % float(amt))
    else:
        lines.append("昨日未成功买入 USDC，故无需卖回")
    if transfer_error and not transferred:
        lines.append("卖回尚未成功：%s" % str(transfer_error)[:180])
    return _plain_card(lines)


def build_restore_done_message(amt):
    lines = [
        "已将锁定的 USDC 现货卖回 USDT",
    ]
    if amt:
        lines.append("卖出数量 %.2f USDC" % float(amt))
    return _plain_card(lines)


def apply_pending_restore(state, transfer_fn, notify_fn, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    amt = _safe_float(state.get("pending_restore_usdc"), 0.0) or 0.0
    if amt < MIN_TRANSFER_USDT:
        amt = _safe_float(state.get("pending_restore_amt"), 0.0) or 0.0
    need_unlock = bool(state.get("unlock_notify_pending"))
    transferred = False
    transfer_result = None
    transfer_error = None
    skipped_backoff = False
    if amt >= MIN_TRANSFER_USDT:
        last_try = _safe_float((state.get("last_notify_ts") or {}).get("restore_try"), 0.0) or 0.0
        if last_try and (now_ts - last_try) < RESTORE_RETRY_SEC:
            skipped_backoff = True
            transfer_result = {"ok": False, "skipped": True, "error": "restore_backoff"}
        else:
            live = _live_usdc_available()
            sell_amt = amt
            if live is not None:
                if live < MIN_TRANSFER_USDT:
                    transfer_result = {
                        "ok": False, "amt": amt, "live_usdc": live,
                        "error": "可用 USDC 不足（%.4f）" % live,
                    }
                    sell_amt = 0.0
                else:
                    sell_amt = min(amt, live)
            if transfer_result is None:
                try:
                    transfer_result = transfer_fn(sell_amt, ACCT_FUNDING, ACCT_TRADING)
                except Exception as exc:
                    transfer_result = {"ok": False, "amt": sell_amt, "error": str(exc)}
            amt = sell_amt if sell_amt else amt
            state.setdefault("last_notify_ts", {})["restore_try"] = now_ts
        transferred = bool(transfer_result and transfer_result.get("ok") and not transfer_result.get("skipped"))
        if transferred:
            # Ledger may be inflated by repark cycles; live fill is the real pile.
            state["pending_restore_amt"] = 0
            state["pending_restore_usdc"] = 0
        elif isinstance(transfer_result, dict):
            transfer_error = transfer_result.get("error") or transfer_result.get("msg") or "划转未确认成功"
        else:
            transfer_error = "划转未确认成功"
    else:
        state["pending_restore_amt"] = 0
        state["pending_restore_usdc"] = 0
        amt = 0.0

    message = None
    notify_result = None
    notified = False
    if need_unlock:
        message = build_unlock_message(
            state.get("unlock_from_date"), transferred, amt, transfer_error=transfer_error,
        )
        key = "day_unlock|" + str(state.get("beijing_date") or "")
        if _should_notify(state, key, now_ts, cooldown=1):
            notify_result = notify_fn(message, {
                "kind": "day_unlock",
                "from_date": state.get("unlock_from_date"),
                "transferred": transferred,
                "amt": amt,
            })
            notified = True
            state.setdefault("last_notify_ts", {})[key] = now_ts
        state["unlock_notify_pending"] = False
    elif transferred:
        message = build_restore_done_message(amt)
        key = "restore_done|" + str(state.get("beijing_date") or "")
        if _should_notify(state, key, now_ts, cooldown=1):
            notify_result = notify_fn(message, {
                "kind": "restore_done",
                "amt": amt,
                "transferred": True,
            })
            notified = True
            state.setdefault("last_notify_ts", {})[key] = now_ts

    row = {
        "time": _now(),
        "kind": "day_unlock" if need_unlock else ("restore_done" if transferred else "restore_hold"),
        "amt": amt,
        "transferred": transferred,
        "transfer": transfer_result,
        "notified": notified,
        "message": message,
        "notify": notify_result,
    }
    if (need_unlock or transferred or transfer_error) and not skipped_backoff:
        _append_event(row)
    return row


def _close_all_positions(positions, close_fn):
    """Day-lock flatten: close leftover manuals only. Keep 马卡龙/大福 auto holds."""
    closed = []
    for pos in positions or []:
        if not is_manual_position(pos):
            continue
        result = close_fn(
            pos.get("inst_id"),
            pos.get("side"),
            reason="force_protect:day_lock_evacuate",
            mgn_mode=pos.get("mgn_mode"),
        ) or {}
        closed.append({"inst_id": pos.get("inst_id"), "side": pos.get("side"), "close": result})
    return closed


def enforce_funding_lock(
    state, positions, close_fn, notify_fn, transfer_fn, trading_fn,
    locked_this_tick=False, now_ts=None, bills_fn=None,
):
    now_ts = time.time() if now_ts is None else now_ts
    closed = _close_all_positions(positions, close_fn)
    if cash_window_active(now_ts) and not locked_this_tick:
        row = {
            "time": _now(),
            "kind": "cash_window_hold",
            "closed": closed,
            "available_usdt": None,
            "transferred": False,
            "transfer": None,
            "inbound": False,
            "notified": False,
            "message": None,
            "notify": None,
        }
        _append_event(row)
        return row
    snap = trading_fn() if trading_fn else {"ok": True, "available_usdt": 0}
    available = _safe_float((snap or {}).get("available_usdt"), 0.0) or 0.0
    transferred = False
    transfer_result = None
    amt = 0.0
    transfer_error = None
    if available >= MIN_TRANSFER_USDT:
        amt = available
        try:
            transfer_result = transfer_fn(amt, ACCT_TRADING, ACCT_FUNDING)
        except Exception as exc:
            transfer_result = {"ok": False, "amt": amt, "error": str(exc)}
        transferred = bool(transfer_result and transfer_result.get("ok") and not transfer_result.get("skipped"))
        if transferred:
            state["lock_evacuated"] = True
            prev = _safe_float(state.get("evacuated_amt"), 0.0) or 0.0
            state["evacuated_amt"] = prev + float(amt)
            got_usdc = _safe_float((transfer_result or {}).get("usdc"), 0.0) or 0.0
            if got_usdc <= 0:
                got_usdc = float(amt)
            live_usdc = _live_usdc_available()
            if live_usdc is not None and live_usdc > 0:
                state["evacuated_usdc"] = live_usdc
            else:
                prev_usdc = _safe_float(state.get("evacuated_usdc"), 0.0) or 0.0
                state["evacuated_usdc"] = prev_usdc + got_usdc
        elif isinstance(transfer_result, dict):
            transfer_error = transfer_result.get("error") or transfer_result.get("msg") or "划转未确认成功"
        else:
            transfer_error = "划转未确认成功"

    inbound = False
    from_auto_close = False
    close_snap = {}
    if state.get("lock_evacuated") and not locked_this_tick:
        if available >= INBOUND_DUST_USDT:
            inbound = True
            close_snap = auto_close_repark_window_snap(now_ts)
            from_auto_close = bool(close_snap)

    message = None
    notify_result = None
    notified = False
    if locked_this_tick:
        message = build_lock_message(
            state.get("danger_count") or 0,
            transferred,
            amt if amt else None,
            transfer_error=transfer_error,
        )
        key = "lock_arm|" + str(state.get("beijing_date") or "")
        if _should_notify(state, key, now_ts, cooldown=1):
            notify_result = notify_fn(message, {
                "kind": "day_lock",
                "danger_count": state.get("danger_count"),
                "transferred": transferred,
            })
            notified = True
            state.setdefault("last_notify_ts", {})[key] = now_ts
    elif inbound and from_auto_close:
        message = build_auto_close_repark_message(
            amt if amt else available, transferred, snap=close_snap)
        key = "auto_close_repark|" + str(close_snap.get("started_ts") or state.get("beijing_date") or "")
        if _should_notify(state, key, now_ts, cooldown=1):
            notify_result = notify_fn(message, {
                "kind": "force_protect_auto_close_repark",
                "amt": amt or available,
                "transferred": transferred,
                "auto_close": True,
            })
            notified = _manual_notify_succeeded(notify_result)
            if notified:
                state.setdefault("last_notify_ts", {})[key] = now_ts
        if transferred:
            end_auto_close_repark_window()
    elif inbound:
        message = build_inbound_block_message(amt if transferred else available, transferred)
        key = "inbound_block|" + str(state.get("beijing_date") or "")
        if _should_notify(state, key, now_ts, cooldown=INBOUND_NOTIFY_COOLDOWN_SEC):
            notify_result = notify_fn(message, {
                "kind": "inbound_block",
                "amt": amt or available,
                "transferred": transferred,
            })
            notified = True
            state.setdefault("last_notify_ts", {})[key] = now_ts

    if inbound and from_auto_close:
        row_kind = "auto_close_lock_repark"
    elif locked_this_tick:
        row_kind = "lock_arm"
    elif inbound:
        row_kind = "inbound_block"
    else:
        row_kind = "lock_hold"
    row = {
        "time": _now(),
        "kind": row_kind,
        "closed": closed,
        "available_usdt": available,
        "transferred": transferred,
        "transfer": transfer_result,
        "inbound": inbound,
        "auto_close": from_auto_close,
        "notified": notified,
        "message": message,
        "notify": notify_result,
    }
    _append_event(row)
    return row


def evaluate_positions(positions, equity_usdt):
    hits = []
    for pos in positions or []:
        reasons = violation_reasons(pos, equity_usdt)
        if reasons:
            hits.append({"position": pos, "reasons": reasons})
    return hits


def evaluate_size_advisories(positions, equity_usdt, force_hits=None):
    force_ids = set()
    for hit in force_hits or []:
        pos = (hit or {}).get("position") or {}
        force_ids.add(pos.get("position_id") or pos.get("inst_id"))
    rows = []
    for pos in positions or []:
        key = pos.get("position_id") or pos.get("inst_id")
        if key in force_ids:
            continue
        reasons = advisory_reasons(pos, equity_usdt)
        if reasons:
            rows.append({"position": pos, "reasons": reasons})
    return rows


def apply_manual_open_quota(state, positions):
    """Record new manual opens and mark extras beyond today's cap.

    Regular cap is 2. After that, 顺风车 (same inst+side as a live
    方向型 auto position) get a separate extra cap of 3. 赔率型自动仓
    即使同向也不算顺风车。

    OKX may reuse the same posId after close+reopen. Open stamp (cTime)
    distinguishes events; when stamp is missing, a bare id that left the
    live set and later returns gets a new generation fingerprint.
    """
    regular = list(state.get("manual_open_ids") or [])
    extra = list(state.get("hitch_extra_ids") or [])
    denied = list(state.get("manual_denied_ids") or [])
    # Old quota appended over-limit fingerprints into the regular list.
    if len(regular) > MANUAL_OPEN_LIMIT:
        overflow = regular[MANUAL_OPEN_LIMIT:]
        regular = regular[:MANUAL_OPEN_LIMIT]
        for old_fp in overflow:
            if old_fp not in extra and old_fp not in denied:
                denied.append(old_fp)
    known = set(regular)
    known.update(extra)
    known.update(denied)
    auto_keys = auto_side_keys(positions)
    active_prev = set(state.get("manual_active_bare_ids") or [])
    closed_seen = set(state.get("manual_closed_bare_ids") or [])
    generations = dict(state.get("manual_bare_generations") or {})
    active_now = set()
    new_rows = []
    over_limit = []
    hitch_live = []
    for pos in positions or []:
        if not is_manual_position(pos):
            continue
        bare = manual_bare_id(pos)
        if not bare:
            continue
        stamp = manual_open_stamp(pos)
        if stamp:
            fp = "%s|%s" % (bare, stamp)
        elif bare in closed_seen:
            generations[bare] = int(generations.get(bare) or 1) + 1
            closed_seen.discard(bare)
            fp = "%s#%s" % (bare, generations[bare])
        else:
            fp = bare
        active_now.add(bare)
        hitch = is_hitchhiker(pos, auto_keys=auto_keys)
        is_new = fp not in known
        used_extra = fp in extra
        if is_new:
            known.add(fp)
            if len(regular) < MANUAL_OPEN_LIMIT:
                regular.append(fp)
                used_extra = False
            elif hitch:
                extra.append(fp)
                used_extra = True
            else:
                denied.append(fp)
                used_extra = False
        allowed = set(regular[:MANUAL_OPEN_LIMIT])
        allowed.update(extra[:HITCH_EXTRA_LIMIT])
        over = fp not in allowed
        extra_count = len(extra)
        row = {
            "position": pos,
            "fingerprint": fp,
            "is_new": is_new,
            "over_limit": over,
            "hitch": hitch,
            "used_extra": used_extra,
            "count": len(regular),
            "remaining": remaining_manual_opens(len(regular)),
            "extra_count": extra_count,
            "extra_remaining": remaining_manual_opens(extra_count, HITCH_EXTRA_LIMIT),
        }
        if is_new:
            new_rows.append(row)
        if over:
            over_limit.append(row)
        if hitch and not over:
            hitch_live.append(row)
    for bare in active_prev - active_now:
        closed_seen.add(bare)
    state["manual_open_ids"] = regular
    state["manual_open_count"] = len(regular)
    state["hitch_extra_ids"] = extra
    state["hitch_extra_count"] = len(extra)
    state["manual_denied_ids"] = denied
    state["manual_active_bare_ids"] = sorted(active_now)
    state["manual_closed_bare_ids"] = sorted(closed_seen)
    state["manual_bare_generations"] = generations
    return {"new": new_rows, "over_limit": over_limit, "hitch_live": hitch_live}


def _manual_notify_succeeded(result):
    """Accept old test adapters while requiring a real outbound receipt.

    Production notify may return sent=False with accepted=True after WxPusher
    creates the send task. That is already a confirmed outbound, not a retry.
    """
    if not isinstance(result, dict):
        return False
    if result.get("blocked") or result.get("dry_run"):
        return False
    if result.get("accepted"):
        return True
    send_result = result.get("send_result")
    if isinstance(send_result, dict) and send_result.get("wxpusher_success"):
        return True
    if "sent" in result:
        return bool(result.get("ok") and result.get("sent"))
    return bool(result.get("ok"))


def _manual_notify_retry_delay(attempts):
    power = max(0, int(attempts or 1) - 1)
    return min(MANUAL_NOTIFY_RETRY_MAX_SEC,
               MANUAL_NOTIFY_RETRY_BASE_SEC * (2 ** min(power, 5)))


def _queue_item_from_row(state, row, equity_usdt, now_ts, status_only=False):
    pos = row["position"]
    fp = str(row.get("fingerprint") or manual_fingerprint(pos))
    return {
        "event_id": "%s|%s" % (state.get("beijing_date"), fp),
        "fingerprint": fp,
        "beijing_date": state.get("beijing_date"),
        "inst_id": pos.get("inst_id"),
        "side": pos.get("side"),
        "count": int(row.get("count") or 0),
        "remaining": int(row.get("remaining") or 0),
        "hitch": bool(row.get("hitch")),
        "used_extra": bool(row.get("used_extra")),
        "extra_count": int(row.get("extra_count") or 0),
        "extra_remaining": int(row.get("extra_remaining") or 0),
        "status_only": bool(status_only),
        "message": build_manual_detect_message(
            pos, equity_usdt, row["count"], row["remaining"],
            hitch=row.get("hitch"),
            extra_count=row.get("extra_count") or 0,
            extra_remaining=row.get("extra_remaining"),
            used_extra=row.get("used_extra"),
            status_only=status_only,
        ),
        "created_at": _now(),
        "created_at_ts": float(now_ts),
        "attempts": 0,
        "next_retry_ts": float(now_ts),
        "last_error": None,
    }


def _mark_hitch_notified(state, fp):
    if not fp:
        return
    notified = list(state.get("hitch_notified_ids") or [])
    if fp not in notified:
        notified.append(fp)
        state["hitch_notified_ids"] = notified


def _upgrade_pending_hitch(item, row, equity_usdt):
    """Keep retrying the same fingerprint, but refresh hitch remaining text."""
    if not row.get("hitch"):
        return False
    if item.get("hitch") and item.get("extra_remaining") is not None:
        return False
    item["hitch"] = True
    item["used_extra"] = bool(row.get("used_extra"))
    item["extra_count"] = int(row.get("extra_count") or 0)
    item["extra_remaining"] = int(row.get("extra_remaining") or 0)
    item["count"] = int(row.get("count") or item.get("count") or 0)
    item["remaining"] = int(row.get("remaining") or item.get("remaining") or 0)
    item["message"] = build_manual_detect_message(
        row["position"], equity_usdt, item["count"], item["remaining"],
        hitch=True,
        extra_count=item["extra_count"],
        extra_remaining=item["extra_remaining"],
        used_extra=item["used_extra"],
        status_only=bool(item.get("status_only")),
    )
    return True


def _queue_manual_notifications(state, new_rows, hitch_live_rows, equity_usdt, now_ts):
    pending = state.setdefault("pending_manual_notifications", {})
    queued = []
    notified = set(state.get("hitch_notified_ids") or [])

    def enqueue(row, status_only=False):
        if row.get("over_limit"):
            return
        pos = row["position"]
        fp = str(row.get("fingerprint") or manual_fingerprint(pos))
        if not fp:
            return
        if fp in pending:
            if _upgrade_pending_hitch(pending[fp], row, equity_usdt) and row.get("hitch"):
                _mark_hitch_notified(state, fp)
                notified.add(fp)
            return
        pending[fp] = _queue_item_from_row(
            state, row, equity_usdt, now_ts, status_only=status_only)
        queued.append(fp)
        if row.get("hitch"):
            _mark_hitch_notified(state, fp)
            notified.add(fp)

    for row in new_rows or []:
        enqueue(row, status_only=False)
    for row in hitch_live_rows or []:
        fp = str(row.get("fingerprint") or "")
        if fp and fp in notified:
            continue
        enqueue(row, status_only=not row.get("is_new"))
    return queued


def _deliver_pending_manual_notifications(state, notify_fn, now_ts):
    pending = state.setdefault("pending_manual_notifications", {})
    notes = []
    for fp in list(pending):
        item = dict(pending.get(fp) or {})
        if float(item.get("next_retry_ts") or 0.0) > float(now_ts):
            continue
        attempt = int(item.get("attempts") or 0) + 1
        meta = {
            "kind": "manual_open_detect",
            "manual_notification_event_id": item.get("event_id"),
            "inst_id": item.get("inst_id"),
            "side": item.get("side"),
            "count": item.get("count"),
            "remaining": item.get("remaining"),
            "hitch": bool(item.get("hitch")),
            "used_extra": bool(item.get("used_extra")),
            "extra_count": item.get("extra_count"),
            "extra_remaining": item.get("extra_remaining"),
            "attempt": attempt,
            "retry": attempt > 1,
        }
        try:
            result = notify_fn(item.get("message") or "", meta) or {}
        except Exception as exc:
            result = {"ok": False, "sent": False, "error": str(exc)}
        succeeded = _manual_notify_succeeded(result)
        note = {
            "time": _now(),
            "kind": "manual_open_detect" if attempt == 1 else "manual_open_detect_retry",
            "event_id": item.get("event_id"),
            "inst_id": item.get("inst_id"),
            "side": item.get("side"),
            "count": item.get("count"),
            "remaining": item.get("remaining"),
            "hitch": bool(item.get("hitch")),
            "used_extra": bool(item.get("used_extra")),
            "extra_remaining": item.get("extra_remaining"),
            "attempt": attempt,
            "retry": attempt > 1,
            "delivery_succeeded": succeeded,
            "message": item.get("message"),
            "notify": result,
        }
        notes.append(note)
        _append_event(note)
        if succeeded:
            pending.pop(fp, None)
        else:
            item["attempts"] = attempt
            item["last_attempt_at"] = _now()
            item["last_attempt_ts"] = float(now_ts)
            item["last_error"] = (
                result.get("notification_error") or result.get("error")
                or "notification_not_confirmed_sent")
            item["next_retry_ts"] = (
                float(now_ts) + _manual_notify_retry_delay(attempt))
            pending[fp] = item
        # Persist every receipt transition.  A process restart can therefore
        # resume a failed delivery without recounting the position.
        _persist_state(state)
    return notes


def _merge_quota_hits(hits, quota):
    by_fp = {}
    for hit in hits or []:
        pos = hit["position"]
        by_fp[manual_fingerprint(pos)] = {
            "position": pos,
            "reasons": list(hit.get("reasons") or []),
        }
    for row in quota.get("over_limit") or []:
        pos = row["position"]
        fp = row["fingerprint"]
        if fp in by_fp:
            if "manual_open_over_limit" not in by_fp[fp]["reasons"]:
                by_fp[fp]["reasons"].append("manual_open_over_limit")
        else:
            by_fp[fp] = {"position": pos, "reasons": ["manual_open_over_limit"]}
    return list(by_fp.values())


def _session_pos_fp(pos):
    fp = str(manual_fingerprint(pos) or "").strip()
    if fp:
        return fp
    return "%s|%s|%s" % (
        pos.get("inst_id") or "",
        pos.get("side") or "",
        pos.get("mgn_mode") or "",
    )


def build_session_open_message(snap):
    snap = snap or {}
    facts = [
        SESSION_OPEN_LINE,
        "本周窗口 %s ～ %s（北京时间）" % (
            snap.get("start_beijing") or "周一 07:30",
            snap.get("end_beijing") or "周六 04:30",
        ),
        "当前北京时间 %s" % (snap.get("now_beijing") or ""),
        "策略信号、自动开仓与平仓现已恢复",
    ]
    quote = (
        "门铃叮咚！本周交易日开市啦。北京时间周一07:30到周六04:30，小管家把策略小门铃重新挂上，开始听信号、帮你看开仓和平仓。"
    )
    return _sweet_note(SESSION_OPEN_LINE, "🌅 庄园开市啦：", quote, facts)


def build_session_close_message(rows, all_ok, snap=None):
    snap = snap or {}
    facts = [SESSION_CLOSE_LINE]
    if snap.get("end_beijing"):
        facts.append("收盘时刻 %s（北京时间）" % snap.get("end_beijing"))
    facts.append("下一次开市：北京时间周一07:30")
    if not rows:
        quote = (
            "%s。本周交易日结束：北京时间周六04:30。当前没有持仓，庄园已打烊。策略信号与自动开平仓暂停，直到下周一07:30。"
            % SESSION_CLOSE_LINE
        )
        return _sweet_note(SESSION_CLOSE_LINE, "🌙 庄园打烊啦：", quote, facts)
    for row in rows or []:
        pos = row.get("position") or {}
        title = pos.get("strategy_title") or (
            "手动开仓" if is_manual_position(pos) else "自动策略")
        facts.append("%s %s %s" % (
            title, _symbol_zh(pos), _dir_zh(pos)))
        facts.append("已平仓" if row.get("closed") else "平仓未确认，将继续重试")
    quote = (
        "%s。本周交易时段已结束：北京时间周一07:30至周六04:30。小鸡毛把还在围栏里的仓位全部抱回家啦，这是收盘打烊，不是危险急刹车。"
        % SESSION_CLOSE_LINE
        if all_ok else
        "%s。本周交易时段已结束，小鸡毛正在把还没回家的仓位抱回围栏，请稍等。"
        % SESSION_CLOSE_LINE
    )
    return _sweet_note(SESSION_CLOSE_LINE, "🌙 庄园打烊啦：", quote, facts)


def _session_close_one(pos, close_fn, reason):
    closed = close_fn(
        pos.get("inst_id"),
        pos.get("side"),
        reason=reason,
        mgn_mode=pos.get("mgn_mode"),
    ) or {}
    did_close = bool(closed.get("ok") and closed.get("closed"))
    already = bool(closed.get("ok") and closed.get("reason") == "position_already_absent")
    return closed, bool(did_close or already)


def _session_protect_payload(
        state, listed, equity_usdt, unlock_result, detect_notes, actions,
        session_close=False, session_open=False):
    return {
        "ok": True,
        "intervened": bool(actions),
        "session_close": bool(session_close),
        "session_open": bool(session_open),
        "equity_usdt": equity_usdt,
        "n_positions": listed.get("position_count") or len(listed.get("positions") or []),
        "actions": actions,
        "hitch_follow_closes": [],
        "advisories": [],
        "danger_count": int(state.get("danger_count") or 0),
        "lock_to_funding": bool(state.get("lock_to_funding")),
        "lock_result": None,
        "unlock_result": unlock_result,
        "beijing_date": state.get("beijing_date"),
        "manual_open_count": int(state.get("manual_open_count") or 0),
        "manual_remaining": remaining_manual_opens(state.get("manual_open_count")),
        "hitch_extra_count": int(state.get("hitch_extra_count") or 0),
        "hitch_extra_remaining": remaining_manual_opens(
            state.get("hitch_extra_count"), HITCH_EXTRA_LIMIT),
        "manual_detects": detect_notes,
        "pending_manual_notification_count": len(
            state.get("pending_manual_notifications") or {}),
        "session": None,
    }


def _apply_session_window(
        state, listed, positions, equity_usdt, close_fn, notify_fn,
        now_ts, unlock_result, detect_notes):
    import auto_trade_session_clock as session_clock
    snap = session_clock.session_snapshot(now_ts)
    if session_clock.in_session(now_ts):
        dirty = False
        for key, empty in (
            ("session_flatten_week", ""),
            ("session_leftover_fps", []),
            ("session_close_notified_week", ""),
        ):
            if state.get(key):
                state[key] = empty
                dirty = True
        week_id = session_clock.session_week_id(now_ts)
        opened = False
        if (state.get("session_open_notified_week") or "") != week_id:
            message = build_session_open_message(snap)
            notify_result = notify_fn(message, {
                "kind": "force_protect_session_open",
                "week_id": week_id,
                "start_beijing": snap.get("start_beijing"),
                "end_beijing": snap.get("end_beijing"),
            })
            opened = _manual_notify_succeeded(notify_result)
            if opened:
                state["session_open_notified_week"] = week_id
            dirty = True
            _append_event({
                "time": _now(),
                "kind": "session_open",
                "week_id": week_id,
                "notified": opened,
                "notify": notify_result,
            })
        if dirty:
            _persist_state(state)
        state["_session_open_just_sent"] = opened
        return None

    closed_id = session_clock.closed_session_id(now_ts) or session_clock.session_week_id(now_ts)
    live = [pos for pos in (positions or []) if _is_live_holding(pos)]
    actions = []

    if state.get("session_flatten_week") == closed_id:
        for pos in live:
            closed, gone = _session_close_one(
                pos, close_fn, "force_protect:manual_open_outside_session")
            if closed.get("ok") and closed.get("closed"):
                state["danger_count"] = int(state.get("danger_count") or 0) + 1
            count_now = int(state.get("manual_open_count") or 0)
            message = build_intervene_message(
                pos, equity_usdt, ["manual_open_outside_session"], gone,
                remaining=remaining_manual_opens(count_now),
                count=count_now,
            )
            notify_result = None
            notified = False
            action_key = "outside_session_manual|%s" % _session_pos_fp(pos)
            if _should_notify(state, action_key, now_ts):
                notify_result = notify_fn(message, {
                    "kind": "force_protect_intervene",
                    "inst_id": pos.get("inst_id"),
                    "side": pos.get("side"),
                    "reasons": ["manual_open_outside_session"],
                    "closed": gone,
                })
                notified = True
                state.setdefault("last_notify_ts", {})[action_key] = now_ts
            row = {
                "time": _now(),
                "kind": "manual_open_outside_session",
                "inst_id": pos.get("inst_id"),
                "side": pos.get("side"),
                "manual": is_manual_position(pos),
                "reasons": ["manual_open_outside_session"],
                "closed": gone,
                "close": closed,
                "notified": notified,
                "notify": notify_result,
                "message": message,
                "danger": True,
            }
            actions.append(row)
            _append_event(row)
        _persist_state(state)
        out = _session_protect_payload(
            state, listed, equity_usdt, unlock_result, detect_notes, actions)
        out["session"] = snap
        return out

    leftover_fps = list(state.get("session_leftover_fps") or [])
    if not leftover_fps and live:
        leftover_fps = [_session_pos_fp(pos) for pos in live]
        state["session_leftover_fps"] = leftover_fps
        _persist_state(state)
    leftover_set = set(leftover_fps)
    leftover_rows = []
    live_fps = set()
    for pos in live:
        fp = _session_pos_fp(pos)
        live_fps.add(fp)
        if leftover_set and fp not in leftover_set:
            closed, gone = _session_close_one(
                pos, close_fn, "force_protect:manual_open_outside_session")
            if closed.get("ok") and closed.get("closed"):
                state["danger_count"] = int(state.get("danger_count") or 0) + 1
            count_now = int(state.get("manual_open_count") or 0)
            message = build_intervene_message(
                pos, equity_usdt, ["manual_open_outside_session"], gone,
                remaining=remaining_manual_opens(count_now),
                count=count_now,
            )
            notify_result = None
            notified = False
            action_key = "outside_session_manual|%s" % fp
            if _should_notify(state, action_key, now_ts):
                notify_result = notify_fn(message, {
                    "kind": "force_protect_intervene",
                    "inst_id": pos.get("inst_id"),
                    "side": pos.get("side"),
                    "reasons": ["manual_open_outside_session"],
                    "closed": gone,
                })
                notified = True
                state.setdefault("last_notify_ts", {})[action_key] = now_ts
            row = {
                "time": _now(),
                "kind": "manual_open_outside_session",
                "inst_id": pos.get("inst_id"),
                "side": pos.get("side"),
                "manual": is_manual_position(pos),
                "reasons": ["manual_open_outside_session"],
                "closed": gone,
                "close": closed,
                "notified": notified,
                "notify": notify_result,
                "message": message,
                "danger": True,
            }
            actions.append(row)
            _append_event(row)
            continue
        closed, gone = _session_close_one(
            pos, close_fn, "force_protect:session_close")
        row = {
            "time": _now(),
            "kind": "session_close",
            "inst_id": pos.get("inst_id"),
            "side": pos.get("side"),
            "manual": is_manual_position(pos),
            "reasons": ["session_close"],
            "closed": gone,
            "close": closed,
            "danger": False,
            "position": pos,
        }
        leftover_rows.append(row)
        actions.append(row)
        _append_event(row)

    closed_fps = set()
    all_ok = True
    for row in leftover_rows:
        if row.get("closed"):
            closed_fps.add(_session_pos_fp(row.get("position") or {}))
        else:
            all_ok = False
    leftover_remaining = [
        fp for fp in leftover_fps
        if fp in live_fps and fp not in closed_fps
    ]
    if not leftover_remaining:
        state["session_flatten_week"] = closed_id
        state["session_leftover_fps"] = []
    else:
        state["session_leftover_fps"] = leftover_remaining

    if (state.get("session_close_notified_week") or "") != closed_id:
        message = build_session_close_message(leftover_rows, all_ok, snap=snap)
        notify_result = notify_fn(message, {
            "kind": "force_protect_session_close",
            "closed_session_id": closed_id,
            "n_closed": len([r for r in leftover_rows if r.get("closed")]),
            "n_positions": len(leftover_rows),
        })
        notified = _manual_notify_succeeded(notify_result)
        for row in leftover_rows:
            row["notified"] = notified
            row["notify"] = notify_result
            row["message"] = message
        if not leftover_rows:
            _append_event({
                "time": _now(),
                "kind": "session_close",
                "closed_session_id": closed_id,
                "n_positions": 0,
                "notified": notified,
                "notify": notify_result,
                "message": message,
                "danger": False,
            })
        if notified:
            state["session_close_notified_week"] = closed_id

    _persist_state(state)
    out = _session_protect_payload(
        state, listed, equity_usdt, unlock_result, detect_notes, actions,
        session_close=True)
    out["session"] = snap
    return out


def protect_once(
    close_fn=None, notify_fn=None, listed=None, equity=None,
    transfer_fn=None, trading_fn=None, bills_fn=None, now_ts=None,
):
    import auto_trade_live_positions as live_pos
    now_ts = time.time() if now_ts is None else now_ts
    state = _load_state(now_ts=now_ts)

    if close_fn is None:
        close_fn = live_pos.close_live_position
    if notify_fn is None:
        notify_fn = _notify
    if transfer_fn is None:
        transfer_fn = lock_convert
    if trading_fn is None:
        trading_fn = trading_usdt
    if bills_fn is None:
        bills_fn = funding_to_trading_bills

    unlock_result = None
    pending_amt = max(
        _safe_float(state.get("pending_restore_usdc"), 0.0) or 0.0,
        _safe_float(state.get("pending_restore_amt"), 0.0) or 0.0,
    )
    if state.get("unlock_notify_pending") or pending_amt >= MIN_TRANSFER_USDT:
        unlock_result = apply_pending_restore(state, transfer_fn, notify_fn, now_ts=now_ts)
        _persist_state(state)

    # Pending Wx retries must not wait on OKX listing.
    detect_notes = _deliver_pending_manual_notifications(
        state, notify_fn, now_ts)

    if listed is None:
        try:
            listed = live_pos._list_live_positions_uncached()
        except Exception as exc:
            listed = {"ok": False, "error": str(exc)}
    if not listed.get("ok"):
        return {
            "ok": False,
            "error": listed.get("error") or "positions_failed",
            "actions": [],
            "unlock_result": unlock_result,
            "manual_detects": detect_notes,
            "pending_manual_notification_count": len(
                state.get("pending_manual_notifications") or {}),
        }
    if equity is None:
        equity = account_equity_usdt() if trading_fn is None else trading_fn()
    if not equity.get("ok"):
        return {
            "ok": False,
            "error": equity.get("error") or "equity_failed",
            "actions": [],
            "unlock_result": unlock_result,
            "manual_detects": detect_notes,
            "pending_manual_notification_count": len(
                state.get("pending_manual_notifications") or {}),
        }
    equity_usdt = equity.get("equity_usdt")
    positions = listed.get("positions") or []
    session_out = _apply_session_window(
        state, listed, positions, equity_usdt, close_fn, notify_fn,
        now_ts, unlock_result, detect_notes)
    if session_out is not None:
        return session_out
    session_open_just_sent = bool(state.pop("_session_open_just_sent", False))
    quota = apply_manual_open_quota(state, positions)
    hitch_orphans = sync_hitch_bindings(state, positions)
    hits = _merge_quota_hits(evaluate_positions(positions, equity_usdt), quota)
    advisories = evaluate_size_advisories(positions, equity_usdt, hits)
    prune_size_warn_notified(state, positions)
    seed_size_warn_from_legacy_cooldown(state, positions)

    _queue_manual_notifications(
        state,
        quota.get("new") or [],
        quota.get("hitch_live") or [],
        equity_usdt,
        now_ts,
    )
    # Crash-safe boundary: count/fingerprint and outbound notification are
    # committed together before the first network request.
    _persist_state(state)
    detect_notes.extend(_deliver_pending_manual_notifications(
        state, notify_fn, now_ts))

    still_open_fn = None
    if getattr(close_fn, "__name__", "") in ("close_live_position", "close_live_position"):
        still_open_fn = _hitch_manual_still_open
    hitch_actions = close_orphaned_hitchhikers(
        state, hitch_orphans, close_fn, notify_fn, now_ts=now_ts,
        still_open_fn=still_open_fn)
    if hitch_actions:
        _persist_state(state)
    hitch_fps = set()
    for item in hitch_orphans or []:
        fp = item.get("fingerprint")
        if fp:
            hitch_fps.add(fp)

    actions = []
    for hit in hits:
        pos = hit["position"]
        if manual_fingerprint(pos) in hitch_fps:
            continue
        reasons = hit["reasons"]
        action_key = "%s|%s" % (pos.get("position_id") or pos.get("inst_id"), ",".join(reasons))
        closed = close_fn(
            pos.get("inst_id"),
            pos.get("side"),
            reason="force_protect:" + ",".join(reasons),
            mgn_mode=pos.get("mgn_mode"),
        ) or {}
        did_close = bool(closed.get("ok") and closed.get("closed"))
        already = bool(closed.get("ok") and closed.get("reason") == "position_already_absent")
        if did_close:
            state["danger_count"] = int(state.get("danger_count") or 0) + 1
        count_now = int(state.get("manual_open_count") or 0)
        message = build_intervene_message(
            pos, equity_usdt, reasons, did_close or already,
            remaining=remaining_manual_opens(count_now),
            count=count_now,
        )
        notified = False
        notify_result = None
        if _should_notify(state, action_key, now_ts):
            notify_result = notify_fn(message, {
                "kind": "force_protect_intervene",
                "inst_id": pos.get("inst_id"),
                "side": pos.get("side"),
                "reasons": reasons,
                "closed": did_close or already,
            })
            notified = True
            state.setdefault("last_notify_ts", {})[action_key] = now_ts
        row = {
            "time": _now(),
            "inst_id": pos.get("inst_id"),
            "side": pos.get("side"),
            "manual": is_manual_position(pos),
            "reasons": reasons,
            "occupancy_ratio": occupancy_ratio(pos, equity_usdt),
            "leverage": _safe_float(pos.get("leverage")),
            "closed": did_close or already,
            "close": closed,
            "notified": notified,
            "notify": notify_result,
            "message": message,
        }
        actions.append(row)
        _append_event(row)

    advisory_actions = []
    warned = set(state.get("size_warn_notified_ids") or [])
    for hit in advisories:
        pos = hit["position"]
        if manual_fingerprint(pos) in hitch_fps:
            continue
        reasons = hit["reasons"]
        fp_id = size_warn_fingerprint(pos)
        message = build_size_warn_message(pos, equity_usdt)
        notified = False
        notify_result = None
        already = bool(fp_id and fp_id in warned)
        if fp_id and not already:
            notify_result = notify_fn(message, {
                "kind": "force_protect_manual_size_warn",
                "inst_id": pos.get("inst_id"),
                "side": pos.get("side"),
                "reasons": reasons,
                "closed": False,
                "danger": False,
            })
            if _manual_notify_succeeded(notify_result):
                notified = True
                warned.add(fp_id)
                state["size_warn_notified_ids"] = list(warned)
                _persist_state(state)
        row = {
            "time": _now(),
            "kind": "manual_size_warn",
            "inst_id": pos.get("inst_id"),
            "side": pos.get("side"),
            "manual": True,
            "reasons": reasons,
            "occupancy_ratio": occupancy_ratio(pos, equity_usdt),
            "leverage": _safe_float(pos.get("leverage")),
            "closed": False,
            "intervened": False,
            "danger": False,
            "notified": notified,
            "notify": notify_result,
            "message": message,
        }
        advisory_actions.append(row)
        if not already:
            _append_event(row)

    locked_this_tick = False
    if int(state.get("danger_count") or 0) >= DANGER_LIMIT and not state.get("lock_to_funding"):
        state["lock_to_funding"] = True
        state["lock_at"] = _now()
        state["lock_at_ts"] = now_ts
        locked_this_tick = True
        _persist_state(state)

    lock_result = None
    if state.get("lock_to_funding"):
        try:
            lock_result = enforce_funding_lock(
                state,
                listed.get("positions") or [],
                close_fn,
                notify_fn,
                transfer_fn,
                trading_fn,
                locked_this_tick=locked_this_tick,
                now_ts=now_ts,
                bills_fn=bills_fn,
            )
        except Exception as exc:
            lock_result = {"ok": False, "error": str(exc), "kind": "lock_error"}
            _append_event({"time": _now(), "kind": "lock_error", "error": str(exc)})

    _persist_state(state)
    intervened = bool(actions) or bool(hitch_actions) or bool(lock_result and (
        lock_result.get("transferred") or lock_result.get("inbound") or locked_this_tick
    ))
    return {
        "ok": True,
        "intervened": intervened,
        "session_open": session_open_just_sent,
        "session_close": False,
        "equity_usdt": equity_usdt,
        "n_positions": listed.get("position_count") or len(listed.get("positions") or []),
        "actions": actions,
        "hitch_follow_closes": hitch_actions,
        "advisories": advisory_actions,
        "danger_count": int(state.get("danger_count") or 0),
        "lock_to_funding": bool(state.get("lock_to_funding")),
        "lock_result": lock_result,
        "unlock_result": unlock_result,
        "beijing_date": state.get("beijing_date"),
        "manual_open_count": int(state.get("manual_open_count") or 0),
        "manual_remaining": remaining_manual_opens(state.get("manual_open_count")),
        "hitch_extra_count": int(state.get("hitch_extra_count") or 0),
        "hitch_extra_remaining": remaining_manual_opens(
            state.get("hitch_extra_count"), HITCH_EXTRA_LIMIT),
        "manual_detects": detect_notes,
        "pending_manual_notification_count": len(
            state.get("pending_manual_notifications") or {}),
    }


def run_forever(poll_sec=POLL_SEC):
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    print("force_protect start", _now(), "warn>", WARN_RATIO, "force>", FORCE_CLOSE_RATIO, "lev>", MAX_LEVERAGE)
    while True:
        try:
            out = protect_once()
            if out.get("unlock_result") and out["unlock_result"].get("notified"):
                print(_now(), "unlock", out["unlock_result"].get("kind"), "amt", out["unlock_result"].get("amt"))
            elif out.get("lock_result") and out["lock_result"].get("kind") == "auto_close_lock_repark":
                print(_now(), "auto_close_lock_repark", out["lock_result"].get("available_usdt"))
            elif out.get("lock_to_funding"):
                print(_now(), "lock", "count", out.get("danger_count"), out.get("lock_result") and out["lock_result"].get("kind"))
            elif out.get("hitch_follow_closes"):
                print(_now(), "hitch_follow_close", len(out.get("hitch_follow_closes") or []))
            elif out.get("session_open"):
                print(_now(), "session_open")
            elif out.get("session_close"):
                print(_now(), "session_close", len(out.get("actions") or []))
            elif out.get("intervened"):
                print(_now(), "intervened", len(out.get("actions") or []), "count", out.get("danger_count"))
            elif any(a.get("notified") for a in (out.get("advisories") or [])):
                print(_now(), "size_warn", len(out.get("advisories") or []))
            elif out.get("manual_detects"):
                print(_now(), "manual_open", len(out.get("manual_detects") or []),
                      "left", out.get("manual_remaining"),
                      "hitch_left", out.get("hitch_extra_remaining"))
            elif not out.get("ok"):
                print(_now(), "protect_error", out.get("error"))
        except Exception as exc:
            print(_now(), "protect_exception", type(exc).__name__, exc)
            _append_event({"time": _now(), "error": str(exc), "kind": "exception"})
        time.sleep(max(1.0, float(poll_sec)))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-sec", type=float, default=POLL_SEC)
    args = parser.parse_args()
    if args.once:
        print(json.dumps(protect_once(), ensure_ascii=False, indent=2, default=str))
    else:
        run_forever(poll_sec=args.poll_sec)
