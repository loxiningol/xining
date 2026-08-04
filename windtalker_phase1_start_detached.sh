#!/bin/bash
# Start Windtalker Phase 1 durable runner detached on server.
set -euo pipefail
ROOT=/root/auto_trade/windtalker_phase1
mkdir -p "$ROOT/candidates"
cd "$ROOT"

# copy runner if invoked from deploy path
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$SCRIPT_DIR/windtalker_phase1_runner.py" ]]; then
  cp -f "$SCRIPT_DIR/windtalker_phase1_runner.py" "$ROOT/windtalker_phase1_runner.py"
fi
if [[ -f "$SCRIPT_DIR/windtalker_phase1_orchestrator.py" ]]; then
  cp -f "$SCRIPT_DIR/windtalker_phase1_orchestrator.py" "$ROOT/windtalker_phase1_orchestrator.py"
fi

# refuse double-start if already running and not DONE
if [[ -f "$ROOT/RUN.pid" ]]; then
  OLD=$(cat "$ROOT/RUN.pid" || true)
  if [[ -n "${OLD}" ]] && kill -0 "$OLD" 2>/dev/null; then
    echo "already running pid=$OLD"
    exit 0
  fi
fi

rm -f "$ROOT/DONE.json" "$ROOT/ERROR.json"
: > "$ROOT/RUN.log"
nohup python3 -u "$ROOT/windtalker_phase1_runner.py" >> "$ROOT/RUN.log" 2>&1 &
echo $! > "$ROOT/RUN.pid"
echo "started pid=$(cat "$ROOT/RUN.pid") log=$ROOT/RUN.log status=$ROOT/STATUS.json"
