# -*- coding: utf-8 -*-
"""Human-only remaining-path guidance. Never amends stops. Never places orders.

Published 止盈/止损/定时 stay on remaining_path_odds. This module only
sanitizes a DeepSeek action enum into one line for Wx / 观风金球.
"""
from __future__ import print_function

import re

from dual_engine_workflow_v2.path_lexicon import (
    LEGACY_STOP_LOSS,
    LEGACY_TAKE_PROFIT,
    LEGACY_TIMED,
)
from dual_engine_workflow_v2.review_lexicon import scrub as _scrub

HOLD_TO_PLAN = "hold_to_plan"
LOCK_PROFIT = "lock_profit_manual"
CUT_LOSS = "cut_loss_manual"
WAIT_TRAIL = "wait_trail"
WATCH_GIVEBACK = "watch_giveback"
NO_CALL = "no_call"

ACTIONS = (
    HOLD_TO_PLAN, LOCK_PROFIT, CUT_LOSS, WAIT_TRAIL, WATCH_GIVEBACK, NO_CALL,
)

SKELETONS = {
    HOLD_TO_PLAN: "建议继续交给本策略退出计划。",
    LOCK_PROFIT: "建议人工了结锁定利润；系统不改保护止损。",
    CUT_LOSS: "建议人工了结，避免把剩余路径交给保护止损。",
    WAIT_TRAIL: "建议再拿到追踪激活，再让计划锁定。",
    WATCH_GIVEBACK: "建议优先防转亏，考虑人工了结。",
    NO_CALL: "暂不给动作建议。",
}

GIVEBACK_WATCH = 0.35
AMEND_RE = re.compile(
    r"改止损|修改止损|下调止损|上调止损|移动止损|撤止损|把止损|"
    r"新止损|止损改到|止损设|amend.{0,12}stop",
    re.I,
)
AUTO_RE = re.compile(
    r"自动平仓|系统平仓|立即平仓|(?<!人工)市价平|平掉这",
    re.I,
)
PCT_RE = re.compile(r"(止盈|止损)\s*(\d+(?:\.\d+)?)\s*%")
RSI_RE = re.compile(r"\bRSI\b|相对强弱", re.I)

SYSTEM_PROMPT = (
    "你只写一行给人类的持仓指引，系统不会按你的话下单或改止损。"
    "先触止盈/止损/定时已经算死，必须原样引用 pack.published，禁止另造概率。"
    "只能从给定 action 枚举选一个。"
    "主语必须是「建议人工…」或「建议继续交给本策略退出计划」。"
    "禁止建议改/移/撤交易所保护止损，禁止建议系统自动平仓。"
    "必须引用 pack.counterfactual_zh。"
    "对外只用止盈/止损/定时，不要写先盈/先亏/到期。不要写 RSI。"
    "必须原样返回 evidence_hash。"
    "只输出一个JSON对象，不要Markdown。"
)


def _safe_float(v, default=None):
    try:
        if v in (None, ""):
            return default
        out = float(v)
        if out != out or abs(out) == float("inf"):
            return default
        return out
    except Exception:
        return default


def _round_px(v):
    v = _safe_float(v)
    if v is None:
        return None
    if abs(v) >= 100:
        return round(v, 2)
    if abs(v) >= 10:
        return round(v, 3)
    return round(v, 4)


def pnl_state(side, entry, mark):
    side = str(side or "long").lower()
    entry = _safe_float(entry)
    mark = _safe_float(mark)
    if entry is None or mark is None or not entry:
        return None
    if side == "short":
        delta = (entry - mark) / entry
    else:
        delta = (mark - entry) / entry
    if delta > 0.0003:
        return "profit"
    if delta < -0.0003:
        return "loss"
    return "flat"


