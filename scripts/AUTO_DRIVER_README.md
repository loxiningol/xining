# Autonomous Gate-Retry Driver（无人值守自优化驱动）

外包 Wrapper：在**不修改** `dual_engine_workflow_v2` 管道核心的前提下，自动完成

`跑 Gate → 抓失败上下文 → 3 方 AI 出补丁 → 快照/应用/回滚 → 再跑 Gate`

直到 **SUCCESS** 或 **判定理论极限 / 安全上限**，并写出 `delivery_report.md`。

## 快速开始

```bash
# 1) 准备配置
cp scripts/auto_driver_config.example.json scripts/auto_driver_config.json
# 编辑 pack_path / symbol / timeframe / direction

# 2) 确认 AI 密钥与授权（与现网一致）
#   /root/auto_trade/ai_ecosystem.env   (QIYU_DEEPSEEK/QWEN/GLM_API_KEY)
#   /root/auto_trade/ai_research_consent.json

# 3) 一键启动（生产机 VECTOR_ROOT=/root）
export VECTOR_ROOT=/root
bash scripts/run_auto_driver.sh --config scripts/auto_driver_config.json

# 或直接：
python3 scripts/run_auto_driver.py --config scripts/auto_driver_config.json
```

离线冒烟（不调管道、不调 AI）：

```bash
python3 scripts/run_auto_driver.py \
  --pack strategy_session_liq_engulf_matrix_v1b.json \
  --dry-run --dry-run-skip-ai
```

## 行为说明

| 阶段 | 行为 |
|------|------|
| L1 失败 | 先做 `l1_seed_retries` 次无 AI 种子重试（与现有 submit_* 一致） |
| 非 L1 失败 / L1 耗尽 | 打包 failure_context → DeepSeek/Qwen/GLM → 合并补丁 |
| Gate2 viability 后 | 自动 `mechanism_family` 后缀 `_adN`，降低下一轮立刻 `kb_blocked` |
| 补丁失败 / DSL 校验失败 | 回滚到本轮 `pack.before.json` |
| 终止 | SUCCESS / AI_LIMIT_REACHED / 收敛 / 达 max_iterations / AI_ABORT |

**不会**：`--confirm`、live mount、改管道源码、静默解锁 KB。

## 终止条件

- `SUCCESS`：`run_creation_pipeline_step_a` 返回 `ok=true`
- `AI_LIMIT_REACHED`：≥2 家 AI 或合并结果判定无优化空间
- `CONVERGED_NO_IMPROVEMENT`：连续 N 轮 composite_score 改善 < ε
- `NO_PROGRESS_REPEATED_REASON`：连续同 reason 且分数无抬升
- `MAX_ITERATIONS`：默认 10
- `AI_ABORT`：密钥/授权缺失或无可用提案
- `KB_BLOCKED_EXHAUSTED`：family bump 次数用尽

## 产出

工作目录（默认）：

`auto_trade/dual_engine/workflow_v2/auto_driver_runs/<timestamp>/`

- `pack_initial.json` / `pack_final.json`
- `iter_XX/pack.before.json` / `pack.after.json`
- `iter_XX/failure_context.json` / `ai_merged.json`
- `delivery_report.md`（并复制到 `VECTOR_ROOT/delivery_report.md`）

## 模块

```
scripts/run_auto_driver.py          CLI
scripts/run_auto_driver.sh          shell 入口
scripts/auto_driver_config.example.json
scripts/auto_driver/
  driver.py         主循环
  metrics.py        Gate/L1 指标与缺口
  ai_optimize.py    3 方 AI 提案与合并
  patch_apply.py    补丁应用与快照回滚
  limits.py         极限/收敛判定
  report.py         delivery_report.md
```

## AI 补丁协议（摘要）

AI 只输出 JSON：`decision=PATCH|LIMIT_REACHED|ABORT` + `patches[]`。

支持的 `op`：`set` / `replace_dsl` / `merge_dsl` / `replace_entry` / `replace_exit` /
`merge_mechanism_spec` / `rename_family` / `set_max_hold_bars` / `noop`。

调用复用现网 `auto_trade_dual_engine_factory._ai_json`（DeepSeek / Qwen / GLM）。
