<timestamp>Tuesday, Jul 28, 2026, 9:34 PM (UTC+8)</timestamp>
<user_query>
风语者计划 — 第四阶段完整执行提示词

Research-to-Formal Promotion Bridge

研究机制向正式候选链桥接验证

你现在开始执行“风语者计划”第四阶段。

⸻

一、阶段性质

这是：

完整的风语者第四阶段桥接验证任务。

本阶段不是第三批正式策略批量创造。

本阶段也不是继续扩展研究数据、继续搭建DSL、继续设计探针或继续修复Phase 1/2旧策略。

第三阶段已经证明：

* 研究层已接入OI、Funding/Basis、Taker flow和Cross-asset sync；
* 研究DSL支持状态机、事件序列、多资产和动态退出；
* 结构忠实度和因果忠实度已经建立；
* 4个研究探针中有3个overall PASS；
* 研究探针没有再次坍缩为普通K线模板；
* 生产交易链未被修改。

但第三阶段没有证明：

正式STEP A候选实现链能够完整承载这些新机制。

当前明确存在以下断点：

research mechanism
→ research DSL
→ causal fidelity PASS

尚未被证明可以安全转换为：

formal mechanism_spec
→ formal candidate IR
→ formal implementation
→ STEP A Gate0–6

尤其是：

* 模板坍缩根因定位于codex_implement_from_spec；
* Phase 3没有修改正式STEP A；
* production DSL未接入研究DSL能力；
* research DSL禁止live_enabled；
* 新数据只在research/shadow/probe层使用。

因此第四阶段的唯一核心任务是：

建立并验证研究机制向正式候选实现链的受控桥梁，证明正式候选不会在转换过程中重新发生语义坍缩。

⸻

二、当前基线

必须重新读取Phase 1、Phase 2、Phase 3的真实报告和证据。

提示词中的静态基线仅作定位参考，线上文件为最终事实来源。

当前参考基线：

Phase 1

* 正式机制：8
* Gate3通过：0
* failure KB新增：25
* 自动交易目标实际前进度：0%

Phase 2

* 正式机制：8
* 实际Gate3通过：0
* Gate4进入：0
* 自动交易目标实际前进度：0%
* 发现shared cost-floor attractor after Codex template collapse

Phase 3

* 审计机制：16
* HIGH语义损失：4
* 行为不独立pair：28
* 坍缩主层：codex_implement_from_spec
* 研究数据类：
    * OI
    * Funding/Basis
    * Taker flow
    * Cross-asset sync
* 研究探针：4
* overall PASS：3
* 通过的研究能力：
    * OI
    * Cross-asset sync
    * Taker flow
* 未通过因果忠实度：
    * Funding/Basis
* production DSL未修改
* STEP A未修改
* research DSL不可live_enabled
* 自动交易目标实际前进度：0%

当前正期望频率缺口参考值：

+3.5/week

必须从线上重新读取：

* forecast；
* STEP B creation input；
* current STATUS；
* mechanism KB；
* Phase 3 probe artifacts；
* production config hash；
* risk config hash。

⸻

三、第四阶段核心目标

第四阶段必须解决四个问题。

目标A：建立Research-to-Formal桥接层

建立一条明确、可审计、可回滚的转换链：

research probe/spec
→ research DSL AST
→ candidate-grade intermediate representation
→ formal mechanism_spec
→ formal candidate implementation
→ formal STEP A Gate

不得：

* 直接把research DSL设置为live；
* 直接让production daemon读取research probe；
* 直接复制探针代码到生产；
* 继续依赖旧Codex family模板进行降维翻译。

目标B：保持转换前后语义一致

桥接后的正式候选必须保持：

* 核心盈利机制；
* 对手方；
* 数据依赖；
* 事件顺序；
* 状态机；
* 入场条件；
* 失效条件；
* 退出逻辑；
* 因果变量；
* 代理变量声明。

不得出现：

Research：OI扩张 + 价格停滞 + taker衰减
Formal：RSI超买 + 长上影

也不得出现：

Research：BTC lead-lag
Formal：ETH单资产趋势突破

目标C：正式候选必须保持因果忠实度

正式转换后不得只做字段级fidelity。

必须重新执行：

