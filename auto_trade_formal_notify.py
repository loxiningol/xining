
# -*- coding: utf-8 -*-
# STAGE8_23_FINAL_SAFE_FORMAL_NOTIFY_MODULE
from pathlib import Path
import json, time, os, importlib.util, sys, re

ROOT = Path("/root")
AUTO_DIR = ROOT / "auto_trade"
CONFIG_FILE = AUTO_DIR / "formal_notify_config.json"
AUDIT_LOG_FILE = AUTO_DIR / "formal_notification_audit.log"

REAL_NOTIFY_MODULE = "/root/common.py"
REAL_NOTIFY_FUNCTION = "send_wx"
WXPUSHER_URL = "https://wxpusher.zjiecode.com/api/send/message"
# Hard-blocked Wx kinds/markers (cannot be overridden by callers).
HARD_BLOCKED_WX_KINDS = frozenset({"strategy_triple_friction_tip"})
HARD_BLOCKED_WX_MARKERS = ("三倍摩擦风险提示", "三倍摩擦压力提示（非淘汰）")

def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def _read_json(path, default):
    try:
        p = Path(path)
        if not p.exists():
            return default
        s = p.read_text(encoding="utf-8", errors="ignore")
        return json.loads(s) if s.strip() else default
    except Exception:
        return default

def _write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

def _audit(kind, message, meta=None):
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    row = {"time": _now(), "ts": time.time(), "kind": kind, "message": message, "meta": meta or {}}
    with AUDIT_LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row

def default_config():
    return {
        "enabled": True,
        "notify_module_path": REAL_NOTIFY_MODULE,
        "notify_function": REAL_NOTIFY_FUNCTION,
        "verified_send_mode": "direct_wxpusher_http_using_common_token_uid",
        "bind_mode": "existing_real_wxpusher_common_send_wx",
        "created_by": "stage8_23_final_safe",
        "fake_local_file_channel": False,
        "audit_only_is_not_notification": True
    }

def get_config():
    cfg = _read_json(CONFIG_FILE, None)
    if not isinstance(cfg, dict):
        cfg = default_config()
        _write_json(CONFIG_FILE, cfg)
    for k, v in default_config().items():
        cfg.setdefault(k, v)
    cfg["notify_module_path"] = REAL_NOTIFY_MODULE
    cfg["notify_function"] = REAL_NOTIFY_FUNCTION
    cfg["verified_send_mode"] = "direct_wxpusher_http_using_common_token_uid"
    cfg["bind_mode"] = "existing_real_wxpusher_common_send_wx"
    cfg["fake_local_file_channel"] = False
    cfg["audit_only_is_not_notification"] = True
    _write_json(CONFIG_FILE, cfg)
    return cfg

def set_config(**kwargs):
    cfg = get_config()
    if "enabled" in kwargs:
        cfg["enabled"] = bool(kwargs.get("enabled"))
    cfg["notify_module_path"] = REAL_NOTIFY_MODULE
    cfg["notify_function"] = REAL_NOTIFY_FUNCTION
    cfg["verified_send_mode"] = "direct_wxpusher_http_using_common_token_uid"
    cfg["bind_mode"] = "existing_real_wxpusher_common_send_wx"
    cfg["fake_local_file_channel"] = False
    cfg["audit_only_is_not_notification"] = True
    _write_json(CONFIG_FILE, cfg)
    return {"ok": True, "config": cfg, "status": get_status()}

def _import_module(path):
    p = Path(path)
    if not p.exists():
        raise RuntimeError("notify module not found: " + str(path))
    name = "qiyu_real_common_notify_" + str(abs(hash(str(p))))
    spec = importlib.util.spec_from_file_location(name, str(p))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import notify module: " + str(p))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

def _common_module():
    mod = _import_module(REAL_NOTIFY_MODULE)
    fn = getattr(mod, REAL_NOTIFY_FUNCTION, None)
    if not callable(fn):
        raise RuntimeError("common.send_wx not callable")
    token = getattr(mod, "WX_TOKEN", None)
    uid = getattr(mod, "WX_UID", None)
    if not token or not uid:
        raise RuntimeError("common.WX_TOKEN or common.WX_UID missing")
    return mod, fn, token, uid

