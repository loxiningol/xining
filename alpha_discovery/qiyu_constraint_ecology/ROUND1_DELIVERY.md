# 栖语约束生态网络 — 第一轮交付（Round 1）

**Qiyu Constraint Ecology**

- at: 2026-08-04 23:30 CST
- 范围：**机制宇宙 + 可观测性 + 自动机规范**（不是完整平台）
- `production_formal_pass_strategy_count`: **0**
- **禁止结论**：系统接近成功 / Route-0 优化即可 / 静态区可策略化

---

## 0. 锁定合同（单策略，不可修改）

| 项 | 值 |
|---|---|
| leverage | 20 |
| stop | 0.5% 价格 |
| win_rate | ≥ **0.65** |
| 盈利单杠杆净收益 | ≥ **0.1111**（含费，仅盈利单） |
| **每策略**周频 | ≥ **0.1**（不是组合最低频） |
| 自动创造 + Production Formal PASS | 必须 |

组合不能替单策略补考。周频 0.1 是**每条**策略门槛；组合质变来自大量**低相关、机制独立**策略的有效独立机会数，不是近克隆堆数量。

---

## 1. 负面对照（必须保留）

| 对照 | 结论 |
|---|---|
| **Region A / B** | 全样本可过合同数字，严格时间外崩溃 → `PHASE0_FALSE_POSITIVE` |
| 教训 | **静态状态区 ≠ 机制**；禁止再走「状态漂亮 → 策略化」 |

禁止机制化：RSI / 布林 / 均线 / 纯波动或趋势分位 / 纯时段 / K线形态 / 无约束链的 AI 故事。

---

## 2. 研究本体（六元 + 扩展）

每条假设必须填满：

`actor → constraint → pressure → mechanical_trigger → forced_action → observable_trajectory → liquidity_response → edge_payer → capture_window → failure_modes`

缺一不可进入事件发现。

世界观可用核心（非幻觉版）：

> 不预测所有人的自由选择；猎取参与者被制度/风险约束迫使行动、行为自由度短暂收缩的窗口。

---

## 3. 不超过 12 个机制假设（完整链摘要）

| ID | 母体 | 谁被迫 | 约束 | 强制动作 | 支付者 | 可观测性 |
|---|---|---|---|---|---|---|
| M01 | 清算保证金 | 近强平杠杆账户 | 维持保证金 | 市价强平 | 后至的被迫成交方 | **NOT_OBSERVABLE**（无连续强平回放） |
| M02 | 清算保证金 | 同 M01 | 同 | 级联延续强平 | 对手流动性 | **NOT_OBSERVABLE** |
| M03 | 清算保证金 | 全市场 | 外生冲击 | 恐慌+强平 | 未定义 | 分类门：只 Abstain |
| M04 | 流动性撤补 | 主动 taker | 必须立即成交 | 持续主动成交直至衰减 | 信息已入价后的晚到 aggressor | **PARTIAL**（telemetry ~35d） |
| M05 | 流动性撤补 | 做市/被动 LP | 逆选择与库存限额 | 撤报价/扩点差 | 穿越宽点差的紧急单 | **NOT_OBSERVABLE**（无历史 L2） |
| M06 | 流动性撤补 | 被动 LP | 风险限额延迟回补 | 延迟补量 | 错位中间价持仓者 | **NOT_OBSERVABLE** |
| M07 | 做市库存 | 做市商 | 库存带与报价倾斜 | 倾斜报价后回归/对冲 | 撞倾斜盘口的非知情流 | **NOT_OBSERVABLE** |
| M08 | 做市库存 | 做市商对冲 | 库存越界对冲紧迫 | 同向对冲市价 | 滞后对手盘 | **PARTIAL**（仅有 taker 代理） |
| M09 | 跨市资金 | 拥挤永续持仓 | funding 支付制度 | 减仓/翻仓停付 funding | 拥挤侧穿越出场 | **PARTIAL**（funding+OI 可拉） |
| M10 | 跨市资金 | 期现套利台 | 资本与保证金 | 多腿收敛单 | 打出 basis 的方向流 | PARTIAL；单腿 DSL 慎入 |
| M11 | 跨市资金 | ETH 侧风控/再平衡 | 相对 BTC 暴露约束 | ETH 追赶单 | 慢速再平衡者 | **PARTIAL**（多品种 K 线+流） |
| M12 | 制度时钟 | 赶 funding 结算的账户 | 固定结算时刻 | 结算窗调仓 | 被时刻表驱动的账户 | PARTIAL；**禁止把 EU/US 当时段机制** |

机器可读全文：`ROUND1_MECHANISM_UNIVERSE.json`

---

## 4. 数据可观测性审计（生产事实）

| 数据 | 状态 | 用途 |
|---|---|---|
| OHLCV research candles | AVAILABLE | **仅**路径结果与辅助；不得当机制主证据 |
| OI / Funding | PARTIAL | OKX 可拉；非全品种多年连续 Formal 就绪 |
| Taker / 微观窗口 | PARTIAL_FORWARD | `microstructure_telemetry.db` ~35 天留存；非连续历史回放 |
| 历史 L2 / microprice | **MECHANISM_NOT_OBSERVABLE** | 成本模型与 Windtalker 矩阵已声明无回放 |
| 连续强平流 | **MECHANISM_NOT_OBSERVABLE** | 无连续 liquidation replay |
| 跨所深度 | **MECHANISM_NOT_OBSERVABLE** | 无 |

