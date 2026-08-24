# 策略创造模块 — 当前实现全步骤说明书（给另一 AI）

> 仓库：`intraday_live_v1_20260720`  
> 用途：把**今天实际跑的代码路径**讲清楚（含旁路/软通过），不是理想教义，也不是未落地的 E>0 重构计划。  
> 入口铁律：人类/Cursor/Codex 创造指令 → **只许** `scripts/strategy_create_sole.py` / `creation_sole_entry.create_strategy` / `parallel_creation.submit_job`。禁止直接写 DSL、ecosystem/mass/factory 旁路创造。

---

## 0. 总览

### 0.1 对外宣称的三步教义（文档层）

来源：`dual_engine_workflow_v2/creation_quality_doctrine.py`

1. **研究发现**：委员会 + 探针 + 多重检验  
2. **门槛**：WR>50%、去最大盈利不崩、漏斗 L0–L3、门槛 0–7、寒霜贰/crec  
3. **四阶段复核**：只有过第二步才交复核  

### 0.2 今天实际生效的运行模式（代码层）

关键开关：`dual_engine_workflow_v2/manufacture_batch_policy.py`

```text
PRE_REVIEW_HARD_GATES_DISABLED = True
```

因此**今天默认是「制造批次模式 + 精简多 AI 复核」**：

- 探针 / 委员会 / 反证 / EFR / OOS / 组装中的 WR>50% 等大量门槛被 **soft-pass 或跳过**
- 真正硬杀主要落在：**交接 E>0 + 周开仓≥0.5**（以及复核侧同类口径）
- 传统「四阶段复核 + Gate0–7 + 漏斗」在 slim 路径下**不到达**

### 0.3 端到端拓扑

```text
Human / Cursor / Codex
  → scripts/strategy_create_sole.py
  → parallel_creation.submit_job
  → qiyu-creation-worker@{0,1}
  → creation_sole_entry.create_strategy
  → creation_blueprint.run_creation_blueprint
       ├─ research_discovery.run_discovery
       └─ _assemble_admitted_population + manufacture_batch_policy
  → [candidate_ready] formal_review_bridge.submit_blueprint_to_formal_review
       → pipeline_step_a.run_creation_pipeline_step_a  (slim multiai)
            → auto_trade_human_confirm_pipeline.ingest_and_screen
                 ├─ strategy_pending_human_confirm.json
                 └─ strategy_pending_optimize/strategies.json
  → 人工确认后才可能挂载（永不自动上线）
```

### 0.4 进度条阶段（UI）

`parallel_creation.PROGRESS_STAGES`：

| stage | 中文 | 约 % |
|-------|------|------|
| queued | 排队等待 | 0 |
| claimed | 已认领 | 5 |
| contract | 研究契约 | 12 |
| population | 机制种群生成 | 22 |
| committee | 委员会 | 35 |
| map_elites | 质量—多样性 | 48 |
| probe | 裸探测 | 60 |
| antifalsify | 抗证伪 | 72 |
| assembly | 组装 | 82 |
| formal_review | 交复核 | 92 |
| done | 本轮结束 | 100 |

---

## 1. 入口与排队

### 1.1 CLI 提交

- **文件**：`scripts/strategy_create_sole.py:main`
- **作用**：解析参数并调用 `parallel_creation.submit_job`
- **必填**：`--research-direction`、`--symbol`（**禁止 ADA-USDT-SWAP 研究**）
- **默认**：`--timeframe 5m`、`--direction long|short`（必须单向拆任务）、`--source` ∈ cursor/codex/human/web/system_timer/direct
- **可选**：`--brief`、`--with-llm`、`--max-loops`（默认 5，工人侧封顶 12）、`--pipeline 1|2`、`--force` 跳过去重
- **输出**：stdout JSON；不直接写策略文件

### 1.2 任务入队

- **文件**：`dual_engine_workflow_v2/parallel_creation.py:submit_job`
- **作用**：
  - 构造不可变 `job_key`（方向/标的/周期/brief/契约/版本等哈希）
  - 校验 mutation parent 血缘（若有）
  - 去重：running/pending 同 key，或 completed 冷却期内
  - 写入 pending，唤醒 `qiyu-creation-worker@0/1`
