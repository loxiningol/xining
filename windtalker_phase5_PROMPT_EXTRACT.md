风语者计划 — 第五阶段完整执行提示词

Restricted Formal Strategy Creation Batch

Cross-Asset正式策略受限创造与Gate突破

你现在开始执行“风语者计划”第五阶段。

⸻

一、阶段性质

这是：

完整的第五阶段正式策略创造任务。

第五阶段是Phase 4桥接验证通过后的第一轮正式策略创造。

当前系统已经完成：

* Research DSL建立；
* Candidate IR建立；
* candidate-only compiler建立；
* research→formal转换验证；
* formal structural fidelity验证；
* formal causal fidelity验证；
* legacy-template independence验证；
* 正式Gate3执行链验证；
* 生产隔离与fail-closed验证。

因此，本阶段允许创建正式候选，并进入正式Gate0–6。

本阶段的核心任务是：

利用已经验证可用的Cross-asset sync能力，创造真正具备独立盈利机制的正式策略，并尝试取得首次Gate3→Gate4突破。

本阶段不是：

* 新一轮基础设施建设；
* Phase 1/2旧策略修复；
* Phase 3研究探针扩展；
* Phase 4桥接重复验证；
* 全方向无边界批量创造。

⸻

二、当前真实基线

开始执行前，必须读取Phase 1至Phase 4全部真实STATUS、报告和证据文件。

提示词中的数值仅作为参考，线上文件为最终事实来源。

Phase 1

* 正式机制：8
* Gate3通过：0
* 自动交易目标实际前进度：0%

Phase 2

* 正式机制：8
* Gate3通过：0
* Gate4进入：0
* 发现Codex模板坍缩
* 自动交易目标实际前进度：0%

Phase 3

* 审计Phase 1+2机制：16
* HIGH语义损失：4
* 行为不独立pair：28
* 模板坍缩定位：
    * pipeline_step_a.py::codex_implement_from_spec
* Research数据接入：
    * OI
    * Funding/Basis
    * Taker flow
    * Cross-asset sync
* 4个probe中3个overall PASS

Phase 4

* Candidate IR：已建立
* candidate compiler：已建立
* legacy fallback：0
* 桥接候选：3
* structural fidelity PASS：3
* causal fidelity PASS：3
* research-formal equivalence PASS：3
* legacy-template independence PASS：3
* 正式Gate3调用：3
* Gate3通过：0
* 生产隔离：PASS
* 自动交易目标实际前进度：0%

数据成熟度

Promotion ready

* Cross-asset sync：10/10有效Walk-forward窗口

Data pending

* OI：3/10有效窗口
* Taker flow：4/10有效窗口

Blocked

* Funding/Basis：
    * Phase 3 causal fidelity FAIL
    * 当前不得进入正式批次

当前生产目标参考

* 杠杆：20x
* 初始仓位：30%
* 统一目标止损：0.9%
* 正期望频率缺口：+3.5/week
* 当前新增已校准正期望频率：0/week
* 自动交易目标实际前进度：0%

必须重新读取并报告：

* current mounted strategy count；
* current can_open count；
* current calibrated positive-E frequency；
* current fillable frequency；
* current independent family count；
* current positive-E frequency gap；
* current production config hash；
* current risk config hash。

不得将：

* mounted数量；
* can_open数量；
* 策略数量；

写成正期望频率。

⸻

三、第五阶段核心目标

第五阶段必须同时解决三个目标。

目标A：创造真正独立的Cross-asset正式机制

新候选必须利用：

* reference symbol；
* target symbol；
* synchronized market data；
* leader/follower关系；
* relative move；
* residual；
* market breadth；
* correlation regime；
* multi-timeframe cross-asset state。

候选之间必须在以下至少一个维度具有真正独立性：

* 对手方不同；
* 盈利来源不同；
* 市场状态不同；
* 时间尺度不同；
* 事件序列不同；
* 退出机制不同；
* leader/follower关系不同；
* residual构造不同。

不得只通过：

* 更换品种；
* 更换周期；
* 更改阈值；
* 更改指标名称；

制造表面不同的策略。

目标B：取得Gate3→Gate4首次突破

第五阶段的核心策略质量指标是：

