# Field Audit — Running Strategies / Expectancy / Frequency Forecast
Date: 2026-07-26 · Production: 64.176.47.192 · Backup: `/root/backups/metrics_forecast_refactor_20260726_183126`

## §一 Live inventory (verified)

| # | Symbol | TF | Strategy key | auto_open | grade (controls) | AI WR | Rating ER | Rating WR | Live fills |
|---|--------|----|--------------|-----------|------------------|-------|-----------|-----------|------------|
| 1 | ADA | 5m | `codex0725t3_ada5m_trendpb_r42_z2p3_h14` | Y | B | 74.3 | **0.0 (missing)** | 50.0 placeholder | 0 |
| 2 | LTC | 5m | `ltc5_exhaustion_fade_short_ai` | Y | B | 60.5 | 5.654 | 73.45 | 1 |
| 3 | NG | 5m | `ng5_exhaustion_fade_short_ai` | Y | B | 60.0 | 3.452 | 63.49 | 0 |
| 4 | XRP | 15m | `frost_xrp_rescue_h20_t45` | Y | B | 79.0 | **0.0 (missing)** | 50.0 placeholder | 0 |
| 5 | BTC | 1h | `frost3_btc1h_xrpport_exhaustion_fade_slope` | Y | B | 76.0 | **0.0 (missing)** | 50.0 placeholder | 0 |

**Actual live mounted count = 5** (ADA/LTC/NG/XRP + newly confirmed BTC1h). Forecast snapshot from 2026-07-25 still shows **3** (pre-XRP/BTC1h).

Leverage on all enabled configs: **20x**. B-grade `max_position_ratio`: **0.30**. Fixed SL **0.9%** (protected).

## §二 Field provenance map

| UI / API field | Source today | Problem |
|----------------|--------------|---------|
| 预期胜率 | Frontend uses `ai_theoretical_wr_avg` only | Ignores rating/backtest/live; missing → bare `-` |
| 预期盈利率 | Live `actual_single_trade_pnl_pct` if any else rating `expected_return_per_trade_pct` | Mixes n=1 live margin-ROI with backtest prior; units unlabeled |
| `expected_win_rate_pct` | `auto_trade_strategy_rating.refresh` Bayesian blend | Defaults to 50 when `backtest_trades=0` — looks real but is placeholder |
| `expected_return_per_trade_pct` | Same module; prior from `live_portfolio_frequency.accepted[].pnl_ratio` | `pnl_ratio` is **margin ROI**, not price return; costs not subtracted |
| weekly/daily expected opens | AI GLM numbers (fallback local 7d) | AI free estimates used as primary numbers |
| `forecast_contribution` | Not a persisted field; narrative only in `weekly_contributors_zh` | Opaque |
| enabled/paused counts | Forecast snapshot from daemon configs + runtime controls | Correct when refreshed; stale after new mounts |
| Expand `0/10` | `近10组开平仓记录（n/10）` | Read as score; means “n of 10 closed trade groups available” |

## §三 Answers

### 1. Why ADA / XRP (and BTC1h) missing expectancy?
- Not in `live_portfolio_frequency.json` accepted baselines → `backtest_trades=0`.
- Rating prior collapses to `wr=50`, `er=0.0`.
- `web_server._vector_pick_expected_return_per_trade` treats `er=0` with zero samples as **missing** → UI `-`.
- Frontend 预期胜率 reads AI WR; ADA/XRP/BTC **do** have AI WR in controls, but older bugs / wrong key paths caused `-` for EMA6-era cards. Current ADA assignment has `ai_theoretical_wr_avg=74.333` — if UI still shows `-`, API path for that zone key is broken or stale page cache. Audit: rating path still produces empty calibrated expectancy until baseline or live fills exist.

### 2. LTC 16.21% vs NG 3.45% — consistent?
**No.** Different estimators:
- LTC card prefers **one live fill** margin ROI `+16.21%` (= net_pnl / initial_margin).
- NG has **0 live fills**, falls back to rating prior ≈ mean(backtest `pnl_ratio`)*100 ≈ **3.45%**.
- Same label “预期盈利率”, different sample basis and no cost/net/equity breakdown. With uniform math: margin ROI ÷ 20 = price return; equity = price × 0.30 × 20.

### 3. Leverage / size / fees in expectancy?
- **Not today.** Rating expectancy ignores fees/slippage/funding.
- Live ROI includes exchange fees in net_pnl (good for realized) but backtest prior does not apply `execution_cost_model.json` drag.
- Size 30% and 20x are **not** converted to equity expectancy on the card.

### 4. Why XRP missing from last forecast?
- Forecast generated **2026-07-25 16:59** when snapshot only had ADA/LTC/NG.
- XRP + BTC1h mounted later; `system_forecast_latest.json` not refreshed. `list_auto_trade_strategies()` would include them on next run.

### 5. What does `0/10` mean?
- Not a grade. Literal: **0 of 10 recent closed trade groups** in the expand dropdown. Misleading status copy.

### 6. 4 vs 3 strategies?
- UI/roster follows enabled daemon `strategy_keys` → currently **5**.
- Forecast `active_strategy_count=3` is stale.
- Ratings file count=5 after refresh. “4” likely mid-transition count (ADA/LTC/NG/XRP before BTC1h confirm).

## §四 Mechanism families (current live)

| Family | Members | Independent niche? |
|--------|---------|--------------------|
| `exhaustion_fade_short` | NG5, LTC5, XRP15m, BTC1h-xrpport | **No — one mechanism cluster** |
| `session_trend_pullback` | ADA5 trendpb | Separate |

Counting NG/LTC/XRP as 3 niches overstates diversification.

## §五 Frequency gap (current)

- Latest AI forecast: ~0.32 fills/day, ~2.25/week.
- Target band: **0.5–1.0 / day**, **3.5–7 / week**.
- Gap: material shortfall; should drive creation briefs — **not** loosen entries / force open.

## §六 PROTECT checklist (do not weaken)

auto-open, 0.9% SL, TP, close chain, 20x, 30% size, position monitor, WxPusher, SAT_WITNESS, B/C/A/S, human confirm — **untouched by this refactor**.
