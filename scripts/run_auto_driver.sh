#!/usr/bin/env bash
# One-click launcher for the autonomous Gate-retry driver.
# Does NOT modify STEP A core. Does NOT auto-mount / confirm.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WS_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -d /root/dual_engine_workflow_v2 ]]; then
  export VECTOR_ROOT="${VECTOR_ROOT:-/root}"
else
  export VECTOR_ROOT="${VECTOR_ROOT:-${WS_ROOT}}"
fi

cd "${VECTOR_ROOT}"

CONFIG_DEFAULT="${SCRIPT_DIR}/auto_driver_config.example.json"
if [[ $# -eq 0 && -f "${CONFIG_DEFAULT}" ]]; then
  echo "[run_auto_driver.sh] using example config: ${CONFIG_DEFAULT}"
  echo "[run_auto_driver.sh] tip: copy to auto_driver_config.json and edit pack_path/symbol"
  exec python3 "${SCRIPT_DIR}/run_auto_driver.py" --config "${CONFIG_DEFAULT}" "$@"
fi

exec python3 "${SCRIPT_DIR}/run_auto_driver.py" "$@"