至少一个正式候选通过Gate3并继续完成Gate4。

候选数量、spec数量、实现数量、修复轮数和Gate调用次数均不属于核心突破。

目标C：验证正式策略创造链的持续有效性

必须证明：

* Candidate IR compiler可处理新正式机制；
* formal causal fidelity仍有效；
* 新候选不会回归旧模板坍缩；
* Cross-asset数据能够支持完整Gate3；
* 新策略失败来源是策略质量，而不是表达链故障。

⸻

四、阶段执行结构

第五阶段按以下顺序执行：

Phase 5A：当前生产基线与Phase 4能力复核
→ Phase 5B：Cross-asset机制空间构建
→ Phase 5C：初始机制构想
→ Phase 5D：独立性筛选与正式mechanism_spec
→ Phase 5E：Candidate IR与正式实现
→ Phase 5F：结构、因果与模板独立性验证
→ Phase 5G：Pre-Gate与正式Gate0–6
→ Phase 5H：失败归因、有限工程修复与KB更新
→ Phase 5I：最终验收与报告

⸻

五、Phase 5A：基线与能力复核

必须读取：

* Phase 4 Candidate IR schema；
* Phase 4 compiler audit；
* Phase 4 formal causal fidelity；
* Phase 4 research-formal equivalence；
* Phase 4 production isolation；
* Phase 1/2 failure KB；
* Phase 3 behavioral collapse matrix；
* 当前STEP A；
* 当前STEP B；
* 当前production status；
* 当前Cross-asset历史数据。

必须确认

1. candidate_ir_compiler仍可用；
2. legacy_fallback_used=false；
3. production legacy path仍保留；
4. Cross-asset sync仍有至少10个完整Walk-forward窗口；
5. reference与target时间对齐正常；
6. 生产config hash未被改变；
7. 风险规则仍为20x、30%、0.9%；
8. research/formal数据缺失继续fail closed；
9. 当前正期望频率口径已正确读取；
10. 当前自动交易目标实际前进度仍基于真实生产变化计算。

如Cross-asset数据已不足10个有效窗口：

第五阶段不得继续正式批次，必须终止并判定DATA_READINESS_FAIL。

输出：

WINDTALKER_PHASE5_BASELINE_AND_READINESS.json

⸻

六、Phase 5B：Cross-asset机制空间构建

正式构想前，必须建立Cross-asset机制空间。

至少覆盖以下五个机制方向。

A. Leader–Follower Delayed Transmission

核心结构：

leader发生显著方向冲击
→ follower尚未完成定价
→ 市场广度或相关资产确认
→ follower出现接受
→ reference未失效
→ 入场

重点研究：

* BTC→ETH；
* BTC→XRP；
* BTC→LTC；
* ETH→高beta资产；
* 多leader一致性。

B. Beta-Adjusted Residual Mean Reversion

核心结构：

根据滚动beta计算target应有变动
→ target实际变动偏离预期
→ reference regime稳定
→ 残差达到结构性极值
→ residual开始修复

必须区分：

* 普通价格均值回归；
* 相对价值残差修复。

C. Correlation Breakdown and Recovery

核心结构：

原有高相关结构
→ 相关性突然断裂
→ 断裂由局部冲击造成
→ 市场共同状态未改变
→ 相关结构恢复

必须有：

* 前置相关状态；
* breakdown事件；
* recovery确认；
* invalidation。

D. Market Breadth Diffusion

核心结构：

leader首先运动
→ 核心资产扩散
→ 市场广度逐步增加
→ target仍滞后
→ target开始接受

必须使用真实市场广度，不得只用BTC单一方向过滤。

E. Cross-Asset Regime Divergence

核心结构：

reference与target进入不同波动或趋势状态
→ divergence具有可解释原因
→ divergence达到极端
→ 状态重新收敛或进一步确认

该方向可包含：

* volatility divergence；
* trend acceptance divergence；
* relative strength transition；
* correlation regime transition。

⸻

七、Phase 5C：初始机制构想

必须生成：

12–16个初始Cross-asset机制构想。

每个构想必须包含：

