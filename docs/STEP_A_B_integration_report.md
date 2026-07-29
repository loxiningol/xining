# STEP A + B Integration Report — Closed-Loop Proof

- Date: 2026-07-27 (UTC+8)
- Host: `64.176.47.192`
- Workspace: `intraday_live_v1_20260720`
- Scope: Integration proof only (no STEP A/B feature re-implementation; ADA SL not migrated)
- Verdict: **Loop skeleton is largely WIRED in code; end-to-end loop has NOT closed in production** (STEP A: 0 all-gate / 0 mount; STEP B: positive-E = 0, gap = +3.5/week)

---

## 0. Front-page summary (do not inflate)

| | STEP A | STEP B |
|--|--------|--------|
| Completeness | **局部** | **局部** |
| Headline numbers | **0** all-gate pass · **0** production mount | fillable ~**2.35**/wk · calibrated positive-E **0** · gap **+3.5**/wk |
| H / gates | Real-net farthest: Gate0–2 pass, Gate3 fail | **H1 PASS** · **H2 FAIL** (ADA daemon SL=0.006) |
| Calibration | N/A (creation domain) | **观测期** · `complete_periods=0` · scientific validation forbidden |
| Can current results alone finish the STEP? | **No** | **No** |
| Primary reports | `STEP_A_strategy_creation_training_final_report.md` | `STEP_B_metrics_forecast_final_report.md` |

**Integration one-liner:** Demand signal and creation/gate/mount/stale/refresh plumbing exist; **no new strategy has traversed the full loop**, so positive-E gap remains the entire target band (+3.5/week). Readability of the gap ≠ gap filled.

---

## 1. Intended closed loop

```
positive_expectancy_frequency_gap
  → strategy_creation_brief / strategy_creation_frequency_input
  → missing family / symbol / tf / freq layer
  → GLM mechanism_spec (immutable)
  → Codex faithful impl + fidelity_diff
  → multi-AI attack
  → Gate0–7
  → human confirm
  → candidate
  → production mount
  → strategy_pool_version change
  → forecast auto-stale
  → lightweight statistical refresh
  → positive-E recalculated
  → only after-cost positive-E meeting credibility shrinks the gap
```

---

## 2. Mandatory proofs (PASS / FAIL with evidence)

| Claim | Result | Evidence |
|-------|--------|----------|
| STEP B only generates creation demand; does **not** create strategies | **PASS** | STEP B surfaces: `auto_trade_forecast_closeout.py` (`creation_brief`, `CREATION_INPUT_PATH`), `auto_trade_expectancy_metrics.py` (`build_frequency_gap_report`), event/forecast modules. No call to `start_creation_task` / `pipeline_step_a`. Demand artifacts only. |
| STEP A only produces strategies; does **not** forge forecast numbers | **PASS** | `dual_engine_workflow_v2/pipeline_step_a.py` writes tasks, `mechanism_spec`, fidelity diffs, gate results, human-confirm pending. Does not write `system_forecast_latest.json` / positive-E buckets / calibration periods. |
| New strategies **not** counted in positive-E frequency until full Gates passed | **PASS (by construction; unexercised)** | Positive-E only over mounted `can_open` rows via `classify_expectancy_bucket` (`auto_trade_forecast_closeout.py`). STEP A keeps `live_enabled=false` / `auto_trade_eligible=false` until Gate7 human path; `STEP_A_gate_results.json`: `fully_passed_all_gates=0`. Nothing new entered the positive-E set. |
| New strategies **not** counted in production progress until really mounted | **PASS** | `gates.py` / `pipeline_step_a.py`: `production_mounted` stays false without human confirm + mount. Gate results: `production_mounted_count=0`. Formal daemons still the prior 5 mounted keys. |
| Mount → `strategy_pool_version` change → forecast auto-stale → statistical refresh → positive-E recalculated | **PASS (wired); NOT exercised by STEP A mounts** | `strategy_pool_version()` in `auto_trade_strategy_events.py`; `notify_pool_change` → `mark_forecast_stale` → `run_lightweight_statistical_refresh` → `apply_closeout_layers` (rebuilds positive-E) in `auto_trade_forecast_closeout.py`. Callers: `auto_trade_human_confirm_pipeline.py` (`_ensure_daemon_key` / confirm), `auto_trade_strategy_lifecycle.py`. Live pool hash `a51a90ee3c993ebe`, mounted_count=5; stale flag currently cleared. **No STEP A mount has fired this path.** |
| Only after-cost positive-E meeting credibility can shrink positive-E gap | **PASS (rule wired; gap unchanged)** | Buckets require after-cost point > near-zero, CI floor >0, sample≥10, credibility≥0.35, not AI-only, not 1 live trade. Live: calibrated_positive_E_weekly=**0**, gap_weekly_to_band=**+3.5**. Fillable≠positive-E. |

