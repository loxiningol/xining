# 风险评估报告 — btc15m_mtf_momentum_v1

## 硬风控
- 保护性止损 0.9%（开仓价）
- 单笔保证金 ≤ 权益 5%，杠杆 20x
- ATR 动态仓位；EMA144(1h) 趋势过滤

## 压力测试（Backtrader-lite + 红队）
- passed: True
- full_max_drawdown: -0.05025328963122411
- attacks: `[{"name": "adverse_slippage_3bp", "max_drawdown": -0.04765218571441532, "total_return": -0.04765218571441532, "pass": true}, {"name": "periodic_gap_loss", "max_drawdown": -0.07846814935741864, "total_return": -0.07846814935741864, "pass": true}, {"name": "liquidity_drought", "max_drawdown": -0.09948228588911634, "total_return": -0.09948228588911634, "pass": true}]`

## 极端预期亏损
- 历史5%分位单笔收益(近似VaR): -1.00%
- 最差单笔: -1.00%
- 理论单笔触硬止损最大权益损失上界约 0.9%（notional≤权益×5%×20 时）

## 失效场景
- 1h 趋势过滤滞后导致假突破连续止损
- 波动骤升使 ATR 仓位仍触 0.9% 硬止损堆叠
- 短窗过拟合；需更长 OKX 历史复验后方可进入 ADA5 复核

## 结论
本包为创造蓝图交付物，未挂载实盘。短窗未证明夏普目标；回撤可控。下一步：拉长历史复验 → 现有 ADA5 四复核（不改复核代码）。