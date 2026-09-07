# -*- coding: utf-8 -*-
"""创造小样本严谨层（Phase A）：反凑 n / 禁 waive / 软收缩 / 叶预算软 / 统计默认观测。

分档（由环境变量控制，禁止两侧同日默认 hard 统计项）：
  H = hard veto（写入 failed_rules，ok=False）
  S = soft（warnings + 回灌收缩分，不单独否决）
  O = observe（仅 evidence）

环境（Phase B 分侧 drop-in；Phase A 默认偏安全）：
  CREATE_ANTI_EVASION=1          禁 waive、禁凑 n 提示（默认 1）
  CREATE_N_DISCOUNT=1            回灌 n/(n+κ)（默认 1）
  CREATE_N_DISCOUNT_KAPPA=50
  CREATE_TIMING_BUDGET=soft|hard|off   默认 soft
  CREATE_PLACEBO=off|observe|soft|hard 默认 observe（Phase A）
  CREATE_LOO=off|observe|soft|hard     默认 observe
  CREATE_MC_SUBSET=off|observe|soft|hard 默认 observe
  CREATE_NOISE_STRESS=off|observe|soft|hard 默认 off
  CREATE_SMALL_N_SEED=20260907
"""
from __future__ import print_function

import math
import os
import random

# ---- Phase 0 lexicon (H/S/O) ----
TIER_HARD = "hard"
TIER_SOFT = "soft"
TIER_OBSERVE = "observe"
TIER_OFF = "off"

DEFAULT_KAPPA = 50.0
PLACEBO_DRAWS = 1000
PLACEBO_TOP_FRAC = 0.05
MC_DRAWS = 200
MC_SUBSET_FRAC = 0.7
MC_MAX_CV = 2.5  # soft/observe threshold; hard later

# Internal codes (public lexicon scrubbed elsewhere)
CODE_WAIVE_FORBIDDEN = "n_waive_forbidden"
CODE_TIMING_DIM = "timing_dim_above_budget"
CODE_PLACEBO = "placebo_not_top5pct"
CODE_LOO = "loo_mean_unstable"
CODE_MC = "mc_subset_variance_too_high"
CODE_NOISE = "noise_stress_fragile"
CODE_N_DISCOUNT = "n_discounted_score_below_floor"

ANTI_EVASION_PROMPT_ZH = (
    "禁止为凑 n 而：拆 timeframe、噪声灌样本抬成交、参数考古刷笔数、或要求放宽 trade_count。"
    "n 不足则保持研究中/未达到机器基础门槛；不得把低 n 高 C 当成功。"
    "timing 叶宜少：优先 2–3 个可解释时机叶，禁止堆叶硬滤。"
)


def _env(name, default=""):
    return str(os.environ.get(name, default) or default).strip()


def _tier(name, default):
    raw = _env(name, default).lower()
    if raw in (TIER_HARD, TIER_SOFT, TIER_OBSERVE, TIER_OFF, "0", "1", "true", "false"):
        if raw in ("1", "true"):
            return TIER_OBSERVE if name.startswith("CREATE_PLACEBO") or name in (
                "CREATE_LOO", "CREATE_MC_SUBSET", "CREATE_NOISE_STRESS",
            ) else TIER_SOFT
        if raw in ("0", "false"):
            return TIER_OFF
        return raw
    return default


def config():
    """Resolved tiers for this process (hub drop-in may differ)."""
    kappa = DEFAULT_KAPPA
    try:
        kappa = float(_env("CREATE_N_DISCOUNT_KAPPA", str(DEFAULT_KAPPA)))
    except Exception:
        kappa = DEFAULT_KAPPA
    anti = _env("CREATE_ANTI_EVASION", "1") not in ("0", "false", "off", "")
    discount = _env("CREATE_N_DISCOUNT", "1") not in ("0", "false", "off", "")
    return {
        "anti_evasion": anti,
        "n_discount": discount,
        "kappa": kappa,
        "timing_budget": _tier("CREATE_TIMING_BUDGET", TIER_SOFT),
        "placebo": _tier("CREATE_PLACEBO", TIER_OBSERVE),
        "loo": _tier("CREATE_LOO", TIER_OBSERVE),
        "mc_subset": _tier("CREATE_MC_SUBSET", TIER_OBSERVE),
        "noise_stress": _tier("CREATE_NOISE_STRESS", TIER_OFF),
        "seed": int(_env("CREATE_SMALL_N_SEED", "20260907") or 20260907),
        "phase": "A",
    }


