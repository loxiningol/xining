## Summary
<!-- 1-3 bullets: why this change -->

## Services touched
<!-- Check all that apply -->
- [ ] `qiyu-kimi-thin-hub` (hub-a)
- [ ] `qiyu-kimi-thin-hub-b` (hub-b)
- [ ] Mac worker-a (`com.qiyu.research-host-eval-worker` / `/tmp/kdh_thin`)
- [ ] Mac worker-b (`…-worker-b` / `/tmp/kdh_thin_b`)
- [ ] Other systemd / web / formal（写明）:
- [ ] Docs / CI only（无运行时）

## Shared invent files?
- [ ] **No** — hub-local / docs only
- [ ] **Yes** — touches shared list (`kimi_provider` / `kimi_thin_recipe_loop` / `research_host_eval_worker` / dual create HTTP). Branch must be `cursor/shared-*`; wait for CI green before any VPS copy.

## Restart required after merge/deploy?
- [ ] None
- [ ] Restart hub-a only
- [ ] Restart hub-b only
- [ ] Restart both hubs（需双方知情）
- [ ] Reload Mac worker-a / worker-b（写明）

## Deploy checklist
- [ ] PR CI green（`kimi-provider-failover` when workflow scope enabled）
- [ ] Merged to `main`
- [ ] Tag created（P2 shared deploy）
- [ ] VPS files updated from tag/sha（not ad-hoc dirty tree）
- [ ] `/root/deployed.sha` written (`scripts/write_deployed_sha.sh`)
- [ ] `scripts/check_deployed_sha.sh` OK from Mac

## Risk / rollback
<!-- one line -->
