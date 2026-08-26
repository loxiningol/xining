# -*- coding: utf-8 -*-
"""独立安全网模块。

只对外提供额度、锁定与规则快照，不承担开平仓，也不进入策略创造主链。
守护进程仍是 auto_trade_force_protect；本模块只读其日状态文件。
"""
from __future__ import print_function

import json
import os
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
AUTO_DIR = ROOT / "auto_trade"
STATE_PATH = AUTO_DIR / "force_protect_state.json"

MODULE_NAME = "安全网"
MANUAL_OPEN_LIMIT = 2
HITCH_EXTRA_LIMIT = 3
DANGER_LIMIT = 3
HALF_RATIO = 0.50
WARN_RATIO = 0.50
FORCE_CLOSE_RATIO = 0.75
MAX_LEVERAGE = 30.0


def beijing_date(now_ts=None):
    ts = time.time() if now_ts is None else float(now_ts)
    return datetime.utcfromtimestamp(ts + 8 * 3600).strftime("%Y-%m-%d")


def remaining(count, limit):
    used = int(count or 0)
    if used < 0:
        used = 0
    left = int(limit) - used
    if left < 0:
        left = 0
    return left


def _read_state():
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def _quota_block(used, limit, label):
    used = int(used or 0)
    if used < 0:
        used = 0
    cap = int(limit)
    left = remaining(used, cap)
    return {
        "label": label,
        "used": used,
        "limit": cap,
        "remaining": left,
        "exhausted": left <= 0,
        "ratio": (float(used) / float(cap)) if cap > 0 else 0.0,
        "text": "已开 %s/%s，剩余 %s" % (used, cap, left),
    }


def status_snapshot(now_ts=None, state=None):
    """Frontend/backend contract for the 安全网 module."""
    today = beijing_date(now_ts)
    raw = dict(state) if isinstance(state, dict) else _read_state()
    same_day = str(raw.get("beijing_date") or "") == today
    if same_day:
        manual_used = int(raw.get("manual_open_count") or 0)
        hitch_used = int(raw.get("hitch_extra_count") or 0)
        danger_used = int(raw.get("danger_count") or 0)
        locked = bool(raw.get("lock_to_funding"))
        updated_at = raw.get("updated_at") or ""
    else:
        manual_used = 0
        hitch_used = 0
        danger_used = 0
        locked = False
        updated_at = ""
    manual = _quota_block(manual_used, MANUAL_OPEN_LIMIT, "手动开仓限额")
    hitch = _quota_block(hitch_used, HITCH_EXTRA_LIMIT, "顺风车开仓限额")
    hitch["usable"] = (not locked) and manual["exhausted"] and hitch["remaining"] > 0
    hitch["note"] = "常规次数用尽后，与仍持仓的自动仓同标的同方向可再开%s次" % HITCH_EXTRA_LIMIT
    danger_left = remaining(danger_used, DANGER_LIMIT)
    summary = "手动 %s/%s · 顺风车 %s/%s" % (
        manual["used"], manual["limit"], hitch["used"], hitch["limit"],
    )
    if locked:
        summary = "日锁定中 · " + summary
    return {
        "ok": True,
        "module": MODULE_NAME,
        "beijing_date": today,
        "updated_at": updated_at,
        "locked": locked,
        "summary": summary,
        "manual_open": manual,
        "hitch_extra": hitch,
        "danger": {
            "label": "危险介入",
            "used": danger_used,
            "limit": DANGER_LIMIT,
            "remaining": danger_left,
            "lock_on_next": (not locked) and danger_left <= 0,
            "text": "今日 %s/%s" % (danger_used, DANGER_LIMIT),
        },
        "rules": {
            "manual_size_warn": "手动单笔占用超过50%、不超过75%仅发消息，不干预交易，不计入危险信号",
            "manual_over_force_close": "手动单笔占用超过75%无条件平仓并计入危险信号",
            "leverage_gt_30x": "杠杆大于30x无条件平仓",
            "manual_open_limit": "每个北京自然日手动开仓最多%s次" % MANUAL_OPEN_LIMIT,
            "hitch_extra": hitch["note"],
            "day_lock": "危险介入满%s次即日锁定，次日0点自动解除" % DANGER_LIMIT,
        },
    }
