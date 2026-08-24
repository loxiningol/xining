# VPS 文档同步状态

> 同步时间：2026-08-24  
> 生产主机：`admin@47.81.25.235`（阿里云，hostname `iZ0johqy12n1gsg5pwlzb5Z`）  
> 旧主机 `root@64.176.47.192`（Vultr）已不可达（SSH/ICMP 超时）

## 同步结论

| 路径 | 状态 | 说明 |
|------|------|------|
| `/root/docs/` ↔ `docs/` | **已一致** | 45 个文件 MD5 对齐；含 `STRATEGY_CREATION_MODULE_CURRENT_STATE.md`（23489B，2026-08-07） |
| `p5_verification/` | **已一致** | `FINAL_ACCEPTANCE_REPORT.md` 等 |
| `p6_quality/` | **已一致** | `P6_ACCEPTANCE_REPORT.md` 等 |
| `p7_structural/` | **已一致** | `P7_ACCEPTANCE_REPORT.md` 等 |
| `p7_1_closure/` | **已一致** | `P7_1_ACCEPTANCE_REPORT.md` 等 |
| `p7_1_formal/` | **已一致** | 正式验收 JSON/MD |
| 仓库根目录全景报告 | **已一致** | `QUANT_SYSTEM_PANORAMA_*.md` 等 |

## 本地独有（生产 `/root/docs/` 无，保留不覆盖）

- `docs/PRODUCTION_SOURCE_OF_TRUTH.md` — 生产准入不变量（本地维护）
- `docs/STEP_B_event_coverage_matrix.json`
- `docs/STEP_B_frequency_math_audit.json`
- `docs/STEP_B_stop_loss_matrix.json`
- `docs/closeout_*.json`
- `docs/gen_step_b_sl_matrix.py`
- `docs/system_forecast_latest_closeout.json`

## 本地独有（VPS 无对应目录）

- `alpha_discovery/*.md` — Alpha Discovery 验收/审计报告（仅本地生成，未部署至 VPS `/root/alpha_discovery`）

## 复跑同步

```bash
bash scripts/sync_docs_from_vps.sh
```

日志写入 `docs/_sync_logs/doc_sync_*.log`。

## 创造模块说明书

另一 AI 重构的核心文档：`docs/STRATEGY_CREATION_MODULE_CURRENT_STATE.md`  
描述 sole entry → parallel_creation → creation_blueprint / cognitive 路径、进度条、旁路与软通过现状。本地与 VPS 已对齐。
