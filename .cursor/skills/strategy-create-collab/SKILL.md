---
name: strategy-create-collab
description: >-
  MANDATORY sole creation path. When the user orders a new trading strategy,
  ALWAYS call scripts/strategy_create_sole.py (or creation_sole_entry.create_strategy).
  Never invent DSL directly, never use ecosystem/mass/factory/dual-engine blank create.
  Research discovery first; ADA5 review is separate.
---

# 唯一创造管道（强制）

## 铁律

1. **人类给 Cursor/Codex 的创造指令 → 只许走 sole entry**  
   `python3 scripts/strategy_create_sole.py --symbol ... --timeframe ... --brief "..."`  
   等价：`strategy_create_blueprint.py` / `strategy_create_collab.py`（已重定向）。
2. **禁止**：直接写 DSL、ecosystem `--create-strategy` 旧逻辑、mass/factory、Web 空白 dual-engine 创造、跳过研究发现进 STEP A。
3. **强制异构委员会（独立提交，不聊天）**：机制研究者 / 数据研究者 / 符号搜索者 / 反证 / 构造型红队 / 统计裁判 / Judge（Kimi 默认关闭）。
4. **强制多阶段**：契约→种群→裸探针→反证矩阵→EFR→多重检验→（仅存活）组装。
5. 工具必须真算（ledger/IC/探针/DSR/PBO/MAP-Elites），禁止名称包装空跑。

## 入口

```bash
python3 scripts/strategy_create_sole.py \
  --symbol ADA-USDT-SWAP --timeframe 5m \
  --brief "人类原话"
```
