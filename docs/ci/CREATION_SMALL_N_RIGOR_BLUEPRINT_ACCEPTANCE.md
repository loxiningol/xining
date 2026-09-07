# Phase 0–E 蓝图全域验收（2026-09-07 对齐版）

创造服务保持 **stopped/disabled**；本文件验收代码/文档/VPS 文件落地，不 restart hub。

## 蓝图口径（强制）

| 侧 | 角色 | Day-0（B） | 冻结（E） |
|---|---|---|---|
| **hub-a** | 标定 | timing soft + 统计 observe | timing **hard** + 统计 **observe** |
| **hub-b** | 钝侧 | timing soft + 统计 **off** | timing **hard**（跟随）+ 统计 **off** |

## Phase 0 — 基线表（改前 ~14 天日志抽样）

| 指标 | hub-a | hub-b |
|---|---|---|
| atom_eval 过门率 | 2.5%（28/1118） | 0%（0/1206） |
| waive_true（日志） | 0 | 0 |
| 低 n 高 C（n\<30 且 C_week_pct≥0.1） | 305 | 509 |
| 平均 timing 叶数 | 2.628（n=2407） | 2.353（n=2098） |

### H/S/O 与码表

| 档 | 含义 | 代表码 |
|---|---|---|
| H | failed_rules | `n_waive_forbidden`；`timing_dim_above_budget`（hard 时） |
| S | warnings + 回灌 | `timing_dim_above_budget`（soft）；`n_discounted_*` |
| O | evidence only | `placebo_not_top5pct` / `loo_mean_unstable` / `mc_subset_variance_too_high` |

金标：`docs/ci/creation_small_n_gold_samples.json`

## Phase A — 共享骨架

| 任务 | 状态 |
|---|---|
| creation_small_n_rigor H/S/O | ✅ |
| 永久删除 waive 生效路径 | ✅ `scripts/patch_invent_kill_n_waive.py` + install monkeypatch |
| 禁凑 n 提示 | ✅ |
| n/(n+κ) 软回灌 | ✅ |
| timing 默认可 S；冻结 hard | ✅ |
| 置换/LOO/MC 默认 O/off | ✅ |
| 单测金标 | ✅ |
| PR+CI | 本对齐 PR |

## Phase B–E

| 阶段 | 状态 |
|---|---|
| B 分侧 a 标定 / b 钝 | ✅ 剖面 `PROFILE_HUB_*_PHASE_B` |
| C a 升 timing→hard | ✅ 写入 `PROFILE_HUB_A_PHASE_C` / 冻结 |
| D b 跟随 timing hard | ✅ `PROFILE_HUB_B_PHASE_D` |
| E 冻结 + invariants | ✅ `FROZEN_HUB_A/B` + 单测锁 |

## 验收命令（不启 hub）

```bash
cd /root && PYTHONPATH=/root python3 -m unittest tests_workflow_v2.test_creation_small_n_rigor -v
python3 /root/scripts/patch_invent_kill_n_waive.py   # idempotent
python3 -c 'from dual_engine_workflow_v2.creation_small_n_rigor import frozen_invariants; print(frozen_invariants())'
```

Hubs 必须保持 inactive/disabled，直至创造重构完成再启。
