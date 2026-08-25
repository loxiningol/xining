# -*- coding: utf-8 -*-
"""Closeout helpers: correlation freq, positive-E gap, calibration stage, stale forecast.

Schema helpers for metrics/forecast refactor closeout.
Does NOT mutate live SL/TP/leverage/size or place orders.
"""
from __future__ import print_function

import json
import math
import os
import random
import tempfile
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
LATEST_PATH = AUTO_DIR / "system_forecast_latest.json"
STATE_PATH = AUTO_DIR / "system_forecast_state.json"
CALIBRATION_PATH = AUTO_DIR / "forecast_calibration_weekly.json"
GAP_REPORT_PATH = AUTO_DIR / "frequency_gap_report.json"
CREATION_INPUT_PATH = AUTO_DIR / "strategy_creation_frequency_input.json"
STALE_FLAG_PATH = AUTO_DIR / "forecast_stale_flag.json"

TARGET_FILLS_DAY = (0.5, 1.0)
TARGET_FILLS_WEEK = (3.5, 7.0)
MIN_CREDIBILITY_POSITIVE_E = 0.35
NEAR_ZERO_EQUITY_PCT = 0.05  # |net equity %| below this → near-zero bucket
MIN_SAMPLE_POSITIVE_E = 10  # ban 1-live-trade / thin samples as positive-E
MIN_LIVE_OR_BT_POSITIVE_E = 5  # need live or BT evidence; AI-only ≠ positive-E

_refresh_lock = threading.Lock()
_SCHEDULE_REFRESH_LOCK = threading.Lock()
_SCHEDULE_REFRESH_PENDING = False


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def _patch_state(**kwargs):
    st = _read(STATE_PATH, {}) or {}
    st.update(kwargs)
    st["updated_at"] = _now()
    _atomic(STATE_PATH, st)
    return st


def lightweight_refresh_in_flight():
    st = _read(STATE_PATH, {}) or {}
    return bool(
        _refresh_lock.locked()
        or _SCHEDULE_REFRESH_PENDING
        or st.get("lightweight_refresh_running")
    )


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        delete=False, dir=str(path.parent), mode="w", encoding="utf-8")
    try:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        handle.close()
        Path(handle.name).replace(path)
    except Exception:
        try:
            Path(handle.name).unlink()
        except Exception:
            pass
        raise


def _float(value, default=None):
    try:
        if value in (None, ""):
            return default
        out = float(value)
        if not math.isfinite(out):
            return default
        return out
    except Exception:
        return default


def calibration_stage(n_complete_periods):
    """Stages: 0–3 观测期; 4–7 初步; 8–11 中期; ≥12 基础完成.

    complete_periods=0 → must NOT claim scientific validation.
    """
    n = int(n_complete_periods or 0)
    if n <= 3:
        stage = "观测期"
        banner = (
            "预测模型处于观测期（完整校准周期=%d，需≥4进入初步校准）。"
            "当前数值为统计估计，不代表已验证精度；complete_periods=0时禁止宣称科学验证。"
            % n
        )
        scientifically_validated = False
        ui_banner = True
    elif n <= 7:
        stage = "初步校准"
        banner = "预测模型尚处于初步校准期，当前数值为统计估计，不代表已验证精度。"
        scientifically_validated = False
        ui_banner = True
    elif n <= 11:
        stage = "中期校准"
        banner = "预测模型处于中期校准，精度仍在验证中，不得视为已完成科学验证。"
        scientifically_validated = False
        ui_banner = True
    else:
        stage = "已完成基础预测校准"
        banner = "已完成基础预测校准；仍需持续监控误差，不得过度外推。"
        scientifically_validated = False  # never claim full scientific validation early
        ui_banner = False
    return {
        "complete_periods": n,
        "stage": stage,
        "banner_zh": banner,
        "scientifically_validated": scientifically_validated,
        "ui_must_show_calibration_banner": ui_banner or n < 4,
        "stage_bands": {
            "观测期": "0-3",
            "初步校准": "4-7",
            "中期校准": "8-11",
            "已完成基础预测校准": ">=12",
        },
    }


