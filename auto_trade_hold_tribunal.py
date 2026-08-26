# -*- coding: utf-8 -*-
"""Hold-path tribunal: remaining 止盈/止损/定时 from this strategy's exit_plan.

Published odds are first-touch against this strategy's take-profit /
trailing / breakeven, the exchange protective stop, and hold timeout.
DeepSeek-v4 may explain those numbers; it does not invent a take-profit
price. Kernel and morphology are frozen tool evidence.

Directional (方向型) exception: extreme CCI/K/J hard-closes the position
immediately without DeepSeek. Odds (赔率型) never use that path.
Does not amend exchange stop-loss. Does not use RSI in the path kernel.
"""
from __future__ import print_function

import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
AUTO_DIR = ROOT / "auto_trade"
STATE_PATH = AUTO_DIR / "hold_tribunal_state.json"
EVENT_PATH = AUTO_DIR / "hold_tribunal_events.jsonl"

PRIMARY_PROVIDER = "deepseek"
BACKUP_PROVIDER = "qwen"

WARN_TP = 0.20
WARN_SL = 0.60
URGENT_TP = 0.12
URGENT_SL = 0.70
ASK_STOP_CONSUMED = 0.70
SHOCK_REMAINING_FRACTION = 0.50
ASK_COOLDOWN_SEC = 1800.0
NOTIFY_COOLDOWN_SEC = 1800.0
POLL_SEC = 30.0
# No live positions: sleep long (观风金球 closed). Still wake to detect new opens.
IDLE_POLL_SEC = 300.0
SLOT_SEC = 1800.0
TZ_OFFSET_SEC = 8 * 3600.0
TIER_RANK = {None: 0, "warn": 1, "urgent": 2}

SYSTEM_PROMPT = (
    "你只解释已经按本策略退出计划算出的先触概率。"
    "禁止另造止盈位，禁止把程序先验直接当成现仓概率。"
    "止盈=先触本策略止盈/追踪/保本；止损=先触交易所保护止损；定时=持有期满未分晓。"
    "pack.exit_barriers 里有障碍价。has_take_profit 为 false 时止盈概率必须是 0。"
    "禁止建议改止损、禁止建议平仓。"
    "对外只用止盈/止损/定时，不要写先盈/先亏/到期。"
    "必须原样返回证据包中的 evidence_hash。"
    "只输出一个JSON对象，不要Markdown。"
)


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
    EVENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(row or {})
    payload.setdefault("time", _now())
    with EVENT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def classify_tier(p_take_profit, p_stop, morph=None):
    """Return None / 'warn' / 'urgent'. OR of the two sides.

    Original 20/60 and 12/70 cuts stay. P0 adds: heavy giveback + lower-high
    while still green is at least warn, without changing the urgent numbers.
    """
    tp = _safe_float(p_take_profit)
    sl = _safe_float(p_stop)
    urgent = (tp is not None and tp < URGENT_TP) or (sl is not None and sl >= URGENT_SL)
    warn = (tp is not None and tp < WARN_TP) or (sl is not None and sl > WARN_SL)
    morph = morph if isinstance(morph, dict) else {}
    giveback = _safe_float(morph.get("giveback_frac"))
    methods = set(str(x) for x in (morph.get("methods") or []) if x)
    structure_weak = bool(
        morph.get("lower_high")
        or "swing_decay" in methods
        or "giveback" in methods
    )
    if (
        giveback is not None
        and giveback >= 0.60
        and structure_weak
        and not morph.get("close_below_entry")
    ):
        warn = True
    if urgent:
        return "urgent"
    if warn:
        return "warn"
    return None


def cap_backup_tier(tier, provider):
    if str(provider or "") != BACKUP_PROVIDER:
        return tier
    if tier == "urgent":
        return "warn"
    return tier


def stop_distance(entry, stop_px, side):
    entry = _safe_float(entry)
    stop_px = _safe_float(stop_px)
    if entry is None or stop_px is None or entry <= 0:
        return None
    side = str(side or "").lower()
    if side == "long":
        return entry - stop_px
    if side == "short":
        return stop_px - entry
    return None


def adverse_move(entry, extreme_px, side):
    entry = _safe_float(entry)
    extreme_px = _safe_float(extreme_px)
    if entry is None or extreme_px is None or entry <= 0:
        return None
    side = str(side or "").lower()
    if side == "long":
        return entry - extreme_px
    if side == "short":
        return extreme_px - entry
    return None


def stop_consumed(entry, extreme_px, stop_px, side):
    dist = stop_distance(entry, stop_px, side)
    adv = adverse_move(entry, extreme_px, side)
    if dist is None or adv is None or dist <= 0:
        return None
    return max(0.0, adv / dist)


def update_extreme(prev_extreme, mark, side):
    mark = _safe_float(mark)
    prev = _safe_float(prev_extreme)
    if mark is None:
        return prev
    side = str(side or "").lower()
    if prev is None:
        return mark
    if side == "long":
        return min(prev, mark)
    if side == "short":
        return max(prev, mark)
    return mark


def five_minute_shock(side, stop_px, candle):
    """True if the latest 5m bar ate >= 50% of remaining stop (from bar open)."""
    if not isinstance(candle, dict):
        return False
    open_px = _safe_float(candle.get("open"))
    low = _safe_float(candle.get("low"))
    high = _safe_float(candle.get("high"))
    stop_px = _safe_float(stop_px)
    side = str(side or "").lower()
    if open_px is None or stop_px is None:
        return False
    remaining = stop_distance(open_px, stop_px, side)
    if remaining is None or remaining <= 0:
        return False
    if side == "long":
        if low is None:
            return False
        eaten = open_px - low
    elif side == "short":
        if high is None:
            return False
        eaten = high - open_px
    else:
        return False
    return eaten >= (SHOCK_REMAINING_FRACTION * remaining)


def should_ask(consumed, shocked, last_ask_ts, now_ts):
    if not (shocked or (consumed is not None and consumed >= ASK_STOP_CONSUMED)):
        return False
    last_ask_ts = _safe_float(last_ask_ts)
    if last_ask_ts is None:
        return True
    return (float(now_ts) - last_ask_ts) >= ASK_COOLDOWN_SEC


def half_hour_slot_id(now_ts, tz_offset=TZ_OFFSET_SEC, slot_sec=SLOT_SEC):
    """Integer id of the local :00 / :30 slot containing now_ts."""
    local = int(float(now_ts) + float(tz_offset))
    return local // int(slot_sec)


def should_notify(tier, last_tier, last_notify_ts, now_ts, allow=True,
                  last_slot=None, slot=None):
    if not allow:
        return False
    rank = TIER_RANK.get(tier, 0)
    if rank <= 0:
        return False
    if last_slot is not None and slot is not None:
        return last_slot != slot
    last_notify_ts = _safe_float(last_notify_ts)
    if last_notify_ts is None:
        return True
    return (float(now_ts) - last_notify_ts) >= SLOT_SEC


def _prob_from_obj(parsed, key):
    if not isinstance(parsed, dict):
        return None
    v = parsed.get(key)
    x = _safe_float(v)
    if x is None:
        return None
    if x > 1.0 and x <= 100.0:
        x = x / 100.0
    if x < 0 or x > 1:
        return None
    return round(x, 4)


def parse_vote(parsed):
    tp = _prob_from_obj(parsed, "p_take_profit")
    sl = _prob_from_obj(parsed, "p_stop")
    if sl is None:
        sl = _prob_from_obj(parsed, "p_stop_68_10")
    reason = ""
    if isinstance(parsed, dict):
        reason = str(parsed.get("reason_zh") or parsed.get("reason") or "")[:240]
    ok = tp is not None and sl is not None
    ev_hash = ""
    cited = []
    tool_values = None
    if isinstance(parsed, dict):
        ev_hash = str(parsed.get("evidence_hash") or "").strip()
        from auto_trade_hold_assist_gate import parse_cited_tools
        cited = parse_cited_tools(parsed.get("cited_tools"))
        raw_tv = parsed.get("tool_values")
        if isinstance(raw_tv, dict):
            tool_values = raw_tv
    return {
        "ok": ok,
        "p_take_profit": tp,
        "p_stop": sl,
        "reason_zh": reason,
        "evidence_hash": ev_hash or None,
        "cited_tools": cited,
        "tool_values": tool_values,
        "error": None if ok else "missing_probability_fields",
    }


def _flatten_content(value):
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item or ""))
        return "".join(parts)
    return str(value or "")