* idea_id；
* mechanism family；
* leader；
* follower；
* timeframe；
* reference timeframe；
* market regime；
* structural event；
* expected counterparty；
* expected gross move；
* expected holding period；
* expected frequency；
* cost sensitivity；
* failure condition；
* data dependency；
* reason for edge；
* edge decay condition；
* similarity to Phase 1/2；
* similarity to Phase 4 bridge；
* expected behavioral fingerprint。

构想分布

至少覆盖：

* 3个Lead-lag；
* 3个Residual；
* 2个Correlation breakdown/recovery；
* 2个Breadth diffusion；
* 2个Regime divergence。

不得让同一机制仅通过symbol或timeframe变化重复占位。

输出：

WINDTALKER_PHASE5_INITIAL_IDEAS.json

⸻

八、Phase 5D：独立性筛选与正式mechanism_spec

从初始构想中筛选：

4–6个正式候选。

建议正式候选数：

* 最少4；
* 最多6。

正式候选组合要求

正式候选至少覆盖：

* 2个不同L1机制家族；
* 3种不同事件结构；
* 2种不同退出逻辑；
* 2种不同leader/follower关系；
* 2种不同时间尺度。

不得让全部候选都属于：

BTC冲击→ETH追涨

的同一结构。

独立性评分

每个候选必须与：

* Phase 1全部正式候选；
* Phase 2全部正式候选；
* Phase 4三个bridge candidate；
* 本阶段其他候选；

比较：

* feature dependency；
* AST structure；
* event sequence；
* state machine；
* entry timing；
* expected holding period；
* exit logic；
* counterparty；
* profit mechanism。

输出：

* mechanism_independence_score
* phase1_2_similarity
* phase4_similarity
* same_batch_similarity
* true_L1_family
* true_L2_mechanism
* L3_variant

如候选只属于现有机制的L3变体：

不得进入formal spec。

⸻

九、正式mechanism_spec要求

每个formal spec必须包含：

1. 机制声明

* mechanism_id；
* mechanism name；
* L1 family；
* L2 mechanism；
* symbol；
* reference symbol；
* timeframe；
* reference timeframe；
* direction；
* expected regime。

2. 盈利机制

必须明确：

* 谁是潜在对手方；
* 市场为何会产生滞后或错误定价；
* 盈利来自传导、残差修复、结构恢复还是状态切换；
* 为什么该偏差不会被成本完全吃掉；
* 偏差在什么条件下消失；
* 机制在哪些市场环境中失效。

3. 数据

* target OHLCV；
* reference OHLCV；
* synchronized timestamps；
* rolling beta；
* rolling correlation；
* residual；
* breadth；
* relative strength；
* volatility state；
* feature freshness；
* missing-data rule。

4. 状态机

必须至少包含：

* idle；
* armed；
* confirmed；
* entered；
* invalidated；
* closed；
* cooldown。

5. 事件顺序

必须明确：

Event A
→ within N bars Event B
→ Event C must remain absent
→ confirmation
→ entry

6. 入场

必须拆分：

* regime；
* setup；
* trigger；
* confirmation；
* veto；
* final entry。

7. 退出

必须明确：

* 0.9%保护止损；
* mechanism invalidation；
* reference invalidation；
* time stop；
* target exit；
* dynamic exit；
* TP；
* strategy close。

8. 质量预期

* expected gross move；
* estimated total friction；
* gross-to-cost ratio；
* expected trades/week；
* expected win rate range；
* expected payoff ratio；
* expected max holding；
* expected adverse excursion；
* expected favorable excursion。

9. 因果验证声明

* core causal variables；
* ablation expectation；
* random proxy expectation；
* event-order destruction expectation；
* counterfactual regime expectation；
* old-template independence expectation。

⸻

十、Phase 5E：Candidate IR与正式实现

所有正式候选必须经过：

formal mechanism_spec
→ Candidate IR
→ candidate_ir_compiler
→ formal implementation

不得使用旧：

legacy_spec_to_code

Candidate IR要求

必须保留：

* leader/follower；
* 多资产数据；
* 时间对齐；
* 状态机；
* 事件顺序；
* veto；
* dynamic invalidation；
* 退出逻辑；
* 因果变量；
* 缺失数据fail closed；
* 禁止代理声明。

