# FINAL REPORT — Metrics / Forecast Closeout (rejects prior ~90%)
Date: 2026-07-26 19:36 · Host: 64.176.47.192  
Workspace: `intraday_live_v1_20260720`  
Backups: `/root/backups/metrics_forecast_closeout_20260726_191447`, `/root/backups/metrics_forecast_closeout_deploy_20260726_193126`  
Rollback: `/root/docs/rollback_metrics_forecast_closeout.sh`

---

## Mandatory answers (1–15)

1. **Full STEP vs partial:** **PARTIAL STEP.** Closeout modules deployed and live for stats/UI/APIs; event stream just started; calibration has **0** complete forward periods.
2. **Do current results alone complete the whole module refactor?** **No.**
3. **Pre-change production progress baseline:** Prior agent claimed ~90% with best-effort funnel, naive frequency sum, no positive-E gap, no stale-on-mount, no calibration-stage banner, SL H1/H2 conflated. Evidence baseline: forecast 5 strategies @ 18:41, weekly≈2.26 (simple sum), gap shortfall, LTC “~0.00%”.
4. **Deviation from original function:** Frequency headline now **conflict-adjusted fillable** (not naive sum). Creation priority is **positive-E gap**. AI WR demoted to split reference columns. **No live SL auto-migration** (ADA remains 0.6% in daemon config).
5. **H1 / H2 (SEPARATE):**
   - **H1 auto SL attach chain not broken: PASS** (executor attach path + LTC live order `stop_loss_pct=0.009`, exchange distance≈0.008889).
   - **H2 all running strategies actually 0.9%: FAIL** (ADA daemon `stop_loss_pct=0.006`).
6. **Full event funnel truly wired?** **Partially.** `strategy_events.jsonl` emitting after daemon reload (17 events observed). Forecast **prefers** event stream when `event_n>0`; 7-day history still mostly legacy until taxonomy accumulates. Not yet a rich multi-stage production funnel.
7. **Forecast still in calibration period?** **Yes — 初步校准**, complete_periods=**0**. UI banner required: 「预测模型尚处于校准期，当前数值为统计估计，不代表已验证精度。」
8. **Current total frequency (fillable):** weekly **2.1647**, daily **0.3092**
9. **Current calibrated positive-E frequency:** weekly **0.0000**, daily **0.0000**
10. **Current uncalibrated frequency:** weekly **0.4500** (ADA+XRP+BTC weak priors)
11. **Current frequency gap:** daily gap **+0.1908**, weekly gap **+1.3353** (shortfall vs 0.5–1.0 / 3.5–7)
12. **Current positive-E frequency gap:** daily **+0.5000**, weekly **+3.5000** (entire band empty of calibrated positive-E)
13. **Strategies lacking proven after-cost positive expectancy:** **ALL 5** — ADA/XRP/BTC missing samples; LTC indistinguishable from zero; NG negative point estimate (CI includes 0).
14. **Unfinished:** deep event taxonomy rates with sample; ≥4 completed calibration periods; walk-forward WR data source; ADA 0.9% SL human migration; any claim of scientific validation.
15. **Verifiable itemized completion: 76%** (see table). **Not ~90%.**

---

## Verifiable itemized completion (strict)

Score rule: only **code change + production call-chain + tested + not rolled back + visible in UI/API** counts. Theory / unwired / UI-only / failed = 0. Partial = 0.5.

