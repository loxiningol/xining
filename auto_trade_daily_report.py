# -*- coding: utf-8 -*-
"""Daily open/close ledger and idempotent 23:59 WxPusher report."""
from __future__ import print_function

from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
import argparse
import glob
import json
import os
import tempfile
import time

import auto_trade_strategy_rating as rating


ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
STATE_FILE = AUTO_DIR / "daily_trade_report_state.json"
AUDIT_FILE = AUTO_DIR / "daily_trade_reports.jsonl"
LEDGER_FILE = AUTO_DIR / "daily_trade_records_5d.json"
LATEST_LEDGER_FILE = AUTO_DIR / "latest_auto_trade_records_20.json"
RETENTION_DAYS = 5
LATEST_RECORD_LIMIT = 20


def _read_json(path, default):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(delete=False, dir=str(path.parent), mode="w", encoding="utf-8")
    try:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush(); handle.close(); Path(handle.name).replace(path)
    except Exception:
        try: Path(handle.name).unlink()
        except Exception: pass
        raise


def _float(value, default=None):
    try:
        if value in (None, ""): return default
        return float(value)
    except Exception: return default


def _nested(obj, *keys):
    cur = obj
    for key in keys:
        if not isinstance(cur, dict): return None
        cur = cur.get(key)
    return cur


def _date_text(value):
    text = str(value or "")
    return text[:10] if len(text) >= 10 else None


def _time_text(value):
    text = str(value or "")
    return text[11:19] if len(text) >= 19 else (text or "-")


def _close_type_text(value):
    raw = str(value or "").strip()
    lowered = raw.lower()
    if "stop" in lowered or "止损" in raw:
        return "交易所止损" if "exchange" in lowered or "交易所" in raw else "止损"
    if "take_profit" in lowered or "strategy" in lowered or "止盈" in raw:
        return "策略止盈"
    if "timed" in lowered or "timeout" in lowered or "定时" in raw:
        return "定时强制平仓"
    if "manual" in lowered or "手动" in raw:
        return "手动平仓"
    return raw or "平仓"


def _assignment_lookup():
    result = {}
    for row in rating.active_assignments():
        result[(row["symbol"], row["strategy_key"])] = row
    return result


def _all_positions():
    rows = []
    seen = set()
    lookup = _assignment_lookup()
    for path in sorted(glob.glob(str(AUTO_DIR / "formal_v6_state*.json"))):
        state = _read_json(path, {})
        raw_rows = list(state.get("history") or []) if isinstance(state, dict) else []
        current = state.get("current") if isinstance(state, dict) else None
        if isinstance(current, dict) and current:
            raw_rows.append(current)
        for raw in raw_rows:
            if not isinstance(raw, dict): continue
            open_fill = _nested(raw, "open_order", "filled", "order") or {}
            close_fill = (_nested(raw, "close_order", "filled", "order")
                          or _nested(raw, "last_close_attempt", "close_order", "filled", "order") or {})
            token = close_fill.get("ordId") or open_fill.get("ordId") or raw.get("execution_id") or "%s|%s" % (raw.get("symbol"), raw.get("opened_at"))
            if token in seen: continue
            seen.add(token)
            symbol = str(raw.get("symbol") or open_fill.get("instId") or "").upper()
            key = rating.canonical_key(raw.get("strategy_key"))
            assignment = lookup.get((symbol, key), {})
            timeframe = str(raw.get("timeframe") or _nested(raw, "entry_data", "timeframe")
                            or assignment.get("timeframe") or "1h").lower()
            realized = _float(close_fill.get("pnl"), _float(raw.get("pnl")))
            open_fee = _float(open_fill.get("fee"), 0.0) or 0.0
            close_fee = _float(close_fill.get("fee"), 0.0) or 0.0
            net = realized + open_fee + close_fee if realized is not None else None
            position = _nested(raw, "position_poll", "position") or {}
            margin = _float(position.get("imr"), _float(raw.get("imr")))
            roi = net/margin*100.0 if net is not None and margin not in (None,0) else None
            if roi is None:
                entry = _float(raw.get("entry_price"), _float(open_fill.get("avgPx")))
                close = _float(raw.get("close_price"), _float(close_fill.get("avgPx")))
                if entry not in (None,0) and close is not None:
                    direction = -1.0 if str(raw.get("side") or raw.get("posSide")).lower() == "short" else 1.0
                    leverage = _float(raw.get("leverage"), _float(close_fill.get("lever"),1.0)) or 1.0
                    roi = direction*(close-entry)/entry*leverage*100.0
            rows.append({
                "token": str(token), "symbol": symbol,
                "timeframe": timeframe,
                "strategy_key": key,
                "strategy_name": raw.get("strategy_name") or assignment.get("strategy_name") or key,
                "side": raw.get("side") or raw.get("posSide"),
                "opened_at": raw.get("opened_at"), "closed_at": raw.get("closed_at"),
                "entry_price": _float(raw.get("entry_price"), _float(open_fill.get("avgPx"))),
                "close_price": _float(raw.get("close_price"), _float(close_fill.get("avgPx"))),
                "leverage": _float(raw.get("leverage"), _float(open_fill.get("lever"))),
                "stop_loss_pct": _float(raw.get("stop_loss_pct")),
                "close_type": _close_type_text(raw.get("close_type") or raw.get("close_reason")),
                "net_pnl_usdt": round(net,8) if net is not None else None,
                "pnl_rate_pct": round(roi,4) if roi is not None else None,
                "status": "已平仓" if raw.get("closed_at") else "持仓中",
                "win": bool(net > 0) if net is not None else (bool(roi > 0) if roi is not None else None),
            })
    return rows


