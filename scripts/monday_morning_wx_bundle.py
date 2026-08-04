#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Monday 08:00 Asia/Shanghai Wx bundle — single schedule, deduped sends.

Order:
  1) 栖语策略生态演化周报
  2) 系统交易频率预测·摘要
  3) 策略健康诊断·周报

All messages share the same batch timestamp footer.
"""
from __future__ import print_function

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

STATE_PATH = ROOT / "auto_trade" / "monday_morning_wx_bundle_state.json"
BEIJING_FMT = "%Y-%m-%d %H:%M:%S"


def _now():
    return datetime.now().strftime(BEIJING_FMT)


def _week_bucket():
    return datetime.now().strftime("%Y-W%W")


def _read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def _already_sent_this_week():
    st = _read(STATE_PATH, {})
    return st.get("week_bucket") == _week_bucket() and st.get("completed")


def main():
    if _already_sent_this_week() and os.environ.get("FORCE_MONDAY_BUNDLE") != "1":
        print(json.dumps({"ok": True, "skipped": True, "reason": "already_sent",
                          "state": _read(STATE_PATH, {})}, ensure_ascii=False))
        return 0

    batch_time = _now()
    out = {"ok": True, "batch_time": batch_time, "week_bucket": _week_bucket(),
           "steps": []}

    import auto_trade_strategy_lifecycle as lifecycle
    eco = lifecycle.maybe_write_weekly_report(
        force=False, push_wx=True, batch_time=batch_time)
    out["steps"].append({"step": "ecology_weekly", "ok": bool(eco.get("ok")),
                         "wx_sent": eco.get("wx_sent")})

    import auto_trade_system_forecast as forecast
    fc = forecast.run_forecast(push_wx=True, batch_time=batch_time)
    rep = fc.get("report") or {}
    out["steps"].append({
        "step": "system_frequency_forecast",
        "ok": bool(fc.get("ok")),
        "generated_at": rep.get("generated_at"),
        "wx": (fc.get("wx") or {}).get("ok"),
    })

    import auto_trade_strategy_dynamic_optimizer as opt
    health = opt.weekly_health_diagnosis(batch_time=batch_time, push_wx=True)
    out["steps"].append({"step": "strategy_health_weekly",
                         "ok": bool(health.get("ok")),
                         "n": len(health.get("reports") or [])})

    out["completed"] = True
    _atomic(STATE_PATH, {
        "week_bucket": _week_bucket(),
        "batch_time": batch_time,
        "completed": True,
        "steps": out["steps"],
    })
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