def _parse_dt(text):
    text = str(text or "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except Exception:
            continue
    return None


def enrich_calibration(calib=None):
    """Attach MAE/MedAE/Bias/coverage/Brier + stage labels. Forever rows kept upstream."""
    hist = calib or _read(CALIBRATION_PATH, {"schema": "qiyu_forecast_calibration_v1", "rows": []})
    rows = list(hist.get("rows") or [])
    complete = [r for r in rows if r.get("realized_weekly_fills") is not None
                and r.get("expected_weekly_fills") is not None
                and r.get("period_kind") != "hourly_snapshot_noise"]
    errors = []
    abs_errors = []
    cover80_hits = 0
    cover80_n = 0
    cover95_hits = 0
    cover95_n = 0
    brier_terms = []
    by_strategy_err = defaultdict(list)
    by_family_err = defaultdict(list)
    pos_e_errors = []
    legacy_errors = []
    event_errors = []
    for r in complete:
        exp = _float(r.get("expected_weekly_fills"))
        real = _float(r.get("realized_weekly_fills"))
        if exp is None or real is None:
            continue
        err = real - exp
        errors.append(err)
        abs_errors.append(abs(err))
        r["error"] = round(err, 4)
        r["abs_error"] = round(abs(err), 4)
        interval = r.get("weekly_interval") or r.get("weekly_opens_range")
        if isinstance(interval, (list, tuple)) and len(interval) >= 2:
            cover80_n += 1
            lo, hi = _float(interval[0], 0), _float(interval[1], 0)
            if lo is not None and hi is not None and lo <= real <= hi:
                cover80_hits += 1
                r["in_80pct_interval"] = True
            else:
                r["in_80pct_interval"] = False
        interval95 = r.get("weekly_interval_95")
        if isinstance(interval95, (list, tuple)) and len(interval95) >= 2:
            cover95_n += 1
            lo, hi = _float(interval95[0], 0), _float(interval95[1], 0)
            if lo is not None and hi is not None and lo <= real <= hi:
                cover95_hits += 1
                r["in_95pct_interval"] = True
            else:
                r["in_95pct_interval"] = False
        daily = _float(r.get("expected_daily_fills"))
        if daily is None and exp is not None:
            daily = exp / 7.0
        if daily is not None:
            p_no = math.exp(-max(0.0, daily) * 3.0)
            y = 1.0 if real <= 0 else 0.0
            brier_terms.append((p_no - y) ** 2)
            r["p_3day_no_entry"] = round(p_no, 6)
            r["brier_term_3day_no_entry"] = round((p_no - y) ** 2, 6)
        for sid, e in (r.get("strategy_errors") or {}).items():
            by_strategy_err[sid].append(float(e))
        for fam, e in (r.get("family_errors") or {}).items():
            by_family_err[fam].append(float(e))
        if r.get("positive_E_error") is not None:
            pos_e_errors.append(float(r["positive_E_error"]))
        src = str(r.get("frequency_source_layer") or "")
        if "legacy" in src:
            legacy_errors.append(err)
        if "event" in src:
            event_errors.append(err)

    def _med(vals):
        if not vals:
            return None
        s = sorted(vals)
        mid = len(s) // 2
        if len(s) % 2:
            return s[mid]
        return 0.5 * (s[mid - 1] + s[mid])

    def _mean(vals):
        if not vals:
            return None
        return round(sum(vals) / float(len(vals)), 4)

    metrics = {
        "MAE": None if not abs_errors else round(sum(abs_errors) / float(len(abs_errors)), 4),
        "MedAE": None if not abs_errors else round(_med(abs_errors), 4),
        "Bias": None if not errors else round(sum(errors) / float(len(errors)), 4),
        "coverage_80pct": (
            None if cover80_n <= 0 else round(float(cover80_hits) / float(cover80_n), 4)),
        "coverage_95pct": (
            None if cover95_n <= 0 else round(float(cover95_hits) / float(cover95_n), 4)),
        "brier_p_3day_no_entry": (
            None if not brier_terms else round(sum(brier_terms) / float(len(brier_terms)), 6)),
        "complete_periods": len(complete),
        "total_persisted_rows": len(rows),
        "strategy_errors_mae": {
            k: _mean([abs(x) for x in v]) for k, v in by_strategy_err.items()
        },
        "family_errors_mae": {
            k: _mean([abs(x) for x in v]) for k, v in by_family_err.items()
        },
        "positive_E_error_mae": _mean([abs(x) for x in pos_e_errors]),
        "legacy_vs_event_error": {
            "legacy_MAE": _mean([abs(x) for x in legacy_errors]),
            "event_MAE": _mean([abs(x) for x in event_errors]),
            "note": "null until both layers have completed periods",
        },
    }
    stage = calibration_stage(len(complete))
    hist["rows"] = rows
    hist["period_metrics"] = metrics
    hist["calibration_stage"] = stage
    hist["updated_at"] = _now()
    if len(complete) == 0:
        hist["validation_claim_forbidden"] = True
        hist["validation_note_zh"] = (
            "complete_periods=0 → 禁止宣称科学验证 / scientifically_validated 必须为 false"
        )
    _atomic(CALIBRATION_PATH, hist)
    return hist


def classify_expectancy_bucket(net_equity_pct, credibility, clearly_positive=None,
                               sample_n=None, live_n=None, backtest_n=None,
                               ci_floor=None, ai_only=False):
    """Strict positive-E gate.

    Requires: after-cost point estimate >0, CI floor >0, sample floor,
    credibility floor, not AI-only, not 1 live trade.
    """
    if net_equity_pct is None:
        return "uncalibrated"
    net = float(net_equity_pct)
    cred = float(credibility or 0.0)
    sample_n = int(sample_n or 0)
    live_n = int(live_n or 0)
    backtest_n = int(backtest_n or 0)
    evidence_n = max(live_n, backtest_n, sample_n)
    if ai_only and evidence_n <= 0:
        return "uncalibrated"
    if abs(net) <= NEAR_ZERO_EQUITY_PCT:
        return "near_zero_E"
    if net < -NEAR_ZERO_EQUITY_PCT:
        return "negative_E"
    # Positive point estimate path — still need floors
    pos_ok = (
        net > NEAR_ZERO_EQUITY_PCT
        and cred >= MIN_CREDIBILITY_POSITIVE_E
        and evidence_n >= MIN_SAMPLE_POSITIVE_E
        and (live_n >= MIN_LIVE_OR_BT_POSITIVE_E or backtest_n >= MIN_SAMPLE_POSITIVE_E)
        and (ci_floor is None or float(ci_floor) > 0)
        and clearly_positive is not False
        and not ai_only
    )
    if clearly_positive is True and pos_ok:
        return "calibrated_positive_E"
    if pos_ok and clearly_positive is None:
        return "calibrated_positive_E"
    if net > NEAR_ZERO_EQUITY_PCT and not pos_ok:
        # positive point but fails floors → uncalibrated (not positive-E)
        return "uncalibrated"
    if cred < MIN_CREDIBILITY_POSITIVE_E:
        return "uncalibrated"
    return "near_zero_E"


def build_positive_expectancy_frequency(per_strategy_rows, total_fillable_weekly=None):
    """Split total forecasted fills by expectancy credibility buckets."""
    buckets = {
        "calibrated_positive_E": 0.0,
        "uncalibrated": 0.0,
        "near_zero_E": 0.0,
        "negative_E": 0.0,
    }
    detail = []
    for row in per_strategy_rows or []:
        if not row.get("can_open"):
            continue
        w = _float(row.get("expected_weekly_fills"), 0.0) or 0.0
        d = _float(row.get("expected_daily_fills"), w / 7.0) or 0.0
        net = row.get("net_expectancy_equity_pct")
        if isinstance(net, dict):
            net = net.get("value")
        cred = row.get("credibility")
        if cred is None:
            exp = row.get("expectancy") or {}
            cred = exp.get("credibility")
        clearly = row.get("clearly_positive_after_cost")
        sample = row.get("sample") or {}
        sample_n = row.get("sample_n") or sample.get("combined_n")
        live_n = row.get("live_n") or sample.get("live_n")
        bt_n = row.get("backtest_n") or sample.get("backtest_n")
        ci = row.get("net_equity_ci_80pct") or row.get("ci_floor")
        ci_floor = None
        if isinstance(ci, (list, tuple)) and ci:
            ci_floor = ci[0]
        elif isinstance(ci, (int, float)):
            ci_floor = ci
        ai_wr = row.get("ai_theoretical_wr_pct") or row.get("ai_wr_pct")
        live_wr = row.get("live_win_rate_pct")
        bt_wr = row.get("backtest_win_rate_pct")
        ai_only = bool(ai_wr is not None and live_wr is None and bt_wr is None
                       and int(sample_n or 0) <= 0)
        bucket = classify_expectancy_bucket(
            net, cred, clearly_positive=clearly,
            sample_n=sample_n, live_n=live_n, backtest_n=bt_n,
            ci_floor=ci_floor, ai_only=ai_only)
        buckets[bucket] += w
        detail.append({
            "strategy_key": row.get("strategy_key"),
            "mechanism_family": row.get("mechanism_family"),
            "expected_weekly_fills": w,
            "expected_daily_fills": d,
            "net_expectancy_equity_pct": net,
            "credibility": cred,
            "sample_n": sample_n,
            "live_n": live_n,
            "backtest_n": bt_n,
            "ci_floor": ci_floor,
            "ai_only": ai_only,
            "bucket": bucket,
            "thresholds": {
                "after_cost_point_gt_0": NEAR_ZERO_EQUITY_PCT,
                "min_credibility": MIN_CREDIBILITY_POSITIVE_E,
                "min_sample": MIN_SAMPLE_POSITIVE_E,
                "min_live_or_bt": MIN_LIVE_OR_BT_POSITIVE_E,
                "ci_floor_must_gt_0": True,
                "ai_only_forbidden": True,
                "single_live_trade_forbidden": True,
            },
        })

    total_w = sum(buckets.values())
    fillable_w = _float(total_fillable_weekly, total_w) or total_w
    pos_w = buckets["calibrated_positive_E"]
    day_lo, day_hi = TARGET_FILLS_DAY
    week_lo, week_hi = TARGET_FILLS_WEEK
    pos_daily = pos_w / 7.0
    gap_pos_week = max(0.0, week_lo - pos_w)
    gap_pos_day = max(0.0, day_lo - pos_daily)

    out = {
        "schema": "qiyu_positive_expectancy_frequency_v1",
        "updated_at": _now(),
        "total_fillable_weekly": round(fillable_w, 4),
        "total_fillable_daily": round(fillable_w / 7.0, 4),
        "total_forecasted_weekly": round(total_w, 4),
        "total_forecasted_daily": round(total_w / 7.0, 4),
        "calibrated_positive_E_weekly": round(pos_w, 4),
        "calibrated_positive_E_daily": round(pos_daily, 4),
        "uncalibrated_weekly": round(buckets["uncalibrated"], 4),
        "uncalibrated": round(buckets["uncalibrated"], 4),
        "near_zero_E_weekly": round(buckets["near_zero_E"], 4),
        "near_zero": round(buckets["near_zero_E"], 4),
        "negative_E_weekly": round(buckets["negative_E"], 4),
        "negative": round(buckets["negative_E"], 4),
        "positive_expectancy_frequency_gap": {
            "target_daily": list(TARGET_FILLS_DAY),
            "target_weekly": list(TARGET_FILLS_WEEK),
            "gap_daily_to_band": round(gap_pos_day, 4),
            "gap_weekly_to_band": round(gap_pos_week, 4),
            "status": "shortfall" if gap_pos_week > 0 or gap_pos_day > 0 else "in_band",
            "note": "creation must prioritize positive-E gap, not raw open-count gap",
        },
        "min_credibility": MIN_CREDIBILITY_POSITIVE_E,
        "near_zero_threshold_equity_pct": NEAR_ZERO_EQUITY_PCT,
        "min_sample_positive_E": MIN_SAMPLE_POSITIVE_E,
        "per_strategy": detail,
        "strict_rules_zh": (
            "正期望=成本后点估计>近零阈值且CI下限>0且样本≥10且非AI-only且非1笔live；"
            "LTC近零≠正期望；ADA/XRP/BTC缺样本≠正期望；NG负点估计→negative"
        ),
    }
    return out


def correlation_adjusted_frequency(per_strategy_rows, seed=20260726):
    """Portfolio frequency with family/symbol correlation + mutex conflict.

    Method: empirical bootstrap of Bernoulli daily-trigger proxies with
    within-family correlation and same-symbol mutex (max 1 fill/day/symbol).
    """
    rows = [r for r in (per_strategy_rows or []) if r.get("can_open")]
    if not rows:
        return {
            "schema": "qiyu_portfolio_frequency_v1",
            "simple_sum_weekly": 0.0,
            "simple_sum_daily": 0.0,
            "correlation_adjusted_weekly": 0.0,
            "conflict_adjusted_weekly": 0.0,
            "final_fillable_weekly": 0.0,
            "final_fillable_daily": 0.0,
            "co_signal_prob": None,
            "co_silence_prob": None,
            "mutex_frequency_loss_weekly": 0.0,
            "method": "empty",
        }

    simple_w = sum(_float(r.get("expected_weekly_fills"), 0.0) or 0.0 for r in rows)
    # Daily trigger probs from weekly / 7, clipped.
    probs = []
    meta = []
    for r in rows:
        w = max(0.0, _float(r.get("expected_weekly_fills"), 0.0) or 0.0)
        p = min(0.95, max(0.0, w / 7.0))
        probs.append(p)
        meta.append({
            "strategy_key": r.get("strategy_key"),
            "family": r.get("mechanism_family") or "other",
            "symbol": r.get("symbol"),
            "side": "short",  # live set currently shorts-dominant
            "p_daily": p,
            "weekly": w,
        })

    rng = random.Random(seed)
    n = len(probs)
    # Family correlation matrix: same family ρ=0.55, else 0.15; same symbol +0.15
    rho = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                rho[i][j] = 1.0
            else:
                base = 0.55 if meta[i]["family"] == meta[j]["family"] else 0.15
                if meta[i]["symbol"] == meta[j]["symbol"]:
                    base = min(0.85, base + 0.20)
                rho[i][j] = base

    def _gaussian_copula_bern(ps):
        # Draw correlated normals then threshold to Bernoulli with marginal p.
        zs = [rng.gauss(0.0, 1.0) for _ in range(n)]
        # One-factor approx: z_i = sqrt(ρ)*f + sqrt(1-ρ)*e_i using pairwise mean ρ
        f = rng.gauss(0.0, 1.0)
        out = []
        for i, p in enumerate(ps):
            # average off-diag rho for i
            off = [rho[i][j] for j in range(n) if j != i]
            ri = (sum(off) / float(len(off))) if off else 0.0
            ri = max(0.0, min(0.95, ri))
            zi = math.sqrt(ri) * f + math.sqrt(max(1e-9, 1.0 - ri)) * zs[i]
            # Φ approx
            t = 1.0 / (1.0 + 0.2316419 * abs(zi))
            d = 0.3989423 * math.exp(-0.5 * zi * zi)
            pphi = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))))
            cdf = 1.0 - pphi if zi > 0 else pphi
            out.append(1 if cdf <= p else 0)
        return out

    days = 400
    sum_indep = 0.0
    sum_corr_count = 0.0  # still E[sum]=sum(p); track for diagnostics
    sum_conflict = 0.0
    co_sig = 0
    co_sil = 0
    same_dir_overlap = 0
    # Bootstrap weekly samples from correlated+conflict daily draws
    weekly_conflict_samples = []
    day_buf = []
    for day_i in range(days):
        indep = [1 if rng.random() < p else 0 for p in probs]
        corr = _gaussian_copula_bern(probs)
        # Conflict model:
        # 1) same-symbol mutex (max 1)
        # 2) same-family co-fire arbitration (priority keeps highest p only)
        conflict = list(corr)
        by_sym = defaultdict(list)
        by_fam = defaultdict(list)
        for i, m in enumerate(meta):
            if conflict[i]:
                by_sym[m["symbol"]].append(i)
                by_fam[m["family"]].append(i)
        for _sym, idxs in by_sym.items():
            if len(idxs) <= 1:
                continue
            same_dir_overlap += 1
            idxs_sorted = sorted(idxs, key=lambda i: meta[i]["p_daily"], reverse=True)
            for i in idxs_sorted[1:]:
                conflict[i] = 0
        for _fam, idxs in by_fam.items():
            live = [i for i in idxs if conflict[i]]
            if len(live) <= 1:
                continue
            idxs_sorted = sorted(live, key=lambda i: meta[i]["p_daily"], reverse=True)
            for i in idxs_sorted[1:]:
                conflict[i] = 0
        sum_indep += sum(indep)
        sum_corr_count += sum(corr)
        csum = sum(conflict)
        sum_conflict += csum
        day_buf.append(csum)
        if len(day_buf) == 7:
            weekly_conflict_samples.append(sum(day_buf))
            day_buf = []
        if sum(corr) >= 2:
            co_sig += 1
        if sum(corr) == 0:
            co_sil += 1

    target_daily = simple_w / 7.0
    # Mean of sum is correlation-invariant; report simple_sum as correlation-adjusted mean,
    # and use family/symbol arbitration for conflict-adjusted / final fillable.
    # Math: for any dependence structure, E[Σ X_i] = Σ E[X_i] = Σ p_i.
    # Correlation changes Var(Σ X_i) and joint P(co-fire/co-silence), NOT the mean.
    corr_adj_daily = target_daily
    # Scale conflict sim so its independent baseline matches target, then apply ratio.
    indep_daily = sum_indep / float(days)
    conflict_daily_raw = sum_conflict / float(days)
    if indep_daily > 1e-9:
        conflict_adj_daily = target_daily * (conflict_daily_raw / indep_daily)
    else:
        conflict_adj_daily = 0.0
    conflict_adj_daily = min(target_daily, max(0.0, conflict_adj_daily))
    mutex_loss_daily = max(0.0, corr_adj_daily - conflict_adj_daily)

    def _pct(sorted_vals, q):
        if not sorted_vals:
            return None
        idx = int(round(q * (len(sorted_vals) - 1)))
        idx = max(0, min(len(sorted_vals) - 1, idx))
        return sorted_vals[idx]

    weekly_sorted = sorted(weekly_conflict_samples)
    # Scale bootstrap weekly samples to match conflict-adjusted mean level
    raw_mean_w = (
        sum(weekly_sorted) / float(len(weekly_sorted)) if weekly_sorted else 0.0)
    scale = (
        (conflict_adj_daily * 7.0) / raw_mean_w
        if raw_mean_w > 1e-12 else 1.0
    )
    weekly_scaled = [x * scale for x in weekly_sorted]
    weekly_scaled_sorted = sorted(weekly_scaled)
    med7 = _pct(weekly_scaled_sorted, 0.50)
    # P(3-day no fill) from daily conflict rate (Poisson/Bernoulli approx)
    p_day = conflict_adj_daily  # expected fills/day after conflict
    # treat as Poisson rate λ=p_day for portfolio fills
    p_3day_no = math.exp(-max(0.0, p_day) * 3.0)
    week_lo_target = TARGET_FILLS_WEEK[0]
    p_7day_below = (
        float(sum(1 for x in weekly_scaled if x < week_lo_target))
        / float(len(weekly_scaled))
        if weekly_scaled else None
    )

    return {
        "schema": "qiyu_portfolio_frequency_v1",
        "method": "mean_invariant_corr_plus_family_symbol_arbitration",
        "simple_sum_weekly": round(simple_w, 4),
        "simple_sum_daily": round(target_daily, 4),
        "correlation_adjusted_weekly": round(corr_adj_daily * 7.0, 4),
        "correlation_adjusted_daily": round(corr_adj_daily, 4),
        "conflict_adjusted_weekly": round(conflict_adj_daily * 7.0, 4),
        "conflict_adjusted_daily": round(conflict_adj_daily, 4),
        "final_fillable_weekly": round(conflict_adj_daily * 7.0, 4),
        "final_fillable_daily": round(conflict_adj_daily, 4),
        "mutex_frequency_loss_weekly": round(mutex_loss_daily * 7.0, 4),
        "co_signal_prob": round(co_sig / float(days), 4),
        "co_silence_prob": round(co_sil / float(days), 4),
        "same_direction_overlap_day_rate": round(same_dir_overlap / float(days), 4),
        "family_correlation_assumed": 0.55,
        "cross_family_correlation_assumed": 0.15,
        "bootstrap": {
            "n_weekly_samples": len(weekly_scaled),
            "median_7d": None if med7 is None else round(med7, 4),
            "interval_50pct": [
                None if not weekly_scaled_sorted else round(_pct(weekly_scaled_sorted, 0.25), 4),
                None if not weekly_scaled_sorted else round(_pct(weekly_scaled_sorted, 0.75), 4),
            ],
            "interval_80pct": [
                None if not weekly_scaled_sorted else round(_pct(weekly_scaled_sorted, 0.10), 4),
                None if not weekly_scaled_sorted else round(_pct(weekly_scaled_sorted, 0.90), 4),
            ],
            "interval_95pct": [
                None if not weekly_scaled_sorted else round(_pct(weekly_scaled_sorted, 0.025), 4),
                None if not weekly_scaled_sorted else round(_pct(weekly_scaled_sorted, 0.975), 4),
            ],
            "p_3day_no_fill": round(p_3day_no, 6),
            "p_7day_below_target": (
                None if p_7day_below is None else round(p_7day_below, 4)),
            "target_weekly_lo": week_lo_target,
        },
        "math_reason_corr_mean_invariant": (
            "E[sum_i X_i] = sum_i E[X_i] for any dependence; correlation changes "
            "distribution / co-fire probability, not the mean. Conflict adjustment "
            "THEN reduces mean via same-symbol mutex + same-family arbitration."
        ),
        "strategies": meta,
        "note": (
            "E[sum] invariant to correlation; final_fillable applies same-symbol "
            "mutex + same-family co-fire arbitration (priority keeps one)."
        ),
    }


