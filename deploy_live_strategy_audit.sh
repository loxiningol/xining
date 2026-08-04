#!/usr/bin/env bash
set -euo pipefail

STAGE="/root/qiyu_compute_audit_stage_20260723_0420"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP="/root/backups/live_strategy_cognitive_audit_${STAMP}"
mkdir -p "$BACKUP"

FILES=(
  auto_trade_ai_consensus.py
  backtest_engine_v2.py
  auto_trade_live_strategy_auditor.py
  freeze_live_strategy_entries.py
  auto_trade_formal_daemon.py
  auto_trade_formal_notify.py
  auto_trade_formal_v6_executor.py
  test_strategy_ecosystem_v2.py
)

for file in "${FILES[@]}"; do
  if [[ -e "/root/$file" ]]; then
    cp -a "/root/$file" "$BACKUP/$file"
  fi
done

SERVICES=(
  qiyu-formal-auto-trade.service
  qiyu-formal-auto-trade-ada-5m.service
  qiyu-formal-auto-trade-btc-15m.service
  qiyu-formal-auto-trade-btc-5m.service
  qiyu-formal-auto-trade-cl.service
  qiyu-formal-auto-trade-cl-5m.service
  qiyu-formal-auto-trade-ltc-5m.service
  qiyu-formal-auto-trade-ng.service
  qiyu-formal-auto-trade-ng-5m.service
  qiyu-formal-auto-trade-xag-5m.service
  qiyu-formal-auto-trade-xau.service
  qiyu-formal-auto-trade-xau-15m.service
)

rollback() {
  for file in "${FILES[@]}"; do
    if [[ -e "$BACKUP/$file" ]]; then
      cp -a "$BACKUP/$file" "/root/$file"
    else
      rm -f "/root/$file"
    fi
  done
  systemctl restart "${SERVICES[@]}" qiyu-web.service || true
  echo "ROLLED_BACK backup=$BACKUP" >&2
}
trap rollback ERR

cd "$STAGE"
PYTHONPATH="$STAGE:/root" VECTOR_ROOT=/root python3 -m py_compile "${FILES[@]}"

for file in "${FILES[@]}"; do
  mode=0644
  [[ "$file" == "auto_trade_live_strategy_auditor.py" ]] && mode=0700
  [[ "$file" == "freeze_live_strategy_entries.py" ]] && mode=0700
  install -m "$mode" "$STAGE/$file" "/root/$file"
done

cd /root
PYTHONPATH=/root VECTOR_ROOT=/root python3 -m py_compile "${FILES[@]}"
PYTHONPATH=/root VECTOR_ROOT=/root timeout 300 python3 test_strategy_ecosystem_v2.py

systemctl restart "${SERVICES[@]}" qiyu-web.service
systemctl is-active "${SERVICES[@]}" qiyu-web.service

trap - ERR
echo "DEPLOY_OK backup=$BACKUP"
