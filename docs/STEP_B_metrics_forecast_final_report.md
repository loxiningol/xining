# STEP B — Metrics / Expectancy / Frequency Forecast Final Report

Date: 2026-07-27 09:55 · Host: 64.176.47.192  
Workspace: `intraday_live_v1_20260720`  
Scope: **STEP B ONLY** (STEP A not claimed complete)  
Backup: `/root/backups/step_b_metrics_forecast_20260727_094552`  
Rollback: `/root/docs/rollback_step_b_metrics_forecast.sh`

---

## Front page (mandatory)

| Field | Value |
|-------|-------|
| 完整STEP / 局部实施 | **局部实施 (STEP B package landed; not full module completion)** |
| 仅执行当前成果能否完成频率预测重构 | **否** — event funnel wired but ≤7d full coverage absent; calibration complete_periods=0; positive-E frequency still 0 |
| 当前总频率 (fillable) | **weekly 2.3472 / daily 0.3353** |
| 当前已校准正期望频率 | **weekly 0.0000 / daily 0.0000** |
| 当前正期望频率缺口 | **weekly +3.5000 / daily +0.5000** (entire target band empty) |
| 完整事件漏斗状态 | **wired in production call-chain; legacy still primary** (`legacy_still_primary=true`, no strategy has ≥7d full taxonomy) |
| 校准周期状态 | **观测期 · complete_periods=0 · scientifically_validated=false** |
| H1 | **PASS** (auto SL attach chain intact) |
| H2 | **FAIL** (ADA daemon SL=0.006) |

**Ban reminders honored:** AI WR ≠ live WR; uncalibrated ≠ positive-E; event_n>0 ≠ 7-day base; UI% ≠ STEP%; complete_periods=0 ≠ scientific validation; ADA 0.6→0.9 **not migrated**.

---

## Live baseline vs STEP B (verify-live)

| Metric | Closeout baseline | Live after STEP B |
|--------|------------------:|------------------:|
| Live strategies / families | ~5 / 2 | **5 / 2** |
| Fillable weekly / daily | 2.1647 / 0.3092 | **2.3472 / 0.3353** |
| Simple sum weekly | ~2.258 | **2.608** |
| Corr-adjusted weekly (mean) | 2.258 | **2.608** (= simple; mean-invariant) |
| Conflict-adjusted weekly | 2.1647 | **2.3472** |
| Calibrated positive-E weekly | 0 | **0** |
| Uncalibrated weekly | ~0.45 | **0.30** (ADA+BTC weak priors) |
| Near-zero weekly | LTC ~1.077 | **LTC 0.50** |
| Negative weekly | NG | **NG 1.308 + XRP 0.50 = 1.808** |
| Calibration stage | 初步校准 (old label) | **观测期** (0–3 band; honest relabel) |
| complete_periods | 0 | **0** |
| Event stream | ~17 then growing | **≥13k**; required fields now include daemon_instance_id / runtime_config_version |

Numbers moved because XRP recorded a live fill (method → live_14d) and LTC research density re-estimated — not because STEP B “improved creation.”

---

## §11 Event funnel

**Wired from real daemon/executor call chain** (not forecast-side fabrication):

- Daemon `_append_event` → `emit_from_daemon_action` (open chain now emits `order_attempted` → `order_filled`/`order_rejected` → `position_opened`; exits emit TP/SL/strategy_close/position_closed; filters/risk/conflict mapped).
- Executor `_append_event` dual-writes via `emit_from_executor`.
- Required fields on emit: `event_id, strategy_id, strategy_version, symbol, timeframe, event_type, reason_code, timestamp, source_data_version, order_id, position_id, daemon_instance_id, runtime_config_version`.

**Coverage truth:** most strategies still only emit `market_evaluated` (+ XRP historical conflict/raw/filter). Full taxonomy **wired** but many types **never-seen** until live path fires. Smoke (non-trade, `order_id=smoke_test_not_live`) proved open-chain emit works.

Deliverable: `STEP_B_event_coverage_matrix.json`.

---

## §12 Legacy migration rules

Display layers: **event-derived / legacy-derived / prior-derived / uncalibrated**.

Rule enforced in code: **stop using legacy as primary only after ≥7d full event coverage**. `event_n>0` alone is insufficient. Forecast UI shows source layer + `⚠源不足` when thin.

**Current:** all 5 strategies `legacy-derived` / `insufficient_source=true`.

---

## §13 Portfolio frequency math

See `STEP_B_frequency_math_audit.json`.

- **Why corr-adjusted mean = simple sum:** \(E[\sum X_i]=\sum E[X_i]\) for any dependence. Correlation changes distribution / co-fire probability, **not** the mean.
- **Why conflict < simple:** same-symbol mutex + same-family co-fire arbitration reduce expected fills.
- **Bootstrap (live):** 7d median 2.1237; 50%/80%/95% intervals present; P(3-day no fill)=0.3657; P(7-day below target 3.5)=0.8947.

