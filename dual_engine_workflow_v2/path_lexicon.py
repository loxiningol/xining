# -*- coding: utf-8 -*-
"""Canonical path-exit lexicon (对外中文).

路径三种结果（禁止再用旧称）：
  止盈  ← 旧称「先盈」；内部 first_touch=target / profit_first=1
  止损  ← 旧称「先亏」；内部 first_touch=stop* / profit_first=0
  定时  ← 旧称「到期」；内部 first_touch=unresolved / profit_first=-1

策略有效期「届满/过期」不是路径结果，不要用「定时」替换。
内部英文字段名可保留；对外 / AI / Wx / 报表 / 聊天必须经本模块。
"""
from __future__ import print_function

import re

PATH_TAKE_PROFIT = "止盈"
PATH_STOP_LOSS = "止损"
PATH_TIMED = "定时"

# Legacy → canonical (path outcome only)
LEGACY_TAKE_PROFIT = "先盈"
LEGACY_STOP_LOSS = "先亏"
LEGACY_TIMED = "到期"

FIRST_TOUCH_ZH = {
    "target": PATH_TAKE_PROFIT,
    "stop": PATH_STOP_LOSS,
    "stop_same_bar_as_target": PATH_STOP_LOSS,
    "unresolved": PATH_TIMED,
    "horizon": PATH_TIMED,
    "timed": PATH_TIMED,
    "take_profit": PATH_TAKE_PROFIT,
    "stop_loss": PATH_STOP_LOSS,
    PATH_TAKE_PROFIT: PATH_TAKE_PROFIT,
    PATH_STOP_LOSS: PATH_STOP_LOSS,
    PATH_TIMED: PATH_TIMED,
    LEGACY_TAKE_PROFIT: PATH_TAKE_PROFIT,
    LEGACY_STOP_LOSS: PATH_STOP_LOSS,
    LEGACY_TIMED: PATH_TIMED,
}

PROFIT_FIRST_ZH = {
    1: PATH_TAKE_PROFIT,
    0: PATH_STOP_LOSS,
    -1: PATH_TIMED,
}

# Hold-to-horizon phrasing in briefs / contracts
HOLD_TOKENS_LEGACY = (
    "仅到期平仓",
    "固定持有到期",
    "到期平仓",
)
HOLD_TOKENS_CANONICAL = (
    "仅定时平仓",
    "固定持有定时",
    "定时平仓",
)

_SCRUB_RULES = [
    # longest / most specific first
    (re.compile(r"路径先盈"), "路径止盈"),
    (re.compile(r"路径先亏"), "路径止损"),
    (re.compile(r"路径到期"), "路径定时"),
    (re.compile(r"仅到期平仓"), "仅定时平仓"),
    (re.compile(r"固定持有到期"), "固定持有定时"),
    (re.compile(r"到期平仓"), "定时平仓"),
    (re.compile(r"到期未分晓"), "定时未分晓"),
    (re.compile(r"靠到期平仓"), "靠定时平仓"),
    (re.compile(r"持有到期"), "持有定时"),
    (re.compile(r"先盈"), PATH_TAKE_PROFIT),
    (re.compile(r"先亏"), PATH_STOP_LOSS),
    # Bare「到期」as a path-result token (CSV cells / table cells / 路径：…/到期)
    # Avoid strategy-TTL phrases: 已到期 / 到期删除 / 到期时间 / 有效期到期
    (re.compile(
        r"(?<![已有期效届满过删时])到期(?![删除时间日窗满期届])"
    ), PATH_TIMED),
]


def first_touch_zh(first_touch, profit_first=None):
    """Map internal first_touch / profit_first → 止盈|止损|定时."""
    key = str(first_touch or "").strip()
    if key in FIRST_TOUCH_ZH:
        return FIRST_TOUCH_ZH[key]
    if profit_first is not None:
        try:
            return PROFIT_FIRST_ZH[int(profit_first)]
        except Exception:
            pass
    return PATH_TIMED if key in ("", "None", "none") else key


def profit_first_zh(profit_first):
    try:
        return PROFIT_FIRST_ZH[int(profit_first)]
    except Exception:
        return PATH_TIMED


def scrub(text):
    """Rewrite legacy path-exit wording to canonical 止盈/止损/定时."""
    s = str(text if text is not None else "")
    if not s:
        return s
    for pat, repl in _SCRUB_RULES:
        s = pat.sub(repl, s)
    return s


def column_rename(name):
    """Rename CSV/table column headers that used legacy path terms."""
    return scrub(str(name or ""))
