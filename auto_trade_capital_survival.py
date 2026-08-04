# -*- coding: utf-8 -*-
"""Small-capital survival audit — API/compute cost vs expected edge.

Produces breakeven surfaces and recommended budget caps for 17U-class books.
"""
from __future__ import print_function

from datetime import datetime
from pathlib import Path
import json
import os
import tempfile

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
REPORT_PATH = AUTO_DIR / "capital_survival_report.json"
ECO_DB = AUTO_DIR / "strategy_ecosystem.db"

# Conservative unit costs (USD). Override via strategy_breath_sensors / env.
DEFAULT_COSTS = {
    "deepseek_per_call": 0.002,
    "qwen_per_call": 0.003,
    "chatgpt_per_call": 0.02,
    "vps_daily": 0.20,          # share of Vultr attributed to this stack
    "okx_fee_rt_rate": 0.001,   # already in strategy friction; kept for clarity
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def estimate_daily_ai_calls(profile="current"):
    """Profiles of call volume by subsystem."""
    profiles = {
        "current": {
            "hackathon_creator": 3,          # daily
            "hackathon_peer_review": 6,
            "evolution_revive_ai": 3,        # when enabled hourly-ish → amortize
            "env_boundary_ai": 2,
            "solvability_ai": 3,
            "shock_draft_ai": 0,
            "note": "current timers; evolution AI often skip_ai on hour path",
        },
        "aggressive": {
            "hackathon_creator": 3,
            "hackathon_peer_review": 6,
            "evolution_revive_ai": 9,
            "env_boundary_ai": 6,
            "solvability_ai": 9,
            "shock_draft_ai": 6,
            "note": "full adaptive engine without budget caps",
        },
        "thrifty": {
            "hackathon_creator": 2,          # drop weakest provider by score
            "hackathon_peer_review": 4,      # degraded pair allowed
            "evolution_revive_ai": 0,        # weekly batch only
            "env_boundary_ai": 1,            # deterministic draft + weekly AI
            "solvability_ai": 1,             # cached unless fingerprint changes
            "shock_draft_ai": 2,
            "note": "budget-aware; format checks local; AI on value path only",
        },
    }
    return profiles.get(profile) or profiles["current"]


def _split_provider_mix(total_calls, mix=None):
    mix = mix or {"deepseek": 0.40, "qwen": 0.35, "chatgpt": 0.25}
    return {k: total_calls * float(v) for k, v in mix.items()}


def daily_ai_cost_usd(calls_by_bucket, costs=None, mix=None):
    costs = dict(DEFAULT_COSTS)
    costs.update(costs or {})
    total = sum(float(v) for k, v in calls_by_bucket.items() if k != "note")
    by_prov = _split_provider_mix(total, mix)
    ai = (by_prov.get("deepseek", 0) * costs["deepseek_per_call"]
          + by_prov.get("qwen", 0) * costs["qwen_per_call"]
          + by_prov.get("chatgpt", 0) * costs["chatgpt_per_call"])
    return {
        "ai_calls": total,
        "ai_usd": round(ai, 4),
        "vps_usd": costs["vps_daily"],
        "total_usd": round(ai + costs["vps_daily"], 4),
        "by_provider_calls": {k: round(v, 2) for k, v in by_prov.items()},
    }


def expected_daily_pnl_usd(equity_usd, opens_per_day, mean_net_pct_equity):
    """mean_net_pct_equity is net edge on total equity per trade after friction."""
    return float(equity_usd) * float(opens_per_day) * float(mean_net_pct_equity)


def breakeven_surface(equity_usd=17.0, cost_usd=0.35):
    rows = []
    for opens in (1, 2, 3, 5):
        for edge_pct in (0.005, 0.01, 0.015, 0.02, 0.03):
            pnl = expected_daily_pnl_usd(equity_usd, opens, edge_pct)
            rows.append({
                "opens_per_day": opens,
                "mean_net_pct_equity": edge_pct,
                "expected_pnl_usd": round(pnl, 4),
                "cost_usd": cost_usd,
                "net_usd": round(pnl - cost_usd, 4),
                "viable": pnl > cost_usd,
            })
    return rows


def recommend_budgets(equity_usd=17.0):
    """Per-cluster daily API spend caps in USD."""
    # Keep AI+VPS under ~30% of optimistic 3×1.5% edge day.
    optimistic = expected_daily_pnl_usd(equity_usd, 3, 0.015)
    ai_budget = max(0.05, min(0.25, 0.30 * optimistic))
    return {
        "equity_usd": equity_usd,
        "daily_ai_budget_usd": round(ai_budget, 3),
        "per_cluster_daily_ai_usd": round(ai_budget / 12.0, 4),
        "chatgpt_calls_daily_cap": 8,
        "deepseek_qwen_calls_daily_cap": 40,
        "rules": [
            "format/schema validation local — no AI",
            "solvability AI only on fingerprint change",
            "evolution revive AI weekly batch, not hourly",
            "hackathon: skip lowest-availability provider if score<0.35",
            "shock-draft only when breath score>=0.55",
        ],
    }


def build_report(equity_usd=17.0):
    costs = dict(DEFAULT_COSTS)
    sensors = _read(AUTO_DIR / "strategy_breath_sensors.json", {})
    for k in DEFAULT_COSTS:
        if k in sensors:
            costs[k] = float(sensors[k])

    profiles = {}
    for name in ("current", "aggressive", "thrifty"):
        buckets = estimate_daily_ai_calls(name)
        profiles[name] = {
            "buckets": buckets,
            "cost": daily_ai_cost_usd(buckets, costs),
        }

    thrifty_cost = profiles["thrifty"]["cost"]["total_usd"]
    surface = breakeven_surface(equity_usd, thrifty_cost)
    # Target operating point
    target = expected_daily_pnl_usd(equity_usd, 3, 0.015)
    report = {
        "ok": True,
        "schema": "qiyu_capital_survival_v1",
        "generated_at": _now(),
        "equity_usd": equity_usd,
        "unit_costs_usd": costs,
        "profiles": profiles,
        "breakeven_surface": surface,
        "target_point": {
            "opens_per_day": 3,
            "mean_net_pct_equity": 0.015,
            "expected_pnl_usd": round(target, 4),
            "thrifty_cost_usd": thrifty_cost,
            "expected_net_usd": round(target - thrifty_cost, 4),
            "viable": target > thrifty_cost,
        },
        "budgets": recommend_budgets(equity_usd),
        "optimization": {
            "cut_first": [
                "hourly evolution AI revive → weekly",
                "duplicate lifecycle timers",
                "ChatGPT on format-only tasks → local parser",
                "solvability unanimous every heartbeat → on change only",
            ],
            "keep": [
                "hackathon nightly (creative bottleneck)",
                "triple-friction offline screens (local)",
                "WxPusher on real fills only",
            ],
            "meta_controller": (
                "Add daily_ai_budget_usd to productivity meta; skip cluster "
                "wake if cluster_spend_usd >= per_cluster_daily_ai_usd unless "
                "breath score>=0.7 and fires_per_day<1"
            ),
        },
        "natural_language": (
            "17U 本金下，节俭配置日成本约 $%.2f；若日均 3 笔、单笔净赚权益 1.5%%，"
            "日期望约 $%.2f，可覆盖成本。激进全开 AI 容易把微利吃光——必须上预算帽。"
            % (thrifty_cost, target)
        ),
    }
    _atomic(REPORT_PATH, report)
    return report


if __name__ == "__main__":
    print(json.dumps(build_report(), ensure_ascii=False, indent=2))
