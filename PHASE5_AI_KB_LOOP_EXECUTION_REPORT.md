# PHASE5_AI_KB_LOOP_EXECUTION_REPORT

- **Date**: 2026-07-29
- **Scope**: Local workspace `intraday_live_v1_20260720`
- **Deploy**: **Not deployed to production** (local-first; live mounts / formal daemons untouched)

---

## 1. Summary

Phase 5 adds a **3-party AI unanimous dimensional review** (Qwen+DeepSeek+GLM), a **Failure KB self-learning feedback loop**, and **silent drop / Wx routing isolation** — on top of Phase 4 incubator + Phase 3 funnel.

| Part | Role |
|---|---|
| **A 3-Party Consensus** | Causal (DeepSeek) + Game/Friction (Qwen) + Production Risk (GLM); unanimous APPROVE + exact candidate_hash required; any Fatal flag → immediate REJECT |
| **B Failure KB Loop** | Auto-extract fingerprint at every pipeline rejection (Gates 0-7 / L1-L3 / Incubator / 3-Party); incremental KB update; mandatory pre-read in creation prompts |
| **C Silent Drop + Wx** | Rejected candidates: silent (no WxPusher), only write to KB. Approved candidates: enqueue + Wx rich card as before |

Protective **0.9% SL** unchanged; **no ADA migrate**; **no auto-mount**; **no live formal daemon restart**. `production_mounted=False` until CLI `--confirm`.

---

## 2. Entry points

| Component | Location |
|---|---|
| Phase 5 consensus | `auto_trade_ai_consensus.py` (`phase5_unanimous_review`, `phase5_review_one`) |
| Formal 4D bridge | `dual_engine_workflow_v2/formal_4d.py` (`run_phase5_consensus`) |
| Pipeline wire | `pipeline_step_a.py` — after Gate6 pass, before Gate7 |
| Failure KB auto-record | `dual_engine_workflow_v2/failure_kb.py` (`record_pipeline_rejection`) |
| KB pre-read enhanced | `failure_kb.py` (`kb_context_for_ai` — now includes `recent_failure_fingerprints`) |
| Factory KB injection | `auto_trade_strategy_creation_factory.py` (`build_factory_context` → `failure_kb_must_read`) |
| Silent drop | `auto_trade_strategy_creation_factory.py` (`screen_and_push` — rejected → KB only, no Wx) |

---

## 3. Part A — 3-Party AI Unanimous Dimensional Review

### 3.1 Dimensions
- **DeepSeek**: `causal_logic` — causal chain coherence, falsifiability, overfitting independence
- **Qwen**: `market_game_friction` — adversary adaptation, crowding, friction coverage, capacity
- **GLM**: `production_risk` — execution chain, latency, slippage, partial fill, 20x leverage survival

### 3.2 Rules
- All 3 providers must return `decision=APPROVE` with exact `candidate_hash` match
- Any provider returning `fatal_risk=true` → entire review REJECT (even if decision=APPROVE)
- Missing consent (`ai_research_consent.json`) or API key → fail-closed REJECT before any network call
- Each provider runs independently in parallel (no cross-visibility)

### 3.3 Pipeline position
After Gate6 (multi-AI separate reviews) pass → Phase 5 consensus → Gate7 (human confirm queue)

---

## 4. Part B — Failure KB Self-Learning Feedback Loop

### 4.1 Auto-fingerprint extraction
`record_pipeline_rejection()` called at every rejection point:
- Gate0 (mechanism spec incomplete)
- Gate1 (fidelity fail)
- Funnel L1 (micro-screen cull)
- Phase4 incubator (overfit / cliff-drop)
- Gate6 (multi-AI review fail)
- Phase5 consensus (3-party reject)
- Creation factory screen (safety/lookahead/death)

Each record includes:
- `failed_tests` list
- `reject_reasons` list
- `mechanism_fingerprint` (DSL hash + key + direction + timeframe)
- `blocked_paths` (stage|test compound keys)
- Auto-incremental write to `failure_knowledgebase.json`

### 4.2 Creation-time negative feedback injection
`kb_context_for_ai()` now includes:
- `recent_failure_fingerprints` (last 10 fingerprints with stage + DSL hash)
- Enhanced instruction: "Phase5: also avoid patterns matching recent_failure_fingerprints"
- Injected into `build_factory_context()` as `failure_kb_must_read`

### 4.3 Blocked path intercept
`path_is_blocked()` checks compound keys (e.g. `funnel_l1|micro_screen`) against KB `blocked_paths`; new pipeline rejections auto-populate this list.

---

## 5. Part C — Silent Drop & Wx Routing Isolation

### 5.1 Approved route
Gates 0-7 + L1-L3 + Incubator + 3-Party Consensus all pass → `strategy_pending_human_confirm.json` → WxPusher rich card (Calmar, payoff, cross-asset score, mean MAE)

### 5.2 Rejected silent route
Any rejection at any stage → **no WxPusher** notification → only write failure fingerprint to KB → delete temporary files silently

In `screen_and_push()`: when `out.ok=False`, call `record_pipeline_rejection()` and do not call `_wx()`.

---

## 6. Test results

```text
python3 -m unittest test_phase5_ai_kb_loop test_phase4_incubator_risk test_phase3_funnel_null_hypothesis -v
→ Ran 39 tests — OK
```

Proven:
- 3-party unanimous approve requires all 3 APPROVE + exact hash + no fatal
- Single REJECT from any provider blocks entire consensus
- Fatal flag on any provider overrides APPROVE decision
- Missing consent/key → fail-closed (no network call)
- Pipeline rejection auto-writes to Failure KB with fingerprint
- KB context includes recent failure fingerprints for creation pre-read
- Blocked path intercept works on compound stage|test keys
- Creation factory context includes `failure_kb_must_read`
- Rejected candidates do not trigger WxPusher
- Code version updated to `phase5_ai_kb_loop`
- Protective SL / B-grade constants remain 0.009 / 0.30 / 20x

---

## 7. Production impact

| Item | Status |
|---|---|
| Live strategy mounts | **None changed** |
| Formal daemons | **Not restarted** |
| Auto-mount | **Disabled** |
| ADA migrate | **Not touched** |
| Deploy to prod host | **Deferred** — local-first |

---

## 8. Rollback

1. Revert `phase5_unanimous_review` + `phase5_review_one` in `auto_trade_ai_consensus.py`
2. Revert `run_phase5_consensus` in `formal_4d.py`
3. Revert `record_pipeline_rejection` calls in `pipeline_step_a.py`
4. Revert `kb_context_for_ai` to Phase 4 version (remove `recent_failure_fingerprints`)
5. Revert `failure_kb_must_read` injection in `auto_trade_strategy_creation_factory.py`
6. Revert `step_a_config.py` code version to `phase4_incubator`

---

## 9. files_modified

- `auto_trade_ai_consensus.py` (Phase 5 consensus functions added)
- `dual_engine_workflow_v2/formal_4d.py` (`run_phase5_consensus` added)
- `dual_engine_workflow_v2/failure_kb.py` (`record_pipeline_rejection`, enhanced `kb_context_for_ai`)
- `dual_engine_workflow_v2/pipeline_step_a.py` (Phase 5 gate wired; KB auto-record at rejections)
- `dual_engine_workflow_v2/step_a_config.py` (code version → `phase5_ai_kb_loop`)
- `auto_trade_strategy_creation_factory.py` (KB injection in context; silent drop in `screen_and_push`)
- `test_phase5_ai_kb_loop.py` (**NEW**)
- `PHASE5_AI_KB_LOOP_EXECUTION_REPORT.md` (this file)
