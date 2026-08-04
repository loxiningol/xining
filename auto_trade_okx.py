
from pathlib import Path
import os
import json
import time
import hmac
import hashlib
import base64
import datetime
import urllib.parse
import urllib.request
import urllib.error
import math
import stat

ROOT = Path("/root")
AUTO_DIR = ROOT / "auto_trade"
CREDENTIAL_FILE = AUTO_DIR / "okx_credentials.json"
STATUS_CACHE_FILE = AUTO_DIR / "okx_api_status.json"

DEFAULT_BASE_URL = "https://www.okx.com"
DEFAULT_SYMBOL = "BTC-USDT-SWAP"
DEFAULT_TD_MODE = "cross"

PUBLIC_TIMEOUT = 10
PRIVATE_TIMEOUT = 10

def _now_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def _utc_timestamp():
    dt = datetime.datetime.utcnow()
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + ("%03d" % int(dt.microsecond / 1000)) + "Z"

def _mask(value, keep=4):
    if value is None:
        return None
    text = str(value)
    if not text:
        return ""
    if len(text) <= keep * 2:
        return "*" * len(text)
    return text[:keep] + "*" * (len(text) - keep * 2) + text[-keep:]

def _json_read(path, default=None):
    if default is None:
        default = {}
    try:
        if not Path(path).exists():
            return default
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            return default
        data = json.loads(text)
        return data if isinstance(data, dict) else default
    except Exception:
        return default

def _json_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp_stage8_12b")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)

def _env_true(name):
    return os.environ.get(name, "").strip().lower() in ["1", "true", "yes", "y", "on"]

def normalize_okx_symbol(symbol=None):
    text = str(symbol or DEFAULT_SYMBOL).strip().upper()
    if text in ["BTC-USDT", "BTCUSDT", "BTC-USDT-SWAP"]:
        return "BTC-USDT-SWAP"
    if text.endswith("-SWAP"):
        return text
    if text.count("-") == 1:
        return text + "-SWAP"
    return text

def get_okx_credential_file_permission_status():
    path = CREDENTIAL_FILE

    if not path.exists():
        return {
            "exists": False,
            "path": str(path),
            "mode": None,
            "strict": False,
            "warning": "credential file does not exist"
        }

    try:
        mode_int = stat.S_IMODE(path.stat().st_mode)
        mode_text = oct(mode_int)
        strict = (mode_int & 0o077) == 0
        return {
            "exists": True,
            "path": str(path),
            "mode": mode_text,
            "strict": bool(strict),
            "warning": None if strict else "credential file permission should be 600 or stricter"
        }
    except Exception as e:
        return {
            "exists": True,
            "path": str(path),
            "mode": None,
            "strict": False,
            "warning": "cannot read credential file permission: " + str(e)
        }

def load_okx_credentials(mask=False):
    data = {}

    file_data = _json_read(CREDENTIAL_FILE, {})
    if isinstance(file_data, dict):
        data.update(file_data)

    env_map = {
        "api_key": "OKX_API_KEY",
        "secret_key": "OKX_SECRET_KEY",
        "passphrase": "OKX_PASSPHRASE",
        "base_url": "OKX_BASE_URL",
        "simulated_trading": "OKX_SIMULATED_TRADING",
        "private_read_enabled": "OKX_PRIVATE_READ_ENABLED"
    }

    for key, env_name in env_map.items():
        val = os.environ.get(env_name)
        if val not in [None, ""]:
            data[key] = val

    if not data.get("base_url"):
        data["base_url"] = DEFAULT_BASE_URL

    sim = data.get("simulated_trading", data.get("x_simulated_trading", False))
    if isinstance(sim, str):
        sim = sim.strip().lower() in ["1", "true", "yes", "y", "demo", "simulated"]
    data["simulated_trading"] = bool(sim)

    private_enabled = data.get("private_read_enabled", False)
    if isinstance(private_enabled, str):
        private_enabled = private_enabled.strip().lower() in ["1", "true", "yes", "y", "on"]
    data["private_read_enabled"] = bool(private_enabled)

    if mask:
        masked = dict(data)
        for key in ["api_key", "secret_key", "passphrase"]:
            masked[key] = _mask(masked.get(key))
        masked["credential_file"] = str(CREDENTIAL_FILE)
        masked["credential_file_permission"] = get_okx_credential_file_permission_status()
        masked["env_supported"] = [
            "OKX_API_KEY",
            "OKX_SECRET_KEY",
            "OKX_PASSPHRASE",
            "OKX_SIMULATED_TRADING",
            "OKX_BASE_URL",
            "OKX_PRIVATE_READ_ENABLED"
        ]
        return masked

    return data

