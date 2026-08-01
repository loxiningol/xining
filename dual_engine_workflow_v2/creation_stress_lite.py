# -*- coding: utf-8 -*-
"""Stage ⑤ Stress — Backtrader-lite extreme replay + AutoGen-style red team.

No backtrader/autogen install required. Replays the worst historical equity
drawdown windows and applies adversarial shocks (slippage, latency, gap).
"""
from __future__ import print_function

from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def probe():
    out = {
        "ok": True,
        "provider": "backtrader_autogen_stress_lite_v1",
        "backends": {"backtrader": False, "autogen": False},
        "notes": [
            "Local extreme-window equity replay + adversarial shock agents.",
            "Real backtrader/autogen optional later.",
        ],
        "probed_at": _now(),
    }
    for name, key in (("backtrader", "backtrader"), ("autogen", "autogen")):
        try:
            __import__(name)
            out["backends"][key] = True
        except Exception:
            pass
    return out


def _equity_from_returns(rets):
    eq = [1.0]
    for r in rets:
        eq.append(eq[-1] * (1.0 + float(r)))
    return eq


def _max_drawdown(eq):
    peak = eq[0]
    mdd = 0.0
    for x in eq:
        if x > peak:
            peak = x
        dd = x / peak - 1.0
        if dd < mdd:
            mdd = dd
    return mdd


def _worst_windows(rets, window=80, top_k=3):
    """Find windows with worst cumulative return (crisis proxies)."""
    n = len(rets)
    if n < window + 5:
        window = max(20, n // 3)
    scored = []
    for i in range(0, max(1, n - window + 1), max(1, window // 4)):
        seg = rets[i:i + window]
        if len(seg) < max(10, window // 2):
            continue
        cum = 1.0
        for r in seg:
            cum *= (1.0 + float(r))
        scored.append((cum - 1.0, i, seg))
    scored.sort(key=lambda x: x[0])  # worst first
    return scored[: int(top_k)]


def backtest_extreme(trade_returns, max_dd_limit=-0.18):
    """Backtrader-lite: stress trade returns through crisis-like windows."""
    rets = [float(x) for x in (trade_returns or []) if x is not None]
    out = {
        "ok": False,
        "source": "backtrader_lite",
        "passed": False,
        "windows": [],
        "at": _now(),
    }
    if len(rets) < 20:
        out["error"] = "insufficient_trades"
        return out
    full_eq = _equity_from_returns(rets)
    full_mdd = _max_drawdown(full_eq)
    windows = []
    for cum, start, seg in _worst_windows(rets):
        eq = _equity_from_returns(seg)
        mdd = _max_drawdown(eq)
        windows.append({
            "start_idx": start,
            "n": len(seg),
            "cum_return": cum,
            "max_drawdown": mdd,
            "pass": mdd >= float(max_dd_limit),  # mdd is negative
        })
    out["windows"] = windows
    out["full_max_drawdown"] = full_mdd
    out["full_total_return"] = full_eq[-1] - 1.0
    # Majority of crisis windows + full equity within bound (lite adapter)
    win_pass = sum(1 for w in windows if w["pass"])
    out["passed"] = (
        bool(windows)
        and win_pass >= max(1, (len(windows) + 1) // 2)
        and full_mdd >= float(max_dd_limit)
    )
    out["ok"] = True
    out["max_dd_limit"] = max_dd_limit
    return out


def red_team_attack(trade_returns, max_dd_limit=-0.18):
    """AutoGen-style attacker agent: slippage / gap / liquidity shocks."""
    rets = [float(x) for x in (trade_returns or []) if x is not None]
    attacks = []
    if len(rets) < 20:
        return {
            "ok": False,
            "source": "autogen_redteam_lite",
            "passed": False,
            "error": "insufficient",
            "attacks": [],
            "at": _now(),
        }

    # Attack A: every fill pays the same 3bp adverse return delta.  The old
    # implementation added 3bp to losing trades, accidentally improving them.
    shocked_a = [r - 0.0003 for r in rets]
    eq_a = _equity_from_returns(shocked_a)
    mdd_a = _max_drawdown(eq_a)
    attacks.append({
        "name": "adverse_slippage_3bp",
        "per_trade_return_delta": -0.0003,
        "n_shocked_trades": len(shocked_a),
        "max_drawdown": mdd_a,
        "total_return": eq_a[-1] - 1.0,
        "pass": mdd_a >= float(max_dd_limit),
    })

    # Attack B: every 10th trade becomes a gap loss of -1.2%
    shocked_b = list(rets)
    for i in range(0, len(shocked_b), 10):
        shocked_b[i] = min(shocked_b[i], -0.012)
    eq_b = _equity_from_returns(shocked_b)
    mdd_b = _max_drawdown(eq_b)
    attacks.append({
        "name": "periodic_gap_loss",
        "max_drawdown": mdd_b,
        "total_return": eq_b[-1] - 1.0,
        "pass": mdd_b >= float(max_dd_limit),
    })

    # Attack C: liquidity drought — inflate losses 1.5x, shrink wins 0.7x
    shocked_c = [(r * 0.7 if r > 0 else r * 1.5) for r in rets]
    eq_c = _equity_from_returns(shocked_c)
    mdd_c = _max_drawdown(eq_c)
    attacks.append({
        "name": "liquidity_drought",
        "max_drawdown": mdd_c,
        "total_return": eq_c[-1] - 1.0,
        "pass": mdd_c >= float(max_dd_limit),
    })

    n_pass = sum(1 for a in attacks if a["pass"])
    passed = n_pass >= max(2, len(attacks) - 1)  # tolerate one failed attack vector
    return {
        "ok": True,
        "source": "autogen_redteam_lite",
        "passed": passed,
        "attacks": attacks,
        "n_pass": n_pass,
        "at": _now(),
        "note_zh": "攻击 Agent：滑点 / 跳空 / 流动性枯竭；不达标则回溯修正逻辑。",
    }


def run_stress(trade_returns, max_dd_limit=-0.18):
    bt = backtest_extreme(trade_returns, max_dd_limit=max_dd_limit)
    red = red_team_attack(trade_returns, max_dd_limit=max_dd_limit)
    return {
        "ok": bool(bt.get("ok")) and bool(red.get("ok")),
        "schema": "qiyu_creation_stage5_stress_v1",
        "passed": bool(bt.get("passed")) and bool(red.get("passed")),
        "backtrader": bt,
        "red_team": red,
        "probe": probe(),
        "at": _now(),
    }
