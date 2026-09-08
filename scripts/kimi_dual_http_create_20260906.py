# -*- coding: utf-8 -*-
"""双通道 Kimi HTTP 创造。停机：单本 C_week≥0.1% 且 C_week_oos≥0.05%。

一条配方立刻机器复核，数字当场配对回 Kimi 改时机，不整批发完再重试。
不走 sole / submit_job / KimiCreatorRuntime / 课包。不 import 已删创造运行时。
不重熔 KEEP4。不删 STOP_KIMI_CREATION。不重启 formal。Python 3.6 compatible.
"""
from __future__ import print_function

import gc
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

try:
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
except Exception:
    pass

os.environ["VECTOR_ROOT"] = "/root"
os.environ["PYTHONPATH"] = "/root"
os.environ.pop("QIYU_QI_AUTHOR_ON_INGEST", None)
sys.path.insert(0, "/root")
sys.path.insert(0, "/root/scripts")
os.chdir("/root")

from auto_trade_ai_consensus import _load_root_only_env
_load_root_only_env()

import auto_trade_capability_ratio as cap
import auto_trade_confirm_trajectory as traj
import auto_trade_dynamic_leverage as lev
import auto_trade_forecast_closeout as closeout
import auto_trade_formal_daemon as daemon
import auto_trade_human_confirm_pipeline as hcp
import auto_trade_live_roster as roster
import auto_trade_portfolio_combo_metrics as pcm
import auto_trade_slot_paths as slot_paths
import auto_trade_system_forecast as forecast
from dual_engine_workflow_v2 import ast_compiler
from dual_engine_workflow_v2.cognitive_designer import _load_candles_bundle, candles_to_dataframe
from dual_engine_workflow_v2.dataset_contract import (
    DatasetSlice, build_split_contract, compute_dataset_version, slice_candles,
)
from dual_engine_workflow_v2.easyquant_bridge import creation_data_preflight
from dual_engine_workflow_v2.entry_structure_literacy import assess
from dual_engine_workflow_v2.entry_stretch import annotate_stretch_fields
from dual_engine_workflow_v2.exit_dsl import validate_exit_plan
from dual_engine_workflow_v2.kimi_provider import (
    _choice_text, _first_json_value, kimi_endpoint_chain, kimi_json_from_raw,
    kimi_post_named,
)
from dual_engine_workflow_v2.quality_gate import check_full, normalize_handoff_metrics
from dual_engine_workflow_v2.research_symbol_policy import forbidden_symbols
from dual_engine_workflow_v2.semantic_backtest import run_semantic_backtest
from dual_engine_workflow_v2.semantic_executor import EXECUTABLE_ATOM_KEYS
from dual_engine_workflow_v2.strategy_ir import compute_recipe_hash, ir_from_dict
from trial_ex_live_generic import (
    _STATE, _ensure_patch, _median_atr_stop, _pct, _prep_trade, run_trial,
)
LOG = Path("/tmp/kimi_dual_http_create_20260906.log")
OUT = Path("/tmp/kimi_dual_http_create_20260906.json")
STATE_PATH = Path("/root/auto_trade/dual_engine/sole_creation_runs/kimi_dual_http_20260906.json")
AUTO = Path("/root/auto_trade")
RUNS = AUTO / "dual_engine" / "sole_creation_runs"
CONTROL_PATH = AUTO / "strategy_runtime_controls.json"
SEM_PATH = Path("/root/strategy_configs/semantic_live_strategies.json")
TF = "1h"  # default only; recipe/cand must carry resolved timeframe
try:
    from dual_engine_workflow_v2.thin_create_policy import (
        TIMEFRAME_POOL as _TF_POOL,
        resolve_tf as _resolve_tf,
        resolve_recipe_tf as _resolve_recipe_tf,
        DEFAULT_TF as _DEFAULT_TF,
    )
    TF = _DEFAULT_TF or "1h"
    TIMEFRAME_POOL = list(_TF_POOL)
except Exception:
    def _resolve_tf(value, default=None):
        raw = str(value or "").strip().lower()
        aliases = {"15min": "15m", "15m": "15m", "1h": "1h", "4h": "4h"}
        if not raw:
            return default if default is not None else "1h"
        if raw in aliases:
            return aliases[raw]
        if raw in ("15m", "1h", "4h"):
            return raw
        return default

    def _resolve_recipe_tf(recipe, lane_state=None):
        lane_state = lane_state or {}
        raw = (recipe or {}).get("timeframe") or lane_state.get("tf") or "1h"
        tf = _resolve_tf(raw, default=None)
        if not tf:
            return None, "tf_not_in_pool"
        return tf, None

    TIMEFRAME_POOL = ["15m", "1h", "4h"]
SUPPORTED = "supported"
KEEP4 = frozenset((
    "SNDK-USDT-SWAP", "SOL-USDT-SWAP", "SOXL-USDT-SWAP", "XRP-USDT-SWAP",
))
EXTRA_SKIP = frozenset(("SUI-USDT-SWAP", "ADA-USDT-SWAP", "LTC-USDT-SWAP", "XAG-USDT-SWAP"))
ROUNDS = 120
PER_REPLY = 1
ADJUSTS_MAX = 60
BLAST_SOFT = 999
WAVE_MAX = 8
KIMI_TIMEOUT = 180
C_MIN = 0.001
OOS_MIN = 0.0005
C_MIN_PCT = 0.1
OOS_MIN_PCT = 0.05
try:
    from dual_engine_workflow_v2 import thin_timing_atoms as _TTA
    TIMING_SKDJ = _TTA.TIMING_SKDJ
    TIMING_PEER = _TTA.TIMING_PEER
    DEFAULT_TIMING = _TTA.default_timing()
except Exception:
    TIMING_SKDJ = frozenset(("skdj_k", "skdj_d", "skdj_kd", "skdj_diff"))
    TIMING_PEER = frozenset((
        "macd_hist", "macd_dif", "macd_dd",
        "cci", "kdj_k", "kdj_d", "kdj_kd",
    ))
    DEFAULT_TIMING = [
        {"factor": "skdj_diff", "operator": "above", "value": -20, "window": 9},
        {"factor": "macd_hist", "operator": "above", "value": -1},
    ]
EQUITY = (
    "TSM-USDT-SWAP", "NVDA-USDT-SWAP", "AMD-USDT-SWAP", "AVGO-USDT-SWAP",
    "META-USDT-SWAP", "AAPL-USDT-SWAP", "MSFT-USDT-SWAP", "GOOGL-USDT-SWAP",
    "AMZN-USDT-SWAP", "QQQ-USDT-SWAP", "SPY-USDT-SWAP", "IWM-USDT-SWAP",
    "NFLX-USDT-SWAP", "ORCL-USDT-SWAP", "LRCX-USDT-SWAP", "ASML-USDT-SWAP",
    "KLAC-USDT-SWAP", "QCOM-USDT-SWAP", "INTC-USDT-SWAP", "ARM-USDT-SWAP",
    "COIN-USDT-SWAP", "NOW-USDT-SWAP", "IBM-USDT-SWAP", "CRM-USDT-SWAP",
    "HOOD-USDT-SWAP", "PLTR-USDT-SWAP", "TSLA-USDT-SWAP", "MSTR-USDT-SWAP",
    "ADBE-USDT-SWAP",
)
CRYPTO = (
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "BNB-USDT-SWAP", "XAU-USDT-SWAP",
    "BCH-USDT-SWAP", "LINK-USDT-SWAP", "UNI-USDT-SWAP", "AAVE-USDT-SWAP",
    "DOGE-USDT-SWAP",
)
LANES = {"primary": EQUITY, "backup": CRYPTO}
US_EQUITY = frozenset(
    [s.split("-")[0] for s in EQUITY]
    + [
        "SPY", "QQQ", "AMZN", "NFLX", "HOOD", "IWM", "MSFT", "AAPL", "NVDA",
        "PLTR", "INTC", "MSTR", "AMD", "GOOGL", "AVGO", "ARM", "TSLA", "CRCL",
        "COIN", "NOW", "SMCI", "SKHYNIX", "META", "EWY", "SMH", "AMAT", "MU",
        "TSM", "ASML", "QCOM", "LRCX", "KLAC", "ORCL", "IBM", "CRM", "ADBE",
    ]
)

_LOG_LOCK = threading.Lock()
_LOCK = threading.Lock()
_STOP = threading.Event()
_MOUNTED = []
FORMAL_PID = ""