* 核心变量消融；
* 随机代理替换；
* 事件顺序破坏；
* regime veto；
* 行为独立性；
* research/formal交易行为等价性。

目标D：证明正式STEP A可以真实执行新机制

至少要证明：

* 新数据在候选研究回测层可读取；
* 正式候选不会退化为旧OHLC模板；
* Gate0–3能够执行；
* Gate3结果来自正式候选实现；
* 新数据故障时fail closed；
* 生产交易链仍完全隔离。

⸻

四、阶段执行结构

第四阶段按照以下顺序执行：

Phase 4A：Phase 3证据与数据覆盖复核
→ Phase 4B：桥接架构与Candidate IR建立
→ Phase 4C：正式实现器隔离升级
→ Phase 4D：三个桥接候选生成
→ Phase 4E：转换语义等价验证
→ Phase 4F：正式因果忠实度验证
→ Phase 4G：STEP A Gate0–6执行
→ Phase 4H：生产隔离、回归与Rollback
→ Phase 4I：桥接能力验收和最终报告

不得在4A–4C完成前开始批量正式候选实现。

⸻

五、Phase 4A：Phase 3证据与数据覆盖复核

必须重新读取：

* WINDTALKER_PHASE3_SPEC_TO_CODE_SEMANTIC_MAP.json
* WINDTALKER_PHASE3_COLLAPSE_ROOT_CAUSE.json
* WINDTALKER_PHASE3_BEHAVIORAL_COLLAPSE_MATRIX.json
* WINDTALKER_PHASE3_DATA_CAPABILITY_MATRIX.json
* WINDTALKER_PHASE3_DSL_CAPABILITY_SPEC.json
* WINDTALKER_PHASE3_CAUSAL_FIDELITY_FRAMEWORK.json
* WINDTALKER_PHASE3_PROBES_SUMMARY.json
* WINDTALKER_PHASE3_PRODUCTION_ISOLATION.json
* 4个probe完整spec、AST、交易日志和消融结果。

1. 修正报告文字矛盾

必须核验：

* config_hash_unchanged.pass=true
* Q35“config hash是否变化？True”

必须明确给出：

* 配置hash是否保持不变；
* 配置hash是否发生变化；
* 哪个表述是原报告文字错误；
* 当前真实hash。

不得让歧义继续进入后续自动验收。

2. 覆盖率字段语义核验

Phase 3报告中的：

* funding_rate：1.0
* oi：0.1464
* basis_bps：0.2136
* taker_imbalance：0.4107
* cross_sync_score：0.9857
* flow_imbalance：1.0

必须明确这些值分别代表：

* 实际数据覆盖率；
* 当前样本值；
* 特征值；
* 非缺失率；
* 可回放窗口比例；
* 质量评分；
* 其他含义。

不得在含义未确定时使用其支持正式候选。

3. 候选级数据可用性

对以下三个已通过probe能力分别生成数据质量报告：

OI

* 历史覆盖范围；
* 非缺失比例；
* 时间戳精度；
* symbol覆盖；
* timeframe支持；
* 延迟；
* stale rate；
* 是否可完成至少10个walk-forward窗口。

Cross-asset sync

* BTC与目标资产同步率；
* 时间对齐误差；
* 缺失bar处理；
* lead-lag可用历史长度；
* reference asset中断处理；
* 是否可重放。

Taker flow

* 主动买卖量来源；
* 历史覆盖；
* 聚合方法；
* timestamp规则；
* 缺失率；
* 是否发生方向标签反转；
* 是否可完成walk-forward。

若任何能力的数据不足以完成正式Gate3：

对应桥接候选不得伪造执行，必须标记data_not_promotion_ready。

输出：

WINDTALKER_PHASE4_DATA_PROMOTION_READINESS.json

⸻

六、Phase 4B：Candidate-grade Intermediate Representation

必须建立独立的：

Candidate IR

Candidate IR位于：

research DSL AST
→ Candidate IR
→ formal implementation

不得让Codex直接从自然语言spec自由生成正式策略代码。

Candidate IR必须包含

1. 身份信息

* candidate_id
* source_probe_id
* source_research_spec_hash
* source_research_ast_hash
* candidate_ir_version
* created_at
* promotion_status

2. 数据依赖