def okx_private_read_enabled():
    creds = load_okx_credentials(mask=False)
    return bool(creds.get("private_read_enabled"))

def _require_private_read_enabled():
    if not okx_private_read_enabled():
        raise ValueError("OKX private read is disabled; set OKX_PRIVATE_READ_ENABLED=true or private_read_enabled=true in credential file")

def get_okx_credentials_status():
    raw = load_okx_credentials(mask=False)
    masked = load_okx_credentials(mask=True)

    required = ["api_key", "secret_key", "passphrase"]
    missing = [k for k in required if not raw.get(k)]

    perm = get_okx_credential_file_permission_status()

    warnings = []
    if perm.get("exists") and not perm.get("strict"):
        warnings.append(perm.get("warning") or "credential file permission is not strict")

    return {
        "ok": len(missing) == 0 and (not perm.get("exists") or perm.get("strict")),
        "credentials_present": len(missing) == 0,
        "private_read_enabled": okx_private_read_enabled(),
        "missing": missing,
        "masked": masked,
        "credential_file_permission": perm,
        "warnings": warnings,
        "base_url": raw.get("base_url") or DEFAULT_BASE_URL,
        "simulated_trading": bool(raw.get("simulated_trading")),
        "time": _now_text()
    }

def validate_okx_credentials(check_network=False):
    status = get_okx_credentials_status()

    if not status.get("credentials_present"):
        status["network_checked"] = False
        status["private_ready"] = False
        status["message"] = "OKX credentials missing"
        return status

    if status.get("credential_file_permission", {}).get("exists") and not status.get("credential_file_permission", {}).get("strict"):
        status["network_checked"] = False
        status["private_ready"] = False
        status["message"] = "OKX credential file permission is not strict"
        return status

    if not okx_private_read_enabled():
        status["network_checked"] = False
        status["private_ready"] = False
        status["message"] = "OKX private read is disabled"
        return status

    if not check_network:
        status["network_checked"] = False
        status["private_ready"] = None
        status["message"] = "OKX credentials are present; network check not requested"
        return status

    try:
        cfg = get_okx_account_config()
        status["network_checked"] = True
        status["private_ready"] = _okx_response_ok(cfg)
        status["account_config"] = _summarize_okx_response(cfg)
        status["message"] = "OKX private API validation completed"
    except Exception as e:
        status["network_checked"] = True
        status["private_ready"] = False
        status["error"] = str(e)
        status["message"] = "OKX private API validation failed"

    _write_status_cache({"credentials": status})
    return status

def _sign(timestamp, method, request_path, body, secret_key):
    if body is None:
        body = ""
    prehash = str(timestamp) + str(method).upper() + str(request_path) + str(body)
    digest = hmac.new(str(secret_key).encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")

def _query(params):
    if not params:
        return ""
    clean = []
    # Do not sort. Keep caller insertion order so the actual requestPath and signed requestPath remain identical.
    for k, v in params.items():
        if v is None:
            continue
        clean.append((k, str(v)))
    return urllib.parse.urlencode(clean)

def _okx_request(method, path, params=None, body=None, auth=False, timeout=10):
    method = str(method).upper()
    creds = load_okx_credentials(mask=False)
    base_url = (creds.get("base_url") or DEFAULT_BASE_URL).rstrip("/")

    query = _query(params)
    request_path = path + (("?" + query) if query else "")
    url = base_url + request_path

    body_text = ""
    data = None

    if body is not None:
        body_text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        data = body_text.encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 AutoTradeStage8/8.12c Python urllib OKXAdapter",
        "Accept": "application/json"
    }

    if creds.get("simulated_trading"):
        headers["x-simulated-trading"] = "1"

    if auth:
        _require_private_read_enabled()

        missing = [k for k in ["api_key", "secret_key", "passphrase"] if not creds.get(k)]
        if missing:
            raise ValueError("OKX credentials missing: " + ",".join(missing))

        perm = get_okx_credential_file_permission_status()
        if perm.get("exists") and not perm.get("strict"):
            raise ValueError("OKX credential file permission is not strict: " + str(perm.get("mode")))

        ts = _utc_timestamp()
        headers["OK-ACCESS-KEY"] = creds.get("api_key")
        headers["OK-ACCESS-SIGN"] = _sign(ts, method, request_path, body_text, creds.get("secret_key"))
        headers["OK-ACCESS-TIMESTAMP"] = ts
        headers["OK-ACCESS-PASSPHRASE"] = creds.get("passphrase")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
            parsed = json.loads(text) if text else {}
            if isinstance(parsed, dict):
                parsed["_http_status"] = resp.getcode()
                parsed["_request_path"] = request_path
            return parsed

    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(text) if text else {}
        except Exception:
            parsed = {"raw": text}
        if isinstance(parsed, dict):
            parsed["_http_status"] = e.code
            parsed["_request_path"] = request_path
        raise RuntimeError("OKX HTTP error " + str(e.code) + ": " + json.dumps(parsed, ensure_ascii=False)[:800])

    except Exception as e:
        raise RuntimeError("OKX request failed: " + str(e))