def counterfactual_zh(barriers, path_vote=None):
    barriers = barriers if isinstance(barriers, dict) else {}
    path_vote = path_vote if isinstance(path_vote, dict) else {}
    p_tp = _safe_float(path_vote.get("p_take_profit"))
    p_sl = _safe_float(path_vote.get("p_stop"))
    p_td = _safe_float(path_vote.get("p_timed"))
    hits = []
    if barriers.get("take_profit_price") is not None:
        hits.append("止盈位")
    if barriers.get("trail_armed") and barriers.get("trail_price") is not None:
        hits.append("追踪")
    elif barriers.get("breakeven_armed"):
        hits.append("保本")
    if barriers.get("stop") is not None:
        hits.append("保护止损")
    hits.append("定时")
    likely = "保护止损"
    if p_tp is not None and p_sl is not None:
        if p_td is not None and p_td >= max(p_tp, p_sl):
            likely = "定时"
        elif p_tp >= p_sl:
            likely = "止盈"
        else:
            likely = "止损"
    return (
        "若人工不动，本策略下一步只会撞%s之一；当前先触更可能是%s。"
        % ("/".join(hits), likely)
    )


def build_context(snap=None, struct=None, path_vote=None, receipts=None):
    snap = snap if isinstance(snap, dict) else {}
    struct = struct if isinstance(struct, dict) else {}
    path_vote = path_vote if isinstance(path_vote, dict) else {}
    barriers = snap.get("exit_barriers") if isinstance(snap.get("exit_barriers"), dict) else {}
    formulas = struct.get("formulas") if isinstance(struct.get("formulas"), dict) else {}
    gd = formulas.get("giveback_dynamics") if isinstance(formulas.get("giveback_dynamics"), dict) else {}
    sw = formulas.get("swing") if isinstance(formulas.get("swing"), dict) else {}
    mom = formulas.get("momentum") if isinstance(formulas.get("momentum"), dict) else {}
    vol = formulas.get("volume") if isinstance(formulas.get("volume"), dict) else {}
    wk = formulas.get("weakening") if isinstance(formulas.get("weakening"), dict) else {}
    side = snap.get("side") or barriers.get("side")
    entry = _safe_float(snap.get("entry_price"), barriers.get("entry"))
    mark = _safe_float(snap.get("mark_price"), barriers.get("mark"))
    stop = _safe_float(snap.get("stop_loss_price"), barriers.get("stop"))
    tp = _safe_float(barriers.get("take_profit_price"))
    giveback = _safe_float(struct.get("giveback_frac"), gd.get("giveback_frac"))
    pnl = pnl_state(side, entry, mark)
    return {
        "pnl": pnl,
        "side": side,
        "entry": entry,
        "mark": mark,
        "stop": stop,
        "take_profit_price": tp,
        "dist_tp": None if None in (mark, tp) else abs(tp - mark),
        "dist_stop": None if None in (mark, stop) else abs(mark - stop),
        "trail_armed": bool(barriers.get("trail_armed")),
        "has_trailing": bool(barriers.get("has_trailing") or barriers.get("trail_price")),
        "trail_near": bool(barriers.get("trail_near")),
        "trail_activation_price": barriers.get("trail_activation_price"),
        "bars_left": barriers.get("bars_left"),
        "giveback_frac": giveback,
        "giveback_bars": gd.get("giveback_bars"),
        "swing_state": sw.get("swing_state"),
        "cci": mom.get("cci"),
        "k": mom.get("k"),
        "j": mom.get("j"),
        "vol_ratio_down": vol.get("vol_ratio_down"),
        "weakening_intensity": wk.get("weakening_intensity"),
        "p_take_profit": path_vote.get("p_take_profit"),
        "p_stop": path_vote.get("p_stop"),
        "p_timed": path_vote.get("p_timed"),
        "receipts_note": "同策略回执只对照，不是现仓概率",
        "receipts": receipts,
        "counterfactual_zh": counterfactual_zh(barriers, path_vote),
    }