| # | Requirement | Score | Evidence |
|---|-------------|------:|----------|
| 1 | Uniform gross/cost/net expectancy (price/margin/equity) | 1.0 | `auto_trade_expectancy_metrics.py` + `/api/metrics/expectancy` + roster |
| 2 | Cost model fees/slip/funding applied | 1.0 | `execution_cost_model.json` wired |
| 3 | Page primary WR = calibrated_expected_win_rate | 1.0 | roster `calibrated_expected_win_rate_pct` |
| 4 | Split AI / WF / BT / live / calibrated + n + credibility | 0.5 | columns live; **WF source usually empty** |
| 5 | AI must not look high-confidence without samples | 1.0 | `wr_high_confidence` gate; ADA AI 74% shown as 非高可信 |
| 6 | Statistical×regime frequency; AI explain-only | 1.0 | forecast refresh mode statistical |
| 7 | Unified `strategy_event` emit from daemons | 1.0 | `/root/auto_trade/strategy_events.jsonl` n≥17 after reload |
| 8 | Forecast primary source = event stream | 0.5 | prefers events when present; 7d still legacy-dominated |
| 9 | Full funnel rates (filter/risk/conflict/attempt/fill/exit/hold) | 0.5 | schema+API; insufficient multi-type events yet |
| 10 | Forever forecast snapshots | 1.0 | `forecast_snapshots/` |
| 11 | Calibration stage labels + <4 banner | 1.0 | stage=初步校准, banner on |
| 12 | MAE/MedAE/Bias/80% cov/Brier + family/strategy errors | 0.5 | framework live; **0 complete periods**; contribution errors thin |
| 13 | Raw frequency_gap → creation (no loosen/force) | 1.0 | `frequency_gap_report.json` |
| 14 | `positive_expectancy_frequency` + gap prioritization | 1.0 | API + creation input v2 + dual_engine |
| 15 | Mechanism family clustering | 1.0 | 2 families / 5 strats |
| 16 | Correlation + conflict-adjusted portfolio frequency | 1.0 | simple 2.258 → final fillable 2.1647 |
| 17 | Low-frequency class, no auto-kill | 1.0 | `auto_kill=False` |
| 18 | Roster expectancy UI (no bare `-` / no `0/10` score) | 1.0 | 暂无样本/未校准 + expand copy |
| 19 | Forecast UI: stale + pool version + posE + portfolio | 1.0 | `templates/forecast.html` |
| 20 | Mount/pause/unmount/lifecycle → stale + auto statistical refresh | 1.0 | human_confirm + lifecycle hooks |
| 21 | Dual-engine reads positive-E gap | 1.0 | `collect_inputs` fields |
| 22 | Full LTC/all ledgers ≥4 decimals + sign label | 1.0 | `/api/metrics/ledgers` + `docs/closeout_ledgers.json` |
| 23 | SL matrix audit; **no unilateral live SL change** | 1.0 | audit JSON; ADA not auto-fixed |
| 24 | H2: all live strategies actually 0.9% SL | **0.0** | ADA daemon **0.6%** |
| **Sum** | | **19.0 / 25.0** | |
| **Completion** | | **76%** | |

---

## Stop-loss matrix (5 live strategies)

| Strategy | Daemon SL | Runtime SL | DSL SL | Order SL | Exchange distance | Frontend default | 0.9%? |
|----------|----------:|-----------:|--------|----------|-------------------:|-----------------|------|
| ADA `codex0725t3_ada5m_trendpb_r42_z2p3_h14` | **0.006** | 0.009 | none | none yet | n/a | 0.009 | **NO — inconsistent** |
| LTC `ltc5_exhaustion_fade_short_ai` | 0.009 | missing→daemon | none | 0.009 | 0.008889 | 0.009 | YES* |
| NG `ng5_exhaustion_fade_short_ai` | 0.009 | missing→daemon | none | none yet | n/a | 0.009 | YES* |
| XRP `frost_xrp_rescue_h20_t45` | 0.009 | 0.009 | none | none yet | n/a | 0.009 | YES |
| BTC1h `frost3_btc1h_xrpport_exhaustion_fade_slope` | 0.009 (`formal_daemon_config.json`) | 0.009 | none | none yet | n/a | 0.009 | YES |

\*Runtime SL missing on LTC/NG is noted; effective daemon SL is 0.9%.

### ADA 0.9% vs 0.6% “contradiction” resolution
Prior report’s “0.9% protected” meant **do-not-weaken policy / no refactor mutation**, not “all configs equal 0.9%”.  
**Evidence:** `formal_daemon_config_ada_5m.json` still has `stop_loss_pct=0.006` while runtime controls show `0.009`. **H2 FAIL.**