def _okx_response_ok(data):
    return isinstance(data, dict) and str(data.get("code")) == "0"

def _summarize_okx_response(data, max_items=3):
    if not isinstance(data, dict):
        return {"ok": False, "type": str(type(data))}

    out = {
        "code": data.get("code"),
        "msg": data.get("msg"),
        "http_status": data.get("_http_status"),
        "request_path": data.get("_request_path")
    }

    d = data.get("data")
    if isinstance(d, list):
        out["data_count"] = len(d)
        out["data_sample"] = d[:max_items]
    elif isinstance(d, dict):
        out["data_keys"] = sorted(list(d.keys()))[:30]
    else:
        out["data_type"] = str(type(d))

    return out

def _write_status_cache(fragment):
    cache = _json_read(STATUS_CACHE_FILE, {})
    if not isinstance(cache, dict):
        cache = {}
    cache.update(fragment)
    cache["updated_at"] = _now_text()
    _json_write(STATUS_CACHE_FILE, cache)
    return cache

def get_okx_account_config():
    return _okx_request("GET", "/api/v5/account/config", auth=True, timeout=PRIVATE_TIMEOUT)

def get_okx_account_balance(ccy="USDT"):
    params = {}
    if ccy:
        params["ccy"] = ccy
    return _okx_request("GET", "/api/v5/account/balance", params=params, auth=True, timeout=PRIVATE_TIMEOUT)

def get_okx_positions(symbol=DEFAULT_SYMBOL):
    symbol = normalize_okx_symbol(symbol)
    params = {"instType": "SWAP", "instId": symbol}
    return _okx_request("GET", "/api/v5/account/positions", params=params, auth=True, timeout=PRIVATE_TIMEOUT)

def get_okx_instrument_info(symbol=DEFAULT_SYMBOL):
    symbol = normalize_okx_symbol(symbol)
    params = {"instType": "SWAP", "instId": symbol}
    return _okx_request("GET", "/api/v5/public/instruments", params=params, auth=False, timeout=PUBLIC_TIMEOUT)

def get_okx_ticker(symbol=DEFAULT_SYMBOL):
    symbol = normalize_okx_symbol(symbol)
    params = {"instId": symbol}
    return _okx_request("GET", "/api/v5/market/ticker", params=params, auth=False, timeout=PUBLIC_TIMEOUT)

def get_okx_leverage(symbol=DEFAULT_SYMBOL, mgn_mode=DEFAULT_TD_MODE):
    symbol = normalize_okx_symbol(symbol)
    params = {"instId": symbol, "mgnMode": mgn_mode}
    return _okx_request("GET", "/api/v5/account/leverage-info", params=params, auth=True, timeout=PRIVATE_TIMEOUT)