* primary symbol；
* reference symbol；
* timeframes；
* OI；
* funding/basis；
* taker flow；
* cross-asset；
* feature freshness；
* missing-data policy；
* fail-closed rule；
* proxy declaration。

3. 状态机

* states；
* transitions；
* transition conditions；
* timeouts；
* reset rules；
* invalidation；
* cooldown。

4. 事件顺序

必须显式记录：

* event A；
* event B；
* maximum gap；
* event C veto；
* confirmation；
* entry eligibility。

不得被编译为无序AND条件。

5. 入场表达

* regime；
* setup；
* trigger；
* confirmation；
* veto；
* final entry；
* side；
* timestamp semantics。

6. 退出表达

* 0.9%生产保护止损适配；
* mechanism invalidation；
* time stop；
* dynamic exit；
* strategy close；
* TP；
* trailing，如适用；
* cross-asset invalidation，如适用。

7. 因果结构

* core causal variables；
* supporting variables；
* non-causal filters；
* forbidden proxies；
* expected ablation effect；
* expected event-order destruction effect。

8. 编译约束

* 不允许旧family template降级；
* 不允许自动添加RSI、EMA、CCI、VWAP；
* 不允许单资产替代跨资产；
* 不允许OHLC代理替代真实OI或taker flow；
* 不允许删除状态机；
* 不允许将事件序列改成同bar条件；
* 不允许修改退出逻辑。

输出：

WINDTALKER_PHASE4_CANDIDATE_IR_SCHEMA.json

⸻

七、Phase 4C：正式实现器隔离升级

模板坍缩根因已经定位于：

/root/dual_engine_workflow_v2/pipeline_step_a.py::codex_implement_from_spec

第四阶段必须对正式候选实现路径进行受控修复。

1. 不得直接覆盖旧实现器

必须建立：

* 新实现路径；
* feature flag；
* candidate-only mode；
* rollback；
* backup；
* old path preservation。

示例：

formal_implementation_mode:
  legacy_spec_to_code
  candidate_ir_compiler

本阶段桥接候选只能使用：

candidate_ir_compiler

现有生产策略继续使用：

legacy_spec_to_code

2. 实现器职责

Candidate IR compiler只能：

* 将IR确定性编译为候选代码；
* 映射状态机；
* 映射事件顺序；
* 映射真实数据依赖；
* 映射退出逻辑；
* 生成可审计trace；
* 拒绝不支持的语义。

不得：

* 自由补充逻辑；
* 自动选择指标；
* 自动替换变量；
* 依据回测结果改写逻辑；
* 用旧family模板兜底；
* 缺数据时退化成OHLC代理。

3. 不支持即失败

如果Candidate IR包含尚不支持的能力：

编译必须FAIL CLOSED。

不得：

* 静默忽略；
* 自动简化；
* 用默认值；
* 使用零值；
* 替换为旧指标；
* 继续生成部分实现并判定fidelity PASS。

4. 输出编译证据

每个候选必须输出：

* source IR；
* compiler version；
* compilation trace；
* feature mapping；
* state mapping；
* event-order mapping；
* exit mapping；
* rejected clauses；
* generated code hash；
* unsupported feature list；
* legacy fallback used；
* final compiler verdict。

要求：

legacy_fallback_used=false

输出：

WINDTALKER_PHASE4_FORMAL_COMPILER_AUDIT.json

⸻

八、Phase 4D：三个桥接候选

本阶段只创建三个桥接候选。

不是6–8个正式批次。

必须分别来自Phase 3已通过overall PASS的三个能力。

Bridge Candidate 1：OI与价格拥挤衰竭

来源：

probe_oi_price_crowding_fade_btc_5m

必须使用真实：

* OI；
* price；
* state transition；
* confirmation；
* invalidation。

禁止用：

* RSI；
* 单纯大阴/大阳；
* 成交量替代OI；
* 普通超买超卖。

核心结构示例：

价格方向扩张
→ OI同步显著增长
→ 价格延续能力下降
→ flow或价格确认
→ 拥挤衰竭入场

Bridge Candidate 2：BTC Lead-lag传导

来源：

probe_btc_lead_lag_eth_5m

必须使用真实：

* BTC作为reference symbol；
* follower asset；
* 同步时间戳；
* lead move；
* lag condition；
* follower acceptance；
* reference invalidation。