- **硬门槛**：source 白名单；ADA 禁研；mutation parent 必须在 completed/failed 且证据齐全
- **冷却**：`QIYU_CREATION_DEDUPE_COOLDOWN_SECONDS` 默认 **21600s（6h）**
- **产物**：`auto_trade/dual_engine/parallel_creation/pending/{job_id}.json`

### 1.3 Worker 认领执行

- **文件**：`parallel_creation.py:worker` → `claim_next` → `execute_claimed`
- **作用**：
  - 槽位 0/1 = 管道1/管道2（最多 **2** 并行，`creation_capacity_{0,1}` 锁）
  - 设置 `QIYU_CREATION_SLOT_HELD`、进度路径
  - 调 `creation_sole_entry.create_strategy`
  - 若 `QIYU_AUTO_FORMAL_REVIEW` 非 off（默认 on）→ 调 formal review bridge
  - 收尾到 `completed/` 或 `failed/`
- **产物**：
  - `parallel_creation/running/{job_id}.json`（瞬时）
  - `parallel_creation/completed|failed/{job_id}.json`
  - `parallel_creation/artifacts/{job_id}/qualified_blueprint.json`
  - `parallel_creation/artifacts/{job_id}/formal_review_handoff.json`
  - `formal_review_wx_notify.json`（若已交复核）

---

## 2. 唯一创造入口包装

### 2.1 create_strategy

- **文件**：`dual_engine_workflow_v2/creation_sole_entry.py:create_strategy` → `_create_strategy_unlocked`
- **作用**：
  - 容量闸门（工人已持锁时可直入）
  - 调 `creation_blueprint.run_creation_blueprint`
  - `verify_blueprint_stages` 清单校验
  - 归类 outcome
- **outcome 分类**：
  - `candidate_ready`：有合格蓝图，可交/已交复核
  - `candidate_quality_failure`：材料化有产物，但交接门槛失败（常见）
  - `generation_system_failure`：材料化/管道系统失败
  - `research_rejected`：发现无可信候选
  - `data_blocked`：缺 K 线/契约数据
  - `technical_failed`：异常/容量满/僵死
- **产物**：`auto_trade/dual_engine/sole_creation_runs/{mission_id}.json`

### 2.2 verify_blueprint_stages

- **文件**：`creation_sole_entry.py:verify_blueprint_stages`
- **作用**：对「声称 candidate_ready」做 fail-closed 检查（委员会、种群优先、试探预算、探针、judge/DSR 证据、recipe 血缘等）
- **注意**：`review_admission_passed` 语义是「有 survivor 可交组装」，**不等于** DSR/PBO 通过

---

## 3. 蓝图编排

### 3.1 run_creation_blueprint

- **文件**：`dual_engine_workflow_v2/creation_blueprint.py:run_creation_blueprint`
- **顺序**：
  1. 编译研究契约  
  2. 加载 K 线 + 因子矩阵（`easyquant_bridge`，prefer research store；lookback 默认约 730d；max_bars 视周期 30k–50k）  
  3. `creation_meta_think.run_meta_think`（设计种子；可 LLM）  
  4. `research_discovery.run_discovery`  
  5. 若空且 `QIYU_STRUCTURED_DEEPEN=1`：同身份加深再试  
  6. `_assemble_admitted_population`（制造批次排序/交接）  
- **硬失败**：契约无效 → research_rejected；无 K 线 → data_blocked；无 present_to_assembly → 材料化失败或研究否决  
- **产物目录**：`auto_trade/dual_engine/creation_blueprint/`  
  - 成功：`{symbol}_{tf}_{stamp}_blueprint.json` / `_params.json` / `_strategy_code.py` / `_risk_report.md`  
  - 失败：`*_failed_blueprint.json`

### 3.2 数据加载