def mark_forecast_stale(reason, detail=None):
    """Mark latest forecast stale and record pool version mismatch."""
    try:
        import auto_trade_strategy_events as sev
        pool = sev.strategy_pool_version()
    except Exception:
        pool = {"strategy_pool_version": "unknown", "mounted_count": None}
    flag = {
        "stale": True,
        "reason": str(reason or "pool_change"),
        "detail": detail or {},
        "marked_at": _now(),
        "strategy_pool_version_now": pool.get("strategy_pool_version"),
        "mounted_count_now": pool.get("mounted_count"),
    }
    latest = _read(LATEST_PATH, {})
    if latest:
        latest["stale"] = True
        latest["stale_reason"] = flag["reason"]
        latest["stale_marked_at"] = flag["marked_at"]
        latest["strategy_pool_version_now"] = flag["strategy_pool_version_now"]
        # keep prior generated_at; UI must not treat as current
        latest["is_current"] = False
        _atomic(LATEST_PATH, latest)
    _atomic(STALE_FLAG_PATH, flag)
    try:
        import auto_trade_strategy_events as sev
        sev.emit(
            event_type="param_version_changed",
            strategy_id="",
            reason_code=str(reason or "pool_change"),
            source_data_version=flag["strategy_pool_version_now"],
            extra=detail or {},
        )
    except Exception:
        pass
    return flag


