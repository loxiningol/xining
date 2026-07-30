# 回测报告摘要 — btc15m_mtf_momentum_v1

## 标的筛选
- 主标的：**BTC-USDT-SWAP**（15m 入场 + 1h EMA144 趋势）
- 备选：CL-USDT-SWAP、LTC-USDT-SWAP
- 元思考发散后选择：**A 多时间框架动量突破**

## 成本假设
- 滑点 0.05%，OKX Taker 手续费 0.05%（开平各计）
- 杠杆 20x；硬止损 0.9%；单笔保证金 ≤ 权益 5%

## 样本窗口（重要）
- 数据源：生产 formal_btc_15m/1h_candles_cache.json（实盘同源 K 线）
- 覆盖约 2026-07-17 → 2026-07-30（约13天，1200根15m），不足以验证 2024YTD 目标
- 下列指标仅反映该短窗；年化夏普为粗代理，不作达标承诺

## 结果
| 指标 | 数值 | 目标 |
|---|---|---|
| 总收益率 | -2.04% | — |
| 年化夏普代理 | -9.248 | >1.5 |
| 最大回撤 | -2.04% | <15% |
| 胜率 | 28.6% | — |
| 盈亏比 | 1.002 | — |
| 成交笔数 | 21 | — |
| 日均笔数 | 1.62 | 2-8 |

## 目标达成（短窗）
- 夏普>1.5：否（短窗未达标）
- 回撤<15%：是
- 日均2-8笔：接近/不足（EMA144预热+短样本）

## QuantOracle
- source: local_fallback
- certified: `{"sharpe_ratio": -1.7106591533185855, "sortino": null, "calmar": null, "win_rate": 0.2857142857142857, "profit_factor": null, "ann_vol": null, "kelly_quarter": null, "kelly_recommended": null, "max_drawdown": null, "hurst": null}`