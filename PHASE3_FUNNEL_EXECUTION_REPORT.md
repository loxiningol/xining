# PHASE3_FUNNEL_EXECUTION_REPORT

- **Date**: 2026-07-29
- **Scope**: Local workspace `intraday_live_v1_20260720`
- **Deploy**: **Not deployed to production** (local-first; live mounts / formal daemons untouched)

---

## 1. Summary

Phase 3 adds a **three-level research funnel** on top of Phase 2 fitness:

| Level | Stage id | Role |
|---|---|---|
| **L1** | `funnel_l1_micro_screen` | After DSL validate, **before** full-history BT — sample 10–20% market slices; hard-cull garbage |
| **L2** | `funnel_l2_full_bt_pareto` | Full BT + Phase-2 multi-objective fitness + Pareto front (Calmar, payoff, trade frequency) |
| **L3** | `funnel_l3_null_hypothesis` | Permuted returns + inverted market + WF ≥7/10 (Calmar≥1.0 ∧ positive expectancy) |

Fail-closed logging records `task["phase3_funnel"]` with `rejected_at` + `reject_reasons`. Gate0–7 public signatures preserved (`evaluate_gate3` gains optional `null_hypothesis=` only). Protective **0.9% SL** unchanged; **no ADA migrate**; **no auto-mount**; **no live daemon restart**.

---

## 2. Entry points located (pre-implement)

| Component | Location | Notes |
|---|---|---|
| Gate2 | `dual_engine_workflow_v2/gates.py::evaluate_gate2` | Phase-2 fitness hard gates |
| Gate3 | `gates.py::evaluate_gate3` | WF ≥7/10 + optional OOS fitness |
| WF builder | `pipeline_step_a.py::_walk_forward_detail` | Was mean_net>0 chunks; now Phase-3 Calmar+expectancy |
| Full BT | `auto_trade_dual_engine_factory._backtest` / `_frame` | Unchanged API |
| Fitness | `dual_engine_workflow_v2/fitness_engine.py` | Reused by L2 / Gate2 |

---

## 3. What changed

| File | Change |
|---|---|
| `dual_engine_workflow_v2/funnel_l1_micro_screen.py` | **NEW** L1 micro-screen |
| `dual_engine_workflow_v2/funnel_l2_pareto.py` | **NEW** L2 fitness objectives + Pareto |
| `dual_engine_workflow_v2/funnel_l3_null_hypothesis.py` | **NEW** perm / invert / WF |
| `dual_engine_workflow_v2/phase3_funnel.py` | **NEW** L1→L2→L3 orchestrator |
| `dual_engine_workflow_v2/pipeline_step_a.py` | Wire L1 before full BT; L2/L3 around Gate2/3; WF uses Phase-3 rule |
| `dual_engine_workflow_v2/gates.py` | Additive `null_hypothesis=` on Gate3 |
| `dual_engine_workflow_v2/step_a_config.py` | Code version → `..._20260729_phase3_funnel` |
| `auto_trade_strategy_creation_factory.py` | Additive Pareto filter in `screen_and_push` when metrics present |
| `test_phase3_funnel_null_hypothesis.py` | **NEW** unit tests |
| `PHASE3_FUNNEL_EXECUTION_REPORT.md` | This file |
| `backups/phase3_funnel_*.tgz` | Local backup tarball |

---

## 4. Level 1 — Micro-screen

**Timing**: after `validate_strategy`, before `_walk_forward_detail` full BT.

**Sampling**: 10–20% contiguous random slices (`sample_frame_slices`), default fraction 0.15, ≥400 bars/slice when frame available.

**Hard reject**:
- sample filled entries **< 5**
- sample payoff **≤ 1.2**
- severe MAE: any `mae_price_pct` **> 2.5 × avg_win** (price space; leverage-adjusted)

**Measured wall time** (trade-list / in-memory path on this host):

| Path | Wall time | Target |
|---|---|---|
| `run_micro_screen(trades=...)` | **~0.045 ms** | < 100 ms |

Full-frame sample BT wall time depends on market data availability and is recorded in `wall_time_ms` / `under_target` on each L1 result.

**Cull rate** (100 garbage fixtures: few-fills / low-payoff / severe-MAE): **100%** (≥90% goal met).

---

## 5. Level 2 — Full BT + Pareto