def clear_stale_on_report(report):
    try:
        import auto_trade_strategy_events as sev
        pool = sev.strategy_pool_version()
    except Exception:
        pool = {"strategy_pool_version": "unknown", "mounted_count": None}
    report = dict(report or {})
    report["stale"] = False
    report["is_current"] = True
    report["strategy_pool_version"] = pool.get("strategy_pool_version")
    report["strategy_pool_mounted_count"] = pool.get("mounted_count")
    report["stale_cleared_at"] = _now()
    _atomic(STALE_FLAG_PATH, {
        "stale": False,
        "cleared_at": _now(),
        "strategy_pool_version": pool.get("strategy_pool_version"),
    })
    return report


def load_latest_for_ui():
    """UI-safe latest: never present stale snapshot as current without banner.

    Do not recompute occupancy combo on this path. Combo is written into the
    snapshot by run_lightweight_statistical_refresh / apply_closeout_layers.
    Recomputing here blocks GET /api/forecast/latest and the 控制台 panel
    times out or sits on 暂无.
    """
    report = _read(LATEST_PATH, {})
    flag = _read(STALE_FLAG_PATH, {})
    try:
        import auto_trade_strategy_events as sev
        pool = sev.strategy_pool_version()
    except Exception:
        pool = {}
    pool_ver = pool.get("strategy_pool_version")
    report_ver = report.get("strategy_pool_version")
    mounted_now = pool.get("mounted_count")
    report_mounted = report.get("strategy_pool_mounted_count")
    if report_mounted is None:
        report_mounted = report.get("active_strategy_count")
    stale = bool(flag.get("stale") or report.get("stale"))
    if report_ver and pool_ver and report_ver != pool_ver:
        stale = True
        report["stale"] = True
        report["stale_reason"] = report.get("stale_reason") or "strategy_pool_version_mismatch"
    if (report_mounted is not None
            and mounted_now is not None
            and int(report_mounted or 0) != int(mounted_now or 0)):
        stale = True
        report["stale"] = True
        report["stale_reason"] = "mounted_count_mismatch"
    report["is_current"] = not stale
    report["strategy_pool_version_now"] = pool_ver
    report["strategy_pool_mounted_count_now"] = mounted_now
    report["ui_stale_banner_zh"] = (
        "组合数据已过期（挂载池已变更），下列频率数字不可用；请点「重算并刷新」。"
        if stale else None
    )
    combo = report.get("portfolio_combo")
    if not isinstance(combo, dict):
        report["portfolio_combo"] = {"missing": True, "reason": "combo_not_in_snapshot"}
    if stale:
        # Do not keep selling old pool's frequency as current.
        overall = dict(report.get("overall") or {})
        overall["daily_opens_expected"] = None
        overall["weekly_opens_expected"] = None
        overall["stale_suppressed"] = True
        report["overall"] = overall
        try:
            schedule_lightweight_refresh(
                reason=report.get("stale_reason") or "pool_change",
                detail={"mounted_now": mounted_now, "report_mounted": report_mounted},
            )
        except Exception:
            pass
    report["lightweight_refresh_running"] = lightweight_refresh_in_flight()
    return report