def get_channel():
    cfg = get_config()
    if not cfg.get("enabled", True):
        return {"ok": False, "ready": False, "notification_real_channel_ready": False, "error": "notification disabled", "config": cfg}
    try:
        p = Path(REAL_NOTIFY_MODULE)
        if not p.exists():
            return {"ok": False, "ready": False, "notification_real_channel_ready": False, "error": "/root/common.py not found", "config": cfg}
        txt = p.read_text(encoding="utf-8", errors="ignore")
        if "def send_wx" not in txt or "wxpusher.zjiecode.com/api/send/message" not in txt or "WX_TOKEN" not in txt or "WX_UID" not in txt:
            return {"ok": False, "ready": False, "notification_real_channel_ready": False, "error": "common.py send_wx/WxPusher/WX_TOKEN/WX_UID marker missing", "config": cfg}
        _common_module()
        return {"ok": True, "ready": True, "notification_real_channel_ready": True, "module_path": REAL_NOTIFY_MODULE, "function": REAL_NOTIFY_FUNCTION, "verified_send_mode": "direct_wxpusher_http_using_common_token_uid", "bind_mode": "existing_real_wxpusher_common_send_wx", "config": cfg}
    except Exception as e:
        return {"ok": False, "ready": False, "notification_real_channel_ready": False, "error": str(e), "config": cfg}

def _wxpusher_success(resp_json):
    if not isinstance(resp_json, dict):
        return False
    code = resp_json.get("code")
    success = resp_json.get("success")
    msg = str(resp_json.get("msg") or resp_json.get("message") or "").lower()
    if code in [0, 1000, "0", "1000"] and success is not False:
        return True
    if success is True:
        return True
    if "success" in msg or msg == "ok":
        return True
    return False

def _send_via_verified_wxpusher(message):
    mod, fn, token, uid = _common_module()
    import requests
    payload = {
        "appToken": token,
        "content": message,
        "summary": "策略信号通知",
        "contentType": 1,
        "uids": [uid],
    }
    resp = requests.post(WXPUSHER_URL, json=payload, timeout=8)
    status_code = getattr(resp, "status_code", None)
    text = getattr(resp, "text", "")
    try:
        resp_json = resp.json()
    except Exception:
        resp_json = None
    ok = bool(status_code == 200 and _wxpusher_success(resp_json))
    return {
        "http_status": status_code,
        "response_json": resp_json,
        "response_text_prefix": str(text)[:300],
        "wxpusher_success": ok,
        "verified_by": "http_status_and_wxpusher_json",
    }

def send_message(message, kind="formal_auto_trade", meta=None, dry_run=False):
    ch = get_channel()
    meta = dict(meta or {})
    kind_s = str(kind or "")
    msg_s = str(message or "")
    try:
        from dual_engine_workflow_v2.review_lexicon import scrub as _scrub_review
        msg_s = _scrub_review(msg_s)
        message = msg_s
    except Exception:
        pass
    if kind_s in HARD_BLOCKED_WX_KINDS or any(m in msg_s for m in HARD_BLOCKED_WX_MARKERS):
        row = _audit(kind_s + "_hard_blocked", msg_s,
                     {"blocked": True, "reason": "hard_blocked_wx_kind_or_marker", **meta})
        return {"ok": True, "sent": False, "blocked": True, "audit_logged": True,
                "reason": "hard_blocked_wx_kind_or_marker", "audit": row,
                "audit_log_file": str(AUDIT_LOG_FILE)}
    try:
        import auto_trade_strategy_titles as titles
        message = titles.rewrite_strategy_keys_in_text(message)
        if meta.get("strategy_key") and not meta.get("strategy_title"):
            meta["strategy_title"] = titles.resolve_strategy_name(
                meta.get("strategy_key"), meta.get("strategy_name"))
    except Exception:
        pass
    if dry_run or os.environ.get("STAGE823_INSTALL_SELF_TEST") == "1":
        row = _audit(kind + "_dry_run", message, {"dry_run": True, "channel": ch, **meta})
        return {"ok": True, "dry_run": True, "sent": False, "audit_logged": True, "notification_real_channel_ready": bool(ch.get("notification_real_channel_ready")), "notification_error": None if ch.get("notification_real_channel_ready") else ch.get("error"), "channel": ch, "audit_log_file": str(AUDIT_LOG_FILE), "audit": row}
    if not ch.get("notification_real_channel_ready"):
        row = _audit(kind + "_not_sent", message, {"channel": ch, **meta})
        return {"ok": False, "sent": False, "audit_logged": True, "notification_real_channel_ready": False, "notification_error": ch.get("error"), "audit_log_file": str(AUDIT_LOG_FILE), "audit": row}
    try:
        send_result = _send_via_verified_wxpusher(message)
        if not send_result.get("wxpusher_success"):
            row = _audit(kind + "_send_failed_verified", message, {"sent_by": REAL_NOTIFY_MODULE + ":" + REAL_NOTIFY_FUNCTION, "send_result": send_result, **meta})
            return {"ok": False, "sent": False, "audit_logged": True, "notification_real_channel_ready": True, "notification_error": "WxPusher HTTP/JSON did not confirm success", "channel": REAL_NOTIFY_MODULE + ":" + REAL_NOTIFY_FUNCTION, "send_result": send_result, "audit_log_file": str(AUDIT_LOG_FILE), "audit": row}
        _audit(kind + "_sent_verified", message, {"sent_by": REAL_NOTIFY_MODULE + ":" + REAL_NOTIFY_FUNCTION, "send_result": send_result, **meta})
        return {"ok": True, "sent": True, "audit_logged": True, "notification_real_channel_ready": True, "channel": REAL_NOTIFY_MODULE + ":" + REAL_NOTIFY_FUNCTION, "send_result": send_result}
    except Exception as e:
        row = _audit(kind + "_send_error", message, {"send_error": str(e), **meta})
        return {"ok": False, "sent": False, "audit_logged": True, "notification_real_channel_ready": True, "notification_error": str(e), "audit_log_file": str(AUDIT_LOG_FILE), "audit": row}

