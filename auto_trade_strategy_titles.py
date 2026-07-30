# -*- coding: utf-8 -*-
"""Strategy display titles for WxPusher / UI.

All user-facing notifications should show readable titles, not raw keys
like xau15_h1_breakout_long_ai. Operational CLI tokens (--confirm/--reject)
keep the raw key.
"""
from __future__ import print_function

import json
import os
import re
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
DSL_CONFIG_PATH = ROOT / "strategy_configs" / "ai_dsl_strategies.json"
EXP_CONFIG_PATH = ROOT / "strategy_configs" / "experimental_strategies.json"

_FALLBACK = {
    "ema6_center_down_then_fall": "EMA6居中后再下行",
    "ema7_center_down_short": "EMA6居中后再下行",
    "conventional_up_break_long": "常规上升排列突破",
    "early_downtrend_ema6_ema75_short": "EMA19反抽失败·EMA6/EMA75同步破位",
    "conventional_down_arrangement_bottom_up_long": "常规下跌排列筑底上行",
    "cci_75_100": "EMA7上升趋势回踩续涨",
    "ema53_liquidity_sweep_reclaim_long": "EMA53缓升｜36小时低点扫荡收回（AI创造）",
    "ema8_mainwave_long": "EMA8主升浪",
    "cci_neg60_neg110_short": "CCI负60-负110下行中再下行",
    "btc15_dual_cycle_downtrend_reentry_short_ai": "双周期下跌加速再死叉（AI创造）",
    "conventional_up_arrangement_valid_death_cross_short": "常规上升排列有效死叉",
    "btc5_exhaustion_reclaim_long_ai": "BTC 5分钟超跌收回（AI创造）",
    "cl5_exhaustion_fade_short_ai": "CL 5分钟冲高衰竭回落（AI创造）",
    "ng5_exhaustion_fade_short_ai": "NG 5分钟冲高衰竭回落（AI创造）",
    "ng5_session_exhaustion_reclaim_long_ai": "NG 5分钟时段超跌收回（AI创造）",
    "xag5_session_breakdown_short_ai": "XAG 5分钟时段顺势破位（AI创造）",
    "ltc5_exhaustion_fade_short_ai": "LTC 5分钟冲高衰竭回落（AI创造）",
    "ada5_session_trend_pullback_short_ai": "ADA 5分钟时段趋势反抽（AI创造）",
    "ada5_z20_t60_prev_h14_0724k": "ADA 5分钟 H1趋势回踩续涨",
    "codex0725_ada5_trendpb_r42_z2p0_h14": "ADA5趋势回踩·0725GLM",
    "codex0725t2_ada5m_trendpb_r42_z2p2_h14": "ADA5顺势回升·0725T2",
    "codex0725t3_ada5m_trendpb_r42_z2p3_h14": "ADA5顺势回升·0725T3",
    "xau15_h1_breakout_long_ai": "XAU 15分钟顺势放量突破（AI创造）",
    "frost_xrp_rescue_h20_t45": "[XRP 15m] 动量衰竭反转 (Exhaustion Fade)",
    "frost3_btc1h_xrpport_exhaustion_fade_slope": "[BTC 1h] 动量衰竭反转 (Exhaustion Fade)",
}

_CACHE = None
_CACHE_MTIME = None


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def _config_mtime():
    total = 0.0
    for path in (
        DSL_CONFIG_PATH,
        EXP_CONFIG_PATH,
        ROOT / "auto_trade" / "strategy_pending_human_confirm.json",
    ):
        try:
            total += Path(path).stat().st_mtime
        except Exception:
            pass
    return total


def strategy_title_map():
    global _CACHE, _CACHE_MTIME
    mtime = _config_mtime()
    if _CACHE is not None and _CACHE_MTIME == mtime:
        return _CACHE
    names = dict(_FALLBACK)
    for path in (DSL_CONFIG_PATH, EXP_CONFIG_PATH):
        doc = _read(path, {"strategies": []})
        for row in (doc.get("strategies") or []):
            if not isinstance(row, dict):
                continue
            key = row.get("key") or row.get("strategy_key")
            if not key:
                continue
            title = (row.get("name") or row.get("strategy_name")
                     or row.get("title") or "").strip()
            # Ignore name==key placeholders so _FALLBACK can win.
            if title and title != str(key):
                names[str(key)] = title
    # Pending human-confirm queue also carries display names.
    pending_path = ROOT / "auto_trade" / "strategy_pending_human_confirm.json"
    pending = _read(pending_path, {"items": []})
    for row in (pending.get("items") or []):
        if not isinstance(row, dict):
            continue
        key = row.get("key") or row.get("strategy_key")
        title = (row.get("name") or "").strip()
        if key and title and title != str(key):
            names[str(key)] = title
    _CACHE = names
    _CACHE_MTIME = mtime
    return names


