#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 0/1 acceptance for creation learning on VECTOR_ROOT (default /root)."""
from __future__ import print_function

import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))


def _fail(msg, **extra):
    print(json.dumps({"ok": False, "error": msg, **extra}, ensure_ascii=False, indent=2))
    return 2


def main():
    checks = []

    def add(name, ok, detail=None):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    modules = (
        "creation_attribution",
        "creation_case_store",
        "creation_learning_pack",
        "creation_live_lessons",
    )
    for name in modules:
        path = ROOT / "dual_engine_workflow_v2" / ("%s.py" % name)
        add("module_%s" % name, path.is_file(), str(path))

    qi = (ROOT / "dual_engine_workflow_v2" / "quality_inspector.py").read_text(encoding="utf-8")
    add("qi_record_hook", "record_creation_outcome" in qi)

    kimi = (ROOT / "dual_engine_workflow_v2" / "kimi_creator_runtime.py").read_text(encoding="utf-8")
    add("kimi_case_library", "case_library" in kimi)
    add("kimi_not_direction_ban", "不是研究方向禁令" in kimi)
    add("kimi_learning_slice", "_creator_learning_slice" in kimi)

    from dual_engine_workflow_v2.creation_learning_pack import (
        learning_enabled,
        pack_path,
        public_creator_library,
        refresh_pack,
        store_dir,
    )
    from dual_engine_workflow_v2.creation_case_store import record_creation_outcome

    add("learning_enabled_default", learning_enabled() is True)
    store = store_dir()
    add("store_dir_writable", store.is_dir(), str(store))

    pack = refresh_pack(cases=[], live_paths={
        "occupancy_geo_excluded": True, "by_family": {},
    }, apply_beliefs=False)
    add("refresh_pack", pack.get("schema") == "qiyu_creation_learning_pack_v1", {
        "version": pack.get("version"),
        "path": str(pack_path()),
        "remelt": pack.get("remelt_enqueue"),
    })
    add(
        "remelt_enqueue_off",
        (pack.get("remelt_enqueue") or {}).get("skipped") == "QIYU_LIVE_REMELT_ENQUEUE=0",
        pack.get("remelt_enqueue"),
    )

    lib = public_creator_library()
    add("public_library", lib.get("ok") and lib.get("not_a_gate") and "case_library" in lib)

    smoke_job = {
        "job_id": "creation_phase01_accept_smoke",
        "symbol": "ETH-USDT-SWAP",
        "timeframe": "1h",
        "trade_direction": "short",
        "research_direction": "方向型：1h趋势破位顺势做空-验收",
    }
    smoke_result = {
        "ok": False,
        "technical_completed": True,
        "outcome": "candidate_quality_failure",
        "symbol": "ETH-USDT-SWAP",
        "direction": "short",
        "attempts": [{
            "candidate": {
                "symbol": "ETH-USDT-SWAP",
                "timeframe": "1h",
                "direction": "short",
                "title_zh": "验收用趋势破位做空",
                "entry_ast": {
                    "type": "all",
                    "children": [
                        {"type": "compare", "feature": "rsi_14", "op": "lt", "value": 30.0},
                        {"type": "compare", "feature": "close_z_20", "op": "lt", "value": -1.0},
                    ],
                },
                "protective_stop_pct": 0.012,
            },
            "evaluation": {
                "quality_gate": {
                    "ok": False,
                    "failed_rules": [
                        "holding_bars_below_threshold",
                        "tp_trigger_rate_below_threshold",
                    ],
                    "metrics": {"avg_holding_bars": 2.0, "tp_trigger_rate": 0.1},
                    "structure_literacy": {
                        "ok": False,
                        "family": "no_trend_mr",
                        "features": ["rsi_14", "close_z_20"],
                    },
                },
            },
        }],
    }
    wrote = record_creation_outcome(smoke_job, smoke_result)
    case_path = store / "cases" / "creation_phase01_accept_smoke.json"
    add("record_smoke_case", bool(wrote.get("ok")) and case_path.is_file(), {
        "wrote": wrote,
        "case_path": str(case_path),
    })

    # Optional: replay one historical completed job through ingest if available.
    hist = None
    completed = ROOT / "auto_trade" / "dual_engine" / "parallel_creation" / "completed"
    if completed.is_dir():
        for path in sorted(completed.glob("creation_*.json"))[-80:]:
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            outcome = str(row.get("outcome") or "")
            if outcome != "candidate_quality_failure":
                continue
            hist = path
            break
    hist_detail = {"found": bool(hist)}
    if hist is not None:
        try:
            from dual_engine_workflow_v2 import quality_inspector as qi
            job = json.loads(hist.read_text(encoding="utf-8"))
            # Prefer artifact full result when present; else use compact job itself.
            result = job.get("result") if isinstance(job.get("result"), dict) else job
            out = qi.ingest_from_creation_job(job, result)
            jid = str(job.get("job_id") or hist.stem)
            case_file = store / "cases" / ("%s.json" % jid)
            hist_detail.update({
                "job_id": jid,
                "ingest": {"ok": out.get("ok"), "ingested": out.get("ingested")},
                "case_exists": case_file.is_file(),
            })
            add("ingest_historical_quality_failure", out.get("ok") is True, hist_detail)
        except Exception as exc:
            hist_detail["error"] = str(exc)[:240]
            add("ingest_historical_quality_failure", False, hist_detail)
    else:
        add("ingest_historical_quality_failure", True, {"skipped": "no_quality_failure_job"})

    failed = [c for c in checks if not c["ok"]]
    report = {
        "ok": not failed,
        "phase": "0+1",
        "root": str(ROOT),
        "checks": checks,
        "failed": failed,
        "pack_path": str(pack_path()),
        "store_dir": str(store),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