---

## 3. Link-by-link wiring matrix

Status key: **WIRED** = code + production call-chain present · **PARTIAL** = code present but live handoff incomplete / unexercised end-to-end · **NOT WIRED** = missing.

| # | Link | Status | Paths / evidence | Works today vs waits |
|---|------|--------|------------------|----------------------|
| L1 | positive-E gap computed | **WIRED** | `auto_trade_forecast_closeout.py` `build_positive_expectancy_frequency`; live `system_forecast_latest.json` → `calibrated_positive_E_weekly=0`, `positive_expectancy_frequency_gap.gap_weekly_to_band=3.5` | Works. Gap is entire target (no positive-E strategies). |
| L2 | gap → `creation_brief` (positive-E priority) | **WIRED** | Closeout `persist_strategy_creation_frequency_input` writes `qiyu_strategy_creation_frequency_input_v2` after enrichment + again after metrics; expectancy `build_frequency_gap_report` merges peg from `system_forecast_latest` (no bare-v1 clobber). **Live 2026-07-27 12:01** refresh: schema=v2, `priority=positive_expectancy_frequency_gap`, peg weekly=**3.5**, uncal/near/neg=**0.3/0.5/1.808**, `strategy_pool_version=a51a90ee3c993ebe`, `forecast_id=fc_2026-07-27_120120_a51a90ee3c99`. Metrics-only re-run kept v2. | Works on forecast refresh / stale refresh / metrics refresh. |
| L3 | brief → factory `collect_inputs` niches | **WIRED** | `auto_trade_dual_engine_factory.collect_inputs()` prefers create_in/gap brief with `prioritize_positive_expectancy_gap`; reads peg + contribution context + pool/forecast ids; sets `do_not_loosen_entries` / `do_not_force_open`. **Live proof:** `creation_priority=positive_expectancy_frequency_gap`, peg weekly=3.5, contributions 0.3/0.5/1.808. | Factory sees same positive-E priority as forecast UI. Still waits on STEP A gate/mount to shrink gap. |
| L4 | missing family/symbol/tf/freq → Mode A–D | **WIRED (A/B exercised)** | `pipeline_step_a.py` → `build_mode_context` / exploration modes; Mode A/B real-net tasks archived. C/D coded, not fully real-net this round. | Mode selection works; no strategy cleared Gate3+. |
| L5 | GLM immutable `mechanism_spec` | **WIRED** | `mechanism_spec.py`, Gate0; specs under mechanism_specs; overwrite-by-hash rejected. Real-net Gate0 passes recorded. | Works. |
| L6 | Codex faithful impl + fidelity_diff | **WIRED** | `fidelity_diff.py`, Gate1; real-net caught ema6 injection fail. | Works as veto; not yet a passing strategy. |
| L7 | multi-AI attack | **WIRED** | `attackers.py` + Gate6 path; reached only if prior gates pass. | Code ready; real-net batch stopped at Gate3. |
| L8 | Gate0–7 | **WIRED (partial execution)** | `gates.py`, `pipeline_step_a.py`. Real-net: Gate0–2 yes; Gate3 WF fail; Gate4–7 not reached on live tasks. | Framework live; **0** all-gate. |
| L9 | human confirm → candidate | **WIRED (unexercised)** | Gate7 pushes pending via `auto_trade_human_confirm_pipeline.ingest_and_screen`; `human_confirmed=False` always at push; no auto-mount. | Waits on a Gate0–6 passer. |
| L10 | production mount | **WIRED (unexercised)** | Confirm path mounts assignment + `_ensure_daemon_key`; STEP A never sets `production_mounted=True` itself. | **0** mounts from STEP A. |
| L11 | pool version change | **WIRED** | `auto_trade_strategy_events.strategy_pool_version()` hashes mounted keys + pause/auto_open/grade/size/SL. Live: `a51a90ee3c993ebe`, count=5. | Works for current pool; unchanged by STEP A. |
| L12 | forecast auto-stale on mount/pool change | **WIRED** | `notify_pool_change` / `mark_forecast_stale`; UI banner in `templates/forecast.html`; `load_latest_for_ui` mismatches version/mounted_count. | Works when mount/lifecycle fires; no STEP A mount yet. |
| L13 | statistical refresh | **WIRED** | `run_lightweight_statistical_refresh`; also `/api` path in `web_server.py`. Clears stale after rebuild. | Works (verified refresh @ 12:01 with v2 creation-input restamp). |
| L14 | positive-E recalculated after refresh | **WIRED** | Refresh → `apply_closeout_layers` rebuilds buckets + gap. | Works on refresh; still 0 positive-E until a credible after-cost winner is mounted long enough. |
| L15 | gap shrink only via credible positive-E | **WIRED** | Strict classifier; AI WR / uncalibrated / near-zero / negative cannot shrink positive-E gap. | Rule holds. Gap stays +3.5 until real positive-E frequency appears. |