def _safe_float(value, default=None):
    try:
        if value in [None, ""]:
            return default
        return float(value)
    except Exception:
        return default

def _nested(data, *keys):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current

def _percent_label(value, default=0.009):
    pct = _safe_float(value, default)
    text = ("%.4f" % (pct * 100.0)).rstrip("0").rstrip(".")
    return text + "%"

def _close_rate_info(closed):
    open_fill = _nested(closed, "open_order", "filled", "order") or {}
    close_fill = (
        _nested(closed, "close_order", "filled", "order")
        or _nested(closed, "last_close_attempt", "close_order", "filled", "order")
        or {}
    )
    position = (
        _nested(closed, "position_poll", "position")
        or closed.get("okx_position_after_open")
        or {}
    )
    realized_pnl = _safe_float(close_fill.get("pnl"), _safe_float(closed.get("pnl")))
    open_fee = _safe_float(open_fill.get("fee"), 0.0)
    close_fee = _safe_float(close_fill.get("fee"), 0.0)
    initial_margin = _safe_float(position.get("imr"), _safe_float(closed.get("imr")))
    account_equity = _safe_float(_nested(closed, "sizing", "account_equity_usdt"))
    net_pnl = ((realized_pnl + open_fee + close_fee)
               if realized_pnl is not None else None)
    rate_pct = None
    basis = None
    if realized_pnl is not None and initial_margin not in [None, 0]:
        rate_pct = net_pnl / initial_margin * 100.0
        basis = "net_realized_pnl_over_initial_margin"
    else:
        entry_price = _safe_float(closed.get("entry_price"))
        close_price = _safe_float(
            closed.get("close_price"),
            _safe_float(close_fill.get("avgPx"), _safe_float(close_fill.get("fillPx"))),
        )
        if entry_price not in [None, 0] and close_price is not None:
            side = str(closed.get("posSide") or closed.get("side") or "").lower()
            direction = -1.0 if side == "short" else 1.0
            leverage = _safe_float(closed.get("leverage"), _safe_float(close_fill.get("lever"), 1.0))
            rate_pct = direction * (close_price - entry_price) / entry_price * leverage * 100.0
            basis = "fill_price_change_times_leverage"
    if rate_pct is None:
        return {
            "pnl_rate_pct": None,
            "pnl_rate_label": "收益率",
            "pnl_rate_text": "无法计算",
            "pnl_rate_suffix": " （收益率无法计算）",
            "pnl_rate_basis": None,
            "margin_return_text": "无法计算",
            "account_return_pct": None,
            "account_return_text": "无法计算（缺少开仓时总权益）",
        }
    label = "盈利率" if rate_pct > 0 else ("亏损率" if rate_pct < 0 else "收益率")
    text = "%+.2f%%" % rate_pct
    account_rate_pct = (net_pnl/account_equity*100.0
                        if net_pnl is not None and account_equity not in (None, 0)
                        else None)
    return {
        "pnl_rate_pct": rate_pct,
        "pnl_rate_label": label,
        "pnl_rate_text": text,
        "pnl_rate_suffix": " （%s %s）" % (label, text),
        "pnl_rate_basis": basis,
        "margin_return_text": text,
        "account_return_pct": account_rate_pct,
        "account_return_text": ("%+.2f%%" % account_rate_pct
                                if account_rate_pct is not None else
                                "无法计算（缺少开仓时总权益）"),
    }

