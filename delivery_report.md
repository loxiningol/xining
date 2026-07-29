# Auto-Driver Delivery Report

Generated at: `2026-07-29 21:51:14 UTC`

## 1. 终态总结

| Field | Value |
|---|---|
| Final status | **LIMIT_REACHED_FAILED** |
| Stop code | `CONVERGED_NO_IMPROVEMENT` |
| Iterations | 4 |
| Elapsed sec | 923.9 |
| Symbol / TF / Dir | `ETH-USDT-SWAP` / `15m` / `long` |
| Initial family | `session_liq_engulf_displace_matrix_ad11` |
| Final family | `session_liq_engulf_displace_matrix_ad18` |
| Workdir | `/root/auto_trade/dual_engine/workflow_v2/auto_driver_runs/sess_liq_c2c_ad11b_20260730` |

Stop detail:

```json
{
  "message": "composite_score stagnated for 3 rounds (eps=0.0150)",
  "recent_scores": [
    6.277547,
    4.663791,
    4.663791,
    2.588482
  ],
  "deltas": [
    -1.6137560000000004,
    0.0,
    -2.075309
  ],
  "recent_gaps": [
    8.262733,
    8.262733,
    0.493822
  ]
}
```

## 2. 迭代轨迹表

| Iter | Reason | Score | Gap | Pay | Calmar | W5 | L1 fills | AI decision | Patches |
|---:|---|---:|---:|---:|---:|---:|---:|---|---|
| 1 | repair_exhausted_or_drift | 6.2775 | 6.5106 | 2.4825 | 26.0043 | 0.8931 | - | PATCH | replace_exit,rename_family,rename_family |
| 2 | repair_exhausted_or_drift | 4.6638 | 8.2627 | 1.0654 | 57.6044 | 0.9206 | - | PATCH | merge_dsl,rename_family,noop,rename_family |
| 3 | repair_exhausted_or_drift | 4.6638 | 8.2627 | 1.0654 | 57.6044 | 0.9206 | - | PATCH | set,set,rename_family,rename_family |
| 4 | funnel_l1_fail | 2.5885 | 0.4938 | - | - | - | 6 | PATCH | set,set,rename_family |

### 每轮摘要

#### Iteration 1

- task_id: `wsa_20260730_053622_8ded`
- pipeline_reason: `repair_exhausted_or_drift`
- failed_checks: `['payoff_ge_2_5', 'worst5_loss_share_le_40pct', 'remove_max_win_stable']`
- l1_reject: `[]`
- AI: `PATCH` — Gate2失败：payoff=2.48略低于2.5，w5_loss_share=0.89极高，彩票过度依赖。不改entry，采用scale-out：partial_tp_atr@2.5ATR减仓0.5，剩余仓atr_trailing@4.0ATR，保留swing_extreme invalidation。预期改善损失集中度和彩票问题，payoff略升。

#### Iteration 2

- task_id: `wsa_20260730_054055_4b5f`
- pipeline_reason: `repair_exhausted_or_drift`
- failed_checks: `['payoff_ge_2_5', 'expectancy_factor_ge_1_0', 'worst5_loss_share_le_40pct', 'remove_max_win_stable']`
- l1_reject: `[]`
- AI: `PATCH` — Gate2失败：payoff仅1.07，worst5_loss_share=0.92，彩票过度依赖且损失集中。当前仅在ETH上回测，样本不足且亏损集中，启用38symbol矩阵可分散风险、增加盈利样本，从而改善payoff并稀释worst5。不改entry，仅扩展标的并重命名家族。

#### Iteration 3

- task_id: `wsa_20260730_054423_31ff`
- pipeline_reason: `repair_exhausted_or_drift`
- failed_checks: `['payoff_ge_2_5', 'expectancy_factor_ge_1_0', 'worst5_loss_share_le_40pct', 'remove_max_win_stable']`
- l1_reject: `[]`
- AI: `PATCH` — 多标的矩阵回测后payoff骤降至1.07，worst5损失占比0.92，彩票过拟合。不改entry，通过收紧atr_trailing至3.5并缩短max_hold至36，减少利润回吐与极端亏损，提升payoff并稀释worst5集中度。

#### Iteration 4

- task_id: `wsa_20260730_054910_ba4b`
- pipeline_reason: `funnel_l1_fail`
- failed_checks: `[]`
- l1_reject: `['sample_payoff_le_1.2']`
- AI: `PATCH` — L1 failed with payoff=0.71, filled_entries=6. Entry cannot be mutated per optimize_goals. To improve payoff, widen trailing stop (atr_trailing n_atr 3.5→5.0) and extend max hold (36→48 bars), allowing winning trades to run further without fixed TP. No entry changes, scale-out retained. Expect payoff to increase toward 1.2 while filled_entries remain stable.

## 3. 极限分析（未过关）

驱动在终止条件触发后停止，策略仍未 `ok=true`。

连续多轮 composite_score / gap 改善低于阈值，判定收敛到局部平台。

主要指标缺口: l1_payoff=0.4938, l1_filled_entries=0.0000

## 4. Key Diff / 变更说明

