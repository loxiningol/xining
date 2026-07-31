---
name: strategy-create-collab
description: >-
  When the user orders creation of a new trading strategy, run research-discovery
  first (mechanism graph + phenomenon population → naked probes → antifalsify →
  EFR → DSR/PBO), then assemble. ADA5 four-review stays separate — do not modify
  review code. Use when the user says 创造策略, 创建策略, 新策略, or 蓝图.
---

# 策略创造 · 研究发现优先（不含复核）

## 何时启用

用户下令「创造 / 新建 / 设计策略」时立刻启用。禁止只换通用模型直接写 DSL。

## 流水线

| 阶段 | 模块 | 任务 |
|---|---|---|
| 0 研究契约 | `research_discovery` | 目标不可实现 → 返回无可信候选 |
| 1 种群 | `mechanism_graph` + `phenomenon_scanner` | 理论↔数据双向；禁止三选一过早收敛 |
| 2 多样性 | `map_elites_archive` | 行为格子，不是 Top-N 克隆 |
| 3 裸探针 | `probe_protocol` | 固定持有；裸机制无效禁止用退出优化 |
| 4 反证 | `antifalsify` | 证据矩阵；不宣称因果证明 |
| 5 可成交 | `edge_friction` | EFR≥1.5 |
| 6 多重检验 | `multiple_testing` + `research_ledger` | DSR/PBO；试验入账 |
| 7 组装 | 原 lite 矿工/压力/硬度 | 仅对存活假设 |

之后才进入**现有** ADA5 四复核（本 skill 不改复核代码）。

## 铁律

1. **先预测/探针，后策略**；禁止先写完整进出场再圆故事。
2. **周收益≥8% 已撤销**为硬门槛；不可实现需求应返回无可信候选。
3. CausalImpact-lite **不是**因果证明器。
4. 数字以 QuantOracle/统计引擎为准；Kimi Judge 默认未启用。
5. 长历史走 R2/S3 研究仓。
6. 胜率 < 50% → 禁止展示；年化代理与收益/回撤硬度仍生效。

## 入口

```bash
python3 scripts/strategy_create_blueprint.py --symbol ADA-USDT-SWAP --timeframe 5m --brief "..."
python3 scripts/strategy_create_collab.py --symbol ADA-USDT-SWAP --timeframe 5m --brief "..."
```