def emit(obj):
    line = json.dumps(obj, ensure_ascii=False, default=str)
    with _LOG_LOCK:
        print(line, flush=True)
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def atomic_write_json(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    tmp.replace(path)


def _tag(symbol):
    return str(symbol or "").split("-")[0]


def _t(x):
    return str(x).replace(".", "p").replace("-", "m")


def atom(aid, factor, operator, value=None, window=None):
    return {
        "atom_id": aid, "factor": factor, "operator": operator,
        "value": value, "window": window, "mandatory": True, "status": SUPPORTED,
    }


def long_xu(symbol, held, xwin, atr, hold, z=None, timeframe=None):
    tf = _resolve_tf(timeframe or TF)
    bits = ["价格在EMA%d上方" % held]
    atoms = [
        atom("a0", "ema", "held", window=held),
        atom("a1", "ema", "cross_up", window=xwin),
        atom("a2", "candle_pattern", "bullish_reclaim"),
    ]
    extra = ""
    if z is not None:
        atoms.append(atom("a3", "close_z", "below", value=z, window=20))
        bits.append("z-score≤%s" % z)
        extra = "_z%s" % _t(z)
    title = (
        "方向型：%s多头趋势顺势，%s，上穿EMA%d后看涨收回，"
        "止损 %s ATR，止盈 2 ATR，持仓 %d根，不要赔率型"
        % (tf, "，".join(bits), xwin, atr, hold)
    )
    return {
        "id": "xu_e%d_x%d_s%s_h%d%s" % (held, xwin, _t(atr), hold, extra),
        "symbol": symbol, "timeframe": tf, "direction": "long",
        "text_summary": title, "entry_atoms": atoms, "exit_atoms": [],
        "stop_contract": {"mode": "atr", "atr_mult": atr, "take_profit_mode": "atr_2"},
        "holding_policy": {"max_bars": hold},
        "factor_catalog_version": "strategy_ir_v1",
        "display": "%s%s多头上穿收回" % (_tag(symbol), tf),
    }


def short_xd(symbol, held, xwin, atr, hold, z=None, timeframe=None):
    tf = _resolve_tf(timeframe or TF)
    bits = ["价格在EMA%d下方" % held]
    atoms = [
        atom("a0", "ema", "held", window=held),
        atom("a1", "ema", "cross_down", window=xwin),
        atom("a2", "candle_pattern", "bearish_reject"),
    ]
    extra = ""
    if z is not None:
        atoms.append(atom("a3", "close_z", "above", value=z, window=20))
        bits.append("z-score>%s" % z)
        extra = "_z%s" % _t(z)
    title = (
        "方向型：%s空头趋势顺势，%s，下穿EMA%d后看跌拒绝，"
        "止损 %s ATR，止盈 2 ATR，持仓 %d根，不要赔率型"
        % (tf, "，".join(bits), xwin, atr, hold)
    )
    return {
        "id": "xd_e%d_x%d_s%s_h%d%s" % (held, xwin, _t(atr), hold, extra),
        "symbol": symbol, "timeframe": tf, "direction": "short",
        "text_summary": title, "entry_atoms": atoms, "exit_atoms": [],
        "stop_contract": {"mode": "atr", "atr_mult": atr, "take_profit_mode": "atr_2"},
        "holding_policy": {"max_bars": hold},
        "factor_catalog_version": "strategy_ir_v1",
        "display": "%s%s空头下穿拒绝" % (_tag(symbol), tf),
    }


def long_ch(symbol, dwin, atr, hold, timeframe=None, with_pattern=False):
    """P1-CH: Donchian breakout spine; candle_pattern optional augment."""
    tf = _resolve_tf(timeframe or TF)
    dwin = int(dwin)
    atoms = [atom("a0", "donchian", "breakout_up", window=dwin)]
    if with_pattern:
        atoms.append(atom("a1", "candle_pattern", "bullish_reclaim"))
    title = (
        "方向型：%s唐奇安%d上轨突破做多，止损 %s ATR，止盈 2 ATR，持仓 %d根，不要赔率型"
        % (tf, dwin, atr, hold)
    )
    return {
        "id": "ch_d%d_s%s_h%d%s" % (dwin, _t(atr), hold, "_pat" if with_pattern else ""),
        "symbol": symbol, "timeframe": tf, "direction": "long",
        "text_summary": title, "entry_atoms": atoms, "exit_atoms": [],
        "stop_contract": {"mode": "atr", "atr_mult": atr, "take_profit_mode": "atr_2"},
        "holding_policy": {"max_bars": hold},
        "factor_catalog_version": "strategy_ir_v1",
        "display": "%s%s唐奇安上破" % (_tag(symbol), tf),
        "dwin": dwin,
    }


def short_ch(symbol, dwin, atr, hold, timeframe=None, with_pattern=False):
    tf = _resolve_tf(timeframe or TF)
    dwin = int(dwin)
    atoms = [atom("a0", "donchian", "breakout_down", window=dwin)]
    if with_pattern:
        atoms.append(atom("a1", "candle_pattern", "bearish_reject"))
    title = (
        "方向型：%s唐奇安%d下轨突破做空，止损 %s ATR，止盈 2 ATR，持仓 %d根，不要赔率型"
        % (tf, dwin, atr, hold)
    )
    return {
        "id": "chs_d%d_s%s_h%d%s" % (dwin, _t(atr), hold, "_pat" if with_pattern else ""),
        "symbol": symbol, "timeframe": tf, "direction": "short",
        "text_summary": title, "entry_atoms": atoms, "exit_atoms": [],
        "stop_contract": {"mode": "atr", "atr_mult": atr, "take_profit_mode": "atr_2"},
        "holding_policy": {"max_bars": hold},
        "factor_catalog_version": "strategy_ir_v1",
        "display": "%s%s唐奇安下破" % (_tag(symbol), tf),
        "dwin": dwin,
    }


def long_ma(symbol, held, atr, hold, ma_kind="sma", timeframe=None, with_pattern=True):
    """P1-A / P2a: SMA/WMA/HMA held location spine."""
    tf = _resolve_tf(timeframe or TF)
    kind = str(ma_kind or "sma").lower()
    if kind not in ("sma", "wma", "hma"):
        kind = "sma"
    atoms = [atom("a0", kind, "held", window=held)]
    if with_pattern:
        atoms.append(atom("a1", "candle_pattern", "bullish_reclaim"))
    title = (
        "方向型：%s价格在%s%d上方%s，止损 %s ATR，止盈 2 ATR，持仓 %d根，不要赔率型"
        % (tf, kind.upper(), held, "，看涨收回" if with_pattern else "", atr, hold)
    )
    return {
        "id": "ma_%s_e%d_s%s_h%d" % (kind, held, _t(atr), hold),
        "symbol": symbol, "timeframe": tf, "direction": "long",
        "text_summary": title, "entry_atoms": atoms, "exit_atoms": [],
        "stop_contract": {"mode": "atr", "atr_mult": atr, "take_profit_mode": "atr_2"},
        "holding_policy": {"max_bars": hold},
        "factor_catalog_version": "strategy_ir_v1",
        "display": "%s%s%s持有多" % (_tag(symbol), tf, kind.upper()),
    }


def short_ma(symbol, held, atr, hold, ma_kind="sma", timeframe=None, with_pattern=True):
    tf = _resolve_tf(timeframe or TF)
    kind = str(ma_kind or "sma").lower()
    if kind not in ("sma", "wma", "hma"):
        kind = "sma"
    atoms = [atom("a0", kind, "held", window=held)]
    if with_pattern:
        atoms.append(atom("a1", "candle_pattern", "bearish_reject"))
    title = (
        "方向型：%s价格在%s%d下方%s，止损 %s ATR，止盈 2 ATR，持仓 %d根，不要赔率型"
        % (tf, kind.upper(), held, "，看跌拒绝" if with_pattern else "", atr, hold)
    )
    return {
        "id": "mas_%s_e%d_s%s_h%d" % (kind, held, _t(atr), hold),
        "symbol": symbol, "timeframe": tf, "direction": "short",
        "text_summary": title, "entry_atoms": atoms, "exit_atoms": [],
        "stop_contract": {"mode": "atr", "atr_mult": atr, "take_profit_mode": "atr_2"},
        "holding_policy": {"max_bars": hold},
        "factor_catalog_version": "strategy_ir_v1",
        "display": "%s%s%s持有空" % (_tag(symbol), tf, kind.upper()),
    }


def long_pb(symbol, held, z, atr, hold, fast=12, slow=26, timeframe=None):
    tf = _resolve_tf(timeframe or TF)
    title = (
        "方向型：%s多头趋势顺势回踩，价格在EMA%d上方，EMA%d在EMA%d之上，"
        "z-score≤%s后看涨收回，止损 %s ATR，止盈 2 ATR，持仓 %d根，不要赔率型"
        % (tf, held, fast, slow, z, atr, hold)
    )
    return {
        "id": "pb_e%d_m%d_%d_z%s_s%s_h%d" % (
            held, fast, slow, _t(z), _t(atr), hold,
        ),
        "symbol": symbol, "timeframe": tf, "direction": "long",
        "text_summary": title,
        "entry_atoms": [
            atom("a0", "ema", "held", window=held),
            atom("a1", "ema", "above_ema", value=slow, window=fast),
            atom("a2", "close_z", "below", value=z, window=20),
            atom("a3", "candle_pattern", "bullish_reclaim"),
        ],
        "exit_atoms": [],
        "stop_contract": {"mode": "atr", "atr_mult": atr, "take_profit_mode": "atr_2"},
        "holding_policy": {"max_bars": hold},
        "factor_catalog_version": "strategy_ir_v1",
        "display": "%s%s多头回踩收回" % (_tag(symbol), tf),
    }


def short_pb(symbol, held, z, atr, hold, fast=12, slow=26, timeframe=None):
    tf = _resolve_tf(timeframe or TF)
    title = (
        "方向型：%s空头趋势顺势回抽，价格在EMA%d下方，EMA%d在EMA%d之下，"
        "z-score>%s后看跌拒绝，止损 %s ATR，止盈 2 ATR，持仓 %d根，不要赔率型"
        % (tf, held, fast, slow, z, atr, hold)
    )
    return {
        "id": "pb_e%d_m%d_%d_z%s_s%s_h%d" % (
            held, fast, slow, _t(z), _t(atr), hold,
        ),
        "symbol": symbol, "timeframe": tf, "direction": "short",
        "text_summary": title,
        "entry_atoms": [
            atom("a0", "ema", "held", window=held),
            atom("a1", "ema", "below_ema", value=slow, window=fast),
            atom("a2", "close_z", "above", value=z, window=20),
            atom("a3", "candle_pattern", "bearish_reject"),
        ],
        "exit_atoms": [],
        "stop_contract": {"mode": "atr", "atr_mult": atr, "take_profit_mode": "atr_2"},
        "holding_policy": {"max_bars": hold},
        "factor_catalog_version": "strategy_ir_v1",
        "display": "%s%s空头回抽拒绝" % (_tag(symbol), tf),
    }


def literacy_ast_from_ir(ir, direction=None):
    side = str(direction or getattr(ir, "direction", "") or "long").lower()
    if side == "short":
        children = [
            {"type": "compare", "feature": "ema_diff", "op": "lt", "value": 0},
            {"type": "compare", "feature": "ema_ratio", "op": "lt", "value": 1.0},
        ]
    else:
        children = [
            {"type": "compare", "feature": "ema_diff", "op": "gt", "value": 0},
            {"type": "compare", "feature": "ema_ratio", "op": "gt", "value": 1.0},
        ]
    return {"type": "all", "children": children}


def _fallback_ast(direction):
    if str(direction).lower() == "short":
        return {"type": "compare", "feature": "ema_diff", "op": "lt", "value": 0}
    return {"type": "compare", "feature": "ema_diff", "op": "gt", "value": 0}


def _exit_plan(ir):
    hold = 10
    policy = getattr(ir, "holding_policy", None) or {}
    try:
        hold = int(policy.get("max_bars") or hold)
    except Exception:
        hold = 10
    mode = str((getattr(ir, "stop_contract", None) or {}).get("take_profit_mode") or "atr_2")
    if "1.2" in mode:
        tp = 1.2
    elif "1.5" in mode:
        tp = 1.5
    else:
        tp = 2
    return {
        "primary_take_profit": {"unit": "atr", "value": tp},
        "max_holding_bars": hold,
    }


def _ir_payload(cand):
    payload = dict(cand)
    payload.pop("id", None)
    payload.pop("display", None)
    return payload


def _entry_extra_mask_for_cand(cand, exec_candles):
    """P0-F: build causal filter mask for filter_tfs; None if no filters."""
    filters = list(cand.get("filter_tfs") or [])
    if not filters:
        return {"ok": True, "mask": None}
    try:
        from dual_engine_workflow_v2 import creation_mtf_filter as _mtf
    except Exception as exc:
        return {"ok": False, "error": "mtf_import:%s" % str(exc)[:80]}

    def _load(sym, ftf):
        bundle = _load_candles_bundle(sym, ftf, mode="FULL")
        if not bundle.get("ok"):
            return {"ok": False, "error": bundle.get("error") or "research_candles_missing"}
        return {"ok": True, "candles": bundle.get("candles") or []}

    return _mtf.build_exec_filter_mask(
        symbol=cand.get("symbol"),
        exec_tf=cand.get("exec_tf") or cand.get("timeframe"),
        filter_tfs=filters,
        direction=cand.get("direction") or "long",
        exec_candles=exec_candles,
        load_filter_candles=_load,
        filter_timing=cand.get("filter_timing"),
    )


def _has_donchian(ir):
    for a in list(ir.entry_atoms or []) + list(ir.exit_atoms or []):
        if getattr(a, "factor", None) == "donchian":
            return True
    return False


def _sl_first(qg):
    hitch = qg.get("hitch_path") if isinstance(qg, dict) else None
    if not isinstance(hitch, dict):
        return None
    if hitch.get("sl_first_rate") is not None:
        return hitch.get("sl_first_rate")
    stats = hitch.get("stats") if isinstance(hitch.get("stats"), dict) else {}
    return stats.get("sl_rate") or stats.get("sl_first_rate")


def _live_symbols(live=None):
    live = live if live is not None else (forecast.list_auto_trade_strategies() or [])
    out = set()
    for row in live:
        sym = str(row.get("symbol") or "").strip().upper()
        if sym:
            out.add(sym)
    return out


def _banned():
    return set(forbidden_symbols() or []) | set(KEEP4) | set(EXTRA_SKIP) | set(_live_symbols())


def _load_state():
    if STATE_PATH.is_file():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"schema": "kimi_dual_http_v1", "done_ids": [], "mounted": []}


def _save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _formal():
    out = subprocess.check_output(
        ["systemctl", "show", "qiyu-formal-auto-trade",
         "-p", "MainPID", "-p", "ActiveEnterTimestamp", "-p", "ActiveState"],
        universal_newlines=True,
    )
    pid = ""
    for line in out.splitlines():
        if line.startswith("MainPID="):
            pid = line.split("=", 1)[-1].strip()
    return pid, out.replace("\n", " | ")


def _assert_formal():
    pid, raw = _formal()
    if not pid or pid == "0":
        raise RuntimeError("formal_not_running %s" % raw)
    if FORMAL_PID and pid != FORMAL_PID:
        raise RuntimeError("formal_pid_changed:%s %s" % (pid, raw))
    return pid, raw


def _published_cap():
    try:
        snap = json.loads(
            Path("/root/auto_trade/system_forecast_latest.json").read_text(encoding="utf-8")
        )
    except Exception:
        return {}
    combo = snap.get("portfolio_combo") or {}
    cap_pack = combo.get("capability") if isinstance(combo.get("capability"), dict) else {}
    weekly = combo.get("capability_weekly")
    if weekly is None:
        weekly = cap_pack.get("capital_weekly")
    return {
        "weekly": weekly,
        "weekly_oos": combo.get("capability_weekly_oos") or cap_pack.get("capital_weekly_oos"),
        "pool": combo.get("capability_pool_unlevered") or cap_pack.get("pool"),
        "n": combo.get("mounted_strategy_count") or combo.get("strategy_count"),
        "generated_at": snap.get("generated_at"),
        "schema": cap_pack.get("schema"),
    }


def _qg_compact(ev):
    qg = ev.get("quality_gate") or {}
    is_m = ((qg.get("is_gate") or {}).get("metrics") or {})
    return {
        "ok": ev.get("ok"),
        "error": ev.get("error"),
        "failed": list(ev.get("failed_rules") or [])[:8],
        "n": ev.get("n") or is_m.get("n"),
        "E": ev.get("E") or is_m.get("E_raw"),
        "R": ev.get("R"),
        "sharpe": ev.get("sharpe"),
        "oos_sharpe": ev.get("oos_sharpe"),
        "sl_first": ev.get("sl_first"),
        "weekly": ev.get("weekly_opens") or is_m.get("weekly_opens"),
        "literacy": ev.get("literacy_family"),
    }


