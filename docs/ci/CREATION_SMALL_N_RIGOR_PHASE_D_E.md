# Phase D–E：对侧跟随 + 冻结防回潮

日期：2026-09-07  
前置：Phase B–C（tag `create-collab-20260907.4`）

## Phase D — 目标与任务

| 目标 | 把标定侧已验证条款同步到钝侧，仍允许档差 |
| 跟随 | `CREATE_TIMING_BUDGET=hard`（已在 C 验证） |
| 不跟随 | placebo / LOO / MC / noise（a 保持 off） |
| 任务 | ① 更新 `FROZEN_HUB_A` / hub-a example ② handoff ③ 本侧不 restart a |
| 完成标准 | a 示例 phase=D、timing=hard、统计=off；与 b 档位表可查且统计不同 |

## Phase E — 目标与任务

| 目标 | 冻结推荐默认；防 waive / 凑 n / 双侧 hard 统计回潮 |
| 任务 | ① `FROZEN_HUB_B` phase=E ② `frozen_invariants()` ③ 单测锁 ④ 文档写入 PARALLEL/handoff |
| 完成标准 | boot `phase=E` + `small_n_frozen`；invariants ok；CI 绿 |

## 冻结表

| 键 | hub-b（标定冻结） | hub-a（跟随冻结） |
|---|---|---|
| ANTI_EVASION | 1 | 1 |
| N_DISCOUNT | 1 | 1 |
| TIMING_BUDGET | hard | hard |
| PLACEBO/LOO/MC | observe | off |
| NOISE_STRESS | off | off |
| PHASE | E | D |

## 验收清单

1. unittest：frozen_invariants + never_waive + a timing hard / stats off  
2. hub-b boot：phase=E，timing=hard，frozen=true  
3. promotion_log 含 PHASE_E_FREEZE  
4. hub-a 服务未被本侧重启；示例已更新供对侧挂接  
5. `/root/deployed.sha` = tag peel commit  

## 禁止（冻结后）

- 回引 `_waive_trade_count_only` 放行  
- 静默把双侧 PLACEBO/LOO/MC 同升 hard  
- 新条款默认进 hard（须先 O）
