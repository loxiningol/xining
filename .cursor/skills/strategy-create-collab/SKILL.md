---
name: strategy-create-collab
description: >-
  When the user orders creation of a new trading strategy, run the five-stage
  creation blueprint (meta-think → hypothesis → EasyQuant+DeepSeek+QuantOracle
  → Alphalens rescreen → Backtrader/red-team stress) before GLM designs the
  mechanism_spec. ADA5 four-review stays a separate later step — do not modify
  review code. Use when the user says 创造策略, 创建策略, 新策略, or 蓝图.
---

# 策略创造蓝图 · ①–⑤（不含复核）

## 何时启用

用户下令「创造 / 新建 / 设计策略」时立刻启用。禁止只换通用模型直接写 DSL。

## 五阶段（创造飞轮）

| 阶段 | 工具/角色 | 任务 |
|---|---|---|
| ① 元思考 | MetaGPT/AutoGen 风格多角色 | 策略设计文档：逻辑/假设/风险边界/失效场景 |
| ② 假设验证 | Alphalens / CausalImpact 风格 | IC/IR/换手 + 因果效应，剔除伪相关 |
| ③ 因子挖掘 | EasyQuant + DeepSeek + QuantOracle | 挖因子并用确定性计算认证 |
| ④ 快速筛选 | Alphalens 风格再筛 | IC/IR/换手；过拟合熔断 |
| ⑤ 压力测试 | Backtrader 极端 + AutoGen 红队 | 危机窗口与对抗冲击 |

之后才进入**现有** ADA5 四复核（本 skill 不改复核代码）。

## 熔断

1. ②/⑤ 失败回溯 > 5 次 → 终止，返回无法构建报告  
2. IC 衰减过快 / 换手过高 → 丢弃因子  
3. QuantOracle/历史 VaR 超人类日损阈值 → 否决  

## 执行

```bash
# 仅蓝图 ①–⑤
python3 scripts/strategy_create_blueprint.py \
  --symbol ADA-USDT-SWAP --timeframe 5m --direction long \
  --brief "用户原话" --skip-llm

# 蓝图 + GLM mechanism_spec（默认蓝图内不调 LLM）
python3 scripts/strategy_create_collab.py \
  --symbol ADA-USDT-SWAP --timeframe 5m --direction long \
  --brief "用户原话"
```

可选：`--with-llm-research` 启用 GLM 元思考增强 + DeepSeek 因子提议；`--submit-step-a` 才进入现有复核。

## 反例

- 跳过蓝图直接 GLM / 手写 pack  
- LLM 口算夏普/Kelly  
- 修改或绕过 ADA5 复核环节  
- 把 A 股 eqlib 回测冒充 OKX 成交证据  