def schedule_lightweight_refresh(reason="pool_change", detail=None):
    """Background statistical refresh (frequency + combo); never blocks caller."""
    global _SCHEDULE_REFRESH_PENDING
    with _SCHEDULE_REFRESH_LOCK:
        if _SCHEDULE_REFRESH_PENDING or _refresh_lock.locked():
            return {
                "ok": True,
                "skipped": True,
                "reason": "refresh_already_scheduled",
                "detail": detail or {},
            }
        _SCHEDULE_REFRESH_PENDING = True

    def _worker():
        global _SCHEDULE_REFRESH_PENDING
        try:
            run_lightweight_statistical_refresh(push_wx=False)
        except Exception:
            pass
        finally:
            with _SCHEDULE_REFRESH_LOCK:
                _SCHEDULE_REFRESH_PENDING = False

    threading.Thread(
        target=_worker,
        name="forecast-lightweight-refresh",
        daemon=True,
    ).start()
    return {"ok": True, "scheduled": True, "reason": reason, "detail": detail or {}}


def run_lightweight_statistical_refresh(push_wx=False):
    """Refresh statistical forecast numbers without waiting on 3AI explain."""
    if not _refresh_lock.acquire(False):
        return {"ok": False, "error": "refresh_already_running"}
    _patch_state(
        lightweight_refresh_running=True,
        lightweight_refresh_started_at=_now(),
        lightweight_refresh_error=None,
        last_mode="lightweight_statistical",
    )
    try:
        import auto_trade_system_forecast as forecast
        import auto_trade_expectancy_metrics as exp
        snapshot = forecast.collect_forecast_snapshot()
        # Minimal AI bundle: empty explain
        ai_bundle = {
            "ok": False,
            "skipped": True,
            "reason": "lightweight_statistical_refresh",
            "deepseek": {}, "qwen": {}, "glm": {},
            "ai_status": {
                "deepseek_ok": False, "qwen_ok": False, "chatgpt_ok": False,
                "all_ok": False, "role": "explain_only_deferred",
            },
        }
        report = forecast.build_report(snapshot, ai_bundle)
        report = apply_closeout_layers(report)
        report = clear_stale_on_report(report)
        report["refresh_mode"] = "lightweight_statistical"
        report["ai_explain_deferred"] = True
        report["snapshot"] = {
            "active_strategy_count": snapshot.get("active_strategy_count"),
            "can_open_strategy_count": snapshot.get("can_open_strategy_count"),
            "paused_strategy_count": snapshot.get("paused_strategy_count"),
            "strategies": [
                {
                    "strategy_key": row.get("strategy_key"),
                    "symbol": row.get("symbol"),
                    "timeframe": row.get("timeframe"),
                    "grade": row.get("grade"),
                    "can_open": row.get("can_open"),
                    "pause_new_entries": row.get("pause_new_entries"),
                }
                for row in (snapshot.get("strategies") or [])
            ],
        }
        # Publish the coherent current strategy-pool snapshot before optional
        # expectancy/calibration enrichment.  A large metrics refresh must not
        # leave the UI presenting the previous mounted count as current.
        _atomic(LATEST_PATH, report)
        try:
            snap_path = exp.persist_forecast_snapshot(report, forever=True)
            report["snapshot_persist_path"] = snap_path
            calib = exp.update_weekly_calibration(report)
            calib = enrich_calibration(calib)
            report["calibration"] = {
                "updated_at": calib.get("updated_at"),
                "rows": (calib.get("rows") or [])[-12:],
                "period_metrics": calib.get("period_metrics"),
                "calibration_stage": calib.get("calibration_stage"),
            }
            exp.compute_all_live_metrics()
            # Metrics rebuild writes bare v1 gap/creation files — re-stamp v2 peg.
            persist_strategy_creation_frequency_input(report)
        except Exception as exc:
            report["persist_error"] = str(exc)
            try:
                persist_strategy_creation_frequency_input(report)
            except Exception:
                pass
        _atomic(LATEST_PATH, report)
        try:
            forecast._append_history(report)
        except Exception:
            pass
        _atomic(STATE_PATH, {
            "last_run_at": _now(),
            "last_mode": "lightweight_statistical",
            "last_weekly_opens_expected": (report.get("overall") or {}).get(
                "weekly_opens_expected"),
            "strategy_pool_version": report.get("strategy_pool_version"),
            "lightweight_refresh_running": False,
            "lightweight_refresh_finished_at": _now(),
        })
        return {"ok": True, "report": report, "wx": None}
    except Exception as exc:
        try:
            _patch_state(
                lightweight_refresh_running=False,
                lightweight_refresh_error=str(exc),
                lightweight_refresh_finished_at=_now(),
            )
        except Exception:
            pass
        raise
    finally:
        try:
            st = _read(STATE_PATH, {}) or {}
            if st.get("lightweight_refresh_running"):
                st["lightweight_refresh_running"] = False
                st["lightweight_refresh_finished_at"] = _now()
                _atomic(STATE_PATH, st)
        except Exception:
            pass
        _refresh_lock.release()


