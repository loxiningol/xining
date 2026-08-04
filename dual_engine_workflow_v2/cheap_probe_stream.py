# -*- coding: utf-8 -*-
"""Streaming Level-0 cheap probes — summary-only, tiny resident set.

Covers many mechanism cells without materializing 500 backtests in RAM.
Batch size is small (default 6); each probe keeps only a summary dict on disk.
"""
from __future__ import print_function

import gc
import json
import math
import os
from datetime import datetime
from pathlib import Path

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

from . import probe_protocol as probes
from . import data_evidence_ladder as ladder
from .process_safe_state import atomic_write_json


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def campaign_dir(campaign_id):
    d = _root() / "auto_trade" / "dual_engine" / "research_campaigns" / str(campaign_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _finite(v):
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except Exception:
        return None


def _as_float32_matrix(factor_matrix, names):
    """Build a compact dict of float32 arrays; missing → NaN."""
    out = {}
    if np is None:
        for name in names:
            series = list((factor_matrix or {}).get(name) or [])
            out[name] = series
        return out
    for name in names:
        series = list((factor_matrix or {}).get(name) or [])
        arr = np.zeros(len(series), dtype=np.float32)
        for i, v in enumerate(series):
            x = _finite(v)
            arr[i] = np.nan if x is None else np.float32(x)
        out[name] = arr
    return out


def _forward_returns_from_candles(candles, horizon):
    n = len(candles or [])
    if np is None:
        out = [None] * n
        for i in range(n):
            j = i + int(horizon)
            if j >= n:
                break
            try:
                c0 = float(candles[i]["close"])
                c1 = float(candles[j]["close"])
                if c0:
                    out[i] = (c1 / c0) - 1.0
            except Exception:
                out[i] = None
        return out
    closes = np.array([_finite(c.get("close")) or np.nan for c in candles], dtype=np.float32)
    out = np.full(n, np.nan, dtype=np.float32)
    h = int(horizon)
    if h <= 0 or n <= h:
        return out
    out[: n - h] = (closes[h:] / closes[: n - h]) - 1.0
    return out


def _event_mask_from_series(series, side="high", q=0.80):
    """Causal rolling quantile mask without pandas."""
    if np is None:
        return probes._causal_quantile_mask(series, side=side, q=q)
    vals = np.asarray(series, dtype=np.float32)
    n = len(vals)
    out = np.zeros(n, dtype=np.uint8)
    hist = []
    window = 240
    min_history = 80
    for i in range(n):
        v = vals[i]
        if len(hist) >= min_history and not np.isnan(v):
            qq = float(q) if side == "high" else (1.0 - float(q))
            ordered = sorted(hist)
            idx = max(0, min(len(ordered) - 1, int(qq * (len(ordered) - 1))))
            thr = ordered[idx]
            out[i] = 1 if ((v >= thr) if side == "high" else (v <= thr)) else 0
        if not np.isnan(v):
            hist.append(float(v))
        if len(hist) > window:
            hist.pop(0)
    return out


def _independent_count(mask, horizon=3, timeframe="5m"):
    gap = probes._independence_gap_bars(horizon, timeframe=timeframe)
    if np is not None and hasattr(mask, "tolist"):
        mask_list = [bool(x) for x in mask.tolist()]
    else:
        mask_list = list(mask)
    raw, indep = probes._independent_events(mask_list, gap, merge_bars=max(3, int(horizon)))
    return len(raw), len(indep), indep


def _summary_from_returns(rets, indep_n, raw_n, cost_bp=15.0):
    vals = [_finite(x) for x in rets]
    vals = [x for x in vals if x is not None]
    if not vals:
        return {
            "event_count": 0,
            "independent_event_count": 0,
            "gross_mean_bp": None,
            "net_mean_bp": None,
            "t_hac": 0.0,
            "hit_rate": None,
            "failure_state": "SAMPLE_INADEQUATE",
        }
    mean = sum(vals) / float(len(vals))
    t = probes._newey_west_t(vals)
    hit = sum(1 for x in vals if x > 0) / float(len(vals))
    gross_bp = mean * 10000.0
    net_bp = gross_bp - float(cost_bp)
    abs_mean = sum(abs(x) for x in vals) / float(len(vals))
    if indep_n < probes.MIN_INDEPENDENT_EVENTS:
        state = "SAMPLE_INADEQUATE"
    elif mean > 0 and t >= 1.64 and net_bp > 0:
        state = "CHEAP_PASS"
    elif mean > 0 and t >= 1.64 and net_bp <= 0:
        state = "EXECUTION_MAPPING_FAILURE"
    elif abs_mean > abs(mean) * 1.8 and t < 1.64:
        state = "VOLATILITY_EFFECT_ONLY"
    elif mean > 0 and t >= 1.0:
        state = "NEAR_MISS_DIAGNOSTIC"
    else:
        state = "NO_DIRECTIONAL_EFFECT"
    return {
        "event_count": int(raw_n),
        "independent_event_count": int(indep_n),
        "n_filled": len(vals),
        "gross_mean_bp": round(gross_bp, 4),
        "net_mean_bp": round(net_bp, 4),
        "t_hac": round(float(t), 4),
        "hit_rate": round(hit, 4),
        "abs_mean_bp": round(abs_mean * 10000.0, 4),
        "failure_state": state,
    }


def build_mechanism_cells(brief="", max_cells=80):
    """Stage-1 map: many cells, almost no compute — structured defs only."""
    from . import research_branch_manager as branch_mgr
    from . import mechanism_graph as mechanism_graph
    cells = []
    # Brief-locked trees only — never fall back to the full TREES union.
    trees = branch_mgr.trees_for_brief(brief) or []
    allowed_extra_families = set()
    for tree in trees:
        tid = str(tree.get("tree_id") or "")
        if tid == "exhaustion_recovery":
            allowed_extra_families.add("exhaustion")
        elif tid == "donchian_trend_break":
            allowed_extra_families.add("donchian_trend_break")
        elif tid == "vol_squeeze_break":
            allowed_extra_families.add("vol_squeeze")
    mechanism_features = {
        str(row.get("mechanism_id") or ""): list(
            row.get("factor_hints") or row.get("observable_proxy") or []
        )
        for row in mechanism_graph.load_graph()
    }
    # Fixed high-value exhaustion / squeeze proxy cells (report §九)
    extra = [
        {"cell_id": "impact_reversal", "family": "exhaustion", "prediction": "shock_then_reversal",
         "features": ["ret_1", "abs_ret_1", "volume_z"], "cost_class": "cheap"},
        {"cell_id": "impact_elasticity_decay", "family": "exhaustion",
         "prediction": "unit_volume_impact_decays", "features": ["abs_ret_1", "volume_z", "range_pct"],
         "cost_class": "cheap"},
        {"cell_id": "volume_extreme_price_dull", "family": "exhaustion",
         "prediction": "high_volume_small_body", "features": ["volume_z", "candle_efficiency"],
         "cost_class": "cheap"},
        {"cell_id": "downside_velocity_decay", "family": "exhaustion",
         "prediction": "neg_return_speed_decays", "features": ["downside_velocity_decay", "ret_3"],
         "cost_class": "cheap"},
        {"cell_id": "failed_new_low", "family": "exhaustion",
         "prediction": "new_low_not_held", "features": ["close_location", "lower_wick_pct", "close_z_20"],
         "cost_class": "cheap"},
        {"cell_id": "cross_asset_nonconfirm", "family": "exhaustion",
         "prediction": "alt_low_btc_not_confirm", "features": ["close_z_20", "ret_3"],
         "cost_class": "cheap"},
        {"cell_id": "vol_expand_no_direction", "family": "vol_squeeze",
         "prediction": "abs_move_up_sign_noise", "features": ["squeeze_persistence", "volatility_acceleration"],
         "cost_class": "cheap"},
        {"cell_id": "break_accept_fail", "family": "vol_squeeze",
         "prediction": "break_then_reject", "features": ["breakout_acceptance", "expansion_score"],
         "cost_class": "cheap"},
        {"cell_id": "pullback_second_leg", "family": "vol_squeeze",
         "prediction": "break_pullback_continuation", "features": ["trend_efficiency_12", "expansion_score"],
         "cost_class": "cheap"},
        {"cell_id": "donchian_valid_break", "family": "donchian_trend_break",
         "prediction": "channel_break_then_continue",
         "features": ["dist_roll_high", "breakout_acceptance", "volume_z"],
         "cost_class": "cheap"},
        {"cell_id": "donchian_false_break", "family": "donchian_trend_break",
         "prediction": "break_then_reclaim",
         "features": ["dist_roll_high", "upper_wick_pct", "close_location"],
         "cost_class": "cheap"},
        {"cell_id": "donchian_htf_aligned", "family": "donchian_trend_break",
         "prediction": "htf_filter_with_trend_break",
         "features": ["ret_12", "trend_efficiency_12", "dist_roll_high"],
         "cost_class": "cheap"},
        {"cell_id": "donchian_vol_confirm", "family": "donchian_trend_break",
         "prediction": "break_with_expansion",
         "features": ["expansion_score", "breakout_acceptance", "dist_roll_high"],
         "cost_class": "cheap"},
    ]
    for tree in trees:
        for bid, br in (tree.get("branches") or {}).items():
            for mid in br.get("mechanism_ids") or []:
                cells.append({
                    "cell_id": "%s__%s" % (bid, mid),
                    "tree_id": tree.get("tree_id"),
                    "branch_id": bid,
                    "mechanism_id": mid,
                    "family": tree.get("tree_id"),
                    "prediction": br.get("title_zh") or bid,
                    # Never let a named mechanism silently fall back to the same
                    # arbitrary factor as unrelated mechanisms.
                    "features": mechanism_features.get(str(mid), []),
                    "cost_class": "cheap",
                    "task": br.get("task"),
                })
    for row in extra:
        # Only append extras inside brief-locked families (no unconditional dump).
        if allowed_extra_families and str(row.get("family") or "") in allowed_extra_families:
            cells.append(dict(row))
    # Expand each lattice cell into a few *mechanism* variants (not RSI±1 spam).
    feature_bundles = (
        ["close_z_20", "rsi_14", "exhaustion_score"],
        ["abs_ret_1", "volume_z", "range_pct"],
        ["downside_velocity_decay", "lower_wick_pct", "close_location"],
        ["squeeze_persistence", "volatility_acceleration", "expansion_score"],
        ["breakout_acceptance", "trend_efficiency_12", "expansion_score"],
        ["micro_depth_imbalance", "micro_trade_flow_imbalance", "micro_half_spread_rate"],
    )
    base_cells = []
    seen_base = set()
    for c in cells:
        cid = str(c.get("cell_id") or "")
        if cid and cid not in seen_base:
            base_cells.append(c)
            seen_base.add(cid)
    expanded = list(base_cells)
    # Add family-compatible variants only after every base mechanism has one
    # chance.  The old cell-by-cell expansion exhausted max_cells on the first
    # few mechanisms and produced dozens of identical fallback probes.
    for c in base_cells:
        base = str(c.get("cell_id") or "cell")
        blob = "%s %s %s" % (c.get("family"), c.get("prediction"), base)
        blob = blob.lower()
        if any(x in blob for x in ("exhaust", "衰竭", "panic", "liquid")):
            bundle_ids = (0, 1, 2)
        elif any(x in blob for x in ("squeeze", "break", "trend", "donchian", "突破", "压缩")):
            bundle_ids = (3, 4)
        else:
            bundle_ids = (0, 1)
        for bi in bundle_ids:
            feats = feature_bundles[bi]
            row = dict(c)
            row["cell_id"] = "%s__fb%d" % (base, bi)
            row["features"] = list(feats)
            row["cost_class"] = "cheap"
            expanded.append(row)
    # de-dupe by actual research signature, not presentation cell_id alone.
    seen = set()
    out = []
    for c in expanded:
        signature = (
            str(c.get("mechanism_id") or c.get("cell_id") or ""),
            str(c.get("prediction") or ""),
            tuple(sorted(str(x) for x in (c.get("features") or []))),
        )
        if signature in seen:
            continue
        seen.add(signature)
        out.append(c)
        if len(out) >= int(max_cells):
            break
    return out


def _pick_factor(factor_matrix, hints, allow_fallback=None):
    keys = list((factor_matrix or {}).keys())
    for h in hints or []:
        if h in (factor_matrix or {}) and any(_finite(x) is not None for x in (factor_matrix.get(h) or [])[:50]):
            return h
    if allow_fallback is None:
        allow_fallback = not bool(hints)
    if not allow_fallback:
        return None
    # fallbacks common on tiny VPS
    for h in (
        "close_z_20", "rsi_14", "exhaustion_score", "squeeze_persistence",
        "volatility_acceleration", "abs_ret_1", "range_pct", "volume_z",
        "micro_depth_imbalance", "micro_trade_flow_imbalance",
    ):
        if h in (factor_matrix or {}):
            return h
    return keys[0] if keys else None


def evaluate_cheap_probe(cell, factor_matrix, candles, timeframe="5m",
                         horizons=(1, 3, 6), available_data=None, micro_meta=None):
    """One cheap probe: no trade objects, no full backtest, summary only."""
    cell = dict(cell or {})
    mid = cell.get("mechanism_id")
    claim = ladder.evaluate_mechanism_claim(mid, available_data, micro_meta) if mid else {
        "may_research": True, "mode": "proxy_claim", "active_claim": cell.get("cell_id"),
    }
    if mid and not claim.get("may_research"):
        return {
            "probe_id": "P_%s" % cell.get("cell_id"),
            "cell_id": cell.get("cell_id"),
            "family": cell.get("family"),
            "mechanism_id": mid,
            "claim_mode": claim.get("mode"),
            "failure_state": claim.get("block_code") or "DATA_INADEQUATE",
            "event_count": 0,
            "independent_event_count": 0,
            "gross_mean_bp": None,
            "net_mean_bp": None,
            "t_hac": 0.0,
            "tier": "L0_cheap",
            "at": _now(),
        }

    factor = _pick_factor(factor_matrix, cell.get("features"), allow_fallback=False)
    if not factor:
        return {
            "probe_id": "P_%s" % cell.get("cell_id"),
            "cell_id": cell.get("cell_id"),
            "family": cell.get("family"),
            "mechanism_id": mid,
            "failure_state": "PROXY_INADEQUATE",
            "event_count": 0,
            "independent_event_count": 0,
            "gross_mean_bp": None,
            "net_mean_bp": None,
            "t_hac": 0.0,
            "tier": "L0_cheap",
            "at": _now(),
        }

    series = (factor_matrix or {}).get(factor) or []
    side = "low" if any(k in str(cell.get("prediction") or "").lower() + str(cell.get("cell_id") or "")
                        for k in ("exhaust", "衰竭", "low", "fail", "dull", "decay")) else "high"
    if "vol_" in str(cell.get("cell_id") or "") or "squeeze" in str(cell.get("family") or ""):
        side = "low" if "persist" in factor or "squeeze" in factor else "high"
    mask = _event_mask_from_series(series, side=side, q=0.85)

    best = None
    for h in horizons:
        raw_n, indep_n, indep_idx = _independent_count(mask, horizon=h, timeframe=timeframe)
        fwd = _forward_returns_from_candles(candles, h)
        rets = []
        direction = -1.0 if side == "low" and "exhaust" in str(cell.get("family") or "") else 1.0
        # exhaustion cells: buy the bounce → positive if short-horizon recovery
        if any(k in str(cell.get("family") or "") + str(cell.get("cell_id") or "")
               for k in ("exhaust", "衰竭", "vacuum", "impact", "failed_new")):
            direction = 1.0
        for i in indep_idx:
            if i >= len(fwd):
                continue
            if np is not None and hasattr(fwd, "__getitem__"):
                try:
                    r = fwd[i]
                    if r is None or (isinstance(r, float) and (math.isnan(r) or math.isinf(r))):
                        continue
                    if np.isnan(r):
                        continue
                    rets.append(direction * float(r))
                except Exception:
                    continue
            else:
                r = _finite(fwd[i]) if i < len(fwd) else None
                if r is not None:
                    rets.append(direction * float(r))
        summ = _summary_from_returns(rets, indep_n, raw_n)
        summ.update({
            "horizon_bars": int(h),
            "factor": factor,
            "side": side,
            "indep_idx": list(indep_idx)[:400],
            "direction": direction,
        })
        if best is None:
            best = summ
        else:
            # prefer path-hit rate when present, else |t|
            def key(s):
                st = 0 if s.get("failure_state") == "SAMPLE_INADEQUATE" else 1
                return (
                    st,
                    float(s.get("profit_first_rate") or -1.0),
                    abs(float(s.get("t_hac") or 0)),
                    float(s.get("gross_mean_bp") or -1e9),
                )
            if key(summ) > key(best):
                best = summ

    # Path-hit proxy on the winning horizon (cheap tier).
    path_stats = {
        "profit_first_rate": None,
        "mfe_p50_bp": None,
        "mae_p50_bp": None,
    }
    try:
        from . import path_outcome as path_out
        h_best = int((best or {}).get("horizon_bars") or (horizons[0] if horizons else 6))
        indep = list((best or {}).get("indep_idx") or [])
        direction = float((best or {}).get("direction") or 1.0)
        if candles and indep:
            pack = path_out.summarize_signals(
                candles, indep, direction, h_best, mapping="next_bar_open",
            )
            path_stats["profit_first_rate"] = pack.get("profit_first_rate")
            med_mfe = pack.get("median_mfe_pct")
            med_mae = pack.get("median_mae_pct")
            path_stats["mfe_p50_bp"] = (
                None if med_mfe is None else round(float(med_mfe) * 10000.0, 2)
            )
            path_stats["mae_p50_bp"] = (
                None if med_mae is None else round(float(med_mae) * 10000.0, 2)
            )
            if best is not None:
                best["profit_first_rate"] = path_stats["profit_first_rate"]
    except Exception:
        pass

    out = {
        "probe_id": "P_%s_h%s" % (cell.get("cell_id"), (best or {}).get("horizon_bars")),
        "cell_id": cell.get("cell_id"),
        "family": cell.get("family"),
        "tree_id": cell.get("tree_id"),
        "branch_id": cell.get("branch_id"),
        "mechanism_id": mid,
        "claim_mode": claim.get("mode"),
        "active_claim": claim.get("active_claim"),
        "claim_wording_zh": claim.get("wording_zh"),
        "tier": "L0_cheap",
        "factor": (best or {}).get("factor") or factor,
        "side": (best or {}).get("side") or side,
        "horizon_bars": (best or {}).get("horizon_bars"),
        "event_count": (best or {}).get("event_count") or 0,
        "independent_event_count": (best or {}).get("independent_event_count") or 0,
        "gross_mean_bp": (best or {}).get("gross_mean_bp"),
        "net_mean_bp": (best or {}).get("net_mean_bp"),
        "t_hac": (best or {}).get("t_hac") or 0.0,
        "hit_rate": (best or {}).get("hit_rate"),
        "abs_mean_bp": (best or {}).get("abs_mean_bp"),
        "profit_first_rate": path_stats.get("profit_first_rate"),
        "mfe_p50_bp": path_stats.get("mfe_p50_bp"),
        "mae_p50_bp": path_stats.get("mae_p50_bp"),
        "regime_consistency": None,
        "failure_state": (best or {}).get("failure_state") or "SAMPLE_INADEQUATE",
        "at": _now(),
    }
    return out


def stream_cheap_probes(cells, factor_matrix, candles, campaign_id,
                        timeframe="5m", batch_size=6, max_probes=200,
                        available_data=None, micro_meta=None, progress_cb=None):
    """Stream cells in small batches; persist summaries; release batch memory."""
    cells = list(cells or [])[: int(max_probes)]
    out_path = campaign_dir(campaign_id) / "cheap_probe_summaries.jsonl"
    ckpt_path = campaign_dir(campaign_id) / "cheap_probe_checkpoint.json"
    start_i = 0
    if ckpt_path.exists():
        try:
            ck = json.loads(ckpt_path.read_text(encoding="utf-8"))
            start_i = int(ck.get("next_index") or 0)
        except Exception:
            start_i = 0

    summaries = []
    # reopen append
    mode = "a" if start_i > 0 and out_path.exists() else "w"
    fh = out_path.open(mode, encoding="utf-8")
    try:
        i = start_i
        while i < len(cells):
            batch = cells[i: i + int(batch_size)]
            batch_rows = []
            for cell in batch:
                row = evaluate_cheap_probe(
                    cell, factor_matrix, candles, timeframe=timeframe,
                    available_data=available_data, micro_meta=micro_meta,
                )
                batch_rows.append(row)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            summaries.extend(batch_rows)
            i += len(batch)
            atomic_write_json(ckpt_path, {
                "next_index": i,
                "n_done": i,
                "n_total": len(cells),
                "at": _now(),
            })
            if progress_cb:
                try:
                    progress_cb({"stage": "cheap_stream", "done": i, "total": len(cells)})
                except Exception:
                    pass
            del batch_rows
            gc.collect()
    finally:
        fh.close()

    # rank for promotion
    promote = []
    for s in summaries:
        st = s.get("failure_state")
        if st in (
            "CHEAP_PASS", "EXECUTION_MAPPING_FAILURE", "NEAR_MISS_DIAGNOSTIC",
            "VOLATILITY_EFFECT_ONLY", "DIRECTIONAL_BUT_SMALL",
        ):
            promote.append(s)
    # Prefer cells whose tree/family matches the campaign focus (first cell's tree
    # if homogeneous; else prefer non-null tree_id matching majority).
    focus_trees = {}
    for s in summaries:
        tid = s.get("tree_id") or s.get("family")
        if tid:
            focus_trees[tid] = int(focus_trees.get(tid) or 0) + 1
    primary_tree = None
    if focus_trees:
        primary_tree = sorted(focus_trees.items(), key=lambda kv: -kv[1])[0][0]
    promote.sort(
        key=lambda r: (
            1 if r.get("failure_state") == "CHEAP_PASS" else 0,
            1 if primary_tree and (r.get("tree_id") == primary_tree or r.get("family") == primary_tree) else 0,
            abs(float(r.get("t_hac") or 0)),
            float(r.get("gross_mean_bp") or -1e9),
        ),
        reverse=True,
    )
    return {
        "ok": True,
        "campaign_id": campaign_id,
        "n_cells": len(cells),
        "n_summaries": len(summaries) + start_i,  # approx when resumed
        "n_streamed_this_call": len(summaries),
        "summaries_path": str(out_path),
        "promote_ids": [p.get("probe_id") for p in promote[:12]],
        "promote": promote[:12],
        "state_counts": _count_states(summaries),
        "architecture_zh": (
            "流式廉价探针：每批 4–8 个，只落摘要；"
            "覆盖机制格子数由候选数量决定，不由同时驻留内存决定。"
        ),
        "at": _now(),
    }


def _count_states(rows):
    out = {}
    for r in rows or []:
        k = r.get("failure_state") or "?"
        out[k] = int(out.get(k) or 0) + 1
    return out


def load_summaries(campaign_id, limit=500):
    path = campaign_dir(campaign_id) / "cheap_probe_summaries.jsonl"
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
            if len(rows) >= int(limit):
                break
    return rows


def probe():
    return {
        "ok": True,
        "provider": "cheap_probe_stream_v1",
        "batch_default": 6,
        "keeps_zh": "仅摘要字典 + JSONL；不保留交易对象/全特征副本",
        "at": _now(),
    }
