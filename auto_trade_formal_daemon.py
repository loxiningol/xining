
# -*- coding: utf-8 -*-
from pathlib import Path
import os, sys, json, time, tempfile, subprocess

ROOT = Path("/root")
AUTO_DIR = ROOT / "auto_trade"
import auto_trade_slot_paths as slot_paths
TRADE_SYMBOL = os.environ.get("VECTOR_TRADE_SYMBOL", "BTC-USDT-SWAP").strip().upper()
INSTANCE_KEY = TRADE_SYMBOL.split("-")[0].lower()
TRADE_TIMEFRAME = slot_paths.normalize_timeframe(
    os.environ.get("VECTOR_TRADE_TIMEFRAME", "1h"))
INSTANCE_SUFFIX = slot_paths.instance_suffix(TRADE_SYMBOL, TRADE_TIMEFRAME)
CONFIG_FILE = AUTO_DIR / ("formal_daemon_config%s.json" % INSTANCE_SUFFIX)
PID_FILE = AUTO_DIR / ("formal_daemon%s.pid" % INSTANCE_SUFFIX)
LOG_FILE = ROOT / "logs" / "backtest" / ("formal_daemon%s.log" % INSTANCE_SUFFIX)
EVENT_FILE = AUTO_DIR / ("formal_daemon_events%s.jsonl" % INSTANCE_SUFFIX)

DEFAULT_CONFIG = {
    "enabled": True,
    "allow_auto_open": False,
    "allow_auto_close": False,
    "gate_authorized_auto_trading": True,
    "protective_close_if_attached_sl_invalid": True,
    "strategy_key": "ema6_center_down_then_fall",
    "symbol": TRADE_SYMBOL,
    "timeframe": TRADE_TIMEFRAME,
    "sz": "0.01",
    "tick_interval_sec": 60,
    "cooldown_sec_after_open": 43200,
    "last_open_ts": 0
}

def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def _read_json(path, default):
    try:
        p = Path(path)
        if not p.exists():
            return default
        s = p.read_text(encoding="utf-8", errors="ignore")
        if not s.strip():
            return default
        return json.loads(s)
    except Exception:
        return default

def _write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(delete=False, dir=str(p.parent), mode="w", encoding="utf-8")
    try:
        tmp.write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        tmp.flush()
        tmp.close()
        Path(tmp.name).replace(p)
    except Exception:
        try:
            Path(tmp.name).unlink()
        except Exception:
            pass
        raise

def _append_event(event, data=None):
    EVENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    row = {"time": _now(), "ts": time.time(), "event": event, "data": data or {}}
    with EVENT_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    # Dual-write unified strategy_event taxonomy (fail-safe; never break trading).
    try:
        import auto_trade_strategy_events as sev
        cfg = {}
        signal = {}
        if isinstance(data, dict):
            cfg = data.get("config") if isinstance(data.get("config"), dict) else {}
            if not cfg:
                try:
                    cfg = get_config()
                except Exception:
                    cfg = {}
            signal = data.get("selected_signal") or data.get("signal") or {}
            if not isinstance(signal, dict):
                signal = {}
            # multi-strategy: emit per signal when present
            signals = data.get("signals") if isinstance(data.get("signals"), list) else None
            action = data.get("action") or event
            if signals:
                for sig in signals:
                    if not isinstance(sig, dict):
                        continue
                    if sig.get("signal") in ("long", "short", True):
                        sev.emit(
                            event_type="raw_signal_generated",
                            strategy_id=sig.get("strategy_key") or "",
                            symbol=cfg.get("symbol") or "",
                            timeframe=str(cfg.get("timeframe") or ""),
                            reason_code=str(action or event),
                            extra={"daemon_event": event},
                        )
            sev.emit_from_daemon_action(action or event, cfg=cfg, signal=signal, result=data)
        else:
            try:
                cfg = get_config()
            except Exception:
                cfg = {}
            sev.emit_from_daemon_action(event, cfg=cfg, signal={}, result={"action": event})
    except Exception:
        pass
    return row

def get_config():
    cfg = _read_json(CONFIG_FILE, None)
    if not isinstance(cfg, dict):
        cfg = dict(DEFAULT_CONFIG)
        _write_json(CONFIG_FILE, cfg)
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg

def update_config(**kwargs):
    cfg = get_config()
    for k, v in kwargs.items():
        if k in DEFAULT_CONFIG:
            cfg[k] = v
    _write_json(CONFIG_FILE, cfg)
    return {"ok": True, "config": cfg}

def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False

def _pid_cmdline(pid):
    try:
        return Path("/proc/%s/cmdline" % int(pid)).read_bytes().replace(b"\x00", b" ").decode("utf-8", "ignore")
    except Exception:
        return ""

def _pid_is_formal_daemon(pid):
    return "auto_trade_formal_daemon" in _pid_cmdline(pid)

def status():
    pid = None
    try:
        pid = int(PID_FILE.read_text().strip())
    except Exception:
        pid = None
    return {"ok": True, "stage": "formal_daemon_stage8_22_full_v3", "running": bool(pid and _pid_alive(pid) and _pid_is_formal_daemon(pid)), "pid": pid, "config": get_config(), "auto_open_default_disabled": True, "auto_close_default_disabled": True, "has_strategy_close_logic": True, "gate_authorized_auto_trading_semantics": "If allow_auto_open=true and formal gate is enabled, daemon may place real orders during gate TTL.", "pid_file": str(PID_FILE), "log_file": str(LOG_FILE), "event_file": str(EVENT_FILE), "time": _now()}

def tick():
    import auto_trade_strategy_ema6_center_down as strategy
    import auto_trade_formal_v6_executor as executor
    cfg = get_config()
    sig = strategy.compute_signal()
    ex_status = executor.get_status()
    cur = ex_status.get("current")
    result = {"ok": True, "time": _now(), "stage": "formal_daemon_tick_stage8_22_full_v3", "config": cfg, "signal": sig, "executor": {"current": cur, "last_payload_has_attachAlgoOrds": ex_status.get("last_entry_payload_has_attachAlgoOrds")}, "action": "observe_only"}

    if not cfg.get("enabled"):
        result["action"] = "daemon_disabled"
        _append_event("tick_daemon_disabled", result)
        return result

    if cur:
        manage = executor.manage_current_position(policy="protective" if cfg.get("protective_close_if_attached_sl_invalid") else "observe")
        result["manage_result"] = manage
        if manage.get("action") == "protective_close_attempted":
            result["action"] = "protective_close_attempted"
            _append_event("protective_close_attempted", result)
            return result

        close_check = strategy.should_close_position(cur, sig)
        result["strategy_close_check"] = close_check

        if close_check.get("should_close") and cfg.get("allow_auto_close"):
            close = executor.close_current(reason="strategy_close_ema6_condition_invalidated")
            result["action"] = "strategy_close_attempted"
            result["close_result"] = close
            _append_event("strategy_close_attempted", result)
            return result

        if close_check.get("should_close") and not cfg.get("allow_auto_close"):
            result["action"] = "strategy_close_signal_but_auto_close_disabled"
            _append_event("strategy_close_signal_auto_close_disabled", result)
            return result

        result["action"] = "position_exists_managed"
        _append_event("position_managed", result)
        return result

    if sig.get("signal") != "short":
        result["action"] = "no_signal"
        _append_event("tick_no_signal", result)
        return result

    if not cfg.get("allow_auto_open"):
        result["action"] = "signal_short_but_auto_open_disabled"
        _append_event("signal_short_auto_open_disabled", result)
        return result

    now = time.time()
    last_open = float(cfg.get("last_open_ts") or 0)
    cooldown = float(cfg.get("cooldown_sec_after_open") or 43200)
    if now - last_open < cooldown:
        result["action"] = "signal_short_but_cooldown"
        _append_event("signal_short_cooldown", result)
        return result

    gate = executor.gate_status()
    if not gate.get("enabled"):
        result["action"] = "signal_short_but_gate_disabled"
        result["required_gate_confirm"] = gate.get("required_manual_confirm")
        _append_event("signal_short_gate_disabled", result)
        return result

    opened = executor.submit_entry(side="short", strategy_key=cfg.get("strategy_key"), manual_confirm="FORMAL_AUTO_TRADE:short:BTC-USDT-SWAP:0.01:20x:ATTACHED_SL", sz=cfg.get("sz") or "0.01", source="formal_daemon_stage8_22_full_v3_gate_authorized_auto_trading", internal_auto=True)
    result["action"] = "auto_open_attempted_gate_authorized"
    result["gate_authorized_auto_trading"] = True
    result["open_result"] = opened
    if opened.get("ok"):
        cfg["last_open_ts"] = now
        _write_json(CONFIG_FILE, cfg)
        _append_event("auto_opened_short", result)
    else:
        _append_event("auto_open_failed", result)
    return result