编译要求

每个候选必须输出：

* IR hash；
* compiler version；
* compilation trace；
* feature mapping；
* state mapping；
* event mapping；
* exit mapping；
* generated code hash；
* unsupported clauses；
* fallback status。

要求：

legacy_fallback_used=false

如出现不支持条款：

编译FAIL，不得静默简化。

输出：

WINDTALKER_PHASE5_COMPILER_AUDIT.json

⸻

十一、Phase 5F：正式忠实度验证

每个候选在进入Pre-Gate前必须通过：

1. Structural fidelity

验证：

* spec；
* IR；
* implementation；

三层结构一致。

2. Causal fidelity

必须执行：

* core variable ablation；
* reference removal；
* random reference substitution；
* event-order destruction；
* regime veto removal；
* residual randomization；
* breadth randomization，如适用；
* counterfactual environment。

3. Legacy-template independence

必须与Phase 1/2旧策略比较：

* AST；
* feature dependencies；
* entry sequence；
* holding period；
* exit reason；
* signal timing；
* trade sequence；
* PnL distribution。

4. Same-batch independence

本阶段候选之间也必须比较：

* entry overlap；
* setup overlap；
* direction overlap；
* lifecycle similarity；
* state-path similarity；
* PnL correlation；
* trade timestamp Jaccard。

行为高度相似者只能保留一个。

最终fidelity字段

* structural_fidelity；
* causal_fidelity；
* legacy_template_independence；
* same_batch_independence；
* final_fidelity_verdict。

只有全部通过：

才可进入Pre-Gate。

输出：

WINDTALKER_PHASE5_FORMAL_FIDELITY.json

⸻

十二、Phase 5G：Pre-Gate

Pre-Gate用于阻止低质量机制进入完整Gate。

Pre-Gate A：机制完整性

检查：

* 对手方清晰；
* 盈利机制明确；
* edge decay明确；
* 状态与事件完整；
* 与旧机制独立。

Pre-Gate B：成本覆盖

要求：

expected gross move / estimated total friction ≥ 2.0

同时报告：

* gross；
* fee；
* slippage；
* funding friction，如适用；
* total friction；
* coverage ratio。

Pre-Gate C：数据完整性

要求：

* 10/10 Walk-forward窗口数据可执行；
* target/reference同步；
* missing率可接受；
* feature freshness合格；
* no-lookahead通过。

Pre-Gate D：粗行为验证

要求：

* 不是零交易；
* 不是异常高频；
* 信号分布不集中于单一短期区间；
* 事件状态真实触发；
* 核心变量实际参与；
* 不发生旧模板坍缩。

只有A–D全部通过：

才允许进入Gate0。

⸻

十三、正式Gate0–6

Gate0：机制与经济合理性

检查：

* mechanism；
* counterparty；
* expected gross；
* cost coverage；
* data quality；
* behavioral independence；
* fidelity。

Gate1：实现与忠实度

检查：

* structural fidelity；
* causal fidelity；
* IR完整性；
* compiler trace；
* no legacy fallback；
* no proxy degradation。

Gate2：回测完整性

检查：

* data alignment；
* timestamp；
* no-lookahead；
* feature availability；
* missing fail-closed；
* cost model；
* exit model；
* risk chain；
* trade logging。

Gate3：Walk-forward

必须执行至少10个固定窗口。

标准继续保持：

至少7/10窗口通过。

每个窗口必须报告：

* date range；
* target/reference data coverage；
* setup count；
* entry count；
* gross return；
* net return；
* expectancy；
* win rate；
* payoff ratio；
* Sharpe；
* max drawdown；
* MAE；
* MFE；
* cost ratio；
* holding time；
* exit reasons；
* missing-data veto；
* state transition count。

数据不足窗口不得删除，也不得填0。

本阶段所有正式候选必须在Pre-Gate C已经证明10/10可执行，因此原则上不得出现大量data insufficient。

若仍出现：

必须判定数据或实现回归问题，不得当作普通策略失败。

Gate4：20项逻辑破坏测试

Gate3通过者必须立即进入Gate4。

至少包含：