- **文件**：`creation_blueprint.py:_load_matrix` → `easyquant_bridge.load_candles` + factor matrix + forward returns（horizon 默认 3）
- **硬门槛**：加载失败 → blueprint `data_blocked`
- **已知风险**：下游某些路径会截断到约 **12000 bars（5m≈41.7 天）**，短窗会夸大 `weekly_opens = n×7/span`

---

## 4. 研究发现（research_discovery.run_discovery）

主文件：`dual_engine_workflow_v2/research_discovery.py:run_discovery`

### 4.1 研究契约编译

- **文件**：`research_contract.compile_contract`（经 `compile_research_contract`）
- **作用**：锁定标的/方向/特征/事件时序/数据版本；拦截不可达硬需求（如未撤销的「周收益≥8%」）
- **默认试探预算**：`max_trial_budget` ≈ **520**
- **产物**：ledger `contract` 事件；`stages.contract`

### 4.2 机制种群生成（population）

- **文件**：`build_hypothesis_population`
- **来源（异构委员会，禁止群聊式互相抄）**：
  - Alpha Discovery AST / 规则  
  - mechanism scientist（机制研究者）  
  - empirical scientist（数据研究者）  
  - symbolic searcher（符号搜索，非 LLM）  
  - design / GLM 假设  
  - pinned rhyme arms / P3 AI AST 等  
- **策略**：`population_first=True`，`early_pick_one=False`；去重  
- **预算环境变量示例**：`QIYU_MAX_MECHANISMS=20`、`QIYU_MAX_PHENOMENA=36`、`QIYU_MAX_MECHANISM_CELLS=96`、`QIYU_MAX_CHEAP_PROBES=160`  
- **产物**：`research_blackboard/{run_id}/`、`research_ledger/{run_id}.jsonl`

### 4.3 强制材料化骨架

- **文件**：`candidate_materialization.ensure_minimum_population`
- **教义**：「先制造，后评判」；空批次视为管道失败，不是「市场没策略」  
- **行为**：池子太薄时注入 ≥30 个 `forced_materialization_skeleton` 假设

### 4.4 结构化搜索 + 廉价探针（MAP-Elites lean）

- **文件**：`structured_candidate_search.search`、`research_campaign.run_lean_campaign`
- **作用**：dev/confirm 分割（dev 比例默认 0.65，finalists 默认 6）；流式廉价探针，再进重探针  
- **产物**：`research_campaigns/{campaign_id}/cheap_probe_summaries.jsonl`

### 4.5 裸探测（naked probe）

- **文件**：`probe_protocol.py:probe_hypothesis` / `_evaluate_trial`
- **正常模式硬条件（今日大多不到达）**：
  - 账户 WR **严格 > 0.50**
  - 反彩票：`expectancy_factor ≥ 1.0` 且去最大盈利后均值 > 0
  - `READY_FOR_ASSEMBLY`
  - 经济轴：`mean_net>0`；执行轴 EFR 探针侧 ≥ **1.20**
- **今日制造模式**：
  - `manufacture_mode = pre_review_gates_disabled() == True`
  - **`n ≥ MIN_INDEPENDENT_EVENTS(8)` 即可 READY**，可不看 WR/反彩票
  - discovery 侧 `handoff_ready` 在制造模式下约等于 `best.passed`
- **规模**：默认最多探 ~72 假设（16–96 clamp）；全局 trial ~520；每假设 6–14 trials

### 4.6 多生成器稳健性（multiverse）

- **文件**：`creation_multiverse.survival_test`
- **今日**：制造模式 / handoff_ready 时 **soft-pass**

### 4.7 抗证伪（antifalsify）

- **文件**：`antifalsify.run_antifalsify_battery`
- **注释**曾写 antifalsify/leakage/causal 禁止 soft-pass  
- **今日实际**：制造模式可 `soft_pass=manufacture_batch_soft` 强制通过

### 4.8 泄漏审计 / 因果边界

- **文件**：`heterogeneous_committee.run_leakage_auditor`、`run_causal_auditor`
- **今日**：制造模式 soft-pass

### 4.9 EFR（边/摩擦）