def set_okx_leverage_if_needed(symbol=DEFAULT_SYMBOL, leverage=20, mgn_mode=DEFAULT_TD_MODE, pos_side=None, dry_run=True):
    # Stage 8.12b intentionally does not modify account leverage.
    # Real set-leverage belongs to 8.13/8.14 guarded execution/live-arm stages.
    symbol = normalize_okx_symbol(symbol)

    try:
        leverage = int(leverage)
    except Exception:
        raise ValueError("leverage must be int")

    if leverage <= 0 or leverage > 125:
        raise ValueError("leverage out of allowed adapter range: " + str(leverage))

    result = {
        "ok": True,
        "symbol": symbol,
        "target_leverage": leverage,
        "mgnMode": mgn_mode,
        "posSide": pos_side,
        "dry_run": True,
        "would_change": None,
        "changed": False,
        "message": "stage 8.12b is read-only; no set-leverage request is sent"
    }

    if okx_private_read_enabled():
        try:
            info = get_okx_leverage(symbol=symbol, mgn_mode=mgn_mode)
            result["current"] = _summarize_okx_response(info)
            if _okx_response_ok(info):
                data = info.get("data") or []
                if data:
                    lever = data[0].get("lever")
                    result["current_leverage"] = lever
                    result["would_change"] = str(lever) != str(leverage)
        except Exception as e:
            result["current_error"] = str(e)
            result["would_change"] = True
    else:
        result["current_error"] = "private read disabled"

    return result

def _first_data(resp):
    if isinstance(resp, dict) and isinstance(resp.get("data"), list) and resp.get("data"):
        return resp.get("data")[0]
    return None

def _safe_float(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default

def _safe_decimal_str(v):
    f = _safe_float(v)
    if f is None or f <= 0:
        raise ValueError("numeric value must be positive")
    text = ("%.12f" % f).rstrip("0").rstrip(".")
    return text if text else "0"

def _round_down_to_step(value, step):
    value = float(value)
    step = float(step)
    if step <= 0:
        return value
    return math.floor(value / step) * step

def _infer_contract_size_from_notional(symbol, notional_usdt, price=None):
    symbol = normalize_okx_symbol(symbol)

    notional = _safe_float(notional_usdt)
    if notional is None or notional <= 0:
        raise ValueError("notional_usdt must be positive")

    if price is None:
        ticker = get_okx_ticker(symbol)
        first = _first_data(ticker)
        if not first:
            raise ValueError("cannot read ticker price for notional conversion")
        price = first.get("last") or first.get("askPx") or first.get("bidPx")

    price_f = _safe_float(price)
    if price_f is None or price_f <= 0:
        raise ValueError("price must be positive for notional conversion")

    inst = get_okx_instrument_info(symbol)
    first = _first_data(inst)
    if not first:
        raise ValueError("cannot read instrument info for size conversion")

    ct_val = _safe_float(first.get("ctVal"))
    lot_sz = _safe_float(first.get("lotSz"), 1.0)
    min_sz = _safe_float(first.get("minSz"), lot_sz)
    ct_val_ccy = str(first.get("ctValCcy") or "").upper()

    if ct_val is None or ct_val <= 0:
        raise ValueError("instrument ctVal invalid")

    if ct_val_ccy == "BTC":
        contracts = notional / (price_f * ct_val)
    elif ct_val_ccy == "USDT":
        contracts = notional / ct_val
    else:
        raise ValueError("unsupported ctValCcy for automatic size conversion: " + ct_val_ccy)

    contracts = _round_down_to_step(contracts, lot_sz)
    if contracts < min_sz:
        contracts = min_sz

    return _safe_decimal_str(contracts)

def build_okx_order_payload(symbol=DEFAULT_SYMBOL, side=None, local_side=None, pos_side=None, td_mode=DEFAULT_TD_MODE, ord_type="market", sz=None, notional_usdt=None, price=None, reduce_only=False, client_order_id=None):
    symbol = normalize_okx_symbol(symbol)

    if local_side and not side:
        ls = str(local_side).lower()
        if ls == "long":
            side = "buy"
            pos_side = pos_side or "long"
        elif ls == "short":
            side = "sell"
            pos_side = pos_side or "short"
        else:
            raise ValueError("local_side must be long or short")

    if not side:
        raise ValueError("side is required")

    side = str(side).lower()
    if side not in ["buy", "sell"]:
        raise ValueError("side must be buy or sell")

    if not pos_side:
        pos_side = "long" if side == "buy" else "short"

    if pos_side not in ["long", "short", "net"]:
        raise ValueError("pos_side must be long, short, or net")

    if not sz:
        if notional_usdt is None:
            raise ValueError("either sz or notional_usdt is required")
        sz = _infer_contract_size_from_notional(symbol, notional_usdt, price=price)

    payload = {
        "instId": symbol,
        "tdMode": td_mode,
        "side": side,
        "posSide": pos_side,
        "ordType": ord_type,
        "sz": _safe_decimal_str(sz)
    }

    if reduce_only:
        payload["reduceOnly"] = "true"

    if client_order_id:
        payload["clOrdId"] = str(client_order_id)

    validation = validate_okx_order_payload(payload)

    return {
        "ok": validation.get("ok"),
        "payload": payload,
        "validation": validation,
        "network_required": bool(notional_usdt is not None and sz is None),
        "note": "payload constructed only; no order submitted"
    }

def validate_okx_order_payload(payload, instrument=None):
    errors = []

    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["payload must be dict"]}

    required = ["instId", "tdMode", "side", "ordType", "sz"]
    for k in required:
        if payload.get(k) in [None, ""]:
            errors.append("missing " + k)

    inst = normalize_okx_symbol(payload.get("instId")) if payload.get("instId") else None
    if inst and not inst.endswith("-SWAP"):
        errors.append("instId must be SWAP instrument for this adapter")

    if payload.get("side") not in ["buy", "sell"]:
        errors.append("side must be buy/sell")

    if payload.get("tdMode") not in ["cross", "isolated"]:
        errors.append("tdMode must be cross/isolated")

    if payload.get("ordType") not in ["market", "limit"]:
        errors.append("ordType must be market/limit in stage8_12 adapter")

    if payload.get("posSide") and payload.get("posSide") not in ["long", "short", "net"]:
        errors.append("posSide must be long/short/net")

    sz = _safe_float(payload.get("sz"))
    if sz is None or sz <= 0:
        errors.append("sz must be positive")

    if instrument is not None:
        first = instrument
        if isinstance(instrument, dict) and isinstance(instrument.get("data"), list):
            first = _first_data(instrument)

        if isinstance(first, dict):
            min_sz = _safe_float(first.get("minSz"))
            lot_sz = _safe_float(first.get("lotSz"))
            if min_sz is not None and sz is not None and sz < min_sz:
                errors.append("sz below minSz " + str(min_sz))
            if lot_sz is not None and lot_sz > 0 and sz is not None:
                diff = abs((sz / lot_sz) - round(sz / lot_sz))
                if diff > 1e-8:
                    errors.append("sz not aligned with lotSz " + str(lot_sz))

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "payload": dict(payload),
        "time": _now_text()
    }

