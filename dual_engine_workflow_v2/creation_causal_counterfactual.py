# -*- coding: utf-8 -*-
"""CausalImpact-style counterfactual hardness for creation hypotheses.

After meta-think emits a hypothesis, strip likely confounder regimes and
re-test whether the signal effect survives. If the edge disappears once the
alleged cause is removed, reject — do not enter factor mining.

No causalimpact package required (VPS-safe lite).
"""
from __future__ import print_function

import math
from datetime import datetime

from . import creation_alphalens_lite as al


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def probe():
    backends = {"causalimpact": False}
    try:
        __import__("causalimpact")
        backends["causalimpact"] = True
    except Exception:
        pass
    return {
        "ok": True,
        "provider": "causal_counterfactual_lite_v1",
        "backends": backends,
        "note_zh": "反事实：剔除高波/高动量/高range 时段后重测效应是否仍在。",
        "at": _now(),
    }


def _quantile_mask(series, lo_q=0.0, hi_q=1.0):
    vals = [float(v) for v in series if v is not None]
    if len(vals) < 30:
        return [True] * len(series)
    ordered = sorted(vals)
    n = len(ordered)
    lo = ordered[max(0, int(math.floor(n * lo_q)))]
    hi = ordered[min(n - 1, int(math.ceil(n * hi_q)) - 1)]
    out = []
    for v in series:
        if v is None:
            out.append(False)
            continue
        fv = float(v)
        out.append(lo <= fv <= hi)
    return out


def _masked_series(series, keep_mask):
    return [v if keep_mask[i] else None for i, v in enumerate(series)]


def _effect_pack(factor_values, fwd_returns):
    return al.causal_pre_post(factor_values, fwd_returns)


def run_counterfactual_battery(factor_matrix, fwd_returns, core_factors=None,
                               confounders=None):
    """For each core factor, re-test after removing confounder extremes.

    Default confounders (crypto proxies):
      - atr_pct_14 / range_pct high-vol regime (top 20%)
      - abs_ret_1 / ret_12 momentum burst (top 20%)
    Pass if baseline causal significant AND at least one counterfactual
    still keeps effect direction with |t|>=1.64 (relaxed) OR IC persists.
    Fail if ALL counterfactuals wipe the effect → correlation illusion.
    """
    core_factors = list(core_factors or []) or list((factor_matrix or {}).keys())[:3]
    confounders = list(confounders or [])
    if not confounders:
        for name in ("atr_pct_14", "range_pct", "abs_ret_1", "ret_12"):
            if name in (factor_matrix or {}):
                confounders.append(name)

    rows = []
    any_hard_pass = False
    for fname in core_factors:
        series = (factor_matrix or {}).get(fname) or []
        baseline = _effect_pack(series, fwd_returns)
        cf_rows = []
        survived = 0
        wiped = 0
        for cname in confounders:
            if cname == fname:
                continue
            cseries = (factor_matrix or {}).get(cname) or []
            # keep bottom 80% of confounder (remove top-20% "active cause" regime)
            keep = _quantile_mask(cseries, lo_q=0.0, hi_q=0.80)
            f_masked = _masked_series(series, keep)
            r_masked = _masked_series(fwd_returns, keep)
            pack = _effect_pack(f_masked, r_masked)
            # also IC on masked
            ic = al.rolling_ic(f_masked, r_masked)
            still = False
            if pack.get("ok") and pack.get("effect") is not None:
                # survive if same-sign effect and |t|>=1.64
                base_eff = baseline.get("effect")
                if base_eff is None:
                    still = bool(pack.get("significant"))
                else:
                    same_sign = (float(pack["effect"]) * float(base_eff)) > 0
                    still = same_sign and abs(float(pack.get("t_stat") or 0)) >= 1.64
            if not still and ic.get("ok") and abs(float(ic.get("ic_mean") or 0)) >= 0.02:
                still = True
            if still:
                survived += 1
            else:
                wiped += 1
            cf_rows.append({
                "confounder": cname,
                "removed_regime": "top_20pct",
                "causal": pack,
                "ic": {"ic_mean": ic.get("ic_mean"), "ir": ic.get("ir"), "ok": ic.get("ok")},
                "survived": still,
            })

        # Hard rule: baseline must be meaningful AND not fully wiped
        baseline_ok = bool(baseline.get("significant")) or (
            baseline.get("ok") and abs(float(baseline.get("t_stat") or 0)) >= 1.96
            and float(baseline.get("effect") or 0) > 0
        )
        # If no confounders available, require baseline significant alone
        if not cf_rows:
            hard_pass = baseline_ok
            reason = "baseline_only_no_confounders"
        else:
            hard_pass = baseline_ok and survived >= 1 and wiped < len(cf_rows)
            reason = None if hard_pass else (
                "counterfactual_wiped" if survived == 0 else "baseline_not_causal"
            )
            if baseline_ok and survived == 0:
                reason = "counterfactual_wiped_all"
            elif not baseline_ok:
                reason = "baseline_not_causal"

        if hard_pass:
            any_hard_pass = True
        rows.append({
            "factor": fname,
            "baseline": baseline,
            "counterfactuals": cf_rows,
            "survived": survived,
            "wiped": wiped,
            "passed": hard_pass,
            "reason": reason,
        })

    return {
        "ok": True,
        "schema": "qiyu_causal_counterfactual_v1",
        "passed": any_hard_pass,
        "factors": rows,
        "probe": probe(),
        "at": _now(),
        "note_zh": (
            "反事实硬度：剔除疑似原因活跃时段后信号仍需存活；"
            "全部被洗掉则视为相关性错觉，打回元思考，不许挖因子。"
        ),
        "human_banner_zh": (
            None if any_hard_pass else
            "【因果反事实未通过】假设在剔除混杂/疑似原因时段后效应消失，禁止进入因子挖掘。"
        ),
    }