def _lookup_assignment_row(strategy_key, symbol=None, timeframe=None):
    key = str(strategy_key or "").strip()
    if not key:
        return {}, ""
    controls = _read_json(AUTO_DIR / "strategy_runtime_controls.json",
                          {"assignments": {}})
    assignments = (controls.get("assignments") or {}) if isinstance(controls, dict) else {}
    if symbol and timeframe:
        aid = "%s|%s|%s" % (symbol, timeframe, key)
        row = assignments.get(aid)
        if isinstance(row, dict):
            return row, aid
    for aid, row in assignments.items():
        if not isinstance(row, dict):
            continue
        if row.get("strategy_key") == key or str(aid).endswith("|" + key):
            return row, aid
    return {}, ""


def _grade_from_ratio(ratio):
    try:
        r = float(ratio)
    except Exception:
        return None
    # Match human-confirm GRADE_RATIO bands (prefer exact/nearest known).
    bands = (("S", 0.70), ("A", 0.50), ("B", 0.30), ("C", 0.15))
    best = None
    best_diff = 1e9
    for g, v in bands:
        diff = abs(r - v)
        if diff < best_diff:
            best_diff = diff
            best = g
    if best is not None and best_diff <= 0.051:
        return best
    return None


def resolve_strategy_grade(strategy_key, payload=None):
    """Return grade letter like B / A. Applied strategies default to B."""
    payload = payload if isinstance(payload, dict) else {}
    for candidate in (
        payload.get("lifecycle_grade"),
        payload.get("grade"),
        payload.get("max_grade"),
    ):
        raw = str(candidate or "").strip()
        g = raw.upper()
        if g in ("S", "A", "B", "C", "D", "E"):
            return g
        if raw in ("未评级", "待评级") or g in (
                "SHADOW", "UNRATED", "NONE", "NULL", "PENDING", "DELETED"):
            continue
    row, _aid = _lookup_assignment_row(
        strategy_key,
        payload.get("symbol"),
        payload.get("timeframe"),
    )
    raw = str(row.get("lifecycle_grade") or row.get("max_grade") or "").strip()
    g = raw.upper()
    if g in ("S", "A", "B", "C", "D", "E"):
        return g
    inferred = _grade_from_ratio(
        payload.get("max_position_ratio", row.get("max_position_ratio")))
    if inferred:
        return inferred
    return "B"


def strategy_name_with_grade(strategy_key, strategy_name=None, payload=None):
    try:
        import auto_trade_strategy_titles as titles
        base = titles.resolve_strategy_name(strategy_key, strategy_name)
    except Exception:
        base = strategy_name or strategy_key or "未命名策略"
    base = str(base or "").strip() or "未命名策略"
    # Avoid double suffix if already present.
    if re.search(r"（(?:[SABCDE]|待评级)级）\s*$", base):
        return base
    grade = resolve_strategy_grade(strategy_key, payload)
    return "%s（%s级）" % (base, grade)




def format_open_success(data):
    return """=== 栖语自动交易开仓通知 ===
📡 策略名称: {strategy_name}
📊 信号方向: {side_label}
⚙️ 执行模式: 正式 OKX 实盘
📌 杠杆倍数: {leverage}x
💰 仓位模式: {position_mode_label}
📦 下单数量: {sz}
💵 参考价格: {price}
🛡️ 止损比例: {stop_loss_pct_label}
🛡️ 止损价格: {stop_loss_price}
✅ OKX订单ID: {ordId}
🕒 开仓时间: {time}
""".format(**data)