def n_discount_factor(n, kappa=None):
    """Bayesian-style shrinkage weight: n/(n+κ)."""
    if kappa is None:
        kappa = config()["kappa"]
    n = max(0, int(n or 0))
    k = float(kappa) if kappa is not None else DEFAULT_KAPPA
    if k < 0:
        k = DEFAULT_KAPPA
    return float(n) / float(n + k) if (n + k) > 0 else 0.0


def discount_metric(value, n, kappa=None):
    try:
        v = float(value)
    except Exception:
        return None
    return v * n_discount_factor(n, kappa=kappa)


def max_timing_atoms(n):
    """Occam / dim budget: at most max(2, floor(n/10)), cap 3 for Phase A soft."""
    n = int(n or 0)
    if n <= 0:
        return 2
    return max(2, min(3, n // 10))


def count_timing_atoms(timing):
    if not isinstance(timing, (list, tuple)):
        return 0
    n = 0
    for item in timing:
        if isinstance(item, dict) and item.get("factor"):
            n += 1
    return n


def _returns_from_ev(ev):
    qg = (ev or {}).get("quality_gate") or {}
    bt = None
    # prefer explicit trade_returns if present on ev
    rets = (ev or {}).get("trade_returns")
    if rets:
        return [float(r) for r in rets]
    is_gate = qg.get("is_gate") or {}
    metrics = is_gate.get("metrics") or qg.get("metrics") or {}
    # no raw series — synthesize nothing; caller may pass returns=
    return []


def placebo_label_shuffle(returns, n_perm=PLACEBO_DRAWS, seed=0):
    """Shuffle y (returns) n_perm times; require original mean in top 5% of null means."""
    rets = [float(r) for r in (returns or [])]
    n = len(rets)
    if n < 3:
        return {
            "ok": None,
            "skipped": True,
            "reason": "too_few_trades",
            "n": n,
        }
    obs = sum(rets) / float(n)
    rng = random.Random(int(seed))
    beat = 0
    for _ in range(int(n_perm)):
        ys = list(rets)
        rng.shuffle(ys)
        m = sum(ys) / float(n)
        if m >= obs - 1e-15:
            beat += 1
    # rank: fraction of nulls >= obs; want this <= 5% (obs in top 5%)
    p_ge = beat / float(n_perm)
    ok = p_ge <= float(PLACEBO_TOP_FRAC)
    return {
        "ok": ok,
        "skipped": False,
        "obs_mean": obs,
        "null_ge_frac": p_ge,
        "top_frac_required": PLACEBO_TOP_FRAC,
        "n_perm": int(n_perm),
        "n": n,
        "code": CODE_PLACEBO,
    }


def loo_mean_stability(returns):
    """Leave-one-out: sign of mean should stay stable; CV of LOO means not huge."""
    rets = [float(r) for r in (returns or [])]
    n = len(rets)
    if n < 4:
        return {"ok": None, "skipped": True, "reason": "too_few_trades", "n": n}
    full = sum(rets) / float(n)
    loo = []
    for i in range(n):
        s = (full * n - rets[i]) / float(n - 1)
        loo.append(s)
    mean_loo = sum(loo) / float(n)
    var = sum((x - mean_loo) ** 2 for x in loo) / float(max(n - 1, 1))
    std = math.sqrt(var) if var > 0 else 0.0
    # unstable if many LOO means flip sign vs full mean
    flips = sum(1 for x in loo if (full >= 0 and x < 0) or (full < 0 and x >= 0))
    flip_frac = flips / float(n)
    ok = flip_frac <= 0.25 and (abs(full) < 1e-12 or std / max(abs(full), 1e-9) <= 3.0)
    return {
        "ok": ok,
        "skipped": False,
        "full_mean": full,
        "loo_std": std,
        "flip_frac": flip_frac,
        "n": n,
        "code": CODE_LOO,
    }


def mc_subset_variance(returns, n_draws=MC_DRAWS, frac=MC_SUBSET_FRAC, seed=0):
    rets = [float(r) for r in (returns or [])]
    n = len(rets)
    k = max(3, int(math.floor(n * float(frac))))
    if n < 6 or k >= n:
        return {"ok": None, "skipped": True, "reason": "too_few_trades", "n": n}
    rng = random.Random(int(seed) + 17)
    means = []
    for _ in range(int(n_draws)):
        idx = list(range(n))
        rng.shuffle(idx)
        sub = [rets[i] for i in idx[:k]]
        means.append(sum(sub) / float(k))
    mu = sum(means) / float(len(means))
    var = sum((x - mu) ** 2 for x in means) / float(max(len(means) - 1, 1))
    std = math.sqrt(var) if var > 0 else 0.0
    cv = std / max(abs(mu), 1e-9)
    ok = cv <= float(MC_MAX_CV)
    return {
        "ok": ok,
        "skipped": False,
        "mean_of_means": mu,
        "std": std,
        "cv": cv,
        "max_cv": MC_MAX_CV,
        "n_draws": int(n_draws),
        "subset_k": k,
        "n": n,
        "code": CODE_MC,
    }


def noise_stress(returns, seed=0, sigma=0.001, trials=50):
    """Stability stress only — never used to inflate n."""
    rets = [float(r) for r in (returns or [])]
    n = len(rets)
    if n < 4:
        return {"ok": None, "skipped": True, "reason": "too_few_trades", "n": n}
    base = sum(rets) / float(n)
    rng = random.Random(int(seed) + 99)
    flip = 0
    for _ in range(int(trials)):
        noisy = [r + rng.gauss(0.0, sigma) for r in rets]
        m = sum(noisy) / float(n)
        if (base >= 0 and m < 0) or (base < 0 and m >= 0):
            flip += 1
    frac = flip / float(trials)
    ok = frac <= 0.35
    return {
        "ok": ok,
        "skipped": False,
        "flip_frac": frac,
        "sigma": sigma,
        "trials": int(trials),
        "code": CODE_NOISE,
    }


def _apply_tier(tier, check, hard_list, soft_list, observe):
    if not check or check.get("skipped"):
        if check:
            observe[check.get("code") or "skipped"] = check
        return
    code = check.get("code") or "unknown"
    observe[code] = check
    if check.get("ok") is True:
        return
    if check.get("ok") is not False:
        return
    if tier == TIER_HARD:
        hard_list.append(code)
    elif tier == TIER_SOFT:
        soft_list.append(code)
    # observe: evidence only


def evaluate(returns=None, n=None, timing=None, recipe=None, ev=None, cfg=None):
    """Run Phase A rigor. Returns dict with ok/failed_rules/warnings/evidence/feedback."""
    cfg = cfg or config()
    rets = list(returns or [])
    if not rets and ev is not None:
        rets = _returns_from_ev(ev)
    if n is None:
        n = len(rets)
        if ev is not None and (ev.get("n") is not None):
            try:
                n = int(ev.get("n"))
            except Exception:
                pass
    n = int(n or 0)
    timing = timing
    if timing is None and isinstance(recipe, dict):
        timing = recipe.get("timing")
    atoms = count_timing_atoms(timing)
    budget = max_timing_atoms(n if n > 0 else 30)

    hard = []
    soft = []
    evidence = {
        "config": cfg,
        "n": n,
        "timing_atoms": atoms,
        "timing_budget": budget,
        "n_discount_factor": n_discount_factor(n, cfg.get("kappa")),
    }

    # timing budget
    tb = cfg.get("timing_budget") or TIER_OFF
    if tb != TIER_OFF and atoms > budget:
        row = {
            "ok": False,
            "atoms": atoms,
            "budget": budget,
            "code": CODE_TIMING_DIM,
        }
        _apply_tier(tb, row, hard, soft, evidence)

    seed = int(cfg.get("seed") or 20260907)
    if rets:
        _apply_tier(cfg.get("placebo") or TIER_OFF,
                    placebo_label_shuffle(rets, seed=seed), hard, soft, evidence)
        _apply_tier(cfg.get("loo") or TIER_OFF,
                    loo_mean_stability(rets), hard, soft, evidence)
        _apply_tier(cfg.get("mc_subset") or TIER_OFF,
                    mc_subset_variance(rets, seed=seed), hard, soft, evidence)
        _apply_tier(cfg.get("noise_stress") or TIER_OFF,
                    noise_stress(rets, seed=seed), hard, soft, evidence)

    ok = not hard
    return {
        "ok": ok,
        "failed_rules": list(hard),
        "warnings": list(soft),
        "evidence": evidence,
        "n_discount_factor": evidence["n_discount_factor"],
        "anti_evasion_prompt_zh": ANTI_EVASION_PROMPT_ZH if cfg.get("anti_evasion") else "",
        "phase": "A",
    }


def enrich_feedback_machine(machine, ev=None, recipe=None, returns=None):
    """Attach discounted scores + rigor warnings into pairing machine dict (soft)."""
    machine = dict(machine or {})
    cfg = config()
    n = machine.get("n")
    if n is None and ev is not None:
        n = ev.get("n")
    try:
        n = int(n or 0)
    except Exception:
        n = 0
    if cfg.get("n_discount"):
        for key in ("C_week_pct", "C_week_oos_pct", "C_week", "C_week_oos"):
            if machine.get(key) is None:
                continue
            dkey = key + "_n_discounted"
            machine[dkey] = discount_metric(machine.get(key), n, cfg.get("kappa"))
        machine["n_discount_factor"] = n_discount_factor(n, cfg.get("kappa"))
        machine["n_discount_kappa"] = cfg.get("kappa")
        # Soft signal: low n high C is not progress
        c = machine.get("C_week_pct")
        dc = machine.get("C_week_pct_n_discounted")
        if c is not None and n < 30:
            machine["low_n_high_C_not_progress"] = True
            if dc is not None:
                machine["note_zh_internal"] = "n_discounted_score"
    timing = None
    if isinstance(recipe, dict):
        timing = recipe.get("timing")
    rig = evaluate(returns=returns, n=n, timing=timing, recipe=recipe, ev=ev, cfg=cfg)
    machine["small_n_rigor"] = {
        "ok": rig.get("ok"),
        "failed_rules": rig.get("failed_rules"),
        "warnings": rig.get("warnings"),
        "timing_atoms": (rig.get("evidence") or {}).get("timing_atoms"),
        "timing_budget": (rig.get("evidence") or {}).get("timing_budget"),
        "n_discount_factor": rig.get("n_discount_factor"),
    }
    if rig.get("warnings"):
        machine.setdefault("warnings", [])
        for w in rig["warnings"]:
            if w not in machine["warnings"]:
                machine["warnings"].append(w)
    return machine


def never_waive_trade_count(ev):
    """Hard replacement for _waive_trade_count_only: never clear trade_count failure."""
    ev = dict(ev or {})
    failed = list(ev.get("failed_rules") or [])
    if failed:
        return None, ",".join(failed)
    if not ev.get("ok"):
        return None, "gate_not_ok"
    gE = ev.get("E")
    if gE is None:
        gE = (((ev.get("quality_gate") or {}).get("is_gate") or {}).get("metrics") or {}).get("E_raw")
    try:
        gE = float(gE) if gE is not None else None
    except Exception:
        gE = None
    if gE is None or gE + 1e-12 < 0.003:
        return None, "E_raw_below_threshold"
    # still require no trade_count failure in nested gate
    q_failed = list((ev.get("quality_gate") or {}).get("failed_rules") or [])
    if "trade_count_below_threshold" in q_failed or "trade_count_below_threshold" in failed:
        return None, CODE_WAIVE_FORBIDDEN
    return ev, None


def augment_user_brief(text):
    if not config().get("anti_evasion"):
        return text
    base = str(text or "")
    if "禁止为凑 n" in base:
        return base
    return base.rstrip() + "\n" + ANTI_EVASION_PROMPT_ZH + "\n"


def install_into_kdh(kdh):
    """Monkeypatch invent module (hub drop-in). Safe to call once at boot."""
    if getattr(kdh, "_small_n_rigor_installed", False):
        return {"already": True}
    cfg = config()

    if cfg.get("anti_evasion"):
        kdh._waive_trade_count_only = never_waive_trade_count

    _orig_brief = kdh._user_brief

    def _user_brief(lane, symbols):
        return augment_user_brief(_orig_brief(lane, symbols))

    kdh._user_brief = _user_brief

    _orig_pair = kdh._pair_feedback

    def _pair_feedback(recipe, result, ident, adjusts, blast=False):
        result = enrich_feedback_machine(result, recipe=recipe)
        # Keep feedback compact but include discount keys
        return _orig_pair(recipe, result, ident, adjusts, blast=blast)

    kdh._pair_feedback = _pair_feedback

    _orig_eval = kdh.evaluate_atom

    def evaluate_atom(cand):
        out = _orig_eval(cand)
        if not isinstance(out, dict):
            return out
        recipe = None
        if isinstance(cand, dict):
            recipe = cand.get("recipe") or (cand.get("payload") or {}).get("recipe")
        timing = None
        if isinstance(recipe, dict):
            timing = recipe.get("timing")
        # Prefer IR timing if present
        ir = out.get("ir") or {}
        if timing is None and isinstance(ir, dict):
            entry = ir.get("entry") or {}
            timing = entry.get("timing") or ir.get("timing")
        rig = evaluate(
            n=out.get("n"),
            timing=timing,
            recipe=recipe if isinstance(recipe, dict) else None,
            ev=out,
        )
        out["small_n_rigor"] = {
            "ok": rig.get("ok"),
            "failed_rules": rig.get("failed_rules"),
            "warnings": rig.get("warnings"),
            "n_discount_factor": rig.get("n_discount_factor"),
            "evidence_timing_budget": (rig.get("evidence") or {}).get("timing_budget"),
            "evidence_timing_atoms": (rig.get("evidence") or {}).get("timing_atoms"),
        }
        if rig.get("failed_rules"):
            failed = list(out.get("failed_rules") or [])
            for code in rig["failed_rules"]:
                if code not in failed:
                    failed.append(code)
            out["failed_rules"] = failed
            out["ok"] = False
        if rig.get("warnings"):
            out["rigor_warnings"] = list(rig["warnings"])
        # soft discount on reported C if present later — evaluate_atom may lack C
        if cfg.get("n_discount") and out.get("n") is not None:
            out["n_discount_factor"] = n_discount_factor(out.get("n"), cfg.get("kappa"))
        return out

    kdh.evaluate_atom = evaluate_atom
    kdh._small_n_rigor_installed = True
    kdh._small_n_rigor_config = cfg
    return {"installed": True, "config": cfg}


def phase0_baseline_template():
    """Empty baseline sheet for Phase 0 recording."""
    return {
        "phase": "0",
        "metrics": {
            "pass_rate": None,
            "waive_count": None,
            "low_n_high_C_count": None,
            "avg_timing_atoms": None,
            "window_zh": "改前1-2周",
        },
        "tiers_zh": {
            "H": "硬否决",
            "S": "软扣分/警告",
            "O": "只观测",
        },
        "day0_defaults": {
            "hub_b": {
                "CREATE_ANTI_EVASION": "1",
                "CREATE_N_DISCOUNT": "1",
                "CREATE_TIMING_BUDGET": "soft",
                "CREATE_PLACEBO": "observe",
                "CREATE_LOO": "observe",
                "CREATE_MC_SUBSET": "observe",
                "CREATE_NOISE_STRESS": "off",
            },
            "hub_a_suggested": {
                "CREATE_ANTI_EVASION": "1",
                "CREATE_N_DISCOUNT": "1",
                "CREATE_TIMING_BUDGET": "soft",
                "CREATE_PLACEBO": "observe",
                "CREATE_LOO": "observe",
                "CREATE_MC_SUBSET": "observe",
                "CREATE_NOISE_STRESS": "off",
                "note": "统计项与 b 同为 observe；禁止两侧同日升 hard",
            },
        },
    }