禁止退化为：

* ETH单资产趋势；
* BTC单独方向过滤；
* 同时上涨的相关性策略；
* 静态相关系数阈值策略。

Bridge Candidate 3：Taker Flow吸收

来源：

probe_taker_absorption_btc_5m

必须使用真实：

* aggressive buy/sell flow；
* price response；
* flow-price divergence；
* reclaim或acceptance；
* state sequence。

禁止退化为：

* 长下影；
* volume spike；
* RSI divergence；
* 单K反转；
* 普通价格形态。

Funding/Basis方向

不得作为本阶段正式桥接候选。

原因：

Phase 3 probe因果忠实度FAIL。

可以记录失败原因和后续研究建议，但不得为了凑数将其纳入三个桥接候选。

⸻

九、Phase 4E：转换语义等价验证

每个桥接候选必须比较三个层级：

Research probe
Candidate IR
Formal candidate implementation

1. 静态语义等价

比较：

* 数据依赖；
* 状态；
* 事件；
* 顺序；
* 条件；
* veto；
* 入场；
* 退出；
* invalidation；
* proxy使用。

输出每项：

* exact；
* semantically_equivalent；
* changed；
* missing；
* unsupported。

2. AST等价

必须比较：

* research AST；
* Candidate IR AST；
* formal implementation AST或代码结构图。

禁止只比较文本。

3. 行为等价

在同一历史数据、同一时间范围、同一成本关闭条件下，比较：

* setup次数；
* armed次数；
* trigger次数；
* entry次数；
* entry timestamp；
* side；
* state transition；
* exit reason；
* holding period；
* trade sequence。

允许因正式风险适配产生明确且可解释的差异。

不得允许因编译简化产生大面积行为偏移。

4. 等价阈值

必须报告：

* setup overlap；
* entry Jaccard；
* direction match；
* state path match；
* exit-reason agreement；
* trade-sequence similarity。

阈值必须预先固定，不得看结果后调整。

建议最低要求：

* 核心数据依赖一致：100%
* 事件顺序一致：100%
* 状态机关键路径一致：100%
* entry direction match：100%
* setup/entry overlap：达到预设高阈值
* 无禁止代理
* 无legacy fallback

任一核心语义发生变化：

promotion fidelity FAIL。

输出：

WINDTALKER_PHASE4_RESEARCH_FORMAL_EQUIVALENCE.json

⸻

十、Phase 4F：正式因果忠实度

每个桥接候选在formal implementation上重新执行完整因果忠实度。

不能引用Phase 3研究probe的PASS替代。

必须执行

1. 核心变量消融

例如：

* OI候选移除OI；
* Lead-lag候选移除BTC reference；
* Taker候选移除taker flow。

正式候选行为必须显著改变。

2. 随机代理替换

* OI替换为随机序列；
* BTC reference替换为无关资产或乱序数据；
* taker flow替换为随机方向。

不得保持接近相同的交易序列。

3. 事件顺序破坏

将：

A → B → C

改为：

B → A → C

或无序AND。

表现和信号结构应显著受损。

4. Regime veto

移除排除状态，验证错误信号是否增加。

5. 对手方环境反事实

在机制声明的市场条件不成立时：

* 正式候选不应保持相同表现。

6. 旧模板相似度

正式候选必须与Phase 1/2旧策略比较：

* AST相似度；
* feature相似度；
* trade lifecycle；
* entry overlap；
* PnL分布；
* exit分布。

不得再次进入旧的模板坍缩簇。

最终fidelity

每个候选必须输出：

* research_structural_fidelity；
* formal_structural_fidelity；
* research_causal_fidelity；
* formal_causal_fidelity；
* research_formal_equivalence；
* legacy_template_independence；
* final_promotion_fidelity。

只有全部通过：

final_promotion_fidelity=PASS

输出：

WINDTALKER_PHASE4_FORMAL_CAUSAL_FIDELITY.json

⸻

十一、Phase 4G：正式STEP A Gate执行

三个桥接候选必须通过正式STEP A Gate路径测试。

不得使用研究probe测试冒充正式Gate。

Gate0

检查：

* mechanism_spec；
* Candidate IR；
* 数据真实可得；
* 成本空间；
* 机制完整；
* 无禁止代理；
* promotion fidelity前置条件。

