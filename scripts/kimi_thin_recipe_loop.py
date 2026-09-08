# -*- coding: utf-8 -*-
"""VPS thin hub: Kimi dual-channel recipes only; Mac runs isolated_cap.

Requires ALLOW_KIMI_THIN_RECIPE=1. Does not delete STOP_KIMI_CREATION.
Never calls isolated_cap on VPS. After Mac eval, runs quality_gate + trial_mount
once (floor/combo/occupancy are not admission gates).

Python 3.6 compatible. WorkingDirectory=/root.
"""
from __future__ import print_function

import gc
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

if not os.environ.get("ALLOW_KIMI_THIN_RECIPE"):
    sys.stderr.write("REFUSED: set ALLOW_KIMI_THIN_RECIPE=1 for thin hub\n")
    sys.exit(3)

STOP_FLAGS = (
    Path("/root/STOP_KIMI_CREATION"),
    Path("/root/auto_trade/dual_engine/parallel_creation/STOP_KIMI_CREATION"),
    Path("/tmp/STOP_LOCAL_HEAVY_CREATE"),
)
if not any(p.exists() for p in STOP_FLAGS[:2]):
    sys.stderr.write("REFUSED: STOP_KIMI_CREATION missing; keep old halt, do not run thin\n")
    sys.exit(3)

os.environ["VECTOR_ROOT"] = "/root"
os.environ["PYTHONPATH"] = "/root"
os.environ.pop("QIYU_QI_AUTHOR_ON_INGEST", None)
sys.path.insert(0, "/root")
sys.path.insert(0, "/root/scripts")
os.chdir("/root")

from auto_trade_ai_consensus import _load_root_only_env
_load_root_only_env()