def _message_text(raw):
    choice = ((raw or {}).get("choices") or [{}])[0] or {}
    message = choice.get("message") or {}
    text = _flatten_content(message.get("content")).strip()
    extra = _flatten_content(
        message.get("reasoning_content") or message.get("reasoning") or ""
    ).strip()
    # Prefer the visible content when it already carries a JSON object.
    # Thinking models often dump long Chinese reasoning without braces; merging
    # that in front of (or instead of) content breaks the JSON extractor.
    if text and "{" in text:
        return text
    if extra and "{" in extra:
        return extra if not text else (text + "\n" + extra)
    return text or extra


def ask_provider(name, evidence):
    import auto_trade_ai_consensus as consensus
    from dual_engine_workflow_v2.review_lexicon import scrub as _scrub

    name = consensus._normalize_provider_name(name)
    cfg = consensus._provider_config(name)
    out = {
        "provider": name,
        "model": cfg.get("model"),
        "ok": False,
        "p_take_profit": None,
        "p_stop": None,
        "reason_zh": None,
        "error": None,
        "latency_sec": None,
    }
    consent = consensus.external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed"):
        out["error"] = "外部AI研究授权缺失或范围不足"
        return out
    if not cfg.get("api_key"):
        out["error"] = "API密钥未配置"
        return out
    user = {
        "task": "对本仓剩余路径给出两个概率（0到1的小数）。",
        "required_json": {
            "p_take_profit": "止盈（含追踪/保本，很少真到5×ATR）",
            "p_stop": "先触交易所保护止损",
            "reason_zh": "不超过120字，必须引用证据里的笔数（若有）",
            "evidence_hash": "必须原样抄写证据包里的 evidence_hash",
            "cited_tools": (
                "至少三个公式族："
                "volume/giveback_dynamics/swing/momentum/volatility/"
                "conditional_receipts；可用时必须含回吐与结构，并含量能或动量"
            ),
            "tool_values": (
                "对象：各族抄写证据包 tools 中你实际用到的数值，"
                "例如 giveback_frac / swing_state / vol_ratio_down / cci"
            ),
        },
        "evidence": evidence,
    }
    timeout = max(int(cfg.get("timeout") or 90), 180)
    started = time.time()

    def _one_shot(thinking_enabled):
        body = {
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": consensus.canonical_json(user)},
            ],
            "temperature": 0,
            "max_tokens": 8192,
        }
        if name == "deepseek":
            body["response_format"] = {"type": "json_object"}
            body["thinking"] = {
                "type": "enabled" if thinking_enabled else "disabled",
            }
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        http_request = urllib_request.Request(
            cfg["url"], data=encoded,
            headers={
                "Authorization": "Bearer " + cfg["api_key"],
                "Content-Type": "application/json",
            },
            method="POST",
        )
        response = consensus._urlopen(http_request, timeout=timeout)
        raw = json.loads(response.read().decode("utf-8"))
        text = _message_text(raw)
        parsed = consensus._parse_content_json(text)
        vote = parse_vote(parsed)
        vote["thinking"] = "enabled" if thinking_enabled else "disabled"
        if isinstance(parsed, dict) and parsed.get("evidence_hash") and not vote.get("evidence_hash"):
            vote["evidence_hash"] = str(parsed.get("evidence_hash")).strip()
        if vote.get("reason_zh"):
            vote["reason_raw"] = vote.get("reason_zh")
            vote["reason_zh"] = _scrub(vote["reason_zh"])
        return vote

    try:
        # Prefer thinking; if the model spends the budget on reasoning and
        # returns no JSON object, retry once in structured non-thinking mode.
        try:
            vote = _one_shot(thinking_enabled=True)
            if not vote.get("ok"):
                raise ValueError(vote.get("error") or "author_incomplete")
        except Exception as first_exc:
            try:
                vote = _one_shot(thinking_enabled=False)
                vote["thinking_retry"] = True
                vote["thinking_first_error"] = "%s:%s" % (
                    type(first_exc).__name__, str(first_exc)[:160],
                )
            except Exception:
                raise first_exc
        out["latency_sec"] = round(time.time() - started, 3)
        out.update(vote)
        return out
    except urllib_error.HTTPError as exc:
        out["latency_sec"] = round(time.time() - started, 3)
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:240]
        except Exception:
            detail = str(exc)
        out["error"] = "HTTP %s %s" % (exc.code, detail)
        return out
    except Exception as exc:
        out["latency_sec"] = round(time.time() - started, 3)
        out["error"] = "%s:%s" % (type(exc).__name__, str(exc)[:240])
        return out


def ask_guidance_provider(name, evidence):
    """Second-pass DeepSeek: action enum only. Thinking on. Does not parse p_*."""
    import auto_trade_ai_consensus as consensus
    import auto_trade_hold_assist_guidance as guidance

    name = consensus._normalize_provider_name(name)
    cfg = consensus._provider_config(name)
    out = {
        "provider": name,
        "model": cfg.get("model"),
        "ok": False,
        "action": None,
        "guidance_zh": None,
        "error": None,
        "latency_sec": None,
    }
    consent = consensus.external_research_consent_status(name, "strategy_research")
    if not consent.get("allowed"):
        out["error"] = "外部AI研究授权缺失或范围不足"
        return out
    if not cfg.get("api_key"):
        out["error"] = "API密钥未配置"
        return out
    timeout = max(int(cfg.get("timeout") or 90), 180)
    started = time.time()

    def _one_shot(thinking_enabled):
        body = {
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": guidance.SYSTEM_PROMPT},
                {"role": "user", "content": consensus.canonical_json(evidence)},
            ],
            "temperature": 0,
            "max_tokens": 8192,
        }
        if name == "deepseek":
            body["response_format"] = {"type": "json_object"}
            body["thinking"] = {
                "type": "enabled" if thinking_enabled else "disabled",
            }
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        http_request = urllib_request.Request(
            cfg["url"], data=encoded,
            headers={
                "Authorization": "Bearer " + cfg["api_key"],
                "Content-Type": "application/json",
            },
            method="POST",
        )
        response = consensus._urlopen(http_request, timeout=timeout)
        raw = json.loads(response.read().decode("utf-8"))
        text = _message_text(raw)
        parsed = consensus._parse_content_json(text)
        vote = guidance.parse_vote(parsed)
        vote["thinking"] = "enabled" if thinking_enabled else "disabled"
        if isinstance(parsed, dict) and parsed.get("evidence_hash") and not vote.get("evidence_hash"):
            vote["evidence_hash"] = str(parsed.get("evidence_hash")).strip()
        return vote

    try:
        try:
            vote = _one_shot(thinking_enabled=True)
            if not vote.get("ok"):
                raise ValueError(vote.get("error") or "guidance_incomplete")
        except Exception as first_exc:
            try:
                vote = _one_shot(thinking_enabled=False)
                vote["thinking_retry"] = True
                vote["thinking_first_error"] = "%s:%s" % (
                    type(first_exc).__name__, str(first_exc)[:160],
                )
            except Exception:
                raise first_exc
        out["latency_sec"] = round(time.time() - started, 3)
        out.update(vote)
        return out
    except urllib_error.HTTPError as exc:
        out["latency_sec"] = round(time.time() - started, 3)
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:240]
        except Exception:
            detail = str(exc)
        out["error"] = "HTTP %s %s" % (exc.code, detail)
        return out
    except Exception as exc:
        out["latency_sec"] = round(time.time() - started, 3)
        out["error"] = "%s:%s" % (type(exc).__name__, str(exc)[:240])
        return out


def vote_hold_path(evidence, ask_fn=None):
    """Deprecated second path. Official remaining-path author is default_estimate.

    Qwen must not write the official copy. If this is still called, only
    DeepSeek-v4 may answer; failure is technical failure, not a backup fill-in.
    """
    ask = ask_fn or ask_provider
    primary = ask(PRIMARY_PROVIDER, evidence)
    primary = dict(primary or {})
    primary["voter"] = PRIMARY_PROVIDER
    primary["backup_capped"] = False
    if primary.get("ok"):
        return primary
    primary["ok"] = False
    primary["technical_failed"] = True
    primary["error"] = primary.get("error") or "author_unavailable"
    return primary


def _path_bucket(row):
    from dual_engine_workflow_v2.path_lexicon import first_touch_zh, profit_first_zh

    try:
        from auto_trade_hold_assist_ledger import first_touch_from_close
        zh = first_touch_from_close(row)
        if zh == "止盈":
            return "tp"
        if zh == "止损":
            return "sl"
        if zh == "定时":
            return "timed"
    except Exception:
        pass
    reason = str(
        row.get("close_reason") or row.get("exit_reason") or row.get("invalidation") or ""
    ).lower()
    if any(tok in reason for tok in ("invalid", "作废", "cci_exit", "conditional")):
        return "invalidation"
    ft = row.get("first_touch")
    if ft not in (None, ""):
        zh = first_touch_zh(ft)
        if zh == "止盈":
            return "tp"
        if zh == "止损":
            return "sl"
        if zh == "定时":
            return "timed"
    pf = row.get("profit_first")
    try:
        pf_i = int(pf)
    except Exception:
        pf_i = None
    if pf_i is not None:
        zh = profit_first_zh(pf_i)
        if zh == "止盈":
            return "tp"
        if zh == "止损":
            return "sl"
        if zh == "定时":
            return "timed"
    return None