def notify_pool_change(reason, detail=None, auto_refresh=True):
    """Control-plane hook: stale mark + optional lightweight refresh."""
    flag = mark_forecast_stale(reason, detail=detail)
    out = {"stale": flag, "refresh": None}
    if auto_refresh:
        try:
            out["refresh"] = schedule_lightweight_refresh(reason=reason, detail=detail)
        except Exception as exc:
            out["refresh_error"] = str(exc)
    return out


def apply_closeout_layers(report):
    """Attach correlation, positive-E, calibration stage onto a forecast report."""
    report = dict(report or {})
    per = list(report.get("per_strategy") or [])
    # Ensure credibility / clearly_positive fields + source_layer
    try:
        import auto_trade_strategy_events as sev
    except Exception:
        sev = None
    for row in per:
        exp = row.get("expectancy") or {}
        if row.get("credibility") is None:
            row["credibility"] = exp.get("credibility")
        net = row.get("net_expectancy_equity_pct")
        if isinstance(net, dict):
            net = net.get("value")
            row["net_expectancy_equity_pct"] = net
        sample = row.get("sample") or exp.get("sample") or {}
        if not row.get("sample"):
            row["sample"] = sample
        ci = row.get("net_equity_ci_80pct") or exp.get("net_equity_ci_80pct")
        ci_floor = None
        if isinstance(ci, (list, tuple)) and ci:
            ci_floor = ci[0]
        sample_n = int(sample.get("combined_n") or row.get("sample_n") or 0)
        live_n = int(sample.get("live_n") or 0)
        bt_n = int(sample.get("backtest_n") or 0)
        cred = float(row.get("credibility") or 0)
        if row.get("clearly_positive_after_cost") is None:
            row["clearly_positive_after_cost"] = bool(
                net is not None and float(net) > NEAR_ZERO_EQUITY_PCT
                and cred >= MIN_CREDIBILITY_POSITIVE_E
                and sample_n >= MIN_SAMPLE_POSITIVE_E
                and (ci_floor is None or float(ci_floor) > 0)
            )
        # Source layer (§12)
        funnel = row.get("funnel_7d") or {}
        prior_method = row.get("frequency_method") or (
            (row.get("frequency") or {}).get("method"))
        if sev is not None:
            layer = sev.classify_frequency_source_layer(
                row.get("strategy_key") or "",
                event_funnel=funnel if funnel.get("source") == "strategy_events.jsonl" else None,
                legacy_funnel=funnel if funnel.get("legacy_fallback") else None,
                prior_method=prior_method,
            )
            row["source_layer"] = layer.get("source_layer")
            row["frequency_source_primary"] = layer.get("primary")
            row["insufficient_source"] = layer.get("insufficient_source")
            row["event_coverage"] = {
                "sufficient_7d_event_coverage": (
                    (layer.get("coverage") or {}).get("sufficient_7d_event_coverage")),
                "event_n": (layer.get("coverage") or {}).get("event_n"),
                "span_days": (layer.get("coverage") or {}).get("span_days"),
                "core_stages_seen": (layer.get("coverage") or {}).get("core_stages_seen"),
            }
        else:
            row["source_layer"] = "uncalibrated"
            row["insufficient_source"] = True

    portfolio = correlation_adjusted_frequency(per)
    pos = build_positive_expectancy_frequency(
        per, total_fillable_weekly=portfolio.get("final_fillable_weekly"))
    calib = enrich_calibration(_read(CALIBRATION_PATH, {"rows": []}))

    # Prefer fillable (conflict-adjusted) as headline frequency numbers
    overall = dict(report.get("overall") or {})
    overall["simple_sum_weekly_opens_expected"] = portfolio.get("simple_sum_weekly")
    overall["correlation_adjusted_weekly"] = portfolio.get("correlation_adjusted_weekly")
    overall["conflict_adjusted_weekly"] = portfolio.get("conflict_adjusted_weekly")
    overall["weekly_opens_expected"] = portfolio.get("final_fillable_weekly")
    overall["daily_opens_expected"] = portfolio.get("final_fillable_daily")
    boot = portfolio.get("bootstrap") or {}
    if boot.get("interval_80pct"):
        overall["weekly_opens_range"] = boot.get("interval_80pct")
    overall["frequency_method"] = "statistical_baseline_regime_correlation_mutex"
    overall["ai_role"] = "explain_only"
    report["overall"] = overall
    report["portfolio_frequency"] = portfolio
    report["positive_expectancy_frequency"] = pos

    # Rebuild gap vs fillable + positive-E gap
    fill_w = portfolio.get("final_fillable_weekly") or 0.0
    fill_d = portfolio.get("final_fillable_daily") or 0.0
    day_lo, day_hi = TARGET_FILLS_DAY
    week_lo, week_hi = TARGET_FILLS_WEEK
    gap_daily = round(day_lo - fill_d, 4) if fill_d < day_lo else (
        round(day_hi - fill_d, 4) if fill_d > day_hi else 0.0)
    gap_weekly = round(week_lo - fill_w, 4) if fill_w < week_lo else (
        round(week_hi - fill_w, 4) if fill_w > week_hi else 0.0)
    report["frequency_gap"] = {
        "expected_daily_fills": round(fill_d, 4),
        "expected_weekly_fills": round(fill_w, 4),
        "target_daily": list(TARGET_FILLS_DAY),
        "target_weekly": list(TARGET_FILLS_WEEK),
        "gap_daily_to_band": gap_daily,
        "gap_weekly_to_band": gap_weekly,
        "status": (
            "shortfall" if gap_daily > 0 or gap_weekly > 0
            else ("surplus" if gap_daily < 0 or gap_weekly < 0 else "in_band")
        ),
        "basis": "final_fillable_after_correlation_and_mutex",
    }
    report["positive_expectancy_frequency_gap"] = pos.get(
        "positive_expectancy_frequency_gap")
    report["calibration_stage"] = calib.get("calibration_stage")
    report["calibration_period_metrics"] = calib.get("period_metrics")
    if report.get("calibration_stage", {}).get("ui_must_show_calibration_banner"):
        report["calibration_banner_zh"] = report["calibration_stage"]["banner_zh"]

    try:
        import auto_trade_portfolio_combo_metrics as pcm
        report = pcm.attach_to_report(report)
    except Exception as exc:
        report["portfolio_combo_error"] = str(exc)

    # Creation brief prioritizes positive-E gap
    brief = dict(report.get("creation_brief") or {})
    peg = pos.get("positive_expectancy_frequency_gap") or {}
    brief["prioritize_positive_expectancy_gap"] = True
    brief["positive_expectancy_gap_weekly"] = peg.get("gap_weekly_to_band")
    brief["positive_expectancy_gap_daily"] = peg.get("gap_daily_to_band")
    brief["do_not_loosen_entries"] = True
    brief["do_not_force_open"] = True
    brief["summary_zh"] = (
        "组合成交(冲突修正后)日均%.3f、周%.3f；已校准正期望周频率%.3f。"
        "策略创造应优先填补正期望频率缺口(周+%.3f)，禁止放宽入场或强制开仓。"
        % (fill_d, fill_w, pos.get("calibrated_positive_E_weekly") or 0.0,
           peg.get("gap_weekly_to_band") or 0.0)
    )
    report["creation_brief"] = brief

    try:
        import auto_trade_strategy_events as sev
        pool = sev.strategy_pool_version()
        report["strategy_pool_version"] = pool.get("strategy_pool_version")
        report["strategy_pool_mounted_count"] = pool.get("mounted_count")
        report["event_coverage_matrix_summary"] = (
            sev.build_event_coverage_matrix(
                [r.get("strategy_key") for r in per if r.get("strategy_key")]
            ).get("summary")
        )
    except Exception:
        pass

    # Ensure forecast_id present for creation handoff traceability
    if not report.get("forecast_id"):
        stamp = str(report.get("generated_at") or _now()).replace(" ", "_").replace(":", "")
        pool_ver = str(report.get("strategy_pool_version") or "nopool")[:12]
        report["forecast_id"] = "fc_%s_%s" % (stamp, pool_ver)

    # Persist AFTER pool/forecast ids are attached (factory reads this file)
    persist_strategy_creation_frequency_input(report)
    report["per_strategy"] = per
    return report