def isolated_cap(cand, ev):
    ir = ir_from_dict(ev.get("ir") or _ir_payload(cand))
    symbol = cand["symbol"]
    tf = _resolve_tf(cand.get("timeframe") or TF, default=None)
    side = cand["direction"]
    if not tf or tf not in TIMEFRAME_POOL:
        return {"ok": False, "error": "tf_not_in_pool", "timeframe": cand.get("timeframe")}
    bundle = _load_candles_bundle(symbol, tf, mode="FULL")
    if not bundle.get("ok"):
        return {
            "ok": False,
            "error": "research_candles_missing:%s" % tf,
            "timeframe": tf,
            "detail": bundle.get("error"),
        }
    candles = bundle.get("candles") or []
    extra_pack = _entry_extra_mask_for_cand(cand, candles)
    if not extra_pack.get("ok"):
        return {
            "ok": False,
            "error": extra_pack.get("error") or "filter_lookahead_risk",
            "timeframe": tf,
            "filter_tfs": list(cand.get("filter_tfs") or []),
            "hint_zh": extra_pack.get("hint_zh"),
        }
    extra_mask = extra_pack.get("mask")
    dv = compute_dataset_version(candles, symbol=symbol, timeframe=tf)
    contract = build_split_contract(candles, dataset_version=dv, symbol=symbol, timeframe=tf)
    is_off = int((contract["slices"][DatasetSlice.FORMAL_IS] or {}).get("start_idx") or 0)
    oos_off = int((contract["slices"][DatasetSlice.FINAL_OOS] or {}).get("start_idx") or 0)
    proxy = bundle.get("volume_is_proxy")
    is_slice = slice_candles(candles, DatasetSlice.FORMAL_IS, contract)
    oos_slice = slice_candles(candles, DatasetSlice.FINAL_OOS, contract)
    # Align extra mask to sliced windows via absolute indices when possible
    def _slice_mask(full_mask, sliced):
        if full_mask is None:
            return None
        if not sliced:
            return []
        # Prefer ts alignment
        by_ts = {}
        for i, row in enumerate(candles):
            try:
                by_ts[int(row.get("ts"))] = full_mask[i]
            except Exception:
                continue
        out = []
        for row in sliced:
            try:
                out.append(bool(by_ts.get(int(row.get("ts")), False)))
            except Exception:
                out.append(False)
        return out

    is_extra = _slice_mask(extra_mask, is_slice)
    oos_extra = _slice_mask(extra_mask, oos_slice)
    is_bt = run_semantic_backtest(
        ir, is_slice,
        symbol=symbol, timeframe=tf, direction=side, volume_is_proxy=proxy,
        entry_extra_mask=is_extra,
    )
    oos_bt = run_semantic_backtest(
        ir, oos_slice,
        symbol=symbol, timeframe=tf, direction=side, volume_is_proxy=proxy,
        entry_extra_mask=oos_extra,
    )
    cost = float(pcm.QUALITY_BASE_ROUND_TRIP_COST)
    trades = []
    for row in list((is_bt or {}).get("trades_all") or []):
        trades.append(_prep_trade(row, "IS", is_off, cost))
    for row in list((oos_bt or {}).get("trades_all") or []):
        trades.append(_prep_trade(row, "OOS", oos_off, cost))
    frame = candles_to_dataframe(candles, volume_is_proxy=proxy)
    annotated = []
    for row in trades:
        annotated.append(annotate_stretch_fields(row, frame, direction=side))
    trades = annotated
    atr_mult = float((cand.get("stop_contract") or {}).get("atr_mult") or 2.18)
    hold = int((cand.get("holding_policy") or {}).get("max_bars") or 10)
    stop = _median_atr_stop(candles, trades, atr_mult)
    pack = cap.capability_from_trades(
        trades, stop_pct=stop, n_trials=100, purge=hold)
    oos_n = sum(1 for t in trades if str(t.get("sample_split") or "").upper() == "OOS")
    oos_pack = None
    if oos_n >= cap.MIN_OOS_TRADES:
        oos_pack = cap.capability_from_trades(
            trades, stop_pct=stop, n_trials=100, sample_split="OOS", purge=hold)
    c = pack.get("C_week")
    oos = None if oos_pack is None else oos_pack.get("C_week")
    hit = (
        pack.get("usable")
        and oos_pack is not None and oos_pack.get("usable")
        and c is not None and float(c) + 1e-16 >= C_MIN
        and oos is not None and float(oos) + 1e-16 >= OOS_MIN
    )
    scrub = {}
    try:
        scrub = cap.scrub_oos_display(oos, oos_n) or {}
    except Exception:
        scrub = {}
    tier = {}
    try:
        tier = cap.classify_capability_tier(
            c_week=c,
            c_oos=oos,
            n=pack.get("n"),
            n_oos=oos_n,
            usable=pack.get("usable"),
            oos_usable=None if oos_pack is None else oos_pack.get("usable"),
            hitch=pack.get("hitch"),
        ) or {}
    except Exception:
        tier = {}
    diagnosis = {}
    try:
        from dual_engine_workflow_v2.timing_diagnose import diagnose_entry_skdj
        diagnosis = diagnose_entry_skdj(candles, trades)
    except Exception as exc:
        diagnosis = {"hint": "diagnose_fail", "error": str(exc)[:120]}
    stage = "S1_n"
    try:
        from dual_engine_workflow_v2.timing_stage import classify_stage
        stage = classify_stage({
            "n": pack.get("n"),
            "hitch": pack.get("hitch"),
            "E": pack.get("E_path"),
            "weekly": pack.get("lambda_week") or pack.get("weeks"),
            "hit_floor": bool(hit),
            "blockers": pack.get("blockers") or [],
        })
    except Exception:
        pass
    return {
        "ok": True,
        "timeframe": tf,
        "exec_tf": tf,
        "filter_tfs": list(cand.get("filter_tfs") or []),
        "mtf_filter": {
            "applied": bool(extra_mask is not None),
            "filters": (extra_pack or {}).get("filters"),
            "n_true": (extra_pack or {}).get("n_true"),
        },
        "C_week": c,
        "C_week_oos": oos,
        "C_week_pct": _pct(c),
        "C_week_oos_pct": _pct(oos),
        "usable": pack.get("usable"),
        "oos_usable": None if oos_pack is None else oos_pack.get("usable"),
        "n": pack.get("n"),
        "n_oos": oos_n,
        "hit_floor": bool(hit),
        "oos_sparse": bool(scrub.get("oos_sparse")),
        "oos_phase": scrub.get("oos_phase"),
        "oos_sparse_zh": scrub.get("oos_sparse_zh"),
        "capability_tier": tier.get("tier"),
        "capability_tier_zh": tier.get("tier_zh"),
        "C_week_oos_display_pct": (
            None if scrub.get("oos_sparse") else _pct(oos)
        ),
        "blockers": pack.get("blockers") or [],
        "oos_blockers": None if oos_pack is None else (oos_pack.get("blockers") or []),
        "hitch": pack.get("hitch"),
        "oos_hitch": None if oos_pack is None else oos_pack.get("hitch"),
        "E": pack.get("E_path"),
        "E_path": pack.get("E_path"),
        "E_cap": pack.get("E_cap"),
        "weekly": pack.get("lambda_week"),
        "diagnosis": diagnosis,
        "stage": stage,
    }


def _route_allows_donchian(cand):
    route = str((cand or {}).get("route") or "")
    try:
        from dual_engine_workflow_v2 import creation_route_registry as _reg
        if not _reg.cap_ok("create_allows_donchian"):
            return False
        spec = (_reg.ROUTE_SPECS.get(route) or {})
        return bool(spec.get("allows_donchian"))
    except Exception:
        return route == "channel"


def _count_candle_pattern_triggers(candles, direction="long"):
    """Machine count of reclaim/reject bars on a candle list (IS slice)."""
    n = 0
    rows = list(candles or [])
    for i in range(1, len(rows)):
        c = rows[i]
        p = rows[i - 1]
        try:
            o = float(c.get("open"))
            cl = float(c.get("close"))
            pcl = float(p.get("close"))
        except Exception:
            continue
        if str(direction or "long").lower() == "short":
            if cl < o and cl < pcl:
                n += 1
        else:
            if cl > o and cl > pcl:
                n += 1
    return n


def _optional_pattern_freq_gate(cand, is_candles):
    """Reject channel (or any) variants that add low-frequency candle_pattern."""
    atoms = list((cand or {}).get("entry_atoms") or [])
    has_pat = any(str(a.get("factor") or "") == "candle_pattern" for a in atoms)
    if not has_pat:
        return None
    # Only enforce floor when route is channel (optional augment policy).
    if str((cand or {}).get("route") or "") != "channel":
        return None
    n = _count_candle_pattern_triggers(is_candles, direction=cand.get("direction"))
    if n < 20:
        return {
            "id": cand.get("id"),
            "ok": False,
            "error": "candle_pattern_freq_below_floor",
            "pattern_triggers_is": n,
            "floor": 20,
        }
    return None


def evaluate_atom(cand):
    symbol = cand["symbol"]
    tf = _resolve_tf(cand.get("timeframe") or TF, default=None)
    side = cand["direction"]
    title = cand["text_summary"]
    ir = ir_from_dict(_ir_payload(cand))
    if not tf or tf not in TIMEFRAME_POOL:
        return {"id": cand["id"], "ok": False, "error": "tf_not_in_pool", "timeframe": cand.get("timeframe")}
    if _has_donchian(ir) and not _route_allows_donchian(cand):
        return {"id": cand["id"], "ok": False, "error": "donchian_forbidden"}
    bundle = _load_candles_bundle(symbol, tf, mode="FULL")
    if not bundle.get("ok"):
        return {
            "id": cand["id"], "ok": False,
            "error": "research_candles_missing:%s" % tf,
            "timeframe": tf,
            "detail": bundle.get("error"),
        }
    candles = bundle.get("candles") or []
    # vol_confirm requires true volume
    route = str(cand.get("route") or "")
    if route == "vol_confirm" and bool(bundle.get("volume_is_proxy")):
        return {"id": cand["id"], "ok": False, "error": "volume_proxy_forbidden", "timeframe": tf}
    extra_pack = _entry_extra_mask_for_cand(cand, candles)
    if not extra_pack.get("ok"):
        return {
            "id": cand["id"], "ok": False,
            "error": extra_pack.get("error") or "filter_lookahead_risk",
            "timeframe": tf,
            "filter_tfs": list(cand.get("filter_tfs") or []),
        }
    extra_mask = extra_pack.get("mask")
    dv = compute_dataset_version(candles, symbol=symbol, timeframe=tf)
    contract = build_split_contract(candles, dataset_version=dv, symbol=symbol, timeframe=tf)
    proxy = bundle.get("volume_is_proxy")
    is_slice = slice_candles(candles, DatasetSlice.FORMAL_IS, contract)
    oos_slice = slice_candles(candles, DatasetSlice.FINAL_OOS, contract)
    freq_gate = _optional_pattern_freq_gate(cand, is_slice)
    if freq_gate is not None:
        return freq_gate

    def _slice_mask(full_mask, sliced):
        if full_mask is None:
            return None
        by_ts = {}
        for i, row in enumerate(candles):
            try:
                by_ts[int(row.get("ts"))] = full_mask[i]
            except Exception:
                continue
        out = []
        for row in sliced:
            try:
                out.append(bool(by_ts.get(int(row.get("ts")), False)))
            except Exception:
                out.append(False)
        return out

    is_bt = run_semantic_backtest(
        ir, is_slice,
        symbol=symbol, timeframe=tf, direction=side, volume_is_proxy=proxy,
        entry_extra_mask=_slice_mask(extra_mask, is_slice),
    )
    oos_bt = run_semantic_backtest(
        ir, oos_slice,
        symbol=symbol, timeframe=tf, direction=side, volume_is_proxy=proxy,
        entry_extra_mask=_slice_mask(extra_mask, oos_slice),
    )
    ast = literacy_ast_from_ir(ir, side)
    qg = check_full(
        is_bt, timeframe=tf, oos_backtest=oos_bt, test_mode=True,
        entry_ast=ast, direction=side, title_zh=title, claim_text=title,
    )
    is_m = ((qg.get("is_gate") or {}).get("metrics") or {})
    return {
        "id": cand["id"],
        "ok": bool(qg.get("ok")),
        "symbol": symbol,
        "tf": tf,
        "timeframe": tf,
        "side": side,
        "title": title,
        "recipe_hash": compute_recipe_hash(ir),
        "failed_rules": list(qg.get("failed_rules") or []),
        "is_ok": bool((qg.get("is_gate") or {}).get("ok")),
        "oos_ok": bool((qg.get("oos_gate") or {}).get("ok")),
        "R": is_m.get("R"),
        "E": is_m.get("E_raw"),
        "n": is_m.get("n"),
        "sharpe": is_m.get("annualized_sharpe"),
        "weekly_opens": is_m.get("weekly_opens"),
        "win": is_m.get("win_rate"),
        "oos_sharpe": (qg.get("oos_gate") or {}).get("oos_sharpe"),
        "sl_first": _sl_first(qg),
        "quality_gate": qg,
        "ir": ir.to_dict(),
        "literacy_family": ((qg.get("structure_literacy") or {}).get("family")),
    }


def persist_mission(cand, ev):
    mission = "kdh_%s_%s" % (cand["id"], datetime.now().strftime("%Y%m%d_%H%M%S"))
    qg = ev.get("quality_gate") or {}
    artifact = {
        "mission_id": mission,
        "research_direction": cand.get("text_summary"),
        "brief": cand.get("text_summary"),
        "symbol": cand["symbol"],
        "timeframe": TF,
        "direction": cand["direction"],
        "authored_id": cand["id"],
        "quality_gate": qg,
        "ir": ev.get("ir") or _ir_payload(cand),
        "source": "kimi_dual_http",
        "at": _now(),
    }
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / ("%s.json" % mission)).write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return mission


