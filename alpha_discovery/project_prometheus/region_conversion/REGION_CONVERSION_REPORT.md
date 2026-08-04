# Phase0 Region → Strategy 转化报告

- at: 2026-08-04 23:13 CST
- contract locked: WR≥**0.65** · mean_win_net≥**0.1111** · weekly≥**0.1** · stop=0.5% · lev=20
- 未全市场搜索；未 AI 从零造策略；先做严格样本外复现

## 最终成功标准

`production_formal_pass_strategy_count`：**0**（未进入 Formal）

---

## 八问必答

### 1. Region A 是否严格样本外复现？

**NO** → 标记 **`PHASE0_FALSE_POSITIVE`** · `may_strategize=false`

| split | wr | mean_win_net | weekly | n | gates |
|---|---|---|---|---|---|
| full_sample | 0.665 | 0.127 | 103 | 576 | PASS（选择偏倚样本，不足为证） |
| train | 0.612 | 0.133 | 101 | 291 | FAIL wr |
| validation | 0.920 | 0.133 | 167 | 100 | PASS |
| test | 0.750 | 0.119 | 131 | 76 | PASS |
| holdout_tail | **0.514** | **0.105** | 106 | 109 | **FAIL** wr+net |
| rolling 1–4 | 0.92 / 0.96 / **0.45** / **0.50** | — | — | — | **2/4 FAIL** |
| recent_2y | — | — | — | — | **FAIL** 数据仅 **41.7d** ≪ 600d |

失败阶段：**区域复现（holdout + rolling 后半失效）**

### 2. Region B 是否严格样本外复现？

**NO** → 标记 **`PHASE0_FALSE_POSITIVE`** · `may_strategize=false`

| split | wr | mean_win_net | weekly | n | gates |
|---|---|---|---|---|---|
| full_sample | 0.642 | 0.123 | 74 | 165 | FAIL wr（stride=1 全量已低于 0.65） |
| train | 0.784 | 0.123 | 70 | 74 | PASS |
| validation | 0.743 | 0.116 | 105 | 35 | PASS |
| test | **0.143** | **0.097** | 125 | 7 | **FAIL** |
| holdout_tail | **0.429** | 0.131 | 73 | 49 | **FAIL** wr |
| rolling | 1/4 PASS | — | — | — | **FAIL** |
| recent_2y | — | — | — | — | **FAIL** 数据 41.7d + wr |

失败阶段：**区域复现（test/holdout 崩塌）**

### 3. 是否形成正式策略？

**NO**（按规则：OOS 失败不得策略化 → 未做 Event Trigger / Exit / Formal）

### 4. 是否满足 65% 胜率（严格 OOS）？

**NO**（A holdout 0.514；B test 0.143 / holdout 0.429）

### 5. 是否满足 11.11% 盈利单均值（严格 OOS）？

**NO**（A holdout 0.105；B test 0.097）

### 6. 是否满足周频 0.1？

周频在多数切分上 **数值够**，但因 wr/net 已 FAIL，整体不成立。

### 7. 是否通过生产 Formal？

**NO**（未启动）

### 8. 若失败，失败发生在哪？

**区域复现（Region OOS reproduction）**

未进入：事件触发 / 退出搜索 / Formal。

---

## 决策

| Region | 状态 | 下一步 |
|---|---|---|
| A | PHASE0_FALSE_POSITIVE | **停止策略化** |
| B | PHASE0_FALSE_POSITIVE | **停止策略化** |

全样本好看、前半段 rolling 好看，后半段失效 → Phase0 区域为时间不稳定的伪发现，不是可 Formal 的机制。

证据：`alpha_discovery/project_prometheus/region_conversion/REGION_OOS_REPRODUCE.md`