def allowed_actions(context):
    context = context if isinstance(context, dict) else {}
    pnl = context.get("pnl")
    allowed = [HOLD_TO_PLAN, NO_CALL]
    if pnl == "profit":
        allowed.append(LOCK_PROFIT)
        giveback = _safe_float(context.get("giveback_frac"))
        if giveback is not None and giveback >= GIVEBACK_WATCH:
            allowed.append(WATCH_GIVEBACK)
    if pnl == "loss":
        allowed.append(CUT_LOSS)
    if (
        context.get("has_trailing")
        and not context.get("trail_armed")
        and context.get("trail_near")
    ):
        allowed.append(WAIT_TRAIL)
    out = []
    seen = set()
    for action in ACTIONS:
        if action in allowed and action not in seen:
            seen.add(action)
            out.append(action)
    return out


def _odds_contradict(text, context):
    p_tp = _safe_float(context.get("p_take_profit"))
    p_sl = _safe_float(context.get("p_stop"))
    if p_tp is None and p_sl is None:
        return False
    for kind, raw in PCT_RE.findall(text or ""):
        try:
            pct = float(raw)
        except Exception:
            continue
        if pct > 1.5:
            frac = pct / 100.0
        else:
            frac = pct
        if kind == "止盈" and p_tp is not None and abs(frac - p_tp) > 0.04:
            return True
        if kind == "止损" and p_sl is not None and abs(frac - p_sl) > 0.04:
            return True
    return False


def _illegal_text(text):
    blob = str(text or "")
    if AMEND_RE.search(blob):
        return "amend_stop"
    if AUTO_RE.search(blob):
        return "auto_close"
    if RSI_RE.search(blob):
        return "rsi"
    if LEGACY_TAKE_PROFIT in blob or LEGACY_STOP_LOSS in blob:
        return "legacy_lexicon"
    if LEGACY_TIMED in blob:
        return "legacy_lexicon"
    return None


def _normalize_clause(text):
    return re.sub(r"\s+", "", str(text or "").strip())


def _strip_skeleton_prefix(skeleton, extra):
    """Remove one or more leading copies of the action skeleton from model text."""
    extra = str(extra or "").strip()
    sk = str(skeleton or "").strip()
    if not extra or not sk:
        return extra
    sk_norm = _normalize_clause(sk.rstrip("。；，, "))
    if not sk_norm:
        return extra
    changed = True
    while changed and extra:
        changed = False
        if not _normalize_clause(extra).startswith(sk_norm):
            break
        cut = None
        for i in range(1, len(extra) + 1):
            if _normalize_clause(extra[:i]) == sk_norm:
                cut = i
                break
        if cut is None:
            break
        extra = extra[cut:].lstrip("。；，, ")
        changed = True
    return extra.strip()


def _dedupe_clauses(text):
    """Drop repeated sentence/clause fragments while preserving order."""
    blob = str(text or "").strip()
    if not blob:
        return blob
    parts = re.split(r"(?<=[。；])", blob)
    out = []
    seen = set()
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        key = _normalize_clause(piece)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(piece if piece.endswith(("。", "；")) else piece)
    merged = "".join(out).strip()
    return merged or blob


def format_line(action, model_text=None):
    action = str(action or NO_CALL)
    if action not in SKELETONS:
        action = NO_CALL
    skeleton = SKELETONS[action]
    extra = _scrub(str(model_text or "").strip())
    extra = extra.replace("\n", " ").strip()
    if extra.startswith("智能指引"):
        extra = extra.split("：", 1)[-1].strip()
    extra = _strip_skeleton_prefix(skeleton, extra)
    # Models often paste counterfactual_zh; keep it out of the one-line guide.
    cf_prefix = _normalize_clause("若人工不动")
    while extra and _normalize_clause(extra).startswith(cf_prefix):
        extra = re.sub(
            r"^若人工不动[^。；]*[。；]\s*",
            "",
            extra,
            count=1,
        ).strip()
    extra = _dedupe_clauses(extra)
    extra = _strip_skeleton_prefix(skeleton, extra)
    extra = extra[:60]
    if action == NO_CALL or not extra:
        return skeleton
    text = skeleton.rstrip("。") + "。" + extra
    return _dedupe_clauses(text)[:120]


def no_call(reason=None):
    return {
        "ok": False,
        "action": NO_CALL,
        "guidance_zh": None,
        "text_zh": None,
        "error": reason,
        "allowed": list(ACTIONS),
    }


