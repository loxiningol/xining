#!/bin/bash
set -euo pipefail
B=$(cat /root/backups/step_b_metrics_forecast_LAST_BACKUP.txt)
echo "Restoring from $B"
for f in auto_trade_strategy_events.py auto_trade_forecast_closeout.py auto_trade_expectancy_metrics.py auto_trade_formal_v6_executor.py auto_trade_formal_daemon.py auto_trade_system_forecast.py web_server.py forecast.html; do
  if [ -f "$B/$f" ]; then
    cp -a "$B/$f" /root/$f 2>/dev/null || true
    cp -a "$B/$f" /root/auto_trade/$f 2>/dev/null || true
    if [ "$f" = forecast.html ]; then
      cp -a "$B/$f" /root/templates/forecast.html
      cp -a "$B/$f" /root/auto_trade/templates/forecast.html 2>/dev/null || true
    fi
    echo restored "$f"
  fi
done
systemctl restart qiyu-web.service
for s in qiyu-formal-auto-trade-ada-5m qiyu-formal-auto-trade-ltc-5m qiyu-formal-auto-trade-ng-5m qiyu-formal-auto-trade-xrp-15m qiyu-formal-auto-trade; do
  systemctl restart "$s.service" || true
done
echo "STEP B rollback complete"
systemctl is-active qiyu-web.service