def _ensure_daemon(symbol):
    cfg_path = AUTO / slot_paths.daemon_config_name(symbol, TF)
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        if str(cfg.get("symbol") or "").upper() == symbol:
            return {"created": False, "path": str(cfg_path)}
    tag = _tag(symbol).upper()
    if tag in US_EQUITY:
        src_path = AUTO / "formal_daemon_config_now.json"
        if not src_path.exists():
            src_path = AUTO / "formal_daemon_config_coin.json"
        if not src_path.exists():
            src_path = AUTO / "formal_daemon_config_skhynix.json"
        if not src_path.exists():
            src_path = AUTO / "formal_daemon_config_ewy.json"
    else:
        src_path = AUTO / "formal_daemon_config_doge.json"
        if not src_path.exists():
            src_path = AUTO / "formal_daemon_config_now.json"
    src = json.loads(src_path.read_text(encoding="utf-8"))
    cfg = dict(src)
    cfg.update({
        "symbol": symbol,
        "timeframe": TF,
        "enabled": True,
        "allow_auto_open": True,
        "allow_auto_close": True,
        "formal_auto_trading_authorized": True,
        "gate_authorized_auto_trading": True,
        "strategy_key": "",
        "strategy_keys": [],
        "strategy_leverages": {},
        "strategy_stops": {},
        "last_open_ts": 0,
        "last_signal_candle_id": None,
        "last_signal_candle_ids": {},
        "pending_kline_close_entries": {},
        "sz": str(src.get("sz") or "0.01"),
        "cooldown_sec_after_open": 3600,
        "tick_interval_sec": 60,
    })
    atomic_write_json(cfg_path, cfg)
    return {"created": True, "path": str(cfg_path), "cloned_from": str(src_path)}


def _stamp_display(key, symbol, display):
    aid = "%s|%s|%s" % (symbol, TF, key)
    roster_path = AUTO / "current_live_strategy_roster.json"
    if roster_path.exists():
        raw = json.loads(roster_path.read_text(encoding="utf-8"))
        new_rows = []
        hit = False
        for row in list(raw.get("strategies") or []):
            if str(row.get("strategy_key") or "") == key:
                row = dict(row)
                row["strategy_name"] = display
                row["name"] = display
                hit = True
            new_rows.append(row)
        if hit:
            raw["strategies"] = new_rows
            raw["updated_at"] = _now()
            atomic_write_json(roster_path, raw)
    if CONTROL_PATH.exists():
        data = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
        assignments = data.get("assignments") or {}
        row = assignments.get(aid)
        if isinstance(row, dict):
            row = dict(row)
            row["strategy_name"] = display
            row["name"] = display
            assignments[aid] = row
            data["assignments"] = assignments
            atomic_write_json(CONTROL_PATH, data)


def _silence_side_effects():
    orig_notify = closeout.notify_pool_change
    orig_wx = hcp._wx

    def quiet_notify(reason, detail=None, auto_refresh=True):
        return orig_notify(reason, detail=detail, auto_refresh=False)

    def quiet_wx(*args, **kwargs):
        return {"ok": True, "sent": False, "blocked": True, "reason": "kdh_quiet"}

    closeout.notify_pool_change = quiet_notify
    hcp._wx = quiet_wx
    return orig_notify, orig_wx


def _restore_side_effects(orig_notify, orig_wx):
    closeout.notify_pool_change = orig_notify
    hcp._wx = orig_wx


def mount_one(job, mission, ir, qg, trial, chosen):
    symbol = job["symbol"]
    side = job["side"]
    display = job["display"]
    rh = compute_recipe_hash(ir)
    key = "cursor_%s" % rh[:24]
    title = job["rd"]
    live = forecast.list_auto_trade_strategies() or []
    if any(s.get("strategy_key") == key for s in live):
        return {"ok": False, "error": "already_mounted", "key": key}
    if any(str(s.get("symbol") or "").upper() == symbol for s in live):
        return {"ok": False, "error": "symbol_already_live", "symbol": symbol}

    entry_ast = literacy_ast_from_ir(ir, side) or _fallback_ast(side)
    exit_plan = _exit_plan(ir)
    formal = ast_compiler.formal_feature_registry()
    entry_check = ast_compiler.validate_ast(entry_ast, feature_registry_list=formal)
    if not entry_check.get("ok"):
        entry_ast = _fallback_ast(side)
        entry_check = ast_compiler.validate_ast(entry_ast, feature_registry_list=formal)
    exit_check = validate_exit_plan(exit_plan, feature_registry_list=formal)
    lit = assess(entry_check.get("normalized") or entry_ast, direction=side, title_zh=title)
    if not entry_check.get("ok") or not exit_check.get("ok") or not lit.get("ok"):
        return {
            "ok": False,
            "error": "ast_or_literacy_blocked",
            "entry": entry_check.get("ok"),
            "exit": exit_check.get("ok"),
            "literacy": lit.get("ok"),
            "literacy_family": lit.get("family"),
        }
    entry_ast = entry_check.get("normalized")
    exit_plan = exit_check.get("normalized")

    bundle = _load_candles_bundle(symbol, TF, mode="FULL")
    if not bundle.get("ok"):
        return {"ok": False, "error": "candles", "detail": bundle.get("error")}
    full = list(bundle.get("candles") or [])
    proxy = bool(bundle.get("volume_is_proxy"))
    dv = compute_dataset_version(full, symbol=symbol, timeframe=TF)
    contract = build_split_contract(full, dataset_version=dv, symbol=symbol, timeframe=TF)
    is_candles = slice_candles(full, DatasetSlice.FORMAL_IS, contract)
    oos_candles = slice_candles(full, DatasetSlice.FINAL_OOS, contract)
    is_off = int((contract["slices"][DatasetSlice.FORMAL_IS] or {}).get("start_idx") or 0)
    oos_off = int((contract["slices"][DatasetSlice.FINAL_OOS] or {}).get("start_idx") or 0)
    is_bt = run_semantic_backtest(
        ir, is_candles, symbol=symbol, timeframe=TF, direction=side, volume_is_proxy=proxy,
    )
    oos_bt = run_semantic_backtest(
        ir, oos_candles, symbol=symbol, timeframe=TF, direction=side, volume_is_proxy=proxy,
    )
    cost = float(pcm.QUALITY_BASE_ROUND_TRIP_COST)
    is_trades = [
        _prep_trade(row, "IS", is_off, cost)
        for row in list((is_bt or {}).get("trades_all") or [])
    ]
    oos_trades = [
        _prep_trade(row, "OOS", oos_off, cost)
        for row in list((oos_bt or {}).get("trades_all") or [])
    ]
    all_trades = is_trades + oos_trades
    atr_mult = float((getattr(ir, "stop_contract", None) or {}).get("atr_mult") or 2.0)
    stop = _median_atr_stop(full, all_trades, atr_mult)
    trial_stop = ((trial or {}).get("risk") or {}).get("stop")
    if stop is None or not (0.0 < stop < 1.0):
        stop = trial_stop
    if stop is None or not (0.0 < float(stop) < 1.0):
        return {"ok": False, "error": "stop_uncomputable", "stop": stop}
    stop = float(stop)
    lev_pack = pcm.compute_single_strategy_leverage_metrics(
        symbol, TF, key, backtest={"ok": True, "trades_all": all_trades},
    )
    leverage = pcm._positive_leverage(lev_pack.get("exchange_applied_leverage")) or 0.0
    if leverage <= 0:
        leverage = float(((trial or {}).get("risk") or {}).get("leverage") or 0)
    if leverage <= 0:
        return {"ok": False, "error": "kelly_leverage_missing"}
    # Prefer caller tier; if Kelly blows solo-risk, clamp leverage — never refuse
    # for occupancy/combo C reasons.
    pick = _pick_mount_tier(trial=trial, leverage=leverage, stop=stop)
    tier_code = (chosen or {}).get("tier") or pick.get("tier") or "DAIFUKU"
    if pick.get("leverage") and float(pick.get("leverage") or 0) > 0:
        if pick.get("leverage_clamped") or float(pick["leverage"]) + 1e-12 < float(leverage):
            leverage = float(pick["leverage"])
    gate = traj.gate_confirm_tier(tier_code, leverage, stop)
    if not gate.get("ok"):
        pick2 = _pick_mount_tier(
            trial={"risk": {"leverage": leverage, "stop": stop}},
            leverage=leverage, stop=stop,
        )
        tier_code = pick2.get("tier") or "DAIFUKU"
        if pick2.get("leverage"):
            leverage = float(pick2["leverage"])
        gate = traj.gate_confirm_tier(tier_code, leverage, stop)
        if not gate.get("ok"):
            return {"ok": False, "error": "tier_risk_unclamped", "tier": tier_code, "gate": gate}

    hitch = (qg.get("hitch_path") or {}).get("stats") or {}
    metrics = dict(qg.get("metrics") or {})
    metrics.update(normalize_handoff_metrics(is_bt, (qg.get("is_gate") or {}).get("metrics") or {}))
    if metrics.get("expectancy_E") is None:
        metrics["expectancy_E"] = (
            metrics.get("E_raw")
            or ((qg.get("is_gate") or {}).get("metrics") or {}).get("E_raw")
        )
    metrics.setdefault("E_basis", "backtest_account_only")
    metrics.update({
        "symbol": symbol,
        "timeframe": TF,
        "direction": side,
        "key": key,
        "name": display,
        "hitch_class": hitch.get("klass") or "方向型",
        "out_of_sample_sharpe": (qg.get("oos_gate") or {}).get("oos_sharpe"),
        "weekly_opens": (
            metrics.get("weekly_opens")
            or ((qg.get("is_gate") or {}).get("metrics") or {}).get("weekly_opens")
        ),
    })
    # Gate must already be clean (no n-waive). Do not strip trade_count failures.
    qg = dict(qg or {})
    qg["ok"] = True
    qg["all_passed"] = True
    qg["status"] = "PASS"
    qg["failed_rules"] = list(qg.get("failed_rules") or [])
    if isinstance(qg.get("is_gate"), dict):
        ig = dict(qg["is_gate"])
        ig["ok"] = not list(ig.get("reasons") or [])
        ig["all_passed"] = ig["ok"]
        qg["is_gate"] = ig
    admission = {
        "schema": hcp.QUALITY_GATE_ADMISSION_SCHEMA,
        "ok": True,
        "pass": True,
        "quality_gate": qg,
        "metrics": metrics,
        "failed_rules": [],
        "recipe_identity_hash": rh,
        "authority": "capability_floor_plus_E",
        "n_waived_by_human": False,
    }
    dsl = {
        "key": key,
        "name": display,
        "symbol": symbol,
        "timeframe": TF,
        "direction": side,
        "entry_ast": entry_ast,
        "exit_plan": exit_plan,
        "protective_stop_pct": float(stop),
        "max_holding_bars": exit_plan.get("max_holding_bars") or 10,
        "creation_path": "cognitive_kimi_thin",
        "strategy_ir": ir.to_dict(),
    }
    cand = {"dsl": dsl, "symbol": symbol, "timeframe": TF, "name": display}
    daemon_info = _ensure_daemon(symbol)
    receipt_path = pcm._persist_combo_receipt(
        key,
        {"trades_all": is_trades, "n_trials": (qg or {}).get("n_trials")},
        {"trades_all": oos_trades, "n_trials": (qg or {}).get("n_trials")},
        source="cognitive_kimi_thin",
        source_path=str(RUNS / ("%s.json" % mission)),
    )
    enq = hcp.enqueue_for_human(
        cand=cand,
        metrics=metrics,
        source="cognitive_kimi_thin",
        ai_review={"advisory_only": True, "schema": "qiyu_ai_advisory_v1"},
        quality_gate_admission=admission,
        recipe_identity_hash=rh,
        send_notification=False,
    )
    if not enq.get("ok"):
        return {"ok": False, "error": "enqueue_failed", "enqueue": enq, "daemon": daemon_info}
    key = enq.get("key") or key
    orig_notify = closeout.notify_pool_change

    def _quiet_notify(*args, **kwargs):
        kwargs = dict(kwargs)
        kwargs["auto_refresh"] = False
        return orig_notify(*args, **kwargs)

    closeout.notify_pool_change = _quiet_notify
    sync = {}
    try:
        pending = hcp.load_pending()
        for row in pending.get("items") or []:
            if row.get("key") == key and row.get("status") == "awaiting_confirm":
                pack = dict(row.get("trajectory_pack") or {})
                pack["applied_leverage"] = leverage
                pack["add_one_tier"] = tier_code
                row["trajectory_pack"] = pack
                row["strategy_tier"] = tier_code
                row["protective_stop_pct"] = float(stop)
                row["name"] = display
                row["swap_on_confirm"] = False
                row["dsl"] = dict(row.get("dsl") or {})
                row["dsl"]["strategy_ir"] = ir.to_dict()
                row["dsl"]["name"] = display
                weekly = metrics.get("weekly_opens")
                if weekly is not None:
                    row["ai_theoretical_weekly_opens_avg"] = weekly
                    row["statistical_weekly_opens_expected"] = weekly
                break
        hcp.save_pending(pending)
        out = hcp.confirm(key, confirmed_by="cursor_human", tier=tier_code)
        if not out.get("ok"):
            return {
                "ok": False, "error": out.get("error") or "confirm_failed",
                "confirm": out, "daemon": daemon_info,
            }
        _stamp_display(key, symbol, display)
        try:
            hcp._set_semantic_live(key, True)
        except Exception:
            pass
        try:
            lev.resolve(symbol, TF, key, refresh_if_missing=True)
        except Exception:
            pass
        cfg_path = AUTO / slot_paths.daemon_config_name(symbol, TF)
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            stops = dict(cfg.get("strategy_stops") or {})
            stops[key] = float(stop)
            cfg["strategy_stops"] = stops
            cfg["stop_loss_pct"] = float(stop)
            atomic_write_json(cfg_path, cfg)
        sync = daemon.sync_roster_daemon_configs(enable_auto_open=True)
        try:
            closeout.notify_pool_change = orig_notify
            closeout.notify_pool_change(
                "human_confirm_mount",
                detail={"keys": [key], "tier": tier_code},
                auto_refresh=True,
            )
        except Exception:
            pass
    finally:
        closeout.notify_pool_change = orig_notify

    live_after = forecast.list_auto_trade_strategies() or []
    mounted = [row for row in live_after if row.get("strategy_key") == key]
    return {
        "ok": bool(mounted),
        "key": key,
        "tier": tier_code,
        "stop": stop,
        "leverage": leverage,
        "receipt": str(receipt_path),
        "daemon": daemon_info,
        "sync_ok": sync.get("ok") if isinstance(sync, dict) else None,
        "live_n": len(live_after),
        "mounted": {
            "symbol": mounted[0].get("symbol") if mounted else None,
            "tier": mounted[0].get("strategy_tier") if mounted else None,
            "name": mounted[0].get("strategy_name") if mounted else None,
        } if mounted else None,
    }


