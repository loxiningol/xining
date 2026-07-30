# btc15m_mtf_momentum_v1

创造蓝图交付（**未挂载、未进复核**）。

## 选择结论

- 标的筛选：BTC-USDT-SWAP（流动性 + 15m/1h MTF）
- 元思考 A/B/C 发散后择优：**A 多时间框架动量突破**
- 硬约束：20x、止损 0.9%、单笔保证金 ≤5%、ATR 仓位、EMA144(1h) 过滤

## 文件

| 文件 | 说明 |
|---|---|
| `strategy_code.py` | OKX/ccxt 风格策略（含回测与 dry-run 下单） |
| `params.json` | 全部可调参数 |
| `backtest_report.md` | 回测摘要 |
| `risk_report.md` | 压力/极端亏损 |
| `CREATION_SUMMARY.json` | 创造全量摘要 |

## 运行回测

```bash
python3 strategy_code.py
```

## 注意

当前 formal 缓存仅约 13 天，**不能**证明夏普>1.5（2024YTD）目标。进入 ADA5 四复核前需更长 OKX 历史复验。
