# -*- coding: utf-8 -*-
"""Adaptive S/A/B/C ratings for live auto-trading assignments.

Ratings combine a capped backtest prior with time-decayed real fills.  The
module never rewrites strategy rules and never disables a strategy by itself.
It is safe to refresh once per minute and is read by both the daemon and UI.
"""
from __future__ import print_function

from collections import defaultdict
from datetime import datetime
from pathlib import Path
import argparse
import glob
import json
import math
import os
import tempfile
import time


ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
RATING_FILE = AUTO_DIR / "strategy_ratings.json"
FREQUENCY_FILE = AUTO_DIR / "live_portfolio_frequency.json"
STRATEGY_CONFIG = ROOT / "strategy_configs" / "experimental_strategies.json"
GRADE_RANK = {"S": 4, "A": 3, "B": 2, "C": 1}
GRADE_RATIO = {"S": 0.70, "A": 0.50, "B": 0.30, "C": 0.10}
GRADE_POLICY_VERSION = "qiyu_trade_outcome_grade_v2"


def _read_json(path, default):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        delete=False, dir=str(path.parent), mode="w", encoding="utf-8"
    )
    try:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        handle.close()
        Path(handle.name).replace(path)
    except Exception:
        try:
            Path(handle.name).unlink()
        except Exception:
            pass
        raise


def _float(value, default=None):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _nested(obj, *keys):
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _parse_time(value):
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 100000000000:
            raw /= 1000.0
        return datetime.fromtimestamp(raw)
    text = str(value or "")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            pass
    return None


def canonical_key(key):
    aliases = {
        "ema7_center_down_short": "ema6_center_down_then_fall",
        "ema6_center_down_then_fall": "ema6_center_down_then_fall",
    }
    key = str(key or "")
    return aliases.get(key, key)


def assignment_id(symbol, timeframe, strategy_key):
    return "%s|%s|%s" % (
        str(symbol or "").upper(), str(timeframe or "1h").lower(),
        canonical_key(strategy_key),
    )


def _metadata_names():
    data = _read_json(STRATEGY_CONFIG, {})
    return {
        canonical_key(row.get("key")): row.get("name")
        for row in (data.get("strategies") or []) if isinstance(row, dict)
    }


def active_assignments():
    names = _metadata_names()
    rows = []
    seen = set()
    for path in sorted(glob.glob(str(AUTO_DIR / "formal_daemon_config*.json"))):
        cfg = _read_json(path, {})
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        symbol = str(cfg.get("symbol") or "").upper()
        timeframe = str(cfg.get("timeframe") or "1h").lower()
        for raw_key in cfg.get("strategy_keys") or []:
            key = canonical_key(raw_key)
            aid = assignment_id(symbol, timeframe, key)
            if not symbol or not key or aid in seen:
                continue
            seen.add(aid)
            rows.append({
                "assignment_id": aid, "symbol": symbol,
                "timeframe": timeframe, "strategy_key": key,
                "strategy_name": names.get(key) or str(key),
                "config_file": path,
                "auto_open": bool(cfg.get("allow_auto_open")),
            })
    return rows


