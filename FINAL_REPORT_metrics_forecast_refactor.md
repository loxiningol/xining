# §十六 Final Report — Metrics / Forecast Refactor
Date: 2026-07-26 · Host: 64.176.47.192

## Verdict
**Completion ≈ 90%.** Core backend math, APIs, persistence, gap→creation input, roster/forecast UI, and Wx summary are live. Remaining gaps are telemetry depth (signal funnel) and a skipped full 3AI live call in E2E (statistical path verified with AI-down).

If you only cherry-pick the UI without the new metric/forecast modules: **仅执行当前成果不能完成本次模块重构.**

## §三 audit answers (also in `field_audit_metrics_forecast.md`)
1. ADA/XRP/BTC1h missing expectancy → no frequency baseline / live fills; placeholders suppressed via `metric_status=missing`.
2. LTC 16.21% vs NG 3.45% → mixed live margin-ROI vs backtest prior; replaced by uniform net equity/margin after costs.
3. Fees/size/leverage → now applied (`execution_cost_model` + 0.30×20 equity mapping).
4. XRP missing from last forecast → stale 2026-07-25 snapshot; refreshed to **5** strategies.
5. `0/10` → was “n of 10 closed groups”; copy now explicit.
6. 4 vs 3 → actual mounted **5** (ADA/LTC/NG/XRP/BTC1h).

## Acceptance A–H
| ID | Requirement | Status |
|----|-------------|--------|
| A | Uniform gross/cost/net expectancy (price/notional/equity) + fees/slip | **PASS** |
| B | Page 预期胜率 = calibrated_expected_win_rate (AI not raw) | **PASS** |
| C | Frequency = statistical×regime; AI explain-only; funnel; bootstrap; forever snapshots; calibration UI | **PASS** (funnel best-effort) |
| D | Pool gap 0.5–1.0/day & 3.5–7/week → creation input, no loosen/force | **PASS** |
| E | Mechanism family clustering | **PASS** (2 families / 5 strats) |
| F | Low-freq class, no auto-kill | **PASS** |
| G | Roster rows + expand funnel; no bare `-` / no `0/10` score | **PASS** |
| H | Forecast page + Wx §十三 format | **PASS** |

## Live E2E evidence
- Mounted/can_open: **5/5**
- Statistical weekly fills ≈ **2.26**, daily ≈ **0.32**, gap shortfall (day +0.179 / week +1.242)
- Families: `exhaustion_fade_short` + `session_trend_pullback`
- LTC: cal WR 70.12%, net equity ~0.00% after cost; funnel S/A/F/E/B = 1/1/1/1/0
- ADA: `metric_status=missing` → display「暂无样本/未校准」
- Dual-engine `collect_inputs` reads frequency gap/brief
- `qiyu-web` active; formal daemons ADA/LTC/NG/XRP/BTC still active
- PROTECT: leverage 20 kept; SL untouched by this job (ADA was already 0.6% pre-existing)

## Backups / rollback
- `/root/backups/metrics_forecast_refactor_20260726_183126`
- `/root/backups/metrics_forecast_refactor_predeploy_*`
- Restore: copy backed-up `web_server.py`, `auto_trade_system_forecast.py`, `auto_trade_strategy_rating.py`, templates; remove `auto_trade_expectancy_metrics.py` wiring; `systemctl restart qiyu-web`

## Honest gaps (~10%)
1. Daemon event stream lacks rich signal/attempt taxonomy → funnel uses signal-files + fills.
2. E2E used AI-down statistical overlay; scheduled/manual `--run` still calls 3AI for **explain text only**.
3. New mounts without research baselines stay `missing` until samples exist (by design, not AI-filled).