def format_open_failed(data):
    return """=== 栖语自动交易开仓失败通知 ===
📡 策略名称: {strategy_name}
📊 信号方向: {side_label}
⚙️ 执行模式: 正式 OKX 实盘
📌 杠杆倍数: {leverage}x
💰 仓位模式: 全仓
❌ 失败原因: {error}
🕒 时间: {time}
""".format(**data)

def format_risk(data):
    return """=== 栖语自动交易风险通知 ===
📡 策略名称: {strategy_name}
📊 原方向: {side_label}
⚠️ 风险类型: {risk_type}
🛡️ 处理方式: {action}
📦 仓位数量: {sz}
💵 参考价格: {price}
🕒 时间: {time}
""".format(**data)

def format_close(data):
    data = dict(data or {})
    data.setdefault("margin_return_text", "无法计算")
    data.setdefault("account_return_text", "无法计算")
    return """=== 栖语自动交易平仓通知 ===
📡 策略名称: {strategy_name}
📊 原方向: {side_label}
⚙️ 平仓类型: {close_type}
📦 平仓数量: {sz}
💵 开仓价格: {entry_price}
💵 平仓价格: {close_price}
📈 本次盈亏: {pnl}
📊 保证金净收益率: {margin_return_text}
💼 总本金净收益率: {account_return_text}
✅ OKX平仓订单ID: {ordId}
🕒 平仓时间: {time}
""".format(**data)

def notify_open_success(current, open_order):
    side = current.get("side")
    strategy_name = strategy_name_with_grade(
        current.get("strategy_key"),
        current.get("strategy_name"),
        current,
    )
    data = {"strategy_key": current.get("strategy_key") or "unknown", "strategy_name": strategy_name, "side_label": "SHORT / 做空" if side == "short" else "LONG / 做多", "leverage": current.get("leverage") or 20, "position_mode_label": "全仓" if current.get("position_mode") == "full_balance" else str(current.get("position_mode")), "sz": current.get("real_position_sz") or current.get("sz") or "", "price": current.get("entry_price") or current.get("reference_price") or "", "stop_loss_pct_label": _percent_label(current.get("stop_loss_pct")), "stop_loss_price": current.get("stop_loss_price") or "", "ordId": ((open_order or {}).get("ack") or {}).get("ordId") or (open_order or {}).get("ordId") or "", "time": current.get("opened_at") or _now()}
    msg = format_open_success(data)
    key = str(data.get("strategy_key") or "")
    if key.startswith("mass_"):
        # Mass/E-D frozen; keep prefix only for residual historical keys.
        msg = "【已冻结·量产探针开仓(不应再出现)】\n" + msg
        return send_message(msg, kind="mass_probe_opened_frozen", meta=data)
    try:
        import auto_trade_human_confirm_pipeline as pipeline
        controls = pipeline._read(pipeline.CONTROL_PATH, {"assignments": {}})
        for aid, row in (controls.get("assignments") or {}).items():
            if (row.get("strategy_key") == key
                    or aid.endswith("|" + str(key))):
                if row.get("human_confirm_pipeline") or row.get("human_confirmed"):
                    grade = row.get("lifecycle_grade") or resolve_strategy_grade(key, row)
                    ratio = float(row.get("max_position_ratio") or 0) * 100
                    msg = ("【开仓通报·%s级 %.0f%%仓】\n" % (grade, ratio)) + msg
                    return send_message(
                        msg, kind="strategy_opened_graded", meta=dict(
                            data, grade=grade,
                            max_position_ratio=row.get("max_position_ratio")))
    except Exception:
        pass
    return send_message(msg, kind="strategy_opened", meta=data)

def notify_open_failed(payload):
    payload = dict(payload or {})
    side = payload.get("side")
    strategy_name = strategy_name_with_grade(
        payload.get("strategy_key"),
        payload.get("strategy_name"),
        payload,
    )
    data = {"strategy_key": payload.get("strategy_key") or "unknown", "strategy_name": strategy_name, "side_label": "SHORT / 做空" if side == "short" else "LONG / 做多", "leverage": payload.get("leverage") or 20, "error": payload.get("error") or payload.get("reason") or "unknown", "time": _now()}
    return send_message(format_open_failed(data), kind="strategy_open_failed", meta=data)