def _state_live_trades():
    """Return de-duplicated real closed trades with exchange-derived net PnL."""
    out = []
    seen = set()
    assignment_timeframes = defaultdict(set)
    for assignment in active_assignments():
        assignment_timeframes[(assignment["symbol"], assignment["strategy_key"])].add(assignment["timeframe"])
    for path in sorted(glob.glob(str(AUTO_DIR / "formal_v6_state*.json"))):
        state = _read_json(path, {})
        for row in (state.get("history") or []) if isinstance(state, dict) else []:
            if not isinstance(row, dict):
                continue
            open_fill = _nested(row, "open_order", "filled", "order") or {}
            close_fill = (
                _nested(row, "close_order", "filled", "order")
                or _nested(row, "last_close_attempt", "close_order", "filled", "order")
                or {}
            )
            token = (
                close_fill.get("ordId") or row.get("execution_id")
                or "%s|%s|%s" % (row.get("symbol"), row.get("opened_at"), row.get("closed_at"))
            )
            if token in seen:
                continue
            seen.add(token)
            realized = _float(close_fill.get("pnl"), _float(row.get("pnl")))
            open_fee = _float(open_fill.get("fee"), 0.0) or 0.0
            close_fee = _float(close_fill.get("fee"), 0.0) or 0.0
            net_pnl = (realized + open_fee + close_fee) if realized is not None else None
            position = _nested(row, "position_poll", "position") or {}
            initial_margin = _float(position.get("imr"), _float(row.get("imr")))
            roi_pct = None
            if net_pnl is not None and initial_margin not in (None, 0):
                roi_pct = net_pnl / initial_margin * 100.0
            if roi_pct is None:
                entry = _float(row.get("entry_price"), _float(open_fill.get("avgPx")))
                close = _float(row.get("close_price"), _float(close_fill.get("avgPx")))
                if entry not in (None, 0) and close is not None:
                    direction = -1.0 if str(row.get("side") or row.get("posSide")).lower() == "short" else 1.0
                    leverage = _float(row.get("leverage"), _float(close_fill.get("lever"), 1.0)) or 1.0
                    roi_pct = direction * (close-entry) / entry * leverage * 100.0
            closed_at = _parse_time(row.get("closed_at") or row.get("closed_at_ts") or close_fill.get("fillTime"))
            symbol = str(row.get("symbol") or open_fill.get("instId") or "").upper()
            strategy_key = canonical_key(row.get("strategy_key"))
            timeframe = str(row.get("timeframe") or _nested(row, "entry_data", "timeframe") or "").lower()
            if not timeframe:
                candidates = assignment_timeframes.get((symbol, strategy_key), set())
                timeframe = next(iter(candidates)) if len(candidates) == 1 else None
            out.append({
                "symbol": symbol, "timeframe": timeframe,
                "strategy_key": strategy_key,
                "strategy_name": row.get("strategy_name"),
                "opened_at": row.get("opened_at"), "closed_at": row.get("closed_at"),
                "closed_datetime": closed_at, "net_pnl_usdt": net_pnl,
                "roi_pct": roi_pct, "win": bool(net_pnl > 0) if net_pnl is not None else (bool(roi_pct > 0) if roi_pct is not None else None),
                "close_type": row.get("close_type"), "close_reason": row.get("close_reason"),
                "source_file": path, "token": str(token),
            })
    return out


def live_trades():
    return _state_live_trades()


def _baseline_groups():
    data = _read_json(FREQUENCY_FILE, {})
    grouped = defaultdict(list)
    for row in data.get("accepted") or []:
        if not isinstance(row, dict):
            continue
        aid = assignment_id(row.get("symbol"), row.get("timeframe"), row.get("strategy_key"))
        grouped[aid].append(row)
    summaries = {}
    for row in data.get("assignments") or []:
        if isinstance(row, dict):
            summaries[assignment_id(row.get("symbol"), row.get("timeframe"), row.get("strategy_key"))] = row
    return grouped, summaries


def _trade_metrics(rows, cutoff=None):
    selected = []
    for row in rows:
        when = _parse_time(row.get("entry"))
        if cutoff is None or (when and when >= cutoff):
            selected.append(row)
    returns = [_float(row.get("pnl_ratio")) for row in selected]
    returns = [value for value in returns if value is not None]
    wins = sum(1 for row in selected if row.get("profit") is True)
    return {
        "trades": len(selected),
        "wins": wins,
        "win_rate_pct": wins / float(len(selected)) * 100.0 if selected else None,
        "expectancy_pct": sum(returns) / float(len(returns)) * 100.0 if returns else None,
    }


def _grade(expected_win, expected_return, lower_bound, baseline_n, recent_win, live_rows):
    live_closed = [row for row in live_rows if row.get("win") is not None]
    recent_three = live_closed[-3:]
    loss_cluster = len(recent_three) >= 3 and all(row.get("win") is False for row in recent_three)
    if expected_return <= 0 or expected_win < 55 or loss_cluster:
        return "C", "期望非正、预期胜率过低或出现三连损"
    if (expected_win >= 75 and expected_return >= 3 and lower_bound >= 60
            and baseline_n >= 15 and (recent_win is None or recent_win >= 70)):
        return "S", "高胜率、正期望且样本置信度较好"
    if expected_win >= 68 and expected_return > 0 and lower_bound >= 52 and baseline_n >= 8:
        return "A", "胜率与期望达标，具备正常实盘资格"
    return "B", "保持正期望但胜率、样本或置信度仍需观察"