def build_strategy_creation_frequency_input(report):
    """v2 creation input: total gap + positive-E gap (primary) + bucket context."""
    report = report or {}
    pos = dict(report.get("positive_expectancy_frequency") or {})
    peg = (report.get("positive_expectancy_frequency_gap")
           or pos.get("positive_expectancy_frequency_gap") or {})
    brief = dict(report.get("creation_brief") or {})
    brief.setdefault("prioritize_positive_expectancy_gap", True)
    brief.setdefault("do_not_loosen_entries", True)
    brief.setdefault("do_not_force_open", True)
    if peg:
        brief.setdefault("positive_expectancy_gap_weekly", peg.get("gap_weekly_to_band"))
        brief.setdefault("positive_expectancy_gap_daily", peg.get("gap_daily_to_band"))
    # Prefer positive-E weekly gap as the creation target when present
    if peg.get("gap_weekly_to_band") is not None:
        brief["target_incremental_weekly_fills"] = max(
            0.0, float(peg.get("gap_weekly_to_band") or 0.0))
    return {
        "schema": "qiyu_strategy_creation_frequency_input_v2",
        "updated_at": _now(),
        "generated_at": report.get("generated_at") or _now(),
        "forecast_id": report.get("forecast_id"),
        "strategy_pool_version": report.get("strategy_pool_version"),
        "frequency_gap": report.get("frequency_gap"),
        "positive_expectancy_frequency": pos,
        "positive_expectancy_frequency_gap": peg,
        "uncalibrated_contribution": pos.get("uncalibrated_weekly", pos.get("uncalibrated")),
        "near_zero_contribution": pos.get("near_zero_E_weekly", pos.get("near_zero")),
        "negative_contribution": pos.get("negative_E_weekly", pos.get("negative")),
        "portfolio_frequency": report.get("portfolio_frequency"),
        "creation_brief": brief,
        "priority": "positive_expectancy_frequency_gap",
        "mechanism_families": report.get("mechanism_families"),
        "mandatory_niches_hint": [
            fam if isinstance(fam, dict) else {
                "family": fam,
                "thesis": "补齐正期望频率缺口；避开已饱和同质仓位",
                "timeframe_pref": ["5m", "15m"],
            }
            for fam in list(
                (report.get("creation_brief") or {}).get("prefer_new_families") or []
            )[:3]
            if fam
        ],
    }


def persist_strategy_creation_frequency_input(report):
    """Atomic write of v2 creation input + peg enrichment on frequency_gap_report.

    Must be called after closeout enrichment and again after any expectancy
    path that would otherwise rewrite these files as bare v1.
    """
    creation_input = build_strategy_creation_frequency_input(report)
    # Preserve family niche hints written by expectancy (object form preferred)
    try:
        prev = _read(CREATION_INPUT_PATH, {})
        prev_hints = prev.get("mandatory_niches_hint") or []
        new_hints = creation_input.get("mandatory_niches_hint") or []
        if prev_hints and (
                not new_hints
                or (isinstance(prev_hints[0], dict)
                    and not (new_hints and isinstance(new_hints[0], dict)))):
            creation_input["mandatory_niches_hint"] = prev_hints
        if prev.get("mechanism_families") and not creation_input.get(
                "mechanism_families"):
            creation_input["mechanism_families"] = prev.get("mechanism_families")
    except Exception:
        pass
    _atomic(CREATION_INPUT_PATH, creation_input)

    gap_doc = _read(GAP_REPORT_PATH, {})
    if not isinstance(gap_doc, dict):
        gap_doc = {}
    gap_doc["schema"] = gap_doc.get("schema") or "qiyu_frequency_gap_v1"
    if report.get("frequency_gap"):
        gap_doc["pool"] = report.get("frequency_gap")
    gap_doc["positive_expectancy_frequency"] = creation_input.get(
        "positive_expectancy_frequency")
    gap_doc["positive_expectancy_frequency_gap"] = creation_input.get(
        "positive_expectancy_frequency_gap")
    gap_doc["uncalibrated_contribution"] = creation_input.get(
        "uncalibrated_contribution")
    gap_doc["near_zero_contribution"] = creation_input.get("near_zero_contribution")
    gap_doc["negative_contribution"] = creation_input.get("negative_contribution")
    gap_doc["portfolio_frequency"] = creation_input.get("portfolio_frequency")
    gap_doc["creation_brief"] = creation_input.get("creation_brief")
    gap_doc["priority"] = "positive_expectancy_frequency_gap"
    gap_doc["strategy_pool_version"] = creation_input.get("strategy_pool_version")
    gap_doc["forecast_id"] = creation_input.get("forecast_id")
    gap_doc["generated_at"] = creation_input.get("generated_at")
    gap_doc["updated_at"] = _now()
    if creation_input.get("mechanism_families") and not gap_doc.get(
            "mechanism_families"):
        gap_doc["mechanism_families"] = creation_input.get("mechanism_families")
    _atomic(GAP_REPORT_PATH, gap_doc)
    return creation_input


