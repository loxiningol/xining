# -*- coding: utf-8 -*-
"""Optional EasyQuant / eqlib bridge for strategy modeling collaboration.

Public PyPI EasyQuant (eqlib) targets China A-shares. This repo trades OKX
crypto swaps — so the bridge is a *capability probe + modeling envelope*,
not a pretend OKX backtest via A-share APIs.

Set QIYU_EASYQUANT_MODE:
  off       — disabled (default)
  probe     — detect import / endpoint only
  envelope  — attach modeling notes into GLM brief (no fake fills)
"""
from __future__ import print_function

import os
from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def probe_easyquant():
    """Return capability snapshot without raising."""
    mode = str(os.environ.get("QIYU_EASYQUANT_MODE") or "probe").strip().lower()
    out = {
        "ok": False,
        "mode": mode,
        "provider": "easyquant",
        "available": False,
        "backend": None,
        "notes": [],
        "probed_at": _now(),
    }
    if mode in ("0", "off", "false", "no", "disabled"):
        out["notes"].append("QIYU_EASYQUANT_MODE=off")
        return out

    # 1) Python package eqlib (AlanFokCo/EasyQuant) — A-share oriented
    try:
        import eqlib  # noqa: F401
        ver = getattr(eqlib, "__version__", None) or "unknown"
        out["available"] = True
        out["backend"] = "eqlib"
        out["eqlib_version"] = ver
        out["notes"].append(
            "eqlib imported; A-share event-driven framework — NOT a drop-in OKX swap backtester."
        )
        out["ok"] = True
    except Exception as exc:
        out["notes"].append("eqlib_import_failed: %s" % exc)

    # 2) Optional HTTP endpoint if user configures one later
    endpoint = str(os.environ.get("QIYU_EASYQUANT_ENDPOINT") or "").strip()
    if endpoint:
        out["endpoint"] = endpoint
        out["notes"].append("QIYU_EASYQUANT_ENDPOINT set; HTTP client not yet implemented.")
        out["available"] = True
        out["backend"] = out.get("backend") or "http_endpoint"
        out["ok"] = True

    if not out["available"]:
        out["notes"].append(
            "EasyQuant not wired for OKX yet. Continue with GLM mechanism modeling; "
            "do not claim EasyQuant backtest evidence."
        )
    return out


def modeling_envelope(brief=None, focus=None, probe=None):
    """Build a small dict GLM can read as external modeling context."""
    probe = probe if probe is not None else probe_easyquant()
    focus = focus or {}
    return {
        "schema": "qiyu_easyquant_modeling_envelope_v0",
        "brief": str(brief or "").strip(),
        "focus": {
            "symbol": focus.get("symbol"),
            "timeframe": focus.get("timeframe"),
            "direction": focus.get("direction"),
        },
        "easyquant": probe,
        "guidance_zh": (
            "若 EasyQuant 不可用：仅用 GLM 因果建模，禁止伪造回测数字。"
            "若仅有 eqlib：可参考其事件驱动建模习惯（initialize/handle_data），"
            "但样本与成交必须仍走本仓 OKX/STEP A 证据链。"
        ),
        "built_at": _now(),
    }