def sanitize(raw, context, expected_hash=None):
    context = context if isinstance(context, dict) else {}
    raw = raw if isinstance(raw, dict) else {}
    allowed = allowed_actions(context)
    action = str(raw.get("action") or "").strip()
    text = raw.get("guidance_zh") or raw.get("reason_zh") or raw.get("text_zh")
    got_hash = str(raw.get("evidence_hash") or "").strip()
    want = str(expected_hash or "").strip()
    if want and got_hash and got_hash != want:
        failed = no_call("evidence_hash_mismatch")
        failed["allowed"] = allowed
        return failed
    if action not in ACTIONS:
        action = NO_CALL
    if action not in allowed:
        failed = no_call("action_not_allowed:" + (action or "missing"))
        failed["allowed"] = allowed
        return failed
    line = format_line(action, text)
    bad = _illegal_text(line) or _illegal_text(text)
    if bad:
        failed = no_call("forbidden_" + bad)
        failed["allowed"] = allowed
        return failed
    if _odds_contradict(line, context) or _odds_contradict(str(text or ""), context):
        failed = no_call("odds_contradiction")
        failed["allowed"] = allowed
        return failed
    show = None if action == NO_CALL else line
    return {
        "ok": True,
        "action": action,
        "guidance_zh": show,
        "text_zh": show,
        "error": None,
        "allowed": allowed,
    }


def parse_vote(parsed):
    parsed = parsed if isinstance(parsed, dict) else {}
    action = str(parsed.get("action") or "").strip()
    text = parsed.get("guidance_zh") or parsed.get("reason_zh") or ""
    return {
        "ok": action in ACTIONS,
        "action": action if action in ACTIONS else None,
        "guidance_zh": str(text or "")[:160],
        "evidence_hash": str(parsed.get("evidence_hash") or "").strip() or None,
        "error": None if action in ACTIONS else "missing_action",
    }


def evidence_pack(context, evidence_hash, allowed, clocks=None):
    context = context if isinstance(context, dict) else {}
    return {
        "task": (
            "根据已经算死的先触概率与退出障碍，选出一个 action，写不超过60字的人话。"
            "必须引用 counterfactual_zh。禁止另造止盈/止损数字。禁止改保护止损。"
        ),
        "required_json": {
            "action": "枚举之一：" + ",".join(allowed),
            "guidance_zh": "不超过60字，建议人工或建议继续交给计划",
            "evidence_hash": "必须原样抄写",
        },
        "evidence_hash": evidence_hash,
        "clock": clocks,
        "allowed_actions": allowed,
        "skeletons": {k: SKELETONS[k] for k in allowed},
        "pack": {
            "published": {
                "p_take_profit": context.get("p_take_profit"),
                "p_stop": context.get("p_stop"),
                "p_timed": context.get("p_timed"),
            },
            "pnl": context.get("pnl"),
            "entry": context.get("entry"),
            "mark": context.get("mark"),
            "stop": context.get("stop"),
            "take_profit_price": context.get("take_profit_price"),
            "dist_tp": _round_px(context.get("dist_tp")),
            "dist_stop": _round_px(context.get("dist_stop")),
            "trail_armed": context.get("trail_armed"),
            "trail_near": context.get("trail_near"),
            "trail_activation_price": context.get("trail_activation_price"),
            "bars_left": context.get("bars_left"),
            "giveback_frac": context.get("giveback_frac"),
            "giveback_bars": context.get("giveback_bars"),
            "swing_state": context.get("swing_state"),
            "cci": context.get("cci"),
            "k": context.get("k"),
            "j": context.get("j"),
            "vol_ratio_down": context.get("vol_ratio_down"),
            "weakening_intensity": context.get("weakening_intensity"),
            "receipts_note": context.get("receipts_note"),
            "receipts": context.get("receipts"),
            "counterfactual_zh": context.get("counterfactual_zh"),
        },
        "note_zh": (
            "这是人工作用建议，不是第二套概率。"
            "系统不会下单、不会改保护止损。"
        ),
    }
