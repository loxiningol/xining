#!/bin/bash
set -euo pipefail
export VECTOR_ROOT=/root
cd /root
pkill -f 'run_auto_driver.py' 2>/dev/null || true
sleep 1
tmux kill-session -t auto_driver 2>/dev/null || true
sleep 1
: > /tmp/auto_driver_session_vol_squeeze.log
tmux new-session -d -s auto_driver \
  "export VECTOR_ROOT=/root; cd /root; bash /root/scripts/run_auto_driver.sh --config /root/scripts/auto_driver_config.json 2>&1 | tee /tmp/auto_driver_session_vol_squeeze.log; echo EXIT:\$? | tee -a /tmp/auto_driver_session_vol_squeeze.log"
sleep 8
tmux ls
pgrep -af run_auto_driver || echo NO_DRIVER
echo '====LOG===='
head -50 /tmp/auto_driver_session_vol_squeeze.log || true
echo '====CFG===='
python3 -c 'import json;c=json.load(open("/root/scripts/auto_driver_config.json"));print(c["pack_path"], c.get("tag"), c.get("enable_multi_symbol_matrix"))'
systemctl is-active qiyu-formal-auto-trade-btc-5m.service || true
free -m | head -2
