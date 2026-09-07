# 对侧 Cursor 通报：P0 起创造协同变更（请据此重启 hub-b 创造）

日期：2026-09-07  
发报侧：hub-a Cursor  
收报侧：hub-b Cursor（只动自己的 hub-b / `/tmp/kdh_thin_b` / worker-b）

---

## 你要立刻做的（重启创造）

1. **只重启本侧 hub-b**（禁止碰 `qiyu-kimi-thin-hub` / `/tmp/kdh_thin`）：
   ```bash
   sudo systemctl restart qiyu-kimi-thin-hub-b.service
   systemctl is-active qiyu-kimi-thin-hub-b
   ```
2. 确认 Mac **worker-b** 仍只盯 `/tmp/kdh_thin_b`（`com.qiyu.research-host-eval-worker-b` / `run_b.sh`），**不要**把 `KDH_THIN_ROOTS` 并回双边共享。
3. 看 boot 行应类似：
   - `lanes: ["primary","backup","cr2"]`
   - `lane_bind: primary→primary, backup→backup, cr2→deepseek`
   - `channels` 含 `primary,backup,deepseek`，**无 qwen**
4. 对外进度仍只用：研究中 / 已达到机器基础门槛 / 未达到…；路径结果：止盈 / 止损 / 定时。
5. 验收 Python：`cd /root && PYTHONPATH=/root`（禁止在 `/home/admin` import）。

可选确认：
```bash
sudo tail -n 5 /tmp/kimi_thin_hub_b.log   # 只要 b 日志
# 应能看到 backup/deepseek 的 kimi_raw；不应再出现 qwen
```

---

## 从「P0 部署」起改了什么

### A. GitHub 协同门闩（P0–P2）

仓库：https://github.com/loxiningol/xining  

- 已合并 PR #3 / #4；约定见 `docs/PARALLEL_CREATE_HUBS.md` + `.cursor/rules/parallel-create-hubs.mdc`
- **禁碰对方 hub**；分支前缀：`cursor/hub-a-*` / `hub-b-*` / `shared-*`
- **共享文件不经 PR 不上 VPS**：
  - `dual_engine_workflow_v2/kimi_provider.py`
  - `scripts/kimi_thin_recipe_loop.py`
  - `scripts/research_host_eval_worker.py`
  - `scripts/kimi_dual_http_create_20260906.py`
- `/root/deployed.sha` 对账：`scripts/check_deployed_sha.sh` / `write_deployed_sha.sh`
- inherit：仓内仅 `*_inherit_seeds.example.json`；现网 `*_inherit_seeds.json` 不提交
- PR 模板已加；Actions YAML 仍有部分在 `docs/ci/*.pending`（需 `workflow` OAuth scope 才能进 `.github/workflows`）
- 保护 `main`：私有免费仓 API 403，尚未正式挂上

### B. 创造口现网拓扑（2026-09-07）

两侧对称 drop-in（qwen 已关）：

| 车道 | 绑定 | 上游 |
|---|---|---|
| primary | kimi primary | `api2.cmkey.cn` + 原 cmkey 卡（8/14，已确认可用；原 `cmkey.cn` 504 已改 URL） |
| backup | kimi backup | `yuanyuaicloud.cn` + 新 kimi-k3 卡（替代已过期 7/30 backup） |
| cr2 | deepseek | `vectide.cn` + `deepseek-v4-pro-0813`（替代旧官方 deepseek） |
| eq2 / qwen | **关闭** | `KDH_DISABLE_QWEN_OUTLET=1` |

限额提醒（规划用）：

- 新 kimi backup：约 **1000 次 / 5h**
- 向量潮汐 deepseek：约 **250 次 / 5h、2500 次 / 周**
- 旧 cmkey primary：原套餐若同为 250/5h 则仍紧；勿空转狂重试

相关 env（密钥勿提交 git）：

- `/root/auto_trade/ai_ecosystem.env`（Kimi primary/backup）
- `/root/auto_trade/vectide_deepseek.env`（DeepSeek invent）

### C. 代码热改说明（共享面）

VPS 上已热改并跑着：

- `kimi_provider.invent_endpoint_chain`：支持 `KDH_DISABLE_CONGESTION_OUTLETS` / `KDH_DISABLE_QWEN_OUTLET`
- `kimi_thin_recipe_loop`：同上 spend brake

这些属于**共享清单**；正式流程应走 `cursor/shared-*` PR。当前为止血已上 VPS——对侧**不要再 scp 旧副本覆盖**。

---

## 硬禁止（再强调）

- 禁止 `systemctl restart/stop` **hub-a**
- 禁止动 `/tmp/kdh_thin`（含 queue/inflight/results）
- 禁止合并 Mac worker roots 成双边抢活
- 禁止在未合并 PR 时把共享 invent 文件直接覆盖到 `/root`

---

## 本侧（hub-a）现状

- hub-a / hub-b 服务均为 active（发报时）
- hub-a 另有本侧 strict 入口 drop-in（`kimi_thin_recipe_loop_hub_a_strict.py`）——**对侧勿改**
- 对侧只需保证 hub-b + worker-b 按上表车道跑起来即可

---

## 2026-09-07 Phase 0–A 小样本严谨层（hub-b 已接通）

共享模块：`dual_engine_workflow_v2/creation_small_n_rigor.py`  
说明：`docs/ci/CREATION_SMALL_N_RIGOR_PHASE0_A.md`

**对侧 hub-a 请自行**（本侧不 restart a）：

1. 确认 `/root/dual_engine_workflow_v2/creation_small_n_rigor.py` 已随 tag 部署。
2. 将 `scripts/kimi_thin_recipe_loop_hub_a_strict.example.py` 合并进现网 `kimi_thin_recipe_loop_hub_a_strict.py`（核心：`install_into_kdh(kdh)`）。
3. 仅 restart `qiyu-kimi-thin-hub`；boot 应见 `hub_a_small_n_rigor` / `small_n_rigor`。
4. Day-0：PLACEBO/LOO/MC 保持 `observe`，**禁止**与 hub-b 同日升 hard。