def strategy_display_name(strategy_key, fallback_name=None):
    key = str(strategy_key or "").strip()
    if not key:
        return str(fallback_name or "").strip() or ""
    title = strategy_title_map().get(key)
    if title and title != key:
        return title
    fb = str(fallback_name or "").strip()
    if fb and fb != key:
        return fb
    if title:
        return title
    return key


def resolve_strategy_name(strategy_key, strategy_name=None):
    """Prefer readable title; never leave a bare code key when a title exists."""
    key = str(strategy_key or "").strip()
    name = str(strategy_name or "").strip()
    if name and name != key:
        titled = strategy_display_name(key, name)
    else:
        titled = strategy_display_name(key, None)
    if titled and titled != key:
        return titled
    if name and name != key:
        return name
    return titled or name or key or "未命名策略"


def short_strategy_title(strategy_key, strategy_name=None):
    """Wx-friendly short title: drop train/version suffixes like ·0725GLM."""
    title = resolve_strategy_name(strategy_key, strategy_name)
    # Strip common train/version tails for cleaner Wx lines.
    title = re.sub(r"[·・]0725\w*$", "", title).strip()
    title = re.sub(r"[·・]train\d+\w*$", "", title, flags=re.I).strip()
    title = re.sub(r"[·・]?0?7\.?\d{2}[A-Za-z0-9]*$", "", title).strip()
    title = re.sub(r"（AI创造）\s*$", "", title).strip()
    return title or resolve_strategy_name(strategy_key, strategy_name)


def _looks_like_raw_code(text):
    s = str(text or "").strip()
    if not s:
        return True
    if re.search(r"[\u4e00-\u9fff]", s):
        return False
    if "[" in s and "]" in s:
        return False
    # raw keys: underscores + alnum, or param soup like sol_tp47_h32
    if "_" in s or re.search(r"(tp|h|r|c)\d+", s, re.I):
        return True
    return bool(re.fullmatch(r"[A-Za-z0-9._:-]{8,}", s))


def _needs_semantic_wash(text):
    """True if title still carries raw mechanism/param codes needing NL wash."""
    s = str(text or "")
    if _looks_like_raw_code(s):
        return True
    low = s.lower()
    if any(tok in low for tok in (
        "exhaustion_fade", "xrpport", "trendpb", "_tp", "_ad",
        "session_vol", "h32_r", "sol_tp", "btc1h-",
    )):
        return True
    if re.search(r"(tp|h|r|c)\d+", s, re.I) and ("_" in s or "-" in s):
        return True
    return False


def humanize_creation_title(key_or_title, symbol=None, timeframe=None, family=None):
    """Dashboard-facing NL title for creation / formal queue rows.

    Prefers mapped display names; otherwise synthesizes
    ``[SYM TFm] Causal action (Scene)`` from family/key tokens.
    """
    raw = str(key_or_title or "").strip()
    mapped = short_strategy_title(raw, raw) if raw else ""
    if mapped and not _needs_semantic_wash(mapped):
        return mapped
    try:
        from scripts.auto_driver.live_status import humanize_mechanism_title
    except Exception:
        try:
            from auto_driver.live_status import humanize_mechanism_title  # type: ignore
        except Exception:
            humanize_mechanism_title = None
    fam = family or re.sub(r"_ad\d+$", "", raw)
    # peel known noise prefixes from frost/train / 寒霜 display keys
    fam = re.sub(r"^(frost\d*_?|codex\d*t?\d*_?)", "", fam, flags=re.I)
    fam = re.sub(r"^寒霜[^A-Za-z0-9_-]*", "", fam)
    # 寒霜叁W2g-sol_tp47_h32… → sol_tp47_h32…
    m = re.search(r"([a-z]{2,6}(?:_[a-z]*\d+){2,})", fam, flags=re.I)
    if m and ("exhaustion" not in fam.lower() and "fade" not in fam.lower()):
        fam = m.group(1)
    if humanize_mechanism_title:
        sym = symbol or ""
        tf = timeframe or ""
        # try extract symbol/tf from key if missing
        if not sym:
            m = re.search(r"(?i)\b(btc|eth|sol|ada|xrp|ltc|ng|cl|xau|xag|doge|bnb)\b", raw)
            if m:
                sym = m.group(1).upper()
        if not tf:
            m = re.search(r"(?i)(\d+[mh])\b", raw)
            if m:
                tf = m.group(1).lower()
        return humanize_mechanism_title(sym or "?", tf or "?", fam or raw)
    return mapped or raw or "未命名策略"


