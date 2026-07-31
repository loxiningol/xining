# 策略创造蓝图 v2 — 唯一入口

**SOLE ENTRY：** `scripts/strategy_create_sole.py` → `creation_sole_entry.create_strategy`  
其它创造路径（ecosystem / mass / factory / blank dual-engine）一律拒绝或重定向至此。

## 强制异构委员会（独立提交，禁止同模型聊天闭环）

| 角色 | 执行体 | 禁止 |
|---|---|---|
| Research Director | 规则预算 | 不提策略 |
| Mechanism Scientist | `mechanism_graph`（可不看收益） | 先看回测收益 |
| Empirical Scientist | `phenomenon_scanner` | 写交易规则 |
| Symbolic Searcher | `symbolic_searcher` GP-lite（非LLM） | 写故事 |
| Antifalsify Auditor | `antifalsify` 统计 | 帮主假设优化 |
| Constructive Red Team | 反方家族裸探针 | 为主假设开脱 |
| Statistician | DSR/PBO/ledger | LLM 裁决 |
| Judge | 证据字段规则（Kimi 可选且默认关） | 生成新策略 |

所有角色只向 `research_blackboard` **追加**证据。

## 强制多阶段

0 契约 → 1 种群 → 2 去重/MAP-Elites → 3 裸探针 → 4 反证矩阵 → 5 EFR → 6 多重检验 → 7 组装 → 8（另途）ADA5 复核

## 命令

```bash
python3 scripts/strategy_create_sole.py --symbol ADA-USDT-SWAP --timeframe 5m --brief "..."
```