def daily_records(limit=RETENTION_DAYS, end_date=None):
    # The user-facing/persisted daily ledger is intentionally bounded.  The
    # authoritative fill history remains untouched for ratings and audits.
    limit = max(1, min(int(limit), RETENTION_DAYS))
    trades = _all_positions()
    end = end_date or date.today()
    first_dates = [d for row in trades for d in (_date_text(row.get("opened_at")), _date_text(row.get("closed_at"))) if d]
    start = datetime.strptime(min(first_dates), "%Y-%m-%d").date() if first_dates else end
    start = min(end, max(start, end-timedelta(days=limit-1)))
    days = []
    cursor = end
    while cursor >= start and len(days) < limit:
        key = cursor.isoformat()
        opens = [row for row in trades if _date_text(row.get("opened_at")) == key]
        closes = [row for row in trades if _date_text(row.get("closed_at")) == key]
        closes.sort(key=lambda row: row.get("closed_at") or "")
        opens.sort(key=lambda row: row.get("opened_at") or "")
        net_values = [row.get("net_pnl_usdt") for row in closes if row.get("net_pnl_usdt") is not None]
        days.append({
            "date": key, "open_count": len(opens), "close_count": len(closes),
            "wins": sum(1 for row in closes if row.get("win") is True),
            "losses": sum(1 for row in closes if row.get("win") is False),
            "net_pnl_usdt": round(sum(net_values),8) if net_values else 0.0,
            "open_events": opens, "close_events": closes,
        })
        cursor -= timedelta(days=1)
    return days


def _trade_result(row):
    pnl = row.get("net_pnl_usdt")
    rate = row.get("pnl_rate_pct")
    value = pnl if pnl is not None else rate
    if value is None:
        return "待核算"
    if value > 0:
        return "盈利"
    if value < 0:
        return "亏损"
    return "持平"


def latest_trade_records(limit=LATEST_RECORD_LIMIT):
    """Return a newest-first, bounded projection of completed formal trades."""
    limit = max(1, min(int(limit), LATEST_RECORD_LIMIT))
    closed = [row for row in _all_positions() if row.get("closed_at")]
    closed.sort(
        key=lambda row: (
            str(row.get("closed_at") or ""),
            str(row.get("opened_at") or ""),
            str(row.get("token") or ""),
        ),
        reverse=True,
    )
    timeframe_labels = {
        "5m": "5分钟", "15m": "15分钟", "30m": "30分钟",
        "1h": "1小时", "4h": "4小时", "1d": "1天",
    }
    records = []
    for source in closed[:limit]:
        row = dict(source)
        timeframe = str(row.get("timeframe") or "-").lower()
        symbol = str(row.get("symbol") or "-")
        row.update({
            "symbol_label": symbol.replace("-USDT-SWAP", ""),
            "timeframe_label": timeframe_labels.get(timeframe, timeframe),
            "result": _trade_result(row),
            "pnl_rate_leveraged_pct": row.get("pnl_rate_pct"),
            "profit_amount_usdt": row.get("net_pnl_usdt"),
        })
        records.append(row)
    return records


def sync_latest_trade_records(limit=LATEST_RECORD_LIMIT):
    """Persist only the latest 20 rows; authoritative state history is untouched."""
    limit = max(1, min(int(limit), LATEST_RECORD_LIMIT))
    records = latest_trade_records(limit=limit)
    existing = _read_json(LATEST_LEDGER_FILE, {})
    if (
        isinstance(existing, dict)
        and existing.get("schema") == "qiyu_latest_auto_trade_records_v1"
        and existing.get("records") == records
        and existing.get("max_records") == LATEST_RECORD_LIMIT
    ):
        return existing
    payload = {
        "ok": True,
        "schema": "qiyu_latest_auto_trade_records_v1",
        "max_records": LATEST_RECORD_LIMIT,
        "record_count": len(records),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "records": records,
    }
    _atomic_write(LATEST_LEDGER_FILE, payload)
    return payload


def sync_daily_ledger(end_date=None):
    end = end_date or date.today()
    days = daily_records(limit=RETENTION_DAYS, end_date=end)
    payload = {
        "ok": True,
        "schema": "qiyu_daily_trade_records_v1",
        "retention_days": RETENTION_DAYS,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "records": days,
    }
    _atomic_write(LEDGER_FILE, payload)
    return payload


