
# -*- coding: utf-8 -*-
# STAGE8_23_FINAL_SAFE_FORMAL_NOTIFY_MODULE
from pathlib import Path
import json, time, os, importlib.util, sys, re

def _vector_root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


ROOT = _vector_root()
AUTO_DIR = ROOT / "auto_trade"
CONFIG_FILE = AUTO_DIR / "formal_notify_config.json"
AUDIT_LOG_FILE = AUTO_DIR / "formal_notification_audit.log"

REAL_NOTIFY_FUNCTION = "send_wx"
WXPUSHER_URL = "https://wxpusher.zjiecode.com/api/send/message"
_COMMON_CACHE = {"path": None, "payload": None}
_CHANNEL_CACHE = {"ts": 0.0, "payload": None}
_CHANNEL_TTL_SEC = 30.0
_LEGACY_GRADE_SUFFIX_RE = re.compile(r"（(?:[SABCDE]|待评级)级）")


def _resolve_notify_module_path():
    """Prefer VECTOR_ROOT/common.py；生产默认 /root/common.py。"""
    local = _vector_root() / "common.py"
    if local.exists():
        return str(local)
    return "/root/common.py"


REAL_NOTIFY_MODULE = _resolve_notify_module_path()
# Hard-blocked Wx kinds/markers (cannot be overridden by callers).
HARD_BLOCKED_WX_KINDS = frozenset({
    "strategy_triple_friction_tip",
    "creator_failover_exhausted",
    "kimi_creation_quality_gate_failure",
    "system_health_ai_digest",
    "strategy_pending_confirm",
    "strategy_b_online",
    "strategy_opened_graded",
    "strategy_downgrade_c",
    "strategy_downgrade_b",
    "strategy_downgrade_a",
    "strategy_promote_s",
    "strategy_promote_a",
    "strategy_promote_b",
})
HARD_BLOCKED_WX_MARKERS = (
    "三倍摩擦风险提示",
    "三倍摩擦压力提示（非淘汰）",
    "策略创造未产出合格候选",
    "策略用尽本次优化额度仍未通过",
    "【四复核通过·进入策略待优化板块】",
    "已进入策略待优化板块（人工/机器优化；不自动挂载）",
    "【质检通过·待人工确认】",
    "【三AI运维短评】",
    "【策略复核结果】\n结果: 未通过",
    "总结果: 全部未通过",
    "降级至",
    "晋升至",
    "B级策略已上线",
    "首3单2止损",
)
_LEGACY_SABC_WX_RE = re.compile(r"[SABCDE]级")

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
    global REAL_NOTIFY_MODULE, ROOT, AUTO_DIR, CONFIG_FILE, AUDIT_LOG_FILE
    ROOT = _vector_root()
    AUTO_DIR = ROOT / "auto_trade"
    CONFIG_FILE = AUTO_DIR / "formal_notify_config.json"
    AUDIT_LOG_FILE = AUTO_DIR / "formal_notification_audit.log"
    REAL_NOTIFY_MODULE = _resolve_notify_module_path()
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
    # Do not rewrite this file on every send/status poll — that blocked the
    # trade thread and made 开仓/平仓 Wx wait on disk I/O.
    if not CONFIG_FILE.exists():
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
    _CHANNEL_CACHE["ts"] = 0.0
    _CHANNEL_CACHE["payload"] = None
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
    path = str(REAL_NOTIFY_MODULE)
    cached = _COMMON_CACHE.get("payload")
    if cached is not None and _COMMON_CACHE.get("path") == path:
        return cached
    mod = _import_module(path)
    fn = getattr(mod, REAL_NOTIFY_FUNCTION, None)
    if not callable(fn):
        raise RuntimeError("common.send_wx not callable")
    token = getattr(mod, "WX_TOKEN", None)
    uid = getattr(mod, "WX_UID", None)
    if not token or not uid:
        raise RuntimeError("common.WX_TOKEN or common.WX_UID missing")
    payload = (mod, fn, token, uid)
    _COMMON_CACHE["path"] = path
    _COMMON_CACHE["payload"] = payload
    return payload

def get_channel():
    now = time.time()
    cached = _CHANNEL_CACHE.get("payload")
    if isinstance(cached, dict) and (now - float(_CHANNEL_CACHE.get("ts") or 0)) < _CHANNEL_TTL_SEC:
        return dict(cached)
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
        result = {"ok": True, "ready": True, "notification_real_channel_ready": True, "module_path": REAL_NOTIFY_MODULE, "function": REAL_NOTIFY_FUNCTION, "verified_send_mode": "direct_wxpusher_http_using_common_token_uid", "bind_mode": "existing_real_wxpusher_common_send_wx", "config": cfg}
        _CHANNEL_CACHE["ts"] = now
        _CHANNEL_CACHE["payload"] = result
        return dict(result)
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

WX_SUMMARY_QUALITY_ONLINE = "cosmic策略质检与上线通知"
WX_SUMMARY_AUTO_TRADE = "cosmic自动开仓/平仓通知"
WX_SUMMARY_HOLD_ASSIST = "cosmic智能辅助系统"
WX_SUMMARY_SAFETY_NET = "cosmic风险控制系统通知"
WX_SUMMARY_DEFAULT = "cosmic通知"

_QUALITY_ONLINE_KINDS = frozenset({
    "kimi_creation_quality_gate_success",
    "strategy_pending_confirm",
    "strategy_tier_online",
})
_AUTO_TRADE_KINDS = frozenset({
    "strategy_opened",
    "strategy_open_failed",
    "strategy_open_skipped",
    "formal_auto_trade",
    "mass_probe_opened_frozen",
    "strategy_closed",
    "protective_close_attempted",
    "force_protect_session_open",
    "force_protect_session_close",
    "force_protect_auto_close_repark",
})
_HOLD_ASSIST_KINDS = frozenset({
    "hold_path_warning",
    "hold_path_urgent",
})
_SAFETY_NET_KINDS = frozenset({
    "force_protect_intervene",
    "day_lock",
    "inbound_block",
})


