# -*- coding: utf-8 -*-
"""EasyQuant-style automated factor mining on OKX candle caches.

Public eqlib is A-share oriented. This module keeps the *framework habit*
(initialize → feature matrix → rank → certify) but mines factors from local
OKX formal_*_candles_cache.json so creation is professional and reproducible.

Does NOT place trades. Output is research evidence for GLM mechanism design.
"""
from __future__ import print_function

import json
import math
import os
from datetime import datetime
from pathlib import Path


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def probe_easyquant():
    """Capability snapshot (eqlib optional + local OKX miner always available)."""
    mode = str(os.environ.get("QIYU_EASYQUANT_MODE") or "mine").strip().lower()
    out = {
        "ok": True,
        "mode": mode,
        "provider": "easyquant_style",
        "available": True,
        "backend": "okx_candle_factor_miner_v1",
        "notes": [
            "Local OKX candle factor miner active (EasyQuant-style automation).",
            "eqlib A-share package is optional and not required for crypto swaps.",
            "Prefer research R2/S3 long history when configured; formal_* remains live short window.",
        ],
        "probed_at": _now(),
    }
    if mode in ("0", "off", "false", "no", "disabled"):
        out["ok"] = False
        out["available"] = False
        out["notes"] = ["QIYU_EASYQUANT_MODE=off"]
        return out
    try:
        from . import research_candle_store as rcs
        out["research_candles"] = rcs.probe()
    except Exception as exc:
        out["research_candles"] = {"ok": False, "error": str(exc)[:120]}
    try:
        import eqlib  # noqa: F401
        out["eqlib"] = getattr(__import__("eqlib"), "__version__", "present")
        out["notes"].append("eqlib also importable (A-share); not used as OKX fill engine.")
    except Exception as exc:
        out["notes"].append("eqlib_optional_missing: %s" % exc)
    endpoint = str(os.environ.get("QIYU_EASYQUANT_ENDPOINT") or "").strip()
    if endpoint:
        out["endpoint"] = endpoint
    return out


def resolve_candle_cache(symbol, timeframe):
    """Map SYMBOL+tf → /root/auto_trade/formal_*_candles_cache.json."""
    sym = str(symbol or "").upper()
    tf = str(timeframe or "5m").lower()
    base = sym.split("-")[0].lower()
    # normalize commodity suffixes
    aliases = {
        "cl": "cl", "ng": "ng", "xau": "xau", "xag": "xag",
        "btc": "btc", "eth": "eth", "sol": "sol", "ada": "ada",
        "xrp": "xrp", "ltc": "ltc", "bnb": "bnb", "doge": "doge",
    }
    key = aliases.get(base, base)
    name = "formal_%s_%s_candles_cache.json" % (key, tf)
    path = _root() / "auto_trade" / name
    if path.exists():
        return path
    # try without swap naming quirks
    alt = _root() / "auto_trade" / ("formal_%s-%s_candles_cache.json" % (key, tf))
    return path if path.exists() else (alt if alt.exists() else path)


