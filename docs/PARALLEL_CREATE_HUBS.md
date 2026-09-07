# 并行创造双 Hub 隔离（GitHub 协同）

两套 Cursor / 两套 thin hub 同时创造时，用本文件约定边界。  
目标：防互踩、可追溯；**不**用 GitHub 管实盘挂载或评测队列。

## 现网拓扑

| 侧 | systemd | 队列 | 日志 | Mac 工人 |
|---|---|---|---|---|
| **hub-a（本侧默认）** | `qiyu-kimi-thin-hub` | `/tmp/kdh_thin` | `/tmp/kimi_thin_hub.log` | `com.qiyu.research-host-eval-worker`（`--roots /tmp/kdh_thin`，4 路） |
| **hub-b（对侧）** | `qiyu-kimi-thin-hub-b` | `/tmp/kdh_thin_b` | `/tmp/kimi_thin_hub_b.log` | `com.qiyu.research-host-eval-worker-b`（`--roots /tmp/kdh_thin_b`，4 路） |

### 发明车道（2026-09-07）

| 车道 | 绑定 | 上游（现网） |
|---|---|---|
| `primary` | kimi primary | `https://api2.cmkey.cn/v1`（原 `cmkey.cn` 易 504） |
| `backup` | kimi backup | `https://yuanyuaicloud.cn/v1`（`kimi-k3`） |
| `cr2` | deepseek | `https://vectide.cn/v1`（`deepseek-v4-pro-0813`） |
| `eq2` / qwen | **关闭** | `KDH_DISABLE_QWEN_OUTLET=1` |

`KDH_THIN_LANES=primary,backup,cr2`  
`KDH_LANE_BIND=primary:primary,backup:backup,cr2:deepseek`  
`KDH_DISABLE_CONGESTION_OUTLETS=0`

若某 Cursor 会话明确声明「本侧=hub-b」，以该会话声明为准；默认文档按 hub-a=本侧。

## 硬禁止

1. **禁止** `systemctl restart` / `stop` **对方**的 hub 服务。  
2. **禁止**往对方队列目录丢 recipe / 清 inflight。  
3. **禁止**把 Mac worker 的 `KDH_THIN_ROOTS` 重新合并成共享 8 路抢队列（除非 `shared-*` PR 明确改架构）。  
4. **禁止**在 `/home/admin` 下验收生产 Python（见 `prod-python-path` rule）：必须 `cd /root && PYTHONPATH=/root`。

## 分支前缀（P1 强制）

| 前缀 | 用途 | 谁开 |
|---|---|---|
| `cursor/hub-a-*` | 仅影响 hub-a / `/tmp/kdh_thin` / worker-a | 本侧 |
| `cursor/hub-b-*` | 仅影响 hub-b / `/tmp/kdh_thin_b` / worker-b | 对侧 |
| `cursor/shared-*` | 共享清单改动（见下） | 任一侧，须对侧知情 |

禁止用对方前缀开分支改对方运行时。共享改动必须用 `cursor/shared-*`。

## 共享文件（不经 PR 不上 VPS）

下列文件影响**两侧**创造口，改完必须：

1. 开 PR 到 `main`（`cursor/shared-*`）；  
2. CI `kimi-provider-failover`（及相关单测）绿；  
3. **合并后**打 tag（P2）→ 再部署到 VPS / 更新 Mac worker；  
4. 部署后写 `/root/deployed.sha`，用 `scripts/check_deployed_sha.sh` 对账。

**共享清单：**

- `dual_engine_workflow_v2/kimi_provider.py`
- `dual_engine_workflow_v2/creation_small_n_rigor.py`（P0–A 小样本严谨层）
- `scripts/kimi_thin_recipe_loop.py`
- `scripts/research_host_eval_worker.py`
- `scripts/kimi_dual_http_create_20260906.py`（若作库被 thin 引用）
- `.github/workflows/kimi-provider-failover.yml`
- 本文件与 `.cursor/rules/parallel-create-hubs.mdc`

小样本严谨层说明与验收：`docs/ci/CREATION_SMALL_N_RIGOR_PHASE0_A.md`。 Phase B–C：`docs/ci/CREATION_SMALL_N_RIGOR_PHASE_B_C.md`（b=标定 timing hard；a=钝侧统计 off）。  
hub 挂接用 `creation_small_n_rigor.install_into_kdh`；**禁止**两侧同日把 PLACEBO/LOO/MC 升为 hard。

仅动本侧 unit drop-in / inherit 实例可不经 shared PR，但**不得**顺手改共享清单内文件却只 scp 到 `/root`。

## inherit 种子（P1）

| 路径 | Git |
|---|---|
| `auto_trade/dual_engine/sole_creation_runs/kimi_thin_hub_{a,b}_inherit_seeds.example.json` | **提交**（模板） |
| `/root/.../kimi_thin_hub_{a,b}_inherit_seeds.json`（现网） | **不提交** |

## 部署对账（P1）

```bash
./scripts/write_deployed_sha.sh <sha-or-tag>
./scripts/check_deployed_sha.sh
```

## Mac worker 跟 tag（P2）

`scripts/mac_eval_worker/run_hub_a.sh.example` / `run_hub_b.sh.example`：  
若设置 `QIYU_CODE_TAG`，启动前要求工作树 `HEAD` 精确等于该 tag，否则退出。

## 合并后 tag + 可选部署 Action（P2）

- 合并 `shared-*` 到 `main` 后打 `create-collab-YYYYMMDD.N`。  
- `.github/workflows/deploy-vps-approve.yml`：`workflow_dispatch` + environment `vps-prod`（需人工 approve）。

## 对侧重启创造

见 `docs/ci/HANDOFF_OTHER_CURSOR_CREATE_RESTART.md`。

## 不进 Git 的运行时

- `/tmp/kdh_thin*` 队列与结果  
- `*_inherit_seeds.json` 现网实例（仅 `*.example.json` 进仓）  
- API Key / `.env` / `vectide_deepseek.env`  
- `.research_vector`