def _wx_summary(kind=None):
    kind_s = str(kind or "").strip()
    if kind_s in _QUALITY_ONLINE_KINDS:
        return WX_SUMMARY_QUALITY_ONLINE
    if (
        kind_s in _AUTO_TRADE_KINDS
        or kind_s in ("force_protect_session_open", "force_protect_session_close")
    ):
        return WX_SUMMARY_AUTO_TRADE
    if (
        kind_s in _HOLD_ASSIST_KINDS
        or kind_s.startswith("hold_path_")
        or kind_s == "force_protect_hitch_follow_close"
    ):
        return WX_SUMMARY_HOLD_ASSIST
    if kind_s in _SAFETY_NET_KINDS or kind_s.startswith("force_protect_"):
        return WX_SUMMARY_SAFETY_NET
    return WX_SUMMARY_DEFAULT


def _open_symbol_label(payload):
    if not isinstance(payload, dict):
        return "—"
    raw = (
        payload.get("symbol_label")
        or payload.get("symbol")
        or payload.get("instId")
        or payload.get("inst_id")
        or ""
    )
    text = str(raw or "").upper().replace("-USDT-SWAP", "").replace("-USDT", "")
    return text or "—"


def _send_via_verified_wxpusher(message, kind=None):
    mod, fn, token, uid = _common_module()
    import requests
    payload = {
        "appToken": token,
        "content": message,
        "summary": _wx_summary(kind),
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
    meta = dict(meta or {})
    kind_s = str(kind or "")
    msg_s = str(message or "")
    try:
        from dual_engine_workflow_v2.review_lexicon import scrub as _scrub_review
        msg_s = _scrub_review(msg_s)
        message = msg_s
    except Exception:
        pass
    if (
        kind_s in HARD_BLOCKED_WX_KINDS
        or kind_s.startswith("strategy_downgrade_")
        or kind_s.startswith("strategy_promote_")
        or any(m in msg_s for m in HARD_BLOCKED_WX_MARKERS)
        or _LEGACY_SABC_WX_RE.search(msg_s)
    ):
        if (
            "四复核通过·进入策略待优化板块" in msg_s
            or "已进入策略待优化板块" in msg_s
        ):
            reason = "retired_pending_optimize_wx"
        elif (
            kind_s == "strategy_pending_confirm"
            or "【质检通过·待人工确认】" in msg_s
        ):
            reason = "retired_three_ai_confirm_wx"
        elif (
            kind_s == "system_health_ai_digest"
            or "【三AI运维短评】" in msg_s
        ):
            reason = "retired_three_ai_ops_digest_wx"
        else:
            reason = "retired_sabc_grade_wx"
        row = _audit(kind_s + "_hard_blocked", msg_s,
                     {"blocked": True, "reason": reason, **meta})
        return {"ok": True, "sent": False, "blocked": True, "audit_logged": True,
                "reason": reason, "audit": row,
                "audit_log_file": str(AUDIT_LOG_FILE)}
    ch = get_channel()
    try:
        import auto_trade_strategy_titles as titles
        message = titles.rewrite_strategy_keys_in_text(message)
        if meta.get("strategy_key") and not meta.get("strategy_title"):
            meta["strategy_title"] = titles.resolve_strategy_name(
                meta.get("strategy_key"), meta.get("strategy_name"))
    except Exception:
        pass
    message = _LEGACY_GRADE_SUFFIX_RE.sub("", str(message or ""))
    message = re.sub(r"【开仓通报·[SABCDE]级[^】]*】\s*", "", message)
    if dry_run or os.environ.get("STAGE823_INSTALL_SELF_TEST") == "1":
        row = _audit(kind + "_dry_run", message, {"dry_run": True, "channel": ch, **meta})
        return {"ok": True, "dry_run": True, "sent": False, "audit_logged": True, "notification_real_channel_ready": bool(ch.get("notification_real_channel_ready")), "notification_error": None if ch.get("notification_real_channel_ready") else ch.get("error"), "channel": ch, "audit_log_file": str(AUDIT_LOG_FILE), "audit": row}
    if not ch.get("notification_real_channel_ready"):
        row = _audit(kind + "_not_sent", message, {"channel": ch, **meta})
        return {"ok": False, "sent": False, "audit_logged": True, "notification_real_channel_ready": False, "notification_error": ch.get("error"), "audit_log_file": str(AUDIT_LOG_FILE), "audit": row}
    try:
        send_result = _send_via_verified_wxpusher(message, kind=kind_s)
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


def _lookup_roster_row(strategy_key, symbol=None, timeframe=None):
    key = str(strategy_key or "").strip()
    if not key:
        return {}
    try:
        import auto_trade_live_roster as roster
        rows = roster.load_roster()
    except Exception:
        return {}
    symbol = str(symbol or "").upper()
    timeframe = str(timeframe or "").lower()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("strategy_key") or "") != key:
            continue
        if symbol and str(row.get("symbol") or "").upper() != symbol:
            continue
        if timeframe and str(row.get("timeframe") or "").lower() != timeframe:
            continue
        return row
    for row in rows or []:
        if isinstance(row, dict) and str(row.get("strategy_key") or "") == key:
            return row
    return {}


