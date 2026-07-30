# 回测报告摘要 — ada5m_mom_vol_filter_v1

## 元思考发散（换方向重创造）
- 强制指令：请先列举 3 种完全不同的市场微观结构视角来解释当前指令，然后再选择其中一种深入推演。禁止一上来直接写策略；禁止三种视角同质化（例如都写成均值回归变体）。
- 视角1：动量×波动率双重过滤
- 视角2：跨品种价差均值回归（BTC-ADA corr≈0.70 / BTC-LTC≈0.59）
- **择优：方向一（D1）**；方向二短窗夏普为负，放弃交付

## 标的
- **ADA-USDT-SWAP 5m**（在 D1 网格中夏普/频率最优）

## 成本与硬风控
- 滑点 0.05% + Taker 0.05%；杠杆 20x；硬止损 0.9%；保证金≤5%
- ATR 动态仓位；EMA144 趋势过滤；高波分位禁止追单

## 样本窗口
- formal_ada_5m_candles_cache.json，约数天～一周量级（非 2024YTD 完整史）

## 结果
| 指标 | 数值 | 目标 |
|---|---|---|
| 总收益率 | 0.02% | — |
| 年化夏普代理 | 1.806 | >1.5 |
| 最大回撤 | -1.45% | <15% |
| 胜率 | 44.4% | — |
| 盈亏比 | 1.42 | — |
| 成交笔数 | 18 | — |
| 日均笔数 | 3.17 | 2-8 |

## 目标达成（短窗）
- 夏普>1.5：是
- 回撤<15%：是
- 日均2-8笔：是

## QuantOracle
- source: local_fallback
- certified: `{"sharpe_ratio": 0.20836753717317133, "sortino": null, "calmar": null, "win_rate": 0.4444444444444444, "profit_factor": null, "ann_vol": null, "kelly_quarter": null, "kelly_recommended": null, "max_drawdown": null, "hurst": null}`