def detailed_expectancy_ledger(symbol, timeframe, strategy_key, ai_wr_pct=None,
                               position_ratio=0.30, leverage=20.0):
    """Full cost ledger with ≥4 decimal places + sign label."""
    import auto_trade_expectancy_metrics as exp
    block = exp.build_expectancy_block(
        symbol, timeframe, strategy_key,
        position_ratio=position_ratio, leverage=leverage, ai_wr_pct=ai_wr_pct)
    base = exp._margin_rois_from_baseline(None, symbol, timeframe, strategy_key)
    live = exp._live_margin_rois(symbol, timeframe, strategy_key)
    combined = list(base) + list(live)
    wins = [r["margin_roi"] for r in combined if r["margin_roi"] > 0]
    losses = [r["margin_roi"] for r in combined if r["margin_roi"] <= 0]
    avg_win = (sum(wins) / float(len(wins))) if wins else None
    avg_loss = (sum(losses) / float(len(losses))) if losses else None
    payoff = None
    if avg_win is not None and avg_loss not in (None, 0):
        payoff = abs(avg_win / avg_loss)
    gross_wr = (len(wins) / float(len(combined))) if combined else None
    cost = block.get("cost") or {}
    net_eq = (block.get("net_expectancy_equity_pct") or {}).get("value")
    net_m = (block.get("net_expectancy_margin_pct") or {}).get("value")
    net_p = (block.get("net_expectancy_price_pct") or {}).get("value")
    gross_p = (block.get("gross_expectancy_price_pct") or {}).get("value")
    freq = exp.statistical_frequency_forecast(symbol, timeframe, strategy_key, can_open=True)
    weekly = freq.get("expected_weekly_fills")
    monthly_fills = None if weekly is None else float(weekly) * (30.0 / 7.0)
    monthly_eq = None if (net_eq is None or monthly_fills is None) else float(net_eq) * monthly_fills

    rng = random.Random(20260726 + len(strategy_key or ""))
    ci = None
    if combined:
        samples = []
        cost_p = float(cost.get("cost_price_rate") or 0.0)
        for _ in range(500):
            pick = [combined[rng.randrange(len(combined))]["margin_roi"]
                    for _i in range(len(combined))]
            mean_m = sum(pick) / float(len(pick))
            net_price = (mean_m / float(leverage)) - cost_p
            samples.append(net_price * float(position_ratio) * float(leverage) * 100.0)
        samples.sort()
        ci = [round(samples[int(0.1 * (len(samples) - 1))], 4),
              round(samples[int(0.9 * (len(samples) - 1))], 4)]

    if net_eq is None:
        label = "missing_sample"
    elif ci and ci[0] <= 0 <= ci[1]:
        label = "statistically_indistinguishable_from_zero"
    elif abs(float(net_eq)) < 0.01:
        label = "near_zero_magnitude"
    elif float(net_eq) > 0:
        label = "positive"
    else:
        label = "negative"

    clearly_pos = bool(
        label == "positive"
        and (block.get("credibility") or 0) >= MIN_CREDIBILITY_POSITIVE_E
        and net_eq is not None and float(net_eq) > NEAR_ZERO_EQUITY_PCT
        and (ci is None or ci[0] > 0)
    )

    return {
        "schema": "qiyu_expectancy_ledger_v1",
        "strategy_key": strategy_key,
        "symbol": symbol,
        "timeframe": timeframe,
        "sample": {
            "backtest_n": len(base),
            "live_n": len(live),
            "combined_n": len(combined),
        },
        "gross_win_rate": None if gross_wr is None else round(gross_wr, 6),
        "calibrated_win_rate_pct": (
            block.get("calibrated_expected_win_rate_pct") or {}).get("value"),
        "ai_theoretical_wr_pct": ai_wr_pct,
        "backtest_win_rate_pct": (block.get("backtest_win_rate_pct") or {}).get("value"),
        "live_win_rate_pct": (block.get("live_win_rate_pct") or {}).get("value"),
        "avg_gross_win_margin": None if avg_win is None else round(avg_win, 8),
        "avg_gross_loss_margin": None if avg_loss is None else round(avg_loss, 8),
        "payoff_ratio": None if payoff is None else round(payoff, 6),
        "gross_expectancy_price_pct": None if gross_p is None else round(float(gross_p), 6),
        "entry_fee_rate": cost.get("fee_rate_per_side"),
        "exit_fee_rate": cost.get("fee_rate_per_side"),
        "entry_slip_rate": cost.get("slippage_rate_per_side"),
        "exit_slip_rate": cost.get("slippage_rate_per_side"),
        "funding_component": cost.get("funding_component"),
        "partial_fill_cost_rate": cost.get("impact_rate_per_side"),
        "half_spread_rate_per_side": cost.get("half_spread_rate_per_side"),
        "latency_rate_per_side": cost.get("latency_rate_per_side"),
        "cost_price_pct": None if cost.get("cost_price_pct") is None else round(
            float(cost.get("cost_price_pct")), 6),
        "net_expectancy_price_pct": None if net_p is None else round(float(net_p), 6),
        "net_expectancy_margin_pct": None if net_m is None else round(float(net_m), 6),
        "net_expectancy_equity_pct": None if net_eq is None else round(float(net_eq), 6),
        "expected_weekly_fills": weekly,
        "expected_monthly_fills": None if monthly_fills is None else round(monthly_fills, 4),
        "expected_monthly_net_equity_pct": (
            None if monthly_eq is None else round(monthly_eq, 6)),
        "net_equity_ci_80pct": ci,
        "credibility": block.get("credibility"),
        "expectancy_label": label,
        "clearly_positive_after_cost": clearly_pos,
        "effective_core_strategy_allowed": clearly_pos,
    }