def _trade_mae_abs(row):
    mae = _safe_float(row.get("mae_pct"), _safe_float(row.get("mae")))
    if mae is None:
        return None
    return abs(mae)


def summarize_receipts(strategy_key, mae_pct=None):
    key = str(strategy_key or "")
    if not key:
        return None
    try:
        from auto_trade_portfolio_combo_metrics import _semantic_receipt_backtest
        rec = _semantic_receipt_backtest(key)
    except Exception:
        return None
    trades = ((rec or {}).get("backtest") or {}).get("trades_all") or []
    if not trades:
        return None

    def _count(rows):
        out = {"n": len(rows), "tp": 0, "sl": 0, "timed": 0, "invalidation": 0, "unknown": 0}
        for row in rows:
            bucket = _path_bucket(row)
            if bucket in ("tp", "sl", "timed", "invalidation"):
                out[bucket] += 1
            else:
                out["unknown"] += 1
        return out

    mae_cut = _safe_float(mae_pct)
    deep = []
    if mae_cut is not None and mae_cut > 0:
        for row in trades:
            abs_mae = _trade_mae_abs(row)
            if abs_mae is not None and abs_mae + 1e-12 >= mae_cut:
                deep.append(row)
    oos = [row for row in trades if str(row.get("sample_split") or "").upper() == "OOS"]
    allc = _count(trades)
    oosc = _count(oos)
    path_n = int(allc["tp"] + allc["sl"] + allc["timed"])
    # Empty path buckets must not be presented as "nobody hit stop".
    if path_n <= 0:
        return {
            "source": rec.get("source"),
            "usable": False,
            "reason": "path_buckets_empty",
            "all": {"n": allc["n"], "invalidation": allc["invalidation"], "unknown": allc["unknown"]},
            "oos": {"n": oosc["n"], "invalidation": oosc["invalidation"], "unknown": oosc["unknown"]},
            "mae_cut": mae_cut,
            "note_zh": "回执无法分出先触止盈/止损/定时，禁止把止盈0止损0当成统计。",
        }
    return {
        "source": rec.get("source"),
        "usable": True,
        "all": allc,
        "oos": oosc,
        "deep_mae": _count(deep) if deep or mae_cut else None,
        "mae_cut": mae_cut,
    }


def fetch_5m_candles(inst_id, limit=300):
    inst = str(inst_id or "").strip()
    if not inst:
        return []
    try:
        from common import okx_req
        path = "/api/v5/market/candles?instId=%s&bar=5m&limit=%s" % (inst, int(limit))
        raw = okx_req(path)
    except Exception:
        return []
    if not isinstance(raw, dict) or str(raw.get("code")) != "0":
        return []
    rows = []
    for item in reversed(list(raw.get("data") or [])):
        if not isinstance(item, (list, tuple)) or len(item) < 5:
            continue
        row = {
            "ts": _safe_float(item[0]),
            "open": _safe_float(item[1]),
            "high": _safe_float(item[2]),
            "low": _safe_float(item[3]),
            "close": _safe_float(item[4]),
        }
        if len(item) >= 6:
            row["vol"] = _safe_float(item[5])
        rows.append(row)
    return rows


def _extreme_from_candles(entry, side, candles, opened_at_ts=None):
    side = str(side or "").lower()
    extreme = _safe_float(entry)
    open_ts = _safe_float(opened_at_ts)
    for row in candles or []:
        ts = _safe_float(row.get("ts"))
        if open_ts is not None and ts is not None and ts < (open_ts * 1000.0 - 60000):
            continue
        px = _safe_float(row.get("low") if side == "long" else row.get("high"))
        extreme = update_extreme(extreme, px, side)
    return extreme


def resolve_stop_price(pos, current):
    cur = current if isinstance(current, dict) else {}
    for key in ("stop_loss_price", "slTriggerPx"):
        px = _safe_float(pos.get(key), _safe_float(cur.get(key)))
        if px:
            return px
    attached = cur.get("attached_stop_loss") if isinstance(cur.get("attached_stop_loss"), dict) else {}
    px = _safe_float(attached.get("stop_loss_price"), _safe_float((attached.get("payload") or {}).get("slTriggerPx")))
    if px:
        return px
    entry = _safe_float(pos.get("entry_price"), _safe_float(cur.get("entry_price")))
    pct = _safe_float(pos.get("stop_loss_pct"), _safe_float(cur.get("stop_loss_pct")))
    side = str(pos.get("side") or cur.get("side") or "").lower()
    if entry and pct:
        if side == "long":
            return entry * (1.0 - pct)
        if side == "short":
            return entry * (1.0 + pct)
    return None


def _algo_sl_price(row):
    if not isinstance(row, dict):
        return None
    for key in ("slTriggerPx", "triggerPx", "stopLossTriggerPx", "stop_loss_price"):
        px = _safe_float(row.get(key))
        if px:
            return px
    linked = row.get("linkedAlgoOrd")
    if isinstance(linked, dict):
        for key in ("slTriggerPx", "triggerPx"):
            px = _safe_float(linked.get(key))
            if px:
                return px
    return None


def _algo_matches_position(row, inst_id, side):
    if not isinstance(row, dict):
        return False
    if str(row.get("instId") or "").upper() != str(inst_id or "").upper():
        return False
    pos_side = str(row.get("posSide") or "").lower()
    side = str(side or "").lower()
    if pos_side in ("", "net"):
        return True
    return pos_side == side


def fetch_pending_algo_stops(inst_id):
    """Read-only OKX pending algo rows for one instrument. Never places/amends."""
    inst = str(inst_id or "").strip().upper()
    if not inst:
        return {"ok": False, "rows": [], "error": "inst_missing"}
    try:
        import auto_trade_okx as okx
    except Exception as exc:
        return {"ok": False, "rows": [], "error": "okx_import:%s" % exc}
    merged = []
    errors = []
    for typ in ("conditional", "trigger", "oco", "move_order_stop"):
        try:
            raw = okx._okx_request(
                "GET", "/api/v5/trade/orders-algo-pending",
                params={"instType": "SWAP", "instId": inst, "ordType": typ},
                auth=True,
            )
        except Exception as exc:
            errors.append("%s:%s" % (typ, exc))
            continue
        if not isinstance(raw, dict) or str(raw.get("code")) != "0":
            errors.append("%s:code_%s" % (typ, (raw or {}).get("code")))
            continue
        for row in raw.get("data") or []:
            if isinstance(row, dict) and str(row.get("instId") or "").upper() == inst:
                merged.append(row)
    return {
        "ok": not errors or bool(merged),
        "rows": merged,
        "error": None if not errors else ",".join(errors)[:200],
    }


def resolve_exchange_stop_price(pos, algo_rows=None):
    """Pick protective SL trigger from pending algos. Does not invent a price."""
    pos = pos if isinstance(pos, dict) else {}
    inst = pos.get("inst_id") or pos.get("symbol")
    side = str(pos.get("side") or "").lower()
    rows = algo_rows
    if rows is None:
        pack = fetch_pending_algo_stops(inst)
        rows = pack.get("rows") or []
    candidates = []
    for row in rows or []:
        if not _algo_matches_position(row, inst, side):
            continue
        px = _algo_sl_price(row)
        if px is None:
            continue
        candidates.append(px)
    if not candidates:
        return None
    if side == "long":
        return min(candidates)
    if side == "short":
        return max(candidates)
    return candidates[0]


def _opened_at_ts_from_pos(pos, cur):
    cur = cur if isinstance(cur, dict) else {}
    opened = _safe_float(cur.get("opened_at_ts"))
    if opened is not None:
        return opened
    raw = pos.get("c_time") or pos.get("cTime") or cur.get("c_time") or cur.get("cTime")
    ts = _safe_float(raw)
    if ts is None:
        return None
    if ts > 1e12:
        return ts / 1000.0
    return ts


def _fmt_pct(x):
    v = _safe_float(x)
    if v is None:
        return "—"
    return ("%.1f%%" % (v * 100.0)).replace(".0%", "%") if abs(v * 100.0 - round(v * 100.0)) < 1e-9 else "%.1f%%" % (v * 100.0)


def _fmt_px(x):
    v = _safe_float(x)
    if v is None:
        return "—"
    if abs(v) >= 100:
        return "%.2f" % v
    return ("%.4f" % v).rstrip("0").rstrip(".")