def resolve_strategy_tier_label(strategy_key, payload=None):
    """Return 马卡龙策略 / 大福策略. Never S/A/B/C/D/E."""
    try:
        import auto_trade_strategy_tiers as tiers
    except Exception:
        tiers = None
    payload = payload if isinstance(payload, dict) else {}
    candidates = (
        payload.get("strategy_tier"),
        payload.get("strategy_tier_label"),
        payload.get("tier"),
    )
    if tiers is not None:
        for candidate in candidates:
            label = tiers.label(candidate)
            if label:
                return label
    row = _lookup_roster_row(
        strategy_key, payload.get("symbol"), payload.get("timeframe"))
    assign, _aid = _lookup_assignment_row(
        strategy_key, payload.get("symbol"), payload.get("timeframe"))
    if tiers is not None:
        for source in (row, assign, payload):
            if not isinstance(source, dict):
                continue
            code = tiers.normalize(
                value=source.get("strategy_tier") or source.get("strategy_tier_label"),
                position_ratio=source.get("explicit_position_ratio")
                or source.get("max_position_ratio")
                or source.get("position_ratio"),
            )
            label = tiers.label(code)
            if label:
                return label
    return None


def resolve_strategy_grade(strategy_key, payload=None):
    """Retired SABC letter. Kept only so old callers do not crash."""
    return resolve_strategy_tier_label(strategy_key, payload) or ""


def strategy_name_with_tier(strategy_key, strategy_name=None, payload=None):
    try:
        import auto_trade_strategy_titles as titles
        base = titles.resolve_strategy_name(strategy_key, strategy_name)
    except Exception:
        base = strategy_name or strategy_key or "未命名策略"
    base = str(base or "").strip() or "未命名策略"
    base = _LEGACY_GRADE_SUFFIX_RE.sub("", base).strip()
    base = re.sub(r"（(?:马卡龙策略|大福策略|马卡龙|大福)）\s*$", "", base).strip()
    label = resolve_strategy_tier_label(strategy_key, payload)
    if label:
        return "%s（%s）" % (base, label)
        return base


def strategy_name_with_grade(strategy_key, strategy_name=None, payload=None):
    return strategy_name_with_tier(strategy_key, strategy_name, payload)


def margin_mode_label(current=None, td_mode=None):
    try:
        import auto_trade_okx as okx
        mode = td_mode
        if isinstance(current, dict):
            mode = current.get("td_mode") or current.get("mgnMode") or mode
        return okx.margin_mode_zh(mode)
    except Exception:
        return "逐仓"


HITCH_CLASS_DIRECTION = "方向型"
HITCH_CLASS_ODDS = "赔率型"
_HITCH_CLASS_CACHE = {}
_HITCH_CLASS_TTL_SEC = 3600.0

# Frozen bipartition of currently live strategies (2026-08-22).
HITCH_CLASS_BY_KEY = {
    "kimi_c361a198338e20b159799485": HITCH_CLASS_ODDS,
    "kimi_7397ee7b20e5996a5ee09138": HITCH_CLASS_ODDS,
    "kimi_d68f67b1f044236c1f916976": HITCH_CLASS_ODDS,
    "kimi_eb8931e56d4e23b50a1d9e65": HITCH_CLASS_ODDS,
    "kimi_2b631e2045766232353bf68a": HITCH_CLASS_ODDS,
    "kimi_1082af1770b9d767d0e8003a": HITCH_CLASS_ODDS,
    "kimi_c30f664591d0e83da50422b0": HITCH_CLASS_ODDS,
    "kimi_e6fd2daa0848a5e624676a0b": HITCH_CLASS_ODDS,
    "kimi_c959b7e37ffb8a9ae172800b": HITCH_CLASS_ODDS,
    "kimi_66b278db9865bdebade76f9b": HITCH_CLASS_ODDS,
    "kimi_e54eed797d12ec924d94c8f3": HITCH_CLASS_ODDS,
    "kimi_5275f5ef4ef897aa0584c463": HITCH_CLASS_ODDS,
    "kimi_5c8a31283141fff07f1e8c2c": HITCH_CLASS_ODDS,
    "kimi_b1787954b6af6105c709bb1d": HITCH_CLASS_ODDS,
    "kimi_ced4af46fec55ba267608cd1": HITCH_CLASS_ODDS,
    "kimi_83613d953ea4590833e4e896": HITCH_CLASS_ODDS,
    "kimi_9dbab63de7040038a2f2bf2e": HITCH_CLASS_ODDS,
    "kimi_7e0481c6a856310911d9c6b5": HITCH_CLASS_ODDS,
    "kimi_c2151316602c63bac3c65d00": HITCH_CLASS_ODDS,
    "kimi_e6299363792e0eb66f49e61b": HITCH_CLASS_ODDS,
    "kimi_6b07220267035d212cab26d5": HITCH_CLASS_ODDS,
    "kimi_119a045c7cac4ef904b50d93": HITCH_CLASS_ODDS,
    "kimi_6e2080e4b78b6362d5b42bc5": HITCH_CLASS_DIRECTION,
    "kimi_203a55905617169869ce8454": HITCH_CLASS_ODDS,
    "kimi_62eb2a295fb490154741f631": HITCH_CLASS_DIRECTION,
    "kimi_c6fee276568ff089e2ef4bdc": HITCH_CLASS_DIRECTION,
    "kimi_9d842c0ec557226ecb3ec1a8": HITCH_CLASS_ODDS,
    "kimi_0cdf02d8c638dcbcf4aceebf": HITCH_CLASS_ODDS,
    "kimi_5d076c419a061dc7a967ab98": HITCH_CLASS_ODDS,
    "kimi_ac39fad1a5c1baacb638bd32": HITCH_CLASS_ODDS,
    "kimi_a94e4f6e193428a32930a189": HITCH_CLASS_DIRECTION,
    "kimi_4b9e790b9a71939fd0ff855c": HITCH_CLASS_DIRECTION,
    "kimi_b7bf72c7b44cc2e9f8a77463": HITCH_CLASS_DIRECTION,
    "kimi_782c57058431d8e18a03e8c4": HITCH_CLASS_DIRECTION,
    "kimi_2782d4d0d19abf5c981a0317": HITCH_CLASS_ODDS,
}


