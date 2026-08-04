# ETH-USDT-SWAP 15m — Market Behaviour Edge Discovery Report

**Artifact:** `frost3_eth15m_behavior_edge_discovery`  
**Key prefix:** `frost3_eth15m_behavior_`  
**Created:** 2026-07-26  
**Isolation:** 完全独立于 Frost3 / ETH15m fade / ETH5m / ETH1h / BTC5m microedge

## Fixed params（不可改）

| Param | Value |
|-------|-------|
| Leverage | 20x |
| Fixed SL | 0.9% |
| Position | 30% |
| Forbidden | martingale / grid / add / dynamic stop |

## Stability targets

- Opens/day **0.5–1.0**
- Theoretical WR (DS+Qwen+GLM avg) **≥75%**
- Profit Factor **≥2.0**
- Profitable after fees **and** slippage
- Maximize stability / low DD — not max return

## Must-not-be-edge

Trend-breakout、mean-reversion、EMA/MACD/RSI/ADX 作为主边；ETH15m fade 衰竭空同构；ETH1h breakout 同构；CL15m exhaustion 同构。

## DSL gap → honest proxies

原生 DSL 无 VWAP/VP/funding/OI/delta/session。将注入（诚实标注）：

| Proxy | Definition | Honesty |
|-------|------------|---------|
| `utc_hour` / `session_liq` | UTC 小时；[7,20) 会话门 | 日历流动性，非 L2 |
| `sess_vwap_dist` | 日会话 range-weighted VWAP 距离 / ATR | 非交易所 VWAP |
| `va_pos` / `poc_proxy` | 24h 区位；时间价 POC | 非成交量剖面 |
| `delta_proxy` / `cvd_slope4` | 实体/波幅压力；累计斜率 | 非 tick CVD |
| `absorb_score` / `sweep_reclaim` / `effort_result` | OHLC 形态 | 无 stop-map |
| `vol_pulse` | vol_z20 或 range z | 活动脉冲 |
| `funding_skew` / `oi_z20` | 仅当可拉取 | **禁止用指标伪造** |

传统指标政策：仅可作宽过滤；本报告优先 **零指标入场骨架**。

## CL archive lessons

- 勿过窄单阈值 → trades<10 → WF 折数不足  
- 勿边际 exhaustion → 硬止损簇 → friction 崩  
- 软化参数须同时挡新硬止损 + dest±20%  
- friction 崩主因是硬止损簇  
- 目标开仓频率稳态，不刷单  

## Directions（14 ≥ 12）

### D01 — `session_auction_imbalance` LONG
伦敦开盘拍卖买方接受（上半区+正 delta+参与度）。开仓/日≈0.6。非突破追价。

### D02 — `vwap_acceptance_continuation` LONG
会话 VWAP **上方接受**（非回归）。跌破 VWAP 失效。开仓/日≈0.7。

### D03 — `value_area_edge_rejection` SHORT
VA 上沿拒绝（absorb+负 delta）。非 RSI 超买 fade。开仓/日≈0.55。

### D04 — `liquidity_sweep_reclaim` LONG
扫 `prev_low20` 后收回。异于 BNB z20+macd V-reclaim。开仓/日≈0.5。

### D05 — `effort_result_absorption_fade` SHORT
高 effort / 低 progress + 吸收 → 供应释放。Wyckoff 行为。开仓/日≈0.55。

### D06 — `ny_overlap_participation_pulse` LONG
UTC13–16 重叠窗参与度脉冲+正流+POC 上。开仓/日≈0.65。

### D07 — `opening_range_failed_break` SHORT
日 OR 假上破收回。**反** trend-breakout。频次风险：或 <0.5/日。开仓/日≈0.45。

### D08 — `cvd_price_divergence_release` LONG
价新低但 CVD 代理抬升后翻转。**禁止**改成 RSI 背离。开仓/日≈0.5。

### D09 — `funding_crowding_unwind` SHORT
极端正资金费+行为拒绝。`REQUIRES_FETCHABLE_FUNDING`，否则 Death Test 杀。开仓/日≈0.4。

### D10 — `oi_expansion_price_stall` SHORT
OI↑+价格停滞。`REQUIRES_FETCHABLE_OI`，否则杀。开仓/日≈0.4。

### D11 — `asia_inventory_transfer_dump` SHORT
亚洲推升残留 → 伦敦库存移交抛压。开仓/日≈0.55。

### D12 — `compression_release_inside_value` LONG
VA **内部**压缩后参与度恢复。硬禁破 `prev_high` 入场（避 ETH1h breakout）。开仓/日≈0.7。

### D13 — `one_sided_book_exhaust_proxy` SHORT
连阳实体递减+吸收。硬禁 rsi/z/macd/ema16（避 ETH15m fade）。开仓/日≈0.5。

### D14 — `poc_migration_follow` LONG
POC 上移且收盘在 POC 上接受。非 EMA 排列边。开仓/日≈0.6。

## Next steps（强制顺序）

1. ~~Edge Discovery Report~~ ← **本文件**  
2. Novelty Ranking  
3. Death Test（freezer + CL + live collisions）  
4. Hypothesis（survivors）  
5. Walk Forward ≥7/10  
6. Monte Carlo ≥90% sign-shuffle  
7. Extreme Friction Sharpe≥0  
8. DeepSeek + Qwen + GLM reviews（avg≥75%）  
9. Quick path logic destruction；每阶段 ≤3 repairs  

## SSH note

写报告时主机 `64.176.47.192` SSH banner exchange 超时；本地先落盘，恢复后同步至 `/root/auto_trade/dual_engine/frost3_eth15m_behavior_*`。
