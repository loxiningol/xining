#!/usr/bin/env bash
# Windtalker Phase 2 — durable nohup launcher (idempotent resume).
set -euo pipefail
ROOT="/root/auto_trade/windtalker_phase2"
AUTO="/root/auto_trade"
export PYTHONPATH="/root:/root/auto_trade:${PYTHONPATH:-}"
mkdir -p "$ROOT"/{candidates,checkpoints,logs,audits,specs,ideas,kb,reports,scripts}
cd "$AUTO"

if [[ -f "$ROOT/PID" ]] && kill -0 "$(cat "$ROOT/PID")" 2>/dev/null; then
  echo "Already running pid=$(cat "$ROOT/PID")"
  exit 0
fi

nohup python3 -u "$ROOT/scripts/windtalker_phase2_orchestrator.py" \
  >>"$ROOT/logs/RUN.log" 2>&1 &
echo $! >"$ROOT/PID"
echo $! >"$ROOT/RUN.pid"
echo "started pid=$! log=$ROOT/logs/RUN.log"