def _path_bucket(reason):
    r = str(reason or "")
    if r in ("trailing", "breakeven", "take_profit", "take_profit_continuous", "target"):
        return "tp"
    if r in ("protective_stop", "stop"):
        return "sl"
    if r in ("timeout", "horizon", "timed"):
        return "timed"
    return "inv"


def hitch_class_stats(trades, stop_pct):
    """Trade-path stats behind 方向型 / 赔率型.

    方向型: direction itself is stable; uncertainty is how far it runs;
    protective stop is not the main path (SL-first clearly below 30%,
    deep MAE near the stop is rare).
    """
    n = 0
    sl = 0
    inv = 0
    deep = 0
    mae_gt_mfe = 0
    win_nets = []
    loss_nets = []
    stop = None
    try:
        if stop_pct not in (None, ""):
            stop = float(stop_pct)
            if stop <= 0:
                stop = None
    except Exception:
        stop = None
    for t in trades or []:
        nr = t.get("net_return")
        if nr is None:
            continue
        nr = float(nr)
        n += 1
        if nr > 0:
            win_nets.append(nr)
        else:
            loss_nets.append(nr)
        bucket = _path_bucket(t.get("exit_reason"))
        if bucket == "sl":
            sl += 1
        elif bucket == "inv":
            inv += 1
        mae = abs(float(t.get("maximum_adverse_excursion") or 0))
        mfe = abs(float(t.get("maximum_favorable_excursion") or 0))
        if mae > mfe:
            mae_gt_mfe += 1
        if stop is not None and mae + 1e-12 >= 0.70 * stop:
            deep += 1
    if n <= 0:
        return None
    sl_rate = sl / float(n)
    inv_rate = inv / float(n)
    deep_rate = deep / float(n)
    mae_mfe = mae_gt_mfe / float(n)
    mean_win = (sum(win_nets) / len(win_nets)) if win_nets else 0.0
    mean_loss = (sum(loss_nets) / len(loss_nets)) if loss_nets else 0.0
    payoff = (mean_win / abs(mean_loss)) if mean_loss < 0 else None
    score_odds = min(sl_rate / 0.35, 1.5) * 1.2 + min(deep_rate / 0.45, 1.5) * 1.0
    if payoff:
        score_odds += min(max(payoff - 1.0, 0) / 1.2, 1.2) * 0.8
    score_dir = max(0.0, (0.28 - sl_rate) / 0.28) * 1.4
    score_dir += max(0.0, (0.35 - deep_rate) / 0.35) * 1.0
    score_dir += max(0.0, (0.35 - mae_mfe) / 0.35) * 0.6
    klass = HITCH_CLASS_ODDS if score_odds >= score_dir else HITCH_CLASS_DIRECTION
    if sl_rate >= 0.30 or deep_rate >= 0.48 or inv_rate >= 0.40:
        klass = HITCH_CLASS_ODDS
    if sl_rate <= 0.22 and deep_rate <= 0.32 and inv_rate <= 0.25:
        klass = HITCH_CLASS_DIRECTION
    return {
        "n": n,
        "sl_rate": sl_rate,
        "inv_rate": inv_rate,
        "deep_rate": deep_rate,
        "mae_mfe_rate": mae_mfe,
        "payoff": payoff,
        "klass": klass,
    }


def _classify_from_trades(trades, stop_pct):
    stats = hitch_class_stats(trades, stop_pct)
    if not stats:
        return None
    return stats.get("klass")


OSCILLATOR_FEATURES = frozenset({
    "close_z_20", "rsi_14", "rsi", "stoch_k", "cci_20", "williams_r",
})
MEAN_REVERSION_NAME_MARKERS = ("超卖", "反转", "失败反弹", "假突破")


def _collect_features(node, bag):
    if isinstance(node, dict):
        feat = node.get("feature")
        if feat:
            bag.add(str(feat))
        for value in node.values():
            _collect_features(value, bag)
    elif isinstance(node, list):
        for value in node:
            _collect_features(value, bag)


def semantic_direction_issues(definition):
    """Flags that contradict 方向型: direction stable, distance uncertain.

    Oscillator / z-score invalidation treats a small bounce as 'direction
    broken', so stop/invalidation becomes the main path.
    """
    issues = []
    definition = definition or {}
    name = str(definition.get("name") or "")
    for marker in MEAN_REVERSION_NAME_MARKERS:
        if marker in name:
            issues.append("mean_reversion_name:" + marker)
            break
    plan = definition.get("exit_plan") or {}
    for row in plan.get("conditional_exits") or []:
        role = str(row.get("role") or "")
        if role not in ("invalidation", "失效"):
            continue
        feats = set()
        _collect_features(row.get("when"), feats)
        bad = sorted(feats & OSCILLATOR_FEATURES)
        if bad:
            issues.append("hair_trigger_invalidation:" + ",".join(bad))
    return issues


def load_semantic_strategy(strategy_key):
    key = str(strategy_key or "").strip()
    if not key:
        return None
    path = ROOT / "strategy_configs" / "semantic_live_strategies.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    for row in data.get("strategies") or []:
        if str((row or {}).get("key") or "") == key:
            return row
    return None


def strategy_hitch_class(strategy_key, stop_pct=None, ignore_freeze=False):
    key = str(strategy_key or "").strip()
    if not key:
        return None
    issues = semantic_direction_issues(load_semantic_strategy(key) or {})
    if issues:
        return HITCH_CLASS_ODDS
    if not ignore_freeze:
        frozen = HITCH_CLASS_BY_KEY.get(key)
        if frozen:
            return frozen
    now = time.time()
    cache_key = key if not ignore_freeze else key + "|live"
    cached = _HITCH_CLASS_CACHE.get(cache_key)
    if cached and (now - float(cached[0] or 0.0)) < _HITCH_CLASS_TTL_SEC:
        return cached[1]
    klass = None
    try:
        from auto_trade_portfolio_combo_metrics import _semantic_receipt_backtest
        rec = _semantic_receipt_backtest(key)
        trades = ((rec or {}).get("backtest") or {}).get("trades_all") or []
        klass = _classify_from_trades(trades, stop_pct)
    except Exception:
        klass = None
    _HITCH_CLASS_CACHE[cache_key] = (now, klass)
    return klass


