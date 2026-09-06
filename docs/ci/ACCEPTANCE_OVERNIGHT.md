# GitHub 协同 P0–P2 隔夜验收报告

日期：2026-09-06（隔夜）  
仓库：https://github.com/loxiningol/xining  
PR：https://github.com/loxiningol/xining/pull/3  

## 结论摘要

| 项 | 状态 | 备注 |
|---|---|---|
| P0 docs + Cursor rule（禁碰对方 hub） | 已合入分支 | 默认本侧=hub-a |
| P0 failover 单测 + kimi_provider 种子 | 已合入 | 本地 15 测 ×5 绿 |
| P0 Actions workflow YAML | **待 workflow scope** | 停在 `docs/ci/*.yml.pending`；OAuth 无 `workflow` 无法 push `.github/workflows` |
| P0 保护 main | **阻塞** | 私有免费仓 API 403；需 Pro 或公开仓。脚本已留 `scripts/enable_main_branch_protection.sh` |
| P1 分支前缀 | 已文档化 | `cursor/hub-a-*` / `hub-b-*` / `shared-*` |
| P1 deployed.sha 对账 | 脚本已就绪 | `check_deployed_sha.sh` / `write_deployed_sha.sh`；见下方现网验收 |
| P1 inherit example | 已合入 | `*.example.json` 进仓；现网 `*_inherit_seeds.json` gitignore |
| P2 PR 模板 | 已合入 | `.github/PULL_REQUEST_TEMPLATE.md` |
| P2 tag + approve deploy Action | 模板 pending | `docs/ci/deploy-vps-approve.yml.pending` |
| P2 Mac worker 跟 tag | example + 本机已对齐 | `QIYU_CODE_TAG` 校验 |

## 明早你需要点一下的（约 2 分钟）

1. `export PATH="$HOME/.local/bin:$PATH"`  
2. `gh auth refresh -h github.com -s repo,workflow`（设备码授权）  
3. `./scripts/restore_github_workflows.sh` → commit push → 确认 Actions 绿  
4. 二选一开 branch protection：升级 Pro，或（若可接受）公开仓后跑 `./scripts/enable_main_branch_protection.sh`

## 现网验收命令（本侧 hub-a）

```bash
# 不碰 hub-b
ssh … 'systemctl is-active qiyu-kimi-thin-hub qiyu-kimi-thin-hub-b'
./scripts/check_deployed_sha.sh
```

（具体跑分结果写在同目录 `ACCEPTANCE_OVERNIGHT_RUNLOG.txt`）

## 隔夜执行备注（自动）

- 本机 Mac `run.sh` / `run_b.sh` 已对齐 tag-pin example（默认不设 `QIYU_CODE_TAG`，工人照常跑）。
- `31cf280`（P1/P2）曾因 GitHub 443 未推上；后台 `push_watch` 会在网络恢复后 push → merge PR#3 → tag `create-collab-20260906.1` → 写 `/root/deployed.sha`。
- 查进度：`cat /tmp/push_watch.log`