- **文件**：`edge_friction.evaluate_early_feasibility`
- **阈值**：默认 `min_efr=1.5`；handoff_ready 时可降至 1.0；失败可 soft-pass

### 4.10 执行工程师

- **文件**：`heterogeneous_committee.run_execution_engineer`
- **今日**：handoff_ready 失败 soft-pass

### 4.11 轻量参数平台

- **文件**：`parameter_platform.search`
- **默认**：`QIYU_PARAM_MAX_EVALS=24`；身份锁定复合事件可跳过

### 4.12 构造型红队

- **文件**：`heterogeneous_committee.run_constructive_redteam`
- **今日**：handoff_ready 失败 soft-pass  
- **逻辑要点**：常只要求 main_net > opp_net，不单独要求 E>0

### 4.13 MAP-Elites 归档

- **文件**：`map_elites_archive.upsert`
- **产物**：`stages.map_elites`

### 4.14 材料化修复波（Layer A）

- **触发**：首轮探针后 survivors=0  
- **行为**：注入骨架假设，最多 `MAX_REPAIR_ROUNDS`（默认 3）再探；修复 survivor 常带 soft judge  
- **仍空**：`CANDIDATE_MATERIALIZATION_FAILURE` / `generation_system_failure`

### 4.15 质量修复（Layer B）

- **触发**：制造模式且路径质量塌缩（如 median profit_first < 0.55 或 median MAE > 0.0035）  
- **行为**：P3 AI 诊断或 `quality_optimization` 生成「确认/排除/时序」变体（**禁止改止损/杠杆/降门槛**）  
- **再 append survivors**

### 4.16 P6 质量发现辅件

- **文件**：`failure_path_analyzer`、`family_freeze_policy`、`behavior_similarity_gate`、`quality_funnel`、`candidate_lineage`
- **作用**：血缘门、家族冻结、反克隆、漏斗计数

### 4.17 DSR / PBO（多重检验）

- **文件**：`multiple_testing.evaluate_multiple_testing`
- **阈值**：DSR ≥ **0.95**，PBO ≤ **0.40**
- **今日角色**：写入证据 / `formal_statistical_review_pending`；**不是**制造模式预复核硬否决

### 4.18 OOS 确认门

- **文件**：`evaluate_oos_confirmation_gate`（多重检验相关）
- **阈值意图**：确认窗 WR>50%、expectancy_factor≥1、去最大盈利均值>0、n≥8、mean_net>0  
- **今日制造模式**：advisory；不挡 judge

### 4.19 独立裁判（Kimi 可选）

- **文件**：`heterogeneous_committee.judge_from_evidence`
- **今日制造模式**：`research_discovery` 强制  
  `judgment["admit_to_assembly"]=True`（`manufacture_batch_force_admit`）  
- **正常模式**：依赖 OOS + 确定性分数（约 ≥6.5）+ 可选 Kimi 否决

### 4.20 Discovery 产出

- `assembly_payload[]`（上限 `ASSEMBLY_PAYLOAD_CAP=24`）
- `n_survivors`、`present_to_assembly`
- 今日横幅语义接近：**「制造批次：N 个可编译候选进入组装排序（复核前硬门槛已取消）」**

### 4.21 正式可编译因子桥

- **文件**：`recipe_policy.GENERIC_FACTOR_TO_DSL`、`easyquant_bridge._build_factor_matrix`、`ast_compiler`
- **要点**：研究矩阵有 30+ 特征；正式 DSL 映射约十余个核心因子  
- 不可映射的 research-only 特征 → 正式能力检查失败 → 不能进正式 survivor  
- `ast_compiled` 且 formal_ok 的事件可绕过部分因子名映射限制

---

## 5. 组装与制造批次交接

### 5.1 _assemble_admitted_population

- **文件**：`creation_blueprint.py:_assemble_admitted_population`
- **今日制造模式跳过**：
  - committee_not_admitted  
  - `creation_wr_anti_lottery`  
  - `creation_gate2_floors`  
- **行为**：凡 recipe 完整的行进入 `manufactured[]`，再排序选 Top-N