def _logic_line(pack):
    from auto_trade_hold_structure import human_logic_zh

    logic = human_logic_zh(
        methods=pack.get("methods"),
        ds_reason=pack.get("reason_zh") or pack.get("logic_zh"),
        reason_zh=pack.get("reason_zh"),
    )
    return "逻辑推理：" + logic


def format_warning_message(pack):
    from dual_engine_workflow_v2.review_lexicon import scrub

    name = pack.get("strategy_title") or pack.get("strategy_key") or "持仓"
    symbol = pack.get("symbol_label") or pack.get("inst_id") or ""
    direction = pack.get("direction_zh") or pack.get("side") or ""
    lines = [
        "%s %s %s：估算概率：止盈 %s · 止损 %s" % (
            name, symbol, direction,
            _fmt_pct(pack.get("p_take_profit")),
            _fmt_pct(pack.get("p_stop")),
        ),
        _logic_line(pack),
    ]
    guide = str(pack.get("guidance_zh") or "").strip()
    if guide:
        if not guide.startswith("智能指引"):
            guide = "智能指引：" + guide
        lines.append(guide)
    return scrub("\n".join(lines))


def notify_kind(tier):
    if tier == "urgent":
        return "hold_path_urgent"
    return "hold_path_warning"


def _load_state():
    raw = _read_json(STATE_PATH, {})
    if not isinstance(raw, dict):
        raw = {}
    positions = raw.get("positions")
    if not isinstance(positions, dict):
        positions = {}
    raw["positions"] = positions
    return raw


def _position_state_key(pos):
    return str(pos.get("position_id") or "%s|%s|%s" % (
        pos.get("inst_id") or "", pos.get("side") or "", pos.get("mgn_mode") or "",
    ))


def _listed_is_live(listed):
    if not isinstance(listed, dict) or not listed.get("ok"):
        return False
    if listed.get("building"):
        return False
    return True


def _live_position_map(listed):
    """All live OKX positions (auto + manual) for board / prune."""
    live_keys = set()
    by_key = {}
    for pos in (listed or {}).get("positions") or []:
        if not isinstance(pos, dict):
            continue
        key = _position_state_key(pos)
        live_keys.add(key)
        by_key[key] = pos
    return live_keys, by_key


# Back-compat alias used by older tests / callers.
_auto_live_map = _live_position_map


def prune_closed_positions(state, live_keys, persist=True, now_ts=None, lookup_fn=None):
    import auto_trade_hold_assist_ledger as ledger

    harvested = ledger.harvest_closed_positions(
        state, live_keys, now_ts=now_ts, auto_dir=AUTO_DIR, lookup_fn=lookup_fn,
    )
    if persist:
        state["updated_at"] = _now()
        _write_json(STATE_PATH, state)
    return harvested.get("removed") or []


def _formula_board(vote):
    vote = vote if isinstance(vote, dict) else {}
    tools = vote.get("tool_blend") if isinstance(vote.get("tool_blend"), dict) else {}
    gd = tools.get("giveback_dynamics") if isinstance(tools.get("giveback_dynamics"), dict) else {}
    sw = tools.get("swing") if isinstance(tools.get("swing"), dict) else {}
    wk = tools.get("weakening") if isinstance(tools.get("weakening"), dict) else {}
    vol = tools.get("volume") if isinstance(tools.get("volume"), dict) else {}
    mom = tools.get("momentum") if isinstance(tools.get("momentum"), dict) else {}
    return {
        "giveback_frac": gd.get("giveback_frac") if gd.get("giveback_frac") is not None else vote.get("giveback_frac"),
        "swing_state": sw.get("swing_state"),
        "lower_high": bool(sw.get("lower_high") or vote.get("lower_high")),
        "broke_ref_low": bool(sw.get("broke_ref_low")),
        "weakening_intensity": wk.get("weakening_intensity"),
        "vol_ratio_down": vol.get("vol_ratio_down"),
        "cci": mom.get("cci"),
        "k": mom.get("k"),
        "j": mom.get("j"),
    }


def _hold_board_item(key, pos, row):
    pos = pos if isinstance(pos, dict) else {}
    row = row if isinstance(row, dict) else {}
    formulas = row.get("last_formulas") if isinstance(row.get("last_formulas"), dict) else {}
    p_tp = row.get("last_p_take_profit")
    p_sl = row.get("last_p_stop")
    authored = p_tp is not None and p_sl is not None
    raw_tier = row.get("last_tier") if authored else None
    tier = raw_tier if raw_tier in ("warn", "urgent") else None
    notify_tier = row.get("last_notify_tier") if row.get("last_notify_tier") in ("warn", "urgent") else None
    display_tier = tier or notify_tier
    if row.get("last_hard_close_at") and row.get("last_hard_close_ok"):
        status_zh = "硬平仓"
        status = "hard_close"
    elif (not authored) and row.get("last_deliberation") == "technical_failed":
        status_zh = "研判失败"
        status = "failed"
    elif (not authored) and row.get("last_deliberation") == "quality_failed":
        status_zh = "协议失败"
        status = "failed"
    elif authored and display_tier == "urgent":
        status_zh = "加急"
        status = "urgent"
    elif authored and display_tier == "warn":
        status_zh = "警告"
        status = "warn"
    elif authored:
        status_zh = "观察"
        status = "watch"
    else:
        status_zh = "研判中"
        status = "pending"
    voter = row.get("last_voter") or ""
    if "deepseek" in str(voter).lower() or voter == "deepseek-v4":
        voter_zh = "DeepSeek-v4"
    elif voter in ("remaining_path", "exit_barriers"):
        voter_zh = "退出先触"
    elif voter:
        voter_zh = str(voter)
    else:
        voter_zh = "退出先触"
    return {
        "position_id": key,
        "status": status,
        "status_zh": status_zh,
        "tier": display_tier,
        "tier_zh": "加急" if display_tier == "urgent" else ("警告" if display_tier == "warn" else ("观察" if authored else status_zh)),
        "notify_tier": notify_tier,
        "strategy_title": (
            pos.get("strategy_title")
            or row.get("last_strategy_title")
            or ("手动开仓" if (pos.get("manual") or row.get("last_manual")) else "")
        ),
        "strategy_key": pos.get("strategy_key") or row.get("last_strategy_key") or "",
        "symbol_label": pos.get("symbol_label") or row.get("last_symbol_label") or "",
        "direction_zh": pos.get("direction_zh") or row.get("last_direction_zh") or "",
        "inst_id": pos.get("inst_id") or "",
        "side": pos.get("side") or row.get("last_side") or "",
        "entry_price": pos.get("entry_price") if pos.get("entry_price") is not None else row.get("last_entry_price"),
        "mark_price": pos.get("mark_price") if pos.get("mark_price") is not None else row.get("last_mark_price"),
        "stop_loss_price": pos.get("stop_loss_price") if pos.get("stop_loss_price") is not None else row.get("last_stop_loss_price"),
        "stop_consumed": (
            row.get("last_stop_consumed_now")
            if row.get("last_stop_consumed_now") is not None
            else row.get("last_stop_consumed")
        ),
        "stop_consumed_mae": row.get("last_stop_consumed"),
        "p_take_profit": p_tp,
        "p_stop": p_sl,
        "p_timed": row.get("last_p_timed"),
        "take_profit_price": row.get("last_take_profit_price"),
        "trail_price": row.get("last_trail_price"),
        "exit_mode_zh": row.get("last_exit_mode_zh") or "",
        "bars_left": row.get("last_bars_left"),
        "has_take_profit": bool(row.get("last_has_take_profit")),
        "voter": voter,
        "voter_zh": voter_zh,
        "reason_zh": row.get("last_reason_zh") or "",
        "guidance_zh": row.get("last_guidance_zh") or "",
        "guidance_action": row.get("last_guidance_action") or "",
        "guidance_ok": bool(row.get("last_guidance_ok")),
        "formulas": formulas,
        "floor_applied": bool(row.get("last_floor_applied")),
        "manual": bool(pos.get("manual") or row.get("last_manual")),
        "source": pos.get("source") or row.get("last_source") or "",
        "authored": authored,
        "estimated_at": row.get("last_estimate_at") or "",
        "notified_at": row.get("last_notify_at") or "",
        "notified_ts": row.get("last_notify_ts"),
    }


