# ETH15m Market Behaviour — 最终中文摘要

**状态：失败归档（未进 pending/formal）**  
**精确死因：`full_friction_sharpe`**

## 管线完成度

| 步骤 | 结果 |
|------|------|
| Edge Discovery (14) | 完成 |
| Novelty / Death Test | 完成（D09/D10 DATA_UNAVAILABLE） |
| 代理注入 + DSL | 完成（OHLC 行为代理，禁 RSI/MACD/EMA 作边） |
| Quick WF | **D01 通过 7/10** |
| Full Friction / MC | **失败** |
| Sim / 三模型 / Formal | 未达 |

## 最佳幸存者指标（D01 session_auction_imbalance long）

| 指标 | 值 | 目标 | |
|------|-----|------|---|
| opens/day（探针） | **0.768** | 0.5–1.0 | OK |
| WF | **7/10** | ≥7/10 | OK |
| trades | 38 | ≥10 | OK |
| WR | **50%** | ≥75% | FAIL |
| mean_net | +0.0189 | >0 | 边际 |
| friction Sharpe | **-0.105** | ≥0 | FAIL |
| MC beat | **0.84** | ≥0.90 | FAIL |
| PF | n/a | ≥2.0 | — |

## 其他死亡

- D05 / D11 / D02 / D06 / D01b → `quick_walk_forward`
- D14 → `probe_overtrading`（3.37/日）
- D09 → `DeathTest.DATA_UNAVAILABLE.funding_skew`
- D10 → `DeathTest.DATA_UNAVAILABLE.oi_z20`

## 修理尝试

hold/exit 网格与入场收紧网格均无法在保持 WF+dest 的同时把 friction Sharpe 翻正；identity 配置本身卡在 fr≈-0.105。

## 产物前缀

`/root/auto_trade/dual_engine/frost3_eth15m_behavior_*`  
本地镜像：`dual_engine_local/frost3_eth15m_behavior/` + `frost3_eth15m_behavior_run.py`
