# -*- coding: utf-8 -*-
"""QuantOracle bridge — deterministic quant math for strategy creation.

Uses https://api.quantoracle.dev so GLM/Codex never invent Sharpe/Kelly/Hurst
by hand during the pre-review creation stage.

Env:
  QIYU_QUANTORACLE_URL   default https://api.quantoracle.dev
  QIYU_QUANTORACLE_MODE  on|off|local_fallback  (default on)
"""
from __future__ import print_function

import json
import os
import urllib.error
import urllib.request
from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _api_base():
    return str(os.environ.get("QIYU_QUANTORACLE_URL") or "https://api.quantoracle.dev").rstrip("/")


def _mode():
    return str(os.environ.get("QIYU_QUANTORACLE_MODE") or "on").strip().lower()


def call(endpoint, params, timeout=30):
    """POST /v1/{endpoint}. Returns {ok, endpoint, data|error, source}."""
    ep = str(endpoint or "").lstrip("/")
    if ep.startswith("v1/"):
        ep = ep[3:]
    mode = _mode()
    if mode in ("0", "off", "false", "no", "disabled"):
        return {"ok": False, "endpoint": ep, "error": "quantoracle_disabled", "source": "off"}

    url = "%s/v1/%s" % (_api_base(), ep)
    body = json.dumps(params or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "qiyu-create-collab/quantoracle-bridge/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            return {
                "ok": True,
                "endpoint": ep,
                "data": data,
                "http_status": getattr(resp, "status", 200),
                "source": "quantoracle",
                "at": _now(),
            }
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            detail = str(exc)
        return {
            "ok": False,
            "endpoint": ep,
            "error": "http_%s" % exc.code,
            "detail": detail,
            "source": "quantoracle",
            "at": _now(),
        }
    except Exception as exc:
        return {
            "ok": False,
            "endpoint": ep,
            "error": str(exc)[:240],
            "source": "quantoracle",
            "at": _now(),
        }


def batch(requests, timeout=60):
    """POST /v1/batch with [{endpoint, params}, ...]."""
    mode = _mode()
    if mode in ("0", "off", "false", "no", "disabled"):
        return {"ok": False, "error": "quantoracle_disabled", "source": "off"}
    payload = {"requests": list(requests or [])}
    url = "%s/v1/batch" % _api_base()
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "qiyu-create-collab/quantoracle-bridge/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            return {"ok": True, "data": data, "source": "quantoracle", "at": _now()}
    except Exception as exc:
        detail = ""
        if hasattr(exc, "read"):
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                detail = str(exc)
        else:
            detail = str(exc)
        return {"ok": False, "error": detail[:300], "source": "quantoracle", "at": _now()}


def _local_sharpe(returns):
    rets = [float(x) for x in (returns or []) if x is not None]
    n = len(rets)
    if n < 5:
        return None
    mean = sum(rets) / float(n)
    var = sum((x - mean) ** 2 for x in rets) / float(max(n - 1, 1))
    vol = var ** 0.5
    if vol <= 1e-12:
        return 0.0
    # bar-level sharpe annualized roughly assuming ~365*24*12 for 5m is heavy;
    # keep period sharpe * sqrt(n) style simple annualization factor unused —
    # return raw period sharpe comparable across factors.
    return mean / vol * (n ** 0.5)


