# ada5m_mom_vol_filter_v1

换方向重创造：元思考对比 **D1 动量×波动率过滤** vs **D2 跨品种价差回归** 后择优 D1。

- 标的：ADA-USDT-SWAP 5m
- 硬约束：20x / SL 0.9% / 保证金≤5% / ATR 仓位 / EMA144
- 核心：BB 带宽压缩→扩张 + 突破；高波分位放弃追单

```bash
python3 strategy_code.py
```