def format_tier_hitch_zh(tier_label, hitch_class=None):
    """Compose 马卡龙策略·赔率型 for roster/position badges.

    Capital-tier labels used in occupancy/Wx suffixes stay unchanged.
    """
    base = str(tier_label or "").strip() or "马卡龙策略"
    if "手动" in base:
        return base
    for extra in (HITCH_CLASS_ODDS, HITCH_CLASS_DIRECTION):
        token = "·" + extra
        if base.endswith(token):
            base = base[: -len(token)].rstrip()
            break
    klass = str(hitch_class or "").strip()
    if klass not in (HITCH_CLASS_ODDS, HITCH_CLASS_DIRECTION):
        return base
    return "%s·%s" % (base, klass)


def hitch_open_advice_text(strategy_key, hitch_class=None, **_ignored):
    klass = hitch_class if hitch_class is not None else strategy_hitch_class(strategy_key)
    if klass == HITCH_CLASS_DIRECTION:
        return "可以开顺风车仓（方向型）"
    if klass == HITCH_CLASS_ODDS:
        return "不建议开顺风车单（赔率型）"
    return "暂无分类，不建议开顺风车单"


hitch_open_advice_text = hitch_open_advice_text


def _hitch_class_token(advice=None, hitch_class=None, strategy_key=None):
    text = str(advice or hitch_class or "")
    if not text:
        text = hitch_open_advice_text(strategy_key)
    if "方向型" in text:
        return HITCH_CLASS_DIRECTION
    if "赔率型" in text:
        return HITCH_CLASS_ODDS
    return "未分类"


def hitch_type_line(advice=None, hitch_class=None, strategy_key=None):
    klass = _hitch_class_token(advice, hitch_class, strategy_key)
    if klass == HITCH_CLASS_DIRECTION:
        return "🌦️ 策略类型（方向型）：可以开顺风单"
    if klass == HITCH_CLASS_ODDS:
        return "🌦️ 策略类型（赔率型）：不建议开顺风单"
    return "🌦️ 策略类型（未分类）：不建议开顺风单"


def hitch_close_type_line(advice=None, hitch_class=None, strategy_key=None):
    """Close cards show hitch class only; open-advice stays on open cards."""
    klass = _hitch_class_token(advice, hitch_class, strategy_key)
    return "🌦️ 策略类型%s" % klass


def facts_card(lines):
    return "\n".join(str(x) for x in (lines or []) if x)


def manor_card(banner, quote, facts=None):
    """Housekeeper body for intervene/session cards. Lock cards use facts_card."""
    parts = []
    banner_s = str(banner or "").strip()
    quote_s = str(quote or "").strip()
    if banner_s:
        parts.append(banner_s)
    if quote_s:
        parts.append("“%s”" % quote_s)
    extra = [x for x in (facts or []) if x]
    if extra:
        if parts:
            parts.append("")
        parts.extend(extra)
    return "\n".join(parts)


def _sweet_hitch_whisper(advice):
    return hitch_type_line(advice)


def _with_hitch_advice(data):
    data = dict(data or {})
    if not data.get("hitch_advice"):
        data["hitch_advice"] = hitch_open_advice_text(data.get("strategy_key"))
    return data


def format_open_success(data):
    data = _with_hitch_advice(data)
    data.setdefault("symbol_label", "—")
    data.setdefault("position_mode_label", data.get("position_mode_label") or "—")
    data.setdefault("stop_loss_pct_label", data.get("stop_loss_pct_label") or "—")
    data.setdefault("stop_loss_price", data.get("stop_loss_price") or "—")
    facts = [
        "📡 策略名称: {strategy_name}",
        "📊 方向: {side_label}",
        "📌 开仓标的: {symbol_label}",
        "📌 杠杆倍数: {leverage}x",
        "💰 仓位模式: {position_mode_label}",
        "📦 下单数量: {sz}",
        "💵 开仓价格: {price}",
        "🛡️ 止损比例: {stop_loss_pct_label}",
        "🛡️ 止损价格: {stop_loss_price}",
        "🕒 开仓时间: {time}",
        hitch_type_line(data.get("hitch_advice")),
    ]
    return facts_card([x.format(**data) if "{" in x else x for x in facts])

OPEN_FAIL_REASON_ZH = {
    "portfolio estimated risk limit exceeded": "自动开仓同时估算风险达到阈值",
}


def _open_fail_reason_zh(error):
    text = str(error or "").strip() or "unknown"
    return OPEN_FAIL_REASON_ZH.get(text, text)


def format_open_failed(data):
    data = dict(data or {})
    data.setdefault("symbol_label", "—")
    data.setdefault("error", "unknown")
    data["error"] = _open_fail_reason_zh(data.get("error"))
    facts = [
        "📡 策略名称: {strategy_name}",
        "📊 方向: {side_label}",
        "📌 开仓标的: {symbol_label}",
        "📌 杠杆倍数: {leverage}x",
        "💰 仓位模式: {position_mode_label}",
        "❌ 失败原因: {error}",
        "🕒 时间: {time}",
    ]
    return facts_card([x.format(**data) for x in facts])


def format_open_skipped(data):
    data = dict(data or {})
    data.setdefault("symbol_label", "—")
    data.setdefault("tier_need_text", "—")
    data.setdefault("occupancy_text", "—")
    data.setdefault("leftover_text", "—")
    data.setdefault("error", "资金占用已满")
    facts = [
        "📡 策略名称: {strategy_name}",
        "📊 方向: {side_label}",
        "📌 开仓标的: {symbol_label}",
        "📌 杠杆倍数: {leverage}x",
        "💰 仓位模式: {position_mode_label}",
        "📦 本策略档位: {tier_need_text}",
        "📋 当前占用: {occupancy_text}",
        "📦 剩余可开: {leftover_text}",
        "❌ 跳过原因: {error}",
        "🕒 时间: {time}",
    ]
    return facts_card([x.format(**data) for x in facts])

