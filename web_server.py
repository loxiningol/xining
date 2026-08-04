# -*- coding: utf-8 -*-
import os
import time
import json
import shutil
import re
import threading
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
from flask import Flask, render_template_string, jsonify, request
from flask_httpauth import HTTPBasicAuth

import backtest_engine_v2

try:
    from common import is_pid_alive, send_wx, get_klines, ema, kdj, cci
except Exception:
    def is_pid_alive(pid_path):
        if not os.path.exists(pid_path):
            return False
        try:
            with open(pid_path, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return True
        except Exception:
            return False

    def send_wx(msg):
        print(msg)

    def get_klines(n):
        return [0] * n, [60000] * n, [60100] * n, [59900] * n, [0] * n

    def ema(c, p):
        return c[-1] if c else 0

    def kdj(h, l, c):
        return 50.0, 50.0, 50.0

    def cci(h, l, c):
        return 0.0

try:
    from signal_mgr import get_active, write_signals
except Exception:
    def get_active():
        return []

    def write_signals(sigs):
        pass


app = Flask(__name__)
auth = HTTPBasicAuth()
bt_tasks = {}

STRATEGY_CONFIG_PATH = Path("/root/strategy_configs/experimental_strategies.json")
STRATEGY_CONFIG_BACKUP_DIR = Path("/root/strategy_configs/backups")
VERSION_RECORDER = Path("/root/scripts/record_strategy_version.py")
RESEARCH_INSTRUMENTS = (
    # majors
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "BNB-USDT-SWAP",
    "XRP-USDT-SWAP", "DOGE-USDT-SWAP", "LTC-USDT-SWAP",
    # liquid alts
    "LINK-USDT-SWAP", "AVAX-USDT-SWAP", "DOT-USDT-SWAP", "ATOM-USDT-SWAP",
    "NEAR-USDT-SWAP", "APT-USDT-SWAP", "SUI-USDT-SWAP", "OP-USDT-SWAP",
    "ARB-USDT-SWAP", "FIL-USDT-SWAP", "UNI-USDT-SWAP", "AAVE-USDT-SWAP",
    "BCH-USDT-SWAP", "ETC-USDT-SWAP", "INJ-USDT-SWAP", "SEI-USDT-SWAP",
    "TIA-USDT-SWAP", "TRX-USDT-SWAP", "ICP-USDT-SWAP", "RENDER-USDT-SWAP",
    "ONDO-USDT-SWAP", "JUP-USDT-SWAP", "WLD-USDT-SWAP", "POL-USDT-SWAP",
    # memes / high-beta
    "PEPE-USDT-SWAP", "WIF-USDT-SWAP", "BONK-USDT-SWAP", "FLOKI-USDT-SWAP",
    "SHIB-USDT-SWAP", "ORDI-USDT-SWAP",
    # commodities
    "XAU-USDT-SWAP", "XAG-USDT-SWAP", "NG-USDT-SWAP", "CL-USDT-SWAP",
)


@auth.verify_password
def verify(u, p):
    return u == "quant" and p == "btc2026"


def load_strategy_config():
    if not STRATEGY_CONFIG_PATH.exists():
        raise FileNotFoundError(f"策略配置文件不存在：{STRATEGY_CONFIG_PATH}")

    try:
        data = json.loads(STRATEGY_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"策略配置文件不是合法 JSON：{e}")

    if not isinstance(data, dict):
        raise ValueError("策略配置根节点必须是 JSON 对象")

    strategies = data.get("strategies")
    if not isinstance(strategies, list):
        raise ValueError("策略配置缺少 strategies 数组")

    required = {"key", "name", "group", "direction", "enabled", "version", "modified_at_beijing", "trigger", "exit"}
    keys = set()

    for i, s in enumerate(strategies):
        if not isinstance(s, dict):
            raise ValueError(f"第 {i+1} 个策略不是 JSON 对象")

        missing = required - set(s.keys())
        if missing:
            raise ValueError(f"策略 {s.get('key', i+1)} 缺少字段：{sorted(missing)}")

        for field in ["key", "name", "group", "direction", "version", "modified_at_beijing", "trigger", "exit"]:
            if not str(s.get(field, "")).strip():
                raise ValueError(f"策略 {s.get('key', i+1)} 字段为空：{field}")

        key = s["key"]
        if key in keys:
            raise ValueError(f"策略 key 重复：{key}")
        keys.add(key)

    return data


def get_strategy_list():
    data = load_strategy_config()
    return data["strategies"]


def validate_strategy_config_data(data):
    if not isinstance(data, dict):
        raise ValueError("策略配置根节点必须是 JSON 对象")

    strategies = data.get("strategies")
    if not isinstance(strategies, list):
        raise ValueError("策略配置缺少 strategies 数组")

    required = {"key", "name", "group", "direction", "enabled", "version", "modified_at_beijing", "trigger", "exit"}
    keys = set()

    for i, item in enumerate(strategies):
        if not isinstance(item, dict):
            raise ValueError(f"第 {i+1} 个策略不是 JSON 对象")

        missing = required - set(item.keys())
        if missing:
            raise ValueError(f"策略 {item.get('key', i+1)} 缺少字段：{sorted(missing)}")

        for field in ["key", "name", "group", "direction", "version", "modified_at_beijing", "trigger", "exit"]:
            if not str(item.get(field, "")).strip():
                raise ValueError(f"策略 {item.get('key', i+1)} 字段为空：{field}")

        if not isinstance(item.get("enabled"), bool):
            raise ValueError(f"策略 {item.get('key', i+1)} 的 enabled 必须是 true 或 false")

        params = item.get("params", {})
        if params is None:
            item["params"] = {}
            params = item["params"]

        if not isinstance(params, dict):
            raise ValueError(f"策略 {item.get('key', i+1)} 的 params 必须是 JSON 对象")

        for pk, pv in params.items():
            if not isinstance(pk, str) or not pk.strip():
                raise ValueError(f"策略 {item.get('key', i+1)} 的 params 存在非法参数名")
            if not isinstance(pv, (int, float)):
                raise ValueError(f"策略 {item.get('key', i+1)} 的参数 {pk} 必须是数字")

        key = item["key"]
        if key in keys:
            raise ValueError(f"策略 key 重复：{key}")
        keys.add(key)

    return True


def backup_strategy_config(reason="manual"):
    STRATEGY_CONFIG_BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    if not STRATEGY_CONFIG_PATH.exists():
        raise FileNotFoundError(f"策略配置文件不存在，无法备份：{STRATEGY_CONFIG_PATH}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_reason = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fff-]", "_", str(reason))[:40] or "backup"
    backup_path = STRATEGY_CONFIG_BACKUP_DIR / f"experimental_strategies.{safe_reason}.{ts}.json"

    shutil.copy2(STRATEGY_CONFIG_PATH, backup_path)
    return str(backup_path)


def atomic_write_strategy_config(data):
    validate_strategy_config_data(data)

    STRATEGY_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STRATEGY_CONFIG_PATH.with_suffix(".json.tmp")

    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )
    os.replace(tmp_path, STRATEGY_CONFIG_PATH)


def beijing_strategy_time():
    now = datetime.now()
    return f"{now.year}.{now.month}.{now.day} {now.hour}:{now.minute:02d}"





def compute_param_diff(old_config, new_config, strategy_key):
    diff = {
        "strategy": strategy_key,
        "changed_keys": [],
        "changes": {}
    }

    if not strategy_key:
        return diff

    def find(cfg):
        for item in cfg.get("strategies", []):
            if item.get("key") == strategy_key:
                return item
        return {}

    old_s = find(old_config or {})
    new_s = find(new_config or {})

    old_params = old_s.get("params", {}) or {}
    new_params = new_s.get("params", {}) or {}

    keys = sorted(set(list(old_params.keys()) + list(new_params.keys())))

    for key in keys:
        old_val = old_params.get(key, None)
        new_val = new_params.get(key, None)
        if old_val != new_val:
            diff["changed_keys"].append(key)
            diff["changes"][key] = {
                "old": old_val,
                "new": new_val
            }

    return diff