Gate1

检查：

* 正式结构忠实度；
* 正式因果忠实度；
* research-formal等价；
* legacy fallback为false；
* 无语义坍缩。

Gate2

检查：

* 实现；
* 数据；
* timestamp；
* no-lookahead；
* missing fail-closed；
* 多资产对齐；
* 状态机；
* 事件日志；
* 风险链适配。

Gate3

必须真实执行至少10个walk-forward窗口。

每个窗口报告：

* 数据覆盖；
* 有效天数；
* setup数；
* entry数；
* 成本前收益；
* 成本后收益；
* expectancy；
* 胜率；
* 盈亏比；
* 最大回撤；
* 成本比例；
* 状态分布；
* 数据缺失否决数；
* 核心变量有效性。

如某窗口因数据不足无法执行：

* 必须明确标记；
* 不得填0冒充失败或成功；
* 不得删除窗口；
* 不得将不可执行窗口算作通过。

Gate4–6

如果有桥接候选通过Gate3：

必须继续执行：

* Gate4完整20项逻辑破坏；
* Gate5统计稳健性；
* Gate6多AI攻击。

不得在Gate3通过后停止。

Gate7

本阶段Gate0–6全过者可以进入：

formal_bridge_verified

但不得直接进入：

* live production；
* automatic mount；
* positive-E frequency credit。

若需要进入human-confirm pending，必须单独标记：

bridge_verified_human_review_pending

⸻

十二、第四阶段通过条件

第四阶段不要求三个候选全部盈利。

但必须证明正式桥接能力真实存在。

工程执行通过

必须完成：

1. 数据覆盖复核；
2. Candidate IR；
3. candidate-only compiler；
4. 三个桥接候选；
5. 转换等价验证；
6. formal causal fidelity；
7. Gate执行；
8. 生产隔离；
9. rollback；
10. 最终报告。

核心桥接能力通过

必须同时满足：

1. 三个候选均完成Candidate IR；
2. 三个候选均没有legacy fallback；
3. 至少两个候选：
    * structural fidelity PASS；
    * causal fidelity PASS；
    * research-formal equivalence PASS；
    * legacy-template independence PASS；
4. 至少两个候选正式进入Gate3；
5. 正式Gate3能够读取新数据并真实执行；
6. 不因数据缺失退化为OHLC代理；
7. 正式实现之间不发生模板坍缩；
8. 生产链隔离回归PASS。

允许进入批量正式策略创造的条件

只有在以下条件同时满足时，第四阶段才可以裁定：

ALLOW_FORMAL_BATCH_CREATION=YES

条件：

* 核心桥接能力PASS；
* 至少2个桥接候选formal causal fidelity PASS；
* 至少2个桥接候选进入正式Gate3；
* Candidate IR compiler无legacy fallback；
* 正式候选没有退化为旧K线模板；
* 新数据fail-closed；
* 生产隔离PASS。

不要求一定有Gate3通过者，原因是本阶段首先验证正式链能够忠实承载和测试新机制。

但必须单独报告：

* Gate3通过数；
* 是否出现真正策略质量突破。

⸻

十三、不得混淆的三种成功

必须分开报告：

1. 工程执行成功

表示：

* 4A–4I执行完成。

2. 桥接能力成功

表示：

* research机制能够在formal链中保持语义和因果结构。

3. 策略盈利成功

表示：

* 正式候选通过Gate3及后续Gate。

不得因为工程完成就说正式策略创造能力已解锁。

不得因为桥接成功就说已有正期望策略。

不得因为Gate3能够运行就说Gate3已经通过。

⸻

十四、修复规则

每个桥接候选最多允许三轮工程修复。

但不得机械跑满三轮。

允许修复

* IR编译错误；
* 数据接口错误；
* 时间对齐错误；
* 状态机实现错误；
* 事件顺序实现错误；
* 缺失值处理错误；
* formal与research不一致；
* 退出映射错误；
* 日志或manifest缺失；
* no-lookahead缺陷。

禁止修复