**Overall loop status:** **PARTIAL closed-loop** — engineering spine present; **production proof of full traversal = FAIL** (blocked on STEP A gate passes + mount + time for credibility samples / calibration periods).

---

## 4. What works today vs what waits

### Works today
- STEP B metrics/forecast package live: event emit dual-write, source layers, portfolio math, strict positive-E fields, calibration stage honesty (`complete_periods=0`), stale-on-pool-change, lightweight refresh.
- STEP A creation entry routed (`creation_entry=v2|step_a` → `start_creation_task_step_a`); Gate0–3 real-net exercised; failure KB + fidelity veto proven.
- Boundary: B does not mint strategies; A does not mint forecast stats.
- Formal trading chain (open/SL/TP/close) not weakened; ADA 0.6% SL **not** migrated.

### Waits on time / strategies (not on missing “report scaffolding”)
1. ≥1 strategy through Gate3–7 + human confirm + real mount.
2. After mount: live samples / CI / credibility to enter `calibrated_positive_E` (not AI WR, not 1 trade).
3. Only then can positive-E weekly rise and gap (+3.5) shrink.
4. ≥7d full event taxonomy coverage before event-primary forecasts.
5. ≥4 complete calibration periods before leaving 观测期 / any scientific-validation claim.
6. Optional separate ADA 0.6→0.9 migration (H2); scripts exist, **not** run.
7. ~~Harden L2/L3 creation-input peg handoff~~ → **done 2026-07-27** (see §8 note). Remaining wait is still Gate/mount/credibility, not file schema.

---

## 5. Deploy / backup / rollback pointers

### STEP A
| Item | Path |
|------|------|
| Report | local + `/root/docs/STEP_A_strategy_creation_training_final_report.md` |
| Gate / fidelity / KB / prod evidence | `/root/docs/STEP_A_*.json` (+ local mirrors) |
| Code package | `/root/dual_engine_workflow_v2/` (`pipeline_step_a.py`, gates, mechanism_spec, …) |
| Backup | `/root/backups/step_a_strategy_creation_20260726_234045/` |
| Rollback | `/root/backups/step_a_strategy_creation_20260726_234045/ROLLBACK.sh` · helper `/root/docs/rollback_step_a_strategy_creation.sh` |
| Note | formal_daemon intentionally untouched on A rollback |

### STEP B
| Item | Path |
|------|------|
| Report | local + `/root/docs/STEP_B_metrics_forecast_final_report.md` |
| JSON audits | `/root/docs/STEP_B_{event_coverage_matrix,frequency_math_audit,calibration_status,stop_loss_matrix}.json` |
| Deployed modules | `auto_trade_strategy_events.py`, `auto_trade_forecast_closeout.py`, `auto_trade_expectancy_metrics.py`, `auto_trade_formal_v6_executor.py`, `templates/forecast.html` → `/root/` + `/root/auto_trade/` |
| Backup | `/root/backups/step_b_metrics_forecast_20260727_094552` (pointer file `…_LAST_BACKUP.txt`) |
| Rollback | `/root/docs/rollback_step_b_metrics_forecast.sh` |
| ADA SL scripts (not executed) | `/root/docs/step_b_ada_sl_migrate_0p6_to_0p9.sh` · `/root/docs/step_b_ada_sl_rollback_0p9_to_0p6.sh` |

