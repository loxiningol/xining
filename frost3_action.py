#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜叁行动 — parallel directions → pending (≥2 target).

Hard avoid: ADA/LTC/NG/XRP any TF. Learn CL15m archive traps.
Caps: hyp audit revise≤2; Quick repair≤3; Full repair≤3; Sim repair≤3.
"""
from __future__ import print_function

import copy
import json
import os
import re
import sys
import threading
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d
import frost3_step1 as step1

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
f2.AVOID = [
    ("ADA-USDT-SWAP", None),
    ("LTC-USDT-SWAP", None),
    ("NG-USDT-SWAP", None),
    ("XRP-USDT-SWAP", None),
]

OUT = "/root/auto_trade/dual_engine"
PREFIX = "frost3"
MAX_AUDIT_REVISE = 2
MAX_QUICK_REPAIR = 3
MAX_FULL_REPAIR = 3
MAX_SIM_REPAIR = 3
TARGET_PENDING = 2


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write(name, obj):
    path = os.path.join(OUT, name)
    open(path, "w").write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


def _safe(s):
    s = str(s or "x")
    for ch in (".", " ", "/", ":", "+", "%", "-"):
        s = s.replace(ch, "p" if ch in (".", "-") else "_")
    return s[:40]


# ─── DSL builders (CL-trap aware) ──────────────────────────────────────

def build_dsl_from_direction(row, idx):
    symbol = str(row.get("symbol") or "").upper().split("|")[0]
    timeframe = str(row.get("timeframe") or "15m")
    direction = str(row.get("direction") or "long").lower()
    logic = str(row.get("logic_class") or "custom")
    thesis = " ".join([
        str(row.get("thesis") or ""),
        str(row.get("entry_sketch") or ""),
        str(row.get("exit_sketch") or ""),
    ])
    # Prefer explicit sketch_to_dsl from frost2, then overlay CL-safe tweaks
    dsl = f2.sketch_to_dsl(symbol, timeframe, direction, logic, row, idx)
    dsl["key"] = "frost3_%s_%s_%s_%d" % (
        symbol.split("-")[0].lower(), timeframe, _safe(logic)[:14], idx)
    dsl["name"] = "寒霜叁-%s-%s-%s" % (symbol.split("-")[0], timeframe, logic)
    # CL trap: ensure enough room / not ultra-tight single filter
    hold = int(dsl.get("max_hold_bars") or 16)
    if timeframe == "5m":
        hold = max(hold, 28)
    elif timeframe == "15m":
        hold = max(hold, 20)
    else:
        hold = max(hold, 12)
    dsl["max_hold_bars"] = hold

    # For short exhaustion-like: raise rsi floor away from 54 marginal + add mild cci
    low = (thesis + " " + logic).lower()
    entry = list((dsl.get("entry") or {}).get("all") or [])
    if direction == "short" and ("exhaust" in low or "fade" in low or "pullback" in low):
        # rewrite safer short
        entry = [
            {"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 56.0}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.2}},
            {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
            {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 60.0}},
        ]
        dsl["entry"] = {"all": entry}
        dsl["exit"] = {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 42.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"},
             "role": "invalidation"},
        ]}
        dsl["max_hold_bars"] = max(hold, 20)
    elif direction == "long" and "impulse" in low:
        entry = [
            {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema8"}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.2}},
        ]
        dsl["entry"] = {"all": entry}
        dsl["exit"] = {"any": [
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 48.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"},
             "role": "invalidation"},
        ]}
        dsl["max_hold_bars"] = max(hold, 32)
    elif direction == "long" and ("reclaim" in low or "range" in low):
        entry = [
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
            {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_low20"}},
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 42.0}},
            {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 58.0}},
            {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
            {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -0.5}},
            {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.0}},
        ]
        dsl["entry"] = {"all": entry}
        dsl["exit"] = {"any": [
            {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 65.0},
             "role": "take_profit"},
            {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"},
             "role": "invalidation"},
        ]}
        dsl["max_hold_bars"] = max(hold, 24)

    return f2.ensure_dsl(dsl, symbol, timeframe)


def make_book(row, idx):
    symbol = str(row.get("symbol") or "").upper().split("|")[0]
    timeframe = str(row.get("timeframe") or "15m")
    dsl = build_dsl_from_direction(row, idx)
    return {
        "title": "寒霜叁#%d-%s" % (idx, row.get("logic_class")),
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": str(row.get("direction") or "long").lower(),
        "logic_class": str(row.get("logic_class") or "custom"),
        "thesis": row.get("thesis"),
        "entry_sketch": row.get("entry_sketch"),
        "exit_sketch": row.get("exit_sketch"),
        "avoid_from_postmortem": row.get("avoid_death") or row.get("why_avoids_cl_traps"),
        "diff_vs_live": row.get("diff_vs_live"),
        "dsl": dsl,
        "gate_mode": "frost2",
        "source": "frost3_direction",
        "dir_rank": idx,
    }


# ─── GLM audit / repair ────────────────────────────────────────────────

AUDIT_PROMPT = """你是GLM-5.2。只输出JSON。审计寒霜叁假设书。
硬禁：ADA/LTC/NG/XRP；禁止CL15m exhaustion同构。
对照CL陷阱：trades预期≥12；避免边际rsi刚过线；需结构失效出场；logic_destruction稳健。
JSON：{"decision":"pass|reject|revise","score":0到100,"issues":["..."],
"revise":{"entry_sketch":null,"exit_sketch":null,"param_tweaks":{},"why":"..."},
"reason_zh":"..."}
"""


def glm_audit(book, report):
    payload = {
        "book": {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "thesis": book.get("thesis"), "entry": (book.get("dsl") or {}).get("entry"),
            "exit": (book.get("dsl") or {}).get("exit"),
            "max_hold_bars": (book.get("dsl") or {}).get("max_hold_bars"),
            "diff_vs_live": book.get("diff_vs_live"),
        },
        "cl_traps": (report or {}).get("trap_checklist") or [],
        "banned": sorted(step1.AVOID_SYMBOLS),
    }
    ai = d._ai_json("glm", AUDIT_PROMPT, payload, max_tokens=900, temperature=0.1)
    parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
    raw = ai.get("raw_preview") or ""
    if not parsed and raw:
        m = re.search(r"\{[\s\S]*\}", str(raw))
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
    return parsed or {"decision": "pass", "reason_zh": "audit_fallback_pass", "fallback": True}, ai


def apply_revise(book, revise):
    book = copy.deepcopy(book)
    if not isinstance(revise, dict):
        return book
    tweaks = revise.get("param_tweaks") or {}
    # apply numeric tweaks on matching features
    for phase in ("entry", "exit"):
        block = (book.get("dsl") or {}).get(phase) or {}
        rows = block.get("all") or block.get("any") or []
        for row in rows:
            feat = ((row.get("left") or {}).get("feature"))
            if feat in tweaks and isinstance(tweaks[feat], (int, float)):
                if isinstance(row.get("right"), dict) and "value" in row["right"]:
                    row["right"]["value"] = float(tweaks[feat])
    if revise.get("entry_sketch"):
        book["entry_sketch"] = revise["entry_sketch"]
    if revise.get("exit_sketch"):
        book["exit_sketch"] = revise["exit_sketch"]
    # rebuild from sketches if provided
    if revise.get("entry_sketch") or revise.get("exit_sketch"):
        row = {
            "symbol": book["symbol"], "timeframe": book["timeframe"],
            "direction": book["direction"], "logic_class": book["logic_class"],
            "thesis": book.get("thesis"),
            "entry_sketch": book.get("entry_sketch"),
            "exit_sketch": book.get("exit_sketch"),
        }
        book["dsl"] = build_dsl_from_direction(row, int(book.get("dir_rank") or 1))
    book["dsl"] = f2.ensure_dsl(book["dsl"], book["symbol"], book["timeframe"])
    return book


def local_tweak(book, n, failed_step=""):
    book = copy.deepcopy(book)
    dsl = book["dsl"]
    entries = list((dsl.get("entry") or {}).get("all") or [])
    step = str(failed_step or "")
    for row in entries:
        feat = (row.get("left") or {}).get("feature")
        right = row.get("right") or {}
        if "value" not in right:
            continue
        v = float(right["value"])
        if feat == "rsi14" and row.get("op") == "gt":
            # CL trap: don't sit on 54 — move away or loosen for trades
            if "walk_forward" in step or "wf" in step:
                right["value"] = max(50.0, v - 1.0 * n)  # loosen for more trades
            else:
                right["value"] = v + 0.5 * n
        elif feat == "z20" and row.get("op") == "gt":
            if "walk_forward" in step:
                right["value"] = max(0.05, v - 0.05 * n)
            else:
                right["value"] = v + 0.05 * n
        elif feat == "cci" and row.get("op") == "gt":
            # keep cci soft (≥40) to preserve trade count
            right["value"] = max(40.0, v - 5.0 * n) if "walk" in step else min(120.0, v + 5 * n)
    dsl["entry"] = {"all": entries}
    hold = int(dsl.get("max_hold_bars") or 16)
    if "friction" in step or "mc" in step:
        dsl["max_hold_bars"] = max(10, hold - 2 * n)
    elif "walk" in step:
        dsl["max_hold_bars"] = hold + 4 * n
    book["dsl"] = f2.ensure_dsl(dsl, book["symbol"], book["timeframe"])
    # bump key
    book["dsl"]["key"] = (book["dsl"]["key"][:80] + "_t%d" % n)[:100]
    return book


REPAIR_PROMPT = """你是GLM-5.2。只输出JSON。寒霜叁闸门修复（增量，禁止换标的/换大逻辑类）。
对照CL陷阱：勿把交易数压到<10；勿边际rsi≈54入场；保留结构失效出场。
JSON：{"ok":true,"dsl_patch":{"entry_all":null,"exit_any":null,"max_hold_bars":null},
"param_tweaks":{},"why":"..."}
entry_all/exit_any 若提供则为完整条件数组（DSL叶子格式）。
"""


def glm_repair(book, packs, failed_step, stage):
    payload = {
        "stage": stage,
        "failed_step": failed_step,
        "symbol": book["symbol"],
        "timeframe": book["timeframe"],
        "logic_class": book["logic_class"],
        "dsl": book.get("dsl"),
        "metrics": packs.get("base_metrics") or packs.get("metrics"),
        "quick": {k: packs.get(k) for k in ("quick_pass", "failed_step", "logic_destruction")},
        "full": packs.get("full"),
        "cl_traps": [
            "trades>=10 for WF folds",
            "no marginal exhaustion entry",
            "keep invalidation structural",
        ],
    }
    ai = d._ai_json("glm", REPAIR_PROMPT, payload, max_tokens=1100, temperature=0.15)
    parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
    raw = ai.get("raw_preview") or ""
    if not parsed and raw:
        m = re.search(r"\{[\s\S]*\}", str(raw))
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
    if not parsed:
        return None, ai
    book2 = copy.deepcopy(book)
    patch = parsed.get("dsl_patch") or {}
    if isinstance(patch.get("entry_all"), list) and patch["entry_all"]:
        book2["dsl"]["entry"] = {"all": patch["entry_all"]}
    if isinstance(patch.get("exit_any"), list) and patch["exit_any"]:
        book2["dsl"]["exit"] = {"any": patch["exit_any"]}
    if patch.get("max_hold_bars"):
        try:
            book2["dsl"]["max_hold_bars"] = int(patch["max_hold_bars"])
        except Exception:
            pass
    tweaks = parsed.get("param_tweaks") or {}
    if tweaks:
        book2 = apply_revise(book2, {"param_tweaks": tweaks})
    book2["dsl"]["key"] = (_safe(book2["dsl"].get("key")) + "_r")[:100]
    book2["dsl"] = f2.ensure_dsl(book2["dsl"], book2["symbol"], book2["timeframe"])
    return book2, ai


# ─── Per-direction pipeline ────────────────────────────────────────────

_lock = threading.Lock()
_status = {"directions": {}, "pending_keys": [], "updated_at": None}


def _upd(dir_id, **kw):
    with _lock:
        row = _status["directions"].setdefault(str(dir_id), {})
        row.update(kw)
        row["updated_at"] = _now()
        _status["updated_at"] = _now()
        _write("%s_status.json" % PREFIX, _status)


def _finish(dir_id, out):
    """Update status without leaking dir_id into **kwargs."""
    payload = {k: v for k, v in (out or {}).items() if k != "dir_id"}
    _upd(dir_id, **payload)
    return out


def process_direction(row, idx, report):
    dir_id = "d%d_%s_%s" % (idx, str(row.get("symbol")).split("-")[0].lower(),
                            row.get("timeframe"))
    hist = []
    try:
        if f2.avoided(str(row.get("symbol")), str(row.get("timeframe"))):
            out = {
                "dir_id": dir_id, "ok": False, "failed_step": "avoid_banned_symbol",
                "reason": "symbol in ADA/LTC/NG/XRP ban list", "hist": hist,
            }
            _finish(dir_id, out)
            return out

        book = make_book(row, idx)
        _upd(dir_id, stage="audit", symbol=book["symbol"], tf=book["timeframe"],
             logic=book["logic_class"], key=(book.get("dsl") or {}).get("key"))

        # Hypothesis audit ≤2 revise
        audit_ok = False
        for atry in range(MAX_AUDIT_REVISE + 1):
            decision, ai = glm_audit(book, report)
            hist.append({"stage": "audit", "try": atry, "decision": decision.get("decision"),
                         "reason": decision.get("reason_zh"), "fallback": decision.get("fallback")})
            dec = str(decision.get("decision") or "pass").lower()
            if dec == "pass":
                audit_ok = True
                break
            if dec == "revise" and atry < MAX_AUDIT_REVISE:
                book = apply_revise(book, decision.get("revise") or {})
                continue
            # reject
            out = {
                "dir_id": dir_id, "ok": False, "failed_step": "hyp_audit_reject",
                "reason": decision.get("reason_zh") or decision.get("issues"),
                "hist": hist, "book_key": (book.get("dsl") or {}).get("key"),
            }
            _finish(dir_id, out)
            return out
        if not audit_ok:
            out = {
                "dir_id": dir_id, "ok": False, "failed_step": "hyp_audit_reject",
                "reason": "audit not passed after revises", "hist": hist,
            }
            _finish(dir_id, out)
            return out

        # Quick ≤3 repairs
        packs = None
        for qtry in range(MAX_QUICK_REPAIR + 1):
            _upd(dir_id, stage="quick", attempt=qtry)
            packs = f2.quick_suite(book)
            hist.append({
                "stage": "quick", "try": qtry, "pass": packs.get("quick_pass"),
                "failed_step": packs.get("failed_step"),
                "fp": (packs.get("base_metrics") or {}).get("fold_positive"),
                "folds": (packs.get("base_metrics") or {}).get("folds"),
                "tr": (packs.get("base_metrics") or {}).get("trades"),
                "dest": (packs.get("logic_destruction") or {}).get("pass"),
            })
            if packs.get("quick_pass"):
                break
            if qtry >= MAX_QUICK_REPAIR:
                out = {
                    "dir_id": dir_id, "ok": False,
                    "failed_step": packs.get("failed_step") or "quick_exhausted",
                    "reason": "Quick failed after %d repairs" % MAX_QUICK_REPAIR,
                    "metrics": packs.get("base_metrics"),
                    "dest": (packs.get("logic_destruction") or {}).get("pass"),
                    "hist": hist, "book_key": (book.get("dsl") or {}).get("key"),
                }
                _finish(dir_id, out)
                return out
            # repair: local tweak then GLM
            book = local_tweak(book, qtry + 1, packs.get("failed_step"))
            repaired, _ai = glm_repair(book, packs, packs.get("failed_step"), "quick")
            if repaired:
                book = repaired
            hist.append({"stage": "quick_repair", "try": qtry, "ok": bool(repaired)})

        # Full ≤3 repairs
        for ftry in range(MAX_FULL_REPAIR + 1):
            _upd(dir_id, stage="full", attempt=ftry)
            packs = f2.full_suite(book, packs)
            hist.append({
                "stage": "full", "try": ftry, "pass": packs.get("full_pass"),
                "failed_step": packs.get("failed_step"),
                "friction": (packs.get("full") or {}).get("friction_sharpe"),
                "mc": ((packs.get("full") or {}).get("mc") or {}).get("beat_ratio"),
            })
            if packs.get("full_pass"):
                break
            if ftry >= MAX_FULL_REPAIR:
                out = {
                    "dir_id": dir_id, "ok": False,
                    "failed_step": packs.get("failed_step") or "full_exhausted",
                    "reason": "Full failed after %d repairs" % MAX_FULL_REPAIR,
                    "full": packs.get("full"),
                    "hist": hist, "book_key": (book.get("dsl") or {}).get("key"),
                }
                _finish(dir_id, out)
                return out
            book = local_tweak(book, ftry + 1, packs.get("failed_step"))
            repaired, _ai = glm_repair(book, packs, packs.get("failed_step"), "full")
            if repaired:
                book = repaired
            # after DSL change must re-quick quickly
            q2 = f2.quick_suite(book)
            hist.append({"stage": "re_quick_after_full_repair", "pass": q2.get("quick_pass"),
                         "failed_step": q2.get("failed_step")})
            if not q2.get("quick_pass"):
                packs = q2
                # continue full loop will call full_suite on failing quick — force fail path
                packs["full_pass"] = False
                packs["failed_step"] = q2.get("failed_step") or "quick_regressed"
                continue
            packs = q2

        # Sim ≤3 repairs then formal
        for stry in range(MAX_SIM_REPAIR + 1):
            _upd(dir_id, stage="sim_formal", attempt=stry)
            sf = f2.run_sim_formal(book, packs)
            hist.append({
                "stage": "sim_formal", "try": stry,
                "failed_step": sf.get("failed_step"),
                "sim_ds": (sf.get("sim") or {}).get("wr_deepseek_sim"),
                "sim_qw": (sf.get("sim") or {}).get("wr_qwen_sim"),
                "formal_approved": (sf.get("formal") or {}).get("approved"),
                "pending": (sf.get("pending") or {}).get("key"),
            })
            if (sf.get("pending") or {}).get("ok"):
                with _lock:
                    k = (sf.get("pending") or {}).get("key")
                    if k and k not in _status["pending_keys"]:
                        _status["pending_keys"].append(k)
                out = {
                    "dir_id": dir_id, "ok": True, "failed_step": None,
                    "pending": sf.get("pending"), "sim": sf.get("sim"),
                    "formal": sf.get("formal"),
                    "book_key": (book.get("dsl") or {}).get("key"),
                    "hist": hist,
                }
                _finish(dir_id, out)
                return out
            # if formal failed but sim ok — stop (no sim repair helps formal much)
            fs = sf.get("failed_step")
            if fs == "formal_review" or fs == "pending_ingest":
                out = {
                    "dir_id": dir_id, "ok": False, "failed_step": fs,
                    "reason": (sf.get("formal") or {}).get("reason") or (
                        sf.get("pending") or {}).get("reason"),
                    "sim": sf.get("sim"), "formal": sf.get("formal"),
                    "pending": sf.get("pending"), "hist": hist,
                    "book_key": (book.get("dsl") or {}).get("key"),
                }
                _finish(dir_id, out)
                return out
            if stry >= MAX_SIM_REPAIR:
                out = {
                    "dir_id": dir_id, "ok": False,
                    "failed_step": fs or "sim_exhausted",
                    "reason": "Sim/formal failed after %d repairs" % MAX_SIM_REPAIR,
                    "sim": sf.get("sim"), "hist": hist,
                    "book_key": (book.get("dsl") or {}).get("key"),
                }
                _finish(dir_id, out)
                return out
            # incremental sim repair only if either WR < 50 as user said; else still try soft tweak
            sim = sf.get("sim") or {}
            wr_ds = float(sim.get("wr_deepseek_sim") or 0)
            wr_qw = float(sim.get("wr_qwen_sim") or 0)
            book = local_tweak(book, stry + 1, "sim")
            repaired, _ai = glm_repair(book, packs, "sim_review", "sim")
            if repaired:
                book = repaired
            # revalidate quick+full before next sim
            q3 = f2.quick_suite(book)
            if not q3.get("quick_pass"):
                hist.append({"stage": "sim_repair_quick_fail", "failed": q3.get("failed_step")})
                continue
            packs = f2.full_suite(book, q3)
            if not packs.get("full_pass"):
                hist.append({"stage": "sim_repair_full_fail", "failed": packs.get("failed_step")})
                continue

        out = {
            "dir_id": dir_id, "ok": False, "failed_step": "unknown_exhausted",
            "hist": hist, "book_key": (book.get("dsl") or {}).get("key"),
        }
        _finish(dir_id, out)
        return out
    except Exception as exc:
        out = {
            "dir_id": dir_id, "ok": False, "failed_step": "exception",
            "reason": str(exc), "trace": traceback.format_exc()[-2000:],
            "hist": hist,
        }
        _finish(dir_id, out)
        return out


def archive_direction(result):
    arch = os.path.join(OUT, "archive", "frost3_%s" % result.get("dir_id"))
    os.makedirs(arch, exist_ok=True)
    open(os.path.join(arch, "result.json"), "w").write(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
    return arch


def main():
    print("=== 寒霜叁 START ===", _now(), flush=True)
    report = step1.main()
    dirs = list(report.get("directions") or [])[:3]
    if len(dirs) < 3:
        raise SystemExit("need ≥3 directions, got %d" % len(dirs))
    _write("%s_adopted_directions.json" % PREFIX, {"at": _now(), "directions": dirs})

    books = [make_book(row, i + 1) for i, row in enumerate(dirs)]
    _write("%s_hypotheses.json" % PREFIX, {"books": books})

    results = []
    print("[frost3] parallel process", len(dirs), "directions", flush=True)
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {
            ex.submit(process_direction, row, i + 1, report): i
            for i, row in enumerate(dirs)
        }
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:
                res = {
                    "dir_id": "d%d" % (i + 1), "ok": False,
                    "failed_step": "exception", "reason": str(exc),
                }
            results.append(res)
            if not res.get("ok"):
                arch = archive_direction(res)
                res["archive_path"] = arch
                print("[frost3] ARCHIVED", res.get("dir_id"), res.get("failed_step"),
                      arch, flush=True)
            else:
                print("[frost3] PENDING", res.get("dir_id"),
                      (res.get("pending") or {}).get("key"), flush=True)

    pending = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    end = {
        "at": _now(),
        "op": "寒霜叁",
        "target_pending": TARGET_PENDING,
        "pending_n": len(pending),
        "pending_keys": [((r.get("pending") or {}).get("key")) for r in pending],
        "failed_n": len(failed),
        "failures": [
            {
                "dir_id": r.get("dir_id"),
                "symbol": ((_status.get("directions") or {}).get(r.get("dir_id") or {}) or {}).get("symbol"),
                "failed_step": r.get("failed_step"),
                "reason": r.get("reason"),
                "metrics": r.get("metrics"),
                "full": r.get("full"),
                "sim": {
                    "ds": (r.get("sim") or {}).get("wr_deepseek_sim"),
                    "qw": (r.get("sim") or {}).get("wr_qwen_sim"),
                } if r.get("sim") else None,
                "archive_path": r.get("archive_path"),
            }
            for r in failed
        ],
        "results": results,
        "success": len(pending) >= TARGET_PENDING,
        "direction_report": "%s_direction_report.json" % PREFIX,
    }
    _write("%s_end_report.json" % PREFIX, end)
    _write("%s_status.json" % PREFIX, _status)

    # update cont-like status
    st_path = os.path.join(OUT, "frost3_run_status.json")
    open(st_path, "w").write(json.dumps({
        "op": "寒霜叁",
        "finished_at": _now(),
        "pending_keys": end["pending_keys"],
        "success": end["success"],
        "failures": end["failures"],
    }, ensure_ascii=False, indent=2) + "\n")

    print("=== 寒霜叁 END ===", json.dumps({
        "pending_n": end["pending_n"],
        "pending_keys": end["pending_keys"],
        "success": end["success"],
        "failures": [
            {"dir": f.get("dir_id"), "step": f.get("failed_step"), "reason": f.get("reason")}
            for f in end["failures"]
        ],
    }, ensure_ascii=False), flush=True)
    return 0 if end["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