def _normalize_grade_label(grade):
    """Normalize grade for live cards. Applied strategies default to B.

    Never surface 未评级/待评级 on live roster cards — user rule: every
    mounted auto-trade strategy is graded, initial default B.
    """
    g = str(grade or "").strip()
    if not g:
        return "B"
    gu = g.upper()
    if gu in ("S", "A", "B", "C", "D", "E"):
        return gu
    if gu in ("SHADOW", "READ_ONLY_SHADOW", "LIFECYCLE_SHADOW") or g == "shadow":
        return "SHADOW"
    if g in ("未评级", "待评级") or gu in ("UNRATED", "NONE", "NULL", "PENDING"):
        return "B"
    if g in ("deleted", "replaced") or gu in ("DELETED", "REPLACED"):
        return gu
    return g


def format_live_strategy_card(strategy_key, strategy_name=None, grade=None,
                              max_position_ratio=None,
                              ai_theoretical_wr_avg=None,
                              ai_theoretical_mean_net_avg=None,
                              actual_single_trade_pnl_pct=None):
    """Natural annotated block for live/runtime roster (Wx + UI text).

    Example:
      ADA5顺势回升
      B
      仓位 30%
      三AI理论胜率 74.3%
      三AI理论盈利单盈利率 +7.2%
    """
    title = short_strategy_title(strategy_key, strategy_name)
    lines = [title or str(strategy_key or "未命名策略")]
    lines.append(_normalize_grade_label(grade))
    try:
        if max_position_ratio is not None and max_position_ratio != "":
            pct = float(max_position_ratio) * 100.0
            lines.append("仓位 %.0f%%" % pct)
    except Exception:
        pass
    try:
        if ai_theoretical_wr_avg is not None and ai_theoretical_wr_avg != "":
            lines.append("三AI理论胜率 %.1f%%" % float(ai_theoretical_wr_avg))
    except Exception:
        pass
    try:
        if (ai_theoretical_mean_net_avg is not None
                and ai_theoretical_mean_net_avg != ""):
            lines.append(
                "三AI理论盈利单盈利率 %+.3f%%" % float(ai_theoretical_mean_net_avg)
            )
    except Exception:
        pass
    try:
        if actual_single_trade_pnl_pct is not None and actual_single_trade_pnl_pct != "":
            val = float(actual_single_trade_pnl_pct)
            lines.append("实际单笔盈利率 %+.1f%%" % val)
    except Exception:
        pass
    return "\n".join(lines)


def format_live_roster_text(rows, heading="【运行中策略】"):
    """Join multiple assignment/stat rows into a Wx-friendly roster."""
    blocks = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key = row.get("strategy_key") or row.get("key") or row.get("name")
        blocks.append(format_live_strategy_card(
            key,
            row.get("strategy_name") or row.get("name"),
            grade=row.get("lifecycle_grade") or row.get("grade")
            or row.get("max_grade"),
            max_position_ratio=row.get("max_position_ratio"),
            ai_theoretical_wr_avg=row.get("ai_theoretical_wr_avg"),
            ai_theoretical_mean_net_avg=row.get("ai_theoretical_mean_net_avg"),
            actual_single_trade_pnl_pct=row.get("actual_single_trade_pnl_pct"),
        ))
    if not blocks:
        return heading + "\n（暂无）"
    return heading + "\n\n" + "\n\n".join(blocks)


def rewrite_strategy_keys_in_text(text):
    """Replace raw strategy keys with readable titles for Wx content.

    Preserves keys used by operational CLI tokens:
      --confirm KEY / --reject KEY
    Also rewrites standalone `Key: <raw>` lines to the title only.
    """
    if text is None:
        return text
    message = str(text)
    mapping = strategy_title_map()
    if not mapping:
        return message

    protected = []

    def _stash(match):
        protected.append(match.group(0))
        return "\x00WXKEY%d\x00" % (len(protected) - 1)

    # Keep operational command keys intact.
    message = re.sub(
        r"--(?:confirm|reject)\s+[A-Za-z0-9_\-]+",
        _stash,
        message,
    )

    def _key_line(match):
        raw = match.group(1)
        title = short_strategy_title(raw, mapping.get(raw) or raw)
        if title and title != raw:
            return title
        return match.group(0)

    # "Key: codex0725_..." → title only (user-facing)
    message = re.sub(
        r"(?im)^\s*Key:\s*([A-Za-z0-9_\-]+)\s*$",
        _key_line,
        message,
    )

    for key in sorted(mapping.keys(), key=len, reverse=True):
        title = mapping.get(key) or ""
        if not key or not title or title == key:
            continue
        if key not in message:
            continue
        message = message.replace(key, title)

    for idx, raw in enumerate(protected):
        message = message.replace("\x00WXKEY%d\x00" % idx, raw)
    return message
