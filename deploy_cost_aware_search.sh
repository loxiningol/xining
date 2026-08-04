#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KEY="$SCRIPT_DIR/../ssh/vultr_codex_ed25519"
HOST="root@64.176.47.192"
STAMP="$(date +%Y%m%d_%H%M%S)"
STAGE="/root/qiyu_cost_aware_stage_$STAMP"

if [[ ! -f "$KEY" ]]; then
  echo "找不到 SSH 密钥：$KEY" >&2
  exit 1
fi

ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "mkdir -p '$STAGE'"

scp -i "$KEY" \
  "$SCRIPT_DIR/auto_trade_strategy_dsl.py" \
  "$SCRIPT_DIR/auto_trade_ai_consensus.py" \
  "$SCRIPT_DIR/auto_trade_strategy_ecosystem.py" \
  "$SCRIPT_DIR/auto_trade_execution_cost_calibrator.py" \
  "$SCRIPT_DIR/backtest_engine_v2.py" \
  "$SCRIPT_DIR/test_strategy_ecosystem_v2.py" \
  "$SCRIPT_DIR/execution_cost_model.json" \
  "$SCRIPT_DIR/qiyu-execution-cost-calibrator.service" \
  "$SCRIPT_DIR/qiyu-execution-cost-calibrator.timer" \
  "$HOST:$STAGE/"

ssh -i "$KEY" -o BatchMode=yes "$HOST" bash -s -- "$STAGE" "$STAMP" <<'REMOTE'
set -euo pipefail
STAGE="$1"
STAMP="$2"
BACKUP="/root/auto_trade/backups/cost_aware_$STAMP"
ARCHIVE="$BACKUP/pre_deploy.tar.gz"
mkdir -p "$BACKUP"

FILES=(
  /root/auto_trade_strategy_dsl.py
  /root/auto_trade_ai_consensus.py
  /root/auto_trade_strategy_ecosystem.py
  /root/auto_trade_execution_cost_calibrator.py
  /root/backtest_engine_v2.py
  /root/test_strategy_ecosystem_v2.py
  /root/auto_trade/execution_cost_model.json
  /etc/systemd/system/qiyu-execution-cost-calibrator.service
  /etc/systemd/system/qiyu-execution-cost-calibrator.timer
)
EXISTING=()
for path in "${FILES[@]}"; do
  [[ -e "$path" ]] && EXISTING+=("${path#/}")
done
if (( ${#EXISTING[@]} > 0 )); then
  tar -czf "$ARCHIVE" -C / "${EXISTING[@]}"
fi

rollback() {
  echo "部署验证失败，正在恢复部署前版本" >&2
  systemctl disable --now qiyu-execution-cost-calibrator.timer >/dev/null 2>&1 || true
  [[ -f "$ARCHIVE" ]] && tar -xzf "$ARCHIVE" -C /
  systemctl daemon-reload || true
  systemctl restart qiyu-web.service || true
}
trap rollback ERR

PYTHONPATH="$STAGE:/root" VECTOR_ROOT=/root python3 -m py_compile \
  "$STAGE/auto_trade_strategy_dsl.py" \
  "$STAGE/auto_trade_ai_consensus.py" \
  "$STAGE/auto_trade_strategy_ecosystem.py" \
  "$STAGE/auto_trade_execution_cost_calibrator.py" \
  "$STAGE/backtest_engine_v2.py" \
  "$STAGE/test_strategy_ecosystem_v2.py"
install -m 0644 "$STAGE/auto_trade_strategy_dsl.py" /root/auto_trade_strategy_dsl.py
install -m 0644 "$STAGE/auto_trade_ai_consensus.py" /root/auto_trade_ai_consensus.py
install -m 0644 "$STAGE/auto_trade_strategy_ecosystem.py" /root/auto_trade_strategy_ecosystem.py
install -m 0644 "$STAGE/auto_trade_execution_cost_calibrator.py" /root/auto_trade_execution_cost_calibrator.py
install -m 0644 "$STAGE/backtest_engine_v2.py" /root/backtest_engine_v2.py
install -m 0644 "$STAGE/test_strategy_ecosystem_v2.py" /root/test_strategy_ecosystem_v2.py
install -m 0600 "$STAGE/execution_cost_model.json" /root/auto_trade/execution_cost_model.json
install -m 0644 "$STAGE/qiyu-execution-cost-calibrator.service" /etc/systemd/system/qiyu-execution-cost-calibrator.service
install -m 0644 "$STAGE/qiyu-execution-cost-calibrator.timer" /etc/systemd/system/qiyu-execution-cost-calibrator.timer

cd /root
python3 -m py_compile auto_trade_strategy_dsl.py auto_trade_ai_consensus.py \
  auto_trade_strategy_ecosystem.py auto_trade_execution_cost_calibrator.py \
  backtest_engine_v2.py test_strategy_ecosystem_v2.py
python3 test_strategy_ecosystem_v2.py
python3 auto_trade_execution_cost_calibrator.py
python3 auto_trade_strategy_ecosystem.py --status >/root/auto_trade/cost_aware_status_check.json

systemctl daemon-reload
systemctl enable --now qiyu-execution-cost-calibrator.timer
systemctl restart qiyu-web.service
systemctl is-active qiyu-web.service qiyu-execution-cost-calibrator.timer \
  qiyu-strategy-creator.timer qiyu-strategy-ecosystem.timer
systemctl list-timers qiyu-execution-cost-calibrator.timer --no-pager

trap - ERR
echo "DEPLOY_OK backup=$BACKUP stage=$STAGE"
REMOTE