def get_okx_api_status(check_network=False, symbol=DEFAULT_SYMBOL):
    symbol = normalize_okx_symbol(symbol)

    creds = get_okx_credentials_status()
    cache = _json_read(STATUS_CACHE_FILE, {})

    status = {
        "ok": True,
        "adapter_installed": True,
        "okx_api_ready": False,
        "credentials_present": creds.get("credentials_present"),
        "private_read_enabled": creds.get("private_read_enabled"),
        "credential_file_permission": creds.get("credential_file_permission"),
        "credentials": creds,
        "symbol": symbol,
        "network_checked": bool(check_network),
        "cached_status_available": bool(cache),
        "cache_not_used_for_ready": True,
        "public_ready": None,
        "private_ready": None,
        "instrument_valid": None,
        "ticker_readable": None,
        "balance_readable": None,
        "positions_readable": None,
        "time": _now_text()
    }

    # Never use cached okx_api_ready as live-ready evidence.
    if not check_network:
        status["message"] = "network check not requested; okx_api_ready remains false by design"
        return status

    public_ok = True
    private_ok = True

    try:
        inst = get_okx_instrument_info(symbol)
        status["instrument"] = _summarize_okx_response(inst)
        status["instrument_valid"] = _okx_response_ok(inst) and bool(_first_data(inst))
        public_ok = public_ok and bool(status["instrument_valid"])
    except Exception as e:
        status["instrument_error"] = str(e)
        status["instrument_valid"] = False
        public_ok = False

    try:
        ticker = get_okx_ticker(symbol)
        status["ticker"] = _summarize_okx_response(ticker)
        status["ticker_readable"] = _okx_response_ok(ticker) and bool(_first_data(ticker))
        public_ok = public_ok and bool(status["ticker_readable"])
    except Exception as e:
        status["ticker_error"] = str(e)
        status["ticker_readable"] = False
        public_ok = False

    if creds.get("credentials_present") and creds.get("private_read_enabled"):
        try:
            bal = get_okx_account_balance("USDT")
            status["balance"] = _summarize_okx_response(bal)
            status["balance_readable"] = _okx_response_ok(bal)
            private_ok = private_ok and bool(status["balance_readable"])
        except Exception as e:
            status["balance_error"] = str(e)
            status["balance_readable"] = False
            private_ok = False

        try:
            pos = get_okx_positions(symbol)
            status["positions"] = _summarize_okx_response(pos)
            status["positions_readable"] = _okx_response_ok(pos)
            private_ok = private_ok and bool(status["positions_readable"])
        except Exception as e:
            status["positions_error"] = str(e)
            status["positions_readable"] = False
            private_ok = False

        try:
            lev = get_okx_leverage(symbol)
            status["leverage"] = _summarize_okx_response(lev)
            status["leverage_readable"] = _okx_response_ok(lev)
            private_ok = private_ok and bool(status["leverage_readable"])
        except Exception as e:
            status["leverage_error"] = str(e)
            status["leverage_readable"] = False
            private_ok = False

    else:
        status["private_ready"] = False
        private_ok = False
        if not creds.get("credentials_present"):
            status["private_blocker"] = "credentials missing"
        elif not creds.get("private_read_enabled"):
            status["private_blocker"] = "OKX private read disabled"

    status["public_ready"] = bool(public_ok)
    status["private_ready"] = bool(private_ok)
    status["okx_api_ready"] = bool(public_ok and private_ok and creds.get("credentials_present") and creds.get("private_read_enabled"))

    _write_status_cache(status)
    return status