def record_strategy_version_event(event_type, strategy="", note="", backup_path="", start_time="", end_time="", run_backtest=True, param_diff=None):
    tmp_name = ""
    try:
        if not VERSION_RECORDER.exists():
            return {"status": "error", "message": "版本记录器不存在"}

        cmd = [
            "python3", str(VERSION_RECORDER),
            "--event", str(event_type),
            "--strategy", str(strategy or ""),
            "--note", str(note or ""),
            "--actor", "web_server",
            "--backup-path", str(backup_path or "")
        ]

        if start_time:
            cmd += ["--start-time", str(start_time).replace("T", " ")]
        if end_time:
            cmd += ["--end-time", str(end_time).replace("T", " ")]
        if not run_backtest:
            cmd += ["--no-backtest"]

        if param_diff is not None:
            fd, tmp_name = tempfile.mkstemp(prefix="vector_param_diff_", suffix=".json")
            os.close(fd)
            Path(tmp_name).write_text(json.dumps(param_diff, ensure_ascii=False), encoding="utf-8")
            cmd += ["--param-diff-file", tmp_name]

        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=90)

        if r.returncode != 0:
            return {"status": "error", "message": r.stderr.strip() or r.stdout.strip()}

        last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "{}"
        data = json.loads(last)

        return {
            "status": data.get("status", "ok"),
            "saved_at": data.get("saved_at", ""),
            "strategy": data.get("strategy", strategy),
            "strategy_config_hash": data.get("strategy_config_hash", ""),
            "strategy_logic_hash": data.get("strategy_logic_hash", ""),
            "data_hash": data.get("data_hash", ""),
            "backtest_status": data.get("backtest_status", ""),
            "param_diff": param_diff or {}
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        if tmp_name:
            try:
                os.remove(tmp_name)
            except Exception:
                pass



# BACKTEST_RISK_CONTROLS_START
BACKTEST_ALLOWED_LEVERAGES = [20, 30, 50]
BACKTEST_ALLOWED_STOP_LOSS_RATIOS = [0.003, 0.006, 0.009]

def _parse_backtest_risk_params(data):
    if data is None:
        data = {}
    if not isinstance(data, dict):
        data = {}

    leverage_raw = data.get("leverage", data.get("backtest_leverage", 20))
    stop_raw = data.get(
        "stop_loss_ratio",
        data.get("stop_loss_pct", data.get("stop_loss", data.get("backtest_stop_loss_ratio", 0.009)))
    )

    try:
        leverage = int(float(str(leverage_raw).replace("x", "").replace("X", "").strip()))
    except Exception:
        return None, None, "invalid leverage"

    if leverage not in BACKTEST_ALLOWED_LEVERAGES:
        return None, None, "leverage must be one of 20, 30, 50"

    try:
        raw_text = str(stop_raw).strip()
        has_percent = "%" in raw_text
        stop_value = float(raw_text.replace("%", "").strip())
    except Exception:
        return None, None, "invalid stop loss"

    if has_percent or stop_value >= 0.1:
        stop_loss_ratio = stop_value / 100.0
    else:
        stop_loss_ratio = stop_value

    matched = None
    for allowed in BACKTEST_ALLOWED_STOP_LOSS_RATIOS:
        if abs(stop_loss_ratio - allowed) < 1e-12:
            matched = allowed
            break

    if matched is None:
        return None, None, "stop loss must be one of 0.3%, 0.6%, 0.9%"

    return leverage, matched, None
# BACKTEST_RISK_CONTROLS_END


@app.route("/")
@auth.login_required
def index():
    with open("/root/templates/template.html", encoding="utf-8") as f:
        return f.read()  # STAGE8_23_RAW_HOME_RENDER_FIX


@app.route("/forecast")
@auth.login_required
def forecast_page():
    path = "/root/templates/forecast.html"
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return ("系统规划预测页面缺失: %s" % e), 500


@app.route("/api/forecast/latest", methods=["GET"])
@auth.login_required
def api_forecast_latest():
    try:
        import auto_trade_system_forecast as forecast
        report = forecast.load_latest() or {}
        return jsonify({
            "ok": True,
            "report": report,
            "stale": bool(report.get("stale")),
            "is_current": bool(report.get("is_current", not report.get("stale"))),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/forecast/refresh_statistical", methods=["POST"])
@auth.login_required
def api_forecast_refresh_statistical():
    try:
        import auto_trade_forecast_closeout as closeout
        out = closeout.run_lightweight_statistical_refresh(push_wx=False)
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/metrics/expectancy", methods=["GET"])
@auth.login_required
def api_metrics_expectancy():
    try:
        import auto_trade_expectancy_metrics as exp
        refresh = str(request.args.get("refresh") or "").lower() in ("1", "true", "yes")
        data = exp.compute_all_live_metrics() if refresh else (
            exp._read(exp.METRICS_FILE, {}) or exp.compute_all_live_metrics())
        return jsonify({"ok": True, "bundle": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/metrics/ledgers", methods=["GET"])
@auth.login_required
def api_metrics_ledgers():
    try:
        import auto_trade_forecast_closeout as closeout
        import auto_trade_system_forecast as forecast
        include_all = str(request.args.get("all") or "").lower() in ("1", "true", "yes")
        rows = []
        for s in forecast.list_auto_trade_strategies() or []:
            if not s.get("can_open") and not include_all:
                continue
            rows.append(closeout.detailed_expectancy_ledger(
                s.get("symbol"), s.get("timeframe"), s.get("strategy_key"),
                ai_wr_pct=s.get("ai_theoretical_wr_avg"),
                position_ratio=s.get("max_position_ratio") or 0.30,
            ))
        return jsonify({"ok": True, "ledgers": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/metrics/positive_expectancy_frequency", methods=["GET"])
@auth.login_required
def api_metrics_positive_expectancy_frequency():
    try:
        import auto_trade_system_forecast as forecast
        report = forecast.load_latest() or {}
        pos = report.get("positive_expectancy_frequency") or {}
        if not pos:
            import auto_trade_forecast_closeout as closeout
            pos = closeout.build_positive_expectancy_frequency(
                report.get("per_strategy") or [])
        return jsonify({
            "ok": True,
            "positive_expectancy_frequency": pos,
            "gap": report.get("positive_expectancy_frequency_gap")
            or pos.get("positive_expectancy_frequency_gap"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/metrics/frequency_gap", methods=["GET"])
@auth.login_required
def api_metrics_frequency_gap():
    try:
        import auto_trade_expectancy_metrics as exp
        import auto_trade_system_forecast as forecast
        gap = exp._read(exp.GAP_REPORT_PATH, {})
        if not gap:
            bundle = exp.compute_all_live_metrics()
            gap = bundle.get("frequency_gap") or {}
        report = forecast.load_latest() or {}
        return jsonify({
            "ok": True,
            "report": gap,
            "frequency_gap": report.get("frequency_gap") or gap.get("pool") or gap,
            "positive_expectancy_frequency": report.get("positive_expectancy_frequency")
            or gap.get("positive_expectancy_frequency"),
            "positive_expectancy_frequency_gap": report.get(
                "positive_expectancy_frequency_gap")
            or gap.get("positive_expectancy_frequency_gap"),
            "portfolio_frequency": report.get("portfolio_frequency")
            or gap.get("portfolio_frequency"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/forecast/calibration", methods=["GET"])
@auth.login_required
def api_forecast_calibration():
    try:
        import auto_trade_expectancy_metrics as exp
        import auto_trade_forecast_closeout as closeout
        data = exp._read(exp.CALIBRATION_PATH, {})
        data = closeout.enrich_calibration(data)
        return jsonify({"ok": True, "calibration": data,
                        "calibration_stage": data.get("calibration_stage"),
                        "period_metrics": data.get("period_metrics")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/forecast/history", methods=["GET"])
@auth.login_required
def api_forecast_history():
    try:
        import auto_trade_system_forecast as forecast
        limit = request.args.get("limit", 20)
        try:
            limit = max(1, min(int(limit), 100))
        except Exception:
            limit = 20
        return jsonify({"ok": True, "items": forecast.load_history(limit)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


_forecast_job_lock = threading.Lock()
_forecast_job = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "ok": None,
    "status": "idle",
    "error": None,
    "report": None,
    "wx": None,
    "text": None,
}


def _forecast_job_snapshot():
    with _forecast_job_lock:
        return dict(_forecast_job)


def _run_forecast_job(push_wx=True):
    import auto_trade_system_forecast as forecast
    with _forecast_job_lock:
        if _forecast_job.get("running"):
            return False
        _forecast_job.update({
            "running": True,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None,
            "ok": None,
            "status": "running",
            "error": None,
            "report": None,
            "wx": None,
            "text": None,
        })
    try:
        out = forecast.run_forecast(push_wx=push_wx)
        report = out.get("report") or {}
        ai_ok = bool((report.get("ai_status") or {}).get("all_ok"))
        with _forecast_job_lock:
            _forecast_job.update({
                "running": False,
                "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "ok": True,
                "status": "ok" if ai_ok else "partial",
                "error": None,
                "report": report,
                "wx": out.get("wx"),
                "text": out.get("text"),
            })
        return True
    except Exception as e:
        with _forecast_job_lock:
            _forecast_job.update({
                "running": False,
                "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "ok": False,
                "status": "error",
                "error": str(e),
                "report": None,
                "wx": None,
                "text": None,
            })
        return False


@app.route("/api/forecast/run_status", methods=["GET"])
@auth.login_required
def api_forecast_run_status():
    """查询手动触发的系统规划预测任务状态。"""
    st = _forecast_job_snapshot()
    return jsonify({
        "ok": True if st.get("ok") is None else bool(st.get("ok")),
        "running": bool(st.get("running")),
        "status": st.get("status") or "idle",
        "started_at": st.get("started_at"),
        "finished_at": st.get("finished_at"),
        "error": st.get("error"),
        "report": st.get("report"),
        "wx": st.get("wx"),
        "text": st.get("text"),
    })


@app.route("/api/forecast/run", methods=["POST"])
@auth.login_required
def api_forecast_run():
    """手动触发系统规划预测（3次AI调用，不计入策略复核配额）。

    默认 async=true：后台运行（等同 python3 /root/auto_trade_system_forecast.py --run，含 Wx），
    立即返回 202；前端轮询 /api/forecast/run_status。
    传 {"async": false} 可同步等待（旧行为）。
    """
    try:
        payload = request.get_json(silent=True) or {}
        push_wx = True if payload.get("push_wx") is None else bool(payload.get("push_wx"))
        run_async = True if payload.get("async") is None else bool(payload.get("async"))
        st = _forecast_job_snapshot()
        if st.get("running"):
            return jsonify({
                "ok": True,
                "async": True,
                "status": "running",
                "message": "预测任务已在运行",
                "started_at": st.get("started_at"),
            }), 409
        if run_async:
            def _worker():
                _run_forecast_job(push_wx=push_wx)
            threading.Thread(target=_worker, name="forecast-run", daemon=True).start()
            # brief wait so status flips to running before first poll
            time.sleep(0.05)
            st2 = _forecast_job_snapshot()
            return jsonify({
                "ok": True,
                "async": True,
                "status": "started",
                "message": "预测任务已启动（含Wx推送）",
                "started_at": st2.get("started_at"),
            }), 202
        import auto_trade_system_forecast as forecast
        out = forecast.run_forecast(push_wx=push_wx)
        report = out.get("report") or {}
        ai_ok = bool((report.get("ai_status") or {}).get("all_ok"))
        return jsonify({
            "ok": True,
            "async": False,
            "status": "ok" if ai_ok else "partial",
            "report": report,
            "wx": out.get("wx"),
            "text": out.get("text"),
        }), (200 if ai_ok else 202)
    except Exception as e:
        return jsonify({"ok": False, "status": "error", "error": str(e),
                        "message": str(e)}), 500


# ─── Dual-engine strategy creation (GLM designer + Codex engineer) ─────

_VECTOR_STATUS_CACHE = {"ts": 0.0, "payload": None, "building": False, "error": None}
_VECTOR_STATUS_CACHE_LOCK = threading.Lock()
_VECTOR_STATUS_CACHE_TTL = 8.0
_VECTOR_STATUS_STALE_TTL = 120.0
_VECTOR_STATUS_BUILD_TIMEOUT = 18.0


def _vector_minimal_status_payload(error=None):
    return {
        "ok": False if error else True,
        "degraded": True,
        "error": str(error) if error else None,
        "message": "运行策略状态降级返回（主机内存紧张或构建超时）",
        "asset_zones": [],
        "daily_trade_records": [],
        "assets": [],
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _vector_status_payload_cached():
    """Serve fresh/stale cache; never block the web thread on a full roster rebuild."""
    now = time.time()
    with _VECTOR_STATUS_CACHE_LOCK:
        cached = _VECTOR_STATUS_CACHE.get("payload")
        ts = float(_VECTOR_STATUS_CACHE.get("ts") or 0)
        building = bool(_VECTOR_STATUS_CACHE.get("building"))
        age = now - ts if ts else 1e9
        if cached is not None and age < _VECTOR_STATUS_CACHE_TTL:
            return cached
        stale = cached if (cached is not None and age < _VECTOR_STATUS_STALE_TTL) else None
        already_building = building

    def _build():
        try:
            payload = _vector_status_payload()
            with _VECTOR_STATUS_CACHE_LOCK:
                _VECTOR_STATUS_CACHE["ts"] = time.time()
                _VECTOR_STATUS_CACHE["payload"] = payload
                _VECTOR_STATUS_CACHE["error"] = None
                _VECTOR_STATUS_CACHE["building"] = False
        except Exception as exc:
            with _VECTOR_STATUS_CACHE_LOCK:
                _VECTOR_STATUS_CACHE["error"] = str(exc)
                _VECTOR_STATUS_CACHE["building"] = False
                if _VECTOR_STATUS_CACHE.get("payload") is None:
                    _VECTOR_STATUS_CACHE["payload"] = _vector_minimal_status_payload(exc)
                    _VECTOR_STATUS_CACHE["ts"] = time.time()

    # Kick a background rebuild when cache is missing/expired.
    with _VECTOR_STATUS_CACHE_LOCK:
        if not _VECTOR_STATUS_CACHE.get("building"):
            _VECTOR_STATUS_CACHE["building"] = True
            already_building = False
            threading.Thread(target=_build, name="vector-status-build", daemon=True).start()
        else:
            already_building = True

    if stale is not None:
        out = dict(stale)
        out["cache"] = "stale"
        return out

    # Another request is already rebuilding — do not stack 18s waits on the UI.
    if already_building:
        return _vector_minimal_status_payload("status_building")

    # First paint only: wait briefly for the builder, then degrade instead of hanging.
    deadline = time.time() + min(4.0, _VECTOR_STATUS_BUILD_TIMEOUT)
    while time.time() < deadline:
        with _VECTOR_STATUS_CACHE_LOCK:
            cached = _VECTOR_STATUS_CACHE.get("payload")
            if cached is not None:
                return cached
            if not _VECTOR_STATUS_CACHE.get("building"):
                err = _VECTOR_STATUS_CACHE.get("error")
                return _vector_minimal_status_payload(err or "status_build_failed")
        time.sleep(0.05)
    return _vector_minimal_status_payload("status_build_timeout")


@app.route("/api/dual_engine/status", methods=["GET"])
@auth.login_required
def api_dual_engine_status():
    try:
        import auto_trade_dual_engine_factory as dual
        dual._ensure_dirs()
        dual._load_env()
        return jsonify({"ok": True, "status": dual.load_status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/creation/pipelines", methods=["GET"])
@auth.login_required
def api_creation_pipelines():
    """策略创造演进模块：管道1 / 管道2 实时状态。"""
    try:
        from dual_engine_workflow_v2 import parallel_creation as pc
        return jsonify(pc.status())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "pipelines": []}), 500


@app.route("/api/creation/submit", methods=["POST"])
@auth.login_required
def api_creation_submit():
    """Submit a research direction into the sole parallel creation entry."""
    try:
        from dual_engine_workflow_v2 import parallel_creation as pc
        payload = request.get_json(silent=True) or {}
        out = pc.submit_job(
            source=payload.get("source") or "web",
            research_direction=payload.get("research_direction") or payload.get("brief") or "",
            symbol=payload.get("symbol"),
            timeframe=payload.get("timeframe") or "5m",
            direction=payload.get("direction") or "long",
            brief=payload.get("brief") or "",
            skip_llm=not bool(payload.get("with_llm")),
            max_loops=int(payload.get("max_loops") or 5),
            pipeline=payload.get("pipeline"),
            research_contract=payload.get("research_contract"),
            mutation_contract=payload.get("mutation_contract"),
            data_version=payload.get("data_version"),
            code_version=payload.get("code_version"),
            cooldown_seconds=payload.get("cooldown_seconds"),
            force=bool(payload.get("force")),
        )
        return jsonify(out), (200 if out.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "message": str(e)}), 400


@app.route("/api/creation/review", methods=["GET"])
@auth.login_required
def api_creation_review():
    """策略创造复核模块：四阶段复核板 + 双管道对接。"""
    try:
        from dual_engine_workflow_v2 import parallel_creation as pc
        return jsonify(pc.review_status())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/dual_engine/auto_driver", methods=["GET"])
@auth.login_required
def api_dual_engine_auto_driver():
    """Auto-Driver live progress + multi-slot board for True Words console."""
    try:
        import sys
        from pathlib import Path
        root = Path(os.environ.get("VECTOR_ROOT") or "/root")
        scripts_dir = str(root / "scripts")
        local_scripts = str(Path(__file__).resolve().parent / "scripts")
        for d in (scripts_dir, local_scripts):
            if d not in sys.path:
                sys.path.insert(0, d)
        try:
            from auto_driver import live_status as ad_live  # type: ignore
            from auto_driver import multi_slot as ad_slots  # type: ignore
        except Exception:
            from scripts.auto_driver import live_status as ad_live
            from scripts.auto_driver import multi_slot as ad_slots
        data = ad_live.read_live_status(str(root))
        board = ad_slots.read_slots_board(str(root))
        return jsonify({"ok": True, "auto_driver": data, "slots": board})
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "auto_driver": {"ok": False, "running": False},
            "slots": {"ok": False, "slots": [], "capacity": 1},
        }), 500


@app.route("/api/dual_engine/bootstrap", methods=["POST"])
@auth.login_required
def api_dual_engine_bootstrap():
    try:
        import auto_trade_dual_engine_factory as dual
        return jsonify(dual.bootstrap())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/dual_engine/start_task", methods=["POST"])
@auth.login_required
def api_dual_engine_start_task():
    """创造策略指令入口：第一步研究发现 → 第二步统一门槛 → 第三步四阶段复核。"""
    try:
        import auto_trade_dual_engine_factory as dual
        payload = request.get_json(silent=True) or {}
        out = dual.start_creation_task(
            async_mode=True,
            symbol=payload.get("symbol"),
            timeframe=payload.get("timeframe"),
            exploration_mode=payload.get("exploration_mode"),
            force_workflow=payload.get("force_workflow"),
        )
        code = 202 if out.get("status") == "started" else (409 if out.get("status") == "running" else 200)
        if out.get("ok") is False and out.get("status") != "running":
            code = 400
        return jsonify(out), code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "message": str(e)}), 500


@app.route("/api/dual_engine/workflow", methods=["GET"])
@auth.login_required
def api_dual_engine_workflow():
    """Workflow v2 flags, upgrade status, and latest artifacts for column 4."""
    try:
        import dual_engine_workflow_v2 as wfv2
        payload = wfv2.workflow_status_payload()
        task_id = request.args.get("task_id")
        if task_id:
            payload["artifacts"] = wfv2.store.latest_task_artifacts(task_id)
        return jsonify({"ok": True, "workflow": payload})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/dual_engine/workflow/flags", methods=["POST"])
@auth.login_required
def api_dual_engine_workflow_flags():
    """Update workflow flags. Refuses creation_entry=v2 unless acceptance A–H all true."""
    try:
        import dual_engine_workflow_v2 as wfv2
        payload = request.get_json(silent=True) or {}
        if "creation_entry" in payload and payload.get("creation_entry") == "v2":
            flags = wfv2.load_flags()
            acc = flags.get("acceptance") or {}
            missing = [k for k in list("ABCDEFGH") if not acc.get(k)]
            if missing and not payload.get("force_after_acceptance_override"):
                return jsonify({
                    "ok": False,
                    "error": "acceptance_incomplete",
                    "missing": missing,
                    "message": "不得在验收A–H全部通过前切换创造入口",
                }), 409
            out = wfv2.set_creation_entry("v2", note=payload.get("note") or "api_switch")
            return jsonify({"ok": True, "flags": out})
        patch = {}
        for k in ("upgrade_status", "acceptance", "pause", "notes"):
            if k in payload:
                patch[k] = payload[k]
        if payload.get("creation_entry") == "legacy":
            out = wfv2.set_creation_entry("legacy", note=payload.get("note") or "api_rollback_entry")
            return jsonify({"ok": True, "flags": out})
        out = wfv2.save_flags(patch) if patch else wfv2.load_flags()
        return jsonify({"ok": True, "flags": out})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/dual_engine/evolve", methods=["POST"])
@auth.login_required
def api_dual_engine_evolve():
    """强制进化池迭代一世代。"""
    try:
        import auto_trade_dual_engine_factory as dual
        out = dual.force_evolve_async()
        code = 202 if out.get("status") == "started" else (409 if out.get("status") == "running" else 200)
        return jsonify(out), code
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "message": str(e)}), 500


@app.route("/api/dual_engine/refresh_insight", methods=["POST"])
@auth.login_required
def api_dual_engine_refresh_insight():
    try:
        import auto_trade_dual_engine_factory as dual
        return jsonify(dual.refresh_insight_async()), 202
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/strategy_desc")
@auth.login_required
def strategy_desc():
    try:
        data = load_strategy_config()
        return jsonify({
            "schema_version": data.get("schema_version"),
            "mode": data.get("mode"),
            "updated_at": data.get("updated_at"),
            "strategies": data["strategies"]
        })
    except Exception as e:
        return jsonify({
            "error": "策略配置读取失败",
            "message": str(e),
            "config_path": str(STRATEGY_CONFIG_PATH)
        }), 500




@app.route("/api/strategy_config")
@auth.login_required
def strategy_config_get():
    try:
        data = load_strategy_config()
        return jsonify({
            "status": "ok",
            "config_path": str(STRATEGY_CONFIG_PATH),
            "backup_dir": str(STRATEGY_CONFIG_BACKUP_DIR),
            "config": data
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "error": "策略配置读取失败",
            "message": str(e),
            "config_path": str(STRATEGY_CONFIG_PATH)
        }), 500


@app.route("/api/strategy_config/backup", methods=["POST"])
@auth.login_required
def strategy_config_backup():
    try:
        req = request.get_json(silent=True) or {}
        reason = req.get("reason", "manual")
        backup_path = backup_strategy_config(reason=reason)
        return jsonify({
            "status": "ok",
            "msg": "策略配置已备份",
            "backup_path": backup_path
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "error": "策略配置备份失败",
            "message": str(e)
        }), 500



@app.route("/api/strategy_config/update", methods=["POST"])
@auth.login_required
def strategy_config_update():
    try:
        req = request.get_json(silent=True)
        if not isinstance(req, dict):
            return jsonify({
                "status": "error",
                "error": "请求体必须是 JSON 对象"
            }), 400

        old_config = load_strategy_config()

        if "config" in req:
            new_config = req["config"]
        elif "strategies" in req:
            new_config = dict(old_config)
            new_config["strategies"] = req["strategies"]
        else:
            new_config = req

        if not isinstance(new_config, dict):
            return jsonify({
                "status": "error",
                "error": "配置必须是 JSON 对象"
            }), 400

        for key in old_config.keys():
            if key not in new_config and key != "strategies":
                new_config[key] = old_config[key]

        changed_strategy_key = req.get("changed_strategy_key", "")
        if changed_strategy_key:
            for strategy in new_config.get("strategies", []):
                if strategy.get("key") == changed_strategy_key:
                    strategy["modified_at_beijing"] = beijing_strategy_time()
                    break

        new_config["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        validate_strategy_config_data(new_config)

        param_diff = compute_param_diff(old_config, new_config, changed_strategy_key)

        backup_path = backup_strategy_config(reason="before_update")
        atomic_write_strategy_config(new_config)

        backtest_start = req.get("backtest_start", "2026-06-01 00:00")
        backtest_end = req.get("backtest_end", "2026-06-28 00:00")
        run_snapshot = bool(req.get("run_backtest_snapshot", True))

        version_record = record_strategy_version_event(
            event_type="parameter_save",
            strategy=changed_strategy_key,
            note="网页保存参数或说明文本",
            backup_path=backup_path,
            start_time=backtest_start,
            end_time=backtest_end,
            run_backtest=run_snapshot,
            param_diff=param_diff
        )

        return jsonify({
            "status": "ok",
            "msg": "策略配置已保存",
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "backup_path": backup_path,
            "config_path": str(STRATEGY_CONFIG_PATH),
            "strategy_count": len(new_config.get("strategies", [])),
            "version_record": version_record
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "error": "策略配置保存失败",
            "message": str(e)
        }), 500


@app.route("/api/dialysis")
@auth.login_required
def dialysis():
    try:
        timeframe = backtest_engine_v2.normalize_timeframe(
            request.args.get("timeframe") or "1h"
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    timeframe_spec = backtest_engine_v2.TIMEFRAME_SPECS[timeframe]

    def snapshot(inst_id):
        try:
            import auto_trade_okx
            raw = auto_trade_okx._okx_request(
                "GET", "/api/v5/market/history-candles",
                params={
                    "instId": inst_id,
                    "bar": timeframe_spec["okx_bar"],
                    "limit": "200",
                },
                auth=False,
            )
            rows = []
            for row in raw.get("data") or []:
                if isinstance(row, list) and len(row) >= 9 and str(row[8]) == "1":
                    rows.append({
                        "timestamp": pd.to_datetime(int(row[0]), unit="ms", utc=True),
                        "open": float(row[1]), "high": float(row[2]),
                        "low": float(row[3]), "close": float(row[4]),
                    })
            rows.sort(key=lambda x: x["timestamp"])
            frame = pd.DataFrame(rows).set_index("timestamp")
            frame = backtest_engine_v2.precompute_indicators(frame)
            last = frame.iloc[-1]
            ma_values = [(name, float(last[name.lower()])) for name in ("EMA7", "EMA8", "EMA23", "EMA32")]
            return {
                "ok": True, "symbol": inst_id,
                "timeframe": timeframe,
                "timeframe_label": timeframe_spec["label"],
                "price": float(last["close"]),
                "ma_status": " > ".join(x[0] for x in sorted(ma_values, key=lambda x: x[1], reverse=True)),
                "kdj_k": float(last["k"]), "kdj_d": float(last["d"]),
                "kdj_j": float(last["j"]), "cci": float(last["cci"]),
            }
        except Exception as exc:
            return {
                "ok": False, "symbol": inst_id,
                "timeframe": timeframe,
                "timeframe_label": timeframe_spec["label"],
                "price": 0,
                "ma_status": "底层行情流数据获取异常", "kdj_k": 0,
                "kdj_d": 0, "kdj_j": 0, "cci": 0, "error": str(exc),
            }

    markets = [
        snapshot("BTC-USDT-SWAP"),
        snapshot("CL-USDT-SWAP"),
        snapshot("XAU-USDT-SWAP"),
        snapshot("NG-USDT-SWAP"),
        snapshot("XAG-USDT-SWAP"),
        snapshot("LTC-USDT-SWAP"),
        snapshot("ADA-USDT-SWAP"),
    ]
    btc = markets[0]
    return jsonify({
        "signals": get_active(),
        "timeframe": timeframe,
        "timeframe_label": timeframe_spec["label"],
        "markets": markets,
        "price": btc["price"],
        "ma_status": btc["ma_status"],
        "kdj_k": btc["kdj_k"],
        "kdj_d": btc["kdj_d"],
        "kdj_j": btc["kdj_j"],
        "cci": btc["cci"],
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })


def build_process_status_payload():
    components = []

    def add(key, name, state, detail, critical=True, detail_lines=None):
        lines = list(detail_lines or [])
        if not lines and detail:
            lines = [x for x in str(detail).split("\n") if x != ""]
        text = "\n".join(lines) if lines else (detail or "")
        components.append({
            "key": key,
            "name": name,
            "state": state,
            "healthy": state == "healthy",
            "detail": text,
            "detail_lines": lines,
            "critical": bool(critical),
        })

    def _read_pid(path):
        try:
            return int(Path(path).read_text().strip())
        except Exception:
            return None

    def _pid_alive(pid):
        if not pid:
            return False
        try:
            os.kill(int(pid), 0)
            return True
        except Exception:
            return False

    def _proc_cmdline(pid):
        try:
            return Path("/proc/%s/cmdline" % int(pid)).read_bytes().replace(b"\x00", b" ").decode("utf-8", "ignore")
        except Exception:
            return ""

    def _find_web_listen_pid():
        """Prefer the process actually bound to :8080 that runs web_server.py."""
        candidates = []
        try:
            for proc in Path("/proc").iterdir():
                if not proc.name.isdigit():
                    continue
                cmd = _proc_cmdline(proc.name)
                if "web_server.py" not in cmd:
                    continue
                pid = int(proc.name)
                # Prefer current request process when it is the web server.
                if pid == os.getpid():
                    return pid
                candidates.append(pid)
        except Exception:
            pass
        if os.getpid() in candidates or "web_server.py" in _proc_cmdline(os.getpid()):
            return os.getpid()
        return candidates[0] if len(candidates) == 1 else (candidates[0] if candidates else None)

    # --- Website / PID file ---
    web_pid_path = Path("/root/web_server.pid")
    file_web_pid = _read_pid(web_pid_path)
    live_web_pid = _find_web_listen_pid()
    if live_web_pid and "web_server.py" in _proc_cmdline(live_web_pid):
        if file_web_pid != live_web_pid:
            try:
                web_pid_path.write_text(str(live_web_pid) + "\n")
            except Exception:
                pass
        web_pid = live_web_pid
        web_ok = True
        web_detail_lines = [
            "8080 网站服务正常",
            "PID %s（已与运行进程对齐）" % web_pid,
        ]
    else:
        web_pid = file_web_pid
        web_ok = False
        web_detail_lines = [
            "网站服务异常：未找到运行中的 web_server.py",
            "PID文件=%s" % (file_web_pid if file_web_pid is not None else "无"),
        ]
    add("web", "网站服务", "healthy" if web_ok else "error",
        "\n".join(web_detail_lines), detail_lines=web_detail_lines)

    # --- Formal daemon farm inventory (includes XRP 15m) ---
    daemon_status = {}
    try:
        import auto_trade_formal_daemon as daemon
        daemon_status = daemon.status()
    except Exception as e:
        daemon_status = {"running": False, "error": str(e)}

    daemon_pids = []
    try:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            cmd = _proc_cmdline(proc.name)
            if "python3 -c import auto_trade_formal_daemon as d; d.run_forever()" in cmd:
                daemon_pids.append(int(proc.name))
    except Exception:
        pass
    daemon_pid_set = set(daemon_pids)

    expected_daemons = [
        ("BTC 1小时", "/root/auto_trade/formal_daemon.pid"),
        ("BTC 15分钟", "/root/auto_trade/formal_daemon_btc_15m.pid"),
        ("BTC 5分钟", "/root/auto_trade/formal_daemon_btc_5m.pid"),
        ("ETH 5分钟", "/root/auto_trade/formal_daemon_eth_5m.pid"),
        ("SOL 5分钟", "/root/auto_trade/formal_daemon_sol_5m.pid"),
        ("XRP 5分钟", "/root/auto_trade/formal_daemon_xrp_5m.pid"),
        ("CL 1小时", "/root/auto_trade/formal_daemon_cl.pid"),
        ("CL 5分钟", "/root/auto_trade/formal_daemon_cl_5m.pid"),
        ("XAU 1小时", "/root/auto_trade/formal_daemon_xau.pid"),
        ("XAU 15分钟", "/root/auto_trade/formal_daemon_xau_15m.pid"),
        ("NG 1小时", "/root/auto_trade/formal_daemon_ng.pid"),
        ("NG 5分钟", "/root/auto_trade/formal_daemon_ng_5m.pid"),
        ("XAG 5分钟", "/root/auto_trade/formal_daemon_xag_5m.pid"),
        ("LTC 5分钟", "/root/auto_trade/formal_daemon_ltc_5m.pid"),
        ("ADA 5分钟", "/root/auto_trade/formal_daemon_ada_5m.pid"),
        ("XRP 15分钟", "/root/auto_trade/formal_daemon_xrp_15m.pid"),
    ]
    daemon_rows = []
    missing_daemons = []
    for label, pid_path in expected_daemons:
        pid = _read_pid(pid_path)
        ok = bool(pid and pid in daemon_pid_set and _pid_alive(pid))
        daemon_rows.append({"label": label, "pid": pid, "ok": ok})
        if not ok:
            missing_daemons.append(label)

    expected_count = len(expected_daemons)
    running_expected = sum(1 for row in daemon_rows if row["ok"])
    # Healthy when every expected instance is up. Extra procs are noted, not fatal.
    daemon_group_ok = running_expected == expected_count
    daemon_running = bool(daemon_status.get("running")) or running_expected > 0
    daemon_state = "healthy" if daemon_group_ok else "error"
    pid_by_label = {row["label"]: row["pid"] for row in daemon_rows}
    btc_daemon_pid = pid_by_label.get("BTC 1小时")
    btc_15m_daemon_pid = pid_by_label.get("BTC 15分钟")
    btc_5m_daemon_pid = pid_by_label.get("BTC 5分钟")
    eth_5m_daemon_pid = pid_by_label.get("ETH 5分钟")
    sol_5m_daemon_pid = pid_by_label.get("SOL 5分钟")
    xrp_5m_daemon_pid = pid_by_label.get("XRP 5分钟")
    cl_daemon_pid = pid_by_label.get("CL 1小时")
    cl_5m_daemon_pid = pid_by_label.get("CL 5分钟")
    xau_daemon_pid = pid_by_label.get("XAU 1小时")
    xau_15m_daemon_pid = pid_by_label.get("XAU 15分钟")
    ng_daemon_pid = pid_by_label.get("NG 1小时")
    ng_5m_daemon_pid = pid_by_label.get("NG 5分钟")
    xag_5m_daemon_pid = pid_by_label.get("XAG 5分钟")
    ltc_5m_daemon_pid = pid_by_label.get("LTC 5分钟")
    ada_5m_daemon_pid = pid_by_label.get("ADA 5分钟")
    xrp_15m_daemon_pid = pid_by_label.get("XRP 15分钟")

    if daemon_state == "healthy":
        daemon_lines = [
            "实盘守护进程 %d/%d 在线（含 XRP 15分钟）" % (running_expected, expected_count),
        ]
        if len(daemon_pids) > expected_count:
            daemon_lines.append("另有 %d 个额外进程（不判异常）" % (len(daemon_pids) - expected_count))
    else:
        daemon_lines = [
            "守护进程异常：%d/%d 在线" % (running_expected, expected_count),
            "缺失：" + ("、".join(missing_daemons) if missing_daemons else "未知"),
            "当前进程数 %d" % len(daemon_pids),
        ]
    add("auto_daemon", "自动交易守护进程", daemon_state,
        "\n".join(daemon_lines), detail_lines=daemon_lines)

    # --- Entry priority ---
    try:
        import auto_trade_symbol_priority as symbol_priority
        priority_status = symbol_priority.status()
        priority_order = priority_status.get("priority") or []
        expected_priority = [
            "BTC-USDT-SWAP", "CL-USDT-SWAP",
            "XAU-USDT-SWAP", "NG-USDT-SWAP",
            "XAG-USDT-SWAP",
            "LTC-USDT-SWAP", "ADA-USDT-SWAP",
        ]
        priority_ok = (
            priority_status.get("ok") is True
            and priority_order[:7] == expected_priority
            and priority_status.get("unknown_symbol_policy") == "append_to_end"
        )
        import auto_trade_portfolio_risk as portfolio_risk
        portfolio_policy = portfolio_risk.load_policy()
        max_positions = int(portfolio_policy.get("max_open_positions", 3))
        max_risk_pct = float(portfolio_policy.get("max_total_estimated_risk_ratio", 0.152)) * 100.0
        priority_lines = [
            "优先级：" + " > ".join(x.replace("-USDT-SWAP", "") for x in priority_order),
            "未知标的：追加到队尾（含 XRP）",
            "组合上限：最多 %d 笔，同标的 1 笔" % max_positions,
            "止损风险合计不超过 %.1f%%" % max_risk_pct,
        ]
        priority_detail = "\n".join(priority_lines)
    except Exception as e:
        priority_status = {"ok": False, "error": str(e)}
        priority_ok = False
        priority_lines = ["优先级协调器读取失败：%s" % e]
        priority_detail = priority_lines[0]
    add("entry_priority", "全账户开仓优先级",
        "healthy" if priority_ok else "error", priority_detail, detail_lines=priority_lines)

    # --- Live strategy roster (unified daemon configs) ---
    live_rows = []
    strategy_read_error = None
    try:
        import auto_trade_system_forecast as forecast
        import auto_trade_strategy_titles as titles
        for item in (forecast.list_auto_trade_strategies() or []):
            if not item.get("can_open"):
                continue
            key = item.get("strategy_key")
            symbol = str(item.get("symbol") or "").replace("-USDT-SWAP", "")
            timeframe = item.get("timeframe") or ""
            tf_label = {"1h": "1小时", "15m": "15分钟", "5m": "5分钟"}.get(timeframe, timeframe)
            name = titles.short_strategy_title(
                key, item.get("strategy_name") or (item.get("row") or {}).get("strategy_name"))
            grade = item.get("lifecycle_grade") or "B"
            ratio = item.get("max_position_ratio")
            ratio_txt = ""
            try:
                if ratio is not None and ratio != "":
                    ratio_txt = "｜仓位 %.0f%%" % (float(ratio) * 100.0)
            except Exception:
                ratio_txt = ""
            live_rows.append({
                "key": key,
                "line": "· %s %s｜%s｜%s%s" % (symbol, tf_label, name, grade, ratio_txt),
            })
    except Exception as e:
        strategy_read_error = str(e)

    # Expected current live openable set.
    expected_live_keys = {
        "codex0725t3_ada5m_trendpb_r42_z2p3_h14",
        "ltc5_exhaustion_fade_short_ai",
        "ng5_exhaustion_fade_short_ai",
        "frost_xrp_rescue_h20_t45",
        "frost3_btc1h_xrpport_exhaustion_fade_slope",
        "btc5_trend_rebound_ada5_clone_v1",
        "eth5_trend_rebound_ada5_clone_v1",
        "sol5_trend_rebound_ada5_clone_v1",
        "xrp5_trend_rebound_ada5_clone_v1",
    }
    live_keys = {row["key"] for row in live_rows}
    strategy_ok = (
        strategy_read_error is None
        and live_keys == expected_live_keys
        and daemon_state == "healthy"
    )
    if strategy_read_error:
        strategy_lines = ["策略花名册读取失败：%s" % strategy_read_error]
    elif not live_rows:
        strategy_lines = ["未读取到可开仓运行策略"]
    else:
        strategy_lines = ["可开仓运行策略 %d 条：" % len(live_rows)]
        strategy_lines.extend(row["line"] for row in live_rows)
        if live_keys != expected_live_keys:
            missing = sorted(expected_live_keys - live_keys)
            extra = sorted(live_keys - expected_live_keys)
            if missing:
                strategy_lines.append("缺少预期：" + "、".join(missing))
            if extra:
                strategy_lines.append("额外策略：" + "、".join(extra))
    add("auto_strategies", "自动交易策略监测",
        "healthy" if strategy_ok else "error",
        "\n".join(strategy_lines), detail_lines=strategy_lines)

    # --- 15m modules: XRP live; BTC/CL/XAU/NG idle ---
    def _cfg_keys(config):
        keys = list(config.get("strategy_keys") or []) if isinstance(config, dict) else []
        return keys

    def _idle_15m_ok(config, symbol):
        return (
            isinstance(config, dict)
            and config.get("symbol") == symbol
            and config.get("timeframe") == "15m"
            and _cfg_keys(config) == []
            and bool(config.get("allow_auto_open")) is False
        )

    fifteen_symbols_idle = (
        "BTC-USDT-SWAP",
        "CL-USDT-SWAP",
        "XAU-USDT-SWAP",
        "NG-USDT-SWAP",
    )
    fifteen_idle_ok = True
    fifteen_notes = []
    for symbol in fifteen_symbols_idle:
        config = _vector_load_json(
            Path("/root/auto_trade/formal_daemon_config_%s_15m.json" % symbol.split("-")[0].lower()),
            {},
        )
        ok = _idle_15m_ok(config, symbol)
        fifteen_idle_ok = fifteen_idle_ok and ok
        if not ok:
            fifteen_notes.append("%s 15分钟非空闲" % symbol.split("-")[0])

    xrp_15m_cfg = _vector_load_json(Path("/root/auto_trade/formal_daemon_config_xrp_15m.json"), {})
    xrp_15m_ok = (
        isinstance(xrp_15m_cfg, dict)
        and xrp_15m_cfg.get("symbol") == "XRP-USDT-SWAP"
        and xrp_15m_cfg.get("timeframe") == "15m"
        and _cfg_keys(xrp_15m_cfg) == ["frost_xrp_rescue_h20_t45"]
        and bool(xrp_15m_cfg.get("allow_auto_open")) is True
        and bool(xrp_15m_cfg.get("formal_auto_trading_authorized")) is True
        and bool(xrp_15m_daemon_pid and xrp_15m_daemon_pid in daemon_pid_set)
    )
    # BTC/XAU 15m daemons may stay up for monitoring even while idle.
    btc_15m_proc_ok = bool(btc_15m_daemon_pid and btc_15m_daemon_pid in daemon_pid_set)
    xau_15m_proc_ok = bool(xau_15m_daemon_pid and xau_15m_daemon_pid in daemon_pid_set)
    fifteen_minute_ok = bool(fifteen_idle_ok and xrp_15m_ok and btc_15m_proc_ok and xau_15m_proc_ok)
    if fifteen_minute_ok:
        fifteen_lines = [
            "XRP 15分钟：寒霜 exhaustion_fade 可开仓",
            "BTC / CL / XAU / NG 15分钟：保持关闭（无开仓授权）",
            "BTC、XAU 15分钟守护进程在线（监测）",
        ]
    else:
        fifteen_lines = ["15分钟模块配置或进程异常"]
        if not xrp_15m_ok:
            fifteen_lines.append("XRP 15分钟未按预期授权运行")
        if not fifteen_idle_ok:
            fifteen_lines.extend(fifteen_notes or ["存在非预期 15分钟开仓授权"])
        if not btc_15m_proc_ok:
            fifteen_lines.append("BTC 15分钟守护进程未在线")
        if not xau_15m_proc_ok:
            fifteen_lines.append("XAU 15分钟守护进程未在线")
    add(
        "dual_timeframe_modules",
        "多周期自动交易模块",
        "healthy" if fifteen_minute_ok else "error",
        "\n".join(fifteen_lines),
        detail_lines=fifteen_lines,
    )

    # --- 5m live layer ---
    five_minute_expected = {
        "BTC-USDT-SWAP": ["btc5_trend_rebound_ada5_clone_v1"],
        "ETH-USDT-SWAP": ["eth5_trend_rebound_ada5_clone_v1"],
        "SOL-USDT-SWAP": ["sol5_trend_rebound_ada5_clone_v1"],
        "XRP-USDT-SWAP": ["xrp5_trend_rebound_ada5_clone_v1"],
        "CL-USDT-SWAP": [],
        "NG-USDT-SWAP": ["ng5_exhaustion_fade_short_ai"],
        "XAU-USDT-SWAP": [],
        "XAG-USDT-SWAP": [],
        "LTC-USDT-SWAP": ["ltc5_exhaustion_fade_short_ai"],
        "ADA-USDT-SWAP": ["codex0725t3_ada5m_trendpb_r42_z2p3_h14"],
    }
    five_minute_pid = {
        "BTC-USDT-SWAP": btc_5m_daemon_pid,
        "ETH-USDT-SWAP": eth_5m_daemon_pid,
        "SOL-USDT-SWAP": sol_5m_daemon_pid,
        "XRP-USDT-SWAP": xrp_5m_daemon_pid,
        "CL-USDT-SWAP": cl_5m_daemon_pid,
        "NG-USDT-SWAP": ng_5m_daemon_pid,
        "XAU-USDT-SWAP": None,  # no dedicated 5m daemon required
        "XAG-USDT-SWAP": xag_5m_daemon_pid,
        "LTC-USDT-SWAP": ltc_5m_daemon_pid,
        "ADA-USDT-SWAP": ada_5m_daemon_pid,
    }
    five_minute_ok = True
    five_active = []
    five_issues = []
    for symbol, expected in five_minute_expected.items():
        key = symbol.split("-")[0].lower()
        config = _vector_load_json(Path("/root/auto_trade/formal_daemon_config_%s_5m.json" % key), {})
        active_expected = bool(expected)
        cfg_ok = isinstance(config, dict) and _cfg_keys(config) == expected
        open_ok = bool(config.get("allow_auto_open")) == active_expected if isinstance(config, dict) else False
        pid = five_minute_pid.get(symbol)
        proc_ok = True
        if pid is not None:
            # Idle modules still keep monitoring daemons for BTC/CL/XAG.
            proc_ok = bool(pid in daemon_pid_set)
        if active_expected:
            proc_ok = bool(pid and pid in daemon_pid_set)
        row_ok = cfg_ok and open_ok and proc_ok
        five_minute_ok = five_minute_ok and row_ok
        label = symbol.split("-")[0]
        if active_expected and row_ok:
            five_active.append(label)
        if not row_ok:
            five_issues.append(label)
    if five_minute_ok:
        five_lines = [
            "可开仓：%s 5分钟" % ("、".join(five_active) if five_active else "无"),
            "CL / XAG / XAU 5分钟：关闭或待命",
        ]
    else:
        five_lines = [
            "5分钟配置或进程状态异常",
            "异常标的：" + ("、".join(five_issues) if five_issues else "未知"),
        ]
    add("five_minute_live", "5分钟实盘层",
        "healthy" if five_minute_ok else "error",
        "\n".join(five_lines), detail_lines=five_lines)

    fifteen_minute_data = {}
    for symbol in (
        "BTC-USDT-SWAP",
        "CL-USDT-SWAP",
        "XAU-USDT-SWAP",
        "NG-USDT-SWAP",
    ):
        metadata = _vector_load_json(
            Path(
                "/root/market_data/%s/15m/data_provenance.json"
                % symbol
            ),
            {},
        )
        fifteen_minute_data[symbol] = metadata
    fifteen_minute_data_ok = all(
        isinstance(metadata, dict)
        and int(metadata.get("rows") or 0) >= 1000
        and int(metadata.get("non_expected_gap_count") or 0) == 0
        and metadata.get("bar") == "15m"
        for metadata in fifteen_minute_data.values()
    )
    if fifteen_minute_data_ok:
        data_lines = [
            "%s %s根" % (symbol.split("-")[0], int((metadata or {}).get("rows") or 0))
            for symbol, metadata in fifteen_minute_data.items()
        ]
        data_lines.append("全部为 OKX 确认 K 线，无非预期间隔")
    else:
        data_lines = ["BTC、CL、XAU 或 NG 的 15 分钟本地历史数据尚未完整就绪"]
    add(
        "backtest_15m_data",
        "15分钟回测数据",
        "healthy" if fifteen_minute_data_ok else "error",
        "\n".join(data_lines),
        detail_lines=data_lines,
    )

    executor_status = {}
    try:
        import auto_trade_formal_v6_executor as executor
        executor_status = executor.get_status()
    except Exception as e:
        executor_status = {"ok": False, "error": str(e)}
    xau_executor_state = _vector_load_json(Path("/root/auto_trade/formal_v6_state_xau.json"), {})
    cl_executor_state = _vector_load_json(Path("/root/auto_trade/formal_v6_state_cl.json"), {})
    ng_executor_state = _vector_load_json(Path("/root/auto_trade/formal_v6_state_ng.json"), {})
    xag_executor_state = _vector_load_json(Path("/root/auto_trade/formal_v6_state_xag.json"), {})
    ltc_executor_state = _vector_load_json(Path("/root/auto_trade/formal_v6_state_ltc.json"), {})
    ada_executor_state = _vector_load_json(Path("/root/auto_trade/formal_v6_state_ada.json"), {})
    xrp_executor_state = _vector_load_json(Path("/root/auto_trade/formal_v6_state_xrp.json"), {})
    executor_ok = bool(
        executor_status.get("ok")
        and isinstance(xau_executor_state, dict)
        and isinstance(cl_executor_state, dict)
        and isinstance(ng_executor_state, dict)
        and isinstance(xag_executor_state, dict)
        and isinstance(ltc_executor_state, dict)
        and isinstance(ada_executor_state, dict)
    )
    current = executor_status.get("current")
    xau_current = xau_executor_state.get("current") if isinstance(xau_executor_state, dict) else None
    cl_current = cl_executor_state.get("current") if isinstance(cl_executor_state, dict) else None
    ng_current = ng_executor_state.get("current") if isinstance(ng_executor_state, dict) else None
    xag_current = xag_executor_state.get("current") if isinstance(xag_executor_state, dict) else None
    ltc_current = ltc_executor_state.get("current") if isinstance(ltc_executor_state, dict) else None
    ada_current = ada_executor_state.get("current") if isinstance(ada_executor_state, dict) else None
    xrp_current = xrp_executor_state.get("current") if isinstance(xrp_executor_state, dict) else None

    def _pos_label(pos):
        return "持仓中" if isinstance(pos, dict) and pos else "无持仓"

    if executor_ok:
        executor_lines = [
            "BTC %s" % _pos_label(current),
            "CL %s" % _pos_label(cl_current),
            "XAU %s" % _pos_label(xau_current),
            "NG %s" % _pos_label(ng_current),
            "XAG %s" % _pos_label(xag_current),
            "LTC %s" % _pos_label(ltc_current),
            "ADA %s" % _pos_label(ada_current),
            "XRP %s" % _pos_label(xrp_current),
        ]
    else:
        executor_lines = ["执行器读取失败：%s" % executor_status.get("error")]
    add("executor", "交易执行器", "healthy" if executor_ok else "error",
        "\n".join(executor_lines), detail_lines=executor_lines)

    active_positions = [
        x for x in (current, cl_current, xau_current, ng_current, xag_current, ltc_current, ada_current, xrp_current)
        if isinstance(x, dict)
    ]
    if active_positions:
        protected = all(bool(x.get("exchange_side_stop_verified")) for x in active_positions)
        protection_detail = "全部持仓的交易所止损已确认" if protected else "存在持仓的交易所止损尚未确认"
        add("position_protection", "持仓保护", "healthy" if protected else "error", protection_detail)
    else:
        add("position_protection", "持仓保护", "healthy", "当前无持仓，无需止损保护")

    try:
        import auto_trade_formal_notify as notify
        notify_status = notify.get_status()
        notify_ok = bool(notify_status.get("notification_real_channel_ready"))
        notify_detail = "WxPusher通道可用" if notify_ok else "WxPusher通道未就绪"
    except Exception as e:
        notify_ok = False
        notify_detail = "通知状态读取失败：%s" % e
    add("notification", "交易事件通知", "healthy" if notify_ok else "warning",
        notify_detail, critical=False)

    critical = [c for c in components if c["critical"]]
    healthy_count = sum(1 for c in components if c["healthy"])
    overall = "healthy" if all(c["healthy"] for c in critical) else "error"
    return {
        "ok": True,
        "overall": overall,
        "summary": "核心服务正常" if overall == "healthy" else "检测到核心服务异常",
        "healthy_count": healthy_count,
        "total_count": len(components),
        "components": components,
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.route("/api/process_status")
@auth.login_required
def process_status():
    return jsonify(build_process_status_payload())



def parse_bool_param(value):
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(value)


def is_strategy_enabled_for_backtest(strategy_key):
    data = load_strategy_config()
    for item in data.get("strategies", []):
        if item.get("key") == strategy_key:
            return item.get("enabled") is not False
    raise ValueError("未知策略：" + str(strategy_key))


def strategy_supports_backtest_scope(item,instrument,timeframe):
    supported_timeframes = item.get("supported_timeframes")
    if (
        isinstance(supported_timeframes,list)
        and supported_timeframes
        and timeframe not in supported_timeframes
    ):
        return False
    supported_instruments = item.get("supported_instruments")
    if (
        isinstance(supported_instruments,list)
        and supported_instruments
        and instrument not in supported_instruments
    ):
        return False
    return True


def make_json_safe(obj):
    try:
        import numpy as np
        import pandas as pd
    except Exception:
        np = None
        pd = None

    if obj is None:
        return None

    if isinstance(obj, (str, int, float, bool)):
        return obj

    if np is not None:
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return [make_json_safe(x) for x in obj.tolist()]

    if pd is not None:
        if isinstance(obj, pd.Timestamp):
            return obj.strftime("%Y-%m-%d %H:%M:%S")
        if obj is pd.NaT:
            return None

    if isinstance(obj, dict):
        return {str(k): make_json_safe(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple, set)):
        return [make_json_safe(x) for x in obj]

    try:
        return obj.item()
    except Exception:
        return str(obj)


@app.route("/api/backtest", methods=["POST"])
@auth.login_required
def run_bt():
    req = request.get_json() or {}
    _risk_payload = req if isinstance(req, dict) else (request.get_json(silent=True) or {})
    if not isinstance(_risk_payload, dict):
        _risk_payload = {}
    _bt_leverage, _bt_stop_loss_ratio, _bt_risk_error = _parse_backtest_risk_params(_risk_payload)
    if _bt_risk_error:
        return jsonify({"error": _bt_risk_error}), 400

    start_raw = req.get("start_time", "").replace("T", " ")
    end_raw = req.get("end_time", "").replace("T", " ")
    instrument = str(req.get("instrument") or "BTC-USDT-SWAP").upper()
    if instrument not in RESEARCH_INSTRUMENTS:
        return jsonify({"error": "不支持的回测标的"}), 400
    try:
        timeframe = backtest_engine_v2.normalize_timeframe(
            req.get("timeframe") or "1h"
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    strategy = req.get("strategy", "")
    strategies = req.get("strategies")
    if isinstance(strategies, list):
        strategies = [str(x).strip() for x in strategies if str(x).strip()]
    else:
        strategies = []
    if not strategies and strategy:
        strategies = [strategy]
    # Preserve selection order while rejecting duplicates.
    strategies = list(dict.fromkeys(strategies))

    if not start_raw or not end_raw or not strategies:
        return jsonify({"error": "回测请求参数缺失"}), 400

    allow_disabled = parse_bool_param(req.get("allow_disabled", False))
    try:
        disabled_selected = [x for x in strategies if not is_strategy_enabled_for_backtest(x)]
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    if disabled_selected and (not allow_disabled):
        return jsonify({
            "error": "选中的策略包含已停用项，默认禁止回测。如需强制回测，请传 allow_disabled=true。",
            "strategies": disabled_selected,
            "enabled": False
        }), 400

    try:
        start_dt = datetime.strptime(start_raw, "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(end_raw, "%Y-%m-%d %H:%M")
        if start_dt >= end_dt:
            return jsonify({"error": "开始时间不能大于或等于结束时间"}), 400
    except ValueError:
        return jsonify({"error": "时间格式不合规"}), 400

    try:
        valid_keys = {
            s["key"]
            for s in get_strategy_list()
            if s.get("enabled",True)
            and strategy_supports_backtest_scope(
                s,instrument,timeframe
            )
        }
    except Exception as e:
        return jsonify({"error": f"策略配置读取失败：{e}"}), 500

    invalid = [x for x in strategies if x not in valid_keys]
    if invalid:
        return jsonify({
            "error":"未知、未启用或不适用于当前标的/周期的策略："
            +"、".join(invalid)
        }),400

    tid = str(int(time.time() * 1000))
    bt_tasks[tid] = {"cur": 0, "total": 0, "done": False, "res": None}

    def _task():
        try:
            results = []
            total_selected = len(strategies)
            for index, strategy_key in enumerate(strategies):
                bt_tasks[tid]["cur"] = index
                bt_tasks[tid]["total"] = total_selected
                result = backtest_engine_v2.run_backtest(
                    strategy_key, instrument, start_raw, end_raw,
                    _bt_leverage, _bt_stop_loss_ratio,
                    timeframe=timeframe,
                )
                if isinstance(result, dict):
                    result["strategy"] = strategy_key
                    result["instrument"] = instrument
                    result["timeframe"] = timeframe
                    for trade in result.get("trades") or []:
                        if isinstance(trade, dict):
                            trade["strategy"] = strategy_key
                results.append({"strategy": strategy_key, "result": result})
                bt_tasks[tid]["cur"] = index + 1
            if total_selected == 1:
                bt_tasks[tid]["res"] = results[0]["result"]
            else:
                valid = [x for x in results if not (x.get("result") or {}).get("error")]
                all_trades = []
                total_return = 0.0
                wins = 0
                for item in valid:
                    rr = item.get("result") or {}
                    total_return += float(rr.get("total_return_percent") or 0.0)
                    for trade in rr.get("trades") or []:
                        row = dict(trade)
                        row["strategy"] = item["strategy"]
                        all_trades.append(row)
                        if row.get("profit"):
                            wins += 1
                all_trades.sort(key=lambda x: (str(x.get("entry_time") or ""), str(x.get("strategy") or "")))
                compound_equity = 1.0
                compound_trades = sorted(all_trades, key=lambda x: (
                    str(x.get("exit_time") or ""), str(x.get("entry_time") or ""),
                    str(x.get("strategy") or "")
                ))
                for trade in compound_trades:
                    compound_equity *= (1.0 + float(trade.get("pnl_ratio") or 0.0))
                bt_tasks[tid]["res"] = {
                    "combination": True,
                    "instrument": instrument,
                    "timeframe": timeframe,
                    "timeframe_label": backtest_engine_v2.TIMEFRAME_SPECS[
                        timeframe
                    ]["label"],
                    "strategy_count": total_selected,
                    "strategies": strategies,
                    "strategy_results": results,
                    "allocation_mode": "equal_weight",
                    "total_return_percent": (total_return / len(valid)) if valid else 0.0,
                    "compound_return_percent": (compound_equity - 1.0) * 100.0,
                    "compound_calculation": "shared_equity_exit_time_order",
                    "total_trades": len(all_trades),
                    "win_rate_percent": (wins / len(all_trades) * 100.0) if all_trades else 0.0,
                    "trades": all_trades,
                    "failed_strategy_count": total_selected - len(valid)
                }
        except Exception as e:
            bt_tasks[tid]["res"] = {"error": str(e)}
        finally:
            bt_tasks[tid]["done"] = True

    threading.Thread(target=_task, daemon=True).start()
    return jsonify({"status": "ok", "task_id": tid})


@app.route("/api/backtest/data_coverage")
@auth.login_required
def backtest_data_coverage():
    instruments = RESEARCH_INSTRUMENTS
    timeframes = ("1h", "15m", "5m")
    requested_instrument = str(
        request.args.get("instrument") or "BTC-USDT-SWAP"
    ).upper()
    if requested_instrument not in instruments:
        return jsonify({"error": "不支持的回测标的"}), 400
    try:
        requested_timeframe = backtest_engine_v2.normalize_timeframe(
            request.args.get("timeframe") or "1h"
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    datasets = {}
    for instrument in instruments:
        datasets[instrument] = {}
        for timeframe in timeframes:
            metadata_path = Path(
                "/root/market_data/%s/%s/data_provenance.json"
                % (instrument, timeframe)
            )
            if (
                instrument == "BTC-USDT-SWAP"
                and timeframe == "1h"
                and not metadata_path.exists()
            ):
                metadata_path = Path(
                    "/root/market_data/BTC-USDT/1h/data_provenance.json"
                )
            metadata = {}
            if metadata_path.exists():
                try:
                    metadata = json.loads(
                        metadata_path.read_text(encoding="utf-8")
                    )
                except Exception as exc:
                    metadata = {"error": str(exc)}
            metadata = dict(metadata or {})
            metadata["available"] = bool(metadata) and not metadata.get(
                "error"
            )
            metadata["instrument"] = instrument
            metadata["timeframe"] = timeframe
            metadata["timeframe_label"] = (
                backtest_engine_v2.TIMEFRAME_SPECS[timeframe]["label"]
            )
            datasets[instrument][timeframe] = metadata
    selected = datasets[requested_instrument][requested_timeframe]
    return jsonify({
        "ok": bool(selected.get("available")),
        "instrument": requested_instrument,
        "timeframe": requested_timeframe,
        "selected": selected,
        "datasets": datasets,
        # Backwards compatibility for older front-end clients.
        "btc": datasets["BTC-USDT-SWAP"]["1h"],
    })


@app.route("/api/backtest_progress/<tid>")
@auth.login_required
def bt_progress(tid):
    t = bt_tasks.get(tid)
    if not t:
        return jsonify({"status": "error", "msg": "未找到该回测任务"}), 404
    return jsonify(make_json_safe({
        "status": "done" if t["done"] else "running",
        "current": t["cur"],
        "total": t["total"],
        "result": t["res"]
    }))


@app.route("/api/repair", methods=["POST"])
@auth.login_required
def repair():
    os.system("screen -X -S cci_75_100 quit 2>/dev/null; screen -dmS cci_75_100 python3 /root/strategy_cci_75_100.py")
    os.system("screen -X -S cci75_110_short quit 2>/dev/null; screen -dmS cci75_110_short python3 /root/strategy_cci75_110_short.py")
    return jsonify({"msg": "正式策略监控进程已重启"})


@app.route("/api/ai_fix_all", methods=["POST"])
@auth.login_required
def ai_fix_all():
    os.system("screen -dmS cci_75_100 python3 /root/strategy_cci_75_100.py")
    os.system("screen -dmS cci75_110_short python3 /root/strategy_cci75_110_short.py")
    # Daily 10:00 generic status report disabled.  Trade-event notifications
    # continue through auto_trade_formal_notify.py using the same WxPusher channel.
    return jsonify({"msg": "全局守护进程已重新拉起"})


@app.route("/api/clear_signals", methods=["POST"])
@auth.login_required
def clear_signals():
    write_signals([])
    return jsonify({"msg": "活跃信号已清空"})


@app.route("/api/calibrate", methods=["POST"])
@auth.login_required
def calibrate():
    try:
        o, c, h, l, _ = get_klines(200)
        k, d, j = kdj(h, l, c)
        msg = (
            "指标校准播报：\n"
            f"价格：{c[-1] if c else 0}\n"
            f"EMA7：{ema(c, 7):.2f}\n"
            f"EMA8：{ema(c, 8):.2f}\n"
            f"EMA23：{ema(c, 23):.2f}\n"
            f"EMA32：{ema(c, 32):.2f}\n"
            f"K：{k:.2f}\n"
            f"D：{d:.2f}\n"
            f"J：{j:.2f}\n"
            f"CCI：{cci(h, l, c):.2f}"
        )
        send_wx(msg)
    except Exception:
        pass
    return jsonify({"status": "ok"})





def sha256_file_short(path):
    try:
        import hashlib
        p = Path(path)
        if not p.exists():
            return "missing"
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception as e:
        return "error:" + str(e)


def read_recent_version_records(limit=50):
    vf = Path("/root/strategy_versions/version_history.jsonl")
    if not vf.exists():
        return []
    rows = []
    for line in vf.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


@app.route("/api/strategy_config/export")
@auth.login_required
def strategy_config_export():
    try:
        raw_limit = request.args.get("limit", "50")
        try:
            limit = int(raw_limit)
        except Exception:
            limit = 50
        if limit <= 0:
            limit = 50
        if limit > 500:
            limit = 500

        config = load_strategy_config()
        records = read_recent_version_records(limit)

        payload = {
            "status": "ok",
            "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "config_path": str(STRATEGY_CONFIG_PATH),
            "version_file": "/root/strategy_versions/version_history.jsonl",
            "limit": limit,
            "hash": {
                "strategy_config_hash": sha256_file_short(STRATEGY_CONFIG_PATH),
                "strategy_logic_hash": sha256_file_short("/root/strategy_logic.py"),
                "backtest_engine_hash": sha256_file_short("/root/backtest_engine_v2.py")
            },
            "config": config,
            "recent_version_records": records
        }

        return jsonify(payload)

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route("/api/version_records")
@auth.login_required
def version_records():
    try:
        vf = Path("/root/strategy_versions/version_history.jsonl")

        raw_limit = request.args.get("limit", "50")
        try:
            limit = int(raw_limit)
        except Exception:
            limit = 50

        if limit <= 0:
            limit = 50
        if limit > 500:
            limit = 500

        strategy_filter = str(request.args.get("strategy", "") or "").strip()
        event_type_filter = str(request.args.get("event_type", "") or "").strip()

        if not vf.exists():
            return jsonify({
                "status": "ok",
                "version_file": str(vf),
                "count": 0,
                "total_matched": 0,
                "limit": limit,
                "filters": {
                    "strategy": strategy_filter,
                    "event_type": event_type_filter
                },
                "records": []
            })

        records = []
        lines = vf.read_text(encoding="utf-8", errors="ignore").splitlines()

        for line in lines:
            try:
                item = json.loads(line)
            except Exception:
                continue

            if strategy_filter and str(item.get("strategy", "")) != strategy_filter:
                continue

            if event_type_filter and str(item.get("event_type", "")) != event_type_filter:
                continue

            records.append(item)

        limited = records[-limit:]

        return jsonify({
            "status": "ok",
            "version_file": str(vf),
            "count": len(limited),
            "total_matched": len(records),
            "limit": limit,
            "filters": {
                "strategy": strategy_filter,
                "event_type": event_type_filter
            },
            "records": limited
        })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500



# AUTO_TRADE_API_ROUTES_START

def _auto_trade_api_error(message, status=400):
    return jsonify({
        "ok": False,
        "error": str(message)
    }), status

def _auto_trade_auth_ok():
    auth = request.authorization
    if not auth:
        return False
    return auth.username == "quant" and auth.password == "btc2026"

def _auto_trade_require_auth():
    if not _auto_trade_auth_ok():
        return _auto_trade_api_error("unauthorized", 401)
    return None

def _auto_trade_payload():
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("request body must be JSON object")
    return data


def _auto_trade_call(fn, *args, **kwargs):
    try:
        return jsonify(fn(*args, **kwargs))
    except ValueError as e:
        if e.__class__.__name__ == "AutoTradeBusyError" or "runtime lock busy" in str(e):
            return _auto_trade_api_error(str(e), 409)
        return _auto_trade_api_error(str(e), 400)
    except Exception as e:
        if e.__class__.__name__ == "AutoTradeRuntimeBusy" or "runtime lock busy" in str(e):
            return _auto_trade_api_error(str(e), 409)
        return _auto_trade_api_error(str(e), 500)

def api_auto_trade_status():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_manager as auto_trade_manager
    return _auto_trade_call(auto_trade_manager.get_auto_trade_status)

@app.route("/api/auto_trade/config", methods=["GET"])
def api_auto_trade_config():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_manager as auto_trade_manager
    return _auto_trade_call(auto_trade_manager.get_auto_trade_config)

@app.route("/api/auto_trade/events", methods=["GET"])
def api_auto_trade_events():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_manager as auto_trade_manager

    try:
        limit = int(request.args.get("limit", 100))
    except Exception:
        limit = 100

    if limit <= 0:
        limit = 100
    if limit > 1000:
        limit = 1000

    return _auto_trade_call(auto_trade_manager.get_auto_trade_events, limit)

@app.route("/api/auto_trade/strategy_config", methods=["POST"])
def api_auto_trade_strategy_config():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_manager as auto_trade_manager
    return _auto_trade_call(auto_trade_manager.update_strategy_auto_config, _auto_trade_payload())

@app.route("/api/auto_trade/start", methods=["POST"])
def api_auto_trade_start():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_manager as auto_trade_manager
    data = _auto_trade_payload()
    return _auto_trade_call(auto_trade_manager.start_auto_trade, data.get("mode"))

@app.route("/api/auto_trade/pause", methods=["POST"])
def api_auto_trade_pause():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_manager as auto_trade_manager
    return _auto_trade_call(auto_trade_manager.pause_auto_trade)

@app.route("/api/auto_trade/resume", methods=["POST"])
def api_auto_trade_resume():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_manager as auto_trade_manager
    return _auto_trade_call(auto_trade_manager.resume_auto_trade)

@app.route("/api/auto_trade/strategy_pause", methods=["POST"])
def api_auto_trade_strategy_pause():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_manager as auto_trade_manager
    data = _auto_trade_payload()
    key = data.get("strategy_key") or data.get("key")
    return _auto_trade_call(auto_trade_manager.pause_strategy, key)

@app.route("/api/auto_trade/strategy_resume", methods=["POST"])
def api_auto_trade_strategy_resume():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_manager as auto_trade_manager
    data = _auto_trade_payload()
    key = data.get("strategy_key") or data.get("key")
    return _auto_trade_call(auto_trade_manager.resume_strategy, key)

@app.route("/api/auto_trade/refresh", methods=["POST"])
def api_auto_trade_refresh():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_manager as auto_trade_manager
    return _auto_trade_call(auto_trade_manager.refresh_auto_trade_once)


# AUTO_TRADE_HEALTH_API_ROUTES_START

@app.route("/api/auto_trade/health", methods=["GET"])
def api_auto_trade_health():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_health as auto_trade_health
    return _auto_trade_call(auto_trade_health.get_auto_trade_health)


@app.route("/api/auto_trade/health_summary", methods=["GET"])
def api_auto_trade_health_summary():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_health as auto_trade_health
    return _auto_trade_call(auto_trade_health.get_auto_trade_health_summary)


@app.route("/api/auto_trade/live_ready", methods=["GET"])
def api_auto_trade_live_ready():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_health as auto_trade_health
    return _auto_trade_call(auto_trade_health.get_live_ready_status)


def _auto_trade_reconcile_from_payload():
    try:
        import json as _json
        from flask import request as _request

        raw = _request.get_data(as_text=True) or ""

        if raw.strip():
            try:
                payload = _json.loads(raw)
            except Exception as e:
                raise ValueError("invalid JSON payload: " + str(e))
        else:
            payload = {}

    except ValueError:
        raise
    except Exception as e:
        raise ValueError("invalid JSON payload: " + str(e))

    if payload is None:
        payload = {}

    if not isinstance(payload, dict):
        raise ValueError("JSON payload must be an object")

    dry_run = payload.get("dry_run", True)

    import auto_trade_health as auto_trade_health
    return auto_trade_health.reconcile_auto_trade_system(dry_run=dry_run)


@app.route("/api/auto_trade/reconcile", methods=["POST"])
def api_auto_trade_reconcile():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    return _auto_trade_call(_auto_trade_reconcile_from_payload)

# AUTO_TRADE_HEALTH_API_ROUTES_END


# AUTO_TRADE_OKX_API_ROUTES_START

@app.route("/api/auto_trade/okx/status", methods=["GET"])
def api_auto_trade_okx_status():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    from flask import request as _request
    check_network = str(_request.args.get("check_network", "false")).lower() in ["1", "true", "yes"]
    symbol = _request.args.get("symbol", "BTC-USDT-SWAP")

    import auto_trade_okx as auto_trade_okx
    return _auto_trade_call(auto_trade_okx.get_okx_api_status, check_network=check_network, symbol=symbol)


@app.route("/api/auto_trade/okx/credentials", methods=["GET"])
def api_auto_trade_okx_credentials():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_okx as auto_trade_okx
    return _auto_trade_call(auto_trade_okx.get_okx_credentials_status)


@app.route("/api/auto_trade/okx/balance", methods=["GET"])
def api_auto_trade_okx_balance():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    from flask import request as _request
    ccy = _request.args.get("ccy", "USDT")

    import auto_trade_okx as auto_trade_okx
    return _auto_trade_call(auto_trade_okx.get_okx_account_balance, ccy=ccy)


@app.route("/api/auto_trade/okx/positions", methods=["GET"])
def api_auto_trade_okx_positions():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    from flask import request as _request
    symbol = _request.args.get("symbol", "BTC-USDT-SWAP")

    import auto_trade_okx as auto_trade_okx
    return _auto_trade_call(auto_trade_okx.get_okx_positions, symbol=symbol)


@app.route("/api/auto_trade/okx/instrument", methods=["GET"])
def api_auto_trade_okx_instrument():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    from flask import request as _request
    symbol = _request.args.get("symbol", "BTC-USDT-SWAP")

    import auto_trade_okx as auto_trade_okx
    return _auto_trade_call(auto_trade_okx.get_okx_instrument_info, symbol=symbol)


@app.route("/api/auto_trade/okx/ticker", methods=["GET"])
def api_auto_trade_okx_ticker():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    from flask import request as _request
    symbol = _request.args.get("symbol", "BTC-USDT-SWAP")

    import auto_trade_okx as auto_trade_okx
    return _auto_trade_call(auto_trade_okx.get_okx_ticker, symbol=symbol)


@app.route("/api/auto_trade/okx/leverage", methods=["GET"])
def api_auto_trade_okx_leverage():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    from flask import request as _request
    symbol = _request.args.get("symbol", "BTC-USDT-SWAP")
    mgn_mode = _request.args.get("mgn_mode", "cross")

    import auto_trade_okx as auto_trade_okx
    return _auto_trade_call(auto_trade_okx.get_okx_leverage, symbol=symbol, mgn_mode=mgn_mode)


def _auto_trade_okx_payload_from_raw_body():
    try:
        import json as _json
        from flask import request as _request

        raw = _request.get_data(as_text=True) or ""
        if raw.strip():
            try:
                payload = _json.loads(raw)
            except Exception as e:
                raise ValueError("invalid JSON payload: " + str(e))
        else:
            payload = {}
    except ValueError:
        raise
    except Exception as e:
        raise ValueError("invalid JSON payload: " + str(e))

    if payload is None:
        payload = {}

    if not isinstance(payload, dict):
        raise ValueError("JSON payload must be an object")

    return payload


def _auto_trade_okx_build_order_payload_from_api():
    payload = _auto_trade_okx_payload_from_raw_body()

    import auto_trade_okx as auto_trade_okx
    return auto_trade_okx.build_okx_order_payload(
        symbol=payload.get("symbol", "BTC-USDT-SWAP"),
        side=payload.get("side"),
        local_side=payload.get("local_side"),
        pos_side=payload.get("posSide") or payload.get("pos_side"),
        td_mode=payload.get("tdMode") or payload.get("td_mode", "cross"),
        ord_type=payload.get("ordType") or payload.get("ord_type", "market"),
        sz=payload.get("sz"),
        notional_usdt=payload.get("notional_usdt"),
        price=payload.get("price"),
        reduce_only=bool(payload.get("reduceOnly") or payload.get("reduce_only")),
        client_order_id=payload.get("clOrdId") or payload.get("client_order_id")
    )


@app.route("/api/auto_trade/okx/order_payload", methods=["POST"])
def api_auto_trade_okx_order_payload():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    return _auto_trade_call(_auto_trade_okx_build_order_payload_from_api)

# AUTO_TRADE_OKX_API_ROUTES_END




# AUTO_TRADE_EXECUTION_API_ROUTES_START

@app.route("/api/auto_trade/execution/status", methods=["GET"])
def api_auto_trade_execution_status():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_execution as auto_trade_execution
    return _auto_trade_call(auto_trade_execution.get_execution_status)


def _auto_trade_execution_payload_from_raw_body():
    try:
        import json as _json
        from flask import request as _request

        raw = _request.get_data(as_text=True) or ""

        if raw.strip():
            try:
                payload = _json.loads(raw)
            except Exception as e:
                raise ValueError("invalid JSON payload: " + str(e))
        else:
            payload = {}

    except ValueError:
        raise
    except Exception as e:
        raise ValueError("invalid JSON payload: " + str(e))

    if payload is None:
        payload = {}

    if not isinstance(payload, dict):
        raise ValueError("JSON payload must be an object")

    return payload


def _auto_trade_execution_require_strategy(payload):
    strategy_key = payload.get("strategy_key")
    if not strategy_key:
        raise ValueError("strategy_key required")
    return strategy_key


@app.route("/api/auto_trade/execution/precheck", methods=["POST"])
def api_auto_trade_execution_precheck():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    def _call():
        payload = _auto_trade_execution_payload_from_raw_body()
        strategy_key = _auto_trade_execution_require_strategy(payload)

        check_okx = payload.get("check_okx")
        if check_okx is not None:
            check_okx = bool(check_okx)

        import auto_trade_execution as auto_trade_execution
        return auto_trade_execution.validate_execution_preconditions(
            strategy_key,
            payload.get("signal_info") or {},
            payload.get("mode"),
            bool(payload.get("submit", False)),
            check_okx
        )

    return _auto_trade_call(_call)


@app.route("/api/auto_trade/execution/paper_entry", methods=["POST"])
def api_auto_trade_execution_paper_entry():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    def _call():
        payload = _auto_trade_execution_payload_from_raw_body()
        strategy_key = _auto_trade_execution_require_strategy(payload)

        import auto_trade_execution as auto_trade_execution
        return auto_trade_execution.execute_paper_entry(
            strategy_key,
            payload.get("signal_info") or {},
            lock=True
        )

    return _auto_trade_call(_call)


@app.route("/api/auto_trade/execution/paper_exit", methods=["POST"])
def api_auto_trade_execution_paper_exit():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    def _call():
        payload = _auto_trade_execution_payload_from_raw_body()

        import auto_trade_execution as auto_trade_execution
        return auto_trade_execution.execute_paper_exit(
            exit_info=payload.get("exit_info") or {},
            lock=True
        )

    return _auto_trade_call(_call)


@app.route("/api/auto_trade/execution/live_dry_run", methods=["POST"])
def api_auto_trade_execution_live_dry_run():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    def _call():
        payload = _auto_trade_execution_payload_from_raw_body()
        strategy_key = _auto_trade_execution_require_strategy(payload)

        check_okx = payload.get("check_okx")
        if check_okx is not None:
            check_okx = bool(check_okx)

        import auto_trade_execution as auto_trade_execution
        return auto_trade_execution.execute_live_entry(
            strategy_key,
            payload.get("signal_info") or {},
            submit=False,
            lock=True,
            check_okx=check_okx
        )

    return _auto_trade_call(_call)


@app.route("/api/auto_trade/execution/live_one_order", methods=["POST"])
def api_auto_trade_execution_live_one_order():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    def _call():
        payload = _auto_trade_execution_payload_from_raw_body()
        strategy_key = _auto_trade_execution_require_strategy(payload)

        dry_run = payload.get("dry_run")
        if dry_run is None:
            dry_run = True
        else:
            dry_run = bool(dry_run)

        check_okx = payload.get("check_okx")
        if dry_run is False:
            check_okx = True
        elif check_okx is not None:
            check_okx = bool(check_okx)
        else:
            check_okx = False

        import auto_trade_execution as auto_trade_execution
        return auto_trade_execution.submit_live_one_order(
            strategy_key,
            payload.get("signal_info") or {},
            manual_confirm=payload.get("manual_confirm"),
            dry_run=dry_run,
            check_okx=check_okx,
            lock=True
        )

    return _auto_trade_call(_call)

# AUTO_TRADE_EXECUTION_API_ROUTES_END


# AUTO_TRADE_EXECUTION_STATE_API_STAGE8_16D_START

@app.route("/api/auto_trade/execution_state/status", methods=["GET"])
def api_auto_trade_execution_state_status():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_execution_state as execution_state
    return _auto_trade_call(execution_state.get_execution_state_status)


@app.route("/api/auto_trade/execution_state/self_test", methods=["POST"])
def api_auto_trade_execution_state_self_test():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_execution_state as execution_state
    return _auto_trade_call(execution_state.self_test_sandbox)


@app.route("/api/auto_trade/execution_state/reconcile", methods=["POST"])
def api_auto_trade_execution_state_reconcile():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    def _call():
        payload = {}
        try:
            import json as _json
            from flask import request as _request
            raw = _request.get_data(as_text=True) or ""
            if raw.strip():
                payload = _json.loads(raw)
        except Exception as e:
            raise ValueError("invalid JSON payload: " + str(e))

        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be an object")

        symbol = payload.get("symbol") or "BTC-USDT-SWAP"

        import auto_trade_execution_state as execution_state
        return execution_state.reconcile_with_okx(symbol)

    return _auto_trade_call(_call)


@app.route("/api/auto_trade/execution_state/import_current", methods=["POST"])
def api_auto_trade_execution_state_import_current():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_execution_state as execution_state
    return _auto_trade_call(execution_state.import_current_live_position)


@app.route("/api/auto_trade/execution_state/register_order", methods=["POST"])
def api_auto_trade_execution_state_register_order():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    def _call():
        payload = {}
        try:
            import json as _json
            from flask import request as _request
            raw = _request.get_data(as_text=True) or ""
            if raw.strip():
                payload = _json.loads(raw)
        except Exception as e:
            raise ValueError("invalid JSON payload: " + str(e))

        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be an object")

        strategy_key = payload.get("strategy_key")
        symbol = payload.get("symbol") or "BTC-USDT-SWAP"
        side = payload.get("side")
        requested_size = payload.get("requested_size") or payload.get("sz")

        if not strategy_key:
            raise ValueError("strategy_key required")
        if not side:
            raise ValueError("side required")
        if requested_size is None:
            raise ValueError("requested_size or sz required")

        import auto_trade_execution_state as execution_state
        return execution_state.register_execution_order(
            strategy_key=strategy_key,
            symbol=symbol,
            side=side,
            requested_size=requested_size,
            order_response=payload.get("order_response"),
            order_payload=payload.get("order_payload") or {},
            source=payload.get("source") or "api"
        )

    return _auto_trade_call(_call)


@app.route("/api/auto_trade/execution_state/replay", methods=["POST"])
def api_auto_trade_execution_state_replay():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_execution_state as execution_state
    return _auto_trade_call(execution_state.replay_wal)

# AUTO_TRADE_EXECUTION_STATE_API_STAGE8_16D_END


# AUTO_TRADE_EVENT_LEDGER_API_STAGE8_17_START

@app.route("/api/auto_trade/event_ledger/status", methods=["GET"])
def api_auto_trade_event_ledger_status():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_event_ledger as event_ledger
    return _auto_trade_call(event_ledger.get_ledger_status)


@app.route("/api/auto_trade/event_ledger/self_test", methods=["POST"])
def api_auto_trade_event_ledger_self_test():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_event_ledger as event_ledger
    return _auto_trade_call(event_ledger.self_test_sandbox)


@app.route("/api/auto_trade/event_ledger/replay", methods=["POST"])
def api_auto_trade_event_ledger_replay():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    def _call():
        import auto_trade_event_ledger as event_ledger
        return event_ledger.replay_ledger(write_snapshot=True)

    return _auto_trade_call(_call)


@app.route("/api/auto_trade/event_ledger/mapping", methods=["GET", "POST"])
def api_auto_trade_event_ledger_mapping():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    def _call():
        payload = {}
        try:
            import json as _json
            from flask import request as _request
            raw = _request.get_data(as_text=True) or ""
            if raw.strip():
                payload = _json.loads(raw)
        except Exception:
            payload = {}

        if not isinstance(payload, dict):
            payload = {}

        symbol = payload.get("symbol") or "BTC-USDT-SWAP"

        import auto_trade_event_ledger as event_ledger
        return event_ledger.build_okx_local_mapping(symbol)

    return _auto_trade_call(_call)


@app.route("/api/auto_trade/event_ledger/reconcile", methods=["POST"])
def api_auto_trade_event_ledger_reconcile():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    def _call():
        payload = {}
        try:
            import json as _json
            from flask import request as _request
            raw = _request.get_data(as_text=True) or ""
            if raw.strip():
                payload = _json.loads(raw)
        except Exception as e:
            raise ValueError("invalid JSON payload: " + str(e))

        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be an object")

        symbol = payload.get("symbol") or "BTC-USDT-SWAP"

        import auto_trade_event_ledger as event_ledger
        return event_ledger.reconcile_with_okx(symbol)

    return _auto_trade_call(_call)


@app.route("/api/auto_trade/event_ledger/import_816", methods=["POST"])
def api_auto_trade_event_ledger_import_816():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_event_ledger as event_ledger
    return _auto_trade_call(event_ledger.import_stage816_state)


@app.route("/api/auto_trade/event_ledger/append", methods=["POST"])
def api_auto_trade_event_ledger_append():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    def _call():
        payload = {}
        try:
            import json as _json
            from flask import request as _request
            raw = _request.get_data(as_text=True) or ""
            if raw.strip():
                payload = _json.loads(raw)
        except Exception as e:
            raise ValueError("invalid JSON payload: " + str(e))

        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be an object")

        event_type = payload.get("event_type")
        if not event_type:
            raise ValueError("event_type required")

        import auto_trade_event_ledger as event_ledger
        return event_ledger.append_event(
            event_type=event_type,
            data=payload.get("data") or {},
            source=payload.get("source") or "api",
            execution_id=payload.get("execution_id"),
            idempotency_key=payload.get("idempotency_key")
        )

    return _auto_trade_call(_call)

# AUTO_TRADE_EVENT_LEDGER_API_STAGE8_17_END


# AUTO_TRADE_AUTONOMOUS_ENGINE_API_STAGE8_18B_START

@app.route("/api/strategy-ecosystem/status", methods=["GET"])
def api_strategy_ecosystem_status():
    """Authenticated, read-only operational view of the learning loop."""
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    try:
        import sqlite3
        import auto_trade_strategy_ecosystem as ecosystem
        value = ecosystem.status()
        conn = sqlite3.connect(str(ecosystem.DB_PATH))
        try:
            value["recent_lifecycle"] = [
                dict(zip(("strategy_key", "symbol", "timeframe", "deviation_state",
                          "action", "created_at"), row))
                for row in conn.execute(
                    "SELECT strategy_key,symbol,timeframe,deviation_state,action,created_at "
                    "FROM lifecycle_metrics ORDER BY created_at DESC LIMIT 20"
                ).fetchall()
            ]
            value["recent_failures"] = [
                dict(zip(("stage", "strategy_key", "symbol", "timeframe",
                          "reason_code", "occurrences", "last_seen"), row))
                for row in conn.execute(
                    "SELECT stage,strategy_key,symbol,timeframe,reason_code,occurrences,last_seen "
                    "FROM failure_experiences ORDER BY last_seen DESC LIMIT 20"
                ).fetchall()
            ]
        finally:
            conn.close()
        return jsonify(value)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

@app.route("/api/auto_trade/autonomous/status", methods=["GET"])
def api_auto_trade_autonomous_status():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_autonomous_engine as autonomous_engine
    return _auto_trade_call(autonomous_engine.get_daemon_status)


@app.route("/api/auto_trade/autonomous/self_test", methods=["POST"])
def api_auto_trade_autonomous_self_test():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_autonomous_engine as autonomous_engine
    return _auto_trade_call(autonomous_engine.self_test_sandbox)


@app.route("/api/auto_trade/autonomous/tick", methods=["POST"])
def api_auto_trade_autonomous_tick():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_autonomous_engine as autonomous_engine
    return _auto_trade_call(lambda: autonomous_engine.engine_tick(reason="api_tick"))


@app.route("/api/auto_trade/autonomous/start", methods=["POST"])
def api_auto_trade_autonomous_start():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_autonomous_engine as autonomous_engine
    return _auto_trade_call(autonomous_engine.start_daemon)


@app.route("/api/auto_trade/autonomous/stop", methods=["POST"])
def api_auto_trade_autonomous_stop():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_autonomous_engine as autonomous_engine
    return _auto_trade_call(autonomous_engine.stop_daemon)


@app.route("/api/auto_trade/autonomous/watchdog/start", methods=["POST"])
def api_auto_trade_autonomous_watchdog_start():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_autonomous_engine as autonomous_engine
    return _auto_trade_call(autonomous_engine.start_watchdog)


@app.route("/api/auto_trade/autonomous/watchdog/stop", methods=["POST"])
def api_auto_trade_autonomous_watchdog_stop():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    import auto_trade_autonomous_engine as autonomous_engine
    return _auto_trade_call(autonomous_engine.stop_watchdog)


@app.route("/api/auto_trade/autonomous/config", methods=["GET", "POST"])
def api_auto_trade_autonomous_config():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    def _call():
        from flask import request as _request
        import json as _json
        import auto_trade_autonomous_engine as autonomous_engine

        if _request.method == "GET":
            return {
                "ok": True,
                "config": autonomous_engine.load_config(create=False)
            }

        payload = {}
        raw = _request.get_data(as_text=True) or ""
        if raw.strip():
            payload = _json.loads(raw)

        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be an object")

        return autonomous_engine.update_config(payload)

    return _auto_trade_call(_call)

# AUTO_TRADE_AUTONOMOUS_ENGINE_API_STAGE8_18B_END




# AUTO_TRADE_STAGE8_FINAL_CONSOLE_API_8_18C_START

def _stage8_final_safe_call(module_name, func_name):
    import importlib
    try:
        m = importlib.import_module(module_name)
        fn = getattr(m, func_name)
        data = fn()
        return {
            "ok": True,
            "module": module_name,
            "function": func_name,
            "data": data
        }
    except Exception as e:
        return {
            "ok": False,
            "module": module_name,
            "function": func_name,
            "error": str(e)
        }


def _stage8_final_component(name, label, module_name, func_name, critical=False):
    res = _stage8_final_safe_call(module_name, func_name)
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    ok = bool(res.get("ok") and (data.get("ok", True) is not False))

    return {
        "name": name,
        "label": label,
        "ok": ok,
        "critical": critical,
        "module": module_name,
        "function": func_name,
        "error": res.get("error"),
        "data": data
    }


def _stage8_final_collect_status():
    components = [
        _stage8_final_component(
            "autonomous",
            "auto_trade_autonomous_engine",
            "get_daemon_status",
            critical=True
        ),
        _stage8_final_component(
            "ledger",
            "auto_trade_event_ledger",
            "get_ledger_status",
            critical=True
        ),
        _stage8_final_component(
            "execution_state",
            "auto_trade_execution_state",
            "get_execution_state_status",
            critical=False
        ),
        _stage8_final_component(
            "temp_test",
            "临时测试模块",
            "auto_trade_temp_test",
            "get_status",
            critical=False
        )
    ]

    by_name = {}
    for c in components:
        by_name[c["name"]] = c

    autonomous = by_name.get("autonomous", {}).get("data") or {}
    ledger = by_name.get("ledger", {}).get("data") or {}
    temp_test = by_name.get("temp_test", {}).get("data") or {}

    safe_defaults = autonomous.get("safe_defaults") or {}
    daemon_running = bool(autonomous.get("daemon_running"))
    watchdog_running = bool(autonomous.get("watchdog_running"))

    critical_ok = True
    for c in components:
        if c.get("critical") and not c.get("ok"):
            critical_ok = False

    live_enabled = bool(safe_defaults.get("allow_live_execution"))
    auto_entry_enabled = bool(safe_defaults.get("allow_auto_entry"))
    auto_exit_enabled = bool(safe_defaults.get("allow_auto_exit"))

    summary = {
        "stage": "8.18C",
        "frontend": "unified_console",
        "ok": critical_ok,
        "backend_core_ok": critical_ok,
        "legacy_console_hidden": True,
        "daemon_running": daemon_running,
        "watchdog_running": watchdog_running,
        "mode": autonomous.get("mode"),
        "safe_mode": not live_enabled,
        "live_enabled": live_enabled,
        "auto_entry_enabled": auto_entry_enabled,
        "auto_exit_enabled": auto_exit_enabled,
        "ledger_events": ledger.get("events"),
        "ledger_executions": ledger.get("executions"),
        "temp_test_open": bool((temp_test.get("current") or {})),
        "live_ready_interpretation": "8.18C uses autonomous safety gates; daemon stopped is SAFE unless user explicitly starts it."
    }

    return {
        "ok": True,
        "stage": "8.18C",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "components": components,
        "deprecated": {
            "old_auto_trade_console": True,
            "old_live_ready_panel": True,
            "old_stage8_2_to_8_14_events_panel": True,
            "reason": "Replaced by unified 8.18C console."
        }
    }


@app.route("/api/auto_trade/stage8_final/status", methods=["GET"])
def api_auto_trade_stage8_final_status():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    from flask import jsonify
    return jsonify(_stage8_final_collect_status())


@app.route("/api/auto_trade/stage8_final/self_test", methods=["POST"])
def api_auto_trade_stage8_final_self_test():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err

    from flask import jsonify

    status = _stage8_final_collect_status()
    components = status.get("components") or []

    names = {}
    for c in components:
        names[c.get("name")] = c

    required = ["autonomous", "ledger", "temp_test"]
    missing = []
    for r in required:
        if r not in names:
            missing.append(r)

    result = {
        "ok": len(missing) == 0,
        "stage": "8.18C",
        "unified_console_api_pass": True,
        "components_found": list(names.keys()),
        "missing_required": missing,
        "legacy_console_hidden_expected": True,
        "backend_trading_logic_untouched": True
    }

    return jsonify(result)

# AUTO_TRADE_STAGE8_FINAL_CONSOLE_API_8_18C_END






# REAL_TEMP_TEST_V6_ATTACHED_SL_STAGE8_18_START

@app.route("/api/auto_trade/temp_test/status", methods=["GET"])
def api_auto_trade_temp_test_status():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_temp_test as temp_test
    return _auto_trade_call(temp_test.get_status)


@app.route("/api/auto_trade/temp_test/self_test", methods=["POST"])
def api_auto_trade_temp_test_self_test():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_temp_test as temp_test
    return _auto_trade_call(temp_test.self_test)


@app.route("/api/auto_trade/temp_test/preflight", methods=["POST"])
def api_auto_trade_temp_test_preflight():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_temp_test as temp_test
    return _auto_trade_call(temp_test.preflight_check)


@app.route("/api/auto_trade/temp_test/real_gate/status", methods=["GET"])
def api_auto_trade_temp_test_real_gate_status():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_temp_test as temp_test
    return _auto_trade_call(temp_test.real_gate_status)


@app.route("/api/auto_trade/temp_test/real_gate/enable", methods=["POST"])
def api_auto_trade_temp_test_real_gate_enable():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    def _call():
        import json as _json
        from flask import request as _request
        import auto_trade_temp_test as temp_test
        payload = {}
        raw = _request.get_data(as_text=True) or ""
        if raw.strip():
            payload = _json.loads(raw)
        if not isinstance(payload, dict):
            payload = {}
        return temp_test.enable_real_gate(
            manual_confirm=payload.get("manual_confirm"),
            ttl_sec=payload.get("ttl_sec")
        )
    return _auto_trade_call(_call)


@app.route("/api/auto_trade/temp_test/real_gate/disable", methods=["POST"])
def api_auto_trade_temp_test_real_gate_disable():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_temp_test as temp_test
    return _auto_trade_call(temp_test.disable_real_gate)


@app.route("/api/auto_trade/temp_test/arm", methods=["POST"])
def api_auto_trade_temp_test_arm():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    def _call():
        import json as _json
        from flask import request as _request
        import auto_trade_temp_test as temp_test
        payload = {}
        raw = _request.get_data(as_text=True) or ""
        if raw.strip():
            payload = _json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be object")
        return temp_test.arm(side=payload.get("side"), sz=payload.get("sz") or payload.get("size"))
    return _auto_trade_call(_call)


@app.route("/api/auto_trade/temp_test/open", methods=["POST"])
def api_auto_trade_temp_test_open():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    def _call():
        import json as _json
        from flask import request as _request
        import auto_trade_temp_test as temp_test
        payload = {}
        raw = _request.get_data(as_text=True) or ""
        if raw.strip():
            payload = _json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be object")
        return temp_test.open_test_position(
            side=payload.get("side"),
            source="frontend_real_temp_test_v6_attached_sl",
            requested_price=payload.get("entry_price") or payload.get("price"),
            manual_confirm=payload.get("manual_confirm"),
            sz=payload.get("sz") or payload.get("size"),
            arm_token=payload.get("arm_token")
        )
    return _auto_trade_call(_call)


@app.route("/api/auto_trade/temp_test/close", methods=["POST"])
def api_auto_trade_temp_test_close():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    def _call():
        import json as _json
        from flask import request as _request
        import auto_trade_temp_test as temp_test
        payload = {}
        raw = _request.get_data(as_text=True) or ""
        if raw.strip():
            payload = _json.loads(raw)
        if not isinstance(payload, dict):
            payload = {}
        return temp_test.close_test_position(
            reason=payload.get("reason") or "frontend_real_manual_close",
            position_id=payload.get("position_id")
        )
    return _auto_trade_call(_call)


@app.route("/api/auto_trade/temp_test/ensure_stop_loss", methods=["POST"])
def api_auto_trade_temp_test_ensure_stop_loss():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    def _call():
        import json as _json
        from flask import request as _request
        import auto_trade_temp_test as temp_test
        payload = {}
        raw = _request.get_data(as_text=True) or ""
        if raw.strip():
            payload = _json.loads(raw)
        if not isinstance(payload, dict):
            payload = {}
        return temp_test.ensure_stop_loss(auto_close_on_fail=payload.get("auto_close_on_fail", True))
    return _auto_trade_call(_call)


@app.route("/api/auto_trade/temp_test/cleanup_dangling_stop", methods=["POST"])
def api_auto_trade_temp_test_cleanup_dangling_stop():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_temp_test as temp_test
    return _auto_trade_call(temp_test.cleanup_dangling_stop)


@app.route("/api/auto_trade/temp_test/monitor/start", methods=["POST"])
def api_auto_trade_temp_test_monitor_start():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_temp_test as temp_test
    return _auto_trade_call(temp_test.start_monitor)


@app.route("/api/auto_trade/temp_test/monitor/stop", methods=["POST"])
def api_auto_trade_temp_test_monitor_stop():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_temp_test as temp_test
    return _auto_trade_call(temp_test.stop_monitor)


@app.route("/api/auto_trade/temp_test/watchdog/start", methods=["POST"])
def api_auto_trade_temp_test_watchdog_start():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_temp_test as temp_test
    return _auto_trade_call(temp_test.start_watchdog)


@app.route("/api/auto_trade/temp_test/watchdog/stop", methods=["POST"])
def api_auto_trade_temp_test_watchdog_stop():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_temp_test as temp_test
    return _auto_trade_call(temp_test.stop_watchdog)


@app.route("/api/auto_trade/temp_test/monitor/tick", methods=["POST"])
def api_auto_trade_temp_test_monitor_tick():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_temp_test as temp_test
    return _auto_trade_call(temp_test.monitor_tick_public)

# REAL_TEMP_TEST_V6_ATTACHED_SL_STAGE8_18_END


# STAGE8_22_FULL_V3_FORMAL_AUTO_TRADE_API_START

@app.route("/api/auto_trade/formal/status", methods=["GET"])
def api_auto_trade_formal_status_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_formal_console as console
    return _auto_trade_call(console.get_status)

@app.route("/api/auto_trade/formal/events", methods=["GET"])
def api_auto_trade_formal_events_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    def _call():
        from flask import request as _request
        import auto_trade_formal_console as console
        try:
            limit = int(_request.args.get("limit") or 20)
        except Exception:
            limit = 20
        return console.get_events(limit)
    return _auto_trade_call(_call)

@app.route("/api/auto_trade/formal/arm", methods=["POST"])
def api_auto_trade_formal_arm_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    def _call():
        import json as _json
        from flask import request as _request
        import auto_trade_formal_v6_executor as ex
        payload = {}
        raw = _request.get_data(as_text=True) or ""
        if raw.strip():
            payload = _json.loads(raw)
        if not isinstance(payload, dict):
            payload = {}
        return ex.arm(side=payload.get("side"), sz=payload.get("sz") or "0.01", ttl_sec=payload.get("ttl_sec") or 90, source="api_formal_arm")
    return _auto_trade_call(_call)

@app.route("/api/auto_trade/formal/executor/status", methods=["GET"])
def api_auto_trade_formal_executor_status_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_formal_v6_executor as ex
    return _auto_trade_call(ex.get_status)

@app.route("/api/auto_trade/formal/executor/self_test", methods=["POST"])
def api_auto_trade_formal_executor_self_test_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_formal_v6_executor as ex
    return _auto_trade_call(ex.self_test)

@app.route("/api/auto_trade/formal/executor/check_stop", methods=["POST"])
def api_auto_trade_formal_executor_check_stop_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_formal_v6_executor as ex
    return _auto_trade_call(ex.check_attached_stop_loss_current)

@app.route("/api/auto_trade/formal/executor/manage_current", methods=["POST"])
def api_auto_trade_formal_executor_manage_current_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_formal_v6_executor as ex
    return _auto_trade_call(ex.manage_current_position)

@app.route("/api/auto_trade/formal/strategy/status", methods=["GET"])
def api_auto_trade_formal_strategy_status_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_strategy_ema6_center_down as strategy
    return _auto_trade_call(strategy.get_status)

@app.route("/api/auto_trade/formal/strategy/self_test", methods=["POST"])
def api_auto_trade_formal_strategy_self_test_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_strategy_ema6_center_down as strategy
    return _auto_trade_call(strategy.self_test)

@app.route("/api/auto_trade/formal/preflight", methods=["POST"])
def api_auto_trade_formal_preflight_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_formal_v6_executor as ex
    return _auto_trade_call(ex.preflight)

@app.route("/api/auto_trade/formal/gate/status", methods=["GET"])
def api_auto_trade_formal_gate_status_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_formal_v6_executor as ex
    return _auto_trade_call(ex.gate_status)

@app.route("/api/auto_trade/formal/gate/enable", methods=["POST"])
def api_auto_trade_formal_gate_enable_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    def _call():
        import json as _json
        from flask import request as _request
        import auto_trade_formal_v6_executor as ex
        payload = {}
        raw = _request.get_data(as_text=True) or ""
        if raw.strip():
            payload = _json.loads(raw)
        if not isinstance(payload, dict):
            payload = {}
        return ex.enable_gate(manual_confirm=payload.get("manual_confirm"), ttl_sec=payload.get("ttl_sec") or 300)
    return _auto_trade_call(_call)

@app.route("/api/auto_trade/formal/gate/disable", methods=["POST"])
def api_auto_trade_formal_gate_disable_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_formal_v6_executor as ex
    return _auto_trade_call(ex.disable_gate)

@app.route("/api/auto_trade/formal/open", methods=["POST"])
def api_auto_trade_formal_open_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    def _call():
        import json as _json
        from flask import request as _request
        import auto_trade_formal_v6_executor as ex
        payload = {}
        raw = _request.get_data(as_text=True) or ""
        if raw.strip():
            payload = _json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be object")
        return ex.submit_entry(side=payload.get("side"), strategy_key=payload.get("strategy_key") or "ema6_center_down_then_fall", manual_confirm=payload.get("manual_confirm"), sz=payload.get("sz") or "0.01", source="formal_auto_trade_panel_stage8_22_full_v3", arm_token=payload.get("arm_token"))
    return _auto_trade_call(_call)

@app.route("/api/auto_trade/formal/close", methods=["POST"])
def api_auto_trade_formal_close_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    def _call():
        import json as _json
        from flask import request as _request
        import auto_trade_formal_v6_executor as ex
        payload = {}
        raw = _request.get_data(as_text=True) or ""
        if raw.strip():
            payload = _json.loads(raw)
        if not isinstance(payload, dict):
            payload = {}
        return ex.close_current(reason=payload.get("reason") or "formal_panel_manual_close")
    return _auto_trade_call(_call)

@app.route("/api/auto_trade/formal/daemon/status", methods=["GET"])
def api_auto_trade_formal_daemon_status_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_formal_daemon as d
    return _auto_trade_call(d.status)

@app.route("/api/auto_trade/formal/daemon/self_test", methods=["POST"])
def api_auto_trade_formal_daemon_self_test_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_formal_daemon as d
    return _auto_trade_call(d.self_test)

@app.route("/api/auto_trade/formal/daemon/tick", methods=["POST"])
def api_auto_trade_formal_daemon_tick_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_formal_daemon as d
    return _auto_trade_call(d.tick)

@app.route("/api/auto_trade/formal/daemon/start", methods=["POST"])
def api_auto_trade_formal_daemon_start_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_formal_daemon as d
    return _auto_trade_call(d.start)

@app.route("/api/auto_trade/formal/daemon/stop", methods=["POST"])
def api_auto_trade_formal_daemon_stop_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_formal_daemon as d
    return _auto_trade_call(d.stop)

@app.route("/api/auto_trade/formal/daemon/config", methods=["GET", "POST"])
def api_auto_trade_formal_daemon_config_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    def _call():
        from flask import request as _request
        import json as _json
        import auto_trade_formal_daemon as d
        if _request.method == "GET":
            return {"ok": True, "config": d.get_config()}
        payload = {}
        raw = _request.get_data(as_text=True) or ""
        if raw.strip():
            payload = _json.loads(raw)
        if not isinstance(payload, dict):
            payload = {}
        return d.update_config(**payload)
    return _auto_trade_call(_call)

@app.route("/api/auto_trade/formal/execution_binding/status", methods=["GET"])
def api_auto_trade_formal_execution_binding_status_stage822_full_v3():
    auth_err = _auto_trade_require_auth()
    if auth_err:
        return auth_err
    import auto_trade_execution as execution
    return _auto_trade_call(execution.get_execution_binding_status)

# STAGE8_22_FULL_V3_FORMAL_AUTO_TRADE_API_END


# STAGE8_23_FINAL_SAFE_NOTIFY_API_START
from flask import request as _stage823_request, jsonify as _stage823_jsonify, Response as _stage823_Response

def _stage823_auth_err():
    auth = _stage823_request.authorization
    if not auth or auth.username != "quant" or auth.password != "btc2026":
        return _stage823_Response("Auth required", 401, {"WWW-Authenticate": 'Basic realm="Login Required"'})
    return None

def _stage823_json_call(fn):
    try:
        return _stage823_jsonify(fn())
    except Exception as e:
        return _stage823_jsonify({"ok": False, "error": str(e)}), 500

@app.before_request
def stage823_final_safe_api_intercept():
    p = _stage823_request.path
    m = _stage823_request.method

    if p == "/api/auto_trade/formal/status" and m == "GET":
        err = _stage823_auth_err()
        if err:
            return err
        def call():
            import auto_trade_formal_console as c
            return c.get_status()
        return _stage823_json_call(call)

    if p == "/api/auto_trade/formal/notify/status" and m == "GET":
        err = _stage823_auth_err()
        if err:
            return err
        def call():
            import auto_trade_formal_notify as notify
            return notify.get_status()
        return _stage823_json_call(call)

    if p == "/api/auto_trade/formal/notify/config" and m in ["GET", "POST"]:
        err = _stage823_auth_err()
        if err:
            return err
        def call():
            import auto_trade_formal_notify as notify
            if m == "GET":
                return {"ok": True, "config": notify.get_config(), "status": notify.get_status()}
            payload = _stage823_request.get_json(silent=True) or {}
            if not isinstance(payload, dict):
                payload = {}
            return notify.set_config(**payload)
        return _stage823_json_call(call)

    if p == "/api/auto_trade/formal/notify/self_test" and m == "POST":
        err = _stage823_auth_err()
        if err:
            return err
        def call():
            import auto_trade_formal_notify as notify
            return notify.self_test()
        return _stage823_json_call(call)

    return None

@app.route("/api/auto_trade/formal/notify/status", methods=["GET"])
def api_auto_trade_formal_notify_status_stage823_final_safe():
    err = _stage823_auth_err()
    if err:
        return err
    def call():
        import auto_trade_formal_notify as notify
        return notify.get_status()
    return _stage823_json_call(call)

@app.route("/api/auto_trade/formal/notify/config", methods=["GET", "POST"])
def api_auto_trade_formal_notify_config_stage823_final_safe():
    err = _stage823_auth_err()
    if err:
        return err
    def call():
        import auto_trade_formal_notify as notify
        if _stage823_request.method == "GET":
            return {"ok": True, "config": notify.get_config(), "status": notify.get_status()}
        payload = _stage823_request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            payload = {}
        return notify.set_config(**payload)
    return _stage823_json_call(call)

@app.route("/api/auto_trade/formal/notify/self_test", methods=["POST"])
def api_auto_trade_formal_notify_self_test_stage823_final_safe():
    err = _stage823_auth_err()
    if err:
        return err
    def call():
        import auto_trade_formal_notify as notify
        return notify.self_test()
    return _stage823_json_call(call)
# STAGE8_23_FINAL_SAFE_NOTIFY_API_END


# STAGE8_23_AUTO_MODE_CONTROL_API_START
from flask import request as _stage823_auto_request, jsonify as _stage823_auto_jsonify, Response as _stage823_auto_Response

def _stage823_auto_auth_err():
    auth = _stage823_auto_request.authorization
    if not auth or auth.username != "quant" or auth.password != "btc2026":
        return _stage823_auto_Response("Auth required", 401, {"WWW-Authenticate": 'Basic realm="Login Required"'})
    return None

def _stage823_auto_read_cfg():
    import json
    from pathlib import Path
    p = Path("/root/auto_trade/formal_daemon_config.json")
    try:
        if p.exists():
            s = p.read_text(encoding="utf-8", errors="ignore")
            cfg = json.loads(s) if s.strip() else {}
        else:
            cfg = {}
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    return cfg

def _stage823_auto_write_cfg(cfg):
    import json
    from pathlib import Path
    p = Path("/root/auto_trade/formal_daemon_config.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

def _stage823_auto_base_cfg():
    return {
        "enabled": True,
        "position_mode": "full_balance",
        "full_position_ratio": 1.0,
        "reserve_usdt": 0,
        "fee_buffer_usdt": 0,
        "take_profit_pct": 0.009,
        "notification_enabled": True,
        "strategy_key": "ema6_center_down_then_fall",
        "symbol": "BTC-USDT-SWAP",
        "tick_interval_sec": 60
    }

def _stage823_auto_apply(mode):
    cfg = _stage823_auto_read_cfg()
    cfg.update(_stage823_auto_base_cfg())
    cfg.setdefault("leverage", 20)
    cfg.setdefault("stop_loss_pct", 0.009)
    if mode == "SAFE_INSTALLED":
        cfg.update({
            "allow_auto_open": False,
            "allow_auto_close": False,
            "formal_auto_trading_authorized": False,
            "gate_authorized_auto_trading": False
        })
    elif mode == "AUTO_OPEN_ONLY":
        cfg.update({
            "allow_auto_open": True,
            "allow_auto_close": False,
            "formal_auto_trading_authorized": True,
            "gate_authorized_auto_trading": True
        })
    elif mode == "FULL_AUTO_LIVE_READY":
        cfg.update({
            "allow_auto_open": True,
            "allow_auto_close": True,
            "formal_auto_trading_authorized": True,
            "gate_authorized_auto_trading": True
        })
    else:
        raise RuntimeError("unknown auto mode: " + str(mode))
    _stage823_auto_write_cfg(cfg)
    return cfg

def _stage823_auto_mode_from_cfg(cfg):
    ao = bool(cfg.get("allow_auto_open"))
    ac = bool(cfg.get("allow_auto_close"))
    fa = bool(cfg.get("formal_auto_trading_authorized"))
    ga = bool(cfg.get("gate_authorized_auto_trading"))
    if ao and ac and fa and ga:
        return "FULL_AUTO_LIVE_READY"
    if ao and (not ac) and fa and ga:
        return "AUTO_OPEN_ONLY"
    return "SAFE_INSTALLED"

def _stage823_auto_status_payload(message=None):
    cfg = _stage823_auto_read_cfg()
    cfg.update(_stage823_auto_base_cfg())
    cfg.setdefault("leverage", 20)
    cfg.setdefault("stop_loss_pct", 0.009)
    try:
        import auto_trade_formal_daemon as d
        ds = d.status()
        daemon_running = bool(ds.get("running"))
    except Exception as e:
        ds = {"ok": False, "error": str(e)}
        daemon_running = False
    try:
        import auto_trade_formal_notify as notify
        ns = notify.get_status()
        notify_ready = bool(ns.get("notification_real_channel_ready"))
    except Exception as e:
        ns = {"ok": False, "error": str(e), "notification_real_channel_ready": False}
        notify_ready = False

    mode = _stage823_auto_mode_from_cfg(cfg)
    return {
        "ok": True,
        "stage": "stage8_23_auto_mode_control",
        "daemon_running": daemon_running,
        "mode": mode,
        "message": message,
        "allow_auto_open": bool(cfg.get("allow_auto_open")),
        "allow_auto_close": bool(cfg.get("allow_auto_close")),
        "formal_auto_trading_authorized": bool(cfg.get("formal_auto_trading_authorized")),
        "gate_authorized_auto_trading": bool(cfg.get("gate_authorized_auto_trading")),
        "position_mode": "full_balance",
        "full_position_ratio": 1.0,
        "leverage": int(cfg.get("leverage", 20)),
        "stop_loss_pct": float(cfg.get("stop_loss_pct", 0.009)),
        "take_profit_pct": float(cfg.get("take_profit_pct") or 0.009),
        "notification_enabled": bool(cfg.get("notification_enabled", True)),
        "notification_real_channel_ready": notify_ready,
        "can_auto_open_now": bool(daemon_running and notify_ready and cfg.get("allow_auto_open") and cfg.get("formal_auto_trading_authorized") and cfg.get("gate_authorized_auto_trading") and cfg.get("position_mode") == "full_balance" and int(cfg.get("leverage", 20)) in (20, 30, 50) and float(cfg.get("stop_loss_pct", 0.009)) in (0.003, 0.006, 0.009)),
        "can_auto_close_now": bool(daemon_running and notify_ready and cfg.get("allow_auto_close")),
        "daemon_status": ds,
        "notify_status": ns
    }

def _stage823_auto_json_call(fn):
    try:
        return _stage823_auto_jsonify(fn())
    except Exception as e:
        return _stage823_auto_jsonify({"ok": False, "stage": "stage8_23_auto_mode_control", "error": str(e)}), 500

@app.before_request
def stage823_auto_mode_control_api_intercept():
    p = _stage823_auto_request.path
    m = _stage823_auto_request.method
    if not p.startswith("/api/auto_trade/formal/auto_mode/"):
        return None

    err = _stage823_auto_auth_err()
    if err:
        return err

    if p == "/api/auto_trade/formal/auto_mode/status" and m == "GET":
        return _stage823_auto_json_call(lambda: _stage823_auto_status_payload())

    if p in ["/api/auto_trade/formal/auto_mode/enable", "/api/auto_trade/formal/auto_mode/full_auto"] and m == "POST":
        def call():
            _stage823_auto_apply("FULL_AUTO_LIVE_READY")
            out = _stage823_auto_status_payload("自动开仓与自动平仓已开启")
            out["mode"] = "FULL_AUTO_LIVE_READY"
            return out
        return _stage823_auto_json_call(call)

    if p == "/api/auto_trade/formal/auto_mode/open_only" and m == "POST":
        def call():
            _stage823_auto_apply("AUTO_OPEN_ONLY")
            out = _stage823_auto_status_payload("自动开仓已开启，自动平仓未开启")
            out["mode"] = "AUTO_OPEN_ONLY"
            return out
        return _stage823_auto_json_call(call)

    if p == "/api/auto_trade/formal/auto_mode/disable" and m == "POST":
        def call():
            _stage823_auto_apply("SAFE_INSTALLED")
            out = _stage823_auto_status_payload("自动开仓与自动平仓已关闭")
            out["mode"] = "SAFE_INSTALLED"
            return out
        return _stage823_auto_json_call(call)

    return _stage823_auto_jsonify({"ok": False, "stage": "stage8_23_auto_mode_control", "error": "unsupported method or path"}), 404
# STAGE8_23_AUTO_MODE_CONTROL_API_END

# AUTO_TRADE_API_ROUTES_END

# VECTOR_SAFE_REAL_VERIFY_V3_WEB_PATCH_START
from flask import request as _vector_req, jsonify as _vector_jsonify, Response as _vector_Response
import json as _vector_json
import time as _vector_time
from pathlib import Path as _vector_Path

_VECTOR_ROOT = _vector_Path("/root")
_VECTOR_AUTO = _VECTOR_ROOT / "auto_trade"

def _vector_auth_ok():
    try:
        a = _vector_req.authorization
        return bool(a and a.username == "quant" and a.password == "btc2026")
    except Exception:
        return False

def _vector_unauth():
    return _vector_Response("Unauthorized", 401, {"WWW-Authenticate": 'Basic realm="VECTOR"'})

def _vector_load_json(path, default=None):
    try:
        p = _vector_Path(path)
        if not p.exists():
            return default
        return _vector_json.loads(p.read_text(encoding="utf-8", errors="ignore") or "{}")
    except Exception:
        return default

def _vector_configured_symbols(timeframe, baseline=()):
    """Return the baseline plus every valid daemon config for a timeframe.

    The web roster must follow mounted daemon configs. Keeping a separate,
    hard-coded asset allowlist caused new live strategies to be omitted.
    """
    timeframe = str(timeframe or "").strip().lower()
    symbols = []
    for symbol in baseline or ():
        symbol = str(symbol or "").strip().upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    pattern = "formal_daemon_config_*_%s.json" % timeframe
    for path in sorted(_VECTOR_AUTO.glob(pattern)):
        config = _vector_load_json(path, {})
        if not isinstance(config, dict):
            continue
        config_timeframe = str(
            config.get("timeframe") or config.get("bar") or ""
        ).strip().lower()
        symbol = str(config.get("symbol") or "").strip().upper()
        if (
            config_timeframe == timeframe
            and symbol.endswith("-USDT-SWAP")
            and symbol not in symbols
        ):
            symbols.append(symbol)
    return tuple(symbols)

def _vector_write_json(path, obj):
    try:
        p = _vector_Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_vector_json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def _vector_read_jsonl(path, limit=160):
    p = _vector_Path(path)
    out = []
    if not p.exists():
        return out
    try:
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                obj = _vector_json.loads(line)
            except Exception:
                obj = {"raw": line}
            obj["_file"] = str(p)
            out.append(obj)
    except Exception as e:
        out.append({"_file": str(p), "error": str(e)})
    return out

def _vector_import_status_only():
    """Local-only daemon/executor snapshots. Never block the UI on exchange I/O."""
    daemon = {}
    executor = {}
    # Prefer on-disk fingerprints over live module status() which may touch OKX.
    try:
        daemon = _vector_load_json(_VECTOR_AUTO / "formal_daemon_status.json", {}) or {}
        if not isinstance(daemon, dict):
            daemon = {}
        if not daemon:
            cfg = _vector_load_json(_VECTOR_AUTO / "formal_daemon_config.json", {}) or {}
            try:
                pid = int((_VECTOR_AUTO / "formal_daemon.pid").read_text().strip())
                running = bool(pid > 0 and is_pid_alive(str(_VECTOR_AUTO / "formal_daemon.pid")))
            except Exception:
                pid, running = None, False
            daemon = {
                "running": running,
                "daemon_running": running,
                "pid": pid,
                "allow_auto_open": bool((cfg or {}).get("allow_auto_open")),
                "allow_auto_close": bool((cfg or {}).get("allow_auto_close")),
                "formal_auto_trading_authorized": bool((cfg or {}).get("formal_auto_trading_authorized")),
                "gate_authorized_auto_trading": bool((cfg or {}).get("gate_authorized_auto_trading")),
                "notification_enabled": bool((cfg or {}).get("notification_enabled")),
                "strategy": {"key": (cfg or {}).get("strategy_key"), "status": {}},
                "take_profit_pct": (cfg or {}).get("take_profit_pct"),
                "stop_loss_pct": (cfg or {}).get("stop_loss_pct"),
                "leverage": (cfg or {}).get("leverage"),
                "position_mode": (cfg or {}).get("position_mode"),
            }
    except Exception as e:
        daemon = {"ok": False, "error": str(e)}

    try:
        executor = _vector_load_json(_VECTOR_AUTO / "formal_executor_status.json", {}) or {}
        if not isinstance(executor, dict):
            executor = {}
    except Exception as e:
        executor = {"ok": False, "error": str(e)}

    return daemon if isinstance(daemon, dict) else {}, executor if isinstance(executor, dict) else {}

def _vector_current(executor=None):
    files = [
        _VECTOR_AUTO / "formal_v6_state.json",
        _VECTOR_AUTO / "formal_executor_state.json",
        _VECTOR_AUTO / "formal_v6_executor_state.json",
        _VECTOR_AUTO / "formal_state.json",
        _VECTOR_AUTO / "formal_current_position.json",
    ]
    found = []
    for f in files:
        obj = _vector_load_json(f, {})
        if isinstance(obj, dict):
            cur = obj.get("current") or obj.get("position") or obj.get("current_position")
            if isinstance(cur, dict) and cur:
                cur = dict(cur)
                cur["_source_file"] = str(f)
                found.append(cur)
    for cur in found:
        status = str(cur.get("status", "")).lower()
        if status not in ["closed", "close", "none", "null"] and not cur.get("closed_at"):
            return cur
    if found:
        return found[0]

    executor = executor if isinstance(executor, dict) else {}
    cur = executor.get("current")
    if isinstance(cur, dict) and cur:
        cur = dict(cur)
        cur["_source_file"] = "executor.get_status"
        return cur

    # Status API must stay network-free. Do NOT call OKX positions here —
    # exchange recovery belongs to daemons, not the dashboard poll path.
    return None

def _vector_active(cur):
    if not isinstance(cur, dict) or not cur:
        return False
    if str(cur.get("status", "")).lower() in ["closed", "close", "none", "null"]:
        return False
    if cur.get("closed_at"):
        return False
    return bool(cur.get("side") or cur.get("posSide") or cur.get("open_order") or cur.get("entry_price"))

def _vector_float(x):
    try:
        if x in [None, ""]:
            return None
        return float(x)
    except Exception:
        return None

def _vector_entry(cur):
    if not isinstance(cur, dict):
        return None
    for k in ["entry_price", "open_price", "avgPx", "fillPx"]:
        v = _vector_float(cur.get(k))
        if v is not None:
            return v
    oo = cur.get("open_order")
    if isinstance(oo, dict):
        filled = oo.get("filled")
        if isinstance(filled, dict):
            order = filled.get("order")
            if isinstance(order, dict):
                for k in ["avgPx", "fillPx"]:
                    v = _vector_float(order.get(k))
                    if v is not None:
                        return v
    return None

def _vector_stop(cur):
    if not isinstance(cur, dict):
        return None
    att = cur.get("attached_stop_loss")
    if isinstance(att, dict):
        for k in ["stop_loss_price", "slTriggerPx"]:
            if att.get(k) not in [None, ""]:
                return att.get(k)
        payload = att.get("payload")
        if isinstance(payload, dict):
            for k in ["slTriggerPx", "stop_loss_price"]:
                if payload.get(k) not in [None, ""]:
                    return payload.get(k)
    return cur.get("stop_loss_price") or cur.get("slTriggerPx")

def _vector_event(e):
    if not isinstance(e, dict):
        return {"raw": str(e)}
    payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}
    info = e.get("info") if isinstance(e.get("info"), dict) else {}
    ev = e.get("event") if isinstance(e.get("event"), dict) else {}
    return {
        "time": e.get("time") or e.get("created_at") or e.get("ts") or payload.get("time") or ev.get("time"),
        "type": e.get("event_type") or e.get("type") or (e.get("event") if isinstance(e.get("event"), str) else None) or payload.get("event") or payload.get("action"),
        "action": e.get("action") or payload.get("action") or ev.get("action"),
        "strategy": e.get("strategy") or e.get("strategy_key") or payload.get("strategy") or payload.get("strategy_key"),
        "side": e.get("side") or payload.get("side") or ev.get("side"),
        "price": e.get("price") or payload.get("price") or ev.get("price") or payload.get("entry_price") or payload.get("exit_price"),
        "reason": e.get("reason") or payload.get("reason") or ev.get("reason") or info.get("reason"),
        "final_reason": e.get("final_reason") or payload.get("final_reason") or ev.get("final_reason") or info.get("final_reason"),
        "_file": e.get("_file"),
    }

def _vector_events():
    files = [
        _VECTOR_AUTO / "formal_daemon_events.jsonl",
        _VECTOR_AUTO / "formal_executor_events.jsonl",
        _VECTOR_AUTO / "formal_events.jsonl",
        _VECTOR_AUTO / "formal_notification_audit.log",
    ]
    out = []
    for f in files:
        out.extend(_vector_read_jsonl(f, 180))
    return [_vector_event(e) for e in out][-80:]

def _vector_nested(obj, *keys):
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _vector_pick_expected_return_per_trade(assignment_rating, assignment_row):
    """Backtest/rereview expected return; ignore empty-rating placeholders.

    Live card 预期盈利率 prefers actual_single_trade_pnl_pct in the UI when
    closed trades exist for THIS strategy key. This helper only picks the
    non-live fallback. Rating rows with 0 backtest + 0 real trades often store
    expected_return_per_trade_pct=0.0 as a prior default — treat that as missing.
    """
    assignment_rating = assignment_rating if isinstance(assignment_rating, dict) else {}
    assignment_row = assignment_row if isinstance(assignment_row, dict) else {}
    rating_er = assignment_rating.get("expected_return_per_trade_pct")
    row_er = assignment_row.get("expected_return_per_trade_pct")
    try:
        bt_n = int(assignment_rating.get("backtest_trades") or 0)
    except Exception:
        bt_n = 0
    try:
        rt_n = int(assignment_rating.get("real_trade_count") or 0)
    except Exception:
        rt_n = 0
    if rating_er is not None and (bt_n > 0 or rt_n > 0):
        return rating_er
    if row_er is not None:
        return row_er
    return None

def _vector_canonical_strategy_key(key):
    # Only true renames belong here. Do NOT map deleted/predecessor strategies
    # (e.g. ada5_session_trend_pullback_short_ai) onto a different live key —
    # that wrongly attributes their closed PnL to the new strategy card.
    aliases = {
        "ema7_center_down_short": "ema6_center_down_then_fall",
        "ema6_center_down_then_fall": "ema6_center_down_then_fall",
    }
    key = str(key or "")
    return aliases.get(key, key)


def _vector_strategy_key_aliases(key):
    """Return lookup keys for assignment/rating ids (canonical + storage aliases)."""
    key = _vector_canonical_strategy_key(key)
    aliases = {
        "ema6_center_down_then_fall": [
            "ema6_center_down_then_fall",
            "ema7_center_down_short",
        ],
    }
    out = []
    for item in aliases.get(key, [key]):
        if item and item not in out:
            out.append(item)
    if key and key not in out:
        out.insert(0, key)
    return out


def _vector_lookup_assignment_row(assignments, ratings_by_id, symbol, timeframe, strategy_key):
    assignments = assignments if isinstance(assignments, dict) else {}
    ratings_by_id = ratings_by_id if isinstance(ratings_by_id, dict) else {}
    symbol = str(symbol or "").upper()
    timeframe = str(timeframe or "").lower()
    row = {}
    rating = {}
    assignment_id = None
    for key in _vector_strategy_key_aliases(strategy_key):
        aid = "%s|%s|%s" % (symbol, timeframe, key)
        cand = assignments.get(aid)
        if isinstance(cand, dict) and cand and not row:
            row = cand
            assignment_id = aid
        rcand = ratings_by_id.get(aid)
        if isinstance(rcand, dict) and rcand and not rating:
            rating = rcand
            if assignment_id is None:
                assignment_id = aid
    if assignment_id is None:
        assignment_id = "%s|%s|%s" % (
            symbol, timeframe, _vector_canonical_strategy_key(strategy_key)
        )
    return assignment_id, row, rating

def _vector_trade_row(raw):
    if not isinstance(raw, dict):
        return None
    open_fill = _vector_nested(raw, "open_order", "filled", "order") or {}
    close_fill = (
        _vector_nested(raw, "last_close_attempt", "close_order", "filled", "order")
        or _vector_nested(raw, "close_order", "filled", "order")
        or {}
    )
    position = _vector_nested(raw, "position_poll", "position") or {}
    entry_price = _vector_entry(raw)
    close_price = None
    for value in [
        raw.get("close_price"),
        close_fill.get("avgPx"),
        close_fill.get("fillPx"),
    ]:
        close_price = _vector_float(value)
        if close_price is not None:
            break

    closed_at = raw.get("closed_at")
    closed_at_ts = _vector_float(raw.get("closed_at_ts"))
    is_closed = bool(closed_at or closed_at_ts or str(raw.get("status") or "").lower() in ["closed", "close"])
    realized_pnl = _vector_float(close_fill.get("pnl"))
    open_fee = _vector_float(open_fill.get("fee"))
    close_fee = _vector_float(close_fill.get("fee"))
    initial_margin = _vector_float(position.get("imr"))
    net_pnl = None
    pnl_rate_pct = None
    account_return_pct = None
    rate_basis = None
    account_equity = _vector_float(
        _vector_nested(raw, "sizing", "account_equity_usdt")
        or _vector_nested(raw, "sizing", "account_equity")
        or raw.get("account_equity_usdt")
    )
    position_ratio = _vector_float(
        _vector_nested(raw, "sizing", "full_position_ratio")
        or _vector_nested(raw, "sizing", "position_ratio")
        or raw.get("full_position_ratio")
        or raw.get("position_ratio")
    )
    if is_closed and realized_pnl is not None and initial_margin not in [None, 0]:
        net_pnl = realized_pnl + (open_fee or 0.0) + (close_fee or 0.0)
        # 单笔盈利率：相对本笔保证金，不按总仓位/账户权益缩放
        pnl_rate_pct = net_pnl / initial_margin * 100.0
        rate_basis = "net_realized_pnl_over_initial_margin"
    elif is_closed and entry_price not in [None, 0] and close_price is not None:
        side = str(raw.get("posSide") or raw.get("side") or "").lower()
        direction = -1.0 if side == "short" else 1.0
        leverage = _vector_float(raw.get("leverage") or close_fill.get("lever")) or 1.0
        pnl_rate_pct = direction * (close_price-entry_price) / entry_price * leverage * 100.0
        rate_basis = "fill_price_change_times_leverage"
    if is_closed:
        existing_acct = _vector_float(raw.get("account_return_pct"))
        if existing_acct is not None:
            account_return_pct = existing_acct
        elif net_pnl is not None and account_equity not in [None, 0]:
            account_return_pct = net_pnl / account_equity * 100.0
        elif realized_pnl is not None and account_equity not in [None, 0]:
            account_return_pct = (
                (realized_pnl + (open_fee or 0.0) + (close_fee or 0.0))
                / account_equity * 100.0
            )
        # Fallback: if margin rate missing but equity + position share known,
        # recover intrinsic single-trade rate by unscaling.
        if pnl_rate_pct is None and account_return_pct is not None and position_ratio not in [None, 0]:
            pnl_rate_pct = account_return_pct / position_ratio
            rate_basis = rate_basis or "equity_return_unscaled_by_position_ratio"

    strategy_key = _vector_canonical_strategy_key(raw.get("strategy_key") or raw.get("strategy") or "ema6_center_down_then_fall")
    fallback_names = {
        "ema6_center_down_then_fall": "EMA6居中后再下行",
        "ema7_center_down_short": "EMA6居中后再下行",
        "btc15_dual_cycle_downtrend_reentry_short_ai": "双周期下跌加速再死叉（AI创造）",
        "conventional_up_arrangement_valid_death_cross_short": "常规上升排列有效死叉",
        "btc5_exhaustion_reclaim_long_ai": "BTC 5分钟超跌收回（AI创造）",
        "cl5_exhaustion_fade_short_ai": "CL 5分钟冲高衰竭回落（AI创造）",
        "ng5_exhaustion_fade_short_ai": "NG 5分钟冲高衰竭回落（AI创造）",
        "ng5_session_exhaustion_reclaim_long_ai": "NG 5分钟时段超跌收回（AI创造）",
        "xag5_session_breakdown_short_ai": "XAG 5分钟时段顺势破位（AI创造）",
        "ltc5_exhaustion_fade_short_ai": "LTC 5分钟冲高衰竭回落（AI创造）",
        "ada5_session_trend_pullback_short_ai": "ADA 5分钟时段趋势反抽（AI创造）",
        "xau15_h1_breakout_long_ai": "XAU 15分钟顺势放量突破（AI创造）",
        "frost_xrp_rescue_h20_t45": "寒霜-XRP-15m-exhaustion_fade",
    }
    strategy_name = raw.get("strategy_name") or fallback_names.get(
        strategy_key, str(strategy_key)
    )
    open_time = raw.get("opened_at") or open_fill.get("fillTime") or ""
    close_time = closed_at or ""
    # 总仓位盈/亏率优先用账户权益收益；缺权益时才回退到单笔保证金收益率
    equity_pct = account_return_pct if account_return_pct is not None else None
    single_pct = pnl_rate_pct
    return {
        "execution_id": raw.get("execution_id") or raw.get("position_id"),
        "symbol": raw.get("symbol") or "BTC-USDT-SWAP",
        "strategy_key": strategy_key,
        "strategy_name": strategy_name,
        "side": raw.get("posSide") or raw.get("side"),
        "trigger_time": open_time,
        "opened_at": open_time,
        "trigger_ts": _vector_float(raw.get("opened_at_ts") or open_fill.get("fillTime")),
        "close_time": close_time,
        "closed_at": close_time,
        "close_ts": closed_at_ts,
        "status": "已平仓" if is_closed else "持仓中",
        "entry_price": entry_price,
        "close_price": close_price,
        "close_reason": raw.get("close_reason"),
        "net_pnl": round(net_pnl, 8) if net_pnl is not None else None,
        "pnl_rate_pct": round(single_pct, 4) if single_pct is not None else None,
        "single_trade_pnl_pct": round(single_pct, 4) if single_pct is not None else None,
        "account_return_pct": round(account_return_pct, 4) if account_return_pct is not None else None,
        "equity_return_pct": round(equity_pct, 4) if equity_pct is not None else (
            round(single_pct, 4) if single_pct is not None else None
        ),
        "position_ratio": round(position_ratio, 6) if position_ratio is not None else None,
        "rate_basis": rate_basis,
        "win": bool(
            (equity_pct if equity_pct is not None else single_pct) > 0
        ) if is_closed and (equity_pct is not None or single_pct is not None) else None,
    }

def _vector_trade_sort_ts(row):
    """Prefer close time for completed trades so 'recent' matches latest closes."""
    if not isinstance(row, dict):
        return 0.0
    for key in ("close_ts", "trigger_ts"):
        val = _vector_float(row.get(key))
        if val is not None:
            return float(val)
    text = str(row.get("close_time") or row.get("closed_at")
               or row.get("trigger_time") or row.get("opened_at") or "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).timestamp()
        except Exception:
            continue
    return 0.0


def _vector_aggregate_all_trades(limit=15):
    """Merge closed/open trades across all formal_v6_state*.json (system-wide)."""
    rows = []
    seen = set()
    for path in sorted(_VECTOR_AUTO.glob("formal_v6_state*.json")):
        state = _vector_load_json(path, {})
        if not isinstance(state, dict):
            continue
        raw_items = list(state.get("history") or [])
        cur = state.get("current")
        if _vector_active(cur):
            raw_items.append(cur)
        for item in raw_items:
            row = _vector_trade_row(item)
            if not row:
                continue
            dedupe = (
                str(row.get("execution_id") or ""),
                str(row.get("strategy_key") or ""),
                str(row.get("trigger_time") or ""),
                str(row.get("close_time") or ""),
            )
            if dedupe in seen:
                continue
            seen.add(dedupe)
            rows.append(row)
    rows.sort(key=_vector_trade_sort_ts, reverse=True)
    return rows[: max(1, int(limit or 15))]


def _vector_trade_dashboard(cur, strategy_key, strategy_name, cfg=None, state_suffix=""):
    cfg = cfg if isinstance(cfg, dict) else {}
    rating_state = _vector_load_json(_VECTOR_AUTO / "strategy_ratings.json", {})
    ratings_by_id = rating_state.get("ratings_by_id") if isinstance(rating_state, dict) else {}
    ratings_by_id = ratings_by_id if isinstance(ratings_by_id, dict) else {}
    rating_symbol = str(cfg.get("symbol") or "BTC-USDT-SWAP").upper()
    rating_timeframe = str(cfg.get("timeframe") or "1h").lower()
    controls = _vector_load_json(_VECTOR_AUTO / "strategy_runtime_controls.json", {})
    assignments = (controls.get("assignments") or {}) if isinstance(controls, dict) else {}
    state = _vector_load_json(_VECTOR_AUTO / ("formal_v6_state%s.json" % state_suffix), {})
    raw_trades = list(state.get("history") or []) if isinstance(state, dict) else []
    if _vector_active(cur):
        raw_trades.append(cur)
    all_rows = [row for row in (_vector_trade_row(item) for item in raw_trades) if row]
    configured_keys = {
        _vector_canonical_strategy_key(key)
        for key in (cfg.get("strategy_keys") or [])
        if key
    }
    if configured_keys:
        all_rows = [
            row for row in all_rows
            if row.get("strategy_key") in configured_keys
        ]
    # Keep only this zone's symbol when state file is shared across symbols.
    zone_symbol = str(cfg.get("symbol") or "").upper()
    if zone_symbol:
        scoped = [
            row for row in all_rows
            if str(row.get("symbol") or zone_symbol).upper() == zone_symbol
        ]
        if scoped:
            all_rows = scoped
    all_rows.sort(key=_vector_trade_sort_ts, reverse=True)
    rows = all_rows[:15]

    running = []
    seen = set()
    candidates = []
    configured_keys = [k for k in (cfg.get("strategy_keys") or []) if k]
    # Only mounted keys appear in 运行策略 roster; never inject EMA6 default.
    if configured_keys:
        for key in configured_keys:
            candidates.append((key, None))
    elif strategy_key:
        # Legacy single-key path only when explicitly configured.
        if strategy_key not in (
            "ema6_center_down_then_fall",
            "ema7_center_down_short",
        ):
            candidates.append((strategy_key, strategy_name))
    configured = cfg.get("strategies")
    if isinstance(configured, list):
        for item in configured:
            if isinstance(item, dict) and item.get("enabled", True):
                candidates.append((item.get("key") or item.get("strategy_key"), item.get("name") or item.get("strategy_name")))
    elif isinstance(configured, dict):
        for key, item in configured.items():
            if not isinstance(item, dict) or item.get("enabled", True):
                candidates.append((key, item.get("name") if isinstance(item, dict) else None))
    known_names = {
        "ema6_center_down_then_fall": "EMA6居中后再下行",
        "ema7_center_down_short": "EMA6居中后再下行",
        "conventional_up_break_long": "常规上升排列突破",
        "early_downtrend_ema6_ema75_short": "EMA19反抽失败·EMA6/EMA75同步破位",
        "conventional_down_arrangement_bottom_up_long": "常规下跌排列筑底上行",
        "cci_75_100": "EMA7上升趋势回踩续涨",
        "ema53_liquidity_sweep_reclaim_long": "EMA53缓升｜36小时低点扫荡收回（AI创造）",
        "ema8_mainwave_long": "EMA8主升浪",
        "cci_neg60_neg110_short": "CCI负60-负110下行中再下行",
        "btc15_dual_cycle_downtrend_reentry_short_ai": "双周期下跌加速再死叉（AI创造）",
        "conventional_up_arrangement_valid_death_cross_short": "常规上升排列有效死叉",
        "btc5_exhaustion_reclaim_long_ai": "BTC 5分钟超跌收回（AI创造）",
        "cl5_exhaustion_fade_short_ai": "CL 5分钟冲高衰竭回落（AI创造）",
        "ng5_exhaustion_fade_short_ai": "NG 5分钟冲高衰竭回落（AI创造）",
        "ng5_session_exhaustion_reclaim_long_ai": "NG 5分钟时段超跌收回（AI创造）",
        "xag5_session_breakdown_short_ai": "XAG 5分钟时段顺势破位（AI创造）",
        "ltc5_exhaustion_fade_short_ai": "LTC 5分钟冲高衰竭回落（AI创造）",
        "ada5_session_trend_pullback_short_ai": "ADA 5分钟时段趋势反抽（AI创造）",
        "xau15_h1_breakout_long_ai": "XAU 15分钟顺势放量突破（AI创造）",
        "frost_xrp_rescue_h20_t45": "寒霜-XRP-15m-exhaustion_fade",
    }
    for key, name in candidates:
        key = _vector_canonical_strategy_key(key)
        if key and key not in seen:
            seen.add(key)
            try:
                import auto_trade_strategy_titles as _titles
                shown = _titles.short_strategy_title(key, name or known_names.get(key))
            except Exception:
                shown = name or known_names.get(key) or str(key)
            running.append({"key": key, "name": shown})
    stats = []
    completed = [row for row in all_rows if row.get("status") == "已平仓"]
    grade_rank_map = {"S": 4, "A": 3, "B": 2, "C": 1, "SHADOW": 0}
    for strategy in running:
        trades = [row for row in completed if row.get("strategy_key") == strategy["key"]]
        windows = {}
        for size in (5, 10, 15):
            sample = trades[:size]
            enough = len(sample) >= size
            wins = sum(1 for row in sample if row.get("win") is True) if enough else None
            windows[str(size)] = {
                "required": size,
                "available": len(trades),
                "enough": enough,
                "wins": wins,
                "win_rate_pct": round(wins / size * 100.0, 1) if enough else None,
                "label": ("%.1f%%" % (wins / size * 100.0)) if enough else "次数不足",
            }
        direction = "做多" if strategy["key"] in ["conventional_up_break_long", "conventional_down_arrangement_bottom_up_long", "cci_75_100", "ema53_liquidity_sweep_reclaim_long", "ema8_mainwave_long", "btc5_exhaustion_reclaim_long_ai", "ng5_session_exhaustion_reclaim_long_ai", "xau15_h1_breakout_long_ai"] else (
            "做空" if strategy["key"] in ["ema6_center_down_then_fall", "ema7_center_down_short", "early_downtrend_ema6_ema75_short", "cci_neg60_neg110_short", "btc15_dual_cycle_downtrend_reentry_short_ai", "conventional_up_arrangement_valid_death_cross_short", "cl5_exhaustion_fade_short_ai", "ng5_exhaustion_fade_short_ai", "xag5_session_breakdown_short_ai", "ltc5_exhaustion_fade_short_ai", "ada5_session_trend_pullback_short_ai", "frost_xrp_rescue_h20_t45"] else ""
        )
        # Heuristic for newer long ADA trend-pullback keys.
        if not direction:
            lk = str(strategy["key"] or "").lower()
            if "ada5" in lk and (
                    "trendpb" in lk or "prev_h14" in lk or "trend" in lk):
                if "short" not in lk:
                    direction = "做多"
        assignment_id, assignment_row, assignment_rating = _vector_lookup_assignment_row(
            assignments, ratings_by_id, rating_symbol, rating_timeframe, strategy["key"]
        )
        raw_grade = str(
            assignment_row.get("lifecycle_grade")
            or assignment_rating.get("grade")
            or ""
        ).strip()
        lifecycle_grade = raw_grade.upper()
        # Deleted strategies must never reappear as live B in the roster.
        if lifecycle_grade == "DELETED" or assignment_row.get("deleted_at"):
            continue
        # Mounted auto-trade strategies always show a letter grade; default B.
        # Never surface 未评级/待评级 (Chinese labels survive .upper()).
        # Do NOT map DELETED/eliminated grades to B.
        if (not raw_grade
                or raw_grade in ("未评级", "待评级")
                or lifecycle_grade in (
                    "UNRATED", "NONE", "NULL", "PENDING", "待评级")):
            lifecycle_grade = "B"
        if lifecycle_grade == "SHADOW" or str(
                assignment_row.get("audit_state") or "") == "read_only_shadow":
            lifecycle_grade = "SHADOW"
        max_position_ratio = assignment_row.get("max_position_ratio")
        grade_ratio_map = {"S": 0.70, "A": 0.50, "B": 0.30, "C": 0.10}
        if max_position_ratio is None and lifecycle_grade in grade_rank_map:
            max_position_ratio = grade_ratio_map.get(lifecycle_grade)
        # Applied B-grade strategies must size at 30% (never legacy 15%).
        if lifecycle_grade == "B":
            try:
                if max_position_ratio is None or abs(float(max_position_ratio) - 0.15) < 1e-9:
                    max_position_ratio = 0.30
            except Exception:
                max_position_ratio = 0.30
        recent_trades = []
        single_rates = []
        for row in trades:
            single = row.get("single_trade_pnl_pct")
            if single is None:
                single = row.get("pnl_rate_pct")
            if single is not None:
                try:
                    single_rates.append(float(single))
                except Exception:
                    pass
        # Keep newest 10 open/close groups for UI dropdown (drop older).
        for row in trades[:10]:
            eq = row.get("equity_return_pct")
            if eq is None:
                eq = row.get("account_return_pct")
            single = row.get("single_trade_pnl_pct")
            if single is None:
                single = row.get("pnl_rate_pct")
            # If only one rate exists, keep columns distinct when possible.
            if single is None and eq is not None and row.get("position_ratio") not in [None, 0]:
                try:
                    single = float(eq) / float(row.get("position_ratio"))
                except Exception:
                    single = None
            recent_trades.append({
                "opened_at": row.get("opened_at") or row.get("trigger_time") or "-",
                "closed_at": row.get("closed_at") or row.get("close_time") or "-",
                "equity_return_pct": round(float(eq), 4) if eq is not None else None,
                "single_trade_pnl_pct": round(float(single), 4) if single is not None else None,
                "pnl_rate_pct": round(float(single), 4) if single is not None else None,
                "position_ratio": row.get("position_ratio"),
                "win": row.get("win"),
            })
        actual_single = None
        if single_rates:
            actual_single = round(sum(single_rates) / float(len(single_rates)), 3)
        shown_name = strategy["name"]
        try:
            import auto_trade_strategy_titles as _titles
            shown_name = _titles.short_strategy_title(
                strategy["key"],
                assignment_row.get("strategy_name") or strategy["name"])
        except Exception:
            pass
        # Uniform expectancy + calibrated WR (fees/slippage aware).
        exp_row = {}
        exp_block = {}
        try:
            import auto_trade_expectancy_metrics as _exp
            exp_row = _exp.metrics_for(
                rating_symbol, rating_timeframe, strategy["key"], refresh=False) or {}
            exp_block = exp_row.get("expectancy") or {}
            if not exp_block:
                exp_block = _exp.build_expectancy_block(
                    rating_symbol, rating_timeframe, strategy["key"],
                    position_ratio=max_position_ratio or 0.30,
                    leverage=20.0,
                    ai_wr_pct=_vector_float(assignment_row.get("ai_theoretical_wr_avg")),
                ) or {}
        except Exception as _exp_err:
            exp_block = {"error": str(_exp_err)}
        cal_wr = assignment_rating.get("calibrated_expected_win_rate_pct")
        if cal_wr is None:
            cal_wr = (exp_block.get("calibrated_expected_win_rate_pct") or {}).get("value")
        if cal_wr is None:
            cal_wr = assignment_rating.get("expected_win_rate_pct")
        net_eq = assignment_rating.get("net_expectancy_equity_pct")
        if net_eq is None:
            net_eq = (exp_block.get("net_expectancy_equity_pct") or {}).get("value")
        net_m = assignment_rating.get("net_expectancy_margin_pct")
        if net_m is None:
            net_m = (exp_block.get("net_expectancy_margin_pct") or {}).get("value")
        metric_status = assignment_rating.get("metric_status") or {
            "win_rate": (exp_block.get("calibrated_expected_win_rate_pct") or {}).get(
                "metric_status"),
            "expectancy": (exp_block.get("net_expectancy_equity_pct") or {}).get(
                "metric_status"),
            "display_win_rate": (exp_block.get("calibrated_expected_win_rate_pct") or {}).get(
                "display"),
            "display_expectancy": (exp_block.get("net_expectancy_equity_pct") or {}).get(
                "display"),
        }
        funnel = exp_row.get("funnel_7d") or {}
        freq = exp_row.get("frequency") or {}
        stats.append({
            "strategy_key": strategy["key"],
            "strategy_name": shown_name,
            "strategy_title": shown_name,
            "direction": direction, "completed_count": len(trades), "windows": windows,
            "grade": lifecycle_grade,
            "lifecycle_grade": lifecycle_grade,
            "max_position_ratio": max_position_ratio,
            "grade_rank": grade_rank_map.get(lifecycle_grade)
            or assignment_rating.get("grade_rank") or 0,
            "expected_win_rate_pct": cal_wr,
            "calibrated_expected_win_rate_pct": cal_wr,
            "expected_return_per_trade_pct": _vector_pick_expected_return_per_trade(
                assignment_rating, assignment_row
            ),
            "gross_expectancy_price_pct": assignment_rating.get("gross_expectancy_price_pct")
            if assignment_rating.get("gross_expectancy_price_pct") is not None
            else (exp_block.get("gross_expectancy_price_pct") or {}).get("value"),
            "net_expectancy_price_pct": assignment_rating.get("net_expectancy_price_pct")
            if assignment_rating.get("net_expectancy_price_pct") is not None
            else (exp_block.get("net_expectancy_price_pct") or {}).get("value"),
            "net_expectancy_notional_pct": assignment_rating.get("net_expectancy_notional_pct")
            if assignment_rating.get("net_expectancy_notional_pct") is not None
            else (exp_block.get("net_expectancy_notional_pct") or {}).get("value"),
            "net_expectancy_equity_pct": net_eq,
            "net_expectancy_margin_pct": net_m,
            "cost_price_pct": assignment_rating.get("cost_price_pct")
            if assignment_rating.get("cost_price_pct") is not None
            else (exp_block.get("cost_price_pct") or {}).get("value"),
            "profit_factor": assignment_rating.get("profit_factor")
            if assignment_rating.get("profit_factor") is not None
            else (exp_block.get("profit_factor") or {}).get("value"),
            "cost_ratio": assignment_rating.get("cost_ratio")
            if assignment_rating.get("cost_ratio") is not None
            else (exp_block.get("cost_ratio") or {}).get("value"),
            "max_drawdown_margin_pct": assignment_rating.get("max_drawdown_margin_pct")
            if assignment_rating.get("max_drawdown_margin_pct") is not None
            else (exp_block.get("max_drawdown_margin_pct") or {}).get("value"),
            "credibility": assignment_rating.get("credibility")
            if assignment_rating.get("credibility") is not None
            else exp_block.get("credibility"),
            "monthly_expected_net_equity_pct": (
                (exp_block.get("monthly_expected_net_equity_pct") or {}).get("value")),
            "mechanism_family": assignment_rating.get("mechanism_family")
            or exp_block.get("mechanism_family") or exp_row.get("mechanism_family"),
            "metric_status": metric_status,
            "funnel_7d": funnel,
            "frequency_forecast": {
                "expected_daily_fills": exp_row.get("expected_daily_fills")
                or freq.get("expected_daily_fills"),
                "expected_weekly_fills": exp_row.get("expected_weekly_fills")
                or freq.get("expected_weekly_fills"),
                "weekly_interval": freq.get("weekly_interval"),
                "class": (exp_row.get("frequency_class") or {}).get("class"),
            },
            "actual_single_trade_pnl_pct": actual_single,
            "confidence_lower_win_rate_pct": assignment_rating.get("confidence_lower_win_rate_pct"),
            "backtest_trades": assignment_rating.get("backtest_trades"),
            "real_trade_count": assignment_rating.get("real_trade_count"),
            "rating_reason": assignment_rating.get("rating_reason"),
            "recommended_action": assignment_rating.get("recommended_action"),
            "recent_trades": recent_trades,
            "human_confirmed": bool(
                assignment_row.get("human_confirmed")
                or assignment_row.get("human_confirm_pipeline")),
            "ai_theoretical_wr_avg": assignment_row.get("ai_theoretical_wr_avg"),
            "ai_theoretical_wr_by_provider": assignment_row.get(
                "ai_theoretical_wr_by_provider"),
            # Split WR columns — AI must never look like high-confidence production WR alone.
            "ai_logic_wr_pct": assignment_row.get("ai_theoretical_wr_avg"),
            "walk_forward_wr_pct": assignment_rating.get("walk_forward_win_rate_pct")
            or assignment_row.get("walk_forward_win_rate_pct"),
            "backtest_wr_pct": (
                (exp_block.get("backtest_win_rate_pct") or {}).get("value")
                if isinstance(exp_block.get("backtest_win_rate_pct"), dict)
                else assignment_rating.get("backtest_win_rate_pct")),
            "live_wr_pct": (
                (exp_block.get("live_win_rate_pct") or {}).get("value")
                if isinstance(exp_block.get("live_win_rate_pct"), dict)
                else assignment_rating.get("live_win_rate_pct")),
            "sample_n": (exp_block.get("sample") or {}).get("combined_n")
            or assignment_rating.get("backtest_trades")
            or 0,
            "wr_high_confidence": bool(
                (exp_block.get("credibility") or assignment_rating.get("credibility") or 0) >= 0.7
                and ((exp_block.get("sample") or {}).get("combined_n") or 0) >= 20
            ),
            "new_entries_allowed": bool(assignment_row.get("new_entries_allowed")),
            "pause_new_entries": bool(assignment_row.get("pause_new_entries")),
            "audit_state": assignment_row.get("audit_state"),
        })
    stats.sort(key=lambda row: (
        -int(row.get("grade_rank") or 0),
        -float(row.get("actual_single_trade_pnl_pct")
               if row.get("actual_single_trade_pnl_pct") is not None
               else (row.get("expected_return_per_trade_pct") or -999)),
        -float(row.get("expected_win_rate_pct") or 0),
        row.get("strategy_name") or "",
    ))
    return {
        "trades": rows,
        "strategy_stats": stats,
        "rate_definition": (
            "总仓位盈/亏率=净盈亏÷开仓时账户总权益；"
            "单笔盈利率=净盈亏÷本笔初始保证金（不按总仓位缩放）"
        ),
    }

def _vector_status_payload():
    cfg = _vector_load_json(_VECTOR_AUTO / "formal_daemon_config.json", {})
    if not isinstance(cfg, dict):
        cfg = {}

    daemon, executor = _vector_import_status_only()
    logic = _vector_load_json(_VECTOR_AUTO / "vector_logic_chain_self_test.json", {"ok": False, "status": "NOT_RUN"})
    preflight = _vector_load_json(_VECTOR_AUTO / "vector_real_okx_preflight_readonly.json", {"ok": False, "status": "NOT_RUN"})
    active_verify = _vector_load_json(_VECTOR_AUTO / "vector_real_active_position_verify_last.json", {"ok": False, "status": "NOT_RUN"})
    tp_verify = _vector_load_json(_VECTOR_AUTO / "vector_take_profit_path_verify.json", {"ok": False, "status": "NOT_RUN"})

    cur = _vector_current(executor)
    active = _vector_active(cur)
    xau_cfg = _vector_load_json(_VECTOR_AUTO / "formal_daemon_config_xau.json", {})
    xau_state = _vector_load_json(_VECTOR_AUTO / "formal_v6_state_xau.json", {})
    xau_cur = xau_state.get("current") if isinstance(xau_state, dict) else None
    cl_cfg = _vector_load_json(_VECTOR_AUTO / "formal_daemon_config_cl.json", {})
    cl_state = _vector_load_json(_VECTOR_AUTO / "formal_v6_state_cl.json", {})
    cl_cur = cl_state.get("current") if isinstance(cl_state, dict) else None
    ng_cfg = _vector_load_json(_VECTOR_AUTO / "formal_daemon_config_ng.json", {})
    ng_state = _vector_load_json(_VECTOR_AUTO / "formal_v6_state_ng.json", {})
    ng_cur = ng_state.get("current") if isinstance(ng_state, dict) else None
    try:
        xau_pid = int((_VECTOR_AUTO / "formal_daemon_xau.pid").read_text().strip())
        xau_running = bool(xau_pid > 0 and is_pid_alive(str(_VECTOR_AUTO / "formal_daemon_xau.pid")))
    except Exception:
        xau_pid, xau_running = None, False
    try:
        cl_pid = int((_VECTOR_AUTO / "formal_daemon_cl.pid").read_text().strip())
        cl_running = bool(cl_pid > 0 and is_pid_alive(str(_VECTOR_AUTO / "formal_daemon_cl.pid")))
    except Exception:
        cl_pid, cl_running = None, False
    try:
        ng_pid = int((_VECTOR_AUTO / "formal_daemon_ng.pid").read_text().strip())
        ng_running = bool(ng_pid > 0 and is_pid_alive(str(_VECTOR_AUTO / "formal_daemon_ng.pid")))
    except Exception:
        ng_pid, ng_running = None, False

    try:
        sig = ((daemon.get("strategy") or {}).get("status") or {}).get("signal") or {}
    except Exception:
        sig = {}

    configured_keys = [k for k in (cfg.get("strategy_keys") or []) if k]
    # Never ghost-default BTC 1h empty roster back to deleted EMA6.
    if configured_keys:
        strategy_key = (
            sig.get("strategy_key")
            or ((daemon.get("strategy") or {}).get("key"))
            or cfg.get("strategy_key")
            or configured_keys[0]
        )
        strategy_name = (
            sig.get("strategy_name")
            or ((daemon.get("strategy") or {}).get("name"))
            or strategy_key
        )
    else:
        strategy_key = ""
        strategy_name = "无运行策略"

    daemon_running = bool(daemon.get("running") or daemon.get("daemon_running") or daemon.get("pid"))
    allow_open = bool(cfg.get("allow_auto_open") or daemon.get("allow_auto_open"))
    allow_close = bool(cfg.get("allow_auto_close") or daemon.get("allow_auto_close"))
    formal_auth = bool(cfg.get("formal_auto_trading_authorized") or daemon.get("formal_auto_trading_authorized"))
    gate_auth = bool(cfg.get("gate_authorized_auto_trading") or daemon.get("gate_authorized_auto_trading"))
    notify_ready = bool(cfg.get("notification_enabled") or daemon.get("notification_enabled") or daemon.get("notification_real_channel_ready"))

    chain_ready = bool(daemon_running and allow_open and formal_auth and gate_auth)
    runtime = "持仓中" if active else ("监测中" if chain_ready else ("监测中（未完全授权）" if daemon_running else "未运行"))

    entry = _vector_entry(cur)
    side = cur.get("side") if isinstance(cur, dict) else None
    posSide = cur.get("posSide") if isinstance(cur, dict) else None
    tp_pct = _vector_float(cfg.get("take_profit_pct") or daemon.get("take_profit_pct")) or 0.009
    tp_price = None
    if entry is not None:
        if side == "short" or posSide == "short":
            tp_price = round(entry * (1 - tp_pct), 2)
        elif side == "long" or posSide == "long":
            tp_price = round(entry * (1 + tp_pct), 2)

    events = _vector_events()
    blobs = [(_vector_json.dumps(e, ensure_ascii=False).lower(), e) for e in events]
    trade_dashboard = _vector_trade_dashboard(cur, strategy_key, strategy_name, cfg)
    xau_trade_dashboard = _vector_trade_dashboard(
        xau_cur, None, None, xau_cfg if isinstance(xau_cfg, dict) else {}, state_suffix="_xau"
    )
    cl_trade_dashboard = _vector_trade_dashboard(
        cl_cur, None, None, cl_cfg if isinstance(cl_cfg, dict) else {}, state_suffix="_cl"
    )
    ng_trade_dashboard = _vector_trade_dashboard(
        ng_cur, None, None, ng_cfg if isinstance(ng_cfg, dict) else {}, state_suffix="_ng"
    )
    fifteen_minute_assets = []
    fifteen_minute_zones = []
    for symbol in (
        "BTC-USDT-SWAP",
        "CL-USDT-SWAP",
        "XAU-USDT-SWAP",
        "NG-USDT-SWAP",
        "XRP-USDT-SWAP",
    ):
        asset_key = symbol.split("-")[0].lower()
        config = _vector_load_json(
            _VECTOR_AUTO
            / ("formal_daemon_config_%s_15m.json" % asset_key),
            {},
        )
        if not isinstance(config, dict):
            config = {}
        shared_state_suffix = "" if asset_key == "btc" else "_" + asset_key
        shared_state = _vector_load_json(
            _VECTOR_AUTO / ("formal_v6_state%s.json" % shared_state_suffix),
            {},
        )
        configured_keys = {
            _vector_canonical_strategy_key(key)
            for key in (config.get("strategy_keys") or [])
            if key
        }
        raw_current = (
            shared_state.get("current")
            if isinstance(shared_state, dict)
            else None
        )
        current_key = _vector_canonical_strategy_key(
            (raw_current or {}).get("strategy_key")
            if isinstance(raw_current, dict)
            else None
        )
        zone_current = raw_current if current_key in configured_keys else None
        pid_file = _VECTOR_AUTO / (
            "formal_daemon_%s_15m.pid" % asset_key
        )
        try:
            zone_pid = int(pid_file.read_text().strip())
            zone_running = bool(
                zone_pid > 0 and is_pid_alive(str(pid_file))
            )
        except Exception:
            zone_pid, zone_running = None, False
        dashboard = _vector_trade_dashboard(
            zone_current,
            None,
            None,
            config,
            state_suffix=shared_state_suffix,
        )
        auto_open = bool(config.get("allow_auto_open"))
        auto_close = bool(config.get("allow_auto_close"))
        chain_ready = bool(
            zone_running
            and config.get("enabled")
            and auto_open
            and config.get("formal_auto_trading_authorized")
            and config.get("gate_authorized_auto_trading")
        )
        runtime_state = (
            "持仓中"
            if _vector_active(zone_current)
            else (
                "监测中"
                if chain_ready
                else (
                    "等待配置15分钟策略"
                    if not configured_keys
                    else "未运行"
                )
            )
        )
        fifteen_minute_assets.append(
            {
                "symbol": symbol,
                "timeframe": "15m",
                "timeframe_label": "15分钟",
                "running": zone_running,
                "pid": zone_pid,
                "status": runtime_state,
                "current": zone_current,
            }
        )
        fifteen_minute_zones.append(
            {
                "symbol": symbol,
                "name": "%s 自动交易（15分钟）" % symbol.split("-")[0],
                "timeframe": "15m",
                "timeframe_label": "15分钟",
                "running": zone_running,
                "pid": zone_pid,
                "auto_open": auto_open,
                "auto_close": auto_close,
                "runtime_state": runtime_state,
                "position": zone_current,
                "config": config,
                "records": dashboard,
                "strategy_count": len(dashboard.get("strategy_stats") or []),
                "assignment_status": (
                    config.get("assignment_status")
                    or "waiting_for_15m_strategy"
                ),
            }
        )

    five_minute_assets = []
    five_minute_zones = []
    for symbol in _vector_configured_symbols("5m", (
        "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
        "XRP-USDT-SWAP", "CL-USDT-SWAP", "XAU-USDT-SWAP",
        "NG-USDT-SWAP", "XAG-USDT-SWAP", "LTC-USDT-SWAP",
        "ADA-USDT-SWAP",
    )):
        asset_key = symbol.split("-")[0].lower()
        config = _vector_load_json(_VECTOR_AUTO/("formal_daemon_config_%s_5m.json"%asset_key),{})
        if not isinstance(config,dict):
            config = {}
        shared_state_suffix = "" if asset_key == "btc" else "_"+asset_key
        shared_state = _vector_load_json(_VECTOR_AUTO/("formal_v6_state%s.json"%shared_state_suffix),{})
        configured_keys = {_vector_canonical_strategy_key(k) for k in (config.get("strategy_keys") or []) if k}
        raw_current = shared_state.get("current") if isinstance(shared_state,dict) else None
        current_key = _vector_canonical_strategy_key((raw_current or {}).get("strategy_key") if isinstance(raw_current,dict) else None)
        zone_current = raw_current if current_key in configured_keys else None
        pid_file = _VECTOR_AUTO/("formal_daemon_%s_5m.pid"%asset_key)
        try:
            zone_pid = int(pid_file.read_text().strip())
            zone_running = bool(zone_pid > 0 and is_pid_alive(str(pid_file)))
        except Exception:
            zone_pid,zone_running = None,False
        dashboard = _vector_trade_dashboard(zone_current,None,None,config,state_suffix=shared_state_suffix)
        auto_open = bool(config.get("allow_auto_open"))
        auto_close = bool(config.get("allow_auto_close"))
        chain_ready = bool(zone_running and config.get("enabled") and auto_open and config.get("formal_auto_trading_authorized") and config.get("gate_authorized_auto_trading"))
        runtime_state = "持仓中" if _vector_active(zone_current) else ("监测中" if chain_ready else ("等待配置5分钟策略" if not configured_keys else "未运行"))
        five_minute_assets.append({"symbol":symbol,"timeframe":"5m","timeframe_label":"5分钟","running":zone_running,"pid":zone_pid,"status":runtime_state,"current":zone_current})
        five_minute_zones.append({
            "symbol": symbol,
            "name": "%s 自动交易（5分钟）" % symbol.split("-")[0],
            "timeframe": "5m",
            "timeframe_label": "5分钟",
            "running": zone_running,
            "pid": zone_pid,
            "auto_open": auto_open,
            "auto_close": auto_close,
            "runtime_state": runtime_state,
            "position": zone_current,
            "config": config,
            "records": dashboard,
            "strategy_count": len(dashboard.get("strategy_stats") or []),
            "assignment_status": config.get("assignment_status") or "waiting_for_5m_strategy",
            "validity_days_total": config.get("validity_days_total"),
            "valid_until": config.get("valid_until"),
            "days_remaining": config.get("days_remaining"),
            "validity_countdown_zh": config.get("validity_countdown_zh"),
            "strategy_validity": config.get("strategy_validity") or {},
        })

    full_pass = bool(logic.get("ok") and preflight.get("ok") and active_verify.get("ok") and tp_verify.get("ok"))
    waiting_real_position = bool(active_verify.get("status") == "NO_ACTIVE_POSITION_REAL_FALLBACK_NOT_EXECUTED")
    safe_ready_waiting = bool(logic.get("ok") and preflight.get("ok") and waiting_real_position)

    if full_pass:
        verify_label = "完整通过"
        verify_level = "full_pass"
    elif safe_ready_waiting:
        verify_label = "逻辑通过 / OKX只读通过 / 等待真实持仓验证"
        verify_level = "waiting_real_position"
    else:
        verify_label = "未通过"
        verify_level = "failed"

    self_check = {
        "full_pass": full_pass,
        "safe_ready_waiting_real_position": safe_ready_waiting,
        "waiting_real_position": waiting_real_position,
        "verify_label": verify_label,
        "verify_level": verify_level,
        "status_api_side_effect_free": True,
        "status_api_does_not_run_selftest": True,
        "status_api_does_not_place_orders": True,
        "logic_chain_self_test": logic,
        "real_okx_preflight_readonly": preflight,
        "real_active_position_verify_once": active_verify,
        "take_profit_path_verify": tp_verify,
        "daemon_running": daemon_running,
        "auto_open": allow_open,
        "auto_close": allow_close,
        "formal_auth": formal_auth,
        "gate_auth": gate_auth,
        "notify_ready": notify_ready,
        "full_balance": (cfg.get("position_mode") or daemon.get("position_mode")) == "full_balance",
        "leverage_allowed": int(cfg.get("leverage") or daemon.get("leverage") or 20) in (20, 30, 50),
        "stop_loss_allowed": float(cfg.get("stop_loss_pct") or daemon.get("stop_loss_pct") or 0.009) in (0.003, 0.006, 0.009),
    }

    try:
        import auto_trade_symbol_priority as _symbol_priority
        priority_policy = _symbol_priority.status()
    except Exception as priority_error:
        priority_policy = {"ok": False, "error": str(priority_error)}

    try:
        import auto_trade_strategy_rating as _strategy_rating
        strategy_rating = _strategy_rating.status(refresh_if_stale=True)
    except Exception as rating_error:
        strategy_rating = {"ok": False, "error": str(rating_error), "ratings": []}
    try:
        import auto_trade_daily_report as _daily_report
        daily_trade_records = _daily_report.daily_records(limit=5)
    except Exception as daily_error:
        daily_trade_records = []
        strategy_rating.setdefault("daily_record_error", str(daily_error))

    return {
        "ok": True,
        "stage": "vector_safe_real_verify_v3_status",
        "time": _vector_time.strftime("%Y-%m-%d %H:%M:%S"),
        "assets": [
            {"symbol": "BTC-USDT-SWAP", "running": daemon_running,
             "timeframe": "1h", "timeframe_label": "1小时",
             "status": "持仓中" if active else "监测中", "current": cur},
            {"symbol": "CL-USDT-SWAP", "running": cl_running,
             "timeframe": "1h", "timeframe_label": "1小时",
             "status": "持仓中" if _vector_active(cl_cur) else ("监测中" if cl_running else "未运行"),
             "current": cl_cur, "pid": cl_pid},
            {"symbol": "XAU-USDT-SWAP", "running": xau_running,
             "timeframe": "1h", "timeframe_label": "1小时",
             "status": "持仓中" if _vector_active(xau_cur) else ("监测中" if xau_running else "未运行"),
             "current": xau_cur, "pid": xau_pid},
            {"symbol": "NG-USDT-SWAP", "running": ng_running,
             "timeframe": "1h", "timeframe_label": "1小时",
             "status": "持仓中" if _vector_active(ng_cur) else ("监测中" if ng_running else "未运行"),
             "current": ng_cur, "pid": ng_pid},
        ] + fifteen_minute_assets + five_minute_assets,
        "asset_zones": [
            {
                "symbol": "BTC-USDT-SWAP", "name": "BTC 自动交易（1小时）",
                "timeframe": "1h", "timeframe_label": "1小时",
                "running": daemon_running, "auto_open": allow_open, "auto_close": allow_close,
                "runtime_state": "持仓中" if active else ("监测中" if daemon_running else "未运行"),
                "position": cur, "config": cfg, "records": trade_dashboard,
                "strategy_count": len(trade_dashboard.get("strategy_stats") or []),
                "assignment_status": "active",
            },
            {
                "symbol": "CL-USDT-SWAP", "name": "CL 自动交易（1小时）",
                "timeframe": "1h", "timeframe_label": "1小时",
                "running": cl_running,
                "auto_open": bool((cl_cfg or {}).get("allow_auto_open")),
                "auto_close": bool((cl_cfg or {}).get("allow_auto_close")),
                "runtime_state": "持仓中" if _vector_active(cl_cur) else (
                    "监测中" if cl_running and bool((cl_cfg or {}).get("allow_auto_open"))
                    else ("已停用" if cl_running else "未运行")
                ),
                "position": cl_cur, "config": cl_cfg, "records": cl_trade_dashboard,
                "strategy_count": len(cl_trade_dashboard.get("strategy_stats") or []),
                "assignment_status": (cl_cfg or {}).get("cl_strategy_assignment_status") or "pending_cl_specific_strategies",
            },
            {
                "symbol": "XAU-USDT-SWAP", "name": "XAU 自动交易（1小时）",
                "timeframe": "1h", "timeframe_label": "1小时",
                "running": xau_running,
                "auto_open": bool((xau_cfg or {}).get("allow_auto_open")),
                "auto_close": bool((xau_cfg or {}).get("allow_auto_close")),
                "runtime_state": "持仓中" if _vector_active(xau_cur) else (
                    "监测中" if xau_running and bool((xau_cfg or {}).get("allow_auto_open")) else (
                        "仅监测（等待XAU专用策略）" if xau_running else "未运行"
                    )
                ),
                "position": xau_cur, "config": xau_cfg, "records": xau_trade_dashboard,
                "strategy_count": len(xau_trade_dashboard.get("strategy_stats") or []),
                "assignment_status": (xau_cfg or {}).get("xau_strategy_assignment_status") or "pending_xau_specific_strategies",
            },
            {
                "symbol": "NG-USDT-SWAP", "name": "NG 自动交易（1小时）",
                "timeframe": "1h", "timeframe_label": "1小时",
                "running": ng_running,
                "auto_open": bool((ng_cfg or {}).get("allow_auto_open")),
                "auto_close": bool((ng_cfg or {}).get("allow_auto_close")),
                "runtime_state": "持仓中" if _vector_active(ng_cur) else (
                    "监测中" if ng_running and bool((ng_cfg or {}).get("allow_auto_open"))
                    else ("已停用" if ng_running else "未运行")
                ),
                "position": ng_cur, "config": ng_cfg, "records": ng_trade_dashboard,
                "strategy_count": len(ng_trade_dashboard.get("strategy_stats") or []),
                "assignment_status": (ng_cfg or {}).get("ng_strategy_assignment_status") or "pending_ng_specific_strategies",
            },
        ] + fifteen_minute_zones + five_minute_zones,
        "priority_policy": priority_policy,
        "strategy_rating": strategy_rating,
        "daily_trade_records": daily_trade_records,
        "runtime_state": runtime,
        "position": {
            "status": "持仓中" if active else "无持仓",
            "strategy_key": (cur or {}).get("strategy_key") if isinstance(cur, dict) else None,
            "strategy_name": (cur or {}).get("strategy_name") if isinstance(cur, dict) else None,
            "side": side,
            "posSide": posSide,
            "symbol": (cur or {}).get("symbol") if isinstance(cur, dict) else "BTC-USDT-SWAP",
            "entry_price": entry,
            "stop_loss_price": _vector_stop(cur),
            "take_profit_pct": tp_pct,
            "take_profit_price": tp_price,
            "exchange_side_stop_verified": bool((cur or {}).get("exchange_side_stop_verified")) if isinstance(cur, dict) else False,
            "attached_stop_loss_source": (cur or {}).get("attached_stop_loss_source") if isinstance(cur, dict) else None,
        },
        "strategy": {
            "name": strategy_name,
            "key": strategy_key,
            "signal": sig.get("signal") or "none",
            "side": sig.get("side"),
            "reason": sig.get("reason") or (
                "等待策略触发" if strategy_key else "BTC 1h 无挂载运行策略"
            ),
            "metrics": sig.get("metrics") if isinstance(sig, dict) else None,
        },
        "execution": {
            "will_open_when_strategy_triggers": chain_ready,
            "will_open_with_stop_loss": bool(chain_ready and logic.get("case_1_strategy_trigger_entry_payload_attached_sl_direct_verified")),
            "will_fallback_order_algo_if_attach_missing": bool(logic.get("case_2_attach_missing_fallback_order_algo_created_and_verified")),
            "will_send_message": bool(chain_ready and notify_ready),
            "will_record_events": True,
            "will_take_profit_close": bool(allow_close and tp_verify.get("ok")),
            "allow_auto_open": allow_open,
            "allow_auto_close": allow_close,
            "formal_auto_trading_authorized": formal_auth,
            "gate_authorized_auto_trading": gate_auth,
            "notification_real_channel_ready": notify_ready,
            "position_mode": cfg.get("position_mode") or daemon.get("position_mode"),
            "leverage": cfg.get("leverage") or daemon.get("leverage"),
            "stop_loss_pct": cfg.get("stop_loss_pct") or daemon.get("stop_loss_pct"),
            "take_profit_pct": tp_pct,
        },
        "diagnostics": {
            "price": sig.get("price") or daemon.get("price") or daemon.get("btc_price"),
            "ema_order": sig.get("ema_order") or daemon.get("ema_order"),
            "kdj": sig.get("kdj") or daemon.get("kdj"),
            "cci": sig.get("cci") or daemon.get("cci"),
        },
        "records": {
            "recent": events[-24:],
            "open_records": [e for blob, e in blobs if "open" in blob or "开仓" in blob or "order_sent" in blob][-12:],
            "close_records": [e for blob, e in blobs if "close" in blob or "平仓" in blob][-12:],
            "take_profit_records": [e for blob, e in blobs if "take_profit" in blob or "止盈" in blob][-12:],
            # System-wide recent trades (all formal_v6_state*), not BTC-1h only.
            "trades": _vector_aggregate_all_trades(15),
            "strategy_stats": trade_dashboard["strategy_stats"],
            "rate_definition": trade_dashboard["rate_definition"],
            "scope": "all_symbols",
        },
        "self_check": self_check,
        "raw": {"config": cfg, "daemon": daemon, "executor": executor, "current": cur}
    }

@app.before_request
def vector_safe_real_verify_v3_api():
    if _vector_req.path == "/api/vector/auto_trade/latest_records":
        if not _vector_auth_ok():
            return _vector_unauth()
        if _vector_req.method != "GET":
            return _vector_jsonify({"ok": False, "error": "GET_REQUIRED"}), 405
        try:
            import auto_trade_daily_report as _latest_report
            return _vector_jsonify(_latest_report.sync_latest_trade_records(limit=20))
        except Exception as e:
            return _vector_jsonify({
                "ok": False,
                "schema": "qiyu_latest_auto_trade_records_v1",
                "max_records": 20,
                "record_count": 0,
                "records": [],
                "error": str(e),
            }), 500

    if _vector_req.path == "/api/vector/auto_trade/risk_settings":
        if not _vector_auth_ok():
            return _vector_unauth()
        if _vector_req.method != "POST":
            return _vector_jsonify({"ok": False, "error": "POST_REQUIRED"}), 405
        try:
            payload = _vector_req.get_json(silent=True) or {}
            symbol = str(payload.get("symbol") or "BTC-USDT-SWAP").upper()
            if symbol not in ("BTC-USDT-SWAP", "CL-USDT-SWAP", "XAU-USDT-SWAP", "NG-USDT-SWAP", "XAG-USDT-SWAP", "LTC-USDT-SWAP", "ADA-USDT-SWAP", "XRP-USDT-SWAP"):
                return _vector_jsonify({"ok": False, "error": "不支持的自动交易标的"}), 400
            try:
                timeframe = backtest_engine_v2.normalize_timeframe(
                    payload.get("timeframe") or "1h"
                )
            except Exception as exc:
                return _vector_jsonify(
                    {"ok": False, "error": str(exc)}
                ), 400
            leverage = int(payload.get("leverage"))
            stop_loss_pct = float(payload.get("stop_loss_pct"))
            if leverage not in (20, 30, 50):
                return _vector_jsonify({"ok": False, "error": "杠杆只能选择20x、30x或50x"}), 400
            if stop_loss_pct not in (0.003, 0.006, 0.009):
                return _vector_jsonify({"ok": False, "error": "止损只能选择0.3%、0.6%或0.9%"}), 400
            if symbol == "BTC-USDT-SWAP" and timeframe == "1h":
                import auto_trade_formal_daemon as _risk_daemon
                updated = _risk_daemon.update_config(leverage=leverage, stop_loss_pct=stop_loss_pct)
                cfg = (updated or {}).get("config") or {}
            else:
                if timeframe in ("15m","5m"):
                    cfg_name = "formal_daemon_config_%s_%s.json" % (
                        symbol.split("-")[0].lower(), timeframe
                    )
                else:
                    cfg_name = {
                        "CL-USDT-SWAP": "formal_daemon_config_cl.json",
                        "XAU-USDT-SWAP": "formal_daemon_config_xau.json",
                        "NG-USDT-SWAP": "formal_daemon_config_ng.json",
                    }[symbol]
                cfg_path = _VECTOR_AUTO / cfg_name
                cfg = _vector_load_json(cfg_path, {})
                if not isinstance(cfg, dict):
                    return _vector_jsonify({"ok": False, "error": "%s配置读取失败" % symbol}), 500
                cfg["leverage"] = leverage
                cfg["stop_loss_pct"] = stop_loss_pct
                _vector_write_json(cfg_path, cfg)
            if int(cfg.get("leverage", 0)) != leverage or abs(float(cfg.get("stop_loss_pct", 0)) - stop_loss_pct) > 0.0000001:
                return _vector_jsonify({"ok": False, "error": "配置持久化校验失败"}), 500
            message = (
                "=== 栖语自动交易参数调整 ===\n"
                "自动交易系统的杠杆止损组合已调整\n"
                "标的：%s\n"
                "周期：%s\n"
                "杠杆：%sx\n"
                "止损：%.1f%%\n"
                "生效范围：该标的下一次所有监测策略的新开仓\n"
                "当前已有持仓：不追溯修改"
            ) % (
                symbol,
                "5分钟" if timeframe == "5m" else ("15分钟" if timeframe == "15m" else "1小时"),
                leverage,
                stop_loss_pct * 100,
            )
            try:
                import auto_trade_formal_notify as _risk_notify
                notify_result = _risk_notify.send_message(message, kind="risk_settings_changed", meta={"symbol": symbol, "timeframe": timeframe, "leverage": leverage, "stop_loss_pct": stop_loss_pct}, dry_run=False)
            except Exception as notify_error:
                notify_result = {"ok": False, "sent": False, "error": str(notify_error)}
            try:
                import auto_trade_formal_v6_executor as _risk_executor
                _risk_executor._append_event("risk_settings_changed", {"symbol": symbol, "timeframe": timeframe, "leverage": leverage, "stop_loss_pct": stop_loss_pct, "notification_sent": bool((notify_result or {}).get("sent"))}, "all_monitored_strategies")
            except Exception:
                pass
            return _vector_jsonify({"ok": True, "symbol": symbol, "timeframe": timeframe, "leverage": leverage, "stop_loss_pct": stop_loss_pct, "applies_to": "next_new_positions_selected_symbol_and_timeframe", "existing_position_unchanged": True, "notification_sent": bool((notify_result or {}).get("sent")), "notification": notify_result})
        except Exception as e:
            return _vector_jsonify({"ok": False, "error": str(e)}), 500

    if _vector_req.path == "/api/vector/auto_trade/status":
        if not _vector_auth_ok():
            return _vector_unauth()
        try:
            return _vector_jsonify(_vector_status_payload_cached())
        except Exception as e:
            return _vector_jsonify({"ok": False, "stage": "vector_safe_real_verify_v3_status", "error": str(e)}), 500

    if _vector_req.path == "/api/vector/auto_trade/verify_active_once":
        if not _vector_auth_ok():
            return _vector_unauth()
        if _vector_req.method != "POST":
            return _vector_jsonify({"ok": False, "error": "POST_REQUIRED"}), 405
        try:
            import sys as _sys
            if "/root" not in _sys.path:
                _sys.path.insert(0, "/root")
            import auto_trade_formal_v6_executor as ex
            if not hasattr(ex, "vector_real_active_position_verify_once"):
                return _vector_jsonify({"ok": False, "error": "verify function missing"}), 500
            res = ex.vector_real_active_position_verify_once()
            return _vector_jsonify(res)
        except Exception as e:
            return _vector_jsonify({"ok": False, "error": str(e)}), 500

    return None
# VECTOR_SAFE_REAL_VERIFY_V3_WEB_PATCH_END

# PURGE_818C_V7_NO_CACHE_START
@app.after_request
def purge_818c_v7_no_cache(response):
    try:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    except Exception:
        pass
    return response
# PURGE_818C_V7_NO_CACHE_END

if __name__ == "__main__":
    def _write_web_pid():
        try:
            with open("/root/web_server.pid", "w") as f:
                f.write(str(os.getpid()) + "\n")
        except Exception:
            pass

    _write_web_pid()
    # Re-sync PID after bind in case the runtime process identity differs.
    try:
        @app.before_request
        def _ensure_web_pid_synced():
            if getattr(app, "_web_pid_synced", False):
                return None
            _write_web_pid()
            app._web_pid_synced = True
            return None
    except Exception:
        pass
    # Warm the auto-trade roster cache so the first UI paint is not empty/degraded.
    try:
        def _warm_vector_status():
            try:
                _vector_status_payload_cached()
            except Exception:
                pass
        threading.Thread(target=_warm_vector_status, name="vector-status-warm", daemon=True).start()
    except Exception:
        pass
    app.run(host="0.0.0.0", port=8080, threaded=True)