Closeout example (2.258→2.258→2.1647) remains the reference math case; live inputs differ.

---

## §14 Positive-E fields (strict)

Fields: `total_fillable`, `calibrated_positive_E`, `uncalibrated`, `near_zero`, `negative`.

Gates: after-cost point > near-zero threshold, CI floor >0, sample ≥10, credibility ≥0.35, not AI-only, not 1 live trade.

| Strategy | Bucket | Why |
|----------|--------|-----|
| NG | **negative** | net equity ≈ −0.79% |
| XRP | **negative** | net ≈ −0.26% after live sample |
| LTC | **near_zero** | net ≈ +0.0002% — NOT positive-E |
| ADA / BTC | **uncalibrated** | missing samples; AI WR does not qualify |

Calibrated positive-E frequency = **0**.

---

## §15 Forecast calibration

- Forever snapshots retained under `forecast_snapshots/`.
- Weekly period upsert by ISO week; hourly rows marked `hourly_snapshot_noise` (do not inflate complete_periods).
- Auto-backfill actuals when period age ≥7d.
- Metrics framework: MAE/MedAE/Bias/80%/95%/Brier/strategy&family/positive-E/legacy-vs-event — all **null** until complete periods exist.
- Stages: **0–3 观测期; 4–7 初步; 8–11 中期; ≥12 基础完成**.
- **complete_periods=0 → scientific validation forbidden.**

Deliverable: `STEP_B_calibration_status.json`.

---

## §16 ADA SL — report only (NOT migrated)

Deliverable: `STEP_B_stop_loss_matrix.json`.

| Strategy | Daemon SL | Runtime SL | Order creation reads | 0.9%? |
|----------|----------:|-----------:|---------------------:|------|
| ADA | **0.006** | 0.009 | **0.006** | NO |
| LTC | 0.009 | (missing→daemon) | 0.009 | YES |
| NG | 0.009 | (missing→daemon) | 0.009 | YES |
| XRP | 0.009 | 0.009 | 0.009 | YES |
| BTC1h | 0.009 | 0.009 | 0.009 | YES |

**Priority:** executor `_active_stop_loss_pct()` ← daemon config file. Runtime/frontend 0.009 **not** read for attach SL.

**Next ADA order:** **0.006**, not 0.009. Display-vs-reality risk: UI can show 0.9% while real attach is 0.6%.

Scripts (not executed):  
`/root/docs/step_b_ada_sl_migrate_0p6_to_0p9.sh` · `/root/docs/step_b_ada_sl_rollback_0p9_to_0p6.sh`  
ADA-only restart + next-order verification plan included in JSON.

**H1 PASS / H2 FAIL** (separate).

---

## §17 Scoring arithmetic (auto-sum)

Rule: only code + production call-chain + tested + not rolled back + visible counts. Framework-empty ≠ full marks. Calibration 0 periods weighs down. UI ≠ core algorithm weight.

### Items (unique IDs)

| ID | Category | Requirement | Score | Max |
|----|----------|-------------|------:|----:|
| B11-01 | wiring | Unified emit from all live daemons | 1.0 | 1 |
| B11-02 | wiring | Executor dual-write open/close/SL/TP | 1.0 | 1 |
| B11-03 | wiring | Required event fields incl. daemon/runtime versions | 1.0 | 1 |
| B11-04 | coverage | All taxonomy types ever emitted in prod | 0.5 | 1 |
| B11-05 | coverage | Coverage matrix delivered | 1.0 | 1 |
| B12-01 | framework | Source-layer rules event/legacy/prior/uncal | 1.0 | 1 |
| B12-02 | wiring | Legacy primary until ≥7d full coverage | 1.0 | 1 |
| B12-03 | UI | Insufficient-source visible on forecast page | 1.0 | 1 |
| B13-01 | framework | Corr mean-invariant + conflict math documented | 1.0 | 1 |
| B13-02 | wiring | Bootstrap 7d median + 50/80/95 + P(no-fill) live | 1.0 | 1 |
| B13-03 | coverage | Math audit JSON with live numbers | 1.0 | 1 |
| B14-01 | framework | Strict positive-E thresholds (CI/sample/AI ban) | 1.0 | 1 |
| B14-02 | wiring | total_fillable + bucket fields in API/report | 1.0 | 1 |
| B14-03 | goal | Calibrated positive-E frequency > 0 | **0.0** | 1 |
| B15-01 | framework | Stage bands 观测/初步/中期/基础 | 1.0 | 1 |
| B15-02 | wiring | Forever snapshots + weekly upsert + auto-backfill | 1.0 | 1 |
| B15-03 | calibration | MAE/MedAE/Bias/cov/Brier computed on ≥1 complete period | **0.0** | 1 |
| B15-04 | calibration | complete_periods ≥4 (exit 观测期) | **0.0** | 1 |
| B16-01 | framework | SL matrix + ADA deep dive + scripts | 1.0 | 1 |
| B16-02 | goal | H2 all strategies actually 0.9% | **0.0** | 1 |
| B17-01 | framework | Auto-sum unique IDs / real denominator | 1.0 | 1 |