### Safe migration (DO NOT auto-run)
1. Confirm ADA flat / cooldown clear (currently no open positions observed at audit time — re-check before change).  
2. Human-confirm change daemon ADA SL 0.006→0.009 only.  
3. Restart only `qiyu-formal-auto-trade-ada-5m`.  
4. Verify next attach distance ≈0.9% on exchange.  
5. Do not batch-change other strategies’ SL in the same window.

---

## LTC full ledger (ban “约0.00%”)

| Field | Value |
|-------|------:|
| Gross WR | 0.689655 |
| Calibrated WR | 70.12% |
| AI WR (reference) | 60.5% |
| Avg gross win (margin) | 0.14466375 |
| Avg gross loss (margin) | -0.18027989 |
| Payoff ratio | 0.802440 |
| Gross expectancy price % | 0.219100 |
| Entry/exit fee rate (each side) | 0.0005 |
| Entry/exit slip rate (each side) | 0.0002 |
| Half-spread / latency / impact | from cost model |
| Funding component | 0.00001148 |
| Cost price % | 0.219080 |
| **Net price expectancy %** | **0.000000** |
| **Net margin expectancy %** | **0.000300** |
| **Net equity expectancy %** | **0.000100** |
| Expected monthly fills | 2.1429 |
| Expected monthly net equity % | 0.000214 |
| 80% CI (net equity %/trade) | [-1.1520, 1.1173] |
| Samples | bt=28, live=1, n=29 |
| Credibility | 0.703 |
| **Label** | **statistically_indistinguishable_from_zero** |
| Effective core strategy? | **NO** (not clearly positive after cost) |

Near-zero buckets / frequency contribution must **not** elevate LTC to “effective core” solely for fills.

### Other strategies (summary)
- **NG:** net equity **-0.792200%**, CI includes 0 → not clearly positive; point estimate negative; bucket=negative_E.  
- **ADA / XRP / BTC1h:** missing samples → uncalibrated; AI WR 74–79% must not display as high-confidence production expected WR.

---

## Acceptance A–H (original definitions; H split)

| ID | Definition | Verdict |
|----|------------|---------|
| A | Uniform gross/cost/net expectancy + fees/slip | **PASS** |
| B | Page 预期胜率 = calibrated (not raw AI) | **PASS** |
| C | Freq=statistical×regime; AI explain; funnel; bootstrap; forever snaps; calibration UI | **PARTIAL** (funnel/calibration depth) |
| D | Pool gap 0.5–1.0/day & 3.5–7/week → creation, no loosen/force | **PASS** (+ positive-E priority) |
| E | Mechanism family clustering | **PASS** |
| F | Low-freq class, no auto-kill | **PASS** |
| G | Roster rows + expand; no bare `-` / no `0/10` score | **PASS** |
| H1 | SL attach chain not broken | **PASS** |
| H2 | All running strategies actually 0.9% SL | **FAIL** |

---

## Portfolio frequency (anti-naive-sum)

| Metric | Weekly | Daily |
|--------|-------:|------:|
| Simple sum | 2.2580 | 0.3226 |
| Correlation-adjusted (mean-invariant) | 2.2580 | 0.3226 |
| Conflict-adjusted / **final fillable** | **2.1647** | **0.3092** |
| Mutex/arbitration loss | 0.0933 | — |
| Co-signal P | 0.055 | |
| Co-silence P | 0.720 | |

Families: `exhaustion_fade_short` (4) + `session_trend_pullback` (1).

---

## Protect checklist (untouched by design)
Live open/close/TP path, 20x, 30% B-size, WxPusher, SAT_WITNESS — **not weakened**.  
ADA SL **not** auto-changed.

---

## Deploy / rollback
- Deploy script: `deploy_metrics_forecast_closeout.sh`  
- Last deploy backup: `/root/backups/metrics_forecast_closeout_deploy_20260726_193126`  
- Rollback: `bash /root/docs/rollback_metrics_forecast_closeout.sh`  
- Local mirrors: `docs/closeout_*.json`, this report, modules synced in workspace.

---

## Bottom line
**Module refactor is not complete.** Verified completion **76%**, not ~90%.  
**H1 PASS / H2 FAIL.**  
**Calibrated positive-E frequency = 0**; creation must target that gap.  
**Forecast remains in 初步校准 (0 complete periods)** — not scientifically validated.
