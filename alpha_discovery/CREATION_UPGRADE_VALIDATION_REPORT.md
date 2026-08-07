# 创造能力升级验证报告

**Brief**: 均线金叉做多 | **标的**: ETH-USDT-SWAP 5m long | **数据**: 52,876 bars / 183.6 天

## 一、已实施的代码升级

| 模块 | 改动 |
|---|---|
| `creation_meta_think.py` | 均线/金叉 brief 注入 3 个正交设计视角（金叉趋势、回撤收复、假交叉对照），含 `required_factor_intersection` + `factor_side_constraints` |
| `research_discovery.py` `_design_seed_hypotheses` | 传递因子侧约束；摄入 architect/GLM `hypotheses`；设计种子 `priority_boost` 提升至 6–8 |
| `research_discovery.py` `_try_param_rescue_after_probe` | 裸探针失败后、写 lineage 前强制参数搜索（默认 `QIYU_PARAM_PREPROBE_RESCUE=1`）；路径搜索无可行解时回退 legacy 网格 |
| `research_discovery.py` | `QIYU_MAX_HYP_PROBE` 默认 96；`QIYU_CREATION_WITH_LLM=1` → `skip_llm=False` |
| `creation_blueprint.py` | `skip_llm=None` + `_creation_skip_llm()` 解析环境变量 |
| `parameter_platform.py` | `QIYU_PARAM_MAX_EVALS` 默认 48 |

## 二、验证运行（run_id: `validate_ma_cross_v2`）

| 指标 | 升级前 (trend1) | 升级后 |
|---|---|---|
| 耗时 | ~508s | **1432s** |
| `NO_DIRECTIONAL_EFFECT` | 63 | **68** |
| 存活 / 可装配 | 0 | **0** |
| 元思考选中视角 | — | **均线金叉趋势跟踪** |
| 设计种子入种群 | 0（未结构化） | **5 条**（ledger: 2 条被探测） |
| GLM 增强 | — | **未启用**（无 API key，`glm_enriched=false`） |
| WR>35% & mean_net>0 | 否 | **否** |

### 设计种子探针明细（ledger）

| hypothesis_id | best_factor | mean_net | state |
|---|---|---|---|
| `H_design_f9b25a50…` | trend_bias_50_200 | -0.047 | NO_DIRECTIONAL_EFFECT |
| `H_design_60e015b9…` | rsi_14 | -0.049 | NO_DIRECTIONAL_EFFECT |

参数救援：对 `trend_bias_50_200` 跑了 36 次 entry 路径搜索，**全部不可行**（`mean_winning_levered` 门槛），legacy 回退已补上但尚未在本 brief 上产生正期望候选。

## 三、结论

### 已恢复的能力
1. **有目的的设计种子**：brief「均线金叉」不再落进默认均值回归/流动性 sweep 三件套，而是生成趋势交集逻辑（`trend_bias_50_200 high ∧ ret_12 high`）。
2. **创造器而非仅检测器**：设计种子进入种群优先队列（`priority_boost` 6–8），参数救援在淘汰前介入。
3. **LLM 开关就绪**：`QIYU_CREATION_WITH_LLM=1` 已接通；本机无 key 时自动走结构化本地设计。

### 尚未达标的验收项
- **无 WR>35% & mean_net>0 候选**：ETH 5m 183 天窗口上，即使用结构化金叉逻辑，净期望仍为负（~25% 胜率）。
- **NDE 未下降**：因探测预算扩大（96 hyp × 120 cheap），总淘汰数略增，不代表逻辑回退。
- **GLM 种子未验证**：需配置 DeepSeek/GLM API 后重跑 `--with-llm`。

## 四、下一步（需人工配合）

```bash
export QIYU_CREATION_WITH_LLM=1
export QIYU_PARAM_MAX_EVALS=48
export QIYU_MAX_CHEAP_PROBES=320
export QIYU_MAX_HYP_PROBE=96
# 配置 ai_ecosystem.env 中的 GLM/DeepSeek key 后：
python3 scripts/validate_creation_upgrade.py
```

或换 brief/周期（如 ETH 1h 趋势）验证正期望候选是否更容易出现。

---
*生成时间: 2026-08-06 | 机器可读: `CREATION_UPGRADE_VALIDATION_REPORT.json`*
