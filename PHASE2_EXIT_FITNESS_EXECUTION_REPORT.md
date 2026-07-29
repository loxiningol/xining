# PHASE2_EXIT_FITNESS_EXECUTION_REPORT

- **Date**: 2026-07-29
- **Scope**: Local workspace `intraday_live_v1_20260720`
- **Deploy**: **Not deployed to production** (local-first; live mounts untouched)

---

## 1. Summary

Phase 2 abolishes **fixed tiny take-profit** (e.g. 0.6% / 0.9% / 1% / 1.3% price TP) in research/formal DSL + Candidate IR compile paths, adds **opt-in** `atr_trailing` (N×ATR_14, N∈[2.5,4.0]) and `swing_extreme` exits, and hardens Gate2/Gate3 with a **multi-objective fitness engine** (Calmar / payoff / expectancy-factor / worst-5 losses / MAE demotion / remove-max-win).

**Protective production 0.9% stop-loss chain is unchanged** (20x, 30% size, open→SL→TP/close). This task does **not** remount live strategies and does **not** migrate ADA SL.

---

## 2. What changed

| File | Change |
|---|---|
| `auto_trade_strategy_dsl.py` | `exit_op` leaves: `atr_trailing`, `swing_extreme`, `fixed_pct_tp`; hard refuse tiny fixed TP (`pct < 2.0%`); runtime eval + MAE tracking in `backtest_dsl`; default SL param remains `0.009` |
| `dual_engine_workflow_v2/fitness_engine.py` | **NEW** multi-objective fitness + PreGate helper |
| `dual_engine_workflow_v2/gates.py` | Gate2 enforces fitness; Gate3 enforces fitness when OOS trades provided |
| `dual_engine_workflow_v2/pipeline_step_a.py` | Default Codex implement exits → ATR trail + swing invalidation; Gate3 wired with trades |
| `windtalker_phase4_deploy/candidate_ir_compiler.py` | Fail-closed ban on fixed tiny TP; opt-in ATR/swing wiring; compile ban flags; version suffix `_phase2_exits` |
| `backtest_engine_v2.py` | DSL exit factory supports structured exits + MAE on trades |
| `test_phase2_exit_fitness.py` | **NEW** unit/integration tests |

---

## 3. Hard bans (no silent convert)

- Any `exit_op: fixed_pct_tp` with price move **&lt; 2.0%** → `DSLValidationError` / `CompilerError` with message containing `REFUSED` and `Does not silently convert` / `Does NOT silently convert`.
- Known param aliases scanned in compiler exit blobs: `price_take_profit_pct`, `take_profit_price_ratio`, `take_profit_pct`, `tp_pct`, etc.
- IR `tp` / `trailing` with `type=fixed_pct` and tiny pct → compile FAIL CLOSED.
- **Not banned**: protective `production_sl_0_9pct` / `stop_loss_pct=0.009`.
- Legacy hard-coded backtest strategy params with tiny `take_profit_price_ratio` for **already-mounted live families** were **not** force-migrated (avoid breaking production). New research/formal DSL + IR paths refuse them.

---

## 4. Structured exits (opt-in)

### `atr_trailing`
- Trail long: `peak_high_since_entry − N×ATR_14`; exit when `low ≤ trail`
- Trail short: `peak_low_since_entry + N×ATR_14`; exit when `high ≥ trail`
- `n_atr ∈ [2.5, 4.0]`, `atr_period` default 14

### `swing_extreme`
- Lookback last N bars (5–60) excluding current
- `role=take_profit`: long → swing high; short → swing low
- `role=invalidation`: long → swing low; short → swing high

Design target (research guidance): average price move ~2%–5%+ (equity ROE context at 20x×30% noted in docs); fitness requires payoff ≥ 2.5.

Fail-closed: evaluating `exit_op` without position context → not passed (no false exit).

---

## 5. Fitness formulas

| Metric | Formula / rule | Threshold |
|---|---|---|
| **Calmar** | `((1+R)^(365.25/span_days)−1) / max_DD` where R is compound equity return; `span_days` from first entry→last exit timestamps, else `n_trades` as days proxy | ≥ 1.5 |
| **Payoff** | `avg_win / avg_loss_magnitude` | ≥ 2.5 |
| **Expectancy factor** (user) | `win_rate × payoff` | ≥ 1.0 |
| **Classic expectancy** | `mean(pnl_ratio)` | reported only |
| **Worst-5 losses** | sum of 5 largest \|losses\| / total \|loss\| sum | ≤ 40% |
| **MAE demotion** | if any trade `mae_price_pct > 2.0 × avg_win_price` → `dead_hold_to_BE` reject (skipped if no MAE fields) | hard when MAE present |
| **Remove-max-win** | drop largest win; if Calmar or Sharpe relative drop &gt; 50% → `lottery_overfitting` (near-zero-DD uses capped/floor-safe mode) | reject |

Wired into:
- `evaluate_gate2` (always enforce)
- `evaluate_gate3(..., trades=...)` when trades provided (STEP A pipeline now passes them)
- `evaluate_pregate_fitness` for Windtalker/formal PreGate callers

---

## 6. Test results

```text
python3 -m unittest test_phase2_exit_fitness test_near_duplicate_logic -v
→ Ran 20 tests — OK

python3 tests_workflow_v2/test_step_a_acceptance.py
→ ALL_STEP_A_UNIT_TESTS_PASSED
```

Proven:
- Fitness **rejects** pseudo high-WR low-payoff (90% WR, tiny wins / large loss)
- Fitness **passes** synthetic series with payoff ≥ 2.5 and expectancy factor ≥ 1.0
- MAE dead-hold and lottery (remove-max-win) demotions fire
- DSL/compiler refuse fixed 1% TP without conversion
- Protective SL default `0.009` still on `backtest_dsl`

---

## 7. Production impact

| Item | Status |
|---|---|
| Live strategy mounts | **None changed** |
| Daemon / auto-mount | **Not touched** |
| Protective 0.9% SL | **Preserved** |
| DSL sandbox (no import/IO/network) | **Preserved** — only data-plane exit_op leaves added |
| Existing live tiny-TP hard-coded families | **Not force-remounted / not migrated** |
| Deploy to `64.176.47.192` | **Deferred** — local-first; deploy only with `/root/backups/phase2_exit_fitness_*` if explicitly approved |

---

## 8. Rollback notes

1. Revert the listed files (or restore from git / backup tarball).
2. Fitness hardening is in `gates.py` + `fitness_engine.py` — rolling those back restores prior Gate2 soft sample-only pass.
3. DSL `exit_op` is additive; strategies without `exit_op` behave as before (boolean indicator exits).
4. Candidate IR compiler version string changes to `..._v1_phase2_exits`; rollback restores prior compiler module.
5. If deployed: restore from `/root/backups/phase2_exit_fitness_*` then restart only research/creation workers — **do not** restart live trade daemons unless verified.

---

## 9. files_modified

- `auto_trade_strategy_dsl.py`
- `dual_engine_workflow_v2/fitness_engine.py` (new)
- `dual_engine_workflow_v2/gates.py`
- `dual_engine_workflow_v2/pipeline_step_a.py`
- `windtalker_phase4_deploy/candidate_ir_compiler.py`
- `backtest_engine_v2.py`
- `test_phase2_exit_fitness.py` (new)
- `PHASE2_EXIT_FITNESS_EXECUTION_REPORT.md` (this file)
