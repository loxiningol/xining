#!/usr/bin/env bash
# Rollback metrics/forecast closeout to last deploy backup.
set -euo pipefail
LAST=$(cat /root/backups/metrics_forecast_closeout_LAST_BACKUP.txt 2>/dev/null || true)
BK=${1:-$LAST}
if [[ -z "$BK" || ! -d "$BK" ]]; then
  echo "usage: $0 /root/backups/metrics_forecast_closeout_deploy_YYYYMMDD_HHMMSS"
  exit 1
fi
echo "Restoring from $BK"
for f in auto_trade_strategy_events.py auto_trade_forecast_closeout.py \
         auto_trade_expectancy_metrics.py auto_trade_system_forecast.py \
         auto_trade_formal_daemon.py auto_trade_human_confirm_pipeline.py \
         auto_trade_strategy_lifecycle.py auto_trade_dual_engine_factory.py \
         web_server.py; do
  if [[ -f "$BK/$f" ]]; then
    cp -a "$BK/$f" "/root/$f"
    cp -a "$BK/$f" "/root/auto_trade/$f" 2>/dev/null || true
  fi
done
[[ -f "$BK/forecast.html" ]] && cp -a "$BK/forecast.html" /root/templates/forecast.html
[[ -f "$BK/template.html" ]] && cp -a "$BK/template.html" /root/templates/template.html && cp -a "$BK/template.html" /root/template.html
systemctl restart qiyu-web
systemctl is-active qiyu-web
echo ROLLBACK_OK
