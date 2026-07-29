# STRATEGY_CANDIDATE_V1 — STEP A submit prep note

Generated: 2026-07-29 (local workspace). **No auto-mount. Human must confirm CLI.**

## Failure KB (force-read)

**Prod source of truth:** `/root/auto_trade/dual_engine/workflow_v2/failure_knowledgebase.json`  
(SSH `root@64.176.47.192`, updated_at ≈ `2026-07-29 19:40:41`)

**Local mirrors:** no full `failure_knowledgebase.json` in workspace; status stubs only:
- `STEP_A_failure_knowledgebase_status.json`
- `docs/STEP_A_failure_knowledgebase_status.json`  
(stale vs prod — prod now has **15 blocked families**, not 1)

### Banned / blocked fingerprints to avoid

| Pattern | Notes |
|--------|--------|
| **Blocked families (15)** | `breakout_trap_reversal`, `liquidity_failed_breakout_{high,low}`, `mean_reversion_{vwap_deviation,rsi_extreme,zscore_dislocation}`, `time_structure_session_breakout`, `trend_continuation_thrust_retest`, `vol_regime_compression_release`, `absorption_climax_reclaim`, `selective_session_thrust`, `confirmed_vol_release`, `cascade_trap_proxy`, `vacuum_fill_impulse`, **`compression_release_structural_breakout`** |
| **Tiny-range / cost-floor high WR** | Phase1–2 template collapse onto OHLC+vol_z → payoff attractor; L1 rejects `sample_payoff_le_1.2` |
| **Fake / fixed micro-TP** | Hard ban fixed % TP &lt; 2%; protective **0.9% SL is not TP** |
| **Incomplete mechanism_spec** | Gate0 needs all **20** `MECHANISM_SPEC_FIELDS` |
| **Immutable overwrite** | Same `mechanism_id` different hash → archived |
| **Unknown DSL top-level fields** | `validate_strategy` rejects extras |
| **Unsupported features** | e.g. `vol_z20` alias mistakes; use allow-list only |
| **Exhaustion fade clone** | Forbidden in Mode A unless deep-dig |

Latest prod fail (`wsa_20260729_194021_5ad2`): ATR-squeeze structural breakout → L1 **`sample_payoff_le_1.2`** → family now KB-blocked. Do **not** resubmit that clone.

## Thesis (this candidate)

**Family:** `vol_accepted_donchian_h1_continuation` (new; not in blocked list)  
**Primary submit:** BTC-USDT-SWAP **15m** **long**  
**Matrix intent (not auto-run):** ETH/SOL/XAU + meme-class; TF 5m/15m/1h

**Edge:** Volume-accepted Donchian (prev_high20/low20) break + **h1_slope4** alignment = liquidity cascade continuation (not ATR-squeeze).  

**Exits (Phase-2):** `atr_trailing` **n_atr=3.5** (∈[2.5,4.0]) + `swing_extreme` lookback=16 invalidation.  
**SL:** protective **0.9%** engine/daemon only — no fixed % TP.

**File:** `strategy_candidate_v1.json` (20/20 mechanism_spec fields + `dsl` / `dsl_long` / `dsl_short`)

## Exact terminal commands

### Local dry-run Gate0 only (cheap — preferred first)

```bash
cd /Users/lele/Documents/Codex/2026-07-12/ru-g/work/intraday_live_v1_20260720
# prefer -m (reliable package imports); plain script path also bootstraps
python3 -m dual_engine_workflow_v2.pipeline_step_a \
  --strategy_json strategy_candidate_v1.json \
  --dry_run_gate0
```

### Full STEP A on production (human confirm — long / AI cost)

Copy JSON then run from `/root` (or set `VECTOR_ROOT=/root` and `PYTHONPATH=/root`):

```bash
# from laptop — copy pack
scp -i /Users/lele/Documents/Codex/2026-07-12/ru-g/work/ssh/vultr_codex_ed25519 \
  /Users/lele/Documents/Codex/2026-07-12/ru-g/work/intraday_live_v1_20260720/strategy_candidate_v1.json \
  root@64.176.47.192:/root/strategy_candidate_v1.json

# on prod — ALSO sync pipeline_step_a.py if --strategy_json not yet deployed
ssh -i /Users/lele/Documents/Codex/2026-07-12/ru-g/work/ssh/vultr_codex_ed25519 root@64.176.47.192
cd /root
python3 -m dual_engine_workflow_v2.pipeline_step_a \
  --strategy_json /root/strategy_candidate_v1.json \
  --symbol BTC-USDT-SWAP --timeframe 15m --direction long \
  --exploration_mode A \
  --windtalker_tag strategy_candidate_v1
```

**Fallback if prod CLI not updated yet** (existing API — no flag needed):

```python
# /root one-liner equivalent
import json
from dual_engine_workflow_v2.pipeline_step_a import run_creation_pipeline_step_a
pack = json.load(open("/root/strategy_candidate_v1.json"))
run_creation_pipeline_step_a(
    symbol="BTC-USDT-SWAP", timeframe="15m", exploration_mode="A",
    prebuilt_spec_pack=pack, windtalker_tag="strategy_candidate_v1",
)
```

Or reuse pattern in `scripts/submit_atr_squeeze_step_a.py` pointing at this JSON (do not reuse blocked family).

## What the pipeline will run (wired today)

| Stage | Wired? |
|-------|--------|
| Failure KB preread | Yes |
| Gate0 mechanism_spec (+ immutable save) | Yes |
| Fingerprint / duplicate / KB family block | Yes |
| Codex implement (uses prebuilt `dsl` if present) | Yes |
| Gate1 fidelity | Yes |
| Phase-3 **L1** micro-screen → **L2** fitness/Pareto → **L3** null/WF | Yes |
| Gate2/3 (fitness + WF ≥7/10) | Yes |
| Phase-4 **incubator** (only if L1–L3 pass) | Yes |
| Gate4 split-20 / Gate5 MC | Yes |
| **Gate6 multi-AI** review | Yes (AI cost) |
| Gate7 human confirm pending | Yes — `production_mounted=False` until separate `--confirm` CLI |
| Auto live mount | **No** |

## Reminders

1. **Do not** auto-run full STEP A on prod unless you accept long runtime + AI spend.  
2. **No auto-mount**; Gate7 waits for human confirm.  
3. Do not touch ADA SL / weaken 0.9% SL chain.  
4. CLI `--strategy_json` is **additive** local support in `dual_engine_workflow_v2/pipeline_step_a.py` — deploy that file to prod before relying on the flag.