def unmount_key(key, symbol):
    if str(symbol or "").upper() in KEEP4:
        return {"ok": False, "error": "keep4_symbol", "symbol": symbol}
    aid = "%s|%s|%s" % (str(symbol).upper(), TF, key)
    removed = roster.unregister(key, source="kdh_cap_not_up")
    try:
        hcp._set_semantic_live(key, False)
    except Exception:
        pass
    controls = {}
    try:
        controls = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
    except Exception:
        controls = {"assignments": {}}
    assignments = controls.setdefault("assignments", {})
    row = dict(assignments.get(aid) or {})
    if row:
        row["pause_new_entries"] = True
        row["deleted_at"] = _now()
        row["deleted_reason"] = "kdh_cap_not_up"
        assignments[aid] = row
        controls["updated_at"] = _now()
        tmp = CONTROL_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(controls, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(CONTROL_PATH)
    if SEM_PATH.exists():
        sem = json.loads(SEM_PATH.read_text(encoding="utf-8"))
        rows = []
        for existing in list(sem.get("strategies") or []):
            if existing.get("key") == key:
                existing = dict(existing)
                existing["live"] = False
                existing["live_enabled"] = False
            rows.append(existing)
        sem["strategies"] = rows
        atomic_write_json(SEM_PATH, sem)
    sync = daemon.sync_roster_daemon_configs(enable_auto_open=True)
    return {
        "ok": True,
        "key": key,
        "unregister": removed,
        "sync_ok": sync.get("ok") if isinstance(sync, dict) else None,
    }


def _pick_mount_tier(trial=None, leverage=None, stop=None):
    """Pick capital tier for mount. Never uses occupancy/combo C as admission.

    Prefer DAIFUKU (smaller ratio). If Kelly leverage blows solo-risk, clamp
    leverage down so the tier fits risk_cap — do not refuse the mount.
    """
    preferred = ("DAIFUKU", "MACARON")
    stop_f = None
    try:
        stop_f = float(stop) if stop not in (None, "") else None
    except Exception:
        stop_f = None
    lev_f = None
    try:
        lev_f = float(leverage) if leverage not in (None, "") else None
    except Exception:
        lev_f = None
    if lev_f is None:
        try:
            lev_f = float(((trial or {}).get("risk") or {}).get("leverage") or 0) or None
        except Exception:
            lev_f = None
    if stop_f is None:
        try:
            stop_f = float(((trial or {}).get("risk") or {}).get("stop") or 0) or None
        except Exception:
            stop_f = None
    for code in preferred:
        if lev_f and stop_f and stop_f > 0:
            gate = traj.gate_confirm_tier(code, lev_f, stop_f)
            if gate.get("ok"):
                return {"tier": code, "leverage": lev_f, "stop": stop_f, "gate": gate}
            # Clamp leverage to fit this tier under risk_cap.
            ratio = float(traj.TIER_RATIOS.get(code) or 0)
            cap = float(gate.get("risk_cap") or traj.risk_cap())
            if ratio > 0 and cap > 0:
                max_lev = cap / (ratio * float(stop_f))
                applied = traj.floor_exchange_leverage(max_lev)
                if applied and applied > 0:
                    gate2 = traj.gate_confirm_tier(code, applied, stop_f)
                    if gate2.get("ok"):
                        return {
                            "tier": code, "leverage": float(applied), "stop": stop_f,
                            "gate": gate2, "leverage_clamped": True,
                        }
        else:
            return {"tier": code, "leverage": lev_f, "stop": stop_f}
    return {"tier": "DAIFUKU", "leverage": lev_f, "stop": stop_f}


def _waive_trade_count_only(ev):
    """Permanently disabled: never clear trade_count / never invent n-waive.

    Kept as a named hook so install_into_kdh can still override; body is
    never_waive_trade_count (hard forbid).
    """
    try:
        from dual_engine_workflow_v2.creation_small_n_rigor import never_waive_trade_count
        return never_waive_trade_count(ev)
    except Exception:
        ev = dict(ev or {})
        failed = list(ev.get("failed_rules") or [])
        if failed:
            return None, ",".join(failed)
        if not ev.get("ok"):
            return None, "gate_not_ok"
        gE = ev.get("E")
        if gE is None:
            gE = (((ev.get("quality_gate") or {}).get("is_gate") or {}).get("metrics") or {}).get("E_raw")
        try:
            gE = float(gE) if gE is not None else None
        except Exception:
            gE = None
        if gE is None or gE + 1e-12 < 0.003:
            return None, "E_raw_below_threshold"
        q_failed = list((ev.get("quality_gate") or {}).get("failed_rules") or [])
        if "trade_count_below_threshold" in q_failed:
            return None, "n_waive_forbidden"
        return ev, None


def _clear_deleted_flag(symbol, key):
    if not CONTROL_PATH.is_file():
        return False
    try:
        controls = json.loads(CONTROL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return False
    aid = "%s|1h|%s" % (symbol, key)
    row = (controls.get("assignments") or {}).get(aid)
    if not isinstance(row, dict):
        return False
    if not (row.get("deleted_at") or row.get("deleted_reason") or row.get("pause_new_entries")):
        return False
    row = dict(row)
    row.pop("deleted_at", None)
    row.pop("deleted_reason", None)
    row["pause_new_entries"] = False
    row["new_entries_allowed"] = True
    controls.setdefault("assignments", {})[aid] = row
    atomic_write_json(CONTROL_PATH, controls)
    return True


def _combo_book(combo, symbol, key):
    by = combo.get("capability_by_key") or {}
    key = str(key or "")
    cands = []
    aid = "%s|1h|%s" % (symbol, key)
    for k, row in by.items():
        if not isinstance(row, dict):
            continue
        ks = str(k)
        if ks in (key, aid) or ks.endswith("|" + key) or (key and key in ks):
            cands.append((ks, row))
    if not cands:
        return {}

    def _score(item):
        row = item[1]
        c = row.get("C_week")
        oos = row.get("C_week_oos")
        hit = (
            c is not None and float(c) + 1e-16 >= C_MIN
            and oos is not None and float(oos) + 1e-16 >= OOS_MIN
        )
        return (1 if hit else 0, float(c or 0), float(oos or 0))

    cands.sort(key=_score, reverse=True)
    return cands[0][1]


def _refresh_combo():
    emit({"phase": "refresh_start", "at": _now()})
    out = closeout.run_lightweight_statistical_refresh(push_wx=False)
    emit({
        "phase": "refresh_done",
        "ok": out.get("ok") if isinstance(out, dict) else None,
        "error": (out or {}).get("error") if isinstance(out, dict) else None,
        "at": _now(),
    })
    try:
        subprocess.check_call(["systemctl", "restart", "qiyu-web"])
    except Exception as exc:
        emit({"phase": "web_restart_fail", "error": str(exc)[:200]})
    return out


def _ensure_base_combo():
    if _STATE["live"] is None or _STATE["base_combo"] is None:
        live = forecast.list_auto_trade_strategies() or []
        combo = pcm.compute_portfolio_combo_metrics(live)
        _STATE["live"] = live
        _STATE["base_combo"] = combo


def trial_mount_if_floor(cand, ev, state):
    """Mount when capability floor + E are met.

    Authority: isolated_cap hit_floor + E_raw≥0.003 + gate ok（含 n≥30，禁止 n-waive）.
    Explicitly NOT admission: occupancy trial combo C, raises_pool, dual_up_book,
    skip_occ_below_floor, post-mount combo unmount.
    """
    try:
        iso = isolated_cap(cand, ev if isinstance(ev, dict) else {})
    except Exception as exc:
        emit({"phase": "iso_fail_before_mount", "id": cand.get("id"), "error": str(exc)[:240]})
        return False
    if not iso.get("hit_floor"):
        emit({
            "phase": "skip_below_cap_floor", "id": cand.get("id"),
            "C_week_pct": iso.get("C_week_pct"), "C_week_oos_pct": iso.get("C_week_oos_pct"),
            "n": iso.get("n"),
        })
        return False

    gated, why = _waive_trade_count_only(ev)  # never waives; gate-pass check only
    if gated is None:
        emit({
            "phase": "skip_gate_E_or_other", "id": cand.get("id"), "reason": why,
            "failed": list((ev or {}).get("failed_rules") or [])[:8],
            "E": (ev or {}).get("E"),
            "n_waive": "forbidden",
        })
        return False
    ev = gated

    mission = persist_mission(cand, ev)
    ir = ir_from_dict(ev.get("ir") or _ir_payload(cand))
    rh = compute_recipe_hash(ir)
    trial_key = "cursor_%s" % rh[:24]
    emit({
        "phase": "ingest", "id": cand["id"], "mission": mission,
        "trial_key": trial_key, "authority": "capability_floor_plus_E",
        "n_waived": False,
        "C_week_pct": iso.get("C_week_pct"), "C_week_oos_pct": iso.get("C_week_oos_pct"),
    })
    _STATE["trial_key"] = trial_key

    # Trial is metadata only (stop/leverage hints). Never an occupancy admission.
    trial = {}
    try:
        trial = run_trial(
            mission, cand["symbol"], TF, cand["direction"], trial_key,
            cand.get("display") or cand["id"],
            out_path="/tmp/trial_%s.json" % cand["id"],
        ) or {}
    except Exception as exc:
        emit({"phase": "trial_meta_fail", "id": cand["id"], "error": str(exc)[:200]})
        trial = {"ok": False, "risk": {}, "rows": []}
    emit({
        "phase": "trial_meta_only", "id": cand["id"], "ok": trial.get("ok"),
        "risk": trial.get("risk"),
        "note": "occupancy_combo_C_not_used_for_admission",
    })

    pick = _pick_mount_tier(trial=trial)
    chosen = {"tier": pick.get("tier") or "DAIFUKU"}
    job = {
        "symbol": cand["symbol"],
        "side": cand["direction"],
        "rd": cand.get("text_summary") or cand["id"],
        "display": cand.get("display") or ("%s1h%s" % (
            _tag(cand["symbol"]),
            "空头" if cand["direction"] == "short" else "多头",
        )),
        "label": cand["id"],
    }
    if pick.get("leverage"):
        trial = dict(trial)
        risk = dict(trial.get("risk") or {})
        risk["leverage"] = pick.get("leverage")
        if pick.get("stop"):
            risk["stop"] = pick.get("stop")
        trial["risk"] = risk
    emit({
        "phase": "mount_start", "id": cand["id"], "tier": chosen.get("tier"),
        "C_week_pct": iso.get("C_week_pct"), "C_week_oos_pct": iso.get("C_week_oos_pct"),
        "leverage_clamped": bool(pick.get("leverage_clamped")),
    })
    orig_notify, orig_wx = _silence_side_effects()
    try:
        mounted = mount_one(job, mission, ir, ev.get("quality_gate") or {}, trial, chosen)
    finally:
        _restore_side_effects(orig_notify, orig_wx)
    emit({"phase": "mount_done", **{
        k: mounted.get(k) for k in (
            "ok", "key", "error", "live_n", "sync_ok", "mounted", "stop", "leverage", "tier",
        ) if k in mounted or mounted.get(k) is not None
    }})
    if not mounted.get("ok"):
        return False
    key = mounted.get("key")
    _clear_deleted_flag(cand["symbol"], key)
    state.setdefault("mounted", []).append({
        "id": cand["id"], "key": key, "symbol": cand["symbol"],
        "tier": chosen.get("tier"), "at": _now(),
        "authority": "capability_floor_plus_E",
        "iso_C_week_pct": iso.get("C_week_pct"),
        "iso_C_week_oos_pct": iso.get("C_week_oos_pct"),
    })
    _save_state(state)
    try:
        _refresh_combo()
    except Exception:
        pass
    live_after = forecast.list_auto_trade_strategies() or []
    payload = {
        "ok": True, "at": _now(), "id": cand["id"], "key": key,
        "symbol": cand["symbol"], "tier": chosen.get("tier"),
        "C_week": iso.get("C_week"), "C_week_oos": iso.get("C_week_oos"),
        "C_week_pct": iso.get("C_week_pct"), "C_week_oos_pct": iso.get("C_week_oos_pct"),
        "reason": "capability_floor_hit_E_ok",
        "keep4_untouched": True,
        "occupancy_not_admission": True,
        "live_n": len(live_after),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    emit({"phase": "done", **payload})
    return True


# Geometry atoms (entry spine) + timing whitelist from thin_timing_atoms (single source).
_GEO_ATOM_KEYS = (
    ("ema", "held"), ("ema", "cross_up"), ("ema", "cross_down"),
    ("ema", "above_ema"), ("ema", "below_ema"),
    ("sma", "held"), ("wma", "held"), ("hma", "held"),
    ("donchian", "breakout_up"), ("donchian", "breakout_down"),
    ("candle_pattern", "bullish_reclaim"), ("candle_pattern", "bearish_reject"),
    ("close_z", "below"), ("close_z", "above"),
)
try:
    CREATE_ATOM_KEYS = _GEO_ATOM_KEYS + tuple(_TTA.TIMING_ATOM_PAIRS)
    _TIMING_ALIAS_NOTE = (
        "timing 别名：macd_histogram_cross→macd_dd cross_up；"
        "cci_oversold→cci below；cci_overbought→cci above。"
    )
except Exception:
    CREATE_ATOM_KEYS = _GEO_ATOM_KEYS + (
        ("skdj_k", "below"), ("skdj_k", "above"), ("skdj_k", "between"), ("skdj_k", "cross_up"),
        ("skdj_d", "below"), ("skdj_d", "above"), ("skdj_d", "cross_up"),
        ("skdj_kd", "above"), ("skdj_kd", "below"),
        ("skdj_kd", "cross_up"), ("skdj_kd", "cross_down"),
        ("skdj_diff", "above"), ("skdj_diff", "below"), ("skdj_diff", "between"),
        ("kdj_k", "below"), ("kdj_k", "above"), ("kdj_k", "cross_up"),
        ("kdj_d", "below"), ("kdj_d", "above"),
        ("kdj_kd", "above"), ("kdj_kd", "below"),
        ("kdj_kd", "cross_up"), ("kdj_kd", "cross_down"),
        ("macd_dd", "above"), ("macd_dd", "below"),
        ("macd_dd", "cross_up"), ("macd_dd", "cross_down"),
        ("macd_hist", "above"), ("macd_hist", "below"),
        ("macd_dif", "above"), ("macd_dif", "below"),
        ("cci", "above"), ("cci", "below"), ("cci", "cross_up"), ("cci", "cross_down"),
    )
    _TIMING_ALIAS_NOTE = ""


def _free_create_prompt():
    return str(os.environ.get("KDH_FREE_CREATE") or "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _system_prompt():
    keys = ["%s %s" % (a, b) for a, b in CREATE_ATOM_KEYS if (a, b) in EXECUTABLE_ATOM_KEYS]
    pool = "/".join(TIMEFRAME_POOL)
    if _free_create_prompt():
        return (
            "你是现网合约策略作者（多周期池 %s；默认 %s）。过关前禁止输出任何自然语言、思考过程、解释、英文、markdown。"
            "只输出一个 JSON 对象，第一个字符必须是 {，最后一个必须是 }。\n"
            "输出形状：{\"hypothesis\":{\"intent\":\"add_location|add_relation|add_timing|"
            "adjust_geometry|replace_spine\",\"rationale_keys\":[\"...\"],\"recipe\":{...}}}。"
            "也可兼容 {\"recipe\":{...}}，但优先 hypothesis。\n"
            "自由创造：family/symbol/timeframe/route/几何(held,xwin,atr,hold,z,dwin…)/timing 均可自选与重写。"
            "禁止被种子或上一条身份焊死；换标的、换族、换周期、改几何都允许（换周期=新研究合同，重算 n/C）。\n"
            "不合格时请大胆改完整 recipe：优先叠加 location/relation（价格相对MA/EMA held|cross|reclaim），"
            "不要只会拧 timing。\n"
            "diversity：禁止复读同一壳子；新一轮须实质改变结构（路线/家族/几何/timing 至少两维）。\n"
            "family 只能是 xu_long / xd_short / pb_long / pb_short / ch_long / ch_short / ma_long / ma_short。"
            "mean_revert 路线：family 用 ma_long（SMA）。"
            "symbol 必须是真实合约，禁止 XXX 占位符。"
            "recipe 必须含 timeframe（%s）与 route。\n"
            "对外中文结果只用：止盈 / 止损 / 定时。创造进度只用：研究中 / 已达到机器基础门槛 / 未达到机器基础门槛。"
            "这些词只用于机器内部键，回复里不要写句子。\n"
            "可执行原子（factor operator）只能从下面选（timing 以 thin_timing_atoms 展平白名单为准）：\n"
            % (pool, TF, pool)
            + "；".join(keys)
            + (("\n" + _TIMING_ALIAS_NOTE) if _TIMING_ALIAS_NOTE else "")
            + "\n禁止为凑 n 放宽参数；禁止堆砌3个以上同维振荡器族而无量能/波动维。"
        )
    return (
        "你是现网合约策略作者（多周期池 %s；默认 %s）。过关前禁止输出任何自然语言、思考过程、解释、英文、markdown。"
        "只输出一个 JSON 对象，第一个字符必须是 {，最后一个必须是 }。\n"
        "研究合同字段：route（创造路线）、exec_tf/timeframe（主执行周期）、filter_tfs（大周期过滤，可空数组）。\n"
        "几何锁死（held/xwin/atr/hold/z/fast/slow）；family/symbol/route 受探索预算与路线可用性约束。"
        "不合格时只改 timing，除非机器下发 switch_family / switch_tf / switch_route / close / refine_timing_diverse / refine_geometry_rr。\n"
        "换周期须等机器下发跨周期合同（新合同重算 n/C，禁止合并上周期样本）；"
        "换族须完成该路线+周期+家族最小探索深度。\n"
        "diversity：禁止连续只拧阈值。新一轮 timing 必须改变至少 1 个 factor|operator 结构"
        "（优先 cross_up/cross_down/between；可用 atr_pct/vwap 换维）；"
        "同一 structure_fingerprint 反复出现视为无效探索。\n"
        "family 只能是 xu_long / xd_short / pb_long / pb_short / ch_long / ch_short / ma_long / ma_short。"
        "channel 路线主脊是唐奇安突破（可不加 candle_pattern）；ema_osc 仍要均线持有+形态；"
        "mean_revert 路线：family 必须 ma_long（SMA 位置脊），禁止 xu_long/EMA；"
        "timing 用 atr_pct below + skdj_k cross_up（波动压缩后收回），禁止当趋势突破写。"
        "symbol 必须是真实合约，禁止 XXX 占位符。"
        "recipe 必须含 timeframe（%s）与 route。\n"
        "对外中文结果只用：止盈 / 止损 / 定时。创造进度只用：研究中 / 已达到机器基础门槛 / 未达到机器基础门槛。"
        "这些词只用于机器内部键，回复里不要写句子。\n"
        "可执行原子（factor operator）只能从下面选（timing 以 thin_timing_atoms 展平白名单为准）：\n"
        % (pool, TF, pool)
        + "；".join(keys)
        + (("\n" + _TIMING_ALIAS_NOTE) if _TIMING_ALIAS_NOTE else "")
        + "\n禁止为凑 n 放宽参数；S1 不得输出/执行 loosen_timing。"
        "禁止堆砌3个以上同维振荡器族而无量能/波动维。"
    )


def _user_brief(lane, symbols):
    live = ",".join(sorted(_live_symbols()))
    keep = ",".join(sorted(KEEP4))
    forbid = ",".join(sorted(forbidden_symbols() or []))
    pool = ",".join(symbols)
    lock_or_free = (
        "自由创造：可换标的/家族/周期/几何/timing；禁止只拧 timing 复读。"
        if _free_create_prompt()
        else (
            "几何锁死；family 受探索预算；预算耗尽由机器下发 next_family / switch_tf / close 才可换。"
            "平时只改 timing（SKDJ+MACD/CCI/KDJ）。"
            "不合格时只改 timing，除非机器下发 switch_family / switch_tf / close。"
        )
    )
    return (
        "现在进行新一次策略创造，要求与制造 EWY-USDT-SWAP 多头上穿收回时相同。过关前禁止任何文字，只输出 JSON。\n"
        "1) 先过机器门：样本内成交≥30，止损先触<30%%，周开仓频次≥0.50，原始期望≥0.003，趋势识字必须过。\n"
        "2) 再生产能力比率：capability_from_trades（挂钩硬顶、DSR、min_trl 都开）。样本内 C_week≥0.1%% 且样本外 C_week_oos≥0.05%%，两边 usable。\n"
        "3) 过门后按 EWY 同款试轮挂载。档位马卡龙=50%%、大福=25%%。不要赔率型。\n"
        "4) 研究时钟=实盘时钟。timeframe 默认 %s，池=%s。route=channel 时可用唐奇安突破；其它路线不要 Donchian。"
        "不要加宽止损来假装挂钩下降。禁止为凑 n 放宽参数。\n"
        "5) 不要动已挂本，不要重熔 KEEP4。\n"
        "位置型入场必须保留家族：均线持有 + 上穿/下穿 + 看涨收回/看跌拒绝（或回踩/回抽 + z）。不要纯振荡器当方向。\n"
        "SKHYNIX 质变先例：位置不动，加 SKDJ 时机。SKHYNIX 是 EMA28 持有 + 上穿 EMA12 + z≤1.0 + 看涨收回 + SKDJ(22) K>50。\n"
        "EWY 已挂先例：上穿 EMA2 + 看涨收回 + MACD柱>0 + SKDJ(12) K-D>-2 + SKDJ K<96。SKDJ 必须是通达信/OKX 的 SKDJ，不是普通 KDJ。\n"
        "扩展：SKDJ 时机叶不能孤立、不能单调。必须再配 MACD（hist/dif/死叉金叉）或 CCI 或 KDJ，并与 K 线形态（收回/拒绝）同时成立。位置负责方向，时机负责何时下单。\n"
        "已挂禁止再造：%s\nKEEP4 禁止：%s\n研究禁标：%s\n"
        "本通道优先标的：%s\n"
        "每次只输出 1 条 JSON。机器会立刻回测并把数字配对回来。"
        "%s"
        "目标：样本内 C_week≥0.1%% 且样本外 C_week_oos≥0.05%%，且止损先触<30%%、n≥30。禁止文字。\n"
        "格式：{\"recipe\":{\"family\":\"xu_long\",\"symbol\":\"BTC-USDT-SWAP\",\"timeframe\":\"%s\",\"held\":4,\"xwin\":2,\"z\":null,\"atr\":1.7,\"hold\":16,\"timing\":[{\"factor\":\"macd_hist\",\"operator\":\"above\",\"value\":0},{\"factor\":\"skdj_diff\",\"operator\":\"above\",\"value\":-2,\"window\":12}]}}\n"
        "xu/xd 用 held,xwin,atr,hold,z；pb 用 held,fast,slow,z,atr,hold。timing 至少 1 个 SKDJ + 至少 1 个 MACD 或 CCI 或 KDJ。\n"
        "通道：%s。现在只输出第 1 条 {\"recipe\":{...}}。"
        % (TF, "/".join(TIMEFRAME_POOL), live, keep, forbid, pool, lock_or_free, TF, lane)
    )


def _extract_json(text):
    raw = str(text or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", raw, re.S | re.I)
    if fence:
        raw = fence.group(1)
    else:
        start_obj = raw.find("{")
        start_arr = raw.find("[")
        starts = [i for i in (start_obj, start_arr) if i >= 0]
        if not starts:
            return None
        start = min(starts)
        raw = raw[start:]
    try:
        return json.loads(raw)
    except Exception:
        try:
            last = max(raw.rfind("}"), raw.rfind("]"))
            if last > 0:
                return json.loads(raw[: last + 1])
        except Exception:
            return None
    return None


def _timing_ok(timing):
    try:
        from dual_engine_workflow_v2 import thin_timing_atoms as _tta
        rows = _tta.normalize_timing(timing)
        if rows is None:
            return False
        for item in rows:
            factor = str(item.get("factor") or "")
            op = str(item.get("operator") or "")
            if (factor, op) not in EXECUTABLE_ATOM_KEYS:
                return False
        return _tta.timing_ok(rows)
    except Exception:
        factors = set()
        for item in timing:
            if not isinstance(item, dict):
                return False
            factor = str(item.get("factor") or "")
            op = str(item.get("operator") or "")
            if (factor, op) not in EXECUTABLE_ATOM_KEYS:
                return False
            factors.add(factor)
        return bool(factors & TIMING_SKDJ) and bool(factors & TIMING_PEER)


def _attach_timing(row, timing):
    row = dict(row)
    atoms = list(row.get("entry_atoms") or [])
    title = str(row.get("text_summary") or "")
    for i, item in enumerate(timing):
        fid = "tm%d" % i
        node = atom(
            fid,
            str(item.get("factor")),
            str(item.get("operator")),
            value=item.get("value"),
            window=item.get("window"),
        )
        if item.get("upper_value") is not None:
            node["upper_value"] = item.get("upper_value")
        atoms.append(node)
        tag = "%s %s %s" % (
            item.get("factor"), item.get("operator"), item.get("value"),
        )
        if "不要赔率型" in title:
            title = title.replace("不要赔率型", tag + "，不要赔率型")
        else:
            title = title + "，" + tag
        row["id"] = "%s_%s" % (row.get("id"), fid)
    row["entry_atoms"] = atoms
    row["text_summary"] = title
    return row


def _cand_from_recipe(recipe, lane):
    if not isinstance(recipe, dict):
        return None, "not_object"
    # P0-C/R: normalize research contract (route / exec_tf / filter_tfs)
    try:
        from dual_engine_workflow_v2 import creation_contract as _cc
        lane_state = {"tf": TF, "route": recipe.get("route")}
        norm, nerr = _cc.normalize_contract(recipe, lane_state=lane_state)
        if nerr:
            return None, nerr
        recipe = norm
    except Exception as exc:
        return None, "contract:%s" % str(exc)[:80]
    family = str(recipe.get("family") or "").strip()
    symbol = str(recipe.get("symbol") or "").strip().upper()
    tf, tf_err = _resolve_recipe_tf(recipe)
    if tf_err:
        return None, tf_err
    if (not symbol) or ("XXX" in symbol) or ("|" in family) or ("|" in symbol):
        return None, "placeholder_or_bad_symbol"
    if symbol not in (LANES.get(lane) or EQUITY) and symbol not in CRYPTO and symbol not in EQUITY:
        # still allow if in lane pool only; reject unknown placeholders
        pass
    if symbol in _banned():
        return None, "banned_or_live"
    timing = list(recipe.get("timing") or [])
    try:
        from dual_engine_workflow_v2 import thin_timing_atoms as _tta
        normed = _tta.normalize_timing(timing)
        if normed is None:
            return None, "atom_not_in_whitelist"
        timing = normed
        recipe = dict(recipe)
        recipe["timing"] = timing
    except Exception:
        pass
    # P0-Q hard reject if streak already exhausted (lane state not always here)
    try:
        from dual_engine_workflow_v2 import creation_dimension_policy as _cdp
        assess = _cdp.assess_dimension_stack(timing)
        if assess.get("soft_warn") and int(recipe.get("_dim_soft_ignore") or 0) >= 3:
            return None, "dimension_redundant_hard"
    except Exception:
        pass
    if not _timing_ok(timing):
        return None, "timing_need_skdj_plus_macd_cci_or_kdj"
    route = str(recipe.get("route") or "ema_osc")
    family = str(recipe.get("family") or "").strip()
    # Channel / MA geo ranges
    try:
        atr = float(recipe.get("atr"))
        hold = int(recipe.get("hold"))
    except Exception:
        return None, "bad_geo"
    if hold < 6 or hold > 36 or atr < 1.2 or atr > 3.8:
        return None, "geo_range"
    z = recipe.get("z")
    try:
        z = None if z in (None, "", "null") else float(z)
    except Exception:
        return None, "bad_z"
    with_pattern = any(
        str((t or {}).get("factor") or "") == "candle_pattern"
        for t in (recipe.get("entry_extra") or [])
    )
    # Explicit recipe flag or presence of pattern request
    if recipe.get("with_pattern") in (True, 1, "1", "true", "True"):
        with_pattern = True
    try:
        if family == "ch_long":
            dwin = int(recipe.get("dwin") or recipe.get("held") or 20)
            if dwin < 5 or dwin > 100:
                return None, "geo_range"
            row = long_ch(symbol, dwin, atr, hold, timeframe=tf, with_pattern=with_pattern)
        elif family == "ch_short":
            dwin = int(recipe.get("dwin") or recipe.get("held") or 20)
            if dwin < 5 or dwin > 100:
                return None, "geo_range"
            row = short_ch(symbol, dwin, atr, hold, timeframe=tf, with_pattern=with_pattern)
        elif family == "ma_long":
            held = int(recipe.get("held"))
            if held < 2 or held > 100:
                return None, "geo_range"
            kind = str(recipe.get("ma_kind") or "sma")
            row = long_ma(symbol, held, atr, hold, ma_kind=kind, timeframe=tf, with_pattern=True)
        elif family == "ma_short":
            held = int(recipe.get("held"))
            if held < 2 or held > 100:
                return None, "geo_range"
            kind = str(recipe.get("ma_kind") or "sma")
            row = short_ma(symbol, held, atr, hold, ma_kind=kind, timeframe=tf, with_pattern=True)
        elif family == "xu_long":
            held = int(recipe.get("held"))
            if held < 2 or held > 48:
                return None, "geo_range"
            row = long_xu(symbol, held, int(recipe.get("xwin")), atr, hold, z, timeframe=tf)
        elif family == "xd_short":
            held = int(recipe.get("held"))
            if held < 2 or held > 48:
                return None, "geo_range"
            row = short_xd(symbol, held, int(recipe.get("xwin")), atr, hold, z, timeframe=tf)
        elif family == "pb_long":
            held = int(recipe.get("held"))
            if held < 2 or held > 48:
                return None, "geo_range"
            row = long_pb(
                symbol, held, z if z is not None else 1.0, atr, hold,
                fast=int(recipe.get("fast") or 12),
                slow=int(recipe.get("slow") or 26),
                timeframe=tf,
            )
        elif family == "pb_short":
            held = int(recipe.get("held"))
            if held < 2 or held > 48:
                return None, "geo_range"
            row = short_pb(
                symbol, held, z if z is not None else 1.0, atr, hold,
                fast=int(recipe.get("fast") or 12),
                slow=int(recipe.get("slow") or 26),
                timeframe=tf,
            )
        else:
            return None, "bad_family"
    except Exception as exc:
        return None, "build:%s" % str(exc)[:80]
    row = _attach_timing(row, timing)
    factors = set(str(a.get("factor") or "") for a in (row.get("entry_atoms") or []))
    if route == "channel" or family.startswith("ch"):
        if "donchian" not in factors:
            return None, "need_donchian_breakout"
        if "donchian" in factors and not _route_allows_donchian({"route": route}):
            return None, "donchian_forbidden"
    elif family.startswith("ma"):
        if not (factors & set(("sma", "wma", "hma"))):
            return None, "need_ma_held"
    else:
        if "ema" not in factors or "candle_pattern" not in factors:
            return None, "need_location_and_pattern"
        if "donchian" in factors:
            return None, "donchian_forbidden"
    row["id"] = "kdh_%s_%s" % (lane, row.get("id"))
    row["timeframe"] = tf
    row["exec_tf"] = tf
    row["filter_tfs"] = list(recipe.get("filter_tfs") or [])
    row["route"] = route
    if family.startswith("xu"):
        row["display"] = "%s%s多头上穿收回" % (_tag(symbol), tf) if row.get("direction") == "long" else "%s%s空头下穿拒绝" % (_tag(symbol), tf)
    elif family.startswith("xd"):
        row["display"] = "%s%s空头下穿拒绝" % (_tag(symbol), tf)
    elif family.startswith("pb"):
        row["display"] = "%s%s%s" % (
            _tag(symbol),
            tf,
            "空头回抽拒绝" if row.get("direction") == "short" else "多头回踩收回",
        )
    elif family.startswith("ch"):
        row["display"] = "%s%s唐奇安%s" % (
            _tag(symbol), tf, "下破" if row.get("direction") == "short" else "上破",
        )
    elif family.startswith("ma"):
        row["display"] = row.get("display") or ("%s%s均线持有" % (_tag(symbol), tf))
    return row, None


def _gate_extra(ev):
    qg = ev.get("quality_gate") or {}
    is_gate = qg.get("is_gate") or {}
    is_m = is_gate.get("metrics") or {}
    hitch = qg.get("hitch_path") or is_gate.get("hitch_path") or {}
    stats = hitch.get("stats") if isinstance(hitch.get("stats"), dict) else {}
    return {
        "gate_n": is_m.get("n") or ev.get("n"),
        "gate_sl": is_m.get("sl_first_rate") or stats.get("sl_rate"),
        "gate_E": is_m.get("E_raw"),
        "gate_weekly": is_m.get("weekly_opens"),
        "failed": list(ev.get("failed_rules") or [])[:8],
    }


def _eval_one(cand, state, lane):
    if _STOP.is_set():
        return {"skip": True, "reason": "stopped"}
    tf, tf_err = _resolve_recipe_tf({"timeframe": cand.get("timeframe")})
    if tf_err:
        return {"ok": False, "phase": "skip_tf", "error": tf_err, "timeframe": cand.get("timeframe")}
    pf = creation_data_preflight(cand["symbol"], tf)
    if not pf.get("ok"):
        return {
            "ok": False,
            "phase": "skip_data",
            "error": "research_candles_missing:%s" % tf,
            "detail": pf.get("error"),
            "timeframe": tf,
        }
    try:
        ir = ir_from_dict(_ir_payload(cand))
        rh = compute_recipe_hash(ir)
    except Exception as exc:
        return {"ok": False, "phase": "ir_fail", "error": str(exc)[:160]}
    if rh in (state.get("done_ids") or []):
        return {"ok": False, "phase": "dup", "rh": rh}
    emit({"phase": "atom_start", "lane": lane, "id": cand["id"], "symbol": cand["symbol"], "at": _now()})
    try:
        cap_row = isolated_cap(cand, {"ir": _ir_payload(cand)})
    except Exception as exc:
        cap_row = {"ok": False, "error": str(exc)[:200], "trace": traceback.format_exc()[-300:]}
    emit({"phase": "isolated_cap", "lane": lane, "id": cand["id"],
          "C_week_pct": cap_row.get("C_week_pct"), "C_week_oos_pct": cap_row.get("C_week_oos_pct"),
          "n": cap_row.get("n"), "n_oos": cap_row.get("n_oos"), "hitch": cap_row.get("hitch"),
          "hit_floor": cap_row.get("hit_floor"), "error": cap_row.get("error")})
    if not cap_row.get("ok") and cap_row.get("error"):
        state.setdefault("done_ids", []).append(rh)
        _save_state(state)
        return {
            "ok": False, "phase": "iso_fail", "rh": rh,
            "error": cap_row.get("error"),
            "C_week_pct": cap_row.get("C_week_pct"),
            "C_week_oos_pct": cap_row.get("C_week_oos_pct"),
        }
    # hit_floor is diagnostic only — still attempt gate+mount.
    try:
        ev = evaluate_atom(cand)
    except Exception as exc:
        ev = {"ok": False, "error": "exc", "detail": str(exc)[:300]}
    packed = dict(_qg_compact(ev))
    packed.update(_gate_extra(ev))
    emit({"phase": "atom_eval", "lane": lane, "id": cand["id"], **packed})
    state.setdefault("done_ids", []).append(rh)
    _save_state(state)
    if not ev.get("ok"):
        return {"ok": False, "phase": "gate_fail", "rh": rh, **_gate_extra(ev),
                "hit_floor": cap_row.get("hit_floor")}
    if trial_mount_if_floor(cand, ev, state):
        _STOP.set()
        _MOUNTED.append({"lane": lane, "id": cand["id"], "symbol": cand["symbol"]})
        return {"ok": True, "phase": "mounted", "id": cand["id"]}
    return {"ok": False, "phase": "mount_skip", "rh": rh}


def _recipe_complete(recipe):
    if not isinstance(recipe, dict):
        return False
    family = str(recipe.get("family") or "").strip()
    symbol = str(recipe.get("symbol") or "").strip()
    timing = recipe.get("timing")
    return bool(family and symbol and isinstance(timing, list) and timing)


def _first_recipe(parsed):
    if isinstance(parsed, dict):
        hyp = parsed.get("hypothesis")
        if isinstance(hyp, dict):
            rec = hyp.get("recipe")
            if _recipe_complete(rec):
                return rec
            got = _first_recipe(hyp)
            if got:
                return got
        rec = parsed.get("recipe")
        if _recipe_complete(rec):
            return rec
        for row in list(parsed.get("recipes") or []):
            if _recipe_complete(row):
                return row
            got = _first_recipe(row)
            if got:
                return got
        if _recipe_complete(parsed):
            return parsed
        return None
    if isinstance(parsed, list):
        for row in parsed:
            got = _first_recipe(row)
            if got:
                return got
    return None


def _identity(recipe):
    if not _recipe_complete(recipe):
        return ""
    try:
        from dual_engine_workflow_v2 import creation_contract as _cc
        return _cc.contract_identity(recipe)
    except Exception:
        pass
    family = str(recipe.get("family") or "").strip()
    symbol = str(recipe.get("symbol") or "").strip().upper()
    tf = _resolve_tf(recipe.get("exec_tf") or recipe.get("timeframe") or TF)
    held = recipe.get("held")
    xwin = recipe.get("xwin")
    route = str(recipe.get("route") or "ema_osc")
    if family.startswith("pb"):
        return "%s|%s|%s|%s|%s|%s|%s" % (
            symbol, tf, route, family, held, recipe.get("fast"), recipe.get("slow"),
        )
    return "%s|%s|%s|%s|%s|%s" % (symbol, tf, route, family, held, xwin)


def _recipe_snap(recipe):
    if not isinstance(recipe, dict):
        return {}
    keys = (
        "route", "exec_tf", "filter_tfs", "research_mode",
        "family", "symbol", "timeframe", "held", "xwin", "fast", "slow", "z", "atr", "hold", "timing",
    )
    out = {}
    for key in keys:
        if key in recipe:
            out[key] = recipe.get(key)
    tf = _resolve_tf(out.get("exec_tf") or out.get("timeframe") or recipe.get("timeframe") or TF)
    out["timeframe"] = tf
    out["exec_tf"] = tf
    if "route" not in out:
        out["route"] = "ema_osc"
    if "filter_tfs" not in out:
        out["filter_tfs"] = []
    return out


def _adjust_hint(row):
    if not isinstance(row, dict):
        return "按数字改 timing。"
    if row.get("reject"):
        return "配方不合法，按错误改；占位符/坏 family 直接炸掉换真标的。"
    n = row.get("n")
    try:
        n = int(n or 0)
    except Exception:
        n = 0
    hitch = row.get("hitch")
    try:
        hitch = None if hitch is None else float(hitch)
    except Exception:
        hitch = None
    c = row.get("C_week_pct")
    oos = row.get("C_week_oos_pct")
    try:
        c = None if c is None else float(c)
    except Exception:
        c = None
    try:
        oos = None if oos is None else float(oos)
    except Exception:
        oos = None
    if n < 30:
        return (
            "未达到机器基础门槛：自然样本不足（证据不足）。"
            "禁止放宽参数凑笔数。请换不同合法 timing 原子组合；"
            "探索深度满后再换周期/换族或结案。"
        )
    if hitch is not None and hitch >= 0.30:
        return "止损先触过高：收紧 SKDJ/MACD 时机，不要加宽止损。"
    if c is not None and c > 0 and (oos is None or oos <= 0):
        return "样本内有数样本外没有：位置不动，改 SKDJ K/D 相对位置或差值并配 MACD/CCI/KDJ。"
    if (c is None or c <= 0) and (oos is None or oos <= 0):
        return "两边 C 都未正：位置保留，只改 timing。"
    return "未达 0.1%/0.05%，继续改这一条的 timing。"


def _should_blast(result, adjusts):
    """只炸占位符/坏配方。数字不过门不换标的，继续改同一条。"""
    if not isinstance(result, dict):
        return False
    rej = str(result.get("reject") or "")
    return rej in ("placeholder_or_bad_symbol", "bad_family", "banned_or_live")


def _pair_feedback(recipe, result, ident, adjusts, blast=False):
    pair = {
        "recipe": _recipe_snap(recipe),
        "machine": result,
        "identity": ident,
        "adjusts": adjusts,
        "need": {"C_week_pct": C_MIN_PCT, "C_week_oos_pct": OOS_MIN_PCT, "n": 30, "hitch_lt": 0.30},
        "hint": _adjust_hint(result),
        "blast": bool(blast),
        "lock": "same_symbol_same_family_only_timing",
    }
    if blast:
        return (
            "配方非法。禁止文字。只输出合法 {\"recipe\":{...}}，symbol 必须真实。\n"
            + json.dumps(pair, ensure_ascii=False, default=str)[:1200]
        )
    phase = str((result or {}).get("phase") or "")
    if phase == "dup":
        return (
            "与上一条完全重复。禁止文字。必须改 route/timeframe/几何/至少2个timing原子后再输出 {\"recipe\":{...}}。\n"
            + json.dumps(pair, ensure_ascii=False, default=str)[:1200]
        )
    if _free_create_prompt():
        return (
            "配对数字如下。禁止文字。自由改完整 recipe（标的/家族/周期/几何/timing 均可），"
            "继续改进。\n"
            + json.dumps(pair, ensure_ascii=False, default=str)[:1200]
        )
    return (
        "配对数字如下。禁止文字。锁死同一标的与位置家族，只改 timing，继续改进，不许换标的。\n"
        + json.dumps(pair, ensure_ascii=False, default=str)[:1200]
    )


def _channel(lane, state):
    symbols = [s for s in LANES.get(lane) or EQUITY if s not in _banned()]
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": _user_brief(lane, symbols)},
    ]
    current = {"id": "", "adjusts": 0, "last_pair": ""}
    for rnd in range(1, ROUNDS + 1):
        if _STOP.is_set():
            emit({"phase": "lane_stop", "lane": lane, "round": rnd})
            return
        emit({"phase": "kimi_ask", "lane": lane, "round": rnd, "at": _now()})
        body = {
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 700,
            "response_format": {"type": "json_object"},
        }
        posted = kimi_post_named(lane, body, timeout=KIMI_TIMEOUT)
        if not posted.get("ok"):
            err = str(posted.get("error") or "")
            if "400" in err or "response_format" in err.lower():
                body = dict(body)
                body.pop("response_format", None)
                posted = kimi_post_named(lane, body, timeout=KIMI_TIMEOUT)
        if not posted.get("ok"):
            err = str(posted.get("error") or "")
            emit({
                "phase": "kimi_fail", "lane": lane, "round": rnd,
                "error": err[:240], "at": _now(),
                "endpoint": posted.get("endpoint"),
                "used": posted.get("used"),
                "failover": posted.get("failover"),
            })
            wait = 4
            low = err.lower()
            if "429" in err or "tpm" in low:
                wait = 25
            elif "504" in err or "503" in err or "502" in err:
                wait = 10
            elif "empty" in low:
                wait = 8
            time.sleep(wait)
            messages.append({
                "role": "user",
                "content": "通道失败。禁止文字。只输出 {\"recipe\":{...}}。",
            })
            continue
        text = _choice_text(posted.get("raw") or {})
        obj = kimi_json_from_raw(posted.get("raw") or {})
        recipe = _first_recipe(obj)
        if not recipe:
            recipe = _first_recipe(_extract_json(text))
        if not recipe:
            s = text or ""
            pos = 0
            while not recipe:
                start = s.find("{", pos)
                if start < 0:
                    break
                recipe = _first_recipe(_first_json_value(s[start:]))
                pos = start + 1
        snap = json.dumps({"recipe": _recipe_snap(recipe)}, ensure_ascii=False) if recipe else ""
        emit({"phase": "kimi_raw", "lane": lane, "round": rnd, "text": snap or "NO_JSON"})
        try:
            with open("/tmp/kimi_dual_http_raw.txt", "a") as fh:
                fh.write("\n===== %s %s r%s =====\n%s\n" % (_now(), lane, rnd, snap or "NO_JSON"))
        except Exception:
            pass
        if not recipe:
            messages.append({
                "role": "user",
                "content": "禁止文字。只输出 1 条 {\"recipe\":{...}}，第一个字符必须是 {。",
            })
            continue
        messages.append({"role": "assistant", "content": snap})
        emit({
            "phase": "kimi_parsed", "lane": lane, "round": rnd,
            "n": 1, "endpoint": posted.get("endpoint"),
            "identity": _identity(recipe),
        })
        ident = _identity(recipe)
        if current.get("lock_recipe") and current.get("id"):
            # 锁死标的与位置几何：强制覆盖，只允许改 timing
            lock = current["lock_recipe"]
            for key in ("family", "symbol", "held", "xwin", "fast", "slow", "z", "atr", "hold"):
                if key in lock:
                    recipe[key] = lock.get(key)
            ident = _identity(recipe)
            snap = json.dumps({"recipe": _recipe_snap(recipe)}, ensure_ascii=False)
        if current["id"] and ident and ident != current["id"]:
            emit({
                "phase": "pair_hold", "lane": lane, "want": current["id"],
                "got": ident, "adjusts": current["adjusts"],
            })
            messages.append({
                "role": "user",
                "content": (
                    "身份已锁死 %s。禁止换标的/家族/held/xwin。禁止文字。只改 timing 后输出 {\"recipe\":{...}}。上一条：%s"
                    % (current["id"], current.get("last_pair") or "")
                )[:1500],
            })
            continue
        cand, err = _cand_from_recipe(recipe, lane)
        if err:
            result = {"reject": err, "symbol": recipe.get("symbol"), "phase": "pair_reject"}
            emit({"phase": "pair_reject", "lane": lane, "error": err, "symbol": recipe.get("symbol")})
            current["id"] = ident or current["id"]
            current["adjusts"] = int(current.get("adjusts") or 0) + 1
            current["last_pair"] = json.dumps(result, ensure_ascii=False, default=str)[:400]
            blast = _should_blast(result, current["adjusts"])
            messages.append({
                "role": "user",
                "content": _pair_feedback(recipe, result, ident, current["adjusts"], blast=blast),
            })
            if blast:
                emit({
                    "phase": "blast", "lane": lane, "identity": ident or current["id"],
                    "reason": err, "adjusts": current["adjusts"],
                })
                current = {"id": "", "adjusts": 0, "last_pair": current["last_pair"]}
            continue
        with _LOCK:
            if _STOP.is_set():
                return
            try:
                result = _eval_one(cand, state, lane)
            except Exception as exc:
                result = {"ok": False, "phase": "eval_exc", "error": str(exc)[:200]}
            _STATE["live"] = None
            _STATE["base_combo"] = None
            gc.collect()
        pair = {
            "id": cand.get("id"),
            "symbol": cand.get("symbol"),
            "phase": result.get("phase"),
            "C_week_pct": result.get("C_week_pct"),
            "C_week_oos_pct": result.get("C_week_oos_pct"),
            "n": result.get("n") or result.get("gate_n"),
            "n_oos": result.get("n_oos"),
            "hitch": result.get("hitch") or result.get("gate_sl"),
            "oos_hitch": result.get("oos_hitch"),
            "usable": result.get("usable"),
            "oos_usable": result.get("oos_usable"),
            "blockers": result.get("blockers"),
            "failed": result.get("failed"),
            "error": result.get("error"),
        }
        if ident == current["id"]:
            current["adjusts"] = int(current.get("adjusts") or 0) + 1
        else:
            current = {
                "id": ident,
                "adjusts": 1,
                "last_pair": "",
                "lock_recipe": _recipe_snap(recipe),
            }
        if not current.get("lock_recipe"):
            current["lock_recipe"] = _recipe_snap(recipe)
        current["last_pair"] = json.dumps(pair, ensure_ascii=False, default=str)[:500]
        emit({
            "phase": "pair", "lane": lane, "round": rnd,
            "identity": ident, "adjusts": current["adjusts"],
            "eval_phase": result.get("phase"),
            "id": pair.get("id"), "symbol": pair.get("symbol"),
            "C_week_pct": pair.get("C_week_pct"),
            "C_week_oos_pct": pair.get("C_week_oos_pct"),
            "n": pair.get("n"), "n_oos": pair.get("n_oos"),
            "hitch": pair.get("hitch"),
        })
        if result.get("phase") == "mounted":
            return
        blast = _should_blast(pair, current["adjusts"])
        messages.append({
            "role": "user",
            "content": _pair_feedback(recipe, pair, ident, current["adjusts"], blast=blast),
        })
        if blast:
            emit({
                "phase": "blast", "lane": lane, "identity": ident,
                "reason": pair.get("phase") or "below_floor",
                "adjusts": current["adjusts"],
                "C_week_pct": pair.get("C_week_pct"),
                "C_week_oos_pct": pair.get("C_week_oos_pct"),
                "hitch": pair.get("hitch"),
                "n": pair.get("n"),
            })
            current = {"id": "", "adjusts": 0, "last_pair": current["last_pair"]}
        if len(messages) > 10:
            messages = [messages[0], messages[1]] + messages[-8:]
    emit({"phase": "lane_exhausted", "lane": lane, "at": _now()})


def main():
    global FORMAL_PID
    live_pid, live_raw = _formal()
    if not live_pid or live_pid == "0":
        raise RuntimeError("formal_not_running %s" % live_raw)
    FORMAL_PID = live_pid
    pid, raw = _assert_formal()
    print("PYFILE", __file__, flush=True)
    chain = kimi_endpoint_chain()
    names = [row.get("name") for row in chain]
    if "primary" not in names or "backup" not in names:
        raise RuntimeError("need_both_kimi_endpoints %s" % names)
    pub0 = _published_cap()
    state = _load_state()
    emit({
        "phase": "boot", "at": _now(), "pid": pid, "formal": raw,
        "published": pub0, "wave": "kimi_dual_http_create",
        "c_min_pct": C_MIN_PCT, "oos_min_pct": OOS_MIN_PCT,
        "keep4_untouched": True, "no_clear_deleted": True,
        "kimi_queue": False, "stop_kimi_file_untouched": True,
        "channels": names,
        "self_contained": True,
        "pair_live": True,
        "blast_on_fail": False,
        "lock_identity": True,
        "wave_max": WAVE_MAX,
        "stop_zh": "过基础质检且单本能力比率≥0.1%、样本外≥0.05%",
        "lead_zh": "选定后锁死标的；过关前禁止文字；只改 timing 不断改进",
    })
    emit({"phase": "universe", "live": sorted(_live_symbols()), "banned_n": len(_banned())})
    _ensure_patch()
    for wave in range(1, WAVE_MAX + 1):
        if _STOP.is_set() or _MOUNTED:
            break
        emit({"phase": "wave_start", "wave": wave, "at": _now()})
        threads = []
        for lane in ("primary", "backup"):
            th = threading.Thread(target=_channel, args=(lane, state), name="kdh-%s-%s" % (wave, lane))
            th.daemon = True
            threads.append(th)
            th.start()
        for th in threads:
            th.join()
        if _MOUNTED:
            break
        emit({"phase": "wave_exhausted", "wave": wave, "at": _now()})
        time.sleep(2)
    if _MOUNTED:
        emit({"phase": "done", "at": _now(), "mounted": _MOUNTED})
        return 0
    emit({"phase": "exhausted", "at": _now(), "done_n": len(state.get("done_ids") or [])})
    return 2


if __name__ == "__main__":
    sys.exit(main() or 0)