def trigger_board(listed=None, persist=True):
    import auto_trade_live_positions as live_pos
    if listed is None:
        listed = live_pos.list_live_positions()
    if not _listed_is_live(listed):
        return {
            "ok": False,
            "schema": "qiyu_hold_assist_board_v2",
            "error": (listed or {}).get("error") or "positions_unavailable",
            "count": 0,
            "alert_count": 0,
            "positions": [],
            "triggers": [],
            "pruned": [],
        }
    state = _load_state()
    live_keys, by_key = _live_position_map(listed)
    pruned = prune_closed_positions(state, live_keys, persist=persist)
    positions_state = state.get("positions") or {}
    positions = []
    triggers = []
    for key, pos in by_key.items():
        row = positions_state.get(key) if isinstance(positions_state.get(key), dict) else {}
        item = _hold_board_item(key, pos, row)
        positions.append(item)
        if row.get("last_notify_tier") in ("warn", "urgent"):
            triggers.append(item)
    rank = {"urgent": 0, "warn": 1, "watch": 2, "pending": 3, "failed": 4, "hard_close": 5}

    def _sort_key(r):
        return (
            rank.get(r.get("status"), 9),
            -(r.get("notified_ts") or 0),
            str(r.get("symbol_label") or r.get("inst_id") or ""),
        )

    positions.sort(key=_sort_key)
    triggers.sort(key=_sort_key)
    alert_count = sum(1 for r in positions if r.get("status") in ("warn", "urgent"))
    return {
        "ok": True,
        "schema": "qiyu_hold_assist_board_v2",
        "count": len(positions),
        "alert_count": alert_count,
        "positions": positions,
        "triggers": triggers,
        "pruned": pruned,
        "author": "deepseek-v4",
        "updated_at": _now(),
    }


def _join_local(pos, locals_by_plain):
    import auto_trade_live_positions as live_pos
    plain = live_pos._plain_position_key(pos.get("inst_id"), pos.get("side"))
    return locals_by_plain.get(plain) or {}


def build_snapshot(pos, local_row, candles, state_row, now_ts, algo_rows=None):
    cur = (local_row or {}).get("current") if isinstance(local_row, dict) else {}
    if not isinstance(cur, dict):
        cur = {}
    entry = _safe_float(pos.get("entry_price"), _safe_float(cur.get("entry_price")))
    mark = _safe_float(pos.get("mark_price"), _safe_float(cur.get("mark_price")))
    stop_px = resolve_stop_price(pos, cur)
    stop_source = "local" if stop_px is not None else None
    if stop_px is None:
        stop_px = resolve_exchange_stop_price(pos, algo_rows=algo_rows)
        if stop_px is not None:
            stop_source = "exchange_algo"
    side = str(pos.get("side") or cur.get("side") or "").lower()
    opened_at_ts = _opened_at_ts_from_pos(pos, cur)
    extreme = _safe_float((state_row or {}).get("extreme_adverse_px"))
    extreme = update_extreme(extreme, entry, side)
    extreme = update_extreme(extreme, mark, side)
    extreme = update_extreme(
        extreme, _extreme_from_candles(entry, side, candles, opened_at_ts), side)
    consumed = stop_consumed(entry, extreme, stop_px, side)
    consumed_now = stop_consumed(entry, mark, stop_px, side)
    dist = stop_distance(entry, stop_px, side)
    mae_pct = None
    if entry and dist is not None and consumed is not None:
        mae_pct = consumed * (dist / entry) if entry else None
    latest = candles[-1] if candles else None
    shocked = five_minute_shock(side, stop_px, latest)
    timeframe = (
        pos.get("timeframe")
        or (local_row or {}).get("timeframe")
        or "1h"
    )
    if not str(timeframe or "").strip():
        timeframe = "1h"
    title = pos.get("strategy_title") or cur.get("strategy_name")
    if pos.get("manual") and not title:
        title = "手动开仓"
    return {
        "entry_price": entry,
        "mark_price": mark,
        "stop_loss_price": stop_px,
        "stop_source": stop_source,
        "side": side,
        "extreme_adverse_px": extreme,
        "stop_consumed": consumed,
        "stop_consumed_now": consumed_now,
        "mae_pct": mae_pct,
        "shocked": shocked,
        "opened_at": cur.get("opened_at") or pos.get("opened_at"),
        "opened_at_ts": opened_at_ts,
        "stop_loss_pct": _safe_float(cur.get("stop_loss_pct"), _safe_float(pos.get("stop_loss_pct"))),
        "strategy_key": pos.get("strategy_key") or cur.get("strategy_key"),
        "strategy_title": title,
        "timeframe": timeframe,
        "manual": bool(pos.get("manual")),
        "source": pos.get("source") or ((local_row or {}).get("source") if isinstance(local_row, dict) else None),
        "leverage": _safe_float(pos.get("leverage"), _safe_float(cur.get("leverage"))),
        "candles": list(candles or []),
        "entry_atr_pct": _safe_float((cur.get("entry_data") or {}).get("entry_atr_pct") if isinstance(cur.get("entry_data"), dict) else None),
    }


def build_evidence(pos, snap):
    receipts = summarize_receipts(snap.get("strategy_key"), snap.get("mae_pct"))
    return {
        "asof": _now(),
        "position": {
            "symbol": pos.get("inst_id"),
            "timeframe": snap.get("timeframe"),
            "side": snap.get("side"),
            "strategy": snap.get("strategy_title") or snap.get("strategy_key"),
            "opened_at": snap.get("opened_at"),
            "entry": snap.get("entry_price"),
            "last": snap.get("mark_price"),
            "exchange_stop": snap.get("stop_loss_price"),
            "stop_pct": snap.get("stop_loss_pct"),
            "leverage": snap.get("leverage"),
            "contracts": pos.get("contracts"),
            "unrealized_usdt": pos.get("profit_amount_usdt"),
            "mae_pct": None if snap.get("mae_pct") is None else round(float(snap["mae_pct"]) * 100.0, 4),
            "stop_consumed": snap.get("stop_consumed"),
            "extreme_adverse_px": snap.get("extreme_adverse_px"),
            "flash_5m_shock": bool(snap.get("shocked")),
        },
        "receipt_counts_same_strategy": receipts,
        "note_zh": "止盈含追踪/保本，很少真到5×ATR。止损=交易所保护止损。",
    }