### 5.2 非制造模式账户门槛（代码仍在，今日旁路）

- **文件**：`_admission_trade_quality`、`creation_quality_doctrine.gate2_account_returns_ok`
- **阈值**：WR 严格 >50%、n≥8、expectancy_factor≥1、mean_net>0、反彩票；walk-forward 比例门

### 5.3 manufacture_batch_policy（今日交接真门槛）

- **文件**：`dual_engine_workflow_v2/manufacture_batch_policy.py`
- **批次常数**：
  - `MIN_MANUFACTURE = 10`（目标制造量，不足打日志）
  - `TOP_N_TO_REVIEW = 3`
  - `ASSEMBLY_PAYLOAD_CAP = 24`
- **Sole economic gate（宣称）**：
  - `E = W × R − (1 − W) > 0`  
    - W = 胜率（0–1）  
    - R = mean_win / |mean_loss|  
  - `weekly_opens ≥ 0.5`
  - `HANDOFF_MIN_TRADES = 1`（极松）
- **已退休硬门槛（注释宣称）**：WR>50%、mean>stop×L、PFR、MAE、n≥12
- **实现风险（现状缺陷，给下一 AI 必读）**：
  1. `package_metrics` 可能用 **path profit-first** 覆盖交接用的 `win_rate`，与账户 WR 分裂  
  2. `mean_loss` 缺失时可用 **stop×leverage×100**（默认约 10pp）兜底 → R 偏乐观 → E 可被抬高  
  3. 短窗 span≈42d 时 `weekly = n×7/span` 易虚高过 0.5  
  4. `n≥1` 可使「单笔盈利」在数学上把 E 弄正  
- **排序**：`score_package` → `rank_packages` → `select_top_for_review`  
- **Token**：`review_gate.issue_handoff_token`（slim 复核强制校验）  
- **失败码**：`top_for_review` 空 → `CANDIDATE_QUALITY_FAILURE` / `manufacture_handoff_floor_fail`  
  （材料化成功 = A 层 OK；质量失败 = B 层）

---

## 6. 交予复核桥

### 6.1 formal_review_bridge

- **文件**：`dual_engine_workflow_v2/formal_review_bridge.py:submit_blueprint_to_formal_review`
- **作用**：
  - 校验 recipe 血缘、admission envelope、禁止全局挖矿换机制  
  - 对 `review_batch` 每行（最多 3）：从 admitted recipe 锁规格 → 实现 DSL → 校验 DSL 对齐 →（可选）持仓校准 → `pipeline_step_a(..., formal_submission=True)`  
- **制造模式硬条件**：每行 `qualifies_for_review_handoff` + 有效 handoff_token  
- **生产约束**：保护止损强制 **0.5%** 价格（`_patch_recipe_stop_05`）  
- **锁**：`process_lock("formal_review_submission")` 串行提交

---

## 7. 复核管道 Step A

### 7.1 今日默认：精简多 AI（slim）

- **文件**：`pipeline_step_a.py:run_creation_pipeline_step_a`
- **条件**：`manufacture_batch_policy.slim_multiai_review_only()==True` 且 `formal_submission=True`
- **跳过**：Gate1 保真、Gate2/3、漏斗 L0–L3、Gate0–7、传统四阶段正式复核  
- **仍做**：
  - DSL 回测  
  - `review_gate.validate_review_metrics_parity`（指标对账）  
  - handoff_token 校验  
  - `auto_trade_ai_consensus.theoretical_review_all`（providers：deepseek / qwen / glm / kimi）  
- **聚合门槛**：`multiai_average_pass` → **E>0 + weekly≥0.5**  
- **旁路**：四家 AI 全 `SKIP_INFRA` 时，可用交接 `select_metrics` 做 **统计放行**（`handoff_statistical_review_pass`）→ 进待优化  
- **周频样本意图**：`WEEKLY_OPENS_MIN_SPAN_DAYS=600`；slim 路径上 2y 否决可能被弱化/跳过（实现需对照当前函数）

### 7.2 传统四阶段路径（代码仍在，今日 slim 不到达）