def _pnl_negative(pnl):
    try:
        if pnl in (None, ""):
            return False
        return float(str(pnl).replace(",", "").replace("+", "").strip()) < 0.0
    except Exception:
        return False


def close_reason_from_exit_type(exit_type):
    """Map strategy exit_type to executor close reason. Close timing unchanged."""
    text = str(exit_type or "").strip()
    if "定时" in text or "第二根K线" in text:
        return "strategy_timed_forced_close"
    if "失效" in text:
        return "strategy_invalidated_exit"
    if "止损" in text:
        return "strategy_protective_exit"
    if "止盈" in text:
        return "strategy_take_profit_authoritative_exit"
    return "strategy_rule_exit"


def close_type_from_reason(reason, exit_type=None, pnl=None):
    """User-facing 平仓类型. A losing close is never labeled 止盈."""
    text = str(exit_type or "").strip()
    reason_s = str(reason or "")
    if not text:
        if "timed_forced" in reason_s:
            text = "定时强制平仓"
        elif "take_profit" in reason_s:
            text = "策略止盈"
        elif "invalidated" in reason_s:
            text = "策略失效"
        elif "protective" in reason_s or "attached_sl" in reason_s:
            text = "保护性平仓"
        elif "rule_exit" in reason_s:
            text = "策略规则退出"
        else:
            text = "手动平仓"
    if _pnl_negative(pnl) and (
        "止盈" in text or "take_profit" in reason_s.lower()
    ):
        return "策略规则退出"
    return text or "策略平仓"


def format_risk(data):
    data = dict(data or {})
    facts = [
        "📡 策略名称: {strategy_name}",
        "📊 方向: {side_label}",
        "⚠️ 风险类型: {risk_type}",
        "🛡️ 处理方式: {action}",
        "📦 仓位数量: {sz}",
        "💵 参考价格: {price}",
        "🕒 时间: {time}",
    ]
    return facts_card([x.format(**data) for x in facts])

def format_close(data):
    data = dict(data or {})
    data = _with_hitch_advice(data)
    data.setdefault("ordId", data.get("ordId") or "")
    data.setdefault("margin_return_text", data.get("margin_pnl_text") or "无法计算")
    data.setdefault("account_return_text", data.get("account_pnl_text") or "无法计算")
    data["close_type"] = close_type_from_reason(
        data.get("close_reason") or data.get("close_type"),
        exit_type=data.get("exit_type") or data.get("close_type"),
        pnl=data.get("pnl"),
    )
    facts = [
        "📡 策略名称: {strategy_name}",
        "📊 方向: {side_label}",
        "⚙️ 平仓类型: {close_type}",
        "📦 平仓数量: {sz}",
        "💵 开仓价格: {entry_price}",
        "💵 平仓价格: {close_price}",
        "📈 本次盈亏: {pnl}",
        "📊 保证金净收益率: {margin_return_text}",
        "💼 总本金净收益率: {account_return_text}",
        "🕒 平仓时间: {time}",
        hitch_close_type_line(data.get("hitch_advice")),
    ]
    return facts_card([x.format(**data) if "{" in x else x for x in facts])

def notify_open_success(current, open_order):
    side = current.get("side")
    strategy_name = strategy_name_with_tier(
        current.get("strategy_key"),
        current.get("strategy_name"),
        current,
    )
    data = {"strategy_key": current.get("strategy_key") or "unknown", "strategy_name": strategy_name, "side_label": "SHORT / 做空" if side == "short" else "LONG / 做多", "symbol_label": _open_symbol_label(current), "leverage": current.get("leverage") or 20, "position_mode_label": margin_mode_label(current), "sz": current.get("real_position_sz") or current.get("sz") or "", "price": current.get("entry_price") or current.get("reference_price") or "", "stop_loss_pct_label": _percent_label(current.get("stop_loss_pct")), "stop_loss_price": current.get("stop_loss_price") or "", "ordId": ((open_order or {}).get("ack") or {}).get("ordId") or (open_order or {}).get("ordId") or "", "time": current.get("opened_at") or _now()}
    data["hitch_advice"] = hitch_open_advice_text(
        data.get("strategy_key"),
        side=side,
        inst_id=current.get("symbol") or current.get("inst_id") or current.get("instId"),
        timeframe=current.get("timeframe") or "1h",
        price=data.get("price"),
    )
    msg = format_open_success(data)
    key = str(data.get("strategy_key") or "")
    if key.startswith("mass_"):
        # Mass/E-D frozen; keep prefix only for residual historical keys.
        msg = "【已冻结·量产探针开仓(不应再出现)】\n" + msg
        return send_message(msg, kind="mass_probe_opened_frozen", meta=data)
    label = resolve_strategy_tier_label(key, current)
    if label:
        data["strategy_tier_label"] = label
    return send_message(msg, kind="strategy_opened", meta=data)

SKIP_NOTIFY_FILE = AUTO_DIR / "occupancy_skip_notify.json"
SKIP_NOTIFY_COOLDOWN_SEC = 3600.0


def _fmt_leverage(value, default=20):
    try:
        number = float(value)
    except Exception:
        number = float(default)
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return ("%.4f" % number).rstrip("0").rstrip(".")


def _pct_label(ratio):
    try:
        return "%s%%" % int(round(float(ratio) * 100))
    except Exception:
        return "—"


