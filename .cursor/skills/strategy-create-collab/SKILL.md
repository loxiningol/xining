---
name: strategy-create-collab
description: >-
  When the user orders creation of a new trading strategy, collaborate with
  GLM as mechanism designer and optional EasyQuant modeling bridge before
  writing DSL. Use when the user says 创造策略, 创建策略, 新策略, design a
  strategy, or asks Codex to invent a mechanism with GLM.
---

# 策略创造 · GLM 协作建模

## 何时启用

用户下令「创造 / 新建 / 设计策略」时立刻启用。禁止直接手写 DSL 蒙混过关。

## 铁律

1. **GLM 先建模**：先让 GLM 产出完整 `mechanism_spec`（市场无效性、对手方、边为何存在、入场/出场因果、不可协商规则）。
2. **Codex 后落码**：只按契约把 spec 译成 DSL；禁止用 EMA/RSI 等套路偷换机制。
3. **禁止 prebuilt 旁路**：不得把「已写好的 JSON pack」当成创造；创造必须走 GLM live design（除非用户明确给出现成 pack 只要求复核）。
4. **四复核同一套**：创造完成后的过关标准与 ADA5 校准档一致——第一次语法/断言/密度 → 第二次单标的稳定性（n≥10、WR≥50%、mean_net>0）→ 第三次矩阵 soft → 第四次三AI（WR≥65%、盈利单均盈≥5%）→ 人工确认；永不自动上线。
5. **EasyQuant**：先探测 `eqlib` / 用户指定的 EasyQuant 端点；若不可用，在建模摘要里标明「未接入」并继续 GLM 建模，不要假装已回测。

## 执行步骤

1. 读用户 brief（标的、时框、方向、机制偏好、禁区）。
2. 在生产机或本地跑：
   ```bash
   python3 scripts/strategy_create_collab.py --symbol ETH-USDT-SWAP --timeframe 5m --direction long --brief "用户原话"
   ```
3. 检查输出的 `mechanism_spec` + 建模摘要；有 EasyQuant 信号则并入 brief 再让 GLM 修订一轮。
4. 用 `codex_implement_from_spec` / STEP A（**不要** `prebuilt_spec_pack` 跳过 GLM）提交创造。
5. 报告：机制中文名、因果一句话、四复核结果；失败则归因到具体复核阶段，禁止甩锅 UI。

## 反例（禁止）

- 直接丢一个 `strategy_*.json` 进 auto_driver 却声称「已与 GLM 协作创造」
- 用旧扫荡模板改参数冒充新机制
- 胜率 <50% 的样本还宣称接近 ADA5 质量