Gate1 → Gate2/3 + walk-forward → 漏斗 L0–L3 → 空假设 → 孵化软门 → Gate0–6 → 四 AI → pending_human_confirm

### 7.3 成功后

- `auto_trade_human_confirm_pipeline.ingest_and_screen(..., require_ai_review=True)`  
- task.stage → `awaiting_human`  
- 产物：`auto_trade/dual_engine/tasks/{task_id}.json`、`strategy_ecosystem.db`

---

## 8. 人工确认与策略待优化

### 8.1 入队

- **文件**：`auto_trade_human_confirm_pipeline.py:ingest_and_screen` / `enqueue_for_human`
- **产物**：
  - `auto_trade/strategy_pending_human_confirm.json`
  - `auto_trade/human_confirm_pipeline_audit.jsonl`
  - Wx 通知（若配置）
- **铁律**：`production_mounted` 永不由创造自动置真

### 8.2 策略待优化板块

- **文件**：`dual_engine_workflow_v2/pending_optimize.py`
- **API**：`GET /api/strategy/pending_optimize`（`web_server.py`）
- **UI**：主站「策略创造复核」下方「策略待优化」  
- **展示列（当前）**：策略标题、近2年账户胜率、盈利单平均盈利率、周开仓频率、期望值（E）  
- **存储**：`auto_trade/strategy_pending_optimize/strategies.json`

### 8.3 人工确认上线

- **文件**：`auto_trade_human_confirm_pipeline.py:confirm`
- **硬门槛**：status 等待确认、AI 复核已验证、weekly≥0.5  
- **行为**：写 DSL 实盘配置、B 档仓位上限（约 30%）、`human_confirmed=True`  
- **永不**：创造管道自动挂载

---

## 9. 异构委员会角色表（研究发现内）

| 角色 | 模块倾向 | 今日是否硬挡制造模式 |
|------|----------|----------------------|
| 机制研究者 | mechanism scientist | 否（生成假设） |
| 数据研究者 | empirical scientist | 否 |
| 符号搜索者 | symbolic searcher | 否 |
| 反证 | antifalsify | soft-pass |
| 泄漏/因果 | leakage/causal auditor | soft-pass |
| 执行工程师 | execution engineer | soft-pass |
| 构造红队 | constructive redteam | soft-pass |
| 统计裁判 | DSR/PBO | 证据，非硬否决 |
| Judge（可含 Kimi） | judge_from_evidence | **强制 admit** |

Kimi 配置（若启用）：`QIYU_KIMI_*`，Base `https://cmkey.cn/v1/chat/completions`，模型 `kimi-k3`，`QIYU_FORCE_NO_PROXY=1`。

---

## 10. 关键数值速查（今日）

| 项 | 值 | 是否硬杀（制造模式） |
|----|----|----------------------|
| PRE_REVIEW_HARD_GATES_DISABLED | True | 开启旁路 |
| 探针 READY | n≥8 | 是（仅样本） |
| 账户 WR>50% | 0.50 exclusive | 否（旁路） |
| 反彩票 expectancy_factor | ≥1.0 | 否（旁路） |
| EFR | 1.5 / 1.0 | soft |
| DSR / PBO | ≥0.95 / ≤0.40 | 证据 |
| 交接 E | >0 | **是** |
| 交接 weekly | ≥0.5 | **是** |
| 交接 n_trades | ≥1 | 是（过松） |
| Top-N 交复核 | 3 | — |
| 组装 payload 帽 | 24 | — |
| 目标制造量 | 10 | 软目标 |
| 并行创造槽 | 2 | 硬 |
| 默认杠杆 / 止损 | 20× / 0.5% | 生产锁止损 |
| 去重冷却 | 6h | 硬（可 --force） |

---

## 11. 产物路径总表（`auto_trade/dual_engine/`）