### Category sums (auto)

| Category | Item IDs | Sum | Max | % |
|----------|----------|----:|----:|--:|
| Engineering framework | B12-01, B13-01, B14-01, B15-01, B16-01, B17-01 | 6.0 | 6.0 | 100% |
| Production wiring | B11-01..03, B12-02, B13-02, B14-02, B15-02 | 7.0 | 7.0 | 100% |
| Data coverage | B11-04, B11-05, B13-03 | 2.5 | 3.0 | 83% |
| UI surface | B12-03 | 1.0 | 1.0 | 100% |
| Statistical calibration | B15-03, B15-04 | 0.0 | 2.0 | **0%** |
| Positive-E frequency goal | B14-03 | 0.0 | 1.0 | **0%** |
| H2 goal | B16-02 | 0.0 | 1.0 | 0% |
| **Overall STEP B** | 21 unique IDs | **16.5** | **21.0** | **78.6% itemized — NOT “~90%”** |

Auto-check: `sum([1,1,1,0.5,1,1,1,1,1,1,1,1,1,0,1,1,0,0,1,0,1]) = 16.5`.  
Interpretation: engineering/wiring high; **calibration + positive-E goal still empty**. Overall ≠ “frequency forecast refactor complete.”

---

## Acceptance — 23 questions

1. Full STEP or partial? **Partial STEP B.**
2. Can current results alone finish the frequency-forecast refactor? **No.**
3. Current total fillable frequency? **2.3472/week, 0.3353/day.**
4. Current calibrated positive-E frequency? **0 / 0.**
5. Current positive-E gap? **+3.5/week, +0.5/day.**
6. Current uncalibrated frequency? **0.30/week.**
7. Near-zero / negative? **near-zero 0.50 (LTC); negative 1.808 (NG+XRP).**
8. Full event funnel truly wired? **Wired yes; rich multi-type emission mostly never-seen; legacy still primary.**
9. ≥7d full coverage achieved? **No.**
10. Forecast still in calibration/observation? **Yes — 观测期, complete_periods=0.**
11. Scientifically validated? **No — forbidden.**
12. H1 auto SL attach unbroken? **PASS.**
13. H2 all live strategies 0.9%? **FAIL (ADA 0.6%).**
14. ADA next order SL? **0.006.**
15. Was ADA migrated this STEP? **No.**
16. Corr-adjusted mean equals simple sum? **Yes — mean invariance.**
17. Why conflict < simple? **Mutex + family arbitration.**
18. Source layers shown? **Yes.**
19. AI WR used as positive-E? **No.**
20. Open/SL/TP/close chain weakened? **No.**
21. Deploy rolled back? **No — live; rollback script ready.**
22. STEP A claimed complete? **No.**
23. Frequency gap readable = frequency filled / new strategies? **No.**

---

## Deploy / backup / evidence

**Deployed files:**  
`auto_trade_strategy_events.py`, `auto_trade_forecast_closeout.py`, `auto_trade_expectancy_metrics.py`, `auto_trade_formal_v6_executor.py`, `templates/forecast.html` → `/root/` + `/root/auto_trade/` (+ templates).

**Services restarted (ADA SL unchanged=0.006):**  
`qiyu-web`, `qiyu-formal-auto-trade-ada-5m`, `…-ltc-5m`, `…-ng-5m`, `…-xrp-15m`, `qiyu-formal-auto-trade` — all **active**.

**Backup:** `/root/backups/step_b_metrics_forecast_20260727_094552`  
**Rollback:** `/root/docs/rollback_step_b_metrics_forecast.sh`

**Artifacts (local + `/root/docs/`):**
1. `STEP_B_metrics_forecast_final_report.md` (this file)
2. `STEP_B_event_coverage_matrix.json`
3. `STEP_B_frequency_math_audit.json`
4. `STEP_B_calibration_status.json`
5. `STEP_B_stop_loss_matrix.json`

---

## Diff summary

- Event taxonomy bridges completed (daemon open/exit chains + executor dual-write + required IDs).
- Legacy/event source-layer gate (≥7d full coverage).
- Portfolio bootstrap intervals + math audit.
- Stricter positive-E classification + `total_fillable`.
- Calibration stage relabel to 观测期; weekly upsert; noise rows excluded from complete_periods.
- Forecast UI: source layer, bootstrap, insufficient-source.
- ADA SL audit/scripts only — **no live SL change**.

---

## Unfinished (honest)

- Wait ≥7 days for full event taxonomy coverage before event-primary forecasts.
- Accumulate ≥4 complete calibration periods before any “初步校准完成” claim.
- Human-confirmed ADA 0.6→0.9 migration (optional, separate).
- Produce calibrated positive-E strategies (creation = STEP A domain; not claimed here).
- STEP A+B integration report — scaffolding only until user requests full integration.