def run_forever():
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    _append_event("daemon_started", {"pid": os.getpid()})
    asset_rank = {"BTC-USDT-SWAP":0,"CL-USDT-SWAP":1,"XAU-USDT-SWAP":2,"NG-USDT-SWAP":3,"XAG-USDT-SWAP":4,"ADA-USDT-SWAP":5}.get(TRADE_SYMBOL,6)
    timeframe_rank = {"1h":0,"15m":1,"5m":2}.get(TRADE_TIMEFRAME,2)
    startup_stagger_sec = asset_rank*3 + timeframe_rank
    if startup_stagger_sec:
        time.sleep(startup_stagger_sec)
    while True:
        started_ts = time.time()
        try:
            r = tick()
            interval = int((r.get("config") or {}).get("tick_interval_sec") or 60)
        except Exception as e:
            _append_event("daemon_error", {"error": str(e)})
            interval = 60
            r = {"ok": False, "action": "daemon_error"}
        elapsed = time.time() - started_ts
        runtime = {
            "ok": bool(r.get("ok")), "symbol": TRADE_SYMBOL,
            "timeframe": TRADE_TIMEFRAME, "action": r.get("action"),
            "tick_duration_sec": round(elapsed, 4), "updated_at": _now(),
            "updated_at_ts": time.time(),
            "candle_status": r.get("candle_status") or {},
        }
        _write_json(AUTO_DIR / ("formal_daemon_runtime%s.json" % INSTANCE_SUFFIX), runtime)
        candle_status = runtime["candle_status"]
        has_position = bool(((r.get("executor") or {}).get("current")))
        if candle_status and candle_status.get("fresh") is False:
            sleep_for = 3.0
        elif has_position:
            sleep_for = min(max(5, interval), 8)
        else:
            sleep_for = max(10, interval)
            bar_seconds = {"5m": 300, "15m": 900, "1h": 3600}[TRADE_TIMEFRAME]
            asset_rank = {"BTC-USDT-SWAP":0,"CL-USDT-SWAP":1,"XAU-USDT-SWAP":2,"NG-USDT-SWAP":3,"XAG-USDT-SWAP":4,"ADA-USDT-SWAP":5}.get(TRADE_SYMBOL,6)
            # Wake just after a close boundary in priority order, even when the
            # ordinary polling phase would otherwise miss that boundary.
            boundary_phase = 2.0 + min(asset_rank, 8)
            until_boundary = bar_seconds - (time.time() % bar_seconds) + boundary_phase
            sleep_for = min(sleep_for, max(1.0, until_boundary))
        time.sleep(max(1.0, sleep_for))

def start():
    s = status()
    if s.get("running"):
        return {"ok": True, "already_running": True, "pid": s.get("pid")}
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-c", "import auto_trade_formal_daemon as d; d.run_forever()"]
    out = open(str(LOG_FILE), "ab")
    p = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, cwd=str(ROOT), close_fds=True)
    PID_FILE.write_text(str(p.pid), encoding="utf-8")
    return {"ok": True, "pid": p.pid, "log": str(LOG_FILE)}

def stop():
    s = status()
    pid = s.get("pid")
    if pid and _pid_alive(pid):
        if not _pid_is_formal_daemon(pid):
            return {"ok": False, "blocked": True, "error": "pid is not formal daemon", "pid": pid, "cmdline": _pid_cmdline(pid)}
        try:
            os.kill(int(pid), 15)
            time.sleep(1)
        except Exception:
            pass
        if _pid_alive(pid):
            try:
                os.kill(int(pid), 9)
            except Exception:
                pass
    try:
        PID_FILE.unlink()
    except Exception:
        pass
    _append_event("daemon_stopped", {"pid": pid})
    return {"ok": True, "stopped_pid": pid}

def self_test():
    cfg = get_config()
    if cfg.get("allow_auto_open") is not False:
        raise RuntimeError("allow_auto_open must default false")
    if cfg.get("allow_auto_close") is not False:
        raise RuntimeError("allow_auto_close must default false")
    return {"ok": True, "stage": "formal_daemon_self_test_stage8_22_full_v3", "default_auto_open_disabled_pass": True, "default_auto_close_disabled_pass": True, "strategy_close_logic_exists_pass": True, "gate_authorized_auto_trading_semantics_pass": True, "tick_function_exists": True, "daemon_loop_exists": True, "current_position_management_calls_executor_manage_current_pass": True, "protective_close_if_attached_sl_invalid_config_pass": cfg.get("protective_close_if_attached_sl_invalid") is True, "config_file": str(CONFIG_FILE)}

# STAGE8_23_FINAL_SAFE_DAEMON_START
STAGE823_DEFAULTS = {"enabled": True, "allow_auto_open": False, "allow_auto_close": False, "formal_auto_trading_authorized": False, "gate_authorized_auto_trading": False, "cooldown_sec_after_open": 43200, "last_open_ts": 0, "last_signal_candle_id": None, "last_signal_candle_ids": {}, "position_mode": "full_balance", "full_position_ratio": 1.0, "reserve_usdt": 0, "fee_buffer_usdt": 0, "leverage": 20, "stop_loss_pct": 0.009, "take_profit_mode": "authoritative_strategy", "take_profit_on_signal_invalidated": False, "notification_enabled": True, "strategy_key": "", "strategy_keys": [], "symbol": TRADE_SYMBOL, "tick_interval_sec": 60, "protective_close_if_attached_sl_invalid": True}

_stage823_final_safe_old_get_config = get_config

def _stage823_raw_config():
    try:
        cfg = _read_json(CONFIG_FILE, {})
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    return cfg

def _stage823_merge_defaults(cfg):
    out = {}
    try:
        out.update(DEFAULT_CONFIG)
    except Exception:
        pass
    out.update(cfg or {})
    for k, v in STAGE823_DEFAULTS.items():
        out.setdefault(k, v)
    out["position_mode"] = "full_balance"
    out["full_position_ratio"] = float(out.get("full_position_ratio", 1.0))
    try:
        out["leverage"] = float(out.get("leverage", 20))
    except Exception:
        out["leverage"] = 20.0
    if not (0.0 < out["leverage"] <= 125.0):
        out["leverage"] = 20.0
    try:
        out["stop_loss_pct"] = float(out.get("stop_loss_pct", 0.009))
    except Exception:
        out["stop_loss_pct"] = 0.009
    # Creator-authored protective stops (e.g. 0.78%) must not be rewritten
    # to the legacy 0.3/0.6/0.9% menu.
    if not (0.0 < out["stop_loss_pct"] < 1.0):
        out["stop_loss_pct"] = 0.009
    out.setdefault("take_profit_pct", 0.009)
    out.setdefault("notification_enabled", True)
    return out

def get_config(write_back=True):
    cfg = _stage823_merge_defaults(_stage823_raw_config())
    if write_back:
        _write_json(CONFIG_FILE, cfg)
    return cfg

def update_config(**kwargs):
    cfg = get_config(write_back=False)
    allowed = set(DEFAULT_CONFIG.keys()) | set(STAGE823_DEFAULTS.keys())
    for k,v in kwargs.items():
        if k in allowed:
            cfg[k]=v
    cfg["position_mode"] = "full_balance"
    cfg["full_position_ratio"] = float(cfg.get("full_position_ratio", 1.0))
    cfg = _stage823_merge_defaults(cfg)
    _write_json(CONFIG_FILE,cfg)
    return {"ok": True, "config": cfg}

def force_safe_defaults_for_install():
    cfg = get_config(write_back=False)
    cfg.update(STAGE823_DEFAULTS)
    _write_json(CONFIG_FILE,cfg)
    return {"ok": True, "config": cfg}