def create_credentials_template(path=None):
    path = Path(path or CREDENTIAL_FILE)
    if path.exists():
        try:
            os.chmod(str(path), 0o600)
        except Exception:
            pass
        return {
            "ok": True,
            "exists": True,
            "path": str(path),
            "permission": get_okx_credential_file_permission_status(),
            "message": "credential template already exists; permission normalized if possible"
        }

    data = {
        "api_key": "",
        "secret_key": "",
        "passphrase": "",
        "simulated_trading": True,
        "private_read_enabled": False,
        "base_url": DEFAULT_BASE_URL
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(str(path), 0o600)
    except Exception:
        pass

    return {
        "ok": True,
        "exists": True,
        "path": str(path),
        "permission": get_okx_credential_file_permission_status(),
        "message": "credential template created; fill it on server only, never paste secrets into chat"
    }


# AUTO_TRADE_OKX_STAGE8_14C_ORDER_START

OKX_STAGE8_INTERNAL_ORDER_TOKEN = "AUTO_TRADE_EXECUTION_STAGE8_14C"

def _okx_stage8_env_enabled():
    return os.environ.get("OKX_STAGE8_LIVE_ONE_ORDER_ENABLED", "").strip().lower() in ["1", "true", "yes", "y", "on"]

def place_okx_order(payload, internal_confirm=None):
    """
    Stage 8.14c guarded single-order submit wrapper.

    This function intentionally has its own second-layer guard:
    1. OKX_STAGE8_LIVE_ONE_ORDER_ENABLED must be true.
    2. Caller must pass the internal execution token.
    3. Caller-side manual confirmation / single-use / sync / circuit breaker
       must still happen in auto_trade_execution.py.
    """
    if not _okx_stage8_env_enabled():
        raise ValueError("OKX live order submit env gate is not enabled")

    if internal_confirm != OKX_STAGE8_INTERNAL_ORDER_TOKEN:
        raise ValueError("OKX live order internal confirm token mismatch")

    if not isinstance(payload, dict):
        raise ValueError("order payload must be dict")

    return _okx_request("POST", "/api/v5/trade/order", body=payload, auth=True, timeout=10)

def get_okx_order(symbol=DEFAULT_SYMBOL, ord_id=None, cl_ord_id=None):
    symbol = normalize_okx_symbol(symbol)
    params = {"instId": symbol}

    if ord_id:
        params["ordId"] = str(ord_id)

    if cl_ord_id:
        params["clOrdId"] = str(cl_ord_id)

    if not ord_id and not cl_ord_id:
        raise ValueError("ord_id or cl_ord_id required")

    return _okx_request("GET", "/api/v5/trade/order", params=params, auth=True, timeout=10)

# AUTO_TRADE_OKX_STAGE8_14C_ORDER_END

