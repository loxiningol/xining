# Macro SFP Displacement — research features

Added for STEP A family `macro_sfp_displacement` (2026-07-30).

## Features (`backtest_engine_v2.precompute_indicators`)

| Feature | Definition | Lookahead |
|---------|------------|-----------|
| `pdh` | Prior UTC day high | `daily.shift(1)` then ffill onto LTF |
| `pdl` | Prior UTC day low | same |
| `pdc` | Prior UTC day close | same |
| `h4_high24` | Max high of prior 24 completed 4h bars | rolling on shifted 4h highs; outer `shift(1)` before LTF align |
| `h4_low24` | Min low of prior 24 completed 4h bars | same |

## DSL allowlist

`auto_trade_strategy_dsl.FEATURES` includes the five names above.

`ATR_TRAIL_N_MAX` raised **4.0 → 5.0** so Macro SFP can use 3.5–5.0× ATR_14 trails.

## Production sync

- Backup: `/root/backups/macro_sfp_displacement_20260730_021406`
- Synced `/root/backtest_engine_v2.py` and `/root/auto_trade_strategy_dsl.py`
- **Formal daemons not restarted** — live processes keep prior in-memory modules until a future planned restart

## Sandbox

DSL remains data-only over the FEATURES allowlist; no imports/IO from strategy JSON.