| 路径 | 含义 |
|------|------|
| `parallel_creation/pending\|running\|completed\|failed/*.json` | 任务生命周期 |
| `parallel_creation/artifacts/{job_id}/` | 合格蓝图、交接、Wx |
| `sole_creation_runs/{mission_id}.json` | sole 回执 |
| `creation_blueprint/*` | 蓝图/参数/代码/风控/失败包 |
| `research_ledger/{run_id}.jsonl` | 实验事件 |
| `research_blackboard/{run_id}/` | 委员会/预算快照 |
| `research_campaigns/{campaign_id}/` | 廉价探针与 campaign_result |
| `tasks/{task_id}.json` | Step A 任务 |
| `outcome_attributions/*` | 归因碎片 |

相关站外：

- `auto_trade/strategy_pending_human_confirm.json`
- `auto_trade/strategy_pending_optimize/strategies.json`
- `auto_trade/ai_ecosystem.env`（密钥，不入库）
- `auto_trade/ai_research_consent.json`

---

## 12. 环境变量速查

| 变量 | 作用 |
|------|------|
| `VECTOR_ROOT` | 根目录（生产 `/root`） |
| `QIYU_SOLE_CREATION` | 强制 sole（默认 on） |
| `QIYU_AUTO_FORMAL_REVIEW` | 完成后自动交桥（默认 on） |
| `QIYU_CREATION_DEDUPE_COOLDOWN_SECONDS` | 去重窗 |
| `QIYU_CREATION_WITH_LLM` / `--with-llm` | 打开 LLM 元思考/修复 |
| `QIYU_MAX_HYP_PROBE` 等 | 发现广度 |
| `QIYU_CREATION_MAX_BARS` / `QIYU_RESEARCH_LOOKBACK_DAYS` | 样本长度 |
| `QIYU_CREATION_FORBIDDEN_SYMBOLS` | 额外禁研标的 |
| `QIYU_*_API_KEY` / `QIYU_KIMI_*` | 四模型密钥 |

---

## 13. 近期实证（给下一 AI 的失败样例）

- **job_id**：`creation_20260805_130902_319410_eb59a81a`  
- **标的**：ETH-USDT-SWAP 5m long  
- **结果**：`候选质量失败：材料化已完成但未过交接门槛`  
- **最佳候选粗算**：账户 WR≈7.4%，E≈**−0.884**，span≈**42 天**，n≈81  
- **解读**：A 层材料化成功；B 层质量失败。根因是制造模式让低 WR 候选成为 survivor，短窗+口径问题让周频好看，但真实 E 为负，交接全滅。

---

## 14. 教义 vs 实现冲突清单（必读）

| 文档/教义说 | 今日代码做 |
|-------------|------------|
| 第二步 WR>50% 硬底 | 制造模式探针/组装旁路 |
| 反证/泄漏/因果禁止 soft-pass | 制造模式 soft-pass |
| 裸探针→反证→EFR→DSR/PBO→仅存活组装 | DSR/PBO 不否决；judge 强制 admit |
| 四阶段复核 | slim 多 AI + E/周频 |
| E 用账户 W/R | 可能被 path WR / stop×L 损失兜底污染 |
| 近 2 年样本 | 可能落到 ~12k bars≈42d |

---

## 15. 给下一 AI 的协作接口

**只允许这样开创造：**

```bash
python3 scripts/strategy_create_sole.py \
  --source cursor \
  --symbol ETH-USDT-SWAP \
  --timeframe 5m \
  --direction long \
  --research-direction "……" \
  --brief "人类原话" \
  --pipeline 1 \
  --force   # 仅当需要跳过去重时
```

然后：`systemctl start qiyu-creation-worker@0`（oneshot），读  
`auto_trade/dual_engine/parallel_creation/completed/{job_id}.json`  
的 `result.failure_evidence` / `review_batch` / `expectancy`。

**不要**：直接 invent DSL；不要调 ecosystem `--create-strategy`；不要把 soft-pass 当成真实过关；不要用短窗周频当作 2 年稳健证据。

---

*文档生成自当前仓库只读审计；若后续关闭 `PRE_REVIEW_HARD_GATES_DISABLED` 或落地 E>0 全链路硬杀，以代码为准更新本节。*
