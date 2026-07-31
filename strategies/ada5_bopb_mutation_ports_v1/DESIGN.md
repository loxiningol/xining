# ada5_bopb_mutation_ports_v1 — 既有策略变异创造

## 创造方向
从生产已验证骨架做**异标的 / 异周期推演**，而非同标同拓扑刷参：

1. **ADA5顺势回升**（`codex0725t3_ada5m_trendpb_r42_z2p3_h14`）  
   底层：H1 EMA19>EMA53 + slope>0 + RSI 上穿 + close>ema21 + z20 上限；RSI≥60 止盈 / prev_low20 失效。
2. **亚盘高突破回踩再进**（`ada5m_bopb_asia_o20_r42_z2p3`）  
   底层：同上骨架 + `close.offset(N) > asia_high.offset(N)`（突破记忆后再等 RSI 回升）。

## 变异轴
| 轴 | 内容 |
|---|---|
| 标的移植 | ETH/BNB/SOL/LTC/XRP/AVAX/LINK/DOGE/XAG |
| 周期移植 | 5m → 15m（有帧则测） |
| 轻度参数演化 | RSI / z / hold / asia_offset / TP RSI |
| 会话箱演化 | asia_high → london_high（救援阶段） |

## 禁区
- ADA-USDT-SWAP 5m 同拓扑近重复（已 live / 已挂 B）
- 自动挂载 / `--confirm`（本链只做到创造证据与是否可交四阶段复核）

## 门槛（创造预筛）
- n≥10 且 WR≥50% 且 mean_net>0（train5 特征帧）
- 若通过，再要求更长窗 / 近2年周开仓折价门（R4）后方可交四阶段复核
