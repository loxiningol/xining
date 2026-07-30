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
| ① 元思考 | MetaGPT/AutoGen 风格多角色 | **先强制发散**：列举 3 种完全不同微观结构视角，再择一深入；然后出设计文档 |
| ② 假设验证 | Alphalens / CausalImpact 风格 | **winsorize 去极值 + neutralize 中性化** → IC/IR/换手 + 因果 |
| ③ 因子挖掘 | EasyQuant + DeepSeek + QuantOracle | 挖因子并用确定性计算认证 |
| ④ 快速筛选 | Alphalens 风格再筛 | 再次 winsorize/neutralize 后 IC/IR/换手；过拟合熔断 |
| ⑤ 压力测试 | Backtrader 极端 + AutoGen 红队 | 危机窗口与对抗冲击 |

之后才进入**现有** ADA5 四复核（本 skill 不改复核代码）。

## 铁律（创造时必须遵守）

1. **元思考发散**：GLM 提示词开头强制「先列举 3 种完全不同的市场微观结构视角，并估算年化容量，再择一深入」——用 Prompt 弥补辩论缺失。
2. **Alphalens 底线**：轻量适配器必须保留并执行 `winsorize`（去极值）与 `neutralize`（对市值/行业或加密代理暴露中性化），不可省略。
3. **因果反事实**：假设验证后必须跑 CausalImpact 风格反事实（剔除高波/高动量时段）；效应被洗掉 → 打回，不许挖因子。
4. **苏格拉底质询**：AutoGen 风格质疑者只追问不设计；逻辑连接点答不上来 → 打回。
5. **外部认知卡片**：创造前阅读微观真相卡片（RAG/蒸馏），失效视角禁止深入。
6. **多宇宙生存**：蒙特卡洛合成路径；仅少数宇宙盈利 → 强制加环境过滤或换视角。
7. 数字以 QuantOracle 为准；蓝图不含复核，不改 ADA5 路径。
8. **长历史走 R2/S3 研究仓**（`research_candle_store`），禁止把 formal 短窗当 2024YTD。
9. **初评胜率门禁（硬）**：胜率 < 50% → 禁止展示。
10. **收益硬度门禁（硬）**：周收益代理 ≥ 8%，收益/回撤 ≥ 1.0；因子多空周收益(带杠杆) < 3% → 丢弃。
11. **策略退化熔断（硬）**：暴露 < 10% / 单笔 < 1bp / 窗内总收益 < 1% → 换视角。

## 熔断

1. ②/⑤ 失败回溯 > 5 次 → 终止，返回无法构建报告  
2. IC 衰减过快 / 换手过高 → 丢弃因子  
3. QuantOracle/历史 VaR 超人类日损阈值 → 否决  
4. 周收益代理 < 8% / 收益回撤比 < 1.0 / 策略退化 → 换视角或经典变式  
5. 因子多空周收益(带杠杆) < 3% → 丢弃

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