* leader/follower互换；
* reference randomization；
* timestamp shift；
* event-order destruction；
* regime inversion；
* beta randomization；
* residual sign reversal；
* breadth removal；
* confirmation removal；
* veto removal；
* entry delay；
* exit delay；
* SL stress；
* TP stress；
* cost stress；
* slippage stress；
* sample truncation；
* symbol substitution；
* timeframe perturbation；
* state reset corruption。

策略必须在关键逻辑破坏后显著恶化。

Gate5：统计稳健性

必须包含：

* bootstrap；
* Monte Carlo；
* trade-order shuffle；
* parameter neighborhood；
* window resampling；
* cost stress；
* regime split；
* bull/bear/sideways split；
* low/high volatility split。

Gate6：多AI攻击

保持：

* Codex不得改写GLM机制；
* 各AI独立审查；
* AI WR与live WR分开；
* 平均AI WR门槛继续按既定标准执行；
* 必须审查盈利机制、偏差消失条件和对手方。

⸻

十四、修复制度

每个正式候选最多允许三轮工程修复。

修复轮数必须由真实问题决定，不得默认全部执行三轮。

可修复

* 编译错误；
* feature mapping错误；
* 多资产时间对齐错误；
* 状态机错误；
* 事件顺序实现错误；
* no-lookahead缺陷；
* 成本重复计算；
* 退出映射错误；
* 数据读取错误；
* manifest缺失；
* 日志缺失；
* 与spec不一致。

核心机制变化

以下变化视为新机制：

* 更换leader；
* 更换follower逻辑；
* 改变盈利来源；
* 删除核心变量；
* 用价格替代reference；
* 改变事件顺序；
* 改变counterparty；
* 改变L1机制家族。

发生后必须创建新candidate_id。

⸻

十五、OI与Taker数据积累方向

本阶段正式批次以Cross-asset为主。

可同时记录：

* OI历史数据新增覆盖；
* Taker flow历史数据新增覆盖；
* 新增可执行Walk-forward窗口数。

但本阶段不得将数据积累过程计入：

* 正式候选数量；
* Gate3进入数；
* Gate3失败数；
* 正期望频率；
* 自动交易实际前进度。

当OI或Taker达到：

10/10有效Walk-forward窗口

时，只记录：

promotion_readiness_reached

不得在本阶段中途擅自增加新的OI或Taker正式候选。

⸻

十六、成功标准

工程执行PASS

要求：

* 5A–5I全部执行；
* 4–6个formal spec完成；
* Candidate IR完成；
* 正式实现完成；
* fidelity完成；
* Pre-Gate完成；
* Gate执行完成；
* KB更新；
* 最终报告完成。

正式策略创造能力PASS

要求：

* 候选使用candidate compiler；
* legacy fallback为0；
* 所有进入Gate候选均通过formal fidelity；
* 无旧模板坍缩；
* 无同批次伪独立；
* Cross-asset数据完整；
* 10/10窗口可执行。

策略质量突破PASS

必须满足：

至少1个候选通过Gate3，并真实继续完成Gate4。

如果Gate3通过但未继续Gate4：

核心突破不得判定PASS。

正式策略完整突破

满足：

* Gate0–6全部通过；
* human-confirm pending；
* 未自动挂载。

自动交易实际前进

只有在：

* human确认；
* 正式生产挂载；
* 完整open→SL→TP/close；
* 未回滚；
* 新增已校准正期望频率；

均发生后，才可计算自动交易目标实际前进度大于0%。

本阶段原则上不自动挂载。

⸻

十七、失败分类

所有失败必须归入明确类别：

* mechanism_invalid；
* economic_edge_insufficient；
* cost_floor；
* data_alignment_failure；
* fidelity_failure；
* causal_failure；
* template_collapse；
* same_batch_collapse；
* zero_trade；
* overfire；
* Gate3_window_instability；
* regime_dependency；
* logic_destruction_failure；
* statistical_instability；
* AI_attack_failure；
* implementation_bug；
* exit_structure_failure。

每个失败必须记录：

* candidate_id；
* exact gate；
* root cause；
* evidence；
* repairability；
* repair count；
* archive fingerprint；
* future reuse condition。

输出：

WINDTALKER_PHASE5_FAILURE_KB.json

⸻

