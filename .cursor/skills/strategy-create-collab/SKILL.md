---
name: strategy-create-collab
description: >-
  When the user orders creation of a new trading strategy, run EasyQuant-style
  factor mining + QuantOracle deterministic certification before GLM designs
  the mechanism_spec, then ADA5 four-review. Use when the user says 创造策略,
  创建策略, 新策略, or asks to invent a mechanism with GLM.
---

# 策略创造 · QuantOracle + EasyQuant + GLM

## 何时启用

用户下令「创造 / 新建 / 设计策略」时立刻启用。禁止直接手写 DSL 或只换一个通用模型蒙混。

## 铁律

1. **先挖因子（EasyQuant 风格）**：用本地 OKX K 线做自动化因子挖掘与分位数信号评估。
2. **再认证计算（QuantOracle）**：对候选因子收益序列跑确定性 Sharpe / Kelly / Drawdown / Hurst，禁止 LLM 口算。
3. **GLM 后建模**：mechanism_spec 必须锚定已认证因子与对手方；禁止与 certified 数字冲突。
4. **Codex 落码**：只按契约译 DSL。
5. **ADA5 四复核**：提交后仍走与 ADA5顺势回升相同门槛；永不自动上线。

## 执行

```bash
python3 scripts/strategy_create_collab.py \
  --symbol ETH-USDT-SWAP --timeframe 5m --direction long \
  --brief "用户原话"
```

可选：`--implement` 落 DSL；`--submit-step-a` 进入 STEP A（ADA5 admission）。

## 反例

- 跳过 research 直接 GLM / 手写 pack 却声称专业创造
- 用通用聊天模型自己编夏普/胜率
- 把 A 股 eqlib 回测冒充 OKX 成交证据