def default_estimate(pos, snap):
    import auto_trade_hold_assist_formulas as formulas
    import auto_trade_hold_assist_gate as gate
    import auto_trade_hold_exit_barriers as barriers
    import auto_trade_hold_path_kernel as kernel
    import auto_trade_hold_structure as structure
    import auto_trade_hold_tf_samples as tf_samples

    tf_pack = snap.get("tf_samples") if isinstance(snap.get("tf_samples"), dict) else None
    if tf_pack is None:
        if snap.get("strategy_candles") is not None:
            rows = list(snap.get("strategy_candles") or [])
            tf_pack = {
                "ok": len(rows) >= tf_samples.MIN_TF_BARS,
                "candles": rows,
                "n": len(rows),
                "atr_pct": tf_samples.tf_atr_pct(rows),
                "error": None if len(rows) >= tf_samples.MIN_TF_BARS else "tf_samples_insufficient",
            }
        else:
            tf_pack = tf_samples.load_symbol_tf_bars(
                pos.get("inst_id") or snap.get("inst_id"),
                snap.get("timeframe") or pos.get("timeframe") or "1h",
                mark=snap.get("mark_price"),
            )
    snap["tf_samples"] = tf_pack
    snap["strategy_candles"] = list(tf_pack.get("candles") or [])
    if not tf_pack.get("ok"):
        return {
            "ok": True,
            "author_ok": False,
            "skip_reason": "tf_samples_insufficient",
            "technical_failed": False,
            "p_take_profit": None,
            "p_stop": None,
            "p_timed": None,
            "guidance_action": None,
            "guidance_zh": None,
            "guidance_ok": False,
            "error": tf_pack.get("error") or "tf_samples_insufficient",
            "tf_n": tf_pack.get("n"),
            "ds_ok": False,
        }

    kernel_vote = kernel.estimate_position(
        strategy_key=snap.get("strategy_key") or pos.get("strategy_key"),
        inst_id=pos.get("inst_id"),
        timeframe=snap.get("timeframe") or "1h",
        side=snap.get("side") or pos.get("side"),
        entry=snap.get("entry_price"),
        mark=snap.get("mark_price"),
        stop=snap.get("stop_loss_price"),
        extreme=snap.get("extreme_adverse_px"),
        opened_at_ts=snap.get("opened_at_ts"),
        now_ts=snap.get("_now_ts") or time.time(),
        candles=snap.get("strategy_candles"),
    )
    struct = structure.detect_structure(
        snap.get("side") or pos.get("side"),
        snap.get("entry_price"),
        snap.get("candles") or [],
        opened_at_ts=snap.get("opened_at_ts"),
        now_ts=snap.get("_now_ts") or time.time(),
    )
    strategy_candles = snap.get("strategy_candles")
    if strategy_candles is None:
        strategy_candles = list((tf_pack or {}).get("candles") or [])
        snap["strategy_candles"] = strategy_candles
    formula_pack = formulas.build_formula_pack(
        side=snap.get("side") or pos.get("side"),
        entry=snap.get("entry_price"),
        candles_5m=snap.get("candles") or [],
        now_ts=snap.get("_now_ts") or time.time(),
        opened_at_ts=snap.get("opened_at_ts"),
        timeframe=snap.get("timeframe") or "1h",
        strategy_candles=strategy_candles,
        strategy_key=snap.get("strategy_key") or pos.get("strategy_key"),
    )
    struct = dict(struct or {})
    struct["formulas"] = formula_pack
    exit_barriers = barriers.resolve_barriers(
        strategy_key=snap.get("strategy_key") or pos.get("strategy_key"),
        side=snap.get("side") or pos.get("side"),
        entry=snap.get("entry_price"),
        mark=snap.get("mark_price"),
        stop=snap.get("stop_loss_price"),
        candles=snap.get("candles") or [],
        opened_at_ts=snap.get("opened_at_ts"),
        now_ts=snap.get("_now_ts") or time.time(),
        timeframe=snap.get("timeframe") or "1h",
        entry_atr_pct=snap.get("entry_atr_pct"),
        tf_candles=snap.get("strategy_candles") or [],
        live_atr_pct=(tf_pack or {}).get("atr_pct"),
        samples_ok=True,
    )
    exit_barriers["stop_consumed"] = snap.get("stop_consumed")
    exit_barriers["stop_consumed_now"] = snap.get("stop_consumed_now")
    path_vote = barriers.remaining_path_odds(
        exit_barriers, kernel_vote=kernel_vote, shocked=bool(snap.get("shocked")),
    )
    snap["exit_barriers"] = exit_barriers
    snap["remaining_path"] = {
        "p_take_profit": path_vote.get("p_take_profit"),
        "p_stop": path_vote.get("p_stop"),
        "p_timed": path_vote.get("p_timed"),
        "voter": path_vote.get("voter"),
        "kernel_blended": bool(path_vote.get("kernel_blended")),
    }
    pos_key = pos.get("position_id") or snap.get("strategy_key")
    now_ts = snap.get("_now_ts") or time.time()
    receipts = summarize_receipts(snap.get("strategy_key") or pos.get("strategy_key"), snap.get("mae_pct"))
    frozen = gate.freeze_evidence(
        pos_key, snap, struct, now_ts,
        timeframe=snap.get("timeframe"), receipts=receipts,
    )
    ehash = frozen.get("evidence_hash")
    clocks = frozen.get("clocks")
    need = structure.needs_author(kernel_vote, struct, snap)
    ask_api = snap.get("_ask_api")
    if ask_api is False:
        ask_now, skip_why = False, "clock_slot"
    else:
        ask_now, skip_why = gate.should_deliberate(
            need, frozen,
            snap.get("_last_evidence_hash"),
            snap.get("_last_deliberation"),
            now_ts=now_ts,
            last_ask_ts=snap.get("_last_ask_ts"),
            cooldown_sec=structure.DS_COOLDOWN_SEC,
        )
        if ask_api is True:
            ask_now, skip_why = True, "half_hour_slot"
    ds = {"ok": False, "skipped": True, "reason": skip_why}
    if ask_now:
        try:
            ds = structure.maybe_ask_deepseek(
                pos_key,
                struct,
                snap,
                ask_fn=ask_provider,
                now_ts=now_ts,
                kernel_vote=kernel_vote,
                evidence_hash=ehash,
                clocks=clocks,
                ignore_cooldown=(skip_why in (
                    "new_evidence", "quality_retry", "technical_retry",
                    "half_hour_slot",
                )),
                receipts=receipts,
            )
        except Exception as exc:
            ds = {"ok": False, "error": str(exc)[:200]}
    vote = structure.author_remaining_path(
        kernel_vote, struct, ds, snap=snap, receipts=receipts, path_vote=path_vote,
    )
    vote["evidence_hash"] = ehash
    vote["clocks"] = clocks
    vote["exit_barriers"] = exit_barriers
    vote["ds_ok"] = bool(ds.get("ok"))
    if ds.get("skipped"):
        vote["skip_reason"] = ds.get("reason") or vote.get("skip_reason")
    if vote.get("author_ok"):
        vote = gate.apply_structure_floor(vote, tools=vote.get("tool_blend"))
        if vote.get("floor_applied"):
            note = vote.get("reason_zh") or ""
            if "结构下限" not in note:
                vote["reason_zh"] = (note + " 结构下限已校正。").strip()
    vote["guidance_action"] = None
    vote["guidance_zh"] = None
    vote["guidance_ok"] = False
    if vote.get("author_ok") and ask_now and ds.get("ok"):
        import auto_trade_hold_assist_guidance as guidance
        ctx = guidance.build_context(
            snap=snap, struct=struct, path_vote=path_vote, receipts=receipts,
        )
        ctx["p_take_profit"] = vote.get("p_take_profit")
        ctx["p_stop"] = vote.get("p_stop")
        ctx["p_timed"] = vote.get("p_timed")
        allowed = guidance.allowed_actions(ctx)
        try:
            raw_g = structure.maybe_ask_guidance(
                pos_key, ctx, allowed,
                evidence_hash=ehash, clocks=clocks,
                ask_fn=ask_guidance_provider,
            )
        except Exception as exc:
            raw_g = {"ok": False, "error": str(exc)[:200]}
        checked = gate.quality_guidance(raw_g, ctx, expected_hash=ehash)
        vote["guidance_action"] = checked.get("action")
        vote["guidance_zh"] = checked.get("guidance_zh")
        vote["guidance_ok"] = bool(checked.get("ok") and checked.get("guidance_zh"))
        vote["guidance_error"] = checked.get("error")
    return vote