def certify_factor_signal(returns, win_rate=None, avg_win=None, avg_loss=None,
                          equity_curve=None):
    """Run QuantOracle risk/stats suite on a factor's bar returns.

    Uses sequential free endpoints (batch is paid/blocked on free tier).
    Falls back to local math only when API fails and mode allows.
    """
    rets = [float(x) for x in (returns or []) if x is not None]
    out = {
        "schema": "qiyu_quantoracle_cert_v1",
        "ok": False,
        "n_returns": len(rets),
        "calls": {},
        "certified": {},
        "source": None,
        "at": _now(),
    }
    if len(rets) < 8:
        out["error"] = "insufficient_returns"
        return out

    sample = rets[-400:]
    eq = equity_curve
    if not eq:
        s = 1.0
        eq = []
        for r in sample:
            s *= (1.0 + float(r))
            eq.append(s)
    eq_sample = [float(x) for x in eq[-400:]]

    plan = [
        ("stats/sharpe-ratio", {"returns": sample}),
        ("risk/portfolio", {"returns": sample}),
        ("risk/drawdown", {"equity_curve": eq_sample}),
        ("stats/hurst-exponent", {"series": eq_sample}),
    ]
    if win_rate is not None and avg_win is not None and avg_loss is not None:
        try:
            aw = abs(float(avg_win))
            al = abs(float(avg_loss))
            if aw > 0 and al > 0:
                plan.append((
                    "risk/kelly",
                    {
                        "win_rate": float(win_rate),
                        "avg_win": aw,
                        "avg_loss": al,
                    },
                ))
        except Exception:
            pass

    any_ok = False
    for ep, params in plan:
        row = call(ep, params, timeout=30)
        out["calls"][ep] = row
        if row.get("ok"):
            any_ok = True

    if any_ok:
        sh = out["calls"].get("stats/sharpe-ratio") or {}
        port = out["calls"].get("risk/portfolio") or {}
        risk = ((port.get("data") or {}).get("risk") if port.get("ok") else None) or {}
        rets_sum = ((port.get("data") or {}).get("returns") if port.get("ok") else None) or {}
        kelly = out["calls"].get("risk/kelly") or {}
        dd = out["calls"].get("risk/drawdown") or {}
        hurst = out["calls"].get("stats/hurst-exponent") or {}
        sharpe = None
        if sh.get("ok") and isinstance(sh.get("data"), dict):
            sharpe = sh["data"].get("sharpe_ratio")
        dd_data = dd.get("data") if dd.get("ok") else {}
        out["certified"] = {
            "sharpe_ratio": sharpe if sharpe is not None else risk.get("sharpe"),
            "sortino": risk.get("sortino"),
            "calmar": risk.get("calmar"),
            "win_rate": rets_sum.get("win_rate"),
            "profit_factor": rets_sum.get("profit_factor"),
            "ann_vol": rets_sum.get("vol"),
            "kelly_quarter": (kelly.get("data") or {}).get("quarter_kelly") if kelly.get("ok") else None,
            "kelly_recommended": (kelly.get("data") or {}).get("recommended") if kelly.get("ok") else None,
            "max_drawdown": (
                dd_data.get("max_dd")
                if dd_data.get("max_dd") is not None
                else dd_data.get("max_drawdown")
            ),
            "hurst": (hurst.get("data") or {}).get("hurst_exponent") if hurst.get("ok") else None,
            "hurst_interp": (hurst.get("data") or {}).get("interpretation") if hurst.get("ok") else None,
        }
        out["ok"] = True
        out["source"] = "quantoracle"
        return out

    if _mode() in ("local_fallback", "on", "1", "true"):
        out["source"] = "local_fallback"
        errs = [("%s:%s" % (k, (v or {}).get("error"))) for k, v in out["calls"].items()]
        out["error"] = ";".join(errs)[:300]
        out["certified"] = {
            "sharpe_ratio": _local_sharpe(rets),
            "sortino": None,
            "calmar": None,
            "win_rate": (
                sum(1 for x in rets if x > 0) / float(len(rets)) if rets else None
            ),
            "profit_factor": None,
            "ann_vol": None,
            "kelly_quarter": None,
            "kelly_recommended": None,
            "max_drawdown": None,
            "hurst": None,
        }
        out["ok"] = out["certified"].get("sharpe_ratio") is not None
        out["note_zh"] = "QuantOracle 不可用，已用本地可复现公式兜底；不得对外宣称 QuantOracle 认证。"
        return out

    out["error"] = "quantoracle_unavailable"
    return out


def probe():
    r = call("risk/kelly", {"win_rate": 0.55, "avg_win": 0.02, "avg_loss": 0.01}, timeout=15)
    return {
        "ok": bool(r.get("ok")),
        "provider": "quantoracle",
        "base": _api_base(),
        "mode": _mode(),
        "sample": r,
        "probed_at": _now(),
    }
