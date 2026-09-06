# -*- coding: utf-8 -*-
"""Kimi K3 OpenAI-compatible gateway helpers with a bounded backup endpoint.

Primary: QIYU_KIMI_URL + QIYU_KIMI_API_KEY (cmkey.cn).
Backup:  QIYU_KIMI_BACKUP_URL + QIYU_KIMI_BACKUP_API_KEY (api2.cmkey.cn).

When both channels are configured, POSTs race in parallel and the first
successful body wins. Sequential 540s primary hang before backup is the
failure mode this module exists to avoid.
"""
from __future__ import print_function

import json
import os
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from concurrent.futures import TimeoutError as FuturesTimeout

_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

KIMI_FAILOVER_MARKERS = (
    "401", "403", "unauthorized", "鉴权", "令牌无效", "已过期",
    "blocked", "fraud", "breach", "账户被阻止", "身份验证失败",
    "429", "rate limit", "rate_limit", "too many", "tpm", "500次",
    "请求过于频繁", "并发已达上限",
    "5h", "5小时", "5 小时",
    "insufficient", "balance", "余额不足", "额度", "quota",
    "周额度", "weekly", "billing", "payment required",
    "500", "internal server", "bad_response_body",
    "jsondecode", "invalid character", "unexpected end of json",
    "kimi_empty_content", "kimi_json_parse_failed",
    "502", "503", "504", "service unavailable", "bad gateway",
    "gateway time",
    "timeout", "timed out", "kimi_wall_clock_timeout",
)

SAME_ENDPOINT_TRANSIENT_CODES = (502, 503, 504)
SAME_ENDPOINT_TRANSIENT_RETRIES = 1
SAME_ENDPOINT_RETRY_SLEEP_SEC = 8
# Python 3.6 SSL sockets can ignore urlopen(timeout=) and hang in poll().
WALL_CLOCK_GRACE_SEC = 15


def normalize_kimi_chat_url(url):
    """Accept host, /v1, or full chat URL; always return .../v1/chat/completions."""
    u = str(url or "").strip().rstrip("/")
    if not u or "moonshot." in u:
        u = "https://cmkey.cn/v1"
    lowered = u.lower()
    if lowered in ("https://cmkey.cn", "http://cmkey.cn"):
        u = "https://cmkey.cn/v1"
    if lowered in ("https://api2.cmkey.cn", "http://api2.cmkey.cn"):
        u = "https://api2.cmkey.cn/v1"
    if u.endswith("/chat/completions"):
        return u
    if u.endswith("/v1"):
        return u + "/chat/completions"
    return u + "/v1/chat/completions"


def kimi_should_failover(error_text):
    text = str(error_text or "").lower()
    return any(marker in text for marker in KIMI_FAILOVER_MARKERS)


def kimi_transport_backoff_seconds(error_text, repair_number):
    """Seconds to wait before a technical repair after a provider fault."""
    text = str(error_text or "").lower()
    n = int(repair_number)
    if "401" in text or "unauthorized" in text or "403" in text:
        return min(90, 15 * (2 ** n))
    if any(marker in text for marker in (
        "502", "503", "504", "gateway", "timeout", "timed out",
        "service unavailable", "bad gateway",
    )):
        return min(30, 5 * (2 ** n))
    return min(180, 60 * (2 ** n))


def kimi_endpoint_chain():
    """Primary then optional backup. Empty key/url entries are skipped."""
    primary_url = str(os.environ.get("QIYU_KIMI_URL") or "").strip()
    primary_key = str(os.environ.get("QIYU_KIMI_API_KEY") or "").strip()
    backup_url = str(
        os.environ.get("QIYU_KIMI_BACKUP_URL")
        or os.environ.get("QIYU_KIMI_URL_2")
        or ""
    ).strip()
    backup_key = str(
        os.environ.get("QIYU_KIMI_BACKUP_API_KEY")
        or os.environ.get("QIYU_KIMI_API_KEY_2")
        or ""
    ).strip()
    model = str(
        os.environ.get("QIYU_KIMI_BACKUP_MODEL")
        or os.environ.get("QIYU_KIMI_MODEL")
        or "kimi-k3"
    ).strip() or "kimi-k3"
    rows = []
    if primary_url and primary_key:
        rows.append({
            "name": "primary",
            "url": normalize_kimi_chat_url(primary_url),
            "key": primary_key,
            "model": str(os.environ.get("QIYU_KIMI_MODEL") or "kimi-k3").strip() or "kimi-k3",
        })
    if backup_url and backup_key:
        rows.append({
            "name": "backup",
            "url": normalize_kimi_chat_url(backup_url),
            "key": backup_key,
            "model": model,
        })
    return rows