def evaluate_once(
    listed=None,
    candles_fn=None,
    estimate_fn=None,
    ask_fn=None,
    notify_fn=None,
    close_fn=None,
    hard_close_candles_fn=None,
    now_ts=None,
    dry_run=False,
    state=None,
):
    import auto_trade_hold_assist_hard_close as hard_close
    import auto_trade_live_positions as live_pos

    now_ts = time.time() if now_ts is None else float(now_ts)
    try:
        import auto_trade_session_clock as session_clock
        if not session_clock.in_session(now_ts):
            return {
                "ok": True,
                "action": "outside_trading_session",
                "actions": [],
                "n_positions": 0,
                "session": session_clock.session_snapshot(now_ts),
            }
    except Exception as exc:
        return {
            "ok": True,
            "action": "outside_trading_session",
            "actions": [],
            "n_positions": 0,
            "session_error": str(exc),
        }
    if listed is None:
        listed = live_pos.list_live_positions()
    if not isinstance(listed, dict) or not listed.get("ok"):
        return {
            "ok": False,
            "error": (listed or {}).get("error") or "positions_unavailable",
            "actions": [],
            "n_positions": -1,
        }
    locals_by_plain = {}
    for row in live_pos._collect_local_currents():
        plain = live_pos._plain_position_key(row.get("symbol"), row.get("side"))
        locals_by_plain[plain] = row
    get_candles = candles_fn or fetch_5m_candles
    state = state if isinstance(state, dict) else _load_state()
    positions_state = state.setdefault("positions", {})
    live_keys = set()
    actions = []
    asked = 0
    algo_cache = {}
    for pos in listed.get("positions") or []:
        if not isinstance(pos, dict):
            continue
        key = _position_state_key(pos)
        live_keys.add(key)
        local_row = _join_local(pos, locals_by_plain)
        candles = []
        try:
            candles = get_candles(pos.get("inst_id")) or []
        except Exception:
            candles = []
        row_state = positions_state.get(key) if isinstance(positions_state.get(key), dict) else {}
        need_algo = resolve_stop_price(pos, (local_row or {}).get("current") or {}) is None
        algo_rows = None
        if need_algo:
            inst = str(pos.get("inst_id") or "").upper()
            if inst not in algo_cache:
                algo_cache[inst] = fetch_pending_algo_stops(inst).get("rows") or []
            algo_rows = algo_cache.get(inst) or []
        snap = build_snapshot(
            pos, local_row, candles, row_state, now_ts, algo_rows=algo_rows,
        )
        snap["_now_ts"] = now_ts
        snap["_last_evidence_hash"] = row_state.get("last_evidence_hash")
        snap["_last_deliberation"] = row_state.get("last_deliberation")
        snap["_last_ask_ts"] = row_state.get("last_ask_ts")
        current_slot = half_hour_slot_id(now_ts)
        snap["_ask_api"] = row_state.get("last_api_slot") != current_slot
        snap["_allow_notify"] = row_state.get("last_notify_slot") != current_slot
        snap["_slot_id"] = current_slot
        row_state = dict(row_state)
        row_state["extreme_adverse_px"] = snap.get("extreme_adverse_px")
        row_state["opened_at_ts"] = snap.get("opened_at_ts")
        row_state["last_strategy_key"] = snap.get("strategy_key") or pos.get("strategy_key")
        row_state["last_manual"] = bool(pos.get("manual") or snap.get("manual"))
        row_state["last_source"] = snap.get("source") or pos.get("source") or ""
        row_state["updated_at"] = _now()
        row_state["updated_at_ts"] = now_ts
        positions_state[key] = row_state
        if snap.get("stop_loss_price") is None or snap.get("entry_price") is None:
            actions.append({
                "position_id": key,
                "action": "skip_no_stop",
                "manual": bool(pos.get("manual")),
            })
            continue
        import auto_trade_hold_tf_samples as tf_samples
        tf_pack = tf_samples.load_symbol_tf_bars(
            pos.get("inst_id") or snap.get("inst_id"),
            snap.get("timeframe") or pos.get("timeframe") or "1h",
            mark=snap.get("mark_price") or pos.get("mark_price"),
        )
        snap["tf_samples"] = tf_pack
        snap["strategy_candles"] = list(tf_pack.get("candles") or [])
        # Directional hard close: CCI/K/J extreme → flat immediately, no DeepSeek.
        # Manual positions are never hard-closed here.
        hc = hard_close.evaluate_position(
            pos, snap=snap,
            candles=snap.get("strategy_candles") or None,
            candles_fn=hard_close_candles_fn,
        )
        if hc.get("applicable") and hc.get("triggered"):
            if not hard_close.should_retry(row_state, now_ts):
                actions.append({
                    "position_id": key,
                    "action": "hard_close_cooldown",
                    "hit": hc.get("hit"),
                })
                continue
            message = hard_close.format_hard_close_message(pos, snap)
            closed = {"ok": True, "closed": False, "dry_run": True}
            if not dry_run:
                closed = hard_close.close_position(pos, close_fn=close_fn) or {}
            sent = {"ok": True, "sent": False, "dry_run": True}
            kind = hard_close.HARD_CLOSE_KIND
            if notify_fn is None:
                import auto_trade_formal_notify as notify
                sent = notify.send_message(
                    message, kind=kind,
                    meta={
                        "strategy_key": snap.get("strategy_key") or pos.get("strategy_key"),
                        "hard_close": hc.get("hit"),
                        "close_ok": bool(closed.get("ok")),
                        "closed": bool(closed.get("closed")),
                    },
                    dry_run=dry_run,
                )
            else:
                sent = notify_fn(message, kind, {
                    "strategy_key": snap.get("strategy_key") or pos.get("strategy_key"),
                    "hit": hc.get("hit"),
                }, dry_run) or {}
            if not dry_run:
                row_state["last_hard_close_ts"] = now_ts
                row_state["last_hard_close_at"] = _now()
                row_state["last_hard_close_hit"] = hc.get("hit")
                row_state["last_hard_close_ok"] = bool(
                    closed.get("ok") and (
                        closed.get("closed")
                        or closed.get("reason") == "position_already_absent"
                    )
                )
                row_state["last_tier"] = None
                positions_state[key] = row_state
            note = {
                "position_id": key,
                "action": "hard_close",
                "hit": hc.get("hit"),
                "indicators": hc.get("indicators"),
                "close": {
                    "ok": closed.get("ok"),
                    "closed": closed.get("closed"),
                    "reason": closed.get("reason"),
                    "error": closed.get("error"),
                    "dry_run": bool(dry_run),
                },
                "notify": True,
                "kind": kind,
                "sent": bool(sent.get("sent")),
                "message": message,
            }
            actions.append(note)
            _append_event({
                "kind": kind,
                "position_id": key,
                "hit": hc.get("hit"),
                "closed": bool(closed.get("closed")),
                "sent": bool(sent.get("sent")),
                "dry_run": bool(dry_run),
            })
            continue
        estimate = estimate_fn or default_estimate
        vote = estimate(pos, snap) or {}
        asked += 1
        if not dry_run and snap.get("_ask_api"):
            row_state["last_api_slot"] = snap.get("_slot_id")
        author_ok = vote.get("author_ok")
        if vote.get("evidence_hash"):
            row_state["last_clocks"] = vote.get("clocks")
        if not dry_run:
            row_state["last_estimate_ts"] = now_ts
            row_state["last_estimate_at"] = _now()
            row_state["last_voter"] = vote.get("voter")
            row_state["last_author_ok"] = author_ok
            row_state["last_n_eff"] = vote.get("n_eff")
            tool = vote.get("tool_blend") if isinstance(vote.get("tool_blend"), dict) else {}
            row_state["last_tool_p_take_profit"] = tool.get("p_take_profit")
            row_state["last_tool_p_stop"] = tool.get("p_stop")
            if author_ok:
                row_state["last_p_take_profit"] = vote.get("p_take_profit")
                row_state["last_p_stop"] = vote.get("p_stop")
                row_state["last_p_invalidation"] = vote.get("p_invalidation")
                row_state["last_p_timed"] = vote.get("p_timed")
                row_state["last_evidence_hash"] = vote.get("evidence_hash")
                skip_reason = vote.get("skip_reason")
                if skip_reason in ("evidence_unchanged", "cooldown", "clock_slot"):
                    row_state["last_deliberation"] = row_state.get("last_deliberation") or "ok"
                    if not row_state.get("last_reason_zh"):
                        row_state["last_reason_zh"] = vote.get("reason_zh")
                else:
                    row_state["last_reason_zh"] = vote.get("reason_zh")
                    row_state["last_ask_ts"] = now_ts
                    if vote.get("ds_ok") or vote.get("explainer") or skip_reason:
                        row_state["last_deliberation"] = "ok"
                    elif vote.get("ds_ok") is False:
                        row_state["last_deliberation"] = "technical_failed"
                    else:
                        row_state["last_deliberation"] = "ok"
                    if vote.get("guidance_action") is not None:
                        row_state["last_guidance_zh"] = vote.get("guidance_zh")
                        row_state["last_guidance_action"] = vote.get("guidance_action")
                        row_state["last_guidance_ok"] = bool(vote.get("guidance_ok"))
                row_state["last_formulas"] = _formula_board(vote)
                row_state["last_floor_applied"] = bool(vote.get("floor_applied"))
                row_state["last_strategy_title"] = snap.get("strategy_title") or pos.get("strategy_title")
                row_state["last_strategy_key"] = snap.get("strategy_key") or pos.get("strategy_key")
                row_state["last_symbol_label"] = pos.get("symbol_label")
                row_state["last_direction_zh"] = pos.get("direction_zh")
                row_state["last_side"] = snap.get("side")
                row_state["last_manual"] = bool(pos.get("manual") or snap.get("manual"))
                row_state["last_source"] = snap.get("source") or pos.get("source") or ""
                row_state["last_entry_price"] = snap.get("entry_price")
                row_state["last_mark_price"] = snap.get("mark_price")
                row_state["last_stop_loss_price"] = snap.get("stop_loss_price")
                row_state["last_stop_source"] = snap.get("stop_source")
                row_state["last_stop_consumed"] = snap.get("stop_consumed")
                row_state["last_stop_consumed_now"] = snap.get("stop_consumed_now")
                eb = vote.get("exit_barriers") if isinstance(vote.get("exit_barriers"), dict) else (snap.get("exit_barriers") or {})
                row_state["last_take_profit_price"] = eb.get("take_profit_price")
                row_state["last_trail_price"] = eb.get("trail_price")
                row_state["last_exit_mode_zh"] = eb.get("tp_mode_zh")
                row_state["last_bars_left"] = eb.get("bars_left")
                row_state["last_has_take_profit"] = bool(eb.get("has_take_profit"))
            elif vote.get("quality_failed"):
                row_state["last_evidence_hash"] = vote.get("evidence_hash")
                row_state["last_deliberation"] = "quality_failed"
                row_state["last_ask_ts"] = now_ts
            elif vote.get("technical_failed"):
                row_state["last_deliberation"] = "technical_failed"
                row_state["last_ask_ts"] = now_ts
            elif author_ok is None:
                row_state["last_p_take_profit"] = vote.get("p_take_profit")
                row_state["last_p_stop"] = vote.get("p_stop")
                row_state["last_p_invalidation"] = vote.get("p_invalidation")
                row_state["last_p_timed"] = vote.get("p_timed")
        if not vote.get("ok"):
            row_state["last_error"] = vote.get("error")
            positions_state[key] = row_state
            actions.append({
                "position_id": key,
                "action": "quality_rejected" if vote.get("quality_failed") else "estimate_failed",
                "error": vote.get("error"),
                "technical_failed": bool(vote.get("technical_failed")),
                "quality_failed": bool(vote.get("quality_failed")),
            })
            _append_event({
                "kind": "quality_rejected" if vote.get("quality_failed") else "estimate_failed",
                "position_id": key,
                "error": vote.get("error"),
                "technical_failed": bool(vote.get("technical_failed")),
                "quality_failed": bool(vote.get("quality_failed")),
            })
            continue
        if author_ok is False:
            row_state["last_tier"] = None
            positions_state[key] = row_state
            actions.append({
                "position_id": key,
                "action": "voted",
                "tier": None,
                "voter": vote.get("voter"),
                "author_ok": False,
                "skip_reason": vote.get("skip_reason"),
                "notify": False,
            })
            continue
        tier = classify_tier(
            vote.get("p_take_profit"),
            vote.get("p_stop"),
            morph={
                "giveback_frac": vote.get("giveback_frac"),
                "lower_high": vote.get("lower_high"),
                "methods": vote.get("methods"),
                "close_below_entry": (vote.get("tool_blend") or {}).get("morphology", {}).get(
                    "close_below_entry"
                ) if isinstance(vote.get("tool_blend"), dict) else None,
            },
        )
        row_state["last_tier"] = tier
        pack = {
            "tier": tier,
            "voter": vote.get("voter") or "path_kernel",
            "p_take_profit": vote.get("p_take_profit"),
            "p_stop": vote.get("p_stop"),
            "p_invalidation": vote.get("p_invalidation"),
            "p_timed": vote.get("p_timed"),
            "reason_zh": vote.get("reason_zh"),
            "logic_zh": vote.get("logic_zh"),
            "guidance_zh": vote.get("guidance_zh") or row_state.get("last_guidance_zh"),
            "guidance_action": vote.get("guidance_action") or row_state.get("last_guidance_action"),
            "methods": list(vote.get("methods") or []),
            "backup_capped": False,
            "strategy_title": snap.get("strategy_title") or pos.get("strategy_title"),
            "strategy_key": snap.get("strategy_key") or pos.get("strategy_key"),
            "symbol_label": pos.get("symbol_label"),
            "inst_id": pos.get("inst_id"),
            "direction_zh": pos.get("direction_zh"),
            "side": snap.get("side"),
            "entry_price": snap.get("entry_price"),
            "mark_price": snap.get("mark_price"),
            "stop_loss_price": snap.get("stop_loss_price"),
            "stop_consumed": snap.get("stop_consumed"),
            "mae_pct": snap.get("mae_pct"),
        }
        send = should_notify(
            tier, row_state.get("last_notify_tier"),
            row_state.get("last_notify_ts"), now_ts,
            allow=bool(snap.get("_allow_notify", True)),
            last_slot=row_state.get("last_notify_slot"),
            slot=snap.get("_slot_id"),
        )
        note = {
            "position_id": key,
            "action": "voted",
            "tier": tier,
            "voter": vote.get("voter"),
            "p_take_profit": vote.get("p_take_profit"),
            "p_stop": vote.get("p_stop"),
            "notify": False,
        }
        if send and tier:
            message = format_warning_message(pack)
            kind = notify_kind(tier)
            sent = {"ok": True, "sent": False, "dry_run": True}
            if notify_fn is None:
                import auto_trade_formal_notify as notify
                sent = notify.send_message(
                    message, kind=kind, meta={"strategy_key": pack.get("strategy_key")},
                    dry_run=dry_run,
                )
            else:
                sent = notify_fn(message, kind, pack, dry_run) or {}
            delivered = bool(sent.get("sent"))
            if not dry_run and delivered:
                row_state["last_notify_ts"] = now_ts
                row_state["last_notify_at"] = _now()
                row_state["last_notify_tier"] = tier
                row_state["last_notify_slot"] = snap.get("_slot_id")
                row_state["last_reason_zh"] = vote.get("reason_zh") or pack.get("reason_zh")
                row_state["last_strategy_title"] = pack.get("strategy_title")
                row_state["last_symbol_label"] = pack.get("symbol_label")
                row_state["last_direction_zh"] = pack.get("direction_zh")
            note["action"] = "notified"
            note["notify"] = True
            note["kind"] = kind
            note["sent"] = bool(sent.get("sent"))
            note["dry_run"] = bool(sent.get("dry_run") or dry_run)
            _append_event({
                "kind": kind,
                "position_id": key,
                "tier": tier,
                "voter": vote.get("voter"),
                "p_take_profit": vote.get("p_take_profit"),
                "p_stop": vote.get("p_stop"),
                "sent": bool(sent.get("sent")),
                "dry_run": bool(sent.get("dry_run") or dry_run),
            })
        positions_state[key] = row_state
        actions.append(note)
    before_ids = len(state.get("outcome_ids") or [])
    harvested_keys = prune_closed_positions(
        state, live_keys, persist=False, now_ts=now_ts,
    )
    recorded_n = max(0, len(state.get("outcome_ids") or []) - before_ids)
    state["updated_at"] = _now()
    state["updated_at_ts"] = now_ts
    _write_json(STATE_PATH, state)
    if harvested_keys:
        for key in harvested_keys:
            actions.append({
                "position_id": key,
                "action": "retired",
            })
        _append_event({"kind": "outcomes_recorded", "n": recorded_n})
    return {
        "ok": True,
        "asked": asked,
        "n_positions": len(listed.get("positions") or []),
        "actions": actions,
        "outcomes_recorded": recorded_n,
        "dry_run": bool(dry_run),
    }