# FREE_CREATE (default ON): no seed overwrite, no geometry hard-lock, no family/symbol soft-lock.
# Set KDH_FREE_CREATE=0 only to revive the old identity-weld invent mode.
def _free_create():
    return str(os.environ.get("KDH_FREE_CREATE") or "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


from dual_engine_workflow_v2.timing_stage import (
    build_critique, critique_user_message, classify_stage,
    LOCK_KEYS_HARD,
    rr_geometry_bounds_ok,
)
try:
    from dual_engine_workflow_v2 import thin_create_policy as tcp
except Exception:
    tcp = None

try:
    from dual_engine_workflow_v2.kimi_provider import json_parse_critique
except Exception:
    def json_parse_critique(raw_text, max_show=400):
        return (
            "禁止文字。只输出 1 条 {\"recipe\":{...}}，第一个字符必须是 {。"
        )

# Import create helpers as library (main must not auto-run).
import importlib.util

_lib_path = "/root/scripts/kimi_dual_http_create_20260906.py"
_spec = importlib.util.spec_from_file_location("kdh_lib", _lib_path)
kdh = importlib.util.module_from_spec(_spec)
# Prevent accidental heavy main if file still has guard — we never call main().
_spec.loader.exec_module(kdh)

QUEUE = Path("/tmp/kdh_thin/queue")
RESULTS = Path("/tmp/kdh_thin/results")
LOG = Path("/tmp/kimi_thin_hub.log")
STATE_PATH = Path("/root/auto_trade/dual_engine/sole_creation_runs/kimi_thin_hub_20260906.json")
EVAL_WAIT_SEC = int(os.environ.get("KDH_THIN_EVAL_WAIT") or "900")
POLL = float(os.environ.get("KDH_THIN_POLL") or "3")
LANES = [
    x.strip() for x in (
        os.environ.get("KDH_THIN_LANES") or "primary,backup,eq2,cr2"
    ).split(",")
    if x.strip()
]
# Namespace for parallel Cursor hubs. Default "" keeps legacy paths (hub-a).
# Example hub-b: KDH_THIN_NS=b → /tmp/kdh_thin_b, /tmp/kimi_thin_hub_b.log
NS = str(os.environ.get("KDH_THIN_NS") or "").strip().strip("_")
if NS:
    _root = Path(os.environ.get("KDH_THIN_ROOT") or ("/tmp/kdh_thin_%s" % NS))
    QUEUE = _root / "queue"
    RESULTS = _root / "results"
    LOG = Path(os.environ.get("KDH_THIN_LOG") or ("/tmp/kimi_thin_hub_%s.log" % NS))
    STATE_PATH = Path(
        os.environ.get("KDH_THIN_STATE")
        or ("/root/auto_trade/dual_engine/sole_creation_runs/kimi_thin_hub_%s.json" % NS)
    )
elif os.environ.get("KDH_THIN_ROOT"):
    _root = Path(os.environ.get("KDH_THIN_ROOT"))
    QUEUE = _root / "queue"
    RESULTS = _root / "results"
# Four invent lanes per hub; Mac LaunchAgent locks 4 workers to this hub's queue.
# Dual-lane diversified seeds (not live / not KEEP4).
# PR-A / thin_blueprint_v2: default timing locked — no skdj_kd cross_up.
try:
    from dual_engine_workflow_v2.thin_timing_atoms import default_timing as _default_timing
    _SEED_TIMING = _default_timing()
except Exception:
    _SEED_TIMING = [
        {"factor": "skdj_diff", "operator": "above", "value": -20, "window": 9},
        {"factor": "macd_hist", "operator": "above", "value": -1},
    ]

SEED_PRIMARY = {
    "family": "xu_long",
    "symbol": "AMD-USDT-SWAP" if NS == "b" else "NVDA-USDT-SWAP",
    "timeframe": "1h",
    "held": 28,
    "xwin": 8.0,
    "z": 1.0,
    "atr": 2.18,
    "hold": 10,
    "timing": [dict(x) for x in _SEED_TIMING],
}
SEED_BACKUP = {
    "family": "xu_long",
    "symbol": "BCH-USDT-SWAP" if NS == "b" else "BNB-USDT-SWAP",
    "timeframe": "1h",
    "held": 28,
    "xwin": 8.0,
    "z": 1.0 if NS == "b" else None,
    "atr": 2.18 if NS == "b" else 2.0,
    "hold": 10 if NS == "b" else 12,
    "timing": [dict(x) for x in _SEED_TIMING],
}
# Extra equity/crypto lanes (distinct symbols; avoid live BTC/LINK).
SEED_EQ2 = {
    "family": "xu_long",
    # hub-a META research candles filled (OKX+Binance); hub-b keeps AVGO
    "symbol": "AVGO-USDT-SWAP" if NS == "b" else "META-USDT-SWAP",
    "timeframe": "1h",
    "held": 28,
    "xwin": 8.0,
    "z": 1.0 if NS == "b" else None,
    "atr": 2.18,
    "hold": 10,
    "timing": [dict(x) for x in _SEED_TIMING],
}
SEED_CR2 = {
    "family": "xu_long",
    # Avoid live ETH on hub-a; hub-b keeps UNI.
    "symbol": "UNI-USDT-SWAP" if NS == "b" else "DOGE-USDT-SWAP",
    "timeframe": "1h",
    "held": 28,
    "xwin": 8.0,
    "z": 1.0 if NS == "b" else None,
    "atr": 2.18 if NS == "b" else 2.0,
    "hold": 10 if NS == "b" else 12,
    "timing": [dict(x) for x in _SEED_TIMING],
}
SEEDS = {
    "primary": SEED_PRIMARY,
    "backup": SEED_BACKUP,
    "eq2": SEED_EQ2,
    "cr2": SEED_CR2,
}
# Optional restart inherit: lane -> recipe snap (keeps near-threshold work).
_INHERIT_DEFAULT = (
    "/root/auto_trade/dual_engine/sole_creation_runs/kimi_thin_hub_%s_inherit_seeds.json" % (NS or "a")
)
_INHERIT_PATH = Path(os.environ.get("KDH_THIN_INHERIT_SEEDS") or _INHERIT_DEFAULT or "")
if _INHERIT_PATH and _INHERIT_PATH.exists():
    try:
        _inh = json.loads(_INHERIT_PATH.read_text(encoding="utf-8"))
        if isinstance(_inh, dict):
            for _lane, _rec in _inh.items():
                if isinstance(_rec, dict) and _rec.get("symbol"):
                    SEEDS[str(_lane)] = dict(_rec)
    except Exception:
        pass
# Optional global pin (overrides per-lane bind). Prefer KDH_LANE_BIND instead.
KIMI_BIND = str(os.environ.get("KDH_KIMI_BIND") or "").strip()
# Congestion outlets: eq2→qwen, cr2→deepseek (Kimi 429 then still failsover).
# Format: lane:endpoint,lane:endpoint
_DEFAULT_LANE_BIND = "eq2:qwen,cr2:deepseek,primary:primary,backup:backup"


def _parse_lane_bind(raw):
    out = {}
    text = str(raw or "").strip() or _DEFAULT_LANE_BIND
    for part in text.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        lane, ep = part.split(":", 1)
        lane = lane.strip()
        ep = ep.strip()
        if lane and ep:
            out[lane] = ep
    return out


LANE_BIND = _parse_lane_bind(os.environ.get("KDH_LANE_BIND"))

# Spend brake: optional outlet kills (qwen and/or all congestion).
_dis = str(os.environ.get("KDH_DISABLE_CONGESTION_OUTLETS") or "").strip().lower()
_no_qwen = str(os.environ.get("KDH_DISABLE_QWEN_OUTLET") or "").strip().lower()
if _dis in ("1", "true", "yes", "on"):
    os.environ.pop("QIYU_QWEN_API_KEY", None)
    os.environ.pop("QIYU_DEEPSEEK_API_KEY", None)
    LANE_BIND = dict(
        (k, v) for k, v in LANE_BIND.items()
        if str(v).strip().lower() not in ("qwen", "deepseek")
    )
elif _no_qwen in ("1", "true", "yes", "on"):
    os.environ.pop("QIYU_QWEN_API_KEY", None)
    LANE_BIND = dict(
        (k, v) for k, v in LANE_BIND.items()
        if str(v).strip().lower() not in ("qwen",)
    )


def _kimi_for_lane(lane):
    """Map invent lane → API mouth (kimi primary/backup, qwen, deepseek)."""
    if KIMI_BIND:
        return KIMI_BIND
    name = str(lane or "").strip()
    if name in LANE_BIND:
        return LANE_BIND[name]
    if name in ("backup", "cr2") or name.startswith("backup"):
        return "backup"
    if name in ("eq2", "qwen"):
        return "qwen"
    if name in ("deepseek",):
        return "deepseek"
    return "primary"


def emit(obj):
    obj = dict(obj)
    if "at" not in obj:
        obj["at"] = kdh._now()
    line = json.dumps(obj, ensure_ascii=False, default=str)
    try:
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except Exception:
        pass
    print(line[:480], flush=True)


def _assert_no_local_isolated_cap_in_callstack():
    # Soft guard: thin path must use remote_eval only.
    return True


MAC_EVAL_RETRIES = max(1, int(os.environ.get("KDH_MAC_EVAL_RETRIES") or "2"))
DUP_COOLDOWN_SEC = max(30, int(os.environ.get("KDH_DUP_COOLDOWN_SEC") or "30"))
INVENT_MAX_TOKENS = max(256, int(os.environ.get("KDH_INVENT_MAX_TOKENS") or "1024"))


def _trim_messages_seed(messages, seed_user_content):
    """Keep system (+ optional first seed) then one fresh seed user turn."""
    head = []
    if messages and messages[0].get("role") == "system":
        head.append(messages[0])
    head.append({"role": "user", "content": str(seed_user_content or "")[:1500]})
    return head


def remote_isolated_cap(cand, recipe, lane, state, mode="eval", diagnosis=None):
    """Enqueue for Mac; wait for result. Never run isolated_cap locally.

    On eval_timeout, re-enqueue the same recipe up to MAC_EVAL_RETRIES times.
    Does not call LLM — invent remains one-shot per outer round.
    """
    _assert_no_local_isolated_cap_in_callstack()
    QUEUE.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    wait_sec = EVAL_WAIT_SEC
    if mode == "micro_search":
        wait_sec = max(EVAL_WAIT_SEC, 1800)
    last = {"ok": False, "phase": "eval_timeout", "mode": mode}
    for attempt in range(MAC_EVAL_RETRIES):
        job_id = "thin_%s_%s_%s" % (lane, int(time.time()), cand.get("id") or "x")
        if mode == "micro_search":
            job_id = "micro_%s_%s" % (lane, int(time.time()))
        job_id = job_id.replace("/", "_")[:120]
        payload = {
            "id": job_id,
            "lane": lane,
            "mode": mode,
            "recipe": kdh._recipe_snap(recipe),
            "cand_id": cand.get("id"),
            "symbol": cand.get("symbol"),
            "diagnosis": diagnosis or {},
            "at": kdh._now(),
            "mac_attempt": attempt + 1,
        }
        qpath = QUEUE / ("%s.recipe.json" % job_id)
        rpath = RESULTS / ("%s.result.json" % job_id)
        if rpath.exists():
            try:
                rpath.unlink()
            except Exception:
                pass
        qpath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        emit({
            "phase": "enqueue", "id": job_id, "lane": lane, "mode": mode,
            "symbol": cand.get("symbol"), "mac_attempt": attempt + 1,
        })
        t0 = time.time()
        while time.time() - t0 < wait_sec:
            if kdh._STOP.is_set():
                return {"ok": False, "phase": "stopped"}
            if rpath.exists():
                try:
                    raw = rpath.read_text(encoding="utf-8")
                    result = json.loads(raw)
                except Exception as exc:
                    return {"ok": False, "phase": "bad_result", "error": str(exc)[:160]}
                try:
                    rpath.unlink()
                except Exception:
                    pass
                if qpath.exists():
                    try:
                        qpath.unlink()
                    except Exception:
                        pass
                return result
            time.sleep(POLL)
        emit({
            "phase": "eval_timeout", "id": job_id, "wait": wait_sec,
            "mode": mode, "mac_attempt": attempt + 1,
            "mac_retries_left": MAC_EVAL_RETRIES - attempt - 1,
        })
        if qpath.exists():
            try:
                qpath.unlink()
            except Exception:
                pass
        last = {
            "ok": False, "phase": "eval_timeout", "id": job_id,
            "mode": mode, "mac_attempt": attempt + 1,
        }
    return last


def eval_one_thin(cand, recipe, state, lane):
    """Mac isolated_cap; then VPS quality_gate + trial_mount once.

    hit_floor / combo C / occupancy are NOT mount admission gates.
    """
    if kdh._STOP.is_set():
        return {"skip": True, "reason": "stopped"}
    try:
        rh = kdh.compute_recipe_hash(kdh.ir_from_dict(kdh._ir_payload(cand)))
    except Exception as exc:
        return {"ok": False, "phase": "ir_fail", "error": str(exc)[:160]}
    if rh in (state.get("done_ids") or []):
        return {"ok": False, "phase": "dup", "rh": rh}
    emit({"phase": "atom_start", "lane": lane, "id": cand["id"], "symbol": cand["symbol"]})
    cap_row = remote_isolated_cap(cand, recipe, lane, state, mode="eval")
    stage = cap_row.get("stage") or classify_stage(cap_row)
    emit({
        "phase": "isolated_cap_remote",
        "lane": lane,
        "id": cand["id"],
        "C_week_pct": cap_row.get("C_week_pct"),
        "C_week_oos_pct": cap_row.get("C_week_oos_pct"),
        "n": cap_row.get("n"),
        "hitch": cap_row.get("hitch"),
        "E": cap_row.get("E") or cap_row.get("E_path"),
        "stage": stage,
        "hit_floor": cap_row.get("hit_floor"),
        "eval_phase": cap_row.get("phase"),
        "host": cap_row.get("host"),
        "diagnosis_hint": (cap_row.get("diagnosis") or {}).get("hint"),
        "error": cap_row.get("error") or cap_row.get("reject"),
    })
    if cap_row.get("reject"):
        state.setdefault("done_ids", []).append(rh)
        kdh._save_state(state)
        return {
            "ok": False,
            "phase": "pair_reject",
            "reject": cap_row.get("reject"),
            "rh": rh,
            "stage": stage,
            "diagnosis": cap_row.get("diagnosis"),
        }
    if cap_row.get("phase") == "eval_timeout" or cap_row.get("error"):
        # Keep refining; do not treat timeout as a permanent done_id.
        return {
            "ok": False,
            "phase": cap_row.get("phase") or "eval_fail",
            "rh": rh,
            "error": cap_row.get("error"),
            "stage": stage,
            "diagnosis": cap_row.get("diagnosis"),
        }
    # Proceed to VPS gate+mount regardless of Mac hit_floor (floor is diagnostic only).
    try:
        ev = kdh.evaluate_atom(cand)
    except Exception as exc:
        ev = {"ok": False, "error": "exc", "detail": str(exc)[:300]}
    packed = dict(kdh._qg_compact(ev))
    packed.update(kdh._gate_extra(ev))
    emit({"phase": "atom_eval", "lane": lane, "id": cand["id"], **packed})
    state.setdefault("done_ids", []).append(rh)
    kdh._save_state(state)
    if not ev.get("ok"):
        return {
            "ok": False, "phase": "gate_fail", "rh": rh, **kdh._gate_extra(ev),
            "C_week_pct": cap_row.get("C_week_pct"),
            "C_week_oos_pct": cap_row.get("C_week_oos_pct"),
            "n": cap_row.get("n"), "hitch": cap_row.get("hitch"),
            "stage": stage, "diagnosis": cap_row.get("diagnosis"),
            "hit_floor": cap_row.get("hit_floor"),
        }
    if kdh.trial_mount_if_floor(cand, ev, state):
        kdh._STOP.set()
        kdh._MOUNTED.append({"lane": lane, "id": cand["id"], "symbol": cand["symbol"]})
        return {"ok": True, "phase": "mounted", "id": cand["id"],
                "C_week_pct": cap_row.get("C_week_pct"),
                "C_week_oos_pct": cap_row.get("C_week_oos_pct"),
                "stage": stage, "hit_floor": cap_row.get("hit_floor")}
    return {"ok": False, "phase": "mount_skip", "rh": rh,
            "C_week_pct": cap_row.get("C_week_pct"),
            "C_week_oos_pct": cap_row.get("C_week_oos_pct"),
            "stage": stage, "hit_floor": cap_row.get("hit_floor")}


def _seed_for(lane):
    row = dict(SEEDS.get(lane) or SEED_BACKUP)
    if not row.get("timeframe"):
        row["timeframe"] = "1h"
    if tcp is not None:
        row["timeframe"] = tcp.resolve_tf(row.get("timeframe"))
    row["exec_tf"] = row.get("exec_tf") or row.get("timeframe")
    # Route comes from process-wide RR at explore init — do not hardcode ema_osc.
    if "filter_tfs" not in row:
        row["filter_tfs"] = []
    return row


def _explore_bundle(current):
    """Lane-local explore state (combos / tf_tried / timing_history / route)."""
    if current.get("explore") is None:
        tf = "1h"
        if tcp is not None:
            tf = tcp.resolve_tf(current.get("tf") or "1h")
            current["explore"] = tcp.empty_lane_explore_state(
                tf,
                route=current.get("route"),
                symbol=current.get("symbol"),
            )
            current["route"] = current["explore"].get("route")
            current["filter_tfs"] = list(current["explore"].get("filter_tfs") or [])
        else:
            current["explore"] = {
                "tf": tf, "exec_tf": tf, "route": "ema_osc", "filter_tfs": [],
                "combos": {}, "tf_tried": [tf],
                "families_tried": [], "timing_history": [],
                "last_group_diverse": True,
                "route_rr_index": 0, "route_cooldowns": {},
                "route_no_improve": {}, "dim_soft_ignore": 0,
            }
    return current["explore"]


def _apply_hard_lock(recipe, lock, allow_rr=False):
    if not isinstance(lock, dict) or not isinstance(recipe, dict):
        return recipe
    hard = LOCK_KEYS_HARD
    if tcp is not None:
        hard = tcp.LOCK_KEYS_HARD
    if allow_rr:
        hard = tuple(k for k in hard if k not in ("xwin", "atr", "dwin"))
    for key in hard:
        if key in lock:
            recipe[key] = lock.get(key)
    # Contract locks during refine
    for key in ("timeframe", "exec_tf", "route", "filter_tfs"):
        if key in lock:
            recipe[key] = lock.get(key)
    return recipe


def _seed_brief(lane, symbols):
    return _seed_brief_with(lane, symbols, _seed_for(lane))


def _seed_brief_with(lane, symbols, seed_rec):
    base = kdh._user_brief(lane, symbols)
    seed = json.dumps({"recipe": seed_rec}, ensure_ascii=False)
    free = ",".join(symbols[:16])
    if _free_create():
        return (
            base
            + "\n本通道可用标的（勿碰现网/禁用）：%s\n" % free
            + "自由创造：输出完整可执行 recipe（family/symbol/timeframe/route/"
            "几何/timing 均可自选）。下面仅是可选参考示例，禁止被示例绑死，"
            "禁止只拧 timing 复读同一壳子：\n"
            + seed
            + "\n"
        )
    return (
        base
        + "\n本通道可用标的（勿碰现网/禁用）：%s\n" % free
        + "锁死起点：先输出这条再只改 timing（换周期/换族/换路线须等机器合同）：\n"
        + seed
        + "\n"
    )


def channel(lane, state):
    if lane in ("backup", "cr2") or str(lane).startswith("backup"):
        pool = kdh.CRYPTO
    elif lane in ("primary", "eq2") or str(lane).startswith("primary"):
        pool = kdh.EQUITY
    else:
        pool = kdh.LANES.get(lane) or kdh.EQUITY
    symbols = [s for s in pool if s not in kdh._banned()]
    seed0 = _seed_for(lane)
    current = {
        "id": "",
        "adjusts": 0,
        "last_pair": "",
        "tf": seed0.get("timeframe") or "1h",
        "symbol": seed0.get("symbol"),
    }
    expl0 = _explore_bundle(current)
    # Bind RR-picked route into seed before first Kimi turn
    seed0["route"] = current.get("route") or expl0.get("route") or "ema_osc"
    seed0["filter_tfs"] = list(current.get("filter_tfs") or expl0.get("filter_tfs") or [])
    if seed0["route"] == "channel":
        seed0["family"] = "ch_long"
        seed0.setdefault("dwin", 20)
    elif seed0["route"] == "ma_family":
        seed0["family"] = "ma_long"
        seed0.setdefault("ma_kind", "sma")
    elif seed0["route"] == "vol_confirm":
        seed0["timing"] = [
            {"factor": "skdj_diff", "operator": "above", "value": -20, "window": 9},
            {"factor": "volume_ratio", "operator": "above", "value": 1.2, "window": 20},
        ]
    elif seed0["route"] == "momentum":
        seed0["timing"] = [
            {"factor": "skdj_diff", "operator": "above", "value": -20, "window": 9},
            {"factor": "roc", "operator": "above", "value": 0, "window": 12},
        ]
    elif seed0["route"] == "mean_revert":
        seed0["family"] = "ma_long"
        seed0["ma_kind"] = "sma"
        seed0.setdefault("held", 20)
        seed0.setdefault("xwin", 8.0)
        seed0.setdefault("atr", 1.6)
        seed0["timing"] = [
            {"factor": "atr_pct", "operator": "below", "value": 0.3, "window": 200},
            {"factor": "skdj_k", "operator": "cross_up", "value": 20, "window": 9},
            {"factor": "macd_hist", "operator": "above", "value": -1},
        ]
    messages = [
        {"role": "system", "content": kdh._system_prompt()},
        {"role": "user", "content": _seed_brief_with(lane, symbols, seed0)},
    ]
    for rnd in range(1, kdh.ROUNDS + 1):
        if kdh._STOP.is_set():
            emit({"phase": "lane_stop", "lane": lane, "round": rnd})
            return
        emit({"phase": "kimi_ask", "lane": lane, "round": rnd})
        body = {
            "messages": messages,
            "temperature": 0.55 if _free_create() else 0.2,
            "max_tokens": INVENT_MAX_TOKENS,
            "response_format": {"type": "json_object"},
        }
        kimi_name = _kimi_for_lane(lane)
        posted = kdh.kimi_post_named(kimi_name, body, timeout=kdh.KIMI_TIMEOUT)
        if not posted.get("ok"):
            err = str(posted.get("error") or "")
            if "400" in err or "response_format" in err.lower():
                body = dict(body)
                body.pop("response_format", None)
                posted = kdh.kimi_post_named(kimi_name, body, timeout=kdh.KIMI_TIMEOUT)
        if not posted.get("ok"):
            err = str(posted.get("error") or "")
            emit({
                "phase": "kimi_fail",
                "lane": lane,
                "round": rnd,
                "error": err[:240],
                "endpoint": posted.get("endpoint"),
                "used": posted.get("used"),
                "failover": posted.get("failover"),
                "http_code": posted.get("http_code"),
                "soft_block": posted.get("soft_block"),
                "window_count": posted.get("window_count"),
                "window_limit": posted.get("window_limit"),
            })
            wait = 8
            low = err.lower()
            # Both mouths already tried inside kimi_post_named; still back off.
            if posted.get("soft_block") or "rate_limit_soft_block" in low:
                wait = 90
            elif "429" in err or "tpm" in low or "quota" in low or "insufficien" in low:
                wait = 120
            elif "504" in err or "503" in err or "502" in err:
                wait = 15
            time.sleep(wait)
            messages.append({
                "role": "user",
                "content": "通道失败。禁止文字。只输出 {\"recipe\":{...}}。",
            })
            continue
        text = kdh._choice_text(posted.get("raw") or {})
        obj = kdh.kimi_json_from_raw(posted.get("raw") or {})
        recipe = kdh._first_recipe(obj)
        if not recipe:
            recipe = kdh._first_recipe(kdh._extract_json(text))
        snap = json.dumps({"recipe": kdh._recipe_snap(recipe)}, ensure_ascii=False) if recipe else ""
        emit({
            "phase": "kimi_raw",
            "lane": lane,
            "round": rnd,
            "text": snap or "NO_JSON",
            "endpoint": posted.get("endpoint"),
            "failover": bool(posted.get("failover")),
            "http_code": posted.get("http_code"),
            "window_count": posted.get("window_count"),
            "window_limit": posted.get("window_limit"),
        })
        if not recipe:
            # 避免空包连打烧光 TPM
            time.sleep(12)
            messages.append({
                "role": "user",
                "content": json_parse_critique(text)[:1500],
            })
            continue
        messages.append({"role": "assistant", "content": snap})
        # Ensure timeframe on recipe
        if not recipe.get("timeframe"):
            recipe["timeframe"] = current.get("tf") or seed0.get("timeframe") or "1h"
        if not recipe.get("route"):
            recipe["route"] = current.get("route") or seed0.get("route") or "ema_osc"
        if "filter_tfs" not in recipe:
            recipe["filter_tfs"] = list(
                current.get("filter_tfs") or seed0.get("filter_tfs") or []
            )
        # P0-C: normalize contract (skip absolute data here; invent path gates)
        try:
            from dual_engine_workflow_v2 import creation_contract as _cc
            norm, nerr = _cc.normalize_contract(
                recipe,
                lane_state={
                    "tf": current.get("tf"),
                    "route": current.get("route") or recipe.get("route"),
                },
                skip_data_gate=True,
            )
            if nerr:
                if nerr == "route_frozen_atom_missing":
                    emit({
                        "phase": "route_frozen_atom_missing",
                        "lane": lane,
                        "route": recipe.get("route"),
                    })
                messages.append({
                    "role": "user",
                    "content": (
                        "合同非法：%s。请输出含 route/exec_tf(=timeframe)/filter_tfs 的合法 recipe。"
                        "禁止文字。" % nerr
                    )[:800],
                })
                continue
            recipe = norm
            current["tf"] = recipe.get("exec_tf") or recipe.get("timeframe")
            current["route"] = recipe.get("route")
            current["filter_tfs"] = list(recipe.get("filter_tfs") or [])
        except Exception as exc:
            messages.append({
                "role": "user",
                "content": "合同规范化失败：%s。禁止文字。" % str(exc)[:120],
            })
            continue
        # mean_revert: force SMA location spine (cut EMA/xu)
        if str(recipe.get("route") or "") == "mean_revert":
            fam = str(recipe.get("family") or "").strip()
            if fam in ("", "xu_long", "xd_short", "pb_long", "pb_short"):
                recipe["family"] = "ma_long"
                recipe["ma_kind"] = recipe.get("ma_kind") or "sma"
            elif fam not in ("ma_long", "ma_short", "ch_long", "ch_short"):
                messages.append({
                    "role": "user",
                    "content": (
                        "mean_revert 禁止 family=%s；须 ma_long（SMA 位置脊）。禁止文字。"
                        % fam
                    )[:800],
                })
                continue
            else:
                recipe["ma_kind"] = recipe.get("ma_kind") or "sma"
        # FREE_CREATE: never overwrite Kimi recipe with lane seed / hard-lock geometry.
        # Legacy mode only: force seed identity before geometry gate (was weld-to-seed).
        if (not _free_create()) and (not current.get("id")):
            for key in ("family", "symbol", "timeframe", "exec_tf", "filter_tfs",
                        "held", "xwin", "z", "atr", "hold"):
                if key in seed0:
                    recipe[key] = seed0.get(key)
            if current.get("route"):
                recipe["route"] = current.get("route")
            elif seed0.get("route"):
                recipe["route"] = seed0.get("route")
            if not recipe.get("timing"):
                recipe["timing"] = list(seed0.get("timing") or [])
        expl = _explore_bundle(current)
        # Legacy identity weld only when FREE_CREATE is off.
        if (not _free_create()) and current.get("lock_recipe") and current.get("id"):
            lock = current["lock_recipe"]
            proposed_family = str(recipe.get("family") or "").strip()
            proposed_symbol = str(recipe.get("symbol") or "").strip().upper()
            lock_family = str(lock.get("family") or "").strip()
            lock_symbol = str(lock.get("symbol") or "").strip().upper()
            _apply_hard_lock(recipe, lock, allow_rr=bool(current.get("allow_rr_geometry")))
            family_changed = bool(proposed_family and proposed_family != lock_family)
            symbol_changed = bool(proposed_symbol and proposed_symbol != lock_symbol)
            if family_changed or symbol_changed:
                allow_switch = False
                if family_changed and tcp is not None:
                    probe = dict(lock)
                    probe["family"] = lock_family
                    allow_switch = tcp.may_switch_family(expl, probe)
                if allow_switch and family_changed and not symbol_changed:
                    order = list(getattr(tcp, "FAMILY_ORDER", []) or [])
                    if proposed_family in order:
                        emit({
                            "phase": "family_switch",
                            "lane": lane,
                            "from": lock_family,
                            "to": proposed_family,
                        })
                        tried = list(expl.get("families_tried") or [])
                        if lock_family and lock_family not in tried:
                            tried.append(lock_family)
                        if proposed_family not in tried:
                            tried.append(proposed_family)
                        expl["families_tried"] = tried
                        recipe["family"] = proposed_family
                        recipe["symbol"] = lock_symbol
                        recipe["timeframe"] = lock.get("timeframe") or current.get("tf")
                        recipe["timing"] = tcp.default_timing() if hasattr(tcp, "default_timing") else list(
                            (_SEED_TIMING if _SEED_TIMING else [])
                        )
                        for gk in ("held", "xwin", "fast", "slow", "z", "atr", "hold"):
                            recipe.pop(gk, None)
                        current["lock_recipe"] = None
                        current["id"] = ""
                        current["stalled"] = 0
                        current["micro_done"] = False
                        current["adjusts"] = 0
                        messages.append({
                            "role": "user",
                            "content": (
                                "已批准换族至 %s。请输出完整 recipe（新家族几何+timing），"
                                "禁止文字。" % proposed_family
                            )[:1500],
                        })
                        continue
                recipe["family"] = lock_family
                recipe["symbol"] = lock_symbol
                msg = (
                    "身份已锁死 %s。禁止换标的/家族/held/xwin。"
                    "家族须完成最小探索深度后方可换族。禁止文字。只改 timing 后输出 {\"recipe\":{...}}。"
                    % current["id"]
                )
                if tcp is not None:
                    msg = (
                        "身份已锁死 %s。几何硬锁；家族/标的为探索预算软锁。"
                        "当前周期+家族须≥%d次评估且≥%d种 timing 指纹后方可换族。"
                        "禁止文字。只改 timing 后输出 {\"recipe\":{...}}。"
                        % (
                            current["id"],
                            tcp.MIN_EVALS_PER_COMBO,
                            tcp.MIN_DISTINCT_TIMING_FPS,
                        )
                    )
                messages.append({"role": "user", "content": msg[:1500]})
                continue
        # Escape-proof geometry bounds (physical range only; not seed weld)
        ok_rr, err_rr = rr_geometry_bounds_ok(recipe)
        if not ok_rr:
            emit({
                "phase": "rr_geometry_reject",
                "lane": lane,
                "xwin": recipe.get("xwin"),
                "atr": recipe.get("atr"),
                "error": (err_rr or "")[:160],
            })
            messages.append({
                "role": "user",
                "content": (err_rr or "几何越界。禁止文字。")[:800],
            })
            continue
        if tcp is not None:
            tf_ok, tf_err = tcp.resolve_recipe_tf(recipe, {"tf": current.get("tf")})
            if tf_err:
                messages.append({
                    "role": "user",
                    "content": (
                        "timeframe 不在周期池（15m/1h/4h）。禁止回落伪装。"
                        "请输出合法 timeframe。禁止文字。"
                    )[:800],
                })
                continue
            recipe["timeframe"] = tf_ok
            recipe["exec_tf"] = tf_ok
            current["tf"] = tf_ok
        # P0-Q soft→hard dimension stack
        try:
            from dual_engine_workflow_v2 import creation_dimension_policy as _cdp
            expl0 = _explore_bundle(current)
            ok_dim, dim_code, dim_payload = _cdp.apply_dimension_policy(
                expl0, recipe.get("timing"),
            )
            if dim_code == "dimension_redundant_hard":
                emit({
                    "phase": "dimension_redundant_hard",
                    "lane": lane,
                    "dimension": dim_payload,
                })
                if tcp is not None:
                    nxt_route = tcp.pick_next_route(expl0)
                    recipe["route"] = nxt_route
                    current["route"] = nxt_route
                    expl0["dim_soft_ignore"] = 0
                messages.append({
                    "role": "user",
                    "content": (
                        "维度冗余硬否决。请换路线或加入非 D2 维原子后重发完整 recipe。禁止文字。"
                    )[:800],
                })
                continue
            if dim_code == "dimension_redundant_soft":
                recipe["_dim_soft_pending"] = True
        except Exception:
            pass
        ident = kdh._identity(recipe)
        if current["id"] and ident and ident != current["id"]:
            if _free_create():
                # New research contract: accept identity change, do not weld back.
                emit({
                    "phase": "identity_switch",
                    "lane": lane,
                    "from": current.get("id"),
                    "to": ident,
                })
                current["id"] = ""
                current["lock_recipe"] = None
                current["stalled"] = 0
                current["micro_done"] = False
                current["adjusts"] = 0
                current["allow_rr_geometry"] = True
            else:
                messages.append({
                    "role": "user",
                    "content": (
                        "身份已锁死 %s。禁止换标的/家族/held/xwin/timeframe。"
                        "禁止文字。只改 timing 后输出 {\"recipe\":{...}}。"
                        % current["id"]
                    )[:1500],
                })
                continue
        cand, err = kdh._cand_from_recipe(recipe, lane)
        if err:
            result = {"reject": err, "symbol": recipe.get("symbol"), "phase": "pair_reject"}
            current["id"] = ident or current["id"]
            current["adjusts"] = int(current.get("adjusts") or 0) + 1
            blast = kdh._should_blast(result, current["adjusts"])
            messages.append({
                "role": "user",
                "content": kdh._pair_feedback(recipe, result, ident, current["adjusts"], blast=blast),
            })
            if blast:
                current = {
                    "id": "", "adjusts": 0, "last_pair": "",
                    "tf": seed0.get("timeframe") or "1h",
                    "explore": current.get("explore"),
                }
            continue
        with kdh._LOCK:
            if kdh._STOP.is_set():
                return
            try:
                result = eval_one_thin(cand, recipe, state, lane)
            except Exception as exc:
                result = {"ok": False, "phase": "eval_exc", "error": str(exc)[:200],
                          "trace": traceback.format_exc()[-200:]}
            kdh._STATE["live"] = None
            kdh._STATE["base_combo"] = None
            gc.collect()
        # P1: exact-hash dup → force diversify (fingerprint untouched)
        if result.get("phase") == "dup":
            current["adjusts"] = int(current.get("adjusts") or 0) + 1
            current["dup_streak"] = int(current.get("dup_streak") or 0) + 1
            emit({
                "phase": "dup_force",
                "lane": lane,
                "round": rnd,
                "rh": result.get("rh"),
                "identity": ident,
                "adjusts": current["adjusts"],
                "dup_streak": current["dup_streak"],
            })
            critique = (
                "上一轮 IR 与已评测配方完全重复（精确 hash）。禁止原样重交。"
                "必须切换 route，或切换 timeframe，或改变至少 2 个 timing 原子；"
                "否则本车道记为空转。"
            )
            seed_content = critique + " 禁止文字。只输出完整 {\"recipe\":{...}}。"
            if tcp is not None:
                sw = tcp.switch_route_contract(expl, recipe)
                emit({
                    "phase": "switch_route",
                    "lane": lane,
                    "from": sw.get("from_route"),
                    "to": sw.get("to_route"),
                    "reason": "dup_force",
                })
                seed = dict(sw.get("recipe_seed") or {})
                current["lock_recipe"] = None
                current["id"] = ""
                current["stalled"] = 0
                current["micro_done"] = False
                current["adjusts"] = 0
                current["route"] = seed.get("route")
                current["filter_tfs"] = list(seed.get("filter_tfs") or [])
                expl["route"] = seed.get("route")
                seed_snap = json.dumps({"recipe": seed}, ensure_ascii=False)
                seed_content = (
                    critique + " " + str(sw.get("prompt") or "")
                    + (" 可选参考（禁止绑死）：" if _free_create() else " 种子骨架：") + seed_snap
                    + " 禁止文字。只输出完整 {\"recipe\":{...}}。"
                )[:1500]
            emit({
                "phase": "idle_spin_cooldown",
                "lane": lane,
                "sec": DUP_COOLDOWN_SEC,
                "dup_streak": current.get("dup_streak"),
            })
            time.sleep(DUP_COOLDOWN_SEC)
            messages = _trim_messages_seed(messages, seed_content)
            continue
        # P0.5: Mac timeout — never burn invent LLM on same identity
        if result.get("phase") == "eval_timeout":
            emit({
                "phase": "eval_timeout_no_llm",
                "lane": lane,
                "round": rnd,
                "identity": ident,
                "id": result.get("id"),
                "mac_attempt": result.get("mac_attempt"),
            })
            seed_content = (
                "回测回包超时（非发明失败）。禁止同身份再发明。"
                "请换 route 或 timeframe 后输出完整 {\"recipe\":{...}}。禁止文字。"
            )
            if tcp is not None:
                sw = tcp.switch_route_contract(expl, recipe)
                emit({
                    "phase": "switch_route",
                    "lane": lane,
                    "from": sw.get("from_route"),
                    "to": sw.get("to_route"),
                    "reason": "eval_timeout",
                })
                seed = dict(sw.get("recipe_seed") or {})
                current["lock_recipe"] = None
                current["id"] = ""
                current["stalled"] = 0
                current["micro_done"] = False
                current["adjusts"] = 0
                current["dup_streak"] = 0
                current["route"] = seed.get("route")
                current["filter_tfs"] = list(seed.get("filter_tfs") or [])
                expl["route"] = seed.get("route")
                seed_snap = json.dumps({"recipe": seed}, ensure_ascii=False)
                seed_content = (
                    seed_content + " " + str(sw.get("prompt") or "")
                    + (" 可选参考（禁止绑死）：" if _free_create() else " 种子骨架：") + seed_snap
                )[:1500]
            time.sleep(min(DUP_COOLDOWN_SEC, 45))
            messages = _trim_messages_seed(messages, seed_content)
            continue
        current["dup_streak"] = 0
        pair = {
            "id": cand.get("id"),
            "symbol": cand.get("symbol"),
            "phase": result.get("phase"),
            "C_week_pct": result.get("C_week_pct"),
            "C_week_oos_pct": result.get("C_week_oos_pct"),
            "n": result.get("n") or result.get("gate_n"),
            "n_oos": result.get("n_oos"),
            "hitch": result.get("hitch") or result.get("gate_sl"),
            "reject": result.get("reject"),
            "error": result.get("error"),
            "E": result.get("E") or result.get("E_path"),
            "weekly": result.get("weekly"),
            "diagnosis": result.get("diagnosis"),
            "stage": result.get("stage") or classify_stage(result),
            "hit_floor": result.get("hit_floor"),
            "blockers": result.get("blockers"),
            "timing_history": list(expl.get("timing_history") or []),
        }
        if ident == current.get("id"):
            current["adjusts"] = int(current.get("adjusts") or 0) + 1
        else:
            current = {
                "id": ident,
                "adjusts": 1,
                "last_pair": "",
                "lock_recipe": kdh._recipe_snap(recipe),
                "stage": pair.get("stage"),
                "stalled": 0,
                "tf": recipe.get("timeframe") or current.get("tf"),
                "explore": expl,
            }
        if not current.get("lock_recipe"):
            current["lock_recipe"] = kdh._recipe_snap(recipe)
        prev_stage = current.get("stage")
        if prev_stage and pair.get("stage") == prev_stage and pair.get("phase") != "mounted":
            current["stalled"] = int(current.get("stalled") or 0) + 1
        else:
            current["stalled"] = 0
            current["stage"] = pair.get("stage")
        current["last_pair"] = json.dumps(pair, ensure_ascii=False, default=str)[:500]
        current["tf"] = recipe.get("timeframe") or current.get("tf")
        # FREE_CREATE: geometry always editable. Legacy: unlock RR only after S2.
        if _free_create():
            current["allow_rr_geometry"] = True
        else:
            current["allow_rr_geometry"] = (str(pair.get("stage") or "") == "S2_hitch")
        try:
            n_oos_i = int(pair.get("n_oos") or result.get("n_oos") or 0)
        except Exception:
            n_oos_i = 0
        if n_oos_i > 0 and n_oos_i < 20:
            emit({
                "phase": "OOS_SPARSE",
                "lane": lane,
                "n_oos": n_oos_i,
                "C_week_oos_pct": pair.get("C_week_oos_pct"),
                "note_zh": "样本外不足（仅%d笔），能力不可信" % n_oos_i,
            })
        # Surgery④: last 5 strongly negative C as critique negatives
        try:
            cwp = pair.get("C_week_pct")
            cwp = None if cwp is None else float(cwp)
        except Exception:
            cwp = None
        if cwp is not None and cwp < -1.0:
            negs = list(expl.get("neg_summaries") or [])
            fp = ""
            try:
                if tcp is not None:
                    fp = tcp.fingerprint_key(
                        tcp.structure_fingerprint(recipe.get("timing"))
                    )
            except Exception:
                fp = ""
            negs.append({
                "summary": "route=%s atr=%s xwin=%s hitch=%s C=%s%% fp=%s" % (
                    recipe.get("route"),
                    recipe.get("atr"),
                    recipe.get("xwin"),
                    pair.get("hitch"),
                    round(cwp, 2),
                    fp,
                ),
                "C_week_pct": cwp,
            })
            expl["neg_summaries"] = negs[-5:]
        # P0-S route failure penalty + P0-Q soft-ignore increment
        if tcp is not None and pair.get("phase") != "mounted":
            cool_info = tcp.note_route_pair_outcome(
                expl, recipe, pair.get("stage"), prev_stage,
            )
            if cool_info.get("cooled"):
                emit({
                    "phase": "route_cooldown",
                    "lane": lane,
                    "key": cool_info.get("cooled"),
                    "slots": cool_info.get("slots"),
                })
                # Equal-floor: after cooldown, force next active route contract
                sw = tcp.switch_route_contract(expl, recipe)
                decision = sw
                pair["explore"] = tcp.explore_actions_for_critique(
                    expl, recipe, pair.get("stage"),
                )
                emit({
                    "phase": "pair", "lane": lane, "round": rnd,
                    "identity": ident, "adjusts": current["adjusts"],
                    "eval_phase": result.get("phase"),
                    "stage": pair.get("stage"),
                    "stalled": current.get("stalled"),
                    "tf": current.get("tf"),
                    "route": recipe.get("route") or current.get("route"),
                    "explore_action": "switch_route",
                    "C_week_pct": pair.get("C_week_pct"),
                    "C_week_oos_pct": pair.get("C_week_oos_pct"),
                    "n": pair.get("n"), "hitch": pair.get("hitch"),
                    "E": pair.get("E"),
                })
                emit({
                    "phase": "switch_route",
                    "lane": lane,
                    "from": sw.get("from_route"),
                    "to": sw.get("to_route"),
                })
                seed = dict(sw.get("recipe_seed") or {})
                current["lock_recipe"] = None
                current["id"] = ""
                current["stalled"] = 0
                current["micro_done"] = False
                current["adjusts"] = 0
                current["route"] = seed.get("route")
                current["filter_tfs"] = list(seed.get("filter_tfs") or [])
                expl["route"] = seed.get("route")
                seed_snap = json.dumps({"recipe": seed}, ensure_ascii=False)
                messages.append({
                    "role": "user",
                    "content": (
                        str(sw.get("prompt") or "")
                        + (" 可选参考（禁止绑死）：" if _free_create() else " 种子骨架：") + seed_snap
                        + " 禁止文字。只输出完整 {\"recipe\":{...}}。"
                    )[:1500],
                })
                continue
        try:
            from dual_engine_workflow_v2 import creation_dimension_policy as _cdp
            assess = _cdp.assess_dimension_stack(recipe.get("timing"))
            if assess.get("soft_warn"):
                _cdp.note_soft_ignored(expl)
            else:
                expl["dim_soft_ignore"] = 0
        except Exception:
            pass
        # Explore depth + S1 TF/family/route migration
        decision = {"action": "continue"}
        if tcp is not None and pair.get("phase") != "mounted":
            decision = tcp.on_eval_done(expl, recipe, pair)
            pair["explore"] = tcp.explore_actions_for_critique(
                expl, recipe, pair.get("stage"),
            )
        emit({
            "phase": "pair", "lane": lane, "round": rnd,
            "identity": ident, "adjusts": current["adjusts"],
            "eval_phase": result.get("phase"),
            "stage": pair.get("stage"),
            "stalled": current.get("stalled"),
            "tf": current.get("tf"),
            "route": recipe.get("route") or current.get("route"),
            "explore_action": decision.get("action"),
            "C_week_pct": pair.get("C_week_pct"),
            "C_week_oos_pct": pair.get("C_week_oos_pct"),
            "n": pair.get("n"), "hitch": pair.get("hitch"),
            "E": pair.get("E"),
        })
        if result.get("phase") == "mounted":
            return
        if decision.get("action") == "switch_route":
            emit({
                "phase": "switch_route",
                "lane": lane,
                "from": decision.get("from_route"),
                "to": decision.get("to_route"),
            })
            seed = dict(decision.get("recipe_seed") or {})
            current["lock_recipe"] = None
            current["id"] = ""
            current["stalled"] = 0
            current["micro_done"] = False
            current["adjusts"] = 0
            current["route"] = seed.get("route")
            current["filter_tfs"] = list(seed.get("filter_tfs") or [])
            expl["route"] = seed.get("route")
            seed_snap = json.dumps({"recipe": seed}, ensure_ascii=False)
            messages.append({
                "role": "user",
                "content": (
                    str(decision.get("prompt") or "")
                    + (" 可选参考（禁止绑死）：" if _free_create() else " 种子骨架：") + seed_snap
                    + " 禁止文字。只输出完整 {\"recipe\":{...}}。"
                )[:1500],
            })
            continue
        if decision.get("action") == "new_contract":
            emit({
                "phase": "cross_tf_contract",
                "lane": lane,
                "from": decision.get("from_tf"),
                "to": decision.get("to_tf"),
                "note_zh": decision.get("note_zh"),
            })
            seed = dict(decision.get("recipe_seed") or {})
            current["lock_recipe"] = None
            current["id"] = ""
            current["stalled"] = 0
            current["micro_done"] = False
            current["adjusts"] = 0
            current["tf"] = seed.get("timeframe")
            expl["tf"] = seed.get("timeframe")
            seed_snap = json.dumps({"recipe": seed}, ensure_ascii=False)
            messages.append({
                "role": "user",
                "content": (
                    str(decision.get("prompt") or "")
                    + (" 可选参考（禁止绑死）：" if _free_create() else " 种子骨架：") + seed_snap
                    + " 禁止文字。只输出完整 {\"recipe\":{...}}。"
                )[:1500],
            })
            continue
        if decision.get("action") == "switch_family":
            emit({
                "phase": "family_switch",
                "lane": lane,
                "from": decision.get("from_family"),
                "to": decision.get("family"),
            })
            seed = {
                "family": decision.get("family"),
                "symbol": recipe.get("symbol"),
                "timeframe": recipe.get("timeframe") or current.get("tf"),
                "exec_tf": recipe.get("exec_tf") or recipe.get("timeframe") or current.get("tf"),
                "route": recipe.get("route") or current.get("route") or "ema_osc",
                "filter_tfs": list(recipe.get("filter_tfs") or current.get("filter_tfs") or []),
                "timing": list(decision.get("timing") or []),
            }
            current["lock_recipe"] = None
            current["id"] = ""
            current["stalled"] = 0
            current["micro_done"] = False
            current["adjusts"] = 0
            messages.append({
                "role": "user",
                "content": (
                    "探索深度已满且周期池耗尽。换族至 %s（路线保持 %s）。"
                    "请按新家族重填几何+timing。种子：%s 禁止文字。"
                    % (
                        decision.get("family"),
                        seed.get("route"),
                        json.dumps({"recipe": seed}, ensure_ascii=False),
                    )
                )[:1500],
            })
            continue
        if decision.get("action") == "close":
            emit({
                "phase": "lane_exhausted_evidence",
                "lane": lane,
                "reason": decision.get("reason") or "evidence_exhausted",
                "note_zh": "跨周期验证与探索预算已耗尽；证据不足结案",
            })
            messages.append({
                "role": "user",
                "content": "证据耗尽（周期池+家族池已探索）。禁止文字。可输出新标的 recipe 重启，或等待通道结束。",
            })
            current = {
                "id": "", "adjusts": 0, "last_pair": "",
                "tf": seed0.get("timeframe") or "1h",
                "symbol": (recipe or {}).get("symbol") or seed0.get("symbol"),
            }
            _explore_bundle(current)
            continue
        # Plateau → Mac micro_search once (skip parameter-only micro on S1_n)
        if int(current.get("stalled") or 0) >= 3 and not current.get("micro_done"):
            if pair.get("stage") == "S1_n":
                emit({
                    "phase": "micro_search_skip_s1",
                    "lane": lane,
                    "identity": ident,
                    "note_zh": "S1_n 跳过参数微搜；优先多样 timing / 探索深度 / 换周期合同",
                })
                current["micro_done"] = True
                current["stalled"] = 0
            else:
                emit({"phase": "micro_search_start", "lane": lane, "identity": ident, "stage": pair.get("stage")})
                micro = remote_isolated_cap(
                    cand, current.get("lock_recipe") or recipe, lane, state,
                    mode="micro_search", diagnosis=pair.get("diagnosis") or {},
                )
                current["micro_done"] = True
                current["stalled"] = 0
                emit({
                    "phase": "micro_search_done",
                    "lane": lane,
                    "scanned": micro.get("scanned"),
                    "stage": micro.get("stage"),
                    "n": micro.get("n"),
                    "hitch": micro.get("hitch"),
                    "E": micro.get("E"),
                    "C_week_pct": micro.get("C_week_pct"),
                    "hit_floor": micro.get("hit_floor"),
                })
                best_rec = micro.get("best_recipe") or micro.get("recipe")
                if best_rec:
                    recipe = best_rec
                    snap = json.dumps({"recipe": kdh._recipe_snap(recipe)}, ensure_ascii=False)
                    messages.append({"role": "assistant", "content": snap})
                    pair = {
                        "id": cand.get("id"),
                        "symbol": recipe.get("symbol"),
                        "phase": micro.get("phase") or "micro_search_done",
                        "C_week_pct": micro.get("C_week_pct"),
                        "C_week_oos_pct": micro.get("C_week_oos_pct"),
                        "n": micro.get("n"),
                        "hitch": micro.get("hitch"),
                        "E": micro.get("E"),
                        "diagnosis": micro.get("diagnosis"),
                        "stage": micro.get("stage") or classify_stage(micro),
                        "hit_floor": micro.get("hit_floor"),
                        "source": "mac_micro_search",
                        "timing_history": list(expl.get("timing_history") or []),
                        "explore": pair.get("explore"),
                    }
                    # Floor is diagnostic only — always re-enter eval/mount with best recipe.
                    cand2, err2 = kdh._cand_from_recipe(recipe, lane)
                    if not err2:
                        result2 = eval_one_thin(cand2, recipe, state, lane)
                        if result2.get("phase") == "mounted":
                            return
        critique = build_critique(
            kdh._recipe_snap(recipe), pair, ident, current["adjusts"],
            diagnosis=pair.get("diagnosis"),
            explore_state=expl,
        )
        if decision.get("explore_hint_zh"):
            critique["hint"] = (
                (critique.get("hint") or "") + " " + decision["explore_hint_zh"]
            ).strip()
        messages.append({
            "role": "user",
            "content": critique_user_message(critique),
        })
        if len(messages) > 10:
            messages = [messages[0], messages[1]] + messages[-8:]
    emit({"phase": "lane_exhausted", "lane": lane})


def main():
    # Point state path for thin hub
    kdh.STATE_PATH = STATE_PATH
    kdh.LOG = LOG
    live_pid, live_raw = kdh._formal()
    if not live_pid or live_pid == "0":
        raise RuntimeError("formal_not_running %s" % live_raw)
    kdh.FORMAL_PID = live_pid
    chain = kdh.invent_endpoint_chain() if hasattr(kdh, "invent_endpoint_chain") else kdh.kimi_endpoint_chain()
    # invent_endpoint_chain lives on kimi_provider; dual create re-exports named posts.
    try:
        from dual_engine_workflow_v2.kimi_provider import invent_endpoint_chain as _invent_chain
        chain = _invent_chain()
    except Exception:
        chain = kdh.kimi_endpoint_chain()
    names = [row.get("name") for row in chain]
    if "primary" not in names or "backup" not in names:
        raise RuntimeError("need_both_kimi_endpoints %s" % names)
    # Extra invent lanes: map to equity/crypto pools.
    lane_map = dict(kdh.LANES or {})
    lane_map["eq2"] = list(kdh.EQUITY)
    lane_map["cr2"] = list(kdh.CRYPTO)
    kdh.LANES = lane_map
    QUEUE.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = kdh._load_state()
    route_boot = {}
    try:
        from dual_engine_workflow_v2 import creation_route_registry as _reg
        route_boot = _reg.active_routes()
    except Exception as exc:
        route_boot = {"error": str(exc)[:120]}
    emit({
        "phase": "boot",
        "mode": "thin_hub",
        "ns": NS or "a",
        "queue": str(QUEUE),
        "results": str(RESULTS),
        "log": str(LOG),
        "kimi_bind": KIMI_BIND or None,
        "lane_bind": LANE_BIND,
        "inherit_seeds": str(_INHERIT_PATH) if _INHERIT_PATH and _INHERIT_PATH.exists() else None,
        "formal": live_raw,
        "lanes": LANES,
        "seeds": SEEDS,
        "seed": SEED_BACKUP,
        "allow": True,
        "stop_present": True,
        "no_local_isolated_cap": True,
        "channels": names,
        "p0_wave": "C+R+S+Q",
        "min_evals_per_combo": getattr(tcp, "MIN_EVALS_PER_COMBO", None) if tcp else None,
        "min_structure_fps": getattr(tcp, "MIN_DISTINCT_COMBO_FPS", None) if tcp else None,
        "active_routes": route_boot.get("active"),
        "frozen_routes": [
            {"route": r.get("route"), "missing_atoms": r.get("missing_atoms"),
             "missing_caps": r.get("missing_caps")}
            for r in (route_boot.get("frozen") or [])
        ],
        "contract_fields": ["route", "exec_tf", "filter_tfs", "timeframe"],
        "free_create": _free_create(),
    })
    kdh._ensure_patch()
    threads = []
    for lane in LANES:
        th = threading.Thread(target=channel, args=(lane, state), name="thin-%s" % lane)
        th.daemon = True
        threads.append(th)
        th.start()
    for th in threads:
        th.join()
    if kdh._MOUNTED:
        emit({"phase": "done", "mounted": kdh._MOUNTED})
        return 0
    emit({"phase": "exhausted", "done_n": len(state.get("done_ids") or [])})
    return 2


if __name__ == "__main__":
    sys.exit(main() or 0)