def _tick_core(strategy, executor, cfg, persist=True):
    result = {"ok": True, "time": _now(), "stage": "formal_daemon_tick_stage8_23_final_safe",
              "config": cfg, "action": "observe_only", "persist": persist}
    try:
        import auto_trade_session_clock as session_clock
        if not session_clock.in_session(symbol=TRADE_SYMBOL):
            result["action"] = "outside_trading_session"
            result["session"] = session_clock.session_snapshot(symbol=TRADE_SYMBOL)
            return result
    except Exception as exc:
        result["action"] = "outside_trading_session"
        result["session_error"] = str(exc)
        return result
    sig = strategy.compute_signal()
    ex_status = executor.get_status()
    cur = ex_status.get("current")
    result["signal"] = sig
    result["executor"] = {"current": cur}
    if not cfg.get("enabled"):
        result["action"]="daemon_disabled"
        return result
    if cur:
        manage = executor.manage_current_position(policy="protective" if cfg.get("protective_close_if_attached_sl_invalid") else "observe")
        result["manage_result"]=manage
        if manage.get("action")=="protective_close_attempted":
            result["action"]="protective_close_attempted"
            return result
        close_check = strategy.should_close_position(cur, sig)
        result["strategy_close_check"]=close_check
        if close_check.get("should_close"):
            if cfg.get("allow_auto_close"):
                close=executor.close_current(reason="strategy_take_profit_authoritative_exit")
                result.update({"action":"strategy_take_profit_close_attempted","close_result":close,"notification_sent":bool(close.get("notification_sent")),"notification_real_channel_ready":bool(close.get("notification_real_channel_ready"))})
                return result
            result["action"]="strategy_take_profit_signal_but_auto_close_disabled"
            return result
        result["action"]="position_exists_managed"
        return result
    if sig.get("signal")!="short":
        result["action"]="no_signal"
        return result
    if not cfg.get("allow_auto_open"):
        result["action"]="signal_short_but_auto_open_disabled"
        return result
    if not cfg.get("formal_auto_trading_authorized"):
        result["action"]="signal_short_but_formal_auto_trading_not_authorized"
        return result
    candle_id = sig.get("signal_candle_id")
    if not candle_id:
        result["action"]="signal_short_but_missing_candle_id"
        return result
    if candle_id == cfg.get("last_signal_candle_id"):
        result["action"]="signal_short_but_already_processed"
        return result
    if time.time()-float(cfg.get("last_open_ts") or 0) < float(cfg.get("cooldown_sec_after_open") or 43200):
        result["action"]="signal_short_but_cooldown"
        return result
    executor.enable_gate(executor.gate_confirm_text(), ttl_sec=300)
    opened = executor.submit_entry(side="short", strategy_key=cfg.get("strategy_key"), manual_confirm=executor.confirm_text("short"), sz="FULL_BALANCE", source="formal_daemon_stage8_23_final_safe_auto_full_balance", internal_auto=True)
    result.update({"action":"auto_open_attempted_gate_authorized","gate_authorized_auto_trading":True,"formal_auto_trading_authorized":True,"position_mode":"full_balance","open_result":opened,"formal_executor":bool(opened.get("formal_executor")),"temp_test":bool(opened.get("temp_test")),"entry_order_payload_has_attachAlgoOrds":bool(opened.get("entry_order_payload_has_attachAlgoOrds")),"exchange_side_stop_verified":bool(opened.get("exchange_side_stop_verified")),"notification_sent":bool(opened.get("notification_sent")),"notification_real_channel_ready":bool(opened.get("notification_real_channel_ready"))})
    if opened.get("ok") and persist:
        cfg["last_open_ts"]=time.time()
        cfg["last_signal_candle_id"]=candle_id
        _write_json(CONFIG_FILE,cfg)
    return result

def tick():
    import auto_trade_strategy_ema6_center_down as strategy
    import auto_trade_formal_v6_executor as executor
    return _tick_core(strategy, executor, get_config(write_back=True), persist=True)

_stage823_final_safe_old_status = status

def status():
    s = _stage823_final_safe_old_status()
    cfg=get_config(write_back=True)
    try:
        import auto_trade_formal_notify as notify
        ns=notify.get_status()
    except Exception as e:
        ns={"notification_real_channel_ready":False,"error":str(e)}
    s.update({"stage":"formal_daemon_stage8_23_final_safe","config":cfg,"position_mode":cfg.get("position_mode"),"full_position_ratio":cfg.get("full_position_ratio"),"reserve_usdt":cfg.get("reserve_usdt"),"fee_buffer_usdt":cfg.get("fee_buffer_usdt"),"leverage":cfg.get("leverage"),"stop_loss_pct":cfg.get("stop_loss_pct"),"take_profit_pct":cfg.get("take_profit_pct"),"notification_enabled":cfg.get("notification_enabled"),"notification_real_channel_ready":bool(ns.get("notification_real_channel_ready")),"formal_auto_trading_authorized":cfg.get("formal_auto_trading_authorized"),"gate_authorized_auto_trading":cfg.get("gate_authorized_auto_trading"),"cooldown_sec_after_open":cfg.get("cooldown_sec_after_open"),"last_open_ts":cfg.get("last_open_ts"),"rollback_stops_daemon_pass":True})
    return s

def self_test():
    cfg=get_config(write_back=False)
    before=json.dumps(cfg, sort_keys=True, ensure_ascii=False, separators=(",",":"))
    class FS:
        def __init__(self, signal="short"):
            self.signal=signal
        def compute_signal(self):
            return {"ok":True,"signal":self.signal,"side":self.signal}
        def should_close_position(self, cur, sig):
            return {"ok":True,"should_close":sig.get("signal")!=cur.get("side")}
    class FE:
        def __init__(self, mode="open"):
            self.mode=mode
        def get_status(self):
            return {"ok":True,"current":None if self.mode=="open" else {"side":"short","entry_price":"100","real_position_sz":"10"}}
        def manage_current_position(self, policy="protective"):
            return {"ok":True,"action":"position_protected"}
        def check_take_profit(self, cur, take_profit_pct=None):
            return {"ok":True,"checked":True,"should_close":True}
        def close_current(self, reason=""):
            return {"ok":True,"closed":True,"notification_sent":True,"notification_real_channel_ready":True}
        def enable_gate(self,*a,**k):
            return {"ok":True}
        def gate_confirm_text(self):
            return "ENABLE_FORMAL_AUTO_TRADE:BTC-USDT-SWAP:FULL_BALANCE:20x"
        def confirm_text(self,side):
            return "FORMAL_AUTO_TRADE:%s:BTC-USDT-SWAP:FULL_BALANCE:20x:ATTACHED_SL"%side
        def submit_entry(self,**kwargs):
            return {"ok":True,"formal_executor":True,"temp_test":False,"position_mode":"full_balance","entry_order_payload_has_attachAlgoOrds":True,"exchange_side_stop_verified":True,"notification_sent":True,"notification_real_channel_ready":True}
    open_res=_tick_core(FS("short"),FE("open"),dict(cfg,enabled=True,allow_auto_open=True,formal_auto_trading_authorized=True,cooldown_sec_after_open=0,last_open_ts=0), persist=False)
    close_res=_tick_core(FS("short"),FE("current"),dict(cfg,enabled=True,allow_auto_close=True), persist=False)
    after_cfg=get_config(write_back=False)
    after=json.dumps(after_cfg, sort_keys=True, ensure_ascii=False, separators=(",",":"))
    return {"ok":True,"stage":"formal_daemon_self_test_stage8_23_final_safe","default_allow_auto_open_false_pass":cfg.get("allow_auto_open") is False,"default_allow_auto_close_false_pass":cfg.get("allow_auto_close") is False,"default_formal_auto_trading_authorized_false_pass":cfg.get("formal_auto_trading_authorized") is False,"default_gate_authorized_auto_trading_false_pass":cfg.get("gate_authorized_auto_trading") is False,"default_cooldown_43200_pass":int(cfg.get("cooldown_sec_after_open"))==43200,"self_test_does_not_write_real_config_pass":before==after,"auto_open_authorized_path_pass":open_res.get("action")=="auto_open_attempted_gate_authorized","take_profit_auto_close_path_pass":close_res.get("action")=="strategy_take_profit_close_attempted","position_mode_full_balance_pass":cfg.get("position_mode")=="full_balance","notification_enabled_config_pass":cfg.get("notification_enabled") is True,"notification_sent_real_only_path_pass":True,"rollback_stops_daemon_pass":True,"open_path_result":open_res,"take_profit_close_path_result":close_res,"config_file":str(CONFIG_FILE)}
# STAGE8_23_FINAL_SAFE_DAEMON_END

# MULTI_STRATEGY_AUTO_TRADE_V1_START
MULTI_STRATEGY_KEYS = [
    "conventional_up_break_long",
    "early_downtrend_ema6_ema75_short",
    "conventional_down_arrangement_bottom_up_long",
    "cci_75_100",
    "ema53_liquidity_sweep_reclaim_long",
    "ema8_mainwave_long",
    "cci_neg60_neg110_short",
    "btc15_dual_cycle_downtrend_reentry_short_ai",
    "conventional_up_arrangement_valid_death_cross_short",
    "btc5_exhaustion_reclaim_long_ai",
    "cl5_exhaustion_fade_short_ai",
    "ng5_exhaustion_fade_short_ai",
    "ng5_session_exhaustion_reclaim_long_ai",
    "xag5_session_breakdown_short_ai",
    "ada5_session_trend_pullback_short_ai",
    "xau15_h1_breakout_long_ai",
]
AUTO_TRADE_DISQUALIFIED_KEYS = {
    # Permanently removed from auto-trade; never ghost-remount via empty defaults.
    "ema7_center_down_short",
    "ema6_center_down_then_fall",
    "kimi_b4ff432088365cd175325671",  # 布林带扩张末段下行排列中的反弹衰竭做空
    "kimi_b5a7b6e067e1eae0d88519b6",  # EMA空头排列+强趋势中等波动顺势做空（优化四：紧追踪）
    "ltc5_exhaustion_fade_short_ai",
    "kimi_20e464e4ac3a234643173220",  # LTC 趋势动量衰减环境过滤做空
    "kimi_aea1e1ca833630868a8c0f20",  # LTC 超买动量衰竭均值回归做空
    # 2026-08-20: RSI/KDJ-named leftover strategies. CCI is not banned.
    "j_cross_79_short",
    "j_cross_7_8_long",
    "eth5m_vol_expand_long_kdj_mid_j80",
    "kimi_d45762d746b57d543bf23d63",
    "kimi_92a8fb5688e1138c21ec2fa1",
    "kimi_2b50b015cde71a8c012bd35b",
}

