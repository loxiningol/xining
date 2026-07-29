# PHASE4_INCUBATOR_RISK_EXECUTION_REPORT

- **Date**: 2026-07-29
- **Scope**: Local workspace `intraday_live_v1_20260720`
- **Deploy**: **Not deployed to production** (local-first; live mounts / formal daemons untouched)

---

## 1. Summary

Phase 4 adds a **multi-environment incubator**, **ATR dynamic risk sizing** (research/backtest only), and a **pending human-confirm queue** with enriched Wx cards — on top of Phase 3 L1/L2/L3 funnel.

| Part | Role |
|---|---|
| **A Incubator** | Cross-asset blind test + multi-TF / window ±20% perturbation |
| **B Dynamic R** | `Position Size = (Equity * Risk_Pct) / (ATR_14 * Target_Multiplier)`; DD throttle to 0.5% |
| **C Confirm queue** | After Gates0–7 + L1–L3 + Incubator → `strategy_pending_human_confirm.json`; `production_mounted=False` until CLI `--confirm` |

Protective **0.9% SL** unchanged; **no ADA migrate**; **no auto-mount**; **no live formal daemon restart**. Live mount sizing remains **B-grade 30% / 20x / 0.9% SL** via human `--confirm` only.

---

## 2. Entry points

| Component | Location |
|---|---|
| Incubator | `dual_engine_workflow_v2/incubator.py` |
| Pipeline wire | `pipeline_step_a.py` after L1–L3 pass |
| ATR sizing | `auto_trade_strategy_dsl.py` (`atr_position_size`, `backtest_dsl(dynamic_risk_sizing=…)`) |
| Engine sizing | `backtest_engine_v2.py` `run_backtest(dynamic_risk_sizing=…)` |
| IR compiler | `windtalker_phase4_deploy/candidate_ir_compiler.py` → `research_risk_sizing` |
| Pending + Wx | `auto_trade_human_confirm_pipeline.py` `enqueue_for_human` |
| Creation factory | `auto_trade_strategy_creation_factory.py` `screen_and_push` |

---

## 3. Part A — Incubator hard gates

### 3.1 Cross-asset
- Default non-target symbols: `ETH-USDT-SWAP`, `SOL-USDT-SWAP`, `BNB-USDT-SWAP`
- Frame loader: local dual-engine / human-confirm parquet paths (fail-closed if &lt;2 frames available)
- **PASS** requires Expectancy &gt; 0 **AND** Calmar ≥ 0.8 on **≥2** non-target symbols
- Else reject with `cross_asset_single_symbol_overfit`

### 3.2 Multi-TF / window perturbation
- 15m → try 5m & 1h when frames exist
- Always perturb ATR/lookback windows ±20% on baseline frame
- **REJECT** if Calmar cliff-drops **&gt;40%** vs baseline

---

## 4. Part B — Dynamic volatility sizing

```
Position Size = (Equity * Risk_Pct) / (ATR_14 * Target_Multiplier)
```

| Knob | Default |
|---|---|
| Risk_Pct | 1.2% (band 1.0%–1.5%) |
| ATR period | 14 |
| Target multiplier | 1.0 |
| DD throttle | when current DD ≥ 50% of peak-to-trough max DD → R = **0.5%** |

**Production mount interaction (explicit):**
- Research/incubator BT may enable `dynamic_risk_sizing=True`
- CLI `--confirm` path still writes **B / 30% / 20x / stop_loss_pct=0.009**
- `production_mount_sizing.dynamic_r_applies_to_live = False` — do not silently change live risk

IR compiler emits `research_risk_sizing` metadata on bridge formals; legacy IRs get a default fill (non-breaking).

---

## 5. Part C — Pending confirm + Wx + safe mount gate

After Gates0–6 + L1–L3 + Incubator pass → Gate7 enqueues pending item with:
- `production_mounted=False`
- Wx card fields: **Calmar**, **payoff**, **cross-asset score**, **mean MAE**
- Confirm command unchanged: `python3 auto_trade_human_confirm_pipeline.py --confirm KEY`
- Only `--confirm` grants B-grade 30% live assignment

---

## 6. Test results

```text
python3 -m unittest test_phase4_incubator_risk test_phase3_funnel_null_hypothesis -v
→ Ran 29 tests — OK
```

Proven:
- Incubator rejects single-symbol overfit (&lt;2 cross-asset passes)
- Incubator accepts when ≥2 symbols pass Calmar/expectancy
- Multi-TF window cliff-drop &gt;40% rejects; stable Calmar passes
- ATR sizing: higher ATR → lower notional / account_fraction
- DD throttle: R cuts to 0.5%; lowers notional vs base R
- Protective SL / B-grade constants remain 0.009 / 0.30 / 20x

---

## 7. Production impact

| Item | Status |
|---|---|
| Live strategy mounts | **None changed** |
| Formal daemons | **Not restarted** |
| Auto-mount | **Disabled** |
| ADA migrate | **Not touched** |
| Deploy to prod host | **Deferred** — local-first + GitHub |

---

## 8. Rollback

1. Revert `incubator.py` + pipeline Gate2/3 incubator block + Gate7 card enrichment
2. Revert `dynamic_risk_sizing` kwargs (defaults keep legacy full-size BT)
3. Confirm path unchanged historically — no live risk migration to undo

---

## 9. files_modified

- `dual_engine_workflow_v2/incubator.py` (**NEW**)
- `dual_engine_workflow_v2/pipeline_step_a.py`
- `dual_engine_workflow_v2/step_a_config.py`
- `auto_trade_strategy_dsl.py`
- `backtest_engine_v2.py`
- `windtalker_phase4_deploy/candidate_ir_compiler.py`
- `auto_trade_human_confirm_pipeline.py`
- `auto_trade_strategy_creation_factory.py`
- `test_phase4_incubator_risk.py` (**NEW**)
- `PHASE4_INCUBATOR_RISK_EXECUTION_REPORT.md` (this file)
