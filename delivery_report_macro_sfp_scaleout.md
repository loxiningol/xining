# Auto-Driver Delivery Report

Generated at: `2026-07-29 21:11:08 UTC`

## 1. 终态总结

| Field | Value |
|---|---|
| Final status | **LIMIT_REACHED_FAILED** |
| Stop code | `AI_LIMIT_REACHED` |
| Iterations | 2 |
| Elapsed sec | 363.3 |
| Symbol / TF / Dir | `ETH-USDT-SWAP` / `15m` / `long` |
| Initial family | `macro_sfp_displacement` |
| Final family | `macro_sfp_displacement_ad1` |
| Workdir | `/root/auto_trade/dual_engine/workflow_v2/auto_driver_runs/20260730_050504` |

Stop detail:

```json
{
  "message": "deepseek: 核心信号频率极低，且所有可调参数已位于合理边界（vol_z=1.8最低，ATR trailing=5.0最高），无法增加filled_entries而不违反不可协商规则。 | qwen: 单标的(ETH-USDT-SWAP) 15m timeframe下，入场条件受硬约束锁死（不可放宽vol_z20、sweep、reclaim、displacement body），无法通过调参增加样本数，客观数据稀疏导致样本量无法达到5个的最低要求。"
}
```

## 2. 迭代轨迹表

| Iter | Reason | Score | Gap | Pay | Calmar | W5 | L1 fills | AI decision | Patches |
|---:|---|---:|---:|---:|---:|---:|---:|---|---|
| 1 | kb_blocked | 0.0000 | 5.0000 | - | - | - | - | PATCH | replace_exit |
| 2 | funnel_l1_fail | 2.7000 | 2.0000 | - | - | - | 3 | LIMIT_REACHED | - |

### 每轮摘要

#### Iteration 1

- task_id: `wsa_20260730_050504_1f8e`
- pipeline_reason: `kb_blocked`
- failed_checks: `[]`
- l1_reject: `[]`
- AI: `PATCH` — 当前DSL的partial_tp_atr n_atr=2.0过早止盈50%仓位，严重限制盈亏比；同时atr_trail n_atr=4.5低于机制设计值5.0。移除partial并提升trailing ATR至5.0，符合v2优化方向，预计提高payoff ratio并降低worst5损失占比。

#### Iteration 2

- task_id: `wsa_20260730_050827_ab9d`
- pipeline_reason: `funnel_l1_fail`
- failed_checks: `[]`
- l1_reject: `['sample_filled_entries_lt_5']`
- AI: `LIMIT_REACHED` — majority_limit
- limit_reason: deepseek: 核心信号频率极低，且所有可调参数已位于合理边界（vol_z=1.8最低，ATR trailing=5.0最高），无法增加filled_entries而不违反不可协商规则。 \| qwen: 单标的(ETH-USDT-SWAP) 15m timeframe下，入场条件受硬约束锁死（不可放宽vol_z20、sweep、reclaim、displacement body），无法通过调参增加样本数，客观数据稀疏导致样本量无法达到5个的最低要求。

## 3. 极限分析（未过关）

驱动在终止条件触发后停止，策略仍未 `ok=true`。

3 方 AI 多数/指定判定：在当前数据、硬规则与 non-negotiables 下已无进一步优化空间。

AI limit_reason: deepseek: 核心信号频率极低，且所有可调参数已位于合理边界（vol_z=1.8最低，ATR trailing=5.0最高），无法增加filled_entries而不违反不可协商规则。 | qwen: 单标的(ETH-USDT-SWAP) 15m timeframe下，入场条件受硬约束锁死（不可放宽vol_z20、sweep、reclaim、displacement body），无法通过调参增加样本数，客观数据稀疏导致样本量无法达到5个的最低要求。

主要指标缺口: l1_filled_entries=2.0000, l1_payoff=0.0000

## 4. Key Diff / 变更说明

- mechanism_family: `macro_sfp_displacement` → `macro_sfp_displacement_ad1`
- 累计应用补丁 1 条：
  - replace_exit {}

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
  "pack_path": "/root/strategy_macro_sfp_displacement_v2_scaleout.json",
  "symbol": "ETH-USDT-SWAP",
  "timeframe": "15m",
  "direction": "long",
  "tag": "auto_driver_macro_sfp_scaleout",
  "max_iterations": 10,
  "l1_seed_retries": 6,
  "ai_providers": [
    "deepseek",
    "qwen",
    "glm"
  ],
  "prefer_provider": "glm",
  "ai_max_tokens": 2800,
  "convergence_eps": 0.02,
  "convergence_patience": 3,
  "stagnant_reason_patience": 4,
  "max_kb_family_bumps": 3,
  "auto_bump_family_after_gate2": true,
  "report_name": "delivery_report.md",
  "report_copy_to": "delivery_report_macro_sfp_scaleout.md",
  "workdir": null,
  "vector_root": "/root",
  "dry_run": false,
  "dry_run_skip_ai": false,
  "skip_pipeline": false,
  "optimize_goals": [
    "scale_out_partial_tp_atr: lock partial_tp_ratio=0.5 at n_atr=2.0×ATR_14 (NOT fixed % TP)",
    "remainder_trail: atr_trailing n_atr=4.5×ATR_14 on remaining 50%",
    "lottery_fix: avoid 100% single-trail max-win domination of remove_max_win",
    "matrix_38: suitable_symbols covers full 38-symbol whitelist for cross-asset smoothing",
    "family_adN: after Gate2 fail enable macro_sfp_displacement_adN (auto_bump); never lower Gate2 floors",
    "never_lower_gate2_floors: payoff/calmar/worst5/remove_max_win thresholds stay"
  ],
  "notes": [
    "Macro SFP displacement structural mutation via Auto-Driver (scale-out + lottery).",
    "Seed pack already has partial_tp_atr 0.5@2.0ATR + trail 4.5 + 38-symbol matrix.",
    "Do NOT --confirm, do NOT auto-mount, do NOT lower Gate2 floors, do NOT restart formal daemons.",
    "Requires /root/auto_trade/ai_ecosystem.env keys + ai_research_consent.json."
  ]
}
```
