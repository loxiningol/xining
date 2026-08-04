#!/bin/bash
# WINDTALKER PHASE 4 durable launcher
set -e
ROOT=/root/auto_trade/windtalker_phase4
SCRIPTS=$ROOT/scripts
LOG=$ROOT/logs/phase4_$(date +%Y%m%d_%H%M%S).log
mkdir -p "$ROOT/logs" "$SCRIPTS"
cd "$ROOT"
export PYTHONPATH="/root/auto_trade:/root/dual_engine_workflow_v2:$SCRIPTS:/root/auto_trade/windtalker_phase3/scripts:$PYTHONPATH"
nohup python3 -u "$SCRIPTS/windtalker_phase4_orchestrator.py" >> "$LOG" 2>&1 &
echo $! | tee "$ROOT/RUN.pid"
echo "LOG=$LOG"
echo "PID=$(cat $ROOT/RUN.pid)"