def live_position_count(listed):
    """How many exchange positions the tribunal should watch (auto + manual)."""
    if not isinstance(listed, dict) or not listed.get("ok"):
        return -1
    n = 0
    for pos in listed.get("positions") or []:
        if isinstance(pos, dict):
            n += 1
    return n


def next_poll_sec(n_positions, poll_sec=POLL_SEC, idle_poll_sec=IDLE_POLL_SEC):
    """Active hold: short poll (hard-close). Flat book: idle — 观风金球 closed."""
    active = max(1.0, float(poll_sec))
    idle = max(active, float(idle_poll_sec))
    try:
        n = int(n_positions)
    except Exception:
        n = -1
    if n < 0:
        return active
    if n == 0:
        return idle
    return active


def run_forever(poll_sec=POLL_SEC, idle_poll_sec=IDLE_POLL_SEC):
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import auto_trade_hold_structure as structure
        structure.load_ds_last()
    except Exception:
        pass
    print("hold_assist start", _now(),
          "exit_plan first-touch, ds/wx at :00/:30, idle when flat=%ss, warn tp<%s sl>%s urgent tp<%s sl>=%s" % (
              int(idle_poll_sec), WARN_TP, WARN_SL, URGENT_TP, URGENT_SL,
          ))
    was_idle = None
    while True:
        n_positions = -1
        try:
            out = evaluate_once()
            n_positions = int(out.get("n_positions") if out.get("n_positions") is not None else -1)
            notified = [a for a in (out.get("actions") or []) if a.get("notify")]
            failed = [a for a in (out.get("actions") or []) if a.get("action") == "estimate_failed"]
            if notified:
                print(_now(), "notified", len(notified), notified[0].get("kind"),
                      notified[0].get("p_take_profit"), notified[0].get("p_stop"))
            elif failed:
                print(_now(), "estimate_failed", failed[0].get("error"))
            elif not out.get("ok"):
                print(_now(), "hold_error", out.get("error"))
            idle_now = n_positions == 0
            if idle_now and was_idle is not True:
                print(_now(), "hold_idle", "no positions; 观风金球 closed; wake every %ss" % int(idle_poll_sec))
                _append_event({"kind": "hold_idle", "idle_poll_sec": float(idle_poll_sec)})
            elif (not idle_now) and n_positions > 0 and was_idle is not False:
                print(_now(), "hold_active", "positions=%s; ds/wx half-hour; hard-close poll %ss" % (
                    n_positions, int(poll_sec),
                ))
                _append_event({"kind": "hold_active", "n_positions": n_positions})
            if n_positions >= 0:
                was_idle = idle_now
        except Exception as exc:
            print(_now(), "hold_exception", type(exc).__name__, exc)
            _append_event({"kind": "exception", "error": str(exc)})
        time.sleep(next_poll_sec(n_positions, poll_sec=poll_sec, idle_poll_sec=idle_poll_sec))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ledger", action="store_true")
    parser.add_argument("--poll-sec", type=float, default=POLL_SEC)
    parser.add_argument("--idle-poll-sec", type=float, default=IDLE_POLL_SEC)
    args = parser.parse_args()
    if args.ledger:
        import auto_trade_hold_assist_ledger as ledger
        print(json.dumps(ledger.summarize_outcomes(), ensure_ascii=False, indent=2))
    elif args.once:
        print(json.dumps(
            evaluate_once(dry_run=bool(args.dry_run)),
            ensure_ascii=False, indent=2, default=str,
        ))
    else:
        run_forever(poll_sec=args.poll_sec, idle_poll_sec=args.idle_poll_sec)
