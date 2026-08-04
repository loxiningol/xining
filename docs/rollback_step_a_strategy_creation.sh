#!/usr/bin/env bash
# Local copy of remote rollback helper. Prefer executing on server:
#   /root/backups/step_a_strategy_creation_20260726_234045/ROLLBACK.sh
set -euo pipefail
BACKUP="${1:-/root/backups/step_a_strategy_creation_20260726_234045}"
ssh -i "${SSH_KEY:-/Users/lele/Documents/Codex/2026-07-12/ru-g/work/ssh/vultr_codex_ed25519}" \
  -o BatchMode=yes root@64.176.47.192 "bash $BACKUP/ROLLBACK.sh"
echo "Remote STEP A code snapshot restored. formal_daemon intentionally untouched."
