# Phase 0–A：创造小样本严谨层（基线 + 共享骨架）

日期：2026-09-07  
范围：P0 定标 + PA 共享模块；**统计项默认 observe，禁止双侧同日 hard**。

## Phase 0 — 目标与任务

| 目标 | 有可对比基线；H/S/O 与分侧默认写死 |
| 任务 | ① 档位词典 ② Day-0 分侧默认 ③ 改前指标槽位（下表填数） ④ 金标意图 |
| 完成 | 本文件 + `creation_small_n_rigor.phase0_baseline_template()` |

### H / S / O

| 档 | 含义 |
|---|---|
| H | 硬否决 → `failed_rules`，不可挂载 |
| S | 软：warnings + 回灌收缩分 |
| O | 只写 evidence |

### Day-0 默认（避免一刀切）

| 键 | hub-b（本侧接通） | hub-a（建议，对侧自行加 install） |
|---|---|---|
| CREATE_ANTI_EVASION | 1（H：禁 waive） | 1 |
| CREATE_N_DISCOUNT | 1（S） | 1 |
| CREATE_TIMING_BUDGET | soft | soft |
| CREATE_PLACEBO / LOO / MC_SUBSET | observe | observe |
| CREATE_NOISE_STRESS | off | off |

### 改前基线槽位（部署后对照填）

| 指标 | 改前 | 改后（验收） |
|---|---|---|
| waive 次数 | （填） | **必须 0** |
| 低 n 高 C 当进度 | （填） | 回灌含 `n_discount` / `low_n_high_C_not_progress` |
| 平均 timing 叶数 | （填） | 超预算 → warnings |
| 过门率 | （填） | 允许略降，禁止断崖无解释 |

### 金标意图

- **作恶**：`trade_count` 失败仍想挂 → 必须挡  
- **健康**：n≥30、2 叶、收益均值明显为正 → 不被 H 误杀（统计仅 O）

## Phase A — 目标与任务

| 目标 | 共享模块可合入；hub 包装可挂接；waive 永禁；软收缩+叶软预算；统计默认 O |
| 产物 | `dual_engine_workflow_v2/creation_small_n_rigor.py` |
| 测试 | `tests_workflow_v2/test_creation_small_n_rigor.py` |
| 挂接 | `install_into_kdh(kdh)`；hub-b wrapper 已调用 |

### 不做（本阶段）

- 置换/LOO/MC 升 soft/hard  
- 改对侧 hub-a unit / restart  
- 用噪声抬 n  

## 验收清单（PA 部署后）

1. `python -m unittest tests_workflow_v2.test_creation_small_n_rigor -v` 绿  
2. VPS：`cd /root && PYTHONPATH=/root python3 -c 'from dual_engine_workflow_v2.creation_small_n_rigor import never_waive_trade_count; print(never_waive_trade_count({"ok":False,"failed_rules":["trade_count_below_threshold"],"E":0.01}))'` → `(None, ...)`  
3. hub-b boot 日志含 `small_n_rigor_installed`  
4. `/root/deployed.sha` 对应该 tag  
5. hub-a：模块已在 `/root` 可读；**对侧**自行在 strict wrapper 加 `install_into_kdh`（本侧不 restart a）