def _prune_audit(cutoff_date):
    if not AUDIT_FILE.exists():
        return
    kept = []
    try:
        for line in AUDIT_FILE.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if str(row.get("date") or "") >= cutoff_date:
                    kept.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
            except Exception:
                continue
        handle = tempfile.NamedTemporaryFile(delete=False, dir=str(AUTO_DIR), mode="w", encoding="utf-8")
        try:
            if kept:
                handle.write("\n".join(kept) + "\n")
            handle.flush(); handle.close(); Path(handle.name).replace(AUDIT_FILE)
        except Exception:
            try: Path(handle.name).unlink()
            except Exception: pass
            raise
    except Exception:
        return


def format_report(day):
    lines = [
        "=== 栖语每日自动交易记录 ===",
        "📅 日期: %s" % day["date"],
        "📥 开仓: %s 笔" % day["open_count"],
        "📤 平仓: %s 笔（盈利 %s / 亏损 %s）" % (day["close_count"],day["wins"],day["losses"]),
        "💰 已实现净盈亏: %+.6f USDT" % float(day["net_pnl_usdt"]),
    ]
    if day["open_events"]:
        lines.append("\n--- 今日开仓 ---")
        for row in day["open_events"]:
            side = "做空" if str(row.get("side")).lower() == "short" else "做多"
            lines.append("• %s %s｜%s｜%s %s｜开仓价 %s" % (
                _time_text(row.get("opened_at")), row.get("symbol","-").split("-")[0],
                row.get("strategy_name") or "未命名策略", row.get("timeframe"), side,
                row.get("entry_price") if row.get("entry_price") is not None else "-"))
    if day["close_events"]:
        lines.append("\n--- 今日平仓 ---")
        for row in day["close_events"]:
            icon = "✅" if row.get("win") else "❌"
            rate = "%+.2f%%" % row["pnl_rate_pct"] if row.get("pnl_rate_pct") is not None else "收益率待核算"
            lines.append("%s %s %s｜%s｜%s｜净盈亏 %+.6f USDT（%s）" % (
                icon, _time_text(row.get("closed_at")), row.get("symbol","-").split("-")[0],
                row.get("strategy_name") or "未命名策略", row.get("close_type") or "平仓",
                float(row.get("net_pnl_usdt") or 0.0), rate))
    if not day["open_events"] and not day["close_events"]:
        lines.append("\n今日无自动开仓或平仓记录。")
    rating_state = rating.status(refresh_if_stale=True)
    counts = defaultdict(int)
    for row in rating_state.get("ratings") or []: counts[row.get("grade") or "C"] += 1
    lines.append("\n📊 当前策略评级: S %s / A %s / B %s / C %s" % (counts["S"],counts["A"],counts["B"],counts["C"]))
    try:
        import auto_trade_system_health_ai as health_mod
        lines.append(health_mod.format_daily_report_section())
    except Exception as exc:
        lines.append("\n--- 系统健康与三AI运维 ---\n三AI巡检跳过: %s" % exc)
    lines.append("🕒 生成时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    return "\n".join(lines)


def send_daily_report(day_text=None, dry_run=False, force=False):
    target = datetime.strptime(day_text,"%Y-%m-%d").date() if day_text else date.today()
    sync_daily_ledger()
    day = daily_records(limit=1,end_date=target)[0]
    state = _read_json(STATE_FILE,{"sent_dates":{}})
    sent_dates = state.setdefault("sent_dates",{})
    cutoff = (target-timedelta(days=RETENTION_DAYS-1)).isoformat()
    for old_date in list(sent_dates):
        if old_date < cutoff:
            sent_dates.pop(old_date, None)
    if target.isoformat() in sent_dates and not force:
        return {"ok":True,"sent":False,"skipped":True,"reason":"already_sent","day":day}
    message = format_report(day)
    import auto_trade_formal_notify as notify
    result = notify.send_message(message,kind="daily_auto_trade_report",meta={"date":target.isoformat(),"open_count":day["open_count"],"close_count":day["close_count"],"net_pnl_usdt":day["net_pnl_usdt"]},dry_run=dry_run)
    audit = {"time":time.strftime("%Y-%m-%d %H:%M:%S"),"ts":time.time(),"date":target.isoformat(),"day":day,"message":message,"result":result}
    with AUDIT_FILE.open("a",encoding="utf-8") as handle: handle.write(json.dumps(audit,ensure_ascii=False,sort_keys=True)+"\n")
    _prune_audit(cutoff)
    if result.get("sent"):
        sent_dates[target.isoformat()]={"sent_at":audit["time"],"notification":result}
        _atomic_write(STATE_FILE,state)
    return {"ok":bool(result.get("ok")),"sent":bool(result.get("sent")),"day":day,"message":message,"notification":result}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--send",action="store_true"); parser.add_argument("--dry-run",action="store_true"); parser.add_argument("--force",action="store_true"); parser.add_argument("--sync",action="store_true"); parser.add_argument("--date")
    args=parser.parse_args()
    if args.send or args.dry_run:
        result=send_daily_report(args.date,dry_run=args.dry_run,force=args.force)
    elif args.sync:
        target = datetime.strptime(args.date,"%Y-%m-%d").date() if args.date else date.today()
        result=sync_daily_ledger(target)
    else:
        result=sync_daily_ledger()
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__": main()