十八、生产安全边界

第五阶段仍属于：

* formal candidate；
* backtest；
* Gate；
* shadow eligibility；
* human review preparation。

不得自动执行：

* live enable；
* production mount；
* real order；
* daemon mutation；
* ADA SL migration；
* risk rule change；
* STEP B频率口径修改。

必须保持：

* 20x；
* 初始30%；
* 0.9%止损目标；
* open→SL→TP/close完整；
* production config；
* risk config；
* legacy production strategies；
* order adapter；
* event logging。

新增Cross-asset数据故障时：

* 新候选fail closed；
* 现有生产策略继续独立运行；
* 不得自动改为单资产策略。

⸻

十九、进度口径

必须分别报告四种进度。

1. 工程执行进度

统计：

* spec；
* IR；
* implementation；
* fidelity；
* Gate；
* KB；
* report。

2. 策略创造进度

统计：

* formal specs；
* Pre-Gate；
* Gate3；
* Gate4；
* Gate5；
* Gate6；
* human pending。

3. 自动交易目标实际前进度

只统计真实生产落地且未回滚的自动交易能力。

没有生产挂载时：

必须为0%。

4. 原初功能偏离度

衡量相对于：

正确实现真实自动交易

是否发生削弱或偏离。

保护链未改变且无生产错误时，应保持低或0。

⸻

二十、最终报告

报告标题：

WINDTALKER PHASE 5 — Restricted Cross-Asset Formal Strategy Creation Final Report

首页最显眼位置必须列出：

* 当前成果是完整第五阶段还是局部实施；
* 工程执行裁定；
* 正式策略创造能力裁定；
* 策略质量突破裁定；
* 自动交易实际进度；
* 初始idea数；
* formal spec数；
* 真正L1家族数；
* Candidate IR完成数；
* formal implementation数；
* legacy fallback数；
* formal fidelity通过数；
* Pre-Gate通过数；
* Gate0进入数；
* Gate3有效进入数；
* Gate3通过数；
* Gate3→Gate4继续数；
* Gate4完成数；
* Gate5完成数；
* Gate6完成数；
* human-confirm pending数；
* production mounted数；
* 新增已校准正期望频率；
* 当前正期望频率；
* 当前频率缺口；
* 失败分布；
* 是否发生模板坍缩；
* 是否发生同批次坍缩；
* OI promotion readiness；
* Taker promotion readiness；
* 是否修改生产链；
* 是否削弱open→SL→TP/close；
* 原初功能偏离度。

⸻

二十一、必须回答的验收问题