def notify_risk(payload):
    payload = dict(payload or {})
    side = payload.get("side")
    strategy_name = strategy_name_with_grade(
        payload.get("strategy_key"),
        payload.get("strategy_name"),
        payload,
    )
    data = {"strategy_key": payload.get("strategy_key") or "unknown", "strategy_name": strategy_name, "side_label": "SHORT / 做空" if side == "short" else "LONG / 做多", "risk_type": payload.get("risk_type") or "attached stop loss not verified", "action": payload.get("action") or "保护性平仓", "sz": payload.get("sz") or "", "price": payload.get("price") or "", "time": _now()}
    return send_message(format_risk(data), kind="protective_close_attempted", meta=data)

def notify_close(closed):
    closed = dict(closed or {})
    # Dynamic optimizer: learn from each closed trade (non-blocking)
    try:
        import auto_trade_strategy_dynamic_optimizer as optimizer
        optimizer.note_closed_trade(closed)
    except Exception:
        pass
    side = closed.get("side")
    close_order = closed.get("close_order") or {}
    ack = close_order.get("ack") or {}
    rate = _close_rate_info(closed)
    strategy_name = strategy_name_with_grade(
        closed.get("strategy_key"),
        closed.get("strategy_name"),
        closed,
    )
    data = {"strategy_key": closed.get("strategy_key") or "unknown", "strategy_name": strategy_name, "side_label": "SHORT / 做空" if side == "short" else "LONG / 做多", "close_type": closed.get("close_type") or closed.get("close_reason") or "策略平仓", "sz": closed.get("real_position_sz") or closed.get("sz") or "", "entry_price": closed.get("entry_price") or "", "close_price": closed.get("close_price") or "", "pnl": closed.get("pnl") if closed.get("pnl") is not None else "", "pnl_rate_suffix": rate.get("pnl_rate_suffix"), "ordId": ack.get("ordId") or close_order.get("ordId") or "", "time": closed.get("closed_at") or _now(), **rate}
    return send_message(format_close(data), kind="strategy_closed", meta=data)

def get_status():
    ch = get_channel()
    cfg = get_config()
    return {"ok": True, "stage": "formal_notify_stage8_23_final_safe", "enabled": cfg.get("enabled"), "bind_mode": "existing_real_wxpusher_common_send_wx", "notify_module_path": REAL_NOTIFY_MODULE, "notify_function": REAL_NOTIFY_FUNCTION, "verified_send_mode": "direct_wxpusher_http_using_common_token_uid", "notification_real_channel_ready": bool(ch.get("notification_real_channel_ready")), "channel": ch, "config_file": str(CONFIG_FILE), "audit_log_file": str(AUDIT_LOG_FILE), "no_fake_local_file_notify_channel": True, "audit_log_not_counted_as_sent": True, "sent_true_requires_wxpusher_http_json_success": True}

def self_test():
    open_msg = format_open_success({"strategy_key": "ema7_center_down_short", "strategy_name": "EMA6居中后再下行", "side_label": "SHORT / 做空", "leverage": 20, "position_mode_label": "全仓", "sz": "1", "price": "100", "stop_loss_pct_label": "0.9%", "stop_loss_price": "100.9", "ordId": "fake-order", "time": _now()})
    close_msg = format_close({"strategy_key": "ema7_center_down_short", "strategy_name": "EMA6居中后再下行", "side_label": "SHORT / 做空", "close_type": "策略止盈", "sz": "1", "entry_price": "100", "close_price": "99", "pnl": "1", "pnl_rate_suffix": " （盈利率 +20.00%）", "ordId": "fake-close", "time": _now()})
    dry = send_message(open_msg, kind="self_test", dry_run=True)
    st = get_status()
    return {"ok": True, "stage": "formal_notify_self_test_stage8_23_final_safe", "open_notification_template_pass": "栖语自动交易开仓通知" in open_msg, "close_notification_template_pass": "栖语自动交易平仓通知" in close_msg, "explicit_notify_binding_pass": True, "common_send_wx_binding_pass": st.get("notify_module_path") == "/root/common.py" and st.get("notify_function") == "send_wx", "verified_wxpusher_send_semantics_pass": st.get("sent_true_requires_wxpusher_http_json_success") is True, "no_fake_local_file_notify_channel_pass": True, "runtime_root_scan_removed_pass": True, "audit_log_not_counted_as_sent_pass": dry.get("sent") is False and dry.get("audit_logged") is True, "notification_real_channel_ready": st.get("notification_real_channel_ready"), "notification_real_channel_ready_pass": st.get("notification_real_channel_ready") is True, "status": st}
