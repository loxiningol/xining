#!/usr/bin/env bash
# Invent contract preflight: seed ↔ gate ↔ lock ↔ loop order.
# Run on Mac before/after deploy, or on VPS:
#   cd /root && PYTHONPATH=/root python3 -m dual_engine_workflow_v2.invent_contract_audit --live
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
LIVE_FLAG="${1:---no-live}"
if [[ "$LIVE_FLAG" == "--live" ]]; then
  python3 -m dual_engine_workflow_v2.invent_contract_audit --live
else
  python3 -m dual_engine_workflow_v2.invent_contract_audit --no-live
fi
python3 tests_workflow_v2/test_invent_contract_audit.py
echo "OK invent_contract_preflight"
