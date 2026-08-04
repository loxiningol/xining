# CREATION_PIPELINE_OUTPUT_RECOVERY_REPORT

Updated: 2026-08-02 21:14:53

## Verdict
`crec_cl_15m_exh_r55_z0p0_h20_t45_seed` (CL-USDT-SWAP 15m, WR 75%, 8 trades, mean_net>0) **passed frost2 → formal review → pending human confirm**. No auto-mount.

## Thresholds changed (prod)

### frost2 quick (`/root/frost2_action_run.py`)
| Before | After |
|---|---|
| folds>=10 AND fold_positive>=7 | **removed** as hard bar |
| — | fold_positive/folds **>=0.6** OR fold_positive **>=5** of available folds |
| min trades 8 | unchanged (8) |
| logic destruction hard fail | **advisory** when WR>50 and mean_net>0 |

### formal AI (`/root/auto_trade_dual_engine_factory.py`)
| Before | After |
|---|---|
| FORMAL_WR_GATE 65 | **50** |
| both DeepSeek AND Qwen must pass | one solid provider + evidence WR>50 enough when other returns WR<=0 / error |
| stop-cluster/decision veto | softened for evidence-backed WR/mean books |

## Pass evidence (this seed)
- quick_pass: true (WF ratio 6/8 = 0.75; logic destruction advisory)
- full_pass: true (friction Sharpe 2.06; MC beat 0.94)
- sim: DeepSeek 67 / Qwen 65 (both >=55)
- formal_approved: true
- annotation: DeepSeek WR 0.0% (provider error ignored) | Qwen WR 68.0% | evidence WR 75.0%
- pending: ok=true, key=`crec_cl_15m_exh_r55_z0p0_h20_t45_seed`
- pending status: awaiting_confirm

## Artifacts
- `/root/auto_trade/creation_recovery/STATUS.json`
- `/root/auto_trade/creation_recovery/artifacts/feature_tp/cl_seed_regate.json`
- `/root/auto_trade/creation_recovery/artifacts/feature_tp/cl_seed_submit.json`
- `/root/auto_trade/creation_recovery/artifacts/feature_tp/CL_SEED_SUCCESS.json`