* 改变盈利机制；
* 删除OI、taker或reference symbol；
* 用OHLC代理替代核心数据；
* 放宽入场；
* 删除状态；
* 删除事件顺序；
* 调低成本；
* 调低Gate；
* 删除失败窗口；
* 改变20x；
* 改变30%；
* 改变0.9%；
* 为过Gate扫描参数；
* 将桥接候选改成另一策略。

一旦需要改变核心机制才能修复：

立即归档，不得继续沿用原candidate_id。

⸻

十五、生产安全边界

第四阶段全部工作仍处于：

* candidate；
* research replay；
* formal backtest；
* shadow-eligible evaluation。

不得：

* 启用live；
* 修改生产daemon；
* 自动挂载；
* 发真实订单；
* 修改生产策略；
* 修改STEP B分类；
* 修改风险配置；
* 修改ADA止损；
* 改变open→SL→TP/close链。

必须验证：

* production config hash；
* risk hash；
* live strategy hash；
* daemon hash；
* order adapter；
* SL adapter；
* TP adapter；
* close adapter；
* event logging；
* fail-closed isolation。

如果新增数据停止：

* 桥接候选不得产生信号；
* 现有生产策略不得受影响；
* 系统不得用旧价格代理继续开仓。

⸻

十六、自动交易目标实际前进度

第四阶段原则上不挂载策略。

因此如果没有真实生产策略落地：

自动交易目标实际前进度必须为0%。

以下不得计入自动交易实际前进度：

* Candidate IR建立；
* compiler建立；
* formal fidelity通过；
* Gate3执行；
* Gate3通过；
* Gate4–6通过；
* bridge verified；
* human review pending。

这些属于：

* 工程前进；
* 候选能力前进；
* 策略研发前进。

不属于：

已落地生产自动交易前进。

⸻

十七、最终报告

报告标题：

WINDTALKER PHASE 4 — Research-to-Formal Promotion Bridge Final Report

首页必须最显眼列出：

* 当前成果是完整第四阶段还是局部实施；
* 工程执行裁定；
* 桥接能力裁定；
* 策略质量裁定；
* 是否允许批量正式策略创造；
* Phase 3配置hash矛盾是否修正；
* 数据coverage字段真实含义；
* Candidate IR是否建立；
* candidate compiler是否建立；
* legacy fallback次数；
* 桥接候选数；
* IR完成数；
* formal实现完成数；
* structural fidelity通过数；
* causal fidelity通过数；
* research-formal equivalence通过数；
* legacy-template independence通过数；
* 正式Gate3进入数；
* 正式Gate3通过数；
* Gate4完成数；
* Gate5完成数；
* Gate6完成数；
* bridge verified数；
* human review pending数；
* 生产挂载数；
* 当前正期望频率；
* 当前正期望缺口；
* 自动交易目标实际前进度；
* 原初功能偏离度；
* 生产隔离结果；
* open→SL→TP/close是否完整。

⸻

十八、必须回答的验收问题

