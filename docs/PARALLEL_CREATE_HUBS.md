# 并行创造双 Hub 隔离（GitHub 协同 P0）

两套 Cursor / 两套 thin hub 同时创造时，用本文件约定边界。  
目标：防互踩、可追溯；**不**用 GitHub 管实盘挂载或评测队列。

## 现网拓扑

| 侧 | systemd | 队列 | 日志 | Mac 工人 |
|---|---|---|---|---|
| hub-a（对侧） | `qiyu-kimi-thin-hub` | `/tmp/kdh_thin` | `/tmp/kimi_thin_hub.log` | `com.qiyu.research-host-eval-worker`（`--roots /tmp/kdh_thin`，4 路） |
| hub-b（本侧） | `qiyu-kimi-thin-hub-b` | `/tmp/kdh_thin_b` | `/tmp/kimi_thin_hub_b.log` | `com.qiyu.research-host-eval-worker-b`（`--roots /tmp/kdh_thin_b`，4 路） |

发明车道（两侧对称）：`primary,backup,eq2,cr2`  
默认口绑定：`primary→kimi primary`，`backup→kimi backup`，`eq2→qwen`，`cr2→deepseek`（429 仍按 invent 链 failover）。

## 硬禁止

1. **禁止** `systemctl restart` / `stop` **对方**的 hub 服务。  
2. **禁止**往对方队列目录丢 recipe / 清 inflight。  
3. **禁止**把 Mac worker 的 `KDH_THIN_ROOTS` 重新合并成共享 8 路抢队列（除非 `shared-*` PR 明确改架构）。  
4. **禁止**在 `/home/admin` 下验收生产 Python（见 `prod-python-path` rule）：必须 `cd /root && PYTHONPATH=/root`。

## 共享文件（不经 PR 不上 VPS）

下列文件影响**两侧**创造口，改完必须：

1. 开 PR 到 `main`（或经 `shared-*` 分支合并）；  
2. CI `kimi-provider-failover`（及相关单测）绿；  
3. **合并后**再部署到 VPS / 更新 Mac worker 代码。

**共享清单（P0）：**

- `dual_engine_workflow_v2/kimi_provider.py`
- `scripts/kimi_thin_recipe_loop.py`
- `scripts/research_host_eval_worker.py`
- `scripts/kimi_dual_http_create_20260906.py`（若作库被 thin 引用）
- `.github/workflows/kimi-provider-failover.yml`
- 本文件与 `.cursor/rules/parallel-create-hubs.mdc`

仅动本侧 unit drop-in（如 hub-b 的 `lanes.conf`、inherit 种子实例）可不经 shared PR，但**不得**顺手改共享清单内文件却只 scp 到 `/root`。

## 分支建议

| 前缀 | 用途 |
|---|---|
| `cursor/hub-a-*` | 对侧专用 |
| `cursor/hub-b-*` | 本侧专用 |
| `cursor/shared-*` | 共享清单改动 |

`main` 受保护：仅 PR 合并；禁止 force push。

## 部署检查（人工）

```bash
# 本侧只重启 b
sudo systemctl restart qiyu-kimi-thin-hub-b.service

# 对侧只重启 a
sudo systemctl restart qiyu-kimi-thin-hub.service

# 对账（有 tag/sha 后）
git rev-parse HEAD
ssh … 'sudo cat /root/deployed.sha'   # P1 落地后
```

## 不进 Git 的运行时

- `/tmp/kdh_thin*` 队列与结果  
- `kimi_thin_hub_*_inherit_seeds.json` 实例（可用 `*.example.json` 模板）  
- API Key / `.env`  
- `.research_vector`
