#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase5-only Cross-asset feature enrichment (not production)."""
from __future__ import print_function
import math
import copy


CROSS_EXTRA_FEATURES = (
    "beta20", "residual", "residual_z20", "corr_delta5",
    "breadth_score", "lead_vol_z20", "vol_div", "corr_high_flag",
)


def _zscore(xs, win=20):
    out = [float("nan")] * len(xs)
    for i in range(win, len(xs)):
        w = xs[i - win + 1:i + 1]
        vals = [x for x in w if isinstance(x, (int, float)) and math.isfinite(x)]
        if len(vals) < win // 2:
            continue
        m = sum(vals) / float(len(vals))
        var = sum((x - m) ** 2 for x in vals) / float(len(vals))
        sd = math.sqrt(var) if var > 0 else 0.0
        if sd > 0 and isinstance(xs[i], (int, float)) and math.isfinite(xs[i]):
            out[i] = (xs[i] - m) / sd
    return out


def enrich_cross_frame(frame):
    """Add residual/beta/breadth/vol-div columns; fail-closed NaN when inputs missing."""
    fr = frame
    n = len(fr.get("close") or [])
    lead = fr.get("lead_ret1") or [float("nan")] * n
    lag = fr.get("lag_ret1") or [float("nan")] * n
    corr = fr.get("lead_lag_corr20") or [float("nan")] * n
    sync = fr.get("cross_sync_score") or [float("nan")] * n
    vol = fr.get("vol_z20") or [float("nan")] * n

    beta = [float("nan")] * n
    residual = [float("nan")] * n
    corr_delta = [float("nan")] * n
    breadth = [float("nan")] * n
    vol_div = [float("nan")] * n
    corr_high = [float("nan")] * n
    win = 20
    for i in range(win, n):
        xs = lead[i - win + 1:i + 1]
        ys = lag[i - win + 1:i + 1]
        pairs = [(x, y) for x, y in zip(xs, ys)
                 if isinstance(x, (int, float)) and isinstance(y, (int, float))
                 and math.isfinite(x) and math.isfinite(y)]
        if len(pairs) >= win // 2:
            mx = sum(p[0] for p in pairs) / float(len(pairs))
            my = sum(p[1] for p in pairs) / float(len(pairs))
            num = sum((p[0] - mx) * (p[1] - my) for p in pairs)
            den = sum((p[0] - mx) ** 2 for p in pairs)
            if den > 0:
                beta[i] = num / den
        if (isinstance(beta[i], (int, float)) and math.isfinite(beta[i])
                and isinstance(lead[i], (int, float)) and math.isfinite(lead[i])
                and isinstance(lag[i], (int, float)) and math.isfinite(lag[i])):
            residual[i] = lag[i] - beta[i] * lead[i]
        if (i >= 5 and isinstance(corr[i], (int, float)) and math.isfinite(corr[i])
                and isinstance(corr[i - 5], (int, float)) and math.isfinite(corr[i - 5])):
            corr_delta[i] = corr[i] - corr[i - 5]
        if (isinstance(sync[i], (int, float)) and math.isfinite(sync[i])
                and isinstance(corr[i], (int, float)) and math.isfinite(corr[i])):
            breadth[i] = abs(sync[i]) * max(corr[i], 0.0)
        if isinstance(corr[i], (int, float)) and math.isfinite(corr[i]):
            corr_high[i] = 1.0 if corr[i] >= 0.4 else 0.0

    lead_abs = [
        abs(x) if isinstance(x, (int, float)) and math.isfinite(x) else float("nan")
        for x in lead
    ]
    lead_vol = _zscore(lead_abs, win=20)
    for i in range(n):
        if (isinstance(vol[i], (int, float)) and math.isfinite(vol[i])
                and isinstance(lead_vol[i], (int, float)) and math.isfinite(lead_vol[i])):
            vol_div[i] = vol[i] - lead_vol[i]

    fr["beta20"] = beta
    fr["residual"] = residual
    fr["residual_z20"] = _zscore(residual, win=20)
    fr["corr_delta5"] = corr_delta
    fr["breadth_score"] = breadth
    fr["lead_vol_z20"] = lead_vol
    fr["vol_div"] = vol_div
    fr["corr_high_flag"] = corr_high
    return fr


def coverage_of(frame, col, start=0, end=None):
    xs = (frame.get(col) or [])[start:end]
    if not xs:
        return 0.0
    return sum(
        1 for x in xs if isinstance(x, (int, float)) and math.isfinite(x)
    ) / float(len(xs))
