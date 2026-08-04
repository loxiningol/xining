#!/usr/bin/env bash
# Atomic deploy + rollback for metrics/forecast closeout.
set -euo pipefail
KEY="${SSH_KEY:-/Users/lele/Documents/Codex/2026-07-12/ru-g/work/ssh/vultr_codex_ed25519}"
HOST="${DEPLOY_HOST:-root@64.176.47.192}"
LOCAL="${LOCAL_ROOT:-/Users/lele/Documents/Codex/2026-07-12/ru-g/work/intraday_live_v1_20260720}"
TS=$(date +%Y%m%d_%H%M%S)
BK="/root/backups/metrics_forecast_closeout_deploy_${TS}"

FILES=(
  auto_trade_strategy_events.py
  auto_trade_forecast_closeout.py
  auto_trade_expectancy_metrics.py
  auto_trade_system_forecast.py
  auto_trade_formal_daemon.py
  auto_trade_human_confirm_pipeline.py
  auto_trade_strategy_lifecycle.py
  auto_trade_dual_engine_factory.py
  web_server.py
  templates/forecast.html
  templates/template.html
  template.html
)

ssh -i "$KEY" -o StrictHostKeyChecking=no "$HOST" "mkdir -p '$BK' /root/docs /root/auto_trade /root/templates"

echo "== backup =="
for f in "${FILES[@]}"; do
  base=$(basename "$f")
  ssh -i "$KEY" -o StrictHostKeyChecking=no "$HOST" \
    "cp -a /root/$base '$BK/' 2>/dev/null || true; \
     cp -a /root/auto_trade/$base '$BK/auto_$base' 2>/dev/null || true; \
     cp -a /root/templates/$base '$BK/' 2>/dev/null || true"
done
ssh -i "$KEY" -o StrictHostKeyChecking=no "$HOST" "echo '$BK' > /root/backups/metrics_forecast_closeout_LAST_BACKUP.txt; ls -la '$BK' | head"

echo "== upload =="
for f in "${FILES[@]}"; do
  scp -i "$KEY" -o StrictHostKeyChecking=no "$LOCAL/$f" "$HOST:/tmp/closeout_$(basename "$f")"
done

echo "== install =="
ssh -i "$KEY" -o StrictHostKeyChecking=no "$HOST" bash -s <<REMOTE
set -euo pipefail
install_py() {
  local name="\$1"
  install -m 0644 "/tmp/closeout_\${name}" "/root/\${name}"
  install -m 0644 "/tmp/closeout_\${name}" "/root/auto_trade/\${name}"
}
install_py auto_trade_strategy_events.py
install_py auto_trade_forecast_closeout.py
install_py auto_trade_expectancy_metrics.py
install_py auto_trade_system_forecast.py
install_py auto_trade_formal_daemon.py
install_py auto_trade_human_confirm_pipeline.py
install_py auto_trade_strategy_lifecycle.py
install_py auto_trade_dual_engine_factory.py
install -m 0644 /tmp/closeout_web_server.py /root/web_server.py
install -m 0644 /tmp/closeout_forecast.html /root/templates/forecast.html
install -m 0644 /tmp/closeout_forecast.html /root/auto_trade/templates/forecast.html 2>/dev/null || true
install -m 0644 /tmp/closeout_template.html /root/templates/template.html
install -m 0644 /tmp/closeout_template.html /root/template.html
# syntax check
python3 -m py_compile /root/auto_trade_strategy_events.py \
  /root/auto_trade_forecast_closeout.py \
  /root/auto_trade_expectancy_metrics.py \
  /root/auto_trade_system_forecast.py \
  /root/auto_trade_formal_daemon.py \
  /root/web_server.py
# restart web only (do NOT restart formal daemons — protect live positions)
systemctl restart qiyu-web
sleep 2
systemctl is-active qiyu-web
# lightweight statistical refresh (no 3AI)
cd /root && python3 - <<'PY'
import auto_trade_forecast_closeout as c
out = c.run_lightweight_statistical_refresh(push_wx=False)
rep = out.get("report") or {}
print("refresh_ok", out.get("ok"))
print("generated_at", rep.get("generated_at"))
print("stale", rep.get("stale"), "is_current", rep.get("is_current"))
print("pool", rep.get("strategy_pool_version"), "mounted", rep.get("active_strategy_count"))
print("weekly_fillable", (rep.get("overall") or {}).get("weekly_opens_expected"))
print("daily_fillable", (rep.get("overall") or {}).get("daily_opens_expected"))
pos = rep.get("positive_expectancy_frequency") or {}
print("posE_weekly", pos.get("calibrated_positive_E_weekly"))
print("uncal_weekly", pos.get("uncalibrated_weekly"))
print("near0_weekly", pos.get("near_zero_E_weekly"))
print("neg_weekly", pos.get("negative_E_weekly"))
peg = rep.get("positive_expectancy_frequency_gap") or {}
print("posE_gap_week", peg.get("gap_weekly_to_band"))
print("calib_stage", (rep.get("calibration_stage") or {}).get("stage"),
      (rep.get("calibration_stage") or {}).get("complete_periods"))
pf = rep.get("portfolio_frequency") or {}
print("simple", pf.get("simple_sum_weekly"), "corr", pf.get("correlation_adjusted_weekly"),
      "final", pf.get("final_fillable_weekly"))
PY
echo DEPLOY_OK backup=$BK
REMOTE

echo "Rollback: ssh ... 'bash /root/docs/rollback_metrics_forecast_closeout.sh'"