def load_candles(symbol, timeframe, max_bars=1200, prefer_research=None, lookback_days=None):
    """Load candles for creation/research.

    Preference:
      1) Research store (R2/S3/local long history) when enabled & populated
      2) formal_* short rolling live cache (never mutated by research path)

    prefer_research: None → auto from env QIYU_CREATION_PREFER_RESEARCH (default 1)
    """
    if prefer_research is None:
        prefer_research = str(os.environ.get("QIYU_CREATION_PREFER_RESEARCH") or "1").strip().lower() not in (
            "0", "false", "no", "off",
        )
    lookback_days = int(
        lookback_days
        if lookback_days is not None
        else (os.environ.get("QIYU_RESEARCH_LOOKBACK_DAYS") or 400)
    )

    research_meta = None
    if prefer_research:
        try:
            from . import research_candle_store as rcs
            # allow large max_bars for research; cap by env
            cap = int(os.environ.get("QIYU_RESEARCH_MAX_BARS") or max(int(max_bars), 50000))
            got = rcs.load_for_creation(
                symbol, timeframe, lookback_days=lookback_days, max_bars=cap,
            )
            research_meta = {
                "ok": got.get("ok"),
                "backend": got.get("backend"),
                "n": got.get("n"),
                "error": got.get("error"),
                "start_ts": got.get("start_ts"),
                "end_ts": got.get("end_ts"),
            }
            if got.get("ok") and got.get("candles"):
                rows = got["candles"]
                if max_bars and len(rows) > int(max_bars):
                    rows = rows[-int(max_bars):]
                return {
                    "ok": True,
                    "path": "research_store:%s" % got.get("backend"),
                    "n": len(rows),
                    "candles": rows,
                    "source": "research_r2_or_local",
                    "research": research_meta,
                    "fetched_at": got.get("at"),
                    "symbol": symbol,
                    "timeframe": timeframe,
                }
        except Exception as exc:
            research_meta = {"ok": False, "error": str(exc)[:200]}

    path = resolve_candle_cache(symbol, timeframe)
    if not path.exists():
        return {
            "ok": False,
            "error": "candle_cache_missing",
            "path": str(path),
            "candles": [],
            "research": research_meta,
            "hint_zh": "短窗缺失且研究仓无数据；请配置 R2 并回填，或等待 formal 热缓存。",
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": str(path), "candles": [], "research": research_meta}
    candles = raw.get("candles") if isinstance(raw, dict) else raw
    if not isinstance(candles, list):
        return {"ok": False, "error": "bad_candle_shape", "path": str(path), "candles": [], "research": research_meta}
    rows = []
    for c in candles[-int(max_bars):]:
        if not isinstance(c, dict):
            continue
        try:
            rows.append({
                "ts": c.get("ts"),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
            })
        except Exception:
            continue
    return {
        "ok": len(rows) >= 80,
        "path": str(path),
        "n": len(rows),
        "candles": rows,
        "source": "formal_short_cache",
        "research": research_meta,
        "note_zh": (
            "使用实盘 formal 短窗；若需 2024YTD 请配置 R2 并运行 research_candles_backfill_r2.py"
            if research_meta and not research_meta.get("ok")
            else None
        ),
        "fetched_at": (raw.get("fetched_at") if isinstance(raw, dict) else None),
        "symbol": symbol,
        "timeframe": timeframe,
    }


def _sma(xs, n):
    out = [None] * len(xs)
    s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / float(n)
    return out


def _std(xs, n):
    out = [None] * len(xs)
    for i in range(len(xs)):
        if i < n - 1:
            continue
        window = xs[i - n + 1: i + 1]
        m = sum(window) / float(n)
        var = sum((v - m) ** 2 for v in window) / float(max(n - 1, 1))
        out[i] = var ** 0.5
    return out


def _build_factor_matrix(candles):
    c = [r["close"] for r in candles]
    h = [r["high"] for r in candles]
    l = [r["low"] for r in candles]
    o = [r["open"] for r in candles]
    n = len(c)
    ret1 = [None] + [(c[i] / c[i - 1] - 1.0) for i in range(1, n)]
    ret3 = [None] * 3 + [(c[i] / c[i - 3] - 1.0) for i in range(3, n)]
    ret12 = [None] * 12 + [(c[i] / c[i - 12] - 1.0) for i in range(12, n)]
    ma20 = _sma(c, 20)
    std20 = _std(c, 20)
    vol_z = []
    for i in range(n):
        if ma20[i] is None or std20[i] is None or std20[i] <= 1e-12:
            vol_z.append(None)
        else:
            vol_z.append((c[i] - ma20[i]) / std20[i])
    range_pct = [((h[i] - l[i]) / c[i]) if c[i] else None for i in range(n)]
    upper_wick = [
        ((h[i] - max(o[i], c[i])) / c[i]) if c[i] else None for i in range(n)
    ]
    lower_wick = [
        ((min(o[i], c[i]) - l[i]) / c[i]) if c[i] else None for i in range(n)
    ]
    # rolling 288-ish for 5m≈24h; scale with tf-agnostic 96 bars as portable window
    win = 96
    dist_high = [None] * n
    dist_low = [None] * n
    for i in range(n):
        if i < win - 1:
            continue
        hh = max(h[i - win + 1: i + 1])
        ll = min(l[i - win + 1: i + 1])
        dist_high[i] = (c[i] / hh - 1.0) if hh else None
        dist_low[i] = (c[i] / ll - 1.0) if ll else None
    rets_abs = [abs(x) if x is not None else None for x in ret1]
    atr_proxy = _sma([((h[i] - l[i]) / c[i]) if c[i] else 0.0 for i in range(n)], 14)

    return {
        "ret_1": ret1,
        "ret_3": ret3,
        "ret_12": ret12,
        "close_z_20": vol_z,
        "range_pct": range_pct,
        "upper_wick_pct": upper_wick,
        "lower_wick_pct": lower_wick,
        "dist_roll_high": dist_high,
        "dist_roll_low": dist_low,
        "atr_pct_14": atr_proxy,
        "abs_ret_1": rets_abs,
    }


def _forward_returns(closes, horizon=3):
    n = len(closes)
    out = [None] * n
    for i in range(n - horizon):
        if closes[i]:
            out[i] = closes[i + horizon] / closes[i] - 1.0
    return out


def _rank_factor(values, fwd, direction="long_high", q=0.8):
    """Quantile long/short on factor → trade returns list + stats."""
    pairs = []
    for v, r in zip(values, fwd):
        if v is None or r is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            continue
        pairs.append((float(v), float(r)))
    if len(pairs) < 40:
        return None
    pairs.sort(key=lambda x: x[0])
    k = max(5, int(len(pairs) * (1.0 - float(q))))
    if direction == "long_high":
        selected = pairs[-k:]
        sign = 1.0
    elif direction == "long_low":
        selected = pairs[:k]
        sign = 1.0
    elif direction == "short_high":
        selected = pairs[-k:]
        sign = -1.0
    else:  # short_low
        selected = pairs[:k]
        sign = -1.0
    rets = [sign * r for _, r in selected]
    if not rets:
        return None
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    wr = len(wins) / float(len(rets))
    avg_win = (sum(wins) / float(len(wins))) if wins else 0.0
    avg_loss = (sum(losses) / float(len(losses))) if losses else 0.0
    mean = sum(rets) / float(len(rets))
    # equity curve from unit-notional trades
    eq = [1.0]
    for r in rets:
        eq.append(eq[-1] * (1.0 + r))
    payoff = (avg_win / abs(avg_loss)) if avg_loss < 0 else None
    return {
        "n": len(rets),
        "win_rate": wr,
        "mean_net": mean,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff": payoff,
        "returns": rets,
        "equity_curve": eq,
        "direction_rule": direction,
        "quantile": q,
    }


def mine_factors(symbol, timeframe, horizon=3, top_k=5, max_bars=1200):
    """Automated factor dig → ranked candidates with trade-return series."""
    loaded = load_candles(symbol, timeframe, max_bars=max_bars)
    if not loaded.get("ok"):
        return {
            "ok": False,
            "error": loaded.get("error") or "load_fail",
            "path": loaded.get("path"),
            "factors": [],
            "at": _now(),
        }
    candles = loaded["candles"]
    closes = [r["close"] for r in candles]
    matrix = _build_factor_matrix(candles)
    fwd = _forward_returns(closes, horizon=int(horizon))
    specs = [
        ("close_z_20", "long_low", "均值回归：价格低于均线过深后回升"),
        ("close_z_20", "short_high", "均值回归：价格高于均线过深后回落"),
        ("dist_roll_low", "long_low", "滚动低点外延后收回（扫荡多）"),
        ("dist_roll_high", "short_high", "滚动高点外延后收回（扫荡空）"),
        ("lower_wick_pct", "long_high", "长下影线后多头回收"),
        ("upper_wick_pct", "short_high", "长上影线后空头回收"),
        ("ret_12", "long_high", "短动量延续"),
        ("ret_12", "short_high", "短动量衰竭反手"),
        ("range_pct", "long_low", "压缩后方向选择（低波）"),
        ("atr_pct_14", "long_high", "波动扩张环境"),
    ]
    scored = []
    for name, rule, thesis in specs:
        series = matrix.get(name) or []
        stats = _rank_factor(series, fwd, direction=rule, q=0.8)
        if not stats:
            continue
        # score: mean_net * sqrt(n) * wr emphasis, punish negative mean
        if stats["mean_net"] <= 0:
            score = stats["mean_net"] * 10.0
        else:
            score = stats["mean_net"] * (stats["n"] ** 0.5) * (0.5 + stats["win_rate"])
        scored.append({
            "factor": name,
            "rule": rule,
            "thesis_zh": thesis,
            "score": score,
            "stats": {
                "n": stats["n"],
                "win_rate": stats["win_rate"],
                "mean_net": stats["mean_net"],
                "avg_win": stats["avg_win"],
                "avg_loss": stats["avg_loss"],
                "payoff": stats["payoff"],
            },
            "returns": stats["returns"],
            "equity_curve": stats["equity_curve"],
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[: int(top_k)]
    return {
        "ok": bool(top),
        "schema": "qiyu_easyquant_factor_mine_v1",
        "symbol": symbol,
        "timeframe": timeframe,
        "horizon_bars": int(horizon),
        "candle_path": loaded.get("path"),
        "n_bars": loaded.get("n"),
        "n_scored": len(scored),
        "factors": top,
        "all_scores": [
            {"factor": x["factor"], "rule": x["rule"], "score": x["score"],
             "win_rate": x["stats"]["win_rate"], "mean_net": x["stats"]["mean_net"], "n": x["stats"]["n"]}
            for x in scored[:12]
        ],
        "at": _now(),
        "note_zh": (
            "EasyQuant 风格自动化因子挖掘：基于本地 OKX K 线分位数信号，"
            "尚未经 STEP A 成交摩擦；仅作创造阶段研究输入。"
        ),
    }


def modeling_envelope(brief=None, focus=None, probe=None, mine=None):
    probe = probe if probe is not None else probe_easyquant()
    focus = focus or {}
    mine = mine or {}
    tops = []
    for f in (mine.get("factors") or [])[:5]:
        tops.append({
            "factor": f.get("factor"),
            "rule": f.get("rule"),
            "thesis_zh": f.get("thesis_zh"),
            "win_rate": (f.get("stats") or {}).get("win_rate"),
            "mean_net": (f.get("stats") or {}).get("mean_net"),
            "n": (f.get("stats") or {}).get("n"),
            "score": f.get("score"),
        })
    return {
        "schema": "qiyu_easyquant_modeling_envelope_v1",
        "brief": str(brief or "").strip(),
        "focus": {
            "symbol": focus.get("symbol"),
            "timeframe": focus.get("timeframe"),
            "direction": focus.get("direction"),
        },
        "easyquant": probe,
        "factor_mine": {
            "ok": bool(mine.get("ok")),
            "n_bars": mine.get("n_bars"),
            "top_factors": tops,
            "note_zh": mine.get("note_zh"),
        },
        "guidance_zh": (
            "创造阶段必须优先使用已挖掘因子的可复现统计，禁止空口编造胜率。"
            "mechanism_spec 的 entry_logic 应锚定 top_factors 的因果叙述；"
            "正式过关仍以 ADA5 四复核 + OKX 摩擦回测为准。"
        ),
        "built_at": _now(),
    }
