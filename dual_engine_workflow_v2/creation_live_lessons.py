# -*- coding: utf-8 -*-
"""Live path lessons for creation: 止盈/止损/定时 × structure_family.

Account-path closes only. Occupancy weekly-geo is excluded and must never
appear as a reward. Slow-updates mechanism_beliefs; never unmounts.

Same-strategy entry situations (vol / session / trend location) are attributed
only after ≥3 repeats of the same path. That is not a research-direction ban.
"""
from __future__ import print_function

import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .creation_case_store import store_dir
from .path_lexicon import PATH_STOP_LOSS, PATH_TAKE_PROFIT, PATH_TIMED
from .process_safe_state import atomic_write_json, process_lock


SCHEMA = "qiyu_creation_live_family_paths_v1"
MIN_FAMILY_CLOSES = 5
MIN_CONSECUTIVE_STOPS = 3
MIN_SITUATION_REPEATS = 3
MIN_SITUATION_CONCENTRATION = 0.75
OCCUPANCY_GEO_KEYS = (
    "weekly_geometric_growth",
    "weekly_geometric_growth_oos",
    "occupancy_replay",
    "geo_delta",
    "geo_oos_delta",
)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def _auto_dir():
    return _root() / "auto_trade"


def cursor_path():
    return store_dir() / "live_beliefs_cursor.json"


def _path_zh(closed):
    try:
        from auto_trade_hold_assist_ledger import first_touch_from_close
        zh = first_touch_from_close(closed)
        if zh in (PATH_TAKE_PROFIT, PATH_STOP_LOSS, PATH_TIMED):
            return zh
    except Exception:
        pass
    from .path_lexicon import first_touch_zh
    zh = first_touch_zh(
        (closed or {}).get("first_touch"),
        (closed or {}).get("profit_first"),
    )
    if zh in (PATH_TAKE_PROFIT, PATH_STOP_LOSS, PATH_TIMED):
        return zh
    return None


def load_structure_by_key():
    mapping = {}
    path = _auto_dir() / "live_structure_classification.json"
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        rows = payload.get("rows") or payload.get("items") or payload
        if isinstance(rows, dict):
            rows = rows.get("rows") or []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            key = str(row.get("strategy_key") or row.get("key") or "").strip()
            family = str(row.get("family") or "").strip()
            if key and family:
                mapping[key] = family
    try:
        from .creation_case_store import load_cases
        for case in load_cases():
            key = str(case.get("strategy_key") or "").strip()
            family = str(case.get("structure_family") or "").strip()
            if key and family and key not in mapping:
                mapping[key] = family
    except Exception:
        pass
    return mapping