def refresh(now=None):
    now = now or datetime.now()
    grade_monitor = {}
    try:
        import auto_trade_human_confirm_pipeline as grade_pipeline
        grade_monitor = grade_pipeline.monitor_live_grades() or {}
    except Exception as grade_error:
        grade_monitor = {"ok": False, "error": str(grade_error)}
    previous = _read_json(RATING_FILE, {})
    previous_map = previous.get("ratings_by_id") if isinstance(previous, dict) else {}
    grouped, summaries = _baseline_groups()
    actual = _state_live_trades()
    assignments = active_assignments()
    runtime_controls = _read_json(
        AUTO_DIR / "strategy_runtime_controls.json", {"assignments": {}})
    runtime_assignments = runtime_controls.get("assignments") or {}
    ratings = []
    changes = []
    for assignment in assignments:
        aid = assignment["assignment_id"]
        base_rows = grouped.get(aid, [])
        full = _trade_metrics(base_rows)
        recent = _trade_metrics(base_rows, cutoff=datetime(2026, 4, 22))
        summary = summaries.get(aid, {})
        if full["trades"] == 0 and summary:
            full = {
                "trades": int(summary.get("trades") or 0),
                "wins": int(summary.get("wins") or 0),
                "win_rate_pct": _float(summary.get("win_rate")),
                "expectancy_pct": None,
            }
        full_wr = _float(full.get("win_rate_pct"), 50.0)
        recent_wr = _float(recent.get("win_rate_pct"), full_wr)
        full_ev = _float(full.get("expectancy_pct"), 0.0)
        recent_ev = _float(recent.get("expectancy_pct"), full_ev)
        prior_wr = 0.45 * full_wr + 0.55 * recent_wr
        prior_ev = 0.45 * full_ev + 0.55 * recent_ev
        baseline_n = int(full.get("trades") or 0)
        prior_strength = min(24.0, max(8.0, math.sqrt(max(1, baseline_n)) * 3.0))
        live_rows = [row for row in actual
                     if row.get("symbol") == assignment["symbol"]
                     and row.get("timeframe") == assignment["timeframe"]
                     and row.get("strategy_key") == assignment["strategy_key"]]
        live_rows.sort(key=lambda row: row.get("closed_datetime") or datetime.min)
        live_weight = live_win_weight = live_return_weighted = 0.0
        live_return_weight = 0.0
        for row in live_rows:
            closed = row.get("closed_datetime") or now
            age_days = max(0.0, (now-closed).total_seconds()/86400.0)
            weight = 2.0 * (0.5 ** (age_days/30.0))
            if row.get("win") is not None:
                live_weight += weight
                if row.get("win"):
                    live_win_weight += weight
            if row.get("roi_pct") is not None:
                live_return_weight += weight
                live_return_weighted += weight * float(row["roi_pct"])
        total_strength = prior_strength + live_weight
        expected_win = (prior_strength * prior_wr + live_win_weight * 100.0) / max(total_strength, 1e-9)
        return_strength = prior_strength + live_return_weight
        expected_return = (prior_strength * prior_ev + live_return_weighted) / max(return_strength, 1e-9)
        p = max(0.0, min(1.0, expected_win/100.0))
        lower_bound = max(0.0, (p - 1.28*math.sqrt(max(0.0, p*(1-p))/max(total_strength, 1.0))) * 100.0)
        # Enrich with uniform expectancy / calibrated WR (fees+slippage aware).
        exp_block = {}
        try:
            import auto_trade_expectancy_metrics as _exp
            controls = _read_json(AUTO_DIR / "strategy_runtime_controls.json", {})
            assign_row = ((controls.get("assignments") or {}).get(aid) or {})
            pos = _float(assign_row.get("max_position_ratio"), 0.30) or 0.30
            ai_wr = _float(assign_row.get("ai_theoretical_wr_avg"))
            exp_block = _exp.build_expectancy_block(
                assignment["symbol"], assignment["timeframe"],
                assignment["strategy_key"], position_ratio=pos,
                leverage=20.0, ai_wr_pct=ai_wr,
            ) or {}
            cal = (exp_block.get("calibrated_expected_win_rate_pct") or {}).get("value")
            net_m = (exp_block.get("net_expectancy_margin_pct") or {}).get("value")
            if cal is not None:
                expected_win = float(cal)
            if net_m is not None:
                expected_return = float(net_m)
            p = max(0.0, min(1.0, expected_win / 100.0))
            lower_bound = max(
                0.0,
                (p - 1.28 * math.sqrt(max(0.0, p * (1 - p)) / max(total_strength, 1.0)))
                * 100.0,
            )
        except Exception as exp_err:
            exp_block = {"error": str(exp_err)}

        performance_grade, performance_reason = _grade(
            expected_win, expected_return, lower_bound, baseline_n,
            recent_wr, live_rows)
        runtime_row = runtime_assignments.get(aid) or {}
        grade = str(runtime_row.get("lifecycle_grade") or "B").upper()
        if grade not in GRADE_RANK:
            grade = "B"
        reason = (
            "实盘成交结果状态机评级；统计评分%s仅作观测，不改仓位"
            % performance_grade)
        position_ratio = _float(
            runtime_row.get("max_position_ratio"), GRADE_RATIO[grade])
        if position_ratio is None:
            position_ratio = GRADE_RATIO[grade]
        live_losses = sum(1 for row in live_rows if row.get("win") is False)
        if grade == "S":
            action = "优先运行"
        elif grade == "A":
            action = "正常运行"
        elif grade == "B":
            action = "谨慎运行，继续观察"
        elif live_losses >= 3:
            action = "建议暂停并重新验证"
        else:
            action = "低优先级运行，等待复核"
        old_grade = (previous_map or {}).get(aid, {}).get("grade")
        if old_grade and old_grade != grade:
            changes.append({"assignment_id": aid, "from": old_grade, "to": grade})
        cal_wr_metric = (exp_block.get("calibrated_expected_win_rate_pct")
                         if isinstance(exp_block, dict) else None)
        net_eq_metric = (exp_block.get("net_expectancy_equity_pct")
                         if isinstance(exp_block, dict) else None)
        missing_sample = int(baseline_n or 0) == 0 and len(live_rows) == 0
        ratings.append(dict(assignment, **{
            "grade": grade, "grade_rank": GRADE_RANK[grade],
            "grade_source": "strategy_runtime_controls",
            "grade_policy_version": GRADE_POLICY_VERSION,
            "position_ratio": round(float(position_ratio), 4),
            "performance_grade": performance_grade,
            "performance_grade_reason": performance_reason,
            "expected_win_rate_pct": (
                None if missing_sample and not (
                    isinstance(cal_wr_metric, dict) and cal_wr_metric.get("value") is not None
                ) else round(expected_win, 2)),
            "calibrated_expected_win_rate_pct": (
                None if not isinstance(cal_wr_metric, dict)
                else cal_wr_metric.get("value")),
            "expected_return_per_trade_pct": (
                None if missing_sample and (
                    not isinstance(exp_block, dict)
                    or (exp_block.get("net_expectancy_margin_pct") or {}).get("value") is None
                ) else round(expected_return, 3)),
            "gross_expectancy_price_pct": (
                (exp_block.get("gross_expectancy_price_pct") or {}).get("value")
                if isinstance(exp_block, dict) else None),
            "net_expectancy_price_pct": (
                (exp_block.get("net_expectancy_price_pct") or {}).get("value")
                if isinstance(exp_block, dict) else None),
            "net_expectancy_notional_pct": (
                (exp_block.get("net_expectancy_notional_pct") or {}).get("value")
                if isinstance(exp_block, dict) else None),
            "net_expectancy_equity_pct": (
                None if not isinstance(net_eq_metric, dict)
                else net_eq_metric.get("value")),
            "net_expectancy_margin_pct": (
                (exp_block.get("net_expectancy_margin_pct") or {}).get("value")
                if isinstance(exp_block, dict) else None),
            "cost_price_pct": (
                (exp_block.get("cost_price_pct") or {}).get("value")
                if isinstance(exp_block, dict) else None),
            "profit_factor": (
                (exp_block.get("profit_factor") or {}).get("value")
                if isinstance(exp_block, dict) else None),
            "cost_ratio": (
                (exp_block.get("cost_ratio") or {}).get("value")
                if isinstance(exp_block, dict) else None),
            "max_drawdown_margin_pct": (
                (exp_block.get("max_drawdown_margin_pct") or {}).get("value")
                if isinstance(exp_block, dict) else None),
            "credibility": (
                exp_block.get("credibility") if isinstance(exp_block, dict) else None),
            "mechanism_family": (
                exp_block.get("mechanism_family") if isinstance(exp_block, dict) else None),
            "metric_status": {
                "win_rate": (
                    None if not isinstance(cal_wr_metric, dict)
                    else cal_wr_metric.get("metric_status") or (
                        "missing" if missing_sample else "ok")),
                "expectancy": (
                    None if not isinstance(net_eq_metric, dict)
                    else net_eq_metric.get("metric_status") or (
                        "missing" if missing_sample else "ok")),
                "display_win_rate": (
                    None if not isinstance(cal_wr_metric, dict)
                    else cal_wr_metric.get("display")),
                "display_expectancy": (
                    None if not isinstance(net_eq_metric, dict)
                    else net_eq_metric.get("display")),
            },
            "confidence_lower_win_rate_pct": round(lower_bound, 2),
            "backtest_trades": baseline_n,
            "backtest_win_rate_pct": round(full_wr, 2),
            "recent_backtest_win_rate_pct": round(recent_wr, 2),
            "real_trade_count": len(live_rows),
            "real_wins": sum(1 for row in live_rows if row.get("win") is True),
            "real_losses": sum(1 for row in live_rows if row.get("win") is False),
            "rating_reason": reason, "recommended_action": action,
            "learning_policy": "capped_backtest_prior_plus_30d_half_life_real_fills",
            "rule_auto_rewrite": False, "auto_disabled_by_rating": False,
        }))
    ratings.sort(key=lambda row: (
        -int(row.get("grade_rank") or 0),
        -(float(row["expected_win_rate_pct"]) if row.get("expected_win_rate_pct") is not None else -1),
        -(float(row["expected_return_per_trade_pct"]) if row.get("expected_return_per_trade_pct") is not None else -999),
        row.get("symbol") or "",
        row.get("timeframe") or "",
    ))
    by_id = {row["assignment_id"]: row for row in ratings}
    payload = {
        "ok": True, "schema": "qiyu_strategy_rating_v1",
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at_ts": time.time(), "ratings": ratings,
        "ratings_by_id": by_id, "changes": changes,
        "grade_monitor": grade_monitor,
        "grade_order": ["S", "A", "B", "C"],
        "grade_ratios": GRADE_RATIO,
        "grade_policy_version": GRADE_POLICY_VERSION,
        "grade_policy": {
            "initial": "所有新挂载自动交易策略从B级30%开始",
            "promotion": "4单3盈升级一级；A升S另需平均总本金收益率>5%",
            "demotion": "3单2止损降级一级",
            "removal": "C级再次出现3单2止损则卸载并归档",
        },
        "safety": (
            "唯一评级来自实盘成交结果状态机；统计评分仅作观测。"
            "评级只改仓位上限，不改杠杆、止损或策略规则。"),
    }
    _atomic_write(RATING_FILE, payload)
    return payload


def status(refresh_if_stale=True):
    data = _read_json(RATING_FILE, {})
    age = time.time() - float((data or {}).get("updated_at_ts") or 0)
    if refresh_if_stale and (not data.get("ok") or age > 180):
        return refresh()
    return data


def rating_for(symbol, timeframe, strategy_key, refresh_if_stale=False):
    data = status(refresh_if_stale=refresh_if_stale)
    return (data.get("ratings_by_id") or {}).get(assignment_id(symbol, timeframe, strategy_key), {})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    result = refresh() if args.refresh else status()
    counts = {grade: 0 for grade in ("S", "A", "B", "C")}
    for row in result.get("ratings") or []:
        grade = row.get("grade")
        if grade in counts:
            counts[grade] += 1
    print(json.dumps({"ok": result.get("ok"), "updated_at": result.get("updated_at"),
                      "rating_count": sum(counts.values()), "grade_counts": counts,
                      "changes": result.get("changes")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