- Full history via existing dual-engine `_backtest`
- Phase-2 fitness: Calmar≥1.5, payoff≥2.5, WR×payoff≥1.0, MAE demotion, loss concentration, remove-max-win
- Pareto objectives **maximize**: `(calmar, payoff_ratio, trade_frequency)`
- Keep **non-dominated** fitness-passing candidates only
- STEP A single-candidate path: singleton is on-front iff fitness passes
- Creation factory `screen_and_push`: batch Pareto when candidates carry calmar/payoff metrics

---

## 6. Level 3 — Null hypothesis + WF

### 6.1 Permuted price/returns
- Shuffle close log-returns; rebuild OHLC (range-scaled)
- **Reject if** `Sharpe(permuted_BT) ≥ 0.5` → coincidence fit

### 6.2 Inverted market — exact reject rule
Reflect OHLC about first close; ensure high≥low and positive prices.

**REJECT when inverted backtest yields ALL of:**
1. `n_trades >= 5`
2. `sharpe >= 0.5`
3. `classic_expectancy (mean pnl) > 0`

Rationale: a true directional mechanism should lose edge under price mirror; retaining Sharpe≥0.5 with positive expectancy implies path-agnostic / spurious signal.

### 6.3 Walk-Forward
- 10 windows on trade sequence
- Window pass = **Calmar ≥ 1.0 AND classic expectancy > 0**
- Need **≥ 7/10** (`WF_WINDOW_PASS_REQUIREMENT`)
- Wired into `_walk_forward_detail` so Gate3 consumes the same windows without signature break
- Gate3 also fail-closes when `null_hypothesis` payload is provided and `pass=False`

---

## 7. Integration / non-breaks

| Constraint | Status |
|---|---|
| Gate0–7 API signatures | Preserved (`null_hypothesis` optional kw-only additive) |
| Protective 0.9% SL | Preserved (`stop_loss_pct=0.009`) |
| ADA migrate | Disabled / not touched |
| Auto-mount | Not enabled |
| Live formal daemons | **Not restarted** |
| Fail-closed reject log | `task["phase3_funnel"].rejected_at` + reasons |

Pipeline stage names added: `funnel_l1_micro_screen` (archive on L1 cull).

---

## 8. Test results

```text
python3 -m unittest test_phase2_exit_fitness test_phase3_funnel_null_hypothesis -v
→ Ran 36 tests — OK

python3 tests_workflow_v2/test_step_a_acceptance.py
→ ALL_STEP_A_UNIT_TESTS_PASSED
```

Proven:
- L1 cull ≥90% garbage; wall time ≪ 0.1s on in-memory path
- L2 Pareto drops dominated + fitness-failed candidates
- L3 permuted Sharpe≥0.5 reject catches coincidence-fit fixture
- L3 inverted spurious-edge reject fires; dead inverted edge passes
- WF 7/10 Calmar+expectancy rule pass/fail
- Gate3 additive NH fail-closed; protective SL default still 0.009

---

## 9. Production impact

| Item | Status |
|---|---|
| Live strategy mounts | **None changed** |
| Daemon / auto-mount | **Not touched** |
| Deploy to `64.176.47.192` | **Deferred** — local-first |
| `/root/docs/` sync | Optional later; not done |

---

## 10. Rollback

1. Restore from `backups/phase3_funnel_*.tgz` or revert listed files.
2. Or set pipeline to skip by reverting `pipeline_step_a.py` Gate2+3 block to Phase-2-only path.
3. Gate3 without `null_hypothesis=` behaves as Phase-2 (windows + fitness only).
4. If ever deployed: restore backup then restart **research/creation workers only** — do **not** restart live trade daemons unless verified.

---

## 11. files_modified

- `dual_engine_workflow_v2/funnel_l1_micro_screen.py` (new)
- `dual_engine_workflow_v2/funnel_l2_pareto.py` (new)
- `dual_engine_workflow_v2/funnel_l3_null_hypothesis.py` (new)
- `dual_engine_workflow_v2/phase3_funnel.py` (new)
- `dual_engine_workflow_v2/pipeline_step_a.py`
- `dual_engine_workflow_v2/gates.py`
- `dual_engine_workflow_v2/step_a_config.py`
- `auto_trade_strategy_creation_factory.py`
- `test_phase3_funnel_null_hypothesis.py` (new)
- `PHASE3_FUNNEL_EXECUTION_REPORT.md` (this file)
- `backups/phase3_funnel_20260729_*.tgz`
