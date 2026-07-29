# STEP A — 策略生产与训练系统重构最终报告

- 时间：2026-07-27（UTC+8）
- 代码版本：`step_a_strategy_creation_20260726` / `strategy_workflow_v2_step_a_20260726`
- 备份：`/root/backups/step_a_strategy_creation_20260726_234045/`（含 `ROLLBACK.sh`）
- 前序：v2 制度升级（`creation_entry=v2`）；首轮 Mode A 曾因 `representation_failure` 归档

---

## 封面结论（强制）

| 项 | 结论 |
|----|------|
| **完整STEP / 局部实施** | **局部实施（系统接线已落地；端到端产出未完成）** |
| **仅执行当前成果能否完成策略生产与训练重构** | **不能。** 流程与门控已接线，但尚无策略通过 Gate3+ 全链路至 Gate7，更无生产挂载 |
| **自动交易目标实际前进度** | 创造入口已切到 STEP A 管线；实盘 formal 未动。**新策略贡献进度 = 0%**（无候选/无挂载） |
| **原初功能偏离度** | **低。** 未改 auto-open / 挂载 SL / TP / close 链；杠杆 20x、B 档 30%、固定 SL 0.9% 约束保留；ADA 0.6% **未迁移** |
| **新增独立机制数量** | **提出 3 个独立家族假设**（liquidity_sweep_reversal / orderbook_imbalance_snapback / breakout_trap_reversal）；**通过全部 Gate 并计为完成 = 0** |
| **通过全部 Gate 数量** | **0** |
| **真实挂载生产数量** | **0** |

**明确声明：仅执行当前成果不能完成本次 STEP A 重构的“稳定产出可上线策略”目标。**  
已完成的是：**可运行的生产/训练制度骨架**（角色边界、不可变机制书、20 项拆分测试、三轮修复、失败 KB、Gate0–7、拆分评分），且已用实网任务证明门控会诚实归档失败。

---

## 一、相对 v2 的缺口审计（执行前）

| §一问题 | v2 已有 | STEP A 缺口 | 本轮处置 |
|---------|---------|-------------|---------|
| 逻辑破坏反复死亡 | ABC 替代 ±20% | 非用户指定的 **20 项拆分测试** | 已实现 `split_tests_20.py` 并接线 Gate4 |
| Codex 重写 GLM | condition_audit | 缺不可变 `mechanism_spec.json` + 每轮 `mechanism_fidelity_diff.json` | 已实现 |
| 机制家族克隆 | fingerprint | 缺 STEP A 指纹字段 + exhaustion_fade 拦截 | 已实现 |
| 评分混用 | 辅助 WR 不可覆盖 fatal | 缺显式拆分评分+样本量 | 已实现 `scores_split.py` |
| 失败阶段不透明 | failure_archive | 缺 `strategy_failure_record` + 强制预读 KB | 已实现 |
| 频率诱导放宽 | frequency_exit 检测 | 需禁放宽入场 | 修复仅 engineering/tunable；禁止改机制 |
| 无可复用失败 KB | 结构化归档 | 下一轮必须读取 | `kb_context_for_ai` 强制注入 GLM prompt |
| 测试只证明能跑 | ABC + 四维 | 需 Gate0–7 + 20 拆分 | 已接线；实网最远 Gate0–2 通过、Gate3 失败 |

---

## 二、已部署文件与接线

### 新增（本地 + `/root/dual_engine_workflow_v2/`）

- `step_a_config.py` / `mechanism_spec.py` / `fidelity_diff.py` / `failure_kb.py`
- `step_a_fingerprint.py` / `split_tests_20.py` / `gates.py` / `attackers.py` / `scores_split.py`
- `pipeline_step_a.py`（主创造管线）

### 修改

- `dual_engine_workflow_v2/__init__.py` — 导出 step_a
- `dual_engine_workflow_v2/config.py` — CODE_VERSION bump
- `auto_trade_dual_engine_factory.py` — `creation_entry=v2|step_a` → `start_creation_task_step_a`

### 入口

`start_creation_task()` → STEP A 管线（保留 `start_creation_task_v2` 供回滚）。

### 单元测试

`tests_workflow_v2/test_step_a_acceptance.py` — **8/8 PASS**（本地与服务器）。

---

## 三、实网跑通证据（Mode A / B）

### Mode A（新机制，`exploration_mode=A`）

| task_id | 结果 |
|---------|------|
| `wsa_20260726_235259_bae0` | Gate0 **PASS**（representation_failure **已修复**）；Gate1 FAIL（旧 Codex 注入 ema6）→ 已归档 |
| `wsa_20260727_013520_52ed` | Gate0–1 PASS；DSL 校验 FAIL（未知顶栏字段）→ 工程修复后不再复现 |
| `wsa_20260727_013842_ca6c` | Gate0–2 PASS；Gate3 WF **0/10**；三轮修复后 **mechanism_viability_fail_archived** |

最远真实进度：**Gate0+1+2 通过，Gate3 未过**。未进入 Gate4–7。**未挂载。**

提出的独立机制家族（不计入“完成”）：`liquidity_sweep_reversal`、`orderbook_imbalance_snapback`、`breakout_trap_reversal`（均非 exhaustion_fade_short 克隆）。

