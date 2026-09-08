# 单一创造系统（并行双 Hub 已退役）

自 2026-09-09 起：**只有一个创造系统**，不再有 hub-a / hub-b 对照或队列隔离。

## 现网拓扑

| 项 | 值 |
|---|---|
| systemd | **仅** `qiyu-kimi-thin-hub` |
| ExecStart | `/root/scripts/kimi_thin_recipe_loop_hub.py`（`hub_a_strict.py` 为兼容别名） |
| 队列 | `/tmp/kdh_thin` |
| 日志 | `/tmp/kimi_thin_hub.log` |
| Mac worker | `com.qiyu.research-host-eval-worker` → `--roots /tmp/kdh_thin`（建议 8 路） |
| 车道 | `1→primary(kimi1)`，`2→backup(kimi2)`；`KDH_INVENT_STRICT_BIND=1` |

**已退役：** `qiyu-kimi-thin-hub-b`、`/tmp/kdh_thin_b`、`com.qiyu.research-host-eval-worker-b`、`KDH_THIN_NS=b`。

## 硬禁止（更新）

1. **禁止**再启 hub-b / 再开第二套 invent 队列抢 Mac。  
2. **禁止**把「对侧 Cursor」当成第二创造中枢；共享改动仍走 `cursor/shared-*` PR。  
3. 验收 Python：`cd /root && PYTHONPATH=/root`（见 `prod-python-path`）。

## 共享文件（不经 PR 不上 VPS）

仍适用：

- `dual_engine_workflow_v2/kimi_provider.py`
- `scripts/kimi_thin_recipe_loop.py`
- `scripts/kimi_thin_recipe_loop_hub.py`
- `scripts/research_host_eval_worker.py`
- `scripts/kimi_dual_http_create_20260906.py`
- `dual_engine_workflow_v2/invent_*.py`（hypothesis / reward / element）

紧急 scp 须 `note_emergency_scp` + 30 分钟内 `deployed.sha`。

## 分支前缀

| 前缀 | 用途 |
|---|---|
| `cursor/shared-*` | 创造主链共享改动（默认） |
| `cursor/hub-a-*` / `cursor/hub-b-*` | **废弃**；勿再开 |

历史双 hub 文档段落保留仅作考古；以本文件「单一创造系统」为准。