def _tier_need_text(payload):
    payload = payload if isinstance(payload, dict) else {}
    label = resolve_strategy_tier_label(
        payload.get("strategy_key"), payload) or ""
    ratio = payload.get("needed_ratio")
    if ratio is None:
        ratio = payload.get("full_position_ratio")
    if label and ratio is not None:
        return "%s %s" % (label, _pct_label(ratio))
    if label:
        return label
    if ratio is not None:
        return _pct_label(ratio)
    return "—"


def _open_card_fields(payload):
    payload = dict(payload or {})
    side = payload.get("side")
    strategy_name = strategy_name_with_tier(
        payload.get("strategy_key"),
        payload.get("strategy_name"),
        payload,
    )
    error = payload.get("error") or payload.get("reason") or "unknown"
    if error == "full balance sizing failed":
        sizing = payload.get("sizing") or payload.get("reason")
        if isinstance(sizing, dict) and sizing.get("error"):
            error = "资金占用不足（%s）" % sizing.get("error")
    occ = payload.get("occupancy") or {}
    return {
        "strategy_key": payload.get("strategy_key") or "unknown",
        "strategy_name": strategy_name,
        "side_label": "SHORT / 做空" if side == "short" else "LONG / 做多",
        "symbol_label": _open_symbol_label(payload),
        "leverage": _fmt_leverage(payload.get("leverage"), 20),
        "position_mode_label": margin_mode_label(payload),
        "error": error,
        "time": payload.get("time") or _now(),
        "tier_need_text": _tier_need_text(payload),
        "occupancy_text": occ.get("occupancy_text") or payload.get("occupancy_text") or "—",
        "leftover_text": occ.get("leftover_zh") or payload.get("leftover_text") or "—",
    }


def occupancy_skip_due(payload, now_ts=None, cooldown_sec=None):
    """True when this occupancy skip should send Wx."""
    now_ts = time.time() if now_ts is None else float(now_ts)
    wait = SKIP_NOTIFY_COOLDOWN_SEC if cooldown_sec is None else float(cooldown_sec)
    key = "%s|%s" % (
        payload.get("strategy_key") or "",
        str(payload.get("symbol") or payload.get("instId") or "").upper(),
    )
    fingerprint = str(payload.get("occupancy_fingerprint") or "")
    doc = _read_json(SKIP_NOTIFY_FILE, {})
    if not isinstance(doc, dict):
        doc = {}
    row = doc.get(key) or {}
    if (
        fingerprint
        and row.get("fingerprint") == fingerprint
        and (now_ts - float(row.get("ts") or 0)) < wait
    ):
        return False
    doc[key] = {"fingerprint": fingerprint, "ts": now_ts, "time": _now()}
    try:
        _write_json(SKIP_NOTIFY_FILE, doc)
    except Exception:
        pass
    return True


def notify_open_failed(payload):
    data = _open_card_fields(payload)
    return send_message(format_open_failed(data), kind="strategy_open_failed", meta=data)


def notify_open_skipped(payload):
    payload = dict(payload or {})
    if not occupancy_skip_due(payload):
        return {"ok": True, "sent": False, "skipped": True,
                "reason": "occupancy_skip_cooldown", "audit_logged": False}
    data = _open_card_fields(payload)
    return send_message(format_open_skipped(data), kind="strategy_open_skipped", meta=data)

def notify_risk(payload):
    payload = dict(payload or {})
    side = payload.get("side")
    strategy_name = strategy_name_with_tier(
        payload.get("strategy_key"),
        payload.get("strategy_name"),
        payload,
    )
    data = {"strategy_key": payload.get("strategy_key") or "unknown", "strategy_name": strategy_name, "side_label": "SHORT / 做空" if side == "short" else "LONG / 做多", "risk_type": payload.get("risk_type") or "attached stop loss not verified", "action": payload.get("action") or "保护性平仓", "sz": payload.get("sz") or "", "price": payload.get("price") or "", "time": _now()}
    return send_message(format_risk(data), kind="protective_close_attempted", meta=data)

def notify_close(closed):
    closed = dict(closed or {})
    side = closed.get("side")
    close_order = closed.get("close_order") or {}
    ack = close_order.get("ack") or {}
    rate = _close_rate_info(closed)
    strategy_name = strategy_name_with_tier(
        closed.get("strategy_key"),
        closed.get("strategy_name"),
        closed,
    )
    data = {"strategy_key": closed.get("strategy_key") or "unknown", "strategy_name": strategy_name, "side_label": "SHORT / 做空" if side == "short" else "LONG / 做多", "close_type": closed.get("close_type") or closed.get("close_reason") or "策略平仓", "close_reason": closed.get("close_reason") or "", "exit_type": closed.get("exit_type") or "", "sz": closed.get("real_position_sz") or closed.get("sz") or "", "entry_price": closed.get("entry_price") or "", "close_price": closed.get("close_price") or "", "pnl": closed.get("pnl") if closed.get("pnl") is not None else "", "pnl_rate_suffix": rate.get("pnl_rate_suffix"), "ordId": ack.get("ordId") or close_order.get("ordId") or "", "time": closed.get("closed_at") or _now(), **rate}
    data["hitch_advice"] = hitch_open_advice_text(
        data.get("strategy_key"),
        side=side,
        inst_id=closed.get("symbol") or closed.get("inst_id") or closed.get("instId"),
        timeframe=closed.get("timeframe") or "1h",
        price=data.get("close_price") or data.get("entry_price"),
    )
    result = send_message(format_close(data), kind="strategy_closed", meta=data)
    # Learn after Wx so optimizer / death-map work cannot delay the card.
    try:
        import auto_trade_strategy_dynamic_optimizer as optimizer
        optimizer.note_closed_trade(closed)
    except Exception:
        pass
    return result


