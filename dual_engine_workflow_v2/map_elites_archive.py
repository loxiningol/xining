# -*- coding: utf-8 -*-
"""MAP-Elites / novelty archive — keep diverse behavioral niches, not Top-N clones."""
from __future__ import print_function

import math
from datetime import datetime


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


FAMILIES = (
    "mean_reversion", "liquidity_sweep", "vol_squeeze_break",
    "crowding_fade", "liquidation_bounce", "trend_pullback",
    "data_driven", "other",
)

HORIZON_BUCKETS = ("lt_15m", "15m_1h", "1h_6h", "gt_6h")
FREQ_BUCKETS = ("rare", "medium", "frequent")


def _horizon_bucket(horizon):
    h = str(horizon or "").lower()
    if "5m" in h or "10m" in h or h in ("label_horizon", "h=label"):
        return "lt_15m"
    if "15m" in h or "30m" in h:
        return "15m_1h"
    if "1h" in h or "2h" in h or "4h" in h or "6h" in h:
        return "1h_6h"
    if "24h" in h or "day" in h:
        return "gt_6h"
    return "15m_1h"


def _freq_bucket(n_hits, n_bars):
    if not n_bars:
        return "medium"
    rate = float(n_hits or 0) / float(max(n_bars, 1))
    if rate < 0.02:
        return "rare"
    if rate > 0.15:
        return "frequent"
    return "medium"


def behavior_descriptor(hypothesis, probe_best=None, n_bars=None):
    family = str(hypothesis.get("family") or "other")
    if family not in FAMILIES:
        family = "other"
    horizon = _horizon_bucket(hypothesis.get("horizon"))
    n_hits = ((probe_best or {}).get("n_hits")) if probe_best else None
    freq = _freq_bucket(n_hits, n_bars)
    direction = str(hypothesis.get("predicted_direction") or "unknown")[:24]
    return {
        "family": family,
        "horizon_bucket": horizon,
        "freq_bucket": freq,
        "direction": direction,
        "cell": "%s|%s|%s" % (family, horizon, freq),
    }


def quality_score(probe_best=None, antifalsify=None, efr=None):
    q = 0.0
    if probe_best and probe_best.get("passed"):
        q += max(0.0, float(probe_best.get("mean_net") or 0.0) * 1000.0)
        q += min(3.0, abs(float(probe_best.get("t_stat") or 0.0)))
    if antifalsify and antifalsify.get("passed"):
        q += 2.0 + 0.3 * float(antifalsify.get("support_n") or 0)
        q -= 0.5 * float(antifalsify.get("oppose_n") or 0)
    if efr and efr.get("efr") is not None:
        q += max(0.0, min(5.0, float(efr.get("efr") or 0.0)))
    return q


def novelty_vs_archive(descriptor, archive):
    """Simple behavioral novelty: unseen cell → 1; else distance via quality gap."""
    cell = descriptor.get("cell")
    if cell not in (archive or {}):
        return 1.0
    return 0.2


def upsert(archive, hypothesis, probe_best=None, antifalsify=None, efr=None, n_bars=None):
    archive = dict(archive or {})
    desc = behavior_descriptor(hypothesis, probe_best=probe_best, n_bars=n_bars)
    cell = desc["cell"]
    q = quality_score(probe_best=probe_best, antifalsify=antifalsify, efr=efr)
    nov = novelty_vs_archive(desc, archive)
    row = {
        "hypothesis_id": hypothesis.get("hypothesis_id"),
        "mechanism_id": hypothesis.get("mechanism_id"),
        "source": hypothesis.get("source"),
        "path": hypothesis.get("path"),
        "descriptor": desc,
        "quality": q,
        "novelty": nov,
        "probe_mean_net": (probe_best or {}).get("mean_net"),
        "probe_factor": (probe_best or {}).get("factor"),
        "antifalsify_passed": (antifalsify or {}).get("passed"),
        "efr": (efr or {}).get("efr"),
        "hypothesis": hypothesis,
        "updated_at": _now(),
    }
    prev = archive.get(cell)
    if prev is None or float(row["quality"]) >= float(prev.get("quality") or -1e9):
        archive[cell] = row
        replaced = bool(prev is not None)
    else:
        replaced = False
        row = prev
    return archive, row, replaced


def elites(archive, min_quality=0.0):
    rows = list((archive or {}).values())
    rows = [r for r in rows if float(r.get("quality") or 0) >= float(min_quality)]
    rows.sort(key=lambda r: float(r.get("quality") or 0), reverse=True)
    return rows


def similarity_jaccard(a_hints, b_hints):
    a = set([str(x) for x in (a_hints or []) if x])
    b = set([str(x) for x in (b_hints or []) if x])
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def dedupe_hypotheses(hypotheses, max_jaccard=0.85):
    """Drop near-duplicate factor-hint sets unless they declare new info."""
    kept = []
    dropped = []
    for h in hypotheses or []:
        hints = h.get("factor_hints") or h.get("observable_proxy") or []
        dup = False
        for k in kept:
            j = similarity_jaccard(hints, k.get("factor_hints") or k.get("observable_proxy"))
            same_family = (h.get("family") == k.get("family"))
            if j >= float(max_jaccard) and same_family:
                if not h.get("new_information_zh"):
                    dup = True
                    dropped.append({
                        "hypothesis_id": h.get("hypothesis_id"),
                        "duplicate_of": k.get("hypothesis_id"),
                        "jaccard": j,
                    })
                    break
        if not dup:
            kept.append(h)
    return {"ok": True, "kept": kept, "dropped": dropped, "n_kept": len(kept)}


def probe():
    return {
        "ok": True,
        "provider": "map_elites_archive_v1",
        "cells": "family|horizon|freq",
        "at": _now(),
    }
