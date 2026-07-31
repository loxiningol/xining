# 【已拒绝交付】ada5_bopb_mutation_ports_v1

## 创造指令
以 **ADA5顺势回升** 与 **亚盘高突破回踩再进** 为种子，做异标的 / 异周期推演与轻度参数演化；不自动挂载。

## 常规链条做了什么
1. 抽取种子 DSL（trendpb / bopb）并定义变异轴：标的、周期、RSI/z/hold/offset、会话箱（asia→london）
2. train5 特征帧多标的核心筛 + near-miss 扩展（`scripts/screen_ada5_bopb_mutation_ports.py`）
3. 对 BNB15m / XAG5m near-miss 做 TP/持有/伦敦箱救援（`scripts/screen_ada5_bopb_mutation_rescue.py`）
4. 对短窗 POS 代表候选做更长 OHLC 再测（BNB15m ~187d、XAG5m ~173d）

## 结论

| 阶段 | 结果 | 可交付？ |
|---|---|---|
| 核心移植筛（ETH/BNB/SOL/LTC/XRP/…） | POS = 0；仅 BNB15m / XAG5m 出现 near-miss | 否 |
| 短窗救援 | BNB15m bopb / XAG5m bopb 出现 n≥10 且 WR≥50% 且 mean>0 | 仅短窗，不可交 |
| 长窗再测（~6个月） | 代表候选 WR 尚可或崩溃，**mean_net 全面转负** | 否 |

**长窗 POS（n≥10 且 WR≥50% 且 mean_net>0）= 0**

最佳短窗逼近（均在长窗坍塌）：
- BNB-USDT-SWAP 15m · london BO-PB · WR≈66.7% n=12 mean≈+0.99% → 长窗 mean≈−3.5%
- BNB-USDT-SWAP 15m · asia BO-PB · WR≈61.5% n=13 mean≈+0.49% → 长窗 mean≈−1.1%~−1.8%
- XAG-USDT-SWAP 5m · asia BO-PB · WR=50% n=10 mean≈+0.56% → 长窗 WR≈31% mean≈−3.1%

证据：
- `strategies/ada5_bopb_mutation_ports_v1/_scratch/mutation_ports_screen_v1.json`
- `strategies/ada5_bopb_mutation_ports_v1/_scratch/mutation_ports_rescue_v1.json`
- `strategies/ada5_bopb_mutation_ports_v1/_scratch/bnb15m_mutation_long_retest.json`
- `strategies/ada5_bopb_mutation_ports_v1/_scratch/xag5m_mutation_long_retest.json`
- `strategies/ada5_bopb_mutation_ports_v1/_scratch/long_window_collapse_summary.json`

## 铁律动作
- **不提交四阶段复核**
- **不挂载、不 `--confirm`**
- 不把短窗刷胜率移植当交付

## 建模观察（供下一轮）
1. ADA 种子逻辑的跨标的/跨周期移植，在当前摩擦与出场下**期望不稳健**——高 WR 短窗样本会被更长窗吃掉。
2. BNB15m 会话箱族是唯一持续 near-miss 槽位；若续研，应先换出场/成本敏感结构，再谈挂载。
3. 同标 ADA 5m 参数微调被近重复门禁挡住；本轮未碰该禁区。