def invent_endpoint_chain():
    """Kimi mouths plus optional Qwen/DeepSeek congestion outlets.

    Extra providers are OpenAI-compatible chat completions endpoints used when
    invent lanes bind to them or when Kimi 429/tpm/quota fails over.
    """
    rows = list(kimi_endpoint_chain())
    qwen_key = str(os.environ.get("QIYU_QWEN_API_KEY") or "").strip()
    qwen_url = str(
        os.environ.get("QIYU_QWEN_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    ).strip()
    qwen_model = str(
        os.environ.get("QIYU_QWEN_MODEL") or "qwen3.7-plus"
    ).strip() or "qwen3.7-plus"
    if qwen_key and qwen_url:
        rows.append({
            "name": "qwen",
            "url": qwen_url,
            "key": qwen_key,
            "model": qwen_model,
        })
    ds_key = str(os.environ.get("QIYU_DEEPSEEK_API_KEY") or "").strip()
    ds_url = str(
        os.environ.get("QIYU_DEEPSEEK_URL")
        or "https://api.deepseek.com/chat/completions"
    ).strip()
    ds_model = str(
        os.environ.get("QIYU_DEEPSEEK_MODEL") or "deepseek-v4-pro"
    ).strip() or "deepseek-v4-pro"
    if ds_key and ds_url:
        rows.append({
            "name": "deepseek",
            "url": ds_url,
            "key": ds_key,
            "model": ds_model,
        })
    return rows


def _open_no_proxy(req, timeout):
    url = ""
    try:
        url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
    except Exception:
        url = ""
    if "cmkey.cn" in str(url):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def _open_with_deadline(req, timeout):
    """urlopen timeout plus a wall clock; SSL on py3.6 can ignore socket timeout."""
    timeout = float(timeout or 540)
    grace = float(WALL_CLOCK_GRACE_SEC)
    pool = ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(_open_no_proxy, req, timeout)
    try:
        return fut.result(timeout=timeout + grace)
    except FuturesTimeout:
        raise socket.timeout("kimi_wall_clock_timeout:%ss" % int(timeout))
    finally:
        pool.shutdown(wait=False)


def _http_error_text(exc):
    body = ""
    try:
        body = exc.read().decode("utf-8", "replace")[:400]
    except Exception:
        body = ""
    code = getattr(exc, "code", "")
    reason = getattr(exc, "reason", "")
    return "HTTPError:HTTP Error %s: %s %s" % (code, reason, body)


def _choice_parts(value):
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            elif item is not None:
                parts.append(str(item))
        return "".join(parts)
    if value is None:
        return ""
    return str(value)


def _choice_text(raw):
    message = ((raw or {}).get("choices") or [{}])[0].get("message") or {}
    content = _choice_parts(message.get("content")).strip()
    if content:
        return content
    for key in ("reasoning_content", "reasoning", "refusal"):
        extra = _choice_parts(message.get(key)).strip()
        if extra:
            return extra
    return ""


def _first_json_value(text):
    s = str(text or "")
    start_obj = s.find("{")
    start_arr = s.find("[")
    starts = [i for i in (start_obj, start_arr) if i >= 0]
    if not starts:
        return None
    start = min(starts)
    opener = s[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start:i + 1])
                except Exception:
                    return None
    return None


def kimi_json_from_raw(raw):
    """Return parsed JSON from content/reasoning. Drop chain-of-thought prose."""
    message = ((raw or {}).get("choices") or [{}])[0].get("message") or {}
    for key in ("content", "reasoning_content", "reasoning"):
        obj = _first_json_value(_choice_parts(message.get(key)))
        if obj is not None:
            return obj
    return None


def _transient_http_code(exc):
    try:
        return int(getattr(exc, "code", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _kimi_post_one(endpoint, body, timeout, same_endpoint_retries):
    """POST one channel. same_endpoint_retries is 0 when racing a backup."""
    last_error = "kimi_provider_not_configured"
    used = []
    payload = dict(body or {})
    payload["model"] = endpoint.get("model") or payload.get("model") or "kimi-k3"
    req = urllib.request.Request(
        endpoint["url"],
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + endpoint["key"],
            "Content-Type": "application/json",
        },
        method="POST",
    )
    attempts = 1 + max(0, int(same_endpoint_retries or 0))
    for attempt in range(attempts):
        try:
            with _open_with_deadline(req, timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
            if not _choice_text(raw):
                last_error = "kimi_empty_content"
                used.append("%s:%s" % (endpoint["name"], last_error))
                break
            used.append(endpoint["name"])
            raw["_qiyu_kimi_endpoint"] = endpoint["name"]
            return {
                "ok": True, "raw": raw,
                "endpoint": endpoint["name"], "used": used,
            }
        except urllib.error.HTTPError as exc:
            last_error = _http_error_text(exc)
            used.append("%s:%s" % (endpoint["name"], last_error[:80]))
            code = _transient_http_code(exc)
            if (
                code in SAME_ENDPOINT_TRANSIENT_CODES
                and attempt + 1 < attempts
            ):
                time.sleep(SAME_ENDPOINT_RETRY_SLEEP_SEC * (attempt + 1))
                continue
            break
        except Exception as exc:
            last_error = "%s:%s" % (type(exc).__name__, str(exc)[:240])
            used.append("%s:%s" % (endpoint["name"], last_error[:80]))
            break
    return {
        "ok": False, "error": last_error,
        "endpoint": endpoint["name"], "used": used,
    }


def _kimi_race(endpoints, body, timeout):
    """Fire every remaining channel at once; first ok body wins."""
    timeout = float(timeout or 540)
    used = []
    last = {"ok": False, "error": "kimi_all_endpoints_failed", "used": used}
    pool = ThreadPoolExecutor(max_workers=max(1, len(endpoints)))
    futs = [
        pool.submit(_kimi_post_one, endpoint, body, timeout, 0)
        for endpoint in endpoints
    ]
    try:
        wait_s = timeout + float(WALL_CLOCK_GRACE_SEC) + 2.0
        for fut in as_completed(futs, timeout=wait_s):
            try:
                posted = fut.result()
            except Exception as exc:
                posted = {
                    "ok": False,
                    "error": "%s:%s" % (type(exc).__name__, str(exc)[:240]),
                    "used": [],
                }
            used.extend(posted.get("used") or [])
            if posted.get("ok"):
                posted = dict(posted)
                posted["used"] = list(used)
                posted["raced"] = True
                return posted
            last = dict(posted)
            last["used"] = list(used)
            last["raced"] = True
        last["used"] = list(used)
        last["raced"] = True
        return last
    except FuturesTimeout:
        return {
            "ok": False,
            "error": "kimi_all_endpoints_timeout",
            "used": used or ["race_timeout"],
            "raced": True,
        }
    finally:
        pool.shutdown(wait=False)


def kimi_post_named(endpoint_name, body, timeout=540):
    """POST preferred named invent mouth first.

    Preferred may be kimi primary/backup or congestion outlets qwen/deepseek.
    On 429 / tpm / quota / other failover-class faults, try the remaining
    invent endpoints sequentially (does not race).
    """
    name = str(endpoint_name or "").strip()
    chain = invent_endpoint_chain()
    preferred = None
    others = []
    for row in chain:
        if row.get("name") == name:
            preferred = row
        else:
            others.append(row)
    if preferred is None:
        return {
            "ok": False,
            "error": "kimi_endpoint_not_configured:%s" % name,
            "used": [],
        }
    posted = _kimi_post_one(
        preferred, body, timeout, SAME_ENDPOINT_TRANSIENT_RETRIES,
    )
    if posted.get("ok"):
        return posted
    err = str(posted.get("error") or "")
    if not others or not kimi_should_failover(err):
        return posted
    used = list(posted.get("used") or [])
    last = dict(posted)
    for row in others:
        alt = _kimi_post_one(
            row, body, timeout, SAME_ENDPOINT_TRANSIENT_RETRIES,
        )
        used.extend(alt.get("used") or [])
        if alt.get("ok"):
            alt = dict(alt)
            alt["used"] = used
            alt["failover"] = True
            alt["failover_from"] = preferred.get("name")
            return alt
        last = dict(alt)
        last["used"] = used
        last["failover"] = True
        last["failover_from"] = preferred.get("name")
    return last


def kimi_post_json(body, timeout=540, exclude=None):
    """POST one OpenAI-compatible chat body.

    Two configured channels race. A lone channel still retries 502/503/504
    once. Do not sit 540s on primary before touching api2.
    """
    skip = set(exclude or [])
    endpoints = [row for row in kimi_endpoint_chain() if row.get("name") not in skip]
    if not endpoints:
        return {"ok": False, "error": "kimi_provider_not_configured"}
    if len(endpoints) >= 2:
        return _kimi_race(endpoints, body, timeout)
    return _kimi_post_one(
        endpoints[0], body, timeout, SAME_ENDPOINT_TRANSIENT_RETRIES,
    )