缺必要数据 → 输出 `MECHANISM_NOT_OBSERVABLE`，禁止用 RSI/K 线伪装。

---

## 5. 事件自动机（统一骨架）

每个机制必须实例化：

```yaml
states: [DORMANT, ACCUMULATING, ARMED, FORCED_FLOW,
         ABSORPTION, EXHAUSTION, ACCELERATION, INVALIDATED, CAPTURE]
transition: ...
observables: ...
minimum_duration / maximum_duration: ...
negation_conditions: ...
external_shock_exclusion: ...
event_origin: [endogenous_buildup, exogenous_shock, mixed, unknown]
```

规则：
- **禁止**仅在静态状态 / 仅在 `ARMED` 预测入场；
- 默认只对 `endogenous_buildup` 做提前捕获；
- `unknown` 不准进策略创造；
- `exogenous_shock` → 默认 `ABSTAIN`（M03）。

捕获分支（繁殖维度，不是改参）：CONTINUATION / REVERSAL / CONVERGENCE / NO_TRADE。

---

## 6. 黄金事件与反事实标注规范

每个进入小规模验证的机制，先标（验证**事件定义**，非训最终模型）：

| 类型 | 数量目标 | 定义 |
|---|---|---|
| 正事件 treated | 20–50 | 完整约束链可观测 + endogenous |
| 反事实 control | 20–50 | 表面相似（波动/形态/时段）但缺强制动作链 |
| 失败事件 | 20–50 | 链启动但 RESOLUTION≠预期 |
| 外生冲击 | 尽量全 | 无 ACCUMULATING 的跳空/新闻型 |

每对必须填：

`treated_event / matched_control / surface_similarity / mechanism_difference / path_outcome_difference`

强制实验：时间平移、假触发、block permutation、相似波动对照、相似 K 线对照、跨币对照、外生排除。  
实验组与对照无稳定路径差异 → **淘汰机制**。

---

## 7. 淘汰规则（铁律摘要）

1. 无机制证据不创造策略  
2. 无触发链不交易静态状态  
3. 分不清内生/外生不交易  
4. 无反事实差异不升级  
5. 一个时间块失败，不用参数救活  
6. 谱系连续失败整支冻结  
7. 允许长期空仓；单策略周频≥0.1 即可  
8. 不因已投入工程保留路线  
9. Formal PASS 前一律叫研究证据  
10. Region A/B 式伪发现永久负面对照  

---

## 8. Top 3（唯一建议进入小规模验证）

在 **可观测性 ≠ NOT_OBSERVABLE** 且有完整约束链的集合中选取：

### ① M09 — Funding 拥挤后强制减仓/平仓流
- **为何比 Route-0 更可能**：支付者与触发是**制度时钟+拥挤成本**，不是图形状态；可用 funding+OI 直接观测压力变量。  
- **仍无证据**：严格 OOS 合同可行域、反事实双胞胎、近两年稳定、Formal PASS — **全部尚未做**。  
- **风险**：趋势压过 carry；历史对齐不足。

### ② M04 — 主动流压力后的吸收/耗尽
- **为何**：对象是**订单流轨迹**（ACCUMULATING→EXHAUSTION），不是 vol/trend 分位；telemetry 有 taker 窗口可做短样本事件研究。  
- **仍无证据**：35 天留存不够 Formal；需证明相对 BTC-beta / 时段安慰剂的增量。  
- **风险**：把短样本当机制；重演 Region 伪发现。

### ③ M11 — BTC 冲击后 ETH 被迫追赶
- **为何**：约束是**相对暴露/再平衡**，强制动作是 ETH 侧订单，可用多品种路径做 lead-lag + 流确认。  
- **仍无证据**：稳定 lead-lag、排除“只是共同行情”、OOS 合同域、Formal。  
- **风险**：最易退化成时段/beta 伪相关（正是 Region A/B 教训）。

**明确不进 Top3**：M01/M02/M05/M06/M07（缺强平或 L2 回放 → NOT_OBSERVABLE）；M10（多腿物种，先独立）；M12（易 degenerates 成时段）；M03（只 Abstain）。

---

## 9. 为何这不是 Route-0 换皮

四个倒置（路线定义，**非成功保证**）：

1. 价格预测 → **参与者约束读取**  
2. 静态特征 → **事件轨迹自动机**  
3. 单策略优化 → **机制谱系繁殖**（须先有 1 条 OOS 合格才复制）  
4. 策略数量 → **有效独立机会数**

Route-0 / Phase0 静态区已证明：样本内合同数字 ≠ 机制存在。

---

## 10. 第一轮明确不交付

- 不建大规模平台  
- 不发明 RSI/布林策略  
- 不提交 Formal  
- 不宣称接近成功  
- 不在 0 条 OOS 合格时繁殖 100 变体  

下一步若继续：仅对 Top3 做「数据对齐 → 黄金事件标注 → 反事实 → 严格时间外轨迹」，**仍不是 Formal 成功**。

---

## 最终成功标准（重申）

唯一成功：`production_formal_pass_strategy_count > 0`，且每条独立满足  
20× / 0.5%止损 / WR≥0.65 / 盈利单净≥11.11% / 周频≥0.1。

当前：**0。研究证据阶段。**