- mechanism_family: `session_liq_engulf_displace_matrix_ad11` → `session_liq_engulf_displace_matrix_ad18`
- 累计应用补丁 14 条：
  - replace_exit {}
  - rename_family {"from": "session_liq_engulf_displace_matrix_ad11", "to": "session_liq_engulf_displace_matrix_ad12"}
  - rename_family {"to": "session_liq_engulf_displace_matrix_ad13", "auto": true}
  - merge_dsl {"keys": ["supported_instruments"]}
  - rename_family {"from": "session_liq_engulf_displace_matrix_ad13", "to": "session_liq_engulf_displace_matrix_ad14"}
  - noop {"note": "暂不改exit参数，待多标的回测后观察payoff/w5变化；若仍不达标再考虑收紧ATR trailer至3.0或降低max_hold_bars至24"}
  - rename_family {"to": "session_liq_engulf_displace_matrix_ad15", "auto": true}
  - set `dsl.max_hold_bars` = `36`
  - set `dsl.exit.any[1].n_atr` = `3.5`
  - rename_family {"from": "session_liq_engulf_displace_matrix_ad15", "to": "session_liq_engulf_displace_matrix_ad16"}
  - rename_family {"to": "session_liq_engulf_displace_matrix_ad17", "auto": true}
  - set `dsl.max_hold_bars` = `48`
  - set `dsl.exit.any[1].n_atr` = `5.0`
  - rename_family {"from": "session_liq_engulf_displace_matrix_ad17", "to": "session_liq_engulf_displace_matrix_ad18"}

## 5. 待审查建议

- 阅读第 3 节极限分析：若瓶颈是 Gate2 worst5/lottery 与 vol 选择性冲突，需换机制族而非继续调参。
- 检查 workdir 中各 iter 的 pack 快照与 gate JSON，确认 AI 补丁是否被 DSL validate 拒绝。
- 若 AI_ABORT：检查 `/root/auto_trade/ai_ecosystem.env` 密钥与 `ai_research_consent.json`。
- 不要手动解锁 KB family 后无差异重提；需有可区分的机制/样本策略。
- 可将 delivery_report 与最终 pack 交给主 AI 做架构级 redesign，而不是继续同一入口的微扰。
- 本驱动为外包 Wrapper：未修改 dual_engine_workflow_v2 管道核心逻辑。

## Appendix: config snapshot

```json
{
  "pack_path": "/root/strategy_session_liq_engulf_matrix_ad11_seed.json",
  "symbol": "ETH-USDT-SWAP",
  "timeframe": "15m",
  "direction": "long",
  "tag": "auto_driver_sess_liq_c2c_ad11b",
  "max_iterations": 8,
  "l1_seed_retries": 10,
  "ai_providers": [
    "deepseek",
    "qwen",
    "glm"
  ],
  "prefer_provider": "glm",
  "ai_max_tokens": 2800,
  "convergence_eps": 0.015,
  "convergence_patience": 3,
  "stagnant_reason_patience": 4,
  "max_kb_family_bumps": 6,
  "auto_bump_family_after_gate2": true,
  "enable_multi_symbol_matrix": false,
  "report_name": "delivery_report.md",
  "report_copy_to": "/root/delivery_report.md",
  "workdir": "/root/auto_trade/dual_engine/workflow_v2/auto_driver_runs/sess_liq_c2c_ad11b_20260730",
  "vector_root": "/root",
  "dry_run": false,
  "optimize_goals": [
    "gate2_after_l1 fingerprint pay≈2.482 w5≈0.893 lottery — NEVER mutate entry",
    "prefer replace_exit with partial_tp_atr 0.5@2.5ATR + atr_trailing<=5.0",
    "dsl bounds n_atr trail[2.5,5.0]; never vol_z <1.8; never mount/confirm"
  ],
  "notes": [
    "c2c clean ad11; matrix disabled for RAM; macro config parked"
  ]
}
```

## 5. 审查建议 / 理论上限（人工）

### 结论
在 **不放宽 Gate2 门槛**、**不降低 vol_z20≥1.8**、**不改核心 sweep/reclaim/body** 的前提下，本 family 在 ETH 15m 上复现了与 `macro_sfp_displacement` / 原 `session_liq` 几乎同一套 Gate2 失败指纹，并在无人值守补丁循环中 **收敛失败**。

### 最佳可达态（循环内）
- Iter1（改 exit 前）: pay **2.482** / w5 **0.893** / lottery fail / Calmar ≫1.8 / L1 PASS
- scale-out（partial 0.5@2.5 + trail 4.0）后: pay **崩到 ~1.07**，w5 更差 → 说明“减仓锁利”在该稀疏样本上砍掉了决定性大赢，反而加重 lottery/w5 叙事。

### 为何 AI 补丁无效
1. **样本稀疏**：vol_z≥1.8 + 同 bar PDL sweep/reclaim 在单标的上只有十几笔；清 w5 需要更多近似等额小亏，清 lottery 需要多个同量级赢单——二者与稀疏边互相冲突。
2. **退出结构无自由维度**：trail 已顶格附近（≤5.0）；再 scale-out 会毁 payoff；加密度（升 volz / 改 entry）要么违规要么毁 L1。
3. **矩阵路径**：本机 764MB，38-symbol 矩阵与正式 daemon 并发会 OOM；本轮强制 `enable_multi_symbol_matrix=false`。即便打开，历史同类 family 仍卡同一指纹。

### 可选下一步（需你授权之一）
1. **授权调整 Gate2** 对选择性宏观流动性策略的门槛（尤其 worst5 / remove_max_win），或
2. **授权放宽 vol_z 硬约束**（与现 non-negotiable 冲突），或
3. **换机制族**（避开已 KB 封锁的 session_liq / macro_sfp 谱系），或
4. 停止该方向，保留 failure KB 与本报告。

### 安全态
- `production_mounted=false`；未挂载、未 human confirm。
- 正式交易 daemon 保持运行。
- Auto-driver 仅包装 STEP A；未改 pipeline 核心门槛。
