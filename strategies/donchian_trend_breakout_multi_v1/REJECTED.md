# 【已拒绝交付】donchian_trend_breakout_multi_v1

## 创造指令
趋势突破型：经典 Donchian Channel（`prev_high20`/`prev_low20`）+ 趋势过滤的中频突破；
不局限 ADA，在高流动性标的上寻找有效突破 / 过滤假突破 / 主趋势启动。

## 常规链条做了什么
1. 复用既有 BTC15m 创造包结论（短窗 WR 好看、长窗坍塌）
2. 在生产 dual_engine / train5 特征帧上做**多标的网格**（ETH/BNB/SOL/LTC/XAU/XAG 等）
3. 变体覆盖：裸突破、结构确认、量能过滤、回踩再进、假突破回落、ATR/分批出场

## 多标的结论（关键）

| 家族 | 代表标的 | 典型结果 | 可交付？ |
|---|---|---|---|
| 裸 Donchian 追突破 + H1 | ETH/SOL/BNB/XAU/XAG | WR 约 15%–32%，期望为负 | 否 |
| 结构确认 + vol_z + H1 | ETH15m / BNB15m / XAU15m | WR 约 20%–51%，均值净值为负 | 否 |
| 突破回踩再进（唐奇安记忆） | 同上 | 样本极稀或期望为负 | 否 |
| 假突破回落（空） | ETH/BNB/XAU/SOL | 样本不足或 WR/收益不达标 | 否 |
| ATR / partial_tp 出场升级 | ETH/BNB/XAU | XAU partial WR≈51.5% 但仍负期望 | 否 |

**POS（n≥8 且 WR≥50% 且 mean_net>0）= 0**

证据文件：
- `strategies/_scratch/donchian_diag_v2.json`
- `strategies/_scratch/donchian_retest_screen_v1.json`
- `strategies/_scratch/donchian_exit_screen_v1.json`
- `strategies/_scratch/donchian_multi_screen_v1.json`
- 既有 `strategies/btc15m_donchian_trend_v1/REJECTED.md`

## 铁律动作
- **不提交四阶段复核**
- **不挂载、不 `--confirm`**
- 不把短窗刷胜率策略当交付

## 下一轮可研方向（需你点头再开新链）
1. **会话箱突破回踩**（`asia_high` / London box）在 ETH/SOL/BNB 上移植「突破记忆+RSI再进」——ADA 上已验证结构，但不是纯唐奇安
2. **更慢通道**（`prev_high48` / H1 Donchian）+ 更严启动过滤，拉长持有
3. **只做假突破衰竭**并换摩擦更友好的出场（需先过样本与 WR 门槛）
