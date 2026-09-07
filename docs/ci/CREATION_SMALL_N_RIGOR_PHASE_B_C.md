# Phase B–C：分侧默认 + 标定侧首条升档

日期：2026-09-07  
前置：Phase 0–A（tag `create-collab-20260907.3`）

## 归属修正

规则强制 **本侧=hub-b**。故标定侧改挂 **hub-b**；对侧 hub-a 为钝侧。  
（原口述「a 标定 / b 钝」在归属纠正后对调，避免本侧无法验收 Phase C。）

## Phase B — 目标与任务

| 目标 | 同一代码、不同默认；立刻削弱作恶；避免双侧同激进 |
| 任务 | ① hub-b=标定（统计 observe）② hub-a=钝（统计 off）③ 文档/handoff ④ 本侧 restart b |
| 完成标准 | boot 可见 `hub_role`；a/b 档位表可查且不同 |

## Phase C — 目标与任务

| 目标 | 标定侧一次只升一条；钝侧不跟随 |
| 本轮首条 | `CREATE_TIMING_BUDGET` soft→**hard**（削弱堆叶凑过） |
| 不升 | placebo / LOO / MC（仍 observe；满 ≥50 kimi_ask 后再议 O→S） |
| 任务 | ① 写 promotion_log ② hub-b 应用 hard 叶预算 ③ hub-a 保持 Phase B ④ 验收 |
| 完成标准 | boot `phase=C` + `timing_budget=hard`；promotion_log 有一条；统计非 hard |

## 验收清单

1. 单测含 profile / phase_c_ready / timing hard  
2. VPS hub-b boot：`small_n_phase=C`，`timing_budget=hard`，`placebo=observe`  
3. `creation_small_n_promotion_log.json` 含 timing soft→hard  
4. waive 仍不可用  
5. hub-a **未**被本侧重启；示例 wrapper 为 Phase B 钝侧  

## 下一升档（未做）

满 `PROMOTION_MIN_ASKS=50` 后，**仅 hub-b** 可考虑 `CREATE_PLACEBO=observe→soft`；hub-a 不跟随。