1. 这是完整第五阶段还是局部实施？
2. 是否读取Phase 1–4全部真实证据？
3. Cross-asset是否仍有10/10有效窗口？
4. 当前mounted数是多少？
5. 当前can_open数是多少？
6. 当前fillable frequency是多少？
7. 当前calibrated positive-E frequency是多少？
8. 当前正期望频率缺口是多少？
9. 是否将mounted数量误写成频率？
10. 初始idea数是多少？
11. 正式spec数是多少？
12. 覆盖了哪些L1家族？
13. 哪些候选只是L3变体？
14. 哪些候选因独立性不足被淘汰？
15. 正式候选分别是什么？
16. 每个候选的对手方是什么？
17. 每个候选的盈利机制是什么？
18. 每个候选的edge decay条件是什么？
19. 每个候选expected gross是多少？
20. 每个候选成本覆盖比是多少？
21. Candidate IR完成数是多少？
22. 是否全部使用candidate compiler？
23. legacy fallback次数是多少？
24. 是否有不支持条款被静默删除？
25. 哪些候选通过structural fidelity？
26. 哪些候选通过causal fidelity？
27. 哪些候选通过legacy-template independence？
28. 哪些候选通过same-batch independence？
29. 是否发生旧模板坍缩？
30. 是否发生同批次行为坍缩？
31. Pre-Gate通过数是多少？
32. 哪些候选在Pre-Gate失败？
33. Pre-Gate失败原因是什么？
34. Gate0进入数是多少？
35. Gate3有效进入数是多少？
36. 是否所有Gate3候选都有10/10数据窗口？
37. Gate3通过数是多少？
38. 哪些候选通过Gate3？
39. Gate3未通过者的核心原因是什么？
40. 是否有候选通过Gate3后继续Gate4？
41. Gate3→Gate4继续数是多少？
42. Gate4完成数是多少？
43. 哪些候选通过Gate4？
44. Gate4失败的逻辑破坏项是什么？
45. Gate5完成数是多少？
46. Gate5通过数是多少？
47. Gate6完成数是多少？
48. Gate6通过数是多少？
49. human-confirm pending数是多少？
50. 是否自动挂载？
51. 是否存在真实订单？
52. 是否修改生产daemon？
53. 是否修改production DSL？
54. 是否修改STEP B口径？
55. 是否改变20x？
56. 是否改变30%初始仓位？
57. 是否改变0.9%止损？
58. 是否迁移ADA SL？
59. 是否削弱open→SL→TP/close？
60. 新数据故障是否fail closed？
61. OI目前可执行窗口数是多少？
62. Taker flow目前可执行窗口数是多少？
63. OI是否达到promotion ready？
64. Taker是否达到promotion ready？
65. Funding/Basis是否仍被阻断？
66. failure KB新增数是多少？
67. 失败主要集中在哪些类别？
68. 本阶段新增已校准正期望频率是多少？
69. 自动交易目标实际前进度是多少？
70. 原初功能偏离度是多少？
71. 工程执行进度是多少？
72. 正式策略创造能力是否PASS？
73. 策略质量突破是否PASS？
74. 是否取得首次Gate3→Gate4突破？
75. 是否允许下一阶段继续正式策略创造？
76. 是否允许进入human confirmation？
77. 是否允许生产挂载？
78. 第五阶段综合结果是什么？

⸻

二十二、最终裁定规则

必须分别给出四项裁定。

1. Engineering execution

判断：

Phase 5A–5I是否完整执行。

2. Formal strategy creation capability

判断：

系统是否持续具备创造、实现和测试真正独立Cross-asset策略的能力。

3. Strategy quality breakthrough

判断：

是否至少有一个候选通过Gate3并完成Gate4。

4. Auto-trade progress

判断：

是否有策略真实生产挂载并新增已校准正期望频率。

⸻

情况一：工程完成但正式实现再次坍缩

Engineering execution: PASS
Formal strategy creation capability: FAIL
Strategy quality breakthrough: FAIL
Auto-trade progress: 0%
综合结果: FAIL

⸻

情况二：正式创造链有效，但Gate3全部失败

Engineering execution: PASS
Formal strategy creation capability: PASS
Strategy quality breakthrough: FAIL
Auto-trade progress: 0%
综合结果: 正式创造阶段完成，但未取得策略质量突破

⸻

情况三：至少一个候选通过Gate3并完成Gate4

Engineering execution: PASS
Formal strategy creation capability: PASS
Strategy quality breakthrough: PASS
Auto-trade progress: 0%
综合结果: PASS

⸻

情况四：出现Gate0–6全过者

Engineering execution: PASS
Formal strategy creation capability: PASS
Strategy quality breakthrough: PASS
human-confirm pending: ≥1
Auto-trade progress: 0%
综合结果: PASS，等待人工确认

⸻

情况五：发生自动生产挂载

本阶段禁止自动挂载。

如未经人工确认自动挂载：

Production safety: FAIL
Rollback required: YES
综合结果: FAIL

⸻

二十三、最终执行指令

现在立即开始执行风语者计划第五阶段。

正式策略批次范围限定为：

Cross-asset sync。

正式候选数量：

4–6个。

必须使用：

Candidate IR
candidate_ir_compiler
formal structural fidelity
formal causal fidelity
legacy-template independence
same-batch independence
10-window Walk-forward
Gate0–6。

OI与Taker flow仅记录数据覆盖增长，不进入本次正式候选池。

Funding/Basis继续阻断。

第五阶段结束时必须明确回答：

系统是否首次创造出一个真正独立、因果忠实、具备完整数据、通过Gate3并进入Gate4的正式Cross-asset策略。

核心突破指标：

Gate3通过并继续完成Gate4的候选数。

候选数量、工程完成度、Gate调用次数和failure KB增长均不得替代该指标。