1. 是否完整执行Phase 4A–4I？
2. Phase 3 config hash文字矛盾如何修正？
3. production config是否实际发生变化？
4. Phase 3 coverage_sample各字段真实含义是什么？
5. OI历史数据是否足以完成正式Gate3？
6. Cross-asset sync是否足以完成正式Gate3？
7. Taker flow是否足以完成正式Gate3？
8. Funding/Basis为何未进入桥接候选？
9. Candidate IR是否建立？
10. Candidate IR是否支持状态机？
11. Candidate IR是否支持事件顺序？
12. Candidate IR是否支持多资产？
13. Candidate IR是否支持真实OI？
14. Candidate IR是否支持真实taker flow？
15. Candidate IR是否支持动态退出？
16. Candidate IR是否显式禁止代理降级？
17. 是否建立candidate-only compiler？
18. 是否保留legacy实现路径？
19. 是否使用feature flag隔离？
20. 是否存在legacy fallback？
21. legacy fallback发生在哪些候选？
22. 哪些IR条款被编译器拒绝？
23. 是否存在静默语义删除？
24. 三个桥接候选分别是什么？
25. 三个候选是否分别来自Phase 3三个PASS probe？
26. OI候选是否真实使用OI？
27. Lead-lag候选是否真实使用reference symbol？
28. Taker候选是否真实使用主动买卖流？
29. 是否有候选退化为普通OHLC策略？
30. 哪些候选通过structural fidelity？
31. 哪些候选通过formal causal fidelity？
32. 哪些候选通过research-formal equivalence？
33. 哪些候选通过legacy-template independence？
34. 核心变量消融后发生了什么？
35. 随机代理替换后发生了什么？
36. 事件顺序破坏后发生了什么？
37. 哪些候选与Phase 1/2旧模板高度相似？
38. 哪些候选正式进入Gate3？
39. Gate3实际执行数是多少？
40. 哪些候选通过Gate3？
41. 未通过Gate3的主要原因是什么？
42. 数据不足窗口是否被正确标记？
43. 是否删除过失败窗口？
44. 是否降低Gate3标准？
45. 是否有Gate3通过者继续Gate4？
46. 哪些候选完成Gate4？
47. 哪些候选完成Gate5？
48. 哪些候选完成Gate6？
49. bridge verified数是多少？
50. 是否允许批量正式策略创造？
51. 允许或拒绝的硬证据是什么？
52. 是否修改生产DSL？
53. 是否修改生产daemon？
54. 是否改变20x？
55. 是否改变30%初始仓位？
56. 是否改变0.9%止损目标？
57. 是否迁移ADA SL？
58. 是否削弱open→SL→TP/close？
59. 新数据故障是否fail closed？
60. 是否存在真实订单行为？
61. 生产挂载数是多少？
62. 当前正期望频率是多少？
63. 当前正期望缺口是多少？
64. 自动交易目标实际前进度是多少？
65. 原初功能偏离度是多少？
66. 工程执行最终裁定是什么？
67. 桥接能力最终裁定是什么？
68. 策略质量最终裁定是什么？
69. 是否允许进入下一轮6–8个正式策略创造？
70. 第四阶段综合结果是什么？

⸻

十九、最终裁定规则

必须分别给出四项裁定。

1. 工程执行裁定

判断：

4A–4I是否真实完成。

2. 桥接能力裁定

判断：

research机制进入formal链后是否保持语义、因果结构和行为独立性。

3. 策略质量裁定

判断：

是否有桥接候选通过Gate3及后续Gate。

4. 自动交易实际进度裁定

判断：

是否有新策略真实生产挂载并增加正期望频率。

情况一：工程完成但桥接失败

Engineering execution: PASS
Research-to-formal bridge: FAIL
Strategy quality breakthrough: FAIL
Auto-trade progress: 0%
ALLOW_FORMAL_BATCH_CREATION: NO
综合结果：FAIL

情况二：桥接成功但没有Gate3通过者

Engineering execution: PASS
Research-to-formal bridge: PASS
Strategy quality breakthrough: NOT YET
Auto-trade progress: 0%
ALLOW_FORMAL_BATCH_CREATION: YES
综合结果：桥接阶段PASS

前提是：

* 至少2个正式候选通过formal causal fidelity；
* 至少2个正式候选进入真实Gate3；
* 无模板坍缩；
* 无legacy fallback；
* 生产隔离PASS。

情况三：桥接成功且存在Gate3突破

Engineering execution: PASS
Research-to-formal bridge: PASS
Strategy quality breakthrough: PASS
Auto-trade progress: 0%
ALLOW_FORMAL_BATCH_CREATION: YES
综合结果：PASS

情况四：出现生产挂载

本阶段原则上禁止生产挂载。

如发现自动挂载：

立即判定生产安全违规，回滚并将综合结果判为FAIL。

⸻

二十、最终执行指令

现在立即开始执行风语者计划第四阶段。

不得直接开始6–8个正式策略批次。

不得直接开放research DSL live能力。

不得继续使用旧codex_implement_from_spec自由翻译高维机制。

必须完成：

Phase 3证据复核
→ 数据promotion readiness
→ Candidate IR
→ candidate-only compiler
→ 三个桥接候选
→ research/formal语义等价
→ formal causal fidelity
→ legacy-template independence
→ 正式STEP A Gate
→ 生产隔离与rollback
→ 最终桥接验收。

第四阶段结束时必须明确回答：

当前系统是否已经真正打通“研究机制 → 正式候选实现 → 正式Gate”的链条，并且不会重新退化为旧的K线模板。

只有答案由真实证据支持为YES时，才允许进入下一轮6–8个正式策略批量创造。
</user_query>