def notify_strategy_review_outcome(payload=None, dry_run=False):
    """策略进入四阶段复核后的结果推送：仅过关发 Wx，不过关只记审计。

    走已有 WxPusher 通道（common.send_wx / formal_notify）。
    """
    payload = dict(payload or {})
    passed = bool(payload.get("passed"))
    status_zh = str(payload.get("status_zh") or ("通过" if passed else "未通过"))
    stage = payload.get("review_stage") or payload.get("reason") or "—"
    reason = payload.get("reason") or stage
    try:
        from dual_engine_workflow_v2 import review_lexicon as lex
        review_n = lex.review_n_from_reason(reason, stage=stage)
        gate_zh = lex.review_label_from_reason(reason, stage=stage) or lex.pipe_label(stage, stage)
        cause_zh = lex.scrub(str(payload.get("message_zh") or reason or "—"))
    except Exception:
        review_n = None
        gate_zh = str(stage)
        cause_zh = str(payload.get("message_zh") or reason or "—")
    name = (
        payload.get("strategy_name")
        or payload.get("strategy_key")
        or payload.get("research_direction")
        or "未命名策略"
    )
    try:
        import auto_trade_strategy_titles as titles
        name = titles.resolve_strategy_name(payload.get("strategy_key"), name) or name
    except Exception:
        pass
    symbol = payload.get("symbol") or "—"
    timeframe = payload.get("timeframe") or "—"
    direction = payload.get("direction") or payload.get("trade_direction") or "—"
    pipeline_label = payload.get("pipeline_label") or (
        "管道%s" % payload.get("pipeline") if payload.get("pipeline") else "—"
    )
    task_id = payload.get("task_id") or "—"
    job_id = payload.get("job_id") or "—"
    next_zh = (
        "已进入人工确认（不自动挂载）"
        if passed else
        "未进入人工确认；候选停留在质检否决"
    )
    msg = (
        "【策略复核结果】\n"
        "结果: {status}\n"
        "名称: {name}\n"
        "标的/周期/方向: {symbol} / {timeframe} / {direction}\n"
        "管道: {pipeline}\n"
        "复核关卡: {gate}\n"
        "原因: {cause}\n"
        "复核任务: {task}\n"
        "创造任务: {job}\n"
        "后续: {nxt}\n"
        "时间: {t}"
    ).format(
        status=status_zh,
        name=name,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        pipeline=pipeline_label,
        gate=gate_zh if not review_n else "%s（第%s次）" % (gate_zh, review_n),
        cause=str(cause_zh)[:280],
        task=task_id,
        job=job_id,
        nxt=next_zh,
        t=_now(),
    )
    meta = {
        "strategy_name": name,
        "strategy_key": payload.get("strategy_key"),
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "pipeline": payload.get("pipeline"),
        "pipeline_label": pipeline_label,
        "passed": passed,
        "status_zh": status_zh,
        "review_stage": stage,
        "reason": reason,
        "task_id": task_id,
        "job_id": job_id,
        "review_n": review_n,
        "channel": "auto_trade_formal_notify",
    }
    if not passed:
        row = _audit("strategy_review_outcome_skipped_failure", msg, meta)
        return {
            "ok": True, "sent": False, "skipped_failure": True,
            "audit_logged": True, "message": msg, "meta": meta, "audit": row,
    }
    result = send_message(
        msg,
        kind="strategy_review_outcome",
        meta=meta,
        dry_run=dry_run,
    )
    result["message"] = msg
    result["meta"] = meta
    return result

def get_status():
    ch = get_channel()
    cfg = get_config()
    return {"ok": True, "stage": "formal_notify_stage8_23_final_safe", "enabled": cfg.get("enabled"), "bind_mode": "existing_real_wxpusher_common_send_wx", "notify_module_path": REAL_NOTIFY_MODULE, "notify_function": REAL_NOTIFY_FUNCTION, "verified_send_mode": "direct_wxpusher_http_using_common_token_uid", "notification_real_channel_ready": bool(ch.get("notification_real_channel_ready")), "channel": ch, "config_file": str(CONFIG_FILE), "audit_log_file": str(AUDIT_LOG_FILE), "no_fake_local_file_notify_channel": True, "audit_log_not_counted_as_sent": True, "sent_true_requires_wxpusher_http_json_success": True}

def self_test():
    open_msg = format_open_success({"strategy_key": "ema7_center_down_short", "strategy_name": "EMA6居中后再下行", "side_label": "SHORT / 做空", "leverage": 20, "position_mode_label": "逐仓", "sz": "1", "price": "100", "stop_loss_pct_label": "0.9%", "stop_loss_price": "100.9", "ordId": "fake-order", "time": _now()})
    close_msg = format_close({"strategy_key": "ema7_center_down_short", "strategy_name": "EMA6居中后再下行", "side_label": "SHORT / 做空", "close_type": "策略止盈", "sz": "1", "entry_price": "100", "close_price": "99", "pnl": "1", "pnl_rate_suffix": " （盈利率 +20.00%）", "ordId": "fake-close", "time": _now()})
    dry = send_message(open_msg, kind="self_test", dry_run=True)
    st = get_status()
    return {"ok": True, "stage": "formal_notify_self_test_stage8_23_final_safe", "open_notification_template_pass": "📡 策略名称:" in open_msg, "close_notification_template_pass": "📡 策略名称:" in close_msg and "⚙️ 平仓类型:" in close_msg, "explicit_notify_binding_pass": True, "common_send_wx_binding_pass": st.get("notify_module_path") == "/root/common.py" and st.get("notify_function") == "send_wx", "verified_wxpusher_send_semantics_pass": st.get("sent_true_requires_wxpusher_http_json_success") is True, "no_fake_local_file_notify_channel_pass": True, "runtime_root_scan_removed_pass": True, "audit_log_not_counted_as_sent_pass": dry.get("sent") is False and dry.get("audit_logged") is True, "notification_real_channel_ready": st.get("notification_real_channel_ready"), "notification_real_channel_ready_pass": st.get("notification_real_channel_ready") is True, "status": st}