def iter_formal_closes():
    rows = []
    auto = _auto_dir()
    if not auto.is_dir():
        return rows
    for path in sorted(auto.glob("formal_v6_state*.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        history = state.get("history") or []
        if not isinstance(history, list):
            continue
        for item in history:
            if isinstance(item, dict):
                rows.append(item)
        last = state.get("last_closed")
        if isinstance(last, dict):
            rows.append(last)
    return rows


def _blank_counts():
    return {PATH_TAKE_PROFIT: 0, PATH_STOP_LOSS: 0, PATH_TIMED: 0, "n": 0}


def _safe_float(value, default=None):
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if out != out:
            return default
        return out
    except Exception:
        return default


def _entry_data(closed):
    closed = closed if isinstance(closed, dict) else {}
    data = closed.get("entry_data")
    if isinstance(data, dict):
        return data
    info = closed.get("entry_info")
    if isinstance(info, dict):
        return info
    snap = closed.get("entry_snapshot")
    if isinstance(snap, dict):
        indicators = snap.get("indicators")
        if isinstance(indicators, dict):
            merged = dict(indicators)
            merged.setdefault("regime", snap.get("regime"))
            return merged
    return {}


def _opened_dt(closed):
    closed = closed if isinstance(closed, dict) else {}
    text = closed.get("opened_at") or (_entry_data(closed).get("opened_at"))
    try:
        return datetime.strptime(str(text)[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        ts = _safe_float(closed.get("opened_at_ts"))
        if ts is None:
            return None
        try:
            return datetime.fromtimestamp(ts)
        except Exception:
            return None


def _hold_hours(closed):
    opened = _safe_float(closed.get("opened_at_ts"))
    closed_ts = _safe_float(closed.get("closed_at_ts"))
    if opened is None or closed_ts is None or closed_ts < opened:
        odt = _opened_dt(closed)
        try:
            cdt = datetime.strptime(str(closed.get("closed_at") or "")[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            cdt = None
        if odt is None or cdt is None:
            return None
        return max(0.0, (cdt - odt).total_seconds() / 3600.0)
    return max(0.0, (closed_ts - opened) / 3600.0)


def _lookup_num(data, keys):
    for key in keys:
        value = _safe_float((data or {}).get(key))
        if value is not None:
            return value
    return None


def vol_bin(atr_pct):
    atr = _safe_float(atr_pct)
    if atr is None:
        return "unknown"
    if atr < 0.008:
        return "low"
    if atr < 0.02:
        return "mid"
    return "high"


def session_bin(opened_dt):
    if opened_dt is None:
        return "unknown"
    hour = int(opened_dt.hour)
    if 8 <= hour < 16:
        return "asia"
    if 16 <= hour < 21:
        return "europe"
    if hour >= 21 or hour < 5:
        return "us"
    return "off"


def trend_bin(side, trend_value):
    side = str(side or "").strip().lower()
    trend = _safe_float(trend_value)
    if trend is None or side not in ("long", "short"):
        return "unknown"
    if side == "long":
        if trend > 0:
            return "aligned"
        if trend < 0:
            return "counter"
        return "flat"
    if trend < 0:
        return "aligned"
    if trend > 0:
        return "counter"
    return "flat"


def adx_bin(adx):
    value = _safe_float(adx)
    if value is None:
        return "unknown"
    if value < 20:
        return "weak"
    if value < 35:
        return "mid"
    return "strong"


VOL_ZH = {"low": "低波动", "mid": "中波动", "high": "高波动", "unknown": "波动未知"}
SESS_ZH = {
    "asia": "亚盘", "europe": "欧盘", "us": "美盘",
    "off": "过渡时段", "unknown": "时段未知",
}
TREND_ZH = {
    "aligned": "顺势位置", "counter": "逆势位置",
    "flat": "趋势近零", "unknown": "趋势未知",
}
ADX_ZH = {
    "weak": "ADX弱", "mid": "ADX中", "strong": "ADX强", "unknown": "ADX未知",
}
SIDE_ZH = {"long": "做多", "short": "做空"}


def classify_entry_situation(closed):
    """Bucket the entry context. Path result is the label, not part of the key."""
    closed = closed if isinstance(closed, dict) else {}
    data = _entry_data(closed)
    snap = closed.get("entry_snapshot") if isinstance(closed.get("entry_snapshot"), dict) else {}
    indicators = snap.get("indicators") if isinstance(snap.get("indicators"), dict) else {}
    merged = dict(indicators)
    merged.update(data)
    side = str(closed.get("side") or merged.get("side") or "").strip().lower()
    atr = _lookup_num(merged, ("entry_atr_pct", "atr_pct_14", "atr_pct"))
    trend = _lookup_num(merged, (
        "trend_bias_50_200", "trend_bias", "ema_diff", "ema_ratio",
    ))
    adx = _lookup_num(merged, ("adx_14", "adx"))
    vol = vol_bin(atr)
    sess = session_bin(_opened_dt(closed))
    trend_b = trend_bin(side, trend)
    adx_b = adx_bin(adx)
    sid = "vol:%s|sess:%s|trend:%s|adx:%s|side:%s" % (
        vol, sess, trend_b, adx_b, side or "unknown",
    )
    parts = [VOL_ZH[vol], SESS_ZH[sess]]
    if trend_b != "unknown":
        parts.append(TREND_ZH[trend_b])
    if adx_b != "unknown":
        parts.append(ADX_ZH[adx_b])
    if side in SIDE_ZH:
        parts.append(SIDE_ZH[side])
    return {
        "situation_id": sid,
        "vol_bin": vol,
        "session_bin": sess,
        "trend_bin": trend_b,
        "adx_bin": adx_b,
        "side": side or "unknown",
        "label_zh": " · ".join(parts),
        "entry_atr_pct": atr,
        "hold_hours": _hold_hours(closed),
    }


def _median(values):
    rows = [float(item) for item in values if item is not None]
    if not rows:
        return None
    rows.sort()
    mid = len(rows) // 2
    if len(rows) % 2:
        return round(rows[mid], 3)
    return round((rows[mid - 1] + rows[mid]) / 2.0, 3)


def _concentration(bag):
    n = int(bag.get("n") or 0)
    if n <= 0:
        return None, None, 0.0
    dominant = PATH_TAKE_PROFIT
    best = int(bag.get(PATH_TAKE_PROFIT) or 0)
    for path in (PATH_STOP_LOSS, PATH_TIMED):
        count = int(bag.get(path) or 0)
        if count > best:
            dominant = path
            best = count
    return dominant, best, (best / float(n))


def _reason_from_contrast(stop_row, tp_row):
    reasons = []
    if stop_row.get("vol_bin") != tp_row.get("vol_bin"):
        reasons.append(
            "止损入场偏%s，止盈入场偏%s：高波动更容易先触保护止损，中低波动才走完止盈幅度"
            % (
                VOL_ZH.get(stop_row.get("vol_bin"), stop_row.get("vol_bin")),
                VOL_ZH.get(tp_row.get("vol_bin"), tp_row.get("vol_bin")),
            )
        )
    if stop_row.get("trend_bin") != tp_row.get("trend_bin"):
        reasons.append(
            "止损入场偏%s，止盈入场偏%s：入场时趋势位置决定路径，不是该方向本身不能做"
            % (
                TREND_ZH.get(stop_row.get("trend_bin"), stop_row.get("trend_bin")),
                TREND_ZH.get(tp_row.get("trend_bin"), tp_row.get("trend_bin")),
            )
        )
    if stop_row.get("session_bin") != tp_row.get("session_bin"):
        reasons.append(
            "止损入场偏%s，止盈入场偏%s：品种活跃时段与缺口环境在分化路径"
            % (
                SESS_ZH.get(stop_row.get("session_bin"), stop_row.get("session_bin")),
                SESS_ZH.get(tp_row.get("session_bin"), tp_row.get("session_bin")),
            )
        )
    if stop_row.get("adx_bin") != tp_row.get("adx_bin"):
        reasons.append(
            "止损入场偏%s，止盈入场偏%s：趋势强度不足时破位/延续更容易失败"
            % (
                ADX_ZH.get(stop_row.get("adx_bin"), stop_row.get("adx_bin")),
                ADX_ZH.get(tp_row.get("adx_bin"), tp_row.get("adx_bin")),
            )
        )
    if not reasons:
        reasons.append(
            "同类入场点反复后路径分化，但波动/时段/顺逆势分箱相同；"
            "需看持仓时长与退出几何，不能把研究方向本身判死刑"
        )
    return "；".join(reasons) + "。"


def attribute_entry_situations(closes, family_by_key=None, min_repeats=None):
    """Same strategy, same entry situation: promote only after ≥3 repeats."""
    min_repeats = int(min_repeats or MIN_SITUATION_REPEATS)
    family_by_key = family_by_key if family_by_key is not None else {}
    bags = {}
    holds = defaultdict(list)
    for raw in closes or []:
        if not isinstance(raw, dict):
            continue
        zh = _path_zh(raw)
        if zh not in (PATH_TAKE_PROFIT, PATH_STOP_LOSS, PATH_TIMED):
            continue
        key = str(raw.get("strategy_key") or raw.get("key") or "").strip()
        if not key:
            continue
        sit = classify_entry_situation(raw)
        bag_key = (key, sit["situation_id"])
        bag = bags.get(bag_key)
        if bag is None:
            bag = {
                "strategy_key": key,
                "structure_family": family_by_key.get(key),
                "situation_id": sit["situation_id"],
                "label_zh": sit["label_zh"],
                "vol_bin": sit["vol_bin"],
                "session_bin": sit["session_bin"],
                "trend_bin": sit["trend_bin"],
                "adx_bin": sit["adx_bin"],
                "side": sit["side"],
                PATH_TAKE_PROFIT: 0,
                PATH_STOP_LOSS: 0,
                PATH_TIMED: 0,
                "n": 0,
            }
            bags[bag_key] = bag
        bag[zh] = int(bag.get(zh) or 0) + 1
        bag["n"] = int(bag.get("n") or 0) + 1
        if sit.get("hold_hours") is not None:
            holds[bag_key].append(sit.get("hold_hours"))

    promoted = []
    for bag_key, bag in sorted(bags.items()):
        dominant, best, conc = _concentration(bag)
        if best < min_repeats or conc < MIN_SITUATION_CONCENTRATION:
            continue
        if dominant not in (PATH_TAKE_PROFIT, PATH_STOP_LOSS):
            continue
        median_hold = _median(holds.get(bag_key) or [])
        hold_note = ""
        if median_hold is not None:
            hold_note = "中位持仓 %.1f 小时。" % median_hold
        lesson_zh = (
            "同一策略 %s：入场点「%s」反复 %s 次先触%s（占比 %.0f%%）。%s"
            "这是入场情境课，不是研究方向禁令。"
        ) % (
            bag["strategy_key"], bag["label_zh"], best, dominant,
            conc * 100.0, hold_note,
        )
        promoted.append({
            "strategy_key": bag["strategy_key"],
            "structure_family": bag.get("structure_family"),
            "situation_id": bag["situation_id"],
            "label_zh": bag["label_zh"],
            "vol_bin": bag["vol_bin"],
            "session_bin": bag["session_bin"],
            "trend_bin": bag["trend_bin"],
            "adx_bin": bag["adx_bin"],
            "side": bag["side"],
            "dominant_path": dominant,
            "n": bag["n"],
            "count": {
                PATH_TAKE_PROFIT: int(bag.get(PATH_TAKE_PROFIT) or 0),
                PATH_STOP_LOSS: int(bag.get(PATH_STOP_LOSS) or 0),
                PATH_TIMED: int(bag.get(PATH_TIMED) or 0),
            },
            "concentration": round(conc, 4),
            "median_hold_hours": median_hold,
            "lesson_zh": lesson_zh,
            "not_a_gate": True,
            "not_a_research_direction_ban": True,
        })

    by_strategy = defaultdict(lambda: {PATH_STOP_LOSS: [], PATH_TAKE_PROFIT: []})
    for row in promoted:
        by_strategy[row["strategy_key"]][row["dominant_path"]].append(row)

    attributions = []
    for key, split in sorted(by_strategy.items()):
        stops = split[PATH_STOP_LOSS]
        tps = split[PATH_TAKE_PROFIT]
        if not stops and not tps:
            continue
        if stops and tps:
            reason = _reason_from_contrast(stops[0], tps[0])
            lesson_zh = (
                "同一策略 %s 对照：止损入场点「%s」（%s次），止盈入场点「%s」（%s次）。归因：%s"
            ) % (
                key, stops[0]["label_zh"], stops[0]["n"],
                tps[0]["label_zh"], tps[0]["n"], reason,
            )
        elif stops:
            lesson_zh = (
                "同一策略 %s：入场点「%s」反复 %s 次止损，尚无对照的止盈入场点。"
                "先检查该情境的趋势位置与波动，不要把整个研究方向判死刑。"
            ) % (key, stops[0]["label_zh"], stops[0]["n"])
            reason = lesson_zh
        else:
            lesson_zh = (
                "同一策略 %s：入场点「%s」反复 %s 次止盈。可作同类入场形状的正例，禁止抄参数阈值。"
            ) % (key, tps[0]["label_zh"], tps[0]["n"])
            reason = lesson_zh
        attributions.append({
            "strategy_key": key,
            "structure_family": (stops or tps)[0].get("structure_family"),
            "stop_situations": [row["label_zh"] for row in stops[:4]],
            "take_profit_situations": [row["label_zh"] for row in tps[:4]],
            "reason_zh": reason,
            "lesson_zh": lesson_zh,
            "not_a_gate": True,
            "not_a_research_direction_ban": True,
        })

    common = []
    by_sit = defaultdict(list)
    for row in promoted:
        sit_key = "vol:%s|sess:%s|trend:%s|adx:%s" % (
            row.get("vol_bin"), row.get("session_bin"),
            row.get("trend_bin"), row.get("adx_bin"),
        )
        by_sit[(sit_key, row["dominant_path"])].append(row)
    for (sit_key, path), rows in sorted(by_sit.items()):
        keys = sorted({row["strategy_key"] for row in rows})
        if len(keys) < 2:
            continue
        sample = rows[0]
        common.append({
            "situation_id": sit_key,
            "label_zh": sample["label_zh"],
            "dominant_path": path,
            "n_strategies": len(keys),
            "strategy_keys_sample": keys[:6],
            "lesson_zh": (
                "共同因子：入场点「%s」在 %s 个策略上反复先触%s。"
                "这是入场情境课，不是某个研究方向或做多做空禁令。"
            ) % (sample["label_zh"], len(keys), path),
            "not_a_gate": True,
            "not_a_research_direction_ban": True,
        })

    return {
        "schema": "qiyu_creation_live_entry_situations_v1",
        "min_repeats": min_repeats,
        "min_concentration": MIN_SITUATION_CONCENTRATION,
        "occupancy_geo_excluded": True,
        "not_a_gate": True,
        "situations": promoted[:40],
        "attributions": attributions[:20],
        "common_factors": common[:12],
        "note_zh": (
            "同一策略、同一类入场点反复≥%s次才归因。"
            "对照止盈/止损入场情境做逻辑推理。占用周几何与单笔盈亏不是奖励。"
            "不是研究方向禁令，也不能放行。"
        ) % min_repeats,
    }


def summarize_live_family_paths(closes=None, family_by_key=None):
    family_by_key = family_by_key if family_by_key is not None else load_structure_by_key()
    closes = list(closes if closes is not None else iter_formal_closes())
    by_family = defaultdict(_blank_counts)
    by_key_stops = defaultdict(list)
    skipped_no_path = 0
    skipped_no_family = 0
    for raw in closes:
        if not isinstance(raw, dict):
            continue
        zh = _path_zh(raw)
        if zh is None:
            skipped_no_path += 1
            continue
        key = str(raw.get("strategy_key") or raw.get("key") or "").strip()
        family = family_by_key.get(key) if key else None
        if not family:
            skipped_no_family += 1
            continue
        bag = by_family[family]
        bag[zh] = int(bag.get(zh) or 0) + 1
        bag["n"] = int(bag.get("n") or 0) + 1
        by_key_stops[key].append((family, zh))

    published = {}
    warnings = []
    for family, bag in sorted(by_family.items()):
        n = int(bag.get("n") or 0)
        if n < MIN_FAMILY_CLOSES:
            continue
        stop_n = int(bag.get(PATH_STOP_LOSS) or 0)
        published[family] = {
            PATH_TAKE_PROFIT: int(bag.get(PATH_TAKE_PROFIT) or 0),
            PATH_STOP_LOSS: stop_n,
            PATH_TIMED: int(bag.get(PATH_TIMED) or 0),
            "n": n,
            "stop_rate": round(stop_n / float(n), 4) if n else None,
        }
        if family == "no_trend_mr" and stop_n >= MIN_FAMILY_CLOSES:
            warnings.append({
                "structure_family": family,
                "note_zh": "无趋势振荡器家族实盘止损偏多。强化结构先验，不改质检门槛，不自动卸挂。",
            })

    consecutive = []
    for key, seq in by_key_stops.items():
        trail = 0
        family = None
        for family, zh in reversed(seq):
            if zh == PATH_STOP_LOSS:
                trail += 1
            else:
                break
        if trail >= MIN_CONSECUTIVE_STOPS:
            consecutive.append({
                "strategy_key": key,
                "structure_family": family,
                "consecutive_stop_n": trail,
                "note_zh": "同构连续止损。只作创造结构先验，禁止当单笔强化信号。",
            })

    entry_situations = attribute_entry_situations(closes, family_by_key=family_by_key)
    return {
        "schema": SCHEMA,
        "source": "formal_account_path",
        "occupancy_geo_excluded": True,
        "occupancy_keys_forbidden": list(OCCUPANCY_GEO_KEYS),
        "by_family": published,
        "live_warnings": warnings,
        "consecutive_same_thesis_stops": consecutive[:20],
        "entry_situations": entry_situations,
        "skipped_no_path": skipped_no_path,
        "skipped_no_family": skipped_no_family,
        "n_closes_read": len(closes),
        "note_zh": (
            "实盘先触路径按结构家族汇总；同一策略的入场点在反复≥3次后对照止盈/止损做归因。"
            "占用周几何、单笔盈亏、盲OOS数字不在此课。"
        ),
        "at": _now(),
    }


def _cursor_load():
    path = cursor_path()
    if not path.is_file():
        return {"schema": "qiyu_creation_live_beliefs_cursor_v1", "applied": {}}
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"schema": "qiyu_creation_live_beliefs_cursor_v1", "applied": {}}
    if not isinstance(row, dict):
        return {"schema": "qiyu_creation_live_beliefs_cursor_v1", "applied": {}}
    row.setdefault("applied", {})
    return row


def apply_slow_mechanism_beliefs(paths):
    """One medium update per family when path counts change. Never fast PnL."""
    paths = paths if isinstance(paths, dict) else {}
    by_family = paths.get("by_family") or {}
    cursor = _cursor_load()
    applied = dict(cursor.get("applied") or {})
    updates = []
    try:
        from . import mechanism_beliefs as beliefs
    except Exception:
        return {"ok": False, "error": "mechanism_beliefs_unavailable", "updates": []}

    for family, bag in sorted(by_family.items()):
        if not isinstance(bag, dict):
            continue
        n = int(bag.get("n") or 0)
        if n < MIN_FAMILY_CLOSES:
            continue
        signature = "%s:%s:%s:%s" % (
            bag.get(PATH_TAKE_PROFIT), bag.get(PATH_STOP_LOSS),
            bag.get(PATH_TIMED), n,
        )
        if applied.get(family) == signature:
            continue
        stop_rate = float(bag.get("stop_rate") or 0)
        take_n = int(bag.get(PATH_TAKE_PROFIT) or 0)
        take_rate = take_n / float(n) if n else 0.0
        attribution = {
            "family": family,
            "mechanism_id": "family:%s" % family,
            "reward_vector": {},
        }
        if stop_rate >= 0.60:
            attribution["fail_stage"] = "mechanism"
            attribution["responsibility"] = {"mechanism_failure": 0.35}
        elif take_rate >= 0.40:
            attribution["fail_stage"] = "survived"
            attribution["responsibility"] = {"success_credit_mechanism": 0.30}
        else:
            attribution["fail_stage"] = "ambiguous"
            attribution["responsibility"] = {}
        got = beliefs.update_from_attribution(attribution, timescale="medium")
        applied[family] = signature
        updates.append({
            "family": family,
            "ok": bool((got or {}).get("ok")),
            "posterior": (got or {}).get("posterior_probability"),
        })

    cursor["applied"] = applied
    cursor["updated_at"] = _now()
    with process_lock("creation_live_beliefs"):
        atomic_write_json(cursor_path(), cursor)
    return {"ok": True, "updates": updates, "at": _now()}