### Mode B（已知机制深挖）

| task_id | 结果 |
|---------|------|
| `wsa_20260727_014053_d405` | Gate0 FAIL（`spec_incomplete`）→ 诚实归档；KB 预读已执行 |

模式 C/D 代码路径已映射（combination / failure_reverse_research），本轮未再燃烧额外实网轮次（优先交付报告与诚实进度）。

---

## 四、§十 验收 18 问（如实作答）

1. **完整STEP还是局部工具？** → **局部实施**（制度接线完整度高；策略产出目标未完成）。
2. **仅执行当前成果能否完成重构？** → **不能**（无全 Gate 通过策略，无生产挂载）。
3. **新流程是否真实接入生产调用链？** → **是（创造入口）**：`creation_entry=v2` 路由到 `pipeline_step_a`；**否（实盘交易链）**：未改 formal_daemon / auto_open。
4. **GLM 机制是否永久保存且不可覆盖？** → **是**：`mechanism_specs/*_mechanism_spec.json`，同 id 不同 hash 拒绝覆盖。
5. **Codex 漂移检测是否真实执行？** → **是**：每轮 `mechanism_fidelity_diff.json`；修复轮指纹比对；实网曾捕获 ema6 注入并 Gate1 失败。
6. **四种探索模式是否真实可运行？** → **A/B 已实网跑**；C/D 已接线可调用，本轮未实网轮转完毕。
7. **机制指纹与重复拦截是否接入？** → **是**（编码前拦截；exhaustion_fade_short 在新机制模式禁止；横向扩展不计独立生态位）。
8. **三轮修复是否真实执行？** → **是**：`wsa_…_ca6c` 记录 3 轮后 viability 归档，无无限调参。
9. **失败归档是否被下一轮 AI 读取？** → **是**：`failure_kb_preread` + prompt 注入；KB 状态见 `STEP_A_failure_knowledgebase_status.json`（4 records）。
10. **逻辑破坏是否已拆分？** → **是（代码+Gate4）**：20 项结构化输出；本轮最远任务在 Gate3 失败，**未执行到 Gate4 实网**。
11. **Gate0–7 是否真实执行？** → **管线内真实调度**；实网最远执行到 Gate3。Gate4–7 有代码路径，本批任务未抵达。
12. **框架 vs 真实通过？** → 框架/接线已落地；**真实通过全部 Gate = 0**。
13. **是否生产出新独立机制策略？** → **提出了独立机制假设，但无策略通过训练门控** → 按验收规则 **不计完成**。
14. **是否进入候选池？** → **否**。
15. **是否真实挂载生产？** → **否（0）**。
16. **自动交易目标前进度？** → 实盘守护 **13** 路仍运行；新策略对目标贡献 **0**；创造制度从“胜率门控”转向“机制+Gate”。
17. **原初功能偏离度？** → **低**（见封面）；ADA SL 未动。
18. **未完成内容？** → 见下一节。

---

## 五、未完成 / 阻断

1. 尚无策略通过 Gate3（WF ≥7/10）及之后 Gate4–7。
2. Mode C/D 缺本轮实网完整样例。
3. Gate4 的 20 项测试中部分依赖第二标的/真 bar-shift，当前为 **INCONCLUSIVE 代理**（已标注），需后续补数据通道。
4. Codex 实现对 orderbook 类机制仍是微观结构代理（prev_high/vol_z/atr），与“必须用订单簿”类 non_negotiable 存在实现能力缺口——应在 Gate1/6 继续严格否决或补数据源，而不是放宽。
5. evolve timer 仍暂停（有意）。
6. STEP B **未做**（按要求）。

---

## 六、生产保护证据

- formal 服务运行数：**13**（见 `STEP_A_production_service_evidence.json`）
- 未停止 open-hunter / human-confirm
- 未迁移 ADA 0.6% SL
- 失败部署可回滚：`/root/backups/step_a_strategy_creation_20260726_234045/ROLLBACK.sh`
- 新策略 `live_enabled=false` / `auto_trade_eligible=false`；Gate7 前禁止挂载

---

## 七、产物清单

| 文件 | 位置 |
|------|------|
| 本报告 | 本地 `docs/` + `/root/docs/STEP_A_strategy_creation_training_final_report.md` |
| Gate 结果 | `STEP_A_gate_results.json` |
| 忠实度审计 | `STEP_A_mechanism_fidelity_audit.json` |
| 失败 KB 状态 | `STEP_A_failure_knowledgebase_status.json` |
| 生产证据 | `STEP_A_production_service_evidence.json` |
| Mode A/B 跑批 | `step_a_mode_a_run.json` / `step_a_mode_b_run.json` |

---

## 八、Diff 摘要

- **新增** STEP A 包模块约 10 个 + `pipeline_step_a.py`
- **改路由** dual-engine 创造入口 → step_a
- **不改** `auto_trade_formal_v6_executor.py` 开平仓主链、评级、人工确认内核语义（仅消费 pending）

---

**最终一句：制度与门控已接线且实网可跑；仅执行当前成果不能完成本次 STEP A 的策略生产完成目标（全 Gate 通过并挂载 = 0）。**