### This integration report
| Item | Path |
|------|------|
| Local | `STEP_A_B_integration_report.md` (+ `docs/` copy) |
| Remote | `/root/docs/STEP_A_B_integration_report.md` |

### L2/L3 creation-input peg fix (2026-07-27)
| Item | Path |
|------|------|
| Backup | `/root/backups/l2_l3_creation_input_peg_20260727_115908` (pointer `/root/backups/L2_L3_CREATION_INPUT_PEG_LAST_BACKUP.txt`) |
| Rollback | `/root/backups/l2_l3_creation_input_peg_20260727_115908/ROLLBACK.sh` · helper `docs/rollback_l2_l3_creation_input_peg.sh` |
| Touched modules | `auto_trade_forecast_closeout.py`, `auto_trade_expectancy_metrics.py`, `auto_trade_dual_engine_factory.py`, `auto_trade_system_forecast.py` |

---

## 6. Explicit non-claims

- Frequency-gap **readable** ≠ frequency **filled**.
- Fillable ~2.35/wk ≠ positive-E.
- Uncalibrated / near-zero / AI WR ≠ positive-E.
- `complete_periods=0` ≠ scientifically validated forecast.
- STEP A mechanism hypotheses proposed ≠ completed independent strategies.
- Stale/refresh code present ≠ loop closed by a new STEP A mount.
- H1 PASS does not imply H2 PASS.

---

## 7. Final verdict

| Question | Answer |
|----------|--------|
| Is the intended A↔B closed loop proven in production end-to-end? | **No — FAIL as completed loop.** |
| Is the loop honestly deniable as “not wired”? | **No — spine is WIRED/PARTIAL in code and call-chains.** |
| What blocks closure? | **0 Gate-all strategies, 0 mounts, 0 calibrated positive-E frequency, 0 calibration periods.** |
| Safe operating statement | Run STEP A until a strategy clears gates + human mount; let STEP B refresh/reclassify; only then expect positive-E gap to move. Do not loosen entries to fake frequency. |

**Final sentence:** Integration plumbing exists and boundaries hold; **current results do not constitute a finished A+B closed loop** — they constitute a partial, honest wiring with an empty positive-E side and zero new mounts.

---

## 8. Patch note — L2/L3 positive-E creation-input handoff (2026-07-27)

**Root cause:** `compute_all_live_metrics()` → `build_frequency_gap_report()` rewrote `/root/auto_trade/strategy_creation_frequency_input.json` as bare **v1** *after* closeout had written **v2**, so factory/STEP A never saw `positive_expectancy_frequency_gap`.

**Fix (small, no trading-path change, ADA SL untouched):**
1. Closeout owns `persist_strategy_creation_frequency_input()` (v2: total gap + peg primary + uncal/near/neg context + `strategy_pool_version` / `forecast_id` / `generated_at`); re-stamps after metrics in lightweight refresh + full forecast.
2. Expectancy gap writer merges peg from `system_forecast_latest` so metrics-only refresh cannot clobber back to bare v1.
3. Factory `collect_inputs()` prefers positive-E brief/gap and hard-flags `do_not_loosen_entries` / `do_not_force_open`.

**Evidence (live after refresh @ 12:01; re-verified @ 12:45):** creation input schema=`qiyu_strategy_creation_frequency_input_v2`, peg weekly=3.5, uncal/near/neg=0.3/0.5/1.808, `creation_priority=positive_expectancy_frequency_gap`; factory `collect_inputs` reads same with `do_not_loosen_entries`/`do_not_force_open`; `compute_all_live_metrics()` re-run kept v2 (no bare-v1 clobber).

**Status:** L2 + L3 → **WIRED**. Full A↔B loop remains unclosed (0 all-gate / 0 mount / 0 calibrated positive-E).