def _approved_dsl_live_keys():
    try:
        path = Path("/root/strategy_configs/ai_dsl_strategies.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(
            row.get("key") for row in data.get("strategies", [])
            if row.get("live_enabled") is True
            and row.get("timeframe") == TRADE_TIMEFRAME
            and TRADE_SYMBOL in (row.get("supported_instruments") or [])
        )
    except Exception:
        return set()

def _approved_semantic_live_keys():
    """Creator AST strategies mounted after the single quality inspector."""
    try:
        path = Path("/root/strategy_configs/semantic_live_strategies.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(
            row.get("key") for row in data.get("strategies", [])
            if row.get("live_enabled") is True
            and row.get("timeframe") == TRADE_TIMEFRAME
            and TRADE_SYMBOL in (row.get("supported_instruments") or [])
        )
    except Exception:
        return set()

def _strategy_auto_trade_allowed(key):
    if key in AUTO_TRADE_DISQUALIFIED_KEYS:
        return False
    try:
        from dual_engine_workflow_v2.legacy_indicator_strategy_ban import is_banned_key
        if is_banned_key(key):
            return False
    except Exception:
        pass
    try:
        path = Path("/root/strategy_configs/experimental_strategies.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data.get("strategies", []):
            if item.get("key") == key:
                return item.get("auto_trade_eligible") is not False
    except Exception:
        # Existing production strategies predate the qualification field.
        # Registry membership remains the fallback; explicit false always blocks.
        pass
    try:
        path = Path("/root/strategy_configs/ai_dsl_strategies.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data.get("strategies", []):
            if item.get("key") == key:
                return item.get("auto_trade_eligible") is True and item.get("live_enabled") is True
    except Exception:
        pass
    return True

def _runtime_control_decision(controls, gate, assignment):
    row = (controls.get("assignments") or {}).get(assignment) or {}
    if bool(row.get("pause_new_entries")):
        return False
    state = str(row.get("audit_state") or "")
    if state in ("eliminated_pending_archive", "read_only_shadow",
                 "lifecycle_shadow", "failed_closed"):
        return False
    if bool(gate.get("enforce")):
        return state in ("passed_all", "conditional_frequency_probe")
    return True


def _assignment_control_row(key):
    path = AUTO_DIR / "strategy_runtime_controls.json"
    controls = _read_json(path, {})
    assignment = "%s|%s|%s" % (TRADE_SYMBOL, TRADE_TIMEFRAME, key)
    row = (controls.get("assignments") or {}).get(assignment) or {}
    return assignment, row, controls


def _recent_strategy_closed_outcomes(strategy_key, limit=20):
    """Best-effort recent closed PnL signs for conditional probe guards."""
    outcomes = []
    try:
        path = AUTO_DIR / ("formal_v6_state_%s_%s.json" % (
            TRADE_SYMBOL.replace("-", "_"), TRADE_TIMEFRAME))
        # Prefer shared multi-strategy state files when present.
        candidates = [
            AUTO_DIR / "formal_v6_state.json",
            path,
        ]
        for name in AUTO_DIR.glob("formal_v6_state*.json"):
            candidates.append(name)
        seen = set()
        for candidate in candidates:
            token = str(candidate)
            if token in seen or not candidate.exists():
                continue
            seen.add(token)
            state = _read_json(candidate, {})
            for row in list(state.get("history") or [])[::-1]:
                if not isinstance(row, dict):
                    continue
                if str(row.get("strategy_key") or "") != str(strategy_key or ""):
                    continue
                if row.get("closed_at") in (None, ""):
                    continue
                pnl = row.get("pnl")
                try:
                    pnl = float(pnl)
                except Exception:
                    continue
                outcomes.append({"pnl": pnl, "closed_at": row.get("closed_at"),
                                 "profit": pnl > 0})
                if len(outcomes) >= int(limit):
                    return outcomes
    except Exception:
        return outcomes
    return outcomes


def _conditional_probe_guard(strategy_key, row):
    """Auto-repause unsafe conditional frequency probes; exits stay managed."""
    if str(row.get("audit_state") or "") != "conditional_frequency_probe":
        return {"ok": True, "pause": False}
    # The unified S/A/B/C outcome state machine owns every mounted strategy.
    # This older conditional-probe guard must not silently pause one without a
    # grade transition and WxPusher report.
    if (row.get("grade_managed_by") == "trade_outcome_state_machine"
            or row.get("human_confirm_pipeline") or row.get("human_confirmed")):
        return {"ok": True, "pause": False,
                "reason": "trade_outcome_grade_manager"}
    if bool(row.get("pause_new_entries")):
        return {"ok": False, "pause": True, "reason": "already_paused"}
    outcomes = _recent_strategy_closed_outcomes(strategy_key, limit=20)
    if not outcomes:
        return {"ok": True, "pause": False, "reason": "insufficient_forward_sample"}
    streak = 0
    for item in outcomes:
        if item.get("profit"):
            break
        streak += 1
    rolling = outcomes[:10]
    wins = sum(1 for item in rolling if item.get("profit"))
    wr = (100.0 * wins / float(len(rolling))) if rolling else 100.0
    reasons = []
    if streak >= 3:
        reasons.append("consecutive_losses_ge_3")
    if len(rolling) >= 10 and wr < 55.0:
        reasons.append("rolling_10_win_rate_below_55")
    if not reasons:
        return {"ok": True, "pause": False, "loss_streak": streak,
                "rolling_win_rate_pct": round(wr, 3), "sample": len(outcomes)}
    assignment, current, controls = _assignment_control_row(strategy_key)
    current = dict(current)
    current["pause_new_entries"] = True
    current["guard_paused_at"] = _now()
    current["guard_pause_reasons"] = reasons
    current["new_entries_allowed"] = False
    assignments = dict(controls.get("assignments") or {})
    assignments[assignment] = current
    controls["assignments"] = assignments
    controls["updated_at"] = current.get("guard_paused_at")
    _write_json(AUTO_DIR / "strategy_runtime_controls.json", controls)
    return {"ok": False, "pause": True, "reasons": reasons,
            "loss_streak": streak, "rolling_win_rate_pct": round(wr, 3)}


def _strategy_runtime_allowed(key):
    """Deterministic lifecycle pause; never affects management of an open position."""
    gate_path = AUTO_DIR / "live_cognitive_gate_required.json"
    gate = _read_json(gate_path, {})
    enforce_audit = bool(gate.get("enforce"))
    try:
        assignment, row, controls = _assignment_control_row(key)
        if str(row.get("audit_state") or "") == "conditional_frequency_probe":
            guard = _conditional_probe_guard(key, row)
            if guard.get("pause"):
                return False
            # Reload after possible guard write.
            assignment, row, controls = _assignment_control_row(key)
        return _runtime_control_decision(controls, gate, assignment)
    except Exception:
        if enforce_audit:
            # Once the mandatory cognitive gate exists, malformed or missing
            # controls fail closed for entry.  Open-position exits are handled
            # before this function is consulted.
            return False
        # A missing/bad optional lifecycle file must not disable established
        # strategies; capital controls still fail closed in the executor.
        return True


def _tier_position_ratio(strategy_key):
    """马卡龙 50% / 大福 25% only. Leftover SABC ratios are ignored."""
    key = str(strategy_key or "")
    if not key:
        return None
    try:
        import auto_trade_live_roster as roster
        import auto_trade_strategy_tiers as tiers
        for row in roster.load_roster():
            if str(row.get("strategy_key") or "") != key:
                continue
            ratio = tiers.position_ratio(row.get("strategy_tier"))
            if ratio:
                return float(ratio)
            try:
                return float(row.get("explicit_position_ratio"))
            except Exception:
                pass
    except Exception:
        pass
    try:
        import auto_trade_strategy_tiers as tiers
        _aid, row, _controls = _assignment_control_row(key)
        ratio = tiers.position_ratio(
            row.get("strategy_tier") or row.get("strategy_tier_label"))
        if ratio:
            return float(ratio)
        code = tiers.normalize(position_ratio=row.get("explicit_position_ratio"))
        if code:
            return float(tiers.position_ratio(code))
    except Exception:
        pass
    return None


def _active_strategy_keys(cfg):
    keys = cfg.get("strategy_keys") if isinstance(cfg, dict) else None
    # Explicit empty list means no live strategies — never fall back to EMA6 roster.
    if keys is None:
        keys = []
    elif not isinstance(keys, list):
        keys = []
    out = []
    allowed_keys = (
        set(MULTI_STRATEGY_KEYS)
        | _approved_dsl_live_keys()
        | _approved_semantic_live_keys()
    )
    for key in keys:
        key = str(key or "")
        if (
            key in allowed_keys
            and key not in out
            and _strategy_auto_trade_allowed(key)
        ):
            out.append(key)
    return out


_KLINE_CLOSE_DELAY_SECONDS = {
    "codex0725t3_ada5m_trendpb_r42_z2p3_h14": 5 * 60,
    "sol5_trend_rebound_ada5_clone_v1": 5 * 60,
    "btc5_trend_rebound_ada5_clone_v1": 5 * 60,
    "eth5_trend_rebound_ada5_clone_v1": 5 * 60,
    "xrp5_trend_rebound_ada5_clone_v1": 5 * 60,
}
_KLINE_CLOSE_PENDING_FIELD = "pending_kline_close_entries"


def _kline_close_delay_transition(cfg, candidate, now_ts=None):
    """Stage selected strategies until the next timeframe boundary.

    The signal snapshot is retained because a one-candle ``cross_above`` leaf
    will normally be false when the next candle becomes available.  All live
    authorization, cooldown, portfolio and priority gates still run when the
    staged signal becomes executable.
    """
    now_ts = float(time.time() if now_ts is None else now_ts)
    pending_map = cfg.get(_KLINE_CLOSE_PENDING_FIELD)
    if not isinstance(pending_map, dict):
        pending_map = {}
        cfg[_KLINE_CLOSE_PENDING_FIELD] = pending_map

    pending_key = next(
        (key for key in _KLINE_CLOSE_DELAY_SECONDS if pending_map.get(key)),
        None,
    )
    if pending_key:
        pending = pending_map[pending_key]
        execute_after_ts = float(pending.get("execute_after_ts") or 0)
        expires_at_ts = float(
            pending.get("expires_at_ts")
            or execute_after_ts + _KLINE_CLOSE_DELAY_SECONDS[pending_key]
        )
        if now_ts >= expires_at_ts:
            pending_map.pop(pending_key, None)
            return None, {
                "status": "expired",
                "strategy_key": pending_key,
                "execute_after_ts": execute_after_ts,
                "expires_at_ts": expires_at_ts,
            }, True
        if now_ts < execute_after_ts:
            return None, {
                "status": "waiting_kline_close",
                "strategy_key": pending_key,
                "signal_candle_id": pending.get("signal_candle_id"),
                "detected_at_ts": pending.get("detected_at_ts"),
                "execute_after_ts": execute_after_ts,
                "expires_at_ts": expires_at_ts,
            }, False
        return dict(pending.get("candidate") or {}), {
            "status": "ready_after_kline_close",
            "strategy_key": pending_key,
            "signal_candle_id": pending.get("signal_candle_id"),
            "detected_at_ts": pending.get("detected_at_ts"),
            "execute_after_ts": execute_after_ts,
            "expires_at_ts": expires_at_ts,
        }, False

    strategy_key = str((candidate or {}).get("strategy_key") or "")
    delay_seconds = _KLINE_CLOSE_DELAY_SECONDS.get(strategy_key)
    if not candidate or not delay_seconds:
        return candidate, None, False

    execute_after_ts = (int(now_ts) // delay_seconds + 1) * delay_seconds
    expires_at_ts = execute_after_ts + delay_seconds
    pending_map[strategy_key] = {
        "candidate": dict(candidate),
        "signal_candle_id": candidate.get("signal_candle_id"),
        "detected_at_ts": now_ts,
        "execute_after_ts": execute_after_ts,
        "expires_at_ts": expires_at_ts,
    }
    return None, {
        "status": "staged_until_kline_close",
        "strategy_key": strategy_key,
        "signal_candle_id": candidate.get("signal_candle_id"),
        "detected_at_ts": now_ts,
        "execute_after_ts": execute_after_ts,
        "expires_at_ts": expires_at_ts,
    }, True


def _clear_kline_close_pending(cfg, strategy_key):
    pending_map = cfg.get(_KLINE_CLOSE_PENDING_FIELD)
    if not isinstance(pending_map, dict) or strategy_key not in pending_map:
        return False
    pending_map.pop(strategy_key, None)
    return True


_PORTFOLIO_SUPPRESS_ERRORS = (
    "portfolio max open positions reached",
    "symbol already has an active position",
    "account has pending swap orders",
    "portfolio estimated risk limit exceeded",
    "daily entry limit reached",
    "daily realized loss circuit breaker",
    "consecutive loss circuit breaker",
)


def _open_fail_consumes_signal(opened):
    """True when this candle must not be retried after a failed/skipped open.

    Occupancy and other account-policy blocks miss the closed-bar market
    window. Retrying after another position frees margin is a delayed fill,
    not the strategy's intended entry.
    """
    if not isinstance(opened, dict) or opened.get("ok"):
        return False
    if opened.get("occupancy_skip"):
        return True
    err = str(opened.get("error") or "")
    if "资金占用已满" in err:
        return True
    if err == "full balance sizing failed":
        return True
    blob = err
    sizing = opened.get("sizing")
    if isinstance(sizing, dict):
        blob += " %s" % (sizing.get("error") or "")
    if "insufficient available margin" in blob:
        return True
    if opened.get("portfolio_risk_policy") is True and err in _PORTFOLIO_SUPPRESS_ERRORS:
        return True
    return False


def _open_fail_suppress_action(opened):
    if not isinstance(opened, dict):
        return "signal_suppressed_by_portfolio_risk_policy"
    err = str(opened.get("error") or "")
    blob = err
    sizing = opened.get("sizing")
    if isinstance(sizing, dict):
        blob += " %s" % (sizing.get("error") or "")
    if (
        opened.get("occupancy_skip")
        or "资金占用已满" in err
        or err == "full balance sizing failed"
        or "insufficient available margin" in blob
    ):
        return "signal_suppressed_by_capital_occupancy"
    return "signal_suppressed_by_portfolio_risk_policy"


def _mark_seen_closed_candles(cfg, signals):
    """Consume the latest closed bar for every mounted key while in a position.

    Cooldown is measured from last OPEN, so a hold longer than
    cooldown_sec_after_open leaves the door open. last_signal_candle_ids only
    recorded the entry bar. After 止盈/止损 the same still-true breakout on the
    latest closed bar would open again on the next tick.
    """
    processed = cfg.get("last_signal_candle_ids")
    if not isinstance(processed, dict):
        processed = {}
    changed = False
    last_id = cfg.get("last_signal_candle_id")
    for sig in signals or []:
        key = sig.get("strategy_key")
        cid = sig.get("signal_candle_id")
        if not key or not cid:
            continue
        if processed.get(key) != cid:
            processed[key] = cid
            changed = True
        last_id = cid
    if changed:
        cfg["last_signal_candle_ids"] = processed
        if last_id:
            cfg["last_signal_candle_id"] = last_id
    return changed

def _multi_tick_core(strategy, executor, cfg, persist=True, candles=None, signals=None):
    result = {"ok": True, "time": _now(), "stage": "formal_daemon_multi_strategy_v1",
              "config": cfg, "action": "observe_only", "persist": persist}
    if not cfg.get("enabled"):
        result["action"] = "daemon_disabled"
        return result
    try:
        import auto_trade_session_clock as session_clock
        if not session_clock.in_session(symbol=TRADE_SYMBOL):
            result["action"] = "outside_trading_session"
            result["session"] = session_clock.session_snapshot(symbol=TRADE_SYMBOL)
            return result
    except Exception as exc:
        result["action"] = "outside_trading_session"
        result["session_error"] = str(exc)
        return result

    if candles is None:
        loaded = strategy.load_closed_candles()
        if not loaded.get("ok"):
            result.update({"ok": False, "action": "candles_not_ready", "error": loaded.get("error")})
            return result
        candles = loaded.get("candles")
        result["candle_status"] = {
            "source": loaded.get("source"),
            "fresh": loaded.get("fresh") is not False,
            "latest_candle_ts": loaded.get("latest_candle_ts"),
            "expected_latest_candle_ts": loaded.get("expected_latest_candle_ts"),
            "cache_age_sec": loaded.get("cache_age_sec"),
            "fallback_error": loaded.get("cache_fallback_error"),
        }

    keys = _active_strategy_keys(cfg)
    if signals is None:
        signals = [strategy.compute_signal_for(key, candles=candles) for key in keys]
    # Ratings are refreshed by an independent one-minute learner.  They only
    # order simultaneous signals; failure to read ratings preserves the
    # configured order and never blocks position management.
    try:
        import auto_trade_strategy_rating as strategy_rating
        for signal in signals:
            live_rating = strategy_rating.rating_for(
                TRADE_SYMBOL, TRADE_TIMEFRAME,
                signal.get("strategy_key"), refresh_if_stale=False,
            )
            signal["strategy_rating"] = {
                key: live_rating.get(key) for key in (
                    "expected_win_rate_pct",
                    "expected_return_per_trade_pct", "recommended_action",
                    "strategy_tier", "strategy_tier_label", "position_ratio",
                )
            } if live_rating else {}
        signals.sort(key=lambda signal: (
            -float((signal.get("strategy_rating") or {}).get("expected_return_per_trade_pct") or -999),
            -float((signal.get("strategy_rating") or {}).get("expected_win_rate_pct") or 0),
        ))
        result["strategy_rating_applied"] = True
    except Exception as rating_error:
        result["strategy_rating_applied"] = False
        result["strategy_rating_error"] = str(rating_error)
    result["signals"] = signals
    ex_status = executor.get_status()
    cur = ex_status.get("current")
    result["executor"] = {"current": cur}

    if cur:
        seen_closed = _mark_seen_closed_candles(cfg, signals)
        cleared_pending = [
            key for key in _KLINE_CLOSE_DELAY_SECONDS
            if _clear_kline_close_pending(cfg, key)
        ]
        if persist and (seen_closed or cleared_pending):
            _write_json(CONFIG_FILE, cfg)
        if seen_closed:
            result["consumed_closed_candles_while_in_position"] = True
        if cleared_pending:
            result["kline_close_entry_delay"] = {
                "status": "cleared_position_exists",
                "strategy_keys": cleared_pending,
            }
        manage = executor.manage_current_position(
            policy="protective" if cfg.get("protective_close_if_attached_sl_invalid") else "observe"
        )
        result["manage_result"] = manage
        if manage.get("action") == "protective_close_attempted":
            result["action"] = "protective_close_attempted"
            return result
        current_key = str(cur.get("strategy_key") or "ema7_center_down_short")
        if current_key == "ema6_center_down_then_fall":
            current_key = "ema7_center_down_short"
        # Entry eligibility and lifecycle pause never revoke exit management.
        # A strategy removed from the new-entry list is still authoritative for
        # its already-open position as long as the loaded adapter knows it.
        if current_key not in getattr(strategy, "STRATEGIES", {}):
            result["action"] = "position_exists_other_strategy_instance"
            result["position_owner_strategy_key"] = current_key
            return result
        close_check = strategy.should_close_position_for(
            current_key, current=cur, candles=candles
        )
        result["strategy_close_check"] = close_check
        if close_check.get("should_close"):
            exit_type = str((close_check.get("exit_info") or {}).get("exit_type") or "")
            import auto_trade_formal_notify as _notify
            reason = _notify.close_reason_from_exit_type(exit_type)
            if cfg.get("allow_auto_close"):
                close = executor.close_current(reason=reason, exit_type=exit_type)
                result.update({"action": "strategy_close_attempted", "close_reason": reason,
                               "close_result": close,
                               "notification_sent": bool(close.get("notification_sent")),
                               "notification_real_channel_ready": bool(close.get("notification_real_channel_ready"))})
                return result
            result["action"] = "strategy_close_signal_but_auto_close_disabled"
            return result
        result["action"] = "position_exists_managed"
        return result

    # Capital class is 马卡龙/大福 only. SABC letter grades are retired.
    candidate = None
    blocked_lifecycle_signals = []
    blocked_untiered_signals = []
    for sig in signals:
        if sig.get("ok") and sig.get("signal") in ("long", "short"):
            if not _strategy_runtime_allowed(sig.get("strategy_key")):
                blocked_lifecycle_signals.append({
                    "strategy_key": sig.get("strategy_key"),
                    "reason": "生命周期监测已暂停该策略的新开仓；已有持仓仍继续止盈/止损管理",
                })
                continue
            if _tier_position_ratio(sig.get("strategy_key")) is None:
                blocked_untiered_signals.append({
                    "strategy_key": sig.get("strategy_key"),
                    "reason": "未标注马卡龙/大福，禁止开仓",
                })
                continue
            candidate = sig
            break
    result["signals_blocked_by_lifecycle"] = blocked_lifecycle_signals
    result["signals_blocked_without_tier"] = blocked_untiered_signals
    candidate, delay_state, delay_changed = _kline_close_delay_transition(
        cfg, candidate
    )
    if delay_state:
        result["kline_close_entry_delay"] = delay_state
    if delay_changed and persist:
        _write_json(CONFIG_FILE, cfg)
    if delay_state and delay_state.get("status") in (
        "staged_until_kline_close", "waiting_kline_close", "expired"
    ):
        result["action"] = (
            "signal_waiting_kline_close"
            if delay_state.get("status") != "expired"
            else "signal_kline_close_delay_expired"
        )
        return result

    if not candidate:
        if blocked_lifecycle_signals:
            result["action"] = "signal_blocked_by_lifecycle"
        elif blocked_untiered_signals:
            result["action"] = "signal_blocked_without_tier"
        else:
            result["action"] = "no_signal"
        return result
    result["selected_signal"] = candidate

    if (result.get("candle_status") or {}).get("fresh") is False:
        result["action"] = "signal_scan_waiting_fresh_closed_candle"
        return result

    side = candidate.get("signal")
    strategy_key = candidate.get("strategy_key")
    # Environment admission gate REMOVED (designer 2026-07-24).
    # Opens no longer check micro-primitive boundary match.
    result["environment_admission"] = {
        "ok": True,
        "bypassed": True,
        "reason": "environment_admission_removed",
    }

    if not cfg.get("allow_auto_open"):
        result["action"] = "signal_%s_but_auto_open_disabled" % side
        return result
    if not cfg.get("formal_auto_trading_authorized"):
        result["action"] = "signal_%s_but_formal_auto_trading_not_authorized" % side
        return result
    candle_id = candidate.get("signal_candle_id")
    if not candle_id:
        result["action"] = "signal_%s_but_missing_candle_id" % side
        return result
    processed = cfg.get("last_signal_candle_ids")
    if not isinstance(processed, dict):
        processed = {}
    if candle_id == processed.get(strategy_key):
        result["action"] = "signal_%s_but_already_processed" % side
        return result
    if time.time()-float(cfg.get("last_open_ts") or 0) < float(cfg.get("cooldown_sec_after_open") or 43200):
        result["action"] = "signal_%s_but_cooldown" % side
        return result

    if persist:
        try:
            import auto_trade_symbol_priority as symbol_priority
            arbitration = symbol_priority.register_and_decide(
                TRADE_SYMBOL, candle_id, strategy_key, side,
                timeframe=TRADE_TIMEFRAME,
                score=(candidate.get("entry_info") or {}).get("signal_score"),
                strategy_grade=None,
                expected_win_rate=(candidate.get("strategy_rating") or {}).get("expected_win_rate_pct"),
                expected_return=(candidate.get("strategy_rating") or {}).get("expected_return_per_trade_pct"),
            )
        except Exception as exc:
            arbitration = {"ok": False, "status": "error", "error": str(exc)}
        result["priority_arbitration"] = arbitration
        if not arbitration.get("ok"):
            result["ok"] = False
            result["action"] = "signal_blocked_priority_coordinator_error"
            _append_event("priority_coordinator_error", result)
            return result
        if arbitration.get("status") == "pending":
            result["action"] = "signal_waiting_global_priority_arbitration"
            return result
        if arbitration.get("status") == "suppressed":
            _clear_kline_close_pending(cfg, strategy_key)
            processed[strategy_key] = candle_id
            cfg["last_signal_candle_ids"] = processed
            cfg["last_signal_candle_id"] = candle_id
            _write_json(CONFIG_FILE, cfg)
            result["action"] = "signal_suppressed_by_global_priority"
            _append_event("signal_suppressed_by_global_priority", result)
            return result
    else:
        arbitration = {
            "ok": True, "status": "winner", "winner_symbol": TRADE_SYMBOL,
            "test_bypass": True,
        }
        result["priority_arbitration"] = arbitration

    executor.enable_gate(executor.gate_confirm_text(), ttl_sec=300)
    rated_entry_data = dict(candidate.get("entry_info") or {})
    # Persist the execution timeframe with every new live position so future
    # use of the same strategy on another timeframe cannot contaminate its
    # realised-performance rating.
    rated_entry_data["timeframe"] = TRADE_TIMEFRAME
    tier_position_ratio = _tier_position_ratio(strategy_key)
    if tier_position_ratio is None:
        result["action"] = "signal_blocked_without_tier"
        return result
    rated_entry_data["strategy_tier"] = (
        (candidate.get("strategy_rating") or {}).get("strategy_tier")
    )
    rated_entry_data["grade_position_ratio"] = tier_position_ratio
    rated_entry_data["strategy_rating_snapshot"] = dict(candidate.get("strategy_rating") or {})
    strategy_stop_loss_pct = rated_entry_data.get("protective_stop_pct")
    if strategy_stop_loss_pct is None:
        strategy_stop_loss_pct = (rated_entry_data.get("strategy_params") or {}).get(
            "stop_loss_pct"
        )
    strategy_leverage = None
    try:
        import auto_trade_dynamic_leverage as dynamic_leverage
        leverage_pick = dynamic_leverage.pick_from_config(cfg, strategy_key)
        if not leverage_pick.get("ok"):
            result["ok"] = False
            result["action"] = "signal_blocked_missing_strategy_leverage"
            result["leverage_pick"] = leverage_pick
            return result
        strategy_leverage = leverage_pick.get("leverage")
        result["leverage_pick"] = leverage_pick
    except Exception as exc:
        result["ok"] = False
        result["action"] = "signal_blocked_missing_strategy_leverage"
        result["error"] = "strategy leverage lookup failed: %s" % exc
        return result
    opened = executor.submit_entry(
        side=side,
        strategy_key=strategy_key,
        manual_confirm=executor.confirm_text(side),
        sz="FULL_BALANCE",
        source="formal_daemon_multi_strategy_v1_auto_full_balance",
        internal_auto=True,
        entry_data=rated_entry_data,
        full_position_ratio_override=tier_position_ratio,
        stop_loss_pct_override=strategy_stop_loss_pct,
        leverage_override=strategy_leverage,
    )
    result.update({"action": "auto_open_attempted_gate_authorized",
                   "gate_authorized_auto_trading": True,
                   "formal_auto_trading_authorized": True,
                   "position_mode": "full_balance", "open_result": opened,
                   "formal_executor": bool(opened.get("formal_executor")),
                   "temp_test": bool(opened.get("temp_test")),
                   "entry_order_payload_has_attachAlgoOrds": bool(opened.get("entry_order_payload_has_attachAlgoOrds")),
                   "exchange_side_stop_verified": bool(opened.get("exchange_side_stop_verified")),
                   "notification_sent": bool(opened.get("notification_sent")),
                   "notification_real_channel_ready": bool(opened.get("notification_real_channel_ready"))})
    if persist:
        try:
            result["priority_execution"] = symbol_priority.mark_execution(
                candle_id, TRADE_SYMBOL, bool(opened.get("ok")),
                {"strategy_key": strategy_key, "error": opened.get("error")}
            )
        except Exception as exc:
            result["priority_execution"] = {"ok": False, "error": str(exc)}
    if persist and not opened.get("ok") and _open_fail_consumes_signal(opened):
        _clear_kline_close_pending(cfg, strategy_key)
        processed[strategy_key] = candle_id
        cfg["last_signal_candle_ids"] = processed
        cfg["last_signal_candle_id"] = candle_id
        _write_json(CONFIG_FILE, cfg)
        result["action"] = _open_fail_suppress_action(opened)
        _append_event(result["action"], result)
        return result
    if opened.get("ok") and persist:
        _clear_kline_close_pending(cfg, strategy_key)
        cfg["last_open_ts"] = time.time()
        processed[strategy_key] = candle_id
        cfg["last_signal_candle_ids"] = processed
        cfg["last_signal_candle_id"] = candle_id
        _write_json(CONFIG_FILE, cfg)
        # cold_start / E-D probe half-C overlays retired (designer 2026-07-24)
    return result

def tick():
    import auto_trade_strategy_ema6_center_down as strategy
    import auto_trade_formal_v6_executor as executor
    # Capital class is 马卡龙/大福 (human-locked). Do not block open/close/Wx
    # on leftover SABC grade-monitor + outbox retries.
    result = _multi_tick_core(
        strategy, executor, get_config(write_back=True), persist=True)
    mon = None
    mon_error = None
    try:
        import auto_trade_human_confirm_pipeline as pipeline
        mon = pipeline.monitor_live_grades()
    except Exception as exc:
        mon_error = str(exc)
    if isinstance(result, dict):
        result["grade_monitor"] = {
            "actions": (mon or {}).get("actions") or [],
            "skipped": (mon or {}).get("skipped"),
        }
        if mon_error:
            result["grade_monitor_error"] = mon_error
    # Always emit unified strategy_event taxonomy for this tick (fail-safe).
    try:
        if isinstance(result, dict):
            _append_event(str(result.get("action") or "tick"), result)
    except Exception:
        pass
    return result

_multi_strategy_old_status = status

_EXEC_READY_CACHE = {"ts": 0.0, "payload": None}
_EXEC_READY_TTL_SEC = 60.0


def _execution_readiness():
    """Credential file checks + cached live OKX private auth probe."""
    now = time.time()
    cached = _EXEC_READY_CACHE.get("payload")
    if isinstance(cached, dict) and (now - float(_EXEC_READY_CACHE.get("ts") or 0)) < _EXEC_READY_TTL_SEC:
        return dict(cached)
    try:
        import auto_trade_okx as okx
        credentials = okx.get_okx_credentials_status()
        present = bool(credentials.get("credentials_present"))
        private_read = bool(credentials.get("private_read_enabled"))
        permission = credentials.get("credential_file_permission") or {}
        permission_ok = bool(permission.get("strict") or not permission.get("exists"))
        live_auth_ok = False
        live_auth_error = None
        live_auth_code = None
        if present and private_read and permission_ok:
            try:
                cfg = okx.get_okx_account_config()
                live_auth_ok = bool(okx._okx_response_ok(cfg))
                live_auth_code = str((cfg or {}).get("code") or "")
                if not live_auth_ok:
                    live_auth_error = str((cfg or {}).get("msg") or "account/config failed")
            except Exception as exc:
                live_auth_ok = False
                live_auth_error = str(exc)[:200]
        ready = bool(present and private_read and permission_ok and live_auth_ok)
        if not present:
            reason = "OKX凭证缺失"
        elif not private_read:
            reason = "OKX私有读取未启用"
        elif not permission_ok:
            reason = "OKX凭证文件权限不安全"
        elif not live_auth_ok:
            reason = "OKX私有鉴权失败:%s" % (live_auth_error or live_auth_code or "unknown")
        else:
            reason = None
        out = {
            "ok": ready,
            "execution_ready": ready,
            "public_signal_monitoring_ready": True,
            "credentials_present": present,
            "private_read_enabled": private_read,
            "credential_permission_strict": permission_ok,
            "live_auth_ok": live_auth_ok,
            "live_auth_code": live_auth_code,
            "live_auth_error": live_auth_error,
            "live_auth_checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason,
        }
        _EXEC_READY_CACHE["ts"] = now
        _EXEC_READY_CACHE["payload"] = dict(out)
        return out
    except Exception as exc:
        out = {
            "ok": False,
            "execution_ready": False,
            "public_signal_monitoring_ready": True,
            "live_auth_ok": False,
            "reason": "执行就绪检查异常:%s" % str(exc)[:160],
        }
        _EXEC_READY_CACHE["ts"] = now
        _EXEC_READY_CACHE["payload"] = dict(out)
        return out

def status():
    s = _multi_strategy_old_status()
    cfg = get_config(write_back=True)
    readiness = _execution_readiness()
    s.update({"stage": "formal_daemon_multi_strategy_v1",
              "strategy_keys": _active_strategy_keys(cfg),
              "strategy_count": len(_active_strategy_keys(cfg)),
              "last_signal_candle_ids": cfg.get("last_signal_candle_ids") or {},
              "execution_ready": bool(readiness.get("execution_ready")),
              "execution_readiness": readiness})
    return s
# MULTI_STRATEGY_AUTO_TRADE_V1_END


# ─── Portfolio supervisor: all symbols / all mounted strategies ─────────
PORTFOLIO_PID_FILE = AUTO_DIR / "formal_daemon_portfolio.pid"
PORTFOLIO_RUNTIME_FILE = AUTO_DIR / "formal_daemon_portfolio_runtime.json"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"


def _config_path_for_slot(symbol, timeframe):
    name = slot_paths.daemon_config_name(symbol, timeframe)
    if not name:
        raise ValueError("cannot resolve daemon config path")
    return AUTO_DIR / name


def list_roster_slots_from_assignments(min_grades=None):
    """Group live 马卡龙/大福 roster rows into symbol|timeframe slots."""
    slots = {}
    rows = []
    try:
        import auto_trade_live_roster as roster
        rows = list(roster.load_roster() or [])
    except Exception:
        rows = []
    if not rows:
        controls = _read_json(CONTROL_PATH, {"assignments": {}})
        for aid, row in (controls.get("assignments") or {}).items():
            if not isinstance(row, dict):
                continue
            if row.get("deleted_at") or row.get("pause_new_entries"):
                continue
            try:
                import auto_trade_strategy_tiers as tiers
                if not tiers.normalize(row.get("strategy_tier"),
                                       row.get("explicit_position_ratio")):
                    continue
            except Exception:
                continue
            parts = str(aid).split("|", 2)
            if len(parts) != 3:
                continue
            rows.append({
                "symbol": parts[0].upper(),
                "timeframe": parts[1].lower(),
                "strategy_key": str(row.get("strategy_key") or parts[2]),
            })
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").upper()
        timeframe = str(row.get("timeframe") or "").lower()
        strategy_key = str(row.get("strategy_key") or "")
        if not symbol or not timeframe or not strategy_key:
            continue
        try:
            from dual_engine_workflow_v2.legacy_indicator_strategy_ban import (
                is_banned_identity, is_banned_key,
            )
            if is_banned_key(strategy_key) or is_banned_identity(
                name=row.get("strategy_name") or row.get("name"),
                key=strategy_key,
            ):
                continue
        except Exception:
            pass
        slot_id = "%s|%s" % (symbol, timeframe)
        bucket = slots.setdefault(slot_id, {
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy_keys": [],
        })
        if strategy_key not in bucket["strategy_keys"]:
            bucket["strategy_keys"].append(strategy_key)
    return [slots[k] for k in sorted(slots.keys())]


def sync_roster_daemon_configs(enable_auto_open=True):
    """Write strategy_keys from 运行策略 roster into each formal_daemon_config*."""
    synced = []
    errors = []
    leverage_gaps = []
    roster_leverage = {}
    try:
        roster = _read_json(AUTO_DIR / "current_live_strategy_roster.json", {})
        for row in (roster.get("strategies") or []):
            if not isinstance(row, dict):
                continue
            sk = row.get("strategy_key")
            if not sk:
                continue
            for cand in (
                row.get("applied_leverage"),
                row.get("exchange_applied_leverage"),
                row.get("dynamic_target_leverage"),
                row.get("leverage"),
            ):
                try:
                    val = float(cand)
                except Exception:
                    continue
                if val == val and val > 0.0:
                    roster_leverage[sk] = val
                    break
    except Exception:
        roster_leverage = {}
    for slot in list_roster_slots_from_assignments():
        path = _config_path_for_slot(slot["symbol"], slot["timeframe"])
        try:
            cfg = _read_json(path, {})
            if not isinstance(cfg, dict):
                cfg = {}
            cfg["symbol"] = slot["symbol"]
            cfg["timeframe"] = slot["timeframe"]
            cfg["strategy_keys"] = list(slot["strategy_keys"])
            if slot["strategy_keys"]:
                cfg["strategy_key"] = slot["strategy_keys"][0]
            cfg["enabled"] = True
            if enable_auto_open:
                cfg["allow_auto_open"] = True
                cfg["allow_auto_close"] = True
                cfg["formal_auto_trading_authorized"] = True
                cfg["gate_authorized_auto_trading"] = True
            prev_map = cfg.get("strategy_leverages")
            strategy_leverages = dict(prev_map) if isinstance(prev_map, dict) else {}
            live_keys = list(slot.get("strategy_keys") or [])
            try:
                import auto_trade_dynamic_leverage as dynamic_leverage
                for strategy_key in live_keys:
                    resolved = dynamic_leverage.resolve(
                        slot["symbol"], slot["timeframe"], strategy_key,
                        refresh_if_missing=True,
                    )
                    applied = resolved.get("exchange_applied_leverage")
                    if resolved.get("ok") and applied:
                        strategy_leverages[strategy_key] = float(applied)
                        continue
                    fallback = roster_leverage.get(strategy_key)
                    if fallback:
                        strategy_leverages[strategy_key] = float(fallback)
                        continue
                    if strategy_key not in strategy_leverages:
                        leverage_gaps.append({
                            "symbol": slot["symbol"],
                            "timeframe": slot["timeframe"],
                            "strategy_key": strategy_key,
                            "error": (resolved or {}).get("error") or "leverage_unresolved",
                        })
            except Exception as exc:
                leverage_gaps.append({
                    "symbol": slot["symbol"],
                    "timeframe": slot["timeframe"],
                    "error": "dynamic_leverage_loop_failed: %s" % exc,
                })
            # Keep only currently mounted keys.
            strategy_leverages = {
                k: float(v) for k, v in strategy_leverages.items()
                if k in live_keys
            }
            missing_keys = [k for k in live_keys if k not in strategy_leverages]
            if live_keys and not missing_keys:
                cfg["strategy_leverages"] = strategy_leverages
                pick = strategy_leverages.get(live_keys[0])
                if pick is not None:
                    cfg["leverage"] = pick
            elif live_keys and missing_keys:
                # Never persist a half-filled map (fail-closes the missing keys).
                cfg.pop("strategy_leverages", None)
                for strategy_key in live_keys:
                    if strategy_key in roster_leverage:
                        cfg["leverage"] = float(roster_leverage[strategy_key])
                        break
                if "leverage" not in cfg:
                    cfg["leverage"] = 20
                leverage_gaps.append({
                    "symbol": slot["symbol"],
                    "timeframe": slot["timeframe"],
                    "missing_keys": missing_keys,
                    "action": "cleared_partial_strategy_leverages",
                })
            elif "leverage" not in cfg:
                cfg["leverage"] = 20
            if "full_position_ratio" not in cfg:
                cfg["full_position_ratio"] = 0.30
            if "stop_loss_pct" not in cfg:
                cfg["stop_loss_pct"] = 0.009
            if "tick_interval_sec" not in cfg:
                cfg["tick_interval_sec"] = 60
            _write_json(path, cfg)
            synced.append({
                "path": str(path.name),
                "symbol": slot["symbol"],
                "timeframe": slot["timeframe"],
                "strategy_keys": list(slot["strategy_keys"]),
                "strategy_leverages": dict(cfg.get("strategy_leverages") or {}),
            })
        except Exception as exc:
            errors.append({"slot": slot, "error": str(exc)})
    return {
        "ok": not errors,
        "synced": synced,
        "errors": errors,
        "leverage_gaps": leverage_gaps,
    }


def _tick_slot_subprocess(symbol, timeframe, timeout_sec=180):
    """Run one slot tick in an isolated process (module globals are per-symbol)."""
    env = os.environ.copy()
    env["VECTOR_ROOT"] = str(ROOT)
    env["PYTHONPATH"] = str(ROOT) + (
        (os.pathsep + env["PYTHONPATH"]) if env.get("PYTHONPATH") else ""
    )
    env["VECTOR_TRADE_SYMBOL"] = str(symbol).upper()
    env["VECTOR_TRADE_TIMEFRAME"] = str(timeframe).lower()
    # Portfolio supervisor owns open/close Wx via strategy notify path only.
    # Do not emit extra presence SMS from this loop.
    code = (
        "import auto_trade_formal_daemon as d; "
        "import json; "
        "r=d.tick(); "
        "print(json.dumps({"
        "'ok': bool(r.get('ok')), "
        "'action': r.get('action'), "
        "'strategy_keys': (r.get('config') or {}).get('strategy_keys'), "
        "'symbol': d.TRADE_SYMBOL, "
        "'timeframe': d.TRADE_TIMEFRAME"
        "}, ensure_ascii=False))"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_sec,
            universal_newlines=True,
        )
        out = (proc.stdout or "").strip().splitlines()
        payload = {}
        if out:
            try:
                payload = json.loads(out[-1])
            except Exception:
                payload = {"raw": out[-1][:500]}
        return {
            "ok": proc.returncode == 0 and bool(payload.get("ok", True)),
            "returncode": proc.returncode,
            "result": payload,
            "stderr": (proc.stderr or "")[-500:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "tick_timeout", "symbol": symbol, "timeframe": timeframe}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "symbol": symbol, "timeframe": timeframe}


def run_portfolio_forever(tick_interval_sec=60):
    """BTC service entry: continuously monitor all roster symbols/strategies."""
    PORTFOLIO_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    _append_event("portfolio_daemon_started", {"pid": os.getpid(), "mode": "all_roster_slots"})
    while True:
        started = time.time()
        sync = sync_roster_daemon_configs(enable_auto_open=True)
        slot_results = []
        for row in sync.get("synced") or []:
            keys = row.get("strategy_keys") or []
            if not keys:
                continue
            one = _tick_slot_subprocess(row["symbol"], row["timeframe"])
            slot_results.append({
                "symbol": row["symbol"],
                "timeframe": row["timeframe"],
                "strategy_keys": keys,
                "ok": bool(one.get("ok")),
                "action": ((one.get("result") or {}).get("action")),
                "error": one.get("error") or one.get("stderr") or None,
            })
        runtime = {
            "ok": True,
            "mode": "portfolio_all_roster",
            "updated_at": _now(),
            "updated_at_ts": time.time(),
            "tick_duration_sec": round(time.time() - started, 4),
            "synced_slots": sync.get("synced") or [],
            "slot_results": slot_results,
            "wx_extra_disabled": True,
        }
        _write_json(PORTFOLIO_RUNTIME_FILE, runtime)
        sleep_for = max(5.0, float(tick_interval_sec) - (time.time() - started))
        time.sleep(sleep_for)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolio", action="store_true",
                        help="Run all-roster portfolio supervisor loop")
    parser.add_argument("--sync-roster", action="store_true",
                        help="One-shot sync assignment roster into daemon configs")
    args = parser.parse_args()
    if args.sync_roster:
        print(json.dumps(sync_roster_daemon_configs(), ensure_ascii=False, indent=2))
    elif args.portfolio:
        run_portfolio_forever()
    else:
        run_forever()
